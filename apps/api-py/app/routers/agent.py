# =============================================================================
# SUPERSEDED AS THE STUDENT-FACING ASSISTANT — 2026-08. RETAINED FOR ROLLBACK.
#
# The student-facing assistant is now the realtime mock interviewer in
# app/routers/interview.py: a WebSocket relay between the student's browser and
# the OpenAI Realtime API, documented in docs/interview-assistant.md. The
# Angular assistant screen no longer opens a text chat against this module.
#
# NOTHING HAS BEEN DELETED. This file stays mounted and working, as do
# app/ai/orchestrator.py, app/assistant_tools.py, app/knowledge.py,
# app/ai/llm.py and app/eval/. Rolling back is re-pointing the client at
# /api/agent/ask — no code has to be restored from git. Deleting the old path
# in the same change that introduces its replacement is how a rollback stops
# being possible; do not tidy this away until the interviewer has held up in
# front of real students.
#
# 2026-09 UPDATE: /api/agent/ask HAS A UI CALLER AGAIN. The design handoff
# added a "REEP Agent" text screen — the Knowledge-Base helper, distinct from
# the mock interviewer — at apps/web/src/app/features/agent/. It calls /ask,
# /history, /conversation and /feedback, so AGENT_RUNS_COLLECTED is True again
# and the feedback tombstone below has retired itself. The interviewer is
# unchanged and still writes no runs; only this text surface does.
#
# SUPERSEDED entry points — still mounted, still functional, no UI caller:
#   POST   /api/agent/ask              was the primary path; replaced by the
#                                      interview WebSocket in 2026-08 and
#                                      re-pointed at by the REEP Agent screen in
#                                      2026-09 (see the update above)
#   POST   /api/agent/chat             no UI caller before or after; still the
#                                      subject of tests/test_conversations.py
#   POST   /api/agent/chat/stream      no caller before or after (SSE, dead)
#   GET    /api/agent/runs             no caller before or after
#   GET    /api/agent/knowledge/search test-only (tests/test_knowledge.py); the
#                                      orchestrator calls knowledge.search()
#                                      directly, not this HTTP route
#
# STILL LIVE — do NOT retire these along with the ones above:
#   GET    /api/agent/history          the transcript read for the assistant
#                                      screen. Interview turns are persisted
#                                      through app/conversations.py into the
#                                      SAME conversations/messages tables, and
#                                      conversations.history() filters on
#                                      is_final only — never on channel — so
#                                      this endpoint returns interview turns
#                                      unchanged, alongside typed ones.
#                                      One memory bank.
#   DELETE /api/agent/conversation     the only way a student clears the thread
#                                      the interviewer writes into (204).
#   POST   /api/agent/feedback         live, but CONDITIONAL: it 404s unless an
#                                      agent_runs row exists whose actor_id is
#                                      the caller, and the client only ever
#                                      learns a run_id from AskOut.run_id. The
#                                      interview path writes no AgentRun rows,
#                                      so on a student account it 404s on every
#                                      request and the thumbs-up/down UI simply
#                                      stops rendering (it is gated on
#                                      `turn.structured?.run_id`) — no console
#                                      error, no failing build. That 404 is now
#                                      a TOMBSTONE: when the caller owns no runs
#                                      at all it says why, so a log full of them
#                                      reads as a retirement, not a fault. Still
#                                      404, still indistinguishable from
#                                      "not yours" — see the handler.
#   GET    /api/agent/metrics          live (the Main Admin, 403 otherwise). It
#                                      never errors, but every AgentRun-derived
#                                      counter froze when runs stopped being
#                                      written. The payload now SAYS so
#                                      (`agent_runs.collected` + the
#                                      `last_run_at` that dates the freeze)
#                                      instead of reporting a bare 0, and
#                                      `turns_by_channel` off Message.channel —
#                                      voice AND interview — is the live source
#                                      beside the feedback counts.
#
# The module docstring below predates /ask, /knowledge/search, /feedback and
# /metrics and lists only five of the nine routes. It is kept verbatim rather
# than rewritten, so what this module was on the day it was superseded is still
# legible.
# =============================================================================

"""Text chat with a SERVER-OWNED, persistent conversation.

The conversation is ALWAYS derived from the authenticated session — the client
never sends a session_id or conversation_id for writing or for deciding whose
history to read. This closes the P0 where a client-chosen `assistant-${userId}`
let a signed-in user read/write another user's thread.

  POST   /api/agent/chat          { message } -> { reply, conversation_id, model }
  POST   /api/agent/chat/stream   { message } -> SSE (same server-owned conversation)
  GET    /api/agent/history       -> { conversation_id, turns } for the CALLER
  DELETE /api/agent/conversation  -> soft-clears the caller's conversation, 204
  GET    /api/agent/runs          -> the caller's recent audit rows

The LLM goes through the universal adapter (app/ai/llm.py). The egress gate still
applies: this is a general conversational assistant, so `carries_student_data`
stays False; wire it True on any path that injects a student's private records.
Provider/exception detail is logged server-side and NEVER surfaced to the client.
"""

import json
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import conversations as convo
from .. import knowledge
from ..ai import orchestrator
from ..ai.llm import complete_chat, llm_config, stream_chat
from ..db import SessionLocal, get_db
from ..governance import require_capability, require_feature
from ..identity import get_current_session
from ..models.agent_run import AgentRun, AgentRunStatus
from ..models.conversation import Message
from ..models.feedback import AssistantFeedback, FeedbackRating
from ..models.user import Role
from ..policies import STAFF_ROLES
from ..ratelimit import llm_rate_limited
from ..redaction import redact_pii

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

SYSTEM_PROMPT = (
    "You are the REEP student assistant for a college placement dashboard. Answer "
    "clearly and concisely, and use the earlier conversation for context. You are a "
    "general helper and do not have direct access to a student's private records; if "
    "asked for specific marks/attendance, say those come from the authenticated "
    "records view."
)

# Keep the replayed context bounded so a long session can't blow the token window.
HISTORY_LIMIT = 40

# Shown to the client when the provider ANSWERED with a failure — a bad key, an
# exhausted quota, a 5xx, a malformed body — or when the adapter itself threw.
# Those are faults an operator has to see as faults, so they stay a 502. The real
# cause is logged server-side; never leaked to the caller.
FRIENDLY_ERROR = "The assistant is temporarily unavailable, please try again."

# ...and the other half. A TRANSPORT fault means the provider was never reached
# at all: no route, DNS gone, TLS reset, connect/read timeout. From the student's
# side that is indistinguishable from "no model is configured on this box", and
# /student/resume/generate already answers that case with a deterministic draft
# and an honest used_ai=false rather than a 5xx. This surface now does the same,
# because a 502 on a laptop that simply has no internet is a dead end the student
# can neither read nor act on.
#
# The split is the point: unreachable degrades, answered-with-an-error escalates.
# Collapsing both into a cheerful 200 would bury a wrong API key behind a polite
# sentence for as long as nobody reads the log.
UNREACHABLE_REPLY = (
    "I could not reach the assistant model just now, so there is no AI answer to "
    "this one. Your message has been saved — send it again in a moment. If it "
    "keeps happening, the model provider is unreachable from this server."
)
UNREACHABLE_NOTE = (
    "No model was reachable, so nothing here was composed by one. The cause is in "
    "the server log."
)

# Whether anything a user can reach still writes AgentRun rows. It was False
# from 2026-08 (the realtime mock interviewer replaced the /api/agent/ask
# assistant screen, and the interview relay persists turns through
# app/conversations.py without writing runs) until 2026-09, when the REEP Agent
# text screen (apps/web/src/app/features/agent/) re-pointed a client at /ask:
# every question it sends lands in `ask()` below, which persists one run per
# turn through `_persist_run` and hands the id back as AskOut.run_id for the
# screen's thumbs-up/down. Stated as a CONSTANT rather than derived from the row
# count on purpose — derive it and one developer exercising /ask in Swagger flips
# the dashboard to "collecting" while no real surface writes anything. If the
# agent screen is ever removed again, flip this back to False in the same change:
# the feedback tombstone and the metrics note key off it and need no second edit.
AGENT_RUNS_COLLECTED = True

# Only ever sent while AGENT_RUNS_COLLECTED is False.
SUPERSEDED_RUNS_NOTE = (
    "AgentRun rows stopped being written when the realtime mock interviewer "
    "replaced the /api/agent/ask assistant screen (2026-08). These counters are a "
    "frozen history, not a live zero — read turns_by_channel for what is still "
    "being collected."
)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatOut(BaseModel):
    reply: str
    conversation_id: str
    model: str
    # The same honest-degrade pair /student/resume/generate returns: an answer the
    # student can act on, plus a flag saying no model wrote it. Defaulted so every
    # existing caller and test keeps the shape it already reads. `note` explains
    # WHY in plain language and never carries provider or exception detail — that
    # is logged server-side (see the module docstring).
    used_ai: bool = True
    note: str | None = None


class HistoryOut(BaseModel):
    conversation_id: str
    turns: list[dict]


class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ActionOut(BaseModel):
    label: str
    route: str
    reason: str


class SourceOut(BaseModel):
    label: str
    type: str  # "student-record" | "policy"


class AssistantResponse(BaseModel):
    answer: str
    actions: list[ActionOut] = []
    sources: list[SourceOut] = []
    limitations: list[str] = []


class AskOut(AssistantResponse):
    conversation_id: str
    model: str
    run_id: str  # the AgentRun id for THIS turn — attach feedback to it


def _append(db: Session, conversation_id: str, sender: str, content: str) -> None:
    """conversations.append_message with the cleared-mid-request race mapped to 404.

    get_or_create resolved a LIVE conversation a moment before each of these
    calls, and "a moment" is enough: DELETE /api/agent/conversation from a second
    tab lands between the two, and append_message then refuses rather than
    writing a turn nobody will ever see again. 404 is the answer assert_owner
    already gives for a cleared thread, so the client hears one story about a
    conversation that is gone no matter which layer noticed it first.
    """
    try:
        convo.append_message(db, conversation_id, sender, content)
    except convo.ConversationGone:
        # conversations.append_message already logged the drop with its cause.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
        ) from None


def _persist_run(
    db: Session,
    session: dict,
    question: str,
    answer: str,
    outcome: AgentRunStatus,
    model: str | None,
    started: datetime,
    trace: list | None = None,
    citations: list | None = None,
    intent: str | None = None,
    resolved: bool | None = None,
) -> str:
    """One audit row per question — scope stamped at run time (mirrors the
    Next.js AgentRun store). The structured assistant path stores its actions in
    `trace` and its sources in `citations`, and stamps the grounding signal
    (`intent`, `resolved`). Returns the new run's id so the caller can hand it to
    the client for feedback."""
    scope = "self" if session.get("role") == "STUDENT" else "programme"
    run = AgentRun(
        actor_id=session["userId"],
        role=Role(session["role"]),
        scope=scope,
        question=question,
        answer=answer,
        status=outcome,
        trace=trace or [],
        citations=citations or [],
        model=model,
        intent=intent,
        resolved=resolved,
        duration_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
    )
    db.add(run)
    db.commit()
    return run.id


def _require_agent_access(db: Session, session: dict) -> None:
    """`mentor.agent` for staff, the `student.agent` FEATURE for a student.

    TWO INSTRUMENTS, ONE ENDPOINT, AND THEY ARE NOT INTERCHANGEABLE — that is
    the whole reason this function has a role branch instead of a single call.

    THE ROLE BRANCH IS THE WHOLE DESIGN AND IT CANNOT BE AN UNCONDITIONAL CALL.
    `ROLE_BASELINE["STUDENT"]` and `["ALUMNI"]` are `frozenset()`, and a student
    reaches this endpoint every day — from /student/agent and from the orb's
    "Type instead". `require_capability(db, session, "mentor.agent")` on the way
    in would 403 every student in the deployment on the deploy that shipped it,
    which is the failure mode `CAPABILITIES`' own docstring calls "a deny-by-
    default rollout". Whether a STUDENT has the agent at all is a FeatureOverride
    question (`student.agent`), which is allow-by-default and answered with the
    override's own message — not a capability, which is deny-by-default and
    answered with "ask an administrator". B2.2 made that second half real; see
    the branch below.

    The capability half gates the STAFF surface: the REEP Agent screen routed at
    /mentor/agent and /admin/agent. Both staff roles hold `mentor.agent` by
    baseline today, so nothing changes for anybody — the point is that the key
    now HAS a call site, so the Governance row means something the day the
    baseline moves or somebody scopes the grant, instead of being a switch
    wired to nothing.

    Placed on the three endpoints that reach a model (/chat, /chat/stream,
    /ask). /history and DELETE /conversation are a caller reading and clearing
    their OWN thread, which the interviewer also writes into: refusing those to
    a staff member without the capability would make an unrelated screen's
    transcript unreachable, and revoking a capability must not delete somebody's
    ability to clear their own data.
    """
    if str(session.get("role") or "") in STAFF_ROLES:
        require_capability(db, session, "mentor.agent")
        return
    # And the student half, which B2.2 filled in. The docstring above has said
    # since B2.1 that "whether a STUDENT has the agent at all is a
    # FeatureOverride question"; until now that sentence described an intention
    # and no code, so `student.agent` could be switched off in Governance and
    # the student kept chatting. Same three endpoints, opposite instrument:
    # allow-by-default, refused with the office's own message.
    #
    # `require_feature` takes the claim as-is: a STUDENT session with no
    # `studentId` (a User row with no Student row) is not somebody an override
    # can describe, so there is nothing to refuse and the orchestrator's own
    # non-student path already handles what it can answer for them.
    require_feature(db, session.get("studentId"), "student.agent")


@router.post(
    "/chat", response_model=ChatOut, dependencies=[Depends(llm_rate_limited)]
)
def chat(
    body: ChatIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ChatOut:
    cfg = llm_config()
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No LLM provider configured — set a provider key in apps/api-py/.env.",
        )

    started = datetime.now(timezone.utc)
    model_label = f"{cfg.provider}:{cfg.model}"

    _require_agent_access(db, session)
    # Server-owned: the conversation is derived from the session, never the body.
    conversation = convo.get_or_create(db, session["userId"], Role(session["role"]))
    conversation_id = conversation.id
    # Captured BEFORE the user turn is appended is not required (the predicate
    # counts assistant turns), but read it before the reply exists so the
    # greeting decision is made from the same state the answer was built on.
    first_reply = convo.awaiting_first_reply(db, conversation_id)
    _append(db, conversation_id, "user", body.message)
    turns = convo.history(db, conversation_id, limit=HISTORY_LIMIT)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *turns]

    # Release the pooled connection BEFORE the model call: complete_chat may
    # legally take LLM_TIMEOUT_MS, and holding a transaction open across it is
    # how a slow provider became a pool-exhaustion outage in the 2026-08 audit.
    # Everything below re-acquires a connection through the same session;
    # conversation_id is captured above so nothing touches the expired ORM row.
    db.commit()

    try:
        # carries_student_data=False, stated rather than inherited. `messages`
        # is SYSTEM_PROMPT plus this conversation's own turns -- no Student row
        # is read into it anywhere in this module, and the prompt itself tells
        # the model it has no access to private records.
        #
        # A student who TYPES their own marks into the chat box is a different
        # thing: rule 1 is that student data must not leave the machine
        # UNBIDDEN, and a person volunteering their own information in a
        # conversation they started has bidden it. What this flag governs is
        # the server composing a record into a prompt, which this path does not
        # do. If that ever changes -- a profile block, a marks summary, a
        # retrieved attendance figure -- this must become True in the same diff.
        reply = complete_chat(messages, carries_student_data=False, max_tokens=1024)
    except httpx.TransportError as exc:
        # The provider was never reached — see UNREACHABLE_REPLY. Degraded, not
        # failed: 200 with an honest answer and used_ai=false, mirroring the
        # resume path. warning, not exception: a connect reset on a box with no
        # internet is an operational condition, and a full traceback per attempt
        # buries the provider-answered-with-an-error case that IS a bug.
        log.warning(
            "agent chat degraded — provider unreachable (conversation=%s): %r",
            conversation_id,
            exc,
        )
        _persist_run(db, session, body.message, "", AgentRunStatus.FAILED, model_label, started)
        # Neither the assistant turn nor the greeting stamp is written. A turn no
        # model composed must not be replayed to one as context on the next turn,
        # and it must not consume the student's single compulsory greeting — the
        # same two rules the streaming path spells out for its failed turns.
        return ChatOut(
            reply=UNREACHABLE_REPLY,
            conversation_id=conversation_id,
            model=model_label,
            used_ai=False,
            note=UNREACHABLE_NOTE,
        )
    except Exception:  # provider answered with a failure / adapter bug — loud, 502
        log.exception("agent chat LLM call failed (conversation=%s)", conversation_id)
        _persist_run(db, session, body.message, "", AgentRunStatus.FAILED, model_label, started)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=FRIENDLY_ERROR
        )

    if first_reply:
        reply = convo.open_with_greeting(reply)

    _append(db, conversation_id, "assistant", reply)
    if first_reply:
        convo.mark_greeted(db, conversation_id)
    _persist_run(db, session, body.message, reply, AgentRunStatus.ANSWERED, model_label, started)
    return ChatOut(reply=reply, conversation_id=conversation_id, model=model_label)


@router.post("/chat/stream", dependencies=[Depends(llm_rate_limited)])
def chat_stream(
    body: ChatIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events variant of /chat: streams the reply token-by-token as
    `data: {"delta": "..."}` frames, then `data: [DONE]`. Same server-owned
    conversation. The full turn is saved and an AgentRun row is written once the
    stream ends — from a fresh Session, since the request's own session is torn
    down when this handler returns and the generator keeps running."""
    cfg = llm_config()
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No LLM provider configured — set a provider key in apps/api-py/.env.",
        )

    started = datetime.now(timezone.utc)
    model_label = f"{cfg.provider}:{cfg.model}"

    _require_agent_access(db, session)
    # Resolve + persist the user turn on the request's own session, so the
    # conversation id is settled before the generator (with a fresh session) runs.
    conversation = convo.get_or_create(db, session["userId"], Role(session["role"]))
    conversation_id = conversation.id
    first_reply = convo.awaiting_first_reply(db, conversation_id)
    _append(db, conversation_id, "user", body.message)
    turns = convo.history(db, conversation_id, limit=HISTORY_LIMIT)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *turns]

    # Release the request session's connection NOW. Dependency teardown for a
    # StreamingResponse runs only after the last byte is sent, so the history
    # SELECT above would otherwise pin a pooled connection for the entire
    # stream — minutes, on a slow provider — while the generator does all its
    # real work on its own fresh SessionLocal anyway (the docstring's contract).
    db.commit()

    def event_stream():
        chunks: list[str] = []
        outcome = AgentRunStatus.ANSWERED
        yield f"data: {json.dumps({'conversation_id': conversation_id, 'model': model_label})}\n\n"
        # The greeting must reach a STREAMING client too. Emitted as the first
        # delta so the student sees it immediately, and kept in `chunks` so the
        # persisted turn matches exactly what was displayed — otherwise the
        # transcript and the screen disagree about what the assistant said.
        if first_reply:
            opening = f"{convo.GREETING}! "
            chunks.append(opening)
            yield f"data: {json.dumps({'delta': opening})}\n\n"
        try:
            # Same declaration as the blocking path above, for the same
            # reason -- see the comment there. The two must not drift: they
            # send the identical `messages`.
            for delta in stream_chat(
                messages, carries_student_data=False, max_tokens=1024
            ):
                chunks.append(delta)
                yield f"data: {json.dumps({'delta': delta})}\n\n"
        except Exception:  # provider/network/quota — reported in-band, never leaked
            log.exception("agent stream LLM call failed (conversation=%s)", conversation_id)
            outcome = AgentRunStatus.FAILED
            yield f"data: {json.dumps({'error': FRIENDLY_ERROR})}\n\n"

        reply = "".join(chunks)
        # A failed turn must not be stored as an assistant message OR consume
        # the greeting. Without this, a provider outage on the very first turn
        # leaves `chunks` holding nothing but "Jai Shri Gurudev! " — a bare
        # greeting persisted as the answer, replayed to the model as context on
        # the next turn, and the student never greeted again.
        model_said_something = outcome == AgentRunStatus.ANSWERED and any(
            c for c in chunks[1:] if c.strip()
        ) if first_reply else bool(reply.strip())

        # Fresh session: the injected request scope is already gone by now.
        with SessionLocal() as fresh:
            if model_said_something:
                try:
                    convo.append_message(fresh, conversation_id, "assistant", reply)
                    if first_reply:
                        convo.mark_greeted(fresh, conversation_id)
                except convo.ConversationGone:
                    # The widest window in this module: the response headers went
                    # out before the first token, and the student can clear the
                    # thread at any point while it streams. No status code is left
                    # to send, so the drop is recorded and the run is still
                    # written — append_message already logged it with its cause.
                    # The greeting is deliberately NOT stamped: the thread that
                    # was owed it no longer exists, and the fresh one is owed it
                    # in its own right.
                    outcome = AgentRunStatus.FAILED
            _persist_run(fresh, session, body.message, reply, outcome, model_label, started)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/ask", response_model=AskOut, dependencies=[Depends(llm_rate_limited)]
)
def ask(
    body: AskIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AskOut:
    """The STRUCTURED, tool-backed assistant path.

    Unlike /chat (free-form prose), this runs the orchestrator: it classifies the
    question, grounds the answer in read-only student tools or approved knowledge,
    and returns a typed AssistantResponse (answer + actions + sources +
    limitations). The conversation is server-owned (derived from the session), the
    user turn and the assistant's answer text are persisted, and one AgentRun audit
    row records the structured actions/sources in trace/citations.

    Personalised (student-data) intents only run for STUDENT accounts; a non-student
    still gets policy/general answers with a stated limitation. The orchestrator
    degrades gracefully on any LLM/provider fault, so this endpoint does not 502.
    """
    started = datetime.now(timezone.utc)
    cfg = llm_config()
    model_label = f"{cfg.provider}:{cfg.model}" if cfg else "deterministic"

    _require_agent_access(db, session)
    # Server-owned: the conversation is derived from the session, never the body.
    conversation = convo.get_or_create(db, session["userId"], Role(session["role"]))
    first_reply = convo.awaiting_first_reply(db, conversation.id)
    _append(db, conversation.id, "user", body.message)

    result = orchestrator.answer_question(
        db, session.get("studentId"), session.get("role"), body.message
    )

    # ONE choke point for the compulsory greeting on this surface. Every
    # orchestrator branch — the six deterministic student-data builders, policy,
    # general, the non-student refusal and the exception fallback — returns
    # through this single `result["answer"]`, so greeting here cannot be missed
    # by a path, and a future branch inherits it for free.
    if first_reply:
        result["answer"] = convo.open_with_greeting(result["answer"])

    _append(db, conversation.id, "assistant", result["answer"])
    if first_reply:
        # Stamp only AFTER the greeted answer is persisted.
        convo.mark_greeted(db, conversation.id)
    run_id = _persist_run(
        db,
        session,
        body.message,
        result["answer"],
        AgentRunStatus.ANSWERED,
        model_label,
        started,
        trace=result["actions"],
        citations=result["sources"],
        intent=result.get("intent"),
        resolved=result.get("resolved"),
    )

    # `result` also carries intent/resolved (grounding signal, persisted above);
    # only the AssistantResponse fields belong on the wire.
    return AskOut(
        answer=result["answer"],
        actions=result["actions"],
        sources=result["sources"],
        limitations=result["limitations"],
        conversation_id=conversation.id,
        model=model_label,
        run_id=run_id,
    )


@router.get("/history", response_model=HistoryOut)
def history(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> HistoryOut:
    """The CALLER's current conversation — resolved from the session, no id in.
    A user can only ever read their own thread; there is no parameter to name
    someone else's."""
    conversation = convo.current_conversation(db, session["userId"])
    if conversation is None:
        return HistoryOut(conversation_id="", turns=[])
    return HistoryOut(
        conversation_id=conversation.id,
        turns=convo.history(db, conversation.id, limit=HISTORY_LIMIT),
    )


@router.delete("/conversation", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> Response:
    """Soft-clear the caller's current conversation. The next chat opens a fresh
    thread. Scoped to the session — a client cannot clear anyone else's."""
    convo.clear(db, session["userId"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class RunOut(BaseModel):
    id: str
    scope: str
    question: str
    status: str
    model: str | None
    duration_ms: int
    created_at: datetime


@router.get("/runs", response_model=list[RunOut])
def runs(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[RunOut]:
    """The caller's own recent assistant runs (audit trail)."""
    rows = db.scalars(
        select(AgentRun)
        .where(AgentRun.actor_id == session["userId"])
        .order_by(AgentRun.created_at.desc())
        .limit(50)
    ).all()
    return [
        RunOut(
            id=r.id,
            scope=r.scope,
            question=r.question,
            status=r.status.value,
            model=r.model,
            duration_ms=r.duration_ms,
            created_at=r.created_at,
        )
        for r in rows
    ]


class KnowledgeHit(BaseModel):
    chunk_text: str
    document_title: str
    source_type: str
    source_url: str | None
    anchor: str | None
    score: float


class KnowledgeSearchOut(BaseModel):
    results: list[KnowledgeHit]


@router.get("/knowledge/search", response_model=KnowledgeSearchOut)
def knowledge_search(
    q: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> KnowledgeSearchOut:
    """Retrieve APPROVED Knowledge-Base chunks that ground an answer to `q`.

    STUDENT-only and scoped to the 'student' audience: this surfaces the
    "explain the rules" layer (policy/FAQ/guidance), never any live student
    fact. Returns an empty list when nothing approved matches, so the caller can
    say "no approved answer" rather than inventing one. Meant for the
    frontend/orchestrator to build grounded, cited replies later.
    """
    if session.get("role") != Role.STUDENT.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Knowledge search is available to student accounts.",
        )
    hits = knowledge.search(db, q, audience="student", limit=5)
    return KnowledgeSearchOut(results=[KnowledgeHit(**h) for h in hits])


# --- Feedback ----------------------------------------------------------------


class FeedbackIn(BaseModel):
    run_id: str
    rating: FeedbackRating
    note: str | None = Field(default=None, max_length=2000)


class FeedbackOut(BaseModel):
    ok: bool


@router.post("/feedback", response_model=FeedbackOut)
def feedback(
    body: FeedbackIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> FeedbackOut:
    """Rate the assistant turn identified by `run_id`.

    The caller MUST own the run (AgentRun.actor_id == the session user) — a run
    owned by anyone else is reported as 404, identical to a run that doesn't
    exist, so feedback can't be used to probe whether another user's run id is
    real. One row per (run, owner): a re-vote UPSERTs, never duplicates. The
    free-text note is PII-redacted before it is stored.

    TOMBSTONE: since the interviewer replaced the agent UI nothing a student can
    reach writes an AgentRun, so on those accounts this 404s on EVERY request and
    the thumbs-up/down control (gated on `turn.structured?.run_id`) simply stops
    rendering — no console error, no failing build, and anyone reading the logs
    sees breakage. The 404 for a caller who owns no runs at all now says so in
    words. Still 404, and still byte-identical between "not yours" and "does not
    exist": the discriminator is a count of the CALLER'S OWN rows, so it discloses
    nothing about the run id they asked about and the no-existence-leak contract
    is untouched.
    """
    run = db.get(AgentRun, body.run_id)
    if run is None or run.actor_id != session["userId"]:
        # No existence leak: not-found and not-owned look the same.
        detail = "Run not found."
        # Gated on the constant as well as the count, so the tombstone retires
        # itself: flip AGENT_RUNS_COLLECTED back to True on rollback and this
        # goes back to a bare "Run not found." without a second edit. A caller
        # who DOES own runs and merely mistyped an id keeps the plain answer —
        # telling them they have none would be a lie.
        if not AGENT_RUNS_COLLECTED:
            owned_runs = (
                db.scalar(
                    select(func.count())
                    .select_from(AgentRun)
                    .where(AgentRun.actor_id == session["userId"])
                )
                or 0
            )
            if owned_runs == 0:
                detail = (
                    "Run not found — this account has no assistant runs to rate. "
                    "Ratings attach to POST /api/agent/ask, which the realtime "
                    "mock interviewer superseded in 2026-08; interview turns are "
                    "persisted as conversation messages and create no run. This "
                    "endpoint stays mounted for the rollback path, not because it "
                    "is broken."
                )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

    note = redact_pii(body.note)
    existing = db.scalar(
        select(AssistantFeedback).where(
            AssistantFeedback.run_id == body.run_id,
            AssistantFeedback.owner_user_id == session["userId"],
        )
    )
    if existing is not None:
        existing.rating = body.rating
        existing.note = note
    else:
        db.add(
            AssistantFeedback(
                run_id=body.run_id,
                owner_user_id=session["userId"],
                rating=body.rating,
                note=note,
            )
        )
    db.commit()
    return FeedbackOut(ok=True)


# --- Metrics (the Main Admin) ------------------------------------------------


@router.get("/metrics")
def metrics(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    """Assistant health for staff — the Main Admin only (403 otherwise).

    Aggregates over AgentRun (+ AssistantFeedback + Messages): resolution/refusal
    rates from the `resolved` grounding signal, latency, and breakdowns by
    intent/model/status, plus per-channel turn and feedback tallies.

    TWO SOURCES, AND THE PAYLOAD SAYS WHICH IS WHICH. Every AgentRun-derived
    number froze when the realtime interviewer replaced the /api/agent/ask
    assistant screen — the relay persists turns through app/conversations.py and
    writes no runs — so a flat 0 here reads as an outage when it is a retired
    collector. `agent_runs` carries that verdict explicitly (`collected`, plus the
    `last_run_at` that dates the freeze), and `turns_by_channel` off Message.channel
    is the source that is still live: it is the same grouping AGENTS.md's voice
    runbook query uses, so "did the interview save anything" is answerable from
    this endpoint too. `total_runs` and the two rates keep their exact meaning —
    nothing that was already pinned has been redefined.

    NOTE: TTFT (time-to-first-token) is a STREAMING-path metric; /ask is
    non-streaming, so `avg_duration_ms` here is the compose-latency proxy (full
    request duration), not TTFT.
    """
    role = session.get("role")
    if role != Role.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Assistant metrics are available to the Main Admin.",
        )

    total_runs = db.scalar(select(func.count()).select_from(AgentRun)) or 0
    resolved_true = (
        db.scalar(
            select(func.count()).select_from(AgentRun).where(AgentRun.resolved.is_(True))
        )
        or 0
    )
    resolved_known = (
        db.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(AgentRun.resolved.isnot(None))
        )
        or 0
    )
    avg_duration = db.scalar(select(func.avg(AgentRun.duration_ms))) or 0

    resolution_rate = (resolved_true / total_runs) if total_runs else 0.0
    refusal_rate = (
        (1 - resolved_true / resolved_known) if resolved_known else 0.0
    )

    by_intent = {
        intent: count
        for intent, count in db.execute(
            select(AgentRun.intent, func.count())
            .where(AgentRun.intent.isnot(None))
            .group_by(AgentRun.intent)
        ).all()
    }
    by_model = {
        (model or "unknown"): count
        for model, count in db.execute(
            select(AgentRun.model, func.count()).group_by(AgentRun.model)
        ).all()
    }
    by_status = {
        st.value.lower(): count
        for st, count in db.execute(
            select(AgentRun.status, func.count()).group_by(AgentRun.status)
        ).all()
    }

    # One grouped scan instead of a count per channel, and the interview channel
    # arrives for free — the surface students actually use now had no number on
    # this dashboard at all. Same shape as the runbook's `group by channel`.
    turns_by_channel = {
        (channel or "unknown"): count
        for channel, count in db.execute(
            select(Message.channel, func.count()).group_by(Message.channel)
        ).all()
    }
    voice_turns = turns_by_channel.get("voice", 0)

    # Dates the freeze. "0 runs" and "0 runs, last one 2026-08-11" are the same
    # number and completely different findings; without the timestamp an operator
    # cannot tell a retired collector from one that died this morning.
    last_run_at = db.scalar(select(func.max(AgentRun.created_at)))

    fb_counts = {
        rating: count
        for rating, count in db.execute(
            select(AssistantFeedback.rating, func.count()).group_by(
                AssistantFeedback.rating
            )
        ).all()
    }
    feedback_out = {
        "helpful": fb_counts.get(FeedbackRating.HELPFUL, 0),
        "not_helpful": fb_counts.get(FeedbackRating.NOT_HELPFUL, 0),
        "report": fb_counts.get(FeedbackRating.REPORT, 0),
    }

    return {
        "total_runs": total_runs,
        "resolution_rate": round(resolution_rate, 4),
        "refusal_rate": round(refusal_rate, 4),
        "avg_duration_ms": round(float(avg_duration), 1),
        "by_intent": by_intent,
        "by_model": by_model,
        "by_status": by_status,
        "voice_turns": voice_turns,
        "turns_by_channel": turns_by_channel,
        "agent_runs": {
            "collected": AGENT_RUNS_COLLECTED,
            "rows": total_runs,
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "note": None if AGENT_RUNS_COLLECTED else SUPERSEDED_RUNS_NOTE,
        },
        "feedback": feedback_out,
    }
