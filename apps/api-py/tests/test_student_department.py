"""A student belongs to a department, not only to a batch (31f7a4c60b12).

`students.cohort_id` was the only institutional pointer a student had, so the
whole spine hung off a level the registration form does not require. College and
Department are REQUIRED on that form while Course, Specialization and Batch are
optional, which means "named a department, seated in nothing" is the ordinary
state of every college that has not built its batches yet — and provisioning
threw that department away. The result was a student with no college, no
department, an empty profile card, and no department-scoped read that could see
them.

WHAT THESE TESTS PIN, in the order the bug was actually made:

  * the department the applicant was forced to name SURVIVES approval;
  * an unseated student still resolves a College and a Department, with the
    levels below reading `pending` rather than blank;
  * the batch remains the authority when there is one, so the two pointers
    cannot drift apart — derived on write, and a contradiction refused;
  * and the derivation happens on every write that MOVES a student, including
    the batch-wide one whose request body never mentions a department.

The last is the one worth guarding hardest: `move` sets `cohort_id` in a loop,
and a version of it that forgets the department leaves a roster full of students
filed under a department their own batch denies. No screen shows that; only the
FK and this file would.
"""

from __future__ import annotations

import uuid

import pytest

from conftest import requires_db
from test_admin_institution import (  # noqa: F401 — fixtures by name
    _code,
    chain,
    director,
    tracker,
)

from sqlalchemy import delete

from app.db import SessionLocal
from app.models.redesign import AuditEvent
from app.models.student_profile import StudentProfile
from app.models.user import LoginDay, Role, Student, User
from app.routers.student import _institution_for


def _email() -> str:
    return f"deptest-{uuid.uuid4().hex[:8]}@bgscet.ac.in"


@pytest.fixture
def make_student(client, chain):
    """Seed the row, then set the interesting fields through the CONSOLE.

    There is no POST /admin/students any more (2026-09-10): a student account
    is minted only by approving a registration. So the three rows are written
    here exactly as registration's `_provision_student` writes them - User with
    the unusable password sentinel, Student, StudentProfile - and everything
    this module is actually about (which department a student is filed under,
    and who gets the last word on it) is applied through PATCH, which is the
    writer that still exists and which runs the same `_department_or_422`.

    The returned value is the PATCH response, so call sites read unchanged
    apart from expecting 200 where they used to expect 201.
    """
    made: list[str] = []

    def _make(**kw):
        email = _email()
        with SessionLocal() as db:
            user = User(
                email=email,
                name="Dept Test Student",
                role=Role.STUDENT,
                password_hash="google-only",
            )
            db.add(user)
            db.flush()
            student = Student(user_id=user.id)
            db.add(student)
            db.flush()
            db.add(StudentProfile(student_id=student.id, email=email))
            db.commit()
            sid, uid = student.id, user.id
        made.append((sid, uid))
        return client.patch(
            f"/api/admin/students/{sid}", headers=chain["headers"], json=dict(kw)
        )

    yield _make

    with SessionLocal() as db:
        for sid, uid in made:
            db.execute(delete(AuditEvent).where(
                AuditEvent.entity_type == "student", AuditEvent.entity_id == sid))
            db.execute(delete(StudentProfile).where(StudentProfile.student_id == sid))
            db.execute(delete(LoginDay).where(LoginDay.user_id == uid))
            db.execute(delete(Student).where(Student.id == sid))
            db.execute(delete(User).where(User.id == uid))
        db.commit()


@pytest.fixture
def second_department(client, chain, tracker):
    """A second department under the same college, to contradict the first with."""
    r = client.post(
        f"/api/admin/colleges/{chain['college']['id']}/departments",
        headers=chain["headers"],
        json={"code": _code("DEP"), "name": "Other Department"},
    )
    assert r.status_code == 201, r.text
    tracker["departments"].append(r.json()["id"])
    return r.json()


# ------------------------------------------- a student with no batch at all --


@requires_db
def test_an_unseated_student_is_still_filed_under_a_department(client, chain, make_student):
    dept = chain["department"]
    r = make_student(department_id=dept["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cohort_id"] is None, "the point of this test is that there is no batch"
    assert body["department_id"] == dept["id"]
    assert body["department"] == dept["name"], "the roster resolves the NAME through the join"


@requires_db
def test_the_profile_card_resolves_a_college_without_a_batch(client, chain, make_student):
    """The read path, which returned an empty card for exactly this student."""
    r = make_student(department_id=chain["department"]["id"])
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        card = _institution_for(db, db.get(Student, r.json()["student_id"]))
    assert card.college_name == chain["college"]["name"]
    assert card.department_name == chain["department"]["name"]
    assert card.batch_label is None
    states = {lv.key: lv.state for lv in card.levels}
    assert states["college"] == "set"
    assert states["department"] == "set"
    # `pending`, never `not_in_use`: the admin OWES this student a batch, and
    # the two words mean opposite things to whoever reads the card.
    assert states["batch"] == "pending"


@requires_db
def test_an_unfiled_student_still_resolves_to_an_empty_card(client, chain, make_student):
    """No batch AND no department is still legal, and must not raise."""
    r = make_student()
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        card = _institution_for(db, db.get(Student, r.json()["student_id"]))
    assert card.college_name is None
    assert card.department_name is None


# ------------------------------------------------- the batch is the authority --


@requires_db
def test_a_batch_decides_the_department_without_being_asked(client, chain, make_student):
    r = make_student(cohort_id=chain["cohort"]["id"])
    assert r.status_code == 200, r.text
    assert r.json()["department_id"] == chain["department"]["id"], (
        "seating a student derives their department from the batch, so the two "
        "pointers cannot be written apart"
    )


@requires_db
def test_a_department_that_contradicts_the_batch_is_refused(
    client, chain, make_student, second_department
):
    r = make_student(cohort_id=chain["cohort"]["id"], department_id=second_department["id"])
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # Both ids named: an admin cannot tell which of the two fields they typed
    # wrong from the word "invalid".
    assert second_department["id"] in detail
    assert chain["department"]["id"] in detail


@requires_db
def test_a_department_that_does_not_exist_is_refused(client, make_student):
    r = make_student(department_id="no-such-department")
    assert r.status_code == 422, r.text
    assert "no-such-department" in r.json()["detail"]


# ------------------------------------------------------------ moving people --


@requires_db
def test_seating_a_filed_student_hands_the_batch_the_last_word(
    client, chain, make_student, second_department
):
    """Filed under one department, then seated in a batch under another.

    The batch wins — and because the client never mentions a department in this
    request, nothing contradicts and nothing is refused.
    """
    r = make_student(department_id=second_department["id"])
    sid = r.json()["student_id"]
    patched = client.patch(
        f"/api/admin/students/{sid}",
        headers=chain["headers"],
        json={"cohort_id": chain["cohort"]["id"]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["department_id"] == chain["department"]["id"]


@requires_db
def test_unseating_a_student_keeps_the_department(client, chain, make_student):
    """Losing a batch is not losing a faculty. The card must not go blank."""
    r = make_student(cohort_id=chain["cohort"]["id"])
    sid = r.json()["student_id"]
    patched = client.patch(
        f"/api/admin/students/{sid}", headers=chain["headers"], json={"cohort_id": None}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["cohort_id"] is None
    assert patched.json()["department_id"] == chain["department"]["id"]


@requires_db
def test_unseating_a_legacy_row_keeps_the_department_it_showed(client, chain, make_student):
    """The row shape this migration inherits: seated, with a NULL own-pointer.

    Every student written before 31f7a4c60b12 — and anything that sets only
    `cohort_id`, `app.seed` included — has `department_id` NULL and shows its
    department THROUGH the batch. Un-seating one read the fallback AFTER the
    batch had been cleared, found nothing on either pointer, and un-filed them:
    "leave this batch" silently became "leave this department too". Found by
    clicking it in a browser, not by the test above, which only ever built rows
    that already had the pointer set.
    """
    sid = make_student(cohort_id=chain["cohort"]["id"]).json()["student_id"]
    with SessionLocal() as db:
        db.get(Student, sid).department_id = None  # the legacy shape, exactly
        db.commit()

    r = client.patch(
        f"/api/admin/students/{sid}", headers=chain["headers"], json={"cohort_id": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["cohort_id"] is None
    assert r.json()["department_id"] == chain["department"]["id"], (
        "the department the batch was showing survives losing the batch"
    )


@requires_db
def test_a_batch_move_carries_the_department_with_it(
    client, chain, make_student, second_department, tracker
):
    """The bulk path, whose body never mentions a department.

    A `move` that sets only `cohort_id` leaves every student in the batch filed
    under the department the OLD batch sat in. Nothing on any screen shows it.
    """
    destination = client.post(
        f"/api/admin/departments/{second_department['id']}/cohorts",
        headers=chain["headers"],
        json={
            "code": _code("BAT"),
            "name": "Destination Batch",
            "batch_label": "2025-27",
            "degree_level": "PG",
            "entry_date": "2025-08-01",
            "expected_completion": "2027-07-31",
        },
    )
    assert destination.status_code == 201, destination.text
    tracker["cohorts"].append(destination.json()["id"])

    seated = make_student(cohort_id=chain["cohort"]["id"])
    sid = seated.json()["student_id"]
    moved = client.post(
        f"/api/admin/cohorts/{chain['cohort']['id']}/students/bulk",
        headers=chain["headers"],
        json={"action": "move", "cohort_id": destination.json()["id"]},
    )
    assert moved.status_code == 200, moved.text
    with SessionLocal() as db:
        student = db.get(Student, sid)
        assert student.cohort_id == destination.json()["id"]
        assert student.department_id == second_department["id"], (
            "the destination batch's department travels with the move"
        )


# ---------------------------------------------- filing a roster in one go --


@requires_db
def test_the_roster_bulk_files_students_who_have_no_batch(client, chain, make_student):
    """The action production needs: a roster of unseated students, filed at once.

    Every other bulk action is scoped to a cohort, which these students do not
    have — so without this they would be filed one PATCH at a time, and a
    correct feature would go unused.
    """
    ids = [make_student().json()["student_id"] for _ in range(3)]
    r = client.post(
        "/api/admin/students/bulk",
        headers=chain["headers"],
        json={
            "student_ids": ids,
            "action": "department",
            "department_id": chain["department"]["id"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"action": "department", "affected": 3}
    with SessionLocal() as db:
        for sid in ids:
            assert db.get(Student, sid).department_id == chain["department"]["id"]


@requires_db
def test_the_roster_bulk_refuses_the_whole_call_when_a_student_is_gone(client, chain, make_student):
    """Half-applying and reporting a count the admin cannot reconcile is worse
    than refusing."""
    sid = make_student().json()["student_id"]
    r = client.post(
        "/api/admin/students/bulk",
        headers=chain["headers"],
        json={
            "student_ids": [sid, "no-such-student"],
            "action": "department",
            "department_id": chain["department"]["id"],
        },
    )
    assert r.status_code == 404, r.text
    with SessionLocal() as db:
        assert db.get(Student, sid).department_id is None, "nothing was applied"


@requires_db
def test_the_roster_bulk_refuses_a_department_the_batch_denies(
    client, chain, make_student, second_department
):
    """A seated student in the selection is refused rather than quietly skipped."""
    sid = make_student(cohort_id=chain["cohort"]["id"]).json()["student_id"]
    r = client.post(
        "/api/admin/students/bulk",
        headers=chain["headers"],
        json={
            "student_ids": [sid],
            "action": "department",
            "department_id": second_department["id"],
        },
    )
    assert r.status_code == 422, r.text
