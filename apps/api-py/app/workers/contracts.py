"""Shared contracts for the Phase 4 worker transport boundary.

SQS is at-least-once delivery only. PostgreSQL job/outbox rows remain the
source of truth, and every consumer must be safe to run more than once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


SUPPORTED_EVENT_VERSION = 1


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    event_type: str
    event_version: int
    aggregate_type: str
    aggregate_id: str
    actor_id: str | None
    tenant_id: str | None
    request_id: str | None
    correlation_id: str | None
    payload: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EventEnvelope":
        required = ("event_id", "event_type", "aggregate_type", "aggregate_id", "payload")
        if any(not isinstance(raw.get(key), str) or not raw[key].strip() for key in required[:4]):
            raise ValueError("event envelope is missing a required identity field")
        if not isinstance(raw.get("payload"), dict):
            raise ValueError("event envelope payload must be an object")
        version = raw.get("event_version", SUPPORTED_EVENT_VERSION)
        if version != SUPPORTED_EVENT_VERSION:
            raise ValueError(f"unsupported event version: {version!r}")
        return cls(
            event_id=raw["event_id"],
            event_type=raw["event_type"],
            event_version=version,
            aggregate_type=raw["aggregate_type"],
            aggregate_id=raw["aggregate_id"],
            actor_id=raw.get("actor_id"),
            tenant_id=raw.get("tenant_id"),
            request_id=raw.get("request_id"),
            correlation_id=raw.get("correlation_id"),
            payload=raw["payload"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
        }


class QueueTransport(Protocol):
    def publish(self, queue_url: str, message: dict[str, Any]) -> str: ...


class ReceiveTransport(QueueTransport, Protocol):
    def receive(self, queue_url: str, *, max_messages: int, wait_seconds: int) -> list[dict[str, Any]]: ...
    def delete(self, queue_url: str, receipt_handle: str) -> None: ...
