"""tools/play/upload_bundle.py puts the phone app on Google Play.

A fake session stands in for Google, so these pin the order of the four calls,
that a failure never leaves a half-made edit behind, and that a bad key is
refused without its contents being printed. No database, no network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent.parent
_spec = importlib.util.spec_from_file_location("upload_bundle", REPO / "tools" / "play" / "upload_bundle.py")
play = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["upload_bundle"] = play
_spec.loader.exec_module(play)

API = f"{play.API}/{play.PACKAGE}"


class _Response:
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body or {}
        self.content = json.dumps(self._body).encode() if body is not None else b""
        self.text = self.content.decode()

    def json(self) -> dict:
        return self._body


class _Play:
    """Records every call; answers like Google unless told to refuse one."""

    def __init__(self, refuse: str | None = None) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.refuse = refuse

    def _answer(self, method: str, url: str, kwargs: dict, body: dict) -> _Response:
        self.calls.append((method, url, kwargs))
        if self.refuse and self.refuse in url:
            return _Response(403, {"error": {"message": "The caller does not have permission"}})
        return _Response(200, body)

    def post(self, url: str, **kwargs) -> _Response:
        if url.endswith("/edits"):
            return self._answer("POST", url, kwargs, {"id": "e1"})
        if "/bundles" in url:
            return self._answer("POST", url, kwargs, {"versionCode": 4201, "sha256": "x"})
        return self._answer("POST", url, kwargs, {"id": "e1"})

    def put(self, url: str, **kwargs) -> _Response:
        return self._answer("PUT", url, kwargs, {"track": "internal"})

    def delete(self, url: str, **kwargs) -> _Response:
        self.calls.append(("DELETE", url, kwargs))
        return _Response(204)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    path = tmp_path / "app-release.aab"
    path.write_bytes(b"PK\x03\x04 a bundle")
    return path


def test_one_edit_upload_track_commit_in_that_order(bundle: Path) -> None:
    google = _Play()
    version, status = play.publish(google, bundle, track="internal", status="draft", release_name="1.0.42")

    assert (version, status) == (4201, "draft")
    assert [(m, u) for m, u, _ in google.calls] == [
        ("POST", f"{API}/edits"),
        ("POST", f"{play.UPLOAD_API}/{play.PACKAGE}/edits/e1/bundles"),
        ("PUT", f"{API}/edits/e1/tracks/internal"),
        ("POST", f"{API}/edits/e1:commit"),
    ]
    upload = google.calls[1][2]
    assert upload["data"] == bundle.read_bytes()
    assert upload["params"] == {"uploadType": "media"}
    assert upload["headers"]["Content-Type"] == "application/octet-stream"
    # The version on the track is the one GOOGLE read out of the bundle.
    assert google.calls[2][2]["json"] == {
        "track": "internal",
        "releases": [{"name": "1.0.42", "versionCodes": ["4201"], "status": "draft"}],
    }
    assert all(kwargs.get("timeout") for _, _, kwargs in google.calls)


@pytest.mark.parametrize("refused", ["/bundles", "/tracks/", ":commit"])
def test_a_refusal_deletes_the_edit_and_names_googles_reason(bundle: Path, refused: str) -> None:
    google = _Play(refuse=refused)
    with pytest.raises(play.PlayError, match="403.*does not have permission"):
        play.publish(google, bundle, track="internal", status="draft", release_name="1.0.42")
    assert google.calls[-1][:2] == ("DELETE", f"{API}/edits/e1")
    if refused != ":commit":
        assert not any(u.endswith(":commit") for _, u, _ in google.calls)


class _DraftApp(_Play):
    """An app never rolled out anywhere: Google takes only a draft release."""

    def __init__(self, message: str = "Only releases with status draft may be created on draft app.") -> None:
        super().__init__()
        self.message = message

    def put(self, url: str, **kwargs) -> _Response:
        self.calls.append(("PUT", url, kwargs))
        if kwargs["json"]["releases"][0]["status"] != "draft":
            return _Response(400, {"error": {"message": self.message}})
        return _Response(200, {"track": "internal"})


def test_internal_testing_rolls_out_at_once(bundle: Path) -> None:
    google = _Play()
    version, status = play.publish(google, bundle, track="internal", status="completed", release_name="1.0.7")
    assert (version, status) == (4201, "completed")
    puts = [kwargs["json"]["releases"][0]["status"] for method, _, kwargs in google.calls if method == "PUT"]
    assert puts == ["completed"]


def test_a_first_release_google_takes_only_as_a_draft_is_kept_as_one(bundle: Path) -> None:
    """Before the app's first rollout Google refuses `completed`; the bundle is
    still delivered, as a draft, in the same edit, and the caller is told."""
    google = _DraftApp()
    version, status = play.publish(google, bundle, track="internal", status="completed", release_name="1.0.7")
    assert (version, status) == (4201, "draft")
    puts = [kwargs["json"]["releases"][0]["status"] for method, _, kwargs in google.calls if method == "PUT"]
    assert puts == ["completed", "draft"]
    assert google.calls[-1][:2] == ("POST", f"{API}/edits/e1:commit")
    assert {u for _, u, _ in google.calls if "/edits/" in u and not u.endswith(":commit")} <= {
        f"{play.UPLOAD_API}/{play.PACKAGE}/edits/e1/bundles",
        f"{API}/edits/e1/tracks/internal",
    }


def test_any_other_refusal_is_not_retried_as_a_draft(bundle: Path) -> None:
    google = _DraftApp(message="APK specifies a version code that has already been used.")
    with pytest.raises(play.PlayError, match="already been used"):
        play.publish(google, bundle, track="internal", status="completed", release_name="1.0.7")
    assert [m for m, _, _ in google.calls].count("PUT") == 1
    assert google.calls[-1][:2] == ("DELETE", f"{API}/edits/e1")


def test_main_writes_the_status_google_accepted_into_the_run_summary(
    bundle: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("PLAY_SERVICE_ACCOUNT_JSON", json.dumps({"type": "service_account", "client_email": "c@p"}))
    monkeypatch.setattr(play, "publish", lambda *a, **k: (4201, "draft"))
    # Stand-ins for google-auth, so the test needs neither it nor a network.
    stubs = {name: types.ModuleType(name) for name in (
        "google", "google.oauth2", "google.oauth2.service_account",
        "google.auth", "google.auth.transport", "google.auth.transport.requests",
    )}
    stubs["google.oauth2.service_account"].Credentials = types.SimpleNamespace(
        from_service_account_info=lambda *a, **k: None
    )
    stubs["google.auth.transport.requests"].AuthorizedSession = lambda credentials: object()
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)

    assert play.main(["--bundle", str(bundle), "--track", "internal", "--status", "completed", "--release-name", "1.0.7"]) == 0
    out = capsys.readouterr().out
    assert "::warning::" in out and "Start rollout" in out
    assert "Start rollout" in summary.read_text(encoding="utf-8")


def test_an_unknown_track_or_status_never_opens_an_edit(bundle: Path) -> None:
    google = _Play()
    with pytest.raises(play.PlayError, match="unknown track"):
        play.publish(google, bundle, track="beta-ish", status="draft", release_name="x")
    with pytest.raises(play.PlayError, match="unknown status"):
        play.publish(google, bundle, track="internal", status="halted", release_name="x")
    assert google.calls == []


@pytest.mark.parametrize(
    ("raw", "says"),
    [
        ("", "is empty"),
        ("   ", "is empty"),
        ("not json, a secret-looking string", "not valid JSON"),
        (json.dumps({"type": "authorized_user", "client_email": "a@b"}), "not a service account"),
        (json.dumps(["service_account"]), "not a service account"),
    ],
)
def test_a_bad_key_is_refused_without_echoing_it(raw: str, says: str) -> None:
    with pytest.raises(play.PlayError, match=says) as refused:
        play.service_account_info(raw)
    if raw.strip():
        assert raw not in str(refused.value)


def test_a_real_looking_key_is_accepted() -> None:
    info = play.service_account_info(json.dumps({"type": "service_account", "client_email": "ci@p.iam.gserviceaccount.com"}))
    assert info["client_email"] == "ci@p.iam.gserviceaccount.com"


def test_main_refuses_before_reaching_google(bundle: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("PLAY_SERVICE_ACCOUNT_JSON", raising=False)
    args = ["--track", "internal", "--status", "draft", "--release-name", "1.0.1"]
    assert play.main(["--bundle", str(bundle), *args]) == 1
    assert "PLAY_SERVICE_ACCOUNT_JSON is empty" in capsys.readouterr().err

    apk = tmp_path / "app-release.apk"
    apk.write_bytes(b"x")
    assert play.main(["--bundle", str(apk), *args]) == 1
    assert "is not an .aab file" in capsys.readouterr().err


def test_the_package_is_the_apps_id() -> None:
    """Play keys everything on the package name, and it can never change after
    the first upload, so the uploader and the app must name the same one."""
    gradle = (REPO / "apps" / "web" / "android" / "app" / "build.gradle").read_text(encoding="utf-8")
    capacitor = (REPO / "apps" / "web" / "capacitor.config.ts").read_text(encoding="utf-8")
    assert f'applicationId "{play.PACKAGE}"' in gradle
    assert f"appId: '{play.PACKAGE}'" in capacitor
