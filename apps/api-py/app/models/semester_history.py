"""Every time a student's semester moved, and who moved it.

WHY A TABLE AND NOT A COLUMN. `students.current_semester` is a position, not a
history: it answers "where is this student now" and destroys the answer to
"when did they get there, and who decided". Promotion is a batch act performed
by the office on dozens of students at once — the single most consequential
write on the roster screen — and the one question asked afterwards is always
"who promoted this batch, when, and why", which a position cannot answer.

THE ROW IS THE RECORD OF AN ACADEMIC ACT, and the numbers on it are a snapshot.
`from_semester` / `to_semester` are stored rather than derived from the
neighbouring rows, because a later correction (a hold-back, an ungraduate) must
not silently rewrite what an earlier promotion said at the time it happened.

`kind` IS A PLAIN STRING, NOT A PG ENUM — app/models/institution.py's stated
house rule, for its stated reason: `promote | graduate | ungraduate | hold_back`
will grow, and a new value should be a data change rather than a `CREATE TYPE`
migration carrying all three of AGENTS.md's enum gotchas. It is validated at the
API edge, where a bad value is a 422 naming the allowed set.

NOTHING ELSE IS REWRITTEN BY A PROMOTION. `semester_results.semester`,
`english_baselines.semester` and `courses.semester` are facts about a semester
that has already happened or about the curriculum, and every one of them sits
under a unique constraint that a rewrite would collide on mid-batch, leaving a
half-promoted cohort. This table is the only new row a promotion writes.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


#: What a history row records. Validated at the API edge against this tuple.
KIND_PROMOTE = "promote"
KIND_GRADUATE = "graduate"
KIND_UNGRADUATE = "ungraduate"
KIND_HOLD_BACK = "hold_back"

SEMESTER_HISTORY_KINDS: tuple[str, ...] = (
    KIND_PROMOTE,
    KIND_GRADUATE,
    KIND_UNGRADUATE,
    KIND_HOLD_BACK,
)


class StudentSemesterHistory(Base):
    """One student, one semester move."""

    __tablename__ = "student_semester_history"
    __table_args__ = (
        # The same floor `ck_student_semester_target` puts on the live position.
        # A move TO semester 0 is not a correction, it is a bug in the caller.
        CheckConstraint(
            "from_semester >= 1 AND to_semester >= 1",
            name="ck_semester_history_bounds",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # NOT NULL and no `ondelete`: a promotion record with no student is not a
    # record of anything, and the database REFUSES to delete a student out from
    # under one. The two destructors take these rows first — children before
    # parents — which is the only path that deletes a student at all.
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    from_semester: Mapped[int] = mapped_column(Integer)
    to_semester: Mapped[int] = mapped_column(Integer)
    # The date the office says the move took effect, which is routinely not the
    # date somebody clicked the button — a batch is promoted after the results
    # are out and backdated to the start of term.
    effective_on: Mapped[date] = mapped_column(Date)
    # WHO decided. Nullable and SET NULL for `redesign_audit_events`'s reason:
    # `python -m app.purge_people` deletes every account but the Main Admin, and
    # the fact that a batch was promoted outlives the account of whoever did it.
    # Remove the person, keep the record.
    by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Free text from the dialog. Nullable: the office is asked for one and a
    # promotion is not refused without it — unlike disabling an account, a
    # promotion's effect is plainly visible on the roster afterwards.
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String, default=KIND_PROMOTE, server_default=KIND_PROMOTE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
