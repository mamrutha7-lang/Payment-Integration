"""Student -> reviewer skill verification: the decision, and what it tells people.

The reviewer here is the student's mentor AND holds a "Verify skills &
evidence" grant reaching the student: since 2026-10-16 the grant is what lets
them decide (app/verifiers.py), and mentoring alone opens none of these
endpoints. test_skill_claim_flow.py pins that the mentor without the grant is
refused.

Three rules are pinned here, and each exists because breaking it is SILENT — the
endpoint still returns 200, the screen still renders, and the damage shows up as
a student who does not know what to do next, or a resume that overstates.

  * NEEDS_CHANGES and REJECTED are different outcomes. One means "redo this",
    the other "this will not be granted", and the student sees a status and a
    note and nothing else. Collapsing them back into one status is the easiest
    regression to make here and the hardest to notice.
  * A non-approval requires a note, in the API and not only in the form. A
    rejection a student cannot act on is indistinguishable, from their side,
    from a bug.
  * A generated resume lists VERIFIED skills only. An unverified StudentSkill is
    a claim still in review; printing it under Skills presents work in
    review as work confirmed, which is the thing this whole flow exists to
    prevent.

All @requires_db: real users, a real claim and a real decision. The behaviour
lives in the endpoints, so testing the pure functions underneath would prove
much less.
"""

import types
import uuid

import pytest
from conftest import TEST_PASSWORD, requires_db

from app.db import SessionLocal
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.skill import Skill, SkillClaim, StudentSkill
from app.models.upload import Upload
from app.models.user import Mentor, Role, Student

# Smallest byte string document_store's magic-byte sniff accepts as a PDF.
_PDF = b"%PDF-1.4\n%%EOF\n"


@pytest.fixture
def pair(client, make_user):
    """A MENTOR with a real Mentor row, a student assigned to them, and the
    verification grant for that student (`make_user`'s teardown deletes grants
    by subject).

    A FIXTURE RATHER THAN A HELPER, for teardown ordering. `make_user` deletes
    its users when it finalises, and the Mentor row added here references one of
    them — a plain helper leaves a foreign key that makes the fixture's own
    cleanup fail with a ForeignKeyViolation. A fixture that depends on
    `make_user` finalises FIRST, which is exactly the window needed to drop the
    rows pointing at those users.

    The re-login matters too. `make_user` mints its session before this adds the
    Mentor row, and rule 2 reads the group from `session.mentorId`; a cookie is
    a signed snapshot rather than a live read, so the row has to exist before
    the session that will act on it.
    """
    mentor_user = make_user(f"sv-mentor-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    student = make_user(f"sv-stud-{uuid.uuid4().hex[:4]}", Role.STUDENT)

    with SessionLocal() as db:
        mentor = Mentor(user_id=mentor_user.user_id)
        db.add(mentor)
        db.flush()
        student_row = db.query(Student).filter(Student.user_id == student.user_id).one()
        student_row.mentor_id = mentor.id
        db.add(
            CapabilityGrant(
                capability="mentor.verifications",
                subject_kind=SubjectKind.USER,
                subject_user_id=mentor_user.user_id,
                scope_level=ScopeLevel.STUDENT,
                scope_id=student_row.id,
                reason="skill verification tests: the reviewer the office chose",
            )
        )
        db.commit()
        mentor_id, student_id = mentor.id, student_row.id

    r = client.post(
        "/api/auth/login", json={"email": mentor_user.email, "password": TEST_PASSWORD}
    )
    assert r.status_code == 200, r.text
    headers = {"Cookie": r.headers.get("set-cookie", "")}
    client.cookies.clear()

    skills: list[str] = []
    yield types.SimpleNamespace(
        mentor_headers=headers, student=student, student_id=student_id, skills=skills
    )

    # Everything pointing at the two users, in dependency order, before
    # make_user tries to delete them.
    with SessionLocal() as db:
        db.query(SkillClaim).filter(SkillClaim.student_id == student_id).delete()
        db.query(StudentSkill).filter(StudentSkill.student_id == student_id).delete()
        db.query(Upload).filter(Upload.student_id == student_id).delete()
        row = db.query(Student).filter(Student.id == student_id).one_or_none()
        if row is not None:
            row.mentor_id = None
        db.flush()
        db.query(Mentor).filter(Mentor.id == mentor_id).delete()
        for sk in skills:
            db.query(Skill).filter(Skill.id == sk).delete()
        db.commit()


def new_skill(pair, label: str) -> tuple[str, str]:
    """A throwaway Skill, registered for the fixture's teardown."""
    with SessionLocal() as db:
        skill = Skill(
            slug=f"sv-{label}-{uuid.uuid4().hex[:8]}",
            name=f"Skill {label} {uuid.uuid4().hex[:4]}",
            category="Platform / Technical",
        )
        db.add(skill)
        db.commit()
        pair.skills.append(skill.id)
        return skill.id, skill.name


def file_a_claim(client, student, skill_id: str) -> str:
    """Upload a certificate and claim `skill_id` against it, as the student."""
    up = client.post(
        "/api/student/uploads",
        headers=student.headers,
        files={"file": ("cert.pdf", _PDF, "application/pdf")},
        data={"kind": "CERTIFICATE_PROOF", "title": "Course certificate"},
    )
    assert up.status_code == 201, up.text
    claim = client.post(
        "/api/student/skill-claims",
        headers=student.headers,
        json={
            "skill_id": skill_id,
            "upload_id": up.json()["id"],
            "student_note": "Covers the dashboard build.",
        },
    )
    assert claim.status_code == 201, claim.text
    return claim.json()["id"]


@requires_db
def test_request_changes_and_reject_are_different_outcomes(client, pair):
    """The two non-approvals must not collapse into one status."""
    skill_a, _ = new_skill(pair, "changes")
    skill_b, _ = new_skill(pair, "reject")
    claim_a = file_a_claim(client, pair.student, skill_a)
    claim_b = file_a_claim(client, pair.student, skill_b)

    r = client.post(
        f"/api/mentor/skill-claims/{claim_a}/review",
        headers=pair.mentor_headers,
        json={"decision": "CHANGES", "note": "The certificate names a different course."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "NEEDS_CHANGES"

    r = client.post(
        f"/api/mentor/skill-claims/{claim_b}/review",
        headers=pair.mentor_headers,
        json={"decision": "REJECT", "note": "Attendance certificate, not an assessment."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REJECTED"

    # Neither grants the skill: only a verified claim may light a badge.
    with SessionLocal() as db:
        held = db.query(StudentSkill).filter(StudentSkill.student_id == pair.student_id).all()
        assert held == []


@requires_db
def test_a_non_approval_without_a_note_is_refused(client, pair):
    """The student is shown the note and nothing else, so it cannot be optional."""
    skill_id, _ = new_skill(pair, "note")
    claim = file_a_claim(client, pair.student, skill_id)

    for decision in ("CHANGES", "REJECT"):
        for note in (None, "   "):
            r = client.post(
                f"/api/mentor/skill-claims/{claim}/review",
                headers=pair.mentor_headers,
                json={"decision": decision, "note": note},
            )
            assert r.status_code == 422, f"{decision}/{note!r} -> {r.status_code} {r.text}"

    # The refusals left the claim untouched, so it is still decidable.
    r = client.post(
        f"/api/mentor/skill-claims/{claim}/review",
        headers=pair.mentor_headers,
        json={"decision": "GRANT"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "VERIFIED"


@requires_db
def test_generated_resume_lists_verified_skills_only(client, pair):
    """An unverified skill is a claim in progress, not a line on a CV."""
    granted_id, granted_name = new_skill(pair, "granted")
    pending_id, pending_name = new_skill(pair, "pending")

    # One claim the mentor verifies...
    claim = file_a_claim(client, pair.student, granted_id)
    r = client.post(
        f"/api/mentor/skill-claims/{claim}/review",
        headers=pair.mentor_headers,
        json={"decision": "GRANT", "note": "Clear evidence."},
    )
    assert r.status_code == 200, r.text

    # ...and one the student holds that nobody has verified.
    with SessionLocal() as db:
        db.add(
            StudentSkill(
                student_id=pair.student_id, skill_id=pending_id, level=3, verified=False
            )
        )
        db.commit()

    r = client.post("/api/student/resume/generate", headers=pair.student.headers, json={})
    assert r.status_code == 200, r.text
    markdown = r.json()["markdown"]

    assert granted_name in markdown, "a verified skill must reach the document"
    assert pending_name not in markdown, "an unverified skill must not be presented as a skill"


@requires_db
def test_recently_reviewed_is_scoped_and_carries_the_decision(client, pair, make_user):
    """The queue's history strip: a decided claim leaves the pending list and
    appears under Recently reviewed WITH the note the student was given — and
    under the same scope as the queue, so a faculty member with no grant is
    refused the history rather than reading the programme's."""
    skill_id, skill_name = new_skill(pair, "history")
    claim = file_a_claim(client, pair.student, skill_id)

    # The pending card names the evidence behind the claim before it is opened.
    r = client.get("/api/mentor/skill-claims/pending", headers=pair.mentor_headers)
    assert r.status_code == 200, r.text
    row = next(c for c in r.json() if c["id"] == claim)
    assert row["evidence_title"] == "Course certificate"
    assert row["evidence_file_name"] == "cert.pdf"
    assert row["evidence_kind"] == "CERTIFICATE_PROOF"

    r = client.get("/api/mentor/skill-claims/reviewed", headers=pair.mentor_headers)
    assert r.status_code == 200, r.text
    assert claim not in [c["id"] for c in r.json()], "undecided claims are not history"

    r = client.post(
        f"/api/mentor/skill-claims/{claim}/review",
        headers=pair.mentor_headers,
        json={"decision": "CHANGES", "note": "Upload the certificate with your name on it."},
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/mentor/skill-claims/reviewed", headers=pair.mentor_headers)
    assert r.status_code == 200, r.text
    hist = next(c for c in r.json() if c["id"] == claim)
    assert hist["status"] == "NEEDS_CHANGES"
    assert hist["skill_name"] == skill_name
    assert hist["review_note"] == "Upload the certificate with your name on it."
    assert hist["reviewed_at"] is not None

    r = client.get("/api/mentor/skill-claims/pending", headers=pair.mentor_headers)
    assert claim not in [c["id"] for c in r.json()], "a decided claim has left the queue"

    # `limit` is honoured and bounded.
    r = client.get("/api/mentor/skill-claims/reviewed?limit=1", headers=pair.mentor_headers)
    assert r.status_code == 200 and len(r.json()) == 1
    assert client.get(
        "/api/mentor/skill-claims/reviewed?limit=0", headers=pair.mentor_headers
    ).status_code == 422

    # A faculty member with no grant sees NOBODY's history (refused at the
    # capability gate since 2026-10-16, where it was an empty list), and a
    # student none at all.
    loner = make_user("sv-loner", Role.MENTOR)
    r = client.get("/api/mentor/skill-claims/reviewed", headers=loner.headers)
    assert r.status_code == 403
    r = client.get("/api/mentor/skill-claims/reviewed", headers=pair.student.headers)
    assert r.status_code == 403


@requires_db
def test_deleting_the_certificate_leaves_the_mentors_verdict_standing(client, pair):
    """A student removing their own upload must not erase a mentor's assessment.

    `skill_claims.upload_id` was `NOT NULL ... ON DELETE CASCADE`, so
    `DELETE /api/student/uploads/{id}` took the whole claim with it — the level
    granted, the reviewer's identity and timestamp, and the note they wrote —
    silently, with a 204, and with nothing else in the system holding a copy.
    Neither the student nor the mentor was told.

    The student IS entitled to delete their own certificate; they are not
    entitled to delete someone else's assessment of it, and one foreign key made
    those the same act. `badge_evidence.upload_id` already had this right
    (models/badge.py:355). The claim now survives with a NULL upload_id.
    """
    skill_id, _ = new_skill(pair, "outlives")

    up = client.post(
        "/api/student/uploads",
        headers=pair.student.headers,
        files={"file": ("cert.pdf", _PDF, "application/pdf")},
        data={"kind": "CERTIFICATE_PROOF", "title": "Course certificate"},
    )
    assert up.status_code == 201, up.text
    upload_id = up.json()["id"]

    r = client.post(
        "/api/student/skill-claims",
        headers=pair.student.headers,
        json={"skill_id": skill_id, "upload_id": upload_id, "student_note": "Covers the build."},
    )
    assert r.status_code == 201, r.text
    claim_id = r.json()["id"]

    r = client.post(
        f"/api/mentor/skill-claims/{claim_id}/review",
        headers=pair.mentor_headers,
        json={"decision": "GRANT", "note": "Assessed course, matches the claim."},
    )
    assert r.status_code == 200, r.text
    reviewer_id = r.json()["reviewed_by_id"]
    assert reviewer_id, "the review must record who made it"

    # The student removes their own certificate. Their right to do so.
    r = client.delete(f"/api/student/uploads/{upload_id}", headers=pair.student.headers)
    assert r.status_code == 204, r.text

    # The verdict survives, in full.
    with SessionLocal() as db:
        claim = db.get(SkillClaim, claim_id)
        assert claim is not None, "the mentor's assessment was destroyed with the upload"
        assert claim.upload_id is None, "the pointer should be cleared, not dangling"
        assert claim.status.value == "VERIFIED"
        assert claim.reviewed_by_id == reviewer_id
        assert claim.review_note == "Assessed course, matches the claim."
        assert claim.reviewed_at is not None

    # And it is still readable on the mentor's own screen, without a 500 from
    # the now-absent Upload — the evidence_* fields degrade to None.
    r = client.get("/api/mentor/skill-claims/reviewed", headers=pair.mentor_headers)
    assert r.status_code == 200, r.text
    row = next((c for c in r.json() if c["id"] == claim_id), None)
    assert row is not None, "the reviewed claim vanished from the mentor's history"
    assert row["upload_id"] is None
    assert row["evidence_file_name"] is None
    assert row["review_note"] == "Assessed course, matches the claim."
