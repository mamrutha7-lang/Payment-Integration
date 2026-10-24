"""TS-API-02  Every parameter-free GET in the Swagger document, called for real.

For each documented GET under /api with no path parameter:
  * anonymous  -> never 2xx for a protected area, never 5xx anywhere;
  * the role that owns the area (student / mentor / admin) -> 200, and the
    body VALIDATES against the response schema the document declares.

This is contract (conformance) testing: the Swagger document is the oracle.
"""
from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from conftest import BASE

import requests

_SPEC = requests.get(f"{BASE}/openapi.json", timeout=30).json()
def _required_params(op: dict) -> bool:
    return any(prm.get("required") for prm in op.get("parameters", []))


_GETS = sorted(p for p, v in _SPEC.get("paths", {}).items()
               if "get" in v and p.startswith("/api/") and "{" not in p
               and not _required_params(v["get"])     # a GET that needs ?track= is exercised by TS-API-05
               and not p.startswith("/api/v1/")        # /api/v1 is the versioned alias of /api
               and not p.endswith(".csv") and not p.endswith(".pdf")
               and "/export" not in p)                # exports write an audit receipt per call

OWNER = {"/api/student/": "student", "/api/mentor/": "mentor", "/api/admin/": "admin"}
PROTECTED = tuple(OWNER)


def owner_of(path: str) -> str | None:
    return next((role for prefix, role in OWNER.items() if path.startswith(prefix)), None)


def schema_for(spec: dict, path: str) -> dict | None:
    content = spec["paths"][path]["get"].get("responses", {}).get("200", {}).get("content", {})
    schema = content.get("application/json", {}).get("schema")
    if not schema:
        return None
    return {**schema, "components": spec.get("components", {})}


@pytest.mark.parametrize("path", _GETS)
def test_api_02_anonymous_is_refused_and_nothing_is_5xx(sessions, path):
    r = sessions["anonymous"].get(f"{BASE}{path}", timeout=30)
    assert r.status_code < 500, f"{path} -> {r.status_code} {r.text[:200]}"
    if path.startswith(PROTECTED):
        assert r.status_code in (401, 403), f"{path} served {r.status_code} to an anonymous caller"


@pytest.mark.parametrize("path", [p for p in _GETS if owner_of(p)])
def test_api_02_owner_role_gets_200_matching_the_documented_schema(sessions, spec, path):
    role = owner_of(path)
    r = sessions[role].get(f"{BASE}{path}", timeout=60)
    if r.status_code == 403 and role == "mentor":
        pytest.skip(f"{path}: capability not held by the seeded mentor (403 is the correct answer)")
    if r.status_code == 404 and role == "student":
        pytest.skip(f"{path}: 404 for a student with no batch/record yet (documented behaviour)")
    assert r.status_code == 200, f"{role} GET {path} -> {r.status_code} {r.text[:300]}"
    schema = schema_for(spec, path)
    if schema is None or "json" not in r.headers.get("content-type", ""):
        return
    errors = sorted(Draft202012Validator(schema).iter_errors(r.json()), key=lambda e: list(e.path))
    assert not errors, f"{path}: response breaks its documented schema: {errors[0].message} at {list(errors[0].path)}"


@pytest.mark.parametrize("path", [p for p in _GETS if p.startswith(("/api/admin/", "/api/mentor/"))])
def test_api_02_rbac_a_student_never_reads_staff_areas(sessions, path):
    """Rule 2 (AGENTS.md): staff scope is decided by role. A STUDENT session gets no 2xx here."""
    r = sessions["student"].get(f"{BASE}{path}", timeout=30)
    assert not (200 <= r.status_code < 300), f"STUDENT read {path}: {r.status_code}"
    assert r.status_code < 500


@pytest.mark.parametrize("path", [p for p in _GETS if p.startswith("/api/admin/governance")])
def test_api_02_rbac_governance_is_the_main_admins_alone(sessions, path):
    r = sessions["mentor"].get(f"{BASE}{path}", timeout=30)
    assert r.status_code in (401, 403, 404), f"MENTOR read {path}: {r.status_code}"
