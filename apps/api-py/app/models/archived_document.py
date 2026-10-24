"""`archived_documents` — the manifest for the permanent document archive.

WHAT IT IS FOR. `app/document_archive.py` copies every stored file into a
versioned, Object-Locked bucket the moment it is accepted, and nothing ever
deletes it from there. That makes the BYTES permanent. It does not make them
USEFUL: the archive key is the `stored_name`, a bare `uuid4().hex`, and the
only thing that ever knew whose file that was, what it was called and why it
existed was the live row pointing at it. Delete the row and the archive holds a
file nobody can name — which is `retention._delete_interview_audio`'s "a delete
that loses the pointer first leaves bytes nobody can find", arriving from the
other direction.

So this table is the index, and it is APPEND-ONLY. One row per file, written in
the same transaction as the live row, and nothing in the product ever deletes
one. `released_at` is stamped when the live row stops pointing at the file; the
row itself stays.

**WHY NOT A `deleted_at` COLUMN ON THE SIX TABLES INSTEAD.** That was the first
design and it cannot express what actually happens. Two of the six stores
REPLACE rather than accumulate: `staff_signatures.user_id` is UNIQUE and its
PUT overwrites `stored_name` in place, and `alumni_profiles` holds exactly one
resume and does the same. In both, the superseded file is not deleted-and-kept,
it is a pointer that no longer exists — there is no row to soft-delete and
nowhere to put the old name. A soft delete also cannot survive the case that
matters most, `python -m app.purge_people`, which empties the deployment of
accounts: a flag on a row whose table is being emptied protects nothing.

**NO FOREIGN KEYS, AND THAT IS THE WHOLE POINT.** `owner_id` is a plain String
holding a `users.id` or `students.id`. An FK would carry `ON DELETE CASCADE`
like the columns it mirrors, so the manifest would be destroyed at exactly the
moment it becomes the only remaining record — when the account goes. The same
argument `app/export_identity.py` makes about labels rather than foreign keys,
for the same reason: this row must outlive the schema it describes.

**IT CARRIES NO MARKS, NO ATTENDANCE AND NO FILE CONTENTS.** A name, a size, a
type, an owner id and two timestamps. It is an index into an archive, not a
second copy of the student record; that is the `pg_dump` tiers' job, keyed by
the same ids.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class DocumentOwnerKind(str, enum.Enum):
    """Which store the file came from.

    A plain enum and not the table name, because the table is an implementation
    detail that has already changed once — a registration document BECOMES an
    upload on approval, moving between two tables while keeping one
    `stored_name`. The kind records where it entered, which is the question an
    operator holding a bare key actually asks.
    """

    STUDENT_UPLOAD = "STUDENT_UPLOAD"
    STAFF_CERTIFICATE = "STAFF_CERTIFICATE"
    STAFF_SIGNATURE = "STAFF_SIGNATURE"
    LEAVE_ATTACHMENT = "LEAVE_ATTACHMENT"
    ALUMNI_RESUME = "ALUMNI_RESUME"
    REGISTRATION_DOCUMENT = "REGISTRATION_DOCUMENT"
    INTERVIEW_AUDIO = "INTERVIEW_AUDIO"


class ArchivedDocument(Base):
    __tablename__ = "archived_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)

    #: The archive key and the disk name, both. UNIQUE, because
    #: `document_store` mints a fresh `uuid4().hex` per file and every table
    #: that points into the store carries its own unique constraint on it — so
    #: a duplicate here means two rows claim one file, which is the one state
    #: that would make a restore ambiguous.
    stored_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    kind: Mapped[DocumentOwnerKind] = mapped_column(
        Enum(DocumentOwnerKind, name="document_owner_kind"), nullable=False
    )
    #: `users.id` or `students.id` — NEVER an FK. See the module docstring.
    #: Nullable because a registration document is uploaded by an applicant who
    #: has no account yet, which is precisely the case where the address on the
    #: `registrations` row is the only identity there is.
    owner_id: Mapped[str | None] = mapped_column(String, nullable=True)

    #: What the person called it, and what the file actually is. Kept because a
    #: bucket listing gives neither: without `original_name` an operator
    #: restoring a student's record has a hundred uuids and no way to tell the
    #: marksheet from the passport photograph.
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    #: The store's own heading for the file where it has one (`uploads.title`);
    #: NULL where the store has none, rather than a guessed value.
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: When the LIVE row stopped pointing at this file — a student's delete, a
    #: signature replaced, a purge. NULL means it is still referenced.
    #:
    #: Stamping it is BEST-EFFORT by design and its absence is never read as
    #: "still live": the bytes are in the archive either way, and a manifest
    #: that refused to record a file because it could not also record the
    #: release would trade the thing that matters for the thing that does not.
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Free text from the release site ("student deleted", "signature
    #: replaced", "purge_students"). Not an enum: the vocabulary is whatever
    #: the next release site calls itself, and an enum here would be a
    #: migration every time one is added.
    released_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        # "Everything this owner ever had", which is the question a restore
        # asks and the only one this table is queried by.
        Index("ix_archived_documents_owner", "kind", "owner_id"),
    )
