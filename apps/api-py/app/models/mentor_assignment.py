"""Who mentored whom, from when to when, and who decided — B9.1.

WHY A TABLE AND NOT A COLUMN, said the way `student_semester_history` says it:
`students.mentor_id` is a POSITION. It answers "who mentors this student now"
and destroys the answer to "who mentored them before, when did that change, who
moved them and why". The pointer stays exactly where it is and stays the single
thing rule 2 filters on; this is history BESIDE it, never a replacement. Nothing
reads a mentor's current group from here, and `mentor_functions.py`'s argument
against a stored copy of "do you mentor anybody" is untouched: that question is
still asked of `students.mentor_id`, live.

------------------------------------------------------------------------------
THE ROW IS A PERIOD, AND A PERIOD HAS TWO ENDS
------------------------------------------------------------------------------

One row per (student, mentor) spell. `to_at IS NULL` means the spell is open,
which is what makes "one open row per current pair" a statement a query can
check rather than a convention a writer has to remember.

04's column list is `(student_id, mentor_id, from_at, to_at, by_user_id, reason,
kind)` — ONE `by_user_id`, ONE `reason`, ONE `kind` — and that list cannot
describe a period, because a period is opened by one act and closed by another.
Written that way, a reassignment either overwrites who made the original
assignment (losing "who seated this student with Dr Rao, and why") or leaves the
closing act unrecorded (losing "who moved them off, and why"), and the second
question is the one the mentor-load screen's history card exists to answer. So
there are two sets of three:

    kind / by_user_id / reason              the act that OPENED this spell
    end_kind / ended_by_user_id / end_reason   the act that CLOSED it

and all four of 04's `kind` values live across the two columns:
`assign` / `reassign` open, `release` / `reassign` / `faculty_disabled` close.
Neither set is ever rewritten once written.

`kind` IS A PLAIN STRING, NOT A PG ENUM — the house rule
`app/models/semester_history.py` and `app/models/institution.py` both state, for
the stated reason: this vocabulary will grow (`graduated`, `transferred`,
`cohort_moved` are all plausible), and a new value should be a data change and
not a `CREATE TYPE` migration carrying all three of AGENTS.md's enum gotchas.
`capability_grants.capability` and `auth_tokens.purpose` are String for the same
reason. It is validated at the API edge against the tuples below.

------------------------------------------------------------------------------
`from_at` IS NULLABLE, AND THE NULL MEANS SOMETHING
------------------------------------------------------------------------------

Every pair standing on the day this table arrived was seated by a writer that
kept no record of when. The migration seeds those rows from `students.created_at`
where the pointer and the account plausibly date together, and NULL where it
cannot: a NULL `from_at` reads as **"mentoring since before this was recorded"**
and the history card says exactly that. The one thing it must never be is
`now()`, which would tell every reader that the whole roster was seated on
deploy day.

A student who has never had a mentor gets NO ROW AT ALL. "Never had a mentor"
and "has had one since forever" are different facts and an empty history must
not be able to say the second.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


#: How a spell BEGINS.
KIND_ASSIGN = "assign"  # the student had no mentor
KIND_REASSIGN = "reassign"  # the student was moved from another mentor

#: How a spell ENDS.
END_RELEASE = "release"  # released to the unassigned pool, no successor
END_REASSIGN = KIND_REASSIGN  # moved on to somebody else
END_FACULTY_DISABLED = "faculty_disabled"  # the faculty account was offboarded

OPEN_KINDS: tuple[str, ...] = (KIND_ASSIGN, KIND_REASSIGN)
END_KINDS: tuple[str, ...] = (END_RELEASE, END_REASSIGN, END_FACULTY_DISABLED)

#: Stamped by migration `e1c4b7a209d6` on a spell it closed because a SECOND
#: open spell existed for that student -- which the writer's read-then-write
#: could produce under concurrency until `uq_mentor_assignment_one_open_spell`
#: forbade it.
#:
#: DELIBERATELY NOT IN `END_KINDS`. That tuple is the vocabulary of human acts,
#: and no operator ever released, reassigned or offboarded anybody here; a
#: repair that borrowed one of those words would put a decision in somebody's
#: mouth on the screen that reports it. A reader that renders `END_KINDS` and
#: nothing else should show this spell as closed without a reason it can name,
#: which is the truth.
END_DUPLICATE_OPEN = "duplicate_open"

#: 04's four, as one set, for the API edge and for anybody grepping the spec.
ASSIGNMENT_KINDS: tuple[str, ...] = (
    KIND_ASSIGN,
    END_RELEASE,
    KIND_REASSIGN,
    END_FACULTY_DISABLED,
)

#: How long a released mentor keeps READ access to the student they handed over
#: (B9.1). Ninety days is 04's number and it is a handover, not a tenure: long
#: enough that the incoming mentor can ask about a note written in March, short
#: enough that it lapses without anybody having to remember to revoke it.
#:
#: THE WINDOW IS NOT ENFORCED FROM THIS TABLE. It is the `expires_at` on the
#: `mentor.mentees` grant `app/mentor_history.py` mints, so expiry is filtered in
#: SQL by `_live_grant_clauses` and revoking the grant in Governance actually
#: closes the door. A second copy of the rule here — "to_at > now - 90 days" —
#: would keep the door open after a revocation, which is the whole reason the
#: grant was chosen over a branch that reads this table.
HANDOVER_DAYS = 90


class MentorAssignment(Base):
    """One student, one mentor, one spell."""

    __tablename__ = "mentor_assignments"
    __table_args__ = (
        # (student_id, to_at) serves both reads: the FK index requirement
        # (`test_every_foreign_key_column_is_indexed` wants student_id LEADING)
        # and "the open row for this student", which is every writer's first
        # question. Same shape for the mentor side.
        Index("ix_mentor_assignment_student_open", "student_id", "to_at"),
        Index("ix_mentor_assignment_mentor_open", "mentor_id", "to_at"),
        # ONE OPEN SPELL PER STUDENT, as a constraint and not a sentence
        # (e1c4b7a209d6). The paragraph above says `to_at IS NULL` is what makes
        # "one open row per current pair" checkable by a query; nothing checked
        # it, and Postgres accepted a second open row for the same student.
        #
        # `record_mentor_change` reads the open row, closes it, opens the next --
        # a read-then-write, so two concurrent assignments for one student both
        # see no open row and both insert. The console posts one request per
        # ticked student on a batch save, which is that shape exactly.
        #
        # PARTIAL, and the predicate is the whole point: a plain
        # `UNIQUE (student_id, to_at)` permits this duplicate rather than
        # forbidding it, because NULLs are distinct in Postgres, so the pairs
        # (student, NULL) and (student, NULL) do not collide. Same shape as
        # `uq_interview_consent_active` and the three other "one live row" rules.
        #
        # Declared here as well as in the migration: an index that exists only in
        # a migration is drift, and `alembic check` asking to drop it every run is
        # how a real drop goes unnoticed.
        Index(
            "uq_mentor_assignment_one_open_spell",
            "student_id",
            unique=True,
            postgresql_where=text("to_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # NOT NULL and no `ondelete`, exactly as `student_semester_history` has it:
    # a spell with no student records nothing, and the database refuses to
    # delete a student out from under one. The two destructors take these rows
    # before the students, which is the only path that deletes a student at all.
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), nullable=False)
    # The GROUP, not the faculty user: `students.mentor_id` points at a `Mentor`
    # row and that is the join every reader already makes. `mentors` rows are
    # never deleted, so this never dangles.
    mentor_id: Mapped[str] = mapped_column(ForeignKey("mentors.id"), nullable=False)

    #: When this spell began — NULL on a row the migration seeded and could not
    #: date. See the module docstring; it is not a missing value, it is
    #: "since before this was recorded".
    from_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: NULL while the spell is open. This is the current pair.
    to_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: The act that opened it. One of OPEN_KINDS.
    kind: Mapped[str] = mapped_column(String(32), default=KIND_ASSIGN, server_default=KIND_ASSIGN)
    #: Who opened it. Nullable and SET NULL for `redesign_audit_events`' reason:
    #: `purge_people` removes every account but the Main Admin, and the fact
    #: that a student was seated with a mentor outlives the account of whoever
    #: seated them. Remove the person, keep the record.
    by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Why. REQUIRED at the API edge from B9.2 onwards and nullable here, for
    #: the reason every column on this table is nullable: rows that predate the
    #: rule exist and inventing words for them would put a sentence in an
    #: administrator's mouth.
    reason: Mapped[str | None] = mapped_column(String(400), nullable=True)

    #: The act that closed it. One of END_KINDS. NULL while open.
    end_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ended_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    end_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
