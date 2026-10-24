"""users.deleted_at — removed from the roster, still in the database

Revision ID: c7d3e9a1f5b2
Revises: d7e2f9a41c86
Create Date: 2026-09-16

The Main Admin asked for two ways to delete a student or a faculty account:
one that is permanent and demands a code emailed to the office, and one that
takes the person off every screen while leaving every row they own in the
database, to be restored later with nothing lost. This is the second one's
column, and it is a column beside `disabled_at` rather than a reuse of it:
`app/models/user.py` says why — a disabled account is meant to stay LISTED
(greyed, with its date), a removed one is meant to leave the lists, and an
account that was disabled and then removed must come back disabled.

Three columns of the same shape as the disabling trio (e5f2c86d40b1) and the
same partial index, declared on the model too — an index that exists only in a
migration is the drift `alembic check` reports on every run and nobody reads.

DOWNGRADE DROPS THE COLUMNS AND LOSES THE FACT. Every removed account becomes
a plain, listed, signable-in account again on the way down; a migration that
tried to keep the fact would have to invent a `disabled_at` for it, which is a
different decision made by nobody.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d3e9a1f5b2"
down_revision: Union[str, None] = "d7e2f9a41c86"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("deleted_by_user_id", sa.String(), nullable=True))
    op.add_column("users", sa.Column("delete_reason", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_users_deleted_by",
        "users",
        "users",
        ["deleted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_deleted_by_user_id", "users", ["deleted_by_user_id"])
    op.create_index(
        "ix_users_deleted_at",
        "users",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_index("ix_users_deleted_by_user_id", table_name="users")
    op.drop_constraint("fk_users_deleted_by", "users", type_="foreignkey")
    op.drop_column("users", "delete_reason")
    op.drop_column("users", "deleted_by_user_id")
    op.drop_column("users", "deleted_at")
