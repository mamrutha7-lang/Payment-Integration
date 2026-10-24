"""colleges + departments, and cohorts.department_id.

The two levels above a Cohort, so the student's locked profile card has a data
source. Before this, there was no College column anywhere in the schema and
Department was free text on `users` that nothing ever wrote — four of the card's
five fields could not be filled by any means short of direct SQL.

WHY department_id IS NULLABLE. `cohorts` predates `departments` (fea4515cdba5).
Existing cohort rows have no department to point at, so NOT NULL would make this
migration unrunnable on any database that already has one. An admin sets it
afterwards; until then the profile card shows a dash rather than inventing a
department.

WHY THE UNIQUE CONSTRAINT IS SCOPED TO THE COLLEGE. Two colleges may each run a
"CSE". A global unique code would force the college name into the department
code, which is how "CSE" becomes "BGSCET-CSE" and every screen inherits the
noise.

NO PG ENUMS. `status` is a plain String, per the rule app/models/voice_platform.py
sets out for degree_level: a new status value should be a data change, not a
CREATE TYPE migration with all three of AGENTS.md's enum gotchas attached.

EXPAND PHASE. Two new tables and one new nullable column — nothing existing
reads any of them, so this deploys safely ahead of the code that does.

Revision ID: e6b2d9c41a83
Revises: d5a1c8b30f47
Create Date: 2026-09-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6b2d9c41a83"
down_revision: Union[str, None] = "d5a1c8b30f47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")

    op.create_table(
        "colleges",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("campus", sa.String(), nullable=True),
        sa.Column("contact", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="ACTIVE", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_college_code"),
    )

    op.create_table(
        "departments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("college_id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("head", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="ACTIVE", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # No ondelete: the database refuses to delete a college that still has
        # departments. Archiving is the retirement path; a cascade here would
        # silently take cohorts and student links with it.
        sa.ForeignKeyConstraint(["college_id"], ["colleges.id"], name="fk_departments_college_id_colleges"),
        sa.UniqueConstraint("college_id", "code", name="uq_department_college_code"),
    )
    # No standalone index on departments.college_id: uq_department_college_code
    # above is (college_id, code) and serves a predicate on its leading column.
    # Confirmed with EXPLAIN — Postgres picks the composite for
    # `WHERE college_id = ?`. See the note on Department.college_id.

    op.add_column("cohorts", sa.Column("department_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_cohorts_department_id_departments",
        source_table="cohorts",
        referent_table="departments",
        local_cols=["department_id"],
        remote_cols=["id"],
    )
    op.create_index("ix_cohorts_department_id", "cohorts", ["department_id"])


def downgrade() -> None:
    op.drop_index("ix_cohorts_department_id", table_name="cohorts")
    op.drop_constraint("fk_cohorts_department_id_departments", "cohorts", type_="foreignkey")
    op.drop_column("cohorts", "department_id")
    op.drop_table("departments")
    op.drop_table("colleges")
