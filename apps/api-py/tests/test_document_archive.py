"""The permanent document archive's contract, pinned without S3 and without a
database.

`app/document_archive.py` and `app/archive_documents.py` are the only thing
standing between a deleted marksheet and a hole in a student's record. Every
other copy of an uploaded file in this deployment expires: the EFS volume's one
backup plan is bounded by `backupRetentionDays` (35, RDS's ceiling), the backup
rule that reaches past it selects the DATABASE ALONE, and the two `pg_dump`
tiers carry rows and never bytes.

The properties that make this worth having are the ones a test has to assert,
because the failure mode is silent and is noticed years later: that the archive
never blocks an upload, that the sweep NEVER deletes anything, that a file the
bucket already holds is not re-uploaded over its own Object Lock, and that an
unconfigured deployment says so instead of looking archived.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import archive_documents, document_archive

BUCKET = "reep-documents-archive-123456789012"


class FakeS3:
    """Records what it was asked to do and refuses to be asked for contents.

    `get_object` and `head_object` RAISE rather than returning something
    plausible, because "the task may write and must never read" is the rule
    this bucket is built on — the archive holds every document the college has,
    so a reader is one compromise away from exfiltrating all of it. A fake that
    politely answered a read would let that rule be broken by a future edit
    with every test still green.
    """

    def __init__(self, keys: list[str] | None = None, fail_put: bool = False) -> None:
        self.keys = list(keys or [])
        self.puts: list[dict] = []
        self.fail_put = fail_put
        self.deletes: list[str] = []

    def list_objects_v2(self, **kw):
        prefix = kw.get("Prefix", "")
        matching = [k for k in self.keys if k.startswith(prefix)]
        if "MaxKeys" in kw:
            matching = matching[: kw["MaxKeys"]]
        return {"Contents": [{"Key": k} for k in matching], "IsTruncated": False}

    def put_object(self, **kw):
        if self.fail_put:
            raise RuntimeError("S3 is having a bad minute")
        self.puts.append(kw)
        self.keys.append(kw["Key"])
        return {}

    def get_object(self, **kw):  # pragma: no cover - must never be reached
        raise AssertionError("the archive task holds no s3:GetObject")

    def head_object(self, **kw):  # pragma: no cover - must never be reached
        raise AssertionError("HeadObject is authorised by s3:GetObject; use list")

    def delete_object(self, **kw):  # pragma: no cover - must never be reached
        raise AssertionError("nothing in this archive is ever deleted")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(document_archive.settings, "document_archive_bucket", BUCKET, raising=False)
    monkeypatch.setattr(document_archive.settings, "document_archive_prefix", "", raising=False)
    monkeypatch.setattr(document_archive.settings, "document_archive_region", "", raising=False)
    return BUCKET


# ------------------------------------------------------------------- keys --


def test_the_key_is_the_stored_name(configured) -> None:
    """A restore is a join, not a search.

    `stored_name` is what every row in all six tables carries, so the key that
    is exactly that string turns "find the bytes for this row" into string
    concatenation. A date or owner in the key would have to be recomputed from
    a row that may be the row that was deleted.
    """
    assert document_archive.key_for("abc123.pdf") == "documents/abc123.pdf"


def test_a_blank_prefix_never_produces_a_leading_slash(configured) -> None:
    assert not document_archive.key_for("a.pdf").startswith("/")


def test_a_prefix_is_honoured_and_normalised(monkeypatch, configured) -> None:
    monkeypatch.setattr(document_archive.settings, "document_archive_prefix", "/reep/", raising=False)
    assert document_archive.key_for("a.pdf") == "reep/documents/a.pdf"


def test_the_two_stores_never_share_a_key_space(configured) -> None:
    """A document and a recording with the same name must not collide. They
    cannot today — one is `uuid4().hex + ext` and the other
    `<session>.<track>.wav` — but the roots make it structural rather than
    lucky, and let an operator holding no `s3:GetObject` tell the two apart
    from a listing alone."""
    doc = document_archive.key_for("x", root=document_archive.DOCUMENTS_ROOT)
    audio = document_archive.key_for("x", root=document_archive.AUDIO_ROOT)
    assert doc != audio


@pytest.mark.parametrize("bad", ["", "../etc/passwd", "a/b.pdf", "a\\b.pdf"])
def test_a_traversing_name_can_never_become_a_key(bad, configured) -> None:
    """In a bucket where every object is locked for years, an object written to
    the wrong key cannot be moved or removed. The store only ever mints a uuid,
    so this refuses what cannot happen today and keeps refusing it after the
    next writer is added."""
    with pytest.raises(ValueError):
        document_archive.key_for(bad)


# ------------------------------------------------------- the inline writer --


def test_an_upload_is_archived_as_it_is_stored(configured) -> None:
    client = FakeS3()
    assert document_archive.archive_bytes("a.pdf", b"%PDF-1.7", content_type="application/pdf", client=client) is True
    assert client.puts[0]["Key"] == "documents/a.pdf"
    assert client.puts[0]["Body"] == b"%PDF-1.7"


def test_the_put_sets_no_object_lock_of_its_own(configured) -> None:
    """The bucket's DEFAULT retention applies to every object written without
    an explicit one, so the lock belongs where a single deploy sets it. A
    retention baked in here would be baked into every object written before
    anybody noticed it was wrong, and could not then be shortened."""
    client = FakeS3()
    document_archive.archive_bytes("a.pdf", b"x", client=client)
    assert "ObjectLockMode" not in client.puts[0]
    assert "ObjectLockRetainUntilDate" not in client.puts[0]


def test_a_failed_archive_never_fails_the_upload(configured, caplog) -> None:
    """The one property the inline path must have. Six upload endpoints worked
    for a year before this bucket existed; an exception here would turn an S3
    blip into a student being told their valid certificate was rejected."""
    client = FakeS3(fail_put=True)
    with caplog.at_level("ERROR"):
        assert document_archive.archive_bytes("a.pdf", b"x", client=client) is False
    assert "archive_documents" in caplog.text


def test_an_unconfigured_deployment_archives_nothing_and_claims_nothing(monkeypatch) -> None:
    monkeypatch.setattr(document_archive.settings, "document_archive_bucket", "", raising=False)
    assert document_archive.archive_enabled() is False
    # No client is constructed, so boto3 need not even be installed.
    assert document_archive.archive_bytes("a.pdf", b"x") is False


# -------------------------------------------------------------- the sweep --


def _settled() -> datetime:
    """A `now` past the freshness floor, for tests about the AUDIO root.

    `archive_documents._QUIET_SECONDS` defers a recording that was written to
    recently, because the two per-speaker tracks of a LIVE interview sit in the
    store under their final names. A test that writes a file and sweeps in the
    same millisecond is describing exactly that state, so it has to say which
    one it means. The documents root is exempt (see `_needs_settling`) and its
    tests pass no clock at all.
    """
    return datetime.now(timezone.utc) + timedelta(seconds=archive_documents._QUIET_SECONDS + 60)


def _store(tmp_path: Path, *names: str) -> Path:
    d = tmp_path / "uploads"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"bytes of " + n.encode())
    return d


def test_the_sweep_uploads_what_the_bucket_does_not_have(monkeypatch, tmp_path, configured) -> None:
    _store(tmp_path, "one.pdf", "two.png")
    client = FakeS3(keys=["documents/one.pdf"])
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(archive_documents, "_store_roots", lambda: [("documents", tmp_path / "uploads")])

    summary = archive_documents.run()

    assert summary == {"seen": 2, "already": 1, "archived": 1, "deferred": 0, "failed": 0}
    assert [p["Key"] for p in client.puts] == ["documents/two.png"]


def test_the_sweep_never_deletes_anything(monkeypatch, tmp_path, configured) -> None:
    """THE PROPERTY THIS MODULE EXISTS FOR. An object with no file behind it is
    the NORMAL state of every document a student has since deleted from the
    website — that is the whole point of the archive. A sweep that reconciled
    in the other direction would destroy exactly the records this keeps."""
    _store(tmp_path, "kept.pdf")
    client = FakeS3(keys=["documents/gone-from-disk.pdf", "documents/kept.pdf"])
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(archive_documents, "_store_roots", lambda: [("documents", tmp_path / "uploads")])

    archive_documents.run()

    assert client.deletes == []
    assert "documents/gone-from-disk.pdf" in client.keys


def test_the_skip_list_matches_the_stores_own_constant() -> None:
    """THE GUARD THAT NEVER FIRED.

    `_SKIP_SUFFIXES` held `.partial` alone, written from AGENTS.md's prose about
    the docker-compose sidecar rather than from the recorder's own constant --
    which is `.part`. So the guard read correctly, tested green against a
    fixture named `.partial`, and matched nothing a real recorder ever wrote.
    Every truncated mixdown on disk at sweep time was eligible for upload into a
    bucket that cannot delete it.

    Comparing against the constant is what makes the two unable to drift; a
    second hand-written literal would only relocate the same mistake.
    """
    from app.interview_audio import _PARTIAL_SUFFIX

    assert _PARTIAL_SUFFIX in archive_documents._SKIP_SUFFIXES


@pytest.mark.parametrize("name", ["sess.mix.wav.part", "sess.mix.wav.partial"])
def test_an_in_flight_recording_is_never_uploaded(name, monkeypatch, tmp_path, configured) -> None:
    """Both spellings, because the recorder writes one and the prose says the
    other. In a bucket where every object is locked for years a truncated
    upload cannot be replaced or removed."""
    _store(tmp_path, "done.wav", name)
    client = FakeS3()
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(archive_documents, "_store_roots", lambda: [("documents", tmp_path / "uploads")])

    archive_documents.run()

    assert [p["Key"] for p in client.puts] == ["documents/done.wav"]


def test_a_nested_recording_is_archived_under_its_folder(monkeypatch, tmp_path, configured) -> None:
    """THE SUBDIRECTORY THE SWEEP USED TO SKIP SILENTLY.

    `_files_on_disk` did not recurse, on the stated grounds that both stores are
    flat. `voice_platform.api.call_close.platform_audio_dir()` is
    `interview_audio._store_root() / "platform"` -- a real subdirectory holding
    the platform's stereo call recordings -- so every one of them was skipped
    while the sweep reported a healthy pass.

    The folder rides in the ROOT, never in the stored_name: `key_for` refuses a
    name containing a separator, and that guard must stay, because an object
    written to the wrong key in this bucket cannot be moved or removed.
    """
    store = _store(tmp_path, "flat.wav")
    nested = store / "platform"
    nested.mkdir()
    (nested / "call-77.stereo.wav").write_bytes(b"stereo bytes")

    client = FakeS3()
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(archive_documents, "_store_roots", lambda: [("interview-audio", store)])

    summary = archive_documents.run(now=_settled())

    assert summary["seen"] == 2
    assert sorted(p["Key"] for p in client.puts) == [
        "interview-audio/flat.wav",
        "interview-audio/platform/call-77.stereo.wav",
    ]


def test_a_nested_file_already_archived_is_not_re_uploaded(monkeypatch, tmp_path, configured) -> None:
    """The membership test has to rebuild the same relative key the writer
    would use, or every nested file is re-PUT on every single sweep -- which on
    a versioned, Object-Locked bucket means a new version a night, forever."""
    store = _store(tmp_path)
    nested = store / "platform"
    nested.mkdir()
    (nested / "call-77.stereo.wav").write_bytes(b"stereo bytes")

    client = FakeS3(keys=["interview-audio/platform/call-77.stereo.wav"])
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(archive_documents, "_store_roots", lambda: [("interview-audio", store)])

    summary = archive_documents.run(now=_settled())

    assert summary == {"seen": 1, "already": 1, "archived": 0, "deferred": 0, "failed": 0}
    assert client.puts == []


def test_one_listing_serves_the_whole_store(monkeypatch, tmp_path, configured) -> None:
    """Not one `list_objects_v2` per file: that turns a ten-thousand-file store
    into ten thousand API calls a night."""
    _store(tmp_path, *[f"f{i}.pdf" for i in range(12)])
    calls: list[dict] = []

    class Counting(FakeS3):
        def list_objects_v2(self, **kw):
            calls.append(kw)
            return super().list_objects_v2(**kw)

    client = Counting()
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(archive_documents, "_store_roots", lambda: [("documents", tmp_path / "uploads")])

    archive_documents.run()

    assert len(calls) == 1


def test_a_dry_run_writes_nothing(monkeypatch, tmp_path, configured) -> None:
    _store(tmp_path, "one.pdf")
    client = FakeS3()
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(archive_documents, "_store_roots", lambda: [("documents", tmp_path / "uploads")])

    summary = archive_documents.run(dry_run=True)

    assert summary["archived"] == 1
    assert client.puts == []


def test_a_file_that_vanishes_mid_sweep_does_not_abort_it(monkeypatch, tmp_path, configured) -> None:
    """The remaining files are the reason this runs; a sweep that aborts on the
    first problem archives nothing on the night one file is unreadable.

    This is not a hypothetical race. The sweep lists the store and then reads
    each file, and `retention.purge_expired` deletes interview audio on its own
    nightly clock — so a recording listed at the top of the pass can genuinely
    be gone by the time this reaches it. The failure is counted and the sweep
    carries on; the exit code carries it out.
    """
    store = _store(tmp_path, "good.pdf")
    listed = [store / "good.pdf", store / "deleted-underneath-us.pdf"]
    client = FakeS3()
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(archive_documents, "_store_roots", lambda: [("documents", store)])
    monkeypatch.setattr(archive_documents, "_files_on_disk", lambda d: listed)

    summary = archive_documents.run()

    assert summary["archived"] == 1
    assert summary["failed"] == 1
    assert [p["Key"] for p in client.puts] == ["documents/good.pdf"]


def test_the_exit_code_tells_a_quiet_night_from_a_broken_one(monkeypatch, tmp_path, configured) -> None:
    _store(tmp_path, "one.pdf")
    monkeypatch.setattr(document_archive, "_client", lambda: FakeS3())
    monkeypatch.setattr(archive_documents, "_store_roots", lambda: [("documents", tmp_path / "uploads")])
    assert archive_documents.main([]) == 0

    monkeypatch.setattr(document_archive, "_client", lambda: FakeS3(fail_put=True))
    assert archive_documents.main([]) == 1


def test_an_unconfigured_sweep_says_so_rather_than_looking_healthy(monkeypatch, caplog) -> None:
    """A deployment with no archive bucket and a deployment whose files are all
    archived must never be distinguishable only by someone going to look."""
    monkeypatch.setattr(document_archive.settings, "document_archive_bucket", "", raising=False)
    with caplog.at_level("WARNING"):
        summary = archive_documents.run()
    assert summary["seen"] == 0
    assert "NO permanent copy" in caplog.text


# --------------------------------------------------- the freshness floor --


def test_a_live_interviews_tracks_are_deferred_not_truncated(
    monkeypatch, tmp_path, configured
) -> None:
    """THE DEFECT THIS FLOOR EXISTS FOR, pinned.

    `_SKIP_SUFFIXES` covers the MIXDOWN, which `interview_audio` writes through
    a `.part` name. It does not cover the two per-speaker SOURCE tracks: those
    are opened at their final `track_path` and flushed after every write so they
    stay playable at all times, which makes a live interview's tracks
    indistinguishable from a finished recording by name alone.

    Without the floor the 02:00 sweep PUTs whatever had been written by 20:30
    UTC into a versioned, Object-Locked bucket with no lifecycle rule -- and
    `rel_key in have` means the finished file is never re-PUT afterwards, so the
    archive keeps a two-minute fragment of that interview forever and reports it
    as archived every night for years.
    """
    store = _store(tmp_path)
    (store / "sess-live.student.wav").write_bytes(b"first two minutes")
    client = FakeS3()
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(
        archive_documents, "_store_roots", lambda: [("interview-audio", store)]
    )

    # Swept while the interview is still running: nothing is uploaded, and the
    # run says so in its own counter rather than reporting a clean night.
    summary = archive_documents.run()
    assert client.puts == []
    assert summary["deferred"] == 1
    assert summary["archived"] == 0
    assert summary["failed"] == 0

    # Tomorrow, untouched since, the same file is taken whole.
    summary = archive_documents.run(now=_settled())
    assert [p["Key"] for p in client.puts] == ["interview-audio/sess-live.student.wav"]
    assert summary["archived"] == 1 and summary["deferred"] == 0


def test_a_document_is_never_deferred(monkeypatch, tmp_path, configured) -> None:
    """THE OTHER HALF OF THE FLOOR, AND THE ONE THAT WAS WRONG FIRST.

    The floor was applied to both roots, which broke this module's stated
    contract on its first run after any upload. Nothing appends to a document:
    `save_bytes` writes it in a single `write_bytes` under a fresh `uuid4()`
    name and no second writer ever touches that path. And the only documents the
    sweep ever has work to do on are the ones whose best-effort inline PUT
    ALREADY FAILED -- so deferring them delays the repair of exactly those files
    by a night, every night, to close a window that does not exist.
    """
    _store(tmp_path, "just-uploaded.pdf")
    client = FakeS3()
    monkeypatch.setattr(document_archive, "_client", lambda: client)
    monkeypatch.setattr(
        archive_documents, "_store_roots", lambda: [("documents", tmp_path / "uploads")]
    )

    summary = archive_documents.run()

    assert [p["Key"] for p in client.puts] == ["documents/just-uploaded.pdf"]
    assert summary["deferred"] == 0 and summary["archived"] == 1
