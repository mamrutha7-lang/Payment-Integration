"""A student's second specialization, in one place (2026-09-23).

A student who opted for a DUAL specialization sits in ONE batch, and a batch
hangs on one specialization at most. So the first of the two is the batch's own,
read through `students.cohort_id` exactly as it always was, and
`students.second_specialization_id` is the other one and nothing else.

Three writers reach that column -- approval (`_provision_student` copies the
application's ticks), the Main Admin's roster editor (`PATCH
/admin/students/{id}`) and every move of a student to another batch -- and all
three ask the questions below, so "which of the two ticks is the second" and
"does this second still fit the batch" have one answer each.
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy.orm import Session

from .models.cohort import Cohort
from .models.institution import AcademicCourse, AcademicSpecialization


def batch_specialization_id(db: Session, cohort_id: str | None) -> str | None:
    """The specialization the batch itself hangs on, or None."""
    if not cohort_id:
        return None
    cohort = db.get(Cohort, cohort_id)
    return cohort.specialization_id if cohort is not None else None


def second_for_seat(db: Session, cohort_id: str | None, picks: Sequence[str | None]) -> str | None:
    """Of an application's ticks, the one the batch does NOT already say.

    The batch is the first specialization, so the second is whichever tick is
    left once the batch's own is taken out. A student who ticked one box, or
    whose batch is not yet chosen and who ticked one, has no second. Where the
    batch names neither tick (an unseated student, or a batch at course level)
    the second tick is the second -- the office seats them later, and a batch
    under the second stream clears it through `settle_after_move`.
    """
    named = [p for p in picks if p]
    if len(named) < 2:
        return None
    own = batch_specialization_id(db, cohort_id)
    others = [p for p in named if p != own]
    if own in named:
        return others[0] if others else None
    return named[1]


def settle_after_move(db: Session, student) -> bool:
    """Clear a second specialization the student's (new) batch contradicts.

    It no longer fits when it IS the batch's own (the student was seated in
    the second stream's batch, so it is now their first), or when the batch
    sits under another course. Returns True when it cleared something, so a
    caller can say so. An unseated student, or a batch at department level,
    keeps it: nothing contradicts it.
    """
    second_id = student.second_specialization_id
    if not second_id or not student.cohort_id:
        return False
    cohort = db.get(Cohort, student.cohort_id)
    if cohort is None:
        return False
    spec = db.get(AcademicSpecialization, second_id)
    contradicted = (
        spec is None
        or cohort.specialization_id == second_id
        or (cohort.course_id is not None and spec.course_id != cohort.course_id)
    )
    if contradicted:
        student.second_specialization_id = None
    return contradicted


def refusal(
    db: Session, *, second_id: str, cohort_id: str | None, department_id: str | None
) -> str | None:
    """Why this specialization cannot be the student's second, or None.

    It must exist, must not be the batch's own (that one is already theirs),
    and must sit under the batch's course -- a dual specialization is two
    streams of ONE course. With no course to compare against (an unseated
    student, a batch at department level), it must at least sit in the
    student's department.
    """
    spec = db.get(AcademicSpecialization, second_id)
    if spec is None:
        return "That specialization does not exist."
    cohort = db.get(Cohort, cohort_id) if cohort_id else None
    if cohort is not None and cohort.specialization_id == second_id:
        return (
            f"{spec.name} is already this student's specialization through their batch. "
            "Choose the other one of the two."
        )
    if cohort is not None and cohort.course_id is not None:
        if spec.course_id != cohort.course_id:
            course = db.get(AcademicCourse, cohort.course_id)
            where = course.name if course is not None else "the batch's course"
            return f"{spec.name} is not a specialization of {where}, the course of this student's batch."
        return None
    if department_id:
        course = db.get(AcademicCourse, spec.course_id)
        if course is None or course.department_id != department_id:
            return f"{spec.name} is not a specialization in this student's department."
    return None
