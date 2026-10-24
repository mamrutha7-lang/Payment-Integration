"""One device at a time: a REEP account holds exactly one live session.

THE RULE. Signing in anywhere signs the account out everywhere else. The newest
sign-in wins and the older device is dropped on its next request. There is no
sessions table and no device list — every sign-in advances `users.token_version`,
and app/security.py already refuses a token whose version is behind the row.

WHY IT NEEDS TESTS OF ITS OWN. The mechanism is invisible from the outside: a
sign-in that forgets to advance the version issues a perfectly good session, the
student sees nothing wrong, every other test stays green, and the account
quietly holds two live devices again. The only way to see it is to hold two
cookies and check the older one is dead — which is exactly what a normal test
never does, because a normal test uses one.

FOUR DOORS, and the rule has to hold on all of them or it holds on none: the
password door, the emailed-code door, the Google door, and the activation link
that signs a new staff account in. A student who wants two devices only needs to
find the one door that forgot.

The negative case matters as much: the session a sign-in has just minted must
not be caught by its own retirement. That would be a login endpoint returning
200 with a cookie that 401s on the very next request.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.user import Role, User
from app.security import SESSION_COOKIE, verify_session_token
from tests.conftest import TEST_PASSWORD, requires_db


def _me(client, headers):
    return client.get("/api/auth/me", headers=headers).status_code


def _version_of(headers: dict) -> int:
    """The tokenVersion claim carried by a cookie header dict."""
    raw = headers["Cookie"].split(f"{SESSION_COOKIE}=", 1)[1].split(";", 1)[0]
    claims = verify_session_token(raw)
    assert claims is not None, "the cookie under test does not verify at all"
    return int(claims.get("tokenVersion", 0))


# --------------------------------------------------------------------------- #
# The password door
# --------------------------------------------------------------------------- #


@requires_db
def test_a_second_sign_in_retires_the_first_device(client, make_user, login):
    student = make_user("one-device")
    first = login(student.email, TEST_PASSWORD)
    assert _me(client, first) == 200, "the first sign-in must work before anything else"

    second = login(student.email, TEST_PASSWORD)
    assert _me(client, second) == 200, "the newest sign-in is the one that wins"
    assert _me(client, first) == 401, (
        "the first device is still signed in. One device at a time means a "
        "sign-in retires the sessions minted before it — see "
        "_retire_other_sessions in app/routers/auth.py."
    )


@requires_db
def test_the_session_a_sign_in_mints_is_not_caught_by_its_own_retirement(client, make_user, login):
    """The off-by-one that would break every login at once.

    The version is advanced and the token is built from the SAME user row, so
    the new token must carry the new version. Building the payload before the
    bump would issue a cookie one version behind the row — a 200 from /login
    followed by a 401 on the next request, for everyone.
    """
    staff = make_user("one-device-self", Role.MENTOR)
    headers = login(staff.email, TEST_PASSWORD)
    assert _me(client, headers) == 200

    with SessionLocal() as db:
        row_version = db.scalar(select(User.token_version).where(User.email == staff.email))
    assert _version_of(headers) == row_version, (
        "the cookie's tokenVersion and users.token_version disagree; the token "
        "was built on the wrong side of the bump"
    )


@requires_db
def test_each_sign_in_advances_the_version_by_exactly_one(client, make_user, login):
    """Monotonic, and by one. A version that stands still retires nothing; a
    version that jumps is a second writer nobody has accounted for."""
    student = make_user("one-device-count")
    versions = [_version_of(login(student.email, TEST_PASSWORD)) for _ in range(3)]
    assert versions == [versions[0], versions[0] + 1, versions[0] + 2], versions


@requires_db
def test_two_different_accounts_do_not_retire_each_other(client, make_user, login):
    """The rule is per ACCOUNT. Keying it wrongly — or bumping a shared row —
    would sign the whole cohort out every time anyone logged in."""
    a = make_user("one-device-a")
    b = make_user("one-device-b")
    a_headers = login(a.email, TEST_PASSWORD)
    b_headers = login(b.email, TEST_PASSWORD)
    assert _me(client, b_headers) == 200
    assert _me(client, a_headers) == 200, "another account's sign-in signed this one out"


@requires_db
def test_a_failed_sign_in_does_not_retire_the_live_device(client, make_user, login):
    """A wrong password must not be a way to sign someone else out.

    Without this, anyone who knows a colleague's email address can drop them
    from their session at will by failing to log in as them — a denial of
    service that needs no credential at all.
    """
    staff = make_user("one-device-wrong-pw", Role.MENTOR)
    live = login(staff.email, TEST_PASSWORD)
    assert _me(client, live) == 200

    refused = client.post(
        "/api/auth/login", json={"email": staff.email, "password": "not the password"}
    )
    assert refused.status_code == 401
    assert _me(client, live) == 200, "a failed sign-in retired a live session"


@requires_db
def test_signing_out_still_ends_the_one_session(client, make_user, login):
    """Logout kept working. It bumps the same column, so a change to the
    sign-in side that broke it would be easy to miss."""
    student = make_user("one-device-logout")
    headers = login(student.email, TEST_PASSWORD)
    assert _me(client, headers) == 200
    out = client.post("/api/auth/logout", headers=headers)
    assert out.status_code == 200, out.text
    client.cookies.clear()
    assert _me(client, headers) == 401


# --------------------------------------------------------------------------- #
# The emailed-code door
# --------------------------------------------------------------------------- #


@requires_db
def test_the_code_door_retires_the_other_device(client, make_user, monkeypatch):
    """`POST /auth/login/code` is a sign-in, so the rule holds there too.

    Its bump is the one with a constraint the others do not have: it must ride
    on the SAME commit as the one-time code's `consumed_at`, or a failure
    between the two leaves the code spent and the session refused. Tested here
    only for the outcome; the ordering is argued at the call site.
    """
    import tests.test_otp_login as otp

    staff = make_user("one-device-code", Role.MENTOR)
    live = client.post("/api/auth/login", json={"email": staff.email, "password": TEST_PASSWORD})
    assert live.status_code == 200, live.text
    live_headers = {"Cookie": live.headers["set-cookie"]}
    client.cookies.clear()
    assert _me(client, live_headers) == 200

    otp._force_otp(monkeypatch)
    code = otp._challenge(client, staff.email)
    signed_in = otp._post_code(client, staff.email, code)
    assert signed_in.status_code == 200, signed_in.text
    client.cookies.clear()

    assert _me(client, live_headers) == 401, (
        "the code door signed the account in without retiring the other device"
    )


# --------------------------------------------------------------------------- #
# The activation door
# --------------------------------------------------------------------------- #


@requires_db
def test_activating_an_account_retires_its_other_devices(client, make_user, login):
    """`POST /auth/activate` signs the staff member in, so it is a sign-in door.

    It is the easiest of the four to forget, because it lives in
    routers/passwords.py rather than routers/auth.py.
    """
    import re

    from app import mail_transport
    from app.account_links import issue_activation

    staff = make_user("one-device-activate", Role.MENTOR)
    live = login(staff.email, TEST_PASSWORD)
    assert _me(client, live) == 200

    # The link is minted the way the console mints it, and the token is read
    # back out of the message — the same route a real staff member takes.
    #
    # THE ACCOUNT IS PUT BACK TO "NO PASSWORD YET" FIRST, which it was not
    # before B3.4. `issue_activation` now REFUSES an account that already holds
    # a scrypt hash, because an activation link that also sets a new password on
    # a live account is an account takeover dressed as a support action. This
    # test is about the sign-in door, not about that refusal, so it uses the
    # only shape a real activation link is ever minted for: an account created
    # with the unusable sentinel and no password set yet. `make_user` issues a
    # real one, hence the reset here.
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == staff.email))
        user.password_hash = "google-only"
        db.flush()
        issue_activation(db, user, created_by_user_id=None)
        db.commit()
    entry = mail_transport.outbox[-1]
    match = re.search(r"token=([A-Za-z0-9_\-]+)", entry.text)
    assert match, f"no activation token in the mail body: {entry.text!r}"

    activated = client.post(
        "/api/auth/activate",
        json={"token": match.group(1), "password": f"Activation-{uuid.uuid4().hex[:10]}"},
    )
    assert activated.status_code == 200, activated.text
    client.cookies.clear()
    assert _me(client, live) == 401, (
        "activation signed the account in without retiring the old device"
    )


# --------------------------------------------------------------------------- #
# The Google door
# --------------------------------------------------------------------------- #
#
# Lives in tests/test_google_callback.py, next to the fixtures that stub Google
# at exactly two seams and run every real verification in between. Reaching into
# another module's fixtures from here would be a second, looser stub to trust —
# and this rule is only worth testing against the real verification path.
