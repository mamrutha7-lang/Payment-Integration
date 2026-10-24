"""Shared fixtures for the REEP API test suite (Swagger / OpenAPI driven).

Runs against a LIVE API (default http://localhost:3300, override REEP_API).
The contract under test is the API's own OpenAPI 3.1 document at
/openapi.json - the same document Swagger UI renders at /docs.

Accounts: the dev seed's mentor and Main Admin, and two RESERVED load-test
students (loadtest101/102, made by testing/tools/create_load_users.py and never
written to the JMeter CSV). REEP keeps one live session per account, so a
functional run signing in as a JMeter virtual user retires that user's session
mid-test - which is exactly what happened on the first load run.
"""
from __future__ import annotations

import os

import pytest
import requests

BASE = os.environ.get("REEP_API", "http://localhost:3300").rstrip("/")
LOAD_PASSWORD = "LoadTest#2026"

ACCOUNTS = {
    "student": ("loadtest101@bgscet.ac.in", LOAD_PASSWORD),
    "student_b": ("loadtest102@bgscet.ac.in", LOAD_PASSWORD),
    "mentor": ("mentor@bgscet.ac.in", "mentor123"),
    "admin": ("admin@bgscet.ac.in", "admin123"),
}


def login(role: str) -> requests.Session:
    email, password = ACCOUNTS[role]
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"BLOCKED: cannot sign in as {role} ({email}): {r.status_code} {r.text[:200]}")
    return s


@pytest.fixture(scope="session")
def base() -> str:
    try:
        requests.get(f"{BASE}/health", timeout=5).raise_for_status()
    except Exception as exc:  # environment, not product: say so
        pytest.fail(f"BLOCKED: API not reachable at {BASE}: {exc}")
    return BASE


@pytest.fixture(scope="session")
def spec(base) -> dict:
    return requests.get(f"{base}/openapi.json", timeout=30).json()


@pytest.fixture(scope="session")
def sessions(base) -> dict[str, requests.Session]:
    out = {role: login(role) for role in ("student", "mentor", "admin")}
    out["anonymous"] = requests.Session()
    return out
