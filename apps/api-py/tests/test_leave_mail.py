"""B10.5 — leave notifications, and the fact that they ship OFF.

Four things, and the first is the one that matters most.

1. OFF BY DEFAULT — the DEFAULT, which is not the same as the deployment. B3.7
   shipped on 2026-09-15 and production now sets `LEAVE_MAIL_ENABLED=true` from
   the task definition over a verified SES identity. What the default protects
   is a machine WITHOUT a transport: with `SES_FROM_ADDRESS` blank, which is
   every development box and every run of this suite, `mail_transport.send`
   logs the message into a bounded in-memory `outbox` that reaches NOBODY, so a
   notification switched on there writes a `mail_logs` row reading SENT for a
   message the applicant never received — and that row is the only thing anyone
   looks at afterwards. It is the shape of the failure that killed
   `PENDING_VERIFICATION`, and `test_it_is_off_by_default` is what keeps the
   default honest. No screen may say "the applicant has been emailed" while it
   is false.
2. THE `reason` NEVER TRAVELS. It is the form's "Purpose" cell — free text,
   routinely medical, the whole subject of rule 2's fence over this area. The
   tests below search the entire outbound message, subject and body, for the
   applicant's own sentence.
3. EXACTLY ONCE PER TRANSITION. These fire on state changes that can be retried;
   `mailer.deliver_once` is the arbiter and the key is `leave:{id}:{status}` —
   stable per transition, different per transition. Keyed on the request alone
   the sanction mail would be silent because the submission mail had gone.
4. NOT EVERY STATUS IS NOTIFIED. A CANCELLED request sends nothing: the
   applicant withdrew it themselves and does not need to be told what they just
   did.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app import leave_mail, mail_transport
from app.config import settings
from app.db import SessionLocal
from app.leave_mail import MAIL_KIND, dedupe_key, notify_transition
from app.models.leave import LeaveRequest, LeaveStatus
from app.models.mail import MailLog
from app.models.user import Role

LEAVES = "/api/leaves"
REASON = "Post-operative review at Manipal, second cycle."


@pytest.fixture
def request_row(client, make_user):
    """One submitted request, through the real endpoint, and its MailLog rows
    removed afterwards — a `mail_logs` row outlives the account that named it."""
    applicant = make_user("mail-app", Role.MENTOR)
    r = client.post(
        LEAVES,
        headers=applicant.headers,
        json={
            "from_date": date.today().isoformat(),
            "to_date": (date.today() + timedelta(days=1)).isoformat(),
            "reason": REASON,
            "leave_kind": "CASUAL",
        },
    )
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]
    mail_transport.outbox.clear()
    yield leave_id
    with SessionLocal() as db:
        # `leave%:` and not `leave:`: the approvers' and the colleagues'
        # mails (test_leave_on_leave_today.py) are keyed on the request too.
        db.execute(delete(MailLog).where(MailLog.dedupe_key.like(f"leave%:{leave_id}:%")))
        db.commit()


def _load(db, leave_id: str) -> LeaveRequest:
    return db.get(LeaveRequest, leave_id)


@requires_db
def test_it_is_off_by_default_and_writes_nothing(request_row):
    """The state of every deployment until an operator turns it on WITH a
    transport behind it."""
    assert settings.leave_mail_enabled is False, (
        "the DEFAULT must stay off: this suite runs with no transport, so mail "
        "turned on here lands in an in-memory outbox and the MailLog row says "
        "SENT about a message nobody received. Production turns it on through "
        "the task definition, over a transport that exists."
    )
    with SessionLocal() as db:
        assert notify_transition(db, _load(db, request_row)) is None
    assert list(mail_transport.outbox) == []
    with SessionLocal() as db:
        assert db.scalars(
            select(MailLog).where(MailLog.dedupe_key.like(f"leave:{request_row}:%"))
        ).all() == []


@requires_db
def test_when_it_is_on_it_sends_once_per_transition_and_carries_no_reason(
    request_row, monkeypatch
):
    monkeypatch.setattr(settings, "leave_mail_enabled", True)
    with SessionLocal() as db:
        lr = _load(db, request_row)
        first = notify_transition(db, lr)
        assert first is not None
        assert first.kind == MAIL_KIND
        assert first.dedupe_key == f"leave:{request_row}:SUBMITTED" == dedupe_key(lr)
        assert first.status.value == "SENT"

        # Retried — the same transition, the same key, no second copy.
        second = notify_transition(db, _load(db, request_row))
        assert second.id == first.id

    assert len(mail_transport.outbox) == 1
    sent = mail_transport.outbox[0]
    assert REASON not in sent.text and REASON not in sent.subject
    assert "casual leave" in sent.text, "the printed option says nothing about the person"
    assert date.today().isoformat() in sent.text

    # A DIFFERENT transition is a different key and does go out.
    with SessionLocal() as db:
        lr = _load(db, request_row)
        lr.status = LeaveStatus.APPROVED
        db.commit()
        third = notify_transition(db, _load(db, request_row))
        assert third.dedupe_key == f"leave:{request_row}:APPROVED"
    assert len(mail_transport.outbox) == 2
    assert "sanctioned" in mail_transport.outbox[1].subject
    assert REASON not in mail_transport.outbox[1].text


@requires_db
def test_a_withdrawal_notifies_nobody(request_row, monkeypatch):
    monkeypatch.setattr(settings, "leave_mail_enabled", True)
    with SessionLocal() as db:
        lr = _load(db, request_row)
        lr.status = LeaveStatus.CANCELLED
        db.commit()
        assert notify_transition(db, _load(db, request_row)) is None
    assert list(mail_transport.outbox) == []


def test_the_logger_is_muted_from_sentry() -> None:
    """A module whose whole job is composing text about a leave request is the
    one that grows a debug line under pressure, and `app.mail_transport` — one
    frame down — already logs the complete body at INFO."""
    from app.telemetry_scrub import MUTED_LOGGERS, scrub_breadcrumb

    assert leave_mail.log.name == "app.leave_mail"
    assert leave_mail.log.name in MUTED_LOGGERS
    assert scrub_breadcrumb({"category": "app.leave_mail", "message": REASON}) is None


def test_the_notified_statuses_are_the_submission_and_the_decision() -> None:
    """SUBMITTED and a decision. FIRST_APPROVED went with the second signature
    (2026-09-16) — nothing writes it any more, so nothing should promise a
    mail for it. Anything else sends nothing at all, which is what makes "not
    notified" a decision rather than an omission."""
    assert set(leave_mail._BODIES) == {
        LeaveStatus.SUBMITTED,
        LeaveStatus.APPROVED,
        LeaveStatus.REJECTED,
    }
    assert LeaveStatus.CANCELLED not in leave_mail._BODIES
    assert LeaveStatus.FIRST_APPROVED not in leave_mail._BODIES


# ---------------------------------------------------------------------------
# THE WIRING (stage 4). The module above is correct and was, until this, called
# by nothing: `notify_transition`'s three call sites are all inside
# `app/routers/leave.py`, which stage 3 was told to leave alone. These two tests
# are behavioural on purpose — they drive the real endpoints rather than
# asserting that a line exists — because "is B10.5 reachable" is a question
# about the call graph and an import is not an answer to it.
#
# NEITHER ENDPOINT CHANGED SHAPE. `notify_transition` returns None when the flag
# is off (every deployment), never raises, and is called AFTER the commit, so
# the 201 and the 200 are byte for byte what they were. The default-off case is
# covered by `test_it_is_off_by_default_and_writes_nothing` above; these two
# turn it on to prove the wire is there at all.
# ---------------------------------------------------------------------------


@requires_db
def test_the_submit_endpoint_sends_the_submission_mail(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "leave_mail_enabled", True)
    mail_transport.outbox.clear()
    applicant = make_user("mail-wire-submit", Role.MENTOR)
    r = client.post(
        LEAVES,
        headers=applicant.headers,
        json={
            "from_date": date.today().isoformat(),
            "to_date": date.today().isoformat(),
            "reason": REASON,
            "leave_kind": "CASUAL",
        },
    )
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]
    try:
        # The applicant's own mail. The approvers are told too (2026-09-24,
        # test_leave_on_leave_today.py), so the outbox holds more than this one.
        mine = [m for m in mail_transport.outbox if m.to == applicant.email]
        assert len(mine) == 1, "submitting must reach the applicant"
        for sent in mail_transport.outbox:
            assert REASON not in sent.text and REASON not in sent.subject
        with SessionLocal() as db:
            row = db.scalar(
                select(MailLog).where(MailLog.dedupe_key == f"leave:{leave_id}:SUBMITTED")
            )
            assert row is not None and row.kind == MAIL_KIND
    finally:
        with SessionLocal() as db:
            db.execute(delete(MailLog).where(MailLog.dedupe_key.like(f"leave%:{leave_id}:%")))
            db.commit()


@requires_db
def test_the_one_signature_sends_the_sanction_mail_and_it_carries_no_reason(
    client, make_user, monkeypatch
):
    """The office's one decision is the sanction, so it is one message under
    one dedupe key — and neither the subject nor the body carries the reason."""
    monkeypatch.setattr(settings, "leave_mail_enabled", True)
    applicant = make_user("mail-wire-app", Role.MENTOR)
    office = make_user("mail-wire-one", Role.ADMIN)
    r = client.post(
        LEAVES,
        headers=applicant.headers,
        json={
            "from_date": date.today().isoformat(),
            "to_date": date.today().isoformat(),
            "reason": REASON,
            "leave_kind": "CASUAL",
        },
    )
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]
    try:
        mail_transport.outbox.clear()
        one = client.post(
            f"{LEAVES}/{leave_id}/decision",
            headers=office.headers,
            json={"decision": "APPROVE", "note": None},
        )
        assert one.status_code == 200, one.text
        assert one.json()["status"] == "APPROVED"
        # The applicant's own mail. A leave that covers today is also
        # announced to the other faculty at approval (2026-09-24), so the
        # outbox may hold those too; the reason check below reads them all.
        mine = [m for m in mail_transport.outbox if m.to == applicant.email]
        assert len(mine) == 1
        assert "sanctioned" in mine[0].subject.lower()

        for sent in mail_transport.outbox:
            assert REASON not in sent.text and REASON not in sent.subject
        with SessionLocal() as db:
            keys = set(
                db.scalars(
                    select(MailLog.dedupe_key).where(
                        MailLog.dedupe_key.like(f"leave:{leave_id}:%")
                    )
                ).all()
            )
        # SUBMITTED is in there too: the submission mail went out before the
        # outbox was cleared, and its `mail_logs` row is exactly the point —
        # two transitions, two keys, one message each.
        assert keys == {
            f"leave:{leave_id}:SUBMITTED",
            f"leave:{leave_id}:APPROVED",
        }
    finally:
        with SessionLocal() as db:
            db.execute(delete(MailLog).where(MailLog.dedupe_key.like(f"leave%:{leave_id}:%")))
            db.commit()


def test_the_applicants_mail_names_nobody() -> None:
    """The three mails to the applicant say what happened to the request and
    nothing about WHO (2026-09-25): not the placement office, not an approver,
    not a department, and not the applicant's own name either. A rejection
    that read "not approved by the placement office" was the report that
    started this."""
    from app.models.user import User

    applicant = User(name="Asha Rao", email="asha@bgscet.ac.in", role=Role.MENTOR)
    day = date(2026, 9, 25)
    for status, builder in leave_mail._BODIES.items():
        lr = LeaveRequest(
            id="lr-1", from_date=day, to_date=day, leave_kind="CASUAL", status=status, reason=REASON
        )
        subject, text = builder(applicant, lr)
        whole = f"{subject}\n{text}".lower()
        for word in ("placement", "office", "approver", "department", "mentor", "asha", "rao"):
            assert word not in whole, f"{status.value} mail names {word!r}: {subject!r} / {text!r}"
        assert REASON.lower() not in whole
        assert text.startswith("Hello,\n"), "the greeting names nobody either"
        assert day.isoformat() in text, "the dates still say which request it was"

    lr = LeaveRequest(id="lr-2", from_date=day, to_date=day, leave_kind="CASUAL", status=LeaveStatus.REJECTED, reason=REASON)
    subject, text = leave_mail._BODIES[LeaveStatus.REJECTED](applicant, lr)
    assert subject == "Your leave request was not approved"
    assert "was not approved" in text
