"""Staff signatures - one uploaded image per staff account, for the leave paper.

One new table. See app/models/staff_signature.py: keyed on users.id (unique),
the opaque stored name, the sniffed MIME type and size, and when it was
uploaded. CASCADE from users: the image goes with the account.

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record: pure DDL on a brand new
table, no lock anybody is waiting on.

Revision ID: e7b1c9d4a2f6
Revises: c5f9a3e7d2b4
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7b1c9d4a2f6"
down_revision: Union[str, None] = "c5f9a3e7d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.create_table(
        "staff_signatures",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("stored_name", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_name"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_table("staff_signatures")
