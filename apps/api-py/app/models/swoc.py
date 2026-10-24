"""SWOC board — strengths / weaknesses / opportunities / challenges, one entry
per observation, attributed to a viewpoint (ported from Prisma `SwocEntry`).
The board is deliberately un-averaged: disagreement between viewpoints is itself
the finding, so every entry keeps its source.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class SwocSource(str, enum.Enum):
    PLACEMENT = "PLACEMENT"
    MENTOR = "MENTOR"
    PM = "PM"


class SwocKind(str, enum.Enum):
    STRENGTH = "STRENGTH"
    WEAKNESS = "WEAKNESS"
    OPPORTUNITY = "OPPORTUNITY"
    CHALLENGE = "CHALLENGE"


class SwocEntry(Base):
    __tablename__ = "swoc_entries"
    __table_args__ = (
        Index("ix_swoc_student_kind", "student_id", "kind"),
        Index("ix_swoc_student_source", "student_id", "source"),
        CheckConstraint("weight >= 1 AND weight <= 5", name="ck_swoc_weight_range"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    source: Mapped[SwocSource] = mapped_column(Enum(SwocSource, name="swoc_source"))
    kind: Mapped[SwocKind] = mapped_column(Enum(SwocKind, name="swoc_kind"))
    text: Mapped[str] = mapped_column(String)
    # 1-5, how strongly the author holds this; orders a quadrant.
    weight: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    author_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ---------------------------------------------------------------- B7.3 --
    #: When it was last edited. `server_default=func.now()` rather than NULL on
    #: existing rows, and the difference matters: a NULL here would make "never
    #: edited" and "edited, we do not know when" the same value, and the screen
    #: that draws "edited {{ date }}" would have to guess which it was looking
    #: at. Equal to `recorded_at` means "as written"; later means "edited then".
    #: NO `onupdate=func.now()`, and that is not an oversight. SQLAlchemy fires
    #: `onupdate` on ANY update of the row, so the student pressing "I have read
    #: this" would stamp `updated_at` and the admin board would report the line
    #: as edited — by nobody, with no revision behind it. The edit path sets
    #: this column explicitly, which is the only write that means "edited".
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ---------------------------------------------------------------- B7.4 --
    #: The student's semester AT THE MOMENT THIS WAS WRITTEN, stamped by the
    #: write path and never recomputed — the whole point is that a line written
    #: in semester 2 stays a semester-2 line after the student is promoted.
    #:
    #: NULLABLE, AND EVERY ROW WRITTEN BEFORE B7.4 KEEPS A NULL. There is no
    #: semester history to backfill from — `students.current_semester` is a bare
    #: position with no change record, `semester_results` exists only for
    #: semesters whose marks were entered, and `redesign_audit_events` only
    #: covers changes made through the console since that endpoint shipped.
    #: Backfilling today's semester onto every historical row would claim last
    #: year's weakness was recorded this term. The semester view renders NULL as
    #: "semester not recorded", which is the one reading that cannot lie.
    #:
    #: An Integer and not an enum, so AGENTS.md's gotcha (a) — adding an enum
    #: COLUMN to an existing table does not CREATE TYPE — never fires here.
    semester: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---------------------------------------------------------------- B7.5 --
    #: When the STUDENT said they had read it. NULL means not acknowledged, and
    #: the student's own screen is the only writer.
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---------------------------------------------------------------- B7.6 --
    #: What this observation is ABOUT, when the author could name it.
    #:
    #: `linked_skill_id` points at `student_skills`, NOT at the `skills`
    #: catalogue, and 04 is ambiguous where the difference is the whole value:
    #: "weak on SQL" as a catalogue reference is a statement about SQL, while as
    #: a per-student row it is a statement about THIS student's SQL — which is
    #: what a SWOC line is, and what a readiness action can be built from.
    #:
    #: All three are SET NULL rather than CASCADE: deleting a job posting must
    #: not silently delete a mentor's written observation about a student.
    linked_skill_id: Mapped[str | None] = mapped_column(
        ForeignKey("student_skills.id", ondelete="SET NULL"), nullable=True, index=True
    )
    linked_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    linked_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )


class SwocEntryRevision(Base):
    """What one SWOC line said before an edit, and what it said after — B7.3.

    WHY A TABLE WHEN `redesign_audit_events` ALREADY HAS THIS. Every PATCH here
    has written a full before/after snapshot to the audit trail since the
    endpoint shipped, so the DATA exists and is retroactive. What does not exist
    is a way for the people who write this board to READ it:
    `routers/audit.py`'s gate is Main-Admin-only, with a written argument for
    why it is not scoped, and the SWOC board is held by granted faculty. A
    history button that answers 403 for everybody who can press it is not a
    feature.

    So this is the scoped copy, and it starts empty. Every line written before
    Phase 4 shows "no earlier version recorded" rather than a fabricated one;
    the Main Admin can still read the older history through `GET
    /api/admin/audit` where it has always been.

    THE SNAPSHOTS ARE JSONB, NOT COLUMNS, and that is what keeps AGENTS.md's
    enum gotcha (b) from firing: a `kind`/`source` column here would reuse
    `swoc_kind`/`swoc_source`, autogenerate would emit a bare `sa.Enum`, and the
    migration would fail with "type already exists". It is also the precedent
    `redesign_mentor_notebook_entry_revisions` set for exactly this shape.
    """

    __tablename__ = "swoc_entry_revisions"
    __table_args__ = (
        Index("ix_swoc_revision_entry", "entry_id", "changed_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    #: CASCADE, unlike the links above: a revision of a deleted line is a
    #: revision of nothing, and the DELETE path already writes the final
    #: before-state to `redesign_audit_events`, which is where a deleted line's
    #: last words survive.
    entry_id: Mapped[str] = mapped_column(
        ForeignKey("swoc_entries.id", ondelete="CASCADE"), nullable=False
    )
    before: Mapped[dict] = mapped_column(JSONB, nullable=False)
    after: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: WHO edited. SET NULL: the edit outlives the editor's account.
    by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
