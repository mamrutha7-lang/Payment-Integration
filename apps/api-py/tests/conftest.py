"""Shared fixtures.

The pure-logic tests (egress gate, rule engine, resume PDF) need no database and
always run. The integration tests hit the seeded dev DB (`reep_py`) through a
TestClient; they are skipped with `@requires_db` when Postgres is not reachable,
so the suite still passes on a developer machine without Docker up.

That convenience is a LIE IN CI. Almost every test that covers conversations,
voice, retention and RBAC is @requires_db, so a pipeline without Postgres prints
a green "N passed" having verified essentially nothing about the product. Set
REEP_REQUIRE_DB=1 (CI does) and an unreachable database becomes a hard collection
error instead of a silent skip.
"""

import os
import types
import uuid

import socket

import pytest
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.engine.url import make_url

from app.db import SessionLocal
from app.models.conversation import Conversation
from app.models.governance import CapabilityGrant
from app.models.mentor_assignment import MentorAssignment
from app.models.user import LoginDay, Mentor, Role, Student, User
from app.security import hash_password


# How long the probe below may spend deciding Postgres is not there. Small on
# purpose: this runs at import, before pytest has printed a single character, so
# every second of it is a second the developer stares at a blank terminal.
_DB_PROBE_TIMEOUT_S = 2


def _db_reachable() -> bool:
    """Is Postgres there? Answered in bounded time, or not at all.

    THE TCP PRE-CHECK IS THE POINT. `SessionLocal()` inherits psycopg's default
    connect_timeout of 0, which means "wait forever" -- and a host that is
    unreachable rather than actively refusing (Docker Desktop stopped, a paused
    VM, a laptop that changed networks) never sends the RST that would make the
    connect fail. The probe then blocks in `psycopg.waiting.wait_conn` inside a
    conftest import, so pytest hangs BEFORE its header: no test names, no
    progress, no traceback, no clue. Observed here as a suite that ran in 35 s
    on Monday and had to be killed at 600 s on Tuesday, with Docker being the
    only thing that changed.

    A socket with an explicit timeout cannot hang, so the failure mode becomes a
    two-second pause and the honest "DB not reachable, DB-backed tests skipped"
    that the rest of this module already knows how to report.
    """
    url = make_url(str(SessionLocal.kw["bind"].url))
    host, port = url.host or "127.0.0.1", url.port or 5432
    try:
        with socket.create_connection((host, port), timeout=_DB_PROBE_TIMEOUT_S):
            pass
    except OSError:
        return False
    # The port answered; now confirm it is really our database and not, say, a
    # tunnel or another service that happens to hold the port.
    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
REQUIRE_DB = os.getenv("REEP_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes"}

if REQUIRE_DB and not DB_UP:
    raise pytest.UsageError(
        "REEP_REQUIRE_DB is set but Postgres is not reachable. The DB-backed "
        "tests (conversations, voice, retention, RBAC) would have been SKIPPED "
        "and the suite would have reported success without exercising them. "
        "Start Postgres (docker compose up -d) or unset REEP_REQUIRE_DB."
    )

#: The password every `make_user` account is created with. Exported because a
#: test that changes a user's ROWS after the fixture logged them in (adding a
#: Mentor row, say) must re-authenticate to pick up the new session claims —
#: the cookie is a signed snapshot, not a live read.
TEST_PASSWORD = "voicepass123"

# Decorator for tests that require the seeded Postgres dev DB.
requires_db = pytest.mark.skipif(not DB_UP, reason="Postgres reep_py not reachable")


#: The smallest bytes app/document_store recognises as a PDF and as a PNG.
#: Every fixture and helper that posts `POST /api/register` sends both,
#: because since 2026-09-22 the endpoint is one multipart request that
#: REFUSES an application without its CV and its photo — that is the fix for
#: the queue filling with applications that had neither.
APPLICATION_CV = b"%PDF-1.4\n% a cv\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
APPLICATION_PHOTO = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"\x00" * 64


def application_files(cv: bytes = APPLICATION_CV, photo: bytes = APPLICATION_PHOTO) -> dict:
    """The `files=` argument of a multipart `POST /api/register`."""
    return {
        "cv": ("cv.pdf", cv, "application/pdf"),
        "photo": ("me.png", photo, "image/png"),
    }


@pytest.fixture
def tmp_document_store(tmp_path, monkeypatch):
    """Point app/document_store at a per-test directory.

    REQUESTED BY every fixture that submits an application (test_passwords'
    `application`, test_registration_hierarchy's `applicant`) and by the tests
    that post one inline, so the two files a submission now carries land in a
    directory pytest removes rather than in the developer's own store. The
    modules that already patch `_store_dir` themselves keep doing so; both
    point at the same `tmp_path`.
    """
    from app import document_store

    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _fresh_per_process_state():
    """Reset the per-process LLM rate limiter and leaderboard cache per test.

    Both are process-lifetime state by design (see app/ratelimit.py and the
    leaderboard cache in app/routers/student.py), and the suite drives one
    seeded student through dozens of /api/agent/chat calls in a single process
    — a limiter that remembered them across tests would fail the suite for
    being thorough, which is how guards get deleted. Reset BEFORE each test so
    every test starts from the documented cold state; nothing is reset after,
    so a test may still assert on the warm state it built itself.
    """
    from app import ratelimit
    from app.routers import student as student_router

    ratelimit.reset()
    with student_router._leaderboard_cache_lock:
        student_router._leaderboard_cache.clear()
    yield


@pytest.fixture
def login(client):
    """Return a helper that logs in and yields the auth-cookie header dict."""

    def _login(email: str, password: str) -> dict:
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Cookie": r.headers.get("set-cookie", "")}

    return _login


@pytest.fixture
def make_user(client):
    """Factory that creates a throwaway User (STUDENT gets a Student row too),
    logs it in, and returns email/user_id/headers. All rows are torn down."""
    created: list[str] = []

    def _make(label: str, role: Role = Role.STUDENT):
        email = f"voicetest-{label}-{uuid.uuid4().hex[:8]}@bgscet.ac.in"
        password = TEST_PASSWORD
        with SessionLocal() as db:
            user = User(
                email=email,
                name=f"Voice Test {label}",
                role=role,
                password_hash=hash_password(password),
            )
            db.add(user)
            db.flush()
            if role == Role.STUDENT:
                db.add(Student(user_id=user.id))
            db.commit()
            uid = user.id
        created.append(uid)

        r = client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        headers = {"Cookie": r.headers.get("set-cookie", "")}
        client.cookies.clear()
        return types.SimpleNamespace(email=email, user_id=uid, headers=headers)

    yield _make

    with SessionLocal() as db:
        for uid in created:
            db.execute(delete(Conversation).where(Conversation.owner_user_id == uid))
            db.execute(delete(LoginDay).where(LoginDay.user_id == uid))
            # B9.1. `mentor_assignments` names a student AND two staff accounts,
            # and its `student_id`/`mentor_id` carry NO `ondelete` on purpose —
            # the database REFUSES to delete a student or a mentor group out
            # from under a spell, which is exactly what this teardown was doing
            # silently before the table existed. Children first, as both purge
            # modules do it: the assignment rows, then the group, then the
            # student, then the account.
            student_ids = [
                sid for (sid,) in db.execute(
                    select(Student.id).where(Student.user_id == uid)
                ).all()
            ]
            group_ids = [
                gid for (gid,) in db.execute(
                    select(Mentor.id).where(Mentor.user_id == uid)
                ).all()
            ]
            if student_ids or group_ids:
                db.execute(
                    delete(MentorAssignment).where(
                        or_(
                            MentorAssignment.student_id.in_(student_ids or [""]),
                            MentorAssignment.mentor_id.in_(group_ids or [""]),
                        )
                    )
                )
            # A handover grant is minted by a RELEASE and keyed on the faculty
            # account, so it outlives the account the same way any grant does.
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id == uid))
            # UN-SEAT BEFORE DELETING THE GROUP. `students.mentor_id` has no
            # `ondelete`, so a group this fixture made cannot go while ANY
            # student still points at it — including one created by a later
            # `make_user` call in the same test, which this loop has not reached
            # yet. Nulling first makes the order of `created` irrelevant.
            if group_ids:
                db.execute(
                    update(Student).where(Student.mentor_id.in_(group_ids)).values(mentor_id=None)
                )
                db.flush()
            db.execute(delete(Mentor).where(Mentor.user_id == uid))
            db.execute(delete(Student).where(Student.user_id == uid))
            db.execute(delete(User).where(User.id == uid))
        db.commit()


@pytest.fixture
def granted(client):
    """Give an account a capability its ROLE BASELINE does not carry, and clean up.

        cap = granted(admin, "mentor.verifications")

    THE MAIN ADMIN IS NOT A FACULTY MEMBER (2026-09-10). Four capabilities
    — `mentor.mentees`, `mentor.notebook`, `mentor.verifications` and
    `mentor.upskilling` — are a mentor's own instruments, and `_FACULTY_ONLY` in
    app/governance.py keeps them OUT of the office account's baseline: it has no
    mentees, no private notebook, nobody's evidence to verify and no upskilling
    shelf, so those screens would render empty for it. When the Main Admin does
    have to look — a student's evidence is stuck and nobody else will — the way
    in is a GRANT it makes in Governance, to itself, carrying a reason, on the
    audit trail. A test that needs that access takes the same path.

    It goes through the real endpoint rather than an INSERT, so the reason floor,
    the audit write and the capability catalogue are all exercised on the way.

    The row is removed afterwards and that is not tidiness: a grant is keyed on a
    user id and OUTLIVES the `make_user` account that named it, so a leaked row
    would silently widen whatever ran next.
    """
    made: list[str] = []

    def _grant(account, capability: str, reason: str | None = None) -> str:
        r = client.post(
            "/api/admin/governance/grants",
            headers=account.headers,
            json={
                "capability": capability,
                "user_ids": [account.user_id],
                "reason": reason or f"Test suite needs {capability} for this scenario.",
            },
        )
        assert r.status_code == 201, r.text
        grant_id = r.json()[0]["id"]
        made.append(grant_id)
        return grant_id

    yield _grant

    if made:
        with SessionLocal() as db:
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
            db.commit()
