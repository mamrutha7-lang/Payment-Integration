import pytest
from fastapi import HTTPException

from app.architecture_events import request_hash, require_idempotency_key
from app.policies import STAFF_ROLES, student_identity


def test_missing_idempotency_key_is_rejected_for_commands():
    with pytest.raises(HTTPException) as error:
        require_idempotency_key(None)
    assert error.value.status_code == 400


def test_student_identity_rejects_staff_session():
    with pytest.raises(HTTPException) as error:
        student_identity({"role": "MENTOR", "userId": "u", "mentorId": "m"})
    assert error.value.status_code == 403


def test_request_hash_is_order_independent():
    assert request_hash({"b": 2, "a": 1}) == request_hash({"a": 1, "b": 2})


def test_student_identity_requires_session_owned_student():
    assert student_identity({"role": "STUDENT", "userId": "u", "studentId": "s"}) == "s"


def test_role_sets_are_explicit():
    # DIRECTOR is deliberately absent (2026-09-10). It was a second Main Admin
    # under another name and now grants nothing; see app/policies.py and
    # tests/test_no_director_privilege.py.
    assert STAFF_ROLES == {"MENTOR", "ADMIN"}
    assert "STUDENT" not in STAFF_ROLES
    assert "DIRECTOR" not in STAFF_ROLES
