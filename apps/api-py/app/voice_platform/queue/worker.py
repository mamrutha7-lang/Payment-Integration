"""The drain worker: pull candidate messages off one degree level's queue and
upsert them into `platform_candidates`.

    python -m app.voice_platform.queue.worker --degree UG
    python -m app.voice_platform.queue.worker --degree PG --once

One process per stream, matching the architecture's separate Undergraduate
and Postgraduate consumers. A message that fails to store is NOT acked: SQS
redelivers it, and after the queue's redrive policy it lands in the dead-letter
queue where an operator can read it — silently acking a bad record would make
the queue look healthy while the roster stays short.

Telemetry: this process reports to the reep-interview-worker Sentry project
through its OWN DSN (SENTRY_INTERVIEW_WORKER_DSN, never the api's). Each
non-empty receive cycle is one transaction, each message one span, and a
failure is classified on the event — `failure.kind=permanent` for a row that
will be wrong on every redelivery (grouped under one issue per stream, so a
bad spreadsheet is one issue with a count), `failure.kind=retryable` for a
store that the next delivery may survive. Nothing about the candidate — not
the id, not the row — is attached; the log line carries the message id, where
it is searchable but does not index. Blank DSN = off, said once at startup.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from ... import tracing
from ...config import settings
from ...observability import SERVICE_INTERVIEW_WORKER, flush, init_sentry, job_run
from .sqs import CandidateQueue, QueuedMessage, candidate_queue
from .validation import CandidateValidationError, validate_candidate

log = logging.getLogger("app.voice_platform.queue.worker")


def store_message(db: Session, message: QueuedMessage) -> tuple[str, bool]:
    """Persist one queued candidate. Returns (external_id, created)."""
    from ..storage import aurora

    body = message.body
    if body.get("type") != "candidate" or not isinstance(body.get("candidate"), dict):
        raise CandidateValidationError("type", f"unexpected message type {body.get('type')!r}")
    candidate = validate_candidate(body["candidate"])
    if candidate.degree_level != message.degree_level:
        raise CandidateValidationError(
            "degree_level",
            f"{candidate.degree_level} candidate arrived on the {message.degree_level} queue",
        )
    row, created = aurora.upsert_candidate(
        db,
        candidate,
        source=str(body.get("source") or "bulk_upload"),
        source_ref=body.get("source_ref"),
        status="validated",
    )
    return row.external_id, created


def _process(
    db: Session, queue: CandidateQueue, degree_level: str, messages: list[QueuedMessage]
) -> tuple[int, int, int]:
    """Store, ack and classify each received message. Returns
    (stored, failed_permanent, failed_retryable)."""
    stored = permanent = retryable = 0
    for message in messages:
        # Tags for whatever Sentry captures while THIS message is handled: the
        # degree level (two values) and, on failure, its kind. Never the
        # message id or the body — those stay in the log line.
        with tracing.scoped(**{"queue.degree": degree_level}) as scope:
            with tracing.span("queue.process", "store candidate", degree=degree_level):
                try:
                    external_id, created = store_message(db, message)
                    db.commit()
                except CandidateValidationError as exc:
                    db.rollback()
                    permanent += 1
                    # PERMANENT: the row is wrong and will be wrong on every
                    # redelivery — left on the queue on purpose (see the module
                    # docstring). One issue per stream, not one per row.
                    if scope is not None:
                        scope.set_tag("failure.kind", "permanent")
                        scope.fingerprint = ["candidate-validation-error", degree_level]
                    log.error("Leaving message %s on the %s queue: %s", message.message_id, degree_level, exc)
                    continue
                except Exception:
                    db.rollback()
                    retryable += 1
                    # RETRYABLE: the store failed for a reason the next
                    # delivery may not hit. SQS redelivers; the exception is
                    # the issue, grouped by its own type as usual.
                    if scope is not None:
                        scope.set_tag("failure.kind", "retryable")
                    log.exception("Could not store message %s from the %s queue", message.message_id, degree_level)
                    continue
            queue.ack(degree_level, message.receipt_handle)
            stored += 1
            log.info("%s candidate %s (%s)", "Stored" if created else "Updated", external_id, degree_level)
    return stored, permanent, retryable


def drain_once(
    db: Session, queue: CandidateQueue, degree_level: str, *, max_messages: int = 10, wait_seconds: int = 0
) -> int:
    """One receive → store → ack cycle. Returns how many were stored.

    The transaction opens only once messages have arrived: a long-poll that
    returns nothing is not work, and a transaction per idle cycle would be
    three a minute of noise sampled at 1.0."""
    messages = list(queue.pull(degree_level, max_messages=max_messages, wait_seconds=wait_seconds))
    if not messages:
        return 0
    with job_run("candidate.drain", **{"queue.degree": degree_level}) as run:
        stored, permanent, retryable = _process(db, queue, degree_level, messages)
        tracing.annotate(
            received=len(messages), stored=stored, failed_permanent=permanent, failed_retryable=retryable
        )
        if stored == 0:
            # Every message received was left on the queue. The cycle
            # completed, and it did not do its job.
            run.mark_error("nothing_stored")
    return stored


def run(degree_level: str, *, once: bool = False, wait_seconds: int = 20, idle_sleep: float = 1.0) -> int:
    from ...db import SessionLocal

    queue = candidate_queue()
    if queue is None or not queue.configured(degree_level):
        raise SystemExit(
            f"No SQS queue is configured for the {degree_level} stream "
            f"(PLATFORM_{degree_level}_QUEUE_URL is blank)."
        )
    total = 0
    while True:
        db = SessionLocal()
        try:
            stored = drain_once(db, queue, degree_level, wait_seconds=wait_seconds)
        finally:
            db.close()
        total += stored
        if once:
            return total
        if stored == 0:
            time.sleep(idle_sleep)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Drain one candidate stream into Postgres.")
    parser.add_argument("--degree", required=True, choices=["UG", "PG"])
    parser.add_argument("--once", action="store_true", help="one receive cycle, then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Its OWN project and its OWN DSN — never the api's, which this process
    # would otherwise happily read from the same task environment.
    init_sentry(SERVICE_INTERVIEW_WORKER, settings.sentry_interview_worker_dsn)
    try:
        stored = run(args.degree, once=args.once)
    finally:
        # --once is a short-lived process: drain the transport before it exits,
        # or the failure it was reporting leaves with it.
        flush()
    if args.once:
        print(f"stored {stored} candidate(s) from the {args.degree} queue")


if __name__ == "__main__":  # pragma: no cover
    main()
