"""imports, the weekly snapshot, criteria per course, jobs scope — and the job-import delete

Revision ID: f1a7c93d5e26
Revises: c3a9f1e7d2b4
Create Date: 2026-09-13

B8.1, B8.2, B8.4, B8.6, B12.1 and B12.2 of
docs/redesign-2026-09/04-backend-changes.md, in ONE revision because they are
one schema round on one screen set: the Analytics and Data-imports boards read
these tables together, and four separate revisions would leave four windows in
which the console is half migrated.

It chains onto c3a9f1e7d2b4 (Phase 4a), which is what gave
`academic_courses.total_semesters` the "semester <= course" validation B8.1
needs.

------------------------------------------------------------------------------
EVERY NEW STATUS COLUMN HERE IS A PLAIN STRING
------------------------------------------------------------------------------

`import_runs.kind`, `import_runs.status`, `import_rows.verdict`,
`analytics_snapshots.scope_type` and `jobs.status` are all `sa.String`, not
`sa.Enum`. That is AGENTS.md's house rule (`Message.channel`, and every
interview vocabulary), and it is the same decision c3a9f1e7d2b4 made for
`cohorts.status` and `students.status`: a sixth import kind, or a third job
state, must be a DATA change and not a `CREATE TYPE` migration carrying all
three of the enum gotchas. The vocabularies live beside the models and are
validated at the API edge, where a bad value is a 422 naming the allowed set.

Consequently this revision creates NO new Postgres type and reuses none, so
gotchas (a), (b) and (c) do not arise anywhere in it.

------------------------------------------------------------------------------
B8.4 IS NOT A CLEAN DELETE, AND THE ORDER IS FORCED
------------------------------------------------------------------------------

04 says "remove /admin/overview, /admin/mail, /admin/job-imports and
`job_import_runs`" and does not mention that **`jobs.import_run_id` is a live
foreign key into that table** (`fk_jobs_import_run`, created by 496d83735a1d)
with its own index (`ix_jobs_import_run_id`, c2f7a9d41e63), nor that
`app/seed.py` wrote a run and back-linked every seeded posting to it.

So the table cannot be dropped first — it fails on the constraint. The order is:

    1. drop fk_jobs_import_run          the constraint
    2. drop ix_jobs_import_run_id       the index on the referencing column
    3. drop jobs.import_run_id          the column
    4. drop ix_jobimport_started        the table's own index
    5. drop job_import_runs             the table

`downgrade()` rebuilds all five, and **the data in the dropped column cannot be
recovered**: which posting came from which sheet is gone the moment step 3 runs.
That is acceptable only because the answer was never read — the three endpoints
had no client of any kind — and it is written here so the next person restoring
a backup knows what the downgrade does not bring back.

------------------------------------------------------------------------------
WHAT IS DELIBERATELY NOT BACKFILLED
------------------------------------------------------------------------------

`placement_criteria.college_id` / `course_id` stay NULL on every existing row,
and `jobs.college_id` / `course_id` / `tracks` stay NULL/empty on every existing
posting. In BOTH tables a NULL means "applies everywhere", which is exactly the
behaviour those rows have today — the single seeded criteria row is the
programme-wide fallback the resolver looks for second, and a posting with no
college is visible to every student, as it is now.

c3a9f1e7d2b4 attached the certification catalogue when exactly one college and
one course made the target unambiguous, and that was right THERE because an
unattached `approved_certifications` row is a row nobody's screen can place.
Here the opposite holds: attaching these rows would NARROW something that is
currently universal, so a deployment that later onboards a second college would
find its jobs board empty and its placement gates missing, with nothing on any
screen to explain it. Widening is recoverable by an admin; silently narrowing is
the bug nobody reports as a bug.

`effective_from` stays NULL for the same shape of reason: stamping it with
today's date would assert that no criteria applied before this migration ran.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a7c93d5e26"
down_revision: Union[str, None] = "c3a9f1e7d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. The imports (B8.1)
    # ------------------------------------------------------------------ #
    op.create_table(
        "import_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="previewed"),
        sa.Column("college_id", sa.String(), nullable=True),
        sa.Column("cohort_id", sa.String(), nullable=True),
        sa.Column("semester", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_ok", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_warning", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_applied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("by_user_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("semester IS NULL OR semester >= 1", name="ck_import_run_semester"),
        # No ondelete on the college: the spine's convention is that the
        # database refuses to delete a rung that still has rows under it.
        sa.ForeignKeyConstraint(["college_id"], ["colleges.id"]),
        # SET NULL, departing from that convention on purpose:
        # DELETE /api/admin/cohorts/{id} refuses only when students are seated,
        # so an empty batch that was once imported into would otherwise fail
        # that delete on a foreign key — a 500 over a receipt.
        sa.ForeignKeyConstraint(["cohort_id"], ["cohorts.id"], ondelete="SET NULL"),
        # SET NULL so an account delete is never what fails here.
        sa.ForeignKeyConstraint(["by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # One index per foreign key, each LEADING with its own column, or the
    # lookup is no faster than a scan (test_every_foreign_key_column_is_indexed).
    op.create_index("ix_import_runs_college_id", "import_runs", ["college_id"])
    op.create_index("ix_import_runs_cohort_id", "import_runs", ["cohort_id"])
    op.create_index("ix_import_runs_by_user_id", "import_runs", ["by_user_id"])
    # The table's one read pattern: the fifty most recent, newest first.
    op.create_index("ix_import_run_created", "import_runs", ["created_at"])

    op.create_table(
        "import_rows",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("usn", sa.String(), nullable=True),
        sa.Column("student_id", sa.String(), nullable=True),
        sa.Column("verdict", sa.String(), nullable=False, server_default="ok"),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["import_runs.id"], ondelete="CASCADE"),
        # SET NULL: the line keeps the USN as typed even after the record it
        # named is gone. A receipt that erases itself is not a receipt.
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # Leads with run_id, which is what indexes THAT foreign key. A second
        # index on run_id alone would be a strict prefix of this constraint and
        # would fail test_no_index_duplicates_the_prefix_of_another.
        sa.UniqueConstraint("run_id", "line_no", name="uq_import_row_line"),
    )
    op.create_index("ix_import_rows_student_id", "import_rows", ["student_id"])

    # ------------------------------------------------------------------ #
    # 2. The weekly snapshot (B8.6)
    # ------------------------------------------------------------------ #
    # `scope_id` IS NOT A FOREIGN KEY — it is polymorphic across colleges,
    # departments, courses, specializations and cohorts, so there is no one
    # table to point at. It is NOT NULL and the programme-wide roll-up stores
    # the sentinel '*': a NULL would be DISTINCT from every other NULL in the
    # unique index below, so the nightly job would append a second row for the
    # same week every night and the series would silently multiply.
    op.create_table(
        "analytics_snapshots",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("week", sa.Date(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_type", "scope_id", "week", name="uq_analytics_snapshot"),
    )

    # ------------------------------------------------------------------ #
    # 3. Placement criteria per course (B8.2)
    # ------------------------------------------------------------------ #
    op.add_column("placement_criteria", sa.Column("college_id", sa.String(), nullable=True))
    op.add_column("placement_criteria", sa.Column("course_id", sa.String(), nullable=True))
    op.add_column("placement_criteria", sa.Column("effective_from", sa.Date(), nullable=True))
    op.add_column("placement_criteria", sa.Column("created_by", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_placement_criteria_college", "placement_criteria", "colleges", ["college_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_placement_criteria_course",
        "placement_criteria",
        "academic_courses",
        ["course_id"],
        ["id"],
    )
    # SET NULL AND NOT A BARE FOREIGN KEY, and this one is load-bearing:
    # `placement_criteria` is KEEP in BOTH destructors, and a users FK with no
    # ON DELETE on a KEPT table aborts `python -m app.purge_people` on a
    # constraint violation partway through deleting the accounts. The
    # alternative is an entry in `purge_people.CREATED_BY_COLUMNS`, which is
    # what the four spine tables use; SET NULL says the same thing in the schema
    # instead of in a tuple somebody has to remember to edit.
    op.create_foreign_key(
        "fk_placement_criteria_created_by",
        "placement_criteria",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_placement_criteria_college_id", "placement_criteria", ["college_id"])
    op.create_index("ix_placement_criteria_course_id", "placement_criteria", ["course_id"])
    op.create_index("ix_placement_criteria_created_by", "placement_criteria", ["created_by"])
    # NO BACKFILL. See the module docstring: NULL on both pointers is the
    # programme-wide row, which is what every existing row already is.

    # ------------------------------------------------------------------ #
    # 4. Where a posting is offered, and whether it still is (B12.1, B12.2)
    # ------------------------------------------------------------------ #
    op.add_column("jobs", sa.Column("college_id", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("course_id", sa.String(), nullable=True))
    # NOT NULL with an empty-array default, matching `required_skills`, which is
    # the in-house precedent for an ARRAY(String) column on this table. An empty
    # list means EVERY track — not "no student qualifies".
    op.add_column(
        "jobs",
        sa.Column(
            "tracks",
            sa.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
    )
    op.add_column(
        "jobs", sa.Column("status", sa.String(), nullable=False, server_default="open")
    )
    op.create_foreign_key("fk_jobs_college", "jobs", "colleges", ["college_id"], ["id"])
    op.create_foreign_key(
        "fk_jobs_course", "jobs", "academic_courses", ["course_id"], ["id"]
    )
    op.create_index("ix_jobs_college_id", "jobs", ["college_id"])
    op.create_index("ix_jobs_course_id", "jobs", ["course_id"])

    # ------------------------------------------------------------------ #
    # 5. B8.4: the job-import machinery, in the only order that works
    # ------------------------------------------------------------------ #
    op.drop_constraint("fk_jobs_import_run", "jobs", type_="foreignkey")
    op.drop_index("ix_jobs_import_run_id", table_name="jobs")
    op.drop_column("jobs", "import_run_id")
    op.drop_index("ix_jobimport_started", table_name="job_import_runs")
    op.drop_table("job_import_runs")


def downgrade() -> None:
    # 5. Rebuilt empty. WHICH POSTING CAME FROM WHICH SHEET IS NOT RECOVERABLE:
    # the column is restored NULL on every row.
    op.create_table(
        "job_import_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=True),
        sa.Column("uploaded_by_id", sa.String(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobimport_started", "job_import_runs", ["started_at"])
    op.add_column("jobs", sa.Column("import_run_id", sa.String(), nullable=True))
    op.create_index("ix_jobs_import_run_id", "jobs", ["import_run_id"])
    op.create_foreign_key(
        "fk_jobs_import_run",
        "jobs",
        "job_import_runs",
        ["import_run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 4.
    op.drop_index("ix_jobs_course_id", table_name="jobs")
    op.drop_index("ix_jobs_college_id", table_name="jobs")
    op.drop_constraint("fk_jobs_course", "jobs", type_="foreignkey")
    op.drop_constraint("fk_jobs_college", "jobs", type_="foreignkey")
    op.drop_column("jobs", "status")
    op.drop_column("jobs", "tracks")
    op.drop_column("jobs", "course_id")
    op.drop_column("jobs", "college_id")

    # 3.
    op.drop_index("ix_placement_criteria_created_by", table_name="placement_criteria")
    op.drop_index("ix_placement_criteria_course_id", table_name="placement_criteria")
    op.drop_index("ix_placement_criteria_college_id", table_name="placement_criteria")
    op.drop_constraint(
        "fk_placement_criteria_created_by", "placement_criteria", type_="foreignkey"
    )
    op.drop_constraint("fk_placement_criteria_course", "placement_criteria", type_="foreignkey")
    op.drop_constraint("fk_placement_criteria_college", "placement_criteria", type_="foreignkey")
    op.drop_column("placement_criteria", "created_by")
    op.drop_column("placement_criteria", "effective_from")
    op.drop_column("placement_criteria", "course_id")
    op.drop_column("placement_criteria", "college_id")

    # 2.
    op.drop_table("analytics_snapshots")

    # 1.
    op.drop_index("ix_import_rows_student_id", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_import_run_created", table_name="import_runs")
    op.drop_index("ix_import_runs_by_user_id", table_name="import_runs")
    op.drop_index("ix_import_runs_cohort_id", table_name="import_runs")
    op.drop_index("ix_import_runs_college_id", table_name="import_runs")
    op.drop_table("import_runs")
