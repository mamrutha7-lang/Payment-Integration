"""Student fees and the Razorpay payments that settle them.

A ``Fee`` is a thing the office asks one student to pay (title, amount, due
date). A ``FeePayment`` is one Razorpay order opened against it. They are two
tables because a fee can be attempted more than once (a failed card, an
abandoned checkout) and every attempt must stay on the record; the fee is
``PAID`` only when one attempt is confirmed.

MONEY IS INTEGER PAISE, never a float: Razorpay's API takes the smallest
currency unit and a float rupee amount drifts (0.1 + 0.2).

Status columns are plain strings with a CHECK rather than PG enums, so adding a
state later is one constraint edit and not an ``ALTER TYPE``.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

FEE_PENDING = "PENDING"
FEE_PAID = "PAID"
FEE_CANCELLED = "CANCELLED"

PAYMENT_CREATED = "CREATED"
PAYMENT_PAID = "PAID"
PAYMENT_FAILED = "FAILED"


def _uuid() -> str:
    return uuid.uuid4().hex


class Fee(Base):
    __tablename__ = "fees"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_fee_amount_positive"),
        CheckConstraint("status IN ('PENDING','PAID','CANCELLED')", name="ck_fee_status"),
        Index("ix_fees_student_status", "student_id", "status"),
        Index("ix_fees_student_course", "student_id", "course_code"),
        # The two foreign keys that do not lead an index above, so deleting a
        # course or an account does not scan every fee to find its SET NULLs.
        Index("ix_fees_course_code", "course_code"),
        Index("ix_fees_created_by", "created_by"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String)
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String, default="INR")
    # Set when the fee is the price of enrolling in a course: confirming the
    # payment is what creates the Enrollment.
    course_code: Mapped[str | None] = mapped_column(
        ForeignKey("courses.code", ondelete="SET NULL"), nullable=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String, default=FEE_PENDING)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeePayment(Base):
    __tablename__ = "fee_payments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','PAID','FAILED')", name="ck_fee_payment_status"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    fee_id: Mapped[str] = mapped_column(
        ForeignKey("fees.id", ondelete="CASCADE"), index=True
    )
    # The amount is COPIED onto the attempt: it is what the order was opened
    # for, and a later edit to the fee must not rewrite what was charged.
    amount_paise: Mapped[int] = mapped_column(Integer)
    razorpay_order_id: Mapped[str] = mapped_column(String, unique=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default=PAYMENT_CREATED)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
