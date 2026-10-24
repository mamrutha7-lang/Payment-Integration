"""B11.2 — HOLD, and the by-status queue the three dead tabs needed.

WHAT THIS MODULE IS FOR. A status is cheap to add and expensive to add
HALF: five places in this codebase branch on `registrations.status`, and a new
member that reaches four of them leaves the fifth quietly wrong — an application
that cannot be decided, a queue tab that is always empty, a KPI that falls when
somebody presses Hold, an applicant who cannot send the document they were held
for. Each of those is one test below, named after the failure rather than the
function.

The other half is the queue. `GET /register/pending` has answered one hard-coded
status since it was written, and the console is built against that array, so the
first assertion in the paging section is that the DEFAULT ANSWER DID NOT MOVE.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, College
from app.models.job import DegreeLevel
from app.models.registration import (
    PENDING_QUEUE_STATUSES,
    Registration,
    RegistrationStatus,
)
from app.models.student_profile import StudentProfile
from app.models.user import Role, Student, User
from app.routers.registration import ALREADY_DECIDED_STATUSES, LISTABLE_STATUSES

from conftest import requires_db


# --------------------------------------------------------------- fixtures --


@pytest.fixture
def applicant():
    """An application in any status, cleaned up WHATEVER the test does.

    Same shape and same reason as `test_institutional_spine.py`'s: a leaked
    Registration does not merely clutter the dev database, it MASKS the next
    run's regression, because the rerun finds the leftover row and reuses it
    instead of inserting. A yield fixture's finaliser runs on a failed assertion
    too, which is the whole point.
    """
    created: list[tuple[str, bool]] = []

    def _make(
        email: str,
        name: str = "Hold Applicant",
        *,
        status: RegistrationStatus = RegistrationStatus.PENDING_REVIEW,
        usn: str | None = None,
        college_id: str | None = None,
        hold_note: str | None = None,
    ) -> str:
        with SessionLocal() as db:
            pre_existing = db.scalar(select(User).where(User.email == email)) is not None
            db.execute(delete(Registration).where(Registration.email == email))
            reg = Registration(
                name=name,
                email=email,
                usn=usn,
                degree_level=DegreeLevel.PG,
                status=status,
                college_id=college_id,
                hold_note=hold_note,
            )
            db.add(reg)
            db.commit()
            created.append((email, pre_existing))
            return reg.id

    yield _make

    with SessionLocal() as db:
        for email, pre_existing in created:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None and not pre_existing:
                for stu in db.scalars(select(Student).where(Student.user_id == user.id)).all():
                    db.execute(delete(StudentProfile).where(StudentProfile.student_id == stu.id))
                    db.execute(delete(Student).where(Student.id == stu.id))
            db.execute(delete(Registration).where(Registration.email == email))
            if user is not None and not pre_existing:
                db.execute(delete(User).where(User.id == user.id))
        db.commit()


@pytest.fixture
def scoped_grant():
    """One capability, scoped to one rung, taken back afterwards.

    A row rather than `POST /api/admin/governance/grants`, for the reason
    test_exports.py and test_scoped_lists.py both give: `admin.registrations`
    is `carries_pii`, so a deputy's API-made grant lands `pending_approval`
    under B2.4 and holds NOTHING while the Main Admin's is live at once — a
    fixture that depended on who granted would make every scope test about the
    approval rule instead.
    """
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel, target_id: str) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key,
                subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                scope_level=level,
                scope_id=target_id,
                reason="the hold tests need a grant that reaches one college only",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


@pytest.fixture
def college():
    """One college, so a grant has a real rung to hang on."""
    made: list[str] = []

    def _make() -> str:
        tag = uuid.uuid4().hex[:6]
        with SessionLocal() as db:
            row = College(code=f"H{tag.upper()}", name="Hold College", status=STATUS_ACTIVE)
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _make

    with SessionLocal() as db:
        db.execute(delete(College).where(College.id.in_(made)))
        db.commit()


def _status_of(registration_id: str) -> RegistrationStatus:
    with SessionLocal() as db:
        return db.get(Registration, registration_id).status


def _row_in(rows: list[dict], registration_id: str) -> dict | None:
    for row in rows:
        if row["id"] == registration_id:
            return row
    return None


# ------------------------------------------------------------- holding it --


@requires_db
def test_holding_stamps_who_when_and_why_and_leaves_the_applicant_alone(
    client, make_user, applicant
):
    """The whole feature in one row: a status, a note, a reviewer, a time.

    AND THE HALF THAT IS DEFINED BY WHAT DOES NOT HAPPEN. A hold is INTERNAL
    (decision 4): `decision_reason` is what the applicant reads on their own
    result card, and a hold that rewrote it would tell somebody their
    application had been re-decided by a step that is a bookmark. Nothing is
    mailed either, which is why the note can be written about them rather than
    to them.
    """
    admin = make_user("hold-stamp", Role.ADMIN)
    reg_id = applicant("hold.stamp@bgscet.ac.in")
    with SessionLocal() as db:
        db.get(Registration, reg_id).decision_reason = "Routed by rule 'X' — awaiting review."
        db.commit()

    r = client.post(
        f"/api/register/{reg_id}/hold",
        headers=admin.headers,
        json={"note": "No CV attached. Emailed him for it on Tuesday."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "HOLD"
    assert body["hold_note"] == "No CV attached. Emailed him for it on Tuesday."
    assert body["held_by_id"] == admin.user_id
    assert body["held_at"] is not None
    # Untouched: the applicant's side of the record.
    assert body["decision_reason"] == "Routed by rule 'X' — awaiting review."
    assert body["reviewed_by_id"] is None and body["reviewed_at"] is None


@requires_db
def test_a_hold_and_its_release_both_reach_the_audit_trail(client, make_user, applicant):
    """Two acts, two rows, and the release carries the note it cleared.

    `reopen` clears `hold_note`, so the trail is the ONLY place the words
    survive — an application released in March and held again in April would
    otherwise read as having only ever waited on the second thing. The `before`
    of a release is where the first reviewer's sentence is kept.
    """
    from app.models.redesign import AuditEvent

    admin = make_user("hold-audit", Role.ADMIN)
    reg_id = applicant("hold.audit@bgscet.ac.in")
    assert client.post(
        f"/api/register/{reg_id}/hold", headers=admin.headers, json={"note": "waiting on marks"}
    ).status_code == 200
    assert client.post(f"/api/register/{reg_id}/reopen", headers=admin.headers).status_code == 200

    with SessionLocal() as db:
        rows = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "registration", AuditEvent.entity_id == reg_id)
            .order_by(AuditEvent.occurred_at)
        ).all()
        actions = [row.action for row in rows]
        assert "HELD" in actions and "REOPENED" in actions
        released = [row for row in rows if row.action == "REOPENED"][-1]
        assert released.before_json["hold_note"] == "waiting on marks"


@requires_db
def test_a_hold_with_no_note_is_refused(client, make_user, applicant):
    """The note IS the feature, so it is required rather than encouraged.

    A HOLD carrying no words is indistinguishable from PENDING_REVIEW on every
    screen in the product — same queue, same buttons, same everything — so a
    note-less hold buys the office nothing and costs the next reviewer the
    reading they thought had already been done. Same rule as B3.1's disable
    reason, for the same reason: nobody remembers in six weeks.
    """
    admin = make_user("hold-nonote", Role.ADMIN)
    reg_id = applicant("hold.nonote@bgscet.ac.in")

    for body in ({}, {"note": None}, {"note": "   "}):
        r = client.post(f"/api/register/{reg_id}/hold", headers=admin.headers, json=body)
        assert r.status_code == 422, (body, r.text)
    assert _status_of(reg_id) is RegistrationStatus.PENDING_REVIEW


@requires_db
def test_holding_a_held_application_is_refused_and_names_reopen(client, make_user, applicant):
    """Re-holding is not an in-place edit of the note, and must not become one.

    Two audit rows — released, then held again — say what actually happened.
    Overwriting the note in place loses the first reviewer's words entirely, and
    those words are the only record of what the application was waiting on
    before somebody decided it was waiting on something else.
    """
    admin = make_user("hold-twice", Role.ADMIN)
    reg_id = applicant("hold.twice@bgscet.ac.in")
    assert client.post(
        f"/api/register/{reg_id}/hold", headers=admin.headers, json={"note": "the first reason"}
    ).status_code == 200

    again = client.post(
        f"/api/register/{reg_id}/hold", headers=admin.headers, json={"note": "a second reason"}
    )
    assert again.status_code == 409, again.text
    assert "reopen" in again.json()["detail"].lower()
    with SessionLocal() as db:
        assert db.get(Registration, reg_id).hold_note == "the first reason"


@requires_db
def test_a_decided_application_cannot_be_held(client, make_user, applicant):
    """Hold is a bookmark in a queue, and a decided row has left the queue.

    A REJECTED application that could be held would appear back in a tab of
    applications waiting on somebody, which is exactly the lie the Held tab
    exists to stop telling.
    """
    admin = make_user("hold-decided", Role.ADMIN)
    reg_id = applicant("hold.decided@bgscet.ac.in", status=RegistrationStatus.REJECTED)

    r = client.post(f"/api/register/{reg_id}/hold", headers=admin.headers, json={"note": "nope"})
    assert r.status_code == 409, r.text
    assert _status_of(reg_id) is RegistrationStatus.REJECTED


@requires_db
def test_a_scoped_reviewer_cannot_hold_an_application_out_of_reach(
    client, make_user, applicant, scoped_grant, college
):
    """THE QUEUE AND EVERY BUTTON ON IT OBEY ONE PREDICATE.

    `_assert_reachable` re-SELECTs through `registration_scope_clause` — the
    list's own clause — rather than reading the reach a second time in Python,
    so a new write route is either wired to it or is a hole. This is that test
    for `/hold`: an application that named NOTHING hangs under no rung and is
    reached by no scoped grant, so a reviewer scoped to one college is refused,
    with 403 and not 404 because the id came off a screen they were shown.
    """
    reviewer = make_user("hold-scoped", Role.MENTOR)
    scoped_grant(reviewer.user_id, "admin.registrations", ScopeLevel.COLLEGE, college())
    reg_id = applicant("hold.unreachable@bgscet.ac.in")

    r = client.post(
        f"/api/register/{reg_id}/hold", headers=reviewer.headers, json={"note": "not yours"}
    )
    assert r.status_code == 403, r.text
    assert _status_of(reg_id) is RegistrationStatus.PENDING_REVIEW


# ------------------------------------------- what HOLD must not switch off --


@requires_db
def test_a_held_application_is_still_decidable(client, make_user, applicant):
    """The already-decided guard tests a NAMED set, and HOLD is not in it.

    This passed by accident before the constant existed — the guard happened to
    list two statuses and HOLD fell through the gap. An accident is not a
    property: the next person to rewrite that condition as "anything but
    PENDING_REVIEW" would strand every held application with no verb that ends
    it, and no test would have noticed.
    """
    assert RegistrationStatus.HOLD not in ALREADY_DECIDED_STATUSES
    admin = make_user("hold-decide", Role.ADMIN)
    reg_id = applicant("hold.decide@bgscet.ac.in")
    assert client.post(
        f"/api/register/{reg_id}/hold", headers=admin.headers, json={"note": "waiting on a call"}
    ).status_code == 200

    r = client.post(
        f"/api/register/{reg_id}/decision",
        headers=admin.headers,
        json={"decision": "REJECT", "note": "He never sent the CV."},
    )
    assert r.status_code == 200, r.text
    assert _status_of(reg_id) is RegistrationStatus.REJECTED


@requires_db
def test_reopen_releases_a_hold_and_clears_its_three_columns(client, make_user, applicant):
    """One verb, one meaning: back in the queue (decision 5).

    A second route doing the same thing to a different status is how the two
    drift — and the drift that matters is the stamp, not the status: a released
    hold whose `held_by_id` survived would show the Held tab's "held by" column
    on a row sitting in Pending.
    """
    admin = make_user("hold-reopen", Role.ADMIN)
    reg_id = applicant("hold.reopen@bgscet.ac.in")
    assert client.post(
        f"/api/register/{reg_id}/hold", headers=admin.headers, json={"note": "waiting on a USN"}
    ).status_code == 200

    r = client.post(f"/api/register/{reg_id}/reopen", headers=admin.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "PENDING_REVIEW"
    assert body["hold_note"] is None
    assert body["held_by_id"] is None
    assert body["held_at"] is None


@requires_db
def test_reopening_an_approved_application_is_still_refused(client, make_user, applicant):
    """The 409 stands, and the board is the thing that is wrong (decision 6).

    Reopen now accepts a second status, which is exactly the edit that could
    have loosened this one. An approved application has a User, a Student and an
    owner who has been told to sign in; "undo" there is a deprovisioning that
    `python -m app.purge_students` does deliberately and no button does quietly.
    """
    admin = make_user("hold-reopen-appr", Role.ADMIN)
    reg_id = applicant("hold.approved@bgscet.ac.in", status=RegistrationStatus.APPROVED)

    r = client.post(f"/api/register/{reg_id}/reopen", headers=admin.headers)
    assert r.status_code == 409, r.text
    assert _status_of(reg_id) is RegistrationStatus.APPROVED


@requires_db
def test_a_held_applicant_can_still_attach_the_document_they_are_held_for(client, applicant):
    """"Held for a missing CV" is the main reason to hold, so the CV must land.

    The upload endpoint is PUBLIC — the application id is the bearer — and its
    status gate listed only the two pending states. Leaving HOLD out of it would
    make the hold note an instruction the product itself blocks: the reviewer
    asks for a CV and the form answers "this application has already been
    decided".
    """
    reg_id = applicant("hold.upload@bgscet.ac.in", status=RegistrationStatus.HOLD)
    # A one-page PDF, the smallest thing document_store will sniff as one.
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

    r = client.post(
        f"/api/register/{reg_id}/documents/cv",
        files={"file": ("cv.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    # And the applicant is told nothing about the hold: `PublicRegistrationOut`
    # declares none of the three columns, so the reviewer's note cannot travel.
    assert "hold_note" not in r.json()
    assert "held_by_id" not in r.json()


@requires_db
def test_the_pending_registrations_tile_still_counts_a_held_application(
    client, make_user, applicant
):
    """Decision 7: a KPI that drops when somebody presses Hold is worse than
    either answer.

    The tile is ONE number and it means "waiting on us". The Registrations
    screen splits the two statuses into tabs because it has room to; Analytics
    does not, and an office reading its queue getting shorter because a reviewer
    parked four applications is being told they made progress they did not make.
    """
    assert RegistrationStatus.HOLD in PENDING_QUEUE_STATUSES
    admin = make_user("hold-tile", Role.ADMIN)
    reg_id = applicant("hold.tile@bgscet.ac.in")

    before = client.get("/api/admin/analytics-summary", headers=admin.headers).json()
    assert client.post(
        f"/api/register/{reg_id}/hold", headers=admin.headers, json={"note": "on hold for the tile"}
    ).status_code == 200
    after = client.get("/api/admin/analytics-summary", headers=admin.headers).json()

    assert after["pending_registrations"] == before["pending_registrations"], (
        "holding an application changed the pending count; the tile counts "
        "PENDING_REVIEW or HOLD precisely so it does not"
    )


# ------------------------------------------------------ the by-status queue --


@requires_db
def test_the_default_queue_is_byte_for_byte_what_it_always_was(client, make_user, applicant):
    """THE ONE ASSERTION THAT PROTECTS THE SCREEN THAT ALREADY WORKS.

    `?status=` is additive or it is a breaking change to the list the Phase 2
    console is built against and the office works from every morning. Held rows
    stay OUT of the default page (they have their own tab), no page size
    appears, and the scope headers are unchanged.
    """
    admin = make_user("queue-default", Role.ADMIN)
    pending_id = applicant("queue.default@bgscet.ac.in")
    held_id = applicant("queue.held@bgscet.ac.in", status=RegistrationStatus.HOLD)

    r = client.get("/api/register/pending", headers=admin.headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert r.headers["X-Reep-Scope"] == "programme"
    assert _row_in(rows, pending_id) is not None
    assert _row_in(rows, held_id) is None, "a held row must not be in the default Pending page"
    # And the checklist is still on every row of it (B11.1's contract).
    assert _row_in(rows, pending_id)["checks"] is not None


@requires_db
def test_each_tab_asks_for_its_own_status(client, make_user, applicant):
    """The three dead tabs, alive (decision 2).

    `GET /register/pending` hard-coded one status, so Auto-approved, Held and
    Rejected were drawn disabled because there was no way to LIST them — 04
    §B11.2 buys the status and not the listing.
    """
    admin = make_user("queue-tabs", Role.ADMIN)
    made = {
        RegistrationStatus.HOLD: applicant(
            "queue.tab.hold@bgscet.ac.in", status=RegistrationStatus.HOLD
        ),
        RegistrationStatus.AUTO_APPROVED: applicant(
            "queue.tab.auto@bgscet.ac.in", status=RegistrationStatus.AUTO_APPROVED
        ),
        RegistrationStatus.REJECTED: applicant(
            "queue.tab.rej@bgscet.ac.in", status=RegistrationStatus.REJECTED
        ),
    }
    for wanted, reg_id in made.items():
        rows = client.get(
            f"/api/register/pending?status={wanted.value}", headers=admin.headers
        ).json()
        assert _row_in(rows, reg_id) is not None, f"{wanted.value} tab did not list its own row"
        assert all(row["status"] == wanted.value for row in rows)
        # And no other tab's row leaked into it.
        for other, other_id in made.items():
            if other is not wanted:
                assert _row_in(rows, other_id) is None


@requires_db
def test_a_decided_row_answers_checks_null_rather_than_an_empty_list(
    client, make_user, applicant
):
    """NULL MEANS NOT COMPUTED, and `[]` would mean "nothing to report".

    A live checklist on a decided row would answer a question that was settled
    last month — "Approve will refuse this" about an application already
    approved. The Rejected tab therefore carries no checklist at all, which the
    client renders as no section; an empty array would render as a clean bill of
    health for a row nobody checked.
    """
    admin = make_user("queue-nochecks", Role.ADMIN)
    reg_id = applicant("queue.nochecks@bgscet.ac.in", status=RegistrationStatus.REJECTED)

    rows = client.get("/api/register/pending?status=REJECTED", headers=admin.headers).json()
    assert _row_in(rows, reg_id)["checks"] is None

    held_id = applicant("queue.heldchecks@bgscet.ac.in", status=RegistrationStatus.HOLD)
    held_rows = client.get("/api/register/pending?status=HOLD", headers=admin.headers).json()
    assert _row_in(held_rows, held_id)["checks"] is not None, (
        "a held row is still decidable, so it still gets the reviewer's checklist"
    )


@requires_db
def test_an_unknown_status_is_refused_and_names_the_ones_that_exist(client, make_user):
    """Including the two dead ones, which are refused rather than answered empty.

    `?status=DRAFT` returning `[]` would read as "nobody was ever a draft" when
    the truth is that nothing has written DRAFT since the table was created. A
    422 naming the five says which it is.
    """
    admin = make_user("queue-badstatus", Role.ADMIN)
    for bad in ("DRAFT", "PENDING_VERIFICATION", "banana", ""):
        r = client.get(f"/api/register/pending?status={bad}", headers=admin.headers)
        assert r.status_code == 422, (bad, r.text)
    detail = client.get(
        "/api/register/pending?status=banana", headers=admin.headers
    ).json()["detail"]
    for listable in LISTABLE_STATUSES:
        assert listable.value in detail


@requires_db
def test_paging_is_part_of_status_and_is_refused_without_it(client, make_user):
    """A silently ignored `limit` is a queue missing applications with nothing
    on screen to say so.

    The default page is deliberately unbounded (it is the list the office works
    from). A client that sends `?limit=25` to it believes it is paging; handing
    back every row and letting it render the first 25 turns a misunderstanding
    into "we never saw that application". A 422 is a bug report.
    """
    admin = make_user("queue-page-guard", Role.ADMIN)
    assert client.get("/api/register/pending?limit=5", headers=admin.headers).status_code == 422
    assert client.get("/api/register/pending?offset=5", headers=admin.headers).status_code == 422
    assert client.get("/api/register/pending", headers=admin.headers).status_code == 200


@requires_db
def test_the_by_status_page_is_bounded_and_pages(client, make_user, applicant):
    """AUTO_APPROVED grows for the life of the deployment, so the bound is not
    optional.

    This endpoint has never had a LIMIT — survivable for a queue an office works
    through, not for a tab listing every application a rule ever waved through,
    with its claim names and document kinds, on every page load.
    """
    admin = make_user("queue-paging", Role.ADMIN)
    ids = [
        applicant(f"queue.page{n}@bgscet.ac.in", status=RegistrationStatus.AUTO_APPROVED)
        for n in range(3)
    ]

    first = client.get(
        "/api/register/pending?status=AUTO_APPROVED&limit=1", headers=admin.headers
    ).json()
    assert len(first) == 1
    second = client.get(
        "/api/register/pending?status=AUTO_APPROVED&limit=1&offset=1", headers=admin.headers
    ).json()
    assert len(second) == 1
    assert first[0]["id"] != second[0]["id"]
    # Newest first on a decided status: these three were made in order, so the
    # most recent of them must come before the oldest.
    ordered = client.get(
        "/api/register/pending?status=AUTO_APPROVED&limit=500", headers=admin.headers
    ).json()
    positions = {row["id"]: n for n, row in enumerate(ordered)}
    assert positions[ids[2]] < positions[ids[0]]


@requires_db
def test_the_by_status_queue_is_scoped_by_the_same_clause_as_the_default(
    client, make_user, applicant, scoped_grant, college
):
    """A new query parameter must not be a new way around `Reach`.

    The tabs are one `select()` with one `registration_scope_clause` on it, so
    this cannot drift by construction — which is worth a test precisely because
    the cheap implementation (a second endpoint for the other statuses) would
    have made it drift by omission.
    """
    reviewer = make_user("queue-scoped", Role.MENTOR)
    scoped_grant(reviewer.user_id, "admin.registrations", ScopeLevel.COLLEGE, college())
    unreachable = applicant("queue.unreachable@bgscet.ac.in", status=RegistrationStatus.HOLD)

    r = client.get("/api/register/pending?status=HOLD", headers=reviewer.headers)
    assert r.status_code == 200, r.text
    assert _row_in(r.json(), unreachable) is None
