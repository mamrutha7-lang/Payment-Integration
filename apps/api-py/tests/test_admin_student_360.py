"""B4.5 — `GET /api/admin/students/{id}/360`, the whole of one student.

WHAT THIS MODULE HOLDS DOWN, in the order a break would be quietest:

1. BOTH FENCES, SEPARATELY. The capability alone does not open rule 2, and rule
   2 alone does not open a scoped grant. Two tests, one per direction, because
   a change that reaches one system and not the other OPENS the other — which
   is not hypothetical in REEP, it is what the DIRECTOR removal did for a few
   hours.
2. NO NUMBER IS INVENTED. A semester whose dates were never recorded reports
   `null` activity, not `0`; a semester with no results row reports `null`
   results, not a row of zeros; an interview that produced no score does not
   drag the best score to 0. This is the rule that has already bitten three
   REEP screens.
3. THE READINESS IS THE STUDENT'S OWN. Byte-for-byte the payload the student
   reads on their own dashboard, because it is the same builder — a staff-shaped
   copy is how a mentor ends up seeing a confident 0 where the student sees a
   dash.
4. IT WRITES NOTHING. No audit row of its own, however many times it is opened.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import delete, func, select

from conftest import TEST_PASSWORD, requires_db

from app.db import SessionLocal
from app.models.academics import SemesterResult
from app.models.account_events import LoginEvent
from app.models.badge import (
    BadgeEvidence,
    EvidenceStatus,
    EvidenceType,
    StudentBadge,
    StudentBadgeStatus,
)
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, AcademicCourse, College, Department
from app.models.interview import InterviewEvaluation, InterviewSession
from app.models.job import DegreeLevel
from app.models.redesign import AuditEvent
from app.models.semester_history import KIND_PROMOTE, StudentSemesterHistory
from app.models.student_profile import StudentProfile
from app.models.time_ledger import (
    DayActivity,
    LedgerDayStatus,
    LedgerSlot,
    TimeLedgerCell,
    TimeLedgerDay,
)
from app.models.upload import Upload, UploadKind, UploadStatus
from app.models.user import LoginDay, Mentor, Role, Student, User
from app.security import hash_password


def _url(student_id: str) -> str:
    return f"/api/admin/students/{student_id}/360"


#: The subject's semester windows, written as history rows. Semester 1 has no
#: move INTO it, so its start comes from `enrolled_at` — the one fallback the
#: window derivation allows, and only because a student in semester 1 with no
#: history has provably never been anywhere else.
ENROLLED = datetime(2024, 8, 1, tzinfo=timezone.utc)
INTO_SEM_2 = date(2025, 1, 15)
INTO_SEM_3 = date(2025, 7, 15)


@pytest.fixture
def subject(client):
    """One student with something in every panel, built with direct inserts.

    Direct rather than through the API because there is no `POST
    /admin/students` (2026-09-10) and no admin write path for results, ledger
    days, interviews or badges — the screen this endpoint feeds reads things
    the console does not create. The three account rows mirror exactly what
    registration's `_provision_student` writes.
    """
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {}
    with SessionLocal() as db:
        college = College(code=f"T360{tag.upper()}", name="Three Sixty College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        home = Department(college_id=college.id, name="Home Department", code=f"H{tag}")
        other = Department(college_id=college.id, name="Other Department", code=f"O{tag}")
        db.add_all([home, other])
        db.flush()
        course = AcademicCourse(
            department_id=home.id, code=f"C{tag}", name="Three Sixty Programme",
            duration_months=24, total_semesters=4,
        )
        db.add(course)
        db.flush()
        batch = Cohort(
            code=f"B{tag}", name="Three Sixty Batch", batch_label="2024-26",
            degree_level=DegreeLevel.PG, department_id=home.id, course_id=course.id,
            start_date=datetime(2024, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        db.add(batch)
        db.flush()

        student_user = User(
            email=f"t360-stu-{tag}@bgscet.ac.in", name="Three Sixty Student",
            role=Role.STUDENT, password_hash=hash_password(TEST_PASSWORD),
        )
        faculty_user = User(
            email=f"t360-fac-{tag}@bgscet.ac.in", name="Three Sixty Faculty",
            role=Role.MENTOR, password_hash=hash_password(TEST_PASSWORD),
        )
        stranger_user = User(
            email=f"t360-str-{tag}@bgscet.ac.in", name="Three Sixty Stranger",
            role=Role.MENTOR, password_hash=hash_password(TEST_PASSWORD),
        )
        db.add_all([student_user, faculty_user, stranger_user])
        db.flush()
        group = Mentor(user_id=faculty_user.id)
        db.add(group)
        db.flush()
        student = Student(
            user_id=student_user.id, usn=f"1T360{tag.upper()}", cohort_id=batch.id,
            mentor_id=group.id, current_semester=3, enrolled_at=ENROLLED,
        )
        db.add(student)
        db.flush()
        # A profile with SOME of the office's fields filled in, so the open-items
        # panel has both halves of its answer to prove.
        db.add(StudentProfile(
            student_id=student.id, email=student_user.email, phone="9000000000",
            city="Bengaluru",
        ))

        # Results for semesters 1 and 2. Semester 2's SGPA is deliberately NULL:
        # a published result whose SGPA has not been entered is a real state and
        # must not render as 0.0.
        db.add_all([
            SemesterResult(student_id=student.id, semester=1, sgpa=7.5, cgpa=7.5, live_backlogs=0),
            SemesterResult(student_id=student.id, semester=2, sgpa=None, cgpa=None, live_backlogs=1),
        ])
        db.add_all([
            StudentSemesterHistory(
                student_id=student.id, from_semester=1, to_semester=2,
                effective_on=INTO_SEM_2, kind=KIND_PROMOTE, reason="Results published",
            ),
            StudentSemesterHistory(
                student_id=student.id, from_semester=2, to_semester=3,
                effective_on=INTO_SEM_3, kind=KIND_PROMOTE,
            ),
        ])

        # One submitted day inside semester 1, one inside semester 2, and a
        # DRAFT day with a cell in it — which is an open item, not a reconciled
        # day, and must be counted in exactly one of the two places.
        for day, status in (
            (date(2024, 10, 1), LedgerDayStatus.SUBMITTED),
            (date(2025, 3, 1), LedgerDayStatus.SUBMITTED),
            (date(2025, 8, 1), LedgerDayStatus.DRAFT),
        ):
            row = TimeLedgerDay(student_id=student.id, day=day, status=status)
            db.add(row)
            db.flush()
            db.add(TimeLedgerCell(
                ledger_day_id=row.id, slot=LedgerSlot.MORNING,
                activity=DayActivity.SKILLING, half_hours=4,
            ))

        # Two interviews inside semester 2. One was scored, one was not — the
        # best score must be 7 and never 0.
        scored = InterviewSession(
            student_id=student.id, status="completed",
            started_at=datetime(2025, 3, 2, 10, 0, tzinfo=timezone.utc),
        )
        unscored = InterviewSession(
            student_id=student.id, status="abandoned",
            started_at=datetime(2025, 3, 3, 10, 0, tzinfo=timezone.utc),
        )
        db.add_all([scored, unscored])
        db.flush()
        db.add(InterviewEvaluation(
            interview_session_id=scored.id, report_status="ok", overall_score=7,
        ))

        db.add(StudentBadge(
            student_id=student.id, badge_code="NEGOTIATION", status=StudentBadgeStatus.EARNED,
            points_awarded=10, earned_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
        ))
        db.add(Upload(
            student_id=student.id, kind=UploadKind.DOCUMENT, title="Internship report",
            original_name="report.pdf", stored_name=f"t360-{tag}.pdf",
            mime_type="application/pdf", size_bytes=10, status=UploadStatus.PENDING_REVIEW,
        ))
        db.add_all([
            BadgeEvidence(
                student_id=student.id, badge_code="NEGOTIATION", title="A course",
                evidence_type=EvidenceType.EXTERNAL_VERIFIED,
                status=EvidenceStatus.PENDING_VERIFICATION,
            ),
            BadgeEvidence(
                student_id=student.id, badge_code="NEGOTIATION", title="Another course",
                evidence_type=EvidenceType.EXTERNAL_VERIFIED,
                status=EvidenceStatus.MORE_INFO_REQUIRED,
            ),
        ])
        db.add_all([
            LoginEvent(user_id=student_user.id, door="password",
                       at=datetime(2025, 5, 1, tzinfo=timezone.utc)),
            LoginEvent(user_id=student_user.id, door="google",
                       at=datetime(2025, 6, 1, tzinfo=timezone.utc)),
        ])
        db.commit()

        made = {
            "college": college.id, "home": home.id, "other": other.id,
            "course": course.id, "batch": batch.id, "student": student.id,
            "student_user": student_user.id, "faculty_user": faculty_user.id,
            "stranger_user": stranger_user.id, "group": group.id,
            "student_email": student_user.email, "faculty_email": faculty_user.email,
            "stranger_email": stranger_user.email,
        }

    yield made

    with SessionLocal() as db:
        sid, uids = made["student"], [
            made["student_user"], made["faculty_user"], made["stranger_user"]
        ]
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id.in_(uids)))
        db.execute(delete(AuditEvent).where(AuditEvent.entity_id.in_([sid, *uids])))
        for session_id in db.scalars(
            select(InterviewSession.id).where(InterviewSession.student_id == sid)
        ).all():
            db.execute(delete(InterviewEvaluation).where(
                InterviewEvaluation.interview_session_id == session_id
            ))
        db.execute(delete(InterviewSession).where(InterviewSession.student_id == sid))
        for day_id in db.scalars(
            select(TimeLedgerDay.id).where(TimeLedgerDay.student_id == sid)
        ).all():
            db.execute(delete(TimeLedgerCell).where(TimeLedgerCell.ledger_day_id == day_id))
        db.execute(delete(TimeLedgerDay).where(TimeLedgerDay.student_id == sid))
        db.execute(delete(Upload).where(Upload.student_id == sid))
        db.execute(delete(BadgeEvidence).where(BadgeEvidence.student_id == sid))
        db.execute(delete(StudentBadge).where(StudentBadge.student_id == sid))
        db.execute(delete(SemesterResult).where(SemesterResult.student_id == sid))
        # NO ondelete on this FK, deliberately: a promotion record with no
        # student is not a record of anything, so the database refuses to delete
        # the student out from under one and the teardown has to say so too.
        db.execute(delete(StudentSemesterHistory).where(StudentSemesterHistory.student_id == sid))
        db.execute(delete(StudentProfile).where(StudentProfile.student_id == sid))
        db.execute(delete(LoginEvent).where(LoginEvent.user_id.in_(uids)))
        # Signing in writes one of these per account per day (the dashboard
        # streak), and the FK has no ondelete — so a teardown that forgets it
        # fails on the accounts the test logged in as, never on the ones it
        # only inserted.
        db.execute(delete(LoginDay).where(LoginDay.user_id.in_(uids)))
        db.execute(delete(Student).where(Student.id == sid))
        db.execute(delete(Mentor).where(Mentor.id == made["group"]))
        db.execute(delete(User).where(User.id.in_(uids)))
        db.execute(delete(Cohort).where(Cohort.id == made["batch"]))
        db.execute(delete(AcademicCourse).where(AcademicCourse.id == made["course"]))
        db.execute(delete(Department).where(Department.id.in_([made["home"], made["other"]])))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


@pytest.fixture
def grant():
    """Write a capability grant straight onto the row, and take it away after.

    Through the row rather than `POST /admin/governance/grants` because
    `admin.student_records` is `carries_pii`, so the endpoint writes a DEPUTY's grant
    `pending_approval` — it HOLDS NOTHING until a different `admin.governance`
    holder approves it (B2.4) — and the Main Admin's live at once. That is
    correct behaviour and it is tested where it belongs, in
    test_governance_review.py; a fixture that depended on who granted would
    make every scope assertion here about the approval rule instead.
    """
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel | None = None, target: str | None = None) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key, subject_kind=SubjectKind.USER, subject_user_id=user_id,
                scope_level=level, scope_id=target,
                reason="a test grant, comfortably past the reason floor",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made or [""])))
        db.commit()


# ------------------------------------------------------------- the gates --

#: The read's own key (2026-09-17). It was `admin.students` -- the roster
#: editor's key -- until the office asked to hand "view this candidate's
#: complete details" to a faculty member without handing them the editor.
KEY = "admin.student_records"


@requires_db
def test_the_capability_is_the_gate(client, make_user, login, subject):
    """A STUDENT and an ungranted faculty member who mentors nobody are both
    refused; the office is not, and neither is the student's own mentor, who
    holds the key by mentoring them (2026-10-16)."""
    admin = make_user("t360-admin", Role.ADMIN)
    student_headers = login(subject["student_email"], TEST_PASSWORD)
    stranger_headers = login(subject["stranger_email"], TEST_PASSWORD)

    assert client.get(_url(subject["student"]), headers=student_headers).status_code == 403
    refused = client.get(_url(subject["student"]), headers=stranger_headers)
    assert refused.status_code == 403
    assert "View student records" in refused.text, "the 403 names the capability to ask for"

    mentor_headers = login(subject["faculty_email"], TEST_PASSWORD)
    assert client.get(_url(subject["student"]), headers=mentor_headers).status_code == 200

    assert client.get(_url(subject["student"]), headers=admin.headers).status_code == 200
    assert client.get(_url("no-such-student"), headers=admin.headers).status_code == 404


@requires_db
def test_the_roster_key_alone_does_not_open_the_record(client, login, subject, grant):
    """The split is the point: `admin.students` edits rows, `admin.student_records`
    reads the record behind one, and holding the first is not holding the
    second. A faculty member granted only the roster editor is refused here
    with the read key named, so the office knows which grant to make."""
    grant(subject["stranger_user"], "admin.students")
    headers = login(subject["stranger_email"], TEST_PASSWORD)
    refused = client.get(_url(subject["student"]), headers=headers)
    assert refused.status_code == 403
    assert "View student records" in refused.text


@requires_db
def test_rule_2_is_not_relaxed_by_the_capability(client, login, subject, grant):
    """A faculty member holding `admin.student_records` PROGRAMME-WIDE still
    sees only their own group.

    The grant is as wide as a grant gets — no scope level, no scope id, which
    B1.2 reads as programme-wide — and it is still not a way past rule 2. The
    stranger mentors nobody, so they see nobody: never the whole programme.
    `app/governance.py` says it in one line, "a capability can never relax the
    student filter", and this is that line as a request.
    """
    grant(subject["stranger_user"], KEY)
    stranger = login(subject["stranger_email"], TEST_PASSWORD)

    refused = client.get(_url(subject["student"]), headers=stranger)
    assert refused.status_code == 404, "rule 2 refuses with a 404, never a 403"
    assert "mentor scope" in refused.text

    # And the faculty member who DOES mentor this student reaches them, by
    # mentoring them (2026-10-16) — so the refusal above is scope and not a
    # broken endpoint.
    mentor = login(subject["faculty_email"], TEST_PASSWORD)
    assert client.get(_url(subject["student"]), headers=mentor).status_code == 200


@requires_db
def test_a_grant_that_reaches_somewhere_else_is_refused_here(client, login, subject, grant):
    """B1.2's second question. The grant is real, but it hangs on the WRONG
    department.

    Without the `target=` argument the narrowing on the roster list would be
    decoration: a scoped holder who cannot see a student in the grid could open
    their entire record by typing the id. The holder here mentors nobody: the
    student's own mentor opens the record by mentoring them (2026-10-16), so a
    grant is not what decides it for them.
    """
    grant(subject["stranger_user"], KEY, ScopeLevel.DEPARTMENT, subject["other"])
    headers = login(subject["stranger_email"], TEST_PASSWORD)

    refused = client.get(_url(subject["student"]), headers=headers)
    assert refused.status_code == 403, "a grant held somewhere else is a 403, not a 404"

    grant(subject["stranger_user"], KEY, ScopeLevel.DEPARTMENT, subject["home"])
    assert client.get(_url(subject["student"]), headers=headers).status_code == 200


# ---------------------------------------------- the two panels added 2026-09-17 --


@requires_db
def test_the_record_carries_the_profile_as_filled_in_and_every_document(
    client, make_user, subject
):
    """"Complete details" means the values, not only the names of the gaps.

    The open-items panel already said which profile fields were MISSING; the
    phone number the student typed was on no panel at all, and their documents
    had no by-id read anywhere. Both are on the payload now, and the document
    row carries its verdict so the office reads the same word the student does.
    """
    admin = make_user("t360-panels", Role.ADMIN)
    body = client.get(_url(subject["student"]), headers=admin.headers).json()

    profile = body["profile"]
    assert profile["on_record"] is True
    assert profile["phone"] == "9000000000"
    assert profile["contact_email"] == subject["student_email"]
    assert isinstance(profile["skills"], list)

    documents = body["documents"]
    assert [d["title"] for d in documents] == ["Internship report"]
    assert documents[0]["status"] == "PENDING_REVIEW"
    assert documents[0]["kind"] == "DOCUMENT"
    assert documents[0]["review_note"] is None
    # The bytes are reached through rule 2's file route, never inlined here.
    assert "content" not in documents[0] and "stored_name" not in documents[0]


@requires_db
def test_a_student_with_no_profile_reads_as_no_profile_and_not_as_blanks(client, make_user):
    """`on_record` is the flag the screen branches on; a student who never
    saved a profile is eight nulls under it, not eight blanks they left."""
    admin = make_user("t360-noprof-admin", Role.ADMIN)
    fresh = make_user("t360-noprof", Role.STUDENT)
    with SessionLocal() as db:
        student_id = db.scalar(select(Student.id).where(Student.user_id == fresh.user_id))
    body = client.get(_url(student_id), headers=admin.headers).json()
    assert body["profile"]["on_record"] is False
    assert body["profile"]["phone"] is None
    assert body["profile"]["placement_eligible"] is None
    assert body["documents"] == []


# ---------------------------------------------------------- the timeline --


@requires_db
def test_the_timeline_attributes_activity_to_the_recorded_semesters(client, make_user, subject):
    admin = make_user("t360-tl", Role.ADMIN)
    body = client.get(_url(subject["student"]), headers=admin.headers).json()

    assert body["total_semesters"] == 4, "the course's shape (B4.1), read through the batch"
    rows = {row["semester"]: row for row in body["semesters"]}
    assert sorted(rows) == [1, 2, 3], "1 up to the student's current position"
    assert rows[3]["is_current"] is True

    one, two, three = rows[1], rows[2], rows[3]

    # Semester 1's start is the enrolment date — the one fallback — and it ends
    # the day they were promoted out of it.
    assert one["started_on"] == ENROLLED.date().isoformat()
    assert one["ended_on"] == INTO_SEM_2.isoformat()
    assert two["started_on"] == INTO_SEM_2.isoformat()
    assert two["ended_on"] == INTO_SEM_3.isoformat()
    assert three["ended_on"] is None, "the current semester is open"

    assert one["results"]["sgpa"] == 7.5
    assert two["results"]["sgpa"] is None, "an unentered SGPA is a dash, never 0.0"
    assert two["results"]["live_backlogs"] == 1
    assert three["results"] is None, "no results imported is null, not a row of zeros"

    assert one["ledger_days_reconciled"] == 1
    assert two["ledger_days_reconciled"] == 1
    assert three["ledger_days_reconciled"] == 0, (
        "a KNOWN window with nothing in it really is zero — that is a different "
        "fact from an unknown window, and only one of them is null"
    )
    assert two["interviews"] == 2
    assert two["best_interview_score"] == 7, "the unscored interview does not drag it to 0"
    assert one["interviews"] == 0 and one["best_interview_score"] is None
    assert two["badges_earned"] == 1
    assert one["badges_earned"] == 0

    history = body["semester_history"]
    assert [r["to_semester"] for r in history] == [3, 2], "newest move first"
    assert history[1]["reason"] == "Results published"
    assert history[0]["kind"] == "promote"


@requires_db
def test_a_semester_with_no_recorded_dates_reports_null_and_not_zero(client, make_user, subject):
    """THE RULE THIS ENDPOINT EXISTS UNDER.

    Every student on a deployment that has never run a promotion has NO history
    rows, so nothing says which semester a ledger day or an interview belongs
    to. Attributing them all to the student's current semester would put three
    semesters of activity under semester 4 and look entirely plausible. The
    counts are null instead, and `activity_known` is the flag that says why.
    """
    admin = make_user("t360-unknown", Role.ADMIN)
    with SessionLocal() as db:
        db.execute(
            delete(StudentSemesterHistory).where(
                StudentSemesterHistory.student_id == subject["student"]
            )
        )
        db.commit()

    rows = client.get(_url(subject["student"]), headers=admin.headers).json()["semesters"]
    by_semester = {row["semester"]: row for row in rows}

    for semester in (1, 2, 3):
        row = by_semester[semester]
        assert row["activity_known"] is False, f"semester {semester} has no recorded window"
        assert row["started_on"] is None and row["ended_on"] is None
        assert row["ledger_days_reconciled"] is None
        assert row["interviews"] is None
        assert row["best_interview_score"] is None
        assert row["badges_earned"] is None

    # The results are NOT null: `semester_results.semester` says which semester
    # a mark belongs to on the row itself, so marks never needed a window.
    assert by_semester[1]["results"]["sgpa"] == 7.5


@requires_db
def test_semester_one_with_no_history_is_the_one_window_that_is_provable(
    client, make_user, subject
):
    """A student who has never been promoted has only ever been in semester 1,
    so enrolment-to-now is a fact rather than a guess — and it is the only
    fallback the derivation allows."""
    admin = make_user("t360-sem1", Role.ADMIN)
    with SessionLocal() as db:
        db.execute(
            delete(StudentSemesterHistory).where(
                StudentSemesterHistory.student_id == subject["student"]
            )
        )
        student = db.get(Student, subject["student"])
        student.current_semester = 1
        db.commit()

    rows = {r["semester"]: r for r in
            client.get(_url(subject["student"]), headers=admin.headers).json()["semesters"]}

    assert rows[1]["activity_known"] is True
    assert rows[1]["started_on"] == ENROLLED.date().isoformat()
    assert rows[1]["ended_on"] is None
    # Everything the fixture wrote falls inside that one open window.
    assert rows[1]["ledger_days_reconciled"] == 2
    assert rows[1]["interviews"] == 2
    assert rows[1]["badges_earned"] == 1

    # Semester 2 is still LISTED, because a `semester_results` row says it
    # happened — the timeline spans as far as the evidence does, not as far as
    # the student's current position. It has no window, so it has no counts, and
    # that contradiction (results for a semester the student has not reached) is
    # shown rather than resolved by dropping one of the two facts.
    assert 2 in rows
    assert rows[2]["activity_known"] is False
    assert rows[2]["ledger_days_reconciled"] is None
    assert rows[2]["results"] is not None


# ------------------------------------------------- the rest of the panels --


@requires_db
def test_the_open_items_are_counted_and_the_gaps_are_named(client, make_user, subject):
    admin = make_user("t360-open", Role.ADMIN)
    items = client.get(_url(subject["student"]), headers=admin.headers).json()["open_items"]

    assert items["pending_uploads"] == 1
    assert items["pending_badge_claims"] == 1
    assert items["badge_claims_needing_info"] == 1, (
        "evidence sent back needs the STUDENT, not staff — the two are kept apart"
    )
    assert items["unsubmitted_ledger_days"] == 1, "the DRAFT day with a cell in it"
    missing = items["missing_profile_fields"]
    assert "LinkedIn URL" in missing and "Career summary" in missing
    assert "Phone number" not in missing and "City" not in missing and "USN" not in missing
    assert items["total"] == 1 + 1 + 1 + 1 + len(missing)


@requires_db
def test_the_login_panel_answers_the_questions_the_office_asks(client, make_user, subject):
    admin = make_user("t360-login", Role.ADMIN)
    login_panel = client.get(_url(subject["student"]), headers=admin.headers).json()["login"]

    assert login_panel["google_linked"] is False, (
        "a real answer, not the None `/auth/me` returns when nobody asked"
    )
    assert login_panel["password_set"] is True
    assert login_panel["disabled"] is False
    assert login_panel["disable_reason"] is None
    assert isinstance(login_panel["token_version"], int)
    doors = [row["door"] for row in login_panel["recent_sign_ins"]]
    assert doors[:2] == ["google", "password"], "newest sign-in first"


@requires_db
def test_the_mentor_history_panel_is_explicitly_empty_until_4d(client, make_user, subject):
    """B9.1 has not landed. The panel says so rather than being absent, and the
    CURRENT assignment is reported separately — as a present-tense fact, never
    as a history of one."""
    admin = make_user("t360-mentor", Role.ADMIN)
    body = client.get(_url(subject["student"]), headers=admin.headers).json()

    assert body["mentor_history"]["available"] is False
    assert body["mentor_history"]["entries"] == []
    assert "history" in body["mentor_history"]["note"].lower()

    assert body["current_mentor"]["mentor_id"] == subject["group"]
    assert body["current_mentor"]["mentor_user_id"] == subject["faculty_user"]
    assert body["current_mentor"]["mentor_name"] == "Three Sixty Faculty"


@requires_db
def test_the_readiness_is_the_students_own_number(client, make_user, login, subject):
    """The same builder, so the office and the student cannot be shown two
    different scores for the same person."""
    admin = make_user("t360-ready", Role.ADMIN)
    theirs = client.get(
        "/api/student/placement-readiness",
        headers=login(subject["student_email"], TEST_PASSWORD),
    ).json()
    ours = client.get(_url(subject["student"]), headers=admin.headers).json()["readiness"]
    assert ours == theirs


@requires_db
def test_the_identity_block_is_the_roster_row(client, make_user, subject):
    """The detail screen stops fetching the whole roster to pick one row out of
    it — so the row it gets here must be that same row."""
    admin = make_user("t360-identity", Role.ADMIN)
    identity = client.get(_url(subject["student"]), headers=admin.headers).json()["identity"]
    row = next(
        r
        for r in client.get(
            f"/api/admin/students?cohort_id={subject['batch']}", headers=admin.headers
        ).json()
        if r["student_id"] == subject["student"]
    )
    assert identity == row


@requires_db
def test_opening_a_record_writes_nothing(client, make_user, subject):
    """A read that audits itself would be the most frequent row in the table
    within a week and would bury every write the trail exists to show."""
    admin = make_user("t360-audit", Role.ADMIN)
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(AuditEvent))

    for _ in range(3):
        assert client.get(_url(subject["student"]), headers=admin.headers).status_code == 200

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == before


@requires_db
def test_the_audit_panel_shows_what_was_done_to_this_student(client, make_user, subject):
    """The rows are narrowed by ENTITY, which an audit row does carry — unlike
    the college that makes the whole trail Main-Admin-only in routers/audit.py."""
    admin = make_user("t360-trail", Role.ADMIN)
    patched = client.patch(
        f"/api/admin/students/{subject['student']}",
        headers=admin.headers,
        json={"current_stage": "EXCEL"},
    )
    assert patched.status_code == 200, patched.text

    rows = client.get(_url(subject["student"]), headers=admin.headers).json()["recent_audit"]
    mine = [r for r in rows if r["entity_id"] == subject["student"]]
    assert mine, "the edit that just happened is not on the record"
    assert mine[0]["action"] == "UPDATE"
    assert mine[0]["actor_name"] is not None
    assert mine[0]["route"] == f"/api/admin/students/{subject['student']}"


@requires_db
def test_an_unseated_student_is_readable_and_says_so(client, make_user, subject):
    """A student with no batch has no course, so `total_semesters` is null — and
    the client must not fall back to 8. They are still a student and the record
    must still open; being unfiled is the state the console exists to fix."""
    admin = make_user("t360-unseated", Role.ADMIN)
    with SessionLocal() as db:
        student = db.get(Student, subject["student"])
        student.cohort_id = None
        student.department_id = subject["home"]
        db.commit()

    body = client.get(_url(subject["student"]), headers=admin.headers).json()
    assert body["total_semesters"] is None
    assert body["identity"]["batch"] is None
    assert body["identity"]["department_id"] == subject["home"]
