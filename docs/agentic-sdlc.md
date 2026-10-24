# Agentic SDLC for REEP

One maintainer, a large rulebook (AGENTS.md) and five required checks. The aim
of this setup is to move the routine parts of the cycle — turning a clear issue
into a PR, the first review, fixing red CI — onto Claude, and keep the human on
the decisions: what to build, whether to merge, and every deploy.

```
 you file an issue ──▶ label `agent` / comment @claude ──▶ Claude opens a draft PR
                                                              │
      you merge ◀── CI green + review clean ◀── Claude review comments, CI runs
                                                              │
                          @claude "fix this" on the PR ◀──────┘  (as many rounds as needed)
```

## The whole lifecycle, and who does each step

| Phase | Agent / workflow | What it does | Human |
|---|---|---|---|
| Plan | Agent task issue template | Four sections the agent works from | You write the issue |
| Code | `claude.yml` | `agent` label or `@claude` → branch, code, checks, draft PR | — |
| Test | `ci.yml` (unchanged) | The five required checks | — |
| Review | `claude-review.yml` | Advisory review against rule 1, rule 2, schema, frontend | You merge |
| Release | `agent-release.yml` + `tools/ci/release_gate.py` | After green CI on `main`: gate, release notes in a `release` issue | — |
| Deploy | `agent-release.yml` → `deploy.yml` | Ships safe changes automatically when `AGENT_AUTODEPLOY=true` | Anything the gate refuses |
| Verify | `agent-release.yml` | Watches the deploy; closes the release issue or triages the failure | — |
| Operate | `agent-ops.yml` → `agent-triage.yml` | Infra drift, failed human deploys, `incident` issues → diagnosis + draft fix PR | `cdk-deploy.yml`, console checks |
| Maintain | `agent-maintenance.yml` | Weekly: vulnerabilities, outdated deps, stale PRs/issues, AGENTS.md drift | Merges the bump PR |

Skills the agents follow: `.claude/skills/ship`, `steward`, `new-migration`,
`ops-triage`, `maintenance`.

### What an agent may deploy on its own

Decided by `tools/ci/release_gate.py` — code, not a model, pinned by
`apps/api-py/tests/test_release_gate.py`. It compares `main` with the last
**successful** production deploy and refuses the automatic path for:
migrations and models, `infra/`, rule 1 / rule 2 / auth / deletion code, the
service worker and PWA manifest, dependency manifests and the Dockerfile, the
deploy pipeline or the gate itself, unknown paths, and more than 40 files.
Automatic deploys always run with `run_migrations=false`, which is safe only
because any migration in the change set already refuses the automatic path.
Refused releases still get notes and the exact button to press.

It also refuses to dispatch when `main` has moved past the commit that was
gated (`deploy.yml` builds whatever `main` is at that moment).

## Setup (once, about five minutes)

1. **Install the Claude GitHub app** on `darshani8/reep-`:
   https://github.com/apps/claude — or run `/install-github-app` in a local
   Claude Code session, which does steps 1–2 for you.
2. **Add one secret** under Settings → Secrets and variables → Actions:
   - `CLAUDE_CODE_OAUTH_TOKEN` — uses your Claude subscription; get it with
     `claude setup-token`; **or**
   - `ANTHROPIC_API_KEY` — pay-per-use from console.anthropic.com.
3. **Create the labels** `agent`, `release`, `incident`, `maintenance`
   (Issues → Labels → New label).
4. **Turn on automatic deploys** when you are ready: Settings → Secrets and
   variables → Actions → **Variables** → `AGENT_AUTODEPLOY` = `true`. Leave it
   unset and everything else still runs; releases wait for your click.
5. **Optional — Sentry to incidents:** in Sentry, add an alert rule whose
   action creates a GitHub issue in this repository with the label `incident`.

Nothing else changes: the ruleset, the five required checks and CODEOWNERS are
untouched, and neither workflow can merge or deploy.

## Daily use

- **Small, clear change** → New issue → "Agent task" → fill it in → add the
  `agent` label. A draft PR appears; review it like any other.
- **Change a PR** → comment `@claude <what to change>` on it (or on a line in a
  review). It pushes to that PR's branch.
- **A question** → `@claude why does /student/time-log lock after two days?`
  on any issue. It answers from the code.
- **Locally / on claude.ai/code** → ask for the change, then "ship it"; the
  `ship` skill runs the checks and writes the PR the same way.

## Guard rails, and why each exists

- **Only owner/members/collaborators can start a run.** The agent has write
  access and reads issue text as instructions.
- **Every agent commit is authored by Darshan Gowda B B, with no co-author.**
  `.github/actions/sole-author` sets the git identity through environment
  variables (which beat the action's own `git config`) and installs a
  commit-msg hook that deletes Co-authored-by / Claude-Session lines. Every
  agent workflow that can commit runs it right after checkout.
- **No production credentials in either workflow.** Rule 1 applies to agents:
  a run that can read student records can paste them into a comment. Keep AWS
  keys and real `DATABASE_URL`s out of `claude.yml` and `claude-review.yml`.
- **Only the release workflow deploys, and only what the gate allows.**
  `cdk-deploy.yml` (AWS infrastructure) and `ops-task.yml` (seeds, purges)
  stay human-only: no agent can run them.
- **No agent can see AWS or the database.** Triage works from GitHub Actions
  logs and code. A read-only AWS role for CloudWatch is a separate decision.
- **The agent stops on judgement calls.** Auth, migrations that rewrite rows
  and `infra/` get a proposal, not a push.
- **Not CI jobs.** §34 pins `ci.yml`'s five job names; these live in their own
  files so they can never become a required check that does not report.

## What it costs and what it does not do

Each run spends tokens on your key or subscription; the review is skipped on
drafts and cancelled when a newer push arrives to keep that down. The agent in
Actions has no Postgres, so the `API (FastAPI + Postgres)` check is left to
CI — the PR says which checks it ran and which it could not. It is a first
reviewer, not a second maintainer: merging, infrastructure changes and anything the gate refuses are still yours.

`workflow_run` and `schedule` triggers only fire for workflows on the default
branch, so the release, ops and maintenance agents start working once this is
merged to `main`.
