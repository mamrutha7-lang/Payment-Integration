"""Governance — the Main Admin console's access endpoints.

GATED ON `admin.governance` THROUGHOUT (B2.6), not on the ADMIN role.

It was `require_admin` at every call site, and for good reasons that have not
changed: REEP has one Main Admin, and deciding what faculty may see is that
account's instrument. The capability keeps that as the DEFAULT -- the Main Admin
holds `admin.governance` by baseline and nobody else holds anything -- and adds
one thing the role gate could not express: a DEPUTY. When the office account is
unreachable and a grant has to be made, the alternative to a deputy is sharing
the Main Admin's mailbox, which is worse in every way and leaves an audit trail
naming the wrong person.

So the deputy is a grant like any other: one key, one named faculty member, a
typed reason, an audit row, revocable in one click. `_refuse_second_main_admin`
in `app/grant_access.py` is UNTOUCHED and must stay so -- a deputy is not a
second Main Admin, holds exactly this one key and nothing else, and cannot mint
an ADMIN account. `tests/test_governance_delegation.py` pins both halves.

Two instruments with opposite defaults, kept apart here as
they are in the model:

    /grants    capability grants for staff. Deny past the role baseline.
    /features  student feature overrides. Allow until switched off.

EVERY MUTATION DEMANDS A REASON, and it is checked in the API rather than only in
the form. A governance trail whose entries do not say why is a list of dates, and
a client is not where that promise can be kept — the twenty-character floor is
here so a second client, a script, or a curl cannot skip it.

EVERY MUTATION IS AUDITED through `architecture_events.record_change`, the
existing writer, rather than a second audit table. It already records actor,
entity, action, before/after and the route; a parallel implementation would be
the copy that stops matching.

THE MAIN ADMIN'S GRANT IS LIVE THE MOMENT IT IS WRITTEN; A DEPUTY'S
`carries_pii` GRANT NEEDS TWO PEOPLE (B2.4, amended 2026-09-16). The office is
one account by rule and the authority every other grant on the deployment
traces back to, so a second signature on ITS decision could only ever come from
a deputy it appointed itself -- one person's decision, asked twice.
`initial_approval_state` is the one place that reads WHO is granting: the Main
Admin's rows are written `active`; a deputy's grant of a capability that shows
a student's own record is written `pending_approval` and holds nothing until a
DIFFERENT holder of `admin.governance` approves it. `granted_capabilities` and
`granted_reaches` both ignore a pending row, so the state is enforced where
access is resolved and not only on the screen that shows it. The approver may
not be the granter, which is the only part of a four-eyes rule that actually
does anything.

THE CATALOGUE ENDPOINTS EXIST FOR THE DROPDOWNS. `/catalogue` and `/hierarchy`
serve exactly what the console's selects need, from the same constants the
enforcement reads — so a capability the API does not know can never appear in the
list an admin picks from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import batch_labels, policies
from ..architecture_events import record_change
from ..db import get_db
from ..governance import capabilities_for, granted_capabilities, require_capability
from ..identity import get_current_session
from ..models.cohort import Cohort
from ..models.governance import (
    APPROVAL_ACTIVE,
    APPROVAL_PENDING,
    CAPABILITIES,
    CAPABILITIES_BY_KEY,
    Capability,
    FEATURES,
    FEATURES_BY_KEY,
    REVIEW_AFTER_DAYS,
    REVIEW_HORIZON_DAYS,
    AccessGroup,
    AccessGroupMember,
    CapabilityGrant,
    CapabilityScope,
    FeatureOverride,
    ScopeLevel,
    SubjectKind,
)
from ..models.institution import (
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from ..models.user import Role, Student, User

router = APIRouter(prefix="/admin/governance", tags=["governance"])

#: B2.6. Declared once and passed by name at every call site, so the gate reads
#: the same everywhere and `tools/ci/check_capability_enforcement.py` can resolve
#: it without a grep for string literals.
GOVERNANCE_CAPABILITY = "admin.governance"


def require_governance(db: Session, session: dict) -> None:
    """The one gate on this router: staff, holding `admin.governance`.

    A ROLE FLOOR UNDER A CAPABILITY, which is the house pattern (see
    `require_staff` under `mentor.notebook` in redesign.py). The floor is not
    redundant with the capability: it is the thing that keeps a STUDENT or
    ALUMNI session out of this router even in the presence of a grant row
    somebody wrote by hand, and it answers before any database read.

    REPLACES `require_admin`, which is why this is a function and not four lines
    inlined sixteen times: the Main Admin holds `admin.governance` by baseline,
    so nothing changes for the office account, and a deputy now exists that the
    role gate could not express (see the module docstring).
    """
    policies.require_staff(session)
    require_capability(db, session, GOVERNANCE_CAPABILITY)


def initial_approval_state(capability: Capability, session: dict) -> str:
    """The state a NEW grant is written in, decided by WHO is granting it.

    THE MAIN ADMIN DOES NOT ASK ANYBODY'S PERMISSION (2026-09-16). The office is
    ONE account by rule (`_refuse_second_main_admin`), it holds every console
    key by baseline, and every deputy on the deployment holds Governance on its
    say-so -- so a second signature on the office's own decision could only
    ever come from somebody the office appointed to give it. That is one
    person's decision asked twice, at the cost of a second account, and until
    this function existed it was exactly what happened: a `carries_pii` grant
    made by the Main Admin landed `pending_approval`, and on the one-admin
    deployment this product is built for it never activated until the office
    appointed a deputy whose only job was to agree with it. The owner's rule is
    that the Main Admin is the one authority that grants access and control,
    and does so without a second approval.

    A DEPUTY'S GRANT OF A `carries_pii` CAPABILITY STILL WAITS (B2.4). A deputy
    holds exactly one key, on the office's say-so; handing a student's records
    to a colleague on their own is the decision the four-eyes rule exists for,
    and the second pair of eyes is the Main Admin's -- or another deputy's,
    `approve_grant`'s rule unchanged. A capability that shows no student record
    is live on anybody's say-so, as it always was.

    `session["role"]` is the signed claim and is the right thing to read here:
    this is a WRITE, decided by who is at the keyboard now, and a role change
    through `grant_access` advances `token_version` so a demoted office account
    cannot keep minting live grants on a stale cookie.

    ONE function, called by BOTH writers of `capability_grants` (`create_grants`
    here and `appoint_college_admin` in admin.py): the rule that decides whether
    a student's records changed hands on one signature or two must not exist in
    two spellings.
    """
    if session.get("role") == Role.ADMIN.value:
        return APPROVAL_ACTIVE
    return APPROVAL_PENDING if capability.carries_pii else APPROVAL_ACTIVE


#: Long enough that "asdf" and "ok" do not pass, short enough that a real
#: sentence does. A trail of one-word reasons is indistinguishable from none.
MIN_REASON_CHARS = 20


def _reason(value: str) -> str:
    text = (value or "").strip()
    if len(text) < MIN_REASON_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"A reason of at least {MIN_REASON_CHARS} characters is required. "
                "It is written onto the audit trail and read by whoever reviews "
                "this grant later."
            ),
        )
    return text


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """Read a naive datetime as UTC before comparing it with one.

    JSON has no timezone rule, so `"2026-12-01T00:00:00"` arrives naive from a
    perfectly ordinary client and `naive <= aware` raises TypeError — a 500 on
    an admin extending a grant, caused by a missing `Z`. Assuming UTC matches
    what the column stores and what every other date in this API means.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Catalogue + hierarchy — the dropdown sources
# --------------------------------------------------------------------------- #

class CapabilityOut(BaseModel):
    key: str
    label: str
    scope: str
    carries_pii: bool


class FeatureOut(BaseModel):
    key: str
    label: str
    #: Does a router actually ask about this key (B2.2)? False means the switch
    #: is inert, the screen must say so, and `PUT /features` REFUSES to set one
    #: — see `set_override`. Served rather than mirrored in the client for the
    #: reason the whole catalogue is: a hand-maintained list of what the server
    #: enforces is right only on the day somebody wrote it, and the Phase 2
    #: screen carried exactly such a list with a comment saying so.
    enforced: bool


class CatalogueOut(BaseModel):
    capabilities: list[CapabilityOut]
    features: list[FeatureOut]
    min_reason_chars: int


@router.get("/catalogue", response_model=CatalogueOut)
def catalogue(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CatalogueOut:
    """What the pickers offer, from the constants enforcement reads.

    Served rather than duplicated in the client so a capability cannot appear in
    a dropdown that no call site checks — the row that names a promise the API
    does not keep.
    """
    require_governance(db, session)
    return CatalogueOut(
        capabilities=[
            CapabilityOut(key=c.key, label=c.label, scope=c.scope.value, carries_pii=c.carries_pii)
            for c in CAPABILITIES
        ],
        features=[
            FeatureOut(key=f.key, label=f.label, enforced=f.enforced) for f in FEATURES
        ],
        min_reason_chars=MIN_REASON_CHARS,
    )


class HierarchyNode(BaseModel):
    scope: str
    id: str
    label: str
    parent_id: str | None
    students: int


@router.get("/hierarchy", response_model=list[HierarchyNode])
def hierarchy(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[HierarchyNode]:
    """Every rung an override can hang on, with a live student count each.

    The count is what makes the console's blast radius honest — "switch off for
    86 students" rather than "switch off for AI & ML". Counted per level from
    `students.cohort_id` through the cohort's own ancestor pointers, because a
    cohort may attach at department, course or specialization depth and only its
    own columns say which.
    """
    require_governance(db, session)

    def counts(column) -> dict[str, int]:
        rows = db.execute(
            select(column, func.count(Student.id))
            .select_from(Student)
            .join(Cohort, Student.cohort_id == Cohort.id)
            .where(column.isnot(None))
            .group_by(column)
        ).all()
        return {str(k): int(n) for k, n in rows}

    by_cohort = counts(Cohort.id)
    by_spec = counts(Cohort.specialization_id)
    by_course = counts(Cohort.course_id)
    by_dept = counts(Cohort.department_id)

    out: list[HierarchyNode] = []
    colleges = db.scalars(select(College).order_by(College.name)).all()
    departments = db.scalars(select(Department).order_by(Department.name)).all()
    by_college: dict[str, int] = {}
    for d in departments:
        by_college[d.college_id] = by_college.get(d.college_id, 0) + by_dept.get(d.id, 0)

    for c in colleges:
        out.append(HierarchyNode(scope="COLLEGE", id=c.id, label=c.name,
                                 parent_id=None, students=by_college.get(c.id, 0)))
    for d in departments:
        out.append(HierarchyNode(scope="DEPARTMENT", id=d.id, label=d.name,
                                 parent_id=d.college_id, students=by_dept.get(d.id, 0)))
    # Held rather than iterated straight into `out`: a cohort's label names the
    # course and specialization it hangs under, and these are the same two
    # reads. See the label below.
    course_rows = db.scalars(select(AcademicCourse).order_by(AcademicCourse.name)).all()
    spec_rows = db.scalars(
        select(AcademicSpecialization).order_by(AcademicSpecialization.name)
    ).all()
    for co in course_rows:
        out.append(HierarchyNode(scope="COURSE", id=co.id, label=co.name,
                                 parent_id=co.department_id, students=by_course.get(co.id, 0)))
    for sp in spec_rows:
        out.append(HierarchyNode(scope="SPECIALIZATION", id=sp.id, label=sp.name,
                                 parent_id=sp.course_id, students=by_spec.get(sp.id, 0)))
    course_names = {co.id: co.name for co in course_rows}
    spec_names = {sp.id: sp.name for sp in spec_rows}
    for ch in db.scalars(select(Cohort).order_by(Cohort.name)).all():
        # A cohort attaches at whichever depth it was created with — the levels
        # are individually optional — so its parent is the deepest pointer it
        # actually carries. `_resolve_ancestry` in admin.py is what guarantees
        # the shallower ones agree.
        parent = ch.specialization_id or ch.course_id or ch.department_id
        # The label is the spine plus the year, composed from those pointers
        # (`batch_labels.compose`). `cohorts.name` is the YEAR alone since
        # a4e7c92d1f38, so the old `name — batch_label` would have offered the
        # office four scope targets all reading "2026-28".
        out.append(HierarchyNode(scope="COHORT", id=ch.id,
                                 label=batch_labels.compose(
                                     course_names.get(ch.course_id) if ch.course_id else None,
                                     spec_names.get(ch.specialization_id)
                                     if ch.specialization_id else None,
                                     ch.name,
                                     ch.batch_label,
                                 ),
                                 parent_id=parent, students=by_cohort.get(ch.id, 0)))
    return out


class StaffOut(BaseModel):
    user_id: str
    name: str
    email: str
    role: str


@router.get("/staff", response_model=list[StaffOut])
def staff(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[StaffOut]:
    """The people a capability can be granted to. Staff only — a capability is
    a staff instrument, and offering a STUDENT in the picker would invite an
    admin to grant one and wonder why nothing changed."""
    require_governance(db, session)
    rows = db.scalars(
        select(User).where(User.role.in_([Role.MENTOR, Role.ADMIN])).order_by(User.name)
    ).all()
    return [
        StaffOut(user_id=u.id, name=u.name or u.email, email=u.email, role=u.role.value)
        for u in rows
    ]


# --------------------------------------------------------------------------- #
# Capability grants
# --------------------------------------------------------------------------- #

#: The most rows one grant request may write: people (or groups) times access
#: types. A whole faculty given a handful of screens is well inside it; a
#: number past it is a client looping, and one transaction that size would hold
#: the grants table for the length of it.
MAX_GRANT_ROWS_PER_REQUEST = 1000

#: The most grants one removal may take away in a single act.
MAX_REVOKES_PER_REQUEST = 500


class GrantIn(BaseModel):
    #: One access type, the field every client before 2026-10-16 sends. Kept,
    #: and folded into `capabilities` below, so an older client still works.
    capability: str | None = None
    #: MANY ACCESS TYPES IN ONE ACT (2026-10-16, the owner's request: "give
    #: access to many at a time"). The console ticks several access types for
    #: several people, and the API writes every (access type, person) pair in
    #: ONE transaction with ONE reason, so a refusal half-way leaves nothing
    #: written rather than half a decision.
    capabilities: list[str] = Field(default_factory=list)
    #: Exactly one of these carries the subjects; both lists so a single request
    #: can grant to four people, matching the console's multi-select.
    user_ids: list[str] = Field(default_factory=list)
    group_ids: list[str] = Field(default_factory=list)
    reason: str
    expires_at: datetime | None = None
    # ------------------------------------------------------------- B1.2 --
    #
    # HOW FAR THE GRANT REACHES, and the half of B1.2 that was missing.
    # `capability_grants.scope_level` / `scope_id` have been read by
    # `governance.granted_reaches` and `policies.scope_filter` since B1.2
    # landed, and enforced by `require_capability(..., target=...)` at every
    # call site that names one — but nothing could WRITE them, so every row
    # this endpoint made was programme-wide and the enforcement only ever saw
    # `(None, None)`. A rule that cannot be expressed is not a rule; the
    # console's scope select was correspondingly disabled.
    #
    # BOTH OR NEITHER. A level with no id names no target, and an id with no
    # level cannot be resolved to one — either alone would be stored as a
    # narrowing and read back as programme-wide, which is the widest possible
    # reading of a request that asked for the narrowest.
    #
    # OMITTED IS PROGRAMME-WIDE, and that is what keeps this additive: every
    # client that has ever called this endpoint sends neither field and keeps
    # the grant it has always been given.
    scope_level: ScopeLevel | None = None
    scope_id: str | None = None

    @field_validator("capability")
    @classmethod
    def _known(cls, v: str | None) -> str | None:
        if v is not None and v not in CAPABILITIES_BY_KEY:
            raise ValueError(f"unknown capability {v!r}")
        return v

    @field_validator("capabilities")
    @classmethod
    def _all_known(cls, values: list[str]) -> list[str]:
        for v in values:
            if v not in CAPABILITIES_BY_KEY:
                raise ValueError(f"unknown capability {v!r}")
        return values

    @property
    def capability_keys(self) -> list[str]:
        """Every access type asked for, the single field first, each once and
        in the order given."""
        ordered = ([self.capability] if self.capability else []) + self.capabilities
        return list(dict.fromkeys(ordered))

    @field_validator("scope_id", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> str | None:
        #: A `<select>` with nothing chosen posts "", not null. Treating that as
        #: a target id would fail the existence check below with "that target
        #: does not exist" for somebody who simply did not narrow the grant.
        text = str(v or "").strip()
        return text or None

    @model_validator(mode="after")
    def _scope_pair(self) -> "GrantIn":
        if not self.capability_keys:
            raise ValueError("name at least one access type to grant")
        if (self.scope_level is None) != (self.scope_id is None):
            raise ValueError(
                "a scope target needs both a level and an id: name the rung and "
                "the thing it hangs on, or neither for a programme-wide grant"
            )
        return self


class GrantOut(BaseModel):
    id: str
    capability: str
    capability_label: str
    #: The CAPABILITY's declared scope — `SCOPED` or `PROGRAMME`, straight off
    #: the catalogue — and NOT the grant's reach. The two are different
    #: questions with confusingly similar names: this one says whether a mentor
    #: group narrows the key at all, `scope_level` below says how far this
    #: particular grant was handed over. Unchanged, because every client built
    #: against it reads it as the first thing.
    scope: str
    #: B1.2. The reach, and `None` means programme-wide rather than "not asked".
    #: A grant with no target is the ordinary one and the column is NULL for it,
    #: so the absent value has exactly one meaning here.
    scope_level: str | None = None
    scope_id: str | None = None
    #: Resolved for the screen, the same way an override's target is. A target
    #: deleted after the grant was made reads "(removed)" rather than an id
    #: nobody can look up — and the grant then reaches nothing, which is what
    #: `reaches_target` already does with it.
    scope_label: str | None = None
    #: B2.4. The console paints a pending row differently and offers Approve on
    #: it; a client that does not know the field yet simply shows a grant that is
    #: listed and not yet working, which is the truth.
    carries_pii: bool = False
    approval_state: str = APPROVAL_ACTIVE
    approved_by: str | None = None
    approved_at: datetime | None = None
    review_at: datetime | None = None
    subject_kind: str
    subject_id: str
    subject_label: str
    reason: str
    granted_by: str | None
    granted_at: datetime
    expires_at: datetime | None


def _grant_out(db: Session, g: CapabilityGrant) -> GrantOut:
    cap = CAPABILITIES_BY_KEY.get(g.capability)
    if g.subject_kind == SubjectKind.USER:
        subject_id = g.subject_user_id or ""
        u = db.get(User, subject_id) if subject_id else None
        label = (u.name or u.email) if u else "(removed user)"
    else:
        subject_id = g.subject_group_id or ""
        grp = db.get(AccessGroup, subject_id) if subject_id else None
        label = grp.name if grp else "(removed group)"
    granter = db.get(User, g.granted_by_user_id) if g.granted_by_user_id else None
    approver = db.get(User, g.approved_by_user_id) if g.approved_by_user_id else None
    return GrantOut(
        id=g.id,
        capability=g.capability,
        capability_label=cap.label if cap else g.capability,
        scope=cap.scope.value if cap else "UNKNOWN",
        carries_pii=bool(cap.carries_pii) if cap else False,
        approval_state=g.approval_state,
        approved_by=(approver.name or approver.email) if approver else None,
        approved_at=g.approved_at,
        review_at=g.review_at,
        scope_level=g.scope_level.value if g.scope_level else None,
        scope_id=g.scope_id,
        scope_label=(
            _target_label(db, g.scope_level, g.scope_id)
            if g.scope_level and g.scope_id
            else None
        ),
        subject_kind=g.subject_kind.value,
        subject_id=subject_id,
        subject_label=label,
        reason=g.reason,
        granted_by=(granter.name or granter.email) if granter else None,
        granted_at=g.granted_at,
        expires_at=g.expires_at,
    )


@router.get("/grants", response_model=list[GrantOut])
def list_grants(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[GrantOut]:
    """Live grants only — INCLUDING the ones awaiting a second approval.

    Revoked and lapsed ones stay in the table and are read from the audit trail,
    which is where "who held this in March" belongs.

    A `pending_approval` row is listed on purpose, with its state on the row: it
    is a decision somebody made that has not taken effect, and hiding it until it
    works would mean the grant an admin just wrote is missing from the screen
    that is supposed to show what they did. `GET /review` is the queue; this is
    the register.
    """
    require_governance(db, session)
    now = _now()
    rows = db.scalars(
        select(CapabilityGrant)
        .where(
            CapabilityGrant.revoked_at.is_(None),
            or_(CapabilityGrant.expires_at.is_(None), CapabilityGrant.expires_at > now),
        )
        .order_by(CapabilityGrant.granted_at.desc())
    ).all()
    return [_grant_out(db, g) for g in rows]


@router.post("/grants", response_model=list[GrantOut], status_code=status.HTTP_201_CREATED)
def create_grants(
    body: GrantIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[GrantOut]:
    """Grant any number of access types to any number of users and groups, in
    one act and one transaction.

    MANY ACCESS TYPES SINCE 2026-10-16. The office gives a new placement
    coordinator four screens at once; one request per screen meant four
    reasons typed and four chances for the third to fail after the first two
    had landed. Every (access type, subject) pair is written here or none is.

    ONE REASON COVERS THE BATCH and is copied onto every row, because that is
    what an admin actually means: "these four, for this reason". Writing one
    audit entry for the batch instead would make "why does Dr. Rao hold this"
    answerable only by finding a grant she is not named in.

    Re-granting something already held is a no-op rather than a duplicate: two
    live rows for the same pair make revocation a question of which one.
    """
    require_governance(db, session)
    reason = _reason(body.reason)
    if not body.user_ids and not body.group_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Name at least one person or access group to grant this to.",
        )

    # B1.2. THE TARGET MUST EXIST AT THE LEVEL GIVEN, checked before a single
    # row is built. `_target_label` is the same resolver a feature override's
    # target goes through, reused rather than restated: two existence checks
    # against the same five tables is how one of them ends up accepting a rung
    # the other refuses.
    #
    # A grant hung on nothing would be stored, listed with an id nobody can look
    # up, and enforced as reaching NOBODY — `reaches_target` never matches an id
    # that is in no student's ancestry — so the admin would watch a grant they
    # made refuse every request, with the console showing it as live. 422 and
    # not 404: the request is well formed and the caller holds governance; it is
    # the target that is wrong.
    #
    # NOT REFUSED FOR A `PROGRAMME` CAPABILITY, deliberately. That word on the
    # catalogue means "no mentor GROUP narrows this" (models/governance.py says
    # so) and never "this cannot be scoped to the spine" — B1.4 narrows
    # `admin.analytics`, `admin.exports`, `admin.placement`, `admin.mentors`,
    # `admin.registrations` and `admin.swoc` by exactly these rungs, through
    # `policies.scope_filter`. Refusing here would make the six keys that most
    # need a fence the only ones that cannot have one.
    if body.scope_level is not None and body.scope_id is not None:
        if _target_label(db, body.scope_level, body.scope_id).startswith("(removed"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="That target does not exist at the level given.",
            )

    keys = body.capability_keys
    user_ids = list(dict.fromkeys(body.user_ids))
    group_ids = list(dict.fromkeys(body.group_ids))
    asked = len(keys) * (len(user_ids) + len(group_ids))
    if asked > MAX_GRANT_ROWS_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"That is {asked} grants in one go; the most is "
                f"{MAX_GRANT_ROWS_PER_REQUEST}. Give access in smaller batches."
            ),
        )

    # Every subject is checked BEFORE a row is built, so a bad id half-way
    # through a batch refuses the whole request rather than writing the pairs
    # that came before it.
    users: list[User] = []
    for uid in user_ids:
        user = db.get(User, uid)
        if user is None or user.role not in (Role.MENTOR, Role.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Capabilities are granted to staff. One of the ids is not a staff account.",
            )
        users.append(user)
    for gid in group_ids:
        if db.get(AccessGroup, gid) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="No such access group."
            )

    now = _now()
    # A grant with an expiry reviews AT that expiry -- the queue shows it as
    # running out, and the admin extends it or lets it lapse. One with no expiry
    # is the grant that most needs a date, because nothing else will ever bring
    # it back to anybody's attention.
    review_at = _aware(body.expires_at) or (now + timedelta(days=REVIEW_AFTER_DAYS))
    created: list[CapabilityGrant] = []

    def already_live(key: str, kind: SubjectKind, subject_id: str) -> bool:
        """Is there already a row for this pair that has not ended?

        A `pending_approval` row COUNTS, deliberately, and this query is
        unfiltered by `approval_state` for that reason rather than by omission.
        Granting twice while the first is awaiting approval would leave two rows
        for one decision, and the second approval would then have to pick one —
        which is the same ambiguity the no-duplicates rule exists to prevent,
        arriving through the one door that does not yet grant anything.
        """
        col = (
            CapabilityGrant.subject_user_id
            if kind == SubjectKind.USER
            else CapabilityGrant.subject_group_id
        )
        return db.scalar(
            select(CapabilityGrant.id).where(
                CapabilityGrant.capability == key,
                CapabilityGrant.subject_kind == kind,
                col == subject_id,
                # B1.2. THE REACH IS PART OF THE PAIR. Without these two clauses
                # this query asks "does she hold this key anywhere", and the
                # answer for a mentor already scoped to Mechanical is yes — so
                # granting her the same key over Civil would silently no-op and
                # the console would report success over a grant it did not make.
                # Two rows at different rungs are two different decisions, and
                # `scope_filter` unions them, which is what "and Civil as well"
                # means. Re-granting the SAME rung is still the no-op it was.
                CapabilityGrant.scope_level == body.scope_level,
                CapabilityGrant.scope_id == body.scope_id,
                CapabilityGrant.revoked_at.is_(None),
                or_(CapabilityGrant.expires_at.is_(None), CapabilityGrant.expires_at > now),
            )
        ) is not None

    for key in keys:
        # WHO is granting decides the state, not only WHAT is granted. The Main
        # Admin's row is live at once; a deputy's grant of a capability that
        # shows a student's own record is written, listed and audited, and
        # holds NOTHING until a different holder of `admin.governance` approves
        # it (B2.4). Asked per access type, because a batch can mix both kinds.
        approval_state = initial_approval_state(CAPABILITIES_BY_KEY[key], session)
        for user in users:
            if already_live(key, SubjectKind.USER, user.id):
                continue
            created.append(CapabilityGrant(
                capability=key, subject_kind=SubjectKind.USER, subject_user_id=user.id,
                reason=reason, granted_by_user_id=session["userId"], expires_at=body.expires_at,
                approval_state=approval_state, review_at=review_at,
                # B1.2. NULL on both columns is the programme-wide grant this
                # endpoint has always written; a pair is the narrowing.
                scope_level=body.scope_level, scope_id=body.scope_id,
                # B2.5. The role this decision was made about.
                # `granted_capabilities` stops counting the row if the account
                # becomes something else.
                role_at_grant=user.role.value,
            ))
        for gid in group_ids:
            if already_live(key, SubjectKind.GROUP, gid):
                continue
            created.append(CapabilityGrant(
                capability=key, subject_kind=SubjectKind.GROUP, subject_group_id=gid,
                reason=reason, granted_by_user_id=session["userId"], expires_at=body.expires_at,
                approval_state=approval_state, review_at=review_at,
                scope_level=body.scope_level, scope_id=body.scope_id,
                # `role_at_grant` STAYS NULL ON A GROUP GRANT, and that is right
                # rather than lazy: the decision names "the placement
                # coordinators", not a role, and there is no single account
                # whose role could later contradict it. Membership is
                # staff-only and checked when somebody joins, which is where
                # that question belongs.
            ))

    for g in created:
        db.add(g)
    db.flush()

    for g in created:
        cap = CAPABILITIES_BY_KEY[g.capability]
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="capability_grant", entity_id=g.id, action="GRANTED",
            before=None,
            after={
                "capability": g.capability, "scope": cap.scope.value,
                # The REACH, beside the capability's declared scope and not
                # instead of it — "who was handed admin.students" and "how far"
                # are the two halves of the question somebody asks this trail in
                # six months, and a row carrying only the first cannot answer it.
                "scope_level": g.scope_level.value if g.scope_level else None,
                "scope_id": g.scope_id,
                "subject_kind": g.subject_kind.value,
                "subject_id": g.subject_user_id or g.subject_group_id,
                "reason": reason,
                "expires_at": body.expires_at.isoformat() if body.expires_at else None,
                "review_at": review_at.isoformat(),
                "approval_state": g.approval_state,
                "carries_pii": cap.carries_pii,
                "role_at_grant": g.role_at_grant,
                "batch_size": len(created),
            },
            event_type="governance.capability.granted",
            payload={"capability": g.capability},
        )
    db.commit()
    for g in created:
        db.refresh(g)
    return [_grant_out(db, g) for g in created]


class RevokeIn(BaseModel):
    reason: str


@router.post("/grants/{grant_id}/revoke", response_model=GrantOut)
def revoke_grant(
    grant_id: str,
    body: RevokeIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> GrantOut:
    """Stamp, never delete. "Who held exports last March, and who signed it off"
    has to stay answerable after the answer stops being current."""
    require_governance(db, session)
    reason = _reason(body.reason)
    g = db.get(CapabilityGrant, grant_id)
    if g is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found.")
    if g.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already revoked.")
    g.revoked_at = _now()
    g.revoked_by_user_id = session["userId"]
    g.revoke_reason = reason
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="capability_grant", entity_id=g.id, action="REVOKED",
        before={"capability": g.capability, "reason": g.reason},
        after={"capability": g.capability, "revoke_reason": reason},
        event_type="governance.capability.revoked", payload={"capability": g.capability},
    )
    db.commit()
    db.refresh(g)
    return _grant_out(db, g)


class RevokeManyIn(BaseModel):
    #: The grants to take away. The console collects them from "Who has
    #: access": one access type ticked off for several people, or several
    #: grants of one person, which is the same request.
    grant_ids: list[str] = Field(min_length=1)
    reason: str


class RevokeManyOut(BaseModel):
    revoked: list[GrantOut]
    #: Ids that were already revoked when this request arrived (a second tab,
    #: a double press). Named rather than refused: what was asked is true.
    already_revoked: list[str] = Field(default_factory=list)


@router.post("/grants/revoke", response_model=RevokeManyOut)
def revoke_grants(
    body: RevokeManyIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RevokeManyOut:
    """Remove access from many at once: one reason, one transaction, one
    audit row per grant (2026-10-16, the owner's request: "remove one function
    for many at a time").

    The console's old "Remove access" on a selection posted `/{id}/revoke`
    once per row, so a refusal on the fourth left three removed and the rest
    standing, with one error on screen naming none of them. Here every id is
    checked first and an unknown one refuses the whole request; nothing is
    stamped unless all of it can be.

    Stamp, never delete, for `revoke_grant`'s reason. The path does not clash
    with `/grants/{grant_id}/revoke`, which has one more segment.
    """
    require_governance(db, session)
    reason = _reason(body.reason)
    ids = list(dict.fromkeys(body.grant_ids))
    if len(ids) > MAX_REVOKES_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"That is {len(ids)} grants in one go; the most is "
                f"{MAX_REVOKES_PER_REQUEST}. Remove access in smaller batches."
            ),
        )
    rows = {g.id: g for g in db.scalars(select(CapabilityGrant).where(CapabilityGrant.id.in_(ids)))}
    missing = [gid for gid in ids if gid not in rows]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"{len(missing)} of these grants could not be found, so nothing was "
                "removed. Reload the page and try again."
            ),
        )
    now = _now()
    revoked: list[CapabilityGrant] = []
    already: list[str] = []
    for gid in ids:
        g = rows[gid]
        if g.revoked_at is not None:
            already.append(gid)
            continue
        g.revoked_at = now
        g.revoked_by_user_id = session["userId"]
        g.revoke_reason = reason
        revoked.append(g)
    for g in revoked:
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="capability_grant", entity_id=g.id, action="REVOKED",
            before={"capability": g.capability, "reason": g.reason},
            after={
                "capability": g.capability,
                "revoke_reason": reason,
                "batch_size": len(revoked),
            },
            event_type="governance.capability.revoked", payload={"capability": g.capability},
        )
    db.commit()
    for g in revoked:
        db.refresh(g)
    return RevokeManyOut(
        revoked=[_grant_out(db, g) for g in revoked], already_revoked=already
    )


class ApproveIn(BaseModel):
    reason: str


@router.post("/grants/{grant_id}/approve", response_model=GrantOut)
def approve_grant(
    grant_id: str,
    body: ApproveIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> GrantOut:
    """The second pair of eyes on a DEPUTY'S `carries_pii` grant (B2.4).

    Only a deputy's row arrives here pending NOW: the Main Admin's grants are
    written live (`initial_approval_state`), so the office's own decisions no
    longer queue and the 409 below is what an approve on one of them answers.
    Rows the office granted BEFORE 2026-09-16 were written pending under the
    old rule and are still on the queue -- and the office is the one account
    that can clear them, below.

    THE SELF-APPROVAL REFUSAL IS THE WHOLE FEATURE, AND IT IS A DEPUTY'S.
    Everything else here — the state column, the check constraint, the four
    readers that honour it — is bookkeeping around one sentence: a deputy who
    decided cannot be the person who agreed. Drop that one check and the flow
    still works end to end, still writes two audit rows, still paints a pending
    badge and still shows an approver's name, and protects nobody at all.
    `tests/test_governance_review.py` asserts it in both directions, because an
    implementation that refuses the granter but admits anybody else is no better.

    THE MAIN ADMIN IS NOT REFUSED ON ITS OWN ROW. The office's signature is
    sufficient on any grant (that is what `initial_approval_state` says on the
    create path), so a pending row the office granted itself -- one written
    before the rule changed, or written while the office's session carried
    another role -- is finalised by the office pressing Approve with a reason,
    which is an audit row saying so. Refusing it, as this did for a day, left
    every such row stuck behind a deputy the office would have had to appoint
    for the purpose: exactly the arrangement the amendment removed.

    It is the GRANTER that is refused, not the SUBJECT. A Main Admin approving a
    grant made to themselves by a deputy is fine and is the ordinary shape of
    "the office was given exports by the deputy"; what must not happen is one
    person writing and agreeing to the same row.
    """
    require_governance(db, session)
    reason = _reason(body.reason)
    g = db.get(CapabilityGrant, grant_id)
    if g is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found.")
    if g.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That grant was revoked. Grant it again if it is still wanted.",
        )
    if g.approval_state == APPROVAL_ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That grant is already active."
        )
    if (
        g.granted_by_user_id
        and g.granted_by_user_id == session.get("userId")
        and session.get("role") != Role.ADMIN.value
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You granted this, so you cannot also approve it. A capability that "
                "carries a student's own records, granted by a deputy, needs a second "
                "person holding Governance — the Main Admin, or another deputy."
            ),
        )
    g.approval_state = APPROVAL_ACTIVE
    g.approved_by_user_id = session["userId"]
    g.approved_at = _now()
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="capability_grant", entity_id=g.id, action="APPROVED",
        before={"approval_state": APPROVAL_PENDING, "capability": g.capability},
        after={
            "approval_state": APPROVAL_ACTIVE,
            "capability": g.capability,
            "granted_by": g.granted_by_user_id,
            "reason": reason,
        },
        event_type="governance.capability.approved", payload={"capability": g.capability},
    )
    db.commit()
    db.refresh(g)
    return _grant_out(db, g)


class ExtendIn(BaseModel):
    reason: str
    expires_at: datetime | None = None
    review_at: datetime | None = None


@router.post("/grants/{grant_id}/extend", response_model=GrantOut)
def extend_grant(
    grant_id: str,
    body: ExtendIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> GrantOut:
    """Push out the expiry, the review date, or both — with a fresh reason.

    THE REASON IS RETYPED, NOT INHERITED. "Still needed" is a different decision
    from "needed", made months later by somebody who may not be the person who
    made the first one, and a review that copies the original reason forward
    produces a trail where every grant looks as though it was justified once and
    never questioned. That is the failure mode the review queue exists to break.

    A LAPSED GRANT IS NOT EXTENDED, IT IS GRANTED AGAIN (409). Reviving a row
    whose expiry has passed would restore access silently on the strength of a
    decision that has already ended, and the original reason would be doing work
    on a day nobody chose. Re-granting costs one request and writes a row that
    says what it is.

    Shortening is allowed and is not a special case: bringing an expiry forward
    is a narrowing, and the one thing this refuses is a date in the past, which
    would be a revocation wearing the wrong verb (`/revoke` records who and why).
    """
    require_governance(db, session)
    reason = _reason(body.reason)
    if body.expires_at is None and body.review_at is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Name a new expiry, a new review date, or both.",
        )
    g = db.get(CapabilityGrant, grant_id)
    if g is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found.")
    if g.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That grant was revoked."
        )
    now = _now()
    new_expires_at, new_review_at = _aware(body.expires_at), _aware(body.review_at)
    if g.expires_at is not None and g.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "That grant has already lapsed. Grant it again, with a reason for "
                "why it is needed now."
            ),
        )
    for label, value in (("expiry", new_expires_at), ("review date", new_review_at)):
        if value is not None and value <= now:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"The new {label} is in the past.",
            )
    before = {
        "expires_at": g.expires_at.isoformat() if g.expires_at else None,
        "review_at": g.review_at.isoformat() if g.review_at else None,
    }
    if new_expires_at is not None:
        g.expires_at = new_expires_at
    if new_review_at is not None:
        g.review_at = new_review_at
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="capability_grant", entity_id=g.id, action="EXTENDED",
        before=before,
        after={
            "capability": g.capability,
            "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            "review_at": g.review_at.isoformat() if g.review_at else None,
            "reason": reason,
        },
        event_type="governance.capability.extended", payload={"capability": g.capability},
    )
    db.commit()
    db.refresh(g)
    return _grant_out(db, g)


class ReviewOut(BaseModel):
    horizon_days: int
    pending: list[GrantOut]
    expiring: list[GrantOut]


@router.get("/review", response_model=ReviewOut)
def review_queue(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ReviewOut:
    """The two lists an admin has to act on: waiting on them, and running out.

    TWO LISTS AND NOT ONE, because they need opposite actions. A pending grant
    holds nothing and somebody is waiting; an expiring grant is working now and
    will stop. Merging them into "needs attention" would put the urgent one
    behind the routine one on the day they collide.

    EXPIRING IS `expires_at` OR `review_at`, whichever falls inside the horizon.
    A grant with no expiry never appears on an expiry-only queue, which is
    exactly the grant that most needs looking at — the one issued for a November
    audit that is still held in March. That is what `review_at` is for, and a
    queue that read only the expiry would have made the column decorative.
    """
    require_governance(db, session)
    now = _now()
    horizon = now + timedelta(days=REVIEW_HORIZON_DAYS)
    unfinished = (
        CapabilityGrant.revoked_at.is_(None),
        or_(CapabilityGrant.expires_at.is_(None), CapabilityGrant.expires_at > now),
    )
    pending = db.scalars(
        select(CapabilityGrant)
        .where(*unfinished, CapabilityGrant.approval_state == APPROVAL_PENDING)
        .order_by(CapabilityGrant.granted_at.asc())
    ).all()
    expiring = db.scalars(
        select(CapabilityGrant)
        .where(
            *unfinished,
            CapabilityGrant.approval_state == APPROVAL_ACTIVE,
            or_(
                CapabilityGrant.expires_at <= horizon,
                CapabilityGrant.review_at <= horizon,
            ),
        )
        .order_by(
            func.coalesce(CapabilityGrant.expires_at, CapabilityGrant.review_at).asc()
        )
    ).all()
    return ReviewOut(
        horizon_days=REVIEW_HORIZON_DAYS,
        pending=[_grant_out(db, g) for g in pending],
        expiring=[_grant_out(db, g) for g in expiring],
    )


@router.get("/effective/{user_id}", response_model=list[str])
def effective_capabilities(
    user_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[str]:
    """What this person actually holds — baseline plus grants, resolved.

    The console shows it beside the grant list because the two are not the same
    thing: a mentor holds sixteen scoped capabilities with no grant at all, and an
    admin looking only at grants would conclude they hold nothing.
    """
    require_governance(db, session)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return sorted(capabilities_for(db, {"userId": user.id, "role": user.role.value}))


# --------------------------------------------------------------------------- #
# Access groups
# --------------------------------------------------------------------------- #

class GroupIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = None


class GroupMemberOut(BaseModel):
    user_id: str
    name: str
    email: str


class GroupOut(BaseModel):
    id: str
    name: str
    description: str | None
    members: list[GroupMemberOut]
    capabilities: list[str]


def _group_out(db: Session, grp: AccessGroup) -> GroupOut:
    members = db.execute(
        select(User).join(AccessGroupMember, AccessGroupMember.user_id == User.id)
        .where(AccessGroupMember.group_id == grp.id).order_by(User.name)
    ).scalars().all()
    now = _now()
    # THE FOURTH READER OF A GRANT, and the one that forgot. B2.4's approval
    # state is enforced in `governance._live_grant_clauses`, which is what
    # `granted_capabilities` asks — but this screen builds the group card from
    # its own query, so a `pending_approval` grant was listed here as a
    # capability the group HOLDS while resolving to nothing on every request.
    # A console that reports access the API does not give is worse than one that
    # reports none: nobody goes looking for the bug.
    caps = db.scalars(
        select(CapabilityGrant.capability).where(
            CapabilityGrant.subject_kind == SubjectKind.GROUP,
            CapabilityGrant.subject_group_id == grp.id,
            CapabilityGrant.revoked_at.is_(None),
            or_(CapabilityGrant.expires_at.is_(None), CapabilityGrant.expires_at > now),
            CapabilityGrant.approval_state == APPROVAL_ACTIVE,
        )
    ).all()
    return GroupOut(
        id=grp.id, name=grp.name, description=grp.description,
        members=[GroupMemberOut(user_id=u.id, name=u.name or u.email, email=u.email) for u in members],
        capabilities=sorted(set(caps)),
    )


@router.get("/groups", response_model=list[GroupOut])
def list_groups(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[GroupOut]:
    require_governance(db, session)
    return [_group_out(db, g) for g in db.scalars(select(AccessGroup).order_by(AccessGroup.name)).all()]


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(
    body: GroupIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> GroupOut:
    require_governance(db, session)
    if db.scalar(select(AccessGroup.id).where(AccessGroup.name == body.name.strip())):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A group with that name exists.")
    grp = AccessGroup(
        name=body.name.strip(), description=(body.description or "").strip() or None,
        created_by_user_id=session["userId"],
    )
    db.add(grp)
    db.flush()
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="access_group", entity_id=grp.id, action="CREATED",
        before=None, after={"name": grp.name},
        event_type="governance.group.created", payload={"name": grp.name},
    )
    db.commit()
    db.refresh(grp)
    return _group_out(db, grp)


class MemberIn(BaseModel):
    user_ids: list[str]
    reason: str


@router.post("/groups/{group_id}/members", response_model=GroupOut)
def add_members(
    group_id: str,
    body: MemberIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> GroupOut:
    """Adding a member hands them every capability the group holds, so it is
    audited exactly like a grant and asks for the same reason."""
    require_governance(db, session)
    reason = _reason(body.reason)
    grp = db.get(AccessGroup, group_id)
    if grp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    for uid in body.user_ids:
        user = db.get(User, uid)
        if user is None or user.role not in (Role.MENTOR, Role.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Access groups hold staff accounts.",
            )
        exists = db.scalar(select(AccessGroupMember.id).where(
            AccessGroupMember.group_id == group_id, AccessGroupMember.user_id == uid))
        if exists:
            continue
        db.add(AccessGroupMember(group_id=group_id, user_id=uid, added_by_user_id=session["userId"]))
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="access_group", entity_id=group_id, action="MEMBER_ADDED",
            before=None, after={"user_id": uid, "reason": reason},
            event_type="governance.group.member_added", payload={"user_id": uid},
        )
    db.commit()
    db.refresh(grp)
    return _group_out(db, grp)


@router.delete("/groups/{group_id}/members/{user_id}", response_model=GroupOut)
def remove_member(
    group_id: str,
    user_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> GroupOut:
    require_governance(db, session)
    grp = db.get(AccessGroup, group_id)
    if grp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    row = db.scalar(select(AccessGroupMember).where(
        AccessGroupMember.group_id == group_id, AccessGroupMember.user_id == user_id))
    if row is not None:
        db.delete(row)
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="access_group", entity_id=group_id, action="MEMBER_REMOVED",
            before={"user_id": user_id}, after=None,
            event_type="governance.group.member_removed", payload={"user_id": user_id},
        )
        db.commit()
    db.refresh(grp)
    return _group_out(db, grp)


# --------------------------------------------------------------------------- #
# Student feature overrides
# --------------------------------------------------------------------------- #

class OverrideIn(BaseModel):
    feature: str
    scope: ScopeLevel
    target_id: str
    enabled: bool = False
    reason: str
    expires_at: datetime | None = None
    #: What the STUDENT reads at the refusal. SEPARATE FROM `reason`, which is
    #: the office's note to itself and is nobody else's business — "withheld
    #: pending the disciplinary meeting" is a true reason and not a sentence to
    #: put on somebody's screen. Optional: null means the feature is simply
    #: absent from their console, and explaining is not always kind.
    student_message: str | None = Field(default=None, max_length=500)

    @field_validator("feature")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in FEATURES_BY_KEY:
            raise ValueError(f"unknown feature {v!r}")
        return v


class OverrideOut(BaseModel):
    id: str
    feature: str
    feature_label: str
    #: False when no router asks about this key. The screen shows "not wired
    #: yet" and `PUT /features` would have refused it.
    feature_enforced: bool
    scope: str
    target_id: str
    target_label: str
    enabled: bool
    reason: str
    student_message: str | None
    set_by: str | None
    set_at: datetime
    expires_at: datetime | None
    students_affected: int


_TARGET_MODEL = {
    ScopeLevel.COLLEGE: College,
    ScopeLevel.DEPARTMENT: Department,
    ScopeLevel.COURSE: AcademicCourse,
    ScopeLevel.SPECIALIZATION: AcademicSpecialization,
    ScopeLevel.COHORT: Cohort,
}


def _target_label(db: Session, scope: ScopeLevel, target_id: str) -> str:
    if scope == ScopeLevel.STUDENT:
        s = db.get(Student, target_id)
        if s is None:
            return "(removed student)"
        u = db.get(User, s.user_id)
        return f"{(u.name or u.email) if u else target_id} — {s.usn or ''}".strip(" —")
    model = _TARGET_MODEL[scope]
    row = db.get(model, target_id)
    if row is None:
        return "(removed)"
    return getattr(row, "name", None) or getattr(row, "label", None) or target_id


def _students_affected(db: Session, scope: ScopeLevel, target_id: str) -> int:
    if scope == ScopeLevel.STUDENT:
        return 1 if db.get(Student, target_id) is not None else 0
    q = select(func.count(Student.id)).select_from(Student).join(Cohort, Student.cohort_id == Cohort.id)
    if scope == ScopeLevel.COHORT:
        return int(db.scalar(q.where(Cohort.id == target_id)) or 0)
    if scope == ScopeLevel.SPECIALIZATION:
        return int(db.scalar(q.where(Cohort.specialization_id == target_id)) or 0)
    if scope == ScopeLevel.COURSE:
        return int(db.scalar(q.where(Cohort.course_id == target_id)) or 0)
    if scope == ScopeLevel.DEPARTMENT:
        return int(db.scalar(q.where(Cohort.department_id == target_id)) or 0)
    # College: through its departments.
    dept_ids = db.scalars(select(Department.id).where(Department.college_id == target_id)).all()
    if not dept_ids:
        return 0
    return int(db.scalar(q.where(Cohort.department_id.in_(dept_ids))) or 0)


def _override_out(db: Session, o: FeatureOverride) -> OverrideOut:
    feat = FEATURES_BY_KEY.get(o.feature)
    setter = db.get(User, o.set_by_user_id) if o.set_by_user_id else None
    return OverrideOut(
        id=o.id, feature=o.feature, feature_label=feat.label if feat else o.feature,
        feature_enforced=bool(feat and feat.enforced),
        scope=o.scope.value, target_id=o.target_id,
        target_label=_target_label(db, o.scope, o.target_id),
        enabled=o.enabled, reason=o.reason, student_message=o.student_message,
        set_by=(setter.name or setter.email) if setter else None,
        set_at=o.set_at, expires_at=o.expires_at,
        students_affected=_students_affected(db, o.scope, o.target_id),
    )


@router.get("/features", response_model=list[OverrideOut])
def list_overrides(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[OverrideOut]:
    require_governance(db, session)
    rows = db.scalars(select(FeatureOverride).order_by(FeatureOverride.set_at.desc())).all()
    return [_override_out(db, o) for o in rows]


@router.put("/features", response_model=OverrideOut)
def set_override(
    body: OverrideIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> OverrideOut:
    """Switch a feature off (or back on) at one rung. Upsert on the unique key.

    `enabled=True` is a real override, not a deletion: switching the voice
    interviewer off for a specialization and back on for one student inside it is
    two rows, and deleting the broader one would turn it on for the other
    eighty-five. Use DELETE to remove a rule entirely.
    """
    require_governance(db, session)
    reason = _reason(body.reason)
    # A SWITCH THAT DOES NOTHING MUST NOT BE SETTABLE (B2.2). Refused in both
    # directions, not only "off": an `enabled=True` row on an unwired key is
    # equally a promise this API does not keep, it is indistinguishable on the
    # screen from a rule that is doing something, and `enabled=True` is the
    # default state anyway — so nothing is lost by refusing it and a stale
    # client cannot write a row that reads as enforcement.
    #
    # 422 rather than 403: the caller holds the capability and the request is
    # well-formed; it is the FEATURE that cannot accept a rule yet. A 403 here
    # would send an admin looking for a grant that would change nothing.
    feature = FEATURES_BY_KEY[body.feature]
    if not feature.enforced:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"'{feature.label}' is not wired to anything yet, so a rule here would "
                "be recorded and ignored. It cannot be switched until the API enforces it."
            ),
        )
    if _target_label(db, body.scope, body.target_id).startswith("(removed"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That target does not exist at the level given.",
        )
    existing = db.scalar(select(FeatureOverride).where(
        FeatureOverride.feature == body.feature,
        FeatureOverride.scope == body.scope,
        FeatureOverride.target_id == body.target_id,
    ))
    before = None
    if existing is None:
        row = FeatureOverride(
            feature=body.feature, scope=body.scope, target_id=body.target_id,
            enabled=body.enabled, reason=reason, set_by_user_id=session["userId"],
            expires_at=body.expires_at, student_message=body.student_message,
        )
        db.add(row)
        db.flush()
    else:
        before = {
            "enabled": existing.enabled,
            "reason": existing.reason,
            "student_message": existing.student_message,
        }
        existing.enabled = body.enabled
        existing.reason = reason
        existing.set_by_user_id = session["userId"]
        existing.set_at = _now()
        existing.expires_at = body.expires_at
        existing.student_message = body.student_message
        row = existing
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="feature_override", entity_id=row.id,
        action="FEATURE_ENABLED" if body.enabled else "FEATURE_DISABLED",
        before=before,
        after={
            "feature": body.feature, "scope": body.scope.value, "target_id": body.target_id,
            "enabled": body.enabled, "reason": reason,
            # On the trail because it is the sentence a student was shown, and
            # "what were they told" is a question somebody asks afterwards.
            "student_message": body.student_message,
            "students_affected": _students_affected(db, body.scope, body.target_id),
        },
        event_type="governance.feature.set", payload={"feature": body.feature},
    )
    db.commit()
    db.refresh(row)
    return _override_out(db, row)


@router.delete("/features/{override_id}", status_code=status.HTTP_204_NO_CONTENT)
def clear_override(
    override_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    """Remove the rule entirely, so the next rung up decides again."""
    require_governance(db, session)
    row = db.get(FeatureOverride, override_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Override not found.")
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="feature_override", entity_id=row.id, action="FEATURE_CLEARED",
        before={"feature": row.feature, "scope": row.scope.value, "enabled": row.enabled},
        after=None, event_type="governance.feature.cleared", payload={"feature": row.feature},
    )
    db.delete(row)
    db.commit()
    from fastapi import Response

    return Response(status_code=status.HTTP_204_NO_CONTENT)
