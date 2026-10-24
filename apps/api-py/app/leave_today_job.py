"""The morning leave announcement: ``python -m app.leave_today_job``.

Every approved faculty leave that covers TODAY -- the programme's today, in
``PROGRAMME_TIMEZONE``, not the container's UTC one -- is mailed to every other
faculty member as "<name> is on leave today". This is the one place that mail is
sent from on an ordinary day, and that is the point of it (2026-09-24): the
office's owner asked that colleagues be told on the leave DAY, not at the moment
the office presses Sanction, which may be a week earlier. The only other sender
is the approval itself, and only when it lands on a day the leave already
covers (``routers/leave.py::decide_leave``). WHAT the mail says, and who gets it,
is ``app/leave_mail.py``; this file is only the seam between the schedule and
that module, ``retention_job``'s rule.

It runs at 07:00 IST (``cron(30 1 * * ? *)`` in ``infra/cdk/reep_core/stack.py``),
before the working day, so the announcement is in the inbox before the first
class a colleague might have to cover.

A RERUN IS SAFE. Every mail is keyed ``leave-today:{id}:{day}:{recipient}``
through ``mailer.deliver_once``, so running this twice on one day -- by hand
after a crashed run, or after a same-day approval already announced one leave --
sends nothing twice. It also RETRIES nothing: a FAILED row holds its key like a
SENT one does, which is ``deliver_once``'s contract everywhere.

It is inert where leave mail is off (``LEAVE_MAIL_ENABLED``, false by default
for ``leave_mail.py``'s reason) and says so, rather than reporting an empty day.

Like every scheduled job it reports as ``reep-scheduled-jobs`` through
``SENTRY_JOBS_DSN`` and never the api's DSN, which is in its environment only
because it runs on the api's task definition with a command override.
"""

from __future__ import annotations

import logging

from . import leave_mail
from .clock import local_today
from .config import settings
from .db import SessionLocal
from .observability import SERVICE_JOBS, flush, init_sentry, job_run

log = logging.getLogger("reep.leave_today")


def main() -> int:
    # basicConfig, not print, for retention_job's reason: an unconfigured root
    # logger sits at WARNING and would drop the summary line below.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    init_sentry(SERVICE_JOBS, settings.sentry_jobs_dsn)

    day = local_today()
    try:
        with job_run("leave.on_leave_today"):
            with SessionLocal() as db:
                summary = leave_mail.announce_faculty_on_leave(db, day=day)
            if not summary.enabled:
                log.info("Leave mail is off (LEAVE_MAIL_ENABLED); nothing announced for %s.", day)
            else:
                log.info(
                    "On-leave announcement for %s: %d faculty leave(s) today, %d mail(s) on "
                    "record, %d failed.",
                    day,
                    summary.leaves,
                    summary.mailed,
                    summary.failed,
                )
                if summary.failed:
                    log.error(
                        "%d on-leave mail(s) FAILED for %s; the Email delivery screen "
                        "has each one and why.",
                        summary.failed,
                        day,
                    )
    except Exception:
        log.exception("On-leave announcement FAILED for %s.", day)
        return 1
    finally:
        flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
