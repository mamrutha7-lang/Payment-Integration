"""mentor_notes.deleted_at — retracting a note stops destroying it.

A mentor removing a meeting note is taking back words the student has very
likely already read: `/student/mentor-meetings` renders these rows. The DELETE
endpoint used to `db.delete(note)`, so "what was said, and later withdrawn"
became unanswerable the instant it ran and nothing else in the system held a
copy — the row IS the record. It is now a stamp; every read filters
`deleted_at IS NULL`. See the column comment on app/models/mentor_note.py.

No partial unique index accompanies this, unlike the Conversation and
InterviewConsent soft deletes: `mentor_notes` has no natural key and no
one-live-row-per-owner rule, so a retained row cannot block a re-create. The two
existing composite indexes are left alone — a student's notes number in the
handful, so the extra predicate is a cheap filter on an already-selective scan.

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record of what the alternatives
cost. Adding a NULLABLE column with NO default is catalogue-only on PostgreSQL —
no table rewrite, no backfill of existing rows — so the ACCESS EXCLUSIVE lock is
held for the length of a catalogue update. Existing notes read back as live,
which is correct: nothing was retracted before this migration existed.

Revision ID: b9d4e7a2c318
Revises: c2f7a9d41e63
Create Date: 2026-09-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9d4e7a2c318"
down_revision: Union[str, None] = "c2f7a9d41e63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.add_column(
        "mentor_notes",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    # Dropping this column HARD-DELETES nothing, but it does discard the record
    # of which notes were retracted — after a downgrade every soft-deleted note
    # reappears on the student's screen. Reversible in schema, not in meaning.
    op.drop_column("mentor_notes", "deleted_at")
