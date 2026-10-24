"""Cohort — an admissions batch (ported from Prisma). degree_level (UG/PG) is an
admissions fact that gates which vacancies the cohort sees; it reuses the
existing `degree_level` PG enum (create_type=False).

`status` (B4.4) is the batch's own lifecycle and is a plain String for the
reason institution.py sets out; see the comment on the column.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .institution import STATUS_ACTIVE
from .job import DegreeLevel


def _uuid() -> str:
    return uuid.uuid4().hex


class Cohort(Base):
    __tablename__ = "cohorts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String, unique=True)  # e.g. MBA-2026-B
    name: Mapped[str] = mapped_column(String)
    batch_label: Mapped[str] = mapped_column(String)  # e.g. 2024-26
    # The department this batch belongs to, and through it the college — the two
    # levels the student's locked profile card needs and the schema had no way
    # to answer. NULLABLE because `cohorts` predates `departments`: existing
    # rows have no department to point at, and a NOT NULL here would make the
    # migration unrunnable on any database that already has cohorts. An admin
    # sets it; until then the card shows a dash rather than a guess.
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True, index=True
    )
    # ------------------------------------------------------------------ #
    # THE TWO OPTIONAL LEVELS, denormalised as ancestor pointers.
    #
    # WHY BOTH AND NOT JUST THE DEEPEST. A batch legitimately attaches at ANY
    # depth — department only, or course, or specialization — because the levels
    # are individually optional. One `specialization_id` cannot express
    # "attached at course level", and a polymorphic column cannot be a foreign
    # key. So both exist, beside `department_id`.
    #
    # WHY THAT IS NOT THREE SOURCES OF TRUTH. There is exactly ONE writer:
    # `_resolve_ancestry` in app/routers/admin.py. The client names only the
    # DEEPEST level it knows; the API walks UP the real foreign keys to fill in
    # the rest, and a shallower value the client also sent is CHECKED against
    # the derived one rather than stored beside it. A contradiction is a 422
    # naming both, never a silent pick — because the silent pick surfaces as a
    # profile card printing the wrong department under the words "verified by
    # Main Admin".
    #
    # WHY THIS IS NOT THE DENORMALISATION THE BUILD LOG FORBIDS. That rule is
    # about copying college/department onto `students` — thousands of rows,
    # where inserting a level becomes a backfill. Here `cohorts` numbers in the
    # dozens, and `department_id` staying always-set and always-correct is what
    # keeps every existing endpoint and its tests working untouched.
    #
    # NULLABLE PERMANENTLY. Requiredness lives in HIERARCHY_LEVELS
    # (app/models/institution.py), never in the schema. See that constant.
    # ------------------------------------------------------------------ #
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_courses.id"), nullable=True, index=True
    )
    specialization_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_specializations.id"), nullable=True, index=True
    )
    degree_level: Mapped[DegreeLevel] = mapped_column(
        Enum(DegreeLevel, name="degree_level", create_type=False)
    )
    # ------------------------------------------------------------------ #
    # THE BATCH'S OWN LIFECYCLE (B4.4). `cohorts` was the ONLY table on the
    # spine without one — College, Department, AcademicCourse and
    # AcademicSpecialization all carry `status` — so "this batch has graduated"
    # had nowhere to live and every roster kept counting it as current.
    #
    # A PLAIN STRING, NOT A PG ENUM, for institution.py's stated reason: a new
    # status value must be a data change, not a CREATE TYPE migration carrying
    # all three of AGENTS.md's enum gotchas. ACTIVE | DRAFT | ARCHIVED are the
    # three that table already uses; GRADUATED is this column's fourth.
    #
    # GRADUATED IS NOT ARCHIVED AND IS NOT EMPTY. The batch keeps its students
    # (their rows are the record of who was in it), so `DELETE /cohorts/{id}`
    # still refuses it — correctly. Emptying a batch is moving its students
    # out; graduating one is a statement about the people, not the row.
    # ------------------------------------------------------------------ #
    status: Mapped[str] = mapped_column(String, default=STATUS_ACTIVE, server_default=STATUS_ACTIVE)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
