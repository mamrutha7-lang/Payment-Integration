#!/usr/bin/env python3
"""Prove that `app/` imports nothing requirements.txt does not declare.

WHY THIS EXISTS. `app/interview_local.py` reached main importing numpy with no
entry in any requirements file. It survived review and it survived CI, because
of exactly one detail: the import is LAZY — it sits inside a request handler —
so the API still booted, every existing test still passed, and the break was
invisible until either a student hit the local-engine path in production or
someone ran the suite on a machine where numpy had not been pip-installed by
hand for something else.

The voice worker has had a guard for this shape of bug since the day
requirements-voice.txt shipped a manifest that omitted four of the packages
voice_agent.py imported (`worker-imports` in .github/workflows/ci.yml). The API
had no equivalent. This is it.

HOW IT WORKS, and why it is import-based rather than a static scan. Walking the
AST for `import` statements sounds tidier, but it cannot tell a third-party
package from a first-party module or a stdlib one without reimplementing the
resolver, and it misses `importlib.import_module(name)`. Actually importing
every module under `app.` in an environment built from requirements.txt ALONE
asks the real question — "can a production image import this?" — and answers it
the same way the runtime will.

Run in CI against a clean install of requirements.txt (NOT -dev: pytest and
friends are not in the image, so a module that imports one of them is just as
broken as one importing numpy). Locally:

    python3 tools/ci/check_api_imports.py
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import traceback
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api-py"


def main() -> int:
    sys.path.insert(0, str(API_ROOT))

    # Settings has development defaults for everything, so importing does not
    # need a configured environment — but be explicit rather than relying on it,
    # and pin ENV to a development name so no module-level production guard
    # fires during a pure import check.
    os.environ.setdefault("ENV", "dev")

    try:
        import app
    except Exception:
        print("FAILED to import the `app` package at all:", file=sys.stderr)
        traceback.print_exc()
        return 1

    failures: list[tuple[str, BaseException]] = []
    scanned = 0

    for module in pkgutil.walk_packages(app.__path__, prefix="app."):
        scanned += 1
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # noqa: BLE001 — every failure is reportable
            failures.append((module.name, exc))

    print(f"imported {scanned} modules under app/")

    if not failures:
        print("OK — every module imports against requirements.txt alone.")
        return 0

    print(f"\n{len(failures)} module(s) import something requirements.txt does not declare:\n",
          file=sys.stderr)
    for name, exc in failures:
        print(f"  {name}", file=sys.stderr)
        print(f"      {type(exc).__name__}: {exc}", file=sys.stderr)
        if isinstance(exc, ModuleNotFoundError) and exc.name:
            print(
                f"      → add `{exc.name}` to apps/api-py/requirements.txt (pinned ==),",
                file=sys.stderr,
            )
            print(
                "        or move the import behind a feature flag that is off by default.",
                file=sys.stderr,
            )
    print(
        "\nA lazy import inside a function does NOT make this acceptable: it only\n"
        "moves the crash from boot to the first student who reaches that code path.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
