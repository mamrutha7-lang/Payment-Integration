"""Programme milestones — the three stage cards on the student landing screen.

THE CATALOGUE IS CODE; ONLY THE STATUS IS A ROW. Reboot / Excel / Elevate and
their items are the programme's structure, identical for every student in the
cohort, and changing it is a decision the placement office makes once — not
per-student data. Seeding fourteen rows per student to say "not started yet"
would put the programme's shape in the database in fourteen thousand places,
where a rename becomes a migration and a half-run migration becomes a cohort
whose landing screens disagree with each other.

So `MILESTONES` below is the structure, and `student_milestones` holds a row
only where a student has actually moved off the default. Absent means
NOT_STARTED, which is what a student who has just enrolled genuinely is.

One item is special: `english_baseline` is a LINK, not a checkbox — its row on
the Reboot card navigates to the English Baseline screen, and its status is
derived from the attempt rather than stored here. `derived_from` marks it so the
router knows not to trust a stored row for that key.
"""

import enum
import uuid
from datetime import datetime
from typing import Final, NamedTuple

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


class MilestoneStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


#: The status glyph, its colour role and its title attribute. Kept here so the
#: three surfaces that render a milestone (landing card, next-actions, the
#: assistant's grounded answer) cannot describe the same status three ways.
STATUS_GLYPH: Final[dict[MilestoneStatus, tuple[str, str, str]]] = {
    MilestoneStatus.COMPLETED: ("check_circle", "good", "Completed"),
    MilestoneStatus.IN_PROGRESS: ("pending", "warn", "In progress"),
    MilestoneStatus.NOT_STARTED: ("radio_button_unchecked", "neutral", "Not started yet"),
}


class Milestone(NamedTuple):
    key: str
    label: str
    #: Route the row navigates to, or None for a row that is a status only.
    route: str | None = None
    #: True when the status comes from another table rather than from
    #: `student_milestones`. See the module docstring.
    derived_from: str | None = None


class Stage(NamedTuple):
    key: str
    label: str
    items: tuple[Milestone, ...]


#: The programme, exactly as the three landing cards render it.
STAGES: Final[tuple[Stage, ...]] = (
    Stage(
        "reboot",
        "Reboot",
        (
            Milestone("ree_101", "REE 101"),
            Milestone("ree_102", "REE 102"),
            Milestone(
                "english_baseline",
                "English Baseline · AI",
                route="/student/english",
                derived_from="english_baselines",
            ),
        ),
    ),
    Stage(
        "excel",
        "Excel",
        (
            Milestone("peep_1", "PEEP 1"),
            Milestone("peep_2", "PEEP 2"),
            Milestone("vtu_1", "VTU 1"),
            Milestone("vtu_2", "VTU 2"),
        ),
    ),
    Stage(
        "elevate",
        "Elevate",
        (
            Milestone("hippo", "Hippo"),
            # "Specialization Cert" (`spec_cert`, routed to the Certification
            # Tracker at /student/certifications) was removed on 2026-09-17 at
            # the owner's request, screen and endpoint with it. Stale
            # `student_milestones` rows for that key are inert, as the class
            # docstring below promises.
            Milestone("mock_gds", "Mock GDS"),
            Milestone("mock_interview", "Mock Interview", route="/student/assistant"),
            Milestone("aptitude", "Aptitude training"),
        ),
    ),
)

#: Flat lookup, built once. A key that appears in two stages is a programme
#: definition bug, and this raises on import rather than silently keeping the
#: last one — the landing screen would otherwise show the item under the wrong
#: stage with no error anywhere.
MILESTONES: Final[dict[str, Milestone]] = {}
for _stage in STAGES:
    for _item in _stage.items:
        if _item.key in MILESTONES:
            raise RuntimeError(f"duplicate milestone key {_item.key!r} in the programme catalogue")
        MILESTONES[_item.key] = _item


class StudentMilestone(Base):
    """A student's status for ONE catalogue key.

    `key` is a plain string rather than an enum on purpose: the catalogue above
    is expected to change with the programme, and an enum would make adding
    "Mock GDS 2" a migration with a CREATE TYPE in it (AGENTS.md's enum gotcha
    (a)) rather than one line of Python. A key not in `MILESTONES` is ignored by
    the reader, so a removed item leaves stale rows that are inert rather than
    crashing the screen.
    """

    __tablename__ = "student_milestones"
    __table_args__ = (
        UniqueConstraint("student_id", "key", name="uq_student_milestone"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String)
    status: Mapped[MilestoneStatus] = mapped_column(
        Enum(MilestoneStatus, name="milestone_status"),
        default=MilestoneStatus.NOT_STARTED,
        server_default="NOT_STARTED",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
