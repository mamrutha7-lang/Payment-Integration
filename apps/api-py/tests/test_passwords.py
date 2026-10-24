"""Activation, forgot-password, change-password, and confirmed registration.

WHAT THESE PIN, and why each one is a test and not a sentence in a docstring:

  * A link works ONCE. The second click is refused, in words that say "already
    used" rather than "expired", because the plan kept `consumed_at` for that.
  * A bad password does NOT burn the link. Policy is checked before the token
    is spent, or a typo costs the person a second email from an admin.
  * "Forgot" answers identically whether the address is real, Google-only or
    unknown. It mails a RESET link to an account that holds a password, a
    SETUP link to a STUDENT that holds none (2026-09-16 - roster-seeded and
    option-B students had no other way to a password), and nothing to a staff
    account that holds none or to any disabled account.
  * A reset signs out every device and kills every other pending link; a
    change keeps THIS device and drops the rest. Both through `token_version`.
  * Activation refuses a student: their first-password walk is onboarding,
    which proves the mailbox with a code first.
  * An application is not decided until its address is confirmed, and an
    auto-approve that provisioning refuses lands in the queue, not the bin.
  * No raw token is ever stored — every hash in both tables is sha256 hex.

Mail is read from `mail_transport.outbox`, which is what the console transport
fills when SES_FROM_ADDRESS is blank — i.e. every test run. The link is parsed
out of the message text exactly as a person would read it.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from conftest import TEST_PASSWORD, application_files, requires_db

from app import mail_transport
from app.config import settings
from app.db import SessionLocal
from app.models.auth_token import AuthToken
from app.models.job import DegreeLevel
from app.models.registration import EmailVerification, Registration, RegistrationRule, RegistrationStatus
from app.models.student_profile import StudentProfile
from app.models.user import LoginDay, Role, Student, User
from app.routers import onboarding as onboarding_router
from app.routers import passwords as passwords_router
from app.routers.registration import SSO_ONLY_PASSWORD_HASH

GOOD = "a memorable phrase of five words"
GOOD2 = "another long and different passphrase"


@pytest.fixture(autouse=True)
def _clean_outbox_and_throttles():
    mail_transport.outbox.clear()
    passwords_router.reset_throttles()
    yield
    mail_transport.outbox.clear()
    passwords_router.reset_throttles()


def _token_from(entry: mail_transport.OutboxEntry) -> str:
    m = re.search(r"token=([A-Za-z0-9_\-]+)", entry.text)
    assert m, entry.text
    return m.group(1)


def _mail_to(address: str, subject_contains: str) -> mail_transport.OutboxEntry:
    hits = [e for e in mail_transport.outbox if e.to == address and subject_contains in e.subject]
    assert hits, f"no mail to {address} with {subject_contains!r}; outbox={list(mail_transport.outbox)}"
    return hits[-1]


def _reg_id(email: str) -> str:
    with SessionLocal() as db:
        rid = db.scalar(select(Registration.id).where(Registration.email == email))
    assert rid, f"no application for {email}"
    return rid


def _make_google_only(user_id: str) -> None:
    """make_user issues a real password; activation is for accounts without one."""
    with SessionLocal() as db:
        db.get(User, user_id).password_hash = SSO_ONLY_PASSWORD_HASH
        db.commit()


# ------------------------------------------------------------- activation --


@requires_db
def test_activation_sets_a_first_password_and_signs_in(client, make_user):
    director = make_user("pw-dir", Role.ADMIN)
    mentor = make_user("pw-new-mentor", Role.MENTOR)
    _make_google_only(mentor.user_id)

    issued = client.post(
        f"/api/admin/users/{mentor.user_id}/activation-link", headers=director.headers
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["emailed"] is False, "no transport is configured in tests"
    mail = _mail_to(mentor.email, "Set up your REEP account")
    assert issued.json()["link"] in mail.text, "the on-screen link and the emailed link are the same link"
    token = _token_from(mail)

    r = client.post("/api/auth/activate", json={"token": token, "password": GOOD})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "MENTOR"
    cookie = {"Cookie": r.headers.get("set-cookie", "")}
    client.cookies.clear()
    assert client.get("/api/auth/me", headers=cookie).status_code == 200, "activation must sign in"

    again = client.post("/api/auth/activate", json={"token": token, "password": GOOD})
    assert again.status_code == 410, again.text
    assert "already been used" in again.json()["detail"]

    login = client.post("/api/auth/login", json={"email": mentor.email, "password": GOOD})
    assert login.status_code == 200, "the password set by activation must work at the door"
    client.cookies.clear()


@requires_db
def test_activation_refuses_a_student(client, make_user):
    """Still refused, and NOT because option B survives — it does not.

    A student's equivalent is the onboarding walk, which makes them confirm the
    address with an emailed CODE before any password is set. An activation link
    is a staff first-password link and skips that step, so handing one to a
    student would trade the mailbox proof for nothing.
    """
    director = make_user("pw-dir2", Role.ADMIN)
    student = make_user("pw-stu", Role.STUDENT)
    r = client.post(f"/api/admin/users/{student.user_id}/activation-link", headers=director.headers)
    assert r.status_code == 422, r.text
    assert "setup link" in r.json()["detail"], "the refusal must name what a student uses instead"
    assert not any(e.to == student.email for e in mail_transport.outbox)


@requires_db
def test_a_bad_password_does_not_burn_the_activation_link(client, make_user):
    director = make_user("pw-dir3", Role.ADMIN)
    mentor = make_user("pw-typo", Role.MENTOR)
    _make_google_only(mentor.user_id)
    client.post(f"/api/admin/users/{mentor.user_id}/activation-link", headers=director.headers)
    token = _token_from(_mail_to(mentor.email, "Set up"))

    short = client.post("/api/auth/activate", json={"token": token, "password": "short"})
    assert short.status_code == 422 and "12" in short.json()["detail"]
    # A published demo password. Note it is refused for LENGTH: every entry in
    # set_password._PUBLISHED_PASSWORDS is under 12 characters, so the 12-floor
    # answers first and the denylist is, today, unreachable. Either refusal is
    # the point here — the link must survive it.
    published = client.post("/api/auth/activate", json={"token": token, "password": "mentor123"})
    assert published.status_code == 422

    ok = client.post("/api/auth/activate", json={"token": token, "password": GOOD})
    assert ok.status_code == 200, "two refusals must not have spent the link"
    client.cookies.clear()


@requires_db
def test_an_expired_activation_link_is_refused_with_the_right_words(client, make_user):
    director = make_user("pw-dir4", Role.ADMIN)
    mentor = make_user("pw-late", Role.MENTOR)
    _make_google_only(mentor.user_id)
    client.post(f"/api/admin/users/{mentor.user_id}/activation-link", headers=director.headers)
    token = _token_from(_mail_to(mentor.email, "Set up"))
    with SessionLocal() as db:
        row = db.scalar(select(AuthToken).where(AuthToken.user_id == mentor.user_id))
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    r = client.post("/api/auth/activate", json={"token": token, "password": GOOD})
    assert r.status_code == 410
    assert "expired" in r.json()["detail"]


@requires_db
@pytest.mark.parametrize("door", ["activate", "reset"])
def test_a_shortened_link_is_refused_as_a_link(client, door):
    """A link a mail client cut short is a DEAD LINK, and is answered as one.

    The schema used to refuse a token under 16 characters as a 422 about the
    request, and the set-password screen reads a 422 as a refused PASSWORD: the
    person was asked for another one, and no password could ever get past it.
    It now reaches the lookup like any other token nobody issued.
    """
    r = client.post(f"/api/auth/{door}", json={"token": "abc123", "password": GOOD})
    assert r.status_code == 410, r.text
    assert r.json()["detail"] == "This link is not valid. Ask for a new one."


@requires_db
@pytest.mark.parametrize(
    "step, body",
    [
        ("start", {"email": "someone@bgscet.ac.in"}),
        ("verify", {"code": "123456"}),
    ],
)
def test_a_shortened_setup_link_is_refused_as_a_link(client, step, body):
    """The /onboard walk's twin of the test above, on the link every approved
    student is mailed and "Forgot password?" mails a student with no password.

    Its first two steps carried the same 16-character floor on the token, and
    that screen prints a 422's message under the address box as something to
    fix: the student read "String should have at least 16 characters" each
    time they retyped their address and was never told the link was dead. A
    410 is what the screen draws as a dead link, with `_REFUSED`'s words.
    """
    r = client.post(f"/api/auth/onboard/{step}", json={"token": "abc123", **body})
    assert r.status_code == 410, r.text
    assert r.json()["detail"] == onboarding_router._REFUSED


@requires_db
def test_reissuing_an_activation_link_supersedes_the_old_one(client, make_user):
    """"Resend" must hand over ONE working link, not two."""
    director = make_user("pw-dir5", Role.ADMIN)
    mentor = make_user("pw-resend", Role.MENTOR)
    _make_google_only(mentor.user_id)
    client.post(f"/api/admin/users/{mentor.user_id}/activation-link", headers=director.headers)
    first = _token_from(_mail_to(mentor.email, "Set up"))
    mail_transport.outbox.clear()
    client.post(f"/api/admin/users/{mentor.user_id}/activation-link", headers=director.headers)
    second = _token_from(_mail_to(mentor.email, "Set up"))
    assert first != second
    assert client.post("/api/auth/activate", json={"token": first, "password": GOOD}).status_code == 410
    assert client.post("/api/auth/activate", json={"token": second, "password": GOOD}).status_code == 200
    client.cookies.clear()


@requires_db
def test_an_activation_link_cannot_set_a_password_on_an_account_that_has_one(
    client, make_user
):
    """B3.4. THE ONE THAT MATTERS. An activation link sets a FIRST password.

    `POST /api/admin/users/{id}/activation-link` will mint one for any staff
    account on request - that is deliberate, it is how "the email never
    arrived" gets closed on the phone. Until this refusal existed, re-minting
    one for a faculty member who ALREADY HELD a password let whoever ended up
    holding that link set a new one on a live account: an account takeover
    wearing a support action's clothes, and one that contradicts the product's
    own rule that an admin never sets somebody's password.

    Two locks, because they close different doors. `issue_activation` refuses
    to MINT (nothing new goes into circulation); `activate` refuses to SPEND
    (the links already handed out stop working). Delete either and the other
    leaves a live path - the first alone leaves yesterday's link working, the
    second alone leaves an admin able to mint links that always fail.
    """
    director = make_user("pw-dir-b34", Role.ADMIN)
    mentor = make_user("pw-has-password", Role.MENTOR)
    _make_google_only(mentor.user_id)

    # A link is minted while the account still has no password, and set aside.
    client.post(f"/api/admin/users/{mentor.user_id}/activation-link", headers=director.headers)
    stashed = _token_from(_mail_to(mentor.email, "Set up"))
    first = client.post("/api/auth/activate", json={"token": stashed, "password": GOOD})
    assert first.status_code == 200, first.text
    client.cookies.clear()

    # LOCK 1: no new link may be minted for it.
    refused = client.post(
        f"/api/admin/users/{mentor.user_id}/activation-link", headers=director.headers
    )
    assert refused.status_code == 422, refused.text
    assert "Forgot password" in refused.json()["detail"], (
        "the refusal must name the door that DOES work"
    )

    # LOCK 2: a link minted before the password existed is dead too. This one
    # is live by construction - it is re-issued straight into the table, which
    # is what "a link already in somebody's inbox" looks like from here.
    with SessionLocal() as db:
        from datetime import timedelta as _td

        from app import account_links
        from app.models.auth_token import PURPOSE_ACTIVATION as _P

        raw, _row = account_links.issue_user_token(db, db.get(User, mentor.user_id), _P, _td(hours=1))
        db.commit()
    spend = client.post("/api/auth/activate", json={"token": raw, "password": "a different long passphrase"})
    assert spend.status_code == 410, spend.text
    assert "Forgot password" in spend.json()["detail"]

    # And the password they actually chose still works.
    ok = client.post("/api/auth/login", json={"email": mentor.email, "password": GOOD})
    assert ok.status_code == 200, "the refused link must not have changed anything"
    client.cookies.clear()


@requires_db
def test_a_spent_link_still_says_it_was_used_rather_than_the_new_refusal(client, make_user):
    """ORDER, and it is the difference between a useful message and a confusing one.

    Both refusals are 410. A person who clicks the same link twice must be told
    "already used"; the B3.4 refusal is for the DIFFERENT case of a live link
    against an account that has a password. Checking the password first would
    collapse the two, and the commonest one - a double click - would start
    telling people to use Forgot password for no reason.
    """
    director = make_user("pw-dir-order", Role.ADMIN)
    mentor = make_user("pw-order", Role.MENTOR)
    _make_google_only(mentor.user_id)
    client.post(f"/api/admin/users/{mentor.user_id}/activation-link", headers=director.headers)
    token = _token_from(_mail_to(mentor.email, "Set up"))
    assert client.post("/api/auth/activate", json={"token": token, "password": GOOD}).status_code == 200
    client.cookies.clear()

    again = client.post("/api/auth/activate", json={"token": token, "password": GOOD})
    assert again.status_code == 410
    assert "already been used" in again.json()["detail"], again.text


# ----------------------------------------------------------------- forgot --


@requires_db
def test_forgot_answers_identically_and_mails_each_account_what_it_can_use(client, make_user):
    with_password = make_user("pw-has", Role.MENTOR)
    google_only = make_user("pw-google", Role.MENTOR)
    _make_google_only(google_only.user_id)
    student_no_password = make_user("pw-student-none", Role.STUDENT)
    _make_google_only(student_no_password.user_id)

    answers = set()
    for email in (
        "nobody-" + with_password.email,
        google_only.email,
        with_password.email,
        student_no_password.email,
    ):
        r = client.post("/api/auth/forgot", json={"email": email})
        assert r.status_code == 202, r.text
        answers.add(r.json()["detail"])
    assert len(answers) == 1, "the answer must not depend on whether the address exists"

    resets = [e for e in mail_transport.outbox if "Reset" in e.subject]
    assert [e.to for e in resets] == [with_password.email], (
        "only the account that HOLDS a password gets a reset link"
    )
    setups = [e for e in mail_transport.outbox if "Set a password" in e.subject]
    assert [e.to for e in setups] == [student_no_password.email], (
        "a STUDENT holding no password gets the setup link; a staff account "
        "holding none gets nothing (its first password is the admin's activation link)"
    )
    assert not [e for e in mail_transport.outbox if e.to == google_only.email]


@requires_db
def test_forgot_walks_a_password_less_student_to_a_first_password(client, make_user):
    """THE REPORT: a student who could sign in with Google and not with a
    password, who typed the right address and was sent nothing. Roster-seeded
    and option-B accounts hold the sentinel and were never mailed a setup link;
    `forgot` skipped them, `change-password` answered 409, and the console has
    no button. Now `forgot` mails the same `/onboard` walk approval sends."""
    student = make_user("pw-roster", Role.STUDENT)
    _make_google_only(student.user_id)
    mail_transport.outbox.clear()

    r = client.post("/api/auth/forgot", json={"email": student.email})
    assert r.status_code == 202, r.text
    setup = _mail_to(student.email, "Set a password")
    assert "/onboard?token=" in setup.text
    assert "approved" not in setup.text.lower(), (
        "this student was never an applicant; the mail must not say their "
        "registration was approved"
    )
    token = _token_from(setup)

    started = client.post("/api/auth/onboard/start", json={"token": token, "email": student.email})
    assert started.status_code == 200, started.text
    code = re.search(r"\b(\d{6})\b", _mail_to(student.email, "verification code").text).group(1)
    ok = client.post("/api/auth/onboard/verify", json={"token": token, "code": code})
    assert ok.status_code == 200, ok.text
    done = client.post(
        "/api/auth/onboard/password", json={"ticket": ok.json()["ticket"], "password": GOOD}
    )
    assert done.status_code == 200, done.text

    signed_in = client.post("/api/auth/login", json={"email": student.email, "password": GOOD})
    assert signed_in.status_code == 200, signed_in.text
    # And the link is spent: the walk cannot be run twice off one mail.
    again = client.post("/api/auth/onboard/start", json={"token": token, "email": student.email})
    assert again.status_code == 410


@requires_db
def test_forgot_sends_a_disabled_password_less_student_nothing(client, make_user):
    """Offboarded is offboarded (B3.3): the setup branch must sit BEHIND the
    disabled check, or disabling a roster account leaves it a way back in."""
    student = make_user("pw-roster-off", Role.STUDENT)
    _make_google_only(student.user_id)
    with SessionLocal() as db:
        db.get(User, student.user_id).disabled_at = datetime.now(timezone.utc)
        db.commit()
    mail_transport.outbox.clear()

    r = client.post("/api/auth/forgot", json={"email": student.email})
    assert r.status_code == 202, r.text
    assert not [e for e in mail_transport.outbox if e.to == student.email]


@requires_db
def test_a_password_less_student_is_pointed_at_forgot_by_change_password(client, make_user, login):
    """The 409 used to name "the link you were emailed", which a roster-seeded
    student never was. It must name the door that exists."""
    student = make_user("pw-student-409", Role.STUDENT)
    headers = login(student.email, TEST_PASSWORD)
    _make_google_only(student.user_id)
    r = client.post("/api/auth/change-password/code", headers=headers)
    assert r.status_code == 409, r.text
    assert "Forgot password" in r.json()["detail"]


@requires_db
def test_forgot_is_throttled_per_address(client, make_user):
    mentor = make_user("pw-flood", Role.MENTOR)
    codes = [client.post("/api/auth/forgot", json={"email": mentor.email}).status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429], codes
    assert sum(1 for e in mail_transport.outbox if e.to == mentor.email) == 3


@requires_db
def test_reset_signs_out_every_device_and_kills_other_links(client, make_user, login):
    mentor = make_user("pw-reset", Role.MENTOR)
    device_a = login(mentor.email, TEST_PASSWORD)
    assert client.get("/api/auth/me", headers=device_a).status_code == 200

    client.post("/api/auth/forgot", json={"email": mentor.email})
    link1 = _token_from(_mail_to(mentor.email, "Reset"))
    mail_transport.outbox.clear()
    client.post("/api/auth/forgot", json={"email": mentor.email})
    link2 = _token_from(_mail_to(mentor.email, "Reset"))

    # Superseded on issue: only the newest link works.
    assert client.post("/api/auth/reset", json={"token": link1, "password": GOOD}).status_code == 410

    done = client.post("/api/auth/reset", json={"token": link2, "password": GOOD})
    assert done.status_code == 200, done.text
    assert "set-cookie" not in {k.lower() for k in done.headers}, "a reset signs in NOBODY, this device included"

    assert client.get("/api/auth/me", headers=device_a).status_code == 401, "every device signed out"
    assert client.post("/api/auth/login", json={"email": mentor.email, "password": TEST_PASSWORD}).status_code != 200
    assert client.post("/api/auth/login", json={"email": mentor.email, "password": GOOD}).status_code == 200
    client.cookies.clear()

    used = client.post("/api/auth/reset", json={"token": link2, "password": GOOD2})
    assert used.status_code == 410 and "already been used" in used.json()["detail"]


# ----------------------------------------------------------------- change --


@requires_db
def test_change_password_keeps_this_device_and_drops_the_others(client, make_user, login):
    """Two rules in one path, and they used to be tested with two live devices.

    An account can no longer HOLD two live devices: one-device-at-a-time means
    the second sign-in retires the first, which this test now asserts on its way
    to the change-password flow rather than assuming otherwise. What remains
    specific to change-password is the half that is not automatic — the cookie in
    the hand of the person making the change is re-issued, so they are not signed
    out by their own action, while the token they arrived with stops working.
    """
    mentor = make_user("pw-change", Role.MENTOR)
    device_a = login(mentor.email, TEST_PASSWORD)
    device_b = login(mentor.email, TEST_PASSWORD)
    # The second sign-in already retired the first — no password change needed.
    assert client.get("/api/auth/me", headers=device_a).status_code == 401, (
        "signing in on a second device must retire the first"
    )
    assert client.get("/api/auth/me", headers=device_b).status_code == 200

    wrong = client.post(
        "/api/auth/change-password",
        headers=device_b,
        json={"current_password": "not it at all", "new_password": GOOD},
    )
    assert wrong.status_code == 403
    same = client.post(
        "/api/auth/change-password",
        headers=device_b,
        json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
    )
    assert same.status_code == 422
    short = client.post(
        "/api/auth/change-password",
        headers=device_b,
        json={"current_password": TEST_PASSWORD, "new_password": "short"},
    )
    assert short.status_code == 422

    ok = client.post(
        "/api/auth/change-password",
        headers=device_b,
        json={"current_password": TEST_PASSWORD, "new_password": GOOD},
    )
    assert ok.status_code == 200, ok.text
    device_b_new = {"Cookie": ok.headers.get("set-cookie", "")}
    client.cookies.clear()

    assert client.get("/api/auth/me", headers=device_b_new).status_code == 200, "this device stays"
    assert client.get("/api/auth/me", headers=device_b).status_code == 401, (
        "the OLD cookie on this device is out"
    )


@requires_db
def test_change_password_refuses_a_google_only_account(client, make_user, login):
    mentor = make_user("pw-nochange", Role.MENTOR)
    headers = login(mentor.email, TEST_PASSWORD)
    _make_google_only(mentor.user_id)
    r = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": TEST_PASSWORD, "new_password": GOOD},
    )
    assert r.status_code == 409


# --------------------------------------------------- confirmed registration --


@pytest.fixture
def application(tmp_document_store):
    """A public application by email, torn down whatever the test does —
    including the User/Student a confirmed auto-approve may have minted.

    Multipart with both files, as the form posts it (2026-09-22); the store
    is the per-test directory `tmp_document_store` points it at."""
    emails: list[str] = []
    rules: list[str] = []

    def _submit(client, email: str, *, usn: str | None = None, name="Applicant"):
        emails.append(email)
        with SessionLocal() as db:
            db.execute(delete(Registration).where(Registration.email == email))
            db.commit()
        # USN, phone, personal email and LinkedIn are required at the schema
        # (2026-09-16); a caller that names no USN gets a unique one.
        return client.post(
            "/api/register",
            data={
                "name": name,
                "email": email,
                "usn": usn or f"1BG26PWD{uuid.uuid4().hex[:3].upper()}",
                "phone": "+91 90000 00000",
                "personal_email": f"personal.{uuid.uuid4().hex[:8]}@gmail.com",
                "linkedin_url": "https://www.linkedin.com/in/password-applicant",
                "degree_level": "PG",
            },
            files=application_files(),
        )

    def _rule(**kw) -> str:
        with SessionLocal() as db:
            rule = RegistrationRule(**kw)
            db.add(rule)
            db.commit()
            rules.append(rule.id)
            return rule.id

    yield _submit, _rule

    with SessionLocal() as db:
        for rid in rules:
            db.execute(delete(RegistrationRule).where(RegistrationRule.id == rid))
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                for stu in db.scalars(select(Student).where(Student.user_id == user.id)).all():
                    db.execute(delete(StudentProfile).where(StudentProfile.student_id == stu.id))
                    db.execute(delete(Student).where(Student.id == stu.id))
                # login_days.user_id carries no ON DELETE, so it goes by hand -
                # an onboarded applicant now SIGNS IN during these tests, which
                # the fixture never had to survive while students held no
                # password.
                db.execute(delete(LoginDay).where(LoginDay.user_id == user.id))
                db.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
            db.execute(delete(Registration).where(Registration.email == email))
        db.commit()


@requires_db
def test_an_application_reaches_the_queue_without_any_email(client, make_user, application):
    """THE INCIDENT, inverted into a test.

    Submission used to write PENDING_VERIFICATION and wait for a confirmation
    link. On a deployment whose SES account is sandboxed that mail cannot be
    delivered to a student address at all, so every applicant sat invisible:
    the student saw a 201, the admin saw an empty queue, and the retry hit the
    duplicate guard's opaque 409. The mailbox proof moved AFTER approval; what
    must never come back is a submission that no human can see.
    """
    admin = make_user("pw-queue", Role.ADMIN)
    submit, _ = application
    email = "pw.applicant@bgscet.ac.in"
    r = submit(client, email, usn="1BG26PWD01")
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "PENDING_REVIEW"

    queue = client.get("/api/register/pending", headers=admin.headers).json()
    assert any(row["email"] == email for row in queue), (
        "an application must reach the review queue on its own, with no mail in between"
    )


@requires_db
def test_approval_emails_a_setup_link_and_the_walk_ends_at_a_password(client, make_user, application):
    """Approve -> link -> address -> code -> password. And no session at the end."""
    admin = make_user("pw-walk", Role.ADMIN)
    submit, _ = application
    email = "pw.walk@bgscet.ac.in"
    submit(client, email, usn="1BG26PWD03", name="Walk Student")
    with SessionLocal() as db:
        reg_id = db.scalar(select(Registration.id).where(Registration.email == email))
    mail_transport.outbox.clear()

    r = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert r.status_code == 200, r.text
    invite = _mail_to(email, "approved")
    assert "Google" not in invite.text, "option B is over; the mail carries the way in"
    token = _token_from(invite)

    # Step 1 - the address typed back. A wrong one must not burn the link.
    wrong = client.post(
        "/api/auth/onboard/start", json={"token": token, "email": "someone.else@bgscet.ac.in"}
    )
    assert wrong.status_code == 410
    started = client.post("/api/auth/onboard/start", json={"token": token, "email": email})
    assert started.status_code == 200, started.text

    # Step 2 - the emailed code.
    code = re.search(r"\b(\d{6})\b", _mail_to(email, "verification code").text).group(1)
    bad = client.post("/api/auth/onboard/verify", json={"token": token, "code": "000000"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/onboard/verify", json={"token": token, "code": code})
    assert ok.status_code == 200, ok.text
    ticket = ok.json()["ticket"]

    # Step 3 - the password, and NO session.
    short = client.post("/api/auth/onboard/password", json={"ticket": ticket, "password": "short"})
    assert short.status_code == 422 and "12" in short.json()["detail"]
    done = client.post("/api/auth/onboard/password", json={"ticket": ticket, "password": GOOD})
    assert done.status_code == 200, done.text
    assert "set-cookie" not in {k.lower() for k in done.headers}, (
        "onboarding ends at the login screen; it must not mint a session"
    )

    # And the password works at the ordinary front door.
    signed_in = client.post("/api/auth/login", json={"email": email, "password": GOOD})
    assert signed_in.status_code == 200, signed_in.text


@requires_db
def test_the_code_cannot_be_skipped_by_holding_the_link(client, make_user, application):
    """The invite alone must not reach the password step.

    This is the whole reason `onboard/password` takes a TICKET and not the
    link: a forwarded invite survives step 1, and if the password step trusted
    it, the mailbox proof would be decorative.
    """
    admin = make_user("pw-skip", Role.ADMIN)
    submit, _ = application
    email = "pw.skip@bgscet.ac.in"
    submit(client, email, usn="1BG26PWD04")
    with SessionLocal() as db:
        reg_id = db.scalar(select(Registration.id).where(Registration.email == email))
    client.post(f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"})
    token = _token_from(_mail_to(email, "approved"))

    straight = client.post("/api/auth/onboard/password", json={"ticket": token, "password": GOOD})
    assert straight.status_code == 410, "the invite is not a ticket"


@requires_db
def test_a_rejection_tells_the_applicant_why(client, make_user, application):
    admin = make_user("pw-reject", Role.ADMIN)
    submit, _ = application
    email = "pw.reject@bgscet.ac.in"
    submit(client, email, usn="1BG26PWD05")
    with SessionLocal() as db:
        reg_id = db.scalar(select(Registration.id).where(Registration.email == email))
    mail_transport.outbox.clear()

    bare = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "REJECT"}
    )
    assert bare.status_code == 422, "a refusal the applicant cannot answer is not a refusal"

    r = client.post(
        f"/api/register/{reg_id}/decision",
        headers=admin.headers,
        json={"decision": "REJECT", "note": "USN does not match any 2026 intake."},
    )
    assert r.status_code == 200, r.text
    assert "USN does not match any 2026 intake." in _mail_to(email, "About your REEP registration").text


@requires_db
def test_an_auto_approve_that_provisioning_refuses_lands_in_the_queue(client, application):
    """The domain fence beats the rule, and the human sees why.

    Off the college's domains but not a public mail provider: a Gmail address in
    the college box is refused at the form itself since 2026-09-29, before any
    rule runs, which would leave this fence untested."""
    submit, rule = application
    email = "pw.attacker@attacker.example"
    rule(name="pw-test-bad-rule", email_domain="attacker.example", auto_approve=True, priority=0, enabled=True)
    submit(client, email, name="Priya Sharma")

    with SessionLocal() as db:
        reg = db.scalar(select(Registration).where(Registration.email == email))
        assert reg.status is RegistrationStatus.PENDING_REVIEW
        assert "would auto-approve, but" in (reg.decision_reason or "")
        assert db.scalar(select(User).where(User.email == email)) is None, "no account minted"


@requires_db
def test_a_student_can_change_their_password_with_an_emailed_code(client, make_user, login):
    """Option B refused a student here with a 409. They set passwords now."""
    student = make_user("pw-otpchange", Role.STUDENT)
    headers = login(student.email, TEST_PASSWORD)
    mail_transport.outbox.clear()

    asked = client.post("/api/auth/change-password/code", headers=headers)
    assert asked.status_code == 200, asked.text
    code = re.search(r"\b(\d{6})\b", _mail_to(student.email, "password-change code").text).group(1)

    wrong = client.post(
        "/api/auth/change-password", headers=headers, json={"code": "000000", "new_password": GOOD}
    )
    assert wrong.status_code == 403

    r = client.post(
        "/api/auth/change-password", headers=headers, json={"code": code, "new_password": GOOD}
    )
    assert r.status_code == 200, r.text
    assert client.post("/api/auth/login", json={"email": student.email, "password": GOOD}).status_code == 200

    replay = client.post(
        "/api/auth/change-password", headers=headers, json={"code": code, "new_password": GOOD + "x"}
    )
    assert replay.status_code in (401, 403), "a spent code must not authorise a second change"


@requires_db
def test_a_rejected_new_password_does_not_burn_the_code(client, make_user, login):
    """A typo must not cost the code.

    The endpoint used to prove authorisation FIRST — which consumes the
    one-time code and commits it — and check the password policy afterwards.
    So "abc" came back 422, the code was already spent, and the next attempt
    with a perfectly good password was refused: the person had to ask for
    another code to fix a typo, and the form looked like it had eaten the one
    they were sent. `reset` and the onboarding password step have always
    peeked, validated, then consumed. This pins the same order here.
    """
    student = make_user("pw-typo", Role.STUDENT)
    headers = login(student.email, TEST_PASSWORD)
    mail_transport.outbox.clear()

    assert client.post("/api/auth/change-password/code", headers=headers).status_code == 200
    code = re.search(r"\b(\d{6})\b", _mail_to(student.email, "password-change code").text).group(1)

    rejected = client.post(
        "/api/auth/change-password", headers=headers, json={"code": code, "new_password": "abc"}
    )
    assert rejected.status_code == 422, rejected.text

    # THE ASSERTION THAT FAILS IF THE ORDER IS EVER PUT BACK: the same code,
    # unspent, still authorises the change.
    retry = client.post(
        "/api/auth/change-password", headers=headers, json={"code": code, "new_password": GOOD}
    )
    assert retry.status_code == 200, retry.text
    assert (
        client.post("/api/auth/login", json={"email": student.email, "password": GOOD}).status_code
        == 200
    )


@requires_db
def test_change_password_needs_exactly_one_proof(client, make_user, login):
    staff = make_user("pw-oneproof", Role.MENTOR)
    headers = login(staff.email, TEST_PASSWORD)
    neither = client.post("/api/auth/change-password", headers=headers, json={"new_password": GOOD})
    assert neither.status_code == 403
    both = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"new_password": GOOD, "current_password": TEST_PASSWORD, "code": "123456"},
    )
    assert both.status_code == 403, "one proof, not two - otherwise a guessed code rides a known password"


# ------------------------------------------------------------------ guard --


@requires_db
def test_no_raw_token_is_ever_stored(make_user):
    """The stored value is sha256 hex and is NOT the raw token.

    THE FIRST VERSION ONLY READ EXISTING ROWS — and created none — so a mutation
    that stored the raw token passed it, because every row it could see had
    been written before the mutation. A guard has to exercise the writer. This
    issues a link under whatever code is running and checks THAT row.
    """
    from datetime import timedelta

    from app import account_links
    from app.models.auth_token import PURPOSE_ACTIVATION

    hexhash = re.compile(r"^[0-9a-f]{64}$")
    mentor = make_user("pw-hashguard", Role.MENTOR)
    with SessionLocal() as db:
        user = db.get(User, mentor.user_id)
        raw, row = account_links.issue_user_token(db, user, PURPOSE_ACTIVATION, timedelta(hours=1))
        db.commit()
        stored = row.token_hash
    assert stored != raw, "the raw token was stored — a working link for anyone who reads the dump"
    assert hexhash.match(stored), f"not sha256 hex: {stored[:20]}"
    with SessionLocal() as db:
        verif = db.scalars(select(EmailVerification.token_hash)).all()
    assert all(hexhash.match(h) for h in verif)


# --------------------------------------------- the arbiter, tested directly --
# `activate` and `reset` call `peek_user_token` before `consume_user_token`, so
# through the endpoints the peek answers first and consume's own predicates
# are never the deciding check. Under two racing clicks the peek is NOT the
# arbiter — the atomic UPDATE is — so its predicates are tested here without
# the peek in front of them. A mutation proved the endpoint tests alone could
# not see either predicate go missing.


@requires_db
def test_consume_refuses_a_second_call_without_any_peek(make_user):
    from datetime import timedelta

    from app import account_links
    from app.models.auth_token import PURPOSE_ACTIVATION

    mentor = make_user("pw-arbiter", Role.MENTOR)
    with SessionLocal() as db:
        user = db.get(User, mentor.user_id)
        raw, _ = account_links.issue_user_token(db, user, PURPOSE_ACTIVATION, timedelta(hours=1))
        db.commit()
        first = account_links.consume_user_token(db, PURPOSE_ACTIVATION, raw)
        first_id = first.id if first is not None else None  # read before commit expires it
        db.commit()
        second = account_links.consume_user_token(db, PURPOSE_ACTIVATION, raw)
        db.commit()
    assert first_id == mentor.user_id
    assert second is None, "the atomic UPDATE must refuse a consumed row on its own"


@requires_db
def test_consume_refuses_an_expired_link_without_any_peek(make_user):
    from datetime import timedelta

    from app import account_links
    from app.models.auth_token import PURPOSE_ACTIVATION

    mentor = make_user("pw-arbiter-exp", Role.MENTOR)
    with SessionLocal() as db:
        user = db.get(User, mentor.user_id)
        raw, row = account_links.issue_user_token(db, user, PURPOSE_ACTIVATION, timedelta(hours=1))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert account_links.consume_user_token(db, PURPOSE_ACTIVATION, raw) is None


def test_policy_is_checked_before_the_link_is_spent_in_the_source():
    """No database. Pins the ORDER in `activate` and `reset`, textually.

    The behavioural test above passes for two reasons, and only one of them is
    the design: (1) the policy check comes first, and (2) `get_db` never
    commits, so an HTTPException raised after a consume rolls the consume back
    anyway. A mutation that swapped the order survived because of (2) — which
    means one stray `db.commit()` placed before the policy check would quietly
    start burning links on typos, and no behavioural test would notice until it
    did. So the intended order is pinned here, where a swap fails the build.
    """
    import inspect

    from app.routers import passwords as mod

    for fn in (mod.activate, mod.reset):
        src = inspect.getsource(fn)
        policy = src.index("password_problem(")
        spend = src.index("consume_user_token(")
        assert policy < spend, f"{fn.__name__}: the link is consumed before the password is checked"
