"""The 4a schema: the reused enums, the backfill that declines to guess, the
three new tables' purge verdicts, and what a cohort purge does to a graduate.

Four of these need no database and run everywhere. The three that touch
Postgres do it the way `test_purge_students.py` does — against the REAL schema,
inside a transaction that is rolled back — because the questions being asked
("did this migration reuse the type or create a second one", "does the
destructor abort on this foreign key") are questions about the database and a
synthetic fixture answers a different one.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import uuid

import pytest
from sqlalchemy import func, insert, select, text

from conftest import requires_db

from app import purge_people, purge_students
from app.db import Base, SessionLocal
from app.models.user import Role

T = Base.metadata.tables

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "c3a9f1e7d2b4_course_shape_semesters_and_catalogue.py"
)


def _migration():
    """Import the revision by path — `migrations/versions` is not a package."""
    spec = importlib.util.spec_from_file_location("rev_c3a9f1e7d2b4", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 1. The enums are REUSED, not recreated
# --------------------------------------------------------------------------- #


def test_the_new_enum_columns_name_the_types_that_already_exist() -> None:
    """The model half: same TYPE NAME and same members as the columns that
    already use them, so nothing here can become a second enum by accident."""
    from app.models.badge import ApprovedCertification
    from app.models.catalogue import StageRule
    from app.models.cohort import Cohort
    from app.models.institution import AcademicCourse

    course_level = AcademicCourse.__table__.c.degree_level.type
    cohort_level = Cohort.__table__.c.degree_level.type
    assert course_level.name == cohort_level.name == "degree_level"
    assert course_level.enums == cohort_level.enums

    rule_stage = StageRule.__table__.c.stage.type
    cert_stage = ApprovedCertification.__table__.c.stage.type
    assert rule_stage.name == cert_stage.name == "stage"
    assert rule_stage.enums == cert_stage.enums

    # And the new course column is NULLABLE, which is the whole backfill
    # decision expressed in the schema: a mixed course has no answer and NULL
    # is what "nobody has said" looks like.
    assert AcademicCourse.__table__.c.degree_level.nullable
    assert AcademicCourse.__table__.c.total_semesters.nullable


def test_the_migration_hand_writes_create_type_false_for_both_enums() -> None:
    """AND THE HALF THAT ACTUALLY BREAKS, read out of the migration as text.

    `create_type=False` passed to a generic `sa.Enum` is silently DISCARDED — it
    is a postgresql-dialect argument, and the model declarations in this repo
    that carry it are documentation rather than enforcement. The place it has to
    be real is the migration, where `postgresql.ENUM(..., create_type=False)` is
    the difference between adding a column and dying on "type already exists"
    against a database that has had `degree_level` since fea4515cdba5.

    Read as source, deliberately: importing the module proves nothing, because
    the argument is only consulted when the DDL is emitted.
    """
    source = MIGRATION.read_text()
    assert (
        'postgresql.ENUM("UG", "PG", name="degree_level", create_type=False)' in source
    ), "the degree_level column must reuse the existing PG type"
    assert (
        'postgresql.ENUM(*_STAGES, name="stage", create_type=False)' in source
    ), "stage_rules.stage must reuse the existing PG type"
    # A bare sa.Enum anywhere in this revision is the mistake being guarded
    # against; the two helpers above are the only enums it may emit.
    assert "sa.Enum(" not in source
    # And the downgrade must not DROP either type: both are still in use by the
    # columns that created them.
    downgrade = source.split("def downgrade()", 1)[1]
    assert "DROP TYPE" not in downgrade.upper()


@requires_db
def test_the_database_holds_one_degree_level_type_and_one_stage_type() -> None:
    """The half the model declaration cannot prove: that the migration actually
    landed those columns on the SAME PostgreSQL type the older ones use.

    A second `academic_courses_degree_level` type holding 'UG' and 'PG' would
    satisfy every Python-side assertion above, read identically on screen, and
    then refuse the one query that compares a course's level against a batch's.
    """
    with SessionLocal() as db:
        pairs = db.execute(
            text(
                "SELECT table_name, column_name, udt_name"
                "  FROM information_schema.columns"
                " WHERE (table_name, column_name) IN "
                "       (('academic_courses','degree_level'),"
                "        ('cohorts','degree_level'),"
                "        ('stage_rules','stage'),"
                "        ('courses','stage'))"
            )
        ).all()
        by_column = {(t, c): udt for t, c, udt in pairs}
        assert by_column[("academic_courses", "degree_level")] == "degree_level"
        assert by_column[("cohorts", "degree_level")] == "degree_level"
        assert by_column[("stage_rules", "stage")] == "stage"
        assert by_column[("courses", "stage")] == "stage"

        # And no lookalike type was created alongside them.
        strays = db.execute(
            text(
                "SELECT typname FROM pg_type"
                " WHERE typtype = 'e'"
                "   AND (typname LIKE '%degree_level%' OR typname LIKE '%stage%')"
            )
        ).scalars().all()
        assert sorted(set(strays)) == ["degree_level", "stage"], strays


# --------------------------------------------------------------------------- #
# 2. The backfill acts on one candidate and abstains on two
# --------------------------------------------------------------------------- #


def _college_and_department(db, tag: str) -> tuple[str, str]:
    college_id = f"col-{tag}"
    dept_id = f"dep-{tag}"
    db.execute(
        insert(T["colleges"]).values(id=college_id, code=f"C{tag}", name=f"College {tag}")
    )
    db.execute(
        insert(T["departments"]).values(
            id=dept_id, college_id=college_id, code=f"D{tag}", name=f"Dept {tag}"
        )
    )
    return college_id, dept_id


def _course(db, dept_id: str, tag: str) -> str:
    course_id = f"crs-{tag}"
    db.execute(
        insert(T["academic_courses"]).values(
            id=course_id, department_id=dept_id, code=f"P{tag}", name=f"Programme {tag}"
        )
    )
    return course_id


def _batch(db, course_id: str, dept_id: str, level: str, tag: str) -> str:
    cohort_id = f"coh-{tag}"
    now = dt.datetime.now(dt.timezone.utc)
    db.execute(
        insert(T["cohorts"]).values(
            id=cohort_id,
            code=f"B-{tag}",
            name=f"Batch {tag}",
            batch_label="2024-26",
            department_id=dept_id,
            course_id=course_id,
            degree_level=level,
            start_date=now,
            end_date=now + dt.timedelta(days=700),
        )
    )
    return cohort_id


@requires_db
def test_the_backfill_fills_an_unambiguous_course_and_leaves_a_mixed_one_alone() -> None:
    """THE ABSTENTION IS THE POINT, and it is asserted by running the real
    function rather than by reading its SQL.

    Nothing in the schema forbids one course from having a UG batch and a PG
    batch — `cohorts.degree_level` is required per batch and is never checked
    against a sibling — so an integrated programme, or one mid-way through being
    split, is a legitimate row this migration must not guess about. Picking the
    majority would be silent and wrong for the minority batches, and the person
    who would have to correct it is never told there was a decision.
    """
    mig = _migration()
    courses = T["academic_courses"]
    with SessionLocal() as db:
        try:
            tag = uuid.uuid4().hex[:8]
            _, dept = _college_and_department(db, tag)

            single = _course(db, dept, f"{tag}s")
            _batch(db, single, dept, "PG", f"{tag}s1")
            _batch(db, single, dept, "PG", f"{tag}s2")

            mixed = _course(db, dept, f"{tag}m")
            _batch(db, mixed, dept, "UG", f"{tag}m1")
            _batch(db, mixed, dept, "PG", f"{tag}m2")

            orphan = _course(db, dept, f"{tag}o")  # no batches at all
            db.flush()

            mig.apply_course_levels(db.connection())

            def read(course_id: str):
                return db.execute(
                    select(courses.c.degree_level, courses.c.total_semesters).where(
                        courses.c.id == course_id
                    )
                ).one()

            level, semesters = read(single)
            assert str(getattr(level, "value", level)) == "PG"
            assert semesters == 4, "PG is four semesters"

            level, semesters = read(mixed)
            assert level is None, "a mixed course must not be guessed at"
            assert semesters is None, "and must not be given a semester count either"

            level, semesters = read(orphan)
            assert level is None and semesters is None
        finally:
            db.rollback()


@requires_db
def test_a_ug_course_gets_eight_semesters() -> None:
    """The other arm of the same table, so a swapped mapping cannot pass."""
    mig = _migration()
    courses = T["academic_courses"]
    with SessionLocal() as db:
        try:
            tag = uuid.uuid4().hex[:8]
            _, dept = _college_and_department(db, tag)
            course = _course(db, dept, tag)
            _batch(db, course, dept, "UG", tag)
            db.flush()

            mig.apply_course_levels(db.connection())
            level, semesters = db.execute(
                select(courses.c.degree_level, courses.c.total_semesters).where(
                    courses.c.id == course
                )
            ).one()
            assert str(getattr(level, "value", level)) == "UG"
            assert semesters == 8
        finally:
            db.rollback()


@requires_db
def test_the_catalogue_is_attached_only_when_one_college_and_one_course_exist() -> None:
    """04 says "attached to BGSCET/MBA by migration". There is no guaranteed
    BGSCET row on any deployment, so the rule is b2c9e04a7731's: act where
    exactly one candidate resolves. Two colleges and the rows stay NULL, which
    MEANS programme-wide — the reading that loses nobody access."""
    mig = _migration()
    certs = T["approved_certifications"]
    colleges = T["colleges"]
    courses = T["academic_courses"]
    with SessionLocal() as db:
        try:
            one_college = db.scalar(select(func.count()).select_from(colleges)) == 1
            one_course = db.scalar(select(func.count()).select_from(courses)) == 1
            if one_college and one_course:
                # The seeded shape: the attachment writes down what is already
                # true, which is the only case in which it is safe.
                assert mig.attach_catalogue(db.connection()) is True
                unattached = db.scalar(
                    select(func.count())
                    .select_from(certs)
                    .where(certs.c.college_id.is_(None))
                )
                assert unattached == 0

            # A second college makes the target ambiguous, and the function must
            # decline rather than pick one.
            tag = uuid.uuid4().hex[:8]
            _college_and_department(db, tag)
            db.flush()
            assert mig.attach_catalogue(db.connection()) is False
        finally:
            db.rollback()


# --------------------------------------------------------------------------- #
# 3. The three new tables are classified by BOTH destructors
# --------------------------------------------------------------------------- #


def test_the_new_tables_have_a_verdict_in_both_destructors() -> None:
    """`check_verdicts` already refuses an unclassified table; this pins WHICH
    verdict each of the three got, because the wrong one is silent in exactly
    the way the pairing exists to prevent — a wrong KEEP leaves a student's
    records behind, a wrong EMPTY destroys a catalogue somebody typed in."""
    assert purge_people.VERDICTS["student_semester_history"] == purge_people.EMPTY
    assert purge_people.VERDICTS["stage_rules"] == purge_people.KEEP
    assert purge_people.VERDICTS["badge_course_map"] == purge_people.KEEP

    # It names a student and `student_id` is NOT NULL, so only a student can own
    # one: ALL. The office's record of the ACT survives as the STUDENTS_PROMOTE
    # audit event, which is KEPT.
    assert purge_students.STUDENT_VERDICTS["student_semester_history"] == purge_students.ALL
    # The catalogue the office maintains, in the same group as
    # `approved_certifications`, and KEPT in BOTH files so that
    # test_this_module_keeps_only_what_it_says_it_keeps stays a four-name set.
    assert purge_students.STUDENT_VERDICTS["stage_rules"] == purge_students.KEEP
    assert purge_students.STUDENT_VERDICTS["badge_course_map"] == purge_students.KEEP

    purge_people.check_verdicts()
    purge_students.check_verdicts()


# --------------------------------------------------------------------------- #
# 4. THE GRADUATION HOLE
# --------------------------------------------------------------------------- #


def _graduate(db) -> dict[str, str]:
    """One account that has already graduated: role ALUMNI, `students` row still
    standing, an alumni profile linked back to it. Written inside the caller's
    transaction and rolled back with it."""
    tag = uuid.uuid4().hex[:8]
    ids = {
        "user": f"u-grad-{tag}",
        "student": f"s-grad-{tag}",
        "profile": f"ap-grad-{tag}",
        "history": f"h-grad-{tag}",
    }
    db.execute(
        insert(T["users"]).values(
            id=ids["user"],
            email=f"grad-{tag}@bgscet.ac.in",
            name="A Graduate",
            role=Role.ALUMNI,
            password_hash="sso-only",
        )
    )
    db.execute(
        insert(T["students"]).values(
            id=ids["student"], user_id=ids["user"], status="GRADUATED", current_semester=4
        )
    )
    db.execute(
        insert(T["alumni_profiles"]).values(
            id=ids["profile"], user_id=ids["user"], company="Somewhere Ltd",
            student_id=ids["student"],
        )
    )
    db.execute(
        insert(T["student_semester_history"]).values(
            id=ids["history"],
            student_id=ids["student"],
            from_semester=4,
            to_semester=4,
            effective_on=dt.date.today(),
            kind="graduate",
        )
    )
    db.flush()
    return ids


@requires_db
def test_a_cohort_purge_refuses_rather_than_stranding_a_graduate() -> None:
    """THE DEFAULT BEHAVIOUR, and the reason this test exists.

    Before this commit the same situation was SILENT: `users` is scoped by role
    so the ALUMNI account survived, while `students` and the thirty tables under
    it are verdict `ALL` and were emptied unconditionally. "Clear the demo
    cohort" deleted every graduate's marks, interviews, uploads and badges and
    left their login working, and nothing in the module could tell.

    Now the plan refuses before a byte is destroyed and names the person.
    """
    with SessionLocal() as db:
        try:
            ids = _graduate(db)
            with pytest.raises(purge_students.PurgeRefused) as caught:
                purge_students.build_plan(db)
            message = str(caught.value)
            assert "student record(s) belong to account(s) this run is NOT" in message
            assert "--include-graduates" in message, "the refusal must name its remedy"

            # Nothing was touched: a refusal is not a partial pass.
            assert db.scalar(
                select(T["students"].c.id).where(T["students"].c.id == ids["student"])
            )
        finally:
            db.rollback()


@requires_db
def test_include_graduates_takes_the_whole_person_and_nobody_else() -> None:
    """THE DELIBERATE BEHAVIOUR when an operator means it: a former student goes
    completely — account, alumni profile and academic record — because they were
    a student on this deployment. There is no third outcome in which some of a
    person's rows go and the rest stay.

    The ALUMNI account that was never a student here (the seeded
    `alumni@bgscet.ac.in`, or a guest speaker) owns no `students` row, so it is
    not selected by the join and is not touched by either mode.
    """
    users = T["users"]
    with SessionLocal() as db:
        try:
            ids = _graduate(db)
            untouched = db.execute(
                select(users.c.id)
                .outerjoin(T["students"], T["students"].c.user_id == users.c.id)
                .where(users.c.role.not_in((Role.STUDENT, "STUDENT")))
                .where(T["students"].c.id.is_(None))
            ).scalars().all()

            plan = purge_students.build_plan(db, include_former_students=True)
            assert ids["user"] in plan.doomed.user_ids
            assert ids["user"] in plan.doomed.former_student_user_ids
            assert plan.former_students >= 1

            purge_students._null_created_by(db, plan)
            purge_students._delete_rows(db, plan)

            for table, row_id in (
                ("users", ids["user"]),
                ("students", ids["student"]),
                ("alumni_profiles", ids["profile"]),
                ("student_semester_history", ids["history"]),
            ):
                t = T[table]
                assert (
                    db.scalar(select(t.c.id).where(t.c.id == row_id)) is None
                ), f"{table}: the graduate's row survived --include-graduates"

            for other in untouched:
                assert db.scalar(
                    select(users.c.id).where(users.c.id == other)
                ), "an alumnus who was never a student here must not be touched"
        finally:
            db.rollback()


@requires_db
def test_a_graduate_with_no_alumni_profile_is_refused_the_same_way() -> None:
    """Graduation deliberately does NOT create the `alumni_profiles` row —
    `company` is NOT NULL and row existence is what the first-login form
    branches on — so the common shape of a graduate has no profile at all. The
    guard keys on the `students` row, not on the profile, and must catch that
    one identically."""
    with SessionLocal() as db:
        try:
            tag = uuid.uuid4().hex[:8]
            db.execute(
                insert(T["users"]).values(
                    id=f"u-{tag}",
                    email=f"bare-grad-{tag}@bgscet.ac.in",
                    name="Bare Graduate",
                    role=Role.ALUMNI,
                    password_hash="sso-only",
                )
            )
            db.execute(
                insert(T["students"]).values(
                    id=f"s-{tag}", user_id=f"u-{tag}", status="GRADUATED"
                )
            )
            db.flush()
            with pytest.raises(purge_students.PurgeRefused):
                purge_students.build_plan(db)
        finally:
            db.rollback()


@requires_db
def test_the_ordinary_cohort_purge_is_unchanged_when_nobody_has_graduated() -> None:
    """The guard must be invisible on every deployment that has not graduated
    anybody — which is all of them until B4.4 ships. A guard that trips on a
    laptop gets deleted by whoever is trying to ship that afternoon."""
    with SessionLocal() as db:
        try:
            plan = purge_students.build_plan(db)  # the seeded dev database
            assert plan.former_students == 0
        finally:
            db.rollback()
