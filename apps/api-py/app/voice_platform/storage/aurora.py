"""Aurora PostgreSQL: the repository over app/models/voice_platform.py.

Every function takes an open SQLAlchemy `Session` and does not commit — the
caller (a request handler, the drain worker, the media bridge's thread hops)
owns the transaction, exactly as the rest of the API does. On AWS the database
is Aurora PostgreSQL Serverless v2; locally it is the docker Postgres. The SQL
is the same, which is the point of putting the schema in SQLAlchemy rather than
in a Data API client.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import settings
from ...models.user import User
from ...models.voice_platform import (
    CANDIDATE_STATUSES,
    DEGREE_LEVELS,
    KEEP_CHANNELS,
    MIX_FORMATS,
    QUESTION_PHASES,
    PlatformCallSession,
    PlatformCandidate,
    PlatformQuestion,
    PlatformRecordingPolicy,
    PlatformSpecialization,
    PlatformTimeLimit,
)
from ..queue.validation import Candidate, normalize_degree, specialization_key

#: Where the arc phases fall, for ordering a question bank.
_PHASE_ORDER = {phase: i for i, phase in enumerate(QUESTION_PHASES)}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def coerce_degree(value: str) -> str:
    """'ug' / 'Undergraduate' → 'UG'; raises ValueError with the sentence the
    API returns as a 422."""
    return normalize_degree(value)


# ---------------------------------------------------------------------------
# Specializations + questions
# ---------------------------------------------------------------------------


def list_specializations(
    db: Session, degree_level: str | None = None, *, active_only: bool = False
) -> list[PlatformSpecialization]:
    stmt = select(PlatformSpecialization).order_by(
        PlatformSpecialization.degree_level, PlatformSpecialization.label
    )
    if degree_level:
        stmt = stmt.where(PlatformSpecialization.degree_level == coerce_degree(degree_level))
    if active_only:
        stmt = stmt.where(PlatformSpecialization.active.is_(True))
    return list(db.scalars(stmt))


def get_specialization(db: Session, spec_id: str) -> PlatformSpecialization | None:
    return db.get(PlatformSpecialization, spec_id)


def find_specialization(db: Session, degree_level: str, key: str) -> PlatformSpecialization | None:
    return db.scalar(
        select(PlatformSpecialization).where(
            PlatformSpecialization.degree_level == coerce_degree(degree_level),
            PlatformSpecialization.key == specialization_key(key),
        )
    )


def specialization_keys_by_degree(db: Session) -> dict[str, set[str]]:
    """{degree: {keys}} for the bulk validator's `allowed_specializations`."""
    out: dict[str, set[str]] = {d: set() for d in DEGREE_LEVELS}
    for spec in list_specializations(db, active_only=True):
        out.setdefault(spec.degree_level, set()).add(spec.key)
    return out


def create_specialization(
    db: Session,
    *,
    degree_level: str,
    key: str,
    label: str,
    persona: str,
    frameworks: Iterable[str] = (),
    syllabus: Iterable[str] = (),
    nova_voice: str = "",
) -> PlatformSpecialization:
    row = PlatformSpecialization(
        degree_level=coerce_degree(degree_level),
        key=specialization_key(key) or specialization_key(label),
        label=label.strip(),
        persona=persona.strip(),
        frameworks=[str(f).strip() for f in frameworks if str(f).strip()],
        syllabus=[str(s).strip() for s in syllabus if str(s).strip()],
        nova_voice=nova_voice.strip().lower(),
    )
    if not row.key:
        raise ValueError("a specialization needs a key or a label to derive one from")
    db.add(row)
    db.flush()
    return row


def update_specialization(db: Session, row: PlatformSpecialization, **fields: Any) -> PlatformSpecialization:
    for name, value in fields.items():
        if value is None:
            continue
        if name == "key":
            value = specialization_key(value)
        elif name in ("frameworks", "syllabus"):
            value = [str(v).strip() for v in value if str(v).strip()]
        elif name == "nova_voice":
            value = str(value).strip().lower()
        elif name in ("label", "persona"):
            value = str(value).strip()
        setattr(row, name, value)
    row.updated_at = _now()
    db.flush()
    return row


def delete_specialization(db: Session, row: PlatformSpecialization) -> None:
    db.delete(row)
    db.flush()


def list_questions(db: Session, spec_id: str, *, active_only: bool = False) -> list[PlatformQuestion]:
    stmt = (
        select(PlatformQuestion)
        .where(PlatformQuestion.specialization_id == spec_id)
        .order_by(PlatformQuestion.order_index, PlatformQuestion.created_at)
    )
    if active_only:
        stmt = stmt.where(PlatformQuestion.active.is_(True))
    rows = list(db.scalars(stmt))
    rows.sort(key=lambda q: (_PHASE_ORDER.get(q.phase, 99), q.order_index, q.created_at))
    return rows


def create_question(
    db: Session,
    spec: PlatformSpecialization,
    *,
    text: str,
    phase: str = "probing",
    order_index: int | None = None,
    rubric: str | None = None,
) -> PlatformQuestion:
    phase = phase.strip().lower()
    if phase not in QUESTION_PHASES:
        raise ValueError(f"phase must be one of {', '.join(QUESTION_PHASES)}")
    if order_index is None:
        current = db.scalar(
            select(func.max(PlatformQuestion.order_index)).where(
                PlatformQuestion.specialization_id == spec.id
            )
        )
        order_index = (current if current is not None else -1) + 1
    row = PlatformQuestion(
        degree_level=spec.degree_level,
        specialization_id=spec.id,
        phase=phase,
        order_index=int(order_index),
        text=text.strip(),
        rubric=(rubric or "").strip() or None,
    )
    if not row.text:
        raise ValueError("a question needs text")
    db.add(row)
    db.flush()
    return row


def update_question(db: Session, row: PlatformQuestion, **fields: Any) -> PlatformQuestion:
    for name, value in fields.items():
        if value is None:
            continue
        if name == "phase":
            value = str(value).strip().lower()
            if value not in QUESTION_PHASES:
                raise ValueError(f"phase must be one of {', '.join(QUESTION_PHASES)}")
        elif name == "text":
            value = str(value).strip()
            if not value:
                raise ValueError("a question needs text")
        elif name == "rubric":
            value = str(value).strip() or None
        setattr(row, name, value)
    row.updated_at = _now()
    db.flush()
    return row


def delete_question(db: Session, row: PlatformQuestion) -> None:
    db.delete(row)
    db.flush()


# ---------------------------------------------------------------------------
# Time limits
# ---------------------------------------------------------------------------


def list_time_limits(db: Session, degree_level: str | None = None) -> list[PlatformTimeLimit]:
    stmt = select(PlatformTimeLimit).order_by(
        PlatformTimeLimit.degree_level, PlatformTimeLimit.specialization_id.nulls_first()
    )
    if degree_level:
        stmt = stmt.where(PlatformTimeLimit.degree_level == coerce_degree(degree_level))
    return list(db.scalars(stmt))


def upsert_time_limit(
    db: Session,
    *,
    degree_level: str,
    specialization_id: str | None,
    max_seconds: int,
    wrap_up_reserve_seconds: int = 90,
) -> PlatformTimeLimit:
    if int(max_seconds) < 120:
        raise ValueError("a call needs at least 120 seconds to reach a verdict")
    if not 0 <= int(wrap_up_reserve_seconds) < int(max_seconds):
        raise ValueError("wrap_up_reserve_seconds must be shorter than the limit")
    degree = coerce_degree(degree_level)
    stmt = select(PlatformTimeLimit).where(PlatformTimeLimit.degree_level == degree)
    stmt = (
        stmt.where(PlatformTimeLimit.specialization_id.is_(None))
        if specialization_id is None
        else stmt.where(PlatformTimeLimit.specialization_id == specialization_id)
    )
    row = db.scalar(stmt)
    if row is None:
        row = PlatformTimeLimit(degree_level=degree, specialization_id=specialization_id)
        db.add(row)
    row.max_seconds = int(max_seconds)
    row.wrap_up_reserve_seconds = int(wrap_up_reserve_seconds)
    row.updated_at = _now()
    db.flush()
    return row


def effective_time_limit(
    db: Session, degree_level: str, specialization_id: str | None
) -> tuple[int, int]:
    """(max_seconds, wrap_up_reserve) — the specialization's row, else the
    degree default, else the configured platform default."""
    degree = coerce_degree(degree_level)
    if specialization_id is not None:
        row = db.scalar(
            select(PlatformTimeLimit).where(
                PlatformTimeLimit.degree_level == degree,
                PlatformTimeLimit.specialization_id == specialization_id,
            )
        )
        if row is not None:
            return row.max_seconds, row.wrap_up_reserve_seconds
    row = db.scalar(
        select(PlatformTimeLimit).where(
            PlatformTimeLimit.degree_level == degree,
            PlatformTimeLimit.specialization_id.is_(None),
        )
    )
    if row is not None:
        return row.max_seconds, row.wrap_up_reserve_seconds
    return int(settings.platform_default_time_limit_seconds), 90


# ---------------------------------------------------------------------------
# Recording policies
# ---------------------------------------------------------------------------


def get_recording_policy(db: Session, degree_level: str) -> PlatformRecordingPolicy | None:
    return db.scalar(
        select(PlatformRecordingPolicy).where(
            PlatformRecordingPolicy.degree_level == coerce_degree(degree_level)
        )
    )


def upsert_recording_policy(
    db: Session,
    *,
    degree_level: str,
    updated_by: str | None,
    enabled: bool | None = None,
    retention_days: int | None = None,
    mix_format: str | None = None,
    keep_channels: str | None = None,
    presign_ttl_seconds: int | None = None,
) -> PlatformRecordingPolicy:
    degree = coerce_degree(degree_level)
    row = get_recording_policy(db, degree)
    if row is None:
        row = PlatformRecordingPolicy(degree_level=degree)
        db.add(row)
    if enabled is not None:
        row.enabled = bool(enabled)
    if retention_days is not None:
        if not 1 <= int(retention_days) <= 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        row.retention_days = int(retention_days)
    if mix_format is not None:
        if mix_format not in MIX_FORMATS:
            raise ValueError(f"mix_format must be one of {', '.join(MIX_FORMATS)}")
        row.mix_format = mix_format
    if keep_channels is not None:
        if keep_channels not in KEEP_CHANNELS:
            raise ValueError(f"keep_channels must be one of {', '.join(KEEP_CHANNELS)}")
        row.keep_channels = keep_channels
    if presign_ttl_seconds is not None:
        if not 60 <= int(presign_ttl_seconds) <= 7 * 24 * 3600:
            raise ValueError("presign_ttl_seconds must be between 60 and 604800")
        row.presign_ttl_seconds = int(presign_ttl_seconds)
    row.updated_by = updated_by
    row.updated_at = _now()
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def list_candidates(
    db: Session,
    *,
    degree_level: str | None = None,
    status: str | None = None,
    query: str | None = None,
    limit: int = 200,
) -> list[PlatformCandidate]:
    stmt = select(PlatformCandidate).order_by(PlatformCandidate.created_at.desc()).limit(
        max(1, min(1000, int(limit)))
    )
    if degree_level:
        stmt = stmt.where(PlatformCandidate.degree_level == coerce_degree(degree_level))
    if status:
        stmt = stmt.where(PlatformCandidate.status == status)
    if query:
        needle = f"%{query.strip().lower()}%"
        stmt = stmt.where(
            func.lower(PlatformCandidate.name).like(needle)
            | func.lower(PlatformCandidate.external_id).like(needle)
            | func.lower(func.coalesce(PlatformCandidate.email, "")).like(needle)
        )
    return list(db.scalars(stmt))


def get_candidate(db: Session, candidate_id: str) -> PlatformCandidate | None:
    return db.get(PlatformCandidate, candidate_id)


def candidate_for_user(db: Session, user_id: str) -> PlatformCandidate | None:
    return db.scalar(
        select(PlatformCandidate)
        .where(PlatformCandidate.user_id == user_id)
        .order_by(PlatformCandidate.created_at.desc())
        .limit(1)
    )


def upsert_candidate(
    db: Session,
    candidate: Candidate,
    *,
    source: str = "bulk_upload",
    source_ref: str | None = None,
    status: str = "validated",
) -> tuple[PlatformCandidate, bool]:
    """Insert or refresh by `external_id`. A re-upload updates name, email,
    specialization and programme; it never demotes a status past `validated`
    (an `invited` or `interviewed` candidate stays that way)."""
    if status not in CANDIDATE_STATUSES:
        raise ValueError(f"status must be one of {', '.join(CANDIDATE_STATUSES)}")
    row = db.scalar(
        select(PlatformCandidate).where(PlatformCandidate.external_id == candidate.external_id)
    )
    created = row is None
    if row is None:
        row = PlatformCandidate(external_id=candidate.external_id, status=status)
        db.add(row)
    elif CANDIDATE_STATUSES.index(row.status) < CANDIDATE_STATUSES.index(status):
        row.status = status
    row.degree_level = candidate.degree_level
    row.name = candidate.name
    row.email = candidate.email
    row.specialization_key = candidate.specialization
    row.programme = candidate.programme
    row.source = source[:16]
    row.source_ref = (source_ref or "")[:512] or None
    row.updated_at = _now()
    # Link to a roster login when the email already matches one — the
    # candidate then interviews under their own session with no admin step.
    if row.user_id is None and candidate.email:
        user = db.scalar(select(User).where(func.lower(User.email) == candidate.email.lower()))
        if user is not None:
            row.user_id = user.id
    db.flush()
    return row, created


def link_candidate_user(db: Session, row: PlatformCandidate, user: User) -> PlatformCandidate:
    row.user_id = user.id
    if CANDIDATE_STATUSES.index(row.status) < CANDIDATE_STATUSES.index("invited"):
        row.status = "invited"
    row.updated_at = _now()
    db.flush()
    return row


def update_candidate(db: Session, row: PlatformCandidate, **fields: Any) -> PlatformCandidate:
    for name, value in fields.items():
        if value is None:
            continue
        if name == "degree_level":
            value = coerce_degree(value)
        elif name == "specialization_key":
            value = specialization_key(value)
        elif name == "status" and value not in CANDIDATE_STATUSES:
            raise ValueError(f"status must be one of {', '.join(CANDIDATE_STATUSES)}")
        elif name == "email":
            value = str(value).strip().lower() or None
        setattr(row, name, value)
    row.updated_at = _now()
    db.flush()
    return row


def delete_candidate(db: Session, row: PlatformCandidate) -> None:
    db.delete(row)
    db.flush()


# ---------------------------------------------------------------------------
# Call sessions
# ---------------------------------------------------------------------------


def create_call_session(
    db: Session,
    *,
    session_id: str,
    degree_level: str,
    user_id: str,
    interview_session_id: str | None,
    specialization_key: str | None,
    time_limit_seconds: int,
    candidate_id: str | None = None,
) -> PlatformCallSession:
    row = PlatformCallSession(
        id=session_id,
        degree_level=coerce_degree(degree_level),
        user_id=user_id,
        candidate_id=candidate_id,
        interview_session_id=interview_session_id,
        specialization_key=specialization_key,
        time_limit_seconds=int(time_limit_seconds),
    )
    db.add(row)
    db.flush()
    return row


def get_call_session(db: Session, session_id: str) -> PlatformCallSession | None:
    return db.get(PlatformCallSession, session_id)


def list_call_sessions(
    db: Session, *, degree_level: str | None = None, user_id: str | None = None, limit: int = 50
) -> list[PlatformCallSession]:
    stmt = select(PlatformCallSession).order_by(PlatformCallSession.started_at.desc()).limit(
        max(1, min(500, int(limit)))
    )
    if degree_level:
        stmt = stmt.where(PlatformCallSession.degree_level == coerce_degree(degree_level))
    if user_id:
        stmt = stmt.where(PlatformCallSession.user_id == user_id)
    return list(db.scalars(stmt))


def touch_call_session(db: Session, session_id: str, *, turns: int | None = None) -> None:
    row = db.get(PlatformCallSession, session_id)
    if row is None:
        return
    row.heartbeat_at = _now()
    if turns is not None:
        row.turns = int(turns)
    db.flush()


def finalize_call_session(
    db: Session, session_id: str, *, code: int, reason: str, status: str, turns: int | None = None
) -> bool:
    """Idempotent by predicate: only a `running` row is moved. Returns whether
    this call was the one that closed it."""
    row = db.get(PlatformCallSession, session_id)
    if row is None or row.status != "running":
        return False
    row.status = status
    row.close_code = int(code)
    row.close_reason = reason[:160]
    row.ended_at = _now()
    if turns is not None:
        row.turns = int(turns)
    db.flush()
    return True


def attach_recording(
    db: Session,
    session_id: str,
    *,
    s3_key: str | None,
    size_bytes: int,
    duration_ms: int,
    truncated: bool,
    meta: dict[str, Any],
) -> None:
    row = db.get(PlatformCallSession, session_id)
    if row is None:
        return
    row.recording_s3_key = s3_key
    row.recording_bytes = int(size_bytes)
    row.recording_duration_ms = int(duration_ms)
    row.recording_truncated = bool(truncated)
    row.recording_meta = dict(row.recording_meta or {}, **meta)
    db.flush()


def mark_synced(db: Session, session_id: str, *, dynamo: bool | None = None) -> None:
    row = db.get(PlatformCallSession, session_id)
    if row is None:
        return
    if dynamo is not None:
        row.dynamo_synced = bool(dynamo)
    db.flush()
