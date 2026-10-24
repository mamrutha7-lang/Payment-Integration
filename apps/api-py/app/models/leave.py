"""Leave requests — the two-approver workflow (ported from Prisma
`LeaveRequest`). A request needs two distinct signatures: SUBMITTED ->
FIRST_APPROVED (first approver) -> APPROVED (second). A rejection at either
stage -> REJECTED. LeaveDecision has no PENDING: a null decision already says
the approver has not looked yet.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class LeaveStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    FIRST_APPROVED = "FIRST_APPROVED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    #: The office took back a sanction it had given (2026-10-13). Not LIVE —
    #: the days are free again — and reachable only from APPROVED.
    REVOKED = "REVOKED"


class LeaveDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# --------------------------------------------------------------------------- #
# WHICH FUNCTION SIGNED (B10.1) — the vocabulary for `first_signed_as` /
# `second_signed_as` below.
# --------------------------------------------------------------------------- #
#
# THREE VALUES, AND THE TWO THAT ARE MISSING ARE MISSING BECAUSE THEY DO NOT
# EXIST. 04-backend-changes.md asks for "Mentor / HOD / Principal / Main Admin".
# There is no HOD ACCOUNT in this product — `departments.head` is a free-text
# String, "as printed on the leave form's department line", with no `head_user_id`
# and no `Role.HOD` — and there is no principal concept anywhere. Minting either
# word here would put a job title on the college's own signed form that nothing
# in the database can substantiate, which is worse than the NULL it replaces.
#
# What the product does have is a GRANT: `admin.leave_approvals` handed to a
# faculty account in Governance, with a reason and an audit row (it was a
# scoped grant of `mentor.leave_approve` until 2026-09-16, and nothing at all
# for the day between that key's retirement and this one's arrival). That is a
# function in every sense that matters here — somebody decided this person
# signs leave — and it is what DELEGATE names.
#
# A plain str, not an enum: see the column comments. Adding a fourth value is a
# data change and a label in `app/leave_paper.py::SIGNED_AS_LABELS`, which
# `tests/test_leave_chain.py` pins against this tuple so the paper can never
# meet a function it has no word for.
SIGNED_AS_MENTOR = "MENTOR"
#: Admitted by a GRANT of `admin.leave_approvals` (2026-09-17; a scoped grant
#: of `mentor.leave_approve` before 2026-09-16), not by mentoring the applicant
#: and not by being the office.
SIGNED_AS_DELEGATE = "DELEGATE"
SIGNED_AS_MAIN_ADMIN = "MAIN_ADMIN"
SIGNED_AS: tuple[str, ...] = (SIGNED_AS_MENTOR, SIGNED_AS_DELEGATE, SIGNED_AS_MAIN_ADMIN)


# One shared Enum instance for both decision columns, so the PG type is created
# exactly once (two separate Enum(...) would each try to CREATE TYPE).
_LEAVE_DECISION = Enum(LeaveDecision, name="leave_decision")


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        Index("ix_leave_status_created", "status", "created_at"),
        Index("ix_leave_requester", "requester_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    requester_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    from_date: Mapped[date] = mapped_column(Date)
    to_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String)
    status: Mapped[LeaveStatus] = mapped_column(
        Enum(LeaveStatus, name="leave_status"), default=LeaveStatus.SUBMITTED, server_default="SUBMITTED"
    )

    first_approver_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    first_decision: Mapped[LeaveDecision | None] = mapped_column(_LEAVE_DECISION, nullable=True)
    first_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_note: Mapped[str | None] = mapped_column(String, nullable=True)
    # WHICH FUNCTION SIGNED, beside which ACCOUNT signed (B10.1).
    #
    # The approver columns above record a person. That is enough to decide
    # whether a second signature came from a different human, and it is not
    # enough to say what the paper needs to say: the same account can be the
    # applicant's mentor on one request and the office signing as the second
    # approver on another, and six months later the row cannot tell you which.
    # These two columns record the function the signer was ACTING IN at the
    # moment they signed, stamped by the decision path and never inferred
    # afterwards from a group that may since have changed.
    #
    # A PLAIN String, not a PG enum, and not because of a house preference: the
    # set of functions is exactly what B10.1 is still arguing about (there is no
    # HOD ACCOUNT in this product at all — `departments.head` is free text — and
    # no principal concept), so it must stay a data change. AGENTS.md's enum
    # gotcha (a) applies on top: adding an enum COLUMN to an existing table does
    # not create the type.
    #
    # NULL is the state of every row written before this column existed, and of
    # every unsigned step. It must render as nothing at all — never as a guessed
    # function — which is why `app/leave_paper.py` may print this only where it
    # is set.
    first_signed_as: Mapped[str | None] = mapped_column(String(32), nullable=True)

    second_approver_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    second_decision: Mapped[LeaveDecision | None] = mapped_column(_LEAVE_DECISION, nullable=True)
    second_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    second_note: Mapped[str | None] = mapped_column(String, nullable=True)
    #: The second signature's function. See `first_signed_as` above.
    second_signed_as: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- the official BGSCET form -------------------------------------------
    # Which of the printed options is being applied for: the form lists Casual
    # Leave / Permission / OOD / RH / LOP and strikes off the rest, so this is a
    # selection among them rather than a free leave-type vocabulary.
    leave_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    # The form's "Credit" cell. A free string, because the sheet accepts one.
    credit: Mapped[str | None] = mapped_column(String, nullable=True)
    # "Alternate Arrangements: (For Department purpose)" — a name, then a table
    # of Date / Staff Name / Class / Time / Remarks. Stored as JSON because the
    # rows are a printed table with no life outside this document; giving them
    # their own table would imply queries nobody makes.
    #
    # B10.6 ADDS KEYS TO THESE OBJECTS AND NEEDS NO MIGRATION — `user_id` (the
    # faculty account the row names) and `accepted_at` (when that colleague
    # agreed to cover). JSONB takes them silently, which is the trap: `_leave_out`
    # in `app/routers/leave.py` does `AltRow(**r)` over rows that were STORED
    # BEFORE the field existed, so ANY new `AltRow` field MUST carry a default
    # or every pre-existing request 500s the moment it is read. The same reason
    # `app/leave_paper.py::_alt_cells` reads its five keys through `getattr` and
    # will ignore both new ones without a change.
    alt_name: Mapped[str | None] = mapped_column(String, nullable=True)
    alt_rows: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # When the applicant signed. The form's SIGNATURE OF STAFF block prints from
    # this and the requester's name; a request with no timestamp is a draft.
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the APPLICANT withdrew it (B10.4). `LeaveStatus.CANCELLED` has existed
    # on this enum and in the Postgres type since a80068bf03da, and
    # `leave_paper.SANCTIONED_WORDS` already prints "Cancelled" — so B10.4 needs
    # no enum work whatever, and nobody should "helpfully" add the value again.
    # What was missing is WHEN: `updated_at` moves for any edit at all, so it
    # cannot answer "when was this withdrawn" on a row somebody touched
    # afterwards. Nullable, and NULL on every row that was never cancelled.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # --- a decision changed after it was made (2026-10-13) ------------------
    # The LATEST change only: REVOKED (APPROVED -> REVOKED) or REOPENED
    # (REJECTED or REVOKED -> SUBMITTED, back in the queue). The full history,
    # including the decision each change undid, is the before-image written to
    # `redesign_audit_events` by `change_decision` — a reopen clears the
    # decision columns above so the next decision lands on a clean row, and
    # the audit row is where the old one survives. NULL on every row whose
    # decision was never changed.
    decision_change_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_changed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Required in words when the change is made, and shown to the applicant.
    decision_change_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    #: How many times a decision on this row has been changed. Part of the mail
    #: dedupe key, so a request sanctioned, revoked, reopened and sanctioned
    #: again is told about the second sanction too.
    decision_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
