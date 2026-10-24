"""B1.4 — one Reach, the models it does not own, and the header that states it.

`policies.scope_filter` answers the question ONCE — how far does this session's
grant for this key reach — and `policies.Reach` hands out a subquery per model
for the two things the spine is made of: `students` and staff `users`. Two of
the lists B1.4 narrows are neither.

A REGISTRATION IS NOT A STUDENT YET, and that is the whole point of the review
queue. The row carries its own `college_id` / `department_id` / `course_id` /
`specialization_id` / `requested_cohort_id` pointers — what the applicant named
on the public form, walked up the spine by `registration._resolve_claim` — and
there is no `students` row to join to until somebody approves it. So the
projection onto `registrations` is written here rather than in `policies.py`,
which knows about the spine and should not learn about an application; and
written ONCE here rather than at each of its two call sites
(`registration.pending` and the pending-applications tile in
`console.analytics_summary`), because two copies of a scope projection is
exactly how one of them stops matching the other.

The reach itself is still computed in exactly one place. Nothing in this module
decides who may see what; it decides how an answer already given is written
against one more table.

THE HEADER. 04-backend-changes.md asks that "every response carries
`scope: {college, department}`" so the app bar's scope control can show it.
Eleven list endpoints answer a bare `list[...]` today and the Phase 2 console is
built against those arrays; wrapping each in `{scope, rows}` is a breaking
change to every one of them, spent on the least important half of the
requirement. `app/exports.py` met the same problem from the other side (a JSON
envelope around a CSV is not a CSV) and answered it with response headers. This
is the same answer in the same shape of name, so a client reads scope one way
wherever it asks.
"""

from __future__ import annotations

from typing import Final

from fastapi import Response
from sqlalchemy import false as sa_false
from sqlalchemy import or_, select
from sqlalchemy import true as sa_true

from .exports import scope_note
from .policies import Reach

#: One of `programme`, `narrowed` or `none` — the three words `scope_note` uses,
#: and for its reason: "may see everything" and "may see nothing" are opposite
#: facts, not two ends of one scale, and must never render the same.
SCOPE_HEADER: Final[str] = "X-Reep-Scope"
SCOPE_COLLEGES_HEADER: Final[str] = "X-Reep-Scope-Colleges"
SCOPE_DEPARTMENTS_HEADER: Final[str] = "X-Reep-Scope-Departments"

#: How many ids a scope header will name before it stops. A header is not a
#: payload: a holder with three hundred cohort grants would otherwise push a
#: response past what several proxies will forward, and the failure would be the
#: whole response disappearing rather than a truncated hint doing so.
MAX_SCOPE_IDS: Final[int] = 20


def _ids(values: frozenset[str]) -> str:
    return ",".join(sorted(values)[:MAX_SCOPE_IDS])


def scope_header(response: Response, reach: Reach) -> None:
    """State the caller's reach on the response, for the app bar's scope control.

    Always sets the word. The two id headers are set only when there are ids to
    set, so a client can tell "narrowed to these colleges" from "narrowed, but
    at a rung this header does not carry" (a cohort- or student-scoped grant)
    without reading an empty string as a list of none.
    """
    note = scope_note(reach)
    response.headers[SCOPE_HEADER] = str(note["scope"])
    if reach.colleges:
        response.headers[SCOPE_COLLEGES_HEADER] = _ids(reach.colleges)
    if reach.departments:
        response.headers[SCOPE_DEPARTMENTS_HEADER] = _ids(reach.departments)


def registration_scope_clause(reach: Reach):
    """The reach, as a WHERE clause over `registrations`.

    Every rung the applicant's claim can name is matched, because the claim is
    walked UP: somebody who names a batch has their department and college
    settled from it, so a department-scoped reviewer sees that application
    through `department_id` and a college-scoped one through `college_id`,
    without either having to re-derive the chain here.

    BOTH COHORT COLUMNS. `requested_cohort_id` is what the applicant asked for
    and `cohort_id` is what a rule or a reviewer settled on; a cohort-scoped
    reach that read only one of them would lose the queue's rows at whichever
    moment the other was written.

    A STUDENT-SCOPED REACH MATCHES NO APPLICATION AT ALL, and that is right
    rather than missing: a grant naming one student is a grant about somebody
    who already has a `students` row, and an application is by definition
    somebody who does not. It falls through to the empty-clause branch below and
    sees nothing, which is the same answer `reaches_target` gives a target that
    hangs under nothing.
    """
    from .models.registration import Registration

    if reach.everything:
        return sa_true()
    clauses = []
    if reach.colleges:
        clauses.append(Registration.college_id.in_(reach.colleges))
    if reach.departments:
        clauses.append(Registration.department_id.in_(reach.departments))
    if reach.courses:
        clauses.append(Registration.course_id.in_(reach.courses))
    if reach.specializations:
        clauses.append(Registration.specialization_id.in_(reach.specializations))
        # The other tick of a dual specialization (2026-09-22). An application
        # naming Finance AND Marketing hangs under both, so a reviewer whose
        # grant reaches only Marketing must still see it - otherwise the one
        # applicant that office most needs to seat is the one it cannot list.
        clauses.append(Registration.second_specialization_id.in_(reach.specializations))
    if reach.cohorts:
        clauses.append(Registration.cohort_id.in_(reach.cohorts))
        clauses.append(Registration.requested_cohort_id.in_(reach.cohorts))
    if not clauses:
        return sa_false()
    return or_(*clauses)


def registration_rule_scope_clause(reach: Reach):
    """The reach, as a WHERE clause over `registration_rules` (B11.3).

    A RULE HANGS ON ITS BATCH, and on nothing else. `registration_rules` carries
    one spine pointer — a nullable `cohort_id` — so the walk is the batch's own:
    `cohorts` carries `department_id`, `course_id` and `specialization_id`, and
    the college is one more join through the department. That is the same walk
    `institution_domains.college_ids_for_cohorts` makes and the same shape
    `import_run_scope_clause` uses directly below; it is written as a subquery
    rather than as ancestry denormalised onto the rule for the reason
    `_resolve_ancestry` exists — one writer for a batch's ancestry.

    A RULE THAT NAMES NO BATCH IS THE MAIN ADMIN'S, and that is the deliberate
    opposite of a `capability_grants` NULL (B1.2), where NULL means
    programme-wide. `cohort_id IS NULL` here means the rule hangs under nothing,
    so `IN (subquery)` is NULL for it and no narrowed reach matches it — the
    same answer `registration_scope_clause` gives an application that named
    nothing, and for the same reason: a rule visible to everybody because it
    named nobody would be the way around every scope in the system.

    THIS IS THE LIST *AND* THE WRITES. `registration._assert_rule_reachable`
    re-SELECTs a rule through this very clause rather than re-reading the reach
    in Python, so "which rules can I see" and "which rules can I edit" are one
    predicate by construction — which matters more here than anywhere else on
    this screen, because an `auto_approve` rule naming a batch is the one
    control in the product that seats a student without a human.
    """
    from .models.cohort import Cohort
    from .models.institution import Department
    from .models.registration import RegistrationRule

    if reach.everything:
        return sa_true()
    clauses = []
    if reach.cohorts:
        clauses.append(RegistrationRule.cohort_id.in_(reach.cohorts))
    if reach.colleges:
        clauses.append(
            RegistrationRule.cohort_id.in_(
                select(Cohort.id)
                .join(Department, Cohort.department_id == Department.id)
                .where(Department.college_id.in_(reach.colleges))
            )
        )
    for values, column in (
        (reach.departments, Cohort.department_id),
        (reach.courses, Cohort.course_id),
        (reach.specializations, Cohort.specialization_id),
    ):
        if values:
            clauses.append(
                RegistrationRule.cohort_id.in_(select(Cohort.id).where(column.in_(values)))
            )
    if not clauses:
        return sa_false()
    return or_(*clauses)


def import_run_scope_clause(reach: Reach):
    """The reach, as a WHERE clause over `import_runs` (B8.1).

    AN IMPORT RUN IS NOT A STUDENT EITHER, which is why it is here beside the
    registrations projection rather than in `policies.py`. It hangs on a COLLEGE
    and on a BATCH, and the four rungs in between are reached through the batch
    — `cohorts` carries `department_id`, `course_id` and `specialization_id`, so
    a department-scoped reviewer sees the run through a subquery over those
    pointers rather than through a copy of the ancestry denormalised onto every
    run. One writer for a batch's ancestry (`_resolve_ancestry` in
    routers/admin.py) is the reason that copy must not exist.

    A STUDENT-SCOPED REACH MATCHES NO RUN, for `registration_scope_clause`'s
    reason turned around: a run is an act performed on a whole batch, and a
    grant naming one student says nothing about whether its holder may see the
    other eighty-five lines in the file.

    A RUN WITH NULL ON BOTH POINTERS IS VISIBLE ONLY TO A REACH OF EVERYTHING,
    and that is deliberate and the opposite of `capability_grants`' NULL rule
    (B1.2), where NULL means programme-wide. Here NULL means "this run's batch
    was deleted and nobody can say which college it was for" — unattributable,
    not universal — and a narrowed holder must not be handed a file of marks
    that nothing places inside their reach.
    """
    from .models.cohort import Cohort
    from .models.data_import import ImportRun

    if reach.everything:
        return sa_true()
    clauses = []
    if reach.colleges:
        clauses.append(ImportRun.college_id.in_(reach.colleges))
    if reach.cohorts:
        clauses.append(ImportRun.cohort_id.in_(reach.cohorts))
    for values, column in (
        (reach.departments, Cohort.department_id),
        (reach.courses, Cohort.course_id),
        (reach.specializations, Cohort.specialization_id),
    ):
        if values:
            clauses.append(
                ImportRun.cohort_id.in_(select(Cohort.id).where(column.in_(values)))
            )
    if not clauses:
        return sa_false()
    return or_(*clauses)


def job_scope_clause(reach: Reach):
    """The reach, as a WHERE clause over `jobs` (B12.1).

    A POSTING IS NOT A STUDENT EITHER, and until B12.1 it hung on nothing at
    all: `console.placement`'s docstring said so in as many words, and the jobs
    sheet was the one console list B1.4 could not narrow. It hangs on a COLLEGE
    and on a COURSE now, and the department in between is reached through the
    course — `academic_courses.department_id` is NOT NULL, so that walk always
    lands, and denormalising a department onto every posting to save the
    subquery would be a second writer for a fact the spine already owns.

    A POSTING WITH NULL ON BOTH POINTERS IS VISIBLE TO EVERY REACH, and this is
    the deliberate OPPOSITE of `import_run_scope_clause` directly above. The two
    NULLs mean different things. An import run with no college is one whose
    batch was deleted — unattributable, and a narrowed holder must not be handed
    a file of marks nothing places inside their fence. A posting with no college
    is one the office published to EVERYBODY: it is the row every deployment has
    today, it is what every student can already see, and hiding it from a
    college admin would mean the narrowing of the jobs sheet reads as the office
    having posted nothing.

    A REACH BELOW THE COURSE — a specialization, a batch or one student — adds
    no clause and therefore sees only the programme-wide postings. That is
    `reaches_target`'s rule rather than a gap: a grant hangs on a rung and
    reaches DOWN, and a posting attached to the whole college sits above a batch
    grant, not inside it. `X-Reep-Scope: narrowed` beside the short list is what
    says so.
    """
    from .models.institution import AcademicCourse
    from .models.job import Job

    if reach.everything:
        return sa_true()
    programme_wide = Job.college_id.is_(None) & Job.course_id.is_(None)
    clauses = [programme_wide]
    if reach.colleges:
        clauses.append(Job.college_id.in_(reach.colleges))
    if reach.courses:
        clauses.append(Job.course_id.in_(reach.courses))
    if reach.departments:
        clauses.append(
            Job.course_id.in_(
                select(AcademicCourse.id).where(
                    AcademicCourse.department_id.in_(reach.departments)
                )
            )
        )
    return or_(*clauses)


def interview_track_scope_clause(reach: Reach):
    """The reach, as a WHERE clause over `interview_tracks` (B5.1).

    A TRACK IS NOT A STUDENT EITHER, and it hangs on its own three pointers —
    college, course, specialization — exactly as a posting hangs on two. So this
    is `job_scope_clause`'s shape, and the reasoning transfers line for line:

    A PROGRAMME-WIDE TRACK — NULL on all three — IS VISIBLE TO EVERY REACH. Those
    are the four rows migration `a4f7d2c80b93` seeds on any deployment with more
    than one college, they are the interviews every student in every college is
    actually sitting, and hiding them from a college admin would make the screen
    say the office has configured no interviewer at all. VISIBLE IS NOT EDITABLE:
    a programme-wide track has an empty ancestry (`ancestry_of_interview_track`),
    so `require_capability(..., target=…)` refuses a scoped holder who tries to
    change one. The list says so per row rather than letting them find out by
    being 403'd after they have typed a persona.

    A reach BELOW the specialization — a batch or one student — adds no clause
    and sees only the programme-wide rows, which is `reaches_target`'s rule: a
    grant hangs on a rung and reaches DOWN, and a track attached to a whole
    course sits above a batch grant rather than inside it.
    """
    from .models.institution import AcademicCourse, AcademicSpecialization
    from .models.interview_track import InterviewTrack

    if reach.everything:
        return sa_true()
    programme_wide = (
        InterviewTrack.college_id.is_(None)
        & InterviewTrack.course_id.is_(None)
        & InterviewTrack.specialization_id.is_(None)
    )
    clauses = [programme_wide]
    if reach.colleges:
        clauses.append(InterviewTrack.college_id.in_(reach.colleges))
    if reach.courses:
        clauses.append(InterviewTrack.course_id.in_(reach.courses))
        clauses.append(
            InterviewTrack.specialization_id.in_(
                select(AcademicSpecialization.id).where(
                    AcademicSpecialization.course_id.in_(reach.courses)
                )
            )
        )
    if reach.specializations:
        clauses.append(InterviewTrack.specialization_id.in_(reach.specializations))
    if reach.departments:
        in_department = select(AcademicCourse.id).where(
            AcademicCourse.department_id.in_(reach.departments)
        )
        clauses.append(InterviewTrack.course_id.in_(in_department))
        clauses.append(
            InterviewTrack.specialization_id.in_(
                select(AcademicSpecialization.id).where(
                    AcademicSpecialization.course_id.in_(in_department)
                )
            )
        )
    return or_(*clauses)
