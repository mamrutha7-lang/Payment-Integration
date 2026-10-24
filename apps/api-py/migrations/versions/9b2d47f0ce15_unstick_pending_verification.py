"""Move every PENDING_VERIFICATION application into the review queue.

WHAT THIS RELEASES. `POST /register` used to write PENDING_VERIFICATION and
wait for an emailed confirmation link before the rule engine ran; the review
queue lists PENDING_REVIEW, so an application whose mail never arrived was
invisible to every screen in the product. On a deployment whose SES account is
still sandboxed that was every application: verified on 2026-09-10, the
production log had not served a single `GET /api/register/verify` in 30 days,
while real applicants submitted and re-submitted into silence.

Submission now goes straight to the queue and the mailbox proof happens after
approval (routers/onboarding.py), so this status has no writer any more. The
rows it already wrote are real people who applied and were never seen. They are
moved to PENDING_REVIEW rather than deleted — `retention.sweep_unverified_registrations`
would otherwise have deleted them 7 days after they applied, quietly.

`decision_reason` is REWRITTEN, not kept. The old value is "Awaiting email
confirmation.", which on the queue would read as an instruction to the admin to
go and wait for something that can no longer happen.

The enum VALUE stays. Dropping one from a Postgres enum means recreating the
type, and nothing here needs it gone — no code path writes it after this.

downgrade() deliberately does NOT put them back. Which rows were
PENDING_VERIFICATION before this ran is not recorded anywhere, and guessing
would re-hide applications an admin may since have decided.

Revision ID: 9b2d47f0ce15
Revises: 31f7a4c60b12
Create Date: 2026-09-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9b2d47f0ce15"
down_revision: Union[str, None] = "31f7a4c60b12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE registrations
               SET status = 'PENDING_REVIEW',
                   decision_reason =
                       'Submitted before email confirmation was moved after approval - never reviewed.'
             WHERE status = 'PENDING_VERIFICATION'
            """
        )
    )
    # The links themselves are dead: nothing consumes them any more, and a
    # confirmation token outliving the flow it belonged to is a live secret
    # with no lock.
    op.execute(sa.text("DELETE FROM email_verifications"))


def downgrade() -> None:
    raise RuntimeError(
        "Irreversible: which applications were PENDING_VERIFICATION is not "
        "recorded, and restoring the status would re-hide applications the "
        "placement office may since have decided."
    )
