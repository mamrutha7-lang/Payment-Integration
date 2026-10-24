"""Leave policy, read on the submit path (B10.2).

The tables are `app/models/leave_policy.py`; this is the reading of them, in one
place, so `app/routers/leave.py` gains a call and not six hundred lines and so
the balance CRUD screen (which does not exist yet) can spell an academic year
exactly the way the submit path looks one up.

==============================================================================
THE ABSENCE OF A BALANCE ROW IS THE DEFAULT, AND IT SWITCHES OFF BOTH CHECKS
==============================================================================

Nothing seeds `leave_balances`. A deployment that has never opened the policy
screen has no rows at all, and on such a deployment `submit_refusal` returns
None for every request — the form behaves EXACTLY as it did before this module
existed. That is `interview_policies`' rule applied here ("no row is seeded and
the absence of one IS the default"), and it is the whole reason this is a
lookup and not a validator.

It governs the OVERLAP check as well as the balance ceiling, and that deserves
saying out loud because overlap reads no allowance and could technically be
enforced without one. Two reasons it is not:

  * The refusals are one policy, not two. "You may not be on two leaves at once"
    and "you may not exceed your allowance" are both the office's rules about
    how much leave a person may take, and a product that starts enforcing half
    of them on the deploy that added a table — on the college's own form, which
    the owner asked not to change — is one that refuses a request today that it
    accepted yesterday with nothing on any screen to explain the difference.
  * An overlap is not always a mistake. A one-day CASUAL leave inside a longer
    OOD, a PERMISSION slip on a day already covered — the office signs those,
    and until it has told REEP what it is counting, REEP has no standing to
    refuse them. `tests/test_leave_dates.py` submits exactly that shape
    (`today..today`, then `today..today+3`) and asserts both are accepted.

So: the office records an allowance for a person and a kind of leave, and from
that moment REEP enforces the office's rule for that kind. Before that it
records what the applicant typed and lets a human decide.

==============================================================================
THE YEAR LABEL IS THE COLLEGE'S OWN STRING AND THIS ONLY EVER *FINDS* ONE
==============================================================================

`leave_balances.academic_year` is a String the office types ("2026-27"). Nothing
in this codebase can derive it: `cohorts.batch_label` is a whole programme,
`students.current_semester` carries no year, and when the year turns over is the
office's decision. `academic_year_for` produces the CONVENTIONAL spelling for a
date so that a lookup has something to ask for, and the balance screen should
pre-fill the same function's output so the two agree by construction.

WHEN THE LOOKUP MISSES, THERE IS NO CHECK. A college that spells its year
"2026-2027" gets no enforcement rather than a refusal computed against the wrong
row. That is the safe failure and the other one is not: consulting "whichever
balance row this person has" would quietly measure a September request against
LAST year's exhausted allowance and tell an applicant with a full entitlement
that they have none left. A silent miss is visible on the policy screen (the
row is sitting there with the other spelling); a wrong refusal is visible
nowhere.

==============================================================================
COUNTING DAYS: A DAY COUNTS UNLESS THE CALENDAR SAYS `holiday`
==============================================================================

No weekday/weekend rule is invented here, and that is deliberate. Nothing in
this product writes down which days of the week the college works — REEP has no
timetable — and the Angular form already computes its own "N day(s)" label from
the two dates alone. A server that silently subtracted Sundays would disagree
with the number the applicant was shown, on a refusal, with nothing on screen
reconciling them.

`academic_calendar` is the record that DOES know, and a `holiday` row is how the
office says "we are shut". A college that works alternate Saturdays marks the
Saturdays it is closed; that is what the row is for. `working` rows are the
office's positive record that a day is open (the Saturday class, the exam on a
public holiday) and are never subtracted — they exist so the office can say
"open" about a day somebody would otherwise assume shut, and so the screen can
show it.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models.leave import LeaveRequest, LeaveStatus
from .models.leave_policy import CALENDAR_HOLIDAY, AcademicCalendarDay, LeaveBalance
from .models.user import Student

#: The month an academic year starts in, for `academic_year_for` only. June is
#: the Indian convention and the one BGSCET's own batch labels follow; it is
#: used to SPELL a label, never to decide anything, so a college that starts in
#: July simply types its own label and this function's guess misses (see the
#: module docstring on why a miss is the safe failure).
ACADEMIC_YEAR_START_MONTH = 6

#: The statuses that make a request LIVE for the overlap check. A REJECTED or
#: CANCELLED request occupies no days: the whole point of B10.4's withdraw is
#: that it frees the dates back up, and a rejection that still blocked the
#: applicant from reapplying would be a refusal with no way out of it.
LIVE_STATUSES = (
    LeaveStatus.SUBMITTED,
    LeaveStatus.FIRST_APPROVED,
    LeaveStatus.APPROVED,
)


def academic_year_for(day: date) -> str:
    """The conventional label for the academic year containing `day`: "2026-27".

    Used to FIND a row the office wrote, and to pre-fill the field where the
    office writes one. See the module docstring — it is never stored by the
    submit path and a miss switches the check off rather than guessing.
    """
    start = day.year if day.month >= ACADEMIC_YEAR_START_MONTH else day.year - 1
    return f"{start}-{str(start + 1)[2:]}"


def balance_for(db: Session, user_id: str, kind: str, academic_year: str) -> LeaveBalance | None:
    """The one allowance row for this person, this kind and this year, or None.

    None means "the office has recorded no allowance", which is NOT a zero
    balance — `LeaveBalance`'s own docstring says so, and every caller here
    branches on the row's absence rather than on `entitled_days == 0`, because
    an entitlement of zero is a real and different decision.
    """
    return db.scalar(
        select(LeaveBalance).where(
            LeaveBalance.user_id == user_id,
            LeaveBalance.kind == kind,
            LeaveBalance.academic_year == academic_year,
        )
    )


def college_of_applicant(db: Session, user_id: str) -> str | None:
    """Which college's calendar governs this applicant's days.

    Both roles apply on this form, so both walks are needed, and neither is
    re-implemented: `governance.ancestry_of_student` reads a student's college
    through their batch OR their own department pointer (the bug that made a
    department-hung feature override miss every unseated student), and
    `ancestry_of_user` reads a faculty account's department and its college.
    Unfiled either way -> None -> no calendar -> every day counts.
    """
    from .governance import ScopeLevel, ancestry_of_student, ancestry_of_user

    student_id = db.scalar(select(Student.id).where(Student.user_id == user_id))
    pairs = (
        ancestry_of_student(db, student_id)
        if student_id
        else ancestry_of_user(db, user_id)
    )
    for level, target_id in pairs:
        if level == ScopeLevel.COLLEGE and target_id:
            return str(target_id)
    return None


def working_days(db: Session, college_id: str | None, from_date: date, to_date: date) -> int:
    """How many days of this span the college is open for.

    Every calendar day in the span, less the ones carrying a `holiday` row at
    this college. No weekend rule — see the module docstring. A span with no
    college, or at a college with an empty calendar, is its own length, which is
    the number the applicant was shown on the form.
    """
    span = (to_date - from_date).days + 1
    if span <= 0:
        return 0
    if not college_id:
        return span
    shut = db.scalars(
        select(AcademicCalendarDay.day).where(
            AcademicCalendarDay.college_id == college_id,
            AcademicCalendarDay.kind == CALENDAR_HOLIDAY,
            AcademicCalendarDay.day >= from_date,
            AcademicCalendarDay.day <= to_date,
        )
    ).all()
    return max(span - len({d for d in shut}), 0)


def overlapping_request(
    db: Session,
    user_id: str,
    from_date: date,
    to_date: date,
    *,
    exclude_id: str | None = None,
) -> LeaveRequest | None:
    """A LIVE request of this applicant's whose days touch [from_date, to_date].

    The applicant's OWN requests only. Two people wanting the same week is the
    normal state of a department and nothing here is a rota.

    Overlap is the standard interval test — `existing.from <= new.to AND
    existing.to >= new.from` — which is the one that gets "20-22 Dec" against
    "22-24 Dec" right; the intuitive pair of `between` checks misses the case
    where the new span CONTAINS the old one entirely.
    """
    query = select(LeaveRequest).where(
        LeaveRequest.requester_user_id == user_id,
        LeaveRequest.status.in_(LIVE_STATUSES),
        LeaveRequest.from_date <= to_date,
        LeaveRequest.to_date >= from_date,
    )
    if exclude_id:
        query = query.where(LeaveRequest.id != exclude_id)
    return db.scalars(query.order_by(LeaveRequest.from_date)).first()


def submit_refusal(
    db: Session,
    *,
    user_id: str,
    from_date: date,
    to_date: date,
    leave_kind: str | None,
) -> str | None:
    """The reason this request must be refused at submit time, or None.

    Returns a SENTENCE rather than raising, so the one place that turns a policy
    into an HTTP status is the endpoint — and so this is testable without a
    client.

    IT DOES NOT REWRITE `leave_kind`. 04 asks for a "balance check with LOP
    fallback", and a fallback implemented as a silent rewrite changes which of
    the five printed options is STRUCK THROUGH on the college's own form without
    the applicant asking for it: they apply for Casual Leave and collect a PDF
    saying Loss Of Pay. The refusal names LOP instead and lets them choose.

    ORDER MATTERS: overlap before balance. An applicant who has double-booked
    themselves should be told that, not handed an arithmetic result computed
    over days they have already applied for.
    """
    if not leave_kind:
        # No printed option named — the form allows that, and there is no
        # allowance to look up without one.
        return None
    academic_year = academic_year_for(from_date)
    balance = balance_for(db, user_id, leave_kind, academic_year)
    if balance is None:
        return None  # the office has recorded no policy here; see the module docstring

    clash = overlapping_request(db, user_id, from_date, to_date)
    if clash is not None:
        state = clash.status.value.replace("_", " ").lower()
        return (
            f"You already have a {state} leave request covering "
            f"{clash.from_date.isoformat()} to {clash.to_date.isoformat()}, which overlaps "
            f"{from_date.isoformat()} to {to_date.isoformat()}. Withdraw that request first "
            "if you want to apply for these days again."
        )

    days = working_days(db, college_of_applicant(db, user_id), from_date, to_date)
    remaining = balance.entitled_days - balance.consumed_days
    if days > remaining:
        left = max(remaining, 0)
        hint = (
            ""
            if leave_kind == "LOP"
            else " Apply for LOP (loss of pay) for the days beyond your balance, or"
        )
        return (
            f"This request is {days} working day(s), and your {leave_kind} balance for "
            f"{academic_year} has {left} of {balance.entitled_days} day(s) left."
            f"{hint} ask the office to adjust your balance."
        )
    return None
