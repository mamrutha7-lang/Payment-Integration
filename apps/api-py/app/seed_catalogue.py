"""The institutional catalogue: ``python -m app.seed_catalogue --college 1MP``.

WHAT THIS IS FOR. The spine -- College -> Department -> Course -> Specialization
-> Batch -> Student -- is the backbone every other decision in REEP hangs off:
rule 2's scope, a grant's reach, a feature override's blast radius, the policy a
student's interview runs under, and which interview TRACK their mock interview
preselects. Until this release the only ways to write it were the Main Admin's
Catalogue screen, one form at a time, and `app.seed`, which refuses on
`ENV=prod` because it also mints the demo logins.

That left a real deployment with exactly one route: about fifteen forms per
college, typed by hand, with one field in them -- a specialization's `code` --
that silently decides whether that college's students can ever be SCORED. Get it
wrong and nothing anywhere reports it; the student simply meets a picker that
says "General interview", and a generic interview has no wrap-up phase and so
produces no scorecard. That is the same dead end `grant_access --department-id`
was added to close, and closing it at the account while leaving it open at the
catalogue is closing one of two doors into the same room.

So the catalogue is CODE and only the student state is a row -- the milestone
and badge rule (`app/models/milestone.py`, `app/models/badge.py`) applied to the
institution. A college is a block in `CATALOGUES` below, reviewed in a pull
request, checked by a test, and written by pressing one button.

PRODUCTION-SAFE AND IDEMPOTENT, like `app.seed_kb` and unlike `app.seed`: it
creates no accounts, holds no passwords and has nothing to refuse on `ENV=prod`.
Re-running it writes nothing it has already written. It is ADDITIVE ONLY -- it
never renames, never deletes, never archives. A college that has diverged from
this file is reported, not corrected: a seeder that "fixed" a name the office
changed on screen last week would be a seeder that undoes people's work every
time somebody presses the button.

THE CODES ARE THE WHOLE POINT, so read this before adding a college.
`routers/interview_policy._default_track` preselects a student's interview track
by exact, case-folded match, most specific rung first: a track mapped to the
batch's specialization, the specialization's own `code`, a track mapped to the
course, the course's own `code`. So a specialization coded `fa` gets the
Financial Analytics interviewer and one coded `FIN` gets nothing at all. The
model's own column comment suggests `FIN`; that comment predates the interview
matrix and is the trap this module exists to keep people out of.

Codes that match nothing today are LEGITIMATE and are not an error: BGSCET's
Marketing specialization and its Logistics & Supply Chain course have no
built-in track, and the office can add one on the Interview Tracks screen
whenever it likes -- at which point the match starts working with no change
here, because both halves are the same string. What this module will not do is
let that be a surprise: every run prints, per leaf, whether a student there gets
a preselected track today and names the code that would make it so.

WHY IT SEEDS BATCHES TOO. A catalogue with no batch places nobody:
`ancestry_of_student` reaches the course and specialization rungs through
`cohorts`, so a student not seated in a batch has no course and no
specialization, whatever the catalogue says. One batch per LEAF of the tree is
therefore the smallest useful thing, not an extra.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models.cohort import Cohort
from .models.job import DegreeLevel
from .models.institution import (
    STATUS_ACTIVE,
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)

log = logging.getLogger("reep.catalogue")


@dataclass(frozen=True)
class Spec:
    """One specialization. `code` is the interview-track key -- see the module docstring."""

    code: str
    name: str


@dataclass(frozen=True)
class Course:
    """One programme in the admissions catalogue.

    `specializations` may be EMPTY and that is a real shape, not an omission: a
    two-year MBA in Digital Marketing IS the qualification, with nothing to
    specialise into. Such a course carries the track key on its own `code`, which
    is why `_default_track` reads the course rung.
    """

    code: str
    name: str
    #: UG or PG. REQUIRED AND DEFAULTED NOWHERE, because `cohorts.degree_level`
    #: is not bookkeeping: it gates which vacancies the batch sees
    #: (`models/cohort.py`'s own first line). A default would file a new
    #: college's courses as whatever this file happened to guess, and the
    #: symptom would be students seeing the wrong jobs with nothing on any
    #: screen to explain it. It sits on the COURSE and not the college because
    #: one institution can run both.
    degree_level: DegreeLevel
    specializations: tuple[Spec, ...] = ()


@dataclass(frozen=True)
class Catalogue:
    college_code: str
    college_name: str
    department_code: str
    department_name: str
    courses: tuple[Course, ...]
    #: Batch labels to open, e.g. ("2025-27",). One batch per LEAF per label.
    batches: tuple[str, ...] = ()


#: One block per college. Adding a college is adding a block and a pull request.
CATALOGUES: dict[str, Catalogue] = {
    "1MP": Catalogue(
        college_code="1MP",
        college_name="BGS College of Engineering and Technology",
        department_code="MBA",
        department_name="Master of Business Administration",
        courses=(
            Course(
                code="gen",
                name="General MBA",
                degree_level=DegreeLevel.PG,
                specializations=(
                    # Three of these four match a built-in interview track by
                    # code. `marketing` deliberately does not: the only
                    # marketing track in the matrix is DIGITAL marketing, which
                    # is a different discipline and is one of this college's
                    # other courses -- coding it `dm` would sit a Marketing
                    # student in front of a CMO asking about CAC/LTV ratios.
                    Spec(code="hr", name="Human Resources"),
                    Spec(code="marketing", name="Marketing"),
                    Spec(code="fa", name="Finance"),
                    Spec(code="ba", name="Business Analytics"),
                ),
            ),
            # No specializations, and the code IS the track key.
            Course(code="dm", name="Digital Marketing", degree_level=DegreeLevel.PG),
            Course(
                code="lscm",
                name="Logistics and Supply Chain Management",
                degree_level=DegreeLevel.PG,
            ),
        ),
        batches=("2025-27",),
    ),
}


def leaves(cat: Catalogue) -> list[tuple[Course, Spec | None]]:
    """Every path a student can actually sit on.

    A course WITH specializations contributes one leaf each and none for itself
    -- nobody is enrolled in "General MBA" without choosing one. A course
    WITHOUT them is its own leaf.
    """
    out: list[tuple[Course, Spec | None]] = []
    for course in cat.courses:
        if course.specializations:
            out.extend((course, spec) for spec in course.specializations)
        else:
            out.append((course, None))
    return out


def track_key(course: Course, spec: Spec | None) -> str:
    """The code `_default_track` will look for on this leaf."""
    return (spec.code if spec is not None else course.code).strip().lower()


def batch_code(cat: Catalogue, course: Course, spec: Spec | None, label: str) -> str:
    """`cohorts.code` is globally UNIQUE, so it carries the whole path.

    A human-readable key rather than a uuid, because this is the string an
    operator reads back off the Batches screen to check the run did what they
    expected.
    """
    parts = [cat.college_code, cat.department_code, course.code]
    if spec is not None:
        parts.append(spec.code)
    parts.append(label)
    return "-".join(p.strip().upper() for p in parts)


#: The month an academic year is taken to begin, for deriving a batch's dates.
#:
#: `cohorts.start_date` and `end_date` are NOT NULL, so a batch cannot be
#: written without them, and this file is not told the college's real term
#: dates. This is therefore a stated CONVENTION and not a fact: 1 July of the
#: first year to 30 June of the last. Both dates are printed by every run and
#: both are editable on the Batches screen, so an office whose term runs on
#: other dates corrects two fields rather than discovering a silent guess.
#:
#: The alternative -- inventing a narrower-looking date such as "1 August,
#: because that is when most Indian MBAs start" -- would read as researched
#: rather than assumed, which is worse than a round number that announces
#: itself.
ACADEMIC_YEAR_START_MONTH = 7


def batch_dates(label: str) -> tuple[datetime, datetime]:
    """``"2025-27"`` -> (1 Jul 2025, 30 Jun 2027), in UTC.

    A malformed label is REFUSED rather than defaulted. A batch carrying dates
    nobody meant is worse than one that failed to write: `status` filters,
    promotion and graduation all read these, and nothing on screen would say the
    span came from a parse that gave up.
    """
    head, _, tail = label.partition("-")
    if not head.isdigit() or not tail.isdigit():
        raise ValueError(
            f"batch label {label!r} is not <start>-<end>, e.g. '2025-27' or "
            "'2025-2027'. Refusing rather than guessing the dates."
        )
    start_year = int(head)
    end_year = int(tail)
    if end_year < 100:  # "27" means 2027, not the year 27
        end_year += start_year - start_year % 100
    if not start_year < end_year <= start_year + 10:
        raise ValueError(
            f"batch label {label!r} spans {start_year} to {end_year}, which is "
            "not a programme. Refusing rather than writing it."
        )
    start = datetime(start_year, ACADEMIC_YEAR_START_MONTH, 1, tzinfo=timezone.utc)
    # The day BEFORE the academic year would next begin: 1 Jul 2025 -> 30 Jun
    # 2027. Subtracting a day rather than naming month-1 keeps this correct if
    # the constant is ever moved to January, where month-1 is not a month.
    end = datetime(end_year, ACADEMIC_YEAR_START_MONTH, 1, tzinfo=timezone.utc) - timedelta(days=1)
    return start, end


def batch_name(label: str) -> str:
    """What the office reads on the Batches screen: THE YEAR, and only the year.

    A BATCH IS A YEAR. "2025-27" is the whole of what this row is -- the span a
    student who joined a two-year degree belongs to. Which course and which
    specialization they joined is NOT part of its name: it is the spine this row
    hangs off, and the row already carries every rung of it as a real foreign
    key (`department_id`, `course_id`, `specialization_id`, set right below in
    `cohort_fields`).

    THIS USED TO MANUFACTURE THE COURSE AND THE SPECIALIZATION INTO THE NAME --
    "General MBA - Finance 2025-27" -- and that was one fact stored twice: once
    as a foreign key `ancestry_of_student` reads, and once as words inside a
    string nothing can join on. The two could disagree the moment the office
    renamed a course on screen, with this row still printing last year's name;
    and because `batch_label` is its own column, every screen that showed a
    batch printed the year a second time next to it ("General MBA - Finance
    2025-27 · 2025-27"). `app/batch_labels.py` is where the spine and the
    year are now put back together, from the links, at read time.

    The department never belonged here either, for the reason it never did: it
    is already on the row, and prefixing it stutters the moment a department and
    its course share a word -- BGSCET's MBA department contains a course called
    "General MBA", which gave "MBA General MBA - Finance 2025-27".
    `batch_code` still carries the full path, because that is the column that
    has to be globally unique; the name is for a person.
    """
    return label


def cohort_fields(
    cat: Catalogue,
    course: Course,
    spec: Spec | None,
    label: str,
    department_id: str,
    course_id: str,
    specialization_id: str | None,
) -> dict[str, Any]:
    """Every column a new `cohorts` row needs, in one place.

    EXTRACTED SO A TEST CAN CHECK IT AGAINST THE TABLE ITSELF. The first version
    of this module supplied five of `cohorts`' eight required columns, and CI
    caught it -- but only by failing an INSERT, which names whichever column
    Postgres happened to reach first and says nothing about the other two. That
    table has grown `degree_level` and `status` in separate rounds already and
    will grow more; a hand-written list checked by a hand-written test goes
    stale the same way, in the same silence.

    `tests/test_seed_catalogue.py` compares these keys against
    `Cohort.__table__` and fails on any non-nullable column this dict omits, so
    the next person to add one is stopped here rather than in production.

    `degree_level` is the one that matters beyond the insert succeeding: it
    gates which vacancies the batch sees, which is why it is declared per course
    rather than defaulted.
    """
    start, end = batch_dates(label)
    return {
        "code": batch_code(cat, course, spec, label),
        "name": batch_name(label),
        "batch_label": label,
        "department_id": department_id,
        "course_id": course_id,
        "specialization_id": specialization_id,
        "degree_level": course.degree_level,
        "start_date": start,
        "end_date": end,
    }


# ---------------------------------------------------------------- the write --


def _get_or_create(db: Session, model: Any, where: Any, **fields: Any) -> tuple[Any, bool]:
    """Find one row or add it. Returns (row, created).

    ADDITIVE ONLY. An existing row is returned UNTOUCHED even where its fields
    differ from this file -- see the module docstring: the office renames things
    on screen, and a seeder that reasserted its own names would quietly undo
    that work on every run. Divergence is reported by `seed()`, never corrected.

    THE SELECT-THEN-INSERT IS A RACE, AND IT IS CLOSED WITH A SAVEPOINT RATHER
    THAN A BARE `except`. Two runs that both find nothing both insert, and the
    loser hits the unique constraint. The Ops task carries `concurrency:
    ops-task`, so two button presses queue rather than overlap -- but this module
    is also runnable as a one-off ECS task with a command override, which nothing
    serialises, and "idempotent" is a promise this file makes in its own
    docstring.

    THE OBVIOUS FIX DOES NOT WORK ON POSTGRES, which is why this is worth the
    eight lines. Catching `IntegrityError` and re-querying inside the same
    transaction fails: Postgres ABORTS a transaction on error and refuses every
    later statement in it ("current transaction is aborted, commands ignored
    until end of transaction block"), so the recovering SELECT raises too. The
    result would LOOK like graceful recovery and would in fact turn one clear
    failure into a confusing one. `begin_nested()` issues a SAVEPOINT, so the
    rollback undoes only the failed INSERT and the outer transaction -- which
    holds every row written so far, and commits once at the end -- survives.

    A re-select that finds NOTHING is re-raised rather than swallowed. Only a
    row matching this call's own `where` proves we lost the race; any other
    IntegrityError (a different constraint, a `cohorts.code` collision with some
    other college's batch) is a real defect, and reporting it as "already
    existed" would hide exactly the mistake worth catching.
    """
    row = db.scalar(select(model).where(where))
    if row is not None:
        return row, False
    try:
        with db.begin_nested():  # SAVEPOINT; see the docstring
            row = model(**fields)
            db.add(row)
            db.flush()  # so children can reference the id, and so the
            # constraint fires HERE, inside the savepoint
    except IntegrityError:
        row = db.scalar(select(model).where(where))
        if row is None:
            raise
        return row, False
    return row, True


def seed(db: Session, cat: Catalogue) -> dict[str, Any]:
    """Write one college's catalogue. Caller commits."""
    summary: dict[str, Any] = {
        "college": cat.college_code,
        "created": {"college": 0, "department": 0, "course": 0, "specialization": 0, "batch": 0},
        "existing": {"college": 0, "department": 0, "course": 0, "specialization": 0, "batch": 0},
        "drift": [],
    }

    def tally(kind: str, created: bool) -> None:
        summary["created" if created else "existing"][kind] += 1

    college, made = _get_or_create(
        db,
        College,
        College.code == cat.college_code,
        code=cat.college_code,
        name=cat.college_name,
        status=STATUS_ACTIVE,
    )
    tally("college", made)
    if not made and college.name != cat.college_name:
        summary["drift"].append(
            f"college {cat.college_code}: named {college.name!r} here, "
            f"{cat.college_name!r} in this file - left as it is"
        )

    department, made = _get_or_create(
        db,
        Department,
        (Department.college_id == college.id) & (Department.code == cat.department_code),
        college_id=college.id,
        code=cat.department_code,
        name=cat.department_name,
        status=STATUS_ACTIVE,
    )
    tally("department", made)

    course_rows: dict[str, Any] = {}
    spec_rows: dict[tuple[str, str], Any] = {}
    for course in cat.courses:
        row, made = _get_or_create(
            db,
            AcademicCourse,
            (AcademicCourse.department_id == department.id)
            & (AcademicCourse.code == course.code),
            department_id=department.id,
            code=course.code,
            name=course.name,
            status=STATUS_ACTIVE,
        )
        course_rows[course.code] = row
        tally("course", made)
        for spec in course.specializations:
            srow, smade = _get_or_create(
                db,
                AcademicSpecialization,
                (AcademicSpecialization.course_id == row.id)
                & (AcademicSpecialization.code == spec.code),
                course_id=row.id,
                code=spec.code,
                name=spec.name,
                status=STATUS_ACTIVE,
            )
            spec_rows[(course.code, spec.code)] = srow
            tally("specialization", smade)

    for label in cat.batches:
        for course, spec in leaves(cat):
            code = batch_code(cat, course, spec, label)
            crow = course_rows[course.code]
            srow = spec_rows[(course.code, spec.code)] if spec is not None else None
            start, end = batch_dates(label)
            _, made = _get_or_create(
                db,
                Cohort,
                Cohort.code == code,
                **cohort_fields(
                    cat,
                    course,
                    spec,
                    label,
                    department.id,
                    crow.id,
                    srow.id if srow is not None else None,
                ),
            )
            tally("batch", made)
            summary.setdefault("batch_dates", {})[label] = (
                f"{start:%d %b %Y} to {end:%d %b %Y}"
            )

    return summary


# ------------------------------------------------------------- the honesty --


def track_report(db: Session, cat: Catalogue) -> list[str]:
    """Per leaf: will a student seated here get a preselected interview track?

    THIS IS THE HALF THAT MAKES THE SEEDER WORTH HAVING. Writing the rows is
    ten lines of SQL; knowing which of them lead to an interview that can be
    SCORED is the thing no screen in the product tells anybody. A leaf whose
    code matches no track is not an error -- the office may not have built that
    track yet -- but it must never be a surprise, so every run says so and names
    the exact string that would fix it.
    """
    from .interview_matrix import SPECIALIZATIONS
    from .models.interview_track import InterviewTrack

    known = {k.strip().lower() for k in SPECIALIZATIONS}
    known |= {
        (c or "").strip().lower()
        for c in db.scalars(
            select(InterviewTrack.code).where(InterviewTrack.enabled.is_(True))
        )
    }

    lines: list[str] = []
    for course, spec in leaves(cat):
        key = track_key(course, spec)
        where = f"{course.name}" + (f" / {spec.name}" if spec else "")
        if key in known:
            lines.append(f"  OK   {where}  ->  track '{key}'")
        else:
            lines.append(
                f"  NONE {where}  ->  no track '{key}'. Students here meet the "
                f"picker and are not preselected; add a track with code '{key}' "
                "on the Interview Tracks screen and this starts working."
            )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed_catalogue",
        description=(
            "Write a college's catalogue - department, courses, specializations "
            "and batches. Idempotent, additive, safe on production."
        ),
    )
    parser.add_argument(
        "--college",
        default="",
        help=f"college code to seed, one of: {', '.join(sorted(CATALOGUES))}",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write. WITHOUT THIS NOTHING IS COMMITTED (dry run is the default)",
    )
    args = parser.parse_args(argv)

    if args.college not in CATALOGUES:
        # Named rather than silently defaulted: seeding the wrong college into a
        # deployment is a catalogue somebody has to unpick by hand, and there is
        # no destructor for it.
        log.error(
            "Pass --college with one of: %s (got %r)",
            ", ".join(sorted(CATALOGUES)),
            args.college,
        )
        return 2
    cat = CATALOGUES[args.college]

    db = SessionLocal()
    try:
        summary = seed(db, cat)
        report = track_report(db, cat)
        if args.apply:
            db.commit()
        else:
            # DRY RUN IS THE DEFAULT, the two purges' rule. The work is done
            # inside the transaction so the counts are real -- a dry run that
            # only guessed would not catch a unique-constraint collision -- and
            # then thrown away.
            db.rollback()
    except Exception:
        db.rollback()
        log.exception("The catalogue was NOT written.")
        return 1
    finally:
        db.close()

    verb = "wrote" if args.apply else "would write"
    log.info(
        "%s %s: %s",
        verb,
        cat.college_code,
        ", ".join(f"{n} {k}" for k, n in summary["created"].items() if n),
    )
    kept = ", ".join(f"{n} {k}" for k, n in summary["existing"].items() if n)
    if kept:
        log.info("already present (left untouched): %s", kept)
    for line in summary["drift"]:
        log.warning("%s", line)
    for label, span in sorted(summary.get("batch_dates", {}).items()):
        # Printed because they are a CONVENTION, not a fact this file was told.
        log.info("Batch %s runs %s (a convention - edit on the Batches screen)", label, span)
    log.info("Interview track preselection, per leaf:")
    for line in report:
        log.info("%s", line)
    if not args.apply:
        log.info("DRY RUN - nothing was committed. Re-run with --apply to write it.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    raise SystemExit(main())
