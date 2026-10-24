"""B1.4 / B1.5 — the lists a scoped grant may read, and who may mentor whom.

B1.2 gave a grant a scope and taught `require_capability` to check it for ONE
record. Every list in the console still selected every row on the deployment, so
a grant scoped to one department was a fence around the single-record endpoints
with an open gate beside it: the roster, the review queue, the SWOC board, the
analytics tiles and the mentor map all answered in full to a holder who could
not open any one of their rows.

What each of these tests holds down, and what comes back if it is deleted:

1. EVERY SCOPED LIST IS NARROWED BY THE SAME REACH. Delete
   `test_a_scoped_holder_sees_only_their_reach` and a department-scoped
   `admin.students` grant reads the other department's roster — names,
   addresses and USNs — on the screen that has an Edit button beside each row.
2. THE BUTTON OBEYS THE LIST. Delete `test_the_edit_is_narrowed_by_the_same_grant`
   or `test_a_scoped_reviewer_cannot_decide_an_application_outside_their_reach`
   and the narrowing becomes decoration: a caller who cannot see a record can
   still change it by typing the id, and for registrations that means minting a
   `users` row, which IS the access control.
3. THE MAIN ADMIN IS NOT NARROWED. Delete `test_the_main_admin_is_not_narrowed`
   and the fix above becomes an empty console for the account that holds every
   capability — a screen that reads as data loss rather than as a permission
   bug.
4. A DERIVED FUNCTION IS NOT A SCOPED GRANT. Delete
   `test_a_faculty_approver_keeps_their_queue` and the two staff queues that
   compose a scope with a mentor-group fence empty themselves for every faculty
   member on the deployment, because `scope_filter` reports `nothing` for a
   capability held as a function.
5. A MENTOR IS IN THE STUDENT'S COLLEGE. Delete
   `test_a_mentor_from_another_college_is_refused` and rule 2 becomes a thing
   the assignment screen can edit across tenants.
6. EXISTING PAIRS ARE KEPT AND FLAGGED. Delete
   `test_an_existing_cross_department_pair_is_kept_and_flagged` and the
   acceptance criterion goes with it: the office meets B1.5 as mentors losing
   their mentees rather than as a policy taking effect.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import delete, or_, select

from conftest import TEST_PASSWORD, requires_db

from app.db import SessionLocal
from app.models.badge import BadgeEvidence, EvidenceStatus, EvidenceType
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.leave import LeaveRequest, LeaveStatus
from app.models.registration import Registration, RegistrationStatus
from app.models.swoc import SwocEntry
from app.models.mentor_assignment import MentorAssignment
from app.models.user import Mentor, Role, Student, User
from app.seed_roster import SSO_ONLY_PASSWORD_HASH


# --------------------------------------------------------------- the spine --


@pytest.fixture
def spine():
    """Two colleges. In the first, two departments with a student in each.

    The smallest shape in which both of this task's sentences can fail: "only
    your own department" needs two departments under one college, and "only your
    own college" needs a second college that a department-level fence would not
    notice.

    A pending APPLICATION per department too, plus one that named NOTHING —
    every level on the public form is optional, so a row with five null pointers
    is a state the form produces on purpose and the queue has to have an answer
    for.
    """
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        one = College(code=f"L{tag.upper()}", name="Scoped College", status=STATUS_ACTIVE)
        two = College(code=f"F{tag.upper()}", name="Far College", status=STATUS_ACTIVE)
        db.add_all([one, two])
        db.flush()
        here = Department(college_id=one.id, name="Here", code=f"H{tag}")
        there = Department(college_id=one.id, name="There", code=f"T{tag}")
        far = Department(college_id=two.id, name="Far", code=f"F{tag}")
        db.add_all([here, there, far])
        db.flush()

        def batch(code: str, department_id: str) -> Cohort:
            return Cohort(
                code=f"{code}-{tag}", name=f"Batch {code}", batch_label="2026-28",
                degree_level=DegreeLevel.PG, department_id=department_id,
                start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
            )

        b_here, b_there, b_far = batch("SH", here.id), batch("ST", there.id), batch("SF", far.id)
        db.add_all([b_here, b_there, b_far])
        db.flush()

        def student(label: str, cohort: Cohort) -> tuple[User, Student]:
            u = User(
                email=f"{label}-{tag}@scoped.test", name=f"{label.title()} {tag}",
                role=Role.STUDENT, password_hash=SSO_ONLY_PASSWORD_HASH,
            )
            db.add(u)
            db.flush()
            s = Student(user_id=u.id, usn=f"{label[:4].upper()}{tag}", cohort_id=cohort.id)
            db.add(s)
            return u, s

        u_here, s_here = student("here", b_here)
        u_there, s_there = student("there", b_there)
        u_far, s_far = student("far", b_far)
        db.flush()

        apps = [
            Registration(
                name=f"App Here {tag}", email=f"app-here-{tag}@scoped.test",
                degree_level=DegreeLevel.PG, status=RegistrationStatus.PENDING_REVIEW,
                college_id=one.id, department_id=here.id,
            ),
            Registration(
                name=f"App There {tag}", email=f"app-there-{tag}@scoped.test",
                degree_level=DegreeLevel.PG, status=RegistrationStatus.PENDING_REVIEW,
                college_id=one.id, department_id=there.id,
            ),
            Registration(
                name=f"App Nowhere {tag}", email=f"app-nowhere-{tag}@scoped.test",
                degree_level=DegreeLevel.PG, status=RegistrationStatus.PENDING_REVIEW,
            ),
        ]
        db.add_all(apps)

        # A PENDING claim and a SUBMITTED leave request per student, so the two
        # composed staff queues have something to be narrowed. Without rows in
        # them, "the other department is not in this list" is a sentence about
        # an empty list and passes whatever the fence does — which is exactly
        # how the first draft of these tests passed with the narrowing removed.
        def claim(student: Student) -> BadgeEvidence:
            return BadgeEvidence(
                student_id=student.id, badge_code="MGR-BUSINESS-COMMUNICATION",
                evidence_type=EvidenceType.EXTERNAL_VERIFIED,
                status=EvidenceStatus.PENDING_VERIFICATION,
                title=f"Claim {tag}",
            )

        def leave(user: User) -> LeaveRequest:
            return LeaveRequest(
                requester_user_id=user.id,
                from_date=date(2026, 10, 1), to_date=date(2026, 10, 2),
                reason=f"scoped-list fixture {tag}", status=LeaveStatus.SUBMITTED,
            )

        claims = [claim(s_here), claim(s_there)]
        leaves = [leave(u_here), leave(u_there)]
        db.add_all(claims + leaves)
        db.commit()
        made |= {
            "college": one.id, "far_college": two.id,
            "here": here.id, "there": there.id, "far": far.id,
            "batch_here": b_here.id, "batch_there": b_there.id, "batch_far": b_far.id,
            "student_here": s_here.id, "student_there": s_there.id, "student_far": s_far.id,
            "user_here": u_here.id, "user_there": u_there.id, "user_far": u_far.id,
            "app_here": apps[0].id, "app_there": apps[1].id, "app_nowhere": apps[2].id,
            "leave_here": leaves[0].id, "leave_there": leaves[1].id,
        }

    yield made

    with SessionLocal() as db:
        students = [made["student_here"], made["student_there"], made["student_far"]]
        db.execute(delete(SwocEntry).where(SwocEntry.student_id.in_(students)))
        db.execute(delete(BadgeEvidence).where(BadgeEvidence.student_id.in_(students)))
        db.execute(
            delete(LeaveRequest).where(
                LeaveRequest.id.in_([made["leave_here"], made["leave_there"]])
            )
        )
        db.execute(
            delete(Registration).where(
                Registration.id.in_([made["app_here"], made["app_there"], made["app_nowhere"]])
            )
        )
        db.execute(delete(Student).where(Student.id.in_(students)))
        db.execute(
            delete(User).where(
                User.id.in_([made["user_here"], made["user_there"], made["user_far"]])
            )
        )
        db.execute(
            delete(Cohort).where(
                Cohort.id.in_([made["batch_here"], made["batch_there"], made["batch_far"]])
            )
        )
        db.execute(
            delete(Department).where(
                Department.id.in_([made["here"], made["there"], made["far"]])
            )
        )
        db.execute(delete(College).where(College.id.in_([made["college"], made["far_college"]])))
        db.commit()


@pytest.fixture
def scoped_grant():
    """Give an account one capability, scoped to one rung, and take it back.

    Written as a row rather than through `POST /api/admin/governance/grants` for
    the reason test_exports.py gives: five of these keys are flagged
    `carries_pii`, so a deputy's API-made grant lands `pending_approval` (B2.4)
    and holds nothing until a different `admin.governance` holder approves it,
    while the Main Admin's is live at once — a fixture that depended on who
    granted would make every test below about the approval rule instead.
    """
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel | None, target_id: str | None) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key, subject_kind=SubjectKind.USER, subject_user_id=user_id,
                scope_level=level, scope_id=target_id,
                reason="the scoped-list tests need a scoped grant, twenty characters plus",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


def _file_under(user_id: str, department_id: str | None) -> None:
    """Put a staff account in a department — where `ancestry_of_user` reads it."""
    with SessionLocal() as db:
        db.get(User, user_id).department_id = department_id
        db.commit()


def _undo(student_ids: list[str], faculty_user_ids: list[str]) -> None:
    """Put everything this test touched back, in the order the keys allow.

    FOUR POINTERS WITH NO `ON DELETE` BETWEEN THEM, and the teardown order is
    forced by all four. `students.mentor_id` -> `mentors.id` and
    `mentors.user_id` -> `users.id` mean an assignment left behind takes
    `make_user`'s own teardown down with it; `users.department_id` ->
    `departments.id` means a faculty account still FILED under this fixture's
    department blocks the fixture from deleting it; and B9.1's
    `mentor_assignments` points at BOTH the student and the mentor group with
    no cascade either, deliberately — the database refuses to delete either out
    from under a recorded spell. All of them surface as an IntegrityError in
    somebody else's traceback, which is why this is one function called from a
    `finally` rather than four lines per test.
    """
    with SessionLocal() as db:
        for sid in student_ids:
            student = db.get(Student, sid)
            if student is not None:
                student.mentor_id = None
        db.flush()
        # Children before parents, as both purge modules order it.
        group_ids = [
            gid for (gid,) in db.execute(
                select(Mentor.id).where(Mentor.user_id.in_(faculty_user_ids))
            ).all()
        ]
        db.execute(
            delete(MentorAssignment).where(
                or_(
                    MentorAssignment.student_id.in_(student_ids or [""]),
                    MentorAssignment.mentor_id.in_(group_ids or [""]),
                )
            )
        )
        # And the handover grants a release minted along the way.
        db.execute(
            delete(CapabilityGrant).where(CapabilityGrant.subject_user_id.in_(faculty_user_ids))
        )
        db.execute(delete(Mentor).where(Mentor.user_id.in_(faculty_user_ids)))
        for uid in faculty_user_ids:
            user = db.get(User, uid)
            if user is not None:
                user.department_id = None
        db.commit()


#: One row per list B1.4 narrows that is reachable by a scoped (non-ADMIN)
#: holder: the URL, the capability its gate checks, and how to read the ids out
#: of the payload. Parametrised rather than five near-copies, because the
#: property is the same sentence about five endpoints and the thing that must
#: not happen is a sixth list arriving without one.
SCOPED_LISTS = [
    ("/api/admin/students", "admin.students", lambda rows: {r["student_id"] for r in rows}),
    ("/api/admin/swoc", "admin.swoc", lambda rows: {r["student_id"] for r in rows}),
    (
        "/api/admin/unassigned-students",
        "admin.mentors",
        lambda rows: {r["student_id"] for r in rows},
    ),
]

# mentor-load is NOT in that table, and the reason is worth knowing before
# adding it back: its student rows are MENTEES, and a student with no mentor
# appears in no row at all. Parametrising it here would have asserted the
# scope of a list that is empty for a different reason, which is a test that
# passes whatever the fence does. It gets its own two tests below — one for the
# faculty rows, one (`cross_department`) for the mentee rows.

# THE JOBS SHEET IS NOT IN THAT TABLE EITHER, and for a sharper reason than
# mentor-load's: `GET /api/admin/jobs` became scopeable in B12.1 and its rows
# carry NO STUDENT AT ALL, so there is no `student_id` for `ids_of` to read.
# Its reach is projected onto `jobs` by `scope_views.job_scope_clause`, whose
# NULL rule is the deliberate opposite of every other projection's — a posting
# naming no college is visible to everybody — and that is a property this
# table's one assertion ("the other department is not in this list") cannot
# express. It is pinned in `tests/test_jobs_scope.py` instead.


@requires_db
@pytest.mark.parametrize("url, capability, ids_of", SCOPED_LISTS, ids=lambda v: str(v)[:24])
def test_a_scoped_holder_sees_only_their_reach(
    client, make_user, spine, scoped_grant, url, capability, ids_of
):
    """A department-scoped grant lists that department's students and no others.

    DELETE THIS AND EVERY CONSOLE LIST GOES BACK TO SELECTING THE WHOLE
    DEPLOYMENT. That was the state B1.2 shipped into: the single-record check
    was scoped and the lists that feed it were not, so the narrowest grant in
    the system read the widest screen in it — including the roster, whose rows
    carry a name, an address and a USN.
    """
    faculty = make_user(f"sl-{capability[-6:]}-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, capability, ScopeLevel.DEPARTMENT, spine["here"])

    r = client.get(url, headers=faculty.headers)
    assert r.status_code == 200, r.text
    seen = ids_of(r.json())
    assert spine["student_there"] not in seen, f"{url} leaked the other department"
    assert spine["student_far"] not in seen, f"{url} leaked the other college"


@requires_db
@pytest.mark.parametrize("url, capability, ids_of", SCOPED_LISTS, ids=lambda v: str(v)[:24])
def test_the_main_admin_is_not_narrowed(client, make_user, spine, url, capability, ids_of):
    """A baseline capability is unscoped, and the office's job is the programme.

    The companion assertion to the one above and the more dangerous of the two
    to lose: `Reach.everything` used to select NO rows, so forgetting one
    `.everything` check produced an empty roster on the account that holds every
    capability — which reads as data loss, not as a permission bug.
    """
    admin = make_user(f"sl-admin-{spine['tag']}", Role.ADMIN)
    r = client.get(url, headers=admin.headers)
    assert r.status_code == 200, r.text
    seen = ids_of(r.json())
    assert {spine["student_here"], spine["student_there"], spine["student_far"]} <= seen


@requires_db
def test_a_grant_pointing_at_a_deleted_department_lists_nothing(
    client, make_user, spine, scoped_grant
):
    """An empty reach is not an unrestricted one.

    `if not clauses` collapsing "reaches nothing" into "no restriction" is the
    inversion `policies.Reach.student_ids` carries its own warning about. Here
    it would mean the narrowest grant on the deployment reading the whole
    roster.
    """
    faculty = make_user(f"sl-gone-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.students", ScopeLevel.DEPARTMENT, "a-department-that-is-gone")
    r = client.get("/api/admin/students", headers=faculty.headers)
    assert r.status_code == 200, r.text
    assert r.json() == []


@requires_db
def test_the_response_states_the_scope_it_was_narrowed_to(
    client, make_user, spine, scoped_grant
):
    """The app bar's scope control reads a header, not an envelope.

    04-backend-changes.md asks every scoped response to carry `scope`. Eleven
    endpoints answer a bare `list[...]` and the Phase 2 console is built against
    those arrays, so the scope travels in headers — the same answer
    `app/exports.py` reached for a CSV. Delete this and a scoped admin has no
    way to tell a narrowed screen from an empty college.
    """
    faculty = make_user(f"sl-hdr-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.students", ScopeLevel.DEPARTMENT, spine["here"])
    r = client.get("/api/admin/students", headers=faculty.headers)
    assert r.headers["X-Reep-Scope"] == "narrowed"
    assert r.headers["X-Reep-Scope-Departments"] == spine["here"]

    admin = make_user(f"sl-hdr-admin-{spine['tag']}", Role.ADMIN)
    assert client.get("/api/admin/students", headers=admin.headers).headers[
        "X-Reep-Scope"
    ] == "programme"


# ---------------------------------------------------------- the mentor map --


@requires_db
def test_mentor_load_lists_only_faculty_in_reach(client, make_user, spine, scoped_grant):
    """The faculty rows are narrowed by `Reach.user_ids()`, not only the mentees.

    Narrowing the mentees alone is the obvious half and the wrong one: the row
    itself carries the person's name, department, designation and load, which is
    exactly the staff directory a college admin of another college has no
    business reading. Delete this and `mentor-load` is that directory.
    """
    inside = make_user(f"ml-in-{spine['tag']}", Role.MENTOR)
    outside = make_user(f"ml-out-{spine['tag']}", Role.MENTOR)
    _file_under(inside.user_id, spine["here"])
    _file_under(outside.user_id, spine["far"])
    reader = make_user(f"ml-read-{spine['tag']}", Role.MENTOR)
    scoped_grant(reader.user_id, "admin.analytics", ScopeLevel.DEPARTMENT, spine["here"])
    try:
        rows = client.get("/api/admin/mentor-load", headers=reader.headers).json()
        seen = {r["user_id"] for r in rows}
        assert inside.user_id in seen
        assert outside.user_id not in seen, "a faculty account in another college was listed"
    finally:
        _undo([], [inside.user_id, outside.user_id])


@requires_db
def test_mentor_load_shows_the_main_admin_every_faculty_account(client, make_user, spine):
    """The companion to the above, and the one whose failure looks like data loss.

    `Reach.everything` returning no rows would draw an empty Faculty & Students
    screen for the office account. Delete this and that regression ships
    looking like a database problem.
    """
    inside = make_user(f"ml-adm-in-{spine['tag']}", Role.MENTOR)
    _file_under(inside.user_id, spine["here"])
    admin = make_user(f"ml-adm-{spine['tag']}", Role.ADMIN)
    try:
        rows = client.get("/api/admin/mentor-load", headers=admin.headers).json()
        assert inside.user_id in {r["user_id"] for r in rows}
        assert client.get(
            "/api/admin/mentor-load", headers=admin.headers
        ).headers["X-Reep-Scope"] == "programme"
    finally:
        _undo([], [inside.user_id])


# ------------------------------------------------------- the review queue --


@requires_db
def test_a_scoped_reviewer_sees_only_their_own_applications(
    client, make_user, spine, scoped_grant
):
    """An application is not a student yet, so the queue is scoped by the CLAIM.

    There is no `students` row for `Reach.student_ids()` to narrow — what the
    row hangs under is what the applicant named on the public form. Delete this
    and `registration_scope_clause` has no caller proving it matches a rung.
    """
    faculty = make_user(f"sl-reg-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.registrations", ScopeLevel.DEPARTMENT, spine["here"])
    r = client.get("/api/register/pending", headers=faculty.headers)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert spine["app_here"] in ids
    assert spine["app_there"] not in ids


@requires_db
def test_an_application_that_named_nothing_belongs_to_the_main_admin(
    client, make_user, spine, scoped_grant
):
    """Every level on the public form is optional, so "named nothing" is real.

    A row hanging under nothing is reached by no scoped grant — the same answer
    `reaches_target` gives any unfiled thing, and for the same reason: if the
    unfiled state were visible to everybody it would be the way around every
    scope in the system, and this is the one state an ANONYMOUS form can create
    on demand.
    """
    faculty = make_user(f"sl-reg-none-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.registrations", ScopeLevel.COLLEGE, spine["college"])
    scoped = client.get("/api/register/pending", headers=faculty.headers)
    assert spine["app_nowhere"] not in {row["id"] for row in scoped.json()}

    admin = make_user(f"sl-reg-admin-{spine['tag']}", Role.ADMIN)
    everyone = client.get("/api/register/pending", headers=admin.headers)
    assert spine["app_nowhere"] in {row["id"] for row in everyone.json()}


@requires_db
def test_a_scoped_reviewer_cannot_decide_an_application_outside_their_reach(
    client, make_user, spine, scoped_grant
):
    """THE QUEUE AND THE BUTTON OBEY ONE RULE.

    Narrowing the list alone leaves the act wide open, and approving is the ONE
    path in REEP that mints a `users` row — the access control itself. Delete
    this and a reviewer scoped to one department provisions accounts for
    another by posting an id they were never shown.
    """
    faculty = make_user(f"sl-dec-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.registrations", ScopeLevel.DEPARTMENT, spine["here"])
    r = client.post(
        f"/api/register/{spine['app_there']}/decision",
        headers=faculty.headers,
        json={"decision": "REJECT", "note": "should never be recorded"},
    )
    assert r.status_code == 403, r.text
    with SessionLocal() as db:
        assert db.get(Registration, spine["app_there"]).status is RegistrationStatus.PENDING_REVIEW


# ------------------------------------------------------------- the roster --


@requires_db
def test_the_edit_is_narrowed_by_the_same_grant_as_the_list(
    client, make_user, spine, scoped_grant
):
    """A roster row a scoped admin cannot SEE is a roster row they cannot EDIT.

    `PATCH /api/admin/students/{id}` changes the name, the batch and the ADDRESS
    the account signs in on. Delete this and narrowing the list becomes
    decoration: the id is all it took.
    """
    faculty = make_user(f"sl-edit-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.students", ScopeLevel.DEPARTMENT, spine["here"])

    mine = client.patch(
        f"/api/admin/students/{spine['student_here']}",
        headers=faculty.headers, json={"current_semester": 2},
    )
    assert mine.status_code == 200, mine.text

    theirs = client.patch(
        f"/api/admin/students/{spine['student_there']}",
        headers=faculty.headers, json={"current_semester": 4},
    )
    assert theirs.status_code == 403, theirs.text
    with SessionLocal() as db:
        assert db.get(Student, spine["student_there"]).current_semester != 4


@requires_db
def test_a_batch_action_refuses_rather_than_applying_to_half_a_batch(
    client, make_user, spine, scoped_grant
):
    """All or nothing, and the refusal names how many are out of reach.

    Applying a move to the students a scoped holder reaches and skipping the
    rest would report `affected: n` for a batch of more than n and split the
    batch across two places with nothing on screen saying why. Delete this and
    the partial write is what a college admin gets.
    """
    faculty = make_user(f"sl-bulk-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.students", ScopeLevel.DEPARTMENT, spine["here"])
    r = client.post(
        f"/api/admin/cohorts/{spine['batch_there']}/students/bulk",
        headers=faculty.headers, json={"action": "semester", "current_semester": 3},
    )
    assert r.status_code == 403, r.text
    with SessionLocal() as db:
        assert db.get(Student, spine["student_there"]).current_semester != 3


# ---------------------------------------------------------- the SWOC board --


@requires_db
def test_a_scoped_author_cannot_write_swoc_about_someone_out_of_reach(
    client, make_user, spine, scoped_grant
):
    """The board is narrowed, so the pen is narrowed.

    A SWOC line is the one thing on a student's landing page that another
    person wrote about them. `admin.swoc` used to be programme-wide by the only
    shape a grant had; delete this and it is again, for writes, on a screen
    whose list is not.
    """
    faculty = make_user(f"sl-swoc-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.swoc", ScopeLevel.DEPARTMENT, spine["here"])
    body = {"kind": "WEAKNESS", "text": "should never be stored", "weight": 3}

    ok = client.post(f"/api/admin/swoc/{spine['student_here']}", headers=faculty.headers, json=body)
    assert ok.status_code == 201, ok.text

    refused = client.post(
        f"/api/admin/swoc/{spine['student_there']}", headers=faculty.headers, json=body
    )
    assert refused.status_code == 403, refused.text
    with SessionLocal() as db:
        assert db.scalar(
            select(SwocEntry.id).where(SwocEntry.student_id == spine["student_there"])
        ) is None


# ------------------------------------------------------------- the tiles --


@requires_db
def test_the_analytics_tiles_count_only_the_reach(client, make_user, spine, scoped_grant):
    """A count is a smaller leak than a list and it is still a leak.

    "Your college has 41 students" told to somebody granted Analytics for one
    department of it is a number they can subtract. Every tile is narrowed
    through ONE reach so the ratios stay reproducible from the screens beside
    them; delete this and a scoped numerator lands over a programme-wide
    denominator.
    """
    faculty = make_user(f"sl-tiles-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.analytics", ScopeLevel.DEPARTMENT, spine["here"])
    scoped = client.get("/api/admin/analytics-summary", headers=faculty.headers).json()

    admin = make_user(f"sl-tiles-admin-{spine['tag']}", Role.ADMIN)
    everyone = client.get("/api/admin/analytics-summary", headers=admin.headers).json()

    assert scoped["students_total"] < everyone["students_total"]
    assert scoped["pending_registrations"] < everyone["pending_registrations"]


# ------------------------------------------- the two composed staff queues --


@requires_db
def test_a_faculty_verifier_reads_their_grant_and_not_their_group(
    client, make_user, login, spine, scoped_grant
):
    """The evidence queue is the GRANT's reach and nothing else (2026-10-16).

    Until this date `mentor.verifications` was derived from mentoring
    somebody, and the queue composed the reach with the mentor-group fence; the
    owner then took verification away from every mentor. So a faculty member
    who mentors a student and holds no grant is REFUSED, and once granted the
    other department they see that department -- and not their own mentee in
    this one. Put the group back into `_evidence_query` and the second half of
    this test fails: the mentee reappears in a queue the office never gave.

    The LEAVE queue is asserted beside it as before: leave approval is the
    Main Admin's alone since 2026-09-16, so a faculty member gets a 403 there
    whatever they mentor.

    Signed in AFTER the group exists, because the session is a signed snapshot:
    `mentorId` is minted at login and rule 2 reads the claim, not the row.
    """
    faculty = make_user(f"sl-fn-{spine['tag']}", Role.MENTOR)
    with SessionLocal() as db:
        group = Mentor(user_id=faculty.user_id)
        db.add(group)
        db.flush()
        db.get(Student, spine["student_there"]).mentor_id = group.id
        db.commit()
    try:
        headers = login(faculty.email, TEST_PASSWORD)
        assert client.get("/api/leaves/pending", headers=headers).status_code == 403
        # Mentoring brings no queue.
        assert client.get("/api/mentor/badge-evidence/pending", headers=headers).status_code == 403

        scoped_grant(faculty.user_id, "mentor.verifications", ScopeLevel.DEPARTMENT, spine["here"])
        evidence = client.get("/api/mentor/badge-evidence/pending", headers=headers)
        assert evidence.status_code == 200, evidence.text
        assert evidence.headers["X-Reep-Scope"] == "narrowed"
        seen = {row["student_id"] for row in evidence.json()}
        # BOTH halves, for the reason the next test gives.
        assert spine["student_here"] in seen
        assert spine["student_there"] not in seen, "the mentor's own group leaked into the queue"
    finally:
        _undo([spine["student_there"]], [faculty.user_id])


@requires_db
def test_a_granted_verifier_reads_only_their_scope_of_the_evidence_queue(
    client, make_user, spine, scoped_grant
):
    """The other half of the same queue, and the half that bites today.

    `_FACULTY_ONLY` keeps `mentor.verifications` out of the Main Admin's
    baseline precisely so that looking at a stuck student's evidence is an
    audited grant it makes to itself — and until B1.4 that grant read every
    pending claim on the deployment. This asserts the ADMIN branch narrows;
    `test_a_faculty_verifier_reads_their_grant_and_not_their_group` asserts
    the MENTOR branch narrows the same way.
    """
    admin = make_user(f"sl-ev-{spine['tag']}", Role.ADMIN)
    scoped_grant(admin.user_id, "mentor.verifications", ScopeLevel.DEPARTMENT, spine["here"])
    r = client.get("/api/mentor/badge-evidence/pending", headers=admin.headers)
    assert r.status_code == 200, r.text
    assert r.headers["X-Reep-Scope"] == "narrowed"
    seen = {row["student_id"] for row in r.json()}
    # BOTH halves. "the other one is absent" is a sentence about an empty list
    # unless the first one is present, and an empty list is what a broken fence
    # and a correct one look like when the fixture has no claims in it.
    assert spine["student_here"] in seen
    assert spine["student_there"] not in seen


# ------------------------------------------------------ B1.5 — who mentors --


@requires_db
def test_a_mentor_from_another_college_is_refused(client, make_user, spine):
    """422 when the faculty account and the student are in different colleges.

    A college is the tenant. Assigning across one edits rule 2 with a form: the
    mentor then reads that student's marks, attendance, USN, leave reasons and
    interview transcripts through every `_assert_can_access_student` in the
    codebase. Delete this and the assignment screen is the way around
    multi-college scoping entirely.
    """
    admin = make_user(f"b15-admin-{spine['tag']}", Role.ADMIN)
    outsider = make_user(f"b15-out-{spine['tag']}", Role.MENTOR)
    _file_under(outsider.user_id, spine["far"])
    try:
        r = client.post(
            f"/api/admin/students/{spine['student_here']}/mentor",
            headers=admin.headers, json={"mentor_user_id": outsider.user_id, "reason": "test assignment"},
        )
        assert r.status_code == 422, r.text
        assert "college" in r.json()["detail"].lower()
        with SessionLocal() as db:
            assert db.get(Student, spine["student_here"]).mentor_id is None
    finally:
        _undo([spine["student_here"]], [outsider.user_id])


@requires_db
def test_a_cross_department_pair_inside_one_college_is_allowed(client, make_user, spine):
    """The refusal is at the COLLEGE, not the department, and that is the line.

    The placement cell mentors across departments routinely; the picker merely
    offers same-department first. Delete this and B1.5 quietly becomes a
    department rule, which breaks the office's own mentoring on the deploy that
    ships it.
    """
    admin = make_user(f"b15-ok-{spine['tag']}", Role.ADMIN)
    faculty = make_user(f"b15-fac-{spine['tag']}", Role.MENTOR)
    _file_under(faculty.user_id, spine["there"])
    try:
        r = client.post(
            f"/api/admin/students/{spine['student_here']}/mentor",
            headers=admin.headers, json={"mentor_user_id": faculty.user_id, "reason": "test assignment"},
        )
        assert r.status_code == 204, r.text
        with SessionLocal() as db:
            assert db.get(Student, spine["student_here"]).mentor_id is not None
    finally:
        _undo([spine["student_here"]], [faculty.user_id])


@requires_db
def test_an_unfiled_faculty_account_can_still_be_assigned(client, make_user, spine):
    """Neither side filed means nobody can assert they are in different colleges.

    An unfiled account is a first-class state the Faculty screen keeps a list
    of. Refusing here would make filing a precondition for mentoring on every
    deployment that has not finished filing anyone — which is every deployment
    the day the spine arrives — and it would read as the assign button being
    broken.
    """
    admin = make_user(f"b15-unfiled-{spine['tag']}", Role.ADMIN)
    faculty = make_user(f"b15-uf-{spine['tag']}", Role.MENTOR)
    try:
        r = client.post(
            f"/api/admin/students/{spine['student_here']}/mentor",
            headers=admin.headers, json={"mentor_user_id": faculty.user_id, "reason": "test assignment"},
        )
        assert r.status_code == 204, r.text
    finally:
        _undo([spine["student_here"]], [faculty.user_id])


@requires_db
def test_an_existing_cross_department_pair_is_kept_and_flagged(client, make_user, spine):
    """THE ACCEPTANCE CRITERION: existing pairs are kept, flagged, never broken.

    Everything already on the roster was assigned under the old rule by somebody
    who meant it, and `students.mentor_id` is rule 2's input — breaking those
    rows on deploy empties real mentor groups, and a mentor with no group sees
    nobody. So the pair stays and `mentor-load` says `cross_department: true`
    about it. Delete this and B1.5 arrives as mentors losing their mentees.
    """
    admin = make_user(f"b15-flag-{spine['tag']}", Role.ADMIN)
    faculty = make_user(f"b15-flagfac-{spine['tag']}", Role.MENTOR)
    _file_under(faculty.user_id, spine["there"])
    try:
        assert client.post(
            f"/api/admin/students/{spine['student_here']}/mentor",
            headers=admin.headers, json={"mentor_user_id": faculty.user_id, "reason": "test assignment"},
        ).status_code == 204

        rows = client.get("/api/admin/mentor-load", headers=admin.headers).json()
        row = next(r for r in rows if r["user_id"] == faculty.user_id)
        mentee = next(m for m in row["mentees"] if m["student_id"] == spine["student_here"])
        assert mentee["cross_department"] is True
    finally:
        _undo([spine["student_here"]], [faculty.user_id])


@requires_db
def test_a_same_department_pair_is_not_flagged(client, make_user, spine):
    """The flag has to be able to be False, or it is a decoration on every row.

    It also must not fire when a side is unfiled: `None != "dept"` is True in
    Python and would paint every unfiled pair as crossing a boundary nobody has
    recorded.
    """
    admin = make_user(f"b15-same-{spine['tag']}", Role.ADMIN)
    faculty = make_user(f"b15-samefac-{spine['tag']}", Role.MENTOR)
    _file_under(faculty.user_id, spine["here"])
    try:
        assert client.post(
            f"/api/admin/students/{spine['student_here']}/mentor",
            headers=admin.headers, json={"mentor_user_id": faculty.user_id, "reason": "test assignment"},
        ).status_code == 204
        rows = client.get("/api/admin/mentor-load", headers=admin.headers).json()
        row = next(r for r in rows if r["user_id"] == faculty.user_id)
        mentee = next(m for m in row["mentees"] if m["student_id"] == spine["student_here"])
        assert mentee["cross_department"] is False
    finally:
        _undo([spine["student_here"]], [faculty.user_id])


@requires_db
def test_an_unfiled_pair_is_not_flagged_as_crossing_a_department(client, make_user, spine):
    """`None != "here"` is True in Python, and that is the bug this pins.

    An unfiled faculty account hangs under no department, so "these two are in
    different departments" is not something anybody can assert about the pair —
    the same reasoning that lets `_assert_same_college` allow the assignment in
    the first place. Written as `department_id and mentor_department and ...`
    rather than a bare `!=`; delete this and every unfiled pair on the screen
    is painted as crossing a boundary nobody recorded.
    """
    admin = make_user(f"b15-nf-{spine['tag']}", Role.ADMIN)
    faculty = make_user(f"b15-nffac-{spine['tag']}", Role.MENTOR)
    try:
        assert client.post(
            f"/api/admin/students/{spine['student_here']}/mentor",
            headers=admin.headers, json={"mentor_user_id": faculty.user_id, "reason": "test assignment"},
        ).status_code == 204
        rows = client.get("/api/admin/mentor-load", headers=admin.headers).json()
        row = next(r for r in rows if r["user_id"] == faculty.user_id)
        mentee = next(m for m in row["mentees"] if m["student_id"] == spine["student_here"])
        assert mentee["cross_department"] is False
    finally:
        _undo([spine["student_here"]], [faculty.user_id])


@requires_db
def test_the_picker_filters_to_one_department_and_cannot_widen_scope(
    client, make_user, spine, scoped_grant
):
    """`?department_id=` narrows within the reach; it never reaches past it.

    The filter and the reach are ANDed, so re-validation is the shape of the
    query rather than a second check that could one day disagree with the first.
    Delete this and the obvious implementation — "if department_id, filter by
    it" applied INSTEAD of the reach — hands a scoped admin any department they
    can name.
    """
    faculty = make_user(f"b15-pick-{spine['tag']}", Role.MENTOR)
    scoped_grant(faculty.user_id, "admin.mentors", ScopeLevel.DEPARTMENT, spine["here"])

    mine = client.get(
        f"/api/admin/unassigned-students?department_id={spine['here']}", headers=faculty.headers
    )
    assert {r["student_id"] for r in mine.json()} >= {spine["student_here"]}

    theirs = client.get(
        f"/api/admin/unassigned-students?department_id={spine['there']}", headers=faculty.headers
    )
    assert theirs.status_code == 200, theirs.text
    assert theirs.json() == [], "asking for a department you do not hold must return nothing"
