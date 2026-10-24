"""Drop ix_login_events_user_id — covered by ix_login_events_user_at.

Revision ID: f3a8d61c07be
Revises: c4f7b1e08d92
Create Date: 2026-09-13

`login_events` was created by e5f2c86d40b1 with `index=True` on `user_id` AND a
composite `(user_id, at)` beside it. Postgres serves every `WHERE user_id = ?`
from the composite's lead column, so the narrow index bought nothing and cost a
write on every sign-in — thirteen of these were found and dropped by
`a91f3c5d80e4`, and `tests/test_codebase_guards.py::
test_no_index_duplicates_the_prefix_of_another` exists to stop the fourteenth.

IT DID NOT STOP THIS ONE, AND THE REASON IS THE INTERESTING PART. That guard
reads `Base.metadata`, and `ix_login_events_user_at` was declared ONLY in the
migration — so the model showed one index on `user_id` and the guard had
nothing to compare it against. The redundancy was real in every database and
invisible in the only place anybody looks. Declaring the composite on the model
(the Phase 4 truthfulness pass, which was chasing three `alembic check` drift
items) made the guard fail immediately.

So the lesson is worth more than the index: an undeclared index is not merely
noise in `alembic check`. It is a hole the schema guards see through.

The foreign key stays indexed — `test_every_foreign_key_column_is_indexed`
accepts any index whose FIRST column is the FK, and the composite leads with it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3a8d61c07be'
down_revision: Union[str, None] = 'c4f7b1e08d92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF EXISTS: this index was created by `index=True` on the column, so a
    # database built from a later model rather than by replaying migrations
    # never had it. A DROP that raises there would block the upgrade on an
    # index whose absence is the desired state.
    op.execute(sa.text("DROP INDEX IF EXISTS ix_login_events_user_id"))


def downgrade() -> None:
    op.create_index("ix_login_events_user_id", "login_events", ["user_id"])
