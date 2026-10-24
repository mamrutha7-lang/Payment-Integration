"""course fee and enrolment payment

Revision ID: b8e4a2d9c6f1
Revises: a7d3f1c8e2b9
Create Date: 2026-10-24 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e4a2d9c6f1'
down_revision: Union[str, None] = 'a7d3f1c8e2b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('courses', sa.Column('fee_paise', sa.Integer(), nullable=True))
    op.add_column('fees', sa.Column('course_code', sa.String(), nullable=True))
    op.create_foreign_key('fk_fees_course_code', 'fees', 'courses', ['course_code'], ['code'], ondelete='SET NULL')
    op.create_index('ix_fees_student_course', 'fees', ['student_id', 'course_code'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_fees_student_course', table_name='fees')
    op.drop_constraint('fk_fees_course_code', 'fees', type_='foreignkey')
    op.drop_column('fees', 'course_code')
    op.drop_column('courses', 'fee_paise')
