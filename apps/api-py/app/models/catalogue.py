"""B13 — the two per-course catalogue tables.

The badge catalogue is CODE (`BADGES` in app/models/badge.py, 48 rows pinned by
tests/test_badges.py) and stays code: adding or retiring a badge is a change to
the programme's design, not a row somebody types. What a college genuinely needs
per course is narrower and is what lives here — which of those 48 badges apply
to an MBA as against an MCA, and which REEP stage a given semester sits in.

BOTH TABLES HANG OFF `academic_courses`, the admissions programme, never off
`courses` (the taught subject). A stage belongs to "semester 3 of the MBA", not
to 22MBA11.

ABSENCE MEANS ENABLED, in `badge_course_map`. That is the load-bearing choice in
this module: the alternative — a row per badge per course, absence meaning
disabled — makes the migration write 48 rows per course and makes a NEW course
start with no badges at all, silently, on the day somebody adds it. So the table
holds only the exceptions, and an empty table is exactly today's behaviour.

`stage_rules` IS OPTIONAL INPUT, NOT A REQUIREMENT. `POST /cohorts/{id}/promote`
applies a stage change only where a rule names one; an empty table makes the
stage half of a promotion a documented no-op, which is what lets B4.3 and B13
land independently of each other.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
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


class StageRule(Base):
    """"In this course, semester N is stage S." Read by B4.3's promotion."""

    __tablename__ = "stage_rules"
    __table_args__ = (
        # One stage per (course, semester) — two rules for one semester is a
        # contradiction the promotion would have to pick between silently.
        # It also LEADS WITH course_id, which is what indexes the foreign key
        # (tests/test_codebase_guards.py::test_every_foreign_key_column_is_indexed);
        # a second index=True here would be a duplicate of this one.
        UniqueConstraint("course_id", "semester", name="uq_stage_rule_course_semester"),
        CheckConstraint("semester >= 1", name="ck_stage_rule_semester"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # No `ondelete`, like every other pointer into the spine: the database
    # REFUSES to delete a course that still has rules under it. The console
    # archives a course; it never deletes one.
    course_id: Mapped[str] = mapped_column(ForeignKey("academic_courses.id"))
    semester: Mapped[int] = mapped_column(Integer)
    # Gotcha (b): `stage` already exists as a PG type (created by the original
    # schema, reused by courses.stage and approved_certifications.stage). A bare
    # sa.Enum here emits CREATE TYPE and the migration dies on "type already
    # exists"; create_type=False is the hand-fix, and the migration says so too.
    stage: Mapped[Stage] = mapped_column(Enum(Stage, name="stage", create_type=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BadgeCourseMap(Base):
    """A badge switched OFF (or explicitly on) for one course.

    `badge_code` is a plain String validated against `BADGE_BY_CODE` at the API
    edge, matching `ApprovedCertification.badge_code`'s stated convention — the
    catalogue is in code, so a foreign key would have nothing to point at.
    """

    __tablename__ = "badge_course_map"
    __table_args__ = (
        UniqueConstraint("course_id", "badge_code", name="uq_badge_course_map"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(ForeignKey("academic_courses.id"))
    badge_code: Mapped[str] = mapped_column(String)
    # Rows exist to say `false`. A `true` row is allowed and is a no-op, so that
    # switching a badge back on is an UPDATE an admin can see rather than a
    # DELETE that leaves no trace of the decision.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
