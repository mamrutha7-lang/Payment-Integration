"""index the fee foreign keys that no composite index leads with

Revision ID: c1a5e7f3b9d2
Revises: b8e4a2d9c6f1
Create Date: 2026-10-24 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c1a5e7f3b9d2'
down_revision: Union[str, None] = 'b8e4a2d9c6f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_fees_course_code', 'fees', ['course_code'], unique=False)
    op.create_index('ix_fees_created_by', 'fees', ['created_by'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_fees_created_by', table_name='fees')
    op.drop_index('ix_fees_course_code', table_name='fees')
