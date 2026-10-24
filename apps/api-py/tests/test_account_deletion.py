"""Deleting ONE person from the console (2026-09-16): remove-and-restore, and
delete-for-good behind a code the Main Admin reads out of their own mailbox.

What is pinned, and what breaks if each goes:

  * every foreign key the walk reaches is CLASSIFIED — a new column with no
    ON DELETE and no policy entry fails here, not as a 500 in the office's
    face after the files are already gone;
  * the walk reaches every table `purge_students` would scope for a student,
    or names the table in `NOT_PER_ACCOUNT` with a reason — so a new
    student-owned table is decided by BOTH destructors;
  * the real delete, run against the real schema and rolled back, takes the
    student's rows and leaves the faculty member's — and vice versa — with the
    mentee released rather than deleted;
  * the permanent endpoints spend a CODE before a byte is touched, a wrong
    code destroys nothing, and a spent code does not work twice;
  * REMOVE takes the account off every list and every door and RESTORE puts
    it back with the rows untouched.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import func, select

from conftest import TEST_PASSWORD, requires_db
from app import account_deletion, college_deletion, mail_transport, purge_students
from app.db import Base, SessionLocal
from app.models.user import Mentor, Role, Student, User
from app.routers import admin_deletion
from test_purge_students import _exists, _seed_one_of_each

T = Base.metadata.tables
USERS = "/api/admin/users"
STUDENTS = "/api/admin/students"
CODE = "/api/admin/deletions/code"


@pytest.fixture(autouse=True)
def _fresh_throttle():
    admin_deletion.reset_throttle()
    yield
    admin_deletion.reset_throttle()


def _last_code() -> str:
    text = mail_transport.outbox[-1].text
    match = re.search(r"\n\s+(\d{6})\n", text)
    assert match, f"no six-digit code in the mail:\n{text}"
    return match.group(1)


# ---------------------------------------------------------- the policy --


def test_every_edge_the_account_delete_reaches_is_classified():
    """No database needed: the guard that catches the next foreign key."""
    assert account_deletion.policy_problems() == []


def test_every_edge_the_college_delete_reaches_is_classified():
    assert college_deletion.policy_problems() == []


def test_the_account_walk_reaches_every_table_purge_students_would_scope():
    """The per-account delete and the whole-cohort purge must agree about what
    is a student's. A table `purge_students` empties or scopes that this walk
    never reaches — and that nobody wrote a reason for — is a student's row
    left standing after the office was told they were deleted."""
    from app.deletion_walk import Walk

    walk = Walk(
        roots={n: T[n].c.id == "-" for n in account_deletion.ACCOUNT_ROOTS},
        policy=account_deletion.ACCOUNT_POLICY,
    )
    reached = set(walk.deleted_tables)
    scoped = {t for t, v in purge_students.STUDENT_VERDICTS.items() if v != purge_students.KEEP}
    missing = sorted(scoped - reached - set(account_deletion.NOT_PER_ACCOUNT))
    assert not missing, (
        "purge_students would take rows from these tables and the one-account "
        f"delete never reaches them: {missing}. Reach them (an FK the policy "
        "classifies) or name them in NOT_PER_ACCOUNT with the reason."
    )
    stale = sorted(set(account_deletion.NOT_PER_ACCOUNT) & reached)
    assert not stale, f"named as not-per-account but reached anyway: {stale}"


def test_the_people_columns_stop_a_college_delete():
    """The three columns that carry a person are REFUSE_IF_ANY and nothing in
    the college policy deletes or clears them: a college with a student seated
    is never deletable by implication."""
    from app.deletion_walk import REFUSE_IF_ANY

    for key in (("students", "cohort_id"), ("students", "department_id"), ("users", "department_id")):
        assert college_deletion.COLLEGE_POLICY[key] == REFUSE_IF_ANY, key


# ------------------------------------------------ the real delete, rolled back --


@requires_db
def test_a_student_goes_with_every_row_and_the_faculty_member_survives():
    with SessionLocal() as db:
        try:
            ids = _seed_one_of_each(db)
            db.flush()
            student_user = db.get(User, ids["student_user"])
            doomed, walk = account_deletion.walk_for(db, student_user)
            assert doomed.student_id == ids["student"]
            account_deletion.delete_rows(db, doomed, walk)

            for table, key in (
                ("users", "student_user"), ("students", "student"),
                ("leave_requests", "student_leave"), ("conversations", "student_conversation"),
                ("messages", "student_message"), ("agent_runs", "student_run"),
                ("mentor_notes", "note"), ("registrations", "approved_registration"),
            ):
                assert not _exists(db, table, ids[key]), f"{table} row survived the student"
            assert db.scalar(
                select(func.count()).select_from(T["login_days"]).where(T["login_days"].c.user_id == ids["student_user"])
            ) == 0

            for table, key in (
                ("users", "faculty_user"), ("mentors", "mentor"),
                ("leave_requests", "faculty_leave"), ("conversations", "faculty_conversation"),
                ("messages", "faculty_message"), ("agent_runs", "faculty_run"),
                ("staff_signatures", "signature"), ("staff_upskilling_certs", "upskilling"),
                ("registrations", "pending_registration"),
            ):
                assert _exists(db, table, ids[key]), f"{table} row went with the student"
        finally:
            db.rollback()


@requires_db
def test_a_faculty_member_goes_and_their_mentee_is_released_not_deleted():
    with SessionLocal() as db:
        try:
            ids = _seed_one_of_each(db)
            db.execute(
                T["students"].update().where(T["students"].c.id == ids["student"]).values(mentor_id=ids["mentor"])
            )
            db.flush()
            faculty = db.get(User, ids["faculty_user"])
            plan = account_deletion.build_plan(db, faculty)
            assert plan.mentees_released == 1
            assert plan.rows.get("mentor_notes") == 1, "the note ABOUT the student goes with the group row"

            doomed, walk = account_deletion.walk_for(db, faculty)
            account_deletion.delete_rows(db, doomed, walk)

            for table, key in (
                ("users", "faculty_user"), ("mentors", "mentor"), ("mentor_notes", "note"),
                ("leave_requests", "faculty_leave"), ("staff_signatures", "signature"),
                ("staff_upskilling_certs", "upskilling"), ("conversations", "faculty_conversation"),
            ):
                assert not _exists(db, table, ids[key]), f"{table} row survived the faculty member"
            for table, key in (
                ("users", "student_user"), ("students", "student"), ("leave_requests", "student_leave"),
                ("registrations", "approved_registration"), ("registrations", "pending_registration"),
            ):
                assert _exists(db, table, ids[key]), f"{table} row went with the faculty member"
            assert db.scalar(select(T["students"].c.mentor_id).where(T["students"].c.id == ids["student"])) is None
        finally:
            db.rollback()


@requires_db
def test_the_plan_counts_what_the_delete_deletes():
    with SessionLocal() as db:
        try:
            ids = _seed_one_of_each(db)
            db.flush()
            student_user = db.get(User, ids["student_user"])
            plan = account_deletion.build_plan(db, student_user)
            doomed, walk = account_deletion.walk_for(db, student_user)
            deleted = account_deletion.delete_rows(db, doomed, walk)
            assert deleted == plan.rows, "the dry run and the real pass disagree"
            assert plan.total_rows >= 8
        finally:
            db.rollback()


def test_the_main_admin_is_never_deletable():
    admin = User(id="x", email="o@x", name="Office", role=Role.ADMIN, password_hash="sso-only")
    with pytest.raises(account_deletion.PermanentDeleteRefused):
        account_deletion.refuse_unless_deletable(admin, acting_user_id=None)


# --------------------------------------------------------- the endpoints --


@requires_db
def test_the_code_guards_the_permanent_delete(client, make_user):
    admin = make_user("del-adm", Role.ADMIN)
    faculty = make_user("del-fac", Role.MENTOR)
    victim = make_user("del-stu")
    other = make_user("del-stu2")
    with SessionLocal() as db:
        sid = db.scalar(select(Student.id).where(Student.user_id == victim.user_id))
        other_sid = db.scalar(select(Student.id).where(Student.user_id == other.user_id))

    # Only the office.
    assert client.get(f"{STUDENTS}/{sid}/delete-plan", headers=faculty.headers).status_code == 403
    assert client.post(CODE, headers=faculty.headers).status_code == 403
    assert client.post(f"{STUDENTS}/{sid}/delete", headers=victim.headers, json={"code": "123456", "reason": "x y z"}).status_code == 403

    plan = client.get(f"{STUDENTS}/{sid}/delete-plan", headers=admin.headers)
    assert plan.status_code == 200, plan.text
    assert plan.json()["kind"] == "student" and plan.json()["rows"]["users"] == 1
    assert plan.json()["consequences"], "the dialog has sentences to print"

    # A wrong code destroys nothing and names nothing.
    wrong = client.post(f"{STUDENTS}/{sid}/delete", headers=admin.headers, json={"code": "000000", "reason": "Left the college"})
    assert wrong.status_code == 403, wrong.text
    with SessionLocal() as db:
        assert db.get(Student, sid) is not None

    # The office cannot delete itself, code or no code.
    assert client.post(f"{USERS}/{admin.user_id}/delete", headers=admin.headers, json={"code": "000000", "reason": "x y z"}).status_code == 422

    mail_transport.outbox.clear()
    sent = client.post(CODE, headers=admin.headers)
    assert sent.status_code == 200, sent.text
    assert mail_transport.outbox[-1].to == admin.email, "to the office's OWN address"
    code = _last_code()

    gone = client.post(f"{STUDENTS}/{sid}/delete", headers=admin.headers, json={"code": code, "reason": "Left the college"})
    assert gone.status_code == 200, gone.text
    assert gone.json()["rows_deleted"] >= 2
    with SessionLocal() as db:
        assert db.get(Student, sid) is None and db.get(User, victim.user_id) is None
        assert db.get(Student, other_sid) is not None, "nobody else went"

    # One code, one act.
    again = client.post(f"{STUDENTS}/{other_sid}/delete", headers=admin.headers, json={"code": code, "reason": "Left too"})
    assert again.status_code == 403, again.text
    with SessionLocal() as db:
        assert db.get(Student, other_sid) is not None

    # The trail carries the act, with the snapshot the row can no longer give.
    from app.models.redesign import AuditEvent

    with SessionLocal() as db:
        row = db.scalar(
            select(AuditEvent).where(AuditEvent.entity_id == victim.user_id, AuditEvent.action == "DELETE_PERMANENT")
        )
        assert row is not None and row.before_json["email"] == victim.email


@requires_db
def test_codes_are_throttled_per_office_account(client, make_user):
    admin = make_user("del-thr", Role.ADMIN)
    for _ in range(admin_deletion.DELETE_CODES_PER_HOUR):
        assert client.post(CODE, headers=admin.headers).status_code == 200
    assert client.post(CODE, headers=admin.headers).status_code == 429


@requires_db
def test_remove_takes_a_faculty_member_off_every_screen_and_restore_brings_them_back(
    client, make_user, login
):
    admin = make_user("rm-adm", Role.ADMIN)
    faculty = make_user("rm-fac", Role.MENTOR)
    live = login(faculty.email, TEST_PASSWORD)

    def listed(removed: bool = False) -> bool:
        r = client.get(f"/api/admin/faculty{'?removed=true' if removed else ''}", headers=admin.headers)
        assert r.status_code == 200, r.text
        return faculty.user_id in [row["user_id"] for row in r.json()]

    assert listed() and not listed(removed=True)
    assert client.post(f"{USERS}/{faculty.user_id}/remove", headers=admin.headers, json={"reason": "x"}).status_code == 422
    # `live` and not `faculty.headers`: the second sign-in retired the first device.
    assert client.post(f"{USERS}/{faculty.user_id}/remove", headers=live, json={"reason": "no thanks"}).status_code == 403

    out = client.post(f"{USERS}/{faculty.user_id}/remove", headers=admin.headers, json={"reason": "Left the institution"})
    assert out.status_code == 200, out.text
    assert out.json()["removed"] is True and out.json()["delete_reason"] == "Left the institution"

    # Off the list, on the removed list, out of every door.
    assert not listed() and listed(removed=True)
    assert client.get("/api/auth/me", headers=live).status_code == 401
    refused = client.post("/api/auth/login", json={"email": faculty.email, "password": TEST_PASSWORD})
    assert refused.status_code == 403, refused.text
    client.cookies.clear()
    # Not disable-able, not enable-able, and not twice.
    assert client.post(f"{USERS}/{faculty.user_id}/disable", headers=admin.headers, json={"reason": "again"}).status_code == 409
    assert client.post(f"{USERS}/{faculty.user_id}/remove", headers=admin.headers, json={"reason": "again"}).status_code == 409
    with SessionLocal() as db:
        row = db.get(User, faculty.user_id)
        assert row is not None and row.deleted_at is not None and row.disabled_at is None

    back = client.post(f"{USERS}/{faculty.user_id}/restore", headers=admin.headers)
    assert back.status_code == 200, back.text
    assert back.json()["removed"] is False
    assert listed() and not listed(removed=True)
    assert client.post("/api/auth/login", json={"email": faculty.email, "password": TEST_PASSWORD}).status_code == 200
    client.cookies.clear()
    assert client.post(f"{USERS}/{faculty.user_id}/restore", headers=admin.headers).status_code == 409


@requires_db
def test_remove_hides_a_student_from_the_roster_and_their_mentor(client, make_user, login):
    admin = make_user("rms-adm", Role.ADMIN)
    faculty = make_user("rms-fac", Role.MENTOR)
    student = make_user("rms-stu")
    with SessionLocal() as db:
        group = Mentor(user_id=faculty.user_id)
        db.add(group)
        db.flush()
        group_id = group.id
        sid = db.scalar(select(Student.id).where(Student.user_id == student.user_id))
        db.get(Student, sid).mentor_id = group_id
        db.commit()
    faculty_live = login(faculty.email, TEST_PASSWORD)

    def on_roster(removed: bool = False) -> bool:
        r = client.get(f"{STUDENTS}{'?removed=true' if removed else ''}", headers=admin.headers)
        assert r.status_code == 200, r.text
        return sid in [row["student_id"] for row in r.json()]

    mentees = client.get("/api/mentor/mentees", headers=faculty_live)
    assert mentees.status_code == 200 and sid in [m["student_id"] for m in mentees.json()]

    out = client.post(f"{STUDENTS}/{sid}/remove", headers=admin.headers, json={"reason": "Withdrew from the programme"})
    assert out.status_code == 200, out.text
    assert not on_roster() and on_roster(removed=True)
    # The mentor's list no longer carries them - and with no other mentee, the
    # mentor no longer holds the derived functions the list is gated on.
    assert client.get("/api/mentor/mentees", headers=faculty_live).status_code == 403
    with SessionLocal() as db:
        assert db.get(Student, sid).mentor_id == group_id, "the pointer is kept for Restore"

    assert client.post(f"{STUDENTS}/{sid}/restore", headers=admin.headers).status_code == 200
    assert on_roster()
    mentees = client.get("/api/mentor/mentees", headers=faculty_live)
    assert mentees.status_code == 200 and sid in [m["student_id"] for m in mentees.json()]


# ------------------------------------------------------------ colleges --


@requires_db
def test_a_college_with_people_is_refused_and_an_empty_one_goes(client, make_user):
    admin = make_user("col-adm", Role.ADMIN)
    student = make_user("col-stu")
    tag = uuid.uuid4().hex[:6].upper()
    made = client.post("/api/admin/colleges", headers=admin.headers, json={"code": f"DEL{tag}", "name": f"Delete Me {tag}"})
    assert made.status_code == 201, made.text
    college_id = made.json()["id"]
    dep = client.post(f"/api/admin/colleges/{college_id}/departments", headers=admin.headers, json={"code": "D1", "name": "Dept One"})
    assert dep.status_code == 201, dep.text
    department_id = dep.json()["id"]

    plan = client.get(f"/api/admin/colleges/{college_id}/delete-plan", headers=admin.headers)
    assert plan.status_code == 200 and plan.json()["deletable"] is True and plan.json()["rows"]["departments"] == 1

    # File a student under it: refused, in words, and the code is not spent.
    with SessionLocal() as db:
        sid = db.scalar(select(Student.id).where(Student.user_id == student.user_id))
        db.get(Student, sid).department_id = department_id
        db.commit()
    plan = client.get(f"/api/admin/colleges/{college_id}/delete-plan", headers=admin.headers)
    assert plan.json()["deletable"] is False and "students.department_id" in plan.json()["blockers"]
    mail_transport.outbox.clear()
    assert client.post(CODE, headers=admin.headers).status_code == 200
    code = _last_code()
    refused = client.post(f"/api/admin/colleges/{college_id}/delete", headers=admin.headers, json={"code": code, "reason": "practice college"})
    assert refused.status_code == 409 and "student" in refused.text
    with SessionLocal() as db:
        db.get(Student, sid).department_id = None
        db.commit()

    gone = client.post(f"/api/admin/colleges/{college_id}/delete", headers=admin.headers, json={"code": code, "reason": "practice college"})
    assert gone.status_code == 200, gone.text
    with SessionLocal() as db:
        from app.models.institution import College, Department

        assert db.get(College, college_id) is None and db.get(Department, department_id) is None


@requires_db
def test_purge_colleges_refuses_an_unknown_keep_code(capsys):
    from app import purge_colleges

    assert purge_colleges.main(["--keep", "NO-SUCH-COLLEGE-" + uuid.uuid4().hex[:4]]) == 2
    assert purge_colleges.main(["--keep", "X", "--apply"]) == 2, "--apply without the sentence"
