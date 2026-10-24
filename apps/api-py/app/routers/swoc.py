"""SWOC — Strengths, Weaknesses, Opportunities, Challenges: the editor.

    GET    /admin/swoc                        every student with their entries
    POST   /admin/swoc/{student_id}           add an entry (kind, text, weight)
    PATCH  /admin/swoc/entries/{entry_id}     edit text / weight
    DELETE /admin/swoc/entries/{entry_id}     remove

The MODEL IS NOT NEW. `swoc_entries` (app/models/swoc.py) has carried the
board since the Prisma port — one row per observation, attributed to a
VIEWPOINT (placement cell, mentor, programme manager) and weighted 1-5 — and
the student already reads it: `GET /student/swoc`, and inside
`GET /student/overview` as `swoc`, drawn on the Mentor / TPO Log and now on
the landing. What was missing was a WRITER. This is it.

GATED BY A CAPABILITY, NOT A ROLE — `admin.swoc`. The placement office holds
it by baseline; a faculty member holds it only when an administrator grants it
in Governance. "From TPO and mentor inputs" means more than one hand writes
the board, and Governance is where the owner decides whose.

AND THE GRANT NOW CARRIES A SCOPE (B1.2/B1.4), WHICH REVERSES WHAT THIS
DOCSTRING USED TO SAY. It read "PROGRAMME scope like the other admin screens:
the grant is the decision to let that person write for every student", and that
sentence was true of the mechanism and wrong about the act. A SWOC line names a
student and characterises them — it is the one thing on their landing page that
another person wrote about them — and "let this lecturer write for every student
in the institution" was never a decision anybody meant to make; it was the only
decision the grant could express. A grant may now name a college, a department,
a batch or one student, `scope_filter` narrows the board to it, and the writes
are narrowed by the same reach through `require_capability(..., target=...)`.
A programme-wide grant still exists and still means what it says; it is now a
choice rather than the only shape.

THE VIEWPOINT IS DERIVED, NOT TYPED. A MENTOR's entry is a MENTOR entry; a
the Main Admin's is the PLACEMENT cell's. The board is deliberately
un-averaged — disagreement between viewpoints is itself the finding — and a
select box that lets a mentor file an opinion as the office's would erase the
one thing the source column exists to keep.

Every write goes through record_change with the before/after snapshot: a line
that tells a student what their weakness is must be traceable to who wrote it
and what it replaced.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import batch_labels
from ..architecture_events import record_change
from ..db import get_db
from ..governance import ancestry_of_student, require_capability
from ..identity import get_current_session
from ..policies import scope_filter
from ..scope_views import scope_header
from ..models.cohort import Cohort
from ..models.institution import AcademicCourse, AcademicSpecialization
from ..models.job import Job
from ..models.interview import InterviewSession
from ..models.skill import StudentSkill
from ..models.swoc import SwocEntry, SwocEntryRevision, SwocKind, SwocSource
from ..models.user import Mentor, Student, User

router = APIRouter(prefix="/admin/swoc", tags=["swoc"])

CAPABILITY = "admin.swoc"

#: One quadrant line is a sentence or two, not an essay: the card draws it in a tile.
MAX_SWOC_CHARS = 400

#: The board's order, the same the student's card draws.
KINDS: tuple[str, ...] = tuple(k.value for k in SwocKind)

#: B7.7. The same three headers `routers/admin_mentoring.py` states a page in, and the
#: same reason for using headers rather than an envelope — see `list_swoc`.
PAGE_HEADER = "X-Reep-Page"
PAGE_SIZE_HEADER = "X-Reep-Page-Size"
TOTAL_HEADER = "X-Reep-Total"

#: A page size is a query parameter, and an unbounded one is "give me
#: everything" with extra steps.
MAX_PAGE_SIZE = 500
#: The highest page any list here will serve. Unbounded, a crafted `?page=` made
#: `(page - 1) * page_size` overflow Postgres' bigint OFFSET and the request a 500
#: (DEF-006, testing/docs/05-incident-reports.md). A value past it is a 422.
MAX_PAGE = 100_000


def _page_headers(response: Response, *, total: int, page: int, page_size: int | None) -> None:
    """`total` is ALWAYS set, paged or not: it is what lets a client that asked
    for no page tell a short list from a truncated one."""
    response.headers[TOTAL_HEADER] = str(total)
    if page_size is not None:
        response.headers[PAGE_HEADER] = str(page)
        response.headers[PAGE_SIZE_HEADER] = str(page_size)


def _source_for(db: Session, session: dict, student_id: str) -> SwocSource:
    """MENTOR only when the author actually mentors THIS student (B7.1).

    THE OLD VERSION STAMPED ON ROLE ALONE, and that quietly broke the one thing
    the source column exists for. The board is deliberately un-averaged —
    disagreement between viewpoints IS the finding — so a lecturer granted
    `admin.swoc` for a whole department filed every line as MENTOR, including
    lines about students they have never met. Two viewpoints became one label.

    Now the relationship decides: a MENTOR-role account whose own `Mentor` group
    this student points at writes as MENTOR, and everybody else — the office,
    and a granted faculty member who is not this student's mentor — writes as
    PLACEMENT. That is what "the placement cell's view" has always meant.

    A FORMER MENTOR INSIDE B9.1's 90-DAY HANDOVER WINDOW WRITES AS PLACEMENT,
    and this is stated rather than left to be discovered. The window is a READ:
    it exists so the incoming mentor can ask about a note the outgoing one
    wrote. Filing a NEW line as MENTOR three months after handing the student
    over would put a current-sounding mentor judgement on the board from
    somebody who no longer holds the relationship — and the incoming mentor,
    reading their own board, could not tell the two apart.

    PM IS NOT REACHABLE FROM HERE AND NEVER WAS. It stays a legal stored value
    (the seed writes one, the client maps it, and dropping a Postgres enum value
    means recreating the type); 04's "PM retired from the writer" is already
    true of the API.
    """
    if session.get("role") != "MENTOR":
        return SwocSource.PLACEMENT
    mentor_id = db.scalar(select(Student.mentor_id).where(Student.id == student_id))
    if not mentor_id:
        return SwocSource.PLACEMENT
    mentors_this_student = db.scalar(
        select(Mentor.id).where(
            Mentor.id == mentor_id, Mentor.user_id == session.get("userId")
        )
    )
    return SwocSource.MENTOR if mentors_this_student else SwocSource.PLACEMENT


# ------------------------------------------------------------- schemas --


def _clean_text(v: object) -> str:
    if not isinstance(v, str):
        raise ValueError("must be text")
    collapsed = " ".join(v.split())
    if not collapsed:
        raise ValueError("must not be blank")
    if len(collapsed) > MAX_SWOC_CHARS:
        raise ValueError(f"at most {MAX_SWOC_CHARS} characters")
    return collapsed


class SwocEntryOut(BaseModel):
    id: str
    kind: str
    source: str
    text: str
    weight: int
    #: Who wrote it — the author's name, or null if that account is gone.
    #:
    #: NOT RENAMED TO 04's `author_name`. `swoc.component.ts` reads `author` and
    #: `tests/test_phase3_compatibility.py` pins it; a rename would break both
    #: to gain nothing. This model EXTENDS, it does not re-cut.
    author: str | None
    #: Whether an author was recorded at all — True exactly when
    #: `author_user_id` is set.
    #:
    #: IT IS NOT "THE ACCOUNT IS GONE" AND CANNOT BE. `author_user_id` is
    #: `ON DELETE SET NULL`, so removing the account clears the pointer rather
    #: than leaving a dangling one: `author_recorded=True` with `author=null` is
    #: unreachable, and `author: null` therefore means ONE thing — nobody was
    #: recorded. That is what makes the screen's old single string ("Author no
    #: longer on the roster") wrong for every row it can ever be drawn on, and
    #: why the seed now writes an author instead: the honest fix was to stop
    #: producing authorless rows, not to describe them better. This flag is here
    #: so a client branches on a stated fact rather than on a falsy name.
    author_recorded: bool
    recorded_at: datetime
    #: B7.3. Equal to `recorded_at` means "as written"; later means "edited then".
    updated_at: datetime
    #: B7.4. NULL on every row written before the column existed — "semester not
    #: recorded", never a guess. See the migration's docstring.
    semester: int | None
    #: B7.5. When the STUDENT said they had read it.
    acknowledged_at: datetime | None
    #: B7.6.
    linked_skill_id: str | None
    linked_session_id: str | None
    linked_job_id: str | None


class _Linkable(BaseModel):
    """B7.6's three optional links, shared by the create and the patch so they
    cannot drift into two validations of one rule."""

    #: `student_skills`, NOT the `skills` catalogue, and 04 is ambiguous exactly
    #: where the difference is the whole value: "weak on SQL" as a catalogue
    #: reference is a statement about SQL, and as a per-student row it is a
    #: statement about THIS student's SQL — which is what a SWOC line is.
    linked_skill_id: str | None = None
    linked_session_id: str | None = None
    linked_job_id: str | None = None

    @field_validator("linked_skill_id", "linked_session_id", "linked_job_id", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        """"" means "no link". Kept as "", it slipped past every check in
        `_assert_links_belong` (each is an `if body.x:` truth test) and reached
        the UPDATE as a foreign key to nothing: a 500 (DEF-005). A blank sent
        on a PATCH still lands in `model_fields_set`, so it still REMOVES the
        link, which is what a cleared box means."""
        if isinstance(v, str) and not v.strip():
            return None
        return v


class SwocEntryIn(_Linkable):
    kind: str
    text: str
    weight: int = Field(default=3, ge=1, le=5)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v: object) -> str:
        key = str(v or "").strip().upper()
        if key not in KINDS:
            raise ValueError("kind must be one of " + ", ".join(k.lower() for k in KINDS))
        return key

    @field_validator("text", mode="before")
    @classmethod
    def _text(cls, v: object) -> str:
        return _clean_text(v)


class SwocEntryPatch(_Linkable):
    text: str | None = None
    weight: int | None = Field(default=None, ge=1, le=5)

    @field_validator("text", mode="before")
    @classmethod
    def _text(cls, v: object) -> str | None:
        return None if v is None else _clean_text(v)


class SwocStudentRow(BaseModel):
    """One line of the editor's list: who, plus everything written about them."""

    student_id: str
    name: str
    usn: str | None
    batch: str | None
    entries: list[SwocEntryOut]


# ------------------------------------------------------------- helpers --


def _out(e: SwocEntry, author: str | None) -> SwocEntryOut:
    return SwocEntryOut(
        id=e.id, kind=e.kind.value, source=e.source.value, text=e.text, weight=e.weight,
        author=author, author_recorded=e.author_user_id is not None,
        recorded_at=e.recorded_at, updated_at=e.updated_at, semester=e.semester,
        acknowledged_at=e.acknowledged_at,
        linked_skill_id=e.linked_skill_id, linked_session_id=e.linked_session_id,
        linked_job_id=e.linked_job_id,
    )


def _snapshot(e: SwocEntry) -> dict:
    return {
        "student_id": e.student_id, "kind": e.kind.value, "source": e.source.value,
        "text": e.text, "weight": e.weight, "semester": e.semester,
        "linked_skill_id": e.linked_skill_id, "linked_session_id": e.linked_session_id,
        "linked_job_id": e.linked_job_id,
    }


def _audit(db: Session, session: dict, request: Request, e: SwocEntry, action: str,
           before: dict | None, after: dict | None) -> None:
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="swoc_entry", entity_id=e.id, action=action,
        before=before, after=after,
        event_type=f"swoc.{action.lower()}",
        payload={"student_id": e.student_id, "kind": e.kind.value, "source": e.source.value},
    )


def _entry_or_404(db: Session, entry_id: str) -> SwocEntry:
    e = db.get(SwocEntry, entry_id)
    if e is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such entry.")
    return e


def _assert_owns(session: dict, e: SwocEntry) -> None:
    """B7.2. The AUTHOR edits and deletes their own line; the office edits any.

    Until now the capability and the reach were the whole check, so any holder
    in reach could rewrite or delete anybody else's line — which is precisely
    "erase the one thing the source column exists to keep", the risk this
    module's own docstring names three paragraphs up. A mentor's judgement is
    theirs; a colleague granted the same screen disagreeing with it should write
    a second entry, which is what an un-averaged board is FOR.

    THE OFFICE IS THE EXCEPTION AND IT IS A ROLE, NOT A CAPABILITY. `admin.swoc`
    cannot be the override — it is the key everybody on this screen holds, so
    "author or `admin.swoc` holder" is "anybody", written at more length. The
    Main Admin (role ADMIN) is the account that answers for the board as a
    whole, has to be able to take down a line somebody should not have written,
    and is the only account on the deployment that can do it by design.

    403 and not 404: the caller can see this entry on the list they just read,
    so pretending it does not exist would read as a bug. The reach check above
    has already answered whether they may see it at all.

    A LINE WITH NO AUTHOR (`author_user_id IS NULL` — a seeded row, or one
    written before that column) is EDITABLE BY THE OFFICE ONLY. Nobody can claim
    authorship of it, and treating "no author" as "everybody is the author"
    would make exactly the rows nobody is answerable for the easiest to rewrite.
    """
    if session.get("role") == "ADMIN":
        return
    if e.author_user_id and e.author_user_id == session.get("userId"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "This entry was written by somebody else. Add your own observation "
            "instead — the board keeps every viewpoint rather than averaging "
            "them. The Main Admin can edit or remove any entry."
        ),
    )


def _assert_links_belong(db: Session, student_id: str, body: _Linkable) -> None:
    """B7.6. A link must point at something that belongs to THIS student.

    Unchecked, `linked_session_id` is a way to name another student's interview
    on a board a third party reads — the id would be echoed back on every GET of
    this entry, and an id is enough to ask the interview endpoints for the rest.
    So each of the three is resolved and must carry the same `student_id`, with
    a 422 that says which one is wrong.

    `linked_job_id` is the exception: a JOB POSTING IS PUBLIC and belongs to
    nobody, so it is checked for existence only. Rule 1 draws the same line —
    a posting needs no egress gate, a student's records do.
    """
    if body.linked_skill_id:
        owner = db.scalar(
            select(StudentSkill.student_id).where(StudentSkill.id == body.linked_skill_id)
        )
        if owner != student_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="linked_skill_id is not one of this student's skills.",
            )
    if body.linked_session_id:
        owner = db.scalar(
            select(InterviewSession.student_id).where(
                InterviewSession.id == body.linked_session_id
            )
        )
        if owner != student_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="linked_session_id is not one of this student's interviews.",
            )
    if body.linked_job_id and db.get(Job, body.linked_job_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="linked_job_id is not a posting.",
        )


# ----------------------------------------------------------- the list --


@router.get("", response_model=list[SwocStudentRow])
def list_swoc(
    response: Response,
    cohort_id: str | None = None,
    semester: int | None = Query(default=None, ge=1, le=20),
    q: str | None = None,
    page: int = Query(default=1, ge=1, le=MAX_PAGE),
    page_size: int | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[SwocStudentRow]:
    """Every student this caller's grant reaches, with their board.

    SCOPED (B1.4) — see the module docstring for why the reversal is part of
    this change and not a side effect of it. The ENTRIES are narrowed too, not
    only the student rows: a board is read by joining one to the other, and
    fetching every entry on the deployment to display a scoped subset would put
    another student's stated weakness in the response body of a screen that
    happens not to draw it.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    scope_header(response, reach)
    if reach.nothing:
        # The total is stated even here, for `console.mentor_load`'s reason: a
        # client must not be able to read "no page headers" as "the server does
        # not paginate" and conclude the empty list is complete. The
        # `X-Reep-Scope: none` beside it is what says WHY it is zero — "may see
        # nothing" and "there is nothing" must never render the same.
        _page_headers(response, total=0, page=page, page_size=page_size)
        return []

    where = [Student.id.in_(reach.student_ids())]
    # B7.7. Both narrow WITHIN the reach and neither can widen it: each is ANDed
    # onto `reach.student_ids()`, so an id the caller's grant does not cover
    # matches nothing rather than somebody else's roster.
    if cohort_id:
        where.append(Student.cohort_id == cohort_id)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        where.append(or_(func.lower(User.name).like(needle), func.lower(Student.usn).like(needle)))

    student_query = (
        # The batch's spine comes down its LINKS, never out of `cohorts.name`,
        # which is the year and nothing else (a4e7c92d1f38).
        select(
            Student.id, User.name, Student.usn, Cohort.name, Cohort.batch_label,
            AcademicCourse.name, AcademicSpecialization.name,
        )
        .join(User, User.id == Student.user_id)
        .outerjoin(Cohort, Cohort.id == Student.cohort_id)
        .outerjoin(AcademicCourse, AcademicCourse.id == Cohort.course_id)
        .outerjoin(AcademicSpecialization, AcademicSpecialization.id == Cohort.specialization_id)
        .where(*where)
        # NAME **AND USN**. A page is a window over a sort and `users.name` is
        # not unique; an unstable sort drops one row off page 2 and repeats
        # another, with nothing on screen saying so.
        .order_by(User.name, Student.usn, Student.id)
    )
    total = db.scalar(
        select(func.count()).select_from(
            select(Student.id).join(User, User.id == Student.user_id).where(*where).subquery()
        )
    ) or 0
    # PAGING IS OPT-IN AND OFF BY DEFAULT (the `mentor-load` precedent, and the
    # RESPONSE IS STILL A BARE ARRAY). 04 asks for pagination; wrapping this in
    # `{items, page, total}` is a breaking change to `swoc.component.ts`, whose
    # whole read model is "the list IS the data — picking a row fills the editor
    # with no second read", and to two test modules that iterate the raw JSON. A
    # default page size would silently truncate a screen that filters in memory
    # over the whole set and has no paging control to reach row 51 with.
    if page_size is not None:
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        page = max(1, int(page))
        student_query = student_query.offset((page - 1) * page_size).limit(page_size)
    _page_headers(response, total=total, page=page, page_size=page_size)

    students = db.execute(student_query).all()
    listed_ids = [sid for sid, *_ in students] or [""]
    entry_where = [SwocEntry.student_id.in_(listed_ids)]
    # B7.4. A semester filter narrows the ENTRIES and not the students: a
    # student with nothing written this term is still a row on the board, with
    # an empty quadrant set. Dropping them would turn "show me semester 3" into
    # "hide everybody nobody has written about yet", which is the cohort the
    # screen exists to find.
    if semester is not None:
        entry_where.append(SwocEntry.semester == semester)
    entries = db.execute(
        select(SwocEntry, User.name)
        .outerjoin(User, User.id == SwocEntry.author_user_id)
        .where(*entry_where)
        .order_by(SwocEntry.student_id, SwocEntry.kind, SwocEntry.weight.desc(), SwocEntry.recorded_at)
    ).all()
    by_student: dict[str, list[SwocEntryOut]] = {}
    for e, author in entries:
        by_student.setdefault(e.student_id, []).append(_out(e, author))
    return [
        SwocStudentRow(
            student_id=sid, name=name, usn=usn,
            batch=(
                batch_labels.compose(course_name, spec_name, cohort_name, batch_label)
                if cohort_name else None
            ),
            entries=by_student.get(sid, []),
        )
        for sid, name, usn, cohort_name, batch_label, course_name, spec_name in students
    ]


# --------------------------------------------------------- the writes --


@router.post("/{student_id}", response_model=SwocEntryOut, status_code=status.HTTP_201_CREATED)
def add_entry(
    student_id: str,
    body: SwocEntryIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SwocEntryOut:
    require_capability(db, session, CAPABILITY)
    if db.get(Student, student_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such student.")
    # The board is narrowed, so the pen is narrowed. Checked AFTER the 404 so
    # the two refusals stay distinguishable to the person fixing whichever one
    # they hit: "no such student" is a bad id, "does not reach" is a grant.
    require_capability(db, session, CAPABILITY, target=ancestry_of_student(db, student_id))
    _assert_links_belong(db, student_id, body)
    e = SwocEntry(
        student_id=student_id,
        source=_source_for(db, session, student_id),
        kind=SwocKind(body.kind),
        text=body.text,
        weight=body.weight,
        author_user_id=session.get("userId"),
        # B7.4. STAMPED AT WRITE TIME AND NEVER RECOMPUTED. This is the only
        # moment anybody knows which semester a line belongs to — after the
        # student is promoted the answer is gone, which is exactly why the
        # migration refuses to backfill one onto historical rows. It may be NULL
        # here too, when the student has no semester on their row.
        semester=db.scalar(select(Student.current_semester).where(Student.id == student_id)),
        linked_skill_id=body.linked_skill_id,
        linked_session_id=body.linked_session_id,
        linked_job_id=body.linked_job_id,
    )
    db.add(e)
    db.flush()
    _audit(db, session, request, e, "CREATE", None, _snapshot(e))
    db.commit()
    db.refresh(e)
    return _out(e, session.get("name"))


@router.patch("/entries/{entry_id}", response_model=SwocEntryOut)
def edit_entry(
    entry_id: str,
    body: SwocEntryPatch,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SwocEntryOut:
    require_capability(db, session, CAPABILITY)
    e = _entry_or_404(db, entry_id)
    require_capability(db, session, CAPABILITY, target=ancestry_of_student(db, e.student_id))
    _assert_owns(session, e)
    _assert_links_belong(db, e.student_id, body)
    before = _snapshot(e)
    if body.text is not None:
        e.text = body.text
    if body.weight is not None:
        e.weight = body.weight
    # `model_fields_set` and not a truth test: an explicit null is how a link is
    # REMOVED, and `if body.linked_job_id is not None` would make removal
    # impossible while looking like it worked.
    sent = body.model_fields_set
    for field in ("linked_skill_id", "linked_session_id", "linked_job_id"):
        if field in sent:
            setattr(e, field, getattr(body, field))
    # B7.3. Set HERE and nowhere else — the model deliberately carries no
    # `onupdate`, so "this row was written to" and "this line was edited" cannot
    # be confused. The student acknowledging a line writes the same row.
    e.updated_at = datetime.now(timezone.utc)
    # B7.4's semester is NOT touched by an edit. Fixing a typo in March does not
    # move a January observation into this term.
    db.flush()
    # B7.3. The scoped copy of the before/after the audit trail has always kept.
    # Written after the flush so `_snapshot(e)` is the saved state, and inside
    # the same transaction as the edit: a revision over a rolled-back edit is a
    # record of a change that did not happen.
    db.add(SwocEntryRevision(
        entry_id=e.id, before=before, after=_snapshot(e),
        by_user_id=session.get("userId"),
    ))
    _audit(db, session, request, e, "UPDATE", before, _snapshot(e))
    db.commit()
    db.refresh(e)
    author = db.scalar(select(User.name).where(User.id == e.author_user_id)) if e.author_user_id else None
    return _out(e, author)


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_entry(
    entry_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    require_capability(db, session, CAPABILITY)
    e = _entry_or_404(db, entry_id)
    require_capability(db, session, CAPABILITY, target=ancestry_of_student(db, e.student_id))
    _assert_owns(session, e)
    _audit(db, session, request, e, "DELETE", _snapshot(e), None)
    db.delete(e)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------- the history --


class SwocRevisionOut(BaseModel):
    """One edit of one line. `before`/`after` are the snapshot `_snapshot`
    writes, so the shape is the same one `GET /api/admin/audit/{id}` returns
    and a reader who has seen one has seen both."""

    id: str
    entry_id: str
    before: dict
    after: dict
    by: str | None
    changed_at: datetime


@router.get("/{student_id}/history", response_model=list[SwocRevisionOut])
def entry_history(
    student_id: str,
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[SwocRevisionOut]:
    """Every edit to every line on one student's board, newest first — B7.3.

    SCOPED LIKE THE REST OF THE BOARD, which is the entire reason this endpoint
    exists rather than a link to `GET /api/admin/audit`. That trail has held the
    same before/after since this module shipped and is RETROACTIVE, which this
    table is not — but its gate is Main-Admin-only, with a written argument at
    `routers/audit.py` for why it is not scoped. The people who write this board
    are granted faculty, so pointing the History button at the audit API would
    be a control that answers 403 for everybody who can press it.

    Both fences, the same two as every other write here: `admin.swoc` bare, then
    again with the student's ancestry as `target`. Reading what a colleague
    wrote and then unwrote about a student is reading about that student.

    AN EMPTY LIST MEANS "NO EDIT RECORDED", NOT "NEVER EDITED". This table began
    empty at Phase 4 and a line edited last year has nothing in it; the client
    says so in those words. The Main Admin still has the older history where it
    has always been.
    """
    require_capability(db, session, CAPABILITY)
    if db.get(Student, student_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such student.")
    require_capability(db, session, CAPABILITY, target=ancestry_of_student(db, student_id))
    rows = db.execute(
        select(SwocEntryRevision, User.name)
        .join(SwocEntry, SwocEntry.id == SwocEntryRevision.entry_id)
        .outerjoin(User, User.id == SwocEntryRevision.by_user_id)
        .where(SwocEntry.student_id == student_id)
        .order_by(SwocEntryRevision.changed_at.desc())
        .limit(MAX_PAGE_SIZE)
    ).all()
    return [
        SwocRevisionOut(
            id=r.id, entry_id=r.entry_id, before=r.before, after=r.after,
            by=by, changed_at=r.changed_at,
        )
        for r, by in rows
    ]
