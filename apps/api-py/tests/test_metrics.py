"""GET /api/agent/metrics — the DIRECTOR/ADMIN assistant-health dashboard.

The contract these tests pin:

  * Role-gated: a STUDENT is 403; a DIRECTOR gets the aggregate.
  * The payload shape is stable (rates, breakdowns, voice + feedback tallies).
  * The `resolved` grounding signal actually drives resolution/refusal rate — a
    resolved run and a refused run move the numbers, and the endpoint's rates
    match a direct count over the column.

Hits the seeded dev DB through the TestClient; skipped when Postgres is down.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.user import Role, User


def _a_user_id() -> str:
    with SessionLocal() as db:
        u = db.query(User).first()
        assert u is not None, "seeded DB has no users"
        return u.id


def _add_run(db, actor_id: str, *, resolved: bool | None, intent: str, model: str):
    db.add(
        AgentRun(
            actor_id=actor_id,
            role=Role.STUDENT,
            scope="self",
            question="q",
            answer="a",
            status=AgentRunStatus.ANSWERED,
            trace=[],
            citations=[],
            model=model,
            intent=intent,
            resolved=resolved,
        )
    )


@requires_db
def test_metrics_requires_auth(client):
    client.cookies.clear()
    r = client.get("/api/agent/metrics")
    assert r.status_code == 401, r.text


@requires_db
def test_metrics_forbidden_for_student(client, login):
    headers = login("student@bgscet.ac.in", "student123")
    r = client.get("/api/agent/metrics", headers=headers)
    assert r.status_code == 403, r.text


@requires_db
def test_metrics_shape_for_director(client, login):
    headers = login("admin@bgscet.ac.in", "admin123")
    r = client.get("/api/agent/metrics", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {
        "total_runs",
        "resolution_rate",
        "refusal_rate",
        "avg_duration_ms",
        "by_intent",
        "by_model",
        "by_status",
        "voice_turns",
        "feedback",
    }
    assert isinstance(body["by_intent"], dict)
    assert isinstance(body["by_model"], dict)
    assert isinstance(body["by_status"], dict)
    assert set(body["feedback"]) == {"helpful", "not_helpful", "report"}
    assert 0.0 <= body["resolution_rate"] <= 1.0
    assert 0.0 <= body["refusal_rate"] <= 1.0


@requires_db
def test_resolved_and_refused_runs_move_resolution_rate(client, login):
    headers = login("admin@bgscet.ac.in", "admin123")
    actor = _a_user_id()

    m1 = client.get("/api/agent/metrics", headers=headers).json()

    # Two grounded (resolved) runs and one refused (resolved=False) run.
    with SessionLocal() as db:
        _add_run(db, actor, resolved=True, intent="readiness", model="test:model")
        _add_run(db, actor, resolved=True, intent="policy", model="test:model")
        _add_run(db, actor, resolved=False, intent="general", model="test:model")
        db.commit()

    m2 = client.get("/api/agent/metrics", headers=headers).json()

    # The three new runs are reflected in the totals and the intent breakdown.
    assert m2["total_runs"] == m1["total_runs"] + 3
    assert m2["by_intent"].get("readiness", 0) >= m1["by_intent"].get("readiness", 0) + 1
    assert m2["by_model"].get("test:model", 0) >= 3

    # The endpoint's rates match a direct count over the `resolved` column —
    # proving the grounding signal drives resolution/refusal, not a guess.
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(AgentRun))
        rtrue = db.scalar(
            select(func.count()).select_from(AgentRun).where(AgentRun.resolved.is_(True))
        )
        rknown = db.scalar(
            select(func.count()).select_from(AgentRun).where(AgentRun.resolved.isnot(None))
        )
    assert m2["resolution_rate"] == round(rtrue / total, 4)
    assert m2["refusal_rate"] == round(1 - rtrue / rknown, 4)
