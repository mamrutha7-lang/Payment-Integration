"""The Time Allocation Ledger — six slots covering a 24-hour day, five activity
heads, hours to the nearest half.

This is NOT a replacement for `time_sheet_entries` and does not read it. That
table answers "how many minutes of SKILLING has this student logged this week",
which the dashboard chart and the weekly target still ask; this one answers
"what did the 24 hours of Thursday actually look like, slot by slot". Merging
them would have meant either losing the slot dimension or inventing a fake slot
for every historical row, so they are separate tables with separate questions.

THE UNIT IS THE HALF HOUR, STORED AS AN INTEGER. The design specifies hours to
the nearest half, and the day must reconcile to exactly 24 h before it may be
submitted. A float column makes that check a game of epsilons — 0.1 + 0.2 style
drift across thirty cells is enough to leave a perfectly filled day sitting at
23.999999 and refusing to submit, with nothing on screen to explain it. Integers
of half-hours make "does this add to 24" an exact comparison against 48, and the
API converts at the edge so the client still speaks in hours.

Every cell is bounded twice, and both bounds live in `SLOT_CAPACITY_HALVES`
below: no single cell may exceed its slot's capacity, and no slot's five cells
may sum past it either. A 4-hour slot holding 5 hours of lectures is not a
rounding problem, it is a data-entry error that would silently make the day
totals meaningless.
"""

import enum
import uuid
from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .timesheet import DayActivity


def _uuid() -> str:
    return uuid.uuid4().hex


class LedgerSlot(str, enum.Enum):
    """The six slots, chronological from 5 am to 5 am.

    The day deliberately starts at 5 am rather than midnight: the last slot then
    contains the whole of a normal night's sleep as ONE block instead of
    splitting it across two calendar days, which is what makes "8.0 h against an
    8 h benchmark" a number a student can actually enter.
    """

    DAWN = "DAWN"  # 5:00 – 9:00 am
    MORNING = "MORNING"  # 9:00 am – 12:00 pm
    MIDDAY = "MIDDAY"  # 12:00 – 3:00 pm
    AFTERNOON = "AFTERNOON"  # 3:00 – 6:00 pm
    EVENING = "EVENING"  # 6:00 – 10:00 pm
    NIGHT = "NIGHT"  # 10:00 pm – 5:00 am


class LedgerDayStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"


# Capacity in HALF HOURS, and the single source of truth for it. The API derives
# the day band's flex weights from this same table, so a slot's width on screen
# can never disagree with the hours it accepts.
SLOT_CAPACITY_HALVES: Final[dict[LedgerSlot, int]] = {
    LedgerSlot.DAWN: 8,  # 4 h
    LedgerSlot.MORNING: 6,  # 3 h
    LedgerSlot.MIDDAY: 6,  # 3 h
    LedgerSlot.AFTERNOON: 6,  # 3 h
    LedgerSlot.EVENING: 8,  # 4 h
    LedgerSlot.NIGHT: 14,  # 7 h
}

#: The whole day, in half hours. Asserted rather than written as a literal so
#: that editing one slot's capacity without fixing another is a boot-time
#: failure here, not a mystery on the submit button months later.
DAY_CAPACITY_HALVES: Final[int] = sum(SLOT_CAPACITY_HALVES.values())
assert DAY_CAPACITY_HALVES == 48, "the six slots must cover exactly 24 hours"

#: The per-cell bound as the DATABASE enforces it — the same table above,
#: rendered as a CHECK, so the router is no longer the only thing standing
#: between a cell and a value of 999. Derived rather than typed beside the
#: dict: the two cannot disagree, and tests/test_codebase_guards.py pins it.
#: `ELSE -1` is deliberate. A CASE with no matching arm yields NULL, a NULL
#: comparison is not FALSE, and a CHECK that is not FALSE passes — so without
#: it a slot this CASE does not know would be UNBOUNDED, silently. With it,
#: every insert for an unknown slot fails, which is the loud failure a new
#: slot deserves: adding one is an enum migration anyway, and this makes
#: forgetting to widen the constraint part of the same afternoon.
LEDGER_CELL_HALF_HOURS_CHECK: Final[str] = (
    "half_hours >= 0 AND half_hours <= (CASE slot "
    + " ".join(f"WHEN '{slot.value}' THEN {cap}" for slot, cap in SLOT_CAPACITY_HALVES.items())
    + " ELSE -1 END)"
)

#: Slot display, in the order the ledger renders. Kept beside the capacities so
#: a new slot cannot be added to one and forgotten in the other.
SLOT_LABEL: Final[dict[LedgerSlot, str]] = {
    LedgerSlot.DAWN: "5:00 – 9:00 am",
    LedgerSlot.MORNING: "9:00 am – 12:00 pm",
    LedgerSlot.MIDDAY: "12:00 – 3:00 pm",
    LedgerSlot.AFTERNOON: "3:00 – 6:00 pm",
    LedgerSlot.EVENING: "6:00 – 10:00 pm",
    LedgerSlot.NIGHT: "10:00 pm – 5:00 am",
}

#: The leading glyph the dawn and night rows carry. Absent means no icon.
SLOT_ICON: Final[dict[LedgerSlot, str]] = {
    LedgerSlot.DAWN: "wb_twilight",
    LedgerSlot.NIGHT: "bedtime",
}

#: Tick labels under the day band — the START of each slot.
SLOT_TICK: Final[dict[LedgerSlot, str]] = {
    LedgerSlot.DAWN: "5 am",
    LedgerSlot.MORNING: "9 am",
    LedgerSlot.MIDDAY: "12 pm",
    LedgerSlot.AFTERNOON: "3 pm",
    LedgerSlot.EVENING: "6 pm",
    LedgerSlot.NIGHT: "10 pm",
}

#: The five heads, in column order, with the swatch the legend and the mix bar
#: both draw. Colour lives here rather than only in the stylesheet because the
#: composition bars are built from server-computed proportions — splitting the
#: two halves across two files is how a legend ends up disagreeing with the bar
#: sitting next to it.
ACTIVITY_ORDER: Final[tuple[DayActivity, ...]] = (
    DayActivity.SLEEPING,
    DayActivity.LEISURE,
    DayActivity.LECTURES,
    DayActivity.COURSEWORK,
    DayActivity.SKILLING,
)

ACTIVITY_LABEL: Final[dict[DayActivity, str]] = {
    DayActivity.SLEEPING: "Sleep",
    DayActivity.LEISURE: "Travel / personal",
    DayActivity.LECTURES: "Lectures",
    DayActivity.COURSEWORK: "Coursework",
    DayActivity.SKILLING: "Skilling",
}

ACTIVITY_COLOUR: Final[dict[DayActivity, str]] = {
    DayActivity.SLEEPING: "#b9a8c9",
    DayActivity.LEISURE: "#d9c8e6",
    DayActivity.LECTURES: "#7a2f9e",
    DayActivity.COURSEWORK: "#552C7E",
    DayActivity.SKILLING: "#BA2185",
}

#: The three heads that count as productive on the metrics strip
#: ("Lectures · coursework · skilling"). Named so the metric and its own
#: sub-caption cannot drift apart.
PRODUCTIVE: Final[frozenset[DayActivity]] = frozenset(
    {DayActivity.LECTURES, DayActivity.COURSEWORK, DayActivity.SKILLING}
)


class TimeLedgerDay(Base):
    """One student's ledger for one calendar day.

    The row exists as soon as anything is typed into it, in DRAFT. SUBMITTED is
    a one-way latch set by `POST /api/student/ledger/submit`, and only once the
    day reconciles to 24 h — which is the whole reason the "0.5 h to reconcile"
    line exists on screen.
    """

    __tablename__ = "time_ledger_days"
    __table_args__ = (
        UniqueConstraint("student_id", "day", name="uq_ledger_day"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    status: Mapped[LedgerDayStatus] = mapped_column(
        Enum(LedgerDayStatus, name="ledger_day_status"),
        default=LedgerDayStatus.DRAFT,
        server_default="DRAFT",
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    cells: Mapped[list["TimeLedgerCell"]] = relationship(
        back_populates="ledger_day", cascade="all, delete-orphan"
    )


class TimeLedgerCell(Base):
    """One (slot, activity) figure, in half hours.

    A cell is written only when it is non-zero. An absent row and a row holding 0
    mean the same thing to every reader, and keeping thirty rows per day per
    student — most of them zero — for a table that grows by one row per student
    per day forever is a lot of storage to say nothing.
    """

    __tablename__ = "time_ledger_cells"
    __table_args__ = (
        UniqueConstraint("ledger_day_id", "slot", "activity", name="uq_ledger_cell"),
        CheckConstraint(LEDGER_CELL_HALF_HOURS_CHECK, name="ck_ledger_cell_half_hours"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ledger_day_id: Mapped[str] = mapped_column(
        ForeignKey("time_ledger_days.id", ondelete="CASCADE")
    )
    slot: Mapped[LedgerSlot] = mapped_column(Enum(LedgerSlot, name="ledger_slot"))
    # Reuses the EXISTING day_activity type. The migration must therefore declare
    # it with create_type=False (AGENTS.md, Alembic enum gotcha b) — autogenerate
    # emits a bare sa.Enum here and the upgrade dies on "type already exists".
    activity: Mapped[DayActivity] = mapped_column(Enum(DayActivity, name="day_activity"))
    half_hours: Mapped[int] = mapped_column(Integer)

    ledger_day: Mapped[TimeLedgerDay] = relationship(back_populates="cells")
