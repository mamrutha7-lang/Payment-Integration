"""A file a student has uploaded (ported from Prisma `Upload`). Bytes live on
disk under `stored_name`; only metadata is in the database, so the table stays
small and the file store can move without a migration. A mentor reviews each
one — PENDING_REVIEW -> VERIFIED / REJECTED — which is what makes a certificate
proof or a profile photo count.

Both enums (`upload_kind`, `upload_status`) are new to the Python schema, so
autogenerate creates them cleanly (no create_type=False dance).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class UploadKind(str, enum.Enum):
    CERTIFICATE_PROOF = "CERTIFICATE_PROOF"  # manual counterpart to the provider sync
    RESUME = "RESUME"  # an existing CV the resume builder can read
    PROFILE_PHOTO = "PROFILE_PHOTO"
    DOCUMENT = "DOCUMENT"  # internship reports, ID proof, anything else


class UploadStatus(str, enum.Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    VERIFIED = "VERIFIED"
    # Sent back to be fixed, NOT refused. The distinction is the student's next
    # action: NEEDS_CHANGES means "do this again with better evidence" and
    # REJECTED means "this claim will not be granted". Collapsing them lost the
    # only thing the student needed to know from a decision that was not a yes.
    NEEDS_CHANGES = "NEEDS_CHANGES"
    REJECTED = "REJECTED"


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        Index("ix_upload_student_kind", "student_id", "kind"),
        Index("ix_upload_cert", "cert_code"),
        Index("ix_upload_status", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    kind: Mapped[UploadKind] = mapped_column(Enum(UploadKind, name="upload_kind"))

    # Set when kind is CERTIFICATE_PROOF — which certification this evidences.
    cert_code: Mapped[str | None] = mapped_column(
        ForeignKey("certifications.code", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String)
    original_name: Mapped[str] = mapped_column(String)
    # Filename on disk. Random, so an uploaded name can never traverse a path.
    stored_name: Mapped[str] = mapped_column(String, unique=True)
    mime_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)

    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, name="upload_status"),
        default=UploadStatus.PENDING_REVIEW,
        server_default="PENDING_REVIEW",
    )
    # Mentor who verified or rejected the proof, and why. A plain column (audit
    # stamp), not a relation — the row is never queried by reviewer.
    reviewed_by_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String, nullable=True)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
