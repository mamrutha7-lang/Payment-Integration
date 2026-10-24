"""The Specialization Matrix for the mock interviewer, and its state machine.

One interview no longer has one fixed persona. The student picks a
specialization on the assistant screen, the client sends it as
`?specialization=hr|dm|ba|fa` on the /api/interview handshake, and this module
answers the two questions the relay then has:

    1. WHO is interviewing?  -- the Specialization row: the AI persona, the
       core frameworks under assessment, the voice the role speaks with, and
       the sample question worked in during probing.
    2. WHERE in the interview are we?  -- InterviewStateMachine, advanced by
       the relay on each COMPLETED student answer, whose phase directive is
       what steers the model from the opening question through to the verdict.

RULE 1 (AGENTS.md) is untouched by all of this. A specialization key is chosen
by the student in the UI and carries nothing from their record; the composed
instructions are a fixed string per (specialization, phase) pair, exactly as
_INTERVIEWER_PERSONA was a fixed string before. No marks, USN, attendance or
resume text enters them, and the base persona's "you cannot see the dashboard"
disclosure is included verbatim in every composition.

A phase change reaches the model in whichever way the engine can carry it. An
engine with an instructions-only update (the local engine) is re-sent the
composition for the new phase. Nova 2 Sonic has no such update AND owns the
turn, so it is BRIEFED ON THE WHOLE ARC ONCE at the handshake
(`build_arc_briefing`), with the same rule the word gate applies, and steers
itself from answer to answer; only the beats that must land at a fixed point
(the invitation to ask questions, the verdict) reach it as control notes, held
until the reply in flight has finished. The reason is in app/interview_nova.py:
a text input is a user turn, and one sent onto a reply Nova is composing
barges in on it. The voice is never part of any of this: it is frozen at the
handshake and cannot be changed once audio has started.

Two more things live here under Interview Engine v3, and both are here rather
than in the relay ON PURPOSE:

    3. IS THAT AN ANSWER?  -- classify_answer, a word count and a filler list.
       Deterministic and local because it runs between the student finishing
       and the interviewer replying: a model call here would be latency on
       every single turn.
    4. WHAT DO WE SAY WHEN IT IS NOT?  -- build_turn_instructions composes the
       per-response override for a clarification. Instruction composition stays
       in ONE module so the base persona keeps arriving first and verbatim, and
       so that "everything this app authors upstream is a fixed string" is a
       property you can check by reading one file. Nothing here ever composes
       the student's own words into an instruction, and that shape is the
       guardrail: the moment student text goes into `instructions`, the next
       editor puts a resume in there.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# The answer floor is operator-tunable, so it is read from config rather than
# frozen here. This is still an I/O-free module: settings is a value object
# read at call time, not a database.
from .config import settings


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


# The voices Amazon Nova 2 Sonic will accept (app/interview_nova.py). Nova
# answers an unknown voiceId with a ValidationException that kills the stream at
# the handshake -- before a single word is spoken -- so the engine validates
# against this set and falls back with a line in the log rather than losing the
# interview to a typo.
#
# There was a second set here, KNOWN_REALTIME_VOICES, and a matching `voice`
# column on every row, for the OpenAI relay. The two providers share no voice
# names at all ("coral" is not a Nova voice, "kiara" is not an OpenAI one), so
# one column could never have served both; both went with the relay in 2026-09.
KNOWN_NOVA_VOICES: Final[frozenset[str]] = frozenset(
    {"matthew", "tiffany", "amy", "olivia", "lupe", "carlos", "ambre",
     "florian", "lennart", "beatrice", "lorenzo", "tina", "carolina", "leo",
     "kiara", "arjun"}
)

# Where the engine lands when the configured voice is not in the set above.
# "matthew" rather than the first name alphabetically: it is the US English
# masculine voice AWS's own examples default to, so an operator who mistypes
# gets an interviewer that sounds ordinary rather than one that suddenly speaks
# in another accent halfway through a cohort.
DEFAULT_NOVA_VOICE: Final[str] = "matthew"


@dataclass(frozen=True, slots=True)
class Specialization:
    """One row of the Specialization Matrix.

    `persona` is a noun phrase, not a sentence ("an empathetic yet compliant
    Chief Human Resources Officer (CHRO)"), so build_instructions can embed it
    without rewording. `sample_question` is the question the PROBING phase
    must work in early; the model is told to rephrase it naturally, not recite
    it. `nova_voice` is the voice the role speaks with -- a CHRO should not
    sound like a CFO -- frozen onto the session at the handshake, because the
    model cannot be re-voiced once it has emitted audio.
    """

    key: str
    label: str
    persona: str
    frameworks: tuple[str, ...]
    sample_question: str
    # A CASTING decision, not a translation: the four roles are deliberately
    # spread across accents and registers, and a table that derived one voice
    # from another would silently recast a role the day a provider renamed one.
    # Defaulted so a row that predates the field still constructs --
    # nova_voice_for() then falls back to the configured generic voice.
    nova_voice: str = ""
    # The course the student was actually taught, module by module, when the
    # programme has one. `frameworks` is what a PRACTITIONER is assessed on and
    # is pitched at the industry bar; `syllabus` is what this cohort has sat in
    # a classroom for, and the two are not the same interview. Optional, and
    # empty for any track with no mapped course - build_instructions omits the
    # block entirely rather than composing an empty heading.
    syllabus: tuple[str, ...] = ()
    # The placement office's own question bank for this track, from the
    # voice-platform catalogue (app/voice_platform) - "[phase] question" lines
    # in the order they should be worked in. Empty for the four fixed MBA rows,
    # whose single `sample_question` predates it; build_instructions omits the
    # block entirely rather than composing an empty heading. Rule 1: this is
    # staff-authored text, never a student's record.
    question_bank: tuple[str, ...] = ()


# VERBATIM from the product spec -- persona wording, framework lists and
# sample questions are what every student is assessed against, same status as
# _INTERVIEWER_PERSONA in app/interview_core.py.
SPECIALIZATIONS: Final[dict[str, Specialization]] = {
    spec.key: spec
    for spec in (
        Specialization(
            key="hr",
            label="Human Resources (HR)",
            persona=(
                "an empathetic yet compliant Chief Human Resources Officer (CHRO)"
            ),
            frameworks=(
                "the STAR method",
                "behavioral competencies",
                "labor laws",
                "conflict resolution",
                "talent acquisition",
            ),
            sample_question=(
                "Walk me through how you would handle a sexual harassment "
                "claim involving a top-performing executive."
            ),
            # English (India), feminine. The one role in the matrix a student is
            # most likely to meet in their own campus drive, and the accent is
            # part of the rehearsal.
            nova_voice="kiara",
        ),
        Specialization(
            key="dm",
            label="Digital Marketing (DM)",
            persona="a growth-oriented, data-driven Chief Marketing Officer (CMO)",
            frameworks=(
                "CAC/LTV ratios",
                "ROAS",
                "SEO/SEM strategies",
                "A/B testing",
                "funnel optimization",
                "brand positioning",
            ),
            sample_question=(
                "Our CAC has increased by 40% on Meta ads this quarter. What "
                "is your step-by-step diagnostic framework?"
            ),
            # 22MDM23 Fundamentals of Digital Marketing, the course this cohort
            # sits. Compressed to what can be ASKED rather than recited: the
            # interviewer needs the shape of the syllabus and its vocabulary,
            # not the answer key. The source bank has 100 questions WITH model
            # answers; sending those upstream would let the model mark a student
            # against a memorised script, and would put ~15 kB into a string
            # that is re-sent on every phase change.
            syllabus=(
                "Module 1 - Foundations: digital versus traditional marketing, "
                "its advantages and limitations, the P-O-E-M framework (paid, "
                "owned and earned media), segmentation, target marketing, "
                "message customization, personalization, and the structure of a "
                "digital marketing plan.",
                "Module 2 - Display advertising: banner, video, rich-media and "
                "responsive formats; buying models; contextual, placement, "
                "interest, geographic, language, demographic and mobile "
                "targeting; remarketing; programmatic; YouTube advertising.",
                "Module 3 - Search advertising: ad placement and Ad Rank, "
                "keywords and keyword research, campaign construction, landing "
                "pages and landing-page relevance, performance reporting.",
                "Module 4 - Social and mobile: organic versus paid social, "
                "platform selection by audience and objective, engagement, "
                "influencer marketing, user-generated content, the mobile "
                "marketing toolkit, location-based services, QR codes, "
                "augmented reality, gamification, mobile analytics.",
                "Module 5 - SEO: crawling, indexing and ranking; on-page versus "
                "off-page SEO; long-tail keywords; backlinks; SEO maintenance; "
                "organic traffic; web analytics metrics.",
                "Metric arithmetic the student is expected to do out loud: "
                "CTR = clicks / impressions x 100; CPC = cost / clicks; "
                "CPM = cost / impressions x 1000; cost per conversion = cost / "
                "conversions; conversion rate = conversions / interactions x "
                "100; ROAS = attributed revenue / cost.",
            ),
            # English (US), feminine. A growth CMO in this cohort's target
            # companies is as often a video call from another country as a
            # person in the building.
            nova_voice="tiffany",
        ),
        Specialization(
            key="ba",
            label="Business Analytics (BA)",
            persona="a highly technical, problem-solving Director of Analytics",
            frameworks=(
                "SQL/Python logic",
                "data modeling",
                "predictive analytics",
                "A/B test statistical significance",
                "data visualization",
            ),
            sample_question=(
                "How would you design a machine learning pipeline to predict "
                "customer churn using messy e-commerce logs?"
            ),
            # English (India), masculine. Paired against HR deliberately: two
            # Indian-English voices of different registers, so the four roles
            # do not all sound like the same interviewer.
            nova_voice="arjun",
        ),
        Specialization(
            key="fa",
            label="Financial Analytics (FA)",
            persona="a sharp, risk-conscious Managing Director / CFO",
            frameworks=(
                "DCF modeling",
                "financial ratios",
                "risk mitigation",
                "valuation techniques",
                "M&A frameworks",
            ),
            sample_question=(
                "Walk me through how a $10 depreciation expense flows through "
                "the three financial statements."
            ),
            # English (US), masculine. The most formal of the four, which is the
            # register a Managing Director interview actually runs at.
            nova_voice="matthew",
        ),
    )
}


def get_specialization(key: str | None) -> Specialization | None:
    """The row for a client-supplied key, or None. Case-insensitive.

    None means two different things to the two callers, on purpose: the router
    treats None-for-a-given-key as a refusal (close 4010), while the relay
    treats a None KEY as the generic interview that predates the matrix.
    """
    if not key:
        return None
    return SPECIALIZATIONS.get(key.strip().lower())


def nova_voice_for(spec: Specialization | None) -> tuple[str, str]:
    """(the Nova voice to use, the name that was asked for).

    Both halves, because they differ exactly when something is wrong and the
    engine has to be able to SAY so: Nova answers an unknown voiceId with a
    ValidationException that ends the stream during the handshake, so a
    mistyped NOVA_SONIC_VOICE would otherwise present as "the interview never
    starts" with nothing naming the cause. Falling back keeps the interview,
    and returning the requested name lets the engine log what it ignored.

    `spec` is None for the generic interview that predates the matrix, which is
    the case NOVA_SONIC_VOICE exists for.
    """
    requested = (
        spec.nova_voice
        if spec is not None and spec.nova_voice
        else settings.nova_sonic_voice
    ).strip().lower()
    if requested in KNOWN_NOVA_VOICES:
        return requested, requested
    return DEFAULT_NOVA_VOICE, requested


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


class InterviewPhase(StrEnum):
    """The explicit lifecycle of one specialized interview.

    Driven by COMPLETED student answers (the relay sees one
    conversation.item.input_audio_transcription.completed per answer), never
    inferred from wall-clock time: a fast student and a deliberate one get the
    same arc.
    """

    OPENING = "opening"  # greet, set expectations, ask the student to self-introduce
    PROBING = "probing"  # framework-by-framework follow-ups incl. the sample question
    DEEP_DIVE = "deep_dive"  # one hard, ambiguous scenario, pressed on trade-offs
    WRAP_UP = "wrap_up"  # no new questions; spoken verdict and close
    ENDED = "ended"  # socket closed; terminal


# Answer counts at which the relay advances the phase. The interview cap is 15
# minutes; five substantive answers in that window is a full interview, so
# WRAP_UP after the fifth leaves the model room to deliver the verdict before
# the relay's own hard cap ends the session.
_PROBING_AFTER_ANSWERS: Final[int] = 1
_DEEP_DIVE_AFTER_ANSWERS: Final[int] = 3
_WRAP_UP_AFTER_ANSWERS: Final[int] = 5

# Every legal edge. The machine is small enough that an explicit map beats a
# convention: an illegal jump is a relay bug and must raise, not silently pass.
_TRANSITIONS: Final[dict[InterviewPhase, frozenset[InterviewPhase]]] = {
    InterviewPhase.OPENING: frozenset(
        {InterviewPhase.PROBING, InterviewPhase.WRAP_UP, InterviewPhase.ENDED}
    ),
    InterviewPhase.PROBING: frozenset(
        {InterviewPhase.DEEP_DIVE, InterviewPhase.WRAP_UP, InterviewPhase.ENDED}
    ),
    InterviewPhase.DEEP_DIVE: frozenset(
        {InterviewPhase.WRAP_UP, InterviewPhase.ENDED}
    ),
    InterviewPhase.WRAP_UP: frozenset({InterviewPhase.ENDED}),
    InterviewPhase.ENDED: frozenset(),
}


class InterviewStateMachine:
    """One interview's position in the arc. All state lives here per session.

    The relay owns the only instance and calls `student_answered()` from the
    user-transcript hook; a True return means the phase changed and the model
    needs the re-composed instructions. This class never does I/O, so it is
    unit-tested directly.
    """

    __slots__ = ("_specialization", "_phase", "_answers")

    def __init__(self, specialization: Specialization | None = None) -> None:
        self._specialization = specialization
        self._phase = InterviewPhase.OPENING
        self._answers = 0

    @property
    def specialization(self) -> Specialization | None:
        return self._specialization

    @property
    def phase(self) -> InterviewPhase:
        return self._phase

    @property
    def answers(self) -> int:
        return self._answers

    def student_answered(self) -> bool:
        """Record one completed student answer. True iff the phase changed.

        WRAP_UP is sticky: answers given during the verdict (the student
        thanking the interviewer, asking a closing question) must not re-arm
        question phases. ENDED absorbs everything.
        """
        if self._phase in (InterviewPhase.WRAP_UP, InterviewPhase.ENDED):
            return False
        self._answers += 1
        if self._answers >= _WRAP_UP_AFTER_ANSWERS:
            return self._transition_to(InterviewPhase.WRAP_UP)
        if self._answers >= _DEEP_DIVE_AFTER_ANSWERS:
            return self._transition_to(InterviewPhase.DEEP_DIVE)
        if self._answers >= _PROBING_AFTER_ANSWERS:
            return self._transition_to(InterviewPhase.PROBING)
        return False

    def force_wrap_up(self) -> bool:
        """Jump straight to WRAP_UP because the clock, not the arc, ran out.

        True iff the phase changed. The relay calls this when the session cap
        is close enough that a verdict started later would be cut off
        mid-sentence -- the failure this engine exists to remove is a
        fifteen-minute interview that ends with no verdict at all, and an early
        verdict beats no verdict. Deliberately NOT student_answered(), so a
        clock decision can never inflate the answer count: a mentor reading
        that number must be reading answers, not deadlines.
        """
        if self._phase in (InterviewPhase.WRAP_UP, InterviewPhase.ENDED):
            return False
        return self._transition_to(InterviewPhase.WRAP_UP)

    def end(self) -> None:
        """Terminal, from any phase -- the socket is closing."""
        self._transition_to(InterviewPhase.ENDED)

    def _transition_to(self, target: InterviewPhase) -> bool:
        if target is self._phase:
            return False
        if target not in _TRANSITIONS[self._phase]:
            # A jump the map does not allow is a bug in the caller, and raising
            # here is what keeps a miscounted threshold from producing a
            # directive the phase never warranted.
            raise ValueError(f"Illegal interview transition {self._phase} -> {target}")
        self._phase = target
        return True


# ---------------------------------------------------------------------------
# Instruction composition
# ---------------------------------------------------------------------------

# Per-phase steering, appended to the composed instructions and REPLACED on
# every phase change. These are directives to the model, not prose for the
# student, and they are deliberately imperative and short: everything else the
# interviewer needs is already in the base persona and the specialization row.
def _phase_directive(spec: Specialization, phase: InterviewPhase) -> str:
    frameworks = ", ".join(spec.frameworks)
    if phase is InterviewPhase.OPENING:
        return (
            f"Open the interview the way a real one begins: greet the student "
            f"briefly, introduce yourself in one sentence as {spec.persona} "
            "on the campus-hiring panel of a leading multinational, set "
            "expectations in one short sentence (a focused, structured "
            "conversation of about 15 minutes with a few questions and honest "
            "feedback at the end), then ask the student to introduce "
            f"themselves and say what drew them to {spec.label}. Ask nothing "
            "else yet -- no domain questions in this phase."
        )
    if phase is InterviewPhase.PROBING:
        if spec.syllabus:
            return (
                "Probe one topic at a time, moving across the syllabus modules "
                "rather than staying inside one. Mix definition questions with "
                "at least one where the student must compute a metric out loud "
                "from figures you give them. Assess against these frameworks: "
                f"{frameworks}. When an answer stays at the surface, ask a "
                'follow-up "why" or "how exactly" before moving on. Early in '
                "this phase, work in this question, rephrased naturally rather "
                f'than recited: "{spec.sample_question}".'
            )
        return (
            "Probe the core frameworks one at a time: "
            f"{frameworks}. When an answer stays at the surface, ask a "
            'follow-up "why" or "how exactly" before moving on. Early in this '
            "phase, work in this question, rephrased naturally rather than "
            f'recited: "{spec.sample_question}".'
        )
    if phase is InterviewPhase.DEEP_DIVE:
        if spec.syllabus:
            return (
                "Raise the difficulty. Present ONE realistic, ambiguous "
                f"{spec.label} scenario spanning at least two syllabus modules "
                "and having no textbook answer - a campaign whose numbers are "
                "healthy at one funnel stage and poor at the next is the shape "
                "to aim for. Press on trade-offs, risks and what they would "
                "measure, not on definitions they have already given you."
            )
        return (
            "Raise the difficulty. Present ONE realistic, ambiguous scenario "
            f"from {spec.label} and press on trade-offs, risks and edge cases "
            "rather than accepting a textbook answer."
        )
    if phase is InterviewPhase.WRAP_UP:
        return (
            "Ask no further questions. Deliver a concise spoken verdict on the "
            "whole interview: two genuine strengths, one priority improvement "
            "tied to the frameworks above, and one concrete drill to practise. "
            "Then thank the student and close the interview."
        )
    # ENDED produces no directive: nothing is sent upstream once the socket is
    # closing, and a caller composing instructions for ENDED has a bug.
    raise ValueError("ENDED has no phase directive")


# ---------------------------------------------------------------------------
# Answer validation
# ---------------------------------------------------------------------------

# Deliberately SHORT. Its job is to catch a cough and an acknowledgement, not to
# judge content: an interviewer that decided "not a real answer" from a word
# list would be marking students on vocabulary. Everything past that is the
# word-count floor, which a human can reason about and an operator can tune.
_FILLER_WORDS: Final[frozenset[str]] = frozenset(
    {
        "um", "uh", "erm", "hmm", "mm", "mhm", "yeah", "yes", "no", "ok",
        "okay", "sure", "right", "thanks", "thank", "you", "sorry", "what",
        "huh", "pardon",
    }
)

# WORDS IN ANY SCRIPT (2026-09-17). This was `[a-z0-9']+`, which is not a
# word pattern, it is an ENGLISH word pattern: a transcript in Devanagari --
# which is what Nova 2 Sonic wrote for an Indian-accented "hello, hi, I'm
# Darshan", because it auto-detects the language and the HR voice is also its
# Hindi voice -- matched nothing at all, so every answer was recorded `empty`
# ("the transcriber returned nothing"), a false fact about an interview that
# had five audible answers in it, and `is_skip_request` could not hear "अगला
# प्रश्न" ("next question"). A regex over `\w` is not the fix either: Python's
# `\w` excludes combining marks, and Hindi vowel signs and the virama are
# marks, so it shatters "प्रश्न" into four fragments. Words are runs of
# letters, digits and marks by Unicode category, with an apostrophe kept
# INSIDE a word so "don't" is one word and not two -- the floor measures how
# much the student said, and splitting contractions inflates it.
_WORD_CATEGORIES: Final[frozenset[str]] = frozenset({"L", "N", "M"})


def words_of(text: str) -> list[str]:
    """The words of a transcript, casefolded, in any script. Pure."""
    words: list[str] = []
    current: list[str] = []
    for char in text.casefold():
        if unicodedata.category(char)[0] in _WORD_CATEGORIES:
            current.append(char)
        elif char == "'" and current:
            current.append(char)
        elif current:
            words.append("".join(current).rstrip("'"))
            current = []
    if current:
        words.append("".join(current).rstrip("'"))
    return [word for word in words if word]

# WHAT A STUDENT SAYS WHEN THEY WANT TO MOVE ON RATHER THAN ANSWER (2026-09-17).
# "Next question" used to be two words -- `too_short` -- and was recorded as a
# failed answer while the model, which had heard it, moved on anyway. So the
# record said the student could not answer and the interviewer said "sure";
# neither the transcript a mentor reads nor the engine's own count agreed with
# what happened in the room. A skip is its own verdict now: it does not count
# as an answer (the arc stays where it is, so the questioning is not shortened
# by skipping), and the model is briefed to move on without pressing.
#
# Matched as WHOLE PHRASES on the normalised words, and only on a SHORT
# transcript: "let's skip the pleasantries -- I led the fintech club to..." is
# an answer that happens to contain the word. Kept small and literal on
# purpose. A fuzzy match here would turn "I'll pass the figures on to the
# board" into a skip, and every entry is a thing a student actually says to an
# interviewer when they want the next question.
_SKIP_PHRASES: Final[tuple[str, ...]] = (
    "next question",
    "next one",
    "go to the next",
    "skip this question",
    "skip this one",
    "skip this",
    "skip that",
    "skip it",
    "can we skip",
    "let's skip",
    "i'd like to skip",
    "i want to skip",
    "skip",
    "move on",
    "moving on",
    "i pass",
    "pass on this",
    "pass",
    # Hindi, because that is what the transcriber wrote for the first student
    # who tried it: "next question" and "next question (सवाल)". The interview
    # is briefed to run in English, but the record must still be true when a
    # student says it in Hindi.
    "अगला प्रश्न",
    "अगला सवाल",
)
# A skip is a sentence or two, never a paragraph. Above this many words the
# student is answering, whatever words the answer happens to contain.
_SKIP_MAX_WORDS: Final[int] = 12


def is_skip_request(transcript: str) -> bool:
    """Did the student ask for the next question rather than answer this one?

    Pure and deterministic, like the rest of the gate. Exposed on its own so the
    briefing, the local engine and the tests all read the same rule.
    """
    words = words_of(transcript)
    if not words or len(words) > _SKIP_MAX_WORDS:
        return False
    padded = f" {' '.join(words)} "
    return any(f" {phrase} " in padded for phrase in _SKIP_PHRASES)


def classify_answer(transcript: str) -> str:
    """'accepted' | 'empty' | 'skipped' | 'filler' | 'too_short'. No I/O, no
    model call.

    This runs between the student finishing and the interviewer replying, which
    is why it is a word count and not a judgement: a round trip here would be
    latency on every single turn of every interview.

    The verdicts are kept distinct rather than collapsed to a bool because
    they are stored on the turn record and read by a human afterwards -- "the
    transcriber returned nothing", "the student said 'yeah'" and "the student
    asked for the next question" are different facts about an interview, and a
    single False loses that.

    `skipped` is judged BEFORE the floor, and before the switch that turns the
    floor off: a student who says "next question" has not answered whatever the
    operator's word count is, and an engine that counted it would shorten the
    interview by one question every time somebody said it.
    """
    words = words_of(transcript)
    if not words:
        return "empty"
    if is_skip_request(transcript):
        return "skipped"
    floor = settings.interview_min_answer_words
    if floor <= 0:
        # 0 is a MEANING, not a typo (see config.py): the gate is off and every
        # transcript counts, which is exactly the pre-v3 behaviour.
        return "accepted"
    if all(word in _FILLER_WORDS for word in words):
        return "filler"
    if len(words) < floor:
        return "too_short"
    return "accepted"


# ---------------------------------------------------------------------------
# Instruction composition
# ---------------------------------------------------------------------------

# The per-response overrides for the turns that are NOT "ask the next question".
# Every one of them is a fixed string that says what the interviewer should do
# and never what the student said: the audio is already in the session and the
# model has it, so quoting it back would buy nothing and cost the property this
# module's header states.
_TURN_DIRECTIVES: Final[dict[str, str]] = {
    "clarify": (
        "The student's last answer was too brief to assess. Stay on the SAME "
        "question: ask one short, specific follow-up that tells them what kind "
        "of detail you are looking for. Do not move on to a new question, and "
        "do not repeat the question word for word."
    ),
    "unheard": (
        "You did not hear the student's last answer. Say so briefly and "
        "without blaming them, ask them to say it again, and stay on the same "
        "question."
    ),
    "resume": (
        "The student spoke before you had finished asking. Do not treat what "
        "they said as their answer: finish the question you were asking, or "
        "put it more briefly, and then wait for them."
    ),
    "skip": (
        "The student has asked to move on from this question. Do not press "
        "them and do not treat it as a failed answer: say in a few words that "
        "you will move on, then ask a different question at the same level. "
        "Do not repeat the question they skipped."
    ),
    "verdict": (
        "Stop the interview here, regardless of how many questions have been "
        "asked. If you have just asked a question the student has not "
        "answered yet, set it aside in one short sentence rather than waiting "
        "for the answer. Then deliver the closing verdict now: two genuine "
        "strengths, one priority improvement, and one concrete drill to "
        "practise. Then thank the student and close. Ask no further questions."
    ),
    "invite_questions": (
        "The questioning part of the interview is done. If you have just asked "
        "a question the student has not answered yet, set it aside in one "
        "short sentence rather than waiting for the answer. Before you give "
        "your verdict, hand the floor to the student the way a real interview "
        "ends: ask whether they have any questions for you about the role or "
        "the company. If they do, answer briefly and honestly in character, "
        "then wait. Do not deliver the verdict yet."
    ),
}

# The scorecard directive, issued once as a text-only response after the spoken
# verdict, in the SAME session -- which already holds the whole interview, so
# nothing about the student has to be sent to fetch it. It names no student and
# quotes no transcript, for the reason the block above gives.
#
# No response_format / json_schema: that parameter is unverified on both API
# generations and a rejected parameter costs the entire report. The JSON is
# DEMANDED here and PARSED DEFENSIVELY at the other end -- degrade, never
# assert.
REPORT_DIRECTIVE: Final[str] = (
    "The interview is over. Assess the student on this interview only -- what "
    "they actually said in this session, not what you assume about them.\n"
    "Reply with a single JSON object and nothing else: no prose, no code "
    "fence, no explanation before or after it. Use exactly these keys:\n"
    '{"overall": <integer 0-100>, "communication": <integer 0-100>, '
    '"domain": <integer 0-100>, "structure": <integer 0-100>, '
    '"strengths": [<2-3 short strings>], "improvements": [<1-2 short strings>], '
    '"drill": "<one concrete task they can practise this week>", '
    '"summary": "<one short paragraph addressed to the student>"}\n'
    "Score against the bar for a graduate hire. If the student barely spoke, "
    "say that in the summary and score it honestly rather than inventing "
    "detail they did not give you."
)


# How the interviewer SOUNDS, composed into every (specialization, phase)
# instruction right after the base persona and before the specialization
# block. The persona says what the interviewer DOES (one question at a time,
# micro-feedback); this says how it comes across out loud -- a realtime voice
# model reciting written-English scaffolding ("Firstly... Secondly...",
# bullet points) is the single biggest tell that the interview is a chatbot.
_DELIVERY_STYLE: Final[str] = (
    "You are speaking aloud, not writing, so sound like a real interviewer "
    "across the table. Begin each turn with one short, genuine acknowledgement "
    "of what the student just said -- vary the phrase, never the same one "
    "twice in a row -- then at most one or two sentences of feedback or "
    "follow-up, then your single question. Plain spoken sentences only: no "
    "numbered lists, no bullet points, no headings, no 'Firstly, Secondly' "
    "recitations. Warm but professional; this is a practice interview, not an "
    "interrogation."
)


def phase_directive(spec: Specialization, phase: InterviewPhase) -> str:
    """The phase's steering directive ALONE, with no persona in front of it.

    For an engine whose system prompt is set ONCE and cannot be replaced
    mid-session. The OpenAI relay re-sent the whole composition on every phase
    change because that is what a Realtime `session.update` takes;
    app/interview_nova.py cannot -- Nova 2 Sonic has no update event -- so it
    sends the persona once at the handshake and injects only what CHANGED as a
    control note. Every engine reads the same string from the same private
    function, so they cannot drift into steering students differently.
    """
    return _phase_directive(spec, phase)


def turn_directive(kind: str) -> str:
    """One entry of _TURN_DIRECTIVES, for the same reason as phase_directive.

    Raises on an unknown kind rather than returning "", because an engine that
    silently injected an empty directive would look exactly like a model
    ignoring it.
    """
    directive = _TURN_DIRECTIVES.get(kind)
    if directive is None:
        raise ValueError(f"Unknown turn kind {kind!r}")
    return directive


def build_turn_instructions(
    spec: Specialization | None,
    base_persona: str,
    phase: InterviewPhase,
    kind: str,
) -> str:
    """The per-response instruction override for one non-standard turn.

    `kind` is one of _TURN_DIRECTIVES. The result REPLACES the session persona
    for the single response it is attached to -- that is how a per-response
    `instructions` behaves upstream -- so it must be self-contained: the base
    persona verbatim, the specialization block and phase directive if there is
    one, and only then the turn directive. Composing the turn directive alone
    would drop the conduct rules and the rule-1 disclosure on exactly the turn
    where the interviewer is improvising.

    `spec` is None for the generic interview that predates the matrix: there is
    no specialization block to compose and the phase never advances, so the
    persona and the turn directive are the whole of it.
    """
    directive = _TURN_DIRECTIVES.get(kind)
    if directive is None:
        # A typo in a kind string would otherwise ship an interviewer with no
        # turn directive at all, which reads as the model ignoring the relay.
        raise ValueError(f"Unknown turn kind {kind!r}")
    head = base_persona if spec is None else build_instructions(spec, base_persona, phase)
    return f"{head}\n\n## This turn\n{directive}"


def build_instructions(
    spec: Specialization,
    base_persona: str,
    phase: InterviewPhase = InterviewPhase.OPENING,
) -> str:
    """The full instructions for one (specialization, phase) pair.

    `base_persona` comes first and VERBATIM -- it carries the conduct rules
    (one question at a time, micro-feedback, no interruptions) and the rule-1
    disclosure, and neither the specialization block nor the phase directive
    may dilute them. The delivery-style block follows it: how the interviewer
    SOUNDS, which is the same for every specialization and every phase. The
    relay calls this once at session.update time and again on every phase
    change, so it must be a pure function of its arguments.
    """
    frameworks = ", ".join(spec.frameworks)
    syllabus_block = ""
    if spec.syllabus:
        modules = "\n".join(f"- {line}" for line in spec.syllabus)
        syllabus_block = (
            "\n## The course this student has actually studied\n"
            "Draw your questions from this syllabus and use its vocabulary. "
            "Where the syllabus and the frameworks above disagree on level, the "
            "syllabus decides the QUESTION and the frameworks set the bar for "
            "the ANSWER: ask what they were taught, then press for the "
            "commercial judgement the framework list implies.\n"
            f"{modules}\n"
        )
    bank_block = ""
    if spec.question_bank:
        lines = "\n".join(f"- {line}" for line in spec.question_bank)
        bank_block = (
            "\n## Question bank for this track\n"
            "Work these questions in, in this order, one at a time and only "
            "when the arc reaches the phase in brackets. Rephrase each "
            "naturally rather than reciting it, and never ask the next one "
            "until the student has answered the current one. They are a "
            "guide to coverage, not a script: follow up on what the student "
            "actually says before moving on.\n"
            f"{lines}\n"
        )
    return (
        f"{base_persona}\n"
        "\n"
        f"{_DELIVERY_STYLE}\n"
        "\n"
        f"## Specialization: {spec.label}\n"
        f"For this session you are {spec.persona}, interviewing this student "
        f"for a {spec.label} role. Assess their command of these core "
        f"frameworks and concepts over the course of the interview: "
        f"{frameworks}.\n"
        f"{syllabus_block}"
        f"{bank_block}"
        "\n"
        f"## Current phase: {phase.value}\n"
        f"{_phase_directive(spec, phase)}"
    )


# The skip rule as the model hears it, stated once and quoted by the arc
# briefing so the model and `classify_answer` agree on what a skip is worth:
# nothing, and no pressure. It names the same phrases the gate matches, so a
# student is not told to say one thing by the interviewer and judged on another.
SKIP_RULE: Final[str] = (
    'If the student says "next question", "skip" or "pass", or otherwise asks '
    "to move on, do not press them and do not treat it as a failed answer: say "
    "in a few words that you will move on, and ask a different question at the "
    "same level. A skipped question does not count as an answer."
)


def build_arc_briefing(spec: Specialization) -> str:
    """The question phases of the arc, briefed ONCE, for an engine that owns the turn.

    app/interview_nova.py appends this to the handshake instructions and never
    sends a phase directive during the interview. It cannot: Nova answers the
    student on its own the moment they stop speaking, and the only way to put
    text in front of it mid-session is a USER turn, which barges in on the
    reply it is composing -- and one held until that reply has finished
    provokes a second question on top of the one the student has just been
    asked. So the model is told, up front, what changes after the first and
    the third answer that counts, and steers itself; the engine's phase
    machine ticks on the same rule for the RECORD and the browser's stepper.

    The rule is `classify_answer`'s, stated in words, so that the model and
    the record agree on which answers count as closely as a model can be made
    to agree with a word count. The two beats that must land at a fixed point
    -- the invitation to ask questions and the verdict -- are deliberately
    NOT here: the interview system sends those, between turns, because a model
    that decides for itself when the questioning is over can decide it one
    answer before the engine does and then wait for a verdict directive the
    engine will never send.

    The directives are `_phase_directive`'s own strings, quoted rather than
    paraphrased, so every engine steers a student with the same words.
    """
    floor = settings.interview_min_answer_words
    counting_rule = (
        "every answer counts, however short"
        if floor <= 0
        else (
            f"an answer counts when it is a real answer of at least {floor} "
            'words and not just filler such as "yes", "okay", "hmm" or '
            '"thanks"; a cough, a one-word acknowledgement or silence does not '
            "count, and on those you simply ask, briefly, for more"
        )
    )
    return (
        "## How this interview unfolds\n"
        "You are in the opening phase now. The interview system counts the "
        f"student's answers, and {counting_rule}. "
        f"{SKIP_RULE}\n"
        "After the student's FIRST answer that counts, move to the probing "
        "phase and stay in it. In the probing phase: "
        f"{_phase_directive(spec, InterviewPhase.PROBING)}\n"
        "After the student's THIRD answer that counts, move to the deep-dive "
        "phase and stay in it. In the deep-dive phase: "
        f"{_phase_directive(spec, InterviewPhase.DEEP_DIVE)}\n"
        "Move through these phases in this order only, and never use a later "
        "phase's material before its phase. Keep asking one question at a "
        "time until the interview system tells you otherwise: it will tell "
        "you when the questioning is over, when to invite the student's own "
        "questions and when to deliver your verdict. Never end the interview "
        "or give a verdict on your own."
    )
