"""alumni joined_on

The alumni profile recorded the current company and designation but not when
that role started, so the placement view could not tell someone three months
into a job from someone three years in. graduation_year does not answer it — an
alumnus can change employer years after graduating.

Revision ID: a71c3e5d9f42
Revises: c4e91b7d3a58
Create Date: 2026-09-04 17:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a71c3e5d9f42'
down_revision: Union[str, None] = 'c4e91b7d3a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alumni_profiles', sa.Column('joined_on', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('alumni_profiles', 'joined_on')
