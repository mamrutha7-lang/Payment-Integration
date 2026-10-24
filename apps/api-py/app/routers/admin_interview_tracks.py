"""The Specialization Matrix as a screen: /api/admin/interview-questions/tracks.

    GET    /tracks             every track this session may see, with counts
    POST   /tracks             add one
    PATCH  /tracks/{id}        edit persona, frameworks, voice, syllabus, spine
    DELETE /tracks/{id}        remove one that no interview has ever used

B5.1. Until now the four tracks were a frozen dict in `app/interview_matrix.py`,
which meant the only way a college could add a fifth — or teach the DM
interviewer its own syllabus — was a pull request. They are rows now, and this
is the CRUD over them.

ITS OWN MODULE, NOT A SECTION OF `routers/interview_bank.py`. The bank router is
the questions; this is the catalogue the questions hang on, and the two have
different scope rules (a question is asked of everyone on a track; a track hangs
on a rung of the spine), different validation (a voice Nova will not accept is a
dead socket; a mistyped phase is a skipped line) and different delete semantics.
`GET /tracks` MOVED HERE FROM THE BANK ROUTER — it is the same URL and the same
five fields, plus the ones the board draws — because two modules cannot both own
one path and the richer answer belongs beside the writes that produce it.

GATED BY A CAPABILITY, NOT A ROLE — `admin.interview_questions`, the key the
bank already uses, because "may you author the interview" is one decision and
splitting it in two would let the office grant a faculty member questions on a
track they may not see.

SCOPED BY B1.2, AND THE TWO HALVES ANSWER DIFFERENT QUESTIONS.
  * READING is `scope_views.interview_track_scope_clause` over
    `policies.scope_filter`: a college admin sees their college's tracks AND the
    programme-wide ones, because those are the interviews their students are
    actually sitting and a screen that hid them would say the office has
    configured no interviewer at all.
  * WRITING is `require_capability(db, session, key, target=…)` against
    `governance.ancestry_of_interview_track`. A programme-wide track hangs on no
    rung, so no scoped grant reaches it: a college admin can READ the shipped
    four and cannot EDIT them, and adds their own college's row (which shadows
    the programme-wide one, `interview_tracks.track_for_code`) instead.
  * `editable` on each row is the FIRST of those answers applied to the second,
    so the form can grey a control instead of 403ing after the persona has been
    typed. It is a convenience and never a gate — the endpoint is what refuses.

RULE 1 is untouched: a track row is staff-authored text, exactly like a bank
question. Nothing here reads a student record, and the engine still receives an
`interview_matrix.Specialization` and never an ORM object
(`app/interview_tracks.py`).

Every write goes through `record_change`. Who changed the persona a cohort was
interviewed against is an audit question, and it is the same question the bank
router already answers for its own rows.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..db import get_db
from ..governance import ancestry_of_interview_track, require_capability
from ..identity import get_current_session
from ..interview_bank import BANK_PHASES
from ..interview_matrix import KNOWN_NOVA_VOICES, SPECIALIZATIONS, Specialization
from ..interview_tracks import (
    AUTHORING_CAPABILITY,
    RESERVED_TRACK_CODES,
    TRACK_CODE_RE,
    may_write_track,
    persona_refusals,
    persona_warnings,
)
from ..models.institution import (
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from ..models.interview import InterviewSession
from ..models.interview_bank import InterviewBankQuestion
from ..models.interview_track import InterviewTrack
from ..policies import scope_filter
from ..scope_views import interview_track_scope_clause, scope_header

router = APIRouter(prefix="/admin/interview-questions/tracks", tags=["interview-bank"])

#: One key over the whole catalogue — the tracks and the questions on them.
#: Defined in `app/interview_tracks.py` so the two routers cannot drift apart.
CAPABILITY = AUTHORING_CAPABILITY

#: `tests/test_interview_matrix.py` requires at least four for each of the four
#: shipped codes, and the reason is the interview rather than the test: the
#: frameworks are what PROBING and DEEP_DIVE work through one at a time, and a
#: track with one of them runs out of interview before wrap-up. A warning and
#: not a refusal — a college adding a narrow track has the right to be narrow.
ADVISED_MINIMUM_FRAMEWORKS = 4

#: Under this and the sample question is a prompt rather than a question the
#: student can be asked. Again a warning: the shipped four run 80-120 characters.
ADVISED_MINIMUM_SAMPLE_QUESTION_CHARS = 30


# ------------------------------------------------------------- schemas --


class AdminTrackOut(BaseModel):
    """One track, for the CONSOLE — and named for that surface, deliberately.

    `routers/interview_policy.py` has its own `TrackOut`, which is what a
    STUDENT is offered on the track picker: a code and a label. This is the
    office's row, persona and spine pointers and all.
    `tests/test_codebase_guards.py::test_no_new_duplicate_schema_names` is what
    stops the two sharing one name — one name must mean one shape, and
    "TrackOut" meaning both a four-field picker entry and a fifteen-field
    catalogue row is how a client ends up parsing one against the other.

    THE FIRST FIVE FIELDS ARE THE OLD `GET /tracks` RESPONSE, unchanged and in
    the same names: `key`, `label`, `phases`, `count`, `enabled_count`. The
    Angular screen is built against them (`interview-questions.component.ts`),
    and B5.1 must not be a breaking change to a screen that works.
    """

    key: str
    label: str
    phases: list[str]
    count: int
    enabled_count: int

    #: None for a track that is still only a row in `interview_matrix.py` — see
    #: `source`. Everything that writes needs an id, so the form disables its
    #: controls on exactly the rows that have none.
    id: str | None = None
    code: str
    persona: str
    frameworks: list[str]
    sample_question: str
    nova_voice: str
    syllabus: list[str]
    enabled: bool
    position: int
    college_id: str | None = None
    course_id: str | None = None
    specialization_id: str | None = None
    #: `table` — a row, editable. `code` — the constant in
    #: `interview_matrix.SPECIALIZATIONS`, which is still the fallback for a code
    #: with no row and still runs real interviews. Shown rather than hidden: a
    #: deployment mid-upgrade, or one whose office deleted a row, is running an
    #: interview this screen would otherwise claim does not exist.
    source: str
    #: Does THIS session's grant reach this track? A convenience for the form,
    #: never the fence — `require_capability(..., target=…)` is.
    editable: bool


class AdminTrackWriteResult(BaseModel):
    """The row, plus anything worth saying about it that is not worth refusing.

    `warnings` is why the persona check is not all-or-nothing. A trailing full
    stop composes into "you are a sharp CFO.." and is refused; a persona that
    starts with a capital is merely unlike the other four and is said out loud
    here, where an admin reads it, rather than silently accepted or fought over.
    """

    track: AdminTrackOut
    warnings: list[str] = []


class AdminTrackIn(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    label: str = Field(min_length=2, max_length=120)
    persona: str = Field(min_length=3, max_length=600)
    frameworks: list[str] = Field(default_factory=list, max_length=30)
    sample_question: str = Field(min_length=12, max_length=600)
    nova_voice: str = Field(default="", max_length=40)
    syllabus: list[str] = Field(default_factory=list, max_length=40)
    college_id: str | None = None
    course_id: str | None = None
    specialization_id: str | None = None
    enabled: bool = True
    position: int | None = None


class AdminTrackPatch(BaseModel):
    """Every field optional, and `None` means "not sent" for all of them except
    the three spine pointers, where an explicit null is how a track is widened.

    Pydantic cannot tell the two apart on a plain `str | None`, so the spine is
    read from `model_fields_set` — the same thing `_resolve_ancestry` in
    `routers/admin.py` does with its `sent` argument, and for the same reason: a
    shallower level the client explicitly cleared while a deeper one stands is a
    contradiction, not a widening.
    """

    label: str | None = Field(default=None, min_length=2, max_length=120)
    persona: str | None = Field(default=None, min_length=3, max_length=600)
    frameworks: list[str] | None = Field(default=None, max_length=30)
    sample_question: str | None = Field(default=None, min_length=12, max_length=600)
    nova_voice: str | None = Field(default=None, max_length=40)
    syllabus: list[str] | None = Field(default=None, max_length=40)
    college_id: str | None = None
    course_id: str | None = None
    specialization_id: str | None = None
    enabled: bool | None = None
    position: int | None = None


# ------------------------------------------------------------- helpers --


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _check_code(code: str) -> str:
    key = (code or "").strip().lower()
    if not TRACK_CODE_RE.match(key):
        raise _bad(
            "code must be 2-20 characters, lower case, starting with a letter and "
            "made of letters, digits, '-' or '_'. It is what ?specialization= "
            "carries on the interview socket and what every past interview is "
            "filed under, so it is a slug, not a title."
        )
    if key in RESERVED_TRACK_CODES:
        raise _bad(
            f"{key!r} is reserved: an interview with no ?specialization= is the "
            "generic one, and a track claiming that name would be "
            "indistinguishable from it on the records grid."
        )
    return key


def _check_voice(voice: str | None) -> str:
    """Refuse a voice Nova will not accept, here rather than at the handshake.

    Nova answers an unknown `voiceId` with a ValidationException that kills the
    stream DURING the handshake — an interview that never starts, for every
    student on the track, with nothing in the UI naming the cause. The model has
    a `@validates` hook that raises the same refusal, but a ValueError out of the
    ORM is a 500; this is the same check phrased as a 422 on the field the admin
    typed it into.
    """
    cleaned = (voice or "").strip().lower()
    if cleaned and cleaned not in KNOWN_NOVA_VOICES:
        raise _bad(
            f"{cleaned!r} is not a voice Amazon Nova 2 Sonic accepts, and an "
            "unknown voice ends the stream during the handshake — an interview "
            "that never starts. Leave it empty for the configured default, or "
            "use one of: " + ", ".join(sorted(KNOWN_NOVA_VOICES))
        )
    return cleaned


def _clean_list(values: list[str] | None) -> list[str]:
    return [v.strip() for v in (values or []) if v and v.strip()]


def _check_persona(persona: str) -> str:
    text = (persona or "").strip()
    problems = persona_refusals(text)
    if problems:
        raise _bad(" ".join(problems))
    return text


def _resolve_spine(
    db: Session, intended: dict, sent: set[str]
) -> tuple[str | None, str | None, str | None]:
    """(college_id, course_id, specialization_id), derived from the deepest one.

    `routers/admin.py::_resolve_ancestry`'s rule applied to a track's own three
    pointers: the client names the deepest rung it means, the API walks the real
    foreign keys upward, and a SHALLOWER value the client also sent is CHECKED
    against the derived one rather than stored beside it. Three pointers is three
    chances to disagree and the disagreement is silent — a track filed under a
    college that does not own the course it names would be reached by one scoped
    grant and not the other, which reads as a permissions bug in whichever
    direction it is noticed.
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
    college_id = intended.get("college_id")
    if course is not None:
        # `academic_courses.department_id` is NOT NULL, so this walk always
        # lands — the same one `scope_views.job_scope_clause` makes for a
        # posting, and the reason `ancestry_of_interview_track` can reach a
        # college from a course when `ancestry_of_cohort` says it cannot (a
        # cohort names its department itself; a track never does).
        college_id = db.scalar(
            select(Department.college_id).where(Department.id == course.department_id)
        )
    elif college_id is not None and db.get(College, college_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="College not found.")

    derived = {
        "college_id": college_id,
        "course_id": course_id,
        "specialization_id": spec.id if spec is not None else None,
    }
    for field, deeper in (("college_id", "course"), ("course_id", "specialization")):
        if field in sent and intended.get(field) != derived[field]:
            raise _bad(
                f"{field} contradicts the {deeper} you chose, which sits under "
                f"{field} {derived[field]}. Clear the {deeper} first, or pick one "
                f"under the {field.removesuffix('_id')} you want."
            )
    return derived["college_id"], derived["course_id"], derived["specialization_id"]


def _ancestry(db: Session, college_id, course_id, specialization_id):
    return ancestry_of_interview_track(
        db,
        college_id=college_id,
        course_id=course_id,
        specialization_id=specialization_id,
    )


def _require_reach(db: Session, session: dict, row_or_values) -> None:
    """The write fence. Named so the three writers read identically."""
    college_id, course_id, specialization_id = row_or_values
    require_capability(
        db,
        session,
        CAPABILITY,
        target=_ancestry(db, college_id, course_id, specialization_id),
    )


def _counts(db: Session) -> dict[tuple[str, bool], int]:
    return {
        (track, enabled): n
        for track, enabled, n in db.execute(
            select(
                InterviewBankQuestion.track,
                InterviewBankQuestion.enabled,
                func.count(),
            ).group_by(InterviewBankQuestion.track, InterviewBankQuestion.enabled)
        ).all()
    }


def _row_out(
    row: InterviewTrack, counts: dict[tuple[str, bool], int], *, editable: bool
) -> AdminTrackOut:
    return AdminTrackOut(
        key=row.code,
        label=row.label,
        phases=list(BANK_PHASES),
        count=counts.get((row.code, True), 0) + counts.get((row.code, False), 0),
        enabled_count=counts.get((row.code, True), 0),
        id=row.id,
        code=row.code,
        persona=row.persona,
        frameworks=list(row.frameworks or []),
        sample_question=row.sample_question,
        nova_voice=row.nova_voice or "",
        syllabus=list(row.syllabus or []),
        enabled=row.enabled,
        position=row.position,
        college_id=row.college_id,
        course_id=row.course_id,
        specialization_id=row.specialization_id,
        source="table",
        editable=editable,
    )


def _constant_out(spec: Specialization, counts: dict[tuple[str, bool], int]) -> AdminTrackOut:
    """A track that still lives only in `interview_matrix.SPECIALIZATIONS`.

    Shown, and shown as NOT editable, because it is REAL: `resolve_specialization`
    falls back to the constant for any code with no row, so this track is running
    interviews right now. Hiding it would make the screen disagree with the
    interviewer; pretending it is editable would offer a PATCH with no id.
    """
    return AdminTrackOut(
        key=spec.key,
        label=spec.label,
        phases=list(BANK_PHASES),
        count=counts.get((spec.key, True), 0) + counts.get((spec.key, False), 0),
        enabled_count=counts.get((spec.key, True), 0),
        id=None,
        code=spec.key,
        persona=spec.persona,
        frameworks=list(spec.frameworks),
        sample_question=spec.sample_question,
        nova_voice=spec.nova_voice or "",
        syllabus=list(spec.syllabus),
        enabled=True,
        position=0,
        source="code",
        editable=False,
    )


def _refuse_clash(
    db: Session, code: str, college_id: str | None, *, exclude_id: str | None = None
) -> None:
    """The two uniqueness rules the table enforces, as a sentence rather than a 500.

    One row per code per college (`uq_interview_track_college_code`), and one
    PROGRAMME-WIDE row per code (`uq_interview_track_global_code`, a partial
    index, because Postgres treats NULLs as DISTINCT and the constraint alone
    would happily hold two programme-wide 'hr' rows). Both exist for one reason:
    the code is what `?specialization=` carries, so two rows sharing one at the
    same reach is a coin toss between two different interviewers.
    """
    at_reach = (
        InterviewTrack.college_id.is_(None)
        if college_id is None
        else InterviewTrack.college_id == college_id
    )
    stmt = select(InterviewTrack).where(InterviewTrack.code == code, at_reach)
    if exclude_id is not None:
        stmt = stmt.where(InterviewTrack.id != exclude_id)
    if db.scalars(stmt).first() is None:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"A track with code {code!r} already exists "
            + ("programme-wide" if college_id is None else "for this college")
            + ". Edit it, or give this one a different code — the code is what "
            "?specialization= carries, so two rows sharing one would be a coin "
            "toss between two interviewers."
        ),
    )


def _track_or_404(db: Session, track_id: str) -> InterviewTrack:
    row = db.get(InterviewTrack, track_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track not found.")
    return row


def _snapshot(row: InterviewTrack) -> dict:
    return {
        "code": row.code,
        "label": row.label,
        "persona": row.persona,
        "frameworks": list(row.frameworks or []),
        "sample_question": row.sample_question,
        "nova_voice": row.nova_voice,
        "syllabus": list(row.syllabus or []),
        "college_id": row.college_id,
        "course_id": row.course_id,
        "specialization_id": row.specialization_id,
        "enabled": row.enabled,
        "position": row.position,
    }


def _audit(db, session, request, row: InterviewTrack, action: str, before, after) -> None:
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="interview_track",
        entity_id=row.id,
        action=action,
        before=before,
        after=after,
        event_type=f"interview_track.{action.lower()}",
        payload={"code": row.code, "college_id": row.college_id},
    )


def _advice(
    persona: str, frameworks: list[str], sample_question: str, voice: str
) -> list[str]:
    notes = persona_warnings(persona)
    if len(frameworks) < ADVISED_MINIMUM_FRAMEWORKS:
        notes.append(
            f"Only {len(frameworks)} framework(s). PROBING and DEEP_DIVE work "
            f"through these one at a time; the shipped tracks carry "
            f"{ADVISED_MINIMUM_FRAMEWORKS} or more, and a track with fewer runs "
            "out of interview before wrap-up."
        )
    if len(sample_question.strip()) < ADVISED_MINIMUM_SAMPLE_QUESTION_CHARS:
        notes.append(
            "The sample question is very short. It is the question PROBING works "
            "in early, rephrased naturally rather than recited."
        )
    if not voice:
        notes.append(
            "No voice set, so this track speaks with the deployment's configured "
            "default (NOVA_SONIC_VOICE). The four shipped tracks each have their "
            "own so that a CHRO does not sound like a CFO."
        )
    return notes


# ----------------------------------------------------------- endpoints --


@router.get("", response_model=list[AdminTrackOut])
def list_tracks(
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AdminTrackOut]:
    """Every track this session may see, with its question counts.

    THE ANSWER IS THE SAME UNION `resolve_specialization` USES: the rows first,
    then the `interview_matrix` constants for any code with no row. A test
    database, and a deployment between `alembic upgrade head` and its seed, have
    no rows at all and still run four interviews — a screen that showed nothing
    there would be reporting an outage that is not happening.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    scope_header(response, reach)
    if reach.nothing:
        # Checked FIRST and returning the empty list deliberately: "may see
        # nothing" and "may see everything" are opposite facts, and the header
        # above is what says which one produced an empty grid.
        return []

    counts = _counts(db)
    rows = db.scalars(
        select(InterviewTrack)
        .where(interview_track_scope_clause(reach))
        .order_by(InterviewTrack.position, InterviewTrack.code, InterviewTrack.id)
    ).all()
    out: list[AdminTrackOut] = []
    for row in rows:
        editable = may_write_track(
            db,
            session,
            college_id=row.college_id,
            course_id=row.course_id,
            specialization_id=row.specialization_id,
        )
        out.append(_row_out(row, counts, editable=editable))
    seen = {row.code for row in rows}
    out.extend(
        _constant_out(spec, counts) for key, spec in SPECIALIZATIONS.items() if key not in seen
    )
    return out


@router.post("", response_model=AdminTrackWriteResult, status_code=status.HTTP_201_CREATED)
def create_track(
    body: AdminTrackIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AdminTrackWriteResult:
    require_capability(db, session, CAPABILITY)
    code = _check_code(body.code)
    persona = _check_persona(body.persona)
    voice = _check_voice(body.nova_voice)
    frameworks = _clean_list(body.frameworks)
    syllabus = _clean_list(body.syllabus)
    sent = body.model_fields_set & {"college_id", "course_id", "specialization_id"}
    college_id, course_id, specialization_id = _resolve_spine(
        db,
        {
            "college_id": body.college_id,
            "course_id": body.course_id,
            "specialization_id": body.specialization_id,
        },
        sent,
    )
    _require_reach(db, session, (college_id, course_id, specialization_id))

    _refuse_clash(db, code, college_id)

    position = body.position
    if position is None:
        position = int(db.scalar(select(func.max(InterviewTrack.position))) or 0) + 1
    row = InterviewTrack(
        code=code,
        label=body.label.strip(),
        persona=persona,
        frameworks=frameworks,
        sample_question=body.sample_question.strip(),
        nova_voice=voice,
        syllabus=syllabus,
        college_id=college_id,
        course_id=course_id,
        specialization_id=specialization_id,
        enabled=body.enabled,
        position=position,
        created_by_user_id=session.get("userId"),
    )
    db.add(row)
    db.flush()
    _audit(db, session, request, row, "CREATED", None, _snapshot(row))
    db.commit()
    db.refresh(row)
    return AdminTrackWriteResult(
        track=_row_out(row, _counts(db), editable=True),
        warnings=_advice(persona, frameworks, row.sample_question, voice),
    )


@router.patch("/{track_id}", response_model=AdminTrackWriteResult)
def patch_track(
    track_id: str,
    body: AdminTrackPatch,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AdminTrackWriteResult:
    """Edit a track. THE CODE IS NOT EDITABLE and there is no field for it.

    `code` is what `?specialization=` carries, what every `interview_sessions`
    row ever written is filed under, and what `question_bank_for` keys on.
    Renaming it is a migration of the history, not a rename — and a rename that
    silently orphaned a cohort's records from their own track is a screen that
    says a student never sat an interview they sat.
    """
    require_capability(db, session, CAPABILITY)
    row = _track_or_404(db, track_id)
    # BOTH ENDS OF A MOVE ARE CHECKED. The reach over the row as it stands is
    # the right to change it at all; the reach over the row as it would be is
    # the right to put it there. Checking only the first would let a
    # college-scoped holder push a track into another college and then be unable
    # to pull it back; only the second would let them take one out of a college
    # they cannot see.
    _require_reach(db, session, (row.college_id, row.course_id, row.specialization_id))

    sent = body.model_fields_set
    spine_sent = sent & {"college_id", "course_id", "specialization_id"}
    intended = {
        "college_id": body.college_id if "college_id" in sent else row.college_id,
        "course_id": body.course_id if "course_id" in sent else row.course_id,
        "specialization_id": (
            body.specialization_id if "specialization_id" in sent else row.specialization_id
        ),
    }
    college_id, course_id, specialization_id = _resolve_spine(db, intended, spine_sent)
    if spine_sent and college_id != row.college_id:
        _require_reach(db, session, (college_id, course_id, specialization_id))
        # Moving a track INTO a college that already has this code would trip
        # `uq_interview_track_college_code` as a 500. Two rows sharing a code at
        # one reach is a coin toss between two interviewers, so it has to be
        # refused — but as the same sentence a create gets, not as a stack trace.
        _refuse_clash(db, row.code, college_id, exclude_id=row.id)
    elif spine_sent:
        _require_reach(db, session, (college_id, course_id, specialization_id))

    before = _snapshot(row)
    if body.label is not None:
        row.label = body.label.strip()
    if body.persona is not None:
        row.persona = _check_persona(body.persona)
    if body.frameworks is not None:
        row.frameworks = _clean_list(body.frameworks)
    if body.sample_question is not None:
        row.sample_question = body.sample_question.strip()
    if body.nova_voice is not None:
        row.nova_voice = _check_voice(body.nova_voice)
    if body.syllabus is not None:
        row.syllabus = _clean_list(body.syllabus)
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.position is not None:
        row.position = body.position
    row.college_id, row.course_id, row.specialization_id = (
        college_id,
        course_id,
        specialization_id,
    )

    _audit(db, session, request, row, "UPDATED", before, _snapshot(row))
    db.commit()
    db.refresh(row)
    return AdminTrackWriteResult(
        track=_row_out(row, _counts(db), editable=True),
        warnings=_advice(
            row.persona, list(row.frameworks or []), row.sample_question, row.nova_voice or ""
        ),
    )


@router.delete("/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_track(
    track_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    """Remove a track NO INTERVIEW HAS EVER BEEN HELD ON.

    A track that has been used is DISABLED, never deleted, and this endpoint
    enforces that rather than leaving it as advice. `interview_sessions` files
    every interview under the track CODE, so deleting a used row leaves every
    one of those records labelled by a dangling code on the student's own
    history screen and on the records grid — the failure
    `app/models/interview_track.py` names on the `enabled` column.

    The QUESTIONS survive either way: `interview_bank_questions.track_id` is
    `ON DELETE SET NULL` on purpose, because the office's questions are the
    expensive thing on this screen and a track is four fields.
    """
    require_capability(db, session, CAPABILITY)
    row = _track_or_404(db, track_id)
    _require_reach(db, session, (row.college_id, row.course_id, row.specialization_id))

    held = db.scalar(
        select(func.count())
        .select_from(InterviewSession)
        .where(InterviewSession.specialization == row.code)
    )
    if held:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{held} interview(s) were held on {row.code!r}. Switch the track "
                "off instead: every one of those records is filed under this code, "
                "and deleting it would leave them labelled by a code nothing "
                "resolves. A disabled track is refused at the handshake and still "
                "reads back on the records grid."
            ),
        )

    _audit(db, session, request, row, "DELETED", _snapshot(row), None)
    db.delete(row)
    db.commit()
