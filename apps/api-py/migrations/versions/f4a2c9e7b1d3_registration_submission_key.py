"""registrations.submission_key_hash — a retry after a lost reply is the same application.

Revision ID: f4a2c9e7b1d3
Revises: e2b7c4d9f1a6
Create Date: 2026-10-01

A student on a phone pressed Submit, the server saved the application, and the
reply never reached the handset. Pressing Submit again met the duplicate
guard's deliberately opaque 409 — "could not be accepted, contact the
placement office" — which reads as a refusal to somebody who has in fact just
applied. The form now mints one random key per filled-in form and sends it
with every submit; `submit` answers a repeat carrying the same key with this
row's original 201. A different key still meets the opaque 409, so the public
form is no more of a "has X applied" lookup than before.

The column holds the sha256 hex of the key, never the key: it is the bearer
for reading the application back, and `auth_tokens` stores every other bearer
the same way. No index — it is only ever compared against the ONE live row the
duplicate guard has already found by email.

NO BACKFILL. Every existing row was written by a form that sent no key, so
NULL is the truth about it, and NULL never matches: those rows keep meeting
the opaque 409 exactly as they did.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a2c9e7b1d3"
down_revision: Union[str, None] = "e2b7c4d9f1a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "registrations",
        sa.Column("submission_key_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    # Exact: the column holds nothing but a hash of a key the client still
    # has; dropping it only returns retries to the opaque 409.
    op.drop_column("registrations", "submission_key_hash")
