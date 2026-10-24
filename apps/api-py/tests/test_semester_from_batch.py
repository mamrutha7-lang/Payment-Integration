"""An approved student's semester is read off the batch they joined.

The registration form asks for a batch and never for a semester, and approval
used to leave `students.current_semester` at its default of 1 -- so a student
who registered in their third semester was exported as semester 1. These pin
`semester_bounds.semester_on`, which `_provision_student` now calls, without a
database: the function reads two rows, and a stand-in session supplies them.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.models.cohort import Cohort
from app.models.institution import AcademicCourse
from app.semester_bounds import semester_on


class _Db:
    def __init__(self, cohort, course=None):
        self._rows = {(Cohort, cohort.id): cohort}
        if course is not None:
            self._rows[(AcademicCourse, course.id)] = course
        self._course_id = cohort.course_id

    def get(self, model, key):
        return self._rows.get((model, key))

    def scalar(self, _stmt):  # course_for_cohort's one query: the batch's course_id
        return self._course_id


def _batch(start: date, course_id: str | None = None):
    return SimpleNamespace(
        id="b1",
        course_id=course_id,
        start_date=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
    )


MBA = SimpleNamespace(id="c1", name="MBA", duration_months=24, total_semesters=4)
JULY_2025 = date(2025, 7, 1)


def test_a_batch_that_started_fifteen_months_ago_is_in_its_third_semester():
    db = _Db(_batch(JULY_2025, "c1"), MBA)
    assert semester_on(db, "b1", date(2026, 10, 6)) == 3


def test_the_semester_turns_on_the_day_and_not_before():
    db = _Db(_batch(JULY_2025, "c1"), MBA)
    assert semester_on(db, "b1", date(2026, 1, 1)) == 2
    assert semester_on(db, "b1", date(2025, 12, 31)) == 1


def test_a_batch_that_has_not_started_is_semester_one():
    db = _Db(_batch(date(2027, 7, 1), "c1"), MBA)
    assert semester_on(db, "b1", date(2026, 10, 6)) == 1


def test_a_finished_batch_stops_at_the_programmes_last_semester():
    db = _Db(_batch(date(2020, 7, 1), "c1"), MBA)
    assert semester_on(db, "b1", date(2026, 10, 6)) == 4


def test_a_batch_with_no_course_counts_six_month_semesters():
    db = _Db(_batch(JULY_2025))
    assert semester_on(db, "b1", date(2026, 10, 6)) == 3


def test_no_batch_is_semester_one():
    assert semester_on(SimpleNamespace(), None, date(2026, 10, 6)) == 1
