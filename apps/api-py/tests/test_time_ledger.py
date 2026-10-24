"""The Time Allocation Ledger's rules.

The screen's whole promise is that a submitted day is a day that adds to 24
hours. Everything below pins one of the three things that promise rests on:
half-hour granularity, per-slot capacity, and the exact 24-hour submit gate.

The gate tests matter more than they look. `POST /ledger/submit` is the only
place a day becomes a record other people read, and the comparison it makes is
integer half-hours against 48 (models/time_ledger.py explains why it is not
floats). A regression that turned that back into a float comparison would pass
every eyeball test and fail only for the student whose day happened to land on
an unlucky sum — so the "exactly 24 h submits" case is asserted against a day
built out of thirty separate half-hour cells, which is precisely the shape that
drifts.

THE DAY LOCKS (2026-09-22). A day stays open for `settings.ledger_edit_window_days`
calendar days after it and is read-only from then on — no save, no submit, no
"copy yesterday" onto it — and the history endpoint is how the student sees
which days they filled, which are drafts, and which have closed. Every "today"
here is the programme's (`app.clock.local_today`), the same clock the router
reads, so these tests mean the same thing at 01:00 IST as at noon.
"""

from datetime import timedelta

import pytest

from app.clock import local_today
from app.config import settings
from tests.conftest import requires_db

TODAY = local_today()
YESTERDAY = TODAY - timedelta(days=1)
#: The first day that is shut: one past the window.
LOCKED_DAY = TODAY - timedelta(days=settings.ledger_edit_window_days + 1)
#: The oldest day that is still open.
LAST_OPEN_DAY = TODAY - timedelta(days=settings.ledger_edit_window_days)


def _cell(slot: str, activity: str, hours: float) -> dict:
    return {"slot": slot, "activity": activity, "hours": hours}


#: A day that reconciles exactly: every slot filled to its capacity.
FULL_DAY = [
    _cell("DAWN", "COURSEWORK", 4),
    _cell("MORNING", "LECTURES", 3),
    _cell("MIDDAY", "LECTURES", 3),
    _cell("AFTERNOON", "SKILLING", 3),
    _cell("EVENING", "LEISURE", 4),
    _cell("NIGHT", "SLEEPING", 7),
]


@requires_db
def test_empty_day_is_a_200_not_a_404(client, make_user):
    """A day with nothing logged is a real answer. The screen's job then is to
    show six empty slots, not an error state."""
    stu = make_user("ledger-empty")
    r = client.get("/api/student/ledger", headers=stu.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_hours"] == 0
    assert body["unaccounted_hours"] == 24
    assert len(body["slots"]) == 6
    assert body["can_submit"] is False


@requires_db
def test_slots_cover_exactly_twenty_four_hours(client, make_user):
    """The six capacities are the contract the whole screen is drawn from — the
    band's flex weights come from the same numbers."""
    stu = make_user("ledger-slots")
    body = client.get("/api/student/ledger", headers=stu.headers).json()
    assert sum(s["capacity_hours"] for s in body["slots"]) == 24
    assert [s["weight"] for s in body["slots"]] == [8, 6, 6, 6, 8, 14]
    assert body["day_capacity_hours"] == 24


@requires_db
def test_non_student_is_refused(client, make_user):
    from app.models.user import Role

    mentor = make_user("ledger-mentor", role=Role.MENTOR)
    assert client.get("/api/student/ledger", headers=mentor.headers).status_code == 403


@requires_db
def test_save_round_trips_and_computes_metrics(client, make_user):
    stu = make_user("ledger-save")
    r = client.put(
        "/api/student/ledger",
        json={"day": str(TODAY), "cells": FULL_DAY},
        headers=stu.headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_hours"] == 24
    assert body["unaccounted_hours"] == 0
    assert body["can_submit"] is True

    metrics = {m["key"]: m for m in body["metrics"]}
    # 3 + 3 + 3 lectures/skilling + 4 coursework = 13 productive hours.
    assert metrics["productive"]["value"] == "13"
    assert metrics["rest"]["value"] == "7.0"
    # 24 - 7 asleep = 17 awake; 13 / 17 = 76 %.
    assert metrics["utilisation"]["value"] == "76"
    assert metrics["accounted"]["sub"] == "Reconciled to 24 h"


@pytest.mark.parametrize(
    "cells, fragment",
    [
        ([_cell("DAWN", "LECTURES", 1.3)], "nearest half"),
        ([_cell("MORNING", "LECTURES", 4)], "holds 3 h"),
        (
            [_cell("MORNING", "LECTURES", 2), _cell("MORNING", "SKILLING", 2)],
            "against a 3 h capacity",
        ),
        ([_cell("DAWN", "NAPPING", 1)], "Unknown activity"),
        ([_cell("TEATIME", "LECTURES", 1)], "Unknown slot"),
    ],
)
@requires_db
def test_invalid_cells_are_refused_with_a_sentence_naming_the_slot(
    client, make_user, cells, fragment
):
    """Each refusal names the slot and the number, because the client renders the
    detail straight onto the row that caused it."""
    stu = make_user("ledger-invalid")
    r = client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": cells}, headers=stu.headers
    )
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]


@requires_db
def test_a_future_day_cannot_be_logged(client, make_user):
    stu = make_user("ledger-future")
    r = client.put(
        "/api/student/ledger",
        json={"day": str(TODAY + timedelta(days=1)), "cells": []},
        headers=stu.headers,
    )
    assert r.status_code == 422
    assert "has not happened yet" in r.json()["detail"]


@requires_db
def test_submit_is_refused_until_the_day_reconciles(client, make_user):
    """The 409's sentence is the same one the metrics strip shows — one
    expression produces both, so the disabled button and the error agree."""
    stu = make_user("ledger-partial")
    partial = [_cell("NIGHT", "SLEEPING", 7), _cell("DAWN", "COURSEWORK", 4)]
    client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": partial}, headers=stu.headers
    )

    r = client.post("/api/student/ledger/submit", json={"day": str(TODAY)}, headers=stu.headers)
    assert r.status_code == 409, r.text
    assert "13 h to reconcile" in r.json()["detail"]


@requires_db
def test_a_day_built_from_half_hours_still_submits(client, make_user):
    """The float-drift case, asserted deliberately.

    Forty-eight separate half-hour cells is the shape that breaks a float
    comparison against 24.0. Storage is integral half-hours, so this must be an
    exact match — if it ever starts failing, someone has reintroduced floats
    into the total.
    """
    stu = make_user("ledger-halves")
    activities = ["SLEEPING", "LEISURE", "LECTURES", "COURSEWORK", "SKILLING"]
    cells = []
    # Fill every slot to capacity out of 0.5 h pieces spread across the five
    # heads: 8 + 6 + 6 + 6 + 8 + 14 = 48 half hours.
    plan = {"DAWN": 8, "MORNING": 6, "MIDDAY": 6, "AFTERNOON": 6, "EVENING": 8, "NIGHT": 14}
    for slot, halves in plan.items():
        per = {a: 0.0 for a in activities}
        for i in range(halves):
            per[activities[i % len(activities)]] += 0.5
        cells += [_cell(slot, a, h) for a, h in per.items() if h]

    saved = client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": cells}, headers=stu.headers
    ).json()
    assert saved["total_hours"] == 24
    assert saved["can_submit"] is True

    r = client.post("/api/student/ledger/submit", json={"day": str(TODAY)}, headers=stu.headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SUBMITTED"


@requires_db
def test_a_submitted_day_is_frozen(client, make_user):
    stu = make_user("ledger-frozen")
    client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": FULL_DAY}, headers=stu.headers
    )
    assert (
        client.post(
            "/api/student/ledger/submit", json={"day": str(TODAY)}, headers=stu.headers
        ).status_code
        == 200
    )

    r = client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": FULL_DAY}, headers=stu.headers
    )
    assert r.status_code == 409
    assert "no longer be edited" in r.json()["detail"]

    again = client.post(
        "/api/student/ledger/submit", json={"day": str(TODAY)}, headers=stu.headers
    )
    assert again.status_code == 409
    assert "already submitted" in again.json()["detail"]


@requires_db
def test_copy_yesterday_only_copies_a_submitted_day(client, make_user):
    """A half-finished draft must not propagate. The design says "the previous
    day's submitted ledger", and copying a draft would spread yesterday's
    mistake into today with nothing on screen saying where it came from."""
    stu = make_user("ledger-copy")

    # A draft yesterday is not a source.
    client.put(
        "/api/student/ledger",
        json={"day": str(YESTERDAY), "cells": [_cell("NIGHT", "SLEEPING", 7)]},
        headers=stu.headers,
    )
    r = client.post(
        "/api/student/ledger/copy-yesterday", json={"day": str(TODAY)}, headers=stu.headers
    )
    assert r.status_code == 404
    assert "No submitted ledger" in r.json()["detail"]

    # Submit yesterday, and it becomes one.
    client.put(
        "/api/student/ledger", json={"day": str(YESTERDAY), "cells": FULL_DAY}, headers=stu.headers
    )
    client.post("/api/student/ledger/submit", json={"day": str(YESTERDAY)}, headers=stu.headers)

    r = client.post(
        "/api/student/ledger/copy-yesterday", json={"day": str(TODAY)}, headers=stu.headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_hours"] == 24
    # Copied as a DRAFT: prefilling is not submitting on the student's behalf.
    assert body["status"] == "DRAFT"


@requires_db
def test_the_mix_bar_and_the_hatch_are_server_computed(client, make_user):
    """`activity: null` is the sentinel for unaccounted time — the client draws
    the hatch off it rather than off a magic colour string."""
    stu = make_user("ledger-mix")
    body = client.put(
        "/api/student/ledger",
        json={"day": str(TODAY), "cells": [_cell("MORNING", "LECTURES", 1.5)]},
        headers=stu.headers,
    ).json()

    morning = next(s for s in body["slots"] if s["key"] == "MORNING")
    assert morning["logged_hours"] == 1.5
    assert morning["state_label"] == "1.5 h open"
    assert morning["state_tone"] == "warn"
    lectures, hatch = morning["mix"]
    assert lectures["activity"] == "LECTURES" and lectures["percent"] == 50
    assert hatch["activity"] is None and hatch["percent"] == 50

    empty = next(s for s in body["slots"] if s["key"] == "NIGHT")
    assert empty["state_label"] == "Empty"


# --- the lock ---------------------------------------------------------------


@requires_db
def test_the_read_serves_the_programmes_today_and_the_lock_fields(client, make_user):
    """No `day` means the programme's today, and the client anchors its stepper
    on the `today` served back rather than on the handset's clock."""
    stu = make_user("ledger-today")
    body = client.get("/api/student/ledger", headers=stu.headers).json()
    assert body["day"] == str(TODAY)
    assert body["today"] == str(TODAY)
    assert body["editable"] is True
    assert body["locked"] is False
    assert body["lock_reason"] is None
    assert body["edit_window_days"] == settings.ledger_edit_window_days
    assert body["edit_until"] == str(TODAY + timedelta(days=settings.ledger_edit_window_days))


@requires_db
def test_a_day_past_the_edit_window_is_locked_in_every_direction(client, make_user):
    """Save, copy-yesterday and submit all answer 409 with the same sentence the
    read puts on the chip, and the read says the day is not editable."""
    stu = make_user("ledger-locked")

    r = client.put(
        "/api/student/ledger",
        json={"day": str(LOCKED_DAY), "cells": FULL_DAY},
        headers=stu.headers,
    )
    assert r.status_code == 409, r.text
    assert "is locked" in r.json()["detail"]
    assert f"{LOCKED_DAY:%d %b %Y}" in r.json()["detail"]

    r = client.post(
        "/api/student/ledger/copy-yesterday", json={"day": str(LOCKED_DAY)}, headers=stu.headers
    )
    assert r.status_code == 409, r.text
    assert "is locked" in r.json()["detail"]

    r = client.post("/api/student/ledger/submit", json={"day": str(LOCKED_DAY)}, headers=stu.headers)
    assert r.status_code == 409, r.text
    assert "is locked" in r.json()["detail"]

    read = client.get(f"/api/student/ledger?day={LOCKED_DAY}", headers=stu.headers).json()
    assert read["locked"] is True
    assert read["editable"] is False
    assert read["can_submit"] is False
    assert read["lock_reason"] == read["submit_blocked_reason"]
    assert read["total_hours"] == 0, "nothing was written to the locked day"


@requires_db
def test_the_last_open_day_still_saves_and_submits(client, make_user):
    """The boundary is inclusive: with a window of N, today minus N is open and
    today minus N+1 is not. Off by one here is a student refused a day the
    screen told them they had until tonight to fill."""
    stu = make_user("ledger-edge")
    r = client.put(
        "/api/student/ledger",
        json={"day": str(LAST_OPEN_DAY), "cells": FULL_DAY},
        headers=stu.headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["locked"] is False and r.json()["editable"] is True

    r = client.post(
        "/api/student/ledger/submit", json={"day": str(LAST_OPEN_DAY)}, headers=stu.headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SUBMITTED"
    assert r.json()["editable"] is False, "submitted is closed too, for the other reason"


@requires_db
def test_history_lists_every_recent_day_with_its_status(client, make_user):
    """The record of what was filled in: one entry per calendar day whether or
    not a row exists, most recent first, with EMPTY / DRAFT / SUBMITTED and the
    lock on each — so "I filled it in and nothing shows" has a screen to
    answer it."""
    stu = make_user("ledger-history")
    client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": FULL_DAY}, headers=stu.headers
    )
    client.post("/api/student/ledger/submit", json={"day": str(TODAY)}, headers=stu.headers)
    client.put(
        "/api/student/ledger",
        json={"day": str(YESTERDAY), "cells": [_cell("NIGHT", "SLEEPING", 7)]},
        headers=stu.headers,
    )

    r = client.get("/api/student/ledger/history?days=7", headers=stu.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["today"] == str(TODAY)
    assert body["window_days"] == 7
    assert body["edit_window_days"] == settings.ledger_edit_window_days
    assert body["days_submitted"] == 1
    assert body["days_logged"] == 2
    days = body["days"]
    assert [d["day"] for d in days] == [str(TODAY - timedelta(days=i)) for i in range(7)]

    today, yesterday = days[0], days[1]
    assert today["status"] == "SUBMITTED" and today["logged_hours"] == 24
    assert today["editable"] is False and today["submitted_at"] is not None
    assert yesterday["status"] == "DRAFT" and yesterday["logged_hours"] == 7
    assert yesterday["editable"] is True and yesterday["locked"] is False
    assert all(d["status"] == "EMPTY" and d["logged_hours"] == 0 for d in days[2:])
    # The lock is on the strip too: every day past the window reads locked and
    # not editable, every day inside it the opposite.
    for offset, d in enumerate(days):
        expected_locked = offset > settings.ledger_edit_window_days
        assert d["locked"] is expected_locked, (offset, d)
        if d["status"] != "SUBMITTED":
            assert d["editable"] is (not expected_locked), (offset, d)


@requires_db
def test_history_is_first_person_and_bounded(client, make_user):
    a, b = make_user("ledger-hist-a"), make_user("ledger-hist-b")
    client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": FULL_DAY}, headers=a.headers
    )
    assert client.get("/api/student/ledger/history", headers=b.headers).json()["days_logged"] == 0
    assert len(client.get("/api/student/ledger/history?days=500", headers=a.headers).json()["days"]) == 90
    assert len(client.get("/api/student/ledger/history?days=0", headers=a.headers).json()["days"]) == 1


@requires_db
def test_the_weekly_skilling_strip_reads_the_ledger(client, make_user):
    """`GET /student/timesheet` summed a table the ledger never writes, so the
    strip under the ledger read 0 h beside the very cells it should have been
    summing. It reads the ledger's SKILLING cells now."""
    stu = make_user("ledger-weekly")
    before = client.get("/api/student/timesheet?days=7", headers=stu.headers).json()
    assert before["skilling_hours"] == 0

    client.put(
        "/api/student/ledger",
        json={
            "day": str(TODAY),
            "cells": [_cell("AFTERNOON", "SKILLING", 3), _cell("EVENING", "SKILLING", 1.5)],
        },
        headers=stu.headers,
    )
    client.put(
        "/api/student/ledger",
        json={"day": str(YESTERDAY), "cells": [_cell("MORNING", "SKILLING", 2)]},
        headers=stu.headers,
    )
    after = client.get("/api/student/timesheet?days=7", headers=stu.headers).json()
    assert after["skilling_hours"] == 6.5
    assert after["by_activity_minutes"] == {"SKILLING": 390}
    assert {(e["day"], e["minutes"]) for e in after["entries"]} == {
        (str(TODAY), 270),
        (str(YESTERDAY), 120),
    }
    # A one-day window counts today alone.
    assert client.get("/api/student/timesheet?days=1", headers=stu.headers).json()["skilling_hours"] == 4.5


@requires_db
def test_one_students_ledger_is_not_another_students(client, make_user):
    """There is no student id in the URL — the day is resolved from the session
    — so this pins that the session is genuinely what selects the rows."""
    a = make_user("ledger-iso-a")
    b = make_user("ledger-iso-b")
    client.put(
        "/api/student/ledger", json={"day": str(TODAY), "cells": FULL_DAY}, headers=a.headers
    )
    assert client.get("/api/student/ledger", headers=b.headers).json()["total_hours"] == 0
