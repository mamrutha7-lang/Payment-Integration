#!/usr/bin/env python3
"""Prove the built SPA still matches the service worker's manifest.

    python3 tools/ci/check_ngsw_integrity.py apps/web/dist/web/browser

WHY THIS EXISTS. `ng build` writes ngsw.json with a SHA-1 of every file the
worker is allowed to serve, and ngsw verifies those hashes at install. A file
that changed after the build is not a stale cache and not a slow update: the
worker cannot validate the new version, so it keeps serving the OLD one and
retries forever. The app looks deployed — CloudFront has the new files, curl
shows them, index.html is right — and every installed student stays on the
previous build indefinitely, with nothing in any log saying why.

Two steps in .github/workflows/deploy.yml run AFTER `ng build` and touch that
directory, which is exactly the window this closes:

  * `sentry-cli sourcemaps inject dist/web/browser` writes a debug id into
    each .js that does not already carry one. The step's own comment argues it
    is a no-op because Angular already stamps ids when sourceMap.scripts is on
    — that is true today and it is an ASSUMPTION about another tool's
    behaviour across versions, which is the kind of thing that changes in a
    patch release and is discovered by a student six weeks later.

  * `find … -name '*.map' -delete`, which is correct and must stay: a map that
    reaches CloudFront is the application's source. It is safe only because
    ngsw-config.json excludes maps from every group, so no .map is in the
    hashTable. This asserts that rather than trusting it.

It also catches the plainer failure of a file in the manifest that is not in
the directory at all.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys


def sha1(path: pathlib.Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def main(argv: list[str]) -> int:
    root = pathlib.Path(argv[1] if len(argv) > 1 else "apps/web/dist/web/browser")
    manifest = root / "ngsw.json"
    if not manifest.exists():
        print(f"{manifest} is missing — was the app built with the production configuration?")
        return 1

    table: dict[str, str] = json.loads(manifest.read_text())["hashTable"]
    missing: list[str] = []
    changed: list[tuple[str, str, str]] = []

    for url, expected in table.items():
        f = root / url.lstrip("/")
        if not f.is_file():
            missing.append(url)
            continue
        actual = sha1(f)
        if actual != expected:
            changed.append((url, expected, actual))

    # A .map in the hashTable is the other half of the same trap: deploy
    # deletes maps before upload, so a worker that expects one can never
    # install. Caught here rather than as a mystery "missing" line above.
    maps = [u for u in table if u.endswith(".map")]

    if not (missing or changed or maps):
        print(f"ngsw.json: {len(table)} files, all present and unmodified")
        return 0

    for url in maps:
        print(f"SOURCE MAP IN MANIFEST: {url}")
        print("  deploy.yml deletes maps before upload, so the worker could never install.")
        print("  Exclude maps from every group in apps/web/ngsw-config.json.")
    for url in missing:
        print(f"MISSING: {url} is in ngsw.json and not on disk")
    for url, expected, actual in changed:
        print(f"MODIFIED AFTER BUILD: {url}")
        print(f"  ngsw.json expects {expected}")
        print(f"  the file on disk is {actual}")
        print("  Something rewrote it after `ng build`. The worker will refuse this")
        print("  version and keep serving the previous one to every installed student.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
