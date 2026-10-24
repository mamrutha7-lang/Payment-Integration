"""The Alternate Arrangements table, and the colleague named in it (B10.6).

    GET  /api/leaves/alternate/mine            requests that name ME
    POST /api/leaves/{id}/alternate/accept     I will cover these classes
    GET  /api/leaves/{id}/alternate            the table, for the applicant and
                                               the people who sign
    POST /api/leaves/{id}/alternate/assign     name a colleague on one row

==============================================================================
THE PRIVACY HAZARD IS THE WHOLE POINT OF THIS MODULE
==============================================================================

Accepting an alternate arrangement gives a THIRD PARTY a reason to open a leave
request — a colleague who is neither the applicant nor an approver, whose only
connection is that their name is in the form's alternate table. `LeaveOut`
carries `reason`, the form's "Purpose" cell, which is free text and routinely
medical. Hand that projection to the alternate and a named colleague reads a
diagnosis they were never entitled to, on a screen built to answer "can you take
my Tuesday class".

So the alternate's two reads answer `LeaveBrief` — id, dates, the printed
option, the state, who is asking, and the ONE row addressed to them. It cannot
carry a reason because it HAS no field for one, which is a property of the shape
rather than a convention about call sites; `tests/test_leave_chain.py` pins its
field tuple and `tests/test_leave_alternate.py` pins that the alternate never
sees a reason through these paths.

`GET /{id}/alternate` is the OTHER projection and is gated accordingly: it is
the applicant's own table and the approver's, so it goes through the same
`_assert_can_decide` the paper and the attachments use, with the same flattened
404. It carries no `reason` either — it is a table of classes — but it names
every colleague on the form, which the alternate has no business reading about
each other.

==============================================================================
`accepted_at` IS NEVER AN INPUT FIELD, AND THAT IS WHY THE SHAPE IS HERE
==============================================================================

`alt_rows` is JSONB and `submit_leave` stores `[r.model_dump() for r in
body.alt_rows]` — the applicant's own request, verbatim. So ANY field added to
`routers/leave.py::AltRow` is a field the applicant can WRITE at submit time.
`user_id` is legitimately theirs to write (they name the colleague). An
acceptance is not: put `accepted_at` on that model and an applicant can submit a
request with their colleague's agreement already stamped on it, forging a
consent the colleague never gave, with nothing in the record to show it.

The keys therefore live in the STORED JSON and not on the input model, written
only by the two endpoints below. Nothing breaks downstream, and both mechanisms
that read those rows were built to tolerate this:

  * `routers/leave.py::_leave_out` does `AltRow(**r)`, and pydantic v2 IGNORES
    keys a model does not declare, so the applicant's and the approvers' view of
    the table is byte-identical to what it was before this module existed;
  * `app/leave_paper.py::_alt_cells` reads exactly (date, staff_name, cls, time,
    remarks) through `getattr`, so the college's form needs NO change at all —
    it prints the NAME, which is what 04 asks for.

`app/models/leave.py` records the same trap from the other side (a new `AltRow`
field with no default 500s every pre-existing row on read).

RULE 1 is not in play. RULE 2's gate is `_assert_can_decide`, imported, never
restated.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..identity import get_current_session
from ..models.leave import LeaveRequest, LeaveStatus
from ..models.user import Role, User
from .leave import AltRow, LeaveBrief, _assert_can_decide, _leave_brief

router = APIRouter(prefix="/leaves", tags=["leave-alternate"])

#: Every refusal on these paths, whatever the reason. A stranger who guesses an
#: id must not be able to tell "no such request" from "a request you are not
#: named on" — told apart, this is a membership oracle over the whole
#: programme, and it is reachable by every signed-in account rather than only by
#: staff.
NOT_FOUND = "Leave request not found."

#: The states in which the applicant may still edit who covers for them. After a
#: final decision the arrangement is what was signed; before the first signature
#: and between the two it is still being arranged. (The same pair
#: `routers/leave.py::CANCELLABLE` names, for the same reason, spelled again
#: here because "may I still change this" and "may I still withdraw this" are
#: two questions that happen to have the same answer today.)
ASSIGNABLE = (LeaveStatus.SUBMITTED, LeaveStatus.FIRST_APPROVED)

#: The states in which a colleague's acceptance still means something. A
#: REJECTED or CANCELLED request needs no cover; accepting one would record an
#: agreement to teach a class nobody is missing.
ACCEPTABLE = (LeaveStatus.SUBMITTED, LeaveStatus.FIRST_APPROVED, LeaveStatus.APPROVED)

#: How many requests `/alternate/mine` answers at once. The same order of
#: magnitude as `/history`'s 200 and for the same reason: the endpoint has no
#: other bound and a colleague who has covered for people for three years should
#: not download all of it to see this week's.
MINE_LIMIT = 100


class AlternateRowOut(BaseModel):
    """One line of the printed table, as the applicant and the approvers see it.

    `index` is the row's position in the stored list and is what
    `/alternate/assign` addresses. It is stable because `alt_rows` is written
    once by `submit_leave` and mutated only by this module — there is no edit
    endpoint for a leave request, and adding one would have to keep this true.
    """

    index: int
    date: str = ""
    staff_name: str = ""
    cls: str = ""
    time: str = ""
    remarks: str = ""
    #: The faculty ACCOUNT named on this row, once somebody has been assigned.
    #: NULL means the row carries a typed name and no account — which is every
    #: row written before B10.6 and every row the applicant has not linked. It
    #: is a real state: the paper prints `staff_name` either way.
    user_id: str | None = None
    user_name: str | None = None
    #: When that colleague agreed to cover. NULL means they have not (or have
    #: not been asked), never that they refused — there is no refusal here, and
    #: inventing one would put a word in a colleague's mouth.
    accepted_at: datetime | None = None


class AlternateTableOut(BaseModel):
    leave_id: str
    alt_name: str | None
    rows: list[AlternateRowOut]


class AlternateAssignIn(BaseModel):
    row_index: int = Field(ge=0)
    #: The faculty account to name. NULL CLEARS the link (and any acceptance
    #: with it), which is how a mis-assignment is undone — the typed
    #: `staff_name` the paper prints is untouched either way.
    user_id: str | None = None


def _rows_of(lr: LeaveRequest) -> list[dict]:
    """The stored table as plain dicts. `alt_rows` is JSONB with a `[]` server
    default, so it is never None in practice; the guard costs one branch and
    covers a row written by hand."""
    return [dict(r) for r in (lr.alt_rows or []) if isinstance(r, dict)]


def _parse_accepted(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        # A stamp nobody can read is not an acceptance. Reading it as one would
        # be the wrong direction to fail.
        return None


def _row_out(index: int, row: dict, people: dict[str, User]) -> AlternateRowOut:
    user_id = row.get("user_id") or None
    named = people.get(user_id or "")
    return AlternateRowOut(
        index=index,
        date=str(row.get("date") or ""),
        staff_name=str(row.get("staff_name") or ""),
        cls=str(row.get("cls") or ""),
        time=str(row.get("time") or ""),
        remarks=str(row.get("remarks") or ""),
        user_id=user_id,
        user_name=named.name if named else None,
        accepted_at=_parse_accepted(row.get("accepted_at")),
    )


def _table_out(db: Session, lr: LeaveRequest) -> AlternateTableOut:
    rows = _rows_of(lr)
    ids = {r.get("user_id") for r in rows if r.get("user_id")}
    people = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}
    )
    return AlternateTableOut(
        leave_id=lr.id,
        alt_name=lr.alt_name,
        rows=[_row_out(i, r, people) for i, r in enumerate(rows)],
    )


def _request_or_404(db: Session, leave_id: str) -> LeaveRequest:
    lr = db.get(LeaveRequest, leave_id)
    if lr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return lr


def _brief_for_alternate(db: Session, lr: LeaveRequest, user_id: str) -> LeaveBrief:
    """The reduced projection, carrying the ONE row addressed to this caller.

    The FIRST matching row when a request names the same colleague on several
    lines: the brief answers "are you being asked to cover, and when is this
    person away", and the whole table is the applicant's and the approvers' to
    read. `/alternate/mine` returns one entry per REQUEST for the same reason.
    """
    mine = next((r for r in _rows_of(lr) if r.get("user_id") == user_id), None)
    return _leave_brief(lr, db, alt_row=AltRow(**mine) if mine else None)


@router.get("/alternate/mine", response_model=list[LeaveBrief])
def requests_naming_me(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[LeaveBrief]:
    """Which requests ask ME to cover — the query `app/models/leave.py` said
    nobody makes, which B10.6 is exactly.

    THE REDUCED PROJECTION, and no capability: being named on somebody's form is
    the entitlement, and it is an entitlement to the dates and the class, not to
    the reason. Every state is returned, cancellations included, because the
    colleague most needs to know when the cover is no longer wanted — the brief
    carries `status` and the screen says which.
    """
    uid = session["userId"]
    rows = db.scalars(
        select(LeaveRequest)
        # JSONB containment: "the array holds an object with this user_id".
        # Narrowed in the DATABASE rather than by reading every request and
        # filtering in Python — the same rule `_narrow_to_scope` follows, and
        # here it is the difference between reading one row and reading the
        # whole table of medical reasons into this process.
        .where(LeaveRequest.alt_rows.contains([{"user_id": uid}]))
        .order_by(LeaveRequest.from_date.desc())
        .limit(MINE_LIMIT)
    ).all()
    return [_brief_for_alternate(db, lr, uid) for lr in rows]


@router.get("/{leave_id}/alternate", response_model=AlternateTableOut)
def alternate_table(
    leave_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AlternateTableOut:
    """The whole table — the applicant's, and whoever may sign the request.

    NOT the alternate's read: this names every colleague on the form and whether
    each has accepted, and a colleague asked to cover one Tuesday has no reason
    to know who else was asked. They get `/alternate/mine`.
    """
    lr = _request_or_404(db, leave_id)
    if lr.requester_user_id != session.get("userId"):
        _assert_can_decide(session, lr, db)
    return _table_out(db, lr)


@router.post("/{leave_id}/alternate/assign", response_model=AlternateTableOut)
def assign_alternate(
    leave_id: str,
    body: AlternateAssignIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AlternateTableOut:
    """Link a faculty ACCOUNT to one row of the table. The applicant's own act.

    THE TYPED NAME IS NOT TOUCHED. `staff_name` is what the college's form
    prints and what the applicant wrote; this adds the account so the colleague
    can be told and can accept. A link that disagreed with the printed name
    would be visible to nobody, so the two are shown side by side on the table
    above.

    ASSIGNING CLEARS ANY ACCEPTANCE ON THAT ROW, including when the same account
    is re-assigned to a different line. An acceptance is an agreement to cover a
    specific class at a specific time; moving the row underneath it would carry
    a colleague's "yes" onto something they never saw.
    """
    lr = _request_or_404(db, leave_id)
    if lr.requester_user_id != session.get("userId"):
        # Not the applicant. Flattened to the same 404 rather than a 403: this
        # endpoint is open to every signed-in account, so a distinguishable
        # refusal would let anybody enumerate leave requests.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if lr.status not in ASSIGNABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Leave is {lr.status.value}; the alternate arrangement can no "
                "longer be changed."
            ),
        )
    rows = _rows_of(lr)
    if body.row_index >= len(rows):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That row is not on this request's alternate arrangement table.",
        )
    if body.user_id:
        named = db.get(User, body.user_id)
        if named is None or named.role not in (Role.MENTOR, Role.ADMIN):
            # 422 and not 404: the LEAVE exists and the caller owns it; what is
            # wrong is the value they sent. A student cannot take a class, and
            # naming one would put somebody on the college's form who cannot
            # cover it.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="An alternate must be a member of staff.",
            )
        if named.id == lr.requester_user_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="You cannot be your own alternate.",
            )
        rows[body.row_index]["user_id"] = named.id
    else:
        rows[body.row_index].pop("user_id", None)
    rows[body.row_index].pop("accepted_at", None)
    # A NEW list object, not a mutation in place: `alt_rows` is a plain JSONB
    # column with no MutableList, so SQLAlchemy notices the reassignment and
    # would not notice an edit through the old one.
    lr.alt_rows = rows
    db.commit()
    db.refresh(lr)
    return _table_out(db, lr)


@router.post("/{leave_id}/alternate/accept", response_model=LeaveBrief)
def accept_alternate(
    leave_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveBrief:
    """"Yes, I will cover this." The named colleague's own act, and nobody
    else's — not the applicant's on their behalf, which would make the
    acceptance worthless.

    IT ANSWERS THE REDUCED PROJECTION. The caller is a third party: they learn
    the dates, the printed option, the state and their own row. Not the reason.

    Every row naming this account is accepted at once, because the question the
    colleague was asked is "will you cover for me while I am away" and splitting
    it per line would make a partial acceptance — half the classes covered,
    nothing on the form saying which half — reachable by a double-tap. An
    acceptance already recorded is left exactly as it was, so a second POST is
    idempotent rather than a fresh timestamp.
    """
    lr = _request_or_404(db, leave_id)
    uid = session["userId"]
    rows = _rows_of(lr)
    mine = [i for i, r in enumerate(rows) if r.get("user_id") == uid]
    if not mine:
        # Not named: the same 404 as a missing id. See NOT_FOUND above — this
        # endpoint is reachable by every signed-in account.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if lr.status not in ACCEPTABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Leave is {lr.status.value}; there is nothing to cover. "
                "You do not need to do anything."
            ),
        )
    now = datetime.now(timezone.utc).isoformat()
    changed = False
    for i in mine:
        if not rows[i].get("accepted_at"):
            rows[i]["accepted_at"] = now
            changed = True
    if changed:
        lr.alt_rows = rows
        db.commit()
        db.refresh(lr)
    return _brief_for_alternate(db, lr, uid)
