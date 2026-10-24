"""Drop voice_worker_heartbeats — the LiveKit voice worker was removed.

The table existed so `GET /api/voice/status` could call voice "healthy" only
when some out-of-process worker had posted a heartbeat in the last 30 seconds.
That worker (`voice_agent.py`), the router that read this table
(`app/routers/voice.py`) and the model (`app/models/voice_worker.py`) were all
deleted in 2026-09 when the LiveKit stack was removed; the one remaining voice
experience is the mock interviewer, which runs INSIDE the API process and needs
no liveness row to prove a fourth process is up.

Dropped rather than left behind because a table no model maps is exactly what
Alembic autogenerate proposes dropping on the next unrelated migration — as a
surprise, in someone else's diff. `if_exists=True` so a database that never got
`d2f7a1c9e4b0` (or one already hand-cleaned) still upgrades.

The downgrade recreates the table verbatim from `d2f7a1c9e4b0`. Heartbeat rows
are liveness data with a 30-second horizon, so there is nothing to preserve and
no data migration either way.

Revision ID: c4a9e7b21d38
Revises: b8f2d4c6a1e0
Create Date: 2026-09-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a9e7b21d38"
down_revision: Union[str, None] = "b8f2d4c6a1e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Raw SQL, not op.drop_table(..., if_exists=True): that keyword needs
    # Alembic >= 1.16 and this would be the tree's first use of it, setting an
    # unrecorded version floor. requirements.txt pins 1.19.1 so it works today,
    # but AGENTS.md records that loose bounds have already burned this repo
    # once, and the failure mode here is a TypeError inside a migration during
    # a deploy rather than an import error at build time. DROP TABLE IF EXISTS
    # has no floor at all.
    op.execute("DROP TABLE IF EXISTS voice_worker_heartbeats")


def downgrade() -> None:
    op.create_table(
        "voice_worker_heartbeats",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worker_id", name="uq_voice_worker_heartbeat_worker_id"),
    )
