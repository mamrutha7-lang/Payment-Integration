"""B2.7 - the audit trail, read back.

`record_change` has been filling `redesign_audit_events` from twenty-seven
endpoints for months and nothing could read it but `psql`. These tests pin the
reader: who may open it, that a page is stable, that every filter narrows, that
the detail carries the before/after pair a list omits, that the download is
itself an event, and that both destructors still say KEEP for the table.

The claims are deliberately about BEHAVIOUR a person would notice. A trail that
repeats one row across two pages, or a CSV whose actor name runs as a formula
when the office opens it in Excel, or a download nobody can prove happened, are
each a way for an audit log to be present and useless.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from conftest import application_files, requires_db

from app.db import SessionLocal
from app.models.redesign import AuditEvent, OutboxEvent
from app.models.registration import Registration
from app.models.student_profile import StudentProfile
from app.models.user import LoginDay, Role, Student, User

API = "/api/admin/audit"


def _rows(payload: dict) -> list[dict]:
    return payload["items"]


@pytest.fixture
def trail(make_user):
    """Sweep the audit rows a test creates.

    `redesign_audit_events` has no cascade back to the things a test makes - it
    is append-only ON PURPOSE, which is the whole point of the table - so the
    rows a test writes outlive it unless they are removed by hand.

    Requesting `make_user` is what orders this teardown BEFORE the accounts go:
    `actor_user_id` is SET NULL, so a row whose actor is deleted first can no
    longer be found by actor and would leak into the next test's listing.
    """
    ids: list[str] = []
    actors: list[str] = []

    def _seed(**kw) -> str:
        """Write one audit row straight into the table.

        Straight in, rather than through an endpoint, wherever the test is about
        the READER: a listing that can only be tested by first performing the
        mutation it describes can only ever cover the mutations that exist.
        """
        row = AuditEvent(
            actor_type="USER",
            entity_type=kw.pop("entity_type"),
            entity_id=kw.pop("entity_id"),
            action=kw.pop("action"),
            metadata_json=kw.pop("metadata_json", {}),
            **kw,
        )
        with SessionLocal() as db:
            db.add(row)
            db.commit()
            ids.append(row.id)
            return row.id

    yield _seed, ids, actors

    with SessionLocal() as db:
        if ids:
            db.execute(delete(AuditEvent).where(AuditEvent.id.in_(ids)))
        if actors:
            db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(actors)))
        db.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_type == "audit_export"))
        db.commit()


# ------------------------------------------------------------ the gate --


@requires_db
def test_the_trail_is_the_offices_alone(client, make_user, trail):
    """Every role but the Main Admin is refused, on all three endpoints.

    Delete this and the audit log becomes readable by any signed-in account.
    That is not a list of harmless administrivia: the rows name students by id,
    carry before/after snapshots of their records, and say which staff member
    touched them - it is the most concentrated view of student data in REEP,
    and the only one that cannot be undone by revoking a capability afterwards.
    """
    seed, _, _ = trail
    marker = f"gate-{uuid.uuid4().hex[:8]}"
    event_id = seed(entity_type=marker, entity_id=uuid.uuid4().hex, action="GRANTED")

    admin = make_user("aud-gate-adm", Role.ADMIN)
    for label, role in (("stu", Role.STUDENT), ("men", Role.MENTOR), ("alu", Role.ALUMNI)):
        who = make_user(f"aud-gate-{label}", role)
        for url in (API, f"{API}/{event_id}", f"{API}/export.csv"):
            r = client.get(url, headers=who.headers)
            assert r.status_code == 403, f"{role.value} reached {url}: {r.status_code}"

    assert client.get(API, headers=admin.headers).status_code == 200
    assert client.get(f"{API}/{event_id}", headers=admin.headers).status_code == 200
    # FILTERED, like the three other export tests in this module, and not because
    # filtering is tidier: `export.csv` refuses a query matching more than
    # MAX_EXPORT_ROWS (10 000) with a 413, deliberately, so that a download
    # nobody bounded cannot stream the whole trail. An UNFILTERED assertion here
    # therefore tests the size of the database rather than the gate this test is
    # about — it passed on CI's fresh schema and failed the moment a long-lived
    # development database crossed the cap, which is the same shape of bug as
    # the B1.1 backfill test that could only pass on a database predating its
    # own migration.
    assert client.get(
        f"{API}/export.csv", headers=admin.headers, params={"target_type": marker}
    ).status_code == 200


@requires_db
def test_an_unknown_event_is_a_404_and_not_a_500(client, make_user, trail):
    """A missing id answers 404.

    Without this the detail route is one `None` dereference away from a 500 on
    a link the console drew from a page that has since been filtered - which
    reads to an operator as "the audit log is broken", not "that row is gone".
    """
    admin = make_user("aud-404", Role.ADMIN)
    r = client.get(f"{API}/{uuid.uuid4().hex}", headers=admin.headers)
    assert r.status_code == 404, r.text


# ------------------------------------------------------------ the page --


@requires_db
def test_paging_a_tie_loses_no_row_and_repeats_none(client, make_user, trail):
    """Five events written at the SAME instant page cleanly in twos.

    `occurred_at` is a server default, so every row `record_change` writes
    inside one transaction shares a timestamp to the microsecond. Order by that
    column alone and Postgres may return the tied rows in a different order for
    each OFFSET, which shows one event on page 1 and again on page 2 while
    another appears on neither - the one failure mode that makes an audit log
    quietly wrong rather than obviously broken. `id` is the tiebreaker; delete
    it and this test fails intermittently, which is the honest signal.
    """
    admin = make_user("aud-page", Role.ADMIN)
    marker = f"audit_test_page_{uuid.uuid4().hex[:8]}"
    tied = datetime.now(timezone.utc)
    seed, _, _ = trail
    for n in range(5):
        seed(entity_type=marker, entity_id=f"e{n}", action="TOUCHED", occurred_at=tied)

    seen: list[str] = []
    for page in (1, 2, 3):
        r = client.get(
            API, headers=admin.headers, params={"target_type": marker, "page": page, "page_size": 2}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 5, "total is the whole filtered set, not the page"
        seen.extend(row["id"] for row in _rows(body))

    assert len(seen) == 5 and len(set(seen)) == 5, seen


@requires_db
def test_every_filter_narrows_and_an_unknown_actor_shows_nothing(client, make_user, trail):
    """actor (by id AND by email), action, target_type, target_id, from, to.

    Each one is the difference between an investigation and a scroll. The email
    form matters on its own: an operator knows a colleague by address, never by
    a uuid4 hex, and an address nobody holds must return an EMPTY page - if it
    silently fell through to "no filter" the console would answer a question
    about one person with the whole log.
    """
    admin = make_user("aud-filter", Role.ADMIN)
    other = make_user("aud-filter-2", Role.ADMIN)
    marker = f"audit_test_filter_{uuid.uuid4().hex[:8]}"
    seed, _, _ = trail
    old = datetime.now(timezone.utc) - timedelta(days=30)

    mine = seed(
        entity_type=marker, entity_id="alpha", action="GRANTED", actor_user_id=admin.user_id
    )
    theirs = seed(
        entity_type=marker, entity_id="beta", action="REVOKED", actor_user_id=other.user_id
    )
    ancient = seed(
        entity_type=marker,
        entity_id="alpha",
        action="GRANTED",
        actor_user_id=admin.user_id,
        occurred_at=old,
    )

    def ids(**params) -> set[str]:
        params.setdefault("target_type", marker)
        r = client.get(API, headers=admin.headers, params=params)
        assert r.status_code == 200, r.text
        return {row["id"] for row in _rows(r.json())}

    assert ids() == {mine, theirs, ancient}
    assert ids(actor=admin.user_id) == {mine, ancient}
    assert ids(actor=admin.email) == {mine, ancient}, "an address resolves to the account"
    assert ids(actor="nobody@bgscet.ac.in") == set(), "an unknown address narrows to nothing"
    assert ids(action="REVOKED") == {theirs}
    assert ids(target_id="beta") == {theirs}
    assert ids(**{"from": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}) == {
        mine,
        theirs,
    }
    assert ids(to=(old + timedelta(minutes=1)).isoformat()) == {ancient}


@requires_db
def test_a_naive_date_bound_is_read_as_utc(client, make_user, trail):
    """`?from=2026-09-01T00:00:00` - no offset - is accepted and means UTC.

    The console's date pickers send exactly that. Comparing a naive datetime
    against this timezone-aware column raises inside psycopg rather than
    answering, so without the coercion the most ordinary use of the screen -
    "show me last week" - is a 500.
    """
    admin = make_user("aud-naive", Role.ADMIN)
    marker = f"audit_test_naive_{uuid.uuid4().hex[:8]}"
    seed, _, _ = trail
    seed(entity_type=marker, entity_id="x", action="TOUCHED")

    r = client.get(
        API,
        headers=admin.headers,
        params={
            "target_type": marker,
            "from": (datetime.now(timezone.utc) - timedelta(days=1))
            .replace(tzinfo=None)
            .isoformat(),
        },
    )
    assert r.status_code == 200, r.text
    assert len(_rows(r.json())) == 1


# ----------------------------------------------------------- the detail --


@requires_db
def test_a_real_grant_is_on_the_trail_with_its_before_and_after(client, make_user, granted, trail):
    """The reader and the writer agree, on a mutation that really happened.

    Every other test here seeds rows directly; this one performs a governance
    grant through its own endpoint and finds it. If the reader's column names
    ever drift from what `record_change` writes - `entity_type` is exposed as
    `target_type`, which is exactly the kind of rename that goes one-way - this
    is the test that notices.

    It also pins the split between list and detail: the snapshots are whole
    documents and belong on ONE event, not on all fifty rows of a page.
    """
    admin = make_user("aud-grant", Role.ADMIN)
    _, _, actors = trail
    actors.append(admin.user_id)
    grant_id = granted(admin, "mentor.verifications")

    r = client.get(
        API, headers=admin.headers, params={"target_type": "capability_grant", "target_id": grant_id}
    )
    assert r.status_code == 200, r.text
    rows = _rows(r.json())
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["action"] == "GRANTED"
    assert row["actor_email"] == admin.email, "the trail names the person, not only their id"
    assert row["route"] == "/api/admin/governance/grants"
    assert "before" not in row and "after" not in row, "the page stays small"

    detail = client.get(f"{API}/{row['id']}", headers=admin.headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["after"]["capability"] == "mentor.verifications"
    assert body["metadata"]["route"] == "/api/admin/governance/grants"


# ----------------------------------------------------------- the export --


def _csv_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


@requires_db
def test_the_download_is_itself_an_event(client, make_user, trail):
    """Exporting the audit log leaves a row saying who took it and how many.

    The list is deliberately NOT audited - reading a log is not an event worth
    logging, and a row per page view would bury the table within a week. A
    FILE is different: it leaves the building and nothing here can recall it,
    which is the same reasoning `export_events` exists for. Delete this and the
    one act on this screen with consequences outside REEP is the only one with
    no trace.
    """
    admin = make_user("aud-export", Role.ADMIN)
    _, _, actors = trail
    actors.append(admin.user_id)
    marker = f"audit_test_export_{uuid.uuid4().hex[:8]}"
    seed, _, _ = trail
    seed(entity_type=marker, entity_id="one", action="TOUCHED")
    seed(entity_type=marker, entity_id="two", action="TOUCHED")

    r = client.get(f"{API}/export.csv", headers=admin.headers, params={"target_type": marker})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = _csv_rows(r.text)
    assert rows[0][0] == "When" and "Target id" in rows[0]
    assert len(rows) == 3, "a header and the two matching events"
    assert "audit_export" not in r.text, "an export never contains the record of itself"

    with SessionLocal() as db:
        written = db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "audit_export",
                AuditEvent.actor_user_id == admin.user_id,
            )
        ).all()
    assert len(written) == 1, "one download, one row"
    assert written[0].action == "EXPORTED"
    assert written[0].after_json["rows"] == 2
    assert written[0].after_json["filters"]["target_type"] == marker, (
        "the filters that decided which rows left the building are part of the record"
    )


@requires_db
def test_a_cell_that_would_run_as_a_formula_is_neutralised(client, make_user, trail):
    """A value beginning `=` `+` `-` `@` TAB or CR is prefixed with an apostrophe.

    The values in this file are not the office's own vocabulary - an entity id,
    a route and an actor's name all arrive from elsewhere, and one recorded as
    `=HYPERLINK("http://evil", "Payslip")` becomes a live formula the moment the
    export is opened in Excel or Sheets. The apostrophe is the spreadsheet
    convention for "this is text": obeyed, never displayed. Delete this and the
    audit export becomes the attack the audit export exists to detect.
    """
    admin = make_user("aud-formula", Role.ADMIN)
    marker = f"audit_test_formula_{uuid.uuid4().hex[:8]}"
    seed, _, _ = trail
    seed(entity_type=marker, entity_id='=HYPERLINK("http://evil")', action="TOUCHED")

    r = client.get(f"{API}/export.csv", headers=admin.headers, params={"target_type": marker})
    assert r.status_code == 200, r.text
    body = _csv_rows(r.text)
    target_id = body[0].index("Target id")
    assert body[1][target_id].startswith("'="), body[1]


@requires_db
def test_an_oversized_export_refuses_rather_than_truncating(client, make_user, trail, monkeypatch):
    """Over the ceiling the download is refused, naming the count.

    Returning the first N rows instead would hand somebody a file that LOOKS
    complete, with nothing inside it saying which period it actually covers - a
    truncated audit export is worse than no export, because it is the kind of
    evidence people rely on.
    """
    from app.routers import audit as audit_router

    monkeypatch.setattr(audit_router, "MAX_EXPORT_ROWS", 1)
    admin = make_user("aud-cap", Role.ADMIN)
    marker = f"audit_test_cap_{uuid.uuid4().hex[:8]}"
    seed, _, _ = trail
    seed(entity_type=marker, entity_id="one", action="TOUCHED")
    seed(entity_type=marker, entity_id="two", action="TOUCHED")

    r = client.get(f"{API}/export.csv", headers=admin.headers, params={"target_type": marker})
    assert r.status_code == 413, r.text
    assert "2 events" in r.json()["detail"]


# ------------------------------------------------ the gap that was found --


@requires_db
def test_approving_an_application_is_on_the_trail(client, make_user, trail, tmp_document_store):
    """The one path that mints a student account records that it did.

    It did not. `POST /api/register/{id}/decision` wrote no audit row at all,
    while the `reopen` endpoint that UNDOES a rejection was audited - so the
    trail carried the undo of an act it had never recorded. The roster is the
    access control in REEP; an account appearing on it is the single most
    consequential write in the product, and the only record of the decision was
    a stamp on the application that reopen can clear.
    """
    admin = make_user("aud-reg", Role.ADMIN)
    _, _, actors = trail
    actors.append(admin.user_id)
    email = f"aud.applicant.{uuid.uuid4().hex[:8]}@bgscet.ac.in"

    created = client.post(
        "/api/register",
        data={"name": "Audit Applicant", "email": email, "usn": f"1BG26AUD{uuid.uuid4().hex[:3].upper()}",
              "phone": "+91 90000 00000", "personal_email": f"personal.{uuid.uuid4().hex[:8]}@gmail.com",
              "linkedin_url": "https://www.linkedin.com/in/audit-applicant", "degree_level": "PG"},
        files=application_files(),
    )
    assert created.status_code == 201, created.text
    reg_id = created.json()["id"]

    try:
        decided = client.post(
            f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
        )
        assert decided.status_code == 200, decided.text

        r = client.get(
            API, headers=admin.headers, params={"target_type": "registration", "target_id": reg_id}
        )
        assert r.status_code == 200, r.text
        rows = _rows(r.json())
        assert [row["action"] for row in rows] == ["APPROVED"], rows

        detail = client.get(f"{API}/{rows[0]['id']}", headers=admin.headers).json()
        assert detail["after"]["provisioned_user_id"], (
            "the account the decision created is the join between the application "
            "and the person who can now sign in"
        )
    finally:
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                for stu in db.scalars(select(Student).where(Student.user_id == user.id)).all():
                    db.execute(delete(StudentProfile).where(StudentProfile.student_id == stu.id))
                    db.execute(delete(Student).where(Student.id == stu.id))
                db.execute(delete(LoginDay).where(LoginDay.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
            db.execute(delete(Registration).where(Registration.id == reg_id))
            db.commit()


# ------------------------------------------------------------ retention --


def test_neither_destructor_empties_the_trail():
    """`purge_people` and `purge_students` both say KEEP, and retention never sweeps it.

    BOTH USED TO EMPTY IT. Handing a deployment over, or clearing a
    demonstration cohort, erased the record of who had done what to it -
    including the approvals that put those very accounts on the roster. The
    verdict maps are the only place that decision is written down, and each
    fails CI when a new table is unclassified, so a future editor reaching for
    "empty everything derived from people" meets this assertion first.

    No database needed: the verdicts are constants and the retention sweep is
    source text.
    """
    from pathlib import Path

    from app import purge_people, purge_students

    assert purge_people.VERDICTS["redesign_audit_events"] == purge_people.KEEP
    assert purge_students.STUDENT_VERDICTS["redesign_audit_events"] == purge_people.KEEP

    retention_src = Path(purge_people.__file__).with_name("retention.py").read_text(
        encoding="utf-8"
    )
    assert "AuditEvent" not in retention_src and "redesign_audit_events" not in retention_src, (
        "the audit trail is append-only; nothing on a clock may delete it"
    )


# ------------------------------------- the two vocabularies in one table --


@requires_db
def test_an_action_filter_reaches_a_row_written_in_lower_case(client, login):
    """`audit_events` grew TWO vocabularies and the filter could reach one.

    Most writers name an action in upper case; `routers/redesign.py`'s six
    mentor-notebook writes named it in lower (`created`, `updated`, `published`,
    `archived`, `registered`). `_filters` upper-cased the needle, so a search for
    `created` became `CREATED` — which matches interview-question rows and can
    never match a notebook one. Those events listed and opened perfectly and were
    unreachable by the filter directly above them, which an operator reports as
    "the notebook is not audited".

    Written against a row inserted in lower case ON PURPOSE, because that is what
    is already in the table: `audit_events` is append-only, the rows saying
    `created` are the record of what happened, and a migration rewriting them to
    `CREATED` would be the audit module editing the trail it exists to show. The
    reader is what has to cope, forever.
    """
    from app.models.redesign import AuditEvent

    tag = uuid.uuid4().hex[:10]
    with SessionLocal() as db:
        row = AuditEvent(
            actor_type="USER",
            entity_type="notebook_entry",
            entity_id=tag,
            action="created",  # lower case, exactly as redesign.py writes it
            metadata_json={"route": "/api/mentor/notebook"},
        )
        db.add(row)
        db.commit()
        event_id = row.id

    try:
        headers = login("admin@bgscet.ac.in", "admin123")
        for needle in ("created", "CREATED", "Created"):
            r = client.get(
                "/api/admin/audit",
                headers=headers,
                params={"action": needle, "target_id": tag},
            )
            assert r.status_code == 200, r.text
            ids = [item["id"] for item in r.json()["items"]]
            assert event_id in ids, (
                f"filtering on {needle!r} did not reach a row stored as 'created'"
            )
    finally:
        with SessionLocal() as db:
            db.execute(delete(AuditEvent).where(AuditEvent.id == event_id))
            db.commit()


@requires_db
def test_record_change_writes_one_vocabulary_from_now_on(client, login):
    """The other half: the funnel normalises, so the split stops growing.

    Normalised in `record_change` rather than at each call site because that is
    the one way in — the next writer does not have to know the convention.
    """
    from app.architecture_events import _normalise_action

    assert _normalise_action("created") == "CREATED"
    assert _normalise_action("  published  ") == "PUBLISHED"
    assert _normalise_action("GRANTED") == "GRANTED"
    assert _normalise_action("") == ""
