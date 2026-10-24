"""colleges.email_domains: the fence belongs to the tenant, not the deployment

Revision ID: a1f4c7d92e08
Revises: 9b2d47f0ce15
Create Date: 2026-09-12

B1.1 of docs/redesign-2026-09/04-backend-changes.md.

Until now one list from the environment (`GOOGLE_ALLOWED_DOMAIN` and
`ROSTER_EMAIL_DOMAIN`, read through `Settings.provisionable_email_domains`)
fenced every application on the deployment, whichever college it named. With one
college that is the same thing. With two it is a hole: an applicant to college B
is admitted on college A's domain, and the roster IS the access control.

THE BACKFILL WRITES DOWN WHAT IS ALREADY TRUE. Every existing college is fenced
by the environment list today — that is the only fence there has ever been — so
copying it onto each row records a fact rather than inventing one, and the
Colleges screen then shows the real fence instead of "none recorded", which
would read as "no fence at all". Colleges created after this migration start
empty and fall back to the environment (app/institution_domains.py), so nothing
about day one changes either way.

It deliberately does NOT match on `code = 'BGSCET'`. The spec names that college
because it is the one on this deployment, but a `WHERE code = 'BGSCET'` is a
statement that silently does nothing on any deployment that named its college
something else — and a migration whose only failure mode is doing nothing is the
worst kind to write.

Reading the environment from inside a migration is unusual and is the point: the
value being preserved IS the environment's, and hard-coding a domain here would
bake this deployment's college address into every future one.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1f4c7d92e08"
down_revision: Union[str, None] = "9b2d47f0ce15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "colleges",
        sa.Column(
            "email_domains",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )

    # Imported here, not at module scope: a migration that fails to import takes
    # `alembic upgrade` down before it can report which revision broke.
    from app.config import settings

    domains = sorted(settings.provisionable_email_domains)
    if domains:
        op.execute(
            sa.text("UPDATE colleges SET email_domains = :domains").bindparams(
                sa.bindparam("domains", value=domains, type_=postgresql.ARRAY(sa.String()))
            )
        )


def downgrade() -> None:
    op.drop_column("colleges", "email_domains")
