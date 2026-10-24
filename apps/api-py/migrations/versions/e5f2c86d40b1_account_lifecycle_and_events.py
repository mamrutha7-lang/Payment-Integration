"""users.disabled_at, notification prefs, and the two append-only event tables

Revision ID: e5f2c86d40b1
Revises: d4e7b93a15c8
Create Date: 2026-09-13

B3.3, B14 and B15 of docs/redesign-2026-09/04-backend-changes.md.

DISABLING IS A TIMESTAMP, NOT A BOOLEAN. "Disabled" without "when" is
unanswerable six months later, and the 90-day window in which enabling restores
the login has to be measured from something. Nothing about the person is
deleted: the mentor notes they wrote, the leave they sanctioned and the evidence
they verified are part of OTHER people's records and were true when they were
written. Emptying a deployment of people is `python -m app.purge_people`, which
is built for it and dry-runs by default.

`login_events` is NOT `login_days`, which already exists. That one records one
row per student per day to draw a streak on the dashboard: no door, no address,
coarse on purpose. A person checking whether somebody else has been in their
account needs when, through which door, and from where — and needs it for staff,
who have no login_days rows at all.

`export_events` records a spreadsheet of students leaving the building. AGENTS.md
is blunt about what that is — "they leave REEP the moment they are downloaded and
nothing here can recall them" — and until now the only trace was a line in an
access log nobody reads.

Both new tables are append-only by intent. Neither gets an ON DELETE CASCADE to
`users` in the case that matters: `export_events.user_id` is SET NULL, because
the fact that an export happened outlives the account that made it, and a purge
that erased the evidence along with the person would be exactly backwards.
`login_events` DOES cascade, because a sign-in record is about that person alone
and is theirs to have erased with them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f2c86d40b1"
down_revision: Union[str, None] = "d4e7b93a15c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("disabled_by_user_id", sa.String(), nullable=True))
    op.add_column("users", sa.Column("disable_reason", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "notification_prefs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.create_foreign_key(
        "fk_users_disabled_by", "users", "users", ["disabled_by_user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_users_disabled_by_user_id", "users", ["disabled_by_user_id"])
    # Partial: the only question ever asked of this column is "is this account
    # disabled", and on a healthy deployment almost every row is NULL.
    op.create_index(
        "ix_users_disabled_at",
        "users",
        ["disabled_at"],
        postgresql_where=sa.text("disabled_at IS NOT NULL"),
    )

    op.create_table(
        "login_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("door", sa.String(length=32), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_login_events_at", "login_events", ["at"])
    # The query the screen actually runs: this user's most recent sign-ins.
    op.create_index("ix_login_events_user_at", "login_events", ["user_id", "at"])

    op.create_table(
        "export_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False, index=True),
        sa.Column("filters", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("carried_pii", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_export_events_at", "export_events", ["at"])


def downgrade() -> None:
    op.drop_table("export_events")
    op.drop_table("login_events")
    op.drop_index("ix_users_disabled_at", table_name="users")
    op.drop_index("ix_users_disabled_by_user_id", table_name="users")
    op.drop_constraint("fk_users_disabled_by", "users", type_="foreignkey")
    op.drop_column("users", "notification_prefs")
    op.drop_column("users", "disable_reason")
    op.drop_column("users", "disabled_by_user_id")
    op.drop_column("users", "disabled_at")
