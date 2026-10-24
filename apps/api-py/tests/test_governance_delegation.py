"""B2.6: Governance is a capability now, and the deputy is what that buys.

It was `require_admin` at sixteen call sites, and the reasons for that have not
changed: REEP has one Main Admin, and deciding what faculty may see is that
account's instrument. `admin.governance` keeps exactly that as the DEFAULT — the
office holds it by baseline, nobody else holds anything — and adds the one thing
a role gate cannot express: a named deputy, appointed with a reason, on the audit
trail, revocable in a click.

The alternative to a deputy is not "nobody can act". It is sharing the office
account's mailbox while the Main Admin is away, which is worse in every way and
leaves a trail naming the wrong person.

WHAT THIS MODULE EXISTS TO PROTECT is the other half of that sentence: a deputy
is NOT a second Main Admin. It holds one key, it reaches nothing else, and
`app.grant_access._refuse_second_main_admin` is untouched — a deputy cannot mint
an ADMIN account, and neither can anybody else while one exists.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest
from conftest import requires_db
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.governance import ROLE_BASELINE, capabilities_for
from app.grant_access import grant as grant_access
from app.models.governance import AccessGroup, AccessGroupMember, CapabilityGrant
from app.models.redesign import AuditEvent
from app.models.user import Role, User

GOV = "/api/admin/governance"
KEY = "admin.governance"
WHY = "Deputy for governance while the office is away for the accreditation visit."


@pytest.fixture
def admin(make_user):
    return make_user(f"dlg-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)


@pytest.fixture
def faculty(make_user):
    return make_user(f"dlg-fac-{uuid.uuid4().hex[:4]}", Role.MENTOR)


@pytest.fixture
def swept():
    """Grants outlive the accounts that named them, so they are cleaned by id."""
    made: dict[str, list[str]] = {"grants": [], "groups": []}
    yield made
    with SessionLocal() as db:
        for gid in made["grants"]:
            db.execute(delete(AuditEvent).where(AuditEvent.entity_id == gid))
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.id == gid))
        for gid in made["groups"]:
            db.execute(delete(AccessGroupMember).where(AccessGroupMember.group_id == gid))
            db.execute(delete(AccessGroup).where(AccessGroup.id == gid))
        db.commit()


def _appoint(client, admin, swept, user_id: str) -> str:
    r = client.post(
        f"{GOV}/grants",
        headers=admin.headers,
        json={"capability": KEY, "user_ids": [user_id], "reason": WHY},
    )
    assert r.status_code == 201, r.text
    rows = r.json()
    swept["grants"] += [g["id"] for g in rows]
    return rows[0]["id"]


# --------------------------------------------------------------------------- #

def test_the_office_holds_governance_by_baseline_and_no_other_role_does() -> None:
    """No database needed: the baseline is a constant, and it is the default.

    B2.6 must not change who reaches Governance on a deployment where nobody has
    granted anything — which is every deployment on the day it ships.

    DELETE THIS and the key is free to drift into the MENTOR baseline, where
    every faculty account would hold the screen that decides what faculty may
    see, and the grant list would look exactly the same.
    """
    assert KEY in ROLE_BASELINE["ADMIN"], "the office lost its own console"
    for role in ("MENTOR", "STUDENT", "ALUMNI", "DIRECTOR"):
        assert KEY not in ROLE_BASELINE[role], f"{role} holds Governance without a grant"


def test_the_router_is_gated_on_the_capability_and_not_on_the_role() -> None:
    """Read as text, because the regression is a one-line edit.

    Putting `require_admin(session)` back at the top of one handler leaves every
    other test in this repository green: the Main Admin holds the capability by
    baseline, so the office notices nothing, and the deputy simply finds one
    screen of the console shut with no explanation that fits.

    DELETE THIS and the gate can go back to being the role at any single call
    site, silently.
    """
    source = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "routers" / "governance.py"
    ).read_text(encoding="utf-8")
    assert 'GOVERNANCE_CAPABILITY = "admin.governance"' in source
    assert source.count("require_governance(db, session)") >= 14, (
        "a governance endpoint stopped asking for the capability"
    )
    assert "    require_admin(session)" not in source, (
        "the role gate came back at a call site"
    )


@requires_db
def test_a_deputy_reaches_governance_and_nothing_else_the_office_holds(
    client, admin, faculty, make_user, swept
):
    """One key, and the key does not bring the rest of the console with it.

    A deputy exists to keep Governance working while the office is away. It is
    not a second office account: it has no analytics, no exports, no roster, and
    holding `admin.governance` must not imply any of them.

    DELETE THIS and the natural "shortcut" — treating the Governance holder as an
    admin, as the old role gate did — reinstates the second-administrator
    problem that removing DIRECTOR was about, this time as a grant.
    """
    # Before: a faculty account is refused, and the refusal names what to ask for.
    shut = client.get(f"{GOV}/grants", headers=faculty.headers)
    assert shut.status_code == 403 and "Governance" in shut.text, shut.text

    _appoint(client, admin, swept, faculty.user_id)

    # After, on the SAME cookie: capabilities resolve live, as they always have.
    assert client.get(f"{GOV}/grants", headers=faculty.headers).status_code == 200
    assert client.get(f"{GOV}/catalogue", headers=faculty.headers).status_code == 200
    assert client.get(f"{GOV}/review", headers=faculty.headers).status_code == 200

    me = client.get("/api/auth/me", headers=faculty.headers).json()
    assert KEY in me["capabilities"]
    for other in ("admin.analytics", "admin.exports", "admin.students", "mentor.mentees"):
        assert other not in me["capabilities"], f"the deputy was handed {other} as well"
    assert client.get("/api/admin/analytics-summary", headers=faculty.headers).status_code == 403

    # And the deputy can do the job: hand a colleague a screen, with a reason.
    colleague = make_user(f"dlg-col-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    r = client.post(
        f"{GOV}/grants",
        headers=faculty.headers,
        json={
            "capability": "admin.catalogue",
            "user_ids": [colleague.user_id],
            "reason": "Maintains the approved certification list for the department.",
        },
    )
    assert r.status_code == 201, r.text
    swept["grants"] += [g["id"] for g in r.json()]
    with SessionLocal() as db:
        assert "admin.catalogue" in capabilities_for(
            db, {"userId": colleague.user_id, "role": "MENTOR"}
        )


@requires_db
def test_revoking_the_deputy_shuts_the_door_the_same_second(client, admin, faculty, swept):
    """Nothing caches a capability, and this is where that matters most.

    A deputy appointed for one fortnight and revoked at the end of it must stop
    reaching the screen that hands out access, on the next request — not on the
    next deploy, and not on three of five workers.

    DELETE THIS and a per-process cache added for speed goes unnoticed until the
    revoked deputy is still granting on the worker nobody restarted.
    """
    gid = _appoint(client, admin, swept, faculty.user_id)
    assert client.get(f"{GOV}/grants", headers=faculty.headers).status_code == 200

    r = client.post(
        f"{GOV}/grants/{gid}/revoke",
        headers=admin.headers,
        json={"reason": "The office is back; the deputy arrangement has ended."},
    )
    assert r.status_code == 200, r.text
    assert client.get(f"{GOV}/grants", headers=faculty.headers).status_code == 403


@requires_db
def test_a_deputy_is_never_a_second_main_admin(client, admin, faculty, swept):
    """`_refuse_second_main_admin` STAYS, and delegation must not route around it.

    The deputy is a MENTOR holding one key. It cannot promote itself or anybody
    else to ADMIN, because nothing in the API mints an account role at all —
    `python -m app.grant_access` is that path, and it refuses a second office
    account while one exists, naming the holder and saying how a handover is
    done.

    DELETE THIS and the obvious next feature — "let the deputy create the
    accounts too" — has nothing standing in its way, and REEP has two
    administrators again under a third name.
    """
    _appoint(client, admin, swept, faculty.user_id)
    with SessionLocal() as db:
        assert db.get(User, faculty.user_id).role is Role.MENTOR, (
            "appointing a deputy changed the account's role"
        )

    # There is no API that mints a role at all; the office tool is the path, and
    # it refuses while an office account exists.
    hopeful = f"dlg-second-{uuid.uuid4().hex[:6]}@bgscet.ac.in"
    with SessionLocal() as db:
        with pytest.raises(ValueError) as why:
            grant_access(db, hopeful, "A Second Office", Role.ADMIN)
        assert "one Main Admin" in str(why.value)
        assert "--role MENTOR" in str(why.value), "the refusal stopped saying how a handover is done"
        db.rollback()
        assert db.scalar(select(User).where(User.email == hopeful)) is None

    # ...and the deputy's own address is refused for the same reason, which is
    # the case this test exists for: holding Governance is not a step toward it.
    with SessionLocal() as db:
        with pytest.raises(ValueError) as why:
            grant_access(db, faculty.email, None, Role.ADMIN)
        assert "one Main Admin" in str(why.value)
        db.rollback()


def test_the_one_main_admin_refusal_is_still_wired_into_the_grant_path() -> None:
    """Read as text, because the refusal is one call that is easy to lose.

    `_refuse_second_main_admin` raising correctly is worth nothing if `grant()`
    stops calling it, and a test that only exercises the function directly would
    stay green through exactly that edit.

    DELETE THIS and the guard can be left defined, documented and never reached.
    """
    source = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "grant_access.py"
    ).read_text(encoding="utf-8")
    assert "def _refuse_second_main_admin(" in source
    assert "        _refuse_second_main_admin(db, normalised)" in source, (
        "grant() no longer calls the one-Main-Admin refusal"
    )
    assert "_OFFICE_ROLES = (Role.ADMIN, Role.DIRECTOR)" in source
