"""The emailed one-time code as a second step on password sign-in.

WHAT THESE PIN:

  * OTP is OFF on dev/CI whatever OTP_REQUIRED says. conftest's `login`
    fixture reads set-cookie straight off POST /api/auth/login, and the whole
    DB-backed suite signs in through it; the first test here is that fixture's
    contract, stated where the feature that could break it lives.
  * With the flag on, a right password answers a CHALLENGE — 200, no cookie,
    one email — and only the code from that email earns the session.
  * A code is single-use, and a wrong one is refused with the same words as
    an expired or replayed one.
  * A code is scoped to the account it was issued to. Six digits are not
    unique across users, so B posting A's code must neither sign in as A nor
    spend A's row.
  * The attempt budget is keyed on the ACCOUNT, never the client address —
    every TestClient request shares one peer, so an address bucket would lock
    the whole suite out after the first ten wrong codes.
  * TWO PEOPLE CAN HOLD THE SAME SIX DIGITS, and one person can re-draw a
    code they have already spent. `auth_tokens.token_hash` is globally unique,
    and the first cut stored a bare sha256(code) — so the second person dealt
    482913 got a UniqueViolation out of the INSERT, a 500 on a CORRECT
    password, with odds that grew by one in a million per staff sign-in for
    the life of the deployment. The random codes the other tests draw can
    never see that; these pin `new_login_code` to one value.
  * An unknown address costs the same work as a wrong code: the consume runs
    against a throwaway user id rather than being skipped.
  * Dead code rows are deleted — predecessors on re-issue, the last one by the
    retention sweep — because there are no "already used" words to keep them
    for, and one permanent row per sign-in is a leak.

The flag is forced by monkeypatching the PROPERTY, not the setting: the
property consults ENV and short-circuits to False on dev/CI by design, so
setting `otp_required = "true"` alone must not (and does not) turn it on here.
Users are created BEFORE the flag flips — `make_user` signs itself in through
/login and would otherwise be handed a challenge.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import TEST_PASSWORD, requires_db

from app import account_links, mail_transport, retention
from app.config import settings
from app.db import SessionLocal
from app.models.auth_token import PURPOSE_LOGIN_CODE, AuthToken
from app.models.user import Role
from app.routers import auth as auth_router

PINNED_CODE = "482913"


def _code_rows(user_id: str) -> list[AuthToken]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(AuthToken).where(
                    AuthToken.user_id == user_id, AuthToken.purpose == PURPOSE_LOGIN_CODE
                )
            ).all()
        )


@pytest.fixture(autouse=True)
def _clean_outbox_and_login_budgets():
    mail_transport.outbox.clear()
    auth_router._login_failures.clear()
    yield
    mail_transport.outbox.clear()
    auth_router._login_failures.clear()


def _force_otp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "otp_required", "true")
    monkeypatch.setattr(type(settings), "otp_login_required", property(lambda self: True))


def _challenge(client, email: str) -> str:
    """POST /login for `email`; assert the challenge shape; return the code."""
    r = client.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["otp_required"] is True
    assert "set-cookie" not in r.headers
    hits = [e for e in mail_transport.outbox if e.to == email]
    assert len(hits) == 1, list(mail_transport.outbox)
    assert "code" in hits[0].subject.lower()
    m = re.search(r"\b\d{6}\b", hits[0].text)
    assert m, hits[0].text
    return m.group(0)


def _post_code(client, email: str, code: str):
    r = client.post("/api/auth/login/code", json={"email": email, "code": code})
    client.cookies.clear()
    return r


@requires_db
def test_otp_is_off_in_dev_so_the_login_fixture_still_works(client, make_user):
    mentor = make_user("otp-off", Role.MENTOR)
    assert settings.otp_login_required is False
    r = client.post("/api/auth/login", json={"email": mentor.email, "password": TEST_PASSWORD})
    client.cookies.clear()
    assert r.status_code == 200, r.text
    assert "set-cookie" in r.headers
    assert r.json()["userId"] == mentor.user_id
    assert "otp_required" not in r.json()


@requires_db
def test_a_challenge_then_the_code_signs_in(client, make_user, monkeypatch):
    mentor = make_user("otp-flow", Role.MENTOR)
    _force_otp(monkeypatch)

    code = _challenge(client, mentor.email)

    r = _post_code(client, mentor.email, code)
    assert r.status_code == 200, r.text
    assert "set-cookie" in r.headers
    assert r.json()["userId"] == mentor.user_id

    me = client.get("/api/auth/me", headers={"Cookie": r.headers["set-cookie"]})
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "MENTOR"
    assert me.json()["email"] == mentor.email


@requires_db
def test_a_wrong_code_is_401_and_the_right_code_is_single_use(client, make_user, monkeypatch):
    mentor = make_user("otp-once", Role.MENTOR)
    _force_otp(monkeypatch)

    code = _challenge(client, mentor.email)

    wrong = _post_code(client, mentor.email, "000000" if code != "000000" else "000001")
    assert wrong.status_code == 401, wrong.text
    assert wrong.json()["detail"] == "Invalid or expired code."

    right = _post_code(client, mentor.email, code)
    assert right.status_code == 200, right.text

    replay = _post_code(client, mentor.email, code)
    assert replay.status_code == 401, replay.text
    assert replay.json()["detail"] == "Invalid or expired code."


@requires_db
def test_a_code_is_scoped_to_its_user(client, make_user, monkeypatch):
    a = make_user("otp-a", Role.MENTOR)
    b = make_user("otp-b", Role.MENTOR)
    _force_otp(monkeypatch)

    code_a = _challenge(client, a.email)

    stolen = _post_code(client, b.email, code_a)
    assert stolen.status_code == 401, stolen.text
    assert "set-cookie" not in stolen.headers

    # B's attempt must not have spent A's row.
    own = _post_code(client, a.email, code_a)
    assert own.status_code == 200, own.text
    assert own.json()["userId"] == a.user_id


@requires_db
def test_ten_wrong_codes_lock_the_account_not_the_address(client, make_user, monkeypatch):
    a = make_user("otp-lock-a", Role.MENTOR)
    b = make_user("otp-lock-b", Role.MENTOR)
    _force_otp(monkeypatch)

    code_a = _challenge(client, a.email)
    wrong = "000000" if code_a != "000000" else "000001"

    for _ in range(auth_router._LOGIN_MAX_FAILURES):
        r = _post_code(client, a.email, wrong)
        assert r.status_code == 401, r.text

    locked = _post_code(client, a.email, wrong)
    assert locked.status_code == 429, locked.text
    assert "Retry-After" in locked.headers
    assert "Google" in locked.json()["detail"]

    # Same peer address, different account: B is untouched.
    code_b = _challenge(client, b.email)
    r = _post_code(client, b.email, code_b)
    assert r.status_code == 200, r.text
    assert r.json()["userId"] == b.user_id


@requires_db
def test_two_users_dealt_the_same_code_both_get_a_challenge(client, make_user, monkeypatch):
    """The refuted case: with a bare sha256(code) in a globally unique column,
    B's INSERT collided with A's live row and /login answered 500 after B's
    password had verified. Now each row hashes the code WITH its own id."""
    a = make_user("otp-same-a", Role.MENTOR)
    b = make_user("otp-same-b", Role.MENTOR)
    _force_otp(monkeypatch)
    monkeypatch.setattr(auth_router, "new_login_code", lambda: PINNED_CODE)

    assert _challenge(client, a.email) == PINNED_CODE
    assert _challenge(client, b.email) == PINNED_CODE  # was IntegrityError -> 500

    rows_a, rows_b = _code_rows(a.user_id), _code_rows(b.user_id)
    assert len(rows_a) == 1 and len(rows_b) == 1
    assert rows_a[0].token_hash != rows_b[0].token_hash, "same digits, distinct hashes"

    # Each address spends ITS OWN row: A signing in must not consume B's code.
    own_a = _post_code(client, a.email, PINNED_CODE)
    assert own_a.status_code == 200, own_a.text
    assert own_a.json()["userId"] == a.user_id
    own_b = _post_code(client, b.email, PINNED_CODE)
    assert own_b.status_code == 200, own_b.text
    assert own_b.json()["userId"] == b.user_id

    # And each is still single-use.
    assert _post_code(client, a.email, PINNED_CODE).status_code == 401
    assert _post_code(client, b.email, PINNED_CODE).status_code == 401


@requires_db
def test_a_consumed_code_redrawn_by_the_same_user_issues_cleanly(client, make_user, monkeypatch):
    """A consumed row used to stay forever and collide with every later draw of
    the same digits — by anyone. Re-issue now DELETES the user's dead rows and
    the new row hashes differently regardless."""
    a = make_user("otp-redraw", Role.MENTOR)
    _force_otp(monkeypatch)
    monkeypatch.setattr(auth_router, "new_login_code", lambda: PINNED_CODE)

    assert _challenge(client, a.email) == PINNED_CODE
    first = _post_code(client, a.email, PINNED_CODE)
    assert first.status_code == 200, first.text
    consumed_hash = _code_rows(a.user_id)[0].token_hash

    mail_transport.outbox.clear()
    assert _challenge(client, a.email) == PINNED_CODE  # was IntegrityError -> 500

    rows = _code_rows(a.user_id)
    assert len(rows) == 1, "the consumed predecessor is deleted, not kept"
    assert rows[0].consumed_at is None
    assert rows[0].token_hash != consumed_hash

    again = _post_code(client, a.email, PINNED_CODE)
    assert again.status_code == 200, again.text
    assert again.json()["userId"] == a.user_id


@requires_db
def test_a_superseded_code_is_deleted_and_only_the_newest_works(client, make_user, monkeypatch):
    a = make_user("otp-supersede", Role.MENTOR)
    _force_otp(monkeypatch)

    first = _challenge(client, a.email)
    mail_transport.outbox.clear()
    second = _challenge(client, a.email)
    assert len(_code_rows(a.user_id)) == 1, "issue deletes the predecessor"

    if first != second:
        assert _post_code(client, a.email, first).status_code == 401
    r = _post_code(client, a.email, second)
    assert r.status_code == 200, r.text


@requires_db
def test_an_unknown_address_runs_the_same_consume_as_a_wrong_code(client, monkeypatch):
    """No SELECT-only shortcut: the unknown-address refusal goes through
    `consume_user_code` against a throwaway user id, so it does the same
    round-trips and the same hashing as a wrong code for a real account."""
    _force_otp(monkeypatch)
    calls: list[tuple] = []

    def spy(db, user_id, purpose, raw):
        calls.append((user_id, purpose, raw))
        return False

    monkeypatch.setattr(auth_router, "consume_user_code", spy)
    r = _post_code(client, "nobody-here@bgscet.ac.in", PINNED_CODE)
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "Invalid or expired code."
    assert calls == [(auth_router._TIMING_EQUALIZER_USER_ID, PURPOSE_LOGIN_CODE, PINNED_CODE)]


def test_the_no_row_path_still_hashes_and_compares(monkeypatch):
    """Library-level: with no live row, consume must not short-circuit before
    the hash work. Pinned by counting `_hash_code` calls on both branches."""
    from unittest.mock import MagicMock

    counted: list[str] = []
    real = account_links._hash_code
    monkeypatch.setattr(
        account_links, "_hash_code", lambda row_id, raw: counted.append(row_id) or real(row_id, raw)
    )
    db = MagicMock()
    db.scalar.return_value = None
    assert account_links.consume_user_code(db, "ghost", PURPOSE_LOGIN_CODE, PINNED_CODE) is False
    assert counted == [account_links._NO_ROW_ID]
    db.execute.assert_not_called()


@requires_db
def test_dead_login_codes_are_swept_by_retention(client, make_user, monkeypatch):
    """Issue deletes predecessors; the LAST code of someone who never signs in
    again is what the retention sweep catches — consumed or expired unused,
    once past the grace. A live code is never touched."""
    spent = make_user("otp-sweep-spent", Role.MENTOR)
    stale = make_user("otp-sweep-stale", Role.MENTOR)
    live = make_user("otp-sweep-live", Role.MENTOR)
    _force_otp(monkeypatch)

    code = _challenge(client, spent.email)
    assert _post_code(client, spent.email, code).status_code == 200
    mail_transport.outbox.clear()
    _challenge(client, stale.email)
    mail_transport.outbox.clear()
    _challenge(client, live.email)

    long_ago = datetime.now(timezone.utc) - account_links.LOGIN_CODE_SWEEP_GRACE - timedelta(hours=1)
    with SessionLocal() as db:
        db.scalar(select(AuthToken).where(AuthToken.user_id == spent.user_id)).consumed_at = long_ago
        db.scalar(select(AuthToken).where(AuthToken.user_id == stale.user_id)).expires_at = long_ago
        db.commit()

    with SessionLocal() as db:
        summary = retention.purge_expired(db)
    assert summary["login_codes_deleted"] >= 2

    assert _code_rows(spent.user_id) == []
    assert _code_rows(stale.user_id) == []
    assert len(_code_rows(live.user_id)) == 1

    # A just-consumed code is INSIDE the grace and survives a second pass.
    with SessionLocal() as db:
        assert account_links.sweep_login_codes(db) == 0
