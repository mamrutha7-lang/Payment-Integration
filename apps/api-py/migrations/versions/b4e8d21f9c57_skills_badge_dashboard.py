"""Skills & Badge dashboard — evidence, awards, cert catalogue, growth

The student-state half of the badge framework (models/badge.py — the 48-badge
catalogue itself is CODE, milestone-style, and puts no rows here):

  approved_certifications   §12's admin-maintained catalogue
  badge_evidence            evidence claims + the four-state review workflow
  student_badges            in-progress / earned, points stamped at award
  capability_assessments    the seven §9 capabilities at T0–T4

HAND-WRITTEN for the enums, per AGENTS.md's gotchas: the five NEW types are
created explicitly ahead of the tables (gotcha a), every column references them
with create_type=False, and `stage` — reused by approved_certifications — is
the EXISTING type from f65867efe738 and is referenced, never created (gotcha b).
badge_evidence_type appears on two tables and shares the single Enum instance
(gotcha c).

Revision ID: b4e8d21f9c57
Revises: a91d47c6b3e8
Create Date: 2026-08-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4e8d21f9c57"
down_revision: Union[str, Sequence[str], None] = "a91d47c6b3e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EVIDENCE_TYPE = postgresql.ENUM(
    "EXTERNAL_VERIFIED", "BGSCET_ASSESSED", "APPLIED",
    name="badge_evidence_type",
    create_type=False,
)
EVIDENCE_STATUS = postgresql.ENUM(
    "PENDING_VERIFICATION", "APPROVED", "REJECTED", "MORE_INFO_REQUIRED",
    name="badge_evidence_status",
    create_type=False,
)
STUDENT_BADGE_STATUS = postgresql.ENUM(
    "IN_PROGRESS", "EARNED", name="student_badge_status", create_type=False
)
CAPABILITY_KIND = postgresql.ENUM(
    "READING", "WRITING", "LISTENING", "SPEAKING",
    "QUANTITATIVE", "DATA_INTERPRETATION", "LOGICAL_REASONING",
    name="capability_kind",
    create_type=False,
)
CHECKPOINT = postgresql.ENUM(
    "T0", "T1", "T2", "T3", "T4", name="assessment_checkpoint", create_type=False
)

_NEW_TYPES = (EVIDENCE_TYPE, EVIDENCE_STATUS, STUDENT_BADGE_STATUS, CAPABILITY_KIND, CHECKPOINT)

# EXISTING — created by the auth-slice migration. Referenced, never created.
STAGE = postgresql.ENUM(
    "REBOOT", "EXCEL", "EXCEL_ADVANCED", "ELEVATE", name="stage", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    for t in _NEW_TYPES:
        t.create(bind, checkfirst=True)

    op.create_table(
        "approved_certifications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("badge_code", sa.String(), nullable=False),
        sa.Column(
            "evidence_type", EVIDENCE_TYPE, nullable=False, server_default="EXTERNAL_VERIFIED"
        ),
        sa.Column("stage", STAGE, nullable=False, server_default="EXCEL"),
        sa.Column("duration_text", sa.String(), nullable=True),
        sa.Column("is_free", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_approvedcert_badge", "approved_certifications", ["badge_code"])

    op.create_table(
        "badge_evidence",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "student_id",
            sa.String(),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("badge_code", sa.String(), nullable=False),
        sa.Column("evidence_type", EVIDENCE_TYPE, nullable=False),
        sa.Column(
            "status", EVIDENCE_STATUS, nullable=False, server_default="PENDING_VERIFICATION"
        ),
        sa.Column(
            "upload_id", sa.String(), sa.ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "approved_certification_id",
            sa.String(),
            sa.ForeignKey("approved_certifications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("completed_on", sa.Date(), nullable=True),
        sa.Column("student_note", sa.String(), nullable=True),
        sa.Column("review_note", sa.String(), nullable=True),
        sa.Column("reviewed_by_id", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_badgeevidence_student_badge", "badge_evidence", ["student_id", "badge_code"])
    op.create_index("ix_badgeevidence_status", "badge_evidence", ["status"])

    op.create_table(
        "student_badges",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "student_id",
            sa.String(),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("badge_code", sa.String(), nullable=False),
        sa.Column("status", STUDENT_BADGE_STATUS, nullable=False, server_default="IN_PROGRESS"),
        sa.Column("points_awarded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("awarded_by_id", sa.String(), nullable=True),
        sa.Column("award_note", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint("student_id", "badge_code", name="uq_student_badge"),
    )
    op.create_index("ix_studentbadge_student", "student_badges", ["student_id"])

    op.create_table(
        "capability_assessments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "student_id",
            sa.String(),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", CAPABILITY_KIND, nullable=False),
        sa.Column("checkpoint", CHECKPOINT, nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("recorded_by_id", sa.String(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "student_id", "capability", "checkpoint", name="uq_capability_checkpoint"
        ),
    )
    op.create_index("ix_capassess_student", "capability_assessments", ["student_id"])


def downgrade() -> None:
    op.drop_index("ix_capassess_student", table_name="capability_assessments")
    op.drop_table("capability_assessments")
    op.drop_index("ix_studentbadge_student", table_name="student_badges")
    op.drop_table("student_badges")
    op.drop_index("ix_badgeevidence_status", table_name="badge_evidence")
    op.drop_index("ix_badgeevidence_student_badge", table_name="badge_evidence")
    op.drop_table("badge_evidence")
    op.drop_index("ix_approvedcert_badge", table_name="approved_certifications")
    op.drop_table("approved_certifications")
    bind = op.get_bind()
    for t in reversed(_NEW_TYPES):
        t.drop(bind, checkfirst=True)
    # `stage` predates this migration and is not dropped.
