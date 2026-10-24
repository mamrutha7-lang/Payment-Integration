"""B8.3 — the alert engine, and the property that makes running it twice safe.

BEFORE THIS, `alerts` HAD ONE WRITER IN THE WHOLE PRODUCT: a single demo row in
`app/seed.py`. `GET /api/mentor/alerts` returned whatever the seed had put
there, and `[]` forever on production, where `app.seed` refuses to run. The five
`AlertRuleKey` values and the per-cohort thresholds beside them were evaluated
nowhere. 04-backend-changes.md's "no decorative rules" is the sentence this
module holds down.

What each test stops coming back:

1. `test_a_rule_raises_an_alert` — the engine runs at all. Without it the whole
   feature is a table and a config screen with nothing between them, which is
   exactly the state it was in.
2. `test_a_second_run_on_the_same_day_writes_nothing` — THE IDEMPOTENCY
   PROPERTY. An operator retrying a failed sweep, or running it by hand after
   fixing a threshold, doubles every mentor's queue. A queue that grows by a
   copy of itself each time somebody touches it is one nobody reads.
3. `test_an_open_alert_is_not_raised_again_tomorrow` — the other half: a
   condition that stays true (attendance does not recover in a day) would
   otherwise file a new row every night until the student's name is the only
   thing in the feed.
4. `test_a_resolved_alert_can_be_raised_again_later` — and the half that must
   NOT be lost to the other two. Once a mentor has closed it, a recurrence is
   news again.
5. `test_no_attendance_records_raise_no_attendance_alert` — the absence
   guardrail, in the place it does the most damage. `attendance_records` has one
   writer (B8.1's import), so a rule that read "no rows" as 0% would, on the
   night this ships, raise a CRITICAL alert against every student on every
   deployment where nobody has imported anything yet.
6. `test_a_disabled_rule_and_a_graduated_batch_are_not_evaluated` — the two
   ways a rule should stop firing without being deleted.
7. `test_every_rule_key_has_an_evaluator` — a key in the enum with no evaluator
   is settable in the console, enabled by an admin, and evaluated by nothing: a
   rule that silently does not run, which is the failure this whole module
   exists to end.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app import alerts as alerts_module
from app.db import SessionLocal
from app.models.alert import Alert, AlertRuleConfig, AlertRuleKey, AlertSeverity
from app.models.attendance import AttendanceRecord
from app.models.cohort import Cohort
from app.models.job import DegreeLevel
from app.models.user import LoginDay, Role, Student, User
from app.seed_roster import SSO_ONLY_PASSWORD_HASH

NOW = datetime(2026, 9, 13, 20, 30, tzinfo=timezone.utc)


@pytest.fixture
def batch():
    """One batch with two students: `poor` has bad attendance recorded, `blank`
    has none at all. The smallest shape in which "below the bar" and "nobody has
    measured them" can be told apart."""
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        cohort = Cohort(
            code=f"ALERT-{tag}",
            name="Alert batch",
            batch_label="2026-28",
            degree_level=DegreeLevel.PG,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        db.add(cohort)
        db.flush()
        made["cohort"] = cohort.id

        for label in ("poor", "blank"):
            user = User(
                email=f"{label}-{tag}@alerts.test",
                name=f"{label.title()} {tag}",
                role=Role.STUDENT,
                password_hash=SSO_ONLY_PASSWORD_HASH,
            )
            db.add(user)
            db.flush()
            student = Student(user_id=user.id, usn=f"{label[:2].upper()}{tag}", cohort_id=cohort.id)
            db.add(student)
            db.flush()
            made[label] = student.id
            made[f"{label}_user"] = user.id
            # Both signed in today, so NO_CHECKIN never fires by accident in a
            # test about something else.
            db.add(LoginDay(user_id=user.id, day=NOW.date()))

        # 1 of 10 sessions attended: unmistakably below any threshold.
        db.add_all(
            [
                AttendanceRecord(
                    student_id=made["poor"],
                    course_code="22MBA11",
                    session_no=n,
                    session_date=NOW - timedelta(days=n),
                    present=(n == 1),
                )
                for n in range(1, 11)
            ]
        )
        db.commit()
    yield made
    with SessionLocal() as db:
        sids = [made["poor"], made["blank"]]
        uids = [made["poor_user"], made["blank_user"]]
        db.execute(delete(Alert).where(Alert.student_id.in_(sids)))
        db.execute(delete(AttendanceRecord).where(AttendanceRecord.student_id.in_(sids)))
        db.execute(delete(AlertRuleConfig).where(AlertRuleConfig.cohort_id == made["cohort"]))
        db.execute(delete(LoginDay).where(LoginDay.user_id.in_(uids)))
        db.execute(delete(Student).where(Student.id.in_(sids)))
        db.execute(delete(User).where(User.id.in_(uids)))
        db.execute(delete(Cohort).where(Cohort.id == made["cohort"]))
        db.commit()


def _rule(cohort_id: str, key: AlertRuleKey, params: dict, enabled: bool = True) -> None:
    with SessionLocal() as db:
        db.add(
            AlertRuleConfig(
                cohort_id=cohort_id,
                rule_key=key,
                params=params,
                enabled=enabled,
                severity=AlertSeverity.CRITICAL,
            )
        )
        db.commit()


def _alerts_for(student_id: str, rule: AlertRuleKey | None = None) -> list[Alert]:
    with SessionLocal() as db:
        stmt = select(Alert).where(Alert.student_id == student_id)
        if rule is not None:
            stmt = stmt.where(Alert.rule_triggered == rule)
        return list(db.scalars(stmt.order_by(Alert.triggered_at)).all())


def _sweep(cohort_id: str, now: datetime = NOW) -> dict[str, int]:
    """Narrowed to ONE batch, so the summary counts this test's rules and not
    also whatever `app/seed.py` configured on the dev database. The nightly job
    passes no `cohort_ids` and evaluates everything."""
    with SessionLocal() as db:
        return alerts_module.evaluate_alerts(db, now=now, cohort_ids=[cohort_id])


@requires_db
def test_a_rule_raises_an_alert(batch):
    _rule(batch["cohort"], AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD, {"minAttendancePct": 75})

    summary = _sweep(batch["cohort"])

    assert summary["rules_evaluated"] == 1
    assert summary["alerts_written"] == 1
    raised = _alerts_for(batch["poor"], AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD)
    assert len(raised) == 1
    assert raised[0].severity is AlertSeverity.CRITICAL, "the config's severity is ignored"
    assert "10.0%" in raised[0].message
    # The numbers that fired it are ON THE ROW, so "why did this appear" is
    # answerable after the attendance behind it has been re-imported.
    assert raised[0].context["attendance_pct"] == 10.0
    assert raised[0].context["threshold_pct"] == 75.0
    assert raised[0].context["cohort_id"] == batch["cohort"]


@requires_db
def test_a_second_run_on_the_same_day_writes_nothing(batch):
    """THE property. Delete this and a retried sweep doubles the queue."""
    _rule(batch["cohort"], AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD, {"minAttendancePct": 75})

    first = _sweep(batch["cohort"])
    second = _sweep(batch["cohort"])

    assert first["alerts_written"] == 1
    # The finding is produced BOTH times — the condition has not changed — and
    # suppressed the second time. Reported separately so a log can tell a quiet
    # night from a broken rule.
    assert second["findings"] == 1
    assert second["alerts_written"] == 0
    assert second["already_raised"] == 1
    assert len(_alerts_for(batch["poor"])) == 1


@requires_db
def test_an_open_alert_is_not_raised_again_tomorrow(batch):
    """Attendance does not recover overnight. Without this the same student is
    in the feed 30 times by the end of the month."""
    _rule(batch["cohort"], AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD, {"minAttendancePct": 75})

    _sweep(batch["cohort"])
    tomorrow = _sweep(batch["cohort"], NOW + timedelta(days=1))

    assert tomorrow["alerts_written"] == 0
    assert tomorrow["already_raised"] == 1
    assert len(_alerts_for(batch["poor"])) == 1


@requires_db
def test_a_resolved_alert_can_be_raised_again_later(batch):
    """The half the other two must not swallow: once a mentor has closed it, a
    recurrence is news."""
    _rule(batch["cohort"], AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD, {"minAttendancePct": 75})
    _sweep(batch["cohort"])

    with SessionLocal() as db:
        row = db.scalars(select(Alert).where(Alert.student_id == batch["poor"])).one()
        row.resolved_at = NOW
        row.resolved_by = batch["poor_user"]
        db.commit()

    # Same day: still suppressed, because the re-run property is about the DAY.
    assert _sweep(batch["cohort"])["alerts_written"] == 0
    # A later day: raised again.
    assert _sweep(batch["cohort"], NOW + timedelta(days=2))["alerts_written"] == 1
    assert len(_alerts_for(batch["poor"])) == 2


@requires_db
def test_no_attendance_records_raise_no_attendance_alert(batch):
    """The absence guardrail, where it does the most damage.

    `attendance_records` has ONE writer — B8.1's import — so on the night this
    engine first runs, a rule that read "no rows" as 0% would raise a CRITICAL
    alert against every student on every deployment where nobody has imported
    anything yet. `blank` is that student.
    """
    _rule(batch["cohort"], AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD, {"minAttendancePct": 75})

    _sweep(batch["cohort"])

    assert _alerts_for(batch["blank"]) == [], (
        "a student with no attendance on record was alerted for being below the "
        "attendance bar — nobody has measured them"
    )
    assert len(_alerts_for(batch["poor"])) == 1


@requires_db
def test_a_never_signed_in_student_is_alerted_and_a_recent_one_is_not(batch):
    """NO_CHECKIN is the one rule where absence IS the finding, and the clock for
    a student who has never signed in runs from the ACCOUNT's creation rather
    than from the epoch."""
    with SessionLocal() as db:
        db.execute(delete(LoginDay).where(LoginDay.user_id == batch["blank_user"]))
        db.get(User, batch["blank_user"]).created_at = NOW - timedelta(days=40)
        db.commit()
    _rule(batch["cohort"], AlertRuleKey.NO_CHECKIN_N_DAYS, {"days": 5})

    _sweep(batch["cohort"])

    silent = _alerts_for(batch["blank"], AlertRuleKey.NO_CHECKIN_N_DAYS)
    assert len(silent) == 1
    assert silent[0].context["ever_signed_in"] is False
    assert silent[0].context["days_silent"] == 40
    assert "never signed in" in silent[0].message
    # `poor` signed in today.
    assert _alerts_for(batch["poor"], AlertRuleKey.NO_CHECKIN_N_DAYS) == []


@requires_db
def test_a_disabled_rule_and_a_graduated_batch_are_not_evaluated(batch):
    """Two ways a rule stops firing without anybody deleting it."""
    _rule(
        batch["cohort"],
        AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD,
        {"minAttendancePct": 75},
        enabled=False,
    )
    off = _sweep(batch["cohort"])
    assert off["rules_evaluated"] == 0
    assert _alerts_for(batch["poor"]) == []

    with SessionLocal() as db:
        db.scalars(
            select(AlertRuleConfig).where(AlertRuleConfig.cohort_id == batch["cohort"])
        ).one().enabled = True
        db.get(Cohort, batch["cohort"]).status = "GRADUATED"
        db.commit()

    graduated = _sweep(batch["cohort"])
    assert graduated["rules_evaluated"] == 0
    assert graduated["rules_skipped"] == 1
    assert _alerts_for(batch["poor"]) == [], (
        "an alumnus was alerted about their attendance"
    )


@requires_db
def test_a_nonsense_threshold_falls_back_instead_of_killing_the_sweep(batch):
    """`params` is operator-entered JSONB and `PUT /alert-rules` takes it
    verbatim — it must, the shape differs per rule. A TypeError raised here
    would take every other batch's rules down with it."""
    _rule(batch["cohort"], AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD, {"minAttendancePct": "most"})

    summary = _sweep(batch["cohort"])

    assert summary["rules_evaluated"] == 1
    assert summary["alerts_written"] == 1
    assert _alerts_for(batch["poor"])[0].context["threshold_pct"] == 75.0


def test_every_rule_key_has_an_evaluator():
    """No database needed: a key with no evaluator is a rule that is settable,
    enablable and silently inert."""
    assert set(alerts_module.EVALUATORS) == set(AlertRuleKey), (
        "a rule key has no evaluator — the console can enable it and nothing "
        "will ever look at it"
    )


@requires_db
def test_the_mentor_feed_reads_what_the_engine_wrote(client, batch, make_user, login):
    """End to end: the engine's row reaches the endpoint the Analytics screen
    already calls on every load. `GET /api/mentor/alerts` is not changed by
    B8.3 — it starts meaning something."""
    from conftest import TEST_PASSWORD
    from app.models.user import Mentor

    _rule(batch["cohort"], AlertRuleKey.ATTENDANCE_BELOW_THRESHOLD, {"minAttendancePct": 75})
    _sweep(batch["cohort"])

    faculty = make_user(f"alerts-{batch['tag']}", Role.MENTOR)
    try:
        with SessionLocal() as db:
            group = Mentor(user_id=faculty.user_id)
            db.add(group)
            db.flush()
            db.get(Student, batch["poor"]).mentor_id = group.id
            db.commit()
        # Signed in AFTER the group exists: `mentorId` is minted at login and
        # rule 2 reads the claim, not the row.
        headers = login(faculty.email, TEST_PASSWORD)
        r = client.get("/api/mentor/alerts", headers=headers)
        assert r.status_code == 200, r.text
        mine = [row for row in r.json() if row["student_id"] == batch["poor"]]
        assert len(mine) == 1
        assert mine[0]["rule_triggered"] == "ATTENDANCE_BELOW_THRESHOLD"
        assert mine[0]["resolved"] is False
        # And rule 2 still fences it: the other student is not in this group.
        assert all(row["student_id"] != batch["blank"] for row in r.json())
    finally:
        with SessionLocal() as db:
            student = db.get(Student, batch["poor"])
            if student is not None:
                student.mentor_id = None
            db.flush()
            db.execute(delete(Mentor).where(Mentor.user_id == faculty.user_id))
            db.commit()
