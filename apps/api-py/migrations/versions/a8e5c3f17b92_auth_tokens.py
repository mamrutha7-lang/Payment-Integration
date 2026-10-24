"""auth_tokens — single-use hashed links for activation and password reset.

See app/models/auth_token.py for the three rules (hashed, atomic single use,
short lives). `purpose` is a plain String, not a PG enum, so a third kind of
link is an INSERT. The unique on token_hash is NAMED so create_all() and this
migration agree on what to call it.

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record of what the alternatives
cost. Pure DDL on a brand-new table: no lock anybody is waiting on.

Revision ID: a8e5c3f17b92
Revises: f7c3a1d92e54
Create Date: 2026-09-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8e5c3f17b92"
down_revision: Union[str, None] = "f7c3a1d92e54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_tokens_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_auth_tokens_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name="uq_auth_token_hash"),
    )
    op.create_index("ix_auth_tokens_user_purpose", "auth_tokens", ["user_id", "purpose"])
    # ix_auth_tokens_created_by_user_id is NOT created here: c2f7a9d41e63 owns
    # it. Adding it here too made the chain create one index twice and drop it
    # twice, and the downgrade died on the second drop.


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index("ix_auth_tokens_user_purpose", table_name="auth_tokens")
    op.drop_table("auth_tokens")
