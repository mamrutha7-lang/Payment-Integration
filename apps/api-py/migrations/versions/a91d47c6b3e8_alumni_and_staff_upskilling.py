"""Alumni role + profile, and staff upskilling certificates

Three things, all serving the faculty/alumni pages:

  role enum          gains the value ALUMNI
  alumni_profiles    one row per alumnus — company, designation, resume metadata
  staff_upskilling_certs
                     a staff member's own completed-course certificates

HAND-WRITTEN. The enum change is the reason: autogenerate does not diff enum
VALUES at all, so `ALTER TYPE role ADD VALUE` has to be typed here or the
ALUMNI login 500s at the first `users.role` write. `IF NOT EXISTS` keeps the
upgrade idempotent against a database where a hotfix already added it. On
Postgres 12+ ADD VALUE is legal inside the migration's transaction as long as
this migration does not itself write a row using the new value — it doesn't.

No new enum types, so neither of AGENTS.md's enum gotchas applies to the tables.

Downgrade drops the two tables but leaves ALUMNI on the enum: Postgres has no
DROP VALUE, and rewriting the type under a live users table is a worse outcome
than a spare enum label nobody mints.

Revision ID: a91d47c6b3e8
Revises: e4c1b7a9d203
Create Date: 2026-08-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a91d47c6b3e8"
down_revision: Union[str, Sequence[str], None] = "e4c1b7a9d203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'ALUMNI'")

    op.create_table(
        "alumni_profiles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("designation", sa.String(), nullable=True),
        sa.Column("graduation_year", sa.Integer(), nullable=True),
        sa.Column("resume_original_name", sa.String(), nullable=True),
        sa.Column("resume_stored_name", sa.String(), nullable=True, unique=True),
        sa.Column("resume_mime_type", sa.String(), nullable=True),
        sa.Column("resume_size_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )

    op.create_table(
        "staff_upskilling_certs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("completed_on", sa.Date(), nullable=True),
        sa.Column("original_name", sa.String(), nullable=False),
        sa.Column("stored_name", sa.String(), nullable=False, unique=True),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_staffcert_user", "staff_upskilling_certs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_staffcert_user", table_name="staff_upskilling_certs")
    op.drop_table("staff_upskilling_certs")
    op.drop_table("alumni_profiles")
    # The ALUMNI enum value stays — see the module docstring.
