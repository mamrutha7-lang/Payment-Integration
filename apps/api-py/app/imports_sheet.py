"""The office's spreadsheet, read: CSV and XLSX in, judged lines out (B8.1).

WHY THIS IS NOT `app/voice_platform/queue/validation.py`, WHICH IT OTHERWISE
COPIES. That module is the precedent for every shape here — an alias table so a
sheet does not have to be renamed to be accepted, one `parse_*` that turns bytes
into rows, one `partition`-shaped split into accepted and refused, and no
HTTPException anywhere — and it is deliberately NOT extended to do this job.
`app/voice_platform/queue/__init__.py` states the constraint in its first
paragraph: `validation.py` is zipped AS-IS into the candidate-ingest Lambda and
imports nothing outside the standard library and boto3. openpyxl in that file is
a Lambda that no longer starts, discovered at the first S3 upload and nowhere
earlier. So the idiom is reused and the module is not, and the two have no
caller in common.

NOTHING HERE TOUCHES THE DATABASE, on `app/semester_bounds.py`'s rule: this
module answers "what does the file say, and is that a number at all", the router
answers "is that a student in this batch" and decides what a wrong answer costs.
A validator that could read the roster would be a second place the roster is
read from, and the one that mattered — is this USN in THIS batch — would end up
written twice.

THE LINE NUMBER IS THE OPERATOR'S, not an index into an array. A header row is
line 1 and the first record is line 2, in both formats, because the person
fixing the file is looking at Excel's own gutter. `parse_bulk` numbers its
rejects from 1 with no header allowance, which is right for an API payload and
wrong for a file somebody is about to reopen.

WHAT THE TWO DATASETS LOOK LIKE is declared once, in `DATASET_COLUMNS`, and the
templates endpoint builds the workbook FROM that constant — so the file REEP
hands out and the file REEP accepts cannot drift into two different shapes,
which is the single most likely way this feature breaks in a term's time.

ATTENDANCE IS AN AGGREGATE AND THE TABLE IS NOT. `attendance_records` holds one
row per session with a `present` boolean; the sheet the examination section
sends holds "sessions held / sessions attended". The expansion between the two
is the router's (it writes rows), but the CAP on it is here — a line claiming
900 sessions is a typo, and a typo that becomes 900 INSERTs per student per
subject is an import that times out rather than one that is refused with a
sentence.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The biggest upload this endpoint will read, matching the voice platform's
#: bulk candidate ingest (`voice_platform/api/admin.py`) so the two admin
#: uploads refuse at the same size rather than at two numbers nobody can recall.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

#: The most lines one file may carry. A batch is tens of students times a
#: handful of subjects; five thousand is an order of magnitude of headroom and
#: still a bound, which an unbounded read is not.
MAX_ROWS = 5_000

#: The most sessions one subject may claim on one line. See the module
#: docstring: this is the bound that keeps a typo from becoming a write.
MAX_SESSIONS = 400

#: Marks are out of a hundred per component. The model's own constraint
#: (`ck_subject_mark_nonnegative`) is only non-negative on purpose — "out of 50"
#: is one VTU scheme and schemes change without a migration — so the ceiling is
#: the API's, where a 422 can name it, rather than the database's.
MAX_MARK = 100

_SUFFIX_CSV = ".csv"
_SUFFIX_XLSX = ".xlsx"

ACCEPTED_SUFFIXES: tuple[str, ...] = (_SUFFIX_CSV, _SUFFIX_XLSX)

KIND_ATTENDANCE = "attendance"
KIND_MARKS = "marks"


class SheetError(ValueError):
    """The FILE could not be read: no usable header, no rows, wrong type.

    Distinct from `RowError` because the two have different consequences. A
    sheet error ends the run with `status='failed'` and an `error` sentence and
    NO `import_rows` at all — which is the one state the five counters on the
    run cannot describe, and why that column exists.
    """


class RowError(ValueError):
    """ONE LINE is wrong, and the message is what the operator reads.

    Carries the column it is about so the error report can say "external" rather
    than "row 41 failed" — `CandidateValidationError`'s reasoning, and the whole
    value of an error report.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


@dataclass(frozen=True)
class SheetRow:
    """One line of the operator's file: their line number, their cells."""

    line_no: int
    values: Mapping[str, Any]


#: canonical field -> the column headings that mean it (lower-case, trimmed).
#: Written per canonical name rather than per heading so that adding a synonym
#: is one entry and can never map two canonical fields onto one heading.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "usn": ("usn", "university seat number", "roll_no", "roll no", "roll number", "reg_no", "student_id"),
    "subject_code": ("subject_code", "subject code", "course_code", "course code", "code", "subject"),
    "subject_name": ("subject_name", "subject name", "course_name", "course name", "title"),
    "credits": ("credits", "credit", "cr"),
    "internal": ("internal", "internal_marks", "internal marks", "cie", "ia"),
    "external": ("external", "external_marks", "external marks", "see", "sea"),
    "total": ("total", "total_marks", "total marks"),
    "sgpa": ("sgpa",),
    "cgpa": ("cgpa",),
    "live_backlogs": ("live_backlogs", "live backlogs", "backlogs", "arrears", "live arrears"),
    "sessions_held": ("sessions_held", "sessions held", "classes_held", "classes held", "held", "total_sessions", "total sessions"),
    "sessions_attended": ("sessions_attended", "sessions attended", "classes_attended", "classes attended", "attended", "present"),
}

#: What each dataset's template and error report call things, in order. The
#: templates endpoint writes this row; `read_marks_row` / `read_attendance_row`
#: read it back. Optional columns are marked so the template can say so in the
#: sheet itself rather than in a wiki nobody opens.
DATASET_COLUMNS: dict[str, tuple[tuple[str, bool], ...]] = {
    KIND_MARKS: (
        ("usn", True),
        ("subject_code", True),
        ("subject_name", False),
        ("credits", False),
        ("internal", True),
        ("external", True),
        ("sgpa", False),
        ("cgpa", False),
        ("live_backlogs", False),
    ),
    KIND_ATTENDANCE: (
        ("usn", True),
        ("subject_code", True),
        ("sessions_held", True),
        ("sessions_attended", True),
    ),
}

#: One example line per dataset, written into the template under the headings.
#: A template with headings and no example is a template every office fills in
#: differently; the USN is deliberately the seed roster's shape.
DATASET_EXAMPLE: dict[str, tuple[Any, ...]] = {
    KIND_MARKS: ("1MP25MDM01", "22MBA11", "Managerial Economics", 4, 42, 38, 8.1, 7.9, 0),
    KIND_ATTENDANCE: ("1MP25MDM01", "22MBA11", 30, 26),
}

_USN_RE = re.compile(r"^[A-Za-z0-9/\-]{3,32}$")


def _normalise_heading(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _pick(values: Mapping[str, Any], field: str) -> Any:
    """The cell for a canonical field, or None. Aliases, case-insensitively."""
    for alias in _FIELD_ALIASES[field]:
        if alias in values:
            cell = values[alias]
            if cell is not None and str(cell).strip() != "":
                return cell
    return None


def _parse_csv(payload: bytes) -> list[SheetRow]:
    text = payload.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise SheetError("The file has no header row.")
    headings = [_normalise_heading(h) for h in reader.fieldnames]
    rows: list[SheetRow] = []
    # enumerate from 2: line 1 is the header the operator can see.
    for line_no, record in enumerate(reader, start=2):
        cells = {
            heading: record.get(original)
            for heading, original in zip(headings, reader.fieldnames)
            if heading
        }
        if not any(str(v or "").strip() for v in cells.values()):
            continue
        rows.append(SheetRow(line_no=line_no, values=cells))
        if len(rows) > MAX_ROWS:
            raise SheetError(f"The file has more than {MAX_ROWS} rows.")
    return rows


def _parse_xlsx(payload: bytes) -> list[SheetRow]:
    """The first worksheet of a workbook, headers from its first non-empty row.

    `read_only=True` so a large book is streamed rather than held, and
    `data_only=True` so a cell holding a FORMULA yields its last cached VALUE
    rather than the string `=SUM(...)`. A sheet exported from a college's own
    results system is full of formulas; reading them as text would reject every
    line for "not a number" and blame the operator.

    The import is INSIDE the function. openpyxl is a runtime pin
    (`requirements.txt`) and this module is imported by the router at boot, so a
    module-level import would be fine — but the parse happens on one request
    path and a lazy import keeps the cost off every other one. It does NOT make
    the dependency optional: `tools/ci/check_api_imports.py` walks this module
    against `requirements.txt` alone, and that is what proves the pin.
    """
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types
        raise SheetError(f"The workbook could not be read ({exc.__class__.__name__}).") from exc
    try:
        sheet = book.worksheets[0] if book.worksheets else None
        if sheet is None:
            raise SheetError("The workbook has no worksheets.")
        headings: list[str] | None = None
        rows: list[SheetRow] = []
        for line_no, raw in enumerate(sheet.iter_rows(values_only=True), start=1):
            cells = list(raw or ())
            if headings is None:
                if not any(str(c or "").strip() for c in cells):
                    continue
                headings = [_normalise_heading(c) for c in cells]
                continue
            if not any(str(c or "").strip() for c in cells):
                continue
            values = {
                heading: (cells[i] if i < len(cells) else None)
                for i, heading in enumerate(headings)
                if heading
            }
            rows.append(SheetRow(line_no=line_no, values=values))
            if len(rows) > MAX_ROWS:
                raise SheetError(f"The file has more than {MAX_ROWS} rows.")
        if headings is None:
            raise SheetError("The first worksheet is empty.")
        return rows
    finally:
        book.close()


def parse_sheet(payload: bytes, filename: str) -> list[SheetRow]:
    """Bytes plus the name they arrived under -> the operator's lines.

    The suffix picks the reader, as `parse_bulk` does, rather than sniffing the
    content: a college that exports "results.csv" as tab-separated is a support
    conversation, and a sniffer that guessed would turn it into a silent
    misparse instead.
    """
    if len(payload) > MAX_UPLOAD_BYTES:
        raise SheetError(
            f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    lowered = (filename or "").lower()
    if lowered.endswith(_SUFFIX_CSV):
        rows = _parse_csv(payload)
    elif lowered.endswith(_SUFFIX_XLSX):
        rows = _parse_xlsx(payload)
    else:
        raise SheetError(
            f"{filename!r} is not a spreadsheet this import reads "
            f"(use {' or '.join(ACCEPTED_SUFFIXES)})."
        )
    if not rows:
        raise SheetError("The file has a header row and no data rows.")
    return rows


# ------------------------------------------------------------ cell readers --


def _number(values: Mapping[str, Any], field: str, *, required: bool,
            low: float, high: float, integer: bool) -> float | int | None:
    raw = _pick(values, field)
    if raw is None:
        if required:
            raise RowError(field, "is required")
        return None
    text = str(raw).strip().replace(",", "")
    try:
        number = float(text)
    except ValueError:
        raise RowError(field, f"{raw!r} is not a number") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise RowError(field, f"{raw!r} is not a number")
    if not (low <= number <= high):
        raise RowError(field, f"{text} is outside {low:g}-{high:g}")
    if integer:
        if abs(number - round(number)) > 1e-9:
            raise RowError(field, f"{text} must be a whole number")
        return int(round(number))
    return round(number, 2)


def read_usn(values: Mapping[str, Any]) -> str:
    raw = _pick(values, "usn")
    if raw is None:
        raise RowError("usn", "is required")
    usn = str(raw).strip().upper()
    if not _USN_RE.match(usn):
        raise RowError("usn", f"{usn!r} is not shaped like a USN")
    return usn


def read_subject_code(values: Mapping[str, Any]) -> str:
    raw = _pick(values, "subject_code")
    if raw is None:
        raise RowError("subject_code", "is required")
    code = str(raw).strip().upper()
    if not (1 <= len(code) <= 32):
        raise RowError("subject_code", f"{code!r} is not a subject code")
    return code


def read_marks_row(values: Mapping[str, Any]) -> dict[str, Any]:
    """One marks line -> the payload `apply` writes. Raises `RowError`.

    `total` IS DERIVED AND NOT READ. A sheet that carries its own total and a
    sheet whose total disagrees with internal + external are the same file to
    this parser, and preferring the file's number would let a transcription
    error through the only arithmetic check there is.

    `passed` IS NOT INVENTED. `SubjectMark.passed` defaults True and this
    importer leaves it there: the pass mark is a property of the scheme, the
    model's own comment says schemes change without a migration, and a guessed
    threshold would mark a real pass as a failure on a student's record. What
    the eligibility engine actually reads is `live_backlogs`, and that comes
    from the file.
    """
    payload: dict[str, Any] = {
        "subject_code": read_subject_code(values),
        "internal": _number(values, "internal", required=True, low=0, high=MAX_MARK, integer=True),
        "external": _number(values, "external", required=True, low=0, high=MAX_MARK, integer=True),
        "credits": _number(values, "credits", required=False, low=0, high=30, integer=True),
        "sgpa": _number(values, "sgpa", required=False, low=0, high=10, integer=False),
        "cgpa": _number(values, "cgpa", required=False, low=0, high=10, integer=False),
        "live_backlogs": _number(values, "live_backlogs", required=False, low=0, high=60, integer=True),
    }
    name = _pick(values, "subject_name")
    payload["subject_name"] = " ".join(str(name).split())[:160] if name is not None else None
    payload["total"] = int(payload["internal"]) + int(payload["external"])
    return payload


def read_attendance_row(values: Mapping[str, Any]) -> dict[str, Any]:
    """One attendance line -> the payload `apply` expands into session rows."""
    held = _number(values, "sessions_held", required=True, low=0, high=MAX_SESSIONS, integer=True)
    attended = _number(
        values, "sessions_attended", required=True, low=0, high=MAX_SESSIONS, integer=True
    )
    assert held is not None and attended is not None  # required=True
    if attended > held:
        raise RowError(
            "sessions_attended",
            f"{attended} attended is more than the {held} held",
        )
    return {
        "subject_code": read_subject_code(values),
        "sessions_held": int(held),
        "sessions_attended": int(attended),
        # The percentage the screen shows, computed once here so the preview,
        # the error report and the grid cannot each round it differently.
        # `None` and not 0.0 when nothing was held: a subject with no sessions
        # is unmeasured, and 0% is a claim about a student who was never asked
        # to attend anything. The same distinction `_attendance_pct` now makes.
        "attendance_percent": round(100 * attended / held, 1) if held else None,
    }


READERS = {KIND_MARKS: read_marks_row, KIND_ATTENDANCE: read_attendance_row}


def template_rows(kind: str) -> tuple[list[str], list[Any], list[str]]:
    """The heading row, the example row, and a note per column, for the .xlsx.

    Built from `DATASET_COLUMNS` rather than typed again — see the module
    docstring: the file REEP hands out and the file REEP reads are the same
    list or they are two shapes that drift.
    """
    columns = DATASET_COLUMNS[kind]
    header = [name for name, _required in columns]
    notes = [
        "required" if required else "optional — leave blank if you do not have it"
        for _name, required in columns
    ]
    return header, list(DATASET_EXAMPLE[kind]), notes


def describe_shape(kind: str) -> str:
    """One sentence naming the columns, for a 422 and for the templates sheet."""
    columns = DATASET_COLUMNS[kind]
    required = [name for name, is_required in columns if is_required]
    optional = [name for name, is_required in columns if not is_required]
    sentence = f"One row per student per subject: {', '.join(required)}"
    if optional:
        sentence += f" (optional: {', '.join(optional)})"
    return sentence + "."

