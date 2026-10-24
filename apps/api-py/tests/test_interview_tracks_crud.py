"""B5.1-B5.4 — the Specialization Matrix as rows the office edits, and the
question bank hanging off it.

THE FOUR TESTS AT THE TOP NEED NO DATABASE, and they are the ones that catch the
mistakes this area is actually prone to:

  * the handshake lookup going back onto the event loop. `routers/interview.py`
    runs every live interview's audio on one coroutine; before B5.1 the track
    lookup was an in-memory dict read and the bank read was explicitly pushed
    onto a worker thread BECAUSE it was a SELECT. Turning the track into a row
    and leaving the lookup where it was would put a database round trip back on
    that loop, and the symptom is other people's interviews stuttering — not
    anything wrong with the one that caused it. Nothing else in the suite would
    notice.
  * a persona stored as a sentence. `build_instructions` embeds it as "you are
    {persona}", so "You are a sharp CFO." composes into "you are You are a sharp
    CFO." — the model is handed broken grammar and simply interviews slightly
    worse, on every interview, for everyone on that track, with nothing on any
    screen saying so.
  * a voice Nova will not accept, which is a ValidationException DURING the
    handshake: an interview that never starts.
  * a compile step that drops a field. `syllabus` is the one 04-backend-changes'
    column list omits and only `dm` has one, so losing it downgrades the Digital
    Marketing interview to a generic CMO chat that nothing reports.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import uuid

import pytest

from conftest import requires_db

from app import interview_tracks
from app.db import SessionLocal
from app.interview_matrix import KNOWN_NOVA_VOICES, SPECIALIZATIONS
from app.interview_tracks import (
    RESERVED_TRACK_CODES,
    persona_refusals,
    persona_warnings,
    track_to_specialization,
)
from app.models.interview_track import InterviewTrack
from app.models.user import Role

API = "/api/admin/interview-questions"
TRACKS = f"{API}/tracks"


# ---------------------------------------------------------------------------
# No database — the loop, the persona, the voice, the compile step
# ---------------------------------------------------------------------------


def test_the_handshake_resolves_the_track_off_the_event_loop():
    """B5.1's one performance-shaped rule, pinned by reading the call graph.

    `app/routers/interview.py` must reach the catalogue through
    `asyncio.to_thread`, and must NOT call the in-memory `get_specialization`
    any more — that function cannot see a track row, so a call to it is a
    handshake that quietly ignores everything the office configured.

    Asserted against the SOURCE rather than by timing, because the failure has
    no local symptom: a synchronous SELECT here is fast on a laptop with four
    rows and stalls a worker carrying twelve live interviews in production.
    """
    source = pathlib.Path(
        inspect.getsourcefile(__import__("app.routers.interview", fromlist=["x"]))
    ).read_text()
    tree = ast.parse(source)

    to_thread_targets: set[str] = set()
    direct_calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "to_thread" and node.args:
            first = node.args[0]
            target = first.attr if isinstance(first, ast.Attribute) else getattr(first, "id", None)
            if target:
                to_thread_targets.add(target)
        elif name:
            direct_calls.add(name)

    assert "resolve_specialization" in to_thread_targets, (
        "the track lookup must run on a worker thread — it is a SELECT now, and "
        "app/routers/interview.py shares one event loop with every live "
        "interview's audio"
    )
    assert "resolve_specialization" not in direct_calls, (
        "resolve_specialization opens a database session; calling it directly "
        "on the handshake coroutine is the exact regression asyncio.to_thread "
        "was there to prevent"
    )
    assert "get_specialization" not in direct_calls, (
        "get_specialization reads the frozen dict and cannot see a track row, so "
        "a call to it on the handshake path silently ignores the catalogue"
    )


def test_the_shipped_personas_are_all_accepted():
    """The heuristic must not refuse the four rows the product ships with.

    Written first and deliberately: a persona check that rejects
    "an empathetic yet compliant Chief Human Resources Officer (CHRO)" is a
    field the office fights instead of uses, and the fix somebody reaches for is
    to delete the check.
    """
    for spec in SPECIALIZATIONS.values():
        assert persona_refusals(spec.persona) == [], spec.key


@pytest.mark.parametrize(
    "persona",
    [
        "You are an empathetic Chief Human Resources Officer",
        "a sharp, risk-conscious Managing Director / CFO.",
        "Act as a growth-oriented CMO",
        "Pretend to be a Director of Analytics!",
        "",
    ],
)
def test_a_persona_that_reads_as_a_sentence_is_refused(persona):
    """`build_instructions` writes "you are {persona}". A sentence there composes
    into "you are You are an empathetic CHRO" or "you are a sharp CFO.." — and
    nothing downstream can catch either: the model is simply handed worse
    grammar and interviews slightly worse forever."""
    assert persona_refusals(persona), persona


def test_a_capitalised_persona_is_a_warning_and_not_a_refusal():
    """The line between the two matters. A leading capital is merely unlike the
    shipped four; a trailing full stop is broken grammar in the prompt. Refusing
    on the first would make the field unusable for a college whose persona
    legitimately reads "Managing Director (India & SEA)"."""
    persona = "An empathetic Chief Human Resources Officer"
    assert persona_refusals(persona) == []
    assert persona_warnings(persona), "…but say so on the form"


def test_the_compile_step_carries_every_field_including_the_syllabus():
    """A row → the frozen dataclass the engine takes, field for field.

    `syllabus` is the field 04-backend-changes.md's column list omits and the
    one only `dm` carries, so a compile step that drops it downgrades the
    Digital Marketing interview to the generic one with nothing on any screen to
    say so. `nova_voice` is the field whose loss is fatal rather than cosmetic.
    """
    row = InterviewTrack(
        code="dm",
        label="Digital Marketing (DM)",
        persona="a growth-oriented, data-driven Chief Marketing Officer (CMO)",
        frameworks=["CAC/LTV ratios", "ROAS", "SEO/SEM strategies", "A/B testing"],
        sample_question="Our CAC has increased by 40% on Meta ads this quarter.",
        nova_voice="tiffany",
        syllabus=["Module 1 - Foundations", "Module 5 - SEO"],
    )
    spec = track_to_specialization(row, ("[probing] What is your ROAS?",))
    assert spec.key == "dm"
    assert spec.label == "Digital Marketing (DM)"
    assert spec.persona == row.persona
    assert spec.frameworks == tuple(row.frameworks)
    assert spec.nova_voice == "tiffany" and spec.nova_voice in KNOWN_NOVA_VOICES
    assert spec.syllabus == ("Module 1 - Foundations", "Module 5 - SEO"), (
        "a track row without its syllabus runs a different interview"
    )
    assert spec.question_bank == ("[probing] What is your ROAS?",)
    # An ORM object must never reach the engine: what crosses is the frozen
    # dataclass, which is what makes B5.1 a data change and not an engine change.
    assert type(spec).__name__ == "Specialization"


def test_a_generic_interview_reads_no_row_at_all():
    """No `?specialization=` is the generic interview that predates the matrix,
    and it must cost exactly what it did before this module existed — no
    session, no query. Asserted by calling it with the database unreachable:
    `SessionLocal()` would raise long before this returns None."""
    assert interview_tracks.resolve_specialization(None) is None
    assert interview_tracks.resolve_specialization("   ") is None


def test_the_reserved_codes_cannot_be_taken():
    assert "general" in RESERVED_TRACK_CODES, (
        "an interview with no ?specialization= is the generic one; a track "
        "named 'general' would be indistinguishable from it on the records grid"
    )


def test_the_scope_clause_shows_programme_wide_tracks_to_a_narrowed_reach():
    """B1.4's projection onto `interview_tracks`, compiled without a database.

    A programme-wide track is what every student in every college is actually
    sitting, so a college admin must SEE it; `require_capability(..., target=…)`
    is what stops them EDITING it. A clause that hid it would make the screen
    say the office has configured no interviewer at all.
    """
    from sqlalchemy import true as sa_true

    from app.policies import Reach
    from app.scope_views import interview_track_scope_clause

    assert str(interview_track_scope_clause(Reach(everything=True))) == str(sa_true())
    narrowed = str(
        interview_track_scope_clause(Reach(everything=False, colleges=frozenset({"c1"})))
    )
    assert "interview_tracks.college_id IS NULL" in narrowed
    assert "interview_tracks.college_id IN" in narrowed


# ---------------------------------------------------------------------------
# With Postgres — the CRUD, the scope, and the wire
# ---------------------------------------------------------------------------


def _payload(code: str) -> dict:
    return {
        "code": code,
        "label": f"Operations ({code.upper()})",
        "persona": "a pragmatic Head of Operations",
        "frameworks": ["lean", "theory of constraints", "SLA design", "S&OP"],
        "sample_question": "Walk me through how you would cut a 14-day lead time in half.",
        "nova_voice": "amy",
        "syllabus": ["Module 1 - Process mapping"],
    }


@pytest.fixture
def _clean_tracks():
    made: list[str] = []
    yield made
    if made:
        with SessionLocal() as db:
            for code in made:
                for row in db.query(InterviewTrack).filter(InterviewTrack.code == code):
                    db.delete(row)
            db.commit()


@requires_db
def test_the_track_crud_is_the_bank_capability_and_a_student_is_refused(
    client, make_user, _clean_tracks
):
    admin = make_user("tk-adm", Role.ADMIN)
    student = make_user("tk-stu")
    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)

    assert client.get(TRACKS, headers=student.headers).status_code == 403
    assert client.post(TRACKS, headers=student.headers, json=_payload(code)).status_code == 403

    r = client.post(TRACKS, headers=admin.headers, json=_payload(code))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["track"]["code"] == code and body["track"]["source"] == "table"
    assert body["track"]["editable"] is True

    listed = client.get(TRACKS, headers=admin.headers)
    assert listed.status_code == 200
    codes = {t["code"] for t in listed.json()}
    assert code in codes
    assert set(SPECIALIZATIONS) <= codes, (
        "the four shipped tracks must still be listed — from the table if the "
        "migration seeded them, from the constant if it has not run yet"
    )
    assert listed.headers.get("X-Reep-Scope") == "programme"


@requires_db
def test_a_voice_nova_does_not_know_is_refused_on_the_form(client, make_user, _clean_tracks):
    """Not at the handshake, where it is a dead socket with nothing naming the
    cause, but here, on the field the admin typed it into."""
    admin = make_user("tk-voice", Role.ADMIN)
    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)
    payload = _payload(code) | {"nova_voice": "coral"}  # an OpenAI voice, not a Nova one
    r = client.post(TRACKS, headers=admin.headers, json=payload)
    assert r.status_code == 422, r.text
    assert "handshake" in r.text


@requires_db
def test_a_persona_that_is_a_sentence_is_refused_on_the_form(client, make_user, _clean_tracks):
    admin = make_user("tk-pers", Role.ADMIN)
    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)
    r = client.post(
        TRACKS, headers=admin.headers, json=_payload(code) | {"persona": "You are a CHRO."}
    )
    assert r.status_code == 422 and "NOUN PHRASE" in r.text


@requires_db
def test_two_tracks_cannot_share_a_code_at_the_same_reach(client, make_user, _clean_tracks):
    """`?specialization=` carries the code, so two rows sharing one is a coin
    toss between two different interviewers — which is why the table carries a
    partial unique index as well as the constraint (Postgres treats NULLs as
    distinct). The endpoint answers it as a sentence rather than a 500."""
    admin = make_user("tk-dup", Role.ADMIN)
    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)
    assert client.post(TRACKS, headers=admin.headers, json=_payload(code)).status_code == 201
    again = client.post(TRACKS, headers=admin.headers, json=_payload(code))
    assert again.status_code == 409 and "coin toss" in again.text


@requires_db
def test_a_question_written_on_a_new_track_carries_its_track_id(client, make_user, _clean_tracks):
    """B5.2. The bank used to refuse any track that was not one of the four
    constants, which would have made a college's own track unusable on the one
    screen that exists to author its questions."""
    from sqlalchemy import delete, select

    from app.models.interview_bank import InterviewBankQuestion

    admin = make_user("tk-bank", Role.ADMIN)
    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)
    created = client.post(TRACKS, headers=admin.headers, json=_payload(code))
    assert created.status_code == 201, created.text
    track_id = created.json()["track"]["id"]

    r = client.post(
        API,
        headers=admin.headers,
        json={
            "track": code,
            "phase": "probing",
            "text": "How would you triage a queue that has doubled overnight?",
        },
    )
    assert r.status_code == 201, r.text
    question_id = r.json()["id"]
    try:
        with SessionLocal() as db:
            row = db.get(InterviewBankQuestion, question_id)
            assert row.track == code, "the CODE stays: it is what ?specialization= carries"
            assert row.track_id == track_id, "…and track_id is the join beside it"
        # And the wire: the engine is handed the row's persona and the question.
        spec = interview_tracks.resolve_specialization(code)
        assert spec is not None
        assert spec.persona == "a pragmatic Head of Operations"
        assert any("triage a queue" in line for line in spec.question_bank)
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(InterviewBankQuestion).where(InterviewBankQuestion.id == question_id)
            )
            db.commit()


@requires_db
def test_a_disabled_track_is_refused_at_the_handshake_and_not_silently_downgraded(
    client, make_user, _clean_tracks
):
    """Disabling must actually stop interviews. Falling back to the constant
    would make the switch a no-op for exactly the four tracks that have one."""
    admin = make_user("tk-off", Role.ADMIN)
    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)
    created = client.post(TRACKS, headers=admin.headers, json=_payload(code))
    track_id = created.json()["track"]["id"]
    assert interview_tracks.resolve_specialization(code) is not None

    r = client.patch(f"{TRACKS}/{track_id}", headers=admin.headers, json={"enabled": False})
    assert r.status_code == 200, r.text
    assert interview_tracks.resolve_specialization(code) is None


@requires_db
def test_a_track_an_interview_was_held_on_cannot_be_deleted(client, make_user, _clean_tracks):
    """`interview_sessions` files every interview under the CODE. Deleting a
    used track leaves those records labelled by a code nothing resolves, on the
    student's own history screen — so the endpoint says "switch it off" instead."""
    from sqlalchemy import delete

    from app.models.interview import InterviewSession
    from app.models.user import Student

    admin = make_user("tk-del", Role.ADMIN)
    owner = make_user("tk-del-stu")
    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)
    track_id = client.post(TRACKS, headers=admin.headers, json=_payload(code)).json()["track"]["id"]

    with SessionLocal() as db:
        student = db.query(Student).filter(Student.user_id == owner.user_id).one()
        db.add(
            InterviewSession(
                student_id=student.id,
                specialization=code,
                status="completed",
            )
        )
        db.commit()
    try:
        r = client.delete(f"{TRACKS}/{track_id}", headers=admin.headers)
        assert r.status_code == 409, r.text
        assert "Switch the track off instead" in r.text
    finally:
        with SessionLocal() as db:
            db.execute(delete(InterviewSession).where(InterviewSession.specialization == code))
            db.commit()
    assert client.delete(f"{TRACKS}/{track_id}", headers=admin.headers).status_code == 204


@requires_db
def test_a_college_scoped_holder_reads_the_programme_wide_tracks_and_cannot_edit_them(
    client, make_user, _clean_tracks
):
    """B1.2 on the catalogue, and the asymmetry is the design.

    A college admin SEES the shipped tracks — their students sit those
    interviews — and cannot change one, because a programme-wide track hangs on
    no rung of the spine and `reaches_target` says no scoped grant reaches an
    empty ancestry. What they can do is add their own college's row, which
    shadows it at the handshake.
    """
    from sqlalchemy import delete, select

    from app.models.governance import CapabilityGrant
    from app.models.institution import College

    admin = make_user("tk-scope-adm", Role.ADMIN)
    faculty = make_user("tk-scope-fac", Role.MENTOR)
    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)
    programme_wide = client.post(TRACKS, headers=admin.headers, json=_payload(code))
    assert programme_wide.status_code == 201, programme_wide.text
    track_id = programme_wide.json()["track"]["id"]

    with SessionLocal() as db:
        college_id = db.scalar(select(College.id).limit(1))
    if not college_id:
        pytest.skip("no college on this database to scope a grant to")

    grant = client.post(
        "/api/admin/governance/grants",
        headers=admin.headers,
        json={
            "capability": "admin.interview_questions",
            "user_ids": [faculty.user_id],
            "reason": "This faculty member authors their own college's interview.",
            "scope_level": "COLLEGE",
            "scope_id": college_id,
        },
    )
    assert grant.status_code == 201, grant.text
    grant_ids = [g["id"] for g in grant.json()]
    try:
        listed = client.get(TRACKS, headers=faculty.headers)
        assert listed.status_code == 200, listed.text
        rows = {t["code"]: t for t in listed.json()}
        assert code in rows, "a programme-wide track is one their students sit"
        assert rows[code]["editable"] is False, (
            "…and editing it would change the interview for every other college"
        )
        assert listed.headers.get("X-Reep-Scope") == "narrowed"

        refused = client.patch(
            f"{TRACKS}/{track_id}", headers=faculty.headers, json={"label": "Mine now"}
        )
        assert refused.status_code == 403, refused.text

        # …and the question bank refuses the same track for the same reason:
        # a question on a programme-wide track is asked of every college.
        blocked = client.post(
            API,
            headers=faculty.headers,
            json={"track": code, "phase": "probing", "text": "A question for everybody."},
        )
        assert blocked.status_code == 422, blocked.text
    finally:
        with SessionLocal() as db:
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(grant_ids)))
            db.commit()


@requires_db
def test_the_engine_reads_the_row_and_not_the_frozen_constant(client, make_user):
    """The point of B5.1, pinned end to end.

    Edit the persona of a SHIPPED track through the console and the next
    handshake composes the edited one. If this ever reverts to
    `interview_matrix.SPECIALIZATIONS`, every screen on the admin console keeps
    working and every interview keeps running — with the office's edits going
    nowhere, and nothing anywhere saying so.
    """
    from sqlalchemy import select

    admin = make_user("tk-live", Role.ADMIN)
    listed = client.get(TRACKS, headers=admin.headers)
    assert listed.status_code == 200, listed.text
    hr = next((t for t in listed.json() if t["code"] == "hr"), None)
    if hr is None or hr["source"] != "table":
        pytest.skip("this database has no seeded hr track row to edit")

    original = hr["persona"]
    edited = "a stern, audit-minded Chief People Officer"
    try:
        r = client.patch(
            f"{TRACKS}/{hr['id']}", headers=admin.headers, json={"persona": edited}
        )
        assert r.status_code == 200, r.text
        spec = interview_tracks.resolve_specialization("hr")
        assert spec is not None and spec.persona == edited
        assert spec.persona != SPECIALIZATIONS["hr"].persona
        # …and the constant is untouched: it is still the fallback for a
        # deployment whose table is empty.
        assert SPECIALIZATIONS["hr"].persona == original or original == edited
    finally:
        with SessionLocal() as db:
            row = db.scalar(
                select(InterviewTrack).where(InterviewTrack.id == hr["id"])
            )
            if row is not None:
                row.persona = original
                db.commit()


@requires_db
def test_a_student_filed_under_no_college_still_gets_an_interview(make_user):
    """An unfiled student — no batch, no department — is an ordinary state the
    console shows a list of. The lookup must still resolve a track for them, or
    the mock interview simply stops working for a cohort nobody has seated yet.
    """
    from sqlalchemy import select

    from app.models.user import Student

    owner = make_user("tk-unfiled")
    with SessionLocal() as db:
        student_id = db.scalar(select(Student.id).where(Student.user_id == owner.user_id))
    spec = interview_tracks.resolve_specialization("hr", student_id=student_id)
    assert spec is not None, (
        "a student hanging under no college must still reach the programme-wide "
        "track, or an unseated cohort loses the interviewer"
    )
    assert spec.key == "hr"


@requires_db
def test_one_colleges_bank_is_not_another_colleges(client, make_user, _clean_tracks):
    """Two colleges can each hold a track with the same CODE, and every question
    on both carries that code.

    So "the questions of track hr" is an incomplete question, and the incomplete
    answer is not merely a leak: `POST /reorder` renumbers every row it finds,
    so a college-A holder reordering their own bank would rewrite college B's
    working order — silently, on an interview they cannot see.
    """
    from sqlalchemy import delete, select

    from app.models.governance import CapabilityGrant
    from app.models.institution import College
    from app.models.interview_bank import InterviewBankQuestion

    admin = make_user("tk-two-adm", Role.ADMIN)
    faculty = make_user("tk-two-fac", Role.MENTOR)
    with SessionLocal() as db:
        existing = db.scalar(select(College.id).limit(1))
    if not existing:
        pytest.skip("this database has no college to scope a grant to")
    # The SECOND college is created here rather than found: how many colleges a
    # shared development database happens to hold changes between runs, and a
    # test that skips when it finds one is a test that stops proving anything
    # without anybody noticing.
    suffix = uuid.uuid4().hex[:6].upper()
    second = client.post(
        "/api/admin/colleges",
        headers=admin.headers,
        json={"code": f"TK{suffix}", "name": f"Track Scope Test College {suffix}"},
    )
    assert second.status_code == 201, second.text
    created_college = second.json()["id"]
    mine, theirs = existing, created_college

    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)
    made = [
        client.post(TRACKS, headers=admin.headers, json=_payload(code) | {"college_id": c})
        for c in (mine, theirs)
    ]
    assert [r.status_code for r in made] == [201, 201], [r.text for r in made]

    questions: list[str] = []
    grant_ids: list[str] = []
    try:
        for label in ("mine", "theirs"):
            r = client.post(
                API,
                headers=admin.headers,
                json={
                    "track": code,
                    "phase": "probing",
                    "text": f"A question that belongs to {label} only, truly.",
                },
            )
            assert r.status_code == 201, r.text
            questions.append(r.json()["id"])
        # The admin wrote both against the FIRST writable row, so re-stamp the
        # second onto the other college the way that college's own admin would.
        with SessionLocal() as db:
            row = db.get(InterviewBankQuestion, questions[1])
            other = db.scalars(
                select(InterviewTrack).where(
                    InterviewTrack.code == code, InterviewTrack.college_id == theirs
                )
            ).one()
            row.track_id, row.college_id = other.id, theirs
            db.commit()

        grant = client.post(
            "/api/admin/governance/grants",
            headers=admin.headers,
            json={
                "capability": "admin.interview_questions",
                "user_ids": [faculty.user_id],
                "reason": "This faculty member authors their own college's interview.",
                "scope_level": "COLLEGE",
                "scope_id": mine,
            },
        )
        assert grant.status_code == 201, grant.text
        grant_ids = [g["id"] for g in grant.json()]

        listed = client.get(f"{API}?track={code}", headers=faculty.headers)
        assert listed.status_code == 200, listed.text
        ids = {q["id"] for q in listed.json()}
        assert questions[0] in ids
        assert questions[1] not in ids, "…that one belongs to the other college"
    finally:
        with SessionLocal() as db:
            if grant_ids:
                db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(grant_ids)))
            if questions:
                db.execute(
                    delete(InterviewBankQuestion).where(InterviewBankQuestion.id.in_(questions))
                )
            db.commit()
        # The tracks go before the college: `interview_tracks.college_id` has no
        # ON DELETE, which is the spine's convention — the database refuses to
        # delete a rung that still has rows under it.
        with SessionLocal() as db:
            for row in db.scalars(
                select(InterviewTrack).where(InterviewTrack.code == code)
            ).all():
                db.delete(row)
            db.commit()
        with SessionLocal() as db:
            leftover = db.get(College, created_college)
            if leftover is not None:
                db.delete(leftover)
                db.commit()


@requires_db
def test_moving_a_track_onto_a_code_that_is_already_taken_is_refused(
    client, make_user, _clean_tracks
):
    """A PATCH can create the same collision a POST can, and it used to do it as
    an IntegrityError — a 500 on a form, for a rule the office could have been
    told in a sentence."""
    from sqlalchemy import select

    from app.models.institution import College

    admin = make_user("tk-move", Role.ADMIN)
    code = "ops" + uuid.uuid4().hex[:3]
    _clean_tracks.append(code)
    with SessionLocal() as db:
        college_id = db.scalar(select(College.id).limit(1))
    if not college_id:
        pytest.skip("this database has no college to pin a track to")

    programme_wide = client.post(TRACKS, headers=admin.headers, json=_payload(code))
    assert programme_wide.status_code == 201, programme_wide.text
    pinned = client.post(
        TRACKS, headers=admin.headers, json=_payload(code) | {"college_id": college_id}
    )
    assert pinned.status_code == 201, "one per college plus one programme-wide is legal"

    moved = client.patch(
        f"{TRACKS}/{pinned.json()['track']['id']}",
        headers=admin.headers,
        json={"college_id": None},
    )
    assert moved.status_code == 409, moved.text
    assert "coin toss" in moved.text
