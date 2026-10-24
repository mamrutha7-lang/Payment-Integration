"""POST /api/agent/feedback — ownership, upsert, and PII redaction.

The contract these tests pin:

  * A feedback row can only be attached to a run the CALLER owns. Someone else's
    (or a non-existent) run id is a 404 either way — no existence leak.
  * One row per (run, owner): a re-vote UPDATES the existing row, never
    duplicates.
  * The free-text note is PII-redacted before it is stored.

They hit the seeded dev DB through the TestClient and are skipped when Postgres
is unreachable.
"""

import pytest

from conftest import requires_db

from app.db import SessionLocal
from app.models.feedback import AssistantFeedback, FeedbackRating
from app.redaction import redact_pii


def _ask_run_id(client, headers, monkeypatch) -> str:
    """Create one structured run for the caller and return its AgentRun id."""
    import app.ai.orchestrator as orch

    # No provider => deterministic student-data answer (no polish, no network).
    monkeypatch.setattr(orch, "llm_config", lambda: None)
    r = client.post(
        "/api/agent/ask", headers=headers, json={"message": "Am I placement-ready?"}
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert run_id
    return run_id


@requires_db
def test_feedback_requires_auth(client):
    client.cookies.clear()
    r = client.post("/api/agent/feedback", json={"run_id": "x", "rating": "HELPFUL"})
    assert r.status_code == 401, r.text


@requires_db
def test_feedback_on_own_run_is_stored(client, login, monkeypatch):
    headers = login("student@bgscet.ac.in", "student123")
    run_id = _ask_run_id(client, headers, monkeypatch)

    r = client.post(
        "/api/agent/feedback",
        headers=headers,
        json={"run_id": run_id, "rating": "HELPFUL", "note": "clear and useful"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    with SessionLocal() as db:
        rows = (
            db.query(AssistantFeedback)
            .filter(AssistantFeedback.run_id == run_id)
            .all()
        )
    assert len(rows) == 1
    assert rows[0].rating == FeedbackRating.HELPFUL


@requires_db
def test_feedback_on_another_users_run_is_404_no_leak(client, login, monkeypatch):
    # The student owns the run; the mentor must not be able to touch it, and the
    # response must be indistinguishable from a run that does not exist.
    student_headers = login("student@bgscet.ac.in", "student123")
    run_id = _ask_run_id(client, student_headers, monkeypatch)

    mentor_headers = login("mentor@bgscet.ac.in", "mentor123")
    r_foreign = client.post(
        "/api/agent/feedback",
        headers=mentor_headers,
        json={"run_id": run_id, "rating": "HELPFUL"},
    )
    r_missing = client.post(
        "/api/agent/feedback",
        headers=mentor_headers,
        json={"run_id": "does-not-exist", "rating": "HELPFUL"},
    )
    assert r_foreign.status_code == 404, r_foreign.text
    assert r_missing.status_code == 404
    # Same body — the foreign run is not distinguishable from a missing one.
    assert r_foreign.json() == r_missing.json()

    # And nothing was written for the mentor against the student's run.
    with SessionLocal() as db:
        n = (
            db.query(AssistantFeedback)
            .filter(AssistantFeedback.run_id == run_id)
            .count()
        )
    assert n == 0


@requires_db
def test_feedback_revote_upserts_not_duplicates(client, login, monkeypatch):
    headers = login("student@bgscet.ac.in", "student123")
    run_id = _ask_run_id(client, headers, monkeypatch)

    r1 = client.post(
        "/api/agent/feedback",
        headers=headers,
        json={"run_id": run_id, "rating": "HELPFUL"},
    )
    r2 = client.post(
        "/api/agent/feedback",
        headers=headers,
        json={"run_id": run_id, "rating": "NOT_HELPFUL", "note": "changed my mind"},
    )
    assert r1.status_code == 200 and r2.status_code == 200

    with SessionLocal() as db:
        rows = (
            db.query(AssistantFeedback)
            .filter(AssistantFeedback.run_id == run_id)
            .all()
        )
    assert len(rows) == 1, "a re-vote must update the single row, not duplicate it"
    assert rows[0].rating == FeedbackRating.NOT_HELPFUL
    assert rows[0].note == "changed my mind"


@requires_db
def test_feedback_note_is_pii_redacted(client, login, monkeypatch):
    headers = login("student@bgscet.ac.in", "student123")
    run_id = _ask_run_id(client, headers, monkeypatch)

    note = "reach me at ravi.kumar@gmail.com or 9876543210, USN 1BG21CS042"
    r = client.post(
        "/api/agent/feedback",
        headers=headers,
        json={"run_id": run_id, "rating": "REPORT", "note": note},
    )
    assert r.status_code == 200, r.text

    with SessionLocal() as db:
        row = (
            db.query(AssistantFeedback)
            .filter(AssistantFeedback.run_id == run_id)
            .one()
        )
    stored = row.note or ""
    assert "ravi.kumar@gmail.com" not in stored
    assert "9876543210" not in stored
    assert "1BG21CS042" not in stored
    assert "[redacted]" in stored


# --- redact_pii unit checks (pure, no DB) ------------------------------------


@pytest.mark.parametrize(
    "raw,gone",
    [
        ("email me at a.b+tag@college.ac.in please", "a.b+tag@college.ac.in"),
        ("call +91 98765 43210 tomorrow", "98765 43210"),
        ("my usn is 1BG21CS042 ok", "1BG21CS042"),
    ],
)
def test_redact_pii_removes_obvious_pii(raw, gone):
    out = redact_pii(raw)
    assert gone not in out
    assert "[redacted]" in out


def test_redact_pii_passthrough_for_empty():
    assert redact_pii(None) is None
    assert redact_pii("") == ""
    # An ordinary sentence with no PII is left intact.
    assert redact_pii("this answer was helpful") == "this answer was helpful"
