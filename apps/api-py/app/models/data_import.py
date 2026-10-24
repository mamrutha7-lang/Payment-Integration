"""The office's spreadsheet imports: one row per run, one row per line (B8.1).

WHAT THIS IS FOR. Attendance and semester marks arrive as a spreadsheet from the
examination section, and until B8.1 there was no way to get them into REEP at
all — `attendance_records` and `semester_results` were written by the seed and
by nothing else. That is why `_attendance_pct` answered 0.0 on a deployment
nobody had imported into, and why the readiness screen spent a year telling
students they fail an attendance gate that had never been measured. That helper
now answers None and the readiness card says "not measured" (B8.5, 2026-09-13),
so the two halves of the problem are separated: the SCREEN no longer lies about
an absence, and this table is what finally ends the absence.

WHY TWO TABLES AND NOT ONE STATUS COLUMN. A preview must be able to show every
line and its verdict BEFORE anything is written — that is the whole shape of the
screen (`apps/web/src/app/features/admin/imports/`), and its header comment says
why: a browser that parsed the file itself would draw rows no validator ever
checked. So the parse is a row per line in `import_rows`, `apply` reads them
back, and the run's `status` is the only thing that says whether the writing
happened. One table with a `payload` blob could not answer "which four lines
were rejected, and why" without re-parsing a file nobody kept.

THE FILE ITSELF IS NOT STORED. A second `document_store.save_bytes` writer needs
its own quota (see that module's contract) and would add this table to
`purge_people.FILE_COLUMNS`; nothing in 04-backend-changes.md asks for it, and
the parsed rows below are a better record than the bytes anyway — they are what
was actually read, not what was uploaded. If that ever changes, both of those
obligations land with it.

`kind` AND `status` ARE PLAIN STRINGS, not PG enums. AGENTS.md's house rule and
its three enum gotchas: a third dataset (the Jobs sheet still draws an "Import
postings" button) must be a data change and not a `CREATE TYPE` migration. The
vocabularies are the tuples below and are validated at the API edge, where a bad
value is a 422 naming the allowed set.

`verdict` IS THE CLIENT'S OWN WORD, DELIBERATELY. `ok | warning | error` is what
`import-dataset.ts::ImportRowVerdict` already declares and what the preview
grid's Check column already renders. Inventing a fourth server-side word here
would mean a translation table in the router, and a translation table is where
"rejected" quietly starts rendering as a pass.

THE COUNTERS ON THE RUN ARE A SUMMARY, NOT THE RECORD. `import_rows` is
authoritative; the five integers exist so the history grid draws fifty runs
without fifty `GROUP BY`s, and so a run that failed to parse AT ALL (no rows,
`error` set) still reports something true. Write them in the same transaction as
the rows they count.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


#: What a spreadsheet holds. 04-backend-changes.md's two datasets, and the two
#: `IMPORT_DATASETS` offers on screen.
KIND_ATTENDANCE = "attendance"
KIND_MARKS = "marks"

IMPORT_KINDS: tuple[str, ...] = (KIND_ATTENDANCE, KIND_MARKS)

#: Where a run got to. `previewed` is the resting state of a file somebody read
#: and did not import — the common one, and not a failure.
STATUS_PREVIEWED = "previewed"
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"

IMPORT_STATUSES: tuple[str, ...] = (STATUS_PREVIEWED, STATUS_APPLIED, STATUS_FAILED)

#: A line's verdict, in the client's vocabulary (see the module docstring).
#: `warning` is a line that WILL be written and that the operator should look at
#: — marks that overwrite a result already on file, most often.
VERDICT_OK = "ok"
VERDICT_WARNING = "warning"
VERDICT_ERROR = "error"

IMPORT_ROW_VERDICTS: tuple[str, ...] = (VERDICT_OK, VERDICT_WARNING, VERDICT_ERROR)


class ImportRun(Base):
    """One spreadsheet, read once, for one batch and one semester."""

    __tablename__ = "import_runs"
    __table_args__ = (
        # The floor `students.current_semester` already carries. A run naming
        # semester 0 is a bug in the caller, not a correction.
        CheckConstraint("semester IS NULL OR semester >= 1", name="ck_import_run_semester"),
        # The table's one read pattern: the fifty most recent runs, newest
        # first, narrowed by the caller's reach.
        Index("ix_import_run_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(
        String, default=STATUS_PREVIEWED, server_default=STATUS_PREVIEWED
    )
    # WHERE THE RUN HANGS ON THE SPINE, and the reason both columns are here
    # rather than just the batch: `app/scope_views.py::import_run_scope_clause`
    # answers a college-scoped reach off `college_id` without a join, and every
    # deeper rung off the batch. NULLABLE, like every other spine pointer in
    # this schema — a run whose batch was deleted keeps its filename, its counts
    # and its rows, and stops being visible to anybody narrower than the office.
    #
    # No `ondelete` on the college: the spine's convention is that the database
    # REFUSES to delete a rung that still has rows under it, and a college is
    # archived rather than deleted.
    college_id: Mapped[str | None] = mapped_column(
        ForeignKey("colleges.id"), nullable=True, index=True
    )
    # SET NULL, and this one DOES depart from that convention on purpose.
    # `DELETE /api/admin/cohorts/{id}` exists and refuses only when students are
    # seated; an empty batch that was once imported into would otherwise fail
    # that delete on a foreign key — a 500 with no sentence explaining it, over
    # a receipt. The receipt survives the batch; it just stops naming one.
    cohort_id: Mapped[str | None] = mapped_column(
        ForeignKey("cohorts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    semester: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filename: Mapped[str | None] = mapped_column(String, nullable=True)

    # The summary. See the module docstring: `import_rows` is authoritative.
    rows_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_ok: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_warning: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: How many lines `apply` actually wrote. Zero on a `previewed` run, and
    #: separate from `rows_ok` because a run can be applied twice over — the
    #: second pass writing nothing — and "84 valid" must not read as "84 saved".
    rows_applied: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Why a `failed` run failed, when the failure was the FILE and not a line:
    #: an unreadable workbook, a missing header column, a sheet with no rows.
    #: A run that failed this way has no `import_rows` at all, which is exactly
    #: the case the counters above cannot describe.
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    # WHO read the file. Nullable and SET NULL for `student_semester_history`'s
    # reason — remove the person, keep the record — even though both destructors
    # empty this table outright: the column must not be the thing that makes an
    # account delete fail on a foreign key.
    by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImportRow(Base):
    """One line of one spreadsheet, as it was read and as it was judged."""

    __tablename__ = "import_rows"
    __table_args__ = (
        # One row per line per run, which makes re-reading the same file into
        # the same run an UPDATE rather than a second copy of every line.
        #
        # It also LEADS WITH run_id, which is what indexes that foreign key
        # (tests/test_codebase_guards.py::test_every_foreign_key_column_is_indexed);
        # an `index=True` on the column as well would be a strict prefix of this
        # constraint and would fail `test_no_index_duplicates_the_prefix_of_another`.
        UniqueConstraint("run_id", "line_no", name="uq_import_row_line"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("import_runs.id", ondelete="CASCADE"))
    #: The line number in the OPERATOR'S file, header included, so the error
    #: report names the line they can see in Excel rather than an index into an
    #: array they cannot.
    line_no: Mapped[int] = mapped_column(Integer)
    #: The USN exactly as typed in the sheet, whether or not it resolved. This
    #: is the whole value of the error report: "1MP25MDM0I is not a USN in this
    #: batch" is actionable, "row 41 failed" is not.
    usn: Mapped[str | None] = mapped_column(String, nullable=True)
    #: The student it resolved to, or NULL when it did not. SET NULL rather than
    #: CASCADE: the line keeps the USN as typed even after the record it named
    #: is gone, which is what a receipt is for.
    student_id: Mapped[str | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"), nullable=True, index=True
    )
    verdict: Mapped[str] = mapped_column(String, default=VERDICT_OK, server_default=VERDICT_OK)
    #: One sentence: what will be written, or why the line was refused. It is
    #: the preview grid's "Subjects / message" column and the error report's.
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    #: The parsed cells, verbatim — subject codes and marks, or sessions held
    #: and attended. A dict rather than columns because the two datasets do not
    #: share a shape and a table that tried to hold both would be half NULL on
    #: every row.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    #: Did `apply` write this line? Not derivable from the run's status: an
    #: applied run skips its own rejected lines, and a re-applied run writes
    #: nothing at all.
    applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
