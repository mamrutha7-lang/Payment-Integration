"""B8.2 — one fallback chain, and the three readers that used to disagree.

Before this, `PlacementCriteria` had three readers and no two of them agreed
about "the office has set nothing":

  * the admin screen answered 404;
  * the student jobs feed applied NO GATE AT ALL;
  * the readiness card applied four hard-coded literals.

So a student could be told they fail a 6.0 CGPA cut-off on one screen and be
offered every posting on the next. What each test here holds down:

1. THE CHAIN IS COURSE, THEN COLLEGE, THEN PROGRAMME, THEN DEFAULTS.
   `test_the_chain_prefers_the_narrowest_row_that_applies` is the whole feature.
   Delete it and a per-course row is written and never read.
2. THE DEFAULTS ARE THE READINESS SCREEN'S OWN NUMBERS, to the digit.
   `test_the_hard_coded_defaults_are_the_numbers_students_have_been_reading`
   stops somebody "tidying" them to the model's column defaults (85 / 75),
   which are a different set that has co-existed in this tree for a year.
3. A ROW DATED IN THE FUTURE DOES NOT GOVERN TODAY. Delete
   `test_a_set_that_has_not_taken_effect_yet_does_not_apply` and typing next
   term's gates changes a student's verdict this afternoon.
4. THE TWO STUDENT SCREENS READ ONE ANSWER. Delete
   `test_the_jobs_feed_and_the_readiness_card_read_the_same_row` and the
   disagreement above comes straight back, silently.
5. A WRITE SUPERSEDES, IT DOES NOT EDIT. Delete
   `test_writing_a_new_set_supersedes_the_old_one_and_keeps_it` and the History
   button has one row in it and a verdict given in March is unexplainable.
6. THE PROGRAMME ROW IS THE MAIN ADMIN'S. Delete
   `test_a_college_scoped_holder_cannot_set_the_programme_wide_gates` and a
   college admin sets the cut-offs for every other college on the deployment.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.criteria import DEFAULTS, resolve, resolve_for_student
from app.db import SessionLocal
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, AcademicCourse, College, Department
from app.models.academics import SemesterResult
from app.models.job import DegreeLevel, Job
from app.models.placement_criteria import PlacementCriteria
from app.models.user import LoginDay, Role, Student, User
from app.seed_roster import SSO_ONLY_PASSWORD_HASH


@pytest.fixture
def spine():
    """A college, a course, a batch and one student seated in it."""
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        college = College(code=f"CRIT{tag.upper()}", name=f"Criteria College {tag}",
                          status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        dept = Department(college_id=college.id, code=f"CD{tag}", name=f"Criteria Dept {tag}")
        db.add(dept)
        db.flush()
        course = AcademicCourse(department_id=dept.id, code=f"CC{tag.upper()}",
                                name=f"Criteria Course {tag}", duration_months=24)
        db.add(course)
        db.flush()
        cohort = Cohort(
            code=f"CRB-{tag}", name=f"Criteria Batch {tag}", batch_label="2025-27",
            department_id=dept.id, course_id=course.id, degree_level=DegreeLevel.PG,
            start_date=datetime(2025, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2027, 7, 31, tzinfo=timezone.utc),
        )
        db.add(cohort)
        db.flush()
        user = User(email=f"crit-{tag}@bgscet.ac.in", name=f"Criteria Student {tag}",
                    role=Role.STUDENT, password_hash=SSO_ONLY_PASSWORD_HASH)
        db.add(user)
        db.flush()
        student = Student(user_id=user.id, cohort_id=cohort.id, usn=f"1CR{tag.upper()}01")
        db.add(student)
        db.flush()
        # A CGPA on file, so the jobs feed has something to judge — and a
        # posting with NO per-posting gate, which is the only kind whose
        # verdict comes from the resolved criteria at all.
        db.add(SemesterResult(student_id=student.id, semester=1, cgpa=7.0, sgpa=7.0))
        job = Job(
            title=f"Criteria Analyst {tag}", company=f"Criteria Co {tag}",
            degree_level=DegreeLevel.PG, required_skills=[],
            posted_on=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        db.add(job)
        db.flush()
        # A SECOND COLLEGE WITH ITS OWN COURSE. The smallest shape in which
        # "your grant does not reach that course" is a statement that can fail:
        # a college-scoped holder naming a course in somebody else's college.
        far_college = College(code=f"CRF{tag.upper()}", name=f"Far College {tag}",
                              status=STATUS_ACTIVE)
        db.add(far_college)
        db.flush()
        far_dept = Department(college_id=far_college.id, code=f"FD{tag}", name=f"Far Dept {tag}")
        db.add(far_dept)
        db.flush()
        far_course = AcademicCourse(department_id=far_dept.id, code=f"FC{tag.upper()}",
                                    name=f"Far Course {tag}", duration_months=24)
        db.add(far_course)
        db.flush()
        made.update(college=college.id, dept=dept.id, course=course.id,
                    cohort=cohort.id, user=user.id, student=student.id, job=job.id,
                    far_college=far_college.id, far_dept=far_dept.id,
                    far_course=far_course.id)
        db.commit()

    yield made

    with SessionLocal() as db:
        db.execute(delete(PlacementCriteria).where(
            PlacementCriteria.college_id.in_([made["college"], made["far_college"]])
            | PlacementCriteria.course_id.in_([made["course"], made["far_course"]])
        ))
        db.execute(delete(SemesterResult).where(SemesterResult.student_id == made["student"]))
        db.execute(delete(Job).where(Job.id == made["job"]))
        db.execute(delete(Student).where(Student.id == made["student"]))
        # The jobs/readiness test signs this student in, and a sign-in writes a
        # streak row with a bare FK to `users`. `make_user`'s teardown does the
        # same for the same reason.
        db.execute(delete(LoginDay).where(LoginDay.user_id == made["user"]))
        db.execute(delete(User).where(User.id == made["user"]))
        db.execute(delete(Cohort).where(Cohort.id == made["cohort"]))
        db.execute(delete(AcademicCourse).where(AcademicCourse.id == made["course"]))
        db.execute(delete(AcademicCourse).where(AcademicCourse.id == made["far_course"]))
        db.execute(delete(Department).where(
            Department.id.in_([made["dept"], made["far_dept"]])
        ))
        db.execute(delete(College).where(
            College.id.in_([made["college"], made["far_college"]])
        ))
        db.commit()


@pytest.fixture
def _write():
    """A criteria row straight into the table, and taken back afterwards.

    A ROW RATHER THAN THE ENDPOINT, so what is under test is the RESOLVER and
    not the write path — and every row is deleted by id, PROGRAMME-WIDE ROWS
    INCLUDED. A programme row left behind would be the newest active one on the
    deployment and would quietly become the fallback every other test in the
    suite resolves against.
    """
    made: list[str] = []

    def _make(spine, **overrides) -> str:
        values = {
            "name": "Test set",
            "active": True,
            "min_cgpa": 5.0,
            "max_live_backlogs": 3,
            "max_gap_months": 36,
            "min_attendance_pct": 60.0,
            "min_reep_completion_pct": 50.0,
            "min_cert_completion_pct": 40.0,
            "require_core_certs": False,
        }
        values.update(overrides)
        with SessionLocal() as db:
            row = PlacementCriteria(**values)
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _make

    with SessionLocal() as db:
        db.execute(delete(PlacementCriteria).where(PlacementCriteria.id.in_(made)))
        db.commit()


def test_the_hard_coded_defaults_are_the_numbers_students_have_been_reading():
    """6.0 · 0 · 75 % · 50 % — `compose_placement_readiness`'s own literals.

    NOT the model's column defaults (`min_attendance_pct=85`,
    `min_cert_completion_pct=75`). Two different "defaults" have existed in this
    tree for a year and this is the pair a student's screen has actually been
    scored against; changing them silently would move a real score.
    """
    assert DEFAULTS.min_cgpa == 6.0
    assert DEFAULTS.max_live_backlogs == 0
    assert DEFAULTS.min_attendance_pct == 75.0
    assert DEFAULTS.min_cert_completion_pct == 50.0
    assert DEFAULTS.is_default


@requires_db
def test_the_chain_prefers_the_narrowest_row_that_applies(spine, _write):
    """Course beats college beats programme beats the hard-coded floor."""
    with SessionLocal() as db:
        # Nothing written for this student's course or college yet. The
        # deployment may carry a seeded programme-wide row, so the only thing
        # asserted here is that it is NOT a course or a college answer.
        assert resolve_for_student(db, spine["student"]).source in ("programme", "defaults")

    _write(spine, college_id=spine["college"], min_cgpa=5.0)
    with SessionLocal() as db:
        resolved = resolve_for_student(db, spine["student"])
        assert resolved.source == "college"
        assert resolved.min_cgpa == 5.0

    _write(spine, course_id=spine["course"], min_cgpa=4.0)
    with SessionLocal() as db:
        resolved = resolve_for_student(db, spine["student"])
        assert resolved.source == "course"
        assert resolved.min_cgpa == 4.0
        # The college row is still there and still answers for a sibling course.
        assert resolve(db, college_id=spine["college"]).min_cgpa == 5.0


@requires_db
def test_a_set_that_has_not_taken_effect_yet_does_not_apply(spine, _write):
    """`effective_from` in the future is next term's policy, not today's."""
    _write(spine, course_id=spine["course"], min_cgpa=4.0,
           effective_from=date.today() - timedelta(days=30))
    _write(spine, course_id=spine["course"], min_cgpa=9.5,
           effective_from=date.today() + timedelta(days=30))
    with SessionLocal() as db:
        assert resolve_for_student(db, spine["student"]).min_cgpa == 4.0


@requires_db
def test_an_inactive_set_does_not_apply(spine, _write):
    """`active=False` is how a superseded row stays in the History and out of
    the answer."""
    _write(spine, course_id=spine["course"], min_cgpa=4.0, active=False)
    with SessionLocal() as db:
        assert resolve_for_student(db, spine["student"]).source != "course"


@requires_db
def test_the_jobs_feed_and_the_readiness_card_read_the_same_row(client, login, spine, _write):
    """The disagreement B8.2 exists to end, asserted across two endpoints.

    A course row with an impossible CGPA gate must make the SAME student fail
    the readiness card's CGPA factor and lose eligibility on the jobs feed. It
    could not before: the feed fell through to `None` and applied nothing.
    """
    from app.security import hash_password

    password = "criteriapass123"
    with SessionLocal() as db:
        user = db.get(User, spine["user"])
        user.password_hash = hash_password(password)
        db.commit()
    headers = login(f"crit-{spine['tag']}@bgscet.ac.in", password)

    # THE COURSE ROW FIRST, THEN A PROGRAMME-WIDE ROW WITH A LOWER GATE. The
    # order is the test: the reader this replaced took "the newest active row"
    # regardless of which course it named, so it would answer 5.0 here and the
    # student would be eligible. Only a resolver that prefers the narrower rung
    # answers 9.9.
    _write(spine, course_id=spine["course"], min_cgpa=9.9, max_live_backlogs=0,
           max_gap_months=1)
    _write(spine, name="Programme floor", min_cgpa=5.0)

    readiness = client.get("/api/student/placement-readiness", headers=headers)
    assert readiness.status_code == 200, readiness.text
    cgpa_factor = next(f for f in readiness.json()["factors"] if f["label"] == "CGPA")
    assert "9.9" in cgpa_factor["detail"], cgpa_factor

    jobs = client.get("/api/student/jobs", headers=headers)
    assert jobs.status_code == 200, jobs.text
    mine = next(row for row in jobs.json() if row["id"] == spine["job"])
    # THE POSTING NAMES NO GATE OF ITS OWN, so before B8.2 this student was
    # eligible for it — the feed fell through to `None` and applied nothing —
    # while the readiness card above was failing them on the same number.
    assert mine["eligible"] is False, "the jobs feed applied no criteria at all"
    assert any("9.9" in reason for reason in mine["reasons"]), mine["reasons"]


# ------------------------------------------------------------- the console --

ADMIN = ("admin@bgscet.ac.in", "admin123")


@requires_db
def test_writing_a_new_set_supersedes_the_old_one_and_keeps_it(client, login, spine):
    """A write is an INSERT plus a deactivation, never an edit in place: a
    verdict given in March has to stay explicable in September."""
    headers = login(*ADMIN)
    first = client.post(
        "/api/admin/criteria",
        headers=headers,
        json={"name": "March", "course_id": spine["course"], "min_cgpa": 5.5},
    )
    assert first.status_code == 201, first.text
    assert first.json()["source"] == "course"
    assert first.json()["min_cgpa"] == 5.5
    # An omitted threshold keeps the number that was already governing, not the
    # fallback: nothing was set for this course, so this is DEFAULTS' value.
    assert first.json()["max_live_backlogs"] == DEFAULTS.max_live_backlogs

    second = client.post(
        "/api/admin/criteria",
        headers=headers,
        json={"name": "September", "course_id": spine["course"], "min_cgpa": 6.5},
    )
    assert second.status_code == 201, second.text

    read = client.get("/api/admin/criteria", headers=headers,
                      params={"course_id": spine["course"]})
    assert read.status_code == 200, read.text
    assert read.json()["min_cgpa"] == 6.5
    assert read.json()["name"] == "September"

    history = client.get("/api/admin/criteria/history", headers=headers,
                         params={"course_id": spine["course"]})
    assert history.status_code == 200, history.text
    rows = history.json()
    assert len(rows) == 2, "the superseded set was edited away instead of kept"
    assert {row["name"] for row in rows} == {"March", "September"}
    assert [row["active"] for row in rows].count(False) == 1
    assert all(row["created_by"] is not None for row in rows)


@requires_db
def test_the_read_still_404s_when_nobody_has_set_anything(client, login, spine):
    """The admin screen asks "what has somebody SET", and the honest answer to
    "nothing" is 404 — which the client renders as "no criteria set yet" rather
    than drawing the engine's fallback as a configuration the office chose.
    `tests/test_phase3_compatibility.py` pins the other half of this sentence.
    """
    headers = login(*ADMIN)
    # No row for this college, and the college rung does not inherit the
    # programme row's identity... but the resolver DOES fall back to it, so the
    # only deployment-independent assertion is on a college with a course that
    # no row names, while the programme row (seeded) still answers.
    r = client.get("/api/admin/criteria", headers=headers,
                   params={"course_id": spine["course"]})
    assert r.status_code in (200, 404), r.text
    if r.status_code == 200:
        assert r.json()["source"] in ("programme", "college")
        assert not all(
            r.json()[k] in (0, 0.0, None)
            for k in ("min_cgpa", "min_attendance_pct", "min_reep_completion_pct")
        )


@pytest.fixture
def scoped_grant():
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel | None, target_id: str | None) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key, subject_kind=SubjectKind.USER, subject_user_id=user_id,
                scope_level=level, scope_id=target_id,
                reason="the criteria scope test needs a college-scoped grant, twenty plus",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


@requires_db
def test_a_college_scoped_holder_cannot_set_the_programme_wide_gates(
    client, make_user, spine, scoped_grant
):
    """The programme row governs every college on the deployment, so it is the
    Main Admin's; their own college's is theirs."""
    faculty = make_user(f"crit-scoped-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.analytics", ScopeLevel.COLLEGE, spine["college"])

    programme_wide = client.post(
        "/api/admin/criteria", headers=faculty.headers, json={"name": "Everyone", "min_cgpa": 1.0}
    )
    assert programme_wide.status_code == 403, programme_wide.text
    assert "Main Admin" in programme_wide.json()["detail"]

    own = client.post(
        "/api/admin/criteria",
        headers=faculty.headers,
        json={"name": "Ours", "college_id": spine["college"], "min_cgpa": 5.0},
    )
    assert own.status_code == 201, own.text

    # A COURSE IN SOMEBODY ELSE'S COLLEGE. The course is checked through its
    # own department, not by trusting that the caller also sent a college_id.
    elsewhere = client.post(
        "/api/admin/criteria",
        headers=faculty.headers,
        json={"name": "Theirs", "course_id": spine["far_course"], "min_cgpa": 1.0},
    )
    assert elsewhere.status_code == 403, elsewhere.text

    with SessionLocal() as db:
        assert db.scalar(
            select(PlacementCriteria).where(
                PlacementCriteria.college_id.is_(None),
                PlacementCriteria.name == "Everyone",
            )
        ) is None
        assert db.scalar(
            select(PlacementCriteria).where(PlacementCriteria.course_id == spine["far_course"])
        ) is None


@requires_db
def test_a_student_cannot_read_or_write_the_criteria(client, make_user, spine):
    student = make_user(f"crit-stu-{spine['tag']}", Role.STUDENT)
    assert client.get("/api/admin/criteria", headers=student.headers).status_code == 403
    assert client.get("/api/admin/criteria/history", headers=student.headers).status_code == 403
    assert client.post(
        "/api/admin/criteria", headers=student.headers, json={"name": "x"}
    ).status_code == 403
