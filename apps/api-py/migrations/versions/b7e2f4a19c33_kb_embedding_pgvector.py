"""knowledge_chunks.embedding -> pgvector `vector` (+ CREATE EXTENSION vector)

Revision ID: b7e2f4a19c33
Revises: f2b8d05e6a11
Create Date: 2026-08-16 10:20:00.000000

Upgrades the Knowledge-Base retrieval column from a plain float ARRAY to a
pgvector `vector`, so semantic search can order by cosine distance in-DB
(`embedding <=> :query_vec`) instead of only Python-side. Requires the
`pgvector/pgvector:pg17` docker image (stock PG17 + the `vector` extension).

The column was ARRAY(Float) and was NEVER populated before pgvector (retrieval
ran on Postgres full-text), so it is entirely NULL at this point — a drop +
re-add as `vector` is a clean, cast-free conversion with no data to migrate.
`app.ai.embeddings.reembed_all` then backfills it from the configured provider.
The `vector` column is DIMENSIONLESS (no typmod): the KB is small and curated, so
an exact cosine scan is instant and any provider's dimension fits without a
schema change.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from pgvector.sqlalchemy import Vector

revision: str = 'b7e2f4a19c33'
down_revision: Union[str, None] = 'f2b8d05e6a11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: the extension may already exist (it is enabled out-of-band on
    # the live DB); on a fresh clone this is where it gets created.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # All-NULL before pgvector -> no cast needed; drop + re-add as `vector`.
    op.drop_column("knowledge_chunks", "embedding")
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding", Vector(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "embedding")
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=True),
    )
    # The `vector` extension is left installed on downgrade — dropping it could
    # break anything else that came to depend on it, and an unused extension is
    # harmless.
