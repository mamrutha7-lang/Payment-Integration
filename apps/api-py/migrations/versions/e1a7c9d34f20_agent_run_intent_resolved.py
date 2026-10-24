"""agent_run intent + resolved grounding signal

Revision ID: e1a7c9d34f20
Revises: 1aa19fa788e9
Create Date: 2026-08-15 23:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e1a7c9d34f20'
down_revision: Union[str, None] = '1aa19fa788e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both nullable — existing rows (and chat/stream runs) leave them null.
    op.add_column('agent_runs', sa.Column('intent', sa.String(), nullable=True))
    op.add_column('agent_runs', sa.Column('resolved', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_runs', 'resolved')
    op.drop_column('agent_runs', 'intent')
