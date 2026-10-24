# Master prompt — paste this as the first message in Claude Code

> You are implementing the approved 2026-09 redesign of REEP (`darshani8/reep-`, branch `main`) — the new **Admin Console**, the new **Student Portal**, and the **future-implementation backlog** they were designed on. Faculty screens are **not** redesigned. Everything you need is in `docs/redesign-2026-09/` in this repo. Read this whole message, then `docs/redesign-2026-09/README.md`, then `AGENTS.md` (`CLAUDE.md` points at it), before you touch a file.

## 1. Sources of truth, in order

1. **The code on `main`.** When a document and the code disagree, the code is right. `docs/*.md` outside `docs/redesign-2026-09/` are known to be stale (they still describe LiveKit, Terraform, the DIRECTOR role, "four CI checks"). Do not implement from them.
2. **`AGENTS.md`** — the house rules. Rule 1 (student-data egress gate), Rule 2 (mentor scope), one Main Admin, no DIRECTOR, "a screen that is routed is not a screen anyone can reach", `.icon` ligature/subset rules, the two global stylesheets that must not claim each other's names, the browser-harness warnings.
3. **`docs/redesign-2026-09/`** — this kit: the design system (`01`), the admin spec (`02`), the student spec (`03`), the backend changes (`04`), the delivery workflow (`05`), the phase prompts (`06`), the acceptance checklist (`07`), the tooling (`08`), the coding standards (`09`), and the boards under `design/`.
4. **The boards** (`design/admin/*.html`, `design/student/*.html`) are the pixel reference. Open them in a browser at 1440 px wide. The PNGs beside them are the rendered reference.

## 2. What is approved and what is not

- **Approved: the Admin Console** — 30 boards: Sign in (Main Admin door), Analytics, Colleges, Institution structure, Students & batches, Student 360, Faculty, Add faculty (wizard), Mentor mapping, Registrations, Data imports & criteria, Catalogue, Leave approvals, Jobs sheet, Placement & offers, Interview question bank, Interview records, SWOC notes, Roles & functions, Student feature switches, Audit log, Exports, REEP Agent, My account, three dialogs (Promote batch, Graduate batch, Disable faculty), plus the Design system, Coverage and Compatibility boards (reference only).
- **Approved: the Student Portal** — Sign in (redesign, states, mobile 390), Skilling (the "Claim a skill with a certificate" card is unchanged from main — keep its markup and styles exactly), Time Sheet, Leaderboards, Faculty / TPO Log (+ request-a-meeting state), Resume Builder (all 16 rail sections), Mock interview (start, live session, practice report, history), REEP Agent.
- **Restyle vs redesign.** Most student screens are **restyles**: same content, same fields, same copy, same endpoints as `main` — only the dress changes. Do not add sections, KPIs, filters or features to a restyled screen. The **mock interview** screens are **plan-driven**: they add what `04-backend-changes.md` §7 specifies, nothing more.
- **Not approved / do not build:** any faculty screen redesign (`features/mentor/*` keep their templates; they may inherit the new shell and tokens only), a student-side "request removal" of interview records, student on/off switches for interview transcript or voice recording, a co-mentor model, a student leave screen, a mentor-side SWOC screen (those are listed as "Not in this design" and stay in the backlog).

## 3. Rules you keep on every change

- **Rule 1** — every `complete_chat` / `stream_chat` call declares `carries_student_data=`; student records never go to an off-machine model unless the gate allows. The CI job `pii-gate` enforces it.
- **Rule 2** — staff scope is derived from role + grant + (new) scope target. A MENTOR with no `mentors` row sees nobody. Never read a missing field as "whole programme".
- **Admin discretion over access** — a *role* is identity (STUDENT, FACULTY/MENTOR, ADMIN, ALUMNI); a *function* is a scoped, expiring, audited grant (mentor, HOD, placement officer, college admin, verifier…). Nothing that a grant could decide is hard-wired to a role. UI copy never says "only your mentor can…"; say "staff your placement office has given access".
- **Students control nothing about recording.** What is streamed, kept and recorded in a mock interview is the placement office's policy for the programme; the student only starts and ends an interview. The report and feedback stay as designed.
- **One Main Admin** (`grant_access._refuse_second_main_admin` stays). College/department admins are FACULTY accounts holding scoped `admin.*` functions, not a second ADMIN role.
- **Additive history, never rewrites** — promotion, graduation, reassignment, disabling all add rows; nothing is re-keyed or deleted. Every write is audited through `architecture_events.record_change`.
- **Status is icon/dot + label, never colour alone.** One primary (gradient) action per view. Nullable scores render as a dash, never 0.
- **Code is simple and named after the people who use it** (`09-coding-standards.md`): files → classes → methods → functions → variables carry stakeholder names (Student, Faculty, Batch, Mentor group, Interview policy…), no abbreviations, no clever shortcuts (no walrus, nested comprehensions, nested ternaries, `and/or` control flow, `!!`, `!`, `any`), rule numbers as named constants, router → service → query, template conditions as named `computed`s, tests that read as sentences.
- **Nothing lands without tests** — backend `pytest` (needs Postgres), `tools/ci/check_pii_gate.py`, `tools/ci/check_api_imports.py`, web `tsc --noEmit`, `ng test`, `ng build` under the 400 kB initial budget, CDK synth tests when infra changes. Run `tools/ci/preflight.sh` before every PR (fix it first if it still calls the deleted voice-worker check).

## 4. How you work

- One phase at a time, in the order of `05-delivery-workflow.md`. One branch per phase (`feat/redesign-p<N>-<slug>`), small PRs inside it, conventional commits (`feat|fix|docs|refactor|chore|ci|infra|security|test`), PR body from `.github/PULL_REQUEST_TEMPLATE.md` (tick Rule 1 / Rule 2).
- Before coding a screen: open its board, list every element on it, map each to an existing endpoint or to a task in `04`. If something on the board has no endpoint and no task, stop and ask — do not invent an API.
- Migrations: Alembic, single head, every schema change has a backfill for existing rows and an `downgrade()`; never drop a column in the same release that stops writing it. Enum gotchas are in `AGENTS.md`.
- Feature flags: server-side capability/feature checks, exposed through `GET /api/auth/me`; the client never decides access on its own.
- Keep `AGENTS.md` truthful: when you change a behaviour it documents, edit that section in the same PR.
- When the harness misleads you (see `AGENTS.md` "testing this stack in a browser harness"), verify in a real browser against the running app (`npx ng serve` + uvicorn + `python -m app.seed`).
- Do not deploy. Deploy is a manual GitHub Actions run by the owner after the acceptance checklist is ticked.
- Ask before: deleting data, changing an endpoint's response shape that a shipped client reads, touching `infra/cdk` core, or anything that touches production secrets.

## 5. Tools you must use (`08-tooling-mcp.md`)

- **Context7 MCP** before any library call you are not certain of (Angular 22, AG Grid, ECharts, FastAPI, SQLAlchemy, Alembic, Pydantic v2, boto3).
- **Postgres MCP** (restricted mode, dev database on `localhost:5433`) before and after every migration: schema read, row counts, sample of backfilled rows, `EXPLAIN` on new scoped queries.
- **FastAPI MCP** (dev-only mount at `/mcp`, `REEP_DEV_SESSION` cookie) to call the endpoint a screen reads as the seeded role and compare with the board.
- **Sentry MCP** for traceability after each deploy: issues by release sha on `reep-api` / `reep-web`, stack traces, traces, cron monitors; keep `X-Request-ID`, the scrubbers and one Sentry init per process.
- Register them in `.mcp.json` in Phase 0 (`08` §1); secrets only from the environment.

## 6. Start

Reply with: (a) the list of files you read, (b) the Phase 0 plan as a checklist with the files you will create/modify, and (c) any conflict you found between the kit and the code on `main`. Then wait for "go".
