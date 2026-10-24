"""B12.1 / B12.2 — a posting knows where it is offered, and whether it still is.

What each of these holds down, and what comes back if it is deleted:

1. A POSTING IS NOT PUBLISHED TO THE WHOLE WORLD ANY MORE. Delete
   `test_a_posting_is_narrowed_to_its_college_course_and_track` and every
   student on the deployment sees every posting again — an MBA cohort's campus
   drive on a B.Tech fresher's feed, which is what `GET /api/student/jobs`
   answered before B12.1 (`select(Job).order_by(posted_on)`, no filter of any
   kind).
2. NULL STILL MEANS EVERYBODY. Delete
   `test_a_posting_that_names_nothing_reaches_everyone` and the obvious
   "tighten it up" edit — NULL college matches nobody — empties every jobs board
   in the product on the deploy that lands it, because every posting written
   before B12.1 has NULLs on all three. The screen looks like a quiet week, not
   like a bug.
3. AN UNFILED STUDENT IS NOT FENCED OUT. Delete
   `test_a_student_with_no_batch_still_sees_the_board` and the hundreds of
   students at a college that has not built its batches yet get an empty
   opportunities screen.
4. THE BUTTON OBEYS THE LIST. Delete `test_applying_obeys_the_same_fence` and
   the narrowing is decoration: the id of a posting for another college is
   guessable and the id of a posting that closed yesterday is in the student's
   own browser.
5. CLOSING IS NOT DELETING. Delete `test_closing_takes_a_posting_off_both_boards`
   and `status` is a column nothing reads; delete
   `test_close_is_idempotent_and_refuses_a_student` and the toolbar's double-tap
   writes a second audit event for one decision, and a STUDENT can withdraw the
   college's postings.
6. THE SHEET IS NARROWED TOO. Delete `test_the_sheet_is_scoped_and_says_so` and
   `admin.jobs` becomes the one console list B1.4 does not reach — which is the
   state B12.1 exists to end.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update

from conftest import requires_db

from app.db import SessionLocal
from app.jobs_visibility import STATUS_CLOSED, STATUS_OPEN
from app.models.alumni import AlumniProfile
from app.models.redesign import AuditEvent
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import (
    STATUS_ACTIVE,
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from app.models.job import DegreeLevel, Job, JobApplication
from app.models.user import Role, Student, User


@pytest.fixture
def two_colleges():
    """Two colleges, each with a department, a course and two specializations.

    The smallest shape in which all three of B12.1's predicates can fail
    independently: college needs two colleges, course needs two courses, and
    track needs two specializations under ONE course — otherwise a track test
    passes on the course filter alone and nobody notices the track predicate was
    never written.
    """
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        here = College(code=f"JH{tag.upper()}", name="Jobs Here", status=STATUS_ACTIVE)
        away = College(code=f"JA{tag.upper()}", name="Jobs Away", status=STATUS_ACTIVE)
        db.add_all([here, away])
        db.flush()
        d_here = Department(college_id=here.id, name="Here Dept", code=f"JH{tag}")
        d_away = Department(college_id=away.id, name="Away Dept", code=f"JA{tag}")
        db.add_all([d_here, d_away])
        db.flush()
        c_here = AcademicCourse(department_id=d_here.id, code=f"MB{tag}", name="MBA Here")
        c_away = AcademicCourse(department_id=d_away.id, code=f"MA{tag}", name="MBA Away")
        db.add_all([c_here, c_away])
        db.flush()
        fin = AcademicSpecialization(course_id=c_here.id, code="FIN", name="Finance")
        mkt = AcademicSpecialization(course_id=c_here.id, code="MKT", name="Marketing")
        db.add_all([fin, mkt])
        db.flush()

        def batch(code: str, department, course, specialization) -> Cohort:
            return Cohort(
                code=f"{code}-{tag}", name=f"Batch {code}", batch_label="2026-28",
                degree_level=DegreeLevel.PG, department_id=department.id,
                course_id=course.id,
                specialization_id=specialization.id if specialization else None,
                start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
            )

        b_fin = batch("FIN", d_here, c_here, fin)
        b_mkt = batch("MKT", d_here, c_here, mkt)
        b_away = batch("AWY", d_away, c_away, None)
        db.add_all([b_fin, b_mkt, b_away])
        db.commit()
        made |= {
            "college_here": here.id, "college_away": away.id,
            "dept_here": d_here.id, "dept_away": d_away.id,
            "course_here": c_here.id, "course_away": c_away.id,
            "spec_fin": fin.id, "spec_mkt": mkt.id,
            "batch_fin": b_fin.id, "batch_mkt": b_mkt.id, "batch_away": b_away.id,
        }

    yield made

    with SessionLocal() as db:
        batches = [made["batch_fin"], made["batch_mkt"], made["batch_away"]]
        # UNSEAT FIRST. `make_user` is set up before this fixture and therefore
        # torn down AFTER it, so its students are still pointing at these
        # batches when this runs — and `students.cohort_id` has no ON DELETE.
        # Without this line the failure is a ForeignKeyViolation inside a
        # fixture, which reads as a broken test rather than a seating order.
        db.execute(
            update(Student).where(Student.cohort_id.in_(batches)).values(cohort_id=None)
        )
        db.execute(delete(Cohort).where(Cohort.id.in_(batches)))
        db.execute(
            delete(AcademicSpecialization).where(
                AcademicSpecialization.id.in_([made["spec_fin"], made["spec_mkt"]])
            )
        )
        db.execute(
            delete(AcademicCourse).where(
                AcademicCourse.id.in_([made["course_here"], made["course_away"]])
            )
        )
        db.execute(
            delete(Department).where(
                Department.id.in_([made["dept_here"], made["dept_away"]])
            )
        )
        db.execute(
            delete(College).where(
                College.id.in_([made["college_here"], made["college_away"]])
            )
        )
        db.commit()


@pytest.fixture
def postings():
    """Publish postings straight to the table and take them away again."""
    made: list[str] = []

    def _post(**kwargs) -> str:
        with SessionLocal() as db:
            job = Job(
                title=kwargs.pop("title", "Analyst"),
                company=kwargs.pop("company", "Test Recruiter"),
                degree_level=DegreeLevel.PG,
                posted_on=datetime.now(timezone.utc) - timedelta(minutes=len(made)),
                **kwargs,
            )
            db.add(job)
            db.commit()
            made.append(job.id)
            return job.id

    yield _post

    with SessionLocal() as db:
        db.execute(delete(JobApplication).where(JobApplication.job_id.in_(made)))
        db.execute(delete(Job).where(Job.id.in_(made)))
        db.commit()


def _seat(student_id: str, cohort_id: str | None) -> None:
    with SessionLocal() as db:
        db.get(Student, student_id).cohort_id = cohort_id
        db.commit()


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


def _feed_ids(client, headers) -> set[str]:
    r = client.get("/api/student/jobs", headers=headers)
    assert r.status_code == 200, r.text
    return {row["id"] for row in r.json()}


# --------------------------------------------------------------------- B12.1


@requires_db
def test_a_posting_is_narrowed_to_its_college_course_and_track(
    client, make_user, two_colleges, postings
):
    spine = two_colleges
    fin_student = make_user("jobs-fin", Role.STUDENT)
    mkt_student = make_user("jobs-mkt", Role.STUDENT)
    away_student = make_user("jobs-away", Role.STUDENT)
    _seat(_student_id(fin_student.user_id), spine["batch_fin"])
    _seat(_student_id(mkt_student.user_id), spine["batch_mkt"])
    _seat(_student_id(away_student.user_id), spine["batch_away"])

    by_college = postings(title="Here only", college_id=spine["college_here"])
    by_course = postings(title="Course only", course_id=spine["course_here"])
    by_track = postings(title="Finance only", tracks=["FIN"])

    here_fin = _feed_ids(client, fin_student.headers)
    here_mkt = _feed_ids(client, mkt_student.headers)
    away = _feed_ids(client, away_student.headers)

    assert by_college in here_fin and by_college in here_mkt
    assert by_college not in away

    assert by_course in here_fin and by_course in here_mkt
    assert by_course not in away

    # The track predicate is the one that has to separate two students in the
    # SAME college and the SAME course, which is why the fixture has two
    # specializations under one course.
    assert by_track in here_fin
    assert by_track not in here_mkt


@requires_db
def test_a_posting_that_names_nothing_reaches_everyone(
    client, make_user, two_colleges, postings
):
    spine = two_colleges
    fin_student = make_user("jobs-any-fin", Role.STUDENT)
    away_student = make_user("jobs-any-away", Role.STUDENT)
    _seat(_student_id(fin_student.user_id), spine["batch_fin"])
    _seat(_student_id(away_student.user_id), spine["batch_away"])

    # Exactly the shape every posting written before B12.1 has.
    everyones = postings(title="Open to all")
    assert everyones in _feed_ids(client, fin_student.headers)
    assert everyones in _feed_ids(client, away_student.headers)


@requires_db
def test_a_student_with_no_batch_still_sees_the_board(client, make_user, two_colleges, postings):
    spine = two_colleges
    unseated = make_user("jobs-unseated", Role.STUDENT)
    _seat(_student_id(unseated.user_id), None)

    everyones = postings(title="Open to all")
    targeted = postings(title="Here only", college_id=spine["college_here"], tracks=["FIN"])
    feed = _feed_ids(client, unseated.headers)
    # Nothing about them narrows anything, so nothing is narrowed: an attribute
    # the viewer does not have must not filter, or the students at a college
    # that has not built its batches read an empty screen.
    assert {everyones, targeted} <= feed


@requires_db
def test_applying_obeys_the_same_fence(client, make_user, two_colleges, postings):
    spine = two_colleges
    away_student = make_user("jobs-apply", Role.STUDENT)
    _seat(_student_id(away_student.user_id), spine["batch_away"])

    elsewhere = postings(title="Here only", college_id=spine["college_here"])
    closed = postings(title="Withdrawn", status=STATUS_CLOSED)
    mine = postings(title="Open to all")

    for job_id in (elsewhere, closed):
        r = client.post(f"/api/student/jobs/{job_id}/apply", headers=away_student.headers, json={})
        assert r.status_code == 404, r.text
    assert (
        client.post(f"/api/student/jobs/{mine}/apply", headers=away_student.headers, json={}).status_code
        == 200
    )


# --------------------------------------------------------------------- B12.2


@requires_db
def test_closing_takes_a_posting_off_both_boards(client, make_user, postings):
    student = make_user("jobs-closed-stud", Role.STUDENT)
    alumnus = make_user("jobs-closed-alum", Role.ALUMNI)
    open_job = postings(title="Still hiring")
    closed_job = postings(title="Withdrawn", status=STATUS_CLOSED)

    feed = _feed_ids(client, student.headers)
    assert open_job in feed and closed_job not in feed

    board = {row["id"] for row in client.get("/api/alumni/jobs", headers=alumnus.headers).json()}
    assert open_job in board and closed_job not in board


@requires_db
def test_close_is_idempotent_and_refuses_a_student(client, make_user, postings):
    admin = make_user("jobs-close-admin", Role.ADMIN)
    student = make_user("jobs-close-stud", Role.STUDENT)
    job_id = postings(title="To be withdrawn")

    assert (
        client.post(f"/api/admin/jobs/{job_id}/close", headers=student.headers).status_code == 403
    )

    first = client.post(f"/api/admin/jobs/{job_id}/close", headers=admin.headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == STATUS_CLOSED

    second = client.post(f"/api/admin/jobs/{job_id}/close", headers=admin.headers)
    assert second.status_code == 200
    assert second.json()["status"] == STATUS_CLOSED

    with SessionLocal() as db:
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "job", AuditEvent.entity_id == job_id
            )
        ).all()
    # One decision, one row. A toolbar button over a grid selection gets
    # double-tapped, and a second receipt for the same withdrawal is noise in
    # the one table that exists to be read.
    assert len(events) == 1

    assert client.post(f"/api/admin/jobs/{uuid.uuid4().hex}/close", headers=admin.headers).status_code == 404


@requires_db
def test_a_closed_posting_still_refuses_to_be_deleted_once_applied(client, make_user, postings):
    """Closing and deleting answer different questions and both still hold."""
    admin = make_user("jobs-close-del", Role.ADMIN)
    student = make_user("jobs-close-del-stud", Role.STUDENT)
    job_id = postings(title="Applied to")
    with SessionLocal() as db:
        db.add(JobApplication(student_id=_student_id(student.user_id), job_id=job_id))
        db.commit()

    assert client.post(f"/api/admin/jobs/{job_id}/close", headers=admin.headers).status_code == 200
    refused = client.delete(f"/api/admin/jobs/{job_id}", headers=admin.headers)
    assert refused.status_code == 409
    assert "applied" in refused.json()["detail"]


# ------------------------------------------------------------- the admin side


@requires_db
def test_the_sheet_is_scoped_and_says_so(client, make_user, two_colleges, postings):
    spine = two_colleges
    faculty = make_user("jobs-sheet-mentor", Role.MENTOR)
    admin = make_user("jobs-sheet-admin", Role.ADMIN)
    here = postings(title="Here only", college_id=spine["college_here"])
    away = postings(title="Away only", college_id=spine["college_away"])
    everyones = postings(title="Open to all")

    grant_id = None
    try:
        with SessionLocal() as db:
            grant = CapabilityGrant(
                capability="admin.jobs", subject_kind=SubjectKind.USER,
                subject_user_id=faculty.user_id,
                scope_level=ScopeLevel.COLLEGE, scope_id=spine["college_here"],
                reason="the jobs-scope test needs a college-scoped grant, twenty plus",
            )
            db.add(grant)
            db.commit()
            grant_id = grant.id

        r = client.get("/api/admin/jobs", headers=faculty.headers)
        assert r.status_code == 200, r.text
        assert r.headers["X-Reep-Scope"] == "narrowed"
        seen = {row["id"] for row in r.json()}
        assert here in seen
        # Programme-wide postings are visible to a narrowed reader — the
        # deliberate opposite of the import-run rule, see job_scope_clause.
        assert everyones in seen
        assert away not in seen

        wide = client.get("/api/admin/jobs", headers=admin.headers)
        assert wide.headers["X-Reep-Scope"] == "programme"
        assert {here, away, everyones} <= {row["id"] for row in wide.json()}
        row = next(r for r in wide.json() if r["id"] == here)
        assert row["college_id"] == spine["college_here"]
        assert row["college_name"] == "Jobs Here"
        assert row["status"] == STATUS_OPEN

        # The button obeys the list on this side too.
        assert (
            client.post(f"/api/admin/jobs/{away}/close", headers=faculty.headers).status_code == 404
        )
    finally:
        if grant_id:
            with SessionLocal() as db:
                db.execute(delete(CapabilityGrant).where(CapabilityGrant.id == grant_id))
                db.commit()


@requires_db
def test_publishing_validates_the_rung_and_normalises_the_track(client, make_user, two_colleges):
    spine = two_colleges
    admin = make_user("jobs-publish", Role.ADMIN)

    refused = client.post(
        "/api/admin/jobs",
        headers=admin.headers,
        json={"title": "Analyst", "company": "Acme", "college_id": uuid.uuid4().hex},
    )
    assert refused.status_code == 422
    assert "college" in refused.json()["detail"].lower()

    refused = client.post(
        "/api/admin/jobs",
        headers=admin.headers,
        json={"title": "Analyst", "company": "Acme", "course_id": uuid.uuid4().hex},
    )
    assert refused.status_code == 422

    r = client.post(
        "/api/admin/jobs",
        headers=admin.headers,
        json={
            "title": "Analyst",
            "company": "Acme",
            "college_id": spine["college_here"],
            "course_id": spine["course_here"],
            "tracks": [" fin ", "FIN", "mkt", ""],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    try:
        # Upper-cased, de-duplicated, blanks dropped — normalised at the one
        # writer so the feeds compare a plain equality.
        assert body["tracks"] == ["FIN", "MKT"]
        assert body["status"] == STATUS_OPEN
        assert body["course_name"] == "MBA Here"
    finally:
        with SessionLocal() as db:
            db.execute(delete(Job).where(Job.id == body["id"]))
            db.commit()


# ------------------------------------------------------------------- alumni


@requires_db
def test_an_alumnus_is_narrowed_only_when_their_profile_names_a_student(
    client, make_user, two_colleges, postings
):
    spine = two_colleges
    unlinked = make_user("jobs-alum-free", Role.ALUMNI)
    linked = make_user("jobs-alum-linked", Role.ALUMNI)
    graduate = make_user("jobs-alum-grad", Role.STUDENT)
    student_id = _student_id(graduate.user_id)
    _seat(student_id, spine["batch_away"])

    here = postings(title="Here only", college_id=spine["college_here"])
    everyones = postings(title="Open to all")

    def board(headers) -> set[str]:
        r = client.get("/api/alumni/jobs", headers=headers)
        assert r.status_code == 200, r.text
        return {row["id"] for row in r.json()}

    # No profile at all: the whole open board, which is what this screen has
    # always shown them. Emptying it would be the worst reading of B12.1.
    assert {here, everyones} <= board(unlinked.headers)

    profile_id = None
    try:
        with SessionLocal() as db:
            profile = AlumniProfile(
                user_id=linked.user_id, student_id=student_id, company="Somewhere Ltd"
            )
            db.add(profile)
            db.commit()
            profile_id = profile.id
        seen = board(linked.headers)
        assert everyones in seen
        assert here not in seen
    finally:
        if profile_id:
            with SessionLocal() as db:
                db.execute(delete(AlumniProfile).where(AlumniProfile.id == profile_id))
                db.commit()
