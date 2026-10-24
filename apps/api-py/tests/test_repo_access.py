"""Who may merge into the long-lived branches, and who may run production workflows.

The repository has a collaborator with write access, and on a personal-account
repository write access is the only role a collaborator can hold. Two things
keep that from being merge access and production access:

  * `.github/rulesets/lead-review.json`: only the admin merges into main, stage
    and dev, and every PR asks the code owner for an approval;
  * an owner-only first step in deploy.yml, cdk-deploy.yml and ops-task.yml.

Each fact is pinned here because the way it breaks is a one-line diff that no
other test notices. No database.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent.parent
RULESETS = REPO / ".github" / "rulesets"
WORKFLOWS = REPO / ".github" / "workflows"
OWNER = "@darshani8"
LONG_LIVED = {"~DEFAULT_BRANCH", "refs/heads/stage", "refs/heads/dev"}


def _ruleset(name: str) -> dict:
    return json.loads((RULESETS / f"{name}.json").read_text(encoding="utf-8"))


def _rule(ruleset: dict, rule_type: str) -> dict:
    matches = [r for r in ruleset["rules"] if r["type"] == rule_type]
    assert len(matches) == 1, f"expected one {rule_type} rule in {ruleset['name']!r}, found {len(matches)}"
    return matches[0]


# --------------------------------------------------------------- the ruleset ---


def test_lead_review_covers_every_long_lived_branch() -> None:
    ruleset = _ruleset("lead-review")
    assert ruleset["enforcement"] == "active"
    assert ruleset["target"] == "branch"
    assert set(ruleset["conditions"]["ref_name"]["include"]) == LONG_LIVED
    assert ruleset["conditions"]["ref_name"]["exclude"] == []


def test_only_a_bypass_actor_may_update_the_long_lived_branches() -> None:
    """The `update` rule is what keeps the merge button shut for a collaborator
    even after an approval. Allowing fetch-and-merge would let a fork sync
    update the branch without the bypass."""
    update = _rule(_ruleset("lead-review"), "update")
    assert update["parameters"]["update_allows_fetch_and_merge"] is False


def test_the_one_bypass_is_the_admin_and_only_through_a_pull_request() -> None:
    """`always` would let the admin push straight to main past this ruleset; a
    second actor would be a second person who merges. Actor 5 is the
    repository admin role."""
    assert _ruleset("lead-review")["bypass_actors"] == [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
    ]


def test_every_pull_request_asks_the_code_owner_on_its_latest_push() -> None:
    params = _rule(_ruleset("lead-review"), "pull_request")["parameters"]
    assert params["required_approving_review_count"] >= 1
    assert params["require_code_owner_review"] is True
    assert params["require_last_push_approval"] is True
    assert params["dismiss_stale_reviews_on_push"] is True


def test_lead_review_narrows_no_merge_method() -> None:
    """Merge methods are the branch rulesets' decision (test_branch_policy.py
    pins merge-only on main and stage). Rules from several rulesets combine, so
    a narrower list here would silently overrule dev's squash."""
    params = _rule(_ruleset("lead-review"), "pull_request")["parameters"]
    assert set(params["allowed_merge_methods"]) == {"merge", "squash", "rebase"}


def test_the_admin_bypass_never_reaches_the_checks() -> None:
    """A bypass is per ruleset. CI, force-push and deletion live in rulesets
    nobody bypasses, so the lead still cannot merge red or push directly. Move
    one of those rules into lead-review.json and the admin skips it."""
    lead = {r["type"] for r in _ruleset("lead-review")["rules"]}
    assert lead == {"update", "pull_request"}, lead
    for branch in ("main", "stage", "dev"):
        ruleset = _ruleset(branch)
        assert ruleset["bypass_actors"] == [], branch
        types = {r["type"] for r in ruleset["rules"]}
        assert {"pull_request", "required_status_checks", "non_fast_forward", "deletion"} <= types, branch


def test_the_code_owner_asked_on_every_path_is_the_owner() -> None:
    """The catch-all is what makes GitHub ask the lead on a collaborator's PR,
    and the last matching pattern wins, so every later line must name the owner
    too or that path asks somebody else."""
    rules = [
        line.split()
        for line in (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert rules[0][0] == "*", rules[0]
    for pattern, *owners in rules:
        assert owners == [OWNER], f"{pattern} is owned by {owners}, not {OWNER}"


# ------------------------------------------------------ production workflows ---

GUARDED = {
    # workflow file -> extra actor admitted besides the owner
    "deploy.yml": "github-actions[bot]",  # agent-release.yml's dispatch
    "cdk-deploy.yml": None,
    "ops-task.yml": None,
}


def _jobs(text: str) -> dict[str, str]:
    """Each top-level job under `jobs:`, as its own block of text."""
    body = text.split("\njobs:\n", 1)[1]
    jobs: dict[str, list[str]] = {}
    current = None
    for line in body.splitlines():
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            current = match.group(1)
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return {name: "\n".join(lines) for name, lines in jobs.items()}


def _owner_step(job: str) -> str | None:
    """The owner check step's text, from its `- name:` to the next step."""
    match = re.search(r"^      - name: Only the repository owner may [^\n]*\n(.*?)(?=^      - |\Z)", job, re.M | re.S)
    return match.group(0) if match else None


@pytest.mark.parametrize("workflow", sorted(GUARDED))
def test_only_the_owner_can_start_a_production_workflow(workflow: str) -> None:
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    jobs = _jobs(text)
    guarded = {name: step for name, job in jobs.items() if (step := _owner_step(job))}
    assert len(guarded) == 1, f"{workflow}: expected the owner check in one job, found {sorted(guarded)}"
    (guard_job, step), = guarded.items()

    # triggering_actor, so a re-run is judged by whoever pressed Re-run.
    assert "ACTOR: ${{ github.triggering_actor }}" in step, workflow
    assert "OWNER: ${{ github.repository_owner }}" in step, workflow
    assert '[ "$ACTOR" != "$OWNER" ]' in step and "exit 1" in step, workflow
    # A step with `if:` can be skipped, and a skipped check passes.
    assert not re.search(r"^        if:", step, re.M), f"{workflow}: the owner check must never be conditional"

    extra = GUARDED[workflow]
    admitted = set(re.findall(r'"\$ACTOR" != "([^"$]+)"', step))
    assert admitted == ({extra} if extra else set()), f"{workflow} admits {admitted} besides the owner"

    # Every job that reaches AWS runs after the check: in the same job, below
    # it, or in a job that needs the guard job.
    for name, job in jobs.items():
        if "aws-actions/configure-aws-credentials" not in job:
            continue
        if name == guard_job:
            assert job.index("Only the repository owner may") < job.index("aws-actions/configure-aws-credentials"), (
                f"{workflow}: the owner check must run before the AWS credentials in {name}"
            )
        else:
            needs = re.search(r"^    needs:\s*(.+)$", job, re.M)
            assert needs and re.search(rf"\b{guard_job}\b", needs.group(1)), (
                f"{workflow}: job {name} reaches AWS without needing {guard_job}"
            )
