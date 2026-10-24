"""Golden-set for the tool-backed assistant (Assistant V2 Phase D).

A small, hand-written regression suite that pins the CONTRACT of
``orchestrator.answer_question`` — not a provider's wording. Each case fixes:

* ``expect_intent``     — the intent the rule router must land on.
* ``expect_resolved``   — the grounding signal for grounded cases (True), OR
* ``expect_refusal``    — True for a case that must honestly NOT be grounded
                          (out-of-scope / unknown), i.e. ``resolved is False``.
* ``expect_source_type``— (optional) the source type the answer must cite:
                          ``student-record`` for a personal tool, ``policy`` for
                          an approved knowledge chunk.

The set covers every student-data intent (readiness, gaps, skills, eligible jobs,
deadlines, profile completion), the policy / FAQ path, and honest refusal on
questions the assistant has no grounded answer for. ``tests/test_assistant_eval.py``
runs each case against the real orchestrator and asserts these fields hold, then
prints the pass rate — a real regression gate, not a smoke test.
"""

from __future__ import annotations

# Grounding source types (mirrors orchestrator source["type"] values).
STUDENT_RECORD = "student-record"
POLICY = "policy"

GOLDEN: list[dict] = [
    # --- Placement readiness -------------------------------------------------
    {
        "id": "readiness-are-we-ready",
        "question": "Am I placement-ready?",
        "expect_intent": "readiness",
        "expect_resolved": True,
        "expect_source_type": STUDENT_RECORD,
    },
    {
        "id": "readiness-score",
        "question": "What's my placement readiness score?",
        "expect_intent": "readiness",
        "expect_resolved": True,
        "expect_source_type": STUDENT_RECORD,
    },
    # --- Completion gaps / next steps ---------------------------------------
    {
        "id": "gaps-this-week",
        "question": "What should I complete this week?",
        "expect_intent": "gaps",
        "expect_resolved": True,
        "expect_source_type": STUDENT_RECORD,
    },
    # --- Skill status --------------------------------------------------------
    {
        "id": "skills-status",
        "question": "What are my skills and which are verified?",
        "expect_intent": "skills",
        "expect_resolved": True,
        "expect_source_type": STUDENT_RECORD,
    },
    # --- Eligible jobs -------------------------------------------------------
    {
        "id": "jobs-eligible",
        "question": "Which jobs am I eligible for?",
        "expect_intent": "jobs",
        "expect_resolved": True,
        "expect_source_type": STUDENT_RECORD,
    },
    # --- Deadlines -----------------------------------------------------------
    # Was "When is my next certification due?" until 2026-09-17, when the
    # Certification Tracker went and the deadlines answer became the courses'
    # next tasks.
    {
        "id": "deadlines-next-course-task",
        "question": "What is due next on my courses?",
        "expect_intent": "deadlines",
        "expect_resolved": True,
        "expect_source_type": STUDENT_RECORD,
    },
    # --- Profile completion --------------------------------------------------
    {
        "id": "profile-complete",
        "question": "Is my profile complete?",
        "expect_intent": "profile",
        "expect_resolved": True,
        "expect_source_type": STUDENT_RECORD,
    },
    # --- Policy / FAQ (approved knowledge, no student PII) -------------------
    {
        "id": "policy-verify-skill",
        "question": "How do I verify a Power BI skill?",
        "expect_intent": "policy",
        "expect_resolved": True,
        "expect_source_type": POLICY,
    },
    {
        "id": "policy-placement-docs",
        "question": "What documents do I need before placements?",
        "expect_intent": "policy",
        "expect_resolved": True,
        "expect_source_type": POLICY,
    },
    {
        "id": "policy-leaderboards",
        "question": "How are leaderboards calculated?",
        "expect_intent": "policy",
        "expect_resolved": True,
        "expect_source_type": POLICY,
    },
    # --- Out-of-scope / unknown -> honest refusal (not grounded) ------------
    {
        "id": "refusal-joke",
        "question": "Tell me a joke about penguins.",
        "expect_intent": "general",
        "expect_refusal": True,
    },
    {
        "id": "refusal-weather",
        "question": "What's the weather in Tokyo tomorrow?",
        "expect_intent": "general",
        "expect_refusal": True,
    },
]
