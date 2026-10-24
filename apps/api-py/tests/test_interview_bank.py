"""The interview question bank: admin-authored questions the free-style
interviewer weaves in.

Three claims, each pinned: the CRUD is gated by the `admin.interview_questions`
CAPABILITY (a director by baseline, a mentor only when granted); the bulk
parser reads every shape people paste and reports, line by line, what it could
not; and the WIRE is real - a Specialization handed to the engine carries the
enabled questions of its track, in order, in the exact "[phase] text" form
build_instructions renders, and nothing else about it changes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from conftest import requires_db

from app.db import SessionLocal
from app.interview_bank import parse_bulk, question_bank_for, with_question_bank
from app.interview_matrix import build_instructions, get_specialization
from app.models.interview_bank import InterviewBankQuestion
from app.models.user import Role

API = "/api/admin/interview-questions"
GOV = "/api/admin/governance"
TAG = "qbank-" + uuid.uuid4().hex[:6]


@pytest.fixture(autouse=True)
def _clean_bank():
    """Every question these tests create carries TAG in its text; remove them
    all afterwards, whatever the test did, so a failure leaves no strays that
    the wire test would then read."""
    yield
    with SessionLocal() as db:
        db.execute(delete(InterviewBankQuestion).where(InterviewBankQuestion.text.contains(TAG)))
        db.commit()


def _q(text: str) -> str:
    return f"{text} ({TAG})"


# ------------------------------------------------------------ the gate --


@requires_db
def test_the_bank_is_a_capability_the_main_admin_holds_and_a_mentor_must_be_granted(client, make_user):
    admin = make_user("qb-dir", Role.ADMIN)  # the Main Admin: the only account that may grant
    mentor = make_user("qb-men", Role.MENTOR)
    student = make_user("qb-stu")

    assert client.get(f"{API}/tracks", headers=admin.headers).status_code == 200
    assert client.get(f"{API}/tracks", headers=student.headers).status_code == 403
    r = client.get(f"{API}/tracks", headers=mentor.headers)
    assert r.status_code == 403 and "Interview questions" in r.text, "the 403 names what to ask for"

    r = client.post(
        f"{GOV}/grants",
        headers=admin.headers,
        json={
            "capability": "admin.interview_questions",
            "user_ids": [mentor.user_id],
            "reason": "This mentor writes the HR question set for the batch.",
        },
    )
    assert r.status_code == 201, r.text
    grant_ids = [g["id"] for g in r.json()]
    try:
        # Same cookie, no re-login: capabilities resolve live.
        assert client.get(f"{API}/tracks", headers=mentor.headers).status_code == 200
        r = client.post(
            API, headers=mentor.headers,
            json={"track": "hr", "phase": "probing", "text": _q("Tell me about a conflict you resolved")},
        )
        assert r.status_code == 201, r.text
    finally:
        for gid in grant_ids:
            client.post(f"{GOV}/grants/{gid}/revoke", headers=admin.headers,
                        json={"reason": "Test grant, removed at the end of the test."})


# ----------------------------------------------------------- the CRUD --


@requires_db
def test_tracks_are_the_matrix_and_questions_are_ordered_edited_and_removed(client, make_user):
    director = make_user("qb-dir2", Role.ADMIN)
    h = director.headers

    tracks = client.get(f"{API}/tracks", headers=h).json()
    assert [t["key"] for t in tracks] == ["hr", "dm", "ba", "fa"], "the four live tracks, from the matrix"
    assert tracks[0]["phases"] == ["opening", "probing", "deep_dive", "wrap_up"]

    a = client.post(API, headers=h, json={"track": "ba", "phase": "opening", "text": _q("Walk me through a dashboard you built")}).json()
    b = client.post(API, headers=h, json={"track": "ba", "phase": "deep_dive", "text": _q("Your model is 92% accurate and the business says it is wrong")}).json()
    assert (a["position"], b["position"]) == (1, 2) or b["position"] == a["position"] + 1

    r = client.post(API, headers=h, json={"track": "ba", "phase": "ended", "text": _q("not a real phase here")})
    assert r.status_code == 422 and "phase must be" in r.text
    r = client.post(API, headers=h, json={"track": "law", "phase": "probing", "text": _q("no such track anywhere")})
    assert r.status_code == 422 and "track must be" in r.text
    r = client.post(API, headers=h, json={"track": "ba", "phase": "probing", "text": "short"})
    assert r.status_code == 422, "eight characters is the floor"

    listed = [q for q in client.get(f"{API}?track=ba", headers=h).json() if TAG in q["text"]]
    assert [q["id"] for q in listed] == [a["id"], b["id"]]

    # Reorder: the full order, in one call.
    r = client.post(f"{API}/reorder", headers=h, json={"track": "ba", "ids": [b["id"], a["id"]] + [q["id"] for q in client.get(f"{API}?track=ba", headers=h).json() if q["id"] not in (a["id"], b["id"])]})
    assert r.status_code == 200, r.text
    listed = [q for q in client.get(f"{API}?track=ba", headers=h).json() if TAG in q["text"]]
    assert [q["id"] for q in listed] == [b["id"], a["id"]]
    r = client.post(f"{API}/reorder", headers=h, json={"track": "ba", "ids": [a["id"]]})
    assert r.status_code == 422, "a partial order is refused, never quietly completed"

    # Edit + disable.
    r = client.patch(f"{API}/{a['id']}", headers=h, json={"text": _q("Walk me through the LAST dashboard you built"), "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and "LAST" in r.json()["text"]
    assert client.patch(f"{API}/{a['id']}", headers=h, json={"phase": "nope"}).status_code == 422

    # Remove.
    assert client.delete(f"{API}/{b['id']}", headers=h).status_code == 204
    assert client.delete(f"{API}/{b['id']}", headers=h).status_code == 404
    assert client.patch(f"{API}/{uuid.uuid4().hex}", headers=h, json={"enabled": True}).status_code == 404


# ------------------------------------------------------------- bulk --


def test_the_bulk_parser_reads_every_shape_and_reports_what_it_cannot():
    rows, skipped = parse_bulk(
        "\n".join(
            [
                "# HR set - comments and blanks are ignored",
                "",
                "[opening] Walk me through your background.",
                "probing | Describe a conflict you resolved at work.",
                "deep_dive, Your team missed a deadline. What do you do first?",
                "Wrap up: Any questions for us?",
                "Why HR, and why now?",  # a comma in a sentence is NOT a phase
                '"Quoted question is unquoted, please?"',
                "[bogus] this bracketed phase does not exist",
                "short",
                "x" * 601,
            ]
        )
    )
    assert rows == [
        ("opening", "Walk me through your background."),
        ("probing", "Describe a conflict you resolved at work."),
        ("deep_dive", "Your team missed a deadline. What do you do first?"),
        ("wrap_up", "Any questions for us?"),
        ("probing", "Why HR, and why now?"),
        ("probing", "Quoted question is unquoted, please?"),
    ]
    assert len(skipped) == 3
    assert skipped[0].startswith("line 9:") and "bogus" in skipped[0]
    assert skipped[1].startswith("line 10:") and "short" in skipped[1]
    assert skipped[2].startswith("line 11:") and "600" in skipped[2]


@requires_db
def test_bulk_appends_in_order_and_reports_skips(client, make_user):
    director = make_user("qb-dir3", Role.ADMIN)
    h = director.headers
    first = client.post(API, headers=h, json={"track": "dm", "phase": "opening", "text": _q("What drew you to marketing")}).json()
    r = client.post(
        f"{API}/bulk", headers=h,
        json={"track": "dm", "lines": f"[probing] {_q('How would you grow a channel from zero')}\n[bogus] {_q('never stored')}\n{_q('Pick a campaign you admire and say why')}\n"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert [q["phase"] for q in body["added"]] == ["probing", "probing"]
    assert [q["position"] for q in body["added"]] == [first["position"] + 1, first["position"] + 2]
    assert len(body["skipped"]) == 1 and "bogus" in body["skipped"][0]
    listed = [q["text"] for q in client.get(f"{API}?track=dm", headers=h).json() if TAG in q["text"]]
    assert "never stored" not in " ".join(listed)


# ------------------------------------------------------------- the wire --


@requires_db
def test_the_engine_gets_the_enabled_questions_in_order_and_nothing_else_changes(client, make_user):
    director = make_user("qb-dir4", Role.ADMIN)
    h = director.headers
    q1 = client.post(API, headers=h, json={"track": "fa", "phase": "probing", "text": _q("Walk me through a DCF you have built")}).json()
    q2 = client.post(API, headers=h, json={"track": "fa", "phase": "deep_dive", "text": _q("Rates rise 200bps overnight; what breaks first")}).json()
    off = client.post(API, headers=h, json={"track": "fa", "phase": "wrap_up", "text": _q("DISABLED and must not be asked")}).json()
    client.patch(f"{API}/{off['id']}", headers=h, json={"enabled": False})

    bank = [line for line in question_bank_for("fa") if TAG in line]
    assert bank == [f"[probing] {q1['text']}", f"[deep_dive] {q2['text']}"], "enabled only, in order, in the prompt's shape"

    base = get_specialization("fa")
    spec = with_question_bank(base)
    assert spec is not base and spec.key == base.key and spec.persona == base.persona, (
        "a copy carrying the bank; the persona and key are untouched"
    )
    assert all(line in spec.question_bank for line in bank)
    prompt = build_instructions(spec, "You are a placement interviewer.")
    assert q1["text"] in prompt and q2["text"] in prompt
    assert "DISABLED and must not be asked" not in prompt
    assert "guide to coverage, not a script" in prompt, "the free-style framing is what makes this a mix"

    # A track with no bank is handed back unchanged - the engine cannot tell.
    empty = get_specialization("hr")
    with SessionLocal() as db:
        db.execute(delete(InterviewBankQuestion).where(InterviewBankQuestion.track == "hr", InterviewBankQuestion.text.contains(TAG)))
        db.commit()
    assert with_question_bank(empty) is empty or with_question_bank(empty).question_bank == empty.question_bank
