"""Mock assessments — the placement cell's GD / interview / aptitude rehearsals
(ported from Prisma `MockAttempt`).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class MockType(str, enum.Enum):
    GD = "GD"
    INTERVIEW = "INTERVIEW"
    APTITUDE = "APTITUDE"


class MockAttempt(Base):
    __tablename__ = "mock_attempts"
    __table_args__ = (
        Index("ix_mock_student_taken", "student_id", "taken_on"),
        Index("ix_mock_type_taken", "type", "taken_on"),
        CheckConstraint(
            "(score IS NULL OR score >= 0) AND (max_score IS NULL OR max_score > 0) "
            "AND (score IS NULL OR max_score IS NULL OR score <= max_score)",
            name="ck_mock_attempt_score",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    type: Mapped[MockType] = mapped_column(Enum(MockType, name="mock_type"))
    taken_on: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluator_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
