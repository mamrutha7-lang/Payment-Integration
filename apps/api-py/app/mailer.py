"""Send-exactly-once mailer over the MailLog dedupe store.

The catalogue of messages and the actual SMTP/API driver are out of scope here
(no mail transport is configured in this environment). What this module ports is
the guarantee the Next.js mailer gives: a message with a given `dedupe_key` is
delivered at most once, no matter how many times the sending job is re-run or how
many workers race. The unique index on `dedupe_key` is the arbiter — a losing
racer catches an IntegrityError and reports the existing row rather than sending
a second copy.

Keep this module free of request/`get_db` concerns so a background worker can call
it with its own Session, exactly as the Next.js mailer is callable off a cron.

A FAILED SEND IS LOGGED, AND THAT LINE IS LOAD-BEARING (2026-09-15).
`deliver_once` swallows the driver's exception on purpose -- a decision must
stand whether or not its mail went, so no caller is made to handle a mail
failure -- and until this date it swallowed it in SILENCE. The row carried
`error`, and nothing anywhere read the row.

What that cost: every outbound message failed for eighty minutes when the task
role lost permission to send under its configuration set. No alarm, no log
line, no failed request -- the endpoints all answered 200. A registration was
rejected and the applicant, who is not a user and has no other channel, got
nothing. It was found because a human said the mail had not arrived.

So the exception is logged HERE, at the one place every message in the product
passes through, with a literal string a CloudWatch metric filter matches
(`reep-mail-send-failed` in infra/cdk). Swallowing it for the CALLER and hiding
it from the OPERATOR are different decisions, and only the first one was ever
intended.
"""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models.mail import MailLog, MailStatus

log = logging.getLogger(__name__)

#: The literal the metric filter matches. Changing this text silently unhooks
#: the alarm, so it is a module constant and `tests/test_codebase_guards.py`
#: compares it against the filter pattern in infra/cdk/reep_core/stack.py.
MAIL_SEND_FAILED = "Mail send failed"

# A driver takes (recipient, subject) and raises on failure. None = no-op stub
# that records the intent without a transport (dev/default).
Driver = Callable[[str, str | None], None]


def deliver_once(
    db: Session,
    *,
    kind: str,
    recipient: str,
    dedupe_key: str,
    subject: str | None = None,
    suppress: bool = False,
    send: Driver | None = None,
) -> MailLog:
    """Deliver `kind` to `recipient` at most once for `dedupe_key`.

    - If the key was already used, returns the existing row and sends nothing —
      the entire point of the table.
    - `suppress=True` records a SUPPRESSED row (recipient opted out) without
      sending.
    - Otherwise reserves the key, attempts the send, and records SENT or FAILED.
    """
    existing = db.scalar(select(MailLog).where(MailLog.dedupe_key == dedupe_key))
    if existing is not None:
        return existing  # dedupe hit — no second delivery

    row = MailLog(
        kind=kind,
        recipient=recipient,
        dedupe_key=dedupe_key,
        subject=subject,
        status=MailStatus.SUPPRESSED if suppress else MailStatus.SENT,
    )
    db.add(row)
    try:
        db.flush()  # hit the unique index now, before doing any real work
    except IntegrityError:
        db.rollback()
        # A concurrent worker reserved the key between our read and flush; defer
        # to their row and send nothing.
        return db.scalar(select(MailLog).where(MailLog.dedupe_key == dedupe_key))

    if not suppress:
        try:
            if send is not None:
                send(recipient, subject)
            row.status = MailStatus.SENT
        except Exception as exc:  # driver failure — recorded, never raised to the caller
            row.status = MailStatus.FAILED
            row.error = str(exc)[:1000]
            # NOT `log.exception`: the traceback of a boto ClientError carries
            # the request id and the full error body, and this line is shipped
            # to CloudWatch and scrubbed for Sentry. `kind` and the exception's
            # own text say which message failed and why; the RECIPIENT is
            # deliberately absent, because a log line naming who was mailed is
            # exactly the student-address leak `telemetry_scrub` exists to stop.
            # The row has the recipient for whoever is entitled to read it.
            log.error("%s kind=%s error=%s", MAIL_SEND_FAILED, kind, str(exc)[:300])

    db.commit()
    db.refresh(row)
    return row
