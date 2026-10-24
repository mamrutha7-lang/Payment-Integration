"""students.second_specialization_id — the other half of a dual specialization.

Revision ID: e2b7c4d9f1a6
Revises: d4c8e1f7a2b9
Create Date: 2026-09-23

d4c8e1f7a2b9 gave the APPLICATION somewhere to put the second tick of a dual
specialization and deliberately stopped there, so the choice lived on the
registration row and nowhere else: once a student was approved the Main Admin
could not see it on the roster, and could not set it for a student whose dual
choice reached the office by phone. This carries it onto the student. The first
specialization is still the batch's own, read through `cohort_id`; this column
is the second and nothing else. Nullable, indexed, SET NULL, the application
column's shape for the application column's reasons.

THE BACKFILL IS A COPY OF A FACT ALREADY RECORDED, not a guess: every student
provisioned from an application that named two specializations gets the second
one, from the latest such application. Where the office has since seated that
student in the batch of the application's SECOND stream, the first is the one
the batch does not already say, so the other tick is written instead. A student
whose application named one specialization, or who never applied through the
form, gets NULL — which is what they have.

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2b7c4d9f1a6"
down_revision: Union[str, None] = "d4c8e1f7a2b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMN = "second_specialization_id"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.add_column("students", sa.Column(_COLUMN, sa.String(), nullable=True))
    op.create_foreign_key(
        f"fk_students_{_COLUMN}",
        "students",
        "academic_specializations",
        [_COLUMN],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(f"ix_students_{_COLUMN}", "students", [_COLUMN])
    op.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (r.approved_student_id)
                   r.approved_student_id AS student_id,
                   r.specialization_id AS first_id,
                   r.second_specialization_id AS second_id
              FROM registrations AS r
             WHERE r.approved_student_id IS NOT NULL
               AND r.second_specialization_id IS NOT NULL
               AND r.status IN ('APPROVED', 'AUTO_APPROVED')
             ORDER BY r.approved_student_id, r.created_at DESC
        )
        UPDATE students AS s
           SET second_specialization_id = CASE
                   WHEN (SELECT c.specialization_id FROM cohorts AS c WHERE c.id = s.cohort_id)
                        = latest.second_id
                   THEN latest.first_id
                   ELSE latest.second_id
               END
          FROM latest
         WHERE s.id = latest.student_id
           AND s.second_specialization_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_index(f"ix_students_{_COLUMN}", table_name="students")
    op.drop_constraint(f"fk_students_{_COLUMN}", "students", type_="foreignkey")
    op.drop_column("students", _COLUMN)
