"""A batch is a year: take the course and the specialization back out of cohorts.name

Revision ID: a4e7c92d1f38
Revises: d8b1f4c2a7e9
Create Date: 2026-09-16

A BATCH IS A YEAR. "2026-28" is the whole of what a `cohorts` row is -- the span
a student who joined a two-year degree belongs to. WHICH course and WHICH
specialization they joined is not part of its name: it is the spine the row
hangs off, and the row already carries every rung of it as a real foreign key
(`department_id`, `course_id`, `specialization_id`).

Both writers of the spine used to manufacture the spine INTO the name --
`app/seed_catalogue.py` and the Set-up-a-college screen agreed on "General MBA -
Finance 2026-28" -- so one fact was stored twice: once as a foreign key
`ancestry_of_student` reads, and once as words inside a string nothing can join
on. They could disagree from the moment the office renamed a course on screen,
with the batch still printing last year's name. And because `batch_label` is its
own column, five endpoints printed the year a second time beside it ("General
MBA - Finance 2026-28 · 2026-28"). The spine is composed from the links at read
time now, in `app/batch_labels.py`; the name is the year.

WHAT THIS REWRITES, AND WHAT IT WILL NOT TOUCH. Only a row whose `name` is
EXACTLY the string those two writers would have manufactured from THAT ROW'S OWN
links -- course name, optionally " - " and the specialization name, then a space
and the batch label. Anything else is a name a person typed, and the seeder's
own first rule applies to it ("an existing row is returned untouched even where
its fields differ ... divergence is REPORTED, never corrected"): a section the
office called "2026-28 Section B", or a course they have since renamed so the
manufactured string no longer matches, is left exactly as it is and prints
verbatim. That is the difference between repairing what this codebase wrote and
overwriting what the office wrote, and it is why the predicate compares the
whole string rather than stripping a prefix.

A row with no `course_id` is skipped because it never had a spine to
manufacture -- `HIERARCHY_LEVELS` makes Course optional, so a batch may
legitimately hang at department level, and its name was already the year.

THE DOWNGRADE IS AN EXACT INVERSE AND SO IT IS WRITTEN, rather than raising the
way `7c4e0b21d9aa` does. Nothing is lost here: the manufactured string is a pure
function of the links and the label, which the row still carries, so going back
re-derives precisely what stood in the column before -- including for batches
written after this migration, which is what the old code would have named them
anyway.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4e7c92d1f38"
down_revision: Union[str, None] = "d8b1f4c2a7e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The string the two writers used to produce, rebuilt in SQL from the row's own
#: links. `LEFT JOIN` on the specialization because it is optional; the
#: `COALESCE` is what makes "no specialization" render as nothing at all rather
#: than as the separator with a hole after it.
_MANUFACTURED = """
    SELECT co.name || COALESCE(' - ' || sp.name, '') || ' ' || c.batch_label
      FROM academic_courses co
      LEFT JOIN academic_specializations sp ON sp.id = c.specialization_id
     WHERE co.id = c.course_id
"""


def upgrade() -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE cohorts AS c
               SET name = c.batch_label
             WHERE c.course_id IS NOT NULL
               AND c.name <> c.batch_label
               AND c.name = ({_MANUFACTURED})
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE cohorts AS c
               SET name = ({_MANUFACTURED})
             WHERE c.course_id IS NOT NULL
               AND c.name = c.batch_label
            """
        )
    )
