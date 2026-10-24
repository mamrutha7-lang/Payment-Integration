"""SQS push/pull for the two candidate streams.

One queue per degree level. `CandidateQueue` routes by `degree_level`, so a
caller never holds two URLs, and refuses a level whose queue is not configured
with `QueueNotConfigured` — the honest answer, rather than a message that
disappears into "".

The client is injected. Tests pass a fake with the three methods used here
(`send_message_batch`, `receive_message`, `delete_message`); production builds
a boto3 client from settings in `candidate_queue()`.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("app.voice_platform.queue.sqs")

#: SQS's own ceiling per SendMessageBatch.
BATCH_SIZE = 10


class QueueNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class QueuedMessage:
    degree_level: str
    message_id: str
    receipt_handle: str
    body: dict[str, Any]


class CandidateQueue:
    def __init__(self, client: Any, *, ug_url: str = "", pg_url: str = "") -> None:
        self._client = client
        self._urls = {"UG": ug_url.strip(), "PG": pg_url.strip()}

    def configured(self, degree_level: str) -> bool:
        return bool(self._urls.get(degree_level.upper(), ""))

    def url_for(self, degree_level: str) -> str:
        url = self._urls.get(degree_level.strip().upper(), "")
        if not url:
            raise QueueNotConfigured(
                f"no SQS queue is configured for the {degree_level.upper()} stream "
                f"(PLATFORM_{degree_level.upper()}_QUEUE_URL)"
            )
        return url

    def push(self, degree_level: str, body: dict[str, Any]) -> str:
        sent, failed = self.push_many(degree_level, [body])
        if failed:
            raise RuntimeError(f"SQS refused the message: {failed[0]}")
        return sent[0]

    def push_many(
        self, degree_level: str, bodies: Iterable[dict[str, Any]]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Send in batches of ten. Returns (message ids sent, failures as SQS
        reports them). Partial failure is reported, not raised, so a 400-row
        upload with one bad row still queues the other 399."""
        url = self.url_for(degree_level)
        sent: list[str] = []
        failed: list[dict[str, Any]] = []
        batch: list[dict[str, Any]] = []

        def flush() -> None:
            if not batch:
                return
            response = self._client.send_message_batch(QueueUrl=url, Entries=list(batch))
            for ok in response.get("Successful", []):
                sent.append(ok.get("MessageId", ""))
            for bad in response.get("Failed", []):
                failed.append(dict(bad))
            batch.clear()

        for body in bodies:
            batch.append({"Id": uuid.uuid4().hex[:32], "MessageBody": json.dumps(body, default=str)})
            if len(batch) == BATCH_SIZE:
                flush()
        flush()
        return sent, failed

    def pull(
        self,
        degree_level: str,
        *,
        max_messages: int = 10,
        wait_seconds: int = 0,
        visibility_timeout: int | None = None,
    ) -> list[QueuedMessage]:
        url = self.url_for(degree_level)
        kwargs: dict[str, Any] = {
            "QueueUrl": url,
            "MaxNumberOfMessages": max(1, min(10, int(max_messages))),
            "WaitTimeSeconds": max(0, min(20, int(wait_seconds))),
        }
        if visibility_timeout is not None:
            kwargs["VisibilityTimeout"] = int(visibility_timeout)
        response = self._client.receive_message(**kwargs)
        out: list[QueuedMessage] = []
        for message in response.get("Messages", []):
            try:
                body = json.loads(message.get("Body", "") or "{}")
            except json.JSONDecodeError:
                log.error("Discarding an unparseable SQS body on %s: %r", degree_level, message.get("Body", "")[:120])
                body = {"type": "invalid", "raw": message.get("Body", "")}
            out.append(
                QueuedMessage(
                    degree_level=degree_level.upper(),
                    message_id=message.get("MessageId", ""),
                    receipt_handle=message.get("ReceiptHandle", ""),
                    body=body if isinstance(body, dict) else {"type": "invalid", "raw": body},
                )
            )
        return out

    def ack(self, degree_level: str, receipt_handle: str) -> None:
        self._client.delete_message(QueueUrl=self.url_for(degree_level), ReceiptHandle=receipt_handle)


def candidate_queue(client: Any | None = None) -> CandidateQueue | None:
    """The configured queue pair, or None when neither stream has a URL.
    Imports settings lazily so this module stays importable inside the Lambda
    zip, where `app.config` does not exist."""
    from ...config import settings

    ug = settings.platform_queue_url("UG")
    pg = settings.platform_queue_url("PG")
    if not (ug or pg):
        return None
    if client is None:
        import boto3

        client = boto3.client("sqs", region_name=settings.platform_region or None)
    return CandidateQueue(client, ug_url=ug, pg_url=pg)
