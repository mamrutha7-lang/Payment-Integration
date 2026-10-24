"""The college's interview policy, resolved (B6.1) — and the two cap ceilings
it sets (B6.4).

ONE ANSWER, IN ONE PLACE. Four callers need to know what a college has decided
about a student's mock interview, and they must never disagree:

  * `routers/interview.py::_open_records` — the socket, which refuses on the
    caps, stamps the retention window, suppresses the transcript and decides
    whether a recorder may be built;
  * `routers/interview_policy.py` — the student's `GET /api/interview/policy`
    card and the office's `PUT /api/admin/interview-policies/...`;
  * `routers/interview_records.py::grant_consent` — which copies the policy's
    two storage scopes onto the acknowledgement row;
  * the cap reset, which has to count the same rows the socket counts.

Two of those run on a WebSocket handshake, so everything here is SYNCHRONOUS
SQLAlchemy and none of it may be called from the event loop — `_open_records`
already runs on a worker thread and this rides in with it.

THE RESOLUTION IS TWO STEPS AND THE ORDER IS THE WHOLE RULE: the (college,
course) row first, the (college, NULL) row second, `config.py`'s constants when
there is neither — exactly the shape `console.resolve_criteria` uses for
placement gates, and for the same reason. NO ROW IS SEEDED, deliberately: the
absence of a row IS the default, so a deployment that never opens the policy
screen behaves precisely as it did before this table existed, and the console's
"not configured" state is reachable rather than being a row that happens to hold
the default numbers.

RULE 1 IS UNAFFECTED AND RULE 2 IS NOT THIS MODULE'S JOB. Nothing here reaches a
model; a policy is staff-authored numbers. And nothing here decides who may READ
a student's record — `_assert_can_access_student` and `require_capability` do
that at the routers, and a resolver that started refusing would be a second
fence disagreeing with the first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from .config import settings
from .governance import ancestry_of_student
from .models.governance import ScopeLevel
from .models.interview import InterviewCapReset, InterviewSession
from .models.interview_policy import (
    DEFAULT_ATTEMPT_CAP,
    DEFAULT_DAILY_CAP,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_TIME_LIMIT_SECONDS,
    InterviewPolicy,
)

log = logging.getLogger(__name__)

#: The rolling window both ceilings are counted over. A window and not a
#: calendar day, so midnight is not a reset button — the value the code has
#: always used, named rather than repeated.
CAP_WINDOW = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    """What governs ONE interview, with the row it came from named.

    `source` is not decoration. "the college has not configured this" and "the
    college configured exactly these numbers" are different facts to whoever is
    reading the policy screen, and a payload that reported the defaults as a row
    would make the second unfalsifiable — which is the same mistake as a
    capability that nothing checks.
    """

    store_transcript: bool
    store_audio: bool
    retention_days: int
    daily_cap: int
    attempt_cap: int
    time_limit_seconds: int
    #: 'default' (no row anywhere), 'college' (the college's default row) or
    #: 'course' (a row for this student's own course).
    source: str
    #: The row this came from, or None for 'default'. The heartbeat watches it;
    #: the console edits it.
    policy_id: str | None = None
    college_id: str | None = None
    course_id: str | None = None

    @property
    def configured(self) -> bool:
        return self.source != "default"


def default_policy() -> EffectivePolicy:
    """`config.py`'s numbers, which are what a deployment runs on today.

    Read from `settings` and not from the model's module constants for
    `retention_days`, `daily_cap` and `attempt_cap`: those three have a
    deployment-level setting, and a college that has not decided must keep
    getting the operator's answer rather than a second copy of it that drifts on
    the next edit to `.env`. `time_limit_seconds` has no setting of its own —
    the real wall is the engine's connection cap, applied at read time because
    it moves with the provider — and takes the model's constant.
    """
    return EffectivePolicy(
        store_transcript=True,
        store_audio=False,
        retention_days=int(
            getattr(settings, "interview_retention_days", DEFAULT_RETENTION_DAYS)
        ),
        daily_cap=int(
            getattr(
                settings, "interview_max_per_student_per_day", DEFAULT_DAILY_CAP
            )
        ),
        attempt_cap=int(
            getattr(
                settings,
                "interview_max_attempts_per_student_per_day",
                DEFAULT_ATTEMPT_CAP,
            )
        ),
        time_limit_seconds=DEFAULT_TIME_LIMIT_SECONDS,
        source="default",
    )


def _from_row(row: InterviewPolicy, source: str) -> EffectivePolicy:
    return EffectivePolicy(
        store_transcript=bool(row.store_transcript),
        store_audio=bool(row.store_audio),
        retention_days=int(row.retention_days),
        daily_cap=int(row.daily_cap),
        attempt_cap=int(row.attempt_cap),
        time_limit_seconds=int(row.time_limit_seconds),
        source=source,
        policy_id=row.id,
        college_id=row.college_id,
        course_id=row.course_id,
    )


def resolve_policy(
    db: Session, *, college_id: str | None, course_id: str | None
) -> EffectivePolicy:
    """The course's row, else the college's default row, else the defaults.

    A NULL `college_id` cannot resolve anything: `interview_policies.college_id`
    is NOT NULL, because the programme-wide fallback is the ABSENCE of a row and
    not a row full of NULLs. A student whose spine does not reach a college —
    every student on a deployment that has not built its institution yet — gets
    the defaults, which is exactly what they get today.
    """
    if not college_id:
        return default_policy()
    if course_id:
        row = db.scalar(
            select(InterviewPolicy).where(
                InterviewPolicy.college_id == college_id,
                InterviewPolicy.course_id == course_id,
            )
        )
        if row is not None:
            return _from_row(row, "course")
    row = db.scalar(
        select(InterviewPolicy).where(
            InterviewPolicy.college_id == college_id,
            InterviewPolicy.course_id.is_(None),
        )
    )
    if row is not None:
        return _from_row(row, "college")
    return default_policy()


def spine_of_student(db: Session, student_id: str) -> tuple[str | None, str | None]:
    """(college_id, course_id) for one student, through the ONE ancestry reader.

    `interview_tracks.college_of_student` is the COLLEGE-ONLY twin of this and
    delegates to the same function; this one also needs the COURSE, because a
    policy hangs on `(college, course)` and a track does not. Two callers, one
    walk, no third copy of the join.

    `governance.ancestry_of_student` is reused rather than re-joined, and that
    is not tidiness: it reads BOTH department pointers (`cohorts.department_id`
    and `students.department_id`), and the version that read only the cohort
    route silently missed every UNSEATED student — which is every student at a
    college that has not built its batches yet. A second join written here would
    be that bug again, this time deciding which policy governs an interview.
    """
    pairs = dict(ancestry_of_student(db, student_id))
    return pairs.get(ScopeLevel.COLLEGE), pairs.get(ScopeLevel.COURSE)


def policy_for_student(db: Session, student_id: str) -> EffectivePolicy:
    """The policy governing this student's next interview."""
    college_id, course_id = spine_of_student(db, student_id)
    return resolve_policy(db, college_id=college_id, course_id=course_id)


# ---------------------------------------------------------------------------
# The caps (B6.4) — two ceilings over one window
# ---------------------------------------------------------------------------
def cap_window_start(db: Session, student_id: str, now: datetime) -> datetime:
    """The lower bound the two counts are taken from.

    `GREATEST(now - 24 h, the latest reset)` — AN EXTRA BOUND ON THE EXISTING
    WINDOW, never a replacement for it. Written the other way round (the reset
    replaces the window) an old reset row would widen the window back open and
    a student would be counted against attempts from last week; written this
    way a second reset can only ever move the bound FORWARD, which is the one
    direction "give this student their attempts back" is allowed to move.

    A reset therefore expires with the window it sits in: 24 hours after it was
    granted it stops mattering, because `now - 24 h` has overtaken it. That is
    correct — the cap is a rolling allowance, not a balance.
    """
    rolling = now - CAP_WINDOW
    last_reset = db.scalar(
        select(func.max(InterviewCapReset.at)).where(
            InterviewCapReset.student_id == student_id
        )
    )
    if last_reset is None:
        return rolling
    if last_reset.tzinfo is None:
        # `interview_cap_resets.at` is `timestamptz`, so this does not happen on
        # Postgres. It is coerced rather than ignored because the alternative —
        # dropping the bound — would silently give a student back attempts an
        # admin never granted, and a comparison against a naive datetime raises
        # where a wrong answer would not.
        last_reset = last_reset.replace(tzinfo=timezone.utc)
    return max(rolling, last_reset)


def _sessions_since(student_id: str, since: datetime) -> Select:
    return (
        select(func.count())
        .select_from(InterviewSession)
        .where(
            InterviewSession.student_id == student_id,
            InterviewSession.started_at >= since,
        )
    )


def cap_counts(db: Session, student_id: str, since: datetime) -> tuple[int, int]:
    """(completed, attempts) in the window — the two numbers, from one table.

    TWO CEILINGS, AND BOTH ARE LOAD-BEARING. 04-backend-changes.md asks for the
    cap to count `status='completed'` only, and on its own that DELETES the
    control the current code exists to provide: `_open_records`' own comment
    says it plainly — every session billed an upstream Bedrock handshake, and
    "a cap that only counts clean finishes is a cap a crash loop never hits".
    A student reconnecting in a loop never reaches `completed`.

    So `completed` is the student's practice allowance (an interview that
    dropped out at minute two no longer costs them a turn, which is the whole
    point of B6.4) and `attempts` is the spend ceiling, higher by construction —
    `ck_interview_policy_bounds` refuses the other order, which would make the
    allowance silently become the ceiling.

    Two COUNTs rather than one grouped read, because the numbers go to different
    places: the student's card shows the first and the operator's refusal
    sentence names whichever tripped.
    """
    completed = db.scalar(
        _sessions_since(student_id, since).where(
            InterviewSession.status == "completed"
        )
    )
    attempts = db.scalar(_sessions_since(student_id, since))
    return int(completed or 0), int(attempts or 0)


@dataclass(frozen=True, slots=True)
class CapVerdict:
    """Whether this student may open another interview, and which wall said no.

    `which` is 'daily' or 'attempts', and the two produce DIFFERENT SENTENCES on
    the same close code (4015). "You have used your 8 practice interviews" and
    "too many attempts in the last 24 hours" are different things to be told,
    and a student who has been reconnecting through a broken microphone needs to
    hear the second one rather than be told they practised eight times.
    """

    allowed: bool
    completed: int
    attempts: int
    which: str | None = None


def evaluate_caps(
    db: Session, student_id: str, policy: EffectivePolicy, now: datetime
) -> CapVerdict:
    """Both ceilings, counted over the reset-aware window."""
    since = cap_window_start(db, student_id, now)
    completed, attempts = cap_counts(db, student_id, since)
    if completed >= policy.daily_cap:
        return CapVerdict(False, completed, attempts, "daily")
    if attempts >= policy.attempt_cap:
        return CapVerdict(False, completed, attempts, "attempts")
    return CapVerdict(True, completed, attempts)


def cap_message(verdict: CapVerdict, policy: EffectivePolicy) -> str:
    """The sentence the student reads on close 4015."""
    if verdict.which == "attempts":
        return (
            "Too many mock interview attempts in the last 24 hours. Try again "
            "later."
        )
    return (
        f"You've used all {policy.daily_cap} of today's mock interviews. Try "
        "again tomorrow."
    )
