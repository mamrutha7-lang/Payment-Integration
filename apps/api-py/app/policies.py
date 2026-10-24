"""Fail-closed capability and student-scope policy for the v1 API."""

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import false as sa_false
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models.redesign import MembershipRole, TenantMembership
from .models.user import Student

# DIRECTOR IS NOT HERE, AND THAT IS THE POINT (2026-09-10).
#
# REEP has one office account: the Main Admin (ADMIN). DIRECTOR was a second
# admin under another name — its baseline was every console screen — and nothing
# has been able to create one since `app.grant_access` began refusing it. It
# survived only in the dev seed and in these role sets, which meant a row that
# could no longer be minted still opened every door if one existed.
#
# The enum value stays on `users.role` (dropping a Postgres enum value with any
# historical row is a destructive migration for no gain), but it now grants
# NOTHING anywhere: the migration that came with this change converted every
# DIRECTOR row to MENTOR and bumped its `token_version`, so no live session
# carries the claim either. `tests/test_no_director_privilege.py` pins it.
STAFF_ROLES = frozenset({"MENTOR", "ADMIN"})
PROGRAMME_ROLES = frozenset({"ADMIN"})
NOTEBOOK_ROLES = frozenset({"MENTOR"})


def require_role(session: dict, *roles: str) -> dict:
    """Require an explicit role; missing or malformed identity is denied."""
    role = session.get("role")
    if not isinstance(role, str) or role not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role.")
    if not session.get("userId"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required.")
    return session


def require_staff(session: dict) -> dict:
    return require_role(session, *sorted(STAFF_ROLES))


def require_programme_admin(session: dict) -> dict:
    return require_role(session, *sorted(PROGRAMME_ROLES))


def require_notebook_staff(session: dict) -> dict:
    """The private notebook is a FACULTY instrument: MENTOR only.

    Not the Main Admin, and not because it is trusted less — a notebook is a
    mentor's own working record of their own mentees, and the office account has
    neither. It can still be GRANTED `mentor.notebook` in Governance when it has
    to stand in for a faculty member; that grant is the audited way in, and this
    role gate is deliberately not it.
    """
    return require_role(session, *sorted(NOTEBOOK_ROLES))


def tenant_id_for_session(session: dict, db: Session) -> str | None:
    """Resolve one active tenant without invalidating legacy sessions.

    Old sessions have no tenant claim and some existing users have not yet been
    provisioned into the additive membership table. In that compatibility case
    this returns None, preserving the pre-tenant path. If memberships exist, a
    claimed or unambiguous tenant is required and verified on every request; an
    ambiguous multi-tenant identity fails closed rather than guessing.
    """
    user_id = session.get("userId")
    role = session.get("role")
    if not user_id or not isinstance(role, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required.")
    try:
        membership_role = MembershipRole(role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid tenant role.") from exc
    query = select(TenantMembership).where(
        TenantMembership.user_id == user_id,
        TenantMembership.role == membership_role,
        TenantMembership.status == "ACTIVE",
        TenantMembership.ended_at.is_(None),
    )
    memberships = db.scalars(query).all()
    claimed = session.get("tenantId")
    if claimed:
        if not any(row.tenant_id == claimed for row in memberships):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant membership is not active.")
        return str(claimed)
    tenant_ids = {row.tenant_id for row in memberships}
    if len(tenant_ids) > 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant context is required.")
    return next(iter(tenant_ids), None)


def assert_student_scope(
    session: dict,
    student_id: str,
    db: Session,
    *,
    allow_handover: bool = False,
    allow_records_grant: bool = False,
) -> Student:
    """Return a student only when the current role is allowed to access it.

    ------------------------------------------------------------------------
    `allow_records_grant` — the office's "View student records" grant
    ------------------------------------------------------------------------

    The owner's decision (2026-10-15): when the Main Admin gives a faculty
    member "View student records" (`admin.student_records`) for one student,
    a batch, a department or the college, that person may OPEN those
    students' records, read only, whether or not they mentor them. Until then
    the grant only ever reached the holder's own mentees, because this check
    ran underneath it.

    It is the handover's shape, wider, and it is fenced the same three ways.
    It is a keyword, it defaults to FALSE, and only the GET reads of the
    Student 360 page pass it, so no write path is touched. It asks the GRANT
    (`_holds_records_grant`), so revoking it in Governance closes the door
    the same hour. And it honours only a grant that NAMES A RUNG: a
    programme-wide `admin.student_records` grant was written when the grant
    meant "your own mentees", so reading it as "every student" would widen
    every such grant silently, on deploy.

    ------------------------------------------------------------------------
    `allow_handover` — B9.1's 90-day window, AND WHY IT IS A KEYWORD
    ------------------------------------------------------------------------

    This function gates THIRTY-SIX call sites and FIFTEEN OF THEM ARE WRITES:
    adding and deleting mentor notes, resolving alerts, reviewing uploads and
    skill claims, approving badge evidence (which MINTS an EARNED badge),
    manual awards, recording capability assessments, six notebook mutations.
    It has no idea which it is serving — a branch that returns the student
    returns it to a POST exactly as readily as to a GET.

    So the handover cannot be a branch that is simply "on": that would turn a
    90-day READ into a 90-day right to write about a student who is now
    somebody else's. It is a parameter, it is keyword-only, it defaults to
    FALSE, and it is passed True at GET call sites only. Every existing caller
    keeps today's behaviour exactly, and opening the window for a write is
    something a person has to type on purpose.

    WHAT IT CHECKS IS A GRANT, NOT THIS TABLE'S `to_at`. `app/mentor_history.py`
    mints a `mentor.mentees` grant scoped to the one student with a 90-day
    expiry when a mentee is released, and `holds_handover_for` asks whether that
    grant is live. Reading the window off `mentor_assignments.to_at` instead
    would leave the door open after an administrator revoked the grant in
    Governance, which is two sources of truth for one permission.

    THE GRANT MUST NAME THIS EXACT STUDENT. `holds_handover_for` refuses to use
    `reaches_target`, which a PROGRAMME-WIDE `mentor.mentees` grant satisfies
    for every student alive — see its docstring. Rule 2 and the capability fence
    are checked separately on purpose (app/governance.py: "a capability can
    never relax the student filter"), and this is the one place where a careless
    reading would collapse them.

    IT ALSO REACHES A STUDENT'S RECORDED VOICE, and that is worth naming rather
    than discovering: `routers/interview_records.py`'s audio download goes
    through this gate. It carries a second `admin.interview_audio` check, which
    a MENTOR holds only by an explicit grant, so a handover alone does not open
    it — but the next person to relax that second check should know this one is
    behind it.
    """
    require_staff(session)
    tenant_id = tenant_id_for_session(session, db)
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    if tenant_id:
        student_membership = db.scalar(
            select(TenantMembership.id).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == student.user_id,
                TenantMembership.role == MembershipRole.STUDENT,
                TenantMembership.status == "ACTIVE",
                TenantMembership.ended_at.is_(None),
            )
        )
        if student_membership is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not in this tenant.")
    if session["role"] == "MENTOR" and (
        not session.get("mentorId") or student.mentor_id != session["mentorId"]
    ):
        # THE HANDOVER IS ASKED LAST, and it is asked of the GRANT rather than
        # of this session's claim. `Mentor` rows are never deleted, so a
        # released mentor's cookie still carries a live `mentorId` — keying the
        # window on "this session has a mentorId" would be true for every
        # faculty member who has ever had one mentee. The grant names a user and
        # a student, which is the pair that actually decides this.
        user_id = str(session.get("userId") or "")
        if not (
            (allow_handover and _holds_handover(db, user_id, student.id))
            or (allow_records_grant and _holds_records_grant(db, user_id, student.id))
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not in your mentor scope.")
    return student


def _holds_handover(db: Session, user_id: str, student_id: str) -> bool:
    """Imported inside the call for `scope_filter`'s reason: `mentor_history`
    reaches into `governance`, which reaches into `mentor_functions`, and a
    module-level import here closes the cycle."""
    from .mentor_history import holds_handover_for

    return holds_handover_for(db, user_id, student_id)


#: The capability whose scoped grant opens a student's record read-only. A
#: plain constant so the Student 360 router and this module name one key.
RECORDS_CAPABILITY = "admin.student_records"


def _holds_records_grant(db: Session, user_id: str, student_id: str) -> bool:
    """Does this account hold a live, SCOPED "View student records" grant that
    reaches this student?

    Scoped only: `(None, None)` -- a programme-wide grant -- is left out on
    purpose, see `assert_student_scope`. Everything else is the ordinary
    question `require_capability(..., target=...)` asks, through the same two
    functions, so liveness, approval and B2.5's role check all apply here
    without being restated. Imported inside the call for `_holds_handover`'s
    reason: `governance` imports `mentor_functions`, which would close a cycle.
    """
    if not user_id:
        return False
    from .governance import ancestry_of_student, granted_reaches, reaches_target

    scoped = [
        reach
        for reach in granted_reaches(db, user_id, RECORDS_CAPABILITY)
        if reach[0] is not None
    ]
    return bool(scoped) and reaches_target(scoped, ancestry_of_student(db, student_id))


def student_identity(session: dict) -> str:
    """Derive the student id from the verified session, never from request JSON."""
    require_role(session, "STUDENT")
    student_id = session.get("studentId")
    if not student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Student profile is not provisioned.")
    return str(student_id)


# --------------------------------------------------------------------------- #
# B1.2 — how far a scoped grant reaches, for the endpoints that return LISTS
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Reach:
    """The rungs of the spine one session may see, for one capability.

    WHY THIS IS NOT A SQLALCHEMY FILTER, which is what 04-backend-changes.md
    asks for by name. The twelve list endpoints B1.4 narrows do not query one
    model: the registrations queue selects Registration, the faculty directory
    selects User, the roster selects Student, the leave queue selects
    LeaveRequest, and the three export endpoints return CSV built from joins.
    One `filter` object cannot be dropped into all of them, and a function that
    pretends otherwise gets used in the two places it happens to fit and
    re-implemented in the other ten — which is the outcome "one helper, never
    re-implemented" exists to prevent.

    So this answers the question ONCE — what may you see — and hands out a
    subquery per model. The reach is the same object either way; only the
    projection differs.

    `everything` is the Main Admin and anyone holding a programme-wide grant.
    It is checked first everywhere, because the alternative is materialising
    every id on the deployment to express "no restriction".
    """

    everything: bool
    colleges: frozenset[str] = frozenset()
    departments: frozenset[str] = frozenset()
    courses: frozenset[str] = frozenset()
    specializations: frozenset[str] = frozenset()
    cohorts: frozenset[str] = frozenset()
    students: frozenset[str] = frozenset()

    @property
    def nothing(self) -> bool:
        """True when this session may see no records at all.

        A real state, and one that must read differently from `everything` at
        every call site: a holder whose only grant is scoped to a department
        that has since been deleted reaches nothing, and a list that answered
        "no restriction" there would be the widest possible failure of the
        narrowest possible grant.
        """
        return not self.everything and not (
            self.colleges
            or self.departments
            or self.courses
            or self.specializations
            or self.cohorts
            or self.students
        )

    def student_ids(self):
        """A SELECT of the student ids this reach covers, for `Student.id.in_(…)`.

        TAKES NO SESSION, deliberately. It used to, and never used it: a `db`
        in the signature is an invitation for the next editor to execute inside
        the helper, which turns a composable subquery into a materialised list
        of ids — the enumeration OpenFGA and AuthZed both warn against, and the
        thing this shape exists to avoid.

        A subquery rather than a predicate over the caller's own FROM clause,
        because the caller's query shape is not ours to change: several of these
        endpoints already join Cohort, several already narrow by mentor group,
        and a helper that demanded its own joins would force every one of them
        to be rewritten around it.

        BOTH DEPARTMENT POINTERS ARE READ, as in `governance.ancestry_of_student`:
        a student reaches a department through their batch or through
        `students.department_id`, and a scoped holder who could see the seated
        students of their department but not the unseated ones would be looking
        at a list with a hole in it that nothing on screen explains.
        """
        from .models.cohort import Cohort
        from .models.institution import Department
        from .models.user import Student

        # EVERYTHING SELECTS EVERYTHING. This used to fall through to the empty
        # clause list below and return NO ROWS, on the theory that every caller
        # checks `.everything` first. That is a convention across twelve list
        # endpoints, and the cost of forgetting it once is an EMPTY ROSTER FOR
        # THE MAIN ADMIN — a screen saying the college has no students, which
        # reads as data loss rather than a permission bug, on the account that
        # holds every capability. A helper whose most dangerous output is
        # produced by its commonest caller omitting one line is built wrong.
        if self.everything:
            return select(Student.id)

        batch_department = (
            select(Cohort.department_id).where(Cohort.id == Student.cohort_id).scalar_subquery()
        )
        department = func.coalesce(batch_department, Student.department_id)
        clauses = []
        if self.students:
            clauses.append(Student.id.in_(self.students))
        if self.cohorts:
            clauses.append(Student.cohort_id.in_(self.cohorts))
        if self.specializations:
            clauses.append(
                Student.cohort_id.in_(
                    select(Cohort.id).where(Cohort.specialization_id.in_(self.specializations))
                )
            )
        if self.courses:
            clauses.append(
                Student.cohort_id.in_(select(Cohort.id).where(Cohort.course_id.in_(self.courses)))
            )
        if self.departments:
            clauses.append(department.in_(self.departments))
        if self.colleges:
            clauses.append(
                department.in_(
                    select(Department.id).where(Department.college_id.in_(self.colleges))
                )
            )
        if not clauses:
            return select(Student.id).where(sa_false())
        return select(Student.id).where(or_(*clauses))

    def user_ids(self):
        """A SELECT of the STAFF user ids this reach covers.

        Shorter than a student's, because a faculty account is filed under a
        department and nothing else (`governance.ancestry_of_user`). An unfiled
        account is covered by nothing but `everything`, which is why the Faculty
        screen's unfiled list is the Main Admin's to work through.
        """
        from .models.institution import Department
        from .models.user import User

        if self.everything:
            return select(User.id)

        clauses = []
        if self.departments:
            clauses.append(User.department_id.in_(self.departments))
        if self.colleges:
            clauses.append(
                User.department_id.in_(
                    select(Department.id).where(Department.college_id.in_(self.colleges))
                )
            )
        if not clauses:
            return select(User.id).where(sa_false())
        return select(User.id).where(or_(*clauses))


def scope_filter(db: Session, session: dict, key: str) -> Reach:
    """How far this session's `key` reaches. The one answer, for every list.

    A capability held through the ROLE BASELINE is unscoped — see
    `governance.require_capability` for why scope is a property of a grant and
    not of a role. Everything else is the union of the session's live grants for
    that key: a programme-wide grant short-circuits to `everything`, and scoped
    grants accumulate, because two grants are more reach than one and a holder
    with a department grant and a college grant sees both.
    """
    from .governance import ROLE_BASELINE, granted_reaches
    from .models.governance import ScopeLevel

    role = str(session.get("role") or "")
    if key in ROLE_BASELINE.get(role, frozenset()):
        return Reach(everything=True)

    buckets: dict[ScopeLevel, set[str]] = {level: set() for level in ScopeLevel}
    for level, target_id in granted_reaches(db, str(session.get("userId") or ""), key):
        if level is None:
            return Reach(everything=True)
        if target_id:
            buckets[level].add(target_id)

    return Reach(
        everything=False,
        colleges=frozenset(buckets[ScopeLevel.COLLEGE]),
        departments=frozenset(buckets[ScopeLevel.DEPARTMENT]),
        courses=frozenset(buckets[ScopeLevel.COURSE]),
        specializations=frozenset(buckets[ScopeLevel.SPECIALIZATION]),
        cohorts=frozenset(buckets[ScopeLevel.COHORT]),
        students=frozenset(buckets[ScopeLevel.STUDENT]),
    )
