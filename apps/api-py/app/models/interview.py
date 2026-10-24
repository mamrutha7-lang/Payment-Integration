"""Interview records — the durable artefact of a mock interview (Interview
Engine v3, §6).

Six tables now, and they are **in addition to `messages`, never instead of it**.
Four of them are the record of an interview (`interview_sessions`,
`interview_turns`, `interview_evaluations`, `interview_consents`) and two arrived
with Phase 4c: `interview_score_summaries`, the four numbers that deliberately
OUTLIVE the record, and `interview_cap_resets`, an admin giving a student their
practice attempts back. Both are at the foot of this module with their reasoning.

Every interview turn still writes its `messages` row with `channel='interview'`,
so `GET /api/agent/history` and the AGENTS.md runbook query
(`select channel, count(*), max(created_at) from messages group by channel;`)
answer exactly what they answer today. What a `messages` row cannot carry is the
*interview*: which phase a turn belonged to, whether the transcriber actually
heard it, whether it advanced the arc, and the verdict at the end. That is what
lives here.

The record is keyed on `interview_session_id` and **never** on `conversation_id`.
A conversation is the student's chat thread: they can clear it with one button,
and `retention.purge_expired` hard-deletes it on a 90-day clock. The interview
record has its own, longer clock (`interview_retention_days`, 180) and has to
survive both — which is why `conversation_id` is nullable ON DELETE SET NULL and
why `interview_turns` hangs off the session rather than off the conversation.
`append_message` raising `ConversationGone` ends the socket; it must not be able
to orphan the record of what was said.

**Every vocabulary column here is a plain `String`, not a PG enum** (§6.1) —
`status`, `specialization`, `final_phase`, `speaker`, `transcription_status`,
`answer_quality`, `report_status`. The precedent is `Message.channel` and
`Conversation.consent_state` in `models/conversation.py`. The reason is that all
three of AGENTS.md's Alembic enum gotchas are `CREATE TYPE` ordering pain, and
**these vocabularies will move**: a fifth specialization is a one-line data
change in `interview_matrix.py` today, and turning it into a migration would be a
regression, not a safeguard. `Upload.status` earned its enum because it is a
frozen three-state review workflow; none of these are. The vocabulary is written
next to the column, which is where the next editor will look for it.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    # Python-side default alongside the server_default, the same pairing
    # models/conversation.py uses: turns written inside one request get distinct,
    # microsecond-precise stamps and therefore a deterministic order, while a row
    # inserted by raw SQL (the retention sweeper, a migration backfill) still
    # gets a timestamp from the database.
    return datetime.now(timezone.utc)


class InterviewSession(Base):
    """One mock interview, from the socket opening to whatever ended it.

    A row is inserted when the relay opens the socket and is **finalized exactly
    once** — three layers try, and the guard that makes them idempotent is the
    `status = 'running'` predicate on the UPDATE, not a lock: the relay's own
    finalizer, the router's `finally:` backstop, and
    `retention.finalize_orphaned_interviews` for the process that was killed
    outright. Whichever gets there first moves `status` off `'running'`; the
    others update zero rows and say nothing.

    A `running` row that is never closed is worse than no row at all, because it
    is a record that lies — which is why `heartbeat_at` exists, and why the
    sweeper stamps `ended_at = heartbeat_at` rather than `now()`: the interview
    ended when the heartbeat stopped, and stamping `now()` would inflate every
    orphaned session's duration by however long the sweeper took to run.
    """

    __tablename__ = "interview_sessions"
    __table_args__ = (
        # "this student's interviews, newest first" — every staff read and the
        # student's own history screen.
        Index("ix_interview_session_student_started", "student_id", "started_at"),
        # ...and the same sort with NO student: the staff list across the whole
        # programme, which the composite above cannot serve because student_id
        # leads it. Bounded by _MAX_SESSIONS_LISTED, so this turns a scan-and-sort
        # of every interview ever held into reading the first page of an index.
        Index("ix_interview_session_started", "started_at"),
        # The orphan sweeper's predicate, and nothing else. Partial on
        # status='running' because a finished interview is never a candidate, and
        # there will be hundreds of thousands of those against a handful of live
        # ones — a full index here would be almost entirely dead weight.
        Index(
            "ix_interview_session_orphans",
            "heartbeat_at",
            postgresql_where=text("status = 'running'"),
        ),
        # The retention reaper's predicate. Partial on "not yet soft-deleted" for
        # the same reason: rows already swept are not candidates again.
        Index(
            "ix_interview_session_retention",
            "retention_until",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Counts can only be counts. NOT `turns_persisted <= turns_emitted`,
        # even though the runbook reads the pair that way: this row is written
        # by the relay's finalizer on a fire-and-forget path, and a constraint
        # able to reject that write would turn a diagnostic counter into a
        # `running` row that lies — the exact failure the three close layers
        # exist to prevent.
        CheckConstraint(
            "turns_emitted >= 0 AND turns_persisted >= 0 AND answers_accepted >= 0",
            name="ck_interview_session_counters",
        ),
        # NO unique constraint beyond the PK, deliberately. "One running
        # interview per student" is enforced by the per-user cap in the router,
        # NOT by a partial unique index on (student_id) WHERE status='running' —
        # because a killed process leaves a `running` row behind, and such an
        # index would then lock that student out of the feature entirely until
        # the sweeper ran. It would convert a crash into a support ticket.
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # The subject. Rule 2's gate (`_assert_can_access_student`) takes a
    # student_id and every staff read is "this student's interviews", so this is
    # the column the whole access-control story is written against. CASCADE: if
    # the Student row goes, so does the interview record — there is no such thing
    # as an interview belonging to nobody.
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE")
    )
    # The chat thread this interview's turns were also written to, for joining
    # the two records. SET NULL, not CASCADE: `retention.purge_expired` hard-
    # deletes conversations on a 90-day clock and the student can clear one at
    # any time. Neither may destroy the interview record, which is kept for 180
    # days and is the artefact a mentor reviews. A NULL here means "the chat
    # thread is gone", never "there was no interview".
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 'hr' | 'dm' | 'ba' | 'fa' — the keys of interview_matrix.SPECIALIZATIONS.
    # NULL is the generic interview, i.e. the no-`?specialization=` path, which
    # is a supported product state and not a missing value.
    specialization: Mapped[str | None] = mapped_column(String, nullable=True)
    # 'running' | 'completed' | 'abandoned' | 'failed'. Starts 'running' and is
    # moved exactly once by whichever finalization layer wins.
    status: Mapped[str] = mapped_column(
        String, default="running", server_default="running"
    )
    # Free text, e.g. '4008 No audio received for 2 minutes'. The human-readable
    # half of (close_code, terminal_reason) — a mentor reading "abandoned" needs
    # to know whether the student walked away or the transcriber died.
    terminal_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # The InterviewPhase value at close: 'opening' | 'probing' | 'deep_dive' |
    # 'wrap_up' | 'ended'. Answers "did this interview ever reach the verdict?"
    # without replaying the turns.
    final_phase: Mapped[str | None] = mapped_column(String, nullable=True)
    # Accepted answers only — the same counter the InterviewStateMachine advances
    # the arc on. A clarification, a cough and an unheard turn do not count here,
    # by design: this is the number the phase transitions are a function of.
    answers_accepted: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    # The pair that answers "the call sounded fine and saved nothing" with no
    # join and no query against `messages`. Turn writes are fire-and-forget so a
    # bad write can never kill a live call (AGENTS.md's voice runbook); the cost
    # of that choice is that dropped turns are invisible. emitted > persisted is
    # exactly how many were dropped.
    turns_emitted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    turns_persisted: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    # ...and the one thing that makes that pair readable once a college can turn
    # the transcript OFF (B6.1, `interview_policies.store_transcript`).
    #
    # A policy that suppresses the transcript suppresses the `interview_turns`
    # rows, so `turns_emitted = 12, turns_persisted = 0` — which is EXACTLY the
    # shape AGENTS.md's runbook tells an operator means writes are failing. The
    # runbook would then fire on every interview at such a college and stop
    # being read at all, which is worse than not having it. This flag is the
    # difference between "we chose not to keep this" and "we lost this", and any
    # health query over the pair must exclude rows where it is true.
    #
    # On the SESSION and not derived from the policy at read time, on purpose:
    # the policy can be edited afterwards, and this has to keep answering what
    # was true when the interview ran — the same reasoning as
    # `retention_until` below and `consent_id` above.
    transcript_suppressed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # The WebSocket close code the student's browser actually saw (1000, 4008,
    # 4011, …). Nullable while the interview is running.
    close_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The relay's 12-hex connection id. This is the join between this row and
    # every log line the interview produced — without it, diagnosing one
    # student's bad session means grepping a log by timestamp.
    conn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # OpenAI's own session id, which is the handle their support asks for. Free
    # to record at the handshake and impossible to recover afterwards.
    upstream_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # The exact consent grant that was live when this interview opened — not
    # "the student's current consent", which is a different question and will
    # give a different answer the moment they revoke. SET NULL rather than
    # CASCADE: deleting a grant must not delete the interviews conducted under
    # it, because those interviews are the reason the grant mattered.
    consent_id: Mapped[str | None] = mapped_column(
        ForeignKey("interview_consents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # --- Audio ---------------------------------------------------------------
    # Capture itself is built in a later wave; these columns land with the schema
    # so the capture commit is a code change and not a migration on a populated
    # table.
    #
    # READ `audio_recorded`, NEVER a NULL check on `audio_path`. That is the
    # whole reason the flag sits next to the nullable path. A NULL path is
    # ambiguous by nature: it means "no audio is stored for this interview",
    # which is true when capture was switched off, when the student refused
    # `scope_store_audio`, when the write failed, AND for every interview that
    # predates capture entirely. Those are four different facts and a NULL cannot
    # tell them apart. `audio_recorded is False` means "we know nothing was
    # kept"; `audio_recorded is True` means a file was written and `audio_path`
    # names it. Code that branches on `audio_path is not None` will eventually
    # meet a row where the flag was set and the file was reaped, or where a path
    # was written for a capture that produced nothing, and it will quietly do the
    # wrong thing about a student's voice recording. Spec §8.4 argued for
    # omitting these columns entirely for exactly this reason; the product owner
    # has since required recording for authorised developer review, so the
    # ambiguity §8.4 feared is closed here in writing instead of avoided.
    audio_recorded: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # A relative name inside the interview-audio root — never an absolute path
    # and never a URL. The root moves between deploys and between machines; a
    # stored absolute path is a dangling pointer the first time it does.
    audio_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # DISK OCCUPIED by this interview's recording: every file the store holds for
    # it, RIFF headers included. Since the mixdown landed that is THREE files —
    # the student's track, the interviewer's, and the derived `mixed` copy the
    # download endpoint serves by default — so this number roughly DOUBLED for
    # the same interview. The meaning did not change ("the disk the reaper is
    # about to free"), which is why the third file was folded in rather than
    # given a column of its own; but a chart of this over the deploy has a step
    # in it, and that step is the mix, not a change in how much anyone talked.
    #
    # IT IS NOT WHAT `interview_recording_max_bytes` MEASURES, and the two are
    # deliberately different numbers. The cap bounds CAPTURED PCM — what
    # `feed()` accepted, both directions, padding included — because that is the
    # thing capture can stop doing. The mix is written afterwards from what
    # survived the cap and is about as long as the longer source, so the 128 MB
    # cap occupies up to ~256 MB on disk. (Both halves of that pairing moved
    # when the cap did: it was 64 MB / ~128 MB, sized before both tracks were
    # padded to wall clock. The arithmetic is the same; the premise changed.)
    # An operator sizing a volume needs the second number; anyone reasoning
    # about truncation needs the first. Neither is wrong and neither is
    # derivable from the other without knowing which track was longer, so both
    # are written down here.
    audio_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Wall-clock duration of the captured audio, which is NOT the duration of the
    # interview: a late start, a paused capture and a truncation each make it
    # shorter. Do not derive one from the other.
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True when capture stopped at `interview_recording_max_bytes` with the
    # interview still running. A truncated recording that does not say so is a
    # recording that gets replayed as if it were complete — "she never answered
    # the last question" when in fact the file ended. Never truncate silently.
    audio_truncated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # WHY `audio_recorded` IS FALSE, when it is (2026-09-17): one of
    # `app/interview_audio.py`'s SKIP_* words -- the operator's switch, the
    # college's policy, the student's acknowledgement, a full store, a store
    # that would not open, or a recorder that closed with nothing. NULL on a
    # recorded interview and on every row that predates the column, which is
    # the same fact for both ("nothing to explain" and "nobody wrote it down"
    # look alike, and the screen says "no recording" for both rather than
    # guessing). It exists because a bare "No audio" chip beside a grey
    # Download button was read by the office as a broken feature, on a
    # deployment where the only thing missing was a tick on the policy card.
    audio_skipped_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    # Bumped by the relay's watchdog roughly once a minute. This is the only
    # thing that makes orphan detection possible: a process killed with -9 leaves
    # `status='running'` forever, and the sweeper's predicate is a STALE
    # HEARTBEAT, not an elapsed `started_at` — by started_at alone a long healthy
    # interview and a dead one are indistinguishable.
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # started_at + settings.interview_retention_days, STORED rather than computed
    # so that changing the setting does not retroactively re-date every interview
    # already on disk. The student was told 180 days, and that promise travels
    # with their row.
    retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Soft-delete, the same two-stage lifecycle shape `Conversation` already has:
    # past retention_until → soft-delete + redact, past the grace window →
    # hard-delete. One retention story in this codebase, not two.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # passive_deletes=True on both: the DDL already carries ON DELETE CASCADE, so
    # SQLAlchemy must NOT load every turn into memory just to delete it one row
    # at a time. It also keeps the ORM path and the bulk-DELETE path the
    # retention job uses behaving identically — a divergence there is how "it
    # works in a test and leaks in production" happens.
    turns: Mapped[list["InterviewTurn"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    evaluation: Mapped["InterviewEvaluation | None"] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class InterviewTurn(Base):
    """One utterance, by either party, with the interview context a `messages`
    row cannot carry.

    Written in the SAME transaction as its `messages` row (§6.5) — one
    `SessionLocal()`, two inserts, one commit. Two fire-and-forget writers were
    considered and rejected: they double the pool pressure at
    `interview_max_sessions = 100`, and they can PARTIALLY succeed, which
    reintroduces the exact silent-failure shape the voice runbook exists to catch
    (the `group by channel` query reports "saved fine" while the reviewable
    record is missing turns). The entire value of this table is being the record
    you can trust when `messages` cannot be; a record that can silently disagree
    with `messages` about which turns happened would have no such value.
    """

    __tablename__ = "interview_turns"
    __table_args__ = (
        # The record's identity, and the second of the two dedup layers: the
        # relay's own `_pending.pop` protects the state machine, this protects
        # the row. A transcription delivered twice, or a `.failed` arriving after
        # a `.completed`, lands here and the IntegrityError is swallowed as the
        # no-op it is. Same single 'u:'/'a:' namespace `messages` uses — do NOT
        # invent a second correlation scheme.
        UniqueConstraint(
            "interview_session_id",
            "provider_turn_id",
            name="uq_interview_turn_provider",
        ),
        # NOT unique, and that is the decision rather than an omission. Ordering
        # is a display concern; uniqueness of the *record* is provider_turn_id's
        # job. A fire-and-forget writer must never be able to raise on a
        # constraint whose violation is cosmetic — a duplicated seq shows a turn
        # twice in a list, while a raise here loses the turn entirely, and this
        # table exists precisely to not lose turns.
        Index("ix_interview_turn_session_seq", "interview_session_id", "seq"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE")
    )
    # 1-based, assigned by the relay in emit order.
    seq: Mapped[int] = mapped_column(Integer)
    # 'student' | 'interviewer'.
    speaker: Mapped[str] = mapped_column(String)
    # The InterviewPhase at the time of the turn. This is the column that makes
    # the phase arc auditable after the fact — "the machine said deep_dive, and
    # here are the three turns that happened inside it".
    phase: Mapped[str] = mapped_column(String)
    # AN EMPTY STRING IS LEGAL HERE, and that is why this column is NOT NULL
    # rather than nullable. A failed or timed-out transcription produces
    # `content = ''` with `transcription_status` saying which — and that row is
    # the entire reason this table exists. `_emit_turn`'s blank-text guard
    # applies to the `messages` insert ONLY; skipping the interview_turns row for
    # a blank transcript would mean the exact turns this table adds a status
    # column for leave no trace at all.
    content: Mapped[str] = mapped_column(Text)
    # 'ok' | 'failed' | 'timeout' | 'not_applicable' — the last for interviewer
    # turns, whose text came from the model and was never transcribed at all.
    transcription_status: Mapped[str] = mapped_column(
        String, default="ok", server_default="ok"
    )
    # 'accepted' | 'empty' | 'filler' | 'too_short' | 'skipped' —
    # classify_answer's verdict — or 'echo', which the Nova engine writes when
    # the "answer" was the interviewer's own words picked up by the student's
    # microphone (app/interview_nova.py::_looks_like_echo). NULL on interviewer
    # turns and on unheard turns: there was no answer to judge, which is a
    # different fact from "the answer was empty".
    answer_quality: Mapped[str | None] = mapped_column(String, nullable=True)
    # Whether this turn ticked the state machine. Together with `phase` above,
    # this is what lets someone reconstruct why the interview reached wrap_up
    # when it did — and, more often the question that gets asked, why it did not.
    counted_as_answer: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # An interviewer turn cut off by barge-in: the student heard only part of the
    # question. Distinguishes "asked" from "started asking", which is the
    # difference between an unanswered question and an interrupted one.
    is_partial: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # 'u:<item_id>' / 'a:<response_id>'. Nullable because a turn can be recorded
    # under a synthetic key when upstream never handed us an id; Postgres treats
    # NULLs as distinct, so those rows coexist instead of colliding.
    provider_turn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # The twin `messages` row, free to capture because both inserts share one
    # transaction. ondelete='SET NULL' must be IN THE DDL and not only in the
    # ORM: `retention.purge_expired` issues a bulk `delete(Message)` that
    # bypasses ORM cascades entirely, and without the database-level rule that
    # delete fails on this FK — taking the retention job down with it.
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # B6.6: the bank question this interviewer turn was asking, WHEN THAT CAN BE
    # KNOWN — and today it cannot be, for anybody.
    #
    # READ THIS BEFORE BUILDING ANYTHING ON IT. Nothing writes this column. The
    # question bank is rendered into the INSTRUCTIONS once, as a block of
    # "[phase] text" lines with the model told to "rephrase each naturally
    # rather than reciting it … a guide to coverage, not a script"
    # (`app/interview_matrix.py`, `app/interview_bank.py`). The engine never
    # SELECTS a question, so there is no "the question it injected" to record:
    # `_TurnRecord` has no slot for one and neither engine could fill it. The
    # column is here because it is free to add to an empty-valued nullable
    # column and expensive to add later to a table with hundreds of thousands of
    # rows — not because the mechanism exists.
    #
    # If it is ever filled, it will be by POST-HOC matching of the interviewer's
    # words against the track's bank on the writer's worker thread, which is
    # lossy BY CONSTRUCTION because the prompt orders the rephrasing. Then NULL
    # means "unmatched", never "no question", and any count built on it
    # (04's B5.5 `asked_count`) is an ESTIMATE that the screen must label as one.
    # The alternative — a `mark_question` tool call inside the turn loop — puts a
    # round trip on the hot path the word gate was made deterministic to avoid,
    # and the local engine has no equivalent, so the two engines would stop
    # sharing one contract.
    #
    # SET NULL: deleting a retired question must never delete the record of an
    # interview in which it was asked.
    question_id: Mapped[str | None] = mapped_column(
        ForeignKey("interview_bank_questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    session: Mapped[InterviewSession] = relationship(back_populates="turns")


class InterviewEvaluation(Base):
    """The scorecard: one row per interview, always written, even when there is
    nothing to score.

    A row saying `report_status='unavailable'` is the record that a report was
    attempted and did not arrive; a MISSING row says nothing at all, and a mentor
    cannot tell "the model failed" from "nobody has looked yet". Every failure
    path still writes.
    """

    __tablename__ = "interview_evaluations"
    __table_args__ = (
        # ONE evaluation per interview, enforced by the database. This is what
        # makes the write idempotent under retry: the report's `response.done`
        # can arrive after the deadline already wrote `report_status='timeout'`,
        # and the loser's IntegrityError is swallowed. The relay's
        # `_report_settled` flag is the first layer; this is the layer that holds
        # when the first one is wrong.
        UniqueConstraint(
            "interview_session_id", name="uq_interview_evaluation_session"
        ),
        # 0-100 or NULL — the range `_report_score` in interview_core already
        # clamps to, so this can never reject a write the parser produced. NULL
        # stays legal (see the column comments): this bounds a value, it never
        # demands one.
        CheckConstraint(
            "(overall_score IS NULL OR overall_score BETWEEN 0 AND 100) AND "
            "(communication_score IS NULL OR communication_score BETWEEN 0 AND 100) AND "
            "(domain_score IS NULL OR domain_score BETWEEN 0 AND 100) AND "
            "(structure_score IS NULL OR structure_score BETWEEN 0 AND 100)",
            name="ck_interview_eval_scores",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE")
    )
    # 'ok' | 'unparseable' | 'timeout' | 'rejected' | 'unavailable'. NOT NULL and
    # deliberately without a default: every writer states which of the five
    # happened, because a defaulted status is a status nobody chose.
    report_status: Mapped[str] = mapped_column(String)
    # 0-100, and NULLABLE EVEN WHEN report_status='ok'. A missing score and a
    # zero mean opposite things to the mentor reading this screen — "the model
    # did not return one" versus "the model scored this student zero". Never
    # fabricate one to fill the column; the render must be able to show a blank.
    overall_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    communication_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    structure_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # JSONB lists of short strings, the type `AgentRun.trace` already uses.
    # Nullable AND defaulted to []: NULL means the report never produced them,
    # [] means it produced none — for `improvements` those read very differently.
    strengths: Mapped[list | None] = mapped_column(JSONB, default=list, nullable=True)
    improvements: Mapped[list | None] = mapped_column(
        JSONB, default=list, nullable=True
    )
    # The one concrete practice task, and the student-facing paragraph. Text
    # rather than String because both are model prose of unbounded length.
    drill: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The model's exact output, kept for debugging a bad parse. The Main Admin
    # ONLY, and never sent to the student: it routinely contains the model's
    # private reasoning ABOUT them. It is the model's words about the student's
    # words rather than a database student record, so rule 1 is untouched — but
    # it is exactly what a student would object to a peer reading, so it is
    # scoped like everything else and redacted by the retention job the way
    # `AgentRun.question` already is.
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )

    session: Mapped[InterviewSession] = relationship(back_populates="evaluation")


class InterviewConsent(Base):
    """A consent GRANT — a row, because consent that lives in `localStorage` is a
    cache and not a record.

    The panel used to write `localStorage`: nothing on the server read it, the
    student could clear it, and on a shared lab machine it belonged to the
    browser rather than to the person. `Conversation.consent_state` is not an
    alternative either — it is destroyed by "Clear conversation" and hard-deleted
    30 days later, and a consent record that the subject's own unrelated action
    destroys is not a consent record.

    Revocation is expressible precisely BECAUSE a grant is a row: revoking stamps
    `revoked_at` and leaves the historical row standing, and
    `interview_sessions.consent_id` pins the grant that was live when that
    interview opened. "Was this student consented at the time of interview X" is
    therefore answerable years later. A boolean on `users` cannot answer it.
    """

    __tablename__ = "interview_consents"
    __table_args__ = (
        # One live grant per (user, version), enforced by the database —
        # identical shape and identical reasoning to
        # `uq_conversation_one_active_per_owner`: a double-clicked button
        # otherwise leaves two live grants, and revocation then clears one and
        # leaves the student still consented by the other. Partial on
        # `revoked_at IS NULL` so the history stays and only the live grant is
        # constrained.
        Index(
            "uq_interview_consent_active",
            "user_id",
            "version",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # Keyed on the USER, not the Student. A grant belongs to a person and must
    # not be destroyed by a `Student` row being rewritten (a re-seeded roster, a
    # corrected USN). CASCADE on the user, because a deleted person has no
    # grants to keep.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # settings.interview_consent_version, stamped so a change of terms is visible
    # in the data instead of assumed. Consent is NOT retroactive: rows carrying
    # an old string stop counting as consent for the new terms, which is exactly
    # what should happen. `POST /api/interview/consent` 422s a version the server
    # does not know, so a stale cached SPA cannot grant against copy the student
    # never saw.
    version: Mapped[str] = mapped_column(String)
    # THREE booleans, not one, because they are three different disclosures and a
    # student may reasonably accept two and refuse the third. One boolean makes
    # "they consented" unfalsifiable. All three are NOT NULL with no default: a
    # grant states each scope explicitly, or it is not a grant.
    #
    # Live audio streamed to the model so it can hear and reply. Nothing from the
    # student record travels with it — rule 1 is structural in the relay, which
    # imports no ORM model at all.
    scope_live_ai: Mapped[bool] = mapped_column(Boolean)
    # The written transcript kept on the college's server as part of the
    # interview record, readable by their mentor and the placement office.
    # Note the sentence the consent panel MUST carry alongside this one: clearing
    # the conversation does NOT delete the interview record.
    scope_store_transcript: Mapped[bool] = mapped_column(Boolean)
    # The student's recorded VOICE kept on disk for authorised review. A
    # materially different posture from a transcript — a stored voice recording
    # of a named student is biometric-adjacent — which is why it is a separate
    # switch a student can refuse while still taking the interview.
    #
    # This one IS read. The capture path checks it per interview, together with
    # `settings.interview_recording_enabled`, before a single byte is written,
    # and `interview_sessions.audio_recorded` records what actually happened. A
    # false here with capture enabled is a lawful, expected outcome: the
    # interview runs normally, no audio is kept, and `audio_recorded` says false.
    scope_store_audio: Mapped[bool] = mapped_column(Boolean)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    # NULL is a live grant; a stamp is a withdrawal. The row is never deleted on
    # revocation — the historical fact that consent WAS given is the thing the
    # interviews conducted under it depend on.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    # A salted hash, NEVER the address. Enough to show that two grants came from
    # the same machine when a grant is disputed; not enough to become a location
    # log on a student.
    source_ip_hash: Mapped[str | None] = mapped_column(String, nullable=True)


class InterviewScoreSummary(Base):
    """The four numbers and the date, kept AFTER the interview itself is gone
    (B6.2).

    THE POINT OF THIS TABLE IS THAT IT OUTLIVES ITS PARENT. `interview_turns`
    holds the student's own words and `interview_evaluations.raw_response` holds
    the model's private reasoning about them; both are deleted on the 180-day
    clock, and that clock is a promise. But "you scored 61, then 68, then 74" is
    a trend a student and their mentor need for longer than six months, and it
    names nothing anybody said. So the scores are copied out at finalization and
    kept, and the transcript goes.

    WHICH MAKES `session_id` THE ONE COLUMN THAT HAD TO BE GOT RIGHT.
    `retention.purge_expired` hard-deletes `interview_sessions` and lets the
    database cascade to the turns and the evaluation. With the default FK
    behaviour or CASCADE here, the summary would go in the same statement and
    B6.2 would silently do nothing at all — the table would look correct in
    every test that does not run a purge, and would be empty in production after
    six months. It is `ON DELETE SET NULL` and nullable: a NULL session means
    "the interview it summarises has been reaped", which is the expected end
    state of every row here, not a fault.

    `student_id` is the opposite decision and is a real FK with CASCADE. The
    summary IS the student's record; it must die with the student, and the row
    would name nobody without them. The same split `interview_sessions` already
    makes between `student_id` (CASCADE) and `consent_id` (SET NULL).

    EVERY SCORE IS NULLABLE, and for `interview_evaluations`' reason: a missing
    score and a zero mean opposite things to whoever reads this screen. A trend
    line must SKIP a NULL, never plot it at the origin.
    """

    __tablename__ = "interview_score_summaries"
    __table_args__ = (
        # One summary per interview, which is what makes the write idempotent:
        # the finalizer, a retry and the backfill can all attempt it and the
        # loser's IntegrityError is the no-op it should be. NULLs are distinct
        # in Postgres, so this constrains nothing once the session is reaped —
        # correctly, because by then there is no interview left to duplicate.
        UniqueConstraint("session_id", name="uq_interview_summary_session"),
        # "this student's scores, oldest first" — the trend, and the only read
        # pattern this table has. Leads with student_id, which is also what
        # indexes that foreign key.
        Index("ix_interview_summary_student_started", "student_id", "started_at"),
        # The same bound `interview_evaluations` carries, for the same reason:
        # it bounds a value and never demands one.
        CheckConstraint(
            "(overall_score IS NULL OR overall_score BETWEEN 0 AND 100) AND "
            "(communication_score IS NULL OR communication_score BETWEEN 0 AND 100) AND "
            "(domain_score IS NULL OR domain_score BETWEEN 0 AND 100) AND "
            "(structure_score IS NULL OR structure_score BETWEEN 0 AND 100)",
            name="ck_interview_summary_scores",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE")
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="SET NULL"), nullable=True
    )
    # The track CODE, copied rather than joined — `interview_tracks` rows can be
    # renamed, re-scoped or disabled, and a summary that changed its mind about
    # which interview it was would be worse than useless on a trend. NULL is the
    # generic interview, exactly as on `interview_sessions.specialization`.
    track_code: Mapped[str | None] = mapped_column(String, nullable=True)
    # Copied from the session for the same reason, and because the whole table
    # has to answer "when" long after the session row is gone.
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The session's terminal status: 'completed' | 'abandoned' | 'failed'. A
    # plain String, §6.1, and a summary IS written for the interviews that did
    # not finish — "three attempts abandoned in the first minute" is a fact a
    # mentor should be able to see, and a table of completions only would hide
    # exactly the student who needs help.
    status: Mapped[str] = mapped_column(String)
    # Named to match `interview_evaluations` COLUMN FOR COLUMN, rather than 04's
    # shorthand (`overall`, `communication`, …). The backfill and the finalizer
    # both copy one into the other, and a rename across that copy is precisely
    # where a mis-mapped column goes unnoticed for months.
    overall_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    communication_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    structure_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )


class InterviewCapReset(Base):
    """"Give this student their practice attempts back", with a reason (B6.4).

    The daily cap is a rolling 24-hour count, so there is nothing to zero: the
    reset is a LOWER BOUND on the window the count is taken over, and the count
    becomes `started_at >= GREATEST(now - 24h, the latest reset)`. Written that
    way — an extra bound on the existing window rather than a replacement for it
    — a second reset can only ever move the bound forward, and an old row can
    never widen the window back open.

    THE REASON IS NOT OPTIONAL, and the precedent is B3.1's disable: this is an
    action whose effect is invisible from the console a day later (the window
    has rolled past it anyway), and six months on nobody remembers whether it
    was a Bedrock outage, a browser that kept dropping, or a favour. The CHECK
    refuses whitespace so an empty box cannot satisfy it.
    """

    __tablename__ = "interview_cap_resets"
    __table_args__ = (
        # "the latest reset for this student" — the only read there is. Leads
        # with student_id, which indexes that foreign key.
        Index("ix_interview_cap_reset_student_at", "student_id", "at"),
        Index("ix_interview_cap_resets_by_user_id", "by_user_id"),
        CheckConstraint("length(btrim(reason)) > 0", name="ck_interview_cap_reset_reason"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # CASCADE: a reset names a student and records nothing without them.
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE")
    )
    # The bound. Ordinarily "now", but settable so an admin can say "from the
    # start of today" without arithmetic in the client.
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    # Who did it. SET NULL rather than a plain string here, unlike the catalogue
    # tables: this row is EMPTIED by both destructors anyway, so it costs
    # `purge_people` nothing, and referential integrity is worth having on a row
    # that grants somebody extra access to a billed upstream service.
    by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text)
