"""B8.5 — the weekly series, the KPI strip and mentor-load pagination.

THE PROPERTY RUNNING THROUGH ALL OF IT is the one 07 §5 states and
`app/scope_views.py` restates: a number nobody measured must not render as a
number somebody measured. An analytics screen is where that failure is most
expensive, because the office ACTS on it — a flat zero line across a term reads
as a collapse and not as "no import has been run".

So every point, every KPI value and every delta here is nullable, and each
series carries the reason it is what it is. The tests below pin, in order:

1. `test_the_series_answers_and_is_shaped_by_the_window` — it exists at all, and
   `?weeks=` is bounded.
2. `test_a_week_with_no_sessions_is_null_and_not_zero` — the guardrail.
   Attendance is NULL for a week nobody recorded a session in; skilling hours
   are 0.0 for a week nobody logged, because those are opposite facts (nobody
   RECORDS a session that did not happen; a self-report of nothing IS a
   measurement of the cohort).
3. `test_readiness_has_no_history_and_says_so` — the honest half of B8.5. There
   is no record of what a student's CGPA was in week 7, so that series is
   `partial` with only the last point filled and a sentence explaining it,
   rather than a fabricated line or a silent absence.
4. `test_the_mock_interview_kpi_refuses_to_invent_a_definition` — cross-area
   discipline: the one definition of a mock interview is B6.3's, and a second
   one written here would disagree with the interview records screen the first
   time either moved.
5. `test_a_scope_over_no_students_draws_no_line` and
   `test_a_reach_of_nothing_says_so_differently` — no line over nobody, the
   legend and the KPI strip keep their shape, and "you may see nothing"
   does not read the same as "there is nothing".
6. `test_both_endpoints_are_scoped_and_stamp_the_header` — they are new members
   of B1.4's family and must behave like the rest of it.
7. `test_mentor_load_pages_without_changing_its_shape` — pagination is opt-in
   and stated in headers, because four Angular screens are built against
   `list[MentorLoadOut]`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from conftest import requires_db

from app.db import SessionLocal
from app.models.attendance import AttendanceRecord
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.user import Role, Student, User
from app.seed_roster import SSO_ONLY_PASSWORD_HASH


@pytest.fixture
def cohort_with_attendance():
    """A batch, one student, and attendance recorded in ONE of the last six
    weeks. The other five weeks are the state this module is about: not zero
    attendance, no attendance recorded."""
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        college = College(code=f"AS{tag.upper()}", name="Series College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        department = Department(college_id=college.id, name="Series", code=f"SD{tag}")
        db.add(department)
        db.flush()
        cohort = Cohort(
            code=f"SER-{tag}",
            name="Series batch",
            batch_label="2026-28",
            degree_level=DegreeLevel.PG,
            department_id=department.id,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        db.add(cohort)
        db.flush()
        user = User(
            email=f"series-{tag}@series.test",
            name=f"Series {tag}",
            role=Role.STUDENT,
            password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        db.add(user)
        db.flush()
        student = Student(user_id=user.id, usn=f"SER{tag.upper()}", cohort_id=cohort.id)
        db.add(student)
        db.flush()
        made.update(
            college=college.id,
            department=department.id,
            cohort=cohort.id,
            student=student.id,
            user=user.id,
        )
        # THIS week only: 3 of 4 sessions.
        at = datetime.now(timezone.utc) - timedelta(days=1)
        db.add_all(
            [
                AttendanceRecord(
                    student_id=student.id,
                    course_code="22MBA11",
                    session_no=n,
                    session_date=at,
                    present=(n != 4),
                )
                for n in range(1, 5)
            ]
        )
        db.commit()
    yield made
    with SessionLocal() as db:
        db.execute(delete(AttendanceRecord).where(AttendanceRecord.student_id == made["student"]))
        db.execute(delete(Student).where(Student.id == made["student"]))
        db.execute(delete(User).where(User.id == made["user"]))
        db.execute(delete(Cohort).where(Cohort.id == made["cohort"]))
        db.execute(delete(Department).where(Department.id == made["department"]))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


@pytest.fixture
def scoped_grant():
    """A grant hung on one rung of the spine, and removed afterwards.

    A ROW RATHER THAN `POST /api/admin/governance/grants`, for the reason
    test_scoped_lists.py and test_exports.py both give: a fixture should not
    depend on who is granting or on what the key carries. A deputy's grant of
    a `carries_pii` key lands `pending_approval` under B2.4 and holds NOTHING
    until a different `admin.governance` holder approves it (the Main Admin's
    is live at once) — and a test that turned on that would pass for the wrong
    reason (an empty series because the grant is inert, read as an empty
    series because the scope is narrow).

    Removed afterwards, and that is not tidiness: a grant is keyed on a user id
    and OUTLIVES the throwaway account that named it, so a leaked row silently
    widens whatever runs next.
    """
    made: list[str] = []

    def _grant(user_id: str, capability: str, level: ScopeLevel, scope_id: str) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=capability,
                subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                scope_level=level,
                scope_id=scope_id,
                reason="the analytics series tests need a scoped grant, twenty plus",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


def _series_of(body: dict, key: str) -> dict:
    return next(s for s in body["series"] if s["key"] == key)


def _kpi_of(body: dict, key: str) -> dict:
    return next(k for k in body["kpis"] if k["key"] == key)


@requires_db
def test_the_series_answers_and_is_shaped_by_the_window(client, make_user):
    admin = make_user("series-admin", Role.ADMIN)
    r = client.get("/api/admin/analytics/series?weeks=4", headers=admin.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["weeks"]) == 4
    assert {s["key"] for s in body["series"]} == {
        "attendance_pct", "skilling_hours", "offers", "readiness_pct"
    }
    for series in body["series"]:
        assert len(series["points"]) == 4, f"{series['key']} is not aligned to the weeks"

    # `?weeks=` is a query parameter, so it is bounded — an unbounded one is a
    # full scan of `attendance_records` a typo can ask for.
    capped = client.get("/api/admin/analytics/series?weeks=9999", headers=admin.headers)
    assert capped.status_code == 200, capped.text
    assert len(capped.json()["weeks"]) == 52
    floored = client.get("/api/admin/analytics/series?weeks=0", headers=admin.headers)
    assert len(floored.json()["weeks"]) == 1


@requires_db
def test_a_week_with_no_sessions_is_null_and_not_zero(
    client, make_user, cohort_with_attendance, scoped_grant
):
    """THE guardrail. A flat zero attendance line across a term reads as a
    collapse; the truth is that nobody has imported anything for those weeks."""
    faculty = make_user(f"series-{cohort_with_attendance['tag']}", Role.MENTOR)
    scoped_grant(
        faculty.user_id, "admin.analytics", ScopeLevel.COHORT, cohort_with_attendance["cohort"]
    )

    r = client.get("/api/admin/analytics/series?weeks=6", headers=faculty.headers)
    assert r.status_code == 200, r.text
    body = r.json()

    attendance = _series_of(body, "attendance_pct")
    assert attendance["source"] == "live"
    assert attendance["points"][-1] == 75.0, "this week's real attendance is missing"
    assert attendance["points"][:-1] == [None] * 5, (
        "a week with no sessions recorded was drawn as 0% attendance — that is "
        "a collapse the office would act on, and it did not happen"
    )

    # Skilling hours are the OPPOSITE case and must not be "fixed" to match:
    # the ledger is a self-report, so a week nobody logged is a measurement of
    # the cohort rather than an absence of one.
    hours = _series_of(body, "skilling_hours")
    assert all(p is not None for p in hours["points"])
    assert hours["points"] == [0.0] * 6


@requires_db
def test_readiness_has_no_history_and_says_so(client, make_user):
    """Readiness is computed from a student's records AS THEY STAND TODAY, so
    week 7's value is not recoverable from any table. The series says `partial`
    and carries the sentence; it does not draw a line it cannot justify, and it
    does not read `analytics_snapshots`, which nothing writes yet."""
    admin = make_user("series-readiness", Role.ADMIN)
    body = client.get("/api/admin/analytics/series?weeks=6", headers=admin.headers).json()
    readiness = _series_of(body, "readiness_pct")

    assert readiness["source"] in {"partial", "unavailable"}
    assert readiness["note"], "the reason the line is empty must travel with it"
    assert readiness["points"][:-1] == [None] * 5, (
        "a readiness history was drawn for weeks whose inputs are not recorded"
    )


@requires_db
def test_the_mock_interview_kpi_refuses_to_invent_a_definition(client, make_user):
    """Cross-area discipline. "Mock interviews" has ONE definition and it is
    B6.3's (`interview_sessions` plus the legacy `mock_attempts`, distinguished
    by source). A second one written here would disagree with the interview
    records screen the first time either moved — so this reports null WITH the
    reason rather than a number nobody else would reproduce."""
    admin = make_user("kpi-mocks", Role.ADMIN)
    body = client.get("/api/admin/analytics/kpis", headers=admin.headers).json()
    mocks = _kpi_of(body, "mock_interviews")

    assert mocks["value"] is None
    assert mocks["note"] and "B6.3" in mocks["note"]


@requires_db
def test_a_kpi_with_no_comparison_period_has_no_delta(client, make_user):
    """A delta is a promise that two numbers were measured the same way. Three
    of these are "how many right now" and nothing records what they were
    twelve weeks ago; a green arrow drawn from an absence is a lie in a glyph."""
    admin = make_user("kpi-deltas", Role.ADMIN)
    body = client.get("/api/admin/analytics/kpis?weeks=12", headers=admin.headers).json()

    assert body["weeks"] == 12
    for key in ("placement_ready_pct", "pending_approvals", "mock_interviews"):
        kpi = _kpi_of(body, key)
        assert kpi["delta"] is None, f"{key} claims a delta it cannot have"
        assert kpi["previous"] is None
        assert kpi["note"], f"{key} is dashed or uncompared with no reason given"

    # And the queue depth IS reported, because it is answerable now.
    assert _kpi_of(body, "pending_approvals")["value"] is not None


@requires_db
def test_a_scope_over_no_students_draws_no_line(client, make_user, scoped_grant):
    """No line is drawn over nobody, and the legend does not disappear.

    A grant pointing at a department that has since been deleted is the real
    shape of this: the reach is NARROWED (it names a rung) and resolves to no
    students. Four flat zero lines there would be the widest possible failure of
    the narrowest possible grant — an attendance collapse and a placement
    drought drawn over a college, from an empty set.
    """
    faculty = make_user("series-nothing", Role.MENTOR)
    scoped_grant(
        faculty.user_id, "admin.analytics", ScopeLevel.DEPARTMENT, "a-department-that-is-gone"
    )

    series = client.get("/api/admin/analytics/series", headers=faculty.headers)
    assert series.status_code == 200, series.text
    body = series.json()
    assert body["students_in_reach"] == 0
    for line in body["series"]:
        assert line["source"] == "unavailable", f"{line['key']} drew a line over nothing"
        assert all(p is None for p in line["points"])
        assert line["note"]
    # THE LEGEND KEEPS ITS SHAPE: all four series come back, dashed. A response
    # with no series at all reads as a screen that failed to load.
    assert [line["key"] for line in body["series"]] == [
        "attendance_pct", "skilling_hours", "offers", "readiness_pct"
    ]

    kpis = client.get("/api/admin/analytics/kpis", headers=faculty.headers)
    strip = kpis.json()["kpis"]
    assert [k["key"] for k in strip] == [
        "placement_rate", "median_ctc", "highest_ctc", "placement_ready_pct",
        "attendance_avg", "mock_interviews", "pending_approvals",
    ]
    assert all(k["value"] is None and k["note"] for k in strip)


@requires_db
def test_a_reach_of_nothing_says_so_differently(client, make_user, monkeypatch):
    """`policies.Reach.nothing` — "you may see nothing" — must not read the same
    as "there is nothing".

    They are opposite facts (`app/scope_views.py` states it), and the difference
    is the whole reason `scope_note` has three words rather than a boolean. A
    `none` reach rendering as an empty chart tells the office there is no work
    rather than that they cannot see it.

    Reached by substituting the reach rather than by building a grant, because
    with `admin.analytics` there is no grant shape that produces it: every
    scoped grant names a rung, which makes the reach NARROWED even when the rung
    is gone. The branch is still live — a capability held as a derived FUNCTION
    resolves to `nothing` (see `tests/test_scoped_lists.py`) — and it is the
    branch that stops a future one drawing zeros.
    """
    from app.policies import Reach
    from app.routers import console

    admin = make_user("series-none", Role.ADMIN)
    monkeypatch.setattr(console, "scope_filter", lambda *a, **k: Reach(everything=False))

    series = client.get("/api/admin/analytics/series", headers=admin.headers)
    assert series.status_code == 200, series.text
    assert series.headers["X-Reep-Scope"] == "none"
    notes = {line["note"] for line in series.json()["series"]}
    assert notes == {"Your access does not reach any students, so there is nothing to plot."}
    assert all(line["source"] == "unavailable" for line in series.json()["series"])


@requires_db
def test_both_endpoints_are_scoped_and_stamp_the_header(
    client, make_user, cohort_with_attendance, scoped_grant
):
    """New members of B1.4's family. A student is refused outright; a scoped
    holder gets `narrowed`; the Main Admin gets `programme`."""
    student = make_user("series-student", Role.STUDENT)
    for url in ("/api/admin/analytics/series", "/api/admin/analytics/kpis"):
        refused = client.get(url, headers=student.headers)
        assert refused.status_code == 403, f"{url} answered a STUDENT: {refused.text}"

    faculty = make_user(f"series-hdr-{cohort_with_attendance['tag']}", Role.MENTOR)
    scoped_grant(
        faculty.user_id, "admin.analytics", ScopeLevel.COLLEGE, cohort_with_attendance["college"]
    )
    admin = make_user("series-hdr-admin", Role.ADMIN)
    for url in ("/api/admin/analytics/series", "/api/admin/analytics/kpis"):
        narrowed = client.get(url, headers=faculty.headers)
        assert narrowed.status_code == 200, narrowed.text
        assert narrowed.headers["X-Reep-Scope"] == "narrowed"
        assert narrowed.headers["X-Reep-Scope-Colleges"] == cohort_with_attendance["college"]
        wide = client.get(url, headers=admin.headers)
        assert wide.headers["X-Reep-Scope"] == "programme"

    # And the narrowing is real, not just declared: the scoped holder counts
    # one student, the office counts the deployment.
    scoped_body = client.get("/api/admin/analytics/series", headers=faculty.headers).json()
    admin_body = client.get("/api/admin/analytics/series", headers=admin.headers).json()
    assert scoped_body["students_in_reach"] == 1
    assert admin_body["students_in_reach"] > scoped_body["students_in_reach"]


@requires_db
def test_mentor_load_pages_without_changing_its_shape(client, make_user):
    """Paging is OPT-IN and stated in headers.

    Four Angular screens read this endpoint and every one is built against a
    bare array; an envelope would break all four, and a default page size would
    silently truncate three of them, which do their own filtering over the whole
    set and have no paging control to reach row 51 with.
    """
    admin = make_user("mentor-load-page", Role.ADMIN)
    # Two faculty accounts of our own, so the property is exercised rather than
    # skipped on a database that happens to have one mentor.
    make_user("mentor-load-page-a", Role.MENTOR)
    make_user("mentor-load-page-b", Role.MENTOR)

    everything = client.get("/api/admin/mentor-load", headers=admin.headers)
    assert everything.status_code == 200, everything.text
    rows = everything.json()
    assert isinstance(rows, list)
    total = int(everything.headers["X-Reep-Total"])
    assert total == len(rows), "the unpaged response is not the whole set"
    # Unpaged means no page headers at all, so a client cannot read a page of
    # one as the complete list.
    assert "X-Reep-Page" not in everything.headers

    first = client.get("/api/admin/mentor-load?page_size=1", headers=admin.headers)
    second = client.get("/api/admin/mentor-load?page=2&page_size=1", headers=admin.headers)
    assert len(first.json()) == 1 and len(second.json()) == 1
    assert first.headers["X-Reep-Page"] == "1"
    assert first.headers["X-Reep-Page-Size"] == "1"
    assert int(first.headers["X-Reep-Total"]) == total, (
        "the total must be the whole set, not the page — it is what a paging "
        "control counts its pages from"
    )
    assert first.json()[0]["user_id"] != second.json()[0]["user_id"]
    # The page is a window over a STABLE sort. `users.name` is not unique, so
    # without the id tiebreak a duplicate name drops one row off page 2 and
    # repeats the other, with nothing on screen saying so.
    assert [r["user_id"] for r in first.json() + second.json()] == [
        r["user_id"] for r in rows[:2]
    ]

    # A page beyond the end is empty and honest, not a 404.
    over = client.get(
        f"/api/admin/mentor-load?page={total + 5}&page_size=1", headers=admin.headers
    )
    assert over.status_code == 200
    assert over.json() == []
    assert int(over.headers["X-Reep-Total"]) == total


@requires_db
def test_the_rollup_reads_the_students_own_readiness_rule(client, make_user):
    """B8.5's "placement ready %" is `compose_readiness_many`, which is
    `build_readiness` over a batch read — the SAME function the student's own
    card calls. A SQL re-implementation would be a second definition of
    readiness that disagrees with the student's screen the first time either is
    edited, which is the failure `routers/mentee_records.py` already warns about.
    """
    import inspect

    from app.routers import console

    source = inspect.getsource(console.analytics_series) + inspect.getsource(
        console.analytics_kpis
    )
    assert "compose_readiness_many" in source, (
        "the readiness roll-up no longer goes through the student's own rule"
    )
    admin = make_user("rollup-admin", Role.ADMIN)
    body = client.get("/api/admin/analytics/kpis", headers=admin.headers).json()
    ready = _kpi_of(body, "placement_ready_pct")
    # Either a percentage or a stated reason — never a bare zero.
    assert ready["value"] is not None or ready["note"]
    if ready["value"] is None:
        assert "record" in ready["note"] or "reach" in ready["note"]
