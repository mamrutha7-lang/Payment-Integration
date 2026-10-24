"""What an account did that the account's owner should be able to see.

Two tables, both append-only, both written by a path that already had the facts
and was throwing them away.

`login_events` (B15) is the "Recent sign-ins" list on My account. `login_days`
already exists and is NOT this: it records one row per student per day for the
streak on the dashboard, carries no door and no address, and is deliberately
coarse. A person checking whether somebody else has been in their account needs
to know WHEN, THROUGH WHICH DOOR, and FROM WHERE, and needs it for staff too.

`export_events` (B14) is the audited record of a spreadsheet of students leaving
the building. AGENTS.md is blunt about what an export is -- "they leave REEP the
moment they are downloaded and nothing here can recall them" -- and until now the
only trace was the request in an access log nobody reads. The row records who,
what, the filters that decided the rows, and how many there were.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class LoginEvent(Base):
    """One successful sign-in.

    ONLY SUCCESSES. A failed attempt is the brute-force limiter's business
    (app/routers/auth.py keys it on the account, never the address) and putting
    failures on a screen the account holder reads turns "somebody mistyped their
    password" into an alarm. What this answers is "was that me".
    """

    __tablename__ = "login_events"
    __table_args__ = (
        # The query the "Recent sign-ins" list actually runs: this user's rows,
        # newest first. Declared HERE as well as in e5f2c86d40b1 because a
        # migration-only index is drift — `alembic check` reports it as a
        # removed index on every run, and a real removal then hides in the
        # noise. Same reasoning as CapabilityGrant's check constraints.
        #
        # AND DECLARING IT FOUND A REDUNDANT INDEX THE GUARD COULD NOT SEE.
        # `user_id` carried `index=True` as well, so `ix_login_events_user_id`
        # was a second btree buying nothing this one does not already give —
        # Postgres serves `WHERE user_id = ?` from the composite's lead column,
        # and the narrow index only cost a write on every sign-in.
        # `test_no_index_duplicates_the_prefix_of_another` is written to catch
        # exactly that and was BLIND to it, because it reads the models and the
        # covering index was declared only in a migration. The undeclared index
        # was not merely noise in `alembic check`; it was hiding a real one.
        # Dropped in `f3a8d61c07be`; the FK stays indexed, by this composite,
        # which leads with it.
        Index("ix_login_events_user_at", "user_id", "at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: `password`, `code`, `google`, `activation`, `reset` — the four doors of
    #: AGENTS.md's single-device note, plus the two link flows. A String, not an
    #: enum: a new door should be a deploy, not a type migration.
    door: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Behind the ALB this is one value for the entire internet, which is why
    #: the brute-force limiter refuses to key on it. It is still worth recording
    #: — on a laptop deployment it is real, and where it is not, it is honestly
    #: the same for every row rather than wrong.
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class ExportEvent(Base):
    """One extract downloaded, with the filters that shaped it."""

    __tablename__ = "export_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: `students`, `placement`, `ledger`, `badges`, `registrations`, `leave`.
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: The query that decided which rows left the building. JSON because the
    #: filters differ per extract and a column per filter would be a migration
    #: every time one is added — which is how the record stops being kept.
    #:
    #: `JSONB` and not `JSON` (e1c4b7a209d6): the natural question to ask an
    #: audit column is "which exports carried this filter", and `json` stores
    #: raw text — no containment, no GIN index, and not even an equality
    #: operator, so `SELECT DISTINCT filters` is an error rather than an answer.
    #: The `server_default` is declared here because the database has carried one
    #: since this column was created and the model never said so; `alembic check`
    #: does not compare server defaults, so the two disagreed silently.
    filters: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    #: How many rows. The number that makes "an export happened" into "an export
    #: of 412 students happened", which is the difference between a log line and
    #: a fact somebody can act on.
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Whether the file carried personal columns. B14 omits them from the CSV
    #: unless the caller holds a PII-carrying function, so this records which
    #: kind of file it was rather than leaving it to be inferred from the date.
    carried_pii: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
