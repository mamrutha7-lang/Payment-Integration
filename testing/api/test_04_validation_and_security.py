"""TS-API-04  Input validation (negative testing) and HTTP-level security.

Business rules exercised through the API, each by its boundary:
  * a leave request whose dates run backwards is refused (LeaveIn.dates_run_forwards);
  * from == to is LEGAL (a one-day leave / a permission slip);
  * leave_kind is a closed list; reason is 1..2000 characters;
  * the public registration form refuses a request without its CV and photo.
Security: response headers, CORS for a foreign origin, OWASP API1 (BOLA) probe.
"""
import datetime as dt

import requests

from conftest import BASE

TODAY = dt.date.today()


def leave(**over):
    body = {"from_date": str(TODAY + dt.timedelta(days=30)), "to_date": str(TODAY + dt.timedelta(days=31)),
            "reason": "API test - validation probe", "leave_kind": "CASUAL"}
    body.update(over)
    return body


def test_api_04_001_leave_dates_running_backwards_are_422(sessions):
    r = sessions["student"].post(f"{BASE}/api/leaves", json=leave(from_date=str(TODAY + dt.timedelta(days=40)),
                                                                  to_date=str(TODAY + dt.timedelta(days=30))), timeout=30)
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], list)


def test_api_04_002_leave_reason_boundaries(sessions):
    s = sessions["student"]
    assert s.post(f"{BASE}/api/leaves", json=leave(reason=""), timeout=30).status_code == 422          # 0 chars
    assert s.post(f"{BASE}/api/leaves", json=leave(reason="x" * 2001), timeout=30).status_code == 422  # max + 1


def test_api_04_003_leave_kind_is_a_closed_list(sessions):
    r = sessions["student"].post(f"{BASE}/api/leaves", json=leave(leave_kind="HOLIDAY"), timeout=30)
    assert r.status_code == 422


def test_api_04_004_one_day_leave_is_accepted_then_withdrawn(sessions):
    """Positive partner of 04_001: from == to is valid. The request is withdrawn afterwards."""
    day = str(TODAY + dt.timedelta(days=60))
    s = sessions["student"]
    r = s.post(f"{BASE}/api/leaves", json=leave(from_date=day, to_date=day, reason="x"), timeout=30)
    assert r.status_code in (200, 201), r.text
    leave_id = r.json()["id"]
    mine = s.get(f"{BASE}/api/leaves/mine", timeout=30)
    if mine.status_code == 200:
        assert any(x["id"] == leave_id for x in mine.json())
    w = s.post(f"{BASE}/api/leaves/{leave_id}/withdraw", timeout=30)
    assert w.status_code in (200, 204, 404, 405), w.text


def test_api_04_005_register_without_files_is_refused(base):
    r = requests.post(f"{BASE}/api/register", data={"name": "Probe", "email": "probe@bgscet.ac.in"}, timeout=30)
    assert r.status_code == 422


def test_api_04_006_security_headers_on_every_response(base):
    for path in ("/health", "/api/auth/sso/status", "/api/student/dashboard"):
        r = requests.get(f"{BASE}{path}", timeout=10)
        assert r.headers.get("x-content-type-options") == "nosniff", path
        assert r.headers.get("x-frame-options") == "DENY", path
        assert r.headers.get("x-request-id"), f"{path}: no X-Request-ID for traceability"


def test_api_04_007_cors_does_not_trust_a_foreign_origin(base):
    r = requests.options(f"{BASE}/api/auth/me", headers={"Origin": "https://evil.example",
                                                          "Access-Control-Request-Method": "GET"}, timeout=10)
    assert r.headers.get("access-control-allow-origin") != "https://evil.example"
    assert r.headers.get("access-control-allow-origin") != "*"


def test_api_04_008_bola_a_student_cannot_read_another_students_record(sessions):
    """OWASP API1:2023 Broken Object Level Authorization."""
    me = sessions["student"].get(f"{BASE}/api/auth/me", timeout=10).json()
    other = sessions["admin"].get(f"{BASE}/api/admin/students", timeout=30)
    assert other.status_code == 200
    rows = other.json() if isinstance(other.json(), list) else other.json().get("rows", [])
    target = next((row["student_id"] for row in rows if row.get("student_id") and row["student_id"] != me.get("studentId")), None)
    assert target, "no second student to probe"
    for path in (f"/api/admin/students/{target}/360", f"/api/mentor/students/{target}/ledger",
                 f"/api/mentor/uploads/{target}/file"):
        r = sessions["student"].get(f"{BASE}{path}", timeout=30)
        assert r.status_code in (401, 403, 404), (path, r.status_code)


def test_api_04_009_sql_injection_strings_are_data_not_code(sessions):
    """A classic payload in a query/body value must come back as a 4xx or as data - never a 500."""
    s = sessions["admin"]
    for payload in ("' OR '1'='1", "1; DROP TABLE users;--", "\" OR \"\"=\""):
        r = s.get(f"{BASE}/api/admin/students", params={"q": payload}, timeout=30)
        assert r.status_code < 500, (payload, r.status_code)
        r = requests.post(f"{BASE}/api/auth/login", json={"email": payload, "password": payload}, timeout=30)
        assert r.status_code in (401, 422, 429), (payload, r.status_code)
    assert s.get(f"{BASE}/api/admin/students", timeout=30).status_code == 200  # the table is still there
