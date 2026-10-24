"""Faculty accounts created by the Main Admin on screen.

Pinned: only the Main Admin may create one (DIRECTOR, MENTOR and STUDENT are
refused by name); the account is a MENTOR with no password and no group; an
address already in use is refused; and the activation link the screen shows
really works - redeemed with a password, the new faculty member signs in on
the password door and holds their own account.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select, update

from conftest import requires_db

from app.db import SessionLocal
from app.models.auth_token import AuthToken
from app.models.redesign import AuditEvent
from app.models.institution import College, Department
from app.models.user import LoginDay, Mentor, Role, User

API = "/api/admin/faculty"


@pytest.fixture
def a_department():
    """A throwaway college + department, because a faculty account cannot be
    created without one any more (B3.1). Yields (department_id, name).

    The college records NO `email_domains`, so the address fence falls back to
    the deployment's list — which is what every college gets today and is why
    `@bgscet.ac.in` addresses still sail through here (B3.2, and
    app/institution_domains.py's "the environment is the fallback")."""
    suffix = uuid.uuid4().hex[:6].upper()
    with SessionLocal() as db:
        college = College(code=f"FC{suffix}", name=f"Faculty College {suffix}")
        db.add(college)
        db.flush()
        dep = Department(college_id=college.id, code=f"FD{suffix}", name=f"Faculty Dept {suffix}")
        db.add(dep)
        db.commit()
        made = (dep.id, dep.name, college.id)
    yield made[0], made[1]
    with SessionLocal() as db:
        # `users.department_id` is a real FK with no ON DELETE, and the accounts
        # filed here are swept by a fixture that finalises AFTER this one. Unfile
        # them first rather than reordering fixtures: the pointer is what the
        # constraint protects, and dropping it is what "the department is gone"
        # means for a row that outlives it.
        db.execute(update(User).where(User.department_id == made[0]).values(department_id=None))
        db.execute(delete(Department).where(Department.id == made[0]))
        db.execute(delete(College).where(College.id == made[2]))
        db.commit()


@pytest.fixture
def swept():
    """Accounts created THROUGH the API are new users make_user knows nothing
    about; every address created here is removed afterwards whatever happened."""
    emails: list[str] = []
    yield emails
    with SessionLocal() as db:
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                db.execute(delete(AuditEvent).where(AuditEvent.entity_type == "faculty", AuditEvent.entity_id == user.id))
                db.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
                db.execute(delete(LoginDay).where(LoginDay.user_id == user.id))  # the sign-in at the end writes one
                db.execute(delete(Mentor).where(Mentor.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
        db.commit()


def _email(label: str) -> str:
    return f"faculty-{label}-{uuid.uuid4().hex[:6]}@bgscet.ac.in"


@requires_db
def test_only_the_main_admin_creates_faculty_and_the_link_it_shows_works(client, make_user, swept, a_department):
    dept_id, dept_name = a_department
    admin = make_user("fac-adm", Role.ADMIN)
    mentor = make_user("fac-men", Role.MENTOR)
    alumni = make_user("fac-alum", Role.ALUMNI)
    student = make_user("fac-stu")
    email = _email("new")
    swept.append(email)
    # `department_id` IS REQUIRED NOW (B3.1). It used to be absent here, and the
    # free-text `department` line alone was enough; that no longer describes the
    # system, because the department is how the account reaches its college and
    # the college is what fences the address (B3.2). The free-text line is still
    # sent and is still overwritten by the real department's NAME.
    body = {
        "name": "  Kavya   N ",
        "email": email.upper(),
        "designation": "Assistant Professor",
        "department": "MBA",
        "department_id": dept_id,
    }

    # A DIRECTOR used to be the interesting case here — the role with the most
    # access that still could not mint an account. There is no DIRECTOR any
    # more (tests/test_no_director_privilege.py), so the set is every other role
    # the product actually has.
    for who in (mentor, alumni, student):
        r = client.post(API, headers=who.headers, json=body)
        assert r.status_code == 403 and "Main Admin" in r.text, r.text

    r = client.post(API, headers=admin.headers, json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["name"] == "Kavya N" and out["email"] == email
    assert out["designation"] == "Assistant Professor"
    assert out["department"] == dept_name, "the real department's NAME overwrites the typed line"
    assert "/activate?token=" in out["activation_link"] and out["expires_in_hours"] == 168
    assert out["emailed"] is False, "no transport in the suite: the screen hands the link over"

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user.role is Role.MENTOR and user.password_hash == "google-only", "no password until the link is redeemed"
        assert db.scalar(select(Mentor).where(Mentor.user_id == user.id)) is None, "a group comes with the first assignment"
        ev = db.scalar(select(AuditEvent).where(AuditEvent.entity_type == "faculty", AuditEvent.entity_id == user.id))
        assert ev.action == "CREATE" and ev.after_json["email"] == email and ev.actor_user_id == admin.user_id

    # The same address again, or anyone's address: refused.
    assert client.post(API, headers=admin.headers, json=body).status_code == 409
    r = client.post(API, headers=admin.headers, json={"name": "X", "email": student.email, "department_id": dept_id})
    assert r.status_code == 409 and "STUDENT account" in r.text
    assert client.post(API, headers=admin.headers, json={"name": "X", "email": "not-an-address", "department_id": dept_id}).status_code == 422
    assert client.post(API, headers=admin.headers, json={"name": "   ", "email": _email("blank"), "department_id": dept_id}).status_code == 422

    # They appear where the Main Admin assigns students, with no group yet.
    row = next(m for m in client.get("/api/admin/mentor-load", headers=admin.headers).json() if m["user_id"] == user.id)
    assert row["mentor_id"] is None and row["name"] == "Kavya N"

    # The link works: a password is set from it and the account signs in.
    token = out["activation_link"].split("token=", 1)[1]
    r = client.post("/api/auth/activate", json={"token": token, "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "MENTOR"
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email, "password": "correct horse battery"})
    assert r.status_code == 200 and r.json()["email"] == email
    client.cookies.clear()
