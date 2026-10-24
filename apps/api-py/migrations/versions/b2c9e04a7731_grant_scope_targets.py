"""capability_grants: where a grant reaches

Revision ID: b2c9e04a7731
Revises: a1f4c7d92e08
Create Date: 2026-09-12

B1.2 of docs/redesign-2026-09/04-backend-changes.md.

A grant said WHAT somebody may do and never WHERE. With one college that is the
whole answer. With two it is not: `admin.students` granted to a faculty member
in one college reads every student in the other, and the Governance screen shows
one word — the capability's label — with nothing to say how far it goes.

THE ENUM IS THE ONE THAT ALREADY EXISTS. `governance_feature_scope` holds
COLLEGE / DEPARTMENT / COURSE / SPECIALIZATION / COHORT / STUDENT — the rungs of
the institutional spine — because a feature override hangs on one. A grant hangs
on the same rungs, so this renames the type to `governance_scope_level` and
reuses it rather than creating a second enum with identical members whose only
difference is the word in front of "Scope". The Python class is renamed to match
(`ScopeLevel`); the rename is metadata-only and instant.

NULL IS PROGRAMME-WIDE. A grant that reaches everything hangs on no rung. The
check constraint is what stops the pair from disagreeing: a level with no id
reaches nothing, an id with no level reaches everything, and both fail silently.

THE BACKFILL IS NARROWER THAN THE SPEC ASKS, ON PURPOSE. 04 says to backfill
every grant to BGSCET "so nobody loses access". That is right on a deployment
with one college and wrong on a deployment with two, where narrowing a grant
that currently reaches everything to one college REMOVES access from whoever
holds it — which is the one thing a backfill must never do. So: exactly one
college, and every grant is pinned to it, which loses nobody anything and writes
down what is already true. More than one, and the grants stay NULL — they are
programme-wide today and the migration is not the place to decide which college
each holder belongs to. The Governance screen can then narrow them deliberately,
with a reason, on the audit trail, which is what B2.x is for.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c9e04a7731"
down_revision: Union[str, None] = "a1f4c7d92e08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEVELS = ("COLLEGE", "DEPARTMENT", "COURSE", "SPECIALIZATION", "COHORT", "STUDENT")


def upgrade() -> None:
    op.execute("ALTER TYPE governance_feature_scope RENAME TO governance_scope_level")

    # create_type=False: the type exists — it was created by the governance
    # migration and has just been renamed above. Without it Alembic emits a bare
    # CREATE TYPE and the migration dies on "type already exists" (AGENTS.md's
    # second enum gotcha).
    level = postgresql.ENUM(*_LEVELS, name="governance_scope_level", create_type=False)
    op.add_column("capability_grants", sa.Column("scope_level", level, nullable=True))
    op.add_column("capability_grants", sa.Column("scope_id", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_capability_grant_scope_pair",
        "capability_grants",
        "(scope_level IS NULL AND scope_id IS NULL)"
        " OR (scope_level IS NOT NULL AND scope_id IS NOT NULL)",
    )
    op.create_index(
        "ix_capgrant_scope", "capability_grants", ["scope_level", "scope_id"]
    )

    colleges = op.get_bind().execute(sa.text("SELECT id FROM colleges")).scalars().all()
    if len(colleges) == 1:
        op.execute(
            sa.text(
                "UPDATE capability_grants"
                "   SET scope_level = 'COLLEGE', scope_id = :college"
                " WHERE scope_level IS NULL"
            ).bindparams(college=colleges[0])
        )


def downgrade() -> None:
    op.drop_index("ix_capgrant_scope", table_name="capability_grants")
    op.drop_constraint("ck_capability_grant_scope_pair", "capability_grants", type_="check")
    op.drop_column("capability_grants", "scope_id")
    op.drop_column("capability_grants", "scope_level")
    op.execute("ALTER TYPE governance_scope_level RENAME TO governance_feature_scope")
