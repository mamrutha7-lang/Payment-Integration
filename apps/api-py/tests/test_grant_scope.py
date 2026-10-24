"""B1.2 — a grant says what you may do AND where.

Before this, `admin.students` granted to a faculty member in one college read
every student in the other, and the Governance screen showed one word — the
capability's label — with nothing to say how far it went.

The four things these tests hold down, in order of how quietly each would break:

1. THE EIGHTY EXISTING CALL SITES DO NOT CHANGE MEANING. `target` is opt-in; a
   three-argument call still asks "may you do this at all".
2. A ROLE BASELINE IS UNSCOPED. Scope is a property of a grant, because a grant
   is the thing somebody decided to hand over.
3. BOTH DEPARTMENT POINTERS ARE READ. A student reaches a department through
   their batch OR through `students.department_id`; reading one was a live bug
   in feature overrides before it was a hole here.
4. AN EMPTY REACH IS NOT AN UNRESTRICTED ONE. A grant scoped to a department
   that has since been deleted must see nothing, not everything.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.governance import (
    ancestry_of_student,
    capabilities_for,
    ancestry_of_user,
    granted_reaches,
    reaches_target,
    require_capability,
)
from app.models.cohort import Cohort
from app.models.governance import (
    APPROVAL_ACTIVE,
    APPROVAL_PENDING,
    CapabilityGrant,
    ScopeLevel,
    SubjectKind,
)
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.user import Role, Student, User
from app.seed_roster import SSO_ONLY_PASSWORD_HASH
from app.policies import Reach, scope_filter


# --------------------------------------------------------------- pure logic --


def test_a_programme_wide_grant_covers_every_target():
    """`(None, None)` is the row shape for a grant that hangs on no rung."""
    assert reaches_target([(None, None)], [(ScopeLevel.COLLEGE, "anything")])
    assert reaches_target([(None, None)], [])


def test_a_scoped_grant_covers_a_target_that_hangs_under_it():
    ancestry = [
        (ScopeLevel.STUDENT, "stu"),
        (ScopeLevel.COHORT, "batch"),
        (ScopeLevel.DEPARTMENT, "dept"),
        (ScopeLevel.COLLEGE, "coll"),
    ]
    assert reaches_target([(ScopeLevel.COLLEGE, "coll")], ancestry)
    assert reaches_target([(ScopeLevel.DEPARTMENT, "dept")], ancestry)
    assert reaches_target([(ScopeLevel.STUDENT, "stu")], ancestry)
    assert not reaches_target([(ScopeLevel.DEPARTMENT, "other")], ancestry)
    assert not reaches_target([(ScopeLevel.COLLEGE, "other")], ancestry)


def test_a_scoped_grant_does_not_cover_something_that_hangs_under_nothing():
    """THE UNFILED STATE IS NOT A WAY ROUND EVERY SCOPE.

    A student seated in no batch and filed under no department, or an unfiled
    faculty account, has an empty ancestry. Answering "covered" there would make
    the one state the console shows a list of into a hole in the whole system.
    """
    assert not reaches_target([(ScopeLevel.COLLEGE, "coll")], [])
    assert not reaches_target([(ScopeLevel.DEPARTMENT, "dept")], [])


def test_an_empty_reach_sees_nothing_rather_than_everything():
    """A grant scoped to a department that has since been deleted."""
    assert Reach(everything=False).nothing
    assert not Reach(everything=True).nothing
    assert not Reach(everything=False, departments=frozenset({"d"})).nothing


# ------------------------------------------------------------------ fixtures --


@pytest.fixture
def spine():
    """A college, two departments, a batch in the first, a student in each.

    The second student is seated in NO batch and carries `students.department_id`
    instead — the shape that the cohort-only ancestry could not see.
    """
    made: dict[str, str] = {}
    with SessionLocal() as db:
        tag = uuid.uuid4().hex[:6]
        college = College(code=f"S{tag.upper()}", name="Scope College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        one = Department(college_id=college.id, name="Dept One", code=f"D1{tag}")
        two = Department(college_id=college.id, name="Dept Two", code=f"D2{tag}")
        db.add_all([one, two])
        db.flush()
        batch = Cohort(
            code=f"SC-{tag}", name="Scope Batch", batch_label="2026-28",
            degree_level=DegreeLevel.PG, department_id=one.id,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        db.add(batch)
        db.flush()

        seated_user = User(
            email=f"seated-{tag}@scope.test", name="Seated", role=Role.STUDENT,
            password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        unseated_user = User(
            email=f"unseated-{tag}@scope.test", name="Unseated", role=Role.STUDENT,
            password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        holder = User(
            email=f"holder-{tag}@scope.test", name="Holder", role=Role.MENTOR,
            department_id=two.id, password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        db.add_all([seated_user, unseated_user, holder])
        db.flush()
        seated = Student(user_id=seated_user.id, usn=f"SEAT{tag}", cohort_id=batch.id)
        # No cohort. Its department pointer is the ONLY path to a department.
        unseated = Student(user_id=unseated_user.id, usn=f"UNSE{tag}", department_id=one.id)
        db.add_all([seated, unseated])
        db.commit()
        made = {
            "college": college.id, "dept_one": one.id, "dept_two": two.id,
            "batch": batch.id, "seated": seated.id, "unseated": unseated.id,
            "holder": holder.id, "seated_user": seated_user.id,
            "unseated_user": unseated_user.id,
        }

    yield made

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id == made["holder"]))
        db.execute(delete(Student).where(Student.id.in_([made["seated"], made["unseated"]])))
        db.execute(delete(User).where(User.id.in_(
            [made["seated_user"], made["unseated_user"], made["holder"]]
        )))
        db.execute(delete(Cohort).where(Cohort.id == made["batch"]))
        db.execute(delete(Department).where(Department.id.in_([made["dept_one"], made["dept_two"]])))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


def _grant(db, user_id: str, key: str, level: ScopeLevel | None, target_id: str | None) -> str:
    row = CapabilityGrant(
        capability=key, subject_kind=SubjectKind.USER, subject_user_id=user_id,
        scope_level=level, scope_id=target_id, reason="a test grant, twenty characters plus",
    )
    db.add(row)
    db.commit()
    return row.id


# ------------------------------------------------------------------- the DB --


@requires_db
def test_an_unseated_student_still_hangs_under_their_department(spine):
    """THE BUG THIS TASK FOUND, pinned.

    `students.department_id` (migration 31f7a4c60b12) is the only path to a
    department for a student who named one on the registration form and has not
    been seated in a batch — which is every student at a college that has not
    built its batches yet. The ancestry read the cohort route alone, so anything
    hung on a department reached the seated students in it and silently missed
    the unseated ones. Feature overrides had that hole before scope existed.
    """
    with SessionLocal() as db:
        seated = dict(ancestry_of_student(db, spine["seated"]))
        unseated = dict(ancestry_of_student(db, spine["unseated"]))

    assert seated[ScopeLevel.DEPARTMENT] == spine["dept_one"]
    assert seated[ScopeLevel.COHORT] == spine["batch"]
    assert seated[ScopeLevel.COLLEGE] == spine["college"]

    assert unseated[ScopeLevel.DEPARTMENT] == spine["dept_one"], (
        "the unseated student lost their department — the cohort-only ancestry"
    )
    assert unseated[ScopeLevel.COLLEGE] == spine["college"]
    assert ScopeLevel.COHORT not in unseated, "an unseated student has no batch"


@requires_db
def test_a_faculty_account_hangs_under_its_department_and_college(spine):
    with SessionLocal() as db:
        assert dict(ancestry_of_user(db, spine["holder"])) == {
            ScopeLevel.DEPARTMENT: spine["dept_two"],
            ScopeLevel.COLLEGE: spine["college"],
        }


@requires_db
def test_an_unfiled_faculty_account_hangs_under_nothing(spine):
    """`users.department_id IS NULL` is a first-class state on the Faculty
    screen, not a broken row. It hangs under nothing, so no scoped grant
    reaches it — which is why filing them is the Main Admin's work."""
    with SessionLocal() as db:
        unfiled = User(
            email=f"unfiled-{uuid.uuid4().hex[:6]}@scope.test", name="U", role=Role.MENTOR,
            password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        db.add(unfiled)
        db.commit()
        unfiled_id = unfiled.id
    try:
        with SessionLocal() as db:
            assert ancestry_of_user(db, unfiled_id) == []
    finally:
        with SessionLocal() as db:
            db.execute(delete(User).where(User.id == unfiled_id))
            db.commit()


@requires_db
def test_a_three_argument_call_still_asks_only_whether_you_hold_it(spine):
    """The compatibility guarantee this whole change rests on: eighty existing
    call sites pass three positional arguments and must keep their meaning."""
    session = {"role": "MENTOR", "userId": spine["holder"]}
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as refused:
            require_capability(db, session, "admin.students")
        assert refused.value.status_code == 403

        _grant(db, spine["holder"], "admin.students", ScopeLevel.DEPARTMENT, spine["dept_two"])
        # Holds it somewhere -> the unscoped question answers yes, exactly as before.
        require_capability(db, session, "admin.students")


@requires_db
def test_a_scoped_grant_refuses_a_student_outside_it(spine):
    session = {"role": "MENTOR", "userId": spine["holder"]}
    with SessionLocal() as db:
        _grant(db, spine["holder"], "admin.students", ScopeLevel.DEPARTMENT, spine["dept_two"])
        inside = ancestry_of_user(db, spine["holder"])
        outside = ancestry_of_student(db, spine["seated"])

        require_capability(db, session, "admin.students", target=inside)
        with pytest.raises(HTTPException) as refused:
            require_capability(db, session, "admin.students", target=outside)
        assert refused.value.status_code == 403
        assert "does not reach" in refused.value.detail


@requires_db
def test_the_main_admin_is_not_narrowed_by_scope(spine):
    """A baseline capability is unscoped. The Main Admin's baseline is every
    programme key and the programme is the job."""
    session = {"role": "ADMIN", "userId": "whoever"}
    with SessionLocal() as db:
        require_capability(db, session, "admin.students", target=ancestry_of_student(db, spine["seated"]))
        assert scope_filter(db, session, "admin.students").everything


@requires_db
def test_the_reach_of_a_department_grant_covers_both_kinds_of_student(spine):
    """The list side of the same bug: a holder scoped to Dept One sees the
    seated student AND the unseated one, because both hang under it."""
    session = {"role": "MENTOR", "userId": spine["holder"]}
    with SessionLocal() as db:
        _grant(db, spine["holder"], "admin.students", ScopeLevel.DEPARTMENT, spine["dept_one"])
        reach = scope_filter(db, session, "admin.students")
        assert not reach.everything and not reach.nothing
        assert reach.departments == frozenset({spine["dept_one"]})

        visible = set(db.scalars(select(Student.id).where(Student.id.in_(reach.student_ids()))).all())
    assert spine["seated"] in visible, "the seated student is in the granted department"
    assert spine["unseated"] in visible, (
        "the unseated student hangs under the same department through their own pointer"
    )


@requires_db
def test_a_college_grant_reaches_every_department_under_it(spine):
    session = {"role": "MENTOR", "userId": spine["holder"]}
    with SessionLocal() as db:
        _grant(db, spine["holder"], "admin.students", ScopeLevel.COLLEGE, spine["college"])
        reach = scope_filter(db, session, "admin.students")
        visible = set(db.scalars(select(Student.id).where(Student.id.in_(reach.student_ids()))).all())
        staff = set(db.scalars(select(User.id).where(User.id.in_(reach.user_ids()))).all())
    assert {spine["seated"], spine["unseated"]} <= visible
    assert spine["holder"] in staff, "a college grant covers faculty filed under it"


@requires_db
def test_two_grants_are_more_reach_than_one(spine):
    """Scoped grants accumulate. A holder with a department grant and a college
    grant sees both, because two grants are more reach than one."""
    session = {"role": "MENTOR", "userId": spine["holder"]}
    with SessionLocal() as db:
        _grant(db, spine["holder"], "admin.students", ScopeLevel.DEPARTMENT, spine["dept_one"])
        _grant(db, spine["holder"], "admin.students", ScopeLevel.COHORT, spine["batch"])
        reach = scope_filter(db, session, "admin.students")
    assert reach.departments == frozenset({spine["dept_one"]})
    assert reach.cohorts == frozenset({spine["batch"]})


@requires_db
def test_an_expired_or_revoked_grant_reaches_nothing(spine):
    session = {"role": "MENTOR", "userId": spine["holder"]}
    with SessionLocal() as db:
        row = CapabilityGrant(
            capability="admin.students", subject_kind=SubjectKind.USER,
            subject_user_id=spine["holder"], scope_level=ScopeLevel.COLLEGE,
            scope_id=spine["college"], reason="expired before it was ever used",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.add(row)
        db.commit()
        assert granted_reaches(db, spine["holder"], "admin.students") == []
        assert scope_filter(db, session, "admin.students").nothing


# ------------------------------------------- what the research pass found --


@requires_db
def test_everything_selects_everything_rather_than_nothing(spine):
    """THE LANDMINE. `Reach(everything=True)` had every bucket empty, so
    `student_ids()` fell through to the empty-clause branch and returned NO
    ROWS -- on the theory that every caller checks `.everything` first.

    That is a convention across twelve list endpoints, and the cost of
    forgetting it once is an EMPTY ROSTER FOR THE MAIN ADMIN: a screen saying
    the college has no students, which reads as data loss rather than as a
    permission bug, on the account that holds every capability. A helper whose
    most dangerous output is produced by its commonest caller omitting one line
    is built wrong.
    """
    with SessionLocal() as db:
        reach = Reach(everything=True)
        visible = set(db.scalars(select(Student.id).where(Student.id.in_(reach.student_ids()))).all())
        staff = set(db.scalars(select(User.id).where(User.id.in_(reach.user_ids()))).all())
    assert spine["seated"] in visible and spine["unseated"] in visible
    assert spine["holder"] in staff


@requires_db
def test_a_grant_awaiting_a_second_approval_is_not_live_yet(spine):
    """FOUR-EYES APPROVAL THAT APPROVES NOTHING. `approval_state` was written on
    every row, carried a check constraint and a docstring describing the
    two-person rule for capabilities that carry PII -- and was read by no query,
    so a grant awaiting approval was fully live the moment it was inserted.

    Both readers are asserted, because they are the same question asked two
    ways and the bug was that only one of them knew the answer.
    """
    session = {"role": "MENTOR", "userId": spine["holder"]}
    with SessionLocal() as db:
        row = CapabilityGrant(
            capability="admin.students", subject_kind=SubjectKind.USER,
            subject_user_id=spine["holder"], reason="awaiting a second pair of eyes",
            approval_state=APPROVAL_PENDING,
        )
        db.add(row)
        db.commit()

        assert granted_reaches(db, spine["holder"], "admin.students") == []
        assert "admin.students" not in capabilities_for(db, session)
        assert scope_filter(db, session, "admin.students").nothing

        row.approval_state = APPROVAL_ACTIVE
        db.commit()
        assert "admin.students" in capabilities_for(db, session)


@requires_db
def test_the_check_and_the_list_agree_at_every_rung(spine):
    """THE PROPERTY NEITHER IMPLEMENTATION GUARANTEES ON ITS OWN.

    Scope is computed twice, in two languages, in opposite directions:
    `ancestry_of_student` walks a target UP to its ancestors in Python, and
    `Reach.student_ids` walks a grant DOWN to its descendants in SQL. Zanzibar
    derives its Check and its reverse index from one namespace configuration so
    they cannot disagree; REEP hand-writes both, and nothing makes them agree.

    That is not hypothetical. It has already happened once here, in exactly
    this shape: `students.department_id` was read by the institution card and
    not by the ancestry walk, and the hole sat silently in feature overrides for
    months. This test is the thing that would have caught it -- for every rung a
    student hangs on, a grant at that rung must both PASS the check and RETURN
    the student in the list.
    """
    with SessionLocal() as db:
        for student_id in (spine["seated"], spine["unseated"]):
            ancestry = ancestry_of_student(db, student_id)
            assert ancestry, "a student in the fixture hangs under nothing"
            for level, target_id in ancestry:
                # The check says yes...
                assert reaches_target([(level, target_id)], ancestry), (
                    f"check refused {level.value} for the student it came from"
                )
                # ...so the list must contain them.
                reach = Reach(everything=False, **{_BUCKET[level]: frozenset({target_id})})
                listed = set(
                    db.scalars(select(Student.id).where(Student.id.in_(reach.student_ids()))).all()
                )
                assert student_id in listed, (
                    f"the check passes at {level.value} but the list leaves the student out — "
                    "the two implementations of scope have drifted"
                )


#: Which Reach bucket each rung fills. Written out rather than derived from the
#: enum name so that adding a rung to ScopeLevel without adding a bucket is a
#: KeyError in this test rather than a silently unchecked level.
_BUCKET = {
    ScopeLevel.COLLEGE: "colleges",
    ScopeLevel.DEPARTMENT: "departments",
    ScopeLevel.COURSE: "courses",
    ScopeLevel.SPECIALIZATION: "specializations",
    ScopeLevel.COHORT: "cohorts",
    ScopeLevel.STUDENT: "students",
}


# --------------------------------------------------- B1.2, the WRITE path --
#
# Everything above proves the reach is READ and enforced correctly. None of it
# proved a reach could be written: `POST /grants` had no scope fields at all, so
# every row this API made was programme-wide and the whole apparatus above only
# ever saw `(None, None)` in production. A rule that cannot be expressed is not
# a rule, and the console's scope select was disabled to say so.


def _admin(login) -> dict:
    return login("admin@bgscet.ac.in", "admin123")


#: The key these tests hang a scope on. `admin.analytics` and NOT
#: `admin.students`, which is the obvious example and the wrong one: it
#: `carries_pii`, so a deputy's grant of it is written `pending_approval` under
#: B2.4 and `granted_reaches` correctly reports no reach at all until somebody
#: else approves it (the Main Admin's own grant is live at once). A scope test
#: whose outcome turned on who was granting would be testing two things and
#: would fail for the other one. Approval-plus-scope has its own test below.
_KEY = "admin.analytics"


@requires_db
def test_a_grant_with_no_scope_is_still_programme_wide(client, login, spine):
    """THE COMPATIBILITY CASE, and the one that must never change.

    Every client written before these two fields existed posts neither, and the
    grant it gets must be the one it has always been given. If this ever fails,
    adding the fields quietly narrowed every existing grant in the product.
    """
    r = client.post(
        "/api/admin/governance/grants",
        headers=_admin(login),
        json={
            "capability": _KEY,
            "user_ids": [spine["holder"]],
            "reason": "No scope named, so this reaches the whole programme.",
        },
    )
    assert r.status_code == 201, r.text
    row = r.json()[0]
    assert row["scope_level"] is None
    assert row["scope_id"] is None
    assert row["scope_label"] is None

    with SessionLocal() as db:
        assert granted_reaches(db, spine["holder"], _KEY) == [(None, None)]
        assert scope_filter(db, {"role": "MENTOR", "userId": spine["holder"]},
                            _KEY).everything


@requires_db
def test_a_department_scoped_grant_is_written_and_read_back(client, login, spine):
    """The point of the whole task: what the console posts is what enforcement
    reads. Written through the endpoint and read through `granted_reaches`, so a
    projection that dropped the columns on the way out would fail here."""
    r = client.post(
        "/api/admin/governance/grants",
        headers=_admin(login),
        json={
            "capability": _KEY,
            "user_ids": [spine["holder"]],
            "reason": "Only the students in their own department.",
            "scope_level": "DEPARTMENT",
            "scope_id": spine["dept_one"],
        },
    )
    assert r.status_code == 201, r.text
    row = r.json()[0]
    assert row["scope_level"] == "DEPARTMENT"
    assert row["scope_id"] == spine["dept_one"]
    assert row["scope_label"] == "Dept One", "the screen needs a name, not an id"
    # `scope` is the CAPABILITY's declared scope and is a different question.
    assert row["scope"] == "PROGRAMME"

    with SessionLocal() as db:
        assert granted_reaches(db, spine["holder"], _KEY) == [
            (ScopeLevel.DEPARTMENT, spine["dept_one"])
        ]
        session = {"role": "MENTOR", "userId": spine["holder"]}
        reach = scope_filter(db, session, _KEY)
        assert not reach.everything
        assert reach.departments == frozenset({spine["dept_one"]})
        # And it reaches the students of that department — both of them, the
        # seated one through the batch and the unseated one through their own
        # pointer.
        listed = set(
            db.scalars(select(Student.id).where(Student.id.in_(reach.student_ids()))).all()
        )
        assert {spine["seated"], spine["unseated"]} <= listed


@requires_db
def test_a_scoped_grant_refuses_the_endpoint_outside_its_reach(client, login, spine):
    """The enforcement half, end to end from the write. A grant over Dept Two
    must not answer for a student in Dept One — and before this task there was
    no way to make such a grant at all, so this path was untested from the
    console's side."""
    r = client.post(
        "/api/admin/governance/grants",
        headers=_admin(login),
        json={
            "capability": _KEY,
            "user_ids": [spine["holder"]],
            "reason": "Their own department, which holds neither test student.",
            "scope_level": "DEPARTMENT",
            "scope_id": spine["dept_two"],
        },
    )
    assert r.status_code == 201, r.text

    session = {"role": "MENTOR", "userId": spine["holder"]}
    with SessionLocal() as db:
        # Holds the key...
        require_capability(db, session, _KEY)
        # ...and not here.
        with pytest.raises(HTTPException) as refusal:
            require_capability(
                db, session, _KEY,
                target=ancestry_of_student(db, spine["seated"]),
            )
        assert refusal.value.status_code == 403
        assert "does not reach" in refusal.value.detail


@requires_db
def test_a_level_without_an_id_is_refused(client, login, spine):
    """BOTH OR NEITHER. A level alone names no target; stored, it would read
    back as programme-wide — the widest reading of a request that asked for the
    narrowest."""
    for body in (
        {"scope_level": "DEPARTMENT"},
        {"scope_id": spine["dept_one"]},
    ):
        r = client.post(
            "/api/admin/governance/grants",
            headers=_admin(login),
            json={
                "capability": _KEY,
                "user_ids": [spine["holder"]],
                "reason": "Half a scope target is not a scope target.",
                **body,
            },
        )
        assert r.status_code == 422, (body, r.text)

    with SessionLocal() as db:
        assert granted_reaches(db, spine["holder"], _KEY) == []


@requires_db
def test_an_empty_scope_id_is_no_scope_rather_than_a_bad_one(client, login, spine):
    """A `<select>` with nothing chosen posts `""`. That is "not narrowed", and
    must not become "narrowed to a target that does not exist"."""
    r = client.post(
        "/api/admin/governance/grants",
        headers=_admin(login),
        json={
            "capability": _KEY,
            "user_ids": [spine["holder"]],
            "reason": "The scope select was left on its blank option.",
            "scope_level": None,
            "scope_id": "",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()[0]["scope_level"] is None


@requires_db
def test_a_target_that_does_not_exist_is_refused(client, login, spine):
    """A grant hung on nothing would be listed as live and reach NOBODY — the
    admin would watch a grant they made refuse every request."""
    r = client.post(
        "/api/admin/governance/grants",
        headers=_admin(login),
        json={
            "capability": _KEY,
            "user_ids": [spine["holder"]],
            "reason": "This department id belongs to no department.",
            "scope_level": "DEPARTMENT",
            "scope_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 422, r.text
    assert "does not exist" in r.text
    with SessionLocal() as db:
        assert granted_reaches(db, spine["holder"], _KEY) == []


@requires_db
def test_the_same_key_at_two_rungs_is_two_grants(client, login, spine):
    """THE DUPLICATE CHECK INCLUDES THE REACH.

    Without that, "and Civil as well" is answered by the no-duplicates rule
    finding the Mechanical grant and silently doing nothing, while the console
    reports success. Two rungs are two decisions and `scope_filter` unions them.
    """
    def grant(dept: str):
        return client.post(
            "/api/admin/governance/grants",
            headers=_admin(login),
            json={
                "capability": _KEY,
                "user_ids": [spine["holder"]],
                "reason": "One department at a time, added separately.",
                "scope_level": "DEPARTMENT",
                "scope_id": dept,
            },
        )

    assert grant(spine["dept_one"]).status_code == 201
    second = grant(spine["dept_two"])
    assert second.status_code == 201, second.text
    assert second.json(), "the second department was swallowed as a duplicate"

    # The SAME rung twice is still the no-op it always was.
    again = grant(spine["dept_one"])
    assert again.status_code == 201
    assert again.json() == []

    with SessionLocal() as db:
        reach = scope_filter(db, {"role": "MENTOR", "userId": spine["holder"]}, _KEY)
        assert reach.departments == frozenset({spine["dept_one"], spine["dept_two"]})


@requires_db
def test_the_trail_records_how_far_the_grant_went(client, login, spine):
    """"Who was handed admin.analytics" and "how far" are the two halves of the
    question somebody asks this trail in six months."""
    from app.models.redesign import AuditEvent

    r = client.post(
        "/api/admin/governance/grants",
        headers=_admin(login),
        json={
            "capability": _KEY,
            "user_ids": [spine["holder"]],
            "reason": "Recorded with its reach, not only its name.",
            "scope_level": "COLLEGE",
            "scope_id": spine["college"],
        },
    )
    assert r.status_code == 201, r.text
    grant_id = r.json()[0]["id"]

    with SessionLocal() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_type == "capability_grant",
                AuditEvent.entity_id == grant_id,
            )
        )
        assert event is not None
        assert event.after_json["scope_level"] == "COLLEGE"
        assert event.after_json["scope_id"] == spine["college"]


@requires_db
def test_a_pii_grant_keeps_its_reach_across_the_second_approval(client, login, spine):
    """B2.4 AND B1.2 TOGETHER, because the interaction is the risk.

    A DEPUTY's grant of a capability that shows a student's own record is
    written `pending_approval` and holds nothing until a different holder of
    `admin.governance` approves it (the Main Admin's own grants are live at
    once — `initial_approval_state`). The approval path rewrites
    `approval_state` on a row it did not create, and the mistake it invites is
    re-deriving the row rather than updating it — which would drop a scope the
    granter chose and silently widen a PII grant to the whole programme at the
    moment a second person signed it off. That is the worst version of this
    bug, so it has its own test.

    The pending row is written directly, as a deputy's would be, and approved
    through the REAL endpoint by the Main Admin — the one door that rewrites
    the state, and the one this test is about.
    """
    with SessionLocal() as db:
        row = CapabilityGrant(
            capability="admin.students", subject_kind=SubjectKind.USER,
            subject_user_id=spine["holder"],
            reason="Their own department's students, and nobody else's.",
            scope_level=ScopeLevel.DEPARTMENT, scope_id=spine["dept_one"],
            approval_state=APPROVAL_PENDING, role_at_grant=Role.MENTOR.value,
        )
        db.add(row)
        db.commit()
        grant_id = row.id
        # Pending means NO reach yet, scope or no scope.
        assert granted_reaches(db, spine["holder"], "admin.students") == []

    r = client.post(
        f"/api/admin/governance/grants/{grant_id}/approve",
        headers=_admin(login),
        json={"reason": "Agreed at the governance meeting; the panel needs this department."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["approval_state"] == APPROVAL_ACTIVE
    assert r.json()["scope_level"] == "DEPARTMENT"
    assert r.json()["scope_id"] == spine["dept_one"]

    with SessionLocal() as db:
        assert granted_reaches(db, spine["holder"], "admin.students") == [
            (ScopeLevel.DEPARTMENT, spine["dept_one"])
        ], "approval widened a scoped PII grant to the whole programme"
