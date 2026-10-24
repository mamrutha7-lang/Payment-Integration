"""The English Proficiency Baseline — a CEFR-aligned, AI-scored assessment taken
once per semester during the Reboot stage.

Two tables, for the reason the interview scorecard is two: the ATTEMPT is a fact
that exists as soon as a student starts, while a SECTION SCORE may legitimately
not exist yet. Speaking is scored by a human-in-the-loop step that runs after the
other three, so "3 of 4 sections scored · Speaking pending" is the normal state
of a healthy attempt, not a broken one.

EVERY SCORE IS NULLABLE, and that is load-bearing rather than lazy. A pending
speaking section and a speaking section scored 0 mean opposite things to the
student reading the screen and to the mentor deciding whether to intervene, so
they must not both be 0. The same reasoning the interview evaluation table
records for its own nullable scores applies here unchanged.

RULE 1 (AGENTS.md). Nothing in this module reaches a model. The scores are
written by whatever assessment pipeline produced them; this is the record it
lands in. If a future path ever sends a student's transcript or writing sample
to a provider to be scored, it must go through
`complete_chat(..., carries_student_data=True)` in app/ai/llm.py, because that
sample is the student's own words attached to their name.
"""

import enum
import uuid
from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class EnglishSkill(str, enum.Enum):
    READING = "READING"
    WRITING = "WRITING"
    LISTENING = "LISTENING"
    SPEAKING = "SPEAKING"


class SectionStatus(str, enum.Enum):
    SCORED = "SCORED"
    PENDING = "PENDING"


class BaselineStatus(str, enum.Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


#: Column order on screen, and the order the progress fraction counts in.
SKILL_ORDER: Final[tuple[EnglishSkill, ...]] = (
    EnglishSkill.READING,
    EnglishSkill.WRITING,
    EnglishSkill.LISTENING,
    EnglishSkill.SPEAKING,
)

SKILL_LABEL: Final[dict[EnglishSkill, str]] = {
    EnglishSkill.READING: "Reading",
    EnglishSkill.WRITING: "Writing",
    EnglishSkill.LISTENING: "Listening",
    EnglishSkill.SPEAKING: "Speaking",
}

SKILL_ICON: Final[dict[EnglishSkill, str]] = {
    EnglishSkill.READING: "menu_book",
    EnglishSkill.WRITING: "edit_note",
    EnglishSkill.LISTENING: "hearing",
    EnglishSkill.SPEAKING: "record_voice_over",
}

#: The CEFR ladder, weakest first. `band_label` on the attempt is derived from
#: this rather than stored twice — a row saying "B1+ / Proficient user" is a row
#: that will be quoted at somebody.
CEFR_LABEL: Final[dict[str, str]] = {
    "A1": "Beginner",
    "A2": "Elementary",
    "B1": "Independent user",
    "B1+": "Independent user",
    "B2": "Independent user",
    "C1": "Proficient user",
    "C2": "Proficient user",
}


class EnglishBaseline(Base):
    """One attempt. One per student per semester is the programme rule; the
    unique index is on (student, semester) so a second attempt is a conflict the
    database refuses rather than a duplicate row two screens disagree about."""

    __tablename__ = "english_baselines"
    __table_args__ = (
        UniqueConstraint("student_id", "semester", name="uq_english_baseline_semester"),
        CheckConstraint(
            "overall_score IS NULL OR overall_score BETWEEN 0 AND 100",
            name="ck_english_baseline_score",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    semester: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[BaselineStatus] = mapped_column(
        Enum(BaselineStatus, name="english_baseline_status"),
        default=BaselineStatus.IN_PROGRESS,
        server_default="IN_PROGRESS",
    )

    # NULL until enough sections are scored to place a band. Provisional while
    # `status` is IN_PROGRESS — the screen says "Provisional band" for exactly
    # this reason.
    overall_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    band: Mapped[str | None] = mapped_column(String, nullable=True)

    taken_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    # The AI feedback block. Lists of plain strings; `next_steps` is a list of
    # {title, sub, target} where `target` is a route key the card links through
    # to (the design sends all three to Skilling).
    strengths: Mapped[list | None] = mapped_column(JSONB, default=list, nullable=True)
    focus_areas: Mapped[list | None] = mapped_column(JSONB, default=list, nullable=True)
    next_steps: Mapped[list | None] = mapped_column(JSONB, default=list, nullable=True)

    # Whether "Download report" is offered. A plain flag, not a derived one: the
    # PDF is produced by the assessment pipeline and may lag the scores.
    report_available: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sections: Mapped[list["EnglishBaselineSection"]] = relationship(
        back_populates="baseline", cascade="all, delete-orphan"
    )


class EnglishBaselineSection(Base):
    """One skill within an attempt.

    `subscores` is a JSONB list of {label, value} — Reading's "Skimming &
    scanning / Inference / Academic vocabulary" and its three peers. They are
    presentation-shaped and differ per skill, which is exactly the case JSONB is
    for: three more nullable integer columns named after this year's rubric would
    become dead weight the first time the rubric changed.
    """

    __tablename__ = "english_baseline_sections"
    __table_args__ = (
        UniqueConstraint("baseline_id", "skill", name="uq_english_section"),
        CheckConstraint(
            "(score IS NULL OR score BETWEEN 0 AND 100) AND minutes >= 0",
            name="ck_english_section_score",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    baseline_id: Mapped[str] = mapped_column(
        ForeignKey("english_baselines.id", ondelete="CASCADE")
    )
    skill: Mapped[EnglishSkill] = mapped_column(Enum(EnglishSkill, name="english_skill"))
    status: Mapped[SectionStatus] = mapped_column(
        Enum(SectionStatus, name="english_section_status"),
        default=SectionStatus.PENDING,
        server_default="PENDING",
    )

    # Both NULL while PENDING. See the module docstring: a pending section and a
    # zero-scored one are different facts.
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    band: Mapped[str | None] = mapped_column(String, nullable=True)

    minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    subscores: Mapped[list | None] = mapped_column(JSONB, default=list, nullable=True)
    ai_report: Mapped[str | None] = mapped_column(Text, nullable=True)

    baseline: Mapped[EnglishBaseline] = relationship(back_populates="sections")
