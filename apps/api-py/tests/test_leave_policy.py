"""B10.2's endpoints — allowances and the college calendar.

What this module holds down:

1. A MISSING ROW IS NOT A ZERO BALANCE, all the way out to the payload. Nothing
   seeds `leave_balances`; a deployment that has never opened the policy screen
   answers an EMPTY LIST, and the screen must say "no allowance recorded" rather
   than "0 days left". `app/leave_policy.py` makes the submit path silent for the
   same reason and `tests/test_leave_chain.py` pins that half.
2. THE WRITES ARE THE MAIN ADMIN'S. No new capability key was minted for this
   (see the router's docstring), so the only thing standing between a granted
   lecturer and the whole roster's allowances is `require_admin` — asserted on
   every write and on the list.
3. A BALANCE READ ABOUT SOMEBODY ELSE IS RULE 2, and the only approver-facing
   door hangs off a LEAVE REQUEST so that `_assert_can_decide` can be the gate.
   A general "any person's balance" endpoint would need either a fourth copy of
   rule 2 or a `require_capability(..., target=...)` that SHORT-CIRCUITS for
   exactly the mentors it is meant to fence.
4. THE BULK WRITE CREATES AND NEVER OVERWRITES. The row carries `consumed_days`,
   and a December bulk that reset it would hand back days people had already
   taken, silently, to a whole department at once.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update

from conftest import requires_db

from app.db import SessionLocal
from app.leave_policy import academic_year_for
from app.models.cohort import Cohort
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.leave_policy import AcademicCalendarDay, LeaveBalance
from app.models.user import Role, Student, User

LEAVES = "/api/leaves"
ADMIN = "/api/admin"
YEAR = academic_year_for(date.today())


@pytest.fixture
def campus(make_user):
    """A college with one department, and the accounts filed under it.

    DEPENDS ON `make_user` so pytest tears THAT down last: the faculty and
    student rows this fixture files under the department are deleted by that
    fixture, and a department cannot go while anything still points at it. The
    pointers are nulled here first, which makes the order irrelevant either way.
    """
    tag = uuid.uuid4().hex[:6]
    staff = make_user(f"pol-staff-{tag}", Role.MENTOR)
    student = make_user(f"pol-stu-{tag}")
    office = make_user(f"pol-adm-{tag}", Role.ADMIN)
    outsider = make_user(f"pol-out-{tag}", Role.MENTOR)

    with SessionLocal() as db:
        college = College(code=f"PC{tag.upper()}", name="Policy College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        department = Department(college_id=college.id, name="Policy Dept", code=f"PD{tag}")
        db.add(department)
        db.flush()
        cohort = Cohort(
            code=f"PB-{tag}",
            name="Policy Batch",
            batch_label="2026-28",
            degree_level=DegreeLevel.PG,
            department_id=department.id,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        db.add(cohort)
        db.flush()
        # The faculty account is filed by `users.department_id`; the student is
        # filed through their BATCH, which is the pointer a query that read only
        # `students.department_id` would miss.
        db.execute(
            update(User).where(User.id == staff.user_id).values(department_id=department.id)
        )
        db.execute(
            update(Student).where(Student.user_id == student.user_id).values(cohort_id=cohort.id)
        )
        ids = {
            "college_id": college.id,
            "department_id": department.id,
            "cohort_id": cohort.id,
        }
        db.commit()

    yield {
        **ids,
        "staff": staff,
        "student": student,
        "office": office,
        "outsider": outsider,
        "tag": tag,
    }

    with SessionLocal() as db:
        db.execute(
            delete(LeaveBalance).where(
                LeaveBalance.user_id.in_(
                    [staff.user_id, student.user_id, office.user_id, outsider.user_id]
                )
            )
        )
        db.execute(
            delete(AcademicCalendarDay).where(AcademicCalendarDay.college_id == ids["college_id"])
        )
        db.execute(update(User).where(User.department_id == ids["department_id"]).values(department_id=None))
        db.execute(update(Student).where(Student.cohort_id == ids["cohort_id"]).values(cohort_id=None))
        db.execute(
            update(Student)
            .where(Student.department_id == ids["department_id"])
            .values(department_id=None)
        )
        db.flush()
        db.execute(delete(Cohort).where(Cohort.id == ids["cohort_id"]))
        db.execute(delete(Department).where(Department.id == ids["department_id"]))
        db.execute(delete(College).where(College.id == ids["college_id"]))
        db.commit()


# --------------------------------------------------------------- balances --


@requires_db
def test_no_row_is_an_empty_list_and_never_a_zero(client, campus):
    """The state of every deployment that has not opened the policy screen."""
    r = client.get(f"{LEAVES}/balances", headers=campus["staff"].headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["balances"] == [], "absence, not a row of zeros"
    assert body["academic_year"] == YEAR
    assert body["user_id"] == campus["staff"].user_id


@requires_db
def test_only_the_main_admin_records_an_allowance(client, campus):
    payload = {
        "user_id": campus["staff"].user_id,
        "kind": "CASUAL",
        "academic_year": YEAR,
        "entitled_days": 12,
    }
    for who in ("staff", "student", "outsider"):
        r = client.put(f"{ADMIN}/leave-balances", headers=campus[who].headers, json=payload)
        assert r.status_code == 403, f"{who} wrote an allowance: {r.text}"

    r = client.put(f"{ADMIN}/leave-balances", headers=campus["office"].headers, json=payload)
    assert r.status_code == 200, r.text
    row = r.json()
    assert (row["entitled_days"], row["consumed_days"], row["remaining_days"]) == (12, 0, 12)
    assert row["user_name"] and row["user_role"] == "MENTOR"

    # The person reads their own without any capability at all.
    mine = client.get(f"{LEAVES}/balances", headers=campus["staff"].headers).json()
    assert [(b["kind"], b["entitled_days"]) for b in mine["balances"]] == [("CASUAL", 12)]

    # An omitted `consumed_days` KEEPS what is recorded rather than resetting it.
    client.put(
        f"{ADMIN}/leave-balances",
        headers=campus["office"].headers,
        json={**payload, "consumed_days": 5},
    )
    kept = client.put(
        f"{ADMIN}/leave-balances",
        headers=campus["office"].headers,
        json={**payload, "entitled_days": 15},
    ).json()
    assert (kept["entitled_days"], kept["consumed_days"], kept["remaining_days"]) == (15, 5, 10)

    # Leave past an allowance happens and the office signs it: the remainder is
    # allowed to go negative rather than being clamped out of sight.
    over = client.put(
        f"{ADMIN}/leave-balances",
        headers=campus["office"].headers,
        json={**payload, "entitled_days": 3, "consumed_days": 5},
    ).json()
    assert over["remaining_days"] == -2

    assert (
        client.delete(f"{ADMIN}/leave-balances/{row['id']}", headers=campus["office"].headers).status_code
        == 204
    )
    assert client.get(f"{LEAVES}/balances", headers=campus["staff"].headers).json()["balances"] == []


@requires_db
def test_a_kind_the_paper_does_not_print_is_refused(client, campus):
    r = client.put(
        f"{ADMIN}/leave-balances",
        headers=campus["office"].headers,
        json={
            "user_id": campus["staff"].user_id,
            "kind": "SABBATICAL",
            "academic_year": YEAR,
            "entitled_days": 90,
        },
    )
    assert r.status_code == 422, "the five options are fixed by the college's own form"


@requires_db
def test_the_bulk_write_covers_a_department_and_never_overwrites(client, campus):
    body = {
        "department_id": campus["department_id"],
        "kind": "CASUAL",
        "academic_year": YEAR,
        "entitled_days": 10,
    }
    assert (
        client.post(f"{ADMIN}/leave-balances/bulk", headers=campus["staff"].headers, json=body).status_code
        == 403
    )

    first = client.post(f"{ADMIN}/leave-balances/bulk", headers=campus["office"].headers, json=body)
    assert first.status_code == 201, first.text
    assert first.json()["created"] == 2, "the faculty member AND the student seated in the batch"
    assert first.json()["skipped"] == 0

    # The student is reached THROUGH THEIR BATCH. A query that read only
    # `students.department_id` would have missed them, which is the bug
    # `ancestry_of_student` had against feature overrides.
    seen = client.get(f"{LEAVES}/balances", headers=campus["student"].headers).json()
    assert [(b["kind"], b["entitled_days"]) for b in seen["balances"]] == [("CASUAL", 10)]

    # Somebody takes four days, and the office runs the bulk write again.
    with SessionLocal() as db:
        db.execute(
            update(LeaveBalance)
            .where(LeaveBalance.user_id == campus["student"].user_id)
            .values(consumed_days=4)
        )
        db.commit()
    second = client.post(f"{ADMIN}/leave-balances/bulk", headers=campus["office"].headers, json=body)
    assert second.status_code == 201
    assert (second.json()["created"], second.json()["skipped"]) == (0, 2)
    after = client.get(f"{LEAVES}/balances", headers=campus["student"].headers).json()
    assert after["balances"][0]["consumed_days"] == 4, "days already taken were not handed back"

    # Staff-only is the common case and is expressible.
    staff_only = client.post(
        f"{ADMIN}/leave-balances/bulk",
        headers=campus["office"].headers,
        json={**body, "kind": "OOD", "include_students": False},
    )
    assert staff_only.json()["created"] == 1
    assert client.get(f"{LEAVES}/balances", headers=campus["student"].headers).json()["balances"][0][
        "kind"
    ] == "CASUAL"

    listing = client.get(
        f"{ADMIN}/leave-balances",
        headers=campus["office"].headers,
        params={"academic_year": YEAR, "department_id": campus["department_id"]},
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 3
    assert all(b["academic_year"] == YEAR for b in listing.json())


@requires_db
def test_the_approver_reads_the_balance_behind_a_request_and_nobody_else_does(client, campus):
    applicant, office, outsider = campus["staff"], campus["office"], campus["outsider"]
    client.put(
        f"{ADMIN}/leave-balances",
        headers=office.headers,
        json={
            "user_id": applicant.user_id,
            "kind": "CASUAL",
            "academic_year": YEAR,
            "entitled_days": 12,
            "consumed_days": 2,
        },
    )
    leave = client.post(
        LEAVES,
        headers=applicant.headers,
        json={
            "from_date": date.today().isoformat(),
            "to_date": (date.today() + timedelta(days=1)).isoformat(),
            "reason": "Post-operative review.",
            "leave_kind": "CASUAL",
        },
    )
    assert leave.status_code == 201, leave.text
    url = f"{LEAVES}/{leave.json()['id']}/balance"

    # The applicant, and the staff who could decide it.
    assert client.get(url, headers=applicant.headers).json()["balances"][0]["remaining_days"] == 10
    seen = client.get(url, headers=office.headers)
    assert seen.status_code == 200
    assert seen.json()["user_id"] == applicant.user_id

    # A faculty account with no group and no grant: the same flattened 404 the
    # decision path and the paper give.
    refused = client.get(url, headers=outsider.headers)
    assert refused.status_code == 404
    assert refused.json()["detail"] == "Leave request not found."
    invented = client.get(f"{LEAVES}/no-such-leave/balance", headers=outsider.headers)
    assert invented.status_code == 404 and invented.json() == refused.json()
    assert client.get(url, headers=campus["student"].headers).status_code == 403


# --------------------------------------------------------------- calendar --


@requires_db
def test_the_calendar_is_the_offices_to_write_and_the_callers_own_to_read(client, campus):
    day = date(2026, 8, 15)
    body = {"day": day.isoformat(), "kind": "holiday", "label": "Independence Day"}

    assert (
        client.put(
            f"{ADMIN}/leave-calendar/{campus['college_id']}",
            headers=campus["staff"].headers,
            json=body,
        ).status_code
        == 403
    )
    written = client.put(
        f"{ADMIN}/leave-calendar/{campus['college_id']}", headers=campus["office"].headers, json=body
    )
    assert written.status_code == 200, written.text
    assert written.json()["kind"] == "holiday" and written.json()["label"] == "Independence Day"
    assert written.json()["created_by_user_id"] == campus["office"].user_id

    # One verdict per college per day: the same date comes back as an edit.
    again = client.put(
        f"{ADMIN}/leave-calendar/{campus['college_id']}",
        headers=campus["office"].headers,
        json={**body, "kind": "working", "label": "Compensatory class"},
    )
    assert again.json()["id"] == written.json()["id"] and again.json()["kind"] == "working"

    # The caller reads their OWN college's calendar, with no id to walk. The
    # faculty member is filed under this college; the outsider is filed nowhere
    # and gets an empty list, which is exactly the calendar the submit path
    # applies to them.
    mine = client.get(f"{LEAVES}/calendar", headers=campus["staff"].headers)
    assert mine.status_code == 200 and [d["day"] for d in mine.json()] == [day.isoformat()]
    assert client.get(f"{LEAVES}/calendar", headers=campus["outsider"].headers).json() == []
    # And the student reaches it through their batch, like their balance does.
    assert [d["day"] for d in client.get(f"{LEAVES}/calendar", headers=campus["student"].headers).json()] == [
        day.isoformat()
    ]

    ranged = client.get(
        f"{LEAVES}/calendar",
        headers=campus["staff"].headers,
        params={"from": "2026-09-01", "to": "2026-09-30"},
    )
    assert ranged.json() == []

    listed = client.get(
        f"{ADMIN}/leave-calendar/{campus['college_id']}", headers=campus["office"].headers
    )
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert (
        client.delete(
            f"{ADMIN}/leave-calendar/{campus['college_id']}/{written.json()['id']}",
            headers=campus["office"].headers,
        ).status_code
        == 204
    )
    assert client.get(f"{LEAVES}/calendar", headers=campus["staff"].headers).json() == []


@requires_db
def test_a_holiday_is_not_counted_against_an_allowance(client, campus):
    """The calendar and the balance meeting on the submit path — the one place
    both tables are read at once. A three-day span with one holiday in it is two
    working days, so an allowance of two accepts it and an allowance of one does
    not."""
    start = date.today() + timedelta(days=30)
    client.put(
        f"{ADMIN}/leave-calendar/{campus['college_id']}",
        headers=campus["office"].headers,
        json={"day": (start + timedelta(days=1)).isoformat(), "kind": "holiday", "label": "Shut"},
    )
    client.put(
        f"{ADMIN}/leave-balances",
        headers=campus["office"].headers,
        json={
            "user_id": campus["staff"].user_id,
            "kind": "CASUAL",
            "academic_year": academic_year_for(start),
            "entitled_days": 2,
        },
    )
    body = {
        "from_date": start.isoformat(),
        "to_date": (start + timedelta(days=2)).isoformat(),
        "reason": "Family function.",
        "leave_kind": "CASUAL",
    }
    ok = client.post(LEAVES, headers=campus["staff"].headers, json=body)
    assert ok.status_code == 201, ok.text

    with SessionLocal() as db:
        db.execute(
            update(LeaveBalance)
            .where(LeaveBalance.user_id == campus["staff"].user_id)
            .values(entitled_days=1)
        )
        db.commit()
    # Now the same shape is refused — and the refusal names the balance, not LOP
    # silently substituted for what the applicant chose.
    refused = client.post(
        LEAVES,
        headers=campus["staff"].headers,
        json={
            **body,
            "from_date": (start + timedelta(days=10)).isoformat(),
            "to_date": (start + timedelta(days=12)).isoformat(),
        },
    )
    assert refused.status_code == 422 and "CASUAL balance" in refused.text


# ------------------------------------------------------------- the sheet --


@requires_db
def test_the_sheet_spells_the_year_the_submit_path_looks_up(client, campus):
    sheet = client.get(f"{ADMIN}/leave-policy", headers=campus["office"].headers)
    assert sheet.status_code == 200, sheet.text
    body = sheet.json()
    assert body["academic_year"] == YEAR, (
        "the console must pre-fill from this, or the office types '2026-2027', "
        "the submit-path lookup misses and the check silently never fires"
    )
    assert body["kinds"] == ["CASUAL", "PERMISSION", "OOD", "RH", "LOP"]
    ours = next(c for c in body["colleges"] if c["college_id"] == campus["college_id"])
    assert (ours["holidays"], ours["working_days"]) == (0, 0)

    client.put(
        f"{ADMIN}/leave-calendar/{campus['college_id']}",
        headers=campus["office"].headers,
        json={"day": "2027-01-26", "kind": "holiday", "label": "Republic Day"},
    )
    client.put(
        f"{ADMIN}/leave-balances",
        headers=campus["office"].headers,
        json={
            "user_id": campus["staff"].user_id,
            "kind": "RH",
            "academic_year": YEAR,
            "entitled_days": 2,
        },
    )
    again = client.get(f"{ADMIN}/leave-policy", headers=campus["office"].headers).json()
    ours = next(c for c in again["colleges"] if c["college_id"] == campus["college_id"])
    assert (ours["holidays"], ours["working_days"]) == (1, 0)
    assert again["balances_recorded"] >= 1 and again["people_with_balances"] >= 1

    assert client.get(f"{ADMIN}/leave-policy", headers=campus["staff"].headers).status_code == 403
