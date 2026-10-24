"""The phone app (Ionic + Capacitor, apps/web/capacitor.config.ts) signs in.

The app is served from https://localhost (Android) or capacitor://localhost
(iOS), so to this API it is another site and the httpOnly `reep_session` cookie
can never reach it. NATIVE_APP_ORIGINS names those origins; for a caller from
one of them, /auth/login puts the session token in the body and the API accepts
it back as `Authorization: Bearer`.

What is pinned here, in the order it matters:

  * A browser on the real site NEVER gets the token in a body. The httpOnly
    cookie being the only copy is the whole reason the web holds it there.
  * A blank setting means nobody gets one, whatever Origin they send.
  * The bearer is the cookie's own token and goes through the same
    verification: a sign-in elsewhere retires it exactly like a cookie.
  * "*" in the setting is refused rather than read as "every origin".
"""

from starlette.requests import Request

from app.config import settings
from app.identity import session_token_from
from conftest import TEST_PASSWORD, requires_db

APP_ORIGIN = "https://localhost"


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def test_the_cookie_wins_over_a_bearer():
    request = _request({"cookie": "reep_session=from-cookie", "authorization": "Bearer from-header"})
    assert session_token_from(request) == "from-cookie"


def test_a_bearer_is_read_when_there_is_no_cookie():
    assert session_token_from(_request({"authorization": "Bearer abc.def"})) == "abc.def"
    assert session_token_from(_request({"authorization": "bearer abc.def"})) == "abc.def"


def test_anything_that_is_not_a_bearer_is_no_token():
    assert session_token_from(_request({})) is None
    assert session_token_from(_request({"authorization": "Basic Zm9vOmJhcg=="})) is None
    assert session_token_from(_request({"authorization": "Bearer   "})) is None


def test_a_wildcard_is_never_an_app_origin(monkeypatch):
    monkeypatch.setattr(settings, "native_app_origins", "*, https://localhost/ ,, capacitor://localhost")
    assert settings.native_app_origin_list == ["https://localhost", "capacitor://localhost"]


def test_the_default_admits_no_app():
    assert type(settings).model_fields["native_app_origins"].default == ""


@requires_db
def test_the_app_gets_a_token_and_the_browser_does_not(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "native_app_origins", APP_ORIGIN)
    user = make_user("native")
    body = {"email": user.email, "password": TEST_PASSWORD}

    web = client.post("/api/auth/login", json=body, headers={"Origin": settings.web_origin})
    client.cookies.clear()
    assert web.status_code == 200, web.text
    assert web.json()["session_token"] is None, "a browser must keep the cookie as the only copy"

    app = client.post("/api/auth/login", json=body, headers={"Origin": APP_ORIGIN})
    client.cookies.clear()
    assert app.status_code == 200, app.text
    token = app.json()["session_token"]
    assert token

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == user.email

    # One device at a time applies to the app exactly as to a browser.
    client.post("/api/auth/login", json=body, headers={"Origin": settings.web_origin})
    client.cookies.clear()
    stale = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert stale.status_code == 401
    assert stale.headers.get("X-Reep-Session") == "retired"


@requires_db
def test_no_setting_means_no_token_for_anybody(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "native_app_origins", "")
    user = make_user("native-off")
    r = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": TEST_PASSWORD},
        headers={"Origin": APP_ORIGIN},
    )
    client.cookies.clear()
    assert r.status_code == 200, r.text
    assert r.json()["session_token"] is None


@requires_db
def test_logout_with_a_bearer_retires_it(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "native_app_origins", APP_ORIGIN)
    user = make_user("native-logout")
    r = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": TEST_PASSWORD},
        headers={"Origin": APP_ORIGIN},
    )
    client.cookies.clear()
    token = r.json()["session_token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/auth/logout", headers=auth).status_code in (200, 204)
    client.cookies.clear()
    assert client.get("/api/auth/me", headers=auth).status_code == 401
