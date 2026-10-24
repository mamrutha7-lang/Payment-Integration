---
name: steward
description: Repo-specific rules for driving a REEP pull request to green and mergeable — how to read CI failures here, which ones are real, and what never to do to get green. Read before acting on CI or review events on a PR you opened or drive.
---

# Driving a REEP PR to green

## The five required checks

`API (FastAPI + Postgres)`, `Rule 1 (every model call declares its cargo)`,
`API (dependency completeness)`, `Web (Angular)`, `Infra (CDK synth guards)`.
Those display names are pinned in four files (§34 in
`apps/api-py/tests/test_codebase_guards.py`); never rename a job to fix a PR.
`Claude review` and `Claude` are advisory and never required.

## Reading a red check

- **Every failure is real until proven otherwise.** There are no known-flaky
  tests in this suite. A pytest failure that passes on re-run locally usually
  means two runs shared one database — reproduce on a fresh database
  (`createdb reep_<name>` + `DATABASE_URL=...`, AGENTS.md) before calling it a
  flake.
- `API (dependency completeness)` red → an import is missing from
  `requirements.txt` (runtime, pinned `==`). Add it there, not only in
  `requirements-dev.txt`.
- `Rule 1` red → a model call without `carries_student_data=`. Add the keyword;
  decide True/False from what the prompt actually carries.
- `Web (Angular)` red on the bundle budget → something was made eager. Restore
  `loadComponent`; never raise the budget.
- `test_codebase_guards.py` red → read the guard's docstring first; it says
  what invariant broke and why. Fix the code, not the guard.
- `alembic` "Multiple head revisions" → re-parent your migration's
  `down_revision` onto the current head. Never `alembic merge`.

## Never

- Skip, `xfail`, delete or weaken a test or guard to get green.
- Push an empty commit or close/reopen to re-trigger CI.
- Force-push someone else's branch; merge `main` in instead.
- Add AWS credentials or a real `DATABASE_URL` to a workflow.
- Deploy. `cdk-deploy.yml` and `ops-task.yml` are human-only.

## Review comments

Small, local asks (rename, test, nit) → do them in the next push. Anything
touching auth, rule 1/2, migrations that rewrite rows, or `infra/` → reply
with a proposal and let the owner decide.
