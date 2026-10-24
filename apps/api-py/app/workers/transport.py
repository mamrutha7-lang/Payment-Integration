"""Transport adapters kept behind a small interface for safe testing."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict, deque
from typing import Any

from .contracts import ReceiveTransport


class SqsTransport(ReceiveTransport):
    """Thin boto3 adapter; no business logic belongs in this class."""

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("sqs")
        self.client = client

    def publish(self, queue_url: str, message: dict[str, Any]) -> str:
        response = self.client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message, separators=(",", ":"), sort_keys=True),
        )
        return str(response["MessageId"])

    def receive(self, queue_url: str, *, max_messages: int, wait_seconds: int) -> list[dict[str, Any]]:
        response = self.client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=max(1, min(max_messages, 10)),
            WaitTimeSeconds=max(0, min(wait_seconds, 20)),
        )
        return list(response.get("Messages", []))

    def delete(self, queue_url: str, receipt_handle: str) -> None:
        self.client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


class InMemoryTransport(ReceiveTransport):
    """Small fake transport used by unit tests and local worker development."""

    def __init__(self) -> None:
        self.queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self.deleted: list[str] = []

    def publish(self, queue_url: str, message: dict[str, Any]) -> str:
        message_id = uuid.uuid4().hex
        self.queues[queue_url].append({
            "MessageId": message_id,
            "ReceiptHandle": f"receipt-{message_id}",
            "Body": json.dumps(message, separators=(",", ":"), sort_keys=True),
        })
        return message_id

    def receive(self, queue_url: str, *, max_messages: int, wait_seconds: int) -> list[dict[str, Any]]:
        del wait_seconds
        result: list[dict[str, Any]] = []
        for _ in range(max(1, max_messages)):
            if not self.queues[queue_url]:
                break
            result.append(self.queues[queue_url].popleft())
        return result

    def delete(self, queue_url: str, receipt_handle: str) -> None:
        del queue_url
        self.deleted.append(receipt_handle)
