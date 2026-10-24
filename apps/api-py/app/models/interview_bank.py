"""Admin-authored interview questions, per track and phase - the question bank
the free-style interviewer weaves in.

THE INTERVIEWER STAYS FREE-STYLE. app/interview_matrix.py's Specialization
already carries `question_bank`, and build_instructions renders it as "a guide
to coverage, not a script: rephrase each naturally, follow up on what the
student actually says". Until now only the voice platform filled that field,
from its own UG/PG catalogue; the interviewer students actually use ran with it
empty. These rows fill it for the four live tracks. Nothing about the phase
machine, the persona or the word gate changes: an admin decides WHAT gets
covered, the model decides HOW it is asked.

`track` and `phase` are plain strings. **The reason given here used to be "the
catalogue of tracks is code", and B5.1 has made that false** — the catalogue is
`interview_tracks` now, a table an admin edits. The columns stay plain strings
anyway, and the reason is better than the one it replaces:

  * `track` is the STABLE CODE (`hr`/`dm`/`ba`/`fa`), which is what the student's
    `?specialization=` carries, what `interview_sessions.specialization` has
    already recorded on every interview ever held, and what
    `question_bank_for(track_code)` and the router's `_check_track` key on. It is
    not a denormalised copy of `interview_tracks.code` waiting to drift: it is
    the identifier, and `track_id` below is the referential join added beside it.
  * `phase` is still `InterviewPhase`, which genuinely is code — the arc is the
    state machine's, not the office's.
  * a PG enum on either would turn a fifth track into a type migration, which is
    exactly the regression `interview_tracks` exists to end
    (docs/interview-engine-v3.md §6.1).

`position` is the order the questions are worked in; the prompt says "in this
order", so it is load-bearing, not cosmetic.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class InterviewBankQuestion(Base):
    __tablename__ = "interview_bank_questions"
    __table_args__ = (Index("ix_interview_bank_track_position", "track", "position"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    track: Mapped[str] = mapped_column(String)  # a track CODE: hr / dm / ba / fa
    #: The track row this question belongs to (B5.2), beside the code and not
    #: instead of it — see the module docstring.
    #:
    #: SET NULL, and that is the interesting half. A deleted track must not take
    #: the office's questions with it: the questions are the expensive thing on
    #: this screen, a track is four fields, and `question_bank_for('hr')` keys on
    #: the CODE, so a question whose track row is gone keeps working the moment
    #: somebody recreates the track. A CASCADE here would make deleting a track
    #: a silent bulk delete of staff-authored text with no confirmation naming
    #: what was lost.
    track_id: Mapped[str | None] = mapped_column(
        ForeignKey("interview_tracks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Which college's bank this is (B5.2). NULL is programme-wide — the reading
    #: `jobs.college_id` and `interview_tracks.college_id` already have, and the
    #: one every row written before Phase 4c keeps: narrowing them to a guessed
    #: college would empty the question bank for somebody.
    college_id: Mapped[str | None] = mapped_column(
        ForeignKey("colleges.id"), nullable=True, index=True
    )
    phase: Mapped[str] = mapped_column(String)  # opening / probing / deep_dive / wrap_up
    text: Mapped[str] = mapped_column(String)
    position: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    #: Audit stamp, a plain column like Registration.reviewed_by_id - not an FK,
    #: so deleting the author never deletes the question.
    created_by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
