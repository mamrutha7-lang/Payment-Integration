"""Changing a ROLE must not rewrite a NAME.

`grant_access` set `user.name = name` unconditionally and `--name` was required,
so the only way to demote an account was to also supply a name — and there was
no safe value. On production, demoting a second Main Admin to faculty meant
either retyping a colleague's name from memory or renaming them; the operator
met "grant-access needs both email and display_name" instead. A role change
should not ask for anything but the role.

So: `--name` is required to CREATE an account (a blank name is not a name) and
optional to UPDATE one. Omitted, the existing name stands.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.grant_access import grant
from app.models.user import LoginDay, Mentor, Role, User


@pytest.fixture
def swept():
    """Addresses granted here are new rows make_user knows nothing about."""
    emails: list[str] = []
    yield emails
    with SessionLocal() as db:
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                db.execute(delete(LoginDay).where(LoginDay.user_id == user.id))
                db.execute(delete(Mentor).where(Mentor.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
        db.commit()


def _email(label: str) -> str:
    return f"gname-{label}-{uuid.uuid4().hex[:6]}@bgscet.ac.in"


@requires_db
def test_a_role_change_leaves_the_name_alone(swept):
    """The production case: demote an account without knowing their name."""
    email = _email("demote")
    swept.append(email)

    with SessionLocal() as db:
        user, created = grant(db, email, "Nithin Kumar", Role.MENTOR)
        db.commit()
        assert created and user.name == "Nithin Kumar"
        original_id = user.id

    # Two role changes, both WITHOUT a name. Deliberately NOT via ADMIN: the
    # one-Main-Admin guard refuses that while an office account exists, which is
    # its own tested behaviour and would mask what this test is about.
    with SessionLocal() as db:
        grant(db, email, None, Role.ALUMNI)
        db.commit()
    with SessionLocal() as db:
        row = db.scalar(select(User).where(User.email == email))
        assert row.role is Role.ALUMNI
        assert row.name == "Nithin Kumar", "a role change must not blank the name"

    with SessionLocal() as db:
        grant(db, email, None, Role.MENTOR)
        db.commit()
    with SessionLocal() as db:
        row = db.scalar(select(User).where(User.email == email))
        assert row.role is Role.MENTOR, "demoted to faculty"
        assert row.name == "Nithin Kumar", "and still called what they are called"
        assert row.id == original_id, "the same account throughout"


@requires_db
def test_whitespace_is_not_a_name_either(swept):
    """A box the operator tabbed through arrives as spaces, not as None, and
    must be treated the same way — otherwise the careless path renames them to
    nothing while the empty path is safe."""
    email = _email("blank")
    swept.append(email)
    with SessionLocal() as db:
        grant(db, email, "Kavya N", Role.MENTOR)
        db.commit()
    with SessionLocal() as db:
        grant(db, email, "   ", Role.ALUMNI)
        db.commit()
    with SessionLocal() as db:
        row = db.scalar(select(User).where(User.email == email))
        assert row.name == "Kavya N" and row.role is Role.ALUMNI


@requires_db
def test_creating_an_account_still_needs_a_name(swept):
    """The one place a name is genuinely required. Refused before any write, so
    a missing name never leaves a half-made account behind."""
    email = _email("create")
    swept.append(email)
    with SessionLocal() as db:
        with pytest.raises(ValueError) as why:
            grant(db, email, None, Role.MENTOR)
        assert "--name is needed to create it" in str(why.value), str(why.value)
        db.rollback()
        assert db.scalar(select(User).where(User.email == email)) is None, "nothing was written"
