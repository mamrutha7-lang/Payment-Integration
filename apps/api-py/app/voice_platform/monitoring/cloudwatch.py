"""Amazon CloudWatch: a log handler and a metrics helper for every platform
handler, plus the `handler_span` decorator that stamps entry, duration and
failure on each one.

Two paths to CloudWatch Logs, and which one runs is a deployment choice:

* stdout — the default. On ECS the awslogs driver already ships every line of
  stdout to the `/reep/api` log group (infra/aws/observability.tf), so the
  platform's loggers write there like everything else and this module adds
  nothing but the structured `event=` prefix.
* direct — set PLATFORM_CLOUDWATCH_LOG_GROUP and `configure()` attaches
  `CloudWatchLogsHandler`, which batches PutLogEvents on a background thread.
  For an EC2 ASG without the agent, or to give the platform its own group.

Metrics go through PutMetricData under PLATFORM_CLOUDWATCH_NAMESPACE, only
when a region resolves; otherwise `put_metric` is a no-op, never an error.
Nothing in here may raise into a handler: observability that can take down the
thing it observes is a liability, so every AWS call is wrapped.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from ...config import settings

_ROOT = "app.voice_platform"
log = logging.getLogger(f"{_ROOT}.monitoring")

_configured = False
_configure_lock = threading.Lock()
_metrics_client: Any = None
_metrics_lock = threading.Lock()


def get_logger(name: str) -> logging.Logger:
    """`app.voice_platform.<name>` — a child of the one logger `configure()`
    attaches the CloudWatch handler to."""
    return logging.getLogger(f"{_ROOT}.{name}")


class CloudWatchLogsHandler(logging.Handler):
    """Batched PutLogEvents. Creates the stream lazily; swallows every error."""

    def __init__(self, client: Any, log_group: str, stream_name: str, *, flush_every_s: float = 2.0, max_batch: int = 200) -> None:
        super().__init__()
        self._client = client
        self._group = log_group
        self._stream = stream_name
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._flush_every = flush_every_s
        self._max_batch = max_batch
        self._stream_ready = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="cloudwatch-logs", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            self._buffer.append({"timestamp": int(record.created * 1000), "message": message[:256000]})
            full = len(self._buffer) >= self._max_batch
        if full:
            self.flush()

    def _ensure_stream(self) -> bool:
        if self._stream_ready:
            return True
        try:
            self._client.create_log_stream(logGroupName=self._group, logStreamName=self._stream)
        except Exception as exc:  # noqa: BLE001
            if "ResourceAlreadyExistsException" not in type(exc).__name__ and "already exists" not in str(exc):
                log.debug("create_log_stream failed: %s", exc)
                return False
        self._stream_ready = True
        return True

    def flush(self) -> None:
        with self._lock:
            events, self._buffer = self._buffer, []
        if not events or not self._ensure_stream():
            with self._lock:
                self._buffer = events + self._buffer
            return
        events.sort(key=lambda e: e["timestamp"])
        try:
            self._client.put_log_events(logGroupName=self._group, logStreamName=self._stream, logEvents=events)
        except Exception as exc:  # noqa: BLE001 - never raise into the app
            log.debug("put_log_events failed (%d events dropped): %s", len(events), exc)

    def _loop(self) -> None:
        while not self._stop.wait(self._flush_every):
            self.flush()
        self.flush()

    def close(self) -> None:
        self._stop.set()
        self.flush()
        super().close()


def configure(*, client: Any | None = None, stream_name: str | None = None) -> bool:
    """Attach the direct CloudWatch handler once, if a log group is set.
    Returns whether it is attached. Called from app startup; harmless twice."""
    global _configured
    with _configure_lock:
        if _configured:
            return True
        group = settings.platform_cloudwatch_log_group.strip()
        if not group:
            return False
        try:
            if client is None:
                import boto3

                client = boto3.client("logs", region_name=settings.platform_region or None)
            import socket

            stream = stream_name or f"api-{socket.gethostname()}-{int(time.time())}"
            handler = CloudWatchLogsHandler(client, group, stream)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            logging.getLogger(_ROOT).addHandler(handler)
            _configured = True
            log.info("CloudWatch Logs handler attached: group=%s stream=%s", group, stream)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("CloudWatch Logs handler could not be attached: %s", exc)
            return False


def put_metric(name: str, value: float = 1.0, *, unit: str = "Count", client: Any | None = None, **dimensions: str) -> bool:
    """One PutMetricData under the platform namespace. No region ⇒ no-op."""
    global _metrics_client
    region = settings.platform_region
    namespace = settings.platform_cloudwatch_namespace.strip()
    if not region or not namespace:
        return False
    try:
        if client is None:
            with _metrics_lock:
                if _metrics_client is None:
                    import boto3

                    _metrics_client = boto3.client("cloudwatch", region_name=region)
                client = _metrics_client
        client.put_metric_data(
            Namespace=namespace,
            MetricData=[{
                "MetricName": name,
                "Value": float(value),
                "Unit": unit,
                "Dimensions": [{"Name": k, "Value": str(v)} for k, v in dimensions.items()],
            }],
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("put_metric_data %s failed: %s", name, exc)
        return False


def handler_span(name: str, **dimensions: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Log entry/exit with duration and emit `HandlerDuration` /
    `HandlerErrors` for one handler. Works on sync and async functions."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        logger = get_logger("handlers")

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.monotonic()
                try:
                    return await fn(*args, **kwargs)
                except Exception:
                    put_metric("HandlerErrors", 1, handler=name, **dimensions)
                    logger.exception("event=handler.error handler=%s", name)
                    raise
                finally:
                    elapsed_ms = (time.monotonic() - started) * 1000
                    logger.info("event=handler.done handler=%s duration_ms=%.0f", name, elapsed_ms)
                    put_metric("HandlerDuration", elapsed_ms, unit="Milliseconds", handler=name, **dimensions)

            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                return fn(*args, **kwargs)
            except Exception:
                put_metric("HandlerErrors", 1, handler=name, **dimensions)
                logger.exception("event=handler.error handler=%s", name)
                raise
            finally:
                elapsed_ms = (time.monotonic() - started) * 1000
                logger.info("event=handler.done handler=%s duration_ms=%.0f", name, elapsed_ms)
                put_metric("HandlerDuration", elapsed_ms, unit="Milliseconds", handler=name, **dimensions)

        return wrapper

    return decorate
