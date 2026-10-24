"""skill claim student_note

The claim form asks the student for a short note to the mentor ("what the
certificate covers"). `review_note` is the mentor's reply and cannot carry it —
the two travel in opposite directions and a mentor writing a decision would
overwrite the context they were given. So the student's note gets its own
column.

Revision ID: f3c81a5d2e70
Revises: d6a4e7f91b22
Create Date: 2026-09-04 15:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3c81a5d2e70'
down_revision: Union[str, None] = 'd6a4e7f91b22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('skill_claims', sa.Column('student_note', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('skill_claims', 'student_note')
