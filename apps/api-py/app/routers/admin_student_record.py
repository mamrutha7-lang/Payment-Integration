"""The rest of one student's record, for the Student 360 page (2026-10-15).

    GET /api/admin/students/{student_id}/record
        builder resumes, skills and growth, leaderboard standing on every board,
        the last 14 ledger days, the English baseline, mock interviews and six
        weeks of attendance
    GET /api/admin/students/{student_id}/access
        who can open this record, and the students a new grant could reach
        (the Main Admin's alone)
    GET /api/admin/students/{student_id}/files/{upload_id}
        one of the student's uploads (their CV, a certificate), as a file
    GET /api/admin/students/{student_id}/resumes/{resume_id}/pdf
        a resume they built in REEP, rendered to PDF
    GET /api/admin/student-records?q=
        the students this account may open, for the faculty member who was
        given access and has no roster to find them on

WHY THIS EXISTS. The page that `GET .../360` feeds was missing the resume, the
skills, the leaderboard and its headline numbers, and the owner asked for all
four on one screen. Three of the panels it drew came from MENTOR reads gated on
`mentor.mentees`, which the Main Admin does not hold by design, so the office
saw "not readable" where a student's time sheet should have been. Everything
here hangs on the page's own key, `admin.student_records`, and reuses the
composer every other screen uses, so the office, the mentor and the student
see one set of numbers.

BOTH FENCES, AND THE ONE WIDENING (`_open_record`). `require_capability` asks
"may you, here", and rule 2 runs underneath with `allow_records_grant=True`:
a faculty member the office gave "View student records" for a batch, a
department or the college may READ those students' records. Every endpoint in
this module is a GET; there is no write path, so the widening cannot reach one.

A DOWNLOAD IS RECORDED, A VIEW IS NOT. Opening a record writes nothing, for
`routers/audit.py`'s reason. A file leaving the system is different, the way an
export CSV audits itself: `?download=true` writes an audit row against the
student, which the record's own history shows. The preview reads the same
bytes without the flag.

EVERY FILE IS SERVED AS AN ATTACHMENT. `document_store.content_disposition`
explains why: a student's PDF rendered inline runs its JavaScript in this
origin. The page draws its preview with pdf.js instead, which renders the
pages and never runs the scripts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from .. import batch_labels
from ..architecture_events import record_change
from ..db import get_db
from ..document_store import content_disposition, read_bytes
from ..governance import (
    ROLE_BASELINE,
    _live_grant_clauses,
    ancestry_of_student,
    current_role_of,
    granted_reaches,
    has_capability,
    reaches_target,
    require_capability,
)
from ..identity import get_current_session
from ..mentor_history import HANDOVER_CAPABILITY
from ..models.cohort import Cohort
from ..models.governance import (
    AccessGroup,
    AccessGroupMember,
    CapabilityGrant,
    ScopeLevel,
    SubjectKind,
)
from ..models.institution import Department
from ..models.resume import Resume
from ..models.student_profile import StudentProfile
from ..models.upload import Upload
from ..models.user import Mentor, Role, Student, User
from ..policies import RECORDS_CAPABILITY, Reach
from ..resume_pdf import render_resume_pdf
from .badges import BadgeDashboardOut, GrowthOut, compose_badges, compose_growth
from .console import StudentWeeklyOut, compose_student_weekly
from .admin_students import AdminStudentOut, _rows
from .governance import _target_label
from .interview_records import InterviewSessionOut, _sessions_for_student
from .mentee_records import LedgerSummaryOut, compose_ledger_summary
from .mentor import _assert_can_access_student, require_admin
from .student import _INSTITUTION_Q, _initials, _leaderboard_scope, _ranked_board
from .student_programme import EnglishBaselineOut, compose_english_baseline

router = APIRouter(prefix="/admin", tags=["admin-students"])

#: The page's key, named once. `tools/ci/check_capability_enforcement.py`
#: resolves the module constant at each call site.
CAPABILITY: Final[str] = RECORDS_CAPABILITY

#: The fortnight a mentor opens the ledger to judge "are they keeping it up".
LEDGER_DAYS: Final[int] = 14

#: The boards the student's own Leaderboards screen draws, in its order and
#: with its words. `certificates` is left out for the reason that screen
#: dropped it: a certificate is evidence for a skill the skills board ranks.
BOARDS: Final[tuple[tuple[str, str], ...]] = (
    ("overall", "Overall"),
    ("skills", "Skills"),
    ("vtu", "VTU results"),
    ("streak", "Streak"),
    ("mocks", "Mocks taken"),
)

#: How much of each board the page shows: the top of it, plus the student and
#: the places either side of them.
BOARD_TOP: Final[int] = 5
BOARD_AROUND: Final[int] = 1


# --------------------------------------------------------------- gate --


def _open_record(db: Session, session: dict, student_id: str) -> Student:
    """Both fences, in the 360's order, for every endpoint below."""
    require_capability(db, session, CAPABILITY)
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    require_capability(db, session, CAPABILITY, target=ancestry_of_student(db, student.id))
    _assert_can_access_student(session, student.id, db, allow_records_grant=True)
    return student


def _record_download(
    db: Session, session: dict, request: Request, student: Student, what: dict
) -> None:
    """One audit row per file that left the system, against the student, so
    the record's own history lists it."""
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="student",
        entity_id=student.id,
        action="DOWNLOADED",
        before=None,
        after=what,
        event_type="student.record.downloaded",
        payload=what,
    )
    db.commit()


# ------------------------------------------------------------ schemas --


class ResumeVersionOut(BaseModel):
    """A resume the student built in REEP. Their CV FILE is an upload and is
    listed in the 360's `documents`, marked `from_application`."""

    id: str
    version: int
    title: str
    target_role: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class BoardRowOut(BaseModel):
    rank: int
    student_id: str
    name: str
    initials: str
    value_label: str
    is_this_student: bool
    #: True when ranks were skipped before this row, so the table draws a gap.
    gap_before: bool


class BoardStandingOut(BaseModel):
    board: str
    label: str
    #: Null when the student has nothing on this board yet, never 0.
    rank: int | None
    ranked: int
    value_label: str | None
    rows: list[BoardRowOut]
    unranked_total: int


class LeaderboardsOut(BaseModel):
    """The student's standing, read the way their own screen reads it.

    `hidden` is the student's own choice to leave the boards. The office is
    told so and is shown no rank, which is what the student's classmates see.
    """

    hidden: bool
    #: "batch" | "department" | "none", as on the student's own screen.
    scope: str
    scope_label: str | None
    classmates: int
    boards: list[BoardStandingOut]


class StudentRecordOut(BaseModel):
    resumes: list[ResumeVersionOut]
    skills: BadgeDashboardOut
    growth: GrowthOut
    leaderboards: LeaderboardsOut
    ledger: LedgerSummaryOut
    english: EnglishBaselineOut
    interviews: list[InterviewSessionOut]
    weekly: StudentWeeklyOut


class AccessHolderOut(BaseModel):
    #: "admin" | "mentor" | "handover" | "grant"
    kind: str
    name: str
    #: What this person may do here, in words.
    detail: str
    #: For a grant: the students it covers ("Management Studies"). Null for the
    #: office and the mentor, whose reach is not a grant.
    reach_label: str | None = None
    until: datetime | None = None
    reason: str | None = None
    granted_by: str | None = None
    granted_at: datetime | None = None
    #: Set on a grant the office can remove (`POST /admin/governance/grants/
    #: {id}/revoke`). Null for everybody else.
    grant_id: str | None = None
    #: Who this is: the account's id, or ``group:<id>`` for an access group.
    #: One person can appear twice (a mentor who also holds a grant, or two
    #: grants), and the page counts people by this, not by rows.
    holder_id: str


class ReachOptionOut(BaseModel):
    """One answer to "which students" in the Give access dialog."""

    scope_level: str
    scope_id: str
    label: str
    students: int


class AccessOut(BaseModel):
    holders: list[AccessHolderOut]
    #: True when this session may give and remove access (`admin.governance`).
    can_manage: bool
    reach_options: list[ReachOptionOut]


# ------------------------------------------------------------- record --


def _resumes(db: Session, student_id: str) -> list[ResumeVersionOut]:
    rows = db.scalars(
        select(Resume).where(Resume.student_id == student_id).order_by(Resume.created_at.desc())
    ).all()
    return [
        ResumeVersionOut(
            id=r.id,
            version=r.version,
            title=r.title or "REEP Resume",
            target_role=r.target_role,
            status=r.status.value,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


def compose_leaderboards(
    db: Session, student: Student, *, openable: ColumnElement[bool] | None = None
) -> LeaderboardsOut:
    """Every board, from the same cached computation the student's screen
    reads (`routers/student.py::_ranked_board`), so a rank here and a rank
    there cannot disagree.

    `openable` narrows the ROWS to the classmates the caller may open
    themselves (`_openable_clause`); None lists them all, which is the Main
    Admin's view. This student's own rank, out of how many, and their value
    are always given. Without it a faculty member granted ONE student read the
    names and CGPAs of that student's top-ranked classmates, whom the grant
    does not reach (found in review of the promotion, 2026-10-15).
    """
    scope = _leaderboard_scope(db, student)
    profile_hidden = db.scalar(
        select(StudentProfile.leaderboard_opt_out).where(StudentProfile.student_id == student.id)
    )
    if profile_hidden:
        return LeaderboardsOut(
            hidden=True, scope=scope.kind, scope_label=scope.label, classmates=0, boards=[]
        )

    classmates = 0
    computed_boards = []
    for key, label in BOARDS:
        computed = _ranked_board(db, scope, key)
        classmates = computed.classmates
        entries = computed.entries
        mine = next((i for i, e in enumerate(entries) if e[1] == student.id), None)
        shown = set(range(min(BOARD_TOP, len(entries))))
        if mine is not None:
            shown |= set(range(max(0, mine - BOARD_AROUND), min(len(entries), mine + BOARD_AROUND + 1)))
        computed_boards.append((key, label, computed, entries, mine, shown))

    visible: set[str] | None = None
    if openable is not None:
        candidates = {entries[i][1] for _, _, _, entries, _, shown in computed_boards for i in shown}
        visible = {student.id}
        if candidates:
            visible |= set(
                db.scalars(select(Student.id).where(Student.id.in_(candidates), openable)).all()
            )

    boards: list[BoardStandingOut] = []
    for key, label, computed, entries, mine, shown in computed_boards:
        rows: list[BoardRowOut] = []
        previous: int | None = None
        for index in sorted(shown):
            rank, sid, name, _value, value_label = entries[index]
            if visible is not None and sid not in visible:
                continue
            rows.append(
                BoardRowOut(
                    rank=rank,
                    student_id=sid,
                    name=name,
                    initials=_initials(name),
                    value_label=value_label,
                    is_this_student=(sid == student.id),
                    gap_before=previous is not None and index != previous + 1,
                )
            )
            previous = index
        boards.append(
            BoardStandingOut(
                board=key,
                label=label,
                rank=entries[mine][0] if mine is not None else None,
                ranked=len(entries),
                value_label=entries[mine][4] if mine is not None else None,
                rows=rows,
                unranked_total=len(computed.unranked),
            )
        )
    return LeaderboardsOut(
        hidden=False,
        scope=scope.kind,
        scope_label=scope.label,
        classmates=classmates,
        boards=boards,
    )


@router.get("/students/{student_id}/record", response_model=StudentRecordOut)
def student_record(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StudentRecordOut:
    """The panels the 360 does not carry, each from its shared composer."""
    student = _open_record(db, session, student_id)
    return StudentRecordOut(
        resumes=_resumes(db, student.id),
        skills=compose_badges(student, db),
        growth=compose_growth(student.id, db),
        leaderboards=compose_leaderboards(db, student, openable=_openable_clause(db, session)),
        ledger=compose_ledger_summary(db, student.id, LEDGER_DAYS),
        english=compose_english_baseline(db, student.id),
        interviews=_sessions_for_student(db, student.id),
        weekly=compose_student_weekly(db, student.id),
    )


# --------------------------------------------------------------- files --


@router.get("/students/{student_id}/files/{upload_id}")
def student_file(
    student_id: str,
    upload_id: str,
    request: Request,
    download: bool = Query(default=False),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """One of this student's uploads. The upload must be THEIRS: an id from
    another student is the same 404 as an id that does not exist."""
    student = _open_record(db, session, student_id)
    upload = db.get(Upload, upload_id)
    if upload is None or upload.student_id != student.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")
    try:
        content = read_bytes(upload.stored_name)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing.")
    if download:
        _record_download(
            db, session, request, student,
            {"file": upload.original_name, "kind": upload.kind.value, "upload_id": upload.id},
        )
    return Response(
        content=content,
        media_type=upload.mime_type,
        headers={"Content-Disposition": content_disposition(upload.original_name)},
    )


@router.get("/students/{student_id}/resumes/{resume_id}/pdf")
def student_resume_pdf(
    student_id: str,
    resume_id: str,
    request: Request,
    download: bool = Query(default=False),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """A resume the student built, rendered locally (no model, no network, so
    rule 1's gate is not on this path) exactly as their own download is."""
    student = _open_record(db, session, student_id)
    resume = db.get(Resume, resume_id)
    if resume is None or resume.student_id != student.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found.")
    pdf = render_resume_pdf(resume.markdown or "", fallback_title=resume.title or "REEP Resume")
    filename = f"resume-v{resume.version}.pdf"
    if download:
        _record_download(
            db, session, request, student,
            {"file": filename, "kind": "BUILT_RESUME", "resume_id": resume.id},
        )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(filename)},
    )


# -------------------------------------------------------------- access --


def _reach_label(db: Session, level: ScopeLevel, target_id: str) -> str:
    """Governance's label, except for a batch: `cohorts.name` is a year, and
    "2025-27" alone does not say which batch (AGENTS.md, "A BATCH IS A YEAR")."""
    if level == ScopeLevel.COHORT:
        cohort = db.get(Cohort, target_id)
        if cohort is None:
            return "(removed)"
        row = db.execute(_INSTITUTION_Q, {"cohort_id": cohort.id}).mappings().first()
        return batch_labels.compose(
            row["course_name"] if row else None,
            row["specialization_name"] if row else None,
            cohort.name,
            cohort.batch_label,
        )
    if level == ScopeLevel.STUDENT:
        return "Only this student"
    return _target_label(db, level, target_id)


def _scoped_reach(reaches: list[tuple[ScopeLevel | None, str | None]]) -> Reach:
    """The students a set of SCOPED grants covers, as `policies.Reach`, so
    every count and list here is the same subquery the rest of the console
    narrows by. A programme-wide pair is dropped: it does not open a record
    beyond the holder's mentees (`policies.assert_student_scope`)."""
    buckets: dict[ScopeLevel, set[str]] = {level: set() for level in ScopeLevel}
    for level, target_id in reaches:
        if level is not None and target_id:
            buckets[level].add(target_id)
    return Reach(
        everything=False,
        colleges=frozenset(buckets[ScopeLevel.COLLEGE]),
        departments=frozenset(buckets[ScopeLevel.DEPARTMENT]),
        courses=frozenset(buckets[ScopeLevel.COURSE]),
        specializations=frozenset(buckets[ScopeLevel.SPECIALIZATION]),
        cohorts=frozenset(buckets[ScopeLevel.COHORT]),
        students=frozenset(buckets[ScopeLevel.STUDENT]),
    )


def _student_count(db: Session, level: ScopeLevel, target_id: str) -> int:
    """How many students a grant at this rung would reach, removed accounts
    left out. Both department pointers count, as they do for the grant."""
    covered = _scoped_reach([(level, target_id)]).student_ids().subquery()
    return int(
        db.scalar(
            select(func.count())
            .select_from(Student)
            .join(User, User.id == Student.user_id)
            .where(Student.id.in_(select(covered)), User.deleted_at.is_(None))
        )
        or 0
    )


def _reach_options(db: Session, student: Student) -> list[ReachOptionOut]:
    """Narrowest first: this student, their batch, department and college."""
    order = (ScopeLevel.STUDENT, ScopeLevel.COHORT, ScopeLevel.DEPARTMENT, ScopeLevel.COLLEGE)
    pairs = dict(ancestry_of_student(db, student.id))
    options = []
    for level in order:
        target_id = pairs.get(level)
        if not target_id:
            continue
        label = _reach_label(db, level, target_id)
        if level == ScopeLevel.STUDENT:
            name = db.scalar(select(User.name).where(User.id == student.user_id))
            label = f"Only {name}" if name else "Only this student"
        options.append(
            ReachOptionOut(
                scope_level=level.value,
                scope_id=target_id,
                label=label,
                students=_student_count(db, level, target_id),
            )
        )
    return options


def _account_can_sign_in(user: User | None) -> bool:
    return user is not None and user.deleted_at is None and user.disabled_at is None


def _grant_holders(db: Session, student: Student) -> list[AccessHolderOut]:
    """Live, SCOPED "View student records" grants whose reach covers this
    student: the same set `policies._holds_records_grant` admits. A
    programme-wide grant is left out because it does not open this record
    unless its holder is the mentor, who is listed as the mentor."""
    ancestry = ancestry_of_student(db, student.id)
    grants = db.scalars(
        select(CapabilityGrant)
        .where(
            CapabilityGrant.capability == CAPABILITY,
            CapabilityGrant.scope_level.is_not(None),
            *_live_grant_clauses(datetime.now(timezone.utc)),
        )
        .order_by(CapabilityGrant.granted_at.desc())
    ).all()
    holders: list[AccessHolderOut] = []
    for g in grants:
        if not reaches_target([(g.scope_level, g.scope_id)], ancestry):
            continue
        granter = db.get(User, g.granted_by_user_id) if g.granted_by_user_id else None
        common = dict(
            kind="grant",
            reach_label=_reach_label(db, g.scope_level, g.scope_id),
            until=g.expires_at,
            reason=g.reason,
            granted_by=(granter.name or granter.email) if granter else None,
            granted_at=g.granted_at,
            grant_id=g.id,
        )
        if g.subject_kind == SubjectKind.USER:
            user = db.get(User, g.subject_user_id) if g.subject_user_id else None
            if not _account_can_sign_in(user):
                continue
            # B2.5: a grant made to the account in another role no longer counts.
            if g.role_at_grant is not None and g.role_at_grant != current_role_of(db, user.id):
                continue
            holders.append(
                AccessHolderOut(
                    name=user.name or user.email,
                    holder_id=user.id,
                    detail="Read only: can open records and download resumes and documents.",
                    **common,
                )
            )
        else:
            group = db.get(AccessGroup, g.subject_group_id) if g.subject_group_id else None
            if group is None:
                continue
            members = int(
                db.scalar(
                    select(func.count())
                    .select_from(AccessGroupMember)
                    .where(AccessGroupMember.group_id == group.id)
                )
                or 0
            )
            holders.append(
                AccessHolderOut(
                    name=f"{group.name} (group of {members})",
                    holder_id=f"group:{group.id}",
                    detail="Read only, for everyone in this access group.",
                    **common,
                )
            )
    return holders


def _handover_holders(db: Session, student: Student) -> list[AccessHolderOut]:
    """A former mentor inside B9.1's 90-day window reads this record's notes,
    ledger and baseline. Listed so the office is not surprised by them."""
    rows = db.execute(
        select(CapabilityGrant, User)
        .join(User, User.id == CapabilityGrant.subject_user_id)
        .where(
            CapabilityGrant.capability == HANDOVER_CAPABILITY,
            CapabilityGrant.subject_kind == SubjectKind.USER,
            CapabilityGrant.scope_level == ScopeLevel.STUDENT,
            CapabilityGrant.scope_id == student.id,
            *_live_grant_clauses(datetime.now(timezone.utc)),
        )
    ).all()
    return [
        AccessHolderOut(
            kind="handover",
            name=user.name or user.email,
            holder_id=user.id,
            detail="Former faculty mentor. Can read notes and the time sheet during the handover.",
            until=g.expires_at,
        )
        for g, user in rows
        if _account_can_sign_in(user)
    ]


@router.get("/students/{student_id}/access", response_model=AccessOut)
def student_access(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AccessOut:
    """Who can open this student's record, and how. Granting and removing go
    through Governance's own endpoints, so there is one place a grant is
    written and one audit trail for it.

    THE MAIN ADMIN'S ALONE (2026-10-17, the owner's decision). Who else can
    read a student, why each of them was let in and by whom is the office's
    business, not the business of a faculty member reading on a grant or a
    mentor reading their own mentee. So this is `require_admin` before the
    record is even opened, and the page draws no Access tab for anybody else.
    """
    require_admin(session)
    student = _open_record(db, session, student_id)
    holders: list[AccessHolderOut] = []
    for admin in db.scalars(
        select(User).where(User.role == Role.ADMIN).order_by(User.name)
    ).all():
        if _account_can_sign_in(admin):
            holders.append(
                AccessHolderOut(
                    kind="admin",
                    name=admin.name or admin.email,
                    holder_id=admin.id,
                    detail="Main Admin. Sees every student and can edit, move and remove them.",
                )
            )
    if student.mentor_id:
        mentor_user = db.scalar(
            select(User).join(Mentor, Mentor.user_id == User.id).where(Mentor.id == student.mentor_id)
        )
        if _account_can_sign_in(mentor_user):
            holders.append(
                AccessHolderOut(
                    kind="mentor",
                    name=mentor_user.name or mentor_user.email,
                    holder_id=mentor_user.id,
                    detail="Faculty mentor. Reads the records of their own mentees.",
                )
            )
    holders.extend(_handover_holders(db, student))
    holders.extend(_grant_holders(db, student))
    return AccessOut(
        holders=holders,
        can_manage=has_capability(db, session, "admin.governance"),
        reach_options=_reach_options(db, student),
    )


def _openable_clause(db: Session, session: dict) -> ColumnElement[bool] | None:
    """The students this account may open on the record page, as a WHERE
    clause over `students`; None for the Main Admin, who may open everybody.

    A faculty member: their own mentees, and the students their SCOPED "View
    student records" grants reach. That is the set `_open_record` admits.
    Mentoring a student opens their record with no grant at all since
    2026-10-16 (`mentor_functions.MENTEE_RECORD_FUNCTIONS`, unscoped like
    every derived function, with rule 2 underneath admitting the mentor's own
    group); before that a mentee opened only beside a programme-wide grant,
    and a mentor with none saw their mentees' names and nothing behind them.
    The "Student records" list and the leaderboard rows on a record both read
    this, so neither names a student the page would refuse.
    """
    role = str(session.get("role") or "")
    if CAPABILITY in ROLE_BASELINE.get(role, frozenset()):
        return None
    user_id = str(session.get("userId") or "")
    covered = _scoped_reach(granted_reaches(db, user_id, CAPABILITY))
    reachable: list[ColumnElement[bool]] = [
        Student.mentor_id.in_(select(Mentor.id).where(Mentor.user_id == user_id))
    ]
    if not covered.nothing:
        reachable.append(Student.id.in_(covered.student_ids()))
    return or_(*reachable)


# ------------------------------------------------------------- the list --

#: A list for finding a student, not a roster: the search narrows it.
RECORDS_LIST_LIMIT: Final[int] = 200


@router.get("/student-records", response_model=list[AdminStudentOut])
def student_records(
    q: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AdminStudentOut]:
    """The students this account may open on the Student 360 page.

    The Main Admin: everybody on the roster. A faculty member: the students
    their SCOPED "View student records" grants reach, and their own mentees,
    which is exactly the set `_open_record` admits. Removed accounts are left
    out, as they are on the roster.
    """
    require_capability(db, session, CAPABILITY)
    where = [User.deleted_at.is_(None)]
    openable = _openable_clause(db, session)
    if openable is not None:
        where.append(openable)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        where.append(
            func.lower(User.name).like(needle)
            | func.lower(User.email).like(needle)
            | func.lower(func.coalesce(Student.usn, "")).like(needle)
        )
    return _rows(db, *where)[:RECORDS_LIST_LIMIT]
