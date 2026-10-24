"""`conversations.append_message` dedup — the FIRST of the two layers.

This contract used to be pinned through `POST /api/voice/transcript`, which was
deleted with the LiveKit voice stack. The rule did not go with it: the interview
relay writes its turns through the SAME `append_message`
(`app/routers/interview.py:386`), and its own comment calls the read-then-insert
dedup "a CHECK, not a guarantee" — so the check is still load-bearing and is
otherwise untested.

`test_interview_records.py` covers the SECOND layer, the
`uq_interview_turn_provider` unique index on `interview_turns`. That is a
different table and a different mechanism; neither test substitutes for the
other.

These exercise the function directly rather than through a route, because there
is no longer a route that reaches it with a `provider_turn_id` from outside.
"""

from sqlalchemy import select

from conftest import requires_db

from app import conversations as convo
from app.db import SessionLocal
from app.models.conversation import Conversation, Message
from app.models.user import Role


def _new_conversation(user_id: str) -> str:
    with SessionLocal() as db:
        return convo.get_or_create(db, user_id, Role.STUDENT).id


def _messages(conversation_id: str) -> list[Message]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at)
            ).all()
        )


@requires_db
def test_a_repeated_provider_turn_id_keeps_the_original_wording(make_user):
    """A re-emitted turn is a no-op, not an update.

    An upstream model may re-emit a turn it has already sent, sometimes with
    revised text. The stored row must stay as first written: the student read
    the original on screen during the call, and silently rewriting the persisted
    turn makes the saved history disagree with what they saw.
    """
    s = make_user("dedup-reemit")
    cid = _new_conversation(s.user_id)

    with SessionLocal() as db:
        first = convo.append_message(
            db, cid, "user", "the original wording", provider_turn_id="reemit-1"
        )
        db.commit()
        first_id = first.id

    with SessionLocal() as db:
        again = convo.append_message(
            db, cid, "user", "a completely different revision", provider_turn_id="reemit-1"
        )
        db.commit()
        # The already-stored Message comes back rather than a second insert.
        assert again.id == first_id

    assert [m.content for m in _messages(cid)] == ["the original wording"]


@requires_db
def test_dedup_is_scoped_to_one_conversation(make_user):
    """The same provider_turn_id in two conversations is two turns.

    Upstream turn ids are only unique within their own stream, so scoping the
    check globally would make one student's turn suppress another's.
    """
    s = make_user("dedup-scope")
    other = make_user("dedup-scope-other")
    cid_a = _new_conversation(s.user_id)
    cid_b = _new_conversation(other.user_id)
    assert cid_a != cid_b

    with SessionLocal() as db:
        convo.append_message(db, cid_a, "user", "mine", provider_turn_id="shared-1")
        convo.append_message(db, cid_b, "user", "theirs", provider_turn_id="shared-1")
        db.commit()

    assert [m.content for m in _messages(cid_a)] == ["mine"]
    assert [m.content for m in _messages(cid_b)] == ["theirs"]


@requires_db
def test_turns_without_a_provider_turn_id_are_never_deduped(make_user):
    """No id means no dedup key — two identical turns are two rows.

    A student really can say the same short thing twice ("yes", "sorry?"), and
    collapsing those on content would lose a real turn from the transcript.
    """
    s = make_user("dedup-none")
    cid = _new_conversation(s.user_id)

    with SessionLocal() as db:
        convo.append_message(db, cid, "user", "yes")
        convo.append_message(db, cid, "user", "yes")
        db.commit()

    assert [m.content for m in _messages(cid)] == ["yes", "yes"]
