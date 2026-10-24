"""Student profile — the editable student record (ported from Prisma
`StudentProfile`). Kept in its own module as the domain schema grows.

No ORM relationship back to Student is declared on purpose: callers resolve a
profile by student_id directly, which keeps the cross-module mapper simple.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), unique=True
    )

    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String, nullable=True)
    github_url: Mapped[str | None] = mapped_column(String, nullable=True)
    portfolio_url: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    career_summary: Mapped[str | None] = mapped_column(String, nullable=True)

    # Placement policy: placement_eligible is ADMIN-set (read-only to the
    # student); the interest flags are the student's own.
    placement_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    interested_in_jobs: Mapped[bool] = mapped_column(Boolean, default=True)
    interested_in_internships: Mapped[bool] = mapped_column(Boolean, default=True)

    # JSON blobs (shape as in the Prisma model's doc comments).
    education: Mapped[list] = mapped_column(JSONB, default=list)
    experience: Mapped[list] = mapped_column(JSONB, default=list)
    projects: Mapped[list] = mapped_column(JSONB, default=list)
    skills: Mapped[list] = mapped_column(JSONB, default=list)
    achievements: Mapped[list] = mapped_column(JSONB, default=list)

    photo_upload_id: Mapped[str | None] = mapped_column(String, nullable=True)
    leaderboard_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
