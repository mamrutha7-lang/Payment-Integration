"""Fill `interview_score_summaries` from the interviews already on file:

    python -m app.backfill_interview_summaries --dry-run   # counts, writes nothing
    python -m app.backfill_interview_summaries             # writes

THIS HAS A CLOCK ON IT, which is why it is a module and not a note in a pull
request. B6.2's summary is written at finalization from now on, but every
interview held BEFORE that code shipped has no summary — and
`retention.purge_expired` is deleting those interviews on a rolling 180-day
window every night. Each night this does not run, some student's first
attempts stop being recoverable, silently, and the trend on their progress
screen starts at whatever survived.

Prod-runnable and deliberately NOT guarded on `ENV=prod`, for `app.seed_kb`'s
and `app.seed_roster`'s reason: it creates no account, mints no credential and
writes no student-authored text — only four integers, a date and a status,
copied from rows that are already in this database. Production is exactly where
it belongs. What replaces the refusal is printing the target database and the
counts before writing anything.

IDEMPOTENT. Keyed on `interview_score_summaries.session_id`, which carries a
unique constraint, so a second run writes nothing and a run interrupted halfway
can simply be run again. It is also safe to run alongside the live finalizer:
the loser of a race takes the IntegrityError as the no-op it is.

WHAT IT CANNOT DO, said plainly rather than left for somebody to discover:

  * An interview that has ALREADY been hard-deleted is gone. Nothing in this
    database records that it happened, so it cannot be counted, let alone
    recovered — a report reading "0 unrecoverable" would be a claim this module
    has no way to make. It says "unknown" instead.
  * A finished interview whose model never returned a scorecard has no scores.
    It is still summarised, with NULLs, because "attempted and abandoned three
    times" is a fact a mentor needs and a table of clean completions only would
    hide exactly the student in trouble. NULL is never turned into 0 (see
    `interview_evaluations`: a missing score and a zero mean opposite things).
  * A `running` interview is skipped. It has not ended, its status is not final,
    and its own finalizer will write the summary when it does.

Soft-deleted interviews ARE included, and that is the urgent half: those are the
rows inside the grace window, already redacted, and about to be hard-deleted.
Their scores are exactly what B6.2 exists to keep.
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .interview_summary import build_summary
from .models.interview import (
    InterviewEvaluation,
    InterviewScoreSummary,
    InterviewSession,
)


def db_target() -> str:
    """host:port/database — never the whole URL, which carries the password.

    The same helper `app.seed_roster.db_target` prints, for the same reason:
    this module runs on production and the operator's only defence against
    pointing it at the wrong database is being told which one it is.
    """
    try:
        parts = urlsplit(settings.sqlalchemy_url)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
        name = (parts.path or "/?").lstrip("/") or "?"
        return f"{host}{port}/{name}"
    except ValueError:
        return "?"


def backfill(db: Session, *, dry_run: bool = False) -> dict[str, int]:
    """Write one summary per ended interview that has none. Returns the counts.

    The query is a LEFT JOIN against the summaries rather than a "not in
    (select session_id …)": `session_id` is NULLABLE by design — it is set NULL
    when the interview is reaped — and `NOT IN` over a column containing NULLs
    matches nothing at all in SQL. That would make this module silently write
    zero rows the first time a reaped summary existed, which is the day it
    matters most.
    """
    rows = db.execute(
        select(InterviewSession, InterviewEvaluation)
        .outerjoin(
            InterviewEvaluation,
            InterviewEvaluation.interview_session_id == InterviewSession.id,
        )
        .outerjoin(
            InterviewScoreSummary,
            InterviewScoreSummary.session_id == InterviewSession.id,
        )
        .where(
            InterviewSession.status != "running",
            InterviewScoreSummary.id.is_(None),
        )
        .order_by(InterviewSession.started_at)
    ).all()

    counts = {
        "candidates": len(rows),
        "written": 0,
        "with_scores": 0,
        "without_scores": 0,
        "soft_deleted_rescued": 0,
        "skipped_conflict": 0,
    }

    for session_row, evaluation in rows:
        if evaluation is not None and evaluation.overall_score is not None:
            counts["with_scores"] += 1
        else:
            counts["without_scores"] += 1
        if session_row.deleted_at is not None:
            counts["soft_deleted_rescued"] += 1
        if dry_run:
            continue

        # THE SAME BUILDER THE THREE FINALIZATION LAYERS USE. Eight values are
        # copied from one row to another here and in `interview_summary`, and
        # the columns are deliberately named the same on both sides so that a
        # mis-mapping would be visible; the way to keep it visible is for there
        # to be ONE copy. A second one here would drift the first time either
        # table gained a column — and it would drift only for the interviews
        # this module touches, which are precisely the ones nobody is watching.
        summary = build_summary(session_row, evaluation)
        # One row at a time, each in its own savepoint. A single flush at the
        # end would lose the whole run to one concurrent finalizer winning a
        # race on one session — and this module exists precisely to be run on a
        # live deployment, where that race is expected rather than exotic.
        try:
            with db.begin_nested():
                db.add(summary)
        except IntegrityError:
            counts["skipped_conflict"] += 1
            continue
        counts["written"] += 1

    if not dry_run:
        db.commit()
    return counts


def _report(counts: dict[str, int], total_summaries: int, *, dry_run: bool) -> None:
    verb = "would write" if dry_run else "wrote"
    print(f"interviews without a summary: {counts['candidates']}")
    print(f"  {verb}: {counts['written']}")
    print(f"  with a scorecard: {counts['with_scores']}")
    print(
        f"  with no scorecard (summarised with NULL scores, never zeros): "
        f"{counts['without_scores']}"
    )
    print(
        "  already soft-deleted, i.e. inside the grace window and about to be "
        f"hard-deleted: {counts['soft_deleted_rescued']}"
    )
    if counts["skipped_conflict"]:
        print(
            f"  skipped, a summary appeared while this ran: "
            f"{counts['skipped_conflict']}"
        )
    print(f"summaries on file now: {total_summaries}")
    # See the module docstring. This is the honest form of 04's "M sessions
    # already past retention and unrecoverable": the number is not knowable from
    # here, and printing 0 would assert something this module cannot check.
    print(
        "interviews hard-deleted before this ran: UNKNOWN and unrecoverable — "
        "nothing in this database records that they existed."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.backfill_interview_summaries",
        description=(
            "Write the B6.2 score summary for every interview already on file. "
            "Idempotent, safe to re-run, safe to run on production."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count what would be written and write nothing",
    )
    args = parser.parse_args(argv)

    print(f"database: {db_target()}")
    with SessionLocal() as db:
        counts = backfill(db, dry_run=args.dry_run)
        total = db.scalar(select(func.count()).select_from(InterviewScoreSummary)) or 0
        _report(counts, total, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
