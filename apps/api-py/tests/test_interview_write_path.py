"""Interview Engine v3 §6.5/§6.7 — the record write path, and the ways it can
lie without anyone noticing.

Everything here defends against the same class of failure, the one the AGENTS.md
voice runbook was written about: the interview sounded fine in the room and the
database disagrees. Under v3 there are now TWO records of a turn -- the
`messages` row every other channel writes, and the `interview_turns` row that
carries the phase, the transcription status and whether the turn advanced the arc
-- and the entire value of the second is being trustworthy when the first cannot
be. A writer that can leave them disagreeing about which turns happened would
have no value at all, so:

  * both inserts share ONE transaction, and a duplicate rolls BOTH back;
  * a blank transcript keeps its interview_turns row and skips only `messages`
    (L4) -- those are precisely the turns `transcription_status` exists for, and
    dropping them would leave the new column with nothing to say;
  * `append_message` refusing (ConversationGone, L5) ends the interview instead
    of dropping one turn per turn for the rest of a fifteen-minute session;
  * every session reaches a terminal status, through whichever of the three
    finalization layers gets there first, because a row stuck at `running` is a
    record that lies.

Plus audit H1's per-user concurrency cap, which is pure logic and needs no
database at all.

The database-backed tests own every row they touch and tear it down; the pure
ones use a fake browser socket, a fake writer and no Postgres.
"""

import asyncio
import json
import uuid

import pytest
from fastapi.websockets import WebSocketState
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.interview_matrix import SPECIALIZATIONS, InterviewPhase
from app.interview_core import (
    _CLOSE_IDLE,
    _CLOSE_INTERNAL,
    _CLOSE_OK,
    _ConnectionLimiter,
    _ReportRecord,
    _SessionEnded,
    _SessionOutcome,
    _TurnRecord,
    _TurnWriteRefused,
)
from app.interview_nova import NovaSonicSession
from app.models.conversation import Conversation, Message
from app.models.interview import InterviewEvaluation, InterviewSession, InterviewTurn
from app.models.user import Role, Student, User
from app.routers.interview import (
    _backstop_status,
    _finalize_if_running,
    _make_finalizer,
    _make_heartbeat,
    _make_report_writer,
    _make_turn_writer,
)


# ---------------------------------------------------------------------------
# Audit H1 — the per-user cap. No database, no socket.
# ---------------------------------------------------------------------------


class TestPerUserSessionCap:
    def test_one_student_cannot_take_every_slot_on_the_worker(self):
        """H1 itself. The worker cap counted sessions and never asked whose.

        Before this, one student looping `new WebSocket('/api/interview')` from
        devtools held every slot -- each socket billing an upstream Realtime
        session from the handshake with no microphone input -- and everybody else
        was answered 1013, which reads as a capacity incident rather than abuse.
        """
        limiter = _ConnectionLimiter(limit=10, per_user_limit=2)

        assert limiter.try_acquire("student-a") == ""
        assert limiter.try_acquire("student-a") == ""
        assert limiter.try_acquire("student-a") == "user"
        # And the worker is nowhere near full, which is the whole point.
        assert limiter.active == 2
        assert limiter.try_acquire("student-b") == ""

    def test_a_full_worker_is_reported_as_a_full_worker(self):
        """The order of the two checks is the difference between two sentences.

        A student holding one legitimate interview on a worker that happens to be
        full must be told "the server is busy", not "you have too many" -- the
        second sends them looking for a tab they do not have open.
        """
        limiter = _ConnectionLimiter(limit=1, per_user_limit=5)
        assert limiter.try_acquire("student-a") == ""
        assert limiter.try_acquire("student-a") == "worker"

    def test_a_released_slot_is_reusable_and_leaves_no_entry_behind(self):
        """The dict is keyed by user id, so it must shrink as well as grow.

        Left at zero instead of popped it gains one permanent entry per student
        who ever interviewed on this worker: a slow leak on a process designed to
        run for weeks, and one nothing would ever report.
        """
        limiter = _ConnectionLimiter(limit=10, per_user_limit=1)
        assert limiter.try_acquire("student-a") == ""
        limiter.release("student-a")
        assert limiter.active == 0
        assert limiter.active_for("student-a") == 0
        assert limiter._by_user == {}
        assert limiter.try_acquire("student-a") == ""

    def test_a_double_release_cannot_raise_the_effective_cap(self):
        limiter = _ConnectionLimiter(limit=2, per_user_limit=1)
        limiter.try_acquire("student-a")
        limiter.release("student-a")
        limiter.release("student-a")
        assert limiter.active == 0
        assert limiter.active_for("student-a") == 0

    def test_the_backstop_never_claims_an_interview_completed(self):
        """Layer 2 knows the close code and nothing else.

        1000 covers both "the scorecard was delivered" and "the student pressed
        End at minute three", so the backstop must not read it as `completed`. A
        record that under-claims is arguable; one that says an interview finished
        when nobody heard a verdict is not.
        """
        assert _backstop_status(_CLOSE_OK) == "abandoned"
        assert _backstop_status(_CLOSE_IDLE) == "abandoned"
        assert _backstop_status(_CLOSE_INTERNAL) == "failed"


# ---------------------------------------------------------------------------
# The relay half: what gets handed to the writer, and what a refusal does
# ---------------------------------------------------------------------------


class _FakeBrowser:
    """Starlette's WebSocket, reduced to what the engine calls."""

    def __init__(self) -> None:
        self.control: list[dict] = []
        # Checked before every send: a browser that has gone away must not be
        # written to. Starlette's real socket carries it.
        self.client_state = WebSocketState.CONNECTED

    async def send_text(self, text: str) -> None:
        self.control.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:  # pragma: no cover
        pass


class _FakeUpstream:
    """Bedrock, reduced to send(). The engine holds one; these tests read none
    of it — what they are about is what reaches the WRITER."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, event: dict) -> None:
        self.sent.append(event)


def _relay(on_turn=None, **kwargs) -> NovaSonicSession:
    """One engine, wired to fakes. Named `_relay` because every test below is
    about the writers, not about which engine drove them."""
    session = NovaSonicSession(
        _FakeBrowser(),
        "feed0feed0fe",
        on_turn=on_turn,
        specialization=SPECIALIZATIONS["hr"],
        **kwargs,
    )
    session._upstream = _FakeUpstream()
    return session


class TestWhatTheWriterIsHanded:
    def test_a_blank_transcript_still_reaches_the_writer(self):
        """L4, the engine half, and the trap this change was most likely to ship.

        The blank-text guard used to return before the writer was ever called, so
        a transcription that heard nothing left NO ROW ANYWHERE -- the exact
        turns v3 adds `transcription_status` to record.
        """
        seen: list[tuple[str, str, str, _TurnRecord]] = []

        async def scenario():
            relay = _relay(on_turn=lambda *a: seen.append(a))
            await relay._on_student_transcript("", "content-1")
            await asyncio.gather(*tuple(relay._writes), return_exceptions=True)

        asyncio.run(scenario())

        assert len(seen) == 1
        sender, text, turn_id, record = seen[0]
        assert (sender, text) == ("user", "")
        assert record.transcription_status == "empty"
        # And it did not advance the arc: an answer nobody heard is not an
        # answer, whatever else is true about it.
        assert record.counted_as_answer is False

    def test_a_counted_answer_carries_its_phase_and_its_tick(self):
        """The two columns a shared `messages` row can never hold.

        `phase` is read at emit time on purpose -- push it after the tick and
        every answer that moved the arc is filed under the phase it moved the
        arc INTO, which makes the record disagree with the interview.
        """
        seen: list[tuple] = []

        async def scenario():
            relay = _relay(on_turn=lambda *a: seen.append(a))
            await relay._on_student_transcript(
                "I led the campus fintech club for two years", "content-1"
            )
            await asyncio.gather(*tuple(relay._writes), return_exceptions=True)

        asyncio.run(scenario())

        record = seen[0][3]
        assert record.phase == InterviewPhase.OPENING.value
        assert record.counted_as_answer is True
        assert record.answer_quality == "accepted"
        assert record.seq == 1

    def test_an_interviewer_turn_is_never_judged_as_an_answer(self):
        """The columns that only make sense for a STUDENT turn stay empty.

        `answer_quality` on an interviewer turn would read as the model having
        been assessed, and `transcription_status` of 'ok' would claim a
        transcriber succeeded on a turn it never saw.

        Two tests stood here that went with the OpenAI relay: one for an answer
        split across two VAD segments (the split is a transcript-level concern
        on the Nova engine and is pinned in test_interview_nova.py), and one for
        `is_partial` on an interrupted question -- a distinction only an engine
        that can cancel its own response in flight can draw.
        """
        seen: list[tuple] = []

        async def scenario():
            relay = _relay(on_turn=lambda *a: seen.append(a))
            relay._emit_turn(
                "assistant",
                "So, tell me about yourself.",
                "nova-a1",
                status="not_applicable",
                quality=None,
            )
            await asyncio.gather(*tuple(relay._writes), return_exceptions=True)

        asyncio.run(scenario())

        record = seen[0][3]
        assert record.transcription_status == "not_applicable"
        assert record.answer_quality is None
        assert record.counted_as_answer is False
        assert record.is_partial is False

    def test_a_refused_turn_write_ends_the_interview(self):
        """L5, and it is not "swallow it".

        The student cleared this chat thread from another tab, so every remaining
        turn would be refused identically: one dropped-turn line per turn for the
        rest of a fifteen-minute call, wrapped around a record already lost. 1000
        because nothing failed -- they deliberately cleared it.
        """

        def refuse(sender, text, turn_id, record):
            raise _TurnWriteRefused("Conversation c1 was cleared")

        async def scenario():
            relay = _relay(on_turn=refuse)
            relay._emit_turn(
                "assistant",
                "So, tell me about yourself.",
                "nova-a1",
                status="not_applicable",
                quality=None,
            )
            await asyncio.gather(*tuple(relay._writes), return_exceptions=True)
            return relay

        relay = asyncio.run(scenario())
        assert relay._stop_requested.is_set()
        assert relay._stop_outcome == (_CLOSE_OK, "Conversation cleared")
        # And the turn is counted as emitted but NOT as persisted, which is what
        # makes turns_emitted > turns_persisted mean "dropped".
        assert (relay._turns_assistant, relay._turns_persisted) == (1, 0)

    def test_a_written_turn_counts_as_persisted(self):
        async def scenario():
            relay = _relay(on_turn=lambda *a: None)
            relay._emit_turn(
                "assistant",
                "So, tell me about yourself.",
                "nova-a1",
                status="not_applicable",
                quality=None,
            )
            await asyncio.gather(*tuple(relay._writes), return_exceptions=True)
            return relay

        relay = asyncio.run(scenario())
        assert relay._turns_persisted == 1


class TestLayerOne:
    """§6.7. A `running` row that is never closed is worse than no row: it is a
    record that lies, and a mentor reading it sees an interview still in
    progress."""

    def _finalize(self, code: int, reason: str, **state) -> _SessionOutcome:
        captured: list[_SessionOutcome] = []

        async def scenario():
            relay = _relay(on_finalize=captured.append)
            for key, value in state.items():
                setattr(relay, key, value)
            await relay._finalize_session(code, reason)

        asyncio.run(scenario())
        return captured[0]

    def test_an_interview_that_never_reached_the_verdict_is_abandoned(self):
        outcome = self._finalize(_CLOSE_IDLE, "No audio received for 2 minutes")
        assert outcome.status == "abandoned"
        assert outcome.close_code == _CLOSE_IDLE
        # The pair the schema's own example uses, so a support report pasting the
        # student's close code and the row agree word for word.
        assert outcome.terminal_reason == "4008 No audio received for 2 minutes"
        assert outcome.final_phase == InterviewPhase.OPENING.value
        # Which is what makes the writer record an 'unavailable' evaluation.
        assert outcome.report_status is None

    def test_a_normal_close_without_a_scorecard_is_not_completed(self):
        """1000 covers "the report was delivered" AND "the student pressed End
        at minute three". Calling the second one `completed` would put a
        finished-looking interview with no verdict in front of a mentor."""
        assert self._finalize(_CLOSE_OK, "Interview complete").status == "abandoned"

    def test_a_settled_scorecard_is_what_completed_means(self):
        outcome = self._finalize(
            _CLOSE_OK, "Interview complete", _report_settled=True, _report_status="ok"
        )
        assert outcome.status == "completed"
        assert outcome.report_status == "ok"

    def test_an_internal_error_is_recorded_as_a_failure(self):
        assert self._finalize(_CLOSE_INTERNAL, "Internal error").status == "failed"

    def test_finalization_happens_once(self):
        """Layer 2 is idempotent by predicate, not by this flag — but Layer 1
        must not write twice on its own account either."""
        captured: list[_SessionOutcome] = []

        async def scenario():
            relay = _relay(on_finalize=captured.append)
            await relay._finalize_session(_CLOSE_OK, "Interview complete")
            await relay._finalize_session(_CLOSE_INTERNAL, "Internal error")

        asyncio.run(scenario())
        assert len(captured) == 1

    def test_a_failed_finalization_never_changes_the_close_code(self):
        """The record is bookkeeping; the close code is what the student sees.
        A database that is down must not turn a completed interview into an
        error in their browser."""

        def explode(outcome):
            raise RuntimeError("postgres is on fire")

        async def scenario():
            relay = _relay(on_finalize=explode)
            await relay._finalize_session(_CLOSE_OK, "Interview complete")

        asyncio.run(scenario())  # no exception escapes

    def test_the_scorecard_is_handed_to_its_writer_before_the_socket_closes(self):
        """L3. Awaited, not fired: on the fire-and-forget path this would be the
        last write scheduled and the first the teardown drain cancels, so the
        scorecard would be the piece most likely to be missing."""
        captured: list[_ReportRecord] = []

        async def scenario():
            relay = _relay(on_report=captured.append)
            # _finish_report ends the interview itself: every report outcome,
            # good or bad, closes 1000 because the INTERVIEW completed.
            with pytest.raises(_SessionEnded):
                await relay._finish_report({"overall": 61}, "ok")
            return relay

        relay = asyncio.run(scenario())
        assert len(captured) == 1
        assert captured[0].status == "ok"
        assert captured[0].report == {"overall": 61}
        # And the payload still went to the browser, in that order: a row that
        # did not land must not cost the student the scorecard they just earned.
        assert relay._ws.control[-1]["type"] == "reep.report"


# ---------------------------------------------------------------------------
# The database half
# ---------------------------------------------------------------------------


@pytest.fixture
def subject():
    """A throwaway User + Student + Conversation + interview_sessions row.

    Owns everything it touches: these tests clear conversations and finalize
    sessions on purpose, and doing that to a shared fixture would take unrelated
    tests down with it.
    """
    with SessionLocal() as db:
        user = User(
            email=f"ivwrite-{uuid.uuid4().hex[:10]}@bgscet.ac.in",
            name="Interview Write Path Fixture",
            role=Role.STUDENT,
            password_hash="x",
        )
        db.add(user)
        db.flush()
        student = Student(user_id=user.id)
        db.add(student)
        conversation = Conversation(owner_user_id=user.id, role=Role.STUDENT)
        db.add(conversation)
        db.flush()
        session_row = InterviewSession(
            student_id=student.id,
            conversation_id=conversation.id,
            specialization="hr",
            conn_id="feed0feed0fe",
        )
        db.add(session_row)
        db.commit()
        ids = (user.id, student.id, conversation.id, session_row.id)

    yield type(
        "Subject",
        (),
        {
            "user_id": ids[0],
            "student_id": ids[1],
            "conversation_id": ids[2],
            "interview_session_id": ids[3],
        },
    )

    with SessionLocal() as db:
        # Student first: interview_sessions cascades off it, taking the turns and
        # the evaluation. The user then takes the conversation and its messages.
        db.execute(delete(Student).where(Student.id == ids[1]))
        db.execute(delete(User).where(User.id == ids[0]))
        db.commit()


def _record(seq: int = 1, **kwargs) -> _TurnRecord:
    return _TurnRecord(
        seq=seq,
        phase=kwargs.pop("phase", "probing"),
        transcription_status=kwargs.pop("transcription_status", "ok"),
        answer_quality=kwargs.pop("answer_quality", "accepted"),
        counted_as_answer=kwargs.pop("counted_as_answer", True),
        is_partial=kwargs.pop("is_partial", False),
    )


def _turns(interview_session_id: str) -> list[InterviewTurn]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(InterviewTurn)
                .where(InterviewTurn.interview_session_id == interview_session_id)
                .order_by(InterviewTurn.seq)
            )
        )


def _messages(conversation_id: str) -> list[Message]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(Message).where(Message.conversation_id == conversation_id)
            )
        )


@requires_db
class TestOneWritePathOneTransaction:
    def test_a_turn_writes_both_rows_and_links_them(self, subject):
        write = _make_turn_writer(
            subject.conversation_id, subject.interview_session_id
        )
        write("user", "I led the campus fintech club", "u:item_1", _record())

        turns = _turns(subject.interview_session_id)
        messages = _messages(subject.conversation_id)
        assert len(turns) == 1 and len(messages) == 1
        assert turns[0].content == "I led the campus fintech club"
        assert turns[0].speaker == "student"
        assert turns[0].phase == "probing"
        assert turns[0].counted_as_answer is True
        # The FK link is only obtainable because both inserts share one
        # transaction -- and it is the line that silently comes back NULL if the
        # session's savepoint is closed instead of committed.
        assert turns[0].message_id == messages[0].id
        assert messages[0].channel == "interview"

    def test_a_blank_transcript_keeps_the_record_and_skips_the_chat(self, subject):
        """L4, the writer half.

        A transcription that timed out is `content = ''` with a status saying
        why -- the entire reason interview_turns exists. `messages` gets nothing,
        because an empty chat bubble in GET /api/agent/history reads as "the
        student said nothing", which is the opposite of what happened.
        """
        write = _make_turn_writer(
            subject.conversation_id, subject.interview_session_id
        )
        write(
            "user",
            "",
            "u:item_1",
            _record(transcription_status="timeout", answer_quality=None,
                    counted_as_answer=False),
        )

        turns = _turns(subject.interview_session_id)
        assert len(turns) == 1
        assert turns[0].content == ""
        assert turns[0].transcription_status == "timeout"
        assert turns[0].message_id is None
        assert _messages(subject.conversation_id) == []

    def test_a_duplicate_turn_is_a_no_op_in_both_tables(self, subject):
        """DF2. Two writes for one turn can both pass append_message's
        read-then-insert dedup before either commits; the unique indexes are what
        actually hold, and the writer treats the loser as the no-op it is."""
        write = _make_turn_writer(
            subject.conversation_id, subject.interview_session_id
        )
        write("user", "first", "u:item_1", _record(seq=1))
        write("user", "first", "u:item_1", _record(seq=2))

        assert len(_turns(subject.interview_session_id)) == 1
        assert len(_messages(subject.conversation_id)) == 1

    def test_a_second_turn_reusing_a_provider_id_lands_in_neither_table(
        self, subject
    ):
        """The stated cost of sharing one transaction, pinned so nobody "fixes"
        it into two independent writers.

        An IntegrityError on EITHER table rolls back BOTH. Here `messages` dedups
        and hands back the stored row while `uq_interview_turn_provider` refuses
        the second interview_turns insert -- and the whole write unwinds, leaving
        the two records agreeing rather than one of them carrying a turn the
        other never heard of. Affordable precisely because the turn is already
        stored by the winner.
        """
        write = _make_turn_writer(
            subject.conversation_id, subject.interview_session_id
        )
        write("user", "first", "u:item_1", _record(seq=1))
        write("user", "second", "u:item_1", _record(seq=2))

        assert [t.content for t in _turns(subject.interview_session_id)] == ["first"]
        assert [m.content for m in _messages(subject.conversation_id)] == ["first"]

    def test_a_cleared_conversation_refuses_the_turn_rather_than_dropping_it(
        self, subject
    ):
        """L5. The refusal must reach the relay, which ends the session.

        Folded into the IntegrityError branch it would be swallowed as a
        harmless duplicate -- the precise silent loss ConversationGone was added
        to end.
        """
        with SessionLocal() as db:
            conversation = db.get(Conversation, subject.conversation_id)
            conversation.deleted_at = conversation.created_at
            db.commit()

        write = _make_turn_writer(
            subject.conversation_id, subject.interview_session_id
        )
        with pytest.raises(_TurnWriteRefused):
            write("user", "anybody there?", "u:item_1", _record())

        # Nothing half-written: both records agree the turn did not happen.
        assert _turns(subject.interview_session_id) == []
        assert _messages(subject.conversation_id) == []


def _session(interview_session_id: str) -> InterviewSession:
    with SessionLocal() as db:
        return db.get(InterviewSession, interview_session_id)


def _evaluations(interview_session_id: str) -> list[InterviewEvaluation]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(InterviewEvaluation).where(
                    InterviewEvaluation.interview_session_id == interview_session_id
                )
            )
        )


def _outcome(**kwargs) -> _SessionOutcome:
    base = {
        "status": "completed",
        "close_code": 1000,
        "terminal_reason": "1000 Interview complete",
        "final_phase": "wrap_up",
        "answers_accepted": 5,
        "turns_emitted": 11,
        "turns_persisted": 11,
        "upstream_session_id": "sess_abc",
        "report_status": "ok",
    }
    base.update(kwargs)
    return _SessionOutcome(**base)


@requires_db
class TestFinalizationLayers:
    def test_layer_1_closes_the_record(self, subject):
        _make_finalizer(subject.interview_session_id)(_outcome())

        row = _session(subject.interview_session_id)
        assert row.status == "completed"
        assert row.close_code == 1000
        assert row.final_phase == "wrap_up"
        assert row.answers_accepted == 5
        assert row.turns_persisted == 11
        assert row.ended_at is not None
        # An evaluation row already exists by this point on the real path, so
        # finalization must not add a second one claiming 'unavailable'.
        assert _evaluations(subject.interview_session_id) == []

    def test_a_session_that_never_reached_the_verdict_records_unavailable(
        self, subject
    ):
        """§5.3. A row saying "no report" is a record; a MISSING row says
        nothing at all and cannot be told from an interview that predates the
        scorecard."""
        _make_finalizer(subject.interview_session_id)(
            _outcome(
                status="abandoned",
                close_code=_CLOSE_IDLE,
                terminal_reason="4008 No audio received for 2 minutes",
                final_phase="probing",
                report_status=None,
            )
        )

        evaluations = _evaluations(subject.interview_session_id)
        assert len(evaluations) == 1
        assert evaluations[0].report_status == "unavailable"
        assert evaluations[0].overall_score is None
        assert _session(subject.interview_session_id).status == "abandoned"

    def test_an_existing_scorecard_survives_a_late_unavailable(self, subject):
        """DF6 from the other direction: first write wins, and the row the
        student was actually shown is the one that stays."""
        _make_report_writer(subject.interview_session_id)(
            _ReportRecord(status="ok", report={"overall": 61}, raw_response="{}",
                          model="gpt-realtime")
        )
        _make_finalizer(subject.interview_session_id)(_outcome(report_status=None))

        evaluations = _evaluations(subject.interview_session_id)
        assert len(evaluations) == 1
        assert evaluations[0].report_status == "ok"
        # And the session still finalized: the courtesy write failing must never
        # roll back the terminal status, which is the whole reason they are two
        # commits in that order.
        assert _session(subject.interview_session_id).status == "completed"

    def test_layer_2_does_nothing_once_layer_1_has_run(self, subject):
        """The `AND status = 'running'` predicate is the ONLY thing making the
        three layers idempotent against each other. Without it the least
        informed layer overwrites the most informed one."""
        _make_finalizer(subject.interview_session_id)(_outcome())
        _finalize_if_running(
            subject.interview_session_id, "feed0feed0fe", 1011, "Internal error"
        )

        row = _session(subject.interview_session_id)
        assert row.status == "completed"
        assert row.close_code == 1000
        assert row.terminal_reason == "1000 Interview complete"

    def test_layer_2_closes_a_record_layer_1_never_reached(self, subject):
        _finalize_if_running(
            subject.interview_session_id, "feed0feed0fe", 1011, "Internal error"
        )

        row = _session(subject.interview_session_id)
        assert row.status == "failed"
        assert row.close_code == 1011
        assert row.terminal_reason == "1011 Internal error"
        assert row.ended_at is not None


@requires_db
class TestHeartbeat:
    def test_the_heartbeat_moves_a_running_row(self, subject):
        """The only thing that makes an orphan detectable. A worker killed with
        -9 cannot finalize its own rows, and `started_at` alone cannot tell a
        long healthy interview from a dead one."""
        before = _session(subject.interview_session_id).heartbeat_at
        _make_heartbeat(subject.interview_session_id)()
        assert _session(subject.interview_session_id).heartbeat_at > before

    def test_the_heartbeat_leaves_a_closed_row_alone(self, subject):
        _make_finalizer(subject.interview_session_id)(_outcome())
        closed = _session(subject.interview_session_id).heartbeat_at
        _make_heartbeat(subject.interview_session_id)()
        assert _session(subject.interview_session_id).heartbeat_at == closed


@requires_db
class TestEvaluationWriter:
    def test_a_scorecard_is_stored_with_everything_the_student_is_shown(
        self, subject
    ):
        _make_report_writer(subject.interview_session_id)(
            _ReportRecord(
                status="ok",
                report={
                    "overall": 61,
                    "communication": 58,
                    "domain": None,
                    "structure": 70,
                    "strengths": ["clear STAR structure"],
                    "improvements": ["quantify outcomes"],
                    "drill": "Re-answer question two in under ninety seconds.",
                    "summary": "A solid first attempt.",
                },
                raw_response='{"overall": 61}',
                model="gpt-realtime",
            )
        )

        row = _evaluations(subject.interview_session_id)[0]
        assert row.report_status == "ok"
        assert row.overall_score == 61
        # NULL, never 0: a missing score and a zero mean opposite things to the
        # person reading them, and nothing here may invent one to fill the gap.
        assert row.domain_score is None
        assert row.strengths == ["clear STAR structure"]
        assert row.drill.startswith("Re-answer")
        assert row.model == "gpt-realtime"

    def test_a_failed_report_is_still_a_row(self, subject):
        _make_report_writer(subject.interview_session_id)(
            _ReportRecord(
                status="unparseable",
                report=None,
                raw_response="Here is your report! Sure thing.",
                model="gpt-realtime",
            )
        )

        row = _evaluations(subject.interview_session_id)[0]
        assert row.report_status == "unparseable"
        assert row.overall_score is None
        # None rather than [], because an empty list would claim the model listed
        # no strengths when in fact it listed nothing at all.
        assert row.strengths is None
        assert row.raw_response.startswith("Here is your report!")

    def test_the_first_evaluation_wins(self, subject):
        """DF6. The report deadline and a late response.done can each settle
        once, and the earlier row is the one whose status matches what the
        student was actually told."""
        write = _make_report_writer(subject.interview_session_id)
        write(_ReportRecord(status="timeout", report=None, raw_response="",
                            model="gpt-realtime"))
        write(_ReportRecord(status="ok", report={"overall": 90}, raw_response="{}",
                            model="gpt-realtime"))

        rows = _evaluations(subject.interview_session_id)
        assert len(rows) == 1
        assert rows[0].report_status == "timeout"
