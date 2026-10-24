"""user google_sub pin + token_version revocation

Revision ID: a3f1c7d5b820
Revises: 6afb55d18ed8
Create Date: 2026-08-19 22:12:41.508233
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f1c7d5b820'
down_revision: Union[str, None] = '6afb55d18ed8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Two columns on `users`, both plain — no enum, so the explicit CREATE TYPE /
# create_type=False dance that 9ac9f4696b0d needed for `stage` does not apply
# here. That is a choice, not an oversight: a Google subject is an opaque string
# the provider owns, and a revocation counter is a number. Either as an enum
# would mean an ALTER TYPE ... ADD VALUE the first time reality moved.


def upgrade() -> None:
    # google_sub — the Google principal this row is pinned to; a sign-in whose
    # `sub` disagrees is refused in app/routers/auth.py. NULLABLE because every
    # row that already exists (the seeded logins, the whole seeded roster) has
    # never been through a Google sign-in, and they must keep working; the pin
    # is written on the first one. NOT NULL would mean inventing a `sub` for
    # rows that have none, and an invented pin is a lock with no key.
    op.add_column('users', sa.Column('google_sub', sa.String(), nullable=True))
    # Nullable AND unique is the deliberate pair: Postgres UNIQUE is NULLS
    # DISTINCT by default, so every un-pinned row coexists while no two rows can
    # ever be pinned to the same Google account. Adding NULLS NOT DISTINCT here
    # later would fail on the second un-pinned user — that is the constraint
    # being asked for the opposite of what it is for, not a bug to work around.
    op.create_unique_constraint('uq_users_google_sub', 'users', ['google_sub'])
    # token_version — bumped on logout, carried in the session JWT, compared on
    # the way back in (app/security.py). server_default is mandatory, not
    # decoration: `users` is populated, and NOT NULL without a default fails the
    # ALTER outright. The default stays on the column rather than being dropped
    # after backfill, because app/seed.py and app/seed_roster.py insert Users
    # without naming this field and would start failing the day it disappeared.
    op.add_column(
        'users',
        sa.Column('token_version', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    # Honest about what rolling back costs: dropping token_version signs nobody
    # back in (a missing claim already reads as version 0), but dropping
    # google_sub UN-PINS every account, so the next Google sign-in for an
    # address re-pins it to whoever presents it — the re-issued-address hole
    # this migration closed. This is a rollback path, not routine maintenance.
    op.drop_column('users', 'token_version')
    # Dropped explicitly even though Postgres would take the constraint down
    # with the column: the two statements are what the upgrade did, in reverse.
    op.drop_constraint('uq_users_google_sub', 'users', type_='unique')
    op.drop_column('users', 'google_sub')
