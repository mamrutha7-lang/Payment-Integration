"""Staff upskilling certificates — a faculty member's own completed-course
uploads, mirroring the student certificate-proof flow but keyed on the USER, not
a Student row (staff have none).

Bytes live in the same hardened document_store as student uploads (magic-byte
sniffing, random stored_name); only metadata is here. No review workflow: a
staff member's certificate is their own record, not evidence awaiting a mentor's
verdict, so there is no status column to invent states for.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class StaffUpskillingCertificate(Base):
    __tablename__ = "staff_upskilling_certs"
    __table_args__ = (Index("ix_staffcert_user", "user_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    title: Mapped[str] = mapped_column(String)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    original_name: Mapped[str] = mapped_column(String)
    stored_name: Mapped[str] = mapped_column(String, unique=True)
    mime_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
