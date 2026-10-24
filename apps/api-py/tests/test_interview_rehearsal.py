"""The Main Admin's rehearsal: the interview with no record (2026-09-16).

`_is_rehearsal` in app/routers/interview.py admits the one office account to
the mock interviewer so it can hear the persona it deploys — and the whole
mechanism is that `_open_records`, the ONE function that writes the
conversation, the `interview_sessions` row and the consent check, is not
called. These tests pin the gate from the outside, through the status probe
the client consults before it opens the socket, and the shape of the flag
from the inside.
"""

from __future__ import annotations

import inspect

import pytest

from app.models.user import Role
from app.routers import interview as interview_router
from tests.conftest import requires_db


class TestTheFlag:
    def test_only_the_main_admin_rehearses(self):
        assert interview_router._is_rehearsal({"role": Role.ADMIN.value}) is True
        for role in (Role.STUDENT, Role.MENTOR, Role.ALUMNI):
            assert interview_router._is_rehearsal({"role": role.value}) is False
        assert interview_router._is_rehearsal({}) is False

    def test_the_rehearsal_never_opens_records(self):
        """The rehearsal branch returns BEFORE `_open_records` and hands the
        relay every writer as None. Read from the source, because the socket
        cannot be driven in this suite: the branch must sit between the
        specialization check and the `try` that opens the records, and must
        carry no writer."""
        src = inspect.getsource(interview_router.interview)
        branch_at = src.index("if rehearsal:")
        open_at = src.index("_open_records,")
        assert branch_at < open_at, "the rehearsal must be decided before any record is opened"
        branch = src[branch_at:open_at]
        for hook in ("on_turn=None", "on_report=None", "on_finalize=None", "on_heartbeat=None", "recorder=None"):
            assert hook in branch, hook
        assert "interview_session_id=None" in branch

    def test_the_backstop_skips_a_rehearsal(self):
        """Layer 2 closes `interview_sessions` rows; a rehearsal has none."""
        src = inspect.getsource(interview_router._run_relay)
        assert "if interview_session_id is None:" in src
        assert src.index("if interview_session_id is None:") < src.index("_finalize_if_running")


@requires_db
class TestTheStatusProbe:
    """GET /api/interview/status is the one place a non-student learns why the
    socket would refuse; for the Main Admin it now says the opposite."""

    def test_the_main_admin_is_not_told_it_is_a_student_feature(self, client, login):
        r = client.get(
            "/api/interview/status",
            headers=login("admin@bgscet.ac.in", "admin123"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rehearsal"] is True
        # Whether it is AVAILABLE depends on the engine being configured on
        # this machine; what must never come back is the role refusal.
        assert body["reason"] != "Mock interviews are a student feature."

    def test_a_mentor_is_still_refused(self, client, login):
        r = client.get(
            "/api/interview/status",
            headers=login("mentor@bgscet.ac.in", "mentor123"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rehearsal"] is False
        assert body["available"] is False
        assert body["reason"] == "Mock interviews are a student feature."

    def test_a_student_is_not_a_rehearsal(self, client, login):
        r = client.get(
            "/api/interview/status",
            headers=login("student@bgscet.ac.in", "student123"),
        )
        assert r.status_code == 200, r.text
        assert r.json()["rehearsal"] is False
