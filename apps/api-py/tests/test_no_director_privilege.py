"""DIRECTOR grants nothing. Both halves, together, because one half is worse.

INCIDENT (2026-09-10, found by an audit mid-refactor). REEP has one office
account, the Main Admin. DIRECTOR was a second one under another name — its
baseline was every console screen — and `app.grant_access` had refused to mint
one for months, so the role survived only in the dev seed and a few role sets.

Removing it touched the ROLE gates first: `require_mentor`, `require_admin`,
`policies.STAFF_ROLES`. For a few hours `governance.ROLE_BASELINE["DIRECTOR"]`
still carried every capability. That state is WORSE than not having started:

    a DIRECTOR session was refused by every require_* gate — it could not open
    its own mentee log — and passed every require_capability gate, which is
    ~50 endpoints, the whole console, including DELETE /api/admin/students/{id}
    and the full-roster exports CSV.

Two systems decide access in this app and they are checked separately by design
(`app/governance.py`: "a capability can never relax the student filter"). A role
removal that only reaches one of them leaves the other wide open, and no test
that looked at either half alone would have noticed.

So this file asserts BOTH, and asserts them for the same session, which is the
only assertion that would have failed during those hours.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from conftest import requires_db

from app.db import SessionLocal
from app.governance import ROLE_BASELINE, capabilities_for
from app.models.user import Role, User
from app.policies import NOTEBOOK_ROLES, PROGRAMME_ROLES, STAFF_ROLES


# --------------------------------------------------------------------------- #
# The two halves, as pure data — no database needed, so this runs everywhere.
# --------------------------------------------------------------------------- #


def test_director_holds_no_capability_by_baseline() -> None:
    """The half that was missed. This is the assertion that was false."""
    assert ROLE_BASELINE["DIRECTOR"] == frozenset(), (
        "DIRECTOR has a capability baseline again. Every require_capability gate "
        "in the app — roughly fifty endpoints, the entire console — is open to it, "
        "however tightly the require_* role gates are written."
    )


def test_director_is_in_no_role_set() -> None:
    """The half that was done. Kept beside the other so they cannot drift."""
    for name, roles in (
        ("STAFF_ROLES", STAFF_ROLES),
        ("PROGRAMME_ROLES", PROGRAMME_ROLES),
        ("NOTEBOOK_ROLES", NOTEBOOK_ROLES),
    ):
        assert "DIRECTOR" not in roles, f"DIRECTOR is back in {name}"


def test_the_admin_baseline_is_untouched() -> None:
    """A guard that only removes is a guard that can pass by deleting the thing
    it protects. The Main Admin must still hold the console."""
    assert "admin.analytics" in ROLE_BASELINE["ADMIN"]
    assert "admin.interview_audio" in ROLE_BASELINE["ADMIN"]
    assert ROLE_BASELINE["MENTOR"], "MENTOR lost its own scoped baseline"
    assert ROLE_BASELINE["STUDENT"] == frozenset()


# --------------------------------------------------------------------------- #
# And the same thing end to end, for a real session
# --------------------------------------------------------------------------- #


@requires_db
def test_a_director_session_reaches_neither_gate(client, make_user) -> None:
    """One session, both gates, one test.

    `make_user` cannot mint a DIRECTOR through any supported path any more — the
    role is not grantable and the seed no longer creates one — so the row is
    written directly. That is the point: this is the account an old database or
    an un-migrated checkout still holds, and it must reach nothing.
    """
    person = make_user("no-director")
    with SessionLocal() as db:
        row = db.scalar(select(User).where(User.id == person.user_id))
        row.role = Role.DIRECTOR
        db.commit()

    # Re-authenticate so the cookie carries role=DIRECTOR: the session is a
    # signed snapshot, not a live read of the row.
    login = client.post(
        "/api/auth/login", json={"email": person.email, "password": "voicepass123"}
    )
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "DIRECTOR"
    headers = {"Cookie": login.headers.get("set-cookie", "")}
    client.cookies.clear()

    # Half one: the ROLE gates.
    assert client.get("/api/mentor/mentees", headers=headers).status_code == 403

    # Half two: the CAPABILITY gates — the ones that stayed open.
    for url in (
        "/api/admin/colleges",
        "/api/admin/departments",
        "/api/admin/mentor-load",
        "/api/admin/exports/students.csv",
    ):
        got = client.get(url, headers=headers).status_code
        assert got == 403, f"{url} admitted a DIRECTOR ({got}); the console is open to it"

    # And it holds no capability at all, which is what makes the above true
    # rather than a list of URLs somebody remembered to add.
    with SessionLocal() as db:
        held = capabilities_for(db, {"userId": person.user_id, "role": "DIRECTOR"})
    assert set(held) == set(), f"a DIRECTOR holds {sorted(held)}"
