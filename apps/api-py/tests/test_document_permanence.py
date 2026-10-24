"""A file's record outlives the file's deletion, end to end through the API.

`tests/test_document_manifest.py` pins the manifest's own contract on an
in-memory database. This module asks the question the college actually has:
**after a student or a staff member deletes a document on the live website, can
anyone still say what it was?**

Until `archived_documents` existed the answer was no. The bytes had 35 days
(the daily backup plan's lifecycle on the EFS volume, bounded by RDS's ceiling;
the archive rule that reaches past it selects the database alone) and the row
had none at all. Now the bytes are in an Object-Locked bucket and the row below
is what names them.

These run against the real endpoints because the manifest write lives in the
same transaction as the live row, and a transaction is exactly what a unit test
with a fake session cannot check.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models.archived_document import ArchivedDocument, DocumentOwnerKind
from app.models.user import Role

from conftest import requires_db

_PDF = b"%PDF-1.7\n% a certificate\n"
_PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"signature bytes"


def _manifest(stored_name: str) -> ArchivedDocument | None:
    with SessionLocal() as db:
        return db.scalar(
            select(ArchivedDocument).where(ArchivedDocument.stored_name == stored_name)
        )


def _manifest_for(kind: DocumentOwnerKind, owner_id: str) -> list[ArchivedDocument]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(ArchivedDocument).where(
                    ArchivedDocument.kind == kind, ArchivedDocument.owner_id == owner_id
                )
            )
        )


@requires_db
def test_a_deleted_certificate_is_still_nameable(client, make_user):
    """The staff shelf: upload, delete, and the record stands.

    `released_at` moves and nothing else does. The person's screen is correct a
    second later -- the certificate is gone from their list and their quota is
    freed -- and the archive still holds bytes somebody can put a name to.
    """
    mentor = make_user("perm-mentor", Role.MENTOR)
    r = client.post(
        "/api/staff/upskilling",
        headers=mentor.headers,
        files={"file": ("nptel.pdf", _PDF, "application/pdf")},
        data={"title": "NPTEL Data Science", "provider": "NPTEL", "completed_on": "2026-06-30"},
    )
    assert r.status_code == 201, r.text
    cert_id = r.json()["id"]

    rows = _manifest_for(DocumentOwnerKind.STAFF_CERTIFICATE, mentor.user_id)
    assert len(rows) == 1
    stored_name = rows[0].stored_name
    assert rows[0].original_name == "nptel.pdf"
    assert rows[0].title == "NPTEL Data Science"
    assert rows[0].released_at is None

    assert client.delete(f"/api/staff/upskilling/{cert_id}", headers=mentor.headers).status_code == 204
    assert client.get("/api/staff/upskilling", headers=mentor.headers).json() == []

    after = _manifest(stored_name)
    assert after is not None, "the manifest row went with the certificate"
    assert after.released_at is not None
    assert after.original_name == "nptel.pdf"
    assert after.title == "NPTEL Data Science"


@requires_db
def test_a_replaced_signature_keeps_both_generations(client, make_user):
    """THE CASE THAT MOTIVATED THE TABLE.

    `staff_signatures.user_id` is UNIQUE and the PUT overwrites `stored_name`
    in place, so there is no second row to soft-delete and nowhere on the live
    row to keep the old name. A `deleted_at` column cannot express this; two
    manifest rows can.
    """
    mentor = make_user("perm-sig", Role.MENTOR)
    for name in ("first.png", "second.png"):
        r = client.put(
            "/api/staff/signature",
            headers=mentor.headers,
            files={"file": (name, _PNG, "image/png")},
        )
        assert r.status_code == 200, r.text

    rows = _manifest_for(DocumentOwnerKind.STAFF_SIGNATURE, mentor.user_id)
    assert {r.original_name for r in rows} == {"first.png", "second.png"}
    superseded = next(r for r in rows if r.original_name == "first.png")
    current = next(r for r in rows if r.original_name == "second.png")
    assert superseded.released_at is not None
    assert superseded.released_reason == "signature replaced"
    assert current.released_at is None


@requires_db
def test_a_rejected_upload_stores_nothing_at_all(client, make_user):
    """A signature that is a valid PDF is refused, and NOTHING is written.

    This used to store the file, read the sniffed mime off the result and
    delete it again. That stopped being survivable when `save_bytes` gained
    the permanent archive: the delete cannot reach an Object-Locked bucket, so
    every wrong-type upload left an unnamed object there while its manifest row
    rolled back with the failed request. `document_store.sniff` asks first.
    """
    mentor = make_user("perm-reject", Role.MENTOR)
    before = len(_manifest_for(DocumentOwnerKind.STAFF_SIGNATURE, mentor.user_id))

    r = client.put(
        "/api/staff/signature",
        headers=mentor.headers,
        files={"file": ("signature.pdf", _PDF, "application/pdf")},
    )
    assert r.status_code == 422, r.text

    assert len(_manifest_for(DocumentOwnerKind.STAFF_SIGNATURE, mentor.user_id)) == before


@requires_db
def test_the_manifest_row_rolls_back_with_a_refused_upload(client, make_user):
    """`save_and_record` adds and flushes, never commits.

    A separate commit would make "file recorded, upload rolled back" reachable;
    this proves the manifest does not accumulate rows for requests that failed
    after the store write.
    """
    mentor = make_user("perm-rollback", Role.MENTOR)
    before = len(_manifest_for(DocumentOwnerKind.STAFF_CERTIFICATE, mentor.user_id))

    r = client.post(
        "/api/staff/upskilling",
        headers=mentor.headers,
        files={"file": ("junk.pdf", b"not a pdf at all", "application/pdf")},
        data={"title": "T", "provider": "P", "completed_on": "2026-06-30"},
    )
    assert r.status_code == 422, r.text
    assert len(_manifest_for(DocumentOwnerKind.STAFF_CERTIFICATE, mentor.user_id)) == before


@requires_db
def test_a_refused_field_never_leaves_an_unnameable_object(client, make_user):
    """The alumni profile was the one endpoint of the six that could still raise
    between the store write and the commit.

    `joined_on` was parsed AFTER the resume was stored, which was harmless until
    `save_bytes` gained the permanent archive: the 422 rolls the request back,
    but the bytes have already been written to the volume AND PUT into an
    Object-Locked bucket, while the manifest row that would have named them
    rolls back with the request. An unnameable object, kept for a decade,
    because a date was mistyped.

    THE ASSERTION IS ON THE FILE, NOT ON THE MANIFEST, and the first version of
    this test got that wrong. Counting manifest rows passes with the bug
    present, because the row rolls back either way -- the test was vacuous and
    was caught by re-running it against the unfixed code. What the archive
    copies is whatever `save_bytes` wrote, so the only honest question is
    whether anything was written at all.
    """
    alum = make_user("perm-alum", Role.ALUMNI)
    store = settings.uploads_path
    before = {p.name for p in store.iterdir()} if store.is_dir() else set()

    r = client.post(
        "/api/alumni/profile",
        headers=alum.headers,
        files={"resume": ("cv.pdf", _PDF, "application/pdf")},
        data={"company": "Acme", "joined_on": "not-a-date"},
    )
    assert r.status_code == 422, r.text
    assert "Date of joining" in r.text

    after = {p.name for p in store.iterdir()} if store.is_dir() else set()
    assert after == before, (
        f"the refused request still wrote {after - before} to the store; those "
        "bytes are in the permanent archive with no manifest row naming them"
    )
    assert _manifest_for(DocumentOwnerKind.ALUMNI_RESUME, alum.user_id) == []


@requires_db
def test_a_valid_alumni_profile_still_records_its_resume(client, make_user):
    """The other half: moving the parse earlier must not have broken the
    ordinary path, where the date IS valid and the resume IS stored."""
    alum = make_user("perm-alum-ok", Role.ALUMNI)

    r = client.post(
        "/api/alumni/profile",
        headers=alum.headers,
        files={"resume": ("cv.pdf", _PDF, "application/pdf")},
        data={"company": "Acme", "joined_on": "2026-01-15"},
    )
    assert r.status_code in (200, 201), r.text

    rows = _manifest_for(DocumentOwnerKind.ALUMNI_RESUME, alum.user_id)
    assert len(rows) == 1
    assert rows[0].original_name == "cv.pdf"
    assert rows[0].released_at is None
