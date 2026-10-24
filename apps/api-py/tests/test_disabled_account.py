"""Disabling an account, from the student's side and the office's (2026-10-17).

The owner asked three things, and this module holds the two that are about an
account rather than a screen:

1. THE MAIN ADMIN DISABLES AND ENABLES A STUDENT, AND NOBODY ELSE DOES. The
   endpoints were always `require_admin`; the student record had no button for
   them. A student has no 90-day window: "create a new account instead" means
   nothing for one, whose account is their whole record.
2. THE PERSON IS TOLD WHY, AT EVERY DOOR. A disabled account hears that it was
   disabled for security reasons and to contact the placement office (Main
   Admin) at the password door, and its open session's 401 carries
   `X-Reep-Session: disabled` so the login screen can say the same. A REMOVED
   account hears neither: nobody told it about a security issue.

The Google door's `sso_disabled` is in test_google_callback.py, beside the
other codes; the third ask, the Access tab, is in test_student_record_access.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import TEST_PASSWORD, requires_db

from app import security
from app.db import SessionLocal
from app.models.user import Role, User
from app.routers import auth as auth_router

USERS = "/api/admin/users"


def _sign_in(client, email: str):
    auth_router._login_failures.clear()
    response = client.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
    client.cookies.clear()
    return response


def _forget_cached_state(user_id: str) -> None:
    """A row edited behind the app's back is re-read, the way a second worker
    would already see it (test_faculty_lifecycle's chokepoint test)."""
    with security._version_lock:
        security._version_cache.pop(user_id, None)


@requires_db
def test_the_office_disables_a_student_and_they_are_told_why(client, make_user):
    admin = make_user("dis-admin", Role.ADMIN)
    faculty = make_user("dis-faculty", Role.MENTOR)
    student = make_user("dis-student", Role.STUDENT)
    assert client.get("/api/auth/me", headers=student.headers).status_code == 200

    # Nobody but the Main Admin: not a faculty member, not the student.
    for headers in (faculty.headers, student.headers):
        refused = client.post(
            f"{USERS}/{student.user_id}/disable", headers=headers, json={"reason": "not mine to do"}
        )
        assert refused.status_code == 403, refused.text

    out = client.post(
        f"{USERS}/{student.user_id}/disable",
        headers=admin.headers,
        json={"reason": "Password shared with another student"},
    )
    assert out.status_code == 200, out.text
    assert out.json()["disabled"] is True

    # The session they had open ends, and says why.
    me = client.get("/api/auth/me", headers=student.headers)
    assert me.status_code == 401
    assert me.headers.get(security.SESSION_RETIRED_HEADER) == security.SESSION_DISABLED_VALUE

    # The password door says it in words, and never the office's reason.
    barred = _sign_in(client, student.email)
    assert barred.status_code == 403, barred.text
    detail = barred.json()["detail"]
    assert detail == auth_router.DISABLED_SIGN_IN_MESSAGE
    assert "security" in detail and "placement office" in detail
    assert "Password shared" not in detail

    # No 90-day window for a student: the office brings them back when the
    # issue is resolved, however long that took.
    with SessionLocal() as db:
        db.get(User, student.user_id).disabled_at = datetime.now(timezone.utc) - timedelta(days=200)
        db.commit()
    back = client.post(f"{USERS}/{student.user_id}/enable", headers=admin.headers)
    assert back.status_code == 200, back.text
    assert back.json()["disabled"] is False
    assert "Governance" not in back.json()["detail"], "a student holds no grants to restore"
    assert _sign_in(client, student.email).status_code == 200


@requires_db
def test_a_removed_account_hears_the_roster_sentence_and_no_security_issue(client, make_user):
    student = make_user("rem-student", Role.STUDENT)
    with SessionLocal() as db:
        db.get(User, student.user_id).deleted_at = datetime.now(timezone.utc)
        db.commit()
    _forget_cached_state(student.user_id)

    me = client.get("/api/auth/me", headers=student.headers)
    assert me.status_code == 401
    assert me.headers.get(security.SESSION_RETIRED_HEADER) is None, (
        "a removed account was told its account was disabled for security reasons"
    )

    barred = _sign_in(client, student.email)
    assert barred.status_code == 403, barred.text
    assert barred.json()["detail"] == auth_router.REMOVED_SIGN_IN_MESSAGE


@requires_db
def test_an_expired_or_retired_cookie_is_not_called_disabled(client, make_user):
    """The header is a statement about the ACCOUNT, so it is never sent for a
    cookie that merely lost to a newer sign-in, or for no cookie at all."""
    faculty = make_user("ret-faculty", Role.MENTOR)
    _sign_in(client, faculty.email)  # retires the cookie make_user holds

    retired = client.get("/api/auth/me", headers=faculty.headers)
    assert retired.status_code == 401
    assert retired.headers.get(security.SESSION_RETIRED_HEADER) == security.SESSION_RETIRED_VALUE

    anonymous = client.get("/api/auth/me")
    assert anonymous.status_code == 401
    assert anonymous.headers.get(security.SESSION_RETIRED_HEADER) is None
