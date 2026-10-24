"""Time sheet — minutes per calendar day per activity bucket (ported from Prisma
`TimeSheetEntry`). One row per (student, day, activity); the day is a date, not
an instant, so a bucket can't shift across a timezone change.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class DayActivity(str, enum.Enum):
    SLEEPING = "SLEEPING"
    LEISURE = "LEISURE"
    LECTURES = "LECTURES"
    COURSEWORK = "COURSEWORK"
    SKILLING = "SKILLING"


class TimeSheetEntry(Base):
    __tablename__ = "time_sheet_entries"
    __table_args__ = (
        UniqueConstraint("student_id", "day", "activity", name="uq_timesheet"),
        CheckConstraint("minutes >= 0 AND minutes <= 1440", name="ck_timesheet_minutes"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    activity: Mapped[DayActivity] = mapped_column(Enum(DayActivity, name="day_activity"))
    minutes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
