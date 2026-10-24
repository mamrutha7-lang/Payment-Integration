"""B13 — the catalogue, per college and per course.

WHAT THIS MODULE OWNS, and the one sentence that explains all of it: the
48 badges are CODE and stay code; what a college maintains is which of them
apply to WHICH PROGRAMME, which external certificates count towards them, and
which REEP stage a given semester of that programme sits in.

    approved_certifications   which certificates count        (rows, existing)
    badge_course_map          which of the 48 apply here      (rows, B13)
    stage_rules               semester N of this course is S  (rows, B13)
    courses                   the taught subjects             (rows, existing)

THE THREE APPROVED-CERTIFICATION HANDLERS MOVED HERE FROM
`routers/badge_verification.py`, URLs unchanged. Two things changed with them
and both are B13:

  * the gate is `require_capability(db, session, "admin.catalogue")` rather
    than `require_admin`. The Catalogue SCREEN has always opened on the
    grantable `admin.catalogue` while its most important list refused anyone
    but the Main Admin, and `catalogue.component.ts` carries a signal and a
    paragraph of copy to explain that split to the user. A screen that has to
    apologise for its own endpoint is the endpoint's bug.
  * the list is NARROWED and the writes are FENCED by B1.2's reach.

`admin.catalogue` IS STILL A `PROGRAMME` CAPABILITY AND DOES NOT CHANGE. The
4a map reads `CapabilityScope.PROGRAMME` as "a grant of this key cannot be
narrowed" and concludes that scoping this screen means re-scoping the key,
with a Phase-3 blast radius. It does not: `routers/governance.py` says in as
many words that a scope target is NOT refused for a PROGRAMME capability —
that word means "no mentor GROUP narrows this", and B1.4 narrows exactly these
keys by exactly these rungs. `scope_filter` reads the GRANT's `scope_level`,
never the capability's. So a college-scoped `admin.catalogue` grant already
works, and nothing in `models/governance.py` had to move.

NULL COLLEGE **AND** NULL COURSE MEANS PROGRAMME-WIDE, and every read here
keeps such a row visible to a narrowed holder. That is the meaning every row
written before B13 already carries (the migration attaches none of them unless
exactly one college and one course exist), and a scoped list that hid them
would empty the certification grid on the day the first scoped grant was made
— with the screen going quiet rather than refusing, which nobody reports.

WHY THE REACH PROJECTION LIVES HERE rather than in `app/scope_views.py`.
`Reach` hands out a subquery per model for the two things the spine is made of,
students and staff users. A CATALOGUE ROW IS NEITHER — same problem
`registration_scope_clause` solved for an application, and solved there because
it had two call sites in two different routers. This one has five call sites and
all five are in this file, so it is written once here, next to the rows it is
about. If a second module ever needs it, it moves to `scope_views.py` whole; it
does not get copied.

A COURSE HANGS UNDER A DEPARTMENT AND THAT DEPARTMENT'S COLLEGE — two rungs,
the same shape as `governance.ancestry_of_user`, and deliberately NOT the six
rungs of a student. A COHORT- or STUDENT-scoped grant therefore reaches no
course at all, which matches `reaches_target` exactly: a grant covers a target
when its (rung, id) is in the target's ancestry, and a batch sits BELOW a
course, not above it.

Rule 1: nothing here calls a model. Rule 2: no endpoint in this module names a
student, so `_assert_can_access_student` has nothing to guard; the fence is the
capability plus the reach.
"""

from __future__ import annotations

import csv
import io
from typing import Final, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..db import get_db
from ..governance import require_capability
from ..identity import get_current_session
# The subject import's byte ceiling is the spreadsheet importer's, IMPORTED and
# never re-declared: two admin upload doors refusing at two numbers is two
# numbers nobody can recall, which is the reason written above the constant
# itself in `imports_sheet.py`. Nothing heavy comes with it — that module's
# openpyxl import is function-local, so this does not put a parser in the import
# graph of every catalogue request.
from ..imports_sheet import MAX_UPLOAD_BYTES
from ..models.badge import (
    BADGE_BY_CODE,
    BADGES,
    CATEGORY_LABEL,
    ApprovedCertification,
    BadgeEvidence,
    EvidenceType,
    Stage,
)
from ..models.catalogue import BadgeCourseMap, StageRule
from ..models.course import Course, CourseModel, Dimension
from ..models.governance import ScopeLevel
from ..models.institution import AcademicCourse, College, Department
from ..policies import Reach, scope_filter
from ..scope_views import scope_header

router = APIRouter(tags=["admin-catalogue"])

CAPABILITY: Final[str] = "admin.catalogue"

#: The parts `POST /admin/catalogue/copy` knows how to copy. Named rather than
#: free text because each one is a different table with a different unique key,
#: and "parts" the server does not recognise must be a 422 and not a silent
#: no-op reported as success.
CopyPart = Literal["certifications", "badges", "stage_rules"]

#: How many rows one subject import will accept. A CSV is pasted by a human from
#: a spreadsheet; a hundred thousand rows is a wrong file, and finding that out
#: as a timeout rather than as a sentence is the difference between a mistake
#: and an incident.
MAX_IMPORT_ROWS: Final[int] = 2000


# --------------------------------------------------------------- the reach --


def _ancestry_of_course(db: Session, course_id: str) -> list[tuple[ScopeLevel, str]]:
    """Every (rung, id) an `academic_courses` row hangs under.

    COURSE, then its DEPARTMENT, then that department's COLLEGE. Shorter than a
    student's ancestry and longer than a staff account's, and — like both — it
    reads nothing that is stored on the row itself, because copying a course's
    college onto the course is the backfill the spine exists to avoid.

    An empty list for a course that does not exist, which `reaches_target`
    already treats as reached by nobody scoped.
    """
    row = db.execute(
        select(AcademicCourse.id, AcademicCourse.department_id, Department.college_id)
        .select_from(AcademicCourse)
        .outerjoin(Department, Department.id == AcademicCourse.department_id)
        .where(AcademicCourse.id == course_id)
    ).first()
    if row is None:
        return []
    pairs = [
        (ScopeLevel.COURSE, row.id),
        (ScopeLevel.DEPARTMENT, row.department_id),
        (ScopeLevel.COLLEGE, row.college_id),
    ]
    return [(level, target) for level, target in pairs if target]


def _reachable_course_ids(reach: Reach):
    """A SELECT of the `academic_courses` ids this reach covers.

    A subquery, never a materialised list, for the reason `Reach.student_ids`
    gives: executing inside the helper turns a composable clause into an
    enumeration of every id on the deployment.

    EVERYTHING SELECTS EVERYTHING rather than falling through to the empty
    clause below — the same correction `Reach.student_ids` carries, and for the
    same reason: the commonest caller is the Main Admin, and the cost of one
    call site forgetting `reach.everything` would be an empty catalogue on the
    account that holds every capability.
    """
    base = select(AcademicCourse.id)
    if reach.everything:
        return base
    clauses = []
    if reach.courses:
        clauses.append(AcademicCourse.id.in_(reach.courses))
    if reach.departments:
        clauses.append(AcademicCourse.department_id.in_(reach.departments))
    if reach.colleges:
        clauses.append(
            AcademicCourse.department_id.in_(
                select(Department.id).where(Department.college_id.in_(reach.colleges))
            )
        )
    if not clauses:
        # A cohort-, specialization- or student-scoped grant of this key. It
        # reaches no COURSE, because those rungs sit below one.
        return base.where(sa_false())
    return base.where(or_(*clauses))


def _certification_scope_clause(reach: Reach):
    """The reach, as a WHERE clause over `approved_certifications`.

    PROGRAMME-WIDE ROWS ARE ALWAYS IN. A row with neither pointer set applies to
    every college and every course on the deployment — that is what NULL means
    on these two columns — so it is part of a narrowed holder's catalogue too,
    and hiding it would be a narrower answer than the grant asked for.

    A row pinned to a COLLEGE is matched directly; a row pinned to a COURSE is
    matched through `_reachable_course_ids`, so a department-scoped holder sees
    the certificates filed against their department's courses without anybody
    having to re-derive that chain here.
    """
    if reach.everything:
        return sa_true()
    programme_wide = ApprovedCertification.college_id.is_(None) & (
        ApprovedCertification.course_id.is_(None)
    )
    clauses = [programme_wide]
    if reach.colleges:
        clauses.append(ApprovedCertification.college_id.in_(reach.colleges))
    clauses.append(ApprovedCertification.course_id.in_(_reachable_course_ids(reach)))
    return or_(*clauses)


def _course_within_reach(db: Session, session: dict, course_id: str) -> AcademicCourse:
    """The course, or the refusal that names why — 404 then 403, in that order.

    404 FIRST, deliberately, and it is not an information leak: `admin.catalogue`
    is a staff capability the caller already holds, the id is a programme's id
    and not a student's, and telling a scoped holder "that course is not yours"
    about an id that does not exist would send them to Governance to widen a
    grant that would never help.
    """
    course = db.get(AcademicCourse, course_id)
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found.")
    require_capability(db, session, CAPABILITY, target=_ancestry_of_course(db, course.id))
    return course


def _audit(
    db: Session, session: dict, request: Request, entity_type: str, entity_id: str,
    action: str, before: dict | None, after: dict | None, payload: dict,
) -> None:
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type=entity_type, entity_id=entity_id, action=action,
        before=before, after=after, event_type=f"{entity_type}.{action.lower()}", payload=payload,
    )


# ------------------------------------------------- the flat course picker --


class AdminCatalogueCourseOut(BaseModel):
    """One programme, with enough of its ancestry to be picked out of a flat list.

    PREFIXED FOR ITS SURFACE, because `CatalogueCourseOut` in `routers/console.py`
    is already a TAUGHT SUBJECT with its certifications nested — a different
    thing under the same word, which is the collision `AcademicCourse` exists to
    keep apart in the models and `test_no_new_duplicate_schema_names` keeps apart
    in the schemas.

    FLAT, ACROSS COLLEGES, and that is the owner's decision rather than the
    board's: the board draws a college select and then a course select, which is
    two controls and two round trips to answer one question on a deployment that
    has one college. The college's NAME is on every row instead, so a search box
    over this list finds "MBA" and "BGSCET" equally.
    """

    id: str
    code: str
    name: str
    department_id: str | None
    department: str | None
    college_id: str | None
    college: str | None
    degree_level: str | None
    total_semesters: int | None
    #: Rows filed against this course, so the picker can say what copying FROM
    #: it would actually move, and what copying INTO it would land beside.
    certifications: int
    badge_overrides: int
    stage_rules: int


@router.get("/admin/catalogue/courses", response_model=list[AdminCatalogueCourseOut])
def list_catalogue_courses(
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AdminCatalogueCourseOut]:
    """Every programme this caller's grant reaches, with its catalogue counts.

    The source for the copy dialog's two pickers and for the badge and stage-rule
    tabs' course select. One list, narrowed once.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    scope_header(response, reach)
    if reach.nothing:
        return []

    reachable = _reachable_course_ids(reach)
    rows = db.execute(
        select(AcademicCourse, Department.name, Department.college_id, College.name)
        .select_from(AcademicCourse)
        .outerjoin(Department, Department.id == AcademicCourse.department_id)
        .outerjoin(College, College.id == Department.college_id)
        .where(AcademicCourse.id.in_(reachable))
        .order_by(College.name, Department.name, AcademicCourse.code)
    ).all()

    certs = dict(
        db.execute(
            select(ApprovedCertification.course_id, func.count())
            .where(ApprovedCertification.course_id.is_not(None))
            .group_by(ApprovedCertification.course_id)
        ).all()
    )
    badges = dict(
        db.execute(
            select(BadgeCourseMap.course_id, func.count()).group_by(BadgeCourseMap.course_id)
        ).all()
    )
    stages = dict(
        db.execute(select(StageRule.course_id, func.count()).group_by(StageRule.course_id)).all()
    )

    return [
        AdminCatalogueCourseOut(
            id=course.id,
            code=course.code,
            name=course.name,
            department_id=course.department_id,
            department=dept_name,
            college_id=college_id,
            college=college_name,
            degree_level=course.degree_level.value if course.degree_level else None,
            total_semesters=course.total_semesters,
            certifications=certs.get(course.id, 0),
            badge_overrides=badges.get(course.id, 0),
            stage_rules=stages.get(course.id, 0),
        )
        for course, dept_name, college_id, college_name in rows
    ]


# ----------------------------- the Approved Certification Catalogue (§12) --


class ApprovedCertificationIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    provider: str = Field(min_length=1, max_length=200)
    badge_code: str
    evidence_type: str = "EXTERNAL_VERIFIED"
    stage: str = "EXCEL"
    duration_text: str | None = Field(default=None, max_length=100)
    is_free: bool = True
    url: str | None = Field(default=None, max_length=1000)
    active: bool = True
    # B13. BOTH OMITTED IS PROGRAMME-WIDE and is what every pre-B13 row means,
    # which is why neither has a default that points at something.
    college_id: str | None = None
    course_id: str | None = None


class ApprovedCertificationOut(BaseModel):
    id: str
    name: str
    provider: str
    badge_code: str
    badge_name: str
    # Read off the badge catalogue (code, not rows): the category the badge
    # sits in and the points it is worth. The Catalogue screen's Category and
    # Points columns, derived here so the two can never disagree with the badge.
    badge_category: str
    badge_points: int
    evidence_type: str
    stage: str
    duration_text: str | None
    is_free: bool
    url: str | None
    active: bool
    # Evidence rows students have filed against this catalogue entry, in any
    # review state. A certification nobody claims is a finding, not a gap.
    claims: int
    # B13's scope, resolved. `scope_label` is the sentence the grid shows, and
    # it says "Programme-wide" rather than an empty cell: a blank in a scope
    # column reads as "not set yet" on a row whose scope is set, and set wide.
    college_id: str | None
    college: str | None
    course_id: str | None
    course: str | None
    scope_label: str


def _validated_certification_fields(body: ApprovedCertificationIn) -> tuple[EvidenceType, Stage]:
    if body.badge_code not in BADGE_BY_CODE:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="No such badge.")
    try:
        ev_type = EvidenceType(body.evidence_type)
        stage = Stage(body.stage)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Bad evidence_type or stage.",
        )
    return ev_type, stage


def _scope_names(db: Session, rows: list[ApprovedCertification]) -> tuple[dict, dict]:
    """College and course names for a page of certifications, in two reads."""
    college_ids = {r.college_id for r in rows if r.college_id}
    course_ids = {r.course_id for r in rows if r.course_id}
    colleges = (
        dict(db.execute(select(College.id, College.name).where(College.id.in_(college_ids))).all())
        if college_ids
        else {}
    )
    courses = (
        dict(
            db.execute(
                select(AcademicCourse.id, AcademicCourse.name).where(
                    AcademicCourse.id.in_(course_ids)
                )
            ).all()
        )
        if course_ids
        else {}
    )
    return colleges, courses


def _approved_certification_row(
    c: ApprovedCertification,
    claims: int = 0,
    college: str | None = None,
    course: str | None = None,
) -> ApprovedCertificationOut:
    badge = BADGE_BY_CODE.get(c.badge_code)
    if c.course_id:
        scope_label = f"Course · {course or c.course_id}"
    elif c.college_id:
        scope_label = f"College · {college or c.college_id}"
    else:
        scope_label = "Programme-wide"
    return ApprovedCertificationOut(
        id=c.id,
        name=c.name,
        provider=c.provider,
        badge_code=c.badge_code,
        badge_name=badge.name if badge else c.badge_code,
        badge_category=CATEGORY_LABEL[badge.category] if badge else "",
        badge_points=badge.points if badge else 0,
        evidence_type=c.evidence_type.value,
        stage=c.stage.value,
        duration_text=c.duration_text,
        is_free=c.is_free,
        url=c.url,
        active=c.active,
        claims=claims,
        college_id=c.college_id,
        college=college,
        course_id=c.course_id,
        course=course,
        scope_label=scope_label,
    )


def _resolve_certification_scope(
    db: Session, session: dict, body: ApprovedCertificationIn
) -> tuple[str | None, str | None]:
    """The (college_id, course_id) to store, refusing what this caller cannot reach.

    A COURSE SETTLES ITS OWN COLLEGE, the way `_resolve_ancestry` settles a
    batch's: send a course and the college is derived from it, and a
    contradicting `college_id` is a 422 rather than a silent pick between two
    things the caller said.

    A NARROWED HOLDER CANNOT WRITE A PROGRAMME-WIDE ROW. Both fields omitted
    means "every college on this deployment", which is more reach than the
    grant that admitted the request — so it is refused with the sentence that
    says what to do instead, rather than stored and silently visible to
    everyone.
    """
    college_id = (body.college_id or "").strip() or None
    course_id = (body.course_id or "").strip() or None

    if course_id:
        course = _course_within_reach(db, session, course_id)
        derived = db.scalar(
            select(Department.college_id).where(Department.id == course.department_id)
        )
        if college_id and derived and college_id != derived:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"college_id {college_id!r} contradicts the course you chose, which sits "
                    f"under college {derived!r}. Clear the college, or pick a course in it."
                ),
            )
        return derived or college_id, course_id

    if college_id:
        if db.get(College, college_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"No college with id {college_id!r}.",
            )
        require_capability(
            db, session, CAPABILITY, target=[(ScopeLevel.COLLEGE, college_id)]
        )
        return college_id, None

    reach = scope_filter(db, session, CAPABILITY)
    if not reach.everything:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A programme-wide certification applies to every college on this deployment, "
                "which is wider than your grant. Name the college or the course this "
                "certification is for."
            ),
        )
    return None, None


@router.get("/admin/approved-certifications", response_model=list[ApprovedCertificationOut])
def list_approved_certifications(
    response: Response,
    course_id: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[ApprovedCertificationOut]:
    """The certification catalogue, narrowed to this caller's reach (B13).

    `?course_id=` narrows FURTHER, to one programme, and it is a FILTER and not
    a fence: it is still evaluated inside the reach, so a caller cannot read
    another college's catalogue by naming its course. What it adds is the
    course's own rows PLUS the programme-wide ones, which is the list that
    actually applies to a student on that course — the same set
    `compose_badges` shows them.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    scope_header(response, reach)
    if reach.nothing:
        return []

    where = [_certification_scope_clause(reach)]
    if course_id:
        _course_within_reach(db, session, course_id)
        where.append(
            or_(
                ApprovedCertification.course_id == course_id,
                ApprovedCertification.course_id.is_(None)
                & ApprovedCertification.college_id.is_(None),
            )
        )

    claims = {
        cert_id: n
        for cert_id, n in db.execute(
            select(BadgeEvidence.approved_certification_id, func.count())
            .where(BadgeEvidence.approved_certification_id.is_not(None))
            .group_by(BadgeEvidence.approved_certification_id)
        ).all()
    }
    rows = list(
        db.scalars(
            select(ApprovedCertification).where(*where).order_by(ApprovedCertification.name)
        ).all()
    )
    colleges, courses = _scope_names(db, rows)
    return [
        _approved_certification_row(
            c, claims.get(c.id, 0), colleges.get(c.college_id), courses.get(c.course_id)
        )
        for c in rows
    ]


@router.post(
    "/admin/approved-certifications",
    response_model=ApprovedCertificationOut,
    status_code=status.HTTP_201_CREATED,
)
def add_approved_certification(
    body: ApprovedCertificationIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ApprovedCertificationOut:
    require_capability(db, session, CAPABILITY)
    ev_type, stage = _validated_certification_fields(body)
    college_id, course_id = _resolve_certification_scope(db, session, body)
    cert = ApprovedCertification(
        name=body.name.strip(),
        provider=body.provider.strip(),
        badge_code=body.badge_code,
        evidence_type=ev_type,
        stage=stage,
        duration_text=(body.duration_text or "").strip() or None,
        is_free=body.is_free,
        url=(body.url or "").strip() or None,
        active=body.active,
        college_id=college_id,
        course_id=course_id,
    )
    db.add(cert)
    db.flush()
    _audit(
        db, session, request, "approved_certification", cert.id, "CREATE", None,
        {"badge_code": cert.badge_code, "college_id": college_id, "course_id": course_id},
        {"name": cert.name, "provider": cert.provider},
    )
    db.commit()
    db.refresh(cert)
    colleges, courses = _scope_names(db, [cert])
    return _approved_certification_row(
        cert, 0, colleges.get(cert.college_id), courses.get(cert.course_id)
    )


@router.patch("/admin/approved-certifications/{cert_id}", response_model=ApprovedCertificationOut)
def edit_approved_certification(
    cert_id: str,
    body: ApprovedCertificationIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ApprovedCertificationOut:
    require_capability(db, session, CAPABILITY)
    cert = db.get(ApprovedCertification, cert_id)
    if cert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Certification not found.")
    # THE ROW AS IT STANDS IS FENCED BEFORE THE ROW AS IT WOULD BE. A holder who
    # cannot see this certification must not be able to move it INTO their own
    # reach and edit it there, which is what checking only the incoming scope
    # would allow.
    _assert_certification_within_reach(db, session, cert)
    ev_type, stage = _validated_certification_fields(body)
    college_id, course_id = _resolve_certification_scope(db, session, body)
    before = {
        "name": cert.name, "active": cert.active,
        "college_id": cert.college_id, "course_id": cert.course_id,
    }
    cert.name = body.name.strip()
    cert.provider = body.provider.strip()
    cert.badge_code = body.badge_code
    cert.evidence_type = ev_type
    cert.stage = stage
    cert.duration_text = (body.duration_text or "").strip() or None
    cert.is_free = body.is_free
    cert.url = (body.url or "").strip() or None
    cert.active = body.active
    cert.college_id = college_id
    cert.course_id = course_id
    _audit(
        db, session, request, "approved_certification", cert.id, "UPDATE", before,
        {"name": cert.name, "active": cert.active,
         "college_id": college_id, "course_id": course_id},
        {"badge_code": cert.badge_code},
    )
    db.commit()
    db.refresh(cert)
    claims = (
        db.scalar(
            select(func.count())
            .select_from(BadgeEvidence)
            .where(BadgeEvidence.approved_certification_id == cert.id)
        )
        or 0
    )
    colleges, courses = _scope_names(db, [cert])
    return _approved_certification_row(
        cert, claims, colleges.get(cert.college_id), courses.get(cert.course_id)
    )


def _assert_certification_within_reach(
    db: Session, session: dict, cert: ApprovedCertification
) -> None:
    """Refuse an edit to a catalogue row this caller's grant does not cover.

    A PROGRAMME-WIDE ROW IS EDITABLE ONLY BY A PROGRAMME-WIDE HOLDER, which is
    the other half of `_resolve_certification_scope`'s refusal: a row that
    applies to every college is not one college's to change, and a narrowed
    holder who could edit it could change what every other college sees.
    """
    if cert.course_id:
        require_capability(
            db, session, CAPABILITY, target=_ancestry_of_course(db, cert.course_id)
        )
        return
    if cert.college_id:
        require_capability(
            db, session, CAPABILITY, target=[(ScopeLevel.COLLEGE, cert.college_id)]
        )
        return
    reach = scope_filter(db, session, CAPABILITY)
    if not reach.everything:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This certification is programme-wide, so it belongs to every college on "
                "this deployment and your grant reaches one. An administrator can widen it "
                "in Governance."
            ),
        )


# ---------------------------------------------- which badges apply here ----


class CourseBadgeOut(BaseModel):
    code: str
    name: str
    category: str
    category_label: str
    stage: str
    points: int
    #: What a student on this course actually sees. Absence of a row means
    #: ENABLED, so this is `true` for all 48 on a course nobody has edited.
    enabled: bool
    #: True when a row exists — the difference between "on because nobody has
    #: said otherwise" and "on because somebody switched it back on", which is a
    #: decision with a date on it and should not render as the default.
    overridden: bool


class BadgeCourseMapIn(BaseModel):
    course_id: str
    badge_code: str
    enabled: bool


def disabled_badge_codes(db: Session, course_id: str | None) -> frozenset[str]:
    """The badges switched OFF for this course. THE ONE READER OF THE TABLE.

    ABSENCE MEANS ENABLED, so this returns only the codes with a row saying
    `false`; a course with no rows, and a student seated in no batch, both come
    back as the empty set, which is exactly today's behaviour. Imported by
    `routers/badges.py::compose_badges` rather than re-derived there, because two
    readings of "does this badge apply" is how a student's dashboard and the
    screen that configured it end up disagreeing.
    """
    if not course_id:
        return frozenset()
    return frozenset(
        db.scalars(
            select(BadgeCourseMap.badge_code).where(
                BadgeCourseMap.course_id == course_id,
                BadgeCourseMap.enabled.is_(False),
            )
        ).all()
    )


@router.get("/admin/catalogue/badges", response_model=list[CourseBadgeOut])
def list_course_badges(
    course_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[CourseBadgeOut]:
    """All 48 badges, with whether each applies to this course.

    ALL 48 ALWAYS, never only the exceptions: the screen's question is "which of
    the 48 does an MBA student reach", and a list of three disabled codes cannot
    answer it. The catalogue is code; this endpoint says which of it is live
    here.
    """
    require_capability(db, session, CAPABILITY)
    _course_within_reach(db, session, course_id)
    rows = {
        r.badge_code: r
        for r in db.scalars(
            select(BadgeCourseMap).where(BadgeCourseMap.course_id == course_id)
        ).all()
    }
    return [
        CourseBadgeOut(
            code=b.code,
            name=b.name,
            category=b.category.value,
            category_label=CATEGORY_LABEL[b.category],
            stage=b.stage.value,
            points=b.points,
            enabled=rows[b.code].enabled if b.code in rows else True,
            overridden=b.code in rows,
        )
        for b in BADGES
    ]


@router.put("/admin/catalogue/badges", response_model=CourseBadgeOut)
def set_course_badge(
    body: BadgeCourseMapIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CourseBadgeOut:
    """Switch one badge on or off for one course.

    AN `enabled=true` ROW IS WRITTEN RATHER THAN THE `false` ROW DELETED, even
    though absence already means enabled. Switching a badge back on is a
    decision somebody made on a date, and a DELETE leaves the catalogue looking
    exactly as it did before anybody ever touched it — which is the state the
    audit trail then has to be read to distinguish.
    """
    require_capability(db, session, CAPABILITY)
    _course_within_reach(db, session, body.course_id)
    badge = BADGE_BY_CODE.get(body.badge_code)
    if badge is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="No such badge.")

    row = db.scalar(
        select(BadgeCourseMap).where(
            BadgeCourseMap.course_id == body.course_id,
            BadgeCourseMap.badge_code == body.badge_code,
        )
    )
    before = {"enabled": row.enabled} if row else None
    if row is None:
        row = BadgeCourseMap(
            course_id=body.course_id, badge_code=body.badge_code, enabled=body.enabled
        )
        db.add(row)
        db.flush()
    else:
        row.enabled = body.enabled
    _audit(
        db, session, request, "badge_course_map", row.id,
        "CREATE" if before is None else "UPDATE", before, {"enabled": body.enabled},
        {"course_id": body.course_id, "badge_code": body.badge_code},
    )
    db.commit()
    return CourseBadgeOut(
        code=badge.code,
        name=badge.name,
        category=badge.category.value,
        category_label=CATEGORY_LABEL[badge.category],
        stage=badge.stage.value,
        points=badge.points,
        enabled=body.enabled,
        overridden=True,
    )


# ------------------------------------------------------------ stage rules --


class StageRuleOut(BaseModel):
    id: str
    course_id: str
    semester: int
    stage: str


class StageRuleIn(BaseModel):
    course_id: str
    semester: int = Field(ge=1, le=20)
    stage: str


@router.get("/admin/catalogue/stage-rules", response_model=list[StageRuleOut])
def list_stage_rules(
    response: Response,
    course_id: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[StageRuleOut]:
    """"Semester N of this course is stage S", for every course in reach.

    Read by `POST /admin/cohorts/{id}/promote`, which applies a stage change
    only where a rule names one — so an empty list here is a promotion that
    moves the semester and leaves the stage alone, which is the documented
    no-op that lets B4.3 and B13 land independently.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    scope_header(response, reach)
    if reach.nothing:
        return []
    where = [StageRule.course_id.in_(_reachable_course_ids(reach))]
    if course_id:
        _course_within_reach(db, session, course_id)
        where.append(StageRule.course_id == course_id)
    return [
        StageRuleOut(id=r.id, course_id=r.course_id, semester=r.semester, stage=r.stage.value)
        for r in db.scalars(
            select(StageRule).where(*where).order_by(StageRule.course_id, StageRule.semester)
        ).all()
    ]


@router.put("/admin/catalogue/stage-rules", response_model=StageRuleOut)
def set_stage_rule(
    body: StageRuleIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StageRuleOut:
    """Upsert the rule for one (course, semester).

    UPSERT AND NOT CREATE, because `uq_stage_rule_course_semester` makes a
    second rule for the same semester an IntegrityError — a 500 on a screen
    whose user simply changed their mind about semester 3. The unique key IS
    the identity of the thing being edited.
    """
    require_capability(db, session, CAPABILITY)
    _course_within_reach(db, session, body.course_id)
    try:
        stage = Stage(body.stage)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Bad stage {body.stage!r}.",
        ) from None

    row = db.scalar(
        select(StageRule).where(
            StageRule.course_id == body.course_id, StageRule.semester == body.semester
        )
    )
    before = {"stage": row.stage.value} if row else None
    if row is None:
        row = StageRule(course_id=body.course_id, semester=body.semester, stage=stage)
        db.add(row)
        db.flush()
    else:
        row.stage = stage
    _audit(
        db, session, request, "stage_rule", row.id, "CREATE" if before is None else "UPDATE",
        before, {"stage": stage.value},
        {"course_id": body.course_id, "semester": body.semester},
    )
    db.commit()
    return StageRuleOut(
        id=row.id, course_id=body.course_id, semester=body.semester, stage=stage.value
    )


@router.delete("/admin/catalogue/stage-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stage_rule(
    rule_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Remove a rule. Nothing hangs off one — a promotion simply stops changing
    the stage at that semester, which is the same thing as never having had a
    rule there. So this is a real DELETE and not a deactivation, unlike a
    certification, which students' evidence rows point at."""
    require_capability(db, session, CAPABILITY)
    row = db.get(StageRule, rule_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stage rule not found.")
    _course_within_reach(db, session, row.course_id)
    _audit(
        db, session, request, "stage_rule", row.id, "DELETE",
        {"course_id": row.course_id, "semester": row.semester, "stage": row.stage.value}, None,
        {"course_id": row.course_id},
    )
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ copy --


class CatalogueCopyIn(BaseModel):
    from_course: str
    to_course: str
    parts: list[CopyPart] = Field(min_length=1)


class CopyPartOut(BaseModel):
    part: str
    copied: int
    #: Rows the destination already had, matched on the same unique key the
    #: table enforces. Reported rather than swallowed, because "copied 0,
    #: skipped 12" and "copied 0" are different answers and only one of them
    #: means the copy did nothing.
    skipped: int


class CatalogueCopyOut(BaseModel):
    from_course: str
    to_course: str
    dry_run: bool
    parts: list[CopyPartOut]


@router.post("/admin/catalogue/copy", response_model=CatalogueCopyOut)
def copy_catalogue(
    body: CatalogueCopyIn,
    request: Request,
    dry_run: bool = False,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CatalogueCopyOut:
    """Copy catalogue rows from one programme to another.

    THE ENDPOINT'S SHAPE IS THE SETTLED ONE — `{from_course, to_course,
    parts[]}`, an owner's decision. The board's two-step college-then-course
    picker becomes one flat searchable course list on the client
    (`GET /admin/catalogue/courses`), which is the same question asked once.

    BOTH COURSES ARE FENCED. Copying reads one programme and writes another, so
    a holder scoped to one college must not be able to pull another college's
    catalogue into their own by naming it as `from_course` — the source is as
    much a read of somebody's data as the list endpoint is.

    IT IS ADDITIVE AND IT NEVER OVERWRITES. Each part matches the destination on
    the unique key its own table enforces, and a row that is already there is
    SKIPPED and counted. Overwriting would make "copy the MBA's stage rules
    across" silently discard the MCA's, and the button that did it says "copy".

    `?dry_run=true` answers with the same counts and writes nothing, because
    the useful moment to learn that eleven of twelve rows already exist is
    before the copy, not after it.
    """
    require_capability(db, session, CAPABILITY)
    if body.from_course == body.to_course:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The source and destination course are the same.",
        )
    source = _course_within_reach(db, session, body.from_course)
    destination = _course_within_reach(db, session, body.to_course)

    results: list[CopyPartOut] = []
    for part in dict.fromkeys(body.parts):  # de-duplicated, order kept
        if part == "certifications":
            results.append(_copy_certifications(db, source, destination, dry_run))
        elif part == "badges":
            results.append(_copy_badge_map(db, source, destination, dry_run))
        else:
            results.append(_copy_stage_rules(db, source, destination, dry_run))

    if not dry_run:
        _audit(
            db, session, request, "academic_course", destination.id, "CATALOGUE_COPY", None,
            {"from_course": source.id, "parts": [r.part for r in results]},
            {"copied": {r.part: r.copied for r in results},
             "skipped": {r.part: r.skipped for r in results}},
        )
        db.commit()
    else:
        db.rollback()
    return CatalogueCopyOut(
        from_course=source.id, to_course=destination.id, dry_run=dry_run, parts=results
    )


def _copy_certifications(
    db: Session, source: AcademicCourse, destination: AcademicCourse, dry_run: bool
) -> CopyPartOut:
    """Certifications pinned to the source course, re-pinned to the destination.

    PROGRAMME-WIDE ROWS ARE NOT COPIED — they already apply to the destination,
    and copying one would turn a single shared row into two that drift apart.
    The destination's college is derived from the destination, never carried
    over from the source, or a copy across colleges would file a row under the
    wrong one.
    """
    existing = {
        (name, provider, badge)
        for name, provider, badge in db.execute(
            select(
                ApprovedCertification.name,
                ApprovedCertification.provider,
                ApprovedCertification.badge_code,
            ).where(ApprovedCertification.course_id == destination.id)
        ).all()
    }
    target_college = db.scalar(
        select(Department.college_id).where(Department.id == destination.department_id)
    )
    copied = skipped = 0
    for row in db.scalars(
        select(ApprovedCertification).where(ApprovedCertification.course_id == source.id)
    ).all():
        if (row.name, row.provider, row.badge_code) in existing:
            skipped += 1
            continue
        copied += 1
        if not dry_run:
            db.add(
                ApprovedCertification(
                    name=row.name, provider=row.provider, badge_code=row.badge_code,
                    evidence_type=row.evidence_type, stage=row.stage,
                    duration_text=row.duration_text, is_free=row.is_free, url=row.url,
                    active=row.active, college_id=target_college, course_id=destination.id,
                )
            )
    return CopyPartOut(part="certifications", copied=copied, skipped=skipped)


def _copy_badge_map(
    db: Session, source: AcademicCourse, destination: AcademicCourse, dry_run: bool
) -> CopyPartOut:
    existing = set(
        db.scalars(
            select(BadgeCourseMap.badge_code).where(BadgeCourseMap.course_id == destination.id)
        ).all()
    )
    copied = skipped = 0
    for row in db.scalars(
        select(BadgeCourseMap).where(BadgeCourseMap.course_id == source.id)
    ).all():
        if row.badge_code in existing:
            skipped += 1
            continue
        copied += 1
        if not dry_run:
            db.add(
                BadgeCourseMap(
                    course_id=destination.id, badge_code=row.badge_code, enabled=row.enabled
                )
            )
    return CopyPartOut(part="badges", copied=copied, skipped=skipped)


def _copy_stage_rules(
    db: Session, source: AcademicCourse, destination: AcademicCourse, dry_run: bool
) -> CopyPartOut:
    existing = set(
        db.scalars(
            select(StageRule.semester).where(StageRule.course_id == destination.id)
        ).all()
    )
    copied = skipped = 0
    for row in db.scalars(select(StageRule).where(StageRule.course_id == source.id)).all():
        if row.semester in existing:
            skipped += 1
            continue
        copied += 1
        if not dry_run:
            db.add(
                StageRule(course_id=destination.id, semester=row.semester, stage=row.stage)
            )
    return CopyPartOut(part="stage_rules", copied=copied, skipped=skipped)


# -------------------------------------------------------- subject import --


#: The columns a subject CSV must carry, in any order, matched case- and
#: space-insensitively. Named here rather than positionally because a
#: spreadsheet exported twice has its columns in two orders, and a positional
#: reader turns that into silently transposed data rather than an error.
REQUIRED_SUBJECT_COLUMNS: Final[frozenset[str]] = frozenset(
    {"code", "name", "stage", "dimension", "semester", "model_type"}
)


class SubjectImportRowOut(BaseModel):
    line: int
    code: str
    #: `create`, `update` or `error`. Three words and not a boolean, because
    #: "this row would change an existing subject" is the one an office reader
    #: needs to see before saying yes.
    outcome: str
    detail: str | None = None


class SubjectImportOut(BaseModel):
    dry_run: bool
    created: int
    updated: int
    errors: int
    rows: list[SubjectImportRowOut]


@router.post("/admin/catalogue/subjects/import", response_model=SubjectImportOut)
def import_subjects(
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = True,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SubjectImportOut:
    """CSV -> `courses`, the taught subjects. DRY RUN BY DEFAULT.

    THE DEFAULT IS THE DRY RUN, unlike every other endpoint here, and it matches
    `purge_people`/`purge_students`: this is the one catalogue write whose input
    is a file somebody exported from a spreadsheet, so the commonest mistake is
    the wrong file rather than the wrong value, and the commonest mistake should
    not be the one that costs a restore.

    `courses.code` IS A GLOBAL PRIMARY KEY, and this endpoint does not pretend
    otherwise. There is no per-college subject namespace: two colleges cannot
    both have `22MBA11`, and adding one is a primary-key change on a table with
    `enrollments`, `certifications` and `subject_marks.subject_code` hanging off
    it — a migration far larger than an import screen. So a subject is
    PROGRAMME-WIDE by construction, and a SCOPED HOLDER IS REFUSED here rather
    than writing a row every other college on the deployment would then see.
    That refusal is the honest report of the schema as it stands; widening
    `courses` is the change that would lift it.

    EVERY ROW IS REPORTED, including the ones that fail, and a failing row does
    not stop the others: an import that gives up on line 4 of 300 makes the
    office fix one typo per round trip.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    if not reach.everything:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A subject code is global across this deployment (courses.code is the "
                "primary key), so importing subjects is a programme-wide change and your "
                "grant is scoped. An administrator can widen it in Governance."
            ),
        )

    # read(MAX + 1), never read(), and BEFORE the decode, because the order is
    # what makes the bound worth anything: a cap checked after the read has
    # already spent the memory the cap exists to protect. On this path it is
    # spent three times over — `read()` buffers the upload, `decode` builds a
    # second copy of it and `io.StringIO` a third — and MAX_IMPORT_ROWS, the
    # only bound this endpoint had, cannot fire until the reader is already
    # walking parsed lines out of that third copy. Nothing sits in front of
    # uvicorn on the AWS deployment to make up the difference, so this read is
    # the bound. One byte past the ceiling is enough to refuse, with the same
    # 422 and the same sentence `admin_imports.py` answers, so the console's two
    # upload doors behave alike rather than at two numbers.
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That file is not UTF-8 text. Export the sheet as CSV and try again.",
        ) from None

    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower().replace(" ", "_") for h in (reader.fieldnames or [])}
    missing = REQUIRED_SUBJECT_COLUMNS - headers
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"The CSV is missing these columns: {', '.join(sorted(missing))}.",
        )

    rows: list[SubjectImportRowOut] = []
    created = updated = errors = 0
    # Codes seen in THIS file, so a sheet that lists 22MBA11 twice is reported as
    # a duplicate rather than counted as two creates and written as one.
    seen: set[str] = set()

    for line, raw_row in enumerate(reader, start=2):
        if line - 1 > MAX_IMPORT_ROWS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"That file has more than {MAX_IMPORT_ROWS} rows. Split it.",
            )
        row = {
            (k or "").strip().lower().replace(" ", "_"): (v or "").strip()
            for k, v in raw_row.items()
        }
        code = row.get("code", "")
        if not code:
            errors += 1
            rows.append(SubjectImportRowOut(line=line, code="", outcome="error",
                                            detail="No subject code on this row."))
            continue
        if code in seen:
            errors += 1
            rows.append(SubjectImportRowOut(line=line, code=code, outcome="error",
                                            detail="This code appears earlier in the file."))
            continue
        seen.add(code)
        try:
            values = _subject_values(row)
        except ValueError as exc:
            errors += 1
            rows.append(SubjectImportRowOut(line=line, code=code, outcome="error",
                                            detail=str(exc)))
            continue

        existing = db.get(Course, code)
        if existing is None:
            created += 1
            rows.append(SubjectImportRowOut(line=line, code=code, outcome="create"))
            if not dry_run:
                db.add(Course(code=code, **values))
        else:
            updated += 1
            rows.append(SubjectImportRowOut(line=line, code=code, outcome="update"))
            if not dry_run:
                for field, value in values.items():
                    setattr(existing, field, value)

    if not dry_run:
        _audit(
            db, session, request, "course_catalogue", "subjects", "IMPORT", None,
            {"created": created, "updated": updated, "errors": errors},
            {"filename": file.filename},
        )
        db.commit()
    else:
        # Nothing was added in a dry run, but a `db.get` miss still leaves the
        # identity map warm; rolling back keeps the request's session clean for
        # whatever the next dependency does with it.
        db.rollback()

    return SubjectImportOut(
        dry_run=dry_run, created=created, updated=updated, errors=errors, rows=rows
    )


def _subject_values(row: dict[str, str]) -> dict:
    """One CSV row as `courses` column values, or a ValueError naming the field.

    THE ERROR NAMES THE COLUMN AND THE VALUE, because an import report saying
    "invalid row" about line 143 of a 300-row sheet is a report the office
    cannot act on without opening the file and guessing.
    """
    def enum_value(field: str, enum_cls):
        raw = row.get(field, "").upper().replace(" ", "_").replace("-", "_")
        try:
            return enum_cls(raw)
        except ValueError:
            allowed = ", ".join(m.value for m in enum_cls)
            raise ValueError(f"{field}: {row.get(field, '')!r} is not one of {allowed}.") from None

    def number(field: str, default: float, minimum: float = 0.0) -> float:
        raw = row.get(field, "")
        if raw == "":
            return default
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{field}: {raw!r} is not a number.") from None
        if value < minimum:
            raise ValueError(f"{field}: {raw!r} is below {minimum}.")
        return value

    name = row.get("name", "")
    if not name:
        raise ValueError("name: every subject needs a name.")
    semester = int(number("semester", 0, minimum=1))
    return {
        "name": name,
        "stage": enum_value("stage", Stage),
        "dimension": enum_value("dimension", Dimension),
        "semester": semester,
        "model_type": enum_value("model_type", CourseModel),
        "teaching_hours": number("teaching_hours", 0.0),
        "self_learning_hours_required": number("self_learning_hours_required", 0.0),
        "duration_weeks": int(number("duration_weeks", 16, minimum=1)),
        "description": row.get("description") or None,
    }
