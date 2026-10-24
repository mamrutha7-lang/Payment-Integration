"""Additive Phase 4 architecture models.

Legacy tables remain intact. These models provide the durable contracts used by
new mentor notebook, ingestion/vector, and cross-service workflows while the
legacy domain is migrated expand/contract.
"""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class MembershipRole(str, enum.Enum):
    STUDENT = "STUDENT"
    MENTOR = "MENTOR"
    DIRECTOR = "DIRECTOR"
    ADMIN = "ADMIN"


class RecordStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class NotebookVisibility(str, enum.Enum):
    PRIVATE_STAFF = "PRIVATE_STAFF"
    STUDENT_VISIBLE = "STUDENT_VISIBLE"


class NotebookEntryType(str, enum.Enum):
    MEETING = "MEETING"
    ACADEMIC_REVIEW = "ACADEMIC_REVIEW"
    WELLBEING = "WELLBEING"
    PLACEMENT = "PLACEMENT"
    ATTENDANCE = "ATTENDANCE"
    REFERRAL = "REFERRAL"
    CUSTOM = "CUSTOM"


class ActionStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class ActionPriority(str, enum.Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EmbeddingStatus(str, enum.Enum):
    PENDING = "PENDING"
    READY = "READY"
    STALE = "STALE"
    FAILED = "FAILED"


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class Tenant(Base):
    __tablename__ = "redesign_tenants"
    __table_args__ = (UniqueConstraint("slug", name="uq_redesign_tenant_slug"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantMembership(Base):
    __tablename__ = "redesign_tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_redesign_membership_tenant_user"),
        Index("ix_redesign_membership_user_role", "user_id", "role"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("redesign_tenants.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[MembershipRole] = mapped_column(Enum(MembershipRole, name="redesign_membership_role"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "redesign_audit_events"
    __table_args__ = (
        Index("ix_redesign_audit_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_redesign_audit_entity_time", "entity_type", "entity_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("redesign_tenants.id", ondelete="SET NULL"), nullable=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False, default="USER", server_default="USER")
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEvent(Base):
    __tablename__ = "redesign_outbox_events"
    __table_args__ = (
        Index("ix_redesign_outbox_delivery", "status", "available_at"),
        Index("ix_redesign_outbox_aggregate", "aggregate_type", "aggregate_id"),
        # Created by d6a4e7f91b22 and undeclared here until 2026-09: `alembic check`
        # reported them as "removed" on every run, which made real drift invisible.
        Index("ix_redesign_outbox_lease", "lease_until"),
        Index("ix_redesign_outbox_route", "routing_key", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("redesign_tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[DeliveryStatus] = mapped_column(Enum(DeliveryStatus, name="redesign_delivery_status"), nullable=False, default=DeliveryStatus.PENDING, server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    routing_key: Mapped[str] = mapped_column(String(80), nullable=False, default="default", server_default="default")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8, server_default="8")
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DomainJob(Base):
    __tablename__ = "redesign_domain_jobs"
    __table_args__ = (
        Index("ix_redesign_job_queue", "status", "available_at"),
        # Created by d6a4e7f91b22; declared here since 2026-09 (see OutboxEvent).
        Index("ix_redesign_job_lease", "lease_until"),
        Index("ix_redesign_job_type_queue", "job_type", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("redesign_tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="redesign_job_status"), nullable=False, default=JobStatus.QUEUED, server_default="QUEUED")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8, server_default="8")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiIdempotencyKey(Base):
    __tablename__ = "redesign_api_idempotency_keys"
    __table_args__ = (UniqueConstraint("principal_id", "route", "key", name="uq_redesign_idempotency_principal_route_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    route: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reservation_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MentorNotebookEntry(Base):
    __tablename__ = "redesign_mentor_notebook_entries"
    __table_args__ = (
        Index("ix_redesign_notebook_student_time", "student_id", "meeting_at"),
        Index("ix_redesign_notebook_student_visibility", "student_id", "visibility", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    author_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    mentor_id: Mapped[str | None] = mapped_column(ForeignKey("mentors.id", ondelete="SET NULL"), nullable=True, index=True)
    entry_type: Mapped[NotebookEntryType] = mapped_column(Enum(NotebookEntryType, name="redesign_notebook_entry_type"), nullable=False, default=NotebookEntryType.MEETING, server_default="MEETING")
    template_key: Mapped[str] = mapped_column(String(80), nullable=False, default="meeting", server_default="meeting")
    template_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    visibility: Mapped[NotebookVisibility] = mapped_column(Enum(NotebookVisibility, name="redesign_notebook_visibility"), nullable=False, default=NotebookVisibility.PRIVATE_STAFF, server_default="PRIVATE_STAFF")
    status: Mapped[RecordStatus] = mapped_column(Enum(RecordStatus, name="redesign_notebook_status"), nullable=False, default=RecordStatus.DRAFT, server_default="DRAFT")
    meeting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    client_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MentorNotebookAction(Base):
    __tablename__ = "redesign_mentor_notebook_actions"
    __table_args__ = (Index("ix_redesign_notebook_action_student_due", "student_id", "status", "due_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    entry_id: Mapped[str | None] = mapped_column(ForeignKey("redesign_mentor_notebook_entries.id", ondelete="SET NULL"), nullable=True, index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ActionStatus] = mapped_column(Enum(ActionStatus, name="redesign_action_status"), nullable=False, default=ActionStatus.OPEN, server_default="OPEN")
    priority: Mapped[ActionPriority] = mapped_column(Enum(ActionPriority, name="redesign_action_priority"), nullable=False, default=ActionPriority.NORMAL, server_default="NORMAL")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MentorNotebookEntryRevision(Base):
    __tablename__ = "redesign_mentor_notebook_entry_revisions"
    __table_args__ = (UniqueConstraint("entry_id", "version", name="uq_redesign_notebook_revision"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    entry_id: Mapped[str] = mapped_column(ForeignKey("redesign_mentor_notebook_entries.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    author_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MentorNotebookAttachment(Base):
    __tablename__ = "redesign_mentor_notebook_attachments"
    __table_args__ = (Index("ix_redesign_notebook_attachment_entry", "entry_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    entry_id: Mapped[str] = mapped_column(ForeignKey("redesign_mentor_notebook_entries.id", ondelete="CASCADE"), nullable=False)
    uploaded_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeNamespace(Base):
    __tablename__ = "redesign_knowledge_namespaces"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_redesign_knowledge_namespace"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("redesign_tenants.id", ondelete="CASCADE"), nullable=True)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    visibility: Mapped[str] = mapped_column(String(30), nullable=False, default="PUBLIC", server_default="PUBLIC")


class KnowledgeDocumentVersion(Base):
    __tablename__ = "redesign_knowledge_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no", name="uq_redesign_knowledge_document_version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(100), nullable=False)
    namespace_id: Mapped[str] = mapped_column(ForeignKey("redesign_knowledge_namespaces.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1", server_default="1")
    chunker_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1", server_default="1")
    status: Mapped[RecordStatus] = mapped_column(Enum(RecordStatus, name="redesign_notebook_status"), nullable=False, default=RecordStatus.DRAFT, server_default="DRAFT")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeChunkV2(Base):
    __tablename__ = "redesign_knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal", name="uq_redesign_knowledge_chunk_ordinal"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("redesign_knowledge_document_versions.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    section_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    anchor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class EmbeddingModel(Base):
    __tablename__ = "redesign_embedding_models"
    __table_args__ = (UniqueConstraint("provider", "model_name", "dimension", name="uq_redesign_embedding_model"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_metric: Mapped[str] = mapped_column(String(30), nullable=False, default="cosine", server_default="cosine")
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeChunkEmbedding(Base):
    __tablename__ = "redesign_knowledge_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "embedding_model_id", name="uq_redesign_chunk_embedding_model"),
        Index("ix_redesign_embedding_status_model", "embedding_model_id", "status"),
        # Created by d6a4e7f91b22; declared here since 2026-09 (see OutboxEvent).
        Index("ix_redesign_embedding_queue", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("redesign_knowledge_chunks.id", ondelete="CASCADE"), nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(ForeignKey("redesign_embedding_models.id", ondelete="RESTRICT"), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[EmbeddingStatus] = mapped_column(Enum(EmbeddingStatus, name="redesign_embedding_status"), nullable=False, default=EmbeddingStatus.PENDING, server_default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
