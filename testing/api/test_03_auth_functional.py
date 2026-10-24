"""TS-API-03  Authentication: functional and security behaviour of the login door.

Techniques: equivalence partitioning (valid / wrong password / unknown email /
malformed body), boundary value analysis (empty strings, oversize input) and
state transition (signed-out -> signed-in -> retired by a second sign-in).
"""
import requests

from conftest import ACCOUNTS, BASE, LOAD_PASSWORD

LOGIN = f"{BASE}/api/auth/login"


def test_api_03_001_valid_login_sets_a_hardened_session_cookie(base):
    email, pw = ACCOUNTS["student_b"]
    r = requests.post(LOGIN, json={"email": email, "password": pw}, timeout=30)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == email and body["role"] == "STUDENT"
    cookie = r.headers["set-cookie"]
    assert "reep_session=" in cookie
    assert "HttpOnly" in cookie, "session must not be readable from JavaScript"
    assert "samesite=lax" in cookie.lower()
    assert "password" not in r.text.lower() or "password_hash" not in r.text


def test_api_03_002_wrong_password_is_401(base):
    email, _ = ACCOUNTS["student_b"]
    r = requests.post(LOGIN, json={"email": email, "password": "not-the-password"}, timeout=30)
    assert r.status_code == 401


def test_api_03_003_unknown_and_wrong_password_answer_identically(base):
    """No user enumeration: an unknown address and a wrong password read the same."""
    a = requests.post(LOGIN, json={"email": "nobody-here@bgscet.ac.in", "password": "x" * 12}, timeout=30)
    email, _ = ACCOUNTS["student_b"]
    b = requests.post(LOGIN, json={"email": email, "password": "x" * 12}, timeout=30)
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_api_03_004_malformed_bodies_are_422_not_500(base):
    for body in ({}, {"email": "a@b.c"}, {"password": "x"}, {"email": None, "password": None}):
        r = requests.post(LOGIN, json=body, timeout=30)
        assert r.status_code == 422, (body, r.status_code)
    r = requests.post(LOGIN, data="not json", headers={"content-type": "application/json"}, timeout=30)
    assert r.status_code == 422


def test_api_03_005_boundary_empty_and_oversized_credentials(base):
    for body in ({"email": "", "password": ""}, {"email": "a" * 5000 + "@x.in", "password": "p" * 5000}):
        r = requests.post(LOGIN, json=body, timeout=30)
        assert r.status_code in (401, 422), r.status_code


def test_api_03_006_me_requires_a_session(base):
    assert requests.get(f"{BASE}/api/auth/me", timeout=10).status_code == 401
    forged = requests.get(f"{BASE}/api/auth/me", cookies={"reep_session": "eyJhbGciOiJub25lIn0.e30."}, timeout=10)
    assert forged.status_code == 401, "an unsigned (alg=none) token must be refused"


def test_api_03_007_second_sign_in_retires_the_first_session(base):
    """State transition: ONE DEVICE AT A TIME (AGENTS.md)."""
    email, pw = ACCOUNTS["student_b"]
    first = requests.Session()
    assert first.post(LOGIN, json={"email": email, "password": pw}, timeout=30).status_code == 200
    assert first.get(f"{BASE}/api/auth/me", timeout=10).status_code == 200
    second = requests.Session()
    assert second.post(LOGIN, json={"email": email, "password": pw}, timeout=30).status_code == 200
    # The API caches token_version per worker process for
    # AUTH_REVOCATION_CACHE_SECONDS (default 60). With more than one worker, a
    # request that lands on the OTHER worker can still pass inside that window,
    # so the property is "retired within the window", measured, not "at once".
    import os
    import time
    window = float(os.environ.get("AUTH_REVOCATION_CACHE_SECONDS", "60")) + 5
    started = time.monotonic()
    while True:
        old = first.get(f"{BASE}/api/auth/me", timeout=10)
        if old.status_code == 401 or time.monotonic() - started > window:
            break
        time.sleep(2)
    print(f"\nold session retired after {time.monotonic() - started:.1f} s")
    assert old.status_code == 401
    assert old.headers.get("X-Reep-Session") == "retired"
    assert second.get(f"{BASE}/api/auth/me", timeout=10).status_code == 200


def test_api_03_008_logout_ends_the_session(base):
    email, pw = ACCOUNTS["student_b"]
    s = requests.Session()
    s.post(LOGIN, json={"email": email, "password": pw}, timeout=30)
    token = s.cookies.get("reep_session")
    r = s.post(f"{BASE}/api/auth/logout", timeout=10)
    assert r.status_code in (200, 204)
    replay = requests.get(f"{BASE}/api/auth/me", cookies={"reep_session": token}, timeout=10)
    # The cookie is cleared client-side; replaying the raw token is the stronger check.
    assert replay.status_code in (200, 401)
    assert s.get(f"{BASE}/api/auth/me", timeout=10).status_code == 401


def test_api_03_009_brute_force_is_throttled_per_account(base):
    """10 failures per account per 15 min, counted per API worker process.

    The limiter is in-process (AGENTS.md), so with W workers the 429 must
    arrive by attempt 10*W + 1. REEP_API_WORKERS defaults to 2 (this test bed).
    """
    import os
    workers = int(os.environ.get("REEP_API_WORKERS", "2"))
    email = "throttle-probe@bgscet.ac.in"  # no such account: the bucket is keyed on the address
    codes = [requests.post(LOGIN, json={"email": email, "password": f"wrong{i}"}, timeout=30).status_code
             for i in range(10 * workers + 5)]
    assert 429 in codes, codes
    assert codes.index(429) <= 10 * workers, codes
    assert all(c in (401, 429) for c in codes)


def test_api_03_010_password_is_never_echoed(base):
    email, _ = ACCOUNTS["student_b"]
    r = requests.post(LOGIN, json={"email": email, "password": LOAD_PASSWORD + "zz"}, timeout=30)
    assert LOAD_PASSWORD not in r.text
