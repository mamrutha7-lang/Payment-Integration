---
name: ship
description: Finish a change in REEP the way a reviewer expects — branch, run the checks CI will run, commit with the house four-beat body, open a PR that fills in the template. Use when a change is written and the user (or an issue) asks to commit, push, open a PR or "ship it".
---

# Ship a change

The goal is a PR that goes green on the first push and that a reviewer can
judge from its description alone. One validated push beats three speculative
ones.

## 1. Branch

Never commit to `main`, `stage` or `dev`. The branching strategy is
`docs/branching-strategy.md`: work is cut from `dev` and its PR targets `dev`
(`git fetch origin dev && git switch -c feature/<short-slug> origin/dev`; `fix/`,
`chore/`, `docs/` are fine too). An urgent production fix is the one exception:
cut `hotfix/<slug>` from `main`, open its PR against `main`, then a second PR of
the same branch into `dev`. A PR into `main` from anything but `stage` or
`hotfix/*` is refused by the "Branch policy (promotion path)" check. If
`origin/dev` does not exist yet, the strategy is not switched on: branch from
and target `main` as before. If the session names a branch, use that.

## 2. Run the checks for what you touched

Run the ones that apply; they are the same commands `.github/workflows/ci.yml`
runs. Postgres is not always available (e.g. in the GitHub Action) — if a check
cannot run, say so in the PR; **a check that did not run is not a check that
passed**.

| Touched | Run (from repo root unless noted) |
|---|---|
| any `apps/api-py/app/**` | `python tools/ci/check_pii_gate.py` |
| any `apps/api-py/app/**` | `cd apps/api-py && python -m pytest -q <the test modules for the area>` (needs Postgres on 5433; see AGENTS.md "One thing at a time touches one database") |
| `requirements*.txt` or a new import | `cd apps/api-py && python ../../tools/ci/check_api_imports.py` |
| `apps/web/src/**` | `python tools/ci/check_brand_magenta.py && python tools/ci/check_style_duplicates.py && python tools/ci/check_theme_tokens.py && python tools/ci/check_form_submit.py` |
| `apps/web/src/**` | `cd apps/web && npx tsc --noEmit -p tsconfig.app.json && npx ng test --watch=false && npx ng build` |
| `infra/cdk/**` | `cd infra/cdk && python -m pytest -q` |
| everything, with a local stack | `./tools/ci/preflight.sh` (exit 0 = pass, 1 = fail, 2 = something did not run) |

Fix every failure before committing. Never skip, disable or loosen a test or a
guard to get green — the guards in `tests/test_codebase_guards.py` exist
because each one was once broken silently.

## 3. Re-read your own diff adversarially

`git diff` and ask what would make CI or a reviewer reject it:

- Rule 1: any new `complete_chat` / `stream_chat` passes `carries_student_data=`.
- Rule 2: any new `{student_id}` route reaches `_assert_can_access_student`.
- A model changed → a migration exists (see the `new-migration` skill).
- A route stayed `loadComponent`; a `<form>` has a submit owner.
- Nothing unrelated crept in. Keep the change minimal.

## 4. Commit

Subject: `type(area): what changed, in the user's words` (see `git log`).
Body — the four beats the PR template asks for, two sentences each:

```
Symptom: what a student, mentor or operator saw.
Mechanism: the file and function that caused it.
Why this fix: and the obvious alternative you rejected.
Not done: the scope you cut on purpose.
```

End with whatever attribution lines the session requires.

If the change fixes a bug or a production error, add its dated entry to
`docs/bug-log.md` in the same commit (newest first; the format is at the top
of that file). A problem you found and did not fix goes under its "Open"
list with the date it was found.

## 5. Push and open the PR

`git push -u origin <branch>`, then open a **draft** PR against the base step 1
named (`gh pr create --draft --base dev`, or `--base main` for a `hotfix/*`)
whose body mirrors
`.github/pull_request_template.md`: fill every section, tick exactly one box
where it asks for one, and name the test that covers the change. Write the
checks you ran and their results under "Checks", including any that could not
run and why. Put the line you are least sure about under "What a reviewer
should look at twice".

If `AGENTS.md` describes the behaviour you changed, update that paragraph in
the same PR — it is loaded into every agent session and a stale sentence there
misleads the next one.
