"""upload_status NEEDS_CHANGES

A review that is not a yes has two meanings — "fix this and claim again" and
"this will not be granted" — and the student's next action differs between them.
The enum only had REJECTED, so a mentor asking for a better certificate and a
mentor refusing the claim outright produced the same row.

Revision ID: b8d2f7a4c619
Revises: f3c81a5d2e70
Create Date: 2026-09-04 15:35:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b8d2f7a4c619'
down_revision: Union[str, None] = 'f3c81a5d2e70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE is allowed inside a transaction on PG12+ as long
    # as the new value is not USED in that same transaction; nothing here writes
    # a row, so this is safe under Alembic's transactional migration.
    op.execute("ALTER TYPE upload_status ADD VALUE IF NOT EXISTS 'NEEDS_CHANGES'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum. Removing it would mean
    # recreating the type and rewriting every column that uses it, and any row
    # already holding NEEDS_CHANGES would have to be mapped to something else —
    # a data decision, not a schema one. Left in place deliberately: an unused
    # enum value is inert.
    pass
