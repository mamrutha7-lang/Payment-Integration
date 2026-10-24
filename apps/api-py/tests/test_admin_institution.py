"""The Main Admin institutional write layer: `app/routers/admin.py`.

WHY THIS FILE EXISTS. `admin.py` shipped with 11 operations, no tests and no
frontend caller. Three independent reviews of the institutional spine put the
same item at the top of all three lists, and one of them found a live 500 in
three of the endpoints just by reading them — a cleared form field sends
`{"name": null}`, which `exclude_unset` keeps and `setattr` then writes to a NOT
NULL column. Eleven `require_admin` calls, none of them proven to run before
the work happens, is not a surface anyone should deploy.

WHAT IT PINS, in the order the reviewers ranked the danger:

  * Every operation refuses a non-director. Rule 2 says scope is decided by
    role; that only holds if the check is reached.
  * `{"name": null}` is a 422 and not an IntegrityError-shaped 500.
  * Seating a student changes THAT student's card and nobody else's.
  * `department_id` has a writer, and cohorts that have no department are
    reachable — without both, every cohort predating this feature is stranded
    where the console cannot see or fix it.
  * The uniqueness rules are the ones intended: college code global, department
    code per-college, so two colleges may each run a "CSE".
"""

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.cohort import Cohort
from app.models.institution import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
    HierarchyLevel,
)
from app.models.student_profile import StudentProfile
from app.models.user import Role, Student, User


def _code(prefix: str) -> str:
    """Codes are unique columns, so every test needs its own or the second run
    of the suite fails on rows the first one left behind."""
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


@pytest.fixture
def director(make_user):
    return make_user("admin-dir", Role.ADMIN)


@pytest.fixture
def tracker():
    """Deletes what a test created, innermost first, WHATEVER the test does.

    Cohorts before departments before colleges: these are real foreign keys with
    no ondelete, so the database refuses any other order — which is the point of
    them.
    """
    made: dict[str, list[str]] = {
        "cohorts": [],
        "specializations": [],
        "courses": [],
        "departments": [],
        "colleges": [],
    }
    yield made
    with SessionLocal() as db:
        for cid in made["cohorts"]:
            db.execute(
                Student.__table__.update().where(Student.cohort_id == cid).values(cohort_id=None)
            )
            db.execute(delete(Cohort).where(Cohort.id == cid))
        for sid in made["specializations"]:
            db.execute(delete(AcademicSpecialization).where(AcademicSpecialization.id == sid))
        for cid in made["courses"]:
            db.execute(delete(AcademicCourse).where(AcademicCourse.id == cid))
        for did in made["departments"]:
            # Both department pointers are released first, for the same reason
            # `cohort_id` is above: neither FK has an ondelete, so the database
            # refuses to drop a department anyone is still filed under. Students
            # gained theirs in 31f7a4c60b12 and staff in 31ca99852acd — a
            # teardown that knows about only one of them fails the moment a test
            # provisions a student, which is exactly how this surfaced.
            db.execute(
                Student.__table__.update().where(Student.department_id == did).values(department_id=None)
            )
            db.execute(
                User.__table__.update().where(User.department_id == did).values(department_id=None)
            )
            db.execute(delete(Department).where(Department.id == did))
        for cid in made["colleges"]:
            db.execute(delete(College).where(College.id == cid))
        db.commit()


@pytest.fixture
def chain(client, director, tracker):
    """A College -> Department -> Cohort built THROUGH THE API.

    Deliberately not through SessionLocal. The spine tests build their fixture
    with direct inserts, which is how the missing `department_id` writer went
    unnoticed: the chain existed in every test and could not be created by any
    admin. Building it through the endpoints means the fixture itself fails if
    the console cannot do this.
    """
    h = director.headers
    college = client.post(
        "/api/admin/colleges",
        headers=h,
        json={"code": _code("COL"), "name": "Chain College", "campus": "Bengaluru"},
    ).json()
    tracker["colleges"].append(college["id"])

    dept = client.post(
        f"/api/admin/colleges/{college['id']}/departments",
        headers=h,
        json={"code": _code("DEP"), "name": "Chain Department"},
    ).json()
    tracker["departments"].append(dept["id"])

    cohort = client.post(
        f"/api/admin/departments/{dept['id']}/cohorts",
        headers=h,
        json={
            "code": _code("BAT"),
            "name": "Chain Batch",
            "batch_label": "2024-26",
            "degree_level": "PG",
            "entry_date": "2024-08-01",
            "expected_completion": "2026-07-31",
        },
    ).json()
    tracker["cohorts"].append(cohort["id"])
    return {"college": college, "department": dept, "cohort": cohort, "headers": h}


# ------------------------------------------------------------ the role gate --


@requires_db
def test_every_admin_operation_refuses_a_student(client, make_user, chain):
    """Every operation, one test, because the failure is identical in each.

    A per-endpoint test would be eleven near-copies; what matters is that NO
    path reaches its work without `require_admin`, and that is a property of
    the set. Enumerated explicitly rather than walked off the router, so adding
    an endpoint without a line here is a visible omission rather than silently
    covered by a loop.
    """
    student = make_user("admin-stu", Role.STUDENT)
    h = student.headers
    college_id = chain["college"]["id"]
    dept_id = chain["department"]["id"]
    cohort_id = chain["cohort"]["id"]

    calls = [
        ("get", "/api/admin/colleges", None),
        ("post", "/api/admin/colleges", {"code": "X", "name": "X"}),
        ("patch", f"/api/admin/colleges/{college_id}", {"name": "X"}),
        ("get", f"/api/admin/colleges/{college_id}/departments", None),
        # The flat picker: every department with its college, for filing a
        # faculty member without asking them to pick a college first.
        ("get", "/api/admin/departments", None),
        ("post", f"/api/admin/colleges/{college_id}/departments", {"code": "X", "name": "X"}),
        ("patch", f"/api/admin/departments/{dept_id}", {"name": "X"}),
        ("get", f"/api/admin/departments/{dept_id}/cohorts", None),
        ("get", "/api/admin/cohorts/unassigned", None),
        (
            "post",
            f"/api/admin/departments/{dept_id}/cohorts",
            {
                "code": "X",
                "name": "X",
                "batch_label": "X",
                "degree_level": "PG",
                "entry_date": "2024-08-01",
                "expected_completion": "2026-07-31",
            },
        ),
        ("patch", f"/api/admin/cohorts/{cohort_id}", {"name": "X"}),
        ("get", f"/api/admin/cohorts/{cohort_id}/students", None),
        ("get", "/api/admin/students/unseated", None),
        ("put", "/api/admin/students/whoever/cohort", {"cohort_id": None}),
        ("patch", "/api/admin/users/whoever/institutional-identity", {"designation": "X"}),
        # The two optional levels and the switch.
        ("get", "/api/admin/hierarchy/levels", None),
        ("get", "/api/admin/cohorts/incomplete", None),
        ("get", f"/api/admin/departments/{dept_id}/academic-courses", None),
        ("post", f"/api/admin/departments/{dept_id}/academic-courses", {"code": "X", "name": "X"}),
        ("patch", "/api/admin/academic-courses/whoever", {"name": "X"}),
        ("get", "/api/admin/academic-courses/whoever/academic-specializations", None),
        (
            "post",
            "/api/admin/academic-courses/whoever/academic-specializations",
            {"code": "X", "name": "X"},
        ),
        ("patch", "/api/admin/academic-specializations/whoever", {"name": "X"}),
        ("post", "/api/admin/users/whoever/activation-link", {}),
        # B1.3 — the college-admin appointment. Gated on `require_governance`
        # rather than this module's `admin.institution` (it reads and writes who
        # holds what, which is Governance's subject), so the refusal a STUDENT
        # gets here comes from `policies.require_staff` rather than from
        # `require_admin` — the same 403, from the gate that owns the act.
        ("get", f"/api/admin/colleges/{college_id}/admins", None),
        (
            "post",
            f"/api/admin/colleges/{college_id}/admins",
            {"user_id": "whoever", "reason": "a reason long enough to pass the floor"},
        ),
    ]
    # Pinned to the router, so a new operation without a line above is a
    # visible failure here rather than silently unguarded.
    from app.routers.admin import router as admin_router

    assert len(calls) == len(admin_router.routes), (
        f"admin.py has {len(admin_router.routes)} operations but this test enumerates "
        f"{len(calls)}. Add the new one — every operation must be proven to refuse a STUDENT."
    )
    for method, url, body in calls:
        r = getattr(client, method)(url, headers=h, **({"json": body} if body else {}))
        assert r.status_code == 403, f"{method.upper()} {url} admitted a STUDENT ({r.status_code})"

    with SessionLocal() as db:
        assert db.scalar(select(College).where(College.name == "X")) is None, (
            "a refused request still wrote a row"
        )


# ------------------------------------------------- the cleared-field 500 --


@requires_db
@pytest.mark.parametrize(
    "url_key, field",
    [
        ("college", "name"),
        ("college", "code"),
        ("department", "name"),
        ("cohort", "batch_label"),
        ("cohort", "entry_date"),
    ],
)
def test_clearing_a_not_null_field_is_refused_rather_than_crashing(client, chain, url_key, field):
    """`{"name": null}` is what a form sends when someone clears an input.

    Before PatchModel every PATCH here took that literally: `exclude_unset`
    keeps an explicit null, `setattr` wrote it to a NOT NULL column, and the
    response was an unhandled IntegrityError — a 500 with a Postgres constraint
    name in the body. 422 says which field and why.
    """
    paths = {
        "college": f"/api/admin/colleges/{chain['college']['id']}",
        "department": f"/api/admin/departments/{chain['department']['id']}",
        "cohort": f"/api/admin/cohorts/{chain['cohort']['id']}",
    }
    r = client.patch(paths[url_key], headers=chain["headers"], json={field: None})
    assert r.status_code == 422, f"clearing {url_key}.{field} returned {r.status_code}"


@requires_db
def test_a_nullable_field_can_still_be_cleared(client, chain):
    """The guard above must not become "no field may ever be nulled".

    `campus` and `contact` are nullable columns, and sending null is how they
    are cleared. A blanket refusal would remove the only way to unset them.
    """
    r = client.patch(
        f"/api/admin/colleges/{chain['college']['id']}",
        headers=chain["headers"],
        json={"campus": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["campus"] is None


# ------------------------------------------------------------- uniqueness --


@requires_db
def test_a_college_code_is_normalised_and_unique(client, director, tracker):
    """Uppercased on the way in, so "bgscet" and "BGSCET" are one college.

    Without the normalisation the unique constraint is satisfied by case alone
    and the college exists twice — with every downstream reference ambiguous,
    which is the reason the column is unique at all.
    """
    code = _code("UNI")
    first = client.post(
        "/api/admin/colleges", headers=director.headers, json={"code": code.lower(), "name": "First"}
    )
    assert first.status_code == 201, first.text
    tracker["colleges"].append(first.json()["id"])
    assert first.json()["code"] == code, "the code was not uppercased"

    second = client.post(
        "/api/admin/colleges", headers=director.headers, json={"code": code, "name": "Second"}
    )
    assert second.status_code == 409, "a duplicate college code must be refused"


@requires_db
def test_two_colleges_may_each_have_a_department_with_the_same_code(client, director, tracker):
    """Department codes are unique PER COLLEGE, not globally.

    Two colleges each running a "CSE" is the normal case, and forcing them to
    differ pushes the college name into the department code. A mutation to
    global uniqueness breaks exactly this and nothing else notices.
    """
    h = director.headers
    shared = _code("CSE")
    for label in ("A", "B"):
        college = client.post(
            "/api/admin/colleges", headers=h, json={"code": _code("MC"), "name": f"College {label}"}
        ).json()
        tracker["colleges"].append(college["id"])
        r = client.post(
            f"/api/admin/colleges/{college['id']}/departments",
            headers=h,
            json={"code": shared, "name": "Computer Science"},
        )
        assert r.status_code == 201, f"college {label} could not use the shared code: {r.text}"
        tracker["departments"].append(r.json()["id"])

    # ...but twice within ONE college is still refused.
    dup = client.post(
        f"/api/admin/colleges/{college['id']}/departments",
        headers=h,
        json={"code": shared, "name": "Computer Science Again"},
    )
    assert dup.status_code == 409, "a department code must still be unique within its college"


# ------------------------------------------------------------ the batch --


@requires_db
def test_a_batch_must_finish_after_it_starts(client, chain):
    r = client.post(
        f"/api/admin/departments/{chain['department']['id']}/cohorts",
        headers=chain["headers"],
        json={
            "code": _code("BAD"),
            "name": "Backwards",
            "batch_label": "2026-24",
            "degree_level": "PG",
            "entry_date": "2026-07-31",
            "expected_completion": "2024-08-01",
        },
    )
    assert r.status_code == 422, r.text


@requires_db
def test_an_unassigned_batch_is_visible_and_can_be_filed(client, chain, tracker):
    """The console must be able to see and fix a batch with no department.

    THIS IS THE GAP THAT MADE THE FEATURE UNREACHABLE. `department_id` had one
    writer — cohort creation — so every cohort predating this work had it NULL,
    and because the only cohort listing is keyed on a department path segment, a
    NULL-department cohort matched no route: invisible, and therefore impossible
    to file. The seeded cohort was in exactly that state, which is why a fresh
    database showed dashes for College and Department on a card headed "verified
    by Main Admin".
    """
    h = chain["headers"]
    cohort_id = chain["cohort"]["id"]

    # Un-seat it: an explicit null is the release action.
    released = client.patch(
        f"/api/admin/cohorts/{cohort_id}", headers=h, json={"department_id": None}
    )
    assert released.status_code == 200, released.text
    assert released.json()["department_id"] is None

    listed = client.get("/api/admin/cohorts/unassigned", headers=h)
    assert listed.status_code == 200, listed.text
    assert cohort_id in [c["id"] for c in listed.json()], (
        "a batch with no department is invisible to the console and cannot be filed"
    )

    # File it back, and it leaves the unassigned list.
    refiled = client.patch(
        f"/api/admin/cohorts/{cohort_id}",
        headers=h,
        json={"department_id": chain["department"]["id"]},
    )
    assert refiled.status_code == 200, refiled.text
    assert refiled.json()["department_id"] == chain["department"]["id"]
    assert cohort_id not in [
        c["id"] for c in client.get("/api/admin/cohorts/unassigned", headers=h).json()
    ]


@requires_db
def test_filing_a_batch_under_a_department_that_does_not_exist_is_a_404(client, chain):
    """Not an IntegrityError. `department_id` is a real FK, so an unchecked bad
    value is a 500 with a constraint name; this surface answers 404."""
    r = client.patch(
        f"/api/admin/cohorts/{chain['cohort']['id']}",
        headers=chain["headers"],
        json={"department_id": "no-such-department"},
    )
    assert r.status_code == 404, r.text


# --------------------------------------------------------- seating a student --


@requires_db
def test_seating_a_student_populates_their_card_and_nobody_elses(
    client, make_user, director, chain
):
    """The write the locked profile card depends on, end to end.

    Also the isolation: `PUT /students/{id}/cohort` names a student in the path,
    and a resolver that scanned for "the newest cohort" instead of following
    `students.cohort_id` would pass a single-student test and put one student's
    batch on every card in the programme.
    """
    seated = make_user("admin-seated", Role.STUDENT)
    other = make_user("admin-other", Role.STUDENT)

    with SessionLocal() as db:
        seated_id = db.scalar(select(Student).where(Student.user_id == seated.user_id)).id
        other_id = db.scalar(select(Student).where(Student.user_id == other.user_id)).id
        # make_user creates a Student but NOT a StudentProfile, and
        # GET /api/student/profile 404s without one — so the card would be
        # unreachable and this test would fail on a KeyError rather than on
        # anything it means to assert. The profile rows cascade with the
        # students, so make_user's own finaliser still cleans them up.
        db.add_all(
            [
                StudentProfile(student_id=seated_id, email=seated.email),
                StudentProfile(student_id=other_id, email=other.email),
            ]
        )
        db.commit()

    r = client.put(
        f"/api/admin/students/{seated_id}/cohort",
        headers=director.headers,
        json={"cohort_id": chain["cohort"]["id"]},
    )
    assert r.status_code == 204, r.text

    card = client.get("/api/student/profile", headers=seated.headers).json()["institution"]
    assert card["college_name"] == "Chain College"
    assert card["department_name"] == "Chain Department"
    assert card["batch_label"] == "2024-26"
    assert card["entry_date"] == "2024-08-01"

    others_card = client.get("/api/student/profile", headers=other.headers).json()["institution"]
    assert others_card["batch_label"] is None, "seating one student filled in another's card"

    # And the release path: an explicit null empties the card again.
    assert (
        client.put(
            f"/api/admin/students/{seated_id}/cohort",
            headers=director.headers,
            json={"cohort_id": None},
        ).status_code
        == 204
    )
    after = client.get("/api/student/profile", headers=seated.headers).json()["institution"]
    assert after["batch_label"] is None


@requires_db
def test_the_seating_reads_agree_with_the_write(client, make_user, director, chain):
    """The two lists the panel is built on move in lockstep with PUT.

    A student is in exactly one of the two: `unseated`, or some batch's
    `students`. Seat -> leaves the pool and appears under the batch; release ->
    the reverse. The write was tested on its own; without these reads a screen
    had nothing to show, and a read that disagreed with the write would show
    a student in two places or none.
    """
    stu = make_user("admin-seat-reads", Role.STUDENT)
    with SessionLocal() as db:
        sid = db.scalar(select(Student).where(Student.user_id == stu.user_id)).id
    h = director.headers
    batch = chain["cohort"]["id"]

    def ids(path: str) -> set[str]:
        r = client.get(path, headers=h)
        assert r.status_code == 200, r.text
        return {row["student_id"] for row in r.json()}

    assert sid in ids("/api/admin/students/unseated")
    assert sid not in ids(f"/api/admin/cohorts/{batch}/students")

    assert client.put(f"/api/admin/students/{sid}/cohort", headers=h, json={"cohort_id": batch}).status_code == 204
    assert sid not in ids("/api/admin/students/unseated"), "seated, but still in the pool"
    seated = client.get(f"/api/admin/cohorts/{batch}/students", headers=h).json()
    row = next(r for r in seated if r["student_id"] == sid)
    assert row["email"] == stu.email and row["cohort_id"] == batch

    assert client.put(f"/api/admin/students/{sid}/cohort", headers=h, json={"cohort_id": None}).status_code == 204
    assert sid in ids("/api/admin/students/unseated")
    assert sid not in ids(f"/api/admin/cohorts/{batch}/students")

    assert client.get("/api/admin/cohorts/no-such-batch/students", headers=h).status_code == 404


@requires_db
def test_seating_a_student_in_a_batch_that_does_not_exist_is_a_404(client, director, make_user):
    stu = make_user("admin-bad-seat", Role.STUDENT)
    with SessionLocal() as db:
        sid = db.scalar(select(Student).where(Student.user_id == stu.user_id)).id
    r = client.put(
        f"/api/admin/students/{sid}/cohort",
        headers=director.headers,
        json={"cohort_id": "no-such-batch"},
    )
    assert r.status_code == 404, r.text
    with SessionLocal() as db:
        assert db.get(Student, sid).cohort_id is None, "a refused seating still wrote"


# --------------------------------------------------------------- archiving --


@requires_db
def test_archiving_and_restoring_a_college_are_the_same_operation(client, chain):
    """Archive is a status change, not a delete — and it is reversible.

    The docstring on this router says "ARCHIVE, NEVER DELETE"; an archive that
    could not be undone would make a mis-click permanent on a row that student
    records hang off.
    """
    h = chain["headers"]
    url = f"/api/admin/colleges/{chain['college']['id']}"
    archived = client.patch(url, headers=h, json={"status": STATUS_ARCHIVED})
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == STATUS_ARCHIVED

    listed = client.get("/api/admin/colleges", headers=h).json()
    assert chain["college"]["id"] in [c["id"] for c in listed], (
        "an archived college vanished from the list — archive is not delete, and "
        "a row that cannot be seen cannot be restored"
    )

    restored = client.patch(url, headers=h, json={"status": STATUS_ACTIVE})
    assert restored.status_code == 200
    assert restored.json()["status"] == STATUS_ACTIVE


@requires_db
def test_an_unknown_status_is_refused(client, chain):
    r = client.patch(
        f"/api/admin/colleges/{chain['college']['id']}",
        headers=chain["headers"],
        json={"status": "DELETED"},
    )
    assert r.status_code == 422, r.text


# ------------------------------------------- the two columns with no writer --


@requires_db
def test_designation_and_department_are_written_and_can_be_cleared(client, director, make_user):
    """The entire reason this endpoint exists.

    `users.designation` and `users.department` are READ by the BGSCET leave
    form, which labels them "(synced)", and by the director's mentor-load
    screen. Neither had a writer anywhere — not an endpoint, not a CLI, not a
    seed — so "(synced)" was a promise nothing kept and both rendered null
    forever. There was no assertion that this write lands.
    """
    mentor = make_user("admin-ident", Role.MENTOR)
    url = f"/api/admin/users/{mentor.user_id}/institutional-identity"

    r = client.patch(
        url,
        headers=director.headers,
        json={"designation": "Associate Professor", "department": "MBA"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["designation"] == "Associate Professor"

    with SessionLocal() as db:
        user = db.get(User, mentor.user_id)
        assert user.designation == "Associate Professor", "the column was not written"
        assert user.department == "MBA"

    # An empty string is a clearing, not a value: the leave form should say
    # "not on record" rather than print a blank line.
    cleared = client.patch(url, headers=director.headers, json={"designation": ""})
    assert cleared.status_code == 200
    assert cleared.json()["designation"] is None
    with SessionLocal() as db:
        assert db.get(User, mentor.user_id).designation is None
        assert db.get(User, mentor.user_id).department == "MBA", "an omitted field was changed"


@requires_db
def test_setting_an_identity_on_an_unknown_user_is_a_404(client, director):
    r = client.patch(
        "/api/admin/users/no-such-user/institutional-identity",
        headers=director.headers,
        json={"designation": "Professor"},
    )
    assert r.status_code == 404, r.text


# ------------------------------------------- the two optional levels --


@pytest.fixture
def levels(client, chain, tracker):
    """A Course and a Specialization under the chain's department, via the API."""
    h = chain["headers"]
    course = client.post(
        f"/api/admin/departments/{chain['department']['id']}/academic-courses",
        headers=h,
        json={"code": _code("MBA"), "name": "Master of Business Administration", "duration_months": 24},
    )
    assert course.status_code == 201, course.text
    tracker["courses"].append(course.json()["id"])
    spec = client.post(
        f"/api/admin/academic-courses/{course.json()['id']}/academic-specializations",
        headers=h,
        json={"code": _code("FIN"), "name": "Finance"},
    )
    assert spec.status_code == 201, spec.text
    tracker["specializations"].append(spec.json()["id"])
    return {**chain, "course": course.json(), "specialization": spec.json()}


@requires_db
def test_the_switch_is_served_to_the_form_from_the_same_constant(client, director):
    """Reader 2 of HIERARCHY_LEVELS. The form builds its validators from this,
    so flipping the constant reaches the form with no second edit."""
    from app.models.institution import HIERARCHY_LEVELS

    r = client.get("/api/admin/hierarchy/levels", headers=director.headers)
    assert r.status_code == 200, r.text
    assert r.json() == [lv._asdict() for lv in HIERARCHY_LEVELS]
    assert [lv["key"] for lv in r.json()] == ["course", "specialization"]


@requires_db
def test_a_batch_names_the_deepest_level_and_the_ancestors_are_derived(client, levels, tracker):
    """The single-writer contract: send specialization_id, get course_id and
    department_id filled in from the real foreign keys, not from the client."""
    r = client.post(
        f"/api/admin/departments/{levels['department']['id']}/cohorts",
        headers=levels["headers"],
        json={
            "code": _code("BAT"),
            "name": "Derived Batch",
            "batch_label": "2025-27",
            "degree_level": "PG",
            "entry_date": "2025-08-01",
            "expected_completion": "2027-07-31",
            "specialization_id": levels["specialization"]["id"],
        },
    )
    assert r.status_code == 201, r.text
    tracker["cohorts"].append(r.json()["id"])
    assert r.json()["specialization_id"] == levels["specialization"]["id"]
    assert r.json()["course_id"] == levels["course"]["id"], "course_id was not derived"
    assert r.json()["department_id"] == levels["department"]["id"]
    assert r.json()["missing_levels"] == []


@requires_db
def test_a_course_under_another_department_is_a_contradiction_not_a_silent_pick(
    client, director, levels, tracker
):
    """THE SPLIT-BRAIN GUARD. Three parent pointers is three chances to
    disagree, and the disagreement surfaces as a profile card printing the
    wrong department under "verified by Main Admin". So a course filed under
    department A, posted to department B's batch endpoint, is a 422 naming
    both — never stored with B beside A's course."""
    h = director.headers
    other_college = client.post(
        "/api/admin/colleges", headers=h, json={"code": _code("OC"), "name": "Other College"}
    ).json()
    tracker["colleges"].append(other_college["id"])
    other_dept = client.post(
        f"/api/admin/colleges/{other_college['id']}/departments",
        headers=h,
        json={"code": _code("OD"), "name": "Other Department"},
    ).json()
    tracker["departments"].append(other_dept["id"])

    r = client.post(
        f"/api/admin/departments/{other_dept['id']}/cohorts",  # department B ...
        headers=h,
        json={
            "code": _code("BAD"),
            "name": "Contradiction",
            "batch_label": "2025-27",
            "degree_level": "PG",
            "entry_date": "2025-08-01",
            "expected_completion": "2027-07-31",
            "course_id": levels["course"]["id"],  # ... with department A's course
        },
    )
    assert r.status_code == 422, r.text
    assert "contradicts" in r.json()["detail"]


@requires_db
def test_clearing_a_course_while_a_specialization_points_at_it_is_refused(
    client, levels, tracker
):
    """"No course" cannot coexist with a specialization that belongs to one."""
    h = levels["headers"]
    created = client.post(
        f"/api/admin/departments/{levels['department']['id']}/cohorts",
        headers=h,
        json={
            "code": _code("BAT"),
            "name": "Seated",
            "batch_label": "2025-27",
            "degree_level": "PG",
            "entry_date": "2025-08-01",
            "expected_completion": "2027-07-31",
            "specialization_id": levels["specialization"]["id"],
        },
    ).json()
    tracker["cohorts"].append(created["id"])

    r = client.patch(f"/api/admin/cohorts/{created['id']}", headers=h, json={"course_id": None})
    assert r.status_code == 422, r.text
    # Clearing from the DEEPEST level is the supported way down.
    r = client.patch(
        f"/api/admin/cohorts/{created['id']}", headers=h, json={"specialization_id": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["specialization_id"] is None
    assert r.json()["course_id"] == levels["course"]["id"], "clearing the leaf must not clear its parent"


@requires_db
def test_the_flip_refuses_new_batches_but_never_bricks_old_ones(
    client, levels, tracker, monkeypatch
):
    """THE SWITCH, end to end, on the admin side.

    Three things must be true the morning after `required=True` is deployed,
    and one test holds all three because they are one contract:

      1. a NEW batch without the level is refused (422, naming it);
      2. an OLD batch missing it still appears in /cohorts/incomplete, flagged
         in `missing_levels` — visible, so it can be fixed;
      3. that old batch is STILL EDITABLE: a PATCH that does not touch the
         missing level succeeds, and a PATCH that fills it in succeeds. Only a
         PATCH that would WIDEN the gap is refused.

    A switch that makes existing data un-editable is a trap, and the fix would
    be to flip it back.
    """
    from app.models import institution as institution_model

    h = levels["headers"]
    dept = levels["department"]["id"]

    # An old batch, created while the levels were optional: no course.
    legacy = client.post(
        f"/api/admin/departments/{dept}/cohorts",
        headers=h,
        json={
            "code": _code("OLD"),
            "name": "Legacy Batch",
            "batch_label": "2023-25",
            "degree_level": "PG",
            "entry_date": "2023-08-01",
            "expected_completion": "2025-07-31",
        },
    )
    assert legacy.status_code == 201, legacy.text
    legacy_id = legacy.json()["id"]
    tracker["cohorts"].append(legacy_id)
    assert client.get("/api/admin/cohorts/incomplete", headers=h).json() == [], (
        "before the flip nothing is required, so nothing is incomplete"
    )

    # --- the one-line change, made on the model module -----------------------
    flipped = tuple(
        HierarchyLevel(lv.key, lv.label, lv.field, required=(lv.key == "course"))
        for lv in institution_model.HIERARCHY_LEVELS
    )
    monkeypatch.setattr(institution_model, "HIERARCHY_LEVELS", flipped)

    # 1. NEW batches are refused, and the form learns why from the same place.
    refused = client.post(
        f"/api/admin/departments/{dept}/cohorts",
        headers=h,
        json={
            "code": _code("NEW"),
            "name": "New Batch",
            "batch_label": "2026-28",
            "degree_level": "PG",
            "entry_date": "2026-08-01",
            "expected_completion": "2028-07-31",
        },
    )
    assert refused.status_code == 422, refused.text
    assert "Course" in refused.text
    served = client.get("/api/admin/hierarchy/levels", headers=h).json()
    assert {lv["key"]: lv["required"] for lv in served} == {"course": True, "specialization": False}

    # 2. The OLD batch is visible and flagged.
    incomplete = client.get("/api/admin/cohorts/incomplete", headers=h).json()
    assert legacy_id in [c["id"] for c in incomplete]
    assert next(c for c in incomplete if c["id"] == legacy_id)["missing_levels"] == ["Course"]

    # 3a. Still editable: an edit that does not touch the gap succeeds.
    renamed = client.patch(
        f"/api/admin/cohorts/{legacy_id}", headers=h, json={"name": "Legacy Batch (renamed)"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["missing_levels"] == ["Course"], "the gap is unchanged, and still flagged"

    # 3b. Filling the gap in is always allowed, and clears the flag.
    filled = client.patch(
        f"/api/admin/cohorts/{legacy_id}", headers=h, json={"course_id": levels["course"]["id"]}
    )
    assert filled.status_code == 200, filled.text
    assert filled.json()["missing_levels"] == []
    assert legacy_id not in [
        c["id"] for c in client.get("/api/admin/cohorts/incomplete", headers=h).json()
    ]

    # 3c. WIDENING the gap on a now-compliant batch is refused.
    widened = client.patch(f"/api/admin/cohorts/{legacy_id}", headers=h, json={"course_id": None})
    assert widened.status_code == 422, widened.text
    assert "required" in widened.json()["detail"]


@requires_db
def test_course_codes_are_unique_per_department_and_specializations_per_course(
    client, levels, tracker
):
    h = levels["headers"]
    dup_course = client.post(
        f"/api/admin/departments/{levels['department']['id']}/academic-courses",
        headers=h,
        json={"code": levels["course"]["code"], "name": "Again"},
    )
    assert dup_course.status_code == 409
    dup_spec = client.post(
        f"/api/admin/academic-courses/{levels['course']['id']}/academic-specializations",
        headers=h,
        json={"code": levels["specialization"]["code"], "name": "Again"},
    )
    assert dup_spec.status_code == 409


@requires_db
def test_the_new_levels_record_who_created_them(client, director, levels):
    """`created_by_user_id` is the audit column the console's Governance tab
    reads. Set from the session, never from the client."""
    with SessionLocal() as db:
        course = db.get(AcademicCourse, levels["course"]["id"])
        spec = db.get(AcademicSpecialization, levels["specialization"]["id"])
        college = db.get(College, levels["college"]["id"])
        assert course.created_by_user_id == director.user_id
        assert spec.created_by_user_id == director.user_id
        assert college.created_by_user_id == director.user_id
