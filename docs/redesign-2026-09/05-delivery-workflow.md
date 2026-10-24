# 05 · Delivery workflow

How the approved design and backlog reach production without breaking the students, faculty and admin who use REEP today.

## 1. Phases, order, dependencies

| Phase | Branch | Scope | Depends on | Backend change |
|---|---|---|---|---|
| **0 — Design system + shell** | `feat/redesign-p0-design-system` | tokens into `reep-v2.scss`, fonts (retire Orbitron/Chakra Petch), new app bar with scope control (hidden until B1.2), sidebar groups, page-head/card/chip/button/field/note/tabs/grid classes, ECharts theme registration, AG Grid install + theme, icon subset additions, `interview-report-card` restyle | — | none |
| **1 — Student restyles** | `feat/redesign-p1-student` | Sign in (3 boards), Skilling (claim card frozen), Time Sheet, Leaderboards, Faculty / TPO Log, Resume Builder (16 sections), REEP Agent — all restyle-only | 0 | none |
| **2 — Admin console on existing APIs** | `feat/redesign-p2-admin-ui` | every admin screen in its new dress on the endpoints that exist today; plan-driven parts rendered as disabled/"coming with Phase 3" only where the board has them; Faculty directory split from Mentor mapping; Account page; Interview records grid on the current list; Governance grant panel with the scope selector greyed | 0 | none (feature-flag the new console per user with `session.capabilities` until accepted, or ship behind `?console=v2` for the first review) |
| **3 — Backend choke points** | `feat/redesign-p3-scoping` | B1 (scoping), B2 (functions/governance), B3 (faculty lifecycle), B15 (account), B14 (exports) | 2 | migrations + backfills |
| **4 — Plan-driven features** | one branch per area: `feat/redesign-p4-semesters` (B4, B13), `-imports-analytics` (B8, B12), `-interviews` (B5, B6, B17 + student interview screens), `-mentoring` (B9, B7), `-leave` (B10), `-registrations` (B11) | 3 (and 1 for the student interview screens) | migrations + jobs |
| **5 — Cleanup + hardening** | `chore/redesign-p5-cleanup` | dead endpoints (B8.4), dead client code (`ThemeService`, `IconComponent`, kit), `AGENTS.md` + `docs/` truthfulness pass, `tools/ci/preflight.sh` fixed, ruleset check list corrected, CDK harden (B3.7) with the owner | 4 | — |

Rules of order: Phase 1 and Phase 2 can run in parallel after 0; Phase 3 starts only after Phase 2 is deployed and accepted (the screens then get their plan-driven parts turned on); Phase 4 areas can run in parallel on separate branches but merge one at a time (each carries migrations — single Alembic head).

## 2. Per-phase loop (what Claude Code does)

1. `git switch -c <branch>` from a fresh `main`; read the phase prompt (`06`), the relevant spec sections and boards.
2. Write the plan as a checklist in the PR description first (files to touch, migrations, endpoints, tests, boards covered).
3. Implement in small commits (conventional: `feat(admin): …`, `feat(student): …`, `feat(api): …`, `test: …`, `docs: …`).
4. Tests: backend `cd apps/api-py && python -m pytest -q` (Postgres on 5433 via `docker compose up -d`, `alembic upgrade head`, `python -m app.seed`); `python tools/ci/check_pii_gate.py`; `python tools/ci/check_api_imports.py`; web `npx tsc --noEmit -p tsconfig.app.json && npx ng test --watch=false && npx ng build`; infra `cd infra/cdk && pytest` when touched. Then `tools/ci/preflight.sh`.
5. Browser verification against the running app (`npx ng serve` + uvicorn) as the seeded logins (`student@bgscet.ac.in/student123`, `mentor@…/mentor123`, `admin@…/admin123`); compare each screen with its board at 1440 px; check empty/loading/error states; keyboard pass (tab order, focus ring, live regions).
6. Open the PR with the template (Why, Rule 1 / Rule 2 boxes, migrations + backfill noted, screenshots of each screen next to its board). CI must be green. The owner reviews against `07-acceptance-checklist.md` and merges.
7. Owner deploys: Actions → **Deploy** (`target`, `run_migrations=true`, `confirm=deploy`). Migrations run as a one-off task before the service rolls; a failed migration leaves the service untouched. Verify `/ready`, sign in as each role, smoke the changed screens.

## 3. Migrations, backfills, flags

- One Alembic head at all times; rebase before merge. Every migration has `downgrade()`; enum rules from `AGENTS.md`.
- **Expand → backfill → switch → contract.** Add the column/table and backfill in one release; switch the code to read it in the same or next release; drop old columns only a release after nothing writes them. Never destructive in the same deploy.
- Backfills that touch many rows run as a one-off ops task (`ops-task.yml` gets a `backfill` task) or inside the migration when < 1 minute on prod data.
- `purge_people.VERDICTS` / `purge_students.STUDENT_VERDICTS` get a verdict for **every new table** (their tests fail otherwise — that is the point).
- Feature gates: server first (`require_capability`, `features_for`), client reads `GET /api/auth/me`. Phase 2's new console can be gated per user with a temporary capability `ui.console_v2` (Main Admin baseline) until accepted, then the gate is removed in Phase 5.
- Retention/purge jobs learn about new tables (`interview_score_summaries` never purged; audit append-only).

## 4. Test data

`python -m app.seed` stays the dev baseline. Add to it (dev only, refused in prod as today): a second college with its own domain and admin function, two departments with faculty (one with a group, one without, one disabled), a batch in semester 3 of 4 with results imported for semesters 1–2, four interview sessions for the seeded student with evaluations (42 → 71) so the trend and summaries render, SWOC entries with authors, a leave request at FIRST_APPROVED, an interview policy row with `store_audio=false`. Every board's populated state must be reachable with seed data.

## 5. Browser verification harness

The repo already warns about harness false alarms (`AGENTS.md`). For repeatable checks use Playwright against the dev stack: sign in through `POST /api/auth/login`, open each route, screenshot at 1440 px and compare with `design/*/*.png` by eye; assert no element overflows its container and no `text-overflow` truncation in grids (the redesign was built with that check). Keep the scripts under `tools/ui-check/` and out of CI unless they are stable.

## 6. Rollback

Deploy from an older ref re-dispatches the same workflow. Because migrations are expand-first, a code rollback never needs a schema rollback within a release. If a migration must be reverted, run `alembic downgrade -1` as a one-off task **before** redeploying the old image, and only for migrations whose `downgrade()` is loss-free (documented in the PR).

## 7. Documentation to keep true

- `AGENTS.md`: update the sections on roles/functions (B2), consent → policy (B6.1), promotion/graduation (B4), imports (B8), leave chain (B10), the admin sidebar list, and remove the stale "four CI checks"/Terraform/LiveKit lines in Phase 5.
- `docs/redesign-2026-09/` (this kit) is the design record; add `CHANGELOG.md` there per phase (what shipped, what was deferred).
- `.github/rulesets/main.json` and `tools/ci/protect-main.sh`: required checks = the five real jobs (drop the dead voice-worker check).

## 8. Working agreements with Claude Code

- Never merge its own PR; never deploy; never touch production secrets or `infra/cdk` core without an explicit go.
- Write code the way `09-coding-standards.md` says: simple, explicit, named after the stakeholder concept; leave touched code clearer than found without changing behaviour.
- Prefer editing existing components over parallel new ones; delete the old markup when the new screen ships (no two versions of a screen).
- When a board and the code disagree on copy, keep the code's copy unless the board's copy implements the admin-discretion rule.
- Report at the end of each phase: what shipped, test counts, screens verified, what was deferred and why.
