"""Governance — capability grants, access groups, and student feature overrides.

Four tables and two enums. See app/models/governance.py for the reasoning; the
short version is that these are TWO instruments with OPPOSITE defaults:
capability grants are deny-by-default for staff, feature overrides are
allow-by-default for students and are switched off down the institutional
hierarchy.

WHY THE ENUMS ARE NEW TYPES AND `capability` / `feature` ARE PLAIN STRINGS.
The subject kind (USER/GROUP) and the hierarchy rung are closed vocabularies the
database should enforce — a row naming a seventh rung is meaningless. The
capability and feature KEYS are not: their catalogue lives in code
(models/governance.py), adding one is a deploy, and making them a PG enum would
turn every new capability into a type migration. Same choice `auth_tokens.purpose`
made, for the same reason.

THE CHECK CONSTRAINT IS THE POINT OF THE GRANTS TABLE. `subject_kind` says which
of the two subject columns is meaningful, and without the constraint a row can
set both, or neither, and be a grant nobody holds and nothing revokes. Expressed
in SQL rather than in the router because the router is not the only writer a
database ever has.

`target_id` on feature_overrides is deliberately NOT a foreign key: it points at
colleges, departments, academic_courses, academic_specializations, cohorts or
students depending on `scope`, and no database can express a polymorphic FK.
Resolution joins explicitly per level instead (app/governance.py).

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record. Pure DDL on four brand
new tables: no lock anybody is waiting on.

Revision ID: f2a5d83c91b4
Revises: e3c9a4d15f80
Create Date: 2026-09-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a5d83c91b4"
down_revision: Union[str, None] = "e3c9a4d15f80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SUBJECT_KIND = sa.Enum("USER", "GROUP", name="governance_subject_kind")
_FEATURE_SCOPE = sa.Enum(
    "COLLEGE", "DEPARTMENT", "COURSE", "SPECIALIZATION", "COHORT", "STUDENT",
    name="governance_feature_scope",
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")

    op.create_table(
        "access_groups",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=400), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_access_group_name"),
    )
    op.create_index("ix_access_groups_created_by_user_id", "access_groups", ["created_by_user_id"])

    op.create_table(
        "access_group_members",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("added_by_user_id", sa.String(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["access_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "user_id", name="uq_access_group_member"),
    )
    op.create_index("ix_access_group_member_user", "access_group_members", ["user_id"])
    op.create_index("ix_access_group_members_added_by_user_id", "access_group_members", ["added_by_user_id"])

    op.create_table(
        "capability_grants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("subject_kind", _SUBJECT_KIND, nullable=False),
        sa.Column("subject_user_id", sa.String(), nullable=True),
        sa.Column("subject_group_id", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("granted_by_user_id", sa.String(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(), nullable=True),
        sa.Column("revoke_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["subject_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_group_id"], ["access_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # Exactly one subject. Without this a grant can name both a user and a
        # group, or neither — held by nobody, revocable by no one, and invisible
        # to both lookups.
        sa.CheckConstraint(
            "(subject_kind = 'USER'  AND subject_user_id  IS NOT NULL AND subject_group_id IS NULL)"
            " OR "
            "(subject_kind = 'GROUP' AND subject_group_id IS NOT NULL AND subject_user_id  IS NULL)",
            name="ck_capability_grant_one_subject",
        ),
    )
    op.create_index("ix_capgrant_user_live", "capability_grants", ["subject_user_id", "capability", "revoked_at"])
    op.create_index("ix_capgrant_group_live", "capability_grants", ["subject_group_id", "capability", "revoked_at"])
    op.create_index("ix_capgrant_capability", "capability_grants", ["capability"])
    op.create_index("ix_capability_grants_granted_by_user_id", "capability_grants", ["granted_by_user_id"])
    op.create_index("ix_capability_grants_revoked_by_user_id", "capability_grants", ["revoked_by_user_id"])

    op.create_table(
        "feature_overrides",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("scope", _FEATURE_SCOPE, nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("set_by_user_id", sa.String(), nullable=True),
        sa.Column("set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["set_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # One rule per (feature, rung, target). A second row for the same target
        # would make "is this on?" depend on which one the planner returned.
        sa.UniqueConstraint("feature", "scope", "target_id", name="uq_feature_override_target"),
    )
    op.create_index("ix_feature_override_lookup", "feature_overrides", ["feature", "scope", "target_id"])
    op.create_index("ix_feature_overrides_set_by_user_id", "feature_overrides", ["set_by_user_id"])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    # Dropping these DISCARDS every grant and every override. A downgrade
    # therefore re-opens every student feature that was switched off and drops
    # every capability an admin issued — the access model reverts to roles alone.
    # That is recoverable only from a backup, so take one first.
    op.drop_table("feature_overrides")
    op.drop_table("capability_grants")
    op.drop_table("access_group_members")
    op.drop_table("access_groups")
    _FEATURE_SCOPE.drop(op.get_bind(), checkfirst=True)
    _SUBJECT_KIND.drop(op.get_bind(), checkfirst=True)
