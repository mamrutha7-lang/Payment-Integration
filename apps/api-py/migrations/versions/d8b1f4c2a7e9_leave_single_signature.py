"""Leave approval is the Main Admin's alone: revoke every grant of the key that went

Revision ID: d8b1f4c2a7e9
Revises: c7d3e9a1f5b2
Create Date: 2026-09-16

`mentor.leave_approve` left the capability catalogue on 2026-09-16, when the
owner made leave approval a single decision by the Main Admin
(`app/routers/leave.py`). A grant row naming a key the catalogue no longer
defines is a row Governance cannot label and `require_capability` would raise
on; and a live grant of "Approve leave" listed against a faculty member is a
promise the API stopped keeping. So every live grant of it is REVOKED here —
stamped, never deleted, which is the rule for that table (`app/models/
governance.py`): the trail keeps that the office once made this decision.

NO SCHEMA CHANGE. `LeaveStatus.FIRST_APPROVED` and the `second_*` columns stay:
a Postgres enum value cannot be dropped, rows carry them, and the router
completes a once-signed row with the office's one decision.

DOWNGRADE DOES NOTHING. The grants were revoked with a reason written on
them; un-revoking would re-issue access nobody has decided to give back.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8b1f4c2a7e9"
down_revision: Union[str, None] = "c7d3e9a1f5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE capability_grants SET revoked_at = now(), "
            "revoke_reason = 'leave approval became the Main Admin''s alone on 2026-09-16; "
            "the mentor.leave_approve capability was retired' "
            "WHERE capability = 'mentor.leave_approve' AND revoked_at IS NULL"
        )
    )


def downgrade() -> None:
    pass
