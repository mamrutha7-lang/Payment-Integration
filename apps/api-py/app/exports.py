"""B14 — what an extract is allowed to carry, and the record that it left.

An export is the one thing in REEP that cannot be taken back. AGENTS.md is
blunt about it: the rows "leave REEP the moment they are downloaded and nothing
here can recall them". Until this module there were four CSV endpoints in two
routers, each with its own idea of who may call it, each returning every row on
the deployment, and each leaving no trace but a line in an access log nobody
reads.

Three rules live here, in one place, because a rule about exports written twice
is a rule that will be applied once:

1. SCOPE. `policies.scope_filter(db, session, "admin.exports")` decides which
   students may be in the file, exactly as it decides which students may be in
   a list. An export is a list that leaves the building; it cannot be the one
   surface where a department-scoped grant reads the whole programme.

2. PERSONAL COLUMNS. See `PERSONAL_COLUMN_CAPABILITY` — the columns that NAME a
   person are dropped, header and cell, unless the caller holds the roster
   function. A de-identified extract is still a useful extract: stage and
   semester distributions, ledger compliance rates, an offer funnel. A named
   one is a spreadsheet of students.

3. THE RECEIPT. Every download writes an `export_events` row — who, what kind,
   the filters that decided the rows, how many rows, and whether the file
   carried names — and an audit event beside it. A read that produces a file is
   a mutation of the world outside REEP, which is why a GET is audited here and
   nowhere else in this codebase.

WHY THE SCOPE IS NOT IN THE RESPONSE BODY. 04-backend-changes.md asks (B1.4)
that "every response carries `scope: {college, department}`". Three of these
four endpoints have no body to put it in: they return
`Response(media_type="text/csv")`, and a JSON envelope around a CSV is not a
CSV. A comment row inside the file would corrupt it for every reader that is
not a human. So the scope travels as RESPONSE HEADERS (`X-Reep-Export-Scope`
and friends), which every caller can read and no spreadsheet parser can trip
over, and `GET /api/admin/exports/history` — which does have a body — carries
the scope of the caller reading it.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Final, Sequence

from fastapi import Response
from sqlalchemy.orm import Session

from .architecture_events import record_change
from .governance import has_capability
from .models.account_events import ExportEvent
from .policies import Reach

#: The function that unlocks the columns which NAME a person.
#:
#: `Capability.carries_pii` is the obvious candidate and it cannot be the test
#: here — not through oversight, but because `admin.exports` is ITSELF flagged
#: `carries_pii=True` (that flag is what makes a DEPUTY's grant of it wait for
#: a second signature, B2.4). "Does the caller hold a capability that carries PII" is
#: therefore answered yes by the very capability that let them through the door,
#: for every caller, on every one of these endpoints. A rule that is true for
#: everybody is not a rule; it is a comment.
#:
#: So the personal columns hang on the ROSTER function instead. `admin.students`
#: is the capability whose entire subject is a student's identity — name, USN,
#: address, which batch they sit in — and granting it is already the decision
#: that this person may read students BY NAME. Read the two together and the
#: sentence is the right one: an Exports grant alone says "you may take the
#: numbers away"; the roster function says "and you may take the names with
#: them". The Main Admin holds both by baseline and sees no change at all.
#:
#: One constant rather than a set, deliberately. A set invites the next editor
#: to add a second key to unblock one screen, and the day it holds three keys
#: nobody can say what the rule is any more.
PERSONAL_COLUMN_CAPABILITY: Final[str] = "admin.students"

#: What a redacted file says INSTEAD of an omitted header, in the scope header.
PERSONAL_INCLUDED: Final[str] = "included"
PERSONAL_OMITTED: Final[str] = "omitted"


def carries_personal_columns(db: Session, session: dict) -> bool:
    """May this caller's file name the students in it?"""
    return has_capability(db, session, PERSONAL_COLUMN_CAPABILITY)


def csv_cell(value: object) -> str:
    """One cell, safe to open in a spreadsheet.

    CSV FORMULA INJECTION. A student who registers as `=HYPERLINK("http://…",
    "click")` has written a live formula into every export that names them, and
    Excel and Google Sheets both evaluate it the moment a placement officer
    opens the file — on their machine, with their credentials. The leading
    apostrophe is the spreadsheet convention for "this is text": it is obeyed
    and never displayed.

    SIX LEADING CHARACTERS, and the last three are the ones usually missed.
    `=`, `+` and `-` start a formula outright; `@` starts one in Excel's older
    lookup syntax; and TAB and CR are stripped by some readers BEFORE the
    formula test, so `\\t=cmd|…` arrives at the parser as `=cmd|…` with the
    guard already satisfied. Every cell goes through this, not only the ones
    that look like names — a course code, a cohort label and a mentor's name are
    all typed by a person somewhere.
    """
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def scope_note(reach: Reach) -> dict[str, Any]:
    """The reach, as something that can be written into a JSON column.

    Three words rather than a boolean, because `everything` and `nothing` are
    not two ends of one scale: "this account may see the whole programme" and
    "this account's only grant points at a department that has been deleted" are
    opposite facts that must never render the same on a receipt.
    """
    if reach.everything:
        return {"scope": "programme"}
    if reach.nothing:
        return {"scope": "none"}
    note: dict[str, Any] = {"scope": "narrowed"}
    for name in ("colleges", "departments", "courses", "specializations", "cohorts", "students"):
        ids = getattr(reach, name)
        if ids:
            note[name] = sorted(ids)
    return note


def drop_personal(
    header: Sequence[str],
    rows: Sequence[Sequence[object]],
    personal: Sequence[str],
    *,
    carry: bool,
) -> tuple[list[str], list[list[object]]]:
    """Remove the named columns from the header AND every row, or keep both.

    The columns are named rather than indexed on purpose: an index list sitting
    next to a header list is two things that must agree, and the day somebody
    inserts a column in the middle the redaction quietly starts deleting the
    wrong one — which fails in the safe-looking direction (a name survives) and
    is invisible in review.
    """
    if carry:
        return list(header), [list(r) for r in rows]
    omit = {name for name in personal}
    keep = [i for i, name in enumerate(header) if name not in omit]
    return [header[i] for i in keep], [[row[i] for i in keep] for row in rows]


def csv_response(
    header: Sequence[str],
    rows: Sequence[Sequence[object]],
    filename: str,
    *,
    reach: Reach,
    carried_pii: bool,
) -> Response:
    """The file, with the scope stated in headers. See this module's docstring
    for why the scope is not in a body."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([csv_cell(h) for h in header])
    for row in rows:
        writer.writerow([csv_cell(v) for v in row])
    note = scope_note(reach)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Reep-Export-Scope": str(note["scope"]),
            "X-Reep-Export-Rows": str(len(rows)),
            "X-Reep-Export-Personal": PERSONAL_INCLUDED if carried_pii else PERSONAL_OMITTED,
        },
    )


def record_export(
    db: Session,
    *,
    session: dict,
    request: Any,
    kind: str,
    filters: dict[str, Any],
    rows: int,
    carried_pii: bool,
) -> None:
    """Write the receipt. Called by every export endpoint, after the rows are
    counted and before the file is returned.

    IT COMMITS, because `get_db` never does and these are GET handlers that
    would otherwise roll back on the way out — which is how an audited export
    becomes an unaudited one without anybody editing the audit line.

    A FAILURE HERE FAILS THE DOWNLOAD, deliberately, and it is the only place in
    this codebase where telemetry is allowed to do that. Everywhere else the
    reasoning is `_record_login`'s: the session is the product, the counter is
    telemetry, and refusing a correct password over a streak row would be
    absurd. An export inverts it. The file is the thing that cannot be recalled,
    the receipt is the only record that it went, and a download that silently
    leaves no trace is exactly the state this table was added to end.
    """
    db.add(
        ExportEvent(
            user_id=session.get("userId"),
            kind=kind,
            filters=filters,
            rows=rows,
            carried_pii=1 if carried_pii else 0,
        )
    )
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="export",
        entity_id=kind,
        action="EXPORT_DOWNLOAD",
        before=None,
        after={"kind": kind, "rows": rows, "carried_pii": carried_pii, **filters},
        event_type="export.download",
        payload={"kind": kind, "rows": rows, "carried_pii": carried_pii},
    )
    db.commit()
