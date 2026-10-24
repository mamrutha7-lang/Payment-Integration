"""Phase 4c's schema: the track catalogue, the college's policy, the retained
score and the cap reset.

Four of these tests need no database at all, and they are the ones that catch
the mistakes this area is actually prone to:

  * the migration's seeded tracks silently losing a field — the DM syllabus is
    the one 04-backend-changes.md's column list omits, and a track without it
    runs the Digital Marketing interview as a generic CMO chat with nothing on
    any screen to say so;
  * a voice Nova will not accept, which is a ValidationException AT THE
    HANDSHAKE, i.e. an interview that never starts;
  * a purge verdict that keeps a student's scores after the student is gone.

The rest need Postgres and are skipped without it. The two that matter most
there are the FK rules: `interview_score_summaries.session_id` MUST be SET NULL
(or the retention job deletes the summary along with the interview and B6.2
silently does nothing at all, correctly in every test that does not run a purge)
and `student_id` MUST cascade (or a deleted student leaves scores behind).
"""

import uuid

import pytest
from sqlalchemy import delete, func, select

from conftest import requires_db

from app import purge_people, purge_students
from app.db import SessionLocal
from app.interview_matrix import KNOWN_NOVA_VOICES, SPECIALIZATIONS
from app.models.interview import (
    InterviewCapReset,
    InterviewScoreSummary,
    InterviewSession,
)
from app.models.interview_track import InterviewTrack
from app.models.user import Role, Student, User

MIGRATION = "a4f7d2c80b93"


def _seeded_tracks() -> list[dict]:
    """`_SEEDED_TRACKS` out of the migration, loaded by path.

    Imported by file rather than as a module because a revision id is not a
    legal Python identifier and the versions directory is not a package.
    """
    import importlib.util
    from pathlib import Path

    path = next(
        Path(__file__).resolve().parents[1].joinpath("migrations", "versions").glob(
            f"{MIGRATION}_*.py"
        )
    )
    spec = importlib.util.spec_from_file_location("_mig_4c", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._SEEDED_TRACKS


# ---------------------------------------------------------------------------
# No database needed — and these are the ones that catch the real mistakes
# ---------------------------------------------------------------------------


def test_the_seeded_tracks_are_the_matrix_field_for_field():
    """The migration's snapshot must equal `interview_matrix.SPECIALIZATIONS`.

    Not "roughly" and not "the columns 04 lists". While the table is empty the
    constant is still the fallback the engine reads, so the moment the two
    disagree the interview changes when the table fills — which happens on
    deploy, for everybody, with no screen showing it. `syllabus` is the field
    that would go missing (04's column list omits it and only `dm` has one) and
    `nova_voice` is the field whose corruption is fatal rather than cosmetic.
    """
    seeded = {t["code"]: t for t in _seeded_tracks()}
    assert set(seeded) == set(SPECIALIZATIONS), (
        "the seeded codes must be exactly hr/dm/ba/fa — that string is what "
        "?specialization= carries and what every interview_sessions row holds"
    )
    for code, spec in SPECIALIZATIONS.items():
        row = seeded[code]
        assert row["label"] == spec.label, code
        assert row["persona"] == spec.persona, code
        assert row["sample_question"] == spec.sample_question, code
        assert row["nova_voice"] == spec.nova_voice, code
        assert row["frameworks"] == list(spec.frameworks), code
        assert row["syllabus"] == list(spec.syllabus), code


def test_only_the_dm_seed_carries_a_syllabus():
    """The specific loss this area is exposed to, pinned on its own.

    `tests/test_interview_matrix.py` pins it on the constant; this pins it on
    the rows the migration writes, because those are what the engine will read
    once B5.1's compile step lands.
    """
    seeded = {t["code"]: t for t in _seeded_tracks()}
    assert seeded["dm"]["syllabus"], "the DM track lost its mapped syllabus"
    for code in ("hr", "ba", "fa"):
        assert seeded[code]["syllabus"] == [], code


def test_the_seeded_voices_are_voices_nova_accepts():
    """An unknown voiceId is a Bedrock ValidationException during the handshake
    — an interview that never starts, with nothing in the UI naming the cause.
    """
    for track in _seeded_tracks():
        assert track["nova_voice"] in KNOWN_NOVA_VOICES, track["code"]


def test_a_track_refuses_a_voice_nova_does_not_know():
    """The write-time guard, which is what turns that failure into a 422 on the
    admin form instead of a dead socket for the next student on that track.

    NOT a CHECK constraint and NOT a PG enum: AWS adds voices, and a deployment
    that cannot use a new one until somebody ships a migration is the same trap
    §6.1 describes for `code`.
    """
    track = InterviewTrack(code="hr", label="HR", persona="a CHRO", sample_question="?")
    with pytest.raises(ValueError, match="Nova"):
        track.nova_voice = "coral"  # an OpenAI voice; the two sets share nothing
    track.nova_voice = "KIARA "
    assert track.nova_voice == "kiara", "normalised, so a stray capital is not a 4001"
    track.nova_voice = ""
    assert track.nova_voice == "", "empty means 'use the configured generic voice'"


def test_the_new_tables_are_classified_by_both_destructors():
    """A catalogue must survive an intake; a student's scores must not.

    Spelled out per table rather than left to `check_verdicts`, which only
    proves that SOMEBODY answered. `interview_score_summaries` is the one worth
    reading twice: it is built to survive `retention.purge_expired`, and it
    would be easy to read that as "never deleted" and write KEEP — which would
    leave score rows naming students who have been removed from the deployment,
    invisible on every screen because every screen reads them through a
    `students` join that now matches nothing.
    """
    assert purge_people.VERDICTS["interview_tracks"] == purge_people.KEEP
    assert purge_people.VERDICTS["interview_policies"] == purge_people.KEEP
    assert purge_people.VERDICTS["interview_score_summaries"] == purge_people.EMPTY
    assert purge_people.VERDICTS["interview_cap_resets"] == purge_people.EMPTY

    assert purge_students.STUDENT_VERDICTS["interview_tracks"] == purge_students.KEEP
    assert purge_students.STUDENT_VERDICTS["interview_policies"] == purge_students.KEEP
    assert purge_students.STUDENT_VERDICTS["interview_score_summaries"] == purge_students.ALL
    assert purge_students.STUDENT_VERDICTS["interview_cap_resets"] == purge_students.ALL


# ---------------------------------------------------------------------------
# The database half — the FK rules, exercised as bulk deletes so it is the
# DATABASE being tested and not SQLAlchemy's in-memory cascade
# ---------------------------------------------------------------------------


@pytest.fixture
def subject():
    """A throwaway User + Student, torn down afterwards. It owns everything it
    touches, because these tests delete a student on purpose."""
    with SessionLocal() as db:
        user = User(
            email=f"iv4c-{uuid.uuid4().hex[:10]}@bgscet.ac.in",
            name="Interview 4c Fixture",
            role=Role.STUDENT,
            password_hash="x",
        )
        db.add(user)
        db.flush()
        student = Student(user_id=user.id)
        db.add(student)
        db.commit()
        ids = (user.id, student.id)

    yield type("Subject", (), {"user_id": ids[0], "student_id": ids[1]})

    with SessionLocal() as db:
        db.execute(delete(Student).where(Student.id == ids[1]))
        db.execute(delete(User).where(User.id == ids[0]))
        db.commit()


@requires_db
def test_the_summary_outlives_the_interview_it_summarises(subject):
    """`session_id` ON DELETE SET NULL — the whole point of B6.2.

    `retention.purge_expired` hard-deletes `interview_sessions` in bulk and lets
    the database cascade to the turns and the evaluation. If this FK cascaded
    too, the summary would go in the same statement and B6.2 would silently do
    nothing — passing every test that does not run a delete, and leaving the
    table empty in production after six months.
    """
    with SessionLocal() as db:
        sess = InterviewSession(
            student_id=subject.student_id, specialization="hr", status="completed"
        )
        db.add(sess)
        db.commit()
        summary = InterviewScoreSummary(
            student_id=subject.student_id,
            session_id=sess.id,
            track_code="hr",
            started_at=sess.started_at,
            status="completed",
            overall_score=61,
        )
        db.add(summary)
        db.commit()
        summary_id = summary.id

        db.execute(delete(InterviewSession).where(InterviewSession.id == sess.id))
        db.commit()

        row = db.get(InterviewScoreSummary, summary_id)
        assert row is not None, "the summary was deleted with its interview"
        assert row.session_id is None, "a NULL session means 'the interview was reaped'"
        assert row.overall_score == 61
        assert row.started_at is not None, "the date must survive the session row"


@requires_db
def test_the_summary_dies_with_the_student(subject):
    """`student_id` ON DELETE CASCADE, the opposite decision from `session_id`
    and for the opposite reason: the summary IS the student's record, and
    without them it names nobody."""
    with SessionLocal() as db:
        db.add(
            InterviewScoreSummary(
                student_id=subject.student_id,
                started_at=func.now(),
                status="abandoned",
            )
        )
        db.commit()

        db.execute(delete(Student).where(Student.id == subject.student_id))
        db.commit()
        assert db.scalar(
            select(func.count())
            .select_from(InterviewScoreSummary)
            .where(InterviewScoreSummary.student_id == subject.student_id)
        ) == 0


@requires_db
def test_a_summary_may_have_no_scores_at_all(subject):
    """Every score nullable, for `interview_evaluations`' reason: a missing
    score and a zero mean opposite things. An interview that was abandoned in
    the first minute is still summarised — "three attempts, none finished" is
    the fact a mentor most needs — and its scores are dashes, never zeros."""
    with SessionLocal() as db:
        row = InterviewScoreSummary(
            student_id=subject.student_id, started_at=func.now(), status="abandoned"
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.overall_score is None
        assert row.communication_score is None
        assert row.domain_score is None
        assert row.structure_score is None
        assert row.track_code is None, "the generic interview, not a missing value"


@requires_db
def test_a_cap_reset_must_say_why(subject):
    """B3.1's rule applied to the other action whose effect is invisible a day
    later: the window has rolled past it anyway, and nobody remembers whether it
    was an outage, a flaky browser or a favour. An empty box must not satisfy
    "say why", so the refusal is in the database and not only on the form."""
    from sqlalchemy.exc import IntegrityError

    with SessionLocal() as db:
        db.add(InterviewCapReset(student_id=subject.student_id, reason="   "))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(
            InterviewCapReset(
                student_id=subject.student_id, reason="Bedrock outage, 14:00-15:30"
            )
        )
        db.commit()


@requires_db
def test_one_programme_wide_track_per_code():
    """NULLs are DISTINCT in Postgres, so `UNIQUE (college_id, code)` does not
    by itself stop a second programme-wide 'hr'. The partial unique index does,
    and two of them would make the track lookup a coin toss between two
    different interviewers."""
    from sqlalchemy.exc import IntegrityError

    code = f"t{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        db.add(
            InterviewTrack(
                code=code, label="A", persona="an interviewer", sample_question="?"
            )
        )
        db.commit()
        db.add(
            InterviewTrack(
                code=code, label="B", persona="an interviewer", sample_question="?"
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.execute(delete(InterviewTrack).where(InterviewTrack.code == code))
        db.commit()
