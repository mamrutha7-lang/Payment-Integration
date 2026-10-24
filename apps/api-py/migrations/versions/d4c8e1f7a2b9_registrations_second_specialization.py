"""registrations.second_specialization_id — the second tick of a dual
specialization.

Revision ID: d4c8e1f7a2b9
Revises: b7d2e4f9a1c3
Create Date: 2026-09-22

The public form's Specialization box was a single <select>, and a student who
opted for a DUAL specialization (Finance and Marketing, say) could name only
one of them, so the office learned of the second by phone or not at all. The
box is a checklist now, capped at two, and this is where the second tick
lands: a sixth nullable, indexed, SET NULL foreign key beside the five
c5f9a3e7d2b4 added, for c5f9a3e7d2b4's reasons — an application must survive
the office archiving a specialization, and an ADD COLUMN ... NULL is a
catalogue write, not a rewrite. `specialization_id` keeps its meaning and
every reader it had.

NO BACKFILL, and nothing to backfill from: every row already written named at
most one specialization, and that is a fact about the form those applicants
filled in, not a gap in the data.

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4c8e1f7a2b9"
down_revision: Union[str, None] = "b7d2e4f9a1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMN = "second_specialization_id"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.add_column("registrations", sa.Column(_COLUMN, sa.String(), nullable=True))
    op.create_foreign_key(
        f"fk_registrations_{_COLUMN}",
        "registrations",
        "academic_specializations",
        [_COLUMN],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(f"ix_registrations_{_COLUMN}", "registrations", [_COLUMN])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_index(f"ix_registrations_{_COLUMN}", table_name="registrations")
    op.drop_constraint(f"fk_registrations_{_COLUMN}", "registrations", type_="foreignkey")
    op.drop_column("registrations", _COLUMN)
