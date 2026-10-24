"""BGSCET leave form fields

The leave request carried dates, a reason and two approvals. The official form
the college actually files prints more than that — which of Casual Leave /
Permission / OOD / RH / LOP is being applied for, a Credit cell, and an
"Alternate Arrangements" block naming who covers which class — and none of it
had anywhere to live, so the printed document could not be reproduced from the
record.

Designation and department go on users for the same reason: the form prints them
beside the applicant's name as institutional fields, and they were nowhere in
the schema. Nullable, because the roster does not carry them for every row yet.

Revision ID: c4e91b7d3a58
Revises: b8d2f7a4c619
Create Date: 2026-09-04 15:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c4e91b7d3a58'
down_revision: Union[str, None] = 'b8d2f7a4c619'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('designation', sa.String(), nullable=True))
    op.add_column('users', sa.Column('department', sa.String(), nullable=True))

    op.add_column('leave_requests', sa.Column('leave_kind', sa.String(), nullable=True))
    op.add_column('leave_requests', sa.Column('credit', sa.String(), nullable=True))
    op.add_column('leave_requests', sa.Column('alt_name', sa.String(), nullable=True))
    op.add_column(
        'leave_requests',
        sa.Column(
            'alt_rows',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='[]',
        ),
    )
    op.add_column(
        'leave_requests', sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True)
    )
    # Every request that already exists was submitted, which under the new model
    # means it was signed. Backfilling from created_at keeps those rows from
    # rendering as unsigned drafts on the form.
    op.execute("UPDATE leave_requests SET signed_at = created_at WHERE signed_at IS NULL")


def downgrade() -> None:
    op.drop_column('leave_requests', 'signed_at')
    op.drop_column('leave_requests', 'alt_rows')
    op.drop_column('leave_requests', 'alt_name')
    op.drop_column('leave_requests', 'credit')
    op.drop_column('leave_requests', 'leave_kind')
    op.drop_column('users', 'department')
    op.drop_column('users', 'designation')
