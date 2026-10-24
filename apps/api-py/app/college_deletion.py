"""Delete ONE college and its whole structure — departments, courses,
specializations, batches and the configuration hung on them — and REFUSE while
anybody is filed under it.

The other half of `app.seed_catalogue`, which writes the institutional spine
and is ADDITIVE ONLY. Nothing removed a college before 2026-09-16: the console
could archive one (`PATCH /api/admin/colleges/{id}` with `status=ARCHIVED`),
and a demonstration deployment that had been set up three times over carried
three colleges' worth of departments and batches in every picker. The owner
asked for a Delete button on the Colleges screen and for "every college except
this one" to go; `python -m app.purge_colleges --keep 1MP` is the second half
and this module is what both call.

WHAT GOES, decided by `COLLEGE_POLICY` through `deletion_walk.Walk`: the
college, its departments, their courses, the courses' specializations, every
batch under any of them, and the rows that are configuration OF this college
and mean nothing without it — its academic calendar, its interview policies,
the stage rules and badge-course map of its courses. `alert_rule_configs`
cascades from the batch on its own.

WHAT STAYS, UNSCOPED. Catalogue rows that merely POINTED at the college keep
existing and lose the pointer, and the plan counts each so the office sees it
before pressing: job postings (a posting scoped to a deleted college becomes
programme-wide, which the dialog says in words), approved certifications,
placement criteria, interview tracks (a track mapped to one of its courses
becomes unmapped and stops preselecting anybody), bank questions, spreadsheet
runs. Registrations lose their college too (`SET NULL` in the schema) — but
see the refusal below.

WHAT IT REFUSES, AND THIS IS THE POINT: PEOPLE. `students.cohort_id`,
`students.department_id` and `users.department_id` carry no ON DELETE on
purpose ("a batch with people in it is deleted by first saying what happens
to the people", `admin_students.delete_cohort`). They are `REFUSE_IF_ANY`
here, so a college with one student seated or one faculty account filed under
it cannot be deleted — the refusal names the counts and the office moves them
on the roster and Faculty screens first. An application still in the review
queue that names this college is refused for the same reason: approving it
afterwards would file a student under nothing.

A deleted college is gone from every picker at once; the students and staff
of the OTHER colleges are untouched, because nothing here reaches a row that
does not hang under this college's id. `tests/test_college_deletion.py` runs
the real delete against the real schema and rolls it back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Base
from .deletion_walk import CLEAR_POINTER, DELETE_ROW, REFUSE_IF_ANY, DeletionRefused, Walk, probe_policy
from .models.institution import College
from .models.registration import Registration, RegistrationStatus

log = logging.getLogger("reep.college_deletion")

COLLEGE_ROOTS: tuple[str, ...] = ("colleges",)

#: Every foreign key the walk reaches from `colleges` whose clause the schema
#: left undecided. Read the module docstring for the three groups.
COLLEGE_POLICY: dict[tuple[str, str], str] = {
    # -- the structure: goes with the college ---------------------------------
    ("departments", "college_id"): DELETE_ROW,
    ("academic_courses", "department_id"): DELETE_ROW,
    ("academic_specializations", "course_id"): DELETE_ROW,
    ("cohorts", "department_id"): DELETE_ROW,
    ("cohorts", "course_id"): DELETE_ROW,
    ("cohorts", "specialization_id"): DELETE_ROW,
    # -- configuration OF this college: meaningless without it ----------------
    ("academic_calendar", "college_id"): DELETE_ROW,
    ("interview_policies", "college_id"): DELETE_ROW,
    ("interview_policies", "course_id"): DELETE_ROW,
    ("badge_course_map", "course_id"): DELETE_ROW,
    ("stage_rules", "course_id"): DELETE_ROW,
    # -- catalogue rows that pointed here: stay, un-scoped ---------------------
    ("approved_certifications", "college_id"): CLEAR_POINTER,
    ("approved_certifications", "course_id"): CLEAR_POINTER,
    ("import_runs", "college_id"): CLEAR_POINTER,
    ("interview_bank_questions", "college_id"): CLEAR_POINTER,
    ("interview_tracks", "college_id"): CLEAR_POINTER,
    ("interview_tracks", "course_id"): CLEAR_POINTER,
    ("interview_tracks", "specialization_id"): CLEAR_POINTER,
    ("jobs", "college_id"): CLEAR_POINTER,
    ("jobs", "course_id"): CLEAR_POINTER,
    ("placement_criteria", "college_id"): CLEAR_POINTER,
    ("placement_criteria", "course_id"): CLEAR_POINTER,
    # -- PEOPLE: move them first -----------------------------------------------
    ("students", "cohort_id"): REFUSE_IF_ANY,
    ("students", "department_id"): REFUSE_IF_ANY,
    ("users", "department_id"): REFUSE_IF_ANY,
}

#: Applications the office has not decided yet. One of these naming the
#: college stops the delete; a decided one (approved into a student, or
#: rejected) merely loses its pointer, which is the schema's own SET NULL.
OPEN_REGISTRATION_STATUSES: tuple[RegistrationStatus, ...] = tuple(
    s for s in RegistrationStatus if s not in (RegistrationStatus.APPROVED, RegistrationStatus.REJECTED)
)

#: How the refusal names each people column, in the console's words.
_PEOPLE_LABELS: dict[str, str] = {
    "students.cohort_id": "student(s) seated in its batches",
    "students.department_id": "student(s) filed under its departments",
    "users.department_id": "faculty account(s) filed under its departments",
}


class CollegeDeleteRefused(DeletionRefused):
    """Raised before anything is destroyed."""


@dataclass
class CollegePlan:
    college_id: str
    code: str
    name: str
    rows: dict[str, int] = field(default_factory=dict)
    cleared: dict[str, int] = field(default_factory=dict)
    #: People and open applications that stop this delete. Empty means it may go.
    blockers: dict[str, int] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())

    @property
    def deletable(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict:
        return {
            "college_id": self.college_id,
            "code": self.code,
            "name": self.name,
            "rows": dict(sorted(self.rows.items())),
            "cleared": dict(sorted(self.cleared.items())),
            "blockers": dict(sorted(self.blockers.items())),
            "total_rows": self.total_rows,
            "deletable": self.deletable,
        }

    def refusal(self) -> str:
        parts = []
        for key, n in sorted(self.blockers.items()):
            label = _PEOPLE_LABELS.get(key, key)
            parts.append(f"{n} {label}")
        return (
            f"{self.code} still has people attached: " + "; ".join(parts) + ". Move them to "
            "another college first (Students and Faculty screens) and decide the open "
            "applications; nothing was changed."
        )


def walk_for(college_id: str) -> Walk:
    colleges = Base.metadata.tables["colleges"]
    return Walk(roots={"colleges": colleges.c.id == college_id}, policy=COLLEGE_POLICY)


def _open_registrations(db: Session, college_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(
                Registration.college_id == college_id,
                Registration.status.in_(OPEN_REGISTRATION_STATUSES),
            )
        )
        or 0
    )


def build_plan(db: Session, college: College) -> CollegePlan:
    walk = walk_for(college.id)
    plan = CollegePlan(college_id=college.id, code=college.code, name=college.name)
    plan.rows = walk.count_rows(db)
    plan.cleared = walk.count_cleared(db)
    plan.blockers = walk.count_refusals(db)
    open_regs = _open_registrations(db, college.id)
    if open_regs:
        plan.blockers["registrations.open"] = open_regs
    return plan


def delete_rows(db: Session, college_id: str) -> dict[str, int]:
    """Pointers cleared, then the structure children-first. NO COMMIT."""
    walk = walk_for(college_id)
    walk.clear_pointers(db)
    return walk.delete_rows(db)


def execute(db: Session, plan: CollegePlan) -> None:
    """Destroy one college. Re-checks the blockers INSIDE the transaction, so a
    student seated between the dialog and the button still stops it."""
    college = db.get(College, plan.college_id)
    if college is None:
        raise CollegeDeleteRefused("That college is already gone.")
    fresh = build_plan(db, college)
    if not fresh.deletable:
        raise CollegeDeleteRefused(fresh.refusal())
    delete_rows(db, college.id)
    # A Core count, not `db.get`: the identity map still holds the College
    # instance `build_plan` loaded, and `db.get` would answer from it without
    # asking the database whether the row is gone.
    colleges = Base.metadata.tables["colleges"]
    survived = db.scalar(select(func.count()).select_from(colleges).where(colleges.c.id == plan.college_id))
    if survived:
        db.rollback()
        raise CollegeDeleteRefused("The college row survived its own delete; rolled back.")
    db.commit()
    db.expire_all()


def policy_problems() -> list[str]:
    return probe_policy(list(COLLEGE_ROOTS), COLLEGE_POLICY)
