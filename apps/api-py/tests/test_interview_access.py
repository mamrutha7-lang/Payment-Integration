"""Interview Engine v3 §7 — who may read an interview record.

THIS FILE EXISTS FOR AGENTS.md RULE 2, and the three cases it exists for are
named as three tests you can find by searching for the word "mentor":

  * test_mentor_in_the_students_group_can_read_the_interview
  * test_mentor_in_a_different_group_gets_404
  * test_mentor_with_no_mentor_group_gets_404_and_never_the_whole_programme

A MENTOR with no `Mentor` group is the account the rule exists to exclude, and
"no group" has been read as "no filter, therefore everybody" in this codebase
before — app/routers/leave.py's header records that exact bug, found by the
2026-08 audit, in which a group-less mentor listed every leave request programme
wide with the reason attached. An interview transcript is the same class of
material: fifteen minutes of a named student's unrehearsed speech, plus a score.

The other axis is horizontal. `_assert_can_access_student` is given the PATH's
student id and can say nothing at all about a session id, so every by-id staff
endpoint re-checks that the row's own subject is that student
(test_a_mentor_cannot_read_another_groups_session_id_through_their_own_student).
Gating on one argument and loading by another is a privilege bug wearing a
correct-looking first line.

And §5.4's split: the student sees every score of their own report, staff see
the same through the group gate, and `raw_response` — the model's private
reasoning about the student — is DIRECTOR/ADMIN only. Three tests pin all three
audiences, because the tempting "simplification" here is one payload with a
conditionally-filled field.

WHY THIS BUILDS ITS OWN APP. The routers are included from `app/main.py`, which
belongs to another step of this rollout, so a test against the shared `client`
fixture would pass or fail on whether that landed rather than on anything about
access control. The app here mounts exactly the two routers under test with
their own declared prefixes, which is also what makes
test_the_routers_are_mounted_at_the_documented_paths a separate, honest check of
the wiring rather than a hidden precondition of everything else.

Sessions are minted directly with `create_session_token` rather than posted
through `POST /api/auth/login`: the claim set IS the input to every gate under
test (`role`, `studentId`, `mentorId`), and building it by hand is the only way
to express "a MENTOR with no group" — a mentor with no `Mentor` row — without
also asserting things about how sign-in composes it.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, update, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.interview import (
    InterviewConsent,
    InterviewEvaluation,
    InterviewSession,
    InterviewTurn,
)
from app.models.user import Mentor, Role, Student, User
from app.security import SESSION_COOKIE, create_session_token

RAW_RESPONSE = "MODEL-PRIVATE-REASONING-ABOUT-THE-STUDENT"


def _auth(**claims) -> dict:
    """A signed session cookie carrying exactly these claims."""
    return {"Cookie": f"{SESSION_COOKIE}={create_session_token(claims)}"}


@pytest.fixture(scope="module")
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import interview_records

    app = FastAPI()
    app.include_router(interview_records.student_router)
    app.include_router(interview_records.staff_router)
    with TestClient(app) as c:
        yield c


class _World:
    """The cast. Attribute names are the roles the tests read out loud."""


@pytest.fixture
def world():
    """Two mentor groups, a group-less mentor, a director, and one interview
    each for two students who are in DIFFERENT groups.

    Everything is thrown away afterwards. Nothing is borrowed from the dev seed:
    these tests assert on exact list contents, and a seeded student who happened
    to share a mentor would make a passing test meaningless.
    """
    w = _World()
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as db:

        def _user(label: str, role: Role) -> User:
            u = User(
                email=f"ivacc-{label}-{tag}@bgscet.ac.in",
                name=f"Interview Access {label}",
                role=role,
                password_hash="x",
            )
            db.add(u)
            db.flush()
            return u

        mentor_a_user = _user("mentor-a", Role.MENTOR)
        mentor_b_user = _user("mentor-b", Role.MENTOR)
        # A MENTOR with NO Mentor row: the session carries no mentorId, which is
        # exactly the "no group" state rule 2 is written about.
        groupless_user = _user("mentor-none", Role.MENTOR)
        director_user = _user("director", Role.ADMIN)
        student_user = _user("student", Role.STUDENT)
        other_user = _user("other-student", Role.STUDENT)
        # A STUDENT whose user row has no Student row behind it, so their
        # session carries no studentId claim.
        studentless_user = _user("no-student-row", Role.STUDENT)

        mentor_a = Mentor(user_id=mentor_a_user.id)
        mentor_b = Mentor(user_id=mentor_b_user.id)
        db.add_all([mentor_a, mentor_b])
        db.flush()

        student = Student(user_id=student_user.id, mentor_id=mentor_a.id)
        other = Student(user_id=other_user.id, mentor_id=mentor_b.id)
        db.add_all([student, other])
        db.flush()

        started = datetime.now(timezone.utc) - timedelta(minutes=20)
        interview = InterviewSession(
            student_id=student.id,
            specialization="hr",
            status="completed",
            terminal_reason="1000 Interview complete",
            final_phase="wrap_up",
            answers_accepted=5,
            turns_emitted=11,
            turns_persisted=11,
            close_code=1000,
            conn_id=uuid.uuid4().hex[:12],
            started_at=started,
            heartbeat_at=started,
            ended_at=started + timedelta(minutes=14),
        )
        other_interview = InterviewSession(
            student_id=other.id,
            specialization="ba",
            status="completed",
            close_code=1000,
            started_at=started,
            heartbeat_at=started,
        )
        db.add_all([interview, other_interview])
        db.flush()

        db.add_all(
            [
                InterviewTurn(
                    interview_session_id=interview.id,
                    seq=1,
                    speaker="interviewer",
                    phase="opening",
                    content="Tell me about yourself.",
                    transcription_status="not_applicable",
                    provider_turn_id="a:resp_1",
                ),
                InterviewTurn(
                    interview_session_id=interview.id,
                    seq=2,
                    speaker="student",
                    phase="opening",
                    content="I led the campus fintech club.",
                    transcription_status="ok",
                    answer_quality="accepted",
                    counted_as_answer=True,
                    provider_turn_id="u:item_1",
                ),
                # The turn L4 is about: the transcriber never answered, so the
                # content is '' and the STATUS carries the fact. If this row is
                # missing from a transcript payload the table has failed at the
                # one job it was added for.
                InterviewTurn(
                    interview_session_id=interview.id,
                    seq=3,
                    speaker="student",
                    phase="probing",
                    content="",
                    transcription_status="timeout",
                    provider_turn_id="u:item_2",
                ),
            ]
        )
        db.add(
            InterviewEvaluation(
                interview_session_id=interview.id,
                report_status="ok",
                overall_score=68,
                communication_score=71,
                # Left NULL deliberately: a missing score and a zero mean
                # opposite things, and the payload has to be able to carry the
                # difference.
                domain_score=None,
                structure_score=60,
                strengths=["Clear structure", "Concrete examples"],
                improvements=["Quantify outcomes"],
                drill="Rehearse one STAR answer with numbers in it.",
                summary="A solid practice session.",
                raw_response=RAW_RESPONSE,
                model="gpt-realtime",
            )
        )
        db.commit()

        w.mentor_a_user_id = mentor_a_user.id
        w.mentor_b_user_id = mentor_b_user.id
        w.groupless_user_id = groupless_user.id
        w.director_user_id = director_user.id
        w.student_user_id = student_user.id
        w.other_user_id = other_user.id
        w.studentless_user_id = studentless_user.id
        w.mentor_a_id = mentor_a.id
        w.mentor_b_id = mentor_b.id
        w.student_id = student.id
        w.other_student_id = other.id
        w.interview_id = interview.id
        w.other_interview_id = other_interview.id
        user_ids = [
            mentor_a_user.id,
            mentor_b_user.id,
            groupless_user.id,
            director_user.id,
            student_user.id,
            other_user.id,
            studentless_user.id,
        ]
        student_ids = [student.id, other.id]
        mentor_ids = [mentor_a.id, mentor_b.id]

    # Sessions the tests read as each member of the cast.
    w.as_student = _auth(
        userId=w.student_user_id,
        email="s@bgscet.ac.in",
        name="S",
        role="STUDENT",
        studentId=w.student_id,
    )
    w.as_other_student = _auth(
        userId=w.other_user_id,
        email="o@bgscet.ac.in",
        name="O",
        role="STUDENT",
        studentId=w.other_student_id,
    )
    w.as_studentless = _auth(
        userId=w.studentless_user_id,
        email="ns@bgscet.ac.in",
        name="NS",
        role="STUDENT",
    )
    w.as_mentor_in_group = _auth(
        userId=w.mentor_a_user_id,
        email="ma@bgscet.ac.in",
        name="MA",
        role="MENTOR",
        mentorId=w.mentor_a_id,
    )
    w.as_mentor_other_group = _auth(
        userId=w.mentor_b_user_id,
        email="mb@bgscet.ac.in",
        name="MB",
        role="MENTOR",
        mentorId=w.mentor_b_id,
    )
    w.as_mentor_no_group = _auth(
        userId=w.groupless_user_id,
        email="mn@bgscet.ac.in",
        name="MN",
        role="MENTOR",
    )
    w.as_director = _auth(
        userId=w.director_user_id,
        email="d@bgscet.ac.in",
        name="D",
        role="ADMIN",
    )

    yield w

    with SessionLocal() as db:
        db.execute(delete(InterviewConsent).where(InterviewConsent.user_id.in_(user_ids)))
        db.execute(
            delete(InterviewSession).where(InterviewSession.student_id.in_(student_ids))
        )
        db.execute(delete(Student).where(Student.id.in_(student_ids)))
        db.execute(delete(Mentor).where(Mentor.id.in_(mentor_ids)))
        db.execute(delete(User).where(User.id.in_(user_ids)))
        db.commit()


def _staff_paths(world) -> list[str]:
    """Every staff read of the one interview, so a test can assert on ALL of
    them instead of on whichever one it remembered."""
    base = f"/api/mentor/students/{world.student_id}/interviews"
    return [base, f"{base}/{world.interview_id}", f"{base}/{world.interview_id}/transcript",
            f"{base}/{world.interview_id}/report"]


# ---------------------------------------------------------------------------
# The student: own record only
# ---------------------------------------------------------------------------


@requires_db
def test_student_sees_their_own_interview(api, world):
    r = api.get("/api/interview/sessions", headers=world.as_student)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["id"] for row in rows] == [world.interview_id]
    row = rows[0]
    assert row["specialization"] == "hr"
    assert row["status"] == "completed"
    assert row["answers_accepted"] == 5
    # The scorecard summary comes from the LEFT JOIN, so the history screen
    # needs no second request per interview.
    assert row["report_status"] == "ok"
    assert row["overall_score"] == 68
    # `audio_recorded` is the flag to read; `audio_path` is a server-side
    # location and must not be in the payload at all.
    assert row["audio_recorded"] is False
    assert "audio_path" not in row


@requires_db
def test_student_never_sees_another_students_interview_in_the_list(api, world):
    r = api.get("/api/interview/sessions", headers=world.as_other_student)
    assert r.status_code == 200, r.text
    assert [row["id"] for row in r.json()] == [world.other_interview_id]


@requires_db
@pytest.mark.parametrize("suffix", ["", "/transcript", "/report"])
def test_another_students_session_id_is_404_and_never_403(api, world, suffix):
    """404, not 403 — a 403 would confirm that this id exists, which is the one
    thing an enumeration is trying to learn (conversations.assert_owner sets the
    rule)."""
    r = api.get(
        f"/api/interview/sessions/{world.interview_id}{suffix}",
        headers=world.as_other_student,
    )
    assert r.status_code == 404, r.text


@requires_db
def test_a_student_with_no_student_row_sees_nothing_rather_than_everything(api, world):
    """No `studentId` claim must read as "no interviews", never as "no filter".

    An unfiltered query here would hand one user the whole college's
    transcripts, and it is one forgotten `.where()` away.
    """
    r = api.get("/api/interview/sessions", headers=world.as_studentless)
    assert r.status_code == 200, r.text
    assert r.json() == []
    r = api.get(
        f"/api/interview/sessions/{world.interview_id}", headers=world.as_studentless
    )
    assert r.status_code == 404, r.text


@requires_db
def test_staff_cannot_use_the_students_own_endpoints(api, world):
    """The student endpoints are STUDENT-only. A director reads a student's
    interviews through the staff route, where the group gate lives — not through
    a route that would silently return their own (empty) list."""
    for headers in (world.as_director, world.as_mentor_in_group):
        r = api.get("/api/interview/sessions", headers=headers)
        assert r.status_code == 403, r.text


def test_the_students_own_routes_take_no_student_id():
    """§7.3, pinned structurally rather than by behaviour.

    Not `@requires_db`: this reads the route table and touches no row, and the
    IDOR it guards against is exactly the kind of change someone makes on a
    laptop with no Postgres running.

    The moment a `student_id` appears in one of these paths "for symmetry" with
    the staff routes, every student endpoint is an IDOR waiting for someone to
    forget the filter. A path parameter is easy to add and impossible to notice
    in review, so it is asserted here.
    """
    from app.routers import interview_records

    for route in interview_records.student_router.routes:
        assert "{student_id}" not in route.path, route.path


@requires_db
def test_unauthenticated_requests_are_refused(api, world):
    assert api.get("/api/interview/sessions").status_code == 401
    assert api.get(_staff_paths(world)[0]).status_code == 401


# ---------------------------------------------------------------------------
# AGENTS.md rule 2 — the three cases, by name
# ---------------------------------------------------------------------------


@requires_db
def test_mentor_in_the_students_group_can_read_the_interview(api, world):
    r = api.get(_staff_paths(world)[0], headers=world.as_mentor_in_group)
    assert r.status_code == 200, r.text
    assert [row["id"] for row in r.json()] == [world.interview_id]

    r = api.get(_staff_paths(world)[1], headers=world.as_mentor_in_group)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == world.interview_id

    r = api.get(_staff_paths(world)[2], headers=world.as_mentor_in_group)
    assert r.status_code == 200, r.text
    assert [t["seq"] for t in r.json()] == [1, 2, 3]

    r = api.get(_staff_paths(world)[3], headers=world.as_mentor_in_group)
    assert r.status_code == 200, r.text
    assert r.json()["overall_score"] == 68


@requires_db
def test_mentor_in_a_different_group_gets_404(api, world):
    """mentor_b has a real Mentor group — just not this student's. Every read of
    this student, at every depth, is 404."""
    for path in _staff_paths(world):
        r = api.get(path, headers=world.as_mentor_other_group)
        assert r.status_code == 404, f"{path} -> {r.status_code} {r.text}"


@requires_db
def test_mentor_with_no_mentor_group_gets_404_and_never_the_whole_programme(api, world):
    """THE CASE THE RULE EXISTS FOR.

    A MENTOR with no `Mentor` row has no `mentorId` claim. That must read as
    "sees NOBODY" — not as "no group filter to apply, therefore the whole
    programme". Both students are checked, because the failure mode is not
    "reads the wrong one", it is "reads all of them".
    """
    for path in _staff_paths(world):
        r = api.get(path, headers=world.as_mentor_no_group)
        assert r.status_code == 404, f"{path} -> {r.status_code} {r.text}"

    r = api.get(
        f"/api/mentor/students/{world.other_student_id}/interviews",
        headers=world.as_mentor_no_group,
    )
    assert r.status_code == 404, r.text


@requires_db
def test_director_sees_every_students_interviews(api, world):
    for student_id, interview_id in (
        (world.student_id, world.interview_id),
        (world.other_student_id, world.other_interview_id),
    ):
        r = api.get(
            f"/api/mentor/students/{student_id}/interviews", headers=world.as_director
        )
        assert r.status_code == 200, r.text
        assert [row["id"] for row in r.json()] == [interview_id]


@requires_db
def test_a_student_cannot_reach_the_staff_endpoints_even_for_their_own_record(
    api, world
):
    """`_assert_can_access_student` calls `require_mentor` first, so this is a
    403 about the ROLE and not a 404 about the row."""
    r = api.get(_staff_paths(world)[0], headers=world.as_student)
    assert r.status_code == 403, r.text


@requires_db
def test_a_mentor_cannot_read_another_groups_session_id_through_their_own_student(
    api, world
):
    """The horizontal case, and the reason every by-id staff endpoint checks the
    row's own subject.

    mentor_b legitimately passes the gate for `other_student` — that IS their
    mentee. If the handler then loaded the session id from the path without
    re-checking whose it is, mentor_b would read mentor_a's student's interview
    through a first line that looks perfectly correct.
    """
    base = f"/api/mentor/students/{world.other_student_id}/interviews"
    for suffix in ("", "/transcript", "/report"):
        r = api.get(
            f"{base}/{world.interview_id}{suffix}", headers=world.as_mentor_other_group
        )
        assert r.status_code == 404, f"{suffix or '(detail)'} -> {r.status_code}"


# ---------------------------------------------------------------------------
# §5.4 — what the student sees of their own report, and what staff see
# ---------------------------------------------------------------------------


@requires_db
def test_the_student_sees_every_score_of_their_own_report(api, world):
    """Scores are NOT withheld from the student (§5.4). The model already speaks
    its verdict aloud, and a score the student cannot see while their mentor can
    is a secret file on a student."""
    r = api.get(
        f"/api/interview/sessions/{world.interview_id}/report", headers=world.as_student
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overall_score"] == 68
    assert body["communication_score"] == 71
    assert body["structure_score"] == 60
    assert body["strengths"] == ["Clear structure", "Concrete examples"]
    assert body["improvements"] == ["Quantify outcomes"]
    assert body["summary"] == "A solid practice session."
    # NULL survives as null: "the model did not return one" must stay
    # distinguishable from "the model scored this student zero".
    assert body["domain_score"] is None


@requires_db
def test_the_student_never_receives_raw_response(api, world):
    """Not as `null`, not at all — the key's absence is the honest way to say
    "this is not yours to read"."""
    r = api.get(
        f"/api/interview/sessions/{world.interview_id}/report", headers=world.as_student
    )
    assert r.status_code == 200, r.text
    assert "raw_response" not in r.json()
    assert RAW_RESPONSE not in r.text


@requires_db
def test_a_mentor_in_group_reads_the_report_but_not_raw_response(api, world):
    """Both gates, not either. The group gate said which student; it says
    nothing about who may read the model's private reasoning about them."""
    r = api.get(_staff_paths(world)[3], headers=world.as_mentor_in_group)
    assert r.status_code == 200, r.text
    assert r.json()["overall_score"] == 68
    assert "raw_response" not in r.json()
    assert RAW_RESPONSE not in r.text


@requires_db
def test_a_director_reads_raw_response(api, world):
    r = api.get(_staff_paths(world)[3], headers=world.as_director)
    assert r.status_code == 200, r.text
    assert r.json()["raw_response"] == RAW_RESPONSE


@requires_db
def test_report_is_404_while_no_evaluation_row_exists(api, world):
    """A row is written on EVERY terminal path, including 'unavailable', so a
    missing row means the interview has not finished — a different sentence from
    "the report failed", and the client needs to be able to tell them apart."""
    r = api.get(
        f"/api/mentor/students/{world.other_student_id}/interviews/"
        f"{world.other_interview_id}/report",
        headers=world.as_director,
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# The transcript
# ---------------------------------------------------------------------------


@requires_db
def test_the_transcript_keeps_a_turn_whose_transcription_failed(api, world):
    """L4, from the read side. `content = ''` with a status saying why is the
    entire reason `interview_turns` exists; a payload that dropped it would make
    the record agree with the bug."""
    r = api.get(
        f"/api/interview/sessions/{world.interview_id}/transcript",
        headers=world.as_student,
    )
    assert r.status_code == 200, r.text
    turns = r.json()
    assert [t["seq"] for t in turns] == [1, 2, 3]
    blank = turns[2]
    assert blank["content"] == ""
    assert blank["transcription_status"] == "timeout"
    assert blank["answer_quality"] is None
    assert turns[1]["counted_as_answer"] is True
    assert turns[0]["speaker"] == "interviewer"


@requires_db
def test_a_soft_deleted_interview_is_gone_for_the_student_and_for_staff(api, world):
    """`retention.purge_expired` stamps `deleted_at` and REDACTS the turn text in
    the same step, so a soft-deleted interview is a hollowed-out record on its
    way to a hard delete. Serving it would put an empty transcript in front of a
    mentor with nothing to say why it is empty."""
    with SessionLocal() as db:
        row = db.get(InterviewSession, world.interview_id)
        row.deleted_at = datetime.now(timezone.utc)
        db.commit()

    # The student's own routes.
    assert api.get("/api/interview/sessions", headers=world.as_student).json() == []
    for suffix in ("", "/transcript", "/report"):
        r = api.get(
            f"/api/interview/sessions/{world.interview_id}{suffix}",
            headers=world.as_student,
        )
        assert r.status_code == 404, f"{suffix or '(detail)'} -> {r.status_code}"

    # And BOTH staff audiences, at every depth. A director is checked as well as
    # a mentor because the exclusion lives in the shared loader rather than in
    # the gate — a DIRECTOR passes every access check there is, so if the
    # `deleted_at IS NULL` filter were ever dropped from one of these queries,
    # the director is the caller who would still see the hollowed-out row.
    for headers in (world.as_director, world.as_mentor_in_group):
        for path in _staff_paths(world)[1:]:
            r = api.get(path, headers=headers)
            assert r.status_code == 404, f"{path} -> {r.status_code} {r.text}"
        assert api.get(_staff_paths(world)[0], headers=headers).json() == []


# ---------------------------------------------------------------------------
# Consent — a row, not a localStorage key
# ---------------------------------------------------------------------------


def _live_consents(user_id: str) -> list[InterviewConsent]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(InterviewConsent).where(
                    InterviewConsent.user_id == user_id,
                    InterviewConsent.revoked_at.is_(None),
                )
            ).all()
        )


@requires_db
def test_consent_reports_the_version_the_server_is_asking_for(api, world):
    """The client cannot post a grant without knowing the current version
    string, and this is the endpoint it already calls."""
    from app.config import settings

    r = api.get("/api/interview/consent", headers=world.as_student)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "version": settings.interview_consent_version,
        "consent": None,
        # WHO receives the student's voice, for the disclosure the panel shows
        # before they agree. Engine-dependent (INTERVIEW_ENGINE picks between
        # the OpenAI relay, Nova Sonic on Bedrock and the on-machine engine), so
        # it is read from settings rather than spelled out here. It stays inside
        # the exact-payload comparison on purpose: a field the client does not
        # expect is as much a bug as a missing one.
        "provider": settings.interview_provider_label,
    }
    # Never blank, whatever the engine is. The copy reads "your microphone audio
    # is streamed to {provider} so it can hear you", and an empty string there
    # is a sentence with a hole in it on the screen where a student agrees to
    # having their voice sent somewhere.
    assert r.json()["provider"].strip()


@requires_db
def test_consent_is_an_acknowledgement_of_the_policy_and_there_is_no_withdrawal(
    api, world
):
    """B6.1. The student acknowledges the COLLEGE's policy; they do not choose
    it, and they can no longer withdraw it.

    This test used to post three booleans and then DELETE them. Both halves
    changed and neither was weakened: the payload is now the version string
    alone (the two storage scopes are the college's, and the row still records
    three so "they consented" stays falsifiable), and `DELETE` is gone from the
    router entirely — which makes it 405 for everyone rather than 403 for a
    student. `admin_students.py` set that precedent and stated the reason: a
    capability refusal would mean the endpoint is still there waiting for a
    grant, and this one is not there at all.
    """
    from app.config import settings

    body = {"version": settings.interview_consent_version}
    r = api.post("/api/interview/consent", json=body, headers=world.as_student)
    assert r.status_code == 201, r.text
    granted = r.json()
    # No policy row exists on the seeded deployment, so these are `config.py`'s
    # defaults — which is exactly what the socket would have used, and is the
    # compatibility rule: a deployment that never opens the policy screen
    # behaves as it did before the table existed.
    assert granted["scope_live_ai"] is True
    assert granted["scope_store_transcript"] is True
    assert granted["scope_store_audio"] is False
    # The audit fields are recorded but not handed back to the browser that
    # supplied them.
    assert "source_ip_hash" not in granted and "user_agent" not in granted

    r = api.get("/api/interview/consent", headers=world.as_student)
    assert r.json()["consent"]["id"] == granted["id"]

    # THE WITHDRAWAL ROUTE IS GONE. 405 and not 403: the operation does not
    # exist here, which is true; 403 would say it exists and you may not, which
    # is not.
    r = api.delete("/api/interview/consent", headers=world.as_student)
    assert r.status_code == 405, r.text
    # ...and nothing was revoked by asking.
    assert api.get("/api/interview/consent", headers=world.as_student).json()[
        "consent"
    ]["id"] == granted["id"]
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(InterviewConsent).where(
                    InterviewConsent.user_id == world.student_user_id
                )
            ).all()
        )
    assert len(rows) == 1 and rows[0].revoked_at is None


@requires_db
def test_acknowledging_twice_returns_the_standing_row_and_does_not_supersede_it(
    api, world
):
    """IDEMPOTENCE IS LOAD-BEARING HERE, not tidiness.

    The client posts this at every Start. If an unchanged acknowledgement
    superseded the live row, a student pressing Start in a second tab would
    stamp `revoked_at` on the very row their RUNNING interview is pinned to,
    and the heartbeat would close that interview 4014 "Consent withdrawn" — a
    sentence they did not earn. A supersede must happen only when the policy has
    actually changed.
    """
    from app.config import settings

    body = {"version": settings.interview_consent_version}
    first = api.post("/api/interview/consent", json=body, headers=world.as_student)
    assert first.status_code == 201, first.text
    second = api.post("/api/interview/consent", json=body, headers=world.as_student)
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]

    live = _live_consents(world.student_user_id)
    assert [row.id for row in live] == [first.json()["id"]]


@requires_db
def test_a_changed_policy_supersedes_the_row_instead_of_editing_it(api, world):
    """A grant is a row and a row is never edited: `interview_sessions.
    consent_id` pins the grant that was live when an interview opened, and
    mutating it would rewrite the answer for every interview pointing at it.

    What triggers the supersede changed with B6.1 — it is the COLLEGE changing
    the policy, not the student changing their mind — and the property under it
    did not."""
    from app.config import settings

    from app.interview_policy import spine_of_student
    from app.models.institution import College, Department
    from app.models.interview_policy import InterviewPolicy

    body = {"version": settings.interview_consent_version}
    first = api.post("/api/interview/consent", json=body, headers=world.as_student)
    assert first.status_code == 201, first.text
    assert first.json()["scope_store_transcript"] is True

    # `world`'s student is a throwaway with no batch, so it hangs under no
    # college and resolves the deployment defaults. Giving it a department is
    # what puts a college above it — and it goes on `students.department_id`
    # rather than through a cohort ON PURPOSE: that is the pointer an UNSEATED
    # student has, and reading only the cohort route is the bug
    # `ancestry_of_student` was fixed for.
    college_id = f"col-{uuid.uuid4().hex[:10]}"
    department_id = f"dep-{uuid.uuid4().hex[:10]}"
    with SessionLocal() as db:
        db.add(College(id=college_id, name="Policy Test College", code=college_id[:12]))
        db.add(
            Department(
                id=department_id,
                college_id=college_id,
                name="Policy Test Dept",
                code=department_id[:12],
            )
        )
        db.flush()
        db.get(Student, world.student_id).department_id = department_id
        db.add(
            InterviewPolicy(
                college_id=college_id,
                course_id=None,
                store_transcript=False,
                store_audio=False,
            )
        )
        db.commit()
    with SessionLocal() as db:
        assert spine_of_student(db, world.student_id)[0] == college_id
    try:
        second = api.post(
            "/api/interview/consent", json=body, headers=world.as_student
        )
        assert second.status_code == 201, second.text
        assert second.json()["id"] != first.json()["id"]
        assert second.json()["scope_store_transcript"] is False

        live = _live_consents(world.student_user_id)
        assert [row.id for row in live] == [second.json()["id"]]
        with SessionLocal() as db:
            superseded = db.get(InterviewConsent, first.json()["id"])
            assert superseded is not None and superseded.revoked_at is not None
            # The superseded row still says exactly what the student was told at
            # the time, which is the whole point of keeping it.
            assert superseded.scope_store_transcript is True
    finally:
        with SessionLocal() as db:
            db.get(Student, world.student_id).department_id = None
            db.execute(
                delete(InterviewPolicy).where(
                    InterviewPolicy.college_id == college_id
                )
            )
            db.flush()
            db.execute(delete(Department).where(Department.id == department_id))
            db.execute(delete(College).where(College.id == college_id))
            db.commit()


@requires_db
def test_consent_refuses_a_version_this_server_does_not_know(api, world):
    """A stale cached SPA must not be able to grant against copy the student
    never saw — the one job a version string has."""
    r = api.post(
        "/api/interview/consent",
        json={
            "version": "1999-01",
            "scope_live_ai": True,
            "scope_store_transcript": True,
            "scope_store_audio": False,
        },
        headers=world.as_student,
    )
    assert r.status_code == 422, r.text
    assert _live_consents(world.student_user_id) == []


@requires_db
def test_a_stale_client_cannot_choose_its_own_scopes(api, world):
    """B6.1. The scopes are the college's, so a bundle cached from before this
    release posts its three booleans and gets the POLICY's.

    Not refused — a 422 would take the feature away from every tab open across
    the deploy — and not obeyed either: honouring a stale client's
    `scope_store_audio: true` over a college that has turned recording off would
    record a student the college said not to record.
    """
    from app.config import settings

    r = api.post(
        "/api/interview/consent",
        json={
            "version": settings.interview_consent_version,
            "scope_live_ai": False,
            "scope_store_transcript": False,
            "scope_store_audio": True,
        },
        headers=world.as_student,
    )
    assert r.status_code == 201, r.text
    assert r.json()["scope_store_audio"] is False
    assert r.json()["scope_store_transcript"] is True
    assert r.json()["scope_live_ai"] is True


@requires_db
def test_only_a_student_can_grant_consent(api, world):
    from app.config import settings

    r = api.post(
        "/api/interview/consent",
        json={
            "version": settings.interview_consent_version,
            "scope_live_ai": True,
            "scope_store_transcript": True,
            "scope_store_audio": False,
        },
        headers=world.as_director,
    )
    assert r.status_code == 403, r.text


@requires_db
def test_the_withdrawal_route_is_gone_and_an_old_version_row_is_left_alone(
    api, world
):
    """This test used to prove that "I withdraw" was about the PERSON and not
    about a version string: `DELETE /consent` revoked every live grant of every
    version. The route is gone (B6.1 — what it withdrew is now the college's
    decision), so what has to be pinned is the other half: asking does nothing,
    to any version, and the rows are untouched.
    """
    with SessionLocal() as db:
        db.add(
            InterviewConsent(
                user_id=world.student_user_id,
                version="1999-01",
                scope_live_ai=True,
                scope_store_transcript=True,
                scope_store_audio=False,
            )
        )
        db.commit()
    assert len(_live_consents(world.student_user_id)) == 1

    r = api.delete("/api/interview/consent", headers=world.as_student)
    assert r.status_code == 405, r.text
    assert len(_live_consents(world.student_user_id)) == 1


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_the_routers_are_mounted_at_the_documented_paths():
    """If `app/main.py` includes these routers, it must do so with NO extra
    prefix.

    Both routers declare their full `/api/...` path (the shape
    app/routers/interview.py uses), while the domain routers next to them in
    main.py declare `/mentor` and are mounted with `prefix="/api"`. Copying that
    line for `staff_router` serves it at `/api/api/mentor/...`: every request
    404s, nothing raises, and the only symptom is a mentor screen that is
    permanently empty.

    Conditional on purpose. `app/main.py` belongs to another step of this
    rollout, so before it lands there is nothing to assert and this must not be
    the test that fails for a reason outside its own subject. The moment ONE of
    these paths appears, all of them are required — which is exactly the state a
    mis-prefixed include produces.

    ASK THE OPENAPI DOCUMENT, NOT `app.routes`. On this FastAPI version
    `include_router` does not flatten the sub-routes into `app.routes`; it
    appends one `fastapi.routing._IncludedRouter` per include, and that object
    carries no `.path` at all. A set built from `app.routes` therefore comes back
    as `{""}` plus the four doc routes, `expected & mounted` is empty, and this
    test skips itself with a message blaming another agent's file while the
    endpoints are being served perfectly. It did exactly that on its first run.
    `app.openapi()["paths"]` is the version-independent answer to "what does this
    app actually serve", which is the question being asked.
    """
    from app.main import app
    from app.routers import interview_records

    expected = {
        route.path
        for router in (
            interview_records.student_router,
            interview_records.staff_router,
        )
        for route in router.routes
    }
    served = set(app.openapi()["paths"])
    if not (expected & served):
        pytest.skip(
            "app/main.py does not include the interview_records routers yet "
            "(step 7 of the rollout owns that file). Nothing to check."
        )
    assert expected <= served, sorted(expected - served)
    assert not any(path.startswith("/api/api/") for path in served)


# --------------------------------------------------------------------------- #
# The records grid: GET /api/mentor/interviews and the bulk zip
# --------------------------------------------------------------------------- #

@requires_db
def test_staff_are_told_why_an_interview_has_no_recording(api, world):
    """`audio_skipped_reason` rides every staff read of a record (2026-09-17).

    A row whose `audio_recorded` is false used to say "No audio" and nothing
    else, and the office read a policy that was never ticked as a broken
    feature. The word the finalizer wrote is served on the per-student detail,
    the per-student list and the grid, and it is null -- never guessed -- on a
    row written before the column existed.
    """
    from app.interview_audio import SKIP_POLICY_OFF

    with SessionLocal() as db:
        db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == world.interview_id)
            .values(audio_skipped_reason=SKIP_POLICY_OFF)
        )
        db.commit()
    base, detail, _transcript, _report = _staff_paths(world)
    r = api.get(detail, headers=world.as_director)
    assert r.status_code == 200, r.text
    assert r.json()["audio_recorded"] is False
    assert r.json()["audio_skipped_reason"] == SKIP_POLICY_OFF
    # The runbook pair rides the same payload, so "turns emitted, turns saved"
    # is readable from the screen and not only from a database client.
    assert (r.json()["turns_emitted"], r.json()["turns_persisted"]) == (11, 11)
    listed = {row["id"]: row for row in api.get(base, headers=world.as_director).json()}
    assert listed[world.interview_id]["audio_skipped_reason"] == SKIP_POLICY_OFF
    grid = {row["session_id"]: row for row in api.get("/api/mentor/interviews", headers=world.as_director).json()}
    assert grid[world.interview_id]["audio_skipped_reason"] == SKIP_POLICY_OFF
    # The other interview predates the column, as far as the row knows.
    assert grid[world.other_interview_id]["audio_skipped_reason"] is None


@requires_db
def test_the_records_grid_applies_rule_2_in_sql(api, world):
    """Every interview WITH the student named — and scoped exactly like the
    mentees list. A director sees both groups' interviews; a mentor in group A
    sees only A's; a MENTOR WITH NO GROUP SEES NOBODY, never the whole
    programme; a student is refused outright. The narrowing is SQL, so an
    out-of-group row never leaves the database."""
    r = api.get("/api/mentor/interviews", headers=world.as_director)
    assert r.status_code == 200, r.text
    ids = {row["session_id"] for row in r.json()}
    assert {world.interview_id, world.other_interview_id} <= ids
    row = next(x for x in r.json() if x["session_id"] == world.interview_id)
    assert row["student_id"] == world.student_id
    assert row["student_name"], "the grid must NAME the student"
    assert "started_at" in row and "audio_recorded" in row

    r = api.get("/api/mentor/interviews", headers=world.as_mentor_in_group)
    assert r.status_code == 200, r.text
    got = {row["session_id"] for row in r.json()}
    assert world.interview_id in got
    assert world.other_interview_id not in got, "group B's interview leaked to a group-A mentor"

    r = api.get("/api/mentor/interviews", headers=world.as_mentor_no_group)
    assert r.status_code == 200, r.text
    assert r.json() == [], "a mentor with no group must see NOBODY"

    r = api.get("/api/mentor/interviews", headers=world.as_student)
    assert r.status_code in (401, 403), r.text


@requires_db
def test_a_mentor_granted_interview_audio_gets_past_the_capability_gate(api, world):
    """The Main Admin can hand `admin.interview_audio` to a faculty member
    (2026-09-17 made the screen reachable; the key always was grantable).
    With the grant, a mentor IN THE STUDENT'S GROUP is no longer refused by
    the capability: the answer becomes rule 2's and the file's — here 404 "no
    recording", because the world's interview has none — never 403. The
    group gate is untouched: the same grant does nothing for a mentor whose
    group the student is not in."""
    from app.models.governance import CapabilityGrant, SubjectKind

    audio = f"/api/mentor/students/{world.student_id}/interviews/{world.interview_id}/audio"
    assert api.get(audio, headers=world.as_mentor_in_group).status_code == 403
    with SessionLocal() as db:
        rows = [
            CapabilityGrant(
                capability="admin.interview_audio",
                subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                reason="the interview-access tests grant the audio key programme-wide",
            )
            for user_id in (world.mentor_a_user_id, world.mentor_b_user_id)
        ]
        db.add_all(rows)
        db.commit()
        grant_ids = [row.id for row in rows]
    try:
        r = api.get(audio, headers=world.as_mentor_in_group)
        assert r.status_code == 404, r.text
        assert "No recording" in r.text
        r = api.get(audio, headers=world.as_mentor_other_group)
        assert r.status_code == 404, r.text
        assert "No recording" not in r.text, "rule 2 still fences the other group"
    finally:
        with SessionLocal() as db:
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(grant_ids)))
            db.commit()


@requires_db
def test_the_bulk_zip_is_gated_like_a_single_recording(api, world):
    """A zip of many students' voices is not a lesser act than one recording,
    so it sits behind the same `admin.interview_audio` capability.

    The subject is a MENTOR WHO HOLDS THIS STUDENT — every other gate on this
    route passes for them, so the 403 can only be the audio capability. It used
    to be a DIRECTOR, the role whose baseline excluded exactly this one
    capability; DIRECTOR no longer exists (tests/test_no_director_privilege.py)
    and ADMIN carries the capability by baseline, so a mentor is now the only
    account that demonstrates the asymmetry interview_records.py defends.
    """
    r = api.post(
        "/api/mentor/interviews/audio.zip",
        headers=world.as_mentor_in_group,
        json={"session_ids": [world.interview_id]},
    )
    assert r.status_code == 403, r.text
    assert "Interview audio" in r.text
