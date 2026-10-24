"""Registration documents — a CV and a photo attached before the application is decided.

One new table. An applicant has no Student row until a director approves them,
so their CV cannot be an `uploads` row (keyed on students.id); it is owned by the
application instead and MOVED into `uploads` on approval — same stored_name, so
the bytes are never copied — or deleted with the application on rejection and
on the sweep of never-verified applications.

`kind` is a plain String, not a PG enum (the `auth_tokens.purpose` choice); the
unique (registration_id, kind) is what makes a re-upload a replace. The FK
carries the leading index the FK-index guard demands.

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record: pure DDL on a brand new
table, no lock anybody is waiting on.

Revision ID: a3d7e5f9c2b1
Revises: f2a5d83c91b4
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d7e5f9c2b1"
down_revision: Union[str, None] = "f2a5d83c91b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.create_table(
        "registration_documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("registration_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("original_name", sa.String(), nullable=False),
        sa.Column("stored_name", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["registration_id"], ["registrations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_name"),
        sa.UniqueConstraint("registration_id", "kind", name="uq_regdoc_registration_kind"),
    )
    op.create_index("ix_regdoc_registration", "registration_documents", ["registration_id"])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_index("ix_regdoc_registration", table_name="registration_documents")
    op.drop_table("registration_documents")
