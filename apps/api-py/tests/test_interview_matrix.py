"""The Specialization Matrix and its interview state machine.

Two contracts are pinned here:

1. The matrix itself -- the four rows (HR, DM, BA, FA) with their personas,
   frameworks and sample questions are the product spec verbatim, so a test
   fails the moment a row goes missing or a field is left empty.

2. The state machine -- the phase arc (opening -> probing -> deep_dive ->
   wrap_up -> ended) is what steers the model mid-interview, and an illegal
   jump or a sticky-phase bug changes what every student is assessed against.

3. classify_answer -- the deterministic gate that decides whether an utterance
   advanced the interview. It runs on every turn between the student finishing
   and the interviewer replying, so it must stay a word count.

No database, no sockets: this module does no I/O by design.
"""

import pytest

from app.config import settings
from app.interview_matrix import (
    KNOWN_NOVA_VOICES,
    REPORT_DIRECTIVE,
    SPECIALIZATIONS,
    InterviewPhase,
    InterviewStateMachine,
    build_instructions,
    build_turn_instructions,
    classify_answer,
    get_specialization,
    is_skip_request,
    words_of,
)
from app.interview_core import _INTERVIEWER_PERSONA


class TestMatrix:
    def test_exactly_the_four_specializations(self):
        assert set(SPECIALIZATIONS) == {"hr", "dm", "ba", "fa"}

    @pytest.mark.parametrize("key", ["hr", "dm", "ba", "fa"])
    def test_every_row_is_complete(self, key):
        spec = SPECIALIZATIONS[key]
        assert spec.label
        assert spec.persona
        assert len(spec.frameworks) >= 4
        assert all(framework for framework in spec.frameworks)
        # Phrased as a prompt, not necessarily with a "?": two of the four are
        # "Walk me through..." statements in the spec.
        assert len(spec.sample_question) > 30
        # Every role speaks with a voice the model will actually accept: an
        # unknown one is a ValidationException at the handshake, i.e. an
        # interview that never starts.
        assert spec.nova_voice in KNOWN_NOVA_VOICES

    def test_the_per_specialization_voices(self):
        """A CHRO does not sound like a CFO. Pinned so a casual re-voice of
        every track to the same name is a deliberate act."""
        assert SPECIALIZATIONS["hr"].nova_voice == "kiara"
        assert SPECIALIZATIONS["dm"].nova_voice == "tiffany"
        assert SPECIALIZATIONS["ba"].nova_voice == "arjun"
        assert SPECIALIZATIONS["fa"].nova_voice == "matthew"
        # Four roles, four voices -- the field is per-specialization, not a
        # second global default wearing a matrix costume.
        assert len({spec.nova_voice for spec in SPECIALIZATIONS.values()}) == 4

    def test_the_spec_sample_questions(self):
        # The spec's own wording, pinned so a casual edit is a deliberate act.
        assert "sexual harassment claim" in SPECIALIZATIONS["hr"].sample_question
        assert "CAC has increased by 40%" in SPECIALIZATIONS["dm"].sample_question
        assert "customer churn" in SPECIALIZATIONS["ba"].sample_question
        assert "$10 depreciation" in SPECIALIZATIONS["fa"].sample_question

    def test_only_dm_carries_a_mapped_syllabus(self):
        """The field is optional and must stay optional.

        DM maps to 22MDM23; the other three tracks have no mapped course yet.
        A default of () rather than None is what lets build_instructions test
        truthiness without a None check at every call site.
        """
        assert SPECIALIZATIONS["dm"].syllabus
        for key in ("hr", "ba", "fa"):
            assert SPECIALIZATIONS[key].syllabus == ()

    def test_the_dm_syllabus_covers_every_module_and_the_arithmetic(self):
        """22MDM23 is five modules plus the metric formulas.

        Pinned by content, not by length: a syllabus that silently lost SEO or
        the formulas would still be a non-empty tuple, and the interview would
        simply stop asking about a fifth of the course with nothing failing.
        """
        blob = " ".join(SPECIALIZATIONS["dm"].syllabus)
        for module in ("Module 1", "Module 2", "Module 3", "Module 4", "Module 5"):
            assert module in blob
        for concept in ("P-O-E-M", "remarketing", "Ad Rank", "gamification", "crawling"):
            assert concept in blob
        for formula in ("CTR =", "CPC =", "CPM =", "ROAS ="):
            assert formula in blob

    def test_the_syllabus_carries_no_answer_key(self):
        """Questions, never model answers.

        The source bank ships 100 questions WITH answers. Composing those into
        the instructions would have the model marking a student against a
        memorised script instead of listening to them, so the syllabus is a
        topic map and the giveaway phrasing must never appear in it.
        """
        blob = " ".join(SPECIALIZATIONS["dm"].syllabus)
        assert "Answer:" not in blob

    def test_lookup_is_case_insensitive_and_tolerant(self):
        assert get_specialization("HR") is SPECIALIZATIONS["hr"]
        assert get_specialization(" fa ") is SPECIALIZATIONS["fa"]

    def test_lookup_misses(self):
        assert get_specialization(None) is None
        assert get_specialization("") is None
        assert get_specialization("marketing") is None


class TestSyllabusComposition:
    """How the syllabus reaches the model, and what it must not disturb."""

    def test_the_block_appears_for_dm_and_is_absent_otherwise(self):
        dm = build_instructions(SPECIALIZATIONS["dm"], _INTERVIEWER_PERSONA)
        hr = build_instructions(SPECIALIZATIONS["hr"], _INTERVIEWER_PERSONA)
        assert "## The course this student has actually studied" in dm
        # Not merely absent content - an empty heading composed anyway would
        # tell the model a syllabus exists and then show it nothing.
        assert "actually studied" not in hr

    def test_the_base_persona_still_comes_first_and_verbatim(self):
        """The syllabus block may not displace the conduct rules.

        base_persona carries "one question at a time" and the rule-1
        disclosure. A block inserted above it, or one that pushed it out of the
        instruction entirely, would be invisible in review and would change
        every DM interview.
        """
        out = build_instructions(SPECIALIZATIONS["dm"], _INTERVIEWER_PERSONA)
        assert out.startswith(_INTERVIEWER_PERSONA)
        assert out.index(_INTERVIEWER_PERSONA) < out.index("## The course")

    @pytest.mark.parametrize(
        "phase",
        [InterviewPhase.OPENING, InterviewPhase.PROBING,
         InterviewPhase.DEEP_DIVE, InterviewPhase.WRAP_UP],
    )
    def test_every_phase_still_composes_with_a_syllabus(self, phase):
        out = build_instructions(SPECIALIZATIONS["dm"], _INTERVIEWER_PERSONA, phase)
        assert f"## Current phase: {phase.value}" in out
        assert "## The course this student has actually studied" in out

    def test_probing_and_deep_dive_use_the_syllabus_when_there_is_one(self):
        dm_probe = build_instructions(
            SPECIALIZATIONS["dm"], _INTERVIEWER_PERSONA, InterviewPhase.PROBING
        )
        hr_probe = build_instructions(
            SPECIALIZATIONS["hr"], _INTERVIEWER_PERSONA, InterviewPhase.PROBING
        )
        assert "syllabus modules" in dm_probe
        assert "syllabus modules" not in hr_probe
        dm_deep = build_instructions(
            SPECIALIZATIONS["dm"], _INTERVIEWER_PERSONA, InterviewPhase.DEEP_DIVE
        )
        assert "at least two syllabus modules" in dm_deep

    def test_no_student_data_path_is_opened(self):
        """Rule 1, restated as a test on the new field.

        The syllabus is fixed course text, so it is safe upstream for exactly
        the reason the persona is: it is authored here and contains no student
        record. This pins that the composed instruction is a pure function of
        the matrix - the same arguments give the same string, so nothing
        request-scoped can have leaked into it.
        """
        a = build_instructions(SPECIALIZATIONS["dm"], _INTERVIEWER_PERSONA)
        b = build_instructions(SPECIALIZATIONS["dm"], _INTERVIEWER_PERSONA)
        assert a == b


class TestStateMachine:
    def test_starts_at_opening(self):
        machine = InterviewStateMachine(SPECIALIZATIONS["hr"])
        assert machine.phase is InterviewPhase.OPENING
        assert machine.answers == 0

    def test_full_arc_on_completed_answers(self):
        machine = InterviewStateMachine(SPECIALIZATIONS["dm"])
        # answer 1 -> probing
        assert machine.student_answered() is True
        assert machine.phase is InterviewPhase.PROBING
        # answer 2 stays probing
        assert machine.student_answered() is False
        assert machine.phase is InterviewPhase.PROBING
        # answer 3 -> deep dive
        assert machine.student_answered() is True
        assert machine.phase is InterviewPhase.DEEP_DIVE
        # answer 4 stays
        assert machine.student_answered() is False
        # answer 5 -> wrap up
        assert machine.student_answered() is True
        assert machine.phase is InterviewPhase.WRAP_UP

    def test_wrap_up_is_sticky(self):
        machine = InterviewStateMachine(SPECIALIZATIONS["ba"])
        for _ in range(5):
            machine.student_answered()
        assert machine.phase is InterviewPhase.WRAP_UP
        # Answers during the verdict must not re-arm question phases.
        assert machine.student_answered() is False
        assert machine.phase is InterviewPhase.WRAP_UP

    def test_end_is_terminal_from_any_phase(self):
        machine = InterviewStateMachine(SPECIALIZATIONS["fa"])
        machine.end()
        assert machine.phase is InterviewPhase.ENDED
        assert machine.student_answered() is False

    def test_illegal_transition_raises(self):
        machine = InterviewStateMachine(SPECIALIZATIONS["hr"])
        with pytest.raises(ValueError, match="Illegal interview transition"):
            machine._transition_to(InterviewPhase.DEEP_DIVE)

    def test_force_wrap_up_does_not_inflate_the_answer_count(self):
        # The clock, not the arc, ended this interview. A mentor reading
        # answers_accepted must be reading answers.
        machine = InterviewStateMachine(SPECIALIZATIONS["hr"])
        machine.student_answered()
        assert machine.force_wrap_up() is True
        assert machine.phase is InterviewPhase.WRAP_UP
        assert machine.answers == 1
        # Idempotent: the relay may reach it twice on the same session.
        assert machine.force_wrap_up() is False

    def test_generic_interview_still_runs(self):
        # No specialization: the machine exists but the relay never advances it.
        machine = InterviewStateMachine(None)
        assert machine.specialization is None
        assert machine.phase is InterviewPhase.OPENING


class TestAnswerClassification:
    """The gate that decides whether the phase machine may tick at all.

    Every verdict here has a consequence a student feels: "accepted" moves the
    interview on, everything else re-asks the same question.
    """

    def test_nothing_at_all_is_empty(self):
        # A timed-out or failed transcription arrives as "", and it must not
        # read as an answer -- it is the absence of one.
        assert classify_answer("") == "empty"
        assert classify_answer("   ") == "empty"
        assert classify_answer("...!") == "empty"

    @pytest.mark.parametrize("text", ["uh", "yes", "yeah ok", "hmm, sorry, what?"])
    def test_acknowledgements_and_coughs_are_filler(self, text):
        assert classify_answer(text) == "filler"

    def test_a_short_but_real_answer_is_too_short_not_filler(self):
        # The two are stored separately because they are different facts about
        # the interview: one student said nothing, the other said too little.
        assert classify_answer("I led it") == "too_short"

    def test_a_real_short_answer_still_counts(self):
        # The floor is 4 words precisely so this passes -- a real answer must
        # not be re-asked for being brief.
        assert classify_answer("I led the campus fintech club") == "accepted"

    def test_contractions_are_one_word(self):
        # "I don't know really" is four words, not five: splitting contractions
        # would inflate the count and let a non-answer through.
        assert classify_answer("I don't know") == "too_short"

    def test_zero_disables_the_gate(self, monkeypatch):
        # 0 is a MEANING (config.py): the pre-v3 behaviour where any transcript
        # counted as an answer.
        monkeypatch.setattr(settings, "interview_min_answer_words", 0)
        assert classify_answer("yes") == "accepted"
        # Still not an answer when there is no transcript at all.
        assert classify_answer("") == "empty"
        # And still not an answer when the student asked for the next question:
        # a skip is a different fact from a short answer, whatever the floor.
        assert classify_answer("next question please") == "skipped"

    @pytest.mark.parametrize(
        "text",
        [
            "next question",
            "Next question, please.",
            "can we skip this one",
            "I'd like to skip this question",
            "skip",
            "I pass on this",
            "let's move on",
            "um, I don't know this one, next question",
        ],
    )
    def test_asking_for_the_next_question_is_a_skip(self, text):
        # Two words used to be `too_short`: the record said the student failed
        # to answer while the model, which had heard them, moved on. A skip is
        # its own verdict, does not count, and is not a failed answer.
        assert is_skip_request(text) is True
        assert classify_answer(text) == "skipped"

    @pytest.mark.parametrize(
        "text, verdict",
        [
            # What Nova 2 Sonic wrote, in Devanagari, for the first student who
            # tried the interview with an Indian accent. The gate used to
            # match `[a-z0-9']+`, so every one of these was `empty` -- "the
            # transcriber returned nothing" about five audible answers.
            ("हेलो हाई मैं दर्शन हूँ।", "accepted"),  # hello, hi, I'm Darshan: 5 words
            ("हाँ।", "too_short"),  # yes: one word, and not on the English filler list
            ("ओके, अगला प्रश्न।", "skipped"),  # ok, next question
            ("अगला प्रश्न, कृपया।", "skipped"),  # next question, please
            ("अगला सवाल", "skipped"),
        ],
    )
    def test_a_transcript_in_another_script_is_read_word_for_word(self, text, verdict):
        assert classify_answer(text) == verdict

    def test_words_are_runs_of_letters_digits_and_marks_in_any_script(self):
        # Python's \w excludes combining marks, and Hindi vowel signs and the
        # virama ARE marks, so a \w-based pattern shatters one word into four.
        assert words_of("अगला प्रश्न, कृपया।") == ["अगला", "प्रश्न", "कृपया"]
        assert words_of("I don't know... really!") == ["i", "don't", "know", "really"]
        assert words_of("'tis 'quoted'") == ["tis", "quoted"]

    @pytest.mark.parametrize(
        "text",
        [
            # An answer that happens to contain the word is still an answer.
            "Let's skip the pleasantries: I led the campus fintech club and grew "
            "it to eighty members over two years",
            # "passed", not "pass"; a whole-word match, never a substring.
            "I passed the CFA level one on my first attempt last year",
            # A long answer about moving on is an answer.
            "When the client asked us to move on from the pilot we first "
            "measured retention across the three cohorts and then decided",
        ],
    )
    def test_an_answer_that_mentions_skipping_is_not_a_skip(self, text):
        assert is_skip_request(text) is False
        assert classify_answer(text) == "accepted"


class TestTurnInstructions:
    """The per-response overrides. They REPLACE the session persona, so each
    one has to be self-contained or the interviewer loses its conduct rules on
    exactly the turn where it is improvising."""

    @pytest.mark.parametrize(
        "kind", ["clarify", "unheard", "resume", "skip", "verdict", "invite_questions"]
    )
    def test_every_kind_keeps_the_persona_and_the_rule_1_disclosure(self, kind):
        instructions = build_turn_instructions(
            SPECIALIZATIONS["ba"], _INTERVIEWER_PERSONA, InterviewPhase.PROBING, kind
        )
        assert instructions.startswith(_INTERVIEWER_PERSONA)
        assert "cannot see" in instructions
        assert "## This turn" in instructions

    def test_the_generic_interview_gets_a_self_contained_override(self):
        instructions = build_turn_instructions(
            None, _INTERVIEWER_PERSONA, InterviewPhase.OPENING, "clarify"
        )
        assert instructions.startswith(_INTERVIEWER_PERSONA)
        assert "## Specialization" not in instructions

    def test_an_unknown_kind_raises_rather_than_shipping_no_directive(self):
        with pytest.raises(ValueError, match="Unknown turn kind"):
            build_turn_instructions(None, _INTERVIEWER_PERSONA, InterviewPhase.OPENING, "nudge")

    def test_the_report_directive_names_nobody(self):
        # Rule 1's shape: everything this app authors upstream is a fixed
        # string. The scorecard is generated in the session that already holds
        # the transcript, so it needs no student data to be sent for it.
        assert "JSON" in REPORT_DIRECTIVE
        assert "{" in REPORT_DIRECTIVE
        for key in ("overall", "communication", "domain", "structure", "drill", "summary"):
            assert key in REPORT_DIRECTIVE


class TestInstructionComposition:
    def test_base_persona_leads_and_is_verbatim(self):
        instructions = build_instructions(SPECIALIZATIONS["hr"], _INTERVIEWER_PERSONA)
        assert instructions.startswith(_INTERVIEWER_PERSONA)

    def test_opening_is_a_real_opening_not_a_domain_question(self):
        """The interview begins the way a real one does: a greeting, a one-
        sentence self-introduction, expectations, and "tell me about yourself".
        The hard scenario question moved to PROBING -- asking it cold, seconds
        after the handshake, is what made the old opening feel like a quiz."""
        spec = SPECIALIZATIONS["fa"]
        instructions = build_instructions(spec, _INTERVIEWER_PERSONA)
        assert spec.persona in instructions
        assert spec.label in instructions
        for framework in spec.frameworks:
            assert framework in instructions
        assert "introduce themselves" in instructions
        assert "no domain questions in this phase" in instructions
        assert "## Current phase: opening" in instructions
        # The sample question is NOT here any more -- it is worked in during
        # probing, once the student has actually started talking.
        assert spec.sample_question not in instructions

    @pytest.mark.parametrize("key", ["hr", "dm", "ba", "fa"])
    def test_probing_works_in_the_sample_question(self, key):
        """Both PROBING variants -- with a syllabus (DM) and without -- carry
        the matrix's sample question, rephrased rather than recited."""
        spec = SPECIALIZATIONS[key]
        instructions = build_instructions(
            spec, _INTERVIEWER_PERSONA, InterviewPhase.PROBING
        )
        assert spec.sample_question in instructions
        assert "rephrased naturally rather than recited" in instructions

    def test_the_delivery_style_follows_the_persona_and_precedes_the_track(self):
        """How the interviewer SOUNDS is one shared block, positioned after the
        conduct rules it must not dilute and before the specialization it
        applies to."""
        instructions = build_instructions(SPECIALIZATIONS["dm"], _INTERVIEWER_PERSONA)
        assert instructions.startswith(_INTERVIEWER_PERSONA)
        assert (
            instructions.index(_INTERVIEWER_PERSONA)
            < instructions.index("speaking aloud, not writing")
            < instructions.index("## Specialization:")
        )
        # The substance: spoken register, one acknowledgement, no scaffolding.
        assert "no bullet points" in instructions

    def test_the_candidate_questions_turn_kind(self):
        """A real interview ends with "any questions for us?" BEFORE the
        verdict. This is the directive for that beat."""
        instructions = build_turn_instructions(
            SPECIALIZATIONS["hr"], _INTERVIEWER_PERSONA, InterviewPhase.WRAP_UP,
            "invite_questions",
        )
        assert instructions.startswith(_INTERVIEWER_PERSONA)
        assert "any questions for you" in instructions
        assert "Do not deliver the verdict yet" in instructions

    def test_wrap_up_forbids_new_questions(self):
        instructions = build_instructions(
            SPECIALIZATIONS["ba"], _INTERVIEWER_PERSONA, InterviewPhase.WRAP_UP
        )
        assert "## Current phase: wrap_up" in instructions
        assert "Ask no further questions" in instructions

    def test_ended_has_no_directive(self):
        with pytest.raises(ValueError):
            build_instructions(
                SPECIALIZATIONS["hr"], _INTERVIEWER_PERSONA, InterviewPhase.ENDED
            )

    def test_rule_1_disclosure_survives_every_phase(self):
        # The "you cannot see the dashboard" disclosure is part of the base
        # persona; a composition that dropped it would let the model invent a
        # CGPA out loud.
        for phase in (
            InterviewPhase.OPENING,
            InterviewPhase.PROBING,
            InterviewPhase.DEEP_DIVE,
            InterviewPhase.WRAP_UP,
        ):
            instructions = build_instructions(
                SPECIALIZATIONS["dm"], _INTERVIEWER_PERSONA, phase
            )
            assert "cannot see" in instructions
