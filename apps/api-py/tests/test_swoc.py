"""SWOC - the board on a student's landing, and who may write it.

Three claims, each pinned: the editor is gated by the `admin.swoc` CAPABILITY
(the office by baseline, a faculty member only when granted, and revoking it
takes it back the same second); the VIEWPOINT is derived from the writer's
role, never typed, and the student's own board carries it; and every write is
bounded, attributed, and on the audit trail with what it replaced.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.redesign import AuditEvent
from app.models.swoc import SwocEntry
from app.models.user import Role, Student, User

API = "/api/admin/swoc"
MINE = "/api/student/swoc"
GOV = "/api/admin/governance"


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


def _name(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(User.name).where(User.id == user_id))


@pytest.fixture
def swept(make_user):
    """Entries CASCADE with their student, whom make_user removes; the audit
    rows have no FK and are swept here. Requesting make_user orders this
    teardown BEFORE the students go, so the entry ids are still readable."""
    student_ids: list[str] = []
    yield student_ids
    with SessionLocal() as db:
        for sid in student_ids:
            entry_ids = db.scalars(select(SwocEntry.id).where(SwocEntry.student_id == sid)).all()
            if entry_ids:
                db.execute(delete(AuditEvent).where(AuditEvent.entity_type == "swoc_entry", AuditEvent.entity_id.in_(entry_ids)))
            db.execute(delete(SwocEntry).where(SwocEntry.student_id == sid))
        db.commit()


# ------------------------------------------------------------ the gate --


@requires_db
def test_the_editor_is_a_capability_the_office_holds_and_faculty_are_granted(client, make_user, swept):
    admin = make_user("sw-dir", Role.ADMIN)  # the Main Admin: the only account that may grant
    mentor = make_user("sw-men", Role.MENTOR)
    student = make_user("sw-stu")
    sid = _student_id(student.user_id)
    swept.append(sid)
    entry = {"kind": "strength", "text": "Quick learner, asks good questions."}

    assert client.get(API, headers=admin.headers).status_code == 200
    assert client.get(API, headers=student.headers).status_code == 403
    r = client.get(API, headers=mentor.headers)
    assert r.status_code == 403 and "SWOC notes" in r.text, "the 403 names what to ask for"
    assert client.post(f"{API}/{sid}", headers=mentor.headers, json=entry).status_code == 403

    r = client.post(
        f"{GOV}/grants",
        headers=admin.headers,
        json={
            "capability": "admin.swoc",
            "user_ids": [mentor.user_id],
            "reason": "This faculty member writes SWOC lines for the batch.",
        },
    )
    assert r.status_code == 201, r.text
    rows = r.json()
    grant_ids = [g["id"] for g in rows]
    # SWOC CARRIES PII, AND THE MAIN ADMIN HANDS IT OVER ON ITS OWN SIGNATURE
    # (2026-09-16). A SWOC line names a student and characterises them, so
    # `admin.swoc` is flagged `carries_pii`: a DEPUTY's grant of it is written
    # `pending_approval` and waits for the office (B2.4), but the office is the
    # one authority here and its own grant is live at once. Asserted rather
    # than assumed, because the previous rule made this same grant hold nothing
    # until the office appointed a deputy to agree with it.
    assert rows[0]["approval_state"] == "active", rows[0]
    try:
        # Same cookie, no re-login: capabilities resolve live.
        assert client.get(API, headers=mentor.headers).status_code == 200
        r = client.post(f"{API}/{sid}", headers=mentor.headers, json=entry)
        assert r.status_code == 201, r.text
        # B7.1 CHANGED THIS LINE AND THE CHANGE IS THE POINT. It used to assert
        # MENTOR, because `_source_for` stamped on ROLE alone. This faculty
        # member does not mentor this student — they hold a GRANT on the board —
        # and the viewpoint is now derived from the relationship, so they write
        # as PLACEMENT. The board is deliberately un-averaged and disagreement
        # between viewpoints IS the finding; a granted lecturer filing every
        # line as MENTOR collapsed two viewpoints into one label, about students
        # they have never met. The `mentors this student` half is asserted in
        # tests/test_mentor_assignments.py, where a real pairing exists.
        assert r.json()["source"] == "PLACEMENT", (
            "a granted faculty member who does not mentor this student speaks "
            "for the placement cell, not as their mentor"
        )
        assert r.json()["author"] == _name(mentor.user_id)
    finally:
        for gid in grant_ids:
            client.post(f"{GOV}/grants/{gid}/revoke", headers=admin.headers,
                        json={"reason": "Test grant, removed at the end of the test."})
    assert client.get(API, headers=mentor.headers).status_code == 403, "revoked means gone, same second"


# ------------------------------------------------- writing and reading --


@requires_db
def test_the_office_writes_the_student_reads_their_own_and_the_trail_keeps_what_changed(client, make_user, swept):
    director = make_user("sw-dir2", Role.ADMIN)
    student = make_user("sw-stu2")
    other = make_user("sw-stu3")
    sid = _student_id(student.user_id)
    swept.append(sid)
    swept.append(_student_id(other.user_id))
    h = director.headers

    empty = {"strengths": [], "weaknesses": [], "opportunities": [], "challenges": []}
    assert client.get(MINE, headers=student.headers).json() == empty
    listed = next(r for r in client.get(API, headers=h).json() if r["student_id"] == sid)
    assert listed["entries"] == [] and listed["name"].startswith("Voice Test"), "the list carries everyone, written or not"

    r = client.post(f"{API}/{sid}", headers=h, json={"kind": "STRENGTH", "text": "  Strong analytical   and quantitative skills.  ", "weight": 5})
    assert r.status_code == 201, r.text
    strength = r.json()
    assert strength["text"] == "Strong analytical and quantitative skills.", "whitespace collapsed"
    assert strength["source"] == "PLACEMENT" and strength["author"] == _name(director.user_id)
    r = client.post(f"{API}/{sid}", headers=h, json={"kind": "weakness", "text": "Needs structured problem-solving practice."})
    assert r.status_code == 201 and r.json()["weight"] == 3, "weight defaults to the middle"
    weakness = r.json()
    r = client.post(f"{API}/{sid}", headers=h, json={"kind": "strength", "text": "Reliable under deadlines.", "weight": 2})
    assert r.status_code == 201
    lesser = r.json()

    mine = client.get(MINE, headers=student.headers).json()
    assert [s["text"] for s in mine["strengths"]] == ["Strong analytical and quantitative skills.", "Reliable under deadlines."], "heaviest first"
    assert mine["strengths"][0]["source"] == "PLACEMENT"
    assert mine["weaknesses"][0]["text"] == "Needs structured problem-solving practice."
    assert client.get(MINE, headers=other.headers).json() == empty, "first-person: another student sees their own"
    assert client.post(f"{API}/{sid}", headers=student.headers, json={"kind": "strength", "text": "I am great"}).status_code == 403

    # Edit: text and weight, separately; the list re-sorts.
    r = client.patch(f"{API}/entries/{lesser['id']}", headers=h, json={"weight": 5, "text": "Reliable under real deadlines."})
    assert r.status_code == 200 and r.json()["weight"] == 5 and r.json()["text"] == "Reliable under real deadlines."
    listed = next(r for r in client.get(API, headers=h).json() if r["student_id"] == sid)
    assert [e["id"] for e in listed["entries"] if e["kind"] == "STRENGTH"][0] in {strength["id"], lesser["id"]}
    assert len(listed["entries"]) == 3

    # Remove, and the board no longer shows it.
    assert client.delete(f"{API}/entries/{weakness['id']}", headers=h).status_code == 204
    assert client.get(MINE, headers=student.headers).json()["weaknesses"] == []
    assert client.delete(f"{API}/entries/{weakness['id']}", headers=h).status_code == 404

    with SessionLocal() as db:
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == "swoc_entry",
                                     AuditEvent.entity_id.in_([strength["id"], weakness["id"], lesser["id"]]))
        ).all()
    actions = sorted(e.action for e in events)
    assert actions == ["CREATE", "CREATE", "CREATE", "DELETE", "UPDATE"]
    update = next(e for e in events if e.action == "UPDATE")
    assert update.before_json["weight"] == 2 and update.after_json["weight"] == 5
    assert update.before_json["text"] == "Reliable under deadlines."
    deletion = next(e for e in events if e.action == "DELETE")
    assert deletion.before_json["text"] == "Needs structured problem-solving practice." and deletion.after_json is None
    assert {e.actor_user_id for e in events} == {director.user_id}


@requires_db
def test_bounds_and_unknowns(client, make_user, swept):
    director = make_user("sw-dir3", Role.ADMIN)
    student = make_user("sw-stu4")
    sid = _student_id(student.user_id)
    swept.append(sid)
    h = director.headers

    assert client.post(f"{API}/{sid}", headers=h, json={"kind": "threat", "text": "Not one of the four"}).status_code == 422
    assert client.post(f"{API}/{sid}", headers=h, json={"kind": "strength", "text": "   "}).status_code == 422
    assert client.post(f"{API}/{sid}", headers=h, json={"kind": "strength", "text": "x" * 401}).status_code == 422
    assert client.post(f"{API}/{sid}", headers=h, json={"kind": "strength", "text": "Fine", "weight": 0}).status_code == 422
    assert client.post(f"{API}/{sid}", headers=h, json={"kind": "strength", "text": "Fine", "weight": 6}).status_code == 422
    r = client.post(f"{API}/no-such-student", headers=h, json={"kind": "strength", "text": "Anything at all"})
    assert r.status_code == 404 and "No such student" in r.text
    assert client.patch(f"{API}/entries/no-such-entry", headers=h, json={"weight": 4}).status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(SwocEntry).where(SwocEntry.student_id == sid)) is None, "a refused write writes nothing"
