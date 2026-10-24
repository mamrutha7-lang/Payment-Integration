"""interview_sessions.audio_skipped_reason — why an interview has no recording.

Revision ID: b7d2e4f9a1c3
Revises: a3f9c2e17b48
Create Date: 2026-09-17

`audio_recorded = false` collapsed four different facts into one and the
Interview records screen rendered all four as "No audio" beside a grey
"Download recording" button. On the deployment that prompted this the
operator's INTERVIEW_RECORDING_ENABLED was true and the college's policy had
never had "Allow voice recording" ticked, and the only place that said so was
an INFO line in the API log. The finalizer now writes which gate closed
(`app/interview_audio.py`'s SKIP_* vocabulary), and the screen names the
switch to flip.

NULLABLE, NO BACKFILL. A recorded interview has nothing to explain, and for
every row already written nobody knows which gate it was; writing a guess
would tell the office something the code never observed.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2e4f9a1c3"
down_revision: Union[str, None] = "a3f9c2e17b48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "interview_sessions", sa.Column("audio_skipped_reason", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "audio_skipped_reason")
