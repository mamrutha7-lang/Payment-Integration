"""The local interview engine — the parts that must hold with nothing hosted.

app/interview_local.py runs the same interview as the Nova engine with nothing
leaving the machine. These tests pin the contracts that make that a drop-in
rather than a fork, and they do NO model work: loading faster-whisper and Piper
takes seconds and needs a GPU, and a suite that needs either stops being run.

What is pinned here:

1. The engine is interchangeable with the hosted one. Same constructor, same
   run() contract, same payload records -- because the router picks between them
   on one setting and every writer around them is shared.
2. Student text reaches the model as a MESSAGE and never as an instruction.
   That is the same line the hosted engine draws and the reason it draws it.
3. The turn boundary. `_Vad` is the local replacement for a server VAD, so its
   thresholds decide when a student is judged to have stopped talking.
"""

import inspect

import pytest

from app import interview_local as local
from app.config import settings
from app.interview_matrix import SPECIALIZATIONS, InterviewPhase, build_instructions
from app.interview_core import _INTERVIEWER_PERSONA
from app.interview_nova import NovaSonicSession


class TestItIsADropIn:
    """The router swaps these two on a setting; they must be interchangeable."""

    def test_the_constructor_matches_the_hosted_engine(self):
        """Every keyword the router passes must exist on both engines.

        A missing one is a TypeError at the moment a student presses Start,
        which is the worst possible time to find out.
        """
        relay = set(inspect.signature(NovaSonicSession.__init__).parameters)
        localsig = set(inspect.signature(local.LocalSession.__init__).parameters)
        assert relay == localsig

    def test_run_and_request_stop_exist_with_the_same_shape(self):
        assert inspect.iscoroutinefunction(local.LocalSession.run)
        stop = inspect.signature(local.LocalSession.request_stop)
        assert list(stop.parameters) == ["self", "code", "reason"]

    def test_the_payload_records_are_the_shared_ones(self):
        """Imported from app/interview_core.py, not redefined.

        A parallel definition would drift the moment either gained a field, and
        it would drift silently: both would still construct, and only one would
        carry the new value into the database.
        """
        from app import interview_core

        assert local._TurnRecord is interview_core._TurnRecord
        assert local._ReportRecord is interview_core._ReportRecord
        assert local._SessionOutcome is interview_core._SessionOutcome


class TestRuleOne:
    def test_student_text_never_enters_the_instructions(self):
        """Instructions are fixed strings; what the student said is a message.

        The local engine could get away with anything here -- nothing leaves the
        machine, so there is no egress to gate. It holds the line anyway,
        because the guardrail is about what the NEXT editor will do with a
        composable instruction string, not about this hop.
        """
        spec = SPECIALIZATIONS["dm"]
        composed = build_instructions(spec, _INTERVIEWER_PERSONA, InterviewPhase.PROBING)
        secret = "my USN is 1MP25MDM01 and my CGPA is 8.4"
        assert secret not in composed

    def test_the_engine_talks_only_to_loopback(self):
        """By literal address, never the name.

        On Windows `localhost` resolves to ::1 first and stalls against an
        IPv4-only server -- measured at 2036 ms per request against 30 ms. It
        presents as a slow model and is not one.
        """
        base = local._ollama_base()
        assert base.startswith("http://127.0.0.1") or base.startswith("http://[::1]")
        assert "localhost" not in base

    def test_the_v1_suffix_is_stripped(self):
        """llm_base_url carries /v1 for the OpenAI-compatible surface; the
        native /api/chat route this engine uses does not live under it."""
        assert not local._ollama_base().endswith("/v1")


class TestTheRecordSaysWhoSpoke:
    """The sender strings must be the ones the router's writer understands."""

    def test_the_student_is_sent_as_user(self):
        """`speaker = "student" if sender == "user" else "interviewer"`.

        "student" is the obvious guess and it is wrong: it lands in the else
        branch and files the student's own answer under the interviewer's name.
        Caught in a live run, where seq 2 was an accepted student answer
        attributed to the interviewer -- the row was written, the counters
        matched, and only reading the transcript back showed it.
        """
        assert local._SENDER_STUDENT == "user"

    def test_the_interviewer_is_not_sent_as_user(self):
        assert local._SENDER_INTERVIEWER != "user"

    def test_no_call_site_spells_the_sender_by_hand(self):
        """A literal at a call site is how this bug came back the first time."""
        source = inspect.getsource(local)
        assert '_emit_turn("student"' not in source
        assert '_emit_turn("assistant"' not in source


class TestTurnBoundary:
    """`_Vad` is the local stand-in for a server VAD."""

    def test_one_loud_chunk_is_not_a_turn(self):
        """A cough, a keystroke or a chair must not open a turn."""
        vad = local._Vad()
        assert vad.feed(0.5) is None
        assert not vad.speaking

    def test_three_consecutive_chunks_open_it(self):
        vad = local._Vad()
        assert [vad.feed(0.05) for _ in range(3)] == [None, None, "started"]
        assert vad.speaking

    def test_a_gap_shorter_than_the_hangover_does_not_end_the_turn(self):
        """People pause mid-sentence. Ending the turn there interrupts them."""
        vad = local._Vad()
        for _ in range(3):
            vad.feed(0.05)
        short_gap = local._VAD_HANGOVER_CHUNKS - 1
        assert all(vad.feed(0.0) is None for _ in range(short_gap))
        assert vad.speaking

    def test_the_full_hangover_ends_it_exactly_once(self):
        vad = local._Vad()
        for _ in range(3):
            vad.feed(0.05)
        events = [vad.feed(0.0) for _ in range(local._VAD_HANGOVER_CHUNKS + 4)]
        assert events.count("stopped") == 1
        assert not vad.speaking

    def test_room_tone_never_opens_a_turn(self):
        """Tuned against the recorded interviews: room tone sits below 0.004."""
        vad = local._Vad()
        assert all(vad.feed(0.003) is None for _ in range(200))
        assert not vad.speaking


class TestClauseCutting:
    """Time-to-first-audio is decided here: TTS starts at the first clause."""

    def test_it_waits_for_enough_to_be_worth_speaking(self):
        assert local._clause_cut("Well,") is None

    def test_it_cuts_at_punctuation(self):
        text = "Can you walk me through that, in detail?"
        assert text[: local._clause_cut(text)] == "Can you walk me through that,"

    def test_a_clause_that_never_punctuates_is_still_cut(self):
        """Otherwise one runaway sentence is the whole latency budget."""
        text = "a" * 200
        cut = local._clause_cut("word " * 40)
        assert cut is not None and cut <= local._MAX_CLAUSE_CHARS
        assert local._clause_cut(text) is None or local._clause_cut(text) <= 200


class TestReportParsing:
    """A local model fences JSON far more often than a hosted one."""

    @pytest.mark.parametrize(
        "raw",
        [
            '{"overall": 7}',
            '```json\n{"overall": 7}\n```',
            'Here is the scorecard:\n{"overall": 7}\nHope that helps.',
        ],
    )
    def test_it_recovers_the_object(self, raw):
        assert local._parse_report(raw) == {"overall": 7}

    @pytest.mark.parametrize("raw", ["", "no json here", "[1, 2, 3]", "{broken"])
    def test_it_returns_none_rather_than_guessing(self, raw):
        """None becomes report_status='unparseable', which is an honest record.
        A half-parsed scorecard would be a dishonest one."""
        assert local._parse_report(raw) is None


class TestEngineSelection:
    def test_the_default_is_the_hosted_engine(self):
        """The local engine is opt-IN, and stays that way.

        It needs a fourth venv, model weights on disk and ideally a GPU; a
        deployment that has not set any of that up must not find its interviews
        silently running on an engine that cannot start.
        """
        assert type(settings).model_fields["interview_engine"].default == "nova"

    @pytest.mark.parametrize("bad", ["loca", "LOCALL", "yes", "true", "  "])
    def test_an_unrecognised_engine_falls_back_rather_than_guessing(self, bad):
        """An allowlist, not `!= "local"`.

        A typo must not quietly run the hosted engine while the operator
        believes nothing is leaving the machine -- nor silently run the local
        one, which needs weights on disk they may never have installed.
        """
        assert type(settings)._known_engine(bad) == "nova"

    @pytest.mark.parametrize(
        "good,expected",
        [("local", "local"), (" LOCAL ", "local"), ("nova", "nova"), (" Nova ", "nova")],
    )
    def test_the_known_names_survive_normalisation(self, good, expected):
        assert type(settings)._known_engine(good) == expected

    def test_the_interviewer_model_is_separate_from_the_app_model(self):
        """They have different constraints: the interviewer shares a GPU with
        Whisper and must answer inside a conversation. Measured cost of
        conflating them: 9528 ms to first clause instead of 47 ms."""
        assert type(settings).model_fields["interview_local_llm_model"].default
        assert (
            type(settings).model_fields["interview_local_llm_model"].default
            != type(settings).model_fields["llm_model"].default
        )
