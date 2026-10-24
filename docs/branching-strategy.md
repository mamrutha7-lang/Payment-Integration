# Git branching strategy

Five kinds of branch, one direction of travel.

| Branch | Purpose | Cut from | Merges into | How |
|---|---|---|---|---|
| `main` | Production-ready. The only branch `deploy.yml` may ship (its OIDC role is pinned to `refs/heads/main`). | — | — | — |
| `stage` | Pre-production / staging. What `main` becomes at the next release. | `main` (once) | `main` | PR, **merge commit** |
| `dev` | Development / integration. Every piece of work lands here first. | `main` (once) | `stage` | PR, **merge commit** |
| `feature/*` | One piece of work (`feature/login`, `feature/payment`). `fix/`, `chore/`, `docs/` are the same kind. | `dev` | `dev` | PR, squash or merge |
| `hotfix/*` | An urgent production fix (`hotfix/v1.0.1`). | `main` | `main`, then `dev` | two PRs |

```
feature/login ──┐
feature/payment ┴─▶ dev ─▶ stage ─▶ main ──tag v1.0──▶ deploy
                    ▲                 │
                    └── back-merge ───┤
hotfix/v1.0.1 (from main) ────────────┴─▶ main, and ─▶ dev
```

## Day to day

```bash
git fetch origin dev
git switch -c feature/login origin/dev
# ... work, tools/ci/preflight.sh ...
git push -u origin HEAD
gh pr create --base dev
```

## Releasing

1. **dev → stage.** `gh pr create --base stage --head dev --title "Promote dev to stage"`. Merge with a merge commit once the five CI checks are green. Verify on stage.
2. **stage → main.** `gh pr create --base main --head stage --title "Release vX.Y"`. Merge with a merge commit.
3. **Tag it.** `git tag -a vX.Y -m "vX.Y" origin/main && git push origin vX.Y`.
4. **Deploy.** Unchanged: Actions → Deploy → type `deploy` (or `agent-release.yml`, which still watches green CI on `main`).
5. **Back-merge** `main → dev` (`gh pr create --base dev --head main`) if the release carried anything dev lacks.

## Hotfix

```bash
git fetch origin main
git switch -c hotfix/v1.0.1 origin/main
# ... the smallest fix that works ...
git push -u origin HEAD
gh pr create --base main          # ships it
gh pr create --base dev           # same branch, so dev does not regress it
```

A hotfix does not go to `stage` directly: it reaches stage on the next `dev → stage` promotion (or by a `main → stage` back-merge if stage must have it now).

## Why merge commits on `stage` and `main`

A squash creates a commit that exists only on the target. Squash `dev` into `stage` and `dev` never contains that commit, so the next promotion re-applies everything and conflicts with the last one, every release, for ever. `main.json` and `stage.json` allow **merge only**; `dev.json` allows squash for work branches. `tests/test_branch_policy.py` pins this.

## What enforces it

| Rule | Where | Mechanism |
|---|---|---|
| A PR into `main` comes from `stage` or `hotfix/*` only | `tools/ci/branch_policy.py`, run by `.github/workflows/branch-policy.yml` | required check **Branch policy (promotion path)** |
| A PR into `stage` comes from `dev` (or a `main` back-merge) only | same | same |
| A PR into `dev` comes from anything except `stage` | same | same (advisory on `dev`, not required) |
| No direct push, no force push, no deletion, the five CI checks green | `.github/rulesets/{main,stage,dev}.json` | GitHub rulesets — **an admin must apply them** (see `.github/rulesets/README.md`) |
| Only the lead (the repository admin) merges into `main`, `stage` and `dev`; every PR asks the code owner `@darshani8` for an approval | `.github/rulesets/lead-review.json`, `.github/CODEOWNERS` | GitHub ruleset (`update` rule, admin bypass on pull requests only) — **an admin must apply it** |
| Only the repository owner runs Deploy, CDK deploy and Ops task | the first step of `deploy.yml`, `cdk-deploy.yml`, `ops-task.yml` | `github.triggering_actor` checked against the owner |
| CI runs on pushes to all three long-lived branches | `.github/workflows/ci.yml` | `on.push.branches` |
| Only `main` deploys to production | `deploy.yml`'s OIDC role (`githubDeployRef`, `refs/heads/main`) | AWS IAM trust policy |

Until **both** `stage` and `dev` exist on the remote, the branch-policy job passes with a notice that it is not enforcing — otherwise every PR into `main` would be refused before there was anywhere else to send it.

**There is no staging *environment* yet.** `stage` is a gate branch: it holds the release candidate and runs CI, but nothing deploys it. Standing up a staging stack is an infrastructure decision (a second `reep-core`, its own database and secrets) and is deliberately not part of this change; `docs/phase4-staging-runbook.md` is the runbook for when one exists.

## One-time setup (a repository admin)

```bash
git fetch origin main
git push origin origin/main:refs/heads/dev
git push origin origin/main:refs/heads/stage
for b in main stage dev lead-review; do
  gh api -X POST repos/darshani8/reep-/rulesets --input .github/rulesets/$b.json
done
```

Keep `main` the **default branch**: `agent-release.yml` (`workflow_run`), the scheduled workflows and `main.json`'s `~DEFAULT_BRANCH` all read it. Contributors pass `--base dev` instead (CONTRIBUTING.md and the `ship` skill say so).
