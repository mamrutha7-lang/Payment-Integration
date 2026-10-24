"""Student fees and Razorpay.

Three claims, each pinned: a fee is PAID only on a valid signature (the browser
is never believed); the amount charged is the fee row's, and a student cannot
reach another student's fee; and the webhook authenticates by HMAC over the raw
body, refuses when no secret is configured, and is idempotent with the
browser callback.

Razorpay itself is never called: ``create_order`` is monkeypatched.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app import razorpay_client
from app.config import settings
from app.db import SessionLocal
from app.models.fee import Fee, FeePayment
from app.models.user import Role, Student

KEY_SECRET = "test_key_secret"
WEBHOOK_SECRET = "test_webhook_secret"


def _sign(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


@pytest.fixture
def razorpay(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_abc")
    monkeypatch.setattr(settings, "razorpay_key_secret", KEY_SECRET)
    monkeypatch.setattr(settings, "razorpay_webhook_secret", WEBHOOK_SECRET)
    counter = iter(range(1, 1000))

    def fake_create_order(*, amount_paise, currency, receipt, notes):
        return {"id": f"order_T{next(counter)}", "amount": amount_paise, "currency": currency}

    monkeypatch.setattr(razorpay_client, "create_order", fake_create_order)


# ------------------------------------------------------- pure signature --


def test_checkout_signature_roundtrip(razorpay):
    sig = _sign(KEY_SECRET, b"order_1|pay_1")
    assert razorpay_client.verify_checkout_signature("order_1", "pay_1", sig)
    assert not razorpay_client.verify_checkout_signature("order_1", "pay_2", sig)
    assert not razorpay_client.verify_checkout_signature("order_1", "pay_1", "")


def test_webhook_signature_signs_the_raw_body_with_the_webhook_secret(razorpay):
    body = b'{"event":"payment.captured"}'
    assert razorpay_client.verify_webhook_signature(body, _sign(WEBHOOK_SECRET, body))
    # the API key secret is a different secret and must not verify a webhook
    assert not razorpay_client.verify_webhook_signature(body, _sign(KEY_SECRET, body))


def test_blank_webhook_secret_refuses_even_a_correctly_computed_signature(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "")
    body = b"{}"
    assert not razorpay_client.verify_webhook_signature(body, _sign("", body))


# ------------------------------------------------------------ the flow --


@pytest.fixture
def fees_swept():
    ids: list[str] = []
    yield ids
    with SessionLocal() as db:
        if ids:
            db.execute(delete(Fee).where(Fee.student_id.in_(ids)))  # payments cascade
        db.commit()


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


def _raise_fee(client, admin, sid, amount="1500.50"):
    r = client.post(
        "/api/admin/fees",
        headers=admin.headers,
        json={"title": "Exam fee", "amount": amount, "student_ids": [sid]},
    )
    assert r.status_code == 201, r.text


@requires_db
def test_pay_a_fee_end_to_end(client, make_user, razorpay, fees_swept):
    admin = make_user("fee-adm", Role.ADMIN)
    student = make_user("fee-stu")
    sid = _student_id(student.user_id)
    fees_swept.append(sid)
    _raise_fee(client, admin, sid)

    fees = client.get("/api/student/fees", headers=student.headers).json()
    assert len(fees) == 1 and fees[0]["amount_paise"] == 150050 and fees[0]["status"] == "PENDING"

    order = client.post(f"/api/student/fees/{fees[0]['id']}/order", headers=student.headers)
    assert order.status_code == 200, order.text
    assert order.json()["amount"] == 150050, "the charge is the fee row's amount"
    oid = order.json()["order_id"]

    bad = client.post(
        "/api/student/fees/verify",
        headers=student.headers,
        json={"razorpay_order_id": oid, "razorpay_payment_id": "pay_1", "razorpay_signature": "x" * 64},
    )
    assert bad.status_code == 400
    assert client.get("/api/student/fees", headers=student.headers).json()[0]["status"] == "PENDING"

    good = client.post(
        "/api/student/fees/verify",
        headers=student.headers,
        json={
            "razorpay_order_id": oid,
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": _sign(KEY_SECRET, f"{oid}|pay_1".encode()),
        },
    )
    assert good.status_code == 200 and good.json()["status"] == "PAID"
    # a second order on a paid fee is refused
    assert client.post(f"/api/student/fees/{fees[0]['id']}/order", headers=student.headers).status_code == 409


@requires_db
def test_another_students_fee_is_a_404_and_staff_cannot_use_student_routes(client, make_user, razorpay, fees_swept):
    admin = make_user("fee-adm2", Role.ADMIN)
    owner = make_user("fee-own")
    other = make_user("fee-oth")
    sid = _student_id(owner.user_id)
    fees_swept.extend([sid, _student_id(other.user_id)])
    _raise_fee(client, admin, sid)
    fee_id = client.get("/api/student/fees", headers=owner.headers).json()[0]["id"]

    assert client.post(f"/api/student/fees/{fee_id}/order", headers=other.headers).status_code == 404
    assert client.get("/api/student/fees", headers=other.headers).json() == []
    assert client.get("/api/student/fees", headers=admin.headers).status_code == 403
    # A VALID body: FastAPI validates the body before the handler's admin gate
    # runs, so `{}` would answer 422 and test the schema, not the gate.
    raise_fee = {"title": "x", "amount": 1, "student_ids": [sid]}
    assert client.post("/api/admin/fees", headers=owner.headers, json=raise_fee).status_code == 403


@requires_db
def test_orders_are_refused_when_razorpay_is_not_configured(client, make_user, monkeypatch, fees_swept):
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    admin = make_user("fee-adm3", Role.ADMIN)
    student = make_user("fee-stu3")
    sid = _student_id(student.user_id)
    fees_swept.append(sid)
    _raise_fee(client, admin, sid)
    fee_id = client.get("/api/student/fees", headers=student.headers).json()[0]["id"]
    assert client.post(f"/api/student/fees/{fee_id}/order", headers=student.headers).status_code == 503


def _webhook(client, secret, order_id, amount, event="payment.captured"):
    body = json.dumps(
        {"event": event, "payload": {"payment": {"entity": {"id": "pay_W1", "order_id": order_id, "amount": amount}}}}
    ).encode()
    return client.post(
        "/api/payments/razorpay/webhook",
        content=body,
        headers={"x-razorpay-signature": _sign(secret, body), "content-type": "application/json"},
    )


@requires_db
def test_webhook_settles_once_checks_amount_and_rejects_forgeries(client, make_user, razorpay, fees_swept):
    admin = make_user("fee-adm4", Role.ADMIN)
    student = make_user("fee-stu4")
    sid = _student_id(student.user_id)
    fees_swept.append(sid)
    _raise_fee(client, admin, sid, "200.00")
    fee_id = client.get("/api/student/fees", headers=student.headers).json()[0]["id"]
    oid = client.post(f"/api/student/fees/{fee_id}/order", headers=student.headers).json()["order_id"]

    assert _webhook(client, "wrong-secret", oid, 20000).status_code == 400
    _webhook(client, WEBHOOK_SECRET, oid, 100)  # wrong amount: acknowledged, ignored
    assert client.get("/api/student/fees", headers=student.headers).json()[0]["status"] == "PENDING"

    assert _webhook(client, WEBHOOK_SECRET, oid, 20000).status_code == 200
    assert _webhook(client, WEBHOOK_SECRET, oid, 20000).status_code == 200  # redelivery
    assert client.get("/api/student/fees", headers=student.headers).json()[0]["status"] == "PAID"
    with SessionLocal() as db:
        assert db.scalars(select(FeePayment).where(FeePayment.razorpay_order_id == oid)).one().status == "PAID"


@requires_db
def test_cancel_works_on_unpaid_and_hides_it_from_the_student(client, make_user, razorpay, fees_swept):
    admin = make_user("fee-adm5", Role.ADMIN)
    student = make_user("fee-stu5")
    sid = _student_id(student.user_id)
    fees_swept.append(sid)
    _raise_fee(client, admin, sid)
    fee_id = client.get("/api/student/fees", headers=student.headers).json()[0]["id"]
    assert client.post(f"/api/admin/fees/{fee_id}/cancel", headers=admin.headers).status_code == 200
    assert client.get("/api/student/fees", headers=student.headers).json() == []
    assert client.post(f"/api/student/fees/{fee_id}/order", headers=student.headers).status_code == 404


# ------------------------------------------------- pay at enrolment --

PAID_COURSE = "ZZ-PAY-1"
FREE_COURSE = "ZZ-FREE-1"


@pytest.fixture
def courses():
    """Two throwaway sem-1 courses (new students default to semester 1)."""
    from app.models.course import Course, CourseModel, Dimension, Enrollment
    from app.models.user import Stage

    with SessionLocal() as db:
        for code, fee in ((PAID_COURSE, None), (FREE_COURSE, None)):
            db.add(
                Course(
                    code=code, name=f"Test {code}", stage=next(iter(Stage)),
                    dimension=Dimension.TECHNICAL, semester=1,
                    model_type=CourseModel.INSTRUCTOR_LED, fee_paise=fee,
                )
            )
        db.commit()
    yield
    with SessionLocal() as db:
        db.execute(delete(Fee).where(Fee.course_code.in_([PAID_COURSE, FREE_COURSE])))
        db.execute(delete(Enrollment).where(Enrollment.course_code.in_([PAID_COURSE, FREE_COURSE])))
        db.execute(delete(Course).where(Course.code.in_([PAID_COURSE, FREE_COURSE])))
        db.commit()


def _enrolled(sid, code):
    from app.models.course import Enrollment

    with SessionLocal() as db:
        return db.scalar(
            select(Enrollment.id).where(Enrollment.student_id == sid, Enrollment.course_code == code)
        ) is not None


@requires_db
def test_a_free_course_enrols_at_once(client, make_user, razorpay, courses, fees_swept):
    student = make_user("enr-free")
    sid = _student_id(student.user_id)
    fees_swept.append(sid)
    r = client.post(f"/api/student/courses/{FREE_COURSE}/enroll", headers=student.headers)
    assert r.status_code == 200 and r.json() == {"enrolled": True, "order": None}
    assert _enrolled(sid, FREE_COURSE)
    assert client.post(f"/api/student/courses/{FREE_COURSE}/enroll", headers=student.headers).status_code == 409


@requires_db
def test_a_priced_course_enrols_only_when_the_payment_is_confirmed(client, make_user, razorpay, courses, fees_swept):
    admin = make_user("enr-adm", Role.ADMIN)
    student = make_user("enr-stu")
    sid = _student_id(student.user_id)
    fees_swept.append(sid)

    assert client.put(f"/api/admin/courses/{PAID_COURSE}/fee", headers=student.headers, json={"amount": "2500"}).status_code == 403
    assert client.put(f"/api/admin/courses/{PAID_COURSE}/fee", headers=admin.headers, json={"amount": "2500"}).status_code == 200

    avail = {c["code"]: c for c in client.get("/api/student/courses/available", headers=student.headers).json()}
    assert avail[PAID_COURSE]["fee_paise"] == 250000

    r = client.post(f"/api/student/courses/{PAID_COURSE}/enroll", headers=student.headers).json()
    assert r["enrolled"] is False and r["order"]["amount"] == 250000
    assert not _enrolled(sid, PAID_COURSE), "no seat before the money is confirmed"

    # clicking again reuses the open fee instead of stacking another
    again = client.post(f"/api/student/courses/{PAID_COURSE}/enroll", headers=student.headers).json()
    fees = [f for f in client.get("/api/student/fees", headers=student.headers).json() if f["course_code"] == PAID_COURSE]
    assert len(fees) == 1

    oid = again["order"]["order_id"]
    ok = client.post(
        "/api/student/fees/verify",
        headers=student.headers,
        json={
            "razorpay_order_id": oid,
            "razorpay_payment_id": "pay_E1",
            "razorpay_signature": _sign(KEY_SECRET, f"{oid}|pay_E1".encode()),
        },
    )
    assert ok.status_code == 200
    assert _enrolled(sid, PAID_COURSE)
    # the webhook arriving afterwards changes nothing and does not double-enrol
    assert _webhook(client, WEBHOOK_SECRET, oid, 250000).status_code == 200
    assert client.post(f"/api/student/courses/{PAID_COURSE}/enroll", headers=student.headers).status_code == 409
