"""The papers that came with a leave request (B10.3).

    POST   /api/leaves/{id}/attachments              attach one
    GET    /api/leaves/{id}/attachments              list them
    GET    /api/leaves/{id}/attachments/{aid}/file   download one
    DELETE /api/leaves/{id}/attachments/{aid}        remove one

ITS OWN MODULE, FOR `app/routers/leave_paper.py`'S REASON. `routers/leave.py`
holds the form's submit and decide paths, which the owner asked to leave exactly
as they are; it gains nothing here. What this module needs from it — the scope
rule — is IMPORTED, never restated.

==============================================================================
THE READ GATE IS THE PAPER'S, AND EVERY REFUSAL IS THE SAME 404
==============================================================================

What is attached to a leave request is medical or personal by construction: a
certificate for the `reason` that reads "post-operative review", the letter for
an OOD. So the question "may this account see this request" is asked exactly
where the PDF asks it — `leave._assert_can_decide` -> `mentor._assert_can_access_student`
-> `policies.assert_student_scope` — with the applicant's own id as the only
other door.

Every refusal is flattened to the same `Leave request not found.` a missing id
gets, for `_assert_can_decide`'s reason: told apart, these endpoints are a
membership oracle over the whole programme (guess ids, read the error, learn who
has leave pending and what they attached).

THAT INCLUDES THE ROLE GATE, AND THIS MODULE CANNOT BORROW `routers/leave.py`'S
EXEMPTION FOR IT. On the approver's own endpoints `require_mentor` runs before
any id is looked up, so its 403 is the same answer for a real id and an invented
one and leaks nothing — which is why that module is allowed to leave it
unflattened. Here it cannot run first. The applicant is the OTHER door, and a
STUDENT reading the certificate on their own request is the ordinary case, so
the row has to be loaded and its requester compared before the caller's role is
asked about at all. Left as a 403, that ordering made every path in this module
the exact oracle the flattening exists to close: an invented id answered 404 and
a named colleague's real one answered 403, which is "yes, that person has leave
on file" told to anybody holding a session. So `_readable_request` catches EVERY
refusal `_assert_can_decide` raises, the role gate included, and answers the one
404.

==============================================================================
THE STORE'S LIMITS ARE THE STORE'S, AND THEY ARE NOT RESTATED HERE
==============================================================================

`app/document_store.py` decides the accepted types by MAGIC BYTES (PDF / PNG /
JPEG) and caps one file at `MAX_BYTES` (10 MB). 04's "PDF/JPEG/PNG <= 10 MB" is
exactly that, already enforced, and a copy of it in this router is a second
place to be wrong. What belongs here is the COUNTING, which the store cannot do
because it needs this table and must stay free of the ORM.

THE QUOTA IS TWO-PHASE AND THE ORDER IS LOAD-BEARING. `check_slot()` runs BEFORE
the body is read, so an owner already at their file count never gets their
megabytes buffered into this process at all; `check_bytes()` runs after, inside
`save_bytes`, when the size is finally known. A single combined check would have
to read first, which is the denial of service the count cap exists to stop.

THE TWO NUMBERS ARE COUNTED AGAINST DIFFERENT OWNERS, and `document_store` says
why at length: `MAX_LEAVE_ATTACHMENTS_PER_REQUEST` over THIS REQUEST's rows (a
request carrying more than a handful is a mis-click or a scanner spraying a page
per file), `MAX_LEAVE_ATTACHMENT_BYTES_PER_USER` over THIS UPLOADER's rows
across every request (the disk does not care which request the megabytes arrived
on). One `VolumeQuota` carries both, so the three refusals stay the store's:
`ShelfFull` -> 409, `AllowanceExceeded` -> 413, `UploadRejected` -> 422.
`QuotaRejected` is deliberately not a subclass of `UploadRejected`, so catching
them in that order is not a style choice — reversed, a full shelf would tell the
uploader their valid PDF was malformed.

DOWNLOADS ARE `attachment`, NEVER INLINE. `content_disposition` defaults to it
and that default is a security posture: the SPA and the API are same-origin, and
a PDF rendered inline runs its embedded JavaScript in that origin — uploaded by
one applicant, opened by the approver reading it. The magic-byte sniff cannot
help, because the payload IS a valid PDF.

RULE 1 is not in play: nothing here reaches a model, and the bytes never leave
the volume.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..document_manifest import DocumentFacts, release, save_and_record
from ..models.archived_document import DocumentOwnerKind
from ..document_store import (
    MAX_BYTES,
    MAX_LEAVE_ATTACHMENT_BYTES_PER_USER,
    MAX_LEAVE_ATTACHMENTS_PER_REQUEST,
    QuotaRejected,
    UploadRejected,
    VolumeQuota,
    content_disposition,
    read_bytes,
)
from ..document_store import delete as document_store_delete
from ..identity import get_current_session
from ..models.leave import LeaveRequest
from ..models.leave_attachment import LeaveAttachment
from ..models.user import Role, User
# The ONE implementation of "may this account see this leave request". Imported
# from the module that owns it exactly as routers/leave_paper.py imports it; a
# second copy here would be the copy that stops tracking the first.
from .leave import _assert_can_decide

router = APIRouter(prefix="/leaves", tags=["leave-attachments"])

#: The one sentence every refusal on these paths answers with. A missing leave,
#: somebody else's leave, a missing attachment and an attachment belonging to
#: another request all read the same, on purpose.
NOT_FOUND = "Leave request not found."


class LeaveAttachmentOut(BaseModel):
    id: str
    leave_request_id: str
    original_name: str
    mime_type: str
    size_bytes: int
    uploaded_at: datetime
    #: Who attached it. NULL means the ACCOUNT is gone (`purge_people` removes
    #: every account but the Main Admin and this FK is SET NULL), never that
    #: nobody attached it — the screen must say "account removed", not "—".
    uploaded_by_user_id: str | None
    uploaded_by_name: str | None
    #: Is the caller allowed to remove this one? Answered by the server because
    #: the rule (the uploader, or the Main Admin) is the server's, and a client
    #: that re-derives it draws a button that 403s.
    can_delete: bool


def _out(row: LeaveAttachment, *, uploader: User | None, session: dict) -> LeaveAttachmentOut:
    return LeaveAttachmentOut(
        id=row.id,
        leave_request_id=row.leave_request_id,
        original_name=row.original_name,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        uploaded_at=row.uploaded_at,
        uploaded_by_user_id=row.uploaded_by_user_id,
        uploaded_by_name=uploader.name if uploader else None,
        can_delete=_may_delete(row, session),
    )


def _may_delete(row: LeaveAttachment, session: dict) -> bool:
    """The uploader removes their own paper; the Main Admin removes any.

    NOT "anyone who can read it": an approver who may read the applicant's
    medical certificate has no business deleting it, and the applicant has no
    business deleting the office's own letter attached to their request. The
    Main Admin is the second door because an uploader's account can be
    disabled or purged, and an attachment nobody can remove is a file nobody
    can remove.
    """
    if session.get("role") == Role.ADMIN.value:
        return True
    return bool(row.uploaded_by_user_id) and row.uploaded_by_user_id == session.get("userId")


def _readable_request(db: Session, session: dict, leave_id: str) -> LeaveRequest:
    """The request, if this caller may read it. See the module docstring."""
    lr = db.get(LeaveRequest, leave_id)
    if lr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if lr.requester_user_id != session.get("userId"):
        # Not mine: then only the staff who could decide it, with the same 404
        # for everyone else — INCLUDING the 403 `require_mentor` raises inside
        # `_assert_can_decide` for a STUDENT or an ALUMNI caller. That refusal
        # is allowed to stand on `routers/leave.py`'s own endpoints because it
        # runs there before any id is read; it cannot stand here, because the
        # applicant's door had to be tried first and the row is therefore
        # already known to exist by the time the role is asked about. A 403 only
        # a REAL id can reach, beside a 404 for an invented one, answers "does
        # this person have leave on file" for every id somebody cares to try.
        # Flattened exactly as `badge_verification.py` flattens
        # `_assert_can_access_student`.
        try:
            _assert_can_decide(session, lr, db)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND
            ) from None
    return lr


def _attachment_of(db: Session, lr: LeaveRequest, attachment_id: str) -> LeaveAttachment:
    row = db.get(LeaveAttachment, attachment_id)
    if row is None or row.leave_request_id != lr.id:
        # An attachment on ANOTHER request is not this request's attachment, and
        # saying so any more precisely confirms an id to somebody probing.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return row


def _uploaders(db: Session, rows: list[LeaveAttachment]) -> dict[str, User]:
    ids = {r.uploaded_by_user_id for r in rows if r.uploaded_by_user_id}
    if not ids:
        return {}
    found = db.scalars(select(User).where(User.id.in_(ids))).all()
    return {u.id: u for u in found}


@router.get("/{leave_id}/attachments", response_model=list[LeaveAttachmentOut])
def list_attachments(
    leave_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[LeaveAttachmentOut]:
    """The papers on this request, oldest first — which is the order they were
    handed over in and the order `ix_leave_attachment_request` serves."""
    lr = _readable_request(db, session, leave_id)
    rows = db.scalars(
        select(LeaveAttachment)
        .where(LeaveAttachment.leave_request_id == lr.id)
        .order_by(LeaveAttachment.uploaded_at)
    ).all()
    people = _uploaders(db, list(rows))
    return [
        _out(r, uploader=people.get(r.uploaded_by_user_id or ""), session=session) for r in rows
    ]


@router.post(
    "/{leave_id}/attachments",
    response_model=LeaveAttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_attachment(
    leave_id: str,
    file: UploadFile = File(...),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaveAttachmentOut:
    """Attach a document to a leave request.

    Sync `def` on purpose, like `routers/student.py::create_upload` and the
    staff upskilling shelf: a 10 MB write belongs in the threadpool, not on the
    event loop the live interviews share.

    THE APPLICANT AND THE APPROVERS BOTH WRITE HERE, which is why the gate is
    the read gate and not "the applicant only". The applicant attaches the
    certificate; an approver attaches the office's own letter to a request they
    are signing — `app/models/leave_attachment.py` says so — and both are
    recorded against the account that did it.
    """
    lr = _readable_request(db, session, leave_id)
    user_id = session["userId"]

    # PHASE ONE, BEFORE THE BODY IS READ. The file COUNT is this request's, the
    # byte allowance is this uploader's: two owners, one quota object. See the
    # module docstring and document_store's constants.
    used_files = (
        db.scalar(
            select(func.count(LeaveAttachment.id)).where(
                LeaveAttachment.leave_request_id == lr.id
            )
        )
        or 0
    )
    used_bytes = (
        db.scalar(
            select(func.coalesce(func.sum(LeaveAttachment.size_bytes), 0)).where(
                LeaveAttachment.uploaded_by_user_id == user_id
            )
        )
        or 0
    )
    quota = VolumeQuota(
        max_files=MAX_LEAVE_ATTACHMENTS_PER_REQUEST,
        max_bytes=MAX_LEAVE_ATTACHMENT_BYTES_PER_USER,
        used_files=used_files,
        used_bytes=used_bytes,
        noun="attachment",
    )
    try:
        quota.check_slot()
    except QuotaRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    # read(MAX+1), never read(): `save_bytes` refuses anything past the per-file
    # cap, so one extra byte trips it without buffering an unbounded body in RAM.
    content = file.file.read(MAX_BYTES + 1)
    try:
        stored_name, mime, size = save_and_record(
            db,
            content,
            quota=quota,
            kind=DocumentOwnerKind.LEAVE_ATTACHMENT,
            owner_id=user_id,
            original_name=file.filename or "attachment",
        )
    except QuotaRejected as exc:
        # 409 (shelf full) or 413 (allowance). CAUGHT FIRST: QuotaRejected is
        # deliberately not a subclass of UploadRejected, and reversing these two
        # clauses would tell somebody their valid PDF was malformed.
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

    row = LeaveAttachment(
        leave_request_id=lr.id,
        uploaded_by_user_id=user_id,
        original_name=file.filename or stored_name,
        stored_name=stored_name,
        mime_type=mime,
        size_bytes=size,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row, uploader=db.get(User, user_id), session=session)


@router.get("/{leave_id}/attachments/{attachment_id}/file")
def download_attachment(
    leave_id: str,
    attachment_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    lr = _readable_request(db, session, leave_id)
    row = _attachment_of(db, lr, attachment_id)
    try:
        content = read_bytes(row.stored_name)
    except FileNotFoundError:
        # The row outlived its bytes. Say THAT rather than "not found": the
        # caller may read this request, so there is nothing to protect here and
        # an operator needs to know a file is missing from the volume.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing."
        )
    return Response(
        content=content,
        media_type=row.mime_type,
        headers={
            # `attachment`, always. See the module docstring on inline PDFs.
            "Content-Disposition": content_disposition(row.original_name),
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/{leave_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    leave_id: str,
    attachment_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Remove one attachment. The uploader's, or the Main Admin's — see
    `_may_delete`.

    FILE FIRST, THEN THE ROW. `retention._delete_interview_audio`'s reasoning
    and `purge_people`'s: the row is the last pointer to the bytes, so a delete
    that loses the pointer first leaves a student's medical certificate on the
    volume with nothing left to find it by. A file that is already gone is not
    an error (`document_store.delete` ignores it), so the reverse failure is
    harmless.
    """
    lr = _readable_request(db, session, leave_id)
    row = _attachment_of(db, lr, attachment_id)
    if not _may_delete(row, session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only the person who attached this file can remove it. "
                "Ask the office if it needs to go."
            ),
        )
    # RELEASED BEFORE THE BYTES GO, NOT AFTER, AND THE ORDER IS THE WHOLE POINT.
    # `release` runs a SELECT against `archived_documents`. Between an
    # irreversible `unlink` and the commit, ANY failure of that query -- a lock
    # timeout, a connection blip, or the table not existing yet because
    # deploy.yml's `run_migrations` was left off -- rolls the transaction back
    # with the file already destroyed, leaving a row whose download 404s. Ahead
    # of the unlink, the same failure destroys nothing: the request 500s and
    # the person retries.
    #
    # Deliberately NOT a `try/except OperationalError` around the call, which
    # was the suggested fix: swallowing it would drop the manifest row silently
    # and that row is the only thing that can ever name the archived bytes.
    # The failure must stay loud. Moving it earlier makes it harmless as well.
    release(
        db,
        row.stored_name,
        reason="attachment removed from leave request",
        facts=DocumentFacts(
            kind=DocumentOwnerKind.LEAVE_ATTACHMENT,
            owner_id=row.uploaded_by_user_id,
            original_name=row.original_name,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            recorded_at=row.uploaded_at,
        ),
    )
    document_store_delete(row.stored_name)
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
