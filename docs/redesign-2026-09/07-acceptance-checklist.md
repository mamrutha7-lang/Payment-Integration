# 07 · Acceptance checklist

Tick per phase before deploying. "Matches board" means: same elements in the same places at 1440 px, the design-system components, no overflow or truncated text, populated / empty / loading / error states present, keyboard reachable, status = dot + label.

## 1. Phase 0 — design system + shell
- [ ] Tokens merged; `--brand-magenta` appears only in the gradient (CI grep test)
- [ ] Plus Jakarta Sans + Inter self-hosted; no Orbitron / Chakra Petch in the shell, login, register, resume builder
- [ ] App bar per role (student: avatar + name/USN + Sign out; admin: search, scope control, bell, help, avatar; faculty: unchanged items)
- [ ] Sidebar groups per `01` §3; active pill gradient; student items identical to main
- [ ] Global components render per `01` §4 (card, KPI, chip+dot, five button kinds, select, field/synced, note, steps, tabs, rail, meter, avatar, table dress, dark stage)
- [ ] AG Grid + ECharts themes registered, only in lazy chunks; initial bundle ≤ 400 kB
- [ ] Icon subset regenerated; no missing glyphs anywhere
- [ ] Every existing screen still works for all four roles

## 2. Phase 1 — student restyles (same content as main)
| Screen | Board | Checks |
|---|---|---|
| Sign in | LoginRedesign / LoginStates / LoginMobile | portal tiles are labels only · Google + password doors per `sso/status` · code step · forgot inline · `?error=` and `signedOut=elsewhere` states · 390 px layout |
| Skilling | SkillingRedesign | claim card byte-identical to main · claims-in-progress table · 48 badges by category as emblems, 6 per row · earned = solid + seal · legend/footer |
| Time Sheet | TimeSheetRedesign | stepper (next disabled at today) · Submit day disabled until 24 h with the reason tooltip · four metrics with warn tones · slot × activity grid with status chips · day-total row · Save draft disabled until dirty · footnote · skilling strip |
| Leaderboards | LeaderboardsRedesign | four pill tabs · own-rank card · explainer note · ranking grid with own row highlighted · opted-out and empty states |
| Faculty / TPO Log | MentorLogRequest | SWOC tiles with coloured edge · Request a meeting (secondary, disabled while open) · form with main's placeholders, Send disabled until typed · meeting history rows · empty state |
| Resume Builder | ResumeRedesign + 15 sections | flow steps · goal strip · rail with 16 sections in 5 groups · each section's fields = `ResumeBuilderService` map · synced fields locked · footer save state · Tailor/Preview/Export untouched |
| REEP Agent | AgentRedesign | thread with action rows, source chips, limitations, feedback row · starters · composer Send/Stop · hint · empty state |
- [ ] No new sections, KPIs, filters or features on any restyled screen
- [ ] Access copy uses "staff your placement office has given access"
- [ ] No API change in the PR

## 3. Phase 2 — admin console (existing APIs)
- [ ] All 24 screens + 3 dialogs match their boards on seed data; plan-driven controls disabled with "Available with Phase 3/4"
- [ ] AG Grid on the listed grids: quick filter, floating filters, selection + bulk toolbar, pinned column, sort/column menu, side panel, status bar pagination
- [ ] Analytics: one composite chart with legend toggles, emphasis, three axes, crosshair tooltip, dataZoom; mentor load as a paginated grid; the sunburst sample is gone
- [ ] New routes guarded by the right capability; old screens still reachable behind `ui.console_v2`
- [ ] Faculty screens unchanged except the shell

## 4. Phases 3–4 — backlog coverage (from the Coverage board)
| Area | Item | Task | Accept when |
|---|---|---|---|
| Multi-college | college = tenant; scoped admin.* grants; college admin | B1.2 B1.3 | a department-scoped grant sees only its department in every scoped list |
| Multi-college | registration domains on the college | B1.1 | BGSCET keeps the env domains; a second college refuses off-domain |
| Multi-college | mentor in the student's college | B1.5 | cross-college assign → 422; old pairs flagged |
| Multi-college | scoped lists/queues/exports/analytics; per-college catalogues | B1.4 B13 | responses carry `scope`; exports limited to scope |
| Faculty | college → department before a login exists | B3.1 | POST without department_id → 422 |
| Faculty | FACULTY identity; mentor/HOD/placement as functions | B2.3 | no group → no mentor.* ; group → four grants; baseline shrunk after backfill |
| Faculty | mail in production | B3.7 | `platform/status` says `ses`; invite email arrives |
| Faculty | offboarding | B3.3 | disabled → 401 on every door; mentees released with history; reversible 90 d |
| Faculty | activation link hardening; editable identity | B3.4 B3.5 | active account → 410; re-mint audited; name/email PATCH |
| Course & semester | level/duration/semesters; promote; graduate | B4.1–B4.4 | promotion adds history rows only; graduation keeps login + USN |
| Interview bank | tracks per course/specialization; one catalogue | B5.1–B5.4 | student default track from batch; old codes still open sessions |
| Governance | enforce or delete every key | B2.1 | guard test green; 0 unenforced keys |
| Governance | scope, expiry, review, second approval | B1.2 B2.4 | PII grant pending until approved; review queue lists expiring |
| Governance | grants follow role; deny via functions | B2.5 B2.3 | demoted account loses grants |
| Governance | delegable governance | B2.6 | deputy can open Governance; second ADMIN still refused |
| SWOC | scoped; ownership; history; ack; linked | B7 | student sees author/date; non-author PATCH → 403 |
| Interview records | policy enforced; summaries survive | B6.1 B6.2 | transcript skipped when policy says so; summary rows outlive purge |
| Interview records | filters/pagination/trend; cap counts completed | B6.4 B6.7 | abandoned sessions do not count; reset audited |
| Analytics | ingestion; criteria CRUD; alerts | B8.1–B8.3 | preview → apply writes attendance/marks; criteria per course; alerts real or deleted |
| Analytics | series, KPIs, scope | B8.5 B8.6 | nightly snapshot; KPIs with deltas |
| Faculty & students | audited assignment; history; handover; capacity | B9.1 B9.2 | reason required; previous mentor reads for 90 d |
| Leave | chain by function; Main Admin either step | B10.1 | faculty leave reaches APPROVED |
| Leave | balances, calendar, attachments, cancel, alternate acceptance | B10.2–B10.6 | overlap → 422; cancel works; alternate accepts |
| Registrations | scoped queue; checks; hold; rules | B11 | domain check from the college; HOLD state; rules editable |
| Jobs & placement | scope, close, funnel | B12 | applied posting closes, never deletes |
| Exports | scoped, audited, interviews summary | B14 | download history rows |
| Account | Google link, sessions, prefs | B15 | sign-out-everywhere bumps token_version |

Not in this design (stay in the backlog): mentor-side SWOC screen, student erasure request, co-mentor, student leave screen.

## 5. Compatibility guardrails (must hold after every phase)
- [ ] Existing faculty sign in and land on `/mentor/notebook`; `users.role` unchanged
- [ ] Faculty with mentees keep Mentee Log / Verifications / Leave queue; faculty without see Notebook, Leave (own), Signature, Agent
- [ ] Every pre-existing grant still opens its screen (scope = BGSCET)
- [ ] Student routes and APIs unchanged by scoping
- [ ] Records after promotion keep their semester numbers; graduation keeps login and USN
- [ ] Registration auto-approval behaves as before on day one
- [ ] FIRST_APPROVED leave rows are decidable by the Main Admin immediately
- [ ] Old interview track codes still open sessions; existing consent rows still valid until the version bumps
- [ ] Daily cap value 8; counted attempts never lost mid-day
- [ ] Interview summaries backfilled before the first purge after deploy
- [ ] Disabled faculty only: active accounts untouched
- [ ] Activation links already sent stay valid to expiry
- [ ] SWOC entries show their author without re-entry
- [ ] Old leave PDFs untouched
- [ ] Criteria defaults remain the fallback; screens say "no import yet" instead of zeros

## 6. Non-negotiables on every PR
- [ ] Rule 1 CI job green; Rule 2 scope helper used, never re-implemented
- [ ] No copy says "only your mentor can…"; students have no recording/transcript switches
- [ ] One primary action per view; status dot + label; nullable scores as dash
- [ ] Migrations expand-first with backfill and `downgrade()`; purge verdicts updated
- [ ] `AGENTS.md` updated where behaviour changed
- [ ] Code follows `09-coding-standards.md`: stakeholder names at every level, no banned shortcuts, named constants, small functions, readable errors, tests as sentences — the review checklist from `09` §5 is in the PR
