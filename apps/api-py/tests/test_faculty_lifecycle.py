"""The faculty account, from the day it is created to the day it is switched off.

B3.1 - B3.6. What is pinned here, and what breaks if each assertion goes:

  * a faculty account cannot be created without a DEPARTMENT, because the
    department is how it reaches its college, and the college is what fences the
    address and scopes every grant made on the person. Delete that assertion and
    an admin mints tenant-less accounts again;
  * an address off the college's domains is refused unless the admin says so in
    writing, and the words they wrote land in the audit row. Delete it and a
    typo in a domain puts a REEP account on an address the institution does not
    control - which is a roster row, and the roster is the access control;
  * the address can be corrected, and doing so checks uniqueness (two rows
    answering one address is an account takeover with no attacker in it) and
    signs the account out (the cookie carries `email` as a claim);
  * DISABLING shuts every door from ONE place and keeps every record. Delete the
    "still 200 on someone else" half and the next refactor turns offboarding
    into a global outage; delete the "notes survive" half and it turns into a
    delete;
  * the Main Admin and your own account cannot be disabled, or the console locks
    every human out of the only screen that could undo it;
  * enabling restores the LOGIN only. Delete that and a colleague's capability
    grants come back on the word of whoever pressed the button;
  * sign-out-everywhere retires every device from either side without touching
    the password - the first move while the facts are unclear.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update

from conftest import TEST_PASSWORD, requires_db

from app import mail_transport
from app.db import SessionLocal
from app.models.auth_token import AuthToken
from app.models.institution import College, Department
from app.models.redesign import AuditEvent
from app.models.user import LoginDay, Mentor, Role, User

API = "/api/admin/faculty"
USERS = "/api/admin/users"


def _email(label: str, domain: str = "bgscet.ac.in") -> str:
    return f"lifecycle-{label}-{uuid.uuid4().hex[:6]}@{domain}"


@pytest.fixture
def swept():
    """Accounts created THROUGH the API are rows `make_user` knows nothing
    about; every address created here is removed afterwards whatever happened."""
    emails: list[str] = []
    yield emails
    with SessionLocal() as db:
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                db.execute(delete(AuditEvent).where(AuditEvent.entity_id == user.id))
                db.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
                db.execute(delete(LoginDay).where(LoginDay.user_id == user.id))
                db.execute(delete(Mentor).where(Mentor.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
        db.commit()


def _college(domains: list[str] | None):
    """A throwaway college + department. `domains` of None records none, which is
    the state of every college today and makes the fence fall back to the
    deployment's env list (app/institution_domains.py)."""
    suffix = uuid.uuid4().hex[:6].upper()
    with SessionLocal() as db:
        college = College(code=f"LC{suffix}", name=f"Lifecycle College {suffix}")
        if domains is not None:
            college.email_domains = domains
        db.add(college)
        db.flush()
        dep = Department(college_id=college.id, code=f"LD{suffix}", name=f"Lifecycle Dept {suffix}")
        db.add(dep)
        db.commit()
        return college.id, dep.id, dep.name


def _drop_college(college_id: str, department_id: str) -> None:
    with SessionLocal() as db:
        # `users.department_id` is a real FK with no ON DELETE; unfile whoever is
        # still pointing here before the department goes.
        db.execute(update(User).where(User.department_id == department_id).values(department_id=None))
        db.execute(delete(Department).where(Department.id == department_id))
        db.execute(delete(College).where(College.id == college_id))
        db.commit()


@pytest.fixture
def house():
    """A college with no recorded domains: the everyday case."""
    college_id, dep_id, dep_name = _college(None)
    yield {"college_id": college_id, "department_id": dep_id, "department_name": dep_name}
    _drop_college(college_id, dep_id)


@pytest.fixture
def fenced():
    """A college that HAS recorded its own domains, so the env list no longer
    applies to it — the fence is the college's alone, or it is not a fence."""
    college_id, dep_id, dep_name = _college(["fenced-college.edu"])
    yield {"college_id": college_id, "department_id": dep_id, "department_name": dep_name}
    _drop_college(college_id, dep_id)


def _audit(entity_id: str, action: str) -> AuditEvent | None:
    with SessionLocal() as db:
        return db.scalar(
            select(AuditEvent)
            .where(AuditEvent.entity_id == entity_id, AuditEvent.action == action)
            .order_by(AuditEvent.occurred_at.desc())
            .limit(1)
        )


# ------------------------------------------------------------------- B3.1 --


@requires_db
def test_a_faculty_account_must_name_a_department_and_the_unfiled_stay_listable(
    client, make_user, swept, house
):
    """Institution first. An account with no department has no college, and the
    college is what fences its address and scopes every grant made on it.

    The pre-rule accounts are not orphaned by the change - `?unfiled=true` is
    the backlog screen. Delete that half and the rule becomes a trap: no way to
    create one, and no way to find the ones that already exist.
    """
    admin = make_user("life-adm1", Role.ADMIN)
    email = _email("needs-dept")
    swept.append(email)

    missing = client.post(API, headers=admin.headers, json={"name": "No Dept", "email": email})
    assert missing.status_code == 422, missing.text
    assert "department" in missing.text.lower()
    blank = client.post(
        API, headers=admin.headers, json={"name": "No Dept", "email": email, "department_id": "  "}
    )
    assert blank.status_code == 422, "a whitespace department is the same as none"
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == email)) is None, "a refused create wrote a row"

    made = client.post(
        API,
        headers=admin.headers,
        json={"name": "Filed F", "email": email, "department_id": house["department_id"]},
    )
    assert made.status_code == 201, made.text
    assert made.json()["placement"]["college_id"] == house["college_id"]
    assert made.json()["shown_once"] is True, "the link exists only in this response"

    # A row from before the rule: made directly, the way production's are.
    legacy_email = _email("legacy")
    swept.append(legacy_email)
    with SessionLocal() as db:
        db.add(User(email=legacy_email, name="Legacy F", role=Role.MENTOR, password_hash="google-only"))
        db.commit()

    unfiled = client.get(f"{API}?unfiled=true", headers=admin.headers).json()
    addresses = {r["email"] for r in unfiled}
    assert legacy_email in addresses, "the backlog screen cannot find the backlog"
    assert email not in addresses, "a filed account is not unfiled"
    assert all(r["placement"]["filed"] is False for r in unfiled)
    # And the unnarrowed list still carries both.
    everyone = {r["email"] for r in client.get(API, headers=admin.headers).json()}
    assert {email, legacy_email} <= everyone


# ------------------------------------------------------------------- B3.2 --


@requires_db
def test_an_outside_address_needs_a_reason_and_the_reason_is_recorded(
    client, make_user, swept, fenced
):
    """The college's domains fence who may hold one of its accounts.

    A visiting lecturer on a personal address is real, so the fence has a gate -
    but the gate needs a reason, and the reason is written into the audit row.
    Delete the reason requirement and the gate is just an off switch nobody has
    to justify; delete the audit assertion and the decision leaves no trace,
    which is indistinguishable from there being no policy.
    """
    admin = make_user("life-adm2", Role.ADMIN)
    outside = _email("visiting", "gmail.com")
    swept.append(outside)

    refused = client.post(
        API,
        headers=admin.headers,
        json={"name": "Visiting V", "email": outside, "department_id": fenced["department_id"]},
    )
    assert refused.status_code == 422, refused.text
    assert "fenced-college.edu" in refused.text, "the refusal must name what IS allowed"

    no_reason = client.post(
        API,
        headers=admin.headers,
        json={
            "name": "Visiting V", "email": outside,
            "department_id": fenced["department_id"], "allow_external": True,
        },
    )
    assert no_reason.status_code == 422 and "reason" in no_reason.text.lower()

    allowed = client.post(
        API,
        headers=admin.headers,
        json={
            "name": "Visiting V", "email": outside,
            "department_id": fenced["department_id"],
            "allow_external": True,
            "external_reason": "Guest lecturer, Spring term, approved by the HOD",
        },
    )
    assert allowed.status_code == 201, allowed.text
    policy = _audit(allowed.json()["user_id"], "CREATE").after_json["email_policy"]
    assert policy["external"] is True and policy["domain"] == "gmail.com"
    assert "Guest lecturer" in policy["reason"]

    # The college's own domain needs neither flag nor reason.
    inside = _email("staff", "fenced-college.edu")
    swept.append(inside)
    ok = client.post(
        API,
        headers=admin.headers,
        json={"name": "Inside I", "email": inside, "department_id": fenced["department_id"]},
    )
    assert ok.status_code == 201, ok.text
    assert _audit(ok.json()["user_id"], "CREATE").after_json["email_policy"]["external"] is False


# ------------------------------------------------------------------- B3.5 --


@requires_db
def test_correcting_a_faculty_address_is_checked_audited_and_signs_them_out(
    client, make_user, login, swept, house
):
    """A married name and a mistyped address are the two edits this screen is
    for, and the address is the one with teeth.

    UNIQUENESS: two rows answering one address is an account takeover with
    nobody attacking anything - Google sign-in finds a row by address.
    SIGN-OUT: the session cookie carries `email` as a claim, so a session left
    live would keep acting under the old address for twelve hours.
    """
    admin = make_user("life-adm3", Role.ADMIN)
    email = _email("rename")
    swept.append(email)
    made = client.post(
        API,
        headers=admin.headers,
        json={"name": "Anu Old", "email": email, "department_id": house["department_id"]},
    )
    assert made.status_code == 201, made.text
    user_id = made.json()["user_id"]
    # Give them a password and a live session, the way a real faculty member has.
    token = made.json()["activation_link"].split("token=", 1)[1]
    activated = client.post("/api/auth/activate", json={"token": token, "password": "correct horse battery"})
    assert activated.status_code == 200, activated.text
    live = {"Cookie": activated.headers.get("set-cookie", "")}
    client.cookies.clear()
    assert client.get("/api/auth/me", headers=live).status_code == 200

    clash = client.patch(f"{API}/{user_id}", headers=admin.headers, json={"email": admin.email})
    assert clash.status_code == 409, "a second row on one address was accepted"

    new_email = _email("renamed")
    swept.append(new_email)
    renamed = client.patch(
        f"{API}/{user_id}",
        headers=admin.headers,
        json={"name": "Anu New", "email": new_email.upper()},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["email"] == new_email, "the address must be stored lower-cased"
    assert renamed.json()["name"] == "Anu New"

    event = _audit(user_id, "UPDATE")
    assert event.before_json["email"] == email and event.after_json["email"] == new_email

    assert client.get("/api/auth/me", headers=live).status_code == 401, (
        "the session still acts under the old address"
    )
    again = client.post("/api/auth/login", json={"email": new_email, "password": "correct horse battery"})
    assert again.status_code == 200, "the password is unchanged; only the address moved"
    client.cookies.clear()


# ------------------------------------------------------------------- B3.3 --


@requires_db
def test_disabling_an_account_shuts_every_door_and_keeps_every_record(
    client, make_user, login, swept, house
):
    """Offboarding is a column, not a delete, and ONE lookup enforces it.

    Every door is checked from this one test because that is the claim: the
    refusal lives in `security.verify_session_token` (every authenticated
    request) and in `auth._payload_for` (every door that mints a session), not
    at five call sites somebody has to remember.

    The "somebody else is still 200" line is the one that must never be deleted:
    without it, a refusal that accidentally refuses everyone still passes.
    """
    admin = make_user("life-adm4", Role.ADMIN)
    bystander = make_user("life-bystander", Role.MENTOR)
    email = _email("leaver")
    swept.append(email)
    made = client.post(
        API,
        headers=admin.headers,
        json={"name": "Leaver L", "email": email, "department_id": house["department_id"]},
    )
    user_id = made.json()["user_id"]
    token = made.json()["activation_link"].split("token=", 1)[1]
    activated = client.post("/api/auth/activate", json={"token": token, "password": "correct horse battery"})
    live = {"Cookie": activated.headers.get("set-cookie", "")}
    client.cookies.clear()
    bystander_live = login(bystander.email, TEST_PASSWORD)
    # A reset link in flight, to prove it does not outlive the account.
    with SessionLocal() as db:
        from app import account_links

        account_links.issue_password_reset(db, db.get(User, user_id))
    reset_token = mail_transport.outbox[-1].text.split("token=", 1)[1].split()[0]

    no_reason = client.post(f"{USERS}/{user_id}/disable", headers=admin.headers, json={"reason": "x"})
    assert no_reason.status_code == 422, "a disable with no reason in words was accepted"

    out = client.post(
        f"{USERS}/{user_id}/disable",
        headers=admin.headers,
        json={"reason": "Resigned; last working day 30 Sep"},
    )
    assert out.status_code == 200, out.text
    assert out.json()["disabled"] is True and out.json()["links_revoked"] >= 1

    # 1. The session they were holding.
    assert client.get("/api/auth/me", headers=live).status_code == 401
    # 2. The password door.
    refused = client.post(
        "/api/auth/login", json={"email": email, "password": "correct horse battery"}
    )
    assert refused.status_code == 403, refused.text
    assert "disabled" in refused.json()["detail"].lower()
    client.cookies.clear()
    # 3. The reset link that was already in the post - killed with the account.
    spent = client.post("/api/auth/reset", json={"token": reset_token, "password": "another good passphrase"})
    assert spent.status_code == 410, spent.text
    # And a link that somehow exists anyway. Nothing mints one for a disabled
    # account today (/forgot skips them, and disable consumed the rest), so this
    # one is minted straight into the table: the refusal in `/auth/reset` is the
    # backstop for the day some other code path does, and a backstop with no
    # test is a backstop that gets deleted as dead code.
    with SessionLocal() as db:
        from app import account_links

        account_links.issue_password_reset(db, db.get(User, user_id))
    late_token = mail_transport.outbox[-1].text.split("token=", 1)[1].split()[0]
    late = client.post("/api/auth/reset", json={"token": late_token, "password": "another good passphrase"})
    assert late.status_code == 403, late.text
    # 4. And /forgot still answers its one sentence, having sent nothing.
    mail_transport.outbox.clear()
    assert client.post("/api/auth/forgot", json={"email": email}).status_code == 202
    assert not [e for e in mail_transport.outbox if e.to == email], (
        "a disabled account was mailed a way back in"
    )

    # Everybody else is untouched. Without this the test passes for a refusal
    # that refuses the whole college.
    assert client.get("/api/auth/me", headers=bystander_live).status_code == 200

    # Nothing was deleted, and the row says who and why.
    with SessionLocal() as db:
        row = db.get(User, user_id)
        assert row is not None and row.disabled_at is not None
        assert row.disable_reason.startswith("Resigned")
        assert row.disabled_by_user_id == admin.user_id
    assert _audit(user_id, "DISABLE").after_json["disable_reason"].startswith("Resigned")

    # They are still ON the faculty list, greyed rather than gone: an account
    # that vanishes cannot be switched back on.
    listed = next(r for r in client.get(API, headers=admin.headers).json() if r["user_id"] == user_id)
    assert listed["disabled_at"] is not None and listed["disable_reason"].startswith("Resigned")

    assert client.post(
        f"{USERS}/{user_id}/disable", headers=admin.headers, json={"reason": "again"}
    ).status_code == 409


@requires_db
def test_a_disabled_account_is_refused_even_when_its_session_is_still_current(
    client, make_user, login
):
    """THE CHOKEPOINT, isolated. `disabled_at` alone is enough to refuse.

    The endpoint disables AND bumps `token_version`, and the bump alone would
    retire the sessions that exist - so the everyday test cannot tell the two
    apart, and a refactor that dropped the `disabled` half of
    `security.verify_session_token` would pass it. Here the column is set
    DIRECTLY, with the version untouched, which is the state a session is in if
    it was minted between the two writes, if a replica lagged, or if a future
    caller sets the column without a bump. The cookie is cryptographically
    perfect and at the current version; it must still be refused.

    Delete this and the one place every authenticated request passes through
    stops being the place offboarding is enforced - and then it has to be
    remembered at every door, which is exactly what B3.3 was written to avoid.
    """
    staff = make_user("life-choke", Role.MENTOR)
    live = login(staff.email, TEST_PASSWORD)
    assert client.get("/api/auth/me", headers=live).status_code == 200

    with SessionLocal() as db:
        row = db.get(User, staff.user_id)
        version_before = int(row.token_version or 0)
        row.disabled_at = datetime.now(timezone.utc)
        row.disable_reason = "set straight on the column, no version bump"
        db.commit()
    # The revocation cache is per worker and keyed on the user, so a row edited
    # behind the app's back has to be re-read. Nothing in the app wrote this, so
    # the cache is cleared the way a second worker would already see it.
    from app import security as _security

    with _security._version_lock:
        _security._version_cache.pop(staff.user_id, None)

    assert client.get("/api/auth/me", headers=live).status_code == 401, (
        "a disabled account acted on a token that was still at the current version"
    )
    with SessionLocal() as db:
        assert int(db.get(User, staff.user_id).token_version or 0) == version_before, (
            "this test is only meaningful while the version is untouched"
        )
    # The WebSocket door and every router read the session through the same
    # function, so there is nothing else to check - which is the point.


@requires_db
def test_the_onboarding_walk_will_not_finish_setting_up_a_disabled_account(
    client, make_user, swept, house
):
    """The third door, and the one the chokepoints cannot reach.

    `/auth/onboard/*` neither reads a session nor mints one, so neither
    `security.verify_session_token` nor `auth._payload_for` sees it: a setup
    link in somebody's inbox is the whole credential. Without this refusal, an
    account disabled between "approved" and "opened the email" would still get
    a password set on it, which is the state a later `enable` silently hands
    over.

    IT ANSWERS THE WALK'S OWN OPAQUE SENTENCE, not the 403 the sign-in doors
    give. The caller is holding a link that arrived in the post; telling them
    "this account is disabled" would turn the invite into an oracle for the
    state of somebody else's account, which is the one thing that message
    exists to prevent.
    """
    admin = make_user("life-adm9", Role.ADMIN)
    email = _email("never-started")
    swept.append(email)
    made = client.post(
        API,
        headers=admin.headers,
        json={"name": "Never N", "email": email, "department_id": house["department_id"]},
    )
    user_id = made.json()["user_id"]
    def _invite() -> str:
        with SessionLocal() as db:
            from app import account_links

            account_links.issue_onboarding(db, db.get(User, user_id))
        return mail_transport.outbox[-1].text.split("token=", 1)[1].split()[0]

    # It works while the account is live...
    assert client.post(
        "/api/auth/onboard/start", json={"token": _invite(), "email": email}
    ).status_code == 200

    client.post(f"{USERS}/{user_id}/disable", headers=admin.headers, json={"reason": "Offer withdrawn"})
    # ...and the invite is MINTED AFTER the account is disabled, deliberately.
    # Disabling consumes every outstanding link, so an invite issued beforehand
    # is already dead and would answer the same 410 with the refusal removed -
    # this test could not see the difference. A live link against a disabled
    # account is the state the refusal exists for.
    refused = client.post("/api/auth/onboard/start", json={"token": _invite(), "email": email})
    assert refused.status_code == 410, refused.text
    assert "setup link" in refused.json()["detail"], (
        "the walk must not say WHY - that would make the invite an oracle"
    )


@requires_db
def test_the_office_account_and_your_own_cannot_be_disabled(client, make_user, swept, house):
    """The Main Admin IS the console. Disabling it locks every human out of the
    only screen that could switch it back on, and there is no second admin by
    design. Delete this and one click is an unrecoverable deployment."""
    admin = make_user("life-adm5", Role.ADMIN)
    other_admin = make_user("life-adm6", Role.ADMIN)

    mine = client.post(
        f"{USERS}/{admin.user_id}/disable", headers=admin.headers, json={"reason": "testing"}
    )
    assert mine.status_code == 422 and "signed in with" in mine.text

    theirs = client.post(
        f"{USERS}/{other_admin.user_id}/disable", headers=admin.headers, json={"reason": "testing"}
    )
    assert theirs.status_code == 422 and "Main Admin" in theirs.text

    # And only the Main Admin may disable anybody at all.
    mentor = make_user("life-men", Role.MENTOR)
    assert client.post(
        f"{USERS}/{mentor.user_id}/disable", headers=mentor.headers, json={"reason": "testing"}
    ).status_code == 403


@requires_db
def test_enable_restores_the_login_only_and_only_within_ninety_days(
    client, make_user, swept, house
):
    """Enabling is not the inverse of disabling, on purpose.

    The login comes back; capability grants do not, because a grant is made in
    Governance with a reason on the audit trail and restoring one as a side
    effect of a different button is a grant nobody made. And past ninety days
    the answer is a new account - "re-enable" a year later is how a departed
    colleague's access returns with nobody having decided it should.
    """
    admin = make_user("life-adm7", Role.ADMIN)
    email = _email("returner")
    swept.append(email)
    made = client.post(
        API,
        headers=admin.headers,
        json={"name": "Return R", "email": email, "department_id": house["department_id"]},
    )
    user_id = made.json()["user_id"]
    token = made.json()["activation_link"].split("token=", 1)[1]
    client.post("/api/auth/activate", json={"token": token, "password": "correct horse battery"})
    client.cookies.clear()

    assert client.post(
        f"{USERS}/{user_id}/enable", headers=admin.headers
    ).status_code == 409, "an account that is not disabled cannot be enabled"

    client.post(f"{USERS}/{user_id}/disable", headers=admin.headers, json={"reason": "Sabbatical"})
    # Too long ago: refused, and the refusal says what to do instead.
    with SessionLocal() as db:
        db.get(User, user_id).disabled_at = datetime.now(timezone.utc) - timedelta(days=91)
        db.commit()
    stale = client.post(f"{USERS}/{user_id}/enable", headers=admin.headers)
    assert stale.status_code == 422 and "new account" in stale.text

    with SessionLocal() as db:
        db.get(User, user_id).disabled_at = datetime.now(timezone.utc) - timedelta(days=10)
        db.commit()
    back = client.post(f"{USERS}/{user_id}/enable", headers=admin.headers)
    assert back.status_code == 200, back.text
    assert back.json()["disabled"] is False
    assert "Governance" in back.json()["detail"], "the response must say what was NOT restored"
    assert _audit(user_id, "ENABLE") is not None

    signed_in = client.post(
        "/api/auth/login", json={"email": email, "password": "correct horse battery"}
    )
    assert signed_in.status_code == 200, "enabling did not restore the login"
    client.cookies.clear()


# ------------------------------------------------------------------- B3.6 --


@requires_db
def test_sign_out_everywhere_works_from_both_sides_and_changes_nothing_else(
    client, make_user, login, swept
):
    """The call that starts "I left myself signed in on the lab machine".

    It retires every session and touches nothing else - no password reset, no
    disable, nothing to undo - which is what makes it the right first move while
    the facts are unclear. Both halves are pinned: the person's own button, and
    the office's, because the person cannot always reach a keyboard.
    """
    admin = make_user("life-adm8", Role.ADMIN)
    staff = make_user("life-selfout", Role.MENTOR)

    live = login(staff.email, TEST_PASSWORD)
    assert client.get("/api/auth/me", headers=live).status_code == 200
    mine = client.post("/api/auth/sign-out-everywhere", headers=live)
    assert mine.status_code == 200, mine.text
    assert client.get("/api/auth/me", headers=live).status_code == 401, (
        "sign-out-everywhere left the session that asked for it alive"
    )
    client.cookies.clear()

    # The password is untouched, so signing in again just works.
    second = login(staff.email, TEST_PASSWORD)
    assert client.get("/api/auth/me", headers=second).status_code == 200

    theirs = client.post(f"{USERS}/{staff.user_id}/sign-out-everywhere", headers=admin.headers)
    assert theirs.status_code == 200, theirs.text
    assert theirs.json()["disabled"] is False, "signing somebody out is not disabling them"
    assert client.get("/api/auth/me", headers=second).status_code == 401
    assert _audit(staff.user_id, "SIGN_OUT_EVERYWHERE") is not None
    with SessionLocal() as db:
        db.execute(delete(AuditEvent).where(AuditEvent.entity_id == staff.user_id))
        db.commit()

    # A mentor cannot do it to anybody else.
    assert client.post(
        f"{USERS}/{admin.user_id}/sign-out-everywhere", headers=second
    ).status_code == 401, "that session was just retired"
    third = login(staff.email, TEST_PASSWORD)
    assert client.post(
        f"{USERS}/{admin.user_id}/sign-out-everywhere", headers=third
    ).status_code == 403


@requires_db
def test_an_activation_link_cannot_be_minted_for_a_disabled_account(
    client, make_user, swept, house
):
    """Disabling spends every outstanding link; minting a fresh one must refuse.

    Found while wiring the Faculty screen, which draws "Activation link" on
    every row including a disabled one. `routers/passwords.py::activate` already
    refuses a disabled holder on redemption, so this was never a way back in —
    it was a link that COULD NOT BE REDEEMED, minted without a word saying so.

    THE COST OF THAT SILENCE IS PAID IN FRONT OF SOMEBODY. The admin presses the
    button precisely because the person is on the phone saying they cannot get
    in, reads out a URL, and it fails for them a minute later with a refusal
    that names neither the admin's action nor the real reason. "The account is
    disabled" is the answer, and it belongs at the moment of minting.

    Refused in `account_links.issue_activation` rather than in the router, so
    `app.grant_access` gets it too — the CLI is the path the very first account
    takes, and it would otherwise print a link nobody can use.
    """
    admin = make_user("link-admin", Role.ADMIN)
    email = _email("offboard-link")
    swept.append(email)

    created = client.post(
        "/api/admin/faculty",
        headers=admin.headers,
        json={"name": "Offboarded Faculty", "email": email,
              "department_id": house["department_id"]},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["user_id"]

    # While active, a link is mintable — the control this test is about.
    assert client.post(
        f"{USERS}/{user_id}/activation-link", headers=admin.headers
    ).status_code == 200

    disabled = client.post(
        f"{USERS}/{user_id}/disable",
        headers=admin.headers,
        json={"reason": "Offboarded, to prove a link cannot follow them out."},
    )
    assert disabled.status_code == 200, disabled.text

    refused = client.post(f"{USERS}/{user_id}/activation-link", headers=admin.headers)
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert "disabled" in detail.lower(), detail
    assert "enable the account first" in detail.lower(), (
        "the refusal must say what to do, not only that it refused"
    )

    # Enabling makes it mintable again: the refusal is about the state, not a
    # one-way door.
    assert client.post(
        f"{USERS}/{user_id}/enable", headers=admin.headers
    ).status_code == 200
    assert client.post(
        f"{USERS}/{user_id}/activation-link", headers=admin.headers
    ).status_code == 200
