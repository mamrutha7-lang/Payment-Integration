"""Which postings a person sees — B12.1's one answer, and B12.2's.

THREE BOARDS READ ONE TABLE. `GET /api/student/jobs` (with the match % and the
eligibility verdict), `GET /api/alumni/jobs` (deliberately without either) and
`GET /api/admin/jobs` all select from `jobs`, and before B12.1 all three
selected EVERY row: a posting published for one college's MBA cohort appeared on
every student's feed on the deployment. The narrowing has to be identical on the
two candidate-facing boards or "the posting is not on my screen" becomes a
question with two answers, so it is written here once and imported, for the
reason `_assert_can_access_student` is imported rather than reimplemented.

THE THREE PREDICATES ARE ALL "NULL MEANS EVERYBODY", AND THAT IS THE COMPATIBLE
READING RATHER THAN AN OVERSIGHT. Every posting that existed before B12.1 has a
NULL college, a NULL course and an empty `tracks`, and every student could see
it. If NULL narrowed instead — "this posting names no college, so no college
sees it" — the migration would empty every jobs board in the product on deploy,
which is the failure nobody reports as a permissions bug because the screen
looks like it is simply having a quiet week.

THE VIEWER'S SIDE HAS THE SAME RULE, TURNED AROUND: an attribute the viewer does
not have does not filter. A student who has not been seated in a batch has no
college, no course and no track; matching them against a posting's college would
show them nothing at all, and a student with no batch is exactly the student a
college that has not built its batches yet has hundreds of. What we cannot
resolve, we do not narrow by — and the posting's own NULLs still mean everybody,
so the two rules compose to "today's behaviour" for anybody unfiled.

WHY A TRACK IS AN ACADEMIC SPECIALIZATION CODE AND NOT AN INTERVIEW TRACK.
`interview_matrix.SPECIALIZATIONS` (hr / dm / ba / fa) is a vocabulary of
REHEARSALS: a student picks one at the start of a mock interview and nothing
stores the choice on them, so "the student's interview track" is not a fact the
database holds. `academic_specializations.code` is — it hangs off the batch the
student is seated in, it is what the console calls "Specialization", and it is
the only track a posting could be matched against. The codes are normalised to
upper case by the one writer (`console.create_job`), so the comparison here is a
plain equality rather than a per-row `upper()` that no index could serve.

CLOSED IS A FACT, EXPIRED IS ARITHMETIC (B12.2). This module filters the two
candidate boards on `status = 'open'` and NOT on `closes_on`, even though the
client already derives a state from that date. They answer different questions:
`status` is the office asserting a posting is withdrawn, `closes_on` is the
deadline printed on it. Filtering on the date as well would silently remove
every past-deadline posting from a feed that shows them today — a behaviour
change to a student-facing list that B12.2 did not ask for and that would be
indistinguishable, from the student's side, from postings going missing.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from .models.governance import ScopeLevel
from .models.institution import AcademicSpecialization
from .models.job import Job

#: `jobs.status`. A plain string column, so the vocabulary lives here rather
#: than in a PG type — see the column's own comment for why.
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
JOB_STATUSES = (STATUS_OPEN, STATUS_CLOSED)


@dataclass(frozen=True)
class Audience:
    """Where one viewer sits, as far as a posting is concerned.

    All three are optional and `None` means "not resolvable", never "none of
    them" — see the module docstring. An alumnus with no linked student row is
    three Nones and sees the whole open board, which is what they saw before
    B12.1 and what a screen that empties itself would not be.
    """

    college_id: str | None = None
    course_id: str | None = None
    track: str | None = None

    @property
    def unfiled(self) -> bool:
        """True when nothing about this viewer narrows anything."""
        return not (self.college_id or self.course_id or self.track)


def normalise_track(value: str) -> str:
    """One track code as it is stored and compared: trimmed, upper case."""
    return value.strip().upper()


def audience_for_student(db: Session, student_id: str) -> Audience:
    """The college, course and track of one student, through the spine.

    The ancestry comes from `governance.ancestry_of_student` — the one place
    that walk is written, and the one that reads BOTH department pointers, so a
    student who named a department on the registration form and has never been
    seated in a batch still resolves a college. `criteria.resolve_for_student`
    takes the same route for the same reason; a second flat join here would be
    the copy that forgets the unseated.
    """
    from .governance import ancestry_of_student

    ancestry = dict(ancestry_of_student(db, student_id))
    specialization_id = ancestry.get(ScopeLevel.SPECIALIZATION)
    code = (
        db.scalar(
            select(AcademicSpecialization.code).where(
                AcademicSpecialization.id == specialization_id
            )
        )
        if specialization_id
        else None
    )
    return Audience(
        college_id=ancestry.get(ScopeLevel.COLLEGE),
        course_id=ancestry.get(ScopeLevel.COURSE),
        track=normalise_track(code) if code else None,
    )


def audience_for_alumnus(db: Session, user_id: str) -> Audience:
    """The same, for an alumnus — through the student row their profile links.

    AN ALUMNUS WITH NO LINK SEES THE WHOLE OPEN BOARD. `alumni_profiles.
    student_id` (B4.4) is filled in by the first-login form and is NULL for
    every alumnus who predates it and for anyone who never studied here. The
    third option — show them nothing until somebody links them — silently
    empties a working screen for the role whose entire surface is three pages,
    so it is not on the table.
    """
    from .models.alumni import AlumniProfile

    student_id = db.scalar(
        select(AlumniProfile.student_id).where(AlumniProfile.user_id == user_id)
    )
    if not student_id:
        return Audience()
    return audience_for_student(db, student_id)


def visible_clauses(audience: Audience, *, open_only: bool = True) -> list:
    """The WHERE clauses that put a posting on this viewer's board."""
    clauses: list = []
    if open_only:
        clauses.append(Job.status == STATUS_OPEN)
    if audience.college_id:
        clauses.append(
            or_(Job.college_id.is_(None), Job.college_id == audience.college_id)
        )
    if audience.course_id:
        clauses.append(or_(Job.course_id.is_(None), Job.course_id == audience.course_id))
    if audience.track:
        # `cardinality` rather than `array_length`, which answers NULL — not 0 —
        # for the empty array every pre-B12.1 row carries.
        clauses.append(
            or_(func.cardinality(Job.tracks) == 0, Job.tracks.any(audience.track))
        )
    return clauses


def postings_for(audience: Audience, *, open_only: bool = True) -> Select:
    """Every posting this viewer may see, newest first.

    Returns the SELECT rather than the rows, so the two feeds keep their own
    shapes: the student's builds a match % per row in Python and the alumnus's
    does not, and a helper that returned models would have to be told which.
    """
    return select(Job).where(*visible_clauses(audience, open_only=open_only)).order_by(
        Job.posted_on.desc()
    )
