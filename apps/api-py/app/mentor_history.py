"""The one place `students.mentor_id` moving is written down — B9.1.

FIVE WRITERS SET THAT POINTER, AND `mentor_functions.py` NAMES THEM BY COUNT AS
A KNOWN HAZARD: `routers/admin_mentoring.py` (the assignment screen), two paths in
`routers/admin_students.py` (the single PATCH and the batch action),
`app/seed.py` and `app/grant_access.py`. A sixth arrives one day. Every one of
them calls `record_mentor_change` and nothing writes `mentor_assignments`
directly, so the history cannot be half-kept: the failure mode of the
alternative is not a crash, it is a student whose mentor changed with no row to
say so, discovered months later by somebody asking who moved them.

------------------------------------------------------------------------------
WHAT A MOVE DOES
------------------------------------------------------------------------------

At most two rows are touched, and always in this order:

  1. The OPEN row for this student (`to_at IS NULL`), if there is one, is
     closed: `to_at`, `end_kind`, `ended_by_user_id`, `end_reason`.
  2. A new open row is created for the incoming mentor, if there is one.

A release closes and opens nothing. An assignment onto a student who had nobody
opens and closes nothing. A reassignment does both and stamps `reassign` on each
end, which is what makes "moved from Dr Rao to Dr Iyer on the 3rd, by the office,
because Dr Rao is on sabbatical" one act read from two rows rather than two
unrelated events.

Setting the same mentor again writes NOTHING. The console's per-student loop
sends one request per selected student and a re-save of an unchanged row is
routine; a history that grows a row every time somebody presses Save is a
history nobody reads.

------------------------------------------------------------------------------
THE 90-DAY HANDOVER IS A SCOPED CAPABILITY GRANT, NOT A BRANCH IN RULE 2
------------------------------------------------------------------------------

04 says "the previous mentor keeps read access until `to_at + 90 d`
(`policies.assert_student_scope` honours it)". Implemented literally that is a
third pass-branch in the function that gates THIRTY-SIX call sites, FIFTEEN OF
WHICH ARE WRITES — mentor notes, badge-evidence approval that mints an EARNED
badge, capability assessments, notebook entries. `assert_student_scope` cannot
express "read only": a branch that returns the student returns it to a POST as
readily as to a GET, so a 90-day READ becomes a 90-day right to write about a
student who is now somebody else's.

So the window is a `capability_grants` row instead: `mentor.mentees`, subject
the departing faculty account, `scope_level=STUDENT` pinned to that one student,
`expires_at = now + 90 days`, `reason="handover"`. Everything that makes this
safe already exists and is used by every other grant — `_live_grant_clauses`
filters revocation, expiry and approval IN SQL, the Governance screen lists it,
revoking it closes the door the same hour, and `record_change` files the act.

AND IT FIXES A HAZARD THE BRANCH ALONE COULD NOT. `mentor_functions_for` derives
the four `mentor.*` capabilities live from `mentee_count > 0`, so a faculty
member whose last mentee was just reassigned holds NONE of them:
`require_capability` refuses at the door with 403 and `assert_student_scope`
never runs at all. A handover honoured only inside rule 2 would be invisible to
precisely the person it was built for. A grant is unioned into
`capabilities_for`, so the mentee log opens again — for that one student, until
the date.

ONE read-only branch in `assert_student_scope` is still needed, because the
grant answers "which screen" and rule 2 asks "which student". It is
`allow_handover=False` by default and passed True at GET call sites only; see
`app/policies.py`.

`mentor.mentees` DOES NOT CARRY PII IN THE CATALOGUE, so this grant is written
`active` and works immediately. Were it ever marked `carries_pii`, B2.4 would
write it `pending_approval` and every handover on a one-admin deployment would
silently hold nothing — `tests/test_mentor_assignments.py` pins the activation
rather than the flag, so that change fails there and not in a support call.

NO GRANT IS MINTED FOR `faculty_disabled`. A disabled account cannot make a
request at all, so the grant would reach nothing; what it would do is put a live
handover row on the Governance screen for somebody who has been offboarded,
which reads as access nobody revoked.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .architecture_events import record_change
from .models.governance import (
    APPROVAL_ACTIVE,
    CapabilityGrant,
    ScopeLevel,
    SubjectKind,
)
from .models.mentor_assignment import (
    END_FACULTY_DISABLED,
    END_REASSIGN,
    END_RELEASE,
    HANDOVER_DAYS,
    KIND_ASSIGN,
    KIND_REASSIGN,
    MentorAssignment,
)
from .models.user import Mentor, User

log = logging.getLogger("reep.mentor_history")

#: The capability a handover hands back. One key, named once, so the grant that
#: is written and the branch that reads it cannot drift apart.
HANDOVER_CAPABILITY = "mentor.mentees"

#: What `reason` a handover grant carries. Read by nothing — it is the sentence
#: the Governance screen shows the office next to a grant they did not make by
#: hand, and "handover" is the only word that explains it there.
HANDOVER_REASON = "handover"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def open_assignment(db: Session, student_id: str) -> MentorAssignment | None:
    """The row for the spell this student is in now, or None."""
    return db.scalar(
        select(MentorAssignment).where(
            MentorAssignment.student_id == student_id,
            MentorAssignment.to_at.is_(None),
        )
    )


def record_mentor_change(
    db: Session,
    *,
    student_id: str,
    previous_mentor_id: str | None,
    new_mentor_id: str | None,
    by_user_id: str | None,
    reason: str | None,
    end_kind: str | None = None,
    session: dict | None = None,
    request: Any | None = None,
) -> MentorAssignment | None:
    """Write the history for one move. Returns the new open row, if any.

    FLUSHES, NEVER COMMITS. Every caller is inside a transaction that also moves
    `students.mentor_id`, and the pointer and its history must land together or
    not at all — a committed history row over a rolled-back pointer is a record
    of something that did not happen.

    `end_kind` overrides the act that closed the previous spell. It exists for
    `faculty_disabled`, which is the one closing act that is not "somebody
    pressed a button on the assignment screen"; everything else derives it from
    whether there is a successor.

    `session` and `request` are optional, and their absence is the CLI case —
    `app/seed.py` and `app/grant_access.py` have no request to audit against.
    When they are present the act is filed with `record_change` as well, because
    a grant minted by a release is a governance act and the audit trail is where
    the office reads those.
    """
    now = _now()
    previous = open_assignment(db, student_id)

    # Nothing to do. A re-save of the same pairing is routine on a screen that
    # posts one request per ticked student, and it must not grow the history.
    if previous_mentor_id == new_mentor_id and (previous is not None or new_mentor_id is None):
        return previous

    if previous is not None:
        previous.to_at = now
        previous.end_kind = end_kind or (END_REASSIGN if new_mentor_id else END_RELEASE)
        previous.ended_by_user_id = by_user_id
        previous.end_reason = reason

    created: MentorAssignment | None = None
    if new_mentor_id is not None:
        created = MentorAssignment(
            student_id=student_id,
            mentor_id=new_mentor_id,
            from_at=now,
            kind=KIND_REASSIGN if previous is not None else KIND_ASSIGN,
            by_user_id=by_user_id,
            reason=reason,
        )
        db.add(created)

    db.flush()

    # The handover. Only when somebody actually lost a student, and never for an
    # offboarded account — see the module docstring.
    if previous is not None and previous.end_kind != END_FACULTY_DISABLED:
        grant_handover_read(
            db,
            student_id=student_id,
            departing_mentor_id=previous.mentor_id,
            by_user_id=by_user_id,
            session=session,
            request=request,
        )

    return created


def grant_handover_read(
    db: Session,
    *,
    student_id: str,
    departing_mentor_id: str,
    by_user_id: str | None,
    session: dict | None = None,
    request: Any | None = None,
) -> CapabilityGrant | None:
    """Mint the 90-day read grant for the mentor who just lost this student.

    Idempotent against an existing live grant at the same rung, following
    `routers/governance.py`'s `already_live`: two releases in a month must not
    stack two windows, and the second must not silently shorten or extend the
    first.
    """
    faculty_user_id = db.scalar(select(Mentor.user_id).where(Mentor.id == departing_mentor_id))
    if not faculty_user_id:
        return None
    # A disabled or deleted account is not handed a window. `role_at_grant`
    # would refuse it on the read side anyway; refusing here keeps the
    # Governance screen from listing a grant that can never be used.
    faculty = db.get(User, faculty_user_id)
    if faculty is None or faculty.barred_at is not None:
        return None

    now = _now()
    live = db.scalar(
        select(CapabilityGrant.id).where(
            CapabilityGrant.capability == HANDOVER_CAPABILITY,
            CapabilityGrant.subject_kind == SubjectKind.USER,
            CapabilityGrant.subject_user_id == faculty_user_id,
            CapabilityGrant.scope_level == ScopeLevel.STUDENT,
            CapabilityGrant.scope_id == student_id,
            CapabilityGrant.revoked_at.is_(None),
            or_(CapabilityGrant.expires_at.is_(None), CapabilityGrant.expires_at > now),
        )
    )
    if live is not None:
        return None

    grant = CapabilityGrant(
        capability=HANDOVER_CAPABILITY,
        subject_kind=SubjectKind.USER,
        subject_user_id=faculty_user_id,
        scope_level=ScopeLevel.STUDENT,
        scope_id=student_id,
        reason=HANDOVER_REASON,
        granted_by_user_id=by_user_id,
        expires_at=now + timedelta(days=HANDOVER_DAYS),
        # WRITTEN ACTIVE, not pending. `mentor.mentees` does not carry PII in
        # the catalogue, so B2.4's second signature does not apply — and a
        # handover that waited for one would hold nothing on the single-admin
        # deployment this product is built for.
        approval_state=APPROVAL_ACTIVE,
        # B2.5. The role this decision is about. A faculty member who becomes
        # the Main Admin next month stops holding it, which is correct: the
        # decision named a mentor handing over their mentee.
        role_at_grant=faculty.role.value,
        # NO `review_at`. A grant with an expiry does not need a review date —
        # it ends by itself, which is the property this whole design was chosen
        # for. `review_at` exists for the grants that never lapse.
    )
    db.add(grant)
    db.flush()

    if session is not None and request is not None:
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="capability_grant", entity_id=grant.id, action="GRANTED",
            before=None,
            after={
                "capability": HANDOVER_CAPABILITY,
                "scope_level": ScopeLevel.STUDENT.value,
                "scope_id": student_id,
                "subject_kind": SubjectKind.USER.value,
                "subject_id": faculty_user_id,
                "reason": HANDOVER_REASON,
                "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
            },
            event_type="governance.grant.handover",
            payload={
                "student_id": student_id,
                "user_id": faculty_user_id,
                "days": HANDOVER_DAYS,
            },
        )
    log.info(
        "handover read granted: %s keeps student %s for %s days",
        faculty.email, student_id, HANDOVER_DAYS,
    )
    return grant


def holds_handover_for(db: Session, user_id: str, student_id: str) -> bool:
    """Does this account hold a LIVE grant pinned to exactly THIS student?

    DELIBERATELY NARROWER THAN `governance.reaches_target`, and the difference is
    the difference between a handover and a hole. `reaches_target` answers "does
    any of this holder's grants cover this student", which a PROGRAMME-WIDE
    `mentor.mentees` grant does — and a programme-wide grant has never let a
    mentor past rule 2, because AGENTS.md's two fences are checked separately on
    purpose: "a capability can never relax the student filter". Reading the
    window with `reaches_target` would turn every existing `mentor.mentees`
    grant into universal access to every student's records, silently, on deploy.

    So: the scope pair must be (STUDENT, this student) exactly. That pair can
    only have been written by a handover or by an administrator naming one
    student on purpose, and both of those are decisions somebody made.

    Liveness is asked of `_live_grant_clauses` — the one definition of what makes
    a grant count — so revocation, expiry, the approval state and B2.5's role
    check all apply here without being restated.
    """
    if not user_id:
        return False
    # Imported inside the function: `governance` imports nothing from here, and
    # keeping it that way is what stops the cycle `policies` already avoids.
    from .governance import _live_grant_clauses, current_role_of

    now = _now()
    return db.scalar(
        select(CapabilityGrant.id).where(
            CapabilityGrant.capability == HANDOVER_CAPABILITY,
            CapabilityGrant.subject_kind == SubjectKind.USER,
            CapabilityGrant.subject_user_id == user_id,
            CapabilityGrant.scope_level == ScopeLevel.STUDENT,
            CapabilityGrant.scope_id == student_id,
            *_live_grant_clauses(now, current_role_of(db, user_id)),
        )
    ) is not None


def release_mentees_of(
    db: Session,
    *,
    faculty_user_id: str,
    by_user_id: str | None,
    reason: str | None,
    session: dict | None = None,
    request: Any | None = None,
) -> list[str]:
    """Unseat every student this faculty account mentors. Returns their ids.

    B9.1's `faculty_disabled` half. `routers/admin_faculty.py`'s `disable_account`
    deferred this in its own docstring; this is the function it deferred it to,
    and it lives here rather than there because `mentor_assignments` has one
    writer and this is it.
    """
    from .models.user import Student

    group_ids = list(db.scalars(select(Mentor.id).where(Mentor.user_id == faculty_user_id)).all())
    if not group_ids:
        return []
    released: list[str] = []
    for student in db.scalars(select(Student).where(Student.mentor_id.in_(group_ids))).all():
        previous = student.mentor_id
        student.mentor_id = None
        record_mentor_change(
            db,
            student_id=student.id,
            previous_mentor_id=previous,
            new_mentor_id=None,
            by_user_id=by_user_id,
            reason=reason,
            end_kind=END_FACULTY_DISABLED,
            session=session,
            request=request,
        )
        released.append(student.id)
    return released
