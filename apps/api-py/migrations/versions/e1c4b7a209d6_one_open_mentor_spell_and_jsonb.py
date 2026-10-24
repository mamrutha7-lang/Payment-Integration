"""One open mentor spell per student, and the last two json columns become jsonb

Revision ID: e1c4b7a209d6
Revises: b7e4d21af905
Create Date: 2026-09-15

------------------------------------------------------------------------------
1. "ONE OPEN ROW PER STUDENT" WAS A SENTENCE, NOT A CONSTRAINT
------------------------------------------------------------------------------

`app/models/mentor_assignment.py` says `to_at IS NULL` is "what makes 'one open
row per current pair' a statement a query can check rather than a convention a
writer has to remember". A query could check it; nothing did. The table carried
its primary key and two plain btrees, and Postgres accepted a second open row
for the same student without complaint -- verified against the real schema
before this was written:

    INSERT INTO mentor_assignments (id, student_id, mentor_id, from_at, to_at, kind)
    SELECT 'dup', student_id, mentor_id, now(), NULL, kind FROM mentor_assignments LIMIT 1;
    -- INSERT 0 1
    --  student_id                        | open_spells
    --  1b3a93265d0f4a7190684a7a1b62bb21  |           2

`app/mentor_history.py::record_mentor_change` is the one writer and it is
correct in a single transaction: it reads the open row, closes it, opens the
next. That is a read-then-write, so under READ COMMITTED two concurrent
assignments for one student both see no open row and both insert. The console
posts ONE REQUEST PER TICKED STUDENT on a batch save, which is exactly the
shape that produces two in flight at once.

What it costs is not access -- rule 2 filters on `students.mentor_id`, which is
a single column and cannot disagree with itself. It costs the history card the
answer it exists for: `open_assignment()` returns `.first()` of two rows, so
"who mentors this student, since when" starts answering with whichever row the
planner reaches first, and the spell that never happened is indistinguishable
from the one that did.

THE SHAPE IS THE HOUSE PATTERN, NOT A NEW IDEA. Four tables already enforce a
"one live row" rule this way -- `uq_conversation_one_active_per_owner`,
`uq_interview_consent_active`, `uq_interview_policy_college_default`,
`uq_interview_track_global_code`. A plain `UNIQUE (student_id, to_at)` would
NOT do it: in Postgres NULLs are distinct, so it permits exactly the duplicate
this forbids while looking like it forbids it.

`tests/test_codebase_guards.py::test_no_index_duplicates_the_prefix_of_another`
does not fire on this, and that is by its own design rather than by luck:
`_index_key` returns None for a partial index, because a partial unique index
covers nothing in general and comparing it by column name "is how a guard
starts recommending the deletion of an index the planner needs". So
`(student_id) WHERE to_at IS NULL` is not read as a prefix of
`ix_mentor_assignment_student_open (student_id, to_at)`, which stays: it still
serves "every spell for this student" and the FK-is-indexed guard.

WHY THE REPAIR RUNS FIRST. A unique index over data that already violates it
fails at CREATE -- on production, inside the migration task, after the image is
pushed, which is the failure mode AGENTS.md spends a paragraph on for enum
ordering. On a deployment that never hit the race the UPDATE below matches no
rows and costs one sequential scan of a small table. On one that did, it closes
the losers and keeps the winner, and the winner is chosen to AGREE WITH THE
POINTER: `students.mentor_id` is the authority on who mentors this student now,
so the open row naming that mentor is the true one. Only where the pointer
settles nothing does it fall back to the most recent `from_at` (NULLS LAST --
a NULL there means "since before this was recorded", which is the oldest thing
a row can say, not the newest).

The closed rows are stamped `duplicate_open`, a value that is deliberately NOT
in `END_KINDS`: that tuple is the vocabulary a human act can choose from, and
no operator ever chose this. Nothing is deleted -- the table is append-only
history, and a repair that erased the evidence of the race would leave the next
reader unable to tell this migration had ever had anything to do.

------------------------------------------------------------------------------
2. THE LAST TWO `json` COLUMNS
------------------------------------------------------------------------------

39 of this schema's 41 JSON columns are `jsonb`. `users.notification_prefs` and
`export_events.filters` are `json`, which stores the raw text: no equality
operator, no containment, no GIN index, duplicate keys preserved verbatim. That
is not a style point, it is a column you cannot ask an ordinary question of:

    SELECT DISTINCT notification_prefs FROM users;
    ERROR:  could not identify an equality operator for type json

Both are read whole into Python today, so nothing is broken right now. That is
precisely when this is cheap to change: `notification_prefs` is the B14
preferences object and `filters` is "the query that decided which rows left the
building" -- an audit column whose natural future question is "which exports
carried this filter", which is a containment query `json` cannot answer.

The `USING` clause is required (there is no implicit cast) and the server
default is restated rather than left to Postgres's own re-cast, so the stored
default is `'{}'::jsonb` and not a `json` literal wearing a new type. Note
`export_events.filters` carries a server default in the database that its model
never declared -- `alembic check` does not compare server defaults, so the two
have disagreed silently since the column was created; declaring it on the model
in this revision is what stops that being rediscovered a third time.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# The ANNOTATED form, which is the template every other revision in this
# directory uses. It is not decoration: `_revisions()` in
# `tests/test_codebase_guards.py` reads these lines with
# `^revision:\s*str\s*=` and treats a file it cannot parse as a hard failure
# rather than skipping it — "a guard that silently skips what it cannot parse
# is worse than no guard, because the green tick is read as coverage". Written
# as a bare `revision = "..."` this migration is invisible to both the
# one-head and the down-revision-exists guards.
revision: str = "e1c4b7a209d6"
down_revision: Union[str, None] = "b7e4d21af905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Stamped on a spell closed by the repair below. Not a member of `END_KINDS`:
#: that vocabulary is what a person can choose on the console, and nobody chose
#: this one.
_REPAIR_END_KIND = "duplicate_open"

_REPAIR_REASON = (
    "closed by migration e1c4b7a209d6: a second open spell existed for this "
    "student, which the writer's read-then-write could produce under "
    "concurrency. The open spell agreeing with students.mentor_id was kept."
)


def upgrade() -> None:
    # --- 1. Repair, then constrain ------------------------------------------
    #
    # A no-op on every deployment that never hit the race. `LEFT JOIN students`
    # rather than an inner join: the join exists only to ASK the pointer, and a
    # spell whose student is somehow unreadable must still be de-duplicated
    # rather than skipped.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT ma.id,
                       ROW_NUMBER() OVER (
                           PARTITION BY ma.student_id
                           ORDER BY (s.mentor_id IS NOT DISTINCT FROM ma.mentor_id) DESC,
                                    ma.from_at DESC NULLS LAST,
                                    ma.id
                       ) AS rn
                  FROM mentor_assignments ma
                  LEFT JOIN students s ON s.id = ma.student_id
                 WHERE ma.to_at IS NULL
            )
            UPDATE mentor_assignments m
               SET to_at = now(),
                   end_kind = :end_kind,
                   end_reason = :end_reason
              FROM ranked r
             WHERE m.id = r.id
               AND r.rn > 1
            """
        ).bindparams(end_kind=_REPAIR_END_KIND, end_reason=_REPAIR_REASON)
    )

    op.create_index(
        "uq_mentor_assignment_one_open_spell",
        "mentor_assignments",
        ["student_id"],
        unique=True,
        postgresql_where=sa.text("to_at IS NULL"),
    )

    # --- 2. json -> jsonb ----------------------------------------------------
    op.alter_column(
        "users",
        "notification_prefs",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="notification_prefs::jsonb",
        server_default=sa.text("'{}'::jsonb"),
    )
    op.alter_column(
        "export_events",
        "filters",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="filters::jsonb",
        server_default=sa.text("'{}'::jsonb"),
    )


def downgrade() -> None:
    op.alter_column(
        "export_events",
        "filters",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=postgresql.JSON(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="filters::json",
        server_default=sa.text("'{}'::json"),
    )
    op.alter_column(
        "users",
        "notification_prefs",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=postgresql.JSON(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="notification_prefs::json",
        server_default=sa.text("'{}'::json"),
    )

    op.drop_index("uq_mentor_assignment_one_open_spell", table_name="mentor_assignments")

    # The repaired rows are deliberately NOT reopened. Dropping the index makes
    # a second open spell legal again; it does not make one TRUE. Reopening them
    # would restore, on purpose, the exact state that made the history card
    # answer at random -- and there is no record of which of the two rows a
    # reader would have seen before. A downgrade returns the schema, never the
    # defect.
