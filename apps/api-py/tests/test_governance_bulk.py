"""Giving and removing access many at a time (2026-10-16).

The owner asked for two things on the Governance screen: give access to many
at a time, and take one access type away from many people at a time. Both are
one request and one transaction here, and these tests hold that down:

1. ONE REQUEST, EVERY PAIR. Several access types for several people is one
   POST, every (access type, person) pair written once, with the one reason on
   each row and one audit row per grant.
2. ALL OR NOTHING. A bad id anywhere in the batch refuses the whole request,
   for the grant and for the removal: a decision half-written is the failure
   the old one-request-per-row console produced.
3. THE APPROVAL RULE IS ASKED PER ACCESS TYPE. A deputy's batch that mixes a
   personal-record key with a plain one writes the first pending and the
   second live, exactly as two separate requests would.
4. A REMOVAL IS A STAMP. Removing one access type from several people leaves
   everybody else's grant standing and keeps every removed row for the trail.
"""

from __future__ import annotations

import uuid

import pytest
from conftest import requires_db
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.governance import capabilities_for
from app.models.governance import APPROVAL_ACTIVE, APPROVAL_PENDING, CapabilityGrant
from app.models.redesign import AuditEvent
from app.models.user import Role
from app.routers.governance import MAX_GRANT_ROWS_PER_REQUEST

GOV = "/api/admin/governance"
REASON = "Placement coordinators for the December recruitment drive."
OFF = "The December recruitment drive is over; the screens go back."


@pytest.fixture
def admin(make_user):
    return make_user(f"blk-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)


@pytest.fixture
def faculty(make_user):
    """Three faculty accounts, none of whom mentors anybody."""
    return [make_user(f"blk-fac{i}-{uuid.uuid4().hex[:4]}", Role.MENTOR) for i in range(3)]


@pytest.fixture
def swept():
    """Grants outlive the accounts they name, so they are cleaned by id."""
    made: list[str] = []
    yield made
    with SessionLocal() as db:
        db.execute(delete(AuditEvent).where(AuditEvent.entity_id.in_(made or [""])))
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made or [""])))
        db.commit()


def _grants_of(user_ids: list[str]) -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(func.count(CapabilityGrant.id)).where(
                CapabilityGrant.subject_user_id.in_(user_ids)
            )
        ) or 0


def _held(user_id: str) -> frozenset[str]:
    with SessionLocal() as db:
        return capabilities_for(db, {"userId": user_id, "role": "MENTOR"})


# ------------------------------------------------------- 1. every pair --


@requires_db
def test_one_request_gives_several_access_types_to_several_people(client, admin, faculty, swept):
    keys = ["admin.analytics", "admin.jobs", "admin.placement"]
    people = [f.user_id for f in faculty[:2]]
    r = client.post(
        f"{GOV}/grants", headers=admin.headers,
        json={"capabilities": keys, "user_ids": people, "reason": REASON},
    )
    assert r.status_code == 201, r.text
    rows = r.json()
    swept.extend(g["id"] for g in rows)

    assert {(g["capability"], g["subject_id"]) for g in rows} == {
        (k, p) for k in keys for p in people
    }
    assert all(g["reason"] == REASON for g in rows), "the one reason is on every row"
    assert all(g["approval_state"] == APPROVAL_ACTIVE for g in rows)
    for person in people:
        assert set(keys) <= _held(person)
    assert not set(keys) & _held(faculty[2].user_id), "the third person was not in the batch"

    with SessionLocal() as db:
        audited = db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.entity_id.in_([g["id"] for g in rows])
            )
        )
    assert audited == len(rows), "one audit row per grant, so each person is named in one"

    # Again, with one access type added: only the new pairs are written.
    again = client.post(
        f"{GOV}/grants", headers=admin.headers,
        json={"capabilities": [*keys, "admin.catalogue"], "user_ids": people, "reason": REASON},
    )
    assert again.status_code == 201, again.text
    swept.extend(g["id"] for g in again.json())
    assert {g["capability"] for g in again.json()} == {"admin.catalogue"}
    assert len(again.json()) == len(people)


@requires_db
def test_the_single_field_still_works_and_is_folded_in(client, admin, faculty, swept):
    """Every client before today sends `capability`; it still grants, and sent
    beside `capabilities` it is one more key, never a duplicate row."""
    person = faculty[0].user_id
    r = client.post(
        f"{GOV}/grants", headers=admin.headers,
        json={
            "capability": "admin.analytics",
            "capabilities": ["admin.analytics", "admin.jobs"],
            "user_ids": [person, person],
            "reason": REASON,
        },
    )
    assert r.status_code == 201, r.text
    swept.extend(g["id"] for g in r.json())
    assert sorted(g["capability"] for g in r.json()) == ["admin.analytics", "admin.jobs"]


@requires_db
def test_a_request_with_no_access_type_or_an_unknown_one_is_refused(client, admin, faculty):
    person = faculty[0].user_id
    for body in (
        {"capabilities": [], "user_ids": [person], "reason": REASON},
        {"capabilities": ["admin.analytics", "admin.nothing"], "user_ids": [person], "reason": REASON},
    ):
        r = client.post(f"{GOV}/grants", headers=admin.headers, json=body)
        assert r.status_code == 422, r.text
    assert _grants_of([person]) == 0


# ------------------------------------------------------- 2. all or nothing --


@requires_db
def test_a_bad_person_in_the_batch_writes_nothing(client, admin, faculty, make_user):
    student = make_user(f"blk-stu-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    good = [f.user_id for f in faculty[:2]]
    r = client.post(
        f"{GOV}/grants", headers=admin.headers,
        json={
            "capabilities": ["admin.analytics", "admin.jobs"],
            "user_ids": [*good, student.user_id],
            "reason": REASON,
        },
    )
    assert r.status_code == 422, r.text
    assert _grants_of(good) == 0, "the two staff before the student were granted anyway"


@requires_db
def test_a_batch_past_the_ceiling_is_refused_before_anything_is_read(client, admin):
    r = client.post(
        f"{GOV}/grants", headers=admin.headers,
        json={
            "capabilities": ["admin.analytics"],
            "user_ids": [uuid.uuid4().hex for _ in range(MAX_GRANT_ROWS_PER_REQUEST + 1)],
            "reason": REASON,
        },
    )
    assert r.status_code == 422, r.text
    assert "smaller batches" in r.text


# ------------------------------------------------------- 3. approval --


@requires_db
def test_a_deputys_mixed_batch_waits_only_where_a_record_is_shown(
    client, admin, faculty, swept
):
    deputy, colleague = faculty[0], faculty[1]
    appointed = client.post(
        f"{GOV}/grants", headers=admin.headers,
        json={
            "capability": "admin.governance", "user_ids": [deputy.user_id],
            "reason": "Deputy for governance while the office is at the accreditation visit.",
        },
    )
    assert appointed.status_code == 201, appointed.text
    swept.extend(g["id"] for g in appointed.json())

    r = client.post(
        f"{GOV}/grants", headers=deputy.headers,
        json={
            "capabilities": ["admin.analytics", "admin.exports"],
            "user_ids": [colleague.user_id],
            "reason": REASON,
        },
    )
    assert r.status_code == 201, r.text
    swept.extend(g["id"] for g in r.json())
    state = {g["capability"]: g["approval_state"] for g in r.json()}
    assert state == {"admin.analytics": APPROVAL_ACTIVE, "admin.exports": APPROVAL_PENDING}
    held = _held(colleague.user_id)
    assert "admin.analytics" in held and "admin.exports" not in held


# ------------------------------------------------------- 4. removal --


@requires_db
def test_one_access_type_is_removed_from_several_people_at_once(client, admin, faculty, swept):
    people = [f.user_id for f in faculty]
    given = client.post(
        f"{GOV}/grants", headers=admin.headers,
        json={"capabilities": ["admin.analytics", "admin.jobs"], "user_ids": people, "reason": REASON},
    )
    assert given.status_code == 201, given.text
    rows = given.json()
    swept.extend(g["id"] for g in rows)

    # Analytics, from the first two. The third keeps it; everybody keeps Jobs.
    chosen = [
        g["id"] for g in rows
        if g["capability"] == "admin.analytics" and g["subject_id"] in people[:2]
    ]
    r = client.post(
        f"{GOV}/grants/revoke", headers=admin.headers,
        json={"grant_ids": chosen, "reason": OFF},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(g["id"] for g in body["revoked"]) == sorted(chosen)
    assert body["already_revoked"] == []

    for person in people[:2]:
        held = _held(person)
        assert "admin.analytics" not in held and "admin.jobs" in held
    assert {"admin.analytics", "admin.jobs"} <= _held(people[2])

    listed = {g["id"] for g in client.get(f"{GOV}/grants", headers=admin.headers).json()}
    assert not listed & set(chosen), "a removed grant is still on the register"
    with SessionLocal() as db:
        kept = db.scalars(select(CapabilityGrant).where(CapabilityGrant.id.in_(chosen))).all()
        assert len(kept) == 2, "a removal stamps the row; it never deletes it"
        assert all(g.revoke_reason == OFF and g.revoked_at is not None for g in kept)

    # The same press again (a second tab): named, not refused, nothing changes.
    twice = client.post(
        f"{GOV}/grants/revoke", headers=admin.headers,
        json={"grant_ids": chosen, "reason": OFF},
    )
    assert twice.status_code == 200, twice.text
    assert twice.json()["revoked"] == []
    assert sorted(twice.json()["already_revoked"]) == sorted(chosen)


@requires_db
def test_an_unknown_grant_in_the_removal_removes_nothing(client, admin, faculty, swept):
    given = client.post(
        f"{GOV}/grants", headers=admin.headers,
        json={"capability": "admin.analytics", "user_ids": [faculty[0].user_id], "reason": REASON},
    )
    swept.extend(g["id"] for g in given.json())
    real = given.json()[0]["id"]

    r = client.post(
        f"{GOV}/grants/revoke", headers=admin.headers,
        json={"grant_ids": [real, uuid.uuid4().hex], "reason": OFF},
    )
    assert r.status_code == 404, r.text
    assert "nothing was removed" in r.text
    assert "admin.analytics" in _held(faculty[0].user_id)


@requires_db
def test_the_removal_asks_for_a_reason_and_is_the_offices(client, admin, faculty, swept, make_user):
    given = client.post(
        f"{GOV}/grants", headers=admin.headers,
        json={"capability": "admin.analytics", "user_ids": [faculty[0].user_id], "reason": REASON},
    )
    swept.extend(g["id"] for g in given.json())
    ids = [g["id"] for g in given.json()]

    short = client.post(f"{GOV}/grants/revoke", headers=admin.headers, json={"grant_ids": ids, "reason": "done"})
    assert short.status_code == 422 and "20 characters" in short.text

    empty = client.post(f"{GOV}/grants/revoke", headers=admin.headers, json={"grant_ids": [], "reason": OFF})
    assert empty.status_code == 422

    for who in (faculty[1], make_user(f"blk-stu-{uuid.uuid4().hex[:4]}", Role.STUDENT)):
        refused = client.post(f"{GOV}/grants/revoke", headers=who.headers, json={"grant_ids": ids, "reason": OFF})
        assert refused.status_code == 403, refused.text
    assert "admin.analytics" in _held(faculty[0].user_id)
