"""Additive Phase 4 redesign foundation.

Revision ID: c8f4d9e2a101
Revises: b4e8d21f9c57

No legacy table is altered or dropped. The migration adds the new bounded
contracts first; later expand/contract migrations can backfill tenant IDs and
retire compatibility routes after measured adoption.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "c8f4d9e2a101"
down_revision: Union[str, Sequence[str], None] = "b4e8d21f9c57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


MEMBERSHIP_ROLE = _enum("redesign_membership_role", "STUDENT", "MENTOR", "DIRECTOR", "ADMIN")
DELIVERY_STATUS = _enum("redesign_delivery_status", "PENDING", "DELIVERED", "FAILED")
JOB_STATUS = _enum("redesign_job_status", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")
NOTEBOOK_ENTRY_TYPE = _enum("redesign_notebook_entry_type", "MEETING", "ACADEMIC_REVIEW", "WELLBEING", "PLACEMENT", "ATTENDANCE", "REFERRAL", "CUSTOM")
NOTEBOOK_VISIBILITY = _enum("redesign_notebook_visibility", "PRIVATE_STAFF", "STUDENT_VISIBLE")
NOTEBOOK_STATUS = _enum("redesign_notebook_status", "DRAFT", "PUBLISHED", "ARCHIVED")
ACTION_STATUS = _enum("redesign_action_status", "OPEN", "IN_PROGRESS", "DONE", "CANCELLED")
ACTION_PRIORITY = _enum("redesign_action_priority", "LOW", "NORMAL", "HIGH", "URGENT")
EMBEDDING_STATUS = _enum("redesign_embedding_status", "PENDING", "READY", "STALE", "FAILED")


def _json_default() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for enum_type in (
        MEMBERSHIP_ROLE, DELIVERY_STATUS, JOB_STATUS, NOTEBOOK_ENTRY_TYPE,
        NOTEBOOK_VISIBILITY, NOTEBOOK_STATUS, ACTION_STATUS, ACTION_PRIORITY,
        EMBEDDING_STATUS,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "redesign_tenants",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slug", name="uq_redesign_tenant_slug"),
    )
    op.create_table(
        "redesign_tenant_memberships",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("redesign_tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", MEMBERSHIP_ROLE, nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_redesign_membership_tenant_user"),
    )
    op.create_index("ix_redesign_membership_user_role", "redesign_tenant_memberships", ["user_id", "role"])

    op.create_table(
        "redesign_audit_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("redesign_tenants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_type", sa.String(30), nullable=False, server_default="USER"),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("correlation_id", sa.String(100), nullable=True),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("before_json", postgresql.JSONB, nullable=True),
        sa.Column("after_json", postgresql.JSONB, nullable=True),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False, server_default=_json_default()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_redesign_audit_tenant_time", "redesign_audit_events", ["tenant_id", "occurred_at"])
    op.create_index("ix_redesign_audit_entity_time", "redesign_audit_events", ["entity_type", "entity_id", "occurred_at"])

    op.create_table(
        "redesign_outbox_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("redesign_tenants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("aggregate_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=_json_default()),
        sa.Column("status", DELIVERY_STATUS, nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_redesign_outbox_delivery", "redesign_outbox_events", ["status", "available_at"])
    op.create_index("ix_redesign_outbox_aggregate", "redesign_outbox_events", ["aggregate_type", "aggregate_id"])

    op.create_table(
        "redesign_domain_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("redesign_tenants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_type", sa.String(100), nullable=False),
        sa.Column("subject_type", sa.String(100), nullable=False),
        sa.Column("subject_id", sa.String(100), nullable=False),
        sa.Column("status", JOB_STATUS, nullable=False, server_default="QUEUED"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", postgresql.JSONB, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_redesign_job_queue", "redesign_domain_jobs", ["status", "available_at"])

    op.create_table(
        "redesign_api_idempotency_keys",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("principal_id", sa.String(100), nullable=False),
        sa.Column("route", sa.String(200), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_json", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("principal_id", "route", "key", name="uq_redesign_idempotency_principal_route_key"),
    )

    op.create_table(
        "redesign_mentor_notebook_entries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("mentor_id", sa.String(), sa.ForeignKey("mentors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entry_type", NOTEBOOK_ENTRY_TYPE, nullable=False, server_default="MEETING"),
        sa.Column("template_key", sa.String(80), nullable=False, server_default="meeting"),
        sa.Column("template_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("structured_data", postgresql.JSONB, nullable=False, server_default=_json_default()),
        sa.Column("visibility", NOTEBOOK_VISIBILITY, nullable=False, server_default="PRIVATE_STAFF"),
        sa.Column("status", NOTEBOOK_STATUS, nullable=False, server_default="DRAFT"),
        sa.Column("meeting_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("client_request_id", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_redesign_notebook_student_time", "redesign_mentor_notebook_entries", ["student_id", "meeting_at"])
    op.create_index("ix_redesign_notebook_student_visibility", "redesign_mentor_notebook_entries", ["student_id", "visibility", "status"])

    op.create_table(
        "redesign_mentor_notebook_actions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("entry_id", sa.String(), sa.ForeignKey("redesign_mentor_notebook_entries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", ACTION_STATUS, nullable=False, server_default="OPEN"),
        sa.Column("priority", ACTION_PRIORITY, nullable=False, server_default="NORMAL"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_redesign_notebook_action_student_due", "redesign_mentor_notebook_actions", ["student_id", "status", "due_at"])

    op.create_table(
        "redesign_mentor_notebook_entry_revisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("entry_id", sa.String(), sa.ForeignKey("redesign_mentor_notebook_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("author_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB, nullable=False, server_default=_json_default()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("entry_id", "version", name="uq_redesign_notebook_revision"),
    )

    op.create_table(
        "redesign_mentor_notebook_attachments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("entry_id", sa.String(), sa.ForeignKey("redesign_mentor_notebook_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_redesign_notebook_attachment_entry", "redesign_mentor_notebook_attachments", ["entry_id"])

    op.create_table(
        "redesign_knowledge_namespaces",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("redesign_tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("visibility", sa.String(30), nullable=False, server_default="PUBLIC"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_redesign_knowledge_namespace"),
    )
    op.create_table(
        "redesign_knowledge_document_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(100), nullable=False),
        sa.Column("namespace_id", sa.String(), sa.ForeignKey("redesign_knowledge_namespaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("canonical_text_sha256", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(50), nullable=False, server_default="1"),
        sa.Column("chunker_version", sa.String(50), nullable=False, server_default="1"),
        sa.Column("status", NOTEBOOK_STATUS, nullable=False, server_default="DRAFT"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("document_id", "version_no", name="uq_redesign_knowledge_document_version"),
    )
    op.create_table(
        "redesign_knowledge_chunks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("document_version_id", sa.String(), sa.ForeignKey("redesign_knowledge_document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("section_title", sa.String(200), nullable=True),
        sa.Column("anchor", sa.String(200), nullable=True),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False, server_default=_json_default()),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_redesign_knowledge_chunk_ordinal"),
    )
    op.create_index("ix_redesign_knowledge_chunk_version", "redesign_knowledge_chunks", ["document_version_id"])
    op.create_table(
        "redesign_embedding_models",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model_name", sa.String(160), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("distance_metric", sa.String(30), nullable=False, server_default="cosine"),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "model_name", "dimension", name="uq_redesign_embedding_model"),
    )
    op.create_table(
        "redesign_knowledge_chunk_embeddings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("chunk_id", sa.String(), sa.ForeignKey("redesign_knowledge_chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("embedding_model_id", sa.String(), sa.ForeignKey("redesign_embedding_models.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("status", EMBEDDING_STATUS, nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("chunk_id", "embedding_model_id", name="uq_redesign_chunk_embedding_model"),
    )
    op.create_index("ix_redesign_embedding_status_model", "redesign_knowledge_chunk_embeddings", ["embedding_model_id", "status"])


def downgrade() -> None:
    for index, table in (
        ("ix_redesign_embedding_status_model", "redesign_knowledge_chunk_embeddings"),
        ("ix_redesign_knowledge_chunk_version", "redesign_knowledge_chunks"),
        ("ix_redesign_notebook_attachment_entry", "redesign_mentor_notebook_attachments"),
        ("ix_redesign_notebook_action_student_due", "redesign_mentor_notebook_actions"),
        ("ix_redesign_notebook_student_visibility", "redesign_mentor_notebook_entries"),
        ("ix_redesign_notebook_student_time", "redesign_mentor_notebook_entries"),
        ("ix_redesign_job_queue", "redesign_domain_jobs"),
        ("ix_redesign_outbox_aggregate", "redesign_outbox_events"),
        ("ix_redesign_outbox_delivery", "redesign_outbox_events"),
        ("ix_redesign_audit_entity_time", "redesign_audit_events"),
        ("ix_redesign_audit_tenant_time", "redesign_audit_events"),
        ("ix_redesign_membership_user_role", "redesign_tenant_memberships"),
    ):
        op.drop_index(index, table_name=table)
    for table in (
        "redesign_knowledge_chunk_embeddings", "redesign_embedding_models", "redesign_knowledge_chunks",
        "redesign_knowledge_document_versions", "redesign_knowledge_namespaces", "redesign_mentor_notebook_attachments",
        "redesign_mentor_notebook_entry_revisions", "redesign_mentor_notebook_actions", "redesign_mentor_notebook_entries",
        "redesign_api_idempotency_keys", "redesign_domain_jobs", "redesign_outbox_events", "redesign_audit_events",
        "redesign_tenant_memberships", "redesign_tenants",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    for enum_type in (
        EMBEDDING_STATUS, ACTION_PRIORITY, ACTION_STATUS, NOTEBOOK_STATUS,
        NOTEBOOK_VISIBILITY, NOTEBOOK_ENTRY_TYPE, JOB_STATUS, DELIVERY_STATUS,
        MEMBERSHIP_ROLE,
    ):
        enum_type.drop(bind, checkfirst=True)
