"""The tool-backed orchestrator + POST /api/agent/ask.

The contract these tests pin:

  * STUDENT-DATA intents are composed DETERMINISTICALLY from the read-only tools
    and cite a 'student-record' source — the model is never the source of truth,
    and (with a remote provider) the student's PII is never sent to classify or
    to answer.
  * POLICY intents are grounded over ONLY approved knowledge chunks and cite a
    'policy' source; an unanswerable policy question yields the honest
    "no approved answer" fallback, never a guess.
  * Intent routing lands each question on the right tool.

The intent-routing tests are pure (no DB). The end-to-end tests hit the seeded
dev DB through the TestClient and are skipped when Postgres is unreachable. Where
a model call would otherwise be non-deterministic, `complete_chat` is
monkeypatched so the assertion pins behaviour, not a provider's wording.
"""

import pytest

from conftest import requires_db

from app.ai import orchestrator
from app.db import SessionLocal
from app.seed_kb import seed_knowledge


# ---------------------------------------------------------------------------
# Intent routing — pure, no DB, no PII leaves the process to classify.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question,intent",
    [
        ("Am I placement-ready?", orchestrator.READINESS),
        ("What's my readiness score?", orchestrator.READINESS),
        ("What should I complete this week?", orchestrator.GAPS),
        ("what should i do next", orchestrator.GAPS),
        ("Which jobs am I eligible for?", orchestrator.JOBS),
        ("can i apply to any jobs", orchestrator.JOBS),
        ("What are my skills?", orchestrator.SKILLS),
        ("How do I verify a Power BI skill?", orchestrator.POLICY),
        ("what does verified mean", orchestrator.POLICY),
        ("Is my profile complete?", orchestrator.PROFILE),
        ("What is due next on my courses?", orchestrator.DEADLINES),
        ("What documents do I need before placements?", orchestrator.POLICY),
        ("steps to apply for placements", orchestrator.POLICY),
        ("How are leaderboards calculated?", orchestrator.POLICY),
        ("Tell me a joke", orchestrator.GENERAL),
    ],
)
def test_intent_routing(question, intent):
    assert orchestrator.classify(question) == intent


# ---------------------------------------------------------------------------
# Orchestrator end-to-end over the seeded DB.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _ensure_kb():
    if not _db_up():
        return
    with SessionLocal() as db:
        seed_knowledge(db)


def _db_up() -> bool:
    from conftest import DB_UP

    return DB_UP


def _student_id() -> str:
    from app.models.user import Student

    with SessionLocal() as db:
        s = db.query(Student).first()
        assert s is not None, "seeded DB has no students"
        return s.id


@requires_db
def test_readiness_is_deterministic_with_score_and_weakest_factor(monkeypatch):
    # No provider => the optional polish is skipped and the deterministic tool
    # result is proven to be the source of truth for the score and the action.
    monkeypatch.setattr(orchestrator, "llm_config", lambda: None)
    with SessionLocal() as db:
        sid = _student_id()
        res = orchestrator.answer_question(db, sid, "STUDENT", "Am I placement-ready?")
        truth = orchestrator.tools.placement_readiness(db, sid)

    # The score when there is one, the BAND when there is not. A student with no
    # marks, attendance or certifications on record has `score: None` (07 §5 —
    # absence is not a low score), and the deterministic summary opens with the
    # band alone. Asserting "{score}/100" unconditionally would demand the
    # string "None/100" of the one student the guardrail is about.
    if truth["score"] is None:
        assert truth["band"] in res["answer"]
        assert "None" not in res["answer"]
    else:
        assert f"{truth['score']}/100" in res["answer"]
    assert res["sources"] == [
        {"label": "Placement readiness (your record)", "type": "student-record"}
    ]
    # Grounding signal: routed to READINESS and grounded in the student tool.
    assert res["intent"] == orchestrator.READINESS
    assert res["resolved"] is True
    # An action pointing at the weakest (unmet, highest-weight) factor, when one
    # exists. MEASURED as well as unmet: a factor with no rows behind it — no
    # marks imported, no attendance recorded — is not a weakness the student can
    # act on, and the orchestrator no longer offers one for it.
    unmet = [f for f in truth["factors"] if f["measured"] and not f["met"]]
    if unmet:
        assert res["actions"], "expected an action toward the weakest factor"
        top = sorted(unmet, key=lambda f: f["weight"], reverse=True)[0]
        route, _ = orchestrator._FACTOR_ACTION[top["label"]]
        assert res["actions"][0]["route"] == route


@requires_db
def test_gaps_actions_come_from_completion_gaps_tool():
    with SessionLocal() as db:
        sid = _student_id()
        res = orchestrator.answer_question(db, sid, "STUDENT", "What should I complete this week?")
        gaps = orchestrator.tools.completion_gaps(db, sid)

    assert res["sources"] == [{"label": "Next actions (your record)", "type": "student-record"}]
    if gaps:
        # Every action route is one the tool actually produced (deterministic, grounded).
        tool_routes = {g["cta_route"] for g in gaps}
        assert res["actions"]
        assert all(a["route"] in tool_routes for a in res["actions"])


@requires_db
def test_policy_answer_is_grounded_and_cites_a_policy_source(monkeypatch):
    # Ground the reply over ONLY the approved chunk text; pin the model's wording.
    captured = {}

    def fake_complete(messages, **kwargs):
        captured["carries_student_data"] = kwargs.get("carries_student_data")
        captured["prompt"] = messages[-1]["content"]
        return "To verify a skill, upload evidence and your mentor approves it."

    monkeypatch.setattr(orchestrator, "complete_chat", fake_complete)
    with SessionLocal() as db:
        res = orchestrator.answer_question(
            db, _student_id(), "STUDENT", "How do I verify a Power BI skill?"
        )

    # Public content path — never flagged as carrying student data.
    assert captured["carries_student_data"] is False
    assert "verify" in res["answer"].lower()
    assert res["sources"], "a grounded policy answer must cite its source"
    assert all(s["type"] == "policy" for s in res["sources"])
    # Grounding signal: POLICY intent, grounded over an approved chunk.
    assert res["intent"] == orchestrator.POLICY
    assert res["resolved"] is True
    assert any("skill" in s["label"].lower() for s in res["sources"])
    # No student PII in the limitations / no student-record source.
    assert all(s["type"] != "student-record" for s in res["sources"])


@requires_db
def test_unanswerable_policy_falls_back_honestly(monkeypatch):
    # A query whose every token is gibberish matches no approved chunk, so the
    # model is never consulted and the honest fallback is returned. (Target the
    # policy branch directly: a stopword-laden question would ILIKE-match chunks.)
    monkeypatch.setattr(
        orchestrator, "complete_chat", lambda *a, **k: "made up policy"
    )
    with SessionLocal() as db:
        res = orchestrator._policy(db, "zqwxplorbnix vfgtrkzylophonic quzzmatic bxqptlwr")
    assert res["answer"] == orchestrator.NO_POLICY_ANSWER
    assert res["sources"] == []
    assert res["limitations"] == ["No approved knowledge source matched."]
    # No approved answer -> not resolved.
    assert res["resolved"] is False


@requires_db
def test_student_data_intent_is_refused_for_a_non_student():
    with SessionLocal() as db:
        res = orchestrator.answer_question(db, None, "MENTOR", "Am I placement-ready?")
    assert res["actions"] == []
    assert res["sources"] == []
    assert res["limitations"] == ["Personalised tools are student-only."]
    # Refused for a non-student -> not resolved (but intent is still classified).
    assert res["intent"] == orchestrator.READINESS
    assert res["resolved"] is False


# ---------------------------------------------------------------------------
# POST /api/agent/ask — the structured endpoint.
# ---------------------------------------------------------------------------
@requires_db
def test_ask_endpoint_returns_structured_readiness(client, login, monkeypatch):
    import app.ai.orchestrator as orch

    # No provider inside the orchestrator => deterministic answer (no polish).
    monkeypatch.setattr(orch, "llm_config", lambda: None)
    headers = login("student@bgscet.ac.in", "student123")
    r = client.post("/api/agent/ask", headers=headers, json={"message": "Am I placement-ready?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {
        "answer", "actions", "sources", "limitations", "conversation_id", "model", "run_id"
    }
    assert "/100" in body["answer"]
    assert body["sources"] and body["sources"][0]["type"] == "student-record"
    assert body["conversation_id"]
    assert body["run_id"]  # the AgentRun id for this turn, for attaching feedback


@requires_db
def test_ask_endpoint_policy_has_policy_source_and_no_student_record(client, login, monkeypatch):
    import app.ai.orchestrator as orch

    monkeypatch.setattr(
        orch, "complete_chat", lambda *a, **k: "Upload evidence; your mentor verifies it."
    )
    headers = login("student@bgscet.ac.in", "student123")
    r = client.post(
        "/api/agent/ask", headers=headers, json={"message": "How do I verify a Power BI skill?"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sources"] and all(s["type"] == "policy" for s in body["sources"])
    assert all(s["type"] != "student-record" for s in body["sources"])


@requires_db
def test_ask_endpoint_requires_auth(client):
    client.cookies.clear()
    r = client.post("/api/agent/ask", json={"message": "anything"})
    assert r.status_code == 401, r.text
