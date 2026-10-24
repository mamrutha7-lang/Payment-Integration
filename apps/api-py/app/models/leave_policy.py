"""What a person is entitled to, and which days the college does not count
(B10.2). Two tables, and they answer two different questions.

`leave_balances` is a PERSON's allowance for one kind of leave in one academic
year. `academic_calendar` is the COLLEGE's list of days that are not working
days — which is what turns "20 Dec to 10 Jan" into a number of days worth
deducting from that allowance. Neither is a decision about a single request;
both are the reference data the submit path reads.

==============================================================================
`kind` IS THE FIVE OPTIONS THE PAPER PRINTS, AND THE SET IS CLOSED
==============================================================================

CASUAL / PERMISSION / OOD / RH / LOP. That list is not a policy choice this
codebase gets to extend: it is PRINTED on the college's own leave form
(`app/assets/leave_form_template.pdf`), and `app/leave_paper.py::OPTIONS` holds
a measured x-range for each one so the four that do not apply can be struck
through. A sixth kind would have nowhere to be struck, would print as a blank
"Application for" line, and would need a new template with new coordinates —
i.e. a new form from the office, not a migration. `LeaveIn.leave_kind`'s
`pattern="^(CASUAL|PERMISSION|OOD|RH|LOP)$"` is the same set stated a third
time at the API edge.

It is a plain `String` with a CHECK built from `PRINTED_LEAVE_KINDS` below —
not a PG enum. AGENTS.md's enum gotchas (a) and (b) both apply to a vocabulary
shared by two tables, and the house rule since `Message.channel` is a String
with the vocabulary in Python. The CHECK is BUILT from the tuple rather than
typed out beside it, for `time_ledger`'s reason: two copies of a list is how a
widened set is accepted on screen and refused on INSERT.

==============================================================================
THE UNIT IS A WHOLE DAY, AND AN INTEGER
==============================================================================

A leave request carries two DATES and nothing finer, so the span it consumes is
an integer number of days by construction; there is no half-day anywhere in this
product to represent. Integers also make "does this request fit in the balance"
an exact comparison, which is the `time_ledger` lesson written down: a float
column turns a perfectly filled allowance into 11.999999 and refuses it with
nothing on screen to explain why. If half-days ever arrive, follow
`time_ledger`'s half-hours — store the halves as integers — never a float.

==============================================================================
`academic_year` IS A NEW SPELLING, AND IT IS DECLARED HERE ON PURPOSE
==============================================================================

Nothing in this codebase had one. `cohorts.batch_label` is "2024-26" (a cohort's
whole programme, not a year), `students.current_semester` is a bare position with
no year attached, and Phase 4a's promotion history records semesters rather than
years. So this column defines the spelling: a plain `String` holding the
college's own label for the academic year, "2026-27". A String and not an
Integer because an Indian academic year straddles two calendar years and
"2026" is ambiguous about which; a String and not a derived value because the
office decides when its year turns over and no date arithmetic here can know.

==============================================================================
THE CALENDAR IS A COLLEGE CATALOGUE
==============================================================================

It names no person. Somebody in the office types in next year's holidays once
and every intake after that reads them — exactly like `colleges`, `departments`
and `approved_certifications`, and it is KEPT by both destructors for that
reason (`app/purge_people.py`, `app/purge_students.py`). Emptying it when a
cohort leaves would silently change how every subsequent leave request is
counted.

Two kinds, and the second is not redundant: `holiday` is a day the college is
closed, `working` is a day it is OPEN that would otherwise be assumed shut — the
Saturday class, the term day on a public holiday. Without the second value a
calendar can only ever subtract, and a college that works alternate Saturdays
cannot be described at all.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
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


#: The five options PRINTED on the college's leave form. Fixed by the paper —
#: see the module docstring before adding a sixth. `app/leave_paper.py::OPTIONS`
#: holds the measured strike-through range for each of these keys, and
#: `LeaveIn.leave_kind` states the same set at the API edge.
PRINTED_LEAVE_KINDS: tuple[str, ...] = ("CASUAL", "PERMISSION", "OOD", "RH", "LOP")

#: Built FROM the tuple above, never typed out beside it. Two copies of a
#: vocabulary is how a widened set is accepted by the schema and refused by the
#: database — `time_ledger.LEDGER_CELL_HALF_HOURS_CHECK` is the same idiom.
LEAVE_BALANCE_KIND_CHECK = "kind IN (" + ", ".join(f"'{k}'" for k in PRINTED_LEAVE_KINDS) + ")"

#: A day the college is closed, and a day it is open that would otherwise be
#: assumed closed. See the module docstring for why the second one earns a row.
CALENDAR_HOLIDAY = "holiday"
CALENDAR_WORKING = "working"
CALENDAR_KINDS: tuple[str, ...] = (CALENDAR_HOLIDAY, CALENDAR_WORKING)
ACADEMIC_CALENDAR_KIND_CHECK = "kind IN (" + ", ".join(f"'{k}'" for k in CALENDAR_KINDS) + ")"


class LeaveBalance(Base):
    """One person's allowance of one kind of leave in one academic year.

    KEYED ON `users.id`, NOT `students.id`, AND THAT IS THE POINT. Both roles
    apply on this form — `purge_students` scopes `leave_requests` by
    `requester_user_id` for exactly that reason — and a faculty member's casual
    leave allowance is the one the office is most often asked about. A
    `students` key would have made staff balances unrepresentable and pushed
    them into a second table.

    A MISSING ROW IS NOT A ZERO BALANCE. Nothing seeds this table, and a
    deployment that never opens the policy screen has no rows at all; that state
    must read as "no allowance has been recorded", never as "you have none
    left" — which is `interview_policies`' compatibility rule and the English
    Baseline's nullable-score rule applied here. The submit path must branch on
    the row's ABSENCE, not on `entitled_days == 0`, because an entitlement of
    zero is a real and different decision (LOP, typically).
    """

    __tablename__ = "leave_balances"
    __table_args__ = (
        # One row per person per kind per year. Without this a second "CASUAL
        # 2026-27" row for the same account makes the balance whichever one the
        # planner returned. `user_id` LEADS it, which is also this table's FK
        # index.
        UniqueConstraint(
            "user_id", "kind", "academic_year", name="uq_leave_balance_user_kind_year"
        ),
        CheckConstraint(LEAVE_BALANCE_KIND_CHECK, name="ck_leave_balance_kind"),
        # Neither number can be negative. `consumed_days > entitled_days` is
        # deliberately NOT refused: leave granted past an allowance happens, the
        # office signs it, and a CHECK that refused it would make the console
        # unable to record a decision a human already made.
        CheckConstraint(
            "entitled_days >= 0 AND consumed_days >= 0", name="ck_leave_balance_days"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    #: CASCADE: a balance is meaningless without the account it belongs to, and
    #: `purge_people` empties this table anyway.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    #: One of `PRINTED_LEAVE_KINDS`. A String with a CHECK, never a PG enum.
    kind: Mapped[str] = mapped_column(String(20))
    #: The college's own label, e.g. "2026-27". See the module docstring.
    academic_year: Mapped[str] = mapped_column(String(16))

    #: Whole days granted for the year. See the module docstring on the unit.
    entitled_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Whole days already taken. Maintained by the leave workflow; it is a
    #: RUNNING TOTAL and not derived from `leave_requests` on read, because the
    #: two must be allowed to disagree — an allowance carried over, an
    #: adjustment the office made by hand, a request approved before this table
    #: existed. The console edits it; nothing recomputes it behind the office's
    #: back.
    consumed_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AcademicCalendarDay(Base):
    """One college-level day that is not an ordinary working day.

    A CATALOGUE, KEPT BY BOTH DESTRUCTORS. It names nobody and outlives every
    intake; see the module docstring.
    """

    __tablename__ = "academic_calendar"
    __table_args__ = (
        # One verdict per college per day. A second row for 15 August would make
        # "is this a working day" depend on which row was read.
        UniqueConstraint("college_id", "day", name="uq_academic_calendar_college_day"),
        CheckConstraint(ACADEMIC_CALENDAR_KIND_CHECK, name="ck_academic_calendar_kind"),
        # `college_id` leads the unique constraint above, which serves its FK
        # lookup. This one is for the creator column.
        Index("ix_academic_calendar_created_by", "created_by_user_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    #: No `ondelete`: the institutional spine's convention. The database REFUSES
    #: to delete a college that still has a calendar, the way it refuses one
    #: that still has departments.
    college_id: Mapped[str] = mapped_column(ForeignKey("colleges.id"))
    #: The calendar day itself. Named `day` and not `date`: the column would
    #: otherwise shadow `datetime.date` in this module's own annotations, and a
    #: reader of `AcademicCalendarDay.date` cannot tell the field from the type.
    #: 04-backend-changes.md spells it `date`; this is the same column.
    day: Mapped[date] = mapped_column(Date)
    #: `holiday` or `working` — see the module docstring on why the second one
    #: is not redundant. A String with a CHECK, never a PG enum.
    kind: Mapped[str] = mapped_column(String(16), default=CALENDAR_HOLIDAY)
    #: What to call it on screen: "Independence Day", "Compensatory working
    #: Saturday". Nullable, because a date the office simply blocked out needs
    #: no name and a required field here would make the console invent one.
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: Who typed it in, for the console's audit view. Nullable: a seed and the
    #: CLIs have no user.
    #:
    #: ON DELETE SET NULL, and this is a deliberate departure from the four
    #: spine tables, which declare `created_by_user_id` with no ON DELETE and
    #: are listed in `purge_people.CREATED_BY_COLUMNS` so a pass can null them
    #: before the accounts go. That mechanism works, and its guard does not:
    #: `test_the_delete_order_survives_every_foreign_key` only catches a missing
    #: entry when there is actually a row pointing at a doomed account, and a
    #: calendar is empty on every development database. SET NULL makes the same
    #: invariant a property of the schema rather than of a list somebody has to
    #: remember to edit — `mentor_assignments.by_user_id` is the recent
    #: precedent and says so in the same words.
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
