"""The public form is compulsory end to end (2026-09-16), the files included
(2026-09-22).

The owner's rule: every box on `/register` is required except Specialization,
and the CV and the photo with them. Pinned here — USN, phone, the personal
address and the LinkedIn profile are refused blank or absent at the schema,
the two contact columns are written and travel to the reviewer (and never to
the applicant), approval copies phone and LinkedIn onto the profile, and —
since 2026-09-22 — `POST /register` is ONE multipart request that refuses an
application without both files, so the office's queue can no longer fill with
applications that have neither. The reviewer's checklist still names a
missing CV or photo, but only a row written before that rule can carry one.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app import document_store
from app.db import SessionLocal
from app.models.auth_token import AuthToken
from app.models.job import DegreeLevel
from app.models.registration import Registration, RegistrationStatus
from app.models.student_profile import StudentProfile
from app.models.user import LoginDay, Role, Student, User
from app.routers import registration as registration_router
from app.routers.registration import CHECK_DOCUMENTS, CHECK_OK, CHECK_WARN
from conftest import application_files, requires_db

PDF = b"%PDF-1.4\n% a cv\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"\x00" * 64


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    monkeypatch.setattr(registration_router, "_rate_windows", {})
    return tmp_path


@pytest.fixture
def cleanup():
    emails: list[str] = []
    yield emails
    with SessionLocal() as db:
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                for stu in db.scalars(select(Student).where(Student.user_id == user.id)).all():
                    db.execute(delete(StudentProfile).where(StudentProfile.student_id == stu.id))
                    db.execute(delete(Student).where(Student.id == stu.id))
                db.execute(delete(LoginDay).where(LoginDay.user_id == user.id))
                db.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
            db.execute(delete(Registration).where(Registration.email == email))
        db.commit()


def _payload(email: str, **override) -> dict:
    body = {
        "name": "Required Applicant",
        "email": email,
        "usn": f"1BG26REQ{uuid.uuid4().hex[:3].upper()}",
        "phone": "+91 98765 43210",
        "personal_email": f"req.{uuid.uuid4().hex[:8]}@gmail.com",
        "linkedin_url": "linkedin.com/in/required-applicant",
        "degree_level": "PG",
    }
    body.update(override)
    return {k: v for k, v in body.items() if v is not ...}


def _post(client, email: str, **override):
    """`POST /api/register` as the form sends it: multipart, both files."""
    return client.post("/api/register", data=_payload(email, **override), files=application_files())


def _address(cleanup) -> str:
    email = f"req.{uuid.uuid4().hex[:8]}@bgscet.ac.in"
    cleanup.append(email)
    return email


@requires_db
@pytest.mark.parametrize("field", ["usn", "phone", "personal_email", "linkedin_url"])
def test_each_required_field_is_refused_absent_and_blank(client, cleanup, field):
    """Absent and blank are the same refusal: a form that pads a box with a
    space must not be the one application in the queue with no phone."""
    email = _address(cleanup)
    absent = _post(client, email, **{field: ...})
    assert absent.status_code == 422, absent.text
    blank = _post(client, email, **{field: "   "})
    assert blank.status_code == 422, blank.text


@requires_db
def test_the_personal_email_and_linkedin_have_a_shape(client, cleanup):
    email = _address(cleanup)
    r = _post(client, email, personal_email="not-an-address")
    assert r.status_code == 422, r.text
    r = _post(client, email, linkedin_url="instagram.com/someone")
    assert r.status_code == 422, r.text
    r = _post(client, email, linkedin_url="https://www.linkedin.com/")
    assert r.status_code == 422, "a bare linkedin.com with no profile path names nobody"


def test_linkedin_accepts_the_links_the_app_hands_out():
    """2026-09-29: `in.linkedin.com`, `m.linkedin.com` and `lnkd.in` links were
    refused 422, and the form showed that only as "(422)". A subdomain is the
    same profile and is folded onto www; a short link is kept as given."""
    shape = registration_router.RegisterIn._linkedin_shape
    assert shape("https://in.linkedin.com/in/asha-rao") == "https://www.linkedin.com/in/asha-rao"
    assert shape("m.linkedin.com/in/asha-rao/") == "https://www.linkedin.com/in/asha-rao"
    assert shape("https://www.linkedin.com/in/asha?utm_source=share") == "https://www.linkedin.com/in/asha"
    assert shape("https://lnkd.in/gAbC12x") == "https://lnkd.in/gAbC12x"
    for refused in ("notlinkedin.com/in/asha", "linkedin.com.evil.example/in/asha", "lnkd.in/", "in.linkedin.com"):
        with pytest.raises(ValueError):
            shape(refused)


@requires_db
@pytest.mark.parametrize("college", ["same", "gmail"])
def test_a_personal_address_in_the_college_box_is_refused_in_words(client, cleanup, college):
    """2026-09-29: the college box autofilled with the student's Gmail, so the
    office saw only that address and approval could never admit it. Both
    certain mistakes are a 422 naming the box, with no application written."""
    personal = f"req.{uuid.uuid4().hex[:8]}@gmail.com"
    email = personal if college == "same" else f"other.{uuid.uuid4().hex[:8]}@gmail.com"
    cleanup.append(email)
    r = _post(client, email, personal_email=personal)
    assert r.status_code == 422, r.text
    assert "college email" in r.json()["detail"]
    with SessionLocal() as db:
        assert db.scalar(select(Registration).where(Registration.email == email)) is None


@requires_db
def test_an_address_merely_off_the_domain_list_still_reaches_the_reviewer(client, cleanup):
    """Only the two certain mistakes are refused at the form: a college whose
    domains are not listed yet must not lose every applicant."""
    email = f"req.{uuid.uuid4().hex[:8]}@some-college.example"
    cleanup.append(email)
    r = _post(client, email)
    assert r.status_code == 201, r.text


@requires_db
def test_the_contact_fields_are_stored_normalised_and_shown_to_staff_only(client, make_user, cleanup):
    admin = make_user("req-staff", Role.ADMIN)
    email = _address(cleanup)
    r = _post(
        client,
        email,
        usn=" 1BG26REQX01 ",
        phone=" +91 98765 43210 ",
        personal_email="Req.Person@Gmail.com",
        linkedin_url="http://linkedin.com/in/Req-Person/",
    )
    assert r.status_code == 201, r.text
    public = r.json()
    for private in ("phone", "personal_email", "linkedin_url"):
        assert private not in public, f"{private} must not travel on the applicant's own card"

    with SessionLocal() as db:
        reg = db.scalar(select(Registration).where(Registration.email == email))
        assert reg is not None
        assert reg.usn == "1BG26REQX01"
        assert reg.phone == "+91 98765 43210"
        assert reg.personal_email == "req.person@gmail.com"
        assert reg.linkedin_url == "https://www.linkedin.com/in/Req-Person"

    queue = client.get("/api/register/pending", headers=admin.headers)
    assert queue.status_code == 200, queue.text
    row = next(x for x in queue.json() if x["id"] == public["id"])
    assert row["phone"] == "+91 98765 43210"
    assert row["personal_email"] == "req.person@gmail.com"
    assert row["linkedin_url"] == "https://www.linkedin.com/in/Req-Person"


@requires_db
def test_an_application_cannot_exist_without_its_cv_and_photo(client, cleanup, _tmp_store):
    """THE FIX FOR A QUEUE FULL OF APPLICATIONS WITH NO FILES (2026-09-22).

    The form required both files and posted them AFTER the 201, so every way
    that second half could fail — and it failed by design for every
    application a rule auto-approved — left an application in the queue from
    a student who had filled in every box. Now the files are judged before a
    row exists: a missing file is a 422, a wrong one a 415, the old JSON shape
    a 422, and none of them leaves an application or a byte behind, so the
    applicant retries the same form and never meets the duplicate guard.
    """
    email = _address(cleanup)

    no_photo = client.post("/api/register", data=_payload(email), files={"cv": application_files()["cv"]})
    assert no_photo.status_code == 422, no_photo.text
    assert "photo" in no_photo.text

    png_as_cv = client.post("/api/register", data=_payload(email), files=application_files(cv=PNG))
    assert png_as_cv.status_code == 415, png_as_cv.text
    assert "PDF" in png_as_cv.text

    old_shape = client.post("/api/register", json=_payload(email))
    assert old_shape.status_code == 422, old_shape.text

    with SessionLocal() as db:
        assert db.scalar(select(Registration).where(Registration.email == email)) is None, (
            "a refused submission writes no application"
        )
    assert list(_tmp_store.iterdir()) == [], "and stores no bytes"

    created = _post(client, email)
    assert created.status_code == 201, created.text
    assert created.json()["documents"] == ["CV", "PHOTO"], "both files are on the row from the 201"
    assert len(list(_tmp_store.iterdir())) == 2


@requires_db
def test_the_reviewer_is_told_which_document_is_missing_on_a_row_from_before(client, make_user, cleanup):
    """`POST /register` refuses an application without both files now, so a
    row without them can only be one written BEFORE 2026-09-22 (or one whose
    upload failed after the 201 back then). The checklist still names the gap
    on such a row, and goes green once the replacement path has both."""
    admin = make_user("req-docs", Role.ADMIN)
    email = _address(cleanup)
    with SessionLocal() as db:
        legacy = Registration(
            name="Legacy Applicant",
            email=email,
            usn=f"1BG26REQ{uuid.uuid4().hex[:3].upper()}",
            phone="+91 98765 43210",
            personal_email=f"legacy.{uuid.uuid4().hex[:8]}@gmail.com",
            linkedin_url="https://www.linkedin.com/in/legacy-applicant",
            degree_level=DegreeLevel.PG,
            status=RegistrationStatus.PENDING_REVIEW,
            decision_reason="No rule matched — needs manual review.",
        )
        db.add(legacy)
        db.commit()
        reg_id = legacy.id

    def documents_check() -> dict:
        queue = client.get("/api/register/pending", headers=admin.headers)
        assert queue.status_code == 200, queue.text
        row = next(x for x in queue.json() if x["id"] == reg_id)
        return {c["key"]: c for c in row["checks"]}[CHECK_DOCUMENTS]

    both_missing = documents_check()
    assert both_missing["status"] == CHECK_WARN
    assert "CV" in both_missing["label"] and "photo" in both_missing["label"].lower()

    cv = client.post(
        f"/api/register/{reg_id}/documents/cv",
        files={"file": ("cv.pdf", PDF, "application/octet-stream")},
    )
    assert cv.status_code == 200, cv.text
    photo_missing = documents_check()
    assert photo_missing["status"] == CHECK_WARN
    assert "photo" in photo_missing["label"].lower() and "CV" not in photo_missing["label"]

    photo = client.post(
        f"/api/register/{reg_id}/documents/photo",
        files={"file": ("me.png", PNG, "application/octet-stream")},
    )
    assert photo.status_code == 200, photo.text
    assert documents_check()["status"] == CHECK_OK


@requires_db
def test_approval_copies_phone_and_linkedin_onto_the_profile(client, make_user, cleanup):
    admin = make_user("req-approve", Role.ADMIN)
    email = _address(cleanup)
    created = _post(client, email, linkedin_url="linkedin.com/in/copied-over")
    assert created.status_code == 201, created.text
    decided = client.post(
        f"/api/register/{created.json()['id']}/decision",
        headers=admin.headers,
        json={"decision": "APPROVE"},
    )
    assert decided.status_code == 200, decided.text

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        assert student is not None
        profile = db.scalar(select(StudentProfile).where(StudentProfile.student_id == student.id))
        assert profile is not None
        assert profile.phone == "+91 98765 43210"
        assert profile.linkedin_url == "https://www.linkedin.com/in/copied-over"
