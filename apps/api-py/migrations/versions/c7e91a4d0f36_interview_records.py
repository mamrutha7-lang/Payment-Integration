"""interview records — sessions, turns, evaluations, consents

Revision ID: c7e91a4d0f36
Revises: a3f1c7d5b820
Create Date: 2026-08-19 22:13:28.489012

THERE IS NO `CREATE TYPE` IN THIS MIGRATION, AND THAT IS DELIBERATE.

Four new tables carrying seven vocabulary columns between them — `status`,
`specialization`, `final_phase`, `speaker`, `transcription_status`,
`answer_quality`, `report_status` — and every one of them is a plain
`sa.String()`. So none of AGENTS.md's three Alembic enum gotchas apply here:
there is no enum column added to an existing table needing a hand-written
CREATE TYPE first, no new table reusing an existing enum that needs
`postgresql.ENUM(..., create_type=False)`, and no pair of columns sharing one
`Enum` instance. Their absence is a decision, not an oversight, which is why it
is stated at the top rather than left for the next editor to wonder about.

The reason is in `app/models/interview.py`'s module docstring: these vocabularies
will move. A fifth interview specialization is a one-line data change in
`interview_matrix.py` today; making it an `ALTER TYPE ... ADD VALUE` instead
would be a regression. `Upload.status` earned its PG enum because it is a frozen
three-state review workflow — none of these seven are frozen.

Creation order is forced by the foreign keys and must not be reshuffled:
`interview_consents` first (`interview_sessions.consent_id` points at it), then
`interview_sessions`, then `interview_turns` and `interview_evaluations`, which
both hang off the session. The downgrade unwinds in exactly that order reversed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c7e91a4d0f36'
down_revision: Union[str, None] = 'a3f1c7d5b820'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- interview_consents --------------------------------------------------
    # First, because interview_sessions.consent_id references it. A consent grant
    # is a ROW rather than a flag: that is what makes revocation expressible
    # (stamp revoked_at, keep the history) and what lets "was this student
    # consented at the time of interview X" stay answerable after they revoke.
    # Keyed on users.id and not students.id — a grant belongs to a person and
    # must survive a Student row being rewritten by a re-seeded roster.
    op.create_table('interview_consents',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('version', sa.String(), nullable=False),
    # Three scopes, three columns, all NOT NULL and none defaulted: a grant that
    # does not state each scope explicitly is not a grant.
    sa.Column('scope_live_ai', sa.Boolean(), nullable=False),
    sa.Column('scope_store_transcript', sa.Boolean(), nullable=False),
    sa.Column('scope_store_audio', sa.Boolean(), nullable=False),
    sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_agent', sa.String(), nullable=True),
    # A salted hash, never the address — enough to correlate two grants from one
    # machine, not enough to be a location log on a student.
    sa.Column('source_ip_hash', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    # One LIVE grant per (user, version), same partial-unique shape and same
    # reasoning as uq_conversation_one_active_per_owner (migration 6afb55d18ed8):
    # a double-clicked button would otherwise leave two live grants, and a
    # revocation would then clear one and leave the student consented by the
    # other. Partial on revoked_at IS NULL so the revoked history stays.
    op.create_index('uq_interview_consent_active', 'interview_consents', ['user_id', 'version'], unique=True, postgresql_where=sa.text('revoked_at IS NULL'))

    # --- interview_sessions --------------------------------------------------
    # One row per mock interview. Note the three FKs are deliberately three
    # different delete rules, and each one is load-bearing:
    #   students.id      CASCADE  — an interview with no subject is not a record.
    #   conversations.id SET NULL — the chat thread is cleared by the student and
    #                               hard-deleted by retention on a 90-day clock;
    #                               neither may destroy a 180-day interview
    #                               record. NULL here means "the thread is gone".
    #   consents.id      SET NULL — deleting a grant must not delete the
    #                               interviews conducted under it.
    op.create_table('interview_sessions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('student_id', sa.String(), nullable=False),
    sa.Column('conversation_id', sa.String(), nullable=True),
    sa.Column('specialization', sa.String(), nullable=True),
    sa.Column('status', sa.String(), server_default='running', nullable=False),
    sa.Column('terminal_reason', sa.String(), nullable=True),
    sa.Column('final_phase', sa.String(), nullable=True),
    sa.Column('answers_accepted', sa.Integer(), server_default='0', nullable=False),
    # turns_emitted vs turns_persisted: the pair that answers "the call sounded
    # fine and saved nothing" without a join. Turn writes are fire-and-forget so
    # a bad write can never kill a live call; the cost is that drops are
    # invisible, and this is what makes them visible again.
    sa.Column('turns_emitted', sa.Integer(), server_default='0', nullable=False),
    sa.Column('turns_persisted', sa.Integer(), server_default='0', nullable=False),
    sa.Column('close_code', sa.Integer(), nullable=True),
    sa.Column('conn_id', sa.String(), nullable=True),
    sa.Column('upstream_session_id', sa.String(), nullable=True),
    sa.Column('consent_id', sa.String(), nullable=True),
    # Audio. Capture is built in a later wave; the columns land now so that
    # commit is a code change and not a migration against a populated table.
    # audio_recorded is the field to TEST — never `audio_path IS NOT NULL`. A
    # NULL path means "no audio is stored for this interview", which is equally
    # true when capture was off, when the student refused scope_store_audio, when
    # the write failed, and for every interview that predates capture. Those are
    # four different facts and a NULL cannot tell them apart; the boolean can.
    sa.Column('audio_recorded', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('audio_path', sa.String(), nullable=True),
    sa.Column('audio_bytes', sa.Integer(), nullable=True),
    sa.Column('audio_duration_ms', sa.Integer(), nullable=True),
    # A truncated recording that does not say so gets replayed as if it were
    # complete. Never truncate silently.
    sa.Column('audio_truncated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    # heartbeat_at is what makes orphan detection possible at all: a process
    # killed with -9 leaves status='running' forever, and by started_at alone a
    # long healthy interview and a dead one are indistinguishable.
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retention_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['consent_id'], ['interview_consents.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    # NO unique constraint beyond the PK. "One running interview per student" is
    # the router's per-user cap, NOT a partial unique index on (student_id) WHERE
    # status='running' — a killed process leaves a running row, and such an index
    # would lock that student out of the feature until the sweeper ran, turning a
    # crash into a support ticket.
    )
    # Both partial indexes exist to serve one sweeper predicate each, and are
    # partial because the rows they exclude will vastly outnumber the rows they
    # include once this table has a semester of history in it.
    op.create_index('ix_interview_session_orphans', 'interview_sessions', ['heartbeat_at'], unique=False, postgresql_where=sa.text("status = 'running'"))
    op.create_index('ix_interview_session_retention', 'interview_sessions', ['retention_until'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_interview_session_student_started', 'interview_sessions', ['student_id', 'started_at'], unique=False)

    # --- interview_evaluations -----------------------------------------------
    # One scorecard per interview, and a row is written on EVERY path — including
    # 'unavailable' when the interview never reached wrap_up. A row saying
    # nothing arrived is a record; a missing row is a question nobody can answer.
    op.create_table('interview_evaluations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('interview_session_id', sa.String(), nullable=False),
    sa.Column('report_status', sa.String(), nullable=False),
    # Nullable EVEN WHEN report_status='ok'. To the mentor reading this, a
    # missing score and a zero mean opposite things. Never fabricate one.
    sa.Column('overall_score', sa.Integer(), nullable=True),
    sa.Column('communication_score', sa.Integer(), nullable=True),
    sa.Column('domain_score', sa.Integer(), nullable=True),
    sa.Column('structure_score', sa.Integer(), nullable=True),
    sa.Column('strengths', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('improvements', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('drill', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    # DIRECTOR/ADMIN only, and redacted by the retention job the way
    # AgentRun.question already is: it routinely holds the model's private
    # reasoning about the student.
    sa.Column('raw_response', sa.Text(), nullable=True),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['interview_session_id'], ['interview_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    # The constraint that makes the evaluation write idempotent under retry: the
    # report's response.done can land after the deadline already wrote
    # report_status='timeout', and the loser's IntegrityError is swallowed.
    sa.UniqueConstraint('interview_session_id', name='uq_interview_evaluation_session')
    )

    # --- interview_turns -----------------------------------------------------
    # One row per utterance. `content` is NOT NULL and an EMPTY STRING IS LEGAL:
    # a failed or timed-out transcription is content='' with
    # transcription_status saying which, and those are the very turns this table
    # exists to keep. message_id's SET NULL must live in the DDL and not only in
    # the ORM — retention.purge_expired issues a bulk delete(Message) that
    # bypasses ORM cascades entirely, and without this rule that delete would
    # fail on the FK and take the retention job down with it.
    op.create_table('interview_turns',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('interview_session_id', sa.String(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('speaker', sa.String(), nullable=False),
    sa.Column('phase', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('transcription_status', sa.String(), server_default='ok', nullable=False),
    sa.Column('answer_quality', sa.String(), nullable=True),
    sa.Column('counted_as_answer', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_partial', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('provider_turn_id', sa.String(), nullable=True),
    sa.Column('message_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['interview_session_id'], ['interview_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    # The record's identity and the second dedup layer (the relay's own pop is
    # the first). Nullable provider_turn_id is fine here: Postgres treats NULLs
    # as distinct, so turns recorded under a synthetic key coexist.
    sa.UniqueConstraint('interview_session_id', 'provider_turn_id', name='uq_interview_turn_provider')
    )
    # NOT unique, deliberately. Ordering is a display concern; record uniqueness
    # is provider_turn_id's job. A fire-and-forget writer must never be able to
    # raise on a constraint whose violation is cosmetic — a duplicated seq shows
    # a turn twice, a raise here loses the turn.
    op.create_index('ix_interview_turn_session_seq', 'interview_turns', ['interview_session_id', 'seq'], unique=False)


def downgrade() -> None:
    # Honest about what rolling back costs: this DROPS every interview record —
    # transcripts, scorecards and the consent grants they were conducted under.
    # The `messages` rows with channel='interview' survive (they are a different
    # table and this migration never touched them), so the conversation history a
    # student sees is unaffected; what is destroyed is the reviewable record,
    # which is the part nothing else can reconstruct. This is a rollback path,
    # not routine maintenance.
    #
    # Reverse dependency order: children before parents, sessions before the
    # consents they reference. Postgres would drop each table's indexes with it,
    # but they are dropped explicitly because the downgrade is the upgrade in
    # reverse and reading the two side by side is how a missing step gets caught.
    op.drop_index('ix_interview_turn_session_seq', table_name='interview_turns')
    op.drop_table('interview_turns')
    op.drop_table('interview_evaluations')
    op.drop_index('ix_interview_session_student_started', table_name='interview_sessions')
    op.drop_index('ix_interview_session_retention', table_name='interview_sessions', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_interview_session_orphans', table_name='interview_sessions', postgresql_where=sa.text("status = 'running'"))
    op.drop_table('interview_sessions')
    op.drop_index('uq_interview_consent_active', table_name='interview_consents', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_table('interview_consents')
