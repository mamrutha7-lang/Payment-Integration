"""Attendance — one row per student per course-session (ported from Prisma
`AttendanceRecord`). course_code is a plain string for now; the FK to Course is
added when that model is ported.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("student_id", "course_code", "session_no", name="uq_attendance"),
        Index("ix_attendance_student_date", "student_id", "session_date"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    course_code: Mapped[str] = mapped_column(String)
    session_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_no: Mapped[int] = mapped_column(Integer)
    present: Mapped[bool] = mapped_column(Boolean, default=True)
