"""Upload a signed Android App Bundle to one Google Play track.

Run by .github/workflows/android-play.yml, never by hand on a laptop that
holds the service account (docs/mobile-app.md, "Publish to Google Play").

    PLAY_SERVICE_ACCOUNT_JSON='{...}' python tools/play/upload_bundle.py \
        --bundle app-release.aab --track internal --status completed --release-name 1.0.42

It speaks the Google Play Developer API (androidpublisher v3) directly with
Google's own auth library rather than through a third-party GitHub Action,
because whatever runs this step holds the credential that publishes REEP to
every student's phone. One edit, four calls: open the edit, upload the bundle,
put that version on the track, commit. Anything that fails before the commit
deletes the edit, so a half-made release never sits in Play Console.

The service account arrives in an ENVIRONMENT VARIABLE and never in argv:
argv is readable in `ps` and is echoed in the workflow log.

Google refuses the API for an app's very first bundle: that one is uploaded by
hand in Play Console (the docs say how), and every later one comes through here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PACKAGE = "in.ac.bgscet.reep"  # capacitor.config.ts appId and build.gradle applicationId
TRACKS = ("internal", "alpha", "beta", "production")
# draft: the release waits in Play Console until somebody presses "Start rollout".
# completed: it goes to the track's testers (or, on production, to everybody) at once.
STATUSES = ("draft", "completed")
# Google's refusal of a non-draft release on an app that has never been rolled
# out anywhere. Matched on this phrase alone, because it is the one refusal a
# draft retry can answer; every other one is reported as it is.
DRAFT_APP_REFUSAL = "draft app"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"
API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
UPLOAD_API = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications"
# An AAB is tens of megabytes; the default of no timeout at all would let a
# stalled upload hold the runner for its whole six hours.
TIMEOUT = (30, 900)


class PlayError(RuntimeError):
    """A refusal from Google Play, carrying Google's own sentence."""


def _ok(response: Any, what: str) -> dict:
    if 200 <= response.status_code < 300:
        return response.json() if response.content else {}
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = response.text[:500]
    raise PlayError(f"{what} failed ({response.status_code}): {message}")


def service_account_info(raw: str) -> dict:
    """The parsed key, or a refusal that never echoes what it was given."""
    if not raw.strip():
        raise PlayError(
            "PLAY_SERVICE_ACCOUNT_JSON is empty. Add the service account's JSON key "
            "as a secret of the play-store environment (docs/mobile-app.md)."
        )
    try:
        info = json.loads(raw)
    except ValueError:
        raise PlayError("PLAY_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the whole key file.") from None
    if not isinstance(info, dict) or info.get("type") != "service_account" or not info.get("client_email"):
        raise PlayError("PLAY_SERVICE_ACCOUNT_JSON is not a service account key (its \"type\" must be \"service_account\").")
    return info


def publish(session: Any, bundle: Path, *, track: str, status: str, release_name: str,
            package: str = PACKAGE) -> tuple[int, str]:
    """Upload `bundle` and put it on `track`.

    Returns the versionCode Google recorded and the status the release was
    actually given: a `completed` release on an app that has never been rolled
    out anywhere is refused by Google, and is then put on the track as a draft
    in the same edit rather than failing the run with the bundle unsent.
    """
    if track not in TRACKS:
        raise PlayError(f"unknown track {track!r}; expected one of {', '.join(TRACKS)}")
    if status not in STATUSES:
        raise PlayError(f"unknown status {status!r}; expected one of {', '.join(STATUSES)}")

    edit_id = _ok(session.post(f"{API}/{package}/edits", timeout=TIMEOUT), "Opening an edit")["id"]
    committed = False
    try:
        uploaded = _ok(
            session.post(
                f"{UPLOAD_API}/{package}/edits/{edit_id}/bundles",
                params={"uploadType": "media"},
                data=bundle.read_bytes(),
                headers={"Content-Type": "application/octet-stream"},
                timeout=TIMEOUT,
            ),
            "Uploading the bundle",
        )
        version_code = int(uploaded["versionCode"])

        def put_on_track(release_status: str) -> None:
            _ok(
                session.put(
                    f"{API}/{package}/edits/{edit_id}/tracks/{track}",
                    json={
                        "track": track,
                        "releases": [
                            {"name": release_name, "versionCodes": [str(version_code)], "status": release_status}
                        ],
                    },
                    timeout=TIMEOUT,
                ),
                f"Putting version {version_code} on the {track} track",
            )

        try:
            put_on_track(status)
        except PlayError as refused:
            if status == "draft" or DRAFT_APP_REFUSAL not in str(refused):
                raise
            status = "draft"
            put_on_track(status)
        _ok(session.post(f"{API}/{package}/edits/{edit_id}:commit", timeout=TIMEOUT), "Committing the edit")
        committed = True
        return version_code, status
    finally:
        if not committed:
            # Best effort: an abandoned edit also expires on its own.
            try:
                session.delete(f"{API}/{package}/edits/{edit_id}", timeout=TIMEOUT)
            except Exception:  # noqa: BLE001 - the original error is the one worth reporting
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bundle", required=True, type=Path, help="the signed .aab")
    parser.add_argument("--track", required=True, choices=TRACKS)
    parser.add_argument("--status", required=True, choices=STATUSES)
    parser.add_argument("--release-name", required=True, help="shown in Play Console, e.g. 1.0.42")
    args = parser.parse_args(argv)

    try:
        if args.bundle.suffix != ".aab" or not args.bundle.is_file():
            raise PlayError(f"{args.bundle} is not an .aab file")
        info = service_account_info(os.environ.get("PLAY_SERVICE_ACCOUNT_JSON", ""))

        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        version_code, status = publish(
            AuthorizedSession(credentials), args.bundle,
            track=args.track, status=args.status, release_name=args.release_name,
        )
    except PlayError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    if status != args.status:
        print(
            "::warning::Google Play accepts only a draft until the app's first release has been rolled out "
            "in Play Console, so this version is a draft. Press Start rollout there once; later runs roll out "
            "on their own."
        )
    if status == "draft":
        where = "waiting in Play Console for you to press **Start rollout**"
    elif args.track == "internal":
        where = "rolling out to your internal testers now (usually within minutes)"
    else:
        where = "rolling out"
    line = f"Version {args.release_name} (code {version_code}) is on the **{args.track}** track, {where}."
    print(f"Uploaded {PACKAGE}. {line}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"### Uploaded to Google Play\n{line}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
