"""Consume event notifications while keeping job state authoritative in Postgres."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.redesign import DomainJob, JobStatus
from .contracts import EventEnvelope, ReceiveTransport
from .leasing import complete_job, retry_job

log = logging.getLogger(__name__)
Handler = Callable[[Session, DomainJob, EventEnvelope], dict[str, Any] | None]


class PermanentMessageError(ValueError):
    """The message cannot be retried safely."""


def _payload(envelope: EventEnvelope) -> dict[str, Any]:
    nested = envelope.payload.get("payload")
    return nested if isinstance(nested, dict) else envelope.payload


def _claim_job(db: Session, job_id: str, *, owner: str, lease_seconds: int = 300) -> tuple[DomainJob | None, str | None]:
    now = datetime.now(timezone.utc)
    job = db.scalar(select(DomainJob).where(DomainJob.id == job_id).with_for_update())
    if job is None:
        return None, "missing"
    if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
        return job, "terminal"
    if job.status == JobStatus.RUNNING and job.lease_until and job.lease_until > now and job.lease_owner != owner:
        return job, "owned"
    job.status = JobStatus.RUNNING
    job.lease_owner = owner
    job.lease_token = uuid.uuid4().hex
    job.lease_until = datetime.fromtimestamp(now.timestamp() + lease_seconds, tz=timezone.utc)
    job.last_heartbeat_at = now
    job.last_attempt_at = now
    job.attempts += 1
    db.flush()
    return job, "claimed"


def process_event(db: Session, envelope: EventEnvelope, *, owner: str, handlers: dict[str, Handler]) -> str:
    data = _payload(envelope)
    job_id = data.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise PermanentMessageError("event does not reference a domain job")
    job, state = _claim_job(db, job_id, owner=owner)
    if state == "missing":
        raise PermanentMessageError(f"domain job {job_id} does not exist")
    if state in ("terminal", "owned"):
        db.rollback()
        return "noop"
    assert job is not None
    token = job.lease_token or ""
    db.commit()  # Claim first; external work must not hold the claim transaction.
    handler = handlers.get(job.job_type)
    if handler is None:
        with db.begin():
            fresh = db.get(DomainJob, job_id)
            if fresh is not None:
                retry_job(db, fresh, owner=owner, token=token, error_code="UNKNOWN_JOB_TYPE", detail=job.job_type)
        return "failed"
    try:
        result = handler(db, job, envelope)
        with db.begin():
            fresh = db.get(DomainJob, job_id)
            if fresh is None or not complete_job(db, fresh, owner=owner, token=token, result=result):
                raise RuntimeError("job lease was lost before completion")
        return "completed"
    except PermanentMessageError:
        with db.begin():
            fresh = db.get(DomainJob, job_id)
            if fresh is not None:
                retry_job(db, fresh, owner=owner, token=token, error_code="PERMANENT_MESSAGE", detail="permanent worker message")
        return "failed"
    except Exception as exc:
        db.rollback()
        with db.begin():
            fresh = db.get(DomainJob, job_id)
            if fresh is not None:
                retry_job(db, fresh, owner=owner, token=token, error_code="HANDLER_ERROR", detail=str(exc))
        log.exception("domain job %s failed", job_id)
        return "retry"


def process_message(db: Session, body: str | bytes, *, owner: str, handlers: dict[str, Handler]) -> str:
    try:
        raw = json.loads(body)
        if not isinstance(raw, dict):
            raise ValueError("message body must be an object")
        envelope = EventEnvelope.from_dict(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PermanentMessageError(str(exc)) from exc
    return process_event(db, envelope, owner=owner, handlers=handlers)


def consume_once(db: Session, transport: ReceiveTransport, queue_url: str, *, owner: str, handlers: dict[str, Handler], max_messages: int = 10) -> dict[str, int]:
    received = transport.receive(queue_url, max_messages=max_messages, wait_seconds=1)
    completed = failed = retried = 0
    for message in received:
        receipt = message.get("ReceiptHandle")
        try:
            result = process_message(db, message.get("Body", ""), owner=owner, handlers=handlers)
            transport.delete(queue_url, receipt)
            if result == "completed": completed += 1
            elif result == "failed": failed += 1
            else: retried += 1
        except PermanentMessageError:
            # Leave malformed messages for the configured SQS redrive policy.
            failed += 1
            log.exception("permanent worker message failure")
        except Exception:
            retried += 1
            log.exception("worker message will be retried by transport")
    return {"received": len(received), "completed": completed, "failed": failed, "retried": retried}
