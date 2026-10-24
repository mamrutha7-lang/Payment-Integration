"""DEVELOPMENT seed. Run AFTER migrations:

    python -m alembic upgrade head
    python -m app.seed

⚠️ REFUSES TO RUN WHEN ENV=prod, and that is the point of the guard below.

This module creates demo accounts whose passwords are published in AGENTS.md —
including admin@bgscet.ac.in / admin123, the Main Admin, who reads EVERY
student's marks, attendance and USN (AGENTS.md rule 2). That account existing on
a production host is a full-cohort data breach behind a password anyone who has
read the repo already knows. It is not a "weak default" to be rotated later; it
is a credential that must never be created there at all.

For the Knowledge Base — the one piece of seed data production genuinely needs —
use `python -m app.seed_kb`, which carries the KB and no accounts.

Data only — Alembic owns the schema. Requires the DB reachable and the reep_py
database created (see .env.example).
"""

import struct
import sys
import zlib
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .mentor_history import record_mentor_change
from .resume_pdf import render_resume_pdf
from .models.academic_history import AcademicGap, AcademicQualification, QualificationLevel
from .models.academics import SemesterResult, SubjectMark
from .models.alert import Alert, AlertRuleConfig, AlertRuleKey, AlertSeverity
from .models.attendance import AttendanceRecord
from .models.badge import (
    ApprovedCertification,
    AssessmentCheckpoint,
    BadgeEvidence,
    CapabilityAssessment,
    CapabilityKind,
    EvidenceStatus,
    EvidenceType,
    StudentBadge,
)
from .models.badge import StudentBadgeStatus as BadgeStatus
from .models.certification import Certification, CertificationProgress
from .models.cohort import Cohort
from .models.institution import AcademicCourse, AcademicSpecialization, College, Department
from .models.course import Course, CourseModel, Dimension, Enrollment, ProgressStatus
from .models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from .models.job import DegreeLevel, Job
from .models.lab import ActivityType, CheckInSource, LabSession, LearningMode
from .models.mail import MailLog
from .models.registration import Registration, RegistrationRule, RegistrationStatus
from .models.upload import Upload, UploadKind, UploadStatus
from .mailer import deliver_once
from .models.mentor_note import MentorAction, MentorNote
from .models.mock_test import MockAttempt, MockType
from .models.placement_criteria import PlacementCriteria
from .models.student_profile import StudentProfile
from .models.schedule import ScheduleItem, ScheduleType
from .models.skill import Skill, SkillClaim, StudentSkill
from .models.swoc import SwocEntry, SwocKind, SwocSource
from .models.english_baseline import (
    BaselineStatus,
    EnglishBaseline,
    EnglishBaselineSection,
    EnglishSkill,
    SectionStatus,
)
from .models.milestone import MilestoneStatus, StudentMilestone
from .models.time_ledger import (
    LedgerDayStatus,
    LedgerSlot,
    TimeLedgerCell,
    TimeLedgerDay,
)
from .models.timesheet import DayActivity, TimeSheetEntry
from .models.user import LoginDay, Mentor, Role, Stage, Student, User
# The rule-WRITE half of the usn_pattern defence; the router holds the match-time
# half. Imported from there rather than copied because the two checks have to be
# the SAME check: a pattern this module is willing to write and the public
# endpoint is not willing to run is a rule that silently matches nobody.
from .routers.registration import validate_usn_pattern
from .security import hash_password
from .verifiers import VERIFY_CAPABILITY
# Single copy of the KB, shared with the production-safe `python -m app.seed_kb`.
from .seed_kb import seed_knowledge


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A valid single-colour PNG, built from the two stdlib modules that can.

    Pillow is not a dependency and must not become one for the sake of a seed
    placeholder — `requirements.txt` is what the production image installs
    (AGENTS.md), and a runtime dependency added for dev data would ship. zlib and
    struct are enough: PNG is a length-prefixed chunk format over a zlib stream.
    """
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _seed_upload_files() -> dict[str, bytes]:
    """Write the placeholder files the seeded `uploads` rows point at.

    Returns stored_name -> content so the caller can record the true size.

    Written on EVERY run, not only when the rows are created: a developer whose
    database was seeded before this existed has three rows and no files, and
    telling them to drop the database to fix a broken link is not what
    "idempotent" is supposed to mean. Overwriting a placeholder with an
    identical placeholder costs nothing.

    The PDFs come from the app's own renderer, so what a mentor opens is a real
    document rather than four bytes that merely start with %PDF — the store
    sniffs magic bytes, but a human opens the file.
    """
    files = {
        # A profile photo has to be an image, and PNG is the one image format
        # the standard library can honestly produce. The row says PNG too: bytes
        # and metadata that disagree are how a preview breaks in the browser
        # while every check on the server passes.
        "up_photo_0001.png": _solid_png(240, 240, (124, 92, 176)),
        "up_cert_0002.pdf": render_resume_pdf(
            "# Leadership Foundations\n\n"
            "Certificate of completion — SAMPLE SEED DOCUMENT, not a real credential.\n\n"
            "## Awarded to\nTest Student (1BG24MBA001)\n\n"
            "## Provider\nCoursera\n",
            fallback_title="Leadership certificate",
        ),
        "up_resume_0003.pdf": render_resume_pdf(
            "# Test Student\n\n"
            "SAMPLE SEED DOCUMENT — the CV a student had before REEP composed one.\n\n"
            "## Contact\nstudent@bgscet.ac.in\n",
            fallback_title="Existing CV",
        ),
    }
    store = settings.uploads_path
    store.mkdir(parents=True, exist_ok=True)
    for stored_name, content in files.items():
        (store / stored_name).write_bytes(content)
    return files


def main() -> None:
    # Fail closed. There is no override flag on purpose: an escape hatch here
    # would be found and used, and every path through it ends with admin123
    # live on the internet.
    if settings.is_prod:
        print(
            "REFUSED: app.seed creates demo accounts with published passwords "
            "(including the Main Admin, who reads every student's records) and "
            "ENV=prod. For the Knowledge Base — the only seed data production "
            "needs — run: python -m app.seed_kb",
            file=sys.stderr,
        )
        raise SystemExit(1)

    db = SessionLocal()
    try:
        # NO DIRECTOR ACCOUNT (2026-09-10). This block used to create
        # director@bgscet.ac.in / director123 with Role.DIRECTOR.
        #
        # DIRECTOR was a second Main Admin under another name — its baseline was
        # every console screen — and `app.grant_access` has refused to mint one
        # for months. The dev seed was the last thing still creating them, which
        # meant every developer database, every test run and every reviewer's
        # mental model carried a privileged role the product does not have. It
        # now grants nothing anywhere (app/policies.py), so seeding one would
        # create an account that can sign in and reach nothing at all.
        #
        # The Main Admin below is the office. Faculty are MENTOR, and reach a
        # console screen only as a grant the Main Admin makes in Governance.
        # An existing dev database is converted by the migration that came with
        # this change; nothing here has to clean up after it.

        # The Main Admin - the one account that may open Governance and decide
        # what faculty see (routers/governance.py is ADMIN-only; the seeded
        # director above holds every console screen and still cannot grant one).
        # Dev only, like the rest: production's Main Admin is minted once by
        # `python -m app.grant_access`, which then refuses a second.
        admin_email = "admin@bgscet.ac.in"
        if db.scalar(select(User).where(User.email == admin_email)) is None:
            db.add(
                User(
                    email=admin_email,
                    name="Main Admin (seed)",
                    role=Role.ADMIN,
                    password_hash=hash_password("admin123"),
                )
            )
            db.commit()
            print(f"created {admin_email} / admin123")
        else:
            print(f"{admin_email} already exists")

        student_email = "student@bgscet.ac.in"
        if db.scalar(select(User).where(User.email == student_email)) is None:
            user = User(
                email=student_email,
                name="Test Student",
                role=Role.STUDENT,
                password_hash=hash_password("student123"),
            )
            db.add(user)
            db.flush()
            stu = Student(user_id=user.id)
            db.add(stu)
            db.flush()
            db.add(
                StudentProfile(
                    student_id=stu.id,
                    city="Bengaluru",
                    career_summary="MBA finance candidate seeking placement.",
                )
            )
            db.commit()
            print(f"created {student_email} / student123 (+ profile)")
        else:
            print(f"{student_email} already exists")

        mentor_email = "mentor@bgscet.ac.in"
        if db.scalar(select(User).where(User.email == mentor_email)) is None:
            mentor_user = User(
                email=mentor_email,
                name="Test Mentor",
                role=Role.MENTOR,
                password_hash=hash_password("mentor123"),
            )
            db.add(mentor_user)
            db.flush()
            db.add(Mentor(user_id=mentor_user.id))
            db.commit()
            print(f"created {mentor_email} / mentor123")

        alumni_email = "alumni@bgscet.ac.in"
        if db.scalar(select(User).where(User.email == alumni_email)) is None:
            db.add(
                User(
                    email=alumni_email,
                    name="Test Alumnus",
                    role=Role.ALUMNI,
                    password_hash=hash_password("alumni123"),
                )
            )
            db.commit()
            # No AlumniProfile row on purpose: the first-login create-profile
            # flow is the thing to see on a fresh database.
            print(f"created {alumni_email} / alumni123 (no profile — first-login flow)")

        # Idempotently give the test student one semester result with subjects.
        stu_user = db.scalar(select(User).where(User.email == student_email))
        stu = (
            db.scalar(select(Student).where(Student.user_id == stu_user.id)) if stu_user else None
        )
        if stu and stu.usn is None:
            stu.usn = "1BG24MBA001"
            stu.current_stage = Stage.EXCEL_ADVANCED
            stu.current_semester = 2
            db.commit()
            print("backfilled student USN + stage")
        mentor = db.scalar(
            select(Mentor).join(User, Mentor.user_id == User.id).where(User.email == "mentor@bgscet.ac.in")
        )
        if stu and mentor and stu.mentor_id != mentor.id:
            previous_mentor_id = stu.mentor_id
            stu.mentor_id = mentor.id
            # B9.1. The seed is the fourth of the five writers of this pointer,
            # and it goes through the same one function as the other four —
            # `app/mentor_history.py` names them by count for the reason this
            # line exists. No session and no request: there is no HTTP caller to
            # audit against, which is what the optional arguments are for.
            record_mentor_change(
                db,
                student_id=stu.id,
                previous_mentor_id=previous_mentor_id,
                new_mentor_id=mentor.id,
                by_user_id=None,
                reason="seeded demo pairing",
            )
            db.commit()
            print("assigned student to mentor group")
        if stu and mentor and db.scalar(
            # Idempotency guard: unfiltered, a soft-deleted note would convince
            # the seed a note already exists and it would never re-create one.
            select(MentorNote).where(MentorNote.student_id == stu.id, MentorNote.deleted_at.is_(None))
        ) is None:
            db.add(
                MentorNote(
                    mentor_id=mentor.id,
                    student_id=stu.id,
                    note_text="Discussed placement readiness; strong on analytics, work on GD delivery.",
                    linked_action=MentorAction.ONE_ON_ONE_SCHEDULED,
                )
            )
            db.commit()
            print("added a mentor note")
        if stu and db.scalar(select(Alert).where(Alert.student_id == stu.id)) is None:
            db.add(
                Alert(
                    student_id=stu.id,
                    rule_triggered=AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD,
                    severity=AlertSeverity.WARNING,
                    message="Attendance in Managerial Economics dropped below 80%.",
                    context={"course": "22MBA12", "percent": 80},
                )
            )
            db.commit()
            print("added an alert")
        if stu and db.scalar(select(SemesterResult).where(SemesterResult.student_id == stu.id)) is None:
            result = SemesterResult(
                student_id=stu.id,
                semester=1,
                sgpa=8.2,
                cgpa=8.2,
                closed_backlogs=0,
                live_backlogs=0,
                result_class="FIRST CLASS WITH DISTINCTION",
            )
            result.subjects = [
                SubjectMark(
                    subject_code="22MBA11",
                    subject_name="Management & Organisational Behaviour",
                    credits=4,
                    internal=42,
                    external=40,
                    total=82,
                    passed=True,
                ),
                SubjectMark(
                    subject_code="22MBA12",
                    subject_name="Managerial Economics",
                    credits=4,
                    internal=38,
                    external=36,
                    total=74,
                    passed=True,
                ),
            ]
            db.add(result)
            db.commit()
            print("added a semester result (2 subjects) for the test student")

        # Idempotently add attendance: 2 courses x 20 sessions (~85% overall).
        if stu and db.scalar(
            select(AttendanceRecord).where(AttendanceRecord.student_id == stu.id)
        ) is None:
            base = datetime(2026, 1, 6, tzinfo=timezone.utc)
            records = []
            for code, present_count in (("22MBA11", 18), ("22MBA12", 16)):  # of 20
                for n in range(1, 21):
                    records.append(
                        AttendanceRecord(
                            student_id=stu.id,
                            course_code=code,
                            session_date=base + timedelta(days=n),
                            session_no=n,
                            present=(n <= present_count),
                        )
                    )
            db.add_all(records)
            db.commit()
            print("added attendance (40 sessions across 2 courses)")

        # Idempotently add a few SWOC entries across the viewpoints.
        if stu and db.scalar(select(SwocEntry).where(SwocEntry.student_id == stu.id)) is None:
            # EVERY SEEDED LINE NOW HAS AN AUTHOR, and that is a one-line fix to
            # a demo that told a lie. `author_user_id` was NULL on all four, and
            # the admin board's copy for a NULL author is "Author no longer on
            # the roster" — a false statement about a row nobody ever wrote, on
            # a screen the student's own Faculty / TPO Log reads from. Two
            # distinct facts (never recorded / recorded but the account is gone)
            # were collapsing into one string, and the honest fix is to stop
            # producing the first one in the seed. `semester` is stamped for the
            # same reason the write path stamps it: at the moment of writing.
            mentor_user_id = db.scalar(select(User.id).where(User.email == "mentor@bgscet.ac.in"))
            office_user_id = db.scalar(select(User.id).where(User.email == "admin@bgscet.ac.in"))
            db.add_all(
                [
                    SwocEntry(student_id=stu.id, source=SwocSource.MENTOR, kind=SwocKind.STRENGTH, text="Strong analytical and quantitative skills.", weight=5, author_user_id=mentor_user_id, semester=stu.current_semester),
                    SwocEntry(student_id=stu.id, source=SwocSource.PLACEMENT, kind=SwocKind.WEAKNESS, text="Needs structured problem-solving practice.", weight=4, author_user_id=office_user_id, semester=stu.current_semester),
                    # PM STAYS. It is a legal stored value, the client maps it,
                    # and dropping a Postgres enum value means recreating the
                    # type — 04's "PM retired from the writer" is already true
                    # of the API, where `_source_for` has only two outcomes.
                    SwocEntry(student_id=stu.id, source=SwocSource.PM, kind=SwocKind.OPPORTUNITY, text="Fintech internships opening this quarter.", weight=3, author_user_id=office_user_id, semester=stu.current_semester),
                    SwocEntry(student_id=stu.id, source=SwocSource.MENTOR, kind=SwocKind.CHALLENGE, text="Public speaking under time pressure.", weight=3, author_user_id=mentor_user_id, semester=stu.current_semester),
                ]
            )
            db.commit()
            print("added SWOC entries (4 across quadrants)")

        # Idempotently add a couple of mock-assessment attempts.
        if stu and db.scalar(select(MockAttempt).where(MockAttempt.student_id == stu.id)) is None:
            base = datetime(2026, 2, 1, tzinfo=timezone.utc)
            db.add_all(
                [
                    MockAttempt(student_id=stu.id, type=MockType.APTITUDE, taken_on=base, score=72, max_score=100, notes="Solid quant; revise data interpretation."),
                    MockAttempt(student_id=stu.id, type=MockType.GD, taken_on=base + timedelta(days=7), score=7, max_score=10, notes="Good points; interrupt less."),
                ]
            )
            db.commit()
            print("added mock attempts (2)")

        # Idempotently seed a small skill catalogue + the student's skills.
        if db.scalar(select(Skill)) is None:
            db.add_all(
                [
                    Skill(slug="excel", name="MS Excel", category="Analytics", aliases=["advanced excel"]),
                    Skill(slug="power-bi", name="Power BI", category="Analytics", aliases=[]),
                    Skill(slug="financial-modeling", name="Financial Modeling", category="Business", aliases=[]),
                ]
            )
            db.commit()
            print("added skill catalogue (3)")
        if stu and db.scalar(select(StudentSkill).where(StudentSkill.student_id == stu.id)) is None:
            catalogue = {s.slug: s for s in db.scalars(select(Skill)).all()}
            db.add_all(
                [
                    StudentSkill(student_id=stu.id, skill_id=catalogue["excel"].id, level=4, verified=True),
                    StudentSkill(student_id=stu.id, skill_id=catalogue["power-bi"].id, level=3, verified=False),
                    StudentSkill(student_id=stu.id, skill_id=catalogue["financial-modeling"].id, level=3, verified=False),
                ]
            )
            db.commit()
            print("added student skills (3)")

        # Idempotently seed a 5-day login streak ending today.
        if stu_user:
            existing_days = set(
                db.scalars(select(LoginDay.day).where(LoginDay.user_id == stu_user.id)).all()
            )
            today = date.today()
            added = 0
            for delta in range(5):  # today .. today-4
                day = today - timedelta(days=delta)
                if day not in existing_days:
                    db.add(LoginDay(user_id=stu_user.id, day=day))
                    added += 1
            if added:
                db.commit()
                print(f"seeded {added} login-day(s) for a streak")

        # Idempotently add 3 days of time-sheet entries (5 activities each).
        if stu and db.scalar(
            select(TimeSheetEntry).where(TimeSheetEntry.student_id == stu.id)
        ) is None:
            today = date.today()
            plan = [
                (DayActivity.SLEEPING, 420),
                (DayActivity.LECTURES, 240),
                (DayActivity.COURSEWORK, 90),
                (DayActivity.SKILLING, 120),
                (DayActivity.LEISURE, 120),
            ]
            entries = [
                TimeSheetEntry(
                    student_id=stu.id,
                    day=today - timedelta(days=delta),
                    activity=act,
                    minutes=mins,
                )
                for delta in range(3)
                for act, mins in plan
            ]
            db.add_all(entries)
            db.commit()
            print(f"added timesheet entries ({len(entries)})")

        # Idempotently add prior qualifications + a gap row.
        if stu and db.scalar(
            select(AcademicQualification).where(AcademicQualification.student_id == stu.id)
        ) is None:
            db.add_all(
                [
                    AcademicQualification(student_id=stu.id, level=QualificationLevel.TENTH, institution="St. Joseph's High School", board="CBSE", year=2018, marks=88, max_marks=100, medium="English"),
                    AcademicQualification(student_id=stu.id, level=QualificationLevel.TWELFTH, institution="Sri Chaitanya PU College", board="Karnataka PUC", year=2020, marks=82, max_marks=100, medium="English"),
                    AcademicQualification(student_id=stu.id, level=QualificationLevel.UNDERGRAD, institution="Bangalore University", year=2024, marks=72, max_marks=100),
                ]
            )
            db.commit()
            print("added academic qualifications (3)")
        if stu and db.scalar(select(AcademicGap).where(AcademicGap.student_id == stu.id)) is None:
            db.add(AcademicGap(student_id=stu.id, twelfth_to_grad_mo=0, grad_to_pg_mo=0))
            db.commit()
            print("added academic gap row")

        # Idempotently seed a few jobs with varied eligibility gates.
        if db.scalar(select(Job)) is None:
            base = datetime(2026, 8, 1, tzinfo=timezone.utc)
            db.add_all(
                [
                    Job(title="Financial Analyst", company="Acme Capital", degree_level=DegreeLevel.PG, location="Bengaluru", required_skills=["excel", "financial-modeling"], posted_on=base, min_cgpa=7.0, max_live_backlogs=0, description="Financial analysis and modeling.", apply_url="https://example.com/apply/1"),
                    Job(title="BI Developer", company="DataWorks", degree_level=DegreeLevel.PG, location="Remote", required_skills=["power-bi", "excel"], posted_on=base, min_cgpa=8.5, description="Build dashboards.", apply_url="https://example.com/apply/2"),
                    Job(title="Junior Accountant", company="LedgerCo", degree_level=DegreeLevel.UG, location="Mysuru", required_skills=["excel"], posted_on=base, description="Entry-level accounting."),
                ]
            )
            db.commit()
            print("added jobs (3)")

        # Idempotently add a few upcoming schedule items for the student.
        if stu and db.scalar(select(ScheduleItem).where(ScheduleItem.student_id == stu.id)) is None:
            now = datetime.now(timezone.utc)
            db.add_all(
                [
                    ScheduleItem(student_id=stu.id, type=ScheduleType.MENTOR_MEETING, title="1:1 with mentor", starts_at=now + timedelta(days=1), location="Cabin 3"),
                    ScheduleItem(student_id=stu.id, type=ScheduleType.LECTURE, title="Managerial Economics", starts_at=now + timedelta(days=2), course_code="22MBA12", location="Room 201"),
                    ScheduleItem(student_id=stu.id, type=ScheduleType.CERT_DEADLINE, title="Power BI certification due", starts_at=now + timedelta(days=5)),
                ]
            )
            db.commit()
            print("added schedule items (3)")

        # Idempotently seed two courses + the student's enrollments.
        if db.scalar(select(Course)) is None:
            db.add_all(
                [
                    Course(code="22MBA11", name="Management & Organisational Behaviour", stage=Stage.EXCEL, dimension=Dimension.PROFESSIONAL, semester=1, model_type=CourseModel.INSTRUCTOR_LED, teaching_hours=40, self_learning_hours_required=20),
                    Course(code="22MBA12", name="Managerial Economics", stage=Stage.EXCEL, dimension=Dimension.THINKING, semester=1, model_type=CourseModel.TEACHING_PLUS_SELF_LEARN, teaching_hours=40, self_learning_hours_required=20),
                ]
            )
            db.commit()
            print("added courses (2)")
        if stu and db.scalar(select(Enrollment).where(Enrollment.student_id == stu.id)) is None:
            db.add_all(
                [
                    Enrollment(student_id=stu.id, course_code="22MBA11", status=ProgressStatus.IN_PROGRESS, teaching_hours_attended=32, lectures_attended=18, lectures_total=20),
                    Enrollment(student_id=stu.id, course_code="22MBA12", status=ProgressStatus.IN_PROGRESS, teaching_hours_attended=28, lectures_attended=16, lectures_total=20),
                ]
            )
            db.commit()
            print("added enrollments (2)")

        # Idempotently seed a certification (on 22MBA11) + the student's progress.
        if db.scalar(select(Certification)) is None:
            db.add(
                Certification(
                    code="CERT-22MBA11-LEAD",
                    course_code="22MBA11",
                    name="Leadership Foundations",
                    provider="Coursera",
                    required_hours=15,
                    link="https://coursera.org/learn/leadership-foundations",
                    due_week=12,
                )
            )
            db.commit()
            print("added certification (1)")
        if stu and db.scalar(
            select(CertificationProgress).where(CertificationProgress.student_id == stu.id)
        ) is None:
            db.add(
                CertificationProgress(
                    student_id=stu.id,
                    cert_code="CERT-22MBA11-LEAD",
                    status=ProgressStatus.IN_PROGRESS,
                    progress_pct=60,
                    hours_logged=9,
                    due_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
                )
            )
            db.commit()
            print("added certification progress (1)")

        # Idempotently add two completed focus/lab sessions.
        if stu and db.scalar(select(LabSession).where(LabSession.student_id == stu.id)) is None:
            base = datetime.now(timezone.utc)
            db.add_all(
                [
                    LabSession(student_id=stu.id, course_code="22MBA12", module="Demand analysis", activity=ActivityType.ONLINE_COURSE, mode=LearningMode.SUPERVISED_LAB, source=CheckInSource.LAB_PC, check_in_at=base - timedelta(days=1, hours=2), check_out_at=base - timedelta(days=1), duration_min=120, mentor_confirmed=True),
                    LabSession(student_id=stu.id, course_code="22MBA11", module="OB case study", activity=ActivityType.GROUP_STUDY, mode=LearningMode.INDEPENDENT, source=CheckInSource.SELF_REPORTED, check_in_at=base - timedelta(hours=3), check_out_at=base - timedelta(hours=1, minutes=30), duration_min=90),
                ]
            )
            db.commit()
            print("added lab sessions (2)")

        # Idempotently create the institution chain the student's locked profile
        # card is read through: College -> Department -> Cohort -> Student.
        #
        # WITHOUT THESE TWO ROWS THE FEATURE IS INVISIBLE. The card is headed
        # "Institutional assignment - verified by Main Admin" and reads College
        # and Department through the join; a cohort with no department renders
        # both as dashes. That would be the state on every fresh database --
        # after docker compose up, alembic upgrade head and this seed -- so
        # every developer and every demo would see the empty version of a
        # feature that works. AGENTS.md's own discipline: the seed exists to
        # make the interesting state the one you get by default.
        if db.scalar(select(College)) is None:
            db.add(
                College(
                    code="BGSCET",
                    name="BGS College of Engineering and Technology",
                    campus="Bengaluru",
                )
            )
            db.commit()
            print("added college (1)")
        college = db.scalar(select(College).where(College.code == "BGSCET"))
        # The DEPARTMENT is the organisational unit; the COURSE (below) is the
        # programme it offers. They are different levels and must not share a
        # code — an earlier seed gave both "MBA", which rendered as
        # BGSCET -> MBA -> MBA -> FIN and read as a bug. MGMT offers the MBA.
        if college and db.scalar(select(Department).where(Department.college_id == college.id)) is None:
            db.add(
                Department(
                    college_id=college.id,
                    code="MGMT",
                    name="Department of Management Studies",
                    head="Dr. Kavya N",
                )
            )
            db.commit()
            print("added department (1)")
        department = (
            db.scalar(
                select(Department)
                .where(Department.college_id == college.id)
                .order_by(Department.created_at)
            )
            if college
            else None
        )
        # Self-healing for a database seeded before the rename: the seed is
        # idempotent, so correct the legacy code in place rather than creating
        # a second department beside it.
        if department is not None and department.code == "MBA":
            department.code = "MGMT"
            db.commit()
            print("renamed department code MBA -> MGMT")

        # The two optional levels, so the student's card shows all of Course and
        # Specialization on a fresh database rather than the "not used here"
        # footnote — the interesting state is the one you should meet by default.
        course = None
        if department is not None:
            course = db.scalar(
                select(AcademicCourse).where(
                    AcademicCourse.department_id == department.id, AcademicCourse.code == "MBA"
                )
            )
            if course is None:
                course = AcademicCourse(
                    department_id=department.id,
                    code="MBA",
                    name="Master of Business Administration",
                    duration_months=24,
                )
                db.add(course)
                db.commit()
                print("added academic course (1)")
        specialization = None
        if course is not None:
            specialization = db.scalar(
                select(AcademicSpecialization).where(
                    AcademicSpecialization.course_id == course.id,
                    AcademicSpecialization.code == "FIN",
                )
            )
            if specialization is None:
                specialization = AcademicSpecialization(
                    course_id=course.id, code="FIN", name="Finance"
                )
                db.add(specialization)
                db.commit()
                print("added academic specialization (1)")

        # Idempotently create a cohort and assign the student to it.
        if db.scalar(select(Cohort)) is None:
            db.add(
                Cohort(
                    code="MBA-2026-B",
                    # A BATCH IS A YEAR, plus whatever the office adds to tell
                    # two sections of it apart. The course and the
                    # specialization are the three pointers below and are
                    # composed back on at read time (`app/batch_labels.py`), so
                    # this seeds the one shape that rule has to survive: a
                    # non-standard batch whose own name says more than the span.
                    # It used to read "MBA Batch 2024-26 - Section B", which
                    # printed the course and the year twice over.
                    name="2024-26 Section B",
                    batch_label="2024-26",
                    degree_level=DegreeLevel.PG,
                    department_id=department.id if department else None,
                    course_id=course.id if course else None,
                    specialization_id=specialization.id if specialization else None,
                    start_date=datetime(2024, 8, 1, tzinfo=timezone.utc),
                    end_date=datetime(2026, 7, 31, tzinfo=timezone.utc),
                )
            )
            db.commit()
            print("added cohort (1)")
        cohort = db.scalar(select(Cohort).where(Cohort.code == "MBA-2026-B"))
        # Seats a cohort seeded before departments existed. Without this the
        # backfill never happens on a database that already ran an older seed.
        if cohort and department and cohort.department_id is None:
            cohort.department_id = department.id
            db.commit()
            print("filed cohort under department")
        if cohort and course and cohort.course_id is None:
            cohort.course_id = course.id
            cohort.specialization_id = specialization.id if specialization else None
            db.commit()
            print("filed cohort under course/specialization")
        if stu and cohort and stu.cohort_id != cohort.id:
            stu.cohort_id = cohort.id
            db.commit()
            print("assigned student to cohort")
        # BOTH POINTERS, because setting only the batch is the legacy shape that
        # made "un-seat" read as "un-file" (31f7a4c60b12). A dev database that
        # seeds the inconsistent row teaches the bug back in.
        if stu and cohort and cohort.department_id and stu.department_id != cohort.department_id:
            stu.department_id = cohort.department_id
            db.commit()
            print("filed student under the cohort's department")

        # SKILL VERIFICATION IS A GRANT THE OFFICE MAKES (2026-10-16), no longer
        # something mentoring brings (app/verifiers.py). So a fresh database
        # would have a pending claim and a pending document with nobody able to
        # open the queue they sit in. The seed makes the choice the office
        # would: Test Mentor verifies for the seeded college, written as the
        # Main Admin's grant with a reason, exactly as Who can do what writes
        # one. Idempotent on a live grant, so a revoked one is not re-made
        # behind the back of whoever revoked it on a dev database.
        mentor_user = db.scalar(select(User).where(User.email == mentor_email))
        if mentor_user and college and db.scalar(
            select(CapabilityGrant).where(
                CapabilityGrant.capability == VERIFY_CAPABILITY,
                CapabilityGrant.subject_kind == SubjectKind.USER,
                CapabilityGrant.subject_user_id == mentor_user.id,
            )
        ) is None:
            db.add(
                CapabilityGrant(
                    capability=VERIFY_CAPABILITY,
                    subject_kind=SubjectKind.USER,
                    subject_user_id=mentor_user.id,
                    scope_level=ScopeLevel.COLLEGE,
                    scope_id=college.id,
                    role_at_grant=Role.MENTOR.value,
                    granted_by_user_id=db.scalar(
                        select(User.id).where(User.email == "admin@bgscet.ac.in")
                    ),
                    reason="Seeded: Test Mentor verifies skill claims and documents for this college.",
                )
            )
            db.commit()
            print("granted Test Mentor skill verification for the seeded college")

        # Idempotently create the default active placement criteria.
        if db.scalar(select(PlacementCriteria)) is None:
            db.add(PlacementCriteria(name="Default", active=True))
            db.commit()
            print("added placement criteria (default)")

        # Idempotently seed sample uploads awaiting mentor review: a profile
        # photo, a certificate proof (tied to the seeded certification), and a
        # resume — one already VERIFIED so the review states are visible.
        #
        # THE BYTES ARE WRITTEN TOO, and they were not before. These rows named a
        # `stored_name` the file store had never been asked to write, so every
        # "View proof" link on the seeded certificate — the one the Resume
        # Builder's Evidence-backed Skills step renders — answered 404 "Stored
        # file is missing" on a completely fresh database. A broken link on seed
        # data reads as a bug in the download path, and the first thing anyone
        # does is go and debug a path that was working perfectly.
        seed_files = _seed_upload_files()
        if stu and db.scalar(select(Upload).where(Upload.student_id == stu.id)) is None:
            base = datetime.now(timezone.utc)
            db.add_all(
                [
                    # size_bytes is len(content), not a plausible-looking number:
                    # the ledger prints this figure next to a file a mentor can
                    # open, and a row claiming 512 KB over a 2 KB file is a lie
                    # the UI repeats.
                    Upload(student_id=stu.id, kind=UploadKind.PROFILE_PHOTO, title="Profile photo", original_name="me.png", stored_name="up_photo_0001.png", mime_type="image/png", size_bytes=len(seed_files["up_photo_0001.png"]), status=UploadStatus.PENDING_REVIEW, uploaded_at=base - timedelta(days=2)),
                    Upload(student_id=stu.id, kind=UploadKind.CERTIFICATE_PROOF, cert_code="CERT-22MBA11-LEAD", title="Leadership certificate", original_name="leadership_completion.pdf", stored_name="up_cert_0002.pdf", mime_type="application/pdf", size_bytes=len(seed_files["up_cert_0002.pdf"]), status=UploadStatus.PENDING_REVIEW, uploaded_at=base - timedelta(days=1)),
                    Upload(student_id=stu.id, kind=UploadKind.RESUME, title="Existing CV", original_name="resume_v1.pdf", stored_name="up_resume_0003.pdf", mime_type="application/pdf", size_bytes=len(seed_files["up_resume_0003.pdf"]), status=UploadStatus.VERIFIED, reviewed_at=base - timedelta(hours=6), review_note="Looks good.", uploaded_at=base - timedelta(days=3)),
                ]
            )
            db.commit()
            print("added uploads (3: 2 pending, 1 verified)")

        # Repair a database seeded before the files existed, rather than asking
        # for a rebuild: the rows are already there and idempotent means the
        # second run leaves the developer with a working link, not the same 404.
        if stu:
            legacy = db.scalar(
                select(Upload).where(
                    Upload.student_id == stu.id, Upload.stored_name == "up_photo_0001.jpg"
                )
            )
            if legacy is not None:
                legacy.original_name = "me.png"
                legacy.stored_name = "up_photo_0001.png"
                legacy.mime_type = "image/png"
                legacy.size_bytes = len(seed_files["up_photo_0001.png"])
                db.commit()
                print("repaired seeded photo upload (jpg row -> stored png)")

        # Idempotently seed one pending skill claim: the student claims a level
        # on a catalogue skill, backed by their certificate-proof upload.
        if stu and db.scalar(select(SkillClaim).where(SkillClaim.student_id == stu.id)) is None:
            skill = db.scalar(select(Skill).order_by(Skill.slug))
            evidence = db.scalar(
                select(Upload).where(Upload.student_id == stu.id, Upload.kind == UploadKind.CERTIFICATE_PROOF)
            )
            if skill and evidence:
                db.add(
                    SkillClaim(student_id=stu.id, skill_id=skill.id, upload_id=evidence.id, claimed_level=4)
                )
                db.commit()
                print("added skill claim (1, pending)")

        # Idempotently seed two registration rules: a narrow MBA-USN auto-approve
        # rule (priority 10) that assigns the seeded cohort, and a broad
        # college-domain route-to-review rule (priority 100).
        if db.scalar(select(RegistrationRule)) is None:
            cohort = db.scalar(select(Cohort).where(Cohort.code == "MBA-2026-B"))
            registration_rules = [
                RegistrationRule(name="MBA 2024-26 auto-admit", enabled=True, email_domain="bgscet.ac.in", usn_pattern=r"^1BG2[0-9]MBA[0-9]{3}$", degree_level=DegreeLevel.PG, cohort_id=cohort.id if cohort else None, auto_approve=True, priority=10),
                RegistrationRule(name="College domain — route to review", enabled=True, email_domain="bgscet.ac.in", auto_approve=False, priority=100),
            ]
            # CHECK THE PATTERN BEFORE IT REACHES THE TABLE. `usn_pattern` is a
            # regular expression that POST /register runs — the one endpoint in
            # this app with no cookie, no roster check and no rate limit worth
            # the name in front of it. app/routers/registration.py names this
            # module as rule-write defence #1 and _usn_matcher() as #2, but #1
            # did not exist: the seed could plant exactly the pattern the router
            # then refuses to run, and the only symptom would be one WARNING in
            # the API log plus a rule that quietly matches nobody — applications
            # falling through to manual review with no rule to blame.
            #
            # Raising here is loud, immediate, and lands on whoever typed the
            # pattern, which is the only person who can fix it. Nothing is
            # committed, so a rejected pattern leaves the table empty and the
            # next run seeds again rather than leaving a half-written rule set.
            #
            # ANY future writer (an admin-facing rule editor) must call this
            # too, for the same reason: a rule row can also be edited straight in
            # psql, which is why defence #2 exists at all.
            for rule in registration_rules:
                if rule.usn_pattern:
                    validate_usn_pattern(rule.usn_pattern)
            db.add_all(registration_rules)
            db.commit()
            print("added registration rules (2)")

        # Idempotently seed three sample applications: one the narrow rule
        # auto-approves, one only the broad rule routes to manual review, and one
        # a reviewer has HELD.
        #
        # THE HELD ONE IS SEEDED FOR THE SAME REASON THE TIME LEDGER IS SEEDED
        # HALF AN HOUR SHORT: a state nobody can see on a fresh database is a
        # state nobody reviews. HOLD is one row in one column, the Held tab is
        # empty without it, and "the tab loads and shows nothing" is
        # indistinguishable from "the tab is broken" until somebody holds an
        # application by hand. The note is what a real hold looks like — it says
        # what the application is waiting on, because a hold with no note is a
        # PENDING_REVIEW row with extra steps.
        if db.scalar(select(Registration)) is None:
            rules = {r.name: r for r in db.scalars(select(RegistrationRule)).all()}
            auto = rules.get("MBA 2024-26 auto-admit")
            broad = rules.get("College domain — route to review")
            db.add_all(
                [
                    Registration(name="Asha Rao", email="1bg24mba045@bgscet.ac.in", usn="1BG24MBA045", degree_level=DegreeLevel.PG, status=RegistrationStatus.AUTO_APPROVED, cohort_id=auto.cohort_id if auto else None, matched_rule_id=auto.id if auto else None, decision_reason="Auto-approved by rule 'MBA 2024-26 auto-admit'."),
                    Registration(name="Ravi Kumar", email="ravi.kumar@bgscet.ac.in", degree_level=DegreeLevel.PG, status=RegistrationStatus.PENDING_REVIEW, matched_rule_id=broad.id if broad else None, decision_reason="Routed by rule 'College domain — route to review' — awaiting review."),
                    # `held_by_id` is left NULL and `decision_reason` keeps the
                    # rule engine's own sentence: the seed has no reviewer to
                    # name, and a hold changes nothing the applicant is told.
                    Registration(name="Nikhil Shetty", email="nikhil.shetty@bgscet.ac.in", degree_level=DegreeLevel.PG, status=RegistrationStatus.HOLD, matched_rule_id=broad.id if broad else None, decision_reason="Routed by rule 'College domain — route to review' — awaiting review.", hold_note="No CV attached, and the USN on the form is one digit short. Asked him to resend both.", held_at=datetime.now(timezone.utc)),
                ]
            )
            db.commit()
            print("added registrations (3: 1 auto-approved, 1 pending review, 1 held)")

        # THERE IS NO JOB-IMPORT SEED ANY MORE. B8.4 deleted `job_import_runs`
        # and `jobs.import_run_id` with the three endpoints nobody called; this
        # block used to write one run and back-link every seeded posting to it.
        # B8.1's `import_runs` is the import provenance now, and it is seeded
        # nowhere on purpose: an import run is a receipt for a file somebody
        # chose, and a fabricated one on a fresh database would put a row in the
        # history grid that no operator can account for.

        # Idempotently seed the per-cohort alert thresholds (config in data,
        # never hard-coded). One row per (cohort, rule_key).
        cohort = db.scalar(select(Cohort).where(Cohort.code == "MBA-2026-B"))
        if cohort and db.scalar(select(AlertRuleConfig).where(AlertRuleConfig.cohort_id == cohort.id)) is None:
            db.add_all(
                [
                    AlertRuleConfig(cohort_id=cohort.id, rule_key=AlertRuleKey.NO_CHECKIN_N_DAYS, params={"days": 5}, severity=AlertSeverity.WARNING),
                    AlertRuleConfig(cohort_id=cohort.id, rule_key=AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD, params={"minAttendancePct": 75}, severity=AlertSeverity.CRITICAL),
                    AlertRuleConfig(cohort_id=cohort.id, rule_key=AlertRuleKey.CERT_OVERDUE, params={"graceDays": 3}, severity=AlertSeverity.WARNING),
                ]
            )
            db.commit()
            print("added alert rule configs (3)")

        # Idempotently seed a few mail-log rows via the dedupe-safe mailer, so
        # the ops audit view has sample data. Re-running is a no-op: the unique
        # dedupe_key collapses the second attempt.
        if db.scalar(select(MailLog)) is None:
            deliver_once(db, kind="job-alert", recipient=student_email, dedupe_key=f"job-alert:{student_email}:2026-W33", subject="3 new jobs match your profile")
            deliver_once(db, kind="weekly-digest", recipient=student_email, dedupe_key=f"weekly-digest:{student_email}:2026-W33", subject="Your week in REEP")
            print("added mail logs (2)")

        # Idempotently seed the grounded assistant's KNOWLEDGE BASE. These are
        # APPROVED, student-audience policy/FAQ/guidance documents — the
        # "explain the rules" layer. They hold NO live student facts; a
        # student's own numbers still come from the authenticated records view.
        # Keyed on title so re-running is a no-op.
        seed_knowledge(db)

        # --- v2 UI screens -------------------------------------------------
        #
        # The three screens the v2 student UI adds have nothing to draw on a
        # fresh database, and "the design is broken" is the report you get when
        # a brand-new checkout shows three empty states. Each block is keyed on
        # its own first row, so re-running the seed is a no-op like every other
        # block here.
        seed_v2_screens(db, stu, mentor)
    finally:
        db.close()


def seed_v2_screens(db, stu, mentor) -> None:
    """Sample data for the Time Allocation Ledger, the English Baseline and the
    Mentor Meeting Log.

    Called from `main()`, which already refuses to run when ENV=prod — these
    rows describe a demo student and must never reach a real deployment for the
    same reason the three demo logins must not.
    """
    if stu is None:
        return

    # A submitted ledger for YESTERDAY and a part-filled draft for TODAY. Two
    # days rather than one so "Copy yesterday" has a legal source on a fresh
    # database — its 404 is correct behaviour but reads as a broken button.
    if db.scalar(select(TimeLedgerDay).where(TimeLedgerDay.student_id == stu.id)) is None:
        today = date.today()
        full = {
            LedgerSlot.DAWN: {DayActivity.LEISURE: 2, DayActivity.COURSEWORK: 6},
            LedgerSlot.MORNING: {DayActivity.LECTURES: 6},
            LedgerSlot.MIDDAY: {DayActivity.LECTURES: 2, DayActivity.COURSEWORK: 4},
            LedgerSlot.AFTERNOON: {DayActivity.SKILLING: 6},
            LedgerSlot.EVENING: {DayActivity.SKILLING: 2, DayActivity.LEISURE: 6},
            LedgerSlot.NIGHT: {DayActivity.SLEEPING: 14},
        }
        submitted = TimeLedgerDay(
            student_id=stu.id,
            day=today - timedelta(days=1),
            status=LedgerDayStatus.SUBMITTED,
            submitted_at=datetime.now(timezone.utc),
        )
        for slot, per in full.items():
            for activity, halves in per.items():
                submitted.cells.append(
                    TimeLedgerCell(slot=slot, activity=activity, half_hours=halves)
                )
        db.add(submitted)

        # Today, deliberately 0.5 h short — that is the state the design shows
        # ("23.5 / 24 h · 0.5 h to reconcile"), and it is the one worth seeing.
        draft = TimeLedgerDay(student_id=stu.id, day=today)
        for slot, per in full.items():
            for activity, halves in per.items():
                if slot is LedgerSlot.AFTERNOON and activity is DayActivity.SKILLING:
                    halves -= 1
                if halves:
                    draft.cells.append(
                        TimeLedgerCell(slot=slot, activity=activity, half_hours=halves)
                    )
        db.add(draft)
        db.commit()
        print("added time allocation ledger (yesterday submitted, today 23.5/24 h)")

    # The English Baseline: three sections scored, speaking pending — the state
    # the screen is designed around, not a tidy finished one.
    if db.scalar(select(EnglishBaseline).where(EnglishBaseline.student_id == stu.id)) is None:
        baseline = EnglishBaseline(
            student_id=stu.id,
            semester=2,
            status=BaselineStatus.IN_PROGRESS,
            overall_score=62,
            band="B1+",
            taken_on=date.today() - timedelta(days=11),
            report_available=True,
            strengths=[
                "Reads academic text at pace — B2 on skimming and detail",
                "Task responses stay on brief and are well structured",
            ],
            focus_areas=[
                "Grammar range in writing: tense shifts under time pressure",
                "Listening to unfamiliar accents drops accuracy by ~8 points",
            ],
            next_steps=[
                {"title": "Business Writing Clinic", "sub": "4 sessions · Skilling track", "target": "/student/skilling"},
                {"title": "Accent & Listening Lab", "sub": "Weekly · Reboot support", "target": "/student/skilling"},
                {"title": "GD practice pod", "sub": "Peer group · Thursdays", "target": "/student/skilling"},
            ],
        )
        baseline.sections = [
            EnglishBaselineSection(
                skill=EnglishSkill.READING, status=SectionStatus.SCORED, score=68, band="B2",
                minutes=18,
                subscores=[
                    {"label": "Skimming & scanning", "value": 72},
                    {"label": "Inference", "value": 66},
                    {"label": "Academic vocabulary", "value": 63},
                ],
                ai_report="Reads at pace and handles detail well; inference under time pressure is the next lift.",
            ),
            EnglishBaselineSection(
                skill=EnglishSkill.WRITING, status=SectionStatus.SCORED, score=57, band="B1",
                minutes=25,
                subscores=[
                    {"label": "Task response", "value": 61},
                    {"label": "Grammar range", "value": 54},
                    {"label": "Cohesion", "value": 57},
                ],
                ai_report="On brief and well organised; tense control slips when writing quickly.",
            ),
            EnglishBaselineSection(
                skill=EnglishSkill.LISTENING, status=SectionStatus.SCORED, score=61, band="B1",
                minutes=15,
                subscores=[
                    {"label": "Gist & detail", "value": 66},
                    {"label": "Accent handling", "value": 58},
                    {"label": "Note-taking", "value": 59},
                ],
            ),
            EnglishBaselineSection(
                skill=EnglishSkill.SPEAKING, status=SectionStatus.PENDING, minutes=12,
                subscores=[
                    {"label": "Fluency", "value": None},
                    {"label": "Pronunciation", "value": None},
                    {"label": "Interaction", "value": None},
                ],
            ),
        ]
        db.add(baseline)
        db.commit()
        print("added english baseline (3 of 4 sections scored, speaking pending)")

    # Programme milestones — enough of Reboot done to make the landing cards
    # show all three states. english_baseline is NOT seeded: its status is
    # derived from the attempt above (see models/milestone.py).
    if db.scalar(select(StudentMilestone).where(StudentMilestone.student_id == stu.id)) is None:
        db.add_all(
            [
                StudentMilestone(student_id=stu.id, key="ree_101", status=MilestoneStatus.COMPLETED),
                StudentMilestone(student_id=stu.id, key="ree_102", status=MilestoneStatus.COMPLETED),
                StudentMilestone(student_id=stu.id, key="peep_1", status=MilestoneStatus.COMPLETED),
                StudentMilestone(student_id=stu.id, key="peep_2", status=MilestoneStatus.IN_PROGRESS),
                StudentMilestone(student_id=stu.id, key="vtu_1", status=MilestoneStatus.COMPLETED),
                StudentMilestone(student_id=stu.id, key="vtu_2", status=MilestoneStatus.IN_PROGRESS),
                StudentMilestone(student_id=stu.id, key="hippo", status=MilestoneStatus.IN_PROGRESS),
            ]
        )
        db.commit()
        print("added programme milestones (7)")

    # Titles and locations on the existing mentor notes, so the Mentor Meeting
    # Log has headings rather than falling back to the linked action for every
    # row. Only fills blanks — a mentor's own title is never overwritten.
    if mentor is not None:
        titled = 0
        notes = db.scalars(
            # Headings are spent one per note, so a retracted row here would
            # consume a heading and leave a live note untitled.
            select(MentorNote)
            .where(MentorNote.student_id == stu.id, MentorNote.deleted_at.is_(None))
            .order_by(MentorNote.meeting_at.desc())
        ).all()
        headings = [
            ("1:1 review", "Cabin 3"),
            ("Mock GD debrief", "Room 201"),
            ("Skilling plan for the semester", "Cabin 3"),
        ]
        for note, (title, location) in zip(notes, headings):
            if note.title is None:
                note.title, note.location = title, location
                titled += 1
        if titled:
            db.commit()
            print(f"titled mentor meeting notes ({titled})")

    # --- Skills & Badge dashboard (models/badge.py) --------------------------
    # A slice of every state the screen can show: earned (with approved
    # evidence), verification pending, more-info-required, in progress, plus
    # T0→T2 assessment scores so the growth table and Most Improved board have
    # something honest to draw. The catalogue rows give the "approved
    # certification" picker its simpler path.
    if db.scalar(select(ApprovedCertification).limit(1)) is None:
        db.add_all(
            [
                ApprovedCertification(
                    name="Excel Skills for Business: Essentials",
                    provider="Macquarie University / Coursera",
                    badge_code="TECH-EXCEL-FOUNDATION",
                    duration_text="≈ 25 hours",
                    is_free=True,
                    url="https://www.coursera.org/learn/excel-essentials",
                ),
                ApprovedCertification(
                    name="Google Data Analytics: Share Data Through Visualization",
                    provider="Google / Coursera",
                    badge_code="SEC-BA-DATA-VISUALISATION",
                    duration_text="≈ 20 hours",
                    is_free=True,
                    url="https://www.coursera.org/learn/visualize-data",
                ),
                ApprovedCertification(
                    name="Successful Negotiation: Essential Strategies and Skills",
                    provider="University of Michigan / Coursera",
                    badge_code="MGR-NEGOTIATION",
                    duration_text="≈ 17 hours",
                    is_free=True,
                    url="https://www.coursera.org/learn/negotiation-skills",
                ),
            ]
        )
        db.commit()
        print("added approved certification catalogue (3)")

    if db.scalar(select(StudentBadge).where(StudentBadge.student_id == stu.id)) is None:
        admin_user = db.scalar(select(User).where(User.email == "admin@bgscet.ac.in"))
        reviewer_id = admin_user.id if admin_user else None
        now = datetime.now(timezone.utc)
        db.add_all(
            [
                # Earned, on approved external evidence.
                StudentBadge(
                    student_id=stu.id,
                    badge_code="MGR-BUSINESS-COMMUNICATION",
                    status=BadgeStatus.EARNED,
                    points_awarded=10,
                    earned_at=now - timedelta(days=30),
                    awarded_by_id=reviewer_id,
                    award_note="Approved evidence",
                ),
                StudentBadge(
                    student_id=stu.id,
                    badge_code="TECH-EXCEL-FOUNDATION",
                    status=BadgeStatus.EARNED,
                    points_awarded=10,
                    earned_at=now - timedelta(days=12),
                    awarded_by_id=reviewer_id,
                    award_note="Approved evidence",
                ),
                # In progress and pending — rows exist, nothing earned.
                StudentBadge(student_id=stu.id, badge_code="THK-CRITICAL-THINKING"),
                StudentBadge(student_id=stu.id, badge_code="SEC-BA-FUNDAMENTALS"),
            ]
        )
        db.add_all(
            [
                BadgeEvidence(
                    student_id=stu.id,
                    badge_code="MGR-BUSINESS-COMMUNICATION",
                    evidence_type=EvidenceType.EXTERNAL_VERIFIED,
                    status=EvidenceStatus.APPROVED,
                    title="Business Communication Certificate",
                    provider="Coursera",
                    reviewed_by_id=reviewer_id,
                    reviewed_at=now - timedelta(days=30),
                ),
                BadgeEvidence(
                    student_id=stu.id,
                    badge_code="TECH-EXCEL-FOUNDATION",
                    evidence_type=EvidenceType.BGSCET_ASSESSED,
                    status=EvidenceStatus.APPROVED,
                    title="REEP Excel workshop assessment",
                    provider="BGSCET MBA",
                    reviewed_by_id=reviewer_id,
                    reviewed_at=now - timedelta(days=12),
                ),
                # The review queue's fresh item.
                BadgeEvidence(
                    student_id=stu.id,
                    badge_code="SEC-BA-FUNDAMENTALS",
                    evidence_type=EvidenceType.EXTERNAL_VERIFIED,
                    status=EvidenceStatus.PENDING_VERIFICATION,
                    title="Business Analytics Fundamentals",
                    provider="Coursera",
                    student_note="Completed last week — certificate attached on my uploads.",
                ),
                # And one sent back for more information.
                BadgeEvidence(
                    student_id=stu.id,
                    badge_code="THK-CRITICAL-THINKING",
                    evidence_type=EvidenceType.APPLIED,
                    status=EvidenceStatus.MORE_INFO_REQUIRED,
                    title="Case competition — campus round",
                    review_note="Add the certificate or the organiser's result sheet.",
                    reviewed_by_id=reviewer_id,
                    reviewed_at=now - timedelta(days=3),
                ),
            ]
        )
        db.commit()
        print("added badge dashboard slice (2 earned, 1 pending, 1 more-info)")

    if db.scalar(
        select(CapabilityAssessment).where(CapabilityAssessment.student_id == stu.id)
    ) is None:
        admin_user = db.scalar(select(User).where(User.email == "admin@bgscet.ac.in"))
        recorder = admin_user.id if admin_user else None
        scores = {
            # capability: (T0, T1, T2) — visible growth, one flat line, and
            # speaking assessed only twice so a dash renders somewhere.
            CapabilityKind.READING: (5.0, 5.8, 6.4),
            CapabilityKind.WRITING: (4.2, 4.9, 5.6),
            CapabilityKind.LISTENING: (5.5, 5.6, 5.5),
            CapabilityKind.SPEAKING: (3.4, 4.8, None),
            CapabilityKind.QUANTITATIVE: (4.8, 5.5, 6.2),
            CapabilityKind.DATA_INTERPRETATION: (4.0, 5.0, 6.1),
            CapabilityKind.LOGICAL_REASONING: (5.2, 5.9, 6.5),
        }
        rows = []
        for cap, values in scores.items():
            for checkpoint, score in zip(
                (AssessmentCheckpoint.T0, AssessmentCheckpoint.T1, AssessmentCheckpoint.T2), values
            ):
                if score is not None:
                    rows.append(
                        CapabilityAssessment(
                            student_id=stu.id,
                            capability=cap,
                            checkpoint=checkpoint,
                            score=score,
                            recorded_by_id=recorder,
                        )
                    )
        db.add_all(rows)
        db.commit()
        print(f"added capability assessments ({len(rows)} scores, T0–T2)")


if __name__ == "__main__":
    main()
