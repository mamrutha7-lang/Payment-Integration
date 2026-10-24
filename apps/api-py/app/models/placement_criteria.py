"""Placement criteria (ported from Prisma `PlacementCriteria`). Admin-set
academic gates the placement funnel reads; one set is `active`. A job's per-
posting override wins; otherwise these defaults apply.

B8.2 GAVE THE TABLE A COURSE AND A DATE, and both are nullable forever.

WHY NULLABLE. The resolution order is "the student's course row, else the
programme-wide row, else the hard-coded defaults" — and the programme-wide row
is the one with NULL on both pointers. Every row written before B8.2 is exactly
that, which is why the migration does NOT attach them to a college: a NULL here
MEANS "applies everywhere", the widest reading, and it is the behaviour every
existing deployment already has. Attaching them to the one college that happens
to exist would silently mean a second college inherits nothing.

`effective_from` NULL means "since the beginning", for the same reason. Stamping
existing rows with the migration's date would assert that no criteria applied
before it, which is false on every deployment that has been running.

`created_by` IS `SET NULL` AND THAT IS NOT COSMETIC. This table is KEEP in both
destructors. `python -m app.purge_people` deletes every account but the Main
Admin, and a `users` foreign key with no ON DELETE on a KEPT table aborts that
pass on a constraint violation — which is why `purge_people.CREATED_BY_COLUMNS`
exists for the four spine tables that do it the other way. SET NULL keeps the
rule and loses the name: remove the person, keep the record.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class PlacementCriteria(Base):
    __tablename__ = "placement_criteria"
    __table_args__ = (
        CheckConstraint(
            "min_reep_completion_pct BETWEEN 0 AND 100 AND min_attendance_pct BETWEEN 0 AND 100 "
            "AND min_cert_completion_pct BETWEEN 0 AND 100 AND min_cgpa BETWEEN 0 AND 10 "
            "AND max_live_backlogs >= 0 AND max_gap_months >= 0",
            name="ck_placement_criteria_range",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, default="Default", server_default="Default")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # --------------------------------------------------- B8.2: where it applies
    # No `ondelete` on either pointer: the spine's convention is that the
    # database refuses to delete a rung that still has rows under it, and both
    # are archived rather than deleted. NULL on both is the programme-wide row.
    college_id: Mapped[str | None] = mapped_column(
        ForeignKey("colleges.id"), nullable=True, index=True
    )
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_courses.id"), nullable=True, index=True
    )
    #: When this set took effect. NULL means "since the beginning" — see the
    #: module docstring. The resolver reads the latest row that has already
    #: taken effect, so a set typed today for next term does not change a
    #: student's verdict this afternoon.
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: WHO set it. Nullable: `python -m app.seed` and the CLIs have no user.
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    min_reep_completion_pct: Mapped[float] = mapped_column(Float, default=80, server_default="80")
    require_core_certs: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    min_attendance_pct: Mapped[float] = mapped_column(Float, default=85, server_default="85")
    min_cert_completion_pct: Mapped[float] = mapped_column(Float, default=75, server_default="75")
    min_cgpa: Mapped[float] = mapped_column(Float, default=6.0, server_default="6.0")
    max_live_backlogs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_gap_months: Mapped[int] = mapped_column(Integer, default=24, server_default="24")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
