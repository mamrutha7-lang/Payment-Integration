"""Database-backed leasing primitives shared by all Phase 4 workers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models.redesign import DeliveryStatus, DomainJob, JobStatus, OutboxEvent


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_token() -> str:
    return uuid.uuid4().hex


def claim_outbox(db: Session, *, owner: str, limit: int = 20, lease_seconds: int = 60, now: datetime | None = None) -> list[OutboxEvent]:
    now = now or utc_now()
    rows = db.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.status.in_([DeliveryStatus.PENDING, DeliveryStatus.FAILED]),
            OutboxEvent.available_at <= now,
            or_(OutboxEvent.lease_until.is_(None), OutboxEvent.lease_until < now),
            OutboxEvent.dead_at.is_(None),
        )
        .order_by(OutboxEvent.available_at.asc(), OutboxEvent.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
    ).all()
    for row in rows:
        row.lease_owner = owner
        row.lease_token = _lease_token()
        row.lease_until = now + timedelta(seconds=lease_seconds)
        row.last_attempt_at = now
        row.attempts += 1
    db.flush()
    return rows


def mark_outbox_delivered(db: Session, row: OutboxEvent, *, owner: str, token: str, message_id: str, now: datetime | None = None) -> bool:
    if row.lease_owner != owner or row.lease_token != token or row.dead_at is not None:
        return False
    row.status = DeliveryStatus.DELIVERED
    row.delivered_at = now or utc_now()
    row.published_message_id = message_id
    row.lease_owner = None
    row.lease_token = None
    row.lease_until = None
    db.flush()
    return True


def mark_outbox_failure(db: Session, row: OutboxEvent, *, owner: str, token: str, error: str, now: datetime | None = None) -> bool:
    now = now or utc_now()
    if row.lease_owner != owner or row.lease_token != token or row.dead_at is not None:
        return False
    bounded = error[:1000]
    row.last_error = bounded
    if row.attempts >= row.max_attempts:
        row.dead_at = now
        row.dead_reason = bounded
        row.status = DeliveryStatus.FAILED
    else:
        row.status = DeliveryStatus.FAILED
        row.available_at = now + timedelta(seconds=min(3600, 2 ** min(row.attempts, 10)))
    row.lease_owner = None
    row.lease_token = None
    row.lease_until = None
    db.flush()
    return True


def claim_jobs(db: Session, *, owner: str, job_type: str | None = None, limit: int = 10, lease_seconds: int = 300, now: datetime | None = None) -> list[DomainJob]:
    now = now or utc_now()
    eligible = or_(
        DomainJob.status == JobStatus.QUEUED,
        (DomainJob.status == JobStatus.RUNNING) & (DomainJob.lease_until < now),
    )
    conditions: list[Any] = [eligible, DomainJob.available_at <= now]
    if job_type:
        conditions.append(DomainJob.job_type == job_type)
    rows = db.scalars(
        select(DomainJob)
        .where(*conditions)
        .order_by(DomainJob.available_at.asc(), DomainJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
    ).all()
    for row in rows:
        row.status = JobStatus.RUNNING
        row.lease_owner = owner
        row.lease_token = _lease_token()
        row.lease_until = now + timedelta(seconds=lease_seconds)
        row.last_heartbeat_at = now
        row.last_attempt_at = now
        row.attempts += 1
    db.flush()
    return rows


def heartbeat_job(db: Session, job: DomainJob, *, owner: str, token: str, lease_seconds: int = 300, now: datetime | None = None) -> bool:
    if job.status != JobStatus.RUNNING or job.lease_owner != owner or job.lease_token != token:
        return False
    now = now or utc_now()
    job.last_heartbeat_at = now
    job.lease_until = now + timedelta(seconds=lease_seconds)
    db.flush()
    return True


def complete_job(db: Session, job: DomainJob, *, owner: str, token: str, result: dict | None = None, now: datetime | None = None) -> bool:
    if job.status != JobStatus.RUNNING or job.lease_owner != owner or job.lease_token != token:
        return False
    job.status = JobStatus.SUCCEEDED
    job.result_json = result
    job.completed_at = now or utc_now()
    job.lease_owner = None
    job.lease_token = None
    job.lease_until = None
    db.flush()
    return True


def retry_job(db: Session, job: DomainJob, *, owner: str, token: str, error_code: str, detail: str, now: datetime | None = None) -> bool:
    now = now or utc_now()
    if job.status != JobStatus.RUNNING or job.lease_owner != owner or job.lease_token != token:
        return False
    job.error_code = error_code[:100]
    job.error_detail = detail[:1000]
    if job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED
        job.dead_at = now
        job.dead_reason = detail[:1000]
        job.completed_at = now
    else:
        job.status = JobStatus.QUEUED
        job.available_at = now + timedelta(seconds=min(3600, 2 ** min(job.attempts, 10)))
    job.lease_owner = None
    job.lease_token = None
    job.lease_until = None
    db.flush()
    return True
