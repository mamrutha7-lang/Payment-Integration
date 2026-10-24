"""Interview Engine v3 §6 — the four record tables, and the constraints that
have to BITE rather than merely exist.

Every guarantee pinned here is one the application code is allowed to lean on,
and each one is load-bearing for a specific failure the design names:

  * `uq_interview_turn_provider` — the SECOND dedup layer. The relay's own
    `_pending.pop` protects the state machine; this protects the row when a
    transcription is delivered twice, or a `.failed` lands after a `.completed`.
    The turn writer swallows the IntegrityError as the no-op it is, which is only
    safe if the constraint is really there.
  * `(interview_session_id, seq)` is deliberately NOT unique. That is tested too,
    because "someone added unique=True for tidiness" is a silent way to turn a
    cosmetic duplicate into a fire-and-forget writer that raises and loses turns.
  * `uq_interview_evaluation_session` — one scorecard per interview, which is
    what makes the evaluation write idempotent when the report's `response.done`
    arrives after the deadline already wrote `report_status='timeout'` (DF6).
  * The delete rules, all four of them, exercised as BULK deletes so it is the
    DATABASE being tested and not SQLAlchemy's in-memory cascade. `purge_expired`
    issues exactly that kind of bulk delete, so an ORM-only rule would pass a
    unit test and fail the retention job.
  * Finalize-exactly-once: three layers race to close a session and the only
    thing making them idempotent is the `status = 'running'` predicate. This
    pins the predicate, not the callers.
  * `content = ''` is legal (L4). A failed or timed-out transcription writes an
    empty string with a status saying why, and that row is the entire reason this
    table exists — a NOT NULL that also rejected '' would lose it.

Pure database tests: no TestClient, no socket, no relay. Skipped when Postgres is
unreachable (and REEP_REQUIRE_DB=1 turns that skip into a hard error, because a
green suite that verified none of this is worse than a red one).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from conftest import requires_db

from app.db import SessionLocal
from app.models.conversation import Conversation, Message
from app.models.interview import (
    InterviewConsent,
    InterviewEvaluation,
    InterviewSession,
    InterviewTurn,
)
from app.models.user import Role, Student, User

CONSENT_VERSION = "2026-08"


def _utc(**kw) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**kw)


@pytest.fixture
def subject():
    """A throwaway User + Student + Conversation + Message to hang interview rows
    off, torn down afterwards.

    It owns everything it touches rather than borrowing a seeded row: these tests
    delete students and conversations on purpose to prove cascades, and doing
    that to a shared fixture would take unrelated tests down with it.
    """
    with SessionLocal() as db:
        user = User(
            email=f"ivrec-{uuid.uuid4().hex[:10]}@bgscet.ac.in",
            name="Interview Records Fixture",
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
        message = Message(
            conversation_id=conversation.id,
            sender="user",
            channel="interview",
            content="hello",
        )
        db.add(message)
        db.commit()
        ids = (user.id, student.id, conversation.id, message.id)

    yield type(
        "Subject",
        (),
        {
            "user_id": ids[0],
            "student_id": ids[1],
            "conversation_id": ids[2],
            "message_id": ids[3],
        },
    )

    with SessionLocal() as db:
        # Student first (users.id is NOT ON DELETE CASCADE from students), which
        # cascades any surviving interview_sessions and their children; deleting
        # the user then takes the conversation, its messages and any consent
        # grants with it.
        db.execute(delete(Student).where(Student.id == ids[1]))
        db.execute(delete(User).where(User.id == ids[0]))
        db.commit()


def _session_row(subject, **overrides) -> InterviewSession:
    kwargs = dict(
        student_id=subject.student_id,
        conversation_id=subject.conversation_id,
        specialization="hr",
        status="running",
        conn_id=uuid.uuid4().hex[:12],
    )
    kwargs.update(overrides)
    return InterviewSession(**kwargs)


def _turn(session_id: str, seq: int, **overrides) -> InterviewTurn:
    kwargs = dict(
        interview_session_id=session_id,
        seq=seq,
        speaker="student",
        phase="probing",
        content="I led the campus fintech club.",
        transcription_status="ok",
        answer_quality="accepted",
        counted_as_answer=True,
        provider_turn_id=f"u:item_{seq}",
    )
    kwargs.update(overrides)
    return InterviewTurn(**kwargs)


# ---------------------------------------------------------------------------
# Turn identity
# ---------------------------------------------------------------------------
@requires_db
def test_turn_provider_id_is_unique_per_session(subject):
    """The same provider_turn_id cannot land twice in one interview.

    This is what makes the turn writer's `except IntegrityError: pass` correct
    rather than optimistic — a redelivered `.completed`, or a `.failed` racing a
    `.completed`, must be a no-op and not a duplicated turn in the transcript a
    mentor reads.
    """
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()
        sid = s.id

        db.add(_turn(sid, 1, provider_turn_id="u:item_abc"))
        db.commit()

        db.add(_turn(sid, 2, provider_turn_id="u:item_abc"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        assert db.scalar(
            select(func.count()).select_from(InterviewTurn).where(
                InterviewTurn.interview_session_id == sid
            )
        ) == 1


@requires_db
def test_turn_provider_id_is_scoped_to_its_own_session(subject):
    """Two different interviews may each carry `u:item_abc`.

    Upstream item ids are only unique within one realtime session, so a
    constraint on provider_turn_id ALONE would start rejecting perfectly good
    turns the first time two interviews collided — and the writer swallows
    IntegrityError, so the turns would vanish with no error anywhere.
    """
    with SessionLocal() as db:
        a, b = _session_row(subject), _session_row(subject)
        db.add_all([a, b])
        db.commit()

        db.add(_turn(a.id, 1, provider_turn_id="u:item_abc"))
        db.add(_turn(b.id, 1, provider_turn_id="u:item_abc"))
        db.commit()  # must NOT raise

        assert db.scalar(
            select(func.count()).select_from(InterviewTurn).where(
                InterviewTurn.provider_turn_id == "u:item_abc"
            )
        ) == 2


@requires_db
def test_turns_with_no_provider_id_coexist(subject):
    """NULL provider_turn_id rows do not collide with each other.

    A turn recorded under a synthetic key (upstream never sent
    `input_audio_buffer.committed`, so there is no item id) still has to be
    storable — several times over in one interview. Postgres treats NULLs as
    distinct in a UNIQUE constraint, and this pins that we rely on it.
    """
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()

        db.add(_turn(s.id, 1, provider_turn_id=None))
        db.add(_turn(s.id, 2, provider_turn_id=None))
        db.commit()  # must NOT raise

        assert db.scalar(
            select(func.count()).select_from(InterviewTurn).where(
                InterviewTurn.interview_session_id == s.id
            )
        ) == 2


@requires_db
def test_seq_is_deliberately_not_unique(subject):
    """`(interview_session_id, seq)` is an ORDERING index, not a constraint.

    If someone "tidies" this into a unique index, a duplicated seq stops being a
    turn shown twice in a list and becomes an IntegrityError inside a
    fire-and-forget writer — which swallows it, and the turn is gone. The
    cosmetic failure is strictly better than the silent one, so the non-
    uniqueness is asserted rather than left to be discovered.
    """
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()

        db.add(_turn(s.id, 3, provider_turn_id="u:item_x"))
        db.add(_turn(s.id, 3, provider_turn_id="u:item_y"))
        db.commit()  # must NOT raise

        assert db.scalar(
            select(func.count()).select_from(InterviewTurn).where(
                InterviewTurn.interview_session_id == s.id,
                InterviewTurn.seq == 3,
            )
        ) == 2


@requires_db
def test_empty_transcript_is_storable(subject):
    """L4: an unheard turn is `content = ''` plus a status, and it MUST persist.

    `_emit_turn`'s blank-text guard applies to the `messages` insert only. If the
    same guard reached this table, the exact turns `transcription_status` was
    added for would leave no row at all — a timed-out answer would be
    indistinguishable from an answer that never happened.
    """
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()

        db.add(
            _turn(
                s.id,
                1,
                content="",
                transcription_status="timeout",
                answer_quality=None,
                counted_as_answer=False,
                provider_turn_id="u:item_lost",
            )
        )
        db.commit()

        row = db.scalar(
            select(InterviewTurn).where(InterviewTurn.interview_session_id == s.id)
        )
        assert row.content == ""
        assert row.transcription_status == "timeout"
        assert row.answer_quality is None


# ---------------------------------------------------------------------------
# One evaluation per interview
# ---------------------------------------------------------------------------
@requires_db
def test_one_evaluation_per_session(subject):
    """DF6: the second write of a scorecard is refused by the database.

    The report deadline can fire and write `report_status='timeout'` moments
    before the real `response.done` arrives and tries to write `ok`. The relay's
    `_report_settled` flag is the first layer; this constraint is the layer that
    holds when the first one is wrong, and the loser's IntegrityError is
    swallowed.
    """
    with SessionLocal() as db:
        s = _session_row(subject, status="completed")
        db.add(s)
        db.commit()
        sid = s.id

        db.add(
            InterviewEvaluation(
                interview_session_id=sid,
                report_status="timeout",
            )
        )
        db.commit()

        db.add(
            InterviewEvaluation(
                interview_session_id=sid,
                report_status="ok",
                overall_score=71,
                strengths=["clear structure", "good examples"],
                improvements=["quantify results"],
                drill="Rewrite two answers using STAR.",
                summary="A solid practice run.",
                raw_response='{"overall": 71}',
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        rows = db.scalars(
            select(InterviewEvaluation).where(
                InterviewEvaluation.interview_session_id == sid
            )
        ).all()
        assert len(rows) == 1
        # The FIRST write wins; the retry is a no-op, not an overwrite.
        assert rows[0].report_status == "timeout"


@requires_db
def test_evaluation_scores_may_be_null_when_status_is_ok(subject):
    """A missing score and a zero mean opposite things to a mentor.

    `report_status='ok'` with a NULL `overall_score` is a legal, meaningful row:
    the model returned a report that did not carry that number. The columns are
    nullable so nobody is ever tempted to write 0 to satisfy the schema.
    """
    with SessionLocal() as db:
        s = _session_row(subject, status="completed")
        db.add(s)
        db.commit()
        db.add(
            InterviewEvaluation(
                interview_session_id=s.id,
                report_status="ok",
                summary="Report parsed, but the model omitted the scores.",
            )
        )
        db.commit()

        row = db.scalar(
            select(InterviewEvaluation).where(
                InterviewEvaluation.interview_session_id == s.id
            )
        )
        assert row.overall_score is None
        assert row.communication_score is None


# ---------------------------------------------------------------------------
# The delete rules — bulk deletes, so it is the DATABASE being tested
# ---------------------------------------------------------------------------
@requires_db
def test_deleting_a_session_cascades_turns_and_evaluation(subject):
    """CASCADE on both children, exercised through a bulk DELETE.

    ORM-level `cascade="all, delete-orphan"` would pass this test on its own and
    still leak in production, because `purge_expired` deletes in bulk and never
    loads the children. The rule has to be in the DDL, so the test bypasses the
    ORM's unit of work entirely.
    """
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()
        sid = s.id
        db.add(_turn(sid, 1))
        db.add(InterviewEvaluation(interview_session_id=sid, report_status="ok"))
        db.commit()

        db.execute(delete(InterviewSession).where(InterviewSession.id == sid))
        db.commit()

        assert db.scalar(
            select(func.count()).select_from(InterviewTurn).where(
                InterviewTurn.interview_session_id == sid
            )
        ) == 0
        assert db.scalar(
            select(func.count()).select_from(InterviewEvaluation).where(
                InterviewEvaluation.interview_session_id == sid
            )
        ) == 0


@requires_db
def test_deleting_the_conversation_keeps_the_interview(subject):
    """SET NULL, not CASCADE — the whole reason the record is keyed on the
    session and never on the conversation.

    A student clearing their chat, or `purge_expired` reaping it on the 90-day
    clock, must not destroy a 180-day interview record. If this ever flipped to
    CASCADE, the record would disappear on the subject's own unrelated button
    press and nothing would report an error.
    """
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()
        sid = s.id

        db.execute(
            delete(Conversation).where(Conversation.id == subject.conversation_id)
        )
        db.commit()

        row = db.get(InterviewSession, sid)
        assert row is not None
        assert row.conversation_id is None


@requires_db
def test_deleting_a_message_keeps_the_turn(subject):
    """`interview_turns.message_id` SET NULL, in the DDL and not only in the ORM.

    `purge_expired` issues a bulk `delete(Message)`. Without a database-level
    rule that statement would fail on this foreign key and take the whole
    retention job down — and with a CASCADE it would silently delete the
    interview transcript instead. SET NULL is the only correct answer, and this
    pins it.
    """
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()
        db.add(_turn(s.id, 1, message_id=subject.message_id))
        db.commit()

        db.execute(delete(Message).where(Message.id == subject.message_id))
        db.commit()

        row = db.scalar(
            select(InterviewTurn).where(InterviewTurn.interview_session_id == s.id)
        )
        assert row is not None
        assert row.message_id is None
        assert row.content  # the transcript itself is untouched


@requires_db
def test_deleting_a_consent_grant_keeps_the_interviews_run_under_it(subject):
    """SET NULL on `consent_id`.

    Deleting a grant must not delete the interviews conducted under it — those
    interviews are the reason the grant mattered. The row losing its pointer is
    recoverable; the interview record disappearing is not.
    """
    with SessionLocal() as db:
        grant = InterviewConsent(
            user_id=subject.user_id,
            version=CONSENT_VERSION,
            scope_live_ai=True,
            scope_store_transcript=True,
            scope_store_audio=False,
        )
        db.add(grant)
        db.commit()
        s = _session_row(subject, consent_id=grant.id)
        db.add(s)
        db.commit()
        sid = s.id

        db.execute(delete(InterviewConsent).where(InterviewConsent.id == grant.id))
        db.commit()

        row = db.get(InterviewSession, sid)
        assert row is not None
        assert row.consent_id is None


@requires_db
def test_deleting_the_student_removes_their_interviews(subject):
    """CASCADE from `students.id`. An interview with no subject is not a record,
    and leaving it behind would leave a transcript of a named student attached to
    nobody — the worst of both outcomes."""
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()
        sid = s.id
        db.add(_turn(sid, 1))
        db.commit()

        db.execute(delete(Student).where(Student.id == subject.student_id))
        db.commit()

        assert db.get(InterviewSession, sid) is None
        assert db.scalar(
            select(func.count()).select_from(InterviewTurn).where(
                InterviewTurn.interview_session_id == sid
            )
        ) == 0


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------
@requires_db
def test_a_session_finalizes_exactly_once(subject):
    """Three layers race to close a session; the `status='running'` predicate is
    the ONLY thing making them idempotent.

    The relay's own finalizer, the router's `finally:` backstop and the orphan
    sweeper can all fire for one interview — on a cancelled shutdown, all three
    will. The second and third must update ZERO rows and say nothing, or a
    session that ended cleanly at 1000 gets rewritten as 'abandoned' by whichever
    layer arrived last. This pins the predicate itself, independent of its
    callers, because the callers are three different modules owned by three
    different steps.
    """
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()
        sid = s.id
        assert s.status == "running"

        def finalize(status: str, reason: str, code: int) -> int:
            res = db.execute(
                update(InterviewSession)
                .where(InterviewSession.id == sid, InterviewSession.status == "running")
                .values(
                    status=status,
                    terminal_reason=reason,
                    close_code=code,
                    ended_at=func.now(),
                )
            )
            db.commit()
            return res.rowcount

        assert finalize("completed", "Interview complete", 1000) == 1
        # Layer 2 (the router backstop) and Layer 3 (the sweeper) both arrive
        # after the relay already won. Zero rows, no exception, no overwrite.
        assert finalize("abandoned", "orphaned (no heartbeat)", None) == 0
        assert finalize("failed", "1011 relay error", 1011) == 0

        db.expire_all()
        row = db.get(InterviewSession, sid)
        assert row.status == "completed"
        assert row.terminal_reason == "Interview complete"
        assert row.close_code == 1000
        assert row.ended_at is not None


@requires_db
def test_two_running_sessions_for_one_student_are_allowed(subject):
    """No partial unique index on (student_id) WHERE status='running', and that
    is deliberate.

    "One running interview per student" is the router's per-user cap. As a
    database constraint it would convert a crashed process — which leaves a
    `running` row nobody will ever close — into a student locked out of the
    feature until the sweeper ran. A support ticket instead of a retry.
    """
    with SessionLocal() as db:
        db.add_all([_session_row(subject), _session_row(subject)])
        db.commit()  # must NOT raise

        assert db.scalar(
            select(func.count()).select_from(InterviewSession).where(
                InterviewSession.student_id == subject.student_id,
                InterviewSession.status == "running",
            )
        ) == 2


@requires_db
def test_orphan_sweep_predicate_finds_only_stale_running_rows(subject):
    """The sweeper's predicate, and its most important detail:
    `ended_at = heartbeat_at`, never `now()`.

    The interview ended when the heartbeat stopped. Stamping `now()` would
    inflate every orphaned session's duration by however long the sweeper took to
    run — which, for a sweeper that fires once at startup, could be days.
    """
    with SessionLocal() as db:
        stale = _session_row(subject)
        stale.heartbeat_at = _utc(minutes=-40)
        fresh = _session_row(subject)
        fresh.heartbeat_at = _utc(seconds=-5)
        done = _session_row(subject, status="completed")
        done.heartbeat_at = _utc(minutes=-40)
        db.add_all([stale, fresh, done])
        db.commit()
        stale_id, fresh_id, done_id = stale.id, fresh.id, done.id
        stale_heartbeat = stale.heartbeat_at

        cutoff = _utc(minutes=-20)
        res = db.execute(
            update(InterviewSession)
            .where(
                InterviewSession.status == "running",
                InterviewSession.heartbeat_at < cutoff,
                InterviewSession.id.in_([stale_id, fresh_id, done_id]),
            )
            .values(
                status="abandoned",
                terminal_reason="orphaned (no heartbeat)",
                ended_at=InterviewSession.heartbeat_at,
            )
        )
        db.commit()
        assert res.rowcount == 1

        db.expire_all()
        assert db.get(InterviewSession, stale_id).status == "abandoned"
        assert db.get(InterviewSession, stale_id).ended_at == stale_heartbeat
        assert db.get(InterviewSession, fresh_id).status == "running"
        # A finished interview is never a sweep candidate, however old.
        assert db.get(InterviewSession, done_id).status == "completed"


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------
@requires_db
def test_one_live_consent_grant_per_user_and_version(subject):
    """A double-clicked Accept button must not produce two live grants.

    Same partial-unique shape as `uq_conversation_one_active_per_owner`, and the
    same reason: with two live grants, revoking clears one and leaves the student
    still consented by the other. Revocation has to actually revoke.
    """
    def _grant(**kw):
        return InterviewConsent(
            user_id=subject.user_id,
            version=CONSENT_VERSION,
            scope_live_ai=True,
            scope_store_transcript=True,
            scope_store_audio=False,
            **kw,
        )

    with SessionLocal() as db:
        first = _grant()
        db.add(first)
        db.commit()
        first_id = first.id

        db.add(_grant())
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # Revoking frees the slot AND keeps the history — the grant a past
        # interview's consent_id points at must not vanish.
        db.execute(
            update(InterviewConsent)
            .where(InterviewConsent.id == first_id)
            .values(revoked_at=func.now())
        )
        db.commit()

        db.add(_grant())
        db.commit()  # must NOT raise

        rows = db.scalars(
            select(InterviewConsent).where(InterviewConsent.user_id == subject.user_id)
        ).all()
        assert len(rows) == 2
        assert sum(1 for r in rows if r.revoked_at is None) == 1


@requires_db
def test_a_new_version_is_a_separate_grant(subject):
    """The version string is scoped into the index on purpose.

    Consent is not retroactive: when the terms change, the old grant stops
    counting for the new ones and the student is asked again. Two live rows for
    two different versions is the correct state, not a duplicate.
    """
    with SessionLocal() as db:
        for version in (CONSENT_VERSION, "2027-01"):
            db.add(
                InterviewConsent(
                    user_id=subject.user_id,
                    version=version,
                    scope_live_ai=True,
                    scope_store_transcript=True,
                    scope_store_audio=False,
                )
            )
        db.commit()  # must NOT raise

        assert db.scalar(
            select(func.count()).select_from(InterviewConsent).where(
                InterviewConsent.user_id == subject.user_id,
                InterviewConsent.revoked_at.is_(None),
            )
        ) == 2


@requires_db
def test_audio_defaults_say_not_recorded_rather_than_unknown(subject):
    """`audio_recorded` is the field to test — never `audio_path IS NOT NULL`.

    A fresh session reads "no audio was kept" without anyone writing anything,
    which is what lets every interview taken before capture existed answer the
    question honestly instead of ambiguously. The nullable path is the ambiguous
    half of the pair and is only ever meaningful when the flag is true.
    """
    with SessionLocal() as db:
        s = _session_row(subject)
        db.add(s)
        db.commit()
        db.expire_all()

        row = db.get(InterviewSession, s.id)
        assert row.audio_recorded is False
        assert row.audio_truncated is False
        assert row.audio_path is None
        assert row.audio_bytes is None
        assert row.audio_duration_ms is None


@requires_db
def test_no_pg_enum_types_were_created_for_the_interview_vocabularies():
    """§6.1: every vocabulary column here is a plain String.

    The seven original ones — status, specialization, final_phase, speaker,
    transcription_status, answer_quality, report_status — will move (a fifth
    specialization used to be a one-line data change in `interview_matrix.py`
    and is now a row in `interview_tracks`), and turning that into an
    `ALTER TYPE ... ADD VALUE` migration would be a regression. This test fails
    the moment someone "fixes" one into an enum, which is exactly when the next
    editor should be made to read §6.1.

    Phase 4c's four tables are covered here too, and `interview_tracks.code` is
    now the sharpest case: that table exists SO THAT a fifth track is a data
    change, so an enum on it would defeat the feature it belongs to.
    """
    expected = {
        "interview_sessions": {
            "status", "specialization", "final_phase", "terminal_reason",
            "conn_id", "upstream_session_id", "audio_path",
        },
        "interview_turns": {
            "speaker", "phase", "transcription_status", "answer_quality",
            "provider_turn_id", "question_id",
        },
        "interview_evaluations": {"report_status", "model"},
        "interview_consents": {"version", "user_agent", "source_ip_hash"},
        # Phase 4c. `interview_tracks.code` is the one this rule was really
        # written for: B5.1 exists so that a fifth track is a DATA change, and
        # an enum here would put it straight back into a `CREATE TYPE`
        # migration. `interview_score_summaries.status` copies
        # `interview_sessions.status` and must stay the same kind of thing.
        "interview_tracks": {"code", "label", "nova_voice"},
        "interview_score_summaries": {"status", "track_code"},
        "interview_cap_resets": set(),
        "interview_policies": set(),
    }
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                select table_name, column_name, data_type
                  from information_schema.columns
                 where table_name in (
                       'interview_sessions', 'interview_turns',
                       'interview_evaluations', 'interview_consents',
                       'interview_tracks', 'interview_policies',
                       'interview_score_summaries', 'interview_cap_resets')
                """
            )
        ).all()
    by_table: dict[str, dict[str, str]] = {}
    for table, column, dtype in rows:
        by_table.setdefault(table, {})[column] = dtype

    assert set(by_table) == set(expected), "a table is missing from the database"
    for table, columns in expected.items():
        for column in columns:
            assert by_table[table][column] == "character varying", (
                f"{table}.{column} is {by_table[table][column]!r}, not a plain "
                "String — see interview-engine-v3.md §6.1 before changing this"
            )
        # USER-DEFINED is how information_schema reports a PG enum. Not one of
        # these four tables may grow one without that decision being revisited.
        assert "USER-DEFINED" not in by_table[table].values(), (
            f"{table} grew a PG enum column; §6.1 says these vocabularies stay "
            "plain Strings because they will move"
        )
