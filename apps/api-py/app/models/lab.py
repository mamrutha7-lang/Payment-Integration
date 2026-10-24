"""Lab / focus sessions — one check-in -> check-out block of study (ported from
Prisma `LabSession`). The SUPERVISED_LAB rows are what the mentor Focus Log
reads. duration_min is null while a session is still open.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class ActivityType(str, enum.Enum):
    LECTURE = "LECTURE"
    SUPERVISED_LAB = "SUPERVISED_LAB"
    ONLINE_COURSE = "ONLINE_COURSE"
    PRACTICE_PROBLEMS = "PRACTICE_PROBLEMS"
    GROUP_STUDY = "GROUP_STUDY"
    ASSIGNMENT = "ASSIGNMENT"
    PROJECT_WORK = "PROJECT_WORK"
    READING = "READING"
    REVISION = "REVISION"
    MOCK_INTERVIEW = "MOCK_INTERVIEW"
    PRESENTATION_PREP = "PRESENTATION_PREP"
    APTITUDE_PREP = "APTITUDE_PREP"
    INDUSTRY_VISIT = "INDUSTRY_VISIT"
    MENTOR_MEETING = "MENTOR_MEETING"
    OTHER = "OTHER"


class LearningMode(str, enum.Enum):
    INSTRUCTOR_LED = "INSTRUCTOR_LED"
    SUPERVISED_LAB = "SUPERVISED_LAB"
    INDEPENDENT = "INDEPENDENT"


class CheckInSource(str, enum.Enum):
    BADGE = "BADGE"
    LAB_PC = "LAB_PC"
    MANUAL = "MANUAL"
    SELF_REPORTED = "SELF_REPORTED"


class LabSession(Base):
    __tablename__ = "lab_sessions"
    __table_args__ = (
        Index("ix_labsession_student_checkin", "student_id", "check_in_at"),
        Index("ix_labsession_course", "course_code"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    course_code: Mapped[str] = mapped_column(ForeignKey("courses.code", ondelete="CASCADE"))
    module: Mapped[str] = mapped_column(String)
    activity: Mapped[ActivityType] = mapped_column(
        Enum(ActivityType, name="activity_type"),
        default=ActivityType.ONLINE_COURSE,
        server_default="ONLINE_COURSE",
    )
    activity_note: Mapped[str | None] = mapped_column(String, nullable=True)
    mode: Mapped[LearningMode] = mapped_column(
        Enum(LearningMode, name="learning_mode"),
        default=LearningMode.SUPERVISED_LAB,
        server_default="SUPERVISED_LAB",
    )
    source: Mapped[CheckInSource] = mapped_column(
        Enum(CheckInSource, name="check_in_source"),
        default=CheckInSource.LAB_PC,
        server_default="LAB_PC",
    )
    check_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_at_check_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    progress_at_check_out: Mapped[float | None] = mapped_column(Float, nullable=True)
    progress_delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mentor_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
