"""The admin's interview question bank: /api/admin/interview-questions.

    GET    /?track=hr          one track's questions, enabled and not, in order
    POST   /                   add one
    POST   /bulk               add many from pasted lines (or a file's text)
    PATCH  /{id}               edit text / phase / enabled
    DELETE /{id}               remove
    POST   /reorder            the order the interviewer works them in

GATED BY A CAPABILITY, NOT A ROLE - `admin.interview_questions`. The Main Admin
hold it through the baseline; a MENTOR holds it only when granted, which is the
point: the owner asked for "analytics to multiple faculty", and question
authoring is the same shape. PROGRAMME scope, because a question is asked of
every student on the track and no mentor group could narrow it.

WHAT THIS DOES NOT TOUCH. The interviewer stays free-style. app/interview_bank.py
renders these rows into Specialization.question_bank, and build_instructions
already frames that as "a guide to coverage, not a script: rephrase each
naturally, follow up on what the student actually says". The phase machine,
the persona and the word gate are unchanged; an admin decides WHAT is covered,
the model decides HOW it is asked. `position` is load-bearing - the prompt says
"in this order" - which is why reorder is a first-class endpoint and not a PATCH
on a number.

`GET /tracks` AND THE TRACK CRUD LIVE IN `routers/admin_interview_tracks.py`
(B5.1). Two modules cannot both own one path, and the catalogue grew writes of
its own — a persona, a voice Nova has to accept, a rung of the spine — that have
nothing to do with a question. This module keeps the questions and asks that
module which codes exist.

Every write goes through record_change: who added, edited, disabled or removed
a question a student was asked is an audit question, and a bank edited in
silence is one nobody can explain to the student who was asked it.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..db import get_db
from ..governance import ancestry_of_interview_track, require_capability
from ..identity import get_current_session
from ..interview_bank import BANK_PHASES, MAX_QUESTION_CHARS, parse_bulk
from ..interview_tracks import (
    authorable_codes,
    readable_codes,
    writable_track_for_code,
)
from ..models.interview_bank import InterviewBankQuestion
from ..models.interview_track import InterviewTrack

router = APIRouter(prefix="/admin/interview-questions", tags=["interview-bank"])

CAPABILITY = "admin.interview_questions"


# ------------------------------------------------------------- schemas --


class BankQuestionOut(BaseModel):
    id: str
    track: str
    phase: str
    text: str
    position: int
    enabled: bool
    created_at: datetime


class BankQuestionIn(BaseModel):
    track: str
    phase: str
    text: str = Field(min_length=8, max_length=MAX_QUESTION_CHARS)


class BankQuestionPatch(BaseModel):
    phase: str | None = None
    text: str | None = Field(default=None, min_length=8, max_length=MAX_QUESTION_CHARS)
    enabled: bool | None = None


class BulkIn(BaseModel):
    track: str
    #: One question per line. See interview_bank.parse_bulk for the accepted
    #: shapes. 200k characters is ~2 000 generous questions; a file larger than
    #: that is not a question list.
    lines: str = Field(min_length=1, max_length=200_000)


class BulkOut(BaseModel):
    added: list[BankQuestionOut]
    skipped: list[str]


class ReorderIn(BaseModel):
    track: str
    ids: list[str] = Field(min_length=1, max_length=2000)


# ------------------------------------------------------------- helpers --


def _out(q: InterviewBankQuestion) -> BankQuestionOut:
    return BankQuestionOut(
        id=q.id, track=q.track, phase=q.phase, text=q.text,
        position=q.position, enabled=q.enabled, created_at=q.created_at,
    )


def _in_reach(db: Session, session: dict):
    """A WHERE clause narrowing `interview_bank_questions` to this session's reach.

    TWO COLLEGES CAN EACH HAVE AN `hr` TRACK, and the questions on both carry the
    code `hr`. Every query in this module that selects "the questions of a track"
    therefore has to say WHOSE — otherwise a college-scoped holder listing `hr`
    reads the other college's bank, and `reorder`, which renumbers every row it
    finds, REWRITES it.

    Programme-wide questions (`college_id IS NULL`) are included for
    `scope_views.interview_track_scope_clause`'s reason: they are the ones every
    student actually gets asked. Writing one is still refused by `_check_track`.
    """
    from sqlalchemy import false as sa_false
    from sqlalchemy import or_
    from sqlalchemy import true as sa_true

    from ..policies import scope_filter

    reach = scope_filter(db, session, CAPABILITY)
    if reach.everything:
        return sa_true()
    if not reach.colleges:
        return InterviewBankQuestion.college_id.is_(None)
    return or_(
        InterviewBankQuestion.college_id.is_(None),
        InterviewBankQuestion.college_id.in_(reach.colleges),
    )


def _check_readable_track(db: Session, session: dict, track: str) -> str:
    """The track code for a READ. Wider than `_check_track` below, deliberately.

    A college-scoped holder sees the programme-wide tracks on the grid — their
    students sit those interviews — and must be able to open one and read what
    it asks. They simply cannot change it. Validating a read against the WRITE
    set would leave rows on the grid that answer 422 when clicked, which reads
    as a broken screen rather than as a permission.
    """
    key = (track or "").strip().lower()
    codes = readable_codes(db, session)
    if key not in codes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"track must be one of {', '.join(codes)}." if codes else
            "Your 'Interview questions' capability reaches no track.",
        )
    return key


def _check_track(db: Session, session: dict, track: str) -> str:
    """The track code, checked against the CATALOGUE AND THIS SESSION'S REACH.

    It used to read `TRACK_KEYS` — the four keys of
    `interview_matrix.SPECIALIZATIONS` — and B5.1/B5.2 make that wrong in both
    directions. A college that has added its own track could not write a single
    question for it, on the screen whose whole purpose is authoring questions;
    and a college-scoped holder could write against `hr`, which is
    programme-wide, and put a question into every college's HR interview through
    a screen they hold for one college. `authorable_codes` answers both — the
    rows this session may write, plus the shipped constants only when it is
    unscoped.
    """
    key = (track or "").strip().lower()
    codes = authorable_codes(db, session)
    if key not in codes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"track must be one of {', '.join(codes)}."
                if codes
                else "Your 'Interview questions' capability does not reach any "
                "track. An administrator can widen it in Governance, or add a "
                "track for your college."
            ),
        )
    return key


def _track_pointers(
    db: Session, session: dict, code: str
) -> tuple[str | None, str | None]:
    """(track_id, college_id) for a code, or (None, None) for a code that has
    no row yet — B5.2's join, written at the moment the question is created.

    THE COLLEGE COMES FROM THE TRACK, not from the author. A question is asked of
    everyone on its track, so "which students hear this" is the track's question
    and not the author's; `_check_track` above has already refused an author who
    may not write that track at all.

    NOT backfilled on read, and `interview_tracks.bank_for_track` deliberately
    accepts a NULL `track_id`: a question written before this stamp existed must
    keep being asked, and a question that silently vanishes from the bank is
    invisible — the interview still runs, it just stops covering what the office
    said to cover.
    """
    row = writable_track_for_code(db, session, code)
    return (row.id, row.college_id) if row is not None else (None, None)


def _check_phase(phase: str) -> str:
    key = (phase or "").strip().lower()
    if key not in BANK_PHASES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"phase must be one of {', '.join(BANK_PHASES)}.",
        )
    return key


def _next_position(db: Session, track: str) -> int:
    current = db.scalar(
        select(func.max(InterviewBankQuestion.position)).where(InterviewBankQuestion.track == track)
    )
    return int(current or 0) + 1


def _require_question_reach(db: Session, session: dict, q: InterviewBankQuestion) -> None:
    """The B1.2 fence on a question reached BY ID rather than by track.

    `_check_track` narrows the endpoints that name a track in the body; PATCH,
    DELETE and the per-question reads name an ID, and without this a
    college-scoped holder could edit a PROGRAMME-WIDE question — one asked of
    every college — simply by knowing its id. The horizontal-privilege rule that
    `interview_records._session_of_student_or_404` applies to a student's record,
    applied to a catalogue row.

    The reach is the TRACK'S, resolved through `track_id` when the question has
    one and falling back to the question's own `college_id`: a question written
    before B5.2 has no `track_id`, and refusing to let anybody edit it would make
    the whole existing bank read-only on the day this shipped.
    """
    row = db.get(InterviewTrack, q.track_id) if q.track_id else None
    require_capability(
        db,
        session,
        CAPABILITY,
        target=ancestry_of_interview_track(
            db,
            college_id=row.college_id if row is not None else q.college_id,
            course_id=row.course_id if row is not None else None,
            specialization_id=row.specialization_id if row is not None else None,
        ),
    )


def _question_or_404(db: Session, question_id: str) -> InterviewBankQuestion:
    q = db.get(InterviewBankQuestion, question_id)
    if q is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found.")
    return q


def _audit(db, session, request, q: InterviewBankQuestion, action: str, before: dict | None, after: dict | None) -> None:
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="interview_question", entity_id=q.id, action=action,
        before=before, after=after,
        event_type=f"interview_bank.{action.lower()}",
        payload={"track": q.track, "phase": q.phase},
    )


def _snapshot(q: InterviewBankQuestion) -> dict:
    return {"track": q.track, "phase": q.phase, "text": q.text, "position": q.position, "enabled": q.enabled}


# ----------------------------------------------------------- endpoints --


@router.get("", response_model=list[BankQuestionOut])
def list_questions(
    track: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[BankQuestionOut]:
    """Every question on the track, enabled or not, in the order it is worked."""
    require_capability(db, session, CAPABILITY)
    key = _check_readable_track(db, session, track)
    rows = db.scalars(
        select(InterviewBankQuestion)
        .where(InterviewBankQuestion.track == key, _in_reach(db, session))
        .order_by(InterviewBankQuestion.position, InterviewBankQuestion.created_at)
    ).all()
    return [_out(q) for q in rows]


@router.post("", response_model=BankQuestionOut, status_code=status.HTTP_201_CREATED)
def create_question(
    body: BankQuestionIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> BankQuestionOut:
    require_capability(db, session, CAPABILITY)
    track = _check_track(db, session, body.track)
    phase = _check_phase(body.phase)
    track_id, college_id = _track_pointers(db, session, track)
    q = InterviewBankQuestion(
        track=track,
        track_id=track_id,
        college_id=college_id,
        phase=phase,
        text=body.text.strip(),
        position=_next_position(db, track),
        enabled=True,
        created_by_user_id=session.get("userId"),
    )
    db.add(q)
    db.flush()
    _audit(db, session, request, q, "CREATED", None, _snapshot(q))
    db.commit()
    db.refresh(q)
    return _out(q)


@router.post("/bulk", response_model=BulkOut, status_code=status.HTTP_201_CREATED)
def bulk_create(
    body: BulkIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> BulkOut:
    """Many at once, from pasted lines or a file's text. Lines that cannot be
    read are reported by number and NOT stored: an author fixes the three that
    failed rather than re-checking the fifty that did not. Appended in order
    after whatever the track already holds."""
    require_capability(db, session, CAPABILITY)
    track = _check_track(db, session, body.track)
    rows, skipped = parse_bulk(body.lines)
    track_id, college_id = _track_pointers(db, session, track)
    position = _next_position(db, track)
    added: list[InterviewBankQuestion] = []
    for phase, text in rows:
        q = InterviewBankQuestion(
            track=track, track_id=track_id, college_id=college_id,
            phase=phase, text=text, position=position, enabled=True,
            created_by_user_id=session.get("userId"),
        )
        db.add(q)
        added.append(q)
        position += 1
    db.flush()
    for q in added:
        _audit(db, session, request, q, "CREATED", None, {**_snapshot(q), "via": "bulk"})
    db.commit()
    for q in added:
        db.refresh(q)
    return BulkOut(added=[_out(q) for q in added], skipped=skipped)


@router.patch("/{question_id}", response_model=BankQuestionOut)
def patch_question(
    question_id: str,
    body: BankQuestionPatch,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> BankQuestionOut:
    require_capability(db, session, CAPABILITY)
    q = _question_or_404(db, question_id)
    _require_question_reach(db, session, q)
    before = _snapshot(q)
    if body.phase is not None:
        q.phase = _check_phase(body.phase)
    if body.text is not None:
        q.text = body.text.strip()
    if body.enabled is not None:
        q.enabled = body.enabled
    _audit(db, session, request, q, "UPDATED", before, _snapshot(q))
    db.commit()
    db.refresh(q)
    return _out(q)


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    question_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    require_capability(db, session, CAPABILITY)
    q = _question_or_404(db, question_id)
    _require_question_reach(db, session, q)
    _audit(db, session, request, q, "DELETED", _snapshot(q), None)
    db.delete(q)
    db.commit()


@router.post("/reorder", response_model=list[BankQuestionOut])
def reorder(
    body: ReorderIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[BankQuestionOut]:
    """The complete order for one track. Every id must belong to the track and
    every question on the track must be named: a partial order is a
    question silently moved to the end, which nobody asked for."""
    require_capability(db, session, CAPABILITY)
    track = _check_track(db, session, body.track)
    rows = db.scalars(
        select(InterviewBankQuestion).where(
            InterviewBankQuestion.track == track, _in_reach(db, session)
        )
    ).all()
    by_id = {q.id: q for q in rows}
    if set(body.ids) != set(by_id) or len(body.ids) != len(set(body.ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="ids must name every question on the track exactly once.",
        )
    for pos, qid in enumerate(body.ids, start=1):
        by_id[qid].position = pos
    db.commit()
    ordered = [by_id[qid] for qid in body.ids]
    for q in ordered:
        db.refresh(q)
    return [_out(q) for q in ordered]
