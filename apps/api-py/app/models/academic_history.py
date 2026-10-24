"""Academic history — prior qualifications (10th/12th/diploma/UG/PG) and the
education gaps in months, which the eligibility engine reads (ported from Prisma
`AcademicQualification` / `AcademicGap`). A gap over the criteria threshold is a
common placement disqualifier, modelled explicitly rather than inferred.
"""

import enum
import uuid

from sqlalchemy import CheckConstraint, Enum, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class QualificationLevel(str, enum.Enum):
    TENTH = "TENTH"
    TWELFTH = "TWELFTH"
    DIPLOMA = "DIPLOMA"
    UNDERGRAD = "UNDERGRAD"
    POSTGRAD = "POSTGRAD"


class AcademicQualification(Base):
    __tablename__ = "academic_qualifications"
    __table_args__ = (
        Index("ix_acadqual_student_level", "student_id", "level"),
        CheckConstraint("max_marks > 0 AND marks >= 0 AND marks <= max_marks", name="ck_acadqual_marks"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    level: Mapped[QualificationLevel] = mapped_column(
        Enum(QualificationLevel, name="qualification_level")
    )
    institution: Mapped[str] = mapped_column(String)
    board: Mapped[str | None] = mapped_column(String, nullable=True)
    year: Mapped[int] = mapped_column(Integer)
    marks: Mapped[float] = mapped_column(Float)
    max_marks: Mapped[float] = mapped_column(Float, default=100, server_default="100")
    medium: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    subjects: Mapped[str | None] = mapped_column(String, nullable=True)


class AcademicGap(Base):
    __tablename__ = "academic_gaps"

    # One row per student — student_id IS the primary key.
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), primary_key=True
    )
    twelfth_to_grad_mo: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    diploma_to_grad_mo: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    grad_to_pg_mo: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    other_mo: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
