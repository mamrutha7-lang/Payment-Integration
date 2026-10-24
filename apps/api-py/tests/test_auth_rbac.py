"""Auth + role-scope enforcement, end-to-end via TestClient against the seeded
dev DB. The mentorScope() rule (staff-only, director-only) is the security spine
of the whole app, so it gets first-class coverage.

Paths are under /api — the whole domain surface is mounted there so the Angular
client and its dev proxy reach it uniformly.
"""

from conftest import requires_db


@requires_db
def test_login_success_sets_session_cookie(client):
    r = client.post("/api/auth/login", json={"email": "student@bgscet.ac.in", "password": "student123"})
    assert r.status_code == 200
    assert "reep_session" in r.headers.get("set-cookie", "")
    assert r.json()["role"] == "STUDENT"


@requires_db
def test_login_wrong_password_401(client):
    r = client.post("/api/auth/login", json={"email": "student@bgscet.ac.in", "password": "nope"})
    assert r.status_code == 401


@requires_db
def test_me_reflects_session(client, login):
    h = login("admin@bgscet.ac.in", "admin123")
    r = client.get("/api/auth/me", headers=h)
    assert r.status_code == 200
    # The seeded office account is the Main Admin. There is no DIRECTOR any more
    # — see tests/test_no_director_privilege.py.
    assert r.json()["role"] == "ADMIN"


@requires_db
def test_student_can_read_own_dashboard(client, login):
    h = login("student@bgscet.ac.in", "student123")
    assert client.get("/api/student/dashboard", headers=h).status_code == 200


@requires_db
def test_student_forbidden_from_mentor_area(client, login):
    h = login("student@bgscet.ac.in", "student123")
    assert client.get("/api/mentor/mentees", headers=h).status_code == 403


@requires_db
def test_student_forbidden_from_director_area(client, login):
    # B8.4 DELETED `/api/admin/overview`, which this used to ask for, and these
    # two tests were RE-POINTED rather than removed: they are the only place in
    # this module where a STUDENT is proved out of the admin surface, and
    # deleting them would have traded a dead endpoint for a live hole in the
    # coverage. `/analytics-summary` is the surviving equivalent — the same
    # programme aggregates, through `scope_filter`, with the scope header.
    h = login("student@bgscet.ac.in", "student123")
    assert client.get("/api/admin/analytics-summary", headers=h).status_code == 403


@requires_db
def test_mentor_forbidden_from_director_only(client, login):
    # A MENTOR is staff but not a director: reads mentees, blocked from director config.
    h = login("mentor@bgscet.ac.in", "mentor123")
    assert client.get("/api/mentor/mentees", headers=h).status_code == 200
    assert client.get("/api/admin/alert-rules", headers=h).status_code == 403


@requires_db
def test_director_can_read_overview(client, login):
    # Re-pointed by B8.4 — see the note on the student half above.
    h = login("admin@bgscet.ac.in", "admin123")
    assert client.get("/api/admin/analytics-summary", headers=h).status_code == 200


@requires_db
def test_unauthenticated_is_rejected(client):
    # No cookie → the session dependency refuses (this app's convention is 403).
    assert client.get("/api/student/dashboard").status_code in (401, 403)


@requires_db
def test_resume_generate_respects_egress_gate(client, login, monkeypatch):
    """A REMOTE provider with the flag off must compose deterministically.

    This pins the provider instead of reading whatever LLM_BASE_URL the machine
    happens to carry. It used to assert `used_ai is False` against the ambient
    .env, which quietly meant "this developer is pointed at a remote model" --
    so it passed on Gemini and FAILED the day the deployment moved to a local
    model on loopback, which is the one case the gate exists to ALLOW. The
    assertion was true for the wrong reason, and the failure named the gate
    rather than the config that had actually changed.
    """
    from app.config import settings

    h = login("student@bgscet.ac.in", "student123")
    monkeypatch.setattr(settings, "llm_base_url", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(settings, "llm_allow_remote_student_data", "")
    r = client.post("/api/student/resume/generate", headers=h, json={"title": "T"})
    assert r.status_code == 200
    assert r.json()["used_ai"] is False


@requires_db
def test_the_leave_queue_is_the_main_admins_alone(client, login, make_user):
    """The leave queue is the Main Admin's (2026-09-16): a MENTOR sees NOBODY
    and cannot sign for anybody, whether or not they have a group.

    Leave is the surface where getting this wrong hurts most — `reason` is free
    text and is routinely medical or personal — and it is the one place that
    gated on require_mentor alone until the 2026-08 audit.
    """
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.leave import LeaveRequest
    from app.models.user import Role

    student_h = login("student@bgscet.ac.in", "student123")
    client.cookies.clear()  # explicit Cookie headers only; the jar would override them
    r = client.post(
        "/api/leaves",
        headers=student_h,
        json={"from_date": "2026-09-01", "to_date": "2026-09-02", "reason": "RBAC probe"},
    )
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]

    try:
        # The seeded mentor DOES have the seeded student in their group, and is
        # refused anyway: a mentor group is a claim over students' records, not
        # over their leave, since 2026-09-16.
        grouped_h = login("mentor@bgscet.ac.in", "mentor123")
        client.cookies.clear()
        assert client.get("/api/leaves/pending", headers=grouped_h).status_code == 403
        admin_h = login("admin@bgscet.ac.in", "admin123")
        client.cookies.clear()
        rows = client.get("/api/leaves/pending", headers=admin_h).json()
        assert leave_id in [row["id"] for row in rows]

        # A MENTOR with no Mentor group: not "the whole programme", not one row.
        #
        # THE REFUSAL MOVED ONE GATE EARLIER AND THE PROPERTY IS UNCHANGED.
        # This asserted `== []` and a 404, which is what the SQL scope rule
        # produced: the account was admitted by `require_mentor`, narrowed to
        # its own (empty) group, and handed an empty queue. The approver's
        # endpoints are `require_admin` since 2026-09-16 (leave approval is
        # the Main Admin's alone), so this account is refused before the
        # query runs — the same outcome with the true reason attached, where
        # `200 []` said nothing at all.
        loner = make_user("nogroup", Role.MENTOR)
        assert client.get("/api/leaves/pending", headers=loner.headers).status_code == 403
        # ...and no signature either. The anti-oracle property this line has
        # always guarded is INTACT and is now asserted directly rather than
        # implied: the refusal is decided before any id is looked up, so a real
        # leave id and an invented one come back identical. (It is a 403 rather
        # than the old flattened 404 because the caller never got as far as the
        # row; `_assert_can_decide`'s 404-flattening still governs everyone who
        # does hold the capability.)
        decision = client.post(
            f"/api/leaves/{leave_id}/decision",
            headers=loner.headers,
            json={"decision": "APPROVE"},
        )
        assert decision.status_code == 403, decision.text
        invented = client.post(
            "/api/leaves/not-a-real-leave/decision",
            headers=loner.headers,
            json={"decision": "APPROVE"},
        )
        assert (invented.status_code, invented.json()) == (
            decision.status_code,
            decision.json(),
        ), "the refusal distinguishes a real leave id from an invented one"
    finally:
        with SessionLocal() as db:
            db.execute(delete(LeaveRequest).where(LeaveRequest.id == leave_id))
            db.commit()
