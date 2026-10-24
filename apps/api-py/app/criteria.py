"""Which placement gates apply to this student — B8.2's one answer.

THE FALLBACK CHAIN WAS THREE DIFFERENT CHAINS. Before B8.2, `PlacementCriteria`
had exactly three readers and no two of them agreed about what happens when the
office has set nothing:

  * `console.criteria` (the admin screen) answered **404**;
  * `student.my_jobs` (the opportunities feed) fell through to **None**, which
    is NO GATE AT ALL — every posting eligible for everybody;
  * `student.compose_placement_readiness` fell through to **four literals**
    (6.0 CGPA, 0 live backlogs, 75% attendance, 50% certifications).

So on a deployment with no criteria row a student's readiness screen told them
they failed a CGPA cut-off that the jobs screen, two clicks away, did not apply.
This module is the single resolver all three now import, for the reason
`_assert_can_access_student` is imported rather than reimplemented: a rule with
three copies is a rule with three behaviours.

THE FOUR LITERALS ARE KEPT EXACTLY. `DEFAULTS` below carries the readiness
screen's numbers unchanged, because they are the ones a student has been reading
and the cross-cutting guardrail names them: "criteria defaults remain the
fallback". The three fields readiness never needed — `max_gap_months`,
`min_reep_completion_pct`, `require_core_certs` — take the MODEL'S OWN column
defaults, which is the only other place those numbers are written down.

THAT IS A BEHAVIOUR CHANGE TO THE JOBS FEED AND IT IS DELIBERATE. On a
deployment with no criteria row the feed now applies the same defaults the
readiness screen applies, so a student whose CGPA is below the fallback sees the
same verdict on both screens. Every seeded and every configured deployment has a
row and is unaffected; what changes is the deployment where nobody has set
anything, and there the old behaviour was not "no policy" but "two policies".

THE CHAIN IS FOUR RUNGS, NOT TWO. 04-backend-changes.md says "course row →
programme row → hard-coded defaults". The table also carries `college_id`, so a
college row sits between them: course → college → programme (NULL on both) →
defaults. A deployment that has written no college rows resolves identically to
the two-rung chain, and a multi-college deployment gets the rung it obviously
needs rather than a second table later.

`effective_from` IS READ AS "HAS THIS TAKEN EFFECT YET". A row dated in the
future is ignored, so an office can type next term's gates today without
changing a student's verdict this afternoon. NULL means "since the beginning" —
see the model's docstring for why the migration did not stamp existing rows.

NOTHING HERE RAISES HTTPException, following `app/semester_bounds.py`: this
module answers a question about policy and the routers decide what the answer
costs. `console.criteria` still 404s when nothing is SET, which is a different
question from "what applies" and is why `ResolvedCriteria.is_default` exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models.governance import ScopeLevel
from .models.placement_criteria import PlacementCriteria

#: Where a resolved set came from. Carried rather than derived, because the
#: screens say different things about each: "the MBA's own gates", "your
#: college's", "the programme default" and "nobody has set this yet" are four
#: sentences, and a boolean could only draw two of them.
SOURCE_COURSE = "course"
SOURCE_COLLEGE = "college"
SOURCE_PROGRAMME = "programme"
SOURCE_DEFAULTS = "defaults"


@dataclass(frozen=True)
class ResolvedCriteria:
    """The gates that apply, and where they came from."""

    min_cgpa: float
    max_live_backlogs: int
    max_gap_months: int
    min_attendance_pct: float
    min_reep_completion_pct: float
    min_cert_completion_pct: float
    require_core_certs: bool

    source: str = SOURCE_DEFAULTS
    criteria_id: str | None = None
    name: str | None = None
    college_id: str | None = None
    course_id: str | None = None
    effective_from: date | None = None
    active: bool = True

    @property
    def is_default(self) -> bool:
        """True when NO row answered and these are the hard-coded numbers.

        The distinction the admin screen needs: it must say "nobody has set
        this" rather than draw the fallback as though the office chose it.
        """
        return self.source == SOURCE_DEFAULTS


#: The hard-coded floor. The first four are `compose_placement_readiness`'s own
#: literals, kept to the digit; the last three are the model's column defaults.
#: Every number here must satisfy `ck_placement_criteria_range`, because a row
#: written from these values has to be storable.
DEFAULTS = ResolvedCriteria(
    min_cgpa=6.0,
    max_live_backlogs=0,
    max_gap_months=24,
    min_attendance_pct=75.0,
    min_reep_completion_pct=80.0,
    min_cert_completion_pct=50.0,
    require_core_certs=True,
    source=SOURCE_DEFAULTS,
)


def from_row(row: PlacementCriteria, source: str) -> ResolvedCriteria:
    """One stored row in the resolver's shape, labelled with the rung it hangs on.

    Public because the console's write and history endpoints build the same
    view of a row they have just written or just read; a second constructor
    there would be the second place a row's meaning is decided.
    """
    return ResolvedCriteria(
        min_cgpa=row.min_cgpa,
        max_live_backlogs=row.max_live_backlogs,
        max_gap_months=row.max_gap_months,
        min_attendance_pct=row.min_attendance_pct,
        min_reep_completion_pct=row.min_reep_completion_pct,
        min_cert_completion_pct=row.min_cert_completion_pct,
        require_core_certs=row.require_core_certs,
        source=source,
        criteria_id=row.id,
        name=row.name,
        college_id=row.college_id,
        course_id=row.course_id,
        effective_from=row.effective_from,
        active=row.active,
    )


def _live(on: date):
    """The rows that are switched on and have already taken effect."""
    return (
        PlacementCriteria.active.is_(True),
        or_(
            PlacementCriteria.effective_from.is_(None),
            PlacementCriteria.effective_from <= on,
        ),
    )


def _newest(db: Session, *clauses) -> PlacementCriteria | None:
    """The most recent live row matching these clauses.

    Ordered by `effective_from` first and `updated_at` second, NULLS LAST on the
    date so a dated row beats an undated one. Two rows written the same day are
    separated by `updated_at`, which is what the table had before B8.2 and what
    the existing single-row deployments are ordered by today.
    """
    return db.scalar(
        select(PlacementCriteria)
        .where(*clauses)
        .order_by(
            PlacementCriteria.effective_from.desc().nullslast(),
            PlacementCriteria.updated_at.desc(),
        )
        .limit(1)
    )


def resolve(
    db: Session,
    *,
    college_id: str | None = None,
    course_id: str | None = None,
    on: date | None = None,
) -> ResolvedCriteria:
    """The gates for one course in one college, walking the chain down.

    A COURSE ROW IS NOT REQUIRED TO NAME ITS COLLEGE. `placement_criteria.
    college_id` and `course_id` are independent pointers, so a row naming only
    the course applies to that course wherever it is taught — which is the
    common case, because a course belongs to exactly one department and
    therefore to exactly one college anyway. The course rung therefore matches
    on `course_id` and does not also demand the college; the college rung
    matches `college_id` with `course_id IS NULL`, so it cannot swallow a
    course row that happens to name the same college.
    """
    today = on or date.today()
    live = _live(today)

    if course_id:
        row = _newest(db, *live, PlacementCriteria.course_id == course_id)
        if row is not None:
            return from_row(row, SOURCE_COURSE)

    if college_id:
        row = _newest(
            db,
            *live,
            PlacementCriteria.college_id == college_id,
            PlacementCriteria.course_id.is_(None),
        )
        if row is not None:
            return from_row(row, SOURCE_COLLEGE)

    row = _newest(
        db,
        *live,
        PlacementCriteria.college_id.is_(None),
        PlacementCriteria.course_id.is_(None),
    )
    if row is not None:
        return from_row(row, SOURCE_PROGRAMME)

    return DEFAULTS


def resolve_for_student(db: Session, student_id: str) -> ResolvedCriteria:
    """The gates that apply to one student, through the spine.

    The ancestry comes from `governance.ancestry_of_student`, which is the one
    place that walk is written and which reads BOTH department pointers — so a
    student who named a department on the registration form and has not been
    seated in a batch still resolves a college. Deriving the pair here instead
    would be a second copy of that walk, and the copy would be the one that
    forgot the unseated.
    """
    from .governance import ancestry_of_student

    ancestry = dict(ancestry_of_student(db, student_id))
    return resolve(
        db,
        college_id=ancestry.get(ScopeLevel.COLLEGE),
        course_id=ancestry.get(ScopeLevel.COURSE),
    )


def as_payload(resolved: ResolvedCriteria) -> dict:
    """The seven numbers, for a response model or an audit `after` block."""
    return {
        "min_cgpa": resolved.min_cgpa,
        "max_live_backlogs": resolved.max_live_backlogs,
        "max_gap_months": resolved.max_gap_months,
        "min_attendance_pct": resolved.min_attendance_pct,
        "min_reep_completion_pct": resolved.min_reep_completion_pct,
        "min_cert_completion_pct": resolved.min_cert_completion_pct,
        "require_core_certs": resolved.require_core_certs,
    }



def resolve_for_cohort(db: Session, cohort_id: str | None) -> ResolvedCriteria:
    """The gates a whole batch shares, through `governance.ancestry_of_cohort`.

    A batch hangs on exactly one course and one college, so every student seated
    in it resolves the same set — which is what makes the roll-up below cheap.
    """
    from .governance import ancestry_of_cohort

    if not cohort_id:
        return resolve(db)
    ancestry = dict(ancestry_of_cohort(db, cohort_id))
    return resolve(
        db,
        college_id=ancestry.get(ScopeLevel.COLLEGE),
        course_id=ancestry.get(ScopeLevel.COURSE),
    )


def resolve_for_students(db: Session, student_ids: list[str]) -> dict[str, ResolvedCriteria]:
    """The gates for many students: one resolve per BATCH, not one per student.

    B8.5's cohort roll-up scores every student in a reach, and
    `resolve_for_student` is two reads each — four thousand queries on a
    two-thousand-student deployment, on a screen the office opens every morning.
    `readiness_inputs_many` met the same problem and answered it the same way:
    the DECISION is not repeated, only the fetching is batched.

    THE ANCESTRY WALK IS NOT COPIED HERE. Students seated in a batch are grouped
    by `cohort_id` and resolved through `governance.ancestry_of_cohort`; the
    unseated — who have a department and no batch, and are the reason
    `ancestry_of_student` reads both department pointers — fall through to
    `resolve_for_student` one at a time, because there is nothing to group them
    by and there are never many of them. Two existing walks, no third.
    """
    if not student_ids:
        return {}
    from .models.user import Student

    seats = db.execute(
        select(Student.id, Student.cohort_id).where(Student.id.in_(student_ids))
    ).all()
    by_cohort: dict[str, ResolvedCriteria] = {}
    out: dict[str, ResolvedCriteria] = {}
    for student_id, cohort_id in seats:
        if cohort_id:
            if cohort_id not in by_cohort:
                by_cohort[cohort_id] = resolve_for_cohort(db, cohort_id)
            out[student_id] = by_cohort[cohort_id]
        else:
            out[student_id] = resolve_for_student(db, student_id)
    # A student id that named no row at all still gets an answer, because the
    # caller is about to render a score for it either way.
    programme = None
    for student_id in student_ids:
        if student_id not in out:
            if programme is None:
                programme = resolve(db)
            out[student_id] = programme
    return out
