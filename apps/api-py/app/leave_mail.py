"""Leave notifications (B10.5) — and the DEFAULT is off, not the deployment.

`notify_transition(db, lr)` mails the APPLICANT that their request has moved.
Three transitions are worth a message and they are the three 04 names: it was
submitted, somebody gave the first signature, somebody decided it. The mails to
the APPROVERS and to the applicant's COLLEAGUES are at the foot of this module
and have their own section below.

==============================================================================
OFF BY DEFAULT, AND THE DEFAULT IS ABOUT THE TRANSPORT, NOT ABOUT THE FEATURE
==============================================================================

`settings.leave_mail_enabled` defaults to false. It used to say here that it
must stay false on EVERY deployment until B3.7 (SES in production) shipped.
B3.7 shipped on 2026-09-15 — the api task carries a real `SES_FROM_ADDRESS`,
the identity is verified and the account holds production access — so
production sets `LEAVE_MAIL_ENABLED=true` from the task definition (infra/cdk's
`leaveMailEnabled`, which the CDK app refuses to synthesise without a sender,
so the switch cannot get ahead of the transport).

The default stays false because of what it protects, which was never
production: a machine with `SES_FROM_ADDRESS` blank — every development box,
every CI run — where `mail_transport.send` logs the message and appends it to a
bounded in-memory `outbox` that reaches NOBODY. A notification switched on over
THAT transport writes a `mail_logs` row reading SENT for a message the
applicant never received, and that row is the only thing anybody looks at
afterwards. It is the exact shape of the failure that killed
`PENDING_VERIFICATION`: a step nobody could pass, reported as a step that
worked.

So while the flag is false NO SCREEN MAY SAY "the applicant has been emailed" —
04 §B3.7 names that trap for the Add-faculty wizard and it is the same trap
here. `notify_transition` returns None when it is off, so a caller that wants to
tell the user something has an honest value to branch on.

==============================================================================
`deliver_once`, NEVER `mail_transport.send` DIRECTLY
==============================================================================

These messages fire on STATE TRANSITIONS, and a state transition can be retried:
a request that 500s after the commit, a console that double-taps Sanction, a
background retry somebody adds later. `app/mailer.py::deliver_once` is the
send-exactly-once wrapper over `mail_logs`, whose unique index on `dedupe_key`
is the arbiter — a losing racer catches the IntegrityError and reports the
existing row rather than sending a second copy.

The key is `leave:{id}:{status}`, which is stable per transition and different
per transition. Keying on the request alone would make the sanction mail silent
because the submission mail had already gone; keying on a timestamp would send
one copy per retry, which is the thing being prevented.

==============================================================================
THE `reason` NEVER TRAVELS. NOT ONCE, NOT SUMMARISED.
==============================================================================

`leave_requests.reason` is the form's "Purpose" cell: free text, routinely
medical ("post-operative review", "chemotherapy — day 2"), and the whole subject
of rule 2's fence over this area. An email leaves the building through a third
party's infrastructure and lands in a mailbox this product does not control, so
a `reason` in a mail body is a medical disclosure REEP made on the applicant's
behalf without asking. The dates, the printed option and the state are enough to
say what happened; `_BODIES` below carries no other field and a reviewer should
refuse any patch that adds one.

For the same reason this module's LOGGER is muted from Sentry
(`app/telemetry_scrub.py::MUTED_LOGGERS`) alongside `app.mail_transport`. It
does not log a body today — but `mail_transport` does, one frame down, and a
module whose whole job is composing text about a leave request is exactly the
one that grows a debug line under pressure. The cost is honest: a failure here
is visible in the `mail_logs` row and the CloudWatch line, not as a Sentry
breadcrumb.

RULE 1 is not in play (nothing here reaches a model). Rule 2 is why the two
mails below that go to somebody OTHER than the applicant carry less than the
applicant's own mail does.

==============================================================================
TWO MORE AUDIENCES (2026-09-24): THE APPROVERS, AND THE FACULTY ON THE DAY
==============================================================================

A FACULTY MEMBER'S APPLICATION IS MAILED TO EVERYBODY WHO CAN DECIDE IT —
the Main Admin, and every faculty account holding a live grant of
`admin.leave_approvals` (`leave_approvers`). Until this the queue was found by
opening the screen, and a delegate the office had handed the queue to had no
way to learn that anything was waiting. The mail names the applicant and the
dates and sends them to the screen; the `reason` stays out, for the reason
above, and so does the printed option, which the approver reads on the screen
anyway.

AN APPROVED FACULTY LEAVE IS ANNOUNCED TO EVERY OTHER FACULTY MEMBER ON EACH
DAY OF THE LEAVE, AND NEVER ON THE DAY IT IS APPROVED — unless that is one of
the leave days. The owner's words: the mail says "this person is on leave
today", so it goes on the leave day, not when the office presses Sanction.
A leave approved on Monday for Friday sends nothing on Monday; Friday
morning's `python -m app.leave_today_job` sends it. A leave approved on its own
first day (or in the middle of it) is announced at approval, because the
morning run has already passed and that day is a leave day. The key is
`leave-today:{id}:{day}:{recipient}`, so the morning run and the approval
cannot both send the same day's mail, and a rerun of the job sends nothing
twice.

THE BROADCAST CARRIES THE NAME AND THE DATES AND NOTHING ELSE — not the
printed option either. The applicant's own mail says "casual leave"; a mail to
forty colleagues must not say "loss-of-pay leave" (pay) or "restricted
holiday" (which is how the form spells a religious observance). Colleagues
need to know WHO is away and UNTIL WHEN; why, and on what terms, is between the
applicant and the office.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .clock import local_today
from .config import settings
from .db import SessionLocal
from .governance import has_capability
from .mail_transport import send as transport_send
from .mailer import deliver_once
from .models.governance import AccessGroupMember, CapabilityGrant, SubjectKind
from .models.leave import LeaveRequest, LeaveStatus
from .models.mail import MailLog, MailStatus
from .models.user import Role, User

log = logging.getLogger(__name__)

#: `kind` on the MailLog row, so the office can count leave mail apart from
#: activation and reset mail in one query.
MAIL_KIND = "leave-status"


def _span(lr: LeaveRequest) -> str:
    if lr.from_date == lr.to_date:
        return lr.from_date.isoformat()
    return f"{lr.from_date.isoformat()} to {lr.to_date.isoformat()}"


def _kind_phrase(lr: LeaveRequest) -> str:
    """"Casual leave", or just "leave" when no printed option was named. The
    option is one of five words on the college's own form and says nothing about
    the person; the `reason` says everything and never appears."""
    if not lr.leave_kind:
        return "leave"
    return {
        "CASUAL": "casual leave",
        "PERMISSION": "permission",
        "OOD": "on-official-duty leave",
        "RH": "restricted holiday",
        "LOP": "loss-of-pay leave",
    }.get(lr.leave_kind, "leave")


# THE THREE MAILS TO THE APPLICANT NAME NOBODY (2026-09-25). Not the office,
# not an approver, not a department, and not the applicant either — the
# greeting is a bare "Hello,". The owner's words, after a rejection mail that
# read as though a named office had turned the person down: say what happened
# to the request and nothing about who. The screen is where the decision, its
# date and any note are read in context. `test_leave_mail.py::
# test_the_applicants_mail_names_nobody` holds every one of them to it.
_GREETING = "Hello,\n\n"


def _submitted(user: User, lr: LeaveRequest) -> tuple[str, str]:
    return (
        "Your leave request has been received",
        f"{_GREETING}"
        f"Your leave request for {_kind_phrase(lr)} covering {_span(lr)} has been "
        f"received and is waiting for a decision.\n\n"
        f"You can follow it, or withdraw it while it is still undecided, on your "
        f"Leave Requests screen.\n",
    )


def _approved(user: User, lr: LeaveRequest) -> tuple[str, str]:
    return (
        "Your leave request has been sanctioned",
        f"{_GREETING}"
        f"Your leave request for {_kind_phrase(lr)} covering {_span(lr)} has been "
        f"sanctioned. The signed form is on your Leave Requests screen.\n",
    )


def _rejected(user: User, lr: LeaveRequest) -> tuple[str, str]:
    # The decider's note stays out too. `first_note`/`second_note` are free
    # text written by a member of staff ABOUT this person, for the office's
    # file; the screen shows it to them in context, and the mail only says
    # where to look.
    return (
        "Your leave request was not approved",
        f"{_GREETING}"
        f"Your leave request for {_kind_phrase(lr)} covering {_span(lr)} was not "
        f"approved. You can see it on your Leave Requests screen.\n",
    )


#: Which transitions are worth a message. A status that is not here sends
#: nothing at all — CANCELLED especially: the applicant withdrew it themselves
#: and does not need to be told what they just did.
_BODIES: dict[LeaveStatus, Callable[[User, LeaveRequest], tuple[str, str]]] = {
    LeaveStatus.SUBMITTED: _submitted,
    # FIRST_APPROVED is no longer a transition anything writes (one signature
    # since 2026-09-16), so there is no message for it.
    LeaveStatus.APPROVED: _approved,
    LeaveStatus.REJECTED: _rejected,
}


def dedupe_key(lr: LeaveRequest) -> str:
    """`leave:{id}:{status}` — stable per transition, different per transition.
    Exported so a test can assert the key without composing a message.

    A row whose decision has been CHANGED (`decision_revision` > 0) carries the
    revision as a suffix, `leave:{id}:{status}:r{n}`: a request sanctioned,
    revoked, reopened and sanctioned again must be told about the second
    sanction, and the bare key would find the first one's row and send nothing.
    Revision 0 keeps the bare key, so every row written before 2026-10-13 keys
    exactly as it did."""
    base = f"leave:{lr.id}:{lr.status.value}"
    revision = lr.decision_revision or 0
    return base if revision == 0 else f"{base}:r{revision}"


def notify_transition(db: Session, lr: LeaveRequest) -> MailLog | None:
    """Tell the applicant their request has moved. Returns the `mail_logs` row,
    or None when nothing was sent.

    None means one of three things and the caller must not read it as failure:
    the feature is off (the default), this status is not one that is notified,
    or the applicant's account is gone. It NEVER raises — `deliver_once` records
    a FAILED row rather than propagating a transport error, because a mail that
    did not go out must never be the reason a sanctioned leave fails to save.

    IT DOES NOT COMMIT THE LEAVE ROW. `deliver_once` commits its own MailLog, so
    call this AFTER the transition's own commit: called before, it would commit
    a half-written request, and a mail about a decision that has not landed yet
    is worse than a late one.
    """
    if not settings.leave_mail_enabled:
        return None
    builder = _BODIES.get(lr.status)
    if builder is None:
        return None
    user = db.get(User, lr.requester_user_id)
    if user is None or not (user.email or "").strip():
        # The account was removed (or has no address) between the transition and
        # this call. Nothing to do, and nothing is wrong with the leave request.
        log.info("Leave notification skipped: no recipient for request %s", lr.id)
        return None
    subject, text = builder(user, lr)
    return deliver_once(
        db,
        kind=MAIL_KIND,
        recipient=user.email,
        dedupe_key=dedupe_key(lr),
        subject=subject,
        # The transport composes nothing; it is handed the finished body, the
        # way account_links.py's `_driver` does it.
        send=lambda to, subj: transport_send(to, subj or "", text),
    )


# ---------------------------------------------------------------------------
# The approvers, when a faculty member applies.
# ---------------------------------------------------------------------------

#: `kind` on the rows below, so the office can tell the three leave mails apart
#: on the Email delivery screen.
APPROVER_MAIL_KIND = "leave-approval-needed"
ON_LEAVE_MAIL_KIND = "leave-on-leave-today"

#: Whose leave the two mails below are about. "Faculty" is the MENTOR role: a
#: STUDENT's absence is not their teachers' business to be broadcast, and the
#: office's own leave is decided by a delegate who already sees the queue.
FACULTY_ROLES: tuple[Role, ...] = (Role.MENTOR,)

#: The approver's capability. The same string `routers/leave.py` names as
#: `LEAVE_APPROVAL_CAPABILITY`, restated rather than imported because that
#: router imports this module; `test_leave_mail` compares the two.
LEAVE_APPROVAL_CAPABILITY = "admin.leave_approvals"


def _is_active(user: User | None) -> bool:
    return user is not None and user.barred_at is None and bool((user.email or "").strip())


def _is_faculty(user: User | None) -> bool:
    return user is not None and user.role in FACULTY_ROLES


def approver_dedupe_key(lr: LeaveRequest, approver: User) -> str:
    """`leave-approval:{id}:{approver}`, plus `:r{n}` once a decision on the
    request has been changed — a REOPENED request is waiting again, and the bare
    key would find the submission's row and send nothing."""
    base = f"leave-approval:{lr.id}:{approver.id}"
    revision = lr.decision_revision or 0
    return base if revision == 0 else f"{base}:r{revision}"


def on_leave_dedupe_key(lr: LeaveRequest, day: date, recipient: User) -> str:
    return f"leave-today:{lr.id}:{day.isoformat()}:{recipient.id}"


def leave_approvers(db: Session) -> list[User]:
    """Every live account that can decide a leave request today.

    The Main Admin by role, and every faculty member holding a live grant of
    `admin.leave_approvals` — asked through `has_capability`, the same question
    `_assert_can_decide` asks, so this list and the gate cannot disagree about
    who may press Sanction. The SQL only narrows the candidates to accounts a
    grant NAMES; liveness (revoked, expired, pending, role changed) is decided
    by `has_capability`, never restated here.
    """
    direct = select(CapabilityGrant.subject_user_id).where(
        CapabilityGrant.capability == LEAVE_APPROVAL_CAPABILITY,
        CapabilityGrant.subject_kind == SubjectKind.USER,
    )
    via_group = (
        select(AccessGroupMember.user_id)
        .join(CapabilityGrant, CapabilityGrant.subject_group_id == AccessGroupMember.group_id)
        .where(
            CapabilityGrant.capability == LEAVE_APPROVAL_CAPABILITY,
            CapabilityGrant.subject_kind == SubjectKind.GROUP,
        )
    )
    candidates = db.scalars(
        select(User)
        .where(
            User.deleted_at.is_(None),
            User.disabled_at.is_(None),
            (User.role == Role.ADMIN)
            | ((User.role == Role.MENTOR) & (User.id.in_(direct) | User.id.in_(via_group))),
        )
        .order_by(User.email)
    ).all()
    return [
        u
        for u in candidates
        if _is_active(u)
        and has_capability(db, {"role": u.role.value, "userId": u.id}, LEAVE_APPROVAL_CAPABILITY)
    ]


def notify_approvers(db: Session, lr: LeaveRequest) -> list[MailLog]:
    """Tell everybody who can decide a FACULTY member's new application that
    it is waiting. Never the applicant, even when they hold the grant: nobody
    decides their own request. Returns the rows written; empty when the feature
    is off, the request is not a submission, or the applicant is not faculty.

    Never raises, and commits only its own `mail_logs` rows — call it after the
    submission's commit, `notify_transition`'s rule.
    """
    if not settings.leave_mail_enabled or lr.status is not LeaveStatus.SUBMITTED:
        return []
    applicant = db.get(User, lr.requester_user_id)
    if not _is_faculty(applicant) or applicant.barred_at is not None:
        return []
    subject = f"Leave request from {applicant.name} needs your decision"
    rows: list[MailLog] = []
    for approver in leave_approvers(db):
        if approver.id == applicant.id:
            continue
        if lr.decision_change_kind == "REOPENED":
            # A decided request put back in the queue (2026-10-13). Names the
            # applicant and the dates, never who reopened it or why.
            what = f"{applicant.name}'s leave request covering {_span(lr)} has been reopened"
        else:
            what = f"{applicant.name} has applied for leave covering {_span(lr)}"
        text = (
            f"Hello {approver.name},\n\n"
            f"{what}. It is waiting for a decision on the Leave Approvals screen.\n"
        )
        rows.append(
            deliver_once(
                db,
                kind=APPROVER_MAIL_KIND,
                recipient=approver.email,
                dedupe_key=approver_dedupe_key(lr, approver),
                subject=subject,
                send=lambda to, subj, text=text: transport_send(to, subj or "", text),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Every other faculty member, on each day of an approved faculty leave.
# ---------------------------------------------------------------------------


@dataclass
class OnLeaveSummary:
    """What one announcement pass did, for the job's log line."""

    day: date
    enabled: bool = True
    leaves: int = 0
    #: SENT rows on record for this day — sent by this run, or by an earlier
    #: run or a same-day approval (a dedupe hit is not a second mail).
    mailed: int = 0
    failed: int = 0


def faculty_on_leave(db: Session, day: date) -> list[LeaveRequest]:
    """Approved leave, of a faculty member still on the roster, covering `day`."""
    return list(
        db.scalars(
            select(LeaveRequest)
            .join(User, User.id == LeaveRequest.requester_user_id)
            .where(
                LeaveRequest.status == LeaveStatus.APPROVED,
                LeaveRequest.from_date <= day,
                LeaveRequest.to_date >= day,
                User.role.in_(FACULTY_ROLES),
                User.deleted_at.is_(None),
                User.disabled_at.is_(None),
            )
            .order_by(LeaveRequest.from_date, LeaveRequest.id)
        ).all()
    )


def _faculty_recipients(db: Session) -> list[User]:
    return [
        u
        for u in db.scalars(
            select(User)
            .where(User.role.in_(FACULTY_ROLES), User.deleted_at.is_(None), User.disabled_at.is_(None))
            .order_by(User.email)
        ).all()
        if _is_active(u)
    ]


def _on_leave_text(recipient: User, applicant: User, lr: LeaveRequest, day: date) -> str:
    if lr.from_date == lr.to_date:
        when = "today only"
    else:
        when = f"from {lr.from_date.isoformat()} to {lr.to_date.isoformat()}"
    return (
        f"Hello {recipient.name},\n\n"
        f"{applicant.name} is on leave today, {day.strftime('%A')} {day.isoformat()} "
        f"({when}).\n"
    )


def announce_on_leave(
    db: Session, lr: LeaveRequest, day: date, *, recipients: list[User] | None = None
) -> list[MailLog]:
    """Mail every other active faculty member that the applicant of `lr` is on
    leave on `day`. Sends NOTHING unless `day` is one of the leave's own days
    and the leave is an approved faculty leave — that check is here and not
    only in the callers, so no caller can announce a leave early.

    Once per (leave, day, recipient): `deliver_once` on
    `leave-today:{id}:{day}:{recipient}`, so the morning job and a same-day
    approval never both send, and a rerun sends nothing twice.
    """
    if not settings.leave_mail_enabled:
        return []
    if lr.status is not LeaveStatus.APPROVED or not (lr.from_date <= day <= lr.to_date):
        return []
    applicant = db.get(User, lr.requester_user_id)
    if not _is_faculty(applicant) or applicant.barred_at is not None:
        return []
    subject = f"{applicant.name} is on leave today"
    rows: list[MailLog] = []
    for recipient in recipients if recipients is not None else _faculty_recipients(db):
        if recipient.id == applicant.id:
            continue
        text = _on_leave_text(recipient, applicant, lr, day)
        rows.append(
            deliver_once(
                db,
                kind=ON_LEAVE_MAIL_KIND,
                recipient=recipient.email,
                dedupe_key=on_leave_dedupe_key(lr, day, recipient),
                subject=subject,
                send=lambda to, subj, text=text: transport_send(to, subj or "", text),
            )
        )
    return rows


def announce_faculty_on_leave(db: Session, *, day: date) -> OnLeaveSummary:
    """The morning pass (`python -m app.leave_today_job`): every approved
    faculty leave covering `day`, announced to every other faculty member."""
    summary = OnLeaveSummary(day=day, enabled=settings.leave_mail_enabled)
    if not summary.enabled:
        return summary
    leaves = faculty_on_leave(db, day)
    summary.leaves = len(leaves)
    if not leaves:
        return summary
    recipients = _faculty_recipients(db)
    for lr in leaves:
        for row in announce_on_leave(db, lr, day, recipients=recipients):
            if row is None:
                continue
            if row.status is MailStatus.FAILED:
                summary.failed += 1
            elif row.status is MailStatus.SENT:
                summary.mailed += 1
    return summary


# ---------------------------------------------------------------------------
# The request-path entry points. Each opens its OWN session, because they run
# as FastAPI background tasks after the response, when the request's session
# is already closed — and so a slow mail server never holds up the button.
# ---------------------------------------------------------------------------


def _in_own_session(what: str, leave_id: str, work: Callable[[Session, LeaveRequest], object]) -> None:
    """Run `work` against a fresh session, and never let it raise: the response
    has already gone, so an exception here reaches nobody who can act on it.
    Logged by TYPE only, `mailer.deliver_once`'s rule — a database error's text
    carries its bound parameters, which here are email addresses."""
    if not settings.leave_mail_enabled:
        return
    try:
        with SessionLocal() as db:
            lr = db.get(LeaveRequest, leave_id)
            if lr is not None:
                work(db, lr)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.error("Leave mail pass failed: %s for request %s (%s)", what, leave_id, type(exc).__name__)


def notify_approvers_in_background(leave_id: str) -> None:
    _in_own_session("approvers", leave_id, notify_approvers)


def announce_if_on_leave_today(leave_id: str) -> None:
    """After an approval: announce the leave now ONLY if today is one of its
    days. A leave approved ahead of time sends nothing here; the morning job
    sends it on the day."""
    _in_own_session("on leave today", leave_id, lambda db, lr: announce_on_leave(db, lr, local_today()))


# ---------------------------------------------------------------------------
# A decision changed after it was made (2026-10-13).
# ---------------------------------------------------------------------------

#: `kind` on the rows below, so the Email delivery screen tells a changed
#: decision apart from the first one.
CHANGE_MAIL_KIND = "leave-decision-changed"


def change_dedupe_key(lr: LeaveRequest, recipient: User) -> str:
    """`leave-change:{id}:r{revision}:{recipient}` — one message per person per
    change, however often the request is retried."""
    return f"leave-change:{lr.id}:r{lr.decision_revision}:{recipient.id}"


def _revoked_for_applicant(lr: LeaveRequest) -> tuple[str, str]:
    # Names nobody, like the applicant's other three mails, and carries NOT the
    # reason for the change: that is read on the screen, in context, where the
    # applicant can see the request it belongs to.
    return (
        "The sanction on your leave request has been withdrawn",
        f"{_GREETING}"
        f"The sanction on your leave request for {_kind_phrase(lr)} covering "
        f"{_span(lr)} has been withdrawn. You can see why on your Leave Requests "
        f"screen.\n",
    )


def _reopened_for_applicant(lr: LeaveRequest) -> tuple[str, str]:
    return (
        "Your leave request has been reopened",
        f"{_GREETING}"
        f"Your leave request for {_kind_phrase(lr)} covering {_span(lr)} has been "
        f"reopened and is waiting for a fresh decision. You can follow it on your "
        f"Leave Requests screen.\n",
    )


def _no_longer_covering(recipient: User, applicant: User, lr: LeaveRequest) -> tuple[str, str]:
    # The colleague who agreed to cover reads the name and the dates, as they
    # did when they were asked — never the printed option, never the reason.
    return (
        f"{applicant.name}'s leave is no longer sanctioned",
        f"Hello {recipient.name},\n\n"
        f"The leave {applicant.name} asked you to cover ({_span(lr)}) is no longer "
        f"sanctioned, so the cover you agreed to is not needed for now. If it is "
        f"sanctioned again you will see it on your Leave Requests screen.\n",
    )


def _covering_colleagues(db: Session, lr: LeaveRequest) -> list[User]:
    """Every account named on an Alternate Arrangements row, once each, minus
    the applicant. Rows typed as a bare name (no `user_id`) reach nobody: there
    is no account behind them to write to."""
    seen: set[str] = set()
    people: list[User] = []
    for row in lr.alt_rows or []:
        user_id = (row or {}).get("user_id") if isinstance(row, dict) else None
        if not user_id or user_id in seen or user_id == lr.requester_user_id:
            continue
        seen.add(user_id)
        user = db.get(User, user_id)
        if _is_active(user):
            people.append(user)
    return people


def notify_decision_changed(db: Session, lr: LeaveRequest) -> list[MailLog]:
    """Tell the applicant that a decision on their request was changed, and —
    on a revocation — tell every colleague named to cover that the cover is
    off. Off with the rest of leave mail (`leave_mail_enabled`), never raises,
    and is called AFTER the change's own commit, for `notify_transition`'s
    reasons.

    The "on leave today" broadcast needs nothing here: the morning job reads
    APPROVED rows only, so a revoked leave simply stops being announced."""
    if not settings.leave_mail_enabled:
        return []
    if lr.status is LeaveStatus.REVOKED:
        applicant_mail = _revoked_for_applicant(lr)
    elif lr.status is LeaveStatus.SUBMITTED and lr.decision_change_kind == "REOPENED":
        applicant_mail = _reopened_for_applicant(lr)
    else:
        return []
    rows: list[MailLog] = []
    applicant = db.get(User, lr.requester_user_id)
    if applicant is not None and (applicant.email or "").strip():
        subject, text = applicant_mail
        rows.append(
            deliver_once(
                db,
                kind=CHANGE_MAIL_KIND,
                recipient=applicant.email,
                dedupe_key=change_dedupe_key(lr, applicant),
                subject=subject,
                send=lambda to, subj, text=text: transport_send(to, subj or "", text),
            )
        )
    if lr.status is LeaveStatus.REVOKED and applicant is not None:
        for colleague in _covering_colleagues(db, lr):
            subject, text = _no_longer_covering(colleague, applicant, lr)
            rows.append(
                deliver_once(
                    db,
                    kind=CHANGE_MAIL_KIND,
                    recipient=colleague.email,
                    dedupe_key=change_dedupe_key(lr, colleague),
                    subject=subject,
                    send=lambda to, subj, text=text: transport_send(to, subj or "", text),
                )
            )
    return rows
