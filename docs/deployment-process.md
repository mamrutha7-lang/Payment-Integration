# The production deployment process

Who may ship REEP to production, when, after what, and what to do when it goes
wrong.

This file owns the **process**. It does not own the mechanics, and it
deliberately does not repeat them:

| For | Read |
|---|---|
| What the AWS stack is and how to stand it up the first time | `docs/aws-deployment.md` |
| Which environment variable each image needs and what breaks without it | `docs/deployment-env.md` |
| Doing the first apply from a browser with no terminal | `docs/deploy-from-chrome.md` |
| The staging-shaped migration and worker drill | `docs/phase4-staging-runbook.md` |
| The two hard rules, the boot guard, the seed refusals | `AGENTS.md` |

**Read those for "how". Read this for "may I, and what then".**

## Status markers

Every rule below carries one marker, because a document that mixes "this is
enforced" with "this would be nice" teaches its readers to trust neither.

- **[IN FORCE]** — true on `main` today. A mechanism exists and acts.
- **[IN THIS BRANCH]** — the mechanism is a committed file in this branch, so it
  is true from the moment this merges and `ls` or `grep` settles whether it is
  here. Every use of this marker names the command.
- **[NOT WRITTEN]** — described here and implemented nowhere. No file in this
  branch, on `main`, or anywhere else performs it. It is a specification.
- **[ADMIN — NOT YET APPLIED]** — requires a GitHub setting only the repository
  owner (`darshani8`) can apply by hand. **No committed file can turn it on**, and
  as of this document none of them are on.
- **[ASPIRATIONAL]** — no mechanism exists and none is proposed here. Written
  down so the gap is a known gap rather than an assumed control.

**There used to be an [ON MERGE] marker here and it was a lie.** It read "lands
with the development-process branch this document belongs to", and it was
attached to about twenty rules whose mechanism is in no branch at all: a
`ci-green` job, a `verify` job, an `image_tag` input, a pre-migration snapshot
step, a `web needs: api` edge, a repo-hygiene job. `.github/workflows/deploy.yml`
is byte-identical to `main`'s (`git diff main -- .github/workflows/deploy.yml`
prints nothing) and `.github/workflows/ci.yml` carries exactly one change in this
branch — the concurrency scope in §2 item 2, and nothing else — so a reader who
checked that this branch had merged would have concluded all twenty were live. That is worse than [ASPIRATIONAL], because it
converts a known gap into an assumed control at the exact moment nobody is
looking any more. Every one of them now reads **[NOT WRITTEN]**.

### What `ls` answers, measured 2026-08-28T17:45Z

The artifacts this document and `CONTRIBUTING.md` name. Re-run the commands; do
not trust the column.

| Artifact | Command | State when this was written |
|---|---|---|
| `tools/ci/protect-main.sh` | `ls tools/ci/protect-main.sh` | **present** (`bash -n` clean) |
| `tools/ci/preflight.sh` / `.ps1` | `ls tools/ci/preflight.*` | **present** (`bash -n` clean) |
| `.gitignore` covers `.env.*`, `*.pem`, `secrets.json` | `git check-ignore -v .env.production secrets.json apps/api-py/creds.pem` | **all ignored** — in this branch |
| branch protection on `main` | `gh api repos/darshani8/reep-/branches/main/protection` | **404** — nothing applied |
| repository rulesets | `gh api repos/darshani8/reep-/rulesets` | **`[]`** — nothing applied |
| `.github/rulesets/main.json` | `ls .github/rulesets` | **present** — in this branch, and **not applied** (the row above) |
| `.pre-commit-config.yaml` | `ls .pre-commit-config.yaml` | **present** — in this branch, and **not installed** in any clone until someone runs `pre-commit install` |
| `.gitleaks.toml` | `ls .gitleaks.toml` | **present** — in this branch |
| `SECURITY.md` | `ls SECURITY.md` | **absent** — and `protect-main.sh` tells you to record the activation date in it |
| a `Repo hygiene` CI job | `grep -c 'Repo hygiene' .github/workflows/ci.yml` | **0** — `ci.yml` has four jobs |
| `ci-green` / `image_tag` in the deploy | `grep -c 'ci-green\|image_tag' .github/workflows/deploy.yml` | **0** |
| CI's cancellation is scoped to pull requests | `grep -n cancel-in-progress .github/workflows/ci.yml` | **`${{ github.event_name == 'pull_request' }}`** — in this branch |
| any production tag | `git tag` | **empty** |

**There is no staging environment.** Nothing in this document may be read as
claiming one. `docs/phase4-staging-runbook.md` describes a disposable
environment you stand up yourself; it is not a running tier, and no promotion
flows through it. Where a staging step is the right answer, this file says so
and marks it **[ASPIRATIONAL]**.

---

## 1. Who may deploy

**Today: anyone with write access to `github.com/darshani8/reep-`, unreviewed,
by pressing a button.** `gh api repos/darshani8/reep-/collaborators` returns one
account, so in practice that is the owner — but that is a fact about the
collaborator list, not a control. **[IN FORCE]**

Two doors reach production, and both are `workflow_dispatch` with a typed
confirmation word:

- **Actions → Deploy** (`.github/workflows/deploy.yml`) — builds and pushes the
  API image, runs `alembic upgrade head` as a one-off Fargate task, rolls the
  ECS service, builds and publishes the SPA. Confirmation word: `deploy`.
- **Actions → Ops task** (`.github/workflows/ops-task.yml`) — runs one of four
  fixed data commands on the API image. Confirmation word: `run`.

The `guard` job's only assertion is that the operator can spell the word. **It
verifies intent, never correctness.** That is the whole approval story at
present.

**Who hears about it afterwards.** `infra/aws/observability.tf` subscribes one
email address to the `reep-alerts` SNS topic, and every alarm in §8.5 pages
there. The address is `var.alert_email`, which has **no default and no committed
value** — there is no `.tfvars` in `infra/aws/` — so it is whatever was typed at
`terraform apply`, and this repository cannot tell you whose inbox it is. Find
out before you need to know: `terraform -chdir=infra/aws output` or
`aws sns list-subscriptions-by-topic`. An alarm that pages an address nobody
reads is a tripwire with the bell cut off, and the deploying operator is the
person who has to know whether that is the case.

`ops-task.yml` deserves naming separately, because it is the more dangerous
door and it does not look like it: `grant-access` mints a staff account for any
Google address the operator types, and an `ADMIN` created that way
reads every student's marks, attendance and USN by rule 2 (a granted `MENTOR`
reads every student a grant reaches). One person typing
`run` is currently the entire control on that.

**Target state:** a GitHub Environment named `production` with required
reviewers and a deployment branch policy limited to `main`, referenced by
`deploy.yml`'s `api` and `web` jobs and by `ops-task.yml`'s `run` job. GitHub
then holds the job pending until a named human approves and records who did.
`gh api repos/darshani8/reep-/environments` returns `total_count: 0` today.
**[ADMIN — NOT YET APPLIED]**

The AWS OIDC trust policy (`infra/aws/github_oidc.tf`) pins the deploy role's
`sub` to this repository on `refs/heads/main`. That is a genuine second lock and
it stays: it refuses a deploy dispatched from a side branch, and a fork cannot
reach the account at all. **It cannot tell a green `main` commit from a red
one.** **[IN FORCE]**

### When

- Not into a live interview. The realtime interviewer holds WebSockets inside
  the API process; rolling the service cuts them. Check first (§8 has the
  connection instructions):

  ```sql
  select count(*) from interview_sessions where status = 'running';
  ```

  A non-zero count is a student mid-interview. The record survives — three
  layers close a `running` row and `retention.finalize_orphaned_interviews`
  catches the killed process — but the student does not get their interview
  back.
- Not when you are the only person awake and the release carries a migration.
  §7's rollback options all cost more at 2am.
- Not on the strength of "CI was green yesterday". §2 is about that commit, not
  that branch.

---

## 2. The release gate — what must be true of `main`

**A deploy is a claim that a specific commit is fit to serve students. The gate
is what makes that claim checkable.**

Every item is about **the exact sha you are about to dispatch**, not about the
branch in general.

1. **The commit is on `main`, and it got there through a pull request.**
   **[ADMIN — NOT YET APPLIED]** — `main` is not branch-protected today
   (`gh api repos/darshani8/reep-/branches/main/protection` returns 404,
   `/rulesets` returns `[]`), so every CI job in this repository is advisory.
   A direct push bypasses all four. **This is the single control the whole
   document rests on, and it is off.**

   The route that exists is `tools/ci/protect-main.sh`, run by an admin with
   `gh auth login`:

   ```bash
   ./tools/ci/protect-main.sh --show          # confirm it is still 404
   ./tools/ci/protect-main.sh --approvals 0   # apply
   ```

   Pass `--approvals 0`. The script's default is 1, and with one collaborator
   plus `enforce_admins` that makes `main` unmergeable by anybody — GitHub does
   not let an author approve their own pull request. Zero approvals still
   *requires a pull request*, which is the part that forces the branch-and-check
   path. `tools/ci/README.md` has the rest.

   `.github/rulesets/main.json` is in this branch and is the same configuration
   in the *ruleset* API's form — pull request required, `required_approving_review_count: 0`,
   the four checks by their exact display names, `bypass_actors: []`, force push
   and deletion blocked. **[IN THIS BRANCH]**, and applying it is still a
   separate act:

   ```bash
   gh api -X POST repos/darshani8/reep-/rulesets --input .github/rulesets/main.json
   ```

   **Apply one of the two, not both.** Classic branch protection and repository
   rulesets are separate systems; GitHub evaluates both and the most restrictive
   wins, so two sources of truth for one control is how one of them gets relaxed
   while the other is the one everybody reads. `protect-main.sh` detects the
   collision after the fact — it re-reads the live state, finds it is not what
   was requested, warns and exits 1 — which is a diagnosis, not a defence.
   Choose the ruleset (it is reviewable in a diff) or the script (it is one
   command and checks the job names against `ci.yml` first), write down which in
   the commit body, and do not run the other.
2. **All four CI checks concluded `success` on that sha** — not "were
   started", not "were cancelled". These are the four jobs `ci.yml` defines, by
   their exact display names:
   - `API (FastAPI + Postgres)`
   - `API (dependency completeness)`
   - `Voice worker (dependency completeness)`
   - `Web (Angular)`

   A fifth, `Repo hygiene (secrets, ignores, format)`, is described in
   `CONTRIBUTING.md` and elsewhere in this file. **It does not exist**
   (`grep -c 'Repo hygiene' .github/workflows/ci.yml` prints `0`) and no branch
   is writing it. **[NOT WRITTEN]** — do not wait for it and do not add it to a
   required-check list, because a required check that no job reports is a branch
   that never becomes mergeable.

   **A `cancelled` run is neither success nor failure, and this repository has
   already shipped one.** The real sequence, read back from the Actions API:

   | time (UTC) | what |
   |---|---|
   | 13:45:35 | CI run `33176873138` **starts** on the push of `543a265` to `main` |
   | 13:45:39 | Deploy run `33176879314` is dispatched on that same sha — **four seconds later** |
   | 13:45:53 | the deploy's `API → ECR + ECS` job starts building and pushing the image |
   | 13:46:27 | the next push to `main` (`0390f6a`) starts CI run `33176942605` in the same concurrency group |
   | 13:46:33 | `cancel-in-progress: true` kills `543a265`'s `API (FastAPI + Postgres)` job |
   | 13:46:34 | CI run `33176873138` concludes **`cancelled`** |
   | 13:51:44 | the deploy finishes. `543a265` is serving students |

   **The deploy did not race a failed run. It shipped a commit whose tests had
   not finished, and never would.** The four seconds are between CI's *start* and
   the deploy's start; the job was still running for another fifty-four seconds
   after the deploy began, and then it was killed by an unrelated push rather
   than by anything about `543a265`.

   What makes it worse than a missing test: `543a265` is
   *"fix: a published notebook entry now reaches the student who it is about"*,
   and it **carries 134 lines of new tests** in
   `apps/api-py/tests/test_student_programme.py`. The author did the right thing.
   The job that would have run those tests is the one that got cancelled, and
   nothing anywhere read the conclusion. **The test was never the control. The
   test plus a required check is the control.**

   Three of the last thirty push-to-`main` CI runs concluded `cancelled`
   (`gh api "repos/darshani8/reep-/actions/workflows/ci.yml/runs?branch=main&event=push&per_page=30"`).
   The cause was `ci.yml`'s concurrency group: keyed by `github.ref` with
   `cancel-in-progress: true`, so on `main` a run was superseded by the *next
   push* rather than by a newer run of the same change. **[IN THIS BRANCH]** —
   `grep -n cancel-in-progress .github/workflows/ci.yml` now reads
   `${{ github.event_name == 'pull_request' }}`, so a run on `main` always
   reaches a verdict and a superseded run on a branch still costs nothing. That
   edit is a prerequisite rather than a gate: turning on required checks without
   it converts a silent hole into a permanent amber one, because `main` would
   wear a `cancelled` check that no re-run clears and the first person blocked by
   it goes looking for the bypass.
3. **The workflow itself refuses a sha whose checks are not green.** A `ci-green`
   job at the head of `deploy.yml` would query the check-runs for
   `${{ github.sha }}` and exit non-zero unless all four concluded `success`,
   with `api` and `web` declaring `needs: [guard, ci-green]`. **[NOT WRITTEN]** —
   `grep -c ci-green .github/workflows/deploy.yml` prints `0`, and no branch is
   editing that file. **Item 2 is therefore something a human remembers to look
   at, and the timeline above is what happens when they do not.** Open the
   commit on GitHub and read its check list before you type `deploy`.
4. **`alembic heads` prints exactly one row.** Two developers branching off the
   same revision both merge cleanly and only `main`'s own run discovers
   `Multiple head revisions`. **34 `create_type=False` references across 15 of
   the 46 revision files** depend on strict linear ordering, or they raise
   `type "x" does not exist` at apply time — against production, inside the
   migration task, after the image is already pushed. (`e4c1b7a9d203` carries ten
   of them on its own. Re-measure with
   `grep -ho 'create_type=False' apps/api-py/migrations/versions/*.py | wc -l`
   before quoting the number; glob `*.py` rather than recursing the directory, or
   `__pycache__` counts every hit a second time.) Nothing asserts this
   **[NOT WRITTEN]** — run `python -m alembic heads` yourself before dispatching,
   every time.
5. **You can name what you would roll back to** (§3 and §7). If the answer is
   "revert the commit and rebuild", you do not have a rollback, you have a
   second deploy.

---

## 3. Versioning and tagging

**The repository tags nothing.** `git tag` is empty. There is therefore no name
for "the build that was working on Tuesday", and a rollback conversation starts
with archaeology in the Actions log.

### The scheme, and it is deliberately the smallest one that works

**Two names per release, one for machines and one for humans.**

**The machine name is the commit sha, and it is the image tag.** `image_tag`
would default to `github.sha`; the task-definition revision would be registered
pinned to that immutable sha tag; the ECR repository would be
`image_tag_mutability = "IMMUTABLE"`. **[NOT WRITTEN]** — none of those three
changes exists in any branch. Today `infra/aws/ecs.tf:72` hardcodes
`${repository_url}:latest` on a **MUTABLE** repository, so every deploy
overwrites what `latest` means and no earlier build is nameable at all.

**The human name is an annotated git tag on the deployed sha**, one per
production deploy. **This is a human typing a command, and nothing checks that
they did** — `git tag` returns zero tags today, so the scheme below has never
been used once. **[NOT WRITTEN]** as automation; §5.3 asks you to notice its
absence *before* the deploy that needs it, which is the only enforcement there
is. The right fix is a final step in the deploy's `api` job, which is the one
place all four facts exist at the same moment; that step does not exist either.

```bash
git tag -a prod-2026-08-28-1 <sha> -m "image: <sha>
digest: sha256:<the digest ECR reports>
alembic head after deploy: d6a4e7f91b22
pre-migration snapshot: reep-premigrate-<sha>
run: https://github.com/darshani8/reep-/actions/runs/<id>"
git push origin prod-2026-08-28-1
```

**Not semver.** Semver exists to negotiate compatibility with consumers who
choose their own upgrade moment. REEP has one deployment and no external
consumer; nobody has ever asked which minor they are on. What people actually
ask is *"which build was live on Tuesday at three"* and *"what do I type in the
`image_tag` box to get it back"*. A dated tag answers both, needs no changelog
discipline, and cannot be got subtly wrong the way a version bump can.

**What the annotation buys you.** The four lines above are exactly the facts a
rollback needs and the only place they are ever assembled: the image, the
digest that proves which image, the schema state, and the snapshot to restore
to. `git log --oneline prod-2026-08-27-1..prod-2026-08-28-1` then answers "what
actually shipped" without opening a browser, and `git describe --tags` on any
checkout answers "how far past the last production build am I".

**One constraint you must know about, verified:** `infra/aws/ecr.tf` expires
everything past the **last 20 images**. A tag older than twenty deploys names a
commit whose image no longer exists, so rolling back to it is a rebuild, not a
redeploy. Either raise `countNumber` or add a lifecycle rule that keeps tagged
production images — otherwise the tagging scheme promises a rollback target the
registry has already deleted. **[ASPIRATIONAL]** — the lifecycle policy is
unchanged by this branch.

---

## 4. The promotion path, and why the current one is a risk

**Today there is one environment and one hop.** `main` → a human presses Run
workflow → production.

```
[ main ] --workflow_dispatch--> [ PRODUCTION: real cohort, real marks, real USNs ]
```

That is the whole path. **[IN FORCE]**

Four consequences, stated plainly because each one has a different remedy:

1. **Every migration's first contact with a production-shaped database is
   production.** The API test suite runs against a fresh, empty
   `pgvector/pgvector:pg17` service; `alembic upgrade head` on an empty database
   exercises nothing about a table with 5,000 student rows, a lock held for
   eight seconds, or a column that already contains a value the new constraint
   rejects. The affordable interim is §5's pre-migration snapshot and §6's
   expand/contract discipline. The real answer is a staging tier.
   **[ASPIRATIONAL]**
2. **A manual dispatch has no memory.** Nothing records what was deployed,
   when, by whom, or what the schema was before it. §3's tag and
   `$GITHUB_STEP_SUMMARY` are the cheap substitute.
3. **The dispatcher chooses the ref.** A dispatch from a side branch is refused
   by the OIDC `sub` pin — but a dispatch from a `main` whose CI never finished
   is not refused by anything, which is §2 item 3.
4. **`api` and `web` are independent jobs today.** Both declare only
   `needs: guard`, so they run in parallel: a migration that correctly stops the
   API roll leaves the new SPA already halfway to S3. The result is the new
   front end served from CloudFront against the **old** API and a part-applied
   schema — every added screen calling an endpoint that answers 404 — and a
   deploy log showing one red job and one green one. The fix is `web` declaring
   `needs: [guard, ci-green, api]` with an `if:` that still permits `web-only`.
   **[NOT WRITTEN]** — `deploy.yml`'s `web` job still declares `needs: guard`.
   Until it changes, **prefer `api-only`, watch it go green, then dispatch
   `web-only`.** Two dispatches is the whole mitigation and it costs a minute.

**What "staging" would have to mean here to be worth building**, so the
aspiration is concrete rather than a wish: a second Terraform state key, a
`target_environment` input on `deploy.yml`, a restored-from-production-snapshot
database with the roster scrubbed, and a rule that a sha may only be dispatched
to production after it has been dispatched to staging. That is a project, not a
checklist item, which is exactly why it is marked **[ASPIRATIONAL]** instead of
being written as a step people would skip.

---

## 5. The pre-deploy checklist

Run this against **the sha you are about to ship**, in this order.

### 5.1 Secrets — and why the boot guard is the last line, not the first

`Settings.production_boot_failures()` (`apps/api-py/app/config.py`) refuses to
boot on `ENV=prod` when `AUTH_SECRET` is blank, is the value published in this
repository, contains a placeholder marker, or is shorter than 32 characters —
and when `DATABASE_URL` still carries the repo's dev password. **That refusal is
correct and load-bearing and it must never be the thing that catches you.**

What it actually feels like when it does catch you: the migration task and the
new API tasks exit non-zero at lifespan, uvicorn never binds a port, the ECS
circuit breaker throws the deployment away, and `aws ecs wait services-stable`
returns **success** — because the service reconverged, on the old tasks. The
workflow then prints `API deployed: <sha>`. You have a green run, a refusal
buried in `/reep/api`, and the previous build still serving. The guard saved
the students' data and told you nothing.

So check it **before**, at the source, without printing it:

```bash
# length only — never echo a secret into a terminal or a CI log
aws secretsmanager get-secret-value --secret-id <app-secret-arn> \
  --query SecretString --output text | jq -r '.AUTH_SECRET' | wc -c
```

Expect ≥ 33 (64 hex characters plus the newline `wc` counts, for a secret
generated the documented way). Then confirm, from the same secret:

- `DATABASE_URL` does not contain `reep_dev_password`.
- `WEB_ORIGIN` is the exact public origin, scheme included. Wrong, and the
  browser drops the session cookie: every login appears to succeed and then
  behaves as logged-out. `docs/deployment-env.md` names this failure and nothing
  in the stack detects it — §8 is where you catch it.
- `ENV` is a production spelling (`prod`, `production`, `prd`, `live`). Anything
  else — including a typo — is treated as production-like by
  `password_login_allowed`'s allowlist, which is the correct direction, but only
  a recognised name gives you the `Secure` cookie and the seed refusal too.
- (This list carried a `VOICE_WORKER_SECRET` line until Phase 5. The LiveKit
  voice worker was removed in 2026-09 — there is no second image to agree with,
  and nothing under `app/` reads that variable.)

**The guard checks shape, never history.** A secret that leaked last week and
was never rotated passes every one of its tests. §9 is the section for that.

### 5.2 The database backup

**Take the snapshot before the migration, not after.**

```bash
aws rds create-db-snapshot \
  --db-instance-identifier reep-postgres \
  --db-snapshot-identifier reep-premigrate-<sha>
aws rds wait db-snapshot-available --db-snapshot-identifier reep-premigrate-<sha>
```

That is a command you run, at a terminal, before you press the button. As a
gated step in the deploy with the identifier and a UTC timestamp echoed into
`$GITHUB_STEP_SUMMARY`, it is **[NOT WRITTEN]** — `deploy.yml` takes no snapshot
and never has. **If you skip it there is nothing to roll back to**, and §9.3
option 2 is the only option that recovers data.

Why a step and not a shrug: RDS automated snapshots and the AWS Backup vault
(`docs/aws-deployment.md` §1) give you point-in-time recovery, and PITR under
pressure means creating a *new* endpoint, repointing a secret, and knowing the
exact moment to restore **to** — which nothing currently records.
`docs/phase4-staging-runbook.md` requires a `pg_dump -Fc` and its SHA-256 before
touching a **staging** schema. Production, which holds the real cohort and the
`interview_consents` rows whose entire purpose is to stay answerable after a
grant is revoked, currently gets less than staging does.

The existing "Run database migrations" step already refuses to roll the service
on a non-zero migration exit — which is right, and which leaves you with a
half-applied schema and nothing to go back to.

### 5.3 The rest

- `alembic heads` is one row (§2 item 4).
- The release's migrations are expand-only (§6).
- No `running` interview sessions (§1).
- **The previous deploy is tagged at all.** `git tag` currently returns nothing,
  so on the first run of this checklist the honest answer is "no" — tag the
  *currently live* sha before you ship over it (§3), because the moment you need
  that name is the moment you cannot assemble it. Then: you know the previous
  production tag, and its image is still one of the last twenty in ECR (§3).
- If the release changes Knowledge Base content, you will tick `seed_kb`. If it
  does not, you will not — it is idempotent, but a step that runs for no reason
  is a step nobody reads the output of.
- **`python -m app.seed` is not on the menu and never will be.** It creates
  `admin@bgscet.ac.in` — the Main Admin, who by rule 2 reads every student's
  marks, attendance and USN — behind a password published in `AGENTS.md`. It
  refuses on `ENV=prod`; a button for it would be a way around its own guard.
  (This named `director@bgscet.ac.in` until Phase 5. That role and that account
  were removed on 2026-09-10; the argument is unchanged and the account it now
  names is the one that makes it true.)

---

## 6. Migration policy for production

**Expand and contract. Never ship a destructive DDL in the same release as the
code that stops using the column.**

Three releases, minimum, to remove a column:

| Release | Migration | Code |
|---|---|---|
| N — **expand** | add the new column/table, nullable, no constraint that existing rows violate | writes both, reads the old |
| N+1 — **migrate** | backfill (its own revision, no DDL) | reads the new, still tolerates the old |
| N+2 — **contract** | drop the old column | no longer mentions it |

### Why, specifically in this deployment

**1. The old code runs against the new schema, always, on every deploy.**
`deploy.yml` runs `alembic upgrade head` as a one-off task **before**
`update-service --force-new-deployment`, which is the correct order — the new
code never meets an old schema. Its unavoidable consequence is the mirror
image: from the moment the migration commits until the last old task drains
(`api_min_tasks` is 2, plus `deregistration_delay = 30`), **the previous
release's code is serving students against the new schema.** A `DROP COLUMN` in
that window means every request an old task handles that touches the table 500s.
The deploy is green throughout.

**2. Rollback is asymmetric and there is no fixing that.** Redeploying the
previous image does not un-drop a column. §7 spells out what it actually costs.
Expand/contract makes rollback a code-only operation for two releases out of
three, which is the entire point.

**3. The downgrade bodies are code that has only ever been read.** There are 46
of them in `apps/api-py/migrations/versions/` and not one has run against a
database with data in it. The head revision's own downgrade sets an embedding
column back to `nullable=False` — which fails on any database where a worker
left a row NULL, i.e. exactly the state the upgrade exists to permit. A CI step exercising
`alembic downgrade -1 && alembic upgrade head` on an empty database is
**[NOT WRITTEN]**, and even once written it is a syntax check, not a rollback
plan. **Do not run
`alembic downgrade` against production.**

### Rules that follow

- **One concern per revision.** A backfill and a DDL in the same revision cannot
  be reasoned about separately when one of them fails halfway.
- **No long lock on a table students read.** The migration task holds
  `ACCESS EXCLUSIVE` for the length of the DDL while the service is live and
  serving. Add columns without defaults; add the default in a second statement;
  create indexes `CONCURRENTLY` in their own revision (Alembic needs
  `autocommit_block()` for that).
- **Enums are the repeat offender.** `AGENTS.md` lists the three gotchas —
  adding an enum column does not auto-`CREATE TYPE`; a new table reusing an
  existing enum needs
  `postgresql.ENUM(..., name='x', create_type=False)` hand-fixed into the
  autogenerated revision; two columns sharing one enum reuse a single `Enum`
  instance. All three fail **at apply time against the real database**, which on
  this path means inside the migration task, after the image is pushed.
- **`alembic check` must pass** — it fails exactly when a model changed and no
  migration describes it. `env.py` already supplies `target_metadata` and
  `compare_type=True`, so it needs no wiring at all. Nothing runs it:
  **[NOT WRITTEN]**, in CI and in `tools/ci/preflight.sh` both. Run it by hand —
  `cd apps/api-py && python -m alembic check` — and note that this is the single
  most common mistake in this repository, so "by hand" means every time, not when
  you remember.
- **Migrations run once, as their own task.** Never from the API entrypoint:
  every replica would race on the version table on boot and the loser can leave
  the schema half-applied (`docs/deployment-env.md`, "Startup order").

---

## 7. The deploy

Once §2 and §5 pass:

1. **Actions → Deploy → Run workflow.**
2. `target`: `api-and-web`, `api-only` or `web-only`. Prefer the narrowest one
   that ships the change.
3. `run_migrations`: ticked unless you have confirmed the release carries no
   revision. Unticking it on a release that does carry one is how the new code
   meets the old schema.
4. `seed_kb`: only when Knowledge Base content changed.
5. `image_tag`: **there is no such input.** **[NOT WRITTEN]** — the workflow
   builds from the dispatched ref and pushes `:latest`. When it exists it is also
   the rollback input (**§9.1**, which is where rolling back to a previous sha is
   actually described).
6. `confirm`: type `deploy`.
7. **[ADMIN — NOT YET APPLIED]** Approve the `production` environment when GitHub
   holds the job pending. `gh api repos/darshani8/reep-/environments` returns
   `total_count: 0`, so nothing holds and nobody is asked.

The run then does, in order: build and push the image → `alembic upgrade head` as
a one-off Fargate task on the new image, failing the deploy before rolling
anything if it exits non-zero → optionally seed the KB → roll the service and
wait → build and publish the SPA → invalidate the CloudFront entry point. **That
is the whole run.** The pre-migration snapshot, the task-definition revision
pinned to a sha tag, and the post-deploy verify step are all **[NOT WRITTEN]** —
they are §5.2, §3 and §8 as things *you* do, at a terminal, around the button.

**Do not dispatch `ops-task.yml` while a deploy is running.** The two workflows
use different concurrency groups (`deploy-production` and `ops-task`), so a
`grant-access` can execute against a half-rolled image. Both should share
`group: production-mutations`; that is a separate fix. **[ASPIRATIONAL]**

**Neither workflow touches infrastructure.** `terraform apply` stays a human
action at a terminal where the plan can be read first. A button that can
silently recreate a database is not a button worth having.

---

## 8. Post-deploy verification

**`aws ecs wait services-stable` returning success is not evidence.** It returns
as soon as the service reconverges — which is precisely the state ECS reaches
*after* the circuit breaker has thrown your new build away. The workflow then
unconditionally echoes `API deployed: <sha>`. An operator who ships a security
fix, reads that line and closes the tab believes a patched build is running
while the vulnerable one still is.

Verify what is actually serving, in this order.

### 8.1 Which image is running

```bash
aws ecs describe-services --cluster reep --services api \
  --query 'services[0].deployments[?status==`PRIMARY`].[taskDefinition,rolloutState,runningCount,desiredCount]'
```

`rolloutState` must be `COMPLETED`, and the task definition must be the revision
this run registered. Then resolve the image digest that revision points at and
compare it with the digest the build step pushed. A `verify` job doing this and
failing the run on a mismatch is **[NOT WRITTEN]**, so it is two commands you run
yourself, every time — and see §8's opening paragraph for why the workflow's own
`API deployed: <sha>` line is not evidence that it is unnecessary.

### 8.2 The edge — and the trap in it

**Do not curl `https://<domain>/health`.** It will return **200 with HTML** and
prove nothing. The health router is mounted unprefixed (`/health`, `/ready`),
CloudFront routes only `/api/*` to the ALB, and the `spa_fallback` CloudFront
function rewrites any non-file path on the S3 behaviour to `/index.html`. You
get the Angular shell, 200, looking exactly like success. `/api/health` is no
better — it reaches the API and 404s, because no route by that name exists.

`/health` and `/ready` are reachable **at the ALB**, which is locked to
CloudFront's IP ranges. From a laptop, the API's readiness is answered by ECS
and by the target group, not by curl:

```bash
aws elbv2 describe-target-health --target-group-arn <api-tg-arn> \
  --query 'TargetHealthDescriptions[].TargetHealth.State'
```

(The target group's health check path is `/ready`, correctly — it decides
load-balancer membership, not restarts. Pointing a *restart-triggering* probe at
`/ready` turns a Postgres wobble into a restart storm; see
`docs/deployment-env.md`.)

What you *can* curl from the edge, and what each answer means:

```bash
curl -s https://<domain>/api/auth/sso/status | jq
```

- `google_available: true` — sign-in works. `false` means
  `GOOGLE_CLIENT_ID`/`SECRET` did not reach the task, and **nobody can log in**;
  the login screen renders a disabled button with the reason.
- `password_login_available` **must be `false`.** `true` means `ENV` is not a
  production name and the password door is open on the internet. This one line
  is the cheapest read of whether the environment is what you think it is.

```bash
curl -s https://<domain>/api/interview/status | jq   # the interview engine is reachable
curl -s https://<domain>/ | grep -o 'main-[A-Za-z0-9]*\.js'
```

The chunk hash in `index.html` must match the one in this run's `ng build`
output. A mismatch means the invalidation has not propagated, or S3 has last
release's `index.html`, or — the nastier case — `index.html` is new and its
chunks were deleted by `aws s3 sync --delete`, which every route in this app
needs because they are all `loadComponent`. A student's next click then asks for
a chunk that no longer exists.

**Then log in once, in a real browser, and reload the page.** This is the only
check that catches a wrong `WEB_ORIGIN`: the login succeeds, the cookie is
dropped, and the reloaded app behaves as logged-out. No status endpoint reports
it.

If the release regenerated the Material Symbols subset
(`tools/fonts/icon-names.txt`), check an icon renders. Unhashed assets under
`public/` get a one-year immutable cache and are not in the invalidation list,
so a new glyph renders as nothing at all — the exact failure the `.icon` clamp
and the `fonts-ready` gate exist to prevent. **[ASPIRATIONAL]** — syncing
`fonts/**` in its own short-max-age pass and adding `/fonts/*` to the
invalidation is a separate fix.

### 8.3 The logs, and the query that proves turns are being saved

CloudWatch log group `/reep/api`. Four strings worth searching immediately after
a deploy:

- the `production_boot_failures` refusal text — if it is there, the new tasks
  never started and you are looking at the old build (§5.1);
- `Dropped interview turn` — the silent save-nothing failure, and the one AI
  tripwire with a CloudWatch alarm behind it;
  (this line read `OPENAI_API_KEY reached the task` until Phase 5; the `openai`
  engine was deleted in 2026-09 and Nova signs with SigV4, so what this probe
  now proves is a region and a task role, not a key);
- `reep.access` lines carrying `rid=` — the traceability thread
  (`docs/aws-deployment.md` §5).

**Then prove persistence at the database, because the worst failure in this
stack is silent.** Transcript and interview-turn writes are deliberately
fire-and-forget so a bad write can never kill a live call; the cost of that
choice is that dropped turns are invisible from the outside. The conversation is
perfect in the room and empty in the database.

RDS is private. Run these from a one-off ECS task or a temporary bastion —
**never open 5432 to the internet.**

```sql
-- AGENTS.md's runbook query. No 'interview' rows, or a stale max(created_at),
-- means turns are being dropped.
select channel, count(*), max(created_at) from messages group by channel;
```

```sql
-- The interview record's own answer, with no join: emitted > persisted is
-- exactly how many turns were lost.
select status, count(*), max(started_at) from interview_sessions group by status;

select id, status, turns_emitted, turns_persisted, final_phase, close_code
from interview_sessions
order by started_at desc limit 5;
```

A `running` row whose `started_at` is hours old is a record that lies; the
orphan sweeper should have closed it, and if it has not, `retention` is not
running.

**After any deploy that touched `app/interview_*` or `app/conversations.py`, do
a real interview end to end** and then run those queries. There is no substitute:
every layer in that path fails quietly by design.

### 8.4 Record it

Tag the sha (§3). Write the snapshot identifier, the alembic head, the image
digest and who approved into the tag annotation or the run summary. **This is
the input to §9, and it is only ever assembled at deploy time.** Nobody
reconstructs it during an incident.

### 8.5 The first thirty minutes

**§8.1 to §8.4 all finish inside two minutes, and every one of them is a
snapshot.** They prove the right image is serving the right chunks right now.
None of them sees a connection pool that saturates at the tenth concurrent
student, a migration whose lock contention only shows under load, or a route
that 500s for the one role you did not log in as. Those arrive minutes later,
into an inbox — and the deploy has no verification stage at all if the operator
closes the tab before then.

**Watch these until the last one could have fired.** All six alarms already exist
in `infra/aws/observability.tf` and page the single email subscribed to the
`reep-alerts` SNS topic (§1 — find out whose it is *before* you need to). Names
are the terraform `alarm_name` values verbatim, with `var.project` = `reep`:

| Alarm | Fires when | Detection latency |
|---|---|---|
| `reep-no-healthy-api` | `HealthyHostCount` minimum < 1 for **3 × 60 s** | ~3 min |
| `reep-alb-5xx` | `HTTPCode_Target_5XX_Count` sum **> 10** in one 5-min period | ~5 min |
| `reep-interview-dropped-turns` | ≥ 1 `Dropped interview turn` log line in `/reep/api` in one 5-min period | ~5 min |
| `reep-rds-low-storage` | `FreeStorageSpace` minimum < 5 GB | ~5 min |
| `reep-rds-cpu` | RDS `CPUUtilization` average > 85 % for **2 × 5 min** | ~10 min |
| `reep-api-cpu-at-max` | ECS `CPUUtilization` average > 85 % for **3 × 5 min** — autoscaling should have absorbed it, so this means the `api_max_tasks` ceiling | ~15 min |

**Thirty minutes is that table's longest row doubled, not a round number.**
`reep-api-cpu-at-max` needs fifteen minutes of sustained load before it says
anything; leaving after ten proves only that you left.

Check the current state without waiting for mail:

```bash
aws cloudwatch describe-alarms --state-value ALARM --query 'MetricAlarms[].AlarmName'
aws cloudwatch describe-alarms \
  --alarm-names reep-no-healthy-api reep-alb-5xx reep-interview-dropped-turns \
                reep-rds-low-storage reep-rds-cpu reep-api-cpu-at-max \
  --query 'MetricAlarms[].[AlarmName,StateValue,StateUpdatedTimestamp]' --output table
```

`INSUFFICIENT_DATA` is not `OK`, and the six alarms handle it three different
ways — `grep -n 'alarm_name\|treat_missing_data' infra/aws/observability.tf` to
re-derive this:

- `reep-no-healthy-api` sets `breaching`. A metric that stops arriving pages you,
  which is right: no data from a health check is itself the bad news.
- `reep-alb-5xx` and `reep-interview-dropped-turns` set `notBreaching`. Silence
  reads as calm, deliberately — no requests means no 5xx.
- `reep-rds-low-storage`, `reep-rds-cpu` and `reep-api-cpu-at-max` set **nothing**,
  so CloudWatch's default `missing` applies. That is neither of the above: the
  alarm HOLDS ITS PREVIOUS STATE. An alarm that was `OK` before the metric
  stopped stays `OK`, and stays that way indefinitely.

The operational lesson survives all three: a quiet alarm is not evidence of a
healthy system. Know which of the three you are looking at before you read a
green table as a good deploy.

**And Sentry, which is where the errors actually are.** `docs/aws-deployment.md`
§5 calls it the pane of glass and it is the only tool here that sees an
exception: CloudWatch holds raw logs and infra metrics, and no alarm in the table
above fires on a Python traceback. Open the issue stream filtered to **first seen
after this deploy** and look for *new issue types*, not for volume — a route that
500s for the Main Admin only will never move `reep-alb-5xx`'s threshold of ten in five
minutes, and that is exactly the class of bug rule 2 is about. Any Sentry event
carries the `X-Request-ID` that ties it to the `reep.access` line in `/reep/api`.

**Abort criteria — go to §9 without waiting for anyone to complain:**

- `reep-no-healthy-api` enters ALARM at any point. The dashboard is down.
- `reep-alb-5xx` enters ALARM. Ten server errors in five minutes on a cohort this
  size is not noise.
- A new Sentry issue type first seen after this deploy, on any path that reads a
  student row. One event is enough — rule 2 failures do not need volume to be
  serious, they need one wrong reader.
- `reep-interview-dropped-turns` fires and the release touched `app/interview_*`
  or `app/conversations.py`. The interview sounds perfect and saves nothing; that
  is the worst failure in this stack precisely because nobody reports it.

**Two gaps in this instrumentation, so you do not read silence as health:**
`reep-interview-dropped-turns` keys off a log line, so a deploy at an hour when
nobody is interviewing produces no signal whatever the code does — the §8.3
database queries are the only check that works then. And nothing here alarms on
a *wrong answer*: a mentor seeing another mentor's students produces no 5xx, no
exception, and no log line. That is why §2's gate exists at all, and why an
untested change is not made safe by watching it afterwards.

---

## 9. Rollback

### 9.1 Code

**Intended:** dispatch **Deploy** with `image_tag` set to the previous
production sha and `run_migrations` **unticked**. The task-definition revision
pinned to that sha is registered and the service rolls onto it. Minutes, no
rebuild. **[NOT WRITTEN]** — there is no `image_tag` input and no sha-pinned task
definition, in this branch or any other.

**Today:** there is no such input. The task definition hardcodes `:latest`
(`infra/aws/ecs.tf:72`) on a mutable repository, so "the previous build" has no
name — you revert the commit on `main` and run a full rebuild-and-redeploy while
the bad build serves students. **[IN FORCE, and it is the reason step 15 of the
process exists.]**

**The circuit breaker does not save you from this.** `deployment_circuit_breaker
{ rollback = true }` rolls back to the *previous task-definition revision* —
which, while the image reference is `:latest`, is the **same** revision, which
re-resolves `:latest` to the image that just broke. `docs/aws-deployment.md` §4
says the breaker "rolls back a bad image on its own"; that is not what the
mechanism does with a mutable tag. It genuinely protects you from a task that
fails to *start*; it does not protect you from a bad build that starts fine and
serves wrong answers.

### 9.2 The SPA

Rebuilding the previous commit and re-syncing is the whole procedure — but note
that `aws s3 sync --delete` has already deleted the *previous* build's hashed
chunks. While a browser holds the new `index.html` from cache, its chunks are
gone; every `loadComponent` route 404s. Invalidate `/` and `/index.html` again
after the rollback sync and expect a window in which some tabs are broken until
they reload. Dropping `--delete` in favour of S3 versioning plus a noncurrent
lifecycle rule removes this entirely and is a separate fix. **[ASPIRATIONAL]**

### 9.3 The database — read this before you promise anyone a rollback

**A migration that dropped a column is not rolled back by redeploying the old
image.** The old image `SELECT`s a column that no longer exists and 500s on
every request that touches it. Nothing about redeploying changes that. Say this
out loud before shipping a contract migration, not during the incident.

Your actual options, in the order you should want them:

1. **Roll forward.** A new migration that re-adds the column, plus code that
   tolerates it being empty. Fast, no data loss *for rows written since*, but
   the dropped data is gone. This is the right answer far more often than it
   feels like it is.
2. **Restore §5.2's pre-migration snapshot into a new instance and repoint.**
   RDS restores create a **new endpoint**, so this is a snapshot restore *plus* a
   secret update *plus* a service roll, under pressure. And it loses **every
   write since the snapshot** — including `interview_consents` grants, whose
   entire purpose is to stay answerable about what a student agreed to and when.
   A restore that silently drops consent rows makes "was this student consented,
   to what wording, at the time of interview X" unanswerable, which is the one
   question that record exists to answer.
3. **PITR** to a moment you can name. Same new-endpoint cost, and it requires
   knowing the moment — which is why §5.2 echoes a UTC timestamp into the run
   summary.
4. **`alembic downgrade`.** No. See §6: 46 downgrade bodies, none exercised
   against data, and the head revision's own downgrade fails on exactly the
   state its upgrade exists to permit.

**This asymmetry is the argument for §6.** Under expand/contract, two releases
out of three are code-only rollbacks that cost minutes. Skipping it converts
every rollback into option 2.

### 9.4 Whatever you do

Record the sha you rolled back **from** and **to**, the snapshot identifier if
one was used, the alembic head before and after, and the timestamps. The
incident you are in is not the one you will be asked about.

---

## 10. Incidents

Two classes, and they are answered differently. **§10.1–§10.3 are a leaked
credential**: you know what is exposed, and rotation ends it. **§10.4 is
unauthorised access to student data**: you do not yet know what was exposed or to
whom, and the first move is to preserve the evidence that answers those two
questions before anyone starts fixing anything.

**Treat a secret as disclosed the moment it is pasted into a chat, printed to a
log, committed, or pushed — including to a private repository, including if the
commit was force-pushed away.** GitHub retains unreachable objects and serves
them by sha. A force push does not un-publish; only rotation ends the exposure.

### 10.1 `AUTH_SECRET` — the worst case, and the one this repo is shaped around

`AUTH_SECRET` signs the HS256 `reep_session` cookie and derives the OAuth
flow-cookie key. **Whoever knows it is every user.** A forged
`{"role":"ADMIN"}` claim reads every student's marks, attendance and USN,
with no login, no Google round trip and no database row involved.

Sessions are stateless 12-hour JWTs and `POST /api/auth/logout` only deletes a
cookie. **There is no waiting it out that is shorter than rotating.**

1. **Rotate first, investigate second.**

   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

   (This is the exact command `config.py` quotes in its own refusal message, for
   the operator meeting it at 2am.)
2. Write it to Secrets Manager with `aws secretsmanager put-secret-value`, then
   `aws ecs update-service --force-new-deployment` so tasks pick it up. **Every
   live session is signed out.** That is the correct trade and the code says so
   in as many words.
3. Verify the new tasks actually took it: §8.1, then a real login.
4. **The `ENV=prod` boot guard will not help you here.** It tests shape, not
   history: a leaked-but-random 64-hex secret satisfies every one of its checks
   and boots happily. The guard defends against *never having set* a secret, not
   against having lost one.
5. **This is a personal-data incident, not an ops one.** The exposure window is
   from first disclosure to rotation, and during it any reader of that value
   could have read every student's record. Rotating it closes the hole and
   answers nothing about who read what — **continue at §10.4**, which is the half
   this document used to declare in scope and out of scope in the same sentence.

### 10.2 The others, briefly

| Leaked | Do |
|---|---|
| `DATABASE_URL` / RDS password | Rotate the master password, update the secret, roll the service. Blast radius is whoever can reach the VPC — the instance is in private subnets and 5432 is not public. Check RDS logs for connections you cannot account for. |
| `GOOGLE_CLIENT_SECRET` | Rotate in the Google console, update the secret, roll. Sign-in is down until both sides match; plan the minute. |
| Provider keys (`GROQ_API_KEY`, `MISTRAL_API_KEY`, …) | Revoke at the provider **first**. The API degrades — the assistant and the resume polish fall back, and an unreachable interview engine closes the socket 4001 — a visible, safe failure, which is why revocation-first is safe here and not for `AUTH_SECRET`. |
| An AWS access key | Revoke in IAM. Note that the deploy path uses OIDC and stores no key, so a leaked AWS key came from somewhere that should not have had one — find that first. |

### 10.3 Then clean up, then close the hole

- **Purge the value wherever it was captured**: the Actions run log (delete the
  run), Sentry events, the workflow step summary, any chat transcript. Rotation
  has already made it useless; leaving it lying around teaches the next reader
  that leaked secrets are survivable.
- **Then the preventive half**, because none of the above stopped this one:
  - `gitleaks` in `.pre-commit-config.yaml` with a rules file for **this repo's
    own shapes** — `AUTH_SECRET=<64 hex>`, `VOICE_WORKER_SECRET=`, a
    `postgresql+psycopg://` URL whose password is not `reep_dev_password`.
    **[IN THIS BRANCH]** — `.pre-commit-config.yaml` wires the gitleaks hook and
    `.gitleaks.toml` carries the rules for this repository's own shapes,
    including `reep-auth-secret` and `reep-bare-token-hex` (a 64-character hex
    string next to the words secret / token / key / auth, which is the shape
    `secrets.token_hex(32)` produces and no vendor scanner recognises). **But a
    hook is not a control, and this one is doing nothing in your clone right
    now:** hooks live in `.git/`, which is not cloned, so the file is inert until
    each person runs `pre-commit install` — and `git commit --no-verify` skips it
    afterwards. It catches an accident on the machine where it is installed. That
    is worth having and it is not a gate.
  - The same scan as a **blocking CI step** in a `Repo hygiene (secrets, ignores,
    format)` job, which would be the authoritative copy. **[NOT WRITTEN]** —
    `ci.yml` has four jobs and this is not one of them.
  - **Secret-scanning non-provider patterns** on the repository.
    **[ADMIN — NOT YET APPLIED]**, and it is the highest-leverage unapplied
    setting after branch protection, because it is the only one that acts
    **before the object reaches the remote**. Push protection is already enabled
    and already rejects a leaked *AWS* key at the server, but non-provider
    patterns are `disabled` — and REEP's own secrets are exactly non-provider
    shapes: `AUTH_SECRET` is `secrets.token_hex(32)`, a bare 64-character hex
    string matching no vendor's issued-token format. So the one asset whose
    exposure is unrecoverable except by rotation is the one asset nothing scans
    for. Settings → Code security → Secret scanning → custom patterns. One
    click, by `darshani8`, and nothing in this repository can do it.
  - `.gitignore` as a negated allowlist (`.env`, `.env.*`, `!.env.example`),
    plus `*.pem`, `*.key`, `secrets.json`, `service-account*.json`.
    **[IN THIS BRANCH]** — this is the one preventive item that is actually
    written. `git check-ignore -v .env.production .env.prod .env.staging
    secrets.json apps/api-py/creds.pem` names a rule for all five; before this
    branch it named none. `AGENTS.md`'s ENV allowlist teaches operators to think
    in environment names, so `.env.production` is the exact filename a real box
    grows. It stops an accident, not a decision: `git add -f` still works.
- **Rehearse the rotation before you need it.** Change `AUTH_SECRET` on a
  laptop, confirm every existing session is rejected and a fresh login works. A
  rotation nobody has performed is a second incident inside the first one — the
  same argument this repository already makes twice about backups: *a backup
  that has never been restored is a hope, not a backup.*

### 10.4 Unauthorised access to student data

The incident this codebase is actually shaped around: a rule-2 regression, a
staff account minted by `ops-task.yml`'s `grant-access` for the wrong address,
a leaked `AUTH_SECRET` used before it was rotated, or a mentor who could see a
cohort that was not theirs. What is exposed is a named student's marks,
attendance, USN, interview transcripts and — where
`INTERVIEW_RECORDING_ENABLED` was on — per-speaker audio.

**Capture before you fix. The fix destroys the evidence.**

Rolling the service rotates tasks and starts a new log stream; a hotfix deploy
changes the code you are trying to characterise; `retention` deletes interview
records on a 180-day clock. Do these first, and write down the wall-clock time
you started:

1. **Freeze the window.** Note the first and last sha you believe carried the
   defect, and the deploy timestamps from the Actions runs (§8.4's tag
   annotation, if it exists). That pair is the exposure window and everything
   below is scoped to it.
2. **Export the access log for the window before it ages out.** `/reep/api` has
   `retention_in_days = 30`, so this is a real deadline, not a formality:

   ```bash
   aws logs create-export-task --log-group-name /reep/api \
     --from <epoch-ms> --to <epoch-ms> --destination <s3-bucket> \
     --destination-prefix incident-<date>
   ```

   The `reep.access` lines carry `rid=<X-Request-ID>`, which is the thread from a
   request to its Sentry event (`docs/aws-deployment.md` §5). That is how you
   answer *which* rows, not just how many.
3. **Snapshot the database**, named for the incident, not for a deploy:
   `aws rds create-db-snapshot --db-snapshot-identifier reep-incident-<date>`.
   It preserves `interview_sessions`, `interview_consents` and `mentor_notes` as
   they stood, which a later rollback or a retention sweep would not.
4. **Only then fix**: revert or roll forward per §9, and re-check §8.5.

**Scoping who saw what.** Who accessed which student, over the window, comes
from the access log's `rid=` lines joined to the paths that name a student id —
`/api/mentor/students/{id}/...` and the `student_screens` reads. The interview
record answers its own half without a join: `interview_sessions` rows carry the
student, the phase reached and the `consent_id` pinning the exact grant, so *"was
this student consented, to what wording, at the time of interview X"* stays
answerable even after the student revoked. **Do not delete those rows to tidy
up.** They are the only thing that makes the answer falsifiable.

**Telling people.** This is the part no committed file can decide, so it is
written as the smallest thing that is still a commitment rather than a shrug:

- **Who decides:** the repository owner escalates to the college's placement
  office / data-protection contact. **That contact is not named anywhere in this
  repository, and naming it is a one-line change somebody has to make.**
  **[ASPIRATIONAL]** until they do — a disclosure path whose first step is "find
  out who to call" is a disclosure path that runs at the speed of a phone tree,
  during the hour it matters.
- **By when:** escalate within **24 hours of confirming** unauthorised access,
  whether or not the cause is understood. A cause is not a prerequisite for a
  disclosure; the count of affected students is, and step 2 above is how you get
  it.
- **What goes in it:** the window, the number of students whose records were
  reachable, the categories (marks / attendance / USN / interview transcript /
  audio), whether the access was demonstrated or merely possible, and what has
  been done. Do not send a first note that omits the categories — audio and
  transcripts are consented separately from marks, and a report that blurs them
  misrepresents what the student agreed to.

**Blameless, and say so out loud.** Every rule in `AGENTS.md` and in this file
exists because a past failure got explained instead of hidden. The next one is
worth more than this one; that is §11.

---

## 11. After it is over

**Within a week of any incident or rolled-back deploy: one page, in
`docs/incidents/`, or in the body of the commit that fixes it.** Four beats, the
same shape the pull-request template asks for:

1. **Symptom** — what a student, mentor or operator would have said was wrong.
2. **Mechanism** — what actually caused it, at the file and function level.
3. **Which existing gate should have caught this, and why it did not.** This is
   the only beat that is not optional, because it is the one that produces the
   next rule. Answers this repository has already given, and their results: *"CI
   ran but nothing read its conclusion"* → §2 item 3. *"The manifest was
   incomplete and the import was lazy, so boot and tests both passed"* → the
   `api-imports` job. *"The guard fired and the workflow still printed
   success"* → §8's opening paragraph.
4. **What changes** — a gate, a test, a refusal, or explicitly nothing and why
   nothing is right.

**Explicitly blameless and explicitly short.** One page. A post-mortem culture
dies of length before it dies of blame: a template that takes an afternoon gets
skipped, and a skipped post-mortem is indistinguishable from a repo where
nothing ever went wrong.

**Why this section exists at all.** Every rule above is justified by a named past
failure — `543a265` deployed while its own tests were still running,
`app/interview_local.py` reaching `main` with an undeclared numpy import, a voice
manifest that shipped with four packages missing. Those rules exist because
somebody sat down afterwards and reasoned. This process consumes that output on
every page; without §11 it produces none of its own, and the gate list stops
growing from evidence and starts growing from taste. **[NOT WRITTEN]** as
anything mechanical — no template, no `docs/incidents/` directory, no reminder.
It is a paragraph asking for a habit, which is the weakest kind of control here
and is still better than the nothing that preceded it.

---

## Appendix — the gap list, in one place

Things this document describes that **are not enforced by anything**. Kept
together so the list is auditable by command rather than by reading nine
sections. Measured 2026-08-28; re-measure rather than trusting the table.

### Needs an admin. No committed file can do these.

| Gap | Marker | Who closes it, and how |
|---|---|---|
| `main` is not branch-protected; all four CI jobs are advisory and a direct push bypasses every one | **[ADMIN — NOT YET APPLIED]** | `darshani8` applies **one** of: `gh api -X POST repos/darshani8/reep-/rulesets --input .github/rulesets/main.json`, or `./tools/ci/protect-main.sh --approvals 0`. Not both (§2 item 1). Verify: `gh api repos/darshani8/reep-/rulesets` stops returning `[]`, or `.../branches/main/protection` stops returning 404. **This is the one that makes every other row matter.** |
| Secret scanning knows no non-provider patterns, and `AUTH_SECRET` is a bare 64-hex string matching no vendor format | **[ADMIN — NOT YET APPLIED]** | Settings → Code security → Secret scanning → custom patterns. The only listed control that acts *before* the object reaches the remote. |
| No `production` GitHub Environment; no reviewer on any deploy or ops task, including `grant-access` | **[ADMIN — NOT YET APPLIED]** | Settings → Environments. `gh api repos/darshani8/reep-/environments` returns `total_count: 0`. |
| Dependabot alerts disabled | **[ADMIN — NOT YET APPLIED]** | Settings → Code security |

### Described here, written nowhere. These are files somebody has to write.

Every row is a claim this document or `CONTRIBUTING.md` makes about a mechanism
that does not exist. The command in the middle column is how you check whether
that is still true.

| Gap | Check | Marker |
|---|---|---|
| No `ci-green` job: the deploy will ship a sha whose checks never finished (§2 item 3) | `grep -c ci-green .github/workflows/deploy.yml` → `0` | **[NOT WRITTEN]** |
| No `image_tag` input and no sha-pinned task definition, so no rollback target has a name (§3, §9.1) | `grep -c image_tag .github/workflows/deploy.yml` → `0` | **[NOT WRITTEN]** |
| `web` does not depend on `api`, so a failed migration still ships the new SPA against the old API (§4 item 4) | `grep -n 'needs:' .github/workflows/deploy.yml` | **[NOT WRITTEN]** |
| No pre-migration snapshot step; §5.2 is a command a human types (§5.2) | `grep -c create-db-snapshot .github/workflows/deploy.yml` → `0` | **[NOT WRITTEN]** |
| No `verify` job comparing the running digest with the pushed one (§8.1) | `grep -c verify .github/workflows/deploy.yml` | **[NOT WRITTEN]** |
| No `alembic check`, no one-head assertion, no downgrade round trip — in CI or in `preflight.sh` (§2 item 4, §6) | `grep -c 'alembic check' .github/workflows/ci.yml tools/ci/preflight.sh` | **[NOT WRITTEN]** |
| No `Repo hygiene (secrets, ignores, format)` job. Referenced as a fifth required check; `ci.yml` has four jobs (§2 item 2, §10.3) | `grep -c 'Repo hygiene' .github/workflows/ci.yml` → `0` | **[NOT WRITTEN]** |
| No `SECURITY.md`, which `protect-main.sh` tells the operator to record the activation date in | `ls SECURITY.md` | **[NOT WRITTEN]** |
| Nothing installs the pre-commit hook, so `.pre-commit-config.yaml` is inert until each person runs `pre-commit install` (§10.3) | `ls .git/hooks/pre-commit` | **[NOT WRITTEN]**, and unfixable by a committed file |
| Nothing tags a production deploy, and nothing notices that nothing did. `git tag` is empty (§3, §5.3) | `git tag` | **[NOT WRITTEN]** |
| No incident write-up habit, no `docs/incidents/`, no template (§11) | `ls docs/incidents` | **[NOT WRITTEN]** |
| The college data-protection contact for §10.4 is named nowhere in this repository | — | **[ASPIRATIONAL]** |

### Written, and in this branch

| Item | Check |
|---|---|
| `.gitignore` covers `.env.*`, `*.pem`, `*.key`, `secrets.json`, `service-account*.json` (§10.3) | `git check-ignore -v .env.production secrets.json apps/api-py/creds.pem` names a rule for each |
| `tools/ci/protect-main.sh` — the script that applies the row at the top of this appendix | `bash -n tools/ci/protect-main.sh` exits 0 |
| `tools/ci/preflight.sh` / `.ps1` — the four CI checks, locally, before pushing | `bash -n tools/ci/preflight.sh` exits 0 |
| CI's cancellation scoped to pull requests, so a run on `main` always reaches a verdict (§2 item 2) | `grep -n cancel-in-progress .github/workflows/ci.yml` |
| `.github/rulesets/main.json` — the ruleset payload, reviewable in a diff. **Written, not applied** (see the admin table above) | `ls .github/rulesets` |
| `.pre-commit-config.yaml` + `.gitleaks.toml` — gitleaks with this repo's own secret shapes. **Written, not installed** in any clone (row above) | `ls .pre-commit-config.yaml .gitleaks.toml` |

### Genuinely a project, not a checklist item

| Gap | Marker | Who closes it |
|---|---|---|
| No staging tier; every migration meets production first | **[ASPIRATIONAL]** | §4 says what it would have to mean |
| ECR keeps 20 images, so a tag older than twenty deploys names an image that no longer exists | **[ASPIRATIONAL]** | `infra/aws/ecr.tf` lifecycle rule |
| `s3 sync --delete` removes the previous build's chunks, so a rollback breaks cached tabs | **[ASPIRATIONAL]** | Drop `--delete`, add versioning + lifecycle |
| `fonts/**` cached a year and never invalidated | **[ASPIRATIONAL]** | Separate sync pass + `/fonts/*` invalidation |
| `deploy.yml` and `ops-task.yml` do not share a concurrency group, so `grant-access` can run against a half-rolled image | **[ASPIRATIONAL]** | `group: production-mutations` on both |

### One contradiction between documents, still unresolved

`docs/aws-deployment.md` §4 says, in the present tense, that
`aws ecs update-service --force-new-deployment` is safe because "the circuit
breaker rolls back a bad image on its own". **§9.1 of this file says that is not
what the mechanism does**, and the mechanism is checkable: `infra/aws/ecr.tf`
sets the repository MUTABLE and `infra/aws/ecs.tf:72` pins the task definition to
`:latest`, so the breaker's rollback target is the *same* task-definition
revision, which re-resolves `:latest` to the image that just broke.

Both sentences are in the repository right now and they cannot both be true. The
one an operator opens during an outage is the older one — it is the file the
issue chooser used to point at for "a deploy that went wrong". Correcting it is
one line in `docs/aws-deployment.md` §4 pointing here; **this branch does not
touch that file**, so until someone does, read §9.1 as the authority and treat
the breaker as protection against a task that fails to *start*, not against a
build that starts fine and serves wrong answers.
