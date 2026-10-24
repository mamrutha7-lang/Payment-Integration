"""Sentry tracing for the whole application.

`sentry_sdk` is initialised once in app/main.py when SENTRY_DSN is set; every
call below is a no-op otherwise (the SDK's own contract), so a laptop and CI pay
nothing and no caller needs to check first.

WHAT THE FastAPI INTEGRATION ALREADY DOES, so this is not duplicated: it traces
HTTP requests, and it CONTINUES a trace the browser started — apps/web sets
`tracePropagationTargets` scoped to /api, so a click in Angular and the request it
causes are one trace already.

WHAT IT DOES NOT SEE, which is what these helpers are for:
  * a WebSocket's lifetime. `/api/interview` holds a socket for up to eight
    minutes and is the most complex path in the system; to the HTTP integration
    it is a single upgrade request that never returns.
  * the calls made INSIDE a request — Bedrock, the LLM adapter, the knowledge
    base, S3, SQS, DynamoDB. Those are where the latency and the failures are.

LIVES AT THE APP ROOT ON PURPOSE. It began under app/voice_platform/monitoring/,
which meant the core interview path could only trace itself by importing a
feature subpackage — a dependency pointing the wrong way. That module is now a
re-export of this one, so its existing callers are unchanged.

NOTHING HERE RECORDS STUDENT TEXT. Spans carry names, counts, durations and
identifiers; `sentry_sdk.init` sets include_local_variables=False and
max_request_body_size="never" for the same reason (see app/main.py, and
tests/test_codebase_guards.py for the guard). Rule 1 does not stop at the model
adapter — Sentry is off the account too.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Any

import sentry_sdk


def enabled() -> bool:
    try:
        return bool(sentry_sdk.is_initialized())
    except Exception:  # noqa: BLE001 - never let observability raise
        return False


@contextmanager
def transaction(name: str, *, op: str = "websocket.server", **tags: Any) -> Iterator[Any]:
    """A root transaction for one WebSocket connection (or one worker cycle).

    REUSES an active transaction of the same op rather than nesting a second
    one. On sentry-sdk 2.68.1 the Starlette integration already opens a
    `websocket.server` transaction spanning the socket's whole life — the
    "single upgrade request" premise in this module's docstring was true of an
    older pin — so starting another inside it split every interview across two
    transactions with two names and doubled the cost. The outer one keeps its
    spans and gets THIS name and these tags; a different op (a `task` inside a
    socket) still gets its own.
    """
    if not enabled():
        yield None
        return
    current = None
    try:
        current = sentry_sdk.get_current_scope().transaction
    except Exception:  # noqa: BLE001 - never let observability raise
        current = None
    if current is not None and getattr(current, "op", None) == op:
        try:
            current.name = name
            current.source = "custom"
            for key, value in tags.items():
                current.set_tag(key, str(value))
        except Exception:  # noqa: BLE001
            pass
        yield current
        return
    with sentry_sdk.start_transaction(name=name, op=op) as tx:
        for key, value in tags.items():
            tx.set_tag(key, str(value))
        yield tx


@contextmanager
def scoped(**tags: Any) -> Iterator[Any]:
    """A scope for one unit of work — one queue message, one job step. Tags set
    here apply to whatever Sentry captures inside the block and to nothing
    after it. Yields the scope so a caller can set a fingerprint on it, or None
    when Sentry is off. Tags must be low-cardinality: a degree level, a failure
    kind — never a message, student or file id."""
    if not enabled():
        yield None
        return
    with sentry_sdk.new_scope() as scope:
        for key, value in tags.items():
            scope.set_tag(key, str(value))
        yield scope


def annotate(**data: Any) -> None:
    """Attach data to whatever span is active — counts, sizes, identifiers.
    No-op when Sentry is off or nothing is active, so a call site never has to
    branch on the None that `span()` yields."""
    if not enabled():
        return
    try:
        current = sentry_sdk.get_current_span()
    except Exception:  # noqa: BLE001
        return
    if current is None:
        return
    for key, value in data.items():
        try:
            current.set_data(key, value)
        except Exception:  # noqa: BLE001
            pass


def span(op: str, description: str | None = None, **data: Any) -> Any:
    """A child span around one external call: `with span("aws.s3", "put_object")`."""
    if not enabled():
        return nullcontext()
    ctx = sentry_sdk.start_span(op=op, name=description or op)
    for key, value in data.items():
        try:
            ctx.set_data(key, value)
        except Exception:  # noqa: BLE001
            pass
    return ctx


def tag_connection(**tags: Any) -> None:
    """Attach conn/session/degree tags to whatever Sentry captures next."""
    if not enabled():
        return
    for key, value in tags.items():
        sentry_sdk.set_tag(key, str(value))


def capture(exc: BaseException, **tags: Any) -> None:
    if not enabled():
        return
    with sentry_sdk.new_scope() as scope:
        for key, value in tags.items():
            scope.set_tag(key, str(value))
        sentry_sdk.capture_exception(exc)
