"""`python -m app.archive_documents` — the sweep that makes the permanent
document archive a guarantee rather than a best effort.

`document_store.save_bytes` PUTs every new file to the archive INLINE, so a
document is durable within milliseconds of being accepted. That path is
deliberately best-effort: it raises nothing, because an S3 blip must never be
the reason a student is told their valid certificate was rejected (see
`app/document_archive.archive_bytes`). This module is the other half of that
bargain. It walks the stores ON DISK, asks the bucket what it already holds,
and uploads the difference — so an inline failure costs hours instead of the
file.

It is also the ONLY writer that archives INTERVIEW AUDIO. The recorder writes
its two WAVs incrementally and closes them in `run()`'s `finally`; during a
live interview there is no complete file to upload, and a mid-call PUT would
ship a truncated container and put an S3 round trip on the interview's hot
path.

**THE SWEEP DOES NOT GET "A FINISHED FILE OR NOTHING", WHICH IS WHAT THIS
PARAGRAPH USED TO CLAIM.** `_SKIP_SUFFIXES` keeps an in-flight recording out of
the bucket BY ITS NAME, and that guard covers THE MIXDOWN ONLY:
`interview_audio` writes the derived mix to a `.part` and renames it on
success, but the two per-speaker SOURCE tracks are opened directly at their
final `track_path` and flushed after every single write — deliberately, so they
are "playable at all times". So an interview that is live at 20:30 UTC has two
growing, ordinary-looking WAVs sitting in the store under the names they will
keep, and nothing about a name tells them apart from a finished recording. Only
the clock does, which is what `_QUIET_SECONDS` below reads.

**IT ONLY EVER WRITES.** There is no delete, no rename, no move and no
reconciliation in the other direction. A file on the volume that the bucket
does not have is uploaded; an object in the bucket with no file behind it is
left exactly where it is, because that is the normal and intended state of
every document a student has since deleted from the website — the whole point
of the archive. A sweep that "tidied" those away would delete the only
permanent copy of precisely the records this exists to keep.

**ONE LISTING, NOT ONE QUESTION PER FILE.** The obvious shape is
`is_archived()` per file, which is one `list_objects_v2` round trip each and
turns a ten-thousand-file store into ten thousand API calls every night. The
bucket's keys are listed ONCE per root into a set and the difference is taken
locally. `document_archive.is_archived` stays for the single-file question a
test or an operator asks.

**IT NEEDS NO DATABASE AND DELIBERATELY DOES NOT OPEN ONE.** The filesystem is
the authority on which files exist — `retention._delete_interview_audio` makes
the same argument at length, and its reasoning applies exactly: a row records
what a writer BELIEVED and managed to write down, and the failure mode that
matters is the writer dying before it could. A sweep driven by `uploads` and
`interview_sessions` would silently skip every file whose row never landed.

Exit code 0 means every FINISHED file the volume holds is in the archive. The
qualifier is new and it is carried in the summary rather than left to be
inferred: a file younger than `_QUIET_SECONDS` is DEFERRED — counted on its own
line, logged by name, and picked up by tomorrow's sweep — because a file still
being appended to is not an artefact yet. Without that line a night on which
the sweep deliberately postponed an interview's recording would report "0
failed" and read as "everything is safe". Non-zero still means at least one
finished file could NOT be archived, so whatever supervises this can tell a
quiet night from a broken one without parsing prose — `app/retention_job.py`'s
rule. A deferral is deliberately NOT a failure: an interview still running when
the sweep starts is a healthy deployment, and a job that exits non-zero every
time somebody books a late slot is a job whose alarm nobody reads.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import document_archive
from .config import settings
from .document_archive import AUDIO_ROOT, DOCUMENTS_ROOT

log = logging.getLogger("reep.archive_documents")

#: Files the sweep must never upload: a recording in flight, or the wreckage of
#: a process that died mid-write. Neither is a finished artefact, and in a
#: bucket where every object is locked for years a truncated upload cannot be
#: replaced or removed -- exactly the trap `AGENTS.md` records about porting
#: the `.partial` rule to S3.
#:
#: THE FIRST VERSION OF THIS TUPLE HELD `.partial` ALONE AND NEVER FIRED ONCE.
#: `interview_audio._PARTIAL_SUFFIX` is `.part`, not `.partial` -- the comment
#: here was written from the AGENTS.md prose rather than from the constant, so
#: the guard read correctly, tested green, and matched nothing. Every truncated
#: mixdown on disk at 20:30 UTC was eligible for upload into a bucket that
#: cannot delete it. Both spellings are listed now and
#: `tests/test_document_archive.py` compares this tuple against the store's own
#: constant, so the two cannot drift again.
_SKIP_SUFFIXES = (".part", ".partial")

#: THE FRESHNESS FLOOR: how long a file must have been left alone before this
#: sweep will accept it as finished. It is the same judgement `_SKIP_SUFFIXES`
#: makes about a `.part` -- "this is not an artefact yet" -- made about the
#: clock instead of the name, and it exists because the name half covers only
#: the mixdown. The two per-speaker tracks of a LIVE interview are sitting in
#: the audio store right now under their final names, flushed after every write
#: so they stay playable, indistinguishable from a recording that finished an
#: hour ago except by when they were last touched.
#:
#: TWO HOURS, AND THE NUMBER IS SET BY THE LONGEST INTERVIEW A COLLEGE MAY
#: LEGALLY CONFIGURE -- not by the defaults. It is tempting to size this against
#: `settings.nova_sonic_connection_seconds` (480) and
#: `settings.interview_max_seconds` (900), and that is the mistake the first
#: version of this constant made at 1800 s, calling it "twice the longest legal
#: interview". It is not: `interview_policies.time_limit_seconds` is per-college
#: and `ck_interview_policy_bounds` admits anything in 60..3600
#: (`app/models/interview_policy.py`), so a college that sets an hour has
#: interviews the old floor cleared with 30 minutes to spare in the wrong
#: direction.
#:
#: The floor has to clear the WHOLE interview and not merely the gap between two
#: writes: a track gains bytes only when its own speaker does -- `_write_blobs`
#: materialises a silence segment when the NEXT real one arrives, and
#: `_pad_tails` squares the two up at close -- so a student who sits quiet
#: through a long stretch of the interviewer's leaves their own track untouched
#: for minutes at a time, and in the limit its mtime is as old as the interview
#: itself. 7200 s is twice the 3600 s ceiling, so it clears the longest session
#: any policy can ask for with the same headroom
#: `interview_orphan_grace_seconds` takes over its own number.
#:
#: WHAT THE LARGER NUMBER COSTS is one more night's wait for a recording that
#: finished within two hours of 20:30 UTC, and nothing else: an interview taken
#: during the working day is hours stale by the time the sweep runs and is taken
#: on the same night. That is the cheap side of the asymmetry below.
#:
#: THE ASYMMETRY IS THE WHOLE ARGUMENT. Being wrong in this direction costs ONE
#: NIGHT'S DELAY: tomorrow's sweep finds the file untouched, uploads it, and
#: nobody ever knows. Being wrong in the other direction is PERMANENT. The
#: archive bucket is versioned and Object-Locked with no lifecycle rule at all,
#: so a truncated two-minute fragment written under an interview's key can
#: never be replaced or removed -- and `rel_key in have` in `run` means the
#: finished file is never re-PUT afterwards, so the sweep would find that key,
#: call the recording archived, and go on saying so every night for years.
_QUIET_SECONDS = 7200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _has_settled(path: Path, *, cutoff: float) -> bool:
    """Has `path` been left alone long enough to be a finished file?

    Raises `OSError` rather than answering for a file it cannot stat -- the
    caller counts that as a failure, the way it already counts a file that
    vanishes between the listing and the read.

    Applied to the roots `_needs_settling` names, which is the audio half only.
    """
    return path.stat().st_mtime <= cutoff


def _needs_settling(file_root: str) -> bool:
    """Does this root hold files that are written INCREMENTALLY?

    THE FLOOR IS A PROPERTY OF THE WRITER, NOT OF THE ARCHIVE, so it is asked
    per root rather than applied to everything -- and a root added later has to
    answer this question, which is the point of it being a function with a name
    instead of a comparison buried in `run`.

    `AUDIO_ROOT` answers yes. `interview_audio` opens the two per-speaker tracks
    at their FINAL `track_path` and flushes after every write so they stay
    playable at all times, so a live interview's tracks are indistinguishable
    from a finished recording except by when they were last touched. That is the
    case `_QUIET_SECONDS` exists for, and it covers the platform's nested
    `platform/` folder too, which is why the test is a prefix and not equality.

    `DOCUMENTS_ROOT` answers NO, and applying the floor there was a real defect
    rather than harmless caution. Nothing ever appends to a document:
    `document_store.save_bytes` holds the whole file in memory (bounded by its
    own 10 MB ceiling), writes it in a single `write_bytes` call, and the row
    that points at it is not committed until that returns. A `stored_name` is a
    fresh `uuid4().hex` every time, so no second writer ever touches the same
    path. A document is therefore complete the instant it exists and there is no
    in-progress state to wait out.

    What deferring it COSTS is the part that settles the argument. The inline
    PUT is best-effort by contract and this sweep is the guarantee that makes
    that acceptable -- so the only documents the sweep ever has work to do on
    are precisely the ones whose inline PUT ALREADY FAILED. A floor here delays
    the repair of exactly those files by a night, every night, for no window it
    could close; and it breaks this module's stated contract that exit code 0
    means every file the volume holds is in the archive, on the very first run
    after any upload.
    """
    return file_root == AUDIO_ROOT or file_root.startswith(f"{AUDIO_ROOT}/")


def _store_roots() -> list[tuple[str, Path]]:
    """`(archive root, directory)` for each store, resolved at call time.

    `interview_audio` is imported INSIDE the function, the guard
    `retention._delete_interview_audio` uses and for its reason: this module's
    document half must keep working on a slim image, or any host, where that
    module will not import. An ImportError costs the audio half of one sweep,
    never the whole run.
    """
    roots: list[tuple[str, Path]] = [(DOCUMENTS_ROOT, settings.uploads_path)]
    try:
        from .interview_audio import _store_root

        roots.append((AUDIO_ROOT, _store_root()))
    except ImportError:
        log.warning(
            "app.interview_audio will not import here, so interview recordings "
            "were NOT swept this pass. The document half ran normally."
        )
    return roots


def _files_on_disk(directory: Path) -> list[Path]:
    """Every regular file under `directory`, RECURSIVELY.

    IT USED TO REFUSE TO RECURSE, on the stated grounds that "both stores are
    FLAT by design". That was true of the two stores this module was written
    against and false of the volume it sweeps: `voice_platform.api.call_close`'s
    `platform_audio_dir()` is `interview_audio._store_root() / "platform"`, a
    real subdirectory holding the platform's stereo call recordings. A
    non-recursive listing skipped every one of them SILENTLY -- the sweep
    reported a healthy pass having never seen the files.

    A docstring that asserts a layout is not a guarantee of one. Recursing costs
    nothing here (both roots are small) and removes the class of bug where a new
    writer adds a folder and the archive quietly stops covering it.
    """
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.rglob("*")
        if p.is_file() and not p.name.endswith(_SKIP_SUFFIXES)
    )


def _key_parts(path: Path, directory: Path, root: str) -> tuple[str, str] | None:
    """`(archive root, stored_name)` for a file, carrying any subdirectory into
    the ROOT rather than into the name.

    `document_archive.key_for` refuses a `stored_name` containing a separator,
    and that guard must stay: in a bucket where every object is locked for
    years, an object written to the wrong key cannot be moved or removed. So a
    nested file keeps a bare name and its folder is appended to the root --
    `interview-audio/platform/<name>`, never a path smuggled through as a name.

    Returns None for a path that escapes the root. `rglob` cannot produce one,
    which is exactly why it is checked: this computes a key that is immutable
    once written.
    """
    try:
        rel = path.relative_to(directory)
    except ValueError:
        log.error("Refusing to archive %s: outside the store root %s", path, directory)
        return None
    if any(part in ("..", "") for part in rel.parts[:-1]):
        log.error("Refusing to archive %s: unsafe subdirectory", path)
        return None
    sub = rel.parent.as_posix()
    return (root if sub == "." else f"{root}/{sub}"), path.name


def _archived_names(root: str, client) -> set[str]:
    """Every `stored_name` the bucket already holds under `root`.

    `list_objects_v2` under `s3:ListBucket`, paginated by hand rather than
    through a paginator so the one call this module makes against boto3 is the
    same one `document_archive.is_archived` and
    `backup_database.month_is_archived` make. Key names, never contents: this
    task holds no `s3:GetObject` and must not start needing one.
    """
    prefix = document_archive.key_for("_", root=root)[: -len("_")]
    bucket = settings.document_archive_bucket.strip()
    names: set[str] = set()
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            key = obj.get("Key", "")
            if key.startswith(prefix):
                names.add(key[len(prefix) :])
        if not resp.get("IsTruncated"):
            return names
        token = resp.get("NextContinuationToken")
        if not token:
            return names


def run(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, int]:
    """Sweep every store. Returns counts; never raises for one bad file.

    A single unreadable file or a single refused PUT is COUNTED AND REPORTED,
    not fatal: the remaining ten thousand files are the reason this runs, and a
    sweep that aborts on the first problem archives nothing on the night one
    file is locked. The exit code carries the failure instead.

    `now` is a parameter so the freshness floor can be tested without reaching
    for the clock, the shape `retention.purge_expired`, `backup_database.run`
    and `export_identity.run` all use. It must be timezone-aware: the
    comparison happens in epoch seconds against `st_mtime`, and a naive
    datetime would be read as local time and shift the floor by the offset.
    """
    summary = {"seen": 0, "already": 0, "archived": 0, "deferred": 0, "failed": 0}
    cutoff = (now or _utcnow()).timestamp() - _QUIET_SECONDS
    if not document_archive.archive_enabled():
        # SAID OUT LOUD, every run. A deployment with no archive bucket is a
        # supported configuration and it is also a deployment whose files have
        # no copy outliving `backupRetentionDays` — the two facts must never
        # be distinguishable only by someone going to look.
        log.warning(
            "DOCUMENT_ARCHIVE_BUCKET is not set, so this deployment keeps NO "
            "permanent copy of its uploaded files. The only thing behind them "
            "is the daily backup plan's %s-day lifecycle on the volume.",
            "35",
        )
        return summary

    client = document_archive._client()
    for root, directory in _store_roots():
        files = _files_on_disk(directory)
        if not files:
            log.info("%s: nothing on disk at %s", root, directory)
            continue
        try:
            have = _archived_names(root, client)
        except Exception:  # noqa: BLE001
            log.exception(
                "Could not list the archive under %s, so its %d file(s) were not "
                "swept this pass.",
                root,
                len(files),
            )
            summary["failed"] += len(files)
            continue
        for path in files:
            summary["seen"] += 1
            parts = _key_parts(path, directory, root)
            if parts is None:
                summary["failed"] += 1
                continue
            file_root, stored_name = parts
            # `have` holds names RELATIVE TO THE ROOT PREFIX, so a nested file's
            # entry there is "platform/<name>" while its key is built from the
            # deeper root. Rebuilding the same relative string is what makes the
            # membership test agree with the key that would be written.
            rel_key = stored_name if file_root == root else f"{file_root[len(root) + 1:]}/{stored_name}"
            if rel_key in have:
                summary["already"] += 1
                continue
            # THE FRESHNESS FLOOR IS READ HERE AND NOT EARLIER, after the
            # bucket has already answered for this key. A file that is both
            # fresh and archived — every recording the inline path accepted in
            # the last half hour — is simply archived, and reporting it as
            # deferred would fill the summary with work nobody is waiting on.
            # `deferred` therefore means exactly one thing: the archive does
            # not hold this file and the sweep chose not to upload it tonight.
            #
            # And it is asked of the ROOT first: only a store whose files are
            # written incrementally has an in-progress state to wait out. See
            # `_needs_settling`, which argues why the documents root is exempt
            # rather than merely unaffected.
            settled = True
            if _needs_settling(file_root):
                try:
                    settled = _has_settled(path, cutoff=cutoff)
                except OSError:
                    log.exception(
                        "Could not read the modification time of %s, so the sweep "
                        "cannot tell whether it is finished and will not upload it.",
                        path,
                    )
                    summary["failed"] += 1
                    continue
            if not settled:
                # Named, every time, at INFO. The count alone would say a file
                # was held back; an operator asking "which recording is missing
                # from last night" needs the name, and tomorrow's log is where
                # they find out it arrived.
                log.info(
                    "%s: deferring %s — written to within the last %d s, so it "
                    "is presumed to be still in progress. Tomorrow's sweep "
                    "takes it.",
                    file_root,
                    stored_name,
                    _QUIET_SECONDS,
                )
                summary["deferred"] += 1
                continue
            if dry_run:
                log.info("%s: would archive %s", file_root, stored_name)
                summary["archived"] += 1
                continue
            if document_archive.archive_file(
                path, stored_name=stored_name, root=file_root, client=client
            ):
                summary["archived"] += 1
            else:
                summary["failed"] += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.archive_documents",
        description=(
            "Upload every stored document and finished interview recording that "
            "the permanent archive does not already hold. A file written to in "
            "the last half hour is presumed unfinished and left for the next "
            "run, and counted as deferred. Writes only: nothing on the volume "
            "or in the bucket is ever deleted, renamed or moved."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be uploaded and upload nothing.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    summary = run(dry_run=args.dry_run)
    # `deferred` gets its own place in this line rather than being folded into
    # "already" or left out: this one line is what a supervisor's log capture
    # keeps, and "0 failed" on a night the sweep postponed an interview's
    # recording would read as "everything is in the archive".
    log.info(
        "%s: %d seen, %d already archived, %d %s, %d deferred as unfinished, "
        "%d failed",
        "Dry run" if args.dry_run else "Sweep",
        summary["seen"],
        summary["already"],
        summary["archived"],
        "to upload" if args.dry_run else "archived",
        summary["deferred"],
        summary["failed"],
    )
    # A deferral is NOT a failure and must not colour the exit code. The files
    # it names are healthy — an interview in progress, a document accepted a
    # minute ago — and tomorrow's sweep takes them; a job that exits non-zero
    # whenever somebody is mid-interview at 02:00 is a job whose alarm gets
    # muted, and then the night that really breaks says nothing either.
    return 1 if summary["failed"] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
