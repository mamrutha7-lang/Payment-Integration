#!/usr/bin/env python3
"""tools/ci/release_gate.py — may an agent ship this change set on its own?

WHY THIS IS CODE AND NOT A PROMPT. `.github/workflows/agent-release.yml` can
dispatch `deploy.yml`, which puts code in front of every student. The question
"is this safe to ship without a human" must therefore be answered the same way
every time, be readable in a pull request, and be pinned by a test. A model
can write the release notes; it does not get to decide this. It reads the list
of files changed since the last successful production deploy and answers:

  target      none | web-only | api-only | api-and-web   (deploy.yml's choices)
  auto_ok     true only when EVERY changed file is in a class an agent may ship
  reasons     one sentence per refusal, so the release issue says exactly why
              a human is needed

A change is REFUSED for auto-deploy when it touches any of:

  * a migration or a model          — applied once, to production, one way
  * infra/ or a deploy workflow     — cdk-deploy.yml is human-only by design
  * rule 1 / rule 2 / auth / the destructors (SENSITIVE below)
  * the service worker or the PWA manifest — a bad one freezes installed
    phones on the old build (AGENTS.md, "The phone")
  * a runtime dependency manifest or the Dockerfile
  * more than MAX_FILES files       — big releases get a human

Everything else under apps/ is shippable. Docs, tests and tooling need no
deploy at all (target none). Unknown top-level paths are refused rather than
guessed, for the purge modules' reason: an unclassified thing aborts the run.

Usage:
    git diff --name-only BASE HEAD | python tools/ci/release_gate.py
    python tools/ci/release_gate.py --base BASE --head HEAD
Writes JSON to stdout and, under GitHub Actions, target/auto_ok to
$GITHUB_OUTPUT.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

MAX_FILES = 40

# Path PREFIXES whose change always needs a human. Order is irrelevant.
REFUSE = {
    "apps/api-py/migrations/": "a migration runs once, against production, in one direction",
    "apps/api-py/app/models/": "a model change needs its migration applied by a human",
    "apps/api-py/requirements.txt": "a runtime dependency changed",
    "apps/api-py/Dockerfile": "the image definition changed",
    "infra/": "infrastructure is deployed through cdk-deploy.yml by a human",
    ".github/workflows/deploy.yml": "the deploy pipeline itself changed",
    ".github/workflows/cdk-deploy.yml": "the infra deploy pipeline changed",
    ".github/workflows/ops-task.yml": "the ops task menu changed",
    ".github/workflows/agent-release.yml": "the auto-release pipeline changed",
    "tools/ci/release_gate.py": "this gate changed",
    "apps/web/ngsw-config.json": "a bad service worker freezes installed phones",
    "apps/web/public/reep-sw.js": "a bad service worker freezes installed phones",
    "apps/web/src/app/app.config.ts": "it registers the service worker every installed phone runs",
    "apps/web/public/manifest.webmanifest": "the installed-app manifest changed",
    "apps/web/src/polyfills.ts": "the old-browser polyfills changed",
    "apps/web/package.json": "a front-end dependency changed",
    "apps/web/package-lock.json": "a front-end dependency changed",
    "apps/web/angular.json": "the build configuration changed",
}

# Files where one wrong line is rule 1, rule 2, auth or data destruction.
SENSITIVE = (
    "apps/api-py/app/config.py",
    "apps/api-py/app/security.py",
    "apps/api-py/app/google_auth.py",
    "apps/api-py/app/identity.py",
    "apps/api-py/app/policies.py",
    "apps/api-py/app/governance.py",
    "apps/api-py/app/ai/llm.py",
    "apps/api-py/app/observability.py",
    "apps/api-py/app/telemetry_scrub.py",
    "apps/api-py/app/retention.py",
    "apps/api-py/app/account_deletion.py",
    "apps/api-py/app/deletion_walk.py",
    "apps/api-py/app/college_deletion.py",
    "apps/api-py/app/purge_",
    "apps/api-py/app/routers/auth.py",
    "apps/api-py/app/routers/mentor.py",
    "apps/api-py/app/routers/interview.py",
    "apps/api-py/app/routers/admin_deletion.py",
    "apps/api-py/app/routers/onboarding.py",
    "apps/api-py/app/routers/passwords.py",
)

# Paths that never need a deploy at all.
NO_DEPLOY = (
    "docs/",
    "test-management/",
    "tests/",
    "apps/api-py/tests/",
    "apps/api-py/tools/",
    "tools/",
    ".github/",
    ".claude/",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "CONTRIBUTING.md",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    ".gitleaks.toml",
    ".pre-commit-config.yaml",
    ".mcp.json",
    "playwright.config.ts",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "docker-compose.yml",
    "docker/",
    "infra/",  # known, and refused above: cdk-deploy.yml is human-only
)

API = "apps/api-py/"
WEB = "apps/web/"


@dataclass
class Verdict:
    target: str = "none"
    auto_ok: bool = True
    reasons: list[str] = field(default_factory=list)
    files: int = 0

    def refuse(self, why: str) -> None:
        self.auto_ok = False
        if why not in self.reasons:
            self.reasons.append(why)


def classify(paths: list[str]) -> Verdict:
    paths = [p.strip() for p in paths if p.strip()]
    v = Verdict(files=len(paths))
    api = web = False
    for p in paths:
        for prefix, why in REFUSE.items():
            if p.startswith(prefix):
                v.refuse(f"{p}: {why}")
        if any(p.startswith(s) for s in SENSITIVE):
            v.refuse(f"{p}: rule 1 / rule 2 / auth / deletion code needs a human")
        # tools/ci/release_gate.py is both REFUSE and NO_DEPLOY: refused above.
        if p.startswith(API) and not p.startswith(("apps/api-py/tests/", "apps/api-py/tools/")):
            api = True
        elif p.startswith(WEB):
            web = True
        elif any(p == n or p.startswith(n) for n in NO_DEPLOY):
            pass
        else:
            v.refuse(f"{p}: not a path this gate knows how to classify")
    v.target = {
        (True, True): "api-and-web",
        (True, False): "api-only",
        (False, True): "web-only",
        (False, False): "none",
    }[(api, web)]
    if v.files > MAX_FILES:
        v.refuse(f"{v.files} files changed (more than {MAX_FILES}): a big release gets a human")
    if v.target == "none":
        # Nothing to ship is never an auto-deploy, whatever else is true.
        v.auto_ok = False
    return v


def _changed(base: str, head: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.splitlines()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args(argv)
    paths = _changed(args.base, args.head) if args.base else sys.stdin.read().splitlines()
    v = classify(paths)
    print(json.dumps(v.__dict__, indent=2))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"target={v.target}\n")
            fh.write(f"auto_ok={'true' if v.auto_ok else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
