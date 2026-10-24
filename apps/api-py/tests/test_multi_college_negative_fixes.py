"""The defects the 10-college negative sweep found (2026-10-13), pinned.

`testing/api/negative_multi_college.py` and `data_isolation_multi_college.py`
probe a live stack; this module pins the same failures inside the suite, so a
regression goes red in CI rather than on the next manual sweep. Each test names
the BUG number in `testing/docs/06-negative-multi-college-report.md`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update

from app import document_store, mail_transport
from app.config import settings
from app.db import SessionLocal
from app.models.redesign import AuditEvent
from app.models.cohort import Cohort
from app.models.institution import College, Department
from app.models.job import DegreeLevel
from app.models.registration import Registration
from app.models.user import Role, Student, User
from app.routers import registration as registration_router
from conftest import application_files, requires_db

ADMIN = ("admin@bgscet.ac.in", "admin123")


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    monkeypatch.setattr(registration_router, "_rate_windows", {})


@pytest.fixture
def spine():
    """Two colleges, one department and one batch each, removed afterwards."""
    tag = uuid.uuid4().hex[:6].upper()
    made: dict = {}
    with SessionLocal() as db:
        for side in ("A", "B"):
            college = College(code=f"NEG{side}{tag}", name=f"Negative {side} {tag}", email_domains=[])
            db.add(college)
            db.flush()
            dept = Department(college_id=college.id, code="CSE", name="CSE")
            db.add(dept)
            db.flush()
            cohort = Cohort(
                code=f"NEG{side}{tag}-B", name="2026-30", batch_label="2026-30",
                degree_level=DegreeLevel.UG, department_id=dept.id,
                start_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
                end_date=datetime(2030, 6, 30, tzinfo=timezone.utc),
            )
            db.add(cohort)
            db.flush()
            made[side] = {"college": college.id, "dept": dept.id, "cohort": cohort.id}
        db.commit()
    yield made
    with SessionLocal() as db:
        cohorts = [m["cohort"] for m in made.values()]
        depts = [m["dept"] for m in made.values()]
        db.execute(update(Student).where(Student.cohort_id.in_(cohorts)).values(cohort_id=None))
        db.execute(update(Student).where(Student.department_id.in_(depts)).values(department_id=None))
        db.execute(update(User).where(User.department_id.in_(depts)).values(department_id=None))
        db.execute(delete(Registration).where(Registration.college_id.in_([m["college"] for m in made.values()])))
        db.execute(delete(Cohort).where(Cohort.id.in_(cohorts)))
        db.execute(delete(Department).where(Department.id.in_(depts)))
        db.execute(delete(College).where(College.id.in_([m["college"] for m in made.values()])))
        db.commit()


# ------------------------------------------------------------------ BUG-1 --


@requires_db
def test_a_student_cannot_be_moved_away_from_their_mentors_college(client, login, make_user, spine):
    """BUG-1. Seating a student in another college's batch kept their mentor,
    a faculty member of the first college, reading them through rule 2. All
    three move paths refuse it now, and leave the student where they were."""
    admin = login(*ADMIN)
    student = make_user("negmove")
    faculty = make_user("negmovefac", Role.MENTOR)
    with SessionLocal() as db:
        db.execute(update(User).where(User.id == faculty.user_id).values(department_id=spine["A"]["dept"]))
        sid = db.scalar(select(Student.id).where(Student.user_id == student.user_id))
        db.commit()

    r = client.put(f"/api/admin/students/{sid}/cohort", json={"cohort_id": spine["A"]["cohort"]}, headers=admin)
    assert r.status_code == 204, r.text
    r = client.post(
        f"/api/admin/students/{sid}/mentor",
        json={"mentor_user_id": faculty.user_id, "reason": "first assignment"}, headers=admin,
    )
    assert r.status_code == 204, r.text

    to_b = spine["B"]["cohort"]
    put = client.put(f"/api/admin/students/{sid}/cohort", json={"cohort_id": to_b}, headers=admin)
    patch = client.patch(f"/api/admin/students/{sid}", json={"cohort_id": to_b}, headers=admin)
    bulk = client.post(
        f"/api/admin/cohorts/{spine['A']['cohort']}/students/bulk",
        json={"action": "move", "cohort_id": to_b}, headers=admin,
    )
    for name, resp in (("PUT cohort", put), ("PATCH student", patch), ("bulk move", bulk)):
        assert resp.status_code == 422, f"{name}: {resp.status_code} {resp.text}"
        assert "mentor" in resp.text
    with SessionLocal() as db:
        assert db.scalar(select(Student.cohort_id).where(Student.id == sid)) == spine["A"]["cohort"]


# ------------------------------------------------------------------ BUG-2 --


def _apply(client, college_id: str, department_id: str, **override):
    usn = f"1NEG{uuid.uuid4().hex[:6].upper()}"
    data = {
        "name": "Negative Applicant", "email": f"{usn.lower()}@bgscet.ac.in", "usn": usn,
        "phone": "+91 98765 43210", "personal_email": f"{usn.lower()}@gmail.com",
        "linkedin_url": "linkedin.com/in/negative", "degree_level": "UG",
        "college_id": college_id, "department_id": department_id,
    }
    data.update(override)
    return client.post("/api/register", data=data, files=application_files())


@requires_db
@pytest.mark.parametrize("closed", ["college", "department"])
def test_an_archived_college_or_department_takes_no_application(client, spine, closed):
    """BUG-2. The public hierarchy hides an archived rung; the API did not, so a
    stale page or a hand-made request landed an application in the queue."""
    a = spine["A"]
    with SessionLocal() as db:
        model, ident = (College, a["college"]) if closed == "college" else (Department, a["dept"])
        db.execute(update(model).where(model.id == ident).values(status="ARCHIVED"))
        db.commit()
    r = _apply(client, a["college"], a["dept"])
    assert r.status_code == 422, r.text
    assert "not taking applications" in r.text


# ------------------------------------------------------------------ BUG-3 --


@requires_db
def test_blank_codes_and_names_are_refused_on_every_rung(client, login, spine):
    """BUG-3. `min_length=1` was checked before `.strip()`, so "   " was stored
    as "" on every create and as "   " on every PATCH."""
    admin = login(*ADMIN)
    a = spine["A"]
    blank = "   "
    cases = [
        ("POST", "/api/admin/colleges", {"code": blank, "name": "x"}),
        ("POST", "/api/admin/colleges", {"code": f"NB{uuid.uuid4().hex[:5]}", "name": blank}),
        ("PATCH", f"/api/admin/colleges/{a['college']}", {"name": blank}),
        ("PATCH", f"/api/admin/colleges/{a['college']}", {"code": blank}),
        ("POST", f"/api/admin/colleges/{a['college']}/departments", {"code": blank, "name": "x"}),
        ("POST", f"/api/admin/colleges/{a['college']}/departments", {"code": "NBX", "name": blank}),
        ("PATCH", f"/api/admin/departments/{a['dept']}", {"code": blank}),
        ("POST", f"/api/admin/departments/{a['dept']}/academic-courses", {"code": blank, "name": "x"}),
        ("POST", f"/api/admin/departments/{a['dept']}/cohorts", {
            "code": blank, "name": "x", "batch_label": "2026-30", "degree_level": "UG",
            "entry_date": "2026-07-01", "expected_completion": "2030-06-30"}),
        ("PATCH", f"/api/admin/cohorts/{a['cohort']}", {"name": blank}),
    ]
    for method, path, body in cases:
        r = client.request(method, path, json=body, headers=admin)
        assert r.status_code == 422, f"{method} {path} {body}: {r.status_code} {r.text}"
    with SessionLocal() as db:
        assert db.scalar(select(College.name).where(College.id == a["college"])).strip()
        assert db.scalar(select(Cohort.name).where(Cohort.id == a["cohort"])).strip()


@requires_db
def test_the_public_form_refuses_a_blank_name_and_a_phone_that_is_not_one(client, spine):
    """BUG-3 on the public form, and BUG-5."""
    a = spine["A"]
    assert _apply(client, a["college"], a["dept"], name="    ").status_code == 422
    assert _apply(client, a["college"], a["dept"], phone="call me maybe").status_code == 422
    assert _apply(client, a["college"], a["dept"], phone="12").status_code == 422
    assert _apply(client, a["college"], a["dept"], phone=" +91 (98765) 43210 ").status_code == 201


# ------------------------------------------------------------------ BUG-4 --


def test_a_college_domain_must_be_a_domain_and_not_public_mail():
    from app.institution_domains import domain_problem

    assert domain_problem("bgscet.ac.in") is None
    assert domain_problem("college.edu") is None
    for bad in ("not a domain", "http://x.com/", "@@", "localhost", "x.com/path"):
        assert domain_problem(bad), bad
    assert "public mail" in domain_problem("gmail.com")


@requires_db
def test_a_college_cannot_list_gmail_as_its_own_domain(client, login, spine):
    admin = login(*ADMIN)
    for domains in (["gmail.com"], ["not a domain"], ["http://x.com/"]):
        r = client.patch(f"/api/admin/colleges/{spine['A']['college']}", json={"email_domains": domains}, headers=admin)
        assert r.status_code == 422, (domains, r.text)
    r = client.patch(
        f"/api/admin/colleges/{spine['A']['college']}", json={"email_domains": ["@NegA.Edu.In "]}, headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["email_domains"] == ["nega.edu.in"]


# ------------------------------------------------------------------ BUG-7 --


def test_the_container_turns_off_uvicorns_own_access_log():
    """BUG-7. uvicorn's access log prints the query string, the Google
    callback's one-time code included; `reep.access` already covers every
    request without it."""
    cmd = Path(__file__).resolve().parents[1].joinpath("Dockerfile").read_text().split("CMD [", 1)[1]
    assert '"--no-access-log"' in cmd.split("]", 1)[0]


# ------------------------------------------------------------------ BUG-8 --


def test_with_no_transport_outside_dev_a_mail_fails_and_its_body_is_not_logged(monkeypatch, caplog):
    """BUG-8. The console transport logged the whole body (a bearer link) and
    returned normally, which `deliver_once` records as SENT. Outside dev/CI it
    now raises without the address or the body."""
    monkeypatch.setattr(settings, "env", "prod")
    monkeypatch.setattr(settings, "ses_from_address", "")
    before = len(mail_transport.outbox)
    with caplog.at_level(logging.INFO, logger="app.mail_transport"):
        with pytest.raises(RuntimeError):
            mail_transport.send("someone@bgscet.ac.in", "Set up", "https://x/onboard?token=SECRET123")
    assert "SECRET123" not in caplog.text and "someone@bgscet.ac.in" not in caplog.text
    assert len(mail_transport.outbox) == before


def test_in_dev_the_console_transport_logs_one_line(monkeypatch, caplog):
    monkeypatch.setattr(settings, "env", "dev")
    monkeypatch.setattr(settings, "ses_from_address", "")
    with caplog.at_level(logging.INFO, logger="app.mail_transport"):
        mail_transport.send("dev@bgscet.ac.in", "Hi", "line one\nline two")
    lines = [r.getMessage() for r in caplog.records if r.name == "app.mail_transport"]
    assert lines and all("\n" not in line for line in lines)


# ------------------------------------------------------------- BUG-9, 10 --


@requires_db
def test_institution_changes_are_audited(client, login, spine):
    """BUG-9. Creating and editing the spine wrote no audit row."""
    admin = login(*ADMIN)
    r = client.post(
        f"/api/admin/colleges/{spine['A']['college']}/departments",
        json={"code": f"AU{uuid.uuid4().hex[:4]}", "name": "Audited"}, headers=admin,
    )
    assert r.status_code == 201, r.text
    dept_id = r.json()["id"]
    r = client.patch(f"/api/admin/departments/{dept_id}", json={"name": "Audited, renamed"}, headers=admin)
    assert r.status_code == 200, r.text
    try:
        with SessionLocal() as db:
            rows = db.scalars(
                select(AuditEvent).where(AuditEvent.entity_type == "department", AuditEvent.entity_id == dept_id)
            ).all()
            actions = sorted(row.action for row in rows)
            assert actions == ["CREATED", "UPDATED"], actions
            updated = next(row for row in rows if row.action == "UPDATED")
            assert updated.before_json["name"] == "Audited"
            assert updated.after_json["name"] == "Audited, renamed"
    finally:
        with SessionLocal() as db:
            db.execute(delete(Department).where(Department.id == dept_id))
            db.commit()


@requires_db
def test_the_registration_log_names_no_applicant(client, spine, caplog):
    """BUG-10. The INFO line carried the applicant's address."""
    a = spine["A"]
    with caplog.at_level(logging.INFO, logger="app.routers.registration"):
        r = _apply(client, a["college"], a["dept"])
    assert r.status_code == 201, r.text
    assert r.json()["email"] not in caplog.text
