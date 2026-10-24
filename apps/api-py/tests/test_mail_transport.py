"""The transport itself: what reaches SES, and what reaches nobody.

`tests/test_mailer.py` covers the send-exactly-once layer ABOVE this and needs
the database. This module covers `app/mail_transport.py` alone and needs
nothing: no Postgres, no AWS, no credentials. That is the point — the two
behaviours pinned here are the ones whose failure is SILENT in production, and a
test that only runs where Postgres is up is a test that is skipped on the
machine where somebody is editing this file.

1. THE CONFIGURATION SET IS NAMED ON THE CALL. SES applies either the set named
   on the send or the one attached to the identity as its default. Production
   has both, and they are not redundant: an edit to the identity kills the
   default silently -- every send still succeeds, the event destination stops
   firing, and both reputation alarms sit at INSUFFICIENT_DATA looking exactly
   like a quiet week -- where a named set that stops existing makes SES REFUSE
   the send. Loud beats quiet, so the name travels.
2. BLANK MEANS OMIT, NEVER "". SES reads an empty ConfigurationSetName as a set
   that does not exist and refuses the send, so passing "" through would turn
   "this deployment does not name one" into a total mail outage on every
   development machine and in CI.
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from app import mail_transport
from app.config import settings


class _FakeSes:
    """Records the one send_email call instead of making it.

    It also answers the suppression list, because since 2026-09-17 every send
    asks it. A fake without `get_suppressed_destination` would still let these
    tests pass — the lookup fails open by design — but it would pass by way of
    the error branch, so the clean path nothing ever exercises would be the
    production one.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        #: address -> (reason, LastUpdateTime). Empty = nobody is suppressed.
        self.suppressed: dict[str, tuple[str, str]] = {}
        self.lifted: list[str] = []

    def send_email(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"MessageId": "fake"}

    def get_suppressed_destination(self, EmailAddress: str) -> dict:
        entry = self.suppressed.get(EmailAddress)
        if entry is None:
            raise ClientError(
                {"Error": {"Code": "NotFoundException", "Message": "not on the list"}},
                "GetSuppressedDestination",
            )
        reason, since = entry
        return {
            "SuppressedDestination": {
                "EmailAddress": EmailAddress,
                "Reason": reason,
                "LastUpdateTime": since,
            }
        }

    def delete_suppressed_destination(self, EmailAddress: str) -> dict:
        self.lifted.append(EmailAddress)
        self.suppressed.pop(EmailAddress, None)
        return {}


@pytest.fixture
def ses(monkeypatch) -> _FakeSes:
    client = _FakeSes()
    fake_boto3 = type("boto3", (), {"client": staticmethod(lambda *a, **k: client)})
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)
    monkeypatch.setattr(settings, "ses_from_address", "no-reply@example.test")
    return client


def test_a_named_configuration_set_travels_with_every_send(ses, monkeypatch):
    monkeypatch.setattr(settings, "ses_configuration_set", "reep-transactional")
    mail_transport.send("student@example.test", "Set your password", "link")
    assert ses.calls[0]["ConfigurationSetName"] == "reep-transactional"
    assert ses.calls[0]["FromEmailAddress"] == "no-reply@example.test"
    assert ses.calls[0]["Destination"] == {"ToAddresses": ["student@example.test"]}


def test_a_blank_configuration_set_is_omitted_and_not_sent_as_empty(ses, monkeypatch):
    monkeypatch.setattr(settings, "ses_configuration_set", "")
    mail_transport.send("student@example.test", "Set your password", "link")
    assert "ConfigurationSetName" not in ses.calls[0], (
        'an empty ConfigurationSetName is a set SES cannot find; it would refuse every message on every '
        "deployment that does not name one"
    )


def test_whitespace_is_not_a_configuration_set(ses, monkeypatch):
    monkeypatch.setattr(settings, "ses_configuration_set", "   ")
    mail_transport.send("student@example.test", "Set your password", "link")
    assert "ConfigurationSetName" not in ses.calls[0]


def test_with_no_sender_nothing_reaches_ses_and_the_message_is_kept(monkeypatch):
    """The console transport, which is what every development machine runs.

    It is not a stub: the message is logged in full and kept, which is how a
    developer reads an activation link out of the uvicorn log and how the rest
    of the suite reads one out of `outbox`.
    """
    monkeypatch.setattr(settings, "ses_from_address", "")
    monkeypatch.setattr(settings, "ses_configuration_set", "reep-transactional")
    mail_transport.outbox.clear()
    mail_transport.send("student@example.test", "Set your password", "https://example.test/onboard?token=abc")
    assert len(mail_transport.outbox) == 1
    entry = mail_transport.outbox[0]
    assert entry.to == "student@example.test"
    assert "https://example.test/onboard?token=abc" in entry.text


# --------------------------------------------------------------------------- #
#  The suppression list: the failure that looked exactly like success          #
# --------------------------------------------------------------------------- #
#
#  A hard bounce or a complaint puts an address on SES's ACCOUNT SUPPRESSION
#  LIST. SES then ACCEPTS every later send for it — a message id comes back,
#  `deliver_once` writes SENT — and delivers nothing, for ever. A student in
#  that state is told "we have emailed you a code" by the onboarding walk, by
#  "Forgot password?", by the change-password screen and by the setup-link
#  button, and not one of those mails can arrive, while Google sign-in keeps
#  working because no mail is involved. That was reported from production on
#  2026-09-17 and nothing in the product could see it.


def test_a_suppressed_address_is_refused_instead_of_accepted_and_dropped(ses):
    """The send must not happen: SES would take it and deliver nothing."""
    ses.suppressed["gone@example.test"] = ("BOUNCE", "2026-03-04T09:00:00Z")
    with pytest.raises(mail_transport.SuppressedRecipient):
        mail_transport.send("gone@example.test", "Set your password", "link")
    assert ses.calls == [], "a suppressed address must never reach send_email"


def test_the_refusal_says_why_and_never_names_the_recipient(ses):
    """`deliver_once` puts `str(exc)` in a CloudWatch line that carries no
    address on purpose. The reason and the date are what an operator needs."""
    ses.suppressed["gone@example.test"] = ("COMPLAINT", "2026-03-04T09:00:00Z")
    with pytest.raises(mail_transport.SuppressedRecipient) as caught:
        mail_transport.send("gone@example.test", "Set your password", "link")
    message = str(caught.value)
    assert "gone@example.test" not in message
    assert "COMPLAINT" in message and "2026-03-04" in message


def test_a_clean_address_is_sent_normally(ses):
    mail_transport.send("fine@example.test", "Set your password", "link")
    assert len(ses.calls) == 1


def test_the_check_fails_OPEN_so_it_can_never_be_why_a_link_is_not_sent(ses, monkeypatch):
    """No grant, a throttle, a region that will not answer — the send proceeds.

    Failing closed here would turn one missing IAM permission into every
    student's link, which is a far worse outage than the one this guards.
    """

    def boom(**kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetSuppressedDestination")

    monkeypatch.setattr(ses, "get_suppressed_destination", boom)
    mail_transport.send("fine@example.test", "Set your password", "link")
    assert len(ses.calls) == 1


def test_the_probe_tells_a_clean_address_apart_from_one_nobody_could_check(ses, monkeypatch):
    """`suppression_for` collapses both to None; the office screen must not.

    "We asked and it is fine" and "we could not ask" are opposite facts, and a
    screen that renders the second as the first tells the office an address is
    healthy when nobody looked — the same mistake `X-Reep-Scope` avoids by
    never letting `none` and `programme` render the same.
    """
    assert mail_transport.suppression_probe("fine@example.test") == (True, False)

    ses.suppressed["gone@example.test"] = ("BOUNCE", "2026-03-04T09:00:00Z")
    assert mail_transport.suppression_probe("gone@example.test") == (True, True)

    def boom(**kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetSuppressedDestination")

    monkeypatch.setattr(ses, "get_suppressed_destination", boom)
    assert mail_transport.suppression_probe("fine@example.test") == (False, False)


def test_lifting_a_suppression_asks_the_provider_and_reports_the_outcome(ses):
    ses.suppressed["gone@example.test"] = ("BOUNCE", "2026-03-04T09:00:00Z")
    assert mail_transport.lift_suppression("gone@example.test") is True
    assert ses.lifted == ["gone@example.test"]
    # And the address is deliverable again, which is the whole point.
    mail_transport.send("gone@example.test", "Set your password", "link")
    assert len(ses.calls) == 1


def test_a_lift_that_fails_reports_false_rather_than_raising(ses, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(ses, "delete_suppressed_destination", boom)
    assert mail_transport.lift_suppression("gone@example.test") is False
