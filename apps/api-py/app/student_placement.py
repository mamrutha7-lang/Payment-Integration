"""Where a student sits in the institution, when no batch says so.

The student counterpart of `staff_placement.py`, and it exists for the same
reason: `students.cohort_id` was the ONLY pointer a student had, so a student
with no batch had no department and no college either — `_institution_for`
returned an empty card and every department-scoped read skipped them.

WHY THIS IS NOT "JUST SEAT THEM IN A BATCH". The public registration form asks
for College and Department as REQUIRED, and Course / Specialization / Batch as
optional (`HIERARCHY_LEVELS`). A college that has not built its batches yet —
which is every college on its first day — therefore produces applicants who
named a department and could be seated in nothing. Provisioning stored the
batch and dropped the department on the floor, so the one fact the applicant was
forced to give was the one fact the system did not keep.

THE INVARIANT, and it is the whole point of this module. A student now has two
pointers that can both name a department (`students.cohort_id` -> the batch's
department, and `students.department_id`), which is one more chance to
disagree — the split brain `_resolve_ancestry` was written to prevent on
`cohorts`. The rule here is the same one, in the same shape:

  * a batch that names a department WINS, and a contradicting `department_id`
    from the client is a 422 naming both, never a silent pick;
  * a batch that names no department does NOT overwrite a department the client
    gave — that is strictly more information, not a contradiction;
  * with no batch, the client's department stands on its own.

So `students.department_id` is always either derived from the batch or set
deliberately, and can never quietly say something the batch denies.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models.cohort import Cohort
from .models.institution import Department


class DepartmentContradiction(Exception):
    """The client named a department the chosen batch does not sit under.

    Carries both ids so the router can name them in the refusal. A message that
    says only "contradiction" leaves the admin guessing which of the two values
    they typed is the wrong one.
    """

    def __init__(self, sent: str, derived: str) -> None:
        super().__init__(f"{sent!r} contradicts the batch's department {derived!r}")
        self.sent = sent
        self.derived = derived


def resolve_department(db: Session, department_id: str | None) -> Department | None:
    """The department a caller named, or None when they named nothing.

    Raises LookupError for an id that does not exist, so the router answers 422
    rather than filing a student under nothing — a write that reports success
    and stores null is the failure mode this whole change is about. Same
    contract as `staff_placement.resolve_department`, deliberately: two callers
    that mean the same thing should not refuse differently.
    """
    if department_id is None or not str(department_id).strip():
        return None
    dep = db.get(Department, str(department_id).strip())
    if dep is None:
        raise LookupError(department_id)
    return dep


def department_of_cohort(db: Session, cohort_id: str | None) -> str | None:
    """The department a batch sits under, or None (no batch, or an unfiled one)."""
    if not cohort_id:
        return None
    cohort = db.get(Cohort, cohort_id)
    return cohort.department_id if cohort is not None else None


def resolve_student_department(
    db: Session,
    *,
    cohort_id: str | None,
    department_id: str | None,
    department_sent: bool,
) -> str | None:
    """The department id to store on a student row.

    `cohort_id` and `department_id` are the values AFTER the request is applied
    (for a PATCH: the row's current values overlaid with what was sent), and
    `department_sent` says whether the client actually supplied a department —
    the only case in which a derivation can contradict something a human typed.

    Raises LookupError (unknown department) or DepartmentContradiction; the
    router turns both into a 422 that names the offending value.
    """
    derived = department_of_cohort(db, cohort_id)
    if derived is not None:
        if department_sent and department_id and department_id != derived:
            raise DepartmentContradiction(sent=department_id, derived=derived)
        # The batch is the authority whenever it has one. This is also what
        # keeps an existing row honest when it is MOVED to another batch
        # without the client mentioning departments at all.
        return derived
    # No batch, or a batch nobody filed: the client's department stands, and is
    # validated so an unknown id refuses instead of storing null.
    dep = resolve_department(db, department_id)
    return dep.id if dep is not None else None
