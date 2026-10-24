"""A rejected applicant may apply again: one LIVE application per address

Revision ID: d7e2f9a41c86
Revises: e1c4b7a209d6
Create Date: 2026-09-16

------------------------------------------------------------------------------
THE ADDRESS WAS UNIQUE FOREVER, SO A REJECTION WAS PERMANENT BY ACCIDENT
------------------------------------------------------------------------------

`registrations.email` carried a plain UNIQUE constraint from the day the table
was created (9ecfa486074d: `sa.UniqueConstraint('email')`, which Postgres
names `registrations_email_key`). The duplicate guard in
`routers/registration.py::submit` reads the same rule - any row on the address
refuses a new submission with a deliberately opaque 409 - and together they
made every REJECTED application a permanent refusal of the person behind it:

    applicant mistypes their USN  ->  office rejects, reason "USN does not
    match the roster"  ->  the mail says "reply to the placement office"  ->
    the applicant corrects the USN and applies again  ->  409, "this
    application could not be accepted ... contact the placement office".

Neither side could see why. The office's Rejected tab showed a decision made
last week; the applicant's screen showed the same sentence a live duplicate
gets. A rejection is a decision about ONE application, and the row that
records it - who decided, when, and the reason they were made to type - must
stay. The ADDRESS should not be spent with it.

------------------------------------------------------------------------------
PARTIAL UNIQUE, NOT NO UNIQUE
------------------------------------------------------------------------------

The constraint is replaced by a unique index on `email` WHERE `status <>
'REJECTED'`. Every other status is either waiting on a decision (PENDING_REVIEW,
HOLD) or already an account (AUTO_APPROVED, APPROVED), and a second row beside
any of those is exactly the duplicate the guard exists to refuse - kept in the
database rather than only in the guard's read-then-write, so two submissions
racing each other cannot both land. The house pattern for "one live row":
`uq_mentor_assignment_one_open_spell`, `uq_interview_consent_active`, and the
others e1c4b7a209d6 lists.

No repair pass is needed before the index is created: the old constraint
guaranteed at most one row per address, so no address holds two live rows
today. The model declares the same index by name with the same predicate
(`app/models/registration.py`), so `alembic check` is clean after this.

The downgrade re-creates the plain constraint, and it will REFUSE on any
deployment where an address has since applied again after a rejection -
correctly, because there is no way to put that constraint back without
deleting one of two real applications, and this migration will not choose
which.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# The ANNOTATED form: `tests/test_codebase_guards.py::_revisions` reads these
# lines with `^revision:\s*str\s*=` and fails on a file it cannot parse.
revision: str = "d7e2f9a41c86"
down_revision: Union[str, None] = "e1c4b7a209d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SET LOCAL, never SET: env.py runs the whole `upgrade head` in ONE
    # transaction on ONE connection, so a session-scoped SET leaks into every
    # migration downstream of this file, forever. See d5a1c8b30f47.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    # Postgres's own name for the unnamed UNIQUE (email) 9ecfa486074d created;
    # `Base.metadata` carries no naming convention, so nothing renamed it.
    op.drop_constraint("registrations_email_key", "registrations", type_="unique")
    op.create_index(
        "uq_registration_live_email",
        "registrations",
        ["email"],
        unique=True,
        postgresql_where=sa.text("status <> 'REJECTED'"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index("uq_registration_live_email", table_name="registrations")
    # Fails, on purpose, wherever an address has applied again after a
    # rejection since the upgrade: see the module docstring.
    op.create_unique_constraint("registrations_email_key", "registrations", ["email"])
