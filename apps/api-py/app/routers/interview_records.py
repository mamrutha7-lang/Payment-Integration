"""Interview records — the READ side, plus the consent grant (Interview Engine
v3 §7).

Two routers, one module:

  student_router  /api/interview/sessions[...]   the student's OWN interviews
                  /api/interview/consent          the caller's OWN grant
  staff_router    /api/mentor/students/{student_id}/interviews[...]

They are declared here with their FULL paths and both are included from
app/main.py with **no extra prefix**::

    app.include_router(interview_records.student_router)
    app.include_router(interview_records.staff_router)

That is the same shape app/routers/interview.py uses (`prefix="/api/interview"`)
and NOT the shape the domain routers use (`prefix="/mentor"` + `include_router(...,
prefix="/api")`). Mounting `staff_router` with `prefix="/api"` would silently
serve it at `/api/api/mentor/...`: every request 404s, nothing raises, and the
only symptom is a mentor screen that is permanently empty.

WHY A SECOND MODULE AND NOT THREE LINES IN mentor.py. `app/routers/mentor.py`
was being edited by another track while this landed, and a staff endpoint added
there would have collided with it. Declaring a second `/api/mentor` router costs
nothing — FastAPI merges routers by path, not by module — and it means the file
that owns AGENTS.md rule 2 is not touched by this feature at all.

RULE 2 IS THE POINT OF THIS MODULE, AND IT IS NOT RE-IMPLEMENTED HERE.
`_assert_can_access_student` is imported from `app/routers/mentor.py` — the one
implementation of "a MENTOR only for a student in their own group" — and called
as the FIRST LINE of every staff endpoint. app/routers/leave.py already
establishes both the precedent and the reasoning: a second copy here would be
the copy that stops tracking the first. A MENTOR with NO `Mentor` group lands in
that function's own 404 branch and sees NOBODY; that is the behaviour, and
nothing here adds to it, softens it, or works around it.

The gate takes the **path's** `student_id`. It does not, and cannot, say
anything about a session id, so every by-id staff endpoint does a SECOND check:
the row's own `student_id` must equal the path's. Gating on the path and then
loading a row by an id from somewhere else is a horizontal-privilege bug wearing
a correct-looking first line (§7.3).

The student endpoints take NO `student_id` — the subject comes from the session
cookie, the same way `POST /api/agent/ask` and `POST /api/voice/token` take
theirs. Adding one "for symmetry" would turn every student endpoint into an IDOR
the moment someone forgets a `.where()`.

404, NEVER 403, for a record that belongs to somebody else. `conversations.
assert_owner` already sets that rule: a 403 confirms that the id exists, which is
exactly what an enumeration wants to learn.

RULE 1 is not in play here. Nothing in this module talks to a model; it reads
rows that the relay already wrote.
"""

import base64
import binascii
import hashlib
import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy import func, select, tuple_, update
from sqlalchemy import false as sa_false
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..config import settings
from ..db import get_db
from ..exports import (
    carries_personal_columns,
    csv_response,
    drop_personal,
    record_export,
    scope_note,
)
from ..identity import get_current_session
from ..document_store import content_disposition
from ..governance import require_capability
from ..policies import Reach, scope_filter
from ..scope_views import scope_header
# B6.1: the college's policy decides the two storage scopes the acknowledgement
# records. `default_policy` is aliased at the import so the call site below reads
# as what it is — the answer for an account with no student row and therefore no
# college — rather than as a bare `default_policy()` that could be anything's.
from ..interview_policy import default_policy as interview_policy_defaults
from ..interview_policy import policy_for_student
from ..interview_audio import (
    TRACK_MIXED,
    TRACKS,
    AudioStoreError,
    download_name,
    read_track,
)
from ..models.interview import (
    InterviewConsent,
    InterviewEvaluation,
    InterviewScoreSummary,
    InterviewSession,
    InterviewTurn,
)
from ..models.redesign import AuditEvent
from ..models.user import Role, Student, User

# _assert_can_access_student is private to mentor.py on purpose, and importing it
# anyway is the lesser evil — the same call app/routers/leave.py makes, for the
# same reason. require_admin rides along because §7.2 needs BOTH gates on the
# report: require_admin says WHICH ROLE may see `raw_response`,
# _assert_can_access_student says WHICH STUDENT may be read, and neither answers
# the other's question.
from .mentor import _assert_can_access_student, require_admin, require_mentor

log = logging.getLogger(__name__)

student_router = APIRouter(prefix="/api/interview", tags=["interview-records"])
staff_router = APIRouter(prefix="/api/mentor", tags=["interview-records"])
# B6.7 — the records GRID, its KPIs and its extract. A third router in this
# module rather than a fourth file, because the grid and `all_interviews` below
# must ask ONE query builder who a caller may see: two grids over the same rows
# with two copies of rule 2 is two answers, and the day they disagree the
# narrower one looks like a bug and the wider one is a leak.
#
# `app/main.py` discovers every public APIRouter here and mounts it, so this
# needs no wiring — see the comment above that loop, and note the `/api/`
# prefix requirement it enforces.
admin_router = APIRouter(prefix="/api/admin", tags=["interview-records"])

# Newest N interviews, and no pagination. A student sits perhaps a dozen mock
# interviews in a placement season and the 180-day retention window reaps the
# rest, so this is a runaway guard rather than a page size: without it one
# scripted account could make the mentor screen fetch every row it ever wrote.
# If a real cohort ever approaches it, that is the moment to add a cursor, not
# to raise the number.
_MAX_SESSIONS_LISTED = 200

# What a `user-agent` header is worth keeping on a consent row: enough to say
# "Chrome on Android", never a header of unbounded length written by the client.
_MAX_USER_AGENT_CHARS = 300


# ---------------------------------------------------------------------------
# Payloads
#
# §5.4 DECIDED THAT STAFF SEE ONE FIELD MORE THAN THE STUDENT, AND THE SPLIT IS
# EXPRESSED AS TWO MODELS ON PURPOSE. The next editor will want to merge them
# into one payload with `raw_response: str | None` and fill it conditionally.
# Do not: a student would then receive `"raw_response": null`, which reads as
# "the model returned nothing" when it actually means "this is not yours to
# read". The KEY'S ABSENCE is the honest way to say the second thing, and it is
# only absent if there are two models.
#
# Everything else the student sees is everything staff see — including every
# score. That is also a decision and not an oversight (§5.4): the model already
# SPEAKS its verdict aloud at wrap-up, so hiding the number hides only the
# number; and a score the student cannot see while their mentor can is a secret
# file on a student, which is the one arrangement here that would be genuinely
# hard to defend. The honest fix is calibration copy next to the number ("a
# practice score generated by an AI from one 15-minute session; not a placement
# decision"), which the client renders — never concealment.
# ---------------------------------------------------------------------------


class InterviewSessionOut(BaseModel):
    """One interview, as a history row. Same shape for both audiences."""

    id: str
    specialization: str | None
    status: str
    terminal_reason: str | None
    final_phase: str | None
    answers_accepted: int
    close_code: int | None
    # READ THIS, never `audio_path`. The path is a server-side location and is
    # not exposed to anyone; the flag is the fact. app/models/interview.py sets
    # out why a NULL path cannot answer "was this recorded" — capture disabled,
    # consent refused, the write failed and "predates capture entirely" are four
    # different facts and a NULL flattens them into one.
    audio_recorded: bool
    # Why `audio_recorded` is false, in `app/interview_audio.py`'s SKIP_* words,
    # or null: on a recorded interview, and on every row written before the
    # column existed. The screen turns a word into the switch to flip; a null
    # renders as "no recording" and nothing more, because guessing which gate
    # closed a year ago would be inventing a fact.
    audio_skipped_reason: str | None = None
    # AGENTS.md's runbook pair, served rather than left in the database
    # (2026-09-17): a transcript with five student turns and no interviewer
    # turns is either an interviewer that never spoke or a write path that
    # dropped its turns, and only these two numbers tell the office which
    # without a database client. Emitted counts every turn the engine saw;
    # persisted counts the rows that landed. Zero on both for a row that
    # predates the counters.
    turns_emitted: int = 0
    turns_persisted: int = 0
    started_at: datetime
    ended_at: datetime | None
    # From the evaluation row, via one LEFT JOIN, so a history list does not
    # fire a query per interview. NULL report_status means no evaluation row
    # exists at all — the interview is still running, or it predates the
    # scorecard. That is distinct from `report_status='unavailable'`, which is
    # the record that a report WAS attempted and did not arrive.
    report_status: str | None
    overall_score: int | None


class InterviewTurnOut(BaseModel):
    """One utterance. `content` may legitimately be an empty string — that is a
    transcription that failed or timed out, and `transcription_status` says
    which (L4). A client that renders blank turns as "nothing was said" is
    wrong; they are "we could not hear it"."""

    seq: int
    speaker: str
    phase: str
    content: str
    transcription_status: str
    answer_quality: str | None
    counted_as_answer: bool
    is_partial: bool
    created_at: datetime


class InterviewReportOut(BaseModel):
    """The scorecard as the student sees it. No `raw_response` — see above."""

    report_status: str
    # NULLABLE EVEN WHEN report_status == 'ok', and the client must be able to
    # render a blank. A missing score and a zero mean opposite things to the
    # person reading this screen, and neither the model nor this endpoint may
    # invent one to fill the gap.
    overall_score: int | None
    communication_score: int | None
    domain_score: int | None
    structure_score: int | None
    strengths: list | None
    improvements: list | None
    drill: str | None
    summary: str | None
    model: str | None
    generated_at: datetime


class StaffInterviewReportOut(InterviewReportOut):
    """The Main Admin only, and the one field is the whole reason this subclass
    exists.

    `raw_response` is the model's exact output, kept for debugging a bad parse.
    It routinely contains the model's private reasoning ABOUT the student, which
    is why it does not travel to the student and why a MENTOR does not get it
    either — `require_admin` decides, on top of the group gate that already
    decided which student may be read at all.
    """

    raw_response: str | None


class ConsentOut(BaseModel):
    """A live consent grant. Deliberately omits `user_agent` and
    `source_ip_hash`: those are audit fields for a disputed grant, not something
    the granting browser needs handed back to it."""

    id: str
    version: str
    scope_live_ai: bool
    scope_store_transcript: bool
    scope_store_audio: bool
    granted_at: datetime


class ConsentStateOut(BaseModel):
    """The grant AND the version the server is currently asking for.

    The version is not decoration: `POST /api/interview/consent` refuses a
    version it does not know, so the client has to learn the current string from
    somewhere, and this is the endpoint it already calls to decide whether to
    show the panel. Without it a cached SPA could only guess, and a guess that
    is wrong looks to the student like a consent button that does nothing.
    """

    version: str
    consent: ConsentOut | None
    # WHO receives the student's voice, in words, for the disclosure the panel
    # shows before they agree. Server-side because the client cannot know which
    # engine is running — and the sentence it used to hard-code named OpenAI,
    # which becomes a false disclosure the moment a deployment switches engines.
    provider: str


class ConsentIn(BaseModel):
    """An ACKNOWLEDGEMENT of the college's policy, not a form (B6.1).

    THE THREE SCOPE FIELDS ARE GONE FROM THIS PAYLOAD AND THE ROW STILL CARRIES
    THREE. They used to be required, "because a grant states each scope
    explicitly or it is not a grant" — and that is still true of the ROW. What
    changed is WHO STATES THEM: B6.1 makes the two storage scopes the college's
    decision (`interview_policies`), so the server reads them off the policy and
    writes them here. AGENTS.md's "three separate booleans, because one boolean
    makes 'they consented' unfalsifiable" survives exactly: three booleans are
    what gets copied, `interview_sessions.consent_id` still pins the precise row
    an interview ran under, and "was this student consented, to what wording, at
    the time of interview X" is still answerable years later.

    A CLIENT THAT STILL SENDS THEM IS NOT REFUSED, and it is not obeyed either.
    Pydantic ignores unknown fields by default, so a bundle cached from before
    this release posts its three booleans and gets the policy's — which is the
    only safe reading, because honouring a stale client's `scope_store_audio:
    true` over a college that has since turned recording off would record a
    student the college said not to record.

    The version string keeps its one job: a stale cached SPA must not be able to
    acknowledge copy the student never saw, so the server 422s anything but the
    current string rather than back-dating the terms.
    """

    version: str = Field(min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# Shared loaders — used by BOTH audiences, so the 404 semantics cannot drift
# ---------------------------------------------------------------------------


def _own_student_id(session: dict) -> str | None:
    """The caller's own `Student` id. 403 for anyone who is not a STUDENT.

    None means a STUDENT session carrying no `studentId` claim — a user row with
    no `Student` row behind it. Such a caller cannot own an interview at all
    (`interview_sessions.student_id` is NOT NULL), so the callers below read
    None as "no interviews", never as "no filter". A missing subject that turns
    into an unfiltered query is precisely the IDOR this module's header warns
    about.
    """
    if session.get("role") != Role.STUDENT.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Interview records of your own are a student feature.",
        )
    return session.get("studentId")


def _sessions_for_student(db: Session, student_id: str) -> list[InterviewSessionOut]:
    """Newest first, with the scorecard summary joined in.

    Soft-deleted rows are excluded for every audience. `retention.purge_expired`
    stamps `deleted_at` AND redacts the turn text and `raw_response` in the same
    step, so a soft-deleted interview is a hollowed-out record on its way to a
    hard delete — showing it would put an empty transcript in front of a mentor
    with nothing to say why. `conversations.assert_owner` treats a soft-deleted
    conversation the same way.
    """
    rows = db.execute(
        select(
            InterviewSession,
            InterviewEvaluation.report_status,
            InterviewEvaluation.overall_score,
        )
        .outerjoin(
            InterviewEvaluation,
            InterviewEvaluation.interview_session_id == InterviewSession.id,
        )
        .where(
            InterviewSession.student_id == student_id,
            InterviewSession.deleted_at.is_(None),
        )
        .order_by(InterviewSession.started_at.desc(), InterviewSession.id)
        .limit(_MAX_SESSIONS_LISTED)
    ).all()
    return [
        _session_out(row, report_status=report_status, overall_score=overall_score)
        for row, report_status, overall_score in rows
    ]


def _session_of_student_or_404(
    db: Session, session_id: str, student_id: str
) -> InterviewSession:
    """The interview `session_id`, ONLY if it belongs to `student_id`.

    This is §7.3's second check and it is not optional on the staff side:
    `_assert_can_access_student` was handed the PATH's student id and can say
    nothing whatsoever about a session id. Loading by session id alone, after a
    gate that passed on a different argument, is how a mentor reads another
    group's interview through a correct-looking first line.

    404 for missing, another student's, and soft-deleted alike — the caller
    cannot tell which, so no id is confirmed to exist.
    """
    row = db.get(InterviewSession, session_id)
    if row is None or row.student_id != student_id or row.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found."
        )
    return row


def _session_out(
    row: InterviewSession,
    *,
    report_status: str | None = None,
    overall_score: int | None = None,
) -> InterviewSessionOut:
    return InterviewSessionOut(
        id=row.id,
        specialization=row.specialization,
        status=row.status,
        terminal_reason=row.terminal_reason,
        final_phase=row.final_phase,
        answers_accepted=row.answers_accepted,
        close_code=row.close_code,
        audio_recorded=row.audio_recorded,
        audio_skipped_reason=row.audio_skipped_reason,
        turns_emitted=row.turns_emitted or 0,
        turns_persisted=row.turns_persisted or 0,
        started_at=row.started_at,
        ended_at=row.ended_at,
        report_status=report_status,
        overall_score=overall_score,
    )


def _session_detail(db: Session, row: InterviewSession) -> InterviewSessionOut:
    """One session with its scorecard summary — the by-id twin of the LEFT JOIN
    in `_sessions_for_student`, so the two endpoints can never disagree about
    whether an interview has a report."""
    evaluation = db.scalar(
        select(InterviewEvaluation).where(
            InterviewEvaluation.interview_session_id == row.id
        )
    )
    return _session_out(
        row,
        report_status=None if evaluation is None else evaluation.report_status,
        overall_score=None if evaluation is None else evaluation.overall_score,
    )


def _transcript(db: Session, interview_session_id: str) -> list[InterviewTurnOut]:
    """The turns, in interview order.

    ORDER BY (seq, created_at, id) rather than seq alone, and the tie-breakers
    are load-bearing: `(interview_session_id, seq)` is deliberately NOT unique
    (§6.3 — a fire-and-forget writer must never raise on a cosmetic duplicate),
    so two rows can share a seq, and Postgres is free to return an unordered tie
    in either order. Without a TOTAL order the same transcript would come back
    shuffled between two requests, which reads as the record changing itself.
    """
    rows = db.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.interview_session_id == interview_session_id)
        .order_by(InterviewTurn.seq, InterviewTurn.created_at, InterviewTurn.id)
    ).all()
    return [
        InterviewTurnOut(
            seq=t.seq,
            speaker=t.speaker,
            phase=t.phase,
            content=t.content,
            transcription_status=t.transcription_status,
            answer_quality=t.answer_quality,
            counted_as_answer=t.counted_as_answer,
            is_partial=t.is_partial,
            created_at=t.created_at,
        )
        for t in rows
    ]


def _evaluation_or_404(db: Session, interview_session_id: str) -> InterviewEvaluation:
    """The scorecard row, or 404.

    A row is written on EVERY terminal path, including one that says
    `report_status='unavailable'` — so a missing row does not mean "the report
    failed", it means the interview has not finished yet (or predates the
    scorecard entirely). Those are different sentences for the student and this
    404 is what lets the client say the right one.
    """
    evaluation = db.scalar(
        select(InterviewEvaluation).where(
            InterviewEvaluation.interview_session_id == interview_session_id
        )
    )
    if evaluation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report for this interview yet.",
        )
    return evaluation


def _report_out(evaluation: InterviewEvaluation) -> InterviewReportOut:
    return InterviewReportOut(
        report_status=evaluation.report_status,
        overall_score=evaluation.overall_score,
        communication_score=evaluation.communication_score,
        domain_score=evaluation.domain_score,
        structure_score=evaluation.structure_score,
        strengths=evaluation.strengths,
        improvements=evaluation.improvements,
        drill=evaluation.drill,
        summary=evaluation.summary,
        model=evaluation.model,
        generated_at=evaluation.generated_at,
    )


def _may_see_raw_response(session: dict) -> bool:
    """The Main Admin, decided by mentor.py's own `require_admin`.

    It raises 403 and we need a boolean, so it is called and caught rather than
    re-expressed as `role in {...}` here. That looks roundabout and is
    deliberate: a second copy of the role list is a second thing to remember
    when a role is added, and this endpoint must not be the one that keeps
    handing a new role the model's private reasoning about a student.
    """
    try:
        require_admin(session)
    except HTTPException:
        return False
    return True


# ---------------------------------------------------------------------------
# B6.5 — the access log: who opened a student's interview record
#
# NO NEW TABLE. Every one of these reads is already an event with an actor, a
# subject, a time and a route, which is exactly `redesign_audit_events`, and
# that table already has the index this needs
# (`ix_redesign_audit_entity_time (entity_type, entity_id, occurred_at)`). A
# second table would be a second answer to "who has looked at this student",
# and the trail the office reads at `GET /api/admin/audit` would be missing the
# half that matters most.
#
# WHAT IS LOGGED, AND WHAT DELIBERATELY IS NOT. A row is written when a member
# of staff opens ONE named student's record BY ID: the record panel, the
# transcript, the report, a recording, and one row per session inside a bulk
# zip. A row is NOT written when staff open a LIST — the per-student history,
# the records grid — and that is a decision, not an oversight (the map's
# decision 7): one mentor opening the grid would otherwise write two hundred
# rows, and the student's panel would have to tell them "a mentor saw your name
# in a list", which is both untrue in spirit and impossible to act on. The
# panel says "opened your record", and every row behind it is somebody who did.
#
# IT IS WRITTEN AFTER THE GATES, ALWAYS. A probe for another group's session id
# must 404 without leaving a row claiming that record was viewed — the log would
# then be evidence of an access that did not happen, on a screen the student
# reads. Every call site below sits after `_assert_can_access_student` AND after
# `_session_of_student_or_404`.
#
# THE ADMIN SIDE NEEDS NO ENDPOINT OF ITS OWN. 04-backend-changes.md asks for
# the same list "on the admin record panel too", and it is already there:
# `GET /api/admin/audit?target_type=interview_record&target_id=<session id>` is
# the trail read through the console, with its own capability, its own paging
# and its own CSV. A second reader over the same rows would be a second place
# to keep the vocabulary correct, for one panel.
# ---------------------------------------------------------------------------

#: The `entity_type` and `action` the trail is filtered by. Plain strings in one
#: place rather than an enum, the same rule every interview vocabulary follows.
VIEW_ENTITY_TYPE = "interview_record"
VIEW_ACTION = "INTERVIEW_RECORD_VIEW"

#: The four things a staff member can open. `record` is the detail panel itself;
#: the other three are the artefacts. A fifth value is a data change here and a
#: label change on the student's panel — never a migration.
VIEW_RECORD = "record"
VIEW_TRANSCRIPT = "transcript"
VIEW_REPORT = "report"
VIEW_AUDIO = "audio"

#: How many entries the student's own panel lists. Newest first; a student with
#: more than this has a mentor with a habit, and the top of the list says so.
_MAX_VIEWS_LISTED = 200


def _log_record_view(
    db: Session,
    *,
    session: dict,
    request: Request,
    row: InterviewSession,
    what: str,
) -> None:
    """Record that this staff account opened this interview record.

    IT COMMITS, because `get_db` never does and these are GET handlers whose
    transaction is rolled back on the way out — which is how an audited read
    becomes an unaudited one with nobody editing the audit line. `record_export`
    met the same problem and says the same thing.

    IT NEVER FAILS THE READ, which is the opposite of `record_export`'s rule and
    is deliberate. An export is a file that cannot be recalled, so a download
    with no receipt is worse than no download. This is a mentor opening a
    transcript on a screen: a failed log line is a gap in a record the student
    reads, and a 500 in a mentor's face is a gap in the work. The gap is logged
    at WARNING so it is diagnosable, and the read continues.
    """
    try:
        record_change(
            db,
            session=session,
            request=request,
            tenant_id=None,
            entity_type=VIEW_ENTITY_TYPE,
            entity_id=row.id,
            action=VIEW_ACTION,
            before=None,
            # The subject is on the row so the trail can answer "who looked at
            # this STUDENT" without joining back to a session that retention may
            # since have deleted.
            after={"what": what, "student_id": row.student_id},
            event_type="interview.record.view",
            payload={"what": what, "session_id": row.id, "student_id": row.student_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        log.warning(
            "Could not record the %s view of interview %s by user %s. The read "
            "itself succeeded; this is a missing line in the access log the "
            "student can read.",
            what,
            row.id,
            session.get("userId"),
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Student endpoints — own record only, subject taken from the session cookie
# ---------------------------------------------------------------------------


@student_router.get("/sessions", response_model=list[InterviewSessionOut])
def my_interviews(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[InterviewSessionOut]:
    """The caller's own interviews, newest first."""
    student_id = _own_student_id(session)
    if student_id is None:
        return []
    return _sessions_for_student(db, student_id)


@student_router.get("/sessions/{session_id}", response_model=InterviewSessionOut)
def my_interview(
    session_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> InterviewSessionOut:
    student_id = _own_student_id(session)
    if student_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found."
        )
    row = _session_of_student_or_404(db, session_id, student_id)
    return _session_detail(db, row)


@student_router.get(
    "/sessions/{session_id}/transcript", response_model=list[InterviewTurnOut]
)
def my_interview_transcript(
    session_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[InterviewTurnOut]:
    student_id = _own_student_id(session)
    if student_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found."
        )
    row = _session_of_student_or_404(db, session_id, student_id)
    return _transcript(db, row.id)


@student_router.get("/sessions/{session_id}/report", response_model=InterviewReportOut)
def my_interview_report(
    session_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> InterviewReportOut:
    """The student's own scorecard — every score, and no `raw_response`.

    `InterviewReportOut` is the model without that field, so it cannot leak by
    someone later populating it here; see the note above the payload classes for
    why this is two models rather than one with a conditional field.
    """
    student_id = _own_student_id(session)
    if student_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found."
        )
    row = _session_of_student_or_404(db, session_id, student_id)
    return _report_out(_evaluation_or_404(db, row.id))


class RecordViewOut(BaseModel):
    """One staff account opening one interview record (B6.5).

    `viewer_name` is NULLABLE and null means the account has since been deleted
    — `redesign_audit_events.actor_user_id` is `ON DELETE SET NULL`, and the
    trail keeps the event. The client renders that as "a member of staff", never
    as a blank: a row with no name still says somebody looked, which is the
    whole question this screen answers.
    """

    viewer_name: str | None
    what: str
    at: datetime


@student_router.get(
    "/sessions/{session_id}/views", response_model=list[RecordViewOut]
)
def my_interview_views(
    session_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[RecordViewOut]:
    """Who has opened this interview of mine, and what they opened (B6.5).

    THE STUDENT'S OWN, WITH NO `student_id` IN THE PATH — the subject comes from
    the cookie, like every other route on this router, and
    `_session_of_student_or_404` is the second check that this session really is
    theirs. An interview that is not theirs 404s here exactly as it does
    everywhere else in this module, so this endpoint cannot be used to learn
    that a session id exists.

    IT LISTS STAFF READS, NOT THE STUDENT'S OWN. Nothing on the student routes
    writes a view row: a screen that told a student "you opened your own report
    on Tuesday" would bury the one line they are looking for. So an empty list
    means nobody on staff has opened it, which is a real and common answer and
    must render as a sentence rather than as an empty table.

    WHAT IT CANNOT SHOW. A read that happened before this shipped left no row,
    and the list cannot say so; and a record hard-deleted by retention takes
    this endpoint's 404 with it, at which point the audit trail still holds the
    rows but there is no longer a record for the student to ask about.
    """
    student_id = _own_student_id(session)
    if student_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found."
        )
    row = _session_of_student_or_404(db, session_id, student_id)
    rows = db.execute(
        select(AuditEvent.after_json, AuditEvent.occurred_at, User.name)
        .outerjoin(User, AuditEvent.actor_user_id == User.id)
        .where(
            AuditEvent.entity_type == VIEW_ENTITY_TYPE,
            AuditEvent.entity_id == row.id,
            AuditEvent.action == VIEW_ACTION,
        )
        .order_by(AuditEvent.occurred_at.desc())
        .limit(_MAX_VIEWS_LISTED)
    ).all()
    return [
        RecordViewOut(
            viewer_name=name,
            # `.get` rather than `["what"]`: `after_json` is a JSON column and a
            # row written by an older build (or by hand) is data, not a promise.
            # A 500 on a student's own privacy screen because one historical row
            # is shaped differently would be the worst possible failure here.
            what=str((after or {}).get("what") or VIEW_RECORD),
            at=occurred_at,
        )
        for after, occurred_at, name in rows
    ]


# ---------------------------------------------------------------------------
# B6.2 — the progress trend, which is the part of an interview that OUTLIVES it
#
# `interview_score_summaries` is written at finalization and survives the
# 180-day purge that takes the transcript and the scorecard. So this endpoint
# reads the summaries and NEVER `interview_evaluations`: reading the evaluations
# would give an identical answer in every test and a trend that silently loses
# its oldest points every night in production, which is the exact failure B6.2
# was added to prevent.
#
# ONE COMPOSER, TWO AUDIENCES. `compose_interview_progress` is what the student
# reads and what a mentor reads, for `routers/mentee_records.py`'s stated
# reason: a staff screen must show the STUDENT'S OWN numbers or a mentor ends up
# acting on a confident figure the student has never seen.
# ---------------------------------------------------------------------------


class InterviewProgressPointOut(BaseModel):
    """One interview on the trend line.

    EVERY SCORE IS NULLABLE AND A NULL IS A GAP IN THE LINE, never a zero at the
    origin. An interview that was abandoned in the first minute has no scores
    and is still a point — "three attempts abandoned" is the fact a mentor most
    needs — so the client plots the scored ones and renders the rest as a dash.
    """

    session_id: str | None
    track_code: str | None
    started_at: datetime
    status: str
    overall_score: int | None
    communication_score: int | None
    domain_score: int | None
    structure_score: int | None
    #: Is the interview itself still on file — i.e. can this point be opened?
    #: False means retention has reaped the transcript and the scorecard and
    #: only these numbers remain, which is the expected end state of every row
    #: here rather than a fault. The client links the point only when it is
    #: True; a dead link to a purged record reads as data loss.
    record_available: bool


class InterviewProgressOut(BaseModel):
    """The trend and its headline numbers, computed here rather than on screen.

    THE AGGREGATES ARE THE SERVER'S because averaging over NULLs is exactly the
    arithmetic a client gets wrong: `sum/length` across a list where three of
    seven interviews were never scored reports a number about four interviews as
    though it were about seven, and it reads as a student who is getting worse.
    Every aggregate here is taken over the SCORED points only, and every one of
    them is None when there are none.
    """

    points: list[InterviewProgressPointOut]
    #: Every interview on file, including the abandoned ones.
    attempts: int
    completed: int
    #: How many of the points carry an overall score — the denominator of
    #: `average_overall`, published so a screen can say "averaged over 4 of 7".
    scored: int
    best_overall: int | None
    first_overall: int | None
    latest_overall: int | None
    average_overall: float | None
    #: latest − first, and None unless there are TWO scored interviews. A
    #: "+0" drawn from one attempt says the student has not improved, which is
    #: the same false sentence a 0 score would be; `compose_growth` in the badge
    #: dashboard already refuses to claim growth from a baseline alone.
    trend: int | None


def compose_interview_progress(db: Session, student_id: str) -> InterviewProgressOut:
    """The student's own trend. Oldest first, because a trend reads left to right.

    Rule-based, no model, so rule 1's egress gate does not apply. Rule 2's gate
    runs at the staff call site, before this is reached — the same split
    `compose_placement_readiness` and `compose_ledger` already make.
    """
    rows = db.execute(
        select(InterviewScoreSummary, InterviewSession.id)
        # LEFT JOIN, and excluding the soft-deleted: a summary whose session is
        # NULL (reaped) and one whose session is on its way out must both come
        # back with `record_available=False`, because in both cases there is
        # nothing left for the student to open.
        .outerjoin(
            InterviewSession,
            (InterviewSession.id == InterviewScoreSummary.session_id)
            & (InterviewSession.deleted_at.is_(None)),
        )
        .where(InterviewScoreSummary.student_id == student_id)
        .order_by(InterviewScoreSummary.started_at, InterviewScoreSummary.id)
        .limit(_MAX_SESSIONS_LISTED)
    ).all()

    points = [
        InterviewProgressPointOut(
            session_id=summary.session_id,
            track_code=summary.track_code,
            started_at=summary.started_at,
            status=summary.status,
            overall_score=summary.overall_score,
            communication_score=summary.communication_score,
            domain_score=summary.domain_score,
            structure_score=summary.structure_score,
            record_available=live_id is not None,
        )
        for summary, live_id in rows
    ]
    scored = [p.overall_score for p in points if p.overall_score is not None]
    return InterviewProgressOut(
        points=points,
        attempts=len(points),
        completed=sum(1 for p in points if p.status == "completed"),
        scored=len(scored),
        best_overall=max(scored) if scored else None,
        first_overall=scored[0] if scored else None,
        latest_overall=scored[-1] if scored else None,
        # One decimal, like every other average in this codebase, and None —
        # never 0.0 — when nothing has been scored.
        average_overall=round(sum(scored) / len(scored), 1) if scored else None,
        trend=(scored[-1] - scored[0]) if len(scored) >= 2 else None,
    )


@student_router.get("/progress", response_model=InterviewProgressOut)
def my_interview_progress(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> InterviewProgressOut:
    """My mock-interview trend (B6.2/B17).

    A student with no interviews gets a 200 with an empty `points` and every
    aggregate null. That is the honest answer and it is NOT a 404: "you have not
    practised yet" is a state the screen renders, and a 404 would make the client
    guess between that and a broken endpoint.
    """
    student_id = _own_student_id(session)
    if student_id is None:
        return InterviewProgressOut(
            points=[],
            attempts=0,
            completed=0,
            scored=0,
            best_overall=None,
            first_overall=None,
            latest_overall=None,
            average_overall=None,
            trend=None,
        )
    return compose_interview_progress(db, student_id)


# ---------------------------------------------------------------------------
# Consent — a row, because consent that lives in localStorage is a cache
# ---------------------------------------------------------------------------


def _live_consent(db: Session, user_id: str, version: str) -> InterviewConsent | None:
    return db.scalar(
        select(InterviewConsent).where(
            InterviewConsent.user_id == user_id,
            InterviewConsent.version == version,
            InterviewConsent.revoked_at.is_(None),
        )
    )


def _consent_out(row: InterviewConsent) -> ConsentOut:
    return ConsentOut(
        id=row.id,
        version=row.version,
        scope_live_ai=row.scope_live_ai,
        scope_store_transcript=row.scope_store_transcript,
        scope_store_audio=row.scope_store_audio,
        granted_at=row.granted_at,
    )


def _user_agent(request: Request) -> str | None:
    """The caller's User-Agent, truncated. Client-supplied text of unbounded
    length goes on a row only after someone decides how much of it is worth
    keeping; "Chrome on Android" is the whole question this field answers."""
    raw = (request.headers.get("user-agent") or "").strip()
    return raw[:_MAX_USER_AGENT_CHARS] or None


def _ip_hash(request: Request) -> str | None:
    """A salted digest of the caller's address — NEVER the address.

    Enough to show that two disputed grants came from the same machine; not
    enough to become a location log on a student, which is what storing the
    address itself would be. Salted with AUTH_SECRET because an unsalted SHA-256
    of an IPv4 address is a lookup table, not a hash — the college sits behind a
    handful of /24s and every one of them could be enumerated in seconds.

    Behind a proxy this is the proxy's address, and that is left alone
    deliberately: `X-Forwarded-For` is client-supplied unless every hop is
    trusted, and a forgeable audit field is worse than a coarse one.
    """
    client = request.client
    if client is None or not client.host:
        return None
    digest = hmac.new(
        settings.auth_secret.encode("utf-8"),
        client.host.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    # 128 bits is far past collision-proof for "same machine or not", and a
    # shorter column is a smaller thing to leak in a database dump.
    return digest[:32]


@student_router.get("/consent", response_model=ConsentStateOut)
def my_consent(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> ConsentStateOut:
    """The caller's own live grant for the CURRENT terms, or null.

    Scoped to the current version on purpose: consent is not retroactive, so a
    live grant carrying last term's string is not consent to these terms and
    must not be reported as one. The student is shown the new copy and grants
    again; the old row stays live and untouched, which is what keeps
    `interview_sessions.consent_id` meaningful for the interviews conducted
    under it.

    No role check — a signed-in user may always read their own consent state,
    and a non-student simply has none.
    """
    row = _live_consent(db, session["userId"], settings.interview_consent_version)
    return ConsentStateOut(
        version=settings.interview_consent_version,
        consent=None if row is None else _consent_out(row),
        provider=settings.interview_provider_label,
    )


@student_router.post(
    "/consent", response_model=ConsentOut, status_code=status.HTTP_201_CREATED
)
def grant_consent(
    body: ConsentIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ConsentOut:
    """Record an acknowledgement of the college's policy (B6.1). A GRANT IS A
    ROW AND A ROW IS NEVER EDITED.

    Changing what is acknowledged revokes the live row and inserts a new one,
    rather than updating the old row's booleans in place. That is the whole
    reason this is a table: `interview_sessions.consent_id` pins the exact grant
    that was live when an interview opened, so "was this student consented, to
    what, at the time of interview X" is answerable years later. Mutating a
    grant would rewrite the answer for every interview already pointing at it.

    THE SCOPES COME FROM THE POLICY, NEVER FROM THE REQUEST (B6.1). The client
    posts this at Start rather than from a form with three tick boxes; the
    server resolves `interview_policies` for this student's college and course
    and copies the two storage scopes onto the row. `scope_live_ai` is written
    TRUE and has no policy column, because it is not a switch a college owns:
    the interview IS a live AI conversation, so "no" to it is not an interview
    with a setting turned off, it is no interview — which is what not pressing
    Start already means. The column stays, and stays three, so that a reader a
    year from now can still see that this was disclosed.

    IT IS IDEMPOTENT, AND THAT IS LOAD-BEARING RATHER THAN TIDY. A live row for
    this version whose scopes already match the policy is RETURNED UNCHANGED —
    no supersede, no new row. Without that, a student pressing Start in a second
    tab would stamp `revoked_at` on the very row their running interview is
    pinned to, and `_make_heartbeat` would close that interview 4014 "Consent
    withdrawn" — a sentence they did not earn. With it, a supersede happens only
    when the policy has actually changed, which is precisely when the
    compatibility board says a running session may end.

    EXISTING ROWS KEEP WORKING. A grant written in 2026-08 from the old
    three-tick panel is live and this version's; it opens interviews exactly as
    before. It is superseded the first time its scopes disagree with the college
    policy, silently, at a Start the student was pressing anyway — which is the
    compat rule ("existing consent rows keep working for the running version")
    without a prompt anybody has to read twice.

    422 for a version this build does not know (§7.1). That is the one job a
    version string has: a stale cached SPA must not be able to acknowledge copy
    the student never saw.
    """
    if session.get("role") != Role.STUDENT.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a student can consent to a mock interview.",
        )
    current = settings.interview_consent_version
    if body.version != current:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Consent terms have changed (this server is asking for "
                f"'{current}'). Reload the page and read them again."
            ),
        )

    user_id = session["userId"]
    student_id = session.get("studentId")
    # No `students` row means no college and no course, so the policy resolves
    # to `config.py`'s defaults — the same answer the socket would compute for
    # this account, and the same one it has always used.
    policy = (
        policy_for_student(db, student_id)
        if student_id
        else interview_policy_defaults()
    )
    now = datetime.now(timezone.utc)
    live = _live_consent(db, user_id, current)
    if (
        live is not None
        and bool(live.scope_store_transcript) == policy.store_transcript
        and bool(live.scope_store_audio) == policy.store_audio
        and bool(live.scope_live_ai) is True
    ):
        # Nothing has changed, so there is nothing new to record. Returning the
        # STANDING row rather than a fresh one is what keeps a running
        # interview's `consent_id` alive — see the docstring.
        return _consent_out(live)

    # Supersede, then insert, in ONE transaction — the partial unique index
    # `uq_interview_consent_active` (user_id, version) WHERE revoked_at IS NULL
    # allows exactly one live grant, so the revoke has to be part of the same
    # commit as the insert or the two are a race against each other.
    db.execute(
        update(InterviewConsent)
        .where(
            InterviewConsent.user_id == user_id,
            InterviewConsent.version == current,
            InterviewConsent.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    row = InterviewConsent(
        user_id=user_id,
        version=current,
        scope_live_ai=True,
        scope_store_transcript=policy.store_transcript,
        scope_store_audio=policy.store_audio,
        granted_at=now,
        user_agent=_user_agent(request),
        source_ip_hash=_ip_hash(request),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # A double-clicked button: another request committed its grant between
        # our UPDATE and our INSERT. The winner's row says the same thing ours
        # would have, so return it rather than 500-ing — the same shape
        # `conversations.ensure_conversation` uses on the same class of index.
        db.rollback()
        winner = _live_consent(db, user_id, current)
        if winner is None:  # pragma: no cover — index violated for another reason
            raise
        return _consent_out(winner)
    db.refresh(row)
    return _consent_out(row)


# THERE IS NO `DELETE /api/interview/consent` ANY MORE, AND THE 405 IS THE POINT
# (B6.1).
#
# The route was a student withdrawing consent. What it withdrew — whether the
# transcript is kept, whether the audio is captured — is now the COLLEGE's
# decision, taken once in `interview_policies` for everybody on a course, and a
# button that appeared to let one student overrule it would be a promise the
# server does not keep. So the handler is DELETED rather than made to answer
# 403: `admin_students.py` set that precedent and stated the reason —
# "all three answer 405, never 403, because a capability refusal would mean the
# endpoint is still there waiting for a grant". A 405 says the operation does
# not exist here, which is true; a 403 would say it exists and you may not, which
# is not.
#
# 405 CANNOT BE ROLE-CONDITIONAL, whatever 04-backend-changes.md's wording
# suggests: the status comes from Starlette finding a path with no handler for
# the method, before any dependency runs. So it is 405 for everyone, which is
# the honest reading of "the policy is the college's" — a mentor and the Main
# Admin cannot withdraw a student's acknowledgement either, and neither could
# they before.
#
# WHAT THIS COSTS, WRITTEN DOWN: `revoked_at` used to be stamped from here, and
# `_make_heartbeat`'s 4014 watches for exactly that. The stamp now comes from
# `POST /consent` superseding a grant whose scopes no longer match the policy,
# and 4014 fires only when the superseding row covers LESS — see
# `routers/interview.py::_successor_covers`. Close 4013 and 4014 keep their
# meanings; what has gone is the one trigger a student could pull themselves.
#
# The rows are untouched. Nothing is deleted, every historical grant stays
# readable, and `GET /api/interview/consent` still answers what this account
# holds.


# ---------------------------------------------------------------------------
# Staff endpoints — every one opens with _assert_can_access_student
#
# The gate is the FIRST LINE of each handler, not a router-level dependency,
# because it needs the path's `student_id` and because a reader must be able to
# see it in the function they are reading. A dependency declared elsewhere is a
# dependency someone deletes from one route while refactoring.
# ---------------------------------------------------------------------------


@staff_router.get(
    "/students/{student_id}/interviews", response_model=list[InterviewSessionOut]
)
def student_interviews(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[InterviewSessionOut]:
    """This student's interviews — MENTOR only within their own group.

    A MENTOR with no `Mentor` group gets 404 from the gate and sees NOBODY. That
    is not a special case here; it is `_assert_can_access_student`'s own branch,
    and reading "no mentor group" as "the whole programme" is the exact bug
    AGENTS.md rule 2 exists to prevent.
    """
    _assert_can_access_student(session, student_id, db)
    return _sessions_for_student(db, student_id)


@staff_router.get(
    "/students/{student_id}/interviews/progress",
    response_model=InterviewProgressOut,
)
def student_interview_progress(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> InterviewProgressOut:
    """This student's mock-interview trend, as THEY see it (B6.2).

    DECLARED BEFORE `/{session_id}`, and the order is load-bearing: FastAPI
    matches the first route whose path fits, and `progress` is a single path
    segment. Below the by-id route it would be swallowed by it and answer
    "Interview not found." for a trend that exists — the same trap
    `app/main.py` documents for `/admin/interview-questions/tracks`.

    NO VIEW ROW IS WRITTEN (B6.5). This is a trend, not a record: it quotes
    nothing anybody said and is the same four numbers the student's own home
    screen shows them. Logging it would put "a mentor opened your record" on
    their privacy panel every time somebody glanced at a chart.
    """
    _assert_can_access_student(session, student_id, db)
    return compose_interview_progress(db, student_id)


@staff_router.get(
    "/students/{student_id}/interviews/{session_id}",
    response_model=InterviewSessionOut,
)
def student_interview(
    student_id: str,
    session_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> InterviewSessionOut:
    _assert_can_access_student(session, student_id, db)
    # SECOND check: the row's own subject must be the student the gate passed
    # on. Never trust a session id in a path to imply whose interview it is.
    row = _session_of_student_or_404(db, session_id, student_id)
    # The payload is composed BEFORE the log, because `_log_record_view`
    # COMMITS — which expires every ORM object this handler is holding, so a
    # read taken afterwards costs a second SELECT to re-load a row that was
    # already in hand. Nothing about correctness, everything about not making a
    # mentor's screen do twice the work per interview.
    out = _session_detail(db, row)
    # B6.5, and AFTER both gates — a probe for another group's session must 404
    # without leaving a row claiming that record was opened.
    _log_record_view(db, session=session, request=request, row=row, what=VIEW_RECORD)
    return out


@staff_router.get(
    "/students/{student_id}/interviews/{session_id}/transcript",
    response_model=list[InterviewTurnOut],
)
def student_interview_transcript(
    student_id: str,
    session_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[InterviewTurnOut]:
    _assert_can_access_student(session, student_id, db)
    row = _session_of_student_or_404(db, session_id, student_id)
    turns = _transcript(db, row.id)
    _log_record_view(
        db, session=session, request=request, row=row, what=VIEW_TRANSCRIPT
    )
    return turns


@staff_router.get(
    "/students/{student_id}/interviews/{session_id}/report", response_model=None
)
def student_interview_report(
    student_id: str,
    session_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> InterviewReportOut | StaffInterviewReportOut:
    """The scorecard, with `raw_response` for the Main Admin only.

    BOTH gates, not either (§7.2): `_assert_can_access_student` decides which
    student may be read, `require_admin` (through `_may_see_raw_response`)
    decides who may see the model's private reasoning about them. Neither
    answers the other's question — the Main Admin still cannot read a student who
    does not exist, and a MENTOR in the right group still does not get
    `raw_response`.

    `response_model=None` is deliberate. Declaring the staff model would make
    FastAPI serialise a MENTOR's payload through it and emit
    `"raw_response": null`, which says "the model returned nothing" when the
    truth is "not for you". Returning the narrower model omits the key entirely,
    which is the honest sentence.
    """
    _assert_can_access_student(session, student_id, db)
    row = _session_of_student_or_404(db, session_id, student_id)
    evaluation = _evaluation_or_404(db, row.id)
    report = _report_out(evaluation)
    raw = evaluation.raw_response
    # AFTER `_evaluation_or_404` as well, so a request that 404s because no
    # report exists does not log a report view. The student's panel must list
    # what was READ, not what was asked for.
    _log_record_view(db, session=session, request=request, row=row, what=VIEW_REPORT)
    if _may_see_raw_response(session):
        return StaffInterviewReportOut(**report.model_dump(), raw_response=raw)
    return report


# ---------------------------------------------------------------------------


class InterviewRecordRow(BaseModel):
    """One interview across the whole programme, with the student named.

    This is the admin RECORDS view — distinct from InterviewSessionOut (a single
    student's own history), because it carries identity: a mentor or the admin
    reviewing recordings needs to know WHOSE interview each row is, which the
    per-student endpoints deliberately never repeat back.
    """

    session_id: str
    student_id: str
    student_name: str
    usn: str | None
    specialization: str | None
    status: str
    audio_recorded: bool
    #: See `InterviewSessionOut.audio_skipped_reason`: the gate that closed, or
    #: null. The grid's Audio column carries it as the chip's title and the open
    #: record spells it out, with the policy card one scroll away.
    audio_skipped_reason: str | None = None
    started_at: datetime
    ended_at: datetime | None
    #: B6.7. The grid used to carry no score at all, so the Interviews screen
    #: could draw a list and never an average — `interviews.component.ts:22`
    #: says so. NULLABLE, and a null renders as a DASH: a missing score and a
    #: zero mean opposite things to a mentor, and this is the one column on this
    #: grid where the difference is a judgement about a person.
    overall_score: int | None = None
    #: NULL means no evaluation row exists — the interview is still running, or
    #: predates the scorecard. Distinct from `'unavailable'`, which records that
    #: a report was attempted and did not arrive.
    report_status: str | None = None


# ---------------------------------------------------------------------------
# B6.7 — ONE query builder for both grids
#
# `/api/mentor/interviews` (role-gated, the screen that shipped) and
# `/api/admin/interviews` (capability-gated, paginated, filtered) return the
# same rows to the same people. That has to be a property of the call graph and
# not a coincidence of two similar `.where()` chains: the day they drift, the
# narrower one looks like a bug and the wider one is a leak, and nothing on
# either screen says which is which.
#
# BOTH FENCES, SEPARATELY, AND IN THIS FUNCTION. Rule 2 narrows a MENTOR to
# their own group and is applied ALWAYS — `app/governance.py`'s "a capability
# can never relax the student filter". The B1.2 reach narrows a GRANT somebody
# chose to hand over and is applied when a capability was checked. A caller who
# is both a mentor and a scoped grant holder gets the intersection, which is the
# only safe reading of two fences.
# ---------------------------------------------------------------------------


def _records_query(session: dict, *, reach: Reach | None):
    """The interviews this caller may see, as a SELECT of
    (InterviewSession, student name, USN, report_status, overall_score).

    `reach=None` means "no capability was checked here", which is the honest
    state of `/api/mentor/interviews`: its gate is `require_mentor`, a ROLE, and
    `scope_filter` answers "how far does this session's grant for capability K
    reach" — there is no K. Every caller it admits is fenced anyway, by their
    own mentor group or by being the Main Admin.
    """
    query = (
        select(
            InterviewSession,
            User.name,
            Student.usn,
            InterviewEvaluation.report_status,
            InterviewEvaluation.overall_score,
        )
        .join(Student, InterviewSession.student_id == Student.id)
        .join(User, Student.user_id == User.id)
        .outerjoin(
            InterviewEvaluation,
            InterviewEvaluation.interview_session_id == InterviewSession.id,
        )
        .where(InterviewSession.deleted_at.is_(None))
    )
    if session.get("role") == "MENTOR":
        mentor_id = session.get("mentorId")
        if not mentor_id:
            # No `Mentor` group => NOBODY, never the whole programme. Expressed
            # as an impossible predicate rather than an early `return []` so
            # that every caller — including the one that goes on to count rows
            # for a KPI tile — gets the same answer through the same query.
            return query.where(sa_false())
        query = query.where(Student.mentor_id == mentor_id)
    if reach is not None and not reach.everything:
        if reach.nothing:
            return query.where(sa_false())
        query = query.where(Student.id.in_(reach.student_ids()))
    return query


def _record_row(iv, name, usn, report_status, overall_score) -> InterviewRecordRow:
    return InterviewRecordRow(
        session_id=iv.id,
        student_id=iv.student_id,
        student_name=name,
        usn=usn,
        specialization=iv.specialization,
        status=iv.status,
        audio_recorded=iv.audio_recorded,
        audio_skipped_reason=iv.audio_skipped_reason,
        started_at=iv.started_at,
        ended_at=iv.ended_at,
        overall_score=overall_score,
        report_status=report_status,
    )


@staff_router.get("/interviews", response_model=list[InterviewRecordRow])
def all_interviews(
    recorded_only: bool = False,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[InterviewRecordRow]:
    """Every interview, newest first, with the student named — the records grid.

    Scope is rule 2, not a new rule: a MENTOR sees only interviews of students in
    their own group, a MENTOR WITH NO GROUP sees nobody (never the whole
    programme), and the Main Admin sees all. The narrowing is the same
    `session["mentorId"]` predicate the mentees list uses, applied in SQL so an
    out-of-group interview never leaves the database.

    `?recorded_only=1` returns only rows with a stored file — what the download
    grid filters to when an operator wants the recordings and not the failures.

    B1.4 LISTS THIS GRID AS A SCOPE TARGET AND THERE IS NOTHING HERE TO KEY A
    SCOPE ON, which is a finding rather than an omission. `policies.scope_filter`
    answers "how far does this session's grant for CAPABILITY K reach", and this
    endpoint checks no capability at all: its gate is `require_mentor`, a role.
    Every caller it admits is therefore fenced already — a MENTOR by their own
    group, two lines below, which is stricter than any scope; the Main Admin by
    being the Main Admin, whose baseline resolves to `everything` for every key
    there is. Picking a key to narrow on would change nobody's answer and would
    put a capability name in the code that no grant is ever checked against,
    which is the "promise the API does not keep" the catalogue note in
    app/models/governance.py is about. The key this wants is `admin.interviews`,
    which B6.7 adds along with the paginated grid; scope belongs in the same
    commit as the gate, because that is the commit that first admits somebody
    who is neither a mentor nor the office.

    THAT COMMIT HAS LANDED (B6.7) AND THIS ENDPOINT IS UNCHANGED in who it
    admits and which rows it returns. The capability-gated, scoped, paginated,
    filterable grid is `GET /api/admin/interviews`; this one keeps its role
    gate, its 200-row runaway guard and the client that already calls it. Both
    read `_records_query`, so the one thing the two can never do is disagree
    about which rows a caller may see. When the Interviews screen moves to the
    paginated grid, DELETE this — do not leave two endpoints holding two ideas
    of the same fence.
    """
    require_mentor(session)
    query = _records_query(session, reach=None)
    if recorded_only:
        query = query.where(InterviewSession.audio_recorded.is_(True))
    rows = db.execute(
        query.order_by(InterviewSession.started_at.desc(), InterviewSession.id).limit(_MAX_SESSIONS_LISTED)
    ).all()
    return [_record_row(*row) for row in rows]


class BulkAudioIn(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=200)
    track: str = TRACK_MIXED


@staff_router.post("/interviews/audio.zip")
def download_selected_audio(
    body: BulkAudioIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Bundle the selected recordings into one zip and hand it back as a download.

    THE SAME GATE AS A SINGLE RECORDING — `admin.interview_audio` — because a zip
    of forty students' voices is not a lesser act than one. Each id is
    re-checked: the row must exist, must belong to a student this caller may
    reach (rule 2, via `_assert_can_access_student`), and must actually have a
    file; ids that fail any check are skipped, never guessed at, and the zip
    carries only what the caller was entitled to and what exists.

    The filename inside the zip is the interview id and the USN — identifiable on
    purpose, because a folder of `interview-abc.wav` files nobody can attribute
    is useless to the reviewer who asked for them. The zip name carries no
    student data. Built to a temp file and deleted after the response is sent,
    because a 40 x 43 MB zip does not belong in this process's memory.
    """
    _require_developer(session, db)
    if body.track not in TRACKS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"track must be one of {sorted(TRACKS)}.",
        )
    import os
    import tempfile
    import zipfile

    fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="reep-recordings-")
    os.close(fd)
    added = 0
    # B6.5: ONE ROW PER RECORDING THAT ACTUALLY WENT INTO THE ZIP, never one per
    # request. Forty students' voices leaving in one file is forty students who
    # should each be able to see it on their own panel, and a single row against
    # the first session id would hide thirty-nine of them. Collected as the zip
    # is built — so a session that was skipped for scope or for a missing file
    # logs nothing — and written after the zip is complete, in one transaction.
    viewed: list[InterviewSession] = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for sid in dict.fromkeys(body.session_ids):  # de-dup, preserve order
            row = db.get(InterviewSession, sid)
            if row is None or row.deleted_at is not None or not row.audio_recorded:
                continue
            try:
                _assert_can_access_student(session, row.student_id, db)
            except HTTPException:
                continue  # out of this caller's scope; skip silently
            stem = row.audio_path or row.id
            try:
                path = read_track(stem, body.track)
            except FileNotFoundError:
                continue
            usn = db.scalar(select(Student.usn).where(Student.id == row.student_id)) or "unknown"
            arcname = f"{usn}-{download_name(stem, body.track)}"
            zf.write(path, arcname=arcname)
            added += 1
            viewed.append(row)
    if added == 0:
        os.remove(zip_path)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="None of the selected interviews have a recording you can download.",
        )
    for row in viewed:
        _log_record_view(db, session=session, request=request, row=row, what=VIEW_AUDIO)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="reep-interview-recordings.zip",
        background=BackgroundTask(os.remove, zip_path),
    )


def _require_developer(session: dict, db: Session) -> dict:
    """The `admin.interview_audio` capability — ADMIN by baseline, a MENTOR only
    when explicitly granted it. Still deliberately NARROWER than require_mentor.

    Every other staff read in this module is placement business: a mentor needs
    their mentees' scores, transcript and history to do the job. A voice
    recording is not placement business. It exists so whoever operates this
    system can hear what the ENGINE did — a mis-transcribed turn, the
    interviewer talking over an answer — and that is an operator's artefact that
    happens to contain a named student speaking.

    So what reads it is the operator's capability, not the programme's role. A
    MENTOR gets 403 here and 200 everywhere else in this file, which is the
    intended asymmetry and not an oversight: widening this to require_mentor
    would hand the most sensitive bytes REEP stores to every faculty account,
    for no question they cannot already answer from the transcript.

    WHAT CHANGED (2026-09): the check is a CAPABILITY, not a role set. ADMIN
    holds it through ROLE_BASELINE, so nothing the office could do moved. It is
    the one capability a MENTOR does not get with the rest of the scoped set
    (app/governance.py), so the asymmetry above survives intact. What is new is
    that the Main Admin can grant it to a NAMED faculty member, with a reason,
    on the audit trail, for the one person who genuinely needs to hear a
    session — rather than the only options being "every mentor" or "nobody".
    The 403 names the capability and where to ask for it, instead of being a
    dead end.

    THE ROLE THIS PARAGRAPH USED TO BE ABOUT IS GONE (2026-09-10). It argued
    the same asymmetry for DIRECTOR, which held every other capability by
    baseline and this one only by grant. DIRECTOR now holds NOTHING at all, so
    that reading is not merely stale, it is false in the direction that matters:
    it would tell you a DIRECTOR reads transcripts here, and it does not read
    anything. The argument was always about operator-versus-programme, never
    about that particular role, so it transfers to MENTOR unchanged.
    """
    require_capability(db, session, "admin.interview_audio")
    return session


# The audio download — ADMIN only, and the narrowest thing in this file
#
# THE STUDENT DOES NOT GET PLAYBACK OF THEIR OWN RECORDING, and that is a
# different judgement from §5.4's on scores, not a contradiction of it. §5.4
# refused to hide the SCORE because the model already speaks its verdict aloud,
# so concealing the number would only have hidden it from the person it is
# about, leaving "a secret file on a student" — the one arrangement here that
# would be hard to defend. Audio inverts every term of that argument:
#
#   * It tells the student nothing they do not have. They were there; the
#     transcript is already theirs, turn by turn, and it is the artefact that
#     helps them practise. A waveform of their own voice adds no information
#     about how they did.
#   * It is not a judgement about them, so there is nothing to be "kept from"
#     them. The recording exists so an authorised developer can hear what the
#     ENGINE did — why a turn was mis-transcribed, why the interviewer talked
#     over an answer — which is a debugging artefact that happens to contain a
#     student's voice.
#   * A student endpoint is the widest possible surface for the most sensitive
#     bytes REEP holds. Any live student session — a shared lab machine, a
#     borrowed laptop, a tab left signed in — would stream a named person's
#     voice on request. Every recording is reachable through exactly one role
#     here, and that is worth the asymmetry.
#
# What the student DOES get is honesty: `audio_recorded` is on their own session
# payload above, the consent copy told them a recording would be kept, and
# `DELETE /consent` stops the next one. Deletion of an existing recording goes
# through the placement cell, which is the same sentence §8.2 already makes them
# read about the transcript.
#
# The gate order below is `student_interview_report`'s, with the role check
# promoted from "which fields" to "whether at all" -- and tightened from
# require_admin to _require_developer, because "whether at all" is a
# different question from "how much".
# ---------------------------------------------------------------------------


@staff_router.get("/students/{student_id}/interviews/{session_id}/audio")
def student_interview_audio(
    student_id: str,
    session_id: str,
    request: Request,
    track: str = TRACK_MIXED,
    download: bool = False,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream one track of a stored interview recording. Defaults to the MIX.

    BOTH gates and then some, in this order and for three different questions:
    `_require_developer` says WHO may hear a recording at all — the
    `admin.interview_audio` capability: ADMIN by baseline, a MENTOR only by
    an explicit grant; narrower than every other read in this module —
    `_assert_can_access_student` says WHICH STUDENT this caller may read — an
    ADMIN still cannot reach a student who does not exist, and a MENTOR in the
    wrong group still gets 403 — and `_session_of_student_or_404` says the
    row's own subject really is the student in the path (§7.3: gating on the
    path and then loading a row by an id from somewhere else is a
    horizontal-privilege bug wearing a correct-looking first line).

    THREE FILES, selected by `?track=`, and the DEFAULT IS `mixed` — one file
    with both voices on one timeline, the way a phone recording sounds, because
    that is what somebody reviewing an interview actually plays. It is DERIVED:
    the recorder sums the two per-speaker tracks at close, having padded each to
    the session's wall clock so they line up (app/interview_audio.py's header
    sets out what that alignment can and cannot promise).

    `?track=student` AND `?track=interviewer` KEEP WORKING, unchanged, and they
    are still the faithful record — the student's is what their microphone
    captured, the interviewer's is exactly what was forwarded to the browser.
    Reach for them when the question is "what did the model actually send", and
    do NOT delete them as duplicates of the mix: the mix is regenerable from
    them and they are not recoverable from it.

    An earlier revision of this docstring said the two must never be mixed. That
    was correct about the files it described — before the timeline existed each
    track was a speech-only concatenation, so laying them side by side put
    answers under the wrong questions. The mix is honest now because the padding
    made it so, not because the objection was waved off.

    404 — never 403 and never 204 — for a session with no recording, so an
    admin cannot tell "this interview was not recorded" from "that is not a
    real id"; the same no-existence-leak rule the rest of this module follows.
    """
    _require_developer(session, db)
    _assert_can_access_student(session, student_id, db)
    row = _session_of_student_or_404(db, session_id, student_id)

    if track not in TRACKS:
        # 422 rather than a silent fallback to the default track: a client that
        # asked for the interviewer and got the mix would have a reviewer
        # listening to the wrong voice with nothing on screen saying so.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown audio track. Choose one of: {', '.join(TRACKS)}.",
        )

    # THE FLAG, never `audio_path is not None` — app/models/interview.py sets out
    # the four different facts a NULL path collapses into one (capture disabled,
    # consent refused, the write failed, the interview predates capture).
    if not row.audio_recorded:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No recording for this interview."
        )

    # The stored name, falling back to the session id — which is the same string
    # in every recording this store has written, and differs only when the
    # finalizing UPDATE never landed. app/interview_audio.py names files after
    # the session for exactly that case.
    stem = row.audio_path or row.id
    try:
        path = read_track(stem, track)
    except (FileNotFoundError, AudioStoreError):
        # The row says a recording exists and the disk disagrees: the reaper ran
        # between the two, the volume is not mounted, or somebody tidied up.
        # WARNING rather than 500 — nothing is broken for the caller and there is
        # nothing they can do — but it is logged, because a row claiming audio
        # that is not there is how a deletion request quietly fails to be honoured.
        log.warning(
            "Interview session %s claims stored audio (path=%r, track=%s) that is "
            "not on disk.",
            row.id,
            row.audio_path,
            track,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The stored recording is missing.",
        )

    # B6.5, LAST — after the capability, the group gate, the ownership check,
    # the track validation and the file actually being on disk. Every one of
    # those 404s and 422s is a read that did not happen, and a student's panel
    # saying their voice was played when it was not is the one failure mode of
    # an access log that is worse than having none.
    _log_record_view(db, session=session, request=request, row=row, what=VIEW_AUDIO)

    # FileResponse, not Response(content=...): a 15-minute track is ~43 MB, where
    # routers/student.py's upload download reads whole files because those are
    # capped at 10 MB. This streams from disk and answers Range requests, which
    # is what lets a reviewer seek in an <audio> element instead of waiting for
    # the whole file before hearing the first second.
    return FileResponse(
        path,
        media_type="audio/wav",
        headers={
            # RFC 6266 through document_store's helper — the header formatter ONLY.
            # Nothing about the upload store is reused here (§8.4's third
            # objection: admitting audio to that store would loosen the magic-byte
            # rule that makes it trustworthy), and this module writes nothing into
            # `inline` by DEFAULT so the recording plays in the tab rather than
            # landing in a downloads folder that syncs and backs up a student's
            # voice; the filename carries the interview id and no student name.
            # `?download=1` flips it to attachment for an operator who has
            # decided to keep a copy on their own machine — a deliberate act,
            # not the default, which is why it is opt-in per request rather than
            # a change to the header's safe default.
            "Content-Disposition": content_disposition(
                download_name(stem, track), inline=not download
            )
        },
    )


# ---------------------------------------------------------------------------
# B6.7 — the records grid, its KPIs and its extract
#
# THE CAPABILITY IS `admin.interviews`, AND IT IS WHY THIS SECTION EXISTS AT
# ALL. `all_interviews` above states the finding this task was written from:
# scope cannot be applied to an endpoint whose gate is a ROLE, because
# `policies.scope_filter` answers "how far does this session's grant for
# capability K reach" and there was no K. Adding the key and adding the scope
# are one commit, because that is the commit which first admits somebody who is
# neither a mentor nor the office — a faculty member the Main Admin handed the
# Interviews screen to, whose grant may reach one department.
#
# The key is in the catalogue as `_P` (programme) and `carries_pii=True`: these
# rows name students and carry scores. `carries_pii` means B2.4's second
# signature applies to a DEPUTY granting it — such a grant stays
# `pending_approval` and holds nothing until the Main Admin or another deputy
# approves it, which the Governance screen reports rather than pretending. The
# Main Admin's own grant is live at once (`initial_approval_state`).
# ---------------------------------------------------------------------------

CAPABILITY = "admin.interviews"

#: The terminal vocabulary, plain strings — §6.1, and the same rule every other
#: interview vocabulary follows. A filter naming anything else is a 422 rather
#: than an empty grid: "no interviews failed today" and "you spelled `failed`
#: wrong" are opposite facts and an empty table renders them identically.
GRID_STATUSES = ("running", "completed", "abandoned", "failed")

#: What `?track=` must be to ask for the GENERIC interview, whose
#: `interview_sessions.specialization` is NULL. Without a word for it there is
#: no way to filter for the interviews that ran without a track, which is
#: exactly the set somebody investigating "why did this student get no
#: scorecard" wants — the generic interview never reaches wrap-up.
TRACK_GENERAL = "general"

#: Default and maximum page sizes. The 200-row cap `_MAX_SESSIONS_LISTED`
#: expresses is not raised here, it is REPLACED: the cap was a runaway guard on
#: an endpoint that could not paginate, and the comment above it said the moment
#: a real cohort approached it was the moment to add a cursor rather than a
#: bigger number. This is that cursor.
GRID_PAGE_SIZE = 50
GRID_MAX_PAGE_SIZE = 200


class InterviewGridOut(BaseModel):
    """One page of the records grid.

    A CURSOR, NOT `?page=`, and 04-backend-changes.md's `page=&page_size=` is
    the thing being corrected. Interviews are written continuously, so an offset
    taken against a list that grows at the top SKIPS rows: a placement officer
    paging through a morning's interviews would silently never see the ones that
    were pushed across the page boundary while they read. The query already
    orders by `(started_at, id)`, which is a total order and therefore a usable
    keyset — so the page after this one is defined by the last row of this one
    rather than by a count that has since changed.

    `next_cursor` is null on the last page. There is deliberately NO total
    count: counting every interview on the deployment to render "page 3 of 47"
    costs a full scan on every keystroke of a filter, and the KPI endpoint
    answers the question that number is standing in for, once, and properly.
    """

    rows: list[InterviewRecordRow]
    next_cursor: str | None
    page_size: int


class TrackCountOut(BaseModel):
    """One track's slice of the KPIs. `track_code` is null for the generic
    interview — the same NULL `interview_sessions.specialization` carries, not a
    missing value."""

    track_code: str | None
    interviews: int
    completed: int
    #: Over the SCORED interviews of this track only, and null when none are.
    #: Never 0.0: a track nobody has been scored on and a track everybody failed
    #: are opposite facts.
    average_overall: float | None


class InterviewKpisOut(BaseModel):
    """The tiles above the grid, over exactly the rows the grid would return.

    Computed from the SAME `_records_query` the grid uses, filters included, so
    a tile can never report a number the list below it cannot produce — which is
    the way KPI endpoints usually go wrong: a second query, written from the
    same description, that quietly forgets one fence.
    """

    interviews: int
    completed: int
    abandoned: int
    failed: int
    running: int
    #: Distinct students, not interviews. "Sixty interviews" and "sixty students
    #: practising" are different facts and the office plans on the second.
    students: int
    recorded: int
    scored: int
    average_overall: float | None
    by_track: list[TrackCountOut]


def _encode_cursor(started_at: datetime, session_id: str) -> str:
    """`(started_at, id)` as one opaque token.

    Opaque so the client cannot construct one: a hand-made cursor is a client
    deciding where a scoped list starts, and the shape it would have to know is
    a shape this endpoint is then unable to change.
    """
    raw = f"{started_at.isoformat()}|{session_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    """The other half, or a 422 naming the problem.

    422 AND NOT A SILENT RESET TO PAGE ONE. A cursor that cannot be read means
    the client and the server disagree about where the list is, and quietly
    handing back the first page again is how a grid loops forever showing the
    same fifty rows while the operator scrolls.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        stamp, _, session_id = raw.partition("|")
        at = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That page cursor is not one this endpoint issued.",
        )
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That page cursor is not one this endpoint issued.",
        )
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at, session_id


def _students_in(reach_field: str, value: str):
    """The students under one college or one cohort, as a subquery.

    BUILT FROM `Reach` RATHER THAN WRITTEN OUT, and that is the whole point.
    "Which students are in this college" is already answered by
    `policies.Reach.student_ids`, including the part everybody gets wrong — a
    student reaches a department through their BATCH or through
    `students.department_id`, and a filter that reads only the first one
    silently drops every unseated student, which is every student at a college
    that has not built its batches yet. Constructing a one-element reach reuses
    that expression exactly instead of copying it.
    """
    return Reach(everything=False, **{reach_field: frozenset({value})}).student_ids()


def _grid_filters(
    query,
    *,
    college: str | None,
    cohort: str | None,
    track: str | None,
    grid_status: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    recorded_only: bool,
    started_at,
    specialization,
    row_status,
    student_id,
    audio_recorded=None,
):
    """Apply B6.7's filters to either grid query.

    Takes the COLUMNS as arguments because the same six filters have to narrow
    two different tables — `interview_sessions` for the grid and its KPIs,
    `interview_score_summaries` for the extract, which is the table that outlives
    the sessions. One filter function over two column sets rather than two
    functions: the extract must return the same interviews the grid shows or the
    file is not the list the operator was looking at.
    """
    if college:
        query = query.where(student_id.in_(_students_in("colleges", college)))
    if cohort:
        query = query.where(student_id.in_(_students_in("cohorts", cohort)))
    if track:
        wanted = track.strip().lower()
        if wanted == TRACK_GENERAL:
            query = query.where(specialization.is_(None))
        else:
            query = query.where(func.lower(specialization) == wanted)
    if grid_status:
        query = query.where(row_status == grid_status)
    if date_from:
        query = query.where(started_at >= date_from)
    if date_to:
        query = query.where(started_at <= date_to)
    if recorded_only:
        # `audio_recorded` is optional because the extract's table does not have
        # the column — whether a recording exists is a fact about the SESSION,
        # and the summaries outlive it. An assertion rather than a silently
        # ignored filter: a `?recorded_only=1` that quietly returned everything
        # would be a reviewer downloading a file they believe is the recorded
        # subset.
        assert audio_recorded is not None, (
            "recorded_only has no meaning over a table with no audio column"
        )
        query = query.where(audio_recorded.is_(True))
    return query


def _check_grid_status(grid_status: str | None) -> str | None:
    if grid_status and grid_status not in GRID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"status must be one of: {', '.join(GRID_STATUSES)}.",
        )
    return grid_status


@admin_router.get("/interviews", response_model=InterviewGridOut)
def admin_interviews(
    response: Response,
    college: str | None = None,
    cohort: str | None = None,
    track: str | None = None,
    grid_status: str | None = Query(None, alias="status"),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    recorded_only: bool = False,
    cursor: str | None = None,
    page_size: int = Query(GRID_PAGE_SIZE, ge=1, le=GRID_MAX_PAGE_SIZE),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> InterviewGridOut:
    """The records grid: every interview this caller may see, one page at a time.

    THE REACH IS CHECKED FOR `nothing` FIRST AND STATED IN A HEADER. "May see
    everything" and "may see nothing" are opposite facts, not two ends of a
    scale (`app/policies.py`), and a `none` reach rendering as an empty grid
    tells the office there were no interviews rather than that they cannot see
    them. `X-Reep-Scope` carries `programme` | `narrowed` | `none` on every
    response, including the empty one.

    RULE 2 STILL APPLIES ON TOP, in `_records_query`: a MENTOR holding this
    capability sees their own group and no further, and a MENTOR with no group
    sees nobody. A capability can never relax the student filter.

    NO VIEW ROW IS WRITTEN (B6.5). This is a list, and one officer opening it
    would otherwise put fifty "somebody opened your record" lines on fifty
    students' privacy panels for a screen nobody read a word of. The by-id
    reads are what get logged.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    scope_header(response, reach)
    _check_grid_status(grid_status)

    query = _grid_filters(
        _records_query(session, reach=reach),
        college=college,
        cohort=cohort,
        track=track,
        grid_status=grid_status,
        date_from=date_from,
        date_to=date_to,
        recorded_only=recorded_only,
        started_at=InterviewSession.started_at,
        specialization=InterviewSession.specialization,
        row_status=InterviewSession.status,
        audio_recorded=InterviewSession.audio_recorded,
        student_id=InterviewSession.student_id,
    )
    if cursor:
        at, last_id = _decode_cursor(cursor)
        # A ROW-VALUE COMPARISON, not `started_at < at OR (= AND id < …)`
        # spelled out: the two are the same predicate and only one of them
        # stays correct when somebody edits it. Both columns descend, which is
        # what makes the tuple comparison the right one — a mixed-direction
        # order would need the long form and would be wrong the first time two
        # interviews shared a start time.
        query = query.where(
            tuple_(InterviewSession.started_at, InterviewSession.id)
            < tuple_(at, last_id)
        )

    rows = db.execute(
        query.order_by(
            InterviewSession.started_at.desc(), InterviewSession.id.desc()
        ).limit(page_size + 1)  # one extra: "is there another page" without a count
    ).all()
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    out = [_record_row(*row) for row in rows]
    next_cursor = (
        _encode_cursor(rows[-1][0].started_at, rows[-1][0].id) if has_more else None
    )
    return InterviewGridOut(rows=out, next_cursor=next_cursor, page_size=page_size)


@admin_router.get("/interviews/summary", response_model=InterviewKpisOut)
def admin_interviews_summary(
    response: Response,
    college: str | None = None,
    cohort: str | None = None,
    track: str | None = None,
    grid_status: str | None = Query(None, alias="status"),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    recorded_only: bool = False,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> InterviewKpisOut:
    """The tiles above the grid, over exactly the rows the grid would return.

    Same gate, same reach, same filters, same `_records_query`. The aggregation
    happens in Postgres rather than by fetching the rows and counting them in
    Python: this is the number the office asks for on a deployment where the
    grid is deliberately paginated, so it must not be the one place that reads
    every interview into memory.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    scope_header(response, reach)
    _check_grid_status(grid_status)

    base = _grid_filters(
        _records_query(session, reach=reach),
        college=college,
        cohort=cohort,
        track=track,
        grid_status=grid_status,
        date_from=date_from,
        date_to=date_to,
        recorded_only=recorded_only,
        started_at=InterviewSession.started_at,
        specialization=InterviewSession.specialization,
        row_status=InterviewSession.status,
        audio_recorded=InterviewSession.audio_recorded,
        student_id=InterviewSession.student_id,
    ).subquery()

    totals = db.execute(
        select(
            func.count(),
            func.count().filter(base.c.status == "completed"),
            func.count().filter(base.c.status == "abandoned"),
            func.count().filter(base.c.status == "failed"),
            func.count().filter(base.c.status == "running"),
            func.count(func.distinct(base.c.student_id)),
            func.count().filter(base.c.audio_recorded.is_(True)),
            func.count(base.c.overall_score),
            func.avg(base.c.overall_score),
        ).select_from(base)
    ).one()
    (
        interviews,
        completed,
        abandoned,
        failed,
        running,
        students,
        recorded,
        scored,
        average,
    ) = totals

    per_track = db.execute(
        select(
            base.c.specialization,
            func.count(),
            func.count().filter(base.c.status == "completed"),
            func.avg(base.c.overall_score),
        )
        .select_from(base)
        .group_by(base.c.specialization)
        .order_by(func.count().desc(), base.c.specialization)
    ).all()

    return InterviewKpisOut(
        interviews=int(interviews or 0),
        completed=int(completed or 0),
        abandoned=int(abandoned or 0),
        failed=int(failed or 0),
        running=int(running or 0),
        students=int(students or 0),
        recorded=int(recorded or 0),
        scored=int(scored or 0),
        # NULL out of `avg()` when nothing was scored, carried through as None
        # rather than coalesced to 0. `func.avg` over an empty set is the one
        # aggregate that gets this right for free; do not "tidy" it.
        average_overall=None if average is None else round(float(average), 1),
        by_track=[
            TrackCountOut(
                track_code=code,
                interviews=int(n or 0),
                completed=int(done or 0),
                average_overall=None if avg is None else round(float(avg), 1),
            )
            for code, n, done, avg in per_track
        ],
    )


def _summary_query(session: dict, *, reach: Reach):
    """The score SUMMARIES this caller may see — the extract's own two fences.

    A separate query from `_records_query` because it reads a different table,
    and it reads a different table on purpose: `interview_score_summaries`
    outlives the 180-day purge, so the extract of an academic year still has
    rows in it next September when the sessions themselves are gone. Both fences
    are applied here in the same order and for the same reasons; the one thing
    that must never happen is for this to grow a THIRD idea of who may be in a
    file that cannot be recalled.
    """
    query = (
        select(InterviewScoreSummary, User.name, Student.usn)
        .join(Student, InterviewScoreSummary.student_id == Student.id)
        .join(User, Student.user_id == User.id)
    )
    if session.get("role") == "MENTOR":
        mentor_id = session.get("mentorId")
        if not mentor_id:
            return query.where(sa_false())
        query = query.where(Student.mentor_id == mentor_id)
    if not reach.everything:
        if reach.nothing:
            return query.where(sa_false())
        query = query.where(Student.id.in_(reach.student_ids()))
    return query


@admin_router.get("/interviews/export.csv")
def admin_interviews_export(
    request: Request,
    college: str | None = None,
    cohort: str | None = None,
    track: str | None = None,
    grid_status: str | None = Query(None, alias="status"),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """The interviews extract — SUMMARY ROWS ONLY, and that is B14's rule here.

    NOT ONE WORD ANYBODY SAID LEAVES IN THIS FILE. No transcript, no strengths,
    no improvements, no `drill`, no `summary` sentence and above all no
    `raw_response` — the model's private reasoning about a student, which does
    not travel to the student themselves and certainly does not travel in a
    spreadsheet. What leaves is what `interview_score_summaries` holds: a date,
    a track, a status and four integers. An export is the one thing in REEP that
    cannot be recalled (`app/exports.py`), and the narrowest useful file is the
    right one.

    IT READS THE SUMMARIES, NOT THE SESSIONS, for B6.2's reason: this is the
    table that survives retention, so an extract taken in September still
    contains March, which is precisely when somebody asks for it.

    THE THREE B14 RULES APPLY UNCHANGED and none of them are re-implemented
    here: `scope_filter` decides who may be in the file, `carries_personal_
    columns` decides whether it may name them, and `record_export` leaves the
    receipt. The receipt commits and a failure to write it FAILS the download —
    that asymmetry with `_log_record_view` above is deliberate and
    `app/exports.py` argues it.

    `?recorded_only=` IS ABSENT AND THAT IS NOT AN OVERSIGHT: whether a
    recording exists is a fact about the session, and this file is built from
    the summaries, which outlive it. A filter that silently meant "only the
    interviews not yet reaped" would be a filter that changes its answer every
    night at 02:00.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    carried_pii = carries_personal_columns(db, session)
    _check_grid_status(grid_status)

    query = _grid_filters(
        _summary_query(session, reach=reach),
        college=college,
        cohort=cohort,
        track=track,
        grid_status=grid_status,
        date_from=date_from,
        date_to=date_to,
        recorded_only=False,
        started_at=InterviewScoreSummary.started_at,
        specialization=InterviewScoreSummary.track_code,
        row_status=InterviewScoreSummary.status,
        student_id=InterviewScoreSummary.student_id,
    )
    rows = (
        []
        if reach.nothing
        else db.execute(
            query.order_by(
                InterviewScoreSummary.started_at.desc(), InterviewScoreSummary.id
            )
        ).all()
    )

    header, body = drop_personal(
        [
            "Name",
            "USN",
            "Started",
            "Track",
            "Status",
            "Overall",
            "Communication",
            "Domain",
            "Structure",
            "Record",
        ],
        [
            [
                name,
                usn or "",
                s.started_at.isoformat(),
                s.track_code or TRACK_GENERAL,
                s.status,
                # A BLANK CELL FOR A MISSING SCORE, never a 0. This file is
                # opened in a spreadsheet and averaged; a zero would drag a
                # cohort's average down by the interviews nobody marked, which
                # is the same mistake as plotting a NULL at the origin, made
                # somewhere nobody will ever see this code.
                "" if s.overall_score is None else s.overall_score,
                "" if s.communication_score is None else s.communication_score,
                "" if s.domain_score is None else s.domain_score,
                "" if s.structure_score is None else s.structure_score,
                # Says WHY the four numbers may be all there is, so a reader
                # does not report a missing transcript as a bug.
                "on file" if s.session_id else "reaped after retention",
            ]
            for s, name, usn in rows
        ],
        ["Name", "USN"],
        carry=carried_pii,
    )
    record_export(
        db,
        session=session,
        request=request,
        kind="interviews",
        filters={
            **scope_note(reach),
            "college": college,
            "cohort": cohort,
            "track": track,
            "status": grid_status,
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None,
        },
        rows=len(body),
        carried_pii=carried_pii,
    )
    return csv_response(
        header,
        body,
        "reep-interview-scores.csv",
        reach=reach,
        carried_pii=carried_pii,
    )
