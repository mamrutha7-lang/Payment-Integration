"""The resume-builder profile — the free-form sections a student fills in that
have no other home in the schema (family, experience, internships, projects,
publications, seminars, positions of responsibility, references, career
objective, key expertise, achievements/awards, activities, contact extras, and
placement-policy preferences).

One row per student. The whole builder state is a single JSONB `data` blob: the
resume builder is presentational, per-student, and never queried by inner field,
so a blob keeps it flexible (new sections need no migration) and the completeness
score is computed on read. Structured domain facts the builder also shows —
semester results, certifications, uploads — are NOT copied here; the UI reads
those from their own endpoints (academics / certifications / uploads).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class ResumeProfile(Base):
    __tablename__ = "resume_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), unique=True
    )
    # The entire builder state (sections keyed by the mockup's step ids).
    data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
