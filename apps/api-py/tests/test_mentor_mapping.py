"""B9.2 / B9.3 / B9.4 — the two fences on the bulk paths, and the filters.

THE FIRST TEST HERE IS ABOUT A DEFECT THAT WAS LIVE BEFORE PHASE 4, not about a
new feature. `POST /admin/students/{id}/mentor` has refused a cross-college pair
with a 422 since B1.5 (`_assert_same_college`) and has asked
`require_capability(..., target=ancestry_of_user(...))` since B1.2. The OTHER
TWO WRITERS of `students.mentor_id` — `PATCH /admin/students/{id}` with
`mentor_user_id`, and the batch bar's `action="mentor"` — set the same column
and asked neither. `mentor_id` is rule 2's scope key, so the roster editor was a
way to hand a student to a faculty member in another college, and the batch bar
was a way to do it thirty at a time, on screens nobody thinks of as the
assignment screen.

That is the shape of bug this module exists to catch: one rule, enforced on the
path somebody was thinking about, and absent on the two that write the same
column. The tests are written per PATH rather than per rule for exactly that
reason.

The rest is B9.4's filters (`?cohort_id=`, `?q=`, which narrow within a reach
and can never widen it) and H2's capacity, which is now settable per department
and STILL ENFORCES NOTHING — that last assertion is the one somebody will be
tempted to delete.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from conftest import requires_db

# The two-college spine and its helpers, by name — the same import style
# test_admin_students.py uses for test_admin_institution's fixtures. A second
# copy of a fixture that builds colleges, departments, batches and students is
# a second definition of what "another college" means.
from test_scoped_lists import (  # noqa: F401 - fixtures by name
    _file_under,
    _undo,
    spine,
)

from app.db import SessionLocal
from app.models.mentor_assignment import MentorAssignment
from app.models.redesign import AuditEvent
from app.models.user import Role, Student, User

ADMIN_API = "/api/admin"
REASON = "Seating the 2026 intake with their project guides."


def _spells(student_id: str) -> list[MentorAssignment]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(MentorAssignment)
                .where(MentorAssignment.student_id == student_id)
                .order_by(MentorAssignment.created_at)
            ).all()
        )


def _mentor_of(student_id: str) -> str | None:
    with SessionLocal() as db:
        return db.scalar(select(Student.mentor_id).where(Student.id == student_id))


# ------------------------------------------------- the two bulk writers --


@requires_db
def test_the_roster_editor_applies_the_same_college_fence_as_the_assignment_screen(
    client, make_user, spine
):
    """B1.5 ON `PATCH /admin/students/{id}`. A LIVE DEFECT, NOT A NEW RULE.

    This endpoint sets `students.mentor_id` — rule 2's scope key — from a form
    of many fields, and it did it with no college check at all while the
    assignment screen three clicks away refused the same pair with a 422. The
    fix imports the one implementation rather than restating it: two copies of a
    college rule disagree the first time one of them is corrected.
    """
    admin = make_user("mm-patch-adm", Role.ADMIN)
    outsider = make_user("mm-patch-out", Role.MENTOR)
    insider = make_user("mm-patch-in", Role.MENTOR)
    _file_under(outsider.user_id, spine["far"])
    _file_under(insider.user_id, spine["here"])
    sid = spine["student_here"]
    try:
        r = client.patch(
            f"{ADMIN_API}/students/{sid}",
            headers=admin.headers,
            json={"mentor_user_id": outsider.user_id},
        )
        assert r.status_code == 422, r.text
        assert "college" in r.json()["detail"].lower()
        assert _mentor_of(sid) is None, "a refused edit still moved the student"
        assert _spells(sid) == [], "a refused edit wrote history"

        # The same edit with a faculty member inside the college is allowed, and
        # it writes the spell — the fence refuses the pair, not the path.
        ok = client.patch(
            f"{ADMIN_API}/students/{sid}",
            headers=admin.headers,
            json={"mentor_user_id": insider.user_id},
        )
        assert ok.status_code == 200, ok.text
        rows = _spells(sid)
        assert len(rows) == 1 and rows[0].to_at is None
        # NO REASON ON THIS PATH, and the NULL is the honest record of that: the
        # roster editor is a form of many fields and does not ask for one. The
        # audit row carries the whole patch, which is what a reader of this path
        # is looking for.
        assert rows[0].reason is None
    finally:
        _undo([sid], [outsider.user_id, insider.user_id])


@requires_db
def test_the_batch_bar_applies_both_fences_and_writes_history_per_student(
    client, make_user, spine
):
    """B9.3. The same two fences, and a spell PER STUDENT under ONE audit row.

    The batch action is the single action repeated, so the college check runs
    for every pair and the capability target once for the faculty member — and
    the whole batch is refused rather than half-applied, matching the
    all-or-nothing reach check the endpoint already performs.

    HISTORY IS PER STUDENT EVEN THOUGH THE AUDIT ROW IS PER BATCH, and the two
    really are different records. A spell is a fact about ONE student's mentor,
    read on that student's own card; a batch-shaped row there would be a record
    nobody could render. The audit trail keeps the batch shape because that is
    the act the office performed.
    """
    admin = make_user("mm-batch-adm", Role.ADMIN)
    outsider = make_user("mm-batch-out", Role.MENTOR)
    insider = make_user("mm-batch-in", Role.MENTOR)
    _file_under(outsider.user_id, spine["far"])
    _file_under(insider.user_id, spine["here"])
    sid = spine["student_here"]
    bulk = f"{ADMIN_API}/cohorts/{spine['batch_here']}/students/bulk"
    try:
        refused = client.post(
            bulk,
            headers=admin.headers,
            json={"action": "mentor", "mentor_user_id": outsider.user_id, "reason": REASON},
        )
        assert refused.status_code == 422, refused.text
        assert "college" in refused.json()["detail"].lower()
        assert _mentor_of(sid) is None, (
            "the batch bar created exactly the cross-college pair the single "
            "assignment endpoint refuses"
        )
        assert _spells(sid) == []

        ok = client.post(
            bulk,
            headers=admin.headers,
            json={"action": "mentor", "mentor_user_id": insider.user_id, "reason": REASON},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["affected"] == 1
        rows = _spells(sid)
        assert len(rows) == 1 and rows[0].to_at is None and rows[0].kind == "assign"
        assert rows[0].reason == REASON, "the batch's reason did not reach the spell"
        assert rows[0].by_user_id == admin.user_id

        # Re-running the same batch changes nothing and grows no history — the
        # roster bar is pressed twice more often than it is pressed once.
        assert client.post(
            bulk,
            headers=admin.headers,
            json={"action": "mentor", "mentor_user_id": insider.user_id, "reason": REASON},
        ).status_code == 200
        assert len(_spells(sid)) == 1

        # And the audit row is the BATCH's, carrying the reason. It used to
        # carry `before=None, after=None` and no reason at all, which recorded
        # that something happened to a cohort and nothing about what.
        with SessionLocal() as db:
            event = db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.entity_type == "cohort",
                    AuditEvent.entity_id == spine["batch_here"],
                    AuditEvent.action == "STUDENTS_MENTOR",
                )
                .order_by(AuditEvent.occurred_at.desc())
            ).first()
            assert event is not None
            # `after` AND NOT `metadata_json`: the console's audit reader
            # (`GET /api/admin/audit/{id}`) renders before/after, and this row
            # used to carry None for both — a trail saying something happened to
            # a cohort and nothing about what.
            assert (event.after_json or {}).get("reason") == REASON
            assert (event.after_json or {}).get("mentor_user_id") == insider.user_id
            assert (event.after_json or {}).get("affected") == 1
            # `before` is deliberately still None — see the endpoint.
            assert event.before_json is None
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(AuditEvent).where(
                    AuditEvent.entity_type == "cohort",
                    AuditEvent.entity_id == spine["batch_here"],
                )
            )
            db.commit()
        _undo([sid], [outsider.user_id, insider.user_id])


# ------------------------------------------------------------- filters --


@requires_db
def test_a_batch_filter_narrows_the_mentees_and_keeps_the_faculty(client, make_user, spine):
    """B9.4. `?cohort_id=` is a fact about STUDENTS, so it narrows the mentee
    rows and deliberately leaves the faculty list whole.

    Dropping faculty who happen to have nobody in the batch would turn "show me
    who is mentoring 2026 MBA" into "hide every faculty member I could seat them
    with" — on the screen whose whole job is seating them.
    """
    admin = make_user("mm-filter-adm", Role.ADMIN)
    faculty = make_user("mm-filter-fac", Role.MENTOR)
    _file_under(faculty.user_id, spine["here"])
    sid = spine["student_here"]
    try:
        assert client.post(
            f"{ADMIN_API}/students/{sid}/mentor",
            headers=admin.headers,
            json={"mentor_user_id": faculty.user_id, "reason": REASON},
        ).status_code == 204

        def row_for(query: str) -> dict | None:
            rows = client.get(f"{ADMIN_API}/mentor-load{query}", headers=admin.headers).json()
            return next((r for r in rows if r["user_id"] == faculty.user_id), None)

        seated = row_for("")
        assert seated is not None and seated["mentee_count"] == 1

        # Their own batch: the mentee is there.
        mine = row_for(f"?cohort_id={spine['batch_here']}")
        assert mine is not None and mine["mentee_count"] == 1

        # A DIFFERENT batch: the faculty member is STILL LISTED, with nobody.
        other = row_for(f"?cohort_id={spine['batch_there']}")
        assert other is not None, (
            "a batch filter dropped a faculty member from the assignment screen"
        )
        assert other["mentee_count"] == 0
    finally:
        _undo([sid], [faculty.user_id])


@requires_db
def test_the_search_narrows_faculty_by_name_and_students_by_usn(client, make_user, spine):
    """B9.4's `?q=`. It narrows the side the reader is looking at: faculty on
    `mentor-load`, students on `unassigned-students`. Both are ANDed onto the
    reach, so a search can never widen what a scoped holder may see."""
    admin = make_user("mm-q-adm", Role.ADMIN)
    faculty = make_user("mm-q-fac", Role.MENTOR)
    _file_under(faculty.user_id, spine["here"])
    sid = spine["student_here"]
    try:
        with SessionLocal() as db:
            name = db.scalar(select(User.name).where(User.id == faculty.user_id))
            usn = db.scalar(select(Student.usn).where(Student.id == sid))

        hit = client.get(
            f"{ADMIN_API}/mentor-load?q={name.split()[-1]}", headers=admin.headers
        ).json()
        assert any(r["user_id"] == faculty.user_id for r in hit)
        miss = client.get(
            f"{ADMIN_API}/mentor-load?q=zzz{uuid.uuid4().hex[:8]}", headers=admin.headers
        ).json()
        assert miss == []

        pool = client.get(
            f"{ADMIN_API}/unassigned-students?q={usn}", headers=admin.headers
        ).json()
        assert [r["student_id"] for r in pool] == [sid]
        empty = client.get(
            f"{ADMIN_API}/unassigned-students?q=zzz{uuid.uuid4().hex[:8]}",
            headers=admin.headers,
        ).json()
        assert empty == []

        # And the batch filter on the pool, which is the picker's other default.
        assert [
            r["student_id"]
            for r in client.get(
                f"{ADMIN_API}/unassigned-students?cohort_id={spine['batch_here']}",
                headers=admin.headers,
            ).json()
        ] == [sid]

        # B9.4's page, opt-in and stated in headers rather than in an envelope —
        # the pool is a bare array to four screens and wrapping it is a breaking
        # change spent on the least important half of the requirement. THE TOTAL
        # IS THE POINT: a page of one out of a pool of many must not read as a
        # pool of one.
        paged = client.get(
            f"{ADMIN_API}/unassigned-students?page_size=1", headers=admin.headers
        )
        assert len(paged.json()) == 1
        assert int(paged.headers["X-Reep-Total"]) >= 2
        assert paged.headers["X-Reep-Page-Size"] == "1"
        # Unpaged: no page headers, but the total is still stated.
        whole = client.get(f"{ADMIN_API}/unassigned-students", headers=admin.headers)
        assert "X-Reep-Page" not in whole.headers
        assert int(whole.headers["X-Reep-Total"]) == len(whole.json())
    finally:
        _undo([sid], [faculty.user_id])


# ------------------------------------------------------------ capacity --


@requires_db
def test_capacity_comes_from_the_department_and_still_enforces_nothing(
    client, make_user, spine
):
    """H2. THE NUMBER MOVED; THE POLICY DID NOT.

    What was wrong with `settings.mentor_capacity` was only that it lived in an
    environment variable, so tuning it for one department meant a deploy.
    `departments.mentor_capacity` fixes that, and `capacity_source` says which
    of the two answered so the rail can avoid presenting a programme default as
    a departmental decision.

    NOTHING REFUSES AN ASSIGNMENT PAST IT, and this assertion is the one that
    will be deleted by somebody "finishing" the feature. The code declined to
    enforce it twice in writing, in `console.py` and in the Angular component:
    an admin who chooses to overload one faculty member in a thin year should
    not have to edit a setting first. The rail says "At capacity" in the risk
    colour and lets them.
    """
    admin = make_user("mm-cap-adm", Role.ADMIN)
    faculty = make_user("mm-cap-fac", Role.MENTOR)
    _file_under(faculty.user_id, spine["here"])
    sid = spine["student_here"]
    try:
        rows = client.get(f"{ADMIN_API}/mentor-load", headers=admin.headers).json()
        row = next(r for r in rows if r["user_id"] == faculty.user_id)
        assert row["capacity_source"] == "programme"
        programme = row["capacity"]

        # The department names its own, and ZERO is a legal answer: "this
        # department is taking nobody new this year" is a thing an office says.
        with SessionLocal() as db:
            from app.models.institution import Department

            db.get(Department, spine["here"]).mentor_capacity = 0
            db.commit()
        try:
            rows = client.get(f"{ADMIN_API}/mentor-load", headers=admin.headers).json()
            row = next(r for r in rows if r["user_id"] == faculty.user_id)
            # ZERO IS HONOURED. This assertion used to read
            # `row["capacity"] == programme`, recorded as "a KNOWN EDGE rather
            # than an accident: the fallback is `or`, so a department capacity
            # of zero reads as 'not set'" — and the same comment went on to say
            # it was "a lie the screen would print". It was. Sentry's reviewer
            # found the same thing on the pull request, independently.
            #
            # The fallback is `is not None` now, and the SECOND line below is
            # the one that matters: with `or`, a department that deliberately
            # said 0 was reported as `programme` — this field exists precisely
            # "so a programme default is not presented as a departmental
            # decision", and it was doing the opposite for the one value an
            # office uses to say "we are taking nobody new this year".
            assert row["capacity"] == 0
            assert row["capacity_source"] == "department"

            db_capacity = 25 if programme != 25 else 26
            with SessionLocal() as db:
                from app.models.institution import Department

                db.get(Department, spine["here"]).mentor_capacity = db_capacity
                db.commit()
            rows = client.get(f"{ADMIN_API}/mentor-load", headers=admin.headers).json()
            row = next(r for r in rows if r["user_id"] == faculty.user_id)
            assert row["capacity"] == db_capacity
            assert row["capacity_source"] == "department"

            # AND IT IS ADVISORY. The department says 1 and the second
            # assignment is still accepted.
            with SessionLocal() as db:
                from app.models.institution import Department

                db.get(Department, spine["here"]).mentor_capacity = 1
                db.commit()
            for target in (spine["student_here"], spine["student_there"]):
                assert client.post(
                    f"{ADMIN_API}/students/{target}/mentor",
                    headers=admin.headers,
                    json={"mentor_user_id": faculty.user_id, "reason": REASON},
                ).status_code == 204, "capacity refused an assignment — it is advisory"
        finally:
            with SessionLocal() as db:
                from app.models.institution import Department

                db.get(Department, spine["here"]).mentor_capacity = None
                db.commit()
    finally:
        _undo([spine["student_here"], spine["student_there"]], [faculty.user_id])
