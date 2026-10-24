"""The generated resume, and what it is allowed to say.

INCIDENT (2026-09-09, end-to-end test of the Resume Builder): a student filled
all fifteen sections of the builder, the sidebar read 100% complete, and the
document that came out carried none of it — no experience, no internships, no
projects, no publications, no certifications, no positions, no objective, no
referees. `_compose_resume_markdown` read the `student_profiles` row, the
verified skills and the academic record; the builder wrote `resume_profiles.data`
and nothing read it. The preview said "this resume is drawing on your full
record" over a five-line page.

Two tests hold that shut from opposite sides, and both are needed:

  * `test_the_document_carries_every_builder_section` fails if a section stops
    reaching the page. Written as one marker per section so the assertion names
    the section that regressed rather than reporting "markdown differs".
  * `test_the_document_never_carries_the_private_sections` fails if one starts.
    The builder is ALSO the placement office's intake form, so the same map
    holds next-of-kin details, a date of birth, medical history and demographics
    that the screens promise will not travel. A composer that publishes by
    default is one forgotten `if` away from putting a student's medical history
    in front of an employer.

The completeness tests are here for the same reason the composer is: the bar
that says "70%+ produces a materially stronger document" was reachable by
pressing "Add another number" and typing nothing.
"""

import io
import struct
import uuid
import zlib

import pytest
from pypdf import PdfReader
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.models.resume import Resume
from app.models.resume_profile import ResumeProfile
from app.models.skill import Skill, StudentSkill
from app.models.student_profile import StudentProfile
from app.models.upload import Upload, UploadKind, UploadStatus
from app.models.user import Mentor, Role, Student, User
from app.resume_pdf import EvidenceProof, append_evidence, render_resume_pdf
from app.routers.student import _compose_resume_markdown, _resume_completeness
from tests.conftest import TEST_PASSWORD, requires_db


# --------------------------------------------------------------------------- #
# A builder map with something identifiable in every section
# --------------------------------------------------------------------------- #

#: One marker per PUBLIC section. The value is what the document must contain;
#: the key is the name the failure message prints.
PUBLIC_MARKERS = {
    "career objective": "MARKEROBJECTIVE",
    "personal email": "markeremail@example.com",
    "other phone": "9876500001",
    "web link": "https://marker.example.com/profile",
    "address city": "MarkerCity",
    "experience": "MARKERROLE",
    "experience description": "MARKERROLEDESC",
    "internship": "MARKERINTERN",
    "project": "MARKERPROJECT",
    "project tech": "MARKERTECH",
    "key expertise": "MARKEREXPERTISE",
    "external certification": "MARKERCERT",
    "publication": "MARKERPAPER",
    "seminar": "MARKERSEMINAR",
    "position of responsibility": "MARKERPOSITION",
    "achievement": "MARKERACHIEVEMENT",
    "award": "MARKERAWARD",
    "co-curricular activity": "MARKERCOCURRICULAR",
    "extra-curricular activity": "MARKEREXTRACURRICULAR",
    "language": "MARKERLANGUAGE",
    "referee": "MARKERREFEREE",
}

#: One marker per PRIVATE field, with the promise each one breaks. These are
#: quoted from the screens the student reads while typing them in.
PRIVATE_MARKERS = {
    "MARKERDOB": 'date of birth — "not shown on your exported resume"',
    "MARKERMEDICAL": 'medical history — "never to recruiters"',
    "MARKERGENDER": "gender — a placement-profile demographic",
    "MARKERBLOOD": "blood group — a placement-profile demographic",
    "MARKERMARITAL": "marital status — a placement-profile demographic",
    "MARKERDREAM": "dream company — an aspiration addressed to the office",
    "MARKERFATHER": "father's name — next-of-kin, collected for the office",
    "MARKERMOTHER": "mother's name — next-of-kin, collected for the office",
    "MARKERGUARDIAN": "guardian's name — next-of-kin, collected for the office",
    "MARKERSTREET": "street address — the office's delivery detail",
    "MARKERPOSTAL": "postal code — the office's delivery detail",
    "MARKERGOALROLE": "the goal strip's target role — which posting this copy is aimed at",
    "MARKERPOLICY": "the placement-policy acceptance timestamp",
}


def builder_map() -> dict:
    """Every builder section, public and private, filled with its marker."""
    return {
        "basic": {
            "middle_name": "Marker",
            "languages": [PUBLIC_MARKERS["language"]],
            "dob": "MARKERDOB",
            "medical_history": "MARKERMEDICAL",
            "gender": "MARKERGENDER",
            "blood_group": "MARKERBLOOD",
            "marital_status": "MARKERMARITAL",
            "dream_company": "MARKERDREAM",
        },
        "contact": {
            "personal_emails": [PUBLIC_MARKERS["personal email"]],
            "other_phones": [{"code": "+91", "number": PUBLIC_MARKERS["other phone"]}],
            "web_links": [{"type": "GitHub", "url": PUBLIC_MARKERS["web link"]}],
            "current_address": {
                "line1": "MARKERSTREET",
                "line2": "MARKERSTREET two",
                "country": "India",
                "state": "MarkerState",
                "city": PUBLIC_MARKERS["address city"],
                "postal": "MARKERPOSTAL",
            },
            "permanent_same": True,
            "permanent_address": {"line1": "MARKERSTREET permanent", "postal": "MARKERPOSTAL"},
        },
        "family": {
            "father": {"name": "MARKERFATHER", "phone": "9990000001"},
            "mother": {"name": "MARKERMOTHER"},
            "guardians": [{"name": "MARKERGUARDIAN", "relationship": "Sister"}],
        },
        "experience": [
            {
                "title": PUBLIC_MARKERS["experience"],
                "org": "Infosys",
                "sector": "IT Services",
                "start": "Jul 2022",
                "end": "Present",
                "location": "Bengaluru",
                "description": PUBLIC_MARKERS["experience description"],
            }
        ],
        "internship": [
            {"title": PUBLIC_MARKERS["internship"], "org": "Flipkart", "start": "May 2025"}
        ],
        "projects": [
            {
                "title": PUBLIC_MARKERS["project"],
                "description": "Capstone.",
                "tech": [PUBLIC_MARKERS["project tech"]],
                "link": "https://example.com/repo",
            }
        ],
        "publications": [
            {"title": PUBLIC_MARKERS["publication"], "publisher": "IIMB Review", "date": "Mar 2026"}
        ],
        "seminars": [{"title": PUBLIC_MARKERS["seminar"], "provider": "TUG", "date": "Feb 2026"}],
        "por": [{"title": PUBLIC_MARKERS["position of responsibility"], "org": "Placement Cell"}],
        "external_certs": [
            {"name": PUBLIC_MARKERS["external certification"], "provider": "Google", "year": "2025"}
        ],
        "other": {
            "career_objective": PUBLIC_MARKERS["career objective"],
            "key_expertise": [PUBLIC_MARKERS["key expertise"]],
            "achievements": [PUBLIC_MARKERS["achievement"]],
            "awards": [PUBLIC_MARKERS["award"]],
            "co_curricular": [PUBLIC_MARKERS["co-curricular activity"]],
            "extra_curricular": [PUBLIC_MARKERS["extra-curricular activity"]],
        },
        "references": [
            {
                "name": PUBLIC_MARKERS["referee"],
                "designation": "Delivery Manager",
                "org": "Infosys",
                "relationship": "Former manager",
                "email": "referee@example.com",
            }
        ],
        "policy": {"accepted_at": "MARKERPOLICY", "eligible": True},
        "goal": {"role": "MARKERGOALROLE", "location": "Remote", "opportunityId": "abc"},
    }


# --------------------------------------------------------------------------- #
# The document (pure function — no database needed)
# --------------------------------------------------------------------------- #


def test_the_document_carries_every_builder_section() -> None:
    """Everything the student typed reaches the page.

    The regression this catches is not subtle — it is the whole builder going
    missing — but it is invisible from the API, which happily returns a
    well-formed resume containing five lines.
    """
    md = _compose_resume_markdown("Test Student", None, ["MS Excel"], 8.2, [], builder_map())
    missing = [name for name, marker in PUBLIC_MARKERS.items() if marker not in md]
    assert not missing, (
        "The generated resume dropped what the student typed into: "
        + ", ".join(sorted(missing))
        + ".\nThe builder writes resume_profiles.data; _compose_resume_markdown "
        "is the only thing that reads it.\n--- document ---\n" + md
    )


def test_the_document_never_carries_the_private_sections() -> None:
    """The intake form's private half stays off a document an employer reads."""
    md = _compose_resume_markdown("Test Student", None, ["MS Excel"], 8.2, [], builder_map())
    leaked = [f"{marker} ({why})" for marker, why in PRIVATE_MARKERS.items() if marker in md]
    assert not leaked, (
        "The generated resume published a field the student was promised it "
        "would not:\n  " + "\n  ".join(leaked) + "\n--- document ---\n" + md
    )


def test_verified_and_self_reported_skills_stay_apart() -> None:
    """A mentor-verified skill and a self-typed one must not read alike.

    They are two different claims: one has evidence a mentor checked, the other
    is the student's own word. Printing them under one "Skills" heading presents
    work in review as work confirmed — the exact thing the verification flow
    exists to prevent — so the headings carry the difference.
    """
    md = _compose_resume_markdown("Test Student", None, ["MS Excel"], None, [], builder_map())
    assert "## Verified Skills" in md
    assert "## Key Expertise (self-reported)" in md
    assert "## Certifications (self-reported)" in md
    # And the verified list holds only what was passed as verified.
    verified_block = md.split("## Verified Skills", 1)[1].split("##", 1)[0]
    assert "MS Excel" in verified_block
    assert PUBLIC_MARKERS["key expertise"] not in verified_block


def test_an_empty_section_leaves_no_empty_heading() -> None:
    """"Publications — none" is a question a resume should not invite."""
    md = _compose_resume_markdown("Test Student", None, [], None, [], {})
    for heading in (
        "## Professional Experience",
        "## Internships",
        "## Projects",
        "## Publications & Research",
        "## References",
        "## Languages",
        "## Verified Skills",
    ):
        assert heading not in md, f"{heading} was printed with nothing under it"
    # The name and the education table still stand: a document with neither is
    # not a resume, and "not yet assessed" is a fact rather than an empty slot.
    assert md.startswith("# Test Student")
    assert "## Education" in md
    assert "Not yet assessed" in md


def test_a_row_the_student_never_named_is_not_a_claim() -> None:
    """An entry with no title is a row someone opened, not something they did."""
    md = _compose_resume_markdown(
        "Test Student",
        None,
        [],
        None,
        [],
        {"experience": [{"title": "  ", "org": "Ghost Corp", "description": "Never happened"}]},
    )
    assert "Ghost Corp" not in md
    assert "Never happened" not in md
    assert "## Professional Experience" not in md


def test_the_composer_survives_a_hostile_builder_map() -> None:
    """`resume_profiles.data` is an opaque map the client owns.

    Every leaf is whatever the browser last sent — a number where a string
    belongs, a null, a list where an object belongs, a string where a list
    belongs. None of it may raise: a 500 here is a student unable to produce a
    resume the day applications open.
    """
    hostile = {
        "basic": {"languages": "not-a-list"},
        "contact": {"personal_emails": [None, 42], "other_phones": "nope", "current_address": []},
        "experience": [None, 7, {"title": 5}, {"title": "Real Role", "description": None}],
        "projects": {"not": "a list"},
        "other": {"career_objective": 12, "achievements": [None], "key_expertise": {}},
        "references": [[]],
        "publications": None,
    }
    md = _compose_resume_markdown("Test Student", None, [], None, [], hostile)
    assert "Real Role" in md
    # And the same for the two degenerate cases the caller can actually produce:
    # a student with no ResumeProfile row at all (None) and a brand-new one ({}).
    assert _compose_resume_markdown("T", None, [], None, [], None).startswith("# T")
    assert _compose_resume_markdown("T", None, [], None, [], {}).startswith("# T")


# --------------------------------------------------------------------------- #
# Completeness (pure function)
# --------------------------------------------------------------------------- #


def test_form_furniture_does_not_count_as_completeness() -> None:
    """INCIDENT: 8% to 17% for one empty row and one unticked checkbox.

    Pressing "Add another number" seeds `{code: "+91", number: ""}` and
    unticking "same as current address" writes `permanent_same: false`, and the
    old rule counted a section filled if ANY leaf was non-empty. A twelfth of
    the bar moved for nothing typed, on a screen that advertises 70% as the
    point where the document gets materially stronger.
    """
    furniture = {
        "contact": {
            "other_phones": [{"code": "+91", "number": ""}],
            "personal_emails": [""],
            "permanent_same": False,
            "current_address": {"country": "India", "line1": "", "city": ""},
        }
    }
    assert _resume_completeness(furniture) == 0

    # One real character is the difference between furniture and content.
    typed = {
        "contact": {
            "other_phones": [{"code": "+91", "number": "9876543210"}],
            "permanent_same": False,
            "current_address": {"country": "India"},
        }
    }
    assert _resume_completeness(typed) > 0


def test_completeness_still_reaches_100_when_every_section_is_filled() -> None:
    """The stricter rule must not make the bar unreachable.

    A rule nobody can satisfy is worse than one anybody can: the sidebar would
    advise a threshold that does not exist, and the next person to "fix" it
    would loosen it back to counting empty rows.
    """
    assert _resume_completeness(builder_map()) == 100


def test_completeness_is_zero_for_a_profile_nobody_has_touched() -> None:
    assert _resume_completeness({}) == 0
    assert _resume_completeness({"basic": {}, "contact": {"permanent_same": True}}) == 0


# --------------------------------------------------------------------------- #
# End to end: the builder's content reaches the stored resume and its PDF
# --------------------------------------------------------------------------- #


@pytest.fixture
def student_with_a_filled_builder(make_user):
    """A throwaway student whose resume profile holds the marker map."""
    s = make_user("resume-doc")
    with SessionLocal() as db:
        student_id = db.scalar(select(Student.id).where(Student.user_id == s.user_id))
        db.add(ResumeProfile(student_id=student_id, data=builder_map()))
        db.commit()
    s.student_id = student_id
    yield s
    with SessionLocal() as db:
        db.execute(delete(Resume).where(Resume.student_id == student_id))
        db.execute(delete(ResumeProfile).where(ResumeProfile.student_id == student_id))
        db.execute(delete(StudentProfile).where(StudentProfile.student_id == student_id))
        db.commit()


@requires_db
def test_generate_puts_the_builder_on_the_page_and_in_the_pdf(
    client, student_with_a_filled_builder
):
    """The whole path: POST generate -> stored markdown -> rendered PDF."""
    s = student_with_a_filled_builder
    r = client.post("/api/student/resume/generate", json={"title": "Marker"}, headers=s.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    md = body["markdown"]

    missing = [name for name, marker in PUBLIC_MARKERS.items() if marker not in md]
    assert not missing, "generate dropped: " + ", ".join(sorted(missing))
    leaked = [m for m in PRIVATE_MARKERS if m in md]
    assert not leaked, "generate published private fields: " + ", ".join(sorted(leaked))

    # The PDF is a separate render of the same text, and it is what actually
    # reaches an employer. A composer fix that never reached the export would
    # look green here without the second half.
    pdf = client.get(f"/api/student/resume/{body['id']}/pdf", headers=s.headers)
    assert pdf.status_code == 200, pdf.text
    assert pdf.content.startswith(b"%PDF")
    assert len(pdf.content) > 2500, (
        "The exported PDF is barely larger than the empty five-line one, which "
        "is what a document missing every builder section looks like."
    )


@requires_db
def test_completeness_reported_by_the_api_matches_the_document(
    client, student_with_a_filled_builder
):
    """The number on the sidebar and the content of the page are one claim.

    100% next to a page missing the work is the pairing that started all of
    this, so the two are asserted together rather than in separate files.
    """
    s = student_with_a_filled_builder
    profile = client.get("/api/student/resume-profile", headers=s.headers)
    assert profile.status_code == 200, profile.text
    assert profile.json()["completeness"] == 100

    md = client.post("/api/student/resume/generate", json={}, headers=s.headers).json()["markdown"]
    assert PUBLIC_MARKERS["experience"] in md
    assert PUBLIC_MARKERS["referee"] in md


# --------------------------------------------------------------------------- #
# The mentor a student can actually name as a referee
# --------------------------------------------------------------------------- #


@requires_db
def test_profile_carries_the_real_mentor_name_or_none(client, make_user):
    """INCIDENT: the References step offered a hard-coded "Rakesh Iyer".

    One click put a person who does not work here onto a document a recruiter
    may call. The client cannot tell a real mentor from an invented one without
    being told, so the API tells it — and answers None when nobody is assigned,
    which is what turns the suggestion off rather than filling it with a guess.
    """
    s = make_user("resume-mentor")
    # GET /api/student/profile answers 404 without a profile row; the PUT is how
    # the app creates one, so the test creates it the same way the client does.
    seeded = client.put("/api/student/profile", json={"city": "Bengaluru"}, headers=s.headers)
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["mentor_name"] is None

    before = client.get("/api/student/profile", headers=s.headers)
    assert before.status_code == 200, before.text
    assert before.json()["mentor_name"] is None

    mentor_user = make_user("resume-mentor-staff", role=Role.MENTOR)
    with SessionLocal() as db:
        mentor = Mentor(user_id=mentor_user.user_id)
        db.add(mentor)
        db.flush()
        student = db.scalar(select(Student).where(Student.user_id == s.user_id))
        student.mentor_id = mentor.id
        db.commit()
        mentor_id = mentor.id
        student_id = student.id
        real_name = db.scalar(select(User.name).where(User.id == mentor_user.user_id))

    try:
        after = client.get("/api/student/profile", headers=s.headers)
        assert after.status_code == 200, after.text
        assert after.json()["mentor_name"] == real_name
    finally:
        with SessionLocal() as db:
            student = db.scalar(select(Student).where(Student.user_id == s.user_id))
            if student is not None:
                student.mentor_id = None
            db.execute(delete(StudentProfile).where(StudentProfile.student_id == student_id))
            # Commit the detach BEFORE removing the mentor: `students.mentor_id`
            # is a real FK, and a session that flushes the DELETE before the
            # UPDATE fails on the constraint.
            db.commit()
            db.execute(delete(Mentor).where(Mentor.id == mentor_id))
            db.commit()


@requires_db
def test_a_student_cannot_set_their_own_placement_eligibility(client, make_user):
    """`placement_eligible` is admin-set, and the resume builder used to offer a
    Yes/No radio for it above copy promising immediate removal from recruiter
    shortlists. The flag it wrote lived in the resume map, which nothing reads.

    The API side of that: the update endpoint ignores the field entirely, and
    this pins it so a later "just add it to ProfileUpdateIn" cannot pass review
    by being green.
    """
    s = make_user("resume-eligibility")
    with SessionLocal() as db:
        student_id = db.scalar(select(Student.id).where(Student.user_id == s.user_id))

    try:
        r = client.put(
            "/api/student/profile",
            json={"placement_eligible": False, "interested_in_jobs": False},
            headers=s.headers,
        )
        assert r.status_code == 200, r.text
        # The student's own interest flag moved; the office's gate did not.
        assert r.json()["interested_in_jobs"] is False
        assert r.json()["placement_eligible"] is True
    finally:
        with SessionLocal() as db:
            db.execute(delete(StudentProfile).where(StudentProfile.student_id == student_id))
            db.commit()


# --------------------------------------------------------------------------- #
# The evidence appendix
# --------------------------------------------------------------------------- #


def _png(width: int, height: int) -> bytes:
    """A valid PNG, so the image branch of the appendix is exercised for real."""
    raw = b"".join(b"\x00" + bytes((90, 60, 160)) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def test_the_appendix_binds_every_kind_of_proof() -> None:
    """INCIDENT: "Include an evidence appendix with proof links" did nothing.

    It flipped a signal that changed one sentence of consent copy; `exportPdf()`
    opened the same URL either way. A student who ticked it believed their
    certificates were attached and stopped attaching them.

    Three proof shapes in one test because the failure modes differ: a PDF is
    merged page-for-page, an image needs a page drawn around it, and a file the
    parser rejects must become a page that SAYS so rather than vanishing.
    """
    resume = render_resume_pdf("# Test Student\n\n## Verified Skills\nMS Excel")
    proofs = [
        EvidenceProof(
            skill="MS Excel",
            title="Excel certificate",
            original_name="excel.pdf",
            mime_type="application/pdf",
            content=render_resume_pdf("# Certificate of completion"),
            verified_on="09 Sep 2026",
        ),
        EvidenceProof(
            skill="Power BI",
            title="Dashboard screenshot",
            original_name="bi.png",
            mime_type="image/png",
            content=_png(200, 120),
        ),
        EvidenceProof(
            skill="Financial Modeling",
            title="Truncated file",
            original_name="broken.pdf",
            mime_type="application/pdf",
            content=b"%PDF-1.4 this is not a parseable document",
        ),
    ]
    out = append_evidence(resume, proofs)
    # resume + index + one page per proof, the unreadable one included.
    assert _pages(out) == _pages(resume) + 1 + 3
    assert out.startswith(b"%PDF")


def test_no_proofs_leaves_the_resume_byte_for_byte() -> None:
    """A student with nothing to attach must not get a lone "Evidence appendix"
    page announcing that they have no evidence."""
    resume = render_resume_pdf("# Test Student")
    assert append_evidence(resume, []) == resume


@requires_db
def test_the_appendix_carries_only_included_verified_proofs(client, make_user):
    """Two rules at once, because they are one decision.

    VERIFIED: a claim still with a mentor is not evidence.
    INCLUDED: the student curates which verified skills this copy presents, and
    an appendix carrying proofs the resume never mentions hands an employer
    answers to questions nobody asked.
    """
    s = make_user("resume-appendix")
    with SessionLocal() as db:
        student_id = db.scalar(select(Student.id).where(Student.user_id == s.user_id))

        shown = Skill(slug=f"shown-{uuid.uuid4().hex[:6]}", name="Shown Skill", category="Analytics")
        hidden = Skill(
            slug=f"hidden-{uuid.uuid4().hex[:6]}", name="Hidden Skill", category="Analytics"
        )
        # Verified=False but INCLUDED — the case that separates the two rules.
        # Without this row, dropping the `verified` filter changes nothing and
        # the test passes while the guard is gone (caught by mutation M8).
        unverified = Skill(
            slug=f"unver-{uuid.uuid4().hex[:6]}", name="Unverified Skill", category="Analytics"
        )
        db.add_all([shown, hidden, unverified])
        db.flush()

        uploads = []
        for label in ("shown", "hidden", "unverified"):
            content = render_resume_pdf(f"# {label} certificate")
            stored = f"test-appendix-{uuid.uuid4().hex}.pdf"
            (settings.uploads_path).mkdir(parents=True, exist_ok=True)
            (settings.uploads_path / stored).write_bytes(content)
            up = Upload(
                student_id=student_id,
                kind=UploadKind.CERTIFICATE_PROOF,
                title=f"{label} certificate",
                original_name=f"{label}.pdf",
                stored_name=stored,
                mime_type="application/pdf",
                size_bytes=len(content),
                status=UploadStatus.VERIFIED,
            )
            db.add(up)
            uploads.append(up)
        db.flush()

        db.add(
            StudentSkill(
                student_id=student_id,
                skill_id=shown.id,
                verified=True,
                evidence_upload_id=uploads[0].id,
            )
        )
        db.add(
            StudentSkill(
                student_id=student_id,
                skill_id=hidden.id,
                verified=True,
                evidence_upload_id=uploads[1].id,
            )
        )
        db.add(
            StudentSkill(
                student_id=student_id,
                skill_id=unverified.id,
                verified=False,
                evidence_upload_id=uploads[2].id,
            )
        )
        # `shown` is verified AND included -> the one proof that travels.
        # `hidden` is verified but not included -> curated out by the student.
        # `unverified` is included but not verified -> not evidence yet.
        db.add(
            ResumeProfile(
                student_id=student_id,
                data={"evidence_skills": {"included": [shown.slug, unverified.slug]}},
            )
        )
        db.commit()
        skill_ids = [shown.id, hidden.id, unverified.id]
        stored_names = [u.stored_name for u in uploads]

    try:
        made = client.post("/api/student/resume/generate", json={}, headers=s.headers)
        assert made.status_code == 200, made.text
        resume_id = made.json()["id"]

        plain = client.get(f"/api/student/resume/{resume_id}/pdf", headers=s.headers)
        withapp = client.get(
            f"/api/student/resume/{resume_id}/pdf?appendix=true", headers=s.headers
        )
        assert plain.status_code == 200 and withapp.status_code == 200

        # index page + exactly ONE proof: the included one.
        assert _pages(withapp.content) == _pages(plain.content) + 2
    finally:
        with SessionLocal() as db:
            db.execute(delete(StudentSkill).where(StudentSkill.student_id == student_id))
            db.execute(delete(Resume).where(Resume.student_id == student_id))
            db.execute(delete(ResumeProfile).where(ResumeProfile.student_id == student_id))
            db.execute(delete(StudentProfile).where(StudentProfile.student_id == student_id))
            db.execute(delete(Upload).where(Upload.student_id == student_id))
            db.execute(delete(Skill).where(Skill.id.in_(skill_ids)))
            db.commit()
        for stored in stored_names:
            (settings.uploads_path / stored).unlink(missing_ok=True)


@requires_db
def test_only_verified_skills_reach_the_verified_skills_line(client, make_user):
    """AGENTS.md: "ONLY VERIFIED SKILLS REACH THE DOCUMENT."

    Nothing pinned it. Deleting the `verified.is_(True)` filter from
    `generate_resume` left the whole suite green while every skill a student had
    ever added — including one a mentor had refused — printed under the heading
    "Verified Skills" on a document sent to an employer.
    """
    s = make_user("resume-verified-only")
    with SessionLocal() as db:
        student_id = db.scalar(select(Student.id).where(Student.user_id == s.user_id))
        good = Skill(
            slug=f"ok-{uuid.uuid4().hex[:6]}", name="Mentor Checked Skill", category="Analytics"
        )
        bad = Skill(
            slug=f"no-{uuid.uuid4().hex[:6]}", name="Self Asserted Skill", category="Analytics"
        )
        db.add_all([good, bad])
        db.flush()
        db.add(StudentSkill(student_id=student_id, skill_id=good.id, verified=True))
        db.add(StudentSkill(student_id=student_id, skill_id=bad.id, verified=False))
        db.commit()
        skill_ids = [good.id, bad.id]

    try:
        md = client.post("/api/student/resume/generate", json={}, headers=s.headers).json()[
            "markdown"
        ]
        assert "Mentor Checked Skill" in md
        assert "Self Asserted Skill" not in md, (
            "An unverified skill was printed on the resume. Only a mentor's "
            "verdict may put a skill under 'Verified Skills'."
        )
    finally:
        with SessionLocal() as db:
            db.execute(delete(StudentSkill).where(StudentSkill.student_id == student_id))
            db.execute(delete(Resume).where(Resume.student_id == student_id))
            db.execute(delete(StudentProfile).where(StudentProfile.student_id == student_id))
            db.execute(delete(Skill).where(Skill.id.in_(skill_ids)))
            db.commit()


@requires_db
def test_login_password_is_unchanged_by_this_module(client):
    """A guard on the fixture, not the feature.

    `make_user` accounts exist only while a test runs; if TEST_PASSWORD ever
    stops matching what the fixture writes, every test above fails with a 401
    and the failure reads as a broken resume endpoint.
    """
    assert TEST_PASSWORD
