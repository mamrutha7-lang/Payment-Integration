"""Interview question bank - admin-authored questions per track and phase.

One new table. See app/models/interview_bank.py: the free-style interviewer's
`question_bank` field finally has a writer for the four live tracks. `track` and
`phase` are plain strings (validated in code against SPECIALIZATIONS and
InterviewPhase); the (track, position) index serves the one query that matters,
"this track's questions in order".

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record: pure DDL on a brand new
table, no lock anybody is waiting on.

Revision ID: b4e8f2a6d1c3
Revises: a3d7e5f9c2b1
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4e8f2a6d1c3"
down_revision: Union[str, None] = "a3d7e5f9c2b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.create_table(
        "interview_bank_questions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("track", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_interview_bank_track_position", "interview_bank_questions", ["track", "position"])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_index("ix_interview_bank_track_position", table_name="interview_bank_questions")
    op.drop_table("interview_bank_questions")
