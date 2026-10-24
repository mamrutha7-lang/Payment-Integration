"""The student's Skills & Badge dashboard (framework doc §1–§17, student half).

Everything reads through two shared composers — `compose_badges` and
`compose_growth` — which the staff router imports too, so a mentor reading a
student's profile sees EXACTLY the screen the student sees (the compose_ledger
rule). Display status is derived per models/badge.py; nothing here stores a
word a row could contradict.

Write paths a student owns:
  * marking a badge In Progress,
  * attaching evidence — an off-catalogue upload of theirs, or a pick from the
    Approved Certification Catalogue (§12's simpler path). Readiness badges
    (§8) refuse evidence: they are assessment-threshold awards.

Rule 1 is untouched (nothing goes near a model); rule 2 lives in the staff
router. Leaderboards (§16) honour the existing `leaderboard_opt_out` profile
flag — a student who opted out of the main leaderboards did not opt into these.
"""

from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import badge_mail
from ..db import get_db
from ..governance import require_feature
from ..identity import get_current_session
from ..models.badge import (
    BADGE_BY_CODE,
    BADGES,
    CAPABILITY_LABEL,
    CATEGORY_LABEL,
    TRACK_LABEL,
    ApprovedCertification,
    AssessmentCheckpoint,
    BadgeCategory,
    BadgeEvidence,
    CapabilityAssessment,
    CapabilityKind,
    EvidenceStatus,
    EvidenceType,
    StudentBadge,
    StudentBadgeStatus,
)
from ..models.student_profile import StudentProfile
from ..models.upload import Upload
from ..models.user import Student, User
from .student import _require_student

router = APIRouter(prefix="/student", tags=["badges"])

_CHECKPOINTS = [c.value for c in AssessmentCheckpoint]


# --- shapes ------------------------------------------------------------------


class EvidenceOut(BaseModel):
    id: str
    evidence_type: str
    status: str
    title: str
    provider: str | None
    completed_on: date | None
    student_note: str | None
    review_note: str | None
    reviewed_at: datetime | None
    created_at: datetime
    from_catalogue: bool
    upload_id: str | None


class ApprovedCertificationOut(BaseModel):
    id: str
    name: str
    provider: str
    evidence_type: str
    stage: str
    duration_text: str | None
    is_free: bool
    url: str | None


class BadgeOut(BaseModel):
    code: str
    name: str
    category: str
    category_label: str
    track: str | None
    track_label: str | None
    stage: str
    points: int
    description: str
    requirement: str
    staff_awarded: bool
    # §13 display status: NOT_STARTED / IN_PROGRESS / VERIFICATION_PENDING /
    # EARNED — plus advanced_evidence_available riding on EARNED.
    status: str
    advanced_evidence_available: bool
    points_earned: int
    earned_at: datetime | None
    evidence: list[EvidenceOut]
    approved_certifications: list[ApprovedCertificationOut]


class CategoryOut(BaseModel):
    key: str
    label: str
    earned: int
    total: int
    badges: list[BadgeOut]


class BadgeDashboardOut(BaseModel):
    stage: str  # the student's REEP stage — the REBOOT → EXCEL → ELEVATE strip
    points_total: int
    earned_total: int
    badge_total: int
    categories: list[CategoryOut]


class CapabilityRowOut(BaseModel):
    capability: str
    label: str
    scores: dict[str, float | None]  # T0..T4; None = not assessed (never 0)
    current: float | None
    growth: float | None  # current - T0; None until both exist


class GrowthOut(BaseModel):
    checkpoints: list[str]
    rows: list[CapabilityRowOut]


# --- composers (shared with the staff router) --------------------------------


def _course_of(student: Student, db: Session) -> str | None:
    """The `academic_courses` programme this student is on, through their batch.

    None for a student nobody has seated yet, which is an ordinary state and not
    an error — they simply get the un-narrowed catalogue, exactly as before B13.
    """
    if not student.cohort_id:
        return None
    from ..models.cohort import Cohort

    return db.scalar(select(Cohort.course_id).where(Cohort.id == student.cohort_id))


def compose_badges(student: Student, db: Session) -> BadgeDashboardOut:
    # B13. WHICH OF THE 48 APPLY HERE, and which certificates count for them.
    # Both are per-COURSE and both default to "everything", so a deployment
    # with an empty `badge_course_map` and programme-wide certifications sees
    # exactly what it saw before this existed.
    #
    # Imported from the module that WRITES the table rather than re-derived:
    # two readings of "does this badge apply to this student" is how a student's
    # dashboard and the screen that configured it end up disagreeing, with only
    # the student able to see it.
    from .admin_catalogue import disabled_badge_codes

    course_id = _course_of(student, db)
    disabled = disabled_badge_codes(db, course_id)
    applicable = [b for b in BADGES if b.code not in disabled]

    rows = db.scalars(
        select(StudentBadge).where(StudentBadge.student_id == student.id)
    ).all()
    by_code = {r.badge_code: r for r in rows}

    evidence = db.scalars(
        select(BadgeEvidence)
        .where(BadgeEvidence.student_id == student.id)
        .order_by(BadgeEvidence.created_at.desc())
    ).all()
    ev_by_code: dict[str, list[BadgeEvidence]] = defaultdict(list)
    for ev in evidence:
        ev_by_code[ev.badge_code].append(ev)

    # A certification pinned to ANOTHER course is not this student's evidence
    # path, and offering it would send them to buy a certificate their badge
    # will not accept. NULL on both pointers is programme-wide and always in —
    # that is what every row written before B13 means.
    certs = db.scalars(
        select(ApprovedCertification)
        .where(
            ApprovedCertification.active.is_(True),
            or_(
                ApprovedCertification.course_id.is_(None),
                ApprovedCertification.course_id == course_id,
            ),
        )
        .order_by(ApprovedCertification.name)
    ).all()
    certs_by_code: dict[str, list[ApprovedCertification]] = defaultdict(list)
    for c in certs:
        certs_by_code[c.badge_code].append(c)

    categories: dict[BadgeCategory, list[BadgeOut]] = defaultdict(list)
    points_total = 0
    earned_total = 0
    for b in applicable:
        row = by_code.get(b.code)
        evs = ev_by_code.get(b.code, [])
        pending = any(e.status == EvidenceStatus.PENDING_VERIFICATION for e in evs)
        approved_types = {e.evidence_type for e in evs if e.status == EvidenceStatus.APPROVED}

        if row is not None and row.status == StudentBadgeStatus.EARNED:
            display = "EARNED"
        elif pending:
            display = "VERIFICATION_PENDING"
        elif row is not None:  # IN_PROGRESS
            display = "IN_PROGRESS"
        else:
            display = "NOT_STARTED"

        earned = display == "EARNED"
        if earned:
            earned_total += 1
            points_total += row.points_awarded

        categories[b.category].append(
            BadgeOut(
                code=b.code,
                name=b.name,
                category=b.category.value,
                category_label=CATEGORY_LABEL[b.category],
                track=b.track.value if b.track else None,
                track_label=TRACK_LABEL[b.track] if b.track else None,
                stage=b.stage.value,
                points=b.points,
                description=b.description,
                requirement=b.requirement,
                staff_awarded=b.staff_awarded,
                status=display,
                # §13: earned, but more of the three evidence types can still
                # be attached (the Negotiation example).
                advanced_evidence_available=earned
                and not b.staff_awarded
                and len(approved_types) < len(EvidenceType),
                points_earned=row.points_awarded if earned else 0,
                earned_at=row.earned_at if row else None,
                evidence=[
                    EvidenceOut(
                        id=e.id,
                        evidence_type=e.evidence_type.value,
                        status=e.status.value,
                        title=e.title,
                        provider=e.provider,
                        completed_on=e.completed_on,
                        student_note=e.student_note,
                        review_note=e.review_note,
                        reviewed_at=e.reviewed_at,
                        created_at=e.created_at,
                        from_catalogue=e.approved_certification_id is not None,
                        upload_id=e.upload_id,
                    )
                    for e in evs
                ],
                approved_certifications=[
                    ApprovedCertificationOut(
                        id=c.id,
                        name=c.name,
                        provider=c.provider,
                        evidence_type=c.evidence_type.value,
                        stage=c.stage.value,
                        duration_text=c.duration_text,
                        is_free=c.is_free,
                        url=c.url,
                    )
                    for c in certs_by_code.get(b.code, [])
                ],
            )
        )

    return BadgeDashboardOut(
        stage=student.current_stage.value,
        points_total=points_total,
        earned_total=earned_total,
        # WHAT THIS STUDENT CAN REACH, not what the catalogue holds. A course
        # with eight badges switched off would otherwise render "3 / 48" with
        # five of the missing forty-five simply absent from the tiles below.
        badge_total=len(applicable),
        categories=[
            CategoryOut(
                key=cat.value,
                label=CATEGORY_LABEL[cat],
                earned=sum(1 for x in categories[cat] if x.status == "EARNED"),
                total=len(categories[cat]),
                badges=categories[cat],
            )
            for cat in BadgeCategory
        ],
    )


def compose_growth(student_id: str, db: Session) -> GrowthOut:
    rows = db.scalars(
        select(CapabilityAssessment).where(CapabilityAssessment.student_id == student_id)
    ).all()
    matrix: dict[CapabilityKind, dict[str, float]] = defaultdict(dict)
    for r in rows:
        matrix[r.capability][r.checkpoint.value] = r.score

    out_rows: list[CapabilityRowOut] = []
    for cap in CapabilityKind:
        scores = {cp: matrix[cap].get(cp) for cp in _CHECKPOINTS}
        # "Current" is the LATEST assessed checkpoint; a missing score is
        # NOT a zero — §9's nullable-score rule, same as the English baseline.
        assessed = [(cp, s) for cp, s in scores.items() if s is not None]
        current = assessed[-1][1] if assessed else None
        baseline = scores.get("T0")
        # Growth needs a baseline AND a later assessment. With only T0 on
        # record the honest answer is "not yet measured" (a dash), not 0.0 —
        # a zero claims the student has not improved when nobody has looked.
        growth = (
            round(current - baseline, 1)
            if (current is not None and baseline is not None and assessed[-1][0] != "T0")
            else None
        )
        out_rows.append(
            CapabilityRowOut(
                capability=cap.value,
                label=CAPABILITY_LABEL[cap],
                scores=scores,
                current=current,
                growth=growth,
            )
        )
    return GrowthOut(checkpoints=_CHECKPOINTS, rows=out_rows)


# --- student reads -----------------------------------------------------------


@router.get("/badges", response_model=BadgeDashboardOut)
def my_badges(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> BadgeDashboardOut:
    student_id = _require_student(session)
    require_feature(db, student_id, "student.skilling")
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    return compose_badges(student, db)


@router.get("/growth", response_model=GrowthOut)
def my_growth(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> GrowthOut:
    student_id = _require_student(session)
    require_feature(db, student_id, "student.skilling")
    return compose_growth(student_id, db)


# --- student writes ----------------------------------------------------------


def _badge_or_404(code: str):
    badge = BADGE_BY_CODE.get(code)
    if badge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such badge.")
    return badge


@router.post("/badges/{code}/start", response_model=BadgeDashboardOut)
def start_badge(
    code: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> BadgeDashboardOut:
    """Mark a badge In Progress (§13). Idempotent; never demotes an EARNED row."""
    student_id = _require_student(session)
    require_feature(db, student_id, "student.skilling")
    badge = _badge_or_404(code)
    if badge.staff_awarded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Readiness badges are awarded on assessment thresholds — there is nothing to start.",
        )
    row = db.scalar(
        select(StudentBadge).where(
            StudentBadge.student_id == student_id, StudentBadge.badge_code == code
        )
    )
    if row is None:
        db.add(StudentBadge(student_id=student_id, badge_code=code))
        db.commit()
    return compose_badges(db.get(Student, student_id), db)


class EvidenceIn(BaseModel):
    """One evidence claim. EITHER pick a catalogue row (§12's simpler path —
    title/provider/type come from the catalogue) OR describe off-catalogue
    evidence yourself. An upload may back either path."""

    evidence_type: str | None = None  # required off-catalogue; ignored with a catalogue pick
    approved_certification_id: str | None = None
    upload_id: str | None = None
    title: str | None = Field(default=None, max_length=300)
    provider: str | None = Field(default=None, max_length=200)
    completed_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.post(
    "/badges/{code}/evidence", response_model=BadgeDashboardOut, status_code=status.HTTP_201_CREATED
)
def submit_evidence(
    code: str,
    body: EvidenceIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> BadgeDashboardOut:
    student_id = _require_student(session)
    require_feature(db, student_id, "student.skilling")
    badge = _badge_or_404(code)
    if badge.staff_awarded:
        # §8: readiness badges come from BGSCET assessment thresholds, and an
        # upload cannot claim one.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Readiness badges are awarded on BGSCET assessment thresholds, not evidence uploads.",
        )

    catalogue_row = None
    if body.approved_certification_id:
        catalogue_row = db.get(ApprovedCertification, body.approved_certification_id)
        if catalogue_row is None or not catalogue_row.active or catalogue_row.badge_code != code:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="That approved certification does not belong to this badge.",
            )
        ev_type = catalogue_row.evidence_type
        title = catalogue_row.name
        provider = catalogue_row.provider
    else:
        try:
            ev_type = EvidenceType(body.evidence_type or "")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="evidence_type must be EXTERNAL_VERIFIED, BGSCET_ASSESSED or APPLIED.",
            )
        title = (body.title or "").strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Describe the evidence — a title is required off the catalogue.",
            )
        provider = (body.provider or "").strip() or None

    if body.upload_id:
        upload = db.get(Upload, body.upload_id)
        # Same flattened 404 as everywhere else: someone else's upload id must
        # be indistinguishable from a wrong one.
        if upload is None or upload.student_id != student_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")

    ev = BadgeEvidence(
        student_id=student_id,
        badge_code=code,
        evidence_type=ev_type,
        upload_id=body.upload_id,
        approved_certification_id=catalogue_row.id if catalogue_row else None,
        title=title,
        provider=provider,
        completed_on=body.completed_on,
        student_note=(body.note or "").strip() or None,
    )
    db.add(ev)
    # Submitting evidence also moves a Not Started badge to a live row, so the
    # tile stops reading "not started" while a review is pending.
    if (
        db.scalar(
            select(StudentBadge).where(
                StudentBadge.student_id == student_id, StudentBadge.badge_code == code
            )
        )
        is None
    ):
        db.add(StudentBadge(student_id=student_id, badge_code=code))
    db.commit()
    # THE MENTOR IS TOLD A CLAIM IS WAITING (2026-09-17) -- after the commit,
    # so the mail can never describe a row that did not land, and through
    # `deliver_once`, so a retried submission never mails twice. It returns
    # Empty rather than raising when nothing can be sent (gate off, nobody
    # granted verification over this student yet, every verifier barred): a
    # mail that did not go out is never the reason a claim fails to file.
    # app/badge_mail.py carries the reasoning.
    badge_mail.notify_verifiers_of_claim(db, ev)
    return compose_badges(db.get(Student, student_id), db)


# --- leaderboards (§16) ------------------------------------------------------


class LeaderboardRowOut(BaseModel):
    rank: int
    name: str
    # Filled only on the caller's own row. Every other student's USN is the
    # data rule 2 exists to protect — the main board in routers/student.py
    # ships name + initials and nothing else, and this board matches it.
    usn: str | None
    value: float
    is_me: bool


class LeaderboardOut(BaseModel):
    view: str
    label: str
    unit: str  # "points" | "growth"
    # True when the CALLER opted out: rows is then empty, matching
    # routers/student.py's boards — opt-out is bidirectional, you leave the
    # board and the board leaves you.
    opted_out: bool = False
    rows: list[LeaderboardRowOut]


_LB_CATEGORY_VIEWS = {
    "managerial": BadgeCategory.MANAGERIAL,
    "sectoral": BadgeCategory.SECTORAL,
    "platform": BadgeCategory.PLATFORM,
    "thinking": BadgeCategory.THINKING,
    "readiness": BadgeCategory.READINESS,
}
_LB_LABEL = {
    "overall": "Overall REEP Leaderboard",
    "managerial": "Managerial Skills",
    "sectoral": "Sectoral Skills",
    "platform": "Platform / Technical Skills",
    "thinking": "Thinking Skills",
    "readiness": "Career Readiness",
    "most_improved": "Most Improved",
}


@router.get("/badges/leaderboards", response_model=LeaderboardOut)
def badge_leaderboards(
    view: str = "overall",
    track: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaderboardOut:
    """§16. `view` is overall / managerial / sectoral / platform / thinking /
    readiness / most_improved; sectoral additionally accepts `?track=` to split
    by specialisation. Most Improved ranks GROWTH FROM BASELINE, not points —
    students enter the programme at different starting capabilities, and a
    points board would rank their starting line, not their work."""
    student_id = _require_student(session)
    # `student.leaderboards`, NOT `student.skilling`: these are boards, and an
    # office that switched the leaderboards off for a cohort meant all of them.
    # Switching skilling off hides the badge screen; it does not silently leave
    # the same cohort ranked on a board reached from it.
    require_feature(db, student_id, "student.leaderboards")
    if view not in _LB_LABEL:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown view.")

    opted_out = set(
        db.scalars(
            select(StudentProfile.student_id).where(StudentProfile.leaderboard_opt_out.is_(True))
        ).all()
    )
    if student_id in opted_out:
        # Same symmetry as routers/student.py's boards: a student who opted out
        # is excluded from every board AND sees none — otherwise opting out
        # buys privacy from classmates who kept the visibility you still use.
        return LeaderboardOut(view=view, label=_LB_LABEL[view], unit="points", opted_out=True, rows=[])
    # A REMOVED account (`users.deleted_at`) is on no board, these included,
    # for the reason `_ranked_board` in routers/student.py gives: the office is
    # told "… is off every screen". Only a named student is ranked below, so
    # leaving them out of `names` leaves them off every view.
    names = {
        s_id: (name, usn)
        for s_id, name, usn in db.execute(
            select(Student.id, User.name, Student.usn)
            .join(User, Student.user_id == User.id)
            .where(User.deleted_at.is_(None))
        ).all()
    }

    values: dict[str, float] = defaultdict(float)
    if view == "most_improved":
        unit = "growth"
        rows = db.scalars(select(CapabilityAssessment)).all()
        per_student: dict[str, dict[CapabilityKind, dict[str, float]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for r in rows:
            per_student[r.student_id][r.capability][r.checkpoint.value] = r.score
        for s_id, caps in per_student.items():
            growths = []
            for cp_scores in caps.values():
                baseline = cp_scores.get("T0")
                assessed = [cp_scores[cp] for cp in _CHECKPOINTS if cp in cp_scores]
                if baseline is not None and len(assessed) > 1:
                    growths.append(assessed[-1] - baseline)
            if growths:
                values[s_id] = round(sum(growths) / len(growths), 2)
    else:
        unit = "points"
        codes: set[str] | None = None
        if view in _LB_CATEGORY_VIEWS:
            cat = _LB_CATEGORY_VIEWS[view]
            codes = {
                b.code
                for b in BADGES
                if b.category == cat and (track is None or (b.track and b.track.value == track))
            }
            if not codes:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown track."
                )
        for sb in db.scalars(
            select(StudentBadge).where(StudentBadge.status == StudentBadgeStatus.EARNED)
        ).all():
            if codes is None or sb.badge_code in codes:
                values[sb.student_id] += sb.points_awarded

    ranked = sorted(
        ((s_id, v) for s_id, v in values.items() if s_id not in opted_out and s_id in names),
        key=lambda t: (-t[1], names[t[0]][0]),
    )[:50]
    return LeaderboardOut(
        view=view,
        label=_LB_LABEL[view],
        unit=unit,
        rows=[
            LeaderboardRowOut(
                rank=i + 1,
                name=names[s_id][0],
                usn=names[s_id][1] if s_id == student_id else None,
                value=v,
                is_me=s_id == student_id,
            )
            for i, (s_id, v) in enumerate(ranked)
        ],
    )
