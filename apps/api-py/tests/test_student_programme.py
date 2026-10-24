"""The English Baseline, Mentor Meeting Log and programme-card endpoints.

Three things are worth pinning here and the rest is plumbing:

1. A PENDING section reports `score: null`, never 0. The screen prints the score
   in a 27px numeral; a pending speaking section rendering as a confident "0" is
   a student being told they failed something they have not taken.
2. `provisional` is DERIVED from how many sections are scored, so the word
   "Provisional band" cannot outlive the section that resolves it.
3. The `english_baseline` milestone on the landing card is derived from the
   attempt, not from a stored milestone row — two sources of truth for one row
   is how that row ends up saying "not started" under a finished report.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.models.english_baseline import (
    BaselineStatus,
    EnglishBaseline,
    EnglishBaselineSection,
    EnglishSkill,
    SectionStatus,
)
from app.models.mentor_note import MentorAction, MentorNote
from app.models.redesign import (
    MentorNotebookEntry,
    NotebookEntryType,
    NotebookVisibility,
    RecordStatus,
)
from app.models.milestone import MilestoneStatus, StudentMilestone
from app.models.user import Role, Student
from tests.conftest import requires_db


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        from sqlalchemy import select

        return db.scalar(select(Student.id).where(Student.user_id == user_id))


@pytest.fixture
def mentor_row(make_user):
    """A MENTOR user that also has its `mentors` row, plus teardown for it.

    `make_user` deliberately does NOT create one: tests/test_auth_rbac.py builds
    a mentor with no group to prove rule 2's "sees NOBODY", and giving every
    mentor a row from the shared fixture would quietly change that test's
    premise. `mentor_notes.mentor_id` is NOT NULL, so these tests do need a row,
    and it is created here instead.

    The fixture REQUESTS `make_user`, so it is set up after it and therefore torn
    down BEFORE it — which is the only ordering in which these rows can be
    deleted while the `users` row they reference still exists.
    """
    from app.models.user import Mentor

    created: list[str] = []

    def _make(label: str):
        user = make_user(label, role=Role.MENTOR)
        with SessionLocal() as db:
            mentor = Mentor(user_id=user.user_id)
            db.add(mentor)
            db.commit()
            created.append(mentor.id)
            user.mentor_id = mentor.id
        return user

    yield _make

    with SessionLocal() as db:
        for mentor_id in created:
            db.execute(delete(MentorNote).where(MentorNote.mentor_id == mentor_id))
            db.execute(delete(Mentor).where(Mentor.id == mentor_id))
        db.commit()


# ---------------------------------------------------------------------------
# English baseline
# ---------------------------------------------------------------------------


@requires_db
def test_no_attempt_still_returns_the_four_skills(client, make_user):
    """`exists: false` rather than a 404 — the screen's job with nothing taken
    is to show four skills and a start affordance, and a 404 would make the
    client invent that layout from nothing."""
    stu = make_user("eng-none")
    body = client.get("/api/student/english-baseline", headers=stu.headers).json()
    assert body["exists"] is False
    assert [s["skill"] for s in body["sections"]] == [
        "READING",
        "WRITING",
        "LISTENING",
        "SPEAKING",
    ]
    assert all(s["status"] == "PENDING" and s["score"] is None for s in body["sections"])
    assert body["sections_scored"] == 0
    assert body["progress_percent"] == 0


@requires_db
def test_a_pending_section_scores_null_not_zero(client, make_user):
    stu = make_user("eng-pending")
    sid = _student_id(stu.user_id)
    with SessionLocal() as db:
        baseline = EnglishBaseline(
            student_id=sid,
            semester=1,
            status=BaselineStatus.IN_PROGRESS,
            overall_score=62,
            band="B1+",
            taken_on=date.today(),
            strengths=["Reads academic text at pace"],
            focus_areas=["Tense shifts under time pressure"],
            next_steps=[{"title": "Business Writing Clinic", "sub": "4 sessions", "target": "/student/skilling"}],
        )
        baseline.sections = [
            EnglishBaselineSection(
                skill=EnglishSkill.READING, status=SectionStatus.SCORED, score=68, band="B2",
                minutes=18, subscores=[{"label": "Inference", "value": 66}],
            ),
            EnglishBaselineSection(
                skill=EnglishSkill.WRITING, status=SectionStatus.SCORED, score=57, band="B1", minutes=25
            ),
            EnglishBaselineSection(
                skill=EnglishSkill.LISTENING, status=SectionStatus.SCORED, score=61, band="B1", minutes=15
            ),
            EnglishBaselineSection(
                skill=EnglishSkill.SPEAKING, status=SectionStatus.PENDING, minutes=12
            ),
        ]
        db.add(baseline)
        db.commit()

    body = client.get("/api/student/english-baseline", headers=stu.headers).json()
    assert body["exists"] is True
    assert body["overall_score"] == 62
    assert body["band"] == "B1+"
    assert body["band_label"] == "Independent user"
    assert body["sections_scored"] == 3
    assert body["progress_percent"] == 75
    # THE POINT OF THIS TEST.
    speaking = next(s for s in body["sections"] if s["skill"] == "SPEAKING")
    assert speaking["score"] is None
    assert speaking["band"] is None
    assert speaking["status"] == "PENDING"
    assert body["pending_label"] == "Speaking pending · 12 min"
    # Derived, not stored.
    assert body["provisional"] is True
    assert body["strengths"] == ["Reads academic text at pace"]
    assert body["next_steps"][0]["target"] == "/student/skilling"


@requires_db
def test_provisional_clears_when_the_last_section_lands(client, make_user):
    stu = make_user("eng-complete")
    sid = _student_id(stu.user_id)
    with SessionLocal() as db:
        baseline = EnglishBaseline(
            student_id=sid, semester=1, status=BaselineStatus.COMPLETE, overall_score=71, band="B2"
        )
        baseline.sections = [
            EnglishBaselineSection(skill=skill, status=SectionStatus.SCORED, score=70, band="B2")
            for skill in EnglishSkill
        ]
        db.add(baseline)
        db.commit()

    body = client.get("/api/student/english-baseline", headers=stu.headers).json()
    assert body["sections_scored"] == 4
    assert body["provisional"] is False
    assert body["pending_label"] is None


@requires_db
def test_english_baseline_is_student_only(client, make_user):
    mentor = make_user("eng-mentor", role=Role.MENTOR)
    assert (
        client.get("/api/student/english-baseline", headers=mentor.headers).status_code == 403
    )


# ---------------------------------------------------------------------------
# Mentor meeting log
# ---------------------------------------------------------------------------


@requires_db
def test_meeting_log_is_empty_and_honest_for_a_new_student(client, make_user):
    stu = make_user("mtg-none")
    body = client.get("/api/student/mentor-meetings", headers=stu.headers).json()
    assert body["meetings"] == []
    assert body["meetings_logged"] == 0
    assert body["last_meeting"] is None
    assert body["open_actions"] == 0


@requires_db
def test_meeting_log_reads_the_students_own_notes_newest_first(client, make_user, mentor_row):
    """Also pins the student-facing wording: a note whose linked action is
    FLAGGED reads as "Flagged for follow-up", not as the internal enum. A
    student reading "FLAGGED" about themselves learns nothing and worries more
    than the note warrants."""
    stu = make_user("mtg-own")
    other = make_user("mtg-other")
    mentor = mentor_row("mtg-mentor")
    sid = _student_id(stu.user_id)
    other_sid = _student_id(other.user_id)

    mentor_id = mentor.mentor_id
    with SessionLocal() as db:
        base = datetime.now(timezone.utc)
        db.add_all(
            [
                MentorNote(
                    mentor_id=mentor_id, student_id=sid, note_text="Older note",
                    title="Mock GD debrief", location="Room 201",
                    meeting_at=base - timedelta(days=14),
                ),
                MentorNote(
                    mentor_id=mentor_id, student_id=sid, note_text="Newer note",
                    title="1:1 review", location="Cabin 3",
                    linked_action=MentorAction.FLAGGED, meeting_at=base - timedelta(days=1),
                ),
                MentorNote(
                    mentor_id=mentor_id, student_id=other_sid, note_text="Someone else's note",
                    meeting_at=base,
                ),
            ]
        )
        db.commit()

    body = client.get("/api/student/mentor-meetings", headers=stu.headers).json()
    assert body["meetings_logged"] == 2
    assert [m["note"] for m in body["meetings"]] == ["Newer note", "Older note"]
    assert body["meetings"][0]["title"] == "1:1 review"
    assert body["meetings"][0]["location"] == "Cabin 3"
    assert body["meetings"][0]["action_label"] == "Flagged for follow-up"
    assert body["open_actions"] == 1
    # A note with no linked action reads as "Note only", not as an empty string.
    assert body["meetings"][1]["action_label"] == "Note only"
    # Another student's note is not in this student's log.
    assert all("Someone else" not in m["note"] for m in body["meetings"])


@requires_db
def test_an_untitled_note_falls_back_to_its_action(client, make_user, mentor_row):
    """The migration deliberately does not backfill titles — inventing one would
    put words in a mentor's mouth on a screen the student reads. The log has to
    cope."""
    stu = make_user("mtg-untitled")
    mentor = mentor_row("mtg-untitled-mentor")
    sid = _student_id(stu.user_id)

    mentor_id = mentor.mentor_id
    with SessionLocal() as db:
        db.add(
            MentorNote(
                mentor_id=mentor_id, student_id=sid, note_text="No heading on this one",
                linked_action=MentorAction.ONE_ON_ONE_SCHEDULED,
            )
        )
        db.commit()

    body = client.get("/api/student/mentor-meetings", headers=stu.headers).json()
    assert body["meetings"][0]["title"] == "1:1 scheduled"
    assert body["meetings"][0]["location"] is None


# ---------------------------------------------------------------------------
# Programme stage cards
# ---------------------------------------------------------------------------


@requires_db
def test_programme_returns_the_three_stages_with_defaults(client, make_user):
    """A student with no milestone rows is NOT_STARTED across the board — the
    catalogue is code, and absence is the default rather than a missing row
    somebody forgot to seed."""
    stu = make_user("prog-new")
    body = client.get("/api/student/programme", headers=stu.headers).json()
    assert [s["key"] for s in body["stages"]] == ["reboot", "excel", "elevate"]
    # 3 + 4 + 4. Elevate had five until 2026-09-17, when "Specialization Cert"
    # left the catalogue with the screen behind it.
    assert body["total"] == 11
    assert body["completed"] == 0
    reboot = body["stages"][0]
    assert [i["key"] for i in reboot["items"]] == ["ree_101", "ree_102", "english_baseline"]
    assert all(i["status"] == "NOT_STARTED" for i in reboot["items"])
    assert reboot["items"][0]["glyph"] == "radio_button_unchecked"
    # The English row is a link, and it points at the screen that owns it.
    assert reboot["items"][2]["route"] == "/student/english"


@requires_db
def test_stored_milestones_and_the_derived_english_row(client, make_user):
    stu = make_user("prog-mixed")
    sid = _student_id(stu.user_id)
    with SessionLocal() as db:
        db.add_all(
            [
                StudentMilestone(student_id=sid, key="ree_101", status=MilestoneStatus.COMPLETED),
                StudentMilestone(student_id=sid, key="ree_102", status=MilestoneStatus.IN_PROGRESS),
                # A stored row for the DERIVED key must be ignored — this is the
                # trap the derivation exists to close.
                StudentMilestone(
                    student_id=sid, key="english_baseline", status=MilestoneStatus.NOT_STARTED
                ),
                # A key no longer in the catalogue is inert, not fatal.
                StudentMilestone(student_id=sid, key="retired_module", status=MilestoneStatus.COMPLETED),
            ]
        )
        baseline = EnglishBaseline(
            student_id=sid, semester=1, status=BaselineStatus.COMPLETE, overall_score=71, band="B2"
        )
        db.add(baseline)
        db.commit()

    body = client.get("/api/student/programme", headers=stu.headers).json()
    reboot = body["stages"][0]
    by_key = {i["key"]: i for i in reboot["items"]}
    assert by_key["ree_101"]["status"] == "COMPLETED"
    assert by_key["ree_101"]["glyph"] == "check_circle"
    assert by_key["ree_102"]["status"] == "IN_PROGRESS"
    assert by_key["ree_102"]["glyph"] == "pending"
    # Derived from the COMPLETE attempt, NOT from the stored NOT_STARTED row.
    assert by_key["english_baseline"]["status"] == "COMPLETED"
    assert reboot["completed"] == 2
    # The retired key contributed nothing.
    assert body["total"] == 11


@requires_db
def test_programme_is_student_only(client, make_user):
    director = make_user("prog-director", role=Role.ADMIN)
    assert client.get("/api/student/programme", headers=director.headers).status_code == 403


@requires_db
def test_a_published_notebook_entry_reaches_the_students_log(client, make_user, mentor_row):
    """The mentor writes in the Phase 4 notebook; the student reads this screen.

    Those are two different tables, and until they were merged here publishing
    an entry had no visible effect at all: the row sat PUBLISHED and
    STUDENT_VISIBLE while this endpoint read `mentor_notes` alone, so the
    mentor's deliberate act of sharing produced an empty log and no error on
    either screen.

    Also pins what must NOT cross: a DRAFT and a PRIVATE_STAFF entry are
    staff-internal by construction. If either ever appears here, a mentor's
    private working note has been published to the student it is about.
    """
    stu = make_user("nb-own")
    mentor = mentor_row("nb-mentor")
    sid = _student_id(stu.user_id)
    base = datetime.now(timezone.utc)

    with SessionLocal() as db:
        db.add_all(
            [
                MentorNote(
                    mentor_id=mentor.mentor_id, student_id=sid,
                    note_text="Legacy note", title="Older 1:1",
                    meeting_at=base - timedelta(days=10),
                ),
                MentorNotebookEntry(
                    student_id=sid, author_user_id=mentor.user_id,
                    mentor_id=mentor.mentor_id, body="Published notebook entry",
                    title="Placement readiness", entry_type=NotebookEntryType.MEETING,
                    visibility=NotebookVisibility.STUDENT_VISIBLE,
                    status=RecordStatus.PUBLISHED,
                    meeting_at=base - timedelta(days=2), published_at=base,
                ),
                MentorNotebookEntry(
                    student_id=sid, author_user_id=mentor.user_id,
                    mentor_id=mentor.mentor_id, body="Still a draft",
                    visibility=NotebookVisibility.STUDENT_VISIBLE,
                    status=RecordStatus.DRAFT, meeting_at=base - timedelta(days=1),
                ),
                MentorNotebookEntry(
                    student_id=sid, author_user_id=mentor.user_id,
                    mentor_id=mentor.mentor_id, body="Staff eyes only",
                    visibility=NotebookVisibility.PRIVATE_STAFF,
                    status=RecordStatus.PUBLISHED, meeting_at=base,
                ),
            ]
        )
        db.commit()

    body = client.get("/api/student/mentor-meetings", headers=stu.headers).json()
    notes = [m["note"] for m in body["meetings"]]

    assert "Published notebook entry" in notes
    assert "Legacy note" in notes
    assert "Still a draft" not in notes
    assert "Staff eyes only" not in notes

    # Both stores in one list, newest first, with the count agreeing.
    assert notes == ["Published notebook entry", "Legacy note"]
    assert body["meetings_logged"] == 2

    entry = body["meetings"][0]
    assert entry["title"] == "Placement readiness"
    # The notebook records no location; the field stays null rather than
    # inventing one.
    assert entry["location"] is None
    # A notebook entry carries no mentor_notes action, so it reads as a note.
    assert entry["action_label"] == "Note only"
    # It must not inflate the open-actions tile: a note is not a task.
    assert body["open_actions"] == 0


@requires_db
def test_an_untitled_notebook_entry_falls_back_to_its_type(client, make_user, mentor_row):
    """Same contract as an untitled mentor_note: a heading is derived, never
    blank, and never the raw staff enum. A student reading "WELLBEING" as a
    heading about themselves learns less than the words do."""
    stu = make_user("nb-untitled")
    mentor = mentor_row("nb-untitled-mentor")
    sid = _student_id(stu.user_id)

    with SessionLocal() as db:
        db.add(
            MentorNotebookEntry(
                student_id=sid, author_user_id=mentor.user_id,
                mentor_id=mentor.mentor_id, body="No title on this one",
                entry_type=NotebookEntryType.WELLBEING,
                visibility=NotebookVisibility.STUDENT_VISIBLE,
                status=RecordStatus.PUBLISHED,
                meeting_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    body = client.get("/api/student/mentor-meetings", headers=stu.headers).json()
    assert body["meetings"][0]["title"] == "Wellbeing check-in"


@requires_db
def test_a_notebook_entry_with_no_meeting_date_still_dates_the_line(client, make_user, mentor_row):
    """meeting_at is nullable on a notebook entry -- not every entry records a
    meeting -- but met_on is NOT NULL on the way out. It falls back to when the
    entry was published, so the student gets a line they can place in time
    rather than a blank date."""
    stu = make_user("nb-nodate")
    mentor = mentor_row("nb-nodate-mentor")
    sid = _student_id(stu.user_id)
    published = datetime.now(timezone.utc) - timedelta(days=3)

    with SessionLocal() as db:
        db.add(
            MentorNotebookEntry(
                student_id=sid, author_user_id=mentor.user_id,
                mentor_id=mentor.mentor_id, body="Dated by publication",
                visibility=NotebookVisibility.STUDENT_VISIBLE,
                status=RecordStatus.PUBLISHED,
                meeting_at=None, published_at=published,
            )
        )
        db.commit()

    body = client.get("/api/student/mentor-meetings", headers=stu.headers).json()
    assert body["meetings"][0]["met_on"] is not None
    assert body["meetings"][0]["day"] == f"{published:%d}"
