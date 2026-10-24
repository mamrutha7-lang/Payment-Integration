"""REEP Knowledge Base — retrieval contract + the STUDENT-only search endpoint.

The KB is the grounded assistant's "explain the rules" layer: APPROVED
policy/FAQ/guidance only, never a live student fact. These tests pin that:

  * a natural-language question lands on the right approved chunk,
  * an off-topic query returns nothing (or a strictly weaker match),
  * only APPROVED documents are ever surfaced (a DRAFT is invisible),
  * GET /api/agent/knowledge/search is student-only and returns the same hits.

They use the seeded dev DB (skipped when Postgres is unreachable) and clean up
every throwaway row they create.
"""

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app import knowledge
from app.ai import embeddings
from app.db import SessionLocal
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeStatus
from app.seed_kb import seed_knowledge

# pgvector semantic tests only mean anything when an embedder is configured and
# the chunks are embedded; without a provider the KB runs on full-text alone.
requires_embedder = pytest.mark.skipif(
    not embeddings.embedder_configured(),
    reason="no embedding provider configured (KB runs full-text only)",
)


@pytest.fixture(scope="module", autouse=True)
def _ensure_kb():
    """Idempotently make sure the seeded KB is present for this module, and — when
    an embedder is configured — that its chunks carry embeddings (so the pgvector
    branch is actually exercised)."""
    with SessionLocal() as db:
        seed_knowledge(db)
        if embeddings.embedder_configured():
            embeddings.reembed_all(db)


@requires_db
def test_skill_verification_query_lands_on_the_right_doc():
    with SessionLocal() as db:
        hits = knowledge.search(db, "how do I verify a Power BI skill")
    assert hits, "expected at least one approved match"
    assert hits[0]["document_title"] == "Verifying a skill (e.g. Power BI)"
    # The winning chunk is the one that actually explains verification.
    assert "verified" in hits[0]["chunk_text"].lower()


@requires_db
def test_documents_query_lands_on_the_placement_docs_doc():
    with SessionLocal() as db:
        hits = knowledge.search(db, "what documents do I need before placements")
    assert hits
    assert hits[0]["document_title"] == "Documents needed before placements"


@requires_db
def test_offtopic_query_returns_nothing_or_a_weaker_match():
    with SessionLocal() as db:
        on_topic = knowledge.search(db, "how do I verify a Power BI skill")
        off_topic = knowledge.search(db, "photosynthesis chlorophyll stomata xylem")
    on_top = on_topic[0]["score"] if on_topic else 0.0
    off_top = off_topic[0]["score"] if off_topic else 0.0
    # Either nothing comes back, or it ranks strictly below a real match.
    assert not off_topic or off_top < on_top


@requires_db
def test_only_approved_documents_are_returned():
    """An APPROVED doc is found; flipping it to DRAFT removes it from results."""
    token = f"zylophonic{uuid.uuid4().hex[:8]}"  # a rare term unique to this doc
    with SessionLocal() as db:
        doc = KnowledgeDocument(
            title=f"Temp policy {token}",
            source_type="policy",
            status=KnowledgeStatus.APPROVED,
            audience="student",
        )
        doc.chunks = [
            KnowledgeChunk(chunk_text=f"This unique {token} clause explains a temporary rule.")
        ]
        db.add(doc)
        db.commit()
        doc_id = doc.id

    try:
        with SessionLocal() as db:
            found = knowledge.search(db, token)
            assert found, "APPROVED doc with the unique term should be found"
            assert any(token in h["chunk_text"] for h in found)

        # Flip to DRAFT — it must vanish from retrieval.
        with SessionLocal() as db:
            d = db.get(KnowledgeDocument, doc_id)
            d.status = KnowledgeStatus.DRAFT
            db.commit()

        with SessionLocal() as db:
            after = knowledge.search(db, token)
            assert not any(token in h["chunk_text"] for h in after), (
                "a DRAFT document must never be surfaced"
            )
    finally:
        with SessionLocal() as db:
            db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))
            db.commit()


@requires_db
def test_empty_query_returns_empty():
    with SessionLocal() as db:
        assert knowledge.search(db, "   ") == []


# ---------------------------------------------------------------------------
# pgvector semantic retrieval (only when an embedder is configured)
# ---------------------------------------------------------------------------
@requires_db
@requires_embedder
def test_semantic_query_with_no_shared_tokens_lands_on_the_right_doc():
    """A paraphrase that shares NO literal token with the KB wording still
    retrieves the skill-verification doc — the payoff of the vector branch that
    pure full-text cannot deliver."""
    with SessionLocal() as db:
        hits = knowledge.search(db, "prove my competency to a recruiter")
    assert hits, "vector search should surface a semantically-related chunk"
    titles = [h["document_title"] for h in hits]
    assert "Verifying a skill (e.g. Power BI)" in titles


@requires_db
@requires_embedder
def test_vector_threshold_preserves_the_honest_fallback():
    """Vector KNN always has a nearest neighbour; the distance floor must stop it
    from manufacturing a match for a gibberish query, so retrieval returns
    nothing and the caller can fall back to 'no approved answer'."""
    with SessionLocal() as db:
        hits = knowledge.search(db, "zqwxplorbnix vfgtrkzylophonic quzzmatic bxqptlwr")
    assert hits == [], "an out-of-vocabulary query must not be forced to a match"


# ---------------------------------------------------------------------------
# Endpoint: GET /api/agent/knowledge/search — STUDENT only
# ---------------------------------------------------------------------------
@requires_db
def test_endpoint_returns_hits_for_a_student(client, login):
    headers = login("student@bgscet.ac.in", "student123")
    r = client.get(
        "/api/agent/knowledge/search",
        headers=headers,
        params={"q": "how do I verify a Power BI skill"},
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results
    assert results[0]["document_title"] == "Verifying a skill (e.g. Power BI)"
    # Only approved, and the shape the orchestrator expects.
    assert set(results[0]) == {
        "chunk_text", "document_title", "source_type", "source_url", "anchor", "score",
    }


@requires_db
def test_endpoint_is_forbidden_for_a_mentor(client, login):
    headers = login("mentor@bgscet.ac.in", "mentor123")
    r = client.get(
        "/api/agent/knowledge/search",
        headers=headers,
        params={"q": "how do I verify a Power BI skill"},
    )
    assert r.status_code == 403, r.text


@requires_db
def test_endpoint_requires_auth(client):
    client.cookies.clear()
    r = client.get(
        "/api/agent/knowledge/search",
        params={"q": "anything"},
    )
    assert r.status_code == 401, r.text
