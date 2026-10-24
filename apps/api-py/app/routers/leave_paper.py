"""The leave paper as a PDF: GET /api/leaves/{id}/paper.pdf.

Its own module so routers/leave.py - the form's submit and decide paths, which
the owner asked to leave exactly as they are - gains nothing. It IMPORTS that
router's scope rule and its output builder rather than restating either:

  * who may download is who may see: the applicant themselves, and otherwise
    exactly the staff `_assert_can_decide` admits (DIRECTOR/ADMIN for anything,
    a MENTOR only for a student in their own group), with every refusal the
    same 404 so the id space is not an oracle;
  * what is printed is `_leave_out`, so the PDF cannot disagree with the sheet
    the screens draw from the same function.

The signature images are read by user id at render time (models/staff_signature.py)
- the applicant's for the two staff blocks, the sanctioning approver's for the
PROGRAM DIRECTOR block - so the paper always carries whatever is on file NOW.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..document_store import content_disposition, read_bytes
from ..identity import get_current_session
from ..leave_paper import render_leave_paper_pdf
from ..models.leave import LeaveRequest
from ..models.staff_signature import StaffSignature
from .leave import _assert_can_decide, _leave_out

router = APIRouter(prefix="/leaves", tags=["leave-paper"])


def _signature_for(db: Session, user_id: str | None) -> tuple[bytes, str] | None:
    if not user_id:
        return None
    row = db.scalar(select(StaffSignature).where(StaffSignature.user_id == user_id))
    if row is None:
        return None
    try:
        return read_bytes(row.stored_name), row.mime_type
    except FileNotFoundError:
        return None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "staff"


@router.get("/{leave_id}/paper.pdf")
def leave_paper(
    leave_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    lr = db.get(LeaveRequest, leave_id)
    if lr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leave request not found.")
    if lr.requester_user_id != session.get("userId"):
        # Not mine: then only the staff who could decide it may read it, and a
        # refusal is the same 404 as a missing id (see routers/leave.py).
        _assert_can_decide(session, lr, db)

    out = _leave_out(lr, db)
    staff_signature = _signature_for(db, lr.requester_user_id)
    director_signature = None
    if out.director_name:
        director_signature = _signature_for(db, lr.second_approver_user_id or lr.first_approver_user_id)

    pdf = render_leave_paper_pdf(out, staff_signature=staff_signature, director_signature=director_signature)
    filename = f"leave-{_slug(out.requester_name)}-{out.from_date.isoformat()}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(filename), "Cache-Control": "private, no-store"},
    )
