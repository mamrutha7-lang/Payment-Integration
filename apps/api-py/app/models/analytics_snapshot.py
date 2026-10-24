"""One week of analytics for one rung of the spine, computed once (B8.6).

WHY IT EXISTS. B8.5's series answer "readiness, attendance, skilling hours and
offers, per week, for the last twelve weeks" — four aggregates over every
student in the caller's reach, recomputed on every load of the Analytics screen.
That is affordable once and wasteful forever, and it is also UNSTABLE: a series
computed live changes underneath the reader as rows are written, so two people
looking at the same chart at the same time can see different numbers and neither
is wrong.

A snapshot fixes both. `app.analytics_job` writes one row per scope per week and
the series endpoint reads them back.

B8.5 MUST STILL ANSWER WITHOUT THIS TABLE. The nightly job is an EventBridge
schedule in `infra/cdk/reep_core/stack.py`, i.e. an infrastructure change; an
analytics screen that reads only from here is a screen that shows nothing at all
on any deployment where that schedule has not been added, and an empty table
nothing writes is precisely the decorative state B8.3's alert rules are being
judged for. Compute live, cap the window, and let this make it cheap.

`scope_type` IS A PLAIN STRING AND `scope_id` IS NOT A FOREIGN KEY, both on
purpose. The rung vocabulary is `ScopeLevel`'s (`governance_scope_level`), but
reusing that PG type here needs `create_type=False` in every migration that
touches it (AGENTS.md gotcha (b)) to buy nothing — nothing joins on it. And
`scope_id` is polymorphic across five tables, so it CANNOT be a foreign key;
this note is here so the next reader does not add one and the FK-index guard
does not go looking for one.

`PROGRAMME_SCOPE_ID` IS A SENTINEL AND NOT A NULL, AND THAT IS LOAD-BEARING.
The deployment-wide roll-up names no rung, so the obvious column value is NULL —
and Postgres treats NULLs as DISTINCT in a unique index, so `(programme, NULL,
week)` can be inserted again every single night without collision. The series
would then double, then treble, and the chart would show a step change nobody
could trace to a deploy. The sentinel makes the unique constraint mean what it
says: one row per scope per week, rewritten in place.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


#: The rungs a snapshot can be taken at, plus the deployment-wide roll-up.
#: Mirrors `models.governance.ScopeLevel`'s values in lower case; validated
#: where a row is written, never by the database (see the module docstring).
SCOPE_PROGRAMME = "programme"
SCOPE_COLLEGE = "college"
SCOPE_DEPARTMENT = "department"
SCOPE_COURSE = "course"
SCOPE_SPECIALIZATION = "specialization"
SCOPE_COHORT = "cohort"

SNAPSHOT_SCOPE_TYPES: tuple[str, ...] = (
    SCOPE_PROGRAMME,
    SCOPE_COLLEGE,
    SCOPE_DEPARTMENT,
    SCOPE_COURSE,
    SCOPE_SPECIALIZATION,
    SCOPE_COHORT,
)

#: What `scope_id` holds for the deployment-wide row. See the module docstring:
#: a NULL here would make the unique constraint below stop constraining.
PROGRAMME_SCOPE_ID = "*"


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id", "week", name="uq_analytics_snapshot"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    scope_type: Mapped[str] = mapped_column(String)
    scope_id: Mapped[str] = mapped_column(String)
    #: The MONDAY of the ISO week, so "which week is this" has one answer and
    #: two jobs run on different weekdays cannot disagree about it.
    week: Mapped[date] = mapped_column(Date)
    #: The metrics, as a dict. A JSONB blob rather than columns because B8.5's
    #: KPI list is still moving and each addition would otherwise be a migration
    #: plus a backfill of every historical week with a number nobody can compute
    #: after the fact. A key that is absent means NOT MEASURED — never zero.
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
