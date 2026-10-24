"""archived_documents — the manifest for the permanent document archive.

Revision ID: b7e4d21af905
Revises: f3a8d61c07be
Create Date: 2026-09-15

`app/document_archive.py` copies every stored file into a versioned,
Object-Locked bucket as it is accepted, which makes the BYTES permanent. The
key there is the `stored_name`, a bare `uuid4().hex`, and the only thing that
ever knew whose file that was is the live row pointing at it. This table is the
index, and it is append-only: one row per file, `released_at` stamped when the
live row stops pointing at it, and nothing in the product ever deletes one.

NO BACKFILL, AND THAT IS DELIBERATE. The obvious thing is to seed a row per
existing `stored_name` across the six document tables, and it would be WRONG in
the direction that matters: every row so written would carry `recorded_at =
now()`, telling every future reader that the whole store arrived on deploy day
-- `mentor_assignments`'s lesson, where `from_at` was taken from the account's
creation time rather than `now()` for exactly this reason, and a pairing with no
knowable date was left NULL. Here there is no honest substitute to reach for:
`uploads.uploaded_at` exists but `staff_signatures` and `alumni_profiles` carry
the timestamp of the CURRENT file, which is the one case the manifest is for.

What the absence costs is bounded and knowable: a file stored before this
migration has no manifest row, so `document_manifest.release` logs it and moves
on, and a restore falls back to the `pg_dump` tiers, which still hold the live
rows as they were. Every file stored AFTER it is indexed. `archive_documents`
uploads the old bytes to the bucket regardless -- the sweep reads the disk, not
this table -- so nothing is lost, only unnamed, and only for files that predate
this deploy.

`document_owner_kind` is a NEW enum used by a NEW table, so it is created by
`sa.Enum(...).create()` first and the column then declares it with
`create_type=False`. Autogenerate emits a bare `sa.Enum` here, which errors
"type already exists" on the second object that references it -- gotcha (b) in
AGENTS.md's Alembic notes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b7e4d21af905'
down_revision: Union[str, None] = 'f3a8d61c07be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KIND_VALUES = (
    "STUDENT_UPLOAD",
    "STAFF_CERTIFICATE",
    "STAFF_SIGNATURE",
    "LEAVE_ATTACHMENT",
    "ALUMNI_RESUME",
    "REGISTRATION_DOCUMENT",
    "INTERVIEW_AUDIO",
)


def upgrade() -> None:
    kind = postgresql.ENUM(*_KIND_VALUES, name="document_owner_kind")
    kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "archived_documents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("stored_name", sa.String(), nullable=False, unique=True),
        sa.Column(
            "kind",
            postgresql.ENUM(*_KIND_VALUES, name="document_owner_kind", create_type=False),
            nullable=False,
        ),
        # NO FOREIGN KEY on owner_id, and it is the point of the table rather
        # than an omission. An FK here would mirror the ON DELETE CASCADE the
        # columns it shadows carry, so the manifest would be destroyed at the
        # exact moment it becomes the only remaining record of the file --
        # when the account goes. See models/archived_document.py.
        sa.Column("owner_id", sa.String(), nullable=True),
        sa.Column("original_name", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_reason", sa.String(), nullable=True),
    )
    # Declared on the model too. An index that lives only in its migration is
    # what `alembic check` asks to DROP on every run, which is how a real drop
    # goes unnoticed in the noise -- f3a8d61c07be's lesson.
    op.create_index("ix_archived_documents_owner", "archived_documents", ["kind", "owner_id"])


def downgrade() -> None:
    op.drop_index("ix_archived_documents_owner", table_name="archived_documents")
    op.drop_table("archived_documents")
    postgresql.ENUM(name="document_owner_kind").drop(op.get_bind(), checkfirst=True)
