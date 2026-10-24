"""The institutional spine: College -> Department -> Cohort -> Student.

What these pin, and why each one is worth a test:

  * The locked profile card is read THROUGH the cohort join and never stored on
    the student. If someone "optimises" it into columns on `students`, the join
    test still passes but the deferred Academic year / Course / Specialization
    levels become a backfill of every student row. The test that catches that is
    `test_moving_a_batch_moves_every_seated_students_card`.

  * A student cannot write their own institutional assignment. The card says
    "Locked — not editable by students"; this is what makes that true.

  * Approving a registration provisions exactly one account, however many times
    the button is pressed.

  * `ProfileOut` is built in ONE place. It was built in two, they drifted, and
    PUT /api/student/profile raised a 4-field ValidationError on every call with
    nothing catching it.
"""

import uuid
import warnings
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SAWarning

from conftest import requires_db

from app.db import SessionLocal
from app.models.cohort import Cohort
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.registration import Registration, RegistrationStatus
from app.models.student_profile import StudentProfile
from app.models.user import Role, Student, User
from app.routers.student import InstitutionOut, _institution_for


#: Every flat, nullable field on the card. A new level adds to this list — and
#: to nothing else in this file, because the card's rows come from `levels`.
_FLAT_FIELDS = (
    "college_name",
    "college_code",
    "department_name",
    "course_name",
    "specialization_name",
    "batch_label",
    "entry_date",
    "expected_completion",
)


# --------------------------------------------------------------- pure tests --
# No database. These run everywhere, including a machine with Docker stopped.


def test_an_unseated_student_yields_an_empty_card_without_touching_the_database():
    """Every field null, and `db` is never used.

    A student legitimately exists before an admin seats them — provisioning
    creates the row, seating happens after. The card must render dashes rather
    than 404 or raise, because it is one block on a screen with plenty else to
    show. Passing `db=None` proves the early return happens before any query:
    if the resolver ever starts querying first, this raises AttributeError and
    the test says so.
    """
    stu = Student(user_id="u1", cohort_id=None)
    out = _institution_for(None, stu)  # type: ignore[arg-type]
    assert isinstance(out, InstitutionOut)
    flat = {k: v for k, v in out.model_dump().items() if k not in ("levels", "not_in_use_note")}
    assert flat == dict.fromkeys(_FLAT_FIELDS, None)
    # An UNSEATED student has every level pending — the admin owes them a seat.
    # Nothing is "not in use", because nothing is known about their institution.
    assert [lv["state"] for lv in out.model_dump()["levels"]] == ["pending"] * len(out.levels)
    assert out.not_in_use_note is None


def test_a_missing_student_yields_an_empty_card():
    """`_profile_out` passes whatever `db.get(Student, ...)` returned, which may
    be None on a profile whose student row is gone. That must not raise.

    ALL SIX FIELDS, not just one. Asserting only `college_name` left the two
    early returns free to diverge — split them and have the None branch return
    "N/A" for a code and an em dash for a department, and this still passed
    while a card headed "verified by Main Admin" rendered invented text.
    """
    out = _institution_for(None, None)  # type: ignore[arg-type]
    flat = {k: v for k, v in out.model_dump().items() if k not in ("levels", "not_in_use_note")}
    assert flat == dict.fromkeys(_FLAT_FIELDS, None)


# `test_every_institution_field_accepts_none` used to sit here. It constructed
# InstitutionOut directly and asserted the six fields accept None — which the
# two tests above already prove, harder, by getting an all-None model out of the
# resolver itself. It tested nothing `_institution_for` does and could not fail
# in any way they would not also catch, so it is gone rather than kept as a
# third copy of the same assertion. The nullability it meant to defend is
# covered end-to-end by `test_a_partly_built_chain_renders_what_exists`.


# ----------------------------------------------------------- database tests --


@pytest.fixture
def institution(make_user):
    """A College -> Department -> Cohort chain, and a student seated in it.

    Depends on `make_user` deliberately. `make_user` deletes its users on
    finalise, and the Student row references one of them — a plain helper would
    tear down in the wrong order and the run would die on a ForeignKeyViolation
    during cleanup instead of reporting the test result. A fixture that depends
    on `make_user` finalises FIRST, which is the window needed to drop the
    referencing rows.
    """
    s = make_user("spine")
    # Unique per test. A shared literal means one failed teardown cascades into
    # every later test as a uq_college_code violation, which hides the real
    # failure behind four fake ones.
    tag = uuid.uuid4().hex[:6].upper()
    created: dict[str, str] = {}
    with SessionLocal() as db:
        college = College(code=f"TC{tag}", name="Test College", campus="Bengaluru", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        department = Department(
            college_id=college.id, code=f"TD{tag}", name="Test Management", status=STATUS_ACTIVE
        )
        db.add(department)
        db.flush()
        cohort = Cohort(
            code=f"TST-{tag}",
            name="Test Batch",
            batch_label="2024-26",
            degree_level=DegreeLevel.PG,
            department_id=department.id,
            start_date=datetime(2024, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        db.add(cohort)
        db.flush()
        student = db.scalar(select(Student).where(Student.user_id == s.user_id))
        student.cohort_id = cohort.id
        # make_user creates a Student but no StudentProfile, and
        # GET /api/student/profile answers 404 without one.
        if db.get(StudentProfile, student.id) is None:
            db.add(StudentProfile(student_id=student.id))
        created = {
            "college_code": college.code,
            "college_id": college.id,
            "department_id": department.id,
            "cohort_id": cohort.id,
            "student_id": student.id,
        }
        db.commit()

    yield {**created, "session": s}

    with SessionLocal() as db:
        # Order matters: the FKs added in d5a1c8b30f47 / e6b2d9c41a83 refuse
        # otherwise, which is exactly the protection they exist for.
        # make_user owns the Student row; we only release its cohort_id so the
        # Cohort below can be deleted.
        db.execute(delete(StudentProfile).where(StudentProfile.student_id == created["student_id"]))
        stu = db.get(Student, created["student_id"])
        if stu is not None:
            stu.cohort_id = None
            db.flush()  # release the reference before the cohort is deleted
        db.execute(delete(Cohort).where(Cohort.id == created["cohort_id"]))
        db.execute(delete(Department).where(Department.id == created["department_id"]))
        db.execute(delete(College).where(College.id == created["college_id"]))
        db.commit()


@requires_db
def test_the_locked_card_is_populated_through_the_join(client, institution):
    """All five fields, reached from `students.cohort_id` and stored on none of them."""
    s = institution["session"]
    r = client.get("/api/student/profile", headers=s.headers)
    # Asserted, not assumed: without this a 404 surfaces as KeyError on the line
    # below rather than as a readable failure naming the status.
    assert r.status_code == 200, r.text
    card = r.json()["institution"]
    assert card["college_name"] == "Test College"
    assert card["college_code"] == institution["college_code"]
    assert card["department_name"] == "Test Management"
    assert card["batch_label"] == "2024-26"
    # Entry and completion belong to the BATCH. That is why they are not columns
    # on `students`, and why the next test can move them for everyone at once.
    assert card["entry_date"] == "2024-08-01"
    assert card["expected_completion"] == "2026-07-31"


@requires_db
def test_moving_a_batch_moves_every_seated_students_card(client, institution):
    """The anti-denormalisation test.

    Editing the batch changes what the student sees, with no write to the
    student row. If someone copies these onto `students`, this fails — which is
    the point: the copy is the tempting shortcut that makes the deferred
    hierarchy levels expensive later.
    """
    s = institution["session"]
    # READ FIRST. Without this the test does a single GET after the mutation, on
    # a cohort created fresh per test — so any cache added to `_institution_for`
    # would be cold, would return the new value, and the test would pass while
    # every real student saw a stale card until eviction. student.py already
    # carries a `_leaderboard_cache`, so this is not hypothetical. Reading
    # before AND after means a cache has something stale to serve.
    before = client.get("/api/student/profile", headers=s.headers).json()["institution"]
    assert before["batch_label"] == "2024-26"

    with SessionLocal() as db:
        cohort = db.get(Cohort, institution["cohort_id"])
        cohort.batch_label = "2025-27"
        cohort.end_date = datetime(2027, 7, 31, tzinfo=timezone.utc)
        cohort.name = "Moved Batch"
        db.commit()

    card = client.get("/api/student/profile", headers=s.headers).json()["institution"]
    assert card["batch_label"] == "2025-27"
    assert card["expected_completion"] == "2027-07-31"
    # The fields that did NOT move must still resolve through the same join,
    # so a resolver that started scanning for "the newest cohort" is caught.
    assert card["college_name"] == "Test College"
    assert card["department_name"] == "Test Management"
    assert card["entry_date"] == "2024-08-01"


@requires_db
def test_a_partly_built_chain_renders_what_exists_and_nulls_the_rest(client, institution):
    """A cohort with no department is a REAL row, not a hypothetical.

    `Cohort.department_id` is nullable precisely because every cohort that
    existed before `departments` did has it NULL, and this is the case with no
    coverage anywhere: drop the `if cohort.department_id else None` guard in
    `_institution_for` and the happy-path test still passes while every legacy
    cohort raises on `db.get(Department, None)`.

    The card must degrade one hop at a time — batch facts still shown, college
    and department null — because that is exactly what a half-migrated
    institution looks like, and a 500 on the profile screen is not an
    acceptable way to say "no department yet".
    """
    s = institution["session"]
    with SessionLocal() as db:
        db.get(Cohort, institution["cohort_id"]).department_id = None
        db.commit()

    # ERRORS ON SAWarning, and that is the whole point of the `warnings` block.
    # Dropping the `if cohort.department_id else None` guard does NOT raise
    # today — db.get(Department, None) quietly returns None — so a plain
    # behavioural assertion could not tell the guarded code from the unguarded
    # code, and this test would have passed against the bug it exists for.
    # SQLAlchemy does warn: "fully NULL primary key identity cannot load any
    # object. This condition may raise an error in a future release." Promoting
    # that warning to an error is what makes the mutation visible now instead of
    # on the SQLAlchemy upgrade that turns it into an exception.
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        r = client.get("/api/student/profile", headers=s.headers)
    assert r.status_code == 200, r.text
    card = r.json()["institution"]
    assert card["batch_label"] == "2024-26", "batch facts survive a missing department"
    assert card["entry_date"] == "2024-08-01"
    assert card["college_name"] is None, "no department means no college to reach"
    assert card["college_code"] is None
    assert card["department_name"] is None
    # The rows say WHY each blank is blank. A missing department is something
    # the admin owes ("pending"); a level nobody switched on is "not_in_use".
    states = {lv["key"]: lv["state"] for lv in card["levels"]}
    assert states["department"] == "pending"
    assert states["college"] == "pending"
    assert states["batch"] == "set"


@requires_db
def test_the_card_hides_unused_levels_and_says_so(client, institution):
    """A level that is optional and blank is NOT a dash. It is hidden and named.

    Rendering "Course —" on a card headed "verified by Main Admin" tells the
    student their record is incomplete when it is complete: a false pending,
    the mirror image of the confident zero the English-baseline rule forbids.
    The row is omitted, and the omission is stated once in a footnote — so the
    card is neither lying low (a dash) nor lying by silence (nothing at all).
    """
    s = institution["session"]
    card = client.get("/api/student/profile", headers=s.headers).json()["institution"]
    states = {lv["key"]: lv["state"] for lv in card["levels"]}
    # The fixture builds the chain to department + batch only.
    assert states["course"] == "not_in_use"
    assert states["specialization"] == "not_in_use"
    assert card["not_in_use_note"] == (
        f"Course and Specialization are not used at {institution['college_code']}."
    )
    # And the flat fields are still honest nulls for anything reading by name.
    assert card["course_name"] is None
    assert card["specialization_name"] is None


@requires_db
def test_flipping_a_level_to_required_turns_its_blank_into_pending(
    client, institution, monkeypatch
):
    """THE SWITCH, observed from the student's side.

    Flip `required` on the model module's constant — the one line the owner
    edits — and, with no other change, the same blank stops being "not in use"
    and becomes "pending": something the admin now owes this student. Read
    through the module attribute at call time, so the flip is visible from the
    one place it is made.
    """
    from app.models import institution as institution_model
    from app.models.institution import HierarchyLevel

    flipped = tuple(
        HierarchyLevel(lv.key, lv.label, lv.field, required=True)
        for lv in institution_model.HIERARCHY_LEVELS
    )
    monkeypatch.setattr(institution_model, "HIERARCHY_LEVELS", flipped)

    s = institution["session"]
    card = client.get("/api/student/profile", headers=s.headers).json()["institution"]
    states = {lv["key"]: lv["state"] for lv in card["levels"]}
    assert states["course"] == "pending"
    assert states["specialization"] == "pending"
    assert card["not_in_use_note"] is None, "a required level is never 'not in use'"


@requires_db
def test_a_student_cannot_write_admin_owned_columns_on_their_own_profile(client, institution):
    """"Locked — not editable by students" is enforced, not just printed.

    WHAT THE FIRST VERSION OF THIS TEST GOT WRONG, because it matters more than
    the test does. It forged `college_name` and `batch_label` — neither of which
    is a column ANYWHERE in the codebase; both are derived response fields. And
    `ProfileUpdateIn` is `extra="ignore"`, so pydantic dropped the forged keys
    before the handler ever ran. It asserted that nothing happened, which was
    guaranteed: the test was structurally incapable of failing.

    Worse, it was incapable of failing in the exact scenario it existed for. Set
    `model_config = ConfigDict(extra="allow")` on ProfileUpdateIn and
    `update_profile`'s `setattr(prof, field, value)` loop writes EVERY submitted
    key onto the ORM object. `college_name` is not a column so it silently
    no-ops and the old test stayed green — while `placement_eligible` IS a
    column, and a student would be setting their own placement eligibility.

    So this forges the three fields that are real, writable columns on
    StudentProfile and are deliberately absent from ProfileUpdateIn:
    placement_eligible (admin-set, per update_profile's own docstring), skills
    (mentor-verified) and photo_upload_id (set by the upload flow). Those are
    the ones worth a guard; the derived ones never needed one.
    """
    s = institution["session"]
    before = client.get("/api/student/profile", headers=s.headers).json()

    # placement_eligible DEFAULTS to True, so forging True proves nothing —
    # the assertion would pass on the untouched default. Set the restrictive
    # value first; the forge then has to flip it back, which is the direction a
    # student actually gains from.
    with SessionLocal() as db:
        prof = db.scalar(
            select(StudentProfile).where(StudentProfile.student_id == institution["student_id"])
        )
        prof.placement_eligible = False
        prof.skills = []
        prof.photo_upload_id = None
        db.commit()

    r = client.put(
        "/api/student/profile",
        headers=s.headers,
        json={
            "city": "Mysuru",
            "placement_eligible": True,
            "skills": ["forged"],
            "photo_upload_id": "forged-upload-id",
            "college_name": "Forged University",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["city"] == "Mysuru"  # the legitimate field DID change

    # Read the COLUMNS back, not the response: the response could omit a field
    # that was nonetheless written.
    with SessionLocal() as db:
        prof = db.scalar(
            select(StudentProfile).where(StudentProfile.student_id == institution["student_id"])
        )
        assert prof is not None
        assert prof.placement_eligible is False, "a student set their own placement eligibility"
        assert prof.skills == [], "a student wrote their own verified skills"
        assert prof.photo_upload_id is None, "a student pointed their photo at another upload"

    card = client.get("/api/student/profile", headers=s.headers).json()["institution"]
    assert card["college_name"] == before["institution"]["college_name"]


@requires_db
def test_updating_a_profile_returns_a_complete_body(client, institution):
    """Regression: PUT /api/student/profile used to raise a ValidationError.

    `ProfileOut` was built in two places. When usn / full_name /
    current_semester / current_stage were added, only the inline copy in
    `my_profile` was updated, so every PUT failed on four missing fields — and
    no test exercised the update path's body, so nothing caught it. There is now
    one `_profile_out`, and this asserts the shapes match.
    """
    s = institution["session"]
    put = client.put("/api/student/profile", headers=s.headers, json={"city": "Hubli"})
    assert put.status_code == 200, put.text
    get = client.get("/api/student/profile", headers=s.headers)

    # VALUE equality, not `set(put.json()) == set(get.json())`. Comparing key
    # NAMES only would pass against a second builder that returned the right
    # keys with wrong values — hardcoded usn=None, an empty institution card —
    # which is precisely the drift this test exists to catch, and both routes
    # declaring response_model=ProfileOut already makes a NAME drift a 500.
    assert put.json() == get.json()
    assert put.json()["city"] == "Hubli"  # the PUT actually persisted
    for key in ("usn", "full_name", "current_semester", "current_stage", "institution"):
        assert key in put.json()
    assert put.json()["institution"]["college_name"] == "Test College"


@pytest.fixture
def applicant():
    """A pending Registration, cleaned up WHATEVER the test does.

    The first version of these tests did their cleanup inline, after the
    assertions. Any failed assertion therefore leaked a Registration, a User, a
    Student and a StudentProfile into the dev database permanently — and the
    leak then MASKED a real regression, because a rerun found the leftover user
    and reused it instead of inserting. A yield fixture's finaliser runs on
    failure too, which is the whole reason to use one.
    """
    # (email, whether a users row already existed when the application was
    # made). The flag matters: one test deliberately applies with a MENTOR's
    # address, and that mentor belongs to `make_user`, which has its own
    # finaliser and its own login_days rows. Deleting a user this fixture did
    # not create is how a teardown starts failing on someone else's foreign key.
    created: list[tuple[str, bool]] = []

    def _make(email: str, name: str, *, usn: str | None = None):
        with SessionLocal() as db:
            pre_existing = db.scalar(select(User).where(User.email == email)) is not None
            db.execute(delete(Registration).where(Registration.email == email))
            reg = Registration(
                name=name,
                email=email,
                usn=usn,
                degree_level=DegreeLevel.PG,
                status=RegistrationStatus.PENDING_REVIEW,
            )
            db.add(reg)
            db.commit()
            created.append((email, pre_existing))
            return reg.id

    yield _make

    with SessionLocal() as db:
        for email, pre_existing in created:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None and not pre_existing:
                for stu in db.scalars(select(Student).where(Student.user_id == user.id)).all():
                    db.execute(delete(StudentProfile).where(StudentProfile.student_id == stu.id))
                    db.execute(delete(Student).where(Student.id == stu.id))
            db.execute(delete(Registration).where(Registration.email == email))
            if user is not None and not pre_existing:
                db.execute(delete(User).where(User.id == user.id))
        db.commit()


@requires_db
def test_provisioning_the_same_application_twice_inserts_one_of_everything(applicant):
    """Idempotency by lookup — tested where it actually lives.

    WHY THIS CALLS `_provision_student` DIRECTLY. Through the endpoint, the
    already-decided 409 fires BEFORE provisioning runs, so a second approval
    never reaches this function. That made the endpoint-level test worthless as
    an idempotency test: all four lookups inside `_provision_student` — the
    approved_student_id short-circuit, the User lookup, the Student lookup and
    the StudentProfile lookup — could be deleted and it stayed green, including
    the one whose comment records the duplicate-insert bug it was written to fix.

    The realistic path to a second call is not a double-click at all: it is an
    applicant who ALREADY has a users row (seeded by `python -m app.seed_roster`,
    the normal state for an enrolled student) being approved. That inserts a
    duplicate email and 500s the director's Approve button. So: call it twice in
    one session, and count.
    """
    from app.routers.registration import _provision_student

    email = "spine.applicant@bgscet.ac.in"
    reg_id = applicant(email, "Spine Applicant", usn="1BG26SPN99")

    with SessionLocal() as db:
        cohort = db.scalar(select(Cohort))
        cohort_id = cohort.id if cohort is not None else None
        if cohort_id is not None:
            db.get(Registration, reg_id).cohort_id = cohort_id
            db.commit()

    with SessionLocal() as db:
        reg = db.get(Registration, reg_id)
        first = _provision_student(db, reg)
        db.commit()
        second = _provision_student(db, reg)
        db.commit()
        assert first.id == second.id, "the second call created a different student"

    with SessionLocal() as db:
        users = db.scalars(select(User).where(User.email == email)).all()
        assert len(users) == 1, "one application, one user"
        students = db.scalars(select(Student).where(Student.user_id == users[0].id)).all()
        assert len(students) == 1, "one application, one student"
        profiles = db.scalars(
            select(StudentProfile).where(StudentProfile.student_id == students[0].id)
        ).all()
        assert len(profiles) == 1, "one application, one profile"

        # The fields nothing asserted before. Each is a one-line mutation the
        # old test could not see.
        assert users[0].role is Role.STUDENT, (
            "provisioning must mint a STUDENT; role=DIRECTOR here would be an "
            "approval-shaped privilege escalation"
        )
        assert students[0].usn == "1BG26SPN99", "the USN is the roster key and is shown on the card"
        assert students[0].cohort_id == cohort_id, (
            "cohort_id is what connects the rule engine to a seat — the one line "
            "this whole provisioning step exists for"
        )
        assert db.get(Registration, reg_id).approved_student_id == students[0].id


@requires_db
def test_approving_an_applicant_who_is_already_on_the_roster_reuses_their_account(applicant):
    """The scenario the User lookup exists for, which the twice-called test misses.

    Calling `_provision_student` twice does NOT exercise this lookup: the
    `approved_student_id` short-circuit at the top returns before reaching it,
    so deleting the lookup entirely leaves that test green. Verified by
    mutation, which is the only reason this test exists.

    The real path is the ordinary one. `python -m app.seed_roster` creates a
    users row for every enrolled student from the USN roster — so by the time
    an application is approved, the account usually ALREADY EXISTS and carries
    no approved_student_id. Insert unconditionally and that is a duplicate on a
    unique email: a 500 on the director's Approve button, for the most common
    case rather than an edge one.
    """
    from app.routers.registration import SSO_ONLY_PASSWORD_HASH, _provision_student

    email = "spine.onroster@bgscet.ac.in"
    reg_id = applicant(email, "Already On Roster", usn="1BG26ROS01")

    # Exactly what seed_roster leaves behind: a user, no student, no profile.
    with SessionLocal() as db:
        db.add(
            User(
                email=email,
                name="Already On Roster",
                role=Role.STUDENT,
                password_hash=SSO_ONLY_PASSWORD_HASH,
            )
        )
        db.commit()
        seeded_user_id = db.scalar(select(User).where(User.email == email)).id

    with SessionLocal() as db:
        _provision_student(db, db.get(Registration, reg_id))
        db.commit()

    with SessionLocal() as db:
        users = db.scalars(select(User).where(User.email == email)).all()
        assert len(users) == 1, "provisioning inserted a second account for a roster student"
        assert users[0].id == seeded_user_id, "the roster account was replaced rather than reused"
        assert (
            db.scalar(select(Student).where(Student.user_id == seeded_user_id)) is not None
        ), "the existing account gained no student row"


@requires_db
def test_a_second_approval_through_the_endpoint_is_refused(client, make_user, applicant):
    """The 409 — a different claim from the idempotency above, so a separate test."""
    director = make_user("spine-dir", Role.ADMIN)
    reg_id = applicant("spine.twice@bgscet.ac.in", "Twice Applicant")
    first = client.post(
        f"/api/register/{reg_id}/decision", headers=director.headers, json={"decision": "APPROVE"}
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/register/{reg_id}/decision", headers=director.headers, json={"decision": "APPROVE"}
    )
    assert second.status_code == 409, "a second approval must be refused, not re-provision"


@requires_db
def test_an_application_from_outside_the_college_domain_cannot_be_approved(
    client, make_user, applicant
):
    """The roster is the access control, so approval must not be a way onto it.

    POST /api/register is PUBLIC and unauthenticated, validates the address no
    further than "has an @", and `registrations.email_verified_at` has no writer
    — nobody has proved the applicant can read that mailbox. Since approval mints
    a users row, and google_auth.py admits any verified Google account that HAS
    one, an unguarded Approve is a self-service door into the roster. The
    application looks entirely ordinary in the director's queue, which is what
    makes it dangerous.
    """
    director = make_user("spine-dir-dom", Role.ADMIN)
    reg_id = applicant("attacker@gmail.com", "Priya Sharma")
    r = client.post(
        f"/api/register/{reg_id}/decision", headers=director.headers, json={"decision": "APPROVE"}
    )
    assert r.status_code == 422, r.text
    with SessionLocal() as db:
        assert (
            db.scalar(select(User).where(User.email == "attacker@gmail.com")) is None
        ), "a refused approval must leave no account behind"
        assert (
            db.get(Registration, reg_id).status is RegistrationStatus.PENDING_REVIEW
        ), "a refused approval must leave the application decidable"


@requires_db
def test_an_application_naming_a_staff_address_cannot_be_approved(client, make_user, applicant):
    """Approval must never widen an existing account's reach.

    The User lookup in `_provision_student` is BY EMAIL ALONE, and the duplicate
    check on submit is against registrations.email rather than users.email — so
    an application naming a mentor's address is accepted by the public form and
    lands in the queue looking ordinary. Attaching a Student row to it is not
    cosmetic: `_payload_for` mints `studentId` for any user who has one, so that
    mentor's next session would carry role=MENTOR *and* a studentId. Rule 2 says
    scope is decided by role; this would let an unauthenticated form edit it.
    """
    director = make_user("spine-dir-role", Role.ADMIN)
    mentor = make_user("spine-victim", Role.MENTOR)
    reg_id = applicant(mentor.email, "Impersonator")
    r = client.post(
        f"/api/register/{reg_id}/decision", headers=director.headers, json={"decision": "APPROVE"}
    )
    assert r.status_code == 409, r.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == mentor.email))
        assert user.role is Role.MENTOR, "the victim's role must be untouched"
        assert (
            db.scalar(select(Student).where(Student.user_id == user.id)) is None
        ), "a staff account must not gain a Student row, or their session gains a studentId"


@requires_db
def test_a_provisioned_account_has_no_usable_password(client, make_user, applicant):
    """Provisioning issues no credential.

    The account carries the unusable "google-only" sentinel, which is not a
    "scrypt:<salt>:<digest>" string and so can never match in verify_password.
    Sign-in is Google-only in production, and a password — where wanted — comes
    from activation, not from an admin reading one out.
    """
    from app.routers.registration import SSO_ONLY_PASSWORD_HASH

    director = make_user("spine-dir2", Role.ADMIN)
    email = "spine.nopw@bgscet.ac.in"
    reg_id = applicant(email, "No Password")

    r = client.post(
        f"/api/register/{reg_id}/decision", headers=director.headers, json={"decision": "APPROVE"}
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        assert not user.password_hash.startswith("scrypt:")
        # EQUALITY with the constant, not merely "not scrypt". seed_roster.py
        # branches on `password_hash != SSO_ONLY_PASSWORD_HASH` by exact string
        # compare, so changing this sentinel to another non-scrypt value would
        # pass a "not scrypt" assertion while silently breaking seed_roster's
        # idempotency. The three declarations are pinned equal below.
        assert user.password_hash == SSO_ONLY_PASSWORD_HASH


def test_the_unusable_password_sentinel_is_the_same_string_in_all_three_places():
    """No database. Three modules declare this constant INDEPENDENTLY.

    `registration.py`, `grant_access.py` and `seed_roster.py` each define their
    own, and `seed_roster` compares against it with `!=` — an exact string match.
    Let them drift and seed_roster starts classifying provisioned accounts as
    password accounts, silently. One assert closes a three-way drift.
    """
    from app.grant_access import SSO_ONLY_PASSWORD_HASH as from_grant
    from app.routers.registration import SSO_ONLY_PASSWORD_HASH as from_registration
    from app.seed_roster import SSO_ONLY_PASSWORD_HASH as from_roster
    from app.security import verify_password

    assert from_registration == from_grant == from_roster
    assert not verify_password("anything", from_registration)
    assert not verify_password(from_registration, from_registration)
