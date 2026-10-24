"""DEPRECATED — do not use. Kept only as a tombstone; scheduled for deletion.

Historically this was a local SQLite bank keyed by a client-chosen session_id.
That was the P0: whoever named the session owned the thread. Memory now lives in
Postgres as Conversation/Message rows (app/conversations.py), keyed by a
server-issued conversation_id that only the owning user's session can resolve.

Its docstring then advertised itself as the LiveKit voice worker's entry point.
That worker, and the POST /api/voice/transcript door it wrote through, were both
removed from the repo in 2026-09. It has NO importers anywhere in app/ or tests/.

Why it is a hazard rather than merely dead code: save_message() opened its own
SessionLocal and wrote straight into append_message, bypassing every rule the
routers enforce — the compulsory opening greeting, transcript length limits,
final-only policy, provider dedup, and worker authentication. It was the obvious
place a future out-of-request assistant turn would get written, silently
skipping all of it.

If you need to append a turn there is now exactly ONE door:
  app.conversations.append_message(db, ...)

The in-process interview relay uses it too (app/routers/interview.py), so the
greeting, length limits, final-only policy and provider dedup are enforced in one
place for typed and spoken turns alike.
"""

from __future__ import annotations

from typing import NoReturn

_REPLACEMENT = (
    "app.memory is deprecated. Use conversations.append_message(db, ...) — it "
    "enforces the greeting, length limits, final-only policy and dedup that this "
    "module bypassed."
)


def save_message(*_args: object, **_kwargs: object) -> NoReturn:
    """Removed. Raises rather than writing, so a caller finds out at once instead
    of quietly persisting a turn that skipped every policy check."""
    raise NotImplementedError(_REPLACEMENT)


def get_history(*_args: object, **_kwargs: object) -> NoReturn:
    """Removed. Use conversations.history(db, ...) or GET /api/agent/history."""
    raise NotImplementedError(_REPLACEMENT)
