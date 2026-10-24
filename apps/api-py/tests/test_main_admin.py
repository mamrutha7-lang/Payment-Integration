"""One Main Admin: the account tool mints it once and never a DIRECTOR.

`python -m app.grant_access` is the only way a staff account is created on a
production host (the seed refuses there). Pinned here: while an office account
exists, granting ADMIN to a DIFFERENT address is refused and the message names
the account that holds it; DIRECTOR is refused outright; re-running for the
Main Admin's own address is the idempotent update the module promises; and
faculty are MENTOR, which still works exactly as before.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.grant_access import grant
from app.models.user import Mentor, Role, User


@requires_db
def test_grant_access_mints_one_main_admin_and_never_a_director(make_user):
    main = make_user(f"main-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)
    second = f"second-admin-{uuid.uuid4().hex[:6]}@bgscet.ac.in"
    faculty = f"faculty-{uuid.uuid4().hex[:6]}@bgscet.ac.in"
    try:
        with SessionLocal() as db:
            with pytest.raises(ValueError) as why:
                grant(db, second, "Second Admin", Role.ADMIN)
            assert "one Main Admin" in str(why.value) and main.email in str(why.value), str(why.value)
            assert "--role MENTOR" in str(why.value), "the refusal says how a handover is done"
            db.rollback()
            assert db.scalar(select(User).where(User.email == second)) is None, "a refused grant writes nothing"

            # Role.DIRECTOR ON PURPOSE, and it must stay. The role grants
            # nothing anywhere now (tests/test_no_director_privilege.py), but
            # `grant_access` is the code that REFUSES to mint one, so this is
            # the one place the name is still load-bearing. A bulk rename turned
            # this into Role.ADMIN once and the assertion silently started
            # testing the one-admin rule twice instead.
            with pytest.raises(ValueError) as why:
                grant(db, second, "Would-be Director", Role.DIRECTOR)
            assert "DIRECTOR is not granted" in str(why.value)
            db.rollback()

            # The Main Admin's own address: the idempotent update, not a refusal.
            user, created = grant(db, main.email, f"Voice Test main-adm", Role.ADMIN)
            assert created is False and user.role is Role.ADMIN and user.id == main.user_id
            db.rollback()

            # Faculty are MENTOR, with their group, exactly as before.
            user, created = grant(db, faculty, "A Faculty Member", Role.MENTOR, with_group=True)
            db.commit()
            assert created is True and user.role is Role.MENTOR
            assert db.scalar(select(Mentor).where(Mentor.user_id == user.id)) is not None
    finally:
        with SessionLocal() as db:
            row = db.scalar(select(User).where(User.email == faculty))
            if row is not None:
                db.execute(delete(Mentor).where(Mentor.user_id == row.id))
                db.execute(delete(User).where(User.id == row.id))
                db.commit()
