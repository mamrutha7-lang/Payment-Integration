"""What REEP has tried to send, and whether the provider will deliver it.

    GET    /api/admin/mail-log                      the last N messages
    GET    /api/admin/mail-log/suppression          is THIS address deliverable?
    DELETE /api/admin/mail-log/suppression          lift it, and say why

WHY THIS EXISTS. `mail_logs` has been written on every send since the Prisma
days and READ BY NOTHING — no endpoint, no screen, no script. The index
`ix_maillog_sent_at` was added for "the ops mail screen", and that screen was
never built. So when a student says "the code never came", the office had no
way to answer even the first question, which is whether REEP tried at all.

THE SECOND QUESTION IS THE ONE THAT COST US A STUDENT. `MailStatus.SENT` means
SES ACCEPTED the message, never that anybody received it. A hard bounce or a
complaint puts an address on SES's ACCOUNT SUPPRESSION LIST, and from then on
SES accepts every send for it and delivers nothing — so a student whose address
bounced once, possibly during the sandbox period months ago, is told "we have
emailed you a code" by the onboarding walk, by "Forgot password?", by the
change-password screen and by the setup-link button, every time, forever, and
not one of those mails can arrive. Google sign-in keeps working, because no
mail is involved. That is exactly the report this module was written for, and
before it there was no place in the product where the truth existed.

`mail_transport.suppression_for` now refuses such a send up front, so new
attempts land as FAILED with the reason in `error` rather than as a lie. This
screen is the other half: it shows those rows, it asks SES about an address on
demand — which also covers the message that was SENT honestly and bounced
AFTERWARDS, where the row cannot know — and it lets the office lift the
suppression once the address is fixed, which is the actual remedy and is
otherwise a trip to the AWS console.

MAIN ADMIN ONLY (`require_admin`), and not a capability. `mail_logs.recipient`
is every address the deployment has ever written to — students, applicants who
were rejected, staff — so this is a roster of people by another name, and rule
2's answer for the whole roster is the office account. A capability would be
the office handing that out; if that is ever wanted, it is a catalogue key and
a B2.1 enforcement row, not a default.

READ-ONLY OVER `mail_logs`. Nothing here writes, edits or deletes a row: the
table is the record of what was attempted and must stay exactly that. The one
write in this module is to the AUDIT trail, for the one act that changes
something outside REEP.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import mail_transport
from ..architecture_events import record_change
from ..db import get_db
from ..identity import get_current_session
from ..models.mail import MailLog, MailStatus
from .mentor import require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-mail"])

#: The unfiltered view is `ORDER BY sent_at DESC LIMIT n` over a table that
#: grows with the roster, which is what `ix_maillog_sent_at` is for. Capped so
#: a screen cannot ask for the whole history by editing a query string.
MAX_ROWS = 200


def _office(session: dict = Depends(get_current_session)) -> dict:
    """The Main Admin's session, as a DEPENDENCY.

    `require_admin` takes a plain `session: dict`, so handing it to `Depends`
    directly made FastAPI read that parameter as a required JSON BODY: every
    request to all three endpoints below answered 422 "body: Field required",
    before any authentication, for everybody. A GET cannot usefully carry a
    body, so the Email delivery screen could never load. Found by the Swagger
    contract suite (testing/api); pinned by tests/test_admin_mail_log.py.
    """
    return require_admin(session)


class MailRowOut(BaseModel):
    id: str
    kind: str
    recipient: str
    subject: str | None
    status: MailStatus
    #: The driver's complaint on a FAILED row — including, since 2026-09-17,
    #: the sentence that says the provider is suppressing the address.
    error: str | None
    sent_at: datetime


class SuppressionOut(BaseModel):
    """SES's answer about one address. `checked` false means we could not ask."""

    email: str
    checked: bool
    suppressed: bool
    reason: str | None = None
    since: str | None = None


@router.get("/mail-log", response_model=list[MailRowOut])
def mail_log(
    kind: str | None = Query(default=None),
    recipient: str | None = Query(default=None),
    failed_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=MAX_ROWS),
    session: dict = Depends(_office),
    db: Session = Depends(get_db),
) -> list[MailRowOut]:
    """The last `limit` messages, newest first, narrowed by the office.

    `recipient` matches on a CASE-FOLDED PREFIX rather than equality, because
    the address the office types is the one the student told them and the one
    on the row came from the roster; asking them to agree exactly turns a
    support call into a spelling exercise. It is not a LIKE over the middle of
    the string — that could not use the index and would let one query walk the
    whole table looking for a substring.
    """
    stmt = select(MailLog)
    if kind:
        stmt = stmt.where(MailLog.kind == kind.strip())
    if recipient:
        needle = recipient.strip().lower()
        stmt = stmt.where(MailLog.recipient.ilike(f"{needle}%"))
    if failed_only:
        stmt = stmt.where(MailLog.status == MailStatus.FAILED)
    rows = db.scalars(stmt.order_by(MailLog.sent_at.desc()).limit(limit)).all()
    return [
        MailRowOut(
            id=r.id,
            kind=r.kind,
            recipient=r.recipient,
            subject=r.subject,
            status=r.status,
            error=r.error,
            sent_at=r.sent_at,
        )
        for r in rows
    ]


@router.get("/mail-log/suppression", response_model=SuppressionOut)
def suppression(
    email: str = Query(min_length=3, max_length=254),
    session: dict = Depends(_office),
) -> SuppressionOut:
    """Ask SES, right now, whether it will deliver to this address.

    THE ROW CANNOT ANSWER THIS AND NEVER WILL. A message accepted on Monday and
    bounced on Monday evening leaves a row reading SENT for ever — correct, at
    the moment it was written — so the live question has to be asked of the
    provider. That is also why this is a lookup per address rather than a state
    kept on the row: the answer changes without REEP being involved, in both
    directions, and a copy of it would be stale exactly when it mattered.

    `checked: false` is NOT "deliverable". It means the question could not be
    put — no transport configured, no `ses:GetSuppressedDestination` grant, a
    region that would not answer — and the screen must say so rather than
    render it as a clean bill of health, the `X-Reep-Scope` rule about `none`
    and `programme` never looking the same.
    """
    address = email.strip().lower()
    if not mail_transport.configured():
        return SuppressionOut(email=address, checked=False, suppressed=False)
    found = mail_transport.suppression_for(address)
    if found is None:
        # `suppression_for` FAILS OPEN and returns None both for "not on the
        # list" and for "could not ask", which is right where the cost of
        # guessing is one unsent message and wrong here, where the office is
        # being told something. So the transport's own probe is repeated with
        # the two cases separated.
        checked, suppressed = mail_transport.suppression_probe(address)
        return SuppressionOut(email=address, checked=checked, suppressed=suppressed)
    return SuppressionOut(
        email=address,
        checked=True,
        suppressed=True,
        reason=found.reason,
        since=found.since,
    )


@router.delete("/mail-log/suppression", response_model=SuppressionOut)
def lift_suppression(
    request: Request,
    email: str = Query(min_length=3, max_length=254),
    session: dict = Depends(_office),
    db: Session = Depends(get_db),
) -> SuppressionOut:
    """Take this address off SES's suppression list so mail can reach it again.

    AUDITED, because it is the one act here that changes something, and what it
    changes is outside this database: an address the provider had decided not to
    deliver to becomes deliverable again on the office's say-so. If the reason
    was a real dead mailbox, the next send bounces and SES puts it straight
    back — which is the system working, and the audit row is what makes the
    second lift a decision somebody can see rather than a habit.

    It is NOT a way to send anything. The student still walks the same three
    steps; this only stops the provider dropping the mail on the way.
    """
    address = email.strip().lower()
    if not mail_transport.configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This deployment has no mail transport, so there is no suppression list.",
        )
    before = mail_transport.suppression_for(address)
    lifted = mail_transport.lift_suppression(address)
    if not lifted:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The provider would not lift it. Check the mail alarm and try again.",
        )
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="mail_suppression",
        entity_id=address,
        action="SUPPRESSION_LIFTED",
        before={"reason": before.reason, "since": before.since} if before else None,
        after=None,
        event_type="mail.suppression.lifted",
        payload={"email": address},
    )
    db.commit()
    log.info("mail suppression lifted by %s", session.get("userId"))
    return SuppressionOut(email=address, checked=True, suppressed=False)
