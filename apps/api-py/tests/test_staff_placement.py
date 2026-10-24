"""Faculty belong to a department, and through it a college.

THE GAP THIS CLOSES. A student's institution has always been reachable: one
pointer (`students.cohort_id`), and college / department / course / batch read
through the join. Staff had no pointer at all. The only institutional field on a
staff row was `users.department` — FREE TEXT typed into a form — so "Dept of
Management Studies", "DMS" and "Management" were three departments to anything
trying to group by one, and no staff row could name its college at all. With
more than one college in `colleges`, that is the difference between being able
to say which institution a person belongs to and not.

What is pinned here:

  * a faculty member can be filed on creation and afterwards, and the college
    comes back through the join rather than being copied onto the row;
  * an unknown department is REFUSED, because a write that reports success and
    stores null is the failure this whole change exists to remove;
  * `filed` is the flag a screen branches on — a falsy department name cannot
    tell "nobody filed this person" from "a department with a blank name";
  * the free-text line and the pointer never disagree, because the leave form
    prints the one and the console groups by the other;
  * only the Main Admin may file anyone, and only a FACULTY row can be reached.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.auth_token import AuthToken
from app.models.institution import College, Department
from app.models.redesign import AuditEvent
from app.models.user import LoginDay, Mentor, Role, User

API = "/api/admin/faculty"


def _email(label: str) -> str:
    return f"placement-{label}-{uuid.uuid4().hex[:6]}@bgscet.ac.in"


@pytest.fixture
def institution():
    """A throwaway college with two departments, torn down after."""
    suffix = uuid.uuid4().hex[:6].upper()
    with SessionLocal() as db:
        college = College(code=f"PC{suffix}", name=f"Placement College {suffix}")
        db.add(college)
        db.flush()
        first = Department(college_id=college.id, code=f"D1{suffix}", name=f"Management Studies {suffix}")
        second = Department(college_id=college.id, code=f"D2{suffix}", name=f"Computer Science {suffix}")
        db.add_all([first, second])
        db.commit()
        made = {
            "college_id": college.id,
            "college_code": college.code,
            "college_name": college.name,
            "dept_a": first.id,
            "dept_a_name": first.name,
            "dept_b": second.id,
            "dept_b_name": second.name,
        }
    yield made
    with SessionLocal() as db:
        # Staff filed here must be unfiled before the department can go: the FK
        # has no ondelete, which is the point (see the migration).
        db.execute(
            User.__table__.update()
            .where(User.department_id.in_([made["dept_a"], made["dept_b"]]))
            .values(department_id=None)
        )
        db.execute(delete(Department).where(Department.college_id == made["college_id"]))
        db.execute(delete(College).where(College.id == made["college_id"]))
        db.commit()


@pytest.fixture
def swept():
    """Every account created THROUGH the API here, removed afterwards."""
    emails: list[str] = []
    yield emails
    with SessionLocal() as db:
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                continue
            db.execute(
                delete(AuditEvent).where(
                    AuditEvent.entity_type == "faculty", AuditEvent.entity_id == user.id
                )
            )
            db.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
            db.execute(delete(LoginDay).where(LoginDay.user_id == user.id))
            db.execute(delete(Mentor).where(Mentor.user_id == user.id))
            db.execute(delete(User).where(User.id == user.id))
        db.commit()


@requires_db
def test_a_faculty_member_is_filed_on_creation_and_the_college_comes_with_it(
    client, make_user, swept, institution
):
    """The whole point: name a DEPARTMENT, get the COLLEGE back through the join."""
    admin = make_user("place-adm", Role.ADMIN)
    email = _email("filed")
    swept.append(email)

    r = client.post(
        API,
        headers=admin.headers,
        json={
            "name": "Kavya N",
            "email": email,
            "designation": "Assistant Professor",
            "department_id": institution["dept_a"],
        },
    )
    assert r.status_code == 201, r.text
    placement = r.json()["placement"]
    assert placement["filed"] is True
    assert placement["department_id"] == institution["dept_a"]
    assert placement["department_name"] == institution["dept_a_name"]
    # The college was never sent and is never stored — it is reached.
    assert placement["college_id"] == institution["college_id"]
    assert placement["college_code"] == institution["college_code"]

    # And the free-text line the leave form prints agrees with the pointer.
    assert r.json()["department"] == institution["dept_a_name"]


@requires_db
def test_an_account_that_predates_the_rule_is_filed_afterwards(
    client, make_user, swept, institution
):
    """Filing later is not a nicety: the column arrived after the accounts did,
    so without this every existing faculty member is permanently unfiled.

    THE CREATION HALF OF THIS TEST IS GONE (B3.1). It used to POST with no
    `department_id` and assert a 201 with `filed: false`, because the field was
    optional so an admin could mint a login now and file it later. That
    assertion no longer describes the system: the department is how the account
    reaches its COLLEGE, and the college now decides which email domains may
    hold an account (B3.2) and what a scoped grant on this person means (B1.2),
    so an unfiled account is one with no tenant rather than one with a missing
    label. Creation refuses without it — pinned two tests down — and the unfiled
    row here is made the way the ones in production were: directly, before the
    rule existed.
    """
    admin = make_user("place-adm2", Role.ADMIN)
    email = _email("later")
    swept.append(email)

    with SessionLocal() as db:
        legacy = User(email=email, name="Ravi P", role=Role.MENTOR, password_hash="google-only")
        db.add(legacy)
        db.commit()
        user_id = legacy.id

    listed = client.get(API, headers=admin.headers).json()
    assert next(r for r in listed if r["email"] == email)["placement"]["filed"] is False

    filed = client.patch(
        f"{API}/{user_id}",
        headers=admin.headers,
        json={"department_id": institution["dept_a"]},
    )
    assert filed.status_code == 200, filed.text
    assert filed.json()["placement"]["college_code"] == institution["college_code"]

    # Moving between departments works, and the printed line follows.
    moved = client.patch(
        f"{API}/{user_id}", headers=admin.headers, json={"department_id": institution["dept_b"]}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["placement"]["department_id"] == institution["dept_b"]
    assert moved.json()["department"] == institution["dept_b_name"]


@requires_db
def test_an_unknown_department_is_refused_rather_than_filed_under_nothing(
    client, make_user, swept, institution
):
    """A write that reports success and stores null is the bug, not the fix."""
    admin = make_user("place-adm3", Role.ADMIN)
    email = _email("bogus")

    r = client.post(
        API,
        headers=admin.headers,
        json={"name": "Ghost F", "email": email, "department_id": "no-such-department"},
    )
    assert r.status_code == 422, r.text
    assert "no-such-department" in r.text
    # And nothing was created by the refused call.
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == email)) is None

    # The same refusal on the edit path.
    made = client.post(
        API,
        headers=admin.headers,
        json={"name": "Real F", "email": _email("real"), "department_id": institution["dept_a"]},
    )
    swept.append(made.json()["email"])
    bad = client.patch(
        f"{API}/{made.json()['user_id']}",
        headers=admin.headers,
        json={"department_id": "still-not-a-department"},
    )
    assert bad.status_code == 422, bad.text


@requires_db
def test_only_the_main_admin_files_faculty_and_only_faculty_can_be_filed(
    client, make_user, swept, institution
):
    admin = make_user("place-adm4", Role.ADMIN)
    mentor = make_user("place-men", Role.MENTOR)
    alumni = make_user("place-alum", Role.ALUMNI)
    student = make_user("place-stu")

    made = client.post(
        API,
        headers=admin.headers,
        json={"name": "Target F", "email": _email("t"), "department_id": institution["dept_a"]},
    )
    swept.append(made.json()["email"])
    target = made.json()["user_id"]
    body = {"department_id": institution["dept_a"]}

    for who in (mentor, alumni, student):
        assert client.patch(f"{API}/{target}", headers=who.headers, json=body).status_code == 403
        assert client.get(API, headers=who.headers).status_code == 403

    # A STUDENT's user id is not reachable through the faculty endpoint, even
    # for the admin: this writes institutional fields and must not become a way
    # to edit another kind of account.
    assert (
        client.patch(f"{API}/{student.user_id}", headers=admin.headers, json=body).status_code
        == 404
    )


@requires_db
def test_the_faculty_list_puts_the_unfiled_first(client, make_user, swept, institution):
    """The screen's job is to get everyone placed. A list that buries the
    unfiled alphabetically among the filed is a list nobody finishes."""
    admin = make_user("place-adm5", Role.ADMIN)
    filed_email, unfiled_email = _email("aaa-filed"), _email("zzz-unfiled")
    swept.extend([filed_email, unfiled_email])

    client.post(
        API,
        headers=admin.headers,
        json={"name": "AAA Filed", "email": filed_email, "department_id": institution["dept_a"]},
    )
    # UNFILED ROWS ARE NO LONGER CREATABLE THROUGH THE API (B3.1) — the
    # department is required — so the one this test needs is made the way the
    # ones in production were: directly, before the rule existed. The ordering
    # rule it pins is unchanged and still matters, because that backlog is
    # exactly what `?unfiled=true` and this sort exist to work through.
    with SessionLocal() as db:
        db.add(User(email=unfiled_email, name="ZZZ Unfiled", role=Role.MENTOR, password_hash="google-only"))
        db.commit()

    rows = client.get(API, headers=admin.headers).json()
    ours = [r for r in rows if r["email"] in {filed_email, unfiled_email}]
    assert len(ours) == 2
    # Unfiled first even though its name sorts last.
    assert ours[0]["email"] == unfiled_email
    assert ours[0]["placement"]["filed"] is False
    assert ours[1]["placement"]["filed"] is True


@requires_db
def test_mentor_load_carries_the_placement(client, make_user, swept, institution):
    """The Mentors & Students screen is where faculty are managed, so it is
    where the placement has to be visible."""
    admin = make_user("place-adm6", Role.ADMIN)
    email = _email("load")
    swept.append(email)
    made = client.post(
        API,
        headers=admin.headers,
        json={"name": "Load F", "email": email, "department_id": institution["dept_b"]},
    )
    assert made.status_code == 201, made.text

    rows = client.get("/api/admin/mentor-load", headers=admin.headers).json()
    ours = [r for r in rows if r["user_id"] == made.json()["user_id"]]
    assert ours, "the new faculty member is missing from mentor-load"
    assert ours[0]["placement"]["department_id"] == institution["dept_b"]
    assert ours[0]["placement"]["college_code"] == institution["college_code"]
