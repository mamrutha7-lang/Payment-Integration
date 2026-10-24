"""Call sessions as their participants and reviewers see them, plus the
call-close endpoint.

    GET  /api/platform/calls                 mine (STUDENT) / all (Main Admin)
    GET  /api/platform/calls/{id}            + recording_s3_url for a holder of
                                             `admin.interview_audio`, and only
                                             for a holder of it
    POST /api/platform/calls/{id}/close      package whatever the buffer holds

Who may read a call: its owner; the Main Admin; and a MENTOR only through
rule 2's gate on the linked interview record's student
(`_assert_can_access_student`, imported and never reimplemented).

Who may HEAR one is a narrower question with a different answer, asked
separately in `_detail` — see the argument there.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...governance import has_capability
from ...identity import get_current_session
from ...models.interview import InterviewSession
from ...models.user import Role
from ...models.voice_platform import PlatformCallSession
from ...routers.mentor import _assert_can_access_student
from ..monitoring.cloudwatch import handler_span
from ..storage import aurora
from ..storage.s3 import recording_store
from ..streaming import buffer as wav_buffer
from . import call_close
from .admin import CallOut, call_out

log = logging.getLogger("app.voice_platform.api.calls")

router = APIRouter(prefix="/api/platform/calls", tags=["voice-platform-calls"])


class CallDetailOut(CallOut):
    recording_s3_url: str | None
    recording_url_expires_in: int | None
    recording_available: bool
    recording_note: str


class CloseOut(BaseModel):
    session_id: str
    status: str
    recorded: bool
    uploaded: bool
    s3_key: str | None
    size_bytes: int
    duration_ms: int
    truncated: bool
    format: str
    notes: list[str]
    call: CallDetailOut


def _can_read(session: dict, row: PlatformCallSession, db: Session) -> None:
    role = session.get("role")
    if row.user_id == session.get("userId") or role == Role.ADMIN.value:
        return
    if role == Role.MENTOR.value and row.interview_session_id:
        interview = db.get(InterviewSession, row.interview_session_id)
        if interview is not None:
            _assert_can_access_student(session, interview.student_id, db)  # raises 403/404
            return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your call session.")


def _detail(row: PlatformCallSession, db: Session, session: dict) -> CallDetailOut:
    """The call as its reader sees it — metadata for everyone `_can_read`
    admits, and a link to the audio for a holder of `admin.interview_audio`.

    THE RECORDING URL IS THE CAPABILITY'S AND NOTHING ELSE ON THIS PAYLOAD IS.
    `_can_read` admits three callers — the candidate themselves, the Main Admin,
    and a MENTOR through rule 2's gate — and until now all three were handed a
    presigned URL to the dual-channel WAV. That is the same audio the interview
    record refuses them: `api/media_bridge.py` builds one
    `TeeRecorder(buffer, primary)`, so the bytes behind `recording_s3_key` are
    the bytes behind `GET /api/mentor/students/{id}/interviews/{id}/audio`, and
    that endpoint has gated playback on `admin.interview_audio` since it was
    written. One recording cannot have two access policies; the stricter one is
    the policy, and this module is the second door onto it rather than a second
    opinion about it.

    The argument is `app/routers/interview_records.py`'s (`_require_developer`,
    and the block above `student_interview_audio`), and it is quoted rather than
    restated so the next reader finds it there and does not re-litigate it. On
    why a MENTOR is refused while every other staff read in this product lets
    them through: "A voice recording is not placement business. It exists so
    whoever operates this system can hear what the ENGINE did ... an operator's
    artefact that happens to contain a named student speaking." On why the
    candidate's own session is the widest surface of the three rather than the
    safest: "A student endpoint is the widest possible surface for the most
    sensitive bytes REEP holds. Any live student session — a shared lab machine,
    a borrowed laptop, a tab left signed in — would stream a named person's
    voice on request. Every recording is reachable through exactly one role
    here, and that is worth the asymmetry."

    `has_capability` and not `require_capability`, because the refusal has to
    narrow the PAYLOAD and not the REQUEST: a mentor who may legitimately read
    this call still gets its status, its turns, its close code and its timings,
    which is everything they could answer a placement question with. Only the
    link goes. And the presign is skipped rather than minted-and-dropped — a
    signed URL handed to nobody is still a signed URL, sitting in this process's
    logs and in S3's access record with hours left on it.

    What is NOT withheld is the FACT of a recording. `recording_available` stays
    true and the note says plainly that one was kept and what it takes to hear
    it, which is interview_records' own "what the student DOES get is honesty":
    the consent copy already told them a recording would be kept, and a payload
    that quietly reported `false` would make this module the one place in REEP
    that lies to a student about their own voice.
    """
    base = call_out(row).model_dump()
    url: str | None = None
    expires: int | None = None
    may_hear = has_capability(db, session, "admin.interview_audio")
    if row.recording_s3_key and may_hear:
        store = recording_store()
        if store is not None:
            policy = aurora.get_recording_policy(db, row.degree_level)
            ttl = policy.presign_ttl_seconds if policy else None
            try:
                url = store.presigned_url(row.recording_s3_key, ttl)
                expires = ttl or store.presign_ttl_seconds
            except Exception as exc:  # noqa: BLE001 - a link is a convenience
                log.error("Presign failed for %s: %s", row.recording_s3_key, exc)
    meta = row.recording_meta or {}
    if row.recording_s3_key and url:
        note = "Presigned link; expires with recording_url_expires_in."
    elif row.recording_s3_key and not may_hear:
        # Ordered before the "no bucket client" branch deliberately: a caller
        # without the capability must be told the same sentence whether or not
        # this server happens to hold S3 credentials, or the note becomes a way
        # to probe the deployment's configuration from a student session.
        note = (
            "A recording was kept for this call. Playing it back needs the 'Interview audio' "
            "capability, which an administrator can grant in Governance."
        )
    elif row.recording_s3_key:
        note = "Recording is in S3 but no bucket client is configured on this server to sign a link."
    elif meta.get("local_path"):
        note = "Recording is on the server's audio volume (no PLATFORM_RECORDINGS_BUCKET)."
    elif row.status == "running":
        note = "Call is still running."
    else:
        note = "No recording: the policy was off, the candidate did not consent to audio storage, or nobody spoke."
    return CallDetailOut(
        **base,
        recording_s3_url=url,
        recording_url_expires_in=expires,
        recording_available=bool(row.recording_s3_key or meta.get("local_path")),
        recording_note=note,
    )


def _row_or_404(db: Session, session_id: str) -> PlatformCallSession:
    row = aurora.get_call_session(db, session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call session not found.")
    return row


@router.get("", response_model=list[CallOut])
def list_calls(
    degree: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[CallOut]:
    role = session.get("role")
    try:
        if role == Role.ADMIN.value:
            rows = aurora.list_call_sessions(db, degree_level=degree, limit=limit)
        else:
            rows = aurora.list_call_sessions(db, degree_level=degree, user_id=session["userId"], limit=limit)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return [call_out(r) for r in rows]


@router.get("/{session_id}", response_model=CallDetailOut)
def get_call(session_id: str, session: dict = Depends(get_current_session), db: Session = Depends(get_db)) -> CallDetailOut:
    row = _row_or_404(db, session_id)
    _can_read(session, row, db)
    return _detail(row, db, session)


@router.post("/{session_id}/close", response_model=CloseOut)
@handler_span("calls.close")
async def close_call(
    session_id: str, session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> CloseOut:
    """WAV Buffer Upload for a call whose socket did not close cleanly.

    Owner or the Main Admin. If the buffer is still live in this worker, it is
    rendered and uploaded now; if the row was already closed, the current state
    is returned; a row that is `running` on another worker with no buffer here
    is 409 — the honest answer is "ask that worker", not a fake close.
    """
    row = _row_or_404(db, session_id)
    if row.user_id != session.get("userId") and session.get("role") != Role.ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your call session.")
    live = wav_buffer.live(session_id)
    if row.status != "running" and live is None:
        db.refresh(row)
        detail = _detail(row, db, session)
        return CloseOut(
            session_id=row.id, status=row.status, recorded=bool(row.recording_bytes), uploaded=bool(row.recording_s3_key),
            s3_key=row.recording_s3_key, size_bytes=row.recording_bytes or 0, duration_ms=row.recording_duration_ms or 0,
            truncated=row.recording_truncated, format=str((row.recording_meta or {}).get("format", "wav")),
            notes=["already closed"], call=detail,
        )
    if live is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This call is still running and its audio buffer is not on this worker; close the socket first.",
        )
    degree = row.degree_level
    db.close()  # the close handler opens its own session on a worker thread
    report = await call_close.finish_call(
        session_id, degree_level=degree, code=1001, reason="Closed via POST /calls/{id}/close", buffer=live
    )
    fresh_db = next(get_db())
    try:
        fresh = _row_or_404(fresh_db, session_id)
        detail = _detail(fresh, fresh_db, session)
    finally:
        fresh_db.close()
    return CloseOut(
        session_id=session_id, status=report.status, recorded=report.recorded, uploaded=report.uploaded,
        s3_key=report.s3_key, size_bytes=report.size_bytes, duration_ms=report.duration_ms,
        truncated=report.truncated, format=report.format, notes=report.notes, call=detail,
    )
