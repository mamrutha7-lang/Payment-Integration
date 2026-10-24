"""Request traceability — one id per request, carried edge to log line.

Every HTTP request gets an X-Request-ID: the caller's own if it sent one (so a
trace can start at CloudFront/the ALB and stay one id all the way down), a fresh
uuid4 hex otherwise. The id is echoed on the response and stamped on one
structured access line per request in the ``reep.access`` logger:

    method=POST path=/api/student/uploads status=201 duration_ms=41 rid=9f2c…

That single line is what the CloudWatch metric filters and any grep-driven
debugging session key on: find the rid in a student's bug report (the SPA can
read the response header), grep the log group, and every hop of that request is
on one thread of evidence. It deliberately logs NO body, NO query string and NO
cookie — paths here can name student ids, which are opaque; the free-text that
must never reach a log (reasons, notes, transcripts) travels in bodies.

An incoming id is sanitised to [A-Za-z0-9._-], max 64 chars: a response header
is an injection surface, and a log line that lets the caller write newlines is
a forged log.
"""

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

access_log = logging.getLogger("reep.access")

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def _request_id(request: Request) -> str:
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    cleaned = _SAFE_ID.sub("", incoming)[:64]
    return cleaned or uuid.uuid4().hex


class RequestTraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = _request_id(request)
        request.state.request_id = rid
        # The join key between the two telemetry planes: a Sentry event tagged
        # request_id=<rid> and the CloudWatch access line rid=<rid> are the same
        # request. sentry_sdk is a no-op when SENTRY_DSN is blank, so this costs
        # nothing on a laptop; the guard only spares the import on deployments
        # that never installed telemetry at all.
        try:
            import sentry_sdk

            sentry_sdk.get_isolation_scope().set_tag("request_id", rid)
        except Exception:  # noqa: BLE001 — telemetry must never fail a request
            pass
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception continues to FastAPI's handlers; the trace line
            # must not be the casualty of the failure it exists to explain.
            access_log.info(
                "method=%s path=%s status=500 duration_ms=%d rid=%s",
                request.method,
                request.url.path,
                int((time.perf_counter() - started) * 1000),
                rid,
            )
            raise
        response.headers[REQUEST_ID_HEADER] = rid
        # /health is the load balancer talking to itself every few seconds;
        # logging it would bury the lines this file exists to make findable.
        if request.url.path != "/health":
            access_log.info(
                "method=%s path=%s status=%d duration_ms=%d rid=%s",
                request.method,
                request.url.path,
                response.status_code,
                int((time.perf_counter() - started) * 1000),
                rid,
            )
        return response
