"""Per-user rate limiting for LLM-backed HTTP endpoints.

The 2026-08 scalability audit found the expensive routes — resume generation
and the retained /api/agent chat surface — had no per-user ceiling at all.
Every call runs a synchronous model round-trip in the shared 40-thread pool, so
one student's retry loop (or a 60-person class told to "regenerate until it
looks good") was simultaneously a thread-pool DoS and an unbudgeted token bill.
The interview WebSocket already had both halves of this control
(interview_max_sessions_per_user + interview_max_per_student_per_day); this
module gives the HTTP routes their missing half.

Sliding window rather than a token bucket: "at most N calls in any 60 s" is
the sentence the setting (LLM_REQUESTS_PER_MINUTE) promises, a deque of
timestamps is exactly that sentence with no refill arithmetic to get subtly
wrong, and the memory cost is bounded at N floats per user who called at all.

PER PROCESS, on purpose. The state is a dict behind a threading.Lock — these
endpoints are sync `def`s running in the threadpool, so the lock is real, and
an asyncio primitive would be the wrong tool. N uvicorn workers therefore relax
the ceiling to at most N x the setting, which is still a ceiling (the audit's
finding was that there was NONE), costs no infrastructure, and degrades in the
safe direction: a limit that is accidentally loose still stops the loop; a
shared store that is accidentally down stops the feature. Move this to a shared
store the day the multiplied bound stops being acceptable, not before.

429 with Retry-After, not 503: the service is fine, the CALLER is over budget,
and Retry-After is the header that lets a well-behaved client (and a student
reading devtools) know the wait is seconds, not an outage.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import Depends, HTTPException, status

from .config import settings
from .identity import get_current_session

_WINDOW_S = 60.0

_lock = threading.Lock()
_calls: dict[str, deque[float]] = {}


def _check(user_id: str) -> float | None:
    """One call's verdict: None = allowed (and recorded), else seconds to wait.

    The prune and the append happen under one lock acquisition so two threads
    carrying the same cookie cannot both see "one slot left" and both take it.
    A user's entry holds at most `limit` floats forever after their last call —
    ~5,570 accounts x a few floats is not a memory figure worth a sweeper.
    """
    now = time.monotonic()
    limit = settings.llm_requests_per_minute
    with _lock:
        window = _calls.setdefault(user_id, deque())
        while window and now - window[0] >= _WINDOW_S:
            window.popleft()
        if len(window) >= limit:
            return _WINDOW_S - (now - window[0])
        window.append(now)
        return None


def llm_rate_limited(session: dict = Depends(get_current_session)) -> None:
    """FastAPI dependency: attach to any route whose handler calls a model.

    Depends on get_current_session rather than re-reading the cookie so FastAPI's
    per-request dependency cache means zero extra verification work on routes
    that already resolve the session — which is all of them.
    """
    wait = _check(session["userId"])
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many AI requests. Wait a moment and try again — the limit "
                f"is {settings.llm_requests_per_minute} per minute."
            ),
            headers={"Retry-After": str(max(1, int(wait) + 1))},
        )


def reset() -> None:
    """Forget every window. FOR TESTS: the suite drives one seeded student
    through dozens of /api/agent/chat calls in one process, and a limiter that
    remembers them across tests would fail the suite for being thorough — the
    exact trap AGENTS.md warns turns guards into deleted guards. conftest.py
    calls this around each test."""
    with _lock:
        _calls.clear()
