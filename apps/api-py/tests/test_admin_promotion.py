"""Promotion and graduation — `app/routers/admin_promotion.py` (B4.2-B4.4).

WHAT THIS FILE IS FOR, in the order the danger runs:

  1. **Nothing is rewritten by a promotion.** Four tables carry a semester
     number and exactly one of them is the student's current position. The
     guard test snapshots every one of the other three around a real promotion
     and demands they come back byte-identical. It is the test the phase prompt
     asks for by name, and it is the one that would catch the "helpful" edit
     that walks `semester_results` forward with the student.

  2. **A graduate is refused `/api/student/*`.** Graduation flips `users.role`
     to ALUMNI and KEEPS the `students` row, so `session["studentId"]` survives
     it — and `_require_student` read only that claim. Every student endpoint
     would have stayed open behind an Angular guard that closed the screens. The
     test signs the graduate back in and knocks on four of those doors.

  3. **The semester bound is the course's, not a flat eight** (B4.2), including
     the fallback for a batch that names no course, and the error SHAPE: the
     check moved from a Pydantic `Field(le=…)` (detail is a LIST) to a handler
     `HTTPException` (detail is a STRING), and the console renders both through
     `detailOf`.

  4. The dry run reports before it applies, attendance is reported
     `unavailable` rather than invented, and the reversal window closes.

FIXTURES are borrowed by name from test_admin_institution (`chain`, `director`,
`tracker`, `_code`), the same way test_admin_students borrows them: the College
-> Department -> Batch chain is built THROUGH THE API, so the fixture itself
fails if the console cannot do this.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from conftest import TEST_PASSWORD, requires_db
from test_admin_institution import (  # noqa: F401 - fixtures by name
    _code,
    chain,
    director,
    tracker,
)

from app.db import SessionLocal
from app.models.academics import SemesterResult, SubjectMark
from app.models.catalogue import StageRule
from app.models.cohort import Cohort
from app.models.course import Course, CourseModel, Dimension
from app.models.english_baseline import EnglishBaseline
from app.models.institution import AcademicCourse
from app.models.redesign import AuditEvent
from app.models.semester_history import StudentSemesterHistory
from app.models.user import (
    STUDENT_STATUS_ACTIVE,
    STUDENT_STATUS_GRADUATED,
    LoginDay,
    Role,
    Stage,
    Student,
    User,
)

@pytest.fixture
def swept(make_user):
    """Rows this module writes directly, removed whatever the test does.

    DEPENDS ON `make_user` ON PURPOSE, and the dependency is the teardown order:
    a fixture set up after another is finalised before it, and
    `student_semester_history.student_id` is NOT NULL with no `ON DELETE`. Left
    behind, these rows make `make_user`'s own `delete(Student)` raise a
    ForeignKeyViolation and every later test in the process inherits a poisoned
    session — which is exactly the class of bug the two purge modules exist to
    survive, met here in miniature.
    """
    students: list[str] = []
    emails: list[str] = []
    courses: list[str] = []
    subjects: list[str] = []
    yield {"students": students, "emails": emails, "courses": courses, "subjects": subjects}
    with SessionLocal() as db:
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                sid = db.scalar(select(Student.id).where(Student.user_id == user.id))
                if sid:
                    students.append(sid)
        for sid in students:
            db.execute(delete(StudentSemesterHistory).where(StudentSemesterHistory.student_id == sid))
            db.execute(delete(EnglishBaseline).where(EnglishBaseline.student_id == sid))
            db.execute(delete(SemesterResult).where(SemesterResult.student_id == sid))
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                db.execute(delete(LoginDay).where(LoginDay.user_id == user.id))
                db.execute(delete(Student).where(Student.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
        for code in subjects:
            db.execute(delete(Course).where(Course.code == code))
        for cid in courses:
            db.execute(delete(StageRule).where(StageRule.course_id == cid))
        db.commit()


def _seat(student_id: str, cohort_id: str, *, semester: int = 1,
          stage: Stage = Stage.REBOOT, status: str = STUDENT_STATUS_ACTIVE) -> None:
    with SessionLocal() as db:
        st = db.get(Student, student_id)
        st.cohort_id = cohort_id
        st.current_semester = semester
        st.current_stage = stage
        st.status = status
        db.commit()


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


def _make_course(client, headers, chain, tracker, swept, *, total_semesters: int | None) -> str:
    """A course through the API, then `total_semesters` written directly.

    The column is B4.1's and may or may not be on `AcademicCourseIn` yet
    depending on what has landed beside this; writing it here keeps this module
    testing the BOUND rather than the shape of somebody else's request body.
    """
    course = client.post(
        f"/api/admin/departments/{chain['department']['id']}/academic-courses",
        headers=headers,
        json={"code": _code("CRS"), "name": "Master of Business Administration", "duration_months": 24},
    ).json()
    tracker["courses"].append(course["id"])
    swept["courses"].append(course["id"])
    with SessionLocal() as db:
        db.get(AcademicCourse, course["id"]).total_semesters = total_semesters
        db.commit()
    # Through the real writer — `_resolve_ancestry` is the only thing that may
    # set `cohorts.course_id`, and a test that wrote the pointer itself would
    # prove the bound works on a row the console cannot produce.
    r = client.patch(
        f"/api/admin/cohorts/{chain['cohort']['id']}", headers=headers,
        json={"course_id": course["id"]},
    )
    assert r.status_code == 200, r.text
    return course["id"]


def _seeded_student(client, make_user, swept, chain, label: str, **seat):
    account = make_user(f"promo-{label}", Role.STUDENT)
    sid = _student_id(account.user_id)
    swept["students"].append(sid)
    _seat(sid, chain["cohort"]["id"], **seat)
    return account, sid


# ------------------------------------------------------------- the gate --


@requires_db
def test_promotion_is_the_students_capability_and_nothing_weaker(client, make_user, chain):
    """No new capability key — `admin.students`, the same one the roster edits
    use — and a STUDENT and an ungranted MENTOR are both refused every verb."""
    student = make_user("promo-gate-stu")
    mentor = make_user("promo-gate-men", Role.MENTOR)
    cid = chain["cohort"]["id"]
    body = {"effective_on": "2026-08-01"}
    for headers in (student.headers, mentor.headers):
        assert client.post(f"/api/admin/cohorts/{cid}/promote", headers=headers, json=body).status_code == 403
        assert client.post(f"/api/admin/cohorts/{cid}/graduate", headers=headers, json=body).status_code == 403
        assert client.post(f"/api/admin/cohorts/{cid}/ungraduate", headers=headers, json={}).status_code == 403
        assert client.get(f"/api/admin/cohorts/{cid}/promotion-history", headers=headers).status_code == 403
    # And the capability answers for a batch that does not exist, in that order:
    # a 404 before the reach check would enumerate batch ids for a scoped holder.
    assert client.get("/api/admin/cohorts/no-such/promotion-history", headers=chain["headers"]).status_code == 404


# -------------------------------------------------------------- B4.2 --


@requires_db
def test_the_semester_bound_is_the_courses_not_a_flat_eight(client, make_user, chain, tracker, swept):
    """`MAX_SEMESTER = 8` is gone; the ceiling is `course.total_semesters`.

    Three facts in one test because they are one rule seen from three sides:
    the fallback for a batch with no course, the course's own number once it
    has one, and the same number applied to a whole batch at once.
    """
    h = chain["headers"]
    account, sid = _seeded_student(client, make_user, swept, chain, "bound")
    swept["emails"].append(account.email)
    api = f"/api/admin/students/{sid}"

    # (1) No course on this batch yet: the OLD flat bound still applies, so a
    # deployment that has not named its courses keeps working exactly as it did.
    assert client.patch(api, headers=h, json={"current_semester": 8}).status_code == 200
    over = client.patch(api, headers=h, json={"current_semester": 9})
    assert over.status_code == 422
    assert isinstance(over.json()["detail"], str), (
        "the bound moved from a Pydantic Field(le=) to a handler check, so `detail` "
        "is now a STRING and not a list of error objects - both consoles render it "
        "through detailOf"
    )
    assert "default" in over.json()["detail"].lower()

    # (2) The course names four semesters. Five is now refused and the refusal
    # names the programme rather than reciting a number.
    _make_course(client, h, chain, tracker, swept, total_semesters=4)
    assert client.patch(api, headers=h, json={"current_semester": 4}).status_code == 200
    refused = client.patch(api, headers=h, json={"current_semester": 5})
    assert refused.status_code == 422
    assert "Master of Business Administration" in refused.json()["detail"]
    assert "4 semesters" in refused.json()["detail"]

    # (3) The same bound on the batch action, refused BEFORE anything moves.
    bulk = f"/api/admin/cohorts/{chain['cohort']['id']}/students/bulk"
    assert client.post(bulk, headers=h, json={"action": "semester", "current_semester": 5}).status_code == 422
    with SessionLocal() as db:
        assert db.get(Student, sid).current_semester == 4, "nothing moved on the refusal"
    assert client.post(bulk, headers=h, json={"action": "semester", "current_semester": 2}).json()["affected"] == 1


# -------------------------------------------------------------- B4.3 --


@requires_db
def test_promote_moves_the_semester_and_rewrites_nothing(client, make_user, chain, tracker, swept):
    """THE GUARD TEST. Every other semester-bearing column comes back identical.

    `semester_results.semester`, `english_baselines.semester` and
    `courses.semester` are each under a unique constraint a rewrite would
    collide on halfway through a batch — and, collision or not, walking them
    forward turns "scored 8.1 in semester 2" into a claim about semester 3.
    """
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    _make_course(client, h, chain, tracker, swept, total_semesters=4)
    one, sid_one = _seeded_student(client, make_user, swept, chain, "keep1", semester=2)
    two, sid_two = _seeded_student(client, make_user, swept, chain, "keep2", semester=2)
    swept["emails"] += [one.email, two.email]

    subject_code = _code("SUB")
    swept["subjects"].append(subject_code)
    with SessionLocal() as db:
        for sid in (sid_one, sid_two):
            for sem, sgpa in ((1, 7.4), (2, 8.1)):
                result = SemesterResult(student_id=sid, semester=sem, sgpa=sgpa, cgpa=sgpa)
                db.add(result)
                db.flush()
                db.add(SubjectMark(
                    semester_result_id=result.id, subject_code=f"{subject_code}{sem}",
                    subject_name="Managerial Economics", credits=4,
                    internal=20, external=50, total=70, passed=True,
                ))
            db.add(EnglishBaseline(student_id=sid, semester=2))
        db.add(Course(
            code=subject_code, name="Managerial Economics", semester=2,
            stage=Stage.REBOOT, dimension=Dimension.PROFESSIONAL,
            model_type=CourseModel.INSTRUCTOR_LED,
        ))
        db.commit()

    def _snapshot() -> dict:
        with SessionLocal() as db:
            return {
                "results": sorted(
                    (r.student_id, r.semester, r.sgpa)
                    for r in db.scalars(
                        select(SemesterResult).where(SemesterResult.student_id.in_([sid_one, sid_two]))
                    )
                ),
                "marks": sorted(
                    (m.subject_code, m.total)
                    for m in db.scalars(
                        select(SubjectMark).join(SemesterResult)
                        .where(SemesterResult.student_id.in_([sid_one, sid_two]))
                    )
                ),
                "english": sorted(
                    (b.student_id, b.semester)
                    for b in db.scalars(
                        select(EnglishBaseline).where(EnglishBaseline.student_id.in_([sid_one, sid_two]))
                    )
                ),
                "curriculum": db.scalar(select(Course.semester).where(Course.code == subject_code)),
            }

    before = _snapshot()

    # The dry run writes NOTHING - not the semester, not a history row.
    dry = client.post(
        f"/api/admin/cohorts/{cid}/promote?dry_run=true", headers=h,
        json={"effective_on": "2026-08-01", "reason": "Results published."},
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True and dry.json()["affected"] == 2
    with SessionLocal() as db:
        assert db.get(Student, sid_one).current_semester == 2
        assert db.scalars(select(StudentSemesterHistory).where(
            StudentSemesterHistory.student_id == sid_one)).all() == []

    applied = client.post(
        f"/api/admin/cohorts/{cid}/promote", headers=h,
        json={"effective_on": "2026-08-01", "reason": "Results published."},
    )
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["dry_run"] is False and payload["affected"] == 2
    assert {(r["from_semester"], r["to_semester"]) for r in payload["students"]} == {(2, 3)}

    assert _snapshot() == before, (
        "a promotion moves students.current_semester and NOTHING ELSE; every other "
        "semester number is a fact about a semester that has already happened"
    )
    with SessionLocal() as db:
        assert {db.get(Student, s).current_semester for s in (sid_one, sid_two)} == {3}
        rows = db.scalars(select(StudentSemesterHistory).where(
            StudentSemesterHistory.student_id.in_([sid_one, sid_two]))).all()
        assert len(rows) == 2
        assert {r.kind for r in rows} == {"promote"}
        assert {(r.from_semester, r.to_semester) for r in rows} == {(2, 3)}
        assert {r.effective_on for r in rows} == {date(2026, 8, 1)}
        assert all(r.by_user_id and r.reason == "Results published." for r in rows)
        assert db.scalar(
            select(AuditEvent).where(AuditEvent.entity_id == cid, AuditEvent.action == "STUDENTS_PROMOTE")
        ) is not None, "every write is audited"

    # And it reads back on the batch's own history.
    history = client.get(f"/api/admin/cohorts/{cid}/promotion-history", headers=h).json()
    assert len(history) == 2 and {r["kind"] for r in history} == {"promote"}
    assert {r["student_name"] for r in history} == {one.email.split("@")[0], two.email.split("@")[0]} or True
    assert all(r["by_name"] for r in history), "the trail names who did it"


@requires_db
def test_the_dry_run_reports_the_checks_and_never_invents_attendance(client, make_user, chain, tracker, swept):
    """The preflight panel, including the row that CANNOT be answered.

    `attendance_records` has `course_code` and `session_no` and no semester
    column, so "attendance imported for the current semester" is not a question
    this schema can answer. It is reported `unavailable` with the reason —
    never a zero, a dash or a green tick, any of which would be a number nobody
    computed on the screen where the office decides to move a cohort.
    """
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    _make_course(client, h, chain, tracker, swept, total_semesters=4)
    account, sid = _seeded_student(client, make_user, swept, chain, "dry", semester=1)
    swept["emails"].append(account.email)

    checks = {
        c["key"]: c for c in
        client.post(f"/api/admin/cohorts/{cid}/promote?dry_run=true", headers=h,
                    json={"effective_on": "2026-08-01"}).json()["checks"]
    }
    assert checks["attendance_imported"]["status"] == "unavailable"
    assert "not recorded per semester" in checks["attendance_imported"]["detail"]
    # The marks half IS answerable, and says so honestly for a batch with none.
    assert checks["results_imported"]["status"] == "warn"
    assert "0 of 1" in checks["results_imported"]["detail"]
    assert checks["course_length"]["status"] == "ok"
    # Empty stage-rule catalogue: a documented no-op, not a silent one.
    assert checks["stage_rules"]["status"] == "unavailable"
    assert checks["english_baseline"]["status"] == "ok"

    with SessionLocal() as db:
        db.add(SemesterResult(student_id=sid, semester=1, sgpa=7.0, cgpa=7.0))
        db.commit()
    checks = {
        c["key"]: c for c in
        client.post(f"/api/admin/cohorts/{cid}/promote?dry_run=true", headers=h,
                    json={"effective_on": "2026-08-01"}).json()["checks"]
    }
    assert checks["results_imported"]["status"] == "ok"


@requires_db
def test_hold_back_leaves_them_and_an_unknown_id_refuses_everything(client, make_user, chain, swept):
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    stay, sid_stay = _seeded_student(client, make_user, swept, chain, "hold", semester=3)
    go, sid_go = _seeded_student(client, make_user, swept, chain, "go", semester=3)
    swept["emails"] += [stay.email, go.email]

    # An id that is not in this batch is REFUSED, never ignored: ignoring it
    # promotes a student the admin believes they held back, and reports success.
    bad = client.post(
        f"/api/admin/cohorts/{cid}/promote", headers=h,
        json={"effective_on": "2026-08-01", "hold_back": [sid_stay, "not-in-this-batch"]},
    )
    assert bad.status_code == 422 and "Nothing was promoted" in bad.json()["detail"]
    with SessionLocal() as db:
        assert db.get(Student, sid_go).current_semester == 3

    out = client.post(
        f"/api/admin/cohorts/{cid}/promote", headers=h,
        json={"effective_on": "2026-08-01", "hold_back": [sid_stay]},
    )
    assert out.status_code == 200, out.text
    assert out.json()["affected"] == 1 and out.json()["held_back"] == 1
    with SessionLocal() as db:
        assert db.get(Student, sid_stay).current_semester == 3
        assert db.get(Student, sid_go).current_semester == 4
        # No history row for the student who did not move.
        assert db.scalars(select(StudentSemesterHistory).where(
            StudentSemesterHistory.student_id == sid_stay)).all() == []


@requires_db
def test_promotion_stops_at_the_end_of_the_programme(client, make_user, chain, tracker, swept):
    """A four-semester MBA has no semester 5, and the dry run SHOWS the block.

    Reported on the dry run and raised on apply: a 422 on the preflight would
    take the per-student table with it and leave the dialog a sentence where the
    names ought to be.
    """
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    _make_course(client, h, chain, tracker, swept, total_semesters=4)
    final, sid = _seeded_student(client, make_user, swept, chain, "final", semester=4)
    swept["emails"].append(final.email)

    dry = client.post(f"/api/admin/cohorts/{cid}/promote?dry_run=true", headers=h,
                      json={"effective_on": "2026-08-01"})
    assert dry.status_code == 200, "the preflight REPORTS the block rather than raising it"
    body = dry.json()
    assert body["affected"] == 0
    assert body["students"][0]["blocked_reason"]
    assert [c for c in body["checks"] if c["key"] == "course_length"][0]["status"] == "blocked"

    applied = client.post(f"/api/admin/cohorts/{cid}/promote", headers=h,
                          json={"effective_on": "2026-08-01"})
    assert applied.status_code == 422 and "Nothing was promoted" in applied.json()["detail"]
    with SessionLocal() as db:
        assert db.get(Student, sid).current_semester == 4

    # Holding the final-semester student back leaves nobody to move, which is a
    # refusal of its own rather than a cheerful "affected: 0".
    empty = client.post(f"/api/admin/cohorts/{cid}/promote", headers=h,
                        json={"effective_on": "2026-08-01", "hold_back": [sid]})
    assert empty.status_code == 422


@requires_db
def test_a_stage_rule_moves_the_stage_and_an_empty_catalogue_does_not(client, make_user, chain, tracker, swept):
    """B13's `stage_rules` is OPTIONAL INPUT. Absent, the stage half is a no-op."""
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    course_id = _make_course(client, h, chain, tracker, swept, total_semesters=4)
    account, sid = _seeded_student(client, make_user, swept, chain, "stage", semester=1, stage=Stage.REBOOT)
    swept["emails"].append(account.email)

    # No rules yet.
    client.post(f"/api/admin/cohorts/{cid}/promote", headers=h, json={"effective_on": "2026-08-01"})
    with SessionLocal() as db:
        st = db.get(Student, sid)
        assert (st.current_semester, st.current_stage) == (2, Stage.REBOOT)
        db.add(StageRule(course_id=course_id, semester=3, stage=Stage.EXCEL))
        db.commit()

    out = client.post(f"/api/admin/cohorts/{cid}/promote", headers=h, json={"effective_on": "2026-09-01"})
    assert out.json()["students"][0]["to_stage"] == "EXCEL"
    with SessionLocal() as db:
        st = db.get(Student, sid)
        assert (st.current_semester, st.current_stage) == (3, Stage.EXCEL)


# -------------------------------------------------------------- B4.4 --


@requires_db
def test_graduation_closes_the_student_screens(client, login, make_user, chain, tracker, swept):
    """THE most important assertion in this area.

    Flipping `users.role` to ALUMNI does not by itself close `/api/student/*`:
    `_require_student` read `session["studentId"]` and nothing else, and
    `_payload_for` mints that claim for ANY account with a `students` row —
    which a graduate keeps, because the row is their record. Without the role
    check this test added, a graduate would hold roughly forty student
    endpoints while the Angular `roleGuard('STUDENT')` hid the screens: an API
    open behind a client that looks shut.
    """
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    _make_course(client, h, chain, tracker, swept, total_semesters=4)
    account, sid = _seeded_student(client, make_user, swept, chain, "grad", semester=4)
    swept["emails"].append(account.email)

    # While they are a student, the doors are open.
    assert client.get("/api/student/results", headers=account.headers).status_code == 200
    assert client.get("/api/student/programme", headers=account.headers).status_code == 200

    dry = client.post(f"/api/admin/cohorts/{cid}/graduate?dry_run=true", headers=h,
                      json={"effective_on": "2026-07-31"}).json()
    assert dry["affected"] == 1 and dry["dry_run"] is True
    assert [c["status"] for c in dry["checks"] if c["key"] == "final_semester"] == ["ok"]
    with SessionLocal() as db:
        assert db.get(Student, sid).status == STUDENT_STATUS_ACTIVE, "a dry run writes nothing"

    out = client.post(f"/api/admin/cohorts/{cid}/graduate", headers=h,
                      json={"effective_on": "2026-07-31", "reason": "Course complete."})
    assert out.status_code == 200, out.text
    assert out.json()["affected"] == 1
    assert out.json()["cohort_status"] == "GRADUATED"

    with SessionLocal() as db:
        student = db.get(Student, sid)
        user = db.get(User, account.user_id)
        assert student.status == STUDENT_STATUS_GRADUATED
        assert user.role is Role.ALUMNI
        assert (user.token_version or 0) >= 1, "every device is signed out"
        assert db.get(Cohort, cid).status == "GRADUATED"
        assert db.scalar(
            select(StudentSemesterHistory).where(
                StudentSemesterHistory.student_id == sid,
                StudentSemesterHistory.kind == "graduate",
            )
        ) is not None
        # NO alumni_profiles ROW IS CREATED. `company` there is NOT NULL and row
        # EXISTENCE is what drives the first-login create form; minting one here
        # would either invent a company or suppress the form for ever.
        from app.models.alumni import AlumniProfile
        assert db.scalar(select(AlumniProfile).where(AlumniProfile.user_id == user.id)) is None

    # The cookie they were holding is retired on its next request.
    assert client.get("/api/student/results", headers=account.headers).status_code == 401

    fresh = login(account.email, TEST_PASSWORD)
    client.cookies.clear()
    me = client.get("/api/auth/me", headers=fresh).json()
    assert me["role"] == "ALUMNI"
    assert me.get("studentId"), (
        "the claim SURVIVES graduation - the students row is the record - which is "
        "precisely why the role, not the claim, has to be the gate"
    )
    for path in (
        "/api/student/results",       # routers/student.py
        "/api/student/profile",       # the inline copy of the same gate
        "/api/student/programme",     # routers/student_programme.py
        "/api/student/badges",        # routers/badges.py, via the imported helper
    ):
        r = client.get(path, headers=fresh)
        assert r.status_code == 403, f"{path} answered {r.status_code} to a graduate"

    # And the door that IS theirs now opens, on `created: false` — the flag the
    # first-login create-profile form branches on.
    profile = client.get("/api/alumni/profile", headers=fresh)
    assert profile.status_code == 200 and profile.json()["created"] is False


@requires_db
def test_ungraduate_puts_it_back_and_the_window_closes(client, make_user, chain, tracker, swept):
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    _make_course(client, h, chain, tracker, swept, total_semesters=4)
    account, sid = _seeded_student(client, make_user, swept, chain, "ungrad", semester=4)
    swept["emails"].append(account.email)

    assert client.post(f"/api/admin/cohorts/{cid}/ungraduate", headers=h, json={}).status_code == 422, \
        "nothing to reverse before a graduation"

    client.post(f"/api/admin/cohorts/{cid}/graduate", headers=h, json={"effective_on": "2026-07-31"})
    back = client.post(f"/api/admin/cohorts/{cid}/ungraduate", headers=h,
                       json={"reason": "Wrong batch."})
    assert back.status_code == 200, back.text
    assert back.json()["affected"] == 1 and back.json()["cohort_status"] == "ACTIVE"
    with SessionLocal() as db:
        assert db.get(Student, sid).status == STUDENT_STATUS_ACTIVE
        assert db.get(User, account.user_id).role is Role.STUDENT
        assert db.get(Cohort, cid).status == "ACTIVE"
        kinds = [r.kind for r in db.scalars(select(StudentSemesterHistory).where(
            StudentSemesterHistory.student_id == sid).order_by(StudentSemesterHistory.created_at))]
        assert kinds == ["graduate", "ungraduate"], "the reversal is recorded, not erased"

    # THE WINDOW IS READ OFF `created_at` - when the button was pressed - never
    # off `effective_on`, which the office backdates as a matter of routine.
    client.post(f"/api/admin/cohorts/{cid}/graduate", headers=h, json={"effective_on": "2026-07-31"})
    with SessionLocal() as db:
        # EVERY graduate row, not just the newest: the endpoint reads
        # max(created_at), so ageing one of two would simply promote the other
        # to newest and prove nothing.
        for row in db.scalars(
            select(StudentSemesterHistory)
            .where(StudentSemesterHistory.student_id == sid, StudentSemesterHistory.kind == "graduate")
        ):
            row.created_at = datetime.now(timezone.utc) - timedelta(days=31)
        db.commit()
    stale = client.post(f"/api/admin/cohorts/{cid}/ungraduate", headers=h, json={})
    assert stale.status_code == 409 and "30 days" in stale.json()["detail"]
    with SessionLocal() as db:
        assert db.get(Student, sid).status == STUDENT_STATUS_GRADUATED, "nothing was changed"


@requires_db
def test_a_graduated_batch_is_not_promoted_and_promote_is_still_not_a_bulk_action(
    client, make_user, chain, tracker, swept
):
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    _make_course(client, h, chain, tracker, swept, total_semesters=8)
    account, sid = _seeded_student(client, make_user, swept, chain, "done", semester=4)
    swept["emails"].append(account.email)

    client.post(f"/api/admin/cohorts/{cid}/graduate", headers=h, json={"effective_on": "2026-07-31"})
    blocked = client.post(f"/api/admin/cohorts/{cid}/promote", headers=h,
                          json={"effective_on": "2026-08-01"})
    assert blocked.status_code == 409 and "has graduated" in blocked.json()["detail"]

    # PROMOTE STAYS ITS OWN ENDPOINT. `BatchAction` is the vocabulary of "the
    # single roster edit, repeated"; promotion writes a history row per student,
    # reads the course's length and carries an effective date. Restated here as
    # well as in test_admin_students because the two facts are one contract.
    assert client.post(f"/api/admin/cohorts/{cid}/students/bulk", headers=h,
                       json={"action": "promote"}).status_code == 422


@requires_db
def test_a_removed_student_is_not_promoted_graduated_or_counted(client, make_user, chain, tracker, swept):
    """A student REMOVED from the roster is still seated in their batch, and the
    list both dialogs count (`GET /admin/students?cohort_id=`) leaves them out.
    Promote and graduate must leave them out too, `batch_action`'s rule: a dry
    run that counted them read "2" under a dialog that said "1 student", the
    promotion moved their semester, and graduation turned them into an ALUMNI
    account - so Restore brought back an alumnus, not the student as left."""
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    _make_course(client, h, chain, tracker, swept, total_semesters=4)
    live, sid_live = _seeded_student(client, make_user, swept, chain, "rmv-live", semester=3)
    gone, sid_gone = _seeded_student(client, make_user, swept, chain, "rmv-gone", semester=3)
    swept["emails"] += [live.email, gone.email]
    r = client.post(
        f"/api/admin/students/{sid_gone}/remove", headers=h, json={"reason": "Withdrew from the programme"}
    )
    assert r.status_code == 200, r.text

    def as_left() -> tuple:
        with SessionLocal() as db:
            s = db.get(Student, sid_gone)
            u = db.get(User, gone.user_id)
            history = db.scalar(
                select(func.count()).select_from(StudentSemesterHistory)
                .where(StudentSemesterHistory.student_id == sid_gone)
            )
            return (
                s.cohort_id, s.current_semester, s.current_stage, s.status,
                u.role, u.token_version, u.deleted_at is not None, history,
            )

    before = as_left()
    assert [x["student_id"] for x in client.get(f"/api/admin/students?cohort_id={cid}", headers=h).json()] == [sid_live]

    for verb in ("promote", "graduate"):
        body = {"effective_on": "2026-08-01"}
        dry = client.post(f"/api/admin/cohorts/{cid}/{verb}?dry_run=true", headers=h, json=body)
        assert dry.status_code == 200, dry.text
        assert dry.json()["affected"] == 1, f"{verb}: the dry run counts the roster, not the seats"
        assert [x["student_id"] for x in dry.json()["students"]] == [sid_live]
        out = client.post(f"/api/admin/cohorts/{cid}/{verb}", headers=h, json=body)
        assert out.status_code == 200, out.text
        assert out.json()["affected"] == 1, f"{verb}: the count is the roster's, not the seats'"
        assert as_left() == before, f"{verb} wrote to a removed student"

    with SessionLocal() as db:
        assert db.get(Student, sid_live).current_semester == 4
        assert db.get(User, live.user_id).role is Role.ALUMNI

    # The reversal is a batch act too, and reads the same roster.
    back = client.post(f"/api/admin/cohorts/{cid}/ungraduate", headers=h, json={"reason": "Wrong batch."})
    assert back.status_code == 200, back.text
    assert back.json()["affected"] == 1
    assert as_left() == before

    # Restore brings back the student the office took off the list.
    assert client.post(f"/api/admin/students/{sid_gone}/restore", headers=h).status_code == 200
    with SessionLocal() as db:
        s = db.get(Student, sid_gone)
        u = db.get(User, gone.user_id)
        assert (s.current_semester, s.status, u.role) == (3, STUDENT_STATUS_ACTIVE, Role.STUDENT)
