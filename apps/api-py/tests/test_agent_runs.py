"""The REEP Agent screen re-pointed a client at POST /api/agent/ask (2026-09).

apps/web/src/app/features/agent/ is the Knowledge-Base text helper from the
design handoff. It sends every question to /ask, reads /history, clears with
DELETE /conversation and rates answers through /feedback — which is the UI
caller that AGENT_RUNS_COLLECTED (app/routers/agent.py) was waiting for.

The contract these tests pin:

  * AGENT_RUNS_COLLECTED is True: the feedback tombstone is retired and the
    metrics payload reports a LIVE collector (collected=True, note=None).
  * /ask persists exactly one AgentRun per question, for a STUDENT and for a
    MENTOR alike, and the id it returns is that row — the screen attaches
    feedback to it, so a run_id that names nothing would 404 every rating.
  * The persisted row carries the structured answer (actions in `trace`,
    sources in `citations`) and the caller's role/scope, so the audit trail
    says what the student was shown, not just that they asked.
  * A non-student gets a 200 with the stated limitation, never a 4xx: the
    screen is routed under /student, /mentor and /director and must degrade
    honestly rather than break for staff.
  * A mentor can rate their OWN run (the ownership rule is per caller, not
    per role) and an unknown run id is the bare "Run not found." again.

Hits the seeded dev DB through the TestClient; skipped when Postgres is down.
"""

from conftest import requires_db

from app.db import SessionLocal
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.user import Role
from app.routers import agent as agent_router


def _ask(client, headers, monkeypatch, message: str) -> dict:
    import app.ai.orchestrator as orch

    # No provider => the deterministic builders answer; nothing leaves the box.
    monkeypatch.setattr(orch, "llm_config", lambda: None)
    r = client.post("/api/agent/ask", headers=headers, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


def test_agent_runs_are_collected_again():
    # The REEP Agent screen is the caller this constant was waiting for. If it
    # is ever False again, the screen's thumbs-up/down will 404 with the
    # tombstone text and the director dashboard will read as a frozen history.
    assert agent_router.AGENT_RUNS_COLLECTED is True


@requires_db
def test_ask_persists_one_run_per_question_for_a_student(client, login, monkeypatch):
    headers = login("student@bgscet.ac.in", "student123")
    with SessionLocal() as db:
        before = db.query(AgentRun).count()

    body = _ask(client, headers, monkeypatch, "Am I placement-ready?")

    with SessionLocal() as db:
        assert db.query(AgentRun).count() == before + 1
        run = db.get(AgentRun, body["run_id"])
        assert run is not None, "AskOut.run_id must name a persisted AgentRun"
        assert run.role == Role.STUDENT
        assert run.scope == "self"
        assert run.status == AgentRunStatus.ANSWERED
        assert run.question == "Am I placement-ready?"
        assert run.answer == body["answer"]
        # The structured answer is the audit record, not just the prose.
        assert run.trace == body["actions"]
        assert run.citations == body["sources"]
        assert run.intent  # classified on every path
        assert run.resolved is True  # grounded in the student's own record


@requires_db
def test_ask_degrades_honestly_and_still_persists_for_a_mentor(client, login, monkeypatch):
    headers = login("mentor@bgscet.ac.in", "mentor123")
    body = _ask(client, headers, monkeypatch, "Am I placement-ready?")

    # 200, not 4xx: the screen is mounted for staff too and must say why the
    # personalised answer is unavailable rather than break.
    assert body["limitations"] == ["Personalised tools are student-only."]
    assert body["actions"] == []
    assert body["sources"] == []
    assert body["run_id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, body["run_id"])
        assert run is not None
        assert run.role == Role.MENTOR
        assert run.scope == "programme"
        assert run.resolved is False  # refused, so not grounded in the caller's data


@requires_db
def test_mentor_can_rate_their_own_run(client, login, monkeypatch):
    headers = login("mentor@bgscet.ac.in", "mentor123")
    run_id = _ask(client, headers, monkeypatch, "How do I verify a skill?")["run_id"]
    r = client.post(
        "/api/agent/feedback",
        headers=headers,
        json={"run_id": run_id, "rating": "HELPFUL"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


@requires_db
def test_feedback_tombstone_is_retired(client, login):
    # With the collector live, an unknown run id is the bare "Run not found."
    # for every caller — the "this account has no assistant runs" explanation
    # only ever applied while nothing wrote runs.
    headers = login("alumni@bgscet.ac.in", "alumni123")
    r = client.post(
        "/api/agent/feedback",
        headers=headers,
        json={"run_id": "does-not-exist", "rating": "HELPFUL"},
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Run not found."


@requires_db
def test_metrics_report_a_live_collector(client, login):
    headers = login("admin@bgscet.ac.in", "admin123")
    r = client.get("/api/agent/metrics", headers=headers)
    assert r.status_code == 200, r.text
    agent_runs = r.json()["agent_runs"]
    assert agent_runs["collected"] is True
    assert agent_runs["note"] is None
    assert agent_runs["rows"] >= 1
