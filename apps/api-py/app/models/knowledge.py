"""REEP Knowledge Base — the "explain the rules" layer for the grounded assistant.

This is DELIBERATELY separate from every student-fact table. A KnowledgeDocument
holds APPROVED policy / FAQ / guidance text only — never a student's marks,
eligibility, attendance or any other live record. The grounded assistant reads
from here to *explain how REEP works*; a student's own numbers still come from the
authenticated records view, never from this store. Keeping the two apart is what
lets the KB text be embedded and even sent to a remote embedder (it is public
policy) while student PII stays behind the egress gate.

KnowledgeChunk is the retrieval unit: a document is split into chunks, each with
an optional pgvector `embedding` (nullable, so full-text retrieval works before
any embeddings exist) and a Postgres full-text GIN index over `chunk_text`.
"""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class KnowledgeStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


class KnowledgeDocument(Base):
    """An approved unit of policy / guidance. Only APPROVED rows are ever served
    by retrieval, and `audience` narrows who may see them."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_doc_status_audience", "status", "audience"),
        Index("ix_knowledge_doc_source_type", "source_type"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String)
    # 'policy' | 'faq' | 'course_guide' | 'placement_guide'
    source_type: Mapped[str] = mapped_column(String)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[str] = mapped_column(String, default="1", server_default="1")
    status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        default=KnowledgeStatus.DRAFT,
        server_default="DRAFT",
    )
    # 'student' | 'mentor' | 'admin' | 'all'
    audience: Mapped[str] = mapped_column(String, default="student", server_default="student")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_role: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class KnowledgeChunk(Base):
    """A retrievable slice of a document. `embedding` is a nullable pgvector
    `vector` (populated only when an embedding provider is configured); retrieval
    falls back to Postgres full-text when it is absent, and blends DB-side cosine
    distance (`embedding <=> :q`) when it is present. A GIN index on
    to_tsvector('english', chunk_text) backs the full-text path (created in the
    migration via op.execute).

    The column is a DIMENSIONLESS `vector` (no typmod): the KB is small and
    curated, so an exact cosine scan over a few chunks is instant and no ivfflat/
    hnsw index is needed — and any embedding model's dimension fits without a
    schema change (all rows + the query share one provider, so `<=>` dims line
    up). `reembed_all` rewrites every row when the provider changes."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunk_document", "document_id"),
        # Full-text GIN index over chunk_text — the PRIMARY retrieval path
        # (app/knowledge.py). Declared here so metadata matches the DB; the
        # migration creates the identical expression index via op.execute.
        Index(
            "ix_knowledge_chunk_fts",
            text("to_tsvector('english', chunk_text)"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE")
    )
    chunk_text: Mapped[str] = mapped_column(Text)
    section_title: Mapped[str | None] = mapped_column(String, nullable=True)
    anchor: Mapped[str | None] = mapped_column(String, nullable=True)
    # pgvector `vector` (dimensionless) — nullable so the KB works before any
    # embeddings exist. Retrieval orders by `embedding <=> :query_vec` in-DB.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped["KnowledgeDocument"] = relationship(back_populates="chunks")
