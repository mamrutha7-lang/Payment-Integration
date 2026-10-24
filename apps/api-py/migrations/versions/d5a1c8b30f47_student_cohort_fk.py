"""students.cohort_id becomes a real foreign key to cohorts.id.

The column has been an unconstrained String since 9ac9f4696b0d, carrying the
comment "FK to Cohort later". Nothing has ever prevented a value that names no
cohort, and two seeds write it. "Later" arrived when the student's locked
profile card needed College, Batch, Entry date and Expected completion — four
facts that are reached THROUGH this hop and none of which are stored on the
student.

WHY THE UPDATE COMES FIRST. Adding a foreign key validates every existing row.
One orphaned cohort_id and the ALTER TABLE aborts — on a live database, halfway
through a deploy. So orphans are nulled first.

WHY THE ORPHANS ARE COPIED, NOT JUST NULLED. `students_orphaned_cohort_ids`
keeps (student id, the dead cohort_id) before the UPDATE touches anything. The
first draft of this migration nulled them and called them "unrecoverable" in
the downgrade — but that was a CHOICE dressed up as a fact. This column was a
free-form string for its whole life across a Prisma migration; assuming every
value in it is a UUID that happens to point nowhere is the over-confident
reading, and one CREATE TABLE AS makes the downgrade honest and gives an
operator who reads "cleared 400" somewhere to look. The table is small, it is
named after this revision, and dropping it is a decision for whoever is sure.

WHY `NOT EXISTS` AND NOT `NOT IN`. `NOT IN (SELECT id FROM cohorts)` evaluates
to NULL — never true — the moment one NULL appears in the subquery. The sweep
would silently clear nothing and the ADD CONSTRAINT would then abort on exactly
the orphans the sweep exists to remove. `cohorts.id` is a NOT NULL primary key
today, so `NOT IN` happens to be correct; `NOT EXISTS` stays correct without
depending on that.

WHY THE TIMEOUTS ARE `SET LOCAL`. `SET` is SESSION-scoped, and env.py runs the
whole `upgrade head` inside ONE transaction on ONE connection — so a plain
`SET` here leaks into every migration that runs after this one, forever. The
next person's backfill over `messages` would inherit a 60-second
statement_timeout from a file two revisions upstream that their own migration
never mentions, pass on their laptop, and abort in production. `SET LOCAL`
scopes both to this transaction.

WHAT THE LOCK ACTUALLY COSTS. ADD CONSTRAINT takes SHARE ROW EXCLUSIVE on
`students` AND on `cohorts` — writes to BOTH tables block for the duration of
the validating scan. `lock_timeout` bounds how long this migration waits to
acquire that; it does NOT bound how long the application waits on it once
acquired. On a large `students` this wants splitting into `NOT VALID` here plus
a `VALIDATE CONSTRAINT` migration (which takes only SHARE UPDATE EXCLUSIVE)
after. At this table's present size one statement is honest; at scale it is
not, and this paragraph is the note to whoever finds out.

EXPAND PHASE. Old code reads cohort_id as a plain string and keeps working, so
this is safe to deploy ahead of the code that uses the join.

Revision ID: d5a1c8b30f47
Revises: c4a9e7b21d38
Create Date: 2026-09-06

"""

from typing import Sequence, Union

from alembic import op

revision: str = "d5a1c8b30f47"
down_revision: Union[str, None] = "c4a9e7b21d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK_NAME = "fk_students_cohort_id_cohorts"


_ORPHAN_PREDICATE = (
    "students.cohort_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM cohorts WHERE cohorts.id = students.cohort_id)"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")

    # Keep a copy before destroying anything, then null the orphans — the
    # constraint below refuses to validate while any survive.
    op.execute(
        "CREATE TABLE IF NOT EXISTS students_orphaned_cohort_ids AS "
        f"SELECT id AS student_id, cohort_id FROM students WHERE {_ORPHAN_PREDICATE}"
    )
    op.execute(f"UPDATE students SET cohort_id = NULL WHERE {_ORPHAN_PREDICATE}")

    # No rowcount and no print(). op.get_bind() is None in offline mode, so
    # reading .rowcount off it made `alembic upgrade --sql` raise
    # AttributeError — and --sql is the review workflow a locking migration
    # like this one most wants (generate the script, have a DBA read it, run it
    # in a change window). env.py implements run_migrations_offline() in full;
    # every migration before this one was --sql-clean and this one must be too.
    # The count now lives in a table an operator can query, which beats a
    # print() to stdout that alembic's stderr log never interleaves with and
    # that claimed "cleared 400" before the transaction had committed.

    op.create_foreign_key(
        _FK_NAME,
        source_table="students",
        referent_table="cohorts",
        local_cols=["cohort_id"],
        remote_cols=["id"],
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint(_FK_NAME, "students", type_="foreignkey")
    # The orphans ARE restored, from the copy upgrade() took. Only rows still
    # NULL are written, so a cohort_id set legitimately after the upgrade is
    # never overwritten by a stale value from before it.
    op.execute(
        "UPDATE students SET cohort_id = o.cohort_id "
        "FROM students_orphaned_cohort_ids o "
        "WHERE students.id = o.student_id AND students.cohort_id IS NULL"
    )
    # The copy is deliberately NOT dropped. It is the only record that these
    # values ever existed, and deciding it is safe to lose is a judgement for
    # an operator looking at the rows, not for a downgrade running unattended.
