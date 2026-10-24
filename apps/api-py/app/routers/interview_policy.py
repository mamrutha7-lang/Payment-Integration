"""The college's interview policy — the office's CRUD, the student's card, and
the cap reset (B6.1, B6.4).

Three surfaces, one table:

  PUT  /api/admin/interview-policies/{college_id}                 the default
  PUT  /api/admin/interview-policies/{college_id}/{course_id}     one course
  GET  /api/admin/interview-policies/{college_id}                 both, to edit
  POST /api/admin/students/{student_id}/interview-cap/reset       B6.4
  GET  /api/interview/policy                                      the student

WHY A MODULE OF ITS OWN. `09-coding-standards.md` names this file: one concept,
its service functions beside it (`app/interview_policy.py`) and nothing else.
The alternative was three more endpoints in `app/routers/admin.py`, which is
already the file every admin change touches — and the student card does not
belong there at all.

THE CAPABILITY IS `admin.interviews`, AND THIS MODULE IS WHY IT EXISTS. The key
was in 04-backend-changes.md's college-admin set for months while nothing
defined it; `app/routers/admin.py` carried a note saying 4c must add it to the
catalogue, to `COLLEGE_ADMIN_CAPABILITIES` and to a real `require_capability`
call IN ONE COMMIT, because a name in that tuple with no catalogue entry is a
KeyError thrown in front of whoever is appointing a college admin. The three
calls below are the third of those three.

IT CARRIES PII, so a DEPUTY's grant of it lands `pending_approval` and holds
nothing until a different `admin.governance` holder approves it (B2.4). The
Main Admin holds it by baseline, and the Main Admin's own grants of it are live
at once (`initial_approval_state`, 2026-09-16); a college admin appointed by a
deputy will find these endpoints 403 until the office approves, which is the
honest consequence the Colleges screen reports as "N awaiting approval".

THE SCOPE FENCE IS THE REACH, and it is the same shape `console.set_criteria`
uses: a college-scoped holder writes their OWN college's policy and not
another's, and the course is checked through ITS OWN college rather than by
taking the caller's word that it is theirs. The cap reset names a STUDENT in the
path, so it passes `ancestry_of_student` as the `target` — "may you do this at
all" and "may you do it HERE" are different questions and B1.2 answers the
second one only when it is asked.

RULE 1 is not in play (nothing here reaches a model). RULE 2 is: the cap reset
is a write ABOUT one student, so it goes through `_assert_can_access_student`
as well as the capability — the two fences are checked separately and on
purpose, and a capability can never relax the student filter.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..config import settings
from ..db import get_db
from ..governance import ancestry_of_student, require_capability
from ..identity import get_current_session
from ..interview_matrix import SPECIALIZATIONS
from ..interview_tracks import _visible_tracks, enabled_tracks
from ..interview_policy import (
    CAP_WINDOW,
    EffectivePolicy,
    cap_counts,
    cap_window_start,
    policy_for_student,
    resolve_policy,
    spine_of_student,
)
from ..models.governance import ScopeLevel
from ..models.institution import (
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from ..models.interview import InterviewCapReset, InterviewConsent
from ..models.interview_policy import InterviewPolicy
from ..models.interview_track import InterviewTrack
from ..models.user import Role, Student, User
from ..policies import scope_filter
from .mentor import _assert_can_access_student

log = logging.getLogger(__name__)

#: The key this module adds to the catalogue. Named once so the three call
#: sites and the tests cannot drift from each other.
CAPABILITY = "admin.interviews"

admin_router = APIRouter(prefix="/api/admin", tags=["interview-policy"])
student_router = APIRouter(prefix="/api/interview", tags=["interview-policy"])

#: How long a reset reason may be. Long enough for a sentence, short enough that
#: the column is not a place to paste a support thread.
_MAX_REASON_CHARS = 500


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------
class PolicyOut(BaseModel):
    """One stored row, as the console edits it."""

    id: str
    college_id: str
    course_id: str | None
    course_name: str | None = None
    store_transcript: bool
    store_audio: bool
    retention_days: int
    daily_cap: int
    attempt_cap: int
    time_limit_seconds: int
    updated_by: str | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None


class PolicyIn(BaseModel):
    """A partial edit. EVERY FIELD IS OPTIONAL AND AN OMITTED ONE KEEPS THE
    NUMBER THAT IS ALREADY GOVERNING THESE STUDENTS — never the default.

    The same rule `console.set_criteria` follows: a console that sent three
    fields and silently reset the other four would change a decision nobody
    made. The bounds below mirror `ck_interview_policy_bounds` so the refusal is
    a 422 naming the field rather than a 500 from the database, and the ONE
    cross-field rule (`attempt_cap >= daily_cap`) is checked after the merge —
    it is a rule about the ROW, not about the request, and a request that raises
    `daily_cap` alone must be judged against the stored `attempt_cap`.
    """

    store_transcript: bool | None = None
    store_audio: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    daily_cap: int | None = Field(default=None, ge=1, le=100)
    attempt_cap: int | None = Field(default=None, ge=1, le=500)
    time_limit_seconds: int | None = Field(default=None, ge=60, le=3600)


class PolicySheetOut(BaseModel):
    """Everything the policy screen needs for one college in one read."""

    college_id: str
    college_name: str | None
    #: The college's default row, or null when it has never been configured —
    #: which is a real and reachable state, not an empty row full of defaults.
    default: PolicyOut | None
    #: Per-course overrides, deepest first as the screen lists them.
    courses: list[PolicyOut]
    #: What a student at this college gets TODAY if nothing above applies. The
    #: screen renders it as "not configured — these numbers are in force".
    effective_default: "EffectivePolicyOut"
    #: The OPERATOR's switch, `INTERVIEW_RECORDING_ENABLED` (2026-09-17). The
    #: policy's "Allow voice recording" is one of THREE gates on a recording
    #: (`app/interview_audio.py::recorder_or_reason`), and this one is set on
    #: the server, not on this screen: a college that ticks the box on a
    #: deployment where it is off records nothing and has no way to know why.
    #: Served so the card can say so beside the box rather than let the office
    #: tick it and wait.
    recording_enabled_on_server: bool = False


class TrackOut(BaseModel):
    code: str
    label: str


class CapUsageOut(BaseModel):
    """What the student has spent, and against which ceiling.

    BOTH NUMBERS ARE SHOWN because both can refuse the interview and they refuse
    it for different reasons. `completed` is the practice allowance; `attempts`
    counts every session row, finished or not, because each one billed an
    upstream handshake — the comment `_open_records` has carried since the cap
    was written.
    """

    completed: int
    attempts: int
    daily_cap: int
    attempt_cap: int
    window_start: datetime
    #: True when an admin has given this student attempts back inside the
    #: current window, so the card can say why the count looks low.
    reset_applied: bool


class EffectivePolicyOut(BaseModel):
    store_transcript: bool
    store_audio: bool
    retention_days: int
    daily_cap: int
    attempt_cap: int
    time_limit_seconds: int
    #: 'default' | 'college' | 'course'. See `EffectivePolicy.source`.
    source: str


class StudentPolicyOut(BaseModel):
    """The card behind the Start button (B6.1, B5.3, B6.4, B17)."""

    #: The consent wording this build asks to be acknowledged. The client posts
    #: it back to POST /api/interview/consent at Start; it is NOT a form.
    consent_version: str
    provider_label: str
    policy: EffectivePolicyOut
    usage: CapUsageOut
    #: True when the caller already holds a live acknowledgement of
    #: `consent_version` whose scopes match the policy above. The client may
    #: still post — the endpoint is idempotent — but this is what lets the
    #: screen avoid a round trip it does not need.
    acknowledged: bool
    #: The OPERATOR's `INTERVIEW_RECORDING_ENABLED` (2026-09-17), the first of
    #: the three gates in `recorder_or_reason`. The room's "recording on/off"
    #: line used to read the student's OWN consent row, a copy of the policy
    #: taken on the day they agreed: it said "on" over an interview the
    #: recorder had refused. The label reads the policy and this flag now, so
    #: a college that ticked the box on a server that cannot record does not
    #: tell its students they are being recorded.
    recording_enabled_on_server: bool = False
    #: B5.3: the track this student's batch implies, or null when the batch
    #: names no specialization (or names one no track is mapped to). NULL is a
    #: real answer and the picker stays — it must not be filled with a guess.
    default_track: str | None
    tracks: list[TrackOut]


class CapResetIn(BaseModel):
    reason: str = Field(min_length=1, max_length=_MAX_REASON_CHARS)


class CapResetOut(BaseModel):
    id: str
    student_id: str
    at: datetime
    reason: str
    by_user_id: str | None
    usage: CapUsageOut


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _policy_out(row: InterviewPolicy, *, course_name: str | None,
                updated_by_name: str | None) -> PolicyOut:
    return PolicyOut(
        id=row.id,
        college_id=row.college_id,
        course_id=row.course_id,
        course_name=course_name,
        store_transcript=bool(row.store_transcript),
        store_audio=bool(row.store_audio),
        retention_days=int(row.retention_days),
        daily_cap=int(row.daily_cap),
        attempt_cap=int(row.attempt_cap),
        time_limit_seconds=int(row.time_limit_seconds),
        updated_by=row.updated_by,
        updated_by_name=updated_by_name,
        updated_at=row.updated_at,
    )


def _effective_out(policy: EffectivePolicy) -> EffectivePolicyOut:
    return EffectivePolicyOut(
        store_transcript=policy.store_transcript,
        store_audio=policy.store_audio,
        retention_days=policy.retention_days,
        daily_cap=policy.daily_cap,
        attempt_cap=policy.attempt_cap,
        time_limit_seconds=policy.time_limit_seconds,
        source=policy.source,
    )


def _assert_reach(db: Session, session: dict, college_id: str,
                  course_id: str | None) -> None:
    """The B1.2 fence, in the shape `console.set_criteria` already established.

    THE COURSE IS CHECKED THROUGH ITS OWN COLLEGE, never by taking the caller's
    word that it is theirs: a college-scoped holder naming another college's
    course would otherwise write a policy for students they cannot see, and the
    scope check would have been reduced to "did you also send a college_id".
    """
    reach = scope_filter(db, session, CAPABILITY)
    if reach.everything:
        return
    if reach.nothing:
        # `nothing` and `everything` must never render the same. A holder whose
        # only grant was scoped to a department that has since been deleted
        # reaches nobody, and answering "no restriction" there would be the
        # widest possible failure of the narrowest possible grant.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your Interviews capability does not currently reach any "
                "college. An administrator can widen it in Governance."
            ),
        )
    if course_id:
        # A COURSE-scoped holder reaches their own course and nothing above it,
        # so the college check is not asked of them — it would refuse the exact
        # person the scope was written for. The course is still checked through
        # ITS OWN college rather than by taking the caller's word, or a
        # college-scoped holder naming another college's course would write a
        # policy for students they cannot see.
        if course_id in reach.courses:
            return
        owning_college = db.scalar(
            select(Department.college_id)
            .select_from(AcademicCourse)
            .join(Department, AcademicCourse.department_id == Department.id)
            .where(AcademicCourse.id == course_id)
        )
        if owning_college in reach.colleges:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your Interviews capability does not reach that course.",
        )
    # The college's DEFAULT row governs every course under it, including ones a
    # course-scoped holder cannot see, so only a college-level reach may write it.
    if college_id not in reach.colleges:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your Interviews capability does not reach that college.",
        )


def _require_college(db: Session, college_id: str) -> College:
    college = db.get(College, college_id)
    if college is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="College not found."
        )
    return college


def _require_course(db: Session, college_id: str, course_id: str) -> AcademicCourse:
    course = db.get(AcademicCourse, course_id)
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found."
        )
    owning_college = db.scalar(
        select(Department.college_id).where(Department.id == course.department_id)
    )
    if owning_college != college_id:
        # 404 rather than 422: the row named by THIS path does not exist. A
        # course that belongs to another college is not this college's course,
        # and saying so any more precisely would confirm the id to somebody
        # probing for it.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That course does not belong to this college.",
        )
    return course


def _usage_for(
    db: Session,
    student_id: str,
    now: datetime,
    policy: EffectivePolicy | None = None,
) -> CapUsageOut:
    """The two counts and the two ceilings. The policy is passed in where the
    caller has already resolved it — two resolutions of the same thing in one
    request is two chances to disagree about which row won."""
    policy = policy or policy_for_student(db, student_id)
    since = cap_window_start(db, student_id, now)
    completed, attempts = cap_counts(db, student_id, since)
    return CapUsageOut(
        completed=completed,
        attempts=attempts,
        daily_cap=policy.daily_cap,
        attempt_cap=policy.attempt_cap,
        window_start=since,
        # The window only ever moves forward off `now - 24 h` when a reset did
        # it, so this comparison IS "a reset applies" — no second query.
        reset_applied=since > now - CAP_WINDOW,
    )


def _track_list(db: Session, college_id: str | None) -> list[TrackOut]:
    """The tracks this student may be offered, in display order.

    DELEGATED, NOT RE-JOINED. `interview_tracks.enabled_tracks` already answers
    "this college's rows plus the programme-wide ones, its own shadowing the
    latter by code" — and `catalogue_codes` beside it already decided that the
    constant's four are UNIONED IN rather than used as an either/or fallback,
    because a deployment that has added `ops` but never seeded the four still
    answers `?specialization=hr`. A second copy here would be the copy that
    stops tracking the first, on the list a student picks their interviewer
    from.

    The label for a code with no row comes off `SPECIALIZATIONS`, which is where
    the socket would get it too.

    A DISABLED TRACK IS NOT UNIONED BACK IN, and that is the one place this
    cannot simply copy `catalogue_codes`. `resolve_specialization` refuses a code
    whose row is disabled — deliberately, because "disabling `hr` must actually
    stop HR interviews, and falling back would make the switch a no-op for
    precisely the four tracks that have a constant". So a code with a row is
    SHADOWED here whatever that row's `enabled` says; offering it would put a
    button on the screen that the socket answers with close 4010.
    """
    rows = enabled_tracks(db, college_id=college_id)
    out = [TrackOut(code=r.code.strip().lower(), label=r.label) for r in rows]
    # Every code that HAS a row for this college, enabled or not. Read through
    # `interview_tracks`' own visibility query rather than re-written here: the
    # "own rows plus the programme-wide ones" rule is one rule, and the import
    # of a private name is the lesser evil `interview_records.py` already takes
    # with `_assert_can_access_student` — a second copy is the copy that stops
    # tracking the first.
    shadowed = {
        (code or "").strip().lower()
        for code in db.scalars(
            _visible_tracks(college_id).with_only_columns(InterviewTrack.code)
        )
    }
    out.extend(
        TrackOut(code=key, label=spec.label)
        for key, spec in SPECIALIZATIONS.items()
        if key not in shadowed
    )
    return out


def _default_track(db: Session, student_id: str, available: list[TrackOut]) -> str | None:
    """B5.3: the track this student's BATCH implies, or None.

    FOUR ways to match, most specific first: a track row mapped to the batch's
    SPECIALIZATION, the specialization's own `code` read as a track code, then
    the same two against the batch's COURSE. NONE OF THEM IS A GUESS — every one
    is an exact, case-folded match on a code somebody typed on purpose. If
    nothing matches, this is None and the picker stays. Filling it in with "hr
    because it is first" would assess a Finance student against an HR bar with
    nothing on the screen to say so, which is the exact failure the socket
    refuses `?specialization=` typos to avoid.

    THE COURSE RUNG WAS MISSING AND `interview_tracks.course_id` HAS EXISTED THE
    WHOLE TIME. The column is written by the admin screen, returned by the track
    API and rendered as a mapping -- and nothing read it, so a track mapped to a
    course looked mapped, reported mapped, and preselected nothing. B6.6's shape
    exactly, and found the same way: by trying to file a real catalogue.

    It is not a rare shape either. A specialization is OPTIONAL
    (`HIERARCHY_LEVELS`), and a two-year programme that IS the qualification --
    BGSCET's Digital Marketing and Logistics & Supply Chain MBAs -- has no
    specialization under it to hang a track on, because there is nothing to
    specialise into. Reading the specialization alone meant every student on
    such a course fell through to the generic interview, which has no wrap-up
    phase and therefore CANNOT BE SCORED. That is the same dead end
    `grant_access --department-id` was added to close, reached from the
    catalogue instead of from the account.

    Specialization still wins where a batch names one, because it is the more
    specific statement -- the same precedence `ancestry_of_student` applies to
    the two department pointers.
    """
    pairs = dict(ancestry_of_student(db, student_id))
    codes = {t.code for t in available}

    # (rung, the model whose own `code` stands in, the track column mapped to it)
    rungs = (
        (ScopeLevel.SPECIALIZATION, AcademicSpecialization, InterviewTrack.specialization_id),
        (ScopeLevel.COURSE, AcademicCourse, InterviewTrack.course_id),
    )
    for level, model, track_column in rungs:
        rung_id = pairs.get(level)
        if not rung_id:
            continue
        mapped = db.scalar(
            select(InterviewTrack.code).where(
                track_column == rung_id,
                InterviewTrack.enabled.is_(True),
            )
        )
        if mapped and mapped.strip().lower() in codes:
            return mapped.strip().lower()
        own_code = db.scalar(select(model.code).where(model.id == rung_id))
        if own_code and own_code.strip().lower() in codes:
            return own_code.strip().lower()
    return None


# ---------------------------------------------------------------------------
# Admin — the policy
# ---------------------------------------------------------------------------
@admin_router.get(
    "/interview-policies/{college_id}", response_model=PolicySheetOut
)
def read_policies(
    college_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PolicySheetOut:
    """Everything the policy screen shows for one college.

    `default: null` is a REAL ANSWER and the screen must render it as "not
    configured", never as a row holding the defaults: the two are different
    facts, and only one of them is a decision somebody made.
    """
    require_capability(db, session, CAPABILITY)
    _assert_reach(db, session, college_id, None)
    college = _require_college(db, college_id)

    rows = list(
        db.scalars(
            select(InterviewPolicy)
            .where(InterviewPolicy.college_id == college_id)
            .order_by(InterviewPolicy.course_id.nullsfirst())
        )
    )
    course_names = {
        cid: name
        for cid, name in db.execute(
            select(AcademicCourse.id, AcademicCourse.name).where(
                AcademicCourse.id.in_([r.course_id for r in rows if r.course_id] or [""])
            )
        )
    }
    editor_names = {
        uid: name
        for uid, name in db.execute(
            select(User.id, User.name).where(
                User.id.in_([r.updated_by for r in rows if r.updated_by] or [""])
            )
        )
    }
    default_row = next((r for r in rows if r.course_id is None), None)
    return PolicySheetOut(
        college_id=college_id,
        college_name=college.name,
        default=None
        if default_row is None
        else _policy_out(
            default_row,
            course_name=None,
            updated_by_name=editor_names.get(default_row.updated_by or ""),
        ),
        courses=[
            _policy_out(
                r,
                course_name=course_names.get(r.course_id or ""),
                updated_by_name=editor_names.get(r.updated_by or ""),
            )
            for r in rows
            if r.course_id is not None
        ],
        effective_default=_effective_out(
            resolve_policy(db, college_id=college_id, course_id=None)
        ),
        recording_enabled_on_server=bool(settings.interview_recording_enabled),
    )


def _write_policy(
    *,
    db: Session,
    session: dict,
    request: Request,
    college_id: str,
    course_id: str | None,
    body: PolicyIn,
) -> PolicyOut:
    """The upsert both PUTs share.

    ONE FUNCTION AND NOT TWO ROUTES WITH THE SAME BODY: the (college, course)
    row and the (college, NULL) default row differ in exactly one bind
    parameter, and two copies is two places for the bounds check to disagree.
    """
    require_capability(db, session, CAPABILITY)
    _assert_reach(db, session, college_id, course_id)
    _require_college(db, college_id)
    if course_id:
        _require_course(db, college_id, course_id)

    existing = db.scalar(
        select(InterviewPolicy).where(
            InterviewPolicy.college_id == college_id,
            InterviewPolicy.course_id.is_(None)
            if course_id is None
            else InterviewPolicy.course_id == course_id,
        )
    )
    # What an omitted field keeps. For an EXISTING row that is the row's own
    # value; for a new one it is what these students are governed by RIGHT NOW —
    # the college default if there is one, else `config.py`. A new course row
    # that silently snapped four fields back to the deployment defaults would
    # undo the college's decision as a side effect of narrowing one number.
    base = (
        _effective_out(resolve_policy(db, college_id=college_id, course_id=course_id))
        if existing is None
        else EffectivePolicyOut(
            store_transcript=bool(existing.store_transcript),
            store_audio=bool(existing.store_audio),
            retention_days=int(existing.retention_days),
            daily_cap=int(existing.daily_cap),
            attempt_cap=int(existing.attempt_cap),
            time_limit_seconds=int(existing.time_limit_seconds),
            source="row",
        )
    )
    fields = (
        "store_transcript",
        "store_audio",
        "retention_days",
        "daily_cap",
        "attempt_cap",
        "time_limit_seconds",
    )
    values = {
        f: (getattr(body, f) if getattr(body, f) is not None else getattr(base, f))
        for f in fields
    }
    if values["attempt_cap"] < values["daily_cap"]:
        # 422 and not a database error: the caller holds the capability and the
        # request is well formed, it is the ROW that cannot take these numbers.
        # Checked after the merge because it is a rule about the row — raising
        # `daily_cap` alone has to be judged against the stored `attempt_cap`.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"The attempt ceiling ({values['attempt_cap']}) must be at least "
                f"the daily practice cap ({values['daily_cap']}); otherwise the "
                "practice cap can never be reached and the students' allowance "
                "silently becomes the spend ceiling."
            ),
        )

    before = None if existing is None else {f: getattr(existing, f) for f in fields}
    if existing is None:
        row = InterviewPolicy(
            college_id=college_id,
            course_id=course_id,
            updated_by=session.get("userId"),
            **values,
        )
        db.add(row)
    else:
        row = existing
        for field, value in values.items():
            setattr(row, field, value)
        row.updated_by = session.get("userId")
    try:
        db.flush()
    except IntegrityError:
        # Two admins saving the same rung at once. The row exists; re-reading is
        # the honest answer and the loser's numbers are simply not the ones that
        # landed — the same shape `conversations.ensure_conversation` uses.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Somebody else saved this policy a moment ago. Reload and apply "
                "your change to what is there now."
            ),
        )

    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="interview_policy",
        entity_id=row.id,
        action="INTERVIEW_POLICY_SET",
        before=before,
        after={"college_id": college_id, "course_id": course_id, **values},
        event_type="interview.policy.set",
        payload={
            "policy_id": row.id,
            "college_id": college_id,
            "course_id": course_id,
        },
    )
    db.commit()
    db.refresh(row)
    course_name = None
    if course_id:
        course = db.get(AcademicCourse, course_id)
        course_name = course.name if course else None
    editor = db.get(User, row.updated_by) if row.updated_by else None
    return _policy_out(
        row, course_name=course_name, updated_by_name=editor.name if editor else None
    )


@admin_router.put(
    "/interview-policies/{college_id}", response_model=PolicyOut
)
def put_college_policy(
    college_id: str,
    body: PolicyIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PolicyOut:
    """The college's DEFAULT policy — what every course inherits.

    LOWERING `retention_days` APPLIES TO INTERVIEWS HELD AFTER THIS SAVE, and
    the console must say so rather than implying a sweep.
    `interview_sessions.retention_until` is STAMPED AT OPEN from the window in
    force then, precisely so that a number changed here cannot retroactively
    re-date interviews a student was already promised 180 days for.

    NARROWING A STORAGE SCOPE APPLIES THE SAME WAY, with one exception that is
    written down here because it is the one a reader will look for: an interview
    that is ALREADY RUNNING keeps the scopes it opened under (its acknowledgement
    is pinned by `interview_sessions.consent_id` and its recorder was built at
    open). What ends a running session with 4014 is the student's own next
    acknowledgement finding fewer scopes than the one it supersedes — see
    `routers/interview.py::_make_heartbeat`. An edit here that removes a scope
    therefore takes effect on the NEXT interview, within seconds for anybody not
    already speaking.
    """
    return _write_policy(
        db=db,
        session=session,
        request=request,
        college_id=college_id,
        course_id=None,
        body=body,
    )


@admin_router.put(
    "/interview-policies/{college_id}/{course_id}", response_model=PolicyOut
)
def put_course_policy(
    college_id: str,
    course_id: str,
    body: PolicyIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PolicyOut:
    """One course's policy, which shadows the college's default row."""
    return _write_policy(
        db=db,
        session=session,
        request=request,
        college_id=college_id,
        course_id=course_id,
        body=body,
    )


# ---------------------------------------------------------------------------
# Admin — the cap reset (B6.4)
# ---------------------------------------------------------------------------
@admin_router.post(
    "/students/{student_id}/interview-cap/reset",
    response_model=CapResetOut,
    status_code=status.HTTP_201_CREATED,
)
def reset_interview_cap(
    student_id: str,
    body: CapResetIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CapResetOut:
    """Give one student their practice attempts back, WITH A REASON IN WORDS.

    THE REASON IS NOT OPTIONAL, and the precedent is B3.1's disable: this is the
    other console action whose effect is invisible from the console a day later
    — the window rolls, the count falls back to normal, and nothing on any
    screen says why a student had eleven interviews on Tuesday. `min_length=1`
    on the payload plus `ck_interview_cap_reset_reason` in the database, because
    a blank reason typed as a space is still no reason.

    BOTH FENCES, SEPARATELY. `require_capability(..., target=ancestry)` answers
    "may you do this, and HERE"; `_assert_can_access_student` answers rule 2's
    stricter question about a MENTOR's own group. Neither replaces the other and
    a capability can never relax the student filter.

    IT DOES NOT ZERO A COUNTER, because there is no counter: the cap is a
    rolling 24-hour COUNT, so the reset is a lower bound on the window it is
    taken over. A student mid-day never loses attempts already counted — the
    bound only moves forward — and the reset expires with the window, which is
    correct for a rolling allowance rather than a balance.
    """
    require_capability(
        db, session, CAPABILITY, target=ancestry_of_student(db, student_id)
    )
    _assert_can_access_student(session, student_id, db)
    student = db.get(Student, student_id)
    if student is None:
        # Unreachable in practice — `_assert_can_access_student` 404s first —
        # but the row is what the FK needs and a None here would be a 500.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found."
        )
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Say why you are giving this student their attempts back. It is "
                "the only record of the decision."
            ),
        )
    now = datetime.now(timezone.utc)
    row = InterviewCapReset(
        student_id=student_id,
        at=now,
        by_user_id=session.get("userId"),
        reason=reason,
    )
    db.add(row)
    db.flush()
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="interview_cap_reset",
        entity_id=row.id,
        action="INTERVIEW_CAP_RESET",
        before=None,
        after={"student_id": student_id, "reason": reason, "at": now.isoformat()},
        event_type="interview.cap.reset",
        payload={"student_id": student_id, "reset_id": row.id},
    )
    db.commit()
    db.refresh(row)
    return CapResetOut(
        id=row.id,
        student_id=student_id,
        at=row.at,
        reason=row.reason,
        by_user_id=row.by_user_id,
        usage=_usage_for(db, student_id, now),
    )


# ---------------------------------------------------------------------------
# Student — the card behind the Start button
# ---------------------------------------------------------------------------
@student_router.get("/policy", response_model=StudentPolicyOut)
def my_interview_policy(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StudentPolicyOut:
    """What this student's college has decided, what they have spent today, and
    which track their batch implies.

    NO `student_id` IN THE PATH. The subject comes from the session cookie, the
    same rule every other student route here follows — adding one "for symmetry"
    turns the endpoint into an IDOR the moment somebody forgets a `.where()`.

    IT IS A READ AND IT REFUSES NOTHING. A student at or over the cap still gets
    a 200 describing exactly that; the socket is what refuses, with 4015. Two
    places that can say "no" is two places to disagree, and the one that matters
    is the one holding the advisory lock.
    """
    if session.get("role") != Role.STUDENT.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a student has a mock interview policy.",
        )
    student_id = session.get("studentId")
    if not student_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has no student record.",
        )
    now = datetime.now(timezone.utc)
    college_id, course_id = spine_of_student(db, student_id)
    policy = resolve_policy(db, college_id=college_id, course_id=course_id)
    tracks = _track_list(db, college_id)

    live = db.scalar(
        select(InterviewConsent)
        .where(
            InterviewConsent.user_id == session["userId"],
            InterviewConsent.version == settings.interview_consent_version,
            InterviewConsent.revoked_at.is_(None),
        )
        .order_by(InterviewConsent.granted_at.desc())
        .limit(1)
    )
    acknowledged = live is not None and (
        bool(live.scope_store_transcript) == policy.store_transcript
        and bool(live.scope_store_audio) == policy.store_audio
    )
    return StudentPolicyOut(
        consent_version=settings.interview_consent_version,
        provider_label=settings.interview_provider_label,
        policy=_effective_out(policy),
        usage=_usage_for(db, student_id, now, policy),
        acknowledged=acknowledged,
        recording_enabled_on_server=bool(settings.interview_recording_enabled),
        default_track=_default_track(db, student_id, tracks),
        tracks=tracks,
    )


PolicySheetOut.model_rebuild()
