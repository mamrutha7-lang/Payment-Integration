"""B15 — My account: which doors were used, which are still open, and what
REEP may put in your inbox.

The screen exists because being signed out is a ROUTINE event in REEP (one
device at a time, 2026-09-10) and because a session appearing on a device the
holder does not recognise is the one security incident a student can actually
notice. That only works if the list is true.

What each group of tests is holding down, said as the failure:

* EVERY DOOR RECORDS ITSELF — password, emailed code, activation, Google; one
  test each. Delete one and a "Recent sign-ins" list quietly stops covering one
  of the four ways in, which is worse than no list at all, because an absence
  there reads as "nobody signed in".
* `login_events` IS NOT `login_days`. Delete `test_a_staff_sign_in_is_recorded_too`
  and the screen silently covers students only, because the streak table it is
  mistaken for has no staff rows at all.
* UNLINKING GOOGLE CAN LOCK YOU OUT. `test_unlinking_google_is_refused_without_a_password`
  is the test that matters in this whole module: REEP mints accounts holding a
  sentinel hash no password can ever match, so for those accounts Google is the
  only door and the unlink button is a delete-my-access button.
* A STORED PREFERENCE MUST BE READ BY SOMETHING. B2.1 deleted ten capability
  keys nothing checked; `test_an_unknown_preference_is_refused` and
  `test_the_sign_in_alert_is_actually_sent` keep the same rot out of here.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import TEST_PASSWORD, requires_db

from app.db import SessionLocal
from app.models.account_events import LoginEvent
from app.models.mail import MailLog
from app.models.user import Role, User
from app.routers.auth import (
    DOOR_ACTIVATION,
    DOOR_CODE,
    DOOR_GOOGLE,
    DOOR_PASSWORD,
    PREF_SIGN_IN_ALERTS,
)


def _events(user_id: str) -> list[LoginEvent]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(LoginEvent)
                .where(LoginEvent.user_id == user_id)
                .order_by(LoginEvent.at.desc())
            ).all()
        )


def _forget(user_id: str) -> None:
    with SessionLocal() as db:
        db.execute(delete(LoginEvent).where(LoginEvent.user_id == user_id))
        db.commit()


# ------------------------------------------------------------ the ledger --


@requires_db
def test_the_password_door_records_a_sign_in(client, make_user):
    """`make_user` signs in with a password, so the row must already be there.

    Delete this and the commonest door in development and CI — the one six test
    modules authenticate through — stops appearing on the screen that exists to
    say which doors were used.
    """
    account = make_user(f"acct-pw-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        rows = _events(account.user_id)
        assert len(rows) == 1, rows
        assert rows[0].door == DOOR_PASSWORD
        assert rows[0].at is not None
    finally:
        _forget(account.user_id)


@requires_db
def test_a_staff_sign_in_is_recorded_too(client, make_user):
    """`login_days` HAS NO STAFF ROWS. It is the student streak on the
    dashboard, one per student per day, and mistaking it for this table is how
    "Recent sign-ins" ends up blank for every faculty member and the Main Admin
    — which reads as "nobody has been in this account"."""
    faculty = make_user(f"acct-staff-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    try:
        assert [e.door for e in _events(faculty.user_id)] == [DOOR_PASSWORD]
    finally:
        _forget(faculty.user_id)


@requires_db
def test_a_second_sign_in_appends_rather_than_replacing(client, make_user):
    """One row per sign-in, newest first. A table that kept only the latest
    would answer "when did I last sign in" and never "was that me", which is
    the question the screen is for."""
    account = make_user(f"acct-two-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        again = client.post(
            "/api/auth/login", json={"email": account.email, "password": TEST_PASSWORD}
        )
        assert again.status_code == 200, again.text
        client.cookies.clear()
        assert len(_events(account.user_id)) == 2
    finally:
        _forget(account.user_id)


def test_the_four_doors_are_the_four_that_mint_a_session():
    """The spec for B15 names five doors and the code names four.

    `/auth/reset` signs NOBODY in — it sets the password, retires every device
    and sends the person back to the login form — so there is no sign-in there
    to record. `/auth/change-password` re-issues a cookie but is
    `Depends(get_current_session)`: the person was already signed in. Delete
    this and somebody adds a `reset` door back, and the screen starts reporting
    sessions that never existed.
    """
    assert {DOOR_PASSWORD, DOOR_CODE, DOOR_GOOGLE, DOOR_ACTIVATION} == {
        "password",
        "code",
        "google",
        "activation",
    }


@requires_db
def test_record_login_will_not_compile_a_door_that_does_not_name_itself():
    """`door` is a REQUIRED keyword with no default.

    That is the entire mechanism by which this list stays true: a default would
    let a new sign-in path be written, pass review and record the wrong door.
    Delete this and the next door silently files itself under whatever the
    default happened to be.
    """
    import inspect

    from app.routers.auth import _record_login

    door = inspect.signature(_record_login).parameters["door"]
    assert door.kind is inspect.Parameter.KEYWORD_ONLY
    assert door.default is inspect.Parameter.empty


# ------------------------------------------------------------------ /me --


@requires_db
def test_me_carries_the_recent_sign_ins_and_the_google_link(client, make_user):
    """The three B15 fields, on the one endpoint the client already calls to
    refresh its session."""
    account = make_user(f"acct-me-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        r = client.get("/api/auth/me", headers=account.headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["google_linked"] is False, "no Google account is pinned to this row"
        assert len(body["last_sign_ins"]) == 1
        assert body["last_sign_ins"][0]["door"] == DOOR_PASSWORD
        assert PREF_SIGN_IN_ALERTS in body["notification_prefs"]
        pref = body["notification_prefs"][PREF_SIGN_IN_ALERTS]
        assert pref["enabled"] is False, "opt-in, so it is off until somebody asks for it"
        assert pref["enforced"] is True, "something actually reads it"
    finally:
        _forget(account.user_id)


@requires_db
def test_login_does_not_pretend_to_answer_the_my_account_fields(client, make_user):
    """`google_linked` is NULL on /login, not False.

    /login mints a session; it does not render My account. A client that read
    an absent value as `false` would tell somebody their Google sign-in is
    unlinked on the screen immediately after they used it — which is why the
    field is `bool | None` and `features` beside it is `{}` = "not asked".
    """
    account = make_user(f"acct-login-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        r = client.post(
            "/api/auth/login", json={"email": account.email, "password": TEST_PASSWORD}
        )
        client.cookies.clear()
        assert r.status_code == 200, r.text
        assert r.json()["google_linked"] is None
        assert r.json()["last_sign_ins"] == []
        assert r.json()["notification_prefs"] == {}
    finally:
        _forget(account.user_id)


# -------------------------------------------------------- google unlink --


@requires_db
def test_unlinking_google_is_refused_without_a_password(client, make_user):
    """THE TEST THAT MATTERS IN THIS MODULE.

    `app.grant_access` and `app.seed_roster` mint accounts holding
    SSO_ONLY_PASSWORD_HASH, a sentinel that is not `scrypt:salt:digest`, so
    `verify_password` returns False for every password ever tried. For such an
    account Google is the ONLY door, and an unlink that succeeds is the account
    deleting its own access — with no admin action, no confirmation that means
    anything, and no way back except a support call.

    Delete this assertion and the button becomes exactly that.
    """
    account = make_user(f"acct-unlink-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        with SessionLocal() as db:
            user = db.get(User, account.user_id)
            user.google_sub = f"sub-{uuid.uuid4().hex}"
            user.password_hash = "sso-only-no-password"  # the sentinel shape
            db.commit()

        r = client.post("/api/auth/google/unlink", headers=account.headers)
        assert r.status_code == 409, r.text
        assert "lock you out" in r.json()["detail"]

        with SessionLocal() as db:
            assert db.get(User, account.user_id).google_sub is not None, (
                "a refused unlink writes nothing"
            )
    finally:
        _forget(account.user_id)


@requires_db
def test_unlinking_google_works_when_a_real_password_exists(client, make_user):
    """The other half: an account that can still get in by its own door may
    drop the Google pin — which is what makes re-issuing an institutional
    address to a new student possible at all."""
    account = make_user(f"acct-unlink2-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        with SessionLocal() as db:
            db.get(User, account.user_id).google_sub = f"sub-{uuid.uuid4().hex}"
            db.commit()

        r = client.post("/api/auth/google/unlink", headers=account.headers)
        assert r.status_code == 200, r.text
        assert r.json()["google_linked"] is False
        with SessionLocal() as db:
            assert db.get(User, account.user_id).google_sub is None

        # And the session survives: unlinking changes which doors exist, not who
        # is at the keyboard. Signing the person out here would throw them at a
        # login screen at the moment they have one fewer way through it.
        assert client.get("/api/auth/me", headers=account.headers).status_code == 200
    finally:
        _forget(account.user_id)


@requires_db
def test_unlinking_what_was_never_linked_is_a_conflict_not_a_success(client, make_user):
    """"Unlinked" and "was never linked" are different facts. A success toast
    for the second teaches its reader that the button did something."""
    account = make_user(f"acct-unlink3-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        r = client.post("/api/auth/google/unlink", headers=account.headers)
        assert r.status_code == 409, r.text
        assert "not linked" in r.json()["detail"]
    finally:
        _forget(account.user_id)


# ----------------------------------------------------- notification prefs --


@requires_db
def test_an_unknown_preference_is_refused(client, make_user):
    """A JSON column accepts anything, so a typo on the client would be stored,
    read back, rendered as a switch that never does anything, and survive every
    future deploy. That is B2.1's ten unenforced capability keys, arriving in a
    different table."""
    account = make_user(f"acct-pref-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        r = client.put(
            "/api/auth/notification-prefs",
            headers=account.headers,
            json={"prefs": {"sign_in_alertz": True}},
        )
        assert r.status_code == 422, r.text
        assert "sign_in_alertz" in r.json()["detail"]
    finally:
        _forget(account.user_id)


@requires_db
def test_a_preference_is_stored_and_read_back_dense(client, make_user):
    """The map is DENSE — every key, always — for the reason `features` on the
    same model is: an absent key must never be readable as "switched off"."""
    account = make_user(f"acct-pref2-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        r = client.put(
            "/api/auth/notification-prefs",
            headers=account.headers,
            json={"prefs": {PREF_SIGN_IN_ALERTS: True}},
        )
        assert r.status_code == 200, r.text
        assert r.json()[PREF_SIGN_IN_ALERTS]["enabled"] is True

        me = client.get("/api/auth/me", headers=account.headers)
        assert me.json()["notification_prefs"][PREF_SIGN_IN_ALERTS]["enabled"] is True
    finally:
        _forget(account.user_id)


@requires_db
def test_the_sign_in_alert_is_actually_sent(client, make_user):
    """THE PREFERENCE IS READ BY SOMETHING, which is the whole reason this
    catalogue is allowed to exist.

    Delete this and `notification_prefs` becomes a column that stores a promise
    nothing keeps — the failure mode B2.2 names outright when it refuses to let
    an unwired feature switch be turned off.

    One row per sign-in, keyed on the EVENT id: keyed on the user, the mailer's
    dedupe would send the first alert and silently swallow every one after it,
    which is precisely the event this is for.
    """
    account = make_user(f"acct-alert-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        assert (
            client.put(
                "/api/auth/notification-prefs",
                headers=account.headers,
                json={"prefs": {PREF_SIGN_IN_ALERTS: True}},
            ).status_code
            == 200
        )
        before = _mail_count(account.email)

        r = client.post(
            "/api/auth/login", json={"email": account.email, "password": TEST_PASSWORD}
        )
        client.cookies.clear()
        assert r.status_code == 200, r.text

        assert _mail_count(account.email) == before + 1, (
            "the sign-in alert never left; the preference is stored and unread"
        )
    finally:
        _clear_mail(account.email)
        _forget(account.user_id)


@requires_db
def test_nothing_is_mailed_when_the_preference_is_off(client, make_user):
    """OFF BY DEFAULT is what makes this a switch rather than a change of
    behaviour for everybody on the deploy that shipped it — including the seeded
    demo accounts and CI."""
    account = make_user(f"acct-noalert-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    try:
        # `make_user` already signed in once with the default preference.
        assert _mail_count(account.email) == 0
    finally:
        _clear_mail(account.email)
        _forget(account.user_id)


def _mail_count(recipient: str) -> int:
    with SessionLocal() as db:
        return len(
            db.scalars(
                select(MailLog).where(
                    MailLog.recipient == recipient, MailLog.kind == "sign-in-alert"
                )
            ).all()
        )


def _clear_mail(recipient: str) -> None:
    with SessionLocal() as db:
        db.execute(delete(MailLog).where(MailLog.recipient == recipient))
        db.commit()


# ------------------------------------------------------ the other doors --


@requires_db
def test_the_activation_door_records_a_sign_in(client, make_user):
    """Activating an account SIGNS IT IN — it issues the cookie — so it is a
    door and must name itself.

    It lives in `routers/passwords.py` rather than `auth.py`, which is exactly
    how a door gets left out of a ledger written in the other module. Delete
    this and a faculty member's very first sign-in is the one that never
    appears on their security screen.
    """
    from app.seed_roster import SSO_ONLY_PASSWORD_HASH

    admin = make_user(f"acct-act-a-{uuid.uuid4().hex[:4]}", Role.ADMIN)
    faculty = make_user(f"acct-act-f-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    with SessionLocal() as db:
        db.get(User, faculty.user_id).password_hash = SSO_ONLY_PASSWORD_HASH
        db.commit()
    _forget(faculty.user_id)  # drop the password row make_user's own sign-in wrote
    try:
        issued = client.post(
            f"/api/admin/users/{faculty.user_id}/activation-link", headers=admin.headers
        )
        assert issued.status_code == 200, issued.text
        token = issued.json()["link"].rsplit("token=", 1)[1]

        r = client.post(
            "/api/auth/activate",
            json={"token": token, "password": "correct horse battery"},
        )
        client.cookies.clear()
        assert r.status_code == 200, r.text
        assert [e.door for e in _events(faculty.user_id)] == [DOOR_ACTIVATION]
    finally:
        _forget(faculty.user_id)
        _forget(admin.user_id)


@requires_db
def test_the_emailed_code_door_records_a_sign_in(client, make_user, monkeypatch):
    """With OTP_REQUIRED on, /login answers a challenge and sets no cookie; the
    session is minted by /login/code. Recording the challenge would put a
    sign-in on the screen for a session that was never issued, and recording
    neither would leave the OTP deployment with a blank ledger."""
    from app import mail_transport
    from app.config import settings

    account = make_user(f"acct-code-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    _forget(account.user_id)
    monkeypatch.setattr(settings, "otp_required", "true")
    monkeypatch.setattr(type(settings), "otp_login_required", property(lambda self: True))
    mail_transport.outbox.clear()
    try:
        challenge = client.post(
            "/api/auth/login", json={"email": account.email, "password": TEST_PASSWORD}
        )
        assert challenge.json()["otp_required"] is True
        assert _events(account.user_id) == [], (
            "a challenge is not a sign-in: no cookie was set"
        )

        import re

        mail = [e for e in mail_transport.outbox if e.to == account.email][-1]
        code = re.search(r"\b\d{6}\b", mail.text).group(0)
        r = client.post("/api/auth/login/code", json={"email": account.email, "code": code})
        client.cookies.clear()
        assert r.status_code == 200, r.text
        assert [e.door for e in _events(account.user_id)] == [DOOR_CODE]
    finally:
        mail_transport.outbox.clear()
        _forget(account.user_id)


def test_the_google_door_names_itself_at_its_one_call_site():
    """The fourth door, pinned by reading the source rather than by stubbing
    Google's JWKS — `tests/test_google_callback.py` owns that machinery, and a
    second copy of it here would be a second thing to keep true.

    What must not happen is the callback calling `_record_login` without a
    door, or with the wrong one: a sign-in through the door REEP recommends
    for every role, filed under "password".
    """
    import pathlib
    import re

    source = pathlib.Path(__file__).resolve().parents[1] / "app" / "routers" / "auth.py"
    calls = re.findall(r"_record_login\(db, user, door=(\w+)", source.read_text(encoding="utf-8"))
    assert calls == ["DOOR_PASSWORD", "DOOR_CODE", "DOOR_GOOGLE"], calls
