"""Password sign-in outside dev/CI: the flag, the limiter, and the key issuer.

Three rules, and each one fails in a way nothing else would report.

THE FLAG IS AN OPT-IN, NOT A CONSEQUENCE OF ENV. `password_login_allowed` used
to be exactly `_is_dev_env(env)`, and the temptation when opening this door is
to relax it to `not is_prod` — which silently opens `staging`, `uat`, `demo` and
every typo, on boxes that hold real roster rows. The tests below pin that an
unrecognised ENV stays SHUT unless the flag positively says otherwise, which is
the same fail-closed shape the cookie's `Secure` guard uses.

THE LIMITER IS KEYED ON THE ACCOUNT AND NOT THE ADDRESS. An address bucket
behind an ALB is one bucket for the whole internet, so ten wrong passwords from
anyone would lock out every student at once. That is not a hypothetical: it was
written that way first and the suite went from 24 failures to 134, because every
TestClient request shares one peer address. `test_a_failure_for_one_account_does_
not_lock_out_another` is that regression, pinned.

A PASSWORD MUST BE ISSUED DELIBERATELY. Opening the door does not give anybody a
key: accounts minted by grant_access/seed_roster hold the SSO-only sentinel, and
app.set_password is the only way past it. If that ever silently accepts a short
or published password, the door and the key are both weak at once.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sqlalchemy import delete, select, update

from app import set_password as sp
from app.config import Settings, settings
from app.db import SessionLocal
from app.grant_access import SSO_ONLY_PASSWORD_HASH
from app.models.mentor_assignment import MentorAssignment
from app.models.user import LoginDay, Role, User
from app.routers import auth as auth_router
from app.security import verify_password

from conftest import TEST_PASSWORD, requires_db


@pytest.fixture
def sso_only_account():
    """Mint a key-less account the way grant_access does, and tear it down.

    A FIXTURE rather than inline setup because of teardown ORDER: a successful
    sign-in writes a `login_days` row pointing at the user, and deleting the
    user first makes SQLAlchemy null that FK rather than remove the row — which
    fails the NOT NULL constraint and reports as an IntegrityError in whatever
    test happens to run next. conftest's `make_user` clears the same table in
    the same order for the same reason.
    """
    created: list[str] = []

    def _make(role: Role = Role.ALUMNI) -> str:
        email = f"pwtest-{uuid.uuid4().hex[:8]}@bgscet.ac.in"
        with SessionLocal() as db:
            user = User(
                email=email,
                name="Password Test",
                role=role,
                # Exactly what grant_access and seed_roster write: not
                # scrypt:salt:digest, so nothing verifies against it.
                password_hash=SSO_ONLY_PASSWORD_HASH,
            )
            db.add(user)
            db.commit()
            created.append(user.id)
        return email

    yield _make

    with SessionLocal() as db:
        for uid in created:
            db.execute(delete(LoginDay).where(LoginDay.user_id == uid))
            db.execute(delete(User).where(User.id == uid))
        db.commit()


# --------------------------------------------------------------------------
# The flag
# --------------------------------------------------------------------------


def _settings(env: str, password_login: str = "") -> Settings:
    """A Settings instance built directly, so the real .env cannot leak in."""
    return Settings(env=env, password_login=password_login)


@pytest.mark.parametrize("env", ["dev", "development", "test", "testing", "ci", "local"])
def test_dev_environments_keep_password_login_with_no_flag(env: str) -> None:
    """The suite's own door. A guard that trips on a laptop gets deleted."""
    assert _settings(env).password_login_allowed is True


@pytest.mark.parametrize("env", ["prod", "production", "prd", "live"])
def test_production_refuses_password_login_by_default(env: str) -> None:
    """Blank PASSWORD_LOGIN means Google-only, which is the shipped default."""
    assert _settings(env).password_login_allowed is False


@pytest.mark.parametrize("env", ["prod", "production", "staging", "uat", "demo", ""])
def test_the_flag_opens_the_door_on_any_environment(env: str) -> None:
    """Opting in is explicit and works wherever the operator sets it."""
    assert _settings(env, "true").password_login_allowed is True


@pytest.mark.parametrize(
    "env", ["staging", "uat", "demo", "prodd", "", "  ", "Production ", "unknown"]
)
def test_an_unrecognised_environment_stays_shut_without_the_flag(env: str) -> None:
    """FAIL CLOSED. This is the half that must not become `not is_prod`.

    A `staging` box has real HTTPS, real roster rows and real students. It is not
    one of the four prod spellings, so `not is_prod` would hand it an open
    password door that nobody chose.
    """
    assert _settings(env).password_login_allowed is False


@pytest.mark.parametrize("value", ["", "  ", "false", "no", "0", "TRUE ", "yes", "1", "on"])
def test_only_the_exact_word_true_opens_it(value: str) -> None:
    """Anything but `true` is off, including near-misses.

    `TRUE ` is stripped and lowercased and so DOES open it — that spelling is a
    human writing the same word. `yes`/`1`/`on` do not: guessing at synonyms is
    how a flag ends up meaning something nobody wrote down.
    """
    expected = value.strip().lower() == "true"
    assert _settings("prod", value).password_login_allowed is expected


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


@requires_db
def test_production_with_no_keys_issued_refuses_passwords(client, monkeypatch) -> None:
    """A fresh deployment: blank flag, every account holds the sentinel.

    The seeded dev accounts DO hold scrypt hashes, so "no keys" has to be
    simulated here: `password_keys_exist` is the one query the door asks, and
    patching it to False is exactly the state a new production database is in.
    """
    monkeypatch.setattr(settings, "env", "prod", raising=False)
    monkeypatch.setattr(settings, "password_login", "", raising=False)
    monkeypatch.setattr(auth_router, "password_keys_exist", lambda db: False)
    r = client.post(
        "/api/auth/login",
        json={"email": "student@bgscet.ac.in", "password": "student123"},
    )
    assert r.status_code == 403
    # Names the door that does work rather than stopping at "no".
    assert "Google" in r.json()["detail"]


@requires_db
def test_production_opens_on_its_own_once_a_key_exists(client, monkeypatch) -> None:
    """THE DERIVED DOOR. Blank flag, a real hash in the table: open.

    This is the state production is in the moment an operator issues the first
    password, and it must work with no second switch flipped anywhere — on
    Fargate that switch is a terraform apply away and the form would 403 until
    somebody found it.
    """
    monkeypatch.setattr(settings, "env", "prod", raising=False)
    monkeypatch.setattr(settings, "password_login", "", raising=False)
    auth_router._login_failures.clear()
    # Not patched: the seeded student really does hold a scrypt hash, so the
    # real query is what answers here.
    r = client.post(
        "/api/auth/login",
        json={"email": "student@bgscet.ac.in", "password": "student123"},
    )
    assert r.status_code == 200, r.text


@requires_db
def test_password_login_false_shuts_the_door_over_issued_keys(client, monkeypatch) -> None:
    """The hard off-switch. Keys exist; the operator said no; nobody gets in.

    Incident response needs this shape: shut the door NOW without destroying
    every hash, so the same accounts work again when it is reopened.
    """
    monkeypatch.setattr(settings, "env", "prod", raising=False)
    monkeypatch.setattr(settings, "password_login", "false", raising=False)
    r = client.post(
        "/api/auth/login",
        json={"email": "student@bgscet.ac.in", "password": "student123"},
    )
    assert r.status_code == 403


@requires_db
def test_the_shut_door_says_so_in_a_header_and_a_disabled_account_does_not(
    client, make_user, monkeypatch
) -> None:
    """/login gives two 403s that want opposite advice, and a header tells them apart.

    A shut door means "use Google". The RIGHT password for an account the
    office has disabled means "contact the office" — Google refuses that
    account too. The login screen printed the door's words for both, so a
    disabled faculty member was sent to a door that would also refuse them.
    It now prints the server's sentence unless the 403 names the door.
    """
    staff = make_user("door-header", Role.MENTOR)
    with SessionLocal() as db:
        row = db.get(User, staff.user_id)
        row.disabled_at = datetime.now(timezone.utc)
        row.disable_reason = "checking which 403 names the door"
        db.commit()
    auth_router._login_failures.clear()
    barred = client.post(
        "/api/auth/login", json={"email": staff.email, "password": TEST_PASSWORD}
    )
    assert barred.status_code == 403, barred.text
    assert barred.json()["detail"] == auth_router.DISABLED_SIGN_IN_MESSAGE
    assert auth_router.PASSWORD_DOOR_HEADER not in barred.headers

    monkeypatch.setattr(settings, "env", "prod", raising=False)
    monkeypatch.setattr(settings, "password_login", "false", raising=False)
    shut = client.post(
        "/api/auth/login", json={"email": staff.email, "password": TEST_PASSWORD}
    )
    assert shut.status_code == 403, shut.text
    assert shut.headers.get(auth_router.PASSWORD_DOOR_HEADER) == auth_router.PASSWORD_DOOR_CLOSED


def test_the_login_screen_reads_the_door_header_the_api_sends() -> None:
    """The header above is half a contract, and the other half is a literal.

    `passwordErrorFor` in the login component spells the header and its value
    out, and its spec builds the refusal with its own copy of both, so a rename
    on either side passes every test on that side. It would not fail loudly:
    a renamed header makes the shut door print the API's terser sentence, and
    a renamed value makes it print `detail` for the door. So the client file
    is read here, against the constants the API sends.
    """
    login = (
        Path(__file__).resolve().parents[3]
        / "apps" / "web" / "src" / "app" / "features" / "login" / "login.component.ts"
    ).read_text(encoding="utf-8")
    assert f"headers.get('{auth_router.PASSWORD_DOOR_HEADER}')" in login
    assert f"=== '{auth_router.PASSWORD_DOOR_CLOSED}'" in login


@requires_db
def test_the_endpoint_works_on_production_with_the_flag(client, monkeypatch) -> None:
    """The whole point of the change: a real sign-in on ENV=prod."""
    monkeypatch.setattr(settings, "env", "prod", raising=False)
    monkeypatch.setattr(settings, "password_login", "true", raising=False)
    auth_router._login_failures.clear()
    r = client.post(
        "/api/auth/login",
        json={"email": "student@bgscet.ac.in", "password": "student123"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "student@bgscet.ac.in"
    # The SAME cookie the Google path sets — one session, two doors.
    assert "reep_session" in r.headers.get("set-cookie", "")


@requires_db
@pytest.mark.parametrize("google_ready", [False, True], ids=["no-google", "google"])
def test_the_probe_reports_the_same_door_the_endpoint_enforces(
    client, monkeypatch, google_ready: bool
) -> None:
    """The login screen renders its form from this field, and fails CLOSED.

    A server that refuses the password must not advertise the form, or every
    submission on it 403s and reads to a student as "my password is wrong". So
    the probe and /login must agree in every state — same function, one answer.

    PARAMETRISED OVER BOTH GOOGLE BRANCHES, because /sso/status has two return
    statements and this test once passed with one of them still reading the old
    env-only value. Locally the run happened to take the "Google configured"
    branch; CI, with no GOOGLE_CLIENT_ID, took the other and caught it. A test
    that depends on which credentials the machine happens to hold is a test that
    passes on the laptop and fails in CI — so the branch is pinned explicitly.
    """
    monkeypatch.setattr(settings, "env", "prod", raising=False)
    monkeypatch.setattr(auth_router.google_auth, "sso_ready", lambda: google_ready)

    def probe() -> bool:
        body = client.get("/api/auth/sso/status").json()
        assert body["google_available"] is google_ready  # we are on the branch we meant
        return body["password_login_available"]

    # Blank flag, no keys: shut.
    monkeypatch.setattr(settings, "password_login", "", raising=False)
    monkeypatch.setattr(auth_router, "password_keys_exist", lambda db: False)
    assert probe() is False

    # Blank flag, a key exists: open, with nothing else changed.
    monkeypatch.setattr(auth_router, "password_keys_exist", lambda db: True)
    assert probe() is True

    # Forced shut beats an issued key.
    monkeypatch.setattr(settings, "password_login", "false", raising=False)
    assert probe() is False

    # Forced open beats having no keys.
    monkeypatch.setattr(settings, "password_login", "true", raising=False)
    monkeypatch.setattr(auth_router, "password_keys_exist", lambda db: False)
    assert probe() is True


# --------------------------------------------------------------------------
# The limiter
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_limiter():
    """The counter is module state; a leaked bucket fails the NEXT test."""
    auth_router._login_failures.clear()
    yield
    auth_router._login_failures.clear()


@requires_db
def test_repeated_wrong_passwords_stop_being_answered(client) -> None:
    """The budget is spent, then 429 with a Retry-After a client can obey."""
    body = {"email": "student@bgscet.ac.in", "password": "not-the-password"}
    for _ in range(auth_router._LOGIN_MAX_FAILURES):
        assert client.post("/api/auth/login", json=body).status_code == 401

    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    # Points at the door this counter does not gate. A student locked out here
    # — including one an attacker locked out on purpose — still has a way in.
    assert "Google" in r.json()["detail"]


@requires_db
def test_the_limit_holds_even_once_the_password_is_correct(client) -> None:
    """A guesser who finds it on attempt 11 is still refused.

    Checking the budget only on the failure path would make the limiter
    decorative: the one request that matters is the one that succeeds.
    """
    for _ in range(auth_router._LOGIN_MAX_FAILURES):
        client.post(
            "/api/auth/login",
            json={"email": "student@bgscet.ac.in", "password": "wrong"},
        )
    r = client.post(
        "/api/auth/login",
        json={"email": "student@bgscet.ac.in", "password": "student123"},
    )
    assert r.status_code == 429


@requires_db
def test_a_failure_for_one_account_does_not_lock_out_another(client) -> None:
    """THE REGRESSION. Keyed on the account, never on the source address.

    Written with an address bucket first, this failed instantly: every request
    in the suite shares the TestClient's single peer, so ten deliberate
    wrong-password assertions 429'd the other 600 tests. Behind an ALB the same
    thing happens with real users, and the blast radius is every student at once.
    """
    for _ in range(auth_router._LOGIN_MAX_FAILURES + 2):
        client.post(
            "/api/auth/login",
            json={"email": "student@bgscet.ac.in", "password": "wrong"},
        )
    # Same client, same peer address, different account: unaffected.
    r = client.post(
        "/api/auth/login",
        json={"email": "mentor@bgscet.ac.in", "password": "mentor123"},
    )
    assert r.status_code == 200, r.text


@requires_db
def test_a_correct_password_returns_the_budget(client) -> None:
    """A shared lab machine full of typos must not lock out the person who
    eventually types it right, one request later."""
    for _ in range(auth_router._LOGIN_MAX_FAILURES - 1):
        client.post(
            "/api/auth/login",
            json={"email": "student@bgscet.ac.in", "password": "wrong"},
        )
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "student@bgscet.ac.in", "password": "student123"},
        ).status_code
        == 200
    )
    # Budget restored: the earlier failures no longer count against them.
    for _ in range(auth_router._LOGIN_MAX_FAILURES - 1):
        client.post(
            "/api/auth/login",
            json={"email": "student@bgscet.ac.in", "password": "wrong"},
        )
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "student@bgscet.ac.in", "password": "student123"},
        ).status_code
        == 200
    )


@requires_db
def test_an_unknown_address_is_limited_too(client) -> None:
    """Otherwise the limiter answers "does this account exist?".

    An address that can be tried forever is one with no row; an address that
    starts 429ing has one. Charging both keeps the uniform 401 honest.
    """
    ghost = f"nobody-{uuid.uuid4().hex[:8]}@bgscet.ac.in"
    for _ in range(auth_router._LOGIN_MAX_FAILURES):
        assert (
            client.post("/api/auth/login", json={"email": ghost, "password": "x"}).status_code
            == 401
        )
    assert (
        client.post("/api/auth/login", json={"email": ghost, "password": "x"}).status_code == 429
    )


# --------------------------------------------------------------------------
# Issuing the key
# --------------------------------------------------------------------------


@requires_db
def test_set_password_makes_an_sso_only_account_able_to_sign_in(
    client, sso_only_account
) -> None:
    """The end-to-end shape: grant_access mints a key-less account, and only
    set_password gives it one. Opening the door is not itself a key."""
    email = sso_only_account()

    # The sentinel is not scrypt:salt:digest, so nothing verifies against it.
    assert (
        client.post("/api/auth/login", json={"email": email, "password": "anything"}).status_code
        == 401
    )

    with SessionLocal() as db:
        sp.set_password(db, email, "correct horse battery staple")

    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert r.status_code == 200, r.text


@requires_db
def test_revoke_restores_the_sentinel_and_leaves_the_row(client, sso_only_account) -> None:
    """Taking the password away must not take Google access with it."""
    email = sso_only_account()
    with SessionLocal() as db:
        sp.set_password(db, email, "correct horse battery staple")
        user = sp.revoke_password(db, email)
        assert user.password_hash == SSO_ONLY_PASSWORD_HASH
        # The row survives, so google_callback still finds it on the roster.
        assert user.email == email
    assert (
        client.post(
            "/api/auth/login",
            json={"email": email, "password": "correct horse battery staple"},
        ).status_code
        == 401
    )


def test_a_short_password_is_refused() -> None:
    problem = sp.password_problem("short")
    assert problem is not None
    assert str(sp.MIN_PASSWORD_LENGTH) in problem


@pytest.mark.parametrize(
    "published", ["director123", "student123", "mentor123", "alumni123", "Director123"]
)
def test_the_passwords_published_in_this_repository_are_refused(published: str) -> None:
    """These are the exact strings an operator reaches for, and the exact
    strings anyone who has cloned this repo tries first."""
    assert sp.password_problem(published) is not None


def test_a_long_passphrase_is_accepted() -> None:
    assert sp.password_problem("correct horse battery staple") is None


@requires_db
def test_set_password_refuses_an_address_with_no_row() -> None:
    """Creating accounts is grant_access's decision, with a --role it refuses to
    default. Granting privilege must never be a side effect of setting a key."""
    with SessionLocal() as db:
        with pytest.raises(ValueError) as excinfo:
            sp.set_password(db, f"ghost-{uuid.uuid4().hex[:8]}@bgscet.ac.in", "a" * 20)
    assert "grant_access" in str(excinfo.value)


@requires_db
def test_a_weak_password_is_refused_before_it_is_written(sso_only_account) -> None:
    """The refusal must not leave a half-applied hash behind.

    Checked against the DATABASE rather than the return value: a validation that
    raises after committing is indistinguishable from one that raises before,
    right up until the account it half-wrote signs in.
    """
    email = sso_only_account()
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            sp.set_password(db, email, "director123")
    with SessionLocal() as db:
        row = db.query(User).filter(User.email == email).one()
        assert row.password_hash == SSO_ONLY_PASSWORD_HASH
        assert not verify_password("director123", row.password_hash)


# --------------------------------------------------------------------------
# The derived door, and the three-state switch behind it
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), (" TRUE ", True), ("false", False), ("False", False),
     ("", None), ("  ", None), ("yes", None), ("1", None), ("flase", None)],
)
def test_password_login_is_a_three_state_switch(value: str, expected) -> None:
    """A typo degrades to DERIVED, never to open: "flase" is None, not True."""
    assert _settings("prod", value).password_login_forced is expected


@requires_db
def test_password_keys_exist_sees_a_real_hash() -> None:
    """The one query the door asks, run for real: the seed holds scrypt hashes."""
    with SessionLocal() as db:
        assert auth_router.password_keys_exist(db) is True


# --------------------------------------------------------------------------
# Provisioning a key by hash, from the browser
# --------------------------------------------------------------------------

from app import grant_access as ga  # noqa: E402
from app.models.student_profile import StudentProfile  # noqa: E402
from app.models.user import Mentor, Student  # noqa: E402


@pytest.fixture
def granted():
    """Track accounts made through grant_access and tear them down in FK order.

    grant_access can create User + Student + StudentProfile, or User + Mentor,
    and a login writes login_days. Deleting in the wrong order nulls FKs into
    NOT NULL columns and surfaces as an IntegrityError in the NEXT test.
    """
    emails: list[str] = []

    def _track(email: str) -> str:
        emails.append(email.strip().lower())
        return email

    yield _track

    # Core statements, not ORM deletes, ON PURPOSE. `Student.mentor_id` is a bare
    # FK column with no relationship() behind it, so the unit-of-work has no
    # dependency edge between the two mappers and is free to emit `DELETE
    # mentors` before `UPDATE students SET mentor_id = NULL` — which it did, and
    # Postgres refused. Core statements execute in the order written.
    with SessionLocal() as db:
        ids = [
            uid
            for uid in (db.scalar(select(User.id).where(User.email == e)) for e in emails)
            if uid is not None
        ]
        for uid in ids:
            db.execute(delete(LoginDay).where(LoginDay.user_id == uid))
            stu_ids = db.scalars(select(Student.id).where(Student.user_id == uid)).all()
            if stu_ids:
                db.execute(delete(StudentProfile).where(StudentProfile.student_id.in_(stu_ids)))
                # B9.1. `mentor_assignments` points at the student with no
                # `ondelete` — the database refuses to delete somebody out from
                # under a recorded spell — and `app.grant_access` writes one
                # whenever `--mentor` seats a student, which is what this module
                # exercises. Children before parents, same as the rest of this
                # teardown and as both purge modules.
                db.execute(
                    delete(MentorAssignment).where(MentorAssignment.student_id.in_(stu_ids))
                )
                db.execute(delete(Student).where(Student.id.in_(stu_ids)))
        for uid in ids:
            group_ids = db.scalars(select(Mentor.id).where(Mentor.user_id == uid)).all()
            if group_ids:
                db.execute(
                    update(Student).where(Student.mentor_id.in_(group_ids)).values(mentor_id=None)
                )
                db.execute(
                    delete(MentorAssignment).where(MentorAssignment.mentor_id.in_(group_ids))
                )
                db.execute(delete(Mentor).where(Mentor.id.in_(group_ids)))
        for uid in ids:
            db.execute(delete(User).where(User.id == uid))
        db.commit()


def _fresh(label: str) -> str:
    return f"grant-{label}-{uuid.uuid4().hex[:8]}@demo.reep.invalid"


def test_hash_for_paste_applies_the_floor_and_the_denylist() -> None:
    with pytest.raises(ValueError):
        sp.hash_for_paste("short")
    with pytest.raises(ValueError):
        sp.hash_for_paste("director123")
    h = sp.hash_for_paste("correct horse battery staple")
    # Exactly the shape grant_access will accept, and it verifies.
    assert ga._PASSWORD_HASH_RE.fullmatch(h)
    assert verify_password("correct horse battery staple", h)


@pytest.mark.parametrize(
    "bad",
    [
        "correct horse battery staple",  # a PASSWORD, not a hash
        "director123",
        "google-only",  # the sentinel
        "scrypt:abc:def",  # right prefix, wrong lengths
        "sha256:" + "0" * 32 + ":" + "0" * 128,  # wrong KDF
        "scrypt:" + "0" * 32 + ":" + "0" * 127,  # one hex short
        "",
    ],
)
def test_grant_access_refuses_anything_that_is_not_a_scrypt_hash(bad: str) -> None:
    """A password pasted into the hash box by mistake must be REJECTED, not
    stored as a key nobody can use — or worse, stored as a key that works."""
    with pytest.raises(ValueError):
        ga._validate_password_hash(bad)


@requires_db
def test_grant_access_with_a_hash_yields_an_account_that_signs_in(client, granted) -> None:
    """The end-to-end browser path: hash locally, grant with it, sign in."""
    email = granted(_fresh("alumni"))
    h = sp.hash_for_paste("correct horse battery staple")
    with SessionLocal() as db:
        user, created = ga.grant(db, email, "Hashed Alumnus", Role.ALUMNI, password_hash=h)
    assert created is True
    assert user.password_hash == h
    auth_router._login_failures.clear()
    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert r.status_code == 200, r.text


@requires_db
def test_regranting_with_a_hash_rotates_and_without_one_leaves_it(granted) -> None:
    """"Promote this person" must never silently revoke their password."""
    email = granted(_fresh("rotate"))
    first = sp.hash_for_paste("first passphrase here")
    second = sp.hash_for_paste("second passphrase here")
    with SessionLocal() as db:
        ga.grant(db, email, "Rotate", Role.ALUMNI, password_hash=first)
        user, created = ga.grant(db, email, "Rotate", Role.ALUMNI, password_hash=second)
        assert created is False
        assert user.password_hash == second
        # Re-run with NO hash: name/role update only, key untouched.
        user, _ = ga.grant(db, email, "Rotate Renamed", Role.ALUMNI)
        assert user.password_hash == second
        assert user.name == "Rotate Renamed"


@requires_db
def test_a_student_can_be_placed_in_a_mentors_group(granted) -> None:
    """Rule 2 made usable for a demo: the mentor actually sees the student."""
    mentor_email = granted(_fresh("mentor"))
    student_email = granted(_fresh("student"))
    with SessionLocal() as db:
        ga.grant(db, mentor_email, "Demo Mentor", Role.MENTOR, with_group=True)
        ga.grant(
            db, student_email, "Demo Student", Role.STUDENT,
            usn=f"DEMO{uuid.uuid4().hex[:6].upper()}", mentor_email=mentor_email,
        )
        mentor_user = db.scalar(select(User).where(User.email == mentor_email))
        group = db.scalar(select(Mentor).where(Mentor.user_id == mentor_user.id))
        stu_user = db.scalar(select(User).where(User.email == student_email))
        stu = db.scalar(select(Student).where(Student.user_id == stu_user.id))
        assert stu.mentor_id == group.id


@requires_db
def test_mentor_placement_refuses_the_cases_that_would_lie(granted) -> None:
    """Each refusal is a state where the operator would believe something the
    mentor screen would then contradict."""
    groupless = granted(_fresh("groupless"))
    not_a_mentor = granted(_fresh("alum"))
    student_email = granted(_fresh("student"))
    with SessionLocal() as db:
        ga.grant(db, groupless, "No Group", Role.MENTOR)  # no with_group
        ga.grant(db, not_a_mentor, "An Alumnus", Role.ALUMNI)

        # --mentor on a non-student is meaningless.
        with pytest.raises(ValueError, match="STUDENT only"):
            ga.grant(db, _fresh("x"), "X", Role.ALUMNI, mentor_email=groupless)
        # A mentor with no group has nothing to point the student at.
        with pytest.raises(ValueError, match="no Mentor group"):
            ga.grant(db, student_email, "S", Role.STUDENT, usn="DEMOX1", mentor_email=groupless)
        # Not a mentor at all.
        with pytest.raises(ValueError, match="not a MENTOR"):
            ga.grant(db, student_email, "S", Role.STUDENT, usn="DEMOX2", mentor_email=not_a_mentor)
        # Nobody by that address.
        with pytest.raises(ValueError, match="not a MENTOR"):
            ga.grant(db, student_email, "S", Role.STUDENT, usn="DEMOX3", mentor_email=_fresh("ghost"))
