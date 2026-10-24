"""B6.2 — copying an interview's four numbers somewhere the 180-day clock does
not reach.

`interview_score_summaries` exists because two promises in this product point in
opposite directions. `retention.purge_expired` deletes what a student SAID and
what the model privately reasoned about them, on a rolling 180-day window, and
that deletion is a promise. "You scored 61, then 68, then 74" is a trend a
student and their mentor need for longer than six months, and it quotes nobody.
So the scores are copied out when the interview ends and kept; the transcript
goes.

ONE BUILDER, FOUR WRITERS, AND THAT IS THE WHOLE POINT OF THIS MODULE.

    Layer 1  routers/interview.py::_make_finalizer        the relay's own close
    Layer 2  routers/interview.py::_finalize_if_running   the router's backstop
    Layer 3  retention.finalize_orphaned_interviews       the process that died
    (later) app.backfill_interview_summaries              everything already on file

Four places copy eight values from one row to another, and the columns are named
the same on both sides precisely so a mis-mapping is visible (`overall_score` to
`overall_score`, never `overall`). A mis-mapped column in one of four copies
would put communication scores in the domain column for exactly the interviews
that ended one particular way — correct on every screen a developer looks at,
wrong for the students whose sessions crashed. `build_summary` is the one copy;
everything else is a caller.

IDEMPOTENT BY CONSTRAINT, NOT BY CHECK. `uq_interview_summary_session` is what
makes the three finalization layers safe against each other, the same way one
`AND status = 'running'` predicate makes the finalization itself safe: the loser
of a race takes an IntegrityError and treats it as the no-op it is. The
read-then-write in `ensure_summary` is an optimisation that saves the usual case
an exception, never the thing that makes this correct.

IT NEVER RAISES INTO A CLOSING INTERVIEW. `ensure_summary` returns a bool and
logs its own failures. The three finalization layers exist to make sure a record
never lies about being `running`; a bookkeeping copy that could raise inside one
of them would be a summary table that costs the record it summarises.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models.interview import (
    InterviewEvaluation,
    InterviewScoreSummary,
    InterviewSession,
)

log = logging.getLogger(__name__)


def build_summary(
    session_row: InterviewSession, evaluation: InterviewEvaluation | None
) -> InterviewScoreSummary:
    """The one copy. No database, no commit, no decision about whether to write.

    `evaluation is None` is a real and expected state — an interview that never
    reached WRAP_UP, or one whose finalizer has not yet written the courtesy
    `unavailable` row — and every score then stays NULL. NEVER 0: a missing
    score and a zero mean opposite things to whoever reads the trend, and a
    zero plotted at the origin would say "this student scored nothing", which is
    a verdict on an interview that was never marked.

    The row is summarised for EVERY terminal status, not only `completed`.
    "Three attempts abandoned in the first minute" is the fact a mentor most
    needs to see, and a table of clean completions only would hide exactly the
    student who is in trouble.
    """
    return InterviewScoreSummary(
        student_id=session_row.student_id,
        session_id=session_row.id,
        # The track CODE as the session recorded it — NULL is the generic
        # interview, exactly as on `interview_sessions.specialization`.
        track_code=session_row.specialization,
        started_at=session_row.started_at,
        status=session_row.status,
        overall_score=None if evaluation is None else evaluation.overall_score,
        communication_score=(
            None if evaluation is None else evaluation.communication_score
        ),
        domain_score=None if evaluation is None else evaluation.domain_score,
        structure_score=None if evaluation is None else evaluation.structure_score,
    )


def ensure_summary(db: Session, interview_session_id: str) -> bool:
    """Write this interview's summary if it has ended and has none. Commits.

    Returns True only when a row was written by THIS call, so a caller can log
    the unusual case (Layer 2 or Layer 3 writing one means Layer 1 never ran)
    without the usual case saying anything at all.

    A `running` interview is skipped and that is not a guard against a race — it
    is the correct answer. Its status is not terminal, so a summary written now
    would record a status that is about to change, and the layer that closes it
    writes the summary immediately afterwards.

    SWALLOWS EVERYTHING, AND SAYS SO IN THE LOG. Every caller is a finalization
    path whose actual job is to stop a record claiming it is still running. That
    job must not be lost to a bookkeeping copy, and a summary that failed to
    write is recoverable for six months by
    `python -m app.backfill_interview_summaries` — which is the other half of
    why that module is not a note in a pull request.
    """
    try:
        row = db.execute(
            select(InterviewSession, InterviewEvaluation)
            .outerjoin(
                InterviewEvaluation,
                InterviewEvaluation.interview_session_id == InterviewSession.id,
            )
            .where(InterviewSession.id == interview_session_id)
        ).first()
        if row is None:
            return False
        session_row, evaluation = row
        if session_row.status == "running":
            return False
        already = db.scalar(
            select(InterviewScoreSummary.id).where(
                InterviewScoreSummary.session_id == interview_session_id
            )
        )
        if already is not None:
            return False
        db.add(build_summary(session_row, evaluation))
        db.commit()
        return True
    except IntegrityError:
        # The constraint, doing the job the read above only shortcuts: another
        # finalization layer (or the backfill, running on a live deployment)
        # wrote the same summary between the SELECT and the INSERT. The row
        # exists, which is the whole requirement.
        db.rollback()
        return False
    except Exception:
        db.rollback()
        log.exception(
            "Could not write the score summary for interview session %s. The "
            "interview record itself is unaffected; "
            "`python -m app.backfill_interview_summaries` recovers this row "
            "until retention reaps the interview.",
            interview_session_id,
        )
        return False
