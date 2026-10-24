"""Mentor area — staff (MENTOR / ADMIN) views of their mentees.

Scope rule (mirrors mentorScope()/menteeWhere() in the Next.js app, and the
AGENTS.md guidance): a MENTOR sees only students in their Mentor group;
the Main Admin sees all. A MENTOR with NO Mentor group (no mentorId in the
session) sees NOBODY — never the whole programme.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..governance import require_capability
from ..document_store import content_disposition, read_bytes
from ..identity import get_current_session
from ..models.alert import Alert
from ..models.badge import BadgeEvidence, EvidenceStatus
from ..models.lab import LabSession
from ..models.mentor_note import MentorAction, MentorNote
from ..models.offer import OfferStatus, PlacementOffer
from ..models.skill import Skill, SkillClaim, StudentSkill
from ..models.upload import Upload, UploadStatus
from ..models.user import Student, User
from ..policies import assert_student_scope
from ..verifiers import (
    VERIFY_CAPABILITY,
    assert_can_verify,
    can_verify,
    narrow_to_reach,
    verification_reach,
)

router = APIRouter(prefix="/mentor", tags=["mentor"])

# DIRECTOR is not staff any more — it grants nothing. See app/policies.py.
_STAFF = {"MENTOR", "ADMIN"}


def require_mentor(session: dict) -> dict:
    if session.get("role") not in _STAFF:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required.")
    return session


class MenteeOut(BaseModel):
    student_id: str
    name: str
    usn: str | None
    current_stage: str
    current_semester: int
    #: Whether this account may open the student's complete record
    #: (/admin/students/:id). The Mentee Log draws "Full record" only when it
    #: is true, so the link never leads to a refusal.
    record_openable: bool = False


def _openable_mentees(db: Session, session: dict, student_ids: list[str]) -> set[str]:
    """Which of these students the Student 360 page would open for this
    account. The same rule as the "Student records" list: a faculty member's
    own mentees open only beside a programme-wide "View student records" grant,
    and a scoped grant opens the students it names, so a grant for another
    department opens none of them.

    Imported inside the call: `admin_student_record` imports this module for
    rule 2's gate, so a module-level import would close a cycle.
    """
    if not student_ids:
        return set()
    from .admin_student_record import _openable_clause

    query = select(Student.id).where(Student.id.in_(student_ids))
    clause = _openable_clause(db, session)
    if clause is not None:
        query = query.where(clause)
    return set(db.scalars(query))


@router.get("/mentees", response_model=list[MenteeOut])
def mentees(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[MenteeOut]:
    # mentor.mentees: the mentee log and the meeting notes are a faculty
    # member's instruments. The Main Admin holds neither by role - it has no
    # mentees - and grants itself the capability in Governance if it needs to
    # look. Additive over the baseline, so faculty access is unchanged.
    require_capability(db, session, "mentor.mentees")
    # A REMOVED student (users.deleted_at, 2026-09-16) is off every screen,
    # this one included; the row is kept for Restore.
    query = (
        select(Student, User.name)
        .join(User, Student.user_id == User.id)
        .where(User.deleted_at.is_(None))
    )

    if session["role"] == "MENTOR":
        mentor_id = session.get("mentorId")
        if not mentor_id:
            return []  # no Mentor group => nobody (never the whole programme)
        query = query.where(Student.mentor_id == mentor_id)
    # the Main Admin: no narrowing — the whole programme.

    rows = db.execute(query.order_by(User.name)).all()
    openable = _openable_mentees(db, session, [student.id for student, _ in rows])
    return [
        MenteeOut(
            student_id=student.id,
            name=name,
            usn=student.usn,
            current_stage=student.current_stage.value,
            current_semester=student.current_semester,
            record_openable=student.id in openable,
        )
        for student, name in rows
    ]


def _assert_can_access_student(
    session: dict,
    student_id: str,
    db: Session,
    *,
    allow_handover: bool = False,
    allow_records_grant: bool = False,
) -> Student:
    """Rule 2's gate. ONE NAME, ONE IMPLEMENTATION -- this delegates.

    `allow_records_grant` (2026-10-15) is the same kind of keyword for the
    office's scoped "View student records" grant; see `assert_student_scope`.

    `allow_handover` (B9.1) is a PARAMETER and not a second body, which is what
    keeps `tests/test_codebase_guards.py`'s AST guard satisfied: this function
    is still one `return assert_student_scope(...)` and every question it can
    answer is still answered there. It defaults to False, so all 36 call sites
    keep exactly today's behaviour, and it is passed True only where the request
    is a GET. Read `policies.assert_student_scope` for why it cannot be on by
    default: fifteen of those call sites are WRITES, and this function cannot
    tell them apart.

    This used to be a second, independent implementation of "may this staff
    member see this student", alongside `policies.assert_student_scope` which
    the /v1 routers call. Two functions for the project's most load-bearing
    invariant is how a scope rule ends up with two answers depending on which
    URL a request happened to hit -- and rule 2 is exactly the rule where a
    wrong answer is a data-exposure bug, not a UX one.

    The policies version is the survivor because it was STRICTLY STRONGER, not
    merely different: it carried the same MENTOR-group narrowing this one did,
    and additionally required the student to be in the caller's tenant, and
    additionally refused a session with no userId. Keeping the weaker one and
    deleting the stronger would have been a silent downgrade at 25 call sites.

    Both names survive because both are load-bearing in prose: half a dozen
    module docstrings name `_assert_can_access_student` as THE gate, and
    rewriting 25 call sites to prove a point would put the risk in the diff
    rather than in the design.

    Tenant narrowing is inert on a database that has no memberships --
    `tenant_id_for_session` returns None and the behaviour is byte-identical to
    what this function did before. Where memberships DO exist it narrows, which
    is the rule /v1 already enforced and this side silently did not.

    Returns the Student now rather than None, so a caller that needs the row
    does not fetch it a second time. Existing callers ignore it and are
    unaffected.
    """
    return assert_student_scope(
        session,
        student_id,
        db,
        allow_handover=allow_handover,
        allow_records_grant=allow_records_grant,
    )


class NoteOut(BaseModel):
    id: str
    note_text: str
    linked_action: str
    # Both optional, both student-visible. The Mentor Meeting Log renders
    # "1:1 review · Cabin 3" as the heading above each note; without them the
    # student's screen falls back to the linked action, which is a worse
    # heading but never a wrong one.
    title: str | None
    location: str | None
    meeting_at: datetime
    created_at: datetime


class NoteIn(BaseModel):
    note_text: str = Field(min_length=1, max_length=4000)
    linked_action: str = "NONE"
    # NOT backfilled onto existing notes and NOT required on new ones. A mentor
    # who types only a note has written a real note; inventing a heading for it
    # would put words in their mouth on a screen the student reads.
    title: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    meeting_at: datetime | None = None


def _note_out(note: MentorNote) -> NoteOut:
    return NoteOut(
        id=note.id,
        note_text=note.note_text,
        linked_action=note.linked_action.value,
        title=note.title,
        location=note.location,
        meeting_at=note.meeting_at,
        created_at=note.created_at,
    )


@router.get("/students/{student_id}/notes", response_model=list[NoteOut])
def list_notes(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[NoteOut]:
    # mentor.mentees: the mentee log and the meeting notes are a faculty
    # member's instruments. The Main Admin holds neither by role - it has no
    # mentees - and grants itself the capability in Governance if it needs to
    # look. Additive over the baseline, so faculty access is unchanged.
    require_capability(db, session, "mentor.mentees")
    # B9.1. A GET, so the handover window applies: a mentor who handed this
    # student over in the last 90 days can still READ what they wrote about
    # them. The POST and DELETE below are deliberately NOT passed the flag --
    # the window is a read, and the note they wrote in March is what the
    # incoming mentor rings them about.
    _assert_can_access_student(session, student_id, db, allow_handover=True)
    rows = db.scalars(
        select(MentorNote)
        .where(MentorNote.student_id == student_id, MentorNote.deleted_at.is_(None))
        .order_by(MentorNote.meeting_at.desc())
    ).all()
    return [_note_out(n) for n in rows]


@router.post(
    "/students/{student_id}/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED
)
def add_note(
    student_id: str,
    body: NoteIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> NoteOut:
    # mentor.mentees: the mentee log and the meeting notes are a faculty
    # member's instruments. The Main Admin holds neither by role - it has no
    # mentees - and grants itself the capability in Governance if it needs to
    # look. Additive over the baseline, so faculty access is unchanged.
    require_capability(db, session, "mentor.mentees")
    _assert_can_access_student(session, student_id, db)
    mentor_id = session.get("mentorId")
    if not mentor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a mentor (with a Mentor profile) can author notes.",
        )
    try:
        action = MentorAction(body.linked_action)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid linked_action."
        )
    note = MentorNote(
        mentor_id=mentor_id,
        student_id=student_id,
        note_text=body.note_text,
        linked_action=action,
        title=(body.title or "").strip() or None,
        location=(body.location or "").strip() or None,
        meeting_at=body.meeting_at or datetime.now(timezone.utc),
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _note_out(note)


@router.delete("/students/{student_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    student_id: str,
    note_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Remove a meeting note. The student's Mentor Meeting Log reads these, so
    this is the mentor taking back words the student may already have seen —
    which is why it is narrower than the write: rule 2's gate first, then the
    note must belong to THIS student (a note id from another mentee is a 404,
    not a cross-student delete), and a MENTOR may only remove a note they
    authored. the Main Admin may remove any note in the programme.
    """
    # mentor.mentees: the mentee log and the meeting notes are a faculty
    # member's instruments. The Main Admin holds neither by role - it has no
    # mentees - and grants itself the capability in Governance if it needs to
    # look. Additive over the baseline, so faculty access is unchanged.
    require_capability(db, session, "mentor.mentees")
    _assert_can_access_student(session, student_id, db)
    note = db.get(MentorNote, note_id)
    # `db.get` is a primary-key lookup and takes no WHERE clause, so the
    # already-retracted case has to be checked on the attribute. Without this a
    # second DELETE re-stamps deleted_at and answers 204, which reads to the
    # caller as "there was something here and I removed it" when there was not.
    if note is None or note.student_id != student_id or note.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found.")
    if session["role"] == "MENTOR" and note.mentor_id != session.get("mentorId"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the mentor who wrote a note can remove it.",
        )
    # Soft: the row stays, so "what was said and later withdrawn" is still
    # answerable. See the column comment on MentorNote.deleted_at for why a
    # note the student may already have read is not destroyed outright.
    note.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class AlertOut(BaseModel):
    id: str
    student_id: str
    student_name: str
    rule_triggered: str
    severity: str
    message: str
    triggered_at: datetime
    resolved: bool


def _alert_out(alert: Alert, student_name: str) -> AlertOut:
    return AlertOut(
        id=alert.id,
        student_id=alert.student_id,
        student_name=student_name,
        rule_triggered=alert.rule_triggered.value,
        severity=alert.severity.value,
        message=alert.message,
        triggered_at=alert.triggered_at,
        resolved=alert.resolved_at is not None,
    )


#: The alert feed is newest-first and BOUNDED. It had no limit at all, and the
#: two callers pull in opposite directions: a MENTOR's query is narrowed to
#: their own group, but the Main Admin's is not narrowed at all, so `open_only`
#: off asked for every alert ever raised for every student in the programme,
#: sorted, in one response. The screen shows a feed; nobody scrolls to row nine
#: thousand. `Alert.id` is the tiebreak, because two alerts raised in the same
#: transaction share a `triggered_at` and an unstable sort silently drops and
#: repeats rows across a paged read.
_MAX_ALERTS_LISTED = 200


@router.get("/alerts", response_model=list[AlertOut])
def alerts(
    open_only: bool = True,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AlertOut]:
    require_mentor(session)
    query = (
        select(Alert, User.name)
        .join(Student, Alert.student_id == Student.id)
        .join(User, Student.user_id == User.id)
    )
    if open_only:
        query = query.where(Alert.resolved_at.is_(None))
    if session["role"] == "MENTOR":
        mentor_id = session.get("mentorId")
        if not mentor_id:
            return []
        query = query.where(Student.mentor_id == mentor_id)
    rows = db.execute(
        query.order_by(Alert.triggered_at.desc(), Alert.id).limit(_MAX_ALERTS_LISTED)
    ).all()
    return [_alert_out(a, name) for a, name in rows]


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(
    alert_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AlertOut:
    require_mentor(session)
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found.")
    _assert_can_access_student(session, alert.student_id, db)
    if alert.resolved_at is None:
        alert.resolved_at = datetime.now(timezone.utc)
        alert.resolved_by = session["userId"]
        db.commit()
        db.refresh(alert)
    name = db.scalar(
        select(User.name).join(Student, Student.user_id == User.id).where(Student.id == alert.student_id)
    )
    return _alert_out(alert, name or "")


def require_admin(session: dict) -> dict:
    """The Main Admin, and nobody else. THE ONE console gate.

    THERE USED TO BE TWO. `require_admin` admitted {DIRECTOR, ADMIN} to the
    console's screens and this admitted ADMIN alone, and the difference between
    them was the whole of what DIRECTOR meant. With DIRECTOR granting nothing
    (see app/policies.py), the two gates became the same test written twice —
    and two names for one rule is how a screen ends up guarded by the weaker one
    because that was the import already at the top of the file.

    So `require_admin` is gone and its 29 call sites now name this. The name
    is also the honest one: what they were protecting was never a director, it
    was the office.

    Faculty do not reach console screens through a role at all. They reach the
    screens the Main Admin grants them, through `require_capability` — which is
    a different question with a different answer, and stays separate.
    """
    if session.get("role") != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Main Admin access required."
        )
    return session


class PendingOfferOut(BaseModel):
    id: str
    student_id: str
    student_name: str
    job_title: str
    organisation: str
    role_type: str
    ctc_inr: int
    status: str


def _offer_row(offer: PlacementOffer, student_name: str) -> PendingOfferOut:
    return PendingOfferOut(
        id=offer.id,
        student_id=offer.student_id,
        student_name=student_name,
        job_title=offer.job_title,
        organisation=offer.organisation,
        role_type=offer.role_type.value,
        ctc_inr=offer.ctc_inr,
        status=offer.status.value,
    )


@router.get("/offers/pending", response_model=list[PendingOfferOut])
def pending_offers(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[PendingOfferOut]:
    """Offers waiting on the office's decision.

    CHECKED AGAINST B1.4'S LIST AND LEFT ALONE. It was carried into this task as
    one of two "staff queues that already narrow by mentor group and are the
    remaining unscoped leaks"; it is neither. There is no mentor-group narrowing
    in this function and there is no leak: `require_admin` admits the Main Admin
    alone, so the only session that reaches it holds every programme capability
    by baseline and any reach computed here would resolve to `everything`.
    Scoping it becomes real work when approving an offer becomes a grantable
    function (`admin.placement` on this route rather than the role), and then it
    is one `scope_filter` call keyed on that capability — the funnel this queue
    feeds, `console.placement`, is already written that way and is the shape to
    copy.
    """
    require_admin(session)
    rows = db.execute(
        select(PlacementOffer, User.name)
        .join(Student, PlacementOffer.student_id == Student.id)
        .join(User, Student.user_id == User.id)
        .where(PlacementOffer.status == OfferStatus.PENDING_APPROVAL)
        .order_by(PlacementOffer.created_at)
    ).all()
    return [_offer_row(o, name) for o, name in rows]


class DecisionIn(BaseModel):
    decision: str  # "APPROVE" | "REJECT"
    note: str | None = None


@router.post("/offers/{offer_id}/decision", response_model=PendingOfferOut)
def decide_offer(
    offer_id: str,
    body: DecisionIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PendingOfferOut:
    require_admin(session)
    offer = db.get(PlacementOffer, offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found.")
    if offer.status != OfferStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a pending offer can be decided."
        )
    decision = body.decision.upper()
    if decision == "APPROVE":
        offer.status = OfferStatus.APPROVED
    elif decision == "REJECT":
        offer.status = OfferStatus.REJECTED
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="decision must be APPROVE or REJECT.",
        )
    offer.approved_by_id = session["userId"]
    offer.decided_at = datetime.now(timezone.utc)
    offer.decision_note = body.note
    db.commit()
    db.refresh(offer)
    name = db.scalar(
        select(User.name).join(Student, Student.user_id == User.id).where(Student.id == offer.student_id)
    )
    return _offer_row(offer, name or "")


class FocusRowOut(BaseModel):
    id: str
    course_code: str
    module: str
    activity: str
    duration_min: int | None
    check_in_at: datetime
    mentor_confirmed: bool


def _focus_row(ls: LabSession) -> FocusRowOut:
    return FocusRowOut(
        id=ls.id,
        course_code=ls.course_code,
        module=ls.module,
        activity=ls.activity.value,
        duration_min=ls.duration_min,
        check_in_at=ls.check_in_at,
        mentor_confirmed=ls.mentor_confirmed,
    )


@router.get("/students/{student_id}/focus", response_model=list[FocusRowOut])
def student_focus(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[FocusRowOut]:
    _assert_can_access_student(session, student_id, db)
    rows = db.scalars(
        select(LabSession)
        .where(LabSession.student_id == student_id)
        .order_by(LabSession.check_in_at.desc())
    ).all()
    return [_focus_row(ls) for ls in rows]


@router.post("/focus/{session_id}/confirm", response_model=FocusRowOut)
def confirm_focus(
    session_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> FocusRowOut:
    require_mentor(session)
    ls = db.get(LabSession, session_id)
    if ls is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    _assert_can_access_student(session, ls.student_id, db)
    ls.mentor_confirmed = True
    db.commit()
    db.refresh(ls)
    return _focus_row(ls)


class UploadOut(BaseModel):
    id: str
    student_id: str
    student_name: str
    kind: str
    cert_code: str | None
    title: str
    original_name: str
    mime_type: str
    size_bytes: int
    status: str
    reviewed_by_id: str | None
    reviewed_at: datetime | None
    review_note: str | None
    uploaded_at: datetime


def _upload_out(u: Upload, student_name: str) -> UploadOut:
    return UploadOut(
        id=u.id,
        student_id=u.student_id,
        student_name=student_name,
        kind=u.kind.value,
        cert_code=u.cert_code,
        title=u.title,
        original_name=u.original_name,
        mime_type=u.mime_type,
        size_bytes=u.size_bytes,
        status=u.status.value,
        reviewed_by_id=u.reviewed_by_id,
        reviewed_at=u.reviewed_at,
        review_note=u.review_note,
        uploaded_at=u.uploaded_at,
    )


@router.get("/uploads/pending", response_model=list[UploadOut])
def pending_uploads(
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[UploadOut]:
    """Documents awaiting review — profile photos, certificate proofs, offer
    letters — for the faculty the office chose to verify, narrowed to their
    grant's reach.

    THE SAME DOOR AS THE SKILL-CLAIM QUEUE (2026-10-16). This list is the
    Documents half of the Skill Verifications screen, so it follows the same
    rule: a grant of "Verify skills & evidence" (app/verifiers.py), never
    mentoring. It used to be the mentor's own group and, for the Main Admin,
    every pending file on the deployment with no grant at all; a screen whose
    two halves answered to different rules would have shown a granted
    verifier an always-empty Documents list and left every mentor a
    verification door with no screen in front of it.

    A CERTIFICATE THAT BACKS A PENDING BADGE CLAIM IS NOT LISTED HERE
    (2026-09-17). The Skilling claim form stores the certificate through
    `POST /student/uploads` and then files `badge_evidence` against it, so the
    same file used to sit in this queue AND in the claim queue on the same
    screen, and the two could be decided differently — the document verified,
    the claim rejected. Reviewing the claim now decides the certificate with
    it (`badge_verification.review_evidence`), so while the claim is pending
    the file is the claim's and appears once, on the claim card. A document no
    claim stands on — a photo, an offer letter, a report — is reviewed here
    exactly as before.
    """
    require_mentor(session)
    require_capability(db, session, VERIFY_CAPABILITY)
    claimed = select(BadgeEvidence.upload_id).where(
        BadgeEvidence.status == EvidenceStatus.PENDING_VERIFICATION,
        BadgeEvidence.upload_id.is_not(None),
    )
    query = narrow_to_reach(
        select(Upload, User.name)
        .join(Student, Upload.student_id == Student.id)
        .join(User, Student.user_id == User.id)
        .where(Upload.status == UploadStatus.PENDING_REVIEW, Upload.id.not_in(claimed)),
        verification_reach(db, session, response),
    )
    if query is None:
        return []  # a grant whose reach is nothing (never the whole programme)
    rows = db.execute(query.order_by(Upload.uploaded_at)).all()
    return [_upload_out(u, name) for u, name in rows]


class UploadReviewIn(BaseModel):
    decision: str  # "VERIFY" | "REJECT"
    note: str | None = None


@router.post("/uploads/{upload_id}/review", response_model=UploadOut)
def review_upload(
    upload_id: str,
    body: UploadReviewIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> UploadOut:
    """Verify or reject a submitted document. Only by a holder of the
    verification grant whose reach covers the student (app/verifiers.py) --
    the same door as the list above, and as a skill claim's decision."""
    require_mentor(session)
    require_capability(db, session, VERIFY_CAPABILITY)
    up = db.get(Upload, upload_id)
    if up is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")
    assert_can_verify(db, session, up.student_id, detail="Upload not found.")
    if up.status != UploadStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a pending upload can be reviewed."
        )
    decision = body.decision.upper()
    if decision in ("VERIFY", "VERIFIED", "APPROVE"):
        up.status = UploadStatus.VERIFIED
    elif decision in ("REJECT", "REJECTED"):
        up.status = UploadStatus.REJECTED
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="decision must be VERIFY or REJECT.",
        )
    up.reviewed_by_id = session["userId"]
    up.reviewed_at = datetime.now(timezone.utc)
    up.review_note = body.note
    db.commit()
    db.refresh(up)
    name = db.scalar(
        select(User.name).join(Student, Student.user_id == User.id).where(Student.id == up.student_id)
    )
    return _upload_out(up, name or "")


class SkillClaimReviewOut(BaseModel):
    id: str
    student_id: str
    student_name: str
    skill_id: str
    skill_name: str
    # NULL once the student has deleted the certificate. The claim survives.
    upload_id: str | None
    claimed_level: int
    status: str
    student_note: str | None
    reviewed_by_id: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    # What the evidence behind the claim actually IS, read off the Upload row so
    # the reviewer's card can name it before they open the file. All three are
    # optional because a claim can outlive its upload — which is now the ORDINARY
    # case, not the unlikely one: the FK became ondelete=SET NULL so that a
    # student deleting their certificate no longer destroys the mentor's review
    # along with it (models/skill.py).
    evidence_kind: str | None = None
    evidence_title: str | None = None
    evidence_file_name: str | None = None


def _claim_out(
    sc: SkillClaim, student_name: str, skill_name: str, upload: Upload | None = None
) -> SkillClaimReviewOut:
    return SkillClaimReviewOut(
        id=sc.id,
        student_id=sc.student_id,
        student_name=student_name,
        skill_id=sc.skill_id,
        skill_name=skill_name,
        upload_id=sc.upload_id,
        claimed_level=sc.claimed_level,
        status=sc.status.value,
        student_note=sc.student_note,
        reviewed_by_id=sc.reviewed_by_id,
        reviewed_at=sc.reviewed_at,
        review_note=sc.review_note,
        created_at=sc.created_at,
        evidence_kind=upload.kind.value if upload is not None else None,
        evidence_title=upload.title if upload is not None else None,
        evidence_file_name=upload.original_name if upload is not None else None,
    )


def _claim_query(db: Session, session: dict, response: Response | None = None):
    """The claim rows a staff member may see, with the names the card needs.

    Narrowed IN SQL to the verification grant's reach (app/verifiers.py), or
    None for a reach of nothing (`[]` at the call sites). Shared by the pending
    queue and the reviewed history so the two lists cannot disagree about
    scope.

    THE LEGACY QUEUE FOLLOWS THE GRANT TOO (2026-10-16). No screen reads it --
    the Skilling claim form files `badge_evidence` now -- but its review still
    marks a `student_skills` row verified, which is what the resume prints under
    "Verified Skills". Left on rule 2 it would have been the one way a mentor
    could still verify a skill after the owner took verification from every
    mentor, so it answers to the same grant as every other verification door.
    """
    query = (
        select(SkillClaim, User.name, Skill.name, Upload)
        .join(Student, SkillClaim.student_id == Student.id)
        .join(User, Student.user_id == User.id)
        .join(Skill, SkillClaim.skill_id == Skill.id)
        .outerjoin(Upload, SkillClaim.upload_id == Upload.id)
    )
    return narrow_to_reach(query, verification_reach(db, session, response))


@router.get("/skill-claims/pending", response_model=list[SkillClaimReviewOut])
def pending_skill_claims(
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[SkillClaimReviewOut]:
    """Skill claims awaiting review, narrowed to the verification grant."""
    require_mentor(session)
    require_capability(db, session, VERIFY_CAPABILITY)
    query = _claim_query(db, session, response)
    if query is None:
        return []
    rows = db.execute(
        query.where(SkillClaim.status == UploadStatus.PENDING_REVIEW).order_by(
            SkillClaim.created_at
        )
    ).all()
    return [_claim_out(sc, sname, skname, up) for sc, sname, skname, up in rows]


@router.get("/skill-claims/reviewed", response_model=list[SkillClaimReviewOut])
def reviewed_skill_claims(
    response: Response,
    limit: int = Query(default=10, ge=1, le=50),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[SkillClaimReviewOut]:
    """The queue's "Recently reviewed" strip: claims already decided, newest
    decision first, under the SAME scope as the pending list.

    A decision that vanishes from the screen the moment it is made is a decision
    the mentor cannot check they made correctly. This is read-only and carries
    the review note, so the strip shows the outcome AND the words the student
    was given — the two things a mentor asks "did I really say that?" about.
    """
    require_mentor(session)
    require_capability(db, session, VERIFY_CAPABILITY)
    query = _claim_query(db, session, response)
    if query is None:
        return []
    rows = db.execute(
        query.where(SkillClaim.status != UploadStatus.PENDING_REVIEW)
        .order_by(SkillClaim.reviewed_at.desc().nullslast(), SkillClaim.created_at.desc())
        .limit(limit)
    ).all()
    return [_claim_out(sc, sname, skname, up) for sc, sname, skname, up in rows]


@router.get("/uploads/{upload_id}/file")
def download_student_upload(
    upload_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Stream a mentee's uploaded file so the reviewer can actually read it.

    Reviewing a skill claim means looking at the certificate behind it, and the
    only download route was /student/uploads/{id}/file, which is scoped to the
    uploader — a mentor hitting it got a 404 for a file they are being asked to
    approve. The queue was asking people to verify evidence they could not open.

    Scoped by `_assert_can_access_student`, the same gate every other mentor
    read goes through, so a mentor still cannot reach a student outside their
    group. ONE MORE READ-ONLY DOOR (2026-10-16): a holder of the verification
    grant whose reach covers the student, because the Documents queue on the
    Skill Verifications screen opens its files here and a verifier the office
    chose need not mentor anybody. Read-only, so it widens no write.
    """
    require_mentor(session)
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")
    try:
        _assert_can_access_student(session, upload.student_id, db)
    except HTTPException:
        if not can_verify(db, session, upload.student_id):
            raise
    try:
        content = read_bytes(upload.stored_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing."
        )
    return Response(
        content=content,
        media_type=upload.mime_type,
        # Same RFC 6266 encoding as the student route: a non-latin-1 filename
        # interpolated raw raises inside Response.__init__.
        headers={"Content-Disposition": content_disposition(upload.original_name)},
    )


class SkillClaimReviewIn(BaseModel):
    decision: str  # "GRANT" | "CHANGES" | "REJECT"
    granted_level: int | None = Field(default=None, ge=1, le=5)
    note: str | None = None


@router.post("/skill-claims/{claim_id}/review", response_model=SkillClaimReviewOut)
def review_skill_claim(
    claim_id: str,
    body: SkillClaimReviewIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SkillClaimReviewOut:
    """Grant (optionally at a reduced level) or reject a skill claim. Granting
    upserts the student's verified StudentSkill at the granted level and points
    it at the evidence upload — the claim is how a skill becomes verified.
    Only by a holder of the verification grant covering the student."""
    require_mentor(session)
    require_capability(db, session, VERIFY_CAPABILITY)
    sc = db.get(SkillClaim, claim_id)
    if sc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found.")
    assert_can_verify(db, session, sc.student_id, detail="Claim not found.")
    if sc.status != UploadStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a pending claim can be reviewed."
        )
    decision = body.decision.upper()
    if decision in ("GRANT", "APPROVE", "VERIFY"):
        granted = body.granted_level or sc.claimed_level
        sc.status = UploadStatus.VERIFIED
        # Upsert the verified StudentSkill at the granted level.
        existing = db.scalar(
            select(StudentSkill).where(
                StudentSkill.student_id == sc.student_id,
                StudentSkill.skill_id == sc.skill_id,
            )
        )
        if existing is None:
            db.add(
                StudentSkill(
                    student_id=sc.student_id,
                    skill_id=sc.skill_id,
                    level=granted,
                    verified=True,
                    evidence_upload_id=sc.upload_id,
                )
            )
        else:
            existing.level = granted
            existing.verified = True
            existing.evidence_upload_id = sc.upload_id
    elif decision in ("CHANGES", "NEEDS_CHANGES", "RETURN"):
        # Back to the student to redo, not refused.
        if not (body.note or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Say what needs changing — the student only sees this note.",
            )
        sc.status = UploadStatus.NEEDS_CHANGES
    elif decision in ("REJECT", "REJECTED"):
        # A refusal the student cannot act on is indistinguishable from a bug on
        # their side of the screen, so a reason is required here too.
        if not (body.note or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A rejection needs a reason — the student is shown it.",
            )
        sc.status = UploadStatus.REJECTED
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="decision must be GRANT, CHANGES or REJECT.",
        )
    sc.reviewed_by_id = session["userId"]
    sc.reviewed_at = datetime.now(timezone.utc)
    sc.review_note = body.note
    db.commit()
    db.refresh(sc)
    sname = db.scalar(
        select(User.name).join(Student, Student.user_id == User.id).where(Student.id == sc.student_id)
    )
    skname = db.scalar(select(Skill.name).where(Skill.id == sc.skill_id))
    # upload_id is NULL once the student deleted the certificate. `db.get` with
    # a NULL primary key returns None today but warns it "may raise an error in
    # a future release", so the case is decided here rather than in SQLAlchemy.
    evidence = db.get(Upload, sc.upload_id) if sc.upload_id else None
    return _claim_out(sc, sname or "", skname or "", evidence)
