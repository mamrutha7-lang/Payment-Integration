"""staff belong to a department, and through it a college

A student's institution is reached through ONE pointer (`students.cohort_id`)
and stored on nothing else. Staff had no such pointer: the only institutional
field on a staff row was the free-text `users.department`, so no query could
group faculty by a real department and no staff row could name its college at
all. This adds the faculty counterpart.

NAMED CONSTRAINT, deliberately. Autogenerate emitted `op.create_foreign_key(None,
...)` and a matching `op.drop_constraint(None, ...)` — and the downgrade of that
pair cannot run, because there is no constraint called None to drop. It is also
the cycle-closing FK (users -> departments -> colleges -> users), which is why
the model declares it `use_alter=True`.

Revision ID: 31ca99852acd
Revises: e7b1c9d4a2f6
Create Date: 2026-09-10 15:10:20.175171
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "31ca99852acd"
down_revision: Union[str, None] = "e7b1c9d4a2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK = "fk_users_department"
_IX = "ix_users_department_id"


def upgrade() -> None:
    op.add_column("users", sa.Column("department_id", sa.String(), nullable=True))
    op.create_index(_IX, "users", ["department_id"], unique=False)
    # No ondelete: the database REFUSES to delete a department that still has
    # staff filed under it, matching how `departments` refuses a college with
    # departments. Archiving is the retirement path here too, and a cascade
    # would take staff accounts with it.
    op.create_foreign_key(_FK, "users", "departments", ["department_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(_FK, "users", type_="foreignkey")
    op.drop_index(_IX, table_name="users")
    op.drop_column("users", "department_id")
