"""voice worker heartbeats

Revision ID: d2f7a1c9e4b0
Revises: c4e4c58eac29
Create Date: 2026-08-15 21:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2f7a1c9e4b0'
down_revision: Union[str, None] = 'c4e4c58eac29'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'voice_worker_heartbeats',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('worker_id', sa.String(), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('worker_id', name='uq_voice_worker_heartbeat_worker_id'),
    )


def downgrade() -> None:
    op.drop_table('voice_worker_heartbeats')
