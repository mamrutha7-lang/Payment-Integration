"""Registration hierarchy claim - College, Department, Course, Specialization,
Batch, as the applicant picked them on the public form.

Five nullable, indexed, SET NULL foreign keys on `registrations`. They are the
applicant's CLAIM and sit beside `cohort_id`, which the rule engine stamps and
which still wins at provisioning; the requested batch fills the gap when no
rule seats them. Nullable so every existing application is untouched, and an
ADD COLUMN ... NULL is a catalogue write in Postgres, not a rewrite. The FK
constraints validate instantly: every existing row is NULL.

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record.

Revision ID: c5f9a3e7d2b4
Revises: b4e8f2a6d1c3
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5f9a3e7d2b4"
down_revision: Union[str, None] = "b4e8f2a6d1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("college_id", "colleges"),
    ("department_id", "departments"),
    ("course_id", "academic_courses"),
    ("specialization_id", "academic_specializations"),
    ("requested_cohort_id", "cohorts"),
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    for column, table in _COLUMNS:
        op.add_column("registrations", sa.Column(column, sa.String(), nullable=True))
        op.create_foreign_key(
            f"fk_registrations_{column}", "registrations", table, [column], ["id"], ondelete="SET NULL"
        )
        op.create_index(f"ix_registrations_{column}", "registrations", [column])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    for column, _table in reversed(_COLUMNS):
        op.drop_index(f"ix_registrations_{column}", table_name="registrations")
        op.drop_constraint(f"fk_registrations_{column}", "registrations", type_="foreignkey")
        op.drop_column("registrations", column)
