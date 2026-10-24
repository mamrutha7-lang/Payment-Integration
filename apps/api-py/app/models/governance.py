"""Governance — who may reach which screen, and which screens a student has.

TWO INSTRUMENTS, AND THEY HAVE OPPOSITE DEFAULTS. Collapsing them into one
setting is the bug this file exists to prevent, because the same control would
then sometimes grant and sometimes remove.

  * `CapabilityGrant` is DENY BY DEFAULT. Nobody holds a capability until an
    admin issues it, to a named user or to an `AccessGroup` whose members
    inherit it.
  * `FeatureOverride` is ALLOW BY DEFAULT. Every student has every feature until
    one is switched off, at any rung of the institutional hierarchy — a whole
    specialization, one batch, or a single student.

HOW A CAPABILITY COMPOSES WITH RULE 2 — the only question that matters here,
and the two answer DIFFERENT questions, which is what makes the composition safe:

    rule 2      decides WHICH STUDENTS a staff member may reach.
    capability  decides WHICH SCREENS AND ACTIONS they may use.

They are orthogonal, and both must pass. A grant may therefore widen the second —
that is what granting Analytics to a mentor does — but it can NEVER widen the
first. Holding `student.interviews` lets a mentor open scorecards for students
ALREADY IN THEIR OWN GROUP; it cannot reach a student outside it, and a MENTOR
WITH NO GROUP STILL SEES NOBODY, whatever they hold.

That is the whole safety argument: rule 2 exists because "no filter" is the
reading that turns an empty mentor group into the whole programme, and a
capability that could relax the student filter would hand that reading back
through a different door. `tests/test_governance.py` pins it.

CAPABILITIES ARE ADDITIVE OVER A ROLE BASELINE, not deny-everything. Every role
already holds the screens it has today (app/governance.py's ROLE_BASELINE), so
introducing this table takes nothing away from anyone; a grant is how a person
gets something their role does not already carry. A deny-by-default rollout would
have silently removed every mentor's own mentee log on deploy.

THE EXCEPTION, MARKED EVERYWHERE. Analytics, exports and the institution
hierarchy are cross-cohort by nature — there is no group to narrow them to. A
`PROGRAMME` capability therefore hands over the whole college, which is why it
is a separate scope on the catalogue rather than a footnote, why the console
paints it red, and why every grant on this table demands a reason.

THE CATALOGUE IS CODE, ONLY STATE IS ROWS — the same rule as the 48 badges
(models/badge.py) and the programme milestones (models/milestone.py). Adding a
capability is a code change on purpose: each one has to be enforced at a call
site, and a row that names a capability nothing checks is a promise the API does
not keep. What admins maintain in the database is who holds what.

`reason` is NOT NULL on both tables. An audit trail whose entries do not say why
is a list of dates.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# The catalogue (code, not rows)
# --------------------------------------------------------------------------- #

class CapabilityScope(str, enum.Enum):
    #: Checked after rule 2. Reaches only students already in the holder's group.
    SCOPED = "SCOPED"
    #: Cross-cohort by nature. Cannot be narrowed; hands over the whole college.
    PROGRAMME = "PROGRAMME"


@dataclass(frozen=True, slots=True)
class Capability:
    key: str
    label: str
    scope: CapabilityScope
    #: True when exercising it shows a student's own record — marks, transcripts,
    #: certificates, a leave reason, their voice. Rule 1's concern, not rule 2's,
    #: and the reason the console marks these separately from the scope.
    carries_pii: bool = False


_S = CapabilityScope.SCOPED
_P = CapabilityScope.PROGRAMME

#: One entry per screen a staff member can reach. Keys mirror the Angular routes
#: so a reviewer can check the two lists against each other by eye.
#:
#: THE TEN `student.*` KEYS WERE DELETED HERE (B2.1, 2026-09-13), and the reason
#: is the sentence three paragraphs above this one: a row that names a capability
#: nothing checks is a promise the API does not keep. `student.profile`,
#: `.records`, `.skilling`, `.uploads`, `.resume`, `.interviews`, `.english`,
#: `.time_log`, `.mentor_log` and `.jobs` were checked at ZERO call sites in
#: `app/` and read by nothing in `apps/web/src`. Granting one in Governance cost
#: the office a decision, a typed reason and an audit row, and changed nothing
#: anywhere -- which is worse than a missing feature, because it looks like one
#: that works. What actually decides whether a staff member may open a student's
#: ledger is rule 2 (`_assert_can_access_student`) plus the screen's own
#: capability, and what decides whether the STUDENT has a screen at all is a
#: FeatureOverride below -- which is where these ten names properly live (B2.2).
#:
#: Deleting a key does not break a grant that names it: `granted_capabilities`
#: already drops any key the catalogue no longer defines, so an existing row
#: goes inert rather than raising. It was already inert; now it says so.
#:
#: `tools/ci/check_capability_enforcement.py` is what stops the next one being
#: added: every key here must be checked somewhere under `app/`, or exempted in
#: that script with the reason written down.
CAPABILITIES: Final[tuple[Capability, ...]] = (
    # -- faculty tools -------------------------------------------------------
    Capability("mentor.mentees", "Mentee log", _S),
    Capability("mentor.notebook", "Mentor notebook", _S),
    Capability("mentor.verifications", "Verify skills & evidence", _S),
    # `mentor.leave_approve` ("Approve leave") WAS HERE until 2026-09-16 and
    # went when leave approval became the Main Admin's alone (routers/leave.py):
    # a key nothing checks is a promise the API does not keep, and migration
    # d8b1f4c2a7e9 revoked every live grant of it. Its successor is
    # `admin.leave_approvals` below, in the PROGRAMME section, and deliberately
    # not this name: a grant row that survived the revocation must not come
    # back to life under a rule it was never made under.
    Capability("mentor.upskilling", "Own upskilling shelf", _S),
    Capability("mentor.agent", "REEP Agent", _S),
    # -- programme-wide: no group narrows these ------------------------------
    Capability("admin.analytics", "Analytics", _P),
    Capability("admin.exports", "Exports", _P, carries_pii=True),
    Capability("admin.institution", "Institution hierarchy", _P),
    Capability("admin.registrations", "Registrations", _P, carries_pii=True),
    Capability("admin.catalogue", "Approved certifications", _P),
    Capability("admin.jobs", "Jobs sheet", _P),
    Capability("admin.placement", "Placement", _P),
    Capability("admin.mentors", "Mentors & students", _P, carries_pii=True),
    # Leave approval (2026-09-17): the Main Admin's by baseline, and GRANTABLE
    # to any faculty member. Leave became the office's one signature on
    # 2026-09-16 and `mentor.leave_approve` -- a SCOPED key derived from
    # mentoring somebody, which admitted a colleague to their own group's
    # queue -- left with the two-signature chain. The owner then asked for the
    # office to be able to hand the queue to a named faculty member, which is
    # a different thing from the key that went: PROGRAMME, because a leave
    # request hangs on no mentor group (staff apply for leave too), decided
    # by a grant in Governance with a reason, and never derived. `carries_pii`
    # because a leave reason is free text and routinely medical, so a DEPUTY's
    # grant of it waits for a second signature (B2.4); the Main Admin's is live
    # at once. `routers/leave.py::_require_leave_approver` is the call site.
    Capability("admin.leave_approvals", "Approve leave", _P, carries_pii=True),
    Capability("admin.interview_audio", "Interview audio", _P, carries_pii=True),
    # The admin-authored question bank the free-style interviewer weaves in
    # (app/interview_bank.py). PROGRAMME: a question is asked of every student on
    # the track, so no mentor group could narrow it.
    Capability("admin.interview_questions", "Interview questions", _P),
    # SWOC - the four lines on a student's landing (app/models/student_swoc.py).
    # PROGRAMME and personal: it names a student and characterises them, and
    # the office writes it for every student, so a grant is the decision to let
    # a faculty member do the same.
    Capability("admin.swoc", "SWOC notes", _P, carries_pii=True),
    # The Main Admin's CRUD over students, one at a time and a whole batch at a
    # time (app/routers/admin_students.py). PROGRAMME and personal: it creates,
    # edits and deletes roster rows - the access control itself.
    Capability("admin.students", "Students", _P, carries_pii=True),
    # ONE STUDENT'S WHOLE RECORD (2026-09-17): `GET /admin/students/{id}/360`,
    # the screen behind the View button on the roster and the "Full record"
    # link on a faculty member's Mentee Log. Its own key, split off
    # `admin.students`, because the roster key is the WRITE -- it edits and
    # moves roster rows -- and reading a candidate's complete details (contact
    # details, every semester's results, attendance, the documents they
    # uploaded and each one's verdict, badges, interviews, the trail) is a
    # decision the office should be able to hand to a faculty member without
    # also handing them the roster editor. PROGRAMME, narrowed by B1.2's scope
    # on the student's ancestry, and rule 2 still runs underneath: a MENTOR
    # granted it opens their OWN mentees' records and nobody else's.
    # `carries_pii` for the obvious reason -- it is the most complete view of a
    # student the console has -- so a DEPUTY's grant waits for a second
    # signature and the Main Admin's is live at once. The Main Admin holds it
    # by baseline through `_ALL`, which is what puts "candidate complete
    # details" in front of the office with no grant to make.
    Capability("admin.student_records", "View student records", _P, carries_pii=True),
    # B8.1's spreadsheet imports (app/routers/admin_imports.py). PROGRAMME,
    # because an import is FOR A BATCH and no mentor group narrows a batch - the
    # rung it hangs on is the college or the batch itself, which is a B1.2 scope
    # target and not a mentor function.
    #
    # `carries_pii` IS TRUE AND THE CONSEQUENCE IS DELIBERATE. A preview names
    # every student in the batch by USN with their marks or their attendance
    # beside it, on screen, before anything is written - it is one of the most
    # concentrated views of student records the console has. So under B2.4 a
    # DEPUTY's grant of this key lands `pending_approval` and HOLDS NOTHING
    # until a different `admin.governance` holder approves it; the Colleges
    # screen's admin column renders "N awaiting approval" rather than
    # pretending. The Main Admin's own grant is live at once
    # (`initial_approval_state`, 2026-09-16) - the office is the authority the
    # rule protects, not a party it applies to - so a one-admin deployment
    # never needs a deputy to make this work, and the flag stays.
    Capability("admin.imports", "Data imports", _P, carries_pii=True),
    # B6.1/B6.4/B6.7's interviews: the college's interview POLICY (what is kept,
    # for how long, how many attempts a day) and the records grid behind it.
    # PROGRAMME for the same reason `admin.interview_questions` is: a policy
    # governs every student on a course, and no mentor GROUP is a rung a policy
    # could hang on. It is narrowed by B1.2's scope instead — a college-scoped
    # holder writes their own college's policy and not another's.
    #
    # `carries_pii` IS TRUE, and the half that earns it is not the policy row —
    # it is everything that hangs off this key: the records grid names students
    # with their scores beside them, and the cap reset names one student and
    # gives them back attempts. So a DEPUTY's grant lands `pending_approval`
    # under B2.4 and holds NOTHING until a different `admin.governance` holder
    # approves it; the Main Admin's is live at once (`initial_approval_state`,
    # 2026-09-16), so a one-admin deployment never needs a deputy to make this
    # work, and the flag stays. (04-backend-changes.md B1.3 listed this key
    # in `COLLEGE_ADMIN_CAPABILITIES` for months while it did not exist —
    # `app/routers/admin.py` carried the note. It exists now, in the same commit
    # as its first `require_capability` call site and its place in that set.)
    Capability("admin.interviews", "Interviews", _P, carries_pii=True),
    # Governance itself: the grants screen, the access groups and the student
    # feature switches (app/routers/governance.py). B2.6.
    #
    # IT IS NOT FLAGGED `carries_pii`, AND THAT IS DELIBERATE. It reads no
    # student record itself -- it hands out screens -- which is what
    # `carries_pii` actually means on this dataclass. It was also, until
    # 2026-09-16, the bootstrap of the four-eyes rule: every `carries_pii`
    # grant then waited for a second holder of this key, the Main Admin's own
    # included, and REEP has exactly ONE Main Admin by rule, so a deputy had to
    # exist before any student record could change hands at all. That is no
    # longer how the rule reads. The Main Admin's grants are live at once
    # (`initial_approval_state` in the router): the office is the authority the
    # rule protects, not a party it applies to. What a deputy is for now is the
    # day the office is unreachable -- and a deputy's own `carries_pii` grants
    # DO wait, for the Main Admin or another deputy, which is where the second
    # pair of eyes belongs.
    #
    # The appointment is still a decision on the trail with a typed reason, made
    # by the one account that holds this by baseline.
    #
    # It reads no student record itself -- it hands out screens -- which is what
    # `carries_pii` actually means on this dataclass.
    Capability("admin.governance", "Governance", _P),
)

# `ui.console_v2` STOOD HERE UNTIL PHASE 5, AND ITS DELETION IS THE INVARIANT.
#
# It was the one entry that was not a screen: a preview switch the 2026-09
# console's new screens sat behind, in the Main Admin's baseline, so the owner
# could review them on the production deployment while the office kept the
# console it knew. That review is over; the new screens ARE the console, and a
# switch nobody can turn off is a screen's second gate that only ever refuses.
#
# It also cost the catalogue its one exemption. Every OTHER key here is checked
# by a `require_capability` / `has_capability` / `scope_filter` call site under
# `app/`, which `tools/ci/check_capability_enforcement.py` proves; this one
# could not be, because it selected a CLIENT rendering and there was no request
# to refuse. With it gone that checker's EXEMPT dict is EMPTY, and B2.1's rule
# -- enforce every catalogue key or delete it -- has no "or write your name on
# a list" third option any more. `tests/test_codebase_guards.py` pins it empty.
#
# Grants naming it may still exist on a deployment. That is safe and was
# designed for: `granted_capabilities` filters on CAPABILITIES_BY_KEY, so such a
# row resolves to nothing while every other key the same person holds resolves
# normally (tests/test_capability_enforcement.py). Do not write a migration to
# delete those rows -- the audit trail is why the office can answer "who was
# given what, and when".

CAPABILITIES_BY_KEY: Final[dict[str, Capability]] = {c.key: c for c in CAPABILITIES}


@dataclass(frozen=True, slots=True)
class Feature:
    key: str
    label: str
    #: Does a router actually ASK about this key? B2.2.
    #:
    #: It defaults to False, which is the opposite of convenient and is the
    #: whole point. Between 2026-08 and B2.2 every one of these ten was recorded,
    #: audited, displayed with a reason — and inert: `feature_enabled()` and
    #: `features_for()` were written, correct and tested, and NOTHING CALLED
    #: EITHER. The Governance screen said a feature was off and the student used
    #: it all afternoon. A switch wired to nothing is worse than a missing one,
    #: because somebody trusted it.
    #:
    #: So the flag is a claim the next person has to make ON PURPOSE, and
    #: `tests/test_feature_switches.py` makes them back it up: a key marked
    #: enforced with no call site in `app/` fails, and a key that IS gated but
    #: still says False fails too. Adding a Feature and forgetting the wiring is
    #: then a switch the console shows as "not wired yet" and REFUSES to set
    #: (422 on the override write) — honest, and unusable, rather than a lie the
    #: office can act on.
    enforced: bool = False


#: Student-facing features. These are SWITCHED OFF, never granted — every
#: student has them until an override says otherwise.
#:
#: `enforced=True` on every row here means every one of them is asked about at
#: a real call site; the map from key to the endpoints that ask is in
#: `app/governance.py::require_feature`'s docstring, next to the rule about
#: which endpoints are deliberately NOT gated.
FEATURES: Final[tuple[Feature, ...]] = (
    Feature("student.assistant", "Voice interviewer (Mock Interview)", enforced=True),
    Feature("student.agent", "REEP Agent (chat)", enforced=True),
    Feature("student.resume", "Resume Builder", enforced=True),
    Feature("student.english", "English baseline test", enforced=True),
    Feature("student.jobs", "Jobs feed & applications", enforced=True),
    Feature("student.leaderboards", "Leaderboards", enforced=True),
    Feature("student.uploads", "Document uploads", enforced=True),
    Feature("student.time_log", "Time allocation ledger", enforced=True),
    Feature("student.skilling", "Skilling & badges", enforced=True),
    # ADDED BY B2.2, and the spec is why it was missing. 04-backend-changes.md
    # names the ten features to gate as "jobs, leaderboards, mock interview,
    # resume generate, agent, uploads, english, skilling, time-log, mentor-log"
    # — but the catalogue it was describing had no `student.mentor_log`. The
    # screen is real and a student reaches it, so it is a switch: dropping it
    # would have left the spec's own list one short.
    #
    # `student.certifications` sat here until 2026-09-17, when the Certification
    # Tracker (`GET /student/certifications`) was removed at the owner's
    # request. A row that gates nothing is the state B2.2 exists to end, so the
    # row went with the endpoint; a stored override naming the old key is
    # skipped by the resolver and listed under its bare key by the console.
    Feature("student.mentor_log", "Mentor meeting log", enforced=True),
)

FEATURES_BY_KEY: Final[dict[str, Feature]] = {f.key: f for f in FEATURES}


class ScopeLevel(str, enum.Enum):
    """A rung of the institutional hierarchy that something can hang on.

    NOT `institution.HierarchyLevel`, which is a different thing with a
    confusingly similar name: that one says which levels a NEW BATCH must name.
    This one says WHERE something was hung. Ordered most general to most
    specific, and `SPECIFICITY` below depends on that order.

    WAS `FeatureScope`, because a feature override was the only thing that hung
    on a rung. B1.2 hangs capability grants on the same rungs — a grant scoped to
    a department reaches that department's students and no others — and two
    enums with identical members, one called Feature- and one called Grant-,
    would be the same mistake twice. The docstring already described this as "a
    rung of the institutional hierarchy" before anything but features used it.

    There is no PROGRAMME member and there must not be one. A grant that reaches
    everything hangs on NO rung, which is `scope_level IS NULL` on the row, not a
    seventh value here; a feature override always hangs on one. Adding PROGRAMME
    would make it representable for features, where it means nothing.
    """

    COLLEGE = "COLLEGE"
    DEPARTMENT = "DEPARTMENT"
    COURSE = "COURSE"
    SPECIALIZATION = "SPECIALIZATION"
    COHORT = "COHORT"
    STUDENT = "STUDENT"


#: Higher wins. A student-level override beats their cohort, which beats its
#: specialization, and so on up. Resolution reads every override that covers a
#: student and keeps the most specific — which is why an admin can switch a
#: feature off for a whole specialization and still turn it back on for one
#: student inside it, without deleting the broader rule.
SPECIFICITY: Final[dict[ScopeLevel, int]] = {
    ScopeLevel.COLLEGE: 0,
    ScopeLevel.DEPARTMENT: 1,
    ScopeLevel.COURSE: 2,
    ScopeLevel.SPECIALIZATION: 3,
    ScopeLevel.COHORT: 4,
    ScopeLevel.STUDENT: 5,
}


class SubjectKind(str, enum.Enum):
    USER = "USER"
    GROUP = "GROUP"


# --------------------------------------------------------------------------- #
# The rows
# --------------------------------------------------------------------------- #

class AccessGroup(Base):
    """A named set of faculty who inherit the same capabilities.

    Group-level rather than per-person because "the placement coordinators" is a
    role that outlives its current membership: a grant issued to the group is
    held by whoever joins later, without an admin remembering to repeat it.
    """

    __tablename__ = "access_groups"
    __table_args__ = (UniqueConstraint("name", name="uq_access_group_name"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccessGroupMember(Base):
    __tablename__ = "access_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_access_group_member"),
        Index("ix_access_group_member_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("access_groups.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    added_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


#: A grant takes effect immediately, unless a DEPUTY granted a capability that
#: carries PII, in which case a different holder of `admin.governance` has to
#: agree first (B2.4). The Main Admin's grants are always written `active`
#: (`routers/governance.py::initial_approval_state`, 2026-09-16).
APPROVAL_ACTIVE: Final[str] = "active"
APPROVAL_PENDING: Final[str] = "pending_approval"
APPROVAL_STATES: Final[frozenset[str]] = frozenset({APPROVAL_ACTIVE, APPROVAL_PENDING})

#: How long a grant runs before somebody has to look at it again (B2.4).
#:
#: A REVIEW IS NOT AN EXPIRY. An expiry ends the grant on its own; a review only
#: puts it in front of a person, who extends it or revokes it. Most of the access
#: that goes wrong in an institution is access that was correct when it was given
#: and that nobody revisited, so the grant that never lapses is exactly the one
#: that needs a date on it.
REVIEW_AFTER_DAYS: Final[int] = 180

#: How far ahead `GET /review` looks. A month is long enough that the office can
#: act between two of its own meetings, and short enough that the queue is a list
#: of things to do rather than a second copy of the grants table.
REVIEW_HORIZON_DAYS: Final[int] = 30


class CapabilityGrant(Base):
    """One capability, held by one user or one group, until revoked or expired.

    NOT deleted on revoke. `revoked_at` is stamped and the row stays, because
    "who held exports last March, and who signed it off" has to remain
    answerable — the same reasoning as `badge_evidence.upload_id` being SET NULL
    rather than CASCADE. A governance table that forgets is not governance.
    """

    __tablename__ = "capability_grants"
    __table_args__ = (
        # Declared HERE as well as in the migration, or `alembic check` reports it
        # as a removed constraint on every run and real drift becomes invisible
        # behind the noise — the incident recorded on redesign.py's outbox indexes.
        CheckConstraint(
            "(subject_kind = 'USER'  AND subject_user_id  IS NOT NULL AND subject_group_id IS NULL)"
            " OR "
            "(subject_kind = 'GROUP' AND subject_group_id IS NOT NULL AND subject_user_id  IS NULL)",
            name="ck_capability_grant_one_subject",
        ),
        CheckConstraint(
            "(scope_level IS NULL AND scope_id IS NULL)"
            " OR (scope_level IS NOT NULL AND scope_id IS NOT NULL)",
            name="ck_capability_grant_scope_pair",
        ),
        Index("ix_capgrant_user_live", "subject_user_id", "capability", "revoked_at"),
        Index("ix_capgrant_group_live", "subject_group_id", "capability", "revoked_at"),
        Index("ix_capgrant_capability", "capability"),
        # B1.2's rung. Declared here for the same reason as the constraints
        # above: it is created by b2c9e04a7731 and, undeclared, `alembic check`
        # asks to drop it on every single run.
        Index("ix_capgrant_scope", "scope_level", "scope_id"),
        CheckConstraint(
            "approval_state IN ('active', 'pending_approval')",
            name="ck_capability_grant_approval_state",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    #: A key from CAPABILITIES. A plain String, not a PG enum, so adding a
    #: capability is a deploy rather than a type migration — the same choice
    #: auth_tokens.purpose makes and for the same reason.
    capability: Mapped[str] = mapped_column(String(64), nullable=False)

    #: WHERE this grant reaches, as a rung of the spine — or NULL for everywhere.
    #:
    #: NULL IS PROGRAMME-WIDE AND IS NOT A MISSING VALUE. A grant that reaches
    #: every college hangs on no rung; representing that as a seventh ScopeLevel
    #: member would make "PROGRAMME" available to feature overrides, where it
    #: means nothing. The check constraint below is what stops the two columns
    #: from disagreeing — a level with no id reaches nothing and an id with no
    #: level reaches everything, and both are silent.
    #:
    #: `scope_id` is deliberately not a foreign key, for the same reason
    #: `feature_overrides.target_id` is not: it points at one of five tables
    #: depending on the level, and no database expresses a polymorphic FK.
    #: Resolution joins explicitly per level in policies.scope_filter.
    scope_level: Mapped[ScopeLevel | None] = mapped_column(
        Enum(ScopeLevel, name="governance_scope_level"), nullable=True
    )
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True)


    subject_kind: Mapped[SubjectKind] = mapped_column(
        Enum(SubjectKind, name="governance_subject_kind"), nullable=False
    )
    #: Exactly one of these is set; the CHECK constraint in the migration is what
    #: enforces that, because two half-set columns is a grant nobody holds.
    subject_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    subject_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("access_groups.id", ondelete="CASCADE"), nullable=True
    )

    reason: Mapped[str] = mapped_column(String, nullable=False)
    granted_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    #: NULL means it does not lapse. An expiry is the cheapest way to stop a
    #: capability issued for one November audit from still being held in March.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    #: When somebody should LOOK AT THIS AGAIN, which is not when it expires
    #: (B2.4). An expiry ends a grant; a review date only asks whether it is
    #: still the right grant. Most of the access that goes wrong in an
    #: institution is access that was correct when it was given and nobody
    #: revisited — so the review queue is the point, and a grant with no expiry
    #: still gets one of these.
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: `active` or `pending_approval` (B2.4). A capability the catalogue marks
    #: `carries_pii`, granted by a DEPUTY, does not take effect until a
    #: different holder of `admin.governance` approves it, so the person
    #: granting and the person agreeing are two people. The Main Admin's grants
    #: are written `active` whatever they carry
    #: (`routers/governance.py::initial_approval_state`).
    #:
    #: A String with a check constraint rather than a Postgres enum, for the
    #: reason `capability` above is one: a new state should be a deploy, not a
    #: type migration. AGENTS.md's three enum gotchas are all about the cost of
    #: getting that wrong.
    approval_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=APPROVAL_ACTIVE, server_default=APPROVAL_ACTIVE
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: The role the subject held WHEN THIS WAS GRANTED (B2.5).
    #:
    #: A grant is a decision about a person in a role -- "this MENTOR may read
    #: the registrations queue". If that account later becomes something else,
    #: the decision no longer describes anybody, and a grant that silently
    #: survives a role change is how a demoted account keeps a console screen.
    #: NULL means "granted before this column existed", and those are honoured:
    #: the backfill cannot know what role was held at the time, and guessing
    #: would revoke real access on the deploy that shipped it.
    role_at_grant: Mapped[str | None] = mapped_column(String(32), nullable=True)


class FeatureOverride(Base):
    """A student-facing feature switched off (or back on) at one rung.

    `enabled` exists rather than only ever meaning "off" because the most-specific
    rule wins: switching the voice interviewer off for a specialization and back
    on for one student inside it is two rows, and deleting the broader one would
    turn it on for the other eighty-five.
    """

    __tablename__ = "feature_overrides"
    __table_args__ = (
        UniqueConstraint("feature", "scope", "target_id", name="uq_feature_override_target"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[ScopeLevel] = mapped_column(
        Enum(ScopeLevel, name="governance_scope_level"), nullable=False
    )
    #: The id of the college / department / course / specialization / cohort /
    #: student this hangs on. Not an FK: it points at six different tables
    #: depending on `scope`, and a polymorphic FK is a constraint no database can
    #: express. Resolution joins explicitly per level instead.
    target_id: Mapped[str] = mapped_column(String, nullable=False)

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)
    #: What the STUDENT is told when they reach the switched-off thing (B2.2).
    #:
    #: Separate from `reason`, which is why the office did it and is nobody
    #: else's business -- "withheld pending the disciplinary meeting" is a true
    #: reason and not a sentence to put on a student's screen. Null means the
    #: feature is simply absent from their console, which is the right default:
    #: a message is a decision to explain, and explaining is not always kind.
    student_message: Mapped[str | None] = mapped_column(String, nullable=True)
    set_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
