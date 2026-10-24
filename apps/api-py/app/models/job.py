"""Jobs board — postings (`Job`) and self-reported applications
(`JobApplication`), ported from Prisma. required_skills is denormalised onto the
row (canonical Skill.slug values) so the match percentage is one query, not a
fan-out. Per-posting min_cgpa / max_live_backlogs override the default criteria.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ARRAY,
    Boolean,
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


def _uuid() -> str:
    return uuid.uuid4().hex


class DegreeLevel(str, enum.Enum):
    UG = "UG"
    PG = "PG"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("min_cgpa IS NULL OR min_cgpa BETWEEN 0 AND 10", name="ck_job_min_cgpa"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_ref: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String)
    company: Mapped[str] = mapped_column(String)
    degree_level: Mapped[DegreeLevel] = mapped_column(Enum(DegreeLevel, name="degree_level"))
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    apply_url: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, default="", server_default="")
    required_skills: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    posted_on: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closes_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Per-posting eligibility overrides; null => use the default criteria.
    min_cgpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_live_backlogs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ------------------------------------------------------------------ #
    # B12.1: WHERE A POSTING IS OFFERED, and B12.2: whether it still is.
    #
    # A NULL COLLEGE MEANS EVERY COLLEGE, and that is the compatible reading,
    # not an oversight. Every posting that existed before B12.1 has NULLs here
    # and every student could see it; a migration that attached them all to the
    # one college that happens to exist would make the second college onboarded
    # inherit an empty jobs board, silently, on deploy. The console sets the
    # college on a posting that is genuinely for one; the feed treats NULL as
    # "show it to everybody", which is exactly today's behaviour.
    #
    # No `ondelete` on either: the spine's convention is that the database
    # refuses to delete a rung that still has rows under it.
    # ------------------------------------------------------------------ #
    college_id: Mapped[str | None] = mapped_column(
        ForeignKey("colleges.id"), nullable=True, index=True
    )
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_courses.id"), nullable=True, index=True
    )
    #: Interview/specialization track CODES this posting is for, denormalised
    #: onto the row exactly as `required_skills` is above and for the same
    #: reason: the student feed's filter is then one predicate rather than a
    #: join table nobody else reads. EMPTY MEANS EVERY TRACK, matching the NULL
    #: college above — an empty list is not "no student qualifies".
    tracks: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    #: `open` or `closed` (B12.2). A PLAIN STRING, not a PG enum — `Message.
    #: channel` is the house precedent and a new state here must be a data
    #: change, not a CREATE TYPE migration carrying AGENTS.md's three gotchas.
    #:
    #: It sits BESIDE `closes_on`, which the client already derives a state
    #: from; which of the two wins is the router's decision (B12.2) and is
    #: written down there, not here. What the column adds is the case a date
    #: cannot express: a posting withdrawn by the recruiter this morning.
    status: Mapped[str] = mapped_column(String, default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint("student_id", "job_id", name="uq_job_application"),
        Index("ix_jobapp_job", "job_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    self_reported: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
