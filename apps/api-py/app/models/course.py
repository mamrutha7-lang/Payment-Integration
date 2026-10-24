"""Curriculum — Course and a student's Enrollment (ported from Prisma). Course
is keyed by its code (e.g. 22MBA11). Reuses the existing `stage` PG enum type
(create_type=False) and adds Dimension / CourseModel / ProgressStatus.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .user import Stage


def _uuid() -> str:
    return uuid.uuid4().hex


class Dimension(str, enum.Enum):
    PROFESSIONAL = "PROFESSIONAL"
    THINKING = "THINKING"
    TECHNICAL = "TECHNICAL"
    METAPHYSICAL = "METAPHYSICAL"


class CourseModel(str, enum.Enum):
    TEACHING_PLUS_SELF_LEARN = "TEACHING_PLUS_SELF_LEARN"
    SUPERVISED_SELF_LEARN = "SUPERVISED_SELF_LEARN"
    INSTRUCTOR_LED = "INSTRUCTOR_LED"


class ProgressStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    OVERDUE = "OVERDUE"


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        Index("ix_courses_stage", "stage"),
        CheckConstraint(
            "semester >= 1 AND teaching_hours >= 0 AND self_learning_hours_required >= 0",
            name="ck_course_hours",
        ),
    )

    code: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. 22MBA11
    name: Mapped[str] = mapped_column(String)
    stage: Mapped[Stage] = mapped_column(Enum(Stage, name="stage", create_type=False))
    dimension: Mapped[Dimension] = mapped_column(Enum(Dimension, name="dimension"))
    semester: Mapped[int] = mapped_column(Integer)
    teaching_hours: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    self_learning_hours_required: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    model_type: Mapped[CourseModel] = mapped_column(Enum(CourseModel, name="course_model"))
    duration_weeks: Mapped[int] = mapped_column(Integer, default=16, server_default="16")
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # What a student pays to enrol, in paise. NULL or 0 = free: enrolling takes
    # effect at once. Positive = the enrolment is created only when the Razorpay
    # payment is confirmed (app/routers/fees.py).
    fee_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("student_id", "course_code", name="uq_enrollment"),
        Index("ix_enrollment_course", "course_code"),
        CheckConstraint(
            "lectures_attended >= 0 AND lectures_total >= 0 AND lectures_attended <= lectures_total "
            "AND teaching_hours_attended >= 0 AND self_learning_hours_logged >= 0",
            name="ck_enrollment_counts",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    course_code: Mapped[str] = mapped_column(ForeignKey("courses.code", ondelete="CASCADE"))
    status: Mapped[ProgressStatus] = mapped_column(
        Enum(ProgressStatus, name="progress_status"),
        default=ProgressStatus.IN_PROGRESS,
        server_default="IN_PROGRESS",
    )
    teaching_hours_attended: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    self_learning_hours_logged: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    lectures_attended: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lectures_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
