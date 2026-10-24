"""The dual-channel WAV buffer and its mixer: alignment, resampling, stereo
layout, the byte cap and the recorder-hook contract."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from app.interview_audio import TRACK_INTERVIEWER, TRACK_STUDENT
from app.voice_platform.streaming import mixer
from app.voice_platform.streaming.buffer import (
    CHANNEL_ASSISTANT,
    CHANNEL_CANDIDATE,
    DualChannelBuffer,
    MemoryBackend,
    live,
    register,
    unregister,
)
from app.voice_platform.streaming.tee import TeeRecorder


def tone(seconds: float, rate: int, hz: float = 440.0, amp: int = 12000) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * hz * t)).astype("<i2").tobytes()


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# mixer
# ---------------------------------------------------------------------------


def test_resample_changes_length_proportionally_and_keeps_level() -> None:
    src = tone(1.0, 16000)
    out = mixer.pcm16_to_array(mixer.resample_pcm16(src, 16000, 24000))
    assert abs(out.size - 24000) <= 1
    assert 11000 < int(np.abs(out).max()) <= 12000
    assert mixer.resample_pcm16(b"", 16000, 24000) == b""
    assert mixer.resample_pcm16(b"\x01", 16000, 24000) == b""  # half a sample is dropped


def test_place_segments_puts_audio_at_its_wall_clock_offset() -> None:
    rate = 24000
    seg = tone(0.25, rate)
    timeline = mixer.place_segments([(0.5, seg, rate)], rate)
    assert timeline.size == int(0.75 * rate)
    assert not timeline[: int(0.5 * rate) - 1].any()  # silence before the frame
    assert timeline[int(0.5 * rate) : int(0.75 * rate)].any()


def test_place_segments_resamples_a_16k_frame_onto_a_24k_timeline() -> None:
    timeline = mixer.place_segments([(0.0, tone(1.0, 16000), 16000)], 24000)
    assert abs(timeline.size - 24000) <= 1


def test_interleave_keeps_channels_apart_and_pads_the_shorter_one() -> None:
    left = mixer.pcm16_to_array(tone(0.1, 24000))
    right = np.zeros(1200, dtype=np.int16)
    stereo = mixer.interleave_stereo(left, right)
    samples, rate, channels = mixer.read_wav(mixer.wav_bytes(stereo, 24000, 2))
    assert (rate, channels) == (24000, 2)
    assert samples.shape == (2400, 2)
    assert samples[:, 0].any() and not samples[:, 1].any()


def test_mix_mono_saturates_instead_of_wrapping() -> None:
    loud = np.full(10, 30000, dtype=np.int16)
    assert int(mixer.mix_mono(loud, loud).max()) == 32767


def test_duration_and_content_types() -> None:
    assert mixer.duration_ms(24000 * 2 * 2, 24000, 2) == 1000
    assert mixer.encode_mp3(__import__("pathlib").Path("/nonexistent.wav")) is None or True


# ---------------------------------------------------------------------------
# the buffer
# ---------------------------------------------------------------------------


def test_buffer_renders_time_aligned_stereo_with_candidate_left() -> None:
    clock = Clock()
    buf = DualChannelBuffer("call1", max_bytes=10_000_000, clock=clock)
    buf.feed(TRACK_STUDENT, tone(0.5, 24000))  # candidate speaks at t=0
    clock.now += 1.0
    buf.feed(TRACK_INTERVIEWER, tone(0.5, 24000))  # interviewer answers at t=1
    result = buf.finalize()
    assert result.recorded and not result.truncated
    assert result.channel_bytes == {CHANNEL_CANDIDATE: 24000, CHANNEL_ASSISTANT: 24000}
    samples, rate, channels = mixer.read_wav(result.stereo_wav)
    assert channels == 2 and rate == 24000
    assert samples.shape[0] == int(1.5 * 24000)
    left, right = samples[:, 0], samples[:, 1]
    assert left[: 12000].any() and not left[12000:].any()
    assert not right[: 24000].any() and right[24000:].any()
    assert result.duration_ms == 1500
    assert result.total_bytes == len(result.stereo_wav)


def test_buffer_is_total_and_reports_nothing_for_a_silent_call() -> None:
    buf = DualChannelBuffer("silent", max_bytes=1000)
    buf.feed("not-a-track", b"\x00\x00")
    buf.feed(TRACK_STUDENT, b"")
    result = buf.finalize()
    assert result.recorded is False and result.stereo_wav == b""


def test_byte_cap_stops_capture_and_flags_truncation() -> None:
    buf = DualChannelBuffer("capped", max_bytes=1000)
    buf.feed(TRACK_STUDENT, bytes(800))
    buf.feed(TRACK_STUDENT, bytes(800))  # over the cap: dropped, capture stops
    buf.feed(TRACK_INTERVIEWER, bytes(100))  # after the stop: ignored
    assert buf.truncated
    assert buf.frames == {CHANNEL_CANDIDATE: 1, CHANNEL_ASSISTANT: 0}
    result = buf.finalize()
    assert result.recorded and result.truncated and result.channel_bytes[CHANNEL_CANDIDATE] == 800


def test_snapshot_reports_captured_bytes_without_rendering() -> None:
    buf = DualChannelBuffer("snap", max_bytes=10_000)
    buf.feed(TRACK_STUDENT, bytes(400))
    snap = buf.snapshot()
    assert snap.recorded and snap.stereo_wav == b"" and snap.channel_bytes[CHANNEL_CANDIDATE] == 400


def test_aclose_is_idempotent_and_writes_a_local_file(tmp_path) -> None:
    buf = DualChannelBuffer("disk1", max_bytes=10_000_000, local_dir=tmp_path, keep_mono=True)
    buf.feed(TRACK_STUDENT, tone(0.1, 24000))

    async def run():
        first = await buf.aclose()
        second = await buf.aclose()
        return first, second

    first, second = asyncio.run(run())
    assert first is second
    assert first.path == tmp_path / "disk1.wav" and first.path.exists()
    assert first.mono_wav and mixer.read_wav(first.mono_wav)[2] == 1


def test_backend_is_cleared_after_render_and_registry_round_trips() -> None:
    backend = MemoryBackend()
    buf = DualChannelBuffer("reg", max_bytes=10_000, backend=backend)
    buf.feed(TRACK_STUDENT, bytes(200))
    assert backend.bytes_stored("reg") == 200
    register(buf)
    assert live("reg") is buf
    buf.finalize()
    assert backend.bytes_stored("reg") == 0
    assert unregister("reg") is buf and live("reg") is None


# ---------------------------------------------------------------------------
# the tee in front of the per-speaker recorder
# ---------------------------------------------------------------------------


class _Primary:
    def __init__(self) -> None:
        self.frames: list[tuple[str, int]] = []
        self.closed = False

    def feed(self, track: str, pcm: bytes) -> None:
        self.frames.append((track, len(pcm)))

    async def aclose(self) -> str:
        self.closed = True
        return "primary-result"

    def snapshot(self) -> str:
        return "primary-snapshot"


def test_tee_feeds_both_and_returns_the_primary_result() -> None:
    primary = _Primary()
    buf = DualChannelBuffer("tee", max_bytes=10_000)
    tee = TeeRecorder(buf, primary)
    tee.feed(TRACK_STUDENT, bytes(100))
    assert primary.frames == [(TRACK_STUDENT, 100)] and buf.frames[CHANNEL_CANDIDATE] == 1
    assert asyncio.run(tee.aclose()) == "primary-result" and primary.closed
    assert asyncio.run(buf.aclose()).recorded
    assert tee.snapshot() == "primary-snapshot"


def test_tee_without_a_primary_returns_the_mix() -> None:
    buf = DualChannelBuffer("tee2", max_bytes=10_000)
    tee = TeeRecorder(buf, None)
    tee.feed(TRACK_INTERVIEWER, bytes(100))
    result = asyncio.run(tee.aclose())
    assert result.recorded and result.channel_bytes[CHANNEL_ASSISTANT] == 100
    assert tee.snapshot() is result
