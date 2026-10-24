"""Skill-claim notifications (2026-09-17): the verifiers hear that a claim is
waiting, the student hears what was decided.

Two messages and nothing else. `notify_verifiers_of_claim(db, ev)` runs after a
student files a badge claim on Skilling (`POST /student/badges/{code}/evidence`)
and mails every faculty member who can decide it; `notify_student_of_decision(db,
ev)` runs after a reviewer decides the claim (`POST /mentor/badge-evidence/{id}/
review`) and mails the student, whichever way it went.

THE CLAIM MAIL GOES TO THE VERIFIERS, NOT TO THE MENTOR (2026-10-16). Until the
owner took verification away from every mentor, the one person who could decide
a claim was the student's mentor, so that is who was mailed. Now the people who
can decide it are the holders of a "Verify skills & evidence" grant whose reach
covers the student (app/verifiers.py), and mailing the mentor would tell
somebody "it is waiting for your review" about a queue they cannot open. The
recipients are asked of `verifiers_for`, which asks the same question every
verification door asks, so the list and the gate cannot disagree. Nobody holds
such a grant yet means nobody is mailed and the claim waits for the office to
choose somebody.

==============================================================================
WHETHER THEY GO OUT IS DERIVED FROM THE TRANSPORT, NOT DEFAULTED TO OFF
==============================================================================

`settings.badge_mail_active` is the gate, and it is three-state: forced on,
forced off, or -- the default -- on exactly when a real transport exists.
`leave_mail_enabled` is a plain boolean defaulting to false, and its comment in
config.py says what that default protects: a machine with NO transport, where a
notification switched on writes a `mail_logs` row reading SENT about a message
that reached nobody. Deriving from `mail_configured` protects that machine just
as well (no transport, no mail, no row) and turns the notifications on for the
production task, which already carries `SES_FROM_ADDRESS`, without a second
variable in a task definition that somebody has to remember. A developer who
wants to read the message out of the console outbox sets
`BADGE_MAIL_ENABLED=true`; the test suite does the same through `settings`.

While the gate is off both functions return nothing (an empty list, or None)
and write nothing, so a screen that wants to say "the reviewers have been
emailed" has an honest value to branch on. No screen says it today.

==============================================================================
`deliver_once`, NEVER `mail_transport.send` DIRECTLY
==============================================================================

Both messages fire on state transitions that can be retried -- a double-tap on
Verify, a request that 500s after its commit -- so they go through
`app/mailer.py::deliver_once`, whose unique index on `dedupe_key` is the
arbiter. The claim key is `badge-claim:{evidence_id}:{recipient_user_id}`: one row
and one message per verifier, however many times the handler is re-entered. The decision key is
`badge-decision:{evidence_id}:{status}`: stable per transition and different
per transition, so a claim sent back for MORE_INFO and then approved produces
two messages and a retried approval produces one.

==============================================================================
WHAT TRAVELS, AND WHY THE REVIEWER'S NOTE IS IN IT
==============================================================================

`leave_mail.py` keeps the approver's note OUT of its mail, because a note
written for the office's file is not a note written to the applicant. The badge
`review_note` is the opposite kind of sentence: the Skilling screen shows it to
the student as "Note from the reviewer", the review form labels it as the whole
of what the student is told, and the API now REFUSES a rejection without one --
it exists to be read by the student, so the mail that tells them the decision
carries it. What never travels is anything from the student's record: no USN,
no marks, no attendance, no certificate. The verifier's message names the
student and the badge and links to the queue; the student's names the badge,
the outcome and the note, and never who decided it.

The logger is muted from Sentry (`app/telemetry_scrub.py::MUTED_LOGGERS`)
alongside `app.leave_mail`, for its reason: a module whose job is composing
text about a named student is the one that grows a debug line under pressure.

RULE 1 is not in play (nothing here reaches a model). The recipients this
module can address are the student's own account and the accounts whose
verification grant reaches that student -- the people who can already open the
claim the message names.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .mail_transport import send as transport_send
from .mailer import deliver_once
from .models.badge import BADGE_BY_CODE, BadgeEvidence, EvidenceStatus
from .models.mail import MailLog
from .models.user import Student, User
from .verifiers import verifiers_for

log = logging.getLogger(__name__)

#: `kind` on the MailLog rows, so the office can count claim mail apart from
#: activation, reset and leave mail in one query.
CLAIM_MAIL_KIND = "badge-claim"
DECISION_MAIL_KIND = "badge-decision"


def claim_dedupe_key(ev: BadgeEvidence, recipient_user_id: str) -> str:
    """`badge-claim:{id}:{recipient}` -- one message per claim per verifier,
    however often it is retried."""
    return f"badge-claim:{ev.id}:{recipient_user_id}"


def decision_dedupe_key(ev: BadgeEvidence) -> str:
    """`badge-decision:{id}:{status}` -- stable per transition, different per
    transition. Exported so a test can assert the key without composing a
    message."""
    return f"badge-decision:{ev.id}:{ev.status.value}"


def _badge_name(ev: BadgeEvidence) -> str:
    badge = BADGE_BY_CODE.get(ev.badge_code)
    return badge.name if badge else ev.badge_code


def _link(path: str) -> str:
    return f"{settings.web_origin.rstrip('/')}{path}"


def _student_account(db: Session, ev: BadgeEvidence) -> tuple[Student, User] | None:
    row = db.execute(
        select(Student, User)
        .join(User, Student.user_id == User.id)
        .where(Student.id == ev.student_id)
    ).first()
    return (row[0], row[1]) if row else None


def _reachable(user: User | None) -> bool:
    """An account that may still sign in and has an address. A removed or
    disabled account is not mailed: the person has left, and the row that
    would say so is the one every sign-in door already reads (`barred_at`)."""
    return user is not None and user.barred_at is None and bool((user.email or "").strip())


def _driver(text: str) -> Callable[[str, str | None], None]:
    return lambda to, subject: transport_send(to, subject or "", text)


# ------------------------------------------------------------- the claim --


def notify_verifiers_of_claim(db: Session, ev: BadgeEvidence) -> list[MailLog]:
    """Tell everybody who can decide this claim that it is waiting. Returns
    the `mail_logs` rows written, one per verifier.

    Empty means one of four things and the caller must not read it as failure:
    the gate is off, nobody holds a verification grant reaching this student
    (the claim waits until the office grants one -- the Main Admin can grant
    itself the queue), every such account is barred or has no address, or the
    student's own account is gone. It NEVER raises: a mail that did not go out
    must never be the reason a claim fails to file.

    Call it AFTER the claim's own commit, for `leave_mail.notify_transition`'s
    reason: `deliver_once` commits its own row, and a message about a claim
    that has not landed yet is worse than a late one.
    """
    if not settings.badge_mail_active:
        return []
    account = _student_account(db, ev)
    if account is None:
        return []
    student, student_user = account
    verifiers = [u for u in verifiers_for(db, student.id) if _reachable(u)]
    if not verifiers:
        log.info("Claim notification skipped: nobody can verify evidence %s yet", ev.id)
        return []

    badge = _badge_name(ev)
    subject = f"{student_user.name} has claimed the {badge} badge"
    what = ev.title.strip() if ev.title else "a certificate"
    issued = f" issued by {ev.provider.strip()}" if (ev.provider or "").strip() else ""
    rows: list[MailLog] = []
    for verifier in verifiers:
        text = (
            f"Hello {verifier.name},\n\n"
            f"{student_user.name} has claimed the {badge} badge on REEP and attached "
            f"{what}{issued} as evidence. It is waiting for your review.\n\n"
            f"Open your verification queue to verify it, ask for changes, or reject it "
            f"with a reason: {_link('/mentor/verifications')}\n\n"
            f"The student is told your decision, and your note, by email.\n"
        )
        rows.append(
            deliver_once(
                db,
                kind=CLAIM_MAIL_KIND,
                recipient=verifier.email,
                dedupe_key=claim_dedupe_key(ev, verifier.id),
                subject=subject,
                send=_driver(text),
            )
        )
    return rows


# ---------------------------------------------------------- the decision --


def _approved(name: str, badge: str, note: str | None) -> tuple[str, str]:
    body = (
        f"Hello {name},\n\n"
        f"The evidence you submitted for the {badge} badge has been verified. "
        f"The badge is now marked verified on your Skilling board.\n"
    )
    if note:
        body += f"\nNote from the reviewer: {note}\n"
    body += f"\nSkilling: {_link('/student/skilling')}\n"
    return f"Your {badge} badge is verified", body


def _rejected(name: str, badge: str, note: str | None) -> tuple[str, str]:
    body = (
        f"Hello {name},\n\n"
        f"The evidence you submitted for the {badge} badge has been reviewed "
        f"and was not verified.\n"
    )
    if note:
        body += f"\nReason: {note}\n"
    body += (
        f"\nYou can claim the badge again with different evidence from your "
        f"Skilling board: {_link('/student/skilling')}\n"
    )
    return f"Your {badge} badge claim was not verified", body


def _more_info(name: str, badge: str, note: str | None) -> tuple[str, str]:
    body = (
        f"Hello {name},\n\n"
        f"The evidence you submitted for the {badge} badge has been reviewed, "
        f"and changes are needed before it can be verified.\n"
    )
    if note:
        body += f"\nWhat to change: {note}\n"
    body += f"\nClaim the badge again from your Skilling board: {_link('/student/skilling')}\n"
    return f"Your {badge} badge claim needs changes", body


#: Which outcomes are worth a message. A status not here sends nothing --
#: PENDING_VERIFICATION especially: the student filed it themselves and does
#: not need to be told what they just did.
_DECISIONS: dict[EvidenceStatus, Callable[[str, str, str | None], tuple[str, str]]] = {
    EvidenceStatus.APPROVED: _approved,
    EvidenceStatus.REJECTED: _rejected,
    EvidenceStatus.MORE_INFO_REQUIRED: _more_info,
}


def notify_student_of_decision(db: Session, ev: BadgeEvidence) -> MailLog | None:
    """Tell the student their claim was decided. Returns the `mail_logs` row,
    or None when nothing was sent -- the gate is off, the status is not a
    decision, or the account is gone or barred. Never raises, and must be
    called AFTER the decision's own commit, as above."""
    if not settings.badge_mail_active:
        return None
    builder = _DECISIONS.get(ev.status)
    if builder is None:
        return None
    account = _student_account(db, ev)
    if account is None or not _reachable(account[1]):
        log.info("Decision notification skipped: no reachable student for evidence %s", ev.id)
        return None
    user = account[1]
    note = (ev.review_note or "").strip() or None
    subject, text = builder(user.name, _badge_name(ev), note)
    return deliver_once(
        db,
        kind=DECISION_MAIL_KIND,
        recipient=user.email,
        dedupe_key=decision_dedupe_key(ev),
        subject=subject,
        send=_driver(text),
    )
