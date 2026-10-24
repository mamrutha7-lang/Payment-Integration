"""skill_claims.upload_id: CASCADE -> SET NULL, and nullable.

A student deleting their own upload was destroying a mentor's assessment of it.
`skill_claims.upload_id` was `NOT NULL REFERENCES uploads(id) ON DELETE CASCADE`,
so `DELETE /api/student/uploads/{id}` took the whole SkillClaim row with it: the
level the mentor granted, the reviewer's identity and timestamp, and the review
note they wrote. Neither party was told and nothing else held a copy.

The student is entitled to delete their own certificate. They are not entitled to
delete a mentor's assessment of it, and one foreign key made those the same act.

`badge_evidence.upload_id` (models/badge.py:355) already had this exactly right —
"SET NULL so deleting the upload leaves the claim (and its verdict) as an honest
audit line rather than vanishing history." This makes the two consistent.

NOTHING IS BACKFILLED, because nothing survives to backfill: every claim whose
upload was deleted is already gone. This changes the future only.

The readers were already built for it. `SkillClaimReviewOut`'s evidence_* fields
are optional "because a claim can outlive its upload", and `_claim_query` has
always used an OUTER join — the CASCADE is the only reason that case could not
arise.

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record. DROP CONSTRAINT and ADD
CONSTRAINT are catalogue-only; `ALTER COLUMN DROP NOT NULL` is too. The ADD does
NOT scan the table — an FK added over an existing valid one is still validated,
so the statement_timeout is generous for the row count this table will ever hold.

Revision ID: e3c9a4d15f80
Revises: d1f8b06c4e37
Create Date: 2026-09-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3c9a4d15f80"
down_revision: Union[str, None] = "d1f8b06c4e37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The name PostgreSQL generated when the table was created
#: (migrations/versions/6111de4784aa_skill_claims.py). Read off the live
#: database rather than assumed, because a wrong name here is a migration that
#: fails halfway with the column already nullable.
_FK = "skill_claims_upload_id_fkey"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_constraint(_FK, "skill_claims", type_="foreignkey")
    op.alter_column("skill_claims", "upload_id", existing_type=sa.String(), nullable=True)
    op.create_foreign_key(
        _FK, "skill_claims", "uploads", ["upload_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    # Going back re-imposes NOT NULL, which fails if any claim has already
    # outlived its upload. That is correct: those rows are the ones this
    # migration exists to keep, and silently deleting them to satisfy a
    # downgrade would repeat the bug in the opposite direction. Clear them
    # deliberately first if you really mean it.
    op.drop_constraint(_FK, "skill_claims", type_="foreignkey")
    op.alter_column("skill_claims", "upload_id", existing_type=sa.String(), nullable=False)
    op.create_foreign_key(
        _FK, "skill_claims", "uploads", ["upload_id"], ["id"], ondelete="CASCADE"
    )
