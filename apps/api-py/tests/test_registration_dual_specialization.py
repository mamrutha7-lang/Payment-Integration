"""The register form's Specialization box is a CHECKLIST (2026-09-22).

Some students opt for a DUAL specialization — Finance and Marketing, say — and
the <select> the box used to be let them name one of the two, so the office
learned of the other by phone or not at all. The API now takes
`specialization_ids`, at most two, all under one course; the first goes into
`specialization_id` (unchanged for every reader that column already had) and
the other into `second_specialization_id` (migration d4c8e1f7a2b9). Pinned
here: the cap and the merge with the legacy single field (schema-level, no
database); both picks travel by id and by name to the applicant AND the
reviewer; the reviewer's checklist earns a `dual_specialization` WARN; a
batch that pins one of the two is honoured and one that pins neither is the
same contradiction it always was; two picks from two courses are refused; and
a reviewer scoped to EITHER specialization sees the row.

Fixtures borrowed by name from test_admin_institution.py and
test_registration_hierarchy.py, as that module does from its own neighbours.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from conftest import requires_db
from test_admin_institution import (  # noqa: F401 — fixtures by name
    _code,
    chain,
    director,
    levels,
    tracker,
)
from test_registration_hierarchy import applicant  # noqa: F401
from test_scoped_lists import scoped_grant  # noqa: F401

from app.db import SessionLocal
from app.models.governance import ScopeLevel
from app.models.registration import Registration
from app.models.user import Role
from app.policies import Reach
from app.routers import registration as registration_router
from app.routers.registration import (
    CHECK_DUAL_SPECIALIZATION,
    CHECK_WARN,
    MAX_SPECIALIZATIONS_PER_APPLICATION,
    RegisterIn,
)
from app.scope_views import registration_scope_clause

TAG = uuid.uuid4().hex[:6]


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    """One address for every TestClient request, and this module posts more
    applications than the public limiter allows one network in a window."""
    monkeypatch.setattr(registration_router, "_rate_windows", {})


# ------------------------------------------------------- the schema, no DB --


def _body(**override) -> dict:
    body = {
        "name": "Dual Applicant",
        "email": f"dual.{uuid.uuid4().hex[:8]}@bgscet.ac.in",
        "usn": f"1BG26DUA{uuid.uuid4().hex[:3].upper()}",
        "phone": "+91 90000 00000",
        "personal_email": f"dual.{uuid.uuid4().hex[:8]}@gmail.com",
        "linkedin_url": "linkedin.com/in/dual-applicant",
        "degree_level": "PG",
    }
    body.update(override)
    return body


def test_the_cap_is_two_and_the_schema_refuses_a_third():
    """Two, because the office's word is DUAL and the row has two columns. A
    third tick is a 422, never a silent truncation to the first two."""
    assert MAX_SPECIALIZATIONS_PER_APPLICATION == 2
    assert RegisterIn(**_body(specialization_ids=["a", "b"])).specialization_ids == ["a", "b"]
    with pytest.raises(ValidationError) as refused:
        RegisterIn(**_body(specialization_ids=["a", "b", "c"]))
    assert "dual specialization" in str(refused.value)


def test_the_legacy_single_field_is_folded_in_first_and_a_double_tick_is_one():
    """An older client sends `specialization_id`; a client sending both with
    the same pick has ticked one box, not two. Order is the order ticked, with
    the legacy field leading, and blanks are not picks."""
    merged = RegisterIn(**_body(specialization_id="b", specialization_ids=["a", "b"]))
    assert merged.specialization_ids == ["b", "a"]
    assert merged.specialization_id == "b", "the single field mirrors the first pick"
    assert RegisterIn(**_body(specialization_ids=[" a ", "a", "", "  "])).specialization_ids == ["a"]
    empty = RegisterIn(**_body())
    assert empty.specialization_ids == [] and empty.specialization_id is None
    # The legacy field and two ticks is three picks, and the cap counts them all.
    with pytest.raises(ValidationError):
        RegisterIn(**_body(specialization_id="c", specialization_ids=["a", "b"]))


def test_the_queue_scope_reads_both_specialization_columns():
    """A reviewer whose grant reaches only Marketing must still list an
    application that ticked Finance AND Marketing: the row hangs under both."""
    clause = registration_scope_clause(Reach(everything=False, specializations=frozenset({"spec-x"})))
    sql = str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "registrations.specialization_id IN" in sql
    assert "registrations.second_specialization_id IN" in sql


# --------------------------------------------------------- the fixture --


@pytest.fixture
def streams(client, levels, tracker):
    """A second stream under the chain's course (Marketing, beside Finance) and
    a stream under a DIFFERENT course, both via the admin API."""
    h = levels["headers"]
    marketing = client.post(
        f"/api/admin/academic-courses/{levels['course']['id']}/academic-specializations",
        headers=h,
        json={"code": _code("MKT"), "name": "Marketing"},
    )
    assert marketing.status_code == 201, marketing.text
    tracker["specializations"].append(marketing.json()["id"])

    other_course = client.post(
        f"/api/admin/departments/{levels['department']['id']}/academic-courses",
        headers=h,
        json={"code": _code("MCA"), "name": "Master of Computer Applications", "duration_months": 24},
    )
    assert other_course.status_code == 201, other_course.text
    tracker["courses"].append(other_course.json()["id"])
    elsewhere = client.post(
        f"/api/admin/academic-courses/{other_course.json()['id']}/academic-specializations",
        headers=h,
        json={"code": _code("DS"), "name": "Data Science"},
    )
    assert elsewhere.status_code == 201, elsewhere.text
    tracker["specializations"].append(elsewhere.json()["id"])
    return {
        **levels,
        "finance": levels["specialization"],
        "marketing": marketing.json(),
        "elsewhere": elsewhere.json(),
    }


def _row(email: str) -> Registration:
    with SessionLocal() as db:
        return db.scalar(select(Registration).where(Registration.email == email))


# ------------------------------------------------------------ the walk --


@requires_db
def test_two_ticks_travel_to_the_applicant_and_the_reviewer_by_id_and_by_name(client, applicant, streams):
    post, _ = applicant
    email = f"dual.two.{TAG}@bgscet.ac.in"
    r = post(
        client, email, usn="1BG26DUT01",
        specialization_ids=[streams["finance"]["id"], streams["marketing"]["id"]],
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["specialization_id"] == streams["finance"]["id"]
    assert body["second_specialization_id"] == streams["marketing"]["id"]
    assert body["specialization_name"] == "Finance"
    assert body["second_specialization_name"] == "Marketing"
    assert body["course_id"] == streams["course"]["id"], "the course is settled from the first tick, as ever"
    assert body["department_id"] == streams["department"]["id"]

    row = _row(email)
    assert row.second_specialization_id == streams["marketing"]["id"]

    rows = client.get("/api/register/pending", headers=streams["headers"]).json()
    mine = next(x for x in rows if x["id"] == row.id)
    assert mine["specialization_name"] == "Finance" and mine["second_specialization_name"] == "Marketing"
    dual = next(c for c in mine["checks"] if c["key"] == CHECK_DUAL_SPECIALIZATION)
    assert dual["status"] == CHECK_WARN, "a WARN, never a block: Approve still works"
    assert "Finance and Marketing" in dual["detail"]


@requires_db
def test_one_tick_earns_no_dual_line_and_reads_exactly_as_before(client, applicant, streams):
    post, _ = applicant
    email = f"dual.one.{TAG}@bgscet.ac.in"
    r = post(client, email, usn="1BG26DUO01", specialization_ids=[streams["marketing"]["id"]])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["specialization_id"] == streams["marketing"]["id"]
    assert body["second_specialization_id"] is None and body["second_specialization_name"] is None
    rows = client.get("/api/register/pending", headers=streams["headers"]).json()
    mine = next(x for x in rows if x["id"] == _row(email).id)
    assert not [c for c in mine["checks"] if c["key"] == CHECK_DUAL_SPECIALIZATION], (
        "one specialization is the ordinary case and earns no line"
    )


@requires_db
def test_two_ticks_from_two_courses_are_refused_by_name(client, applicant, streams):
    """A dual specialization is two streams of ONE course; Finance under the
    MBA and Data Science under the MCA is two applications, not one."""
    post, _ = applicant
    r = post(
        client, f"dual.split.{TAG}@bgscet.ac.in", usn="1BG26DUS01",
        specialization_ids=[streams["finance"]["id"], streams["elsewhere"]["id"]],
    )
    assert r.status_code == 422, r.text
    assert "different courses" in r.text
    assert _row(f"dual.split.{TAG}@bgscet.ac.in") is None, "a refused application writes no row"


@requires_db
def test_a_batch_under_one_of_the_two_ticks_is_honoured_and_leads(client, applicant, streams, tracker):
    """The batch pins Marketing; the applicant ticked Finance first, then
    Marketing. Marketing is written FIRST so `specialization_id` — the column
    the batch has always been checked against — still agrees with the batch,
    and Finance is the second. Nothing is lost and nothing is refused."""
    h = streams["headers"]
    batch = client.post(
        f"/api/admin/departments/{streams['department']['id']}/cohorts", headers=h,
        json={"code": _code("MKB"), "name": "2026-28", "batch_label": "2026-28", "degree_level": "PG",
              "entry_date": "2026-08-01", "expected_completion": "2028-07-31",
              "specialization_id": streams["marketing"]["id"]},
    )
    assert batch.status_code == 201, batch.text
    tracker["cohorts"].append(batch.json()["id"])

    post, _ = applicant
    email = f"dual.batch.{TAG}@bgscet.ac.in"
    r = post(
        client, email, usn="1BG26DUB01",
        specialization_ids=[streams["finance"]["id"], streams["marketing"]["id"]],
        requested_cohort_id=batch.json()["id"],
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["specialization_id"] == streams["marketing"]["id"], "the batch's own stream leads"
    assert body["second_specialization_id"] == streams["finance"]["id"], "and the other is kept"
    assert body["requested_cohort_id"] == batch.json()["id"]

    # A batch pinning a stream the applicant did NOT tick is the contradiction
    # it was when the box was a <select>, refused by the same sentence.
    r = post(
        client, f"dual.contra.{TAG}@bgscet.ac.in", usn="1BG26DUC01",
        specialization_ids=[streams["finance"]["id"]], requested_cohort_id=batch.json()["id"],
    )
    assert r.status_code == 422, r.text
    assert "specialization" in r.text.lower() and "batch" in r.text.lower()


@requires_db
def test_a_reviewer_scoped_to_the_second_tick_sees_the_application(
    client, applicant, streams, make_user, scoped_grant
):
    """The row hangs under BOTH streams. A faculty member whose Registrations
    grant reaches only Marketing lists an application that ticked Finance and
    Marketing; one reaching only Data Science does not."""
    post, _ = applicant
    email = f"dual.scope.{TAG}@bgscet.ac.in"
    assert post(
        client, email, usn="1BG26DUX01",
        specialization_ids=[streams["finance"]["id"], streams["marketing"]["id"]],
    ).status_code == 201
    reg_id = _row(email).id

    reviewers = {}
    for key, spec in (("marketing", streams["marketing"]), ("elsewhere", streams["elsewhere"])):
        faculty = make_user(f"dual-{key}-{TAG}", Role.MENTOR)
        scoped_grant(faculty.user_id, "admin.registrations", ScopeLevel.SPECIALIZATION, spec["id"])
        reviewers[key] = faculty

    seen = {row["id"] for row in client.get("/api/register/pending", headers=reviewers["marketing"].headers).json()}
    assert reg_id in seen, "the second tick is a rung the row hangs under"
    unseen = {row["id"] for row in client.get("/api/register/pending", headers=reviewers["elsewhere"].headers).json()}
    assert reg_id not in unseen
