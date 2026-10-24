"""The Main Admin's two ways to delete, and the code that guards the permanent
one (2026-09-16).

    POST  /api/admin/deletions/code                     mail ME a six-digit code
    GET   /api/admin/users/{id}/delete-plan             what a permanent delete would take
    GET   /api/admin/students/{id}/delete-plan          (the same, by student id)
    POST  /api/admin/users/{id}/delete       {code, reason}   gone for good
    POST  /api/admin/students/{id}/delete    {code, reason}
    POST  /api/admin/users/{id}/remove       {reason}         off every screen, rows kept
    POST  /api/admin/students/{id}/remove    {reason}
    POST  /api/admin/users/{id}/restore                        back, nothing lost
    POST  /api/admin/students/{id}/restore
    GET   /api/admin/colleges/{id}/delete-plan
    POST  /api/admin/colleges/{id}/delete    {code, reason}   the structure, gone

MAIN ADMIN ONLY, every one of them (`require_admin`), and not a capability:
the owner's rule is that deleting a person or a college is the office's act,
and a capability is a thing the office hands to somebody else. `AGENTS.md`
records why there was NO delete here until now; this module is the owner
asking for one, built the way the purge modules are built rather than the way
the 2026-09-10 delete was.

TWO ANSWERS, AND THEY ARE NOT DEGREES OF ONE THING. REMOVE writes
`users.deleted_at` (`app/models/user.py` says why it is a column beside
`disabled_at`): the person leaves every roster and picker, cannot sign in by
any door (`User.barred_at`), every row they own stays, and RESTORE undoes it
with nothing lost. It asks for a REASON in words, disabling's rule. DELETE is
`app/account_deletion.py`: the rows, the files, the recordings, gone, and it
asks for the CODE.

THE CODE IS THE SECOND FACTOR, AND IT IS MAILED TO THE OFFICE ACCOUNT'S OWN
ADDRESS, never one from the request. Being signed in is not enough for an
act nobody can undo: the cookie is on whichever machine the office left open,
the mailbox is not. Six digits, `settings.otp_code_minutes` to live, spent by
ONE act — `consume_user_code`'s atomic UPDATE is the arbiter, so two deletes
racing on one code get one delete. A wrong code is a 403 that names nothing
about the target and spends nothing; the code is spent BEFORE anything is
destroyed and never after. Requests for a code are throttled per admin, so
the button cannot be turned into a mail cannon against the office's inbox.

THE PLAN ENDPOINTS ARE WHAT THE DIALOG SHOWS BEFORE THE BUTTON. They count,
they destroy nothing, and they are the same walk the delete runs — so what
the office reads is what happens. A college plan also carries the BLOCKERS
(people seated or filed under it, open applications), and the delete refuses
while any remain, inside its own transaction, whatever the dialog said a
minute earlier.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import account_deletion, account_links, college_deletion
from ..architecture_events import record_change
from ..config import settings
from ..db import get_db
from ..identity import get_current_session
from ..mentor_history import release_mentees_of
from ..models.auth_token import PURPOSE_DELETE_CODE
from ..models.institution import College
from ..models.user import Role, Student, User
from ..security import note_revocation
from .mentor import require_admin
from .passwords import _Throttle
from .student import clear_leaderboard_cache

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-deletion"])

#: How many deletion codes one office account may ask for per hour. Six is
#: "I mistyped it twice and the mail was slow"; sixty would be a mail cannon.
DELETE_CODES_PER_HOUR = 6
_code_requests = _Throttle(DELETE_CODES_PER_HOUR, 3600)


def reset_throttle() -> None:
    """For tests."""
    _code_requests.reset()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- schemas --


def _clean_reason(v: object) -> str:
    text = " ".join(str(v or "").split())
    if len(text) < 3:
        raise ValueError("a reason is required, in words")
    return text[:500]


class DeleteCodeOut(BaseModel):
    message: str
    expires_in_minutes: int


class PermanentDeleteIn(BaseModel):
    """The code from the mail and the reason for the record. Both required."""

    code: str = Field(min_length=6, max_length=6)
    reason: str

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v: object) -> str:
        digits = "".join(ch for ch in str(v or "") if ch.isdigit())
        if len(digits) != 6:
            raise ValueError("the code is six digits")
        return digits

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: object) -> str:
        return _clean_reason(v)


class RemoveIn(BaseModel):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: object) -> str:
        return _clean_reason(v)


class AccountDeletePlanOut(BaseModel):
    """`account_deletion.AccountPlan.as_dict()` plus the sentences the dialog
    prints. Built server-side so the words and the numbers come from one
    place; a client that composed its own from `rows` would drift."""

    kind: str  # "student" | "faculty" | "alumni"
    user_id: str
    student_id: str | None
    name: str
    email: str
    role: str
    rows: dict[str, int]
    cleared: dict[str, int]
    total_rows: int
    files: int
    audio_sessions: int
    platform_calls: int
    s3_objects: int
    mentees_released: int
    consequences: list[str]


class AccountDeletedOut(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    rows_deleted: int
    files_deleted: int
    files_failed: list[str]
    mentees_released: int
    detail: str


class RemovalOut(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    removed: bool
    deleted_at: datetime | None = None
    delete_reason: str | None = None
    #: Still disabled underneath, which RESTORE does not undo — see the model.
    disabled: bool = False
    links_revoked: int = 0
    mentees_released: int = 0
    detail: str


class CollegeDeletePlanOut(BaseModel):
    college_id: str
    code: str
    name: str
    rows: dict[str, int]
    cleared: dict[str, int]
    blockers: dict[str, int]
    total_rows: int
    deletable: bool
    consequences: list[str]
    #: The refusal in words when `deletable` is false, else null.
    refusal: str | None


class CollegeDeletedOut(BaseModel):
    college_id: str
    code: str
    name: str
    rows_deleted: int
    detail: str


# --------------------------------------------------------------- helpers --


def _user_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    return user


def _student_user_or_404(db: Session, student_id: str) -> User:
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    return _user_or_404(db, student.user_id)


def _college_or_404(db: Session, college_id: str) -> College:
    college = db.get(College, college_id)
    if college is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="College not found.")
    return college


def _refuse_office_or_self(user: User, session: dict) -> None:
    if user.role is Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "The Main Admin account cannot be removed or deleted - it is the only "
                "way into the console. Hand the office over first (demote it to MENTOR "
                "with `python -m app.grant_access`)."
            ),
        )
    if user.id == session.get("userId"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="You cannot remove or delete the account you are signed in with.",
        )


def _kind_of(user: User) -> str:
    if user.role is Role.STUDENT:
        return "student"
    if user.role is Role.MENTOR:
        return "faculty"
    return user.role.value.lower()


#: The tables the dialog names in words, in the order a reader wants them.
#: Anything not here is summed into "other records".
_ROW_LABELS: tuple[tuple[str, str], ...] = (
    ("uploads", "uploaded document"),
    ("resumes", "resume"),
    ("registration_documents", "registration document"),
    ("interview_sessions", "mock interview record"),
    ("interview_score_summaries", "interview score"),
    ("mentor_notes", "meeting note"),
    ("swoc_entries", "SWOC line"),
    ("semester_results", "semester result"),
    ("attendance_records", "attendance record"),
    ("student_badges", "badge"),
    ("badge_evidence", "badge evidence item"),
    ("leave_requests", "leave request"),
    ("staff_upskilling_certs", "upskilling certificate"),
    ("staff_signatures", "signature image"),
    ("capability_grants", "function grant"),
    ("redesign_mentor_notebook_entries", "notebook entry"),
    ("conversations", "assistant conversation"),
)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _account_consequences(plan: account_deletion.AccountPlan, kind: str) -> list[str]:
    out: list[str] = []
    named = 0
    for table, noun in _ROW_LABELS:
        n = plan.rows.get(table, 0)
        if n:
            named += n
            if kind == "faculty" and table == "mentor_notes":
                out.append(f"{_plural(n, 'meeting note')} they wrote about students - deleted with them")
            else:
                out.append(_plural(n, noun))
    other = plan.total_rows - named
    if other > 0:
        out.append(f"{_plural(other, 'other record')} (sign-ins, links, milestones, skills)")
    if plan.files:
        out.append(f"{_plural(plan.files, 'stored file')} destroyed on the volume")
    if plan.audio_sessions:
        out.append(f"recorded interview audio swept for {_plural(plan.audio_sessions, 'session')}")
    if plan.platform_calls:
        out.append(f"{_plural(plan.platform_calls, 'platform call recording')}")
    if plan.mentees_released:
        out.append(
            f"{_plural(plan.mentees_released, 'mentee')} released to the unassigned pool "
            "(their own records stay)"
        )
    survivors = []
    if plan.cleared.get("swoc_entries.author_user_id"):
        survivors.append(f"{_plural(plan.cleared['swoc_entries.author_user_id'], 'SWOC line')} they wrote stay, authorless")
    signed = plan.cleared.get("leave_requests.first_approver_user_id", 0) + plan.cleared.get(
        "leave_requests.second_approver_user_id", 0
    )
    if signed:
        survivors.append(f"{_plural(signed, 'leave decision')} they signed keep the decision and lose the signer's name")
    if plan.cleared.get("redesign_audit_events.actor_user_id"):
        survivors.append("the audit trail keeps every act, without their name")
    out.extend(survivors)
    return out


def _college_consequences(plan: college_deletion.CollegePlan) -> list[str]:
    labels = {
        "departments": "department",
        "academic_courses": "course",
        "academic_specializations": "specialization",
        "cohorts": "batch",
        "interview_policies": "interview policy",
        "academic_calendar": "calendar day",
        "stage_rules": "stage rule",
        "badge_course_map": "badge-course mapping",
        "alert_rule_configs": "alert rule",
    }
    out = [_plural(n, labels[t]) for t, n in sorted(plan.rows.items()) if t in labels and n]
    unscoped = {
        "jobs": "job posting(s) become programme-wide",
        "interview_tracks": "interview track mapping(s) come off (they stop preselecting anybody)",
        "approved_certifications": "approved certification(s) lose their college",
        "placement_criteria": "placement criteria row(s) lose their college",
        "interview_bank_questions": "bank question(s) lose their college",
        "import_runs": "spreadsheet run(s) lose their college",
        "registrations": "decided application(s) lose their college",
    }
    per_table: dict[str, int] = {}
    for key, n in plan.cleared.items():
        per_table[key.split(".")[0]] = per_table.get(key.split(".")[0], 0) + n
    for table, phrase in unscoped.items():
        if per_table.get(table):
            out.append(f"{per_table[table]} {phrase}")
    return out


def _consume_or_403(db: Session, admin_id: str, code: str) -> None:
    """Spend the code, or refuse without naming the target. Committed on its
    own so a delete that fails afterwards cannot un-spend it — a code is one
    act, and "it did not work, try the same code again" is the replay every
    one-time code exists to refuse."""
    if not account_links.consume_user_code(db, admin_id, PURPOSE_DELETE_CODE, code):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "That code is not right, has expired, or was already used. Ask for a "
                "new one - it is emailed to the office account's own address."
            ),
        )
    db.commit()


# ------------------------------------------------------------- the code --


@router.post("/deletions/code", response_model=DeleteCodeOut)
def send_delete_code(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> DeleteCodeOut:
    """Mail the signed-in Main Admin a code that authorises ONE permanent
    delete. To the address ON THE ACCOUNT, never one from the request."""
    require_admin(session)
    admin = _user_or_404(db, session["userId"])
    if not _code_requests.allow(admin.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many codes asked for. The last one sent still works for "
                f"{settings.otp_code_minutes} minutes; after that, try again in an hour."
            ),
        )
    account_links.issue_delete_code(db, admin)
    log.info("deletion code sent to %s", admin.email)
    return DeleteCodeOut(
        message=(
            f"We have emailed a code to {admin.email}. It expires in "
            f"{settings.otp_code_minutes} minutes and works for one deletion."
        ),
        expires_in_minutes=settings.otp_code_minutes,
    )


# --------------------------------------------------------- the plans --


def _account_plan_out(db: Session, user: User, session: dict) -> AccountDeletePlanOut:
    _refuse_office_or_self(user, session)
    try:
        plan = account_deletion.build_plan(db, user, acting_user_id=session.get("userId"))
    except account_deletion.DeletionRefused as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    kind = _kind_of(user)
    return AccountDeletePlanOut(kind=kind, consequences=_account_consequences(plan, kind), **plan.as_dict())


@router.get("/users/{user_id}/delete-plan", response_model=AccountDeletePlanOut)
def user_delete_plan(
    user_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AccountDeletePlanOut:
    require_admin(session)
    return _account_plan_out(db, _user_or_404(db, user_id), session)


@router.get("/students/{student_id}/delete-plan", response_model=AccountDeletePlanOut)
def student_delete_plan(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AccountDeletePlanOut:
    require_admin(session)
    return _account_plan_out(db, _student_user_or_404(db, student_id), session)


# ------------------------------------------------ the permanent delete --


def _delete_account(
    db: Session, user: User, body: PermanentDeleteIn, session: dict, request: Request
) -> AccountDeletedOut:
    _refuse_office_or_self(user, session)
    kind = _kind_of(user)
    try:
        plan = account_deletion.build_plan(db, user, acting_user_id=session.get("userId"))
    except account_deletion.DeletionRefused as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    before = {
        "email": user.email,
        "name": user.name,
        "role": user.role.value,
        "student_id": plan.doomed.student_id,
        "usn": db.scalar(select(Student.usn).where(Student.id == plan.doomed.student_id))
        if plan.doomed.student_id
        else None,
    }
    # THE CODE, spent before a byte is touched and after every refusal above:
    # a refusal must not cost the office a code, and a delete must never run
    # without one.
    _consume_or_403(db, session["userId"], body.code)
    user_id, email, name = user.id, user.email, user.name
    try:
        failures = account_deletion.execute(db, plan)
    except account_deletion.DeletionRefused as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    # The account row is gone, so the audit row is written with the snapshot
    # taken above; `record_change` reads only the session for the actor.
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="user", entity_id=user_id, action="DELETE_PERMANENT",
        before=before,
        after={"reason": body.reason, "plan": plan.as_dict(), "files_failed": failures},
        event_type="user.delete_permanent",
        payload={"email": email, "role": before["role"], "reason": body.reason, "kind": kind},
    )
    db.commit()
    note_revocation(user_id, 10**9, disabled=True)
    if plan.doomed.student_id:
        clear_leaderboard_cache()
    log.warning(
        "account %s (%s) PERMANENTLY DELETED by %s: %s (%d rows, %d files)",
        email, before["role"], session.get("email"), body.reason, plan.total_rows, plan.files,
    )
    return AccountDeletedOut(
        user_id=user_id, email=email, name=name, role=before["role"],
        rows_deleted=plan.total_rows, files_deleted=plan.files - len(failures),
        files_failed=failures, mentees_released=plan.mentees_released,
        detail=(
            f"{name} ({email}) has been deleted for good: {plan.total_rows} record(s) and "
            f"{plan.files} file(s)."
            + (f" {len(failures)} file(s) could not be destroyed and are listed." if failures else "")
            + (
                f" {plan.mentees_released} student(s) were released to the unassigned pool."
                if plan.mentees_released
                else ""
            )
        ),
    )


@router.post("/users/{user_id}/delete", response_model=AccountDeletedOut)
def delete_user(
    user_id: str,
    body: PermanentDeleteIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AccountDeletedOut:
    require_admin(session)
    return _delete_account(db, _user_or_404(db, user_id), body, session, request)


@router.post("/students/{student_id}/delete", response_model=AccountDeletedOut)
def delete_student(
    student_id: str,
    body: PermanentDeleteIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AccountDeletedOut:
    require_admin(session)
    return _delete_account(db, _student_user_or_404(db, student_id), body, session, request)


# ------------------------------------------------- remove and restore --


def _removal_out(user: User, *, links_revoked: int = 0, mentees_released: int = 0, detail: str) -> RemovalOut:
    return RemovalOut(
        user_id=user.id, email=user.email, name=user.name, role=user.role.value,
        removed=user.deleted_at is not None, deleted_at=user.deleted_at,
        delete_reason=user.delete_reason, disabled=user.disabled_at is not None,
        links_revoked=links_revoked, mentees_released=mentees_released, detail=detail,
    )


def _holds_a_student_record(db: Session, user_id: str) -> bool:
    """Whether the account has a `students` row, which is what puts it on a
    classmate's leaderboard. The row and not the role: a graduate is ALUMNI
    and keeps the row and its batch, and the boards' roster reads the row, so
    removing one reaches the boards like removing a student does. The same
    test the permanent delete makes with `plan.doomed.student_id`."""
    return db.scalar(select(Student.id).where(Student.user_id == user_id).limit(1)) is not None


def _remove_account(db: Session, user: User, body: RemoveIn, session: dict, request: Request) -> RemovalOut:
    _refuse_office_or_self(user, session)
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{user.email} was already removed on {user.deleted_at:%Y-%m-%d}.",
        )
    before = {"deleted_at": None, "delete_reason": None, "token_version": int(user.token_version or 0)}
    user.deleted_at = _now()
    user.deleted_by_user_id = session.get("userId")
    user.delete_reason = body.reason
    user.token_version = int(user.token_version or 0) + 1
    revoked = account_links.revoke_all_user_tokens(db, user.id)
    released: list[str] = []
    if user.role is Role.MENTOR:
        # Their mentees go back to the pool exactly as on a disable (B9.1):
        # a removed faculty member's group is a group nobody can open.
        released = release_mentees_of(
            db, faculty_user_id=user.id, by_user_id=session.get("userId"),
            reason=f"faculty account removed: {body.reason}", session=session, request=request,
        )
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="user", entity_id=user.id, action="REMOVE",
        before=before,
        after={
            "deleted_at": user.deleted_at.isoformat(), "delete_reason": body.reason,
            "token_version": user.token_version, "links_revoked": revoked,
            "mentees_released": len(released),
        },
        event_type="user.remove",
        payload={"email": user.email, "role": user.role.value, "reason": body.reason},
    )
    db.commit()
    note_revocation(user.id, user.token_version, disabled=True)
    if _holds_a_student_record(db, user.id):
        # Off every screen means off classmates' leaderboards on the next
        # read, not after the board's cache has run out.
        clear_leaderboard_cache()
    log.warning("account %s (%s) REMOVED by %s: %s", user.email, user.role.value, session.get("email"), body.reason)
    return _removal_out(
        user, links_revoked=revoked, mentees_released=len(released),
        detail=(
            f"{user.name} is off every screen and cannot sign in. Every record is kept; "
            "Restore brings them back."
            + (
                f" {len(released)} student(s) were released to the unassigned pool."
                if released
                else ""
            )
        ),
    )


def _restore_account(db: Session, user: User, session: dict, request: Request) -> RemovalOut:
    if user.deleted_at is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{user.email} is not removed.")
    before = {"deleted_at": user.deleted_at.isoformat(), "delete_reason": user.delete_reason}
    user.deleted_at = None
    user.deleted_by_user_id = None
    user.delete_reason = None
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="user", entity_id=user.id, action="RESTORE",
        before=before, after={"deleted_at": None, "delete_reason": None},
        event_type="user.restore",
        payload={"email": user.email, "role": user.role.value},
    )
    db.commit()
    # Still refused while disabled underneath; the cache must learn either way.
    note_revocation(user.id, int(user.token_version or 0), disabled=user.disabled_at is not None)
    if _holds_a_student_record(db, user.id):
        clear_leaderboard_cache()
    log.warning("account %s (%s) RESTORED by %s", user.email, user.role.value, session.get("email"))
    return _removal_out(
        user,
        detail=(
            f"{user.name} is back on the screens with every record."
            + (
                " The account is still DISABLED and cannot sign in until it is enabled."
                if user.disabled_at is not None
                else " They can sign in again."
            )
            + (" Faculty released from them are not re-assigned." if user.role is Role.MENTOR else "")
        ),
    )


@router.post("/users/{user_id}/remove", response_model=RemovalOut)
def remove_user(
    user_id: str, body: RemoveIn, request: Request,
    session: dict = Depends(get_current_session), db: Session = Depends(get_db),
) -> RemovalOut:
    require_admin(session)
    return _remove_account(db, _user_or_404(db, user_id), body, session, request)


@router.post("/students/{student_id}/remove", response_model=RemovalOut)
def remove_student(
    student_id: str, body: RemoveIn, request: Request,
    session: dict = Depends(get_current_session), db: Session = Depends(get_db),
) -> RemovalOut:
    require_admin(session)
    return _remove_account(db, _student_user_or_404(db, student_id), body, session, request)


@router.post("/users/{user_id}/restore", response_model=RemovalOut)
def restore_user(
    user_id: str, request: Request,
    session: dict = Depends(get_current_session), db: Session = Depends(get_db),
) -> RemovalOut:
    require_admin(session)
    return _restore_account(db, _user_or_404(db, user_id), session, request)


@router.post("/students/{student_id}/restore", response_model=RemovalOut)
def restore_student(
    student_id: str, request: Request,
    session: dict = Depends(get_current_session), db: Session = Depends(get_db),
) -> RemovalOut:
    require_admin(session)
    return _restore_account(db, _student_user_or_404(db, student_id), session, request)


# ---------------------------------------------------------- colleges --


def _college_plan_out(db: Session, college: College) -> CollegeDeletePlanOut:
    plan = college_deletion.build_plan(db, college)
    return CollegeDeletePlanOut(
        consequences=_college_consequences(plan),
        refusal=None if plan.deletable else plan.refusal(),
        **plan.as_dict(),
    )


@router.get("/colleges/{college_id}/delete-plan", response_model=CollegeDeletePlanOut)
def college_delete_plan(
    college_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CollegeDeletePlanOut:
    require_admin(session)
    return _college_plan_out(db, _college_or_404(db, college_id))


@router.post("/colleges/{college_id}/delete", response_model=CollegeDeletedOut)
def delete_college(
    college_id: str,
    body: PermanentDeleteIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CollegeDeletedOut:
    require_admin(session)
    college = _college_or_404(db, college_id)
    plan = college_deletion.build_plan(db, college)
    if not plan.deletable:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.refusal())
    _consume_or_403(db, session["userId"], body.code)
    code, name = college.code, college.name
    try:
        college_deletion.execute(db, plan)
    except college_deletion.DeletionRefused as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="college", entity_id=college_id, action="DELETE_PERMANENT",
        before={"code": code, "name": name},
        after={"reason": body.reason, "plan": plan.as_dict()},
        event_type="college.delete_permanent",
        payload={"code": code, "reason": body.reason},
    )
    db.commit()
    log.warning("college %s (%s) PERMANENTLY DELETED by %s: %s", code, name, session.get("email"), body.reason)
    return CollegeDeletedOut(
        college_id=college_id, code=code, name=name, rows_deleted=plan.total_rows,
        detail=f"{code} · {name} and its structure are gone: {plan.total_rows} record(s).",
    )
