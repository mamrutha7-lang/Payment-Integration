"""Alumni profile — created by the alumnus themselves on first sign-in.

One row per ALUMNI user (unique user_id). The row existing at all is what the
client's first-login flow branches on: no row => show the create-profile form,
row => show the profile. The resume travels through the same hardened document_store
as student uploads (magic-byte sniffing, random stored name); only its metadata
lives here, and the four resume_* columns are nullable together — a profile
without a resume is a real profile, and replacing the resume swaps all four in
one update.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class AlumniProfile(Base):
    __tablename__ = "alumni_profiles"
    __table_args__ = (
        # NAMED rather than `unique=True` on the column, for College's stated
        # reason: an unnamed unique constraint is called one thing by
        # create_all and another by the migration, and a later drop fails on
        # half the databases.
        UniqueConstraint("student_id", name="uq_alumni_profile_student"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    # ------------------------------------------------------------------ #
    # THE ACADEMIC RECORD THIS ALUMNUS GRADUATED FROM (B4.4). NULLABLE, AND
    # THE ROW IS STILL NOT CREATED AT GRADUATION.
    #
    # 04-backend-changes.md asks graduation to create this row with the link
    # filled in. It cannot: `company` above is NOT NULL, so the row can only be
    # created by inventing a company — and ROW EXISTENCE is the single signal
    # `GET /api/alumni/profile`'s `created:` flag reports, which is what the
    # whole first-login create-profile form branches on. Creating the row makes
    # that form unreachable for every graduate and leaves the office's
    # placeholder printed on their profile for ever.
    #
    # So graduation flips the role and the status and creates NOTHING. The
    # graduate meets the create-profile form, which is what it is for, and this
    # column is filled in when they first save — matched on `user_id`, the fact
    # both rows already share. It is a CONVENIENCE, not the join: alumni
    # history is reachable through `students.user_id` without it.
    #
    # ondelete="SET NULL", and this is the deliberate half. `user_id` above
    # CASCADEs because the profile belongs to the ACCOUNT; this column only
    # points at the academic record, and destroying that record must never
    # destroy the person's profile. It is also what stops
    # `python -m app.purge_students` from dying on a ForeignKeyViolation
    # halfway through a pass — see that module's `_refuse_unless_every_student
    # _row_is_doomed`, which refuses before it gets that far, and the comment
    # there for what a cohort purge now does to a graduate.
    # ------------------------------------------------------------------ #
    student_id: Mapped[str | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"), nullable=True
    )

    # Where they work now — the one field the first-login form requires.
    company: Mapped[str] = mapped_column(String)
    designation: Mapped[str | None] = mapped_column(String, nullable=True)
    # When they started in the current role. A date rather than a year: an
    # alumnus a few months into a job and one three years in are different
    # people to a placement report, and "2026" cannot tell them apart.
    joined_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Current resume: metadata only, bytes in the document_store under resume_stored_name.
    resume_original_name: Mapped[str | None] = mapped_column(String, nullable=True)
    resume_stored_name: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    resume_mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    resume_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
