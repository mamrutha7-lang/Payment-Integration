"""students belong to a department too, not only to a batch

The student half of 31ca99852acd. That migration gave STAFF an institutional
pointer (`users.department_id`); students still had exactly one
(`students.cohort_id`), so a student with no batch resolved to no department
and no college — `_institution_for` returned an empty card and no
department-scoped read could see them.

That is not an edge case. `HIERARCHY_LEVELS` makes Course, Specialization and
Batch OPTIONAL while College and Department are REQUIRED, so the public
registration form forces every applicant to name a department and then had
nowhere to keep it: provisioning wrote `cohort_id` and dropped
`registrations.department_id` on the floor. A college that has not built its
batches yet — every college on day one — produced students with no institution
at all.

BACKFILLED FROM THE BATCH, because that is the one derivation that cannot be
wrong: a seated student's department IS their batch's department, and
`student_placement.resolve_student_department` keeps it that way on every write
afterwards. Students with no batch are left NULL deliberately — inventing a
department for them would be this migration guessing, and the console's bulk
"set department" is where a human answers that.

NAMED CONSTRAINT, for 31ca99852acd's reason: autogenerate emits
`create_foreign_key(None, ...)` whose downgrade cannot run, because there is no
constraint called None to drop.

Revision ID: 31f7a4c60b12
Revises: a91f3c5d80e4
Create Date: 2026-09-10 17:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "31f7a4c60b12"
down_revision: Union[str, None] = "a91f3c5d80e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK = "fk_students_department"
_IX = "ix_students_department_id"


def upgrade() -> None:
    op.add_column("students", sa.Column("department_id", sa.String(), nullable=True))
    op.create_index(_IX, "students", ["department_id"], unique=False)
    # No ondelete: the database REFUSES to delete a department that still has
    # students filed under it, matching `users.department_id`. A cascade here
    # would delete student rows to tidy a catalogue.
    op.create_foreign_key(_FK, "students", "departments", ["department_id"], ["id"])
    # The only safe backfill: a seated student's department is their batch's.
    op.execute(
        """
        UPDATE students
           SET department_id = cohorts.department_id
          FROM cohorts
         WHERE cohorts.id = students.cohort_id
           AND cohorts.department_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "students", type_="foreignkey")
    op.drop_index(_IX, table_name="students")
    op.drop_column("students", "department_id")
