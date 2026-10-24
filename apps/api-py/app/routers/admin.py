"""Main Admin — the institutional write layer.

WHY THIS MODULE EXISTS. Before it, nothing in the running application could
create a College, a Department or a Cohort, seat a student in one, or write
`users.department` / `users.designation`. All of it was CLI-and-seed only, and
`app/seed.py` refuses to run when ENV=prod — so on a production host no cohort
could exist at all, and four of the five fields on the student's locked profile
card could not be filled by any means short of direct SQL.

THE HIERARCHY IT MAINTAINS:

    College -> Department -> Cohort -> Student.cohort_id

"Batch" on screen is `Cohort` here. The mapping is deliberate and is explained
once in docs/institutional-spine-build-log.md.

ARCHIVE, NEVER DELETE. There is no DELETE endpoint in this file. Status moves
ACTIVE <-> ARCHIVED, which is what the design's console does and what the
foreign keys enforce anyway: the database refuses to delete a college that still
has departments, or a cohort that still has students. A graduated student's
record must still name the college they attended.

SCHEMA NAMING. Cohort responses here are `AdminCohortOut`, not `CohortOut`.
Two different `CohortOut` classes already exist with different shapes
(`console.py:116` and `badge_verification.py:429`) — the same collision this
codebase already has for `LeaderboardOut`, where the payload depends on which
URL you happened to hit. A third would make it worse, so this one is named for
its surface.

CAPABILITY. `admin.institution` (the Main Admin by baseline, a faculty member
only when granted in Governance), matching every other admin surface
including `voice_platform/api/admin.py`. A narrower Main-Admin-only gate belongs
with the capability work, not here — inventing a second gate now would mean two
answers to "who may administer", which is exactly the shape of bug rule 2 exists
to prevent.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, ClassVar, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import account_links, batch_labels
from ..config import settings
from ..db import get_db
from ..identity import get_current_session
from ..models.cohort import Cohort
from ..models import institution as institution_model
from ..models.institution import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
    HierarchyLevel,
)
from ..models.job import DegreeLevel
from ..models.user import Role, Student, User
from ..architecture_events import record_change
from ..governance import require_capability
from ..models.governance import (
    CAPABILITIES_BY_KEY,
    REVIEW_AFTER_DAYS,
    CapabilityGrant,
    ScopeLevel,
    SubjectKind,
)
from ..institution_domains import domain_problem, normalise_domain
# B1.3 appoints college admins by writing capability GRANTS, so it answers to
# the gate that owns grants rather than to this module's `admin.institution`.
# `require_governance` and `_reason` are imported from the router that
# declares them — a second reason floor with a different sentence, or a
# second spelling of "who may hand out access", is the drift these two names
# exist to prevent. (`.governance` here is app/routers/governance.py; the
# module `..governance` two lines above is the resolver. The collision is
# pre-existing and deliberate in this codebase.)
from .governance import _reason, initial_approval_state, require_governance
from .mentor import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])

#: The statuses this surface will accept. A plain allowlist rather than an enum,
#: for the reason app/models/voice_platform.py sets out: a new status should be
#: a data change, not a CREATE TYPE migration.
_SETTABLE_STATUSES = {STATUS_ACTIVE, STATUS_ARCHIVED}


class PatchModel(BaseModel):
    """Base for every PATCH body on this surface. Rejects an explicit `null`
    for a column the database will not accept one for.

    THE BUG THIS FIXES. Every field on a PATCH body is `X | None = None` so
    that omitting it means "leave it alone", and the handler reads
    `model_dump(exclude_unset=True)` to tell omitted from sent. But
    `exclude_unset` is not `exclude_none`: `{"name": null}` is SENT, so it
    survives into the dict, and `setattr(college, "name", None)` then reaches
    a NOT NULL column and comes back as an unhandled IntegrityError — a 500,
    with a Postgres constraint name in the response.

    `{"name": null}` is exactly what a form sends when someone clears a field
    and saves, so this was one cleared input away from a 500 on all three
    PATCH endpoints. The distinction the wire needs is three-way — omitted /
    a value / explicitly null — and only the third is an error, because these
    columns are NOT NULL. Subclasses list those columns in NON_NULLABLE; a
    genuinely nullable field (campus, contact) is left out and may be nulled
    to clear it, which is how those fields are cleared.
    """

    #: Field names that must not be set to null. Named on each subclass so the
    #: list sits next to the fields it describes.
    NON_NULLABLE: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _refuse_explicit_null(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        offenders = sorted(
            name for name in cls.NON_NULLABLE if name in data and data[name] is None
        )
        if offenders:
            raise ValueError(
                "These fields cannot be cleared: "
                + ", ".join(offenders)
                + ". Omit a field to leave it unchanged, or send a new value."
            )
        return data


def _as_utc_midnight(value: date) -> datetime:
    """A calendar date as a timezone-aware datetime.

    `cohorts.start_date` / `end_date` are DateTime(timezone=True) — they predate
    this surface and are not worth a migration to change. The API speaks `date`
    because entry and completion are calendar facts, not instants, and the
    conversion happens here at the edge rather than in four call sites.
    """
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


# ----------------------------------------------------------------- colleges --


#: Stripped BEFORE the length check (2026-10-13). Every create here used to
#: check `min_length=1` and then call `.strip()`, so "   " passed and was stored
#: as "": a college, department, course, specialization or batch with no code
#: or no name, drawn as an empty option on the public application form, and a
#: second one refused with "A college with code  already exists.". PATCH did not
#: strip at all. One type per bound, so every rung refuses blank the same way.
_Text32 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
_Text64 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
_Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CollegeOut(BaseModel):
    id: str
    code: str
    name: str
    campus: str | None
    contact: str | None
    status: str
    department_count: int
    #: The addresses this college will admit an applicant on (B1.1). Empty means
    #: the deployment's own list applies — see app/institution_domains.py — so
    #: the screen renders it as "falls back to the deployment list", never as
    #: "nobody may join".
    email_domains: list[str] = []


class CollegeIn(BaseModel):
    code: _Text32
    name: _Text200
    campus: str | None = None
    contact: str | None = None
    email_domains: list[str] | None = None


class CollegePatchIn(PatchModel):
    """Every field optional: only what is sent is changed.

    `status` is here rather than on a separate /archive endpoint because archive
    and restore are the same operation in opposite directions, and a dedicated
    verb for each invites them to drift apart.
    """

    # campus and contact are nullable columns — clearing them is legitimate and
    # is how they are cleared. code, name and status are NOT NULL. See PatchModel.
    NON_NULLABLE: ClassVar[frozenset[str]] = frozenset({"code", "name", "status"})

    code: _Text32 | None = None
    name: _Text200 | None = None
    campus: str | None = None
    contact: str | None = None
    status: str | None = None
    #: NOT in NON_NULLABLE even though the column is NOT NULL: the list is
    #: emptied by sending [], and `null` is refused by PatchModel like any other
    #: non-nullable field. Emptying it restores the deployment fallback, which
    #: is a real thing an admin may want and is not the same as sending null.
    email_domains: list[str] | None = None


def _row_snapshot(row: object | None) -> dict | None:
    """The row's own columns, JSON-safe, for an audit before/after image."""
    if row is None:
        return None
    out: dict = {}
    for col in row.__table__.columns:  # type: ignore[attr-defined]
        value = getattr(row, col.key, None)
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        elif hasattr(value, "value"):  # an enum member
            value = value.value
        elif isinstance(value, (list, tuple)):
            value = list(value)
        out[col.key] = value
    return out


def _audit_institution(
    db: Session, session: dict, request: Request, entity_type: str, entity_id: str,
    action: str, before: dict | None, after: dict | None,
) -> None:
    """One audit row for a change to the institution tree (2026-10-13).

    Until this, creating, editing or archiving a college, department, course,
    specialization or batch, and seating a student in a batch, wrote nothing to
    `audit_events`: only `appoint_college_admin` in this module did. Those are
    the acts that decide which college a student belongs to, and through it
    which faculty may read them, so "who moved this, and when" must be on the
    trail the console already reads. Flushed by the caller's commit, so the row
    and the change land together.
    """
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type=entity_type, entity_id=entity_id, action=action,
        before=before, after=after,
        event_type=f"institution.{entity_type}.{action.lower()}",
        payload={"id": entity_id},
    )


def _clean_domains(values: list[str] | None) -> list[str]:
    """Normalise and de-duplicate a domain list, keeping the order typed.

    `@BGSCET.ac.in` and `bgscet.ac.in ` are the same fence and must not both be
    stored, because the comparison in app/institution_domains.py is an exact
    set membership and a stray `@` would silently admit nobody on that domain.
    An empty list is preserved: it means "fall back to the deployment list",
    which is a real setting and not a missing one.
    """
    seen: list[str] = []
    for value in values or []:
        domain = normalise_domain(value)
        if not domain:
            continue
        # A malformed entry matches no address and says nothing; a public mail
        # provider matches everybody's (2026-10-13). Both are refused in words.
        problem = domain_problem(domain)
        if problem is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
        if domain not in seen:
            seen.append(domain)
    return seen


def _college_out(db: Session, college: College) -> CollegeOut:
    count = db.scalar(
        select(func.count()).select_from(Department).where(Department.college_id == college.id)
    )
    return CollegeOut(
        id=college.id,
        code=college.code,
        name=college.name,
        campus=college.campus,
        contact=college.contact,
        email_domains=list(college.email_domains or []),
        status=college.status,
        department_count=int(count or 0),
    )


@router.get("/colleges", response_model=list[CollegeOut])
def list_colleges(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[CollegeOut]:
    """Every college, archived ones included — the console shows and un-archives them."""
    require_capability(db, session, "admin.institution")
    rows = db.scalars(select(College).order_by(College.name)).all()
    return [_college_out(db, c) for c in rows]


@router.post("/colleges", response_model=CollegeOut, status_code=status.HTTP_201_CREATED)
def create_college(
    body: CollegeIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CollegeOut:
    require_capability(db, session, "admin.institution")
    code = body.code.strip().upper()
    if db.scalar(select(College).where(College.code == code)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A college with code {code} already exists.",
        )
    college = College(
        code=code,
        name=body.name.strip(),
        campus=body.campus,
        contact=body.contact,
        email_domains=_clean_domains(body.email_domains),
        created_by_user_id=session["userId"],
    )
    db.add(college)
    db.flush()
    _audit_institution(db, session, request, "college", college.id, "CREATED", None, _row_snapshot(college))
    db.commit()
    db.refresh(college)
    return _college_out(db, college)


@router.patch("/colleges/{college_id}", response_model=CollegeOut)
def update_college(
    college_id: str,
    body: CollegePatchIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CollegeOut:
    require_capability(db, session, "admin.institution")
    college = db.get(College, college_id)
    before = _row_snapshot(college)
    if college is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="College not found.")
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in _SETTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"status must be one of {sorted(_SETTABLE_STATUSES)}.",
        )
    if "code" in fields and fields["code"]:
        fields["code"] = fields["code"].strip().upper()
        clash = db.scalar(
            select(College).where(College.code == fields["code"], College.id != college_id)
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A college with code {fields['code']} already exists.",
            )
    if "email_domains" in fields:
        fields["email_domains"] = _clean_domains(fields["email_domains"])
    for field, value in fields.items():
        setattr(college, field, value)
    db.flush()
    _audit_institution(db, session, request, "college", college.id, "UPDATED", before, _row_snapshot(college))
    db.commit()
    db.refresh(college)
    return _college_out(db, college)


# ------------------------------------------------------- college admins (B1.3) --
#
# THERE IS NO SECOND MAIN ADMIN, AND THIS IS NOT ONE.
#
# REEP has exactly one office account by rule, and `app.grant_access`'s
# `_refuse_second_main_admin` is what keeps it that way. Nothing in this section
# touches it, changes anybody's `users.role`, or mints an account: a "college
# admin" here is a FACULTY account (role MENTOR, unchanged) holding a set of
# `admin.*` capability grants SCOPED to one college. That is the whole idea —
# the function is the grant, the role is the identity, and the second college a
# deployment onboards gets somebody who runs it without getting the keys to the
# first one.
#
# WHY AN ENDPOINT RATHER THAN "USE GOVERNANCE ELEVEN TIMES". Appointing one is
# eleven grants, all with the same scope and the same reason, and eleven
# separate acts on the Governance screen is eleven chances to hold the set
# wrong: ten of eleven is a person whose console half works, and a twelfth key
# added by mistake is `admin.governance` — the one that hands access on. One
# call, one reason, one audit trail, a fixed list in code.
#
# WHAT IS DELIBERATELY NOT IN THE SET is as much of the decision as what is:
# `admin.governance` (a college admin must not be able to appoint anybody,
# including themselves, to anything — Governance stays the Main Admin's and its
# deputy's), `admin.interview_audio` (a recording is a named student's voice and
# is its own decision every time, which is why it is the one key the Main Admin
# holds and grants separately) and every `mentor.*` key (a college admin is not
# a faculty member of that college's students — the same reasoning
# `_FACULTY_ONLY` applies to the Main Admin). `ui.console_v2` was a fourth, kept
# out as "a rendering preview, not a function"; Phase 5 deleted the key itself.
#
# `admin.interviews` IS NOW IN THE CATALOGUE and is in the set below (Phase 4c,
# B6.1/B6.4). It was listed in 04-backend-changes.md for this set for months
# while the key did not exist, and this comment used to say so — the note is
# kept in the past tense because the rule it records is the one that matters:
# a name in this tuple with no catalogue entry is a KeyError in
# `CAPABILITIES_BY_KEY[key]` below, thrown in front of whoever is appointing a
# college admin. The key, its first `require_capability` call site
# (`app/routers/interview_policy.py`) and this line landed in ONE commit, the
# same rule `admin.imports` was held to. It carries PII, so it is one of the
# keys that lands `pending_approval` when a DEPUTY appoints (the Main Admin's
# appointment is live at once). `admin.interview_questions` is the other
# interview key and stays: the two are different decisions — who may edit the
# questions the interviewer asks, and who may set the college's policy and read
# the records. The appointment endpoint is idempotent, so re-running it on an
# existing college admin fills in whatever is missing.
#
# `admin.imports` (B8.1) IS in the set, by the owner's decision, and it carries
# PII — so when a DEPUTY appoints, it is one of the seven of thirteen that land
# `pending_approval` and hold nothing until a different `admin.governance`
# holder approves them; appointed by the Main Admin, every one of the thirteen
# is live at once (`initial_approval_state`, 2026-09-16). That is the state
# `CollegeAdminGrantOut.approval_state` reports per key, for exactly this
# reason.

#: The functions that make up "runs this college". Ordered as the console's
#: sidebar orders them, so the screen and this list can be read against each
#: other by eye.
COLLEGE_ADMIN_CAPABILITIES: tuple[str, ...] = (
    "admin.analytics",
    "admin.registrations",
    "admin.students",
    # The roster's read side (2026-09-17): a college admin who may edit a row
    # may open the record behind it. Granted beside `admin.students` rather
    # than folded into it, so the office can still hand ONLY the read to a
    # faculty member in Governance.
    "admin.student_records",
    "admin.mentors",
    "admin.institution",
    "admin.catalogue",
    "admin.jobs",
    "admin.placement",
    "admin.swoc",
    "admin.interview_questions",
    "admin.interviews",
    "admin.imports",
    "admin.exports",
)


class CollegeAdminGrantOut(BaseModel):
    capability: str
    label: str
    #: `active` or `pending_approval`. Appointed by the Main Admin every key is
    #: live at once; appointed by a DEPUTY, the seven of thirteen that carry PII
    #: wait for a second signature (B2.4), so that college admin is PART live.
    #: Reporting the state per key rather than one boolean for the set is the
    #: difference between "approve these seven" and "why does half my console
    #: 403".
    approval_state: str
    grant_id: str


class CollegeAdminOut(BaseModel):
    user_id: str
    name: str
    email: str
    #: Where the account itself is filed. An account filed under ANOTHER college
    #: can still be appointed here — a shared registrar, a founder's office —
    #: and the screen should be able to show that rather than hide it.
    department_id: str | None
    capabilities: list[CollegeAdminGrantOut]
    #: Keys from the set this person does NOT hold for this college. Normally
    #: empty; non-empty after the set grows in code, or after somebody revoked
    #: one key in Governance. A list that only ever showed what is held could
    #: not tell those two apart from a complete appointment.
    missing: list[str]


class CollegeAdminIn(BaseModel):
    user_id: str
    reason: str


def _college_or_404(db: Session, college_id: str) -> College:
    college = db.get(College, college_id)
    if college is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="College not found.")
    return college


def _college_admin_rows(db: Session, college_id: str) -> list[CollegeAdminOut]:
    """Everybody holding any of the set, scoped to this college.

    ANY rather than ALL, on purpose. A person holding nine of the thirteen is a
    college admin whose appointment is incomplete or partly revoked, and the one
    screen that could tell the office that is this one; requiring the full set
    would render them as nobody and leave nine live grants invisible here.
    """
    rows = db.execute(
        select(CapabilityGrant, User)
        .join(User, User.id == CapabilityGrant.subject_user_id)
        .where(
            CapabilityGrant.subject_kind == SubjectKind.USER,
            CapabilityGrant.capability.in_(COLLEGE_ADMIN_CAPABILITIES),
            CapabilityGrant.scope_level == ScopeLevel.COLLEGE,
            CapabilityGrant.scope_id == college_id,
            CapabilityGrant.revoked_at.is_(None),
        )
        .order_by(User.name)
    ).all()
    by_user: dict[str, tuple[User, list[CapabilityGrant]]] = {}
    for grant, user in rows:
        by_user.setdefault(user.id, (user, []))[1].append(grant)
    out: list[CollegeAdminOut] = []
    for user, grants in by_user.values():
        held = {g.capability for g in grants}
        out.append(
            CollegeAdminOut(
                user_id=user.id,
                name=user.name,
                email=user.email,
                department_id=user.department_id,
                capabilities=[
                    CollegeAdminGrantOut(
                        capability=g.capability,
                        label=CAPABILITIES_BY_KEY[g.capability].label,
                        approval_state=g.approval_state,
                        grant_id=g.id,
                    )
                    for g in sorted(grants, key=lambda g: g.capability)
                ],
                missing=[k for k in COLLEGE_ADMIN_CAPABILITIES if k not in held],
            )
        )
    return out


@router.get("/colleges/{college_id}/admins", response_model=list[CollegeAdminOut])
def list_college_admins(
    college_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[CollegeAdminOut]:
    """Who runs this college, and with which of the thirteen functions.

    `require_governance`: this reads WHO HOLDS WHAT, which is the Governance
    screen's subject and not the institution screen's. Gating it on
    `admin.institution` — the capability the rest of this module uses — would
    let anyone who may rename a department read the access map.
    """
    require_governance(db, session)
    _college_or_404(db, college_id)
    return _college_admin_rows(db, college_id)


@router.post(
    "/colleges/{college_id}/admins",
    response_model=CollegeAdminOut,
    status_code=status.HTTP_201_CREATED,
)
def appoint_college_admin(
    college_id: str,
    body: CollegeAdminIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CollegeAdminOut:
    """Appoint a faculty account to run one college. One reason, eleven grants.

    IDEMPOTENT BY THE SAME RULE `POST /governance/grants` USES: a key already
    held live for this college is left exactly as it is, rather than granted
    again. Two live rows for one pair make revocation a question of which one —
    and here it would also be a question of which reason was the real one.

    THE SECOND-APPROVAL RULE IS NOT BYPASSED, AND IT IS THE SAME RULE AS
    `POST /governance/grants`'S. `initial_approval_state` decides every row:
    appointed by the Main Admin, all thirteen are live at once (2026-09-16);
    appointed by a deputy, the seven that are `carries_pii` are written
    `pending_approval` and hold nothing until a different holder of
    `admin.governance` approves each in Governance (B2.4). That is the point of
    appointing somebody through grants rather than through a role: a role would
    have handed over the roster the moment it was typed, whoever typed it.

    THIS IS THE SECOND WRITER OF `capability_grants` AND THAT IS A REAL COST.
    `POST /api/admin/governance/grants` is the first, and it cannot be reused
    because `GrantIn` has no scope field — it writes programme-wide rows only.
    The two are kept in step by sharing everything that decides a row
    (`initial_approval_state`, `REVIEW_AFTER_DAYS`, `_reason`, `role_at_grant`);
    the change
    that would delete this duplication outright is a `scope` on `GrantIn`, at
    which point this endpoint becomes a loop over that one.
    """
    require_governance(db, session)
    college = _college_or_404(db, college_id)
    reason = _reason(body.reason)

    user = db.get(User, body.user_id)
    if user is None or user.role not in (Role.MENTOR, Role.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "A college admin is a faculty account holding scoped functions. "
                "That id is not a staff account."
            ),
        )

    now = datetime.now(timezone.utc)
    review_at = now + timedelta(days=REVIEW_AFTER_DAYS)
    created: list[CapabilityGrant] = []
    def already_live(key: str) -> bool:
        """Is there already a row for this pair that has not ended?

        A `pending_approval` ROW COUNTS, and this is the one place it was easy
        to get wrong. The obvious implementation asks `granted_reaches`, which
        is the reader `require_capability` uses — and that reader filters on
        `approval_state == active`, precisely so a grant awaiting a second
        signature holds nothing. Appointed by a deputy, seven of these thirteen
        carry PII and are written pending, so a second appointment of the same
        person wrote seven DUPLICATE rows, each awaiting its own approval, and
        the approver then had to pick one. Exactly the ambiguity
        `create_grants.already_live` documents, reached through the one door
        that does not yet grant anything. This asks the table instead: live
        means not revoked and not expired, whatever it is waiting for.
        """
        return db.scalar(
            select(CapabilityGrant.id).where(
                CapabilityGrant.capability == key,
                CapabilityGrant.subject_kind == SubjectKind.USER,
                CapabilityGrant.subject_user_id == user.id,
                CapabilityGrant.revoked_at.is_(None),
                or_(
                    CapabilityGrant.expires_at.is_(None),
                    CapabilityGrant.expires_at > now,
                ),
                or_(
                    # A programme-wide grant already covers this college, and a
                    # narrower duplicate of it would only be a second row to
                    # revoke later.
                    CapabilityGrant.scope_level.is_(None),
                    (CapabilityGrant.scope_level == ScopeLevel.COLLEGE)
                    & (CapabilityGrant.scope_id == college.id),
                ),
            )
        ) is not None

    for key in COLLEGE_ADMIN_CAPABILITIES:
        if already_live(key):
            continue
        cap = CAPABILITIES_BY_KEY[key]
        created.append(
            CapabilityGrant(
                capability=key,
                subject_kind=SubjectKind.USER,
                subject_user_id=user.id,
                scope_level=ScopeLevel.COLLEGE,
                scope_id=college.id,
                reason=reason,
                granted_by_user_id=session["userId"],
                approval_state=initial_approval_state(cap, session),
                review_at=review_at,
                role_at_grant=user.role.value,
            )
        )
    for grant in created:
        db.add(grant)
    db.flush()

    # ONE audit row per grant, and one `batch_size` on each, exactly as
    # `create_grants` writes them: "why does this person hold Exports for
    # BGSCET" must be answerable from the row that named them, not from a
    # sibling row they are not mentioned in.
    for grant in created:
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="capability_grant", entity_id=grant.id, action="GRANTED",
            before=None,
            after={
                "capability": grant.capability,
                "scope_level": ScopeLevel.COLLEGE.value,
                "scope_id": college.id,
                "college_code": college.code,
                "subject_kind": SubjectKind.USER.value,
                "subject_id": user.id,
                "reason": reason,
                "approval_state": grant.approval_state,
                "review_at": review_at.isoformat(),
                "role_at_grant": grant.role_at_grant,
                "appointment": "college_admin",
                "batch_size": len(created),
            },
            event_type="governance.college_admin.appointed",
            payload={"capability": grant.capability, "college_id": college.id},
        )
    db.commit()

    rows = [r for r in _college_admin_rows(db, college.id) if r.user_id == user.id]
    if not rows:
        # Every key was already held programme-wide: a real outcome, and the
        # honest answer is the person with no COLLEGE-scoped rows rather than a
        # 500 over an empty list.
        return CollegeAdminOut(
            user_id=user.id, name=user.name, email=user.email,
            department_id=user.department_id, capabilities=[],
            missing=list(COLLEGE_ADMIN_CAPABILITIES),
        )
    return rows[0]


# -------------------------------------------------------------- departments --


class DepartmentOut(BaseModel):
    id: str
    college_id: str
    code: str
    name: str
    head: str | None
    status: str
    cohort_count: int


class DepartmentIn(BaseModel):
    code: _Text32
    name: _Text200
    head: str | None = None


class DepartmentPatchIn(PatchModel):
    # `head` is a nullable column: clearing it is how a department records that
    # it currently has no head. See PatchModel.
    NON_NULLABLE: ClassVar[frozenset[str]] = frozenset({"code", "name", "status"})

    code: _Text32 | None = None
    name: _Text200 | None = None
    head: str | None = None
    status: str | None = None


def _department_out(db: Session, department: Department) -> DepartmentOut:
    count = db.scalar(
        select(func.count()).select_from(Cohort).where(Cohort.department_id == department.id)
    )
    return DepartmentOut(
        id=department.id,
        college_id=department.college_id,
        code=department.code,
        name=department.name,
        head=department.head,
        status=department.status,
        cohort_count=int(count or 0),
    )


class DepartmentPickerOut(BaseModel):
    """A department with the college it belongs to, for a one-control picker.

    The per-college list below answers "what is inside this college", which is
    what the institution tree needs. Filing a faculty member is the other
    question — "which department, anywhere" — and answering it from the tree
    would mean the form asked for a college first, purely because of how the API
    happened to be shaped. The college still travels, because two colleges may
    each have a CSE and the label has to say which one.
    """

    id: str
    code: str
    name: str
    college_id: str
    college_code: str
    college_name: str
    #: "Computer Science · BGSCET" — one line, already disambiguated.
    label: str


@router.get("/departments", response_model=list[DepartmentPickerOut])
def list_all_departments(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[DepartmentPickerOut]:
    """Every department in every college, ordered college-then-department."""
    require_capability(db, session, "admin.institution")
    rows = db.execute(
        select(Department.id, Department.code, Department.name, College.id, College.code, College.name)
        .join(College, College.id == Department.college_id)
        .order_by(College.name, Department.name)
    ).all()
    return [
        DepartmentPickerOut(
            id=did,
            code=dcode,
            name=dname,
            college_id=cid,
            college_code=ccode,
            college_name=cname,
            label=f"{dname} · {ccode or cname}",
        )
        for did, dcode, dname, cid, ccode, cname in rows
    ]


@router.get("/colleges/{college_id}/departments", response_model=list[DepartmentOut])
def list_departments(
    college_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[DepartmentOut]:
    require_capability(db, session, "admin.institution")
    if db.get(College, college_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="College not found.")
    rows = db.scalars(
        select(Department).where(Department.college_id == college_id).order_by(Department.name)
    ).all()
    return [_department_out(db, d) for d in rows]


@router.post(
    "/colleges/{college_id}/departments",
    response_model=DepartmentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_department(
    college_id: str,
    body: DepartmentIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> DepartmentOut:
    require_capability(db, session, "admin.institution")
    if db.get(College, college_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="College not found.")
    code = body.code.strip().upper()
    # Unique per college, not globally — two colleges may each run a "CSE".
    clash = db.scalar(
        select(Department).where(Department.college_id == college_id, Department.code == code)
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This college already has a department with code {code}.",
        )
    department = Department(
        college_id=college_id,
        code=code,
        name=body.name.strip(),
        head=body.head,
        created_by_user_id=session["userId"],
    )
    db.add(department)
    db.flush()
    _audit_institution(db, session, request, "department", department.id, "CREATED", None, _row_snapshot(department))
    db.commit()
    db.refresh(department)
    return _department_out(db, department)


@router.patch("/departments/{department_id}", response_model=DepartmentOut)
def update_department(
    department_id: str,
    body: DepartmentPatchIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> DepartmentOut:
    require_capability(db, session, "admin.institution")
    department = db.get(Department, department_id)
    before = _row_snapshot(department)
    if department is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found.")
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in _SETTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"status must be one of {sorted(_SETTABLE_STATUSES)}.",
        )
    if "code" in fields and fields["code"]:
        fields["code"] = fields["code"].strip().upper()
        clash = db.scalar(
            select(Department).where(
                Department.college_id == department.college_id,
                Department.code == fields["code"],
                Department.id != department_id,
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This college already has a department with code {fields['code']}.",
            )
    for field, value in fields.items():
        setattr(department, field, value)
    db.flush()
    _audit_institution(db, session, request, "department", department.id, "UPDATED", before, _row_snapshot(department))
    db.commit()
    db.refresh(department)
    return _department_out(db, department)


# ---------------------------------------------- the switch, and its readers --


def _required_levels() -> list[HierarchyLevel]:
    """The levels a NEW batch must name today.

    Read through the module attribute at call time, not from an import-time
    binding: `from ..models.institution import HIERARCHY_LEVELS` would freeze a
    copy here, and a test that flips `required` on the model module would then
    be flipping a constant this router never looks at. The flip must be
    observable from exactly one place.
    """
    return [lv for lv in institution_model.HIERARCHY_LEVELS if lv.required]


def _compliance_gap(cohort: Cohort) -> list[HierarchyLevel]:
    """Which required levels this batch does not name. Empty means compliant."""
    return [lv for lv in _required_levels() if getattr(cohort, lv.field) is None]


class HierarchyLevelOut(BaseModel):
    key: str
    label: str
    field: str
    required: bool


@router.get("/hierarchy/levels", response_model=list[HierarchyLevelOut])
def hierarchy_levels(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[HierarchyLevelOut]:
    """What the batch form must ask for, and which of it is mandatory.

    The console does NOT hardcode `Validators.required`; it builds the
    controls from this response. One constant, two readers — the pydantic
    validator on AdminCohortIn and this — so "both sides agree" is a property
    of the call graph rather than a promise in a comment. Flip `required` in
    HIERARCHY_LEVELS and the form grows a required validator on its own.
    """
    require_capability(db, session, "admin.institution")
    return [HierarchyLevelOut(**lv._asdict()) for lv in institution_model.HIERARCHY_LEVELS]


# ----------------------------------------------------- academic courses --
# UI: "Course". Prefixed in code because app/models/course.py's `Course` is a
# taught subject (22MBA11) and `CourseOut` is already student.py's. See the
# model docstring.


class AcademicCourseOut(BaseModel):
    id: str
    department_id: str
    code: str
    name: str
    duration_months: int | None
    status: str
    specialization_count: int


class AcademicCourseIn(BaseModel):
    code: _Text32
    name: _Text200
    duration_months: int | None = Field(default=None, ge=1, le=120)


class AcademicCoursePatchIn(PatchModel):
    NON_NULLABLE: ClassVar[frozenset[str]] = frozenset({"code", "name", "status"})

    code: _Text32 | None = None
    name: _Text200 | None = None
    duration_months: int | None = Field(default=None, ge=1, le=120)
    status: str | None = None


def _academic_course_out(db: Session, course: AcademicCourse) -> AcademicCourseOut:
    count = db.scalar(
        select(func.count())
        .select_from(AcademicSpecialization)
        .where(AcademicSpecialization.course_id == course.id)
    )
    return AcademicCourseOut(
        id=course.id,
        department_id=course.department_id,
        code=course.code,
        name=course.name,
        duration_months=course.duration_months,
        status=course.status,
        specialization_count=int(count or 0),
    )


@router.get("/departments/{department_id}/academic-courses", response_model=list[AcademicCourseOut])
def list_academic_courses(
    department_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AcademicCourseOut]:
    require_capability(db, session, "admin.institution")
    if db.get(Department, department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found.")
    rows = db.scalars(
        select(AcademicCourse)
        .where(AcademicCourse.department_id == department_id)
        .order_by(AcademicCourse.code)
    ).all()
    return [_academic_course_out(db, c) for c in rows]


@router.post(
    "/departments/{department_id}/academic-courses",
    response_model=AcademicCourseOut,
    status_code=status.HTTP_201_CREATED,
)
def create_academic_course(
    department_id: str,
    body: AcademicCourseIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AcademicCourseOut:
    require_capability(db, session, "admin.institution")
    if db.get(Department, department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found.")
    code = body.code.strip().upper()
    # Unique per department — two departments may each run an "MBA".
    clash = db.scalar(
        select(AcademicCourse).where(
            AcademicCourse.department_id == department_id, AcademicCourse.code == code
        )
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This department already has a course with code {code}.",
        )
    course = AcademicCourse(
        department_id=department_id,
        code=code,
        name=body.name.strip(),
        duration_months=body.duration_months,
        created_by_user_id=session["userId"],
    )
    db.add(course)
    db.flush()
    _audit_institution(db, session, request, "academic_course", course.id, "CREATED", None, _row_snapshot(course))
    db.commit()
    db.refresh(course)
    return _academic_course_out(db, course)


@router.patch("/academic-courses/{course_id}", response_model=AcademicCourseOut)
def update_academic_course(
    course_id: str,
    body: AcademicCoursePatchIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AcademicCourseOut:
    require_capability(db, session, "admin.institution")
    course = db.get(AcademicCourse, course_id)
    before = _row_snapshot(course)
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found.")
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in _SETTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"status must be one of {sorted(_SETTABLE_STATUSES)}.",
        )
    if "code" in fields and fields["code"]:
        code = fields["code"].strip().upper()
        clash = db.scalar(
            select(AcademicCourse).where(
                AcademicCourse.department_id == course.department_id,
                AcademicCourse.code == code,
                AcademicCourse.id != course.id,
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This department already has a course with code {code}.",
            )
        fields["code"] = code
    if "name" in fields and fields["name"]:
        fields["name"] = fields["name"].strip()
    for field, value in fields.items():
        setattr(course, field, value)
    db.flush()
    _audit_institution(db, session, request, "academic_course", course.id, "UPDATED", before, _row_snapshot(course))
    db.commit()
    db.refresh(course)
    return _academic_course_out(db, course)


# ---------------------------------------------- academic specializations --
# UI: "Specialization". Prefixed because the bare word is already booked three
# times — the interview matrix's tracks, PlatformSpecialization, and the
# interview_sessions column — and `SpecializationOut` is the voice platform's.


class AcademicSpecializationOut(BaseModel):
    id: str
    course_id: str
    code: str
    name: str
    status: str
    cohort_count: int


class AcademicSpecializationIn(BaseModel):
    code: _Text32
    name: _Text200


class AcademicSpecializationPatchIn(PatchModel):
    NON_NULLABLE: ClassVar[frozenset[str]] = frozenset({"code", "name", "status"})

    code: _Text32 | None = None
    name: _Text200 | None = None
    status: str | None = None


def _academic_specialization_out(
    db: Session, spec: AcademicSpecialization
) -> AcademicSpecializationOut:
    count = db.scalar(
        select(func.count()).select_from(Cohort).where(Cohort.specialization_id == spec.id)
    )
    return AcademicSpecializationOut(
        id=spec.id,
        course_id=spec.course_id,
        code=spec.code,
        name=spec.name,
        status=spec.status,
        cohort_count=int(count or 0),
    )


@router.get(
    "/academic-courses/{course_id}/academic-specializations",
    response_model=list[AcademicSpecializationOut],
)
def list_academic_specializations(
    course_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AcademicSpecializationOut]:
    require_capability(db, session, "admin.institution")
    if db.get(AcademicCourse, course_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found.")
    rows = db.scalars(
        select(AcademicSpecialization)
        .where(AcademicSpecialization.course_id == course_id)
        .order_by(AcademicSpecialization.code)
    ).all()
    return [_academic_specialization_out(db, r) for r in rows]


@router.post(
    "/academic-courses/{course_id}/academic-specializations",
    response_model=AcademicSpecializationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_academic_specialization(
    course_id: str,
    body: AcademicSpecializationIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AcademicSpecializationOut:
    require_capability(db, session, "admin.institution")
    if db.get(AcademicCourse, course_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found.")
    code = body.code.strip().upper()
    clash = db.scalar(
        select(AcademicSpecialization).where(
            AcademicSpecialization.course_id == course_id, AcademicSpecialization.code == code
        )
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This course already has a specialization with code {code}.",
        )
    spec = AcademicSpecialization(
        course_id=course_id,
        code=code,
        name=body.name.strip(),
        created_by_user_id=session["userId"],
    )
    db.add(spec)
    db.flush()
    _audit_institution(db, session, request, "academic_specialization", spec.id, "CREATED", None, _row_snapshot(spec))
    db.commit()
    db.refresh(spec)
    return _academic_specialization_out(db, spec)


@router.patch(
    "/academic-specializations/{specialization_id}", response_model=AcademicSpecializationOut
)
def update_academic_specialization(
    specialization_id: str,
    body: AcademicSpecializationPatchIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AcademicSpecializationOut:
    require_capability(db, session, "admin.institution")
    spec = db.get(AcademicSpecialization, specialization_id)
    before = _row_snapshot(spec)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Specialization not found.")
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in _SETTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"status must be one of {sorted(_SETTABLE_STATUSES)}.",
        )
    if "code" in fields and fields["code"]:
        code = fields["code"].strip().upper()
        clash = db.scalar(
            select(AcademicSpecialization).where(
                AcademicSpecialization.course_id == spec.course_id,
                AcademicSpecialization.code == code,
                AcademicSpecialization.id != spec.id,
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This course already has a specialization with code {code}.",
            )
        fields["code"] = code
    if "name" in fields and fields["name"]:
        fields["name"] = fields["name"].strip()
    for field, value in fields.items():
        setattr(spec, field, value)
    db.flush()
    _audit_institution(db, session, request, "academic_specialization", spec.id, "UPDATED", before, _row_snapshot(spec))
    db.commit()
    db.refresh(spec)
    return _academic_specialization_out(db, spec)


# ------------------------------------------------ the single ancestry writer --


class _Ancestry(NamedTuple):
    department_id: str | None
    course_id: str | None
    specialization_id: str | None


_ANCESTRY_FIELDS = ("department_id", "course_id", "specialization_id")


def _resolve_ancestry(db: Session, intended: dict, sent: set[str]) -> _Ancestry:
    """Walk UP from the deepest level supplied and derive every ancestor.

    THE SPLIT-BRAIN THIS PREVENTS. `cohorts` carries three parent pointers
    (department, course, specialization), which is three chances to disagree,
    and the disagreement is silent — it surfaces as a profile card printing the
    wrong department under the words "verified by Main Admin". So the client
    never sets them independently: it names the deepest level it knows, this
    function follows the real foreign keys upward to fill the rest, and a
    SHALLOWER value the client ALSO sent is CHECKED against the derived one
    rather than stored beside it. A contradiction is a 422 naming both, never
    a silent pick.

    `intended` is the full three-field picture after the request is applied
    (for a PATCH: the row's current values overlaid with what was sent).
    `sent` is which of those keys the client actually supplied — the ones that
    can contradict a derivation. An explicit null on a shallower level while a
    deeper one is set is a contradiction too: "no course" cannot coexist with
    a specialization that belongs to one.
    """
    spec = None
    if intended.get("specialization_id") is not None:
        spec = db.get(AcademicSpecialization, intended["specialization_id"])
        if spec is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Specialization not found."
            )
    course_id = spec.course_id if spec is not None else intended.get("course_id")
    course = None
    if course_id is not None:
        course = db.get(AcademicCourse, course_id)
        if course is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found.")
    department_id = course.department_id if course is not None else intended.get("department_id")
    if department_id is not None and db.get(Department, department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found.")

    derived = _Ancestry(department_id, course_id, spec.id if spec is not None else None)
    for field in ("department_id", "course_id"):
        if field in sent and intended.get(field) != getattr(derived, field):
            deeper = "specialization" if field == "course_id" else "course"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"{field} contradicts the {deeper} you chose, which sits under "
                    f"{field} {getattr(derived, field)}. Clear the {deeper} first, or pick "
                    f"one under the {field.removesuffix('_id')} you want."
                ),
            )
    return derived


# ------------------------------------------------- cohorts (UI: "batches") --


class AdminCohortOut(BaseModel):
    """Named for its surface, NOT `CohortOut`.

    `CohortOut` already exists twice with different shapes — `console.py:116`
    and `badge_verification.py:429`. A third would deepen a collision this
    codebase already suffers from.
    """

    id: str
    department_id: str | None
    course_id: str | None
    specialization_id: str | None
    code: str
    name: str
    batch_label: str
    #: The spine, resolved. A batch is a YEAR (`name`); which course and which
    #: specialization it belongs to are these links, and the console needs the
    #: words to draw them. They were manufactured into `name` until
    #: a4e7c92d1f38 — see `app/batch_labels.py` for why that was one fact
    #: stored twice. NULL where the batch hangs shallower than that rung.
    course_name: str | None
    specialization_name: str | None
    #: The spine and the year put together, by the ONE rule
    #: (`batch_labels.compose`): "General MBA - Finance · 2026-28". Served so
    #: that every screen showing a batch shows the same sentence — the client
    #: composing it itself is how five endpoints each grew their own copy of
    #: `name · batch_label`, which printed the course twice.
    display_label: str
    degree_level: str
    entry_date: date
    expected_completion: date
    student_count: int
    #: Labels of the levels that are required NOW and blank on this row. Empty
    #: means compliant. The console renders each as a `.chip warn` reading
    #: "Needs Course" — text and colour, never colour alone — and the row stays
    #: editable and seatable. Flagged, not quarantined.
    missing_levels: list[str]


class AdminCohortIn(BaseModel):
    code: _Text64
    name: _Text200
    batch_label: _Text64  # e.g. 2024-26
    degree_level: DegreeLevel
    entry_date: date
    expected_completion: date
    # The optional levels. Send the DEEPEST one you know; the ancestors are
    # derived (_resolve_ancestry). Both nullable at the wire; whether either is
    # REQUIRED is decided by the validator below, from HIERARCHY_LEVELS.
    course_id: str | None = None
    specialization_id: str | None = None

    @model_validator(mode="after")
    def _honour_required_levels(self) -> "AdminCohortIn":
        # Reader 1 of the switch. Reader 2 is GET /admin/hierarchy/levels,
        # which the form builds its validators from — so a level flipped to
        # required is refused here AND marked on the form, from one constant.
        missing = [lv.label for lv in _required_levels() if getattr(self, lv.field) is None]
        if missing:
            raise ValueError(
                "These are required when creating a batch: "
                + ", ".join(missing)
                + ". (Set by HIERARCHY_LEVELS in app/models/institution.py.)"
            )
        return self


class AdminCohortPatchIn(PatchModel):
    # campus/contact are nullable columns and stay off this list; these four
    # are NOT NULL, so clearing them is a 422 and not a 500. See PatchModel.
    NON_NULLABLE: ClassVar[frozenset[str]] = frozenset(
        {"name", "batch_label", "entry_date", "expected_completion"}
    )

    name: _Text200 | None = None
    batch_label: _Text64 | None = None
    entry_date: date | None = None
    expected_completion: date | None = None
    # THE MOVE. Without this field, `department_id` had exactly one writer —
    # cohort creation — so every cohort that existed before this feature (the
    # seeded one included) was stranded with department_id NULL and no way to
    # set it. And because list_cohorts is keyed on a department path segment, a
    # NULL-department cohort is invisible to the console entirely: unreachable
    # and unfixable. The migration's own comment said "an admin sets it
    # afterwards" while no code path let them.
    #
    # Nullable on purpose, and it IS in NON_NULLABLE's complement: sending null
    # un-seats the batch from its department, which is the only way to correct
    # a batch filed under the wrong one.
    department_id: str | None = None
    # The optional levels, omit-means-keep. Explicit null clears — but only
    # where that does not WIDEN the compliance gap (update_cohort): a required
    # level may be filled in, never emptied, and a legacy batch missing one may
    # still have everything else corrected.
    course_id: str | None = None
    specialization_id: str | None = None


def _admin_cohort_out(db: Session, cohort: Cohort) -> AdminCohortOut:
    count = db.scalar(
        select(func.count()).select_from(Student).where(Student.cohort_id == cohort.id)
    )
    # `db.get` and not a join: these lists are per department and the batches in
    # one share a handful of courses, so the identity map answers every repeat
    # without a round trip. The count above is already the per-row query here.
    course = db.get(AcademicCourse, cohort.course_id) if cohort.course_id else None
    spec = (
        db.get(AcademicSpecialization, cohort.specialization_id)
        if cohort.specialization_id
        else None
    )
    return AdminCohortOut(
        id=cohort.id,
        department_id=cohort.department_id,
        course_id=cohort.course_id,
        specialization_id=cohort.specialization_id,
        course_name=course.name if course else None,
        specialization_name=spec.name if spec else None,
        display_label=batch_labels.compose(
            course.name if course else None,
            spec.name if spec else None,
            cohort.name,
            cohort.batch_label,
        ),
        missing_levels=[lv.label for lv in _compliance_gap(cohort)],
        code=cohort.code,
        name=cohort.name,
        batch_label=cohort.batch_label,
        degree_level=cohort.degree_level.value,
        entry_date=cohort.start_date.date(),
        expected_completion=cohort.end_date.date(),
        student_count=int(count or 0),
    )


@router.get("/departments/{department_id}/cohorts", response_model=list[AdminCohortOut])
def list_cohorts(
    department_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AdminCohortOut]:
    require_capability(db, session, "admin.institution")
    if db.get(Department, department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found.")
    rows = db.scalars(
        select(Cohort).where(Cohort.department_id == department_id).order_by(Cohort.batch_label)
    ).all()
    return [_admin_cohort_out(db, c) for c in rows]


@router.get("/cohorts/unassigned", response_model=list[AdminCohortOut])
def list_unassigned_cohorts(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AdminCohortOut]:
    """Batches that belong to no department yet.

    WITHOUT THIS ENDPOINT THE CONSOLE CANNOT SEE THEM. Every other cohort read
    is `GET /departments/{id}/cohorts`, keyed on a department, so a cohort with
    department_id NULL matches no path — invisible, and therefore impossible to
    move into a department even now that PATCH accepts one. Every cohort
    predating this feature is in exactly that state, the seeded one included.

    This is the console's inbox for them: list here, then PATCH a department_id
    onto each. Once seated a cohort leaves this list, so an empty response is
    the healthy steady state rather than an error.
    """
    require_capability(db, session, "admin.institution")
    rows = db.scalars(
        select(Cohort).where(Cohort.department_id.is_(None)).order_by(Cohort.batch_label)
    ).all()
    return [_admin_cohort_out(db, c) for c in rows]


@router.get("/cohorts/incomplete", response_model=list[AdminCohortOut])
def list_incomplete_cohorts(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AdminCohortOut]:
    """Batches that do not name every currently-required level.

    Flipping a level to required in HIERARCHY_LEVELS does not touch a single
    row — so the morning after that deploy, every batch created before it is
    non-compliant. This is where they are listed, for the same reason
    /cohorts/unassigned exists: a row the console cannot reach is one it cannot
    fix. An empty response is the healthy steady state, and — because the
    predicate is built from the same tuple — it is honestly empty before the
    flip too, rather than pretending everything is compliant.
    """
    require_capability(db, session, "admin.institution")
    required = _required_levels()
    if not required:
        return []
    rows = db.scalars(
        select(Cohort)
        .where(or_(*(getattr(Cohort, lv.field).is_(None) for lv in required)))
        .order_by(Cohort.batch_label)
    ).all()
    return [_admin_cohort_out(db, c) for c in rows]


@router.post(
    "/departments/{department_id}/cohorts",
    response_model=AdminCohortOut,
    status_code=status.HTTP_201_CREATED,
)
def create_cohort(
    department_id: str,
    body: AdminCohortIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AdminCohortOut:
    """Create a batch under a department.

    Entry date and expected completion live HERE, on the batch — not on the
    student. That is what the design shows, and it is why adding an academic
    year level later costs one table rather than a backfill of every student.
    """
    require_capability(db, session, "admin.institution")
    if db.get(Department, department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found.")
    if body.expected_completion <= body.entry_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Expected completion must be after the entry date.",
        )
    code = body.code.strip().upper()
    if db.scalar(select(Cohort).where(Cohort.code == code)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A batch with code {code} already exists.",
        )
    # The path names the department; the body may name a course and/or a
    # specialization. All three go through the one ancestry writer, so a course
    # under some OTHER department is a 422 here rather than a card that lies.
    sent = {"department_id"} | {f for f in ("course_id", "specialization_id") if getattr(body, f)}
    ancestry = _resolve_ancestry(
        db,
        {
            "department_id": department_id,
            "course_id": body.course_id,
            "specialization_id": body.specialization_id,
        },
        sent,
    )
    cohort = Cohort(
        code=code,
        name=body.name.strip(),
        batch_label=body.batch_label.strip(),
        degree_level=body.degree_level,
        department_id=ancestry.department_id,
        course_id=ancestry.course_id,
        specialization_id=ancestry.specialization_id,
        start_date=_as_utc_midnight(body.entry_date),
        end_date=_as_utc_midnight(body.expected_completion),
    )
    db.add(cohort)
    db.flush()
    _audit_institution(db, session, request, "cohort", cohort.id, "CREATED", None, _row_snapshot(cohort))
    db.commit()
    db.refresh(cohort)
    return _admin_cohort_out(db, cohort)


@router.patch("/cohorts/{cohort_id}", response_model=AdminCohortOut)
def update_cohort(
    cohort_id: str,
    body: AdminCohortPatchIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AdminCohortOut:
    """Edit a batch. Every seated student's locked profile card moves with it —
    which is the point of reading those facts through the join rather than
    copying them onto the student."""
    require_capability(db, session, "admin.institution")
    cohort = db.get(Cohort, cohort_id)
    before = _row_snapshot(cohort)
    if cohort is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found.")
    fields = body.model_dump(exclude_unset=True)
    entry = fields.pop("entry_date", None)
    completion = fields.pop("expected_completion", None)
    new_entry = entry or cohort.start_date.date()
    new_completion = completion or cohort.end_date.date()
    if new_completion <= new_entry:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Expected completion must be after the entry date.",
        )
    sent_ancestry = {f for f in _ANCESTRY_FIELDS if f in fields}
    if sent_ancestry:
        # Every parent pointer goes through the ONE writer, current values
        # overlaid with what was sent. An explicit null un-seats — allowed, but
        # only where nothing deeper still points there (a contradiction is a
        # 422 from _resolve_ancestry) and only where it does not widen the
        # compliance gap (checked below).
        intended = {f: fields.pop(f, getattr(cohort, f)) for f in _ANCESTRY_FIELDS}
        ancestry = _resolve_ancestry(db, intended, sent_ancestry)
        gap_before = {lv.key for lv in _compliance_gap(cohort)}
        for f in _ANCESTRY_FIELDS:
            setattr(cohort, f, getattr(ancestry, f))
        gap_after = {lv.key for lv in _compliance_gap(cohort)}
        widened = gap_after - gap_before
        if widened:
            # THE NO-REGRESSION RULE. A required level may be filled in, and a
            # legacy batch that is missing one may still have its name, dates
            # or department corrected — the gap is unchanged, the PATCH
            # passes. What may NOT happen is an edit that makes a compliant
            # batch non-compliant. That is the difference between a
            # data-quality rule and a lockout.
            labels = [
                lv.label for lv in institution_model.HIERARCHY_LEVELS if lv.key in widened
            ]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "This edit would remove " + ", ".join(labels)
                    + ", which is required. Choose a value or leave the field unchanged."
                ),
            )
    if entry is not None:
        cohort.start_date = _as_utc_midnight(entry)
    if completion is not None:
        cohort.end_date = _as_utc_midnight(completion)
    for field, value in fields.items():
        setattr(cohort, field, value)
    db.flush()
    _audit_institution(db, session, request, "cohort", cohort.id, "UPDATED", before, _row_snapshot(cohort))
    db.commit()
    db.refresh(cohort)
    return _admin_cohort_out(db, cohort)


# ------------------------------------------------------ seating a student --


class SetStudentCohortIn(BaseModel):
    """Null releases the student from their batch.

    Explicitly Optional rather than absent-means-keep, mirroring
    `AssignMentorIn` in console.py: an explicit null is the un-seat action, and
    a field that cannot express it forces a second endpoint.
    """

    cohort_id: str | None = None


class AdminStudentRowOut(BaseModel):
    """One student as the seating panel lists them. Name and email come from
    the User row, USN and stage from the Student row; nothing else — this is
    a roster line, not a record, and the console does not need marks to seat
    someone."""

    student_id: str
    name: str
    email: str
    usn: str | None
    current_stage: str | None
    cohort_id: str | None


def _student_rows(db: Session, *where) -> list[AdminStudentRowOut]:
    rows = db.execute(
        select(
            Student.id,
            User.name,
            User.email,
            Student.usn,
            Student.current_stage,
            Student.cohort_id,
        )
        .join(User, Student.user_id == User.id)
        .where(*where)
        .order_by(User.name)
    ).all()
    return [
        AdminStudentRowOut(
            student_id=sid,
            name=name,
            email=email,
            usn=usn,
            current_stage=stage.value if hasattr(stage, "value") else stage,
            cohort_id=cohort_id,
        )
        for sid, name, email, usn, stage, cohort_id in rows
    ]


@router.get("/cohorts/{cohort_id}/students", response_model=list[AdminStudentRowOut])
def list_cohort_students(
    cohort_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AdminStudentRowOut]:
    """Who is seated in this batch. The read half of the seating panel."""
    require_capability(db, session, "admin.institution")
    if db.get(Cohort, cohort_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found.")
    return _student_rows(db, Student.cohort_id == cohort_id)


@router.get("/students/unseated", response_model=list[AdminStudentRowOut])
def list_unseated_students(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AdminStudentRowOut]:
    """Students in no batch at all — the pool the seating panel picks from.

    NOT the same as console.py's /unassigned-students, which is students with
    no MENTOR. Two different facts, two different lists; a student can have a
    mentor and no batch, or a batch and no mentor. An empty response is the
    healthy steady state, exactly like /cohorts/unassigned.
    """
    require_capability(db, session, "admin.institution")
    return _student_rows(db, Student.cohort_id.is_(None))


@router.put("/students/{student_id}/cohort", status_code=status.HTTP_204_NO_CONTENT)
def set_student_cohort(
    student_id: str,
    body: SetStudentCohortIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    """Seat a student in a batch, or release them.

    This is the write that makes the locked profile card work at all. Before it,
    `students.cohort_id` was set only by `app/seed.py` and
    `python -m app.seed_roster` — neither of which runs on a production host.
    """
    require_capability(db, session, "admin.institution")
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    if body.cohort_id is not None and db.get(Cohort, body.cohort_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found.")
    # Imported here: admin_mentoring sits above this module in the import graph.
    from .admin_mentoring import assert_move_keeps_mentor_in_college

    assert_move_keeps_mentor_in_college(db, student, body.cohort_id)
    before = {"cohort_id": student.cohort_id}
    student.cohort_id = body.cohort_id
    _audit_institution(
        db, session, request, "student", student.id, "COHORT_SET", before, {"cohort_id": body.cohort_id}
    )
    db.commit()


# -------------------------------------------------- staff activation link --


class ActivationLinkOut(BaseModel):
    link: str
    emailed: bool
    expires_in_hours: int


@router.post("/users/{user_id}/activation-link", response_model=ActivationLinkOut)
def issue_activation_link(
    user_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ActivationLinkOut:
    """Mint (or re-mint) a staff member's activation link, and show it.

    THE ON-SCREEN LINK IS PERMANENT, not a stopgap while SES is in its
    sandbox. When someone says "the email never arrived" — and someone will —
    the admin reads them this link instead of waiting on a mail queue. Each
    call supersedes the previous link, so "resend" hands over exactly one that
    works. Refuses a STUDENT: they sign in with Google and hold no password.
    """
    require_admin(session)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    try:
        link, emailed = account_links.issue_activation(
            db, user, created_by_user_id=session["userId"]
        )
    except ValueError as why:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(why))
    return ActivationLinkOut(
        link=link, emailed=emailed, expires_in_hours=settings.activation_link_hours
    )


# ------------------------------------- the two columns that had no writer --


class InstitutionalIdentityIn(BaseModel):
    """`users.designation` and `users.department`.

    Both columns have existed since c4e91b5d2e70 and are READ in two places —
    the BGSCET leave form, which labels them "(synced)", and the Main Admin's
    mentor-load screen. Neither had a writer anywhere in the codebase: not an
    endpoint, not a CLI, not even a seed. They rendered null forever, and the
    leave form's "(synced)" was a promise nothing kept. This is the writer.
    """

    designation: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)


class InstitutionalIdentityOut(BaseModel):
    user_id: str
    name: str
    email: str
    designation: str | None
    department: str | None


@router.patch("/users/{user_id}/institutional-identity", response_model=InstitutionalIdentityOut)
def set_institutional_identity(
    user_id: str,
    body: InstitutionalIdentityIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> InstitutionalIdentityOut:
    require_capability(db, session, "admin.mentors")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    fields = body.model_dump(exclude_unset=True)
    for field, value in fields.items():
        # An empty string is a clearing, not a value — the leave form should say
        # "not on record" rather than print a blank line.
        setattr(user, field, (value or "").strip() or None)
    db.commit()
    db.refresh(user)
    return InstitutionalIdentityOut(
        user_id=user.id,
        name=user.name,
        email=user.email,
        designation=user.designation,
        department=user.department,
    )
