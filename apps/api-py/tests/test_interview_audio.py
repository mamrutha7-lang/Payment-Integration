"""Interview audio capture — the four objections v3 §8.4 raised, as tests.

§8.4 said "do not implement byte capture in this pass" and gave four reasons.
The product owner has since required a stored recording for authorised developer
review, so capture exists — and every test below is one of those objections
turned into something that fails if the answer stops holding:

  1. "No encoder."            -> test_the_file_is_a_playable_wav_and_its_duration_matches_the_bytes
  2. "48 kB/s, no quota."     -> TestTheCap, and test_retention_can_delete_by_session_id_alone
  3. "document_store can't be it." -> TestNames (its hardening, carried over, in a store of our own)
  4. "Voice is biometric."    -> TestTheTwoGates — no flag, no bytes; no consent, no bytes

Its FIFTH objection — that the two tracks are not time-aligned, so mixing them
would invent a fact — was upheld for a year and then answered rather than
overruled: TestTheTimeline pins the wall-clock padding that makes the two files
line up, and only then does TestTheMixdown assert that the single-file listening
copy built from them puts each speaker where they actually spoke. Read those two
in that order; the second is meaningless without the first.

Most of it needs no database and no socket. The consent gate and the download
endpoint do, and those are @requires_db.

EVERY TEST WRITES INTO tmp_path. The `store` fixture repoints the store root, so
a failing run cannot leave a WAV file in the developer's real var/ directory —
which for this feature would be somebody's actual voice.
"""

import asyncio
import sys
import uuid
import wave
from array import array
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from conftest import requires_db

from app import interview_audio
from app.config import settings
from app.db import SessionLocal
from app.interview_audio import (
    SOURCE_TRACKS,
    TRACK_INTERVIEWER,
    TRACK_MIXED,
    TRACK_STUDENT,
    TRACKS,
    AudioStoreError,
    CaptureResult,
    InterviewRecorder,
    audio_consent_granted,
    delete_session_audio,
    read_track,
    recorder_for,
    track_path,
)
from fastapi.websockets import WebSocketState

from app import interview_nova as nova
from app.interview_core import _CLOSE_OK
from app.interview_nova import NovaSonicSession

# 24 kHz, 16-bit, mono: 48000 bytes is exactly one second of it. Every duration
# assertion below is derived from this rather than from a magic number, so a
# format change breaks the arithmetic loudly instead of shifting a constant.
BYTES_PER_SECOND = 48000


def _pcm(nbytes: int) -> bytes:
    """`nbytes` of non-silent, sample-aligned PCM16."""
    assert nbytes % 2 == 0
    return bytes(range(256)) * (nbytes // 256) + b"\x00" * (nbytes % 256)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the audio store at a throwaway directory and hand back its root.

    The root is derived from `settings.uploads_path` (a SIBLING of it — see
    _store_root), so moving `upload_dir` moves both. That coupling is asserted
    here rather than assumed: if the derivation changes, this fixture stops
    isolating the tests and somebody's voice ends up in var/.
    """
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    root = tmp_path / "interview-audio"
    assert interview_audio._store_root() == root
    return root


class _Clock:
    """A hand-cranked stand-in for `time.monotonic`.

    The recorder takes its clock as an argument for exactly this: a test about a
    student who says nothing for three seconds must not take three seconds, and
    a test about a laptop that slept for eight hours cannot be written any other
    way. Nothing in production passes one.
    """

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _tone(nbytes: int, value: int) -> bytes:
    """`nbytes` of constant-amplitude PCM16 LE — audio you can assert on.

    `_pcm` above is fine for counting bytes and useless for saying WHERE a
    speaker ended up, because every 256-byte window of it looks like every
    other. A flat amplitude per speaker means one sample identifies who is in
    that region of the mix.
    """
    assert nbytes % 2 == 0
    return value.to_bytes(2, "little", signed=True) * (nbytes // 2)


def _samples(path):
    """A stored track as signed int16 sample values, in order."""
    frames = _read_wav(path)[1]
    out = array("h")
    out.frombytes(frames)
    if sys.byteorder != "little":  # pragma: no cover
        out.byteswap()
    return out


def _read_wav(path):
    """(params, frames) of a stored track, read with the stdlib reader.

    Deliberately `wave` rather than a hand-rolled header parse: the claim being
    tested is "this is a WAV any player can open", and the stdlib reader is the
    nearest thing to a player this suite can hold.
    """
    with wave.open(str(path), "rb") as w:
        return w.getparams(), w.readframes(w.getnframes())


def _recorder(session_id=None, max_bytes=10_000_000, clock=None):
    """A recorder whose clock is STOPPED unless the test asks for a moving one.

    Every assertion in this file that counts bytes would otherwise be a race.
    The recorder pads each track out to the session's wall clock, so a machine
    that takes 20 ms between two `feed()` calls — which is a normal pause under
    a loaded suite — legitimately adds a frame of silence and the counts move.
    That is the feature working, not a bug, so the tests that are about BYTES
    freeze time and the tests that are about the TIMELINE crank a `_Clock` by
    hand. A test that quietly used the real clock would be green on a fast
    laptop and red in CI, which is the worst of both.
    """
    return InterviewRecorder(
        session_id or uuid.uuid4().hex, max_bytes=max_bytes, clock=clock or _Clock()
    )


def _close(recorder: InterviewRecorder) -> CaptureResult:
    return asyncio.run(recorder.aclose())


async def _drain(recorder: InterviewRecorder) -> None:
    """Let every flush the hot path scheduled actually reach the disk.

    `feed()` creates a task and returns; a test that never yields to the loop is
    therefore testing a disk that has stopped answering, which is a real case
    (test_a_stalled_disk_stops_capture_instead_of_growing_the_buffer) and a
    deliberate one. The long-session tests below are about a HEALTHY disk
    keeping up, so they wait for the tasks rather than hoping the loop got round
    to them — a `sleep(0)` would leave the write in the executor and make the
    buffer assertions a race that passes on a fast laptop.

    The set is emptied here rather than trusted to `add_done_callback`: that
    callback runs on a later loop iteration, and `while recorder._tasks` around
    a gather of already-finished tasks would otherwise spin forever.
    """
    while recorder._tasks:
        tasks = tuple(recorder._tasks)
        await asyncio.gather(*tasks, return_exceptions=True)
        recorder._tasks.difference_update(tasks)


# ---------------------------------------------------------------------------
# Objection 1 — "there is no encoder"
# ---------------------------------------------------------------------------
class TestTheFileItself:
    def test_the_file_is_a_playable_wav_and_its_duration_matches_the_bytes(self, store):
        """PCM16 24 kHz mono needs no encoder — it needs a RIFF header.

        If this goes red the recording is not audio any more, and the way that
        usually happens is somebody changing the sample rate on one side of the
        relay only: the file still opens, the voice plays back at the wrong
        speed, and it sounds like a defect in the student.
        """
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)
        payload = _pcm(BYTES_PER_SECOND * 2)  # two seconds
        recorder.feed(TRACK_STUDENT, payload)
        result = _close(recorder)

        assert result.recorded is True
        assert result.truncated is False
        assert result.duration_ms == 2000

        params, frames = _read_wav(track_path(result.path, TRACK_STUDENT))
        assert params.nchannels == 1
        assert params.sampwidth == 2
        assert params.framerate == 24000
        assert params.nframes == len(payload) // 2
        assert frames == payload

    def test_the_two_speakers_stay_two_mono_files_and_the_mix_is_derived(self, store):
        """The per-speaker files are the RECORD; the mix is a listening copy.

        §8.4 refused to mix at all, and it was right about the format it was
        looking at: without a timeline the two tracks are speech-only
        concatenations and laying them together puts answers under the wrong
        questions. The padding closed that (see TestTheTimeline), so the mix is
        now honest — but it is still derived, and these two are still the
        artefact to reach for when the question is what the model actually sent.
        Deleting them as "duplicates of the mix" is the one-way door: the mix is
        regenerable from them and they are not recoverable from it.
        """
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)
        recorder.feed(TRACK_STUDENT, _pcm(4800))
        recorder.feed(TRACK_INTERVIEWER, _pcm(9600))
        result = _close(recorder)

        student, interviewer, mixed = (
            track_path(result.path, TRACK_STUDENT),
            track_path(result.path, TRACK_INTERVIEWER),
            track_path(result.path, TRACK_MIXED),
        )
        assert student.is_file() and interviewer.is_file() and mixed.is_file()
        assert len({student, interviewer, mixed}) == 3
        # Both padded to the session's length, so they can be laid against each
        # other -- which is exactly what the mix does.
        assert _read_wav(student)[0].nframes == 4800
        assert _read_wav(interviewer)[0].nframes == 4800
        assert _read_wav(mixed)[0].nframes == 4800
        # The SPAN of the recording, not the sum: the two tracks are concurrent.
        assert result.duration_ms == (9600 * 1000) // BYTES_PER_SECOND

    def test_an_interview_with_no_audio_leaves_no_file_at_all(self, store):
        """Not an empty 44-byte header a reviewer has to play to discover is empty.

        `recorded=False` is then the honest answer with nothing to clean up, and
        retention has nothing to find because there is nothing there.
        """
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)
        result = _close(recorder)

        assert result == CaptureResult(
            recorded=False, path=None, total_bytes=0, duration_ms=0, truncated=False
        )
        assert not list(store.glob("*.wav"))

    def test_the_header_is_current_before_the_file_is_closed(self, store):
        """A worker killed mid-interview must leave a SHORTER recording, not a
        corrupt one.

        `wave.writeframes` re-patches the RIFF size fields after every write;
        `writeframesraw` does not. Swapping one for the other is a plausible
        micro-optimisation that would silently make every recording unplayable
        unless the process exited cleanly — which is exactly the process that
        does not exit cleanly.
        """
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)

        async def scenario():
            recorder.feed(TRACK_STUDENT, _pcm(BYTES_PER_SECOND))
            # Flush without closing: this is the state a SIGKILL interrupts.
            await recorder._flush()
            return recorder.snapshot()

        snapshot = asyncio.run(scenario())
        params, frames = _read_wav(track_path(snapshot.path, TRACK_STUDENT))
        assert params.nframes == BYTES_PER_SECOND // 2
        assert len(frames) == BYTES_PER_SECOND

    def test_an_odd_byte_is_carried_rather_than_written(self, store):
        """Half a PCM16 sample at the end of a flush is a click and a frame count
        that no longer matches the data. The relay carries its stray byte into
        the next frame for the same reason (`_pcm_carry`)."""
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)

        async def scenario():
            recorder.feed(TRACK_STUDENT, _pcm(1000) + b"\x7f")
            await recorder._flush()
            recorder.feed(TRACK_STUDENT, b"\x7f")
            return await recorder.aclose()

        result = asyncio.run(scenario())
        params, frames = _read_wav(track_path(result.path, TRACK_STUDENT))
        assert params.nframes == 501
        assert len(frames) == 1002


# ---------------------------------------------------------------------------
# The timeline — §8.4's mixing objection, answered in arithmetic
# ---------------------------------------------------------------------------
class TestTheTimeline:
    """Each track is placed on the SESSION's wall clock, not concatenated.

    The defect this class exists to prevent is measured, not theoretical. A real
    interview produced a 141.3 s session holding a 51.7 s student track and a
    116.7 s interviewer track — 168.4 s of audio in 141.3 s of clock, because
    `feed()` appended only the bytes it was handed and every silence was
    squeezed out. Two files like that cannot be laid against each other at all:
    any merge puts the student's answers under the wrong questions, which is
    exactly what §8.4 refused to publish. Padding the gaps is what makes the
    mixdown in TestTheMixdown an honest artefact rather than a plausible one.
    """

    def test_a_silence_lands_the_next_audio_at_its_real_offset(self, store):
        """The whole feature in one assertion: fed nothing for three seconds,
        the audio after it starts three seconds in.

        Without this the second burst starts wherever the first one ended, and
        every later sample in the file is early by the length of every silence
        before it — a drift that grows through the interview and is invisible in
        a file that plays back perfectly well on its own.
        """
        clock = _Clock()
        recorder = InterviewRecorder(uuid.uuid4().hex, max_bytes=10_000_000, clock=clock)
        opening = _tone(BYTES_PER_SECOND // 10, 4000)
        recorder.feed(TRACK_STUDENT, opening)
        clock.advance(3.0)
        answer = _tone(BYTES_PER_SECOND // 10, -6000)
        recorder.feed(TRACK_STUDENT, answer)
        result = _close(recorder)

        frames = _read_wav(track_path(result.path, TRACK_STUDENT))[1]
        at = 3 * BYTES_PER_SECOND
        assert frames[: len(opening)] == opening
        # Digital silence in between, and NOT a shortened file.
        assert frames[len(opening) : at] == bytes(at - len(opening))
        assert frames[at : at + len(answer)] == answer

    def test_both_tracks_end_the_same_length_and_it_is_the_sessions(self, store):
        """Two files that started together must end together.

        The shorter one ending early is how a mix loses its tail, and it is also
        how two files a reviewer drops into an editor stop being a pair.
        """
        clock = _Clock()
        recorder = InterviewRecorder(uuid.uuid4().hex, max_bytes=10_000_000, clock=clock)
        recorder.feed(TRACK_STUDENT, _pcm(BYTES_PER_SECOND))  # 1 s at the start
        clock.advance(5.0)
        recorder.feed(TRACK_INTERVIEWER, _pcm(BYTES_PER_SECOND))  # 1 s at t=5
        clock.advance(2.0)  # ...and then the interview ends, at t=7
        result = _close(recorder)

        session_frames = 7 * (BYTES_PER_SECOND // 2)
        for track in TRACKS:
            assert _read_wav(track_path(result.path, track))[0].nframes == (
                session_frames
            ), f"{track} is not the length of the session it came from"
        assert result.duration_ms == 7000

    def test_a_track_running_ahead_of_the_clock_is_never_trimmed(self, store):
        """The model emits a twenty-second answer in three seconds, and the
        relay forwards it as fast as it arrives.

        So the interviewer's track legitimately runs AHEAD of the wall clock and
        falls back into step during the next silence. "Catching up" by trimming
        would delete audio the interviewer actually said, which is the one thing
        worse than an offset.
        """
        clock = _Clock()
        recorder = InterviewRecorder(uuid.uuid4().hex, max_bytes=10_000_000, clock=clock)
        recorder.feed(TRACK_INTERVIEWER, _pcm(BYTES_PER_SECOND * 4))  # 4 s at once
        clock.advance(1.0)
        recorder.feed(TRACK_INTERVIEWER, _pcm(BYTES_PER_SECOND))
        result = _close(recorder)

        # Five seconds of audio produced in one second of clock, all of it kept.
        assert (
            _read_wav(track_path(result.path, TRACK_INTERVIEWER))[0].nframes
            == 5 * (BYTES_PER_SECOND // 2)
        )
        assert result.truncated is False

    def test_padding_is_charged_to_the_byte_cap(self, store):
        """Silence occupies the same disk as speech, so it costs the same.

        Exempt it and the cap stops being a ceiling: a quiet interview is mostly
        padding, so a 64 MB limit would bound only how much anyone SAID and an
        operator would size a volume against a number that cannot overflow.
        Here the cap is two seconds' worth and the student says 20 ms per
        second — it must trip on the timeline, not after 100 turns of speech.
        """
        clock = _Clock()
        recorder = InterviewRecorder(
            uuid.uuid4().hex, max_bytes=BYTES_PER_SECOND * 2, clock=clock
        )
        spoken = 0
        for _ in range(30):
            recorder.feed(TRACK_STUDENT, _pcm(960))  # 20 ms of speech...
            spoken += 960
            clock.advance(1.0)  # ...and 980 ms of saying nothing
            if recorder.snapshot().truncated:
                break
        result = _close(recorder)

        assert result.truncated is True, "padding was not charged to the cap"
        # The proof that it was the SILENCE that filled it: barely a tenth of a
        # second was ever spoken into a cap two seconds wide.
        assert spoken < BYTES_PER_SECOND // 4
        assert recorder._captured <= BYTES_PER_SECOND * 2

    def test_a_suspended_host_stops_capture_instead_of_writing_a_hole(self, store):
        """`time.monotonic` does not step backwards; a sleeping laptop makes it
        step FORWARD by hours.

        As a pad that is over a gigabyte in one `bytes()` call, so the pad is
        clipped — and hitting the clip stops the recording with the truncation
        flag rather than writing hours of zeroes into a student's file. Silent
        truncation is the one thing this module already refuses; a pathological
        pad must not become a new way to do it.
        """
        clock = _Clock()
        recorder = InterviewRecorder(
            uuid.uuid4().hex, max_bytes=200_000_000, clock=clock
        )
        recorder.feed(TRACK_STUDENT, _pcm(4800))
        clock.advance(8 * 3600)  # the lid was closed
        recorder.feed(TRACK_STUDENT, _pcm(4800))
        result = _close(recorder)

        assert result.truncated is True
        assert _read_wav(track_path(result.path, TRACK_STUDENT))[0].nframes == 2400
        # And the truncated recording is NOT then padded out to the session
        # length, which would dress the truncation up as a student who went quiet.
        assert result.duration_ms == 100


# ---------------------------------------------------------------------------
# The timeline's second bill — silence is COUNTED, not allocated
# ---------------------------------------------------------------------------
class TestSilenceIsCountedNotBuffered:
    """Pending silence is an INTEGER on a track, never bytes on the heap.

    Padding is the feature TestTheTimeline pins, and it arrived as a first-class
    consumer of two budgets that had both been sized for speech-only capture:
    `_MAX_BUFFERED_BYTES` and `interview_recording_max_bytes`. Nothing about the
    padding was wrong — the arithmetic below is what it costs — and every
    consequence was measured on this recorder before it was fixed:

      * `feed()` materialised a track's owed silence the moment that track was
        NEXT FED, so 90 seconds of one-sided answer arrived as one 4.3 MB
        `bytes()`. Over the buffer bound, which stops capture and names the
        disk.
      * `_pad_tails` pushed the whole trailing silence in one allocation and
        never consulted the bound at all: 61.3 MB of `_buffered` on one closing
        session.

    So the pad is an int in the track's queue and the writer materialises it in
    bounded blocks. These tests fail the moment anyone puts it back on the heap.
    """

    def test_a_two_minute_answer_does_not_end_the_recording(self, store):
        """"Tell me about yourself" runs 90 to 120 seconds, and the interviewer
        says nothing for the whole of it.

        Measured before the fix: 30, 60, 75, 80 and 85 second answers were fine,
        90 and 120 second ones truncated — because the bound is
        _MAX_BUFFERED_BYTES / 48000 = 87.4 s of one-sided silence and the whole
        gap arrived in one push. THE FIRST QUESTION OF A REAL INTERVIEW could
        end the recording, and the log line ("the disk is not keeping up") sent
        whoever investigated to check a healthy disk.

        The second half of the assertion matters as much as the first: not
        truncating would be worthless if the next question then landed at the
        wrong offset. Counting the silence must place it exactly where
        materialising it did.
        """
        clock = _Clock()
        recorder = InterviewRecorder(
            uuid.uuid4().hex,
            max_bytes=settings.interview_recording_max_bytes,
            clock=clock,
        )
        question = _tone(BYTES_PER_SECOND, 8000)  # one second of interviewer

        async def interview() -> CaptureResult:
            recorder.feed(TRACK_INTERVIEWER, question)
            clock.advance(1.0)
            # 120 s of answer in 40 ms frames — the rate the relay delivers
            # them, so the student track is flushed all the way through while
            # the interviewer track queues nothing at all.
            for _ in range(120 * 25):
                recorder.feed(TRACK_STUDENT, _tone(BYTES_PER_SECOND // 25, -6000))
                clock.advance(0.04)
                await _drain(recorder)
            assert recorder.snapshot().truncated is False, (
                "the recording ended DURING the answer"
            )
            # The moment that used to stop capture: 120 s of owed silence, on a
            # track that is only now being fed.
            recorder.feed(TRACK_INTERVIEWER, question)
            return await recorder.aclose()

        result = asyncio.run(interview())

        assert result.truncated is False, (
            "a long answer truncated the recording and blamed the disk"
        )
        frames = _read_wav(track_path(result.path, TRACK_INTERVIEWER))[1]
        at = 121 * BYTES_PER_SECOND
        assert frames[: len(question)] == question
        assert frames[len(question) : at] == bytes(at - len(question))
        assert frames[at : at + len(question)] == question

    def test_a_long_idle_tail_never_lands_in_the_buffer(self, store, monkeypatch):
        """Asserted on the PEAK, deliberately, and not on the outcome.

        The recording `_pad_tails` produced was always correct — which is
        exactly why this was invisible: a recorder can write the right file and
        still be one closing interview away from the worker's memory ceiling.
        61.3 MB of `_buffered` was measured on a session that talked for 30
        seconds and then sat open to the session cap, and up to 100 of those can
        be closing at once.

        `_push` is the only place `_buffered` grows, so spying on it gives the
        true peak rather than a sample of it.
        """
        peak = 0
        original = InterviewRecorder._push

        def spy(self, track: str, pad: int, pcm: bytes) -> None:
            nonlocal peak
            original(self, track, pad, pcm)
            peak = max(peak, self._buffered)

        monkeypatch.setattr(InterviewRecorder, "_push", spy)

        clock = _Clock()
        recorder = InterviewRecorder(
            uuid.uuid4().hex,
            max_bytes=settings.interview_recording_max_bytes,
            clock=clock,
        )

        async def interview() -> CaptureResult:
            # 30 s of ordinary conversation...
            for _ in range(30 * 50):
                recorder.feed(TRACK_STUDENT, _pcm(960))
                recorder.feed(TRACK_INTERVIEWER, _pcm(960))
                clock.advance(0.02)
                await _drain(recorder)
            # ...and then the socket sits open to the session cap, which is what
            # a student who walked away looks like from in here.
            clock.now = float(settings.interview_max_seconds)
            return await recorder.aclose()

        result = asyncio.run(interview())

        assert peak <= interview_audio._MAX_BUFFERED_BYTES, (
            f"the trailing silence was allocated: {peak} bytes buffered against "
            f"a bound of {interview_audio._MAX_BUFFERED_BYTES}"
        )
        # And the tail is still THERE — a bounded buffer that achieved it by
        # dropping the silence would pass the assertion above and lose the file.
        assert result.truncated is False
        assert result.duration_ms == settings.interview_max_seconds * 1000

    def test_a_cap_that_cannot_pay_for_both_tails_levels_them_and_says_so(
        self, store
    ):
        """The remainder is solved ONCE, across both tracks.

        `_pad_tails` used to recompute it INSIDE `for track in SOURCE_TRACKS`,
        so `student` — first in that tuple — ate the whole remainder and
        `interviewer` got the crumbs: 43,200,044 B against 20,800,044 B on one
        closing session, from a function whose entire promise is that the two
        files end together. Worse, it left `truncated` CLEAR, reasoning that no
        audio had been lost, only silence. A recording shorter than the
        interview is not complete, whatever was cut off the end of it: the last
        thing on the file is not the last thing that happened, and
        `audio_truncated` is the only place this module gets to say so.
        """
        clock = _Clock()
        cap = 4_000_000  # nowhere near enough to pad two tracks to 900 s
        recorder = InterviewRecorder(uuid.uuid4().hex, max_bytes=cap, clock=clock)
        recorder.feed(TRACK_STUDENT, _pcm(BYTES_PER_SECOND))
        clock.advance(1.0)
        recorder.feed(TRACK_INTERVIEWER, _pcm(BYTES_PER_SECOND))
        clock.advance(float(settings.interview_max_seconds))
        result = _close(recorder)

        student = _read_wav(track_path(result.path, TRACK_STUDENT))[0].nframes
        interviewer = _read_wav(track_path(result.path, TRACK_INTERVIEWER))[0].nframes
        assert student == interviewer, (
            "the remainder of the cap was spent on whichever track came first"
        )
        # Spent evenly AND spent fully: levelling must not become a way to leave
        # the cap unused and the files shorter than they could have been.
        assert (student + interviewer) * 2 == cap
        assert result.truncated is True, (
            "a recording shorter than the interview reported itself complete"
        )

    def test_tails_too_far_apart_to_level_are_not_half_padded(self, store):
        """When the cap cannot even bring the shorter track up to the longer
        one, there is no honest common end — so nothing is padded at all.

        Padding the one that fits would spend the last of the cap making the gap
        between the two files WIDER, which is the opposite of what `_pad_tails`
        is for. The flag carries the fact instead; that is what it is for.
        """
        clock = _Clock()
        cap = 200_000
        recorder = InterviewRecorder(uuid.uuid4().hex, max_bytes=cap, clock=clock)
        recorder.feed(TRACK_STUDENT, _pcm(BYTES_PER_SECOND))  # 1 s at t=0
        clock.advance(2.0)
        recorder.feed(TRACK_INTERVIEWER, _pcm(BYTES_PER_SECOND))  # 1 s at t=2
        clock.advance(60.0)
        result = _close(recorder)

        assert result.truncated is True
        # Untouched: 1 s of student, and 2 s of padding plus 1 s of interviewer.
        assert _read_wav(track_path(result.path, TRACK_STUDENT))[0].nframes == (
            BYTES_PER_SECOND // 2
        )
        assert _read_wav(track_path(result.path, TRACK_INTERVIEWER))[0].nframes == (
            3 * (BYTES_PER_SECOND // 2)
        )
        assert recorder._captured <= cap


# ---------------------------------------------------------------------------
# The mixdown — one file, both voices, "like a phone recording"
# ---------------------------------------------------------------------------
class TestTheMixdown:
    """What the product owner asked for, chosen explicitly over stereo L/R.

    Derived at close from the two padded tracks. The sources stay: they are the
    faithful record and the mix is regenerable from them, never the reverse.
    """

    def test_alternating_speakers_land_at_their_own_offsets_in_the_mix(self, store):
        """A conversation, reconstructed — asserted on SAMPLE VALUES, because a
        mix of the right length can still have both people in the wrong places.

        Each speaker gets a distinct amplitude, so every region of the output
        names who is in it. If the padding ever stops being applied, the student
        moves to 0 s and the interviewer to 0.5 s, the file is still the right
        length, and only this assertion notices.
        """
        clock = _Clock()
        recorder = InterviewRecorder(uuid.uuid4().hex, max_bytes=10_000_000, clock=clock)
        half = BYTES_PER_SECOND // 2

        recorder.feed(TRACK_STUDENT, _tone(half, 1000))  # 0.0 s -> 0.5 s
        clock.advance(1.0)
        recorder.feed(TRACK_INTERVIEWER, _tone(half, -2000))  # 1.0 s -> 1.5 s
        clock.advance(1.0)
        recorder.feed(TRACK_STUDENT, _tone(half, 3000))  # 2.0 s -> 2.5 s
        clock.advance(1.0)  # and the interview ends at 3.0 s
        result = _close(recorder)

        mixed = _samples(track_path(result.path, TRACK_MIXED))
        # A quarter of a second in SAMPLES, not bytes: 24000 samples a second,
        # two bytes each. Indexing a sample array with a byte offset lands at
        # double the intended time and reads as "the mix is wrong".
        quarter = 24000 // 4
        assert len(mixed) == 3 * (BYTES_PER_SECOND // 2)
        assert mixed[quarter] == 1000, "the student is not where they spoke"
        assert mixed[quarter * 3] == 0, "somebody is talking in the first silence"
        assert mixed[quarter * 5] == -2000, "the interviewer is not where they spoke"
        assert mixed[quarter * 7] == 0
        assert mixed[quarter * 9] == 3000, "the student's second answer moved"
        assert mixed[quarter * 11] == 0
        # And the sources are untouched by having been mixed.
        assert _samples(track_path(result.path, TRACK_STUDENT))[quarter] == 1000
        assert _samples(track_path(result.path, TRACK_INTERVIEWER))[quarter] == 0

    def test_two_loud_speakers_clamp_instead_of_wrapping(self, store):
        """The failure this prevents is not distortion, it is NOISE.

        int16 addition that wraps flips a peak to the opposite rail, so the one
        moment a reviewer most wants to hear — both people talking at once,
        which is where a barge-in bug lives — comes back as a burst of static.
        30000 + 30000 wraps to -5536; it must saturate at 32767.
        """
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)
        loud = _tone(2400, 30000) + _tone(2400, -30000)
        recorder.feed(TRACK_STUDENT, loud)
        recorder.feed(TRACK_INTERVIEWER, loud)
        result = _close(recorder)

        mixed = _samples(track_path(result.path, TRACK_MIXED))
        assert mixed[300] == 32767, "a loud overlap wrapped to the negative rail"
        assert mixed[1500] == -32768
        assert max(mixed) == 32767 and min(mixed) == -32768

    def test_neither_speaker_is_halved_to_make_room(self, store):
        """Attenuating both tracks by 6 dB would guarantee headroom and would
        make a quiet, nervous student — the person this recording exists to
        review — inaudible under a model that is always at a confident level.

        People take turns, so overlap is rare and clipping rarer still. A moment
        of clipping is a better trade than an interview you have to strain to
        hear, and this is the test that fails if somebody "fixes" the clamp by
        scaling instead.
        """
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)
        recorder.feed(TRACK_STUDENT, _tone(2400, 900))
        result = _close(recorder)

        assert _samples(track_path(result.path, TRACK_MIXED))[100] == 900

    def test_the_mix_is_absent_when_nothing_was_recorded(self, store):
        """No bytes, no files — the mix does not conjure a 44-byte header for a
        reviewer to play and discover is empty, any more than the sources do."""
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)
        result = _close(recorder)

        assert result.recorded is False
        assert not list(store.glob("*.wav"))
        assert not list(store.glob("*.part"))

    def test_a_half_written_mix_never_becomes_the_served_file(self, store):
        """It is written to a .part and renamed, so a worker killed mid-mixdown
        leaves the sources and no mix — never a short mix that the download
        endpoint serves as though it were the whole interview.

        The failing write is simulated at the rename, which is the last thing
        that happens; everything before it has already touched the disk.
        """
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)
        recorder.feed(TRACK_STUDENT, _pcm(BYTES_PER_SECOND))
        recorder.feed(TRACK_INTERVIEWER, _pcm(BYTES_PER_SECOND))

        def _die(src, dst):
            raise OSError("the volume went away")

        original = interview_audio.os.replace
        interview_audio.os.replace = _die
        try:
            result = _close(recorder)
        finally:
            interview_audio.os.replace = original

        # The record survived, the derived copy did not, and nothing partial is
        # left lying around for retention to miss.
        assert result.recorded is True
        assert track_path(result.path, TRACK_STUDENT).is_file()
        assert not track_path(result.path, TRACK_MIXED).exists()
        assert not list(store.glob("*.part"))

    def test_an_orphaned_part_file_is_still_deleted(self, store):
        """A .part left by a killed worker is a named student's voice on disk.

        Retention sweeps by session id and would never see it if the sweep only
        knew about finished files, so it would outlive the retention window in a
        directory nobody looks at.
        """
        session_id = uuid.uuid4().hex
        recorder = _recorder(session_id, max_bytes=10_000_000)
        recorder.feed(TRACK_STUDENT, _pcm(4800))
        _close(recorder)
        orphan = track_path(session_id, TRACK_MIXED)
        orphan = orphan.with_name(orphan.name + ".part")
        orphan.write_bytes(b"half a recording")

        delete_session_audio(session_id)
        assert not orphan.exists()
        assert not list(store.glob("*"))


# ---------------------------------------------------------------------------
# Objection 2 — "48 kB/s, 43 MB an interview, 4.3 GB/hour, and no quota"
# ---------------------------------------------------------------------------
class TestTheCap:
    def test_the_cap_stops_capture_and_says_so(self, store):
        """A HARD ceiling, and NEVER a silent truncation.

        A truncated recording that does not announce itself is a recording that
        gets replayed as if it were complete: "she never answered the last
        question" when in fact the file ended.
        """
        recorder = _recorder(uuid.uuid4().hex, max_bytes=1000)
        recorder.feed(TRACK_STUDENT, _pcm(600))
        recorder.feed(TRACK_STUDENT, _pcm(600))  # only 400 of these fit
        # Everything after the cap is refused outright rather than queued.
        recorder.feed(TRACK_STUDENT, _pcm(600))
        result = _close(recorder)

        assert result.recorded is True
        assert result.truncated is True
        assert _read_wav(track_path(result.path, TRACK_STUDENT))[0].nframes == 500

    def test_the_cap_is_the_whole_session_not_one_track(self, store):
        """Both directions are the same disk. A per-track cap would let one
        interview use twice the ceiling an operator set."""
        recorder = _recorder(uuid.uuid4().hex, max_bytes=1000)
        recorder.feed(TRACK_STUDENT, _pcm(800))
        recorder.feed(TRACK_INTERVIEWER, _pcm(800))
        result = _close(recorder)

        student = _read_wav(track_path(result.path, TRACK_STUDENT))[0].nframes
        interviewer = _read_wav(track_path(result.path, TRACK_INTERVIEWER))[0].nframes
        assert student * 2 + interviewer * 2 == 1000
        assert result.truncated is True

    def test_the_cap_cuts_on_a_sample_boundary(self, store):
        """An odd cap must not leave half a sample at the end of the file."""
        recorder = _recorder(uuid.uuid4().hex, max_bytes=999)
        recorder.feed(TRACK_STUDENT, _pcm(2000))
        result = _close(recorder)

        assert _read_wav(track_path(result.path, TRACK_STUDENT))[0].nframes == 499

    def test_a_cap_of_zero_records_nothing_at_all(self, store):
        """The setting is the off switch of last resort, and a nonsensical value
        must mean "record nothing" rather than "record without limit"."""
        recorder = _recorder(uuid.uuid4().hex, max_bytes=0)
        recorder.feed(TRACK_STUDENT, _pcm(4800))
        result = _close(recorder)

        assert result.recorded is False
        assert result.truncated is True  # it ended early, and the flag says so
        assert not list(store.glob("*.wav"))

    def test_a_stalled_disk_stops_capture_instead_of_growing_the_buffer(
        self, store, monkeypatch
    ):
        """The bound that stops a wedged disk becoming a memory leak.

        And it STOPS rather than dropping a chunk and carrying on: a file with a
        hole in the middle replays as a student who went quiet, which is a lie
        the truncation flag cannot correct.
        """
        monkeypatch.setattr(interview_audio, "_MAX_BUFFERED_BYTES", 4096)
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)
        # Never awaited, so no flush ever runs: exactly what a disk that has
        # stopped answering looks like from up here.
        for _ in range(10):
            recorder.feed(TRACK_STUDENT, _pcm(1024))

        assert recorder._buffered <= 4096 + 1024
        assert recorder.snapshot().truncated is True

    def test_the_default_cap_cannot_bind_before_the_session_does(self):
        """The cap is a disk-exhaustion stop. It must never be what ends a
        LEGAL interview — that is `interview_max_seconds`' job, and only its
        job.

        Computed from the two settings rather than written down, because
        writing it down is how it broke: the cap was sized at 64,000,000 when
        capture was speech-only, the timeline then made BOTH tracks advance at
        wall clock, and 96,000 B/s put the ceiling at 666 s of a 900 s session.
        Every maximum-length interview lost its last 3.8 minutes. Assert the
        relationship and either number can move safely; assert the number and
        the next person to move one has no idea they broke it.
        """
        floor = 2 * int(settings.interview_max_seconds) * BYTES_PER_SECOND
        assert settings.interview_recording_max_bytes >= floor, (
            "interview_recording_max_bytes "
            f"({settings.interview_recording_max_bytes}) is smaller than the "
            f"{floor} bytes a full {settings.interview_max_seconds}s interview "
            "captures with both tracks padded to the wall clock, so the cap "
            "truncates every full-length recording"
        )

    def test_a_full_length_interview_is_not_truncated_by_the_default_cap(self, store):
        """The end-to-end half of the assertion above: run a session all the way
        to `interview_max_seconds` at the DEFAULT cap and keep every second.

        Measured before the cap was resized: capture stopped at t=673 s with
        `truncated` set. Both numbers come from the settings, so this keeps
        protecting the invariant if either one moves.
        """
        seconds = int(settings.interview_max_seconds)
        clock = _Clock()
        recorder = InterviewRecorder(
            uuid.uuid4().hex,
            max_bytes=settings.interview_recording_max_bytes,
            clock=clock,
        )

        async def interview() -> CaptureResult:
            # Turn-taking for the whole legal length: one 20 ms frame a second,
            # the speaker changing every 15. Enough to keep both tracks pinned
            # to the wall clock — which is what fills the cap — without pushing
            # 22,500 frames through a unit test to prove it.
            speaker = TRACK_INTERVIEWER
            for second in range(seconds):
                recorder.feed(speaker, _pcm(960))
                clock.advance(1.0)
                await _drain(recorder)
                if second % 15 == 14:
                    speaker = (
                        TRACK_STUDENT
                        if speaker == TRACK_INTERVIEWER
                        else TRACK_INTERVIEWER
                    )
            return await recorder.aclose()

        result = asyncio.run(interview())

        assert result.truncated is False, (
            "the byte cap ended a legal-length interview before the session cap did"
        )
        assert result.duration_ms == seconds * 1000
        for track in SOURCE_TRACKS:
            assert _read_wav(track_path(result.path, track))[0].nframes == (
                seconds * (BYTES_PER_SECOND // 2)
            ), f"{track} is shorter than the interview it came from"


# ---------------------------------------------------------------------------
# Objection 3 — "document_store.py cannot be reused"
# ---------------------------------------------------------------------------
class TestNames:
    """document_store's hardening, carried over into a store with its own rules.

    document_store.py is NOT touched, NOT imported by the writer and NOT taught about
    audio — admitting it there would loosen the magic-byte rule that makes that
    store trustworthy. What travels is the lesson: no client string ever becomes
    a path component, and reads AND deletes both refuse a separator.
    """

    @pytest.mark.parametrize(
        "hostile",
        [
            "../../../etc/passwd",
            "..\\..\\windows\\system32\\config\\sam",
            "a/b",
            "a\\b",
            "..",
            "",
            "x" * 65,
            "sess id",
            "sess.id",  # the separator between stem and track, and not part of a stem
        ],
    )
    def test_a_traversal_attempt_is_refused_everywhere(self, store, hostile):
        with pytest.raises(AudioStoreError):
            track_path(hostile, TRACK_STUDENT)
        with pytest.raises(AudioStoreError):
            read_track(hostile, TRACK_STUDENT)
        with pytest.raises(AudioStoreError):
            InterviewRecorder(hostile, max_bytes=1000)
        if hostile:
            # An EMPTY name is the one case delete treats as "there is nothing
            # here", because that is what a NULL `audio_path` looks like by the
            # time it reaches this function — and raising on it would hold every
            # never-recorded row back from retention forever.
            with pytest.raises(AudioStoreError):
                delete_session_audio(hostile, hostile)

    def test_an_unknown_track_is_refused(self, store):
        """The track is the other half of the filename, so it is validated the
        same way. `?track=../x` must never reach the filesystem."""
        with pytest.raises(AudioStoreError):
            track_path(uuid.uuid4().hex, "../x")

    def test_files_are_named_for_the_session_that_owns_them(self, store):
        """The one deliberate divergence from document_store's random names.

        document_store randomises because its names come from a student. Ours is a
        server-generated session id, and naming the file after it is what makes
        the recording findable when the row's `audio_path` is lost — the only
        state in which a recording of a named student becomes undeletable.
        """
        session_id = uuid.uuid4().hex
        recorder = _recorder(session_id, max_bytes=10_000_000)
        recorder.feed(TRACK_STUDENT, _pcm(4800))
        result = _close(recorder)

        assert result.path == session_id
        assert (store / f"{session_id}.student.wav").is_file()

    def test_reading_a_missing_track_raises_file_not_found(self, store):
        with pytest.raises(FileNotFoundError):
            read_track(uuid.uuid4().hex, TRACK_INTERVIEWER)


class TestDeletion:
    """`delete_session_audio` is retention's ONLY door into this store."""

    def _recorded(self) -> CaptureResult:
        recorder = _recorder(uuid.uuid4().hex, max_bytes=10_000_000)
        recorder.feed(TRACK_STUDENT, _pcm(4800))
        recorder.feed(TRACK_INTERVIEWER, _pcm(4800))
        return _close(recorder)

    def test_delete_removes_both_tracks(self, store):
        result = self._recorded()
        delete_session_audio(result.path, result.path)
        assert not list(store.glob("*.wav"))

    def test_retention_can_delete_by_session_id_alone(self, store):
        """`audio_path` NULL and bytes on disk is the state that matters.

        It happens when the finalizing UPDATE never landed. Retention passes the
        row's primary key as well as its path for exactly this case, and if this
        stops working a recording becomes undiscoverable — which is the one
        failure nobody can fix afterwards.
        """
        result = self._recorded()
        delete_session_audio(result.path, None)
        assert not list(store.glob("*.wav"))

    def test_delete_is_idempotent(self, store):
        """The reaper runs again tomorrow; a second pass must not start raising."""
        result = self._recorded()
        # THREE files, not two: the mixdown is on the same disk under the same
        # name, so it goes with them. A count that stayed at 2 would mean the
        # sweep learned about a track and forgot to destroy it.
        assert delete_session_audio(result.path, result.path) == 3
        assert delete_session_audio(result.path, result.path) == 0  # must not raise
        assert delete_session_audio(uuid.uuid4().hex) == 0  # never recorded at all

    def test_deleting_a_session_that_never_recorded_touches_nothing(self, store):
        """The property `retention._delete_interview_audio` now leans its whole
        weight on: it calls this for EVERY expiring session, and in the default
        deployment (`interview_recording_enabled` false) not one of them ever
        wrote a byte, so there is no store directory at all.

        Resolving a path must therefore neither raise nor CREATE the store — an
        empty `interview-audio/` appearing in var/ on a deployment that records
        nothing is the harmless half of that; a read-only or unmounted volume
        raising, and retention reading the raise as "bytes may still be on
        disk", is the half that would hold every interview row back from
        hard-delete forever.
        """
        assert not store.exists()
        assert delete_session_audio(uuid.uuid4().hex) == 0
        assert delete_session_audio(uuid.uuid4().hex, uuid.uuid4().hex) == 0
        assert not store.exists(), "resolving a path must not conjure a store"

    def test_an_unusable_stored_path_still_deletes_what_it_can_and_then_raises(
        self, store
    ):
        """Retention holds the ROW back when this raises, deliberately: a row is
        the last pointer to a recording, and deleting it would leave the file
        undiscoverable."""
        result = self._recorded()
        with pytest.raises(AudioStoreError):
            delete_session_audio(result.path, "../somewhere/else")
        # The files it COULD name are gone even though it went on to raise.
        assert not list(store.glob("*.wav"))


@requires_db
class TestRetentionSweepsTheDiskNotTheRow:
    """THE RESIDUAL: a recording whose row never learned it existed.

    `interview_sessions.audio_recorded` and `audio_path` record what the RELAY
    BELIEVED and got as far as writing down. A session whose Layer 1 finalizer
    never ran — an exception its `except*` clauses do not match — still has its
    files closed by `run()`'s `finally`, so the WAVs are on disk while the row
    says `false` / `NULL`. Retention used to select what to delete by those two
    columns, which asks the row a question only the filesystem can answer, and
    the answer it gave was "there is no audio here" about a named student's
    voice. This is the test that fails if anyone puts that filter back.
    """

    def test_files_are_deleted_even_when_the_row_never_learned_it_had_audio(
        self, store
    ):
        from app import retention
        from app.models.interview import InterviewSession
        from app.models.user import Role, Student, User

        now = datetime.now(timezone.utc)
        tag = uuid.uuid4().hex[:8]
        with SessionLocal() as db:
            user = User(
                email=f"ivresidual-{tag}@bgscet.ac.in",
                name="Interview Audio Residual",
                role=Role.STUDENT,
                password_hash="x",
            )
            db.add(user)
            db.flush()
            student = Student(user_id=user.id)
            db.add(student)
            db.flush()
            started = now - timedelta(days=400)
            row = InterviewSession(
                student_id=student.id,
                status="abandoned",
                terminal_reason="orphaned (no heartbeat)",
                started_at=started,
                heartbeat_at=started,
                # Soft-deleted longer ago than SOFT_DELETE_GRACE_DAYS, so this
                # pass is the one that destroys it for good.
                deleted_at=now
                - timedelta(days=retention.SOFT_DELETE_GRACE_DAYS + 10),
                # The state under test, spelled out rather than left to defaults:
                # the relay died before it could tell the row anything.
                audio_recorded=False,
                audio_path=None,
            )
            db.add(row)
            db.commit()
            session_id, student_id, user_id = row.id, student.id, user.id

        try:
            # Real bytes, written by the real recorder, under the real naming
            # rule — the id is the filename, which is the whole reason a sweep by
            # primary key alone can find them.
            rec = _recorder(session_id, max_bytes=10_000_000)
            rec.feed(TRACK_STUDENT, _pcm(BYTES_PER_SECOND))
            rec.feed(TRACK_INTERVIEWER, _pcm(BYTES_PER_SECOND))
            _close(rec)
            files = [track_path(session_id, track) for track in TRACKS]
            assert all(p.is_file() for p in files), "fixture must leave audio on disk"

            with SessionLocal() as db:
                fresh = db.get(InterviewSession, session_id)
                assert fresh.audio_recorded is False
                assert fresh.audio_path is None
                summary = retention.purge_expired(db, now=now)

            assert not any(p.exists() for p in files), (
                "a named student's voice outlived its retention window because "
                "the row it hung off never learned it was recorded"
            )
            assert summary["interview_audio_deleted"] >= 1
            assert summary["interviews_hard_delete_blocked"] == 0
            with SessionLocal() as db:
                assert db.get(InterviewSession, session_id) is None
        finally:
            with SessionLocal() as db:
                db.execute(
                    delete(InterviewSession).where(
                        InterviewSession.student_id == student_id
                    )
                )
                db.execute(delete(Student).where(Student.id == student_id))
                db.execute(delete(User).where(User.id == user_id))
                db.commit()


# ---------------------------------------------------------------------------
# Objection 4 — "voice is biometric-adjacent"
# ---------------------------------------------------------------------------
class TestTheTwoGates:
    """No flag, no bytes. No consent, no bytes. Ever.

    `recorder_for` is the only supported constructor precisely so that "when
    does REEP record a student's voice?" has one answer in one function.
    """

    def test_the_default_deployment_records_nothing(self, store, monkeypatch):
        """`interview_recording_enabled` is False in app/config.py and this is
        what that means at the capture site."""
        monkeypatch.setattr(settings, "interview_recording_enabled", False)

        def _explode(_user_id):  # pragma: no cover -- must not be reached
            raise AssertionError("the consent query ran with recording disabled")

        monkeypatch.setattr(interview_audio, "audio_consent_granted", _explode)
        assert recorder_for(uuid.uuid4().hex, "user-1") is None
        assert not list(store.glob("*.wav"))

    def test_no_consent_means_no_recorder(self, store, monkeypatch):
        monkeypatch.setattr(settings, "interview_recording_enabled", True)
        monkeypatch.setattr(interview_audio, "audio_consent_granted", lambda _u: False)
        assert recorder_for(uuid.uuid4().hex, "user-1") is None
        assert not list(store.glob("*.wav"))

    def test_both_gates_open_produces_a_recorder(self, store, monkeypatch):
        monkeypatch.setattr(settings, "interview_recording_enabled", True)
        monkeypatch.setattr(interview_audio, "audio_consent_granted", lambda _u: True)
        recorder = recorder_for(uuid.uuid4().hex, "user-1")
        assert isinstance(recorder, InterviewRecorder)
        assert recorder._max_bytes == settings.interview_recording_max_bytes

    def test_every_closed_gate_is_named_and_an_open_one_is_not(self, store, monkeypatch):
        """`recorder_or_reason` says WHICH gate refused (2026-09-17).

        The four Nones used to be indistinguishable outside the API log, and
        the Interview records screen rendered every one of them as "No audio"
        beside a grey Download button -- on a deployment whose only missing
        piece was the college's tick. The word is what the row carries and
        the screen turns into the switch to flip, so each gate must produce
        its own, in gate order, and a recorder must come with none.
        """
        from app.interview_audio import (
            SKIP_NO_CONSENT,
            SKIP_OPERATOR_OFF,
            SKIP_POLICY_OFF,
            SKIP_REASONS,
            SKIP_STORE_FULL,
            recorder_or_reason,
        )

        sid = uuid.uuid4().hex
        monkeypatch.setattr(settings, "interview_recording_enabled", False)
        assert recorder_or_reason(sid, "user-1") == (None, SKIP_OPERATOR_OFF)

        monkeypatch.setattr(settings, "interview_recording_enabled", True)
        assert recorder_or_reason(sid, "user-1", policy_allows_audio=False) == (
            None,
            SKIP_POLICY_OFF,
        )

        monkeypatch.setattr(interview_audio, "audio_consent_granted", lambda _u: False)
        assert recorder_or_reason(sid, "user-1") == (None, SKIP_NO_CONSENT)

        monkeypatch.setattr(interview_audio, "audio_consent_granted", lambda _u: True)
        monkeypatch.setattr(interview_audio, "_store_has_headroom", lambda: False)
        assert recorder_or_reason(sid, "user-1") == (None, SKIP_STORE_FULL)

        monkeypatch.setattr(interview_audio, "_store_has_headroom", lambda: True)
        recorder, reason = recorder_or_reason(sid, "user-1")
        assert isinstance(recorder, InterviewRecorder) and reason is None
        # The vocabulary the client maps to sentences: every word above is in it.
        assert {SKIP_OPERATOR_OFF, SKIP_POLICY_OFF, SKIP_NO_CONSENT, SKIP_STORE_FULL} <= set(
            SKIP_REASONS
        )

    @requires_db
    def test_consent_is_read_from_the_row_and_fails_closed(self, store, consent_user):
        """The grant is a ROW, and every way of not having one reads as False.

        Four cases, and the last two are the ones a boolean-on-users could not
        express: a grant that covers the interview but NOT the recording, and a
        grant that was withdrawn.
        """
        from app.models.interview import InterviewConsent

        user_id = consent_user

        # No grant at all.
        assert audio_consent_granted(user_id) is False

        def _grant(**kw):
            with SessionLocal() as db:
                db.execute(
                    delete(InterviewConsent).where(InterviewConsent.user_id == user_id)
                )
                db.add(
                    InterviewConsent(
                        user_id=user_id,
                        version=settings.interview_consent_version,
                        scope_live_ai=True,
                        scope_store_transcript=True,
                        **kw,
                    )
                )
                db.commit()

        # Consented to the interview and the transcript, refused the recording:
        # a lawful, expected outcome, and the interview still runs.
        _grant(scope_store_audio=False)
        assert audio_consent_granted(user_id) is False

        # Withdrawn. The row survives (the historical fact matters) and stops
        # counting the moment it is stamped.
        _grant(scope_store_audio=True, revoked_at=datetime.now(timezone.utc))
        assert audio_consent_granted(user_id) is False

        # Granted against copy from a different version of the terms — consent
        # is to WORDING, and is not retroactive.
        _grant(scope_store_audio=True)
        with SessionLocal() as db:
            db.execute(
                delete(InterviewConsent).where(InterviewConsent.user_id == user_id)
            )
            db.add(
                InterviewConsent(
                    user_id=user_id,
                    version="1999-01",
                    scope_live_ai=True,
                    scope_store_transcript=True,
                    scope_store_audio=True,
                )
            )
            db.commit()
        assert audio_consent_granted(user_id) is False

        # And the one arrangement that permits a recording.
        _grant(scope_store_audio=True)
        assert audio_consent_granted(user_id) is True

    def test_an_unreadable_database_means_do_not_record(self, monkeypatch):
        """"We could not check whether the student agreed" and "the student
        agreed" are not the same sentence, and only one of them may end with a
        recording of their voice."""

        class _Boom:
            def __enter__(self):
                raise RuntimeError("database is down")

            def __exit__(self, *exc):  # pragma: no cover
                return False

        monkeypatch.setattr("app.db.SessionLocal", lambda: _Boom())
        assert audio_consent_granted("user-1") is False


@pytest.fixture
def consent_user():
    """A throwaway user to hang consent rows off, and its id."""
    from app.models.interview import InterviewConsent
    from app.models.user import Role, User

    with SessionLocal() as db:
        user = User(
            email=f"ivaudio-{uuid.uuid4().hex[:10]}@bgscet.ac.in",
            name="Interview Audio Fixture",
            role=Role.STUDENT,
            password_hash="x",
        )
        db.add(user)
        db.commit()
        user_id = user.id

    yield user_id

    with SessionLocal() as db:
        db.execute(delete(InterviewConsent).where(InterviewConsent.user_id == user_id))
        db.execute(delete(User).where(User.id == user_id))
        db.commit()


# ---------------------------------------------------------------------------
# The engine side — capture is on the audio path, and cannot end an interview
# ---------------------------------------------------------------------------
class _FakeBrowser:
    def __init__(self) -> None:
        self.audio: list[bytes] = []
        # The engine checks this before every send: a browser that has gone
        # away must not be written to. Starlette's real socket carries it.
        self.client_state = WebSocketState.CONNECTED

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)

    async def send_text(self, text: str) -> None:  # pragma: no cover
        pass


class _FakeUpstream:
    """Bedrock, reduced to send(). One event document per call."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, event: dict) -> None:
        self.sent.append(event)


class TestTheEngineFeedsIt:
    def test_the_track_names_match_the_store(self):
        """The engine feeds the two names the store writes files under.

        The OpenAI relay spelled its own copies of these strings and this test
        existed because a divergence would write files the download endpoint
        could not find, with nothing raising. app/interview_nova.py imports them
        from the store instead, so the divergence is impossible by construction
        — and this assertion is what fails if somebody reintroduces local
        copies."""
        assert (nova.TRACK_STUDENT, nova.TRACK_INTERVIEWER) == SOURCE_TRACKS
        # SOURCE_TRACKS is the half of TRACKS an engine may feed: `mixed` is
        # derived at close and nothing on the audio path knows it exists.
        assert TRACK_MIXED in TRACKS and TRACK_MIXED not in SOURCE_TRACKS

    def _relay(self, recorder=None):
        """One engine wired to a fake browser and a fake Bedrock stream.

        Still named `_relay` so the tests below read unchanged: what they are
        about is the RECORDER, and which engine drives it is incidental.
        """
        browser = _FakeBrowser()
        session = NovaSonicSession(
            browser, "c0ffee123456", on_turn=None, recorder=recorder
        )
        session._upstream = _FakeUpstream()
        return session, browser

    def test_both_directions_are_captured_and_reported_on_the_outcome(self, store):
        """The student's uplink and the interviewer's downlink, through the real
        forwarding paths — including the odd-byte carry and the barge-in drop,
        because a recording that disagrees with what upstream heard is worse
        than no recording: it is a plausible one."""
        session_id = uuid.uuid4().hex
        recorder = _recorder(session_id, max_bytes=10_000_000)
        relay, browser = self._relay(recorder)
        outcomes = []
        relay._on_finalize = outcomes.append

        async def scenario():
            await relay._forward_client_audio(_pcm(4800))
            await relay._on_audio_output(
                {
                    "contentId": "a1",
                    "content": __import__("base64").b64encode(_pcm(9600)).decode(),
                }
            )
            await relay._finalize_session(_CLOSE_OK, "Interview complete")

        asyncio.run(scenario())

        outcome = outcomes[0]
        assert outcome.audio_recorded is True
        assert outcome.audio_path == session_id
        assert outcome.audio_truncated is False
        assert outcome.audio_duration_ms == (9600 * 1000) // BYTES_PER_SECOND
        # 4800 frames each: the student's 4800 bytes padded out to the session
        # length the interviewer's 9600 set.
        assert _read_wav(track_path(session_id, TRACK_STUDENT))[0].nframes == 4800
        assert _read_wav(track_path(session_id, TRACK_INTERVIEWER))[0].nframes == 4800
        # The recording is a side effect and never a substitute: the student
        # still heard the interviewer.
        assert browser.audio

    def test_no_recorder_means_the_outcome_says_nothing_was_kept(self, store):
        """The default everywhere. `audio_recorded=False` is a fact — "we know
        nothing was kept" — and not an absence of one."""
        relay, _browser = self._relay(None)
        outcomes = []
        relay._on_finalize = outcomes.append

        async def scenario():
            await relay._forward_client_audio(_pcm(4800))
            await relay._finalize_session(_CLOSE_OK, "Interview complete")

        asyncio.run(scenario())

        outcome = outcomes[0]
        assert outcome.audio_recorded is False
        assert outcome.audio_path is None
        assert outcome.audio_bytes is None
        assert outcome.audio_duration_ms is None
        assert outcome.audio_truncated is False
        assert not list(store.glob("*.wav"))

    def test_a_recorder_that_blows_up_never_ends_the_interview(self, store):
        """The discipline the transcript writes already follow: a live interview
        must never be ended by a failure to keep a record of it."""

        class _Broken:
            def feed(self, track, pcm):
                raise RuntimeError("the disk caught fire")

            def snapshot(self):
                return CaptureResult(False, None, 0, 0, True)

            async def aclose(self):
                raise RuntimeError("still on fire")

        relay, _browser = self._relay(_Broken())
        outcomes = []
        relay._on_finalize = outcomes.append

        async def scenario():
            await relay._forward_client_audio(_pcm(4800))
            await relay._finalize_session(_CLOSE_OK, "Interview complete")

        asyncio.run(scenario())

        # The student's audio still reached the model, and the record still
        # closed.
        assert relay._upstream.sent
        assert outcomes[0].status == "abandoned"
        # aclose() raised, so the fallback snapshot is what was recorded.
        assert outcomes[0].audio_recorded is False
        assert outcomes[0].audio_truncated is True

    def test_the_files_are_closed_even_when_finalization_never_runs(self, store):
        """run()'s own teardown is the last line of defence: an exception no
        `except*` clause matched leaves the finalizer unrun, and without this the
        session would end holding two open file handles."""
        session_id = uuid.uuid4().hex
        recorder = _recorder(session_id, max_bytes=10_000_000)
        relay, _browser = self._relay(recorder)

        async def scenario():
            await relay._forward_client_audio(_pcm(4800))
            # What run()'s finally does, on the path where nothing else ran.
            await relay._close_recorder()

        asyncio.run(scenario())

        assert not recorder._writers
        assert _read_wav(track_path(session_id, TRACK_STUDENT))[0].nframes == 2400


# ---------------------------------------------------------------------------
# The download endpoint — DIRECTOR/ADMIN only, both gates, and a subject re-check
# ---------------------------------------------------------------------------
@pytest.fixture
def audio_world(store):
    """A director, a mentor holding the student's group, the student, and one
    interview with a real recording on disk.

    Built here rather than borrowed from tests/test_interview_access.py's
    `world`: that module is owned by another track this wave, and a shared
    fixture edited from two places is how one of them ends up asserting on rows
    the other changed.
    """
    from types import SimpleNamespace

    from app.models.interview import InterviewSession
    from app.models.user import Mentor, Role, Student, User
    from app.security import SESSION_COOKIE, create_session_token

    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as db:

        def _user(label, role):
            u = User(
                email=f"ivaud-{label}-{tag}@bgscet.ac.in",
                name=f"Interview Audio {label}",
                role=role,
                password_hash="x",
            )
            db.add(u)
            db.flush()
            return u

        director_user = _user("director", Role.DIRECTOR)
        mentor_user = _user("mentor", Role.MENTOR)
        student_user = _user("student", Role.STUDENT)
        mentor = Mentor(user_id=mentor_user.id)
        db.add(mentor)
        db.flush()
        student = Student(user_id=student_user.id, mentor_id=mentor.id)
        db.add(student)
        db.flush()

        started = datetime.now(timezone.utc) - timedelta(minutes=20)
        recorded = InterviewSession(
            student_id=student.id,
            status="completed",
            close_code=1000,
            conn_id=uuid.uuid4().hex[:12],
            started_at=started,
            heartbeat_at=started,
        )
        silent = InterviewSession(
            student_id=student.id,
            status="completed",
            close_code=1000,
            started_at=started,
            heartbeat_at=started,
        )
        db.add_all([recorded, silent])
        db.commit()

        w = SimpleNamespace(
            student_id=student.id,
            recorded_id=recorded.id,
            silent_id=silent.id,
            user_ids=[director_user.id, mentor_user.id, student_user.id],
            student_ids=[student.id],
            mentor_ids=[mentor.id],
        )

        def _auth(**claims):
            return {"Cookie": f"{SESSION_COOKIE}={create_session_token(claims)}"}

        w.as_director = _auth(
            userId=director_user.id, email="d@x", name="D", role="ADMIN"
        )
        # ADMIN is the operator/"developer" account, and it is the ONLY role that
        # may hear a recording. It reuses the director's user row deliberately:
        # the gate is decided by the role claim, never by which user id happens
        # to be carrying it, and a test that gave ADMIN its own row could pass
        # while the endpoint keyed off something else.
        w.as_admin = _auth(
            userId=director_user.id, email="a@x", name="A", role="ADMIN"
        )
        w.as_mentor = _auth(
            userId=mentor_user.id,
            email="m@x",
            name="M",
            role="MENTOR",
            mentorId=mentor.id,
        )
        w.as_student = _auth(
            userId=student_user.id,
            email="s@x",
            name="S",
            role="STUDENT",
            studentId=student.id,
        )

    # A real recording, written by the real recorder, and the row updated to
    # point at it exactly as the finalizer will.
    rec = _recorder(w.recorded_id, max_bytes=10_000_000)
    rec.feed(TRACK_STUDENT, _pcm(BYTES_PER_SECOND))
    rec.feed(TRACK_INTERVIEWER, _pcm(BYTES_PER_SECOND * 2))
    result = _close(rec)
    with SessionLocal() as db:
        row = db.get(InterviewSession, w.recorded_id)
        row.audio_recorded = True
        row.audio_path = result.path
        row.audio_bytes = result.total_bytes
        row.audio_duration_ms = result.duration_ms
        db.commit()

    yield w

    with SessionLocal() as db:
        db.execute(
            delete(InterviewSession).where(
                InterviewSession.student_id.in_(w.student_ids)
            )
        )
        db.execute(delete(Student).where(Student.id.in_(w.student_ids)))
        db.execute(delete(Mentor).where(Mentor.id.in_(w.mentor_ids)))
        db.execute(delete(User).where(User.id.in_(w.user_ids)))
        db.commit()


@pytest.fixture(scope="module")
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import interview_records

    app = FastAPI()
    app.include_router(interview_records.staff_router)
    with TestClient(app) as c:
        yield c


def _audio_url(world, session_id=None, track=None) -> str:
    url = (
        f"/api/mentor/students/{world.student_id}/interviews/"
        f"{session_id or world.recorded_id}/audio"
    )
    return f"{url}?track={track}" if track else url


@requires_db
class TestTheDownload:
    def test_an_admin_can_play_every_track(self, api, audio_world):
        """All three, and the two SOURCES are the half that must not regress.

        The mix became the default; `?track=student` and `?track=interviewer`
        keep working exactly as before, because they are the faithful record and
        the mix is a derived listening copy. Every track is the same length --
        the fixture fed 1 s and 2 s, and the tails were padded to the session.
        """
        for track in TRACKS:
            r = api.get(
                _audio_url(audio_world, track=track), headers=audio_world.as_admin
            )
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "audio/wav"
            assert len(r.content) == 44 + BYTES_PER_SECOND * 2
            # RFC 6266, both parameters, and a name carrying the interview id
            # rather than the student's — the association lives in the database,
            # where access control lives with it.
            disposition = r.headers["content-disposition"]
            assert disposition.startswith("inline; filename=")
            assert "filename*=UTF-8''" in disposition
            assert audio_world.student_id not in disposition

    def test_the_default_track_is_the_mix(self, api, audio_world):
        """One file, both voices, "like a phone recording" -- what a reviewer
        opening this endpoint with no query string actually wants to hear.

        It used to default to the student's track, which handed a reviewer half
        a conversation and nothing on screen saying so. If this ever goes back,
        the two per-track downloads above are how you get the old behaviour;
        do not change the default to fix a client that hardcoded it.
        """
        r = api.get(_audio_url(audio_world), headers=audio_world.as_admin)
        assert r.status_code == 200
        assert len(r.content) == 44 + BYTES_PER_SECOND * 2
        assert "-mixed.wav" in r.headers["content-disposition"]

    def test_a_mentor_in_the_students_own_group_is_still_refused(self, api, audio_world):
        """`_require_developer` FIRST, and it is not softened by the group gate.

        This mentor passes `_assert_can_access_student` — the student is theirs —
        and still gets nothing, because a stored voice recording is ADMIN only.
        The two gates answer different questions and this endpoint needs both.
        """
        r = api.get(_audio_url(audio_world), headers=audio_world.as_mentor)
        assert r.status_code == 403

    def test_staff_read_everything_else_about_the_session_and_still_not_the_audio(
        self, api, audio_world
    ):
        """The asymmetry, pinned — this is the whole point of _require_developer.

        A staff member with legitimate access to this student gets 200 on the
        transcript, the report and `raw_response` for this very session, and 403
        on its audio. That is deliberate: every other staff read is placement
        business, and a voice recording is not. It exists so whoever OPERATES
        the system can hear what the engine did.

        WRITTEN AGAINST A MENTOR because DIRECTOR no longer exists (2026-09-10,
        see tests/test_no_director_privilege.py). The asymmetry it proved is
        unchanged and now rests on the role that actually holds it: a mentor with
        the student in their group. The test above proves the refusal happens
        even for that mentor; this one proves what the same session CAN reach, so
        the 403 cannot be dismissed as "they could not see this student anyway".

        If this ever fails because someone widened the gate, the fix is not to
        update the assertion. Widening hands the most sensitive bytes REEP stores
        to every placement account, for a question the transcript already answers.
        """
        r = api.get(_audio_url(audio_world), headers=audio_world.as_mentor)
        assert r.status_code == 403
        # And the refusal says which role is required, without naming the
        # student or confirming a recording exists.
        assert "administrator" in r.json()["detail"].lower()

        # The same session, same student, reading everything that IS theirs.
        readable = api.get(
            f"/api/mentor/students/{audio_world.student_id}"
            f"/interviews/{audio_world.recorded_id}",
            headers=audio_world.as_mentor,
        )
        assert readable.status_code == 200, readable.text

    def test_the_student_does_not_get_playback_of_their_own_recording(
        self, api, audio_world
    ):
        """§5.4 refused to hide the SCORE from the student and this hides the
        audio, which is a different judgement rather than a contradiction: the
        recording tells them nothing they were not present for, and a student
        endpoint would be the widest possible surface for the most sensitive
        bytes REEP holds. There is deliberately no student-side route either —
        see test_there_is_no_student_route_to_audio."""
        r = api.get(_audio_url(audio_world), headers=audio_world.as_student)
        assert r.status_code == 403

    def test_there_is_no_student_route_to_audio(self):
        from app.routers import interview_records

        assert not [
            route
            for route in interview_records.student_router.routes
            if "audio" in route.path
        ]

    def test_an_interview_with_no_recording_is_404_and_not_204(self, api, audio_world):
        """The flag is the fact, never `audio_path is not None`. 404 so a
        director cannot tell "not recorded" from "not a real id"."""
        r = api.get(
            _audio_url(audio_world, session_id=audio_world.silent_id),
            headers=audio_world.as_admin,
        )
        assert r.status_code == 404

    def test_a_session_id_belonging_to_another_student_is_404(self, api, audio_world):
        """§7.3's second check. The gate was handed the PATH's student id and can
        say nothing at all about a session id."""
        r = api.get(
            f"/api/mentor/students/{audio_world.student_id}/interviews/"
            f"{uuid.uuid4().hex}/audio",
            headers=audio_world.as_admin,
        )
        assert r.status_code == 404

    def test_an_unknown_track_is_422_and_never_a_silent_fallback(self, api, audio_world):
        """A reviewer who asked for the interviewer and was handed the student
        would be listening to the wrong voice with nothing on screen saying so."""
        r = api.get(
            _audio_url(audio_world, track="../../etc/passwd"),
            headers=audio_world.as_admin,
        )
        assert r.status_code == 422

    def test_a_row_that_claims_audio_the_disk_does_not_have_is_404(
        self, api, audio_world, store
    ):
        """The reaper ran between the row and the request, or the volume is not
        mounted. Nothing is broken for the caller, so 404 rather than 500 — but
        the server logs it, because a row claiming audio that is gone is how a
        deletion request quietly fails to be honoured."""
        delete_session_audio(audio_world.recorded_id, None)
        r = api.get(_audio_url(audio_world), headers=audio_world.as_admin)
        assert r.status_code == 404
