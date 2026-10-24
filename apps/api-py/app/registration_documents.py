"""Ageing a rejected application's CV and photograph off the volume.

WHY THIS IS ITS OWN MODULE AND NOT A FUNCTION IN `app/routers/registration.py`,
where it was first written. Its one caller is `app/retention.py` — the product's
single SCHEDULED destructor, the one that "deletes every night with nobody
watching" — and `tests/test_codebase_guards.py` §35 pins that module's entire
import surface, function-local imports included, so that the sweep can never
reach a row that decides whether somebody can sign in.

`app/routers/registration.py` imports `Role`, `Student` and `User`: it is the
module that PROVISIONS ACCOUNTS on approval. Importing it from the sweep to
reach one helper would pull every one of those into the nightly job's process —
past the letter of §35's AST check, which only reads retention.py's own import
statements, and straight through the middle of what that guard exists to
prevent. The helper therefore lives where it can be imported honestly: this file
names `Registration` and `RegistrationDocument` and nothing that is an account.

An application is not a person in this schema — `Registration` has no foreign
key to `Student` and the applicant has no `users` row until somebody approves
them — so sweeping one is squarely inside retention's remit. What was outside it
was the ROUTE to the code, not the code.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .document_manifest import DocumentFacts, release
from .document_store import delete as delete_stored
from .models.archived_document import DocumentOwnerKind
from .models.registration import Registration, RegistrationDocument, RegistrationStatus

log = logging.getLogger("reep.registration_documents")


def purge_rejected_documents(
    db: Session, *, older_than_days: int, now: datetime | None = None
) -> int:
    """Sweep the CV and photograph off applications rejected longer ago than
    `older_than_days`. Returns the number of documents destroyed.

    WHY THIS IS NOT DONE ON REJECT. `reopen`, further down this module, is the
    console's Undo, and it is exact precisely because rejection only STAMPS the
    row - read it: "the attached
    documents untouched (they were never deleted on rejection, precisely so this
    is lossless)". Deleting on the decision would make the Undo a button that
    silently returns half an application - the reviewer sees it back in the
    queue with its document chips gone and no way to ask the applicant for them
    except by email. So the files outlive the refusal on purpose, and the only
    honest way to stop keeping them forever is a sweep on a clock, run well
    after anybody would still be undoing anything.

    THE CLOCK IS `reviewed_at`, WHICH IS THE DECISION STAMP. There is no
    `decided_at` column on `registrations`; `reviewed_at` is what `decide`
    writes and what `reopen` CLEARS, and both of those are the behaviour this
    wants rather than an accident to work around. A reopened application has no
    stamp, is PENDING_REVIEW again and is never seen here; a re-rejection starts
    the window over from the second decision, which is the one that stands. A
    REJECTED row carrying no stamp at all is left alone rather than aged from
    `created_at` - there is no decision time to measure, and a sweep that
    invented one would destroy files behind a rejection nothing recorded.

    FILES BEFORE ROWS, release before unlink - the ordering both purge modules
    are built on, for the reason `retention._delete_interview_audio` states: the
    row is the last thing that knows whose file `3f2a..9c.pdf` was, so a pass
    that dropped it first would leave the archive holding bytes nobody can name.
    The manifest row is released with FULL `DocumentFacts` because the live row
    is still here as we read it, which is the only moment the applicant's own
    filename, size, type and upload time are both true and about to be
    destroyed.

    THE ARCHIVE COPY IS NOT TOUCHED AND CANNOT BE. `document_archive` put these
    bytes in a versioned, Object-Locked bucket the moment they were accepted and
    nothing in the product ever deletes from it. What this frees is the EFS
    volume and what it writes is `released_at`, so a restore can tell "we swept
    this months after a rejection" from "this is still attached to a live
    application". That is the same split the website's delete and the archive's
    permanence already make everywhere else: two promises about two copies.

    NO COMMIT - the endpoints in this module commit and its helpers flush, and
    that split is what lets the releases, the row deletes and whatever else the
    caller is doing land or roll back together.

ITS CALLER IS `retention.purge_expired`, which runs it nightly under
    `REJECTED_REGISTRATION_DOCUMENT_DAYS` and reports the count as
    `registrations_purged` — the key that sat at 0 from the day the old
    confirm-before-review sweep was removed until this replaced it.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=older_than_days)
    rows = db.scalars(
        select(RegistrationDocument)
        .join(Registration, Registration.id == RegistrationDocument.registration_id)
        .where(
            Registration.status == RegistrationStatus.REJECTED,
            # Redundant against the comparison below, which already drops NULLs
            # in SQL, and kept because the reader has to be able to see that
            # "rejected but never stamped" was decided rather than overlooked.
            Registration.reviewed_at.is_not(None),
            Registration.reviewed_at < cutoff,
        )
    ).all()
    for doc in rows:
        release(
            db,
            doc.stored_name,
            reason="rejected application swept",
            facts=DocumentFacts(
                kind=DocumentOwnerKind.REGISTRATION_DOCUMENT,
                # STILL NOBODY, and that is the whole point of the sweep. A
                # rejected applicant never became a user, so the address on the
                # `registrations` row was always the only identity behind these
                # bytes - and that row survives this pass, which is what keeps
                # the archived object explicable at all.
                owner_id=None,
                original_name=doc.original_name,
                mime_type=doc.mime_type,
                size_bytes=doc.size_bytes,
                recorded_at=doc.created_at,
            ),
        )
        try:
            delete_stored(doc.stored_name)
        except FileNotFoundError:
            # Already gone from the volume. The row still has to go, and the
            # release above has already recorded what the file was.
            pass
        db.delete(doc)
    db.flush()
    if rows:
        log.info(
            "Swept %d registration document(s) from applications rejected before %s",
            len(rows),
            cutoff.isoformat(),
        )
    return len(rows)
