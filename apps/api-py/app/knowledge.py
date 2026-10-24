"""Retrieval over the REEP Knowledge Base — the grounded assistant's "explain the
rules" layer.

`search()` returns approved policy/FAQ/guidance chunks that match a query. It is
the ONLY read path the assistant should use to ground an answer, and it can only
ever surface APPROVED documents whose audience admits the caller — never a live
student fact, which lives in a wholly separate table.

Retrieval strategy — HYBRID full-text + pgvector (works with or without an
embedder configured):
  FULL-TEXT  Postgres full-text: rank by ts_rank over to_tsvector('english',
             chunk_text) vs plainto_tsquery('english', query). Backed by the GIN
             index. Falls back to ILIKE when the query produces no ts matches
             (e.g. a single rare token).
  VECTOR     When an embedder is configured, the query is embedded and pgvector
             finds the nearest chunks DB-side with `embedding <=> :query_vec`
             (cosine distance, `.cosine_distance(...)`). This surfaces
             semantically-related chunks that share no literal tokens.
  BLEND      The two candidate sets are merged by chunk id and re-ranked on a
             blend of normalised full-text rank and cosine similarity, so a chunk
             strong on either signal ranks well.

Without an embedder it degrades cleanly to full-text alone — the KB still works
with zero embedding setup. Returns [] when nothing approved matches — the caller
then shows "no approved answer" rather than inventing one.

pgvector runs in the `pgvector/pgvector:pg17` docker image (`CREATE EXTENSION
vector`); `KnowledgeChunk.embedding` is a dimensionless `vector`. The KB is small
and curated, so an exact cosine scan needs no ivfflat/hnsw index.
"""

from __future__ import annotations

import math

from sqlalchemy import String, bindparam, func, or_, select
from sqlalchemy.orm import Session

from .ai.embeddings import embed, embedder_configured
from .models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeStatus

# How many candidates to pull per branch before the blended re-rank.
_CANDIDATE_POOL = 24
# Weight given to the embedding-cosine signal when blending (rest is full-text).
_COSINE_WEIGHT = 0.5
# Relevance FLOOR for the vector branch: a chunk is admitted as a semantic match
# only when its cosine DISTANCE is at or below this. Vector KNN otherwise always
# returns *something* (the nearest chunks, however far), which would manufacture
# an answer for an off-topic or gibberish query and defeat the "no approved
# answer" honest fallback. Calibrated to the embedding model's floor: genuine
# matches sit at distance <= ~0.30 while unrelated text floors around ~0.35+, so
# this sits in the gap. Biased conservative on purpose — a borderline chunk with
# no full-text overlap is better dropped (fall back to "check with your mentor")
# than surfaced as if approved. Full-text remains the primary answer-existence
# signal, so this only gates the *extra* semantic-only candidates.
_MAX_VEC_DISTANCE = 0.32


def _audience_filter(audience: str):
    return KnowledgeDocument.audience.in_([audience, "all"])


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search(
    db: Session,
    query: str,
    audience: str = "student",
    limit: int = 5,
) -> list[dict]:
    """Return up to `limit` approved KB chunks matching `query`.

    Each result: {chunk_text, document_title, source_type, source_url, anchor,
    score}. Only APPROVED documents whose audience is `audience` or 'all' are
    considered. Returns [] when nothing matches.
    """
    q = (query or "").strip()
    if not q:
        return []

    base_where = (
        KnowledgeDocument.status == KnowledgeStatus.APPROVED,
        _audience_filter(audience),
    )

    def _row_fields(row) -> dict:
        return {
            "chunk_text": row.chunk_text,
            "anchor": row.anchor,
            "embedding": row.embedding,
            "title": row.title,
            "source_type": row.source_type,
            "source_url": row.source_url,
        }

    # Candidates keyed by chunk id, carrying both signals: a full-text `rank`
    # (0 when only the vector branch found it) and a cosine `distance` (None when
    # only full-text found it). Merging by id lets a chunk strong on EITHER signal
    # survive to the blended re-rank below.
    cand: dict[str, dict] = {}

    # --- FULL-TEXT branch --------------------------------------------------- #
    ts_vector = func.to_tsvector("english", KnowledgeChunk.chunk_text)
    ts_query = func.plainto_tsquery("english", bindparam("q", value=q, type_=String))
    rank = func.ts_rank(ts_vector, ts_query).label("rank")
    ft_stmt = (
        select(
            KnowledgeChunk.id,
            KnowledgeChunk.chunk_text,
            KnowledgeChunk.anchor,
            KnowledgeChunk.embedding,
            KnowledgeDocument.title,
            KnowledgeDocument.source_type,
            KnowledgeDocument.source_url,
            rank,
        )
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(*base_where)
        .where(ts_vector.op("@@")(ts_query))  # "@@" is the text-search match op
        .order_by(rank.desc())
        .limit(_CANDIDATE_POOL)
    )
    for r in db.execute(ft_stmt).all():
        cand[r.id] = {**_row_fields(r), "rank": float(r.rank), "distance": None}

    if not cand:
        # FALLBACK: ILIKE over the raw tokens when ts produced nothing.
        tokens = [t for t in q.split() if len(t) > 1] or [q]
        ilike_clauses = [KnowledgeChunk.chunk_text.ilike(f"%{tok}%") for tok in tokens]
        fallback = (
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.chunk_text,
                KnowledgeChunk.anchor,
                KnowledgeChunk.embedding,
                KnowledgeDocument.title,
                KnowledgeDocument.source_type,
                KnowledgeDocument.source_url,
            )
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(*base_where)
            .where(or_(*ilike_clauses))
            .limit(_CANDIDATE_POOL)
        )
        for r in db.execute(fallback).all():
            cand[r.id] = {**_row_fields(r), "rank": 0.0, "distance": None}

    # --- VECTOR branch (pgvector nearest-neighbour, DB-side) ---------------- #
    query_vec: list[float] | None = None
    if embedder_configured():
        vecs = embed([q])
        if vecs:
            query_vec = vecs[0]
    if query_vec is not None:
        # cosine_distance renders `embedding <=> :query_vec`; ORDER BY it to pull
        # the nearest chunks even when they share no literal token with the query.
        # Gate on _MAX_VEC_DISTANCE so only CONFIDENT semantic matches are added —
        # unbounded KNN would otherwise always return the nearest few and bypass
        # the honest "no approved answer" fallback for off-topic queries.
        dist_expr = KnowledgeChunk.embedding.cosine_distance(query_vec)
        distance = dist_expr.label("distance")
        vec_stmt = (
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.chunk_text,
                KnowledgeChunk.anchor,
                KnowledgeChunk.embedding,
                KnowledgeDocument.title,
                KnowledgeDocument.source_type,
                KnowledgeDocument.source_url,
                distance,
            )
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(*base_where)
            .where(KnowledgeChunk.embedding.isnot(None))
            .where(dist_expr <= _MAX_VEC_DISTANCE)
            .order_by(distance)
            .limit(_CANDIDATE_POOL)
        )
        for r in db.execute(vec_stmt).all():
            if r.id in cand:
                cand[r.id]["distance"] = float(r.distance)
            else:
                cand[r.id] = {**_row_fields(r), "rank": 0.0, "distance": float(r.distance)}

    if not cand:
        return []

    # --- Blend + re-rank ---------------------------------------------------- #
    max_rank = max((c["rank"] for c in cand.values()), default=0.0) or 1.0
    scored: list[dict] = []
    for c in cand.values():
        ft = c["rank"] / max_rank  # 0..1 (0 for vector-only or ILIKE candidates)
        # Cosine similarity: prefer the DB distance; else compute from the vector.
        sim: float | None = None
        if c["distance"] is not None:
            sim = 1.0 - c["distance"]
        elif query_vec is not None and c["embedding"] is not None:
            sim = _cosine(query_vec, list(c["embedding"]))
        score = (
            (1 - _COSINE_WEIGHT) * ft + _COSINE_WEIGHT * sim if sim is not None else ft
        )
        scored.append(
            {
                "chunk_text": c["chunk_text"],
                "document_title": c["title"],
                "source_type": c["source_type"],
                "source_url": c["source_url"],
                "anchor": c["anchor"],
                "score": round(float(score), 6),
            }
        )

    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:limit]
