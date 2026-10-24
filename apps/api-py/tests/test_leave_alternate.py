"""B10.6 — the colleague named in the Alternate Arrangements table.

THE ONE THING THIS MODULE EXISTS TO STOP is a named colleague reading a medical
reason. Accepting an alternate arrangement gives a THIRD PARTY — neither the
applicant nor an approver — a reason to open a leave request, and `LeaveOut`
carries the form's "Purpose" cell, which is free text and routinely medical. The
alternate's two paths answer `LeaveBrief`, which cannot carry one because it has
no field for one; every test below that touches those paths asserts the absence
by SEARCHING THE WHOLE RESPONSE for the applicant's sentence, not by checking a
key name, so a future field called something else fails here too.

The second thing: `accepted_at` is never an input field. `submit_leave` stores
the applicant's `alt_rows` verbatim, so a field on `routers/leave.py::AltRow` is
a field the applicant can WRITE — and an applicant who can stamp their
colleague's acceptance is forging a consent. The keys therefore live in the
stored JSON, written only by these endpoints, and
`test_the_new_keys_are_invisible_to_every_old_reader` proves that costs the
existing readers nothing.
"""

from __future__ import annotations

from datetime import date, timedelta

from conftest import requires_db

from app.models.user import Role

LEAVES = "/api/leaves"
REASON = "Post-operative review at Manipal, second cycle."


def _leave(client, headers, rows: list[dict] | None = None, **extra) -> dict:
    body = {
        "from_date": date.today().isoformat(),
        "to_date": (date.today() + timedelta(days=2)).isoformat(),
        "reason": REASON,
        "leave_kind": "CASUAL",
        "alt_name": "Kavya N",
        "alt_rows": rows
        if rows is not None
        else [
            {
                "date": date.today().isoformat(),
                "staff_name": "Kavya N",
                "cls": "MBA-II",
                "time": "10:00",
                "remarks": "Swapped",
            },
            {
                "date": (date.today() + timedelta(days=1)).isoformat(),
                "staff_name": "Ravi K",
                "cls": "MBA-I",
                "time": "14:00",
                "remarks": "",
            },
        ],
    }
    body.update(extra)
    r = client.post(LEAVES, headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _assign(client, headers, leave_id: str, index: int, user_id: str | None):
    return client.post(
        f"{LEAVES}/{leave_id}/alternate/assign",
        headers=headers,
        json={"row_index": index, "user_id": user_id},
    )


# ------------------------------------------------------------- the fence --


@requires_db
def test_the_alternate_reads_the_dates_and_never_the_reason(client, make_user):
    applicant = make_user("alt-app", Role.MENTOR)
    covering = make_user("alt-cov", Role.MENTOR)  # no group: an ordinary colleague

    leave = _leave(client, applicant.headers)
    assert _assign(client, applicant.headers, leave["id"], 0, covering.user_id).status_code == 200

    mine = client.get(f"{LEAVES}/alternate/mine", headers=covering.headers)
    assert mine.status_code == 200, mine.text
    rows = mine.json()
    assert [r["id"] for r in rows] == [leave["id"]]
    brief = rows[0]
    assert set(brief) == {
        "id", "from_date", "to_date", "leave_kind", "status", "requester_name", "alt_row",
    }
    assert brief["alt_row"]["cls"] == "MBA-II", "the row addressed to THEM, not the table"
    assert REASON not in mine.text

    accepted = client.post(f"{LEAVES}/{leave['id']}/alternate/accept", headers=covering.headers)
    assert accepted.status_code == 200, accepted.text
    assert REASON not in accepted.text
    assert set(accepted.json()) == set(brief)

    # The colleague still cannot open the request itself: being named is an
    # entitlement to the dates and the class, and to nothing else.
    assert client.get(f"{LEAVES}/{leave['id']}/paper.pdf", headers=covering.headers).status_code == 404


@requires_db
def test_only_the_named_colleague_accepts_and_everybody_else_gets_one_404(client, make_user):
    applicant = make_user("alt2-app", Role.MENTOR)
    covering = make_user("alt2-cov", Role.MENTOR)
    stranger = make_user("alt2-str", Role.MENTOR)
    student = make_user("alt2-stu")

    leave = _leave(client, applicant.headers)
    _assign(client, applicant.headers, leave["id"], 0, covering.user_id)
    url = f"{LEAVES}/{leave['id']}/alternate/accept"

    # The applicant cannot accept on their colleague's behalf — an acceptance
    # the applicant can write is worth nothing.
    mine_refusal = client.post(url, headers=applicant.headers)
    assert mine_refusal.status_code == 404
    assert mine_refusal.json()["detail"] == "Leave request not found."

    for who in (stranger, student):
        r = client.post(url, headers=who.headers)
        assert r.status_code == 404 and r.json() == mine_refusal.json()
    # Byte-identical to an id that never existed: this endpoint is reachable by
    # every signed-in account, so a distinguishable refusal is an oracle anybody
    # can query.
    invented = client.post(f"{LEAVES}/no-such-leave/alternate/accept", headers=student.headers)
    assert invented.status_code == 404 and invented.json() == mine_refusal.json()

    assert client.get(f"{LEAVES}/alternate/mine", headers=stranger.headers).json() == []


@requires_db
def test_the_whole_table_is_the_applicants_and_the_approvers(client, make_user):
    applicant = make_user("alt3-app", Role.MENTOR)
    office = make_user("alt3-adm", Role.ADMIN)
    covering = make_user("alt3-cov", Role.MENTOR)
    student = make_user("alt3-stu")

    leave = _leave(client, applicant.headers)
    _assign(client, applicant.headers, leave["id"], 0, covering.user_id)
    url = f"{LEAVES}/{leave['id']}/alternate"

    table = client.get(url, headers=applicant.headers)
    assert table.status_code == 200, table.text
    rows = table.json()["rows"]
    assert [r["index"] for r in rows] == [0, 1]
    assert rows[0]["user_id"] == covering.user_id and rows[0]["user_name"]
    assert rows[0]["staff_name"] == "Kavya N", "the typed name the paper prints is untouched"
    assert rows[1]["user_id"] is None, "a row nobody has linked is a real state"
    assert REASON not in table.text

    assert client.get(url, headers=office.headers).status_code == 200
    # The COLLEAGUE does not read the table: who else was asked is none of their
    # business. They are not a decider, so they get the flattened 404.
    assert client.get(url, headers=covering.headers).status_code == 404
    assert client.get(url, headers=student.headers).status_code == 403, "role gate first"


# ----------------------------------------------------------- the writing --


@requires_db
def test_assign_refuses_a_student_and_the_applicant_themselves(client, make_user):
    applicant = make_user("alt4-app", Role.MENTOR)
    student = make_user("alt4-stu")
    leave = _leave(client, applicant.headers)

    r = _assign(client, applicant.headers, leave["id"], 0, student.user_id)
    assert r.status_code == 422 and "member of staff" in r.text

    r = _assign(client, applicant.headers, leave["id"], 0, applicant.user_id)
    assert r.status_code == 422 and "your own alternate" in r.text

    # 422 and not 404: the leave exists and the caller owns it; the VALUE is wrong.
    r = _assign(client, applicant.headers, leave["id"], 9, None)
    assert r.status_code == 404 and "alternate arrangement table" in r.text

    # Somebody else's request is the same 404 as a missing one.
    other = make_user("alt4-oth", Role.MENTOR)
    r = _assign(client, other.headers, leave["id"], 0, other.user_id)
    assert r.status_code == 404 and r.json()["detail"] == "Leave request not found."


@requires_db
def test_accepting_is_idempotent_and_reassigning_clears_it(client, make_user):
    applicant = make_user("alt5-app", Role.MENTOR)
    first = make_user("alt5-one", Role.MENTOR)
    second = make_user("alt5-two", Role.MENTOR)
    leave = _leave(client, applicant.headers)
    _assign(client, applicant.headers, leave["id"], 0, first.user_id)

    url = f"{LEAVES}/{leave['id']}/alternate/accept"
    assert client.post(url, headers=first.headers).status_code == 200
    table = client.get(f"{LEAVES}/{leave['id']}/alternate", headers=applicant.headers).json()
    stamp = table["rows"][0]["accepted_at"]
    assert stamp

    # A second POST does not re-stamp: an acceptance is a fact, not an event
    # stream, and a double-tap must not move it.
    assert client.post(url, headers=first.headers).status_code == 200
    again = client.get(f"{LEAVES}/{leave['id']}/alternate", headers=applicant.headers).json()
    assert again["rows"][0]["accepted_at"] == stamp

    # Re-assigning the row clears the acceptance: the agreement was to cover a
    # specific class, and moving the row underneath it would carry somebody's
    # "yes" onto something they never saw.
    assert _assign(client, applicant.headers, leave["id"], 0, second.user_id).status_code == 200
    moved = client.get(f"{LEAVES}/{leave['id']}/alternate", headers=applicant.headers).json()
    assert moved["rows"][0]["user_id"] == second.user_id
    assert moved["rows"][0]["accepted_at"] is None
    assert client.get(f"{LEAVES}/alternate/mine", headers=first.headers).json() == []

    # Clearing the link entirely leaves the printed name alone.
    assert _assign(client, applicant.headers, leave["id"], 0, None).status_code == 200
    cleared = client.get(f"{LEAVES}/{leave['id']}/alternate", headers=applicant.headers).json()
    assert cleared["rows"][0]["user_id"] is None
    assert cleared["rows"][0]["staff_name"] == "Kavya N"


@requires_db
def test_a_withdrawn_request_needs_no_cover(client, make_user):
    applicant = make_user("alt6-app", Role.MENTOR)
    covering = make_user("alt6-cov", Role.MENTOR)
    leave = _leave(client, applicant.headers)
    _assign(client, applicant.headers, leave["id"], 0, covering.user_id)

    assert client.post(f"{LEAVES}/{leave['id']}/cancel", headers=applicant.headers).status_code == 200

    r = client.post(f"{LEAVES}/{leave['id']}/alternate/accept", headers=covering.headers)
    assert r.status_code == 409 and "nothing to cover" in r.text
    # The colleague STILL sees it, because "you no longer need to cover this" is
    # the thing they most need to know. The brief carries the status.
    mine = client.get(f"{LEAVES}/alternate/mine", headers=covering.headers).json()
    assert [r["status"] for r in mine] == ["CANCELLED"]

    # And the applicant can no longer rearrange a settled request.
    assert _assign(client, applicant.headers, leave["id"], 0, None).status_code == 409


# --------------------------------------------- compatibility with B10.3-- --


@requires_db
def test_the_new_keys_are_invisible_to_every_old_reader(client, make_user):
    """`_leave_out` does `AltRow(**r)` over STORED rows and `leave_paper._alt_cells`
    reads five keys through `getattr`. Both were written before `user_id` and
    `accepted_at` existed, and neither may notice them — which is what lets the
    college's own form need no change at all."""
    applicant = make_user("alt7-app", Role.MENTOR)
    covering = make_user("alt7-cov", Role.MENTOR)
    leave = _leave(client, applicant.headers)
    _assign(client, applicant.headers, leave["id"], 0, covering.user_id)
    client.post(f"{LEAVES}/{leave['id']}/alternate/accept", headers=covering.headers)

    mine = client.get(f"{LEAVES}/mine", headers=applicant.headers)
    assert mine.status_code == 200, mine.text
    row = next(x for x in mine.json() if x["id"] == leave["id"])
    assert set(row["alt_rows"][0]) == {"date", "staff_name", "cls", "time", "remarks"}, (
        "AltRow ignores the two stored keys; adding either to that model would "
        "let an applicant forge an acceptance at submit time"
    )
    assert row["alt_rows"][0]["staff_name"] == "Kavya N"

    paper = client.get(f"{LEAVES}/{leave['id']}/paper.pdf", headers=applicant.headers)
    assert paper.status_code == 200 and paper.content.startswith(b"%PDF")


@requires_db
def test_an_applicant_cannot_submit_a_forged_acceptance(client, make_user):
    """THE CLIENT CAN NOW SEND THESE KEYS, AND THE SERVER MUST STILL DROP THEM.

    `leave.component.ts`'s "Edit & resubmit" copies a rejected request's stored
    `alt_rows` into a fresh form with `{...r}` — and since B10.6 those stored
    rows carry `user_id` and `accepted_at`. So the path that posts a colleague's
    acceptance back to `POST /api/leaves` is reachable from the product, not
    only from curl. It must land as nothing.

    What makes it safe is that `routers/leave.py::AltRow` declares five fields
    and pydantic v2 ignores the rest, so `[r.model_dump() for r in body.alt_rows]`
    can only ever write those five. Adding either key to that model — which is
    exactly what 04 §B10.6 asks for in as many words — would make an applicant
    able to stamp their colleague's agreement onto their own form, with nothing
    in the record to show they did.
    """
    applicant = make_user("alt8-app", Role.MENTOR)
    covering = make_user("alt8-cov", Role.MENTOR)
    leave = _leave(
        client,
        applicant.headers,
        rows=[
            {
                "date": date.today().isoformat(),
                "staff_name": "Kavya N",
                "cls": "MBA-II",
                "time": "10:00",
                "remarks": "Swapped",
                # Both forged, exactly as a re-submitted form would carry them.
                "user_id": covering.user_id,
                "accepted_at": "2020-01-01T00:00:00+00:00",
            }
        ],
    )
    table = client.get(f"{LEAVES}/{leave['id']}/alternate", headers=applicant.headers)
    assert table.status_code == 200, table.text
    row = table.json()["rows"][0]
    assert row["user_id"] is None, "the applicant cannot name an account at submit time"
    assert row["accepted_at"] is None, "and certainly cannot stamp the agreement"
    assert row["staff_name"] == "Kavya N", "the typed name the paper prints is kept"

    # And the colleague they tried to name is not on the hook for it.
    mine = client.get(f"{LEAVES}/alternate/mine", headers=covering.headers)
    assert mine.status_code == 200, mine.text
    assert [b["id"] for b in mine.json() if b["id"] == leave["id"]] == []
