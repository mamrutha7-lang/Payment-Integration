"""The permanent, WRITE-ONLY archive for every stored document.

WHY THIS EXISTS. Until this module, a file uploaded to REEP had exactly one
copy — the bytes on the EFS volume under `document_store`'s `stored_name` —
and `document_store.delete` is an `unlink`. The only thing standing behind
that unlink was the daily AWS Backup plan, whose lifecycle is
`backupRetentionDays` (35, and 35 is RDS's ceiling, not a choice). The archive
plan that reaches past it selects `[db_arn]` — **the database alone**, on
purpose — and the two `pg_dump` tiers and the identity ledger carry Postgres
rows, never file bytes. So a marksheet a student deleted was recoverable for
35 days and then gone in both regions at once, with nothing on any screen
saying so.

A college keeps a student's academic record for decades. Thirty-five days is
not a retention policy, it is the absence of one.

**THE LIVE STORE STAYS EXACTLY AS IT IS.** This is not a tombstone, a trash
folder or a soft delete on the filesystem. A student deletes an upload and the
bytes leave EFS that second, the row goes, the screen is correct, the quota is
freed. What changes is that by then the file is ALREADY in a versioned,
Object-Locked bucket with no lifecycle rule at all, written at the moment it
was first stored. The website's delete and the archive's permanence are
different promises about different copies, which is the only way to keep both.

**THE TASK MAY WRITE AND MUST NEVER READ**, and that is the same rule
`app/backup_database.py` states for the dump buckets, for a stronger reason
here: this bucket accumulates every marksheet, certificate, photograph, CV,
staff signature and recorded interview in the deployment. A task that can read
it is one compromise away from exfiltrating every document the college holds,
past rule 1 and past every control on the database. The grant is
`s3:PutObject` plus `s3:ListBucket`, and `is_archived` therefore asks with
`list_objects_v2` rather than the obvious `head_object` — S3 authorises
HeadObject with `s3:GetObject` and has no separate permission for it, so the
obvious spelling is read access to the whole archive.

**TWO WRITERS, AND THE SECOND ONE IS THE GUARANTEE.**

* `archive_bytes` runs INLINE, inside `document_store.save_bytes`, so a file
  is durable within milliseconds of being accepted. It is BEST-EFFORT: an S3
  hiccup logs at ERROR and the upload still succeeds. Failing the upload
  instead would mean a student cannot submit their marksheet because a bucket
  in another region is having a bad minute, and it would put S3 on the
  critical path of six endpoints that have worked without it for a year.
* `python -m app.archive_documents` is what makes the promise true anyway. It
  walks the stores on disk and uploads everything the bucket does not already
  hold, so an inline failure costs hours rather than the file. It is
  idempotent, it is the only thing that archives INTERVIEW AUDIO (see below),
  and it is the reason the inline path is allowed to be best-effort at all.

**INTERVIEW AUDIO IS ARCHIVED BY THE SWEEP ONLY, NEVER INLINE.** The recorder
writes its two WAVs incrementally and closes them in `run()`'s `finally`;
there is no moment during a live interview when a complete file exists to
upload, and a mid-call PUT would ship a truncated container and put an S3
round trip on the interview's hot path. The sweep sees a finished file or it
sees nothing.

**KEYS ARE FLAT AND ARE THE `stored_name`.** `<prefix>/documents/<stored_name>`
and `<prefix>/interview-audio/<relative path>`. `stored_name` is a `uuid4().hex`
plus the sniffed extension and is UNIQUE across every table that points into
the store, so it needs no date, no owner and no table name to disambiguate —
and it is exactly the string the database row carries, so a restore is a join
and not a search. A date prefix was considered and rejected: it would have to
be derived from the row (which may be the row that was deleted) or from the
file's mtime (which EFS's IA transition does not promise to preserve), and a
key nobody can recompute is a key nobody can look up.

Deliberately NOT recorded here: who owned the file, which table pointed at it,
or what it was called. That is the database's job, it reaches the archive
through the `pg_dump` tiers keyed by the same `stored_name`, and a bucket that
also held the metadata would be a second, unindexed, permanently-locked copy
of student PII to keep correct.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import settings

log = logging.getLogger("reep.document_archive")

#: Where documents and audio sit under the bucket's configured prefix. Two
#: roots rather than one flat namespace because they come from two stores with
#: two different writers, and an operator listing the bucket should be able to
#: tell "every document" from "every recording" without opening anything —
#: which, holding no `s3:GetObject`, they cannot do anyway.
DOCUMENTS_ROOT = "documents"
AUDIO_ROOT = "interview-audio"


def archive_enabled() -> bool:
    """Whether an archive bucket is configured.

    Blank is a SUPPORTED configuration and is what every development machine
    and the whole test suite run on — the same honesty rule the identity
    ledger and the dump buckets follow. It is not silently equivalent to
    "archived": `python -m app.archive_documents` says so on every run rather
    than letting a reader assume the permanent copy exists.
    """
    return bool(settings.document_archive_bucket.strip())


def _client():
    """boto3, imported here and not at module scope.

    `document_store` imports this module on every request path that stores a
    file, and a deployment with no archive bucket must not pay for the client
    at boot or need the dependency present at all — the same reasoning
    `app/ai/llm.py` and `app/backup_database.py` both state.
    """
    import boto3

    return boto3.client("s3", region_name=settings.document_archive_region or None)


def key_for(stored_name: str, *, root: str = DOCUMENTS_ROOT) -> str:
    """`<prefix>/<root>/<stored_name>`, with the prefix normalised.

    The separator guard is `document_store.read_bytes`'s, repeated rather than
    imported: a name carrying a separator would write outside `root` and, in a
    bucket where every object is locked for years, a key in the wrong place
    cannot be moved or removed. The store only ever mints `uuid4().hex + ext`,
    so this refuses something that cannot happen today and will keep refusing
    it after the next writer is added.
    """
    if not stored_name or "/" in stored_name or "\\" in stored_name or ".." in stored_name:
        raise ValueError(f"unsafe stored_name for an archive key: {stored_name!r}")
    prefix = settings.document_archive_prefix.strip("/")
    head = f"{prefix}/" if prefix else ""
    return f"{head}{root}/{stored_name}"


def is_archived(stored_name: str, *, root: str = DOCUMENTS_ROOT, client=None) -> bool:
    """Whether this object is already in the archive.

    LIST, NOT HEAD, AND THE DIFFERENCE IS THE TASK'S ENTIRE READ ACCESS — see
    the module docstring, and `backup_database.month_is_archived`, which makes
    the same call for the same reason. The match is on the EXACT key and not
    on the prefix: `uuid4` names collide with nothing today, but a later key
    scheme that appended a suffix would make a prefix match quietly answer
    "already archived" for an object that is not there.
    """
    client = client or _client()
    key = key_for(stored_name, root=root)
    resp = client.list_objects_v2(
        Bucket=settings.document_archive_bucket.strip(), Prefix=key, MaxKeys=1
    )
    return any(o.get("Key") == key for o in resp.get("Contents", []))


def archive_bytes(
    stored_name: str,
    content: bytes,
    *,
    content_type: str = "application/octet-stream",
    root: str = DOCUMENTS_ROOT,
    client=None,
) -> bool:
    """PUT one object. Returns True if it was written, False if not attempted.

    RAISES NOTHING THE CALLER MUST CATCH. The inline caller is
    `document_store.save_bytes`, on the request path of six upload endpoints
    that worked for a year before this bucket existed; an exception here would
    turn every S3 blip into a student being told their valid certificate was
    rejected. The failure is logged at ERROR with the key, and
    `python -m app.archive_documents` picks the file up on its next pass —
    which is why that sweep is the guarantee and this is the fast path.

    `ObjectLockMode` is NOT set on the PUT. The bucket carries a DEFAULT
    retention, which S3 applies to every object written without an explicit
    one, so the lock belongs to the bucket where a single deploy sets it —
    rather than to this call site, where a wrong value would be baked into
    every object written before somebody noticed and could not be shortened.
    """
    if not archive_enabled():
        return False
    try:
        key = key_for(stored_name, root=root)
    except ValueError:
        log.exception("Refusing to archive %r: unsafe key", stored_name)
        return False
    try:
        (client or _client()).put_object(
            Bucket=settings.document_archive_bucket.strip(),
            Key=key,
            Body=content,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )
    except Exception:  # noqa: BLE001 — reported, never fatal to an upload
        log.exception(
            "Could not archive %s to the permanent document archive. The file is "
            "on the volume and the upload stands; `python -m app.archive_documents` "
            "will retry it.",
            key,
        )
        return False
    log.info("Archived %s", key)
    return True


def archive_file(path: Path, *, stored_name: str, root: str, client=None) -> bool:
    """`archive_bytes` for a file already on disk — the sweep's entry point.

    Read whole rather than streamed: the store's own ceiling is
    `document_store.MAX_BYTES` (10 MB) and an interview's WAV is bounded by
    `interview_recording_max_bytes` (128 MB), both of which a task with 20 GiB
    of ephemeral storage holds comfortably, and `put_object` with a bytes body
    is one call where `upload_file` is a multipart state machine that can leave
    parts behind in a bucket nothing is allowed to clean up.
    """
    try:
        content = path.read_bytes()
    except OSError:
        log.exception("Could not read %s for archiving", path)
        return False
    return archive_bytes(stored_name, content, root=root, client=client)
