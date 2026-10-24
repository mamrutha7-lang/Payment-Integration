"""The college's interview policy (B6.1, B6.4): what is kept, for how long, and
how many attempts a day.

WHAT THIS REPLACES, AND WHAT IT DOES NOT. Until now the three storage scopes
were the STUDENT's, ticked on a consent panel, and the caps were one number in
`app/config.py` for the whole deployment. B6.1 makes the scopes the college's
decision and B6.4 makes the caps theirs too. What it does **not** replace is the
record that a student was told: `interview_consents` stays exactly where it is,
`interview_sessions.consent_id` still pins the exact grant an interview ran
under, and close 4013 / 4014 still mean what they mean. The row a student now
holds is an ACKNOWLEDGEMENT of this policy rather than a choice between three
boxes — and AGENTS.md's "three separate booleans, because one boolean makes
'they consented' unfalsifiable" survives intact, because three booleans are what
gets copied onto it.

ONE ROW PER (COLLEGE, COURSE), AND NULL COURSE IS THE COLLEGE'S DEFAULT. The
resolver reads the (college, course) row first and the (college, NULL) row
second — the same two-step `placement_criteria` already uses. Postgres treats
NULLs as distinct, so the unique constraint below cannot by itself stop a second
default row per college; the partial unique index is what does, and without it a
college would have two defaults and the resolver would pick whichever the
planner returned.

THE CAPS ARE TWO CEILINGS AND THAT IS DELIBERATE (B6.4). 04-backend-changes.md
asks for one cap counting `status='completed'` only. On its own that DELETES the
abuse control the current code exists to provide: `_open_records`' comment says
it plainly — every session, finished or not, billed an upstream Bedrock
handshake, and "a cap that only counts clean finishes is a cap a crash loop
never hits". A student reconnecting in a loop would never reach `completed` and
would never be stopped. So there are two numbers:

  * `daily_cap`     — COMPLETED interviews in the rolling 24 h. The student's
                      practice allowance, and the one the screen shows them.
                      An interview that dropped out at minute two no longer
                      costs them a turn, which is the whole point of B6.4.
  * `attempt_cap`   — EVERY session row in the same window, whatever its
                      status. The spend ceiling. Higher than `daily_cap` by
                      construction (the CHECK below refuses the other order,
                      which would make `daily_cap` unreachable), and when it is
                      the one that trips, the 4015 close reason should say so:
                      "too many attempts" and "you have used your 8 practice
                      interviews" are different sentences to a student.

RETENTION IS A PROMISE THAT TRAVELS WITH THE ROW, NOT WITH THIS TABLE.
`interview_sessions.retention_until` is STORED at open (started_at + the window
in force then) precisely so that changing a number here cannot retroactively
re-date interviews a student was already promised 180 days for. Lowering
`retention_days` therefore applies to interviews held AFTER the change, and the
console must say that rather than implying a sweep.

NO PG ENUMS HERE EITHER (§6.1). This table has no vocabulary column at all, and
should not grow one.
"""

from __future__ import annotations

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


#: The defaults a college inherits until somebody edits its row, and they are
#: the values already in force: `settings.interview_retention_days` (180),
#: `settings.interview_max_per_student_per_day` (8) and
#: `settings.nova_sonic_connection_seconds` (480 — Bedrock's own 8-minute stream
#: wall, which is the real ceiling on a session however long
#: `interview_max_seconds` says). A deployment that never opens the policy
#: screen behaves exactly as it does today; that is the compatibility rule.
DEFAULT_RETENTION_DAYS = 180
DEFAULT_DAILY_CAP = 8
DEFAULT_ATTEMPT_CAP = 20
DEFAULT_TIME_LIMIT_SECONDS = 480


class InterviewPolicy(Base):
    """One college's (or one course's) interview rules."""

    __tablename__ = "interview_policies"
    __table_args__ = (
        UniqueConstraint("college_id", "course_id", name="uq_interview_policy_scope"),
        # The college's DEFAULT row — see the module docstring. NULLs are
        # distinct, so the constraint above does not cover this case.
        Index(
            "uq_interview_policy_college_default",
            "college_id",
            unique=True,
            postgresql_where=text("course_id IS NULL"),
        ),
        # The FK index for course_id; college_id leads the unique constraint.
        Index("ix_interview_policies_course_id", "course_id"),
        # Bounds, not opinions. Each one refuses a value that would break
        # something silently rather than loudly:
        #   * retention 0 would delete an interview the moment it ended;
        #   * a cap below 1 would switch the feature off through a number that
        #     reads like a limit — switching it off is a different decision and
        #     needs its own control;
        #   * attempt_cap < daily_cap makes daily_cap unreachable, so the
        #     student's allowance would silently become the spend ceiling;
        #   * the time limit's floor is a minute (anything less cannot hold an
        #     opening question) and its ceiling is an hour. The REAL ceiling is
        #     the engine's connection wall and is applied at read time, because
        #     it moves with the provider and a CHECK would freeze it here.
        CheckConstraint(
            "retention_days >= 1 AND daily_cap >= 1 AND attempt_cap >= daily_cap "
            "AND time_limit_seconds BETWEEN 60 AND 3600",
            name="ck_interview_policy_bounds",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    #: NOT NULL: a policy belongs to a college. The programme-wide fallback is
    #: the absence of a row, and it is `config.py`'s defaults — not a row with
    #: NULLs, which would be a second place for the same answer to live.
    #: No `ondelete`: the spine's convention, the database refuses to delete a
    #: college that still has rows under it.
    college_id: Mapped[str] = mapped_column(ForeignKey("colleges.id"))
    #: NULL is the college's default row (see the module docstring).
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_courses.id"), nullable=True
    )

    #: Whether the turns are kept at all. FALSE suppresses both the `messages`
    #: rows and the `interview_turns` rows — and the interview then stamps
    #: `interview_sessions.transcript_suppressed`, because otherwise
    #: `turns_emitted` > `turns_persisted` on such a session reads exactly like
    #: AGENTS.md's runbook signal for dropped writes. The REPORT is still
    #: written: a scorecard is what the student and their mentor read, and it
    #: quotes nobody.
    store_transcript: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    #: One of the THREE switches recording needs, and the only one a college
    #: controls. The other two are `INTERVIEW_RECORDING_ENABLED` (the operator's)
    #: and the student's own live `scope_store_audio` grant. `recorder_for` must
    #: require all three; a policy that turned recording on over a student who
    #: was never told would be the one failure this whole area is built to
    #: prevent.
    store_audio: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    #: Days. Read at OPEN and stamped onto `interview_sessions.retention_until`;
    #: never applied retroactively (see the module docstring).
    retention_days: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_RETENTION_DAYS, server_default=str(DEFAULT_RETENTION_DAYS)
    )
    #: COMPLETED interviews per rolling 24 h — the student's practice allowance.
    daily_cap: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_DAILY_CAP, server_default=str(DEFAULT_DAILY_CAP)
    )
    #: EVERY session row per rolling 24 h — the spend ceiling. See the module
    #: docstring: this is the half of B6.4 the spec leaves out, and without it
    #: counting completions alone hands a reconnect loop unlimited billable
    #: Bedrock handshakes.
    attempt_cap: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_ATTEMPT_CAP, server_default=str(DEFAULT_ATTEMPT_CAP)
    )
    #: The countdown the client is told about. Bounded at read time by the
    #: engine's own connection wall (`nova_sonic_connection_seconds`), because
    #: Bedrock closes the stream at 8 minutes whatever this says and an
    #: interview cut off mid-verdict is the failure the phase machine exists to
    #: prevent.
    time_limit_seconds: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_TIME_LIMIT_SECONDS,
        server_default=str(DEFAULT_TIME_LIMIT_SECONDS),
    )

    #: Who last changed it. A plain column and NOT a foreign key, the same
    #: decision as `InterviewTrack.created_by_user_id` and
    #: `InterviewBankQuestion.created_by_user_id`: this table is KEPT by both
    #: destructors, and a KEPT table holding a live `users` FK is one more row
    #: `purge_people` has to null out before it can delete an account. The
    #: audited record of the change is a `redesign_audit_events` row, which is
    #: where the office's acts live and which survives the actor by design.
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
