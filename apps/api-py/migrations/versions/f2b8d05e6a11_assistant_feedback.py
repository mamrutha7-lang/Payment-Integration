"""assistant_feedback (+ feedbackrating enum)

Revision ID: f2b8d05e6a11
Revises: e1a7c9d34f20
Create Date: 2026-08-15 23:12:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f2b8d05e6a11'
down_revision: Union[str, None] = 'e1a7c9d34f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# feedbackrating is a BRAND-NEW enum. Per AGENTS.md, CREATE TYPE it explicitly
# BEFORE the table and reference it with create_type=False in create_table, so
# the table build does not try to CREATE TYPE a second time ("already exists").
feedbackrating = postgresql.ENUM(
    'HELPFUL', 'NOT_HELPFUL', 'REPORT', name='feedbackrating'
)


def upgrade() -> None:
    feedbackrating.create(op.get_bind(), checkfirst=True)
    op.create_table(
        'assistant_feedback',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('run_id', sa.String(), nullable=False),
        sa.Column('owner_user_id', sa.String(), nullable=False),
        sa.Column(
            'rating',
            postgresql.ENUM(
                'HELPFUL', 'NOT_HELPFUL', 'REPORT',
                name='feedbackrating', create_type=False,
            ),
            nullable=False,
        ),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'owner_user_id', name='uq_feedback_run_owner'),
    )
    op.create_index('ix_feedback_run', 'assistant_feedback', ['run_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_feedback_run', table_name='assistant_feedback')
    op.drop_table('assistant_feedback')
    feedbackrating.drop(op.get_bind(), checkfirst=True)
