"""course shape, semester history, batch/student status and the per-course catalogue

Revision ID: c3a9f1e7d2b4
Revises: e5f2c86d40b1
Create Date: 2026-09-13

B4.1, B4.3, B4.4 and B13 of docs/redesign-2026-09/04-backend-changes.md, in one
revision because they are one dependency chain: the course has to carry a shape
before a catalogue can be scoped to it, and both have to exist before a
promotion can read either.

THERE IS NO `duration_years`, though 04 asks for one. `academic_courses.
duration_months` already exists, is already written by the admin console and is
already printed on screen. A second duration column is one fact stored twice and
rounds 18-month programmes wrong; years are derived where a screen wants them.
Only `total_semesters` is added.

EVERY ENUM HERE IS ONE THAT ALREADY EXISTS, hand-written for AGENTS.md's gotchas
(a) and (b): adding an enum COLUMN to an existing table does not CREATE TYPE,
and both types are already in the database, so a bare `sa.Enum` dies on "type
already exists". `degree_level` was created with `jobs` and is reused verbatim
the way fea4515cdba5 reuses it for `cohorts`; `stage` was created with the
original schema and is already reused by `courses.stage` and
`approved_certifications.stage`. `create_type=False` on both.

THE TWO NEW STATUS COLUMNS ARE PLAIN STRINGS, not enums, following
app/models/institution.py's stated rule: a new status value must be a data
change, not a CREATE TYPE migration carrying all three of those gotchas.

------------------------------------------------------------------------------
THE BACKFILL, AND WHERE IT DELIBERATELY DOES NOTHING
------------------------------------------------------------------------------

A course inherits its degree level from its batches. That is unambiguous only
when every batch under it agrees, and NOTHING IN THE SCHEMA FORBIDS A COURSE
FROM HAVING BOTH: `cohorts.degree_level` is required per batch and is never
checked against a sibling. A course running an integrated programme, or one
mid-way through being split, legitimately has UG batches and PG batches at once.

So this follows b2c9e04a7731's precedent exactly — act only where exactly one
candidate resolves, and otherwise write nothing:

  * exactly ONE distinct `degree_level` across the course's batches -> set it,
    and set `total_semesters` to 8 (UG) or 4 (PG);
  * ZERO batches, or TWO distinct levels -> BOTH COLUMNS STAY NULL.

A NULL here is not a failure state. `total_semesters` NULL means the student's
semester keeps the old flat bound (admin_students.MAX_SEMESTER) instead of every
edit being refused, and `GET /api/admin/cohorts/incomplete` is the screen where
an unfilled course surfaces to a human who knows the answer. Picking the
majority level would be silent and wrong for the minority batches, and a human
correcting a value nobody told them about is the hardest kind of bug to find.

The catalogue attachment obeys the same rule. 04 says existing programme-wide
`approved_certifications` are "attached to BGSCET/MBA by migration"; there is no
guaranteed BGSCET row on any deployment (`python -m app.seed` refuses on
ENV=prod, and the production-safe seeds create neither a college nor a course),
and a hardcoded `WHERE code = 'BGSCET'` would be a no-op on every real one. So:
exactly one college AND exactly one academic course -> attach every unattached
row to that pair, which writes down what is already true. Anything else -> the
rows stay NULL, which MEANS programme-wide and stays visible everywhere. A NOT
NULL column with a fabricated default is how a second college silently inherits
the first's catalogue.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a9f1e7d2b4"
down_revision: Union[str, None] = "e5f2c86d40b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: UG 8, PG 4 — 04's numbers, and the only place this migration hardcodes them.
_SEMESTERS_BY_LEVEL = {"UG": 8, "PG": 4}

_STAGES = ("REBOOT", "EXCEL", "EXCEL_ADVANCED", "ELEVATE")


def _degree_level() -> postgresql.ENUM:
    """The EXISTING type. Gotchas (a) + (b); see the docstring."""
    return postgresql.ENUM("UG", "PG", name="degree_level", create_type=False)


def _stage() -> postgresql.ENUM:
    return postgresql.ENUM(*_STAGES, name="stage", create_type=False)


# --------------------------------------------------------------------------- #
# The two backfills are FUNCTIONS TAKING A CONNECTION, not inline `op.execute`
# blocks, so that tests/test_semester_schema.py can run the real thing against
# real rows and roll it back. A backfill whose abstention is only asserted by
# reading the SQL is a backfill nobody has watched decline to act.
# --------------------------------------------------------------------------- #


def apply_course_levels(bind) -> list[tuple[str, str]]:
    """Give each course its batches' degree level, where they agree. Returns
    the (course_id, level) pairs it wrote, which is empty for a mixed course."""
    resolved = bind.execute(
        sa.text(
            "SELECT course_id, MIN(degree_level::text) AS level"
            "  FROM cohorts"
            " WHERE course_id IS NOT NULL"
            " GROUP BY course_id"
            # THIS CLAUSE IS THE UNAMBIGUITY TEST. There is no second query
            # anywhere that could fall out of step with it.
            " HAVING COUNT(DISTINCT degree_level) = 1"
        )
    ).all()
    for course_id, level in resolved:
        bind.execute(
            sa.text(
                "UPDATE academic_courses"
                "   SET degree_level = CAST(:level AS degree_level),"
                "       total_semesters = COALESCE(total_semesters, :semesters)"
                " WHERE id = :id AND degree_level IS NULL"
            ).bindparams(level=level, semesters=_SEMESTERS_BY_LEVEL[level], id=course_id)
        )
    return [(c, lvl) for c, lvl in resolved]


def attach_catalogue(bind) -> bool:
    """Attach the programme-wide certification rows, but only when exactly one
    college and exactly one course make the target unambiguous. Returns whether
    it acted."""
    colleges = bind.execute(sa.text("SELECT id FROM colleges")).scalars().all()
    courses = bind.execute(sa.text("SELECT id FROM academic_courses")).scalars().all()
    if len(colleges) != 1 or len(courses) != 1:
        return False
    bind.execute(
        sa.text(
            "UPDATE approved_certifications"
            "   SET college_id = :college, course_id = :course"
            " WHERE college_id IS NULL AND course_id IS NULL"
        ).bindparams(college=colleges[0], course=courses[0])
    )
    return True


def upgrade() -> None:
    bind = op.get_bind()

    # ---------------------------------------------------------------- #
    # 1. The course carries the shape (B4.1)
    # ---------------------------------------------------------------- #
    op.add_column(
        "academic_courses", sa.Column("degree_level", _degree_level(), nullable=True)
    )
    op.add_column(
        "academic_courses", sa.Column("total_semesters", sa.Integer(), nullable=True)
    )

    # Exactly one distinct level across the course's batches, or nothing.
    apply_course_levels(bind)

    # ---------------------------------------------------------------- #
    # 2. Lifecycles: the batch's (B4.4) and the student's (B4.4)
    # ---------------------------------------------------------------- #
    # `cohorts` was the only table on the spine without a status. Both columns
    # carry a server_default so every existing row fills in as ACTIVE, which is
    # what every existing row is.
    op.add_column(
        "cohorts",
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
    )
    op.add_column(
        "students",
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
    )

    # ---------------------------------------------------------------- #
    # 3. The catalogue gets a scope (B13)
    # ---------------------------------------------------------------- #
    op.add_column(
        "approved_certifications", sa.Column("college_id", sa.String(), nullable=True)
    )
    op.add_column(
        "approved_certifications", sa.Column("course_id", sa.String(), nullable=True)
    )
    op.create_foreign_key(
        "fk_approvedcert_college",
        "approved_certifications",
        "colleges",
        ["college_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_approvedcert_course",
        "approved_certifications",
        "academic_courses",
        ["course_id"],
        ["id"],
    )
    # Each FK leads its own index or it is no help to the lookup at all.
    op.create_index("ix_approvedcert_college", "approved_certifications", ["college_id"])
    op.create_index("ix_approvedcert_course", "approved_certifications", ["course_id"])

    attach_catalogue(bind)

    # ---------------------------------------------------------------- #
    # 4. The two per-course catalogue tables (B13)
    # ---------------------------------------------------------------- #
    op.create_table(
        "stage_rules",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("course_id", sa.String(), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column("stage", _stage(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("semester >= 1", name="ck_stage_rule_semester"),
        sa.ForeignKeyConstraint(["course_id"], ["academic_courses.id"]),
        sa.PrimaryKeyConstraint("id"),
        # Leads with course_id, which is what indexes the foreign key. A second
        # index on the column alone would be a duplicate of this one.
        sa.UniqueConstraint("course_id", "semester", name="uq_stage_rule_course_semester"),
    )
    op.create_table(
        "badge_course_map",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("course_id", sa.String(), nullable=False),
        # No foreign key: the 48-badge catalogue is CODE, so there is no table
        # to point at. Validated against BADGE_BY_CODE at the API edge, like
        # approved_certifications.badge_code.
        sa.Column("badge_code", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.ForeignKeyConstraint(["course_id"], ["academic_courses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "badge_code", name="uq_badge_course_map"),
    )
    # DELIBERATELY EMPTY. Absence means enabled: seeding 48 rows per course
    # would put the badge catalogue's shape in the database in thousands of
    # places, and a course added next week would start with no badges at all.

    # ---------------------------------------------------------------- #
    # 5. The semester history (B4.3)
    # ---------------------------------------------------------------- #
    op.create_table(
        "student_semester_history",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("from_semester", sa.Integer(), nullable=False),
        sa.Column("to_semester", sa.Integer(), nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("by_user_id", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, server_default="promote"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_semester >= 1 AND to_semester >= 1",
            name="ck_semester_history_bounds",
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        # SET NULL: purge_people deletes every account but the Main Admin, and
        # the fact that a batch was promoted outlives whoever pressed the
        # button. Remove the person, keep the record.
        sa.ForeignKeyConstraint(["by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_student_semester_history_student_id", "student_semester_history", ["student_id"]
    )
    op.create_index(
        "ix_student_semester_history_by_user_id", "student_semester_history", ["by_user_id"]
    )

    # There is NOTHING TO BACKFILL. A promotion that happened before this table
    # existed left no trace anywhere: `students.current_semester` is a position
    # and `semester_results` records marks, not moves. Inventing a row per
    # student for semesters 1..current would put a date, a reason and an actor
    # against an act nobody recorded, on the screen the office reads to find out
    # who promoted a batch. The history starts empty and starts being true.

    # ---------------------------------------------------------------- #
    # 6. The alumnus's link back to their record (B4.4)
    # ---------------------------------------------------------------- #
    # NULLABLE, and graduation still does not create the row: `company` is NOT
    # NULL and row existence is what the alumni first-login form branches on.
    # See the column's comment in app/models/alumni.py.
    op.add_column("alumni_profiles", sa.Column("student_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_alumni_profile_student",
        "alumni_profiles",
        "students",
        ["student_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # UNIQUE, which also indexes the foreign key. One alumni profile per student
    # record; PostgreSQL allows any number of NULLs, which is every row today.
    op.create_unique_constraint(
        "uq_alumni_profile_student", "alumni_profiles", ["student_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_alumni_profile_student", "alumni_profiles", type_="unique")
    op.drop_constraint("fk_alumni_profile_student", "alumni_profiles", type_="foreignkey")
    op.drop_column("alumni_profiles", "student_id")

    op.drop_index(
        "ix_student_semester_history_by_user_id", table_name="student_semester_history"
    )
    op.drop_index(
        "ix_student_semester_history_student_id", table_name="student_semester_history"
    )
    op.drop_table("student_semester_history")

    op.drop_table("badge_course_map")
    op.drop_table("stage_rules")

    op.drop_index("ix_approvedcert_course", table_name="approved_certifications")
    op.drop_index("ix_approvedcert_college", table_name="approved_certifications")
    op.drop_constraint(
        "fk_approvedcert_course", "approved_certifications", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_approvedcert_college", "approved_certifications", type_="foreignkey"
    )
    op.drop_column("approved_certifications", "course_id")
    op.drop_column("approved_certifications", "college_id")

    op.drop_column("students", "status")
    op.drop_column("cohorts", "status")

    op.drop_column("academic_courses", "total_semesters")
    op.drop_column("academic_courses", "degree_level")
    # The two enum TYPES are not dropped: `degree_level` is still `cohorts`' and
    # `jobs`', and `stage` is still `courses`' and `students`'. Dropping either
    # here would take those columns with it.
