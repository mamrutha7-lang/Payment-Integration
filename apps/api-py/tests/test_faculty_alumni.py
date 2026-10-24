"""Faculty upskilling + alumni area — ownership, role gates and the first-login
profile flow.

Everything here is @requires_db (real users, real rows). The rules pinned:

  * /staff/upskilling admits staff only, and each staff member sees ONLY their
    own certificates — a second mentor's shelf is not readable, downloadable or
    deletable through another account.
  * /alumni/* admits ALUMNI only: a student gets 403, and an alumnus gets 403
    from the staff shelf — neither role leaks into the other's surface.
  * The first-login contract: GET /alumni/profile answers created:false until a
    profile exists; creating one REQUIRES a resume; updating without a file
    keeps the resume on record.
"""

from conftest import requires_db

from app.models.user import Role

# Smallest thing document_store's magic-byte sniff accepts as a PDF.
_PDF = b"%PDF-1.4\n%%EOF\n"


def _upload_cert(client, headers, title="Test Cert", provider="Coursera"):
    return client.post(
        "/api/staff/upskilling",
        headers=headers,
        files={"file": ("cert.pdf", _PDF, "application/pdf")},
        data={"title": title, "provider": provider, "completed_on": "2026-06-30"},
    )


@requires_db
def test_student_cannot_reach_the_staff_shelf(client, make_user):
    student = make_user("upsk-stud", Role.STUDENT)
    assert client.get("/api/staff/upskilling", headers=student.headers).status_code == 403
    assert _upload_cert(client, student.headers).status_code == 403


@requires_db
def test_staff_upload_list_download_delete_roundtrip(client, make_user):
    mentor = make_user("upsk-mentor", Role.MENTOR)

    r = _upload_cert(client, mentor.headers)
    assert r.status_code == 201, r.text
    cert = r.json()
    assert cert["title"] == "Test Cert"
    assert cert["provider"] == "Coursera"
    assert cert["completed_on"] == "2026-06-30"
    assert cert["mime_type"] == "application/pdf"

    listed = client.get("/api/staff/upskilling", headers=mentor.headers).json()
    assert [c["id"] for c in listed] == [cert["id"]]

    dl = client.get(f"/api/staff/upskilling/{cert['id']}/file", headers=mentor.headers)
    assert dl.status_code == 200
    assert dl.content == _PDF

    assert (
        client.delete(f"/api/staff/upskilling/{cert['id']}", headers=mentor.headers).status_code
        == 204
    )
    assert client.get("/api/staff/upskilling", headers=mentor.headers).json() == []


@requires_db
def test_a_certificate_is_invisible_to_other_staff(client, make_user):
    owner = make_user("upsk-owner", Role.MENTOR)
    other = make_user("upsk-other", Role.MENTOR)

    cert = _upload_cert(client, owner.headers).json()
    try:
        # Not in the other mentor's list, and 404 (not 403) on direct access, so
        # an id is never confirmed to exist for someone it does not belong to.
        assert client.get("/api/staff/upskilling", headers=other.headers).json() == []
        assert (
            client.get(
                f"/api/staff/upskilling/{cert['id']}/file", headers=other.headers
            ).status_code
            == 404
        )
        assert (
            client.delete(f"/api/staff/upskilling/{cert['id']}", headers=other.headers).status_code
            == 404
        )
    finally:
        client.delete(f"/api/staff/upskilling/{cert['id']}", headers=owner.headers)


@requires_db
def test_non_pdf_bytes_are_refused(client, make_user):
    mentor = make_user("upsk-badfile", Role.MENTOR)
    r = client.post(
        "/api/staff/upskilling",
        headers=mentor.headers,
        files={"file": ("cert.pdf", b"MZ not a pdf", "application/pdf")},
        data={"title": "Nope"},
    )
    assert r.status_code == 422


@requires_db
def test_alumni_area_is_alumni_only(client, make_user):
    student = make_user("alum-stud", Role.STUDENT)
    mentor = make_user("alum-mentor", Role.MENTOR)
    for headers in (student.headers, mentor.headers):
        assert client.get("/api/alumni/profile", headers=headers).status_code == 403
        assert client.get("/api/alumni/jobs", headers=headers).status_code == 403

    alum = make_user("alum-cross", Role.ALUMNI)
    assert client.get("/api/staff/upskilling", headers=alum.headers).status_code == 403
    assert client.get("/api/student/profile", headers=alum.headers).status_code == 403


@requires_db
def test_first_login_profile_flow(client, make_user):
    alum = make_user("alum-flow", Role.ALUMNI)

    # Before creation: created is false and nothing else is claimed.
    prof = client.get("/api/alumni/profile", headers=alum.headers).json()
    assert prof["created"] is False
    assert prof["company"] is None

    # Creating WITHOUT a resume is refused — company + current resume is the flow.
    r = client.post("/api/alumni/profile", headers=alum.headers, data={"company": "Infosys"})
    assert r.status_code == 422

    # Create with company + resume.
    r = client.post(
        "/api/alumni/profile",
        headers=alum.headers,
        data={"company": "Infosys", "designation": "Analyst", "graduation_year": "2025"},
        files={"resume": ("cv.pdf", _PDF, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    prof = r.json()
    assert prof["created"] is True
    assert prof["company"] == "Infosys"
    assert prof["graduation_year"] == 2025
    assert prof["resume"]["original_name"] == "cv.pdf"

    # The resume downloads back byte-for-byte.
    dl = client.get("/api/alumni/profile/resume", headers=alum.headers)
    assert dl.status_code == 200
    assert dl.content == _PDF

    # Updating without a file keeps the resume on record.
    r = client.post(
        "/api/alumni/profile", headers=alum.headers, data={"company": "Wipro"}
    )
    assert r.status_code == 200
    prof = r.json()
    assert prof["company"] == "Wipro"
    assert prof["resume"]["original_name"] == "cv.pdf"


@requires_db
def test_alumni_jobs_sheet_lists_postings_without_student_verdicts(client, make_user):
    alum = make_user("alum-jobs", Role.ALUMNI)
    r = client.get("/api/alumni/jobs", headers=alum.headers)
    assert r.status_code == 200
    rows = r.json()
    # The seeded dev DB carries postings; whatever is there, the row shape is
    # the public sheet — no match_percent / eligible fields to mis-read.
    for row in rows:
        assert "title" in row and "company" in row
        assert "match_percent" not in row and "eligible" not in row


@requires_db
def test_alumni_password_login_mints_a_plain_session(client, make_user):
    """No studentId/mentorId claims ride on an ALUMNI session."""
    alum = make_user("alum-claims", Role.ALUMNI)
    me = client.get("/api/auth/me", headers=alum.headers)
    assert me.status_code == 200
    body = me.json()
    assert body["role"] == "ALUMNI"
    assert "studentId" not in body or body["studentId"] is None
    assert "mentorId" not in body or body["mentorId"] is None
