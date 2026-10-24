"""Transactional audit/outbox and idempotency helpers for v1 commands."""

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models.redesign import ApiIdempotencyKey, AuditEvent, OutboxEvent

_IDEMPOTENCY_TTL = timedelta(hours=24)
_RESERVATION_TTL = timedelta(minutes=15)


def request_context(request: Any) -> tuple[str | None, str | None]:
    """Return the middleware-normalized IDs for audit and outbox records."""
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    if request_id is None:
        # Keep direct callers/tests safe while preserving the same sanitization
        # contract as RequestTraceMiddleware.
        incoming = request.headers.get("X-Request-ID", "")
        request_id = re.sub(r"[^A-Za-z0-9._-]", "", incoming)[:64] or None
    correlation = request.headers.get("X-Correlation-ID", "")
    correlation = re.sub(r"[^A-Za-z0-9._-]", "", correlation)[:64] or None
    return request_id, correlation


def request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require_idempotency_key(key: str | None) -> str:
    """Require a bounded command key; read-only routes must not call this."""
    normalized = (key or "").strip()
    if not normalized or len(normalized) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key is required for command mutations.",
        )
    return normalized


def replay_or_reserve(
    db: Session, *, principal_id: str, route: str, key: str | None, payload: Any
) -> ApiIdempotencyKey:
    """Return a completed response or reserve a key without rolling back outer work."""
    key = require_idempotency_key(key)
    digest = request_hash(payload)
    now = datetime.now(timezone.utc)
    row = db.scalar(
        select(ApiIdempotencyKey)
        .where(
            ApiIdempotencyKey.principal_id == principal_id,
            ApiIdempotencyKey.route == route,
            ApiIdempotencyKey.key == key,
        )
        .with_for_update()
    )
    if row is not None and row.expires_at is not None and row.expires_at <= now:
        # Expired keys are reusable, but only after the locked row is removed.
        # This prevents indefinite replay retention while preserving the same
        # principal/route/key uniqueness contract for concurrent callers.
        db.delete(row)
        db.flush()
        row = None

    if row is not None:
        row.last_seen_at = now
        if row.expires_at is None:
            # Repair rows created before expiry was populated.
            row.expires_at = now + _IDEMPOTENCY_TTL
        if row.request_hash != digest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key was reused with a different request.",
            )
        if row.response_json is None:
            if row.reserved_at and row.reserved_at + _RESERVATION_TTL <= now:
                row.reserved_at = now
                row.reservation_token = uuid.uuid4().hex
                row.expires_at = now + _IDEMPOTENCY_TTL
                return row
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An identical command is already being processed.",
            )
        return row

    row = ApiIdempotencyKey(
        principal_id=principal_id,
        route=route,
        key=key,
        request_hash=digest,
        reserved_at=now,
        reservation_token=uuid.uuid4().hex,
        last_seen_at=now,
        expires_at=now + _IDEMPOTENCY_TTL,
    )
    try:
        # A savepoint prevents a concurrent unique-key race from rolling back
        # unrelated domain writes in the caller's transaction.
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        row = db.scalar(
            select(ApiIdempotencyKey)
            .where(
                ApiIdempotencyKey.principal_id == principal_id,
                ApiIdempotencyKey.route == route,
                ApiIdempotencyKey.key == key,
            )
            .with_for_update()
        )
        if row is None:
            raise
        row.last_seen_at = now
        if row.request_hash != digest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key was reused with a different request.",
            )
        if row.response_json is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An identical command is already being processed.",
            )
    return row


def store_response(row: ApiIdempotencyKey | None, *, status_code: int, body: dict) -> None:
    if row is not None:
        row.response_status = status_code
        row.response_json = body
        row.last_seen_at = datetime.now(timezone.utc)


def _normalise_action(action: str) -> str:
    """ONE VOCABULARY, decided here because this is the one funnel.

    Callers wrote both cases: most name an action in upper (`GRANTED`,
    `FEATURE_DISABLED`), `routers/redesign.py`'s six notebook writes named it in
    lower (`created`, `published`). Nothing refused either, so the table grew two
    vocabularies and the audit API's filter — which upper-cased the needle —
    could reach only one of them. A search for `created` became `CREATED` and
    never matched a notebook row.

    Normalising HERE rather than at each call site is the point: the next writer
    does not have to know the convention, because there is only one way in. Rows
    already written keep the case they were written with — `audit_events` is
    append-only and rewriting it would be editing the record — so
    `routers/audit.py` compares case-insensitively and says why.
    """
    return (action or "").strip().upper()


def record_change(
    db: Session, *, session: dict, request: Any, tenant_id: str | None,
    entity_type: str, entity_id: str, action: str,
    before: dict | None, after: dict | None, event_type: str, payload: dict,
) -> None:
    request_id, correlation_id = request_context(request)
    db.add(AuditEvent(
        tenant_id=tenant_id, actor_user_id=session.get("userId"), actor_type="USER",
        request_id=request_id, correlation_id=correlation_id, entity_type=entity_type,
        entity_id=entity_id, action=_normalise_action(action),
        before_json=before, after_json=after,
        metadata_json={"route": str(request.url.path)},
    ))
    event_id = uuid.uuid4().hex
    db.add(OutboxEvent(
        id=event_id,
        tenant_id=tenant_id,
        event_type=event_type,
        event_version=1,
        routing_key="default",
        aggregate_type=entity_type,
        aggregate_id=entity_id,
        payload={
            "event_id": event_id, "event_type": event_type, "event_version": 1,
            "aggregate_type": entity_type, "aggregate_id": entity_id,
            "actor_id": session.get("userId"), "tenant_id": tenant_id,
            "request_id": request_id, "correlation_id": correlation_id,
            "payload": payload,
        },
    ))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
