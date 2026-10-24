"""Interview Engine v3 §8.3 — the two consent gates on the socket.

The gates themselves are four lines each. They are tested because of what
sits behind them: a student's voice on a remote provider's wire, and a
transcript kept for 180 days that their mentor and the placement director can
read. "Was this student consented at the time of interview X" has to be
answerable from a row, and the only way that stays true is if the code that
refuses to open an interview without one is pinned by something that fails
loudly when somebody softens it.

The two gates fail in OPPOSITE directions, and that asymmetry is the thing most
likely to be "tidied" into consistency by a later editor:

  * `_open_records` fails CLOSED. An unreachable database refuses the interview,
    because "we could not check whether they agreed" and "they agreed" are not
    the same sentence.
  * `_make_heartbeat`'s revocation watch fails OPEN. A transient database error
    must not end a live call that a real grant authorised, so it stops the
    session only on a query that came back and said the row is gone.

Everything here is database-backed, because both gates are queries and a fake
would only pin the shape of the code that was written rather than the behaviour
the student is promised. Every test owns the rows it touches and tears them
down.
"""

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.config import settings
from app.db import SessionLocal
from app.models.conversation import Conversation
from app.models.interview import InterviewConsent, InterviewSession
from app.models.user import Role, Student, User
from app.routers.interview import (
    _ConsentRequired,
    _make_heartbeat,
    _open_records,
)


@pytest.fixture
def student():
    """A throwaway User + Student, and NOTHING else.

    Deliberately no conversation and no `interview_sessions` row: half of what
    this module checks is that a refused open leaves neither behind, and a
    fixture that pre-created them could not tell the difference.
    """
    with SessionLocal() as db:
        user = User(
            email=f"ivconsent-{uuid.uuid4().hex[:10]}@bgscet.ac.in",
            name="Interview Consent Fixture",
            role=Role.STUDENT,
            password_hash="x",
        )
        db.add(user)
        db.flush()
        row = Student(user_id=user.id)
        db.add(row)
        db.commit()
        ids = (user.id, row.id)

    yield type("Subject", (), {"user_id": ids[0], "student_id": ids[1]})

    with SessionLocal() as db:
        # Student first: interview_sessions cascades off it. The user then takes
        # the conversation and the consent rows.
        db.execute(delete(Student).where(Student.id == ids[1]))
        db.execute(delete(User).where(User.id == ids[0]))
        db.commit()


def _grant(user_id: str, *, version: str | None = None, revoked=None, audio=False) -> str:
    with SessionLocal() as db:
        row = InterviewConsent(
            user_id=user_id,
            version=version or settings.interview_consent_version,
            scope_live_ai=True,
            scope_store_transcript=True,
            scope_store_audio=audio,
            revoked_at=revoked,
        )
        db.add(row)
        db.commit()
        return row.id


def _open(subject):
    """`_open_records` returns an `_OpenedInterview` RECORD since B6.1 — it now
    also carries the college's resolved policy and the pinned acknowledgement's
    scopes. The tests below read it by name rather than unpacking a tuple whose
    length is going to keep changing."""
    return _open_records(
        subject.user_id, Role.STUDENT, subject.student_id, "c0ffeec0ffee", None
    )


def _sessions_of(student_id: str) -> list[InterviewSession]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(InterviewSession).where(InterviewSession.student_id == student_id)
            )
        )


# ---------------------------------------------------------------------------
# Gate 1 — no live grant, no interview (close 4013)
# ---------------------------------------------------------------------------


@requires_db
class TestTheOpenGate:
    def test_no_grant_at_all_refuses(self, student):
        with pytest.raises(_ConsentRequired):
            _open(student)

    def test_a_refusal_writes_nothing_it_could_be_blamed_for_later(self, student):
        """The gate is the FIRST statement, before any insert, and that matters.

        A refusal that had already opened a conversation and an
        `interview_sessions` row would leave a `running` record of an interview
        that never happened -- one the orphan sweeper eventually marks
        `abandoned`, and one a mentor reads as a student who walked out. The
        student never even reached the microphone.
        """
        with pytest.raises(_ConsentRequired):
            _open(student)

        assert _sessions_of(student.student_id) == []
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(Conversation).where(
                        Conversation.owner_user_id == student.user_id
                    )
                )
                is None
            )

    def test_a_grant_for_a_version_this_server_no_longer_asks_for_refuses(self, student):
        """Consent is not retroactive, and this is the whole point of the column.

        Bumping INTERVIEW_CONSENT_VERSION is how the wording changes. A student
        carried into new terms on an old yes agreed to a sentence nobody showed
        them, so an old grant must stop counting the moment the copy moves --
        which puts them back in front of the panel rather than in front of 4013.
        """
        _grant(student.user_id, version="1999-01")
        with pytest.raises(_ConsentRequired):
            _open(student)

    def test_a_revoked_grant_refuses(self, student):
        """Withdrawal has to mean something on the next interview, at minimum.

        The historical row survives revocation on purpose (it is what pins
        `interview_sessions.consent_id` for interviews already conducted), so
        the query has to filter on `revoked_at IS NULL` rather than on the row
        existing. Reading "there is a row" as "they agreed" would make withdraw
        a button that does nothing.
        """
        from datetime import datetime, timezone

        _grant(student.user_id, revoked=datetime.now(timezone.utc))
        with pytest.raises(_ConsentRequired):
            _open(student)

    def test_a_live_grant_opens_the_interview_and_the_row_pins_that_exact_grant(
        self, student
    ):
        """`consent_id` is the answer to a question asked years later.

        Not "did this student ever consent" -- that decays to yes for everyone --
        but "was this student consented, to what wording, at the time of THIS
        interview". Only a foreign key to the grant that was live at open can
        answer it after the grant has been revoked and re-given twice.
        """
        grant_id = _grant(student.user_id)
        opened = _open(student)
        conversation_id = opened.conversation_id
        interview_session_id = opened.interview_session_id
        consent_id = opened.consent_id

        assert consent_id == grant_id
        with SessionLocal() as db:
            row = db.get(InterviewSession, interview_session_id)
            assert row is not None
            assert row.consent_id == grant_id
            assert row.conversation_id == conversation_id
            assert row.status == "running"
            assert row.retention_until is not None

    def test_a_revoked_grant_beside_a_live_one_does_not_shadow_it(self, student):
        """A student who withdrew and agreed again is consented.

        The partial unique index only forbids two LIVE grants per version, so
        the history genuinely accumulates rows and the query has to pick the
        live one out of them. Ordering by `granted_at` alone would be enough
        today and wrong the moment a revoked row is the newest.
        """
        from datetime import datetime, timezone

        _grant(student.user_id, revoked=datetime.now(timezone.utc))
        live = _grant(student.user_id)

        consent_id = _open(student).consent_id
        assert consent_id == live


# ---------------------------------------------------------------------------
# Gate 2 — revoked while it runs (close 4014)
# ---------------------------------------------------------------------------


@requires_db
class TestTheRunningGate:
    def _running_session(self, student) -> tuple[str, str]:
        grant_id = _grant(student.user_id)
        opened = _open(student)
        interview_session_id, consent_id = opened.interview_session_id, opened.consent_id
        assert consent_id == grant_id
        return interview_session_id, grant_id

    def test_a_live_grant_stamps_the_heartbeat_and_stops_nothing(self, student):
        interview_session_id, grant_id = self._running_session(student)
        with SessionLocal() as db:
            before = db.get(InterviewSession, interview_session_id).heartbeat_at

        stopped: list[bool] = []
        _make_heartbeat(
            interview_session_id,
            consent_id=grant_id,
            on_consent_revoked=lambda: stopped.append(True),
        )()

        assert stopped == []
        with SessionLocal() as db:
            assert db.get(InterviewSession, interview_session_id).heartbeat_at >= before

    def test_a_grant_revoked_mid_interview_asks_the_session_to_stop(self, student):
        """4014, and the delay is the cost of it being a poll rather than a push.

        Any registry a DELETE handler could consult is per-process, so with N
        uvicorn workers the revoking request lands on a worker that is not
        holding the socket most of the time -- and the push would then do
        nothing at all while looking like it worked. Re-reading the grant on a
        heartbeat the session is already issuing catches a revocation from any
        worker and any tab.
        """
        from datetime import datetime, timezone

        interview_session_id, grant_id = self._running_session(student)
        with SessionLocal() as db:
            grant = db.get(InterviewConsent, grant_id)
            grant.revoked_at = datetime.now(timezone.utc)
            db.commit()

        stopped: list[bool] = []
        _make_heartbeat(
            interview_session_id,
            consent_id=grant_id,
            on_consent_revoked=lambda: stopped.append(True),
        )()

        assert stopped == [True]

    def test_it_watches_the_grant_by_id_not_the_current_version(self, student):
        """An operator bumping the version must not end every live interview.

        "Is there a live grant for the CURRENT version" would answer no for
        every session already running the moment INTERVIEW_CONSENT_VERSION
        moves, and every one of those students would be told they withdrew
        consent -- a sentence they did not earn and an event nobody could
        explain afterwards. The grant they opened under is still live; that is
        the only thing this check may ask about.
        """
        interview_session_id, grant_id = self._running_session(student)
        # The grant is untouched; only the terms the server now asks for moved.
        original = settings.interview_consent_version
        settings.interview_consent_version = f"{original}-next"
        try:
            stopped: list[bool] = []
            _make_heartbeat(
                interview_session_id,
                consent_id=grant_id,
                on_consent_revoked=lambda: stopped.append(True),
            )()
            assert stopped == []
        finally:
            settings.interview_consent_version = original

    def test_with_no_consent_id_it_stamps_and_watches_nothing(self, student):
        """The relay's own tests construct it this way, and that is honest.

        Both arguments are keyword-only and default to None, meaning "stamp the
        heartbeat and watch nothing". It is never the shape a real interview
        gets -- `_open_records` refuses without a grant, so a live session
        always has an id to pass -- and the default must not become a silent
        way to run an interview with the revocation watch switched off.
        """
        interview_session_id, _ = self._running_session(student)
        _make_heartbeat(interview_session_id)()  # must not raise

    def test_a_finalized_session_is_not_re_stamped(self, student):
        """`AND status = 'running'` on the UPDATE, and it is not decoration.

        A heartbeat still in flight when the session finalizes would otherwise
        move `heartbeat_at` on a closed row, which is exactly the kind of thing
        that costs somebody an afternoon: the sweeper keys on status, so nothing
        breaks, and the timestamp lies quietly forever.
        """
        interview_session_id, grant_id = self._running_session(student)
        with SessionLocal() as db:
            row = db.get(InterviewSession, interview_session_id)
            row.status = "completed"
            db.commit()
            frozen = row.heartbeat_at

        _make_heartbeat(interview_session_id, consent_id=grant_id)()

        with SessionLocal() as db:
            assert db.get(InterviewSession, interview_session_id).heartbeat_at == frozen
