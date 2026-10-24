"""Admin Dashboard CRUD — Undergraduate/Postgraduate management and Recording
Policies. Every route is Main Admin only (rule 2's `require_admin`).

    /api/platform/admin/status                      what is configured
    /api/platform/admin/specializations[/{id}]      per-degree tracks
    /api/platform/admin/specializations/{id}/questions
    /api/platform/admin/questions/{id}
    /api/platform/admin/time-limits                 per degree / per track
    /api/platform/admin/recording-policies[/{degree}]
    /api/platform/admin/candidates[/{id}]           + /bulk, /{id}/link
    /api/platform/admin/calls                       recent call sessions

A ValueError from the repository is a 422 with the sentence as the detail —
the same sentence an operator sees from the CLI — and a duplicate key is a 409.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Final

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...config import settings
from ...db import get_db
from ...identity import get_current_session
from ...models.user import User
from ...models.voice_platform import (
    DEGREE_LEVELS,
    PlatformCallSession,
    PlatformCandidate,
    PlatformQuestion,
    PlatformRecordingPolicy,
    PlatformSpecialization,
    PlatformTimeLimit,
)
from ...routers.mentor import require_admin
from .. import ssm_config
from ..engine import nova as engine
from ..monitoring.cloudwatch import handler_span
from ..queue import validation
from ..queue.sqs import candidate_queue
from ..storage import aurora
from ..storage.s3 import recording_store
from ..streaming import buffer as wav_buffer
from ..streaming import mixer

log = logging.getLogger("app.voice_platform.api.admin")

router = APIRouter(prefix="/api/platform/admin", tags=["voice-platform-admin"])


def _admin(session: dict = Depends(get_current_session)) -> dict:
    require_admin(session)
    return session


def _422(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


#: Said on the result of every queued upload and on `/status` whenever a stream
#: is configured, because the queue path in this deployment has no other end.
#:
#: `app/voice_platform/queue/worker.py` is a complete SQS consumer with NO
#: TRIGGER: no compose service, no ECS service, no schedule, and the CDK ingest
#: Lambda excludes `worker.py` from its asset outright
#: (`infra/cdk/reep_voice_platform/stack.py`). So a queued candidate is not
#: waiting to be processed, it is waiting to expire. It is one sentence in two
#: places on purpose — the operator who reads `/status` before an upload and
#: the one who reads the upload's own response must not be told two different
#: things about the same dead end.
_NO_QUEUE_CONSUMER_NOTE: Final[str] = (
    "Nothing in this deployment drains the candidate queues, so a queued row never "
    "reaches platform_candidates: 'python -m app.voice_platform.queue.worker' has no "
    "trigger (the CDK ingest Lambda excludes worker.py from its asset). The queue's "
    "four-day retention expires the message, and expiry is not a failed receive, so it "
    "never reaches the dead-letter queue either. Upload without mode=queue — the default "
    "stores — or give the worker a trigger."
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StatusOut(BaseModel):
    engine_ready: bool
    #: Where the PLATFORM_* settings came from: "env", "ssm:<path>" or
    #: "unavailable: <why>" (see app/voice_platform/ssm_config.py).
    config_source: str
    queues: dict[str, bool]
    recordings_bucket: bool
    dynamo_tables: dict[str, bool]
    ffmpeg: bool
    live_buffers: int
    interview_recording_enabled: bool
    notes: list[str]


class SpecializationIn(BaseModel):
    degree_level: str
    key: str | None = None
    label: str = Field(min_length=2, max_length=160)
    persona: str = Field(min_length=2)
    frameworks: list[str] = []
    syllabus: list[str] = []
    nova_voice: str = ""


class SpecializationPatch(BaseModel):
    key: str | None = None
    label: str | None = None
    persona: str | None = None
    frameworks: list[str] | None = None
    syllabus: list[str] | None = None
    nova_voice: str | None = None
    active: bool | None = None


class SpecializationOut(BaseModel):
    id: str
    degree_level: str
    key: str
    label: str
    persona: str
    frameworks: list[str]
    syllabus: list[str]
    nova_voice: str
    active: bool
    question_count: int
    time_limit_seconds: int
    created_at: datetime
    updated_at: datetime


class QuestionIn(BaseModel):
    text: str = Field(min_length=3)
    phase: str = "probing"
    order_index: int | None = None
    rubric: str | None = None


class QuestionPatch(BaseModel):
    text: str | None = None
    phase: str | None = None
    order_index: int | None = None
    rubric: str | None = None
    active: bool | None = None


class QuestionOut(BaseModel):
    id: str
    degree_level: str
    specialization_id: str
    phase: str
    order_index: int
    text: str
    rubric: str | None
    active: bool


class TimeLimitIn(BaseModel):
    degree_level: str
    specialization_id: str | None = None
    max_seconds: int = Field(ge=120, le=3600)
    wrap_up_reserve_seconds: int = Field(default=90, ge=0, le=600)


class TimeLimitOut(BaseModel):
    id: str
    degree_level: str
    specialization_id: str | None
    max_seconds: int
    wrap_up_reserve_seconds: int
    updated_at: datetime


class RecordingPolicyIn(BaseModel):
    enabled: bool | None = None
    retention_days: int | None = None
    mix_format: str | None = None
    keep_channels: str | None = None
    presign_ttl_seconds: int | None = None


class RecordingPolicyOut(BaseModel):
    degree_level: str
    enabled: bool
    retention_days: int
    mix_format: str
    keep_channels: str
    presign_ttl_seconds: int
    updated_at: datetime | None
    #: enabled AND the process-wide INTERVIEW_RECORDING_ENABLED — a policy
    #: alone records nothing, and the screen must not say otherwise.
    effective: bool
    note: str


class CandidateIn(BaseModel):
    external_id: str
    name: str
    degree_level: str
    specialization: str
    email: str | None = None
    programme: str | None = None


class CandidatePatch(BaseModel):
    name: str | None = None
    email: str | None = None
    degree_level: str | None = None
    specialization_key: str | None = None
    programme: str | None = None
    status: str | None = None


class CandidateOut(BaseModel):
    id: str
    degree_level: str
    external_id: str
    name: str
    email: str | None
    specialization_key: str | None
    programme: str | None
    status: str
    source: str
    source_ref: str | None
    user_id: str | None
    created_at: datetime
    updated_at: datetime


class BulkResult(BaseModel):
    filename: str
    rows: int
    accepted: int
    rejected: int
    #: "queued" = pushed onto the SQS streams (the Lambda path, from the API);
    #: "stored" = written straight into Postgres. "stored" is what an upload
    #: from the dashboard does now — see `bulk_candidates` for why that flipped.
    mode: str
    pushed: dict[str, int]
    stored: int
    rejects: list[dict[str, Any]]
    #: What is true about the RUN rather than about a row — `StatusOut.notes`
    #: and `CloseOut.notes`' channel, reused rather than reinvented. `rejects`
    #: could not carry this: a queued candidate was not rejected, it was
    #: accepted and handed to a stream nobody reads, which is a different fact
    #: and belongs in a different field.
    notes: list[str]


class LinkIn(BaseModel):
    email: str


class CallOut(BaseModel):
    id: str
    degree_level: str
    user_id: str
    candidate_id: str | None
    interview_session_id: str | None
    specialization_key: str | None
    status: str
    time_limit_seconds: int
    close_code: int | None
    close_reason: str | None
    turns: int
    started_at: datetime
    ended_at: datetime | None
    recording_s3_key: str | None
    recording_bytes: int | None
    recording_duration_ms: int | None
    recording_truncated: bool
    recording_meta: dict[str, Any]
    dynamo_synced: bool


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------


def _spec_out(db: Session, row: PlatformSpecialization) -> SpecializationOut:
    count = db.scalar(
        select(func.count()).select_from(PlatformQuestion).where(
            PlatformQuestion.specialization_id == row.id, PlatformQuestion.active.is_(True)
        )
    )
    max_seconds, _ = aurora.effective_time_limit(db, row.degree_level, row.id)
    return SpecializationOut(
        id=row.id,
        degree_level=row.degree_level,
        key=row.key,
        label=row.label,
        persona=row.persona,
        frameworks=list(row.frameworks or []),
        syllabus=list(row.syllabus or []),
        nova_voice=row.nova_voice,
        active=row.active,
        question_count=int(count or 0),
        time_limit_seconds=max_seconds,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _question_out(row: PlatformQuestion) -> QuestionOut:
    return QuestionOut(
        id=row.id,
        degree_level=row.degree_level,
        specialization_id=row.specialization_id,
        phase=row.phase,
        order_index=row.order_index,
        text=row.text,
        rubric=row.rubric,
        active=row.active,
    )


def _limit_out(row: PlatformTimeLimit) -> TimeLimitOut:
    return TimeLimitOut(
        id=row.id,
        degree_level=row.degree_level,
        specialization_id=row.specialization_id,
        max_seconds=row.max_seconds,
        wrap_up_reserve_seconds=row.wrap_up_reserve_seconds,
        updated_at=row.updated_at,
    )


def _policy_out(degree: str, row: PlatformRecordingPolicy | None) -> RecordingPolicyOut:
    enabled = bool(row.enabled) if row else False
    effective = enabled and settings.interview_recording_enabled
    if not enabled:
        note = "Recording is off for this degree level."
    elif not settings.interview_recording_enabled:
        note = (
            "The policy is on but INTERVIEW_RECORDING_ENABLED is false on this "
            "server, so nothing is captured."
        )
    else:
        note = (
            "Recording for candidates who hold a live store-audio consent grant. "
            + ("Uploads go to S3." if settings.platform_recordings_bucket.strip()
               else "No PLATFORM_RECORDINGS_BUCKET: files stay on the local audio volume.")
        )
    return RecordingPolicyOut(
        degree_level=degree,
        enabled=enabled,
        retention_days=row.retention_days if row else 180,
        mix_format=row.mix_format if row else "wav",
        keep_channels=row.keep_channels if row else "dual",
        presign_ttl_seconds=row.presign_ttl_seconds if row else settings.platform_presign_ttl_seconds,
        updated_at=row.updated_at if row else None,
        effective=effective,
        note=note,
    )


def _candidate_out(row: PlatformCandidate) -> CandidateOut:
    return CandidateOut(
        id=row.id,
        degree_level=row.degree_level,
        external_id=row.external_id,
        name=row.name,
        email=row.email,
        specialization_key=row.specialization_key,
        programme=row.programme,
        status=row.status,
        source=row.source,
        source_ref=row.source_ref,
        user_id=row.user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def call_out(row: PlatformCallSession) -> CallOut:
    return CallOut(
        id=row.id,
        degree_level=row.degree_level,
        user_id=row.user_id,
        candidate_id=row.candidate_id,
        interview_session_id=row.interview_session_id,
        specialization_key=row.specialization_key,
        status=row.status,
        time_limit_seconds=row.time_limit_seconds,
        close_code=row.close_code,
        close_reason=row.close_reason,
        turns=row.turns,
        started_at=row.started_at,
        ended_at=row.ended_at,
        recording_s3_key=row.recording_s3_key,
        recording_bytes=row.recording_bytes,
        recording_duration_ms=row.recording_duration_ms,
        recording_truncated=row.recording_truncated,
        recording_meta=dict(row.recording_meta or {}),
        dynamo_synced=row.dynamo_synced,
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusOut)
def platform_status(_: dict = Depends(_admin)) -> StatusOut:
    notes: list[str] = []
    if not engine.engine_ready():
        notes.append("The Nova Sonic engine is not ready (INTERVIEW_ENGINE/NOVA_SONIC_REGION); the media bridge closes 4001.")
    queues = {d: bool(settings.platform_queue_url(d)) for d in DEGREE_LEVELS}
    if not any(queues.values()):
        notes.append("No SQS queue configured; a bulk upload asking for mode=queue is refused with 503.")
    else:
        # A configured stream reads on this screen as a working projection and
        # it is only half of one — the ingest Lambda fills it and nothing empties
        # it. This is the screen an operator checks BEFORE uploading four hundred
        # candidates, so it is the earliest place the dead end can be said.
        notes.append(_NO_QUEUE_CONSUMER_NOTE)
    if not settings.platform_recordings_bucket.strip():
        notes.append("No PLATFORM_RECORDINGS_BUCKET; recordings stay on the local audio volume.")
    dynamo = {d: bool(settings.platform_dynamo_table(d)) for d in DEGREE_LEVELS}
    if not any(dynamo.values()):
        notes.append("No DynamoDB session tables; realtime session state is in-memory per worker.")
    if not mixer.ffmpeg_available():
        notes.append("ffmpeg is not on this host; an 'mp3' recording policy produces WAV.")
    return StatusOut(
        engine_ready=engine.engine_ready(),
        config_source=ssm_config.source() or "env",
        queues=queues,
        recordings_bucket=bool(settings.platform_recordings_bucket.strip()),
        dynamo_tables=dynamo,
        ffmpeg=mixer.ffmpeg_available(),
        live_buffers=wav_buffer.live_count(),
        interview_recording_enabled=settings.interview_recording_enabled,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Specializations + questions
# ---------------------------------------------------------------------------


@router.get("/specializations", response_model=list[SpecializationOut])
def list_specializations(
    degree: str | None = Query(default=None),
    include_inactive: bool = Query(default=True),
    _: dict = Depends(_admin),
    db: Session = Depends(get_db),
) -> list[SpecializationOut]:
    try:
        rows = aurora.list_specializations(db, degree, active_only=not include_inactive)
    except ValueError as exc:
        raise _422(exc) from exc
    return [_spec_out(db, r) for r in rows]


@router.post("/specializations", response_model=SpecializationOut, status_code=status.HTTP_201_CREATED)
@handler_span("admin.create_specialization")
def create_specialization(
    body: SpecializationIn, _: dict = Depends(_admin), db: Session = Depends(get_db)
) -> SpecializationOut:
    try:
        row = aurora.create_specialization(
            db,
            degree_level=body.degree_level,
            key=body.key or body.label,
            label=body.label,
            persona=body.persona,
            frameworks=body.frameworks,
            syllabus=body.syllabus,
            nova_voice=body.nova_voice,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _422(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That key already exists for this degree level.") from exc
    return _spec_out(db, row)


def _spec_or_404(db: Session, spec_id: str) -> PlatformSpecialization:
    row = aurora.get_specialization(db, spec_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Specialization not found.")
    return row


@router.get("/specializations/{spec_id}", response_model=SpecializationOut)
def get_specialization(spec_id: str, _: dict = Depends(_admin), db: Session = Depends(get_db)) -> SpecializationOut:
    return _spec_out(db, _spec_or_404(db, spec_id))


@router.patch("/specializations/{spec_id}", response_model=SpecializationOut)
def patch_specialization(
    spec_id: str, body: SpecializationPatch, _: dict = Depends(_admin), db: Session = Depends(get_db)
) -> SpecializationOut:
    row = _spec_or_404(db, spec_id)
    try:
        aurora.update_specialization(db, row, **body.model_dump(exclude_none=True))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _422(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That key already exists for this degree level.") from exc
    return _spec_out(db, row)


@router.delete("/specializations/{spec_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_specialization(spec_id: str, _: dict = Depends(_admin), db: Session = Depends(get_db)) -> None:
    row = _spec_or_404(db, spec_id)
    aurora.delete_specialization(db, row)
    db.commit()


@router.get("/specializations/{spec_id}/questions", response_model=list[QuestionOut])
def list_questions(spec_id: str, _: dict = Depends(_admin), db: Session = Depends(get_db)) -> list[QuestionOut]:
    _spec_or_404(db, spec_id)
    return [_question_out(q) for q in aurora.list_questions(db, spec_id)]


@router.post("/specializations/{spec_id}/questions", response_model=QuestionOut, status_code=status.HTTP_201_CREATED)
@handler_span("admin.create_question")
def create_question(
    spec_id: str, body: QuestionIn, _: dict = Depends(_admin), db: Session = Depends(get_db)
) -> QuestionOut:
    spec = _spec_or_404(db, spec_id)
    try:
        row = aurora.create_question(
            db, spec, text=body.text, phase=body.phase, order_index=body.order_index, rubric=body.rubric
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _422(exc) from exc
    return _question_out(row)


def _question_or_404(db: Session, question_id: str) -> PlatformQuestion:
    row = db.get(PlatformQuestion, question_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found.")
    return row


@router.patch("/questions/{question_id}", response_model=QuestionOut)
def patch_question(
    question_id: str, body: QuestionPatch, _: dict = Depends(_admin), db: Session = Depends(get_db)
) -> QuestionOut:
    row = _question_or_404(db, question_id)
    try:
        aurora.update_question(db, row, **body.model_dump(exclude_none=True))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _422(exc) from exc
    return _question_out(row)


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(question_id: str, _: dict = Depends(_admin), db: Session = Depends(get_db)) -> None:
    aurora.delete_question(db, _question_or_404(db, question_id))
    db.commit()


# ---------------------------------------------------------------------------
# Time limits
# ---------------------------------------------------------------------------


@router.get("/time-limits", response_model=list[TimeLimitOut])
def list_time_limits(
    degree: str | None = Query(default=None), _: dict = Depends(_admin), db: Session = Depends(get_db)
) -> list[TimeLimitOut]:
    try:
        return [_limit_out(r) for r in aurora.list_time_limits(db, degree)]
    except ValueError as exc:
        raise _422(exc) from exc


@router.put("/time-limits", response_model=TimeLimitOut)
def put_time_limit(body: TimeLimitIn, _: dict = Depends(_admin), db: Session = Depends(get_db)) -> TimeLimitOut:
    if body.specialization_id is not None:
        spec = _spec_or_404(db, body.specialization_id)
        if spec.degree_level != aurora.coerce_degree(body.degree_level):
            raise _422(ValueError("that specialization belongs to the other degree level"))
    try:
        row = aurora.upsert_time_limit(
            db,
            degree_level=body.degree_level,
            specialization_id=body.specialization_id,
            max_seconds=body.max_seconds,
            wrap_up_reserve_seconds=body.wrap_up_reserve_seconds,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _422(exc) from exc
    return _limit_out(row)


@router.delete("/time-limits/{limit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_time_limit(limit_id: str, _: dict = Depends(_admin), db: Session = Depends(get_db)) -> None:
    row = db.get(PlatformTimeLimit, limit_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Time limit not found.")
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# Recording policies
# ---------------------------------------------------------------------------


@router.get("/recording-policies", response_model=list[RecordingPolicyOut])
def list_recording_policies(_: dict = Depends(_admin), db: Session = Depends(get_db)) -> list[RecordingPolicyOut]:
    return [_policy_out(d, aurora.get_recording_policy(db, d)) for d in DEGREE_LEVELS]


@router.put("/recording-policies/{degree}", response_model=RecordingPolicyOut)
@handler_span("admin.put_recording_policy")
def put_recording_policy(
    degree: str, body: RecordingPolicyIn, session: dict = Depends(_admin), db: Session = Depends(get_db)
) -> RecordingPolicyOut:
    try:
        row = aurora.upsert_recording_policy(
            db, degree_level=degree, updated_by=session.get("userId"), **body.model_dump(exclude_none=True)
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _422(exc) from exc
    return _policy_out(row.degree_level, row)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(
    degree: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    _: dict = Depends(_admin),
    db: Session = Depends(get_db),
) -> list[CandidateOut]:
    try:
        rows = aurora.list_candidates(db, degree_level=degree, status=status_filter, query=q, limit=limit)
    except ValueError as exc:
        raise _422(exc) from exc
    return [_candidate_out(r) for r in rows]


@router.post("/candidates", response_model=CandidateOut, status_code=status.HTTP_201_CREATED)
def create_candidate(body: CandidateIn, _: dict = Depends(_admin), db: Session = Depends(get_db)) -> CandidateOut:
    try:
        candidate = validation.validate_candidate(
            body.model_dump(), allowed_specializations=aurora.specialization_keys_by_degree(db)
        )
        row, _created = aurora.upsert_candidate(db, candidate, source="admin", status="validated")
        db.commit()
    except validation.CandidateValidationError as exc:
        db.rollback()
        raise _422(exc) from exc
    return _candidate_out(row)


def _candidate_or_404(db: Session, candidate_id: str) -> PlatformCandidate:
    row = aurora.get_candidate(db, candidate_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found.")
    return row


@router.patch("/candidates/{candidate_id}", response_model=CandidateOut)
def patch_candidate(
    candidate_id: str, body: CandidatePatch, _: dict = Depends(_admin), db: Session = Depends(get_db)
) -> CandidateOut:
    row = _candidate_or_404(db, candidate_id)
    try:
        aurora.update_candidate(db, row, **body.model_dump(exclude_none=True))
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _422(exc) from exc
    return _candidate_out(row)


@router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_candidate(candidate_id: str, _: dict = Depends(_admin), db: Session = Depends(get_db)) -> None:
    aurora.delete_candidate(db, _candidate_or_404(db, candidate_id))
    db.commit()


@router.post("/candidates/{candidate_id}/link", response_model=CandidateOut)
def link_candidate(
    candidate_id: str, body: LinkIn, _: dict = Depends(_admin), db: Session = Depends(get_db)
) -> CandidateOut:
    """Attach a roster login (a `users` row — the Google allowlist) to the
    candidate, so they interview under their own session."""
    row = _candidate_or_404(db, candidate_id)
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No roster login with that email. Grant access first.")
    aurora.link_candidate_user(db, row, user)
    db.commit()
    return _candidate_out(row)


@router.post("/candidates/bulk", response_model=BulkResult)
@handler_span("admin.bulk_candidates")
async def bulk_candidates(
    file: UploadFile = File(...),
    mode: str = Query(default="store", pattern="^(auto|queue|store)$"),
    _: dict = Depends(_admin),
    db: Session = Depends(get_db),
) -> BulkResult:
    """Upload a CSV/JSON of candidates from the dashboard.

    `store` (the default) writes the accepted rows into `platform_candidates`
    now. `queue` pushes them onto the SQS stream for their degree level and is
    503 when none is configured. `auto` is `queue` where a stream exists and
    `store` where none does. The response says which in `mode`, and the queue
    path additionally says what a queued row's fate actually is, in `notes`.

    THE DEFAULT WAS `auto` AND FLIPPING IT IS THE WHOLE FIX. On a deployed
    stack a stream is always configured, so `auto` always meant `queue`, and
    nothing in this repository drains those streams — `queue/worker.py` has no
    trigger anywhere and the CDK ingest Lambda excludes it from its asset. The
    failure that produced was the silent kind this subsystem is otherwise
    careful about: the dashboard uploaded four hundred candidates, got `200
    {"mode": "queued", "accepted": 400}` — which reads as success in every
    sense the operator has available — and `platform_candidates` stayed empty
    forever. Nothing on any screen said so, and nothing ever would have. The
    queue's retention is FOUR DAYS and its DLQ is keyed on `max_receive_count`,
    so a message nobody ever RECEIVES is expired rather than failed: it is
    deleted by SQS on day four without ever reaching the dead-letter queue,
    which is the one place `docs/voice-platform.md` tells an operator to look
    for a candidate that did not arrive. The office would have been hunting an
    empty DLQ for rows that were never going to be in it.

    So the default is the projection that is actually wired end to end, which
    is this subsystem's stated rule — every AWS projection is optional and
    honest, nothing pretends. `queue` is untouched for the operator who is
    running a worker and asks for it by name, and `auto` survives as an
    explicit opt-in that still prefers the stream; both are now a choice
    somebody typed rather than a default nobody saw. None of this builds or
    schedules the worker: giving it a trigger, or deleting it and the queues
    with it, is an infrastructure decision and belongs in `infra/cdk/`.
    """
    payload = await file.read()
    if len(payload) > 10 * 1024 * 1024:
        raise _422(ValueError("upload is larger than 10 MB"))
    try:
        rows = validation.parse_bulk(payload, file.filename or "upload.csv")
    except (ValueError, UnicodeDecodeError) as exc:
        raise _422(exc) from exc
    accepted, rejects = validation.partition(rows, allowed_specializations=aurora.specialization_keys_by_degree(db))
    queue = candidate_queue()
    use_queue = mode == "queue" or (mode == "auto" and queue is not None)
    if mode == "queue" and queue is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No SQS queue is configured (PLATFORM_UG_QUEUE_URL / PLATFORM_PG_QUEUE_URL).")
    pushed: dict[str, int] = {}
    stored = 0
    notes: list[str] = []
    source_ref = f"admin-upload:{file.filename}"
    if use_queue and queue is not None:
        # Attached to the PATH and not to the push count, and on purpose. A run
        # that queued nothing because every row was rejected still went down a
        # road with no other end, and the operator is about to fix those rows
        # and press the button again — telling them now is what stops the
        # second attempt from succeeding into the same silence.
        notes.append(_NO_QUEUE_CONSUMER_NOTE)
        by_degree: dict[str, list[dict[str, Any]]] = {}
        for candidate in accepted:
            by_degree.setdefault(candidate.degree_level, []).append(
                validation.queue_message(candidate, source="bulk_upload", source_ref=source_ref)
            )
        for degree, messages in by_degree.items():
            if not queue.configured(degree):
                for m in messages:
                    rejects.append({"row": None, "field": "degree_level", "error": f"no queue configured for the {degree} stream"})
                continue
            sent, failed = queue.push_many(degree, messages)
            pushed[degree] = len(sent)
            for bad in failed:
                rejects.append({"row": None, "field": "queue", "error": bad.get("Message", "SQS refused the message")})
    else:
        for candidate in accepted:
            aurora.upsert_candidate(db, candidate, source="bulk_upload", source_ref=source_ref, status="validated")
            stored += 1
        db.commit()
    return BulkResult(
        filename=file.filename or "",
        rows=len(rows),
        accepted=len(accepted),
        rejected=len(rejects),
        mode="queued" if use_queue else "stored",
        pushed=pushed,
        stored=stored,
        notes=notes,
        rejects=rejects[:200],
    )


# ---------------------------------------------------------------------------
# Calls (admin view; the owner/mentor view lives in api/calls.py)
# ---------------------------------------------------------------------------


@router.get("/calls", response_model=list[CallOut])
def list_calls(
    degree: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    _: dict = Depends(_admin),
    db: Session = Depends(get_db),
) -> list[CallOut]:
    try:
        return [call_out(r) for r in aurora.list_call_sessions(db, degree_level=degree, limit=limit)]
    except ValueError as exc:
        raise _422(exc) from exc


__all__ = ["router", "call_out", "CallOut", "recording_store"]
