"""The office's student record page: its new reads, and who they open for.

2026-10-15. The owner asked for the resume, skills, leaderboard and headline
numbers on the Main Admin's view of one student, and for the office to choose
who else may open those records. Their decision: a faculty member given "View
student records" for a student, batch, department or college may READ those
students' records, read only, whether or not they mentor them.

WHAT THIS MODULE HOLDS DOWN, in the order a break would be quietest:

1. THE WIDENING IS ONLY THE OFFICE'S SCOPED GRANT. A programme-wide grant
   still reaches only the holder's mentees (it was written when that is what
   it meant), a grant on another department reaches nobody here, and a
   revoked or lapsed grant closes the door at once.
2. IT IS READ ONLY. The same grant does not open a single mentor endpoint,
   read or write: those never pass the new keyword.
3. A DOWNLOAD IS RECORDED AND A VIEW IS NOT.
4. A FILE MUST BE THIS STUDENT'S. An upload or resume id that belongs to
   somebody else is the same 404 as one that does not exist.
5. THE LEADERBOARD NAMES NOBODY THE VIEWER COULD NOT OPEN. A grant on one
   student is not a grant on their classmates, so the rows around them on a
   board are cut to the students the viewer may open; the office sees all.
6. NO LINK LEADS TO A REFUSAL. The "Student records" list and the Mentee
   Log's "Full record" link offer exactly the students the page opens.
7. A MENTOR OPENS THEIR OWN MENTEES (2026-10-16). Mentoring a student is
   enough to read their record; it reaches nobody else and writes nothing.
8. WHO ELSE CAN OPEN IT IS THE OFFICE'S (2026-10-17). The Access tab's reads
   (the access list, recent sign-ins, the record's history and the reasons
   for a disable or a removal) answer the Main Admin alone; a grant or a
   mentee opens the record and not that.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from conftest import TEST_PASSWORD, requires_db

from app.db import SessionLocal
from app.models.account_events import LoginEvent
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, AcademicCourse, College, Department
from app.models.job import DegreeLevel
from app.models.redesign import AuditEvent
from app.models.resume import Resume, ResumeStatus
from app.models.student_profile import StudentProfile
from app.models.upload import Upload, UploadKind, UploadStatus
from app.models.user import LoginDay, Mentor, Role, Student, User
from app.routers.registration import APPLICATION_CV_TITLE
from app.routers.student import clear_leaderboard_cache
from app.security import hash_password

KEY = "admin.student_records"
CV_BYTES = b"%PDF-1.4\n% a CV, as far as this test is concerned\n%%EOF\n"


def _base(student_id: str) -> str:
    return f"/api/admin/students/{student_id}"


@pytest.fixture
def world(tmp_document_store):
    """A college with two departments, a student in one of them with a CV and
    a built resume, their faculty mentor, a faculty member who mentors nobody,
    and a second student in the OTHER department."""
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        college = College(code=f"REC{tag.upper()}", name="Record College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        home = Department(college_id=college.id, name="Record Home", code=f"RH{tag}")
        other = Department(college_id=college.id, name="Record Other", code=f"RO{tag}")
        db.add_all([home, other])
        db.flush()
        course = AcademicCourse(
            department_id=home.id, code=f"RC{tag}", name="Record Programme",
            duration_months=24, total_semesters=4,
        )
        db.add(course)
        db.flush()
        batch = Cohort(
            code=f"RB{tag}", name="2025-27", batch_label="2025-27",
            degree_level=DegreeLevel.PG, department_id=home.id, course_id=course.id,
            start_date=datetime(2025, 7, 1, tzinfo=timezone.utc),
            end_date=datetime(2027, 6, 30, tzinfo=timezone.utc),
        )
        db.add(batch)
        db.flush()

        def account(label: str, role: Role) -> User:
            user = User(
                email=f"rec-{label}-{tag}@bgscet.ac.in", name=f"Record {label.title()}",
                role=role, password_hash=hash_password(TEST_PASSWORD),
            )
            db.add(user)
            db.flush()
            return user

        student_user = account("student", Role.STUDENT)
        neighbour_user = account("neighbour", Role.STUDENT)
        mentor_user = account("mentor", Role.MENTOR)
        stranger_user = account("stranger", Role.MENTOR)
        group = Mentor(user_id=mentor_user.id)
        db.add(group)
        db.flush()
        student = Student(
            user_id=student_user.id, usn=f"1REC{tag.upper()}", cohort_id=batch.id,
            mentor_id=group.id, current_semester=3,
        )
        neighbour = Student(
            user_id=neighbour_user.id, usn=f"1RECN{tag.upper()}", department_id=other.id,
        )
        db.add_all([student, neighbour])
        db.flush()

        stored = f"rec-cv-{tag}.pdf"
        (tmp_document_store / stored).write_bytes(CV_BYTES)
        cv = Upload(
            student_id=student.id, kind=UploadKind.RESUME, title=APPLICATION_CV_TITLE,
            original_name="Record_CV.pdf", stored_name=stored, mime_type="application/pdf",
            size_bytes=len(CV_BYTES), status=UploadStatus.VERIFIED,
        )
        neighbour_cv = Upload(
            student_id=neighbour.id, kind=UploadKind.RESUME, title=APPLICATION_CV_TITLE,
            original_name="Neighbour_CV.pdf", stored_name=f"rec-ncv-{tag}.pdf",
            mime_type="application/pdf", size_bytes=10, status=UploadStatus.VERIFIED,
        )
        built = Resume(
            student_id=student.id, version=2, title="Finance Analyst",
            status=ResumeStatus.FINALISED, markdown="# Record Student\n\n## Career Objective\n\nTo test.",
        )
        neighbour_built = Resume(student_id=neighbour.id, version=1, title="Other", markdown="# Other")
        db.add_all([cv, neighbour_cv, built, neighbour_built])
        db.commit()

        made = {
            "college": college.id, "home": home.id, "other": other.id, "course": course.id,
            "batch": batch.id, "student": student.id, "neighbour": neighbour.id,
            "group": group.id, "cv": cv.id, "neighbour_cv": neighbour_cv.id,
            "built": built.id, "neighbour_built": neighbour_built.id,
            "users": [student_user.id, neighbour_user.id, mentor_user.id, stranger_user.id],
            "mentor_user": mentor_user.id, "stranger_user": stranger_user.id,
            "mentor_email": mentor_user.email, "stranger_email": stranger_user.email,
            "student_email": student_user.email,
        }

    yield made

    with SessionLocal() as db:
        students = [made["student"], made["neighbour"]]
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id.in_(made["users"])))
        db.execute(delete(AuditEvent).where(AuditEvent.entity_id.in_(students)))
        db.execute(delete(Upload).where(Upload.student_id.in_(students)))
        db.execute(delete(Resume).where(Resume.student_id.in_(students)))
        db.execute(delete(StudentProfile).where(StudentProfile.student_id.in_(students)))
        db.execute(delete(LoginEvent).where(LoginEvent.user_id.in_(made["users"])))
        db.execute(delete(LoginDay).where(LoginDay.user_id.in_(made["users"])))
        db.execute(delete(Student).where(Student.id.in_(students)))
        db.execute(delete(Mentor).where(Mentor.id == made["group"]))
        db.execute(delete(User).where(User.id.in_(made["users"])))
        db.execute(delete(Cohort).where(Cohort.id == made["batch"]))
        db.execute(delete(AcademicCourse).where(AcademicCourse.id == made["course"]))
        db.execute(delete(Department).where(Department.id.in_([made["home"], made["other"]])))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


@pytest.fixture
def grant():
    """A grant written on the row, for test_admin_student_360's reason: the
    endpoint writes a deputy's `carries_pii` grant pending, and these tests are
    about reach, not about who signed."""
    made: list[str] = []

    def _grant(user_id: str, level: ScopeLevel | None = None, target: str | None = None,
               expires_at: datetime | None = None) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=KEY, subject_kind=SubjectKind.USER, subject_user_id=user_id,
                scope_level=level, scope_id=target, expires_at=expires_at,
                reason="Placement coordinator for these students, in a test.",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made or [""])))
        db.commit()


READS = ("/360", "/record")

#: The Access tab's own read. No grant and no mentee opens it (2026-10-17).
OFFICE_ONLY = ("/access",)


# ------------------------------------------------------- 1. the widening --


@requires_db
def test_a_scoped_grant_opens_a_student_the_holder_does_not_mentor(client, login, world, grant):
    stranger = login(world["stranger_email"], TEST_PASSWORD)
    for path in READS:
        assert client.get(_base(world["student"]) + path, headers=stranger).status_code == 403

    grant(world["stranger_user"], ScopeLevel.DEPARTMENT, world["home"])
    for path in READS:
        response = client.get(_base(world["student"]) + path, headers=stranger)
        assert response.status_code == 200, (path, response.text)
    for path in OFFICE_ONLY:
        refused = client.get(_base(world["student"]) + path, headers=stranger)
        assert refused.status_code == 403 and "Main Admin" in refused.text, (path, refused.text)
    # The department named, and nobody outside it: the neighbour sits in the
    # other department, so the same grant is a 403 there.
    assert client.get(_base(world["neighbour"]) + "/360", headers=stranger).status_code == 403


@requires_db
def test_a_programme_wide_grant_still_reaches_only_the_holders_mentees(client, login, world, grant):
    """Every grant written before 2026-10-15 is programme-wide and meant "your
    own mentees". Reading it as "every student" would widen all of them on
    deploy, so the new rule honours a SCOPED grant only."""
    grant(world["stranger_user"])
    stranger = login(world["stranger_email"], TEST_PASSWORD)
    for path in READS:
        refused = client.get(_base(world["student"]) + path, headers=stranger)
        assert refused.status_code == 404, path
        assert "mentor scope" in refused.text

    grant(world["mentor_user"])
    mentor = login(world["mentor_email"], TEST_PASSWORD)
    assert client.get(_base(world["student"]) + "/record", headers=mentor).status_code == 200


@requires_db
def test_a_one_student_grant_opens_that_student_and_no_other(client, login, world, grant):
    grant(world["stranger_user"], ScopeLevel.STUDENT, world["student"])
    stranger = login(world["stranger_email"], TEST_PASSWORD)
    assert client.get(_base(world["student"]) + "/record", headers=stranger).status_code == 200
    assert client.get(_base(world["neighbour"]) + "/record", headers=stranger).status_code == 403


@requires_db
def test_a_revoked_or_lapsed_grant_closes_the_record_at_once(client, login, world, grant):
    stranger = login(world["stranger_email"], TEST_PASSWORD)
    live = grant(world["stranger_user"], ScopeLevel.COLLEGE, world["college"])
    assert client.get(_base(world["student"]) + "/360", headers=stranger).status_code == 200

    with SessionLocal() as db:
        row = db.get(CapabilityGrant, live)
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
    assert client.get(_base(world["student"]) + "/360", headers=stranger).status_code == 403

    grant(
        world["stranger_user"], ScopeLevel.COLLEGE, world["college"],
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    assert client.get(_base(world["student"]) + "/360", headers=stranger).status_code == 403


# ------------------------------------------------------- 2. read only --


@requires_db
def test_the_records_grant_opens_no_mentor_endpoint(client, login, world, grant):
    """The keyword is passed by this page's reads alone. The mentor's own
    reads and writes about the same student are still refused: a 404 from
    rule 2, or a 403 where the endpoint asks for a mentor's capability first."""
    grant(world["stranger_user"], ScopeLevel.DEPARTMENT, world["home"])
    stranger = login(world["stranger_email"], TEST_PASSWORD)
    sid = world["student"]

    for path in (
        f"/api/mentor/students/{sid}/notes",
        f"/api/mentor/students/{sid}/badges",
        f"/api/mentor/students/{sid}/interviews",
        f"/api/mentor/uploads/{world['cv']}/file",
    ):
        assert client.get(path, headers=stranger).status_code in (403, 404), path
    wrote = client.post(
        f"/api/mentor/students/{sid}/notes", headers=stranger,
        json={"note_text": "This must not be written."},
    )
    assert wrote.status_code in (403, 404), wrote.text


# ------------------------------------------------------- 3. files --


@requires_db
def test_the_application_cv_is_marked_and_readable(client, make_user, world):
    admin = make_user("rec-admin", Role.ADMIN)
    documents = client.get(_base(world["student"]) + "/360", headers=admin.headers).json()["documents"]
    cv = next(d for d in documents if d["id"] == world["cv"])
    assert cv["from_application"] is True

    response = client.get(_base(world["student"]) + f"/files/{world['cv']}", headers=admin.headers)
    assert response.status_code == 200
    assert response.content == CV_BYTES
    # Never inline: a student's PDF must not render in this origin.
    assert response.headers["content-disposition"].startswith("attachment")


@requires_db
def test_a_download_is_recorded_and_a_view_is_not(client, make_user, world):
    admin = make_user("rec-admin", Role.ADMIN)
    url = _base(world["student"]) + f"/files/{world['cv']}"

    def downloads() -> int:
        with SessionLocal() as db:
            return int(db.scalar(
                select(func.count()).select_from(AuditEvent).where(
                    AuditEvent.entity_id == world["student"],
                    AuditEvent.action == "DOWNLOADED",
                )
            ) or 0)

    assert client.get(url, headers=admin.headers).status_code == 200
    assert downloads() == 0
    assert client.get(url + "?download=true", headers=admin.headers).status_code == 200
    assert downloads() == 1
    trail = client.get(_base(world["student"]) + "/360", headers=admin.headers).json()["recent_audit"]
    assert trail[0]["action"] == "DOWNLOADED"


@requires_db
def test_a_file_or_resume_of_another_student_is_not_found(client, make_user, world):
    admin = make_user("rec-admin", Role.ADMIN)
    base = _base(world["student"])
    assert client.get(base + f"/files/{world['neighbour_cv']}", headers=admin.headers).status_code == 404
    assert client.get(base + f"/resumes/{world['neighbour_built']}/pdf", headers=admin.headers).status_code == 404

    pdf = client.get(base + f"/resumes/{world['built']}/pdf", headers=admin.headers)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


# ------------------------------------------------------- the panels --


@requires_db
def test_the_record_carries_every_panel_the_page_draws(client, make_user, world):
    admin = make_user("rec-admin", Role.ADMIN)
    body = client.get(_base(world["student"]) + "/record", headers=admin.headers).json()
    assert [r["title"] for r in body["resumes"]] == ["Finance Analyst"]
    assert body["skills"]["badge_total"] > 0
    assert body["ledger"]["window_days"] == 14
    assert body["interviews"] == []
    boards = body["leaderboards"]
    assert boards["hidden"] is False
    assert boards["scope"] == "batch"
    assert [b["board"] for b in boards["boards"]] == ["overall", "skills", "vtu", "streak", "mocks"]
    # Nothing recorded on the VTU board for this student: no rank, never 0.
    vtu = next(b for b in boards["boards"] if b["board"] == "vtu")
    assert vtu["rank"] is None and vtu["value_label"] is None


@requires_db
def test_a_student_who_left_the_boards_shows_no_rank(client, make_user, world):
    admin = make_user("rec-admin", Role.ADMIN)
    with SessionLocal() as db:
        db.add(StudentProfile(student_id=world["student"], leaderboard_opt_out=True))
        db.commit()
    boards = client.get(_base(world["student"]) + "/record", headers=admin.headers).json()["leaderboards"]
    assert boards["hidden"] is True and boards["boards"] == []


@requires_db
def test_the_leaderboard_names_only_students_the_viewer_may_open(client, login, make_user, world, grant):
    """Found in review of the promotion (2026-10-15): the rows around a
    student on a board carry each classmate's name and value, the VTU board's
    value is their CGPA, and a grant on ONE student is not a grant on them."""
    tag = uuid.uuid4().hex[:6]
    student_user = world["users"][0]
    with SessionLocal() as db:
        classmate_user = User(
            email=f"rec-classmate-{tag}@bgscet.ac.in", name="Record Classmate",
            role=Role.STUDENT, password_hash=hash_password(TEST_PASSWORD),
        )
        db.add(classmate_user)
        db.flush()
        classmate = Student(user_id=classmate_user.id, usn=f"1RECC{tag.upper()}", cohort_id=world["batch"])
        db.add(classmate)
        today = date.today()
        # Both on the sign-in board, the classmate ranked first.
        db.add_all([LoginDay(user_id=classmate_user.id, day=today - timedelta(days=i)) for i in range(3)])
        db.add(LoginDay(user_id=student_user, day=today))
        db.commit()
        classmate_id, classmate_user_id = classmate.id, classmate_user.id
    clear_leaderboard_cache()

    def named(headers) -> set[str]:
        boards = client.get(_base(world["student"]) + "/record", headers=headers).json()["leaderboards"]
        streak = next(b for b in boards["boards"] if b["board"] == "streak")
        assert streak["rank"] == 2 and streak["ranked"] == 2, "the student's own standing is always given"
        return {row["student_id"] for b in boards["boards"] for row in b["rows"]}

    try:
        admin = make_user("rec-admin", Role.ADMIN)
        assert classmate_id in named(admin.headers), "the office sees the whole board"

        grant(world["stranger_user"], ScopeLevel.STUDENT, world["student"])
        stranger = login(world["stranger_email"], TEST_PASSWORD)
        assert named(stranger) == {world["student"]}

        mentor = login(world["mentor_email"], TEST_PASSWORD)  # their own mentee, no grant
        assert named(mentor) == {world["student"]}

        grant(world["stranger_user"], ScopeLevel.COHORT, world["batch"])
        assert classmate_id in named(stranger), "a grant on the batch reaches the classmate"
    finally:
        with SessionLocal() as db:
            db.execute(delete(LoginDay).where(LoginDay.user_id == classmate_user_id))
            db.execute(delete(Student).where(Student.id == classmate_id))
            db.execute(delete(User).where(User.id == classmate_user_id))
            db.commit()
        clear_leaderboard_cache()


@requires_db
def test_the_access_list_names_everybody_who_can_open_the_record(client, make_user, login, world, grant):
    admin = make_user("rec-admin", Role.ADMIN)
    grant_id = grant(world["stranger_user"], ScopeLevel.DEPARTMENT, world["home"])
    grant(world["stranger_user"])  # programme-wide: opens nothing here, so not listed

    body = client.get(_base(world["student"]) + "/access", headers=admin.headers).json()
    kinds = [(h["kind"], h["name"], h["holder_id"]) for h in body["holders"]]
    assert ("mentor", "Record Mentor", world["mentor_user"]) in kinds
    granted = [h for h in body["holders"] if h["kind"] == "grant"]
    assert [(h["name"], h["reach_label"], h["grant_id"], h["holder_id"]) for h in granted] == [
        ("Record Stranger", "Record Home", grant_id, world["stranger_user"])
    ]
    assert body["can_manage"] is True
    assert [o["scope_level"] for o in body["reach_options"]] == [
        "STUDENT", "COHORT", "DEPARTMENT", "COLLEGE",
    ]
    # The batch is named by its course and year, never the bare year.
    assert body["reach_options"][1]["label"] == "Record Programme · 2025-27"

    assert granted[0]["reason"], "the office reads the reason for each grant"

    # Nobody else reads the list at all (2026-10-17): not the faculty member
    # the grant names, and not the student's own mentor.
    for email in (world["stranger_email"], world["mentor_email"]):
        refused = client.get(_base(world["student"]) + "/access", headers=login(email, TEST_PASSWORD))
        assert refused.status_code == 403, (email, refused.text)
        assert refused.json()["detail"] == "Main Admin access required."


@requires_db
def test_the_access_tab_half_of_the_record_is_the_offices(client, make_user, login, world, grant):
    """`GET .../360` carries the sign-ins, the record's history and the reasons
    the office wrote when it disabled the account. They are drawn on the Access
    tab, so a reader on a grant or a mentee gets the record without them, and
    still learns THAT the account is disabled."""
    admin = make_user("rec-admin", Role.ADMIN)
    login(world["student_email"], TEST_PASSWORD)  # a sign-in on the student's list
    assert client.get(
        _base(world["student"]) + f"/files/{world['cv']}?download=true", headers=admin.headers
    ).status_code == 200  # a row on the student's history
    with SessionLocal() as db:
        row = db.scalar(select(Student).where(Student.id == world["student"]))
        account = db.get(User, row.user_id)
        account.disabled_at = datetime.now(timezone.utc)
        account.disabled_by_user_id = admin.user_id
        account.disable_reason = "E2E: under investigation for a shared password"
        db.commit()

    office = client.get(_base(world["student"]) + "/360", headers=admin.headers).json()
    assert office["login"]["recent_sign_ins"], "the office reads the sign-ins"
    assert office["recent_audit"], "the office reads the history"
    assert office["login"]["disable_reason"].startswith("E2E: under investigation")

    grant(world["stranger_user"], ScopeLevel.STUDENT, world["student"])
    for email in (world["stranger_email"], world["mentor_email"]):
        seen = client.get(_base(world["student"]) + "/360", headers=login(email, TEST_PASSWORD))
        assert seen.status_code == 200, (email, seen.text)
        body = seen.json()
        assert body["login"]["recent_sign_ins"] == [], email
        assert body["recent_audit"] == [], email
        assert body["login"]["disabled"] is True, "the fact stays; the reason goes"
        assert body["login"]["disable_reason"] is None, email
        assert body["login"]["disabled_by_name"] is None, email


# ------------------------------------------------------- the finding list --


@requires_db
def test_the_records_list_is_exactly_the_students_the_page_opens(client, login, make_user, world, grant):
    """A faculty member given access has no roster; this list is how they find
    the students, so it must name exactly the ones the record opens for."""
    def listed(headers) -> set[str]:
        response = client.get("/api/admin/student-records", headers=headers)
        assert response.status_code == 200, response.text
        return {row["student_id"] for row in response.json()}

    stranger = login(world["stranger_email"], TEST_PASSWORD)
    assert client.get("/api/admin/student-records", headers=stranger).status_code == 403

    grant(world["stranger_user"])  # programme-wide: their mentees, of whom they have none
    assert world["student"] not in listed(stranger)

    grant(world["stranger_user"], ScopeLevel.DEPARTMENT, world["home"])
    seen = listed(stranger)
    assert world["student"] in seen and world["neighbour"] not in seen

    # A mentor with no grant at all: mentoring the student opens their record
    # (2026-10-16), so the list offers their mentee and nobody else.
    mentor = login(world["mentor_email"], TEST_PASSWORD)
    assert client.get(_base(world["student"]) + "/360", headers=mentor).status_code == 200
    seen = listed(mentor)
    assert world["student"] in seen and world["neighbour"] not in seen

    # A grant naming ANOTHER department adds that department's students and
    # takes nothing away from their own mentee (review of #180 asked that the
    # list and the page agree; they still do).
    grant(world["mentor_user"], ScopeLevel.DEPARTMENT, world["other"])
    mentor = login(world["mentor_email"], TEST_PASSWORD)
    assert client.get(_base(world["neighbour"]) + "/360", headers=mentor).status_code == 200
    assert {world["student"], world["neighbour"]} <= listed(mentor)

    admin = make_user("rec-admin", Role.ADMIN)
    everyone = client.get("/api/admin/student-records?q=Record", headers=admin.headers).json()
    assert {world["student"], world["neighbour"]} <= {row["student_id"] for row in everyone}


def test_the_mentee_log_offers_the_full_record_only_where_it_opens(client, login, world, grant):
    """The Mentee Log draws "Full record" from `record_openable`, so the flag
    must agree with the page. Since 2026-10-16 the page opens for a mentor's
    own mentee with no grant ("mentors' mentee records are not visible" was the
    owner's report), so the link is there from the start, and a grant for
    another department does not take it away."""
    record = _base(world["student"]) + "/360"

    def check(page_status: int) -> bool:
        """The record page's answer for this mentor, then the flag beside it."""
        mentor = login(world["mentor_email"], TEST_PASSWORD)
        assert client.get(record, headers=mentor).status_code == page_status
        response = client.get("/api/mentor/mentees", headers=mentor)
        assert response.status_code == 200, response.text
        rows = {row["student_id"]: row for row in response.json()}
        return rows[world["student"]]["record_openable"]

    assert check(200) is True

    grant(world["mentor_user"], ScopeLevel.DEPARTMENT, world["other"])
    assert check(200) is True


@requires_db
def test_a_mentor_opens_their_own_mentees_record_and_nobody_elses(client, login, world):
    """THE FIX, from the mentor's side: no grant, every read of the page opens
    for their own mentee, the auth payload carries the key so the route guard
    and the "Student records" row let them in, and a student outside their
    group is refused exactly as before (rule 2, not the capability)."""
    mentor = login(world["mentor_email"], TEST_PASSWORD)
    me = client.get("/api/auth/me", headers=mentor).json()
    assert KEY in me["capabilities"]
    for path in READS:
        response = client.get(_base(world["student"]) + path, headers=mentor)
        assert response.status_code == 200, (path, response.text)
    for path in OFFICE_ONLY:
        assert client.get(_base(world["student"]) + path, headers=mentor).status_code == 403, path
    cv = client.get(_base(world["student"]) + f"/files/{world['cv']}", headers=mentor)
    assert cv.status_code == 200, cv.text

    refused = client.get(_base(world["neighbour"]) + "/360", headers=mentor)
    assert refused.status_code == 404 and "mentor scope" in refused.text

    # Read only: the record's key opens no write. The roster editor is a
    # different key, which mentoring does not bring.
    edited = client.patch(
        f"/api/admin/students/{world['student']}", headers=mentor, json={"name": "Renamed"}
    )
    assert edited.status_code == 403, edited.text

    # A faculty member who mentors nobody holds nothing of the kind.
    stranger = login(world["stranger_email"], TEST_PASSWORD)
    assert KEY not in client.get("/api/auth/me", headers=stranger).json()["capabilities"]
    assert client.get(_base(world["student"]) + "/360", headers=stranger).status_code == 403
