"""GET /api/student/resume — the versions the Export & share step lists.

Each row carries `created_at`, the "Updated …" line beside a version's name.
It is additive on an existing response: the six older fields are unchanged,
the list is newest-first, and a student sees their own rows only.
"""

from datetime import datetime

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models.resume import Resume, ResumeStatus
from app.models.user import Student
from tests.conftest import requires_db


@pytest.fixture
def two_resumes(make_user):
    """A throwaway student holding two composed resumes, torn down after."""
    s = make_user("resume-list")
    with SessionLocal() as db:
        student_id = db.scalar(select(Student.id).where(Student.user_id == s.user_id))
        db.add(
            Resume(
                student_id=student_id,
                version=1,
                title="General resume",
                status=ResumeStatus.GENERATED,
                markdown="# General",
            )
        )
        db.add(
            Resume(
                student_id=student_id,
                version=2,
                title="Financial Analyst — tailored",
                status=ResumeStatus.GENERATED,
                markdown="# Tailored",
            )
        )
        db.commit()
    yield s
    with SessionLocal() as db:
        student_id = db.scalar(select(Student.id).where(Student.user_id == s.user_id))
        db.execute(delete(Resume).where(Resume.student_id == student_id))
        db.commit()


@requires_db
def test_resume_list_rows_carry_created_at(client, two_resumes):
    r = client.get("/api/student/resume", headers=two_resumes.headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    # Both rows, newest-first; two rows committed together may share a
    # timestamp, so the ORDER is not asserted — only that nothing is missing.
    assert sorted(row["version"] for row in rows) == [1, 2]
    for row in rows:
        assert set(row) == {
            "id",
            "version",
            "title",
            "status",
            "generated_by",
            "model",
            "created_at",
            # Additive (2026-10-13), for the builder's update and delete controls.
            "updated_at",
            "target_role",
            "template",
        }
        # A real timestamp, parseable, never null for a stored row.
        assert row["created_at"] is not None
        datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))


@requires_db
def test_resume_list_is_empty_for_a_fresh_student(client, make_user):
    s = make_user("resume-list-empty")
    r = client.get("/api/student/resume", headers=s.headers)
    assert r.status_code == 200, r.text
    assert r.json() == []
