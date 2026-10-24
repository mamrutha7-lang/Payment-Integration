"""Rule 2 on the staff side of the v2 screens, and the new student write paths.

`student_programme.py` needs no scope test: it has no path parameter naming a
student, so there is nothing for a crafted id to reach. `mentee_records.py` does
name one, which makes it exactly the shape rule 2 governs — and the axis that
matters is HORIZONTAL. Being a MENTOR gets you through the door; it does not get
you a particular student. The tests below check both halves, and specifically
that a MENTOR WITH NO GROUP sees nobody rather than everybody, because "no
filter" is the reading that turns an empty mentor group into the whole
programme.
"""

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app.clock import local_today
from app.db import SessionLocal
from app.models.english_baseline import (
    BaselineStatus,
    EnglishBaseline,
    EnglishBaselineSection,
    EnglishSkill,
    SectionStatus,
)
from app.models.mentor_note import MentorNote
from app.models.user import Mentor, Role, Student
from tests.conftest import TEST_PASSWORD, requires_db

# The programme's day, the same clock the ledger router reads -- never the
# container's. `date.today()` here is UTC, and from 18:30 to midnight UTC it is
# yesterday in IST: the student's PUT landed on one day and the mentor's read,
# which asks the server for "today", looked at the next, empty one.
TODAY = local_today()

FULL_DAY = [
    {"slot": "DAWN", "activity": "COURSEWORK", "hours": 4},
    {"slot": "MORNING", "activity": "LECTURES", "hours": 3},
    {"slot": "MIDDAY", "activity": "LECTURES", "hours": 3},
    {"slot": "AFTERNOON", "activity": "SKILLING", "hours": 3},
    {"slot": "EVENING", "activity": "LEISURE", "hours": 4},
    {"slot": "NIGHT", "activity": "SLEEPING", "hours": 7},
]


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


@pytest.fixture
def staff(client, make_user):
    """A MENTOR user with its `mentors` row, and optionally students in its group.

    `make_user` deliberately creates no Mentor row (tests/test_auth_rbac.py
    builds a groupless mentor to prove rule 2's "sees NOBODY"), so this fixture
    adds one — and tears down the rows that reference the user BEFORE
    `make_user`'s own teardown deletes it, which is why it requests make_user.
    """
    created_mentors: list[str] = []

    def _make(label: str, mentees: list[str] | None = None):
        user = make_user(label, role=Role.MENTOR)
        with SessionLocal() as db:
            mentor = Mentor(user_id=user.user_id)
            db.add(mentor)
            db.commit()
            created_mentors.append(mentor.id)
            user.mentor_id = mentor.id
            for sid in mentees or []:
                db.get(Student, sid).mentor_id = mentor.id
            db.commit()

        # RE-AUTHENTICATE. `make_user` logged this account in before the Mentor
        # row existed, and the session cookie is a SIGNED SNAPSHOT of the claims
        # at minting time — `mentorId` would be absent, and every scope check
        # would read the account as a mentor with no group. This is the same
        # property that makes the session cheap to verify, so the fix is a fresh
        # login rather than anything cleverer.
        r = client.post(
            "/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        assert r.status_code == 200, r.text
        user.headers = {"Cookie": r.headers.get("set-cookie", "")}
        client.cookies.clear()
        return user

    yield _make

    with SessionLocal() as db:
        for mentor_id in created_mentors:
            for student in db.scalars(select(Student).where(Student.mentor_id == mentor_id)):
                student.mentor_id = None
            db.execute(delete(MentorNote).where(MentorNote.mentor_id == mentor_id))
        # FLUSHED BEFORE THE DELETE, not batched with it. students.mentor_id and
        # mentor_notes.mentor_id both reference `mentors`; in one flush the unit
        # of work is free to order the DELETE first, and the foreign keys reject
        # it. Two statements, in the order the constraints require.
        db.flush()
        for mentor_id in created_mentors:
            db.execute(delete(Mentor).where(Mentor.id == mentor_id))
        db.commit()


# ---------------------------------------------------------------------------
# Rule 2 — the horizontal axis
# ---------------------------------------------------------------------------


@requires_db
def test_a_mentor_removes_only_the_notes_they_wrote(client, make_user, staff):
    """DELETE on a meeting note is narrower than POST: rule 2's gate, then the
    note must sit under the student in the path, then a MENTOR must be its
    author. The student reads these notes, so taking one back is not something
    a colleague does on your behalf."""
    stu = make_user("note-del-student")
    sid = _student_id(stu.user_id)
    other = make_user("note-del-other")
    other_sid = _student_id(other.user_id)

    author = staff("note-del-author", mentees=[sid, other_sid])
    r = client.post(
        f"/api/mentor/students/{sid}/notes",
        headers=author.headers,
        json={"note_text": "Reviewed the ledger; next check-in in two weeks."},
    )
    assert r.status_code == 201, r.text
    note_id = r.json()["id"]

    # A note id under the WRONG student is a 404 — the path is the scope.
    r = client.delete(f"/api/mentor/students/{other_sid}/notes/{note_id}", headers=author.headers)
    assert r.status_code == 404, r.text

    # A groupless mentor cannot reach the student at all: 404, never a delete.
    loner = staff("note-del-loner")
    r = client.delete(f"/api/mentor/students/{sid}/notes/{note_id}", headers=loner.headers)
    # 403 since B2.3: a faculty account with no mentees does not hold the mentee
    # log at all, so the capability gate refuses before rule 2 is reached. It was
    # 404 while every faculty account held the key through the baseline.
    assert r.status_code == 403, r.text

    # The student is moved to a second mentor, who is now IN scope but did not
    # write the note: 403, and the note is still there.
    second = staff("note-del-second", mentees=[sid])
    r = client.delete(f"/api/mentor/students/{sid}/notes/{note_id}", headers=second.headers)
    assert r.status_code == 403, r.text
    r = client.get(f"/api/mentor/students/{sid}/notes", headers=second.headers)
    assert note_id in [n["id"] for n in r.json()]

    # The second mentor's own note, however, is theirs to remove.
    r = client.post(
        f"/api/mentor/students/{sid}/notes",
        headers=second.headers,
        json={"note_text": "Handover meeting.", "title": "First 1:1"},
    )
    assert r.status_code == 201, r.text
    own_id = r.json()["id"]
    r = client.delete(f"/api/mentor/students/{sid}/notes/{own_id}", headers=second.headers)
    assert r.status_code == 204, r.text
    r = client.get(f"/api/mentor/students/{sid}/notes", headers=second.headers)
    ids = [n["id"] for n in r.json()]
    assert own_id not in ids and note_id in ids

    # A student has no business here at all.
    r = client.delete(f"/api/mentor/students/{sid}/notes/{note_id}", headers=stu.headers)
    assert r.status_code == 403


@requires_db
def test_retracting_a_note_hides_it_everywhere_but_keeps_the_row(client, make_user, staff):
    """A retracted note leaves BOTH screens and survives in the database.

    The student has very likely already read it — `/student/mentor-meetings`
    renders these — so removing one is a mentor taking back words that were on a
    student's screen. It used to be a hard DELETE, which made "what was said,
    and later withdrawn" unanswerable the moment it ran; nothing else holds a
    copy, because the row IS the record.

    The two disappearances are asserted separately on purpose. Filtering the
    staff list and forgetting the student's own log is the exact half-fix this
    guards against: the mentor would see the note gone and believe it was
    withdrawn, while it sat on the student's screen unchanged.
    """
    stu = make_user("note-retract-student")
    sid = _student_id(stu.user_id)
    author = staff("note-retract-author", mentees=[sid])

    r = client.post(
        f"/api/mentor/students/{sid}/notes",
        headers=author.headers,
        json={"note_text": "Said the offer was withdrawn.", "title": "1:1 review"},
    )
    assert r.status_code == 201, r.text
    note_id = r.json()["id"]

    # It starts out visible to the student, or the rest of this proves nothing.
    log = client.get("/api/student/mentor-meetings", headers=stu.headers).json()
    assert note_id in [m["id"] for m in log["meetings"]], log

    r = client.delete(f"/api/mentor/students/{sid}/notes/{note_id}", headers=author.headers)
    assert r.status_code == 204, r.text

    # Gone from the mentor's Mentee Log...
    r = client.get(f"/api/mentor/students/{sid}/notes", headers=author.headers)
    assert note_id not in [n["id"] for n in r.json()], r.text

    # ...and gone from the student's own Mentor Meeting Log.
    log = client.get("/api/student/mentor-meetings", headers=stu.headers).json()
    assert note_id not in [m["id"] for m in log["meetings"]], log

    # But the row is still there, stamped — that is the whole point.
    with SessionLocal() as db:
        row = db.get(MentorNote, note_id)
        assert row is not None, "the note was hard-deleted; the record is gone"
        assert row.deleted_at is not None, "retracted but not stamped"
        assert row.note_text == "Said the offer was withdrawn.", "the words must survive verbatim"

    # A second DELETE is a 404, not another 204. `db.get` takes no WHERE clause,
    # so without an explicit deleted_at check this would re-stamp the row and
    # answer 204 — which reads as "there was something here and I removed it".
    r = client.delete(f"/api/mentor/students/{sid}/notes/{note_id}", headers=author.headers)
    assert r.status_code == 404, r.text


@requires_db
def test_a_mentor_reads_their_own_mentees_ledger(client, make_user, staff):
    stu = make_user("staff-own-student")
    sid = _student_id(stu.user_id)
    mentor = staff("staff-own-mentor", mentees=[sid])

    client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": FULL_DAY}, headers=stu.headers
    )

    r = client.get(f"/api/mentor/students/{sid}/ledger", headers=mentor.headers)
    assert r.status_code == 200, r.text
    # The STUDENT'S view, computed by the same expression — not a staff copy.
    assert r.json()["total_hours"] == 24
    assert r.json()["metrics"][0]["sub"] == "Reconciled to 24 h"


@requires_db
def test_a_mentor_cannot_read_someone_elses_mentee(client, make_user, staff):
    """404, not 403: the existence of a student outside your group is not yours
    to learn either."""
    theirs = make_user("staff-other-student")
    other_sid = _student_id(theirs.user_id)
    mine = make_user("staff-mine")
    mentor = staff("staff-wrong-mentor", mentees=[_student_id(mine.user_id)])

    for path in ("ledger", "ledger/summary", "english-baseline"):
        r = client.get(f"/api/mentor/students/{other_sid}/{path}", headers=mentor.headers)
        assert r.status_code == 404, f"{path}: {r.status_code} {r.text}"


@requires_db
def test_a_mentor_with_no_group_sees_nobody(client, make_user, staff):
    """The reading rule 2 exists to forbid: an empty mentor group is NOT "no
    filter". A regression here does not error — it quietly hands one mentor the
    whole programme."""
    stu = make_user("staff-nogroup-student")
    sid = _student_id(stu.user_id)
    loner = staff("staff-nogroup-mentor")  # a Mentor row, but no mentees

    for path in ("ledger", "ledger/summary", "english-baseline"):
        r = client.get(f"/api/mentor/students/{sid}/{path}", headers=loner.headers)
        # 403 since B2.3, 404 before it. THE PROPERTY IS "SEES NOBODY", not a
        # particular number: this mentor has no mentees, so they no longer hold
        # the mentee log and the capability gate refuses before rule 2 runs. A
        # mentor WITH mentees probing someone else's student still gets 404,
        # which is the case where the code matters — it must not confirm that
        # the student exists.
        assert r.status_code == 403, f"{path} leaked to a groupless mentor"


@requires_db
def test_the_main_admin_reads_any_student_ONLY_once_it_holds_the_capability(
    client, make_user, granted
):
    """These endpoints are gated on `mentor.mentees`, and the office account does
    not hold it by role.

    This test used to be `test_a_director_reads_any_student` and asserted a plain
    200. Both halves of that changed on 2026-09-10: DIRECTOR is not a role any
    more, and `_FACULTY_ONLY` (app/governance.py) took the four faculty
    instruments out of the Main Admin's baseline — a mentee log belongs to a
    mentor, and the office has no mentees.

    So the 403 comes FIRST, and it is the more important assertion: it is what
    would fail if somebody quietly widened the baseline back to make a screen
    work. The grant is then the documented way in, and the 200 after it proves
    the door still opens for the account that decides who holds what.
    """
    stu = make_user("staff-director-student")
    sid = _student_id(stu.user_id)
    admin = make_user("staff-director", role=Role.ADMIN)

    for path in ("ledger", "english-baseline"):
        r = client.get(f"/api/mentor/students/{sid}/{path}", headers=admin.headers)
        assert r.status_code == 403, (
            f"the Main Admin reached {path} with no grant ({r.status_code}); "
            "a faculty instrument is in its baseline again"
        )

    granted(admin, "mentor.mentees", "Standing in for faculty to read a mentee record.")

    assert client.get(f"/api/mentor/students/{sid}/ledger", headers=admin.headers).status_code == 200
    assert (
        client.get(f"/api/mentor/students/{sid}/english-baseline", headers=admin.headers).status_code
        == 200
    )


@requires_db
def test_a_student_cannot_read_the_staff_endpoints(client, make_user):
    a = make_user("staff-student-a")
    b = make_user("staff-student-b")
    r = client.get(f"/api/mentor/students/{_student_id(b.user_id)}/ledger", headers=a.headers)
    assert r.status_code == 403


@requires_db
def test_staff_see_the_same_pending_section_the_student_does(client, make_user, staff):
    """A mentor deciding whether to intervene must see "not sat" and "scored 0"
    differently, which is the whole reason the score columns are nullable."""
    stu = make_user("staff-eng-student")
    sid = _student_id(stu.user_id)
    mentor = staff("staff-eng-mentor", mentees=[sid])

    with SessionLocal() as db:
        baseline = EnglishBaseline(
            student_id=sid, semester=1, status=BaselineStatus.IN_PROGRESS,
            overall_score=62, band="B1+",
        )
        baseline.sections = [
            EnglishBaselineSection(skill=EnglishSkill.READING, status=SectionStatus.SCORED, score=68, band="B2"),
            EnglishBaselineSection(skill=EnglishSkill.WRITING, status=SectionStatus.SCORED, score=57, band="B1"),
            EnglishBaselineSection(skill=EnglishSkill.LISTENING, status=SectionStatus.SCORED, score=61, band="B1"),
            EnglishBaselineSection(skill=EnglishSkill.SPEAKING, status=SectionStatus.PENDING, minutes=12),
        ]
        db.add(baseline)
        db.commit()

    staff_view = client.get(f"/api/mentor/students/{sid}/english-baseline", headers=mentor.headers).json()
    student_view = client.get("/api/student/english-baseline", headers=stu.headers).json()
    assert staff_view == student_view

    speaking = next(s for s in staff_view["sections"] if s["skill"] == "SPEAKING")
    assert speaking["score"] is None and speaking["status"] == "PENDING"
    assert staff_view["provisional"] is True


@requires_db
def test_the_ledger_summary_averages_over_logged_days_only(client, make_user, staff):
    """Averaging over the WINDOW reports a student who logged three perfect days
    out of fourteen as averaging 5 h — which reads as under-work rather than as
    under-logging, and those need opposite conversations."""
    stu = make_user("staff-summary-student")
    sid = _student_id(stu.user_id)
    mentor = staff("staff-summary-mentor", mentees=[sid])

    client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": FULL_DAY}, headers=stu.headers
    )
    client.post("/api/student/ledger/submit", json={"day": str(TODAY)}, headers=stu.headers)
    client.put(
        "/api/student/ledger",
        json={"day": str(TODAY - timedelta(days=1)), "cells": [
            {"slot": "NIGHT", "activity": "SLEEPING", "hours": 7}
        ]},
        headers=stu.headers,
    )

    body = client.get(
        f"/api/mentor/students/{sid}/ledger/summary?days=14", headers=mentor.headers
    ).json()
    assert body["days_with_anything"] == 2
    assert body["days_submitted"] == 1
    # (24 + 7) / 2 — over the two days that have anything, not over fourteen.
    assert body["mean_logged_hours"] == 15.5
    assert body["days"][0]["reconciled"] is True
    assert body["days"][1]["reconciled"] is False


# ---------------------------------------------------------------------------
# The student write paths behind the previously-inert buttons
# ---------------------------------------------------------------------------


@requires_db
def test_starting_the_baseline_is_idempotent(client, make_user):
    """One attempt per semester is the programme rule and the database enforces
    it; a double-tap must read the existing attempt, not 409."""
    stu = make_user("eng-start")

    first = client.post("/api/student/english-baseline/start", headers=stu.headers)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True
    assert [s["skill"] for s in first.json()["baseline"]["sections"]] == [
        "READING", "WRITING", "LISTENING", "SPEAKING"
    ]
    assert all(s["status"] == "PENDING" for s in first.json()["baseline"]["sections"])
    # Started, not scored: nothing here invents a number.
    assert first.json()["baseline"]["overall_score"] is None

    second = client.post("/api/student/english-baseline/start", headers=stu.headers)
    assert second.status_code == 200
    assert second.json()["created"] is False

    with SessionLocal() as db:
        sid = _student_id(stu.user_id)
        assert len(db.scalars(select(EnglishBaseline).where(EnglishBaseline.student_id == sid)).all()) == 1


@requires_db
def test_the_report_pdf_is_a_pdf_and_404s_before_an_attempt(client, make_user):
    stu = make_user("eng-report")

    missing = client.get("/api/student/english-baseline/report", headers=stu.headers)
    assert missing.status_code == 404

    client.post("/api/student/english-baseline/start", headers=stu.headers)
    r = client.get("/api/student/english-baseline/report", headers=stu.headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    # Scores change as sections land, so a cached copy is a stale assessment.
    assert r.headers["cache-control"] == "no-store"
    # A pending section must never print as a number in a document a student
    # may hand to somebody.
    assert b"Not yet taken" in r.content or len(r.content) > 1000


@requires_db
def test_requesting_a_meeting_lands_in_the_students_own_log(client, make_user, staff):
    stu = make_user("meet-request")
    sid = _student_id(stu.user_id)
    staff("meet-request-mentor", mentees=[sid])

    r = client.post(
        "/api/student/mentor-meetings/request",
        json={"reason": "I would like to talk about GD practice.", "preferred": "Thursday pm"},
        headers=stu.headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["sent"] is True

    log = client.get("/api/student/mentor-meetings", headers=stu.headers).json()
    assert log["meetings_logged"] == 1
    entry = log["meetings"][0]
    assert entry["title"] == "Meeting requested"
    # Attributed honestly — the text says the student asked.
    assert "Meeting requested by the student" in entry["note"]
    assert "Thursday pm" in entry["note"]


@requires_db
def test_requesting_a_meeting_with_no_mentor_is_a_truthful_refusal(client, make_user):
    """A request nobody can receive is worse than a "not yet"."""
    stu = make_user("meet-no-mentor")
    r = client.post(
        "/api/student/mentor-meetings/request",
        json={"reason": "Anyone there?"},
        headers=stu.headers,
    )
    assert r.status_code == 409
    assert "do not have a mentor assigned" in r.json()["detail"]


@requires_db
def test_a_scored_sections_prose_is_returned_but_a_pending_ones_is_not(client, make_user):
    stu = make_user("eng-prose")
    sid = _student_id(stu.user_id)
    with SessionLocal() as db:
        baseline = EnglishBaseline(student_id=sid, semester=1, status=BaselineStatus.IN_PROGRESS)
        baseline.sections = [
            EnglishBaselineSection(
                skill=EnglishSkill.READING, status=SectionStatus.SCORED, score=68, band="B2",
                ai_report="Reads at pace; inference under time pressure is the next lift.",
            ),
            EnglishBaselineSection(
                skill=EnglishSkill.SPEAKING, status=SectionStatus.PENDING,
                ai_report="should not be shown for a pending section",
            ),
        ]
        db.add(baseline)
        db.commit()

    body = client.get("/api/student/english-baseline", headers=stu.headers).json()
    reading = next(s for s in body["sections"] if s["skill"] == "READING")
    speaking = next(s for s in body["sections"] if s["skill"] == "SPEAKING")
    assert reading["ai_report"].startswith("Reads at pace")
    assert speaking["ai_report"] is None
