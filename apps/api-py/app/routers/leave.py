"""Leave requests — submit, and ONE decision by the office or its delegate.

ONE SIGNATURE, THE OFFICE'S (2026-09-16). Any signed-in account submits.
The MAIN ADMIN decides: APPROVE moves SUBMITTED -> APPROVED, REJECT moves it
to REJECTED, and that is the whole chain — a single approval, no second step.
Nobody decides their own request.

AND THE OFFICE MAY HAND THE QUEUE TO A FACULTY MEMBER (2026-09-17). The gate
is the `admin.leave_approvals` capability: the Main Admin holds it by baseline
and nothing about the office's own path changed, and a faculty account holds
it only by a GRANT the Main Admin makes in Governance, with a reason, on the
audit trail, revocable the same hour. A holder sees the same programme-wide
queue the office sees (minus their own requests) and signs as DELEGATE, which
the paper prints beside the name. This is NOT the retired `mentor.leave_approve`
coming back: that key was DERIVED from mentoring somebody and reached one
group's queue, and it deadlocked with the second signature it was part of.
This one is a decision the office makes about a named person, and it carries
the one signature the office would otherwise give.

WHAT IT REPLACES, kept here because the shape of the old rule explains the
columns that are still on the row. Until this date a request needed TWO
DISTINCT signatures (SUBMITTED -> FIRST_APPROVED -> APPROVED), the first from a
MENTOR over their own group or from a faculty member holding a SCOPED GRANT of
`mentor.leave_approve` (B10.1's "third door"), the second from a different
approver. On a deployment with one office account and no such grant that
chain deadlocked every staff request at FIRST_APPROVED with a live "Sanction"
button, and the owner's answer was not a second signer but a single one. So:

  * `first_*` are what a decision writes now; `second_*` stay NULL on every
    new row. The paper and the console read `second or first`, so a request
    decided under either rule prints its decider. `LeaveStatus.FIRST_APPROVED`
    and the `second_*` columns stay for the rows that carry them — a Postgres
    enum value cannot be dropped — and `decide_leave` completes such a row
    with the office's one decision, whoever signed it first.
  * `mentor.leave_approve` is GONE from the capability catalogue and from the
    derived mentor functions; migration `d8b1f4c2a7e9` revokes every live
    grant of it. A key nothing checks is a promise the API does not keep
    (B2.1), and a stray row naming it still opens nothing — the successor is
    a different key on purpose.
  * `_assert_can_decide` still exists and still returns the function the
    decider acted in, because `leave_paper.py`, `leave_attachments.py`,
    `leave_alternate.py` and `leave_policy.py` import it as THE gate on
    reading somebody else's request; it admits the Main Admin
    (`SIGNED_AS_MAIN_ADMIN`) and a holder of `admin.leave_approvals`
    (`SIGNED_AS_DELEGATE`), with the same flattened 404 for everybody else.

RULE 2 ON THE APPLICANT'S SIDE IS UNCHANGED: `/mine`, `POST` and `/cancel` are
the applicant's own and gate on nothing but the session; `reason` is free
text and routinely medical, which is why the queues are the office's alone.

WHAT THE APPLICANT SEES AND WHAT A THIRD PARTY SEES ARE DIFFERENT PROJECTIONS
(B10.7). `_leave_out` carries `reason` and is for the applicant and the office;
`_leave_brief` carries dates, the printed option, the state and the one
alternate row addressed to the caller, and is for anybody else with a reason
to open the record.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..clock import local_today
from ..db import get_db
from ..governance import has_capability, require_capability
from ..identity import get_current_session
from ..leave_mail import (
    announce_if_on_leave_today,
    notify_approvers_in_background,
    notify_decision_changed,
    notify_transition,
)
from ..leave_policy import overlapping_request, submit_refusal
from ..scope_views import SCOPE_HEADER
from ..models.leave import (
    SIGNED_AS_DELEGATE,
    SIGNED_AS_MAIN_ADMIN,
    LeaveDecision,
    LeaveRequest,
    LeaveStatus,
)
from ..models.user import User
from .mentor import require_mentor

router = APIRouter(prefix="/leaves", tags=["leaves"])

#: The approver's key. ADMIN holds it by baseline (`ROLE_BASELINE` is `_ALL`
#: minus the faculty instruments), so the office's path is exactly what it was;
#: a MENTOR holds it only by a grant. Named once, because
#: `tools/ci/check_capability_enforcement.py` resolves module constants and a
#: literal repeated at three call sites is three places to mistype it.
LEAVE_APPROVAL_CAPABILITY = "admin.leave_approvals"


class AltRow(BaseModel):
    """One line of the form's Alternate Arrangements table."""

    date: str = ""
    staff_name: str = ""
    cls: str = ""
    time: str = ""
    remarks: str = ""


class LeaveIn(BaseModel):
    from_date: date
    to_date: date
    # The form's "Purpose" cell. Still `reason` in the schema — renaming a
    # column that four call sites and a scope-rule docstring refer to, to match
    # a label, would be churn for no gain.
    reason: str = Field(min_length=1, max_length=2000)
    # Printed options only: the form lists these five and strikes off the rest,
    # so this is a choice among them and not an open leave-type vocabulary.
    leave_kind: str | None = Field(default=None, pattern="^(CASUAL|PERMISSION|OOD|RH|LOP)$")
    credit: str | None = Field(default=None, max_length=200)
    alt_name: str | None = Field(default=None, max_length=200)
    alt_rows: list[AltRow] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def dates_run_forwards(self) -> "LeaveIn":
        """A leave that ends before it starts is not a leave, and it was
        reaching the official form.

        The two dates were declared independently and nothing related them, so
        20 Dec -> 10 Dec was accepted, stored with a span of MINUS TEN days,
        listed in the approver's queue with a live "Mark Sanctioned" button, and
        rendered onto the college's own PDF. Found in the browser, not by a test.

        The check lives HERE, on the schema, deliberately: the owner's standing
        instruction is that the leave form and its buttons do not change, so the
        one safe place to refuse it is before the request is ever built. A
        single day is legal — `from == to` is how PERMISSION and a one-day
        CASUAL leave are both written on the paper form.
        """
        if self.to_date < self.from_date:
            raise ValueError(
                "The last day of leave cannot fall before the first day. "
                f"You asked for {self.from_date.isoformat()} to {self.to_date.isoformat()}."
            )
        return self


class LeaveOut(BaseModel):
    id: str
    from_date: date
    to_date: date
    reason: str
    status: str
    leave_kind: str | None
    credit: str | None
    alt_name: str | None
    alt_rows: list[AltRow]

    # The form prints the applicant's institutional identity beside their name,
    # so the document can be rendered from one response rather than the client
    # stitching it together from /auth/me.
    requester_name: str
    requester_designation: str | None
    requester_department: str | None

    # The two signature blocks. A signature is a NAME AND A TIME, and neither
    # half is printed without the other: an approval with no timestamp, or a
    # timestamp with no approver, is exactly the ambiguity a signed form exists
    # to remove.
    signed_at: datetime | None
    director_name: str | None
    director_decided_at: datetime | None
    director_note: str | None

    # WHICH FUNCTION EACH SIGNATURE WAS GIVEN IN (B10.1), added at the END of
    # the model so every existing field keeps its place in the payload. NULL on
    # every row decided before these columns existed, and on every step nobody
    # has signed yet; the console and `app/leave_paper.py` must render that as
    # nothing at all rather than guessing a function from who the signer is
    # today. See `app/models/leave.py` for why there is no "HOD" in this set.
    first_signed_as: str | None = None
    second_signed_as: str | None = None

    # A DECISION CHANGED AFTER IT WAS MADE (2026-10-13), at the end for the
    # same reason. The LATEST change only — REVOKED or REOPENED — with who,
    # when and the reason the approver had to give. The applicant reads the
    # reason on their own screen; it is written to them.
    decision_change_kind: str | None = None
    decision_changed_at: datetime | None = None
    decision_change_reason: str | None = None
    decision_changed_by_name: str | None = None


#: The reduced projection's field set, pinned here and asserted in
#: `tests/test_leave_chain.py`. A field added to `LeaveBrief` without a line in
#: this tuple fails that test, which is the only thing standing between "the
#: alternate sees the dates" and "the alternate sees the reason".
BRIEF_FIELDS: tuple[str, ...] = (
    "id",
    "from_date",
    "to_date",
    "leave_kind",
    "status",
    "requester_name",
    "alt_row",
)


class LeaveBrief(BaseModel):
    """WHAT SOMEBODY WHO IS NOT THE APPLICANT AND NOT AN APPROVER MAY SEE (B10.7).

    `LeaveOut` carries `reason` — the form's "Purpose" cell, which is free text
    and is routinely medical — to whoever is handed a row. Until now that was
    safe BY ACCIDENT OF WHO CALLS IT: `/mine` answers the applicant and the two
    queues are behind `mentor.leave_approve` plus rule 2. It is not a property
    of the projection, and B10.6's alternate is a THIRD kind of reader — a
    colleague who is neither the applicant nor an approver, given a reason to
    open the record because their name is in the Alternate Arrangements table.
    Handing them `_leave_out` would hand them a diagnosis.

    So the shape is the fence, not the call site: this model CANNOT carry a
    reason, a credit line, an approver's note or an attachment, because it has
    no field for one. Dates, the printed option, the state, who is asking them
    to cover, and the one alternate row addressed to them — which is everything
    needed to answer "can you take my Tuesday class" and nothing else.

    Split BEFORE the new readers land rather than after, deliberately: a
    reduced projection introduced alongside its first caller is a reduced
    projection that gets skipped the second time somebody is in a hurry.
    """

    id: str
    from_date: date
    to_date: date
    leave_kind: str | None
    status: str
    #: Who is asking. A name and nothing else — no designation, no department:
    #: the alternate is a colleague who already knows them.
    requester_name: str
    #: The one row of the Alternate Arrangements table addressed to this caller,
    #: not the whole table. The other rows name other colleagues' classes.
    alt_row: AltRow | None = None


def _requester_name(lr: LeaveRequest, db: Session) -> str:
    requester = db.get(User, lr.requester_user_id)
    return requester.name if requester else ""


def _leave_brief(lr: LeaveRequest, db: Session, *, alt_row: AltRow | None = None) -> LeaveBrief:
    """The reduced projection. Takes the caller's alternate row rather than
    finding it, because "which row is addressed to you" is B10.6's question and
    this module has no business answering it twice."""
    return LeaveBrief(
        id=lr.id,
        from_date=lr.from_date,
        to_date=lr.to_date,
        leave_kind=lr.leave_kind,
        status=lr.status.value,
        requester_name=_requester_name(lr, db),
        alt_row=alt_row,
    )


def _leave_out(lr: LeaveRequest, db: Session) -> LeaveOut:
    requester = db.get(User, lr.requester_user_id)
    # The PROGRAM DIRECTOR block prints only for a request that reached a final
    # decision. A first-of-two approval is a step, not a sanction, and printing a
    # name against it would show the form as signed off when it is not.
    director = None
    # REVOKED keeps its block: the sanction was given, by that person, at that
    # time, and then withdrawn — both halves are the record.
    if lr.status in (LeaveStatus.APPROVED, LeaveStatus.REJECTED, LeaveStatus.REVOKED):
        approver_id = lr.second_approver_user_id or lr.first_approver_user_id
        director = db.get(User, approver_id) if approver_id else None
    changer = db.get(User, lr.decision_changed_by_user_id) if lr.decision_changed_by_user_id else None
    return LeaveOut(
        id=lr.id,
        from_date=lr.from_date,
        to_date=lr.to_date,
        reason=lr.reason,
        status=lr.status.value,
        leave_kind=lr.leave_kind,
        credit=lr.credit,
        alt_name=lr.alt_name,
        alt_rows=[AltRow(**r) for r in (lr.alt_rows or [])],
        requester_name=requester.name if requester else "",
        requester_designation=requester.designation if requester else None,
        requester_department=requester.department if requester else None,
        signed_at=lr.signed_at,
        director_name=director.name if director else None,
        director_decided_at=lr.second_decided_at or lr.first_decided_at,
        director_note=lr.second_note or lr.first_note,
        first_signed_as=lr.first_signed_as,
        second_signed_as=lr.second_signed_as,
        decision_change_kind=lr.decision_change_kind,
        decision_changed_at=lr.decision_changed_at,
        decision_change_reason=lr.decision_change_reason,
        decision_changed_by_name=changer.name if changer else None,
    )


def _require_leave_approver(db: Session, session: dict) -> None:
    """The approver's gate: the Main Admin, or a faculty member the Main Admin
    granted `admin.leave_approvals` (2026-09-17).

    `require_mentor` first, so a STUDENT or an ALUMNI meets the role gate's own
    403 — the answer they get on every staff surface — and then the capability.
    The Main Admin passes on its baseline and never touches the grants table;
    a MENTOR passes on a live grant and nothing else: not on mentoring
    somebody (that was the retired `mentor.leave_approve`, derived and
    group-scoped, and it deadlocked with the second signature it belonged
    to), and not on a stray row still naming the retired key. From 2026-09-16
    to this date the function was `require_admin` alone.

    IT IS NOT ON THE SUBMIT PATH, AND THAT IS THE POINT. `POST /api/leaves`,
    `/mine` and `/cancel` are open to every signed-in account, faculty with no
    mentees included, because applying for your own leave is not an approver's
    act. Putting this on the form is how a new lecturer discovers they cannot
    ask for a day off.

    CALLED BEFORE ANY id IS LOOKED UP, on the decision path especially. A
    refusal that depended on whether the leave exists would turn this endpoint
    into the membership oracle `_assert_can_decide` flattens its 404s to
    prevent: the answer here is the same for every id, known or invented.

    THE REACH IS THE PROGRAMME. A leave request hangs on no rung of the spine
    that this router reads (the requester is a person, not a batch), so a
    grant is honoured wherever it was made and the queue a holder sees is the
    office's own; `_programme_wide` says so in the scope header rather than
    leaving it blank.
    """
    require_mentor(session)
    require_capability(db, session, LEAVE_APPROVAL_CAPABILITY)


@router.post("", response_model=LeaveOut, status_code=status.HTTP_201_CREATED)
def submit_leave(
    body: LeaveIn,
    background: BackgroundTasks,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveOut:
    """Apply for leave. THE REQUEST AND THE RESPONSE ARE THE SAME SHAPE THEY
    HAVE ALWAYS BEEN — B10.2 adds two REFUSALS, not a field.

    The owner's standing instruction is that the leave form and its buttons do
    not change, and `dates_run_forwards` on `LeaveIn` already established what
    that leaves room for: a request that must not exist can be refused before it
    is built, because a refusal needs no control on the form. These two cannot
    live on the schema — they read the database and the session user — so they
    are the first thing the endpoint does, before a row is constructed.

    BOTH ARE SILENT ON A DEPLOYMENT THAT HAS RECORDED NO ALLOWANCE for this
    applicant and this kind of leave. `app/leave_policy.py` says why at length;
    the short version is that a table nobody has filled in must not change what
    the form accepts, and `tests/test_leave_dates.py` submits a deliberately
    overlapping pair to hold that down.
    """
    refusal = submit_refusal(
        db,
        user_id=session["userId"],
        from_date=body.from_date,
        to_date=body.to_date,
        leave_kind=body.leave_kind,
    )
    if refusal:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=refusal)
    lr = LeaveRequest(
        requester_user_id=session["userId"],
        from_date=body.from_date,
        to_date=body.to_date,
        reason=body.reason,
        leave_kind=body.leave_kind,
        credit=body.credit,
        alt_name=body.alt_name,
        alt_rows=[r.model_dump() for r in body.alt_rows],
        status=LeaveStatus.SUBMITTED,
        # Submitting IS signing on this form — the applicant's signature block is
        # what sends it — so the stamp is taken here rather than left for a
        # second call that could never arrive.
        signed_at=datetime.now(timezone.utc),
    )
    db.add(lr)
    db.commit()
    db.refresh(lr)
    # B10.5, AND IT IS OFF UNLESS `LEAVE_MAIL_ENABLED` IS SET. After the commit,
    # never before: `deliver_once` commits its own MailLog, so calling it first
    # would commit a half-written request, and a mail about a request that has
    # not landed is worse than a late one. It never raises and it returns None
    # when the feature is off, so this line cannot change what this endpoint
    # answers — which is the whole point, on an endpoint that must not change.
    notify_transition(db, lr)
    # And everybody who can decide it (2026-09-24), when the applicant is
    # faculty. After the response, in its own session: there can be several
    # approvers, and the applicant's button must not wait on a mail server.
    background.add_task(notify_approvers_in_background, lr.id)
    return _leave_out(lr, db)


@router.get("/mine", response_model=list[LeaveOut])
def my_leaves(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[LeaveOut]:
    rows = db.scalars(
        select(LeaveRequest)
        .where(LeaveRequest.requester_user_id == session["userId"])
        .order_by(LeaveRequest.created_at.desc())
    ).all()
    return [_leave_out(lr, db) for lr in rows]


#: The two states an applicant may still withdraw from (B10.4). REJECTED and
#: APPROVED are both TERMINAL and for the same reason: somebody signed them.
#: Cancelling a rejection would let an applicant rewrite a decision that was
#: made about them, and cancelling a sanction would take back a day the office
#: has already granted — on a paper form that has been printed and filed.
CANCELLABLE = (LeaveStatus.SUBMITTED, LeaveStatus.FIRST_APPROVED)


@router.post("/{leave_id}/cancel", response_model=LeaveOut)
def cancel_leave(
    leave_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveOut:
    """Withdraw your own request (B10.4).

    THE APPLICANT'S OWN ENDPOINT, LIKE `/mine`, AND DELIBERATELY NOT AN
    APPROVER'S. `_require_leave_approver` is not called here and must not be:
    every signed-in account applies for its own leave, students included, and a
    capability gate on this path is how a student discovers they can ask for a
    day off and not take it back. `requester_user_id` is the only door, so an
    approver cannot withdraw somebody else's request either — that is what
    REJECT is for, and it leaves their name on it.

    THE REFUSAL FOR SOMEBODY ELSE'S LEAVE IS THE SAME 404 AS A MISSING ID, for
    `_assert_can_decide`'s reason: told apart, this endpoint is a membership
    oracle over the whole programme — guess ids, read the error, learn who has
    leave pending. A 403 here would be exactly that, and it would be worse than
    the decision path's because anybody signed in could ask.

    `LeaveStatus.CANCELLED` and its Postgres label have existed since
    `a80068bf03da`, and `leave_paper.SANCTIONED_WORDS` already prints
    "Cancelled": there is no enum work in B10.4 and nobody should add any.
    """
    lr = db.get(LeaveRequest, leave_id)
    if lr is None or lr.requester_user_id != session.get("userId"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Leave request not found."
        )
    if lr.status not in CANCELLABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Leave is {lr.status.value}; it can no longer be withdrawn. "
                "Only a request that is still awaiting a signature can be cancelled."
            ),
        )
    lr.status = LeaveStatus.CANCELLED
    # `updated_at` moves for any edit at all, so it cannot answer "when was this
    # withdrawn" on a row somebody touched afterwards. That is what this column
    # is for.
    lr.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(lr)
    return _leave_out(lr, db)


def _assert_can_decide(session: dict, lr: LeaveRequest, db: Session) -> str:
    """Staff only, and of staff only the Main Admin or a holder of
    `admin.leave_approvals`. RETURNS THE FUNCTION the caller is admitted in —
    `SIGNED_AS_MAIN_ADMIN` for the office, `SIGNED_AS_DELEGATE` for a granted
    faculty member — which is what the decision path stamps on the row and the
    paper prints beside the name.

    STILL THE ONE GATE ON READING SOMEBODY ELSE'S REQUEST. `leave_paper.py`,
    `leave_attachments.py`, `leave_alternate.py` and `leave_policy.py` import
    it, so "who may open this request" is answered in one place and the answer
    changed here once for all four.

    Every refusal is flattened to the SAME 404 the missing-leave path returns,
    so a caller cannot separate "that id exists but is not yours" from "no such
    leave". Left distinguishable, the endpoint is a membership oracle over the
    whole programme: guess ids, read the error, learn who has leave pending.
    `require_mentor` runs first so a STUDENT or an ALUMNI gets the role gate's
    own 403 — the same answer they get on every staff surface — and `lr` and
    `db` stay in the signature for the four importers.
    """
    require_mentor(session)
    if session["role"] == "ADMIN":
        return SIGNED_AS_MAIN_ADMIN
    # `has_capability`, not `require_capability`: a refusal here must be the
    # flattened 404 every other non-approver gets, never a 403 that names the
    # key and confirms the id exists.
    if has_capability(db, session, LEAVE_APPROVAL_CAPABILITY):
        return SIGNED_AS_DELEGATE
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leave request not found.")


def _programme_wide(response: Response) -> None:
    """The scope header the console's college read-out reads (B1.4). The
    Main Admin and a granted delegate both see the whole queue — a leave
    request hangs on no rung this router narrows by — so the word is always
    `programme`; stated rather than omitted, so the app bar does not read a
    missing header as "unknown reach"."""
    response.headers[SCOPE_HEADER] = "programme"


@router.get("/pending", response_model=list[LeaveOut])
def pending_leaves(
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[LeaveOut]:
    """Everything awaiting the office's decision, oldest first.

    FIRST_APPROVED is still in the filter: a row signed once under the old
    two-signature chain is a row still waiting for the office, and the one
    decision `decide_leave` takes now completes it. Own requests are excluded
    as they always were — the applicant reads those under `/mine`.
    """
    _require_leave_approver(db, session)
    _programme_wide(response)
    uid = session["userId"]
    query = (
        select(LeaveRequest)
        .where(
            LeaveRequest.status.in_([LeaveStatus.SUBMITTED, LeaveStatus.FIRST_APPROVED]),
            LeaveRequest.requester_user_id != uid,
        )
        .order_by(LeaveRequest.created_at)
    )
    return [_leave_out(lr, db) for lr in db.scalars(query).all()]


#: What `/history` answers when nobody asks for anything in particular — the
#: Approved and Rejected tabs, exactly the pair it has always returned.
HISTORY_DEFAULT_STATUSES = (LeaveStatus.APPROVED, LeaveStatus.REJECTED)
#: And what `?status=` may name. CANCELLED is settled but it is not DECIDED, so
#: it is reachable only by asking: a withdrawn request appearing unbidden in the
#: Rejected-and-Approved list would read as a decision somebody made.
HISTORY_STATUSES: dict[str, LeaveStatus] = {
    LeaveStatus.APPROVED.value: LeaveStatus.APPROVED,
    LeaveStatus.REJECTED.value: LeaveStatus.REJECTED,
    LeaveStatus.CANCELLED.value: LeaveStatus.CANCELLED,
    # Like CANCELLED, reachable only by asking: the default answer is the pair
    # it has always been, and the console reads revocations as their own tab.
    LeaveStatus.REVOKED.value: LeaveStatus.REVOKED,
}


@router.get("/history", response_model=list[LeaveOut])
def decided_leaves(
    response: Response,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="APPROVED, REJECTED, CANCELLED or REVOKED. Omit for the settled queue (approved and rejected).",
    ),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[LeaveOut]:
    """Requests that are settled — the Approved, Rejected and Cancelled tabs of
    the approvals screen. The Main Admin's, like `/pending`, and unnarrowed
    for the same reason. Own requests are excluded as they are from /pending —
    the applicant reads those under /mine, and the approvals screen is the
    other chair.

    `?status=` IS ADDITIVE AND THE DEFAULT IS UNCHANGED (B10.4). Omitting it
    returns exactly what this endpoint has always returned, in exactly the same
    order. It exists because a CANCELLED row is returned by NEITHER queue today
    — `/pending` filters SUBMITTED and FIRST_APPROVED, this one filtered
    APPROVED and REJECTED — so the console's "Cancelled" tab could not be lit by
    filtering on the client however hard it tried. Widening the default instead
    would have put withdrawn requests into the Approved and Rejected tabs of
    every screen already built against this endpoint.
    """
    _require_leave_approver(db, session)
    _programme_wide(response)
    if status_filter is None:
        wanted = list(HISTORY_DEFAULT_STATUSES)
    elif status_filter.upper() in HISTORY_STATUSES:
        wanted = [HISTORY_STATUSES[status_filter.upper()]]
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "status must be one of "
                + ", ".join(sorted(HISTORY_STATUSES))
                + ". SUBMITTED and FIRST_APPROVED are the pending queue."
            ),
        )
    uid = session["userId"]
    query = (
        select(LeaveRequest)
        .where(
            LeaveRequest.status.in_(wanted),
            LeaveRequest.requester_user_id != uid,
        )
        .order_by(
            # `cancelled_at` first and it changes nothing for the other two: it
            # is NULL on every row that was never withdrawn, so the coalesce
            # falls through to the decision stamps exactly as it did before. A
            # request cancelled AFTER its first signature sorts by the
            # withdrawal, which is the last thing that happened to it.
            func.coalesce(
                LeaveRequest.cancelled_at,
                LeaveRequest.decision_changed_at,
                LeaveRequest.second_decided_at,
                LeaveRequest.first_decided_at,
            ).desc(),
            LeaveRequest.created_at.desc(),
        )
        .limit(200)
    )
    return [_leave_out(lr, db) for lr in db.scalars(query).all()]


class LeaveDecisionIn(BaseModel):
    decision: str  # "APPROVE" | "REJECT"
    note: str | None = None


@router.post("/{leave_id}/decision", response_model=LeaveOut)
def decide_leave(
    leave_id: str,
    body: LeaveDecisionIn,
    background: BackgroundTasks,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveOut:
    """The office's ONE decision: SUBMITTED -> APPROVED or REJECTED.

    Role first, before any DB read: the refusal must not depend on whether
    this leave id exists, or the endpoint tells a caller which ids are real by
    which error comes back. Then `_assert_can_decide`, which hands back the
    FUNCTION it admitted on — stamped beside the signature below and printed
    on the paper (B10.1/B10.8).

    A ROW ALREADY SIGNED ONCE (FIRST_APPROVED, from before 2026-09-16) is
    completed by this same decision, written into the `second_*` slot so the
    first signer's stamp is not rewritten. Whether the first signature was the
    office's own no longer matters: one signature is the rule, and refusing
    the office its own second stroke was the deadlock the rule removed.
    """
    _require_leave_approver(db, session)
    lr = db.get(LeaveRequest, leave_id)
    if lr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leave request not found.")
    signed_as = _assert_can_decide(session, lr, db)
    uid = session["userId"]
    if lr.requester_user_id == uid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot approve your own leave."
        )
    decision = body.decision.upper()
    if decision not in ("APPROVE", "REJECT"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="decision must be APPROVE or REJECT.",
        )
    now = datetime.now(timezone.utc)
    verdict = LeaveDecision.APPROVED if decision == "APPROVE" else LeaveDecision.REJECTED
    outcome = LeaveStatus.APPROVED if decision == "APPROVE" else LeaveStatus.REJECTED

    if lr.status == LeaveStatus.SUBMITTED:
        lr.first_approver_user_id = uid
        lr.first_decided_at = now
        lr.first_note = body.note
        lr.first_signed_as = signed_as
        lr.first_decision = verdict
        lr.status = outcome
    elif lr.status == LeaveStatus.FIRST_APPROVED:
        lr.second_approver_user_id = uid
        lr.second_decided_at = now
        lr.second_note = body.note
        lr.second_signed_as = signed_as
        lr.second_decision = verdict
        lr.status = outcome
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Leave is {lr.status.value}; no decision possible.",
        )

    db.commit()
    db.refresh(lr)
    # B10.5, off by default, after the commit. See `submit_leave`. CANCELLED
    # has no message at all: the applicant withdrew it themselves and does not
    # need telling what they just did.
    notify_transition(db, lr)
    if lr.status == LeaveStatus.APPROVED:
        # "X is on leave today" goes to the other faculty ON THE LEAVE DAY,
        # never because the office pressed Sanction (2026-09-24). This sends
        # only when today is already one of the leave's days — the morning
        # job (`python -m app.leave_today_job`) has run by then and would not
        # otherwise reach it until tomorrow. A leave approved ahead of time
        # sends nothing here.
        background.add_task(announce_if_on_leave_today, lr.id)
    return _leave_out(lr, db)


# --------------------------------------------------------------------------- #
# CHANGING A DECISION AFTER IT WAS MADE (2026-10-13)
# --------------------------------------------------------------------------- #

CHANGE_REVOKE = "REVOKE"
CHANGE_REOPEN = "REOPEN"
#: Where each change may start from. REVOKE undoes a sanction; REOPEN puts a
#: refusal — or a revocation, when the office changes its mind a second time —
#: back in the queue for a fresh decision. Nothing else moves: a pending
#: request is decided with `/decision`, and a CANCELLED one was the applicant's
#: own act, which the office does not undo for them.
CHANGE_FROM: dict[str, tuple[LeaveStatus, ...]] = {
    CHANGE_REVOKE: (LeaveStatus.APPROVED,),
    CHANGE_REOPEN: (LeaveStatus.REJECTED, LeaveStatus.REVOKED),
}
#: What the row's `decision_change_kind` reads after each.
CHANGE_KIND = {CHANGE_REVOKE: "REVOKED", CHANGE_REOPEN: "REOPENED"}


class LeaveDecisionChangeIn(BaseModel):
    action: str  # "REVOKE" | "REOPEN"
    #: Required, in words. Six months later somebody will ask why a sanctioned
    #: leave was pulled, and the row is the only place the answer can live.
    reason: str = Field(min_length=1, max_length=2000)
    #: Revoking a leave that has already begun turns those days into absence
    #: without sanction. The office must say it means that; see below.
    confirm_started: bool = False


def _decision_image(lr: LeaveRequest) -> dict:
    """The decision as it stood, for the audit row's before/after. Dates, the
    state and who signed — NEVER `reason`, which is the applicant's free text
    and routinely medical."""

    def stamp(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "status": lr.status.value,
        "from_date": lr.from_date.isoformat(),
        "to_date": lr.to_date.isoformat(),
        "first_approver_user_id": lr.first_approver_user_id,
        "first_decision": lr.first_decision.value if lr.first_decision else None,
        "first_decided_at": stamp(lr.first_decided_at),
        "first_note": lr.first_note,
        "first_signed_as": lr.first_signed_as,
        "second_approver_user_id": lr.second_approver_user_id,
        "second_decision": lr.second_decision.value if lr.second_decision else None,
        "second_decided_at": stamp(lr.second_decided_at),
        "second_note": lr.second_note,
        "second_signed_as": lr.second_signed_as,
        "decision_revision": lr.decision_revision or 0,
    }


def _clear_decision(lr: LeaveRequest) -> None:
    """A REOPENED request is a pending request: no signature on it, so the next
    `/decision` writes `first_*` onto a clean row and the paper and console —
    which read `second or first` — cannot print a stale legacy second signer
    over the new decider. The cleared stamps survive in the audit row."""
    lr.first_approver_user_id = None
    lr.first_decision = None
    lr.first_decided_at = None
    lr.first_note = None
    lr.first_signed_as = None
    lr.second_approver_user_id = None
    lr.second_decision = None
    lr.second_decided_at = None
    lr.second_note = None
    lr.second_signed_as = None


@router.post("/{leave_id}/change-decision", response_model=LeaveOut)
def change_decision(
    leave_id: str,
    body: LeaveDecisionChangeIn,
    request: Request,
    background: BackgroundTasks,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveOut:
    """Take back a decision: REVOKE a sanction, or REOPEN a refusal or a
    revocation for a fresh decision.

    THE SAME PEOPLE WHO DECIDE MAY CHANGE A DECISION, AND NOBODY ELSE — the
    gate is `decide_leave`'s, in the same order (role first, before any id is
    read, then `_assert_can_decide`'s flattened 404), and nobody changes a
    decision on their own request.

    A REASON IN WORDS, ALWAYS. It is stamped on the row (the applicant reads it
    on their screen) and the decision it undid is written to the audit trail
    as a before-image — the row keeps only the LATEST change, and a reopen
    clears the signatures so the next decision lands on a clean row.

    REVOKING A LEAVE THAT HAS ALREADY BEGUN is a different act from revoking
    one that has not: the applicant may already be away on the office's word,
    and those days become absence without sanction. So once the first day has
    arrived (in the college's zone, `local_today`), only the Main Admin may do
    it — a delegate is refused in words — and only with `confirm_started`;
    without it the answer is a 409 naming the days, which is what the console
    draws as its confirmation.

    The leave balance is NOT touched: `consumed_days` is the office's own
    number and nothing writes it on approval either, so nothing un-writes it
    here. REOPEN refuses a request whose days now clash with another live
    request of the same applicant, `submit_refusal`'s overlap rule, because two
    live requests for one day is the state that rule exists to prevent.
    """
    _require_leave_approver(db, session)
    # FOR UPDATE: two approvers pressing Revoke at the same moment would both
    # pass the status check on an unlocked read and write two audit rows. The
    # second now waits, re-reads REVOKED, and is answered 409.
    lr = db.get(LeaveRequest, leave_id, with_for_update=True)
    if lr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leave request not found.")
    _assert_can_decide(session, lr, db)
    uid = session["userId"]
    if lr.requester_user_id == uid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change a decision on your own leave.",
        )
    action = body.action.strip().upper()
    if action not in CHANGE_FROM:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="action must be REVOKE or REOPEN.",
        )
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Say why the decision is being changed. The applicant reads it.",
        )
    if lr.status not in CHANGE_FROM[action]:
        allowed = " or ".join(s.value for s in CHANGE_FROM[action])
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Leave is {lr.status.value}; only a {allowed} request can be "
            + ("revoked." if action == CHANGE_REVOKE else "reopened."),
        )

    if action == CHANGE_REVOKE and local_today() >= lr.from_date:
        span = (
            lr.from_date.isoformat()
            if lr.from_date == lr.to_date
            else f"{lr.from_date.isoformat()} to {lr.to_date.isoformat()}"
        )
        if session.get("role") != "ADMIN":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This leave ({span}) has already begun. Only the Main Admin can "
                "revoke a leave once its first day has arrived.",
            )
        if not body.confirm_started:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This leave ({span}) has already begun. Revoking it now leaves "
                "the days already taken without a sanction. Confirm to revoke anyway.",
            )

    if action == CHANGE_REOPEN:
        clash = overlapping_request(
            db, lr.requester_user_id, lr.from_date, lr.to_date, exclude_id=lr.id
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The applicant already has another live request for "
                    f"{clash.from_date.isoformat()} to {clash.to_date.isoformat()}, which "
                    "overlaps these days. That one must be settled first."
                ),
            )

    before = _decision_image(lr)
    if action == CHANGE_REVOKE:
        lr.status = LeaveStatus.REVOKED
    else:
        lr.status = LeaveStatus.SUBMITTED
        _clear_decision(lr)
    lr.decision_change_kind = CHANGE_KIND[action]
    lr.decision_changed_at = datetime.now(timezone.utc)
    lr.decision_changed_by_user_id = uid
    lr.decision_change_reason = reason
    lr.decision_revision = (lr.decision_revision or 0) + 1
    after = _decision_image(lr)
    after["change_reason"] = reason
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="leave_request", entity_id=lr.id,
        action=f"DECISION_{CHANGE_KIND[action]}",
        before=before, after=after,
        event_type=f"leave.decision_{CHANGE_KIND[action].lower()}",
        payload={"leave_id": lr.id, "status": lr.status.value},
    )
    db.commit()
    db.refresh(lr)
    # After the commit, like every leave mail: a message about a change that
    # has not landed is worse than a late one.
    notify_decision_changed(db, lr)
    if lr.status is LeaveStatus.SUBMITTED:
        # A reopened request is waiting again: tell whoever can decide it, as
        # a fresh application does (`notify_approvers` keeps it to faculty
        # requests and to the mail switch, and keys it by revision).
        background.add_task(notify_approvers_in_background, lr.id)
    return _leave_out(lr, db)
