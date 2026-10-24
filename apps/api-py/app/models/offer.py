"""Placement offers (ported from Prisma `PlacementOffer`). A student records an
offer as a DRAFT; submitting locks it (PENDING_APPROVAL) and routes to a
Main Admin, who APPROVES or REJECTS — the same single-decision shape as leave.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class OfferRoleType(str, enum.Enum):
    FULL_TIME = "FULL_TIME"
    FULL_TIME_PLUS_INTERNSHIP = "FULL_TIME_PLUS_INTERNSHIP"
    INTERNSHIP = "INTERNSHIP"


class OfferChannel(str, enum.Enum):
    ON_CAMPUS = "ON_CAMPUS"
    OFF_CAMPUS = "OFF_CAMPUS"
    POOL = "POOL"
    REFERRAL = "REFERRAL"


class OfferWorkMode(str, enum.Enum):
    REMOTE = "REMOTE"
    ONSITE = "ONSITE"
    HYBRID = "HYBRID"


class OfferStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PlacementOffer(Base):
    __tablename__ = "placement_offers"
    __table_args__ = (Index("ix_offer_student_status", "student_id", "status"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    role_type: Mapped[OfferRoleType] = mapped_column(Enum(OfferRoleType, name="offer_role_type"))
    job_title: Mapped[str] = mapped_column(String)
    organisation: Mapped[str] = mapped_column(String)
    channel: Mapped[OfferChannel] = mapped_column(
        Enum(OfferChannel, name="offer_channel"), default=OfferChannel.ON_CAMPUS, server_default="ON_CAMPUS"
    )
    joining_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    work_mode: Mapped[OfferWorkMode] = mapped_column(
        Enum(OfferWorkMode, name="offer_work_mode"), default=OfferWorkMode.ONSITE, server_default="ONSITE"
    )
    location: Mapped[str | None] = mapped_column(String, nullable=True)

    ctc_inr: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fixed_gross_inr: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bonuses: Mapped[list] = mapped_column(JSONB, default=list)

    offer_letter_upload_id: Mapped[str | None] = mapped_column(String, nullable=True)
    loi_upload_id: Mapped[str | None] = mapped_column(String, nullable=True)
    job_description: Mapped[str | None] = mapped_column(String, nullable=True)
    bond_details: Mapped[str | None] = mapped_column(String, nullable=True)
    other_benefits: Mapped[str | None] = mapped_column(String, nullable=True)

    status: Mapped[OfferStatus] = mapped_column(
        Enum(OfferStatus, name="offer_status"), default=OfferStatus.DRAFT, server_default="DRAFT"
    )
    approved_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
