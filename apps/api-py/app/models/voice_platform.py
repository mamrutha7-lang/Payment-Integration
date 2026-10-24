"""The voice-assistant platform's durable state (see app/voice_platform).

Six tables behind the dual-path (Undergraduate / Postgraduate) interview
platform. They run on the SAME Postgres the rest of REEP uses — on AWS that is
Aurora PostgreSQL Serverless v2 or RDS; the SQL is identical — and they are
**in addition to** the interview record in `models/interview.py`, never instead
of it: a platform call that runs through the media bridge still opens an
`interview_sessions` row, so the student's own `/student/interviews` screen and
rule 2's mentor gate see it exactly like any other mock interview.
`platform_call_sessions.interview_session_id` is the link.

**Every vocabulary column is a plain `String`, not a PG enum**, on the
precedent of `models/interview.py` and for the same reason: `degree_level` is
UG or PG today and a third programme level is a data change, not a `CREATE
TYPE` migration with all three of AGENTS.md's enum gotchas attached. The
vocabulary is written next to each column.

The catalogue here — questions, specializations, time limits — is what the
spec's Admin Dashboard CRUD maintains **per degree level**. It is deliberately
rows and not code, unlike `interview_matrix.SPECIALIZATIONS` (the four fixed
MBA tracks): the platform exists so the placement office can add a BSc AI
question set without a deploy. At socket-open time the rows are compiled into
an `interview_matrix.Specialization` and handed to the same Nova engine, so the
persona rules, the phase machine and the scorecard are identical on both paths.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base

#: The two candidate workflows in the architecture. `UG` = Undergraduate ASG /
#: queue / sessions table, `PG` = Postgraduate. Kept as a tuple, not an enum —
#: see the module docstring.
DEGREE_LEVELS: tuple[str, ...] = ("UG", "PG")

#: `platform_questions.phase` — which arc phase a question belongs to. The
#: values are `interview_matrix.InterviewPhase` minus ENDED (nothing is asked
#: while the socket is closing).
QUESTION_PHASES: tuple[str, ...] = ("opening", "probing", "deep_dive", "wrap_up")

#: `platform_candidates.status`. queued = pushed onto the SQS stream and not
#: yet drained; validated = the worker upserted the row; invited = an admin
#: linked it to a roster login; interviewed = at least one call session closed.
CANDIDATE_STATUSES: tuple[str, ...] = ("queued", "validated", "invited", "interviewed")

#: `platform_call_sessions.status`. Mirrors `interview_sessions.status` so the
#: two rows for one call never disagree on what happened to it.
CALL_STATUSES: tuple[str, ...] = ("running", "completed", "failed", "abandoned")

#: `platform_recording_policies.keep_channels`: which artefacts the mixer
#: uploads. dual = ONE stereo file (candidate left, interviewer right); mixed =
#: one mono sum; both = the stereo file plus the mono sum.
KEEP_CHANNELS: tuple[str, ...] = ("dual", "mixed", "both")

#: `platform_recording_policies.mix_format`. MP3 needs ffmpeg on the host and
#: falls back to WAV, honestly flagged, when it is missing.
MIX_FORMATS: tuple[str, ...] = ("wav", "mp3")


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformSpecialization(Base):
    """One interview track for one degree level — "BSc AI" under UG, "MTech Data
    Science" under PG. Compiled at socket-open into an
    `interview_matrix.Specialization`, which is why the columns mirror that
    dataclass field for field."""

    __tablename__ = "platform_specializations"
    __table_args__ = (
        UniqueConstraint("degree_level", "key", name="uq_platform_spec_degree_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    degree_level: Mapped[str] = mapped_column(String(2), nullable=False)
    #: Lower-case, URL-safe; what the client passes as `?specialization=`.
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    #: A noun phrase the persona sentence can embed ("a pragmatic Head of Data
    #: Science"), same contract as the matrix.
    persona: Mapped[str] = mapped_column(Text, nullable=False)
    frameworks: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    syllabus: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    nova_voice: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )

    questions: Mapped[list["PlatformQuestion"]] = relationship(
        back_populates="specialization",
        cascade="all, delete-orphan",
        order_by="PlatformQuestion.order_index",
    )


class PlatformQuestion(Base):
    """One question in a specialization's bank, tagged with the phase it belongs
    to. The engine is told to work these in, rephrased naturally, in order —
    never to recite them, and never more than one at a time."""

    __tablename__ = "platform_questions"
    __table_args__ = (
        Index("ix_platform_questions_spec_order", "specialization_id", "order_index"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    degree_level: Mapped[str] = mapped_column(String(2), nullable=False)
    specialization_id: Mapped[str] = mapped_column(
        String, ForeignKey("platform_specializations.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(
        String(16), nullable=False, default="probing", server_default="probing"
    )
    order_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: What a strong answer covers. Fed to the scorecard prompt as assessment
    #: guidance; never spoken.
    rubric: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )

    specialization: Mapped[PlatformSpecialization] = relationship(back_populates="questions")


class PlatformTimeLimit(Base):
    """How long a call may run, per degree level and optionally per
    specialization (`specialization_id` NULL = the degree's default). The engine
    still honours Bedrock's 8-minute stream wall — a limit above it is capped
    at open time with a log line, never silently exceeded."""

    __tablename__ = "platform_time_limits"
    __table_args__ = (
        # Postgres treats NULLs as distinct in a unique constraint, so the
        # per-degree default (NULL specialization) is de-duplicated in code —
        # `storage.aurora.upsert_time_limit` — rather than by this constraint.
        UniqueConstraint(
            "degree_level", "specialization_id", name="uq_platform_time_limit_scope"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    degree_level: Mapped[str] = mapped_column(String(2), nullable=False)
    specialization_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("platform_specializations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    max_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Seconds before the cap at which the engine forces the wrap-up, so the
    #: verdict and the scorecard fit inside the limit.
    wrap_up_reserve_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90, server_default=sql_text("90")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )


class PlatformCandidate(Base):
    """A candidate admitted to the platform — from a bulk CSV/JSON upload (the
    S3 → Lambda → SQS path) or an admin form — and, once linked, the roster
    login they interview with."""

    __tablename__ = "platform_candidates"
    __table_args__ = (Index("ix_platform_candidates_degree_status", "degree_level", "status"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    degree_level: Mapped[str] = mapped_column(String(2), nullable=False)
    #: The institution's own identifier — a USN, roll number or application id.
    external_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    specialization_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    programme: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="bulk_upload", server_default="bulk_upload"
    )
    #: The S3 object (or admin request) this row came from — the audit trail
    #: back to the upload.
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    validation_notes: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )


class PlatformRecordingPolicy(Base):
    """What the platform does with a call's audio, per degree level.

    A policy can only NARROW what is recorded, never widen it past consent:
    `enabled` here AND `INTERVIEW_RECORDING_ENABLED` AND the student's live
    `scope_store_audio` grant are all required before a byte is buffered. The
    policy decides format, channel layout and retention — the things an admin
    should own — and nothing about whose voice may be kept.
    """

    __tablename__ = "platform_recording_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    degree_level: Mapped[str] = mapped_column(String(2), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=180, server_default=sql_text("180")
    )
    mix_format: Mapped[str] = mapped_column(
        String(8), nullable=False, default="wav", server_default="wav"
    )
    keep_channels: Mapped[str] = mapped_column(
        String(8), nullable=False, default="dual", server_default="dual"
    )
    #: Presigned-URL lifetime handed to admins and mentors, in seconds.
    presign_ttl_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3600, server_default=sql_text("3600")
    )
    updated_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )


class PlatformCallSession(Base):
    """One call through the media bridge: the durable mirror of the realtime
    metadata the platform also keeps in DynamoDB (`Undergraduate Sessions` /
    `Postgraduate Sessions`).

    Postgres is the SOURCE OF TRUTH and DynamoDB is a projection, flagged by
    `dynamo_synced` — so a deployment without it loses nothing, and one where
    the projection write failed can be re-synced from here rather than
    reconstructed from logs.

    There was a second projection, OpenSearch, removed in 2026-09. It was
    never read: its `search`/`knn` helpers had no callers, and the session-log
    writer passed raw datetimes into `json.dumps`, so every write raised
    TypeError, was swallowed by "a projection never fails the call", and left
    `opensearch_synced` false for the life of the feature.
    """

    __tablename__ = "platform_call_sessions"
    __table_args__ = (
        Index("ix_platform_call_sessions_degree_started", "degree_level", "started_at"),
        Index("ix_platform_call_sessions_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    degree_level: Mapped[str] = mapped_column(String(2), nullable=False)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("platform_candidates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: The ordinary interview record for this call, so the student's screen and
    #: rule 2's gate see it. SET NULL on delete: the platform row survives the
    #: 180-day interview retention sweep only as long as its own policy says.
    interview_session_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("interview_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    specialization_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", server_default="running"
    )
    time_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    close_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sql_text("0"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Where the finished dual-channel file lives. Presigned URLs are DERIVED
    #: from this on read (`recording_s3_url` in the API) and never stored: a
    #: stored URL is a stored expiry.
    recording_s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    recording_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    recording_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    #: Per-channel byte counts and the format actually produced — `mp3` asked
    #: for and `wav` delivered is visible here, not silent.
    recording_meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    dynamo_synced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
