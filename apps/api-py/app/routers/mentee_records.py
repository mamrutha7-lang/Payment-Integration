"""The staff side of the v2 student screens.

    GET /api/mentor/students/{student_id}/ledger?day=      one day of the ledger
    GET /api/mentor/students/{student_id}/ledger/summary   the last N days
    GET /api/mentor/students/{student_id}/english-baseline the CEFR attempt

RULE 2 IS THE WHOLE POINT OF THIS MODULE. `app/routers/student_programme.py` is
first-person by construction — it reads `studentId` off the session and has no
path parameter naming a student anywhere, so there is nothing for a crafted id
to reach. These endpoints DO name a student in the path, which makes them the
exact shape rule 2 exists to govern, and every one of them goes through
`_assert_can_access_student` before it touches a row: a MENTOR sees only students
in their own group, a MENTOR with no group sees NOBODY, and the Main Admin sees
all. That helper lives in `routers/mentor.py` and is imported rather than
reimplemented — a second copy of a scope check is a second place for it to be
subtly wrong.

THE VIEWS ARE THE STUDENT'S OWN, not a staff rendering of the same rows. Both
endpoints call the builders in `student_programme.py`, so the metrics strip a
mentor reads is the one the student is looking at, computed by the same
expression. The alternative — a staff-shaped copy of "is this section pending or
scored", "how many hours short is this day" — is how a mentor ends up seeing a
confident 0 where the student sees a dash, and acting on it.

READ ONLY. There is deliberately no staff write path here: a mentor does not
edit a student's ledger. Their instrument is the meeting note, which already has
one.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..clock import local_today
from ..db import get_db
from ..identity import get_current_session
from ..models.time_ledger import DAY_CAPACITY_HALVES, LedgerDayStatus, TimeLedgerDay
from ..governance import require_capability
from .mentor import _assert_can_access_student
from .student_programme import (
    EnglishBaselineOut,
    LedgerOut,
    compose_english_baseline,
    compose_ledger,
    load_day,
)

router = APIRouter(prefix="/mentor", tags=["staff-screens"])


@router.get("/students/{student_id}/ledger", response_model=LedgerOut)
def read_student_ledger(
    student_id: str,
    day: date | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LedgerOut:
    """One day of a student's Time Allocation Ledger, exactly as they see it."""
    # mentor.mentees, not require_mentor alone: the Main Admin is not a faculty
    # member and has no mentees. It GRANTS itself this in Governance when it needs
    # to look, which is the rule - the admin decides who holds faculty powers,
    # itself included. Additive over the baseline, so faculty are unaffected.
    require_capability(db, session, "mentor.mentees")
    # B9.1: a GET, so the 90-day handover window applies (read-only by
    # construction -- this module has no write path, by design).
    _assert_can_access_student(session, student_id, db, allow_handover=True)
    # The programme's day (app/clock.py), the same clock the student's own
    # read uses, so staff and student see the same day open and the same lock.
    target = day or local_today()
    return compose_ledger(target, load_day(db, student_id, target))


class LedgerDaySummaryOut(BaseModel):
    day: date
    status: str
    logged_hours: float
    #: True only for a SUBMITTED day that reconciles. A submitted day always
    #: does — the submit gate enforces it — so this is really "did they close
    #: this day", surfaced as its own field so the client never has to infer it
    #: from a float comparison.
    reconciled: bool


class LedgerSummaryOut(BaseModel):
    window_days: int
    days_submitted: int
    days_with_anything: int
    #: Mean over days that have ANY entry, not over the whole window. Averaging
    #: over the window silently reports a student who logged three perfect days
    #: out of fourteen as averaging 5 h — which reads as under-work rather than
    #: as under-logging, and those need opposite conversations.
    mean_logged_hours: float
    days: list[LedgerDaySummaryOut]


@router.get("/students/{student_id}/ledger/summary", response_model=LedgerSummaryOut)
def read_student_ledger_summary(
    student_id: str,
    days: int = Query(default=14, ge=1, le=90),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LedgerSummaryOut:
    """The last `days` of ledger activity — the shape a mentor actually opens
    this screen for, which is "are they keeping it up", not "what did Tuesday
    look like"."""
    # mentor.mentees, not require_mentor alone: the Main Admin is not a faculty
    # member and has no mentees. It GRANTS itself this in Governance when it needs
    # to look, which is the rule - the admin decides who holds faculty powers,
    # itself included. Additive over the baseline, so faculty are unaffected.
    require_capability(db, session, "mentor.mentees")
    # B9.1: a GET, so the 90-day handover window applies (read-only by
    # construction -- this module has no write path, by design).
    _assert_can_access_student(session, student_id, db, allow_handover=True)
    return compose_ledger_summary(db, student_id, days)


def compose_ledger_summary(db: Session, student_id: str, days: int) -> LedgerSummaryOut:
    """The last `days` of one student's ledger, summarised. Shared with the
    office's Student 360 read (`routers/admin_student_record.py`), so the
    mentor and the office see the same numbers. The caller has already run
    both fences."""
    since = local_today() - timedelta(days=days - 1)
    rows = db.scalars(
        select(TimeLedgerDay)
        .options(selectinload(TimeLedgerDay.cells))
        .where(TimeLedgerDay.student_id == student_id, TimeLedgerDay.day >= since)
        .order_by(TimeLedgerDay.day.desc())
    ).all()

    summaries: list[LedgerDaySummaryOut] = []
    for row in rows:
        halves = sum(cell.half_hours for cell in row.cells)
        summaries.append(
            LedgerDaySummaryOut(
                day=row.day,
                status=row.status.value,
                logged_hours=halves / 2,
                reconciled=(
                    row.status == LedgerDayStatus.SUBMITTED and halves == DAY_CAPACITY_HALVES
                ),
            )
        )

    logged = [s.logged_hours for s in summaries if s.logged_hours > 0]
    return LedgerSummaryOut(
        window_days=days,
        days_submitted=sum(1 for s in summaries if s.status == LedgerDayStatus.SUBMITTED.value),
        days_with_anything=len(logged),
        mean_logged_hours=round(sum(logged) / len(logged), 1) if logged else 0.0,
        days=summaries,
    )


@router.get("/students/{student_id}/english-baseline", response_model=EnglishBaselineOut)
def read_student_english_baseline(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> EnglishBaselineOut:
    """A student's CEFR attempt, in the student's own view.

    Including its nullable scores: a mentor deciding whether to intervene needs
    "Speaking has not been sat" and "Speaking scored 0" to look different, which
    is the same reason the columns are nullable in the first place.
    """
    # mentor.mentees, not require_mentor alone: the Main Admin is not a faculty
    # member and has no mentees. It GRANTS itself this in Governance when it needs
    # to look, which is the rule - the admin decides who holds faculty powers,
    # itself included. Additive over the baseline, so faculty are unaffected.
    require_capability(db, session, "mentor.mentees")
    # B9.1: a GET, so the 90-day handover window applies (read-only by
    # construction -- this module has no write path, by design).
    _assert_can_access_student(session, student_id, db, allow_handover=True)
    return compose_english_baseline(db, student_id)
