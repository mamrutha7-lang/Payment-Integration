# REEP redesign — implementation kit for Claude Code

Approved on 2026-09-12: the **Admin Console** new design (30 boards) and the **Student Portal** new design (29 boards), both built on the future-implementation plan. **Faculty screens are not redesigned.**

This folder is everything Claude Code needs to implement it in `darshani8/reep-` (branch `main`).

## How to use it

1. Copy this folder into the repo as `docs/redesign-2026-09/` and commit it (`docs: add redesign implementation kit`).
2. Open Claude Code at the repo root (`claude`), and paste **`00-MASTER-PROMPT.md`** as the first message. It tells Claude Code what is approved, the rules it must keep, where the designs are, and how to work.
3. Then run the phases from **`06-phase-prompts.md`** one at a time — each prompt is self-contained and ends with a definition of done. Do not start a phase before the previous one is merged and deployed.
4. Set up the MCP servers from **`08-tooling-mcp.md`** (Context7, Postgres, FastAPI, Sentry) — Claude Code uses them to read docs, verify migrations, call the dev API and trace releases.
5. Use **`07-acceptance-checklist.md`** to accept each phase (tick boxes per screen / feature) before deploying.

## What is in the kit

| File | What it is | Who reads it |
|---|---|---|
| `00-MASTER-PROMPT.md` | The opening prompt for Claude Code: scope, rules, sources of truth, working agreements | Claude Code (first message) |
| `01-design-system.md` | Tokens, typography, components, AG Grid + ECharts theme mapping, colour rules — the one design language for admin + student | Claude Code (Phase 0) |
| `02-admin-console-spec.md` | The 30 admin boards, screen by screen: route, sidebar, elements, APIs that exist vs. must be added, backlog items covered | Claude Code (Phases 2–4) |
| `03-student-portal-spec.md` | The student boards, screen by screen: what is a restyle (same content as main) and what is plan-driven (mock interview) | Claude Code (Phases 1, 4) |
| `04-backend-changes.md` | The future-implementation backlog as concrete engineering tasks: migrations, endpoints, choke points, backfills, tests | Claude Code (Phases 3–4) |
| `05-delivery-workflow.md` | Phases and their order, branch/PR/CI/deploy flow, migration + backfill rules, feature flags, rollback, test data | You + Claude Code |
| `06-phase-prompts.md` | Ready-to-paste prompts, one per phase | You (paste into Claude Code) |
| `07-acceptance-checklist.md` | What "done" looks like per screen and per backlog item, plus the compatibility guardrails | You (review) |
| `08-tooling-mcp.md` | The MCP servers Claude Code works with (Context7, Postgres, FastAPI, Sentry), the `.mcp.json`, the dev-only API mount, and the traceability rules | Claude Code (Phase 0, every phase) |
| `09-coding-standards.md` | Simple, readable code named after the stakeholders (file → class → method → variable), the shortcuts that are banned, structure rules, and the PR review checklist | Claude Code (every phase) |
| `design/admin/*.html, *.png` | The 30 approved admin boards as standalone HTML (open in a browser at 1440 px) + screenshots | Claude Code (pixel reference) |
| `design/student/*.html, *.png` | The 29 approved student boards + screenshots | Claude Code (pixel reference) |
| `design/tokens.css`, `design/echarts-theme.json`, `design/ag-grid-theme.ts` | Drop-in theme files generated from the design tokens | Claude Code (Phase 0) |
| `design/index.json` | Board list with titles | tooling |

The live, editable canvases stay in claude.ai (Artifacts): **REEP Admin Console** and **REEP Student Console**. The HTML files here are exports of the approved versions; if a board is edited on the canvas later, re-export it.

## The order of work (summary)

```mermaid
flowchart LR
  P0[Phase 0<br/>Design system + shell] --> P1[Phase 1<br/>Student restyles<br/>(no backend change)]
  P0 --> P2[Phase 2<br/>Admin console on<br/>existing APIs]
  P2 --> P3[Phase 3<br/>Backend choke points<br/>scoping · functions · lifecycle]
  P3 --> P4[Phase 4<br/>Plan-driven features<br/>promotion · imports · interviews · leave · SWOC]
  P1 --> P4
  P4 --> P5[Phase 5<br/>Cleanup · docs · hardening]
```

Each phase ships on its own branch, through its own PR, with CI green, and is deployed with the manual **Deploy** workflow before the next phase starts. Details in `05-delivery-workflow.md`.

## Code style in one line

Simple over clever, named after the people who use it: `promote_batch_to_next_semester()`, not `proc()`. The full rules and a PR checklist are in `09-coding-standards.md`.

## Three rules that never move

1. **Rule 1** — student data never leaves the machine unbidden (`carries_student_data=` on every model call; CI job `pii-gate`).
2. **Rule 2** — staff scope is decided by role + grant, never by a missing field; a mentor with no group sees nobody.
3. **Admin discretion** — who may do what is an admin grant, never hard-wired to a role; UI copy never says "only your mentor can…". Students have **no** control over interview recording/transcript policy — they only start and end an interview.
