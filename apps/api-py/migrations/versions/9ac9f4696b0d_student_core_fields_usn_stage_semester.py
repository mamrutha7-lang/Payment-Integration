"""student core fields (usn stage semester)

Revision ID: 9ac9f4696b0d
Revises: 047481d26bbd
Create Date: 2026-08-15 03:29:02.166884
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '9ac9f4696b0d'
down_revision: Union[str, None] = '047481d26bbd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Adding an enum COLUMN to an existing table does not auto-create the PG type
# (unlike create_table), so create it explicitly and reference it with
# create_type=False on the column.
stage_enum = postgresql.ENUM("REBOOT", "EXCEL", "EXCEL_ADVANCED", "ELEVATE", name="stage")


def upgrade() -> None:
    stage_enum.create(op.get_bind(), checkfirst=True)
    op.add_column('students', sa.Column('usn', sa.String(), nullable=True))
    op.add_column('students', sa.Column('cohort_id', sa.String(), nullable=True))
    op.add_column('students', sa.Column('mentor_id', sa.String(), nullable=True))
    op.add_column(
        'students',
        sa.Column(
            'current_stage',
            postgresql.ENUM(
                "REBOOT", "EXCEL", "EXCEL_ADVANCED", "ELEVATE", name="stage", create_type=False
            ),
            server_default='EXCEL',
            nullable=False,
        ),
    )
    op.add_column('students', sa.Column('current_semester', sa.Integer(), server_default='1', nullable=False))
    op.add_column('students', sa.Column('enrolled_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('students', sa.Column('weekly_hour_target', sa.Float(), server_default='12', nullable=False))
    op.create_unique_constraint('uq_students_usn', 'students', ['usn'])
    op.create_foreign_key('fk_students_mentor', 'students', 'mentors', ['mentor_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_students_mentor', 'students', type_='foreignkey')
    op.drop_constraint('uq_students_usn', 'students', type_='unique')
    op.drop_column('students', 'weekly_hour_target')
    op.drop_column('students', 'enrolled_at')
    op.drop_column('students', 'current_semester')
    op.drop_column('students', 'current_stage')
    op.drop_column('students', 'mentor_id')
    op.drop_column('students', 'cohort_id')
    op.drop_column('students', 'usn')
    stage_enum.drop(op.get_bind(), checkfirst=True)
