"""mentor assignment history, the department's capacity, and SWOC history

Revision ID: c7e91a4b26d3
Revises: a4f7d2c80b93
Create Date: 2026-09-13

B9.1, B9.2's capacity half, and B7.3 / B7.4 / B7.5 / B7.6 of
docs/redesign-2026-09/04-backend-changes.md, in ONE revision because they are
one schema round behind two screens that are read together — the Mentors &
Students board and the SWOC board, both of which a Main Admin has open at the
same time. Four revisions would leave four windows in which one of them is half
migrated.

It chains onto a4f7d2c80b93 (Phase 4c).

==============================================================================
1. `mentor_assignments` — AND THE ONE OPEN ROW PER CURRENT PAIR
==============================================================================

`students.mentor_id` STAYS THE CURRENT POINTER. Nothing here replaces it,
nothing reads a mentor's group from this table, and rule 2 still filters on the
column it always has. This is history beside the pointer.

THE SEED IS ONE OPEN ROW PER CURRENT PAIR, AND A STUDENT WITH NO MENTOR GETS
NOTHING. `INSERT ... SELECT ... WHERE mentor_id IS NOT NULL` — on a freshly
seeded development database that is exactly ONE row out of 34 students, because
`app/seed.py` seats the single demo student and `app/seed_roster.py` seats none
of its 33. That asymmetry is the point: "never had a mentor" and "has had one
since forever" are different facts, and an empty history must not be able to say
the second. The mentor-load history card and the student-detail mentor card both
render an empty history as "no assignment recorded", never as a gap.

`from_at` IS THE ACCOUNT'S CREATION TIME, NOT `now()`. The date a pair was
formed is genuinely unknowable — five writers have set that pointer and not one
of them wrote a timestamp. `now()` would tell every reader that the entire
roster was seated on the day this migration ran, which is a confident false
statement.

`students` HAS NO `created_at` OF ITS OWN — checked, not assumed; the timestamp
lives on `users`, and a `students` row is written in the same transaction as the
account it hangs off. So the seed joins `users` and takes that, which is the
earliest moment the pair COULD have existed: a true statement about a bound
rather than a guess at a date. Where even that is NULL, `from_at` stays NULL,
which `app/models/mentor_assignment.py` defines as "mentoring since before this
was recorded" and which the history card prints in those words.

NO HANDOVER GRANT IS MINTED HERE. The seeded rows are all OPEN — nobody has been
released — so there is nothing to hand over, and a migration that wrote
`capability_grants` rows would be handing out access nobody decided on.

`kind` IS A PLAIN `sa.String`, NOT A PG ENUM. `app/models/institution.py`'s
house rule for its stated reason: `graduated` / `transferred` / `cohort_moved`
are all plausible fifth values and must stay a data change. So none of
AGENTS.md's three enum gotchas arises anywhere in this revision.

==============================================================================
2. `departments.mentor_capacity` — A NUMBER, NOT A RULE
==============================================================================

Nullable, falling back to `settings.mentor_capacity` (default 20). 04 asks for
"capacity from a governance setting per department"; there is no
`governance_settings` table and this revision deliberately does not invent one —
a capacity is a fact about a department and departments have a row.

IT IS ADVISORY AND NOTHING ENFORCES IT. Two places in the tree argue that in
writing (`routers/console.py`'s MentorLoadOut and the Angular screen's own
header: "an admin who chooses to overload one faculty member in a thin year
should not have to edit .env first. The rail says 'At capacity' in the risk
colour and lets them"), and both are still right. What was wrong was only that
the number lived in an environment variable, so tuning it for one department
meant a deploy. This column is that fix and nothing else.

Every existing row gets NULL, which reads as "use the programme number" — the
behaviour every department has today, unchanged.

==============================================================================
3. `swoc_entries` — FOUR COLUMNS, AND ONE OF THEM HAS NOTHING TO BACKFILL FROM
==============================================================================

`updated_at` gets `server_default=now()`, NOT NULL. A NULL would make "never
edited" and "edited, we do not know when" the same value on a screen that draws
"edited {{ date }}"; equal-to-`recorded_at` means "as written" and is a fact
about every existing row that is true.

`semester` (B7.4) IS LEFT NULL ON EVERY EXISTING ROW, and this is the paragraph
04 needs corrected. It says "backfill from `recorded_at` vs history". THERE IS
NO SEMESTER HISTORY TO BACKFILL FROM, and that was checked model by model before
this was written: `students.current_semester` is a bare Integer position with no
change record; `semester_results` carries a semester and a NULLABLE
`published_on` and exists only for semesters whose marks were entered;
`academic_history` is prior qualifications, not REEP semesters. Phase 4a's
`student_semester_history` records promotions from the day that endpoint
shipped and knows nothing about the semesters before it, so it cannot date a
SWOC line written last year either.

The three candidates and why two of them lie:
  (a) stamp every row with the student's CURRENT semester — fast, and wrong for
      every row written in an earlier term: it would claim last year's weakness
      was recorded this semester, on the student's own screen;
  (b) leave them NULL and render "semester not recorded" — honest, and the same
      rule this codebase already applies to a pending English Baseline section
      and to a missing interview score, where a confident zero and an absent
      value mean opposite things;
  (c) derive from `recorded_at` against `semester_results.published_on` — half
      populated at best, and silent where marks were never entered.
(b). Going forward the write path stamps the student's semester at the moment
the line is written, which is the only moment anybody actually knows it.

`acknowledged_at` (B7.5) and the three `linked_*` FKs (B7.6) are nullable and
land empty, which is what they mean: nobody has acknowledged a line written
before there was a button, and nobody linked one.

All three links are `ON DELETE SET NULL`. Deleting a job posting, a practice
interview or a skill row must not silently delete a mentor's written observation
about a student.

==============================================================================
4. `swoc_entry_revisions` — STARTS EMPTY, DELIBERATELY
==============================================================================

The before/after data for B7.3 already exists: every PATCH on this board has
written a full snapshot to `redesign_audit_events` since the endpoint shipped.
What does not exist is a way for the people who WRITE the board to read it —
`routers/audit.py`'s gate is Main-Admin-only with a written argument for why it
is not scoped, and the SWOC board is held by granted faculty. This table is the
scoped copy, so it begins empty and a line written before Phase 4 shows "no
earlier version recorded" rather than a fabricated one. The Main Admin still
reads the older history where it has always been.

The snapshots are JSONB rather than columns, which is also what keeps AGENTS.md's
enum gotcha (b) from firing: a `kind`/`source` column here would reuse
`swoc_kind`/`swoc_source`, autogenerate would emit a bare `sa.Enum`, and the
upgrade would fail with "type already exists".

==============================================================================
WHAT THE DOWNGRADE DOES NOT BRING BACK
==============================================================================

`downgrade()` drops both new tables and all five new columns. Every assignment
ever recorded, every per-department capacity an admin typed, every SWOC
acknowledgement, link, semester stamp and revision goes with them. The seeded
open rows come back on the next upgrade from `students.mentor_id`; nothing else
does. Handover grants in `capability_grants` are NOT removed — they are
governance rows written by the application, and a schema downgrade silently
revoking somebody's access is a worse outcome than an orphaned expiry.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7e91a4b26d3"
down_revision: Union[str, None] = "a4f7d2c80b93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----------------------------------------------------------- B9.1 -- #
    op.create_table(
        "mentor_assignments",
        sa.Column("id", sa.String(), nullable=False),
        # NOT NULL, no ondelete: the database refuses to delete a student out
        # from under a spell. `student_semester_history` has it exactly so.
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("mentor_id", sa.String(), nullable=False),
        # Nullable: see the docstring. NULL is "since before this was recorded".
        sa.Column("from_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("to_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kind", sa.String(length=32), server_default="assign", nullable=False),
        sa.Column("by_user_id", sa.String(), nullable=True),
        sa.Column("reason", sa.String(length=400), nullable=True),
        sa.Column("end_kind", sa.String(length=32), nullable=True),
        sa.Column("ended_by_user_id", sa.String(), nullable=True),
        sa.Column("end_reason", sa.String(length=400), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["mentor_id"], ["mentors.id"]),
        # SET NULL on both: `purge_people` removes every account but the Main
        # Admin, and the fact that a student was seated outlives the account of
        # whoever seated them. Without this the purge's KEPT-table rule would
        # need these in CREATED_BY_COLUMNS instead.
        sa.ForeignKeyConstraint(["by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ended_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # (student_id, to_at) and (mentor_id, to_at): each serves its FK's index
    # requirement on its LEADING column and the "open row" read at the same
    # time, which is every writer's first question.
    op.create_index(
        "ix_mentor_assignment_student_open", "mentor_assignments", ["student_id", "to_at"]
    )
    op.create_index(
        "ix_mentor_assignment_mentor_open", "mentor_assignments", ["mentor_id", "to_at"]
    )
    op.create_index(
        op.f("ix_mentor_assignments_by_user_id"), "mentor_assignments", ["by_user_id"]
    )
    op.create_index(
        op.f("ix_mentor_assignments_ended_by_user_id"),
        "mentor_assignments",
        ["ended_by_user_id"],
    )

    # ONE OPEN ROW PER CURRENT PAIR. `from_at` from the student row's creation
    # time, NULL where that is itself NULL — never now(). See the docstring.
    op.execute(
        sa.text(
            """
            INSERT INTO mentor_assignments
                (id, student_id, mentor_id, from_at, to_at, kind,
                 by_user_id, reason, end_kind, ended_by_user_id, end_reason)
            SELECT
                md5(random()::text || clock_timestamp()::text),
                s.id,
                s.mentor_id,
                u.created_at,
                NULL,
                'assign',
                NULL,
                NULL,
                NULL,
                NULL,
                NULL
            FROM students s
            JOIN users u ON u.id = s.user_id
            WHERE s.mentor_id IS NOT NULL
            """
        )
    )

    # ----------------------------------------------------------- B9.2 -- #
    op.add_column(
        "departments", sa.Column("mentor_capacity", sa.Integer(), nullable=True)
    )

    # ------------------------------------------------- B7.3/4/5/6 ------ #
    op.add_column(
        "swoc_entries",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column("swoc_entries", sa.Column("semester", sa.Integer(), nullable=True))
    op.add_column(
        "swoc_entries", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("swoc_entries", sa.Column("linked_skill_id", sa.String(), nullable=True))
    op.add_column("swoc_entries", sa.Column("linked_session_id", sa.String(), nullable=True))
    op.add_column("swoc_entries", sa.Column("linked_job_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_swoc_linked_skill",
        "swoc_entries",
        "student_skills",
        ["linked_skill_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_swoc_linked_session",
        "swoc_entries",
        "interview_sessions",
        ["linked_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_swoc_linked_job",
        "swoc_entries",
        "jobs",
        ["linked_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_swoc_entries_linked_skill_id"), "swoc_entries", ["linked_skill_id"])
    op.create_index(
        op.f("ix_swoc_entries_linked_session_id"), "swoc_entries", ["linked_session_id"]
    )
    op.create_index(op.f("ix_swoc_entries_linked_job_id"), "swoc_entries", ["linked_job_id"])

    # ----------------------------------------------------------- B7.3 -- #
    op.create_table(
        "swoc_entry_revisions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("entry_id", sa.String(), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("by_user_id", sa.String(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # CASCADE, unlike the links above: a revision of a deleted line is a
        # revision of nothing, and the DELETE path writes the final before-state
        # to `redesign_audit_events`, which is where a deleted line survives.
        sa.ForeignKeyConstraint(["entry_id"], ["swoc_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_swoc_revision_entry", "swoc_entry_revisions", ["entry_id", "changed_at"])
    op.create_index(
        op.f("ix_swoc_entry_revisions_by_user_id"), "swoc_entry_revisions", ["by_user_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_swoc_entry_revisions_by_user_id"), table_name="swoc_entry_revisions")
    op.drop_index("ix_swoc_revision_entry", table_name="swoc_entry_revisions")
    op.drop_table("swoc_entry_revisions")

    op.drop_index(op.f("ix_swoc_entries_linked_job_id"), table_name="swoc_entries")
    op.drop_index(op.f("ix_swoc_entries_linked_session_id"), table_name="swoc_entries")
    op.drop_index(op.f("ix_swoc_entries_linked_skill_id"), table_name="swoc_entries")
    op.drop_constraint("fk_swoc_linked_job", "swoc_entries", type_="foreignkey")
    op.drop_constraint("fk_swoc_linked_session", "swoc_entries", type_="foreignkey")
    op.drop_constraint("fk_swoc_linked_skill", "swoc_entries", type_="foreignkey")
    op.drop_column("swoc_entries", "linked_job_id")
    op.drop_column("swoc_entries", "linked_session_id")
    op.drop_column("swoc_entries", "linked_skill_id")
    op.drop_column("swoc_entries", "acknowledged_at")
    op.drop_column("swoc_entries", "semester")
    op.drop_column("swoc_entries", "updated_at")

    op.drop_column("departments", "mentor_capacity")

    op.drop_index(
        op.f("ix_mentor_assignments_ended_by_user_id"), table_name="mentor_assignments"
    )
    op.drop_index(op.f("ix_mentor_assignments_by_user_id"), table_name="mentor_assignments")
    op.drop_index("ix_mentor_assignment_mentor_open", table_name="mentor_assignments")
    op.drop_index("ix_mentor_assignment_student_open", table_name="mentor_assignments")
    op.drop_table("mentor_assignments")
