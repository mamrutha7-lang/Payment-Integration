"""`grant_access` files a new account, or refuses to create one.

This tool was the last door into the roster that placed nobody, and it placed
nobody SILENTLY. The console already refuses -- `AdminFacultyIn.department_id`
is `str` with no default -- and the public registration form requires College
and Department. Only the ops task could mint an account belonging to no
institution at all.

WHY THAT MATTERS MORE THAN IT LOOKS. A student with no department resolves to
no college and no batch, so the interview policy carries no `default_track`, so
the assistant's picker stays on its first option -- "General interview" -- and a
general interview has no wrap-up phase, so it can NEVER produce a scorecard.
Observed in production on 2026-09-15: one granted test student, five interviews,
every one recorded `Generic interview` with a dash where the score goes, and
nothing on any screen saying why.

The refusal lives at the OPERATOR boundary (`main()` passes
`require_department=True`) rather than inside `grant()`, so the in-process
callers that never reach a screen -- `seed_roster`, the suite's own fixtures --
keep working. `test_the_library_still_creates_without_one` pins that line
deliberately, so a later reader can see it was chosen rather than missed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.grant_access import grant
from app.models.user import LoginDay, Mentor, Role, Student, User


@pytest.fixture
def swept():
    """Addresses granted here are new rows `make_user` knows nothing about."""
    emails: list[str] = []
    yield emails
    with SessionLocal() as db:
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                db.execute(delete(LoginDay).where(LoginDay.user_id == user.id))
                db.execute(delete(Mentor).where(Mentor.user_id == user.id))
                db.execute(delete(Student).where(Student.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
        db.commit()


def _email(label: str) -> str:
    return f"gdept-{label}-{uuid.uuid4().hex[:6]}@bgscet.ac.in"


@requires_db
def test_creating_an_account_without_a_department_is_refused(swept):
    """The whole point of the change.

    Fails without it: before `require_department`, this call returned a User
    with `department_id` None and the operator was told it had succeeded.
    """
    email = _email("new")
    swept.append(email)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="needs --department-id"):
            grant(db, email, "Unfiled Person", Role.MENTOR, require_department=True)


@requires_db
def test_the_refusal_names_what_it_costs_a_student(swept):
    """An error that only says "required" teaches nobody why.

    The operator reading it is mid-task and will otherwise retry with the same
    blank box, so the sentence has to carry the consequence, not the rule.
    """
    email = _email("why")
    swept.append(email)
    with SessionLocal() as db:
        with pytest.raises(ValueError) as exc:
            grant(db, email, "Unfiled Person", Role.STUDENT,
                  usn=f"TD{uuid.uuid4().hex[:6].upper()}", require_department=True)
    message = str(exc.value)
    assert "no college" in message
    assert "never be scored" in message


@requires_db
def test_an_unknown_department_id_is_refused_by_name(swept):
    """A typo'd id must not fall through as "no department given".

    Silently treating an unresolvable id as absent is how an operator ends up
    creating exactly the unfiled account they were trying to avoid, having
    typed something.
    """
    email = _email("bogus")
    swept.append(email)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="no department with id"):
            grant(db, email, "Typo Person", Role.MENTOR,
                  department_id="not-a-real-department", require_department=True)


@requires_db
def test_the_library_still_creates_without_one(swept):
    """The line, pinned deliberately.

    `require_department` defaults to False, so `seed_roster` and the suite's
    fixtures are unaffected. If a later change moves the refusal into `grant()`
    itself, this test goes red and its author has to decide about four test
    modules' fixtures on purpose rather than by accident.
    """
    email = _email("lib")
    swept.append(email)
    with SessionLocal() as db:
        user, created = grant(db, email, "Library Caller", Role.ALUMNI)
        db.commit()
        assert created is True
        assert user.department_id is None


@requires_db
def test_updating_an_existing_account_needs_no_department(swept):
    """Re-running with a different --role is the supported promotion path.

    Demanding a department to do it would either block the promotion or make
    the operator retype a value already on the row -- which is exactly how
    `--name` renames people who only needed a role change.
    """
    email = _email("promote")
    swept.append(email)
    with SessionLocal() as db:
        grant(db, email, "Promotable Person", Role.ALUMNI)
        db.commit()
    with SessionLocal() as db:
        user, created = grant(db, email, None, Role.MENTOR, require_department=True)
        db.commit()
        assert created is False
        assert user.role is Role.MENTOR
