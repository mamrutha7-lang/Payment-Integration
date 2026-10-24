"""registration_status HOLD + the hold stamp

B11.2. A reviewer who has read an application and cannot decide it yet — a CV
that never arrived, a USN to confirm, a call to make — had exactly two verbs,
Approve and Reject, and so parked the row by doing nothing to it. The queue then
carried two kinds of PENDING_REVIEW that look identical and mean opposite things:
"nobody has looked at this" and "I looked, and it is waiting on something".

HOLD IS INTERNAL. No mail leaves on a hold, `decision_reason` is untouched, and
the applicant's own result card cannot tell it from PENDING_REVIEW. The note is
written ABOUT the applicant for colleagues, not TO them, which is why it lives
beside `review_note` and not in `decision_reason`.

Revision ID: c4f7b1e08d92
Revises: d8a3f16c05be
Create Date: 2026-09-13 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c4f7b1e08d92'
down_revision: Union[str, None] = 'd8a3f16c05be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `registration_status` is a NATIVE Postgres enum (created inline by
    # 9ecfa486074d's create_table), so a new member is an ALTER TYPE and not a
    # model edit. Shape copied from b8d2f7a4c619, the one precedent in this
    # repository, including both of its reasons:
    #
    # ALTER TYPE ... ADD VALUE is allowed inside a transaction on PG12+ ONLY as
    # long as the new value is not USED in that same transaction. NOTHING BELOW
    # WRITES A ROW HOLDING 'HOLD' — there is nothing to backfill on day one, and
    # combining this with a data step would make the whole migration fail under
    # Alembic's transaction.
    op.execute("ALTER TYPE registration_status ADD VALUE IF NOT EXISTS 'HOLD'")

    # The stamp. Ordinary transactional DDL, and deliberately three columns
    # rather than a reuse of `reviewed_by_id` / `reviewed_at` / `review_note`:
    # those mean DECIDED, and `reopen` clears them because a decision was
    # undone. A hold is not a decision.
    #
    # `held_by_id` is a plain String, NOT a foreign key to `users` — the house
    # style `reviewed_by_id` already set on this table.
    op.add_column("registrations", sa.Column("hold_note", sa.String(), nullable=True))
    op.add_column("registrations", sa.Column("held_by_id", sa.String(), nullable=True))
    op.add_column(
        "registrations",
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # The three columns come back out; the enum VALUE does not, and cannot.
    # Postgres has no DROP VALUE: removing it would mean recreating the type and
    # rewriting every column that uses it, and any row already holding 'HOLD'
    # would have to be mapped to something else — a data decision, not a schema
    # one. Left in place deliberately, as b8d2f7a4c619 left NEEDS_CHANGES: an
    # unused enum value is inert.
    op.drop_column("registrations", "held_at")
    op.drop_column("registrations", "held_by_id")
    op.drop_column("registrations", "hold_note")
