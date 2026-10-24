"""Embedding worker primitives; provider calls are isolated from HTTP requests."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.redesign import EmbeddingModel, EmbeddingStatus, KnowledgeChunkEmbedding, KnowledgeChunkV2
from .leasing import retry_job


class EmbeddingProvider(Protocol):
    def embed(self, text: str, *, model_name: str) -> list[float]: ...


class PermanentEmbeddingError(ValueError):
    pass


def validate_vector(vector: Any, expected_dimension: int) -> list[float]:
    if not isinstance(vector, list) or len(vector) != expected_dimension:
        raise PermanentEmbeddingError(f"embedding dimension mismatch: expected {expected_dimension}")
    result: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise PermanentEmbeddingError("embedding contains a non-finite or non-numeric value")
        result.append(float(value))
    return result


def claim_embedding(db: Session, embedding_id: str, *, owner: str, lease_seconds: int = 300) -> tuple[KnowledgeChunkEmbedding | None, str | None]:
    now = datetime.now(timezone.utc)
    row = db.scalar(select(KnowledgeChunkEmbedding).where(KnowledgeChunkEmbedding.id == embedding_id).with_for_update())
    if row is None:
        return None, "missing"
    if row.status == EmbeddingStatus.READY:
        return row, "ready"
    if row.lease_until and row.lease_until > now and row.lease_owner != owner:
        return row, "owned"
    row.lease_owner = owner
    row.lease_token = uuid.uuid4().hex
    row.lease_until = now + timedelta(seconds=lease_seconds)
    row.attempt_count += 1
    row.available_at = now
    db.flush()
    return row, "claimed"


def process_embedding(db: Session, embedding_id: str, *, owner: str, provider: EmbeddingProvider) -> str:
    row, state = claim_embedding(db, embedding_id, owner=owner)
    if state == "missing":
        return "missing"
    if state in ("ready", "owned"):
        db.rollback()
        return "noop"
    assert row is not None
    token = row.lease_token or ""
    chunk = db.get(KnowledgeChunkV2, row.chunk_id)
    model = db.get(EmbeddingModel, row.embedding_model_id)
    if chunk is None or model is None:
        db.rollback()
        with db.begin():
            fresh = db.get(KnowledgeChunkEmbedding, embedding_id)
            if fresh is not None:
                fresh.status = EmbeddingStatus.FAILED
                fresh.last_error = "embedding source row or model is missing"
                fresh.lease_owner = None
                fresh.lease_token = None
                fresh.lease_until = None
        return "failed"
    db.commit()
    try:
        vector = validate_vector(provider.embed(chunk.normalized_text, model_name=model.model_name), model.dimension)
    except PermanentEmbeddingError as exc:
        with db.begin():
            fresh = db.get(KnowledgeChunkEmbedding, embedding_id)
            if fresh is not None and fresh.lease_token == token:
                fresh.status = EmbeddingStatus.FAILED
                fresh.last_error = str(exc)[:1000]
                fresh.dimension = len(vector) if 'vector' in locals() and isinstance(vector, list) else None
                fresh.lease_owner = None
                fresh.lease_token = None
                fresh.lease_until = None
        return "failed"
    except Exception as exc:
        with db.begin():
            fresh = db.get(KnowledgeChunkEmbedding, embedding_id)
            if fresh is not None and fresh.lease_token == token:
                fresh.status = EmbeddingStatus.PENDING
                fresh.last_error = str(exc)[:1000]
                fresh.available_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 2 ** min(fresh.attempt_count, 10)))
                fresh.lease_owner = None
                fresh.lease_token = None
                fresh.lease_until = None
        return "retry"
    with db.begin():
        fresh = db.get(KnowledgeChunkEmbedding, embedding_id)
        if fresh is None or fresh.lease_token != token:
            return "lease_lost"
        fresh.embedding = vector
        fresh.dimension = len(vector)
        fresh.status = EmbeddingStatus.READY
        fresh.generated_at = datetime.now(timezone.utc)
        fresh.last_error = None
        fresh.lease_owner = None
        fresh.lease_token = None
        fresh.lease_until = None
    return "ready"


def make_provider(call: Callable[[str, str], list[float]]) -> EmbeddingProvider:
    class _Provider:
        def embed(self, text: str, *, model_name: str) -> list[float]:
            return call(text, model_name)

    return _Provider()
