"""voice platform: dual-path (UG/PG) interview catalogue, candidates, policies, calls

Six tables behind app/voice_platform — the Undergraduate / Postgraduate
interview platform (Admin CRUD, the /ws/media-bridge socket, the S3 -> Lambda
-> SQS candidate ingest, and dual-channel call recordings). All vocabulary
columns are plain strings, on the precedent of the interview record.

Revision ID: b8f2d4c6a1e0
Revises: a71c3e5d9f42
Create Date: 2026-09-05 09:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b8f2d4c6a1e0'
down_revision: Union[str, None] = 'a71c3e5d9f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_specializations',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('degree_level', sa.String(length=2), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=160), nullable=False),
        sa.Column('persona', sa.Text(), nullable=False),
        sa.Column('frameworks', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('syllabus', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('nova_voice', sa.String(length=32), server_default='', nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('degree_level', 'key', name='uq_platform_spec_degree_key'),
    )
    op.create_table(
        'platform_questions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('degree_level', sa.String(length=2), nullable=False),
        sa.Column('specialization_id', sa.String(), nullable=False),
        sa.Column('phase', sa.String(length=16), server_default='probing', nullable=False),
        sa.Column('order_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('rubric', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['specialization_id'], ['platform_specializations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_platform_questions_spec_order', 'platform_questions', ['specialization_id', 'order_index'], unique=False)
    op.create_table(
        'platform_time_limits',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('degree_level', sa.String(length=2), nullable=False),
        sa.Column('specialization_id', sa.String(), nullable=True),
        sa.Column('max_seconds', sa.Integer(), nullable=False),
        sa.Column('wrap_up_reserve_seconds', sa.Integer(), server_default=sa.text('90'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['specialization_id'], ['platform_specializations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('degree_level', 'specialization_id', name='uq_platform_time_limit_scope'),
    )
    op.create_table(
        'platform_candidates',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('degree_level', sa.String(length=2), nullable=False),
        sa.Column('external_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=True),
        sa.Column('specialization_key', sa.String(length=64), nullable=True),
        sa.Column('programme', sa.String(length=120), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='queued', nullable=False),
        sa.Column('source', sa.String(length=16), server_default='bulk_upload', nullable=False),
        sa.Column('source_ref', sa.String(length=512), nullable=True),
        sa.Column('user_id', sa.String(), nullable=True),
        sa.Column('validation_notes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id'),
    )
    op.create_index('ix_platform_candidates_degree_status', 'platform_candidates', ['degree_level', 'status'], unique=False)
    op.create_table(
        'platform_recording_policies',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('degree_level', sa.String(length=2), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('retention_days', sa.Integer(), server_default=sa.text('180'), nullable=False),
        sa.Column('mix_format', sa.String(length=8), server_default='wav', nullable=False),
        sa.Column('keep_channels', sa.String(length=8), server_default='dual', nullable=False),
        sa.Column('presign_ttl_seconds', sa.Integer(), server_default=sa.text('3600'), nullable=False),
        sa.Column('updated_by', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('degree_level'),
    )
    op.create_table(
        'platform_call_sessions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('degree_level', sa.String(length=2), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('candidate_id', sa.String(), nullable=True),
        sa.Column('interview_session_id', sa.String(), nullable=True),
        sa.Column('specialization_key', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='running', nullable=False),
        sa.Column('time_limit_seconds', sa.Integer(), nullable=False),
        sa.Column('close_code', sa.Integer(), nullable=True),
        sa.Column('close_reason', sa.String(length=160), nullable=True),
        sa.Column('turns', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('recording_s3_key', sa.String(length=512), nullable=True),
        sa.Column('recording_bytes', sa.BigInteger(), nullable=True),
        sa.Column('recording_duration_ms', sa.Integer(), nullable=True),
        sa.Column('recording_truncated', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('recording_meta', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('dynamo_synced', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('opensearch_synced', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.ForeignKeyConstraint(['candidate_id'], ['platform_candidates.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['interview_session_id'], ['interview_sessions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_platform_call_sessions_degree_started', 'platform_call_sessions', ['degree_level', 'started_at'], unique=False)
    op.create_index('ix_platform_call_sessions_user', 'platform_call_sessions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_platform_call_sessions_user', table_name='platform_call_sessions')
    op.drop_index('ix_platform_call_sessions_degree_started', table_name='platform_call_sessions')
    op.drop_table('platform_call_sessions')
    op.drop_table('platform_recording_policies')
    op.drop_index('ix_platform_candidates_degree_status', table_name='platform_candidates')
    op.drop_table('platform_candidates')
    op.drop_table('platform_time_limits')
    op.drop_index('ix_platform_questions_spec_order', table_name='platform_questions')
    op.drop_table('platform_questions')
    op.drop_table('platform_specializations')
