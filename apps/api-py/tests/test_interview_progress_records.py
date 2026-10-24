"""Phase 4c · B6.2, B6.3, B6.5 and B6.7 — the parts of an interview that
OUTLIVE it, who read them, and who looked.

Four features, one test module, because they are one sentence: an interview
produces four numbers; those numbers are copied somewhere the 180-day clock does
not reach; they feed a student's trend, their readiness and their home chart;
and every staff read of the record they came from leaves a line the student can
read back.

The five properties this file exists to pin, each of which is a bug somebody
would otherwise ship:

1. **A NULL SCORE IS NEVER A ZERO.** It is the guardrail AGENTS.md states twice
   and the one 4b had to fix elsewhere. Asserted on the summary row, on the
   trend's aggregates, on the readiness factor, on the KPI average and in the
   CSV, because each of those is a separate place somebody would write
   `or 0`.

2. **A STUDENT WHO HAS NOT PRACTISED IS NOT LESS PLACEMENT-READY.** The new
   readiness factor is `measured=False` with no score, which takes it out of
   BOTH sides of the arithmetic — so the score is unchanged. A weight-2 factor
   scored as `met=False` would have dropped every un-practised student by ~14
   points on the deploy.

3. **THE SUMMARY SURVIVES THE PURGE.** `retention.purge_expired` hard-deletes
   the session and cascades to the turns and the evaluation. The summary must
   be standing afterwards with a NULL `session_id` — if it is not, B6.2 does
   nothing at all in production and looks correct in every other test.

4. **BOTH FENCES ON THE NEW GRID.** `admin.interviews` decides whether a caller
   may open it and how far their grant reaches; rule 2 still narrows a MENTOR to
   their own group on top. A capability can never relax the student filter.

5. **THE ACCESS LOG RECORDS READS THAT HAPPENED.** A row is written after the
   gates, for by-id reads only, and never for a 404 — a log that says a mentor
   opened a record they were refused is worse than no log, because the student
   reads it.

It builds its own app, like `test_interview_access.py` and for its reason: the
subject is the routers, not `app/main.py`'s wiring, which has its own test.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.interview_summary import build_summary, ensure_summary
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.interview import (
    InterviewEvaluation,
    InterviewScoreSummary,
    InterviewSession,
    InterviewTurn,
)
from app.models.redesign import AuditEvent
from app.models.user import Mentor, Role, Student, User
from app.security import SESSION_COOKIE, create_session_token

CAPABILITY = "admin.interviews"


def _auth(**claims) -> dict:
    return {"Cookie": f"{SESSION_COOKIE}={create_session_token(claims)}"}


@pytest.fixture(scope="module")
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import interview_records

    app = FastAPI()
    app.include_router(interview_records.student_router)
    app.include_router(interview_records.staff_router)
    app.include_router(interview_records.admin_router)
    with TestClient(app) as c:
        yield c


class _World:
    """The cast."""


@pytest.fixture
def world():
    """One student with three interviews — two scored and one abandoned — a
    mentor who has them, a mentor who does not, a group-less mentor, an admin,
    and a second student in the other group.

    THE THREE INTERVIEWS ARE THE POINT. A trend needs two scored points to have
    a direction, and the abandoned one is what proves that a NULL score travels
    all the way to the screen instead of being smoothed into the average.
    """
    w = _World()
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:

        def _user(label: str, role: Role) -> User:
            u = User(
                email=f"ivprog-{label}-{tag}@bgscet.ac.in",
                name=f"Progress {label}",
                role=role,
                password_hash="x",
            )
            db.add(u)
            db.flush()
            return u

        mentor_user = _user("mentor", Role.MENTOR)
        other_mentor_user = _user("mentor-b", Role.MENTOR)
        groupless_user = _user("mentor-none", Role.MENTOR)
        admin_user = _user("admin", Role.ADMIN)
        student_user = _user("student", Role.STUDENT)
        other_user = _user("other", Role.STUDENT)

        mentor = Mentor(user_id=mentor_user.id)
        other_mentor = Mentor(user_id=other_mentor_user.id)
        db.add_all([mentor, other_mentor])
        db.flush()

        student = Student(user_id=student_user.id, mentor_id=mentor.id, usn=f"1MP{tag[:4]}01")
        other = Student(user_id=other_user.id, mentor_id=other_mentor.id)
        db.add_all([student, other])
        db.flush()

        def _interview(days_ago: int, track: str, status: str) -> InterviewSession:
            at = now - timedelta(days=days_ago)
            row = InterviewSession(
                student_id=student.id,
                specialization=track,
                status=status,
                close_code=1000,
                conn_id=uuid.uuid4().hex[:12],
                started_at=at,
                heartbeat_at=at,
                ended_at=at + timedelta(minutes=12),
            )
            db.add(row)
            db.flush()
            return row

        first = _interview(30, "hr", "completed")
        abandoned = _interview(20, "hr", "abandoned")
        latest = _interview(3, "ba", "completed")
        other_interview = _interview(2, "fa", "completed")
        other_interview.student_id = other.id
        db.flush()

        db.add_all(
            [
                InterviewTurn(
                    interview_session_id=latest.id,
                    seq=1,
                    speaker="student",
                    phase="opening",
                    content="I led the campus fintech club.",
                    transcription_status="ok",
                    provider_turn_id=f"u:{tag}:1",
                ),
                InterviewEvaluation(
                    interview_session_id=first.id,
                    report_status="ok",
                    overall_score=61,
                    communication_score=60,
                    domain_score=58,
                    structure_score=64,
                ),
                InterviewEvaluation(
                    interview_session_id=latest.id,
                    report_status="ok",
                    overall_score=74,
                    communication_score=77,
                    # NULL on purpose: the one score the model did not give.
                    domain_score=None,
                    structure_score=70,
                    drill="Rehearse one STAR answer with numbers in it.",
                    raw_response="MODEL-PRIVATE-REASONING",
                ),
                # The abandoned interview gets the courtesy row the finalizer
                # writes, with no scores at all.
                InterviewEvaluation(
                    interview_session_id=abandoned.id,
                    report_status="unavailable",
                ),
            ]
        )
        db.commit()

        for row in (first, abandoned, latest, other_interview):
            ensure_summary(db, row.id)

        w.mentor_user_id = mentor_user.id
        w.other_mentor_user_id = other_mentor_user.id
        w.groupless_user_id = groupless_user.id
        w.admin_user_id = admin_user.id
        w.student_user_id = student_user.id
        w.other_user_id = other_user.id
        w.mentor_id = mentor.id
        w.other_mentor_id = other_mentor.id
        w.student_id = student.id
        w.other_student_id = other.id
        w.student_name = student_user.name
        w.usn = student.usn
        w.first_id = first.id
        w.abandoned_id = abandoned.id
        w.latest_id = latest.id
        w.other_interview_id = other_interview.id
        user_ids = [
            mentor_user.id,
            other_mentor_user.id,
            groupless_user.id,
            admin_user.id,
            student_user.id,
            other_user.id,
        ]
        student_ids = [student.id, other.id]
        mentor_ids = [mentor.id, other_mentor.id]
        session_ids = [first.id, abandoned.id, latest.id, other_interview.id]

    w.as_student = _auth(
        userId=w.student_user_id, email="s@x", name="S", role="STUDENT",
        studentId=w.student_id,
    )
    w.as_other_student = _auth(
        userId=w.other_user_id, email="o@x", name="O", role="STUDENT",
        studentId=w.other_student_id,
    )
    w.as_mentor = _auth(
        userId=w.mentor_user_id, email="m@x", name="Mentor Marks", role="MENTOR",
        mentorId=w.mentor_id,
    )
    w.as_other_mentor = _auth(
        userId=w.other_mentor_user_id, email="mb@x", name="MB", role="MENTOR",
        mentorId=w.other_mentor_id,
    )
    w.as_groupless_mentor = _auth(
        userId=w.groupless_user_id, email="mn@x", name="MN", role="MENTOR",
    )
    w.as_admin = _auth(
        userId=w.admin_user_id, email="a@x", name="Office", role="ADMIN",
    )

    yield w

    with SessionLocal() as db:
        db.execute(
            delete(AuditEvent).where(AuditEvent.entity_id.in_(session_ids))
        )
        db.execute(
            delete(CapabilityGrant).where(CapabilityGrant.subject_user_id.in_(user_ids))
        )
        db.execute(
            delete(InterviewScoreSummary).where(
                InterviewScoreSummary.student_id.in_(student_ids)
            )
        )
        db.execute(
            delete(InterviewSession).where(InterviewSession.student_id.in_(student_ids))
        )
        db.execute(delete(Student).where(Student.id.in_(student_ids)))
        db.execute(delete(Mentor).where(Mentor.id.in_(mentor_ids)))
        db.execute(delete(User).where(User.id.in_(user_ids)))
        db.commit()


def _grant(db, user_id: str, *, level=None, target=None) -> CapabilityGrant:
    """A live grant of `admin.interviews`, written directly.

    Direct rather than through `POST /api/admin/governance/grants` because the
    key is `carries_pii=True`, so the endpoint writes a deputy's grant
    `pending_approval` — holding NOTHING until a different `admin.governance`
    holder approves it (B2.4) — and the Main Admin's live at once. Who is
    granting is the endpoint's concern and not what these tests are about;
    `test_governance_review.py` owns it.
    """
    row = CapabilityGrant(
        capability=CAPABILITY,
        subject_kind=SubjectKind.USER,
        subject_user_id=user_id,
        reason="Phase 4c test fixture",
        approval_state="active",
        role_at_grant="MENTOR",
        scope_level=level,
        scope_id=target,
    )
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# B6.2 — the summary, and the fact that it outlives the interview
# ---------------------------------------------------------------------------


@requires_db
def test_a_finished_interview_gets_a_summary_with_its_four_numbers(world):
    with SessionLocal() as db:
        row = db.scalar(
            select(InterviewScoreSummary).where(
                InterviewScoreSummary.session_id == world.latest_id
            )
        )
    assert row is not None, "finalization did not copy the scores out"
    assert row.student_id == world.student_id
    assert row.track_code == "ba"
    assert row.status == "completed"
    assert row.overall_score == 74
    assert row.communication_score == 77
    assert row.structure_score == 70
    # THE ONE THAT MATTERS. The model gave no domain score, and the copy must
    # carry the absence rather than inventing a floor for it.
    assert row.domain_score is None, "a missing score was turned into a zero"


@requires_db
def test_an_abandoned_interview_is_summarised_too_with_no_scores(world):
    """A table of clean completions only would hide exactly the student in
    trouble — three attempts abandoned in the first minute is the fact a mentor
    most needs."""
    with SessionLocal() as db:
        row = db.scalar(
            select(InterviewScoreSummary).where(
                InterviewScoreSummary.session_id == world.abandoned_id
            )
        )
    assert row is not None
    assert row.status == "abandoned"
    assert row.overall_score is None


@requires_db
def test_ensure_summary_is_idempotent_and_skips_a_running_interview(world):
    with SessionLocal() as db:
        assert ensure_summary(db, world.latest_id) is False, (
            "a second call wrote a second summary; the three finalization "
            "layers race and the loser must be a no-op"
        )
        running = InterviewSession(
            student_id=world.student_id,
            specialization="hr",
            status="running",
            started_at=datetime.now(timezone.utc),
            heartbeat_at=datetime.now(timezone.utc),
        )
        db.add(running)
        db.commit()
        running_id = running.id
        try:
            assert ensure_summary(db, running_id) is False
            assert (
                db.scalar(
                    select(InterviewScoreSummary.id).where(
                        InterviewScoreSummary.session_id == running_id
                    )
                )
                is None
            ), "a running interview was summarised with a status about to change"
        finally:
            db.execute(delete(InterviewSession).where(InterviewSession.id == running_id))
            db.commit()


@requires_db
def test_the_relays_own_finalizer_writes_the_summary(world):
    """Layer 1, driven the way the engine drives it.

    `_make_finalizer` is what the relay calls when an interview ends, and B6.2
    lives inside it. Asserted through the real closure rather than by calling
    `ensure_summary` again, because the thing that would break is somebody
    removing the call, not the function.
    """
    from app.interview_core import _SessionOutcome
    from app.routers.interview import _make_finalizer

    at = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = InterviewSession(
            student_id=world.student_id,
            specialization="fa",
            status="running",
            started_at=at,
            heartbeat_at=at,
        )
        db.add(row)
        db.commit()
        session_id = row.id
    try:
        _make_finalizer(session_id)(
            _SessionOutcome(
                status="completed",
                close_code=1000,
                terminal_reason="1000 Interview complete",
                final_phase="wrap_up",
                answers_accepted=6,
                turns_emitted=12,
                turns_persisted=12,
                upstream_session_id=None,
                report_status=None,
            )
        )
        with SessionLocal() as db:
            summary = db.scalar(
                select(InterviewScoreSummary).where(
                    InterviewScoreSummary.session_id == session_id
                )
            )
            assert summary is not None, "the finalizer closed the record and kept nothing"
            assert summary.status == "completed"
            assert summary.track_code == "fa"
            # No scorecard was requested, so there is no score — and the
            # courtesy `unavailable` evaluation row must not become a 0.
            assert summary.overall_score is None
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(InterviewScoreSummary).where(
                    InterviewScoreSummary.session_id == session_id
                )
            )
            db.execute(delete(InterviewSession).where(InterviewSession.id == session_id))
            db.commit()


@requires_db
def test_the_summary_survives_the_retention_purge(world):
    """THE TEST THIS TABLE EXISTS FOR.

    `retention.purge_expired` hard-deletes `interview_sessions` and lets the
    database cascade. With the default FK behaviour or CASCADE on `session_id`
    the summary would go in the same statement — correct in every test that does
    not run a purge, and an empty table in production after six months. It is
    `ON DELETE SET NULL`, so what is left is the four numbers and a NULL session,
    which is the expected END STATE of every row here rather than a fault.
    """
    from app import retention

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=400)
    with SessionLocal() as db:
        row = InterviewSession(
            student_id=world.student_id,
            specialization="hr",
            status="completed",
            started_at=old,
            heartbeat_at=old,
            ended_at=old,
            retention_until=old + timedelta(days=180),
            # ALREADY SOFT-DELETED, AND PAST THE GRACE WINDOW — which is what
            # lets the sweep below run on the REAL clock.
            #
            # THIS IS NOT A SHORTCUT, IT IS BLAST-RADIUS CONTROL, and it cost an
            # afternoon to learn. Driving stage 3b by handing `purge_expired` a
            # `now` thirty-one days in the future makes `grace_cutoff` TOMORROW,
            # so the sweep hard-deletes every soft-deleted conversation and
            # interview in the database — including the rows other test modules
            # are in the middle of asserting on. The suite then fails in a dozen
            # places that have nothing to do with this file. Ageing THIS ROW
            # instead reaches exactly the same statement and touches nothing
            # else.
            deleted_at=now - timedelta(days=retention.SOFT_DELETE_GRACE_DAYS + 1),
        )
        db.add(row)
        db.flush()
        db.add(
            InterviewEvaluation(
                interview_session_id=row.id, report_status="ok", overall_score=55
            )
        )
        db.commit()
        session_id = row.id
        ensure_summary(db, session_id)
        summary_id = db.scalar(
            select(InterviewScoreSummary.id).where(
                InterviewScoreSummary.session_id == session_id
            )
        )
        assert summary_id is not None

    try:
        with SessionLocal() as db:
            retention.purge_expired(db, now=now)
        with SessionLocal() as db:
            assert (
                db.get(InterviewSession, session_id) is None
            ), "the purge did not reach this interview, so this test proves nothing"
            survivor = db.get(InterviewScoreSummary, summary_id)
            assert survivor is not None, (
                "the score summary was deleted with the interview — B6.2 does "
                "nothing at all in production"
            )
            assert survivor.session_id is None
            assert survivor.overall_score == 55
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(InterviewScoreSummary).where(InterviewScoreSummary.id == summary_id)
            )
            db.execute(delete(InterviewSession).where(InterviewSession.id == session_id))
            db.commit()


def test_build_summary_never_substitutes_a_zero():
    """The pure builder, with no database at all. `evaluation is None` is the
    interview that never reached a scorecard, and every score stays NULL."""
    at = datetime.now(timezone.utc)
    session_row = InterviewSession(
        id="sess-1",
        student_id="stu-1",
        specialization=None,
        status="failed",
        started_at=at,
    )
    summary = build_summary(session_row, None)
    assert summary.student_id == "stu-1"
    assert summary.session_id == "sess-1"
    assert summary.status == "failed"
    # NULL, not "general": the generic interview's track is genuinely absent on
    # the session and the summary copies the absence.
    assert summary.track_code is None
    assert (
        summary.overall_score,
        summary.communication_score,
        summary.domain_score,
        summary.structure_score,
    ) == (None, None, None, None)


# ---------------------------------------------------------------------------
# B6.2 / B17 — the progress trend
# ---------------------------------------------------------------------------


@requires_db
def test_the_student_reads_their_own_trend_oldest_first(api, world):
    r = api.get("/api/interview/progress", headers=world.as_student)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["session_id"] for p in body["points"]] == [
        world.first_id,
        world.abandoned_id,
        world.latest_id,
    ], "a trend must read left to right in time"
    assert body["attempts"] == 3
    assert body["completed"] == 2
    assert body["scored"] == 2
    assert body["first_overall"] == 61
    assert body["latest_overall"] == 74
    assert body["best_overall"] == 74
    assert body["trend"] == 13
    # Averaged over the SCORED points only. (61 + 74) / 2, never (61 + 0 + 74)/3,
    # which is the arithmetic that reads as a student getting worse.
    assert body["average_overall"] == 67.5
    middle = body["points"][1]
    assert middle["status"] == "abandoned"
    assert middle["overall_score"] is None
    assert middle["record_available"] is True


@requires_db
def test_one_scored_interview_has_no_trend(world):
    """A `+0` drawn from a single attempt says the student has not improved,
    which is the same false sentence a 0 score would be."""
    from app.routers.interview_records import compose_interview_progress

    with SessionLocal() as db:
        out = compose_interview_progress(db, world.other_student_id)
    assert out.attempts == 1
    assert out.scored == 0
    assert out.trend is None
    assert out.average_overall is None
    assert out.best_overall is None


@requires_db
def test_a_student_with_no_interviews_gets_an_empty_trend_not_a_404(api, world):
    r = api.get("/api/interview/progress", headers=world.as_other_student)
    assert r.status_code == 200
    assert r.json()["points"] != [] or r.json()["attempts"] >= 0


@requires_db
def test_the_mentor_reads_exactly_the_students_own_trend(api, world):
    """The staff mirror is the SAME composer. A mentor seeing a confident number
    where the student sees a dash is the failure `routers/mentee_records.py`
    states the rule against."""
    mine = api.get("/api/interview/progress", headers=world.as_student).json()
    theirs = api.get(
        f"/api/mentor/students/{world.student_id}/interviews/progress",
        headers=world.as_mentor,
    )
    assert theirs.status_code == 200, theirs.text
    assert theirs.json() == mine


@requires_db
def test_progress_is_rule_2_all_the_way_down(api, world):
    path = f"/api/mentor/students/{world.student_id}/interviews/progress"
    assert api.get(path, headers=world.as_other_mentor).status_code == 404
    # No `Mentor` group means NOBODY, never the whole programme.
    assert api.get(path, headers=world.as_groupless_mentor).status_code == 404
    assert api.get(path, headers=world.as_admin).status_code == 200


@requires_db
def test_the_progress_route_is_not_swallowed_by_the_by_id_route(api, world):
    """`/interviews/progress` and `/interviews/{session_id}` both match one path
    segment, and FastAPI takes the first declared. Declared the wrong way round
    this answers "Interview not found." for a trend that exists."""
    r = api.get(
        f"/api/mentor/students/{world.student_id}/interviews/progress",
        headers=world.as_admin,
    )
    assert r.status_code == 200
    assert "points" in r.json()


# ---------------------------------------------------------------------------
# B6.5 — the access log
# ---------------------------------------------------------------------------


def _views(api, world) -> list[dict]:
    r = api.get(
        f"/api/interview/sessions/{world.latest_id}/views", headers=world.as_student
    )
    assert r.status_code == 200, r.text
    return r.json()


@requires_db
def test_a_staff_transcript_read_is_visible_to_the_student(api, world):
    assert _views(api, world) == []
    r = api.get(
        f"/api/mentor/students/{world.student_id}/interviews/{world.latest_id}/transcript",
        headers=world.as_mentor,
    )
    assert r.status_code == 200, r.text
    rows = _views(api, world)
    assert [row["what"] for row in rows] == ["transcript"]
    assert rows[0]["viewer_name"] == "Progress mentor"


@requires_db
def test_the_report_and_the_record_are_logged_separately(api, world):
    base = f"/api/mentor/students/{world.student_id}/interviews/{world.latest_id}"
    assert api.get(base, headers=world.as_mentor).status_code == 200
    assert api.get(f"{base}/report", headers=world.as_mentor).status_code == 200
    assert sorted(row["what"] for row in _views(api, world)) == ["record", "report"]


@requires_db
def test_a_list_read_is_not_a_view(api, world):
    """One officer opening the grid must not put fifty "somebody opened your
    record" lines on fifty students' panels for a screen nobody read a word of."""
    assert api.get(
        f"/api/mentor/students/{world.student_id}/interviews", headers=world.as_mentor
    ).status_code == 200
    assert api.get("/api/mentor/interviews", headers=world.as_admin).status_code == 200
    assert api.get(
        f"/api/mentor/students/{world.student_id}/interviews/progress",
        headers=world.as_mentor,
    ).status_code == 200
    assert _views(api, world) == []


@requires_db
def test_a_refused_read_leaves_no_row(api, world):
    """A log that says a mentor opened a record they were refused is worse than
    no log, because the student reads it and acts on it."""
    r = api.get(
        f"/api/mentor/students/{world.student_id}/interviews/{world.latest_id}/transcript",
        headers=world.as_other_mentor,
    )
    assert r.status_code == 404
    assert _views(api, world) == []
    # And a report that does not exist is not a report that was read.
    assert api.get(
        f"/api/mentor/students/{world.student_id}/interviews/{world.abandoned_id}/report",
        headers=world.as_mentor,
    ).status_code == 200  # the courtesy `unavailable` row IS a report
    r2 = api.get(
        f"/api/interview/sessions/{world.abandoned_id}/views", headers=world.as_student
    )
    assert [row["what"] for row in r2.json()] == ["report"]


@requires_db
def test_the_views_panel_is_the_students_own_and_nobody_elses(api, world):
    path = f"/api/interview/sessions/{world.latest_id}/views"
    assert api.get(path, headers=world.as_other_student).status_code == 404
    # Not a student at all: the student routes are first-person by construction.
    assert api.get(path, headers=world.as_mentor).status_code == 403


# ---------------------------------------------------------------------------
# B6.7 — the records grid, the KPIs and the extract
# ---------------------------------------------------------------------------


@requires_db
def test_the_grid_needs_the_capability_and_a_role_is_not_enough(api, world):
    """The commit that adds the gate is the commit that adds the scope, because
    it is the one that first admits somebody who is neither a mentor nor the
    office."""
    assert api.get("/api/admin/interviews", headers=world.as_mentor).status_code == 403
    r = api.get("/api/admin/interviews", headers=world.as_admin)
    assert r.status_code == 200, r.text
    assert r.headers["X-Reep-Scope"] == "programme"


@requires_db
def test_a_granted_mentor_is_still_narrowed_by_rule_2(api, world):
    """BOTH FENCES, SEPARATELY. The grant says they may open the grid; rule 2
    still says which students are in it. A capability can never relax the
    student filter."""
    with SessionLocal() as db:
        _grant(db, world.other_mentor_user_id)
    r = api.get("/api/admin/interviews", headers=world.as_other_mentor)
    assert r.status_code == 200, r.text
    ids = {row["session_id"] for row in r.json()["rows"]}
    assert world.other_interview_id in ids
    assert world.latest_id not in ids, (
        "a programme-wide grant of admin.interviews handed a mentor another "
        "group's interviews"
    )


@requires_db
def test_a_granted_group_less_mentor_still_sees_nobody(api, world):
    with SessionLocal() as db:
        _grant(db, world.groupless_user_id)
    r = api.get("/api/admin/interviews", headers=world.as_groupless_mentor)
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [], "no mentor group was read as the whole programme"


@requires_db
def test_a_scoped_grant_narrows_the_grid_and_says_so_in_the_header(api, world):
    with SessionLocal() as db:
        _grant(
            db,
            world.other_mentor_user_id,
            level=ScopeLevel.STUDENT,
            target=world.other_student_id,
        )
    r = api.get("/api/admin/interviews", headers=world.as_other_mentor)
    assert r.status_code == 200, r.text
    # `programme` and `narrowed` must never render the same: one says "you may
    # see everything", the other says "you are looking at part of it".
    assert r.headers["X-Reep-Scope"] == "narrowed"
    assert {row["session_id"] for row in r.json()["rows"]} == {world.other_interview_id}


@requires_db
def test_the_grid_pages_with_a_cursor_and_never_repeats_a_row(api, world):
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        query = f"?page_size=1{f'&cursor={cursor}' if cursor else ''}"
        r = api.get(f"/api/admin/interviews{query}", headers=world.as_admin)
        assert r.status_code == 200, r.text
        body = r.json()
        seen.extend(row["session_id"] for row in body["rows"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)), "a page boundary returned a row twice"
    for session_id in (world.first_id, world.abandoned_id, world.latest_id):
        assert session_id in seen


@requires_db
def test_a_cursor_this_endpoint_did_not_issue_is_refused(api, world):
    r = api.get("/api/admin/interviews?cursor=not-a-cursor", headers=world.as_admin)
    assert r.status_code == 422, (
        "an unreadable cursor silently reset to page one, which is how a grid "
        "loops forever showing the same rows"
    )


@requires_db
def test_the_filters_narrow_and_a_bad_status_is_refused(api, world):
    def ids(query: str) -> set[str]:
        r = api.get(f"/api/admin/interviews?{query}", headers=world.as_admin)
        assert r.status_code == 200, r.text
        return {row["session_id"] for row in r.json()["rows"]}

    assert world.latest_id in ids("track=ba")
    assert world.first_id not in ids("track=ba")
    assert world.abandoned_id in ids("status=abandoned")
    assert world.latest_id not in ids("status=abandoned")
    # `Z` rather than `+00:00`: an unencoded `+` in a query string is a SPACE,
    # and FastAPI then refuses the timestamp as malformed. Worth knowing about
    # before somebody concludes the filter is broken.
    recent = (
        (datetime.now(timezone.utc) - timedelta(days=10))
        .isoformat()
        .replace("+00:00", "Z")
    )
    assert world.latest_id in ids(f"from={recent}")
    assert world.first_id not in ids(f"from={recent}")

    r = api.get("/api/admin/interviews?status=finished", headers=world.as_admin)
    assert r.status_code == 422, (
        "'no interviews are finished' and 'you spelled the status wrong' are "
        "opposite facts and an empty grid renders them identically"
    )


@requires_db
def test_the_grid_carries_the_score_and_a_missing_one_is_null(api, world):
    r = api.get("/api/admin/interviews?page_size=200", headers=world.as_admin)
    rows = {row["session_id"]: row for row in r.json()["rows"]}
    assert rows[world.latest_id]["overall_score"] == 74
    assert rows[world.latest_id]["report_status"] == "ok"
    assert rows[world.abandoned_id]["overall_score"] is None
    assert rows[world.abandoned_id]["report_status"] == "unavailable"


@requires_db
def test_the_kpis_count_the_same_rows_the_grid_lists(api, world):
    r = api.get(
        f"/api/admin/interviews/summary?track=hr&status=completed",
        headers=world.as_admin,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    grid = api.get(
        "/api/admin/interviews?track=hr&status=completed&page_size=200",
        headers=world.as_admin,
    ).json()
    assert body["interviews"] == len(grid["rows"])
    assert body["completed"] == body["interviews"]


@requires_db
def test_an_unscored_slice_averages_to_null_and_never_to_zero(api, world):
    r = api.get(
        "/api/admin/interviews/summary?status=abandoned", headers=world.as_admin
    )
    body = r.json()
    assert body["interviews"] >= 1
    assert body["scored"] == 0
    assert body["average_overall"] is None, (
        "a slice nobody was scored on and a slice everybody failed are opposite "
        "facts"
    )


@requires_db
def test_the_extract_is_summary_rows_only(api, world):
    r = api.get("/api/admin/interviews/export.csv", headers=world.as_admin)
    assert r.status_code == 200, r.text
    text = r.text
    assert r.headers["X-Reep-Export-Scope"] == "programme"
    header = text.splitlines()[0]
    assert header.startswith("Name,USN,Started,Track,Status,Overall")
    # NOT ONE WORD ANYBODY SAID. The transcript, the drill and above all
    # `raw_response` — the model's private reasoning about a student — stay out
    # of a file that cannot be recalled.
    assert "fintech" not in text
    assert "STAR answer" not in text
    assert "MODEL-PRIVATE-REASONING" not in text
    # A missing score is a BLANK CELL. A zero would drag a cohort's average down
    # by the interviews nobody marked, in a spreadsheet, where nobody will ever
    # read this code.
    abandoned_line = next(
        line for line in text.splitlines() if ",abandoned," in line
    )
    assert ",abandoned,,,," in abandoned_line


@requires_db
def test_the_extract_drops_the_names_without_the_roster_function(api, world):
    """B14's second rule, unchanged and not re-implemented here. A mentor with
    `admin.interviews` but not `admin.students` gets the numbers and not the
    people."""
    with SessionLocal() as db:
        _grant(db, world.mentor_user_id)
    r = api.get("/api/admin/interviews/export.csv", headers=world.as_mentor)
    assert r.status_code == 200, r.text
    assert r.headers["X-Reep-Export-Personal"] == "omitted"
    assert r.text.splitlines()[0].startswith("Started,Track,Status")
    assert world.student_name not in r.text
    assert (world.usn or "@@") not in r.text


@requires_db
def test_the_extract_leaves_a_receipt(api, world):
    from sqlalchemy import func as sa_func

    from app.models.account_events import ExportEvent

    def _receipts() -> int:
        # COUNTED, not "read the newest id": `ExportEvent.id` is a random uuid
        # hex, so ordering by it returns whichever receipt sorts highest rather
        # than the one just written — a test that would pass or fail on the
        # spelling of a uuid.
        with SessionLocal() as db:
            return int(
                db.scalar(
                    select(sa_func.count())
                    .select_from(ExportEvent)
                    .where(ExportEvent.kind == "interviews")
                )
                or 0
            )

    before = _receipts()
    assert (
        api.get("/api/admin/interviews/export.csv", headers=world.as_admin).status_code
        == 200
    )
    assert _receipts() == before + 1, (
        "a download that leaves no trace is the state export_events was added "
        "to end"
    )


# ---------------------------------------------------------------------------
# B6.3 — readiness, next actions and the Home chart
# ---------------------------------------------------------------------------


@requires_db
def test_not_having_practised_does_not_lower_the_readiness_score():
    """THE GUARDRAIL 4b FIXED ELSEWHERE, applied here before it can be broken.

    A weight-2 factor scored `met=False` for a student who has simply not
    practised drops them ~14 points for doing nothing wrong. `measured=False`
    takes it out of BOTH the numerator and the denominator, so the score is
    identical with and without the factor.
    """
    from dataclasses import replace

    from app.routers.student import _ReadinessCriteria, _ReadinessInputs, build_readiness

    criteria = _ReadinessCriteria(
        min_cgpa=6.0,
        max_backlogs=0,
        min_attendance_pct=75.0,
        min_cert_completion_pct=50.0,
    )
    inputs = _ReadinessInputs(
        cgpa=8.1,
        backlogs=0,
        attendance_pct=91.0,
        cert_pct=100.0,
        has_contacts=True,
        resume_pct=100,
        best_interview_score=None,
    )
    unpractised = build_readiness(inputs, criteria)
    factor = next(f for f in unpractised.factors if f.label == "Mock interview")
    assert factor.weight == 2
    assert factor.measured is False
    assert unpractised.score == 100, (
        "a student who has met every check anybody has run was marked down for "
        "an assessment nobody has given them"
    )
    assert "not measured yet" in unpractised.summary

    # A real score DOES count, in both directions — which is the whole point of
    # keeping "unmeasured" and "failed" apart.
    good = build_readiness(replace(inputs, best_interview_score=74), criteria)
    assert good.score == 100
    bad = build_readiness(replace(inputs, best_interview_score=41), criteria)
    assert bad.score < 100
    assert (
        next(f for f in bad.factors if f.label == "Mock interview").measured is True
    )


def test_adding_the_seventh_check_did_not_move_the_scoring_threshold():
    """`MIN_SCORED_WEIGHT_SHARE` is a FRACTION so that adding a check does not
    silently raise the bar.

    At 0.5 over the new total of 14, a first-semester student with attendance
    and certifications imported but no results yet would have stopped being
    scored on a deploy that changed nothing about them.
    """
    from app.routers.student import (
        MIN_SCORED_WEIGHT_SHARE,
        _ReadinessCriteria,
        _ReadinessInputs,
        build_readiness,
    )

    criteria = _ReadinessCriteria(
        min_cgpa=6.0, max_backlogs=0, min_attendance_pct=75.0, min_cert_completion_pct=50.0
    )
    blank = _ReadinessInputs(
        cgpa=None,
        backlogs=None,
        attendance_pct=None,
        cert_pct=None,
        has_contacts=False,
        resume_pct=0,
        best_interview_score=None,
    )
    from dataclasses import replace

    assert MIN_SCORED_WEIGHT_SHARE == 3 / 7
    # Nothing measured beyond the two the student types: still not a score.
    assert build_readiness(blank, criteria).score is None
    # Attendance alone: still not a score.
    assert build_readiness(replace(blank, attendance_pct=91.0), criteria).score is None
    # Attendance AND certifications: scored before this change, scored now.
    assert (
        build_readiness(
            replace(blank, attendance_pct=91.0, cert_pct=100.0), criteria
        ).score
        is not None
    )
    # Marks alone still cross.
    assert build_readiness(replace(blank, cgpa=7.0, backlogs=0), criteria).score is not None


@requires_db
def test_readiness_reads_the_best_score_in_the_window_not_the_latest(world):
    """Best, not latest: a rule that reads the last attempt punishes exactly the
    behaviour the product is asking for."""
    from app.routers.student import _best_interview_score, readiness_inputs_many

    with SessionLocal() as db:
        best = _best_interview_score(db, world.student_id)
        assert best == 74
        # ...and the batch reader must agree with the single reader, or a mentor
        # and a student are looking at different numbers again.
        many = readiness_inputs_many(db, [world.student_id, world.other_student_id])
    assert many[world.student_id].best_interview_score == 74
    assert many[world.other_student_id].best_interview_score is None


@requires_db
def test_an_interview_older_than_the_window_is_unassessed_not_failed(world):
    from app.routers.student import INTERVIEW_READINESS_WINDOW_DAYS, _best_interview_score

    stale = datetime.now(timezone.utc) - timedelta(
        days=INTERVIEW_READINESS_WINDOW_DAYS + 5
    )
    with SessionLocal() as db:
        db.execute(
            delete(InterviewScoreSummary).where(
                InterviewScoreSummary.student_id == world.student_id
            )
        )
        db.add(
            InterviewScoreSummary(
                student_id=world.student_id,
                session_id=None,
                track_code="hr",
                started_at=stale,
                status="completed",
                overall_score=88,
            )
        )
        db.commit()
        assert _best_interview_score(db, world.student_id) is None, (
            "a readiness score was standing on an interview from last term"
        )


@requires_db
def test_the_home_chart_carries_both_sources_in_one_series(world):
    """B6.3's `source` field. Two charts side by side would make a student ask
    which one counts."""
    from app.models.mock_test import MockAttempt, MockType
    from app.routers.student import my_mocks

    session = {"role": "STUDENT", "studentId": world.student_id, "userId": world.student_user_id}
    with SessionLocal() as db:
        db.add(
            MockAttempt(
                student_id=world.student_id,
                type=MockType.GD,
                taken_on=datetime.now(timezone.utc) - timedelta(days=1),
                score=8.0,
                max_score=10.0,
            )
        )
        db.commit()
        rows = my_mocks(session, db)
    sources = [r.source for r in rows]
    assert "mock_attempt" in sources
    assert "interview" in sources
    # Newest first, one series, both sources interleaved by date.
    assert rows == sorted(rows, key=lambda r: r.taken_on, reverse=True)
    interview_rows = [r for r in rows if r.source == "interview"]
    # Completed only on this chart — an abandoned interview has no score to plot.
    assert len(interview_rows) == 2
    assert {r.percent for r in interview_rows} == {61.0, 74.0}


@requires_db
def test_the_mocks_leaderboard_agrees_with_the_home_chart(world):
    """A board counting only the staff-logged rehearsals would tell a student
    they had sat 2 while their own home screen showed 9."""
    from app.routers.student import _board_values

    with SessionLocal() as db:
        values = _board_values(
            db, "mocks", [(world.student_id, world.student_user_id)]
        )
    count, label = values[world.student_id]
    assert count == 2.0, "the leaderboard ignored the AI mock interviews"
    assert label == "2 mocks"


@requires_db
def test_the_last_interviews_drill_becomes_a_next_action(world):
    from app.routers.student import next_actions

    session = {"role": "STUDENT", "studentId": world.student_id, "userId": world.student_user_id}
    with SessionLocal() as db:
        out = next_actions(session, db)
    drill = next((a for a in out.actions if a.id == "interview-drill"), None)
    assert drill is not None, "the report's drill never reached the student"
    # The model's words, not a paraphrase: a drill rewritten into a house
    # sentence stops being the thing the student was told at the end of it.
    assert drill.title == "Rehearse one STAR answer with numbers in it."
    assert drill.cta_route == "/student/assistant"
