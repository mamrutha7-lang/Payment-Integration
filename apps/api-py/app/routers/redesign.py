"""Canonical v1 identity, mentor notebook, student visibility, and actions API."""

import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..architecture_events import record_change, replay_or_reserve, store_response, utc_now
from ..db import get_db
from ..governance import require_capability
from ..identity import get_current_session
from ..models.redesign import (
    ActionPriority,
    ActionStatus,
    MentorNotebookAction,
    MentorNotebookAttachment,
    MentorNotebookEntry,
    MentorNotebookEntryRevision,
    NotebookEntryType,
    NotebookVisibility,
    RecordStatus,
)
from ..models.redesign import MembershipRole, TenantMembership
from ..models.user import Student, User
from ..policies import (
    assert_student_scope,
    require_staff,
    student_identity,
    tenant_id_for_session,
)

router = APIRouter(tags=["v1-redesign"])


class MenteeOut(BaseModel):
    student_id: str
    name: str
    usn: str | None = None
    current_stage: str
    current_semester: int


class NotebookEntryIn(BaseModel):
    entry_type: NotebookEntryType = NotebookEntryType.MEETING
    template_key: str = Field(default="meeting", min_length=1, max_length=80)
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    structured_data: dict[str, Any] = Field(default_factory=dict)
    meeting_at: datetime | None = None


class NotebookEntryPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    structured_data: dict[str, Any] | None = None
    meeting_at: datetime | None = None
    expected_version: int = Field(ge=1)


class NotebookEntryOut(BaseModel):
    id: str
    student_id: str
    author_user_id: str
    entry_type: NotebookEntryType
    template_key: str
    template_version: int
    title: str | None
    body: str
    structured_data: dict[str, Any]
    visibility: NotebookVisibility
    status: RecordStatus
    meeting_at: datetime | None
    published_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class StudentNotebookOut(BaseModel):
    id: str
    title: str | None
    body: str
    entry_type: NotebookEntryType
    meeting_at: datetime | None
    published_at: datetime | None


class ActionIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    priority: ActionPriority = ActionPriority.NORMAL
    due_at: datetime | None = None


class ActionOut(BaseModel):
    id: str
    entry_id: str | None
    student_id: str
    owner_user_id: str
    title: str
    description: str | None
    status: ActionStatus
    priority: ActionPriority
    due_at: datetime | None
    version: int

    model_config = {"from_attributes": True}


class AttachmentIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    byte_size: int = Field(ge=1, le=25_000_000)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class AttachmentOut(BaseModel):
    id: str
    entry_id: str
    filename: str
    content_type: str
    byte_size: int
    sha256: str
    status: str

    model_config = {"from_attributes": True}


def _entry_out(row: MentorNotebookEntry) -> NotebookEntryOut:
    return NotebookEntryOut.model_validate(row)


def _action_out(row: MentorNotebookAction) -> ActionOut:
    return ActionOut.model_validate(row)


def _entry_snapshot(row: MentorNotebookEntry) -> dict[str, Any]:
    """Return a redacted change snapshot safe for audit and revision storage.

    Notebook bodies and structured values are private staff content. Keep only
    stable metadata, hashes, and keys in durable history so audit readers,
    exports, backups, and workers cannot accidentally receive the plaintext.
    """
    body_hash = hashlib.sha256((row.body or "").encode("utf-8")).hexdigest()
    structured_json = json.dumps(
        row.structured_data or {}, sort_keys=True, separators=(",", ":"), default=str
    )
    structured_hash = hashlib.sha256(structured_json.encode("utf-8")).hexdigest()
    return {
        "id": row.id,
        "student_id": row.student_id,
        "title": row.title,
        "body_sha256": body_hash,
        "structured_data_sha256": structured_hash,
        "structured_data_keys": sorted((row.structured_data or {}).keys()),
        "visibility": row.visibility.value,
        "status": row.status.value,
        "version": row.version,
    }


def _require_notebook(db: Session, session: dict) -> None:
    """The notebook gate: staff, holding `mentor.notebook`.

    B2.1 REPLACED A ROLE GATE WITH THE CAPABILITY; IT DID NOT ADD ONE BESIDE IT,
    and the difference matters. `policies.require_notebook_staff` admits role
    MENTOR and nobody else. Compose the two and the Main Admin granted
    `mentor.notebook` in Governance -- with a reason, on the audit trail --
    still gets 403, because the role gate refuses before the grant is consulted.
    Three places in this repository promise that grant works:
    `require_notebook_staff`'s own docstring ("that grant is the audited way
    in"), `_FACULTY_ONLY` in app/governance.py ("the Main Admin can GRANT any of
    them ... to itself when a student's evidence is stuck"), and AGENTS.md. A
    key that is checked but can never be satisfied is the same lie B2.1 exists
    to remove, told one layer further in.

    So the capability IS the gate, over a staff floor. `require_staff` stays
    because "is this a member of staff at all" is a different question from "may
    you use the notebook", and it must fail closed for a STUDENT or ALUMNI
    session however a grant row came to exist.

    WHO THIS ADMITS, EXACTLY:
      * a faculty account with mentees -- `mentor.notebook` is one of the four
        functions derived from the mentee count (app/mentor_functions.py), so
        nothing changes for the people who use this screen;
      * a faculty account with NO mentees -- refused 403 rather than the 404 it
        used to get from rule 2 one line later. Same outcome, true reason;
      * the Main Admin, only once someone grants it. Reading, not authoring:
        `create_entry` and `create_action` keep their own "only an assigned
        mentor" checks, so standing in for a colleague means reading their
        record and closing their actions, never writing new entries under a
        mentor's name.

    Rule 2 is untouched and still runs on every one of the eight routes: the
    capability says which SCREEN, `assert_student_scope` says which STUDENTS.
    """
    require_staff(session)
    require_capability(db, session, "mentor.notebook")


@router.get("/mentor/mentees", response_model=list[MenteeOut])
def list_mentees(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[MenteeOut]:
    require_staff(session)
    tenant_id = tenant_id_for_session(session, db)
    query = select(Student, User.name).join(User, Student.user_id == User.id)
    if tenant_id:
        query = query.join(
            TenantMembership, TenantMembership.user_id == Student.user_id
        ).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.role == MembershipRole.STUDENT,
            TenantMembership.status == "ACTIVE",
            TenantMembership.ended_at.is_(None),
        )
    if session["role"] == "MENTOR":
        mentor_id = session.get("mentorId")
        if not mentor_id:
            return []
        query = query.where(Student.mentor_id == mentor_id)
    return [
        MenteeOut(
            student_id=s.id,
            name=name,
            usn=s.usn,
            current_stage=s.current_stage.value,
            current_semester=s.current_semester,
        )
        for s, name in db.execute(query.order_by(User.name)).all()
    ]


@router.get("/mentor/notebook/students/{student_id}/entries", response_model=list[NotebookEntryOut])
def list_entries(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[NotebookEntryOut]:
    _require_notebook(db, session)
    assert_student_scope(session, student_id, db)
    rows = db.scalars(
        select(MentorNotebookEntry)
        .where(
            MentorNotebookEntry.student_id == student_id,
            MentorNotebookEntry.deleted_at.is_(None),
        )
        .order_by(
            MentorNotebookEntry.meeting_at.desc().nullslast(),
            MentorNotebookEntry.created_at.desc(),
        )
    ).all()
    return [_entry_out(row) for row in rows]


@router.post(
    "/mentor/notebook/students/{student_id}/entries",
    response_model=NotebookEntryOut,
    status_code=status.HTTP_201_CREATED,
)
def create_entry(
    student_id: str,
    body: NotebookEntryIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> NotebookEntryOut | JSONResponse:
    _require_notebook(db, session)
    assert_student_scope(session, student_id, db)
    if session["role"] != "MENTOR" or not session.get("mentorId"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an assigned mentor can author notebook entries.",
        )
    replay = replay_or_reserve(
        db,
        principal_id=session["userId"],
        route=str(request.url.path),
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if replay is not None and replay.response_json is not None:
        return JSONResponse(status_code=replay.response_status or 201, content=replay.response_json)

    row = MentorNotebookEntry(
        student_id=student_id,
        author_user_id=session["userId"],
        mentor_id=session["mentorId"],
        entry_type=body.entry_type,
        template_key=body.template_key.strip(),
        title=(body.title or "").strip() or None,
        body=body.body.strip(),
        structured_data=body.structured_data,
        visibility=NotebookVisibility.PRIVATE_STAFF,
        status=RecordStatus.DRAFT,
        meeting_at=body.meeting_at or utc_now(),
        version=1,
    )
    db.add(row)
    db.flush()
    snapshot = _entry_snapshot(row)
    db.add(
        MentorNotebookEntryRevision(
            entry_id=row.id,
            version=1,
            author_user_id=session["userId"],
            snapshot_json=snapshot,
        )
    )
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=tenant_id_for_session(session, db),
        entity_type="mentor_notebook_entry",
        entity_id=row.id,
        action="created",
        before=None,
        after=snapshot,
        event_type="mentor.notebook.entry.created",
        payload={"student_id": student_id, "visibility": "PRIVATE_STAFF"},
    )
    response = _entry_out(row)
    response_json = response.model_dump(mode="json")
    store_response(replay, status_code=201, body=response_json)
    db.commit()
    return response


@router.patch("/mentor/notebook/entries/{entry_id}", response_model=NotebookEntryOut)
def update_entry(
    entry_id: str,
    body: NotebookEntryPatch,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> NotebookEntryOut | JSONResponse:
    _require_notebook(db, session)
    replay = replay_or_reserve(
        db,
        principal_id=session["userId"],
        route=str(request.url.path),
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if replay.response_json is not None:
        return JSONResponse(status_code=replay.response_status or 200, content=replay.response_json)
    row = db.scalar(
        select(MentorNotebookEntry)
        .where(MentorNotebookEntry.id == entry_id)
        .with_for_update()
    )
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Notebook entry not found.")
    assert_student_scope(session, row.student_id, db)
    if row.version != body.expected_version:
        raise HTTPException(status_code=409, detail="Notebook entry was changed by another user.")
    before = _entry_snapshot(row)
    if body.title is not None:
        row.title = body.title.strip() or None
    if body.body is not None:
        row.body = body.body.strip()
    if body.structured_data is not None:
        row.structured_data = body.structured_data
    if body.meeting_at is not None:
        row.meeting_at = body.meeting_at
    row.version += 1
    db.flush()
    after = _entry_snapshot(row)
    db.add(
        MentorNotebookEntryRevision(
            entry_id=row.id,
            version=row.version,
            author_user_id=session["userId"],
            snapshot_json=after,
        )
    )
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=tenant_id_for_session(session, db),
        entity_type="mentor_notebook_entry",
        entity_id=row.id,
        action="updated",
        before=before,
        after=after,
        event_type="mentor.notebook.entry.updated",
        payload={"version": row.version},
    )
    response = _entry_out(row)
    store_response(replay, status_code=200, body=response.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return response


@router.post("/mentor/notebook/entries/{entry_id}/publish", response_model=NotebookEntryOut)
def publish_entry(
    entry_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> NotebookEntryOut | JSONResponse:
    _require_notebook(db, session)
    replay = replay_or_reserve(
        db,
        principal_id=session["userId"],
        route=str(request.url.path),
        key=idempotency_key,
        payload={"entry_id": entry_id, "command": "publish"},
    )
    if replay.response_json is not None:
        return JSONResponse(status_code=replay.response_status or 200, content=replay.response_json)
    row = db.scalar(
        select(MentorNotebookEntry)
        .where(MentorNotebookEntry.id == entry_id)
        .with_for_update()
    )
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Notebook entry not found.")
    assert_student_scope(session, row.student_id, db)
    if row.status == RecordStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="Archived entries cannot be published.")
    before = _entry_snapshot(row)
    row.status = RecordStatus.PUBLISHED
    row.visibility = NotebookVisibility.STUDENT_VISIBLE
    row.published_at = utc_now()
    row.version += 1
    after = _entry_snapshot(row)
    db.add(
        MentorNotebookEntryRevision(
            entry_id=row.id,
            version=row.version,
            author_user_id=session["userId"],
            snapshot_json=after,
        )
    )
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=tenant_id_for_session(session, db),
        entity_type="mentor_notebook_entry",
        entity_id=row.id,
        action="published",
        before=before,
        after=after,
        event_type="mentor.notebook.entry.published",
        payload={"student_id": row.student_id},
    )
    response = _entry_out(row)
    store_response(replay, status_code=200, body=response.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return response


@router.post("/mentor/notebook/entries/{entry_id}/archive", response_model=NotebookEntryOut)
def archive_entry(
    entry_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> NotebookEntryOut | JSONResponse:
    _require_notebook(db, session)
    replay = replay_or_reserve(
        db,
        principal_id=session["userId"],
        route=str(request.url.path),
        key=idempotency_key,
        payload={"entry_id": entry_id, "command": "archive"},
    )
    if replay.response_json is not None:
        return JSONResponse(status_code=replay.response_status or 200, content=replay.response_json)
    row = db.scalar(
        select(MentorNotebookEntry)
        .where(MentorNotebookEntry.id == entry_id)
        .with_for_update()
    )
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Notebook entry not found.")
    assert_student_scope(session, row.student_id, db)
    before = _entry_snapshot(row)
    row.status = RecordStatus.ARCHIVED
    row.deleted_at = utc_now()
    row.version += 1
    after = _entry_snapshot(row)
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=tenant_id_for_session(session, db),
        entity_type="mentor_notebook_entry",
        entity_id=row.id,
        action="archived",
        before=before,
        after=after,
        event_type="mentor.notebook.entry.archived",
        payload={"student_id": row.student_id},
    )
    response = _entry_out(row)
    store_response(replay, status_code=200, body=response.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return response


@router.get("/mentor/notebook/students/{student_id}/actions", response_model=list[ActionOut])
def list_actions(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[ActionOut]:
    _require_notebook(db, session)
    assert_student_scope(session, student_id, db)
    rows = db.scalars(
        select(MentorNotebookAction)
        .where(MentorNotebookAction.student_id == student_id)
        .order_by(
            MentorNotebookAction.due_at.asc().nullslast(),
            MentorNotebookAction.created_at.desc(),
        )
    ).all()
    return [_action_out(row) for row in rows]


@router.post(
    "/mentor/notebook/students/{student_id}/actions",
    response_model=ActionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_action(
    student_id: str,
    body: ActionIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ActionOut | JSONResponse:
    _require_notebook(db, session)
    assert_student_scope(session, student_id, db)
    if session["role"] != "MENTOR":
        raise HTTPException(status_code=403, detail="Only a mentor can create notebook actions.")
    replay = replay_or_reserve(
        db,
        principal_id=session["userId"],
        route=str(request.url.path),
        key=idempotency_key,
        payload={"student_id": student_id, **body.model_dump(mode="json")},
    )
    if replay is not None and replay.response_json is not None:
        return JSONResponse(status_code=replay.response_status or 201, content=replay.response_json)

    row = MentorNotebookAction(
        student_id=student_id,
        owner_user_id=session["userId"],
        title=body.title.strip(),
        description=body.description,
        priority=body.priority,
        due_at=body.due_at,
    )
    db.add(row)
    db.flush()
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=tenant_id_for_session(session, db),
        entity_type="mentor_notebook_action",
        entity_id=row.id,
        action="created",
        before=None,
        after={"title": row.title, "student_id": student_id},
        event_type="mentor.notebook.action.created",
        payload={"student_id": student_id},
    )
    response = _action_out(row)
    store_response(replay, status_code=201, body=response.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return response


@router.post(
    "/mentor/notebook/entries/{entry_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_attachment(
    entry_id: str,
    body: AttachmentIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AttachmentOut | JSONResponse:
    _require_notebook(db, session)
    replay = replay_or_reserve(
        db,
        principal_id=session["userId"],
        route=str(request.url.path),
        key=idempotency_key,
        payload={"entry_id": entry_id, **body.model_dump(mode="json")},
    )
    if replay.response_json is not None:
        return JSONResponse(status_code=replay.response_status or 201, content=replay.response_json)
    entry = db.get(MentorNotebookEntry, entry_id)
    if entry is None or entry.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Notebook entry not found.")
    assert_student_scope(session, entry.student_id, db)
    row = MentorNotebookAttachment(
        entry_id=entry_id,
        uploaded_by_user_id=session["userId"],
        filename=body.filename.strip(),
        content_type=body.content_type,
        byte_size=body.byte_size,
        sha256=body.sha256.lower(),
        storage_key="pending",
        status="PENDING",
    )
    db.add(row)
    db.flush()
    row.storage_key = f"pending/{row.id}"
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=tenant_id_for_session(session, db),
        entity_type="mentor_notebook_attachment",
        entity_id=row.id,
        action="registered",
        before=None,
        after={"entry_id": entry_id, "filename": row.filename},
        event_type="mentor.notebook.attachment.registered",
        payload={"entry_id": entry_id},
    )
    response = AttachmentOut.model_validate(row)
    store_response(replay, status_code=201, body=response.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return response


@router.get("/student/mentor-notebook", response_model=list[StudentNotebookOut])
def student_notebook(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[StudentNotebookOut]:
    student_id = student_identity(session)
    rows = db.scalars(
        select(MentorNotebookEntry)
        .where(
            MentorNotebookEntry.student_id == student_id,
            MentorNotebookEntry.visibility == NotebookVisibility.STUDENT_VISIBLE,
            MentorNotebookEntry.status == RecordStatus.PUBLISHED,
            MentorNotebookEntry.deleted_at.is_(None),
        )
        .order_by(
            MentorNotebookEntry.meeting_at.desc().nullslast(),
            MentorNotebookEntry.created_at.desc(),
        )
    ).all()
    return [
        StudentNotebookOut(
            id=row.id,
            title=row.title,
            body=row.body,
            entry_type=row.entry_type,
            meeting_at=row.meeting_at,
            published_at=row.published_at,
        )
        for row in rows
    ]
