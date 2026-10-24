"""Mentor notes — a mentor's write-up of a meeting with a mentee (ported from
Prisma `MentorNote`). meeting_at (when it happened) is distinct from created_at
(when it was typed), since notes are often written up later.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class MentorAction(str, enum.Enum):
    NONE = "NONE"
    FLAGGED = "FLAGGED"
    NUDGE_SENT = "NUDGE_SENT"
    ONE_ON_ONE_SCHEDULED = "ONE_ON_ONE_SCHEDULED"


class MentorNote(Base):
    __tablename__ = "mentor_notes"
    __table_args__ = (
        Index("ix_mentornote_student_created", "student_id", "created_at"),
        Index("ix_mentornote_student_meeting", "student_id", "meeting_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    mentor_id: Mapped[str] = mapped_column(ForeignKey("mentors.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    note_text: Mapped[str] = mapped_column(String)
    # The student-facing Mentor Meeting Log renders "1:1 review · Cabin 3" above
    # each note. Both are nullable and both stay optional for the mentor: a note
    # typed without them is a real note, and the log falls back to the linked
    # action for its heading rather than showing an empty line.
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    linked_action: Mapped[MentorAction] = mapped_column(
        Enum(MentorAction, name="mentor_action"), default=MentorAction.NONE, server_default="NONE"
    )
    meeting_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Retracting a note is a SOFT delete, because the student has very likely
    # already read it: `/student/mentor-meetings` renders these, so removing one
    # is a mentor taking back words that were on a student's screen. A hard
    # DELETE made "what was said, and later withdrawn" unanswerable the moment
    # it ran, and nothing else in the system held a copy — the row IS the
    # record. Nullable, and NULL means live; every read filters on
    # `deleted_at IS NULL`, and `db.get()` lookups must check the attribute
    # explicitly because a primary-key get takes no WHERE clause.
    #
    # No partial unique index is needed here, unlike Conversation
    # (models/conversation.py:56) and InterviewConsent (models/interview.py:502):
    # `mentor_notes` has no natural key and no one-live-row-per-owner rule, so a
    # retained row can never block a re-create. The two composite indexes stay
    # as they are — a student's notes number in the handful, so the extra
    # predicate is a cheap filter on an already-selective index scan.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
