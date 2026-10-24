"""The claim-to-verification walk (2026-09-17): what a student files on
Skilling reaches the faculty who verify, what they decide reaches the student.

WHO VERIFIES CHANGED ON 2026-10-16. It was the student's mentor, by mentoring
them; the owner took verification away from every mentor, and now it is the
faculty the office grants "Verify skills & evidence" to in Who can do what
(app/verifiers.py). So the reviewer in these tests is a faculty member who
mentors NOBODY and holds a grant reaching the student, and the student's own
mentor, holding no grant, is refused and mailed nothing
(`test_the_students_mentor_without_the_grant_cannot_verify_and_is_not_mailed`).

Five rules, each of which breaks SILENTLY -- the endpoint still answers 200,
the screen still renders, and the damage is a student who does not know what
to do next or a verifier who never hears there is anything to do.

1. THE CERTIFICATE IS DECIDED WITH THE CLAIM. The Skilling form stores the file
   through `POST /student/uploads` (PENDING_REVIEW) and then files
   `badge_evidence` against it, so one act of the student produced two pending
   rows in two queues on the same mentor screen, decidable in opposite
   directions. One decision now writes both, and while the claim is pending
   the file is off the Documents queue.
2. A NO NEEDS A REASON, in the API and not only in the form. REJECT and
   MORE_INFO without a note are 422; the note is the whole of what the student
   is told, on screen and by mail.
3. THE VERIFIERS ARE MAILED WHEN A CLAIM IS FILED, THE STUDENT WHEN IT IS
   DECIDED, each exactly once per transition through `mailer.deliver_once`.
4. THE GATE IS DERIVED FROM THE TRANSPORT. With no SES sender and nothing
   forced, nothing is sent and no `mail_logs` row is written -- the same
   machine `leave_mail_enabled`'s false default protects, protected the same
   way. Forced on, the console outbox carries the messages.
5. THE REVIEWED STRIP READS THE DECISION BACK, with the note, under the same
   scope as the queue.

All @requires_db: real users, a real upload, a real claim and a real decision,
because the behaviour is in the endpoints and the dedupe is in the database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from conftest import TEST_PASSWORD, requires_db

from app import badge_mail, mail_transport
from app.config import settings
from app.db import SessionLocal
from app.models.badge import BadgeEvidence
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.mail import MailLog
from app.models.upload import Upload
from app.models.user import Mentor, Role, Student

# Smallest byte string document_store's magic-byte sniff accepts as a PDF.
_PDF = b"%PDF-1.4\n%%EOF\n"
BADGE = "MGR-NEGOTIATION"


@pytest.fixture
def trio(client, make_user):
    """A student, their mentor (no grant), and a verifier (a grant reaching
    the student, mentoring nobody). The mentor is signed in again after the
    group exists, because the session cookie carries `mentorId`."""
    mentor = make_user("claim-mentor", Role.MENTOR)
    verifier = make_user("claim-verifier", Role.MENTOR)
    student = make_user("claim-student", Role.STUDENT)
    with SessionLocal() as db:
        group = Mentor(user_id=mentor.user_id)
        db.add(group)
        db.flush()
        stu = db.scalar(select(Student).where(Student.user_id == student.user_id))
        stu.mentor_id = group.id
        # A STUDENT-scoped grant: the narrowest reach the office can choose,
        # and one that names this student and nobody else. `make_user`'s
        # teardown deletes grants by subject.
        db.add(
            CapabilityGrant(
                capability="mentor.verifications",
                subject_kind=SubjectKind.USER,
                subject_user_id=verifier.user_id,
                scope_level=ScopeLevel.STUDENT,
                scope_id=stu.id,
                reason="claim-flow tests: the faculty member the office chose",
            )
        )
        db.commit()
    r = client.post(
        "/api/auth/login", json={"email": mentor.email, "password": TEST_PASSWORD}
    )
    assert r.status_code == 200, r.text
    mentor.headers = {"Cookie": r.headers.get("set-cookie", "")}
    client.cookies.clear()
    mail_transport.outbox.clear()
    yield mentor, verifier, student
    with SessionLocal() as db:
        ids = db.scalars(
            select(BadgeEvidence.id)
            .join(Student, BadgeEvidence.student_id == Student.id)
            .where(Student.user_id == student.user_id)
        ).all()
        for ev_id in ids:
            db.execute(delete(MailLog).where(MailLog.dedupe_key.like(f"badge-%:{ev_id}%")))
        db.commit()


@pytest.fixture
def pair(trio):
    """The verifier and the student, for the tests that need no mentor."""
    _mentor, verifier, student = trio
    return verifier, student


def _file_claim(client, student, note: str = "Covers negotiation modules 1-4") -> dict:
    """Exactly what the Skilling form does: store the certificate, then claim."""
    up = client.post(
        "/api/student/uploads",
        headers=student.headers,
        files={"file": ("negotiation.pdf", _PDF, "application/pdf")},
        data={"kind": "CERTIFICATE_PROOF", "title": "Negotiation — Coursera"},
    )
    assert up.status_code == 201, up.text
    upload_id = up.json()["id"]
    r = client.post(
        f"/api/student/badges/{BADGE}/evidence",
        headers=student.headers,
        json={
            "evidence_type": "EXTERNAL_VERIFIED",
            "upload_id": upload_id,
            "title": "Negotiation",
            "provider": "Coursera",
            "note": note,
        },
    )
    assert r.status_code == 201, r.text
    return {"upload_id": upload_id, "dashboard": r.json()}


def _queue_row(client, verifier, upload_id: str) -> dict:
    queue = client.get("/api/mentor/badge-evidence/pending", headers=verifier.headers).json()
    return next(row for row in queue if row["upload_id"] == upload_id)


@requires_db
def test_a_claim_filed_on_skilling_reaches_the_verifier_queue_with_its_file(client, pair):
    verifier, student = pair
    filed = _file_claim(client, student)
    row = _queue_row(client, verifier, filed["upload_id"])
    assert row["badge_name"] == "Negotiation"
    assert row["student_note"] == "Covers negotiation modules 1-4"
    assert row["provider"] == "Coursera"
    assert row["evidence_file_name"] == "negotiation.pdf"
    # The certificate is the CLAIM's while the claim is pending: it is not
    # also listed as a loose document waiting for a second, separate verdict.
    docs = client.get("/api/mentor/uploads/pending", headers=verifier.headers).json()
    assert filed["upload_id"] not in {d["id"] for d in docs}
    # The verifier can open the file behind the claim.
    f = client.get(f"/api/mentor/badge-evidence/{row['id']}/file", headers=verifier.headers)
    assert f.status_code == 200 and f.content.startswith(b"%PDF")


@requires_db
def test_a_no_without_a_reason_is_refused_and_the_claim_stays_pending(client, pair):
    verifier, student = pair
    filed = _file_claim(client, student)
    row = _queue_row(client, verifier, filed["upload_id"])
    for decision in ("REJECT", "MORE_INFO"):
        for note in (None, "", "   "):
            r = client.post(
                f"/api/mentor/badge-evidence/{row['id']}/review",
                headers=verifier.headers,
                json={"decision": decision, "note": note},
            )
            assert r.status_code == 422, (decision, note, r.text)
            assert "note" in r.json()["detail"].lower()
    # Still in the queue, untouched.
    assert _queue_row(client, verifier, filed["upload_id"])["status"] == "PENDING_VERIFICATION"
    with SessionLocal() as db:
        assert db.get(Upload, filed["upload_id"]).status.value == "PENDING_REVIEW"


@requires_db
def test_approving_the_claim_verifies_the_certificate_and_lights_the_badge(client, pair):
    verifier, student = pair
    filed = _file_claim(client, student)
    row = _queue_row(client, verifier, filed["upload_id"])
    r = client.post(
        f"/api/mentor/badge-evidence/{row['id']}/review",
        headers=verifier.headers,
        json={"decision": "APPROVE"},  # no note needed on a yes
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED"
    assert r.json()["evidence_file_name"] == "negotiation.pdf"

    with SessionLocal() as db:
        upload = db.get(Upload, filed["upload_id"])
        assert upload.status.value == "VERIFIED"
        assert upload.reviewed_by_id == verifier.user_id
    # The student's own Uploads screen reads the same verdict.
    mine = client.get("/api/student/uploads", headers=student.headers).json()
    assert next(u for u in mine if u["id"] == filed["upload_id"])["status"] == "VERIFIED"
    # And the board is lit: EARNED with the catalogue's points.
    dash = client.get("/api/student/badges", headers=student.headers).json()
    badge = next(b for c in dash["categories"] for b in c["badges"] if b["code"] == BADGE)
    assert badge["status"] == "EARNED"
    assert badge["points_earned"] > 0


@requires_db
def test_rejecting_the_claim_rejects_the_certificate_with_the_same_note(client, pair):
    verifier, student = pair
    filed = _file_claim(client, student)
    row = _queue_row(client, verifier, filed["upload_id"])
    r = client.post(
        f"/api/mentor/badge-evidence/{row['id']}/review",
        headers=verifier.headers,
        json={"decision": "REJECT", "note": "The name on the certificate is not yours."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REJECTED"
    assert r.json()["review_note"] == "The name on the certificate is not yours."
    with SessionLocal() as db:
        upload = db.get(Upload, filed["upload_id"])
        assert upload.status.value == "REJECTED"
        assert upload.review_note == "The name on the certificate is not yours."
    # Out of the queue, into the reviewed strip, note and all.
    queue = client.get("/api/mentor/badge-evidence/pending", headers=verifier.headers).json()
    assert row["id"] not in {q["id"] for q in queue}
    reviewed = client.get(
        "/api/mentor/badge-evidence/reviewed?limit=5", headers=verifier.headers
    ).json()
    mine = next(q for q in reviewed if q["id"] == row["id"])
    assert mine["status"] == "REJECTED"
    assert mine["review_note"] == "The name on the certificate is not yours."
    assert mine["reviewed_at"] is not None
    # A verdict the document already carries is never rewritten by a later
    # decision on the claim: re-deciding MORE_INFO leaves the upload REJECTED.
    r = client.post(
        f"/api/mentor/badge-evidence/{row['id']}/review",
        headers=verifier.headers,
        json={"decision": "MORE_INFO", "note": "Actually, upload page two as well."},
    )
    assert r.status_code == 200
    with SessionLocal() as db:
        assert db.get(Upload, filed["upload_id"]).status.value == "REJECTED"


@requires_db
def test_a_faculty_member_without_the_grant_sees_no_reviewed_strip_either(client, make_user):
    """The strip is the queue read back, so it is gated the same way: a
    faculty account with no grant holds no `mentor.verifications` at all."""
    lone = make_user("claim-lone", Role.MENTOR)
    assert client.get("/api/mentor/badge-evidence/reviewed", headers=lone.headers).status_code == 403


# ------------------------------------------------------------------ mail --


@requires_db
def test_mail_is_derived_from_the_transport_and_off_here(client, pair):
    """This suite runs with no SES sender and nothing forced, so the derived
    answer is off: no message, and -- the part that matters -- no `mail_logs`
    row reading SENT about a message nobody received."""
    assert settings.badge_mail_enabled.strip() == "", "the suite must not force the switch"
    assert settings.mail_configured is False
    assert settings.badge_mail_active is False
    verifier, student = pair
    filed = _file_claim(client, student)
    row = _queue_row(client, verifier, filed["upload_id"])
    client.post(
        f"/api/mentor/badge-evidence/{row['id']}/review",
        headers=verifier.headers,
        json={"decision": "APPROVE"},
    )
    assert list(mail_transport.outbox) == []
    with SessionLocal() as db:
        assert db.scalars(
            select(MailLog).where(MailLog.dedupe_key.like(f"badge-%:{row['id']}%"))
        ).all() == []


def test_the_switch_reads_only_the_exact_words(monkeypatch):
    for text, forced in (("true", True), ("TRUE ", True), ("false", False), ("flase", None), ("", None)):
        monkeypatch.setattr(settings, "badge_mail_enabled", text)
        expected = forced if forced is not None else settings.mail_configured
        assert settings.badge_mail_active is expected, text


@requires_db
def test_when_on_the_verifier_hears_of_the_claim_and_the_student_of_the_decision(
    client, trio, monkeypatch
):
    monkeypatch.setattr(settings, "badge_mail_enabled", "true")
    mentor, verifier, student = trio

    filed = _file_claim(client, student, note="Modules 1-4, with the final assessment")
    assert len(mail_transport.outbox) == 1
    to_verifier = mail_transport.outbox[0]
    assert to_verifier.to == verifier.email
    assert "Negotiation" in to_verifier.subject
    assert "/mentor/verifications" in to_verifier.text
    assert "Coursera" in to_verifier.text
    # The mentor holds no grant, so they cannot open the queue the mail would
    # send them to, and are not told about it.
    assert mentor.email not in {m.to for m in mail_transport.outbox}
    with SessionLocal() as db:
        ev_id = db.scalar(
            select(BadgeEvidence.id).where(BadgeEvidence.upload_id == filed["upload_id"])
        )
        rows = db.scalars(
            select(MailLog).where(MailLog.dedupe_key.like(f"badge-claim:{ev_id}%"))
        ).all()
        assert len(rows) == 1 and rows[0].kind == badge_mail.CLAIM_MAIL_KIND
        assert rows[0].dedupe_key == badge_mail.claim_dedupe_key(
            db.get(BadgeEvidence, ev_id), verifier.user_id
        )
        assert rows[0].status.value == "SENT"
        # Retried -- the same claim, the same key, no second copy.
        again = badge_mail.notify_verifiers_of_claim(db, db.get(BadgeEvidence, ev_id))
        assert [r.id for r in again] == [rows[0].id]
    assert len(mail_transport.outbox) == 1

    row = _queue_row(client, verifier, filed["upload_id"])
    r = client.post(
        f"/api/mentor/badge-evidence/{row['id']}/review",
        headers=verifier.headers,
        json={"decision": "REJECT", "note": "Attach the graded certificate, not the enrolment."},
    )
    assert r.status_code == 200
    assert len(mail_transport.outbox) == 2
    to_student = mail_transport.outbox[1]
    assert to_student.to == student.email
    assert "not verified" in to_student.subject
    # The reviewer's note IS the message: it was written to the student.
    assert "Attach the graded certificate, not the enrolment." in to_student.text
    assert "/student/skilling" in to_student.text
    with SessionLocal() as db:
        assert db.scalar(
            select(MailLog).where(MailLog.dedupe_key == f"badge-decision:{ev_id}:REJECTED")
        ).kind == badge_mail.DECISION_MAIL_KIND

    # A second decision is a different transition and does go out; the same
    # one again would not.
    r = client.post(
        f"/api/mentor/badge-evidence/{row['id']}/review",
        headers=verifier.headers,
        json={"decision": "APPROVE", "note": "Graded certificate received."},
    )
    assert r.status_code == 200
    assert len(mail_transport.outbox) == 3
    assert "verified" in mail_transport.outbox[2].subject
    assert "Graded certificate received." in mail_transport.outbox[2].text


@requires_db
def test_a_student_nobody_may_verify_mails_nobody_and_still_files(client, make_user, monkeypatch):
    """No grant reaches this student, so nobody holds the queue the claim sits
    in and there is nobody to tell -- the claim still lands, and waits for the
    office to grant somebody (itself included), and nothing raises."""
    monkeypatch.setattr(settings, "badge_mail_enabled", "true")
    student = make_user("claim-orphan", Role.STUDENT)
    mail_transport.outbox.clear()
    r = client.post(
        f"/api/student/badges/{BADGE}/evidence",
        headers=student.headers,
        json={"evidence_type": "APPLIED", "title": "Ran the placement negotiation drill"},
    )
    assert r.status_code == 201
    assert list(mail_transport.outbox) == []


# ------------------------------------------------- who may verify (2026-10-16) --


@requires_db
def test_the_students_mentor_without_the_grant_cannot_verify_and_is_not_mailed(
    client, trio, monkeypatch
):
    """The owner's decision: mentoring a student brings no verification. Every
    verification door refuses the student's own mentor -- the claim queue, its
    strip, a claim's file and decision, the documents queue and decision, and
    the legacy skill-claim queue -- while the mentor's rule-2 READ of their
    mentee's file is untouched."""
    monkeypatch.setattr(settings, "badge_mail_enabled", "true")
    mentor, verifier, student = trio
    filed = _file_claim(client, student)
    assert mentor.email not in {m.to for m in mail_transport.outbox}
    row = _queue_row(client, verifier, filed["upload_id"])

    h = mentor.headers
    assert client.get("/api/mentor/badge-evidence/pending", headers=h).status_code == 403
    assert client.get("/api/mentor/badge-evidence/reviewed", headers=h).status_code == 403
    assert client.get(f"/api/mentor/badge-evidence/{row['id']}/file", headers=h).status_code == 403
    r = client.post(
        f"/api/mentor/badge-evidence/{row['id']}/review", headers=h, json={"decision": "APPROVE"}
    )
    assert r.status_code == 403
    assert client.get("/api/mentor/uploads/pending", headers=h).status_code == 403
    r = client.post(
        f"/api/mentor/uploads/{filed['upload_id']}/review", headers=h, json={"decision": "VERIFY"}
    )
    assert r.status_code == 403
    assert client.get("/api/mentor/skill-claims/pending", headers=h).status_code == 403
    assert client.get("/api/mentor/skill-claims/reviewed", headers=h).status_code == 403
    # A skill badge cannot be marked earned by hand either: an award is a
    # verification without a claim.
    with SessionLocal() as db:
        student_id = db.scalar(select(Student.id).where(Student.user_id == student.user_id))
    r = client.post(
        f"/api/mentor/students/{student_id}/badges/{BADGE}/award", headers=h, json={}
    )
    assert r.status_code == 403
    # Nothing was decided.
    with SessionLocal() as db:
        assert db.get(BadgeEvidence, row["id"]).status.value == "PENDING_VERIFICATION"
        assert db.get(Upload, filed["upload_id"]).status.value == "PENDING_REVIEW"
    # Rule 2 still lets the mentor READ their mentee's file.
    f = client.get(f"/api/mentor/uploads/{filed['upload_id']}/file", headers=h)
    assert f.status_code == 200 and f.content.startswith(b"%PDF")


@requires_db
def test_a_granted_verifier_reviews_documents_of_a_student_they_do_not_mentor(client, pair):
    """The Documents half of the screen answers to the same grant: listed,
    opened and decided by the verifier, who mentors nobody."""
    verifier, student = pair
    up = client.post(
        "/api/student/uploads",
        headers=student.headers,
        files={"file": ("report.pdf", _PDF, "application/pdf")},
        data={"kind": "DOCUMENT", "title": "Internship report"},
    )
    assert up.status_code == 201, up.text
    upload_id = up.json()["id"]
    docs = client.get("/api/mentor/uploads/pending", headers=verifier.headers)
    assert docs.status_code == 200, docs.text
    assert docs.headers["X-Reep-Scope"] == "narrowed"
    assert upload_id in {d["id"] for d in docs.json()}
    f = client.get(f"/api/mentor/uploads/{upload_id}/file", headers=verifier.headers)
    assert f.status_code == 200 and f.content.startswith(b"%PDF")
    r = client.post(
        f"/api/mentor/uploads/{upload_id}/review",
        headers=verifier.headers,
        json={"decision": "VERIFY"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "VERIFIED"


@requires_db
def test_a_grant_scoped_to_one_student_reaches_nobody_else(client, pair, make_user):
    """The reach is the fence: a verifier granted ONE student is refused
    another student's claim with the same 404 as a wrong id."""
    verifier, _student = pair
    other = make_user("claim-other", Role.STUDENT)
    filed = _file_claim(client, other)
    queue = client.get("/api/mentor/badge-evidence/pending", headers=verifier.headers).json()
    assert filed["upload_id"] not in {q["upload_id"] for q in queue}
    with SessionLocal() as db:
        ev_id = db.scalar(
            select(BadgeEvidence.id).where(BadgeEvidence.upload_id == filed["upload_id"])
        )
    r = client.post(
        f"/api/mentor/badge-evidence/{ev_id}/review",
        headers=verifier.headers,
        json={"decision": "APPROVE"},
    )
    assert r.status_code == 404
    r = client.post(
        f"/api/mentor/uploads/{filed['upload_id']}/review",
        headers=verifier.headers,
        json={"decision": "VERIFY"},
    )
    assert r.status_code == 404
