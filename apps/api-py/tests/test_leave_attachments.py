"""B10.3 — the papers that came with a leave request.

What this module holds down, and what comes back if it is deleted:

1. THE READ GATE IS THE PAPER'S. An attachment on a leave request is a medical
   certificate by construction. If these endpoints ever grow a gate of their own
   it will be the copy that stops tracking `_assert_can_decide`, and the first
   symptom will be a faculty account with no group reading a colleague's
   diagnosis. Every refusal is flattened to the same 404 for the same reason the
   decision path flattens its own: told apart, the id space is a membership
   oracle.
2. THE QUOTA IS TWO-PHASE AND THE ORDER IS LOAD-BEARING. `check_slot()` runs
   BEFORE the body is read; `check_bytes()` after. A combined check has to read
   first, which is the denial of service the count cap exists to stop. The
   ordering is asserted BEHAVIOURALLY — a full shelf answers 409 for bytes that
   would have been a 422 if they had been sniffed first, and nothing lands on
   the volume.
3. THE THREE STATUS CODES ARE THE STORE'S AND THEY ARE NOT INTERCHANGEABLE.
   `QuotaRejected` is deliberately not a subclass of `UploadRejected`; catching
   them the other way round tells somebody their valid PDF was malformed.
4. THE DOWNLOAD IS NEVER INLINE. The SPA and the API are same-origin and a PDF
   rendered inline runs its embedded JavaScript there — uploaded by one
   applicant, opened by the approver reading it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from conftest import requires_db

from app import document_store
from app.document_store import MAX_LEAVE_ATTACHMENTS_PER_REQUEST
from app.models.user import Role
from app.routers import leave_attachments as router_module

LEAVES = "/api/leaves"


def _pdf(size: int = 512) -> bytes:
    """A byte string the store's magic-byte sniff accepts as a PDF. It does not
    have to BE a PDF — the sniff reads the first four bytes, and that is the
    whole contract this router depends on."""
    return b"%PDF-1.4\n" + b"x" * max(size - 9, 1)


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    """The same isolation `tests/test_leave_paper.py` uses. Any writer that does
    not go through `document_store._store_dir` escapes it, which is why the
    router calls `save_bytes` rather than writing bytes itself."""
    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    return tmp_path


def _leave(client, headers, **extra) -> dict:
    body = {
        "from_date": date.today().isoformat(),
        "to_date": (date.today() + timedelta(days=1)).isoformat(),
        "reason": "Post-operative review at Manipal.",
        "leave_kind": "CASUAL",
    }
    body.update(extra)
    r = client.post(LEAVES, headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _attach(client, headers, leave_id: str, content: bytes, name: str = "cert.pdf"):
    return client.post(
        f"{LEAVES}/{leave_id}/attachments",
        headers=headers,
        files={"file": (name, content, "application/pdf")},
    )


# ------------------------------------------------------------- the gate --


@requires_db
def test_the_applicant_and_the_approver_read_it_and_everybody_else_gets_one_404(
    client, make_user, _tmp_store
):
    applicant = make_user("att-app", Role.MENTOR)
    office = make_user("att-adm", Role.ADMIN)
    stranger = make_user("att-other", Role.MENTOR)  # staff, no group: sees nobody
    student = make_user("att-stu")

    leave = _leave(client, applicant.headers)
    url = f"{LEAVES}/{leave['id']}/attachments"

    r = _attach(client, applicant.headers, leave["id"], _pdf())
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["mime_type"] == "application/pdf" and row["size_bytes"] == 512
    assert row["uploaded_by_user_id"] == applicant.user_id
    assert row["can_delete"] is True

    # The applicant and the office see it; a group-less faculty account does not.
    assert len(client.get(url, headers=applicant.headers).json()) == 1
    assert len(client.get(url, headers=office.headers).json()) == 1

    refused = client.get(url, headers=stranger.headers)
    assert refused.status_code == 404
    assert refused.json()["detail"] == "Leave request not found."
    # BYTE-IDENTICAL to an id that never existed: that is what stops this being
    # an oracle over who has leave pending.
    invented = client.get(f"{LEAVES}/does-not-exist/attachments", headers=stranger.headers)
    assert invented.status_code == 404 and invented.json() == refused.json()

    # A STUDENT GETS THE SAME 404, and this used to assert 403 on the reasoning
    # that the role gate "runs before any id is looked at". That is true of
    # `routers/leave.py`, where `require_mentor` is a dependency on the route —
    # and false here, because this module has two doors and the APPLICANT's has
    # to be tried first: `_readable_request` loads the row, compares
    # `requester_user_id`, and only then calls `_assert_can_decide`. So the 403
    # was reachable ONLY with a real id while an invented one answered 404,
    # which is the membership oracle the flattening exists to close, told to
    # anybody holding a session. Byte-identical to the invented id, like the
    # staff refusal above.
    student_refused = client.get(url, headers=student.headers)
    assert student_refused.status_code == 404
    assert student_refused.json() == invented.json()


@requires_db
def test_the_approver_may_attach_the_offices_own_paper(client, make_user, _tmp_store):
    """The write gate is the READ gate, not "the applicant only": an approver
    adds the office's letter to a request they are signing, recorded against
    their own account."""
    applicant = make_user("att2-app", Role.MENTOR)
    office = make_user("att2-adm", Role.ADMIN)
    leave = _leave(client, applicant.headers)

    r = _attach(client, office.headers, leave["id"], _pdf(), name="office-note.pdf")
    assert r.status_code == 201, r.text
    assert r.json()["uploaded_by_user_id"] == office.user_id

    rows = client.get(f"{LEAVES}/{leave['id']}/attachments", headers=applicant.headers).json()
    assert [x["original_name"] for x in rows] == ["office-note.pdf"]
    # The applicant may READ the office's paper and may not remove it.
    assert rows[0]["can_delete"] is False
    gone = client.delete(
        f"{LEAVES}/{leave['id']}/attachments/{rows[0]['id']}", headers=applicant.headers
    )
    assert gone.status_code == 403 and "attached this file" in gone.text


# ------------------------------------------------------------ the quota --


@requires_db
def test_a_full_shelf_answers_409_before_the_bytes_are_looked_at(
    client, make_user, _tmp_store
):
    """THE TWO-PHASE ORDER, ASSERTED BEHAVIOURALLY.

    With the shelf full, a body that is NOT an accepted file comes back 409
    (`ShelfFull`) and not 422 (`UploadRejected`). It can only do that if
    `check_slot()` ran before the body was read and sniffed — which is the
    property a single combined check would destroy, and with it the reason the
    count cap exists. Nothing reaches the volume either.
    """
    applicant = make_user("att-full", Role.MENTOR)
    leave = _leave(client, applicant.headers)

    for i in range(MAX_LEAVE_ATTACHMENTS_PER_REQUEST):
        assert _attach(client, applicant.headers, leave["id"], _pdf(), f"p{i}.pdf").status_code == 201
    on_disk = len(list(_tmp_store.iterdir()))
    assert on_disk == MAX_LEAVE_ATTACHMENTS_PER_REQUEST

    r = _attach(client, applicant.headers, leave["id"], b"this is not a pdf at all", "junk.pdf")
    assert r.status_code == 409, r.text
    assert "limit of" in r.json()["detail"]
    assert len(list(_tmp_store.iterdir())) == on_disk, "nothing was written"

    # And the cap is PER REQUEST, not per user: the same applicant's next
    # application starts with an empty shelf.
    second = _leave(client, applicant.headers, reason="A different matter entirely.")
    assert _attach(client, applicant.headers, second["id"], _pdf()).status_code == 201


@requires_db
def test_the_byte_allowance_is_the_uploaders_and_answers_413(
    client, make_user, monkeypatch, _tmp_store
):
    """413, not 409 and not 422: the shelf has room and the bytes are a valid
    PDF; what is exhausted is the total allowance. Patched small so the test
    does not have to push 50 MB through the client."""
    monkeypatch.setattr(router_module, "MAX_LEAVE_ATTACHMENT_BYTES_PER_USER", 1200)
    applicant = make_user("att-bytes", Role.MENTOR)
    leave = _leave(client, applicant.headers)

    assert _attach(client, applicant.headers, leave["id"], _pdf(1000)).status_code == 201
    r = _attach(client, applicant.headers, leave["id"], _pdf(1000), "second.pdf")
    assert r.status_code == 413, r.text
    assert "allowance" in r.json()["detail"]

    # It is the UPLOADER's allowance, not the request's: a different account
    # attaching to the same request is unaffected.
    office = make_user("att-bytes-adm", Role.ADMIN)
    assert _attach(client, office.headers, leave["id"], _pdf(1000), "office.pdf").status_code == 201


@requires_db
def test_an_unsupported_type_is_422_and_the_store_decides_it(client, make_user, _tmp_store):
    applicant = make_user("att-type", Role.MENTOR)
    leave = _leave(client, applicant.headers)
    r = _attach(client, applicant.headers, leave["id"], b"GIF89a not accepted here", "x.pdf")
    assert r.status_code == 422, r.text
    assert "PDF, PNG and JPEG" in r.json()["detail"], "the store's own sentence, not the router's"
    assert list(_tmp_store.iterdir()) == []


# ------------------------------------------------- download and removal --


@requires_db
def test_the_download_is_an_attachment_and_carries_the_real_bytes(
    client, make_user, _tmp_store
):
    applicant = make_user("att-dl", Role.MENTOR)
    office = make_user("att-dl-adm", Role.ADMIN)
    leave = _leave(client, applicant.headers)
    content = _pdf(300)
    row = _attach(client, applicant.headers, leave["id"], content, "ಪ್ರಮಾಣಪತ್ರ.pdf").json()

    url = f"{LEAVES}/{leave['id']}/attachments/{row['id']}/file"
    r = client.get(url, headers=office.headers)
    assert r.status_code == 200 and r.content == content
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;"), "inline PDF is same-origin XSS here"
    assert "filename*=UTF-8''" in disposition, "a non-ASCII name must survive latin-1 headers"
    assert r.headers["cache-control"] == "private, no-store"

    # An attachment id that belongs to another request is not this request's.
    other = _leave(client, applicant.headers, reason="Another application.")
    crossed = client.get(
        f"{LEAVES}/{other['id']}/attachments/{row['id']}/file", headers=applicant.headers
    )
    assert crossed.status_code == 404
    assert crossed.json()["detail"] == "Leave request not found."


@requires_db
def test_the_uploader_removes_it_and_the_file_goes_with_the_row(
    client, make_user, _tmp_store
):
    applicant = make_user("att-del", Role.MENTOR)
    office = make_user("att-del-adm", Role.ADMIN)
    leave = _leave(client, applicant.headers)
    row = _attach(client, applicant.headers, leave["id"], _pdf()).json()
    assert len(list(_tmp_store.iterdir())) == 1

    assert client.delete(
        f"{LEAVES}/{leave['id']}/attachments/{row['id']}", headers=applicant.headers
    ).status_code == 204
    assert list(_tmp_store.iterdir()) == [], "the bytes went with the row, not after it"
    assert client.get(f"{LEAVES}/{leave['id']}/attachments", headers=applicant.headers).json() == []

    # The Main Admin is the second door, because an uploader's account can be
    # disabled or purged and an attachment nobody can remove is a file nobody
    # can remove.
    again = _attach(client, applicant.headers, leave["id"], _pdf()).json()
    assert client.delete(
        f"{LEAVES}/{leave['id']}/attachments/{again['id']}", headers=office.headers
    ).status_code == 204
