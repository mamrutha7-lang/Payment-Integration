"""Skills — a shared catalogue (`Skill`) and the per-student join (`StudentSkill`)
that carries proficiency and whether a mentor verified it (ported from Prisma).
The catalogue exists so "MS Excel" and "Advanced Excel" match one skill, which is
what makes a job-match percentage meaningful.

SkillClaim is the review workflow that feeds StudentSkill: a student claims a
level backed by an Upload, and a reviewer grants or reduces it. It reuses the
existing `upload_status` PG enum (create_type=False).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .upload import UploadStatus


def _uuid() -> str:
    return uuid.uuid4().hex


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (Index("ix_skills_category", "category"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String, unique=True)  # stable machine key
    name: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudentSkill(Base):
    __tablename__ = "student_skills"
    __table_args__ = (
        UniqueConstraint("student_id", "skill_id", name="uq_student_skill"),
        Index("ix_student_skills_skill", "skill_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    # 1 Aware · 2 Beginner · 3 Working · 4 Proficient · 5 Expert.
    level: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    evidence_upload_id: Mapped[str | None] = mapped_column(String, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    skill: Mapped[Skill] = relationship()


class SkillClaim(Base):
    """A student's claim to a skill level, backed by an uploaded artefact, that a
    reviewer grants or reduces. `claimed_level` is what the student asserts; the
    reviewer may grant it or lower it. Reuses the Upload review status; reviewer
    is a plain audit-stamp column, as on Upload."""

    __tablename__ = "skill_claims"
    __table_args__ = (
        Index("ix_skillclaim_student_status", "student_id", "status"),
        Index("ix_skillclaim_status", "status"),
        Index("ix_skillclaim_skill", "skill_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    # SET NULL, not CASCADE, and NULLABLE — matching badge_evidence.upload_id
    # (models/badge.py:355) and for its stated reason: "deleting the upload
    # leaves the claim (and its verdict) as an honest audit line rather than
    # vanishing history."
    #
    # It was CASCADE and NOT NULL, so a student tidying their own uploads screen
    # silently destroyed the whole SkillClaim row — the level a mentor granted,
    # the reviewer's identity and timestamp, and the review note they wrote.
    # Neither party was told, and nothing else in the system held a copy. The
    # student is entitled to delete their own certificate; they are not entitled
    # to delete a mentor's assessment of it, and one FK made those the same act.
    #
    # The response models were already built for this: SkillClaimReviewOut's
    # evidence_* fields are optional "because a claim can outlive its upload",
    # and _claim_query has always used an OUTER join.
    upload_id: Mapped[str | None] = mapped_column(
        ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # The level being claimed, which the reviewer may grant or reduce.
    claimed_level: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, name="upload_status", create_type=False),
        default=UploadStatus.PENDING_REVIEW,
        server_default="PENDING_REVIEW",
    )
    # What the student told the mentor when filing — "what the certificate
    # covers". Distinct from review_note, which travels the other way and holds
    # the mentor's decision; one column could not hold both without the reply
    # erasing the context it was answering.
    student_note: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_by_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
