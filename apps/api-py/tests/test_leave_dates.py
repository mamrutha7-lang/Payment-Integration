"""A leave request's dates must run forwards.

Found in the browser, not by a test: the compose form enabled "Sign & submit"
for 20 Dec -> 10 Dec, the API accepted it, the row was stored with a span of
MINUS TEN days, it appeared in the approver's queue with a live "Mark
Sanctioned & sign" button, and the college's own PDF rendered that span. Two
independently declared `date` fields with nothing relating them.

The refusal is on `LeaveIn` rather than in the endpoint or the client, because
the owner's standing instruction is that the leave form and its buttons do not
change — so the request must die before it is ever built.

`from == to` stays legal and is pinned below: a PERMISSION slip and a one-day
CASUAL leave are both written that way on the paper form, so a validator that
demanded `to > from` would break the two most common requests in the office.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from conftest import requires_db

from app.models.user import Role

LEAVES = "/api/leaves"


def _body(from_date: date, to_date: date) -> dict:
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "reason": "Family function at home.",
        "leave_kind": "CASUAL",
    }


@requires_db
def test_a_leave_that_ends_before_it_starts_is_refused(client, make_user):
    staff = make_user("leave-dates", Role.MENTOR)
    start = date(2026, 12, 20)

    r = client.post(LEAVES, headers=staff.headers, json=_body(start, date(2026, 12, 10)))
    assert r.status_code == 422, r.text
    assert "cannot fall before" in r.text, r.text
    # The message names both dates, so the applicant can see which way round it read them.
    assert "2026-12-20" in r.text and "2026-12-10" in r.text, r.text


@requires_db
def test_one_day_and_multi_day_leave_both_remain_legal(client, make_user):
    """The regression this validator must not become: PERMISSION and a one-day
    CASUAL leave are both `from == to` on the paper form."""
    staff = make_user("leave-dates-ok", Role.MENTOR)
    today = date.today()

    same_day = client.post(LEAVES, headers=staff.headers, json=_body(today, today))
    assert same_day.status_code == 201, same_day.text
    assert same_day.json()["from_date"] == same_day.json()["to_date"]

    spanning = client.post(LEAVES, headers=staff.headers, json=_body(today, today + timedelta(days=3)))
    assert spanning.status_code == 201, spanning.text


@pytest.mark.parametrize("delta", [-1, -365])
@requires_db
def test_any_backwards_span_is_refused_not_just_the_one_that_was_reported(client, make_user, delta):
    staff = make_user(f"leave-dates-{abs(delta)}", Role.MENTOR)
    today = date.today()
    r = client.post(LEAVES, headers=staff.headers, json=_body(today, today + timedelta(days=delta)))
    assert r.status_code == 422, r.text
