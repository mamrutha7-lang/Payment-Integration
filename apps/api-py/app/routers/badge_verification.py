"""Staff and admin half of the Skills & Badge framework (§12, §15–§18).

VERIFYING IS THE OFFICE'S CHOICE OF PERSON, NOT A MENTOR'S BY MENTORING
(2026-10-16). The queue, the reviewed strip, a claim's file and its decision
are reached through a grant of `mentor.verifications` ("Verify skills &
evidence") and narrowed to that grant's reach IN SQL, through app/verifiers.py
-- an out-of-reach claim is never read out of the database, the leave-queue
lesson. Mentoring a student no longer opens any of them. The other per-student
endpoints here (assessments, the staff read of the dashboard) still go through
rule 2's `_assert_can_access_student`, imported from routers/mentor.py and
never reimplemented. Cohort views, exports and the certification catalogue are
the Main Admin (§15's "cohort-level administrative view", §18).

What approving means (§10): the reviewer's APPROVE on an evidence row is the
act that mints the EARNED badge row — points stamped from the catalogue at that
moment, reviewer recorded. REJECT and MORE_INFO_REQUIRED write the verdict and
the note and mint nothing. Revoking (§18, Main Admin only) deletes the award row
— the badge tile falls back to whatever the remaining rows honestly say — and
never touches the evidence history.

The staff read of a student's dashboard is `compose_badges`/`compose_growth`
from routers/badges.py — the same builders the student's own screen uses, so a
mentor can never see a confident number where the student sees a dash.
"""

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import badge_mail
from ..architecture_events import record_change
from ..db import get_db
from ..governance import require_capability
from ..identity import get_current_session
from ..models.badge import (
    BADGE_BY_CODE,
    BADGES,
    CAPABILITY_LABEL,
    CATEGORY_LABEL,
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
from ..document_store import content_disposition, read_bytes
# B14: one owner for the export rules, shared with routers/console.py.
from ..exports import (
    carries_personal_columns,
    csv_response,
    drop_personal,
    record_export,
    scope_note,
)
from ..models.upload import Upload, UploadStatus
from ..models.user import Student, User
from ..policies import scope_filter
from ..verifiers import (
    VERIFY_CAPABILITY,
    assert_can_verify,
    narrow_to_reach,
    verification_reach,
)
from .badges import BadgeDashboardOut, GrowthOut, compose_badges, compose_growth
from .mentor import _assert_can_access_student, require_admin, require_mentor

router = APIRouter(tags=["badge-admin"])


# --- the review queue (§12) --------------------------------------------------


class PendingEvidenceOut(BaseModel):
    id: str
    student_id: str
    student_name: str
    usn: str | None
    badge_code: str
    badge_name: str
    category_label: str
    evidence_type: str
    status: str
    title: str
    provider: str | None
    completed_on: str | None
    student_note: str | None
    from_catalogue: bool
    upload_id: str | None
    created_at: datetime
    # Filled on a DECIDED row -- the "Recently reviewed" strip reads them back
    # so a mentor can check what they said. Null on every pending row.
    review_note: str | None = None
    reviewed_at: datetime | None = None
    # What the file behind the claim is called, so the card names it before the
    # reviewer opens it. Null when nothing backs the claim, and null again once
    # the student has deleted the upload (`badge_evidence.upload_id` is SET
    # NULL): the claim outlives its file as an honest audit line.
    evidence_file_name: str | None = None


def _pending_row(
    ev: BadgeEvidence, name: str, usn: str | None, upload: Upload | None = None
) -> PendingEvidenceOut:
    badge = BADGE_BY_CODE.get(ev.badge_code)
    return PendingEvidenceOut(
        id=ev.id,
        student_id=ev.student_id,
        student_name=name,
        usn=usn,
        badge_code=ev.badge_code,
        badge_name=badge.name if badge else ev.badge_code,
        category_label=CATEGORY_LABEL[badge.category] if badge else "",
        evidence_type=ev.evidence_type.value,
        status=ev.status.value,
        title=ev.title,
        provider=ev.provider,
        completed_on=ev.completed_on.isoformat() if ev.completed_on else None,
        student_note=ev.student_note,
        from_catalogue=ev.approved_certification_id is not None,
        upload_id=ev.upload_id,
        created_at=ev.created_at,
        review_note=ev.review_note,
        reviewed_at=ev.reviewed_at,
        evidence_file_name=upload.original_name if upload is not None else None,
    )


def _evidence_query(db: Session, session: dict, response: Response):
    """The claim rows this reader may see, with the names the card needs, or
    None for a reader whose reach is nothing.

    ONE query for the pending queue and the reviewed strip, so the two lists
    cannot disagree about scope -- `mentor.py::_claim_query`'s rule. The
    narrowing is the verification grant's reach and nothing else
    (app/verifiers.py); `pending_evidence` says why the mentor's own group is no
    longer part of it.
    """
    query = (
        select(BadgeEvidence, User.name, Student.usn, Upload)
        .join(Student, BadgeEvidence.student_id == Student.id)
        .join(User, Student.user_id == User.id)
        .outerjoin(Upload, BadgeEvidence.upload_id == Upload.id)
    )
    return narrow_to_reach(query, verification_reach(db, session, response))


_EVIDENCE_NOT_FOUND = "Evidence not found."


def _assert_can_verify(session: dict, student_id: str, db: Session) -> None:
    """May this session open and decide THIS student's evidence?

    The per-row twin of `_evidence_query`, so a claim that is listed can always
    be opened and one that is not listed cannot: a live grant of
    `mentor.verifications` whose reach covers the student, asked of
    app/verifiers.py. Every refusal is the same 404: an out-of-scope id must not
    be distinguishable from a wrong one.

    RULE 2 IS NO LONGER A DOOR HERE (2026-10-16). It was the first of two: a
    mentor's own mentee, or any student for the Main Admin. The first half went
    with the owner's decision that mentoring brings no verification; the second
    was a quiet widening -- a Main Admin holding a grant scoped to one college
    saw that college's queue and could still open and decide any student's
    claim by id. The grant's reach is now the whole answer for both.

    STILL NOT A CHANGE TO `_assert_can_access_student`. That function gates
    every other per-student staff endpoint, many of them writes (mentor notes,
    assessments); it keeps admitting a mentor to their own group there.
    """
    assert_can_verify(db, session, student_id, detail=_EVIDENCE_NOT_FOUND)


@router.get("/mentor/badge-evidence/pending", response_model=list[PendingEvidenceOut])
def pending_evidence(
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[PendingEvidenceOut]:
    """The verification queue, narrowed the same way both kinds of holder are.

    THIS IS THE QUEUE THE SKILLING SCREEN FILES INTO (2026-09-17). The claim
    form on `/student/skilling` stores the certificate and then posts
    `POST /student/badges/{code}/evidence`, which lands here; the faculty
    Verifications screen reads this list. It used to read `skill_claims`
    (`mentor.py::/skill-claims/pending`), a queue no client has written to
    since the Skilling screen replaced the per-skill claim form -- so every
    claim a student filed went into a table the mentor's screen never opened,
    and the mentor's screen showed a queue nothing could fill. The
    `skill_claims` endpoints stay for the resume builder's read of them.

    ONLY A GRANT OPENS IT, AND ITS REACH IS THE FENCE (2026-10-16). Until this
    date there were two ways to hold `mentor.verifications`: a FUNCTION derived
    from mentoring somebody, fenced to the mentor's own group, and a GRANT,
    fenced to its scope (2026-10-13, after 128 certificates from 26 students
    with no mentor sat pending where no faculty member could see them; the
    grant was ADDED to the group). The owner then asked for verification to be
    taken from every mentor and given only to the faculty the office chooses,
    so the function is gone (app/mentor_functions.py) and the group with it: a
    granted faculty member sees exactly the students the grant covers, their
    own mentees included only if the grant covers them. A faculty member with
    no grant is refused at the capability gate, whoever they mentor. Until the
    office grants somebody, a claim waits in this queue with nobody to see it.

    THE MAIN ADMIN IS HELD TO THE SAME GRANT. `_FACULTY_ONLY` keeps
    `mentor.verifications` out of the office account's baseline precisely so
    that looking at a stuck student's evidence is an audited grant it makes to
    itself, and a grant scoped to the college whose student is stuck shows
    that college's queue.
    """
    # mentor.verifications: held only as a GRANT (app/verifiers.py), by the
    # faculty the office chose -- or by the office itself, which grants itself
    # the queue when a student's evidence is stuck and revokes it after.
    require_capability(db, session, VERIFY_CAPABILITY)
    query = _evidence_query(db, session, response)
    if query is None:
        return []
    rows = db.execute(
        query.where(BadgeEvidence.status == EvidenceStatus.PENDING_VERIFICATION).order_by(
            BadgeEvidence.created_at
        )
    ).all()
    return [_pending_row(ev, name, usn, upload) for ev, name, usn, upload in rows]


@router.get("/mentor/badge-evidence/reviewed", response_model=list[PendingEvidenceOut])
def reviewed_evidence(
    response: Response,
    limit: int = Query(default=10, ge=1, le=50),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[PendingEvidenceOut]:
    """The queue's "Recently reviewed" strip: claims already decided, newest
    decision first, under the SAME scope as the pending list.

    `mentor.py::reviewed_skill_claims`'s reason, for this queue: a decision that
    vanishes from the screen the moment it is made is a decision the mentor
    cannot check they made correctly. Read-only, and it carries the review note,
    so the strip shows the outcome AND the words the student was given.
    """
    require_capability(db, session, VERIFY_CAPABILITY)
    query = _evidence_query(db, session, response)
    if query is None:
        return []
    rows = db.execute(
        query.where(BadgeEvidence.status != EvidenceStatus.PENDING_VERIFICATION)
        .order_by(BadgeEvidence.reviewed_at.desc().nullslast(), BadgeEvidence.created_at.desc())
        .limit(limit)
    ).all()
    return [_pending_row(ev, name, usn, upload) for ev, name, usn, upload in rows]


@router.get("/mentor/badge-evidence/{evidence_id}/file")
def evidence_file(
    evidence_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Stream the certificate behind a claim so the reviewer can actually read
    what they are approving. Same scope, and the same flattened 404, as the
    review endpoint."""
    # mentor.verifications: held only as a GRANT (app/verifiers.py), by the
    # faculty the office chose -- or by the office itself, which grants itself
    # the queue when a student's evidence is stuck and revokes it after.
    require_capability(db, session, VERIFY_CAPABILITY)
    ev = db.get(BadgeEvidence, evidence_id)
    if ev is None or ev.upload_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence not found.")
    _assert_can_verify(session, ev.student_id, db)
    upload = db.get(Upload, ev.upload_id)
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence not found.")
    try:
        content = read_bytes(upload.stored_name)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing.")
    return Response(
        content=content,
        media_type=upload.mime_type,
        headers={"Content-Disposition": content_disposition(upload.original_name)},
    )


def _award(db: Session, student_id: str, code: str, awarded_by: str, note: str | None) -> None:
    """Mint (or confirm) the EARNED row. Idempotent: an already-earned badge
    keeps its original stamp — a second approval adds evidence, not points."""
    badge = BADGE_BY_CODE[code]
    row = db.scalar(
        select(StudentBadge).where(
            StudentBadge.student_id == student_id, StudentBadge.badge_code == code
        )
    )
    if row is None:
        row = StudentBadge(student_id=student_id, badge_code=code)
        db.add(row)
    if row.status != StudentBadgeStatus.EARNED:
        row.status = StudentBadgeStatus.EARNED
        row.points_awarded = badge.points
        row.earned_at = datetime.now(timezone.utc)
        row.awarded_by_id = awarded_by
        row.award_note = note


class ReviewIn(BaseModel):
    decision: str  # APPROVE | REJECT | MORE_INFO
    # Optional on APPROVE; REQUIRED on REJECT and MORE_INFO, enforced in the
    # handler rather than here because the rule depends on the decision.
    note: str | None = Field(default=None, max_length=2000)


#: What a decision on the claim writes onto the certificate behind it, when
#: that certificate is still waiting for a verdict of its own.
_UPLOAD_VERDICT: dict[str, UploadStatus] = {
    "APPROVE": UploadStatus.VERIFIED,
    "REJECT": UploadStatus.REJECTED,
    "MORE_INFO": UploadStatus.NEEDS_CHANGES,
}


@router.post("/mentor/badge-evidence/{evidence_id}/review", response_model=PendingEvidenceOut)
def review_evidence(
    evidence_id: str,
    body: ReviewIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PendingEvidenceOut:
    # mentor.verifications: held only as a GRANT (app/verifiers.py), by the
    # faculty the office chose -- or by the office itself, which grants itself
    # the queue when a student's evidence is stuck and revokes it after.
    require_capability(db, session, VERIFY_CAPABILITY)
    ev = db.get(BadgeEvidence, evidence_id)
    if ev is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence not found.")
    _assert_can_verify(session, ev.student_id, db)

    decision = body.decision.upper()
    if decision not in ("APPROVE", "REJECT", "MORE_INFO"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="decision must be APPROVE, REJECT or MORE_INFO.",
        )
    if ev.status == EvidenceStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This evidence is already approved."
        )
    note = (body.note or "").strip() or None
    # A NO WITHOUT A REASON IS INDISTINGUISHABLE FROM A BROKEN SCREEN. The note
    # is the whole of what the student sees on Skilling under a claim that was
    # not verified, and since 2026-09-17 it is the whole of what their mail
    # says too. The Verifications form asks for it first; this is the
    # guarantee, so the rule does not depend on that form being the only
    # caller (test_skill_verification.py's argument, for this queue).
    if decision != "APPROVE" and not note:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Say why. The note is the only thing the student is told when a "
                "claim is not verified, so Reject and Request changes both need one."
            ),
        )

    now = datetime.now(timezone.utc)
    ev.review_note = note
    ev.reviewed_by_id = session["userId"]
    ev.reviewed_at = now
    if decision == "APPROVE":
        ev.status = EvidenceStatus.APPROVED
        _award(db, ev.student_id, ev.badge_code, session["userId"], "Approved evidence")
    elif decision == "REJECT":
        ev.status = EvidenceStatus.REJECTED
    else:
        ev.status = EvidenceStatus.MORE_INFO_REQUIRED

    # THE CERTIFICATE IS DECIDED WITH THE CLAIM (2026-09-17). The Skilling form
    # stores the file through `POST /student/uploads`, which writes the row
    # PENDING_REVIEW, and then files this claim against it -- so the same
    # certificate sat in the Documents queue AND the claim queue, and the two
    # could be decided differently. One act, both rows: verifying the claim
    # verifies the certificate, refusing it refuses the certificate, and the
    # student's Uploads screen reads the same note. Only a row still waiting is
    # written; a verdict somebody already gave the document is never rewritten.
    upload = db.get(Upload, ev.upload_id) if ev.upload_id else None
    if upload is not None and upload.status == UploadStatus.PENDING_REVIEW:
        upload.status = _UPLOAD_VERDICT[decision]
        upload.reviewed_by_id = session["userId"]
        upload.reviewed_at = now
        upload.review_note = note
    db.commit()
    db.refresh(ev)
    # THE STUDENT IS TOLD, WHICHEVER WAY IT WENT -- after the commit, so the
    # mail never describes a decision that did not land, and through
    # `deliver_once`, so a double-tap on Verify mails once. Never raises;
    # app/badge_mail.py says what None means.
    badge_mail.notify_student_of_decision(db, ev)

    student, name, usn = db.execute(
        select(Student, User.name, Student.usn)
        .join(User, Student.user_id == User.id)
        .where(Student.id == ev.student_id)
    ).one()
    return _pending_row(ev, name, usn, upload)


# --- manual award / revoke (§18) --------------------------------------------


class AwardIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.post("/mentor/students/{student_id}/badges/{code}/award", response_model=BadgeDashboardOut)
def manual_award(
    student_id: str,
    code: str,
    body: AwardIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> BadgeDashboardOut:
    """Manual award — how §8's readiness badges land when assessment thresholds
    are met, and §18's escape hatch for everything else.

    AN AWARD IS A VERIFICATION WITHOUT A CLAIM, SO IT HAS THE SAME DOOR
    (2026-10-16). It mints an EARNED skill badge with no evidence row behind it,
    and until this date any MENTOR could do that for their own mentee by rule 2
    alone -- which, once verification stopped coming with mentoring, would have
    been the one way a mentor could still mark a skill verified. The Main Admin
    keeps the escape hatch for any student; anybody else needs the verification
    grant covering this one. Readiness badges stay the Main Admin's alone,
    below.
    """
    if session.get("role") == "ADMIN":
        _assert_can_access_student(session, student_id, db)
    else:
        require_capability(db, session, VERIFY_CAPABILITY)
        if db.get(Student, student_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
        assert_can_verify(db, session, student_id, detail="Student not found.")
    if code not in BADGE_BY_CODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such badge.")
    if BADGE_BY_CODE[code].staff_awarded:
        # Readiness badges certify that assessment THRESHOLDS were met — a call
        # nothing in this handler verifies, so it stays with the role that can
        # also undo it. Revoke below is require_admin; an award any MENTOR
        # could mint but only the Main Admin could unwind is an asymmetry that only
        # ever accumulates points.
        require_admin(session)
    _award(db, student_id, code, session["userId"], (body.note or "").strip() or "Manually awarded")
    db.commit()
    return compose_badges(db.get(Student, student_id), db)


@router.post("/mentor/students/{student_id}/badges/{code}/revoke", response_model=BadgeDashboardOut)
def revoke_badge(
    student_id: str,
    code: str,
    body: AwardIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> BadgeDashboardOut:
    require_admin(session)
    if db.get(Student, student_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    row = db.scalar(
        select(StudentBadge).where(
            StudentBadge.student_id == student_id, StudentBadge.badge_code == code
        )
    )
    if row is None or row.status != StudentBadgeStatus.EARNED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Badge is not earned.")
    # THE BEFORE-IMAGE, BECAUSE THIS ROW CANNOT BE SOFT-DELETED IN PLACE.
    #
    # Revoking used to be the one destructive endpoint in the product that left
    # NO record of what it destroyed: the award row went, and with it the fact
    # that this student earned this badge, on this date, for these points, and
    # who awarded it. Every sibling destructive endpoint here already writes a
    # before-image — governance, the leave policies, the registration rules,
    # SWOC, the stage-rule catalogue — and this one did not.
    #
    # A `deleted_at` flag was the obvious alternative and `uq_student_badge`
    # rules it out. One row per (student, badge), and `_award` re-earns a badge
    # by UPDATING that same row: a flagged row would be revived and overwritten
    # the next time the badge was awarded, losing the revocation exactly as the
    # delete does. The record has to live somewhere the unique constraint does
    # not reach, and `audit_events` is where this codebase already puts it.
    #
    # The evidence history still stays, unchanged — the tile falls back to
    # whatever the remaining rows honestly derive to.
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="student_badge", entity_id=row.id, action="REVOKED",
        before={
            "student_id": row.student_id,
            "badge_code": row.badge_code,
            "status": row.status.value,
            "points_awarded": row.points_awarded,
            "earned_at": row.earned_at.isoformat() if row.earned_at else None,
            "awarded_by_id": row.awarded_by_id,
            "award_note": row.award_note,
        },
        after=None,
        event_type="badge.revoked",
        payload={"student_id": student_id, "badge_code": code,
                 "reason": (body.note or "").strip() or None},
    )
    db.delete(row)
    db.commit()
    return compose_badges(db.get(Student, student_id), db)


# --- assessment scores (§9, §18) --------------------------------------------


class AssessmentIn(BaseModel):
    checkpoint: str
    # capability value -> score. Partial entry is fine: speaking may be scored
    # weeks after the rest, exactly like the English baseline.
    scores: dict[str, float]


@router.post("/mentor/students/{student_id}/assessments", response_model=GrowthOut)
def record_assessments(
    student_id: str,
    body: AssessmentIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> GrowthOut:
    _assert_can_access_student(session, student_id, db)
    try:
        checkpoint = AssessmentCheckpoint(body.checkpoint)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="checkpoint must be T0–T4."
        )
    if not body.scores:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="No scores given."
        )
    for cap_key, score in body.scores.items():
        try:
            cap = CapabilityKind(cap_key)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown capability {cap_key!r}.",
            )
        if not (1 <= score <= 10):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Scores are on a 1–10 scale.",
            )
        row = db.scalar(
            select(CapabilityAssessment).where(
                CapabilityAssessment.student_id == student_id,
                CapabilityAssessment.capability == cap,
                CapabilityAssessment.checkpoint == checkpoint,
            )
        )
        if row is None:
            row = CapabilityAssessment(
                student_id=student_id, capability=cap, checkpoint=checkpoint, score=score
            )
            db.add(row)
        else:
            row.score = score  # upsert: a typo is corrected, not duplicated
        row.recorded_by_id = session["userId"]
    db.commit()
    return compose_growth(student_id, db)


# --- staff reads of one student (§15, §17) -----------------------------------


@router.get("/mentor/students/{student_id}/badges", response_model=BadgeDashboardOut)
def student_badges(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> BadgeDashboardOut:
    _assert_can_access_student(session, student_id, db)
    return compose_badges(db.get(Student, student_id), db)


@router.get("/mentor/students/{student_id}/growth", response_model=GrowthOut)
def student_growth(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> GrowthOut:
    _assert_can_access_student(session, student_id, db)
    return compose_growth(student_id, db)


class SkillProfileOut(BaseModel):
    """§17 — the consolidated profile: what does this student possess, what
    evidence supports it, how much have they improved."""

    student_id: str
    name: str
    usn: str | None
    stage: str
    points_total: int
    badges: BadgeDashboardOut
    growth: GrowthOut
    evidence_counts: dict[str, int]  # approved evidence per §11 type


@router.get("/mentor/students/{student_id}/skill-profile", response_model=SkillProfileOut)
def skill_profile(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SkillProfileOut:
    _assert_can_access_student(session, student_id, db)
    student, name = db.execute(
        select(Student, User.name).join(User, Student.user_id == User.id).where(Student.id == student_id)
    ).one()
    badges = compose_badges(student, db)
    counts = {t.value: 0 for t in EvidenceType}
    for ev in db.scalars(
        select(BadgeEvidence).where(
            BadgeEvidence.student_id == student_id,
            BadgeEvidence.status == EvidenceStatus.APPROVED,
        )
    ).all():
        counts[ev.evidence_type.value] += 1
    return SkillProfileOut(
        student_id=student_id,
        name=name,
        usn=student.usn,
        stage=student.current_stage.value,
        points_total=badges.points_total,
        badges=badges,
        growth=compose_growth(student_id, db),
        evidence_counts=counts,
    )


# --- cohort views + export (§15, §18) ----------------------------------------


class CohortCapabilityRow(BaseModel):
    capability: str
    label: str
    averages: dict[str, float | None]  # checkpoint -> cohort mean (assessed only)
    assessed_counts: dict[str, int]


class CohortOut(BaseModel):
    students: int
    badges_earned_by_category: dict[str, int]
    capabilities: list[CohortCapabilityRow]


@router.get("/admin/badges/cohort", response_model=CohortOut)
def cohort_view(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> CohortOut:
    require_admin(session)
    total_students = len(db.scalars(select(Student.id)).all())

    by_cat = {c.value: 0 for c in BadgeCategory}
    for sb in db.scalars(
        select(StudentBadge).where(StudentBadge.status == StudentBadgeStatus.EARNED)
    ).all():
        badge = BADGE_BY_CODE.get(sb.badge_code)
        if badge:
            by_cat[badge.category.value] += 1

    sums: dict[tuple[CapabilityKind, str], list[float]] = defaultdict(list)
    for r in db.scalars(select(CapabilityAssessment)).all():
        sums[(r.capability, r.checkpoint.value)].append(r.score)
    rows = []
    checkpoints = [c.value for c in AssessmentCheckpoint]
    for cap in CapabilityKind:
        averages = {}
        counts = {}
        for cp in checkpoints:
            scores = sums.get((cap, cp), [])
            counts[cp] = len(scores)
            averages[cp] = round(sum(scores) / len(scores), 2) if scores else None
        rows.append(
            CohortCapabilityRow(
                capability=cap.value,
                label=CAPABILITY_LABEL[cap],
                averages=averages,
                assessed_counts=counts,
            )
        )
    return CohortOut(
        students=total_students, badges_earned_by_category=by_cat, capabilities=rows
    )


@router.get("/admin/badges/export.csv")
def export_cohort_csv(
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """§18's cohort report: one row per student — points, earned count per
    category, mean growth from baseline. A spreadsheet, because that is what a
    placement office actually forwards.

    THE SAME GATE AS THE SCREEN, and it was not. `cohort_view` above renders this
    data and is `require_admin`; this endpoint answered to `admin.exports`, a
    GRANTABLE capability. So a faculty member granted Exports could download the
    whole cohort's badge and growth record — one row per student — while being
    refused the screen that shows exactly the same thing.

    Narrowed rather than widened deliberately: the screen is the authority on who
    may see cohort-wide student data, and when two gates disagree the safe
    reconciliation is the tighter one. If the placement office genuinely needs a
    granted non-admin to pull this, widen BOTH together — the fix is not to give
    the file a weaker lock than the window.

    B14 STILL APPLIES ON TOP OF THAT GATE, and applying it here is not
    ceremony. `scope_filter` resolves to `everything` for every caller this
    endpoint currently admits, because `require_admin` admits the Main Admin
    alone and `admin.exports` is in its baseline — so today this narrows
    nothing. It is written anyway because the comment above invites exactly one
    future change ("widen BOTH together"), and a widened gate with no scope
    underneath it is the whole-programme download this task exists to stop. The
    receipt and the personal-column rule are not conditional on any of that: the
    Main Admin's own downloads are the ones most worth having a record of.
    """
    require_admin(session)
    reach = scope_filter(db, session, "admin.exports")
    carried_pii = carries_personal_columns(db, session)
    students = (
        []
        if reach.nothing
        else db.execute(
            select(Student, User.name)
            .join(User, Student.user_id == User.id)
            .where(Student.id.in_(reach.student_ids()))
            .order_by(User.name)
        ).all()
    )

    earned: dict[str, list[StudentBadge]] = defaultdict(list)
    for sb in db.scalars(
        select(StudentBadge).where(StudentBadge.status == StudentBadgeStatus.EARNED)
    ).all():
        earned[sb.student_id].append(sb)

    assessments: dict[str, dict[CapabilityKind, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for r in db.scalars(select(CapabilityAssessment)).all():
        assessments[r.student_id][r.capability][r.checkpoint.value] = r.score

    cat_headers = [CATEGORY_LABEL[c] for c in BadgeCategory]
    header = ["Name", "USN", "REEP stage", "Points", *cat_headers, "Mean growth from T0"]
    body: list[list[object]] = []
    checkpoints = [c.value for c in AssessmentCheckpoint]
    for student, name in students:
        badges = earned.get(student.id, [])
        per_cat = {c: 0 for c in BadgeCategory}
        for sb in badges:
            bdef = BADGE_BY_CODE.get(sb.badge_code)
            if bdef:
                per_cat[bdef.category] += 1
        growths = []
        for cp_scores in assessments.get(student.id, {}).values():
            baseline = cp_scores.get("T0")
            assessed = [cp_scores[cp] for cp in checkpoints if cp in cp_scores]
            if baseline is not None and len(assessed) > 1:
                growths.append(assessed[-1] - baseline)
        mean_growth = round(sum(growths) / len(growths), 2) if growths else ""
        body.append(
            [
                name,
                student.usn or "",
                student.current_stage.value,
                sum(sb.points_awarded for sb in badges),
                *[per_cat[c] for c in BadgeCategory],
                mean_growth,
            ]
        )
    # Every cell goes through the shared formula-injection guard now, not only
    # the two that were obviously typed by a person: a REEP stage is an enum
    # today and a cohort label tomorrow, and the guard costs one comparison.
    header, body = drop_personal(header, body, ["Name", "USN"], carry=carried_pii)
    record_export(
        db, session=session, request=request, kind="badges",
        filters=scope_note(reach), rows=len(body), carried_pii=carried_pii,
    )
    return csv_response(
        header, body, "reep-cohort-skill-report.csv",
        reach=reach, carried_pii=carried_pii,
    )

# --- the Approved Certification Catalogue (§12) LIVES IN admin_catalogue.py --
#
# `GET/POST/PATCH /api/admin/approved-certifications` and their two schemas
# moved to `app/routers/admin_catalogue.py` in B13 (2026-09-13). THE URLs DID
# NOT CHANGE; what changed is that the catalogue is now scoped per college and
# per course, and its gate is the grantable `admin.catalogue` rather than
# `require_admin` — which is what the Catalogue screen always claimed to open
# on.
#
# They moved rather than growing here because this module is the EVIDENCE
# QUEUE: every other endpoint in it names a student and goes through rule 2's
# `_assert_can_access_student`. The certification catalogue names no student at
# all, and leaving it here meant the one list on the Catalogue screen was
# governed by the one module that does not know what a course is.
