"""tools/ci/branch_policy.py is the promotion path of docs/branching-strategy.md.

feature -> dev -> stage -> main, and hotfix/* -> main. Each allowed and each
refused pair is pinned by name, because loosening one is a way onto main that
skips stage. No database.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent.parent
_spec = importlib.util.spec_from_file_location("branch_policy", REPO / "tools" / "ci" / "branch_policy.py")
policy = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(policy)


@pytest.mark.parametrize(
    ("base", "head"),
    [
        ("main", "stage"),
        ("main", "hotfix/v1.0.1"),
        ("stage", "dev"),
        ("stage", "main"),  # back-merge after a hotfix
        ("dev", "feature/login"),
        ("dev", "hotfix/v1.0.1"),  # a hotfix goes back into dev too
        ("dev", "main"),  # back-merge after a release or hotfix
        ("dev", "fix/mentor-scope"),
        ("feature/payment", "feature/payment-ui"),  # stacked PR: not policed
    ],
)
def test_the_promotion_path_is_allowed(base: str, head: str) -> None:
    ok, why = policy.verdict(base, head)
    assert ok, why


@pytest.mark.parametrize(
    ("base", "head"),
    [
        ("main", "dev"),  # skips stage
        ("main", "feature/login"),  # skips dev and stage
        ("main", "fix/anything"),  # an urgent fix is spelled hotfix/*
        ("main", "hotfix/"),  # a prefix is not a branch
        ("main", "my-hotfix/x"),
        ("stage", "feature/login"),  # skips dev
        ("stage", "hotfix/v1.0.1"),  # hotfixes reach stage through main
        ("dev", "stage"),  # backwards
        ("main", "main"),
    ],
)
def test_a_shortcut_is_refused(base: str, head: str) -> None:
    ok, why = policy.verdict(base, head)
    assert not ok, f"{head} -> {base} should be refused"
    assert why


def test_the_cli_exit_codes() -> None:
    assert policy.main(["main", "stage"]) == 0
    assert policy.main(["main", "feature/x"]) == 1
    assert policy.main(["main"]) == 2


def test_the_rulesets_require_the_check_by_the_name_the_workflow_reports() -> None:
    """A required check is matched by display NAME; a rename silently retires it."""
    workflow = (REPO / ".github" / "workflows" / "branch-policy.yml").read_text(encoding="utf-8")
    names = re.findall(r"^    name:\s*(\S.*?)\s*$", workflow, re.M)
    assert names == ["Branch policy (promotion path)"], names
    for branch, required in (("main", True), ("stage", True), ("dev", False)):
        ruleset = json.loads((REPO / ".github" / "rulesets" / f"{branch}.json").read_text(encoding="utf-8"))
        rule = next(r for r in ruleset["rules"] if r["type"] == "required_status_checks")
        contexts = {c["context"] for c in rule["parameters"]["required_status_checks"]}
        assert (names[0] in contexts) is required, branch


def test_promotion_branches_take_merge_commits_only() -> None:
    """A squash of dev into stage (or stage into main) leaves the source branch
    without the squash commit, so every later promotion re-conflicts with the
    one before it. Only dev, where work branches are squashed, may squash."""
    for branch, allowed in (("main", ["merge"]), ("stage", ["merge"]), ("dev", ["squash", "merge"])):
        ruleset = json.loads((REPO / ".github" / "rulesets" / f"{branch}.json").read_text(encoding="utf-8"))
        pr = next(r for r in ruleset["rules"] if r["type"] == "pull_request")
        assert pr["parameters"]["allowed_merge_methods"] == allowed, branch
