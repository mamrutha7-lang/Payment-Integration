"""A leave decision can be changed after it was made.

Revision ID: daaeb23b750e
Revises: f4a2c9e7b1d3
Create Date: 2026-10-13

Until now APPROVED and REJECTED were terminal: `decide_leave` refused any row
not still waiting, and the only way to undo a sanction given by mistake was a
database edit. `POST /api/leaves/{id}/change-decision` revokes a sanction
(APPROVED -> REVOKED) or reopens a refusal or a revocation (-> SUBMITTED, back
in the queue), always with a reason in words.

`ALTER TYPE leave_status ADD VALUE` is typed by hand: autogenerate does not
diff enum VALUES. Legal inside the migration's transaction on Postgres 12+
because nothing here writes a row using it.

NO BACKFILL. Every column is NULL (or 0) on every existing row, which is the
truth: no decision on any of them has ever been changed.

Downgrade drops the columns but REFUSES while any row is REVOKED — those rows
would be left holding a status the downgraded code cannot read, and turning
them back into APPROVED would re-sanction leave the office withdrew. The enum
value itself stays (Postgres has no DROP VALUE).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "daaeb23b750e"
down_revision: Union[str, None] = "f4a2c9e7b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "leave_requests_decision_changed_by_user_id_fkey"


def upgrade() -> None:
    op.execute("ALTER TYPE leave_status ADD VALUE IF NOT EXISTS 'REVOKED'")
    op.add_column("leave_requests", sa.Column("decision_change_kind", sa.String(length=16), nullable=True))
    op.add_column(
        "leave_requests", sa.Column("decision_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("leave_requests", sa.Column("decision_changed_by_user_id", sa.String(), nullable=True))
    op.add_column("leave_requests", sa.Column("decision_change_reason", sa.String(), nullable=True))
    op.add_column(
        "leave_requests",
        sa.Column("decision_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index(
        op.f("ix_leave_requests_decision_changed_by_user_id"),
        "leave_requests",
        ["decision_changed_by_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        FK_NAME, "leave_requests", "users", ["decision_changed_by_user_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    revoked = op.get_bind().execute(
        sa.text("SELECT count(*) FROM leave_requests WHERE status::text = 'REVOKED'")
    ).scalar_one()
    if revoked:
        raise RuntimeError(
            f"{revoked} leave request(s) are REVOKED. The code before this revision cannot "
            "read that status, and turning them back into APPROVED would re-sanction leave "
            "the office withdrew. Decide each one (reopen and refuse, or reopen and sanction) "
            "before downgrading."
        )
    op.drop_constraint(FK_NAME, "leave_requests", type_="foreignkey")
    op.drop_index(op.f("ix_leave_requests_decision_changed_by_user_id"), table_name="leave_requests")
    op.drop_column("leave_requests", "decision_revision")
    op.drop_column("leave_requests", "decision_change_reason")
    op.drop_column("leave_requests", "decision_changed_by_user_id")
    op.drop_column("leave_requests", "decision_changed_at")
    op.drop_column("leave_requests", "decision_change_kind")
