"""Sentry is a second HTTP transport out of this process, carrying the same
student text as the first, and no `carries_student_data=True` gate guards it.

These tests are the privacy boundary for telemetry, executable: what leaves,
what is stripped, and that the SDK is OFF when nothing configures it, ONE per
process when something does, and never able to fail a request or a job. Every
test binds a capturing transport, so nothing here ever reaches the network —
the DSN below is the SDK's own documentation placeholder and resolves nowhere.

The incident behind the file: `sentry_sdk.init` set send_default_pii=False
and stopped, and every captured exception shipped every stack frame's locals —
which on this codebase are a student speaking.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
import sentry_sdk
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from app import observability as obs
from app import telemetry_scrub as scrub
from app import tracing
from app.config import settings

FAKE_DSN = "https://abc123@o0.ingest.sentry.io/0"
STUDENT_SAID = "My CGPA is 8.7 and I was rejected by Infosys last week"
EMAIL = "1mp25mdm01@bgscet.ac.in"
USN = "1MP25MDM01"
TOKEN = "deadbeefcafe0123"
COOKIE = "reep_session=eyJhbGciOiJIUzI1NiJ9.secret"
BEARER = "Bearer sk-live-should-never-travel"


class CapturingTransport(Transport):
    """Keeps every envelope in memory. Nothing leaves the machine."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.envelopes: list[Any] = []

    def capture_envelope(self, envelope: Any) -> None:
        self.envelopes.append(envelope)

    def flush(self, timeout: float, callback: Any = None) -> None:
        if callback is not None:
            callback(0, timeout)

    def kill(self) -> None:
        return None

    def items(self, item_type: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for envelope in self.envelopes:
            for item in envelope.items:
                if item.type == item_type:
                    out.append(item.payload.json)
        return out

    def clear(self) -> None:
        self.envelopes.clear()


@pytest.fixture
def live():
    """Sentry initialised for one service with a capturing transport, and torn
    down again so the rest of the suite sees an uninitialised SDK — the inert
    contract tests/test_tracing.py pins."""

    def _start(service: str = obs.SERVICE_API, **overrides: Any) -> CapturingTransport:
        obs._reset_for_tests()
        transport = CapturingTransport()
        assert obs.init_sentry(service, FAKE_DSN, transport=transport, **overrides) is True
        assert obs.enabled()
        return transport

    yield _start
    obs._reset_for_tests()
    assert not tracing.enabled()


def _blob(items: list[dict[str, Any]]) -> str:
    return json.dumps(items, default=str)


# --------------------------------------------------------------------------- #
# Off, once, and never twice
# --------------------------------------------------------------------------- #


def test_sentry_is_off_cleanly_when_no_dsn_is_configured(caplog) -> None:
    obs._reset_for_tests()
    try:
        with caplog.at_level(logging.INFO, logger="reep.observability"):
            assert obs.init_sentry(obs.SERVICE_JOBS, "   ") is False
        assert "Sentry OFF for reep-scheduled-jobs" in caplog.text
        assert not obs.enabled()
        assert obs.current_service() == obs.SERVICE_JOBS
        # Every helper is inert, and the job body still runs.
        ran = False
        with obs.job_run("nothing", monitor_slug="never-sent") as run:
            ran = True
            tracing.annotate(count=1)
        assert ran and run.outcome == "ok" and run.check_in_id is None
        obs.flush()
    finally:
        obs._reset_for_tests()


def test_sentry_initialises_exactly_once_and_refuses_a_second_service(live, caplog) -> None:
    live(obs.SERVICE_API)
    client = sentry_sdk.get_client()
    assert obs.init_sentry(obs.SERVICE_API, FAKE_DSN) is True
    assert sentry_sdk.get_client() is client, "a second init for the same service replaced the client"
    with caplog.at_level(logging.ERROR, logger="reep.observability"):
        assert obs.init_sentry(obs.SERVICE_JOBS, FAKE_DSN) is True
    assert sentry_sdk.get_client() is client, "a second SERVICE was allowed to re-initialise the SDK"
    assert obs.current_service() == obs.SERVICE_API
    assert "refusing to initialise Sentry for reep-scheduled-jobs" in caplog.text


def test_an_unknown_service_is_refused_before_anything_is_initialised() -> None:
    obs._reset_for_tests()
    try:
        with pytest.raises(ValueError):
            obs.init_sentry("reep-something-else", FAKE_DSN)
        assert not obs.enabled()
    finally:
        obs._reset_for_tests()


# --------------------------------------------------------------------------- #
# What every event carries
# --------------------------------------------------------------------------- #


def test_every_event_carries_service_application_environment_and_release(live, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_environment", "test-env")
    monkeypatch.setattr(settings, "sentry_release", "0123456789abcdef0123456789abcdef01234567")
    transport = live(obs.SERVICE_API)
    sentry_sdk.capture_message("hello from the api")
    sentry_sdk.flush()
    events = transport.items("event")
    assert len(events) == 1
    event = events[0]
    assert event["tags"]["service"] == "reep-api"
    assert event["tags"]["application"] == "reep"
    assert event["environment"] == "test-env"
    assert event["release"] == "0123456789abcdef0123456789abcdef01234567"


def test_the_environment_follows_env_when_not_set_explicitly(live, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_environment", "")
    monkeypatch.setattr(settings, "env", "prod")
    transport = live(obs.SERVICE_JOBS)
    sentry_sdk.capture_message("nightly")
    sentry_sdk.flush()
    assert transport.items("event")[0]["environment"] == "prod"


def test_send_default_pii_is_refused_even_when_requested(live, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "sentry_send_default_pii", "true")
    with caplog.at_level(logging.ERROR, logger="reep.observability"):
        live(obs.SERVICE_API)
    assert sentry_sdk.get_client().options["send_default_pii"] is False
    assert sentry_sdk.get_client().options["include_local_variables"] is False
    assert sentry_sdk.get_client().options["max_request_body_size"] == "never"
    assert "REFUSED" in caplog.text


# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/health", 0.0),
        ("/ready", 0.0),
        ("/api/auth/sso/status", 0.0),
        ("/api/auth/google/status", 0.0),
        ("/api/interview/status", 0.0),
        ("/api/auth/login", 1.0),
        ("/api/v1/auth/login", 1.0),
        ("/api/auth/reset", 1.0),
        ("/api/interview", 1.0),
        ("/api/interview/anything", 1.0),
        ("/api/register/verify", 1.0),
        ("/api/student/uploads", 1.0),
        ("/api/student/jobs", 0.2),
        ("/api/auth/me", 0.2),
    ],
)
def test_the_sampler_drops_probes_and_keeps_the_paths_that_matter(monkeypatch, path: str, expected: float) -> None:
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", "0.2")
    ctx = {"asgi_scope": {"path": path}, "transaction_context": {"op": "http.server", "name": path}}
    assert obs.traces_sampler(ctx) == expected


def test_the_sampler_honours_the_browsers_decision_and_keeps_every_job(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", "0.2")
    ordinary = {"asgi_scope": {"path": "/api/student/jobs"}, "transaction_context": {"op": "http.server"}}
    assert obs.traces_sampler({**ordinary, "parent_sampled": True}) == 1.0
    assert obs.traces_sampler({**ordinary, "parent_sampled": False}) == 0.0
    # Hand-started transactions arrive with no asgi_scope at all.
    for op in ("websocket.server", "task", "queue.process"):
        assert obs.traces_sampler({"transaction_context": {"op": op}}) == 1.0


def test_the_sampler_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", "0.3")
    assert obs.traces_sampler({}) == pytest.approx(0.3)
    assert obs.traces_sampler({"asgi_scope": 5, "transaction_context": None}) == pytest.approx(0.3)


# --------------------------------------------------------------------------- #
# The hooks, called directly
# --------------------------------------------------------------------------- #


def _options() -> dict[str, Any]:
    return obs.build_options(obs.SERVICE_API, FAKE_DSN)


def test_before_send_strips_locals_bodies_cookies_headers_tokens_and_identifiers() -> None:
    event = {
        "request": {
            "url": "http://reep.example/api/register/verify",
            "query_string": f"token={TOKEN}&specialization=hr&board=cgpa",
            "cookies": {"reep_session": "secret"},
            "headers": {"Cookie": COOKIE, "Authorization": BEARER, "User-Agent": "ua/1.0"},
            "data": {"usn": USN, "marks": 87},
            "env": {"REMOTE_ADDR": "10.0.0.7"},
        },
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": f"could not parse the answer from {EMAIL}",
                    "stacktrace": {"frames": [{"function": "write", "vars": {"student_text": STUDENT_SAID}}]},
                }
            ]
        },
        "extra": {"sys.argv": ["x"], "api_key": "sk-abc", "note": f"student {USN} said hi"},
        "user": {"ip_address": "10.0.0.7"},
        "tags": {},
    }
    out = _options()["before_send"](event, {})
    assert out is not None
    blob = json.dumps(out)
    for secret in (STUDENT_SAID, TOKEN, COOKIE, BEARER, EMAIL, USN, "sk-abc", "10.0.0.7", "marks"):
        assert secret not in blob, f"{secret!r} left the process"
    assert "specialization=hr" in blob and "board=cgpa" in blob
    assert out["request"]["headers"]["User-Agent"] == "ua/1.0"
    assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["function"] == "write"
    assert out["tags"] == {"service": "reep-api", "application": "reep"}
    assert "user" not in out


def test_before_send_transaction_scrubs_the_request_block_a_sampled_200_carries() -> None:
    event = {
        "type": "transaction",
        "transaction": "/api/register/verify",
        "request": {"query_string": f"token={TOKEN}", "headers": {"cookie": COOKIE}},
        "spans": [{"op": "db", "description": "SELECT 1"}],
        "tags": {"request_id": "abc"},
    }
    out = _options()["before_send_transaction"](event, {})
    blob = json.dumps(out)
    assert TOKEN not in blob and COOKIE not in blob
    assert out["tags"]["service"] == "reep-api" and out["tags"]["request_id"] == "abc"
    assert out["spans"] == [{"op": "db", "description": "SELECT 1"}]


def test_the_silenced_templates_are_dropped_and_their_neighbours_are_not() -> None:
    class Record:
        def __init__(self, msg: str) -> None:
            self.msg = msg

    hook = _options()["before_send"]
    # Prefixed by the interview relay's LoggerAdapter, exactly as it arrives.
    assert hook({"message": "x"}, {"log_record": Record("[conn=abc session=def] Dropped interview turn: %s")}) is None
    assert hook({"message": "x"}, {"log_record": Record("GET /api/auth/sso/status -> 200 unavailable")}) is None
    # The relay's real crash report shares the logger and must survive.
    assert hook({"message": "x"}, {"log_record": Record("[conn=abc] Nova interview failed: %s")}) is not None


def test_awscrt_stream_teardown_is_dropped_but_a_real_task_failure_is_not() -> None:
    """Sentry REEP-API-2: 30 events in 6 days, escalating, nobody affected.

    A Nova stream closing makes awscrt's own request-body task raise on an
    already-completed HTTP/2 stream. Nothing in this codebase awaits that task,
    so CPython's Task.__del__ routes it to asyncio's default handler, which
    logs ERROR — and LoggingIntegration makes every ERROR an event.

    The three negatives below matter more than the positive: this drop is a
    TUPLE precisely so that a genuine failure in the same coroutine, the same
    error code somewhere else, and any other orphaned task in this application
    all keep reporting.
    """

    class Record:
        def __init__(self, msg: str) -> None:
            self.msg = msg

    hook = _options()["before_send"]

    # asyncio builds one string and passes no args, so record.msg is the whole
    # thing — reproduced here as CPython's default_exception_handler emits it.
    teardown = (
        "Task exception was never retrieved\n"
        "future: <Task finished name='Task-165538' "
        "coro=<AIOHttp2ClientStream._set_request_body_generator() done, defined at "
        "/usr/local/lib/python3.14/site-packages/awscrt/aio/http.py:527> "
        "exception=RuntimeError('2080 (AWS_ERROR_HTTP_STREAM_HAS_COMPLETED): "
        "HTTP-stream has completed, action cannot be performed.')>"
    )
    assert hook({"message": "x"}, {"log_record": Record(teardown)}) is None

    # A REAL failure in the very same awscrt coroutine names a different code.
    real_write_failure = (
        "Task exception was never retrieved\n"
        "future: <Task finished name='Task-9' "
        "coro=<AIOHttp2ClientStream._set_request_body_generator() done> "
        "exception=RuntimeError('2058 (AWS_ERROR_HTTP_CONNECTION_CLOSED)')>"
    )
    assert hook({"message": "x"}, {"log_record": Record(real_write_failure)}) is not None

    # The error code on its own, somewhere that is not an orphaned task.
    assert (
        hook({"message": "x"}, {"log_record": Record("AWS_ERROR_HTTP_STREAM_HAS_COMPLETED")})
        is not None
    )

    # An orphaned task of OUR OWN is the signal this silence must not cost.
    ours = (
        "Task exception was never retrieved\n"
        "future: <Task finished coro=<sweep_login_codes() done> "
        "exception=ValueError('boom')>"
    )
    assert hook({"message": "x"}, {"log_record": Record(ours)}) is not None


def test_a_silenced_entry_is_a_fragment_or_a_tuple_of_fragments() -> None:
    """The two forms of a SILENCED_TEMPLATES entry, pinned at the helper so the
    tuple form cannot be quietly read as an any-of and widen every drop."""
    from app.observability import silenced

    assert silenced(None) is False
    assert silenced(123) is False  # type: ignore[arg-type]
    # A tuple entry needs every part; a partial match must not drop.
    assert silenced("Task exception was never retrieved") is False
    assert silenced("AWS_ERROR_HTTP_STREAM_HAS_COMPLETED") is False
    assert silenced("Task exception was never retrieved AWS_ERROR_HTTP_STREAM_HAS_COMPLETED") is True


def test_a_scrubber_that_raises_drops_the_event_rather_than_shipping_it(monkeypatch) -> None:
    def explode(event: Any, hint: Any) -> Any:
        raise RuntimeError("scrubber bug")

    monkeypatch.setattr(scrub, "scrub_event", explode)
    monkeypatch.setattr(scrub, "scrub_transaction", explode)
    options = _options()
    assert options["before_send"]({"message": STUDENT_SAID}, {}) is None
    assert options["before_send_transaction"]({"type": "transaction"}, {}) is None


def test_the_breadcrumb_hook_redacts_and_mutes(monkeypatch) -> None:
    hook = _options()["before_breadcrumb"]
    assert hook({"category": "app.mail_transport", "message": f"MAIL to={EMAIL} /reset?token={TOKEN}"}, {}) is None
    crumb = hook(
        {
            "category": "app.routers.auth",
            "message": f"password reset for {EMAIL}",
            "data": {"url": f"/api/register/verify?token={TOKEN}", "api_key": "sk-1"},
        },
        {},
    )
    blob = json.dumps(crumb)
    assert EMAIL not in blob and TOKEN not in blob and "sk-1" not in blob

    def explode(crumb: Any, hint: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(scrub, "scrub_breadcrumb", explode)
    assert _options()["before_breadcrumb"]({"message": EMAIL}, {}) is None


def test_the_structured_log_hook_redacts_and_mutes() -> None:
    hook = _options()["before_send_log"]
    assert hook({"body": f"MAIL {TOKEN}", "attributes": {"logger.name": "app.mail_transport"}}, {}) is None
    out = hook({"body": f"reset for {EMAIL}", "attributes": {"logger.name": "app.routers.passwords", "token": TOKEN}}, {})
    blob = json.dumps(out)
    assert EMAIL not in blob and TOKEN not in blob


# --------------------------------------------------------------------------- #
# End to end through the FastAPI integration
# --------------------------------------------------------------------------- #


def _tiny_app() -> FastAPI:
    app = FastAPI()

    @app.get("/boom")
    def boom() -> dict[str, str]:
        student_text = STUDENT_SAID  # a live local in the failing frame
        raise RuntimeError(f"could not parse the answer from {EMAIL}: {student_text[:5]}")

    @app.get("/invalid")
    def invalid() -> dict[str, str]:
        raise HTTPException(status_code=422, detail="bad")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_an_unexpected_exception_is_captured_and_carries_no_secrets(live, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", "1.0")
    transport = live(obs.SERVICE_API)
    client = TestClient(_tiny_app(), raise_server_exceptions=False)

    response = client.get(
        f"/boom?token={TOKEN}&specialization=hr",
        headers={"Cookie": COOKIE, "Authorization": BEARER},
    )
    assert response.status_code == 500
    sentry_sdk.flush()

    events = transport.items("event")
    assert len(events) == 1, "an unexpected 500 must be exactly one issue"
    blob = _blob(events)
    for secret in (STUDENT_SAID, EMAIL, TOKEN, COOKIE, BEARER, "reep_session"):
        assert secret not in blob, f"{secret!r} reached the transport"
    assert "specialization=hr" in blob
    event = events[0]
    assert event["tags"]["service"] == "reep-api"
    assert event["exception"]["values"][0]["type"] == "RuntimeError"
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    assert frames and all("vars" not in frame for frame in frames)
    assert any(frame.get("function") == "boom" for frame in frames), "the stack trace itself must survive"


def test_expected_validation_errors_and_probes_are_not_incidents(live, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", "1.0")
    transport = live(obs.SERVICE_API)
    client = TestClient(_tiny_app(), raise_server_exceptions=False)

    assert client.get("/invalid").status_code == 422
    assert client.get("/health").status_code == 200
    sentry_sdk.flush()

    assert transport.items("event") == [], "a 422 and a probe must never become issues"
    names = [t["transaction"] for t in transport.items("transaction")]
    assert "/health" not in names, "the probe is sampled at 0.0"
    assert "/invalid" in names, "a 422 is still a traced request"


def test_telemetry_failures_never_fail_the_request(live, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", "1.0")

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("telemetry bug")

    transport = live(obs.SERVICE_API)
    monkeypatch.setattr(scrub, "scrub_event", explode)
    monkeypatch.setattr(scrub, "scrub_transaction", explode)
    monkeypatch.setattr(scrub, "scrub_breadcrumb", explode)
    client = TestClient(_tiny_app(), raise_server_exceptions=False)

    assert client.get("/boom").status_code == 500
    assert client.get("/health").status_code == 200
    sentry_sdk.flush()
    # Fail closed: the broken scrubber dropped everything, and the app served.
    assert transport.items("event") == []
    assert transport.items("transaction") == []


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #

_MONITOR = {"schedule": {"type": "crontab", "value": "30 21 * * *"}, "timezone": "Etc/UTC", "checkin_margin": 15}


def test_a_job_reports_a_check_in_pair_a_transaction_and_flushes(live) -> None:
    transport = live(obs.SERVICE_JOBS)
    with obs.job_run("retention.sweep", monitor_slug="reep-retention-daily", monitor_config=_MONITOR) as run:
        tracing.annotate(rows=3)
    assert run.outcome == "ok"

    checkins = transport.items("check_in")
    assert [c["status"] for c in checkins] == ["in_progress", "ok"]
    assert checkins[0]["monitor_slug"] == "reep-retention-daily"
    assert checkins[0]["monitor_config"] == _MONITOR
    assert checkins[1]["check_in_id"] == checkins[0]["check_in_id"]
    assert checkins[1]["duration"] >= 0

    transactions = transport.items("transaction")
    assert len(transactions) == 1
    tx = transactions[0]
    assert tx["transaction"] == "retention.sweep"
    assert tx["tags"]["job.name"] == "retention.sweep"
    assert tx["tags"]["job.outcome"] == "ok"
    assert tx["tags"]["service"] == "reep-scheduled-jobs"
    assert tx["contexts"]["trace"]["data"]["rows"] == 3


def test_a_job_that_completes_without_doing_its_job_reports_error(live) -> None:
    transport = live(obs.SERVICE_JOBS)
    with obs.job_run("retention.sweep", monitor_slug="reep-retention-daily") as run:
        run.mark_error("interviews_hard_delete_blocked")
    assert [c["status"] for c in transport.items("check_in")] == ["in_progress", "error"]
    tx = transport.items("transaction")[0]
    assert tx["tags"]["job.outcome"] == "error"
    assert tx["tags"]["job.reason"] == "interviews_hard_delete_blocked"


def test_a_job_that_raises_is_captured_once_and_still_raises(live) -> None:
    transport = live(obs.SERVICE_JOBS)
    log = logging.getLogger("reep.reap")
    try:
        with obs.job_run("retention.sweep", monitor_slug="reep-retention-daily"):
            raise RuntimeError(f"disk full while destroying audio for {USN}")
    except RuntimeError:
        # The entrypoint's own stdout copy. The SDK's dedupe collapses this
        # second report of the same exception object into the first.
        log.exception("Retention run FAILED; nothing further was swept.")
    sentry_sdk.flush()

    assert [c["status"] for c in transport.items("check_in")] == ["in_progress", "error"]
    events = transport.items("event")
    assert len(events) == 1, "one failure, one issue — not one per log line"
    assert USN not in _blob(events)
    assert events[0]["tags"]["service"] == "reep-scheduled-jobs"
    assert events[0]["tags"]["job.name"] == "retention.sweep"


def test_the_retention_job_keeps_its_exit_codes_and_reports_honestly(live, monkeypatch) -> None:
    from app import retention_job

    transport = live(obs.SERVICE_JOBS)

    class FakeSession:
        def __enter__(self) -> "FakeSession":
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

    summary = {
        key: 0
        for key in (
            "soft_deleted",
            "messages_redacted",
            "hard_deleted",
            "messages_deleted",
            "interviews_soft_deleted",
            "interview_turns_redacted",
            "interview_reports_redacted",
            "interviews_hard_deleted",
            "interview_turns_deleted",
            "interview_audio_deleted",
            "interviews_hard_delete_blocked",
        )
    }
    monkeypatch.setattr(retention_job, "SessionLocal", FakeSession)
    monkeypatch.setattr(retention_job.retention, "redact_expired_runs", lambda db, now: 0)

    # A clean night.
    monkeypatch.setattr(retention_job.retention, "purge_expired", lambda db, now: dict(summary))
    assert retention_job.main() == 0
    assert [c["status"] for c in transport.items("check_in")] == ["in_progress", "ok"]
    assert transport.items("event") == []
    transport.clear()

    # Rows held back: the exit code keeps its promise, the monitor tells the truth.
    monkeypatch.setattr(retention_job.retention, "purge_expired", lambda db, now: {**summary, "interviews_hard_delete_blocked": 2})
    assert retention_job.main() == 0
    assert [c["status"] for c in transport.items("check_in")] == ["in_progress", "error"]
    assert any("HELD BACK" in _blob([event]) for event in transport.items("event"))
    transport.clear()

    # The sweep itself fails.
    def broken(db: Any, now: Any) -> dict[str, int]:
        raise RuntimeError("could not reach the database")

    monkeypatch.setattr(retention_job.retention, "purge_expired", broken)
    assert retention_job.main() == 1
    assert [c["status"] for c in transport.items("check_in")] == ["in_progress", "error"]
    exceptions = [e for e in transport.items("event") if e.get("exception")]
    assert len(exceptions) == 1


def test_the_drain_worker_classifies_failures_and_names_nothing_about_the_candidate(live) -> None:
    from app.voice_platform.queue import worker
    from app.voice_platform.queue.sqs import QueuedMessage
    from app.voice_platform.queue.validation import CandidateValidationError

    transport = live(obs.SERVICE_INTERVIEW_WORKER)
    messages = [
        QueuedMessage("UG", "m-bad-row", "rh1", {"type": "candidate", "candidate": {}}),
        QueuedMessage("UG", "m-db-down", "rh2", {"type": "candidate", "candidate": {}}),
        QueuedMessage("UG", "m-fine", "rh3", {"type": "candidate", "candidate": {}}),
    ]

    class FakeQueue:
        acked: list[str] = []

        def pull(self, degree_level: str, *, max_messages: int, wait_seconds: int) -> Any:
            return iter(messages)

        def ack(self, degree_level: str, receipt_handle: str) -> None:
            self.acked.append(receipt_handle)

    class FakeDb:
        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    def fake_store(db: Any, message: QueuedMessage) -> tuple[str, bool]:
        if message.message_id == "m-bad-row":
            raise CandidateValidationError("email", f"not a college address: {EMAIL}")
        if message.message_id == "m-db-down":
            raise RuntimeError("connection refused")
        return ("EXT-1", True)

    original = worker.store_message
    worker.store_message = fake_store  # type: ignore[assignment]
    try:
        stored = worker.drain_once(FakeDb(), FakeQueue(), "UG")
    finally:
        worker.store_message = original  # type: ignore[assignment]
    sentry_sdk.flush()

    assert stored == 1 and FakeQueue.acked == ["rh3"]
    events = transport.items("event")
    kinds = sorted(event["tags"]["failure.kind"] for event in events)
    assert kinds == ["permanent", "retryable"]
    permanent = next(event for event in events if event["tags"]["failure.kind"] == "permanent")
    assert permanent["fingerprint"] == ["candidate-validation-error", "UG"]
    assert permanent["tags"]["queue.degree"] == "UG"
    assert EMAIL not in _blob(events)
    transactions = transport.items("transaction")
    assert len(transactions) == 1 and transactions[0]["transaction"] == "candidate.drain"
    assert transactions[0]["tags"]["service"] == "reep-interview-worker"
    data = transactions[0]["contexts"]["trace"]["data"]
    assert (data["received"], data["stored"], data["failed_permanent"], data["failed_retryable"]) == (3, 1, 1, 1)


def test_an_empty_receive_cycle_opens_no_transaction(live) -> None:
    from app.voice_platform.queue import worker

    transport = live(obs.SERVICE_INTERVIEW_WORKER)

    class EmptyQueue:
        def pull(self, degree_level: str, *, max_messages: int, wait_seconds: int) -> Any:
            return iter(())

    assert worker.drain_once(object(), EmptyQueue(), "PG") == 0
    sentry_sdk.flush()
    assert transport.items("transaction") == []


# --------------------------------------------------------------------------- #
# The tracing helpers under a live SDK
# --------------------------------------------------------------------------- #


def test_a_transaction_reuses_the_active_one_of_the_same_op_and_not_another(live) -> None:
    transport = live(obs.SERVICE_API)
    with sentry_sdk.start_transaction(name="/api/interview", op="websocket.server") as outer:
        with tracing.transaction("interview hr", op="websocket.server", conn_id="c0ffee") as tx:
            assert tx is outer, "a second websocket transaction was nested inside the integration's"
        with tracing.transaction("call.close", op="task") as inner:
            assert inner is not outer, "a task inside a socket must stay its own transaction"
    sentry_sdk.flush()
    names = sorted(t["transaction"] for t in transport.items("transaction"))
    assert names == ["call.close", "interview hr"]
    interview = next(t for t in transport.items("transaction") if t["transaction"] == "interview hr")
    assert interview["tags"]["conn_id"] == "c0ffee"
