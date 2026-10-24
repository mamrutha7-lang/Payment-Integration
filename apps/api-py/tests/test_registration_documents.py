"""Registration documents and the queue's Undo — the backend behind the design's
register form and approval screen.

An applicant attaches a CV (PDF) and a photo (PNG/JPG) BEFORE anyone decides;
the review queue shows what is attached; APPROVE moves the files into the
student's own uploads; a rejection can be reopened (the design's Undo) and an
approval cannot; a never-verified application is swept with its files.

Every byte these tests write lands in tmp_path — the real store is never
touched. The `application` fixture and the mail helpers are test_passwords.py's,
imported by name so pytest sees them here; its autouse throttle reset rides in
the same way, because every TestClient request shares one client address and
the public endpoints are limited per address.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from conftest import requires_db
from test_passwords import (  # noqa: F401 — fixtures/helpers by name
    _clean_outbox_and_throttles,
    _mail_to,
    _token_from,
    application,
)

from app import document_store
from app.routers import registration as registration_router
from app.db import SessionLocal
from app.models.registration import Registration, RegistrationDocument, RegistrationStatus
from app.models.upload import Upload, UploadKind
from app.models.user import Role, Student, User

# The store recognises files by magic bytes, nothing more — enough to be a PDF,
# a PNG, a JPEG, or nothing at all.
PDF = b"%PDF-1.4\n% a cv\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
PDF_2 = b"%PDF-1.7\n% a newer cv\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
JUNK = b"this is not a document" * 4


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    """Point the document store at tmp_path, and give the public register
    limiter a clean window: every TestClient request shares one address, and
    these tests post more than a real applicant would in fifteen minutes."""
    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    monkeypatch.setattr(registration_router, "_rate_windows", {})
    return tmp_path


def _submit(client, submit, email, usn, *, verify: bool = False):
    """`verify` is vestigial (2026-09-10) and kept only so call sites read the
    same: submission now reaches the review queue with no email hop at all, so
    there is nothing left to confirm before a document may be attached."""
    r = submit(client, email, usn=usn)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _attach(client, reg_id: str, kind: str, name: str, content: bytes):
    return client.post(
        f"/api/register/{reg_id}/documents/{kind}",
        files={"file": (name, content, "application/octet-stream")},
    )


def _docs(reg_id: str) -> list[RegistrationDocument]:
    with SessionLocal() as db:
        return db.scalars(
            select(RegistrationDocument).where(RegistrationDocument.registration_id == reg_id)
        ).all()


# ------------------------------------------------------------ attaching --


@requires_db
def test_an_applicant_can_attach_a_cv_and_a_photo_and_replace_them(client, application, _tmp_store):
    """Both files arrive WITH the application (2026-09-22), so `_attach` is
    the replacement path from the first call: one row per kind, old bytes
    deleted, and the queue's list of kinds never changes."""
    submit, _ = application
    reg_id = _submit(client, submit, "rd.attach@bgscet.ac.in", "1BG26RD01", verify=False)
    assert {d.kind for d in _docs(reg_id)} == {"CV", "PHOTO"}, "on the row from the 201"

    r = _attach(client, reg_id, "cv", "my-cv.pdf", PDF)
    assert r.status_code == 200, r.text
    assert r.json()["documents"] == ["CV", "PHOTO"]
    cv = next(d for d in _docs(reg_id) if d.kind == "CV")
    assert cv.mime_type == "application/pdf" and cv.original_name == "my-cv.pdf"
    assert (_tmp_store / cv.stored_name).exists(), "the bytes must actually be on disk"

    r = _attach(client, reg_id, "photo", "me.png", PNG)
    assert r.status_code == 200, r.text
    assert r.json()["documents"] == ["CV", "PHOTO"]

    # A second CV REPLACES the first: still one row of that kind, old bytes gone.
    r = _attach(client, reg_id, "cv", "my-cv-v2.pdf", PDF_2)
    assert r.status_code == 200, r.text
    assert r.json()["documents"] == ["CV", "PHOTO"]
    docs = {d.kind: d for d in _docs(reg_id)}
    assert set(docs) == {"CV", "PHOTO"}, "replace, not a second CV"
    assert docs["CV"].original_name == "my-cv-v2.pdf"
    assert not (_tmp_store / cv.stored_name).exists(), "the replaced file is deleted"
    assert (_tmp_store / docs["CV"].stored_name).exists()


@requires_db
def test_a_cv_must_be_a_pdf_and_a_photo_an_image(client, application, _tmp_store):
    submit, _ = application
    reg_id = _submit(client, submit, "rd.types@bgscet.ac.in", "1BG26RD02", verify=False)

    r = _attach(client, reg_id, "cv", "not-a-cv.png", PNG)
    assert r.status_code == 415, r.text
    assert "PDF" in r.text
    r = _attach(client, reg_id, "photo", "not-a-photo.pdf", PDF)
    assert r.status_code == 415, r.text
    r = _attach(client, reg_id, "cv", "junk.bin", JUNK)
    assert r.status_code == 415, r.text
    r = _attach(client, reg_id, "transcript", "x.pdf", PDF)
    assert r.status_code == 404, "only the two known kinds exist"

    assert {d.kind for d in _docs(reg_id)} == {"CV", "PHOTO"}, "a refused file leaves the rows as they were"
    assert len(list(_tmp_store.iterdir())) == 2, "and adds no bytes on disk"


@requires_db
def test_no_document_can_be_added_after_a_decision(client, make_user, application):
    submit, _ = application
    director = make_user("rd-dir1", Role.ADMIN)
    reg_id = _submit(client, submit, "rd.decided@bgscet.ac.in", "1BG26RD03", verify=True)
    r = client.post(
        f"/api/register/{reg_id}/decision",
        headers=director.headers,
        json={"decision": "REJECT", "note": "incomplete"},
    )
    assert r.status_code == 200, r.text
    r = _attach(client, reg_id, "cv", "late.pdf", PDF)
    assert r.status_code == 409, r.text


# --------------------------------------------------------- the queue --


@requires_db
def test_the_queue_reports_what_is_attached(client, make_user, application):
    submit, _ = application
    director = make_user("rd-dir2", Role.ADMIN)
    reg_id = _submit(client, submit, "rd.queue@bgscet.ac.in", "1BG26RD04", verify=True)
    assert _attach(client, reg_id, "cv", "cv.pdf", PDF).status_code == 200

    r = client.get("/api/register/pending", headers=director.headers)
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["id"] == reg_id)
    assert row["documents"] == ["CV", "PHOTO"]


# ----------------------------------------------------------- approve --


@requires_db
def test_documents_move_into_the_students_uploads_on_approve(client, make_user, application, _tmp_store):
    submit, _ = application
    director = make_user("rd-dir3", Role.ADMIN)
    email = "rd.approve@bgscet.ac.in"
    reg_id = _submit(client, submit, email, "1BG26RD05", verify=True)
    assert _attach(client, reg_id, "cv", "cv.pdf", PDF).status_code == 200
    assert _attach(client, reg_id, "photo", "me.jpg", JPG).status_code == 200
    before = {d.kind: d.stored_name for d in _docs(reg_id)}

    r = client.post(
        f"/api/register/{reg_id}/decision", headers=director.headers, json={"decision": "APPROVE"}
    )
    assert r.status_code == 200, r.text

    assert _docs(reg_id) == [], "moved off the application"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        uploads = {u.kind: u for u in db.scalars(select(Upload).where(Upload.student_id == student.id)).all()}
    assert set(uploads) == {UploadKind.RESUME, UploadKind.PROFILE_PHOTO}
    assert uploads[UploadKind.RESUME].stored_name == before["CV"], "same bytes, not a copy"
    assert uploads[UploadKind.PROFILE_PHOTO].stored_name == before["PHOTO"]
    for name in before.values():
        assert (_tmp_store / name).exists(), "the file survives the move"


@requires_db
def test_an_auto_approved_application_keeps_its_files(client, application, _tmp_store):
    """THE CASE THAT WAS FAILING BY DESIGN. A rule that auto-approves decides
    the application at submit time; the two uploads that used to follow the
    201 were then refused by `attach_document`'s decided-application check,
    so every auto-admitted student started with no resume and no photo. With
    the files in the creating request they exist before the rule runs, and
    `_provision_student` moves them onto the new student's uploads."""
    submit, rule = application
    tag = uuid.uuid4().hex[:4].upper()
    rule(name=f"rd-auto-{tag}", enabled=True, email_domain=None, usn_pattern=f"^1BG26RDA{tag}",
         degree_level=None, cohort_id=None, auto_approve=True, priority=0)
    email = f"rd.auto.{tag.lower()}@bgscet.ac.in"
    created = submit(client, email, usn=f"1BG26RDA{tag}1")
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "AUTO_APPROVED", created.text
    assert created.json()["documents"] == [], "moved off the application at approval"

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        uploads = {u.kind: u for u in db.scalars(select(Upload).where(Upload.student_id == student.id)).all()}
    assert set(uploads) == {UploadKind.RESUME, UploadKind.PROFILE_PHOTO}
    for u in uploads.values():
        assert (_tmp_store / u.stored_name).exists(), "the file survives the move"


# -------------------------------------------------------------- undo --


@requires_db
def test_a_rejection_can_be_reopened_and_an_approval_cannot(client, make_user, application):
    submit, _ = application
    director = make_user("rd-dir4", Role.ADMIN)
    student = make_user("rd-stu4")  # STUDENT: must be refused
    a = _submit(client, submit, "rd.reopen.a@bgscet.ac.in", "1BG26RD06", verify=True)
    b = _submit(client, submit, "rd.reopen.b@bgscet.ac.in", "1BG26RD07", verify=True)
    assert _attach(client, a, "cv", "cv.pdf", PDF).status_code == 200

    r = client.post(
        f"/api/register/{a}/decision",
        headers=director.headers,
        json={"decision": "REJECT", "note": "photo missing"},
    )
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert _docs(a) != [], "rejection keeps the documents, so undo is lossless"

    r = client.post(f"/api/register/{a}/reopen", headers=director.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "PENDING_REVIEW"
    assert body["reviewed_by_id"] is None and body["review_note"] is None
    assert body["documents"] == ["CV", "PHOTO"]
    queue = client.get("/api/register/pending", headers=director.headers).json()
    assert any(x["id"] == a for x in queue), "back in the queue"

    r = client.post(f"/api/register/{b}/decision", headers=director.headers, json={"decision": "APPROVE"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/register/{b}/reopen", headers=director.headers)
    assert r.status_code == 409, "an approval provisioned an account; not undone here"

    assert client.post(f"/api/register/{uuid.uuid4().hex}/reopen", headers=director.headers).status_code == 404
    assert client.post(f"/api/register/{a}/reopen", headers=student.headers).status_code == 403
    assert client.post(f"/api/register/{a}/reopen", headers=director.headers).status_code == 409, (
        "already open — reopening twice is a conflict, not a no-op that hides a race"
    )


# ------------------------------------------------------------- sweep --


# --------------------------------------------------------------------------- #
# The public endpoints answer with the APPLICANT's view, not the reviewer's
# --------------------------------------------------------------------------- #

#: Written by staff, ABOUT the applicant, FOR colleagues. None of it may leave
#: an endpoint that nobody had to sign in to reach.
REVIEWER_FIELDS = ("review_note", "reviewed_by_id", "reviewed_at")
#: Internal plumbing the applicant's result card never renders.
INTERNAL_FIELDS = ("matched_rule_id", "approved_student_id")


@requires_db
def test_the_public_endpoints_never_carry_the_reviewers_side(client, application, _tmp_store):
    """Both public endpoints on this router answer with `PublicRegistrationOut`.

    Neither caller is signed in. The bearer is the application id — an
    unguessable uuid4 handed back on the 201 — which is enough to let someone
    finish their own application and is not enough to be shown what a reviewer
    wrote about them.

    THIS IS A SHAPE, NOT A FILTER, and that distinction is the whole point.
    Today the reviewer's stamp could not travel anyway: uploads are refused once
    an application is decided, and `reopen` clears the note on the way back into
    the queue. Both of those are one small edit away from being untrue, and the
    edit that breaks them will not look like it touches a public response. A
    schema that never declared the field cannot start carrying it by accident.
    """
    submit, _ = application
    email = f"public-view-{uuid.uuid4().hex[:6]}@bgscet.ac.in"
    created = submit(client, email)
    assert created.status_code == 201, created.text
    body = created.json()

    for field in REVIEWER_FIELDS + INTERNAL_FIELDS:
        assert field not in body, f"POST /register leaked {field} to an unauthenticated caller"

    # ...and the applicant's own facts ARE there, so this cannot pass by
    # answering with an empty object.
    assert body["email"] == email
    assert body["status"] in {"PENDING_REVIEW", "AUTO_APPROVED"}
    assert "documents" in body and "decision_reason" in body

    # The upload endpoint answers with the same narrow view.
    up = _attach(client, body["id"], "cv", "my-cv.pdf", PDF)
    assert up.status_code == 200, up.text
    for field in REVIEWER_FIELDS + INTERNAL_FIELDS:
        assert field not in up.json(), f"the upload endpoint leaked {field}"
    assert up.json()["documents"] == ["CV", "PHOTO"]


@requires_db
def test_a_note_a_reviewer_wrote_stays_on_the_reviewers_side(
    client, make_user, application, _tmp_store
):
    """The other half: the STAFF view still carries the note.

    A guard that only proves a field is absent can be satisfied by deleting that
    field everywhere, which would take the reviewer's own screen with it. So this
    writes a real note through a real decision, reads it back where it belongs,
    and then proves it does not come out of the public door — on a REOPENED
    application, which is the one state where an undecided row can have been
    through a reviewer's hands at all.
    """
    submit, _ = application
    admin = make_user(f"pubview-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)

    email = f"public-note-{uuid.uuid4().hex[:6]}@bgscet.ac.in"
    created = submit(client, email)
    assert created.status_code == 201, created.text
    reg_id = created.json()["id"]

    # Into the queue, then rejected with a note a colleague would really write.
    with SessionLocal() as db:
        db.execute(
            update(Registration)
            .where(Registration.id == reg_id)
            .values(status=RegistrationStatus.PENDING_REVIEW)
        )
        db.commit()
    note = "Marks look inflated against the transcript - check with the department first."
    decided = client.post(
        f"/api/register/{reg_id}/decision",
        headers=admin.headers,
        json={"decision": "REJECT", "note": note},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["review_note"] == note, "the staff view lost the note"

    # Reopened, the application accepts uploads again - and the public answer
    # still does not carry the note, whatever the row holds.
    reopened = client.post(f"/api/register/{reg_id}/reopen", headers=admin.headers)
    assert reopened.status_code == 200, reopened.text

    up = _attach(client, reg_id, "cv", "my-cv.pdf", PDF)
    assert up.status_code == 200, up.text
    assert note not in up.text, "a reviewer's note reached an unauthenticated caller"
    for field in REVIEWER_FIELDS:
        assert field not in up.json()
