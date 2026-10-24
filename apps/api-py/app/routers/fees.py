"""Student fees, paid through Razorpay Checkout.

    GET  /api/student/fees                         the student's own fees
    POST /api/student/fees/{id}/order              open a Razorpay order for one
    POST /api/student/fees/verify                  confirm what Checkout returned
    POST /api/payments/razorpay/webhook            Razorpay's own confirmation
    POST /api/admin/fees                           the office raises fees
    GET  /api/admin/fees                           ... and reads them back
    POST /api/admin/fees/{id}/cancel               ... and withdraws an unpaid one

THE BROWSER IS NEVER TRUSTED TO SAY IT PAID. Checkout's success callback runs in
the student's browser; anyone can call ``/verify`` with anything. A fee becomes
PAID only when (a) the HMAC over ``order_id|payment_id`` verifies against the
API key secret, or (b) a webhook signed with the webhook secret says so. Both
paths end in ``_settle``, which is idempotent, so the callback and the webhook
racing each other is the normal case and not a bug.

THE AMOUNT COMES FROM THE FEE ROW, never from the request: the order is opened
for ``fee.amount_paise`` and the webhook is checked against the amount stored on
the attempt, so a tampered client cannot pay a smaller sum.

Every student endpoint is first-person (``_require_student`` reads the id from
the session) and a fee belonging to someone else answers 404, not 403, so the
endpoint does not confirm that a given id exists.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import razorpay_client
from ..config import settings
from ..db import get_db
from ..identity import get_current_session
from ..models.cohort import Cohort
from ..models.course import Course, Enrollment, ProgressStatus
from ..models.fee import (
    FEE_CANCELLED,
    FEE_PAID,
    FEE_PENDING,
    PAYMENT_CREATED,
    PAYMENT_FAILED,
    PAYMENT_PAID,
    Fee,
    FeePayment,
)
from ..models.user import Role, Student, User
from .mentor import require_admin

log = logging.getLogger(__name__)

router = APIRouter(tags=["fees"])


def _require_student(session: dict) -> str:
    """Same gate as routers/student.py -- the ROLE is checked, not just the
    ``studentId`` claim (graduates keep a students row). EDIT ALL COPIES OR NONE."""
    if session.get("role") != Role.STUDENT.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fees are open to current students only.",
        )
    student_id = session.get("studentId")
    if not student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a student account.")
    return student_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FeeOut(BaseModel):
    id: str
    title: str
    amount_paise: int
    amount: str  # "1500.00" -- for display; arithmetic uses amount_paise
    currency: str
    due_date: date | None
    status: str
    overdue: bool
    paid_at: datetime | None
    course_code: str | None = None


class OrderOut(BaseModel):
    key_id: str
    order_id: str
    amount: int  # paise, as Checkout expects
    currency: str
    title: str


class FeeVerifyIn(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=64)
    razorpay_payment_id: str = Field(min_length=1, max_length=64)
    razorpay_signature: str = Field(min_length=1, max_length=256)


class FeeCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)  # rupees
    due_date: date | None = None
    student_ids: list[str] = Field(default_factory=list, max_length=2000)
    cohort_id: str | None = None

    @model_validator(mode="after")
    def _one_target(self) -> "FeeCreateIn":
        if bool(self.student_ids) == bool(self.cohort_id):
            raise ValueError("Give either student_ids or cohort_id, not both and not neither.")
        return self


def _paise(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _fee_out(fee: Fee, today: date) -> FeeOut:
    return FeeOut(
        id=fee.id,
        title=fee.title,
        amount_paise=fee.amount_paise,
        amount=f"{Decimal(fee.amount_paise) / 100:.2f}",
        currency=fee.currency,
        due_date=fee.due_date,
        status=fee.status,
        overdue=fee.status == FEE_PENDING and fee.due_date is not None and fee.due_date < today,
        paid_at=fee.paid_at,
        course_code=fee.course_code,
    )


# ---------------------------------------------------------------------------
# Settling -- the one place a fee becomes PAID
# ---------------------------------------------------------------------------


def _enrol(db: Session, student_id: str, course_code: str) -> bool:
    """Create the Enrollment unless the student already has it. True if created."""
    exists = db.scalar(
        select(Enrollment.id).where(
            Enrollment.student_id == student_id, Enrollment.course_code == course_code
        )
    )
    if exists:
        return False
    db.add(
        Enrollment(student_id=student_id, course_code=course_code, status=ProgressStatus.IN_PROGRESS)
    )
    return True


def _settle(db: Session, payment: FeePayment, razorpay_payment_id: str) -> bool:
    """Mark the attempt and its fee PAID. Idempotent; True if THIS call changed it.

    The attempt row is locked first so the browser callback and the webhook,
    arriving together, serialise here instead of both writing.
    """
    db.execute(select(FeePayment.id).where(FeePayment.id == payment.id).with_for_update())
    db.refresh(payment)
    if payment.status == PAYMENT_PAID:
        return False
    now = _now()
    payment.status = PAYMENT_PAID
    payment.razorpay_payment_id = razorpay_payment_id
    payment.paid_at = now
    payment.failure_reason = None
    fee = db.get(Fee, payment.fee_id)
    if fee is not None and fee.status == FEE_PENDING:
        fee.status = FEE_PAID
        fee.paid_at = now
        # A course fee is the price of enrolling: confirming the payment IS the
        # enrolment. Same transaction, so a paid fee never lacks its seat.
        if fee.course_code:
            _enrol(db, fee.student_id, fee.course_code)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Student
# ---------------------------------------------------------------------------


@router.get("/student/fees", response_model=list[FeeOut])
def my_fees(session: dict = Depends(get_current_session), db: Session = Depends(get_db)):
    student_id = _require_student(session)
    rows = db.scalars(
        select(Fee)
        .where(Fee.student_id == student_id, Fee.status != FEE_CANCELLED)
        .order_by(Fee.status.desc(), Fee.due_date.asc().nulls_last(), Fee.created_at.desc())
    ).all()
    today = date.today()
    return [_fee_out(f, today) for f in rows]


@router.post("/student/fees/{fee_id}/order", response_model=OrderOut)
def open_order(
    fee_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    student_id = _require_student(session)
    fee = db.scalar(select(Fee).where(Fee.id == fee_id, Fee.student_id == student_id))
    if fee is None or fee.status == FEE_CANCELLED:
        raise HTTPException(status_code=404, detail="Fee not found.")
    return _open_order(db, fee)


def _open_order(db: Session, fee: Fee) -> OrderOut:
    """Open a Razorpay order for an unpaid fee and record the attempt."""
    if not razorpay_client.configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Online payment is not switched on yet. Please contact the office.",
        )
    if fee.status == FEE_PAID:
        raise HTTPException(status_code=409, detail="This fee is already paid.")
    try:
        order = razorpay_client.create_order(
            amount_paise=fee.amount_paise,
            currency=fee.currency,
            receipt=f"fee_{fee.id}",
            notes={"fee_id": fee.id, "student_id": fee.student_id},
        )
    except razorpay_client.RazorpayError as exc:
        log.warning("razorpay order failed for fee %s: %s", fee.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The payment gateway could not be reached. Please try again.",
        ) from exc
    db.add(
        FeePayment(
            fee_id=fee.id,
            amount_paise=fee.amount_paise,
            razorpay_order_id=order["id"],
        )
    )
    db.commit()
    return OrderOut(
        key_id=settings.razorpay_key_id,
        order_id=order["id"],
        amount=fee.amount_paise,
        currency=fee.currency,
        title=fee.title,
    )


# ---------------------------------------------------------------------------
# Enrolment -- paying is how a student takes a seat
# ---------------------------------------------------------------------------


class AvailableCourseOut(BaseModel):
    code: str
    name: str
    semester: int
    stage: str
    fee_paise: int
    fee: str  # "2500.00"; "0.00" = free


class EnrolOut(BaseModel):
    enrolled: bool  # True: free course, seat taken now. False: pay `order` to take it.
    order: OrderOut | None = None


class CourseFeeIn(BaseModel):
    amount: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)  # rupees


def _enrollable_course(db: Session, student_id: str, code: str) -> Course:
    """The course, if THIS student may enrol in it now; otherwise 404/409.

    The rule is the student's own semester: the Courses screen has always been
    "what the office registers you for this semester", and self-enrolment opens
    that same set, not the whole catalogue.
    """
    student = db.get(Student, student_id)
    course = db.get(Course, code)
    if course is None or student is None or course.semester != student.current_semester:
        raise HTTPException(status_code=404, detail="Course not found.")
    if db.scalar(
        select(Enrollment.id).where(
            Enrollment.student_id == student_id, Enrollment.course_code == code
        )
    ):
        raise HTTPException(status_code=409, detail="You are already enrolled in this course.")
    return course


@router.get("/student/courses/available", response_model=list[AvailableCourseOut])
def available_courses(session: dict = Depends(get_current_session), db: Session = Depends(get_db)):
    student_id = _require_student(session)
    student = db.get(Student, student_id)
    enrolled = select(Enrollment.course_code).where(Enrollment.student_id == student_id)
    rows = db.scalars(
        select(Course)
        .where(Course.semester == student.current_semester, Course.code.not_in(enrolled))
        .order_by(Course.code)
    ).all()
    return [
        AvailableCourseOut(
            code=c.code,
            name=c.name,
            semester=c.semester,
            stage=c.stage.value,
            fee_paise=c.fee_paise or 0,
            fee=f"{Decimal(c.fee_paise or 0) / 100:.2f}",
        )
        for c in rows
    ]


@router.post("/student/courses/{code}/enroll", response_model=EnrolOut)
def enrol(code: str, session: dict = Depends(get_current_session), db: Session = Depends(get_db)):
    student_id = _require_student(session)
    course = _enrollable_course(db, student_id, code)
    if not course.fee_paise:
        _enrol(db, student_id, code)
        db.commit()
        return EnrolOut(enrolled=True)

    # Reuse an open (unpaid) fee for this course rather than stacking a new one
    # per click, and re-price it: the office may have changed the course fee.
    fee = db.scalar(
        select(Fee).where(
            Fee.student_id == student_id, Fee.course_code == code, Fee.status == FEE_PENDING
        )
    )
    if fee is None:
        fee = Fee(
            student_id=student_id,
            course_code=code,
            title=f"Enrolment: {course.name}",
            amount_paise=course.fee_paise,
        )
        db.add(fee)
    else:
        fee.amount_paise = course.fee_paise
    db.flush()
    return EnrolOut(enrolled=False, order=_open_order(db, fee))


@router.put("/admin/courses/{code}/fee")
def set_course_fee(
    code: str,
    body: CourseFeeIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    """Price a course. ``amount`` null or 0 makes it free."""
    require_admin(session)
    course = db.get(Course, code)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found.")
    course.fee_paise = _paise(body.amount) if body.amount else None
    db.commit()
    return {"code": code, "fee_paise": course.fee_paise or 0}


@router.post("/student/fees/verify", response_model=FeeOut)
def verify_payment(
    body: FeeVerifyIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    student_id = _require_student(session)
    payment = db.scalar(
        select(FeePayment)
        .join(Fee, Fee.id == FeePayment.fee_id)
        .where(
            FeePayment.razorpay_order_id == body.razorpay_order_id,
            Fee.student_id == student_id,
        )
    )
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found.")
    if not razorpay_client.verify_checkout_signature(
        body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
    ):
        raise HTTPException(status_code=400, detail="Payment signature could not be verified.")
    _settle(db, payment, body.razorpay_payment_id)
    fee = db.get(Fee, payment.fee_id)
    db.refresh(fee)
    return _fee_out(fee, date.today())


# ---------------------------------------------------------------------------
# Webhook -- no session; the HMAC over the raw body is the authentication
# ---------------------------------------------------------------------------


@router.post("/payments/razorpay/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()  # the RAW bytes: re-serialised JSON would not match
    if not razorpay_client.verify_webhook_signature(
        body, request.headers.get("x-razorpay-signature", "")
    ):
        raise HTTPException(status_code=400, detail="Bad signature.")
    try:
        event = json.loads(body)
        entity = event["payload"]["payment"]["entity"]
        order_id = entity["order_id"]
        payment_id = entity["id"]
        kind = event["event"]
    except (ValueError, KeyError, TypeError):
        # Signed but not an event we use (order.paid, refunds, ...): acknowledge
        # so Razorpay stops retrying.
        return {"ok": True}

    payment = db.scalar(select(FeePayment).where(FeePayment.razorpay_order_id == order_id))
    if payment is None:
        return {"ok": True}

    if kind == "payment.captured":
        if entity.get("amount") != payment.amount_paise:
            log.error(
                "razorpay amount mismatch on order %s: got %s, expected %s",
                order_id,
                entity.get("amount"),
                payment.amount_paise,
            )
            return {"ok": True}
        _settle(db, payment, payment_id)
    elif kind == "payment.failed" and payment.status == PAYMENT_CREATED:
        payment.status = PAYMENT_FAILED
        payment.razorpay_payment_id = payment_id
        payment.failure_reason = str(entity.get("error_description") or "")[:200] or None
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Office
# ---------------------------------------------------------------------------


@router.post("/admin/fees", status_code=status.HTTP_201_CREATED)
def create_fees(
    body: FeeCreateIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    require_admin(session)
    if body.cohort_id:
        if db.get(Cohort, body.cohort_id) is None:
            raise HTTPException(status_code=404, detail="Cohort not found.")
        student_ids = list(db.scalars(select(Student.id).where(Student.cohort_id == body.cohort_id)))
    else:
        student_ids = list(dict.fromkeys(body.student_ids))
        known = set(db.scalars(select(Student.id).where(Student.id.in_(student_ids))))
        missing = [s for s in student_ids if s not in known]
        if missing:
            raise HTTPException(status_code=404, detail=f"Unknown student(s): {len(missing)}.")
    if not student_ids:
        raise HTTPException(status_code=422, detail="No students to raise the fee for.")
    paise = _paise(body.amount)
    if paise <= 0:
        raise HTTPException(status_code=422, detail="Amount must be at least 0.01.")
    for sid in student_ids:
        db.add(
            Fee(
                student_id=sid,
                title=body.title.strip(),
                amount_paise=paise,
                due_date=body.due_date,
                created_by=session.get("userId"),
            )
        )
    db.commit()
    return {"created": len(student_ids)}


@router.get("/admin/fees")
def list_fees(
    status_filter: str | None = None,
    limit: int = 200,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    require_admin(session)
    q = (
        select(Fee, User.name, Student.usn)
        .join(Student, Student.id == Fee.student_id)
        .join(User, User.id == Student.user_id)
        .order_by(Fee.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    if status_filter:
        q = q.where(Fee.status == status_filter.upper())
    today = date.today()
    return [
        {**_fee_out(f, today).model_dump(mode="json"), "student_name": name, "usn": usn}
        for f, name, usn in db.execute(q).all()
    ]


@router.post("/admin/fees/{fee_id}/cancel", response_model=FeeOut)
def cancel_fee(
    fee_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    require_admin(session)
    fee = db.get(Fee, fee_id)
    if fee is None:
        raise HTTPException(status_code=404, detail="Fee not found.")
    if fee.status == FEE_PAID:
        raise HTTPException(status_code=409, detail="A paid fee cannot be cancelled.")
    fee.status = FEE_CANCELLED
    db.commit()
    return _fee_out(fee, date.today())
