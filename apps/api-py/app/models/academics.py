"""Academic results — VTU semester results and per-subject marks (ported from
Prisma `SemesterResult` / `SubjectMark`). Internal and external are kept
separate, not folded into total, because the diagnostic lives in the split.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class SemesterResult(Base):
    __tablename__ = "semester_results"
    __table_args__ = (
        UniqueConstraint("student_id", "semester", name="uq_semester_result"),
        CheckConstraint(
            "semester >= 1 AND (sgpa IS NULL OR sgpa BETWEEN 0 AND 10) "
            "AND (cgpa IS NULL OR cgpa BETWEEN 0 AND 10)",
            name="ck_semester_result_range",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # No index= here: `uq_semester_result` leads with student_id already.
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    semester: Mapped[int] = mapped_column(Integer)

    sgpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    cgpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The eligibility engine reads live_backlogs (a "no live backlogs" rule);
    # closed_backlogs is history.
    closed_backlogs: Mapped[int] = mapped_column(Integer, default=0)
    live_backlogs: Mapped[int] = mapped_column(Integer, default=0)
    marksheet_upload_id: Mapped[str | None] = mapped_column(String, nullable=True)
    result_class: Mapped[str | None] = mapped_column(String, nullable=True)
    published_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subjects: Mapped[list["SubjectMark"]] = relationship(
        back_populates="semester_result",
        cascade="all, delete-orphan",
        order_by="SubjectMark.subject_code",
    )


class SubjectMark(Base):
    __tablename__ = "subject_marks"
    __table_args__ = (
        UniqueConstraint("semester_result_id", "subject_code", name="uq_subject_mark"),
        # Non-negative only. The "out of 50" beside internal/external is one
        # VTU scheme, and schemes change without a migration.
        CheckConstraint(
            "credits >= 0 AND internal >= 0 AND external >= 0 AND total >= 0",
            name="ck_subject_mark_nonnegative",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    semester_result_id: Mapped[str] = mapped_column(
        ForeignKey("semester_results.id", ondelete="CASCADE")
    )

    subject_code: Mapped[str] = mapped_column(String)
    subject_name: Mapped[str] = mapped_column(String)
    credits: Mapped[int] = mapped_column(Integer, default=4)
    internal: Mapped[int] = mapped_column(Integer, default=0)  # out of 50 (VTU CBCS)
    external: Mapped[int] = mapped_column(Integer, default=0)  # out of 50
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, default=True)

    semester_result: Mapped[SemesterResult] = relationship(back_populates="subjects")
