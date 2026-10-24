# tools/ci — the check CI runs, the copy you run first, and the one that turns CI into a gate

Eight files, and the difference between the KINDS matters more than the count.
Five are executed by a workflow on every push and every pull request:
`check_api_imports.py`, `check_pii_gate.py` and the three design-system guards
(`check_brand_magenta.py`, `check_style_duplicates.py`, `check_theme_tokens.py`)
— and a sixth lives next to the code it reads, at
`apps/api-py/tools/ci/check_capability_enforcement.py`. Two are executed by a
developer on a laptop before pushing and are advisory by construction. One is
executed by a repository admin, once, from a terminal, and is the reason the
others can fail anything at all.

This file documents the three in the second and third groups in depth; the
design-system guards are described where the rule they defend lives
(`AGENTS.md`, "Frontend conventions").

Every claim in this file was checked against the scripts on 2026-09-13. Where a
script does *not* do something a reader would reasonably assume, that is written
down here rather than left to be discovered.

| file | what it proves | who runs it | can it block a merge? |
|---|---|---|---|
| `check_api_imports.py` | `app/` imports nothing `requirements.txt` fails to declare | CI job **API (dependency completeness)**, every push to `main` and every PR | **not yet** — the job fails, but no check is *required*: `main` has no protection (see `protect-main.sh`) |
| `preflight.sh` | all five CI jobs, run locally, before you push | a developer, by hand | **no**, and it is not meant to — it is invoked by nothing |
| `preflight.ps1` | nothing of its own — it finds `bash` and hands `preflight.sh` the arguments | a developer on Windows | **no** |
| `protect-main.sh` | nothing; it *applies* branch protection to `main` | a repository admin, by hand, with `gh auth login` | **no** — but every other row's ability to block comes from it |

**`bash -n` on both shell scripts exits 0** (`bash -n tools/ci/preflight.sh`,
`bash -n tools/ci/protect-main.sh`). That is a parse check, not a behaviour
check; what each one actually does is below.

---

## `check_api_imports.py`

Installs nothing itself. CI builds an environment from `apps/api-py/requirements.txt`
**alone** — deliberately not `-dev`, because that is the environment the
Dockerfile builds — and this script then walks every module under `app/` with
`pkgutil.walk_packages` and imports it.

Run it yourself the same way any time you add a dependency:

```
cd apps/api-py && python ../../tools/ci/check_api_imports.py
```

It exists because `app/interview_local.py` reached `main` importing numpy with
no entry in any requirements file. The import is lazy — inside a request handler
— so the API still booted, every test still passed, and review saw nothing. The
break surfaced as a pytest *collection* failure on a clean machine. A lazy
import does not make an undeclared dependency acceptable; it moves the crash
from boot to the first student who reaches that path.

It is import-based rather than an AST scan on purpose. Walking `import`
statements cannot separate a third-party package from a first-party or stdlib
module without reimplementing the resolver, and it never sees
`importlib.import_module(name)`. Importing asks the question the runtime asks.

It had a sibling, inline in `.github/workflows/ci.yml` rather than here: a
**Voice worker (dependency completeness)** job that loaded `voice_agent.py` from
a fresh py3.12 environment built from `requirements-voice.txt` alone. Same shape
of bug, same shape of proof — that manifest once declared only `livekit-agents`
while the worker imported four more packages, and it worked locally only because
those had been pip-installed by hand for something else.

**That job went with the LiveKit stack in 2026-09**, and what it left behind is
the reason `protect-main.sh` greps `ci.yml` and a test now compares four files:
`.github/rulesets/main.json` went on asking for `"Voice worker (dependency
completeness)"` for months afterwards. Had that ruleset ever been applied, every
pull request would have blocked on a check that could never report — a job is
matched by its DISPLAY NAME as a string, so deleting one does not retire its
requirement.

## `preflight.sh`

All five CI jobs, run on your machine, in the order that fails fastest:

```
./tools/ci/preflight.sh
```

It is the same commands `.github/workflows/ci.yml` runs, not a paraphrase: it
exports `REEP_REQUIRE_DB=1`, applies `alembic upgrade head`, runs `python -m
app.seed`, then `pytest`, and on the web side runs `tsc --noEmit`, `ng test
--watch=false` and `ng build`. Checks are named exactly as the CI jobs are named
so a red line here and a red job there are recognisably the same thing.

```
./tools/ci/preflight.sh --quick         # the fast checks only; seconds
./tools/ci/preflight.sh --keep-going    # run everything even after a failure
./tools/ci/preflight.sh --clean-deps    # dependency checks in a THROWAWAY venv, as CI does
./tools/ci/preflight.sh --npm-ci        # npm ci first, as the Web job does
./tools/ci/preflight.sh --skip-db-setup # do not re-run migrations and the seed
./tools/ci/preflight.sh --help
```

**Read the exit code, not the last line.** `0` is all green; `1` is a real
failure; **`2` means nothing failed but something did not RUN**, which is not the
same as passing and is the distinction the whole script is built around. It is
the same distinction `REEP_REQUIRE_DB=1` draws in `apps/api-py/tests/conftest.py`
and for the same reason: a silent skip reports success having verified nothing.
Every check is recorded as `PASS`, `FAIL`, `SKIP` or `PARTIAL`, and a stopped run
still records the checks it never reached rather than leaving them blank.

Two caveats the script prints and this file repeats, because they are the two
ways a green run here can precede a red run there:

- **Without `--clean-deps`, the two dependency-completeness checks run in your
  existing venvs** — which already contain packages no manifest declares. That is
  a weaker question than CI's. `--clean-deps` builds a throwaway virtualenv from
  the manifest alone and downloads the full dependency set, which is slow and is
  the honest version.
- **`postgres` must be up** (`docker compose up -d`, host port 5433) or the
  "API (FastAPI + Postgres)" check reports `SKIP` and the run exits 2. CI runs
  that job whether you did or not.

**It is not a gate and cannot become one.** It is invoked by no workflow, its
exit code is read by nobody but you, and `git push` does not consult it. The
authority is the required status checks on the pull request — once those exist.

### What it does *not* run

Named here because the gap is the useful part of this section. `preflight.sh`
runs **five** checks because `ci.yml` defines five jobs — and that matched only
from Phase 5. It ran four for months, arguing in its own usage text that
`Infra (CDK synth guards)` "needs its own Python 3.12 environment under
infra/cdk and only matters when infra/ is touched". Both halves were true and
neither made it optional: it is a REQUIRED status check, so it runs on every
pull request whether `infra/` was touched or not, and a red one blocks a merge
about something else entirely. A local runner that covers four fifths of the
gate is worse than one that covers none — it teaches you to trust it and then
lets you push into the fifth. `apps/api-py/tests/test_codebase_guards.py` now
fails the build if `ci.yml`, `.github/rulesets/main.json`, `protect-main.sh` and
`preflight.sh` ever name different checks.

What is still missing from all five is schema hygiene. Nothing runs
`alembic check`, nothing asserts `alembic heads` prints one row, nothing attempts
the `downgrade -1 && upgrade head` round trip, and there is no secret scan, no
`git check-ignore` assertion and no formatter. Those are steps the process
documents ask for and **no workflow performs them today** — so preflight is not
lagging CI, it matches it, and both are silent on the schema mistake that is the
most common one in this repository (a model changed with no revision, or two
heads after a rebase). Check those by hand until a job exists:

```
cd apps/api-py && python -m alembic check && python -m alembic heads
```

## `preflight.ps1`

A wrapper, deliberately not a second implementation: it locates `bash` and
forwards every argument and the exit code unchanged. A PowerShell port would be a
third copy of the same five commands, and the copy that drifts from `ci.yml` is
always the one nobody runs often enough to notice.

```
.\tools\ci\preflight.ps1
.\tools\ci\preflight.ps1 --quick --keep-going
```

It looks for `$env:REEP_BASH` first, then Git for Windows located from `git`
itself, then the usual install paths, then any `bash` on `PATH` **except**
`System32\bash.exe` — that one is the WSL launcher, and running the script inside
a Linux filesystem view makes it invoke `apps/api-py/.venv/Scripts/python.exe`,
producing a failure that reads as a broken preflight rather than as the wrong
shell. With no bash found it exits **69** and prints install instructions.

That fallback lists all five checks by hand, each labelled with the CI job it
stands in for, and keeping it honest has already gone wrong twice.

It listed three of four once, missing **"Voice worker (dependency
completeness)"** — the check whose manifest actually shipped incomplete, and the
one this repository added a guard for first. A Windows developer with no Git Bash
who followed it ran three checks believing they had run all four: the exact
SKIP-versus-PASS confusion exit code 2 exists to prevent.

Then that job was DELETED with the LiveKit stack in 2026-09 and this fallback was
not touched, so until Phase 5 it told a Windows developer to run `voice_agent.py`
out of `.venv-voice` — a deleted file, out of a venv nothing creates, against a
manifest that no longer exists — while saying nothing about Rule 1 or the CDK
guards, which were two of the five things actually gating their merge. (It also
printed `cd ..` followed by a literal BEL byte where `\a` in `..\api-py` had been
eaten by an escape somewhere: the instruction beeped the terminal instead of
changing directory.) Instructions rot in exactly the direction of the deletion
that made them wrong, which is why the check-name agreement is now a test rather
than a habit — see `apps/api-py/tests/test_codebase_guards.py`.

## `protect-main.sh`

Applies classic branch protection to `main` on `github.com/darshani8/reep-`
through `gh api -X PUT repos/{owner}/{repo}/branches/{branch}/protection`: the
five CI jobs required by their exact display names, `strict` (up-to-date)
branches, a pull request required, stale approvals dismissed, conversation
resolution required, force pushes and deletion blocked, and — by default — the
rules applied to administrators.

```
./tools/ci/protect-main.sh --show                # what is live right now
./tools/ci/protect-main.sh --dry-run             # print the exact payload, send nothing
./tools/ci/protect-main.sh --approvals 0         # the configuration this repo actually wants
./tools/ci/protect-main.sh --approvals 0 --yes   # same, non-interactive, for a runbook
```

Other flags: `--allow-admin-bypass` (sets `enforce_admins: false`),
`--skip-name-check`, `--repo owner/name`, `--branch name`, `--help`.

**Nothing has been applied.** As of this file,
`gh api repos/darshani8/reep-/branches/main/protection` returns **404** and
`/rulesets` returns **`[]`**. The script exists; it has not been run. Until an
admin runs it, every job in `ci.yml` is advisory and `git push origin main`
writes straight to the branch `deploy.yml` ships from. Nothing in this
repository can change that on its own — branch protection is an admin-only API,
and see below for why no workflow should hold a token that could call it.

**There are now two routes to the same control, and you must take only one.**
`.github/rulesets/main.json` is the *ruleset* form of this configuration —
`gh api -X POST repos/darshani8/reep-/rulesets --input .github/rulesets/main.json` —
and it requires the same five checks with `required_approving_review_count: 0`
and `bypass_actors: []`. Classic protection (this script) and rulesets are
separate systems that GitHub evaluates together, most-restrictive-wins. Applying
both leaves two places a control can be relaxed and one of them is the place
nobody reads. This script detects the collision only *after* a `PUT`: it re-reads
the live state, finds it is not what was requested, warns and exits 1. That is a
diagnosis, not a defence. Pick the ruleset for reviewability or the script for
the pre-flight name check, say which in the commit body, and leave the other
alone.

**The default is not the configuration you want, and that is the one thing to
know before the first run.** The script defaults to `APPROVALS=1` and
`ENFORCE_ADMINS=true`. On this repository —
`gh api repos/darshani8/reep-/collaborators` returns exactly one account — that
combination makes `main` unmergeable by anyone: GitHub does not let an author
approve their own pull request, and `enforce_admins` removes the override. Its
own header spends thirty lines explaining this and telling you to pass
`--approvals 0`; the bare invocation applies the configuration the header argues
against. **Pass `--approvals 0`.** Zero approvals still *requires a pull
request* — it is the `required_pull_request_reviews` block, not the count, that
forces the branch-and-check path — so the route is still branch → PR → four
green checks → merge, with the count deciding only whether a second human is in
it. Add `--approvals 1` on the day a second collaborator exists.

**The four required check names are matched as strings** against the `name:`
values in `ci.yml`. Rename a job without re-running this and the renamed job
keeps running, keeps passing, and stops being required, with nothing saying so —
while the branch waits forever for a check that will never report. The script
greps `ci.yml` for all four names and refuses rather than pinning a check nothing
can satisfy; `--skip-name-check` overrides that, and there is no good reason to.

**CI never runs this, and it must not.** A token that can apply branch
protection can remove it, and a workflow holding that token is a bypass with a
friendly name. It is a human action with `gh auth login` and admin on the
repository; the script checks `permissions.admin` up front and prints the exact
missing scope (`repo` on a classic PAT) rather than failing half way through a
`PUT`.

**Re-running converges rather than accumulating.** The endpoint is a full-state
idempotent `PUT`, so a second run replaces the whole protection object instead of
layering half-remembered settings. After applying, the script re-reads the live
state and compares it with what was requested; if they differ it **warns and
exits 1**, because the usual cause is a repository *ruleset* also in force on the
branch — GitHub evaluates both and the most restrictive wins, and two overlapping
sources of truth is how one gets relaxed while the other is the one everybody
reads. Pick one and say which.

Its final instructions ask you to verify from the other side —
`git push origin main` must be refused with *"Changes must be made through a pull
request"* — and to record the activation date in `SECURITY.md`. **There is no
`SECURITY.md` in this repository** (`ls SECURITY.md`); either write one or record
the date somewhere that exists, because a control nobody can point at the moment
it was turned on is a control nobody can prove was ever on.

---

## Adding a script here

List it in the table above with the job or the person that runs it, or it becomes
a file nobody can tell is live. That is the same failure the two
dependency-completeness jobs exist to prevent, one level up: a thing that looks
maintained, runs nowhere, and is discovered on the day it was supposed to have
been protecting something. `preflight.sh` sat in this directory unmentioned by
this table, by `CONTRIBUTING.md` and by the pull-request template — an advisory
script nobody is told about is not advisory, it is dead.

Not everything mechanical belongs in this directory. Checks a fresh runner can do
in one line — `gitleaks detect`, `git check-ignore` assertions, `npx prettier
--check` — belong inline in `.github/workflows/ci.yml` where the job that runs
them is readable in one place. **No such job exists yet**; `ci.yml` defines four
jobs and none of them is a hygiene job, so treat every reference to a "Repo
hygiene (secrets, ignores, format)" check elsewhere in the documentation as a
description of work not done. `.pre-commit-config.yaml` and `.gitleaks.toml` are
in the tree and are the local half of that scan — a hook, in `.git/`, inert until
someone runs `pre-commit install` and skipped by `--no-verify`. The authoritative
copy is still the job that has not been written. A file here earns its
place by being long enough to need explaining, or by needing to run identically on
a laptop and on a runner.
