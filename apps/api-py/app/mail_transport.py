"""How an email actually leaves the building. Amazon SES, or the console.

THE DRIVER `app/mailer.py` LEFT A HOLE FOR. That module guarantees a message is
sent at most once per dedupe key and takes a `send(recipient, subject)`
callable; it says plainly that the transport is out of its scope. This is the
transport. The whole choice of provider lives in `send()` — swapping SES for
the college's SMTP relay later is this file, not a project.

WHY SES. The deciding factor is not price (at a college's volume every option
is effectively free); it is that SES needs NO CREDENTIAL. It authenticates
through the same task role that already reaches Bedrock for the interviewer,
so there is nothing to paste, leak or rotate — where every other provider adds
an API key someone has to store, and puts student email addresses in a third
party's systems. There are three settings and no credential among them:
`SES_FROM_ADDRESS`, `SES_CONFIGURATION_SET`, and `SES_REGION`, which falls
back to the AWS environment like everything else here.

WHERE THIS STANDS (2026-09-15). The sandbox is behind us. `sast-skills.com` is
verified with DKIM signing, the account holds production access in ap-south-1,
and `SES_FROM_ADDRESS=no-reply@sast-skills.com` reached the running api task at
13:55 UTC that day — before which every task in every revision booted saying
"NO TRANSPORT" and this module's console path was the whole story. The
institution's own `bgscet.ac.in` was NOT taken to verification: the deployment
sends as sast-skills.com by decision, and docs/ses-mail.md records why and what
moving would cost. Nothing here was ever blocked on any of it — the on-screen
activation link and the console transport below both work without SES, which is
why an unverified domain was never an outage.

THE CONFIGURATION SET IS NAMED ON THE CALL, NOT INHERITED. `send()` passes
`settings.ses_configuration_set` when it is set, and the alternative is worse
than it looks: SES also applies the set attached to the IDENTITY as its
default, so tracking that is inherited disappears the day someone edits the
identity, with every send still succeeding and both reputation alarms sitting at
INSUFFICIENT_DATA looking like a quiet week. A named set cannot fail quietly —
SES refuses a send naming one that does not exist. The setting's own comment in
config.py carries the full reasoning.

THE CONSOLE TRANSPORT IS NOT A STUB, AND IT IS NOT LEGACY EITHER. With
`SES_FROM_ADDRESS` blank — every development machine, every CI run, and any
deployment that has not been given an identity — a
message is logged in full and kept in `outbox`, a bounded in-memory list. A
developer reads the activation link out of the uvicorn log; the test suite
reads it out of `outbox`. It is bounded and only ever filled when no real
transport exists, so it never becomes a copy of student mail sitting in a
production process.

AND IT ASKS WHETHER THE ADDRESS CAN RECEIVE ANYTHING (2026-09-17). Accepting a
message is not delivering it. A hard bounce or a complaint puts an address on
SES's ACCOUNT SUPPRESSION LIST, and from then on SES takes every send for it,
returns a message id, and delivers nothing — so `deliver_once` writes SENT and
every screen in the product tells that student "we have emailed you a code",
for ever. It was reported from production as a student who could sign in with
Google and never received a code by any route, and nothing here could see it,
because there was no failure to see. `suppression_for` is the question, asked
immediately before the send; `SuppressedRecipient` is what a suppressed address
raises INSTEAD of being sent, which turns the one silent failure in this module
into the FAILED row, the `error` sentence and the CloudWatch line that every
other mail failure already produced. The office reads those on `/admin/mail`
and lifts the suppression from there.

RULE 1 (student data must not leave the machine unbidden) is not engaged: an
activation or reset email carries a name and a link, never a mark, a USN or a
transcript. Keep it that way — do not put a student's record in an email.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

from .config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    to: str
    subject: str
    text: str


#: What the console transport delivered, newest last. Bounded: this is a
#: development and test affordance, not a mail archive. Empty whenever a real
#: transport is configured.
outbox: deque[OutboxEntry] = deque(maxlen=200)


def configured() -> bool:
    """Is there a real transport? Read per call so a test can flip it."""
    return bool(settings.ses_from_address.strip())


def _region() -> str:
    import os

    return (
        settings.ses_region.strip()
        or os.environ.get("AWS_REGION", "").strip()
        or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        or "ap-south-1"
    )


class SuppressedRecipient(RuntimeError):
    """SES holds this address on its ACCOUNT SUPPRESSION LIST and will not deliver.

    Raised INSTEAD of sending, so `mailer.deliver_once` records FAILED with this
    sentence in `error` and logs the line the CloudWatch metric filter matches.
    Everything downstream of a failed send already exists; this only stops the
    one case that never reached it.

    THE MESSAGE MUST NOT NAME THE RECIPIENT. `deliver_once` puts `str(exc)` into
    a log line that is shipped to CloudWatch and scrubbed for Sentry, and that
    line deliberately carries no address. The row keeps the recipient for
    whoever is entitled to read it; this string carries only the reason and the
    date.
    """


@dataclass(frozen=True, slots=True)
class Suppression:
    """What SES says about one address, or None from `suppression_for`."""

    reason: str
    since: str


def _ask_provider(to: str) -> tuple[bool, Suppression | None]:
    """(could we ask, what it said). The one place that talks to the list.

    Separating "we asked and it is clean" from "we could not ask" is the whole
    reason this returns a pair. `suppression_for` collapses both to None
    because a send must proceed when the check cannot answer; the office screen
    must NOT, because telling somebody an address is fine when nobody looked is
    the same shape of lie this module exists to stop.
    """
    import boto3
    from botocore.exceptions import ClientError

    try:
        answer = boto3.client("sesv2", region_name=_region()).get_suppressed_destination(
            EmailAddress=to
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NotFoundException":
            return True, None  # asked, and the address is clean
        log.warning("Could not read the SES suppression list: %s", exc.response.get("Error", {}))
        return False, None
    except Exception as exc:  # credentials, region, network — never fatal here
        log.warning("Could not read the SES suppression list: %s", exc)
        return False, None

    entry = answer.get("SuppressedDestination", {})
    return True, Suppression(
        reason=str(entry.get("Reason") or "UNKNOWN"),
        since=str(entry.get("LastUpdateTime") or "")[:10] or "an unknown date",
    )


def suppression_for(to: str) -> Suppression | None:
    """Is this address on SES's account suppression list? None = no, or unknown.

    THIS IS THE ANSWER TO "THE CODE NEVER CAME", and nothing in the product
    could ask it before. A hard bounce or a complaint puts an address on this
    list, and SES then accepts every later send for it and delivers NOTHING —
    `send_email` returns a message id, `deliver_once` writes SENT, every screen
    says "we have emailed you", and the student is told that forever. A bounce
    during the sandbox period suppresses an address that has been perfectly fine
    ever since. The list is where a durable delivery failure comes to rest, for
    bounces and complaints alike, which is why this is a question about the
    ADDRESS rather than a stream of events to subscribe to and store.

    IT FAILS OPEN, AND THAT DIRECTION IS DELIBERATE. Any error other than "not
    on the list" — no `ses:GetSuppressedDestination` grant, a throttle, a region
    that cannot be reached — returns None and lets the send proceed. A check
    that cannot answer must never be the reason a student's link is not sent;
    the cost of being wrong here is one undeliverable message, and the cost of
    failing closed is every message. It is logged where it happens, without the
    address. `suppression_probe` is the same question for a caller that needs
    the two Nones told apart.
    """
    return _ask_provider(to)[1]


def suppression_probe(to: str) -> tuple[bool, bool]:
    """(checked, suppressed), for the office screen. Never fails open."""
    checked, found = _ask_provider(to)
    return checked, found is not None


def lift_suppression(to: str) -> bool:
    """Remove this address from the account suppression list. True on success.

    The remedy, and the reason the office screen is not just a read: a
    suppressed address stays suppressed until somebody deletes it, and the only
    other door to that is the AWS console, which the placement office does not
    have and should not need for a student whose mailbox is now working.
    """
    import boto3

    try:
        boto3.client("sesv2", region_name=_region()).delete_suppressed_destination(
            EmailAddress=to
        )
        return True
    except Exception as exc:
        # Deliberately without the address, like every other line here.
        log.error("Could not lift an SES suppression: %s", exc)
        return False


def send(to: str, subject: str, text: str) -> None:
    """Deliver one plain-text message, or record it if there is no transport.

    Raises on an SES failure so `mailer.deliver_once` records FAILED — that
    row is how an operator learns a student never got their link.

    IT ASKS WHETHER THE ADDRESS IS DELIVERABLE BEFORE IT SENDS. See
    `suppression_for`: without this, a suppressed address is the one failure in
    this module that looks exactly like success, on every path a student has.
    """
    if not configured():
        if not settings.env_is_dev:
            # OUTSIDE DEV AND CI THERE IS NO CONSOLE TRANSPORT (2026-10-13). The
            # body is an onboarding, activation or reset link, each a bearer
            # token, and on a deployed host this log line is CloudWatch. And a
            # normal return here is recorded SENT by `deliver_once` for a
            # message that reached nobody. So: say so without the address or
            # the body, and raise, which `deliver_once` records as FAILED.
            log.warning("MAIL NOT SENT: no transport configured (SES_FROM_ADDRESS blank) subject=%r", subject)
            raise RuntimeError("no mail transport is configured on this environment")
        outbox.append(OutboxEntry(to=to, subject=subject, text=text))
        # One line: a multi-line entry is several events to every log reader.
        log.info("MAIL (no transport configured) to=%s subject=%r body=%r", to, subject, text)
        return

    # Imported here, not at module scope: boto3 is on every install (the resume
    # builder needs it) but nothing else in this module does, and the console
    # path above must stay importable on a machine with no AWS credentials.
    import boto3

    client = boto3.client("sesv2", region_name=_region())
    request: dict[str, Any] = {
        "FromEmailAddress": settings.ses_from_address.strip(),
        "Destination": {"ToAddresses": [to]},
        "Content": {
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": text, "Charset": "UTF-8"}},
            }
        },
    }
    # Omitted when blank rather than sent as "": SES reads an empty
    # ConfigurationSetName as a set that does not exist and refuses the send,
    # which would turn "this deployment does not name one" into a hard mail
    # outage on every dev machine. Blank means defer to the identity's default.
    configuration_set = settings.ses_configuration_set.strip()
    if configuration_set:
        request["ConfigurationSetName"] = configuration_set

    # LAST, immediately before the send: a suppressed address is refused here
    # rather than accepted by SES and dropped. `deliver_once` turns this into a
    # FAILED row, the `error` the office screen prints, and the CloudWatch line
    # the mail alarm already watches for.
    suppressed = suppression_for(to)
    if suppressed is not None:
        raise SuppressedRecipient(
            f"the provider is suppressing this address ({suppressed.reason} on "
            f"{suppressed.since}) and will deliver nothing to it until it is lifted"
        )
    client.send_email(**request)
