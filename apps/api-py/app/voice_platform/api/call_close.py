"""The call-close handler: package the WAV buffer, upload the dual-channel
recording, mint nothing (URLs are derived on read), and project the closed
session to DynamoDB.

Called from two places with the same arguments: the media bridge's `finally`
(the normal path) and `POST /api/platform/calls/{id}/close` (the socket died
before the bridge could finish, and an operator or the client is asking for
whatever the buffer still holds). Idempotent by the Postgres row's `status`
predicate and by the buffer's own once-only render.

NOTHING HERE RAISES INTO THE CALLER. The socket is already closed; the only
thing a failure here can do is lose a recording, and it must lose it loudly —
every step logs and lands in `recording_meta` / the sync flags.

THIS MODULE IS THE WRITER OF THE PLATFORM'S LOCAL RECORDING, SO IT IS ALSO
WHERE THE DESTRUCTORS ASK FOR IT. `platform_audio_dir`, `platform_call_audio_paths`
and `delete_platform_call_audio` below are the store's public face for
`app.purge_people` and `app.purge_students`; nothing outside this file builds a
path into `platform/` for itself, because the name written here and the name
deleted there have to be one fact in one place. That is affordable only because
this module imports no FastAPI at all — no router, no `Depends`, no request
machinery: `api/__init__.py` is a docstring, the two modules that DO declare
routes (`calls.py`, `media_bridge.py`) import this one and never the other way
round, and everything here reaches the database through `SessionLocal` directly.
A destructor can therefore `from .voice_platform.api.call_close import
delete_platform_call_audio` without mounting an application. Keep it that way:
the first `from fastapi import ...` in this file puts a web framework in the
import path of a command that has to run on a console with a broken app.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ...config import settings
from ...db import SessionLocal
# `_safe_stem` beside `_store_root`, and both private on purpose: the platform's
# recordings live INSIDE the interview-audio root (see `platform_audio_dir`), so
# they inherit that store's naming rule rather than growing a second one that
# would have to be kept in step with it. A stem this store will not resolve is
# refused there, with `AudioStoreError`, exactly as it is for a per-speaker WAV.
from ...interview_audio import _safe_stem, _store_root
from ...models.interview import InterviewTurn
from ..monitoring import sentry
from ..monitoring.cloudwatch import get_logger, put_metric
from ..storage import aurora
from ..storage.dynamodb import session_store_for
from ..storage.s3 import RecordingStoreError, recording_store
from ..streaming import buffer as wav_buffer
from ..streaming import mixer
from ..streaming.buffer import DualChannelBuffer, MixResult

log = get_logger("api.call_close")


@dataclass
class CloseReport:
    session_id: str
    status: str
    recorded: bool = False
    uploaded: bool = False
    s3_key: str | None = None
    s3_uri: str | None = None
    local_path: str | None = None
    size_bytes: int = 0
    duration_ms: int = 0
    truncated: bool = False
    format: str = "wav"
    channels: str = "dual"
    extra_keys: list[str] = field(default_factory=list)
    dynamo_synced: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def platform_audio_dir() -> Path:
    """Where the stereo file is written before (or instead of) upload: a
    `platform/` folder beside the per-speaker interview WAVs, on the same
    volume the recording headroom check watches."""
    return _store_root() / "platform"


#: The stereo render, and the optional mono mixdown a `mixed`/`both` recording
#: policy asks for. `_persist_close` composes its write paths from these two
#: rather than from its own literals, so the name this module WRITES and the
#: name the destructors DELETE are one fact and cannot drift apart.
_CALL_AUDIO_STEREO = ".wav"
_CALL_AUDIO_MONO = "-mono.wav"

#: Every name `_persist_close` can leave in `platform_audio_dir()` for ONE call.
#: The third is not written here: an `mp3` recording policy sends the stereo WAV
#: through `mixer.encode_mp3`, which renders `<id>.mp3` BESIDE it
#: (`wav_path.with_suffix(".mp3")`) and unlinks nothing — so after an mp3 call
#: BOTH files are on the volume, and a sweep that knew only the format the
#: policy asked for would leave the other one there forever. Adding an artefact
#: to the writer above means adding its name here, because this tuple is the
#: whole of what `delete_platform_call_audio` looks for.
_CALL_AUDIO_SUFFIXES: tuple[str, ...] = (_CALL_AUDIO_STEREO, _CALL_AUDIO_MONO, ".mp3")


def platform_call_audio_paths(session_id: str) -> list[Path]:
    """Every path this module can write for one call, whether or not it exists.

    The ONLY place a `platform_call_sessions.id` becomes a filesystem path.
    `_safe_stem` runs first for `interview_audio.track_path`'s reason: a caller
    that builds its own path "just this once" is a caller that has dropped the
    traversal guard, and here the callers are two destructors running as root
    on a production volume.

    Resolving a name creates nothing — `_store_root`'s rule, and it matters on
    exactly this path: `purge_people` calls the delete below for EVERY call
    session on the deployment, and most deployments have never had
    `PLATFORM_RECORDINGS_BUCKET` set, let alone written a byte. A dry run that
    conjured an empty `platform/` directory would be a destructor with a side
    effect, and on an unmounted volume it would raise where the honest answer
    is "there is nothing of this student's on that disk".
    """
    directory = platform_audio_dir()
    stem = _safe_stem(session_id)
    return [directory / f"{stem}{suffix}" for suffix in _CALL_AUDIO_SUFFIXES]


def delete_platform_call_audio(session_id: str) -> int:
    """Remove this call's LOCAL recording. Returns how many FILES actually went
    — 0 on most calls, because most deployments record nothing at all.

    `interview_audio.delete_session_audio`'s contract, for the store that module
    cannot see: a platform recording is `platform/<call id>.wav`, which
    `track_path` can never produce, so the per-speaker sweep walks straight past
    it. It is deleted here, by the module that wrote it.

    IDEMPOTENT — a file that is already gone is success, so a second pass over a
    half-finished purge does not start raising.

    A NO-OP FOR A CALL THAT NEVER RECORDED, including one whose store directory
    does not exist. `unlink` on a missing file and `unlink` under a missing
    directory both surface as `FileNotFoundError` and both mean the same thing.

    RAISES ONLY WHEN BYTES MAY STILL BE ON DISK — an id this store will not
    resolve (`AudioStoreError`, from `_safe_stem`), a permission error, a device
    that has gone away. The callers report those per call and keep going, and
    the operator is told which calls they were: a stereo recording of a named
    student whose row has just been deleted is bytes nobody can name again,
    which is the one outcome a purge cannot repair afterwards.

    WHAT THIS DOES NOT TOUCH, deliberately: the object in the recordings bucket
    (that is `purge_people.destroy_s3_recordings`, from `recording_s3_key`) and
    the copy `app.archive_documents` has taken into the Object-Locked archive,
    which no destructor in this product may delete.
    """
    removed = 0
    for path in platform_call_audio_paths(session_id):
        try:
            path.unlink()
        except FileNotFoundError:
            # The common answer, and success. Caught rather than
            # `missing_ok=True` so the returned count means "files destroyed"
            # and not "paths tried": a sweep over a deployment that has never
            # recorded anything has to be able to say honestly that it removed
            # nothing, which is `delete_session_audio`'s reasoning next door.
            continue
        removed += 1
    return removed


def _transcript(db: Any, interview_session_id: str | None) -> tuple[str, int]:
    """The interview's turns as `speaker: text` lines, for the search index."""
    if not interview_session_id:
        return "", 0
    rows = db.execute(
        select(InterviewTurn.speaker, InterviewTurn.content)
        .where(InterviewTurn.interview_session_id == interview_session_id)
        .order_by(InterviewTurn.created_at)
    ).all()
    lines = [f"{speaker}: {content}" for speaker, content in rows if content]
    return "\n".join(lines), len(rows)


def _status_for(code: int) -> str:
    if code == 1000:
        return "completed"
    if code in (1001, 4008, 4009, 4012, 4014):
        return "abandoned"
    return "failed"


async def finish_call(
    session_id: str,
    *,
    degree_level: str,
    code: int,
    reason: str,
    buffer: DualChannelBuffer | None,
    turns: int | None = None,
) -> CloseReport:
    """Close the platform row, render + upload the recording, project it."""
    status = _status_for(code)
    report = CloseReport(session_id=session_id, status=status)
    with sentry.transaction(f"platform.call_close.{degree_level}", op="task", session_id=session_id):
        mix: MixResult | None = None
        if buffer is not None:
            try:
                with sentry.span("audio.render", "DualChannelBuffer.aclose"):
                    mix = await buffer.aclose()
            except Exception as exc:  # noqa: BLE001
                log.error("event=call_close.render_failed session=%s error=%s", session_id, exc)
                report.notes.append(f"render failed: {exc}")
            finally:
                wav_buffer.unregister(session_id)
        try:
            await asyncio.to_thread(_persist_close, report, degree_level, code, reason, turns, mix)
        except Exception as exc:  # noqa: BLE001
            log.exception("event=call_close.persist_failed session=%s", session_id)
            report.notes.append(f"persist failed: {exc}")
        put_metric("CallsClosed", 1, degree=degree_level, status=status)
        if report.recorded:
            put_metric("RecordingBytes", report.size_bytes, unit="Bytes", degree=degree_level)
        log.info("event=call_close.done %s", report.as_dict())
        return report


def _persist_close(
    report: CloseReport,
    degree_level: str,
    code: int,
    reason: str,
    turns: int | None,
    mix: MixResult | None,
) -> None:
    """Worker thread: Postgres, S3, DynamoDB — in that order, each
    step recorded on the report whether or not it succeeded."""
    db = SessionLocal()
    try:
        row = aurora.get_call_session(db, report.session_id)
        if row is None:
            report.notes.append("no platform_call_sessions row")
            return
        closed_now = aurora.finalize_call_session(
            db, report.session_id, code=code, reason=reason, status=report.status, turns=turns
        )
        if not closed_now:
            report.status = row.status
        policy = aurora.get_recording_policy(db, degree_level)
        db.commit()

        # -- the recording ----------------------------------------------------
        if mix is not None and mix.recorded:
            report.recorded = True
            report.duration_ms = mix.duration_ms
            report.truncated = mix.truncated
            want_mp3 = bool(policy and policy.mix_format == "mp3")
            keep = policy.keep_channels if policy else "dual"
            report.channels = keep
            local_dir = platform_audio_dir()
            local_dir.mkdir(parents=True, exist_ok=True)
            # `mix.path` is the same name under the same directory, written by
            # `DualChannelBuffer.finalize` when the media bridge handed it this
            # `local_dir` — so both spellings of "the stereo file" are covered
            # by `_CALL_AUDIO_STEREO` and by the sweep that reads it.
            stereo_path = mix.path or (local_dir / f"{report.session_id}{_CALL_AUDIO_STEREO}")
            if mix.path is None:
                stereo_path.write_bytes(mix.stereo_wav)
            report.local_path = str(stereo_path)
            artefacts: list[tuple[Path, str, bytes | None]] = []  # (path, ext, bytes)
            if keep in ("dual", "both"):
                primary: tuple[Path, str, bytes | None] = (stereo_path, "wav", mix.stereo_wav)
                if want_mp3:
                    mp3 = mixer.encode_mp3(stereo_path)
                    if mp3 is not None:
                        primary = (mp3, "mp3", None)
                        report.format = "mp3"
                    else:
                        report.notes.append("mp3 requested but ffmpeg is unavailable; stored WAV")
                artefacts.append(primary)
            if keep in ("mixed", "both") and mix.mono_wav:
                mono_path = local_dir / f"{report.session_id}{_CALL_AUDIO_MONO}"
                mono_path.write_bytes(mix.mono_wav)
                artefacts.append((mono_path, "wav", mix.mono_wav))
            report.size_bytes = sum(p.stat().st_size for p, _, _ in artefacts if p.exists())

            store = recording_store()
            if store is None:
                report.notes.append(
                    "PLATFORM_RECORDINGS_BUCKET is not set; the recording stays on the local audio volume"
                )
            else:
                for index, (path, ext, data) in enumerate(artefacts):
                    stem = report.session_id if index == 0 else f"{report.session_id}-mono"
                    key = store.key_for(degree_level, stem, ext)
                    try:
                        with sentry.span("aws.s3", "put_object", key=key):
                            if data is not None and ext == "wav":
                                stored = store.upload_bytes(key, data, metadata={"session_id": report.session_id, "degree": degree_level})
                            else:
                                stored = store.upload_file(key, path)
                    except RecordingStoreError as exc:
                        log.error("event=call_close.upload_failed session=%s key=%s error=%s", report.session_id, key, exc)
                        report.notes.append(f"upload failed: {exc}")
                        continue
                    if index == 0:
                        report.uploaded = True
                        report.s3_key = stored.key
                        report.s3_uri = stored.s3_uri
                    else:
                        report.extra_keys.append(stored.key)
            aurora.attach_recording(
                db,
                report.session_id,
                s3_key=report.s3_key,
                size_bytes=report.size_bytes,
                duration_ms=report.duration_ms,
                truncated=report.truncated,
                meta={
                    "format": report.format,
                    "channels": report.channels,
                    "sample_rate_hz": mix.sample_rate_hz,
                    "channel_bytes": mix.channel_bytes,
                    "local_path": report.local_path,
                    "extra_keys": report.extra_keys,
                    "uploaded": report.uploaded,
                    "notes": report.notes,
                },
            )
            db.commit()
        elif mix is not None:
            aurora.attach_recording(
                db, report.session_id, s3_key=None, size_bytes=0, duration_ms=0,
                truncated=mix.truncated, meta={"recorded": False, "channel_bytes": mix.channel_bytes},
            )
            db.commit()

        # -- projections --------------------------------------------------------
        db.refresh(row)
        transcript, turn_count = _transcript(db, row.interview_session_id)
        ended = row.ended_at or datetime.now(timezone.utc)
        doc = {
            "session_id": row.id,
            "degree_level": row.degree_level,
            "user_id": row.user_id,
            "candidate_id": row.candidate_id,
            "interview_session_id": row.interview_session_id,
            "specialization": row.specialization_key,
            "status": row.status,
            "close_code": row.close_code,
            "close_reason": row.close_reason,
            "started_at": row.started_at,
            "ended_at": ended,
            "duration_ms": int((ended - row.started_at).total_seconds() * 1000),
            "turns": turn_count or row.turns,
            "time_limit_seconds": row.time_limit_seconds,
            "recording_s3_key": row.recording_s3_key,
            "recording_bytes": row.recording_bytes,
            "recording_truncated": row.recording_truncated,
        }
        with sentry.span("aws.dynamodb", "update_item"):
            store = session_store_for(degree_level)
            report.dynamo_synced = bool(store.update(row.id, doc)) and store.name != "memory"
        aurora.mark_synced(db, row.id, dynamo=report.dynamo_synced)
        db.commit()
    finally:
        db.close()
