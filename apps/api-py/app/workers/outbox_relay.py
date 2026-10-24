"""Poll and publish outbox rows with at-least-once delivery semantics."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from .contracts import QueueTransport
from .leasing import claim_outbox, mark_outbox_delivered, mark_outbox_failure

log = logging.getLogger(__name__)


def relay_once(
    db: Session,
    transport: QueueTransport,
    queue_map: dict[str, str],
    *,
    owner: str,
    limit: int = 20,
    lease_seconds: int = 60,
) -> dict[str, int]:
    """Publish one bounded batch; each row is committed independently."""
    rows = claim_outbox(db, owner=owner, limit=limit, lease_seconds=lease_seconds)
    published = failed = dead = 0
    for row in rows:
        token = row.lease_token or ""
        queue_url = queue_map.get(row.routing_key)
        if not queue_url:
            mark_outbox_failure(
                db, row, owner=owner, token=token,
                error=f"unknown outbox routing key: {row.routing_key}",
            )
            db.commit()
            failed += 1
            if row.dead_at is not None:
                dead += 1
            continue
        try:
            message: dict[str, Any] = dict(row.payload or {})
            message.update({
                "event_id": row.id,
                "event_type": row.event_type,
                "event_version": row.event_version,
                "aggregate_type": row.aggregate_type,
                "aggregate_id": row.aggregate_id,
            })
            message_id = transport.publish(queue_url, message)
            if not mark_outbox_delivered(db, row, owner=owner, token=token, message_id=message_id):
                raise RuntimeError("outbox lease was lost before delivery was recorded")
            db.commit()
            published += 1
        except Exception as exc:  # transport and lease failures are retryable
            db.rollback()
            fresh = db.get(type(row), row.id)
            if fresh is not None and fresh.lease_owner == owner:
                mark_outbox_failure(db, fresh, owner=owner, token=fresh.lease_token or token, error=str(exc))
                db.commit()
                failed += 1
                if fresh.dead_at is not None:
                    dead += 1
            else:
                log.exception("outbox row %s lost its lease after publish failure", row.id)
    return {"claimed": len(rows), "published": published, "failed": failed, "dead": dead}
