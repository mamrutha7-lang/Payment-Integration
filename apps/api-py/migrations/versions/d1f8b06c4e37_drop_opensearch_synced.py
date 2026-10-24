"""Drop platform_call_sessions.opensearch_synced — the projection is gone.

The OpenSearch Serverless collection cost $361/month and NOTHING READ IT. Both
of its indexes were dead:

  * `question-vectors` was written only when a request opted in with
    `index_vector: true`, was never re-indexed on PATCH, never removed on
    DELETE, and `OpenSearchIndex.search` / `.knn` had **zero callers** in the
    repository.
  * `candidate-sessions` had never successfully written a single document.
    `call_close.py` put raw `datetime` objects into the doc and
    `opensearch.py::_request` handed it straight to `json.dumps` with no
    marshaller (DynamoDB's store has one; this never did), so every call raised
    `TypeError: Object of type datetime is not JSON serializable`, was swallowed
    by "a projection never fails the call", and returned False.

So this column has been `false` for every row that ever existed, and it will
never be anything else. Dropping it is the honest end of the feature: a
`CallOut.opensearch_synced` that reads false forever tells an operator something
is broken when nothing is.

`dynamo_synced` STAYS. That projection works and the flag means what it says.

OFFLINE-CLEAN and SET LOCAL, per d5a1c8b30f47's record. Dropping a column is
catalogue-only on PostgreSQL — the space is reclaimed lazily, no rewrite — so
the ACCESS EXCLUSIVE lock is held for a catalogue update.

Revision ID: d1f8b06c4e37
Revises: b9d4e7a2c318
Create Date: 2026-09-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text as sql_text

revision: str = "d1f8b06c4e37"
down_revision: Union[str, None] = "b9d4e7a2c318"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_column("platform_call_sessions", "opensearch_synced")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    # Restored as it was — false everywhere, which is also the only value it
    # ever held. The writer is not restored; see the module docstring.
    op.add_column(
        "platform_call_sessions",
        sa.Column(
            "opensearch_synced",
            sa.Boolean(),
            nullable=False,
            server_default=sql_text("false"),
        ),
    )
