"""B8.3 — the alert engine: `alert_rule_configs` evaluated into `alerts`.

WHAT THIS REPLACES. Until now `alerts` had exactly ONE writer in the whole
product — a single demo row in `app/seed.py` — so `GET /api/mentor/alerts`
answered whatever the seed had put there and `[]` on every production
deployment, where `app.seed` refuses to run. The five `AlertRuleKey` values and
the per-cohort thresholds beside them were evaluated NOWHERE. The Analytics
screen has been drawing "Nothing open" over an engine that did not exist since
the day it shipped, and 04-backend-changes.md's "no decorative rules" is the
sentence this module answers.

THE SPLIT IS `retention.py` / `retention_job.py`'s, deliberately. This module
decides WHAT an alert is and WHEN one is raised, and is a pure function of `db`
and `now`; `app/alerts_job.py` is the entry point a scheduler calls and decides
nothing. Both halves matter: the policy is testable against a frozen clock
without a process, and the trigger stays a deployment decision a human chose and
can see.

IDEMPOTENT PER RULE / STUDENT / DAY, AND THAT IS THE PROPERTY THAT MAKES A
RE-RUN SAFE. An operator who runs this twice — a retry after a failed sweep, a
manual run after fixing a threshold — must not double the mentor's queue. Two
predicates give it (`_already_raised`):

  * an alert for this (student, rule) raised ALREADY TODAY is not raised again,
    which is the literal re-run property; and
  * an alert for this (student, rule) still OPEN is not raised again, which
    stops a rule that stays true from filing a new row every night for a month
    until the student's name is the only thing in the feed.

IT IS ENFORCED IN THIS MODULE AND NOT BY A UNIQUE INDEX, and that is worth
stating because the obvious question is why. A `unique(student_id,
rule_triggered, date(triggered_at))` index would need an expression index over a
timestamptz — whose value depends on the session TimeZone, so the same row is in
two different "days" for two different connections — and it could not express
the open-alert half at all. The job is a single nightly process; two of them
racing is not a state this design has. If that ever changes, the fix is an
advisory lock around `evaluate_alerts`, not a constraint that can only enforce
half the rule.

EVERY RULE SKIPS A STUDENT IT HAS NO MEASUREMENT FOR, and this is the same
guardrail 07 §5 states for the screens: absence is not a failing score. A
student with no imported attendance is not "below 75%", a student who has never
opened the ledger is not "behind pace", and a student with no certifications
enrolled has nothing overdue. Raising those would fill a mentor's queue with the
consequences of an import nobody has run yet, and the first thing that happens
to a queue like that is that it stops being read. Each rule below says in one
line which absence it refuses to interpret.

WHAT IT NEVER DOES: resolve. `alerts.resolved_by` is a user id and closing an
alert is a mentor's act on a screen (`POST /api/mentor/alerts/{id}/resolve`).
An alert whose condition has since cleared therefore stays open until somebody
looks at it, which is the honest behaviour for a queue about people: "their
attendance recovered" is a thing a mentor should read and close, not something
that should disappear overnight leaving no trace that it was ever raised.

RULE 1 is untouched — no model is called from here and this module imports
nothing from `app/ai/`. RULE 2 is not in play either: this writes rows, and the
fence on who READS them is `GET /api/mentor/alerts`, which is already narrowed
to the caller's own group.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models.alert import Alert, AlertRuleConfig, AlertRuleKey, AlertSeverity
from .models.attendance import AttendanceRecord
from .models.certification import Certification, CertificationProgress, ProgressStatus
from .models.cohort import Cohort
from .models.time_ledger import PRODUCTIVE, LedgerDayStatus, TimeLedgerCell, TimeLedgerDay
from .models.timesheet import DayActivity, TimeSheetEntry
from .models.user import STUDENT_STATUS_ACTIVE, LoginDay, Student, User

log = logging.getLogger("reep.alerts")

#: Batches in these states are not evaluated. A GRADUATED batch's students are
#: alumni (B4.4 flips `students.status` and `users.role` together), and an
#: ARCHIVED one is a batch the office has put away; raising "no check-in for 5
#: days" against either is an alert about somebody who has left.
_CLOSED_BATCH_STATUSES = frozenset({"GRADUATED", "ARCHIVED"})


@dataclass(frozen=True)
class Finding:
    """One alert a rule wants raised: who, what to say, and the numbers behind it.

    `context` is snapshotted onto the row for the reason `models/alert.py` gives
    — the values that fired the rule, so "why did this appear" is answerable
    three months later from the row alone, after the attendance that fired it has
    been re-imported.
    """

    student_id: str
    message: str
    context: dict


@dataclass(frozen=True)
class _Cohort:
    """One batch's students, resolved once and handed to every rule for it.

    Each rule needs the same two maps and none of them should re-query: the
    cohort is the unit of configuration, so a deployment with five rules on one
    batch would otherwise run the roster query five times.
    """

    cohort_id: str
    #: student_id -> the account behind it. Only CURRENT students.
    user_of: dict[str, str]

    @property
    def student_ids(self) -> list[str]:
        return list(self.user_of)


# --------------------------------------------------------------------------- #
# The five rules. Each takes the batch, its params and `now`, and returns the
# findings it wants raised. None of them writes anything.
# --------------------------------------------------------------------------- #


def _int_param(params: dict, key: str, default: int) -> int:
    """One reader for every threshold, because `params` is operator-entered JSONB.

    `PUT /api/admin/alert-rules` validates the rule key and the severity and
    takes `params` verbatim — it has to, the shape differs per rule — so a typo
    ("days": "five") reaches this module. A rule that raised TypeError there
    would take the whole sweep down for every other batch, which is a far worse
    outcome than one rule falling back to the documented default and saying so.
    """
    value = params.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        log.warning(
            "Alert rule param %r is %r, which is not a number; using the default %r.",
            key,
            value,
            default,
        )
        return default


def _float_param(params: dict, key: str, default: float) -> float:
    value = params.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        log.warning(
            "Alert rule param %r is %r, which is not a number; using the default %r.",
            key,
            value,
            default,
        )
        return default


def _no_checkin(db: Session, batch: _Cohort, params: dict, now: datetime) -> list[Finding]:
    """NO_CHECKIN_N_DAYS — nobody has seen this student sign in for N days.

    THE ABSENCE IT REFUSES TO INTERPRET: none. A student with no `login_days`
    row at all has genuinely never signed in, and that is the most alert-worthy
    state this rule has — an account provisioned and never opened. The clock for
    them runs from the ACCOUNT's creation, not from the epoch, so a student
    onboarded yesterday is not immediately five days silent.
    """
    days = _int_param(params, "days", 5)
    today = now.date()
    user_ids = list(batch.user_of.values())
    if not user_ids:
        return []

    last_login: dict[str, date] = {
        uid: day
        for uid, day in db.execute(
            select(LoginDay.user_id, func.max(LoginDay.day))
            .where(LoginDay.user_id.in_(user_ids))
            .group_by(LoginDay.user_id)
        ).all()
    }
    created: dict[str, datetime] = {
        uid: at
        for uid, at in db.execute(
            select(User.id, User.created_at).where(User.id.in_(user_ids))
        ).all()
    }

    findings: list[Finding] = []
    for student_id, user_id in batch.user_of.items():
        seen = last_login.get(user_id)
        ever = seen is not None
        if seen is None:
            made = created.get(user_id)
            if made is None:
                continue
            seen = made.date()
        silent = (today - seen).days
        if silent < days:
            continue
        findings.append(
            Finding(
                student_id=student_id,
                message=(
                    f"No sign-in for {silent} days (the rule fires at {days})."
                    if ever
                    else f"Has never signed in — the account was created {silent} days ago."
                ),
                context={
                    "rule": AlertRuleKey.NO_CHECKIN_N_DAYS.value,
                    "days_silent": silent,
                    "threshold_days": days,
                    "last_seen": seen.isoformat(),
                    "ever_signed_in": ever,
                },
            )
        )
    return findings


def _attendance_below(db: Session, batch: _Cohort, params: dict, now: datetime) -> list[Finding]:
    """ATTENDANCE_BELOW_THRESHOLD — recorded attendance under the cut-off.

    THE ABSENCE IT REFUSES TO INTERPRET: a student with NO attendance records is
    skipped entirely. Their percentage is not 0, it is unknown — the same
    distinction `routers/student.py::_attendance_pct` now makes for the
    readiness screen. Alerting on it would raise "attendance 0% — critical" for
    every student on a deployment where B8.1's import has never been run, which
    is every deployment on the day this ships.
    """
    floor = _float_param(params, "minAttendancePct", 75.0)
    min_sessions = _int_param(params, "minSessions", 1)
    if not batch.student_ids:
        return []

    findings: list[Finding] = []
    for student_id, present, total in db.execute(
        select(
            AttendanceRecord.student_id,
            func.count().filter(AttendanceRecord.present.is_(True)),
            func.count(),
        )
        .where(AttendanceRecord.student_id.in_(batch.student_ids))
        .group_by(AttendanceRecord.student_id)
    ).all():
        total = int(total or 0)
        if total < max(min_sessions, 1):
            continue
        pct = round(100 * int(present or 0) / total, 1)
        if pct >= floor:
            continue
        findings.append(
            Finding(
                student_id=student_id,
                message=f"Attendance is {pct}% across {total} recorded sessions; the cut-off is {floor}%.",
                context={
                    "rule": AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD.value,
                    "attendance_pct": pct,
                    "threshold_pct": floor,
                    "sessions_recorded": total,
                    "sessions_present": int(present or 0),
                },
            )
        )
    return findings


def _cert_overdue(db: Session, batch: _Cohort, params: dict, now: datetime) -> list[Finding]:
    """CERT_OVERDUE — a certification past its due date and not completed.

    THE ABSENCE IT REFUSES TO INTERPRET: a student with no `certification_progress`
    rows has nothing enrolled and therefore nothing overdue — they match no row
    in the query below, so this falls out for free rather than needing a guard.
    """
    grace = _int_param(params, "graceDays", 3)
    if not batch.student_ids:
        return []
    cutoff = now - timedelta(days=grace)

    overdue: dict[str, list[tuple[str, datetime]]] = defaultdict(list)
    for student_id, name, code, due in db.execute(
        select(
            CertificationProgress.student_id,
            Certification.name,
            CertificationProgress.cert_code,
            CertificationProgress.due_date,
        )
        .join(Certification, Certification.code == CertificationProgress.cert_code)
        .where(
            CertificationProgress.student_id.in_(batch.student_ids),
            CertificationProgress.status != ProgressStatus.COMPLETED,
            CertificationProgress.due_date < cutoff,
        )
    ).all():
        overdue[student_id].append((name or code, due))

    findings: list[Finding] = []
    for student_id, items in overdue.items():
        # The WORST one names the alert; the count carries the rest. A row per
        # overdue certification would put six alerts about one student in a feed
        # whose whole job is to be scanned.
        items.sort(key=lambda pair: pair[1])
        worst_name, worst_due = items[0]
        late = (now - worst_due).days
        findings.append(
            Finding(
                student_id=student_id,
                message=(
                    f"{len(items)} certification(s) overdue — "
                    f"{worst_name} is {late} days past its due date."
                ),
                context={
                    "rule": AlertRuleKey.CERT_OVERDUE.value,
                    "overdue_count": len(items),
                    "grace_days": grace,
                    "worst": worst_name,
                    "worst_due": worst_due.isoformat(),
                    "worst_days_late": late,
                },
            )
        )
    return findings


def _pace_below(db: Session, batch: _Cohort, params: dict, now: datetime) -> list[Finding]:
    """PACE_BELOW_THRESHOLD — skilling hours this week well under the target.

    The measure is the one the dashboard already draws: SKILLING minutes in
    `time_sheet_entries` over the trailing seven days, against
    `students.weekly_hour_target`. (Not the Time Allocation Ledger — that table
    answers "what did Thursday look like"; this one answers "how many minutes of
    skilling this week", which is what a target is set against. `models/
    time_ledger.py` states the split.)

    THE ABSENCE IT REFUSES TO INTERPRET: a student who has NEVER logged a
    timesheet entry is skipped. Zero hours against an eight-hour target is a
    real and alertable fact for somebody who used to log and stopped; for
    somebody who has never opened the screen it is a fact about the screen, not
    about their pace, and the office's answer to it is onboarding rather than a
    mentor alert. A student who has logged before and logged nothing this week
    DOES fire — that is the case the rule exists for.
    """
    deviation = _float_param(params, "deviationPct", 25.0)
    if not batch.student_ids:
        return []
    window_start = now.date() - timedelta(days=7)

    targets: dict[str, float] = {
        sid: float(target or 0)
        for sid, target in db.execute(
            select(Student.id, Student.weekly_hour_target).where(
                Student.id.in_(batch.student_ids)
            )
        ).all()
    }
    ever_logged = set(
        db.scalars(
            select(TimeSheetEntry.student_id)
            .where(
                TimeSheetEntry.student_id.in_(batch.student_ids),
                TimeSheetEntry.activity == DayActivity.SKILLING,
            )
            .distinct()
        ).all()
    )
    logged: dict[str, float] = {
        sid: float(minutes or 0) / 60
        for sid, minutes in db.execute(
            select(TimeSheetEntry.student_id, func.sum(TimeSheetEntry.minutes))
            .where(
                TimeSheetEntry.student_id.in_(batch.student_ids),
                TimeSheetEntry.activity == DayActivity.SKILLING,
                TimeSheetEntry.day >= window_start,
            )
            .group_by(TimeSheetEntry.student_id)
        ).all()
    }

    findings: list[Finding] = []
    for student_id in batch.student_ids:
        target = targets.get(student_id, 0.0)
        if target <= 0 or student_id not in ever_logged:
            continue
        hours = round(logged.get(student_id, 0.0), 1)
        floor = target * (1 - deviation / 100)
        if hours >= floor:
            continue
        short_by = round(target - hours, 1)
        findings.append(
            Finding(
                student_id=student_id,
                message=(
                    f"{hours} h of skilling logged in the last 7 days against a "
                    f"{round(target, 1)} h target — {short_by} h short."
                ),
                context={
                    "rule": AlertRuleKey.PACE_BELOW_THRESHOLD.value,
                    "hours_logged": hours,
                    "weekly_hour_target": round(target, 1),
                    "deviation_pct": deviation,
                    "shortfall_hours": short_by,
                },
            )
        )
    return findings


def _low_focus(db: Session, batch: _Cohort, params: dict, now: datetime) -> list[Finding]:
    """LOW_FOCUS_QUALITY — little of a submitted day went on productive heads.

    The measure is the Time Allocation Ledger's own: `PRODUCTIVE` (lectures,
    coursework, skilling) as a share of every half hour on SUBMITTED days in the
    window. The constant is imported rather than restated — `models/
    time_ledger.py` names it precisely so "the metric and its own sub-caption
    cannot drift apart", and a second definition here would drift on the day a
    sixth activity head is added.

    THE ABSENCE IT REFUSES TO INTERPRET: only SUBMITTED days count, and a
    student with none in the window is skipped. A draft day is a day somebody is
    part way through typing; reading it as a finished account of their time
    would alert on the morning of a day that is not over.
    """
    floor = _float_param(params, "minProductivePct", 40.0)
    days = _int_param(params, "days", 14)
    min_days = _int_param(params, "minDays", 3)
    if not batch.student_ids:
        return []
    window_start = now.date() - timedelta(days=days)

    submitted_days: dict[str, int] = {
        sid: int(n or 0)
        for sid, n in db.execute(
            select(TimeLedgerDay.student_id, func.count())
            .where(
                TimeLedgerDay.student_id.in_(batch.student_ids),
                TimeLedgerDay.day >= window_start,
                TimeLedgerDay.status == LedgerDayStatus.SUBMITTED,
            )
            .group_by(TimeLedgerDay.student_id)
        ).all()
    }
    totals: dict[str, int] = defaultdict(int)
    productive: dict[str, int] = defaultdict(int)
    for sid, activity, halves in db.execute(
        select(
            TimeLedgerDay.student_id,
            TimeLedgerCell.activity,
            func.sum(TimeLedgerCell.half_hours),
        )
        .join(TimeLedgerCell, TimeLedgerCell.ledger_day_id == TimeLedgerDay.id)
        .where(
            TimeLedgerDay.student_id.in_(batch.student_ids),
            TimeLedgerDay.day >= window_start,
            TimeLedgerDay.status == LedgerDayStatus.SUBMITTED,
        )
        .group_by(TimeLedgerDay.student_id, TimeLedgerCell.activity)
    ).all():
        totals[sid] += int(halves or 0)
        if activity in PRODUCTIVE:
            productive[sid] += int(halves or 0)

    findings: list[Finding] = []
    for student_id, n_days in submitted_days.items():
        if n_days < max(min_days, 1):
            continue
        total = totals.get(student_id, 0)
        if total <= 0:
            continue
        pct = round(100 * productive.get(student_id, 0) / total, 1)
        if pct >= floor:
            continue
        findings.append(
            Finding(
                student_id=student_id,
                message=(
                    f"{pct}% of logged time went on lectures, coursework or skilling "
                    f"across {n_days} submitted days; the floor is {floor}%."
                ),
                context={
                    "rule": AlertRuleKey.LOW_FOCUS_QUALITY.value,
                    "productive_pct": pct,
                    "threshold_pct": floor,
                    "submitted_days": n_days,
                    "window_days": days,
                },
            )
        )
    return findings


#: Rule key -> its evaluator. EVERY key in the enum is here and the sweep
#: asserts it: a key added to `AlertRuleKey` with no evaluator would be
#: settable in the console, enabled by an admin, and evaluated by nothing — a
#: rule that silently does not run, which is the exact state this module was
#: written to end.
EVALUATORS: dict[AlertRuleKey, Callable[[Session, _Cohort, dict, datetime], list[Finding]]] = {
    AlertRuleKey.NO_CHECKIN_N_DAYS: _no_checkin,
    AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD: _attendance_below,
    AlertRuleKey.CERT_OVERDUE: _cert_overdue,
    AlertRuleKey.PACE_BELOW_THRESHOLD: _pace_below,
    AlertRuleKey.LOW_FOCUS_QUALITY: _low_focus,
}


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #


def _already_raised(
    db: Session, student_ids: Iterable[str], rule: AlertRuleKey, day_start: datetime
) -> set[str]:
    """The students this rule must NOT be raised for again — see the module
    docstring for why there are two reasons and not one."""
    ids = list(student_ids)
    if not ids:
        return set()

    return set(
        db.scalars(
            select(Alert.student_id).where(
                Alert.student_id.in_(ids),
                Alert.rule_triggered == rule,
                or_(Alert.resolved_at.is_(None), Alert.triggered_at >= day_start),
            )
        ).all()
    )


def _batches(db: Session, cohort_ids: Iterable[str]) -> dict[str, _Cohort]:
    """The CURRENT students of each batch, in one query for all of them.

    `students.status` and not `users.role`: B4.4 sets both when a batch
    graduates and the column is the one its own comment says decides "who is
    COUNTED". A disabled account is included deliberately — somebody who cannot
    sign in is exactly who NO_CHECKIN_N_DAYS should surface, and the mentor
    reading it is the person who can ask why.
    """
    ids = list(cohort_ids)
    if not ids:
        return {}
    out: dict[str, dict[str, str]] = {cid: {} for cid in ids}
    for cohort_id, student_id, user_id in db.execute(
        select(Student.cohort_id, Student.id, Student.user_id).where(
            Student.cohort_id.in_(ids),
            Student.status == STUDENT_STATUS_ACTIVE,
        )
    ).all():
        out[cohort_id][student_id] = user_id
    return {cid: _Cohort(cohort_id=cid, user_of=members) for cid, members in out.items()}


def evaluate_alerts(
    db: Session,
    *,
    now: datetime | None = None,
    cohort_ids: list[str] | None = None,
) -> dict[str, int]:
    """Evaluate every enabled rule config and write the alerts it raises.

    `cohort_ids` NARROWS the sweep to those batches. The nightly job passes
    nothing and evaluates everything; the parameter exists for the operator who
    has just fixed one batch's threshold and wants that batch re-evaluated
    without waiting for tonight, and for the tests, which must be able to assert
    on a summary that is not also counting whatever the dev seed configured. It
    can only narrow — there is no value of it that reaches a rule
    `evaluate_alerts()` would not.

    A PURE FUNCTION OF `now`, like `retention.purge_expired` and for the same
    reason: the tests pin behaviour against a frozen clock, and a job whose
    output depends on `datetime.now()` buried three calls down cannot be tested
    at a boundary at all.

    Returns a summary: rules evaluated, rules skipped (batch closed or gone),
    findings produced, alerts written and findings suppressed as already raised.
    The last two are reported SEPARATELY because their difference is the whole
    idempotency story — a second run on the same day should report the same
    findings and zero writes, and a summary that collapsed them would make that
    unobservable from the log.

    ONE COMMIT AT THE END. A commit per alert would leave a sweep that died
    half way through having raised alerts for the first four batches and not the
    last five, with nothing saying where it stopped; the whole night's alerts
    either land or none do, and the operator retries.
    """
    now = now or datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    config_query = select(AlertRuleConfig).where(AlertRuleConfig.enabled.is_(True))
    if cohort_ids is not None:
        config_query = config_query.where(AlertRuleConfig.cohort_id.in_(cohort_ids or [""]))
    configs = db.scalars(
        config_query.order_by(AlertRuleConfig.cohort_id, AlertRuleConfig.rule_key)
    ).all()
    summary = {
        "rules_evaluated": 0,
        "rules_skipped": 0,
        "findings": 0,
        "alerts_written": 0,
        "already_raised": 0,
    }
    if not configs:
        return summary

    # Batches whose lifecycle has closed are dropped BEFORE their students are
    # loaded, so a graduated batch costs one row in a status map rather than a
    # roster query and five rule evaluations.
    statuses = {
        cid: st
        for cid, st in db.execute(
            select(Cohort.id, Cohort.status).where(
                Cohort.id.in_({c.cohort_id for c in configs})
            )
        ).all()
    }
    live = {
        cid
        for cid, st in statuses.items()
        if (st or "").upper() not in _CLOSED_BATCH_STATUSES
    }
    batches = _batches(db, live)

    for config in configs:
        batch = batches.get(config.cohort_id)
        if batch is None:
            # Either the batch has graduated/archived, or its row is gone and
            # the config is an orphan. Counted, not raised: a rule that stops
            # producing alerts because its batch left is not a failure.
            summary["rules_skipped"] += 1
            continue
        evaluator = EVALUATORS.get(config.rule_key)
        if evaluator is None:
            log.error(
                "Alert rule %s has no evaluator; it is enabled on batch %s and "
                "evaluates to nothing. Add it to alerts.EVALUATORS.",
                config.rule_key.value,
                config.cohort_id,
            )
            summary["rules_skipped"] += 1
            continue

        summary["rules_evaluated"] += 1
        findings = evaluator(db, batch, config.params or {}, now)
        summary["findings"] += len(findings)
        if not findings:
            continue

        skip = _already_raised(
            db, (f.student_id for f in findings), config.rule_key, day_start
        )
        for finding in findings:
            if finding.student_id in skip:
                summary["already_raised"] += 1
                continue
            db.add(
                Alert(
                    student_id=finding.student_id,
                    rule_triggered=config.rule_key,
                    severity=config.severity or AlertSeverity.WARNING,
                    message=finding.message,
                    context={**finding.context, "cohort_id": config.cohort_id},
                    triggered_at=now,
                )
            )
            # Within ONE sweep the same (student, rule) can only be produced
            # once — a rule returns at most one finding per student — so nothing
            # else needs to join `skip`. Added anyway: a future rule that emits
            # two findings for one student would otherwise write two rows and
            # discover it in a mentor's feed.
            skip.add(finding.student_id)
            summary["alerts_written"] += 1

    db.commit()
    return summary
