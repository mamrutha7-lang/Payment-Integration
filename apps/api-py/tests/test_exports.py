"""B14 — what an extract may carry, and the record that it left.

An export is the only thing in REEP that cannot be taken back: the rows leave
the moment they are downloaded and nothing here can recall them. These tests
hold down the four properties that made that acceptable, and each one is
written as the sentence it would break into if the assertion were deleted:

1. AN EXPORT IS A LIST THAT LEAVES THE BUILDING, so it is narrowed by the same
   reach as a list. Delete `test_a_scoped_holder_exports_only_their_own_reach`
   and a department-scoped `admin.exports` grant downloads the other
   department's roster, silently and permanently.
2. THE COLUMNS THAT NAME A PERSON ARE A SEPARATE DECISION from the file. Delete
   `test_a_holder_without_the_roster_function_gets_no_names` and every grant of
   Exports becomes a grant of the roster.
3. EVERY DOWNLOAD LEAVES A RECEIPT. Delete `test_every_download_writes_a_receipt`
   and the only record that a spreadsheet of students left the building is an
   access-log line nobody reads.
4. A CELL IS NOT A FORMULA. Delete `test_a_name_that_is_a_formula_is_neutralised`
   and a student who registers as `=HYPERLINK(...)` runs code on the placement
   officer's machine.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.account_events import ExportEvent
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.user import Role, Student, User
from app.seed_roster import SSO_ONLY_PASSWORD_HASH

EXPORTS = [
    "/api/admin/exports/students.csv",
    "/api/admin/exports/placement.csv",
    "/api/admin/exports/ledger.csv",
]


@pytest.fixture
def two_departments():
    """One college, two departments, one student seated in each.

    The shape every scope test in this repository uses, because it is the
    smallest one in which "only their own reach" is a statement that can fail.
    """
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {}
    with SessionLocal() as db:
        college = College(code=f"X{tag.upper()}", name="Export College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        here = Department(college_id=college.id, name="Here", code=f"H{tag}")
        there = Department(college_id=college.id, name="There", code=f"T{tag}")
        db.add_all([here, there])
        db.flush()
        batch = Cohort(
            code=f"XB-{tag}", name="Export Batch", batch_label="2026-28",
            degree_level=DegreeLevel.PG, department_id=here.id,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        db.add(batch)
        db.flush()
        # A name that is a live spreadsheet formula. Registered exactly as a
        # person could type it into the public form.
        mine_user = User(
            email=f"mine-{tag}@export.test", name="=HYPERLINK(\"http://evil\",\"pay\")",
            role=Role.STUDENT, password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        theirs_user = User(
            email=f"theirs-{tag}@export.test", name=f"Theirs {tag}",
            role=Role.STUDENT, password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        db.add_all([mine_user, theirs_user])
        db.flush()
        mine = Student(user_id=mine_user.id, usn=f"MINE{tag}", cohort_id=batch.id)
        theirs = Student(user_id=theirs_user.id, usn=f"THRS{tag}", department_id=there.id)
        db.add_all([mine, theirs])
        db.commit()
        made = {
            "tag": tag, "college": college.id, "here": here.id, "there": there.id,
            "batch": batch.id, "mine": mine.id, "theirs": theirs.id,
            "mine_usn": mine.usn, "theirs_usn": theirs.usn,
            "mine_user": mine_user.id, "theirs_user": theirs_user.id,
        }

    yield made

    with SessionLocal() as db:
        db.execute(delete(Student).where(Student.id.in_([made["mine"], made["theirs"]])))
        db.execute(delete(User).where(User.id.in_([made["mine_user"], made["theirs_user"]])))
        db.execute(delete(Cohort).where(Cohort.id == made["batch"]))
        db.execute(delete(Department).where(Department.id.in_([made["here"], made["there"]])))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


@pytest.fixture
def scoped_grant():
    """Give an account `admin.exports` scoped to one rung of the spine.

    Written as a row rather than through the Governance endpoint because
    `admin.exports` is flagged `carries_pii`, so a deputy's grant made through
    the API lands `pending_approval` (B2.4) and holds nothing until a different
    `admin.governance` holder approves it, while the Main Admin's is live at
    once — that difference is a different test's subject, and a fixture that
    turned on it would make this one pass for the wrong reason.
    """
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel | None, target_id: str | None) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key, subject_kind=SubjectKind.USER, subject_user_id=user_id,
                scope_level=level, scope_id=target_id,
                reason="the export tests need a scoped grant, twenty characters plus",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


def _rows(response) -> tuple[list[str], list[list[str]]]:
    reader = list(csv.reader(io.StringIO(response.text)))
    return reader[0], reader[1:]


def _receipts(user_id: str, kind: str) -> list[ExportEvent]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(ExportEvent)
                .where(ExportEvent.user_id == user_id, ExportEvent.kind == kind)
                .order_by(ExportEvent.at.desc())
            ).all()
        )


def _forget(user_id: str) -> None:
    with SessionLocal() as db:
        db.execute(delete(ExportEvent).where(ExportEvent.user_id == user_id))
        db.commit()


# --------------------------------------------------------------------- scope --


@requires_db
def test_a_scoped_holder_exports_only_their_own_reach(client, make_user, two_departments, scoped_grant):
    """A department-scoped Exports grant downloads that department and no more.

    IF THIS ASSERTION GOES, the widest possible failure of the narrowest
    possible grant comes back: the endpoint used to select every `students` row
    on the deployment, so a faculty member granted Exports for their own
    department walked out with the whole college's roster, in a file that
    cannot be recalled.
    """
    faculty = make_user(f"exp-scoped-{two_departments['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.exports", ScopeLevel.DEPARTMENT, two_departments["here"])
    try:
        r = client.get("/api/admin/exports/ledger.csv", headers=faculty.headers)
        assert r.status_code == 200, r.text
        assert r.headers["X-Reep-Export-Scope"] == "narrowed"
        body = r.text
        assert two_departments["mine_usn"] not in body, (
            "the USN column is omitted for this caller — see the personal-column test"
        )
        # The row count is the honest check that the reach was applied: one
        # student hangs under the granted department, one does not.
        assert r.headers["X-Reep-Export-Rows"] == "1", body
    finally:
        _forget(faculty.user_id)


@requires_db
def test_a_grant_pointing_at_a_deleted_department_exports_nothing(
    client, make_user, two_departments, scoped_grant
):
    """A grant scoped to a department that no longer exists.

    An empty reach and an unrestricted one are opposite facts that a careless
    `if not clauses` collapses into one — `policies.Reach.student_ids` carries
    its own warning about exactly that inversion. Delete this and the narrowest
    grant in the system downloads the whole programme.

    The header still goes out, so the file opens and is plainly empty rather
    than being a zero-byte download that reads as a broken button.
    """
    faculty = make_user(f"exp-none-{two_departments['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.exports", ScopeLevel.DEPARTMENT, "a-department-that-is-gone")
    try:
        r = client.get("/api/admin/exports/students.csv", headers=faculty.headers)
        assert r.status_code == 200, r.text
        header, rows = _rows(r)
        assert rows == [], "a grant that reaches no student must export no student"
        assert header, "the header still goes out, so the file opens and is plainly empty"
        # "narrowed", not "none": the grant DOES name a rung, it just names one
        # nothing hangs under any more. `Reach.nothing` is the different state
        # of holding no scoped grant at all, and the two must not be conflated
        # on a receipt somebody reads later.
        assert r.headers["X-Reep-Export-Scope"] == "narrowed"
        assert r.headers["X-Reep-Export-Rows"] == "0"
    finally:
        _forget(faculty.user_id)


@requires_db
def test_the_main_admin_is_not_narrowed(client, make_user, two_departments):
    """The office holds Exports by baseline, and a baseline capability is
    unscoped. Delete this and the fix for the scoped holder quietly becomes an
    empty roster for the account that holds every capability."""
    admin = make_user(f"exp-admin-{two_departments['tag']}", Role.ADMIN)
    try:
        r = client.get("/api/admin/exports/students.csv", headers=admin.headers)
        assert r.status_code == 200, r.text
        assert r.headers["X-Reep-Export-Scope"] == "programme"
        body = r.text
        assert two_departments["mine_usn"] in body
        assert two_departments["theirs_usn"] in body, (
            "the Main Admin sees both departments — scope is a property of a grant"
        )
    finally:
        _forget(admin.user_id)


# ---------------------------------------------------------- personal columns --


@requires_db
def test_a_holder_without_the_roster_function_gets_no_names(
    client, make_user, two_departments, scoped_grant
):
    """Name and USN are dropped — header AND cell — for a caller who does not
    hold `admin.students`.

    IF THIS GOES, every grant of Exports is a grant of the roster. The columns
    hang on the roster function and not on `Capability.carries_pii` for the
    reason `app/exports.py` sets out: `admin.exports` is itself flagged
    `carries_pii`, so that test is answered yes by the capability that opened
    the door, for every caller, always.
    """
    faculty = make_user(f"exp-nopii-{two_departments['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.exports", ScopeLevel.COLLEGE, two_departments["college"])
    try:
        r = client.get("/api/admin/exports/students.csv", headers=faculty.headers)
        assert r.status_code == 200, r.text
        header, rows = _rows(r)
        assert "Name" not in header and "USN" not in header, header
        assert "REEP stage" in header, "the file is still worth having without names"
        assert r.headers["X-Reep-Export-Personal"] == "omitted"
        assert all(len(row) == len(header) for row in rows), (
            "the cells were dropped with their header, not just the heading"
        )
        assert two_departments["theirs_usn"] not in r.text
    finally:
        _forget(faculty.user_id)


@requires_db
def test_the_roster_function_puts_the_names_back(
    client, make_user, two_departments, scoped_grant
):
    """`admin.students` is the decision that this person may read students by
    name. Delete this and the redaction has no way back, which is how a
    security rule becomes a bug report and then gets deleted."""
    faculty = make_user(f"exp-pii-{two_departments['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.exports", ScopeLevel.COLLEGE, two_departments["college"])
    scoped_grant(faculty.user_id, "admin.students", ScopeLevel.COLLEGE, two_departments["college"])
    try:
        r = client.get("/api/admin/exports/students.csv", headers=faculty.headers)
        assert r.status_code == 200, r.text
        header, _ = _rows(r)
        assert header[:2] == ["Name", "USN"]
        assert r.headers["X-Reep-Export-Personal"] == "included"
        assert two_departments["mine_usn"] in r.text
    finally:
        _forget(faculty.user_id)


# ------------------------------------------------------------------ receipts --


@requires_db
@pytest.mark.parametrize("path,kind", list(zip(EXPORTS, ["students", "placement", "ledger"])))
def test_every_download_writes_a_receipt(client, make_user, two_departments, path, kind):
    """Who, what, the filters that decided the rows, and how many there were.

    Delete this and an export leaves no trace but a line in an access log. The
    row count is part of the assertion on purpose: "an export happened" and "an
    export of 412 students happened" are different facts, and only the second
    one is worth waking somebody up for.
    """
    admin = make_user(f"exp-rcpt-{kind}-{two_departments['tag']}", Role.ADMIN)
    try:
        r = client.get(path, headers=admin.headers)
        assert r.status_code == 200, r.text
        _, rows = _rows(r)

        receipts = _receipts(admin.user_id, kind)
        assert len(receipts) == 1, "one download, one receipt"
        receipt = receipts[0]
        assert receipt.rows == len(rows), "the receipt counts the rows that actually left"
        assert receipt.rows == int(r.headers["X-Reep-Export-Rows"])
        assert bool(receipt.carried_pii) is True, "the Main Admin's file names people"
        assert receipt.filters == {"scope": "programme"}
    finally:
        _forget(admin.user_id)


@requires_db
def test_the_receipt_records_a_redacted_file_as_redacted(
    client, make_user, two_departments, scoped_grant
):
    """`carried_pii` on the row says WHICH KIND of file went, so nobody has to
    infer it from the date the rule changed."""
    faculty = make_user(f"exp-rcpt2-{two_departments['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.exports", ScopeLevel.DEPARTMENT, two_departments["here"])
    try:
        assert client.get(EXPORTS[0], headers=faculty.headers).status_code == 200
        receipt = _receipts(faculty.user_id, "students")[0]
        assert bool(receipt.carried_pii) is False
        assert receipt.filters["scope"] == "narrowed"
        assert receipt.filters["departments"] == [two_departments["here"]], (
            "the receipt records the reach that decided the rows, not just that one existed"
        )
    finally:
        _forget(faculty.user_id)


@requires_db
def test_the_history_shows_the_office_everything_and_a_scoped_holder_only_itself(
    client, make_user, two_departments, scoped_grant
):
    """`GET /api/admin/exports/history` — the one B14 response with a body, and
    therefore the only one that can carry `scope` the way 04 asks for.

    Delete the narrowing assertion and a department-scoped grant reads the
    office's download history, which is a list of what the office has been
    looking at.
    """
    admin = make_user(f"exp-hist-a-{two_departments['tag']}", Role.ADMIN)
    faculty = make_user(f"exp-hist-f-{two_departments['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.exports", ScopeLevel.DEPARTMENT, two_departments["here"])
    try:
        assert client.get(EXPORTS[0], headers=admin.headers).status_code == 200
        assert client.get(EXPORTS[2], headers=faculty.headers).status_code == 200

        mine = client.get("/api/admin/exports/history", headers=faculty.headers)
        assert mine.status_code == 200, mine.text
        assert mine.json()["scope"]["scope"] == "narrowed"
        assert {e["by_user_id"] for e in mine.json()["events"]} == {faculty.user_id}

        office = client.get("/api/admin/exports/history", headers=admin.headers)
        assert office.status_code == 200, office.text
        assert office.json()["scope"] == {"scope": "programme"}
        seen = {e["by_user_id"] for e in office.json()["events"]}
        assert {admin.user_id, faculty.user_id} <= seen, (
            "the point of a receipt is that somebody else reads it"
        )
    finally:
        _forget(admin.user_id)
        _forget(faculty.user_id)


# --------------------------------------------------------- formula injection --


@requires_db
def test_a_name_that_is_a_formula_is_neutralised(client, make_user, two_departments):
    """A student registered as `=HYPERLINK(...)` must not be a live formula.

    Excel and Google Sheets both evaluate it the moment a placement officer
    opens the file, on their machine with their credentials. The leading
    apostrophe is the spreadsheet convention for "this is text"; delete this
    assertion and the public registration form becomes a code-execution path
    into the placement office.
    """
    admin = make_user(f"exp-formula-{two_departments['tag']}", Role.ADMIN)
    try:
        r = client.get("/api/admin/exports/students.csv", headers=admin.headers)
        assert r.status_code == 200, r.text
        _, rows = _rows(r)
        mine = [row for row in rows if row[1] == two_departments["mine_usn"]]
        assert mine, "the student is in the file"
        assert mine[0][0].startswith("'="), mine[0][0]
    finally:
        _forget(admin.user_id)


# ------------------------------------------------------- the badge extract ---


@requires_db
def test_the_badge_extract_keeps_its_own_gate_and_gains_a_receipt(
    client, make_user, two_departments, scoped_grant
):
    """`/api/admin/badges/export.csv` is `require_admin` ON PURPOSE — the screen
    that renders the same data is, and when two gates disagree the safe
    reconciliation is the tighter one (see the comment on the endpoint).

    B14 is layered ON TOP rather than instead: the receipt and the
    personal-column rule apply, and the gate does not move. Delete the 403 half
    and a granted non-admin downloads the whole cohort's badge and growth
    record while being refused the window onto it.
    """
    faculty = make_user(f"exp-badge-f-{two_departments['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.exports", ScopeLevel.COLLEGE, two_departments["college"])
    admin = make_user(f"exp-badge-a-{two_departments['tag']}", Role.ADMIN)
    try:
        refused = client.get("/api/admin/badges/export.csv", headers=faculty.headers)
        assert refused.status_code == 403, refused.text

        r = client.get("/api/admin/badges/export.csv", headers=admin.headers)
        assert r.status_code == 200, r.text
        header, rows = _rows(r)
        assert header[:2] == ["Name", "USN"], "the Main Admin holds the roster function"
        assert r.headers["X-Reep-Export-Scope"] == "programme"
        mine = [row for row in rows if row[1] == two_departments["mine_usn"]]
        assert mine and mine[0][0].startswith("'="), (
            "every cell goes through the shared guard, not only the two that were "
            "obviously typed by a person"
        )

        receipts = _receipts(admin.user_id, "badges")
        assert len(receipts) == 1 and receipts[0].rows == len(rows)
    finally:
        _forget(admin.user_id)
        _forget(faculty.user_id)


@requires_db
def test_an_account_with_no_exports_capability_is_refused(client, make_user):
    """The gate itself, unchanged. A STUDENT and an ungranted faculty member
    both get 403 — not a redacted file, which would be a data-free but still
    scoped read of the roster's shape."""
    student = make_user(f"exp-stu-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    faculty = make_user(f"exp-plain-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    for path in EXPORTS + ["/api/admin/exports/history"]:
        assert client.get(path, headers=student.headers).status_code == 403, path
        assert client.get(path, headers=faculty.headers).status_code == 403, path


# ------------------------------------------------------------- the pure bits --


def test_the_cell_guard_covers_all_six_leading_characters():
    """`=`, `+`, `-`, `@` start a formula; TAB and CR are stripped by some
    readers BEFORE the formula test, so `\\t=cmd` arrives at the parser with the
    guard already satisfied. Delete any one and that character is the way in."""
    from app.exports import csv_cell

    for lead in ("=", "+", "-", "@", "\t", "\r"):
        assert csv_cell(f"{lead}cmd|'/c calc'!A1").startswith("'"), lead
    assert csv_cell("Ordinary Name") == "Ordinary Name"
    assert csv_cell(None) == ""
    assert csv_cell(7) == "7"


def test_dropping_personal_columns_is_by_name_and_not_by_index():
    """An index list beside a header list is two things that must agree, and
    the day somebody inserts a column in the middle the redaction starts
    deleting the wrong one — which fails in the safe-LOOKING direction (a name
    survives) and is invisible in review."""
    from app.exports import drop_personal

    header = ["Name", "USN", "Stage"]
    rows = [["Ada", "1MP", "REBOOT"]]
    assert drop_personal(header, rows, ["Name", "USN"], carry=True) == (header, rows)
    kept_header, kept_rows = drop_personal(header, rows, ["Name", "USN"], carry=False)
    assert kept_header == ["Stage"]
    assert kept_rows == [["REBOOT"]]


def test_a_reach_of_nothing_and_a_reach_of_everything_never_read_the_same():
    """Two opposite facts that a boolean would collapse into one."""
    from app.exports import scope_note
    from app.policies import Reach

    assert scope_note(Reach(everything=True)) == {"scope": "programme"}
    assert scope_note(Reach(everything=False)) == {"scope": "none"}
    narrowed = scope_note(Reach(everything=False, departments=frozenset({"d1"})))
    assert narrowed == {"scope": "narrowed", "departments": ["d1"]}
