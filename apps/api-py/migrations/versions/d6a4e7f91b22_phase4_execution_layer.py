"""Add durable execution state for Phase 4 workers.

Revision ID: d6a4e7f91b22
Revises: c8f4d9e2a101

This migration is additive except for making a pending embedding vector
nullable. It does not delete or reinterpret legacy data.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6a4e7f91b22"
down_revision: Union[str, Sequence[str], None] = "c8f4d9e2a101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("redesign_outbox_events", sa.Column("event_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("redesign_outbox_events", sa.Column("routing_key", sa.String(80), nullable=False, server_default="default"))
    op.add_column("redesign_outbox_events", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"))
    op.add_column("redesign_outbox_events", sa.Column("lease_owner", sa.String(120), nullable=True))
    op.add_column("redesign_outbox_events", sa.Column("lease_token", sa.String(64), nullable=True))
    op.add_column("redesign_outbox_events", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("redesign_outbox_events", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("redesign_outbox_events", sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("redesign_outbox_events", sa.Column("dead_reason", sa.Text(), nullable=True))
    op.add_column("redesign_outbox_events", sa.Column("published_message_id", sa.String(200), nullable=True))
    op.create_index("ix_redesign_outbox_lease", "redesign_outbox_events", ["lease_until"])
    op.create_index("ix_redesign_outbox_route", "redesign_outbox_events", ["routing_key", "status", "available_at"])

    op.add_column("redesign_domain_jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"))
    op.add_column("redesign_domain_jobs", sa.Column("lease_owner", sa.String(120), nullable=True))
    op.add_column("redesign_domain_jobs", sa.Column("lease_token", sa.String(64), nullable=True))
    op.add_column("redesign_domain_jobs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("redesign_domain_jobs", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("redesign_domain_jobs", sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("redesign_domain_jobs", sa.Column("dead_reason", sa.Text(), nullable=True))
    op.create_index("ix_redesign_job_lease", "redesign_domain_jobs", ["lease_until"])
    op.create_index("ix_redesign_job_type_queue", "redesign_domain_jobs", ["job_type", "status", "available_at"])

    op.add_column("redesign_api_idempotency_keys", sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("redesign_api_idempotency_keys", sa.Column("reservation_token", sa.String(64), nullable=True))
    op.add_column("redesign_api_idempotency_keys", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))

    op.alter_column("redesign_knowledge_chunk_embeddings", "embedding", nullable=True)
    op.add_column("redesign_knowledge_chunk_embeddings", sa.Column("dimension", sa.Integer(), nullable=True))
    op.add_column("redesign_knowledge_chunk_embeddings", sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("redesign_knowledge_chunk_embeddings", sa.Column("lease_owner", sa.String(120), nullable=True))
    op.add_column("redesign_knowledge_chunk_embeddings", sa.Column("lease_token", sa.String(64), nullable=True))
    op.add_column("redesign_knowledge_chunk_embeddings", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_redesign_embedding_queue", "redesign_knowledge_chunk_embeddings", ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_redesign_embedding_queue", table_name="redesign_knowledge_chunk_embeddings")
    op.drop_column("redesign_knowledge_chunk_embeddings", "lease_until")
    op.drop_column("redesign_knowledge_chunk_embeddings", "lease_token")
    op.drop_column("redesign_knowledge_chunk_embeddings", "lease_owner")
    op.drop_column("redesign_knowledge_chunk_embeddings", "available_at")
    op.drop_column("redesign_knowledge_chunk_embeddings", "dimension")
    op.alter_column("redesign_knowledge_chunk_embeddings", "embedding", nullable=False)

    op.drop_column("redesign_api_idempotency_keys", "last_seen_at")
    op.drop_column("redesign_api_idempotency_keys", "reservation_token")
    op.drop_column("redesign_api_idempotency_keys", "reserved_at")

    op.drop_index("ix_redesign_job_type_queue", table_name="redesign_domain_jobs")
    op.drop_index("ix_redesign_job_lease", table_name="redesign_domain_jobs")
    for name in ("dead_reason", "dead_at", "last_attempt_at", "last_heartbeat_at", "lease_token", "lease_owner", "max_attempts"):
        op.drop_column("redesign_domain_jobs", name)

    op.drop_index("ix_redesign_outbox_route", table_name="redesign_outbox_events")
    op.drop_index("ix_redesign_outbox_lease", table_name="redesign_outbox_events")
    for name in ("published_message_id", "dead_reason", "dead_at", "last_attempt_at", "lease_until", "lease_token", "lease_owner", "max_attempts", "routing_key", "event_version"):
        op.drop_column("redesign_outbox_events", name)
