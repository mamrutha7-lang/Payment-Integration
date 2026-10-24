"""The WAV Buffer: two channels of time-stamped PCM held for the length of a
call and rendered into one stereo recording when the call closes.

Channel 1 is the CANDIDATE (the browser microphone, as the engine forwards it);
channel 2 is the AI RESPONSE (Nova's audio output). The buffer implements the
same `feed(track, pcm)` / `await aclose()` / `snapshot()` contract as
app/interview_audio.InterviewRecorder, so the Nova engine hands frames to it
through its existing `recorder=` hook without learning anything new — the
engine's own track names (`student`, `interviewer`) are mapped to the
platform's channel names here, once.

`feed` is TOTAL: it never raises and never blocks. It runs on the audio hot
path inside a live call, where an exception would end an interview over a
bookkeeping concern. It stamps each frame with the offset since the buffer was
created, off one clock shared by both channels — that stamp is what lets the
mixer put silence where a speaker was silent and keep the two channels
aligned.

BACKENDS. The frames live behind a `BufferBackend`: `MemoryBackend` is what
every deployment gets today; the protocol is the seam for a Redis-backed store
if the platform is ever spread across hosts that must survive a task loss
mid-call. The hard byte cap applies to either — an unbounded buffer of a
15-minute stereo call on 100 concurrent sockets is how a worker runs out of
memory at the busiest moment of a deadline week.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ...interview_audio import TRACK_INTERVIEWER, TRACK_STUDENT
from . import mixer

log = logging.getLogger("app.voice_platform.streaming")

CHANNEL_CANDIDATE = "candidate"  # channel 1 / left
CHANNEL_ASSISTANT = "assistant"  # channel 2 / right
CHANNELS: tuple[str, str] = (CHANNEL_CANDIDATE, CHANNEL_ASSISTANT)

#: The engine's recorder-hook track names → the platform's channels.
TRACK_TO_CHANNEL: dict[str, str] = {
    TRACK_STUDENT: CHANNEL_CANDIDATE,
    TRACK_INTERVIEWER: CHANNEL_ASSISTANT,
    CHANNEL_CANDIDATE: CHANNEL_CANDIDATE,
    CHANNEL_ASSISTANT: CHANNEL_ASSISTANT,
}

#: The engine feeds both tracks at the browser link rate (24 kHz PCM16 mono),
#: which is also what app/interview_audio.py records at.
DEFAULT_RATE_HZ = 24000


@dataclass(frozen=True)
class Segment:
    offset_s: float
    pcm: bytes
    rate_hz: int


class BufferBackend(Protocol):
    def append(self, session_id: str, channel: str, segment: Segment) -> None: ...

    def segments(self, session_id: str, channel: str) -> list[Segment]: ...

    def bytes_stored(self, session_id: str) -> int: ...

    def clear(self, session_id: str) -> None: ...


class MemoryBackend:
    """Process-local storage. Thread-safe because `feed` runs on the event loop
    while `finalize` runs on a worker thread."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, list[Segment]]] = {}
        self._bytes: dict[str, int] = {}
        self._lock = threading.Lock()

    def append(self, session_id: str, channel: str, segment: Segment) -> None:
        with self._lock:
            self._data.setdefault(session_id, {}).setdefault(channel, []).append(segment)
            self._bytes[session_id] = self._bytes.get(session_id, 0) + len(segment.pcm)

    def segments(self, session_id: str, channel: str) -> list[Segment]:
        with self._lock:
            return list(self._data.get(session_id, {}).get(channel, []))

    def bytes_stored(self, session_id: str) -> int:
        with self._lock:
            return self._bytes.get(session_id, 0)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)
            self._bytes.pop(session_id, None)


@dataclass
class MixResult:
    """What `finalize` produced. `recorded` is False when no frame ever arrived
    — the honest answer for a call where nobody spoke, and the same field the
    engine reads off InterviewRecorder's CaptureResult."""

    session_id: str
    recorded: bool
    stereo_wav: bytes = b""
    mono_wav: bytes = b""
    sample_rate_hz: int = DEFAULT_RATE_HZ
    duration_ms: int = 0
    channel_bytes: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    #: Where the stereo file was written locally, if a directory was given.
    path: Path | None = None

    @property
    def total_bytes(self) -> int:
        return len(self.stereo_wav)


class DualChannelBuffer:
    """One call's two channels, and the render at close."""

    def __init__(
        self,
        session_id: str,
        *,
        max_bytes: int,
        backend: BufferBackend | None = None,
        rate_hz: int = DEFAULT_RATE_HZ,
        clock: Callable[[], float] = time.monotonic,
        local_dir: Path | None = None,
        keep_mono: bool = False,
    ) -> None:
        self.session_id = session_id
        self._keep_mono = bool(keep_mono)
        self._max_bytes = max(0, int(max_bytes))
        self._backend: BufferBackend = backend if backend is not None else MemoryBackend()
        self._rate_hz = int(rate_hz)
        self._clock = clock
        self._t0 = clock()
        self._local_dir = local_dir
        self._frames: dict[str, int] = {c: 0 for c in CHANNELS}
        self._bytes: dict[str, int] = {c: 0 for c in CHANNELS}
        self._stopped = False
        self._truncated = False
        self._result: MixResult | None = None
        self._lock = threading.Lock()

    # -- the hot path -------------------------------------------------------

    def feed(self, track: str, pcm: bytes, *, rate_hz: int | None = None) -> None:
        """Accept one frame for one channel. Never raises, never blocks."""
        try:
            channel = TRACK_TO_CHANNEL.get(track)
            if channel is None or self._stopped or not pcm:
                return
            if self._backend.bytes_stored(self.session_id) + len(pcm) > self._max_bytes:
                self._stop("byte cap reached", truncated=True)
                return
            offset = max(0.0, self._clock() - self._t0)
            self._backend.append(
                self.session_id, channel, Segment(offset, bytes(pcm), rate_hz or self._rate_hz)
            )
            with self._lock:
                self._frames[channel] += 1
                self._bytes[channel] += len(pcm)
        except Exception as exc:  # noqa: BLE001 - audio is never worth the call
            log.warning("WAV buffer dropped a frame for %s: %s", self.session_id, exc)
            self._stop(f"backend error: {exc}", truncated=True)

    def _stop(self, why: str, *, truncated: bool) -> None:
        if not self._stopped:
            self._stopped = True
            self._truncated = self._truncated or truncated
            log.warning("WAV buffer for %s stopped capturing: %s", self.session_id, why)

    # -- rendering ------------------------------------------------------------

    @property
    def truncated(self) -> bool:
        return self._truncated

    @property
    def frames(self) -> dict[str, int]:
        with self._lock:
            return dict(self._frames)

    def finalize(self, *, keep_mono: bool | None = None, stem: str | None = None) -> MixResult:
        """Render both channels into one stereo WAV (and optionally the mono
        sum). CPU-bound; call from a worker thread — `aclose` does."""
        if self._result is not None:
            return self._result
        self._stopped = True
        if keep_mono is None:
            keep_mono = self._keep_mono
        with self._lock:
            channel_bytes = dict(self._bytes)
        if not any(channel_bytes.values()):
            self._result = MixResult(self.session_id, recorded=False, truncated=self._truncated,
                                     channel_bytes=channel_bytes, sample_rate_hz=self._rate_hz)
            self._backend.clear(self.session_id)
            return self._result
        left = mixer.place_segments(
            ((s.offset_s, s.pcm, s.rate_hz) for s in self._backend.segments(self.session_id, CHANNEL_CANDIDATE)),
            self._rate_hz,
        )
        right = mixer.place_segments(
            ((s.offset_s, s.pcm, s.rate_hz) for s in self._backend.segments(self.session_id, CHANNEL_ASSISTANT)),
            self._rate_hz,
        )
        stereo_pcm = mixer.interleave_stereo(left, right)
        stereo_wav = mixer.wav_bytes(stereo_pcm, self._rate_hz, 2)
        mono_wav = b""
        if keep_mono:
            mono_wav = mixer.wav_bytes(
                mixer.mix_mono(left, right).astype("<i2").tobytes(), self._rate_hz, 1
            )
        path: Path | None = None
        if self._local_dir is not None:
            self._local_dir.mkdir(parents=True, exist_ok=True)
            path = self._local_dir / f"{stem or self.session_id}.wav"
            path.write_bytes(stereo_wav)
        self._result = MixResult(
            session_id=self.session_id,
            recorded=True,
            stereo_wav=stereo_wav,
            mono_wav=mono_wav,
            sample_rate_hz=self._rate_hz,
            duration_ms=mixer.duration_ms(len(stereo_pcm), self._rate_hz, 2),
            channel_bytes=channel_bytes,
            truncated=self._truncated,
            path=path,
        )
        self._backend.clear(self.session_id)
        return self._result

    async def aclose(self) -> MixResult:
        """The engine's recorder-hook close: render off the loop, once."""
        if self._result is not None:
            return self._result
        return await asyncio.to_thread(self.finalize)

    def snapshot(self) -> MixResult:
        """What is known WITHOUT rendering — the engine calls this if aclose
        timed out. Optimistic about `recorded` for the same reason
        InterviewRecorder.snapshot is: bytes were captured, whatever became of
        the render."""
        if self._result is not None:
            return self._result
        with self._lock:
            channel_bytes = dict(self._bytes)
        return MixResult(
            self.session_id,
            recorded=any(channel_bytes.values()),
            channel_bytes=channel_bytes,
            truncated=self._truncated,
            sample_rate_hz=self._rate_hz,
        )


# ---------------------------------------------------------------------------
# The registry the call-close handler reads
# ---------------------------------------------------------------------------

_LIVE: dict[str, DualChannelBuffer] = {}
_LIVE_LOCK = threading.Lock()


def register(buffer: DualChannelBuffer) -> None:
    with _LIVE_LOCK:
        _LIVE[buffer.session_id] = buffer


def unregister(session_id: str) -> DualChannelBuffer | None:
    with _LIVE_LOCK:
        return _LIVE.pop(session_id, None)


def live(session_id: str) -> DualChannelBuffer | None:
    with _LIVE_LOCK:
        return _LIVE.get(session_id)


def live_count() -> int:
    with _LIVE_LOCK:
        return len(_LIVE)
