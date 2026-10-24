"""Pluggable text embedder for the Knowledge Base.

The KB stores PUBLIC policy text, so embedding it and even sending it to a remote
embedding endpoint is fine — there is no student PII here (that is the whole point
of keeping knowledge separate from student facts). This module is therefore
DELIBERATELY simple and has NO hard dependency on any embedding provider:

    embed(texts) -> list[list[float]] | None

Returns None when no embedder is configured; every caller then falls back to
Postgres full-text retrieval, so the KB works with zero embedding setup.

Provider selection mirrors the universal LLM adapter (`app/ai/llm.py`): "one set
of keys, any provider, no code change". In priority order —
  1. an explicit `embedding_base_url` + `embedding_model` (any OpenAI-compatible
     `/embeddings` endpoint), else
  2. `MISTRAL_API_KEY` -> Mistral `mistral-embed` (OpenAI-compatible, 1024-dim).
Both POST to `{base}/embeddings`. (The KB is public policy text, so this is
outside the student-data egress gate — nothing private is embedded.)
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import httpx

from ..config import settings
from ..tracing import span

log = logging.getLogger(__name__)

# Mistral's OpenAI-compatible embeddings endpoint + model (used when no explicit
# embedding_* is configured but a Mistral key is present).
_MISTRAL_BASE = "https://api.mistral.ai/v1"
_MISTRAL_MODEL = "mistral-embed"


class _Embedder(NamedTuple):
    base: str
    model: str
    key: str


def _resolve_embedder() -> _Embedder | None:
    """Pick an embeddings provider from config, or None to fall back to full-text.

    Explicit LLM-style `embedding_*` wins; otherwise auto-select Mistral from
    `MISTRAL_API_KEY` (the KB is public text, so a remote embedder is fine)."""
    base = settings.embedding_base_url.strip()
    model = settings.embedding_model.strip()
    if base and model:
        return _Embedder(base.rstrip("/"), model, settings.embedding_api_key.strip())

    mistral = settings.mistral_api_key.strip()
    if mistral:
        return _Embedder(_MISTRAL_BASE, _MISTRAL_MODEL, mistral)

    return None


def embedder_configured() -> bool:
    """True when an embedding provider is available (explicit or auto-selected)."""
    return _resolve_embedder() is not None


def embed(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch of texts via an OpenAI-compatible /embeddings endpoint.

    Returns a list of float vectors aligned with `texts`, or None when no
    embedder is configured (the caller then uses full-text retrieval). Any
    provider/network error is logged and swallowed as None — the KB must never
    hard-fail just because an optional embedder is down.
    """
    provider = _resolve_embedder()
    if not texts or provider is None:
        return None

    base, model, key = provider

    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"

    try:
        # Every embedding in the app goes through here, so one span covers the
        # Knowledge Base's hybrid retrieval and anything added later. It is also
        # the slowest hop in a KB search by an order of magnitude — a remote
        # HTTPS round trip against two Postgres queries — which is exactly the
        # thing a trace should show and a log line cannot. Count and model only;
        # the texts are never attached.
        with span(
            "embeddings.embed",
            f"{model} x{len(texts)}",
            model=model,
            texts=len(texts),
        ):
            resp = httpx.post(
                f"{base}/embeddings",
                json={"model": model, "input": texts},
                headers=headers,
                timeout=max(1.0, settings.llm_timeout_ms / 1000),
            )
        resp.raise_for_status()
        data = resp.json()["data"]
        # OpenAI returns rows possibly out of order; sort by index to realign.
        rows = sorted(data, key=lambda r: r.get("index", 0))
        return [r["embedding"] for r in rows]
    except Exception:
        log.exception("embedding call failed (base=%s model=%s)", base, model)
        return None


def embed_one(text: str, *, model_name: str) -> list[float]:
    """Strict worker-facing call; failures are raised, never silently swallowed."""
    provider = _resolve_embedder()
    if provider is None:
        raise RuntimeError("embedding provider is not configured")
    base, configured_model, key = provider
    if configured_model != model_name:
        raise ValueError(f"configured embedding model {configured_model!r} does not match job model {model_name!r}")
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    response = httpx.post(
        f"{base}/embeddings",
        json={"model": model_name, "input": [text]},
        headers=headers,
        timeout=max(1.0, settings.llm_timeout_ms / 1000),
    )
    response.raise_for_status()
    rows = response.json().get("data")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("embedding provider returned an invalid response")
    vector = rows[0].get("embedding")
    if not isinstance(vector, list):
        raise ValueError("embedding provider returned no vector")
    return vector


def reembed_all(db) -> int:
    """Populate KnowledgeChunk.embedding for every chunk when a provider exists.

    No-op (returns 0) when no embedder is configured. Batches the chunk texts,
    calls embed(), and writes the vectors back. Safe to re-run — it simply
    overwrites with fresh vectors.
    """
    from sqlalchemy import select

    from ..models.knowledge import KnowledgeChunk

    if not embedder_configured():
        return 0

    chunks = db.scalars(select(KnowledgeChunk)).all()
    if not chunks:
        return 0

    updated = 0
    BATCH = 64
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i : i + BATCH]
        vectors = embed([c.chunk_text for c in batch])
        if vectors is None or len(vectors) != len(batch):
            log.warning("reembed_all: embedder returned no/mismatched vectors, stopping")
            break
        for chunk, vec in zip(batch, vectors):
            chunk.embedding = vec
            updated += 1
        db.commit()
    return updated
