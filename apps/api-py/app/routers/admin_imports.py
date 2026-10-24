"""Spreadsheet imports — the history, and the gate the rest of B8.1 lands on.

    GET /admin/imports        the runs this session may see, newest first

ITS OWN MODULE, mounted under the same `/admin` prefix as `admin_students.py`,
`admin_promotion.py` and `admin_faculty.py`: the house precedent for a new admin
surface, and the two files that would otherwise have grown (`console.py` and
`admin.py`) are ~1700 lines each. The client sees one flat surface.

ALL FIVE ENDPOINTS ARE HERE NOW — preview, apply, the history, the error report
and the file templates. The history landed first with the schema, because a
capability key with no call site is a promise the API does not keep
(`app/models/governance.py`, and `tools/ci/check_capability_enforcement.py`
fails the build over it); the other four needed the parser
(`app/imports_sheet.py`) and land with it.

PREVIEW WRITES, AND WHAT IT WRITES IS NOT A STUDENT RECORD. 04-backend-changes.md
says the preview changes nothing, and that is exactly true of the data the
office cares about: not one row of `attendance_records`, `semester_results` or
`subject_marks` is touched until somebody presses Import rows. The run and its
parsed lines ARE written, because they have to be — `apply` takes a `run_id` and
reads the judged lines back rather than re-parsing a file nobody kept, which is
what makes "the rows I reviewed are the rows that were written" true. A preview
nobody applies is a `previewed` run with `rows_applied = 0`, which is the
resting state of a file somebody read and thought better of.

THE PARSE IS ON THE SERVER AND MUST STAY THERE. The screen's header comment says
why from the other side: a browser that read the spreadsheet itself would draw
rows under a Check column that no validator ever saw. Every verdict in the
preview response was reached against this batch's roster, this course's semester
count and this deployment's subject catalogue.

THE SCOPE, AND WHY IT IS NOT `reach.student_ids()`. An import run names a BATCH,
not a student: `app/scope_views.py::import_run_scope_clause` is the projection
of one reach onto `import_runs`, written there beside the registrations one for
the same reason — a run, like an application, is not a `students` row and the
model that does not belong to `policies.py` should not be taught to it.

RULE 2 IS NOT IN PLAY ON THE HISTORY LIST AND IS ON EVERY OTHER ENDPOINT HERE.
A run row is a filename, a batch, five counts and a date; it names no student.
The preview, the apply and the error report name every student in the file, one
line each, so all three take `require_capability(..., target=...)` against the
BATCH'S ancestry (`governance.ancestry_of_cohort`) as well as this key — and the
batch rather than the students, because the batch a new cohort's first results
file is imported into has nobody seated in it yet, and a fence that walked the
students would find none and refuse the office.
"""

import io
import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..db import get_db
from ..exports import carries_personal_columns, csv_response, drop_personal, record_export
from ..governance import ancestry_of_cohort, require_capability
from ..identity import get_current_session
from ..institution_domains import college_id_for_cohort
from ..imports_sheet import (
    MAX_UPLOAD_BYTES,
    RowError,
    SheetError,
    describe_shape,
    parse_sheet,
    read_attendance_row,
    read_marks_row,
    read_usn,
    template_rows,
)
from ..models.academics import SemesterResult, SubjectMark
from ..models.attendance import AttendanceRecord
from ..models.cohort import Cohort
from ..models.course import Course
from ..models.data_import import (
    IMPORT_KINDS,
    KIND_MARKS,
    STATUS_APPLIED,
    STATUS_FAILED,
    STATUS_PREVIEWED,
    VERDICT_ERROR,
    VERDICT_OK,
    VERDICT_WARNING,
    ImportRow,
    ImportRun,
)
from ..models.user import Student, User
from ..policies import scope_filter
from ..scope_views import import_run_scope_clause, scope_header
from ..semester_bounds import ceiling_for_cohort, rejection

router = APIRouter(prefix="/admin", tags=["admin"])

#: The capability this whole surface is gated by. Declared once and passed by
#: NAME, as `admin_students.py`, `swoc.py` and `interview_bank.py` do — the AST
#: guard resolves a module-level constant, so the four endpoints still to come
#: must use this rather than retyping the string.
CAPABILITY = "admin.imports"

#: How many runs the history answers with. The screen's grid is a history, not
#: an archive; an office that imports weekly reaches a year back at this cap.
MAX_RUNS_LISTED = 50


class ImportRunOut(BaseModel):
    id: str
    kind: str
    status: str
    college_id: str | None
    cohort_id: str | None
    #: The batch's own label, resolved here so the grid does not fetch the
    #: hierarchy to render one column. NULL when the batch has been deleted —
    #: which the run survives, keeping its counts (`ImportRun.cohort_id` is
    #: SET NULL, and that model says why).
    cohort_label: str | None
    semester: int | None
    filename: str | None
    rows_total: int
    rows_ok: int
    rows_warning: int
    rows_rejected: int
    rows_applied: int
    #: Why a run FAILED on the file rather than on a line. Null on every other
    #: status, and the only thing that can describe a run with no rows at all.
    error: str | None
    by_user_id: str | None
    by_name: str | None
    created_at: datetime
    applied_at: datetime | None


@router.get("/imports", response_model=list[ImportRunOut])
def import_history(
    response: Response,
    kind: str | None = None,
    cohort_id: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[ImportRunOut]:
    """Every import this session may see, newest first (B8.1, B1.4).

    `?kind=` and `?cohort_id=` NARROW WITHIN THE REACH AND CANNOT WIDEN IT: they
    are extra predicates on a query that already carries the scope clause, which
    is `mentor_load`'s pattern and the one B1.4 asks for. A `cohort_id` outside
    the caller's reach therefore answers an empty list rather than a 403 — the
    row is not refused, it was never in the set.

    `reach.nothing` IS CHECKED FIRST and answers an empty list with
    `X-Reep-Scope: none`, never the unnarrowed table. The header is what lets
    the screen say "you cannot see any batches" rather than "there are no
    imports" — opposite facts that must not render the same.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    scope_header(response, reach)
    if reach.nothing:
        return []

    query = (
        select(ImportRun, Cohort.batch_label, User.name)
        .outerjoin(Cohort, ImportRun.cohort_id == Cohort.id)
        .outerjoin(User, ImportRun.by_user_id == User.id)
        .where(import_run_scope_clause(reach))
    )
    if kind:
        query = query.where(ImportRun.kind == kind)
    if cohort_id:
        query = query.where(ImportRun.cohort_id == cohort_id)
    rows = db.execute(
        query.order_by(ImportRun.created_at.desc(), ImportRun.id).limit(MAX_RUNS_LISTED)
    ).all()
    return [
        ImportRunOut(
            id=run.id,
            kind=run.kind,
            status=run.status,
            college_id=run.college_id,
            cohort_id=run.cohort_id,
            cohort_label=batch_label,
            semester=run.semester,
            filename=run.filename,
            rows_total=run.rows_total,
            rows_ok=run.rows_ok,
            rows_warning=run.rows_warning,
            rows_rejected=run.rows_rejected,
            rows_applied=run.rows_applied,
            error=run.error,
            by_user_id=run.by_user_id,
            by_name=by_name,
            created_at=run.created_at,
            applied_at=run.applied_at,
        )
        for run, batch_label, by_name in rows
    ]


# --------------------------------------------------------------- the parse --

#: How many judged lines the preview response carries back. The file may hold
#: `imports_sheet.MAX_ROWS`; the grid pages ten to a hundred at a time and the
#: reviewer is looking for the refusals, which the error report carries in full.
#: A cap here rather than none, because a five-thousand-row JSON body is a
#: screen that hangs on a spinner and a proxy that may not forward it at all.
MAX_PREVIEW_ROWS_RETURNED = 1_000

#: The `?kind=` values the templates endpoint will build a workbook for, spelled
#: as the URL spells them. Same tuple as the model's, named here so the 422 can
#: list it without importing the vocabulary twice.
TEMPLATE_KINDS = IMPORT_KINDS

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ImportPreviewRowOut(BaseModel):
    """One judged line, in the shape the preview grid draws.

    EVERY NUMBER IS NULLABLE and a missing one renders as a dash, which is the
    house rule the readiness screen and the mentee views already follow: a
    confident `0` where nothing was read tells the reviewer the file said zero.
    """

    line_no: int
    verdict: str
    #: The USN exactly as it was typed in the sheet — the whole value of the
    #: report is that it names what the operator can search for in Excel.
    usn: str | None
    student_id: str | None
    student_name: str | None
    #: What will be written, or why the line was refused.
    message: str
    subject_code: str | None = None
    subject_name: str | None = None
    credits: int | None = None
    internal: int | None = None
    external: int | None = None
    total: int | None = None
    sgpa: float | None = None
    cgpa: float | None = None
    live_backlogs: int | None = None
    sessions_held: int | None = None
    sessions_attended: int | None = None
    attendance_percent: float | None = None


class ImportPreviewOut(BaseModel):
    run: ImportRunOut
    #: The judged lines, capped at `MAX_PREVIEW_ROWS_RETURNED`. Compare against
    #: `run.rows_total` to know whether the grid is showing all of them.
    rows: list[ImportPreviewRowOut]
    #: How many lines `apply` would write — ok plus warning. Named rather than
    #: left to the client to add up, because "84 valid" and "84 saved" are the
    #: two numbers this screen must never confuse (see `ImportRun.rows_applied`).
    rows_to_write: int


def _run_out(db: Session, run: ImportRun) -> ImportRunOut:
    """One run, with the two names the grid draws beside it."""
    batch_label = (
        db.scalar(select(Cohort.batch_label).where(Cohort.id == run.cohort_id))
        if run.cohort_id
        else None
    )
    by_name = (
        db.scalar(select(User.name).where(User.id == run.by_user_id)) if run.by_user_id else None
    )
    return ImportRunOut(
        id=run.id,
        kind=run.kind,
        status=run.status,
        college_id=run.college_id,
        cohort_id=run.cohort_id,
        cohort_label=batch_label,
        semester=run.semester,
        filename=run.filename,
        rows_total=run.rows_total,
        rows_ok=run.rows_ok,
        rows_warning=run.rows_warning,
        rows_rejected=run.rows_rejected,
        rows_applied=run.rows_applied,
        error=run.error,
        by_user_id=run.by_user_id,
        by_name=by_name,
        created_at=run.created_at,
        applied_at=run.applied_at,
    )


def _reachable_run(db: Session, session: dict, run_id: str) -> ImportRun:
    """The run, if this session's grant reaches it — and 404 if it does not.

    404 AND NOT 403, deliberately, and the same choice `_assert_can_decide`
    makes on a leave paper: a run outside the reach is not a row this holder is
    being refused, it is a row that was never in their set. A 403 would confirm
    that a marks file exists for a batch they cannot see, which is the one bit
    the fence is there to withhold.
    """
    require_capability(db, session, CAPABILITY)
    reach = scope_filter(db, session, CAPABILITY)
    if reach.nothing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found.")
    run = db.scalar(
        select(ImportRun).where(ImportRun.id == run_id, import_run_scope_clause(reach))
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found.")
    if run.cohort_id:
        # The batch fence as well as the reach: the rows below name every
        # student in it. `ancestry_of_cohort` is the one walk, and a run whose
        # batch has been deleted (`cohort_id` SET NULL) is already visible only
        # to a reach of everything, which is where the clause above left it.
        require_capability(db, session, CAPABILITY, target=ancestry_of_cohort(db, run.cohort_id))
    return run


def _roster(db: Session, cohort_id: str) -> dict[str, tuple[str, str | None]]:
    """USN (upper-cased) -> (student id, name) for one batch.

    ONE READ FOR THE WHOLE FILE. The alternative — a SELECT per line — is five
    thousand round trips on a file the office will upload once a term, and it is
    how an import that works on a demo cohort times out on a real one.

    A student with no USN on record is not in this map and cannot be matched,
    which is correct: the file names people by USN and a blank cannot name
    anybody.
    """
    rows = db.execute(
        select(Student.id, Student.usn, User.name)
        .join(User, Student.user_id == User.id)
        .where(Student.cohort_id == cohort_id, Student.usn.is_not(None))
    ).all()
    return {str(usn).strip().upper(): (sid, name) for sid, usn, name in rows}


def _known_subject_codes(db: Session) -> set[str]:
    """The taught subjects, upper-cased. `courses.code` is the catalogue.

    AN UNKNOWN CODE IS A REFUSAL AND NOT A WARNING. `attendance_records.
    course_code` is a plain string with no foreign key, so an unknown code
    writes cleanly and then sits in the database naming nothing — the dashboard
    draws it with no subject name, the weekly chart counts it, and nobody can
    tell a typo from a subject the catalogue is missing. Refusing the line names
    the typo while the operator still has the file open.
    """
    return {str(code).strip().upper() for code in db.scalars(select(Course.code)).all()}


def _existing_marks(db: Session, student_ids: list[str], semester: int) -> set[tuple[str, str]]:
    if not student_ids:
        return set()
    rows = db.execute(
        select(SemesterResult.student_id, SubjectMark.subject_code)
        .join(SubjectMark, SubjectMark.semester_result_id == SemesterResult.id)
        .where(
            SemesterResult.student_id.in_(student_ids),
            SemesterResult.semester == semester,
        )
    ).all()
    return {(sid, str(code).upper()) for sid, code in rows}


def _existing_attendance(db: Session, student_ids: list[str]) -> set[tuple[str, str]]:
    if not student_ids:
        return set()
    rows = db.execute(
        select(AttendanceRecord.student_id, AttendanceRecord.course_code)
        .where(AttendanceRecord.student_id.in_(student_ids))
        .distinct()
    ).all()
    return {(sid, str(code).upper()) for sid, code in rows}


@router.post(
    "/imports/preview",
    response_model=ImportPreviewOut,
    status_code=status.HTTP_201_CREATED,
)
async def preview_import(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form(...),
    cohort_id: str = Form(...),
    semester: int | None = Form(default=None),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ImportPreviewOut:
    """Read a spreadsheet against one batch and judge every line (B8.1).

    NO STUDENT RECORD IS TOUCHED. `attendance_records`, `semester_results` and
    `subject_marks` are untouched until `apply`; what this writes is the run and
    its judged lines, which is what `apply` reads back (see the module
    docstring).

    A FILE THAT CANNOT BE READ AT ALL STILL LEAVES A RECEIPT. The run is written
    with `status='failed'` and the reason in `error`, COMMITTED, and only then
    is the 422 raised — so the history shows the attempt and the screen gets the
    sentence. Raising first would roll the receipt back, and "I uploaded it and
    nothing happened" is the support call this table exists to answer.

    THE SEMESTER IS THE RUN'S, not a column in the file. It is required for
    marks, because `semester_results` is keyed on it. For ATTENDANCE it is
    accepted and recorded on the run as provenance and WRITTEN NOWHERE:
    `attendance_records` has no semester column at all, and inventing one here
    would be a schema change smuggled in through an importer. A dry run asking
    "which semester is this attendance for" is answerable from the receipt and
    from nothing else.
    """
    require_capability(db, session, CAPABILITY)
    if kind not in IMPORT_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown import kind {kind!r}. Use one of: {', '.join(IMPORT_KINDS)}.",
        )
    cohort = db.get(Cohort, cohort_id)
    if cohort is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found.")
    require_capability(db, session, CAPABILITY, target=ancestry_of_cohort(db, cohort_id))

    if kind == KIND_MARKS and semester is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A marks import must name the semester it is for.",
        )
    if semester is not None:
        if semester < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Semester 1 is the first semester.",
            )
        # 4a's one answer to "how long is this programme", through the batch's
        # course. `rejection()` writes the sentence so the importer and the
        # roster editor refuse in the same words.
        refusal = rejection(ceiling_for_cohort(db, cohort_id), semester)
        if refusal:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=refusal
            )

    # read(MAX + 1), never read(), and the ORDER is the whole point: the check
    # below compares `len(payload)`, so an unbounded read has already spent the
    # memory the cap exists to protect by the time the comparison runs — the
    # refusal arrives after the damage it was written to prevent. One byte past
    # the ceiling is all the comparison needs. Nothing sits in front of uvicorn
    # on the AWS deployment to bound a request body for us (no nginx
    # `client_max_body_size`), so this process's own read is the only bound
    # there is. `routers/student.py::create_upload` says the same sentence and
    # every document-store writer follows it.
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    filename = (file.filename or "upload")[:255]
    if len(payload) > MAX_UPLOAD_BYTES:
        # Refused BEFORE a run row is written: an upload this size is a wrong
        # file rather than a failed read, and a receipt saying "too large" in
        # the history would be noise the office cannot act on.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    run = ImportRun(
        kind=kind,
        status=STATUS_PREVIEWED,
        college_id=college_id_for_cohort(db, cohort_id),
        cohort_id=cohort_id,
        semester=semester,
        filename=filename,
        by_user_id=session.get("userId"),
    )
    db.add(run)
    db.flush()

    def _audit(action: str, after: dict) -> None:
        record_change(
            db,
            session=session,
            request=request,
            tenant_id=None,
            entity_type="import_run",
            entity_id=run.id,
            action=action,
            before=None,
            after=after,
            event_type=f"import.{action.lower()}",
            payload=after,
        )

    try:
        sheet = parse_sheet(payload, filename)
    except SheetError as exc:
        run.status = STATUS_FAILED
        run.error = str(exc)
        _audit("IMPORT_PREVIEW_FAILED", {"kind": kind, "cohort_id": cohort_id,
                                         "filename": filename, "error": str(exc)})
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    roster = _roster(db, cohort_id)
    known_codes = _known_subject_codes(db)
    student_ids = [sid for sid, _name in roster.values()]
    already = (
        _existing_marks(db, student_ids, semester)
        if kind == KIND_MARKS
        else _existing_attendance(db, student_ids)
    )
    reader = read_marks_row if kind == KIND_MARKS else read_attendance_row

    counts = {VERDICT_OK: 0, VERDICT_WARNING: 0, VERDICT_ERROR: 0}
    seen: set[tuple[str, str]] = set()
    out: list[ImportPreviewRowOut] = []

    for line in sheet:
        usn: str | None = None
        student_id: str | None = None
        student_name: str | None = None
        parsed: dict = {}
        verdict = VERDICT_OK
        message = ""
        try:
            usn = read_usn(line.values)
            match = roster.get(usn)
            if match is None:
                raise RowError("usn", f"{usn} is not a student in this batch")
            student_id, student_name = match
            parsed = reader(line.values)
            code = parsed["subject_code"]
            if code not in known_codes:
                raise RowError(
                    "subject_code",
                    f"{code} is not a subject in the catalogue",
                )
            key = (student_id, code)
            if key in seen:
                # `partition`'s rule: the SECOND appearance is the reject, so
                # the first — the one the operator probably meant — still goes
                # in, and the report names the line to delete.
                raise RowError(
                    "subject_code",
                    f"{code} appears twice for {usn} in this file",
                )
            seen.add(key)
            if key in already:
                verdict = VERDICT_WARNING
                message = f"{code} — overwrites the record already on file"
            else:
                message = _describes(kind, parsed)
        except RowError as exc:
            verdict = VERDICT_ERROR
            message = exc.message if exc.field == "usn" else f"{exc.field}: {exc.message}"

        counts[verdict] += 1
        db.add(
            ImportRow(
                run_id=run.id,
                line_no=line.line_no,
                usn=usn,
                student_id=student_id,
                verdict=verdict,
                message=message,
                payload=parsed if verdict != VERDICT_ERROR else {},
            )
        )
        if len(out) < MAX_PREVIEW_ROWS_RETURNED:
            out.append(
                ImportPreviewRowOut(
                    line_no=line.line_no,
                    verdict=verdict,
                    usn=usn,
                    student_id=student_id,
                    student_name=student_name,
                    message=message,
                    **{
                        field: parsed.get(field)
                        for field in (
                            "subject_code", "subject_name", "credits", "internal",
                            "external", "total", "sgpa", "cgpa", "live_backlogs",
                            "sessions_held", "sessions_attended", "attendance_percent",
                        )
                    },
                )
            )

    run.rows_total = len(sheet)
    run.rows_ok = counts[VERDICT_OK]
    run.rows_warning = counts[VERDICT_WARNING]
    run.rows_rejected = counts[VERDICT_ERROR]
    _audit(
        "IMPORT_PREVIEW",
        {
            "kind": kind,
            "cohort_id": cohort_id,
            "semester": semester,
            "filename": filename,
            "rows_total": run.rows_total,
            "rows_ok": run.rows_ok,
            "rows_warning": run.rows_warning,
            "rows_rejected": run.rows_rejected,
        },
    )
    db.commit()
    db.refresh(run)
    return ImportPreviewOut(
        run=_run_out(db, run),
        rows=out,
        rows_to_write=counts[VERDICT_OK] + counts[VERDICT_WARNING],
    )


def _describes(kind: str, parsed: dict) -> str:
    """The sentence the preview's message column shows for an accepted line."""
    if kind == KIND_MARKS:
        return (
            f"{parsed['subject_code']} — internal {parsed['internal']}, "
            f"external {parsed['external']}, total {parsed['total']}"
        )
    percent = parsed.get("attendance_percent")
    shown = "no sessions held" if percent is None else f"{percent}%"
    return (
        f"{parsed['subject_code']} — {parsed['sessions_attended']} of "
        f"{parsed['sessions_held']} sessions ({shown})"
    )


# --------------------------------------------------------------- the write --


class ImportApplyOut(BaseModel):
    run: ImportRunOut
    #: How many lines were written. Distinct from `run.rows_ok` on purpose —
    #: refused lines are skipped, so "84 valid" must never read as "84 saved".
    rows_applied: int
    #: What those lines became, per table, so the confirmation can say
    #: "86 subject marks across 22 students" rather than "done".
    students_touched: int
    records_written: int


def _apply_marks(db: Session, run: ImportRun, rows: list[ImportRow]) -> tuple[int, int]:
    """Write one marks run: a `semester_results` row per student, marks under it.

    THE SEMESTER RESULT IS GET-OR-CREATE AND THE SUBJECT MARK IS AN UPSERT, and
    both are forced by unique constraints that already exist: `uq_semester_result
    (student_id, semester)` and `uq_subject_mark (semester_result_id,
    subject_code)`. An INSERT-only importer works once and 500s on the re-run
    that follows every correction, which is the normal case and not the edge.

    SGPA, CGPA AND LIVE BACKLOGS ARE PER-STUDENT FACTS ON A PER-SUBJECT SHEET.
    They repeat down every line for one student; the FIRST line that carries a
    value wins and later ones are ignored rather than overwriting it, so a sheet
    that fills the column on row one and leaves it blank below does not end with
    the student's CGPA cleared by their last subject.
    """
    by_student: dict[str, list[ImportRow]] = {}
    for row in rows:
        by_student.setdefault(str(row.student_id), []).append(row)

    existing = {
        sid: rid
        for rid, sid in db.execute(
            select(SemesterResult.id, SemesterResult.student_id).where(
                SemesterResult.student_id.in_(list(by_student)),
                SemesterResult.semester == run.semester,
            )
        ).all()
    }
    written = 0
    for student_id, student_rows in by_student.items():
        result_id = existing.get(student_id)
        if result_id is None:
            result = SemesterResult(student_id=student_id, semester=run.semester)
            db.add(result)
            db.flush()
            result_id = result.id
        else:
            result = db.get(SemesterResult, result_id)
        for field in ("sgpa", "cgpa", "live_backlogs"):
            value = next(
                (r.payload.get(field) for r in student_rows if r.payload.get(field) is not None),
                None,
            )
            if value is not None:
                setattr(result, field, value)
        for row in student_rows:
            payload = row.payload
            values = {
                "semester_result_id": result_id,
                "subject_code": payload["subject_code"],
                "subject_name": payload.get("subject_name") or payload["subject_code"],
                "credits": payload.get("credits") if payload.get("credits") is not None else 4,
                "internal": payload["internal"],
                "external": payload["external"],
                "total": payload["total"],
            }
            statement = pg_insert(SubjectMark).values(id=_new_id(), **values)
            db.execute(
                statement.on_conflict_do_update(
                    constraint="uq_subject_mark",
                    set_={
                        key: statement.excluded[key]
                        for key in ("subject_name", "credits", "internal", "external", "total")
                    },
                )
            )
            row.applied = True
            written += 1
    return len(by_student), written


def _apply_attendance(db: Session, run: ImportRun, rows: list[ImportRow]) -> tuple[int, int]:
    """Write one attendance run, expanding an aggregate into session rows.

    THE SHEET IS AN AGGREGATE AND THE TABLE IS NOT. `attendance_records` holds
    one row per session with a `present` boolean and a unique
    `(student_id, course_code, session_no)`; the file says "26 of 30". So 30
    rows are written, numbered 1..30, the first 26 present. That is not a
    modelling preference — `_attendance_pct` counts rows, the analytics screen
    buckets them by date, and any representation that collapses them (two rows,
    or one with a count) makes every existing reader of this table wrong.

    RE-IMPORTING A SMALLER NUMBER DELETES THE TAIL. A correction from 30 held to
    28 must remove sessions 29 and 30, or the student keeps two absences from a
    number the office has already withdrawn. The delete is scoped to
    `session_no > held` for that student and subject and touches nothing else.

    THE DATE IS THE RUN'S, AND IT IS NOT A CLAIM ABOUT WHEN THE CLASS HAPPENED.
    The sheet carries no per-session date and the column is NOT NULL. Stamping
    the import's own timestamp is the honest reading — "this is what was
    recorded, as of this file" — and it is why the weekly attendance chart on
    the analytics screen shows an imported term as one bucket rather than
    spread across it. A per-session date column on the sheet is the only thing
    that could fix that, and inventing dates to make the chart look right would
    put fictions on a student's record.
    """
    stamped = run.created_at or datetime.now(timezone.utc)
    students: set[str] = set()
    written = 0
    for row in rows:
        student_id = str(row.student_id)
        code = row.payload["subject_code"]
        held = int(row.payload["sessions_held"])
        attended = int(row.payload["sessions_attended"])
        students.add(student_id)
        db.execute(
            delete(AttendanceRecord).where(
                AttendanceRecord.student_id == student_id,
                AttendanceRecord.course_code == code,
                AttendanceRecord.session_no > held,
            )
        )
        for session_no in range(1, held + 1):
            statement = pg_insert(AttendanceRecord).values(
                id=_new_id(),
                student_id=student_id,
                course_code=code,
                session_no=session_no,
                session_date=stamped,
                present=session_no <= attended,
            )
            db.execute(
                statement.on_conflict_do_update(
                    constraint="uq_attendance",
                    set_={
                        "present": statement.excluded.present,
                        "session_date": statement.excluded.session_date,
                    },
                )
            )
            written += 1
        row.applied = True
    return len(students), written


def _new_id() -> str:
    """A row id, the same way every model here defaults one.

    Written explicitly because `pg_insert` bypasses the ORM — a Python-side
    `default=` never runs on a Core INSERT, and the column is a NOT NULL primary
    key, so the upsert would fail on the first row without this.
    """
    return uuid.uuid4().hex


@router.post("/imports/{run_id}/apply", response_model=ImportApplyOut)
def apply_import(
    run_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ImportApplyOut:
    """Write a previewed run's accepted lines — one transaction, audited (B8.1).

    ONE TRANSACTION FOR THE WHOLE FILE. Every write below happens in the
    request's own session and there is exactly one `commit`, at the end, so a
    file that fails halfway leaves the database as it was. A per-row commit
    would leave a half-imported semester with no way to tell which half, on a
    screen whose counters would then be lying about both.

    RE-APPLYING IS REFUSED WITH 409 rather than silently repeated. The writes
    themselves are upserts and would be harmless, but an apply is an audited act
    on a student's marks and a second one is almost always a double-click or a
    stale tab; answering "this file was already imported, on the 3rd, by Anita"
    is more use than doing it again. Correcting an import means previewing the
    corrected file, which leaves its own receipt.

    REFUSED LINES ARE SKIPPED, NOT WRITTEN. `rows_applied` is therefore ≤
    `rows_ok + rows_warning`, and a warning line IS written — a warning means
    "this overwrites what is on file", which is what an import is for.
    """
    run = _reachable_run(db, session, run_id)
    if run.status == STATUS_APPLIED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This file has already been imported"
                + (f" ({run.applied_at:%d %b %Y})." if run.applied_at else ".")
                + " Preview the corrected file to import it again."
            ),
        )
    if run.status == STATUS_FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This file could not be read, so there is nothing to import.",
        )
    if run.kind == KIND_MARKS and run.semester is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This marks run names no semester and cannot be written.",
        )

    rows = list(
        db.scalars(
            select(ImportRow)
            .where(
                ImportRow.run_id == run.id,
                ImportRow.verdict != VERDICT_ERROR,
                ImportRow.student_id.is_not(None),
            )
            .order_by(ImportRow.line_no)
        ).all()
    )
    if run.kind == KIND_MARKS:
        students_touched, written = _apply_marks(db, run, rows)
    else:
        students_touched, written = _apply_attendance(db, run, rows)

    run.status = STATUS_APPLIED
    run.applied_at = datetime.now(timezone.utc)
    run.rows_applied = len(rows)
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="import_run",
        entity_id=run.id,
        action="IMPORT_APPLY",
        before={"status": STATUS_PREVIEWED, "rows_applied": 0},
        after={
            "status": STATUS_APPLIED,
            "kind": run.kind,
            "cohort_id": run.cohort_id,
            "semester": run.semester,
            "filename": run.filename,
            "rows_applied": len(rows),
            "students_touched": students_touched,
            "records_written": written,
        },
        event_type="import.apply",
        payload={"run_id": run.id, "kind": run.kind, "rows_applied": len(rows)},
    )
    db.commit()
    db.refresh(run)
    return ImportApplyOut(
        run=_run_out(db, run),
        rows_applied=run.rows_applied,
        students_touched=students_touched,
        records_written=written,
    )


# -------------------------------------------------------- the error report --

#: The column that NAMES a person, as opposed to the USN they typed themselves.
#: See the endpoint's docstring for why the two are treated differently.
_PERSONAL_COLUMNS = ("Student",)


@router.get("/imports/{run_id}/errors.csv")
def import_errors_csv(
    run_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Every line this run refused or flagged, as a file to fix and re-upload.

    WARNINGS ARE IN IT AS WELL AS ERRORS, and the Check column says which. A
    report that listed only the refusals would hide the lines that silently
    OVERWROTE a result already on file, which is the half of an import an
    examination section most wants to see afterwards.

    THE USN STAYS AND THE NAME IS THE PII COLUMN. B14's rule is that the
    columns which name a person are a separate decision from the file, gated on
    `admin.students` (the roster function) rather than on the export capability
    — and here the two halves genuinely differ. The USN is a string this caller
    uploaded minutes ago in their own spreadsheet; withholding it would make the
    report unusable for its only purpose, which is finding line 41 in Excel. The
    NAME is the roster's answer to that USN and is withheld without the roster
    function, exactly as `students.csv` withholds it.

    IT LEAVES A RECEIPT, through `record_export`, for every other export's
    reason: the file is the thing that cannot be recalled.
    """
    run = _reachable_run(db, session, run_id)
    reach = scope_filter(db, session, CAPABILITY)
    carried = carries_personal_columns(db, session)

    rows = db.execute(
        select(ImportRow, User.name)
        .outerjoin(Student, ImportRow.student_id == Student.id)
        .outerjoin(User, Student.user_id == User.id)
        .where(ImportRow.run_id == run.id, ImportRow.verdict != VERDICT_OK)
        .order_by(ImportRow.line_no)
    ).all()

    header = ["Line", "Check", "USN", "Student", "Subject", "Message"]
    body = [
        [
            row.line_no,
            row.verdict,
            row.usn or "",
            name or "",
            row.payload.get("subject_code") or "",
            row.message or "",
        ]
        for row, name in rows
    ]
    header, body = drop_personal(header, body, _PERSONAL_COLUMNS, carry=carried)
    record_export(
        db,
        session=session,
        request=request,
        kind="import_errors",
        filters={"run_id": run.id, "kind": run.kind, "cohort_id": run.cohort_id},
        rows=len(body),
        carried_pii=carried,
    )
    return csv_response(
        header,
        body,
        f"import-{run.kind}-{run.id[:8]}-errors.csv",
        reach=reach,
        carried_pii=carried,
    )


# ------------------------------------------------------------- the template --


@router.get("/imports/templates/{kind}.xlsx")
def import_template(
    kind: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """The blank workbook for one dataset, built from the parser's own columns.

    THE TEMPLATE AND THE PARSER READ ONE CONSTANT. `imports_sheet.
    DATASET_COLUMNS` is what this writes and what `preview` reads back; a
    template typed out separately is the single most likely way this feature
    breaks a term from now, because the two would drift by one renamed heading
    and the office would be told their own file is wrong.

    IT CARRIES NO STUDENT DATA — it is a header row, one example line and a note
    per column — but it is still behind `admin.imports` rather than open: the
    column list tells an unauthenticated reader exactly what shape of file this
    deployment will accept, and there is no reason for that to be public.
    """
    require_capability(db, session, CAPABILITY)
    if kind not in TEMPLATE_KINDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No template for {kind!r}. Use one of: {', '.join(TEMPLATE_KINDS)}.",
        )
    import openpyxl

    header, example, notes = template_rows(kind)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = kind.capitalize()
    sheet.append(header)
    sheet.append(example)
    sheet.append([])
    sheet.append([describe_shape(kind)])
    for column, note in zip(header, notes):
        sheet.append([column, note])
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return Response(
        content=buffer.getvalue(),
        media_type=_XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="reep-{kind}-template.xlsx"',
        },
    )
