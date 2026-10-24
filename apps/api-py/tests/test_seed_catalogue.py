"""The catalogue seeder's shape, and the codes that decide whether a student
can be scored.

`app/seed_catalogue.py` writes the institutional spine. Most of what it does is
ordinary row creation; ONE field in it -- a leaf's `code` -- silently decides
whether that leaf's students ever get a scorecard, because
`routers/interview_policy._default_track` preselects the interview track by
exact match on it. Nothing on any screen reports a mismatch. So the codes are
asserted here, by name.

The structural tests need no database. The idempotency test does, and uses a
throwaway catalogue rather than the real BGSCET one, so it can never collide
with the dev seed or leave a real-looking college behind.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app import seed_catalogue as sc
from app.db import SessionLocal
from app.interview_matrix import SPECIALIZATIONS
from app.models.cohort import Cohort
from sqlalchemy.exc import IntegrityError

from app.models.job import DegreeLevel
from app.models.institution import (
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)

BGSCET = sc.CATALOGUES["1MP"]


# ------------------------------------------------------- the codes that bite --


def test_every_code_is_lowercase_and_stripped() -> None:
    """`_default_track` case-folds, so a stray capital matches anyway -- but the
    codes are also read by humans off the Catalogue screen, and `HR` beside
    `marketing` reads as two different kinds of thing."""
    for cat in sc.CATALOGUES.values():
        for course in cat.courses:
            assert course.code == course.code.strip().lower(), course
            for spec in course.specializations:
                assert spec.code == spec.code.strip().lower(), spec


def test_codes_are_unique_where_the_schema_requires_it() -> None:
    """`uq_academic_course_department_code` and
    `uq_academic_specialization_course_code`. A duplicate here is an
    IntegrityError halfway through a seed, which is worse than a refusal."""
    for cat in sc.CATALOGUES.values():
        course_codes = [c.code for c in cat.courses]
        assert len(course_codes) == len(set(course_codes)), cat.college_code
        for course in cat.courses:
            spec_codes = [s.code for s in course.specializations]
            assert len(spec_codes) == len(set(spec_codes)), course


def test_the_leaves_that_match_a_built_in_track() -> None:
    """The four that must work on day one, named individually.

    Asserting "most of them match" would pass on a file where the wrong three
    matched. These are the exact pairings the college asked for.
    """
    keys = {
        f"{course.name}|{spec.name if spec else ''}": sc.track_key(course, spec)
        for course, spec in sc.leaves(BGSCET)
    }
    assert keys["General MBA|Human Resources"] == "hr"
    assert keys["General MBA|Business Analytics"] == "ba"
    assert keys["General MBA|Finance"] == "fa"
    assert keys["Digital Marketing|"] == "dm"
    for key in ("hr", "ba", "fa", "dm"):
        assert key in SPECIALIZATIONS, f"{key} is no longer a built-in track"


def test_marketing_is_deliberately_not_mapped_to_digital_marketing() -> None:
    """THE PIN THAT MATTERS MOST, because the "fix" is so tempting.

    Marketing has no interview track, and the nearest-looking one is `dm` --
    Digital Marketing, which is a DIFFERENT DISCIPLINE and is one of this
    college's other two-year courses. Mapping it would put a Marketing student
    in front of a growth CMO asking about CAC/LTV ratios and paid-media
    attribution, and they would be scored against that. The honest state is no
    preselection until the office builds a Marketing track.
    """
    marketing = next(
        spec
        for course, spec in sc.leaves(BGSCET)
        if spec is not None and spec.name == "Marketing"
    )
    assert marketing.code == "marketing"
    assert marketing.code != "dm"
    assert marketing.code not in SPECIALIZATIONS


def test_logistics_is_honestly_unmapped_too() -> None:
    """No track exists, and the code is the one that will make it work later.

    When the office adds a track with this code the preselection begins with no
    change to this file, because both halves are the same string.
    """
    course = next(c for c in BGSCET.courses if c.name.startswith("Logistics"))
    assert course.code == "lscm"
    assert course.code not in SPECIALIZATIONS


# ------------------------------------------------------------ the structure --


def test_a_course_with_specializations_is_not_itself_a_leaf() -> None:
    """Nobody enrols in "General MBA" without choosing one of its four."""
    names = [
        (course.name, spec.name if spec else None) for course, spec in sc.leaves(BGSCET)
    ]
    assert ("General MBA", None) not in names
    assert ("General MBA", "Finance") in names


def test_a_course_without_specializations_is_its_own_leaf() -> None:
    """A two-year MBA in Digital Marketing IS the qualification. This is the
    shape `_default_track` gained the course rung for."""
    names = [
        (course.name, spec.name if spec else None) for course, spec in sc.leaves(BGSCET)
    ]
    assert ("Digital Marketing", None) in names
    assert ("Logistics and Supply Chain Management", None) in names


def test_bgscet_has_six_leaves() -> None:
    assert len(sc.leaves(BGSCET)) == 6


def test_batch_codes_are_globally_unique() -> None:
    """`cohorts.code` is UNIQUE across the whole table, not per college -- so
    two colleges' batches share one namespace and the code has to carry the
    whole path. A collision is an IntegrityError mid-seed."""
    codes = [
        sc.batch_code(cat, course, spec, label)
        for cat in sc.CATALOGUES.values()
        for label in cat.batches
        for course, spec in sc.leaves(cat)
    ]
    assert len(codes) == len(set(codes)), "two batches would claim one code"
    assert all(c.startswith("1MP-MBA-") for c in codes if c.startswith("1MP"))


def test_the_batch_the_college_asked_for_is_the_one_declared() -> None:
    assert BGSCET.batches == ("2025-27",)


# ------------------------------------------------------------- idempotency --


def _throwaway() -> sc.Catalogue:
    tag = uuid.uuid4().hex[:6]
    return sc.Catalogue(
        college_code=f"T{tag}",
        college_name=f"Test College {tag}",
        department_code="TST",
        department_name="Testing",
        courses=(
            sc.Course(
                code="gen",
                name="General",
                degree_level=DegreeLevel.PG,
                specializations=(sc.Spec(code="hr", name="Human Resources"),),
            ),
            sc.Course(code="solo", name="Solo Programme", degree_level=DegreeLevel.UG),
        ),
        batches=("2025-27",),
    )


@pytest.fixture
def swept():
    cats: list[sc.Catalogue] = []
    yield cats
    with SessionLocal() as db:
        for cat in cats:
            college = db.scalar(select(College).where(College.code == cat.college_code))
            if college is None:
                continue
            depts = list(db.scalars(select(Department.id).where(Department.college_id == college.id)))
            if depts:
                courses = list(
                    db.scalars(select(AcademicCourse.id).where(AcademicCourse.department_id.in_(depts)))
                )
                db.execute(delete(Cohort).where(Cohort.department_id.in_(depts)))
                if courses:
                    db.execute(
                        delete(AcademicSpecialization).where(
                            AcademicSpecialization.course_id.in_(courses)
                        )
                    )
                    db.execute(delete(AcademicCourse).where(AcademicCourse.id.in_(courses)))
                db.execute(delete(Department).where(Department.id.in_(depts)))
            db.execute(delete(College).where(College.id == college.id))
        db.commit()


@requires_db
def test_seeding_twice_writes_nothing_the_second_time(swept) -> None:
    """The property that makes this safe to put behind a button.

    An operator who is unsure whether the run worked will press it again. If the
    second press duplicated the catalogue, that uncertainty would cost a college
    a hand-unpicked mess -- and there is no destructor for a catalogue.
    """
    cat = _throwaway()
    swept.append(cat)
    with SessionLocal() as db:
        first = sc.seed(db, cat)
        db.commit()
    assert first["created"] == {
        "college": 1, "department": 1, "course": 2, "specialization": 1, "batch": 2
    }

    with SessionLocal() as db:
        second = sc.seed(db, cat)
        db.commit()
    assert sum(second["created"].values()) == 0, second["created"]
    assert second["existing"]["batch"] == 2


@requires_db
def test_a_renamed_college_is_reported_and_left_alone(swept) -> None:
    """ADDITIVE ONLY. The office renames things on screen; a seeder that
    reasserted its own names would undo that work on every press."""
    cat = _throwaway()
    swept.append(cat)
    with SessionLocal() as db:
        sc.seed(db, cat)
        db.commit()
    with SessionLocal() as db:
        college = db.scalar(select(College).where(College.code == cat.college_code))
        college.name = "Renamed By The Office"
        db.commit()
    with SessionLocal() as db:
        summary = sc.seed(db, cat)
        db.commit()
        college = db.scalar(select(College).where(College.code == cat.college_code))
        assert college.name == "Renamed By The Office"
    assert any("left as it is" in d for d in summary["drift"])


@requires_db
def test_a_leaf_without_a_specialization_still_gets_a_batch(swept) -> None:
    """The whole point of the course rung: a student on such a course must be
    seatable, or they have no ancestry at all and no track can be read."""
    cat = _throwaway()
    swept.append(cat)
    with SessionLocal() as db:
        sc.seed(db, cat)
        db.commit()
    with SessionLocal() as db:
        code = sc.batch_code(cat, cat.courses[1], None, "2025-27")
        row = db.scalar(select(Cohort).where(Cohort.code == code))
        assert row is not None
        assert row.specialization_id is None
        assert row.course_id is not None
        # The three NOT NULL columns the first version of this module omitted.
        assert row.degree_level is DegreeLevel.UG, "the course's own level, not a default"
        assert row.start_date is not None and row.end_date is not None
        assert row.start_date < row.end_date


# ------------------------------------------- the columns CI caught me missing --


def test_every_course_declares_a_degree_level() -> None:
    """`cohorts.degree_level` is NOT NULL, and it is not bookkeeping.

    CAUGHT BY CI, not by reading: the first version of this module omitted it
    and every batch insert failed. But the insert failing is the SMALL half --
    the column gates which vacancies a batch sees (`models/cohort.py`'s first
    line), so a value defaulted here rather than declared would have shown a
    cohort somebody else's jobs, with nothing on any screen to explain it.
    There is deliberately no default on `Course`, so a new college cannot be
    added without someone stating this.
    """
    for cat in sc.CATALOGUES.values():
        for course in cat.courses:
            assert isinstance(course.degree_level, DegreeLevel), course


def test_an_mba_is_postgraduate() -> None:
    """Every BGSCET course is an MBA, so every one of them is PG."""
    assert all(c.degree_level is DegreeLevel.PG for c in BGSCET.courses)


def test_batch_dates_span_the_label() -> None:
    start, end = sc.batch_dates("2025-27")
    assert (start.year, start.month, start.day) == (2025, 7, 1)
    assert (end.year, end.month, end.day) == (2027, 6, 30)


def test_a_four_digit_end_year_reads_the_same() -> None:
    assert sc.batch_dates("2025-2027") == sc.batch_dates("2025-27")


def test_the_end_is_the_day_before_the_year_would_restart() -> None:
    """Subtracting a day rather than naming month-1, so the convention can be
    moved to January without the arithmetic becoming month zero."""
    start, end = sc.batch_dates("2025-27")
    assert (end + __import__("datetime").timedelta(days=1)).month == start.month


@pytest.mark.parametrize("bad", ["2025", "abc-27", "2025-2025", "2025-2099", ""])
def test_a_label_that_is_not_a_programme_is_refused(bad: str) -> None:
    """A batch carrying dates nobody meant is worse than one that failed to
    write: status filters, promotion and graduation all read them, and nothing
    on screen would say the span came from a parse that gave up."""
    with pytest.raises(ValueError):
        sc.batch_dates(bad)


def test_every_declared_batch_label_parses() -> None:
    """The labels in this file are the ones that will actually be written."""
    for cat in sc.CATALOGUES.values():
        for label in cat.batches:
            start, end = sc.batch_dates(label)
            assert start < end


# ------------------------------------------------------------------- the CLI --


def test_an_unknown_college_is_refused_rather_than_defaulted() -> None:
    """Seeding the wrong college into a deployment leaves a catalogue somebody
    unpicks by hand -- there is no destructor for one."""
    assert sc.main(["--college", "NOPE"]) == 2
    assert sc.main([]) == 2


def test_every_required_cohort_column_is_supplied() -> None:
    """THE GUARD THAT WOULD HAVE CAUGHT THIS BEFORE CI DID.

    The first version of this module supplied five of `cohorts`' required
    columns. CI found it, but only by failing an INSERT -- which names whichever
    column Postgres reached first (`degree_level`) and said nothing about
    `start_date` and `end_date`, so fixing what the error named would have left
    two still missing and cost a second round.

    This reads the requirement off `Cohort.__table__` instead of restating it,
    so it cannot go stale: a column added to that table with no default fails
    HERE, in the `api` job, naming itself. `cohorts` has already grown
    `degree_level` and `status` in separate rounds, which is the evidence that
    it will grow again.

    A column with a Python-side or server-side default is excluded on purpose --
    the row is valid without it, which is what a default means.
    """
    cat = sc.CATALOGUES["1MP"]
    course, spec = sc.leaves(cat)[0]
    supplied = set(
        sc.cohort_fields(cat, course, spec, "2025-27", "dep", "crs", "spc")
    )
    required = {
        c.name
        for c in Cohort.__table__.columns
        if not c.nullable
        and c.default is None
        and c.server_default is None
        and not c.primary_key
    }
    missing = required - supplied
    assert not missing, (
        f"seed_catalogue.cohort_fields does not supply {sorted(missing)}, which "
        "cohorts requires. Every batch insert would fail -- and the error names "
        "only the first one Postgres reaches."
    )


def test_the_supplied_fields_are_all_real_columns() -> None:
    """The other direction: a typo'd key is a TypeError at `Cohort(**fields)`,
    raised inside a transaction with the real cause several frames up."""
    cat = sc.CATALOGUES["1MP"]
    course, spec = sc.leaves(cat)[0]
    supplied = set(
        sc.cohort_fields(cat, course, spec, "2025-27", "dep", "crs", "spc")
    )
    assert not supplied - {c.name for c in Cohort.__table__.columns}


# ------------------------------------------------------ the select/insert race --


class _FakeNested:
    """A `begin_nested()` context manager. Does not swallow the exception."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RacingSession:
    """A session where somebody else inserts between our SELECT and our INSERT.

    `scalar` answers None first (nothing there yet) and a row afterwards (the
    other run committed), and `flush` raises the unique-constraint error.
    """

    def __init__(self, second_answer):
        self.answers = [None, second_answer]
        self.rolled_back_to_savepoint = False

    def scalar(self, *_a, **_k):
        return self.answers.pop(0) if self.answers else None

    def begin_nested(self):
        self.rolled_back_to_savepoint = True
        return _FakeNested()

    def add(self, _row):
        pass

    def flush(self):
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))


def test_losing_the_race_returns_the_other_runs_row() -> None:
    """Two runs both find nothing and both insert; the loser must not crash.

    The Ops task serialises its own button (`concurrency: ops-task`), but this
    module is runnable as a one-off ECS task with a command override, which
    nothing serialises -- and this file's docstring promises idempotency without
    that caveat.
    """
    winner = object()
    db = _RacingSession(winner)
    row, created = sc._get_or_create(db, College, College.code == "X", code="X", name="X")
    assert row is winner
    assert created is False


def test_the_insert_is_wrapped_in_a_savepoint() -> None:
    """NOT a bare try/except, and this is the whole reason the fix is eight
    lines rather than three.

    Postgres ABORTS a transaction on error and refuses every later statement in
    it, so catching IntegrityError and re-querying inside the same transaction
    raises again -- code that looks like recovery and turns one clear failure
    into a confusing one. `begin_nested()` issues a SAVEPOINT so the rollback
    undoes only the failed INSERT.
    """
    db = _RacingSession(object())
    sc._get_or_create(db, College, College.code == "X", code="X", name="X")
    assert db.rolled_back_to_savepoint, "the insert ran outside a savepoint"


def test_an_integrity_error_that_is_not_the_race_is_re_raised() -> None:
    """A re-select finding NOTHING means we did not lose a race.

    Some other constraint fired -- a `cohorts.code` collision with another
    college's batch, say -- and reporting that as "already existed" would hide
    exactly the mistake worth catching.
    """
    db = _RacingSession(None)
    with pytest.raises(IntegrityError):
        sc._get_or_create(db, College, College.code == "X", code="X", name="X")
