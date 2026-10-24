"""The alert engine's entrypoint: ``python -m app.alerts_job`` (B8.3).

The twin of ``app/retention_job.py``, and written to the same rule: this file is
only the callable seam between a trigger and a job. It decides nothing — no
schedule, no thresholds, no rules. Change WHAT an alert is in ``app/alerts.py``;
change WHICH batches have which thresholds in the console (``PUT
/api/admin/alert-rules``, which is why `alert_rule_configs` exists at all);
change WHEN this runs in deployment.

Exit code 0 means the sweep ran to completion. Non-zero means it did not, so
whatever supervises this can tell a quiet night from a broken one without
parsing prose.

THERE IS NO SENTRY CRON MONITOR ON THIS JOB YET, AND ITS ABSENCE IS DELIBERATE.
``retention_job`` carries ``MONITOR_SLUG``/``MONITOR_CONFIG`` because an
EventBridge schedule exists to match it, and ``tests/test_codebase_guards.py::
test_the_retention_monitor_keeps_the_same_clock_as_the_scheduler`` compares the
two so they cannot drift. No schedule exists for this job: adding one touches
``infra/cdk/reep_core/stack.py``, i.e. ``reep-core``, which needs the owner's
explicit go. A monitor shipped ahead of its scheduler would report MISSED every
single night against a job that is not supposed to have run, which is how a
Sentry project becomes noise nobody reads.

WHEN THE SCHEDULE IS ADDED, three things land in ONE commit: a second
``scheduler.CfnSchedule`` in that stack reusing the existing ``SchedulerRole``
(its ``ecs:RunTask`` resource is already the whole ``reep-api`` family, so no new
IAM role is needed) with ``command: ["python", "-m", "app.alerts_job"]``; the
``MONITOR_SLUG``/``MONITOR_CONFIG`` pair here and the ``monitor_slug=`` argument
below; and the twin of that clock guard. The board (``02-admin-console-spec.md``
§2) says "Alerts · nightly 02:00", and 02:00 IST is **20:30 UTC** —
``cron(30 20 * * ? *)``, one hour before the retention sweep at 21:30 UTC so the
two do not contend for the same Fargate capacity. Sentry is still initialised
below regardless, so an exception raised tonight is reported whether or not a
scheduler is what invoked this.

IT REPORTS AS ``reep-scheduled-jobs``, THROUGH ``SENTRY_JOBS_DSN``, never the
api's. This process runs on the api's task definition with ``SENTRY_DSN`` in its
environment; reading that would file a nightly sweep under the api's issues,
where nobody looks for one — the reasoning ``retention_job`` already carries and
``tests/test_codebase_guards.py::
test_every_reporting_process_names_its_own_service_and_its_own_dsn`` enforces.
Two scheduled jobs sharing one Sentry PROJECT is right; they are distinguished
by the ``job.name`` tag, not by a second DSN.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import alerts
from .config import settings
from .db import SessionLocal
from .observability import SERVICE_JOBS, flush, init_sentry, job_run

log = logging.getLogger("reep.alerts")


def main() -> int:
    # basicConfig, not print, for retention_job's reason: this module's whole
    # observability story on a container is stdout, and an unconfigured root
    # logger sits at WARNING and drops the summary line below on the floor —
    # along with every "param is not a number" warning app/alerts.py emits when
    # a threshold has been typed wrong in the console.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    init_sentry(SERVICE_JOBS, settings.sentry_jobs_dsn)

    # ONE clock for the whole sweep, handed in. `evaluate_alerts` is a pure
    # function of `now` (its docstring says so and its tests pin it), and a
    # single instant means a rule cannot fire against one student at 23:59:59
    # and be suppressed as "already raised today" for the next at 00:00:01.
    now = datetime.now(timezone.utc)

    try:
        with job_run("alerts.sweep"):
            with SessionLocal() as db:
                summary = alerts.evaluate_alerts(db, now=now)
            log.info(
                "Alert sweep complete. %d rule(s) evaluated, %d skipped "
                "(batch graduated, archived or gone). %d finding(s) produced: "
                "%d alert(s) written, %d suppressed as already raised.",
                summary["rules_evaluated"],
                summary["rules_skipped"],
                summary["findings"],
                summary["alerts_written"],
                summary["already_raised"],
            )
            # A sweep that evaluated NOTHING is not an error and not a success
            # worth being quiet about: it means no `alert_rule_configs` row is
            # enabled anywhere, so the mentor feed will stay empty whatever
            # happens to the data. That is a configuration fact the office can
            # fix on the Rules dialog, and saying it here is the only place it
            # is ever said. NOT an error check-in: a deployment that has
            # configured no rules is under-configured, not broken, and paging
            # somebody nightly over it is how the monitor gets muted.
            if summary["rules_evaluated"] == 0:
                log.warning(
                    "No enabled alert rule was evaluated. Until a rule is "
                    "configured for a batch (PUT /api/admin/alert-rules), the "
                    "mentor alert feed stays empty however the data moves."
                )
    except Exception:
        # Loud, and the exit code honest. job_run has already captured this
        # exception; this line is the operator's copy on stdout.
        log.exception("Alert sweep FAILED; no alerts were written.")
        return 1
    finally:
        # A short-lived process that exits with events still queued loses them,
        # the failure it was reporting included.
        flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
