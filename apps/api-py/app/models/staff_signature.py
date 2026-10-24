"""A staff member's uploaded signature image — one per account, replaced in place.

Uploaded ONCE and reused: it is drawn into the SIGNATURE OF STAFF blocks of
every leave paper they apply on, and into the PROGRAM DIRECTOR block of every
paper they sanction (app/leave_paper.py). It changes nothing about how a leave
is signed - a signature on this form is still a NAME AND A TIME recorded
against the signer's REEP account (routers/leave.py); the image is what the
printed paper shows beside them.

Keyed on `users.id`, like the upskilling shelf, and stored through the same
hardened document_store: the bytes are sniffed (PNG or JPEG only - a PDF is
refused at the router), capped, and the stored name is opaque. Nothing links
to the row: `students` never reach it, and a leave paper reads it at render
time by user id, so a replaced image shows on every paper from then on.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class StaffSignature(Base):
    __tablename__ = "staff_signatures"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    stored_name: Mapped[str] = mapped_column(String, unique=True)
    mime_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
