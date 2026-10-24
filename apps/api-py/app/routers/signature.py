"""A staff member's own signature image: /api/staff/signature.

    GET    /            is one on file, and since when
    PUT    /            upload (PNG or JPEG, under 2 MB) - replaces in place
    GET    /image       the image itself, inline, for the preview
    DELETE /            remove it

Staff only (`require_mentor`: MENTOR / ADMIN), and always the
caller's OWN: there is no path that reads another account's image here.
Where another person's signature is shown is the leave paper, which reads
the row by user id at render time (routers/leave_paper.py) - so a replaced
image shows on every paper from then on, and a removed one shows on none.

ONE SLOT, REPLACED IN PLACE, the alumni-resume shape: `VolumeQuota.single_slot`
says so to the store, and the old bytes are deleted before the row points at
the new ones. The store sniffs the bytes; a PDF is a legal upload elsewhere
and is refused HERE, after the sniff, because a signature is an image.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..document_manifest import DocumentFacts, release, save_and_record
from ..models.archived_document import DocumentOwnerKind
from ..document_store import (
    QuotaRejected,
    UploadRejected,
    VolumeQuota,
    content_disposition,
    delete as delete_stored,
    read_bytes,
    sniff,
)
from ..identity import get_current_session
from ..models.staff_signature import StaffSignature
from .mentor import require_mentor

log = logging.getLogger(__name__)

router = APIRouter(prefix="/staff/signature", tags=["staff-signature"])

#: A signature is a small image. Two megabytes is generous for a scan and far
#: below the store's 10 MB per-file cap, so this is the bound that matters.
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024
IMAGE_TYPES = ("image/png", "image/jpeg")


class SignatureOut(BaseModel):
    present: bool
    mime_type: str | None
    size_bytes: int | None
    uploaded_at: datetime | None


def _normalised_png(content: bytes) -> bytes:
    """`content` as a flat, upright RGBA PNG — or, when Pillow cannot decode
    what the sniffer accepted, the bytes exactly as uploaded, with a warning.

    BEST-EFFORT, NOT A GATE. The sniffer has already said this is a PNG or a
    JPEG; refusing here on Pillow's word would turn a codec gap into "your
    signature was rejected". Stored as uploaded, the paper tries ReportLab
    first and Pillow second (`leave_paper._image`) and logs when neither can
    draw it, which is the failure that used to be silent.
    """
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image) or image
            flat = image.convert("RGBA")
        out = io.BytesIO()
        flat.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warning("signature stored as uploaded; Pillow could not normalise it: %s", exc)
        return content


def _out(row: StaffSignature | None) -> SignatureOut:
    if row is None:
        return SignatureOut(present=False, mime_type=None, size_bytes=None, uploaded_at=None)
    return SignatureOut(present=True, mime_type=row.mime_type, size_bytes=row.size_bytes, uploaded_at=row.uploaded_at)


def _mine(db: Session, session: dict) -> StaffSignature | None:
    return db.scalar(select(StaffSignature).where(StaffSignature.user_id == session["userId"]))


@router.get("", response_model=SignatureOut)
def my_signature(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> SignatureOut:
    require_mentor(session)
    return _out(_mine(db, session))


@router.put("", response_model=SignatureOut)
def upload_signature(
    file: UploadFile = File(...),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SignatureOut:
    """Sync `def` on purpose, like the other upload endpoints: the write belongs
    in the threadpool, not on the event loop the live interviews share."""
    require_mentor(session)
    # read(MAX+1), never read(): one byte over trips the check without
    # buffering an unbounded body (routers/student.py create_upload).
    content = file.file.read(MAX_SIGNATURE_BYTES + 1)
    if len(content) > MAX_SIGNATURE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Keep the signature image under 2 MB.",
        )
    # THE TYPE IS DECIDED BEFORE ANYTHING IS STORED -- see the same change in
    # routers/registration.py and `document_store.sniff`. Storing first and
    # deleting on the way out leaves an unnamed object in the permanent
    # archive, which no delete on this side can reach.
    try:
        sniffed, _ext = sniff(content)
    except UploadRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if sniffed not in IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A signature is an image: upload a PNG or a JPEG.",
        )
    # NORMALISED BEFORE IT IS STORED (2026-09-16): whatever the phone or the
    # scanner produced — a palette PNG with a transparency chunk, a CMYK or
    # EXIF-rotated JPEG, a 16-bit PNG — becomes a flat, upright RGBA PNG, the
    # one shape `app/leave_paper.py` draws without a fallback. The paper used
    # to swallow an unreadable image silently, and "my signature is not on the
    # PDF" was the only symptom. A file Pillow cannot read is refused here,
    # in words, rather than stored and silently skipped on every paper.
    content = _normalised_png(content)
    try:
        stored_name, mime, size = save_and_record(
            db,
            content,
            quota=VolumeQuota.single_slot("signature"),
            kind=DocumentOwnerKind.STAFF_SIGNATURE,
            owner_id=session["userId"],
            original_name=file.filename or "signature",
        )
    except QuotaRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

    row = _mine(db, session)
    if row is None:
        row = StaffSignature(user_id=session["userId"], stored_name=stored_name, mime_type=mime, size_bytes=size)
        db.add(row)
    else:
        old = row.stored_name
        # `staff_signatures.user_id` is UNIQUE and this PUT replaces in place,
        # so there is no second row to soft-delete and nowhere on this one to
        # keep the old name. The manifest is what remembers it -- see
        # models/archived_document.py, where this constraint is the worked
        # example of why that table exists.
        release(
            db,
            old,
            reason="signature replaced",
            facts=DocumentFacts(
                kind=DocumentOwnerKind.STAFF_SIGNATURE,
                owner_id=row.user_id,
                original_name=f"signature{'.png' if row.mime_type == 'image/png' else '.jpg'}",
                mime_type=row.mime_type,
                size_bytes=row.size_bytes,
                recorded_at=row.uploaded_at,
            ),
        )
        row.stored_name = stored_name
        row.mime_type = mime
        row.size_bytes = size
        row.uploaded_at = datetime.now(timezone.utc)
        db.flush()
        try:
            delete_stored(old)
        except FileNotFoundError:
            pass
    db.commit()
    db.refresh(row)
    return _out(row)


@router.get("/image")
def my_signature_image(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> Response:
    require_mentor(session)
    row = _mine(db, session)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No signature on file.")
    try:
        content = read_bytes(row.stored_name)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing.")
    ext = "png" if row.mime_type == "image/png" else "jpg"
    return Response(
        content=content,
        media_type=row.mime_type,
        headers={
            "Content-Disposition": content_disposition(f"signature.{ext}", inline=True),
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def remove_signature(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> Response:
    require_mentor(session)
    row = _mine(db, session)
    if row is not None:
        # Released before the unlink -- routers/student.py's delete carries the
        # full reasoning: a SELECT between an irreversible delete and the commit
        # turns any query failure into destroyed bytes plus a surviving row.
        release(
            db,
            row.stored_name,
            reason="signature removed",
            facts=DocumentFacts(
                kind=DocumentOwnerKind.STAFF_SIGNATURE,
                owner_id=row.user_id,
                original_name=f"signature{'.png' if row.mime_type == 'image/png' else '.jpg'}",
                mime_type=row.mime_type,
                size_bytes=row.size_bytes,
                recorded_at=row.uploaded_at,
            ),
        )
        try:
            delete_stored(row.stored_name)
        except FileNotFoundError:
            pass
        db.delete(row)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
