"""The standard resume template, and the update / delete half of the builder.

Every version the Resume Builder creates or regenerates is laid out in ONE
order (`RESUME_TEMPLATE_SECTIONS`): a placement office reads a batch's resumes
side by side, and a recruiter reads thirty from one college, so a section in a
different place on every page is noise. The pure tests pin the order and the
Education table; the DB tests walk create -> read -> update -> regenerate ->
delete and the two refusals that keep a finalised copy and another student's
copy out of reach.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models.academic_history import QualificationLevel
from app.models.resume import Resume, ResumeStatus
from app.models.user import Student
from app.resume_pdf import render_resume_pdf
from app.routers.student import (
    RESUME_TEMPLATE_ID,
    RESUME_TEMPLATE_SECTIONS,
    _compose_resume_markdown,
    _headings,
    _polish_keeps_template,
)
from tests.conftest import requires_db
from tests.test_resume_document import builder_map


def _qual(level, institution, year, marks, board=None):
    return SimpleNamespace(
        level=level, institution=institution, board=board, year=year, marks=marks, max_marks=100
    )


QUALS = [
    _qual(QualificationLevel.TENTH, "St Joseph's", 2017, 91, "CBSE"),
    _qual(QualificationLevel.UNDERGRAD, "Christ University", 2024, 78),
    _qual(QualificationLevel.TWELFTH, "Jain PU College", 2019, 85, "Karnataka PU Board"),
]
PROGRAMME = {"course": "General MBA", "college": "BGSCET", "batch": "2026-28"}


def _full() -> str:
    return _compose_resume_markdown(
        "Asha Rao", None, ["MS Excel"], 8.4, QUALS, builder_map(), PROGRAMME
    )


def test_every_section_follows_the_template_order() -> None:
    """A full profile prints every template section, in the template's order."""
    assert _headings(_full()) == list(RESUME_TEMPLATE_SECTIONS)


def test_a_sparse_profile_keeps_the_order_of_what_it_has() -> None:
    """Empty sections drop out; the ones that remain keep their relative order."""
    headings = _headings(_compose_resume_markdown("T", None, [], None, [], {}))
    assert headings == [h for h in RESUME_TEMPLATE_SECTIONS if h in headings]
    assert headings[0] == "Education"
    assert headings[-1] == "Declaration"


def test_education_is_a_table_with_the_programme_first_then_newest_first() -> None:
    md = _full()
    block = md.split("## Education", 1)[1].split("\n## ", 1)[0]
    rows = [ln for ln in block.splitlines() if ln.startswith("|")]
    assert rows[0] == "| Qualification | Institution / Board | Year | Score |"
    assert rows[2] == "| General MBA | BGSCET | 2026-28 | CGPA 8.4 |"
    assert [r.split("|")[1].strip() for r in rows[3:]] == ["Graduation", "Class XII", "Class X"]
    assert "Jain PU College, Karnataka PU Board" in rows[4]


def test_the_contact_line_sits_in_the_header_not_under_a_label() -> None:
    md = _full()
    header = md.split("\n## ", 1)[0]
    assert "markeremail@example.com" in header
    assert "**Contact:**" not in md


def test_a_pipe_in_the_data_cannot_split_a_table_row() -> None:
    quals = [_qual(QualificationLevel.UNDERGRAD, "A | B College", 2024, 70)]
    md = _compose_resume_markdown("T", None, [], None, quals, {}, None)
    row = [ln for ln in md.splitlines() if "B College" in ln][0]
    assert row.count("|") == 5


def test_a_polish_that_moves_a_section_is_refused() -> None:
    draft = _full()
    assert _polish_keeps_template(draft, draft)
    reordered = draft.replace("## Education", "## Schooling")
    assert not _polish_keeps_template(draft, reordered)
    assert not _polish_keeps_template(draft, "")


def test_a_polish_that_changes_the_education_table_is_refused() -> None:
    """A score or an institution the model rewrote is not on the student's record."""
    draft = _full()
    assert "CGPA 8.4" in draft and "Christ University" in draft
    assert not _polish_keeps_template(draft, draft.replace("CGPA 8.4", "CGPA 9.4"))
    assert not _polish_keeps_template(draft, draft.replace("Christ University", "IIM Bangalore"))
    # A dropped row is a change too.
    no_tenth = "\n".join(ln for ln in draft.splitlines() if "Class X |" not in ln)
    assert not _polish_keeps_template(draft, no_tenth)


def test_a_polish_that_only_repads_the_table_is_kept() -> None:
    """Whitespace and separator style are formatting, not facts."""
    draft = _full()
    repadded = draft.replace("| --- | --- | --- | --- |", "|:---|:---|:---|:---|").replace(
        "| General MBA | BGSCET |", "|  General MBA  |  BGSCET  |"
    )
    assert repadded != draft
    assert _polish_keeps_template(draft, repadded)
    # And prose outside the table may change freely.
    assert _polish_keeps_template(draft, draft.replace("MARKEROBJECTIVE", "A sharper objective"))


def test_a_line_of_only_separators_is_text_not_a_crash() -> None:
    """A student's own `|---|` line must not 500 the download."""
    for md in ("# T\n|---|\n", "# T\n|-|\n|:-:|\nAfter\n"):
        assert render_resume_pdf(md).startswith(b"%PDF-")


def test_the_template_renders_to_a_pdf() -> None:
    pdf = render_resume_pdf(_full(), fallback_title="Asha Rao")
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 3000


# --------------------------------------------------------------------------- #
# Create, read, update, delete — through the API
# --------------------------------------------------------------------------- #


@pytest.fixture
def resume_student(make_user):
    s = make_user("resume-crud")
    yield s
    with SessionLocal() as db:
        sid = db.scalar(select(Student.id).where(Student.user_id == s.user_id))
        db.execute(delete(Resume).where(Resume.student_id == sid))
        db.commit()


@requires_db
def test_create_read_update_regenerate_delete(client, resume_student):
    h = resume_student.headers

    made = client.post("/api/student/resume/generate", json={"title": "General"}, headers=h)
    assert made.status_code == 200, made.text
    assert made.json()["template"] == RESUME_TEMPLATE_ID
    rid = made.json()["id"]

    one = client.get(f"/api/student/resume/{rid}", headers=h)
    assert one.status_code == 200, one.text
    assert one.json()["template"] == RESUME_TEMPLATE_ID
    assert "## Education" in one.json()["markdown"]

    renamed = client.patch(
        f"/api/student/resume/{rid}",
        json={"title": "Finance roles", "target_role": "Analyst"},
        headers=h,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Finance roles"
    assert renamed.json()["target_role"] == "Analyst"
    assert client.patch(f"/api/student/resume/{rid}", json={"title": "  "}, headers=h).status_code == 422
    assert client.patch(f"/api/student/resume/{rid}", json={"status": "DRAFT"}, headers=h).status_code == 422

    # Finalised is locked against regenerate and delete.
    assert (
        client.patch(f"/api/student/resume/{rid}", json={"status": "FINALISED"}, headers=h).status_code
        == 200
    )
    assert client.post(f"/api/student/resume/{rid}/regenerate", headers=h).status_code == 409
    assert client.delete(f"/api/student/resume/{rid}", headers=h).status_code == 409

    assert (
        client.patch(f"/api/student/resume/{rid}", json={"status": "GENERATED"}, headers=h).status_code
        == 200
    )
    again = client.post(f"/api/student/resume/{rid}/regenerate", headers=h)
    assert again.status_code == 200, again.text
    assert again.json()["id"] == rid
    assert again.json()["version"] == made.json()["version"]

    # A version finalised while its regeneration was composing stays locked.
    from unittest import mock

    from app.routers import student as student_router

    real_polish = student_router._polish

    def finalise_mid_call(db, markdown):
        with SessionLocal() as other:
            other.get(Resume, rid).status = ResumeStatus.FINALISED
            other.commit()
        return real_polish(db, markdown)

    with mock.patch.object(student_router, "_polish", finalise_mid_call):
        raced = client.post(f"/api/student/resume/{rid}/regenerate", headers=h)
    assert raced.status_code == 409, raced.text
    assert (
        client.patch(f"/api/student/resume/{rid}", json={"status": "GENERATED"}, headers=h).status_code
        == 200
    )

    gone = client.delete(f"/api/student/resume/{rid}", headers=h)
    assert gone.status_code == 204, gone.text
    assert client.get(f"/api/student/resume/{rid}", headers=h).status_code == 404
    assert all(r["id"] != rid for r in client.get("/api/student/resume", headers=h).json())


@requires_db
def test_another_students_resume_is_a_404_on_every_verb(client, make_user, resume_student):
    made = client.post(
        "/api/student/resume/generate", json={}, headers=resume_student.headers
    )
    rid = made.json()["id"]

    h = make_user("resume-crud-other").headers
    assert client.get(f"/api/student/resume/{rid}", headers=h).status_code == 404
    assert client.patch(f"/api/student/resume/{rid}", json={"title": "x"}, headers=h).status_code == 404
    assert client.post(f"/api/student/resume/{rid}/regenerate", headers=h).status_code == 404
    assert client.delete(f"/api/student/resume/{rid}", headers=h).status_code == 404
