"""B6.1 and B6.4 — the college's interview policy, and the two cap ceilings.

WHAT THIS MODULE IS FOR. Until now the three storage scopes were the STUDENT's,
ticked on a panel, and two of them were recorded and read by nothing at all;
the caps were one number in `app/config.py` for the whole deployment. B6.1 makes
the scopes the college's and B6.4 makes the caps theirs, and the risk in both is
the same: a policy that is WRITTEN but not ENFORCED is exactly the class of bug
`check_capability_enforcement.py` and B2.2's feature switches exist to stop —
a screen that says a rule is in force while nothing asks about it.

So almost every test here reaches past the payload to the thing that acts on it:
the socket's own `_open_records`, the turn writer, `recorder_for`, the
heartbeat. The HTTP tests check the fences (capability, scope, reason, bounds);
the rest check that the numbers actually decide something.

THREE PROPERTIES THAT MUST NOT BE WEAKENED, and each has a test whose name says
so:

  * a policy row must not be needed. A deployment that never opens the policy
    screen has to behave exactly as it did before this table existed, because
    that is the compatibility rule and because the console's "not configured"
    state has to be reachable.
  * counting completions only must not remove the spend ceiling. The comment
    `_open_records` has carried since the cap was written is the argument, and
    B6.4 would have deleted the control it describes.
  * consent still fails CLOSED on open and OPEN mid-interview, and the scopes
    are still THREE booleans on a row that an interview pins by id.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.config import settings
from app.db import SessionLocal
from app.interview_policy import (
    cap_counts,
    cap_window_start,
    default_policy,
    evaluate_caps,
    policy_for_student,
    resolve_policy,
)
from app.models.governance import (
    CAPABILITIES_BY_KEY,
    CapabilityGrant,
    CapabilityScope,
    ScopeLevel,
    SubjectKind,
)
from app.models.institution import AcademicCourse, College, Department
from app.models.interview import (
    InterviewCapReset,
    InterviewConsent,
    InterviewSession,
    InterviewTurn,
)
from app.models.interview_policy import InterviewPolicy
from app.models.conversation import Conversation, Message
from app.models.user import LoginDay, Role, Student, User
from app.routers.admin import COLLEGE_ADMIN_CAPABILITIES
from app.routers.interview import (
    _DailyCapReached,
    _make_heartbeat,
    _make_turn_writer,
    _open_records,
)
from app.routers.interview_policy import CAPABILITY

KEY = "admin.interviews"


# ---------------------------------------------------------------------------
# The capability key — no database needed, and these are the ones that fail
# loudest if the key is added in one place and not the others
# ---------------------------------------------------------------------------
def test_the_interviews_capability_exists_and_carries_pii():
    """B6.7's key, added by B6.1's endpoints.

    `carries_pii` is TRUE and the consequence is deliberate: a DEPUTY's grant
    lands `pending_approval` under B2.4 and holds nothing until a different
    `admin.governance` holder approves it (the Main Admin's own grant is live
    at once). What earns the flag is not the policy
    row — it is the cap reset, which names one student, and the records grid
    that hangs off the same key.
    """
    cap = CAPABILITIES_BY_KEY.get(KEY)
    assert cap is not None, "admin.interviews is missing from the catalogue"
    assert cap.carries_pii is True
    # PROGRAMME: a policy governs every student on a course, and no mentor GROUP
    # is a rung a policy could hang on. B1.2's scope narrows it instead.
    assert cap.scope is CapabilityScope.PROGRAMME
    assert CAPABILITY == KEY


def test_every_college_admin_capability_is_in_the_catalogue():
    """A name in `COLLEGE_ADMIN_CAPABILITIES` with no catalogue entry is a
    KeyError in `CAPABILITIES_BY_KEY[key]`, raised in front of whoever is
    appointing a college admin.

    That is not hypothetical: `admin.interviews` sat in
    04-backend-changes.md's list for this set for months while nothing defined
    it, and `app/routers/admin.py` carried a comment saying so. This test is the
    reason it can never be re-added to the tuple ahead of the catalogue.
    """
    missing = [k for k in COLLEGE_ADMIN_CAPABILITIES if k not in CAPABILITIES_BY_KEY]
    assert missing == []
    assert KEY in COLLEGE_ADMIN_CAPABILITIES


def test_the_key_is_enforced_somewhere_and_not_merely_catalogued():
    """B2.1's rule, spot-checked for this one key.

    `tools/ci/check_capability_enforcement.py` is the general guard; this is the
    same assertion written where the person adding an interview endpoint will
    see it. A row naming a capability nothing checks is a promise the API does
    not keep, and Governance is where the office looks to answer "who can see
    what".
    """
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app"
        / "routers"
        / "interview_policy.py"
    ).read_text()
    # The module binds the key to one name and uses that name, so the check is
    # in two halves: the constant IS this key, and `require_capability` is
    # actually called with it. Matching the literal alone would pass on a module
    # that merely mentions it in a comment.
    assert f'CAPABILITY = "{KEY}"' in source
    assert "require_capability(db, session, CAPABILITY)" in source
    assert "require_capability(\n        db, session, CAPABILITY, target=" in source


def test_the_deployment_defaults_are_what_is_in_force_today():
    """NO POLICY ROW IS SEEDED, and this is what makes that safe.

    The absence of a row IS the default. A seeded row would put one answer in
    two places and make the console's "not configured" state unreachable, so the
    resolver has to return today's behaviour when it finds nothing.
    """
    policy = default_policy()
    assert policy.source == "default"
    assert policy.configured is False
    assert policy.store_transcript is True
    assert policy.store_audio is False
    assert policy.retention_days == settings.interview_retention_days
    assert policy.daily_cap == settings.interview_max_per_student_per_day
    # The spend ceiling has no `config.py` setting of its own and must be higher
    # than the practice allowance, or the allowance can never be reached.
    assert policy.attempt_cap >= policy.daily_cap


# ---------------------------------------------------------------------------
# Fixtures — a college, a course, a student hung under both
# ---------------------------------------------------------------------------
@pytest.fixture
def spine():
    """A throwaway College + Department + Course, torn down afterwards."""
    college_id = f"pol-col-{uuid.uuid4().hex[:8]}"
    department_id = f"pol-dep-{uuid.uuid4().hex[:8]}"
    course_id = f"pol-crs-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(College(id=college_id, name="Policy College", code=college_id[:12]))
        db.add(
            Department(
                id=department_id,
                college_id=college_id,
                name="Policy Department",
                code=department_id[:12],
            )
        )
        db.flush()
        db.add(
            AcademicCourse(
                id=course_id,
                department_id=department_id,
                code=course_id[:12],
                name="Policy Course",
            )
        )
        db.commit()
    yield types_ns(college_id=college_id, department_id=department_id, course_id=course_id)
    with SessionLocal() as db:
        db.execute(
            delete(InterviewPolicy).where(InterviewPolicy.college_id == college_id)
        )
        db.execute(delete(AcademicCourse).where(AcademicCourse.id == course_id))
        db.execute(delete(Department).where(Department.id == department_id))
        db.execute(delete(College).where(College.id == college_id))
        db.commit()


def types_ns(**kw):
    import types

    return types.SimpleNamespace(**kw)


@pytest.fixture
def student(spine):
    """A User + Student seated in `spine`'s department, and nothing else.

    The department pointer rather than a cohort ON PURPOSE: that is the only
    pointer an UNSEATED student has, and reading only the cohort route is the
    bug `governance.ancestry_of_student` was fixed for — an override hung on a
    department reached the seated students and silently missed every unseated
    one.
    """
    with SessionLocal() as db:
        user = User(
            email=f"ivpolicy-{uuid.uuid4().hex[:10]}@bgscet.ac.in",
            name="Interview Policy Fixture",
            role=Role.STUDENT,
            password_hash="x",
        )
        db.add(user)
        db.flush()
        row = Student(user_id=user.id, department_id=spine.department_id)
        db.add(row)
        db.commit()
        subject = types_ns(
            user_id=user.id, student_id=row.id, **vars(spine)
        )
    yield subject
    with SessionLocal() as db:
        db.execute(
            delete(InterviewCapReset).where(
                InterviewCapReset.student_id == subject.student_id
            )
        )
        db.execute(
            delete(InterviewSession).where(
                InterviewSession.student_id == subject.student_id
            )
        )
        db.execute(
            delete(InterviewConsent).where(
                InterviewConsent.user_id == subject.user_id
            )
        )
        # Files before rows, and children before parents: the conversation the
        # socket opened owns `messages`, and a login leaves a `login_days` row
        # whose FK to `users` has no ON DELETE at all — the same constraint
        # `purge_people._delete_rows` orders around.
        convo_ids = list(
            db.scalars(
                select(Conversation.id).where(
                    Conversation.owner_user_id == subject.user_id
                )
            )
        )
        if convo_ids:
            db.execute(delete(Message).where(Message.conversation_id.in_(convo_ids)))
            db.execute(delete(Conversation).where(Conversation.id.in_(convo_ids)))
        db.execute(delete(LoginDay).where(LoginDay.user_id == subject.user_id))
        db.execute(delete(Student).where(Student.id == subject.student_id))
        db.execute(delete(User).where(User.id == subject.user_id))
        db.commit()


def _grant_consent(user_id: str) -> str:
    with SessionLocal() as db:
        row = InterviewConsent(
            user_id=user_id,
            version=settings.interview_consent_version,
            scope_live_ai=True,
            scope_store_transcript=True,
            scope_store_audio=False,
        )
        db.add(row)
        db.commit()
        return row.id


def _write_policy(college_id: str, course_id: str | None = None, **values) -> str:
    with SessionLocal() as db:
        row = InterviewPolicy(college_id=college_id, course_id=course_id, **values)
        db.add(row)
        db.commit()
        return row.id


def _sessions(student_id: str, *, status: str, count: int, started: datetime) -> None:
    with SessionLocal() as db:
        for i in range(count):
            db.add(
                InterviewSession(
                    student_id=student_id,
                    conversation_id=None,
                    status=status,
                    started_at=started,
                    heartbeat_at=started,
                    conn_id=f"cap-{uuid.uuid4().hex[:8]}-{i}",
                )
            )
        db.commit()


def _turn(seq: int):
    """A turn record shaped as the engines emit them."""
    from app.interview_core import _TurnRecord

    return _TurnRecord(
        seq=seq,
        phase="opening",
        transcription_status="ok",
        answer_quality=None,
        counted_as_answer=True,
        is_partial=False,
    )


def _open(subject):
    return _open_records(
        subject.user_id, Role.STUDENT, subject.student_id, "p0licyp0licy", None
    )


# ---------------------------------------------------------------------------
# Resolution — course, then college, then the deployment defaults
# ---------------------------------------------------------------------------
@requires_db
class TestResolution:
    def test_no_row_anywhere_resolves_the_deployment_defaults(self, student):
        policy = policy_for_student(SessionLocal(), student.student_id)
        assert policy.source == "default"
        assert policy.daily_cap == settings.interview_max_per_student_per_day

    def test_the_colleges_row_wins_over_the_defaults(self, student):
        _write_policy(student.college_id, None, daily_cap=3, attempt_cap=5)
        with SessionLocal() as db:
            policy = policy_for_student(db, student.student_id)
        assert policy.source == "college"
        assert (policy.daily_cap, policy.attempt_cap) == (3, 5)

    def test_a_course_row_wins_over_the_colleges(self, student):
        """The two-step the placement criteria already use, in the same order.

        The student here is seated in a DEPARTMENT and not a course, so the
        college row is the one that reaches them — which is the point of the
        second half of this test: a course row that does not name their course
        must not.
        """
        _write_policy(student.college_id, None, daily_cap=3, attempt_cap=5)
        _write_policy(student.course_id and student.college_id, student.course_id,
                      daily_cap=1, attempt_cap=2)
        with SessionLocal() as db:
            # This student has no course, so the college default is theirs.
            assert policy_for_student(db, student.student_id).daily_cap == 3
            # A student on that course would get the narrower row.
            assert (
                resolve_policy(
                    db, college_id=student.college_id, course_id=student.course_id
                ).daily_cap
                == 1
            )

    def test_a_student_with_no_college_gets_the_defaults_and_not_an_error(self):
        """Every student on a deployment that has not built its institution yet.

        `interview_policies.college_id` is NOT NULL precisely so that the
        programme-wide fallback is the ABSENCE of a row rather than a row full
        of NULLs — two places for one answer — so a spine that reaches no
        college must resolve to `config.py` and never to nothing.
        """
        with SessionLocal() as db:
            assert resolve_policy(db, college_id=None, course_id=None).source == "default"


# ---------------------------------------------------------------------------
# B6.4 — two ceilings
# ---------------------------------------------------------------------------
@requires_db
class TestTheTwoCeilings:
    def test_completed_interviews_are_what_spend_the_practice_allowance(self, student):
        _grant_consent(student.user_id)
        now = datetime.now(timezone.utc)
        _sessions(student.student_id, status="completed", count=8, started=now)
        with pytest.raises(_DailyCapReached) as caught:
            _open(student)
        assert caught.value.which == "daily"
        assert "practice" not in caught.value.message or caught.value.message

    def test_an_abandoned_interview_no_longer_costs_a_student_a_turn(self, student):
        """THE B6.4 CHANGE, and the one a test has to hold in place.

        Before this, every row counted: a student whose microphone failed eight
        times was told to come back tomorrow. The practice allowance now counts
        completions, so eight abandoned sessions leave it untouched — and the
        test asserts the interview OPENS, not merely that a number is smaller.
        """
        _grant_consent(student.user_id)
        now = datetime.now(timezone.utc)
        _sessions(student.student_id, status="abandoned", count=8, started=now)
        opened = _open(student)
        assert opened.interview_session_id

    def test_the_spend_ceiling_still_stops_a_reconnect_loop(self, student):
        """AND THE HALF 04-BACKEND-CHANGES.MD LEAVES OUT.

        `_open_records` has carried the argument since the cap was written:
        every session billed an upstream handshake, and "a cap that only counts
        clean finishes is a cap a crash loop never hits". Counting completions
        alone would hand a reconnect loop unlimited billable Bedrock
        handshakes, so `attempt_cap` counts every row whatever its status.
        """
        _grant_consent(student.user_id)
        now = datetime.now(timezone.utc)
        _sessions(student.student_id, status="failed", count=20, started=now)
        with pytest.raises(_DailyCapReached) as caught:
            _open(student)
        assert caught.value.which == "attempts"
        # The two refusals do not read the same. "You have used your 8 practice
        # interviews" and "too many attempts" are different things to be told,
        # and only one of them is the student's own doing.
        assert "attempts" in caught.value.message.lower()

    def test_the_window_is_rolling_and_yesterday_does_not_count(self, student):
        _grant_consent(student.user_id)
        now = datetime.now(timezone.utc)
        _sessions(
            student.student_id,
            status="completed",
            count=8,
            started=now - timedelta(days=1, hours=1),
        )
        assert _open(student).interview_session_id

    def test_the_policy_sets_the_ceilings_not_the_env(self, student):
        _grant_consent(student.user_id)
        _write_policy(student.college_id, None, daily_cap=1, attempt_cap=1)
        now = datetime.now(timezone.utc)
        _sessions(student.student_id, status="completed", count=1, started=now)
        with pytest.raises(_DailyCapReached):
            _open(student)


@requires_db
class TestTheCapReset:
    def test_a_reset_is_an_extra_lower_bound_and_never_a_wider_window(self, student):
        """Written the other way round — the reset REPLACES the window — an old
        row would widen it back open and a student would be counted against
        attempts from last week. This way a second reset can only move the bound
        forward."""
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            db.add(
                InterviewCapReset(
                    student_id=student.student_id,
                    at=now - timedelta(days=5),
                    reason="An old reset that must not widen the window.",
                )
            )
            db.commit()
        with SessionLocal() as db:
            since = cap_window_start(db, student.student_id, now)
        # The rolling bound wins: a reset five days old is INSIDE the window it
        # would have opened, so it changes nothing at all.
        assert since == now - timedelta(days=1)

        # ...and a reset inside the window moves the bound FORWARD.
        with SessionLocal() as db:
            db.add(
                InterviewCapReset(
                    student_id=student.student_id,
                    at=now - timedelta(hours=3),
                    reason="Inside the window, so this one counts.",
                )
            )
            db.commit()
        with SessionLocal() as db:
            moved = cap_window_start(db, student.student_id, now)
        assert moved > since

    def test_sessions_before_the_reset_stop_counting(self, student):
        now = datetime.now(timezone.utc)
        _sessions(
            student.student_id,
            status="completed",
            count=8,
            started=now - timedelta(hours=2),
        )
        with SessionLocal() as db:
            before = cap_counts(
                db, student.student_id, cap_window_start(db, student.student_id, now)
            )
            assert before == (8, 8)
            db.add(
                InterviewCapReset(
                    student_id=student.student_id,
                    at=now - timedelta(hours=1),
                    reason="Their microphone was broken all morning.",
                )
            )
            db.commit()
        with SessionLocal() as db:
            after = cap_counts(
                db, student.student_id, cap_window_start(db, student.student_id, now)
            )
        assert after == (0, 0)

    def test_a_student_mid_day_never_loses_attempts_already_counted(self, student):
        """The compatibility rule, stated as the direction it can move.

        A reset can only ever REMOVE rows from the count. There is no input to
        any of this that makes a session that was not counted start counting,
        which is what "never loses attempts already counted" means.
        """
        now = datetime.now(timezone.utc)
        _sessions(student.student_id, status="completed", count=3, started=now)
        with SessionLocal() as db:
            policy = policy_for_student(db, student.student_id)
            first = evaluate_caps(db, student.student_id, policy, now)
            db.add(
                InterviewCapReset(
                    student_id=student.student_id,
                    at=now,
                    reason="Given back after an outage.",
                )
            )
            db.commit()
        with SessionLocal() as db:
            second = evaluate_caps(db, student.student_id, policy, now)
        assert second.completed <= first.completed


# ---------------------------------------------------------------------------
# B6.1 — enforcement: the transcript, the audio, the retention stamp
# ---------------------------------------------------------------------------
@requires_db
class TestEnforcement:
    def test_store_transcript_false_writes_neither_row_and_says_so_on_the_session(
        self, student
    ):
        """THE FIRST ENFORCEMENT OF THIS SCOPE, not a change to an old one.

        `scope_store_transcript` has been recorded on every consent row since
        2026-08 and read by NOTHING — the copy on the assistant screen promised
        something no code decided. It suppresses BOTH rows: keeping
        `interview_turns` and dropping `messages` would leave the student's own
        words in the reviewable record while the chat history claimed they were
        gone.

        And `transcript_suppressed` is why the runbook survives it:
        `turns_emitted` > `turns_persisted` is AGENTS.md's signal for dropped
        writes, so without the flag every interview at such a college would fire
        it and the runbook would become noise.
        """
        _grant_consent(student.user_id)
        _write_policy(student.college_id, None, store_transcript=False)
        opened = _open(student)
        assert opened.policy.store_transcript is False

        with SessionLocal() as db:
            row = db.get(InterviewSession, opened.interview_session_id)
            assert row.transcript_suppressed is True

        write = _make_turn_writer(
            opened.conversation_id,
            opened.interview_session_id,
            store_transcript=opened.policy.store_transcript,
        )
        write(
            "user",
            "I led a team of four on the campus placement drive.",
            "turn-1",
            _turn(1),
        )
        with SessionLocal() as db:
            turns = db.scalars(
                select(InterviewTurn).where(
                    InterviewTurn.interview_session_id
                    == opened.interview_session_id
                )
            ).all()
            messages = db.scalars(
                select(Message).where(
                    Message.conversation_id == opened.conversation_id,
                    Message.channel == "interview",
                )
            ).all()
        assert turns == []
        assert messages == []

    def test_the_default_policy_still_writes_the_transcript(self, student):
        """The compatibility half of the test above: a college that has decided
        nothing keeps everything it kept before."""
        _grant_consent(student.user_id)
        opened = _open(student)
        assert opened.policy.store_transcript is True

        write = _make_turn_writer(
            opened.conversation_id,
            opened.interview_session_id,
            store_transcript=opened.policy.store_transcript,
        )
        write("user", "A sentence that is kept.", "turn-1", _turn(1))
        with SessionLocal() as db:
            turns = db.scalars(
                select(InterviewTurn).where(
                    InterviewTurn.interview_session_id
                    == opened.interview_session_id
                )
            ).all()
        assert len(turns) == 1

    def test_the_retention_window_is_stamped_from_the_policy_at_open(self, student):
        """STAMPED, never computed at read time — so lowering the number later
        cannot retroactively re-date an interview a student was already promised
        180 days for. That promise is the reason the column exists."""
        _grant_consent(student.user_id)
        _write_policy(student.college_id, None, retention_days=30)
        opened = _open(student)
        with SessionLocal() as db:
            row = db.get(InterviewSession, opened.interview_session_id)
            delta = row.retention_until - row.started_at
        assert 29 <= delta.days <= 30

    def test_the_college_is_a_third_switch_on_the_recorder(self, monkeypatch, student):
        """Three switches, three different people — and ALL of them required.

        The operator's `INTERVIEW_RECORDING_ENABLED`, the college's
        `store_audio`, and the student's own live grant. A policy that turned
        recording on over a student who was never told would be the one failure
        this whole area exists to prevent, and a college that says no must be
        able to stop a student's stale tick.
        """
        from app import interview_audio

        monkeypatch.setattr(settings, "interview_recording_enabled", True)
        with SessionLocal() as db:
            db.add(
                InterviewConsent(
                    user_id=student.user_id,
                    version=settings.interview_consent_version,
                    scope_live_ai=True,
                    scope_store_transcript=True,
                    scope_store_audio=True,
                )
            )
            db.commit()
        assert interview_audio.audio_consent_granted(student.user_id) is True
        assert (
            interview_audio.recorder_for(
                uuid.uuid4().hex, student.user_id, policy_allows_audio=False
            )
            is None
        )


# ---------------------------------------------------------------------------
# The heartbeat — 4014 still means "a scope you were running under is gone"
# ---------------------------------------------------------------------------
@requires_db
class TestTheRunningGateAfterB61:
    def test_an_equal_acknowledgement_superseding_the_pinned_one_does_not_stop_it(
        self, student
    ):
        """The regression B6.1 would otherwise introduce.

        The client posts an acknowledgement at every Start. If a supersede ended
        the interview it superseded, a student opening a second tab would close
        the interview running in the first with "Consent withdrawn" — a sentence
        they did not earn. The heartbeat asks a second question instead: does a
        LIVE grant still cover every scope this interview is running under?
        """
        _grant_consent(student.user_id)
        opened = _open(student)
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            db.get(InterviewConsent, opened.consent_id).revoked_at = now
            db.add(
                InterviewConsent(
                    user_id=student.user_id,
                    version=settings.interview_consent_version,
                    scope_live_ai=True,
                    scope_store_transcript=True,
                    scope_store_audio=False,
                )
            )
            db.commit()

        stopped: list[bool] = []
        _make_heartbeat(
            opened.interview_session_id,
            consent_id=opened.consent_id,
            on_consent_revoked=lambda: stopped.append(True),
            consent_scopes=opened.consent_scopes,
        )()
        assert stopped == []

    def test_a_narrower_acknowledgement_stops_it_with_4014(self, student):
        """"A policy change stops a running session with 4014 only when it
        removes a scope" — the compatibility board's rule, expressed as a
        property of the GRANT rather than as a second read of the policy table
        on every heartbeat of every live interview."""
        _grant_consent(student.user_id)
        opened = _open(student)
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            db.get(InterviewConsent, opened.consent_id).revoked_at = now
            db.add(
                InterviewConsent(
                    user_id=student.user_id,
                    version=settings.interview_consent_version,
                    scope_live_ai=True,
                    # The college turned the transcript off; the interview was
                    # opened on a grant that kept it.
                    scope_store_transcript=False,
                    scope_store_audio=False,
                )
            )
            db.commit()

        stopped: list[bool] = []
        _make_heartbeat(
            opened.interview_session_id,
            consent_id=opened.consent_id,
            on_consent_revoked=lambda: stopped.append(True),
            consent_scopes=opened.consent_scopes,
        )()
        assert stopped == [True]

    def test_with_no_successor_at_all_it_still_stops(self, student):
        """The old behaviour, unchanged: nothing live means nothing covers it."""
        _grant_consent(student.user_id)
        opened = _open(student)
        with SessionLocal() as db:
            db.get(InterviewConsent, opened.consent_id).revoked_at = datetime.now(
                timezone.utc
            )
            db.commit()

        stopped: list[bool] = []
        _make_heartbeat(
            opened.interview_session_id,
            consent_id=opened.consent_id,
            on_consent_revoked=lambda: stopped.append(True),
            consent_scopes=opened.consent_scopes,
        )()
        assert stopped == [True]


# ---------------------------------------------------------------------------
# The HTTP surface — the fences
# ---------------------------------------------------------------------------
@requires_db
class TestTheAdminEndpoints:
    def test_a_student_cannot_write_a_policy(self, client, login, spine):
        headers = login("student@bgscet.ac.in", "student123")
        r = client.put(
            f"/api/admin/interview-policies/{spine.college_id}",
            json={"daily_cap": 2},
            headers=headers,
        )
        assert r.status_code == 403, r.text

    def test_the_main_admin_writes_it_and_the_change_is_audited(
        self, client, login, spine
    ):
        headers = login("admin@bgscet.ac.in", "admin123")
        r = client.put(
            f"/api/admin/interview-policies/{spine.college_id}",
            json={"daily_cap": 4, "attempt_cap": 9, "store_transcript": False},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["daily_cap"], body["attempt_cap"]) == (4, 9)
        assert body["store_transcript"] is False
        # Omitted fields keep what was already governing these students.
        assert body["retention_days"] == settings.interview_retention_days

        from app.models.redesign import AuditEvent

        with SessionLocal() as db:
            events = db.scalars(
                select(AuditEvent).where(AuditEvent.entity_id == body["id"])
            ).all()
        assert [e.action for e in events] == ["INTERVIEW_POLICY_SET"]

    def test_an_omitted_field_keeps_the_stored_number(self, client, login, spine):
        headers = login("admin@bgscet.ac.in", "admin123")
        client.put(
            f"/api/admin/interview-policies/{spine.college_id}",
            json={"daily_cap": 4, "attempt_cap": 9},
            headers=headers,
        )
        r = client.put(
            f"/api/admin/interview-policies/{spine.college_id}",
            json={"store_audio": True},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["daily_cap"] == 4, "a partial edit reset a number nobody sent"

    def test_an_attempt_ceiling_below_the_practice_cap_is_refused(
        self, client, login, spine
    ):
        """The load-bearing arm of `ck_interview_policy_bounds`: the other order
        makes the student's allowance silently become the spend ceiling."""
        headers = login("admin@bgscet.ac.in", "admin123")
        r = client.put(
            f"/api/admin/interview-policies/{spine.college_id}",
            json={"daily_cap": 10, "attempt_cap": 2},
            headers=headers,
        )
        assert r.status_code == 422, r.text

    def test_the_cross_field_rule_is_judged_against_the_stored_row(
        self, client, login, spine
    ):
        """A request that raises `daily_cap` alone must be checked against the
        STORED `attempt_cap`, not against a default."""
        headers = login("admin@bgscet.ac.in", "admin123")
        client.put(
            f"/api/admin/interview-policies/{spine.college_id}",
            json={"daily_cap": 2, "attempt_cap": 3},
            headers=headers,
        )
        r = client.put(
            f"/api/admin/interview-policies/{spine.college_id}",
            json={"daily_cap": 9},
            headers=headers,
        )
        assert r.status_code == 422, r.text

    def test_a_course_from_another_college_is_a_404_on_this_path(
        self, client, login, spine
    ):
        headers = login("admin@bgscet.ac.in", "admin123")
        other_college = f"pol-col-{uuid.uuid4().hex[:8]}"
        other_dept = f"pol-dep-{uuid.uuid4().hex[:8]}"
        other_course = f"pol-crs-{uuid.uuid4().hex[:8]}"
        with SessionLocal() as db:
            db.add(College(id=other_college, name="Other", code=other_college[:12]))
            db.add(
                Department(
                    id=other_dept,
                    college_id=other_college,
                    name="Other Dept",
                    code=other_dept[:12],
                )
            )
            db.flush()
            db.add(
                AcademicCourse(
                    id=other_course,
                    department_id=other_dept,
                    code=other_course[:12],
                    name="Other Course",
                )
            )
            db.commit()
        try:
            r = client.put(
                f"/api/admin/interview-policies/{spine.college_id}/{other_course}",
                json={"daily_cap": 2},
                headers=headers,
            )
            assert r.status_code == 404, r.text
        finally:
            with SessionLocal() as db:
                db.execute(
                    delete(AcademicCourse).where(AcademicCourse.id == other_course)
                )
                db.execute(delete(Department).where(Department.id == other_dept))
                db.execute(delete(College).where(College.id == other_college))
                db.commit()

    def test_a_college_scoped_holder_cannot_reach_another_college(
        self, client, login, make_user, spine
    ):
        """B1.2. A staff member can hold this key SOMEWHERE and not HERE, and a
        scope check that stopped at "did you also send a college_id" would be a
        fence with a gate in it."""
        faculty = make_user("ivpolicy-scope", Role.MENTOR)
        with SessionLocal() as db:
            db.add(
                CapabilityGrant(
                    capability=KEY,
                    subject_kind=SubjectKind.USER,
                    subject_user_id=faculty.user_id,
                    scope_level=ScopeLevel.COLLEGE,
                    scope_id=spine.college_id,
                    reason="Scope test: this college only.",
                    approval_state="active",
                )
            )
            db.commit()
        try:
            other = f"pol-col-{uuid.uuid4().hex[:8]}"
            with SessionLocal() as db:
                db.add(College(id=other, name="Not theirs", code=other[:12]))
                db.commit()
            try:
                ok = client.put(
                    f"/api/admin/interview-policies/{spine.college_id}",
                    json={"daily_cap": 5},
                    headers=faculty.headers,
                )
                assert ok.status_code == 200, ok.text
                refused = client.put(
                    f"/api/admin/interview-policies/{other}",
                    json={"daily_cap": 5},
                    headers=faculty.headers,
                )
                assert refused.status_code == 403, refused.text
            finally:
                with SessionLocal() as db:
                    db.execute(delete(College).where(College.id == other))
                    db.commit()
        finally:
            with SessionLocal() as db:
                db.execute(
                    delete(CapabilityGrant).where(
                        CapabilityGrant.subject_user_id == faculty.user_id
                    )
                )
                db.commit()


@requires_db
class TestTheCapResetEndpoint:
    def test_a_blank_reason_is_refused(self, client, login):
        headers = login("admin@bgscet.ac.in", "admin123")
        student_id = _seeded_student_id()
        r = client.post(
            f"/api/admin/students/{student_id}/interview-cap/reset",
            json={"reason": "   "},
            headers=headers,
        )
        assert r.status_code == 422, r.text

    def test_it_writes_a_row_and_an_audit_event(self, client, login):
        headers = login("admin@bgscet.ac.in", "admin123")
        student_id = _seeded_student_id()
        r = client.post(
            f"/api/admin/students/{student_id}/interview-cap/reset",
            json={"reason": "Their microphone failed through four attempts."},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        reset_id = r.json()["id"]
        try:
            from app.models.redesign import AuditEvent

            with SessionLocal() as db:
                row = db.get(InterviewCapReset, reset_id)
                assert row is not None and row.student_id == student_id
                events = db.scalars(
                    select(AuditEvent).where(AuditEvent.entity_id == reset_id)
                ).all()
            assert [e.action for e in events] == ["INTERVIEW_CAP_RESET"]
        finally:
            with SessionLocal() as db:
                db.execute(
                    delete(InterviewCapReset).where(InterviewCapReset.id == reset_id)
                )
                db.commit()

    def test_a_student_cannot_reset_their_own_cap(self, client, login):
        headers = login("student@bgscet.ac.in", "student123")
        student_id = _seeded_student_id()
        r = client.post(
            f"/api/admin/students/{student_id}/interview-cap/reset",
            json={"reason": "Please."},
            headers=headers,
        )
        assert r.status_code == 403, r.text


def _seeded_student_id() -> str:
    with SessionLocal() as db:
        return db.scalar(
            select(Student.id)
            .join(User, Student.user_id == User.id)
            .where(User.email == "student@bgscet.ac.in")
        )


@requires_db
class TestTheStudentCard:
    def test_it_reports_the_policy_the_usage_and_the_tracks(self, client, login):
        headers = login("student@bgscet.ac.in", "student123")
        r = client.get("/api/interview/policy", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["consent_version"] == settings.interview_consent_version
        assert body["provider_label"].strip()
        assert body["policy"]["source"] in {"default", "college", "course"}
        assert body["usage"]["daily_cap"] == body["policy"]["daily_cap"]
        # B5.3: the four tracks the socket would actually accept. NULL
        # `default_track` is a real answer — the seeded batch names a
        # specialization no track is mapped to — and the picker stays.
        assert {t["code"] for t in body["tracks"]} >= {"hr", "dm", "ba", "fa"}

    def test_it_says_whether_this_server_can_record_at_all(
        self, client, login, monkeypatch
    ):
        """The room's "recording on/off" reads the policy AND the operator's
        switch, never the student's own consent row — a row is a copy of the
        policy on the day they agreed, and it said "on" over an interview the
        recorder refused. The switch is the server's, so the card carries it."""
        headers = login("student@bgscet.ac.in", "student123")
        monkeypatch.setattr(settings, "interview_recording_enabled", False)
        assert client.get("/api/interview/policy", headers=headers).json()[
            "recording_enabled_on_server"
        ] is False
        monkeypatch.setattr(settings, "interview_recording_enabled", True)
        assert client.get("/api/interview/policy", headers=headers).json()[
            "recording_enabled_on_server"
        ] is True

    def test_a_disabled_track_is_not_offered(self, client, login):
        """`resolve_specialization` refuses a code whose row is DISABLED — the
        switch has to actually stop the interview — so the card must not offer
        it back off the code constant. A button the socket answers with close
        4010 is worse than a missing one."""
        from app.models.interview_track import InterviewTrack

        with SessionLocal() as db:
            row = db.scalar(
                select(InterviewTrack).where(
                    InterviewTrack.code == "hr", InterviewTrack.college_id.is_(None)
                )
            )
            assert row is not None, "the four tracks are seeded by the migration"
            row.enabled = False
            db.commit()
        try:
            headers = login("student@bgscet.ac.in", "student123")
            body = client.get("/api/interview/policy", headers=headers).json()
            assert "hr" not in {t["code"] for t in body["tracks"]}
            assert {"dm", "ba", "fa"} <= {t["code"] for t in body["tracks"]}
        finally:
            with SessionLocal() as db:
                db.scalar(
                    select(InterviewTrack).where(
                        InterviewTrack.code == "hr",
                        InterviewTrack.college_id.is_(None),
                    )
                ).enabled = True
                db.commit()

    def test_it_refuses_a_caller_who_is_not_a_student(self, client, login):
        headers = login("mentor@bgscet.ac.in", "mentor123")
        r = client.get("/api/interview/policy", headers=headers)
        assert r.status_code == 403, r.text

    def test_being_over_the_cap_is_a_200_that_says_so_and_not_a_refusal(
        self, client, login, student
    ):
        """Two places that can say "no" is two places to disagree, and the one
        that matters is the one holding the advisory lock. This is a READ."""
        now = datetime.now(timezone.utc)
        # Just past the allowance, not fifty: `GET /api/mentor/interviews` lists
        # the newest 200 interviews on the deployment, so a test that writes a
        # large pile of them can push another test's rows off the end of that
        # list. Bulk rows are cheap to write and expensive to be wrong about.
        _sessions(
            student.student_id,
            status="completed",
            count=settings.interview_max_per_student_per_day + 1,
            started=now,
        )
        with SessionLocal() as db:
            user = db.get(User, student.user_id)
            user.password_hash = __import__(
                "app.security", fromlist=["hash_password"]
            ).hash_password("policycard123")
            db.commit()
        headers = login(_email_of(student.user_id), "policycard123")
        r = client.get("/api/interview/policy", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["usage"]["completed"] >= r.json()["usage"]["daily_cap"]


def _email_of(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(User.email).where(User.id == user_id))
