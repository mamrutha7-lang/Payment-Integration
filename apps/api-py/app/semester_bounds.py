"""How many semesters a student's programme has — B4.2's one answer.

`admin_students.MAX_SEMESTER = 8` was a FLAT CEILING on every student on every
deployment, expressed as a Pydantic `Field(le=8)` at three call sites. It was
wrong in both directions and quietly: an MBA runs four semesters, so semester 7
was accepted on a programme that has no semester 7 and the roster printed it;
a five-year integrated course has ten, so the console refused a number that is
simply correct and there was nothing on screen to say why.

THE BOUND IS A PROPERTY OF THE COURSE, so it is resolved through the student's
batch — `students.cohort_id -> cohorts.course_id -> academic_courses.
total_semesters` — and this module is the only place that walk is written.

A BATCH WITH NO COURSE KEEPS THE OLD BOUND, and that is deliberate rather than
a gap. `cohorts.course_id` is optional by HIERARCHY_LEVELS' design and
`total_semesters` is nullable for the reason every other column on
`academic_courses` is (an admin creates the course with a code and a name and
fills the rest in later). A deployment that has not named its courses yet must
keep working exactly as it did, so the fallback is the number that was there
before — `DEFAULT_MAX_SEMESTER`. Tightening it to, say, 4 would refuse edits on
rows nobody can fix, which is the failure HIERARCHY_LEVELS' `incomplete` escape
hatch exists to prevent.

NOTHING HERE RAISES HTTPException, following `app/student_placement.py`: this
module answers a question about the institution and the routers decide what a
wrong answer costs. `rejection()` returns the sentence to put in the 422 so the
wording is written once and the two surfaces (a single edit, a whole batch)
cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models.cohort import Cohort
from .models.institution import AcademicCourse

#: The bound that applied to everybody before B4.2, and the fallback for a
#: student whose batch names no course or whose course names no length. Keep
#: this number equal to the old `admin_students.MAX_SEMESTER`: it is what makes
#: "nothing changes for a deployment that has not filled in its courses" true.
DEFAULT_MAX_SEMESTER = 8


@dataclass(frozen=True)
class SemesterCeiling:
    """The highest semester a student may sit in, and where the number came from.

    `source` is carried because the two answers mean different things to the
    person reading the refusal: "the MBA has 4 semesters" is a fact they can
    check, while "8" is a default they may want to replace by filling in the
    course. A 422 that does not say which is a 422 nobody can act on.
    """

    value: int
    #: "course" when an `academic_courses.total_semesters` answered, "default"
    #: when nothing did.
    source: str
    course_id: str | None = None
    course_name: str | None = None

    @property
    def from_course(self) -> bool:
        return self.source == "course"


def course_for_cohort(db: Session, cohort_id: str | None) -> AcademicCourse | None:
    """The admissions programme a batch belongs to, or None.

    `cohorts.course_id` is one of the denormalised ancestor pointers whose only
    writer is `_resolve_ancestry` (app/routers/admin.py), so reading it here is
    reading the value that walk already checked — never a second derivation.
    """
    if not cohort_id:
        return None
    course_id = db.scalar(select(Cohort.course_id).where(Cohort.id == cohort_id))
    if not course_id:
        return None
    return db.get(AcademicCourse, course_id)


def ceiling_for_cohort(db: Session, cohort_id: str | None) -> SemesterCeiling:
    """The bound every student seated in this batch shares."""
    course = course_for_cohort(db, cohort_id)
    if course is None or course.total_semesters is None:
        return SemesterCeiling(
            value=DEFAULT_MAX_SEMESTER,
            source="default",
            course_id=course.id if course is not None else None,
            course_name=course.name if course is not None else None,
        )
    return SemesterCeiling(
        value=int(course.total_semesters),
        source="course",
        course_id=course.id,
        course_name=course.name,
    )


def ceiling_for_student(db: Session, cohort_id: str | None) -> SemesterCeiling:
    """The same answer, asked of one student through the batch they sit in.

    Takes the cohort id rather than the `Student` on purpose: the single-student
    edit may MOVE a student and set their semester in one request, and the
    course that bounds them is the one they are moving TO. Passing the row would
    read the pointer as it was before the patch — the same trap `_domain_fence`
    documents about the college that admits an address.
    """
    return ceiling_for_cohort(db, cohort_id)


def rejection(ceiling: SemesterCeiling, semester: int) -> str | None:
    """The 422's sentence, or None when the semester is within the bound."""
    if semester <= ceiling.value:
        return None
    if ceiling.from_course:
        return (
            f"Semester {semester} is past the end of this programme: "
            f"{ceiling.course_name} runs {ceiling.value} semesters. "
            "A student at the final semester graduates; they are not promoted past it."
        )
    return (
        f"Semester {semester} is above the default bound of {ceiling.value}. "
        "This batch names no course with a semester count, so the deployment-wide "
        "default applies — set Total semesters on the course to raise it."
    )


#: A semester's length when the course does not say. Two a year is what every
#: programme on this deployment runs; a course that names both
#: `duration_months` and `total_semesters` overrides it.
DEFAULT_MONTHS_PER_SEMESTER = 6


def semester_on(db: Session, cohort_id: str | None, today: date) -> int:
    """The semester a student seated in this batch is in TODAY.

    WHY THIS EXISTS. The registration form asks for a batch ("2025-27") and
    never for a semester, and approval created every student at the column's
    default of 1 - so a student who registered in their third semester was
    written down as a first-semester student, and every export and screen
    repeated it. The batch already knows when it started; the semester is how
    many semester-lengths have elapsed since then, plus one.

    Bounded both ways: a batch that has not started yet is semester 1, and one
    past its end sits at the programme's last semester (graduation is a
    decision the office makes, never something a clock does). No batch means 1,
    the old answer, because there is nothing to count from.
    """
    if not cohort_id:
        return 1
    cohort = db.get(Cohort, cohort_id)
    if cohort is None or cohort.start_date is None:
        return 1
    start = cohort.start_date.date()
    course = course_for_cohort(db, cohort_id)
    months_per = DEFAULT_MONTHS_PER_SEMESTER
    if course is not None and course.duration_months and course.total_semesters:
        months_per = max(1, round(course.duration_months / course.total_semesters))
    elapsed = (today.year - start.year) * 12 + (today.month - start.month)
    if today.day < start.day:
        elapsed -= 1
    semester = max(0, elapsed) // months_per + 1
    return min(semester, ceiling_for_cohort(db, cohort_id).value)
