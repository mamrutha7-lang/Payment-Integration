"""Allowances and the college calendar — the office's side of B10.2.

    GET    /api/leaves/balances                       my own allowances
    GET    /api/leaves/calendar                       my college's closed days
    GET    /api/leaves/{id}/balance                   the APPLICANT's allowances

    GET    /api/admin/leave-policy                    the sheet the console draws
    GET    /api/admin/leave-balances                  list, filtered
    PUT    /api/admin/leave-balances                  record one allowance
    POST   /api/admin/leave-balances/bulk             a department, in one act
    DELETE /api/admin/leave-balances/{id}
    GET    /api/admin/leave-calendar/{college_id}
    PUT    /api/admin/leave-calendar/{college_id}     record one day
    DELETE /api/admin/leave-calendar/{college_id}/{day_id}

The TABLES are `app/models/leave_policy.py`; the READING of them on the submit
path is `app/leave_policy.py` (`submit_refusal`, `academic_year_for`,
`working_days`). This module is the third piece: the CRUD, in its own router for
`app/routers/leave_paper.py`'s reason — `routers/leave.py` holds the form's
submit and decide paths, which the owner asked to leave exactly as they are.

==============================================================================
THE WRITES ARE THE MAIN ADMIN'S, AND NO NEW CAPABILITY KEY IS MINTED
==============================================================================

`require_admin` and nothing else. An allowance is the office recording what a
person is entitled to, and the calendar is the college recording when it is
shut; both are the office's own decisions and neither is a screen anybody has
asked to delegate. A new `admin.leave_policy` key would have to be added to
`CAPABILITIES`, enforced in the same commit, kept in `COLLEGE_ADMIN_CAPABILITIES`
or deliberately out of it, and answered for in Governance — all to express
"the Main Admin", which the role gate already says. Keys are cheap to add and
permanent to live with.

This module's one APPROVER-facing read (`/leaves/{id}/balance`) hangs off a
LEAVE REQUEST and is therefore gated by `_assert_can_decide` — the Main Admin
and nobody else since 2026-09-16, the same flattened 404, the same import that
`leave_paper.py` takes.

==============================================================================
A BALANCE READ ABOUT SOMEBODY ELSE IS RULE 2, AND THE OBVIOUS FENCE IS A NO-OP
==============================================================================

`leave_balances` is keyed on `users` and INCLUDES STUDENTS, so "how many days
has this person taken" is a fact about a person that rule 2 governs. There is no
`require_capability(..., target=...)` in this file and there must not be one:
that function SHORT-CIRCUITS before it looks at a scope for a baseline key and
for a capability a MENTOR holds as a derived FUNCTION, so written that way a
fence passes for most of the people it fences. (Leave approval itself stopped
being a capability on 2026-09-16 — it is the Main Admin's by role.)

So there are exactly two doors to somebody else's balance, and both already
exist: the Main Admin (the whole roster, through `require_admin`), and an
approver reading the balance OF A REQUEST THEY MAY DECIDE. A general
"show me any person's balance" endpoint for approvers is deliberately NOT built;
it would need either a fourth copy of rule 2 or the short-circuiting check
above, and neither is worth a screen nobody has drawn.

==============================================================================
THE CALENDAR IS A COLLEGE CATALOGUE AND NAMES NOBODY
==============================================================================

`GET /api/leaves/calendar` answers any signed-in account, for THEIR OWN college
only — it is the list of days the college is shut, which is public in every
sense that matters and is the number the submit path counts with. No rule-2 gate
applies because there is no person in it. Writing it is still the office's.

RULE 1 is not in play: nothing here reaches a model.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..db import get_db
from ..identity import get_current_session
from ..leave_policy import academic_year_for, college_of_applicant
from ..models.cohort import Cohort
from ..models.institution import College, Department
from ..models.leave import LeaveRequest
from ..models.leave_policy import (
    CALENDAR_HOLIDAY,
    CALENDAR_KINDS,
    PRINTED_LEAVE_KINDS,
    AcademicCalendarDay,
    LeaveBalance,
)
from ..models.user import Role, Student, User
from .leave import _assert_can_decide
from .mentor import require_admin

#: Mounted under /api by main.py, so these become /api/leaves/... and
#: /api/admin/... — two prefixes, one module, because they are one concept read
#: from two chairs (interview_policy.py's shape).
leave_router = APIRouter(prefix="/leaves", tags=["leave-policy"])
admin_router = APIRouter(prefix="/admin", tags=["leave-policy"])

NOT_FOUND = "Leave request not found."

#: How far a calendar read may reach in one request. A year of holidays is a few
#: dozen rows; this stops "from 1900 to 2100" being a table scan, and the screen
#: asks for one academic year at a time.
MAX_CALENDAR_DAYS = 1000


# --------------------------------------------------------------- payloads --


class LeaveBalanceOut(BaseModel):
    id: str
    user_id: str
    #: The person, as the console lists them. Absent on `/leaves/balances`,
    #: where the person is the caller.
    user_name: str | None = None
    user_email: str | None = None
    user_role: str | None = None
    kind: str
    academic_year: str
    entitled_days: int
    consumed_days: int
    #: `entitled - consumed`, which may be NEGATIVE: leave past an allowance
    #: happens and the office signs it (`ck_leave_balance_days` refuses only a
    #: negative entitlement or a negative consumption). A screen that clamps
    #: this at zero hides exactly the row the office is looking for.
    remaining_days: int
    updated_at: datetime | None = None


class LeaveBalanceIn(BaseModel):
    """One allowance, recorded or corrected. An UPSERT on
    (user_id, kind, academic_year), which is the row's unique key."""

    user_id: str
    kind: str = Field(pattern="^(CASUAL|PERMISSION|OOD|RH|LOP)$")
    academic_year: str = Field(min_length=4, max_length=16)
    entitled_days: int = Field(ge=0, le=365)
    #: Omitted KEEPS what is already recorded, and on a new row starts at zero.
    #: `interview_policy.PolicyIn`'s rule: a console that sent one field and
    #: silently reset the other would change a decision nobody made — and this
    #: is the field that decides whether somebody may take leave tomorrow.
    consumed_days: int | None = Field(default=None, ge=0, le=365)


class LeaveBalanceBulkIn(BaseModel):
    """One allowance for everybody in a department (02's "per department").

    Without this the office types one row per person, and a department is
    hundreds of students. The console spec asks for balances "per department
    (balances per kind, academic year)" and this is that sentence as a write.
    """

    department_id: str
    kind: str = Field(pattern="^(CASUAL|PERMISSION|OOD|RH|LOP)$")
    academic_year: str = Field(min_length=4, max_length=16)
    entitled_days: int = Field(ge=0, le=365)
    #: Who is covered. Both default true because a department is its staff AND
    #: its students and the form applies to both; either can be turned off for
    #: the common case of a staff-only entitlement.
    include_staff: bool = True
    include_students: bool = True


class LeaveBalanceBulkOut(BaseModel):
    #: Rows written. An EXISTING row is never overwritten — see the endpoint.
    created: int
    #: People who already had an allowance for this kind and year, left alone.
    skipped: int
    department_id: str
    kind: str
    academic_year: str


class CalendarDayOut(BaseModel):
    id: str
    college_id: str
    day: date
    kind: str
    label: str | None
    created_by_user_id: str | None = None
    updated_at: datetime | None = None


class CalendarDayIn(BaseModel):
    """One day, recorded or corrected. An UPSERT on (college_id, day)."""

    day: date
    #: `holiday` (we are shut) or `working` (we are open on a day you would
    #: assume shut). Only `holiday` is ever subtracted from a leave span; see
    #: `app/leave_policy.working_days`.
    kind: str = Field(default=CALENDAR_HOLIDAY, pattern="^(holiday|working)$")
    label: str | None = Field(default=None, max_length=200)


class LeaveCollegeSummaryOut(BaseModel):
    college_id: str
    college_name: str | None
    holidays: int
    working_days: int


class LeavePolicySheetOut(BaseModel):
    """Everything the console's "Leave policy" card needs in one read.

    `academic_year` is `academic_year_for(today)` — the SPELLING the submit path
    will look a row up by. The screen must pre-fill its field with it, or the
    office types "2026-2027", the lookup misses and the check silently never
    fires. `app/leave_policy.py` says why a miss is the safe failure and why it
    must stay visible.
    """

    academic_year: str
    kinds: list[str]
    balances_recorded: int
    people_with_balances: int
    colleges: list[LeaveCollegeSummaryOut]


class LeaveBalanceSetOut(BaseModel):
    """What the applicant's own screen and the approver's panel both read.

    A LIST AND A YEAR, never a single number: the five printed options are five
    separate allowances and "your balance" is not a thing. An EMPTY list means
    the office has recorded no allowance — which is NOT a zero balance, and the
    screen must say so in those words (`LeaveBalance`'s own docstring).
    """

    academic_year: str
    #: Whose allowances these are. The caller's own on `/leaves/balances`; the
    #: APPLICANT's on `/leaves/{id}/balance`, where the reader is an approver.
    user_id: str
    balances: list[LeaveBalanceOut]


# ---------------------------------------------------------------- helpers --


def _balance_out(row: LeaveBalance, *, user: User | None = None) -> LeaveBalanceOut:
    return LeaveBalanceOut(
        id=row.id,
        user_id=row.user_id,
        user_name=user.name if user else None,
        user_email=user.email if user else None,
        user_role=user.role.value if user else None,
        kind=row.kind,
        academic_year=row.academic_year,
        entitled_days=int(row.entitled_days),
        consumed_days=int(row.consumed_days),
        remaining_days=int(row.entitled_days) - int(row.consumed_days),
        updated_at=row.updated_at,
    )


def _day_out(row: AcademicCalendarDay) -> CalendarDayOut:
    return CalendarDayOut(
        id=row.id,
        college_id=row.college_id,
        day=row.day,
        kind=row.kind,
        label=row.label,
        created_by_user_id=row.created_by_user_id,
        updated_at=row.updated_at,
    )


def _balances_of(db: Session, user_id: str, academic_year: str) -> list[LeaveBalance]:
    return list(
        db.scalars(
            select(LeaveBalance)
            .where(
                LeaveBalance.user_id == user_id,
                LeaveBalance.academic_year == academic_year,
            )
            .order_by(LeaveBalance.kind)
        ).all()
    )


def _require_college(db: Session, college_id: str) -> College:
    college = db.get(College, college_id)
    if college is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="College not found.")
    return college


def _department_member_ids(db: Session, department_id: str, *, staff: bool, students: bool) -> list[str]:
    """Every ACCOUNT filed under one department.

    BOTH STUDENT POINTERS ARE READ, and that is not belt and braces: a student
    reaches a department through their BATCH (`cohorts.department_id`) or
    through their own `students.department_id`, and a query that used only the
    first would silently miss every student at a college that has not built its
    batches yet. `governance.ancestry_of_student` had exactly this bug and the
    4d map records it; the fix is the same one, here as a `or_`.
    """
    ids: list[str] = []
    if staff:
        ids += [
            uid
            for (uid,) in db.execute(
                select(User.id).where(
                    User.department_id == department_id,
                    User.role.in_([Role.MENTOR, Role.ADMIN]),
                )
            ).all()
        ]
    if students:
        ids += [
            uid
            for (uid,) in db.execute(
                select(Student.user_id)
                .outerjoin(Cohort, Student.cohort_id == Cohort.id)
                .where(
                    or_(
                        Student.department_id == department_id,
                        Cohort.department_id == department_id,
                    ),
                    Student.user_id.is_not(None),
                )
            ).all()
        ]
    return sorted(set(ids))


# ---------------------------------------------- the person's own two reads --


@leave_router.get("/balances", response_model=LeaveBalanceSetOut)
def my_balances(
    academic_year: str | None = Query(default=None),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveBalanceSetOut:
    """Your own allowances for a year. Every signed-in account, no capability:
    it is your row, and students hold balances too."""
    year = (academic_year or "").strip() or academic_year_for(date.today())
    rows = _balances_of(db, session["userId"], year)
    return LeaveBalanceSetOut(
        academic_year=year, user_id=session["userId"], balances=[_balance_out(r) for r in rows]
    )


@leave_router.get("/calendar", response_model=list[CalendarDayOut])
def my_calendar(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[CalendarDayOut]:
    """The closed (and explicitly open) days of the CALLER'S OWN college.

    No college id in the path on purpose: an account reads its own college's
    calendar and nobody else's, which needs no scope machinery and cannot be
    walked. An account filed under no college (the first-class "unfiled" state
    on the Faculty screen, and every student with no batch and no department)
    gets an empty list — which is exactly the calendar the submit path applies
    to them.
    """
    college_id = college_of_applicant(db, session["userId"])
    if not college_id:
        return []
    return _calendar_rows(db, college_id, from_date, to_date)


def _calendar_rows(
    db: Session, college_id: str, from_date: date | None, to_date: date | None
) -> list[CalendarDayOut]:
    query = select(AcademicCalendarDay).where(AcademicCalendarDay.college_id == college_id)
    if from_date:
        query = query.where(AcademicCalendarDay.day >= from_date)
    if to_date:
        query = query.where(AcademicCalendarDay.day <= to_date)
    rows = db.scalars(query.order_by(AcademicCalendarDay.day).limit(MAX_CALENDAR_DAYS)).all()
    return [_day_out(r) for r in rows]


@leave_router.get("/{leave_id}/balance", response_model=LeaveBalanceSetOut)
def balance_behind_a_request(
    leave_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveBalanceSetOut:
    """The APPLICANT's allowances, for whoever may sign this request.

    Keyed on the REQUEST and not on the person, which is what makes it safe: the
    gate is `_assert_can_decide` — the same function the paper and the
    attachments use, the same three doors, the same flattened 404 — so an
    approver sees the balance of the applicant in front of them and of nobody
    else. The year is taken from the request's OWN start date, not from today: a
    request submitted in March for April is measured against the year it falls
    in, and reading it against today's year in June would answer about a
    different allowance entirely.
    """
    lr = db.get(LeaveRequest, leave_id)
    if lr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if lr.requester_user_id != session.get("userId"):
        _assert_can_decide(session, lr, db)
    year = academic_year_for(lr.from_date)
    rows = _balances_of(db, lr.requester_user_id, year)
    return LeaveBalanceSetOut(
        academic_year=year,
        user_id=lr.requester_user_id,
        balances=[_balance_out(r) for r in rows],
    )


# ------------------------------------------------------- the office's CRUD --


@admin_router.get("/leave-policy", response_model=LeavePolicySheetOut)
def policy_sheet(
    academic_year: str | None = Query(default=None),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeavePolicySheetOut:
    require_admin(session)
    year = (academic_year or "").strip() or academic_year_for(date.today())
    recorded = (
        db.scalar(
            select(func.count(LeaveBalance.id)).where(LeaveBalance.academic_year == year)
        )
        or 0
    )
    people = (
        db.scalar(
            select(func.count(func.distinct(LeaveBalance.user_id))).where(
                LeaveBalance.academic_year == year
            )
        )
        or 0
    )
    counts = db.execute(
        select(
            AcademicCalendarDay.college_id,
            AcademicCalendarDay.kind,
            func.count(AcademicCalendarDay.id),
        ).group_by(AcademicCalendarDay.college_id, AcademicCalendarDay.kind)
    ).all()
    by_college: dict[str, dict[str, int]] = {}
    for college_id, kind, n in counts:
        by_college.setdefault(college_id, {})[kind] = int(n)
    colleges = db.scalars(select(College).order_by(College.name)).all()
    return LeavePolicySheetOut(
        academic_year=year,
        kinds=list(PRINTED_LEAVE_KINDS),
        balances_recorded=int(recorded),
        people_with_balances=int(people),
        colleges=[
            LeaveCollegeSummaryOut(
                college_id=c.id,
                college_name=c.name,
                holidays=by_college.get(c.id, {}).get(CALENDAR_HOLIDAY, 0),
                working_days=by_college.get(c.id, {}).get("working", 0),
            )
            for c in colleges
        ],
    )


@admin_router.get("/leave-balances", response_model=list[LeaveBalanceOut])
def list_balances(
    academic_year: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    department_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[LeaveBalanceOut]:
    """The office's list. Paged, because a department's worth of students is
    hundreds of rows and this endpoint has no other bound."""
    require_admin(session)
    query = select(LeaveBalance, User).join(User, LeaveBalance.user_id == User.id)
    if academic_year:
        query = query.where(LeaveBalance.academic_year == academic_year)
    if user_id:
        query = query.where(LeaveBalance.user_id == user_id)
    if kind:
        query = query.where(LeaveBalance.kind == kind)
    if department_id:
        member_ids = _department_member_ids(db, department_id, staff=True, students=True)
        if not member_ids:
            return []
        query = query.where(LeaveBalance.user_id.in_(member_ids))
    rows = db.execute(
        query.order_by(User.name, LeaveBalance.kind).limit(limit).offset(offset)
    ).all()
    return [_balance_out(b, user=u) for b, u in rows]


@admin_router.put("/leave-balances", response_model=LeaveBalanceOut)
def set_balance(
    body: LeaveBalanceIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveBalanceOut:
    """Record or correct one allowance."""
    require_admin(session)
    user = db.get(User, body.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    row = db.scalar(
        select(LeaveBalance).where(
            LeaveBalance.user_id == body.user_id,
            LeaveBalance.kind == body.kind,
            LeaveBalance.academic_year == body.academic_year,
        )
    )
    before = (
        {"entitled_days": int(row.entitled_days), "consumed_days": int(row.consumed_days)}
        if row
        else None
    )
    if row is None:
        row = LeaveBalance(
            user_id=body.user_id,
            kind=body.kind,
            academic_year=body.academic_year,
            entitled_days=body.entitled_days,
            consumed_days=body.consumed_days or 0,
        )
        db.add(row)
    else:
        row.entitled_days = body.entitled_days
        if body.consumed_days is not None:
            row.consumed_days = body.consumed_days
    db.flush()
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="leave_balance",
        entity_id=row.id,
        action="LEAVE_BALANCE_SET",
        before=before,
        after={
            "user_id": row.user_id,
            "kind": row.kind,
            "academic_year": row.academic_year,
            "entitled_days": int(row.entitled_days),
            "consumed_days": int(row.consumed_days),
        },
        event_type="leave.balance.set",
        payload={"balance_id": row.id, "user_id": row.user_id},
    )
    db.commit()
    db.refresh(row)
    return _balance_out(row, user=user)


@admin_router.post(
    "/leave-balances/bulk",
    response_model=LeaveBalanceBulkOut,
    status_code=status.HTTP_201_CREATED,
)
def set_department_balances(
    body: LeaveBalanceBulkIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveBalanceBulkOut:
    """One allowance for a whole department.

    IT CREATES AND NEVER OVERWRITES. A person who already has a row for this
    kind and year is SKIPPED, count and all — because the row carries
    `consumed_days`, and a bulk write that reset it in December would hand back
    days people had already taken, silently, to everybody at once. Correcting an
    individual allowance is `PUT /leave-balances`, one person at a time, which
    is the shape a correction should have.
    """
    require_admin(session)
    department = db.get(Department, body.department_id)
    if department is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found.")
    member_ids = _department_member_ids(
        db, body.department_id, staff=body.include_staff, students=body.include_students
    )
    existing = set(
        db.scalars(
            select(LeaveBalance.user_id).where(
                LeaveBalance.user_id.in_(member_ids or [""]),
                LeaveBalance.kind == body.kind,
                LeaveBalance.academic_year == body.academic_year,
            )
        ).all()
    )
    created = 0
    for uid in member_ids:
        if uid in existing:
            continue
        db.add(
            LeaveBalance(
                user_id=uid,
                kind=body.kind,
                academic_year=body.academic_year,
                entitled_days=body.entitled_days,
                consumed_days=0,
            )
        )
        created += 1
    db.flush()
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="leave_balance",
        entity_id=body.department_id,
        action="LEAVE_BALANCE_BULK",
        before=None,
        after={
            "department_id": body.department_id,
            "kind": body.kind,
            "academic_year": body.academic_year,
            "entitled_days": body.entitled_days,
            "created": created,
            "skipped": len(existing),
        },
        event_type="leave.balance.bulk",
        payload={"department_id": body.department_id, "created": created},
    )
    db.commit()
    return LeaveBalanceBulkOut(
        created=created,
        skipped=len(existing),
        department_id=body.department_id,
        kind=body.kind,
        academic_year=body.academic_year,
    )


@admin_router.delete("/leave-balances/{balance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_balance(
    balance_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    """Remove an allowance. DELETING IS NOT SETTING IT TO ZERO: with no row the
    submit path stops checking this person's requests altogether, and with a row
    at zero every request of that kind is refused. Two opposite outcomes, which
    is why the console must not offer this as "clear"."""
    require_admin(session)
    row = db.get(LeaveBalance, balance_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Balance not found.")
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="leave_balance",
        entity_id=row.id,
        action="LEAVE_BALANCE_DELETE",
        before={
            "user_id": row.user_id,
            "kind": row.kind,
            "academic_year": row.academic_year,
            "entitled_days": int(row.entitled_days),
            "consumed_days": int(row.consumed_days),
        },
        after=None,
        event_type="leave.balance.deleted",
        payload={"balance_id": row.id, "user_id": row.user_id},
    )
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.get("/leave-calendar/{college_id}", response_model=list[CalendarDayOut])
def list_calendar(
    college_id: str,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[CalendarDayOut]:
    require_admin(session)
    _require_college(db, college_id)
    return _calendar_rows(db, college_id, from_date, to_date)


@admin_router.put("/leave-calendar/{college_id}", response_model=CalendarDayOut)
def set_calendar_day(
    college_id: str,
    body: CalendarDayIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CalendarDayOut:
    """Record one day, or correct the one already recorded for that date."""
    require_admin(session)
    _require_college(db, college_id)
    if body.kind not in CALENDAR_KINDS:  # pragma: no cover - the pattern already refuses
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"kind must be one of {', '.join(CALENDAR_KINDS)}.",
        )
    row = db.scalar(
        select(AcademicCalendarDay).where(
            AcademicCalendarDay.college_id == college_id,
            AcademicCalendarDay.day == body.day,
        )
    )
    before = {"kind": row.kind, "label": row.label} if row else None
    if row is None:
        row = AcademicCalendarDay(
            college_id=college_id,
            day=body.day,
            kind=body.kind,
            label=body.label,
            created_by_user_id=session.get("userId"),
        )
        db.add(row)
    else:
        row.kind = body.kind
        row.label = body.label
    db.flush()
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="academic_calendar",
        entity_id=row.id,
        action="ACADEMIC_CALENDAR_SET",
        before=before,
        after={"college_id": college_id, "day": body.day.isoformat(), "kind": row.kind,
               "label": row.label},
        event_type="leave.calendar.set",
        payload={"calendar_id": row.id, "college_id": college_id},
    )
    db.commit()
    db.refresh(row)
    return _day_out(row)


@admin_router.delete(
    "/leave-calendar/{college_id}/{day_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_calendar_day(
    college_id: str,
    day_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    require_admin(session)
    row = db.get(AcademicCalendarDay, day_id)
    if row is None or row.college_id != college_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar day not found.")
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="academic_calendar",
        entity_id=row.id,
        action="ACADEMIC_CALENDAR_DELETE",
        before={"college_id": row.college_id, "day": row.day.isoformat(), "kind": row.kind,
                "label": row.label},
        after=None,
        event_type="leave.calendar.deleted",
        payload={"calendar_id": row.id, "college_id": row.college_id},
    )
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
