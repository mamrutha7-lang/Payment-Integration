<!--
  This template is a checklist, not a gate. Nothing here blocks a merge — only the
  required status checks on `main` do that, and those are the FIVE named in Checks
  below, declared in `.github/rulesets/main.json` and applied by
  `tools/ci/protect-main.sh`. A ticked box is a claim, not evidence. What a checklist
  buys is narrower and still worth having: after this file exists, "I did not know
  rule 1 applied to that call site" stops being available to anyone.

  Delete nothing. If a section does not apply, tick its "not applicable" box — an
  untouched section reads as an unanswered question, and a reviewer cannot tell the
  difference between "no PII here" and "I skipped that part".
-->

Closes #___  <!-- or: no issue — one line saying why. If an issue exists, quote its
rule-1 and rule-2 answers here rather than re-deriving them; the feature template
asks both before any code exists, which is the cheapest place to answer them. -->

## Why

<!--
  Write this as the commit body, because that is what it becomes. AGENTS.md's rules
  were written FROM these paragraphs — every "this is deliberately a refusal and not
  a warning" in that file started as someone explaining themselves in a PR. 44 of the
  last 120 commits have an empty body (measured with
  `git log -120 --pretty=format:%b`), which is why the reasoning behind a third of
  the guards in this repo is not recorded anywhere.

  Four beats. Two sentences each is plenty.
-->

**Symptom** — what a student, mentor or operator would have said was wrong.

**Mechanism** — what actually causes it, at the file and function level.

**Why this fix and not the obvious alternative** — name the alternative you rejected.

**Deliberately not done** — the scope you cut, so the next person does not read it as an oversight.

---

## Rule 1 — student data must not leave the machine unbidden

`student_data_egress_allowed()` in `apps/api-py/app/ai/llm.py` is opt-IN by omission:
`carries_student_data` defaults to `False` in both `complete_chat` and `stream_chat`.
A handler that composes a student's marks, attendance and USN into a prompt and
forgets the keyword raises nothing, logs nothing, and goes green. Tick one:

- [ ] This PR adds no new call to `complete_chat` / `stream_chat`.
- [ ] It adds one, and **every** new call site passes `carries_student_data=` as an explicit keyword. (Search your own diff for both names — the reviewer will.)
- [ ] It adds one that passes `carries_student_data=False` on purpose, because the prompt carries only public data (a job posting, KB policy text). The prompt-composition lines are in this diff so a reviewer can confirm that.

## Rule 2 — staff scope is decided by role, not by a missing field

`require_mentor` (`apps/api-py/app/routers/mentor.py`) admits MENTOR and ADMIN —
DIRECTOR was removed as a role in 2026-09-10 and `require_admin` is now the one
console gate. It is **not** a student-scope check. Narrowing to a student is
`_assert_can_access_student` (same file), and **a MENTOR with no `Mentor` group sees
NOBODY**. A new `GET /mentor/students/{student_id}/...` that calls `require_mentor` and
then queries by the path id reads exactly like every gated handler around it and admits
every MENTOR to the whole programme.

`assert_student_scope` (`app/policies.py`) is NO LONGER a second implementation — it is
the only one. `_assert_can_access_student` delegates to it (see that function's
docstring), so the 29 call sites across six routers and the 8 in `app/routers/redesign.py`
now get the same answer: the MENTOR-group narrowing, plus the tenant check, plus a
refusal for a session with no userId. Either name is correct to call. What is never
correct is a THIRD one: AGENTS.md names `_assert_can_access_student` as the gate to
import, and the whole point of that delegation is that rule 2 cannot answer differently
depending on which URL a request happened to hit. Tick one:

- [ ] This PR adds no route with `{student_id}` in its path and changes no existing scope check.
- [ ] It adds one, and the handler reaches `_assert_can_access_student` before it touches a student row.
- [ ] It adds one that is intentionally Main-Admin-only, gated by `require_admin`, and the PR says why the cohort-wide read is correct.
- [ ] It adds one that calls `assert_student_scope` directly (the `/v1` routers' idiom). Fine — same gate — as long as it is that function and not a fresh `if role == 'MENTOR'` written inline.

## Tests

<!--
  This section exists because everything else in this file is about a required check,
  and a required check only re-asks a question some test already asked. Delete the
  `if not mentor_id` branch in mentor.py and `tests/test_mentee_records.py` goes red —
  that is the entire reason rule 2 survives a refactor. A guard shipped without a test
  is a comment: it holds until the next person tidies it, and nothing notices.

  "The suites pass" is the Checks section below. This one asks whether this change is
  covered at all.
-->

Tick one:

- [ ] **Adds a test that fails without this change.** Name it: `_______________` (path::test_name). You ran it against the unpatched code and watched it fail — a test written after the fix and never seen red proves the fix compiles, not that it works.
- [ ] **Covered by an existing test.** Name it: `_______________`. One line on which assertion in it breaks if this change is reverted.
- [ ] **Ships without one, deliberately.** Why: `_______________`. Docs, comments, a token rename, a workflow edit are legitimate answers. "Hard to test" is not — it describes the change's shape, and it is the sentence that precedes every untested guard in this repository.

## Schema

- [ ] No model under `apps/api-py/app/models/` changed.
- [ ] A model changed **and** this PR includes the matching revision in `apps/api-py/migrations/versions/`.
- [ ] The new module is imported in `app/models/__init__.py` (otherwise autogenerate never sees it), and `python -m alembic heads` still prints exactly **one** row.
- [ ] Any enum work follows AGENTS.md's three gotchas — new column on an existing table creates the type first; a new table reusing an existing enum uses `postgresql.ENUM(..., name='x', create_type=False)`.

<!--
  Two revisions generated off the same parent both merge cleanly and collide on main
  as "Multiple head revisions". 34 create_type=False references across 15 of the 70
  revision files depend on strict linear ordering, or they raise `type "x" does not
  exist` at apply time — on production, inside the migration task, after the image is
  already pushed. Never resolve a collision with `alembic merge`: it makes two heads
  into one without deciding which CREATE TYPE runs first.

  Re-measure before quoting that number anywhere (glob *.py, so __pycache__ does not
  get counted twice):
      grep -ho 'create_type=False' apps/api-py/migrations/versions/*.py | wc -l
-->

## Config, auth and deploy guards

- [ ] This PR does not touch `app/config.py`, `app/security.py`, `app/google_auth.py`, `app/identity.py`, `.github/workflows/**`, `infra/aws/**` or `infra/cdk/**`.
- [ ] It does, and the PR body above says which guard changed and what it now refuses. Specifically: `production_boot_failures()` must still refuse to boot on a repo-default `AUTH_SECRET`, and `password_login_allowed` must remain an **allowlist** of dev/CI environment names — never `not is_prod`, because an unrecognised `ENV` has to shut the password door, not open it.

## Checks

CI runs five jobs against this PR head — "API (FastAPI + Postgres)", "Rule 1 (every
model call declares its cargo)", "API (dependency completeness)", "Web (Angular)" and
"Infra (CDK synth guards)". The voice worker's job went with the LiveKit stack it
checked. Run them locally first, in one command rather than five typed from memory:

```
./tools/ci/preflight.sh            # all five, in the order that fails fastest
./tools/ci/preflight.sh --quick    # the two fast ones only — NOT sufficient for a PR
.\tools\ci\preflight.ps1           # same thing from PowerShell: it finds bash and hands over
```

Postgres must be up for the pytest check: `docker compose up -d`. **Exit 2 means
nothing failed but something did not RUN**, and a check that did not run is not a
check that passed — that is the same distinction `REEP_REQUIRE_DB=1` draws in
`tests/conftest.py`. `--clean-deps` asks the two dependency-completeness questions the
way CI asks them, from a throwaway venv built from the manifest alone; without it they
run in your existing venvs, which is a weaker question and the script says so.

- [ ] All five pass locally — **exit 0, not exit 2** — or all five are green on this PR.
- [ ] If a dependency was added, it is in the right manifest: `requirements.txt` is runtime-only and pinned `==` (it is what the Dockerfile installs), `requirements-dev.txt` is test-only. "API (dependency completeness)" installs `requirements.txt` ALONE, so a lazy import inside a request handler does not save you.
- [ ] No route in `apps/web/src/app/app.routes.ts` was changed from `loadComponent` to a static `component:`. One re-eager-ed route fails `ng build` on the bundle budget.
- [ ] If this PR touches a template or a component under `apps/web/src`, `check_form_submit.py` still passes: something must own every `<form>`'s submit — `[formGroup]`, a `(submit)` binding that prevents the default, `ngNoForm`, or `FormsModule` in the component's decorator imports. A bare `<form (ngSubmit)="…">` compiles, builds, type-checks and never fires, and the unprevented native submit reloads the page and drops the query string.
- [ ] If this PR touches `apps/web/src/styles/**` or a theme file, the three design-system guards in the "Web (Angular)" job still pass: `check_brand_magenta.py` (the retired magenta is not referenced), `check_style_duplicates.py` (a selector is not newly declared in BOTH global sheets — `reep-v2-resume.scss` loads last and wins app-wide), and `check_theme_tokens.py` (the grid and chart themes copy tokens as literals, because AG Grid writes its variables into a shadow root; the copies must still equal `reep-v2.scss`).

## What a reviewer should look at twice

<!-- The line you are least sure about. Naming it is not weakness; it is the whole point of review. -->
