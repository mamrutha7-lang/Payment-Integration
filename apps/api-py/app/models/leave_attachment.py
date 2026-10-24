"""A document attached to a leave request (B10.3) — a medical certificate, an
OOD letter, the invitation a staff member is travelling for.

Keyed on the REQUEST, not on the person: an attachment has no life of its own,
it is one of the papers that came with this application, and when the request
goes it goes. That is why `leave_request_id` is NOT NULL and ON DELETE CASCADE
while `uploaded_by_user_id` is nullable and ON DELETE SET NULL — the file
outlives the account of whoever attached it, the way `mentor_assignments`
records an act whose actor may be gone, but it cannot outlive the application
it belongs to.

Bytes live in the same hardened document_store as student uploads and staff
signatures (magic-byte sniffing, PDF/PNG/JPEG only, 10 MB, random `stored_name`);
only metadata is here. The store's own limits are NOT restated at the router —
read `app/document_store.py` before writing the upload endpoint, and note that
`save_bytes` will not store a byte without a `VolumeQuota`, that the caller owns
the COUNTING because it needs this table, and that
`MAX_LEAVE_ATTACHMENTS_PER_REQUEST` / `MAX_LEAVE_ATTACHMENT_BYTES_PER_USER`
there are the two numbers this owner is bounded by.

THE READ GATE IS THE PAPER'S, NOT A NEW ONE. What is attached to a leave
request is medical or personal by construction, and a `reason` reading "post-
operative review" is exactly the sentence rule 2 exists to keep inside the
approving group. `app/routers/leave_paper.py` already answers "may this account
see this request" through `_assert_can_decide` -> `mentor._assert_can_access_student`
-> `policies.assert_student_scope`, flattening every refusal to the same 404.
The attachment endpoints must call that same function and flatten to the same
404 — never a fourth copy of the rule, and never a 403 that tells a stranger the
request exists.

NO REVIEW WORKFLOW. There is no status column and there must not be one: an
attachment is evidence the approver reads before signing, not a claim awaiting a
separate verdict. `staff_upskilling_certs` made the same decision for the same
reason, and the verdict that matters here is the leave decision itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class LeaveAttachment(Base):
    __tablename__ = "leave_attachments"
    __table_args__ = (
        # (leave_request_id, uploaded_at) serves the FK's index requirement on
        # its LEADING column and the only read anybody makes of this table —
        # "the papers on this request, oldest first" — at the same time.
        Index("ix_leave_attachment_request", "leave_request_id", "uploaded_at"),
        # The uploader's own FK. Its read is "everything this account attached",
        # which the per-user byte quota needs.
        Index("ix_leave_attachment_uploader", "uploaded_by_user_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    #: NOT NULL and CASCADE: see the module docstring. An attachment with no
    #: request is not a record of anything, and the request is what carries the
    #: access rule that decides who may read the file.
    leave_request_id: Mapped[str] = mapped_column(
        ForeignKey("leave_requests.id", ondelete="CASCADE")
    )
    #: Who attached it — normally the applicant, but an approver may add the
    #: office's own paper to a request they are signing. SET NULL rather than
    #: CASCADE: `purge_people` removes every account but the Main Admin, and the
    #: fact that a document was attached to an application outlives the account
    #: of whoever attached it. Nullable is therefore a real state and reads as
    #: "the account that attached this is gone", never as "nobody did".
    uploaded_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    #: The document_store block, spelled exactly as `uploads`,
    #: `staff_upskilling_certs` and `staff_signatures` spell it. One shape for
    #: every file-backed table is what lets `purge_people.FILE_COLUMNS` be a
    #: dict of table -> column rather than five special cases.
    original_name: Mapped[str] = mapped_column(String)
    #: Filename on disk. Random, so an uploaded name can never traverse the
    #: store; unique, so two rows can never point at one file and a delete of
    #: either orphan the other.
    stored_name: Mapped[str] = mapped_column(String, unique=True)
    #: What the BYTES are, as sniffed by the store — never the client's
    #: Content-Type.
    mime_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
