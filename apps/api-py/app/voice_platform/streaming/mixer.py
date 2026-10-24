"""Audio maths for the dual-channel recording: resample, place on a shared
timeline, interleave into stereo, write WAV, optionally encode MP3.

Pure functions over PCM16 little-endian bytes and numpy int16 arrays. Nothing
here touches a socket, a database or a bucket, which is what makes the merger
unit-testable with synthetic tones (tests/test_voice_platform_mixer.py).

WHY A STEREO FILE AND NOT A SUM. The architecture calls for a "Dual-Channel
MP3/WAV": the candidate on one channel and the AI response on the other, so a
reviewer can solo either speaker and a transcription pass can diarise by
channel instead of by guesswork. app/interview_audio.py's mono mixdown is a
listening convenience; this is the record. Both are derived from the same
per-speaker frames, time-stamped off one clock, so they never disagree.

TIME ALIGNMENT IS BY WALL-CLOCK OFFSET, NOT BY CONCATENATION. Each fed frame
carries the moment it arrived; placing frames at `round(offset * rate)` puts
silence where a speaker was silent, so the two channels line up on playback.
Concatenating frames would pack each speaker's speech end to end and destroy
turn-taking — the one thing a two-channel interview recording exists to show.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

import numpy as np

SAMPLE_WIDTH_BYTES = 2
INT16_MIN = -32768
INT16_MAX = 32767


def pcm16_to_array(pcm: bytes) -> np.ndarray:
    """PCM16-LE bytes → int16 array. A trailing odd byte (half a sample) is
    dropped rather than interpreted as a whole one — see the engine's own
    `_pcm_carry` for why half a sample is white noise."""
    usable = len(pcm) - (len(pcm) % SAMPLE_WIDTH_BYTES)
    if usable <= 0:
        return np.zeros(0, dtype=np.int16)
    return np.frombuffer(pcm[:usable], dtype="<i2").astype(np.int16, copy=False)


def resample_pcm16(pcm: bytes, src_hz: int, dst_hz: int) -> bytes:
    """Linear-interpolation resample of one PCM16 mono block."""
    return resample_array(pcm16_to_array(pcm), src_hz, dst_hz).astype("<i2").tobytes()


def resample_array(samples: np.ndarray, src_hz: int, dst_hz: int) -> np.ndarray:
    if src_hz <= 0 or dst_hz <= 0:
        raise ValueError("sample rates must be positive")
    if src_hz == dst_hz or samples.size == 0:
        return samples.astype(np.int16, copy=False)
    out_len = max(1, int(round(samples.size * dst_hz / src_hz)))
    src_x = np.arange(samples.size, dtype=np.float64)
    dst_x = np.linspace(0.0, samples.size - 1, out_len, dtype=np.float64)
    resampled = np.interp(dst_x, src_x, samples.astype(np.float64))
    return np.clip(np.rint(resampled), INT16_MIN, INT16_MAX).astype(np.int16)


def place_segments(
    segments: Iterable[tuple[float, bytes, int]],
    rate_hz: int,
    *,
    min_seconds: float = 0.0,
) -> np.ndarray:
    """Lay time-stamped PCM segments onto one mono timeline.

    `segments` are `(offset_seconds, pcm16_bytes, source_rate_hz)`. Overlapping
    segments (a resample rounding a frame one sample long) are SUMMED with
    saturation, never overwritten, so a boundary sample is not lost.
    """
    placed: list[tuple[int, np.ndarray]] = []
    end = int(round(min_seconds * rate_hz))
    for offset_s, pcm, src_hz in segments:
        samples = resample_array(pcm16_to_array(pcm), src_hz, rate_hz)
        if samples.size == 0:
            continue
        start = max(0, int(round(offset_s * rate_hz)))
        placed.append((start, samples))
        end = max(end, start + samples.size)
    timeline = np.zeros(end, dtype=np.int32)
    for start, samples in placed:
        timeline[start : start + samples.size] += samples.astype(np.int32)
    return np.clip(timeline, INT16_MIN, INT16_MAX).astype(np.int16)


def interleave_stereo(left: np.ndarray, right: np.ndarray) -> bytes:
    """Two mono int16 arrays → interleaved stereo PCM16-LE bytes. The shorter
    channel is padded with digital silence so both keep their alignment."""
    n = max(left.size, right.size)
    stereo = np.zeros((n, 2), dtype=np.int16)
    stereo[: left.size, 0] = left
    stereo[: right.size, 1] = right
    return stereo.astype("<i2").tobytes()


def mix_mono(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """The listening copy: both channels summed with saturation, unattenuated
    (app/interview_audio._mix_pcm explains why neither side is halved)."""
    n = max(left.size, right.size)
    acc = np.zeros(n, dtype=np.int32)
    acc[: left.size] += left.astype(np.int32)
    acc[: right.size] += right.astype(np.int32)
    return np.clip(acc, INT16_MIN, INT16_MAX).astype(np.int16)


def write_wav(target: BinaryIO | str | Path, pcm: bytes, rate_hz: int, channels: int) -> int:
    """Write a RIFF/WAVE file; returns the payload byte count."""
    if isinstance(target, (str, Path)):
        with open(target, "wb") as handle:
            return write_wav(handle, pcm, rate_hz, channels)
    writer = wave.open(target, "wb")
    try:
        writer.setnchannels(channels)
        writer.setsampwidth(SAMPLE_WIDTH_BYTES)
        writer.setframerate(rate_hz)
        writer.writeframes(pcm)
    finally:
        writer.close()
    return len(pcm)


def wav_bytes(pcm: bytes, rate_hz: int, channels: int) -> bytes:
    buf = io.BytesIO()
    write_wav(buf, pcm, rate_hz, channels)
    return buf.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int, int]:
    """(samples as an (n, channels) int16 array, rate_hz, channels) — for tests
    and for the call-close handler's sanity check of what it is uploading."""
    with wave.open(io.BytesIO(data), "rb") as reader:
        channels = reader.getnchannels()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype(np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels)
    return samples, rate, channels


def duration_ms(payload_bytes: int, rate_hz: int, channels: int) -> int:
    frame_bytes = SAMPLE_WIDTH_BYTES * channels
    if rate_hz <= 0 or frame_bytes <= 0:
        return 0
    return int(round(payload_bytes / frame_bytes / rate_hz * 1000))


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def encode_mp3(wav_path: Path, *, bitrate: str = "96k", timeout_s: float = 120.0) -> Path | None:
    """WAV → MP3 next to it, via ffmpeg. None (never an exception) when ffmpeg
    is absent or fails: the caller keeps the WAV and records the format it
    actually produced, so an "mp3" policy on a host without ffmpeg is visible
    in the session's `recording_meta`, not silently ignored."""
    if not ffmpeg_available():
        return None
    out = wav_path.with_suffix(".mp3")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
                "-codec:a", "libmp3lame", "-b:a", bitrate, str(out),
            ],
            check=True,
            timeout=timeout_s,
            capture_output=True,
        )
    except (subprocess.SubprocessError, OSError):
        out.unlink(missing_ok=True)
        return None
    return out if out.exists() else None
