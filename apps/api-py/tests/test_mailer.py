"""The send-exactly-once mailer over the MailLog dedupe store. Needs the DB;
uses a unique dedupe_key per run so it never collides with prior data.
"""

import logging
import uuid

from app.db import SessionLocal
from app.mailer import MAIL_SEND_FAILED, deliver_once
from app.models.mail import MailStatus

from conftest import requires_db


@requires_db
def test_first_send_then_dedupe_hit():
    key = f"test:{uuid.uuid4().hex}"
    with SessionLocal() as db:
        first = deliver_once(db, kind="test", recipient="a@x.com", dedupe_key=key)
        assert first.status is MailStatus.SENT
        # Same key again → same row, no second delivery.
        again = deliver_once(db, kind="test", recipient="a@x.com", dedupe_key=key)
        assert again.id == first.id
        assert again.status is MailStatus.SENT


@requires_db
def test_suppressed_records_without_sending():
    key = f"test:{uuid.uuid4().hex}"
    with SessionLocal() as db:
        row = deliver_once(db, kind="test", recipient="opt@x.com", dedupe_key=key, suppress=True)
        assert row.status is MailStatus.SUPPRESSED


@requires_db
def test_driver_failure_recorded_not_raised():
    key = f"test:{uuid.uuid4().hex}"

    def boom(recipient, subject):
        raise RuntimeError("SMTP 550")

    with SessionLocal() as db:
        row = deliver_once(db, kind="test", recipient="b@x.com", dedupe_key=key, send=boom)
        assert row.status is MailStatus.FAILED
        assert row.error and "550" in row.error


@requires_db
def test_driver_failure_is_logged_and_never_names_the_recipient(caplog):
    """The line that would have caught the 2026-09-15 outage in minutes.

    Swallowing the exception for the CALLER is deliberate -- a decision stands
    whether or not its mail went. Hiding it from the OPERATOR was not: every
    message in the product failed for eighty minutes behind 200s, a `mail_logs`
    column nothing reads, and no log line at all.

    The recipient must NOT appear. This line ships to CloudWatch and through
    Sentry's scrubbers, and the addresses here are students'; `kind` says which
    message failed and the row holds the address for whoever may read it.
    """
    key = f"test:{uuid.uuid4().hex}"
    recipient = "leaky@x.com"

    def boom(recipient, subject):
        raise RuntimeError("AccessDenied: not authorized to perform ses:SendEmail")

    with caplog.at_level(logging.ERROR, logger="app.mailer"):
        with SessionLocal() as db:
            row = deliver_once(db, kind="registration-rejected", recipient=recipient,
                               dedupe_key=key, send=boom)
            assert row.status is MailStatus.FAILED

    failures = [r for r in caplog.records if MAIL_SEND_FAILED in r.getMessage()]
    assert failures, "a swallowed send failure left no log line -- this is the outage again"
    line = failures[0].getMessage()
    assert "registration-rejected" in line, "the line must say which message failed"
    assert "ses:SendEmail" in line, "the line must carry the driver's own reason"
    assert recipient not in line, (
        "the failure line names a recipient -- that is a student address in CloudWatch and Sentry"
    )
