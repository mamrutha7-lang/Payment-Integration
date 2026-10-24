"""What every interview engine shares, and what none of them owns alone.

`/api/interview` has had more than one engine behind it since the local one
landed, and the pieces below were never about any of them: the persona a student
is assessed against, the records the router's writers take, the close codes the
browser maps to sentences, the per-worker and per-user caps, and the defensive
parse that turns a model's JSON into a scorecard. They lived in
app/interview_relay.py because that was the only engine when they were written.

THEY OUTLIVED IT. The OpenAI relay is gone (2026-09) and Amazon Nova 2 Sonic
runs the interview; the local engine still runs it with nothing leaving the
machine. Neither inherits from the other, and both hold to the same contracts —
which is only true because the contracts live HERE, in one file, imported rather
than copied. A parallel definition of `_TurnRecord` would drift the moment
either side gained a field, silently: both would still construct, and only one
would carry the new value into the database.

RULE 1 (AGENTS.md) is why this module imports no ORM model, no Session and no
app.conversations, exactly as the relay did not. Everything an engine persists
leaves through a callable that routers/interview.py supplies, so the containment
is a property of the import graph rather than a convention — and that is what
lets the engines' tests drive the whole turn protocol with no database.

Nothing here knows a wire protocol. If something in this file starts describing
events, it belongs in the engine that speaks them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Final, Protocol, TypeVar

from fastapi import WebSocket

# Via fastapi, not `from starlette.websockets import ...` directly: starlette is
# a TRANSITIVE dependency here and requirements.txt pins fastapi rather than
# starlette (the same convention as the rest of apps/api-py). Importing it
# directly meant this module depended on a package whose version nothing in this
# repo fixes, under fastapi's own floor of `starlette>=0.46.0`. Identical object.
from fastapi.websockets import WebSocketState

log = logging.getLogger(__name__)

_E = TypeVar("_E", bound=BaseException)


class InterviewEngine(Protocol):
    """The whole of what routers/interview.py needs from an engine.

    Written down because it used to be implicit in the relay's shape, and
    an implicit contract between three classes is one that drifts. `run()`
    returns the (code, reason) BOTH sockets close with; `request_stop` is called
    from outside the engine's own tasks — the consent heartbeat's 4014 and the
    app's shutdown drain — so it may not block and may not await.

    Deliberately structural (a Protocol) rather than a base class: an engine
    shares no implementation with any other, only these two methods and the
    constructor keywords the router passes, and inheritance would invite exactly
    the shared half-abstract base that made the relay hard to remove.
    """

    async def run(self) -> tuple[int, str]: ...

    def request_stop(self, code: int, reason: str) -> None: ...


# ---------------------------------------------------------------------------
# The persona
# ---------------------------------------------------------------------------

# VERBATIM -- the wording is the product spec, not a suggestion. Editing it
# changes what every student is assessed against.
#
# It is also, deliberately, the ONLY thing sent upstream that this app authors
# -- alone for the generic interview, or composed by app/interview_matrix.py
# (base persona first and unchanged, then a specialization block and a phase
# directive, all fixed strings) when the student picked a specialization.
# AGENTS.md rule 1 gates student PII leaving the machine, and Bedrock is
# emphatically not loopback. A fixed persona carries no marks, USN or attendance,
# and neither does a specialization key the student chose in the UI, so the
# hosted engine sits outside that gate today. The moment anyone personalises
# these instructions (branch, CGPA, target company) or feeds a resume into the
# session, the path must be routed through student_data_egress_allowed() in
# apps/api-py/app/ai/llm.py and degrade to this generic persona when it refuses --
# the same shape as /student/resume/generate falling back to used_ai=false.
#
# It is shared, byte for byte, by every engine. Two students sitting the same
# interview on different engines have to be assessed against the same words, or
# their scorecards are not comparable -- which is the whole reason this string
# is imported and never copied.
# THE BAR IS A TIER-1 MULTINATIONAL'S CAMPUS ROUND (2026-09-15). It was "a
# strict yet constructive AI Mock Interviewer" preparing students "for
# corporate and technical job placements" — a fair description of a college
# placement cell's own panel, and the owner asked for the interview a top-tier
# employer actually runs: structured, competency-based, evidence-hungry, and
# unmoved by a textbook answer with no example under it. What changed is the
# standard the answers are held to and the manner (calm, precise, courteous,
# never harsh, never gushing); what did NOT change is the mechanics every test
# and every engine relies on — one question at a time, no interrupting, one
# sentence of micro-feedback after each answer, then the next question. The
# blindness paragraph below is untouched.
_INTERVIEWER_PERSONA: Final[str] = (
    "You are a senior interviewer on the campus-hiring panel of a Tier-1 "
    "multinational company, conducting a structured, competency-based "
    "placement interview. Hold the student to the bar a top-tier employer "
    "applies: answers should be specific, evidence-backed and structured "
    "(STAR for behavioural questions; a stated hypothesis, then analysis, "
    "then a recommendation for case and technical questions), quantified "
    "wherever a number exists, and honest about what they do not know. A "
    "generic or textbook answer with no example or figure under it does not "
    "meet the bar; probe it once, briefly, with 'what exactly did you do' or "
    "'what was the number'. Be calm, precise and courteous throughout - "
    "professional and demanding, never harsh, never gushing, and never "
    "reveal a model answer.\n"
    "\n"
    "Ask one clear question at a time. Do not interrupt the student while "
    "they are speaking. After they finish answering, provide a 1-sentence "
    "micro-feedback critique focusing on their structure (STAR method), "
    "specificity, pacing, or vocabulary, then seamlessly transition to the "
    "next logical interview question.\n"
    "\n"
    # AGENTS.md rule 1, stated to the model as well as enforced by construction.
    # Nothing in this process puts a student record into this prompt, so there is
    # nothing personal here to leak -- but a model that is not TOLD it is blind
    # will cheerfully invent a CGPA and say it out loud, and the student has no
    # way to know it was fiction. It is the marker the next editor meets before
    # adding a "personalise the interview" field.
    "You cannot see this student's marks, attendance, CGPA, USN, resume or any "
    "other record from their REEP dashboard - none of that is available to you, "
    "by design. If they ask what their own figures are, say plainly that you "
    "cannot see them and ask the student to tell you, then carry on with the "
    "interview. Never guess, estimate or invent a figure about them."
)


# ---------------------------------------------------------------------------
# Close codes — the contract with the browser
# ---------------------------------------------------------------------------

# RFC 6455 caps the close-frame reason at 123 BYTES. Exceeding it is a protocol
# error, not a truncation: the peer then reports 1006 "abnormal closure" with no
# reason at all, which is the exact opposite of the intent.
_MAX_CLOSE_REASON_BYTES: Final[int] = 123


_CLOSE_OK: Final[int] = 1000  # Interview complete
_CLOSE_GOING_AWAY: Final[int] = 1001  # Server shutting down
_CLOSE_INTERNAL: Final[int] = 1011  # Unexpected error on our side
_CLOSE_OVERLOADED: Final[int] = 1013  # Per-worker concurrency cap hit
# The engine this deployment runs is not configured: no AWS region for Nova
# Sonic, or (historically) no API key. `settings.interview_ready` decides,
# per engine, and `interview_unready_reason` is the sentence.
_CLOSE_NOT_CONFIGURED: Final[int] = 4001
_CLOSE_UPSTREAM_UNAVAILABLE: Final[int] = 4002  # Upstream 403/429/5xx/handshake
_CLOSE_FORBIDDEN_ORIGIN: Final[int] = 4003  # Origin not in WEB_ORIGIN
_CLOSE_IDLE: Final[int] = 4008  # No inbound audio
_CLOSE_SESSION_CAP: Final[int] = 4009  # Hard wall-clock cap
_CLOSE_UNKNOWN_SPECIALIZATION: Final[int] = 4010  # ?specialization= not in the matrix
# RETIRED WITH THE OPENAI RELAY, and kept deliberately. It meant "our own
# response.create was never acknowledged" — a failure only an engine that issues
# response.create can have, and no engine does now. It stays defined because the
# CLIENT still maps 4011 to a sentence (interview.service.ts), and a code that
# can be received but not explained is worse than one nothing sends. Delete it
# here and there together, or not at all.
_CLOSE_TURN_STALLED: Final[int] = 4011
# One student already holds interview_max_sessions_per_user live interviews on
# this worker. Deliberately NOT 1013: 1013 says "the server is full, everyone is
# affected, try later", while this says "YOUR other interview is still open" --
# a different sentence for the student and a different diagnosis for the
# operator, who otherwise reads a capacity incident where there is none.
_CLOSE_USER_SESSION_CAP: Final[int] = 4012
_CLOSE_CONSENT_REQUIRED: Final[int] = 4013  # No live consent row for this student
_CLOSE_CONSENT_REVOKED: Final[int] = 4014  # Consent withdrawn mid-interview
# The student has already run interview_max_per_student_per_day interviews in
# the last 24 hours — the VOLUME half of the per-user cap (4012 is the
# concurrency half). Deliberately its own code: "your other interview is still
# open" and "you have done eight today" call for different sentences, and every
# accepted socket bills upstream from the handshake, so the refusal must happen
# before an upstream connection exists. Raised by routers/interview.py's
# _open_records, with the rest of the vocabulary defined here.
_CLOSE_DAILY_CAP: Final[int] = 4015

# NOTE 4013/4014 are defined here, with the rest of the vocabulary, but nothing
# in this module raises them yet: consent enforcement lands in
# routers/interview.py only AFTER the client is posting consent rows, or every
# existing student is locked out of the feature on the deploy that turns it on.
# They live here so the numbers cannot be reassigned in the meantime, and so
# that interview.service.ts has one list to mirror.


_E = TypeVar("_E", bound=BaseException)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _close_reason(text: str) -> str:
    """Clip a close reason to the RFC 6455 limit on a UTF-8 character boundary."""
    raw = text.encode("utf-8")
    if len(raw) <= _MAX_CLOSE_REASON_BYTES:
        return text
    return raw[:_MAX_CLOSE_REASON_BYTES].decode("utf-8", errors="ignore")


def _first_leaf(group: BaseExceptionGroup[Any], kind: type[_E]) -> _E | None:
    """First leaf exception of `kind` in a possibly NESTED exception group.

    TaskGroup nests groups when a child is itself a TaskGroup, so `eg.exceptions[0]`
    is not reliably the exception you matched on -- it can be another group.
    """
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            found = _first_leaf(exc, kind)
            if found is not None:
                return found
        elif isinstance(exc, kind):
            return exc
    return None


# ---------------------------------------------------------------------------
# The scorecard, parsed defensively
# ---------------------------------------------------------------------------

_MAX_REPORT_LIST_ITEMS: Final[int] = 5
_MAX_REPORT_ITEM_CHARS: Final[int] = 300
_MAX_REPORT_PARAGRAPH_CHARS: Final[int] = 1200


def _report_score(value: Any) -> int | None:
    """One 0-100 score, or None when the model did not give a usable one.

    NEVER a fabricated 0. A missing score and a zero mean opposite things to the
    mentor reading the scorecard, and only one of them is a judgement about the
    student -- so an unparseable field stays empty rather than becoming the
    worst possible mark.
    """
    if isinstance(value, bool):
        # bool is an int in Python, and True would clamp to 1/100.
        return None
    if isinstance(value, (int, float)):
        number = int(value)
    elif isinstance(value, str):
        try:
            number = int(float(value.strip()))
        except ValueError:
            return None
    else:
        return None
    return max(0, min(100, number))


def _report_strings(value: Any, max_items: int) -> list[str]:
    """A bounded list of bounded strings, salvaging what is salvageable.

    A model that answers with one string instead of a list is corrected rather
    than rejected: "degrade, never assert" is the whole posture of this parse,
    and losing the entire report over a container type would be the opposite.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            continue
        text = entry.strip()[:_MAX_REPORT_ITEM_CHARS]
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _report_paragraph(value: Any) -> str:
    return value.strip()[:_MAX_REPORT_PARAGRAPH_CHARS] if isinstance(value, str) else ""


def _extract_report_text(response: dict[str, Any]) -> str:
    """The model's text, read from response.done rather than from the deltas.

    One event, atomic, present on both API generations -- so the report cannot
    be half-assembled from a stream that stopped. `type` is "text" on one
    surface and "output_text" on the other, exactly like the audio events.
    """
    parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "output_text"):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _parse_report(raw: str) -> dict[str, Any] | None:
    """The model's text -> a validated scorecard, or None if there is not one.

    Defensive by design, because the alternative was a json_schema parameter
    that is unverified on one of the two API generations -- and a rejected
    parameter costs the ENTIRE report. So the JSON is demanded in the
    instructions and salvaged here.

    First "{" to last "}" also strips a ``` fence for free, which is why there
    is no separate fence-stripping step to keep in sync with it.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    report: dict[str, Any] = {
        "overall": _report_score(parsed.get("overall")),
        "communication": _report_score(parsed.get("communication")),
        "domain": _report_score(parsed.get("domain")),
        "structure": _report_score(parsed.get("structure")),
        "strengths": _report_strings(parsed.get("strengths"), _MAX_REPORT_LIST_ITEMS),
        "improvements": _report_strings(parsed.get("improvements"), _MAX_REPORT_LIST_ITEMS),
        "drill": _report_paragraph(parsed.get("drill")),
        "summary": _report_paragraph(parsed.get("summary")),
    }
    if (
        all(report[key] is None for key in ("overall", "communication", "domain", "structure"))
        and not report["summary"]
        and not report["strengths"]
    ):
        # Well-formed JSON that says nothing about the interview is not a
        # report. Calling it one would show the student an empty scorecard and
        # record a success; "unparseable" is the honest answer.
        return None
    return report


# ---------------------------------------------------------------------------
# The records an engine hands to the router's writers
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _TurnRecord:
    """The interview context of one turn -- everything a `messages` row cannot carry.

    Handed to the turn writer alongside (sender, text, provider_turn_id) so the
    two inserts happen in one transaction. Frozen because it is composed once,
    at emit time, and read on a worker thread afterwards: a mutable record could
    be rewritten by the next turn while the previous one is still being written.
    """

    seq: int
    phase: str
    transcription_status: str
    answer_quality: str | None
    counted_as_answer: bool
    is_partial: bool


@dataclass(frozen=True, slots=True)
class _ReportRecord:
    """The scorecard, on its way to interview_evaluations. One per interview."""

    status: str
    report: dict[str, Any] | None
    raw_response: str
    model: str


@dataclass(frozen=True, slots=True)
class _SessionOutcome:
    """How an interview ended, for the one UPDATE that closes its record.

    `report_status` is None when the scorecard was never even attempted (the
    session ended before WRAP_UP), which is what the writer turns into an
    evaluation row saying `unavailable` -- a mentor then reads "no report: hit
    the 15-minute cap" instead of a blank screen, and a MISSING row (which says
    nothing at all) never happens.
    """

    status: str
    close_code: int
    terminal_reason: str
    final_phase: str
    answers_accepted: int
    turns_emitted: int
    turns_persisted: int
    upstream_session_id: str | None
    report_status: str | None

    # What was kept of the student's voice. DEFAULTED, so that every existing
    # construction of this record (and its tests) still compiles and still means
    # exactly what it meant: nothing was recorded. `audio_recorded` is the field
    # to branch on and `audio_path` is not -- app/models/interview.py sets out
    # the four different facts a NULL path collapses into one.
    #
    # `audio_truncated` can be True while `audio_recorded` is False: a capture
    # that was stopped before its first flush ended early AND kept nothing, and
    # the two flags say different things about it.
    audio_recorded: bool = False
    audio_path: str | None = None
    audio_bytes: int | None = None
    audio_duration_ms: int | None = None
    audio_truncated: bool = False


class _TurnWriteRefused(Exception):
    """The conversation this interview writes into no longer accepts turns.

    Raised by the turn writer in routers/interview.py when
    conversations.append_message refuses (the student cleared the thread from
    another tab, or retention purged it mid-call). That function's CALL-SITE
    CONTRACT says a fire-and-forget writer must let the refusal reach its own
    `except Exception` -- where the connection id and the provider turn id are --
    and then END THE SESSION, because both realtime callers resolve
    conversation_id once at socket open and reuse it for fifteen minutes, so one
    dropped-turn line per turn for the rest of the call is noise wrapped around a
    record that is already lost.

    It is a relay-owned type rather than `conversations.ConversationGone`
    reaching in here for one reason: this module imports no ORM model and no
    database code AT ALL (see the header, and rule 1), and that is what lets
    the engines' own tests drive the whole turn protocol with no Postgres.
    The writer translates at the boundary where it already holds a Session. It is
    NOT a swallow -- the refusal is neither ignored nor folded into the
    IntegrityError branch, which is exactly what that contract forbids.
    """


class _SessionEnded(Exception):
    """A deliberate end of the interview, carrying the code both sockets close with.

    Raised by the watchdog or by a client `reep.end`. Raising (rather than
    setting a flag) is what makes the TaskGroup cancel the sibling pumps.
    """

    __slots__ = ("code", "reason")

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(f"{code} {reason}")
        self.code = code
        self.reason = reason


class _ConnLog(logging.LoggerAdapter):
    """Binds the connection id (and the upstream session id, once known) to every line.

    The ids also land on the LogRecord as attributes, so a JSON formatter picks
    them up as fields rather than having to re-parse the message.
    """

    def process(self, msg: Any, kwargs: Any) -> tuple[str, Any]:
        extra = self.extra or {}
        session_id = extra.get("session_id") or "-"
        # logging.LoggerAdapter.process is the ONLY hook that copies `extra` onto
        # the LogRecord. Overriding it without re-setting this key left every
        # record with no conn_id/session_id attribute at all, so the JSON
        # formatter this docstring promises to serve got nothing and the ids were
        # recoverable only by regex over the message. A caller-supplied `extra`
        # wins, so a per-call field is never clobbered by the binding.
        kwargs["extra"] = {**extra, **(kwargs.get("extra") or {})}
        return f"[conn={extra.get('conn_id')} session={session_id}] {msg}", kwargs


# ---------------------------------------------------------------------------
# The concurrency caps
# ---------------------------------------------------------------------------

# What refused an acquire, or "" for success. Two caps share one call because a
# two-step acquire is a leak waiting to happen: the first step succeeds, the
# second refuses, and the slot the first took is released only on the paths
# somebody remembered.
_REFUSED_BY_WORKER: Final[str] = "worker"
_REFUSED_BY_USER: Final[str] = "user"


class _ConnectionLimiter:
    """Per-worker AND per-user caps on concurrent interviews, without ever blocking.

    A student who cannot start must be TOLD so (close 1013 / 4012) rather than
    queued: queueing shows a spinner while their slot is not running, and the
    15-minute cap they are waiting for has not started either.

    THE PER-USER DICT IS THE POINT (audit H1). The worker cap counts sessions and
    never asks whose they are, so one student looping
    `new WebSocket('/api/interview')` from devtools took every slot on the worker
    and everyone else was answered 1013. Each of those sockets authenticates,
    opens an upstream Realtime session and BILLS from the handshake's
    response.create with no microphone input at all -- so the abuse is cheap to
    mount, expensive to absorb, and looks exactly like a capacity incident.

    Deliberately NOT routers/voice.py's `_TOKEN_GRANTS.try_acquire(user_id,
    limit)`, which was written for the same rule one layer up and is not reusable
    here: that one is a TTL-EXPIRING GRANT for a stateless token nobody holds,
    while this is a HELD resource with an explicit release. Reusing it would mean
    a slot that expires while its socket is still open.

    asyncio.Semaphore has no acquire_nowait(), so the test and the increment are
    written out. There is no await between them and asyncio is single-threaded,
    which makes the pair atomic by construction -- no lock is needed or useful.
    (routers/voice.py's version DOES take a lock because it is called from
    threadpooled sync endpoints; this one only ever runs on the event loop.)
    """

    __slots__ = ("_limit", "_per_user_limit", "_active", "_by_user")

    def __init__(self, limit: int, per_user_limit: int) -> None:
        self._limit = limit
        self._per_user_limit = per_user_limit
        self._active = 0
        self._by_user: dict[str, int] = {}

    def try_acquire(self, user_id: str) -> str:
        """Take a slot for `user_id`. Returns "" on success, else which cap refused.

        The worker cap is checked FIRST: when the process is genuinely full, the
        honest answer is "the server is full", not "you have too many" -- a
        student holding one legitimate interview would otherwise be told they
        were the problem.
        """
        if self._active >= self._limit:
            return _REFUSED_BY_WORKER
        held = self._by_user.get(user_id, 0)
        if held >= self._per_user_limit:
            return _REFUSED_BY_USER
        self._active += 1
        self._by_user[user_id] = held + 1
        return ""

    def release(self, user_id: str) -> None:
        # Guard against a double release turning the counter negative, which
        # would quietly raise the effective cap above the configured one.
        if self._active > 0:
            self._active -= 1
        held = self._by_user.get(user_id, 0)
        if held <= 1:
            # POPPED, not left at zero. Keyed by user id, this dict would
            # otherwise gain one permanent entry per student who ever interviewed
            # on this worker -- a slow leak on a process designed to run for
            # weeks, and one no test would ever notice.
            self._by_user.pop(user_id, None)
        else:
            self._by_user[user_id] = held - 1

    def active_for(self, user_id: str) -> int:
        return self._by_user.get(user_id, 0)

    @property
    def active(self) -> int:
        return self._active

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def per_user_limit(self) -> int:
        return self._per_user_limit


# ---------------------------------------------------------------------------
# Closing down
# ---------------------------------------------------------------------------

async def _close_downstream(websocket: WebSocket, code: int, reason: str) -> None:
    """Close the browser socket, once, without ever masking the real cause.

    Both Starlette states are checked: the client half goes DISCONNECTED as soon
    as the peer's close frame is read, and sending into that raises RuntimeError.
    """
    if (
        websocket.client_state is not WebSocketState.CONNECTED
        or websocket.application_state is not WebSocketState.CONNECTED
    ):
        return
    try:
        await websocket.close(code=code, reason=_close_reason(reason))
    except Exception as exc:
        # Logged, never swallowed silently -- but never re-raised either: this
        # runs in a `finally`, where raising would replace the exception that
        # actually ended the interview with a teardown detail.
        log.warning("Failed to close browser socket cleanly: %s", exc)


def ask_all_sessions_to_stop(sessions: set[InterviewEngine]) -> None:
    """Ask every live interview to close ITSELF, with a real code and reason.

    Never blocks and never awaits, so it is safe from a lifespan teardown. Each
    session's own watchdog does the work, which is what turns an abrupt 1006
    into a 1001 the client has a sentence for.
    """
    if not sessions:
        return
    log.info("Shutdown requested: asking %d live interview(s) to close", len(sessions))
    for session in tuple(sessions):
        session.request_stop(_CLOSE_GOING_AWAY, "Server shutting down")
