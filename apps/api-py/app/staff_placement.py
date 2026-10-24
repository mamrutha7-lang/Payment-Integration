"""Where a staff member sits in the institution.

The faculty counterpart of `routers/student.py::_institution_for`, and written
the same way for the same reason: the placement is READ THROUGH A JOIN and
stored on nothing but the single pointer (`users.department_id`). Copying the
department name or the college code onto the staff row would be the backfill
this shape exists to avoid — rename a department and every copy is wrong.

WHY A DEPARTMENT AND NOT A COHORT. A student belongs to a batch, because a batch
is what a student is enrolled in. A staff member belongs to a department, which
is the unit the college itself uses: `departments.head` is a column, and the
official leave form prints a department line. The college is reached through the
department, exactly as a student's college is reached through the cohort.

BATCHED, because the faculty list renders every staff account at once. Resolving
one placement per row is the N+1 that makes an admin screen slow the week a
college finishes onboarding its faculty, so `placements_for` takes a set of user
ids and asks once.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models.institution import College, Department
from .models.user import User


@dataclass(frozen=True)
class StaffPlacement:
    """One staff member's institutional position, resolved.

    Every field is nullable and that is load-bearing: a staff account exists
    before anyone files it (the Main Admin creates the login first and places it
    afterwards), and a screen must be able to say "not filed yet" rather than
    inventing a department. `filed` is the honest boolean to branch on — never a
    falsy department name, which cannot distinguish "unfiled" from "a department
    whose name is empty".
    """

    department_id: str | None = None
    department_code: str | None = None
    department_name: str | None = None
    college_id: str | None = None
    college_code: str | None = None
    college_name: str | None = None

    @property
    def filed(self) -> bool:
        return self.department_id is not None

    @property
    def label(self) -> str:
        """One line for a card: "Management Studies · BGSCET"."""
        parts = [p for p in (self.department_name, self.college_code or self.college_name) if p]
        return " · ".join(parts)


UNFILED = StaffPlacement()


def placements_for(db: Session, user_ids: list[str]) -> dict[str, StaffPlacement]:
    """user_id -> placement, in ONE query. Unfiled ids are simply absent."""
    ids = [u for u in dict.fromkeys(user_ids) if u]
    if not ids:
        return {}
    rows = db.execute(
        select(
            User.id,
            Department.id,
            Department.code,
            Department.name,
            College.id,
            College.code,
            College.name,
        )
        .select_from(User)
        # INNER on department: a user with no department_id is not in the result
        # and the caller falls back to UNFILED. OUTER on college would be wrong
        # only if a department could exist without one, and it cannot —
        # departments.college_id is NOT NULL.
        .join(Department, Department.id == User.department_id)
        .join(College, College.id == Department.college_id)
        .where(User.id.in_(ids))
    ).all()
    return {
        user_id: StaffPlacement(
            department_id=dep_id,
            department_code=dep_code,
            department_name=dep_name,
            college_id=col_id,
            college_code=col_code,
            college_name=col_name,
        )
        for user_id, dep_id, dep_code, dep_name, col_id, col_code, col_name in rows
    }


def placement_for(db: Session, user_id: str) -> StaffPlacement:
    """One staff member's placement, or UNFILED."""
    return placements_for(db, [user_id]).get(user_id, UNFILED)


def resolve_department(db: Session, department_id: str | None) -> Department | None:
    """The department a caller named, or None when they named nothing.

    Raises LookupError for an id that does not exist, so the router can answer
    422 rather than silently filing a staff member under nothing — a write that
    reports success and stores null is the failure mode this whole change is
    about.
    """
    if department_id is None or not str(department_id).strip():
        return None
    dep = db.get(Department, str(department_id).strip())
    if dep is None:
        raise LookupError(department_id)
    return dep
