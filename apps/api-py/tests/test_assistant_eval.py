"""Golden-set regression gate for the tool-backed assistant (Phase D).

Runs every case in ``app.eval.golden.GOLDEN`` through the real orchestrator over
the seeded dev DB and asserts the CONTRACT — routed intent, grounding signal
(resolved / honest refusal), and cited source type — holds. Prints a pass-rate
line for visibility, but the asserts are the gate: a regressed case fails CI.

Determinism: ``llm_config`` is stubbed to None so the student-data builders skip
the optional polish and prove the deterministic tool result is the source of
truth; ``complete_chat`` is stubbed so the policy/general phrasing is fixed —
the test pins intent/grounding, never a provider's words.

Skipped when Postgres is unreachable.
"""

import pytest

from conftest import DB_UP, requires_db

from app.ai import orchestrator
from app.db import SessionLocal
from app.eval.golden import GOLDEN
from app.seed_kb import seed_knowledge


@pytest.fixture(scope="module", autouse=True)
def _ensure_kb():
    if not DB_UP:
        return
    with SessionLocal() as db:
        seed_knowledge(db)


def _student_id() -> str:
    from app.models.user import Student

    with SessionLocal() as db:
        s = db.query(Student).first()
        assert s is not None, "seeded DB has no students"
        return s.id


def _evaluate_case(db, sid, case) -> tuple[bool, str]:
    """Run one golden case; return (passed, reason-if-failed)."""
    res = orchestrator.answer_question(db, sid, "STUDENT", case["question"])

    if res.get("intent") != case["expect_intent"]:
        return False, f"intent {res.get('intent')!r} != {case['expect_intent']!r}"

    if case.get("expect_refusal"):
        if res.get("resolved") is not False:
            return False, f"expected honest refusal (resolved False), got {res.get('resolved')!r}"
    else:
        if res.get("resolved") != case["expect_resolved"]:
            return False, f"resolved {res.get('resolved')!r} != {case['expect_resolved']!r}"

    want_type = case.get("expect_source_type")
    if want_type is not None:
        types = {s.get("type") for s in res.get("sources", [])}
        if want_type not in types:
            return False, f"source type {want_type!r} not in {types!r}"

    return True, ""


def test_golden_set_is_reasonably_sized():
    # A meaningful regression gate covers the intents; guard against it shrinking.
    assert len(GOLDEN) >= 12
    intents = {c["expect_intent"] for c in GOLDEN}
    assert {"readiness", "gaps", "skills", "jobs", "deadlines", "profile", "policy", "general"} <= intents


@requires_db
def test_golden_set_regression_gate(monkeypatch, capsys):
    # Deterministic: no polish, fixed policy/general phrasing.
    monkeypatch.setattr(orchestrator, "llm_config", lambda: None)
    monkeypatch.setattr(
        orchestrator,
        "complete_chat",
        lambda messages, **kwargs: "Grounded over the approved sources provided.",
    )

    sid = _student_id()
    failures: list[str] = []
    passed = 0
    with SessionLocal() as db:
        for case in GOLDEN:
            ok, reason = _evaluate_case(db, sid, case)
            if ok:
                passed += 1
            else:
                failures.append(f"{case['id']}: {reason}")

    total = len(GOLDEN)
    rate = passed / total if total else 0.0
    # Printed for visibility (pytest -s / on failure). Not the gate itself.
    with capsys.disabled():
        print(f"\nGOLDEN pass rate: {passed}/{total} = {rate:.0%}")

    assert not failures, "golden-set regressions:\n" + "\n".join(failures)
    assert passed == total
