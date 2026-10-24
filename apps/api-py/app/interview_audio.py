"""On-disk store for interview audio — a SIBLING of app/document_store.py, never a reuse of it.

Interview Engine v3 §8.4 argued, at length and correctly, that audio should not
be captured in that pass. The product owner has since required a stored
recording for authorised developer review of the interview engine, so this
module is the "later pass" §8.4's closing paragraph sketches. Every one of its
four objections is answered HERE, in code, rather than waved off — and the
answers are the reason each design decision below looks paranoid:

1.  **"There is no encoder."** True of Opus and irrelevant to WAV. The bytes
    already crossing the relay are PCM16 LE mono 24 kHz, which is exactly the
    payload of a RIFF/WAVE file; the stdlib `wave` module writes the 44-byte
    header around them. No dependency, no transcode, and NOT ONE CPU CYCLE of
    encoding on the audio path — `feed()` is a list append.

    `wave.Wave_write.writeframes` also re-patches the RIFF size fields after
    every call (unlike `writeframesraw`), so the file on disk is a PLAYABLE WAV
    at all times, not only after a clean close. A worker killed with -9
    mid-interview therefore leaves a shorter recording rather than a corrupt
    one. That property is worth the per-flush seek and is the reason nothing
    here reaches for the private `_patchheader`.

2.  **"48 kB/s, 43 MB an interview, 4.3 GB/hour at the 100-session cap, on a box
    with no quota."** Those are §8.4's numbers for SPEECH-ONLY capture and the
    timeline doubled them: both tracks now advance at wall-clock rate whether or
    not their speaker is talking, so it is 96 kB/s and ~86 MB for the longest
    interview `interview_max_seconds` permits, plus the derived mix again on
    top. Do the arithmetic against 96,000 whenever you size anything here — the
    byte cap was left at a figure computed from 48,000 for a release, and it
    silently cut 3.8 minutes off every full-length interview. Three answers, in
    order of how much they save:
    `settings.interview_recording_enabled` is FALSE by default, so the usual
    deployment stores nothing at all; `settings.interview_recording_max_bytes`
    is a HARD per-session ceiling after which capture stops and
    `audio_truncated` is set — never a silent truncation; and every file is
    destroyed by `retention.purge_expired` through `delete_session_audio`
    below, on the same 180-day clock as the transcript. "Every file" is meant
    literally: retention sweeps by SESSION ID, for every expiring session,
    never by what the row believes about its own audio — a row's flags record
    what the relay managed to write down before it died, and the filesystem is
    the authority on which files exist.

3.  **"document_store.py cannot be reused — it decides type by magic bytes and
    accepts only PDF/PNG/JPEG, and admitting audio loosens the one control that
    makes it trustworthy."** Also correct, which is why document_store.py is not
    touched, not imported here, and not taught about audio. This store has its
    own root directory, its own naming rule and its own validation. What IS
    carried over is its hardening, because those lessons were paid for once:
    reads and deletes reject any path separator or `..`, no client-supplied
    string ever becomes a path component, and the store is the only code that
    knows where the bytes live.

    ONE DELIBERATE DIVERGENCE. document_store names files randomly because their
    names come from a student; ours are named for the `interview_sessions.id`
    that owns them — a server-generated uuid4 hex that no client ever supplies,
    and still validated on the way in. The reason is deletion: if the row's
    `audio_path` is ever lost (the finalizing UPDATE failed, a migration went
    sideways), a recording of a named student would become undiscoverable and
    therefore undeletable. Naming the file after the session means retention can
    always find it from the row's primary key alone.

4.  **"Voice is biometric-adjacent — a different consent and legal posture."**
    The strongest objection, and the one with the least forgiving failure. So:
    NO BYTES ARE WRITTEN unless `settings.interview_recording_enabled` is true
    AND the student holds a live `interview_consents` row, of the current
    version, whose `scope_store_audio` is true. Both gates are in `recorder_for`
    below and it is the ONLY constructor callers should use. The consent check
    fails CLOSED — an unreachable database means "do not record", never "record
    anyway".

WHERE THE BYTES COME FROM AND HOW THEY LEAVE THE LOOP. `feed()` is called from
the relay's audio hot path (~25 frames/s/session in each direction) and NEVER
does I/O: it appends to an in-memory list and returns. A flush is scheduled as a
task only once ~256 kB has accumulated (~5 s of audio), and the write itself
runs on `asyncio.to_thread` — the same discipline every other blocking call in
this feature uses, and the reason a slow disk cannot stall a live interview's
audio. Buffering rather than a dedicated writer thread per session is deliberate:
at the 100-session cap a thread apiece would be 100 OS threads blocked on a
queue, while `to_thread` reuses the executor the relay already has, and it keeps
one concurrency model in a module that is hard enough to reason about with one.

The buffer is bounded (`_MAX_BUFFERED_BYTES`). If flushes fall so far behind
that the bound is reached, capture STOPS and the truncation flag is set — it
does not skip a chunk and carry on. A file with a hole in the middle is a
recording that replays as if the student went quiet; a file that simply ends is
a fact the flag already describes.

SILENCE IS COUNTED, NOT BUFFERED, and that is not an optimisation — it is the
fix for a recording that ended itself. `feed()` queues a pending pad as an
INTEGER segment in the track's buffer and materialises it only in the writer,
in bounded blocks. Materialising it on the hot path instead made padding a
first-class consumer of a buffer sized for speech: a track only pads when it is
NEXT FED, so a 90-second student answer left the interviewer owing 90 seconds
of silence, and the next question pushed it as ONE 4.3 MB `bytes()` — over
`_MAX_BUFFERED_BYTES`, which stopped capture and blamed the disk. "Tell me about
yourself" runs 90 to 120 seconds, so the FIRST QUESTION of a real interview
could end the recording, and the log line sent whoever investigated to check a
healthy disk. The pad is still CHARGED to the byte cap the instant it is owed
(see `_push`) — deferring the charge would stop the cap bounding disk.

THREE FILES PER INTERVIEW: one per speaker, plus a MIXED listening copy — and
the two per-speaker files are still the record.

§8.4's refusal to mix was right about the format it was looking at and wrong
about nothing else, so read it before you touch this. Its argument was that the
uplink and the downlink are not time-aligned, and under the ORIGINAL `feed()` —
which appended only the bytes it was handed — that was not merely a risk, it was
arithmetic. A real interview produced a 141.3 s session holding a 51.7 s student
track and a 116.7 s interviewer track: 168.4 s of audio in 141.3 s of wall clock,
because each file was a speech-ONLY concatenation with every silence squeezed
out. Laying those two against each other puts the student's answers underneath
the wrong questions, which is exactly the invented fact §8.4 refused to publish.

What changed is that the files now carry a TIMELINE. The recorder takes one
monotonic stamp at construction, and `feed()` pads the gap between where a track
SHOULD be on that timeline and where it actually is with zeroed samples before
appending the real audio (see `_gap_bytes`). Both tracks are padded to the
session's full length at close. Each file is therefore session-length, both
start at the same instant, and mixing them is a sum rather than a guess. The
product owner asked for one file "like a phone recording", explicitly over a
stereo L/R variant, and this is what makes that answerable honestly.

The alignment it can promise, stated plainly so nobody over-reads it: each track
is aligned to WHEN THE BYTES REACHED THE RELAY. For the student that is their
microphone, within a network hop. For the interviewer it is when the model's
audio was forwarded to the browser — which the student heard a play-queue later,
and which the model may have emitted FASTER than real time in a burst. So the
interviewer's track can run ahead of the wall clock during a long answer and
falls back into step during the next silence (a negative gap pads nothing and
never trims — see `_gap_bytes`). The mix is accurate to a beat, not to a frame.
That is a listening copy for a reviewer, and it is why it is DERIVED: the two
per-speaker files are never regenerated from it, are still served by `?track=`,
and are the thing to reach for when the question is "what exactly did the model
send". Do not "clean up" the sources.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import time
import wave
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from .config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The format, which is not negotiable: it is whatever the relay already carries
# ---------------------------------------------------------------------------

# PCM16 LE mono 24 kHz — the wire format of BOTH directions of /api/interview
# (see the module header of app/interview_nova.py). These are not a preference;
# changing one without changing the relay produces a file that plays back at the
# wrong speed, which sounds like a defect in the student rather than in us.
_SAMPLE_RATE_HZ: Final[int] = 24000
_SAMPLE_WIDTH_BYTES: Final[int] = 2
_CHANNELS: Final[int] = 1
_BYTES_PER_SECOND: Final[int] = _SAMPLE_RATE_HZ * _SAMPLE_WIDTH_BYTES * _CHANNELS

TRACK_STUDENT: Final[str] = "student"
TRACK_INTERVIEWER: Final[str] = "interviewer"
# The mixdown: both voices on one timeline, produced at close from the two
# below. DERIVED, never captured — nothing calls `feed(TRACK_MIXED, ...)`, and
# `_Recorder` keeps no buffer for it.
TRACK_MIXED: Final[str] = "mixed"

# The two the recorder actually writes. Iterate THIS for anything to do with
# capture — buffers, byte counters, the flush — and `TRACKS` for anything to do
# with what is on disk, which is one file longer.
SOURCE_TRACKS: Final[tuple[str, ...]] = (TRACK_STUDENT, TRACK_INTERVIEWER)
# Everything the store may hold, and the order is the order a reviewer wants
# them offered in: the mix first, because it is the one a human plays, and the
# two faithful sources after it. `delete_session_audio` walks this, so a track
# added here is a track retention destroys — which is the whole reason there is
# one tuple and not three literals.
TRACKS: Final[tuple[str, ...]] = (TRACK_MIXED, TRACK_STUDENT, TRACK_INTERVIEWER)

# Accumulate this much before touching the disk: ~5 s of ONE track, one write
# per track per few seconds per session rather than one per 20 ms frame. Small
# enough that a killed worker loses seconds, large enough that the syscall rate
# at the 100-session cap stays in the tens per second.
#
# The timeline made both tracks advance at wall-clock rate whenever EITHER
# speaker is producing audio (the silent one is being padded), so a session now
# fills this in ~2.7 s rather than ~5 s. That is a shorter flush interval, not a
# larger one — the direction that loses less to a killed worker.
_FLUSH_BYTES: Final[int] = 256 * 1024

# The bound that stops a wedged disk becoming a memory leak, and it counts REAL
# AUDIO ONLY — pending silence is an integer, not bytes on the heap, so it
# cannot press on this. Reaching it means flushes are ~87 s behind on one
# speaker (~44 s if both are talking), which is not a disk this recording is
# going to survive anyway.
#
# It used to count materialised padding too, and that made the arithmetic read
# the same while meaning something else entirely: a 4 MB lump of silence is 87 s
# of a track that was merely QUIET, and reaching the bound that way stopped a
# perfectly healthy capture. See the module header.
_MAX_BUFFERED_BYTES: Final[int] = 4 * 1024 * 1024

# How much silence the writer materialises at a time, and the one zero block it
# reuses to do it. Allocated ONCE at import, shared by every session: a 15-minute
# tail is 43 MB of zeroes and `bytes(43_000_000)` on a closing interview — with
# up to 100 of them closing — is the allocation spike this replaces. 256 kB is
# ~5.5 s per `writeframes`, so a full tail costs ~170 header patches rather than
# one 43 MB write, which is a rounding error against the disk it is going to.
_SILENCE_CHUNK_BYTES: Final[int] = 256 * 1024
_SILENCE_BLOCK: Final[bytes] = bytes(_SILENCE_CHUNK_BYTES)

# -- the timeline -----------------------------------------------------------
# A gap smaller than one relay frame is JITTER — the frame arrived a beat late,
# nobody was silent — and padding it would put a 5 ms tick in the file for every
# frame of every interview. Gaps are measured against the track's ABSOLUTE
# position on the session timeline, never accumulated frame to frame, so
# swallowing a sub-threshold gap cannot drift: the deficit is still there next
# frame, and gets padded the moment it grows past this.
_MIN_PAD_BYTES: Final[int] = _BYTES_PER_SECOND // 50  # 20 ms

# The clip on a SINGLE pad, and it is not about a clock stepping backwards —
# `time.monotonic` does not do that. It is about a suspended host: close a
# laptop mid-interview and the counter can come back hours later, which as a pad
# is over a gigabyte in one `bytes()` call. A gap longer than an entire
# permitted interview is not silence anybody sat through, so capture STOPS with
# the truncation flag rather than writing a hole — the one thing this module
# already refuses to do quietly.
_MAX_PAD_SECONDS: Final[int] = 2  # multiplier on settings.interview_max_seconds

# Read/written by the mixdown in `_SAMPLE_WIDTH_BYTES`-aligned blocks. 64 kB is
# ~1.4 s: big enough that the all-silent shortcut below covers whole stretches
# of one-sided conversation, small enough that two source blocks and one output
# block are never a memory concern at the 100-session cap.
_MIX_CHUNK_BYTES: Final[int] = 64 * 1024

# The mix is written here and `os.replace`d into place, so a worker killed
# mid-mixdown leaves no half-length file that the download endpoint would serve
# as if it were the whole interview. `delete_session_audio` sweeps these too:
# an orphan .part is still a named student's voice on disk.
_PARTIAL_SUFFIX: Final[str] = ".part"

# `array('h')` is a C short, which is 16-bit on every platform CPython builds
# on, but its BYTE ORDER is the host's and PCM16 here is always little-endian.
_MIX_BYTESWAP: Final[bool] = sys.byteorder != "little"

# A stored name is one of OUR ids, never anything a client sent. Anchored, no
# alternation, no nested quantifier — linear time on any input, because a regex
# that guards a path must not itself be the way in (the audit's ReDoS finding).
_STEM_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]{1,64}")


# One entry in a track's write queue: audio the relay handed us, or a COUNT of
# silence bytes owed at that point on the timeline. The int is materialised only
# by `_write_blobs`, on the writer thread, in `_SILENCE_CHUNK_BYTES` blocks.
_Segment = bytes | int


class AudioStoreError(RuntimeError):
    """The store was asked for something it must refuse — a name it cannot trust."""


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """What was actually kept, in the shape `interview_sessions` records it.

    `recorded` is the field callers must branch on, and `path` is the field they
    must NOT — app/models/interview.py sets out the four different facts a NULL
    path collapses into one. `recorded` is true if and only if bytes reached the
    disk under `path`, which is also the condition under which retention has
    something to destroy.
    """

    recorded: bool
    path: str | None
    # DISK OCCUPIED by this interview, all three files and their RIFF headers —
    # not the number the byte cap measures. See the note at
    # `interview_sessions.audio_bytes` in app/models/interview.py, which is the
    # column this lands in and where the decision is written down.
    total_bytes: int
    duration_ms: int
    truncated: bool


_NOTHING_RECORDED: Final[CaptureResult] = CaptureResult(
    recorded=False, path=None, total_bytes=0, duration_ms=0, truncated=False
)


# ---------------------------------------------------------------------------
# Where the bytes live
# ---------------------------------------------------------------------------
def _store_root() -> Path:
    """The interview-audio root as a PATH. Resolving it creates nothing.

    Directory creation belongs to `_write_blobs`, the one function that actually
    needs the directory to exist, and it lives there for a reason that is not
    tidiness: `retention.purge_expired` now calls `delete_session_audio` for
    EVERY expiring session, most of which never recorded a byte. If resolving a
    path also ran `mkdir`, every retention sweep on a deployment that records
    nothing would conjure an empty audio store — and on a read-only or
    unmounted volume it would RAISE, which retention reads as "bytes may still
    be on disk" and answers by holding the row back from hard-delete. A path is
    a name. Naming a file must not have side effects.

    A SIBLING of `settings.uploads_path` rather than a subdirectory of it, so
    that an operator who moved uploads onto a mounted volume gets the audio on
    that volume too (the common reason to move it is that the container's own
    disk is ephemeral, and a lost recording is exactly the thing that reads as
    "the system deleted my evidence"), while `ls` still shows two separate
    stores with two separate sets of rules.

    There is no `INTERVIEW_AUDIO_DIR` setting today because app/config.py is
    owned by another track this wave. `getattr` picks one up the day it is added
    — mirror `uploads_path` and name it `interview_audio_dir`.
    """
    configured = str(getattr(settings, "interview_audio_dir", "") or "").strip()
    if configured:
        return Path(configured)
    return settings.uploads_path.parent / "interview-audio"


def _store_has_headroom() -> bool:
    """Whether the volume behind the store still has room for a NEW recording.

    Capture is wall-clock-padded at 96,000 B/s per session, so a deadline week
    of interviews can genuinely fill the volume — and a full disk stops upload
    writes and (co-located) Postgres, a far worse failure than one interview
    going unrecorded. `recorder_for` consults this before handing out a
    recorder; in-flight recorders are never touched (their bytes are already
    budgeted by the per-session cap, and cutting a flush mid-file trades a disk
    warning for a corrupt WAV).

    The store directory may not exist yet — `_store_root` is a name, and
    creation belongs to `_write_blobs` — so this measures the nearest EXISTING
    ancestor: that is the filesystem the store will land on. Fails OPEN on a
    measurement error, with the traceback in the log: this is an operations
    guard, not a consent guard, and a broken free-space probe must not silently
    disable a feature a student consented to and staff rely on. (Consent fails
    CLOSED in `audio_consent_granted` above; the two guards fail in opposite
    directions because they protect different things.)
    """
    try:
        probe = _store_root()
        while not probe.exists():
            parent = probe.parent
            if parent == probe:  # filesystem root, and even that is missing
                break
            probe = parent
        return shutil.disk_usage(probe).free >= settings.interview_audio_min_free_bytes
    except Exception:
        log.exception(
            "Could not measure free space for the interview-audio store; "
            "recording proceeds (fail-open: an ops probe must not revoke a "
            "consented feature)."
        )
        return True


def _safe_stem(stem: str) -> str:
    """Validate a stored name, or refuse.

    document_store.read_bytes' guard, tightened from a blocklist of separators to an
    ALLOWLIST of characters. A blocklist has to anticipate every separator the
    platform honours; this has to anticipate nothing, and there is no legitimate
    stored name here that is not an id we generated.
    """
    if not isinstance(stem, str) or not _STEM_RE.fullmatch(stem):
        raise AudioStoreError(f"Refusing an untrusted interview-audio name: {stem!r}")
    return stem


def _safe_track(track: str) -> str:
    if track not in TRACKS:
        raise AudioStoreError(f"Unknown interview-audio track: {track!r}")
    return track


def track_path(stem: str, track: str) -> Path:
    """Absolute path of one track's file. Validates both components first.

    The ONLY place a stored name becomes a filesystem path. Every read, write
    and delete goes through here, so the traversal guard cannot be forgotten by
    a caller that builds its own path "just this once".
    """
    return _store_root() / f"{_safe_stem(stem)}.{_safe_track(track)}.wav"


def download_name(stem: str, track: str) -> str:
    """The filename a reviewer's browser should see. Contains no student name.

    A recording is served to the Main Admin only, but it still lands in a
    downloads folder that syncs, backs up and gets searched. Naming it after the
    interview id keeps the association in the database, where access control
    lives, instead of in a filename.
    """
    return f"interview-{_safe_stem(stem)}-{_safe_track(track)}.wav"


def read_track(stem: str, track: str) -> Path:
    """Resolve one track for playback. Raises FileNotFoundError when absent.

    Returns a PATH rather than bytes on purpose: a 15-minute recording is ~43 MB
    and the download endpoint streams it, where uploads (10 MB, and a mentor
    opening one at a time) are read whole.
    """
    path = track_path(stem, track)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path


def available_tracks(stem: str) -> list[str]:
    """Which tracks actually exist on disk, in playback order (mix first)."""
    return [track for track in TRACKS if track_path(stem, track).is_file()]


def delete_session_audio(
    interview_session_id: str, audio_path: str | None = None
) -> int:
    """Remove any stored audio for this interview. Returns how many FILES were
    actually removed — 0 on most calls, because most interviews recorded nothing.

    THE CONTRACT app/retention.py's `_delete_interview_audio` documents, and its
    only implementation. Three properties it depends on, all load-bearing:

    IDEMPOTENT — a file that is already gone is success. The reaper runs again
    tomorrow and a second pass must not start raising.

    A NO-OP FOR A SESSION THAT NEVER RECORDED — including one whose store
    directory does not exist, which is every deployment with
    `interview_recording_enabled` false. `unlink` on a missing file and `unlink`
    under a missing directory both surface as `FileNotFoundError`, and both mean
    the same thing here: nothing of this student's is on that disk. Nothing in
    this function creates a directory (see `_store_root`), so calling it for a
    session that never had audio leaves the filesystem exactly as it found it.

    RAISES ONLY WHEN BYTES MAY STILL BE ON DISK — a name we refuse to resolve, a
    permission error, a device that is gone. Retention holds the database row
    back from hard-delete when this raises, because a row is the last pointer to
    a recording of a named student: destroy it and the file becomes
    undiscoverable, which is the one outcome nobody can fix afterwards.

    Both the recorded path AND the session id are tried, and they are usually the
    same string. `audio_path` defaults to None because the session id alone IS
    enough — it is the file's name, which is the whole reason §8.4's naming rule
    diverges from document_store's. Pass the row's path anyway when you have one: the
    two differ exactly when the finalizing UPDATE failed and the row never
    learned its path, and a caller that thinks about that case is a caller who
    will keep passing it if the naming rule ever changes.

    THE HOLE THIS USED TO DESCRIBE IS CLOSED. Retention once selected rows by
    `audio_recorded OR audio_path`, so a file whose row learned NEITHER — Layer 1
    died before it could record what it believed — was never offered here at all.
    `retention._delete_interview_audio` now calls this for EVERY expiring
    session, which is why the "no-op for a session that never recorded" property
    above is a contract and not a convenience: it is what makes an unconditional
    call cheap enough to be the only sweep this store needs.
    """
    stems = [s for s in dict.fromkeys([audio_path, interview_session_id]) if s]
    unusable: list[str] = []
    removed = 0
    for stem in stems:
        try:
            paths = [track_path(stem, track) for track in TRACKS]
        except AudioStoreError:
            # A stored name we will not turn into a path. Collected rather than
            # raised immediately so the OTHER stem still gets its chance to
            # delete the bytes; reported at the end so retention keeps the row.
            unusable.append(stem)
            continue
        for path in paths:
            # The .part sibling with it, and not as an afterthought: a worker
            # killed mid-mixdown leaves one behind, nothing ever renames it into
            # place, and it is still a named student's voice. A sweep that only
            # knew about finished files would leave it on disk forever.
            for target in (path, path.with_name(path.name + _PARTIAL_SUFFIX)):
                try:
                    target.unlink()
                except FileNotFoundError:
                    # The common answer, and success: no such file, or no such
                    # store directory. Caught rather than `missing_ok=True` only
                    # so the count above means "bytes destroyed" and not "paths
                    # tried" — retention reports that number to an operator.
                    continue
                removed += 1
    if unusable:
        raise AudioStoreError(
            "Interview audio may still be on disk under a name this store will "
            f"not resolve: {unusable!r}"
        )
    return removed


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
def _mix_pcm(a: bytes, b: bytes) -> bytes:
    """Two PCM16 LE blocks summed sample by sample, SATURATING at the int16 ends.

    Clamping and not wrapping, and this is the whole reason the function exists
    rather than an inline `+`: two loud voices at once overflow, and a wrapped
    int16 flips a peak to the opposite rail. That is not distortion a listener
    forgives — it is a burst of white noise exactly where both people were
    talking, which is the moment a reviewer most wants to hear.

    NEITHER SIDE IS HALVED. Attenuating both tracks by 6 dB would guarantee
    headroom and would also make a quiet, nervous student — the one this
    recording exists to review — inaudible under a model that is always at a
    confident level. Overlap is rare (people take turns), clipping is rarer
    still, and a moment of clipping is a better trade than an interview you
    have to strain to hear.

    Unequal lengths are handled by treating the short side as having gone
    silent, so a truncated or one-sided capture still mixes.
    """
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return a
    head, tail = a[: len(b)], a[len(b) :]
    # The two shortcuts that make this cheap on a REAL interview: most of each
    # track is padding, and `bytes.__contains__`-style scans are C loops where
    # the sum below is a Python one. A block where one side is digital silence
    # is the other side, unchanged.
    if not any(b):
        return a
    if not any(head):
        return b + tail
    left = array("h")
    left.frombytes(head)
    right = array("h")
    right.frombytes(b)
    if _MIX_BYTESWAP:  # pragma: no cover -- no big-endian host in this stack
        left.byteswap()
        right.byteswap()
    out = array(
        "h",
        [
            -32768 if s < -32768 else (32767 if s > 32767 else s)
            for s in map(int.__add__, left, right)
        ],
    )
    if _MIX_BYTESWAP:  # pragma: no cover
        out.byteswap()
    return out.tobytes() + tail


class InterviewRecorder:
    """One interview's capture: two mono WAV files on a shared wall-clock
    timeline, a mixdown of them at close, a hard byte cap, and no I/O on the
    caller's thread.

    Lifecycle, and it is the caller's job in this order:

        recorder = recorder_for(                 # may be None — three gates
            interview_session_id, user_id, policy_allows_audio=policy.store_audio
        )
        recorder.feed(TRACK_STUDENT, pcm)        # loop thread, never blocks
        result = await recorder.aclose()         # once, idempotent

    NOTHING IS OPENED UNTIL THE FIRST BYTE IS FLUSHED. An interview where the
    student never speaks leaves no file at all, rather than a 44-byte header that
    a reviewer has to play to discover is empty — and `recorded=False` then says
    the honest thing without anyone deleting anything.

    `feed` is TOTAL: it does not raise, for any input, ever. It is called from
    the relay's audio path, where an exception would take down a live interview
    over a bookkeeping concern. Failures stop capture, set the truncation flag
    and are logged loudly; the interview continues.
    """

    __slots__ = (
        "_stem",
        "_max_bytes",
        "_clock",
        "_t0",
        "_max_pad_bytes",
        "_position",
        "_buffers",
        "_buffered",
        "_written",
        "_captured",
        "_mixed_bytes",
        "_writers",
        "_lock",
        "_tasks",
        "_stopped",
        "_truncated",
        "_result",
    )

    def __init__(
        self,
        interview_session_id: str,
        max_bytes: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Validated HERE, at construction, so a bad id fails when the interview
        # opens rather than when the first frame arrives on the hot path.
        self._stem = _safe_stem(interview_session_id)
        # Floored at zero rather than trusted: a negative or absurd setting must
        # mean "record nothing", not "record without limit".
        self._max_bytes = max(0, int(max_bytes))

        # ONE stamp, off ONE clock, shared by both tracks. Per-track
        # `time.monotonic()` reads would each be correct and would still drift
        # apart, which is precisely the defect the timeline exists to remove.
        # `clock` is injectable so a test can simulate a student who says
        # nothing for ninety seconds without sitting there for ninety seconds;
        # nothing in production passes it.
        self._clock = clock
        # Stamped at CONSTRUCTION, not at the first frame: the recorder is built
        # when the socket opens, so a student who takes eight seconds to start
        # talking gets eight seconds of leading silence in both files and the
        # two still line up. Starting the clock at the first byte would define
        # zero as "whichever speaker went first", which is a different origin
        # per interview and unknowable afterwards.
        self._t0 = clock()
        # Twice the longest interview the relay will run. No legitimate silence
        # can reach it; a suspended host clears it by hours. See _MAX_PAD_SECONDS.
        self._max_pad_bytes = max(
            _MIN_PAD_BYTES,
            _MAX_PAD_SECONDS * int(settings.interview_max_seconds) * _BYTES_PER_SECOND,
        )
        # Where each track has reached on the session timeline: real audio AND
        # padding, buffered or written, in bytes. This and not `_written` is
        # what a gap is measured against — `_written` lags by whatever is
        # sitting in the buffer, and measuring against it would re-pad the same
        # gap on every frame until the next flush landed.
        self._position: dict[str, int] = {track: 0 for track in SOURCE_TRACKS}

        # Per track, in wire order: `bytes` is audio the relay handed us, `int`
        # is that many bytes of silence owed at that point (see `_push`). The
        # int is never materialised on this thread.
        self._buffers: dict[str, list[_Segment]] = {track: [] for track in SOURCE_TRACKS}
        # REAL AUDIO awaiting a write. Not the length of the buffers: an idle
        # track can owe fifteen minutes of silence and still be 0 here.
        self._buffered = 0
        self._written: dict[str, int] = {track: 0 for track in SOURCE_TRACKS}
        self._captured = 0
        # Payload bytes of the mixdown, once it exists. Kept so `snapshot()` can
        # report the disk this interview occupies without stat()-ing anything.
        self._mixed_bytes = 0
        # track -> (wave writer, the file handle WE opened for it). Both are
        # kept because `wave` closes only handles it opened itself.
        self._writers: dict[str, tuple[wave.Wave_write, IO[bytes]]] = {}
        # Serialises the flushes. Popping the buffer INSIDE the lock is what
        # keeps the file in wire order: whoever holds the lock writes everything
        # buffered at that moment, and the next holder writes whatever arrived
        # since. Popping outside it would let two flushes write out of order and
        # produce a recording that jumps backwards in time.
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped = False
        self._truncated = False
        self._result: CaptureResult | None = None

    # -- the hot path -------------------------------------------------------

    def feed(self, track: str, pcm: bytes) -> None:
        """Accept audio for one track. Never blocks, never raises, never does I/O.

        Still arithmetic and a `bytes` object, which is the contract the relay's
        audio path depends on: the timeline costs one `monotonic()` read, one
        subtraction and — on the frames that follow a silence — one INTEGER
        appended to the track's buffer. No syscall, no lock, and no allocation
        proportional to the gap: a two-minute silence and a two-frame one cost
        the same here, which is what stops a long answer ending the recording.
        """
        try:
            if self._stopped or self._result is not None or not pcm:
                return

            # Where the session says this track should be, minus where it is.
            pad = self._gap_bytes(track)
            if pad > self._max_pad_bytes:
                # Not a silence: a host that went to sleep. Stop, flagged, rather
                # than write hours of zeroes into a student's recording.
                self._stop(
                    f"a {pad // _BYTES_PER_SECOND}s timeline gap, longer than an "
                    "interview can run (a suspended host, not a silence)",
                    truncated=True,
                )
                self._schedule_flush()
                return
            if pad < _MIN_PAD_BYTES:
                pad = 0

            remaining = self._max_bytes - self._captured
            if remaining <= 0:
                self._stop("the per-session byte cap", truncated=True)
                return
            if pad + len(pcm) > remaining:
                # Cut ON A SAMPLE BOUNDARY. Half a PCM16 sample at the end of a
                # file is a click, and every downstream tool has to guess what to
                # do with the odd byte.
                allowed = remaining - (remaining % _SAMPLE_WIDTH_BYTES)
                if pad >= allowed:
                    # What is left of the cap would be spent entirely on silence,
                    # and silence at the end of a file carries nothing. Keep the
                    # bytes rather than the hole; the flag says it ended early.
                    self._stop("the per-session byte cap", truncated=True)
                    self._schedule_flush()
                    return
                self._push(track, pad, pcm[: allowed - pad])
                self._stop("the per-session byte cap", truncated=True)
                self._schedule_flush()
                return

            # PADDING IS CHARGED TO THE CAP, exactly like speech: it occupies the
            # same disk and it is written to the same file. Exempting it would
            # turn a 128 MB ceiling into an unbounded one, because a long quiet
            # interview is mostly padding.
            self._push(track, pad, pcm)

            if self._buffered >= _MAX_BUFFERED_BYTES:
                # Flushes are not keeping up — and `_buffered` is REAL AUDIO, so
                # this can only mean the disk, never a speaker who paused. STOP
                # rather than drop a chunk and continue: a recording that ends
                # early is described by the truncation flag, while one with a
                # silent hole in the middle replays as a student who went quiet.
                self._stop("the write buffer bound (the disk is not keeping up)",
                           truncated=True)
                self._schedule_flush()
                return

            if self._buffered >= _FLUSH_BYTES:
                self._schedule_flush()
        except Exception:  # pragma: no cover -- the belt on the braces
            # `feed` is TOTAL, and this clause is why. Nothing about storing a
            # recording is worth ending a live interview for.
            self._stopped = True
            self._truncated = True
            log.exception("Interview audio capture failed and has stopped (%s)", self._stem)

    def _gap_bytes(self, track: str) -> int:
        """How much silence this track owes the session timeline, right now.

        Aligned DOWN to a sample: half a PCM16 sample of padding is the same
        click as half a sample of speech, and it would also shift every later
        sample in the file by one byte, which is white noise rather than audio.

        NEVER NEGATIVE, and never a trim. A track legitimately runs AHEAD of the
        wall clock — the model emits a twenty-second answer in three seconds and
        the relay forwards it as fast as it arrives — and the honest response is
        to pad nothing and let the next silence bring it back into step. Trimming
        to "catch up" would delete audio somebody actually said.
        """
        elapsed = self._clock() - self._t0
        if elapsed <= 0:
            return 0
        expected = int(elapsed * _BYTES_PER_SECOND)
        gap = expected - self._position[track]
        if gap <= 0:
            return 0
        return gap - (gap % _SAMPLE_WIDTH_BYTES)

    def _push(self, track: str, pad: int, pcm: bytes) -> None:
        """Queue `pad` bytes of silence and then `pcm`, and account for both.

        The ONE place `_captured`, `_buffered` and `_position` move together.
        They are three views of the same bytes — the cap, the memory bound and
        the timeline — and a caller that updated two of them would produce a
        recording that drifts or a ceiling that does not hold, silently.

        The silence goes in as an INT SEGMENT: `_buffers[track]` is an ordered
        list of `bytes` (audio the relay handed us) and `int` (that many bytes
        of digital silence, owed at this exact point in the track). Order is the
        whole point of keeping it in the same list rather than in a counter
        beside it — a pad belongs BEFORE the frame that revealed it, and a
        second pad can arrive before the first has been written.

        THE THREE COUNTERS DIVERGE HERE, deliberately. `_captured` and
        `_position` take the pad immediately: the cap must be charged for
        silence at the moment it is owed or it stops bounding the disk, and the
        timeline must include it or the next `_gap_bytes` re-pads the same gap.
        `_buffered` does NOT: nothing was allocated, so nothing is pressing on
        the memory bound.
        """
        buf = self._buffers[track]
        if pad > 0:
            last = buf[-1] if buf else None
            if isinstance(last, int):
                # Two silences with nothing between them are one silence. Keeps
                # the list short across a long idle stretch of a live interview.
                buf[-1] = last + pad
            else:
                buf.append(pad)
        if pcm:
            buf.append(pcm)
            self._buffered += len(pcm)
        total = pad + len(pcm)
        self._captured += total
        self._position[track] += total

    def _stop(self, why: str, *, truncated: bool) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._truncated = self._truncated or truncated
        # WARNING, not INFO: a truncated recording is a fact somebody reviewing
        # this interview needs to know, and the row's flag alone does not say
        # WHICH cap stopped it.
        log.warning(
            "Interview audio capture for %s stopped at %d bytes: %s",
            self._stem,
            self._captured,
            why,
        )

    def _schedule_flush(self) -> None:
        try:
            task = asyncio.get_running_loop().create_task(self._flush())
        except RuntimeError:  # pragma: no cover -- fed from outside a loop
            # Nothing to schedule onto. The bytes stay buffered and aclose()
            # writes them; this is the shape a unit test that never runs a loop
            # takes, and it must not raise on the hot path.
            return
        # Held so CPython cannot garbage-collect a running task mid-write, the
        # same reason app/interview_nova.py keeps its `_writes` set.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        plans: dict[str, list[_Segment]] = {}
        for track in SOURCE_TRACKS:
            chunks = self._buffers[track]
            if not chunks:
                continue
            plan: list[_Segment] = list(chunks)
            chunks.clear()
            real = sum(len(s) for s in plan if isinstance(s, bytes))
            if real % _SAMPLE_WIDTH_BYTES:
                # An odd tail belongs to the next flush, not to this file: the
                # relay's own `_pcm_carry` makes the same choice one layer up,
                # for the same reason. It is the last byte of the last AUDIO
                # segment — silence runs are sample-aligned by construction, so
                # only real audio can make the total odd — and it goes back into
                # the (now empty) buffer at the HEAD, so it still precedes
                # anything fed while this flush was awaiting the disk.
                for i in range(len(plan) - 1, -1, -1):
                    segment = plan[i]
                    if isinstance(segment, bytes):
                        chunks.append(segment[-1:])
                        if len(segment) == 1:
                            del plan[i]
                        else:
                            plan[i] = segment[:-1]
                        break
                real -= 1
            self._buffered -= real
            if plan:
                plans[track] = plan
        if not plans:
            return
        try:
            # to_thread, NOT an inline write: this is the whole reason the hot
            # path is a list append. A disk that takes 400 ms must cost this
            # session 400 ms of buffering, never 400 ms of every other student's
            # audio on the same worker.
            await asyncio.to_thread(self._write_blobs, plans)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._stop("a write error", truncated=True)
            log.exception("Could not write interview audio for %s", self._stem)

    def _write_blobs(self, plans: dict[str, list[_Segment]]) -> None:
        """Worker thread. Serialised by the caller's lock, so `wave` sees one writer.

        Silence is materialised HERE and nowhere else, one `_SILENCE_CHUNK_BYTES`
        block at a time off the shared `_SILENCE_BLOCK`. That is what keeps a
        fifteen-minute tail — 43 MB of zeroes — off the event loop's heap and
        out of `_buffered`.
        """
        for track, plan in plans.items():
            opened = self._writers.get(track)
            if opened is None:
                # The file handle is opened HERE and handed to `wave`, rather
                # than letting wave.open() take the path, for one reason: we need
                # to flush it ourselves after every write (below), and reaching
                # into `Wave_write._file` to do that would be a private attribute
                # in the one code path that must not break on a Python upgrade.
                path = track_path(self._stem, track)
                # The ONLY mkdir in this module. Resolving a path deliberately
                # creates nothing (see _store_root), so the writer — the one
                # caller that cannot work without the directory — makes it.
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = path.open("wb")
                writer = wave.open(handle, "wb")
                writer.setnchannels(_CHANNELS)
                writer.setsampwidth(_SAMPLE_WIDTH_BYTES)
                writer.setframerate(_SAMPLE_RATE_HZ)
                opened = (writer, handle)
                self._writers[track] = opened
            writer, handle = opened
            written = 0
            for segment in plan:
                if isinstance(segment, int):
                    remaining = segment
                    while remaining > _SILENCE_CHUNK_BYTES:
                        # writeframes, NOT writeframesraw: see below. Patching
                        # the header once per 5.5 s of silence is a seek, and a
                        # seek is nothing against the write it accompanies.
                        writer.writeframes(_SILENCE_BLOCK)
                        remaining -= _SILENCE_CHUNK_BYTES
                    if remaining:
                        writer.writeframes(_SILENCE_BLOCK[:remaining])
                    written += segment
                    continue
                # writeframes, NOT writeframesraw: it re-patches the RIFF size
                # fields after every call. The flush is the other half of that,
                # and without it both the header and the audio sit in a
                # Python-level buffer where a killed worker loses them: the file
                # on disk would be zero bytes, not a shorter recording. Together
                # they are what makes the claim "playable at all times" true
                # rather than aspirational.
                writer.writeframes(segment)
                written += len(segment)
            handle.flush()
            self._written[track] += written

    # -- the mixdown --------------------------------------------------------

    def _pad_tails(self) -> None:
        """Bring both tracks up to the session's own length. Arithmetic only.

        Without this the shorter track simply ends, and two files that started
        together but end minutes apart are no longer a pair a reviewer can drop
        into an editor and trust. The mixdown below tolerates unequal lengths —
        it treats a finished track as silence — so this is about the SOURCES
        staying honest about the interview's shape, not about making the mix
        work.

        NOT done when capture was already stopped. A truncated recording ends
        where the cap or the disk ended it, and padding it out to the session
        length would dress a truncation up as a student who went quiet, which is
        the exact lie `audio_truncated` exists to prevent.

        A track that never received a byte is NOT padded into existence either:
        an interview where the student never spoke still leaves no student file,
        for the same reason nothing is opened until the first byte is flushed.

        THE CAP IS SPENT ON BOTH TRACKS AT ONCE, and that is the correction to a
        version that recomputed `remaining` INSIDE the loop: `student` comes
        first in SOURCE_TRACKS, so it ate whatever was left and `interviewer`
        got the crumbs — one closing session measured 43,200,044 B against
        20,800,044 B, from a function whose entire promise is that the two end
        together. Levelling both to the same reachable point is the only answer
        that keeps that promise when the cap binds.

        AND A SHORT LEVEL IS A TRUNCATION. The old branch reasoned "no audio was
        lost, only silence" and left the flag clear. That reads to a reviewer as
        a complete recording, and it is not one: it is shorter than the
        interview, so the last thing on it is not the last thing that happened.
        `audio_truncated` is how this module says "there is more interview than
        there is file", which is exactly the fact here.
        """
        if self._truncated:
            return
        target = max(
            max(self._position.values(), default=0),
            int((self._clock() - self._t0) * _BYTES_PER_SECOND),
        )
        target -= target % _SAMPLE_WIDTH_BYTES
        # Only tracks that actually recorded something: a track at position 0
        # has no file to keep in step with.
        live = [t for t in SOURCE_TRACKS if self._position[t] > 0]
        if not live:
            return
        remaining = self._max_bytes - self._captured
        if remaining < 0:
            remaining = 0
        # The highest common end the cap can pay for, solved ONCE across every
        # live track: n*level - sum(positions) <= remaining.
        reachable = (remaining + sum(self._position[t] for t in live)) // len(live)
        level = min(target, reachable)
        level -= level % _SAMPLE_WIDTH_BYTES
        floor = max(self._position[t] for t in live)
        if level < floor:
            # Not enough left to bring the shorter track level with the longer
            # one, so there is no honest common end to pad to. Pad nothing and
            # say so; a partial pad here would just be a differently-shaped lie.
            self._note_short_tail(target, min(self._position[t] for t in live))
            return
        for track in live:
            gap = level - self._position[track]
            gap -= gap % _SAMPLE_WIDTH_BYTES
            if gap < _MIN_PAD_BYTES:
                # Sub-frame: padding it would put a tick at the end of the file
                # for nothing. See _MIN_PAD_BYTES.
                continue
            self._push(track, gap, b"")
        if level < target:
            self._note_short_tail(target, level)

    def _note_short_tail(self, target: int, level: int) -> None:
        """The recording ends before the interview did, because the cap ran out.

        Flagged and logged rather than handed to `_stop`: `aclose` has already
        set `_stopped`, so `_stop` would return without saying anything, and the
        one thing an operator needs here is WHICH cap and BY HOW MUCH.
        """
        self._truncated = True
        log.warning(
            "Interview audio for %s ends %.1fs short of the session: the "
            "per-session byte cap (%d) would not pay for the trailing silence",
            self._stem,
            (target - level) / _BYTES_PER_SECOND,
            self._max_bytes,
        )

    def _write_mix(self) -> None:
        """Worker thread. Sum the two padded tracks into one listening copy.

        Runs AFTER `_close_writers`, so the sources are closed files with final
        RIFF headers, and off the event loop for the same reason every other
        write here is: it is the only CPU-bound step in the module. Measured at
        ~1.2 s for TEN minutes of the pathological case (both speakers loud the
        whole way), so ~1.8 s for the full 15 that `interview_max_seconds`
        allows, and ~0.1 s for a real interview — which is what the relay's
        `_AUDIO_CLOSE_TIMEOUT_S` (10 s) has to cover along with the final flush.
        This comment used to quote the ten-minute figure against the fifteen-
        minute case, which is optimistic by half; the headroom is still large,
        but size it against the number that is actually measured.

        STREAMED in `_MIX_CHUNK_BYTES` blocks. The two sources together are
        bounded by `interview_recording_max_bytes`, so reading them whole would
        be that entire cap on a worker's heap per closing interview, at a moment
        when up to 100 of them can be closing.

        WRITTEN TO A .part AND RENAMED. `os.replace` is atomic on both platforms
        this runs on, so the download endpoint never serves a half-finished mix
        as though it were the whole interview — the same refusal to truncate
        silently that the byte cap is built around.

        The two source files are NOT touched. They are the faithful record; this
        is a derived convenience and is regenerable from them, never the other
        way round.
        """
        sources = [
            track_path(self._stem, track)
            for track in SOURCE_TRACKS
            if self._written[track]
        ]
        if not sources:
            return
        dest = track_path(self._stem, TRACK_MIXED)
        partial = dest.with_name(dest.name + _PARTIAL_SUFFIX)
        frames = _MIX_CHUNK_BYTES // _SAMPLE_WIDTH_BYTES
        readers: list[wave.Wave_read] = []
        written = 0
        try:
            for path in sources:
                readers.append(wave.open(str(path), "rb"))
            partial.parent.mkdir(parents=True, exist_ok=True)
            with partial.open("wb") as handle:
                writer = wave.open(handle, "wb")
                writer.setnchannels(_CHANNELS)
                writer.setsampwidth(_SAMPLE_WIDTH_BYTES)
                writer.setframerate(_SAMPLE_RATE_HZ)
                while True:
                    blocks = [reader.readframes(frames) for reader in readers]
                    blob = _mix_pcm(*blocks) if len(blocks) == 2 else blocks[0]
                    if not blob:
                        break
                    writer.writeframes(blob)
                    written += len(blob)
                writer.close()
                handle.flush()
            os.replace(partial, dest)
            self._mixed_bytes = written
        finally:
            for reader in readers:
                try:
                    reader.close()
                except Exception:  # pragma: no cover -- a closed reader
                    pass
            # Whatever is left of a failed attempt goes now, while we still know
            # its name. An orphan .part is a student's voice nothing points at.
            partial.unlink(missing_ok=True)

    # -- teardown -----------------------------------------------------------

    def snapshot(self) -> CaptureResult:
        """The best-known result WITHOUT touching the disk, for when close hangs.

        Deliberately optimistic about `recorded`: if bytes reached a file, this
        says so even though the header may not have been patched a final time.
        The alternative — reporting "not recorded" because the close timed out —
        would leave a real recording of a named student on disk with nothing in
        the database pointing at it, and retention only deletes what a row
        admits exists.
        """
        written = sum(self._written.values())
        if not written:
            return CaptureResult(
                recorded=False, path=None, total_bytes=0, duration_ms=0,
                truncated=self._truncated,
            )
        files = sum(1 for n in self._written.values() if n)
        if self._mixed_bytes:
            files += 1
        return CaptureResult(
            recorded=True,
            path=self._stem,
            # DISK OCCUPIED: both source tracks, the mixdown, and +44 per file
            # for the RIFF header — which is what the reaper accounts for, and
            # what this column has always meant. It is NOT what the byte cap
            # measures: the cap bounds CAPTURED PCM, and the mix adds a derived
            # copy roughly as long as the longer source, so a 128 MB cap
            # occupies up to ~256 MB. That pairing is written down at
            # `interview_sessions.audio_bytes`; if you change one, change both.
            #
            # Counted off `_written` and `_mixed_bytes` rather than off
            # `_writers`, which aclose() empties as it closes the files — and
            # `_mixed_bytes` is 0 until the mixdown lands, so a snapshot taken on
            # the timed-out path honestly under-reports rather than guessing.
            total_bytes=written + self._mixed_bytes + 44 * files,
            duration_ms=self._duration_ms(),
            truncated=self._truncated,
        )

    def _duration_ms(self) -> int:
        """The LONGER of the two tracks — the span of the recording.

        Not the sum (the two are concurrent, not sequential) and not the
        interview's own duration (a late start, a cap or a failed write each make
        this shorter). app/models/interview.py says the same thing at the column.

        Since the tracks carry padded silence they are both very close to the
        session's own length on a clean close, so this and the interview's
        duration now usually AGREE. They are still different facts and one must
        not be derived from the other: a cap, a stalled disk or a suspended host
        stops capture with the interview still running, and this is the one that
        tells the truth about the file.
        """
        return max(
            (written * 1000) // _BYTES_PER_SECOND for written in self._written.values()
        )

    async def aclose(self) -> CaptureResult:
        """Pad, flush, close both files, mix them, and report. Idempotent.

        The order is load-bearing: pad the tails while the buffers are still
        open, flush, close so the RIFF headers are final, and only THEN mix —
        the mixdown reads the two files back through `wave`, which needs a
        complete header to know how many frames are there.
        """
        if self._result is not None:
            return self._result
        self._stopped = True

        # Let scheduled flushes finish first; they hold the lock, so this both
        # orders the writes and stops a task writing into a closed file.
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        async with self._lock:
            try:
                self._pad_tails()
            except Exception:  # pragma: no cover -- arithmetic on our own counters
                log.exception("Could not pad the interview audio tails for %s", self._stem)
            try:
                await self._flush_locked()
            except Exception:  # pragma: no cover -- _flush_locked swallows its own
                log.exception("Final interview-audio flush failed for %s", self._stem)
            try:
                await asyncio.to_thread(self._close_writers)
            except Exception:
                # The bytes are on disk either way, and `snapshot` already
                # accounts for them. Losing the last header patch costs a few
                # seconds off the reported length, not the recording.
                log.exception("Could not close the interview audio for %s", self._stem)
            try:
                await asyncio.to_thread(self._write_mix)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The mix is DERIVED and its absence costs a reviewer one click:
                # `?track=student` and `?track=interviewer` are still there and
                # are still the record. So this is logged and swallowed rather
                # than allowed to mark a perfectly good capture truncated.
                log.exception("Could not mix the interview audio for %s", self._stem)
        self._result = self.snapshot()
        return self._result

    def _close_writers(self) -> None:
        """Close both files. `wave` did not open the handles, so it does not
        close them — that is our job, and a leaked descriptor per interview is
        a worker that runs out of them in a day."""
        while self._writers:
            _track, (writer, handle) = self._writers.popitem()
            try:
                writer.close()
            finally:
                handle.close()


# ---------------------------------------------------------------------------
# The two gates — the ONLY supported way to obtain a recorder
# ---------------------------------------------------------------------------
def audio_consent_granted(user_id: str) -> bool:
    """Does this user hold a live grant, of the CURRENT version, covering audio?

    FAILS CLOSED. Any error — an unreachable database, a schema mid-migration —
    returns False and logs. "We could not check whether the student agreed" and
    "the student agreed" are not the same sentence, and only one of them may
    result in a recording of their voice.

    The version matters: consent is to WORDING, and `scope_store_audio` on a
    grant against copy that never mentioned audio would be consent to a sentence
    the student never read (§8.2). A new version means asking again.

    Imported lazily so that the file store above stays importable with no
    database at all — `retention.purge_expired` and the unit tests both rely on
    that, and it keeps app/interview_core.py's "no ORM behind the engine"
    property intact for anything that only ever calls the writer side.
    """
    from sqlalchemy import select

    from .db import SessionLocal
    from .models.interview import InterviewConsent

    try:
        with SessionLocal() as db:
            return bool(
                db.scalar(
                    select(InterviewConsent.id)
                    .where(
                        InterviewConsent.user_id == user_id,
                        InterviewConsent.version == settings.interview_consent_version,
                        InterviewConsent.revoked_at.is_(None),
                        InterviewConsent.scope_store_audio.is_(True),
                    )
                    .limit(1)
                )
            )
    except Exception:
        log.exception(
            "Could not read interview audio consent; NOT recording this interview"
        )
        return False


# WHY NOTHING WAS RECORDED, as a word the row can carry (2026-09-17). The four
# gates in `recorder_or_reason` used to answer None for four different reasons
# and log INFO lines nobody reading the console could see, so the Interview
# records screen showed "No audio" beside a grey "Download recording" button
# for every interview on a deployment where the operator's switch was ON and
# the college had simply never ticked "Allow voice recording" -- and the office
# asked why the recording feature was broken. Each value names the gate that
# closed, and the finalizer writes it to `interview_sessions.audio_skipped_reason`
# so the screen can say which switch to flip. Fixed vocabulary; the client maps
# each to a sentence.
SKIP_OPERATOR_OFF: Final[str] = "operator_off"
SKIP_POLICY_OFF: Final[str] = "policy_off"
SKIP_NO_CONSENT: Final[str] = "no_consent"
SKIP_STORE_FULL: Final[str] = "store_full"
SKIP_OPEN_FAILED: Final[str] = "open_failed"
#: A recorder WAS built and the session still closed with nothing kept: the
#: write failed, or the session produced no audio at all. Written by the
#: finalizer, not here, because only it knows what the recorder returned.
SKIP_NOTHING_CAPTURED: Final[str] = "nothing_captured"
SKIP_REASONS: Final[tuple[str, ...]] = (
    SKIP_OPERATOR_OFF,
    SKIP_POLICY_OFF,
    SKIP_NO_CONSENT,
    SKIP_STORE_FULL,
    SKIP_OPEN_FAILED,
    SKIP_NOTHING_CAPTURED,
)


def recorder_for(
    interview_session_id: str,
    user_id: str,
    *,
    policy_allows_audio: bool = True,
) -> InterviewRecorder | None:
    """A recorder, or None. The reason for a None is thrown away here; callers
    that can record it (the socket router) use `recorder_or_reason`."""
    return recorder_or_reason(
        interview_session_id, user_id, policy_allows_audio=policy_allows_audio
    )[0]


def recorder_or_reason(
    interview_session_id: str,
    user_id: str,
    *,
    policy_allows_audio: bool = True,
) -> tuple[InterviewRecorder | None, str | None]:
    """A recorder and None, or None and the SKIP_* reason nothing will be kept.

    THE ONLY CONSTRUCTOR CALLERS SHOULD USE. Every gate lives here so that "when
    does REEP record a student's voice?" has one answer in one function, rather
    than a flag checked in one file and a consent row checked in another.

    THERE ARE NOW THREE, AND THEY BELONG TO THREE DIFFERENT PEOPLE (B6.1):

      * `INTERVIEW_RECORDING_ENABLED` — the OPERATOR's, false by default;
      * `policy_allows_audio` — the COLLEGE's `interview_policies.store_audio`,
        false by default, resolved by the caller under the advisory lock that
        opened the interview and passed in rather than re-read here, so that an
        edit landing mid-handshake cannot build a recorder the rest of the
        session does not expect;
      * `audio_consent_granted(user_id)` — the STUDENT's own live
        `scope_store_audio` grant of the current version, which since B6.1 is a
        copy of the college's decision that the student was shown and
        acknowledged. Both are required even though one is derived from the
        other: the derivation happens at Start, and a client on a stale bundle
        that never posts would otherwise keep recording on last term's tick.

    `policy_allows_audio` defaults to True so that the switch it adds is the
    CALLER's to apply: the voice platform's media bridge has its own per-degree
    recording policy and applies it beside this call, and the engine tests have
    no college at all. A default of False would silently disable recording on
    both.

    Order matters for a boring reason: the two flags are memory reads and the
    consent check is a query, so a deployment with recording off (all of them,
    by default) never touches the database for this at all.
    """
    if not settings.interview_recording_enabled:
        return None, SKIP_OPERATOR_OFF
    if not policy_allows_audio:
        # INFO for the same reason the consent branch below is: this is the
        # expected state of a college that has not turned recording on, not a
        # fault. Logged at all because "recording is enabled and nothing is
        # being written" is otherwise a silent mystery, and this is now one of
        # two reasons for it.
        log.info(
            "Interview audio recording is enabled but this college's policy "
            "does not store audio; nothing will be captured."
        )
        return None, SKIP_POLICY_OFF
    if not audio_consent_granted(user_id):
        # INFO, not a warning: a student who has not agreed to be recorded is
        # the expected case, not a fault. It is logged at all because "recording
        # is on and nothing is being written" is otherwise a silent mystery.
        log.info(
            "Interview audio recording is enabled but this student has no live "
            "scope_store_audio grant; nothing will be captured."
        )
        return None, SKIP_NO_CONSENT
    if not _store_has_headroom():
        # The interview PROCEEDS — only new capture is declined. Recording is
        # wall-clock-padded at 96,000 B/s per session, so a deadline week can
        # genuinely fill the volume, and a full disk stops upload writes and
        # (co-located) Postgres — a far worse failure than one unrecorded
        # interview. In-flight recorders keep flushing: their bytes are already
        # budgeted by the per-session cap, and cutting them mid-file would turn
        # a disk warning into a corrupt WAV.
        log.warning(
            "Interview audio store is below INTERVIEW_AUDIO_MIN_FREE_BYTES "
            "(%d) of free disk; declining to record session %s. The interview "
            "itself continues. Free space or grow the volume to resume capture.",
            settings.interview_audio_min_free_bytes,
            interview_session_id,
        )
        return None, SKIP_STORE_FULL
    try:
        recorder = InterviewRecorder(
            interview_session_id, settings.interview_recording_max_bytes
        )
    except Exception:
        # A bad id, or an unwritable root. The interview must still run.
        log.exception(
            "Could not open interview audio capture for session %s; "
            "the interview continues without a recording.",
            interview_session_id,
        )
        return None, SKIP_OPEN_FAILED
    return recorder, None
