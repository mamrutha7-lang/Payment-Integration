"""The leave paper and the signature it carries.

Pinned: a staff member uploads ONE signature image (PNG or JPEG, replaced in
place, a PDF refused, a student refused); the paper downloads for the
applicant and for the staff who could decide it, with the same 404 for
everyone else; and an uploaded signature is drawn into it - the bytes grow
when the image is on file, for the applicant's blocks and for the sanctioning
approver's. The leave form's own endpoints are used as they are and not
touched.
"""

from __future__ import annotations

import io
import os
import struct
import zlib
from datetime import date, timedelta

import pytest

from conftest import requires_db

from app import document_store, leave_paper
from app.models.user import Role

SIG = "/api/staff/signature"
LEAVES = "/api/leaves"


def _png(w: int = 120, h: int = 40, noise: bool = False) -> bytes:
    """A real PNG, no Pillow needed to make it. Opaque black by default - which
    compresses to almost nothing - or random pixels, which do not, for a file
    that is genuinely big."""
    raw = b"".join(
        b"\x00" + (os.urandom(w * 4) if noise else bytes([0, 0, 0, 255]) * w) for _ in range(h)
    )

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    return tmp_path


def _leave(client, headers, **extra):
    body = {
        "from_date": date.today().isoformat(),
        "to_date": (date.today() + timedelta(days=1)).isoformat(),
        "reason": "Family function at home.",
        "leave_kind": "CASUAL",
        "credit": "1",
        "alt_name": "Kavya N",
        "alt_rows": [{"date": date.today().isoformat(), "staff_name": "Kavya N", "cls": "MBA-II", "time": "10:00", "remarks": "Swapped"}],
    }
    body.update(extra)
    r = client.post(LEAVES, headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------- the signature --


@requires_db
def test_a_staff_member_uploads_one_signature_and_replaces_it_in_place(client, make_user, _tmp_store):
    faculty = make_user("sig-fac", Role.MENTOR)
    student = make_user("sig-stu")

    assert client.get(SIG, headers=faculty.headers).json()["present"] is False
    assert client.get(f"{SIG}/image", headers=faculty.headers).status_code == 404
    assert client.get(SIG, headers=student.headers).status_code == 403, "students never sign this form"

    # A PDF is a legal upload elsewhere and refused here; nothing is kept.
    r = client.put(SIG, headers=faculty.headers, files={"file": ("sig.pdf", b"%PDF-1.4 not an image", "application/pdf")})
    assert r.status_code == 422 and "PNG or a JPEG" in r.text
    assert list(_tmp_store.iterdir()) == [], "the refused bytes were removed"

    first = _png()
    r = client.put(SIG, headers=faculty.headers, files={"file": ("sig.png", first, "image/png")})
    assert r.status_code == 200, r.text
    # NORMALISED ON THE WAY IN (2026-09-16): what is stored is a flat RGBA PNG
    # re-encoded by Pillow, not the bytes as uploaded, so the size is the
    # stored file's and the served image is a PNG rather than `first` verbatim.
    assert r.json()["present"] is True and r.json()["mime_type"] == "image/png" and r.json()["size_bytes"] > 0
    img = client.get(f"{SIG}/image", headers=faculty.headers)
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/png")
    assert img.content.startswith(b"\x89PNG") and len(img.content) == r.json()["size_bytes"]
    served_first = img.content
    assert len(list(_tmp_store.iterdir())) == 1

    # Replaced in place: one file on disk, the new bytes.
    second = _png(200, 60)
    r = client.put(SIG, headers=faculty.headers, files={"file": ("sig2.png", second, "image/png")})
    assert r.status_code == 200 and r.json()["size_bytes"] > 0
    assert len(list(_tmp_store.iterdir())) == 1
    served_second = client.get(f"{SIG}/image", headers=faculty.headers).content
    assert served_second.startswith(b"\x89PNG") and served_second != served_first

    # Too big is refused before anything is stored.
    r = client.put(SIG, headers=faculty.headers, files={"file": ("big.png", _png(1200, 500, noise=True), "image/png")})
    assert r.status_code == 422 and "2 MB" in r.text

    assert client.delete(SIG, headers=faculty.headers).status_code == 204
    assert client.get(SIG, headers=faculty.headers).json()["present"] is False
    assert list(_tmp_store.iterdir()) == []


# -------------------------------------------------------------- the paper --


@requires_db
def test_the_paper_downloads_for_the_applicant_and_the_office_and_carries_the_signatures(client, make_user, _tmp_store):
    faculty = make_user("lp-fac", Role.MENTOR)
    other_faculty = make_user("lp-other", Role.MENTOR)  # faculty: never an approver
    student = make_user("lp-stu")
    first_approver = make_user("lp-adm", Role.ADMIN)  # the office, whose one signature sanctions

    leave = _leave(client, faculty.headers)
    url = f"{LEAVES}/{leave['id']}/paper.pdf"

    # Who may download: the applicant, and the staff who could decide it.
    r = client.get(url, headers=faculty.headers)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf"), r.text
    assert r.content.startswith(b"%PDF") and "attachment" in r.headers["content-disposition"]
    assert ".pdf" in r.headers["content-disposition"]
    plain = len(r.content)
    assert client.get(url, headers=first_approver.headers).status_code == 200
    assert client.get(url, headers=student.headers).status_code == 403, "not staff: the leave router's own refusal, role first"
    assert client.get(url, headers=other_faculty.headers).status_code == 404
    assert client.get(f"{LEAVES}/no-such-leave/paper.pdf", headers=first_approver.headers).status_code == 404
    assert client.get(url).status_code == 401

    # The applicant's uploaded signature is drawn in: the paper grows.
    assert client.put(SIG, headers=faculty.headers, files={"file": ("sig.png", _png(), "image/png")}).status_code == 200
    with_staff = len(client.get(url, headers=faculty.headers).content)
    assert with_staff > plain, "the image is in the paper"

    # The office sanctions it with ONE signature (the form's own endpoint, untouched).
    assert client.post(f"{LEAVES}/{leave['id']}/decision", headers=first_approver.headers,
                       json={"decision": "APPROVE", "note": "Sanctioned."}).status_code == 200
    sanctioned = len(client.get(url, headers=faculty.headers).content)

    # The sanctioning approver's signature goes into the PROGRAM DIRECTOR block.
    assert client.put(SIG, headers=first_approver.headers, files={"file": ("dir.png", _png(160, 50), "image/png")}).status_code == 200
    with_director = len(client.get(url, headers=faculty.headers).content)
    assert with_director > sanctioned, "the approver's image is in the paper too"

    # Removing the applicant's signature removes it from the paper; the name and time stay.
    assert client.delete(SIG, headers=faculty.headers).status_code == 204
    r = client.get(url, headers=faculty.headers)
    assert r.status_code == 200 and len(r.content) < with_director


# --------------------------------------------------------- the template --


def test_the_paper_is_the_official_form_itself():
    """The download is the college's own PDF with the request written onto
    it, never a redrawn likeness. The template is pinned by size: the field
    coordinates in app/leave_paper.py were measured from this exact file, so
    a replacement with a different layout must fail here, not print a name
    into the wrong box."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from pypdf import PdfReader

    assert leave_paper.TEMPLATE.is_file()
    assert leave_paper.TEMPLATE.stat().st_size == leave_paper.TEMPLATE_BYTES

    leave = SimpleNamespace(
        requester_name="Asha Rao", requester_designation="Assistant Professor", requester_department="MBA",
        from_date=date(2026, 9, 15), to_date=date(2026, 9, 16), reason="Family function.", status="APPROVED",
        leave_kind="CASUAL", credit="2", alt_name="Kavya N",
        alt_rows=[SimpleNamespace(date="2026-09-15", staff_name="Kavya N", cls="MBA-II", time="10:00", remarks="Swapped")],
        signed_at=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc), director_name="Director (seed)",
        director_decided_at=datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc), director_note="Sanctioned.",
    )
    pdf = leave_paper.render_leave_paper_pdf(leave, staff_signature=(_png(), "image/png"))
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (612, 792), "Letter, like the form"
    text = reader.pages[0].extract_text()
    for printed in ("Jai Sri Gurudev", "BGS COLLEGE OF ENGINEERING AND TECHNOLOGY", "Alternate Arrangements", "PROGRAM"):
        assert printed in text, f"the form's own text is there: {printed!r}"
    for written in ("Asha Rao", "Assistant Professor", "Family function.", "Kavya N", "2026-09-15 to 2026-09-16"):
        assert written in text, f"the request is written onto it: {written!r}"
