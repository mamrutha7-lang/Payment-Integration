# Contributing to REEP

This is the answer to one question: **what do I do, in order, from editing a file to
seeing it in production.** Read `AGENTS.md` first — it is what the codebase is. This is
how a change gets through it.

## Status markers, and why this document carries them

**Most of what follows is not switched on yet.** This file describes a 22-step process,
and a document that mixes "this is enforced" with "this would be good" teaches its readers
to trust neither half. So every step below carries one of the four markers
`docs/deployment-process.md` already uses, and they mean the same thing in both files:

- **[IN FORCE]** — true on `main` today. A mechanism exists and acts.
- **[ON MERGE]** — the file that implements it **is in this branch's diff**. Not true
  until this branch merges, and true the moment it does. No admin, no settings pane.
- **[ADMIN — NOT YET APPLIED]** — a GitHub setting only the repository owner
  (`darshani8`) can turn on by hand. **No committed file turns it on.** Until it is
  applied, the step is advice. (This is `deployment-process.md`'s `[ADMIN]`, with its
  current state spelled into the name so you cannot read past it.)
- **[ASPIRATIONAL]** — nothing implements it, here or in this branch. The step is written
  down so the gap is a *known* gap rather than an assumed control, and it names the file
  that would close it.

**The rule for assigning them is mechanical, and you can check it yourself.** A step is
`[ON MERGE]` only if the artifact named in its heading appears in this branch's diff:

```bash
git status --porcelain            # untracked + modified: everything this branch adds
git diff --stat main...HEAD       # once it is committed
ls .pre-commit-config.yaml .gitleaks.toml .github/rulesets/main.json
```

If a sibling document marks something `[ON MERGE]` and `git status` does not show the
file, **`git status` wins and the sibling is stale.** That is not a hypothetical: the
previous draft of this file described nine artifacts that had never been written, in the
present tense, and the reason it survived review is that nothing in it was checkable
against the tree in one command.

## The one-line state of the repository

**`main` is not protected. Every check in this repository is advisory, and a direct
`git push origin main` bypasses all four of them.** Verified at the shell while writing
this:

```bash
$ gh api repos/darshani8/reep-/branches/main/protection
{"message":"Branch not protected", ... "status":"404"}
$ gh api repos/darshani8/reep-/rulesets
[]
```

That changes when an admin runs `tools/ci/protect-main.sh` — step 2 — and **not before,
and not by merging this branch.** Read every "blocking" in this document as "blocking once
step 2 is applied".

---

## Why there is a process at all

**Unenforced process is indistinguishable from no process.**

Every job in `.github/workflows/ci.yml` is advisory today. The content is good — a real
Postgres with pgvector, `REEP_REQUIRE_DB=1` so a silently skipped DB test is a failure, the
runtime-only import check, the bundle budget — and none of it has authority, because
`main` is unprotected (the two commands above). A `git push origin main` puts code on the
branch `deploy.yml` ships from without one check having to pass, or even to *finish*: two
of the last eight CI runs on main are `cancelled` (`c9ef4f3` and `543a265`), and 543a265
was deployed to production while its own run was still going.

**The 543a265 timeline, exactly, because this is the claim a sceptical reader checks
first:**

```bash
gh api repos/darshani8/reep-/actions/runs/33176873138 \
  --jq '{conclusion, run_started_at, updated_at}'
# {"conclusion":"cancelled","run_started_at":"2026-08-28T13:45:35Z","updated_at":"2026-08-28T13:46:34Z"}
gh api repos/darshani8/reep-/actions/runs/33176879314 \
  --jq '{conclusion, run_started_at}'
# {"conclusion":"success","run_started_at":"2026-08-28T13:45:39Z"}
```

CI run 33176873138 **started** on 543a265 at 13:45:35Z. Deploy run 33176879314 shipped the
same sha to production starting at 13:45:39Z — **four seconds later**. That CI run's
`API (FastAPI + Postgres)` job was cancelled at 13:46:33Z, **54 seconds after the deploy
had already begun**, and the run concluded `cancelled`. So the deploy did not race a run
that had already failed. It shipped a commit whose checks had not finished and never
would, and nothing in the workflow looked at them at all. That is worse than the race, and
it is step 16's entire reason for existing.

That commit changes which mentor-notebook entries cross onto a student's screen, and its
own message says the new tests fail loudly if a private working note ever appears there.
They never finished running on it.

What that costs is not abstract. Delete the `if not mentor_id` condition inside
`_assert_can_access_student` (`apps/api-py/app/routers/mentor.py:81`, the function defined
at `:72`) and `tests/test_mentee_records.py::test_a_mentor_with_no_group_sees_nobody` goes
red — rule 2 is genuinely tested. (Cited by name, not only by line: a line number is stale
after the next edit, and a test name that stops existing is one grep from being noticed.)
On an unprotected main the push lands anyway, and every MENTOR account then reads every
student's marks, attendance and USN, on a public repo. The test was never the control. The
test plus a **required** check is the control, and the "required" half is
**[ADMIN — NOT YET APPLIED]**.

**So the honest summary is this:** the steps below are the route onto `main` *once step 2
is applied*. Until an admin applies it, they are the route you take voluntarily, and
nothing stops you taking another.

---

## The short version

The branching strategy is `docs/branching-strategy.md`: `feature/*` → `dev` →
`stage` → `main`, and `hotfix/*` from `main` back into `main` and `dev`. Until
`dev` exists on the remote, read `dev` below as `main`.

```
git switch -c feature/mentor-scope-on-uploads origin/dev
# ... edit ...
tools/ci/preflight.sh                    # the four CI checks, locally, fail-fastest first
git commit                               # house shape: subject + four-beat body
git push -u origin HEAD
gh pr create --base dev                  # template asks about rule 1 and rule 2 by file path
# the four required checks go green, PR is up to date with main
gh pr merge --squash --delete-branch
# Actions -> Deploy -> Run workflow -> type "deploy"
```

Anything that is not that loop is either a one-time setup step (1, 2, 6, 17, 22) or a
thing the deploy does for you (18, 20).

**Four required check names today, not five.** `tools/ci/protect-main.sh`'s
`REQUIRED_CHECKS` array lists the four jobs that exist in `ci.yml`, verbatim:

```
API (FastAPI + Postgres)
API (dependency completeness)
Voice worker (dependency completeness)
Web (Angular)
```

Step 12 proposes a fifth. Until that job is actually in `ci.yml`, adding its name to the
required list produces a required check that is never reported — which is a merge button
that waits forever for something that will never arrive. See step 2.

---

## The gate list

`Blocking when applied` is what the step does **once its marker resolves**. It is not what
it does today; today the marker column is the whole answer.

| # | Stage | Implemented by | Marker | Blocking when applied |
|---|---|---|---|---|
| 1 | merge | two-line edit to `.github/workflows/ci.yml` | **[ON MERGE]** | no — prerequisite for 2 |
| 2 | merge | ruleset/protection on `main` | **[ADMIN — NOT YET APPLIED]** | **yes** |
| 3 | local | this document; `tools/ci/preflight.sh` | **[ON MERGE]** | no — nothing on a laptop is a gate |
| 3b | local + pr | a test in the same diff; review + the API job | rule **[ON MERGE]**, mechanism **[IN FORCE]**, automation **[ASPIRATIONAL]** | no — review, not a coverage gate |
| 4 | pre-commit | `.pre-commit-config.yaml`, `.gitleaks.toml` | **[ON MERGE]** | no — `--no-verify` skips it |
| 5 | pre-commit | the `.gitignore` env allowlist | **[ON MERGE]** | no — `git add -f` defeats it |
| 5b | pre-commit | a root `.gitattributes` | **[ON MERGE]** | no — git applies it at checkout; nothing rejects a bad commit |
| 6 | pre-commit | secret scanning, non-provider patterns | **[ADMIN — NOT YET APPLIED]** | **yes** — rejects the push |
| 7 | pr | `.github/pull_request_template.md` | **[ON MERGE]** | no — a checkbox is not a gate |
| 8 | pr | the four existing jobs in `ci.yml` | jobs **[IN FORCE]**, required-ness **[ADMIN — NOT YET APPLIED]** | **yes** |
| 9 | pr | three `run:` steps inside "API (FastAPI + Postgres)" | **[ASPIRATIONAL]** | **yes** |
| 9b | pr | `ruff --select F` step inside the same job | **[ASPIRATIONAL]** | **yes** |
| 10 | pr | `apps/api-py/tests/test_rule1_call_sites.py` | **[ASPIRATIONAL]** | **yes** |
| 11 | pr | `apps/api-py/tests/test_route_gates.py` | **[ASPIRATIONAL]** | **yes** |
| 12 | pr | job "Repo hygiene (secrets, ignores, format)" | **[ASPIRATIONAL]** (its `.gitleaks.toml` is **[ON MERGE]**) | **yes** |
| 13 | review | `.github/CODEOWNERS` | **[ON MERGE]** | no — one collaborator today |
| 14 | merge | strict required checks + `delete_branch_on_merge` | **[ADMIN — NOT YET APPLIED]** | **yes** |
| 15 | release | immutable ECR tags + pinned task definition | **[ASPIRATIONAL]** | **yes**, by AWS |
| 16 | deploy | `ci-green` job in `deploy.yml` | **[ASPIRATIONAL]** | **yes** |
| 17 | deploy | `production` GitHub Environment | **[ADMIN — NOT YET APPLIED]** | **yes** |
| 18 | deploy | pre-migration RDS snapshot step | **[ASPIRATIONAL]** | **yes** |
| 19 | deploy | `web` needs `api` | **[ASPIRATIONAL]** | **yes** |
| 20 | post-deploy | `verify` job | **[ASPIRATIONAL]** | no — the code already shipped |
| 21 | post-deploy | `.github/workflows/restore-rehearsal.yml` | **[ASPIRATIONAL]** | no — it reports |
| 22 | post-deploy | `.github/dependabot.yml` + admin toggle | **[ASPIRATIONAL]** + **[ADMIN — NOT YET APPLIED]** | no — it opens a PR |

**Count the markers before you quote the table**, because the tally is the argument. Of
its 25 rows: **six** land with this branch and need nobody's permission; **four** are
purely a GitHub setting; **twelve** are specifications with no implementation anywhere;
and **three** are mixed — step 3b, step 8 (the jobs run, their being *required* does
not), and step 22 (a file plus a toggle). Exactly one row describes a mechanism that
acts today, step 8's four CI jobs, and it acts without authority. **Nothing in this
table blocks anything at the moment you read it.** Recount it yourself by reading the
marker column; that is what the column is for, and the whole point of it is that the
answer comes from `ls` and `gh api` rather than from trusting this file.

**Why the numbering has a `3b` and a `5b` rather than a 23 and a 24.**
`docs/deployment-process.md` refers to "step 15 of the development process" by number, and
a step number is a string another file matches on — exactly the failure mode step 2
describes for required check names. Renumbering retires those references in silence. New
steps are inserted with a letter.

**Keep the required list short on purpose.** Every mechanical gate here —
`alembic check`, the single-head assertion, the downgrade round trip, the rule-1 AST scan,
the rule-2 route sweep — is a *step* or a *test file* inside the existing
"API (FastAPI + Postgres)" job, not a new job. That is deliberate: each name on the
required list is another thing that can go stale, be renamed, or sit amber forever, and
the required list is edited in a settings pane that no pull request reviews.

---

## 1. Scope CI's cancellation to pull requests

*Stage: merge. One-time.* **[ON MERGE]** — the edit to `.github/workflows/ci.yml` is in
this branch's diff. It has **not** landed on `main`: `ci.yml:17` there still reads
`cancel-in-progress: true`.

**What you do.** In `.github/workflows/ci.yml`:

```yaml
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

**Why it is first.** `cancel-in-progress: true` cancels an in-flight run on *every* ref,
main included, and a `cancelled` check is neither success nor failure. Turn on required
checks without this and the next burst of pushes leaves main wearing a check that never
resolves — which reads as a flake, and the first person blocked by it goes looking for the
bypass. AGENTS.md says exactly this about the boot guard: *a guard that trips on a laptop
gets deleted by whoever is trying to ship that afternoon.* Superseding a run is still right
on a PR branch, where the head commit is disposable.

**Do this before step 2, and check it rather than assuming it.**
`tools/ci/protect-main.sh`'s header tells its operator the same thing. An earlier draft of
this file said the edit had "already landed — it was the last change that went to main as
a direct push". It never landed, and an admin who believed that sentence would have
applied protection over an unscoped `cancel-in-progress`, producing exactly the
permanently-amber required check this step exists to prevent. The sentence is deleted; the
edit lands with this branch.

```bash
grep -n cancel-in-progress .github/workflows/ci.yml     # must not read plain `true`
```

**What stops you if you skip it.** Nothing — this is not itself a gate. It is the
prerequisite that makes step 2's gate *resolvable*.

## 2. Put a ruleset on `main`

*Stage: merge. One-time.* **[ADMIN — NOT YET APPLIED]** — a GitHub setting. `main` is
unprotected as of this commit; `gh api repos/darshani8/reep-/branches/main/protection`
returns 404 and `/rulesets` returns `[]`. **Merging this branch does not change that.**

**Since 2026-10-15 there are four rulesets, not one.** The repository has a second
collaborator, and `.github/rulesets/lead-review.json` lets only the admin merge into
`main`, `stage` and `dev`. `.github/rulesets/README.md` says how to apply all four, and
it is the authority over this step where they differ.

**What an admin does.** Apply protection to `main`:

- require a pull request before merging, **approvals: 0**
- require these status checks, by exact name — the four that exist in `ci.yml`:
  `API (FastAPI + Postgres)`, `API (dependency completeness)`,
  `Voice worker (dependency completeness)`, `Web (Angular)`
- require branches to be **up to date** before merging (strict)
- block force pushes, block deletion
- **no bypass actors**

`Repo hygiene (secrets, ignores, format)` is **not** on that list, because step 12's job
does not exist in `ci.yml`. Add the fifth name in the same change that adds the job, never
before it.

**The command.**

```bash
./tools/ci/protect-main.sh --dry-run           # print the exact payload, send nothing
./tools/ci/protect-main.sh --approvals 0       # apply it
./tools/ci/protect-main.sh --show              # read back what is live
```

`.github/rulesets/main.json` **[ON MERGE]** is the committed record, so the configuration
is reviewable and re-appliable. **The file is not the control — applying it is.** A ruleset
JSON sitting in git protects nothing, and this is the distinction the whole marker
vocabulary above exists to keep visible. Read `tools/ci/protect-main.sh`'s header before
running it: it applies *classic* branch protection (a single idempotent full-state `PUT`,
so re-running converges), and if you apply both mechanisms GitHub evaluates both and the
most restrictive wins — pick one as the source of truth and say which in `SECURITY.md`.

**`--approvals 0` is not laziness, and the script defaults to 1 for a reason.**
`gh api repos/darshani8/reep-/collaborators --jq 'length'` returns **1**. With
`enforce_admins` on and one collaborator, requiring an approval means *nobody can merge
anything*: GitHub does not let an author approve their own PR. Zero approvals still refuses
a direct push, still refuses a red merge, and still forces branch → PR → green checks →
merge, which is where the verification actually lives. Raise it to 1 the day a second
maintainer exists. The configuration to avoid is the third one: an approval required with
admins exempt, which reads as protected and behaves as unprotected for the only account
that can push.

**The check names are strings, and that is the sharp edge.** GitHub matches a required
check by the job's **display name** (`name:` in `ci.yml`), not its YAML key. Rename
`Web (Angular)` to `Web (Angular 20)` and the PR reports a green `Web (Angular 20)` while
the required `Web (Angular)` is never reported at all — and a required check that has never
been reported on a branch is one GitHub has nothing to wait for. The job still runs, still
passes, and has silently stopped being a gate. `protect-main.sh` greps `ci.yml` before it
calls the API for exactly this. **Rename a job and re-run the script in the same change.**

**What stops you if you skip it.** Nothing stops *anything*. This is step zero of the whole
design; every other control in this repository is advisory by one cause, and this is the
cause. It is also the one step in this document that no amount of committed file can
supply, which is why it is the only marker that names a person.

## 3. Branch, run the checks locally, write the commit

*Stage: local.* **[ON MERGE]** for `tools/ci/preflight.sh`; the rest is convention, and
nothing on a laptop can stop a commit.

**Branch from main**, named `type/kebab-subject`, using the same type vocabulary as the
commit subjects below:

```bash
git switch main && git pull
git switch -c fix/mentor-scope-on-uploads
```

Existing branches follow this: `feat/dev-password-sign-in`,
`harden/voice-stack-architecture-review`, `chore/mandatory-development-process`.
Machine-generated `claude/*-abc123` branches exist in the remote history; do not add more.

**Run the checks before you push.** `tools/ci/preflight.sh` **[ON MERGE]** runs the same
commands `ci.yml` runs, in the order that fails fastest, and distinguishes *failed* from
*did not run* with a separate exit code — because a check that did not run is not a check
that passed. It is not a gate and its exit code is read by nobody but you; the authority is
the required status checks on the pull request.

```bash
tools/ci/preflight.sh              # everything
tools/ci/preflight.sh --quick      # the two fast dependency checks only — exits 2, NOT sufficient for a PR
```

The two underlying commands, if you would rather run them by hand, are the two AGENTS.md
documents:

```bash
cd apps/api-py && .venv/Scripts/python -m pytest
cd apps/web    && npx ng build
```

`ng build` is not decoration — it is where the production bundle budget is enforced, and one
route slipped back to a static `component:` in `app.routes.ts` fails it. If you touched
`voice_agent.py` or either requirements file, also do what the dependency-completeness jobs
do, in a scratch venv, because a package you installed by hand months ago is invisible to
you and fatal on a clean runner.

### The commit message

The house shape is a **subject naming the outcome or the failure fixed**, then a body with
four beats:

1. **the symptom, as a user would report it** — "A mentor publishes an entry and the
   student sees nothing, with no error on either screen."
2. **the mechanism** — the actual cause, named in files and functions.
3. **why this fix and not the obvious alternative** — "Merged server-side, in the endpoint
   the screen already calls, rather than teaching the client to read two APIs."
4. **what was deliberately NOT done, and why** — "`app.seed` is deliberately not offered
   here and never should be."

Subjects are `type: lowercase phrase`, optionally scoped: the types in use are
`feat`, `fix`, `docs`, `refactor`, `chore`, `ci`, `infra`, `security`, `test`, with scopes
like `fix(web):`, `fix(api):`, `feat(deploy):`. Step 4's `conventional-pre-commit` hook and
step 12's CI mirror check that list. A subject describes the *outcome*, not the diff:

```
fix: a published notebook entry now reaches the student who it is about
infra: stop CloudFront turning API refusals into blank pages
security: fail closed when first Google pin cannot commit
```

not `Refactor models and API endpoints`, `Fix indentation in test cases`, or `Modify
backoff handling in security.py` — all three are real commits in this history and none of
them says what changed for anyone.

**This convention is not decoration.** AGENTS.md's rules were *written from those bodies*.
The paragraph explaining why `app.seed` refuses on `ENV=prod`, the one explaining why the
password login guard is an allowlist and not `not is_prod` — those are commit bodies that
graduated. Measured on this history:

```bash
git log -120 --pretty=format:'%H%n%b%x00' | \
  python -c "import sys;d=sys.stdin.read().split('\x00');print(sum(1 for r in d if r.strip() and '\n'.join(r.strip('\n').split('\n')[1:]).strip()==''))"
# 44
```

**44 of the last 120 commits carry no body at all.** (An earlier draft said 67; nothing in
the history produces 67, and the argument never needed the larger number.) For those 44,
the reasoning behind the next guard someone is tempted to delete is simply not recorded
anywhere. When it is missing, the guard looks like an obstacle.

If a session assistant co-authored the change, keep the trailers the history already uses:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_...
```

**What stops you if you skip it.** The hook in step 4 rejects a subject with no recognised
type once `.pre-commit-config.yaml` lands, and step 12's CI mirror would reject it again on
the PR once that job exists. *Nothing anywhere checks that the body exists.* That half is
you, and it always will be.

## 3b. Ship the test that would have caught it

*Stage: local, then pr.* **[ON MERGE]** as a rule — this document is the only thing that
states it. **[IN FORCE]** for the one mechanism it leans on, the pytest job that already
runs whatever tests exist. **[ASPIRATIONAL]** as anything automatic: there is no coverage
gate, no diff-coverage bot, and this step does not propose one.

**The omission this closes.** Nothing else in these 22 steps requires a change to arrive
with a test. Every mechanical gate in the list checks *shape* — that a keyword is present,
that a migration exists, that a name is still ignored, that a manifest is complete. None of
them checks that the behaviour you just changed is pinned by anything. A repository can
pass all four required checks with a diff that adds a feature and not one assertion about
it, and that is the ordinary case, not the exotic one.

**The rule.** A change that alters behaviour lands with a test that fails without it. In
practice, three shapes:

- **A bug fix** ships the test that reproduces the bug first. If you cannot make it fail
  against the old code, you have not found the bug yet.
- **A new endpoint or a new gate** ships its access test, and for anything under rule 2
  that means the three-case matrix the repo already uses: in-group, out-of-group, and
  **no group at all** — `tests/test_mentee_records.py::test_a_mentor_with_no_group_sees_nobody`
  is the model, because "no mentor group" read as "no filter" is the failure AGENTS.md
  rule 2 names explicitly.
- **A guard, a refusal, or a boot check** ships both halves: that it trips when it should,
  and that it stays silent when it should not. `tests/test_boot_guard.py` pins the
  development-`ENV` silence as hard as it pins the production refusal, for the reason
  AGENTS.md gives — a guard that trips on a laptop gets deleted by whoever is trying to
  ship that afternoon.

**Where the test goes.** Backend tests are `apps/api-py/tests/test_*.py`; there are **39**
of them (`ls apps/api-py/tests/test_*.py | wc -l`), they run against a real
`pgvector/pgvector:pg17` service in CI, and `REEP_REQUIRE_DB=1` makes a silently skipped DB
test a failure rather than a pass. Use `@requires_db` for anything that touches the
database and the `login` fixture in `tests/conftest.py` for anything that needs a session.

**Be honest about what enforces this: review, plus the fact that the test you wrote runs.**
There is no `--cov-fail-under`, no diff-coverage bot, and no check that counts assertions.
The PR template asks the question; the API job runs whatever you wrote. A reviewer — which
on a one-collaborator repository means you, in the PR view, deliberately — is the only
thing that notices a diff with no test in it. That is a weaker control than every
`[ADMIN]` row in this document, and it is stated here rather than dressed up because the
alternative is a ratchet introduced on the same day as branch protection, which fails the
first PR after this lands on a number nobody chose. See "Known gaps" for the measured
coverage position and the order in which it should be fixed.

**What stops you if you skip it.** Nothing mechanical. The API job will run the tests that
exist; it cannot know about the one you did not write.

## 4. Install the pre-commit hooks

*Stage: pre-commit.* **[ON MERGE]** — `.pre-commit-config.yaml` and `.gitleaks.toml` are in
this branch's diff. Not a control either way: hooks live in `.git/`, are not cloned, and
`--no-verify` skips them.

**The command**, once per clone, after this branch merges:

```bash
pip install pre-commit
pre-commit install --install-hooks -t pre-commit -t commit-msg
```

The hooks in `.pre-commit-config.yaml`:

- **gitleaks**, with `.gitleaks.toml` describing *this repo's own* secret shapes:
  `AUTH_SECRET=` with 64 hex characters, `VOICE_WORKER_SECRET=`, and a
  `postgresql+psycopg://` URL whose password is not `reep_dev_password`. No vendor scanner
  knows those shapes; see step 6.
- **conventional-pre-commit** on the real type list
  (`feat|fix|docs|refactor|chore|ci|infra|security|test`).
- **end-of-file-fixer** and **mixed-line-ending `--fix=lf`**.

Two seconds on your machine instead of four minutes in CI, or four weeks in production.

**What stops you if you skip it.** Today, nothing at all. Step 12 is meant to be the CI
mirror that makes the hook the *fast copy* rather than the only copy, and step 12 is
`[ASPIRATIONAL]` — the job does not exist. So until it does, skipping the hook means the
check simply does not happen, on your machine or anywhere else. That is a materially
different sentence from the one the previous draft printed here, and the difference is the
whole reason for the markers.

## 5. Keep the env allowlist honest

*Stage: pre-commit.* **[ON MERGE]** — the `.gitignore` env block is in this branch's diff.
A `.gitignore` is not a control on its own: `git add -f` defeats it.

**What the block does.** The env rules are a **negated allowlist**, so a filename nobody
has thought of is ignored by default rather than by enumeration:

```gitignore
.env
.env.*
!.env.example
!.env.*.example
*.pem
*.key
*.p12
*.pfx
*-credentials.json
service-account*.json
secrets.json
```

**Why.** Before this change, `git check-ignore` reported `.env.production`, `.env.prod`,
`.env.staging`, `secrets.json`, `service-account.json` and `apps/api-py/creds.pem` as
**all not ignored** — only the literal `.env`, `.env.local` and `**/.env` were. AGENTS.md's
`ENV` allowlist (`prod`, `production`, `staging`, `uat`, `demo`) teaches operators to think
in environment names, so the natural file to create on a real box is `.env.production` and
the natural next command is `git add -A`. The one filename the old rules anticipated
(`.env`) is the one used locally; the ones carrying roster credentials and a live
`AUTH_SECRET` sailed straight through. On a public repo there is no un-pushing that, only
rotation, which signs every live session out.

**The command to check your own copy** — one path per call, and the reason why is in step
12:

```bash
for f in .env.production .env.prod .env.staging secrets.json service-account.json apps/api-py/creds.pem; do
  git check-ignore -q "$f" && echo "ignored     $f" || echo "NOT IGNORED $f"
done
```

On this branch every one of those six prints `ignored`, and `.env.example` correctly prints
`NOT IGNORED` — the negation is doing its job.

**What stops you if you skip it.** Nothing yet. Step 12's hygiene job is the thing that
would assert these names *stay* ignored through the next reorganisation, and that job is
`[ASPIRATIONAL]`.

## 5b. A root `.gitattributes`

*Stage: pre-commit.* **[ON MERGE]** — the file is in this branch's diff.
`git check-attr eol -- tools/ci/preflight.sh` is the check; it must print `eol: lf`.

This is a Windows checkout with `core.autocrlf=false`, which puts
`tools/fonts/fetch-fonts.sh` — and now `tools/ci/preflight.sh` and
`tools/ci/protect-main.sh` — one CRLF shebang away from failing on the Ubuntu runner with a
`bad interpreter: ...^M` that reads as a missing binary. A root `.gitattributes` pinning
`*.sh text eol=lf` closes it. It belongs with step 5 because it is the same class of
problem: a default that is wrong in exactly the environment nobody develops in.

The file stops there on purpose. A global `* text=auto eol=lf` is the usual advice and it
is not taken here: `git ls-files --eol | grep -c i/crlf` returns 29, among them
`apps/api-py/app/config.py` and `app/interview_relay.py`. A global rule renormalises all
29 on the next `git add`, so a one-line fix arrives as a 29-file diff and the reviewer
reads neither. That migration may be worth doing — as its own commit, where the diff is
the point.

## 6. Turn on secret-scanning non-provider patterns

*Stage: pre-commit.* **[ADMIN — NOT YET APPLIED]**. Verified:
`secret_scanning_non_provider_patterns` is `disabled`. This is the only control in the repo
that would act at push time, server-side.

**What an admin does.** Settings → Code security → Secret scanning → enable **non-provider
patterns**. `protect-main.sh` does not touch it; it is a click in the web UI.

```bash
gh api repos/darshani8/reep- --jq '.security_and_analysis'
# today: secret_scanning enabled, secret_scanning_push_protection enabled,
#        secret_scanning_non_provider_patterns DISABLED
```

**Why the provider half is not enough.** Push protection today rejects a leaked AWS key at
the server — that part is genuinely on. But REEP's own secrets are exactly *non-provider*
shapes: `AUTH_SECRET` is `secrets.token_hex(32)`, a bare 64-character hex string matching no
vendor's issued-token format. That is the same asset the `ENV=prod` boot guard defends —
and a real `AUTH_SECRET` on a public repo is the forged `{"role":"DIRECTOR"}` cookie
compromise with the boot guard *satisfied and silent*.

**What stops you if you skip it.** Nothing today. Once applied, GitHub rejects the push —
which is why this one is worth an admin's afternoon even though it is not on the merge
path.

## 7. Open the pull request

*Stage: pr.* **[ON MERGE]** for the template. A checkbox is not a gate in any state.

Once step 2 is applied there is no other route to main. Until then this is the route you
choose.

```bash
git push -u origin HEAD
gh pr create --fill        # the body comes from .github/pull_request_template.md
```

The template's checkboxes name the two hard rules **by file path**:

- Does this change a student-PII-to-model path? (`apps/api-py/app/ai/llm.py`)
- Does this change who can read another student's row?
  (`app/routers/mentor.py`, `app/policies.py`)
- Does this add an Alembic revision?
- Does this touch `app/config.py`'s guards (`production_boot_failures`,
  `password_login_allowed`), `app/security.py`, `app/google_auth.py`, `.github/workflows/**`
  or `infra/aws/**`?
- Did a dependency go into the right manifest?

The template is the authority on the exact wording; read it there rather than here. Both
rules are invisible in a diff unless the author volunteers them. A new
`complete_chat(...)` call site and a new mentor-scoped endpoint both read as ordinary
additions — that is precisely the problem.

**What stops you if you skip it.** Nothing. The template's only value is that *"I did not
know"* stops being available. Its intended blocking companions are steps 10 and 11, and
both are `[ASPIRATIONAL]`, so at present the checkboxes are the *only* thing in the process
that looks at rule 1 or rule 2 on a pull request.

## 8. The four existing checks must conclude success

*Stage: pr.* The **jobs** are **[IN FORCE]** — they run on every PR today. Their being
**required** is **[ADMIN — NOT YET APPLIED]**, via step 2. A green tick you can merge past
is a report, not a gate.

| Check name (exact) | What it actually proves |
|---|---|
| **API (FastAPI + Postgres)** | 39 test files against a `pgvector/pgvector:pg17` service with `REEP_REQUIRE_DB=1`, so a silently skipped DB test is a failure |
| **API (dependency completeness)** | `requirements.txt` **alone**, every module under `app/` imported (`tools/ci/check_api_imports.py`) |
| **Voice worker (dependency completeness)** | python 3.12, `requirements-voice.txt` alone, `voice_agent.py` imported from a clean environment |
| **Web (Angular)** | `npm ci`, `npx tsc --noEmit -p tsconfig.app.json`, `npx ng test --watch=false`, `npx ng build` with the bundle budget |

**Read the last row narrowly.** "Web (Angular)" proves that the front end **typechecks**,
that it **builds inside the bundle budget**, and that **two** spec files pass —
`find apps/web/src -name '*.spec.ts' | wc -l` returns 2 against 68 `.ts` files. It does not
prove a screen renders, that a login works, or that any endpoint answers. There is no e2e
framework in `apps/web/package.json` at all. A required check buys confidence in proportion
to its *status*, not to what it verifies, so this row is the one most likely to be
over-read once step 2 makes it a gate.

**What stops you if you skip it.** Today, nothing — the merge button is live regardless.
Once step 2 is applied, the merge button is disabled until all four conclude `success`, not
`cancelled`, which is why step 1 came first.

## 9. Schema steps, inside the API job

*Stage: pr.* **[ASPIRATIONAL]** — `.github/workflows/ci.yml` contains no `alembic check`,
no `alembic heads` assertion and no downgrade round trip. `grep -n 'alembic' .github/workflows/ci.yml`
is the check. Deliberately specified as steps in an existing job, so the required-check
list does not grow.

Three steps to run immediately after `alembic upgrade head` in "API (FastAPI + Postgres)":

```bash
python -m alembic check                      # (a)
python -m alembic heads                      # (b) must print exactly one row
python -m alembic downgrade -1 && python -m alembic upgrade head   # (c)
```

**(a) `alembic check`** fails exactly when a model change has no migration.
`migrations/env.py` already supplies `target_metadata = Base.metadata` and
`compare_type=True`, so it needs no wiring.

**(b) the single-head assertion.** Two developers branching off `d6a4e7f91b22` both merge
cleanly and only main's own run discovers `Multiple head revisions`. Measured on this tree:

```bash
grep -rho --include='*.py' 'create_type=False' apps/api-py/migrations/versions/ | wc -l   # 34
grep -rl  --include='*.py' 'create_type=False' apps/api-py/migrations/versions/ | wc -l   # 15
```

**34 `create_type=False` references across 15 revision files**, and
`e4c1b7a9d203_v2_ui_ledger_english_milestones.py` carries **10** of them by itself. Every
one of them depends on strict linear ordering — the revision that *creates* an enum type
must provably run before the one that reuses it — or the reuse raises
`type "x" does not exist` at apply time, on production, during the migration task, with the
service half-rolled. (Keep `--include='*.py'` in that grep. Without it you count the
compiled copies in `__pycache__` and get 37, which is how the previous draft's "ten across
eight" became a number nobody could reproduce.)

**(c) the downgrade round trip.** All 46 `downgrade()` bodies
(`ls apps/api-py/migrations/versions/*.py | wc -l` → 46) are code that has only ever been
read. The head revision's own downgrade sets an embedding column back to
`nullable=False`, which fails on any database where the embedding worker left a row NULL —
i.e. the exact state the upgrade exists to permit.

**Run them locally before pushing**, from `apps/api-py`, with the compose Postgres up:

```bash
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m alembic check
.venv/Scripts/python -m alembic heads
.venv/Scripts/python -m alembic downgrade -1
.venv/Scripts/python -m alembic upgrade head
```

**What stops you if you skip it.** Nothing, today. Once the three steps are in the API job
and step 2 is applied, "API (FastAPI + Postgres)" goes red and it is required. Both halves
have to be true; either one alone is a report.

## 9b. `ruff` with pyflakes rules only, in the same job

*Stage: pr.* **[ASPIRATIONAL]** — there is no `ruff`, no `pyproject.toml`, no `ruff.toml`
and no `setup.cfg` under `apps/api-py`. There is no Python static analysis in this
repository of any kind.

Add `ruff check --select F` as a step inside "API (FastAPI + Postgres)", exactly as step 9
adds the alembic steps, so no new required-check name appears.

**Why `F` only, and why it is not the mypy argument.** `F` is pyflakes: undefined names,
unused imports, f-strings with no placeholders. It imposes no style, needs no annotations,
and cannot be argued with. The reason it matters here is the same one that produced
`tools/ci/check_api_imports.py`: an undefined name or a shadowed import inside a **lazily
imported** request handler reaches production exactly the way `numpy` did — the API boots,
the tests pass, and the break surfaces on the first student to reach that path. pytest
collection is currently the only thing that would see it, and collection does not enter a
function body.

Steps 10 and 11 hand-roll `ast` walkers precisely because no static analysis exists to host
those rules. Promote rules one at a time after `F`. Keep mypy and CodeQL deferred — that
reasoning is in "Known gaps" and it is sound.

## 10. Rule 1: every model call declares whether it carries student data

*Stage: pr.* **[ASPIRATIONAL]** — `apps/api-py/tests/test_rule1_call_sites.py` does not
exist. `ls apps/api-py/tests/test_rule1_call_sites.py` is the check.

The test would walk `app/` with `ast` and fail unless every `Call` to `complete_chat` or
`stream_chat` passes `carries_student_data=` as an **explicit keyword**, with a short named
allowlist for the reviewed public-data sites (a job posting is not a student record).

**Why.** Rule 1's gate is **opt-in by omission** — the parameter defaults to `False` at
`app/ai/llm.py:207` and `:256`. A mentor-facing "summarise this student" endpoint that
composes marks and attendance into a prompt and forgets the keyword raises nothing, logs
nothing, and goes green. That is the same failure shape `tools/ci/check_api_imports.py` was
written to eliminate elsewhere: **the mechanism silently does the wrong thing when a
developer forgets.**

**The hole is live, not hypothetical.** `app/routers/agent.py:319`
(`complete_chat(messages, max_tokens=1024)`) and `:410`
(`stream_chat(messages, max_tokens=1024)`) pass no `carries_student_data=` at all, and
therefore default to `False`. Those are the two call sites this test was designed around,
and they are still ungated after the process that claims to gate them. Precedent for the
technique is in-repo — `tests/test_voice_worker_source.py` reads a file as text and refuses
forbidden imports.

**What the test could never prove.** It proves a keyword is *present at a call site*. It
cannot prove the gate returns the right answer in the environment where it runs, and today
it does not: `infra/aws/variables.tf:125` defaults `allow_remote_student_data` to `"true"`,
so `student_data_egress_allowed()` returns `True` for any base URL on the deployed stack.
Swap the provider and a student's name, USN, marks and attendance leave the machine with
the gate satisfied and nothing logged. That is a rule-1 violation that is live right now,
it is filed in "Known gaps" with the file that must change, and no step in this list would
catch the variable moving.

**What stops you if you skip it.** Nothing, until the file exists. When it does, it is
collected by the API job and needs no database, so it also fails on your laptop in under a
second.

## 11. Rule 2: every `{student_id}` route reaches the scope gate

*Stage: pr.* **[ASPIRATIONAL]** — `apps/api-py/tests/test_route_gates.py` does not exist.

The test would enumerate every mounted route and assert two frozen properties:

1. Every route whose path contains `{student_id}` reaches `_assert_can_access_student`
   (`app/routers/mentor.py:72`).
2. Every handler lacking a session dependency is one of the small set of deliberately
   public paths — login, the SSO status endpoints, `/health`, `/ready`, registration —
   enumerated by name in the test, so adding a public route is a decision someone makes on
   purpose rather than a default.

**The surface it would pin, measured rather than grepped.** Counting text occurrences of
`{student_id}` is what produced the previous draft's numbers, and they were wrong in both
directions — a module docstring counts as a route, a multi-line decorator does not. Ask the
application instead:

```bash
cd apps/api-py && .venv/Scripts/python -c "
from app.main import app
spec = app.openapi()
paths = [p for p in spec['paths'] if '{student_id}' in p]
print(len(spec['paths']), 'documented paths;', len(paths), 'carry {student_id}')"
# 144 documented paths; 18 carry {student_id}
```

**Write that sweep against `app.openapi()`, not against `app.routes`.** On FastAPI 0.141.1
this application stores included routers lazily: `len(app.routes)` is **24**, almost all of
them `_IncludedRouter` placeholders, and a loop over `app.routes` looking for
`{student_id}` finds **zero**. A test written the obvious way would pass by finding nothing
to check, on every run, forever — a green tick that verifies the absence of its own subject.
That is the same class of failure as a renamed required check, one level down, and it is
the reason this step names the enumeration method rather than leaving it to whoever writes
the file.

**One gate name, not two.** AGENTS.md is explicit that rule 2's gate is
`_assert_can_access_student`, "imported from `routers/mentor.py`, never reimplemented."
There is a second implementation — `assert_student_scope` at `app/policies.py:73` — and it
is not equivalent: it calls `require_staff` rather than `require_mentor`, and adds a
`TenantMembership` check, so the admitted role set and the 404 semantics differ. **The test
must assert `_assert_can_access_student` only**, with `assert_student_scope` in an
explicit, dated allowlist naming each route that still uses it. Accepting either name as
correct would freeze the duplication as permanently compliant — the process would make the
divergence AGENTS.md forbids into the thing CI protects.

The allowlist has one member today, and it can only shrink:

```bash
grep -rn 'assert_student_scope(' apps/api-py/app --include=*.py |
  grep -v 'def assert_student_scope' | grep -c ''
# 8 call sites, all of them in app/routers/redesign.py
# (drop the -v and you get 9: the ninth line is the definition in app/policies.py)
```

`app/routers/redesign.py` is the only module in the tree that uses rule 2's second
implementation, and therefore the only place the two gates can silently disagree. Collapse
it (see "Known gaps") and the allowlist goes to zero.

**What stops you if you skip it.** Nothing, until the file exists. `tests/test_interview_access.py`
already proves this class of check works, for one router.

## 12. Repo hygiene

*Stage: pr.* **[ASPIRATIONAL]** — no job named "Repo hygiene (secrets, ignores, format)"
exists in `.github/workflows/ci.yml`. `grep -nE '^    name:' .github/workflows/ci.yml` returns the
four job display names and no others. Its `.gitleaks.toml` **is** `[ON MERGE]`, so the config would
arrive before its consumer.

The proposed job, three things a fresh ubuntu runner does in under a minute:

```bash
gitleaks detect --config .gitleaks.toml --no-banner        # the CI mirror of step 4

for f in .env.production .env.prod .env.staging secrets.json service-account.json apps/api-py/creds.pem; do
  git check-ignore -q "$f" || { echo "::error::$f is NOT ignored"; fail=1; }
done
[ -z "${fail:-}" ]                                          # step 5 stays fixed

cd apps/web && npx prettier --check .                       # uses the .prettierrc that already exists
```

**The loop is not stylistic, and getting it wrong makes the job red on arrival.** The
previous draft wrote `git check-ignore -q .env.production .env.prod secrets.json
service-account.json` as one call. Run it here and git answers:

```
fatal: --quiet is only valid with a single pathname     # exit 128
```

Inside a `run:` block that is an unconditional job failure, for a reason nobody would guess
from the log — precisely the "a required check that is always red becomes a bypass request"
failure this section warns about. And the single call would be wrong even if `-q` accepted
several paths: `git check-ignore`'s documented exit status is **0 when one or more of the
paths are ignored**, so a four-name call passes with three of the four un-ignored. Verified:
`git check-ignore .env .env.production` exits 0 while `.env.production` is the one that
matters. One path per call, and name the failing one.

gitleaks runs here as well as in the hook because a hook in `.git/` is skippable with
`--no-verify` and push protection does not know non-provider shapes. And four real commits
in this history spent review attention on indentation and formatting — `Fix indentation in
test cases for consistency`, `Fix indentation and formatting in redesign.py` — which a
formatter settles for free. `apps/web/.prettierrc` has existed the whole time and nothing
has ever executed it.

**Two things must land before this name goes on the required list.** One
`npx prettier --write .` commit, or the job is red on arrival. And the fifth string in
`tools/ci/protect-main.sh`'s `REQUIRED_CHECKS` array, in the same change as the job — never
before it, because a required check that has never reported is a merge button waiting for
something that will never arrive. That same change also makes AGENTS.md's "CI has four
jobs" paragraph false, and must update it; see "Review".

**What stops you if you skip it.** Nothing. The job does not exist.

## 13. Review routing

*Stage: review.* **[ON MERGE]** for `.github/CODEOWNERS`; blocking for collaborators once
`.github/rulesets/lead-review.json` is applied **[ADMIN]**.

`.github/CODEOWNERS` routes review for the files where a wrong line has a different
consequence from every other file in the repo:

```
apps/api-py/app/ai/llm.py            # rule 1's gate
apps/api-py/app/routers/mentor.py    # rule 2
apps/api-py/app/policies.py          # rule 2, second implementation
apps/api-py/app/config.py            # the boot guard, password_login_allowed
apps/api-py/migrations/versions/**
.github/workflows/**
infra/aws/**
```

The committed file carries a few more — `app/security.py`, `app/google_auth.py`,
`app/identity.py`, `app/models/`, `tools/ci/`, `AGENTS.md` — and it is the authority; this is
the core of it.

A one-character edit to the egress gate's boolean is today the same size and the same
colour in a diff as a typo fix. CODEOWNERS makes *"this diff touches the thing that decides
whether a student's USN leaves the machine"* a fact the review UI **states**, rather than
something a reviewer has to notice.

**Code-owner review is on for collaborators (2026-10-15).** `.github/rulesets/lead-review.json`
requires one code-owner approval on the latest push for every PR into `main`, `stage` and
`dev`, and lets only the admin merge. The owner's own PRs cannot be approved by their
author, so the admin merges them through that ruleset's pull-request-only bypass. Treat a
diff that touches any path above as one you re-read from the top before merging it.

**What stops you if you skip it.** Nothing. Steps 10 and 11 are the intended mechanical
companions and both are `[ASPIRATIONAL]`.

## 14. Merge through the PR, up to date, and delete the branch

*Stage: merge.* **[ADMIN — NOT YET APPLIED]** — same ruleset as step 2, plus one repository
toggle. `gh api repos/darshani8/reep- --jq '.delete_branch_on_merge'` returns **false**
today.

```bash
gh pr merge --squash --delete-branch
```

**"Require branches to be up to date before merging" (strict) is the anti-two-heads
control** from step 9(b). GitHub does not re-run an open PR's checks when its base moves, so
without strict, two Alembic revisions generated against the same parent each pass their own
run and collide on main — where the failure is `Multiple head revisions` during a production
migration task, not on anyone's laptop.

`delete_branch_on_merge` is one repository toggle that removes a merged branch with no
discipline required. Three remote branches are fully merged into `origin/main` and still
sitting in `git branch -r`
(`git branch -r --merged origin/main | grep -v 'HEAD\|origin/main$'` → 3), undifferentiated
from the ones that carry live work.

**What stops you if you skip it.** Today, nothing: you can push straight to main. Once step
2 is applied you cannot merge any other way, and force-push and deletion of `main` are
blocked with no bypass actors — which is exactly why the next section exists.

## When the gate itself is broken

*Read this before you need it.* **[ADMIN — NOT YET APPLIED]**, exactly like the gate it
escapes: with `main` unprotected there is nothing yet to be locked out of. Opening the
escape and closing it are the same script.

Step 2 specifies `enforce_admins` on, no bypass actors, and four required checks, on a
repository with **one** collaborator. That configuration has an ordinary, non-exotic failure
mode: **main becomes unmergeable by anyone**, including during the security fix this whole
process exists to protect.

**The symptom, and how to tell the two apart.** A required check that has **failed** shows a
red X and a run you can open. A required check that will **never report** shows nothing at
all — the merge button says it is waiting, and there is no run to look at. The second is the
dangerous one, and its causes are: a job renamed so its display name no longer matches the
required string (step 2); the `pgvector/pgvector:pg17` service image failing to pull; or a
GitHub Actions incident in which no runner ever picks the job up.

**The escape, and the fact that it is two commands and not one:**

```bash
./tools/ci/protect-main.sh --allow-admin-bypass     # ENFORCE_ADMINS=false; merge the fix
./tools/ci/protect-main.sh --approvals 0           # close it again, same session
```

The script is a single idempotent full-state `PUT`, so the second command genuinely
restores the previous state rather than layering on top of it.

**Three rules for using it.**

1. **Record it in the PR body** — the reason, the time it was opened, the time it was
   closed. An undocumented escape hatch gets used exactly the same way, just without the
   record, and the difference between "we bypassed the gate on purpose for eleven minutes"
   and "the gate was off and nobody knows since when" is the entire value of having one.
2. **Close it in the same session.** Not tomorrow. `--allow-admin-bypass` left on is
   indistinguishable from having no protection, and it looks protected in the settings UI.
3. **Fix the cause, not the symptom.** If a rename broke a required check, re-run
   `protect-main.sh` with the new name in `REQUIRED_CHECKS`; do not leave the bypass open
   because the check "is flaky".

## 15. Make the release identifiable and the rollback expressible

*Stage: release.* **[ASPIRATIONAL]** — `infra/aws/ecr.tf:3` reads
`image_tag_mutability = "MUTABLE"` and `infra/aws/ecs.tf` builds `api_image` as
`${repository_url}:latest`. Neither is changed in this branch. Blocking **by AWS** once
applied, which is why it is worth doing: the enforcement needs no GitHub setting at all.

- `infra/aws/ecr.tf`: `image_tag_mutability = "IMMUTABLE"` — AWS rejects a second push of
  an existing tag.
- `.github/workflows/deploy.yml`: an `image_tag` input defaulting to `github.sha`; register
  a task-definition revision pinned to that immutable sha tag and pass it to
  `update-service --task-definition`.

**Why.** The task definition hardcodes `:latest` on a **mutable** repository, so every
deploy overwrites what `latest` means — and the ECS circuit breaker's rollback target is
the *same task-definition revision*, which re-resolves `:latest` to the image that just
broke. `docs/aws-deployment.md` §4's claim that the breaker "rolls back a bad image on its
own" is not what the mechanism does. "Put back the build that worked" should be a dispatch
with the previous sha, not a revert commit plus a full rebuild while the bad build serves
students.

## 16. `ci-green`: the deploy checks the sha it is shipping

*Stage: deploy.* **[ASPIRATIONAL]** — `.github/workflows/deploy.yml` has no `ci-green` job;
`api` and `web` both declare `needs: guard` and nothing else.

A `ci-green` job at the head of `deploy.yml`:

```bash
gh api repos/${{ github.repository }}/commits/${{ github.sha }}/check-runs
# exit non-zero unless every required check on THAT sha concluded `success`
```

`api` and `web` then declare `needs: [guard, ci-green]`.

**Why.** The `guard` job's only assertion today is that the operator can spell "deploy" — it
verifies *intent*, never *correctness*. The 543a265 timeline at the top of this document is
the whole argument: a deploy started four seconds after CI started on that sha, and
concluded successfully while the CI run for the code it shipped was being cancelled. Note
what `ci-green` has to assert to catch that case — **`success` on every required check**,
not "no failures". A run that is still going has no failures either.

**What stops you if you skip it.** Nothing today. Once the job exists, the dispatch fails
before the OIDC role is assumed.

## 17. The `production` GitHub Environment

*Stage: deploy.* **[ADMIN — NOT YET APPLIED]**. `gh api repos/darshani8/reep-/environments
--jq '.total_count'` returns **0**.

A GitHub Environment named `production`, with **required reviewers** and a **deployment
branch policy limited to `main`**, referenced by `deploy.yml`'s `api` and `web` jobs and by
`ops-task.yml`'s `run` job. Settings → Environments → New environment; a web-UI action,
which `protect-main.sh` does not perform.

**Why `ops-task.yml` is in that list.** Anyone with write access can dispatch `ops-task.yml`
→ `grant-access` alone and unreviewed, and mint an ADMIN or DIRECTOR account for any Google
address they type — an account that by rule 2 reads every student's marks, attendance and
USN. The whole approval story is currently one person typing the word `run`.

The AWS OIDC `sub` pin to `refs/heads/main` is a genuine second lock and stays. It refuses a
deploy from a side branch; it cannot tell a green main commit from a red one.

## 18. Snapshot before the migration, not after

*Stage: deploy.* **[ASPIRATIONAL]** — no snapshot step exists in `deploy.yml`.

A step in the `api` job, before "Run database migrations", gated on `inputs.run_migrations`:

```bash
aws rds create-db-snapshot --db-snapshot-identifier reep-premigrate-${{ github.sha }} ...
aws rds wait db-snapshot-available --db-snapshot-identifier reep-premigrate-${{ github.sha }}
echo "snapshot: reep-premigrate-${{ github.sha }} at $(date -u +%FT%TZ)" >> "$GITHUB_STEP_SUMMARY"
```

**Why.** `docs/phase4-staging-runbook.md` requires a `pg_dump -Fc` and its SHA-256 before
touching a **staging** schema. Production — which holds the real cohort, and the
`interview_consents` rows whose entire purpose is to stay answerable after a grant is
revoked — currently gets less. The existing step correctly refuses to roll the service on a
non-zero migration exit, but there is then no snapshot to go back to: PITR restore creates a
new endpoint under pressure, and nothing recorded the moment to restore *to*.

## 19. The SPA waits for the API

*Stage: deploy.* **[ASPIRATIONAL]** — `web` declares `needs: guard` today.

`web` becomes `needs: [guard, ci-green, api]`, with an `if:` that still allows `web-only`.

**Why.** Both jobs depend only on `guard` today and run in parallel. A migration that
correctly stops the API roll leaves the new front end already halfway to S3 — the new SPA
served from CloudFront against the **old** API and a **part-applied** schema, every added
screen calling an endpoint that answers 404, and a deploy log showing one red job and one
green one.

## 20. `verify`: assert what is actually serving

*Stage: post-deploy.* **[ASPIRATIONAL]** — there is no `verify` job. Non-blocking even when
it exists: the code has already shipped. Its mechanism is turning a green lie into a red run
that names the rollback.

A `verify` job with `needs: [api, web]`:

1. `aws ecs describe-services`, resolve the **PRIMARY** deployment's image digest, and fail
   unless it matches the digest just pushed. `aws ecs wait services-stable` returns success
   as soon as the service reconverges — which is exactly the state ECS reaches **after** the
   circuit breaker throws the new build away, and the workflow then unconditionally echoes
   `API deployed: <sha>`. An operator who deploys a security fix, reads that line and closes
   the tab believes a patched build is running while the vulnerable one still is.
2. `aws elbv2 describe-target-health` for API readiness, because the ALB target group's
   health check path is `/ready` (`infra/aws/alb.tf:24`) and that is where an unhealthy task
   is visible.
3. Through the edge, `curl -s https://<domain>/api/auth/sso/status`, asserting
   `google_available: true` and `password_login_available: false`. Then the CloudFront root,
   asserting `index.html` references a chunk hash from this build.

**Do not curl `/api/health`. There is no such route.** The health router is mounted
**unprefixed** — `app/main.py` includes it with no prefix under the comment "Health is infra
liveness — unprefixed at /health", and `app/routers/health.py` defines `/health` and
`/ready`. CloudFront sends `/api/*` straight to the ALB with **no path rewriting**
(`infra/aws/cdn.tf`, the `/api/*` ordered cache behavior), so `/api/health` arrives at the
API intact and 404s. A verify job built from that URL fails every deploy, for a reason that
looks like an outage.

So the two probes mean different things and you should say which you want:

- **`/health` and `/ready`** are *infra liveness*, unprefixed, and are reachable at the ALB
  — not through CloudFront, because nothing routes them there. Use them for target health.
- **`/api/auth/sso/status`** is the *edge* probe: it is a real `/api` route, so it exercises
  CloudFront → ALB → the API and back, and its payload states whether Google sign-in is
  configured and whether the password door is shut. That is the one to assert after a deploy,
  because it proves the path a student's browser actually takes.

A wrong `WEB_ORIGIN` drops the session cookie and every login silently behaves as
logged-out — a failure `docs/deployment-env.md` names and nothing detects. Neither probe
above catches it; see "Known gaps" for the smallest thing that would.

## 21. Rehearse the restore weekly

*Stage: post-deploy.* **[ASPIRATIONAL]** — `.github/workflows/restore-rehearsal.yml` does
not exist. Advisory even when it does: it reports, it cannot stop a deploy.

On a schedule: restore the newest RDS snapshot into a disposable instance, run
`python -m alembic current`, assert it prints the expected head, delete the instance.

**Why.** The repo already states the principle twice in its own words — *"A backup that has
never been restored is a hope, not a backup"*, in the db-backup service comment and again in
the deployment env doc — and attaches no mechanism to either. A `pg_dump` silently producing
0-byte files, or a snapshot nobody has ever loaded, is discovered on the one day it is
needed. **The restore either works every Tuesday, or you find out on a Tuesday instead of
during an outage.**

## 22. Dependabot

*Stage: post-deploy.* **[ASPIRATIONAL]** for `.github/dependabot.yml`, which does not exist;
**[ADMIN — NOT YET APPLIED]** for the toggle —
`gh api repos/darshani8/reep- --jq '.security_and_analysis.dependabot_security_updates'`
reports `disabled`. Advisory in both halves: it opens a PR.

Enable Dependabot **alerts and security updates**, and commit `.github/dependabot.yml` for
three ecosystems:

- `pip` at `/apps/api-py` (`requirements.txt` and `requirements-voice.txt`)
- `npm` at `/apps/web`
- `github-actions` at `/` — the workflows pin `actions/checkout@v4` and
  `aws-actions/configure-aws-credentials@v4`, mutable major tags inside a job that holds
  `id-token: write` and can push the production image.

**Why, given the pinning is deliberate.** `requirements.txt` is pinned `==` correctly —
AGENTS.md is explicit that `>=` bounds meant a rebuild months later resolved a dependency
set nobody had run the suite against. That same pinning means the versions never move off a
published CVE either. The exposure is **PyJWT**, which verifies the `reep_session` cookie
carrying `role`, and **psycopg**, which talks to the database holding every student's
record. The two dependency-completeness jobs prove the manifest is **complete**; never that
it is **safe**.

With step 2 applied, each Dependabot PR is a full run of every required check against the
new pin — which is exactly the verification the `==` pinning was protecting.

---

## Migrations: when, and what to do about two heads

### When a migration is required

Any change to a file under `apps/api-py/app/models/` needs one. Import the new module in
`models/__init__.py` first, or Alembic autogenerate cannot see it and `alembic check` will
happily report no changes for a model that is not in the metadata.

```bash
cd apps/api-py
.venv/Scripts/python -m alembic revision --autogenerate -m "short_snake_subject"
# read the generated file before running it
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m alembic downgrade -1     # step 9(c) would do this in CI; do it here first
.venv/Scripts/python -m alembic upgrade head
```

Revisions live in `apps/api-py/migrations/versions/` — **not** `alembic/versions`.

**The three enum gotchas** AGENTS.md records, because they have been hit repeatedly:
adding an enum *column* to an existing table does not auto-`CREATE TYPE`; a *new table*
reusing an *existing* enum must use `postgresql.ENUM(..., name='x', create_type=False)`
(autogenerate emits a bare `sa.Enum` that errors "type already exists" — hand-fix it); two
columns sharing one enum reuse a single `Enum` instance.

### Two people, two heads

`alembic heads` printing two rows is not a merge conflict git can see — both files are new,
both merge cleanly, and the collision surfaces at `upgrade head` on whichever database runs
next. On main that database is production's.

**Prevention** is step 14's strict "up to date before merging": your PR must be rebased on
the new main, so `alembic heads` in *your* run sees the other revision. That is
`[ADMIN — NOT YET APPLIED]`, so today prevention is you remembering to rebase.

**Cure**, when it happens anyway:

```bash
git switch main && git pull
cd apps/api-py
.venv/Scripts/python -m alembic heads             # confirm: two rows
# Preferred: rebase yours onto theirs and re-point down_revision.
git switch your/branch && git rebase main
# edit your revision file: down_revision = "<their revision id>"
.venv/Scripts/python -m alembic heads             # must now print exactly one
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m alembic downgrade -1 && .venv/Scripts/python -m alembic upgrade head
```

**Prefer re-pointing `down_revision` over `alembic merge`.** A merge revision leaves the two
branches in an order nothing pins, and **34 `create_type=False` references across 15
revision files** depend on strict linear ordering: the revision that *creates* an enum type
must provably run before the one that reuses it, or the reuse raises
`type "x" does not exist` on first apply. Linear history is not tidiness here, it is
correctness. Re-measure rather than quoting this paragraph in a year:

```bash
grep -rho --include='*.py' 'create_type=False' apps/api-py/migrations/versions/ | wc -l
grep -rl  --include='*.py' 'create_type=False' apps/api-py/migrations/versions/ | wc -l
```

The head as of this writing is `d6a4e7f91b22` (`phase4_execution_layer`), across 46
revision files.

---

## Review

- **Every change goes through a pull request** — once step 2 is applied. Approvals is 0 in
  the ruleset for the reason in step 2, so on a solo repo the review requirement is: *you
  re-read your own diff in the PR view before merging*, and you do it in the GitHub UI
  rather than in your editor, because that is where the CODEOWNERS labels and the check
  results are.
- **A diff touching any CODEOWNERS path in step 13 is re-read from the top**, not skimmed.
  Those are the files where a one-character change has a consequence unlike anything else in
  the repo.
- **The PR template's rule-1 and rule-2 questions are answered, not deleted.** If the answer
  to either is yes, say in the PR body which call site or which endpoint, and why it is
  correct.
- **A change that alters behaviour arrives with a test** — step 3b. Nothing enforces this
  but the reviewer, and on this repository the reviewer is the author.
- **A change that makes a sentence in AGENTS.md untrue fixes that sentence, in the same
  PR.** AGENTS.md is the first thing this document tells you to read and is simultaneously
  the most authoritative and the least maintained file in the process — commit c8f3a4b is
  literally *"docs: refresh the architecture counts the v2 UI work invalidated"*, which is
  the repository fixing AGENTS.md in arrears. Two live examples this process creates:
  AGENTS.md's **"CI has four jobs"** paragraph is true today and becomes false the moment
  step 12's job lands; and rule 2's *"imported from `routers/mentor.py`, never
  reimplemented"* is already contradicted by the eight `assert_student_scope` call sites in
  `app/routers/redesign.py` — step 11 pins the intended single name, and "Known gaps" holds
  the collapse. **[ASPIRATIONAL]** as a mechanism: no check reads AGENTS.md.
- **The day a second maintainer exists**, turn on `require_code_owner_reviews` in the
  ruleset. That is the single change that converts step 13 from labelling into a gate.

### What this process supersedes in `docs/phase4-staging-runbook.md`

That file's *"CI and review acceptance"* list predates this one and they disagree. Item by
item, for changes **outside** the Phase 4 redesign surface:

| Phase 4 acceptance item | Status under this process |
|---|---|
| Python compile and full API tests pass | **Kept** — "API (FastAPI + Postgres)", step 8 |
| Angular build and tests pass | **Kept** — "Web (Angular)", step 8, read narrowly |
| Migration upgrade succeeds on an empty database **and a representative existing database** | **Half kept.** Step 9 and `deployment-process.md` §6 exercise an empty CI Postgres only. The representative-database half is **[ASPIRATIONAL]** and is the same gap a staging tier would close |
| Worker contract and failure-injection tests pass | **Kept, and scoped to Phase 4** — they are that surface's tests |
| **Human architecture and security review approvals are present** | **Superseded.** Step 2 sets approvals to 0 because GitHub does not let an author approve their own PR and this repository has one collaborator, so the requirement is unsatisfiable by construction, not by choice. It returns the day a second maintainer exists |
| No production deployment or `terraform apply` as part of the PR | **Kept** |

Inside the Phase 4 redesign surface, that file's list still applies as written — with the
same two caveats, because a one-collaborator repository cannot produce an approval there
either.

---

## What a file cannot enforce, and what simply is not written yet

Two different kinds of hole, and the previous draft listed only the first — which inverted
this document's own thesis, because a step describing a file nobody has written misleads
exactly the way a checkbox pretending to be a gate does.

### Kind one: GitHub settings. No commit can apply these.

They must be applied by the repository owner (`darshani8`). **Without them the file-based
half of this document is decoration.**

| Step | Setting | Applied by | Verify it is on | Today |
|---|---|---|---|---|
| 2, 14 | Protection on `main` (PR required, the check list, strict, no bypass) | **`tools/ci/protect-main.sh`** | `gh api repos/darshani8/reep-/branches/main/protection` | **404 — not protected** |
| 14 | `delete_branch_on_merge` | Settings → General | `gh api repos/darshani8/reep- --jq '.delete_branch_on_merge'` | **false** |
| 6 | Secret scanning **non-provider patterns** | Settings → Code security | `gh api repos/darshani8/reep- --jq '.security_and_analysis'` | **disabled** (push protection: enabled) |
| 17 | `production` Environment, required reviewers, main-only branch policy | Settings → Environments | `gh api repos/darshani8/reep-/environments` | **`total_count: 0`** |
| 22 | Dependabot alerts + security updates | Settings → Code security | `gh api repos/darshani8/reep- --jq '.security_and_analysis.dependabot_security_updates'` | **disabled** |

**Only the first row has a script.** The other four are clicks in the web UI, and nothing in
this repository can tell you whether they were made.

```bash
gh auth status                                  # you need ADMIN on the repository
./tools/ci/protect-main.sh --show               # what is live right now
./tools/ci/protect-main.sh --dry-run            # what would change
./tools/ci/protect-main.sh --approvals 0        # apply
```

The script is idempotent — a single full-state `PUT` — so re-running it after someone edits a
setting in the web UI is how you find out that they did. Run `--show` on a schedule you can
keep, because a silently loosened gate looks exactly like a gate.

### Kind two: files that nobody has written. `ls` answers this one.

Every row here is a plain committed file with **no admin dependency**. Each is a step above
marked `[ASPIRATIONAL]`, and each stops being aspirational the day someone writes it.

| Step | The file that would implement it | Exists? |
|---|---|---|
| 5b | `.gitattributes` (root, `*.sh text eol=lf`) | yes — in this branch |
| 9 | three `run:` steps in `ci.yml`'s "API (FastAPI + Postgres)" | no |
| 9b | `ruff` config + one `run:` step in the same job | no |
| 10 | `apps/api-py/tests/test_rule1_call_sites.py` | no |
| 11 | `apps/api-py/tests/test_route_gates.py` | no |
| 12 | job "Repo hygiene (secrets, ignores, format)" in `ci.yml` | no |
| 15 | `infra/aws/ecr.tf` IMMUTABLE + `image_tag` in `deploy.yml` | no |
| 16 | `ci-green` job in `deploy.yml` | no |
| 18 | snapshot step in `deploy.yml`'s `api` job | no |
| 19 | `needs: [guard, ci-green, api]` on `deploy.yml`'s `web` | no |
| 20 | `verify` job in `deploy.yml` | no |
| 21 | `.github/workflows/restore-rehearsal.yml` | no |
| 22 | `.github/dependabot.yml` | no |

```bash
ls .gitattributes .github/dependabot.yml .github/workflows/restore-rehearsal.yml \
   apps/api-py/tests/test_rule1_call_sites.py apps/api-py/tests/test_route_gates.py
grep -nE '^    name:' .github/workflows/ci.yml  # four job display names, and only four
grep -n 'ci-green\|create-db-snapshot\|verify:' .github/workflows/deploy.yml
```

**"Does this gate exist" must be answerable in one command, not by reading this document.**
That is the whole reason for this table.

---

## Known gaps, stated rather than hidden

### Two live rule violations, which are not gaps in the process — they are bugs the process does not catch

1. **Rule 1's gate is open in production and does not ask where the data is going.**
   `infra/aws/variables.tf:125` defaults `allow_remote_student_data` to `"true"`, so
   `student_data_egress_allowed()` returns `True` for any base URL on the deployed stack.
   Blank the Bedrock model, paste a `GROQ_API_KEY`, and student resumes — name, USN, marks,
   attendance — flow to a free tier with the gate still returning `True` and nothing logged.
   Two fixes, and the second is the real one: flip the default to `"false"`, because a gate
   that is opt-in in code and opt-out in infrastructure is not a gate; and make the gate
   **destination-aware** — loopback, or the Bedrock transport, else require the flag — so a
   provider swap re-closes it by itself rather than by an operator remembering a variable.
   Note what step 10 can and cannot do about this: an AST scan proves a keyword is present
   at a call site and can never prove the gate returns the right answer where it runs.
2. **Rule 2 has two implementations.** `_assert_can_access_student`
   (`app/routers/mentor.py:72`) and `assert_student_scope` (`app/policies.py:73`), while
   AGENTS.md says the gate is "imported from `routers/mentor.py`, never reimplemented". They
   admit different role sets (`require_mentor` vs `require_staff`) and return different
   404 semantics. All 8 `assert_student_scope` call sites are in `app/routers/redesign.py`.
   Collapse `policies.py`'s MENTOR branch onto the `mentor.py` function and keep only the
   tenant check local — or at minimum run the three-case matrix (in-group, out-of-group,
   no-group) through both callables. Step 11 pins that a gate is *reached*; it cannot pin
   that two gates *agree*.

### Deliberately not in this process

Each would make the list longer than anyone will follow, and a process nobody follows is the
thing this document exists to prevent.

- **An integration test across the Angular↔FastAPI seam**, which is the product. The API
  side is genuinely tested — 39 test files, a real pgvector Postgres, `REEP_REQUIRE_DB=1`.
  The seam is tested nowhere: 2 spec files against 68 `.ts` files, and no Playwright,
  Cypress or WebDriver anywhere in `apps/web/package.json`. **The named next step, and it is
  one test, not a programme:** one Playwright run against `ng serve` plus a seeded local API
  that logs in as `student@bgscet.ac.in` and asserts the dashboard paints. That single test
  is what would catch the wrong-`WEB_ORIGIN` cookie failure step 20 says nothing detects.
  This belongs above coverage thresholds in the ordering, because it is a *different* thing
  from a percentage.
- **Coverage thresholds.** Real, and measured: 2 spec files against 68 `.ts` files, and no
  pytest coverage measurement at all. But a ratchet introduced *at the same time as* branch
  protection fails the first PR after this lands on a number nobody chose. Add `pytest-cov`
  to `requirements-dev.txt` **only** — never `requirements.txt`, where "API (dependency
  completeness)" would catch the violation — and set `--cov-fail-under` at today's measured
  figure once the gates above are habitual.
- **mypy and eslint.** A type checker introduced as all-or-nothing gets disabled the first
  afternoon someone needs to ship. `ruff --select F` is step 9b for the opposite reason: it
  is not a style, it is undefined names. Prettier is in step 12 only because its config
  already exists and it is one line.
- **CodeQL.** Free on this public repo and a good fit for the hand-rolled scrypt / HS256 /
  Google-ID-token surfaces. Add it as a **non-required** job first and promote it once its
  alert volume is known — a fifth or sixth required check before the first four are proven
  is how a process gets abandoned.
- **A staging environment.** The real answer to "every migration's first contact with a
  production-shaped database is production", and the same gap as the phase-4 acceptance row
  above. Far larger than a process document; step 18's pre-migration snapshot is the
  affordable interim.

### Deploy defects this process assumes are fixed separately

They are bugs, not gates, and putting them in the gate list would hide them. All three are
`[ASPIRATIONAL]`; `docs/deployment-process.md` carries the same three with the same marker.

- `aws s3 sync --delete` (`deploy.yml`) deletes the previous build's hashed chunks the
  moment the new `index.html` goes live. Every route in this app is `loadComponent`, so a
  student mid-session clicking anything asks for a chunk that no longer exists. Drop
  `--delete` and expire noncurrent versions with a lifecycle rule.
- Unhashed assets under `public/` (`favicon.ico`, the subset icon font) get a one-year
  immutable cache and are never invalidated, so a regenerated Material Symbols subset never
  reaches a browser and the added glyph renders as **nothing at all** — the exact failure
  the `.icon` clamp and the `fonts-ready` gate exist to prevent. Sync `fonts/**` in its own
  short-max-age pass and add `/fonts/*` to the invalidation.
- `deploy.yml` and `ops-task.yml` use different concurrency groups, so a `grant-access` task
  can run against a half-rolled image mid-deploy. Give both `group: production-mutations`.
