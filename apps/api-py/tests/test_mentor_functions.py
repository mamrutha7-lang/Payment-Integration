"""B2.3 — a faculty account is not a mentor by existing.

`ROLE_BASELINE["MENTOR"]` handed every faculty account every SCOPED capability,
so "is this person staff" and "may this person read a mentee's ledger" were one
question. The ones that belong to a mentor GROUP are derived from the mentee
count now; the two that belong to the PERSON stay in the baseline. There were
four; leave approval left on 2026-09-16 and evidence verification on
2026-10-16, both to become grants the office makes about a named person.

The guard the spec asks for is the first test: no mentees, none of them;
with a mentee, all of them. The rest hold down the things that would make that
true in the test and false in production.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.governance import ROLE_BASELINE, capabilities_for, require_capability
from app.mentor_functions import (
    DERIVED_FROM_MENTEES,
    MENTEE_RECORD_FUNCTIONS,
    MENTOR_FUNCTIONS,
    mentee_count,
    mentor_functions_for,
)
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, SubjectKind
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.user import Mentor, Role, Student, User
from app.seed_roster import SSO_ONLY_PASSWORD_HASH


def test_the_two_that_belong_to_the_person_stay_in_the_baseline():
    """The assistant and one's own certificate shelf are not a group's.

    Taking them away between assignments would be removing a faculty member's
    own things — and `mentor.upskilling` is that person's uploaded certificates,
    which nobody else has any business in either way.
    """
    assert ROLE_BASELINE["MENTOR"] == {"mentor.agent", "mentor.upskilling"}
    assert MENTOR_FUNCTIONS.isdisjoint(ROLE_BASELINE["MENTOR"])


def test_the_two_functions_are_the_ones_that_need_a_mentee():
    """Each is about somebody else's record: the log of them, the notebook
    about them. Leave approval WAS the fourth until 2026-09-16 and is the Main
    Admin's alone now — not a function, not a capability."""
    assert MENTOR_FUNCTIONS == {"mentor.mentees", "mentor.notebook"}
    assert "mentor.leave_approve" not in MENTOR_FUNCTIONS


def test_skill_verification_does_not_come_with_mentoring():
    """The owner's decision of 2026-10-16: "remove skill verification from
    all mentors". `mentor.verifications` is held only as a grant the office
    makes in Who can do what (app/verifiers.py). Put it back in the derived
    set and every mentor on the deployment verifies their group's skills
    again, silently, on deploy."""
    assert "mentor.verifications" not in DERIVED_FROM_MENTEES
    assert "mentor.verifications" not in ROLE_BASELINE["MENTOR"]
    assert "mentor.verifications" not in ROLE_BASELINE["ADMIN"]


@pytest.fixture
def faculty_with_optional_mentee():
    """A faculty account, a `mentors` row, and a student that can be attached.

    The `mentors` row exists from the start on purpose: having the row and
    having a mentee are different things — `ensure_mentor_group` creates it on
    first assignment and nothing ever deletes it — and this fixture is how the
    difference gets tested.
    """
    made: dict[str, str] = {}
    with SessionLocal() as db:
        tag = uuid.uuid4().hex[:6]
        college = College(code=f"F{tag.upper()}", name="Fn College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        dept = Department(college_id=college.id, name="Fn Dept", code=f"FD{tag}")
        db.add(dept)
        db.flush()
        batch = Cohort(
            code=f"FN-{tag}", name="Fn Batch", batch_label="2026-28",
            degree_level=DegreeLevel.PG, department_id=dept.id,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        db.add(batch)
        db.flush()
        faculty = User(
            email=f"fn-faculty-{tag}@scope.test", name="Fn Faculty", role=Role.MENTOR,
            department_id=dept.id, password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        student_user = User(
            email=f"fn-stu-{tag}@scope.test", name="Fn Student", role=Role.STUDENT,
            password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        db.add_all([faculty, student_user])
        db.flush()
        group = Mentor(user_id=faculty.id)
        db.add(group)
        db.flush()
        student = Student(user_id=student_user.id, usn=f"FN{tag}", cohort_id=batch.id)
        db.add(student)
        db.commit()
        made = {
            "faculty": faculty.id, "group": group.id, "student": student.id,
            "student_user": student_user.id, "batch": batch.id,
            "dept": dept.id, "college": college.id,
        }

    yield made

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id == made["faculty"]))
        db.execute(delete(Student).where(Student.id == made["student"]))
        db.execute(delete(Mentor).where(Mentor.id == made["group"]))
        db.execute(delete(User).where(User.id.in_([made["faculty"], made["student_user"]])))
        db.execute(delete(Cohort).where(Cohort.id == made["batch"]))
        db.execute(delete(Department).where(Department.id == made["dept"]))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


def _attach(db, made, attached: bool) -> None:
    db.get(Student, made["student"]).mentor_id = made["group"] if attached else None
    db.commit()


@requires_db
def test_a_mentors_row_alone_brings_none_of_them(faculty_with_optional_mentee):
    """THE GUARD B2.3 ASKS FOR, first half.

    The account has a `mentors` row and no mentees — the state every faculty
    member who was ever assigned a student and then released them ends up in,
    because nothing deletes the row.
    """
    made = faculty_with_optional_mentee
    with SessionLocal() as db:
        _attach(db, made, False)
        assert mentee_count(db, made["faculty"]) == 0
        assert mentor_functions_for(db, made["faculty"]) == frozenset()
        held = capabilities_for(db, {"userId": made["faculty"], "role": "MENTOR"})
        assert DERIVED_FROM_MENTEES.isdisjoint(held)
        assert "mentor.agent" in held and "mentor.upskilling" in held


@requires_db
def test_one_mentee_brings_all_of_them_and_no_verification(faculty_with_optional_mentee):
    """THE GUARD B2.3 ASKS FOR, second half -- and, since 2026-10-16, the
    mentee does NOT bring skill verification."""
    made = faculty_with_optional_mentee
    with SessionLocal() as db:
        _attach(db, made, True)
        assert mentee_count(db, made["faculty"]) == 1
        assert mentor_functions_for(db, made["faculty"]) == DERIVED_FROM_MENTEES
        held = capabilities_for(db, {"userId": made["faculty"], "role": "MENTOR"})
        assert DERIVED_FROM_MENTEES <= held
        assert "mentor.verifications" not in held


def test_a_mentee_brings_the_read_of_their_record_and_nothing_else():
    """2026-10-16: mentoring a student opens their full record, read only.
    The one key it adds is the record page's, and it is not in the baseline,
    so a faculty member with no mentees does not hold it."""
    assert MENTEE_RECORD_FUNCTIONS == {"admin.student_records"}
    assert DERIVED_FROM_MENTEES == MENTOR_FUNCTIONS | MENTEE_RECORD_FUNCTIONS
    assert MENTEE_RECORD_FUNCTIONS.isdisjoint(ROLE_BASELINE["MENTOR"])


@requires_db
def test_the_functions_follow_the_mentee_with_no_second_step(faculty_with_optional_mentee):
    """WHY THESE ARE DERIVED AND NOT GRANTED.

    Five places in this repository set `students.mentor_id` — three routers,
    app/seed.py and app/grant_access.py. A stored grant is correct only while
    every one of them remembers to re-derive, and the sixth writer somebody adds
    next year will not fail loudly: a faculty member will simply be unable to
    open their own mentee log.

    So this test changes the pointer DIRECTLY, the way a path nobody hooked
    would, and the capability still follows. That is the property; a grant-based
    implementation fails this test by construction.
    """
    made = faculty_with_optional_mentee
    session = {"userId": made["faculty"], "role": "MENTOR"}
    with SessionLocal() as db:
        _attach(db, made, True)
        require_capability(db, session, "mentor.mentees")

        _attach(db, made, False)
        with pytest.raises(Exception) as refused:
            require_capability(db, session, "mentor.mentees")
        assert getattr(refused.value, "status_code", None) == 403

        _attach(db, made, True)
        require_capability(db, session, "mentor.mentees")


@requires_db
def test_a_deliberate_grant_still_works_and_is_additive(faculty_with_optional_mentee):
    """AGENTS.md's escape hatch is untouched: the Main Admin grants itself the
    mentee log when a student's evidence is stuck and nobody else will look.
    Derivation only ever ADDS — it never removes what somebody decided."""
    made = faculty_with_optional_mentee
    with SessionLocal() as db:
        _attach(db, made, False)
        db.add(
            CapabilityGrant(
                capability="mentor.mentees", subject_kind=SubjectKind.USER,
                subject_user_id=made["faculty"],
                reason="Covering while the assigned mentor is on leave.",
            )
        )
        db.commit()
        held = capabilities_for(db, {"userId": made["faculty"], "role": "MENTOR"})
    assert "mentor.mentees" in held, "a deliberate grant was lost"
    assert "mentor.notebook" not in held, "a grant for one key handed over another"


@requires_db
def test_the_office_account_is_unaffected(faculty_with_optional_mentee):
    """The Main Admin has no mentees and never did. `_FACULTY_ONLY` keeps these
    out of its baseline, and deriving them from a mentee count it will never
    have changes nothing about that."""
    made = faculty_with_optional_mentee
    with SessionLocal() as db:
        _attach(db, made, True)
        held = capabilities_for(db, {"userId": "some-admin", "role": "ADMIN"})
    assert "admin.students" in held
    assert "mentor.mentees" not in held
