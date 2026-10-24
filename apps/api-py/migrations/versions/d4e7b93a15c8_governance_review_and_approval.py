"""Grants gain a review date, an approval state and the role they were given to

Revision ID: d4e7b93a15c8
Revises: b2c9e04a7731
Create Date: 2026-09-13

B2.2, B2.4 and B2.5 of docs/redesign-2026-09/04-backend-changes.md.

`review_at` IS NOT AN EXPIRY and the difference is the whole point. An expiry
ends a grant; a review date only asks whether it is still the right grant. Most
of the access that goes wrong in an institution is access that was correct when
it was given and that nobody revisited — the person changed jobs, the project
ended, the cover arrangement became permanent by nobody cancelling it. So a
grant with no expiry still gets a review date.

`approval_state` is the two-person rule (B2.4): a capability that carries PII
does not take effect until a second Main Admin agrees, so the person granting
and the person agreeing are two people. It is a String with a check constraint
rather than a Postgres enum, for the same reason `capability` is one — a new
state should be a deploy, not a type migration.

`role_at_grant` is B2.5. A grant is a decision about a person IN A ROLE: "this
MENTOR may read the registrations queue". If the account later becomes something
else the decision no longer describes anybody, and a grant that silently
survives a role change is how a demoted account keeps a console screen. It
backfills NULL, and NULL is honoured rather than refused: the migration cannot
know what role was held when a 2025 grant was written, and guessing would revoke
real access on the deploy that shipped it.

`feature_overrides.student_message` (B2.2) is what the STUDENT is told, which is
deliberately not `reason`. "Withheld pending the disciplinary meeting" is a true
reason and not a sentence to put on a student's screen.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e7b93a15c8"
down_revision: Union[str, None] = "b2c9e04a7731"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "capability_grants", sa.Column("review_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "capability_grants",
        sa.Column(
            "approval_state",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "capability_grants", sa.Column("approved_by_user_id", sa.String(), nullable=True)
    )
    op.add_column(
        "capability_grants", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "capability_grants", sa.Column("role_at_grant", sa.String(length=32), nullable=True)
    )
    op.create_foreign_key(
        "fk_capgrant_approved_by",
        "capability_grants",
        "users",
        ["approved_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_capability_grants_approved_by_user_id", "capability_grants", ["approved_by_user_id"]
    )
    op.create_check_constraint(
        "ck_capability_grant_approval_state",
        "capability_grants",
        "approval_state IN ('active', 'pending_approval')",
    )

    op.add_column("feature_overrides", sa.Column("student_message", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("feature_overrides", "student_message")
    op.drop_constraint("ck_capability_grant_approval_state", "capability_grants", type_="check")
    op.drop_index("ix_capability_grants_approved_by_user_id", table_name="capability_grants")
    op.drop_constraint("fk_capgrant_approved_by", "capability_grants", type_="foreignkey")
    op.drop_column("capability_grants", "role_at_grant")
    op.drop_column("capability_grants", "approved_at")
    op.drop_column("capability_grants", "approved_by_user_id")
    op.drop_column("capability_grants", "approval_state")
    op.drop_column("capability_grants", "review_at")
