# 06 · Phase prompts — paste one at a time into Claude Code

Each prompt assumes the master prompt (`00`) was pasted first in the same session, or paste it again at the top of a new session. Replace nothing; the paths are real. Every phase's definition of done includes: code follows `09-coding-standards.md` (stakeholder naming, no shortcut operators, router → service → query, named `computed`s) and the PR carries its review checklist.

---

## Phase 0 — Design system + shell

> Implement Phase 0 of `docs/redesign-2026-09/05-delivery-workflow.md` on branch `feat/redesign-p0-design-system`.
>
> Read `docs/redesign-2026-09/01-design-system.md`, `design/tokens.css`, `design/echarts-theme.json`, `design/ag-grid-theme.ts`, and open `design/admin/DesignSystem.html`, `design/admin/Main.html`, `design/student/TimeSheetRedesign.html` in a browser at 1440 px.
>
> Do:
> 1. Merge `design/tokens.css` into `apps/web/src/styles/reep-v2.scss` keeping every existing token name (add the new ones; `--brand-magenta` may only be used inside `--primary-gradient` — add a comment and a stylelint-free grep test in `tools/ci/` that fails if `--brand-magenta` or `#ba2185` appears outside the gradient definitions).
> 2. Fonts: Plus Jakarta Sans (display) + Inter (UI) everywhere including login, register, resume builder; retire Orbitron and Chakra Petch from `fonts.scss`/`tools/fonts/fetch-fonts.sh` (keep the files until Phase 5). Check `reep-v2-resume.scss` does not redefine the class names you add.
> 3. Shell (`apps/web/src/app/layout/app-shell.component.*`): app bar (52 px, brand mark, console name per role, admin search + scope control placeholder + bell/help, avatar + name/USN, Sign out), sidebar groups as in `01` §3 (student items unchanged; admin groups OVERVIEW / INSTITUTION / OPERATIONS / STUDENT INSIGHT / GOVERNANCE / TOOLS; faculty items unchanged), page-head pattern (crumb → h1 → sub + actions), active nav pill with the gradient.
> 4. Global classes from `01` §4: card, kpi tile, chip (+dot), buttons (primary/secondary/danger/ghost/icon, one primary per view), select, field/input (+synced), note, steps, tabs, tree-row, meter, avatar, the plain data-table dress, the dark stage.
> 5. Install `ag-grid-community` + `ag-grid-angular`, register `reepGridTheme`; register the ECharts theme `reep` once (lazy chunk). Keep the initial bundle under the 400 kB budget — both libraries only in lazily loaded routes.
> 6. Icon subset: add every glyph listed in `01` §6 to `tools/fonts/icon-names.txt` and regenerate.
> 7. Restyle the shared `interview-report-card` component to the card/tiles pattern (nullable score = dash, calibration note kept).
> 8. Tooling (`08-tooling-mcp.md`): extend `.mcp.json` with Context7, Postgres MCP (restricted, dev DB), the dev-only FastAPI MCP mount (`fastapi-mcp` in `requirements-dev.txt`, mounted at `/mcp` only when `ENV` is dev and `MCP_ENABLED=true`, cookie forwarded) plus `python -m app.dev_session`, and the Sentry MCP entry; add the guard test that the mount stays dev-only and out of `requirements.txt`.
>
> Verify: every existing screen still renders (no layout breaks) as student, mentor, admin, alumni; `ng build` within budget; `tools/ci/preflight.sh` green. Open a PR with before/after screenshots of the shell for each role. Definition of done: tokens, shell and classes merged; no screen redesigned yet.

---

## Phase 1 — Student restyles (no backend change)

> Implement Phase 1 on branch `feat/redesign-p1-student`. Read `docs/redesign-2026-09/03-student-portal-spec.md` §1–§6 and §9 and open the matching boards in `design/student/`.
>
> Rules: restyle only — same content, fields, copy, states and endpoints as `main`; nothing added. The "Claim a skill with a certificate" card on Skilling keeps its current markup and styles exactly. Apply the copy rule in `03` ("staff your placement office has given access") where main's copy makes an access statement.
>
> Screens, in this order: Sign in (`features/login`, three layouts incl. 390 px), Time Sheet (`features/student/ledger`), Leaderboards, Faculty / TPO Log (`features/student/mentor-log`, with the request form state), Skilling, REEP Agent (`features/agent`, one component for all roles — the admin card comes in Phase 2), Resume Builder (`features/student/resume`, all 16 rail sections; Tailor/Preview/Export stay as they are inside the new frame).
>
> For each screen: list the board's elements → map each to the existing template element and endpoint → restyle → verify empty/loading/error states still exist → screenshot at 1440 px beside the board. Keyboard and live-region behaviour must not regress.
>
> Definition of done: all seven screens match their boards; no API change; `ng build` in budget; PR with side-by-side screenshots.

---

## Phase 2 — Admin console on existing APIs

> Implement Phase 2 on branch `feat/redesign-p2-admin-ui`. Read `docs/redesign-2026-09/02-admin-console-spec.md` and open every board in `design/admin/`.
>
> Build each admin screen in its new dress on the endpoints marked **Existing API** in `02`. Where a board shows a plan-driven element whose task (`B…`) is not merged yet, render it disabled with the tooltip "Available with Phase 3/4" — never fake data, never a dead control that looks live. Keep every existing route; add the new routes from `02` (`/admin/colleges`, `/admin/students/:id`, `/admin/faculty`, `/admin/faculty/new`, `/admin/imports`, `/admin/governance/features`, `/admin/audit`, `/account`) with their capability guards. Gate the new console behind the temporary capability `ui.console_v2` (Main Admin baseline) so the owner can review it on production before it replaces the old screens; the old screens stay reachable until Phase 3 is accepted.
>
> Use AG Grid (theme `reepGridTheme`) for the grids listed in `01` §5 and ECharts (theme `reep`) for Analytics (one composite chart: readiness % + attendance % left axis, skilling hours bars right axis, offers count second right axis; legend toggles; emphasis; crosshair tooltip; dataZoom) and Placement (funnel + donut). Mentor load becomes a paginated grid — delete the 5-mentor sunburst sample.
>
> Definition of done: all 24 screens + 3 dialogs render on real data with the seed; the three reference boards are not screens; PR with a screenshot per board.

---

## Phase 3 — Backend choke points (scoping, functions, faculty lifecycle)

> Implement Phase 3 on branch `feat/redesign-p3-scoping`: tasks B1.1–B1.5, B2.1–B2.7, B3.1–B3.6, B14, B15 from `docs/redesign-2026-09/04-backend-changes.md`. Read `AGENTS.md` sections on governance, Rule 2, the institutional spine and the enum gotchas first.
>
> Order: B1.1 → B1.2 (scope helper + backfill every grant to BGSCET) → B2.3 (functions; backfill one grant per `mentors` row BEFORE shrinking the baseline) → B2.1 → B2.2 → B1.4 → B1.5 → B1.3 → B3.x → B2.4–B2.7 → B14 → B15. Every task ships with tests; add guard tests to `tests/test_codebase_guards.py` for: every catalogue key is enforced, `_refuse_second_main_admin` unchanged, audit table never purged, `--brand-magenta` rule (web).
>
> Migrations follow expand → backfill → switch; `purge_people.VERDICTS` and `purge_students.STUDENT_VERDICTS` get every new table. Then wire the Phase 2 screens' disabled controls that these tasks unlock (scope control in the app bar, Grant function panel with scope target, Faculty drawer actions, Disable faculty dialog, Audit log, Account page, Exports history).
>
> Definition of done: all listed tasks merged with tests; compatibility guardrails from `04` verified by tests (existing grants still work, faculty with groups keep their functions, cross-department pairs flagged not broken); `AGENTS.md` updated.

---

## Phase 4a — Semesters, promotion, graduation, catalogue

> Branch `feat/redesign-p4-semesters`: tasks B4.1–B4.5 and B13. Wire the Students & batches actions, the Promote batch and Graduate batch dialogs, Student 360, Institution structure course fields and Catalogue scoping. Extend `app.seed` with a batch in semester 3 of 4 and results for semesters 1–2. Guard test: promotion rewrites nothing (results/ledger/interviews/badges keep their semester numbers).

## Phase 4b — Imports, criteria, analytics, jobs & placement

> Branch `feat/redesign-p4-imports-analytics`: tasks B8.1–B8.6, B12.1–B12.3. Wire Data imports & criteria, Analytics (series + KPIs + alerts), Jobs sheet close/scope, Placement funnel/KPIs/export. Add `openpyxl` to `requirements.txt`. Decide alerts: build B8.3 or delete the rules — no decorative rules survive. Add the nightly snapshot scheduler to CDK only with the owner's go (it touches `reep_core`).

## Phase 4c — Interviews (tracks, policy, records) + student mock-interview screens

> Branch `feat/redesign-p4-interviews`: tasks B5.1–B5.5, B6.1–B6.7, B17, then the student boards `InterviewStart`, `InterviewLive`, `InterviewReport` (and `InterviewHistory` only after the owner confirms) from `03` §7–§8, and the admin Interview records + Question bank screens. Rules: students have **no** control over transcript/recording — the policy card is read-only; the client posts the acknowledgement at Start with the policy's scopes; remove Change/Withdraw from the UI; nullable scores render as a dash; the daily cap counts completed sessions only; summaries are backfilled before the next retention run. Extend `app.seed` with four evaluated sessions (42 → 71) and a policy row. Interview tests (nova/records/consent/write path) must stay green — extend, do not weaken.

## Phase 4d — Mentoring: mapping history, SWOC

> Branch `feat/redesign-p4-mentoring`: tasks B9.1–B9.4 and B7.1–B7.7. Wire Mentor mapping (history, handover, bulk, filters), SWOC (scope, ownership, history, semester view, acknowledgement) and the student Faculty / TPO Log's author/date lines (already designed in the SWOC tiles). Faculty screens keep their templates; the handover read grant is honoured by `policies.assert_student_scope`.

## Phase 4e — Leave

> Branch `feat/redesign-p4-leave`: tasks B10.1–B10.8. Wire Leave approvals (chain, policy card, attachments, alternate acceptance, calendar, cancel) and the paper PDF. Faculty leave screen keeps its template; only the API contract additions (attachments, cancel, alternate acceptance) are surfaced there with minimal controls.

## Phase 4f — Registrations

> Branch `feat/redesign-p4-registrations`: tasks B11.1–B11.4. Wire the scoped queue, checks, Hold, Seating rules editor and the corrected copy.

---

## Phase 5 — Cleanup, docs, hardening

> Branch `chore/redesign-p5-cleanup`: remove `ui.console_v2` and the old admin screens; delete dead code (B8.4 endpoints, `ThemeService`, `IconComponent`, unused kit components, `Dockerfile.voice`, compose voice-worker), retire the old fonts; fix `tools/ci/preflight.sh`, `.github/rulesets/main.json`, `tools/ci/protect-main.sh` (five real checks); truthfulness pass over `AGENTS.md`, `README.md`, `docs/` (delete or mark stale docs); write `docs/redesign-2026-09/CHANGELOG.md`. With the owner: deploy the CDK harden phase (B3.7) and verify SES.
