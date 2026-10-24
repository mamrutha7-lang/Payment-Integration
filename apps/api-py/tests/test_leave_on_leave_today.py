"""Two more audiences for leave mail (2026-09-24).

1. WHOEVER CAN DECIDE A FACULTY APPLICATION IS TOLD IT IS WAITING: the Main
   Admin and every faculty member holding a live `admin.leave_approvals` grant
   — never the applicant, never a colleague without the grant.
2. "<NAME> IS ON LEAVE TODAY" GOES TO EVERY OTHER FACULTY MEMBER ON THE LEAVE
   DAY, NOT ON APPROVAL. The owner's words: the mail belongs to the leave day.
   An approval ahead of time sends nothing; the morning job
   (`python -m app.leave_today_job`) sends it on each day of the leave; an
   approval that lands ON a leave day sends it then, and the job does not send
   it a second time.

And what neither may carry: the `reason` (routinely medical), and in the
broadcast the printed option too — "loss-of-pay" and "restricted holiday" are
not their colleagues' business.

Every date here is `local_today()`, the programme's day, never `date.today()`:
the container is UTC and India is five and a half hours ahead, so between 18:30
and midnight UTC the two disagree and a test written on the other would be
right only by the hour it ran.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app import leave_mail, leave_today_job, mail_transport
from app.clock import local_today
from app.config import settings
from app.db import SessionLocal
from app.models.governance import CapabilityGrant, SubjectKind
from app.models.leave import LeaveRequest
from app.models.mail import MailLog
from app.models.user import Role, User
from app.routers.leave import LEAVE_APPROVAL_CAPABILITY

LEAVES = "/api/leaves"
REASON = "Chemotherapy, day two of the second cycle."


@pytest.fixture
def leave_ids():
    """Every leave a test makes, and every mail row keyed on it, removed after —
    a `mail_logs` row outlives the account and the request it names."""
    made: list[str] = []
    yield made
    with SessionLocal() as db:
        for leave_id in made:
            db.execute(delete(MailLog).where(MailLog.dedupe_key.like(f"leave%:{leave_id}:%")))
        db.commit()


@pytest.fixture
def mail_on(monkeypatch):
    monkeypatch.setattr(settings, "leave_mail_enabled", True)
    mail_transport.outbox.clear()
    yield
    mail_transport.outbox.clear()


def _submit(client, account, leave_ids, *, start, end, kind="CASUAL") -> str:
    r = client.post(
        LEAVES,
        headers=account.headers,
        json={
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "reason": REASON,
            "leave_kind": kind,
        },
    )
    assert r.status_code == 201, r.text
    leave_ids.append(r.json()["id"])
    return r.json()["id"]


def _approve(client, office, leave_id: str) -> None:
    r = client.post(f"{LEAVES}/{leave_id}/decision", headers=office.headers, json={"decision": "APPROVE"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED"


def _to(address: str) -> list[mail_transport.OutboxEntry]:
    return [m for m in mail_transport.outbox if m.to == address]


def _keys(prefix: str) -> set[str]:
    with SessionLocal() as db:
        return set(db.scalars(select(MailLog.dedupe_key).where(MailLog.dedupe_key.like(f"{prefix}%"))).all())


def _grant_leave_approvals(user_id: str, **extra) -> None:
    """A row, not the endpoint: the Main Admin's grant is live at once and this
    is the state that grant produces. `make_user`'s teardown removes it."""
    with SessionLocal() as db:
        db.add(
            CapabilityGrant(
                capability=LEAVE_APPROVAL_CAPABILITY,
                subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                reason="the on-leave mail tests need a delegate approver, twenty characters plus",
                **extra,
            )
        )
        db.commit()


def test_the_approvers_key_is_the_routers_key() -> None:
    """`leave_mail` restates the key rather than importing the router that
    imports it. The two must not drift: a list of approvers that asks about a
    different key from the gate mails people who cannot press Sanction."""
    assert leave_mail.LEAVE_APPROVAL_CAPABILITY == LEAVE_APPROVAL_CAPABILITY


# ---------------------------------------------------------------- approvers --


@requires_db
def test_a_faculty_application_is_mailed_to_everybody_who_can_decide_it(
    client, make_user, leave_ids, mail_on
):
    applicant = make_user("olt-app", Role.MENTOR)
    office = make_user("olt-office", Role.ADMIN)
    delegate = make_user("olt-dele", Role.MENTOR)
    lapsed = make_user("olt-lapsed", Role.MENTOR)
    colleague = make_user("olt-coll", Role.MENTOR)
    _grant_leave_approvals(delegate.user_id)
    # A grant that has run out opens nothing at the gate, so it mails nobody.
    _grant_leave_approvals(lapsed.user_id, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    # The applicant holding the grant too: nobody decides their own request.
    _grant_leave_approvals(applicant.user_id)

    day = local_today() + timedelta(days=5)
    leave_id = _submit(client, applicant, leave_ids, start=day, end=day)

    approver_keys = _keys(f"leave-approval:{leave_id}:")
    assert f"leave-approval:{leave_id}:{office.user_id}" in approver_keys
    assert f"leave-approval:{leave_id}:{delegate.user_id}" in approver_keys
    for outsider in (applicant, lapsed, colleague):
        assert f"leave-approval:{leave_id}:{outsider.user_id}" not in approver_keys

    for approver in (office, delegate):
        (mail,) = _to(approver.email)
        assert "Voice Test olt-app" in mail.subject
        assert day.isoformat() in mail.text
        assert REASON not in mail.text and REASON not in mail.subject
    assert _to(colleague.email) == [] and _to(lapsed.email) == []
    # The applicant hears only about their own request, as before.
    (own,) = _to(applicant.email)
    assert own.subject == "Your leave request has been received"


@requires_db
def test_a_student_application_mails_no_approver(client, make_user, leave_ids, mail_on):
    student = make_user("olt-stud", Role.STUDENT)
    make_user("olt-office2", Role.ADMIN)
    day = local_today() + timedelta(days=5)
    leave_id = _submit(client, student, leave_ids, start=day, end=day)
    assert _keys(f"leave-approval:{leave_id}:") == set()


@requires_db
def test_with_leave_mail_off_nobody_is_mailed(client, make_user, leave_ids):
    """The default. Neither the approvers nor the morning job write a row."""
    assert settings.leave_mail_enabled is False
    mail_transport.outbox.clear()
    applicant = make_user("olt-off", Role.MENTOR)
    office = make_user("olt-off-office", Role.ADMIN)
    make_user("olt-off-coll", Role.MENTOR)
    today = local_today()
    leave_id = _submit(client, applicant, leave_ids, start=today, end=today)
    _approve(client, office, leave_id)
    with SessionLocal() as db:
        summary = leave_mail.announce_faculty_on_leave(db, day=today)
    assert summary.enabled is False and summary.mailed == 0
    assert _keys(f"leave-approval:{leave_id}:") == set()
    assert _keys(f"leave-today:{leave_id}:") == set()
    assert list(mail_transport.outbox) == []


# ------------------------------------------------------------ on leave today --


@requires_db
def test_an_approval_ahead_of_time_announces_nothing_until_the_day(
    client, make_user, leave_ids, mail_on
):
    """The request that started this: approved on Monday for Friday, the
    colleagues hear on Friday — and on each day of the leave, never after it."""
    applicant = make_user("olt-fut", Role.MENTOR)
    office = make_user("olt-fut-office", Role.ADMIN)
    colleague = make_user("olt-fut-coll", Role.MENTOR)
    start = local_today() + timedelta(days=3)
    end = start + timedelta(days=1)
    leave_id = _submit(client, applicant, leave_ids, start=start, end=end, kind="LOP")

    _approve(client, office, leave_id)
    assert _keys(f"leave-today:{leave_id}:") == set(), "approval must not announce a future leave"
    assert _to(colleague.email) == []

    with SessionLocal() as db:
        # The morning job on an ordinary day before the leave: nothing.
        leave_mail.announce_faculty_on_leave(db, day=local_today())
        assert _keys(f"leave-today:{leave_id}:") == set()

        # The first day of the leave.
        summary = leave_mail.announce_faculty_on_leave(db, day=start)
        assert summary.leaves >= 1 and summary.failed == 0
    keys = _keys(f"leave-today:{leave_id}:")
    assert f"leave-today:{leave_id}:{start.isoformat()}:{colleague.user_id}" in keys
    assert f"leave-today:{leave_id}:{start.isoformat()}:{applicant.user_id}" not in keys
    assert f"leave-today:{leave_id}:{start.isoformat()}:{office.user_id}" not in keys, "faculty only"

    (mail,) = _to(colleague.email)
    assert mail.subject == "Voice Test olt-fut is on leave today"
    assert start.isoformat() in mail.text and end.isoformat() in mail.text
    assert REASON not in mail.text
    # The printed option stays between the applicant and the office.
    assert "loss-of-pay" not in mail.text.lower() and "loss of pay" not in mail.text.lower()
    assert all(m.subject != mail.subject for m in _to(applicant.email)), (
        "the person on leave is not told they are away"
    )

    with SessionLocal() as db:
        # A rerun the same morning sends nothing twice.
        leave_mail.announce_faculty_on_leave(db, day=start)
        assert len(_to(colleague.email)) == 1
        # The second day is its own mail.
        leave_mail.announce_faculty_on_leave(db, day=end)
        assert len(_to(colleague.email)) == 2
        # The day after the leave: nothing.
        leave_mail.announce_faculty_on_leave(db, day=end + timedelta(days=1))
        assert len(_to(colleague.email)) == 2


@requires_db
def test_an_approval_on_a_leave_day_announces_it_then_and_only_once(
    client, make_user, leave_ids, mail_on
):
    """The morning run has already passed by the time the office sanctions
    today's leave, so the approval is what reaches the colleagues — and the
    job, run again that day, does not send it a second time."""
    applicant = make_user("olt-now", Role.MENTOR)
    office = make_user("olt-now-office", Role.ADMIN)
    colleague = make_user("olt-now-coll", Role.MENTOR)
    today = local_today()
    leave_id = _submit(client, applicant, leave_ids, start=today, end=today)

    _approve(client, office, leave_id)
    assert f"leave-today:{leave_id}:{today.isoformat()}:{colleague.user_id}" in _keys(f"leave-today:{leave_id}:")
    (mail,) = _to(colleague.email)
    assert "today only" in mail.text and REASON not in mail.text

    with SessionLocal() as db:
        leave_mail.announce_faculty_on_leave(db, day=today)
    assert len(_to(colleague.email)) == 1


@requires_db
def test_only_an_approved_faculty_leave_is_ever_announced(client, make_user, leave_ids, mail_on):
    applicant = make_user("olt-rej", Role.MENTOR)
    student = make_user("olt-rej-stud", Role.STUDENT)
    office = make_user("olt-rej-office", Role.ADMIN)
    colleague = make_user("olt-rej-coll", Role.MENTOR)
    today = local_today()

    rejected = _submit(client, applicant, leave_ids, start=today, end=today)
    r = client.post(f"{LEAVES}/{rejected}/decision", headers=office.headers, json={"decision": "REJECT"})
    assert r.status_code == 200, r.text
    student_leave = _submit(client, student, leave_ids, start=today, end=today)
    _approve(client, office, student_leave)

    with SessionLocal() as db:
        leave_mail.announce_faculty_on_leave(db, day=today)
    assert _keys(f"leave-today:{rejected}:") == set(), "a rejected leave is not a leave"
    assert _keys(f"leave-today:{student_leave}:") == set(), "a student's absence is not broadcast"
    assert _to(colleague.email) == []


@requires_db
def test_a_removed_or_disabled_colleague_is_not_mailed(client, make_user, leave_ids, mail_on):
    applicant = make_user("olt-gone", Role.MENTOR)
    office = make_user("olt-gone-office", Role.ADMIN)
    removed = make_user("olt-gone-rem", Role.MENTOR)
    disabled = make_user("olt-gone-dis", Role.MENTOR)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.get(User, removed.user_id).deleted_at = now
        db.get(User, disabled.user_id).disabled_at = now
        db.commit()
    day = local_today() + timedelta(days=2)
    leave_id = _submit(client, applicant, leave_ids, start=day, end=day)
    _approve(client, office, leave_id)
    with SessionLocal() as db:
        leave_mail.announce_faculty_on_leave(db, day=day)
    assert _to(removed.email) == [] and _to(disabled.email) == []
    assert _keys(f"leave-today:{leave_id}:"), "somebody still on the roster was told"


@requires_db
def test_an_approval_that_is_undone_before_the_day_announces_nothing(
    client, make_user, leave_ids, mail_on
):
    """`announce_on_leave` checks the status and the day itself, so no caller
    can announce a leave that is not an approved one covering that day."""
    applicant = make_user("olt-guard", Role.MENTOR)
    make_user("olt-guard-coll", Role.MENTOR)
    day = local_today() + timedelta(days=2)
    leave_id = _submit(client, applicant, leave_ids, start=day, end=day)
    with SessionLocal() as db:
        lr = db.get(LeaveRequest, leave_id)
        assert leave_mail.announce_on_leave(db, lr, day) == [], "still SUBMITTED"
        lr.status = leave_mail.LeaveStatus.APPROVED
        db.commit()
        assert leave_mail.announce_on_leave(db, lr, day - timedelta(days=1)) == [], "not a leave day"
    assert _keys(f"leave-today:{leave_id}:") == set()


# --------------------------------------------------------------------- the job --


def test_the_job_announces_the_programmes_today_and_exits_zero(monkeypatch, caplog) -> None:
    """The seam is thin on purpose, and it must hand the module the PROGRAMME's
    day — the container's UTC date is yesterday for India's first five and a
    half hours."""
    seen = {}

    class FakeSession:
        def __enter__(self):
            return object()

        def __exit__(self, *exc):
            return False

    def fake_announce(db, *, day):
        seen["day"] = day
        return leave_mail.OnLeaveSummary(day=day, leaves=1, mailed=3)

    monkeypatch.setattr(leave_today_job, "SessionLocal", FakeSession)
    monkeypatch.setattr(leave_today_job.leave_mail, "announce_faculty_on_leave", fake_announce)
    monkeypatch.setattr(leave_today_job, "local_today", lambda: datetime(2026, 9, 25).date())
    with caplog.at_level("INFO", logger="reep.leave_today"):
        assert leave_today_job.main() == 0
    assert seen["day"].isoformat() == "2026-09-25"
    assert "1 faculty leave(s) today, 3 mail(s)" in caplog.text


def test_the_job_exits_nonzero_when_the_pass_raises(monkeypatch) -> None:
    class FakeSession:
        def __enter__(self):
            raise RuntimeError("database unreachable")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(leave_today_job, "SessionLocal", FakeSession)
    assert leave_today_job.main() == 1


def test_a_background_pass_that_fails_is_logged_and_never_raised(monkeypatch, caplog) -> None:
    """They run after the response has gone, so an exception reaches nobody who
    can act on it. Logged by type only: a database error's text carries its
    bound parameters, and here those are email addresses."""

    class Unreachable:
        def __enter__(self):
            raise RuntimeError("could not connect to server at voicetest-x@bgscet.ac.in")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(settings, "leave_mail_enabled", True)
    monkeypatch.setattr(leave_mail, "SessionLocal", Unreachable)
    with caplog.at_level("ERROR", logger="app.leave_mail"):
        leave_mail.notify_approvers_in_background("some-leave")
        leave_mail.announce_if_on_leave_today("some-leave")
    assert caplog.text.count("Leave mail pass failed") == 2
    assert "RuntimeError" in caplog.text and "@bgscet.ac.in" not in caplog.text
