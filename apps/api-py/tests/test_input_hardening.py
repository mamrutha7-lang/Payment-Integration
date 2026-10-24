"""Bad input is a 422 that says what is wrong, never a 500.

Every case here was a 500 until 2026-09-30 and was found by Schemathesis
fuzzing the API from its own OpenAPI document (testing/api/run_schemathesis.sh;
the DEF numbers are testing/docs/05-incident-reports.md). The pattern was the
same each time: a value the handler never looked at reached the database, and
Postgres refused it on the way in (a NOT NULL, a foreign key, an integer range,
a NUL byte), which surfaced as an IntegrityError or DataError and a 500.
"""
from __future__ import annotations

from sqlalchemy import delete

from conftest import requires_db

from app.db import SessionLocal
from app.models.job import Job
from app.models.user import Role
from app.routers.console import CriteriaIn, JobIn
from app.routers.swoc import SwocEntryPatch


# --------------------------------------------------------------- DEF-002 --

@requires_db
def test_a_null_for_a_required_profile_field_is_422_and_names_it(client, make_user):
    student = make_user("hard-prof")
    r = client.put("/api/student/profile", json={"interested_in_jobs": None}, headers=student.headers)
    assert r.status_code == 422, r.text
    assert "interested_in_jobs" in r.json()["detail"]
    # A nullable field may still be cleared, and a real value still saves.
    ok = client.put(
        "/api/student/profile",
        json={"city": None, "leaderboard_opt_out": True},
        headers=student.headers,
    )
    assert ok.status_code == 200, ok.text


# --------------------------------------------------------------- DEF-003 --

@requires_db
def test_checking_in_to_a_course_that_does_not_exist_is_422(client, make_user):
    student = make_user("hard-checkin")
    r = client.post(
        "/api/student/checkin",
        json={"course_code": "NO-SUCH-COURSE", "module": "m1"},
        headers=student.headers,
    )
    assert r.status_code == 422, r.text
    assert "NO-SUCH-COURSE" in r.json()["detail"]


# --------------------------------------------------------------- DEF-004 --

def test_a_blank_course_or_college_id_means_none_chosen():
    """What a form's empty <select> option posts. As "" it skipped the
    existence checks (a truth test) and reached the INSERT."""
    assert JobIn(title="t", company="c", course_id="", college_id="  ").course_id is None
    assert JobIn(title="t", company="c", college_id="  ").college_id is None
    criteria = CriteriaIn(course_id="", college_id="c1")
    assert criteria.course_id is None and criteria.college_id == "c1"


@requires_db
def test_a_job_posted_with_a_blank_course_is_published_to_everyone(client, make_user):
    admin = make_user("hard-jobs", Role.ADMIN)
    r = client.post(
        "/api/admin/jobs",
        json={"title": "Hardening probe", "company": "REEP", "course_id": "", "college_id": ""},
        headers=admin.headers,
    )
    try:
        assert r.status_code == 201, r.text
    finally:
        with SessionLocal() as db:
            db.execute(delete(Job).where(Job.title == "Hardening probe", Job.company == "REEP"))
            db.commit()
    unknown = client.post(
        "/api/admin/jobs",
        json={"title": "Hardening probe", "company": "REEP", "course_id": "no-such-course"},
        headers=admin.headers,
    )
    assert unknown.status_code == 422, unknown.text


# --------------------------------------------------------------- DEF-005 --

def test_a_blank_swoc_link_removes_the_link_rather_than_naming_nothing():
    patch = SwocEntryPatch(linked_job_id="", linked_skill_id="  ")
    assert patch.linked_job_id is None and patch.linked_skill_id is None
    # Still "sent", so the PATCH clears the link instead of ignoring the field.
    assert {"linked_job_id", "linked_skill_id"} <= patch.model_fields_set


# --------------------------------------------------------------- DEF-006 --

@requires_db
def test_out_of_range_numbers_in_list_queries_are_422(client, make_user):
    admin = make_user("hard-lists", Role.ADMIN)
    for url in (
        "/api/admin/audit?page=92917030724915670548480",
        "/api/admin/swoc?semester=-954555328274038435872768",
        "/api/admin/swoc?page=100001",
        "/api/admin/placement?year=-5466912982786",
        "/api/admin/mentor-load?page=0",
    ):
        r = client.get(url, headers=admin.headers)
        assert r.status_code == 422, (url, r.status_code, r.text[:200])
    assert client.get("/api/admin/placement?year=2026", headers=admin.headers).status_code == 200


# --------------------------------------------------------------- DEF-007 --

@requires_db
def test_a_nul_byte_in_text_is_422_and_the_log_carries_no_value(client, make_user, caplog):
    student = make_user("hard-nul")
    with caplog.at_level("WARNING", logger="reep.data_error"):
        r = client.put("/api/student/profile", json={"city": "Bengal\u0000uru"}, headers=student.headers)
    assert r.status_code == 422, r.text
    assert "NUL character" in r.json()["detail"]
    logged = " ".join(rec.getMessage() for rec in caplog.records if rec.name == "reep.data_error")
    assert "/api/student/profile" in logged
    assert "Bengal" not in logged, "the refused value must never reach a log line"


# --------------------------------------------------------------- DEF-008 --

def test_the_published_422_schema_admits_a_sentence_as_well_as_a_list():
    from app.main import app

    detail = app.openapi()["components"]["schemas"]["HTTPValidationError"]["properties"]["detail"]
    kinds = {branch.get("type") for branch in detail["anyOf"]}
    assert kinds == {"array", "string"}
