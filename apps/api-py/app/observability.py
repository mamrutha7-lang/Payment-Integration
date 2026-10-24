"""One Sentry initialisation for every REEP process, and the policy around it.

Three processes report to Sentry, each to its own project, each through THIS
function and no other:

    reep-api                 app/main.py           SENTRY_DSN
    reep-scheduled-jobs      app/retention_job.py  SENTRY_JOBS_DSN
    reep-interview-worker    app/voice_platform/queue/worker.py
                                                   SENTRY_INTERVIEW_WORKER_DSN

A process names its service once, at import or at the top of main(), and the
DSN it hands over is the one for that project. A blank DSN is OFF: nothing is
initialised, every sentry_sdk call downstream is a documented no-op, and the
process says so in one log line. Nothing here falls back to another service's
DSN — the retention job runs on the api's task definition and could read
SENTRY_DSN, and must not, because a nightly sweep filed under the api's project
is a job nobody watches.

Three constructor flags are rule 1 applied to telemetry and are pinned by
tests/test_codebase_guards.py, which reads THIS file as text: send_default_pii,
include_local_variables and max_request_body_size. The hooks below are the
layer that still holds if a flag is ever dropped; app/telemetry_scrub.py owns
them and is tested without a DSN.

What is deliberately NOT here: any student text, in any span, tag or message —
`tests/test_tracing.py` pins that for the helpers in app/tracing.py, and the
scrubbers here are the backstop for the path nobody has written yet.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import sentry_sdk
from sentry_sdk.crons import capture_checkin
from sentry_sdk.crons.consts import MonitorStatus

from . import telemetry_scrub as scrub
from . import tracing
from .config import settings

log = logging.getLogger("reep.observability")

APPLICATION = "reep"
SERVICE_API = "reep-api"
SERVICE_JOBS = "reep-scheduled-jobs"
SERVICE_INTERVIEW_WORKER = "reep-interview-worker"
SERVICES = frozenset({SERVICE_API, SERVICE_JOBS, SERVICE_INTERVIEW_WORKER})

# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #

#: Checked FIRST, and the order is load-bearing: "/api/interview" is an
#: always-traced prefix and "/api/interview/status" is a poll the client makes
#: before every Start press.
NEVER_TRACED = frozenset(
    {
        # The ALB probes /ready every 15 s from each AZ node against every task.
        # app/traceability.py already carves /health out of the access log for
        # the same reason; that argument stopped at the log line.
        "/health",
        "/ready",
        # The login screen probes this on every render, unauthenticated.
        "/api/auth/sso/status",
        "/api/auth/google/status",
        "/api/interview/status",
    }
)

#: 1.0, because these are the paths where a failure is a person locked out, a
#: record lost, or a file that never arrived — and they are all low-volume.
ALWAYS_TRACED = frozenset(
    {
        "/api/auth/login",
        "/api/auth/login/code",
        "/api/auth/activate",
        "/api/auth/forgot",
        "/api/auth/reset",
        "/api/auth/change-password",
        "/api/student/uploads",
        "/api/staff/upskilling",
        "/api/alumni/profile",
        "/api/platform/admin/candidates/bulk",
    }
)
ALWAYS_TRACED_PREFIX = (
    "/api/interview",  # the socket; /status is in NEVER_TRACED
    "/ws/media-bridge",
    "/api/platform/media-bridge",
    "/api/register",
    "/api/auth/sso/google",
    "/api/auth/google/",
)
# NOT in either list, deliberately: /api/auth/me. The auth guard short-circuits
# on the cached signal, so it is reached once per hard page load across every
# signed-in user — the wrong thing to pin at 1.0 because the word "auth" is in it.


def traces_sampler(sampling_context: dict[str, Any]) -> float:
    """The per-transaction sample decision. Total and pure: a sampler that
    raises does not fail a request (the SDK falls back to traces_sample_rate),
    but a sampler that raises is still a sampler nobody tested.

    Inside here the transaction NAME is the raw URL, not the route template —
    the ASGI middleware runs before routing — so paths are read from
    `asgi_scope`, never from `transaction_context["name"]`.
    """
    try:
        path = (sampling_context.get("asgi_scope") or {}).get("path") or ""
        # app/main.py mounts the auth router a second time under /api/v1.
        # Normalise so one endpoint has one sampling decision.
        if path.startswith("/api/v1/"):
            path = "/api" + path[len("/api/v1") :]

        if path in NEVER_TRACED:
            return 0.0
        if path in ALWAYS_TRACED or path.startswith(ALWAYS_TRACED_PREFIX):
            return 1.0

        # The interview's own transaction and the platform's are started by
        # hand, so they arrive with no asgi_scope and an empty path. They are
        # the whole reason this instrumentation exists; keep every one. A
        # scheduled job's transaction is op="task" for the same reason.
        op = (sampling_context.get("transaction_context") or {}).get("op")
        if op in ("websocket.server", "task", "queue.process"):
            return 1.0

        # Respect the browser. With a traces_sampler the parent decision is
        # NOT inherited automatically — honouring it is something you write
        # down or lose. Everything above deliberately overrules it.
        parent = sampling_context.get("parent_sampled")
        if parent is not None:
            return 1.0 if parent else 0.0

        return settings.sentry_traces_rate
    except Exception:  # noqa: BLE001 - never let observability raise
        return settings.sentry_traces_rate


# --------------------------------------------------------------------------- #
# Hooks
# --------------------------------------------------------------------------- #

#: Matched against record.msg — the TEMPLATE, never record.getMessage(). The
#: formatted message is where the argument values are, and reading them here
#: to decide whether to drop an event is exactly the leak this exists to stop.
#:
#: `in`, NOT `startswith`: app/interview_nova.py logs through a LoggerAdapter
#: whose process() prefixes "[conn=… session=…] ", so the template Sentry
#: receives is prefixed and a startswith filter is false on every event.
SILENCED_TEMPLATES = (
    # app/interview_nova.py, inside a bare `except Exception` whose entire
    # design is that a dropped turn never ends a call. One database blip is one
    # event per turn for the rest of every concurrent interview. It is already
    # detected: the reep-interview-dropped-turns CloudWatch alarm is a metric
    # filter on this literal string, and interview_sessions carries
    # turns_emitted vs turns_persisted. Sentry would add a third copy and no
    # new fact.
    "Dropped interview turn:",
    # app/routers/auth.py logs ERROR on every render of the login screen when
    # ENV=prod and Google is unconfigured — a static configuration state,
    # already reported in the endpoint's own 200 body and in the boot log.
    "GET /api/auth/sso/status -> 200 unavailable",
    # AWSCRT'S OWN TEARDOWN, ARRIVING ON THE `asyncio` LOGGER (Sentry REEP-API-2,
    # 30 events in 6 days and escalating, 0 users impacted).
    #
    # When a Nova bidirectional stream closes, awscrt's internal request-body
    # task makes a final `_write_data(None, True)` on a stream the service has
    # already completed, and raises 2080 AWS_ERROR_HTTP_STREAM_HAS_COMPLETED.
    # Nobody awaits that task — it belongs to awscrt, not to this codebase — so
    # CPython's `Task.__del__` hands it to the loop's default exception handler,
    # which logs it at ERROR on the `asyncio` logger, and LoggingIntegration
    # turns every ERROR into an event. `interview_nova.aclose` already swallows
    # every teardown failure it can actually see; this one is raised inside the
    # SDK, after the last line this process runs.
    #
    # It is not a defect and it is not silence. The CloudWatch line stays
    # exactly as it is, so the record survives where an operator reads it; what
    # is dropped is the ALERT, because a benign event arriving five times a day
    # is how the reep-api project becomes one nobody opens.
    #
    # A TUPLE, SO BOTH HALVES MUST BE PRESENT. Matching
    # `_set_request_body_generator` alone would also swallow a GENUINE failure
    # in that coroutine — a real mid-interview write error would name a
    # different awscrt code and must still report. And matching only the error
    # code would swallow it wherever it appeared. Neither fragment can carry a
    # student's words: the first is an SDK coroutine name and the second is a
    # constant from awscrt's error table.
    #
    # The culprit Sentry shows for these events (`/api/register/hierarchy`) is
    # not where they come from. `Task.__del__` runs on whatever thread and
    # transaction happened to be live, so the transaction is incidental — which
    # is itself a reason this cannot usefully be triaged from the issue.
    (
        "Task exception was never retrieved",
        "AWS_ERROR_HTTP_STREAM_HAS_COMPLETED",
    ),
)


def silenced(template: str | None) -> bool:
    """True when this log TEMPLATE names a known-benign event.

    An entry is either a fragment (drop when it appears) or a TUPLE of
    fragments (drop only when every one of them appears). The tuple form exists
    because the one event that needs it — awscrt's stream teardown above — is
    identified by a coroutine name AND an error code, and either alone would
    also silence a real failure.
    """
    if not isinstance(template, str):
        return False
    return any(
        all(part in template for part in entry) if isinstance(entry, tuple) else entry in template
        for entry in SILENCED_TEMPLATES
    )


def _before_send(event: dict[str, Any], hint: dict[str, Any], *, service: str) -> dict[str, Any] | None:
    """Error and message events. FAIL CLOSED: a scrubber that raises drops the
    event — better an unexplained gap than a student's words on a third party's
    servers. The SDK wraps this call too; the try is here so the reason is
    logged at WARNING (a breadcrumb, never itself an event) rather than lost."""
    try:
        record = hint.get("log_record") if isinstance(hint, dict) else None
        if record is not None and silenced(getattr(record, "msg", None)):
            return None
        scrubbed = scrub.scrub_event(event, hint)
        if scrubbed is None:
            return None
        return scrub.stamp_tags(scrubbed, service=service, application=APPLICATION)
    except Exception:  # noqa: BLE001
        log.warning("telemetry scrubber failed; the event was dropped", exc_info=True)
        return None


def _before_send_transaction(event: dict[str, Any], hint: dict[str, Any], *, service: str) -> dict[str, Any] | None:
    try:
        scrubbed = scrub.scrub_transaction(event, hint)
        if scrubbed is None:
            return None
        return scrub.stamp_tags(scrubbed, service=service, application=APPLICATION)
    except Exception:  # noqa: BLE001
        log.warning("telemetry scrubber failed; the transaction was dropped", exc_info=True)
        return None


def _before_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return scrub.scrub_breadcrumb(crumb, hint)
    except Exception:  # noqa: BLE001
        return None


def _before_send_log(record: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return scrub.scrub_log(record, hint)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Options and initialisation
# --------------------------------------------------------------------------- #


def build_options(service: str, dsn: str, *, environment: str | None = None, release: str | None = None) -> dict[str, Any]:
    """The complete `sentry_sdk.init` argument set for one process. Pure: a
    test can build it, hand it to a Client bound to a scope, and read every
    decision back without touching the global SDK state."""
    if service not in SERVICES:
        raise ValueError(f"unknown telemetry service {service!r}; one of {sorted(SERVICES)}")

    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    return dict(
        dsn=dsn.strip(),
        # Blank SENTRY_ENVIRONMENT means the environment IS `ENV` — one fact, one
        # source. Production tasks carry ENV=prod, so events are tagged `prod`;
        # a saved search or alert rule that says `production` matches nothing.
        environment=(environment if environment is not None else settings.sentry_environment_name),
        # The commit sha, baked into the image as SENTRY_RELEASE by the
        # Dockerfile's build arg. With no value the SDK's own fallback tries the
        # working tree's git revision, which a container never has — so a
        # blank stays blank rather than becoming a release nobody chose.
        release=(release if release is not None else (settings.sentry_release.strip() or None)),
        # STILL PASSED beside the sampler: when the sampler raises, the SDK
        # falls back to the parent decision and then to exactly this option.
        traces_sample_rate=settings.sentry_traces_rate,
        traces_sampler=traces_sampler,
        # Continuous profiling — the API that covers a whole session rather than
        # the legacy 30-second transaction profiler. Nothing is profiled while
        # this is 0.0, whatever profile_lifecycle says, so the default
        # deployment pays nothing.
        profile_session_sample_rate=settings.sentry_profile_rate,
        profile_lifecycle="trace",
        # RULE 1, THREE FLAGS, all pinned by tests/test_codebase_guards.py.
        #
        # send_default_pii=False keeps cookies (the reep_session token), the
        # client IP and user context off every event. SENTRY_SEND_DEFAULT_PII
        # is recognised so an operator who sets it gets a refusal in the log
        # rather than a silent no-op — and never a True here.
        send_default_pii=False,
        # include_local_variables DEFAULTS TO TRUE and attaches every stack
        # frame's locals to every captured exception. On this codebase those
        # locals are a student's words: `student_text` in the interview turn
        # writer, `raw` holding a scorecard, `payload` holding a Nova transcript
        # event. Demonstrated before this line was written: a RuntimeError in a
        # function whose local was "My CGPA is 8.7 and I was rejected by
        # Infosys last week" put that string verbatim into the event.
        include_local_variables=False,
        # POST /student/resume/generate carries a brief with a name, USN, marks
        # and attendance; the default "medium" attaches it to errors.
        max_request_body_size="never",
        # The fourth PII switch, and the one none of the other three covers: an
        # exception MESSAGE. This does not stop that leak — the scrubber
        # redacts the identifiers — but it bounds it.
        max_value_length=2048,
        before_send=partial(_before_send, service=service),
        before_send_transaction=partial(_before_send_transaction, service=service),
        before_breadcrumb=_before_breadcrumb,
        # Structured logs: OFF unless SENTRY_LOGS_ENABLED. stdout already
        # reaches CloudWatch, and logs bypass before_breadcrumb entirely — when
        # they are on, before_send_log is the scrubber for them.
        enable_logs=settings.sentry_logs_on,
        before_send_log=_before_send_log,
        integrations=[
            # Starlette and FastAPI are auto-enabling; listed to pin the two
            # defaults this codebase depends on: transaction names from the
            # route TEMPLATE (never a path with an id in it), and issues only
            # for 5xx — every 401/403/404/409/422/429 is a transaction and
            # never an issue, so no ignore list is needed for them.
            StarletteIntegration(transaction_style="url"),
            FastApiIntegration(transaction_style="url"),
            # A default integration, written down: INFO records become
            # breadcrumbs, ERROR records become issues — which is what makes
            # every log.exception in app/ a Sentry issue with no other change.
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            # NOT AsyncioIntegration. Its setup patches the RUNNING event loop,
            # and this init happens at import time, before uvicorn's loop
            # exists — so listing it would be a line that does nothing while
            # reading as if it did. The fire-and-forget interview tasks catch
            # their own exceptions and reach Sentry through log.exception.
        ],
        in_app_include=["app"],
    )


_STATE_LOCK = threading.Lock()
_INITIALISED: dict[str, str] = {}


def init_sentry(service: str, dsn: str, **overrides: Any) -> bool:
    """Initialise Sentry for `service`, exactly once per process.

    Returns True when the SDK is live for this process, False when it is off.
    Blank DSN: off, one INFO line, nothing else changes. Called twice with the
    same service: the second call is a no-op. Called with a DIFFERENT service
    in the same process: refused with an ERROR line, the first stays — one
    process is one service, and two clients in one process is how the
    interview ends up split across two projects.

    `overrides` are passed straight to sentry_sdk.init on top of build_options
    — the tests use `transport=` so no event ever leaves the machine.
    """
    if service not in SERVICES:
        raise ValueError(f"unknown telemetry service {service!r}; one of {sorted(SERVICES)}")
    with _STATE_LOCK:
        already = _INITIALISED.get("service")
        if already is not None:
            if already != service:
                log.error(
                    "telemetry: refusing to initialise Sentry for %s — this process already reports as %s",
                    service,
                    already,
                )
            return bool(_INITIALISED.get("dsn"))
        if not dsn.strip():
            log.info("telemetry: Sentry OFF for %s (no DSN configured for this service)", service)
            _INITIALISED["service"] = service
            _INITIALISED["dsn"] = ""
            return False
        if settings.sentry_pii_requested:
            log.error(
                "telemetry: SENTRY_SEND_DEFAULT_PII is set and REFUSED — cookies, IPs and user "
                "context never leave this process (rule 1, tests/test_codebase_guards.py)"
            )
        options = build_options(service, dsn)
        options.update(overrides)
        sentry_sdk.init(**options)
        scope = sentry_sdk.get_global_scope()
        scope.set_tag("service", service)
        scope.set_tag("application", APPLICATION)
        _INITIALISED["service"] = service
        _INITIALISED["dsn"] = "set"
        log.info(
            "telemetry: Sentry ON for %s (environment=%s release=%s traces=%s profiles=%s logs=%s)",
            service,
            options["environment"],
            options["release"] or "<unset>",
            settings.sentry_traces_rate,
            settings.sentry_profile_rate,
            settings.sentry_logs_on,
        )
        return True


def current_service() -> str | None:
    return _INITIALISED.get("service")


def _reset_for_tests() -> None:
    """Unbind the global client and forget the service. Tests only."""
    with _STATE_LOCK:
        try:
            sentry_sdk.flush(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
        sentry_sdk.get_global_scope().set_client(None)
        sentry_sdk.get_global_scope().clear()
        sentry_sdk.get_isolation_scope().clear()
        _INITIALISED.clear()


def enabled() -> bool:
    return tracing.enabled()


def flush(timeout: float = 5.0) -> None:
    """Drain the transport. A short-lived process that exits without this loses
    its last events — the failure it was reporting included."""
    if not enabled():
        return
    try:
        sentry_sdk.flush(timeout=timeout)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #


@dataclass
class JobRun:
    """What one job invocation reports: a name, an outcome, and how long it
    took. `mark_error` is for a run that COMPLETED without raising and still
    did not do its job — a retention sweep that held rows back, a drain cycle
    that stored nothing it received — so the monitor says ERROR and the exit
    code can stay whatever the entrypoint already promised."""

    name: str
    outcome: str = "ok"
    reason: str | None = None
    started: float = field(default_factory=time.monotonic)
    check_in_id: str | None = None

    def mark_error(self, reason: str | None = None) -> None:
        self.outcome = "error"
        self.reason = reason

    @property
    def duration_s(self) -> float:
        return time.monotonic() - self.started


@contextmanager
def job_run(
    name: str,
    *,
    monitor_slug: str | None = None,
    monitor_config: dict[str, Any] | None = None,
    **tags: Any,
) -> Iterator[JobRun]:
    """Wrap one job entry point: a transaction named for the job, low-cardinality
    tags, an optional cron check-in pair, the exception captured, and a flush
    before the process can exit.

    Inert without a DSN — the body runs, nothing else happens. An exception
    propagates unchanged, so the caller's exit code and its own log.exception
    keep working; the SDK's dedupe collapses that second report of the same
    exception object into the first.

    `tags` must be low-cardinality: a job name, a queue, a degree level. Never
    a student, an interview, a file or a message id — those go in span data
    or the log line, where they are searchable but do not index.
    """
    run = JobRun(name)
    if not enabled():
        yield run
        return

    if monitor_slug:
        try:
            run.check_in_id = capture_checkin(
                monitor_slug=monitor_slug,
                status=MonitorStatus.IN_PROGRESS,
                monitor_config=monitor_config,
            )
        except Exception:  # noqa: BLE001
            run.check_in_id = None

    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("job.name", name)
            for key, value in tags.items():
                scope.set_tag(key, str(value))
            with tracing.transaction(name, op="task", **{"job.name": name, **tags}) as tx:
                try:
                    yield run
                except BaseException as exc:
                    run.outcome = "error"
                    run.reason = type(exc).__name__
                    if isinstance(exc, Exception):
                        sentry_sdk.capture_exception(exc)
                    raise
                finally:
                    if tx is not None:
                        try:
                            tx.set_tag("job.outcome", run.outcome)
                            tx.set_data("job.duration_s", round(run.duration_s, 3))
                            if run.reason:
                                tx.set_tag("job.reason", run.reason[:64])
                        except Exception:  # noqa: BLE001
                            pass
    finally:
        if monitor_slug and run.check_in_id:
            try:
                capture_checkin(
                    monitor_slug=monitor_slug,
                    check_in_id=run.check_in_id,
                    status=MonitorStatus.OK if run.outcome == "ok" else MonitorStatus.ERROR,
                    duration=run.duration_s,
                )
            except Exception:  # noqa: BLE001
                pass
        flush()


__all__ = [
    "APPLICATION",
    "SERVICE_API",
    "SERVICE_INTERVIEW_WORKER",
    "SERVICE_JOBS",
    "SERVICES",
    "JobRun",
    "build_options",
    "current_service",
    "enabled",
    "flush",
    "init_sentry",
    "job_run",
    "silenced",
    "traces_sampler",
]
