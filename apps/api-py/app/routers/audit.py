"""B2.7 — the audit log, read back.

    GET /api/admin/audit              the filtered page
    GET /api/admin/audit/export.csv   the same query as a spreadsheet (audited)
    GET /api/admin/audit/{id}         one event, with its before/after pair

THE ROWS ALREADY EXIST. `architecture_events.record_change` has been writing
`redesign_audit_events` from nineteen literal call sites — twenty-seven
endpoints once the three `_audit` wrappers in swoc.py, interview_bank.py and
admin_students.py are expanded — since the Phase 4 additive models landed. What
was missing was any way to read them: the only instrument for "who revoked that
grant" was `psql`. This is the reader, and it adds no column and no table.

APPEND-ONLY, AND IT STAYS THAT WAY. There is no write endpoint here and there
must never be one: an audit trail a console can edit is not an audit trail.
`retention.py` has never swept the table, and `purge_people.VERDICTS` and
`purge_students.STUDENT_VERDICTS` both say KEEP for it — they used to EMPTY it,
which meant handing a deployment over erased the record of who had done what to
it. `tests/test_audit_api.py` pins all three so the verdicts cannot drift back.

WHY THIS IS MAIN-ADMIN-ONLY RATHER THAN SCOPED, which is not what
04-backend-changes.md says. The spec asks for the list to be "scoped by B1.2 for
college admins", and B1.2's `policies.scope_filter` is real and works — but it
narrows by the institutional spine (college → department → course →
specialization → batch → student), and an audit row carries NONE of those. Its
only tenancy column is `tenant_id`, which is nullable, and nothing in `app/` or
in `migrations/` has ever inserted a `TenantMembership` or set it: every row in
the table is NULL and always has been. A scope filter written against it would
be a join that compiles, returns everything for the Main Admin, nothing for
anybody else, and reads at the call site as though scoping were solved.

So the honest implementation of "scoped" today is "the office alone", and that
is what this does. When audit rows start carrying a college — the row would need
it stamped at write time by `record_change`, not inferred later, because the
ancestry of an entity at the moment it was changed is not recoverable from the
entity's current row — `_gate` below becomes the one line that changes.

`_gate` is also where `admin.governance` arrives. B2.6 adds that capability in
this same phase; it does not exist in the catalogue yet and this module must not
invent it (`require_capability` raises ValueError on an unknown key, which would
turn every request here into a 500). One line, one swap, one place.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..db import get_db
from ..identity import get_current_session
from ..models.redesign import AuditEvent
from ..models.user import User
from .mentor import require_admin

router = APIRouter(prefix="/admin/audit", tags=["audit"])

#: The page the console draws, and the ceiling a caller may ask for. A page size
#: with no ceiling is one `?page_size=1000000` away from materialising the whole
#: table into one JSON response.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
#: The highest page any list here will serve. Unbounded, a crafted `?page=` made
#: `(page - 1) * page_size` overflow Postgres' bigint OFFSET and the request a 500
#: (DEF-006, testing/docs/05-incident-reports.md). A value past it is a 422.
MAX_PAGE = 100_000

#: The most rows one CSV may carry. Over this the export REFUSES and names the
#: count, rather than silently returning the first N — a truncated audit export
#: is worse than none, because it looks complete and nothing in the file says
#: which period it actually covers.
MAX_EXPORT_ROWS = 10_000


def _gate(db: Session, session: dict) -> None:
    """Who may read the trail. THE ONE LINE TO CHANGE — see the module docstring.

    Today: the Main Admin, by role. When B2.6 lands `admin.governance` in the
    capability catalogue this becomes
    `require_capability(db, session, "admin.governance")` and nothing else in
    this module moves. `db` is already in the signature for that reason.
    """
    require_admin(session)


def _as_utc(value: datetime) -> datetime:
    """A naive bound means UTC. FastAPI accepts `?from=2026-09-01T00:00:00` with
    no offset, and comparing that against a timezone-aware column raises in
    psycopg rather than answering — so the ambiguity is resolved here, once,
    and the same way the column is stored."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------- schemas --


class AuditRowOut(BaseModel):
    id: str
    occurred_at: datetime
    #: Null when the actor's account has been deleted — the FK is SET NULL, and
    #: the event survives the person deliberately.
    actor_user_id: str | None
    actor_name: str | None
    actor_email: str | None
    actor_type: str
    action: str
    #: `entity_type` / `entity_id` on the row. The API says target because
    #: 04-backend-changes.md's query parameters do; the columns are not renamed.
    target_type: str
    target_id: str
    route: str | None
    request_id: str | None
    correlation_id: str | None


class AuditDetailOut(AuditRowOut):
    #: The pair. Either may be null and the two nulls mean different things: a
    #: creation has no before, a deletion has no after, and a row with neither
    #: is an event that changed no fields (a grant revoked twice, an export).
    before: dict | None
    after: dict | None
    metadata: dict


class AuditPageOut(BaseModel):
    items: list[AuditRowOut]
    page: int
    page_size: int
    #: The size of the whole filtered set, not of this page — the console needs
    #: it to draw "1-50 of 412" and to know whether a next page exists.
    total: int


# -------------------------------------------------------------- the query --


def _filters(
    db: Session,
    *,
    actor: str | None,
    action: str | None,
    target_type: str | None,
    target_id: str | None,
    from_: datetime | None,
    to: datetime | None,
) -> list:
    """The WHERE clauses shared by the page, the count and the CSV.

    ONE function for all three, because a CSV that answers a different question
    from the screen above it is the export people quietly stop trusting.
    """
    conds = []
    if actor:
        needle = actor.strip()
        if "@" in needle:
            # An operator reading the console knows colleagues by address, not
            # by a uuid4 hex. Resolved to the id here rather than joined in the
            # predicate so an unknown address returns an empty page instead of
            # every row.
            resolved = db.scalar(select(User.id).where(func.lower(User.email) == needle.lower()))
            conds.append(AuditEvent.actor_user_id == (resolved or ""))
        else:
            conds.append(AuditEvent.actor_user_id == needle)
    if action:
        # CASE-INSENSITIVE, AND THAT IS NOT TIDINESS. This read `== action.strip()
        # .upper()`, which enshrined one of the two vocabularies actually in the
        # table: most writers name an action in upper case (`GRANTED`,
        # `FEATURE_DISABLED`), and `routers/redesign.py`'s six mentor-notebook
        # writes name it in lower (`created`, `updated`, `published`,
        # `archived`, `registered`). Upper-casing the needle turned a search for
        # `created` into `CREATED`, which matches interview-question rows and can
        # NEVER match a notebook one — so those events listed and opened
        # perfectly and were unreachable by the filter above them, which is the
        # shape of bug an operator reports as "the notebook is not audited".
        #
        # Fixed on the READER because `audit_events` is append-only: the rows
        # saying `created` are the record of what happened, and a migration that
        # rewrote them to `CREATED` would be this module editing the trail it
        # exists to show. `record_change` now upper-cases what it writes, so the
        # vocabulary converges going forward; this clause is what keeps every row
        # written before that reachable, forever.
        #
        # `func.upper` on the column is a sequential comparison, and costs
        # nothing here: there is no index on `audit_events.action` to defeat.
        conds.append(func.upper(AuditEvent.action) == action.strip().upper())
    if target_type:
        conds.append(AuditEvent.entity_type == target_type.strip())
    if target_id:
        conds.append(AuditEvent.entity_id == target_id.strip())
    if from_ is not None:
        conds.append(AuditEvent.occurred_at >= _as_utc(from_))
    if to is not None:
        conds.append(AuditEvent.occurred_at <= _as_utc(to))
    return conds


def _ordered(conds: list) -> Select:
    """Newest first, and `id` as the tiebreaker.

    `occurred_at` is a server default, so every row written inside one
    transaction shares a timestamp to the microsecond: without a second, stable
    sort key the same event can appear on page 1 and page 2 of one listing and
    another appear on neither.
    """
    return (
        select(AuditEvent, User.name, User.email)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(*conds)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
    )


def _row(event: AuditEvent, name: str | None, email: str | None) -> AuditRowOut:
    return AuditRowOut(
        id=event.id,
        occurred_at=event.occurred_at,
        actor_user_id=event.actor_user_id,
        actor_name=name,
        actor_email=email,
        actor_type=event.actor_type,
        action=event.action,
        target_type=event.entity_type,
        target_id=event.entity_id,
        route=(event.metadata_json or {}).get("route"),
        request_id=event.request_id,
        correlation_id=event.correlation_id,
    )


# ------------------------------------------------------------- endpoints --


@router.get("", response_model=AuditPageOut)
def list_events(
    actor: str | None = Query(default=None, description="Actor user id, or email address."),
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    page: int = Query(default=1, ge=1, le=MAX_PAGE),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AuditPageOut:
    """The trail, newest first, filtered.

    A READ, and nothing here writes a row of its own. Listing the log is not an
    event worth logging — it would be the most frequent action in the table
    within a week and would bury everything the table exists to show. The
    download is different, and audits itself; see below.
    """
    _gate(db, session)
    conds = _filters(
        db,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        from_=from_,
        to=to,
    )
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conds)) or 0
    rows = db.execute(_ordered(conds).offset((page - 1) * page_size).limit(page_size)).all()
    return AuditPageOut(
        items=[_row(event, name, email) for event, name, email in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/export.csv")
def export_csv(
    request: Request,
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """The same query, as a file — and the download is itself an audit event.

    DECLARED BEFORE `/{event_id}`, and that is not style. FastAPI matches routes
    in declaration order, so a path parameter declared first would swallow
    `export.csv` and answer 404 for an event that does not exist.

    The audit row is written AFTER the rows are selected, so an export never
    contains the record of itself — a file whose last line is "this file was
    downloaded" is confusing, and the row is in the table for the next reader
    either way.
    """
    _gate(db, session)
    conds = _filters(
        db,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        from_=from_,
        to=to,
    )
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conds)) or 0
    if total > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"That query matches {total} events; at most {MAX_EXPORT_ROWS} can be "
                "exported at once. Narrow the date range or the filters."
            ),
        )
    rows = db.execute(_ordered(conds)).all()

    def _cell(value: str) -> str:
        # CSV FORMULA INJECTION, the same hardening the badge cohort export
        # applies in badge_verification.py — and it matters more here, because
        # the values in this file are not the office's own vocabulary: an
        # entity id, a route and an actor's name all arrive from somewhere else,
        # and a name recorded as "=HYPERLINK(...)" becomes a live formula the
        # moment the file is opened in Excel or Sheets. The leading apostrophe
        # is the spreadsheet convention for "this is text": it is obeyed, not
        # displayed. The web client hardens its own CSV cells identically.
        return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["When", "Actor", "Actor email", "Actor type", "Action", "Target type", "Target id", "Route", "Request id"]
    )
    for event, name, email in rows:
        writer.writerow(
            [
                event.occurred_at.isoformat() if event.occurred_at else "",
                _cell(name or ""),
                _cell(email or ""),
                _cell(event.actor_type or ""),
                _cell(event.action or ""),
                _cell(event.entity_type or ""),
                _cell(event.entity_id or ""),
                _cell((event.metadata_json or {}).get("route") or ""),
                _cell(event.request_id or ""),
            ]
        )

    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="audit_export",
        # The export is an ACT, not a record with a lasting identity, so the id
        # names this download and nothing else. Reusing a constant here would
        # make every export look like repeated edits to one object.
        entity_id=uuid.uuid4().hex,
        action="EXPORTED",
        before=None,
        after={
            "rows": len(rows),
            "filters": {
                "actor": actor,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "from": from_.isoformat() if from_ else None,
                "to": to.isoformat() if to else None,
            },
        },
        event_type="audit.exported",
        payload={"rows": len(rows)},
    )
    # `get_db` never commits, so the audit row is this handler's to persist.
    # A download that leaves no trace because nobody called commit is exactly
    # the failure this endpoint exists to prevent.
    db.commit()

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )


@router.get("/{event_id}", response_model=AuditDetailOut)
def read_event(
    event_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AuditDetailOut:
    """One event, with the before/after pair the list deliberately omits.

    The snapshots are whole JSON documents — a capability grant, a student row,
    a SWOC line — and putting them on every row of a fifty-row page would make
    the listing many times larger than the screen that draws it.
    """
    _gate(db, session)
    found = db.execute(
        select(AuditEvent, User.name, User.email)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(AuditEvent.id == event_id)
    ).first()
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Audit event not found."
        )
    event, name, email = found
    base = _row(event, name, email)
    return AuditDetailOut(
        **base.model_dump(),
        before=event.before_json,
        after=event.after_json,
        metadata=event.metadata_json or {},
    )
