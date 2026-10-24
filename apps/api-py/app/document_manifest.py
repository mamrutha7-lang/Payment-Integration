"""The one way to put a file into the store, and the one way to say it is gone.

`app/document_store.py` writes bytes and knows nothing about who owns them —
deliberately, because it holds no ORM and that is what lets it be tested with
four integers and no database. `app/document_archive.py` copies those bytes
somewhere permanent, and knows even less. Neither can answer the question a
restore actually asks: *whose file was `3f2a…9c.pdf`, what was it called, and
when did it stop being live?*

This module is that answer. `save_and_record` is `document_store.save_bytes`
plus the `archived_documents` row, in one call and one transaction, and it is
the ONLY spelling the routers use.

**WHY IT IS A WRAPPER AND NOT A CONVENTION.** `document_store`'s own docstring
records what happened the last time an invariant lived in the callers: the
comment said enforcement was in "routers/student.py create_upload — the single
caller of save_bytes", and three writers later `routers/alumni.py` had no quota
check at all. The lesson taken then was to move the ARITHMETIC into the store
and refuse a caller that brings no `VolumeQuota`. The same lesson applies here
and the store cannot host it, because a manifest row needs a `Session`. So the
enforcement is one rung up: routers call this, and
`tests/test_codebase_guards.py` fails the build if a module under `app/routers/`
imports `save_bytes` directly. A file that reaches the store without a manifest
row is a file in the permanent archive that nothing can ever name.

**THE ROW IS ADDED, NEVER COMMITTED HERE.** `db.add` and `db.flush`, so the
manifest lands in the same transaction as the live row the caller is about to
write. A separate commit would make "file recorded, upload rolled back" and
"upload committed, file unrecorded" both reachable; the first is harmless
noise, the second is the failure this module exists to prevent.

**`release` IS BEST-EFFORT AND ITS ABSENCE IS NEVER READ AS "STILL LIVE".** The
bytes are in the archive whether or not anybody stamps `released_at`. A
manifest that refused to record a file because it could not also record the
release would trade the thing that matters for the thing that does not.

**AND `reattribute`, WHICH IS NEITHER.** A file can change hands without
changing bytes -- approving an application moves the applicant's CV into the
new student's uploads under the same `stored_name` -- and the row that answers
*whose file was this* has to move with it. It is an in-place UPDATE of the
owner pair and nothing else, which is the one edit an append-only manifest must
still allow; see the function for why it cannot be spelled `release` + a second
`save_and_record`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import document_store
from .document_store import VolumeQuota
from .models.archived_document import ArchivedDocument, DocumentOwnerKind

log = logging.getLogger("reep.document_manifest")


def save_and_record(
    db: Session,
    content: bytes,
    *,
    quota: VolumeQuota,
    kind: DocumentOwnerKind,
    owner_id: str | None,
    original_name: str,
    title: str | None = None,
) -> tuple[str, str, int]:
    """Store the bytes, archive them, and record what they were.

    Returns `document_store.save_bytes`'s `(stored_name, mime, size)` unchanged,
    so a call site is a one-word edit rather than a rewrite.

    Every argument after `quota` is keyword-only and none has a default except
    `title`, which is genuinely absent for four of the six stores. `owner_id`
    takes `None` explicitly rather than by omission: a registration document
    belongs to an applicant with no account yet, and that must be a stated fact
    rather than a forgotten argument.
    """
    stored_name, mime, size = document_store.save_bytes(content, quota=quota)
    db.add(
        ArchivedDocument(
            stored_name=stored_name,
            kind=kind,
            owner_id=owner_id,
            original_name=original_name,
            title=title,
            mime_type=mime,
            size_bytes=size,
        )
    )
    db.flush()
    return stored_name, mime, size


@dataclass(frozen=True)
class DocumentFacts:
    """What the LIVE row knows about a file, for a release that finds no
    manifest row.

    WHY THIS EXISTS. Migration `b7e4d21af905` writes no backfill, and its
    reasoning is sound: a row seeded for every existing file would carry
    `recorded_at = ` the deploy date, telling every future reader that the whole
    store arrived that day -- `mentor_assignments`' lesson. The consequence it
    accepted is that a file stored BEFORE the manifest existed loses its name
    the moment somebody deletes it, and on a running college that is every file
    there is on day one.

    That consequence is avoidable, and the avoidance needs no invention: at the
    moment of release the LIVE ROW IS STILL THERE, holding the real owner, the
    real filename and the real upload timestamp. Passing them in is a backfill
    of exactly the rows that need one, from exact data, at the only moment the
    data is both available and about to be destroyed.

    `recorded_at` is REQUIRED here rather than optional for that reason. Every
    one of the six stores carries its own timestamp (`uploads.uploaded_at`,
    `alumni_profiles.created_at`, ...), so a caller always has a true answer,
    and a default would quietly reintroduce the deploy-day lie this class exists
    to avoid.
    """

    kind: "DocumentOwnerKind"
    owner_id: str | None
    original_name: str
    mime_type: str
    size_bytes: int
    recorded_at: datetime
    title: str | None = None


def release(
    db: Session,
    stored_name: str | None,
    *,
    reason: str,
    facts: DocumentFacts | None = None,
) -> None:
    """Stamp `released_at` — the live row no longer points at this file.

    Idempotent and silent about a name it does not know. Two callers make that
    necessary rather than merely tidy: `python -m app.purge_people` releases
    files whose manifest rows predate this table, and a re-run of any delete
    path releases a name already released. Neither is an error, and raising on
    either would put a destructor into a state only a database edit can leave.

    NEVER DELETES THE ROW. The point of the manifest is that it outlives the
    thing it describes.

    `facts` closes the pre-migration gap. When no manifest row exists and the
    caller can describe the file from the live row it is about to destroy, the
    row is CREATED here and released in the same breath -- see `DocumentFacts`.
    Callers that cannot describe it (the purge modules, which walk bare
    `stored_name` columns across every table) pass nothing and get the old
    log-and-continue behaviour, because a row invented without an owner is
    worse than no row.
    """
    if not stored_name:
        return
    row = db.scalar(select(ArchivedDocument).where(ArchivedDocument.stored_name == stored_name))
    now = datetime.now(timezone.utc)
    if row is None:
        if facts is None:
            # A file stored before this table existed, released by a caller that
            # cannot describe it. Worth a line in the log -- it is how a path
            # that skipped `save_and_record` would ever be noticed -- and never
            # worth an exception on a delete the user asked for.
            log.info("Released %s, which has no manifest row (%s)", stored_name, reason)
            return
        db.add(
            ArchivedDocument(
                stored_name=stored_name,
                kind=facts.kind,
                owner_id=facts.owner_id,
                original_name=facts.original_name,
                title=facts.title,
                mime_type=facts.mime_type,
                size_bytes=facts.size_bytes,
                recorded_at=facts.recorded_at,
                released_at=now,
                # Marked, because this row was written at RELEASE and not at
                # store. Everything on it is exact -- it was copied off the live
                # row -- but a reader counting "files stored in March" must be
                # able to tell a row that watched the upload from one that
                # reconstructed it.
                released_reason=f"{reason} (recorded at release; predates the manifest)",
            )
        )
        db.flush()
        log.info("Backfilled and released %s (%s)", stored_name, reason)
        return
    if row.released_at is None:
        row.released_at = now
        row.released_reason = reason


def reattribute(
    db: Session,
    stored_name: str,
    *,
    kind: DocumentOwnerKind,
    owner_id: str | None,
    reason: str,
) -> None:
    """The file changed OWNER without changing bytes -- say so on the row.

    One caller today. Approving an application moves the CV and the photograph
    out of `registration_documents` and into the new student's `uploads`,
    keeping the same `stored_name` because the bytes are never copied. The
    manifest row was written when an APPLICANT posted that file, so it carries
    `kind=REGISTRATION_DOCUMENT` and `owner_id=None` -- the only identity there
    was at the time, and stated rather than forgotten. Left alone it carries
    them forever, and once `python -m app.purge_people` has removed the person
    this row is the ONLY thing that can still name the archived object: an index
    entry saying the file belonged to nobody, which is exactly the failure this
    table exists to prevent, reached from the one direction nobody deletes
    anything in.

    **WHY NOT `release()` AND A SECOND `save_and_record()`.** Three reasons and
    the first is fatal on its own. `stored_name` is UNIQUE -- deliberately, so
    that two rows can never claim one file -- so there is no second row to
    write; the insert would fail on a file that is still perfectly live.
    `released_at` means "the live row stopped pointing at this file", so
    stamping it here would tell every future reader that the student's resume
    was deleted on the day they were admitted. And `release` IGNORES
    `DocumentFacts` when a row already exists (read it: the facts are the
    pre-migration backfill and only the `row is None` branch looks at them), so
    a change of owner routed through it would quietly do nothing at all.

    So this is an in-place UPDATE, and nothing it touches is history.
    `recorded_at`, `released_at`, the file's own name, size and type are as true
    after the move as before; only the pair that answers *whose file is this*
    changes. The two change TOGETHER because the index is on the pair
    (`ix_archived_documents_owner` -- "everything this owner ever had", the one
    question a restore asks). A row reading `REGISTRATION_DOCUMENT` against a
    `students.id` is found by neither half of that question and contradicts
    `owner_id`'s own column comment, which records that a registration document
    has no account behind it precisely because the applicant had none yet.

    `reason` is for the log and not for the row. There is no column for it, and
    `released_reason` is not one: writing there would claim a release that did
    not happen.

    **SILENT ABOUT A NAME IT DOES NOT KNOW**, like `release` and for the same
    reason. Migration `b7e4d21af905` wrote no backfill, so every file stored
    before this table existed has no row at all; an approval that 500s because
    the applicant's CV predates the manifest would be the bookkeeping breaking
    the act it is bookkeeping for. There is no `DocumentFacts` escape hatch
    here either, and that is not an omission: the file is NOT about to be
    destroyed, so nothing is lost by waiting -- it stays live under its new row,
    and the day the student deletes that upload the release there carries full
    facts and backfills it from data that is exact THEN, with the owner this
    call could only have guessed at. A line in the log anyway, because a file
    that reached the store without `save_and_record` is a thing somebody should
    get to notice.

    FLUSHES, NEVER COMMITS -- this module's rule, for this module's reason. The
    re-attribution lands in the same transaction as the move itself, so
    "the upload exists, the manifest still says applicant" is not a state
    anybody can observe.
    """
    row = db.scalar(select(ArchivedDocument).where(ArchivedDocument.stored_name == stored_name))
    if row is None:
        log.info("No manifest row for %s; nothing to reattribute (%s)", stored_name, reason)
        return
    row.kind = kind
    row.owner_id = owner_id
    db.flush()
    log.info(
        "Reattributed %s to %s owner %s (%s)", stored_name, kind.value, owner_id, reason
    )


def record_existing(
    db: Session,
    stored_name: str,
    *,
    kind: DocumentOwnerKind,
    owner_id: str | None,
    original_name: str,
    mime_type: str,
    size_bytes: int,
    title: str | None = None,
) -> bool:
    """Name a file that is ALREADY in a store. Returns True if a row was added.

    THE FOURTH VERB, AND THE ONE THE OTHER THREE CANNOT SPELL. `save_and_record`
    WRITES BYTES -- it goes through `document_store.save_bytes`, which accepts
    PDF/PNG/JPEG by magic number, holds the whole file in memory under a 10 MB
    ceiling and mints its own `stored_name`. An interview recording is none of
    those things: a WAV of tens of megabytes, in a different store, written
    incrementally over eight minutes under a name the recorder chose. It cannot
    be handed to `save_and_record` and must not be copied a third time merely to
    satisfy a signature. `release` stamps `released_at`, which would tell every
    future reader the recording was gone on the day it was made. `reattribute`
    only edits a row that is already there.

    WHY IT BELONGS HERE RATHER THAN IN ITS CALLER. `tests/test_codebase_guards.py`
    section 36 pins ONE writer of `archived_documents`, by scanning every module
    for the model's constructor outside this one. That guard is not a formality
    to be worked around with an exemption: the manifest is the only thing that
    can ever say whose file an object in a bucket with no delete permission was,
    and a second writer is how one of them quietly stops setting `owner_id`.
    The guard was right and the caller was wrong, so the entry point moved here.

    IT IS IDEMPOTENT, AND THAT IS NOT DECORATION. `stored_name` is UNIQUE --
    deliberately, so two rows can never claim one file -- and an interview is
    finalized by three layers that are idempotent against each other by one
    `AND status = 'running'` predicate. Only one of them reaches this today, and
    "only one caller" is exactly the sentence that stops being true quietly, so
    a name already in the table is a no-op returning False rather than an
    IntegrityError landing in the log looking like a database fault.

    ADDS AND FLUSHES, NEVER COMMITS -- this module's rule, for this module's
    reason: the row must land in the caller's transaction, beside whatever live
    row points at the same file, so "file recorded, write rolled back" and
    "write committed, file unrecorded" cannot both be reachable.
    """
    if not stored_name:
        return False
    existing = db.scalar(
        select(ArchivedDocument.stored_name).where(
            ArchivedDocument.stored_name == stored_name
        )
    )
    if existing is not None:
        return False
    db.add(
        ArchivedDocument(
            stored_name=stored_name,
            kind=kind,
            owner_id=owner_id,
            original_name=original_name,
            title=title,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )
    )
    db.flush()
    return True
