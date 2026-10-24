"""academic_courses, academic_specializations, the two nullable ancestor columns
on cohorts, and created_by_user_id on all four institution tables.

THE TWO LEVELS app/models/institution.py's docstring once drew and marked
"deliberately not built yet". Inserting them costs two tables and two nullable
columns and touches NO existing row — no student, no cohort — which is the
property the build log's L1-01 §9 refused to trade away by denormalising onto
`students`. An "Academic year" level was drawn too and is NOT built: the batch
already is the year (batch_label, start_date, end_date), and a table would have
been the fifth representation of one fact. See the model docstring.

NULLABLE, AND STAYING THAT WAY. Requiredness lives in HIERARCHY_LEVELS in
app/models/institution.py, not in the schema. A NOT NULL here would turn "the
product owner decided this is mandatory now" into a migration that aborts on the
first legacy batch, mid-deploy. The switch is one line in that constant.

created_by_user_id ON ALL FOUR. The console's audit view wants who/what/when
and nothing recorded who. Nullable: the seed and the CLIs have no user. Added
to colleges and departments here rather than in a fourth migration, because
nothing is deployed yet and a schema that gains the same column in two
revisions is one that will gain it a third time.

NO PG ENUMS. `status` is a plain String — the rule voice_platform.py set and
institution.py restated. NO CHECK CONSTRAINT ON IT EITHER, on purpose: a CHECK
is alterable without CREATE TYPE, but adding a status value would still be a
migration, and the repo's rule is that a new status is a DATA change. The
allowlist is `_SETTABLE_STATUSES` in app/routers/admin.py.

OFFLINE-CLEAN. No op.get_bind(), no .rowcount, no print(). d5a1c8b30f47 records
what those cost under `alembic upgrade --sql`. This revision is pure DDL.

NOT `CREATE INDEX CONCURRENTLY` AND NOT `NOT VALID` — DELIBERATELY. Both are the
right pattern on a large table, and both are wrong here: CONCURRENTLY cannot
run inside a transaction and env.py runs the whole upgrade in one, and the
tables these FKs reference are created EMPTY three statements earlier, so the
validating scan has nothing to scan. `cohorts` numbers in the dozens. The
escalation, when a table is large enough to need it, is the split d5a1c8b30f47
describes: NOT VALID here, VALIDATE CONSTRAINT in a follow-up.

EXPAND PHASE. Nothing existing reads any of it, so it deploys ahead of the code.

Revision ID: f7c3a1d92e54
Revises: e6b2d9c41a83
Create Date: 2026-09-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7c3a1d92e54"
down_revision: Union[str, None] = "e6b2d9c41a83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _stamp_columns() -> list[sa.Column]:
    """The system columns every institution table carries, in one place."""
    return [
        sa.Column("status", sa.String(), server_default="ACTIVE", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    # SET LOCAL, never SET: env.py runs the whole `upgrade head` in ONE
    # transaction on ONE connection, so a session-scoped SET leaks into every
    # migration downstream of this file, forever. See d5a1c8b30f47.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")

    # --- who created it, on the two tables that already exist ---------------
    # ADD COLUMN ... NULL with no default is metadata-only on PG 11+.
    for table in ("colleges", "departments"):
        op.add_column(table, sa.Column("created_by_user_id", sa.String(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_created_by_user_id_users", table, "users", ["created_by_user_id"], ["id"]
        )

    # --- the two new levels ---------------------------------------------------
    op.create_table(
        "academic_courses",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("department_id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("duration_months", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        *_stamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        # No ondelete, like departments -> colleges: the database REFUSES to
        # delete a department that still has courses. Archiving is the
        # retirement path; a cascade would silently take specializations and
        # every cohort pointer into them.
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_academic_courses_department_id_departments"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_academic_courses_created_by_user_id_users"
        ),
        # NAMED, and byte-identical to __table_args__ in institution.py. An
        # unnamed unique is called one thing by create_all() and another by a
        # migration — the fault `cohorts.code` still has and `colleges` fixed.
        sa.UniqueConstraint("department_id", "code", name="uq_academic_course_department_code"),
    )
    # No standalone index on department_id: the composite unique above leads
    # with it, and EXPLAIN picks it for `WHERE department_id = ?`. Same reason
    # e6b2d9c41a83 dropped ix_departments_college_id.

    op.create_table(
        "academic_specializations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("course_id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        *_stamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["academic_courses.id"],
            name="fk_academic_specializations_course_id_academic_courses",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_academic_specializations_created_by_user_id_users",
        ),
        sa.UniqueConstraint("course_id", "code", name="uq_academic_specialization_course_code"),
    )

    # --- the ancestor pointers on cohorts -------------------------------------
    # Each FK takes SHARE ROW EXCLUSIVE on `cohorts` and the referenced table
    # while validating — but both referenced tables were created empty above
    # and every new column is NULL, so the scan finds nothing.
    for col, ref, fk, ix in (
        (
            "course_id",
            "academic_courses",
            "fk_cohorts_course_id_academic_courses",
            "ix_cohorts_course_id",
        ),
        (
            "specialization_id",
            "academic_specializations",
            "fk_cohorts_specialization_id_academic_specializations",
            "ix_cohorts_specialization_id",
        ),
    ):
        op.add_column("cohorts", sa.Column(col, sa.String(), nullable=True))
        op.create_foreign_key(fk, "cohorts", ref, [col], ["id"])
        # Named to match what index=True on the model produces, exactly as
        # e6b2d9c41a83 did for ix_cohorts_department_id — so autogenerate
        # stays quiet and a later drop_index finds it by the same name.
        op.create_index(ix, "cohorts", [col])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Reverse order, innermost first — the same FK ordering the `tracker`
    # fixture in tests/test_admin_institution.py demonstrates.
    for col, fk, ix in (
        ("specialization_id", "fk_cohorts_specialization_id_academic_specializations", "ix_cohorts_specialization_id"),
        ("course_id", "fk_cohorts_course_id_academic_courses", "ix_cohorts_course_id"),
    ):
        op.drop_index(ix, table_name="cohorts")
        op.drop_constraint(fk, "cohorts", type_="foreignkey")
        op.drop_column("cohorts", col)
    op.drop_table("academic_specializations")
    op.drop_table("academic_courses")
    for table in ("departments", "colleges"):
        op.drop_constraint(f"fk_{table}_created_by_user_id_users", table, type_="foreignkey")
        op.drop_column(table, "created_by_user_id")
