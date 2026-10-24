"""Tracing must describe what happened without carrying what was said.

Sentry is off the account. Rule 1 does not stop at the model adapter — a trace
that helpfully attaches a student's transcript is the same leak as a prompt that
does, through a door no `carries_student_data=True` gate guards. These tests pin
both halves: that the helpers are inert when no DSN is set (the contract every
call site relies on, since none of them checks first), and that when a DSN IS
set the captured payload carries names, counts and identifiers only.

The incident behind this file: `sentry_sdk.init` set send_default_pii=False and
stopped, leaving `include_local_variables` at its default of True, so every
captured exception shipped every stack frame's locals — which on this codebase
are a student speaking.
"""

from __future__ import annotations

import json

import pytest

from app import tracing

# A string that must never appear in anything Sentry is handed.
_STUDENT_SAID = "My CGPA is 8.7 and I was rejected by Infosys last week"


def test_the_helpers_are_inert_without_a_dsn() -> None:
    """No call site checks `enabled()` first, so this is load-bearing.

    app/ai/llm.py, app/ai/embeddings.py, app/interview_nova.py and
    app/routers/interview.py all call these unconditionally. If any raised or
    returned a non-context-manager when Sentry is unconfigured, every laptop and
    every CI run would fail on an observability feature that is meant to cost
    nothing when it is off.
    """
    if tracing.enabled():
        pytest.skip("a DSN is configured in this environment; the inert path cannot be tested")

    with tracing.transaction("interview hr", op="websocket.server", conn_id="c0ffee") as tx:
        assert tx is None
        with tracing.span("llm.complete", "bedrock nova-pro", messages=3):
            pass
    tracing.tag_connection(conn_id="c0ffee")
    tracing.capture(RuntimeError("boom"), conn_id="c0ffee")


def test_a_captured_trace_carries_shape_and_never_content() -> None:
    """With Sentry live, the transaction names the work and omits the words."""
    sentry_sdk = pytest.importorskip("sentry_sdk")
    captured: list[dict] = []

    client = sentry_sdk.Client(
        dsn="https://abc123@o0.ingest.sentry.io/0",
        environment="test",
        traces_sample_rate=1.0,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        before_send_transaction=lambda event, hint: (captured.append(event), None)[1],
    )
    # 2.x has no `use_client`; a client is bound to a scope, and `new_scope`
    # restores the previous binding on exit so this cannot leak into other tests.
    with sentry_sdk.new_scope() as scope:
        scope.set_client(client)
        # The student's words are in scope exactly as they are on the real path.
        student_text = _STUDENT_SAID
        with tracing.transaction(
            "interview hr", op="websocket.server", conn_id="c0ffee123456", engine="NovaSonicSession"
        ):
            with tracing.span(
                "bedrock.invoke_bidirectional_stream",
                "open + handshake",
                region="ap-northeast-1",
                model="amazon.nova-2-sonic-v1:0",
            ):
                pass
            with tracing.span(
                "llm.complete", "bedrock nova-pro", messages=3, carries_student_data=True
            ):
                assert student_text  # used, so it is a live local in this frame

    assert captured, "the transaction was not captured; the helpers are not wired to the SDK"
    blob = json.dumps(captured)

    # The point of the file.
    assert _STUDENT_SAID not in blob, "a student's words reached Sentry through a trace"

    # And the trace is actually useful: the work is named and tagged.
    assert "interview hr" in blob
    assert "bedrock.invoke_bidirectional_stream" in blob
    assert "llm.complete" in blob
    assert "c0ffee123456" in blob, "the connection id should be findable in the trace"
    # `carries_student_data` is recorded as a flag on purpose — it makes rule 1
    # auditable (which calls carried a record, and where they went) without any
    # of the content travelling.
    assert "carries_student_data" in blob


def test_the_voice_platform_shim_still_points_at_the_shared_helper() -> None:
    """The helpers moved to the app root so the core interview path could use
    them without importing a feature subpackage. The voice platform's two
    callers import `..monitoring.sentry`, so that name has to keep resolving to
    the same objects — a second copy would trace into a different transaction.
    """
    from app.voice_platform.monitoring import sentry as shim

    for name in ("enabled", "transaction", "span", "tag_connection", "capture"):
        assert getattr(shim, name) is getattr(tracing, name), f"{name} diverged from app.tracing"
