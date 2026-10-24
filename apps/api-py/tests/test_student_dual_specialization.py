"""A student's dual specialization, after approval (2026-09-23).

d4c8e1f7a2b9 let an applicant tick two specializations and stopped at the
application: once approved, the second choice lived on the registration row and
nowhere the Main Admin could see or change it. `students.second_specialization_id`
(migration e2b7c4d9f1a6) carries it. The first of the two is the batch's own and
is read through `cohort_id` as always; the column is the other one.

Pinned here: approval copies the tick the batch does not already say (and the
second tick when there is no batch); the roster editor sets and clears it and the
roster prints it; it refuses the batch's own stream and a stream of another
course; and moving the student into the second stream's batch clears it rather
than leaving "Marketing and Marketing" on the row.

Fixtures borrowed by name, as test_registration_dual_specialization does.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from conftest import requires_db
from test_admin_institution import (  # noqa: F401 — fixtures by name
    _code,
    chain,
    director,
    levels,
    tracker,
)
from test_registration_dual_specialization import streams  # noqa: F401
from test_registration_hierarchy import applicant  # noqa: F401

from app.db import SessionLocal
from app.models.registration import Registration
from app.models.user import Student, User
from app.routers import registration as registration_router

TAG = uuid.uuid4().hex[:6]


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    monkeypatch.setattr(registration_router, "_rate_windows", {})


def _batch(client, streams, tracker, spec_key: str) -> dict:
    r = client.post(
        f"/api/admin/departments/{streams['department']['id']}/cohorts",
        headers=streams["headers"],
        json={"code": _code(spec_key[:3].upper()), "name": "2026-28", "batch_label": "2026-28",
              "degree_level": "PG", "entry_date": "2026-08-01", "expected_completion": "2028-07-31",
              "specialization_id": streams[spec_key]["id"]},
    )
    assert r.status_code == 201, r.text
    tracker["cohorts"].append(r.json()["id"])
    return r.json()


def _approve(client, applicant, streams, email: str, usn: str, **claim) -> Student:
    post, _ = applicant
    r = post(client, email, usn=usn, **claim)
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        reg_id = db.scalar(select(Registration.id).where(Registration.email == email))
    r = client.post(f"/api/register/{reg_id}/decision", headers=streams["headers"], json={"decision": "APPROVE"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        return db.scalar(select(Student).where(Student.user_id == user.id))


def _roster_row(client, streams, student_id: str) -> dict:
    rows = client.get("/api/admin/students", headers=streams["headers"]).json()
    return next(x for x in rows if x["student_id"] == student_id)


# ------------------------------------------------------------ approval --


@requires_db
def test_approval_carries_the_tick_the_batch_does_not_already_say(client, applicant, streams, tracker):
    marketing_batch = _batch(client, streams, tracker, "marketing")
    student = _approve(
        client, applicant, streams, f"dual.seat.{TAG}@bgscet.ac.in", "1BG26DSA01",
        specialization_ids=[streams["finance"]["id"], streams["marketing"]["id"]],
        requested_cohort_id=marketing_batch["id"],
    )
    assert student.cohort_id == marketing_batch["id"]
    assert student.second_specialization_id == streams["finance"]["id"], (
        "the batch is Marketing, so Finance is the second"
    )
    row = _roster_row(client, streams, student.id)
    assert row["second_specialization_id"] == streams["finance"]["id"]
    assert row["second_specialization"] == "Finance"


@requires_db
def test_approval_with_no_batch_keeps_the_second_tick(client, applicant, streams):
    student = _approve(
        client, applicant, streams, f"dual.unseat.{TAG}@bgscet.ac.in", "1BG26DSU01",
        specialization_ids=[streams["finance"]["id"], streams["marketing"]["id"]],
    )
    assert student.cohort_id is None
    assert student.second_specialization_id == streams["marketing"]["id"]


@requires_db
def test_one_tick_leaves_no_second(client, applicant, streams):
    student = _approve(
        client, applicant, streams, f"dual.single.{TAG}@bgscet.ac.in", "1BG26DSS01",
        specialization_ids=[streams["finance"]["id"]],
    )
    assert student.second_specialization_id is None
    assert _roster_row(client, streams, student.id)["second_specialization"] is None


# ------------------------------------------------------- the roster editor --


@requires_db
def test_the_main_admin_assigns_and_clears_a_dual_specialization(client, applicant, streams, tracker):
    finance_batch = _batch(client, streams, tracker, "finance")
    student = _approve(
        client, applicant, streams, f"dual.edit.{TAG}@bgscet.ac.in", "1BG26DSE01",
        specialization_ids=[streams["finance"]["id"]], requested_cohort_id=finance_batch["id"],
    )
    h = streams["headers"]
    url = f"/api/admin/students/{student.id}"

    r = client.patch(url, headers=h, json={"second_specialization_id": streams["marketing"]["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["second_specialization_id"] == streams["marketing"]["id"]
    assert r.json()["second_specialization"] == "Marketing"

    # The batch's own stream is already theirs; a stream of another course is
    # not half of a dual specialization of this one.
    own = client.patch(url, headers=h, json={"second_specialization_id": streams["finance"]["id"]})
    assert own.status_code == 422 and "already" in own.text
    other = client.patch(url, headers=h, json={"second_specialization_id": streams["elsewhere"]["id"]})
    assert other.status_code == 422 and "not a specialization of" in other.text
    missing = client.patch(url, headers=h, json={"second_specialization_id": "no-such-spec"})
    assert missing.status_code == 422

    cleared = client.patch(url, headers=h, json={"second_specialization_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["second_specialization_id"] is None


@requires_db
def test_moving_into_the_second_streams_batch_clears_it(client, applicant, streams, tracker):
    finance_batch = _batch(client, streams, tracker, "finance")
    marketing_batch = _batch(client, streams, tracker, "marketing")
    student = _approve(
        client, applicant, streams, f"dual.move.{TAG}@bgscet.ac.in", "1BG26DSM01",
        specialization_ids=[streams["finance"]["id"], streams["marketing"]["id"]],
        requested_cohort_id=finance_batch["id"],
    )
    assert student.second_specialization_id == streams["marketing"]["id"]
    h = streams["headers"]
    url = f"/api/admin/students/{student.id}"

    # Moved AND told the other stream in one request: both land.
    r = client.patch(
        url, headers=h,
        json={"cohort_id": marketing_batch["id"], "second_specialization_id": streams["finance"]["id"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["second_specialization_id"] == streams["finance"]["id"]

    # Moved back into the Finance batch WITHOUT naming the second: Finance is
    # now the batch's own, so it cannot also be the second.
    r = client.patch(url, headers=h, json={"cohort_id": finance_batch["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["second_specialization_id"] is None, "not 'Finance and Finance'"
