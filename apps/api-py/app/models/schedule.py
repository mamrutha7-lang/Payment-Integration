"""Schedule items — a student's calendar of sessions, lectures, cert deadlines
and mentor meetings (ported from Prisma `ScheduleItem`). course_code is a plain
string until Course is ported.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class ScheduleType(str, enum.Enum):
    LAB_SESSION = "LAB_SESSION"
    LECTURE = "LECTURE"
    CERT_DEADLINE = "CERT_DEADLINE"
    MENTOR_MEETING = "MENTOR_MEETING"


class ScheduleItem(Base):
    __tablename__ = "schedule_items"
    __table_args__ = (Index("ix_schedule_student_starts", "student_id", "starts_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    course_code: Mapped[str | None] = mapped_column(String, nullable=True)  # FK to Course later
    type: Mapped[ScheduleType] = mapped_column(Enum(ScheduleType, name="schedule_type"))
    title: Mapped[str] = mapped_column(String)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(String, nullable=True)
