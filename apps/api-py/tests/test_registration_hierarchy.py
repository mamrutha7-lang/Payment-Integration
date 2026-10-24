"""Where the applicant says they belong: the register form's hierarchy pickers.

The public form now lets an applicant pick College -> Department -> (Course) ->
(Specialization) -> (Batch) from what the admin built. Pinned here: the public
projection carries structure and nothing private; the claim is derived UPWARD
from the deepest level named and a contradiction is refused, never silently
picked; the queue shows the claim by name; and at approval the RULE's batch
wins while the applicant's batch fills the gap when no rule seats them.

Fixtures borrowed by name: `chain` / `levels` / `tracker` / `director` / `_code`
from test_admin_institution.py (a College -> Department -> Batch -> Course ->
Specialization built THROUGH the admin API and torn down innermost-first), and
the mail helpers + throttle reset from test_passwords.py.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import application_files, requires_db
from test_admin_institution import (  # noqa: F401 — fixtures by name
    _code,
    chain,
    director,
    levels,
    tracker,
)
from test_passwords import (  # noqa: F401
    _clean_outbox_and_throttles,
    _mail_to,
    _token_from,
)

from app.db import SessionLocal
from app.models.registration import Registration, RegistrationRule
from app.models.student_profile import StudentProfile
from app.models.user import Student, User
from app.routers import registration as registration_router

TAG = uuid.uuid4().hex[:6]


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    monkeypatch.setattr(registration_router, "_rate_windows", {})


@pytest.fixture
def applicant(tmp_document_store):
    """POST /api/register with any extra fields, and tear down whatever it and
    an approval created — the same sweep test_passwords' `application` does,
    reimplemented because that fixture's payload has no room for the claim.
    Multipart with both files, as the form posts it (2026-09-22)."""
    emails: list[str] = []
    rules: list[str] = []

    def post(client, email: str, *, usn: str | None = None, **claim):
        emails.append(email)
        with SessionLocal() as db:
            db.execute(delete(Registration).where(Registration.email == email))
            db.commit()
        # USN, phone, personal email and LinkedIn are required at the schema
        # (2026-09-16); a caller that names no USN gets a unique one.
        return client.post(
            "/api/register",
            data={
                "name": "Hierarchy Applicant",
                "email": email,
                "usn": usn or f"1BG26HIE{uuid.uuid4().hex[:3].upper()}",
                "phone": "+91 90000 00000",
                "personal_email": f"personal.{uuid.uuid4().hex[:8]}@gmail.com",
                "linkedin_url": "https://www.linkedin.com/in/hierarchy-applicant",
                "degree_level": "PG",
                **claim,
            },
            files=application_files(),
        )

    def rule(**kw) -> str:
        with SessionLocal() as db:
            r = RegistrationRule(**kw)
            db.add(r)
            db.commit()
            rules.append(r.id)
            return r.id

    yield post, rule
    with SessionLocal() as db:
        for rid in rules:
            db.execute(delete(RegistrationRule).where(RegistrationRule.id == rid))
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                for stu in db.scalars(select(Student).where(Student.user_id == user.id)).all():
                    db.execute(delete(StudentProfile).where(StudentProfile.student_id == stu.id))
                    db.execute(delete(Student).where(Student.id == stu.id))
                db.execute(delete(User).where(User.id == user.id))
            db.execute(delete(Registration).where(Registration.email == email))
        db.commit()


def _verify(client, email: str) -> str:
    """Just the application id now. The confirmation hop this used to walk is
    gone — submission reaches the review queue directly (2026-09-10)."""
    with SessionLocal() as db:
        return db.scalar(select(Registration.id).where(Registration.email == email))


# ------------------------------------------------------ the public picker --


@requires_db
def test_the_public_hierarchy_lists_the_admins_chain_and_nothing_private(client, levels):
    r = client.get("/api/register/hierarchy")  # no auth: it is the public form's
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(lv["key"], lv["required"]) for lv in body["levels"]] == [
        ("college", True), ("department", True), ("course", False), ("specialization", False), ("batch", False),
    ], "College and Department always; Course/Specialization from HIERARCHY_LEVELS; Batch never"

    college = next(c for c in body["colleges"] if c["id"] == levels["college"]["id"])
    assert set(college) == {"id", "code", "name", "departments"}, "names and codes only - no campus, no contact"
    dept = next(d for d in college["departments"] if d["id"] == levels["department"]["id"])
    assert set(dept) == {"id", "code", "name", "courses", "batches"}, "no head of department"
    course = next(c for c in dept["courses"] if c["id"] == levels["course"]["id"])
    assert [s["id"] for s in course["specializations"]] == [levels["specialization"]["id"]]
    batch = next(b for b in dept["batches"] if b["id"] == levels["cohort"]["id"])
    assert batch["batch_label"] == "2024-26" and batch["degree_level"] == "PG"
    assert batch["current"] is False, "the chain batch ended 2026-07-31; listed but marked, not hidden"

    blob = r.text.lower()
    for forbidden in ("usn", '"head"', "contact", "student", "campus"):
        assert forbidden not in blob, f"{forbidden!r} leaked into the public projection"


# ------------------------------------------------------ deriving the claim --


@requires_db
def test_a_batch_pins_its_department_and_college(client, applicant, chain):
    post, _ = applicant
    r = post(client, f"hc.batch.{TAG}@bgscet.ac.in", usn="1BG26HCB01", requested_cohort_id=chain["cohort"]["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["requested_cohort_id"] == chain["cohort"]["id"]
    assert body["department_id"] == chain["department"]["id"], "derived from the batch"
    assert body["college_id"] == chain["college"]["id"], "derived from the department"
    assert body["course_id"] is None and body["specialization_id"] is None, "the batch has neither"
    assert body["college_name"] == "Chain College" and body["department_name"] == "Chain Department"
    assert "Chain Batch" in body["requested_batch"] and "2024-26" in body["requested_batch"]
    assert body["cohort_id"] is None, "the RULE's cohort is a separate column, untouched by the claim"


@requires_db
def test_a_specialization_pins_course_department_and_college(client, applicant, levels):
    post, _ = applicant
    r = post(client, f"hc.spec.{TAG}@bgscet.ac.in", usn="1BG26HCS01", specialization_id=levels["specialization"]["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["specialization_id"] == levels["specialization"]["id"]
    assert body["course_id"] == levels["course"]["id"]
    assert body["department_id"] == levels["department"]["id"]
    assert body["college_id"] == levels["college"]["id"]
    assert body["specialization_name"] == "Finance" and body["course_name"].startswith("Master of Business")


@requires_db
def test_a_contradiction_is_refused_by_name_and_an_unknown_id_too(client, applicant, chain, tracker):
    post, _ = applicant
    h = chain["headers"]
    other_college = client.post("/api/admin/colleges", headers=h, json={"code": _code("OC"), "name": "Other College"}).json()
    tracker["colleges"].append(other_college["id"])
    other_dept = client.post(
        f"/api/admin/colleges/{other_college['id']}/departments", headers=h, json={"code": _code("OD"), "name": "Other Department"}
    ).json()
    tracker["departments"].append(other_dept["id"])

    # The batch sits under Chain Department; naming Other Department alongside it is a contradiction.
    r = post(client, f"hc.contra.{TAG}@bgscet.ac.in", usn="1BG26HCC01",
             requested_cohort_id=chain["cohort"]["id"], department_id=other_dept["id"])
    assert r.status_code == 422, r.text
    assert "department" in r.text.lower() and "batch" in r.text.lower(), "the 422 names both sides"

    # A department under one college, named alongside a different college.
    r = post(client, f"hc.contra2.{TAG}@bgscet.ac.in", usn="1BG26HCC02",
             department_id=chain["department"]["id"], college_id=other_college["id"])
    assert r.status_code == 422, r.text
    assert "college" in r.text.lower()

    # An id that is not there at all.
    r = post(client, f"hc.unknown.{TAG}@bgscet.ac.in", usn="1BG26HCC03", college_id="no-such-college")
    assert r.status_code == 422, r.text
    assert "does not exist" in r.text

    with SessionLocal() as db:
        assert db.scalar(select(Registration).where(Registration.email.like(f"hc.contra%{TAG}%"))) is None, (
            "a refused application writes no row"
        )


# ------------------------------------------------------------- the queue --


@requires_db
def test_the_queue_shows_the_claim_by_name(client, applicant, chain):
    post, _ = applicant
    email = f"hc.queue.{TAG}@bgscet.ac.in"
    assert post(client, email, usn="1BG26HCQ01", requested_cohort_id=chain["cohort"]["id"]).status_code == 201
    reg_id = _verify(client, email)
    rows = client.get("/api/register/pending", headers=chain["headers"]).json()
    row = next(x for x in rows if x["id"] == reg_id)
    assert row["college_name"] == "Chain College"
    assert row["department_name"] == "Chain Department"
    assert "Chain Batch" in row["requested_batch"]


# ------------------------------------------------------------- approval --


@requires_db
def test_approval_seats_the_student_in_the_requested_batch_unless_a_rule_says_otherwise(client, applicant, chain, tracker):
    post, rule = applicant
    h = chain["headers"]

    # (a) No rule seats them: the batch they asked for is where they land.
    email_a = f"hc.seat.{TAG}@bgscet.ac.in"
    assert post(client, email_a, usn="1BG26HSA01", requested_cohort_id=chain["cohort"]["id"]).status_code == 201
    reg_a = _verify(client, email_a)
    r = client.post(f"/api/register/{reg_a}/decision", headers=h, json={"decision": "APPROVE"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email_a))
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        assert student.cohort_id == chain["cohort"]["id"], "seated where they asked"

    # (a2) The department they named survives even with no batch to seat them
    # in. This is the ordinary case at a college that has not built its batches:
    # the form REQUIRES College and Department and makes Batch optional, and
    # provisioning used to keep only the batch — so the one fact the applicant
    # was forced to give was the one fact thrown away, and they were admitted
    # with no institution at all (31f7a4c60b12).
    email_d = f"hc.dept.{TAG}@bgscet.ac.in"
    assert post(
        client, email_d, usn=f"1BG26HSD{TAG[:2].upper()}1",
        college_id=chain["college"]["id"], department_id=chain["department"]["id"],
    ).status_code == 201
    reg_d = _verify(client, email_d)
    r = client.post(f"/api/register/{reg_d}/decision", headers=h, json={"decision": "APPROVE"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email_d))
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        assert student.cohort_id is None, "they asked for no batch, and there need not be one"
        assert student.department_id == chain["department"]["id"], (
            "the department the form REQUIRED is what approval keeps"
        )

    # (b) A rule seats them somewhere else: policy wins over the claim.
    other_batch = client.post(
        f"/api/admin/departments/{chain['department']['id']}/cohorts", headers=h,
        json={"code": _code("RUL"), "name": "Rule Batch", "batch_label": "2026-28", "degree_level": "PG",
              "entry_date": "2026-08-01", "expected_completion": "2028-07-31"},
    ).json()
    tracker["cohorts"].append(other_batch["id"])
    rule(name=f"seat-{TAG}", enabled=True, email_domain=None, usn_pattern=f"^1BG26HSR{TAG[:2].upper()}",
         degree_level=None, cohort_id=other_batch["id"], auto_approve=False, priority=0)
    email_b = f"hc.rule.{TAG}@bgscet.ac.in"
    usn_b = f"1BG26HSR{TAG[:2].upper()}9"
    assert post(client, email_b, usn=usn_b, requested_cohort_id=chain["cohort"]["id"]).status_code == 201
    reg_b = _verify(client, email_b)
    with SessionLocal() as db:
        reg = db.get(Registration, reg_b)
        assert reg.cohort_id == other_batch["id"], "the rule stamped its batch at verification"
        assert reg.requested_cohort_id == chain["cohort"]["id"], "and the claim is still on record"
    r = client.post(f"/api/register/{reg_b}/decision", headers=h, json={"decision": "APPROVE"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email_b))
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        assert student.cohort_id == other_batch["id"], "the rule's batch wins"
