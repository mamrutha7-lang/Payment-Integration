"""Certifications tied to a course, and a student's progress toward each (ported
from Prisma). CertificationProgress reuses the existing `progress_status` PG enum
(create_type=False).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .course import ProgressStatus


def _uuid() -> str:
    return uuid.uuid4().hex


class Certification(Base):
    __tablename__ = "certifications"
    __table_args__ = (
        Index("ix_cert_course", "course_code"),
        CheckConstraint("required_hours >= 0", name="ck_certification_required_hours"),
    )

    code: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. CERT-22MBA11-LEAD
    course_code: Mapped[str] = mapped_column(ForeignKey("courses.code", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String, default="Coursera", server_default="Coursera")
    required_hours: Mapped[float] = mapped_column(Float, default=10, server_default="10")
    link: Mapped[str | None] = mapped_column(String, nullable=True)
    is_optional: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    due_week: Mapped[int] = mapped_column(Integer, default=12, server_default="12")


class CertificationProgress(Base):
    __tablename__ = "certification_progress"
    __table_args__ = (
        UniqueConstraint("student_id", "cert_code", name="uq_cert_progress"),
        Index("ix_certprog_student_status", "student_id", "status"),
        CheckConstraint(
            "progress_pct >= 0 AND progress_pct <= 100 AND hours_logged >= 0",
            name="ck_cert_progress_range",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    cert_code: Mapped[str] = mapped_column(ForeignKey("certifications.code", ondelete="CASCADE"), index=True)
    status: Mapped[ProgressStatus] = mapped_column(
        Enum(ProgressStatus, name="progress_status", create_type=False),
        default=ProgressStatus.NOT_STARTED,
        server_default="NOT_STARTED",
    )
    progress_pct: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    hours_logged: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    self_reported: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
