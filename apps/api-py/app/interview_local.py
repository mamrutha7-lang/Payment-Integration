"""The LOCAL interview engine — the same interview, with no API key.

    browser  <--WS /api/interview-->  THIS PROCESS  ---> nothing leaves it

app/interview_nova.py sends 24 kHz PCM to Amazon Nova 2 Sonic and gets speech
back from one model that hears, reasons and speaks. That model has no local
equivalent, so this module rebuilds the same behaviour out of three pieces that
do: faster-whisper hears, an Ollama model reasons, Piper speaks.

WHY IT IS A DROP-IN AND NOT A REWRITE. `LocalSession` takes the constructor
that `_RelaySession` takes and returns what `run()` returns, so the router
chooses between them on one setting and everything around the engine is
untouched -- consent, the concurrency limiter, turn writes, the finalizer, the
recorder and the three-layer session close all keep working because none of
them ever knew which engine was speaking.

More importantly it emits the SAME downstream events. The browser handles the
OpenAI Realtime event names (`input_audio_buffer.speech_started`,
`...transcription.completed`, `response.created`, `response.done`) plus the five
`reep.*` controls, so this engine synthesises those names from its own pipeline
and the Angular client needs no change at all. The event vocabulary is the
contract; where the audio was produced is an implementation detail.

WHAT UPSTREAM USED TO DO FOR FREE, AND NOW HAS TO BE BUILT:

  voice activity   A speech-to-speech model tells you when the student started
                   and stopped. Here that is an RMS gate with a hangover, below.
  turn taking      There is no server VAD to commit a turn, so `_Vad` deciding
                   silence IS the end of the turn.

Everything ABOVE the audio is unchanged and deliberately shared with the relay:
the phase machine, `classify_answer`, and `build_instructions` all come from
app/interview_matrix.py. A student sitting the local interview is assessed by
the same arc, the same word-count gate and the same instructions as one sitting
the hosted interview. If those two ever diverge, the scorecards stop being
comparable and the whole record becomes untrustworthy.

RULE 1 (AGENTS.md) is satisfied here by construction rather than by a gate:
nothing leaves the machine, so there is no egress to allow or refuse. That is a
stronger guarantee than the relay's, not a weaker one -- but it is not a licence
to compose student records into the prompt. The instructions are the same fixed
strings for the same reason they are there: the moment student text enters an
instruction string, the next editor puts a resume in it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from .config import settings
from .interview_matrix import (
    REPORT_DIRECTIVE,
    InterviewPhase,
    InterviewStateMachine,
    Specialization,
    build_instructions,
    build_turn_instructions,
    classify_answer,
)

log = logging.getLogger(__name__)


def _add_cuda_dlls() -> None:
    """Put the pip-installed NVIDIA runtime on the DLL search path (Windows).

    CTranslate2 resolves cuBLAS by bare name through the ordinary search order,
    which reads PATH and never consults the user-directory list, so installing
    nvidia-cublas-cu12 is necessary and not sufficient. Without this the model
    loads and then dies at the first encode with "Library cublas64_12.dll is
    not found" -- seconds into what looks like a working session. Harmless on
    Linux, where the wheels' RPATH already handles it.
    """
    if os.name != "nt":
        return
    root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    dirs = [str(b) for b in sorted(root.glob("*/bin"))]
    if not dirs:
        return
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except OSError:
            pass
    os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")


# --------------------------------------------------------------------------- audio

# The browser link is unchanged from the relay: raw PCM16 LE mono at 24 kHz in
# both directions, because the client's capture and playback are built for it.
_CLIENT_RATE_HZ: Final[int] = 24_000
# Whisper is trained at 16 kHz and resamples internally otherwise; doing it here
# keeps one resampler in one place.
_STT_RATE_HZ: Final[int] = 16_000
_CHUNK_MS: Final[int] = 40

# --- voice activity -------------------------------------------------------
# An RMS gate, not a neural VAD. The student's microphone is the only thing on
# this link and the interviewer's own audio is already suppressed client-side by
# the echo gate, so the hard case a neural VAD earns its cost on -- speech
# against speech -- does not arise here. If it ever does, this is the seam.
#
# Tuned against the recorded interviews in var/interview-audio: speech in those
# sits at RMS 0.02-0.15 and room tone below 0.004.
_VAD_SPEECH_RMS: Final[float] = 0.012
# Consecutive speech chunks before a turn opens. Three (120 ms) is a syllable;
# one is a cough or a chair, and that is the same reasoning the client's own
# barge-in gate uses.
_VAD_OPEN_CHUNKS: Final[int] = 3
# Silence before the turn is considered finished. This IS the turn-taking
# latency: too short and the interviewer interrupts a student who is thinking
# mid-sentence, too long and the conversation feels dead. 700 ms is roughly the
# pause people leave between sentences without meaning to yield the floor.
_VAD_HANGOVER_MS: Final[int] = 700
_VAD_HANGOVER_CHUNKS: Final[int] = _VAD_HANGOVER_MS // _CHUNK_MS
# Anything shorter than this is not an answer, whatever the transcriber makes of
# it. It also bounds the cost of a noisy room: a burst that opens the gate and
# closes again produces no STT call at all.
_MIN_UTTERANCE_MS: Final[int] = 400
# A student who never stops talking still has to yield eventually, or the phase
# machine never ticks. Long enough that a considered answer is never cut.
_MAX_UTTERANCE_S: Final[int] = 90

# --- generation -----------------------------------------------------------
# A clause is speakable at punctuation; waiting for the whole sentence adds the
# rest of it to time-to-first-audio for nothing, because Piper synthesises the
# remainder while the first chunk is already playing.
_CLAUSE_ENDS: Final[str] = ".?!,;:"
_MIN_CLAUSE_CHARS: Final[int] = 12
_MAX_CLAUSE_CHARS: Final[int] = 90

# Turns of conversation carried into each generation. Twelve is roughly six
# exchanges, which covers the whole arc at five accepted answers while staying
# far inside num_ctx: the instructions alone are ~3.4 kB with a syllabus, and a
# prompt that overflows the window is silently truncated from the FRONT, which
# would drop the persona and the rule-1 disclosure first.
_HISTORY_TURNS: Final[int] = 12

# The sender strings the ROUTER'S writer understands, named rather than spelled
# at each call site. It maps `speaker = "student" if sender == "user" else
# "interviewer"`, so "student" -- the obvious guess -- lands in the else branch
# and files the student's own answer under the interviewer's name. It is a
# silent corruption: the row is written, the counters match, and only a human
# reading the transcript back sees the interviewer saying the student's words.
_SENDER_STUDENT: Final[str] = "user"
_SENDER_INTERVIEWER: Final[str] = "assistant"

# The nudge sent when the student has not spoken yet. Length is stated HERE and
# not in the persona: the persona is verbatim product spec shared with the
# hosted engine, and a hosted model obeys "one question at a time" from it
# without help. A 3B model does not -- left alone it opened with 19.6 SECONDS of
# speech, which is a monologue and not an interview question. Constraining by
# tokens instead would truncate mid-sentence, which is worse than long.
# Appended to the composed instructions on EVERY local turn.
#
# Not in _INTERVIEWER_PERSONA, which is verbatim product spec shared with the
# hosted engine and must stay byte-identical between them: a student's answers
# are assessed against that text, and a local-only sentence inside it would make
# two students' scorecards incomparable. This is about DELIVERY, not assessment,
# which is why it can differ per engine without that consequence.
#
# It exists because a 3B model does not hold "one question at a time" from the
# persona the way a frontier model does. Measured: 19.6 s of speech for an
# opening question, and 31 s for a follow-up once history gave it more to react
# to. A question nobody can sit through is not a better question.
_LOCAL_DELIVERY: Final[str] = (
    "\n\n## Delivery\n"
    "You are SPEAKING, not writing.\n"
    "- Ask exactly ONE question, in one or two short sentences, then stop.\n"
    "- Never narrate the student. Do not say what they seem to be doing, "
    "feeling or struggling with, and do not paraphrase their answer back to "
    "them. Observed in a real session: every reply began 'It seems like "
    "you're...', which is commentary and not an interview.\n"
    "- Do not list options, and do not explain why you are asking.\n"
    "- End with the question itself, so the student knows it is their turn."
)

_NUDGE: Final[str] = (
    "Ask your next question now. One or two sentences at most, then stop and "
    "wait for the student to answer."
)

_CLOSE_OK: Final[int] = 1000
_CLOSE_INTERNAL: Final[int] = 1011
_CLOSE_IDLE: Final[int] = 4008
_CLOSE_CAP: Final[int] = 4009
_CLOSE_ENGINE: Final[int] = 4002


def _rms(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))


def _resample(x: np.ndarray, src_hz: int, dst_hz: int) -> np.ndarray:
    """Linear resample. Adequate here and deliberately dependency-free.

    Speech at these rates is band-limited well below Nyquist on both sides, and
    the alternative is a scipy dependency in the API image for a filter nobody
    would hear.
    """
    if src_hz == dst_hz or x.size == 0:
        return x
    n = int(round(x.size * dst_hz / src_hz))
    return np.interp(
        np.linspace(0, x.size - 1, n), np.arange(x.size), x
    ).astype(np.float32)


# --------------------------------------------------------------------------- models

# Loaded ONCE for the process, not once per interview: a WhisperModel costs ~1 s
# and several hundred MB of VRAM to build, and paying that per student would put
# it on the critical path of every session's first answer. Guarded because two
# sockets opening together would otherwise each build one.
_MODELS: dict[str, Any] = {}
_MODELS_LOCK = asyncio.Lock()


async def _get_models() -> tuple[Any, Any]:
    async with _MODELS_LOCK:
        if "stt" not in _MODELS:
            _add_cuda_dlls()
            _MODELS["stt"], _MODELS["tts"] = await asyncio.to_thread(_build_models)
    return _MODELS["stt"], _MODELS["tts"]


def _build_models() -> tuple[Any, Any]:
    from faster_whisper import WhisperModel
    from piper import PiperVoice

    size = settings.interview_local_stt_model
    device = settings.interview_local_stt_device
    try:
        stt = WhisperModel(size, device=device, compute_type="float16" if device == "cuda" else "int8")
    except Exception as exc:  # noqa: BLE001 - any CUDA failure means fall back
        # A missing cuDNN, a busy card or a driver mismatch all land here, and
        # every one of them is survivable at a cost in speed. Refusing to start
        # the interview over it would be the wrong trade.
        log.warning("Local STT could not start on %s (%s); falling back to cpu/int8", device, exc)
        stt = WhisperModel(size, device="cpu", compute_type="int8")

    voice_path = Path(settings.interview_local_tts_voice)
    if not voice_path.is_absolute():
        voice_path = Path(__file__).resolve().parent.parent / voice_path
    tts = PiperVoice.load(str(voice_path))
    log.info("Local interview models ready (stt=%s, tts=%s)", size, voice_path.name)
    return stt, tts


# --------------------------------------------------------------------------- vad


class _Vad:
    """Turn boundaries from chunk energy. Holds no audio, only the decision."""

    __slots__ = ("_open", "_above", "_below")

    def __init__(self) -> None:
        self._open = False
        self._above = 0
        self._below = 0

    @property
    def speaking(self) -> bool:
        return self._open

    def feed(self, rms: float) -> str | None:
        """'started' | 'stopped' | None for one 40 ms chunk."""
        if rms >= _VAD_SPEECH_RMS:
            self._above += 1
            self._below = 0
            if not self._open and self._above >= _VAD_OPEN_CHUNKS:
                self._open = True
                return "started"
            return None
        self._above = 0
        if self._open:
            self._below += 1
            if self._below >= _VAD_HANGOVER_CHUNKS:
                self._open = False
                self._below = 0
                return "stopped"
        return None

    def reset(self) -> None:
        self._open = False
        self._above = 0
        self._below = 0


# --------------------------------------------------------------------------- session

# The payload records are IMPORTED, not redefined. They are the contract between
# an engine and the router's writers, and a parallel definition here would drift
# the moment one of them gained a field -- silently, because both would still
# construct and only one would carry the new value into the database.
from .interview_audio import TRACK_INTERVIEWER, TRACK_STUDENT  # noqa: E402
from .interview_core import (  # noqa: E402
    _INTERVIEWER_PERSONA,
    _ReportRecord,
    _SessionOutcome,
    _TurnRecord,
)


class LocalSession:
    """One interview, entirely on this machine.

    Mirrors `_RelaySession`'s constructor and `run()` contract so the router can
    choose between them on a setting alone.
    """

    def __init__(
        self,
        websocket: WebSocket,
        conn_id: str,
        *,
        on_turn: Callable[[str, str, str, _TurnRecord], None] | None = None,
        specialization: Specialization | None = None,
        on_report: Callable[[_ReportRecord], None] | None = None,
        on_finalize: Callable[[_SessionOutcome], None] | None = None,
        on_heartbeat: Callable[[], None] | None = None,
        recorder: Any | None = None,
        max_seconds: int | None = None,
    ) -> None:
        # Same per-session cap the Nova engine takes (the voice platform's
        # time-limit rows); None means INTERVIEW_MAX_SECONDS, as always.
        self._max_seconds = int(max_seconds) if max_seconds else None
        self._ws = websocket
        self._conn_id = conn_id
        self._on_turn = on_turn
        self._on_report = on_report
        self._on_finalize = on_finalize
        self._on_heartbeat = on_heartbeat
        self._recorder = recorder
        self._machine = InterviewStateMachine(specialization)
        self._log = log

        self._stt: Any = None
        self._tts: Any = None
        self._vad = _Vad()
        self._buf: list[np.ndarray] = []
        self._buf_samples = 0
        self._stop_code: int | None = None
        self._stop_reason = ""
        self._seq = 0
        self._turns_emitted = 0
        self._turns_persisted = 0
        self._report_status: str | None = None
        # The conversation so far, as chat messages. The hosted relay never
        # needed this: the audio IS the session upstream, so the model already
        # had every turn. A local chat model is stateless between calls, so
        # without this the interviewer greets the student on every single turn
        # and can never ask a follow-up -- which is the whole of PROBING.
        #
        # Student text lives HERE and never in the instructions. That is the
        # same line app/interview_nova.py draws: instructions stay fixed
        # strings, and what the student said travels as a message. The
        # distinction is what stops the next editor putting a resume in the
        # persona.
        self._history: list[dict[str, str]] = []
        # The two-beat close, mirroring the relay: the tick into WRAP_UP speaks
        # an "any questions for us?" turn instead of the verdict, and the
        # student's reply -- any reply, never classify_answer'd, because "no,
        # I'm good" is filler words to the gate -- is what earns the verdict.
        self._awaiting_candidate_questions = False
        self._started_at = time.monotonic()
        self._last_audio_at = time.monotonic()
        self._speaking = False

    # -- lifecycle ---------------------------------------------------------

    def request_stop(self, code: int, reason: str) -> None:
        """Ask this session to end. Safe from another task; never blocks."""
        if self._stop_code is None:
            self._stop_code = code
            self._stop_reason = reason[:120]

    async def run(self) -> tuple[int, str]:
        try:
            self._stt, self._tts = await _get_models()
        except Exception:
            self._log.exception("[conn=%s] Local engine failed to start", self._conn_id)
            await self._send_json(
                {"type": "reep.error", "message": "The local interview engine is unavailable."}
            )
            return _CLOSE_ENGINE, "Local engine unavailable"

        await self._send_ready()
        # The opening question is spoken BEFORE any student audio, which is what
        # makes the arc start at OPENING rather than waiting for someone to talk
        # first into a silence they were never told to fill.
        await self._speak_turn(kind=None)

        code, reason = _CLOSE_OK, "Interview complete"
        try:
            code, reason = await self._loop()
        except WebSocketDisconnect:
            code, reason = _CLOSE_OK, "Client disconnected"
        finally:
            self._machine.end()
            self._finalize(code, reason)
        return code, reason

    # -- the loop ----------------------------------------------------------

    async def _loop(self) -> tuple[int, str]:
        cap = self._max_seconds or settings.interview_max_seconds
        idle = settings.interview_idle_seconds
        while True:
            if self._stop_code is not None:
                return self._stop_code, self._stop_reason
            now = time.monotonic()
            if now - self._started_at >= cap:
                await self._force_wrap_up()
                return _CLOSE_CAP, "Session cap reached"
            if now - self._last_audio_at >= idle:
                return _CLOSE_IDLE, f"No audio received for {idle}s"

            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=1.0)
            except asyncio.TimeoutError:
                # The tick that lets the caps above be checked on a quiet link.
                self._beat()
                continue

            if msg.get("type") == "websocket.disconnect":
                return _CLOSE_OK, "Client disconnected"

            data = msg.get("bytes")
            text = msg.get("text")
            if data is not None:
                await self._on_audio(data)
            elif text is not None:
                verdict = self._on_control(text)
                if verdict is not None:
                    return verdict

    def _on_control(self, text: str) -> tuple[int, str] | None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if payload.get("type") == "reep.end":
            return _CLOSE_OK, "Student ended the interview"
        # The client's echo-gate state is informational here: this engine runs no
        # server VAD that the interviewer's own voice could fool, so there is
        # nothing to suppress. Accepted and ignored rather than rejected, so the
        # client needs no branch for which engine it got.
        return None

    async def _on_audio(self, data: bytes) -> None:
        self._last_audio_at = time.monotonic()
        self._beat()
        if self._recorder is not None:
            self._recorder.feed(TRACK_STUDENT, data)
        if self._speaking:
            # Barge-in is NOT implemented: while the interviewer is talking the
            # student's audio is recorded and dropped rather than interrupting.
            # Stated here rather than swallowed silently -- see the module
            # header's list of what upstream used to do for free.
            return

        pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        if pcm.size == 0:
            return
        event = self._vad.feed(_rms(pcm))
        if self._vad.speaking or event == "stopped":
            self._buf.append(pcm)
            self._buf_samples += pcm.size

        if event == "started":
            await self._send_json({"type": "input_audio_buffer.speech_started"})
        elif event == "stopped":
            await self._send_json({"type": "input_audio_buffer.speech_stopped"})
            await self._end_of_turn()
        elif self._buf_samples >= _MAX_UTTERANCE_S * _CLIENT_RATE_HZ:
            self._vad.reset()
            await self._send_json({"type": "input_audio_buffer.speech_stopped"})
            await self._end_of_turn()

    # -- one student turn --------------------------------------------------

    async def _end_of_turn(self) -> None:
        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, dtype=np.float32)
        self._buf, self._buf_samples = [], 0
        duration_ms = audio.size / _CLIENT_RATE_HZ * 1000

        if duration_ms < _MIN_UTTERANCE_MS:
            # Too short to be an answer and too short to be worth an STT call.
            # Recorded as a turn anyway: "the student made a noise the engine did
            # not accept" is a fact a mentor may need, and dropping it is how
            # turns_emitted and turns_persisted quietly stop matching.
            self._emit_turn(_SENDER_STUDENT, "", "empty", "empty", counted=False)
            await self._speak_turn(kind="unheard")
            return

        transcript = await asyncio.to_thread(self._transcribe, audio)
        status = "completed" if transcript else "failed"
        await self._send_json(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": transcript,
            }
        )

        if self._awaiting_candidate_questions:
            # The reply to "any questions for you?" -- bypassed past
            # classify_answer entirely, exactly as the relay does: "no, I'm
            # good" is filler words to the gate and must not earn a clarify
            # loop on the final turn. A failed transcription goes the same
            # way; a real interviewer just closes.
            self._awaiting_candidate_questions = False
            self._emit_turn(_SENDER_STUDENT, transcript, status, None, counted=False)
            await self._speak_turn(kind="verdict")
            await self._produce_report()
            self.request_stop(_CLOSE_OK, "Interview complete")
            return

        quality = classify_answer(transcript)
        counted = quality == "accepted"
        self._emit_turn(_SENDER_STUDENT, transcript, status, quality, counted=counted)

        if not counted:
            # A skip is neither unheard nor too brief: the student asked for the
            # next question, and the directive says to give them one without
            # pressing. Clarifying a skip would be the interviewer refusing to
            # take "next question" for an answer.
            if quality == "empty":
                kind = "unheard"
            elif quality == "skipped":
                kind = "skip"
            else:
                kind = "clarify"
            await self._speak_turn(kind=kind)
            return

        phase_changed = self._machine.student_answered()
        if phase_changed:
            await self._announce_phase()

        if self._machine.phase is InterviewPhase.WRAP_UP:
            # The questioning is done, but a real interview ends with "any
            # questions for us?" BEFORE the verdict. The reply is handled by
            # the bypass above; the verdict and the report follow it.
            self._awaiting_candidate_questions = True
            await self._speak_turn(kind="invite_questions")
            return
        await self._speak_turn(kind=None)

    def _transcribe(self, audio24: np.ndarray) -> str:
        """Decode one utterance. Settings measured, not guessed.

        `condition_on_previous_text=False` because each call here is ONE turn,
        not a continuation: conditioning lets a mis-decode at the start of an
        interview propagate through every later answer, and a wrong transcript
        does not merely read badly -- it clears the answer-word floor, counts as
        an accepted answer and advances the phase machine on nothing.
        """
        a16 = _resample(audio24, _CLIENT_RATE_HZ, _STT_RATE_HZ)
        segments, _info = self._stt.transcribe(
            a16,
            language="en",
            beam_size=settings.interview_local_stt_beam,
            vad_filter=settings.interview_local_stt_vad_filter,
            condition_on_previous_text=False,
            initial_prompt=settings.interview_local_stt_prompt.strip() or None,
        )
        return "".join(segment.text for segment in segments).strip()

    # -- one interviewer turn ----------------------------------------------

    async def _speak_turn(self, *, kind: str | None) -> None:
        """Generate the next interviewer utterance and stream it as audio.

        The model streams; each completed clause is synthesised and sent while
        the rest is still generating, which is what keeps time-to-first-audio
        near the clause rather than near the whole sentence.
        """
        spec = self._machine.specialization
        phase = self._machine.phase
        if spec is None:
            instructions = _INTERVIEWER_PERSONA
        elif kind is None:
            instructions = build_instructions(spec, _INTERVIEWER_PERSONA, phase)
        else:
            instructions = build_turn_instructions(spec, _INTERVIEWER_PERSONA, phase, kind)

        instructions += _LOCAL_DELIVERY

        await self._send_json({"type": "response.created"})
        self._speaking = True
        spoken: list[str] = []
        try:
            async for clause in self._stream_clauses(instructions):
                spoken.append(clause)
                await self._speak(clause)
        except Exception:
            self._log.exception("[conn=%s] Local generation failed", self._conn_id)
        finally:
            self._speaking = False
            self._vad.reset()
            await self._send_json({"type": "response.done"})

        text = "".join(spoken).strip()
        if text:
            self._emit_turn(_SENDER_INTERVIEWER, text, "completed", None, counted=False)

    async def _stream_clauses(self, instructions: str):
        """Yield speakable clauses from the local model as they are generated."""
        body = {
            "model": _worker_model(),
            "stream": True,
            "keep_alive": settings.interview_local_keep_alive,
            "messages": [
                {"role": "system", "content": instructions},
                *self._history,
                # A trailing nudge only when the student has not spoken yet.
                # After that the last message is already theirs, and appending
                # an instruction behind it makes a 3B model answer the
                # instruction instead of the student.
                *(
                    []
                    if self._history and self._history[-1]["role"] == "user"
                    else [{"role": "user", "content": _NUDGE}]
                ),
            ],
            "options": {
                "num_ctx": settings.interview_local_num_ctx,
                "temperature": 0.4,
                "num_predict": settings.interview_local_max_tokens,
            },
        }
        buf = ""
        async with _ollama_client().stream(
            "POST", f"{_ollama_base()}/api/chat", json=body
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"ollama HTTP {response.status_code}")
            async for line in response.aiter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                buf += (obj.get("message") or {}).get("content") or ""
                while True:
                    cut = _clause_cut(buf)
                    if cut is None:
                        break
                    yield buf[:cut]
                    buf = buf[cut:].lstrip()
                if obj.get("done"):
                    break
        if buf.strip():
            yield buf

    async def _speak(self, text: str) -> None:
        pcm = await asyncio.to_thread(self._synthesize, text)
        if pcm.size == 0:
            return
        raw = pcm.tobytes()
        if self._recorder is not None:
            self._recorder.feed(TRACK_INTERVIEWER, raw)
        # Sent in client-sized chunks rather than one large frame: the browser's
        # jitter buffer schedules per buffer, and one three-second frame would
        # arrive as a single scheduling event with nothing to smooth a late one.
        step = int(_CLIENT_RATE_HZ * _CHUNK_MS / 1000) * 2
        for i in range(0, len(raw), step):
            if self._ws.client_state is not WebSocketState.CONNECTED:
                return
            await self._ws.send_bytes(raw[i : i + step])

    def _synthesize(self, text: str) -> np.ndarray:
        chunks: list[np.ndarray] = []
        rate = _CLIENT_RATE_HZ
        for chunk in self._tts.synthesize(text):
            chunks.append(np.frombuffer(chunk.audio_int16_bytes, dtype="<i2"))
            rate = chunk.sample_rate
        if not chunks:
            return np.zeros(0, dtype="<i2")
        pcm = np.concatenate(chunks).astype(np.float32)
        if rate != _CLIENT_RATE_HZ:
            pcm = _resample(pcm, rate, _CLIENT_RATE_HZ)
        return np.clip(pcm, -32768, 32767).astype("<i2")

    # -- wrap up and the scorecard ----------------------------------------

    async def _force_wrap_up(self) -> None:
        if self._awaiting_candidate_questions:
            # The clock ran out while the student was thinking of a question to
            # ask us. Skip the rest of the beat -- a real interviewer who is
            # out of time just closes.
            self._awaiting_candidate_questions = False
            await self._speak_turn(kind="verdict")
            await self._produce_report()
            return
        if self._machine.force_wrap_up():
            # The forced path goes straight to the verdict, never through the
            # candidate-questions beat.
            await self._announce_phase()
            await self._speak_turn(kind="verdict")
            await self._produce_report()

    async def _produce_report(self) -> None:
        """One text-only generation. Never spoken, never in the chat history."""
        spec = self._machine.specialization
        base = (
            build_instructions(spec, _INTERVIEWER_PERSONA, InterviewPhase.WRAP_UP)
            if spec is not None
            else _INTERVIEWER_PERSONA
        )
        raw = ""
        report: dict[str, Any] | None = None
        try:
            raw = await asyncio.wait_for(
                self._complete(f"{base}\n\n{REPORT_DIRECTIVE}"),
                timeout=settings.interview_report_timeout_ms / 1000,
            )
            report = _parse_report(raw)
            status = "ok" if report is not None else "unparseable"
        except asyncio.TimeoutError:
            status = "timeout"
        except Exception:
            self._log.exception("[conn=%s] Local report generation failed", self._conn_id)
            status = "failed"

        self._report_status = status
        self._dispatch(
            self._on_report, _ReportRecord(status, report, raw, _worker_model()), "report"
        )
        await self._send_json(
            {
                "type": "reep.report",
                "available": status == "ok",
                "reason": None if status == "ok" else status,
                "report": report if status == "ok" else None,
            }
        )

    async def _complete(self, instructions: str) -> str:
        body = {
            "model": _worker_model(),
            "stream": False,
            "keep_alive": settings.interview_local_keep_alive,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": "Produce the scorecard now."},
            ],
            "options": {
                "num_ctx": settings.interview_local_num_ctx,
                "temperature": 0.1,
                "num_predict": 900,
            },
        }
        response = await _ollama_client().post(f"{_ollama_base()}/api/chat", json=body)
        response.raise_for_status()
        return (response.json().get("message") or {}).get("content") or ""

    # -- plumbing ----------------------------------------------------------

    async def _send_ready(self) -> None:
        spec = self._machine.specialization
        await self._send_json(
            {
                "type": "reep.ready",
                "session_id": self._conn_id,
                "conn_id": self._conn_id,
                "audio": {
                    "format": "pcm16",
                    "sample_rate": _CLIENT_RATE_HZ,
                    "chunk_ms": _CHUNK_MS,
                },
                "limits": {
                    "session_max_seconds": self._max_seconds or settings.interview_max_seconds,
                    "idle_max_seconds": settings.interview_idle_seconds,
                },
                "specialization": spec.label if spec is not None else None,
                "phase": self._machine.phase.value,
                # The client does not branch on this; it is here so a support
                # conversation can start from the transcript instead of a guess.
                "engine": "local",
            }
        )

    async def _announce_phase(self) -> None:
        spec = self._machine.specialization
        await self._send_json(
            {
                "type": "reep.phase",
                "phase": self._machine.phase.value,
                "specialization": spec.label if spec is not None else None,
            }
        )

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._ws.client_state is not WebSocketState.CONNECTED:
            return
        try:
            await self._ws.send_text(json.dumps(payload))
        except (RuntimeError, WebSocketDisconnect):
            return

    def _emit_turn(
        self,
        sender: str,
        text: str,
        status: str,
        quality: str | None,
        *,
        counted: bool,
    ) -> None:
        self._seq += 1
        self._turns_emitted += 1
        if text:
            self._history.append(
                {"role": "assistant" if sender == _SENDER_INTERVIEWER else "user", "content": text}
            )
            # Oldest first, because the phase directive in the instructions
            # already carries where the interview IS; what the model needs from
            # history is what was just said, not the opening pleasantries.
            if len(self._history) > _HISTORY_TURNS:
                del self._history[: len(self._history) - _HISTORY_TURNS]
        record = _TurnRecord(
            seq=self._seq,
            phase=self._machine.phase.value,
            transcription_status=status,
            answer_quality=quality,
            counted_as_answer=counted,
            is_partial=False,
        )
        if self._dispatch(
            self._on_turn, (sender, text, f"local-{self._seq}", record), "turn"
        ):
            self._turns_persisted += 1

    def _beat(self) -> None:
        self._dispatch(self._on_heartbeat, None, "heartbeat")

    def _dispatch(self, hook: Callable[..., Any] | None, payload: Any, what: str) -> bool:
        """Fire-and-forget, exactly as the relay does it.

        A failing writer must never end a live interview: the conversation the
        student is having is worth more than the row describing it. The cost is
        that the failure is silent, which is precisely what turns_emitted versus
        turns_persisted exists to make visible afterwards.
        """
        if hook is None:
            return False
        try:
            if payload is None:
                hook()
            elif isinstance(payload, tuple):
                hook(*payload)
            else:
                hook(payload)
            return True
        except Exception:
            self._log.exception("[conn=%s] Dropped interview %s", self._conn_id, what)
            return False

    def _finalize(self, code: int, reason: str) -> None:
        status = "completed" if code in (_CLOSE_OK, _CLOSE_CAP) else "failed"
        self._dispatch(
            self._on_finalize,
            _SessionOutcome(
                status=status,
                close_code=code,
                terminal_reason=reason,
                final_phase=self._machine.phase.value,
                answers_accepted=self._machine.answers,
                turns_emitted=self._turns_emitted,
                turns_persisted=self._turns_persisted,
                upstream_session_id=None,
                report_status=self._report_status,
            ),
            "finalize",
        )


# --------------------------------------------------------------------------- helpers

_CLIENT_BOX: list[Any] = []


def _worker_model() -> str:
    """The interviewer's model, falling back to the app-wide one when blank.

    Separate from llm_model because the interviewer shares a GPU with Whisper
    and has to answer inside a conversation -- see the setting's own note for
    the measurement that produced this split.
    """
    return (
        settings.interview_local_llm_model.strip()
        or settings.llm_model.strip()
        or "llama3.2:3b"
    )


def _ollama_base() -> str:
    """Loopback by LITERAL address, never the name `localhost`.

    On Windows that name resolves to ::1 first and stalls against an IPv4-only
    server: measured on this stack at 2036 ms per request against 30 ms for the
    same call to 127.0.0.1. It presents as a slow model and is not one.
    """
    base = settings.llm_base_url.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base or "http://127.0.0.1:11434"


def _ollama_client() -> Any:
    """ONE client for the process.

    Constructing an httpx client builds an SSL context even for a plain http://
    target, and that cost lands on every request that makes its own. Measured
    here: 187.8 ms to first token with a client per call, 11.6 ms reusing one.
    """
    import httpx

    if not _CLIENT_BOX:
        _CLIENT_BOX.append(httpx.AsyncClient(timeout=None))
    return _CLIENT_BOX[0]


def _clause_cut(text: str) -> int | None:
    """Index to cut the first speakable clause, or None to keep buffering."""
    if len(text) < _MIN_CLAUSE_CHARS:
        return None
    for i, ch in enumerate(text):
        if ch in _CLAUSE_ENDS and i + 1 >= _MIN_CLAUSE_CHARS:
            return i + 1
    if len(text) >= _MAX_CLAUSE_CHARS:
        space = text.rfind(" ", _MIN_CLAUSE_CHARS, _MAX_CLAUSE_CHARS)
        return space if space > 0 else None
    return None


def _parse_report(raw: str) -> dict[str, Any] | None:
    """Defensively. A local model fences JSON more often than a hosted one."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
