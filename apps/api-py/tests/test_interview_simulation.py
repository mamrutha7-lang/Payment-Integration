"""Full-interview simulations: every specialization, end to end, no sockets.

test_interview_nova.py drives the engine one failure at a time; this module
walks a COMPLETE interview the way a student actually experiences it — the
handshake, the greeting, five real answers (one of them arriving in two pieces,
one of them too short), the candidate-questions beat, the spoken verdict, the
scorecard tool call, close 1000 — and asserts on everything that went on the
wire in between.

That is the "does it FEEL like a real interview" regression net: the matrix
tests pin the words, the engine tests pin the failure modes, and these pin that
the two compose into the right CONVERSATION, per specialization, in order.

WHAT CHANGED WHEN THE OPENAI RELAY WENT (2026-09). The old version of this file
counted `response.create` events, because the relay owned the turn and "one
question per answer" was a property you could count. Nova owns the turn — its
own endpointing decides when to answer — so the countable property is now the
STEERING: one control note per phase change, and never one per answer. Where an
assertion below looks weaker than its predecessor, that is why, and the engine's
header says what replaced it.

The harness is test_interview_nova.py's own: a fake upstream that records the
event documents, a fake browser that records what was forwarded, no database.
"""

import json

import pytest

from app.interview_core import _CLOSE_OK, _SessionEnded
from app.interview_matrix import SPECIALIZATIONS, InterviewPhase

# The harness is deliberately imported, not copied: one fake upstream, one fake
# browser, one set of event-sequence helpers, so a change to the harness changes
# every driver of it at once.
from test_interview_nova import (  # noqa: E402
    _GOOD_ANSWER,
    interviewer_turn,
    make_session,
    run,
    spoken_reply,
    student_says,
)
from app import interview_nova as nova

_REPORT = {
    "overall": 71,
    "communication": 68,
    "domain": 74,
    "structure": 70,
    "strengths": ["clear structure", "used real numbers"],
    "improvements": ["name the trade-off sooner"],
    "drill": "Rehearse two STAR answers out loud, timed",
    "summary": "A solid, honest interview with room to be more specific.",
}


# ---------------------------------------------------------------------------
# Driving a whole interview
# ---------------------------------------------------------------------------


def _notes(upstream) -> list[str]:
    """Every control note this session put on the wire, in order."""
    return [
        event["event"]["textInput"]["content"]
        for event in upstream.sent
        if "textInput" in event.get("event", {})
    ]


def _system_prompt(upstream) -> str:
    """The one instruction string the session is configured with."""
    return _notes(upstream)[0]


async def _split_answer(session, first: str, second: str, content_id: str) -> None:
    """One answer that arrived as TWO textOutput events in one content block.

    Nova is documented to deliver the transcription as one block; nothing says
    one event. Treating each as a finished answer would spend a five-answer
    interview in two turns.
    """
    await session._on_upstream_event(
        {
            "event": {
                "contentStart": {
                    "contentId": content_id,
                    "type": "TEXT",
                    "role": "USER",
                    "additionalModelFields": json.dumps({"generationStage": "FINAL"}),
                }
            }
        }
    )
    for piece in (first, second):
        await session._on_upstream_event(
            {"event": {"textOutput": {"contentId": content_id, "content": piece}}}
        )
    await session._on_upstream_event(
        {"event": {"contentEnd": {"contentId": content_id, "type": "TEXT", "stopReason": "END_TURN"}}}
    )


async def _scorecard(session) -> None:
    """The model answering the report request with the tool it was given."""
    await session._on_upstream_event(
        {
            "event": {
                "toolUse": {
                    "toolName": nova._SCORECARD_TOOL_NAME,
                    "toolUseId": "tu-1",
                    "contentId": "tool-1",
                    "content": json.dumps(_REPORT),
                }
            }
        }
    )
    await session._on_upstream_event(
        {"event": {"contentEnd": {"contentId": "tool-1", "type": "TOOL", "stopReason": "TOOL_USE"}}}
    )


# ---------------------------------------------------------------------------
# The simulations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec_key", ["hr", "dm", "ba", "fa"])
def test_a_complete_specialized_interview(spec_key):
    spec = SPECIALIZATIONS[spec_key]
    session, upstream, browser = make_session(spec_key)

    async def scenario():
        # -- the handshake: session, prompt, system prompt, mic, kick-off -----
        # NOTHING GOES UPSTREAM ONTO A REPLY IN FLIGHT. This is the invariant the
        # whole simulation exists to hold: a note sent between completionStart
        # and completionEnd is a user turn that barges in on the interviewer,
        # and the browser hears the voice vanish mid-word.
        original_inject = session._inject

        async def guarded_inject(body: str) -> None:
            assert session._response_open is False, (
                "a control note went upstream while the model was composing a "
                "reply: that is the barge-in that cuts the interviewer off"
            )
            await original_inject(body)

        session._inject = guarded_inject  # type: ignore[method-assign]

        async def turn_opens(cid: str) -> None:
            await session._on_upstream_event(
                {"event": {"completionStart": {"completionId": f"c-{cid}"}}}
            )

        await session._handshake()
        await interviewer_turn(session, "a0", "Hello, and welcome. Tell me about yourself.")

        # Every answer below lands THE WAY BEDROCK DELIVERS IT: inside the
        # completion of the reply it provoked, after completionStart and before
        # the audio -- so every beat the engine decides on a transcript is
        # decided while the model is already speaking.

        # -- answer 1: the self-intro, arriving in two pieces -----------------
        await turn_opens("a1")
        await _split_answer(
            session,
            "I am a final-year student and",
            "I have always enjoyed working with people",
            "u1",
        )
        await spoken_reply(session, "a1")

        # -- answer 2: too short, and the arc does NOT move -------------------
        await turn_opens("a2")
        await student_says(session, "u2", "I think so")
        await spoken_reply(session, "a2")

        # -- answers 2..5 proper -----------------------------------------------
        for index in range(3, 7):
            await turn_opens(f"a{index}")
            await student_says(session, f"u{index}", _GOOD_ANSWER)
            await spoken_reply(session, f"a{index}")
        # The fifth accepted answer ticked WRAP_UP during a6's reply; the
        # invitation was held and released the moment that reply finished.

        # -- the candidate's question, then the verdict -----------------------
        await turn_opens("a-final")
        await student_says(
            session, "u-final", "Yes, what does the first year in this role look like?"
        )
        await spoken_reply(session, "a-final", "Mostly learning the ropes, honestly.")
        # The verdict was held during that answer and released after it; the
        # model speaks it as its own next turn.
        await interviewer_turn(session, "verdict", "You structured your answers well. Good luck.")

        # -- the scorecard, then close ----------------------------------------
        with pytest.raises(_SessionEnded) as caught:
            await _scorecard(session)
        return caught.value

    ended = run(scenario())

    # THE ENDING: well-formed report, clean close.
    assert ended.code == _CLOSE_OK == 1000
    reports = [c for c in browser.control if c["type"] == "reep.report"]
    assert len(reports) == 1
    assert reports[0]["available"] is True
    assert reports[0]["report"]["overall"] == 71

    # THE OPENING: the prompt carries this track's voice, and the system prompt
    # is a REAL opening -- greet, introduce, set expectations, ask the student
    # to introduce themselves -- with the hard scenario question held back for
    # PROBING.
    prompt_start = [e for e in upstream.sent if "promptStart" in e.get("event", {})][0]
    voice = prompt_start["event"]["promptStart"]["audioOutputConfiguration"]["voiceId"]
    assert voice == spec.nova_voice
    opening = _system_prompt(upstream)
    assert spec.persona in opening
    assert "introduce themselves" in opening
    # The probing and deep-dive directives are BRIEFED in the system prompt,
    # scoped to their phases, rather than sent as notes mid-interview; the
    # opening section itself still holds the sample question back.
    head, briefing = opening.split("## How this interview unfolds")
    assert spec.sample_question not in head
    assert spec.sample_question in briefing
    assert "Raise the difficulty" in briefing

    # THE ARC: five accepted answers, and the phase reached WRAP_UP.
    assert session._machine.answers == 5
    assert session._machine.phase is InterviewPhase.WRAP_UP

    # THE STEERING, in order and once each. The kick-off note is the OPENING
    # directive; then NOTHING until the questioning is over (the question
    # phases were briefed up front, and a note per phase change would be a
    # second question on top of the reply that already moved on); then the
    # two closing beats and the report request, each released only once the
    # reply it arrived under had finished. A note per ANSWER would mean the
    # engine was talking over the model it is supposed to be steering.
    notes = _notes(upstream)[1:]  # the system prompt is not a control note
    assert len(notes) == 4
    assert "introduce themselves" in notes[0]          # kick-off / OPENING
    assert "any questions for you" in notes[1]          # WRAP_UP: the invite
    assert "closing verdict" in notes[2]                # the verdict
    assert nova._SCORECARD_TOOL_NAME in notes[3]        # the scorecard
    assert not any("Probe" in note or "Raise the difficulty" in note for note in notes)

    # The too-short answer was RECORDED and did not advance the arc, and it
    # earned no note of its own: the model was already replying, and a second
    # directive would have been a second question.
    assert not any("too brief" in note for note in notes)

    # The split answer is ONE answer: two textOutput events, one turn, one tick.
    assert session._answers_accepted == 5

    # RULE 1's SHAPE, checked against the whole session: nothing the student
    # said appears in anything this engine authored upstream.
    student_words = [
        _GOOD_ANSWER,
        "I am a final-year student",
        "I think so",
        "the first year in this role",
    ]
    for note in _notes(upstream):
        for words in student_words:
            assert words not in note


def test_the_generic_interview_never_reaches_wrap_up_on_its_own():
    """No ?specialization= -- the arc does not exist, and the only route to a
    verdict is the session cap forcing one. Pinned end to end rather than per
    branch."""
    session, upstream, browser = make_session(None)

    async def scenario():
        await session._handshake()
        # Eight accepted answers -- well past five. The machine never ticks.
        for index in range(8):
            await student_says(session, f"u{index}", _GOOD_ANSWER)
            await interviewer_turn(session, f"a{index}")
        assert session._machine.phase is InterviewPhase.OPENING
        assert session._report_requested is False

        # The clock is the only closer this track has -- and on the generic
        # track it closes nothing: there is no bar to score against, so no
        # verdict is forced and no scorecard is invented.
        await session._force_wrap_up()
        return session

    run(scenario())

    assert session._machine.phase is InterviewPhase.OPENING
    assert session._verdict_requested is False
    assert session._report_requested is False
    # No phase was ever announced to the browser, because none was reached.
    assert not [c for c in browser.control if c["type"] == "reep.phase"]
    # Rule 1's shape, on the generic track too.
    for note in _notes(upstream):
        assert _GOOD_ANSWER not in note
