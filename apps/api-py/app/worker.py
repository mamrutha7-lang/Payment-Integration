"""CLI entrypoint for Phase 4 durable workers.

Examples:
    python -m app.worker relay --once
    python -m app.worker domain --once
    python -m app.worker embedding --once
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import time
import uuid

from .workers.transport import SqsTransport

log = logging.getLogger("reep.worker")
_STOP = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="REEP durable worker")
    parser.add_argument("mode", choices=("relay", "domain", "embedding"))
    parser.add_argument("--once", action="store_true", help="process one bounded poll instead of running forever")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    return parser


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for a durable worker")
    return value


def _run_relay(*, once: bool, poll_seconds: float, owner: str) -> None:
    from .db import SessionLocal
    from .workers.outbox_relay import relay_once

    default_queue = _required("REEP_QUEUE_URL_DEFAULT")
    transport = SqsTransport()
    while not _STOP:
        with SessionLocal() as db:
            result = relay_once(db, transport, {"default": default_queue}, owner=owner)
        log.info("outbox relay result=%s", result)
        if once:
            return
        time.sleep(max(0.1, poll_seconds))


def _run_domain(*, once: bool, poll_seconds: float, owner: str) -> None:
    from .db import SessionLocal
    from .workers.domain_worker import consume_once

    queue = _required("REEP_QUEUE_URL_DEFAULT")
    transport = SqsTransport()
    while not _STOP:
        with SessionLocal() as db:
            result = consume_once(db, transport, queue, owner=owner, handlers={})
        log.info("domain worker result=%s", result)
        if once:
            return
        time.sleep(max(0.1, poll_seconds))


def _run_embedding(*, once: bool, poll_seconds: float, owner: str) -> None:
    from .ai.embeddings import embed_one
    from .db import SessionLocal
    from .workers.embedding_worker import make_provider, process_embedding

    queue = _required("REEP_QUEUE_URL_EMBEDDING")
    provider = make_provider(lambda text, model: embed_one(text, model_name=model))
    transport = SqsTransport()
    while not _STOP:
        messages = transport.receive(queue, max_messages=10, wait_seconds=1)
        for message in messages:
            receipt = message.get("ReceiptHandle")
            try:
                import json
                from .workers.contracts import EventEnvelope

                envelope = EventEnvelope.from_dict(json.loads(message.get("Body", "{}")))
                embedding_id = envelope.payload.get("embedding_id") or envelope.payload.get("payload", {}).get("embedding_id")
                if not isinstance(embedding_id, str):
                    raise ValueError("embedding event is missing embedding_id")
                with SessionLocal() as db:
                    result = process_embedding(db, embedding_id, owner=owner, provider=provider)
                if result in ("ready", "failed", "noop", "missing"):
                    transport.delete(queue, receipt)
                log.info("embedding worker result=%s embedding_id=%s", result, embedding_id)
            except Exception:
                log.exception("embedding message failed; transport will retry")
        if once:
            return
        time.sleep(max(0.1, poll_seconds))


def main(argv: list[str] | None = None) -> int:
    global _STOP
    args = _parser().parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    _STOP = False

    def handle_stop(_signum: int, _frame: object) -> None:
        global _STOP
        _STOP = True
        log.info("worker shutdown requested")

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    owner = os.environ.get("REEP_WORKER_ID", "worker-" + uuid.uuid4().hex[:12])
    if args.mode == "relay":
        _run_relay(once=args.once, poll_seconds=args.poll_seconds, owner=owner)
    elif args.mode == "domain":
        _run_domain(once=args.once, poll_seconds=args.poll_seconds, owner=owner)
    else:
        _run_embedding(once=args.once, poll_seconds=args.poll_seconds, owner=owner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
