"""The S3-trigger Lambda: a bulk CSV/JSON candidate file lands in the upload
bucket → validate every row → push the accepted ones onto the Undergraduate or
Postgraduate SQS queue → write a rejects report next to the file.

Deployed by the CDK stack in infra/cdk as a zip of this directory, so it
imports only the standard library, boto3 (in every Lambda Python runtime) and
its siblings `validation.py` / `sqs.py` — by relative import when running
inside the API package (tests) and by plain module name inside the zip.

Environment (set by the CDK stack):
    PLATFORM_UG_QUEUE_URL, PLATFORM_PG_QUEUE_URL   the two streams
    PLATFORM_REJECTS_PREFIX                        default "rejects/"

Idempotent per object: re-delivering the same S3 event re-validates and
re-pushes; the drain worker's upsert on `external_id` makes a duplicate push
harmless.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import unquote_plus

try:  # inside the API package
    from .sqs import CandidateQueue
    from .validation import parse_bulk, partition, queue_message
except ImportError:  # pragma: no cover - the Lambda zip has these as top-level modules
    from sqs import CandidateQueue  # type: ignore[no-redef]
    from validation import parse_bulk, partition, queue_message  # type: ignore[no-redef]

log = logging.getLogger("voice_platform.candidate_ingest")
if not log.handlers:  # Lambda's root handler prints to CloudWatch already
    logging.basicConfig(level=logging.INFO)


def _queue_from_env(client: Any | None = None) -> CandidateQueue:
    if client is None:
        import boto3

        client = boto3.client("sqs")
    return CandidateQueue(
        client,
        ug_url=os.environ.get("PLATFORM_UG_QUEUE_URL", ""),
        pg_url=os.environ.get("PLATFORM_PG_QUEUE_URL", ""),
    )


def process_object(
    bucket: str,
    key: str,
    body: bytes,
    *,
    queue: CandidateQueue,
    s3: Any | None = None,
    rejects_prefix: str = "rejects/",
) -> dict[str, Any]:
    """Validate one uploaded file and push its candidates. Pure apart from the
    queue and the optional rejects report; returns a summary."""
    rows = parse_bulk(body, key)
    accepted, rejects = partition(rows)
    by_degree: dict[str, list[dict[str, Any]]] = {}
    for candidate in accepted:
        by_degree.setdefault(candidate.degree_level, []).append(
            queue_message(candidate, source="bulk_upload", source_ref=f"s3://{bucket}/{key}")
        )
    pushed: dict[str, int] = {}
    unrouted: list[dict[str, Any]] = []
    for degree, messages in by_degree.items():
        if not queue.configured(degree):
            unrouted.extend(messages)
            continue
        sent, failed = queue.push_many(degree, messages)
        pushed[degree] = len(sent)
        for bad in failed:
            rejects.append({"row": None, "field": "queue", "error": bad.get("Message", "SQS refused the message")})
    for message in unrouted:
        rejects.append({
            "row": None,
            "field": "degree_level",
            "error": f"no queue configured for the {message['candidate']['degree_level']} stream",
        })
    summary = {
        "bucket": bucket,
        "key": key,
        "rows": len(rows),
        "accepted": len(accepted),
        "pushed": pushed,
        "rejected": len(rejects),
    }
    if rejects and s3 is not None:
        report_key = f"{rejects_prefix}{key}.rejects.json"
        try:
            s3.put_object(
                Bucket=bucket,
                Key=report_key,
                Body=json.dumps({"summary": summary, "rejects": rejects}, indent=1).encode(),
                ContentType="application/json",
            )
            summary["rejects_report"] = f"s3://{bucket}/{report_key}"
        except Exception as exc:  # noqa: BLE001 - the report is a convenience
            log.error("Could not write the rejects report for %s: %s", key, exc)
    log.info("candidate ingest %s", json.dumps(summary))
    return summary


def handler(event: dict[str, Any], context: Any = None, *, s3: Any | None = None, queue: CandidateQueue | None = None) -> dict[str, Any]:
    """The Lambda entry point. `s3` and `queue` are injectable for tests."""
    if s3 is None:
        import boto3

        s3 = boto3.client("s3")
    if queue is None:
        queue = _queue_from_env()
    rejects_prefix = os.environ.get("PLATFORM_REJECTS_PREFIX", "rejects/")
    results: list[dict[str, Any]] = []
    for record in event.get("Records", []):
        bucket = record.get("s3", {}).get("bucket", {}).get("name", "")
        key = unquote_plus(record.get("s3", {}).get("object", {}).get("key", ""))
        if not bucket or not key:
            continue
        if key.startswith(rejects_prefix):
            # Our own report landing in the same bucket must not re-trigger us.
            continue
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            results.append(process_object(bucket, key, body, queue=queue, s3=s3, rejects_prefix=rejects_prefix))
        except Exception as exc:  # noqa: BLE001 - one bad file must not block the batch
            log.exception("candidate ingest failed for s3://%s/%s", bucket, key)
            results.append({"bucket": bucket, "key": key, "error": str(exc)})
    return {"processed": len(results), "results": results}
