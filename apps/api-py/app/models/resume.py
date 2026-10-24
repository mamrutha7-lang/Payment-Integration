"""Resume drafts (ported from Prisma `Resume`). Generated from the student's own
REEP data. generated_by records whether a model wrote it or the deterministic
composer did — the latter is what happens when the configured model is remote
and student data may not leave the machine.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class ResumeStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    FINALISED = "FINALISED"


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (Index("ix_resume_student_created", "student_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    title: Mapped[str] = mapped_column(String, default="REEP Resume", server_default="REEP Resume")
    target_role: Mapped[str | None] = mapped_column(String, nullable=True)
    target_industry: Mapped[str | None] = mapped_column(String, nullable=True)
    job_description: Mapped[str | None] = mapped_column(String, nullable=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[ResumeStatus] = mapped_column(
        Enum(ResumeStatus, name="resume_status"), default=ResumeStatus.DRAFT, server_default="DRAFT"
    )
    content: Mapped[dict] = mapped_column(JSONB, default=dict)
    markdown: Mapped[str] = mapped_column(String, default="", server_default="")
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scoring: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    generated_by: Mapped[str] = mapped_column(String, default="fallback", server_default="fallback")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
