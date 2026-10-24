"""Assistant V2 Phase A — conversation OWNERSHIP + lifecycle.

The security spine of the assistant: a conversation belongs to exactly one user
and is ALWAYS resolved from the authenticated session. The endpoints accept NO
conversation/session id, so cross-user access is impossible by construction —
these tests pin that contract end-to-end (via TestClient) and unit-test the
`assert_owner` guard in app/conversations.py directly.

The chat endpoint would otherwise call a live LLM; `stub_llm` monkeypatches the
router's `llm_config`/`complete_chat` so the flow is deterministic and offline.
Every row a test creates (users, students, conversations, messages, agent-runs)
is cleaned up, so the suite stays independent and re-runnable.
"""

import types
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from conftest import requires_db

from app import conversations as convo
from app.conversations import GREETING
from app.db import SessionLocal
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.user import LoginDay, Role, Student, User
from app.security import hash_password

STUB_REPLY = "STUB REPLY from the offline assistant."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def stub_llm(monkeypatch):
    """Make /chat deterministic + offline: a fake provider config and a canned
    reply, patched into the agent router's own namespace (where it is called)."""
    import app.routers.agent as agent

    monkeypatch.setattr(
        agent, "llm_config",
        lambda: types.SimpleNamespace(provider="stub", model="stub-model"),
    )
    monkeypatch.setattr(
        agent, "complete_chat", lambda messages, **kwargs: STUB_REPLY
    )
    return STUB_REPLY


@pytest.fixture
def make_student(client):
    """Factory that creates a throwaway STUDENT (User + Student), logs it in, and
    returns its email/password/user_id/headers. All rows it (or the endpoints it
    drives) create are torn down afterwards."""
    created: list[str] = []

    def _make(label: str):
        email = f"convtest-{label}-{uuid.uuid4().hex[:8]}@bgscet.ac.in"
        password = "convpass123"
        with SessionLocal() as db:
            user = User(
                email=email,
                name=f"Conv Test {label}",
                role=Role.STUDENT,
                password_hash=hash_password(password),
            )
            db.add(user)
            db.flush()
            db.add(Student(user_id=user.id))
            db.commit()
            uid = user.id
        created.append(uid)

        r = client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        headers = {"Cookie": r.headers.get("set-cookie", "")}
        # Drop the jar so ONLY the explicit per-request Cookie header decides the
        # caller — essential when two users are exercised in one test.
        client.cookies.clear()
        return types.SimpleNamespace(
            email=email, password=password, user_id=uid, headers=headers
        )

    yield _make

    with SessionLocal() as db:
        for uid in created:
            db.execute(delete(AgentRun).where(AgentRun.actor_id == uid))
            # Conversation.id has an ON DELETE CASCADE to messages, so this also
            # clears the turns.
            db.execute(delete(Conversation).where(Conversation.owner_user_id == uid))
            db.execute(delete(LoginDay).where(LoginDay.user_id == uid))
            db.execute(delete(Student).where(Student.user_id == uid))
            db.execute(delete(User).where(User.id == uid))
        db.commit()


# ---------------------------------------------------------------------------
# Contract: no client-chosen conversation/session id is accepted anywhere
# ---------------------------------------------------------------------------
def test_chat_body_has_only_message():
    """The write path accepts a message and nothing else — no conversation_id /
    session_id field exists to point at someone else's thread."""
    from app.routers.agent import ChatIn

    assert set(ChatIn.model_fields) == {"message"}


@requires_db
def test_history_and_delete_declare_no_id_parameters(client):
    """GET /history and DELETE /conversation take no path/query parameters: the
    caller is resolved from the session, and there is no id to name another."""
    spec = client.get("/openapi.json").json()["paths"]
    assert spec["/api/agent/history"]["get"].get("parameters", []) == []
    assert spec["/api/agent/conversation"]["delete"].get("parameters", []) == []


@requires_db
def test_two_users_get_different_conversation_ids(client, stub_llm, make_student):
    """Two different logged-in users each operate on their OWN server-owned
    conversation — the ids never collide."""
    a = make_student("a")
    b = make_student("b")

    ra = client.post("/api/agent/chat", headers=a.headers, json={"message": "hi from A"})
    rb = client.post("/api/agent/chat", headers=b.headers, json={"message": "hi from B"})
    assert ra.status_code == 200, ra.text
    assert rb.status_code == 200, rb.text

    conv_a = ra.json()["conversation_id"]
    conv_b = rb.json()["conversation_id"]
    assert conv_a and conv_b
    assert conv_a != conv_b


@requires_db
def test_stray_conversation_id_cannot_reach_another_users_thread(
    client, stub_llm, make_student
):
    """Even if a caller smuggles another user's conversation_id/session_id into
    the request, it is ignored: they still only ever touch their own thread."""
    a = make_student("owner")
    b = make_student("intruder")

    ra = client.post(
        "/api/agent/chat", headers=a.headers, json={"message": "secret from A"}
    )
    conv_a = ra.json()["conversation_id"]

    # B smuggles A's id in the body (extra fields are ignored by the model).
    rb = client.post(
        "/api/agent/chat",
        headers=b.headers,
        json={"message": "hi from B", "conversation_id": conv_a, "session_id": conv_a},
    )
    assert rb.status_code == 200, rb.text
    conv_b = rb.json()["conversation_id"]
    assert conv_b != conv_a

    # B tries the same via history query params — still their own empty-of-A thread.
    hist = client.get(
        "/api/agent/history",
        headers=b.headers,
        params={"conversation_id": conv_a, "session_id": conv_a},
    ).json()
    assert hist["conversation_id"] == conv_b
    contents = [t["content"] for t in hist["turns"]]
    assert "secret from A" not in contents


# ---------------------------------------------------------------------------
# Lifecycle: chat -> history -> delete -> fresh
# ---------------------------------------------------------------------------
@requires_db
def test_chat_then_history_returns_the_turn(client, stub_llm, make_student):
    s = make_student("hist")

    # Fresh caller: nothing open yet.
    empty = client.get("/api/agent/history", headers=s.headers).json()
    assert empty == {"conversation_id": "", "turns": []}

    r = client.post("/api/agent/chat", headers=s.headers, json={"message": "hello world"})
    assert r.status_code == 200, r.text
    # The FIRST reply in a conversation carries the compulsory greeting.
    first_reply = r.json()["reply"]
    assert first_reply == f"{GREETING}! {STUB_REPLY}"
    conv_id = r.json()["conversation_id"]
    assert conv_id

    hist = client.get("/api/agent/history", headers=s.headers).json()
    assert hist["conversation_id"] == conv_id
    assert hist["turns"] == [
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "content": first_reply},
    ]

    # ...and ONLY the first: the greeting opens a conversation, it is not a
    # prefix on every turn.
    second = client.post(
        "/api/agent/chat", headers=s.headers, json={"message": "again"}
    ).json()["reply"]
    assert second == STUB_REPLY
    assert not second.startswith(GREETING)


@requires_db
def test_delete_conversation_starts_a_fresh_thread(client, stub_llm, make_student):
    s = make_student("del")

    first = client.post(
        "/api/agent/chat", headers=s.headers, json={"message": "first thread"}
    ).json()["conversation_id"]
    assert client.get("/api/agent/history", headers=s.headers).json()["turns"]

    d = client.delete("/api/agent/conversation", headers=s.headers)
    assert d.status_code == 204

    # History is empty again — the old thread is soft-cleared.
    after = client.get("/api/agent/history", headers=s.headers).json()
    assert after == {"conversation_id": "", "turns": []}

    # The next chat opens a genuinely new conversation, not the deleted one.
    second = client.post(
        "/api/agent/chat", headers=s.headers, json={"message": "second thread"}
    ).json()["conversation_id"]
    assert second and second != first
    reopened = client.get("/api/agent/history", headers=s.headers).json()["turns"]
    # A cleared thread is a NEW conversation, so the greeting is owed again —
    # greeted_at lives on the conversation row, and the old row was soft-deleted.
    assert reopened == [
        {"role": "user", "content": "second thread"},
        {"role": "assistant", "content": f"{GREETING}! {STUB_REPLY}"},
    ]


# ---------------------------------------------------------------------------
# Unit: assert_owner is the ownership guard
# ---------------------------------------------------------------------------
@requires_db
def test_assert_owner_rejects_non_owner_and_unknown(make_student):
    owner = make_student("owner")
    stranger = make_student("stranger")

    with SessionLocal() as db:
        conv = convo.get_or_create(db, owner.user_id, Role.STUDENT)
        cid = conv.id

        # The owner is admitted.
        assert convo.assert_owner(db, cid, owner.user_id).id == cid

        # A different user is refused — and with 404, indistinguishable from
        # "does not exist" (no existence leak).
        with pytest.raises(HTTPException) as ei:
            convo.assert_owner(db, cid, stranger.user_id)
        assert ei.value.status_code == 404

        # A wholly unknown id is likewise 404.
        with pytest.raises(HTTPException) as ei2:
            convo.assert_owner(db, uuid.uuid4().hex, owner.user_id)
        assert ei2.value.status_code == 404


@requires_db
def test_assert_owner_404_after_soft_delete(make_student):
    owner = make_student("softdel")

    with SessionLocal() as db:
        cid = convo.get_or_create(db, owner.user_id, Role.STUDENT).id
        assert convo.assert_owner(db, cid, owner.user_id).id == cid

        convo.clear(db, owner.user_id)  # soft-delete the current thread

        # Even the owner can no longer resolve the deleted conversation by id.
        with pytest.raises(HTTPException) as ei:
            convo.assert_owner(db, cid, owner.user_id)
        assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# The compulsory opening greeting
# ---------------------------------------------------------------------------
@requires_db
def test_greeting_survives_a_failed_first_turn(client, stub_llm, make_student):
    """A turn that fails must NOT consume the greeting.

    The greeting is stamped only after the greeted answer is persisted, so a
    provider outage on the very first message leaves the student still owed it.
    Without this the student's one chance at the greeting is spent on a reply
    they never saw."""
    s = make_student("greetfail")

    def boom(*a, **k):
        raise RuntimeError("provider down")

    # An isolated context, NOT the shared `monkeypatch` fixture: stub_llm patched
    # through that same instance, so undo() would strip the stub too and the
    # "recovered" request would hit the real provider (503 on a keyless CI host).
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.routers.agent.complete_chat", boom)
        r = client.post("/api/agent/chat", headers=s.headers, json={"message": "hi"})
        assert r.status_code == 502

    # Provider recovers (the stub is back); the greeting is still owed.
    ok = client.post("/api/agent/chat", headers=s.headers, json={"message": "hi again"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["reply"].startswith(GREETING)


@requires_db
def test_greeting_is_not_duplicated_when_answer_already_opens_with_it(make_student):
    """open_with_greeting must not stack. The voice agent speaks the phrase and
    the model is told about it, so an answer can legitimately already start with
    it — prefixing again would produce 'Jai Shri Gurudev! Jai Shri Gurudev!'."""
    already = f"{GREETING}! Here is your answer."
    assert convo.open_with_greeting(already) == already
    assert convo.open_with_greeting("Plain answer.") == f"{GREETING}! Plain answer."


@requires_db
def test_greeted_flag_is_per_conversation_not_per_message(client, stub_llm, make_student):
    """awaiting_first_reply reads an explicit stamp, not a count of assistant
    rows — voice writes turns into this same conversation through a best-effort
    cross-process POST, so counting rows would make the text greeting depend on
    whether that write happened to land."""
    s = make_student("greetflag")
    conv_id = client.post(
        "/api/agent/chat", headers=s.headers, json={"message": "first"}
    ).json()["conversation_id"]

    with SessionLocal() as db:
        conv = db.get(Conversation, conv_id)
        assert conv.greeted_at is not None
        assert convo.awaiting_first_reply(db, conv_id) is False

        # A voice turn landing later must not re-arm the greeting.
        convo.append_message(db, conv_id, "assistant", "spoken reply", channel="voice")
        assert convo.awaiting_first_reply(db, conv_id) is False


@requires_db
def test_only_one_active_conversation_per_owner(make_student):
    """The database — not application logic — guarantees one live thread per
    user. get_or_create is read-then-insert, so two concurrent first requests
    can both miss and both insert; without the partial unique index the user
    ends up with two live threads, their turns split across both, and the
    greeting re-armed on whichever one lost."""
    s = make_student("oneactive")

    with SessionLocal() as db:
        first = convo.get_or_create(db, s.user_id, Role.STUDENT)

        # Simulate the racing request: a second insert that skipped the read.
        duplicate = Conversation(owner_user_id=s.user_id, role=Role.STUDENT)
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # get_or_create still resolves to the single live thread.
        assert convo.get_or_create(db, s.user_id, Role.STUDENT).id == first.id

        # Clearing frees the slot, so a genuinely new thread can open.
        convo.clear(db, s.user_id)
        second = convo.get_or_create(db, s.user_id, Role.STUDENT)
        assert second.id != first.id
