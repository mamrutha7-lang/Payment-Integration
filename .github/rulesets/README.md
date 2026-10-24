# `.github/rulesets/` — the committed record of a setting that is not a file

## Status: **[ADMIN — NOT YET APPLIED]**

`main.json` is a payload. It is not protection. Nothing in this directory
changes anything about this repository until a person with admin rights runs
the command below, and as of this commit nobody has:

```
$ gh api repos/darshani8/reep-/branches/main/protection
gh: Branch not protected (HTTP 404)

$ gh api repos/darshani8/reep-/rulesets
[]

$ gh api repos/darshani8/reep-/rules/branches/main
[]
```

Those three were run at the shell before this file was written, not quoted from
somewhere. Run them again before you believe this heading either way.

So: `main` accepts a direct push right now. `git push origin main` writes
straight to the branch `deploy.yml` ships from, all four CI jobs are advisory,
and every gate the process documents in this repository stands behind that one
open door. Read anything in `CONTRIBUTING.md` that speaks of `main` being
protected as describing the state *after* this file is applied.

## Four rulesets: one per long-lived branch, and one for who merges

`main.json`, `stage.json` and `dev.json` implement `docs/branching-strategy.md`.
All three demand a PR, the five CI checks, no force push and no deletion.
`main` and `stage` additionally require **Branch policy (promotion path)**
(`.github/workflows/branch-policy.yml`) and allow **merge commits only**.
`tests/test_codebase_guards.py` §34 compares all three against `ci.yml`. They
have **no bypass**: they bind the owner exactly as they bind everybody else.

`lead-review.json` (2026-10-15) is the fourth, and it exists because the
repository gained a second collaborator with write access. It covers the same
three branches and says two things:

- **Only the repository admin merges.** Its `update` rule refuses every update
  to `main`, `stage` and `dev` from anybody who cannot bypass it, and the one
  bypass is the admin role in `pull_request` mode: a merge through a PR, never a
  direct push. A collaborator opens PRs and pushes their own branches, and the
  merge button stays shut for them even after an approval. The decision to
  merge is the lead's (or Claude's, acting in the lead's own session, which
  merges as the lead).
- **Every PR asks the code owner.** One approval, from a code owner (the
  catch-all in `.github/CODEOWNERS` is `@darshani8`), on the latest push. With
  the `update` rule above this records the review rather than gating the merge,
  and it is what makes GitHub request the owner's review on every collaborator
  PR.

It is a separate ruleset, and not more rules in the other three, because a
bypass is per ruleset. Put in `main.json`, the admin bypass would also lift the
five CI checks and the force-push ban for the admin. Kept apart, the admin
skips only the approval and the merge restriction, and still cannot merge red
CI or push directly. `tests/test_repo_access.py` pins that split.

**Merging as the admin.** Your own PRs (and the PRs Claude opens in your
sessions, which are authored by your account) cannot be approved by their
author. GitHub shows **Merge without waiting for requirements to be met
(bypass rules)** under the merge button; tick it. It bypasses
`lead-review.json` only. If CI is red the button stays disabled, because
`main.json` has no bypass. Merge these by hand rather than with auto-merge,
which waits for the requirements to be met, and a requirement the author
cannot meet is never met.

## Applying it

All four, once, by the admin (`stage` and `dev` must exist as branches first,
or a ruleset naming them applies to nothing):

```bash
for f in main stage dev lead-review; do
  gh api -X POST repos/darshani8/reep-/rulesets --input .github/rulesets/$f.json
done
```

Without `gh`, in the browser: **Settings → Rules → Rulesets → New ruleset →
Import a ruleset**, and import each of the four files in turn.

Then confirm it took, because a POST that returns 201 and a ruleset that
actually gates are not the same claim:

```bash
gh api repos/darshani8/reep-/rulesets --jq '.[] | "\(.id)  \(.name)  \(.enforcement)"'
gh api repos/darshani8/reep-/rules/branches/main --jq '[.[].type]'
```

The second command is the one worth trusting: it asks GitHub which rules apply
*to `main`* rather than which rulesets exist, so a ruleset created against the
wrong ref pattern shows up as an empty list rather than as a green 201.

To change it later, edit `main.json`, then **PUT** to the existing ruleset —
POSTing again creates a second ruleset, and two rulesets on one branch both
apply, which is how a repository ends up with a rule nobody remembers adding:

```bash
gh api -X PUT repos/darshani8/reep-/rulesets/<id> --input .github/rulesets/main.json
```

`tools/ci/protect-main.sh` applies the older *classic branch protection* API
instead. The two mechanisms coexist and both are enforced, so applying both
gives `main` two overlapping sets of rules with two places to look when
something is blocked. Pick one. This file is the ruleset form; the script is the
classic form.

## Why the payload says what it says

**`required_approving_review_count: 0` in the three branch rulesets.** The
approval now lives in `lead-review.json` (above), where the admin can bypass
it. In the branch rulesets, which nobody bypasses, `1` would still make the
owner's own pull requests unmergeable, for the reason that follows. The
paragraph below is the record of why this was zero when the repository had
one collaborator.

**Why it was zero, and still is in the branch rulesets.**
`gh api repos/darshani8/reep-/collaborators` returns one account. GitHub does
not allow an author to approve their own pull request, so `1` would make `main`
unmergeable by anyone — not strict, *unmergeable*. A rule that literally cannot
be satisfied does not get followed; it gets deleted by whoever needs to ship
that afternoon, and the four required checks go out with it. So the approval
count is zero, and the enforcement is carried entirely by the checks. What zero
still buys is the shape: a branch, a pull request, a diff with a URL, and four
jobs that must conclude `success` on the exact head being merged. That is the
whole difference between "the tests ran somewhere" and "the tests ran on this."

Raise it to `1` the day a second maintainer exists. That is a one-line edit to
this file and a re-PUT.

**Four required checks, not five.** These four names are the `name:` values in
`.github/workflows/ci.yml`, byte for byte:

| context in `main.json` | job key in `ci.yml` |
| --- | --- |
| `API (FastAPI + Postgres)` | `api` |
| `API (dependency completeness)` | `api-imports` |
| `Voice worker (dependency completeness)` | `worker-imports` |
| `Web (Angular)` | `web` |

The process documents also describe a fifth check, a repo-hygiene job. **That
job does not exist in `ci.yml`, so it is deliberately not listed here.** A
required check that no job ever reports is not a strict gate — it is a merge
button that waits forever for a status GitHub has never seen on that branch, and
the first person it blocks learns to ask for a bypass. Add the context here in
the same change that adds the job, never before it.

The same trap runs the other way and is quieter: rename `Web (Angular)` to
`Web (Angular 20)` in an ordinary pull request and the PR reports a passing
`Web (Angular 20)`, the required `Web (Angular)` is never reported at all, and
GitHub does not wait for a check it has never seen. The job still runs. The job
still goes green. It has simply stopped being a gate, and the required-check
list lives in a settings pane that no pull request reviews. **A renamed job does
not fail. It retires.** If you rename a job, change this file in the same
commit.

**`strict_required_status_checks_policy: true`** — a branch must be up to date
with `main` before it can merge. This is not tidiness. GitHub does not re-run an
open pull request's checks when its base moves, so without it two Alembic
revisions generated against the same parent each pass their own run and collide
on `main` as two heads. The migrations in `apps/api-py/migrations/versions/`
depend on strict linear ordering for a concrete reason: `create_type=False`
appears **34 times across 15 revision files**, and each of those says "the enum
already exists, because an earlier revision made it". Apply them out of order
and Postgres raises `type "x" does not exist`.

Both numbers were measured, and the `--include` matters:

```bash
grep -rho --include='*.py' 'create_type=False' apps/api-py/migrations/versions/ | wc -l   # 34
grep -rl  --include='*.py' 'create_type=False' apps/api-py/migrations/versions/ | wc -l   # 15
```

Without `--include='*.py'` the same greps return 37 and 18, because
`versions/__pycache__/` holds compiled copies of three of those revisions and
they are counted twice. If you see 37 quoted somewhere, that is where it came
from.

**`non_fast_forward` and `deletion`** block force-pushing and deleting `main`.
Force-push is what turns "we can see what happened" into "we cannot", and it is
the one action that makes an incident unreconstructable.

**`bypass_actors: []`** in `main.json`, `stage.json` and `dev.json`. Only
`lead-review.json` names a bypass, and only for its own two rules — nobody,
including the owner, including admins, bypasses these. That is
the strongest form and it has a real cost: when a required check breaks for a
reason that is not the code (an Actions incident, the `pgvector/pgvector:pg17`
service image failing to pull, a renamed job as above), `main` is unmergeable by
anyone — including for the security fix this whole apparatus exists to protect.
There is no undocumented escape here on purpose. The escape is to edit this file
or the ruleset, in the open, and say in the pull request body why the window was
opened and when it closed. An escape hatch nobody wrote down still gets used;
it just gets used without the record.

## What this file cannot do

Nothing in a repository can apply a repository setting. This directory holds a
reviewable, diffable, re-appliable record of an intended configuration, which is
worth having — you can read it, argue with it in a pull request, and re-apply it
after somebody changes it in the UI. It is not the control. **Applying it is the
control**, and applying it needs a human with admin rights and thirty seconds.
