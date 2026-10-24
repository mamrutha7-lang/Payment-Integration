"""registrations.personal_email / linkedin_url — the two boxes the public form
asked for and threw away.

Revision ID: a3f9c2e17b48
Revises: a4e7c92d1f38
Create Date: 2026-09-16

The registration form drew a Personal email box and a LinkedIn box from the
day it was built and sent neither: the component's own docstring called them
"placeholders the backend does not yet take". On 2026-09-16 every box on that
form became compulsory (Specialization excepted), and a compulsory box whose
value is discarded is a lie told to every applicant, so the two columns land
here. NULLABLE, like `phone` beside them: `RegisterIn` is what requires them of
a NEW application, and the rows already in the queue predate the rule.

No backfill, for the same reason as every other one in this directory: there
is nothing honest to write. The office asks the student for both on their
placement profile, and approval copies these across from now on.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f9c2e17b48"
down_revision: Union[str, None] = "a4e7c92d1f38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("registrations", sa.Column("personal_email", sa.String(), nullable=True))
    op.add_column("registrations", sa.Column("linkedin_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("registrations", "linkedin_url")
    op.drop_column("registrations", "personal_email")
