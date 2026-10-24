"""B8.1 — the office's spreadsheet, read, judged and written.

Each test below is the sentence it would break into if it were deleted:

1. A PREVIEW WRITES NOTHING A STUDENT CAN SEE. Delete
   `test_a_preview_judges_every_line_and_writes_no_student_record` and the
   screen's whole promise — "look before you import" — becomes a button that
   has already imported.
2. AN IMPORT IS AN UPSERT. `attendance_records`, `semester_results` and
   `subject_marks` each carry a unique constraint that an INSERT-only importer
   hits on the FIRST correction anybody uploads. Delete
   `test_marks_are_upserted_so_a_correction_can_be_re-imported` and the normal
   case is a 500.
3. A CORRECTION THAT SHRINKS A NUMBER MUST SHRINK THE RECORD. Delete
   `test_re_importing_fewer_sessions_removes_the_tail` and a batch corrected
   from 30 sessions to 20 keeps ten absences the office has withdrawn.
4. THE VALIDATION IS PER LINE AND THE FILE STILL IMPORTS. Delete
   `test_one_bad_line_does_not_refuse_the_other_eighty_five` and an examination
   section with one typo has to fix the sheet before anything lands.
5. THE SEMESTER IS BOUNDED BY THE COURSE (4a's `total_semesters`). Delete
   `test_a_semester_past_the_end_of_the_programme_is_refused` and a four-semester
   MBA acquires a semester 7 that its students can never sit.
6. THE ERROR REPORT WITHHOLDS THE NAME AND KEEPS THE USN. Delete
   `test_the_error_report_withholds_the_name_without_the_roster_function` and a
   grant of Data imports becomes a grant of the roster, which is B14's rule and
   the reason `carries_personal_columns` exists.
7. A FILE THAT CANNOT BE READ LEAVES A RECEIPT. Delete
   `test_an_unreadable_file_leaves_a_failed_receipt` and "I uploaded it and
   nothing happened" has no answer anywhere in the product.
8. THE TEMPLATE IS THE PARSER'S OWN COLUMN LIST. Delete
   `test_the_template_is_a_file_this_importer_accepts` and the workbook REEP
   hands out drifts from the workbook REEP reads, one renamed heading at a time.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, func, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.academics import SemesterResult, SubjectMark
from app.models.attendance import AttendanceRecord
from app.models.cohort import Cohort
from app.models.course import Course, CourseModel, Dimension
from app.models.data_import import ImportRow, ImportRun
from app.models.governance import CapabilityGrant, SubjectKind
from app.models.institution import STATUS_ACTIVE, AcademicCourse, College, Department
from app.models.job import DegreeLevel
from app.models.user import Role, Stage, Student, User
from app.seed_roster import SSO_ONLY_PASSWORD_HASH

MARKS_HEADER = "usn,subject_code,subject_name,credits,internal,external,sgpa,cgpa,live_backlogs"
ATTENDANCE_HEADER = "usn,subject_code,sessions_held,sessions_attended"


@pytest.fixture
def batch():
    """One college, one four-semester course, one batch, two students, two subjects.

    The smallest shape in which every rule this module pins can fail: a USN that
    IS in the batch and one that is not, a subject code that is in the catalogue
    and one that is not, and a course whose `total_semesters` is small enough
    that a plausible semester is past its end.
    """
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        college = College(code=f"IMPT{tag.upper()}", name=f"Import College {tag}",
                          status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        dept = Department(college_id=college.id, code=f"ID{tag}", name=f"Import Dept {tag}")
        db.add(dept)
        db.flush()
        course = AcademicCourse(
            department_id=dept.id, code=f"MBA{tag.upper()}", name=f"MBA {tag}",
            duration_months=24, total_semesters=4,
        )
        db.add(course)
        db.flush()
        cohort = Cohort(
            code=f"IMPB-{tag}", name=f"Import Batch {tag}", batch_label="2025-27",
            department_id=dept.id, course_id=course.id, degree_level=DegreeLevel.PG,
            start_date=datetime(2025, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2027, 7, 31, tzinfo=timezone.utc),
        )
        db.add(cohort)
        db.flush()
        for index in (1, 2):
            user = User(
                email=f"imp-{tag}-{index}@bgscet.ac.in", name=f"Import Student {index} {tag}",
                role=Role.STUDENT, password_hash=SSO_ONLY_PASSWORD_HASH,
            )
            db.add(user)
            db.flush()
            student = Student(user_id=user.id, cohort_id=cohort.id,
                              usn=f"1IM{tag.upper()}0{index}")
            db.add(student)
            db.flush()
            made[f"user_{index}"] = user.id
            made[f"student_{index}"] = student.id
            made[f"usn_{index}"] = student.usn
        subjects = []
        for suffix, name in (("A", "Managerial Economics"), ("B", "Business Statistics")):
            code = f"{tag.upper()}{suffix}"
            subjects.append(code)
            db.add(Course(code=code, name=name, stage=Stage.EXCEL,
                          dimension=Dimension.THINKING, semester=1,
                          model_type=CourseModel.INSTRUCTOR_LED))
        made["subject_a"], made["subject_b"] = subjects
        made["college"] = college.id
        made["dept"] = dept.id
        made["course"] = course.id
        made["cohort"] = cohort.id
        db.commit()

    yield made

    student_ids = [made["student_1"], made["student_2"]]
    with SessionLocal() as db:
        result_ids = list(
            db.scalars(
                select(SemesterResult.id).where(SemesterResult.student_id.in_(student_ids))
            ).all()
        )
        if result_ids:
            db.execute(delete(SubjectMark).where(SubjectMark.semester_result_id.in_(result_ids)))
        db.execute(delete(SemesterResult).where(SemesterResult.student_id.in_(student_ids)))
        db.execute(delete(AttendanceRecord).where(AttendanceRecord.student_id.in_(student_ids)))
        run_ids = list(
            db.scalars(select(ImportRun.id).where(ImportRun.cohort_id == made["cohort"])).all()
        )
        if run_ids:
            db.execute(delete(ImportRow).where(ImportRow.run_id.in_(run_ids)))
            db.execute(delete(ImportRun).where(ImportRun.id.in_(run_ids)))
        db.execute(delete(Student).where(Student.id.in_(student_ids)))
        db.execute(delete(User).where(User.id.in_([made["user_1"], made["user_2"]])))
        db.execute(delete(Course).where(Course.code.in_([made["subject_a"], made["subject_b"]])))
        db.execute(delete(Cohort).where(Cohort.id == made["cohort"]))
        db.execute(delete(AcademicCourse).where(AcademicCourse.id == made["course"]))
        db.execute(delete(Department).where(Department.id == made["dept"]))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


@pytest.fixture
def programme_grant():
    """A programme-wide grant written as a ROW, taken back afterwards.

    A ROW rather than `POST /api/admin/governance/grants`, so the fixture does
    not depend on who is granting: `admin.imports` carries PII, so a deputy's
    grant lands `pending_approval` and holds nothing (B2.4) while the Main
    Admin's is live at once — and a test that turned on that difference would
    be about the approval rule, which has its own module.
    `tests/test_imports_schema.py` and `tests/test_scoped_lists.py` say the same.
    """
    made: list[str] = []

    def _grant(user_id: str, key: str) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key,
                subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                reason="the import endpoint tests need a live grant, twenty characters plus",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


def _preview(client, headers, batch, kind, body, *, filename=None, semester=1):
    data = {"kind": kind, "cohort_id": batch["cohort"]}
    if semester is not None:
        data["semester"] = str(semester)
    return client.post(
        "/api/admin/imports/preview",
        headers=headers,
        data=data,
        files={"file": (filename or f"{kind}.csv", body.encode("utf-8"), "text/csv")},
    )


def _marks_csv(batch, *, rows=None) -> str:
    rows = rows or [
        f"{batch['usn_1']},{batch['subject_a']},Managerial Economics,4,42,38,8.1,7.9,0",
        f"{batch['usn_2']},{batch['subject_a']},Managerial Economics,4,31,29,7.2,7.0,1",
    ]
    return "\n".join([MARKS_HEADER, *rows]) + "\n"


# ------------------------------------------------------------- the preview --


@requires_db
def test_a_preview_judges_every_line_and_writes_no_student_record(client, make_user, batch):
    """The whole point of the screen: look at the file before it lands."""
    admin = make_user(f"imp-prev-{batch['tag']}", Role.ADMIN)
    r = _preview(client, admin.headers, batch, "marks", _marks_csv(batch))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["run"]["status"] == "previewed"
    assert body["run"]["rows_total"] == 2
    assert body["run"]["rows_ok"] == 2
    assert body["run"]["rows_rejected"] == 0
    assert body["rows_to_write"] == 2
    assert [row["verdict"] for row in body["rows"]] == ["ok", "ok"]
    # The line numbers are the OPERATOR'S: a header is line 1.
    assert [row["line_no"] for row in body["rows"]] == [2, 3]
    assert body["rows"][0]["usn"] == batch["usn_1"]
    assert body["rows"][0]["total"] == 80

    with SessionLocal() as db:
        assert db.scalar(
            select(func.count()).select_from(SemesterResult).where(
                SemesterResult.student_id == batch["student_1"]
            )
        ) == 0, "the preview wrote a student record"


@requires_db
def test_one_bad_line_does_not_refuse_the_other_eighty_five(client, make_user, batch):
    """A USN outside the batch, a subject outside the catalogue, a mark over 100
    and a duplicate — four refusals, and the good line still imports."""
    admin = make_user(f"imp-bad-{batch['tag']}", Role.ADMIN)
    csv = _marks_csv(
        batch,
        rows=[
            f"{batch['usn_1']},{batch['subject_a']},Economics,4,42,38,8.1,7.9,0",
            f"NOTINBATCH01,{batch['subject_a']},Economics,4,40,40,8.0,8.0,0",
            f"{batch['usn_2']},ZZZNOTASUBJECT,Economics,4,40,40,8.0,8.0,0",
            f"{batch['usn_2']},{batch['subject_a']},Economics,4,140,38,8.0,8.0,0",
            f"{batch['usn_1']},{batch['subject_a']},Economics,4,10,10,8.1,7.9,0",
        ],
    )
    r = _preview(client, admin.headers, batch, "marks", csv)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["run"]["rows_total"] == 5
    assert body["run"]["rows_ok"] == 1
    assert body["run"]["rows_rejected"] == 4
    verdicts = {row["line_no"]: (row["verdict"], row["message"]) for row in body["rows"]}
    assert verdicts[2][0] == "ok"
    assert "not a student in this batch" in verdicts[3][1]
    assert "not a subject in the catalogue" in verdicts[4][1]
    assert "outside 0-100" in verdicts[5][1]
    assert "appears twice" in verdicts[6][1]

    applied = client.post(
        f"/api/admin/imports/{body['run']['id']}/apply", headers=admin.headers
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["rows_applied"] == 1


@requires_db
def test_a_semester_past_the_end_of_the_programme_is_refused(client, make_user, batch):
    """4a's `academic_courses.total_semesters`, enforced at the importer."""
    admin = make_user(f"imp-sem-{batch['tag']}", Role.ADMIN)
    r = _preview(client, admin.headers, batch, "marks", _marks_csv(batch), semester=7)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "runs 4 semesters" in detail
    with SessionLocal() as db:
        assert db.scalar(
            select(func.count()).select_from(ImportRun).where(
                ImportRun.cohort_id == batch["cohort"]
            )
        ) == 0, "a refusal before the file was read still wrote a receipt"


@requires_db
def test_a_marks_import_must_name_its_semester(client, make_user, batch):
    """`semester_results` is keyed on it; there is nowhere else to put the row."""
    admin = make_user(f"imp-nosem-{batch['tag']}", Role.ADMIN)
    r = _preview(client, admin.headers, batch, "marks", _marks_csv(batch), semester=None)
    assert r.status_code == 422, r.text
    assert "semester" in r.json()["detail"].lower()


@requires_db
def test_an_unreadable_file_leaves_a_failed_receipt(client, make_user, batch):
    """A run with no rows and an `error` — the state the five counters cannot
    describe, and the reason `ImportRun.error` exists."""
    admin = make_user(f"imp-junk-{batch['tag']}", Role.ADMIN)
    r = _preview(client, admin.headers, batch, "marks", "not a spreadsheet",
                 filename="results.docx")
    assert r.status_code == 422, r.text

    history = client.get("/api/admin/imports", headers=admin.headers,
                         params={"cohort_id": batch["cohort"]})
    assert history.status_code == 200, history.text
    runs = history.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["rows_total"] == 0
    assert runs[0]["filename"] == "results.docx"
    assert "spreadsheet" in (runs[0]["error"] or "")


# --------------------------------------------------------------- the write --


@requires_db
def test_marks_are_upserted_so_a_correction_can_be_re_imported(client, make_user, batch):
    """The unique constraints are `uq_semester_result` and `uq_subject_mark`, so
    the SECOND upload of a corrected sheet is the normal case, not the edge."""
    admin = make_user(f"imp-apply-{batch['tag']}", Role.ADMIN)
    first = _preview(client, admin.headers, batch, "marks", _marks_csv(batch))
    assert first.status_code == 201, first.text
    applied = client.post(
        f"/api/admin/imports/{first.json()['run']['id']}/apply", headers=admin.headers
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["rows_applied"] == 2
    assert applied.json()["students_touched"] == 2
    assert applied.json()["run"]["status"] == "applied"

    with SessionLocal() as db:
        result = db.scalar(
            select(SemesterResult).where(
                SemesterResult.student_id == batch["student_1"], SemesterResult.semester == 1
            )
        )
        assert result is not None
        assert result.cgpa == 7.9
        assert result.live_backlogs == 0
        mark = db.scalar(
            select(SubjectMark).where(
                SubjectMark.semester_result_id == result.id,
                SubjectMark.subject_code == batch["subject_a"],
            )
        )
        assert mark is not None and mark.total == 80

    # The corrected sheet: same student, same subject, different marks.
    corrected = _marks_csv(
        batch,
        rows=[f"{batch['usn_1']},{batch['subject_a']},Economics,4,45,45,9.0,8.8,0"],
    )
    second = _preview(client, admin.headers, batch, "marks", corrected)
    assert second.status_code == 201, second.text
    # The line is a WARNING, not a refusal: overwriting is what an import is for.
    assert second.json()["run"]["rows_warning"] == 1
    assert "overwrites" in second.json()["rows"][0]["message"]
    again = client.post(
        f"/api/admin/imports/{second.json()['run']['id']}/apply", headers=admin.headers
    )
    assert again.status_code == 200, again.text

    with SessionLocal() as db:
        result = db.scalar(
            select(SemesterResult).where(
                SemesterResult.student_id == batch["student_1"], SemesterResult.semester == 1
            )
        )
        assert result.cgpa == 8.8
        marks = db.scalars(
            select(SubjectMark).where(SubjectMark.semester_result_id == result.id)
        ).all()
        assert len(marks) == 1, "the upsert wrote a second row for the same subject"
        assert marks[0].total == 90


@requires_db
def test_applying_the_same_run_twice_is_refused(client, make_user, batch):
    """A double-click on an audited write is a mistake, and the 409 says who
    imported it and when rather than doing it again."""
    admin = make_user(f"imp-twice-{batch['tag']}", Role.ADMIN)
    run_id = _preview(client, admin.headers, batch, "marks", _marks_csv(batch)).json()["run"]["id"]
    assert client.post(f"/api/admin/imports/{run_id}/apply", headers=admin.headers).status_code == 200
    again = client.post(f"/api/admin/imports/{run_id}/apply", headers=admin.headers)
    assert again.status_code == 409, again.text
    assert "already been imported" in again.json()["detail"]


@requires_db
def test_attendance_becomes_one_row_per_session(client, make_user, batch):
    """The sheet is an aggregate and `attendance_records` is not — 26 of 30 is
    thirty rows, the first twenty-six present, because every existing reader of
    that table counts rows."""
    admin = make_user(f"imp-att-{batch['tag']}", Role.ADMIN)
    csv = "\n".join([
        ATTENDANCE_HEADER,
        f"{batch['usn_1']},{batch['subject_a']},30,26",
    ]) + "\n"
    preview = _preview(client, admin.headers, batch, "attendance", csv)
    assert preview.status_code == 201, preview.text
    assert preview.json()["rows"][0]["attendance_percent"] == 86.7
    applied = client.post(
        f"/api/admin/imports/{preview.json()['run']['id']}/apply", headers=admin.headers
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["records_written"] == 30

    with SessionLocal() as db:
        rows = db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.student_id == batch["student_1"],
                AttendanceRecord.course_code == batch["subject_a"],
            )
        ).all()
        assert len(rows) == 30
        assert sum(1 for row in rows if row.present) == 26
        assert sorted(row.session_no for row in rows) == list(range(1, 31))


@requires_db
def test_re_importing_fewer_sessions_removes_the_tail(client, make_user, batch):
    """A correction from 30 held to 20 must not leave ten withdrawn absences."""
    admin = make_user(f"imp-shrink-{batch['tag']}", Role.ADMIN)
    for held, attended in ((30, 26), (20, 18)):
        csv = "\n".join([
            ATTENDANCE_HEADER,
            f"{batch['usn_1']},{batch['subject_a']},{held},{attended}",
        ]) + "\n"
        preview = _preview(client, admin.headers, batch, "attendance", csv)
        assert preview.status_code == 201, preview.text
        assert client.post(
            f"/api/admin/imports/{preview.json()['run']['id']}/apply", headers=admin.headers
        ).status_code == 200

    with SessionLocal() as db:
        rows = db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.student_id == batch["student_1"],
                AttendanceRecord.course_code == batch["subject_a"],
            )
        ).all()
        assert len(rows) == 20
        assert sum(1 for row in rows if row.present) == 18


@requires_db
def test_an_attendance_line_claiming_more_attended_than_held_is_refused(
    client, make_user, batch
):
    """0-100 for attendance means "attended is inside held"; 31 of 30 is a typo
    that would otherwise write a subject at over 100%."""
    admin = make_user(f"imp-over-{batch['tag']}", Role.ADMIN)
    csv = "\n".join([
        ATTENDANCE_HEADER,
        f"{batch['usn_1']},{batch['subject_a']},30,31",
    ]) + "\n"
    r = _preview(client, admin.headers, batch, "attendance", csv)
    assert r.status_code == 201, r.text
    assert r.json()["run"]["rows_rejected"] == 1
    assert "more than the 30 held" in r.json()["rows"][0]["message"]


# ------------------------------------------------------------ XLSX and CSV --


@requires_db
def test_an_xlsx_workbook_reads_the_same_as_the_csv(client, make_user, batch):
    """openpyxl is the genuinely new dependency and this is what it is for."""
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(MARKS_HEADER.split(","))
    sheet.append([batch["usn_1"], batch["subject_a"], "Economics", 4, 42, 38, 8.1, 7.9, 0])
    buffer = io.BytesIO()
    book.save(buffer)

    admin = make_user(f"imp-xlsx-{batch['tag']}", Role.ADMIN)
    r = client.post(
        "/api/admin/imports/preview",
        headers=admin.headers,
        data={"kind": "marks", "cohort_id": batch["cohort"], "semester": "1"},
        files={"file": ("results.xlsx", buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["run"]["rows_ok"] == 1
    assert r.json()["rows"][0]["total"] == 80


@requires_db
def test_the_template_is_a_file_this_importer_accepts(client, make_user, batch):
    """The workbook REEP hands out, filled in and handed back, must import.

    This is the drift test: template and parser read one constant
    (`imports_sheet.DATASET_COLUMNS`), and the day somebody types the headings
    out separately this fails rather than the office being told their own file
    is wrong.
    """
    import openpyxl

    admin = make_user(f"imp-tmpl-{batch['tag']}", Role.ADMIN)
    r = client.get("/api/admin/imports/templates/marks.xlsx", headers=admin.headers)
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]

    book = openpyxl.load_workbook(io.BytesIO(r.content))
    header = [cell.value for cell in book.active[1] if cell.value]
    filled = ",".join(header) + "\n" + ",".join(
        str(v) for v in [batch["usn_1"], batch["subject_a"], "Economics", 4, 42, 38, 8.1, 7.9, 0]
    ) + "\n"
    preview = _preview(client, admin.headers, batch, "marks", filled)
    assert preview.status_code == 201, preview.text
    assert preview.json()["run"]["rows_ok"] == 1

    missing = client.get("/api/admin/imports/templates/jobs.xlsx", headers=admin.headers)
    assert missing.status_code == 404, missing.text


# ------------------------------------------------------- the error report --


@requires_db
def test_the_error_report_withholds_the_name_without_the_roster_function(
    client, make_user, batch, programme_grant
):
    """B14's rule, applied here: the USN the caller typed stays, the roster's
    answer to it does not."""
    admin = make_user(f"imp-err-{batch['tag']}", Role.ADMIN)
    csv = _marks_csv(
        batch,
        rows=[
            f"{batch['usn_1']},{batch['subject_a']},Economics,4,42,38,8.1,7.9,0",
            f"{batch['usn_2']},ZZZNOTASUBJECT,Economics,4,40,40,8.0,8.0,0",
        ],
    )
    run_id = _preview(client, admin.headers, batch, "marks", csv).json()["run"]["id"]

    full = client.get(f"/api/admin/imports/{run_id}/errors.csv", headers=admin.headers)
    assert full.status_code == 200, full.text
    header = full.text.splitlines()[0].split(",")
    assert header == ["Line", "Check", "USN", "Student", "Subject", "Message"]
    assert batch["usn_2"] in full.text
    assert "Import Student 2" in full.text

    faculty = make_user(f"imp-err-fac-{batch['tag']}", Role.MENTOR)
    programme_grant(faculty.user_id, "admin.imports")
    narrowed = client.get(f"/api/admin/imports/{run_id}/errors.csv", headers=faculty.headers)
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.text.splitlines()[0].split(",") == [
        "Line", "Check", "USN", "Subject", "Message"
    ]
    assert batch["usn_2"] in narrowed.text, "the USN they uploaded was withheld from them"
    assert "Import Student 2" not in narrowed.text, "the roster's name leaked without the key"


@requires_db
def test_every_write_endpoint_refuses_an_account_without_the_key(client, make_user, batch):
    """One key, five endpoints. A faculty account holds none of them."""
    faculty = make_user(f"imp-nokey2-{batch['tag']}", Role.MENTOR)
    assert _preview(client, faculty.headers, batch, "marks", _marks_csv(batch)).status_code == 403
    assert client.post(
        "/api/admin/imports/anything/apply", headers=faculty.headers
    ).status_code == 403
    assert client.get(
        "/api/admin/imports/anything/errors.csv", headers=faculty.headers
    ).status_code == 403
    assert client.get(
        "/api/admin/imports/templates/marks.xlsx", headers=faculty.headers
    ).status_code == 403


@requires_db
def test_a_run_outside_the_reach_is_not_found_rather_than_refused(
    client, make_user, batch, programme_grant
):
    """A 403 would confirm that a marks file exists for a batch this holder
    cannot see, which is the one bit the fence is there to withhold."""
    admin = make_user(f"imp-reach-{batch['tag']}", Role.ADMIN)
    run_id = _preview(client, admin.headers, batch, "marks", _marks_csv(batch)).json()["run"]["id"]

    stranger = make_user(f"imp-stranger-{batch['tag']}", Role.MENTOR)
    programme_grant(stranger.user_id, "admin.imports")
    # A programme grant DOES reach it; the negative case is a made-up id, which
    # takes the same branch as a run outside a narrowed reach.
    assert client.post(
        f"/api/admin/imports/{uuid.uuid4().hex}/apply", headers=stranger.headers
    ).status_code == 404
    assert client.get(
        f"/api/admin/imports/{run_id}/errors.csv", headers=stranger.headers
    ).status_code == 200
