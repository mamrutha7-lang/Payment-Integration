"""Who verifies a student's skills and documents: the faculty the office chose.

SKILL VERIFICATION STOPPED BEING A MENTOR'S BY MENTORING (2026-10-16, the
owner's decision). Until then `mentor.verifications` ("Verify skills &
evidence") was DERIVED: every faculty member who mentored somebody held it, for
their own group, with nobody deciding it (app/mentor_functions.py). The owner
asked for it to be taken away from every mentor and given only to the faculty
the placement office chooses. So it is a GRANT now and nothing else: made in
Who can do what, with a reason, a reach (a student, a batch, a department, a
college, or the programme) and an optional end date, and revocable the same
hour. Mentoring somebody brings none of it, and a mentor who is granted it
reaches the grant's students, not their group.

THE GRANT'S REACH IS THE WHOLE ANSWER, AND ONE MODULE GIVES IT. Five queues and
doors verify something -- the skill-claim queue, its reviewed strip, a claim's
file and its decision (routers/badge_verification.py), the documents queue and
its decision, a document's file, and the legacy skill-claim review
(routers/mentor.py) -- and they live in two routers that cannot import each
other. Each used to compose its own fence out of rule 2 and, for the claims,
a grant door beside it. That is how one of them ends up still admitting a
mentor's group after the rest stop: the list empty, the decision open. So
every one of them asks here.

DELIBERATELY NOT A CHANGE TO RULE 2. `_assert_can_access_student` still gates
every other per-student staff endpoint and still admits a mentor to their own
group (notes, the notebook, the 360 read). Verification simply stopped asking
it.

The Main Admin is held to the same grant. `_FACULTY_ONLY` keeps this key out of
the office account's baseline on purpose, so the office looks at a stuck
student's evidence by granting itself the instrument, with a reason, on the
trail -- and now a scoped grant it makes to itself narrows the decision as well
as the list, where until today rule 2 let it decide any student once it held
the key anywhere.
"""

from __future__ import annotations

from fastapi import HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .governance import ROLE_BASELINE, ancestry_of_student, granted_reaches, reaches_target
from .models.governance import AccessGroupMember, CapabilityGrant, ScopeLevel, SubjectKind
from .models.user import Role, Student, User
from .policies import Reach, scope_filter
from .scope_views import scope_header

#: "Verify skills & evidence". The one key every verification door asks for.
VERIFY_CAPABILITY = "mentor.verifications"


def verification_reach(db: Session, session: dict, response: Response | None = None) -> Reach:
    """How far this session's verification reaches, and the scope header that
    says so (`X-Reep-Scope`), so a reach of nothing renders as "you cannot see
    this" rather than as an empty queue."""
    reach = scope_filter(db, session, VERIFY_CAPABILITY)
    if response is not None:
        scope_header(response, reach)
    return reach


def narrow_to_reach(query, reach: Reach):
    """`query` narrowed to the students `reach` covers, or None when it covers
    nobody. The query must already join `Student`."""
    if reach.nothing:
        return None
    if reach.everything:
        return query
    return query.where(Student.id.in_(reach.student_ids()))


def can_verify(
    db: Session,
    session: dict,
    student_id: str,
    *,
    ancestry: list[tuple[ScopeLevel, str]] | None = None,
) -> bool:
    """May this session verify THIS student's evidence? The per-row twin of
    `narrow_to_reach`, so a row that is listed can be opened and decided and one
    that is not cannot. `ancestry` may be passed in by a caller asking the same
    question for many people about one student."""
    role = str(session.get("role") or "")
    if VERIFY_CAPABILITY in ROLE_BASELINE.get(role, frozenset()):
        return True
    reaches = granted_reaches(db, str(session.get("userId") or ""), VERIFY_CAPABILITY)
    if not reaches:
        return False
    if ancestry is None:
        ancestry = ancestry_of_student(db, student_id)
    return reaches_target(reaches, ancestry)


def assert_can_verify(db: Session, session: dict, student_id: str, *, detail: str) -> None:
    """404 with `detail` unless `can_verify`. Every refusal is the same 404 as a
    wrong id, so an out-of-reach id cannot be told apart from one that does not
    exist."""
    if not can_verify(db, session, student_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def verifiers_for(db: Session, student_id: str) -> list[User]:
    """Every live staff account whose verification reaches this student: the
    people a new claim is waiting for.

    Asked through `can_verify`, the same question every door asks, so this list
    and the gate cannot disagree about who may press Verify. The SQL only
    narrows the candidates to accounts a grant NAMES (directly or through a
    group) or whose role carries the key by baseline; liveness (revoked,
    expired, awaiting a second signature, role changed since) is decided by
    `granted_reaches`, never restated here. The student's own account is never
    on it.
    """
    direct = select(CapabilityGrant.subject_user_id).where(
        CapabilityGrant.capability == VERIFY_CAPABILITY,
        CapabilityGrant.subject_kind == SubjectKind.USER,
    )
    via_group = (
        select(AccessGroupMember.user_id)
        .join(CapabilityGrant, CapabilityGrant.subject_group_id == AccessGroupMember.group_id)
        .where(
            CapabilityGrant.capability == VERIFY_CAPABILITY,
            CapabilityGrant.subject_kind == SubjectKind.GROUP,
        )
    )
    by_baseline = [
        Role(role) for role, keys in ROLE_BASELINE.items() if VERIFY_CAPABILITY in keys
    ]
    named = User.id.in_(direct) | User.id.in_(via_group)
    if by_baseline:
        named = named | User.role.in_(by_baseline)
    student_user_id = db.scalar(select(Student.user_id).where(Student.id == student_id))
    ancestry = ancestry_of_student(db, student_id)
    candidates = db.scalars(
        select(User)
        .where(
            User.deleted_at.is_(None),
            User.disabled_at.is_(None),
            User.role.in_([Role.MENTOR, Role.ADMIN]),
            named,
        )
        .order_by(User.email)
    ).all()
    return [
        u
        for u in candidates
        if u.id != student_user_id
        and can_verify(db, {"role": u.role.value, "userId": u.id}, student_id, ancestry=ancestry)
    ]
