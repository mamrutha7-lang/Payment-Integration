"""Resolving governance: what a staff member may use, and what a student has.

Two questions, two functions, and they are deliberately not the same shape:

    has_capability(db, session, key)      -> bool   staff, deny past the baseline
    feature_enabled(db, student_id, key)  -> bool   students, allow until switched off

and each has the refusal that goes with it, which is what the routers call:

    require_capability(db, session, key)  -> 403 "an administrator can grant it"
    require_feature(db, student_id, key)  -> 403 in the office's OWN WORDS

The asymmetry in those two sentences is the asymmetry in the model. A missing
capability is a staff member asking for something they were never given, and the
fix is a grant. A switched-off feature is something a student HAD and the office
took away for a stated reason, and the fix is a conversation — so the refusal
carries `student_message` from the winning override rather than boilerplate.

RULE 2 IS NOT IN THIS FILE, ON PURPOSE. `_assert_can_access_student` decides
WHICH STUDENTS a staff member may reach and lives in routers/mentor.py; this
decides WHICH SCREENS. Both must pass and they are checked separately, so a
capability can never relax the student filter — that is the safety argument, and
importing the two into one function would be the first step in losing it.

Nothing here caches. A grant revoked at 11:04 must stop working at 11:04, and a
per-process cache is how a revoked capability keeps working on three of five
workers until the next deploy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models.cohort import Cohort
from .models.governance import (
    APPROVAL_ACTIVE,
    CAPABILITIES,
    CAPABILITIES_BY_KEY,
    FEATURES_BY_KEY,
    SPECIFICITY,
    AccessGroupMember,
    CapabilityGrant,
    CapabilityScope,
    FeatureOverride,
    ScopeLevel,
    SubjectKind,
)
from .models.institution import AcademicCourse, Department
from .mentor_functions import mentor_functions_for
from .models.user import Student, User

_ALL: Final[frozenset[str]] = frozenset(c.key for c in CAPABILITIES)
_SCOPED: Final[frozenset[str]] = frozenset(
    c.key for c in CAPABILITIES if c.scope is CapabilityScope.SCOPED
)

#: What each role holds with no grant at all — exactly the screens it reaches
#: today. Introducing capability grants must not take anything away from anyone:
#: a deny-by-default rollout would have removed every mentor's own mentee log on
#: the deploy that shipped it. A grant is how someone gets what their role does
#: not already carry, which for a MENTOR is the programme-wide set.
#: DIRECTOR HOLDS NOTHING (2026-09-10), and this line is the one that decides it.
#:
#: The role removal touched the role gates first — `require_mentor`,
#: `require_admin`, `policies.STAFF_ROLES` — and for a few hours THIS map still
#: read `"DIRECTOR": _ALL - {"admin.interview_audio"}`. The result was worse than
#: leaving it alone: a DIRECTOR session was refused by every `require_*` gate and
#: still passed every `require_capability` one, which is roughly fifty endpoints
#: and the whole console — `admin.py`, `console.py`, the roster's
#: DELETE /api/admin/students/{id}, the exports CSV. Half a removal is a role
#: that cannot read its own mentee log but can delete the programme.
#:
#: An empty baseline is what makes "DIRECTOR grants nothing" true rather than
#: intended. The migration converts the rows; this closes the door for any row
#: that predates it, on a checkout where it has not run, or minted from an older
#: image. `tests/test_no_director_privilege.py` asserts both halves together.
#:
#: `admin.interview_audio` is still the one capability ADMIN alone holds nothing
#: extra to be said about — the asymmetry it recorded (a recording is an
#: operator's artefact containing a named student's voice, not placement
#: business) now lives entirely in MENTOR needing an explicit grant for it.
#: Faculty instruments. The Main Admin is not a faculty member: it has no
#: mentees, no private notebook, nobody's evidence to verify and no upskilling
#: shelf. These stay in the catalogue and stay SCOPED, so the Main Admin can
#: GRANT any of them in Governance -- to a faculty member, or to itself when a
#: student's evidence is stuck and nobody else will look -- and revoke them
#: again. Leave approval is `admin.leave_approvals` (2026-09-17): a PROGRAMME
#: key the Main Admin holds by baseline through `_ALL` and can grant to any
#: faculty member in Governance -- never derived from mentoring, which is what
#: the retired `mentor.leave_approve` was.
_FACULTY_ONLY: Final[frozenset[str]] = frozenset(
    {"mentor.mentees", "mentor.notebook", "mentor.verifications", "mentor.upskilling"}
)

ROLE_BASELINE: Final[dict[str, frozenset[str]]] = {
    "ADMIN": _ALL - _FACULTY_ONLY,
    "DIRECTOR": frozenset(),
    # B2.3: A FACULTY ACCOUNT IS NOT A MENTOR BY EXISTING, and this line is
    # where that stopped being only a sentence in AGENTS.md. It was `_SCOPED` —
    # every SCOPED key in the catalogue — so "is this person staff" and "may this
    # person read a mentee's ledger" were one question with one answer.
    #
    # What is left is what belongs to the PERSON: the assistant, and their own
    # certificate shelf. The two that belong to a GROUP — the mentee log and
    # the notebook — arrive as derived functions when they are assigned their
    # first student and go when their last one is released
    # (app/mentor_functions.py). Leave approval left that list on 2026-09-16
    # and evidence verification on 2026-10-16: both are now decisions the
    # office makes about a named person, as grants (app/verifiers.py).
    #
    # The ten `student.*` keys left with them. They gate nothing anywhere —
    # B2.1 deletes them from the catalogue — and no client reads one, so this
    # removes no access from anybody.
    "MENTOR": frozenset({"mentor.agent", "mentor.upskilling"}),
    "STUDENT": frozenset(),
    "ALUMNI": frozenset(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _live_grant_clauses(now: datetime, role: str | None = None) -> tuple:
    """What makes a grant count, in SQL. ONE definition, used by every reader.

    Four conditions, and two of them were missing at one time or another. Not
    revoked and not expired were always filtered here — in SQL rather than in
    Python, so a long-expired grant never reaches the process at all.
    `approval_state` was added for B2.4's two-person rule, written on every row,
    carried a check constraint and a docstring about four-eyes approval — and
    was read by NO query, so a grant awaiting a second signature was fully live
    the moment it was inserted. (Only a DEPUTY's `carries_pii` grant waits now;
    the Main Admin's are written live — `routers/governance.py::
    initial_approval_state`. The reader does not care who wrote the row.)

    `role_at_grant` is B2.5's, and it is the same shape of hole. A grant is a
    decision about a person IN A ROLE — "this MENTOR may read the registrations
    queue". Change the account's role and the sentence stops describing anybody,
    so the grant must stop counting; a grant that silently survives a role change
    is how a demoted account keeps a console screen with a live audit row saying
    it was given one.

    NULL `role_at_grant` IS HONOURED, NOT REFUSED. It means "granted before this
    column existed": the backfill cannot know which role was held at the time,
    and guessing would revoke real access on the deploy that shipped the guess.
    It is also what every GROUP grant carries, correctly — a grant to "the
    placement coordinators" is a decision about the group, and the role that
    matters there is the one each member holds when they join, which
    `AccessGroupMember` and the staff-only membership check already decide.

    `role` is optional so a caller that has not resolved one does not silently
    get an unfiltered read: every caller in this module passes it, and the
    parameter exists for the reads that genuinely have no subject (there are
    none today, and the next one should think about it rather than inherit a
    default).

    A shared tuple rather than the same conditions written four times, because
    `granted_capabilities` and `granted_reaches` ARE one question asked two
    ways, and a condition living in two places is exactly how the approval one
    came to be missing from both.
    """
    clauses = (
        CapabilityGrant.revoked_at.is_(None),
        or_(CapabilityGrant.expires_at.is_(None), CapabilityGrant.expires_at > now),
        CapabilityGrant.approval_state == APPROVAL_ACTIVE,
    )
    if role is None:
        return clauses
    return (
        *clauses,
        or_(
            CapabilityGrant.role_at_grant.is_(None),
            CapabilityGrant.role_at_grant == role,
        ),
    )


def current_role_of(db: Session, user_id: str) -> str | None:
    """The role on the ROW, not the one in the session cookie.

    A session is a signed snapshot minted at sign-in. A role change bumps
    `token_version` and retires it, but the question "does this grant still
    describe this person" is asked on every request including the ones made with
    a cookie that has not been rejected yet — and answering it from the claim
    would let the stale claim vouch for the stale grant.
    """
    if not user_id:
        return None
    role = db.scalar(select(User.role).where(User.id == user_id))
    return getattr(role, "value", role) if role is not None else None


def granted_capabilities(db: Session, user_id: str) -> frozenset[str]:
    """Capabilities this user holds by grant — directly, or through a group.

    Live means: not revoked, not expired, approved, and still describing the role
    this account holds today — `_live_grant_clauses` is the one place that says
    so, and all four are filtered in SQL rather than in Python so a long-expired
    grant never reaches the process at all.
    """
    if not user_id:
        return frozenset()
    now = _now()
    live = _live_grant_clauses(now, current_role_of(db, user_id))
    direct = select(CapabilityGrant.capability).where(
        CapabilityGrant.subject_kind == SubjectKind.USER,
        CapabilityGrant.subject_user_id == user_id,
        *live,
    )
    # Through a group: the grant names the group, the membership names the user.
    # A person who joins the group tomorrow inherits it with no second grant,
    # which is the reason groups exist.
    via_group = (
        select(CapabilityGrant.capability)
        .join(AccessGroupMember, AccessGroupMember.group_id == CapabilityGrant.subject_group_id)
        .where(
            CapabilityGrant.subject_kind == SubjectKind.GROUP,
            AccessGroupMember.user_id == user_id,
            *live,
        )
    )
    keys = set(db.scalars(direct).all()) | set(db.scalars(via_group).all())
    # A grant naming a capability the catalogue no longer defines is ignored
    # rather than trusted: the catalogue is code, so the key was removed in a
    # deploy and nothing enforces it any more.
    return frozenset(k for k in keys if k in CAPABILITIES_BY_KEY)


def granted_reaches(db: Session, user_id: str, key: str) -> list[tuple[ScopeLevel | None, str | None]]:
    """How far this user's live grants for `key` reach, one entry per grant.

    `(None, None)` is a programme-wide grant and means everywhere. Anything else
    is a rung of the spine and the id it hangs on, and the holder reaches a
    target only when that pair is in the target's ancestry.

    Separate from `granted_capabilities` rather than folded into it because the
    two answer different questions and only one of them is asked on every
    request: "which keys does this session hold" drives the sidebar and runs on
    /auth/me, while "how far does this key reach" is asked by the handful of
    endpoints that name a target. Returning the reaches from the common path
    would make every /auth/me carry six columns it does not read.
    """
    if not user_id:
        return []
    now = _now()
    live = (
        CapabilityGrant.capability == key,
        *_live_grant_clauses(now, current_role_of(db, user_id)),
    )
    columns = (CapabilityGrant.scope_level, CapabilityGrant.scope_id)
    direct = select(*columns).where(
        CapabilityGrant.subject_kind == SubjectKind.USER,
        CapabilityGrant.subject_user_id == user_id,
        *live,
    )
    via_group = (
        select(*columns)
        .join(AccessGroupMember, AccessGroupMember.group_id == CapabilityGrant.subject_group_id)
        .where(
            CapabilityGrant.subject_kind == SubjectKind.GROUP,
            AccessGroupMember.user_id == user_id,
            *live,
        )
    )
    return [tuple(row) for row in db.execute(direct).all()] + [
        tuple(row) for row in db.execute(via_group).all()
    ]


def reaches_target(
    reaches: list[tuple[ScopeLevel | None, str | None]],
    ancestry: list[tuple[ScopeLevel, str]],
) -> bool:
    """Does any of these grants cover a target with this ancestry?

    A programme-wide grant covers everything. A scoped grant covers the target
    when its (rung, id) is one of the target's own — which is why the ancestry
    is computed as the full list rather than the deepest rung: a grant on the
    college and a grant on the batch are both satisfied by the same student.

    AN EMPTY ANCESTRY IS NOT COVERED BY A SCOPED GRANT, and that is deliberate.
    A student seated in no batch and filed under no department, or an unfiled
    faculty account, hangs under nothing — so a department-scoped holder cannot
    reach them. Answering "yes" there would make the unfiled state a way around
    every scope in the system, and unfiled is an ordinary state that the console
    shows a list of.
    """
    covered = set(ancestry)
    return any(level is None or (level, target_id) in covered for level, target_id in reaches)


def capabilities_for(db: Session, session: dict) -> frozenset[str]:
    """Everything this session may use: baseline, functions, and grants.

    Three sources, unioned, and each answers a different question. The BASELINE
    is what the role carries — the Main Admin's programme keys, a faculty
    member's own assistant and shelf. The FUNCTIONS are what mentoring somebody
    brings, derived live from the mentee count rather than stored, so no path
    that assigns a student can forget to write them (app/mentor_functions.py
    explains why that is not the design 04 asks for). The GRANTS are what a
    person decided to hand over, in Governance, with a reason.
    """
    role = str(session.get("role") or "")
    user_id = str(session.get("userId") or "")
    baseline = ROLE_BASELINE.get(role, frozenset())
    functions = mentor_functions_for(db, user_id) if role == "MENTOR" else frozenset()
    return baseline | functions | granted_capabilities(db, user_id)


def has_capability(db: Session, session: dict, key: str) -> bool:
    if key not in CAPABILITIES_BY_KEY:
        raise ValueError(f"unknown capability {key!r}")
    return key in capabilities_for(db, session)


def require_capability(
    db: Session,
    session: dict,
    key: str,
    *,
    target: list[tuple[ScopeLevel, str]] | None = None,
) -> None:
    """403 when the session lacks the capability, or holds it somewhere else.

    403 and not 404, unlike rule 2's refusals: the caller is a known staff member
    and the resource is not a student they might be probing for. "You do not hold
    this" is a true and safe thing to tell them, and a 404 here would send an
    admin hunting for a broken route instead of granting a capability.

    `target` IS OPT-IN AND EVERY EXISTING CALL SITE KEEPS ITS MEANING. Eighty
    calls in this repository pass three positional arguments and ask "may you do
    this at all"; they still get exactly that answer. A call that passes a
    target — the ancestry of the student, faculty member or batch it is about —
    additionally asks "may you do it HERE", and that is the only question scope
    narrows. Adding the parameter without a default would have been a change to
    all eighty at once, decided by whoever was quickest to update the signature.

    A capability held through the ROLE BASELINE is unscoped. That is the Main
    Admin, whose baseline is every programme key and whose whole job is the
    programme; a MENTOR's baseline keys are narrowed by rule 2's mentor-group
    check instead, which is a different and stricter gate that this must not
    replace. Scope is a property of a GRANT, because a grant is the thing
    somebody decided to hand over, and handing it over is where "how far" gets
    asked.
    """
    if key not in CAPABILITIES_BY_KEY:
        raise ValueError(f"unknown capability {key!r}")

    role = str(session.get("role") or "")
    user_id = str(session.get("userId") or "")
    if key in ROLE_BASELINE.get(role, frozenset()):
        return
    # A function is unscoped for the same reason a baseline key is: rule 2's
    # mentor-group check is already the fence on all four, and it is stricter
    # than any scope could be — it narrows to THIS mentor's own students rather
    # than to a department's.
    if role == "MENTOR" and key in mentor_functions_for(db, user_id):
        return

    reaches = granted_reaches(db, user_id, key)
    if not reaches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"You do not hold the '{CAPABILITIES_BY_KEY[key].label}' capability. "
                "An administrator can grant it in Governance."
            ),
        )
    if target is not None and not reaches_target(reaches, target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Your '{CAPABILITIES_BY_KEY[key].label}' capability does not reach this "
                "record. An administrator can widen it in Governance."
            ),
        )


# --------------------------------------------------------------------------- #
# Student features
# --------------------------------------------------------------------------- #

def ancestry_of_student(db: Session, student_id: str) -> list[tuple[ScopeLevel, str]]:
    """Every (rung, id) pair this student hangs under.

    One flat read, and nothing is stored: the same shape as
    `routers/student.py::_institution_for`, and for the same reason — copying a
    student's ancestry onto their row is the backfill the spine exists to avoid.

    A cohort carries all three ancestor pointers rather than only the deepest,
    because the levels are individually optional; missing rungs simply produce no
    pair, and anything hung at a level this student has no ancestor for cannot
    match them.

    TWO POINTERS REACH A DEPARTMENT, AND THIS READS BOTH. `cohorts.department_id`
    is one; `students.department_id` (migration 31f7a4c60b12) is the other, and
    it is the ONLY one for a student who named a department on the registration
    form and has not been seated in a batch — which is every student at a college
    that has not built its batches yet. `_institution_for` has tried both since
    that column existed, and this function did not: it read the cohort route
    alone, so an override hung on a department reached the seated students in it
    and silently missed the unseated ones.

    That was a live bug in feature overrides before it was a hole in B1.2's
    scope check, and fixing it HERE fixes both, which is why this is the one
    ancestry function rather than a second one written for grants. Switching a
    feature off for a department now also switches it off for that department's
    unseated students — which is what "for this department" has always meant on
    the screen that sets it.

    The college is reached through the DEPARTMENT only. `cohorts.course_id` and
    `specialization_id` are flat sibling pointers, not a chain upward: a course
    does not carry a college, so walking through one would resolve nothing (see
    `routers/student.py`'s note on why chaining them is wrong).
    """
    row = db.execute(
        select(
            Student.id,
            Student.cohort_id,
            Student.department_id.label("own_department_id"),
            Cohort.department_id.label("batch_department_id"),
            Cohort.course_id,
            Cohort.specialization_id,
        )
        .select_from(Student)
        .outerjoin(Cohort, Student.cohort_id == Cohort.id)
        .where(Student.id == student_id)
    ).first()
    if row is None:
        return []

    # The batch's department wins where there is one, because it is the more
    # specific statement about where this student sits; the student's own
    # pointer stands alone for an unseated student, and stands in for a batch
    # nobody filed. Same precedence as `_institution_for`.
    department_id = row.batch_department_id or row.own_department_id
    college_id = (
        db.scalar(select(Department.college_id).where(Department.id == department_id))
        if department_id
        else None
    )
    pairs = [
        (ScopeLevel.STUDENT, row.id),
        (ScopeLevel.COHORT, row.cohort_id),
        (ScopeLevel.SPECIALIZATION, row.specialization_id),
        (ScopeLevel.COURSE, row.course_id),
        (ScopeLevel.DEPARTMENT, department_id),
        (ScopeLevel.COLLEGE, college_id),
    ]
    return [(scope, tid) for scope, tid in pairs if tid]


def ancestry_of_cohort(db: Session, cohort_id: str) -> list[tuple[ScopeLevel, str]]:
    """Every (rung, id) pair a BATCH hangs under — B8.1's target.

    An import names a batch, not a student, so the fence on
    `POST /admin/imports/preview` has to be able to ask "does this holder's
    grant reach this batch" before a single line of the file is read. The
    student ancestry cannot answer it: a batch with nobody seated in it yet is
    exactly the batch a new cohort's first results file is imported into, and
    walking its students would find none and refuse the office.

    THE THREE ANCESTOR POINTERS ARE READ OFF THE COHORT ROW, never re-derived.
    `cohorts.department_id`, `course_id` and `specialization_id` have exactly
    one writer — `_resolve_ancestry` in routers/admin.py — which is what makes
    reading them here reading a value that walk already checked. The college is
    reached through the DEPARTMENT only, for `ancestry_of_student`'s reason: a
    course carries no college, so chaining through one resolves nothing.

    A batch filed under nothing hangs under nothing and no scoped grant reaches
    it, which is `reaches_target`'s rule and not a special case here.
    """
    row = db.execute(
        select(
            Cohort.id,
            Cohort.department_id,
            Cohort.course_id,
            Cohort.specialization_id,
        ).where(Cohort.id == cohort_id)
    ).first()
    if row is None:
        return []
    college_id = (
        db.scalar(select(Department.college_id).where(Department.id == row.department_id))
        if row.department_id
        else None
    )
    pairs = [
        (ScopeLevel.COHORT, row.id),
        (ScopeLevel.SPECIALIZATION, row.specialization_id),
        (ScopeLevel.COURSE, row.course_id),
        (ScopeLevel.DEPARTMENT, row.department_id),
        (ScopeLevel.COLLEGE, college_id),
    ]
    return [(scope, tid) for scope, tid in pairs if tid]


def ancestry_of_user(db: Session, user_id: str) -> list[tuple[ScopeLevel, str]]:
    """Where a STAFF account sits: its department, and that department's college.

    Shorter than a student's on purpose — a faculty member is filed under a
    department and nothing else. An unfiled account (`users.department_id IS
    NULL`, a first-class state on the Faculty screen) hangs under nothing, so a
    scoped grant cannot reach it and a scoped holder cannot reach them.
    """
    department_id = db.scalar(select(User.department_id).where(User.id == user_id))
    if not department_id:
        return []
    college_id = db.scalar(select(Department.college_id).where(Department.id == department_id))
    pairs = [(ScopeLevel.DEPARTMENT, department_id), (ScopeLevel.COLLEGE, college_id)]
    return [(scope, tid) for scope, tid in pairs if tid]


def ancestry_of_interview_track(
    db: Session,
    *,
    college_id: str | None,
    course_id: str | None,
    specialization_id: str | None,
) -> list[tuple[ScopeLevel, str]]:
    """Where an INTERVIEW TRACK hangs — B5.1's target.

    Takes the three pointers rather than a row id, because the write path checks
    the reach of the values being SUBMITTED, before anything is stored: an
    endpoint that could only ask "may you edit the row as it is today" would let
    a college-scoped holder move a track into another college and then be unable
    to move it back.

    THE COURSE REACHES A DEPARTMENT AND THROUGH IT A COLLEGE, which is the one
    place this differs from `ancestry_of_cohort` directly above. A cohort names
    its department itself, so that function walks department → college and says
    a course carries no college. A track does not name a department at all, and
    `academic_courses.department_id` is NOT NULL — so the walk course →
    department → college always lands, and it is the same walk
    `scope_views.job_scope_clause` already makes for a posting. Without it a
    department-scoped holder could hold a key over a course they own and be
    refused the track hung on it.

    AN EMPTY LIST IS PROGRAMME-WIDE AND IS REACHED BY NO SCOPED GRANT. That is
    `reaches_target`'s rule, not a special case, and here it is the rule doing
    exactly what it should: the four seeded tracks are programme-wide on any
    multi-college deployment, editing one changes the interview for every
    college, and a college admin must not be able to do that from their own
    screen. They can add their college's own row instead, which shadows it.
    """
    if course_id:
        department_id = db.scalar(
            select(AcademicCourse.department_id).where(AcademicCourse.id == course_id)
        )
    else:
        department_id = None
    if not college_id and department_id:
        college_id = db.scalar(
            select(Department.college_id).where(Department.id == department_id)
        )
    pairs = [
        (ScopeLevel.SPECIALIZATION, specialization_id),
        (ScopeLevel.COURSE, course_id),
        (ScopeLevel.DEPARTMENT, department_id),
        (ScopeLevel.COLLEGE, college_id),
    ]
    return [(scope, tid) for scope, tid in pairs if tid]


#: Kept so the feature-override code below reads as it did. The rename is the
#: point: this answers "where does this student hang", which is a question about
#: the spine and not about features.
_ancestry = ancestry_of_student


#: The header a feature refusal carries, so a client can tell "switched off for
#: you" from every other 403 without parsing English out of `detail`. Chosen
#: over a machine code inside `detail` because `detail` is what the screens
#: already print (`detailOf` in the Angular client), and B2.2's whole point is
#: that the STUDENT reads the override's own sentence there. Same idiom as
#: `X-Reep-Session: retired` in app/security.py, for the same reason: the
#: human-readable body stays human-readable and the machine reads a header.
FEATURE_DISABLED_HEADER: Final[str] = "X-Reep-Feature-Disabled"

#: What a student is told when the office switched something off and wrote no
#: `student_message`. Deliberately says nothing about WHY: `reason` is the
#: office's note to itself ("withheld pending the disciplinary meeting" is a
#: true reason and not a sentence to put on a student's screen), and only
#: `student_message` was written to be read by them.
FEATURE_DISABLED_DEFAULT_MESSAGE: Final[str] = (
    "This part of REEP is switched off for your account. "
    "Your placement cell can tell you more."
)


@dataclass(frozen=True, slots=True)
class FeatureState:
    """One feature, resolved for one student: is it on, and what are they told.

    The message travels WITH the boolean rather than being fetched separately,
    because the two come from the same winning row and a second read could pick
    a different winner — an override edited between the two calls, or a
    student-level rule that expired in the gap. One row, one answer.
    """

    key: str
    enabled: bool
    #: The override's `student_message`, and ONLY when the feature is off. A
    #: message on a feature somebody can use is a sentence with nowhere to go,
    #: and carrying it would let a client render "switched off" next to a
    #: working screen.
    message: str | None


def _resolve_features(
    db: Session, student_id: str, only: str | None = None
) -> dict[str, FeatureState]:
    """The ONE feature resolution. Allow by default; most specific rung wins.

    Every public function below is a view of this: `feature_enabled` reads the
    boolean, `require_feature` raises on it, `features_for` and
    `feature_states_for` ask for all of them at once. Written once because the
    rules are subtle in three places at least — the expiry filter, the
    specificity tie-break, and "a message only counts when the answer is off" —
    and a second copy would drift on whichever of the three the copier did not
    notice.

    `only` narrows the read to one key. It is not an optimisation of the query
    (the ancestry read dominates either way); it is so a single-feature caller
    cannot accidentally depend on the other ten being resolved correctly.
    """
    keys = tuple(FEATURES_BY_KEY) if only is None else (only,)
    pairs = _ancestry(db, student_id)
    if not pairs:
        # A student who hangs under nothing is reached by no rule, so every
        # feature is on. Same reasoning as `reaches_target`'s empty ancestry:
        # unfiled is an ordinary state, and it must not be a way around the
        # system in either direction.
        return {key: FeatureState(key, True, None) for key in keys}
    now = _now()
    where = [
        or_(FeatureOverride.expires_at.is_(None), FeatureOverride.expires_at > now),
        or_(
            *[
                (FeatureOverride.scope == scope) & (FeatureOverride.target_id == tid)
                for scope, tid in pairs
            ]
        ),
    ]
    if only is not None:
        where.append(FeatureOverride.feature == only)
    rows = db.scalars(select(FeatureOverride).where(*where)).all()
    best: dict[str, FeatureOverride] = {}
    for r in rows:
        # A row naming a feature the catalogue no longer defines is ignored, for
        # the reason `granted_capabilities` ignores a dropped capability: the
        # key left in a deploy and nothing enforces it any more, so honouring it
        # would switch off a screen no console can switch back on.
        if r.feature not in FEATURES_BY_KEY:
            continue
        current = best.get(r.feature)
        if current is None or SPECIFICITY[r.scope] > SPECIFICITY[current.scope]:
            best[r.feature] = r
    out: dict[str, FeatureState] = {}
    for key in keys:
        row = best.get(key)
        if row is None or row.enabled:
            out[key] = FeatureState(key, True, None)
        else:
            out[key] = FeatureState(key, False, (row.student_message or "").strip() or None)
    return out


def feature_state(db: Session, student_id: str, feature: str) -> FeatureState:
    """Is `feature` on for this student, and what do we tell them if not."""
    if feature not in FEATURES_BY_KEY:
        raise ValueError(f"unknown feature {feature!r}")
    return _resolve_features(db, student_id, feature)[feature]


def feature_enabled(db: Session, student_id: str, feature: str) -> bool:
    """Is `feature` on for this student? Allow by default; most specific wins.

    An admin switches the voice interviewer off for a specialization and back on
    for one student inside it: two rows, and the student-level one wins because
    it is more specific. That is why `FeatureOverride.enabled` is a boolean
    rather than the row's mere existence meaning "off" — deleting the broader
    rule to make an exception would turn the feature on for everyone else too.
    """
    return feature_state(db, student_id, feature).enabled


def require_feature(db: Session, student_id: str | None, feature: str) -> None:
    """403 with the office's own words when this student's switch is off.

    WHICH ENDPOINTS CALL THIS, and the rule that decided it (B2.2). The gate
    goes on the SCREEN — its primary read — and on every action that USES the
    feature. It does NOT go on retrieving or removing an artefact the student
    already produced: `GET /student/uploads/{id}/file`,
    `DELETE /student/uploads/{id}`, `GET /student/resume` and its PDF, and
    `PUT /student/leaderboard-visibility` all keep working with the feature off.

    Switching a feature off is the office saying "not from here, for now". It is
    not the office confiscating a certificate a student uploaded in March, or
    taking away their ability to opt out of a leaderboard — and a student who
    cannot reach the screen cannot be asked to turn that setting off on it
    first.

        student.jobs          GET /student/jobs, POST /student/jobs/{id}/apply
        student.leaderboards  GET /student/leaderboards,
                              GET /student/badges/leaderboards
        student.assistant     GET /api/interview/status, WS /api/interview
        student.resume        POST /student/resume/generate
        student.agent         POST /api/agent/{chat,chat/stream,ask}
        student.uploads       GET + POST /student/uploads
        student.english       GET /student/english-baseline, POST .../start
        student.skilling      GET /student/badges, GET /student/growth,
                              POST /student/badges/{code}/{start,evidence}
        student.time_log      GET + PUT /student/ledger, GET /student/ledger/history,
                              POST /student/ledger/{copy-yesterday,submit}
        student.mentor_log    GET /student/mentor-meetings,
                              POST /student/mentor-meetings/request

    403 rather than 404. The student exists, the screen exists, and "this is
    switched off for you" is a true and safe thing to say to the person it is
    switched off for — it is their own account. The opposite choice would send
    them to support with "the app is broken", which is the outcome the
    `student_message` column was added to prevent.

    `student_id` may be None so a caller that has not resolved one (the agent
    endpoints, which serve staff too) can hand over what it has without an `if`
    of its own. No student id means no student, and a feature override is a
    statement about a student: there is nothing to refuse.
    """
    if not student_id:
        return
    state = feature_state(db, student_id, feature)
    if state.enabled:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=state.message or FEATURE_DISABLED_DEFAULT_MESSAGE,
        headers={FEATURE_DISABLED_HEADER: feature},
    )


def feature_states_for(db: Session, student_id: str) -> dict[str, FeatureState]:
    """Every feature's state for one student, message included.

    One call rather than eleven, because the student's shell asks for all of
    them at once on /auth/me and eleven round trips through `_ancestry` would
    read the same six ids eleven times.
    """
    return _resolve_features(db, student_id)


def features_for(db: Session, student_id: str) -> dict[str, bool]:
    """Every feature's state for one student as plain booleans.

    Kept alongside `feature_states_for` because "which of these may this student
    use" is a question with a yes/no answer and several callers want only that;
    it is a view of the same resolution, never a second one.
    """
    return {key: state.enabled for key, state in _resolve_features(db, student_id).items()}
