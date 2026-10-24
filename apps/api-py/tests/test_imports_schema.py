"""B8.1/B8.4/B8.6 — the import tables, their verdicts, and the one gate on them.

This module is the schema round's guard set. What each test holds down, and what
comes back if it is deleted:

1. THE VERDICTS ARE VALUES, NOT JUST KEYS. `test_purge_people.py` and
   `test_purge_students.py` already fail when a new table has NO verdict. They
   do not care WHICH. `test_an_import_is_destroyed_with_the_people_in_it` is
   what stops `import_rows` being flipped to KEEP by somebody who reads
   "staff-authored receipt" on the run and does not open the rows — which would
   leave a verbatim copy of every USN, name and mark of a purged cohort behind,
   in a table nobody thinks of as student data.

2. THE JOB-IMPORT DELETE IS COMPLETE. `test_the_job_import_machinery_is_gone`
   checks the table, the column and both verdict dicts together. B8.4 is the one
   task in this area that removes rather than adds, and a half-done delete — the
   table gone, a verdict left naming it — aborts BOTH destructors at the moment
   an operator is trying to hand a deployment over.

3. THE KEY AND ITS CALL SITE SHIP TOGETHER. `test_admin_imports_is_enforced_and_
   admin_interviews_does_not_exist_yet` is the catalogue rule written as a test
   in both directions: `admin.imports` is in `CAPABILITIES`, carries PII, is in
   the college-admin set and refuses a faculty account that does not hold it;
   `admin.interviews` is in NEITHER the catalogue nor that set, because
   `COLLEGE_ADMIN_CAPABILITIES` is read through `CAPABILITIES_BY_KEY[key]` and a
   name in one without the other is a KeyError in front of whoever is appointing
   a college admin.

4. THE HISTORY IS NARROWED, AND AN EMPTY REACH IS NOT AN UNRESTRICTED ONE. The
   four scope tests are B1.4's sentence applied to a list that is not keyed on
   students: an import run names a BATCH, so
   `scope_views.import_run_scope_clause` reaches the four middle rungs through
   `cohorts`. Delete them and a college-scoped holder reads another college's
   marks files. The `nothing` branch is asserted as a CLAUSE rather than over
   HTTP, because the grants API refuses to create the grant that produces it —
   which is precisely why that branch would otherwise never be exercised.

5. A RECEIPT DOES NOT BLOCK A BATCH DELETE.
   `test_an_empty_batch_can_still_be_deleted_after_an_import` pins the one place
   `import_runs` departs from the spine's no-ondelete convention, and it is the
   kind of decision that gets "tidied" back: without SET NULL,
   `DELETE /api/admin/cohorts/{id}` answers a 500 on a foreign key for any batch
   that was ever imported into — over a receipt, on the one delete the console
   already guards with a 409 and a sentence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import Base, SessionLocal
from app.models.cohort import Cohort
from app.models.data_import import ImportRun, ImportRow
from app.models.governance import CAPABILITIES_BY_KEY, CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.user import Role
from app.purge_people import EMPTY, KEEP, VERDICTS
from app.purge_students import ALL, STUDENT_VERDICTS
from app.routers.admin import COLLEGE_ADMIN_CAPABILITIES


# ------------------------------------------------------------ the verdicts --


@requires_db
def test_an_import_is_destroyed_with_the_people_in_it() -> None:
    """Both destructors take the runs AND their rows, and the snapshot too.

    The argument for each, so a future editor can disagree with the reason
    rather than with the word:

    `import_runs` is staff-authored — a filename, a batch and who read it — and
    the tempting verdict is KEEP in `purge_people` and a `by_user` scope in
    `purge_students`, filing it with `export_events` as an act of the office.
    That is wrong about what is UNDER it. `import_rows` holds the spreadsheet
    verbatim: a USN, a name, marks or attendance, per line. A kept import
    history is therefore a complete second copy of the academic records the
    purge has just deleted. And in `purge_students` a `by_user` scope would
    match NO run at all — no student has ever read a file — so the whole history
    would survive a pass whose entire claim is that it knows who a row is about.

    `analytics_snapshots` is derived from the people being removed. Keeping it
    leaves "41 students placed" on the Analytics screen of a deployment with no
    students in it, and the next scheduled run rewrites the week anyway.

    The office's record that an import happened survives where the office's acts
    belong: `redesign_audit_events`, which is KEEP in both.
    """
    for table in ("import_runs", "import_rows", "analytics_snapshots"):
        assert VERDICTS[table] == EMPTY, f"purge_people must empty {table}"
        assert STUDENT_VERDICTS[table] == ALL, f"purge_students must empty {table}"


@requires_db
def test_the_job_import_machinery_is_gone() -> None:
    """B8.4, in all four places it had to be removed from at once.

    The table, the FOREIGN KEY COLUMN 04-backend-changes.md did not mention, and
    both verdict dicts. `check_verdicts` in either destructor refuses to run
    while a verdict names a table that no longer exists, so a leftover key here
    is not a tidiness problem — it is a purge that aborts on a production
    console.
    """
    assert "job_import_runs" not in Base.metadata.tables
    assert "import_run_id" not in Base.metadata.tables["jobs"].columns
    assert "job_import_runs" not in VERDICTS
    assert "job_import_runs" not in STUDENT_VERDICTS
    # And the replacement is classified, rather than the delete having simply
    # reduced the table count.
    assert VERDICTS["jobs"] == KEEP and STUDENT_VERDICTS["jobs"] == KEEP


# ----------------------------------------------------------- the capability --


@requires_db
def test_both_new_keys_are_catalogued_and_enforced(client, make_user) -> None:
    """One key landed with its call site in 4b; the other landed with 4c's.

    This test was written while `admin.interviews` did not exist, and it asserted
    that — with the rule that matters spelled out beside it: the key must be
    absent from BOTH the catalogue and `COLLEGE_ADMIN_CAPABILITIES` or present
    in BOTH, because the appointment endpoint reads `CAPABILITIES_BY_KEY[key]`
    for every name in that tuple and a missing one is a KeyError in front of
    whoever is appointing a college admin. 4c added it to both, in one commit,
    with `require_capability` call sites in
    `app/routers/interview_policy.py` — so the assertion flips and the RULE is
    unchanged.

    `tools/ci/check_capability_enforcement.py` proves enforcement statically.
    This proves the half a static walk cannot: that the gate actually refuses
    somebody, and that the college-admin set names no key the catalogue lacks.
    """
    cap = CAPABILITIES_BY_KEY["admin.imports"]
    assert cap.carries_pii, (
        "A preview lists every student in a batch by USN with their marks beside "
        "it. Dropping this flag skips B2.4's second signature on the most "
        "concentrated view of student records the console has."
    )
    assert "admin.imports" in COLLEGE_ADMIN_CAPABILITIES

    # 4c's key, now in both. `carries_pii` for the same reason
    # `admin.imports` carries it: what hangs off this key names students — the
    # cap reset names one, the records grid names a cohort with their scores
    # beside them.
    assert CAPABILITIES_BY_KEY["admin.interviews"].carries_pii
    assert "admin.interviews" in COLLEGE_ADMIN_CAPABILITIES
    assert set(COLLEGE_ADMIN_CAPABILITIES) <= set(CAPABILITIES_BY_KEY)

    faculty = make_user(f"imp-nokey-{uuid.uuid4().hex[:6]}", Role.MENTOR)
    r = client.get("/api/admin/imports", headers=faculty.headers)
    assert r.status_code == 403, r.text
    assert "Data imports" in r.json()["detail"]


# --------------------------------------------------------------- the scope --


@pytest.fixture
def scoped_grant():
    """One capability, one rung, taken back afterwards.

    A row rather than `POST /api/admin/governance/grants`, for the reason
    test_scoped_lists.py gives: `admin.imports` carries PII, so a deputy's
    API-made grant lands `pending_approval` and holds nothing while the Main
    Admin's is live at once — a fixture that depended on who granted would
    make every test below about the approval rule instead.
    """
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel | None, target_id: str | None) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key,
                subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                scope_level=level,
                scope_id=target_id,
                reason="the import-history scope tests need a scoped grant, twenty plus",
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
def two_colleges_with_imports():
    """Two colleges, a department and a batch in each, and one import run each.

    The smallest shape in which the fence can fail in both directions: a
    college-scoped holder must see one run, and a department-scoped one must
    reach the same run THROUGH the batch, which is the join
    `import_run_scope_clause` makes and the only part of it a single-college
    fixture would never exercise.
    """
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        for side in ("here", "far"):
            college = College(code=f"IMP{side[:1].upper()}{tag}", name=f"Imports {side} {tag}",
                              status=STATUS_ACTIVE)
            db.add(college)
            db.flush()
            dept = Department(college_id=college.id, code=f"D{side[:1].upper()}{tag}",
                              name=f"Dept {side} {tag}")
            db.add(dept)
            db.flush()
            cohort = Cohort(code=f"IMP-{side}-{tag}", name=f"Batch {side} {tag}",
                            batch_label="2024-26", department_id=dept.id,
                            degree_level=DegreeLevel.PG,
                            start_date=datetime(2024, 8, 1, tzinfo=timezone.utc),
                            end_date=datetime(2026, 7, 31, tzinfo=timezone.utc))
            db.add(cohort)
            db.flush()
            run = ImportRun(kind="marks", status="previewed", college_id=college.id,
                            cohort_id=cohort.id, semester=1, filename=f"{side}.xlsx",
                            rows_total=1, rows_ok=1)
            db.add(run)
            db.flush()
            db.add(ImportRow(run_id=run.id, line_no=2, usn=f"USN{side}{tag}", verdict="ok",
                             payload={"sgpa": 8.1}))
            made[f"college_{side}"] = college.id
            made[f"dept_{side}"] = dept.id
            made[f"cohort_{side}"] = cohort.id
            made[f"run_{side}"] = run.id
        db.commit()

    yield made

    with SessionLocal() as db:
        db.execute(delete(ImportRow).where(
            ImportRow.run_id.in_([made["run_here"], made["run_far"]])
        ))
        db.execute(delete(ImportRun).where(
            ImportRun.id.in_([made["run_here"], made["run_far"]])
        ))
        db.execute(delete(Cohort).where(
            Cohort.id.in_([made["cohort_here"], made["cohort_far"]])
        ))
        db.execute(delete(Department).where(
            Department.id.in_([made["dept_here"], made["dept_far"]])
        ))
        db.execute(delete(College).where(
            College.id.in_([made["college_here"], made["college_far"]])
        ))
        db.commit()


@requires_db
@pytest.mark.parametrize("rung", ["college", "department"])
def test_a_scoped_holder_sees_only_their_own_imports(
    client, make_user, two_colleges_with_imports, scoped_grant, rung
) -> None:
    """A file of marks belongs to the batch it was read for, and to nobody else.

    Both rungs are asserted because they take different paths through
    `import_run_scope_clause`: the college matches `import_runs.college_id`
    directly, the department only through a subquery over `cohorts`. A clause
    that dropped the second would leave a department-scoped college admin with
    an empty history and no sentence explaining it.
    """
    spine = two_colleges_with_imports
    faculty = make_user(f"imp-{rung}-{spine['tag']}", Role.MENTOR)
    level = ScopeLevel.COLLEGE if rung == "college" else ScopeLevel.DEPARTMENT
    target = spine["college_here"] if rung == "college" else spine["dept_here"]
    scoped_grant(faculty.user_id, "admin.imports", level, target)

    r = client.get("/api/admin/imports", headers=faculty.headers)
    assert r.status_code == 200, r.text
    seen = {row["id"] for row in r.json()}
    assert spine["run_here"] in seen
    assert spine["run_far"] not in seen, "the other college's import file leaked"
    assert r.headers["X-Reep-Scope"] == "narrowed"


@requires_db
def test_a_grant_pointing_at_a_deleted_college_lists_nothing(
    client, make_user, two_colleges_with_imports, scoped_grant
) -> None:
    """An empty reach is not an unrestricted one.

    A grant naming a rung that has since been deleted matches no run. If that
    collapsed into "no restriction" — the `if not clauses` branch falling
    through to an empty WHERE — the narrowest grant on the deployment would read
    every marks file on it. The header still reads `narrowed`, which is true:
    this holder IS narrowed, to a college that is not there any more.
    """
    spine = two_colleges_with_imports
    faculty = make_user(f"imp-gone-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.imports", ScopeLevel.COLLEGE, "a-college-that-is-gone")

    r = client.get("/api/admin/imports", headers=faculty.headers)
    assert r.status_code == 200, r.text
    assert r.json() == []
    assert r.headers["X-Reep-Scope"] == "narrowed"


def test_a_reach_of_nothing_is_a_false_clause_and_not_an_empty_one() -> None:
    """The `none` end of the scale, asserted where it can actually be reached.

    `Reach.nothing` is a real state — a holder whose only grant names a rung at
    a level the reach could not resolve — and it must compile to a predicate
    that matches NO row, never to an absent WHERE. It is checked here as a
    clause rather than over HTTP because the API refuses to CREATE a grant that
    produces it (`scope_level` and `scope_id` are both-or-neither), which is
    exactly why the branch needs a test of its own: nothing else exercises it,
    and the day something does it will be a fence that is open.
    """
    from app.policies import Reach
    from app.scope_views import import_run_scope_clause

    reach = Reach(everything=False)
    assert reach.nothing
    clause = import_run_scope_clause(reach)
    assert str(clause.compile(compile_kwargs={"literal_binds": True})) == "false"


@requires_db
def test_the_main_admin_is_not_narrowed(
    client, make_user, two_colleges_with_imports
) -> None:
    """The office's job is the programme, and `everything` must select rows.

    `Reach.everything` used to fall through to the no-clause branch and select
    NOTHING, which on this screen would read as "no imports have ever been run"
    on the account that holds every capability.
    """
    spine = two_colleges_with_imports
    admin = make_user(f"imp-admin-{spine['tag']}", Role.ADMIN)
    r = client.get("/api/admin/imports", headers=admin.headers)
    assert r.status_code == 200, r.text
    seen = {row["id"] for row in r.json()}
    assert {spine["run_here"], spine["run_far"]} <= seen
    assert r.headers["X-Reep-Scope"] == "programme"


# ------------------------------------------------- the one ondelete departure --


@requires_db
def test_an_empty_batch_can_still_be_deleted_after_an_import(
    client, make_user, two_colleges_with_imports
) -> None:
    """`import_runs.cohort_id` is SET NULL, and this is why.

    Every other pointer into the spine in this schema has NO ondelete, so the
    database refuses to delete a rung with rows under it. That is right for a
    college, which is archived rather than deleted — and wrong here, because
    `DELETE /api/admin/cohorts/{id}` is a real console action that already
    refuses with a 409 and a sentence when students are seated. A batch that is
    empty but was once imported into would otherwise fail on a foreign key: a
    500, over a receipt, on a button whose only documented refusal is about
    people.

    The run survives the batch with its filename, its counts and its rows, and
    stops naming a batch — which is why `import_run_scope_clause` shows a
    NULL-batch run only to a reach of everything.
    """
    spine = two_colleges_with_imports
    admin = make_user(f"imp-del-{spine['tag']}", Role.ADMIN)

    r = client.delete(f"/api/admin/cohorts/{spine['cohort_far']}", headers=admin.headers)
    assert r.status_code == 204, r.text

    with SessionLocal() as db:
        run = db.get(ImportRun, spine["run_far"])
        assert run is not None, "the receipt was deleted with the batch"
        assert run.cohort_id is None
        assert run.filename == "far.xlsx"
        assert db.scalar(
            select(ImportRow).where(ImportRow.run_id == spine["run_far"])
        ) is not None, "the lines went with the batch pointer"
