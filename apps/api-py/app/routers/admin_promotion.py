"""Moving a whole batch through the programme — B4.3 and B4.4.

    POST /admin/cohorts/{id}/promote?dry_run=       everybody up one semester
    POST /admin/cohorts/{id}/graduate?dry_run=      everybody out, as ALUMNI
    POST /admin/cohorts/{id}/ungraduate             undo that, for 30 days
    GET  /admin/cohorts/{id}/promotion-history      what has been done to it

ITS OWN MODULE, mounted under the same `/admin` prefix as `admin_students.py`
and `admin_faculty.py`, because those two are the house precedent and the two
files that would otherwise have grown are ~1700 lines each. The client sees one
flat surface; nothing about the URL says which module answered.

WHAT A PROMOTION IS, AND WHAT IT IS NOT. It moves exactly one number —
`students.current_semester` — and writes one new row per student in
`student_semester_history`. **Nothing is rewritten.** Four other tables carry a
semester number and every one of them is a fact about a semester that has
already happened or about the curriculum:

    semester_results.semester    under uq_semester_result (student, semester)
    subject_marks                reached only through semester_result_id
    english_baselines.semester   under uq_english_baseline_semester
    courses.semester             the subject's slot in the curriculum

Rewriting any of them would collide on its own unique constraint partway
through a batch, leaving a half-promoted cohort and an IntegrityError on screen
— and even if it did not collide, "Ravi scored 8.1 in semester 3" would silently
become a claim about semester 4. `tests/test_admin_promotion.py` pins every one
of those columns byte-identical across a promotion; that guard is the point of
this module, not a formality.

The one legitimate SIDE EFFECT is that promotion unlocks a new English baseline
attempt, because `POST /student/english-baseline/start` keys on
`students.current_semester`. The old attempt is not touched and still renders;
the dialog says so.

WHAT GRADUATION IS. `students.status = GRADUATED`, `users.role = ALUMNI`,
`token_version + 1` (so the account is signed out of every device on its next
request), a history row, and `cohorts.status = GRADUATED`. It creates NO
`alumni_profiles` row, deliberately — `company` there is NOT NULL and mere ROW
EXISTENCE is what `GET /api/alumni/profile`'s `created:` flag reports, which is
the whole trigger for the first-login create-profile form. A row minted here
would either invent a company or suppress that form for ever. The graduate
meets the form, which is what it is for.

AND THE ROLE FLIP ALONE DOES NOT CLOSE THE STUDENT SCREENS. `_require_student`
in routers/student.py and its twin in routers/student_programme.py read ONLY
`session["studentId"]`, and `_payload_for` mints that claim for anybody with a
`students` row — which a graduate still has, because the row IS their record.
Both helpers now check the ROLE as well; without that, graduation would move an
account to ALUMNI in the console and leave roughly forty student endpoints wide
open to it. `tests/test_admin_promotion.py` proves a graduate is refused.

WHY PROMOTE REFUSES AT THE CEILING AND GRADUATE ONLY WARNS. Promoting a student
past `academic_courses.total_semesters` writes a number that is WRONG — semester
9 of an eight-semester programme is not a decision, it is a corrupt row, and
every screen that prints it prints a falsehood. Graduating a batch a semester
early writes nothing invalid; it is a judgement the office is entitled to make,
about people it knows, and `total_semesters` is a nullable column many
deployments will never fill in. So the ceiling is a refusal and the
final-semester and end-date checks are `warn`.

SCOPE. `admin.students` throughout — no new capability key — plus
`assert_batch_within_reach` from `admin_students.py`, imported and never
restated: a scoped holder who reaches only part of a batch is refused the whole
act rather than promoting the part they can see and reporting a count that does
not match the batch. Rule 2's per-student gate is deliberately NOT called in a
loop here, for the same reason `batch_action` does not call it: the answer has
to be one yes or one no for the batch.

RULE 1 is untouched — nothing in this module goes near a model.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session, aliased

from .. import batch_labels
from ..db import get_db
from ..governance import require_capability
from ..identity import get_current_session
from ..models.academics import SemesterResult
from ..models.alumni import AlumniProfile
from ..models.catalogue import StageRule
from ..models.cohort import Cohort
from ..models.institution import STATUS_ACTIVE, AcademicCourse, AcademicSpecialization
from ..models.semester_history import (
    KIND_GRADUATE,
    KIND_PROMOTE,
    KIND_UNGRADUATE,
    StudentSemesterHistory,
)
from ..models.user import (
    STUDENT_STATUS_ACTIVE,
    STUDENT_STATUS_GRADUATED,
    Role,
    Stage,
    Student,
    User,
)
from ..security import note_revocation
from ..semester_bounds import SemesterCeiling, ceiling_for_cohort
from .admin_students import CAPABILITY, _audit, _cohort_or_404, assert_batch_within_reach

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-students"])

#: `cohorts.status` once its people have left. The fourth value on a column
#: whose other three (ACTIVE / DRAFT / ARCHIVED) come from institution.py.
#: GRADUATED is NOT archived and NOT empty: the batch keeps its students,
#: because their rows are the record of who was in it.
COHORT_STATUS_GRADUATED = "GRADUATED"

#: How long a graduation may be taken back. Measured from `created_at` on the
#: history row — WHEN THE BUTTON WAS PRESSED — and never from `effective_on`,
#: which the office routinely backdates to the start of term. A window keyed on
#: a date the caller chooses is not a window.
UNGRADUATE_WINDOW_DAYS = 30


# ------------------------------------------------------------- schemas --


CheckStatus = Literal["ok", "warn", "blocked", "unavailable"]


class PreflightCheck(BaseModel):
    """One line of the dialog's preflight panel.

    `unavailable` IS A REAL STATUS AND NOT A FAILURE. B4.3 asks for an
    "imported results and attendance for the current semester" check;
    `attendance_records` carries `course_code` and `session_no` and NO SEMESTER
    COLUMN AT ALL, so the attendance half is not answerable by this deployment,
    today, by any query. Reporting it as `unavailable` with the reason is the
    only honest answer — a zero, a dash or a green tick would each be a number
    nobody computed, on the screen where the office decides whether a cohort is
    ready to move.
    """

    key: str
    label: str
    status: CheckStatus
    detail: str


class PromotionStudentOut(BaseModel):
    student_id: str
    name: str
    usn: str | None
    from_semester: int
    to_semester: int
    from_stage: str
    #: The stage AFTER the move. Equal to `from_stage` unless `stage_rules`
    #: names one for (course, to_semester) — an empty catalogue makes the stage
    #: half of a promotion a documented no-op, which is what lets B4.3 and B13
    #: land independently.
    to_stage: str
    held_back: bool
    #: Why this student cannot move, or null. Any non-null value here refuses
    #: the whole call: a batch half promoted is the state this module exists to
    #: avoid.
    blocked_reason: str | None = None


class PromoteIn(BaseModel):
    #: The date the office says the move took effect — routinely earlier than
    #: today, because a batch is promoted once the results are out.
    effective_on: date
    #: Student ids to leave where they are. Named explicitly; there is no
    #: "everybody failing" mode, because nothing in this schema knows who is.
    hold_back: list[str] = Field(default_factory=list, max_length=1000)
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: object) -> str | None:
        if v is None:
            return None
        text = " ".join(str(v).split())
        return text or None


class PromoteOut(BaseModel):
    cohort_id: str
    batch: str
    dry_run: bool
    #: How many students moved — and on a dry run, how many WOULD move.
    affected: int
    held_back: int
    #: Students already GRADUATED, who are skipped rather than refused: a batch
    #: half of which has left is a real state after an ungraduate.
    skipped: int
    ceiling: int
    ceiling_source: str
    checks: list[PreflightCheck]
    students: list[PromotionStudentOut]


class GraduateIn(BaseModel):
    effective_on: date
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: object) -> str | None:
        if v is None:
            return None
        text = " ".join(str(v).split())
        return text or None


class GraduateStudentOut(BaseModel):
    student_id: str
    user_id: str
    name: str
    usn: str | None
    semester: int
    role_before: str
    role_after: str
    blocked_reason: str | None = None


class GraduateOut(BaseModel):
    cohort_id: str
    batch: str
    dry_run: bool
    affected: int
    skipped: int
    cohort_status: str
    checks: list[PreflightCheck]
    students: list[GraduateStudentOut]


class UngraduateIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: object) -> str | None:
        if v is None:
            return None
        text = " ".join(str(v).split())
        return text or None


class UngraduateOut(BaseModel):
    cohort_id: str
    batch: str
    dry_run: bool
    affected: int
    cohort_status: str
    #: Graduates who have since filled in an alumni profile. The row is KEPT —
    #: it is theirs, they typed it — and reported, because after this call it is
    #: unreachable (`require_alumni` refuses a STUDENT) and the office should
    #: not discover that from a support call.
    alumni_profiles_kept: int
    students: list[GraduateStudentOut]


class SemesterHistoryOut(BaseModel):
    id: str
    student_id: str
    student_name: str
    usn: str | None
    from_semester: int
    to_semester: int
    effective_on: date
    kind: str
    reason: str | None
    by_user_id: str | None
    by_name: str | None
    created_at: datetime


# ------------------------------------------------------------- helpers --


def _batch_label(db: Session, cohort: Cohort) -> str:
    """How this batch is named in a confirmation and in an audit row.

    The spine comes down the LINKS. `cohorts.name` is the year and nothing else
    since a4e7c92d1f38, so the old `name · batch_label` would make every
    "Promote ..." dialog on the console read "2026-28 · 2026-28" and give
    four identical sentences to four different batches — on the one screen
    whose whole job is to move a specific set of students forward a semester.
    `db.get` on a row this request has already opened costs no round trip.
    """
    course = db.get(AcademicCourse, cohort.course_id) if cohort.course_id else None
    spec = (
        db.get(AcademicSpecialization, cohort.specialization_id)
        if cohort.specialization_id
        else None
    )
    return batch_labels.compose(
        course.name if course else None,
        spec.name if spec else None,
        cohort.name,
        cohort.batch_label,
    )


def _open_batch(db: Session, session: dict, cohort_id: str) -> Cohort:
    """The three gates every write here passes, in the order that is cheapest.

    The capability first (does this account hold Students at all), then the
    batch (does it exist), then the reach (does this account's grant cover ALL
    of it). Asking the reach before the 404 would tell a scoped holder which
    batch ids exist by the shape of the refusal.
    """
    require_capability(db, session, CAPABILITY)
    cohort = _cohort_or_404(db, cohort_id)
    assert_batch_within_reach(db, session, cohort.id)
    return cohort


def _seated(db: Session, cohort_id: str) -> list[tuple[Student, User]]:
    """The batch's ROSTER, which is not every row seated in it.

    A student REMOVED from the roster (`users.deleted_at`) is still seated here,
    and `GET /admin/students` - the list both dialogs count - leaves them out.
    `batch_action` selects the same roster for the same reason: promoting a
    removed student moved their semester behind a dialog that never counted
    them, and graduating one turned them into an ALUMNI account, so Restore
    brought back somebody other than the student the office took off the list.
    """
    rows = db.execute(
        select(Student, User)
        .join(User, Student.user_id == User.id)
        .where(Student.cohort_id == cohort_id, User.deleted_at.is_(None))
        .order_by(User.name, Student.usn)
    ).all()
    return [(s, u) for s, u in rows]


def _stage_rules(db: Session, course_id: str | None) -> dict[int, Stage]:
    """(semester -> stage) for one course. Empty when B13's table is empty,
    which is the shipped state and makes the stage half of a promotion a
    documented no-op rather than a silent one."""
    if not course_id:
        return {}
    rows = db.scalars(select(StageRule).where(StageRule.course_id == course_id)).all()
    return {int(r.semester): r.stage for r in rows}


def _results_check(db: Session, students: list[Student]) -> PreflightCheck:
    """"Have this batch's results been imported for the semester they are in."

    Answerable, unlike the attendance half: `semester_results` carries the
    semester it belongs to. Counted per student against THEIR OWN current
    semester rather than the batch's modal one, because a batch that has been
    held back in parts is exactly the batch this check matters for.
    """
    if not students:
        return PreflightCheck(
            key="results_imported", label="Results imported",
            status="unavailable", detail="No students to check.",
        )
    have = db.scalar(
        select(func.count())
        .select_from(SemesterResult)
        .where(
            tuple_(SemesterResult.student_id, SemesterResult.semester).in_(
                [(s.id, s.current_semester) for s in students]
            )
        )
    ) or 0
    total = len(students)
    if have >= total:
        return PreflightCheck(
            key="results_imported", label="Results imported", status="ok",
            detail=f"All {total} students have results for the semester they are in.",
        )
    return PreflightCheck(
        key="results_imported", label="Results imported", status="warn",
        detail=(
            f"{have} of {total} students have results for the semester they are in. "
            "Promotion does not require them and does not touch them — a result is "
            "a fact about the semester it was scored in."
        ),
    )


def _attendance_check() -> PreflightCheck:
    """NOT ANSWERABLE, and this is the whole of the implementation.

    `attendance_records` has `course_code` and `session_no` and no semester
    column, so "is attendance imported for the current semester" cannot be
    asked of this schema by any query. Inventing a percentage here — or
    silently dropping the row from the panel — is worse than saying so.
    """
    return PreflightCheck(
        key="attendance_imported", label="Attendance imported", status="unavailable",
        detail=(
            "Attendance is not recorded per semester (attendance_records carries a "
            "course code and a session number), so this cannot be checked. Nothing "
            "in a promotion touches attendance."
        ),
    )


def _ceiling_check(ceiling: SemesterCeiling, blocked: int) -> PreflightCheck:
    if blocked:
        return PreflightCheck(
            key="course_length", label="Course length", status="blocked",
            detail=(
                f"{blocked} student{'s are' if blocked != 1 else ' is'} already at "
                f"semester {ceiling.value}, the last of this programme. Graduate the "
                "batch, or hold those students back."
            ),
        )
    if ceiling.from_course:
        return PreflightCheck(
            key="course_length", label="Course length", status="ok",
            detail=f"{ceiling.course_name} runs {ceiling.value} semesters.",
        )
    return PreflightCheck(
        key="course_length", label="Course length", status="unavailable",
        detail=(
            f"This batch names no course with a semester count, so the deployment "
            f"default of {ceiling.value} applies. Set Total semesters on the course "
            "to bound this batch properly."
        ),
    )


def _english_note() -> PreflightCheck:
    return PreflightCheck(
        key="english_baseline", label="English baseline", status="ok",
        detail=(
            "Promotion unlocks a new English baseline attempt (one per semester). "
            "Existing attempts are not touched and keep their semester numbers."
        ),
    )


def _history(
    *, student: Student, from_semester: int, to_semester: int, effective_on: date,
    session: dict, reason: str | None, kind: str,
) -> StudentSemesterHistory:
    return StudentSemesterHistory(
        student_id=student.id,
        from_semester=from_semester,
        to_semester=to_semester,
        effective_on=effective_on,
        by_user_id=session.get("userId"),
        reason=reason,
        kind=kind,
    )


# ------------------------------------------------------------- promote --


@router.post("/cohorts/{cohort_id}/promote", response_model=PromoteOut)
def promote_batch(
    cohort_id: str,
    body: PromoteIn,
    request: Request,
    dry_run: bool = False,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PromoteOut:
    """Everybody in this batch up one semester, except those held back.

    `?dry_run=true` runs every check and every per-student calculation and then
    returns WITHOUT writing — same response shape, `dry_run: true`, `affected`
    reading what would move. The refusals below fire on a dry run too, which is
    the point: the dialog's job is to surface the 422 before the admin presses
    the button, not to hide it until afterwards.

    PROMOTE IS ITS OWN ENDPOINT AND MUST STAY ONE. It is deliberately NOT a
    member of `admin_students.BatchAction`: that Literal is the vocabulary of
    "the single edit, repeated", and promotion is not a single edit — it writes
    a history row per student, reads the course's length, may move a stage and
    carries an effective date. `tests/test_admin_students.py` asserts
    `{"action": "promote"}` on the bulk route is a 422 and that is a contract,
    not an accident.
    """
    cohort = _open_batch(db, session, cohort_id)
    if cohort.status == COHORT_STATUS_GRADUATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{_batch_label(db, cohort)} has graduated. Reverse the graduation first "
                "if this batch is still running."
            ),
        )

    seated = _seated(db, cohort.id)
    if not seated:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="There are no students on this batch's roster.",
        )

    held = set(body.hold_back)
    unknown = held - {s.id for s, _ in seated}
    if unknown:
        # REFUSED, NOT IGNORED. An id that is not in this batch means the admin
        # ticked somebody they think they are holding back; ignoring it promotes
        # that student and reports success.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{len(unknown)} of the students you held back are not seated in this "
                "batch. Nothing was promoted."
            ),
        )

    ceiling = ceiling_for_cohort(db, cohort.id)
    rules = _stage_rules(db, ceiling.course_id)

    rows: list[PromotionStudentOut] = []
    movers: list[tuple[Student, int, Stage]] = []
    skipped = 0
    for student, user in seated:
        if student.status == STUDENT_STATUS_GRADUATED:
            skipped += 1
            continue
        to_semester = student.current_semester + 1
        blocked = None
        if student.id not in held and to_semester > ceiling.value:
            blocked = (
                f"Already at semester {student.current_semester}, the last of "
                f"{ceiling.course_name or 'this programme'}."
            )
        to_stage = rules.get(to_semester, student.current_stage)
        rows.append(PromotionStudentOut(
            student_id=student.id, name=user.name, usn=student.usn,
            from_semester=student.current_semester,
            to_semester=student.current_semester if student.id in held else to_semester,
            from_stage=student.current_stage.value,
            to_stage=student.current_stage.value if student.id in held else to_stage.value,
            held_back=student.id in held,
            blocked_reason=blocked,
        ))
        if blocked is None and student.id not in held:
            movers.append((student, to_semester, to_stage))

    blocked_count = sum(1 for r in rows if r.blocked_reason)
    checks = [
        _ceiling_check(ceiling, blocked_count),
        _results_check(db, [s for s, _ in seated if s.status != STUDENT_STATUS_GRADUATED]),
        _attendance_check(),
        PreflightCheck(
            key="holds", label="Held back", status="ok" if not held else "warn",
            detail=(
                f"{len(held)} student{'s are' if len(held) != 1 else ' is'} held back "
                "and will stay where they are."
                if held else "Nobody is being held back."
            ),
        ),
        PreflightCheck(
            key="stage_rules", label="Stage rules",
            status="ok" if rules else "unavailable",
            detail=(
                f"{sum(1 for r in rows if r.to_stage != r.from_stage)} students change "
                "REEP stage on this promotion."
                if rules else
                "No stage rules are set for this course, so nobody's REEP stage changes."
            ),
        ),
        _english_note(),
    ]

    out = PromoteOut(
        cohort_id=cohort.id, batch=_batch_label(db, cohort), dry_run=dry_run,
        affected=len(movers), held_back=len(held), skipped=skipped,
        ceiling=ceiling.value, ceiling_source=ceiling.source,
        checks=checks, students=rows,
    )
    if blocked_count and not dry_run:
        # ALL OR NOTHING ON APPLY, and REPORTED rather than raised on a dry run.
        # The preflight exists to draw this state — a 422 there would take the
        # per-student table with it and leave the dialog with a sentence where
        # the names ought to be.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{blocked_count} student{'s are' if blocked_count != 1 else ' is'} at "
                f"semester {ceiling.value}, the last of this programme. Graduate the "
                "batch, or hold those students back. Nothing was promoted."
            ),
        )
    if dry_run:
        return out
    if not movers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Every student in this batch is held back or has graduated.",
        )

    for student, to_semester, to_stage in movers:
        db.add(_history(
            student=student, from_semester=student.current_semester,
            to_semester=to_semester, effective_on=body.effective_on,
            session=session, reason=body.reason, kind=KIND_PROMOTE,
        ))
        student.current_semester = to_semester
        student.current_stage = to_stage
    db.flush()
    _audit(
        db, session, request, "cohort", cohort.id, "STUDENTS_PROMOTE", None, None,
        {
            "affected": len(movers), "held_back": sorted(held), "skipped": skipped,
            "effective_on": body.effective_on.isoformat(), "reason": body.reason,
            "student_ids": [s.id for s, _, _ in movers],
        },
    )
    db.commit()
    log.info(
        "batch %s promoted: %d students, %d held back, by %s",
        cohort.code, len(movers), len(held), session.get("email"),
    )
    return out


# ------------------------------------------------------------ graduate --


@router.post("/cohorts/{cohort_id}/graduate", response_model=GraduateOut)
def graduate_batch(
    cohort_id: str,
    body: GraduateIn,
    request: Request,
    dry_run: bool = False,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> GraduateOut:
    """This batch has finished: every student becomes an alumnus.

    THE FOUR WRITES PER STUDENT, and why each is needed rather than derived:

      students.status = GRADUATED    who is a CURRENT student, for rosters,
                                     leaderboards and analytics tiles
      users.role      = ALUMNI       what the ACCOUNT may reach
      token_version + 1              the live session is retired on its next
                                     request; without it the graduate keeps a
                                     STUDENT claim in a cookie that is valid
                                     for its full TTL
      history row, kind=graduate     when it happened, who did it, and the
                                     clock the 30-day reversal is read against

    The status and the role are two facts and not one: the column decides who
    is COUNTED, the role decides what is REACHED, and `students.status`'s own
    comment says so where the next person writing `select(Student)` will read it.

    NO ALUMNI PROFILE IS CREATED — see the module docstring. And no student row
    is deleted: it is the record of what they did here, and deleting it would
    take their marks, their badges and their interview history with it.
    """
    cohort = _open_batch(db, session, cohort_id)
    seated = _seated(db, cohort.id)
    if not seated:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="There are no students on this batch's roster.",
        )

    ceiling = ceiling_for_cohort(db, cohort.id)
    rows: list[GraduateStudentOut] = []
    movers: list[tuple[Student, User]] = []
    skipped = 0
    for student, user in seated:
        if student.status == STUDENT_STATUS_GRADUATED:
            skipped += 1
            continue
        blocked = None
        if user.role is not Role.STUDENT:
            # A `students` row under a non-STUDENT account. Registration's
            # GUARD 2 exists to make this unreachable, so meeting one means
            # something wrote it another way — and flipping that account to
            # ALUMNI would silently demote a mentor. Refuse and name it.
            blocked = (
                f"This account is a {user.role.value}, not a student. Graduating it "
                "would change what it may reach."
            )
        rows.append(GraduateStudentOut(
            student_id=student.id, user_id=user.id, name=user.name, usn=student.usn,
            semester=student.current_semester,
            role_before=user.role.value,
            role_after=user.role.value if blocked else Role.ALUMNI.value,
            blocked_reason=blocked,
        ))
        if blocked is None:
            movers.append((student, user))

    blocked_count = sum(1 for r in rows if r.blocked_reason)
    short = [r for r in rows if not r.blocked_reason and r.semester < ceiling.value]
    end_date = cohort.end_date.date() if cohort.end_date else None
    checks = [
        PreflightCheck(
            key="final_semester", label="Final semester",
            status="ok" if not short else "warn",
            detail=(
                f"All {len(movers)} students are at semester {ceiling.value}, the last "
                f"of this programme."
                if not short else
                f"{len(short)} of {len(movers)} students are below semester "
                f"{ceiling.value}, the last of this programme. Graduating them early "
                "is allowed; nothing about their record changes except that they stop "
                "being current students."
            ),
        ),
        PreflightCheck(
            key="end_date", label="Expected completion",
            status="ok" if end_date and end_date <= body.effective_on else "warn",
            detail=(
                f"The batch was due to finish on {end_date}."
                if end_date and end_date <= body.effective_on
                else f"The batch is not due to finish until {end_date}."
                if end_date else "This batch has no expected completion date."
            ),
        ),
        PreflightCheck(
            key="sessions", label="Sign-out", status="ok",
            detail=(
                f"{len(movers)} accounts are signed out of every device and sign back "
                "in as alumni. The student screens close; the alumni profile form opens."
            ),
        ),
        PreflightCheck(
            key="reversal", label="Reversible", status="ok",
            detail=f"This can be undone for {UNGRADUATE_WINDOW_DAYS} days.",
        ),
    ]

    out = GraduateOut(
        cohort_id=cohort.id, batch=_batch_label(db, cohort), dry_run=dry_run,
        affected=len(movers), skipped=skipped,
        cohort_status=cohort.status if dry_run else COHORT_STATUS_GRADUATED,
        checks=checks, students=rows,
    )
    if blocked_count and not dry_run:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{blocked_count} student record{'s in' if blocked_count != 1 else ' in'} "
                "this batch belongs to an account that is not a student. Nothing was "
                "graduated."
            ),
        )
    if dry_run:
        return out
    if not movers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Every student in this batch has already graduated.",
        )

    revoked: list[tuple[str, int]] = []
    for student, user in movers:
        db.add(_history(
            student=student, from_semester=student.current_semester,
            to_semester=student.current_semester, effective_on=body.effective_on,
            session=session, reason=body.reason, kind=KIND_GRADUATE,
        ))
        student.status = STUDENT_STATUS_GRADUATED
        user.role = Role.ALUMNI
        user.token_version = int(user.token_version or 0) + 1
        revoked.append((user.id, user.token_version))
    cohort.status = COHORT_STATUS_GRADUATED
    db.flush()
    _audit(
        db, session, request, "cohort", cohort.id, "STUDENTS_GRADUATE", None, None,
        {
            "affected": len(movers), "skipped": skipped,
            "effective_on": body.effective_on.isoformat(), "reason": body.reason,
            "student_ids": [s.id for s, _ in movers],
            "user_ids": [u.id for _, u in movers],
        },
    )
    db.commit()
    # AFTER THE COMMIT, never before: the cache says "this worker refuses tokens
    # below version N", and seeding it for a bump that then rolls back would
    # sign a batch of students out of an account change that never happened.
    for user_id, version in revoked:
        note_revocation(user_id, version)
    log.info(
        "batch %s graduated: %d students by %s", cohort.code, len(movers), session.get("email")
    )
    return out


@router.post("/cohorts/{cohort_id}/ungraduate", response_model=UngraduateOut)
def ungraduate_batch(
    cohort_id: str,
    body: UngraduateIn,
    request: Request,
    dry_run: bool = False,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> UngraduateOut:
    """Take a graduation back, within the window.

    Graduating the wrong batch is a two-click mistake that signs sixty people
    out and closes every screen they use, and the console has no other way to
    put a role back. So it is reversible — for `UNGRADUATE_WINDOW_DAYS`, read
    off the history row's `created_at`.

    ALL OR NOTHING ON THE WINDOW. If any graduate in this batch was graduated
    longer ago than the window allows, the whole call is refused rather than
    reversing the recent half: a batch split between ALUMNI and STUDENT
    accounts is a state no screen in this product renders sensibly.

    WHAT IT CANNOT UNDO is an `alumni_profiles` row the person has filled in
    since. That row is theirs and is kept; it is counted in the response,
    because after this call `require_alumni` refuses them and the profile they
    typed is out of reach until they graduate again.
    """
    cohort = _open_batch(db, session, cohort_id)
    seated = _seated(db, cohort.id)
    graduates = [(s, u) for s, u in seated if s.status == STUDENT_STATUS_GRADUATED]
    if not graduates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{_batch_label(db, cohort)} has no graduated students to reverse.",
        )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=UNGRADUATE_WINDOW_DAYS)
    latest = {
        sid: when
        for sid, when in db.execute(
            select(
                StudentSemesterHistory.student_id,
                func.max(StudentSemesterHistory.created_at),
            )
            .where(
                StudentSemesterHistory.student_id.in_([s.id for s, _ in graduates]),
                StudentSemesterHistory.kind == KIND_GRADUATE,
            )
            .group_by(StudentSemesterHistory.student_id)
        ).all()
    }
    stale = [
        s.id for s, _ in graduates
        if latest.get(s.id) is None or latest[s.id] < cutoff
    ]
    if stale:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{len(stale)} of these graduations are older than "
                f"{UNGRADUATE_WINDOW_DAYS} days (or were not recorded here), so they "
                "can no longer be reversed from the console. Nothing was changed."
            ),
        )

    rows = [
        GraduateStudentOut(
            student_id=s.id, user_id=u.id, name=u.name, usn=s.usn,
            semester=s.current_semester,
            role_before=u.role.value, role_after=Role.STUDENT.value,
        )
        for s, u in graduates
    ]
    kept = db.scalar(
        select(func.count())
        .select_from(AlumniProfile)
        .where(AlumniProfile.user_id.in_([u.id for _, u in graduates]))
    ) or 0

    out = UngraduateOut(
        cohort_id=cohort.id, batch=_batch_label(db, cohort), dry_run=dry_run,
        affected=len(graduates),
        cohort_status=cohort.status if dry_run else STATUS_ACTIVE,
        alumni_profiles_kept=int(kept), students=rows,
    )
    if dry_run:
        return out

    revoked: list[tuple[str, int]] = []
    for student, user in graduates:
        db.add(_history(
            student=student, from_semester=student.current_semester,
            to_semester=student.current_semester, effective_on=now.date(),
            session=session, reason=body.reason, kind=KIND_UNGRADUATE,
        ))
        student.status = STUDENT_STATUS_ACTIVE
        user.role = Role.STUDENT
        user.token_version = int(user.token_version or 0) + 1
        revoked.append((user.id, user.token_version))
    cohort.status = STATUS_ACTIVE
    db.flush()
    _audit(
        db, session, request, "cohort", cohort.id, "STUDENTS_UNGRADUATE", None, None,
        {
            "affected": len(graduates), "reason": body.reason,
            "alumni_profiles_kept": int(kept),
            "student_ids": [s.id for s, _ in graduates],
            "user_ids": [u.id for _, u in graduates],
        },
    )
    db.commit()
    for user_id, version in revoked:
        note_revocation(user_id, version)
    log.info(
        "batch %s ungraduated: %d students by %s",
        cohort.code, len(graduates), session.get("email"),
    )
    return out


# -------------------------------------------------------------- history --


@router.get("/cohorts/{cohort_id}/promotion-history", response_model=list[SemesterHistoryOut])
def promotion_history(
    cohort_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[SemesterHistoryOut]:
    """Every semester move recorded against the students seated in this batch.

    Newest first. Read through the same three gates the writes use, because it
    names students: `assert_batch_within_reach` refuses a holder who reaches
    only part of the batch rather than handing back the part they may see —
    "there are no promotions on record" and "you may not see them" are opposite
    facts, and a filtered list renders as the first.
    """
    cohort = _open_batch(db, session, cohort_id)
    actor = aliased(User)
    rows = db.execute(
        select(
            StudentSemesterHistory, User.name, Student.usn, actor.name,
        )
        .join(Student, Student.id == StudentSemesterHistory.student_id)
        .join(User, User.id == Student.user_id)
        .outerjoin(actor, actor.id == StudentSemesterHistory.by_user_id)
        .where(Student.cohort_id == cohort.id)
        .order_by(StudentSemesterHistory.created_at.desc())
    ).all()
    return [
        SemesterHistoryOut(
            id=h.id, student_id=h.student_id, student_name=name, usn=usn,
            from_semester=h.from_semester, to_semester=h.to_semester,
            effective_on=h.effective_on, kind=h.kind, reason=h.reason,
            by_user_id=h.by_user_id, by_name=by_name, created_at=h.created_at,
        )
        for h, name, usn, by_name in rows
    ]
