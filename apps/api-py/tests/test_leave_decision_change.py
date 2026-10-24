"""A leave decision can be changed after it was made (2026-10-13).

`POST /api/leaves/{id}/change-decision` with `action`:

  * REVOKE  — APPROVED -> REVOKED. The days are free again, the "on leave
    today" broadcast stops (it reads APPROVED rows only), and the applicant
    reads the reason on their own screen.
  * REOPEN  — REJECTED or REVOKED -> SUBMITTED, back in the queue with its
    signatures cleared, for a fresh decision through the ordinary `/decision`.

Pinned here: who may do it (exactly the people who may decide, never on their
own request), that a reason in words is compulsory, which states each action
starts from, the stricter rule once a leave has begun (Main Admin only, and
only with `confirm_started`), the overlap refusal on reopen, the audit
before-image, and the mail — which must reach the applicant and the colleague
covering, carry neither the purpose nor the reason, and not be swallowed by
the dedupe key when a request is sanctioned a second time.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from conftest import TEST_PASSWORD, requires_db

from app import mail_transport
from app.clock import local_today
from app.config import settings
from app.db import SessionLocal
from app.leave_mail import CHANGE_MAIL_KIND, dedupe_key
from app.models.governance import CapabilityGrant, SubjectKind
from app.models.leave import LeaveRequest, LeaveStatus
from app.models.mail import MailLog
from app.models.redesign import AuditEvent
from app.models.user import Role

LEAVES = "/api/leaves"
PURPOSE = "Follow-up scan at the cardiology clinic."
CHANGE_REASON = "Exam duty was reassigned to this week; the cover cannot be arranged."


# ----------------------------------------------------------------- fixtures --


@pytest.fixture
def leaves():
    """Every request a test makes, with its audit rows and mail rows, removed
    afterwards — none of the three is removed by deleting the account."""
    made: list[str] = []
    yield made
    with SessionLocal() as db:
        for leave_id in made:
            db.execute(delete(MailLog).where(MailLog.dedupe_key.like(f"leave%:{leave_id}:%")))
            db.execute(delete(AuditEvent).where(AuditEvent.entity_id == leave_id))
        db.execute(delete(LeaveRequest).where(LeaveRequest.id.in_(made or [""])))
        db.commit()


@pytest.fixture
def delegate_grant():
    """`admin.leave_approvals` for a faculty account, written as a row (see
    `test_leave_chain.scoped_grant` for why not through the endpoint)."""
    made: list[str] = []

    def _grant(user_id: str) -> None:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability="admin.leave_approvals", subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                reason="the decision-change tests need a delegate approver",
            )
            db.add(row)
            db.commit()
            made.append(row.id)

    yield _grant
    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made or [""])))
        db.commit()


def _submit(client, headers, leaves: list[str], *, start_in: int, days: int = 2) -> str:
    start = local_today() + timedelta(days=start_in)
    r = client.post(
        LEAVES,
        headers=headers,
        json={
            "from_date": start.isoformat(),
            "to_date": (start + timedelta(days=days - 1)).isoformat(),
            "reason": PURPOSE,
            "leave_kind": "CASUAL",
        },
    )
    assert r.status_code == 201, r.text
    leaves.append(r.json()["id"])
    return r.json()["id"]


def _decide(client, headers, leave_id: str, decision: str, note: str | None = None) -> dict:
    r = client.post(
        f"{LEAVES}/{leave_id}/decision", headers=headers, json={"decision": decision, "note": note}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _change(client, headers, leave_id: str, action: str, reason: str = CHANGE_REASON, **extra):
    return client.post(
        f"{LEAVES}/{leave_id}/change-decision",
        headers=headers,
        json={"action": action, "reason": reason, **extra},
    )


def _row(leave_id: str) -> LeaveRequest:
    with SessionLocal() as db:
        row = db.get(LeaveRequest, leave_id)
        db.expunge(row)
        return row


# -------------------------------------------------------------------- tests --


@requires_db
def test_the_office_revokes_a_sanction_and_the_applicant_reads_why(client, make_user, leaves):
    applicant = make_user("ldc-app", Role.MENTOR)
    office = make_user("ldc-adm", Role.ADMIN)
    leave_id = _submit(client, applicant.headers, leaves, start_in=10)
    _decide(client, office.headers, leave_id, "APPROVE", "Sanctioned.")

    # A reason in words is compulsory, and a blank one is not words.
    assert _change(client, office.headers, leave_id, "REVOKE", reason="").status_code == 422
    assert _change(client, office.headers, leave_id, "REVOKE", reason="   ").status_code == 422
    assert _change(client, office.headers, leave_id, "UNDO").status_code == 422
    # Reopening a sanction is not a thing: revoke it, or leave it.
    assert _change(client, office.headers, leave_id, "REOPEN").status_code == 409

    r = _change(client, office.headers, leave_id, "REVOKE")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "REVOKED"
    assert body["decision_change_kind"] == "REVOKED"
    assert body["decision_change_reason"] == CHANGE_REASON
    assert body["decision_changed_by_name"] and body["decision_changed_at"]
    # The sanction that was withdrawn is still on the record.
    assert body["director_name"] and body["director_decided_at"]

    # Revoked twice is not a thing either.
    assert _change(client, office.headers, leave_id, "REVOKE").status_code == 409

    # The applicant sees the state and the reason on their own list.
    mine = {row["id"]: row for row in client.get(f"{LEAVES}/mine", headers=applicant.headers).json()}
    assert mine[leave_id]["status"] == "REVOKED"
    assert mine[leave_id]["decision_change_reason"] == CHANGE_REASON

    # Reachable only by asking, like CANCELLED; the default settled queue is
    # the pair it always was.
    default = {row["id"] for row in client.get(f"{LEAVES}/history", headers=office.headers).json()}
    revoked = {
        row["id"]
        for row in client.get(f"{LEAVES}/history?status=REVOKED", headers=office.headers).json()
    }
    assert leave_id not in default and leave_id in revoked

    # The decision it undid is on the audit trail, without the purpose.
    with SessionLocal() as db:
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "leave_request", AuditEvent.entity_id == leave_id
            )
        ).all()
    assert [e.action for e in events] == ["DECISION_REVOKED"]
    assert events[0].before_json["status"] == "APPROVED"
    assert events[0].before_json["first_note"] == "Sanctioned."
    assert events[0].after_json["status"] == "REVOKED"
    assert events[0].after_json["change_reason"] == CHANGE_REASON
    assert PURPOSE not in str(events[0].before_json) + str(events[0].after_json)

    # The paper says so rather than printing a sanction.
    paper = client.get(f"{LEAVES}/{leave_id}/paper.pdf", headers=applicant.headers)
    assert paper.status_code == 200


@requires_db
def test_a_revoked_or_refused_request_reopens_for_a_fresh_decision(client, make_user, leaves):
    applicant = make_user("ldc-rapp", Role.MENTOR)
    office = make_user("ldc-radm", Role.ADMIN)
    refused_id = _submit(client, applicant.headers, leaves, start_in=12)
    revoked_id = _submit(client, applicant.headers, leaves, start_in=20)
    _decide(client, office.headers, refused_id, "REJECT", "Clashes with the audit visit.")
    _decide(client, office.headers, revoked_id, "APPROVE")
    assert _change(client, office.headers, revoked_id, "REVOKE").status_code == 200

    for leave_id in (refused_id, revoked_id):
        r = _change(client, office.headers, leave_id, "REOPEN", reason="The visit moved; looking again.")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUBMITTED"
        assert r.json()["decision_change_kind"] == "REOPENED"
        # A pending request carries no signature: the old one is cleared from
        # the row (it lives on in the audit before-image).
        assert r.json()["director_name"] is None and r.json()["first_signed_as"] is None
        row = _row(leave_id)
        assert row.first_approver_user_id is None and row.first_note is None

    pending = {row["id"] for row in client.get(f"{LEAVES}/pending", headers=office.headers).json()}
    assert {refused_id, revoked_id} <= pending

    # And the ordinary decision path decides it again.
    again = _decide(client, office.headers, revoked_id, "APPROVE", "Sanctioned after all.")
    assert again["status"] == "APPROVED" and again["director_note"] == "Sanctioned after all."
    assert _row(revoked_id).decision_revision == 2

    with SessionLocal() as db:
        actions = [
            e.action
            for e in db.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_id == revoked_id)
                .order_by(AuditEvent.occurred_at)
            ).all()
        ]
    assert actions == ["DECISION_REVOKED", "DECISION_REOPENED"]


@requires_db
def test_a_leave_that_has_begun_is_the_main_admin_s_to_revoke_and_only_on_purpose(
    client, make_user, login, leaves, delegate_grant
):
    applicant = make_user("ldc-sapp", Role.MENTOR)
    office = make_user("ldc-sadm", Role.ADMIN)
    delegate = make_user("ldc-sdel", Role.MENTOR)
    delegate_grant(delegate.user_id)
    delegate_h = login(delegate.email, TEST_PASSWORD)
    leave_id = _submit(client, applicant.headers, leaves, start_in=-1, days=3)
    _decide(client, office.headers, leave_id, "APPROVE")

    # The delegate may decide, but not revoke a leave that is under way.
    by_delegate = _change(client, delegate_h, leave_id, "REVOKE", confirm_started=True)
    assert by_delegate.status_code == 403 and "Main Admin" in by_delegate.text

    # The office is asked to confirm, in a sentence naming the days.
    unconfirmed = _change(client, office.headers, leave_id, "REVOKE")
    assert unconfirmed.status_code == 409 and "already begun" in unconfirmed.text
    assert _row(leave_id).status is LeaveStatus.APPROVED

    confirmed = _change(client, office.headers, leave_id, "REVOKE", confirm_started=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "REVOKED"


@requires_db
def test_a_delegate_revokes_a_leave_that_has_not_begun(
    client, make_user, login, leaves, delegate_grant
):
    applicant = make_user("ldc-fapp", Role.MENTOR)
    office = make_user("ldc-fadm", Role.ADMIN)
    delegate = make_user("ldc-fdel", Role.MENTOR)
    delegate_grant(delegate.user_id)
    delegate_h = login(delegate.email, TEST_PASSWORD)
    leave_id = _submit(client, applicant.headers, leaves, start_in=5)
    _decide(client, office.headers, leave_id, "APPROVE")
    r = _change(client, delegate_h, leave_id, "REVOKE")
    assert r.status_code == 200, r.text


@requires_db
def test_nobody_else_changes_a_decision_and_nobody_changes_their_own(
    client, make_user, login, leaves, delegate_grant
):
    applicant = make_user("ldc-napp", Role.MENTOR)
    office = make_user("ldc-nadm", Role.ADMIN)
    colleague = make_user("ldc-ncol", Role.MENTOR)
    delegate = make_user("ldc-ndel", Role.MENTOR)
    delegate_grant(delegate.user_id)
    delegate_h = login(delegate.email, TEST_PASSWORD)
    leave_id = _submit(client, applicant.headers, leaves, start_in=8)
    own_id = _submit(client, delegate_h, leaves, start_in=30)
    _decide(client, office.headers, leave_id, "APPROVE")
    _decide(client, office.headers, own_id, "APPROVE")

    # A colleague without the grant: the role gate's answer, the same for an
    # invented id, so the endpoint names no request.
    refused = _change(client, colleague.headers, leave_id, "REVOKE")
    invented = _change(client, colleague.headers, "no-such-leave", "REVOKE")
    assert refused.status_code == 403
    assert (invented.status_code, invented.json()) == (refused.status_code, refused.json())
    # The applicant cannot revoke or reopen their own sanction.
    assert _change(client, applicant.headers, leave_id, "REVOKE").status_code == 403
    # The delegate holds the key and still cannot touch their own request.
    own = _change(client, delegate_h, own_id, "REVOKE")
    assert own.status_code == 400 and "own leave" in own.text
    assert _row(leave_id).status is LeaveStatus.APPROVED


@requires_db
def test_reopening_is_refused_while_another_live_request_holds_the_days(client, make_user, leaves):
    applicant = make_user("ldc-oapp", Role.MENTOR)
    office = make_user("ldc-oadm", Role.ADMIN)
    first = _submit(client, applicant.headers, leaves, start_in=14)
    _decide(client, office.headers, first, "REJECT", "Not this week.")
    # The applicant applied again for the same days, which is allowed: a
    # refusal frees them.
    second = _submit(client, applicant.headers, leaves, start_in=14)
    r = _change(client, office.headers, first, "REOPEN")
    assert r.status_code == 409 and "overlaps" in r.text
    assert _row(first).status is LeaveStatus.REJECTED
    assert _row(second).status is LeaveStatus.SUBMITTED


@requires_db
def test_the_mail_reaches_the_applicant_and_the_cover_and_carries_no_reason(
    client, make_user, leaves, monkeypatch
):
    monkeypatch.setattr(settings, "leave_mail_enabled", True)
    applicant = make_user("ldc-mapp", Role.MENTOR)
    office = make_user("ldc-madm", Role.ADMIN)
    cover = make_user("ldc-mcov", Role.MENTOR)
    leave_id = _submit(client, applicant.headers, leaves, start_in=9)
    with SessionLocal() as db:
        row = db.get(LeaveRequest, leave_id)
        row.alt_rows = [
            {"date": "", "staff_name": "Cover", "cls": "MBA I", "time": "10:00",
             "remarks": "", "user_id": cover.user_id},
            # A bare typed name has no account behind it and reaches nobody.
            {"date": "", "staff_name": "Typed only", "cls": "", "time": "", "remarks": ""},
        ]
        db.commit()
    _decide(client, office.headers, leave_id, "APPROVE")
    mail_transport.outbox.clear()

    assert _change(client, office.headers, leave_id, "REVOKE").status_code == 200
    sent = list(mail_transport.outbox)
    recipients = sorted(m.to for m in sent)
    assert recipients == sorted([applicant.email, cover.email])
    for message in sent:
        assert PURPOSE not in message.text and PURPOSE not in message.subject
        assert CHANGE_REASON not in message.text, "the reason is read on the screen"
        assert "casual" not in message.text.lower() or message.to == applicant.email
    with SessionLocal() as db:
        kinds = {
            m.kind
            for m in db.scalars(
                select(MailLog).where(MailLog.dedupe_key.like(f"leave-change:{leave_id}:%"))
            ).all()
        }
    assert kinds == {CHANGE_MAIL_KIND}

    # Reopened and sanctioned again: the second sanction is mailed too, under
    # its own key, rather than found as the first one's row and dropped.
    mail_transport.outbox.clear()
    assert _change(client, office.headers, leave_id, "REOPEN").status_code == 200
    _decide(client, office.headers, leave_id, "APPROVE")
    subjects = [m.subject for m in mail_transport.outbox if m.to == applicant.email]
    assert "Your leave request has been reopened" in subjects
    # Whoever can decide it is told it is waiting again, under the revision's
    # own key: the submission already wrote `leave-approval:{id}:{approver}`.
    approver_mail = [m for m in mail_transport.outbox if m.to == office.email]
    assert any("has been reopened" in m.text for m in approver_mail)
    assert all(PURPOSE not in m.text and CHANGE_REASON not in m.text for m in approver_mail)
    with SessionLocal() as db:
        reopened_keys = db.scalars(
            select(MailLog.dedupe_key).where(
                MailLog.dedupe_key.like(f"leave-approval:{leave_id}:%:r2")
            )
        ).all()
    assert reopened_keys, "the approvers' reopen mail is keyed by revision"
    assert "Your leave request has been sanctioned" in subjects
    with SessionLocal() as db:
        assert dedupe_key(db.get(LeaveRequest, leave_id)) == f"leave:{leave_id}:APPROVED:r2"
