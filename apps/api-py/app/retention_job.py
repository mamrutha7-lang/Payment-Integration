"""The retention reaper's entrypoint: ``python -m app.retention_job``, run from
deployment on a daily clock (the ``retention`` sidecar in
docker-compose.prod.yml).

This file exists so app/retention.py can keep the position its own docstring
stakes out: ``purge_expired`` and ``redact_expired_runs`` "delete student data
on a clock, which is a deployment decision, and running them from every boot
would make the amount of data destroyed a function of how often someone
restarts the API." The TRIGGER therefore lives in deployment, where a human
chose it and can see it — and this module is only the callable seam between
that trigger and the two jobs. It decides nothing: no schedule, no windows, no
policy. Change WHEN retention runs in the compose file; change WHAT it does in
app/retention.py; change neither here.

(The third job in that module, ``finalize_orphaned_interviews``, is already
wired — app/main.py runs it at every boot, because a worker that just restarted
is exactly the process that knows the previous one died. It is deliberately
NOT repeated here.)

Exit code 0 means both jobs ran to completion. Non-zero means at least one did
not, so whatever supervises this — the compose loop today, a cron with alerting
tomorrow — can tell a quiet day from a broken one without parsing prose.

The "cron with alerting" is Sentry: this process reports to the
reep-scheduled-jobs project through its own DSN (SENTRY_JOBS_DSN, never the
api's), and every run is a check-in pair on the `reep-retention-daily` monitor
— MISSED when the schedule did not fire, TIMEOUT when it ran too long, ERROR
when it raised or held rows back. A run that completes while a student's
recording could not be destroyed is reported as ERROR and still exits 0, so
the monitor and the exit code each keep their own promise.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import retention
from .config import settings
from .db import SessionLocal
from .observability import SERVICE_JOBS, flush, init_sentry, job_run

log = logging.getLogger("reep.reap")

#: The Sentry cron monitor for this job. Sending monitor_config with the
#: check-in UPSERTS the monitor, so this dict is the source of truth for the
#: schedule rather than a console form somebody edited once. It MUST equal the
#: EventBridge schedule in infra/cdk/reep_core/stack.py — cron(30 21 * * ? *),
#: UTC, flexible window OFF — because two clocks for one job is a monitor that
#: reports MISSED every night against a job that ran fine. Both durations are
#: MINUTES: 15 late before MISSED absorbs a cold Fargate image pull; 30 before
#: TIMEOUT bounds a first run against a neglected backlog (purge_expired does
#: the whole backlog in one transaction — if 30 is genuinely exceeded, add the
#: batching that function's caller already describes, do not raise this).
#: The job runs once a day, so a second chance is a lost day: threshold 1.
MONITOR_SLUG = "reep-retention-daily"
MONITOR_CONFIG = {
    "schedule": {"type": "crontab", "value": "30 21 * * *"},
    "timezone": "Etc/UTC",
    "checkin_margin": 15,
    "max_runtime": 30,
    "failure_issue_threshold": 1,
    "recovery_threshold": 1,
}


def main() -> int:
    # basicConfig, not print: retention reports through `logging`, and the line
    # an operator most needs to see — _delete_interview_audio's ERROR about a
    # recording it could not destroy — must reach the container's stdout, which
    # is this process's entire observability story. An unconfigured root logger
    # would drop everything below WARNING on the floor, including the summary
    # below that says the sweep ran at all.
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    # Its OWN project and its OWN DSN. This process runs on the api's task
    # definition and has SENTRY_DSN in its environment; reading that would file
    # a nightly sweep under the api's issues, where nobody looks for one. Blank
    # means off, and the line it logs says so.
    init_sentry(SERVICE_JOBS, settings.sentry_jobs_dsn)

    # ONE clock for the whole run. Both jobs are pure functions of `now` (their
    # module says so, and its tests pin it), and handing them the same instant
    # means a record cannot be due for one job and not-yet-due for the other
    # inside a single sweep.
    now = datetime.now(timezone.utc)

    try:
        # job_run: a transaction named for the job, the cron check-in pair, the
        # exception captured, and a flush before this process can exit. Inert
        # without a DSN. The exit codes below are unchanged by it.
        with job_run("retention.sweep", monitor_slug=MONITOR_SLUG, monitor_config=MONITOR_CONFIG) as run:
            with SessionLocal() as db:
                # purge_expired takes no batching/limit parameter today, so the
                # first run against a long-neglected backlog does all of it in
                # one transaction. Known follow-up: if that first sweep proves
                # too heavy, add a limit to purge_expired itself and drain it in
                # a loop HERE — do not grow a second retention implementation
                # in this file.
                summary = retention.purge_expired(db, now=now)
                log.info(
                    "Retention sweep complete. Conversations: %d soft-deleted "
                    "(%d message(s) PII-scrubbed), %d hard-deleted (%d message(s) "
                    "destroyed). Interviews: %d soft-deleted (%d turn(s) and %d "
                    "report(s) PII-scrubbed), %d hard-deleted (%d turn(s) "
                    "destroyed), %d audio store(s) destroyed.",
                    summary["soft_deleted"],
                    summary["messages_redacted"],
                    summary["hard_deleted"],
                    summary["messages_deleted"],
                    summary["interviews_soft_deleted"],
                    summary["interview_turns_redacted"],
                    summary["interview_reports_redacted"],
                    summary["interviews_hard_deleted"],
                    summary["interview_turns_deleted"],
                    summary["interview_audio_deleted"],
                )
                # Non-zero here means retention did NOT complete — a named
                # student's recording is still on disk and its row was held
                # back from hard-delete so the file stays discoverable (see
                # retention._delete_interview_audio, which has already logged
                # each blocked session id). Said again at summary level because
                # the per-row lines scroll and this one number is the
                # pager-worthy fact.
                if summary["interviews_hard_delete_blocked"]:
                    log.error(
                        "%d interview session(s) were due for hard-delete and were "
                        "HELD BACK because their stored audio could not be "
                        "destroyed. Retention is incomplete until someone looks.",
                        summary["interviews_hard_delete_blocked"],
                    )
                    # The sweep COMPLETED and still did not do its job: the
                    # monitor says ERROR. The exit code keeps the promise the
                    # docstring makes — "both jobs ran to completion" — so
                    # whatever supervises this sees the same code it always has.
                    run.mark_error("interviews_hard_delete_blocked")

                redacted = retention.redact_expired_runs(db, now=now)
                log.info(
                    "Redacted the free text of %d aged AgentRun row(s); their "
                    "metrics are kept.",
                    redacted,
                )
    except Exception:
        # The sidecar's loop keeps going (tomorrow's run may succeed), but the
        # failure must be loud and the exit code honest — a retention job that
        # fails silently is a 180-day promise quietly not being kept. job_run
        # has already captured this exception and reported the check-in as
        # ERROR; this line is the operator's copy on stdout.
        log.exception("Retention run FAILED; nothing further was swept.")
        return 1
    finally:
        # A short-lived process that exits with events still queued loses them
        # — the failure it was reporting included. job_run flushed once; this
        # covers the log.exception above.
        flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
