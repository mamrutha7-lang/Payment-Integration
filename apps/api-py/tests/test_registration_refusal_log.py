"""A refused registration says WHICH box in the log, and never what was in it.

About seventy POST /api/register calls on 2026-10-01 were refused with 422 in
two milliseconds and nothing on the server said why: the reason went back to
the phone and nowhere else. app/main.py's validation handler now logs the field
names and pydantic's error types for this one route, and `submit` logs a reason
code for the two refusals it raises itself. These pin both halves, and that no
value an applicant typed reaches the line.
"""

import logging
import uuid

from conftest import application_files

from app.routers import registration as registration_router

PHONE = "+91 98111 22233"
PERSONAL = "very.private.person@gmail.com"


def _post(client, **overrides):
    registration_router._rate_windows.clear()
    data = {
        "name": "Refusal Log",
        "email": f"refusal.{uuid.uuid4().hex[:8]}@bgscet.ac.in",
        "usn": "1BG26RLG01",
        "phone": PHONE,
        "personal_email": PERSONAL,
        "linkedin_url": "https://www.linkedin.com/in/refusal-log",
        "degree_level": "PG",
    }
    data.update(overrides)
    data = {k: v for k, v in data.items() if v is not None}
    return client.post("/api/register", data=data, files=application_files())


def test_a_schema_refusal_names_the_fields_and_not_their_values(client, caplog):
    caplog.set_level(logging.INFO, logger="app.routers.registration")
    r = _post(client, linkedin_url="instagram.com/someone", usn=None)
    assert r.status_code == 422, r.text
    lines = [m for m in caplog.messages if "refused (422, schema)" in m]
    assert lines, "the refusal must reach the log"
    assert "linkedin_url:value_error" in lines[-1]
    assert "usn:missing" in lines[-1]
    for secret in ("instagram.com/someone", PHONE, PERSONAL):
        assert secret not in "\n".join(caplog.messages), f"{secret!r} leaked into the log"


def test_the_college_email_refusals_log_a_reason_code_only(client, caplog):
    caplog.set_level(logging.INFO, logger="app.routers.registration")
    r = _post(client, email=PERSONAL)
    assert r.status_code == 422, r.text
    assert any("email:same_as_personal" in m for m in caplog.messages)
    assert PERSONAL not in "\n".join(caplog.messages)

    caplog.clear()
    r = _post(client, email="someone.else@gmail.com")
    assert r.status_code == 422, r.text
    assert any("email:public_mail_domain" in m for m in caplog.messages)
    assert "someone.else@gmail.com" not in "\n".join(caplog.messages)


def test_other_routes_keep_fastapis_own_422(client, caplog):
    caplog.set_level(logging.INFO, logger="app.routers.registration")
    r = client.post("/api/auth/login", json={})
    assert r.status_code == 422
    assert not [m for m in caplog.messages if "refused (422" in m]
