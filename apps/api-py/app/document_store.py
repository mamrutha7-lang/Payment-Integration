"""Hardened on-disk file store for student uploads.

Bytes live on disk under a random `stored_name`; only metadata goes in the
`uploads` table (see models/upload.py). Two rules the Next.js store also enforced
and that matter for trusting a student-supplied file:

- The type is decided by MAGIC BYTES, not the client-sent name or Content-Type.
  A ".pdf" that is actually an executable is rejected; the recorded mime is what
  the bytes actually are.
- The name written to disk is random, so a crafted filename can never traverse
  the store or overwrite another file. Reads reject any separator in the name.

Only PDF / PNG / JPEG are accepted — the formats a mentor reviews (marksheets,
certificates, photos). Max 10 MB, matching the UI copy.

Per-file is not per-student: see MAX_UPLOADS_PER_STUDENT / MAX_UPLOAD_BYTES_PER_STUDENT
below for the quota that stops one account filling the disk 10 MB at a time.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from . import document_archive
from .config import settings

# First bytes -> (mime, extension). Order matters only in that each signature is
# unambiguous.
_MAGIC: list[tuple[bytes, str, str]] = [
    (b"%PDF", "application/pdf", ".pdf"),
    (bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]), "image/png", ".png"),
    (bytes([0xFF, 0xD8, 0xFF]), "image/jpeg", ".jpg"),
]

MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# Per-STUDENT quota. MAX_BYTES bounds one file; on its own it bounds nothing at
# all, because an authenticated student can post 10 MB as many times as they
# like and the store has no notion of who owns what — the disk fills, and every
# other student's upload starts failing on a machine that looks healthy.
#
# The numbers are deliberately far above real use (a marksheet per semester, a
# handful of certificates, a photo, a CV) and far below "one account can hurt
# the box".
#
# This comment used to end "ENFORCEMENT is in routers/student.py create_upload —
# the single caller of save_bytes. Any second writer must apply the same check,
# or the quota is decoration." Three writers later, one of them had no check at
# all. Enforcement now lives in VolumeQuota below and `save_bytes` refuses a
# caller that brings none; the counting still belongs to the caller, because it
# needs that owner's table.
#
# Module constants rather than settings: adding a numeric setting means adding it
# to BOTH validator name-lists in app/config.py, and a limit nobody has ever
# needed to tune per deployment does not earn that.
MAX_UPLOADS_PER_STUDENT = 40
MAX_UPLOAD_BYTES_PER_STUDENT = 200 * 1024 * 1024  # 200 MB

# Per-STAFF-user quota, same reasoning, different shelf.
MAX_CERTIFICATES_PER_USER = 20
MAX_CERTIFICATE_BYTES_PER_USER = 100 * 1024 * 1024  # 100 MB

# B10.3's leave attachments, and THE TWO NUMBERS ARE COUNTED AGAINST DIFFERENT
# OWNERS ON PURPOSE — which is the one thing to read before writing the caller.
#
#   * the FILE COUNT is per leave REQUEST. An application is a medical
#     certificate, maybe an OOD letter, maybe a second page of one; a request
#     carrying more than a handful is a mis-click or a scanner spraying a page
#     per file, and the person who hit the limit is helped by being told so on
#     the request they are looking at. Bounding it per user instead would let
#     one long-running request exhaust an account's whole allowance and then
#     refuse a genuine certificate on the next one.
#   * the BYTE ALLOWANCE is per USER, because the disk does not care which
#     request the megabytes arrived on. This is the cap that actually stops one
#     account filling the volume 10 MB at a time, and it is the same bound
#     MAX_UPLOAD_BYTES_PER_STUDENT provides above, at a size that fits a few
#     dozen scanned certificates and nothing like a video.
#
# So the caller builds ONE VolumeQuota whose `used_files` is counted over this
# request's `leave_attachments` rows and whose `used_bytes` is summed over that
# USER's rows across every request. Both counts are the caller's job, as the
# comment above says, because both need the table this module must not import.
#
# Module constants rather than settings, for MAX_UPLOADS_PER_STUDENT's reason:
# a limit nobody has ever needed to tune per deployment does not earn an entry
# in both of app/config.py's validator name-lists.
MAX_LEAVE_ATTACHMENTS_PER_REQUEST = 5
MAX_LEAVE_ATTACHMENT_BYTES_PER_USER = 50 * 1024 * 1024  # 50 MB


class UploadRejected(ValueError):
    """The bytes are not an accepted file (bad type, empty, or too large)."""


class QuotaRejected(ValueError):
    """The file is fine; the OWNER has no room for it.

    Deliberately NOT a subclass of UploadRejected. Routers answer that one with
    422 ("we will not accept these bytes"), and these need different codes: the
    bytes are perfectly acceptable, the shelf is full. Subclassing would have
    every existing `except UploadRejected` silently answer 422 and tell a
    student their valid PDF was malformed.
    """

    status_code = 409


class ShelfFull(QuotaRejected):
    """Too many files already. 409 -- the caller can delete one and retry."""

    status_code = 409


class AllowanceExceeded(QuotaRejected):
    """This file would cross the total-bytes allowance. 413."""

    status_code = 413


@dataclass(frozen=True)
class VolumeQuota:
    """How much room one owner has left, and the arithmetic that decides.

    THIS EXISTS BECAUSE THE OLD COMMENT WAS WRONG. It said enforcement lives in
    "routers/student.py create_upload -- the single caller of save_bytes", and
    that any second writer must repeat the check. There are now three writers.
    routers/staff_upskilling.py re-implemented the check; routers/alumni.py did
    not. An invariant that lives in the caller is a convention, and a convention
    is one new endpoint away from being false.

    So the LIMITS and the ARITHMETIC live here, and `save_bytes` will not store
    a byte without one. The COUNTING still belongs to the caller, because it
    needs that owner's table (`uploads`, `staff_upskilling_certificates`), and
    this module stays free of the ORM -- which is what lets it be tested with
    four integers and no database.

    Two phases, and the order is the point. `check_slot()` runs BEFORE the
    request body is read, so an owner who is already over their file count never
    gets their megabytes buffered into this process at all; `check_bytes()` runs
    after, when the size is finally known. A single combined check would have to
    read first, which is exactly the denial-of-service the count cap exists to
    stop.
    """

    max_files: int
    max_bytes: int
    used_files: int
    used_bytes: int
    #: What the owner calls these on their own screen -- "file", "certificate".
    #: The message is read by the person who hit the limit, not by an operator.
    noun: str = "file"

    @classmethod
    def single_slot(cls, noun: str = "file") -> "VolumeQuota":
        """For an owner that holds exactly one of these, replaced in place.

        routers/alumni.py is the case: one profile, one resume, and the old
        bytes are deleted before the row points at the new ones. Its volume is
        bounded at one file by construction rather than by counting -- but it
        still has to say so, because `save_bytes` no longer accepts callers that
        say nothing.
        """
        return cls(max_files=1, max_bytes=MAX_BYTES, used_files=0, used_bytes=0, noun=noun)

    def check_slot(self) -> None:
        """Refuse before the body is read. Call first."""
        if self.used_files >= self.max_files:
            raise ShelfFull(
                f"You already have {self.used_files} {self.noun}s uploaded, which is "
                f"the limit of {self.max_files}. Delete one you no longer need, then "
                "try again."
            )

    def check_bytes(self, incoming: int) -> None:
        """Refuse once the size is known. Call after reading the body."""
        if self.used_bytes + incoming > self.max_bytes:
            raise AllowanceExceeded(
                f"This file would take you past your "
                f"{self.max_bytes // (1024 * 1024)} MB upload allowance "
                f"({self.used_bytes / (1024 * 1024):.1f} MB used). Delete "
                f"a {self.noun} you no longer need, then try again."
            )


def _sniff(content: bytes) -> tuple[str, str]:
    for magic, mime, ext in _MAGIC:
        if content.startswith(magic):
            return mime, ext
    raise UploadRejected("Unsupported file type — only PDF, PNG and JPEG are accepted.")


def sniff(content: bytes) -> tuple[str, str]:
    """What these bytes actually are, WITHOUT storing them. `(mime, extension)`.

    For the two callers that accept a narrower set than this store does -- a
    registration document that must be a photograph, a staff signature that
    must be an image -- and which therefore have to know the type before they
    can decide.

    THEY USED TO STORE FIRST AND DELETE ON THE WAY OUT, and that stopped being
    survivable when `save_bytes` gained the archive. Storing a file now copies
    it into a versioned, Object-Locked bucket with no lifecycle rule, and the
    `delete_stored` that followed could not reach it -- so every wrong-type
    upload left an object in the permanent archive whose manifest row was then
    rolled back with the failed request. An unnamed object in a bucket nothing
    is allowed to delete from, which is exactly what
    `tests/test_codebase_guards.py` §36 exists to prevent.

    Asking first costs one pass over the first eight bytes and means nothing
    is written at all.
    """
    return _sniff(content)


def _store_dir() -> Path:
    d = settings.uploads_path
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_bytes(content: bytes, *, quota: VolumeQuota) -> tuple[str, str, int]:
    """Validate and store; return (stored_name, sniffed_mime, size_bytes).

    `quota` is REQUIRED and keyword-only, which is the whole point of it. A
    default would let the next writer store bytes for an owner nobody is
    counting -- the exact way routers/alumni.py came to have no quota while the
    module comment still claimed a single caller. A caller with genuinely one
    slot passes `VolumeQuota.single_slot()` and says so out loud.

    `check_bytes` is repeated here even though every caller should already have
    run it: by this line the size is known for certain, and a backstop inside
    the store is worth more than trust in three call sites.
    """
    if not content:
        raise UploadRejected("The file is empty.")
    if len(content) > MAX_BYTES:
        raise UploadRejected("File too large — the limit is 10 MB.")
    quota.check_bytes(len(content))
    mime, ext = _sniff(content)
    stored_name = uuid.uuid4().hex + ext
    (_store_dir() / stored_name).write_bytes(content)
    # THE PERMANENT COPY, AND THIS IS THE ONE CHOKE POINT THAT REACHES ALL SIX
    # STORES. Student uploads, alumni resumes, staff upskilling certificates,
    # staff signatures, leave attachments and registration documents every one
    # arrive through this function, so archiving here is the difference between
    # a rule and a convention — the same argument VolumeQuota above makes about
    # the quota, which was a convention in the callers until a writer forgot it.
    #
    # AFTER the volume write and never before: the live store is what the
    # website reads, and a file that reached S3 but not the disk is a 404 on
    # the student's own screen. BEST-EFFORT by contract (archive_bytes raises
    # nothing) because S3 must not be on the critical path of an upload, and
    # `python -m app.archive_documents` is what makes the promise hold anyway.
    document_archive.archive_bytes(stored_name, content, content_type=mime)
    return stored_name, mime, len(content)


def read_bytes(stored_name: str) -> bytes:
    """Read a stored file back. Rejects any path separator in the name."""
    if not stored_name or "/" in stored_name or "\\" in stored_name or ".." in stored_name:
        raise FileNotFoundError(stored_name)
    path = _store_dir() / stored_name
    if not path.is_file():
        raise FileNotFoundError(stored_name)
    return path.read_bytes()


def delete(stored_name: str) -> None:
    """Remove a stored file. Rejects any path separator in the name (same guard as
    read_bytes); silently ignores a file that is already gone."""
    if not stored_name or "/" in stored_name or "\\" in stored_name or ".." in stored_name:
        raise FileNotFoundError(stored_name)
    (_store_dir() / stored_name).unlink(missing_ok=True)


def content_disposition(original_name: str, *, inline: bool = False) -> str:
    """Build a Content-Disposition header that survives a non-ASCII filename.

    The default is ``attachment``, and that is a security posture, not a UX
    choice: everything this store serves is a user-supplied file, and the SPA
    and API are same-origin by design. A PDF rendered INLINE runs its embedded
    JavaScript in that shared origin — uploaded by one student, opened by the
    mentor reviewing it — and the magic-byte sniff cannot help, because the
    payload IS a valid PDF. `attachment` hands the bytes to the browser's
    download UI instead of its renderer. Pass ``inline=True`` only for content
    this server authored itself (the interview WAVs, whose container
    interview_audio.py writes).

    Starlette encodes header values as **latin-1**. Interpolating a student's own
    filename straight into this header therefore raises UnicodeEncodeError inside
    Response.__init__ -- before any handler code can catch it -- the moment the name
    contains a character outside that range. At a Bengaluru college that is not a
    hypothetical: a file named "ಪ್ರಮಾಣಪತ್ರ.pdf" uploads perfectly and then 500s on every
    download attempt, which reads to the student as "my certificate is corrupted".

    RFC 6266 exists for exactly this. Emit BOTH parameters:

    * ``filename=``  an ASCII-only fallback, for clients that predate RFC 5987.
    * ``filename*=`` the real name, UTF-8 percent-encoded, which every current
      browser prefers when present.

    Quotes and control characters are stripped from the ASCII fallback rather than
    escaped. A CRLF in the fallback would be a response-splitting attempt; today
    uvicorn's h11 layer rejects such a header itself, but that is a defence borrowed
    from a dependency, and it would evaporate under a different server.
    """
    disposition = "inline" if inline else "attachment"

    # ASCII fallback: transliterate what we can, drop what we cannot, and remove the
    # characters that would break out of the quoted-string.
    ascii_name = original_name.encode("ascii", "ignore").decode("ascii")
    _BAD = (chr(34), chr(92))  # a quote or backslash would break out of the quoted-string
    ascii_name = "".join(c for c in ascii_name if c.isprintable() and c not in _BAD)
    ascii_name = ascii_name.strip() or "download"

    # RFC 5987 encoding of the true name. quote() with an empty safe list percent-
    # encodes everything outside the unreserved set, which is what the grammar wants.
    encoded = quote(original_name, safe="")

    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"
