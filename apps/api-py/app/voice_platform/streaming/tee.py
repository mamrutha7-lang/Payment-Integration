"""Feed one engine's frames to two recorders: REEP's per-speaker WAV recorder
(app/interview_audio.py, when the deployment records) and the platform's
dual-channel WAV buffer.

The Nova engine takes ONE `recorder=`. The platform must not replace the
existing per-speaker capture — that is the faithful record the retention
sweeper, the download endpoint and `interview_sessions.audio_recorded` are
built around — so this tee sits in front of both. `aclose()` returns the
per-speaker recorder's `CaptureResult` when there is one, so the engine's own
audio bookkeeping is byte-for-byte what it was; the buffer's `MixResult` is
read by the media bridge afterwards through `buffer.aclose()` (idempotent).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .buffer import DualChannelBuffer

log = logging.getLogger("app.voice_platform.streaming")


class TeeRecorder:
    def __init__(self, buffer: DualChannelBuffer, primary: Any | None = None) -> None:
        self.buffer = buffer
        self.primary = primary

    def feed(self, track: str, pcm: bytes) -> None:
        # Both are total by contract; guard anyway so one cannot starve the other.
        try:
            self.buffer.feed(track, pcm)
        except Exception as exc:  # noqa: BLE001
            log.warning("WAV buffer feed failed: %s", exc)
        if self.primary is not None:
            try:
                self.primary.feed(track, pcm)
            except Exception as exc:  # noqa: BLE001
                log.warning("Per-speaker recorder feed failed: %s", exc)

    async def aclose(self) -> Any:
        results = await asyncio.gather(
            self.buffer.aclose(),
            self.primary.aclose() if self.primary is not None else asyncio.sleep(0),
            return_exceptions=True,
        )
        mix, primary = results
        if isinstance(mix, BaseException):
            log.error("WAV buffer render failed: %s", mix)
        if self.primary is None:
            if isinstance(mix, BaseException):
                raise mix
            return mix
        if isinstance(primary, BaseException):
            raise primary
        return primary

    def snapshot(self) -> Any:
        if self.primary is not None:
            return self.primary.snapshot()
        return self.buffer.snapshot()
