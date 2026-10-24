"""The engine integration without a socket or a database: the catalogue rows
compile into the matrix's own contract, the question bank reaches the
instructions, the per-session cap is honoured, and the session stores marshal
honestly."""

from __future__ import annotations

from types import SimpleNamespace

from app.config import settings
from app.interview_matrix import InterviewPhase, Specialization, build_instructions
from app.voice_platform.engine.nova import compile_specialization
from app.voice_platform.storage.dynamodb import DynamoSessionStore, MemorySessionStore, _marshal


def _spec(**over):
    base = dict(id="s1", degree_level="UG", key="bsc-ai", label="BSc AI", persona="a pragmatic ML lead",
                frameworks=["bias/variance", "evaluation metrics"], syllabus=["Module 1: Search"], nova_voice="kiara", active=True)
    base.update(over)
    return SimpleNamespace(**base)


def _q(text, phase="probing", order=0, active=True):
    return SimpleNamespace(text=text, phase=phase, order_index=order, active=active)


def test_catalogue_rows_compile_into_the_matrix_contract() -> None:
    spec = compile_specialization(_spec(), [
        _q("Introduce your final-year project", "opening", 0),
        _q("Explain precision vs recall", "probing", 1),
        _q("Retired question", "probing", 2, active=False),
        _q("Design an A/B test for a ranking change", "deep_dive", 3),
    ])
    assert isinstance(spec, Specialization)
    assert spec.key == "bsc-ai" and spec.label == "BSc AI (UG)" and spec.nova_voice == "kiara"
    assert spec.sample_question == "Explain precision vs recall"
    assert spec.question_bank == (
        "[opening] Introduce your final-year project",
        "[probing] Explain precision vs recall",
        "[deep_dive] Design an A/B test for a ranking change",
    )


def test_an_empty_bank_falls_back_to_a_generic_sample_and_no_block() -> None:
    spec = compile_specialization(_spec(), [])
    assert spec.question_bank == () and "project" in spec.sample_question
    text = build_instructions(spec, "PERSONA", InterviewPhase.OPENING)
    assert "Question bank" not in text


def test_the_question_bank_is_rendered_into_the_instructions_verbatim() -> None:
    spec = compile_specialization(_spec(), [_q("Explain precision vs recall")])
    text = build_instructions(spec, "PERSONA", InterviewPhase.PROBING)
    assert text.startswith("PERSONA\n")
    assert "## Question bank for this track" in text
    assert "- [probing] Explain precision vs recall" in text
    assert "Rephrase each naturally" in text


def test_nova_session_honours_a_per_session_cap_but_never_bedrocks_wall() -> None:
    from app.interview_nova import NovaSonicSession

    fixed = NovaSonicSession(SimpleNamespace(), "conn1", max_seconds=300)
    assert fixed._effective_cap() == 300.0
    default = NovaSonicSession(SimpleNamespace(), "conn2")
    wall = float(settings.nova_sonic_connection_seconds) - 20.0
    assert default._effective_cap() == min(float(settings.interview_max_seconds), max(60.0, wall))
    huge = NovaSonicSession(SimpleNamespace(), "conn3", max_seconds=9000)
    assert huge._effective_cap() == max(60.0, wall)


def test_memory_session_store_round_trips_and_marshals_like_dynamo() -> None:
    store = MemorySessionStore()
    assert store.put({"session_id": "c1", "ratio": 0.5, "turns": 3, "tags": ("a", "b")})
    assert store.update("c1", {"status": "completed"})
    assert store.get("c1") == {"session_id": "c1", "ratio": 0.5, "turns": 3, "tags": ["a", "b"], "status": "completed"}
    assert store.get("missing") is None
    assert str(_marshal(0.1)) == "0.1"


def test_dynamo_store_builds_a_set_expression_per_field() -> None:
    calls: list[dict] = []

    class Client:
        def put_item(self, **kw):
            calls.append(("put", kw))

        def update_item(self, **kw):
            calls.append(("update", kw))

        def get_item(self, **kw):
            return {"Item": {"session_id": {"S": "c1"}, "turns": {"N": "3"}}}

    store = DynamoSessionStore("ug-sessions", Client(), ttl_days=1)
    assert store.put({"session_id": "c1", "turns": 3})
    item = calls[0][1]["Item"]
    assert item["session_id"] == {"S": "c1"} and item["turns"] == {"N": "3"} and "expires_at" in item
    assert store.update("c1", {"status": "completed", "turns": 4})
    kw = calls[1][1]
    assert kw["UpdateExpression"] == "SET #f0 = :v0, #f1 = :v1"
    assert kw["ExpressionAttributeNames"] == {"#f0": "status", "#f1": "turns"}
    assert store.get("c1") == {"session_id": "c1", "turns": 3}
