"""Assistant answer feedback (Assistant V2 Phase D).

One thumbs-up / thumbs-down / report per (run, owner): a student rates the exact
assistant turn (``AgentRun``) they were shown, so the metrics endpoint can report
a real helpful/not-helpful signal instead of guessing from resolution alone.

Ownership is enforced at write time (the caller must own the ``AgentRun``); the
``uq_feedback_run_owner`` constraint makes a re-vote an UPDATE, never a duplicate.
The free-text note is PII-redacted (``app.redaction.redact_pii``) before it is
stored — the note is a product signal, not a place to accumulate student PII.

``feedbackrating`` is a NEW PG enum: its migration CREATEs the type before the
table (autogenerate emits a bare ``sa.Enum`` — hand-fixed, per AGENTS.md).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class FeedbackRating(str, enum.Enum):
    HELPFUL = "HELPFUL"
    NOT_HELPFUL = "NOT_HELPFUL"
    REPORT = "REPORT"


class AssistantFeedback(Base):
    __tablename__ = "assistant_feedback"
    __table_args__ = (
        # One feedback row per (run, owner) — a re-vote UPSERTs onto this row.
        UniqueConstraint("run_id", "owner_user_id", name="uq_feedback_run_owner"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    rating: Mapped[FeedbackRating] = mapped_column(
        Enum(FeedbackRating, name="feedbackrating")
    )
    note: Mapped[str | None] = mapped_column(String, nullable=True)  # PII-redacted
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
