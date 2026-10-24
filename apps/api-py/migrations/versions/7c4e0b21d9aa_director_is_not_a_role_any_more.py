"""DIRECTOR is not a role any more: every holder becomes faculty

REEP has ONE office account, the Main Admin (ADMIN). DIRECTOR was a second one
under a different name — its baseline was every console screen — and
`app.grant_access` has refused to mint one for months. What kept it alive was
the dev seed and a handful of role sets, so a row nothing could create still
opened every door if one existed.

WHAT THIS DOES. Converts every remaining DIRECTOR to MENTOR (faculty), and
advances `token_version` on each so their live sessions die immediately rather
than carrying `{"role": "DIRECTOR"}` for up to twelve more hours. The role is
copied into the session JWT, so a role change that does not bump the version is
a change that has not happened yet for anyone already signed in — the same
reasoning `app/grant_access.py` applies on a role transition.

MENTOR, NOT ADMIN, and the choice matters. Promoting them would create a second
Main Admin, which is the exact thing this removal exists to prevent, and
`grant_access` refuses it anyway. Demoting to faculty is the handover AGENTS.md
already documents ("demote the current one to MENTOR first"): they keep an
account, they lose the console, and the Main Admin can hand back individual
screens through Governance if that is what was intended.

THE ENUM VALUE STAYS. Dropping a value from a Postgres enum means recreating the
type and rewriting every dependent column, which is a destructive migration for
a label no row will hold. It grants nothing anywhere (app/policies.py) and
`tests/test_no_director_privilege.py` keeps it that way.

Revision ID: 7c4e0b21d9aa
Revises: 31ca99852acd
Create Date: 2026-09-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c4e0b21d9aa"
down_revision: Union[str, None] = "31ca99852acd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # One statement, so the role change and the session kill cannot separate.
    # A row demoted whose token_version did not move is a person who is still
    # an admin in every request they make until their cookie expires.
    result = conn.execute(
        sa.text(
            """
            UPDATE users
               SET role = 'MENTOR',
                   token_version = COALESCE(token_version, 0) + 1
             WHERE role = 'DIRECTOR'
            """
        )
    )
    print(f"[director-removal] {result.rowcount} DIRECTOR account(s) converted to MENTOR")


def downgrade() -> None:
    """Deliberately NOT reversible, and this is not laziness.

    Nothing records WHICH of the MENTOR rows were DIRECTOR before this ran, so a
    downgrade could only guess — and guessing wrong here re-grants console
    access, over every student's marks, attendance and USN, to an account that
    should not have it. A migration whose reverse silently over-grants is worse
    than one that refuses.

    To restore a specific person's console access, grant them the screens they
    need in Governance, which is the supported path and leaves an audit row.
    """
    raise NotImplementedError(
        "Irreversible: which MENTOR rows were once DIRECTOR is not recorded. "
        "Grant the screens back in Governance instead."
    )
