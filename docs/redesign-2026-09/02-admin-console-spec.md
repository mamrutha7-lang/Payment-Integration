# 02 · Admin Console — screen by screen

30 approved boards in `design/admin/`. For each screen: the board, the route (existing routes are kept; new ones are proposed), what is on the board, the APIs that exist on `main` today, and the backend tasks (`04-backend-changes.md`, ids `B…`) the screen needs before its plan-driven parts can be built. Anything marked **existing API** can be built in Phase 2 with no backend change; anything with a `B…` id waits for Phase 3/4.

The whole console sits in the shared shell (`layout/app-shell.component.*`): new app bar with the **scope control** (College / Department, driven by the caller's grants — `B1.2`, `B1.4`), the admin sidebar groups exactly as on the boards (`01-design-system.md` §3: OVERVIEW · INSTITUTION · OPERATIONS · STUDENT INSIGHT · GOVERNANCE · TOOLS), the floating agent orb unchanged. Guards stay as today: `capabilityGuard('admin.<key>')` per route, `roleGuard('ADMIN')` for Governance until `B2.6` lands.

## 1. Sign in — Main Admin door (`SignIn.html`)
- Route `/login` (public, outside the shell). Same component as the student sign-in (`design/student/LoginRedesign.html`): portal tiles are labels only, Google on the college domain, REEP password form when `GET /api/auth/sso/status` says the door is open, the dashed **Main Admin** door adds the 6-digit emailed code step (`OTP_REQUIRED`), "signed out elsewhere" notice, forgot-password inline.
- **Existing API:** `/api/auth/login`, `/login/code`, `/sso/google`, `/sso/status`, `/forgot`. No change.

## 2. Analytics (`Main.html`) — route `/admin`, cap `admin.analytics`
- KPI tiles with sparkline + delta: Placement rate, Median CTC, Placement-ready, Attendance, Mock interviews, Pending approvals. **One composite chart** "Placement health · weekly" (readiness % and attendance % on the left axis, skilling hours bars on the right axis, offers count on a second right axis; legend toggles; emphasis dims the others; crosshair tooltip; dataZoom). Track bars in the categorical palette. Placement donut + funnel. "Mentor load · 23 mentors" as an AG Grid (sorted, paginated, quick filter) replacing the 5-mentor sunburst sample. "Alerts · nightly 02:00" list with **Rules**. Actions: Export, Import data, Open mentor mapping.
- **Existing API:** `GET /api/admin/analytics-summary`, `/mentor-load`, `/criteria`, `/students/{id}/weekly`, `/alert-rules`. **Needs:** `B8.5` scoped analytics series + KPIs, `B8.6` nightly snapshots, `B8.3` alert engine, `B8.1` imports link, `B1.4` scope.

## 3. Colleges (`Colleges.html`) — route `/admin/colleges` (new), cap `admin.institution` (platform card: Main Admin only)
- Tenant list: code, name, registered email domains, departments / students / faculty counts, college admin, status. "Add college" side panel: name, code, email domains (chips), first college admin (existing faculty or invite), "copy catalogues from" (badges / certifications / interview tracks). Card "What a college admin can do inside BGSCET" = the explicit list of scoped functions (admin.registrations, admin.students, admin.mentors, admin.analytics, admin.exports… for that college; not governance, not platform). "Platform · Main Admin only": mail transport status (SES configured or console), harden phase ("Deploy harden phase" is a link to the runbook, not a button that deploys).
- **Existing API:** `GET/POST/PATCH /api/admin/colleges`. **Needs:** `B1.1` domains on colleges, `B1.3` college-admin functions, `B3.7` mail status endpoint (`GET /api/admin/platform/status`).

## 4. Institution structure (`Institution.html`) — `/admin/institution`, cap `admin.institution`
- College card (domains with "Add domain"), department tree, **Course** card with degree level (UG/PG), duration (years), total semesters, "batches inherit"; **Specializations ↔ interview track** (colour-coded to the categorical palette); Batches grid (code, label, course, specialization, semester x of N, students, status) with Add batch / Archive; Save changes.
- **Existing API:** colleges / departments / academic courses / specializations / cohorts CRUD, `GET /hierarchy/levels`, `GET /cohorts/incomplete`. **Needs:** `B4.1` course level/duration/semesters, `B5.1` track mapping on specializations, `B1.1` domains.

## 5. Students & batches (`Students.html`) — `/admin/students`, cap `admin.students`
- Batch selector + summary line ("58 students · semester 3 of 4 · 55 seated with a mentor"). AG Grid: quick filter, floating filter row, checkbox selection with bulk actions in the toolbar (Assign mentor to N selected, Bulk assign mentor, Move batch), pinned USN column, sort + column menu, side panel, status bar with pagination. Batch actions: **Promote to semester 4**, **Graduate batch** (open the two dialogs). "Promotion history" card.
- **Existing API:** `GET /api/admin/students?cohort_id&q&unseated`, `PATCH /students/{id}`, `POST /cohorts/{id}/students/bulk`, `POST /students/{id}/mentor`. **Needs:** `B4.3` promote, `B4.4` graduate, `B4.2` semester validation, `B9.3` bulk assign, `B1.4` scope.

## 6. Student 360 (`StudentDetail.html`) — `/admin/students/:id` (new), cap `admin.students`
- Header: name, USN, batch, specialization, semester x of N, mentor, status chips. Cards: **Semester timeline** ("nothing is rewritten on promotion" — per-semester rows with results, ledger compliance, interviews, badges), **Identity & login** (Google link, last sign-in, one-device session, "Sign out everywhere", "Re-send invite"; no admin-set passwords; **Reset daily cap** for interviews), **Mentor** (current + history with handover, Reassign), **Readiness · this semester** (now includes interviews and SWOC), **Open items**, **Recent activity**. Actions: Edit profile, Move batch, Hold back, Audit trail.
- **Existing API:** parts of `/api/admin/students`, `/api/mentor/students/{id}/*` reads (ledger, interviews, badges) via ADMIN scope. **Needs:** `B4.5` composite read, `B9.1` mentor history, `B6.4` cap reset, `B3.6` sign-out-everywhere for a user, `B6.3` readiness inputs.

## 7. Faculty (`Faculty.html`) — `/admin/faculty` (new; today part of `/admin/mentors`), cap `admin.mentors`
- AG Grid directory: name, email, department, **status** (Active / Invited / Disabled), **functions** chips (mentor · HOD · placement officer · verifier…), mentees, last sign-in. Drawer: editable name/email, department, functions with **Grant function**, "Sign out everywhere", **Disable** (opens the Disable faculty dialog), re-send activation. "Review 3 expiring grants" links to the Governance review queue.
- **Existing API:** `GET /api/admin/faculty`, `PATCH /faculty/{id}` (designation/department only), `POST /users/{id}/activation-link`. **Needs:** `B3.5` PATCH name/email, `B3.3` disable, `B3.6` sign-out-everywhere, `B2.3` functions, `B2.4` expiry review.

## 8. Add faculty — wizard (`AddFaculty.html`) — `/admin/faculty/new` (new)
- Steps: 1 Institution (College → Department, both required — cascade from `GET /api/admin/colleges` → `/colleges/{id}/departments`), 2 Identity (name, college email with the domain fence, designation), 3 Functions (explicit grants; mentor is auto-granted on first assignment), 4 Invite (email the activation link; the link is shown once only as a fallback when mail is off). "What happens next" card.
- **Existing API:** `POST /api/admin/faculty`. **Needs:** `B3.1` department required (422 without), `B3.2` domain policy, `B3.4` link hardening, `B3.7` mail.

## 9. Mentor mapping (`MentorMapping.html`) — `/admin/mentors`, cap `admin.mentors`
- Left: mentors of the department with load vs capacity (from Governance setting). Right: unassigned students **of the same department**. Assign N selected to a mentor (reason required → audited; both notified). "Recent changes for …" and **Assignment history** with handover window. Bulk assign batch.
- **Existing API:** `GET /api/admin/mentor-load`, `/unassigned-students`, `POST /students/{id}/mentor`. **Needs:** `B1.5` same-college check, `B9.1` history/handover, `B9.2` validation + audit + notifications, `B9.3` bulk, `B9.4` filters.

## 10. Registrations (`Registrations.html`) — `/admin/registrations`, cap `admin.registrations`
- Queue scoped by college; row detail panel with documents (CV, photo), **Checks** (domain matches the college's registered domains, USN pattern, rule matched, duplicate account), Approve & invite (provisions + emails the onboarding link), **Hold** (note), Reject (note required), Reopen within 24 h, Export, **Seating rules** (editable registration rules).
- **Existing API:** `GET /api/register/pending`, `POST /{id}/decision`, `/{id}/reopen`, `GET /rules` (read-only). **Needs:** `B11.1` college scope + domain check, `B11.2` HOLD status, `B11.3` rules CRUD, `B11.4` copy fix ("approval provisions nothing" is wrong today).

## 11. Data imports & criteria (`DataImports.html`) — `/admin/imports` (new), cap `admin.institution` (or new `admin.imports`)
- New import wizard: kind (attendance / marks), batch, semester, file (CSV/XLSX, templates downloadable); **Preview** as an AG Grid with per-row validation (ok / warning / error), **Error report**, "Import 52 rows"; Recent imports history. **Placement criteria · course**: min CGPA, max live backlogs, min attendance %, min certification %, max gap months — Save with history.
- **Existing API:** none for imports; `GET /api/admin/criteria` read-only. **Needs:** `B8.1` imports, `B8.2` criteria CRUD.

## 12. Catalogue (`Catalogue.html`) — `/admin/catalogue`, cap `admin.catalogue`
- Tabs: Subjects (Import subjects), Certifications ↔ badges (Add badge, Retire badge), Stage rules, Interview tracks — all **scoped to a college and a course** with "Copy to course…" and a "Used by" card.
- **Existing API:** `GET /api/admin/catalogue`, `/badge-catalogue`, approved certifications CRUD. **Needs:** `B13` scoped catalogue + copy-to-course; badge catalogue stays code-defined (only its course mapping is data).

## 13. Leave approvals (`Leave.html`) — `/admin/leave-approvals`, `roleGuard('ADMIN')` today → cap `mentor.leave_approve` once enforced
- Queue with the **approval chain** (mentor/HOD signs first, principal or Main Admin sanctions), reason visible to approvers only, attachments, alternate arrangement acceptance, Sanction / Return / Reject / PDF, Calendar, **Leave policy** card per department (balances per kind, academic year).
- **Existing API:** `/api/leaves/pending|history`, `POST /{id}/decision`, `GET /{id}/paper.pdf`. **Needs:** `B10.1`–`B10.8`.

## 14. Jobs sheet (`JobsSheet.html`) — `/admin/jobs`, cap `admin.jobs`
- AG Grid of postings (scope college/course, eligibility from the course's criteria, tracks decide visibility, applications count); Post a job panel (Publish), Duplicate, Import postings, **Close posting** (never delete once applied), Placement funnel link.
- **Existing API:** `GET|POST /api/admin/jobs`, `DELETE /jobs/{id}` (409 with applications). **Needs:** `B12.1` scope/tracks, `B12.2` close state.

## 15. Placement & offers (`Placement.html`) — `/admin/placement`, cap `admin.placement`
- One funnel chart (distinct students: eligible → applied → interviewed → offered → placed), "This year" KPIs (Placement rate, Median CTC, Highest, Multiple offers), By track split, Offers grid with Approve offer / Reject (existing decision endpoint), Export offers.
- **Existing API:** `GET /api/admin/placement`, `GET /api/mentor/offers/pending`, `POST /api/mentor/offers/{id}/decision`. **Needs:** `B12.3` funnel/KPIs by batch and track.

## 16. Interview question bank (`InterviewBank.html`) — `/admin/interview-questions`, cap `admin.interview_questions`
- Tracks list (admin-managed, per course/specialization; Add track: code, label, persona, frameworks, voice, mapped specialization), Questions grid per track/phase with **asked count and avg score** from linked turns, Add question, Bulk paste, Bulk import, reorder, enable.
- **Existing API:** `/api/admin/interview-questions` (`/tracks`, list, POST, `/bulk`, PATCH, DELETE, `/reorder`). **Needs:** `B5.1`–`B5.5`.

## 17. Interview records (`InterviewRecords.html`) — `/admin/interviews`, cap `admin.interview_audio` for audio, new `admin.interviews` for the grid
- KPI tiles (Sessions, Average score, Completion, Daily-cap resets), AG Grid with filters (college, batch, track, status, date) + pagination + side panel; row panel: report, transcript, consent/policy scopes shown as enforced, audio download when it exists. "Priya S · progress" ECharts line across semesters (score summaries survive retention). Retention card (Policy). Export CSV (summary only).
- **Existing API:** `GET /api/mentor/interviews?recorded_only=`, per-student reads, audio + zip. **Needs:** `B6.2`, `B6.4`, `B6.7`, `B6.1`.

## 18. SWOC notes (`Swoc.html`) — `/admin/swoc`, cap `admin.swoc`
- Mentees of the mentor group / department (scoped), four quadrants per student with **author + date + source** on every entry, ownership (edit/delete own unless admin), edit history, **Semester view**, student acknowledgement state, links to skills / interviews / jobs, Export.
- **Existing API:** `/api/admin/swoc` GET/POST/PATCH/DELETE. **Needs:** `B7`.

## 19. Roles & functions (`Governance.html`) — `/admin/governance`, `roleGuard('ADMIN')` → cap `admin.governance` (`B2.6`)
- Left: staff list with role (identity) and functions. Right: **Grant function** panel — function, scope target (college / department / course / batch), reason (≥ 20 chars), expiry, "Send for approval" when the function carries PII; Review queue (expiring / awaiting second approval); Extend / Revoke. Every function listed shows whether it is server-enforced.
- **Existing API:** `/api/admin/governance/*`. **Needs:** `B2.1`–`B2.6`, `B1.2`.

## 20. Student feature switches (`FeatureSwitches.html`) — `/admin/governance/features` (new route for the existing panel)
- The 10 `student.*` keys with default, override scope, value and an **Enforcement** column (server-enforced / not wired yet · hidden from students); override panel with scope target, reason, expiry and the student-facing message.
- **Existing API:** `GET|PUT|DELETE /api/admin/governance/features`. **Needs:** `B2.2`.

## 21. Audit log (`AuditLog.html`) — `/admin/audit` (new), cap `admin.governance` (or `admin.audit`)
- AG Grid of every write: time, actor, action, target, scope check, session; detail panel with **before → after** diff, remark, related events; filters (actor, action, target type, date) + pagination; Export range; Copy event JSON.
- **Existing:** `redesign_audit_events` rows are written by `architecture_events.record_change`; no read API. **Needs:** `B2.7`.

## 22. Exports (`Exports.html`) — `/admin/exports`, cap `admin.exports`
- Cards per extract (Students, Placement, Ledger, **Interviews (summary only)**, Registrations, Leave) with the filters applied, PII flag (needs a PII-carrying function), Download CSV; **Download history** (audited with filters); "Schedule an export" (optional, later).
- **Existing API:** `/api/admin/exports/students.csv|placement.csv|ledger.csv`, `/api/admin/badges/export.csv`. **Needs:** `B14`.

## 23. REEP Agent (`Agent.html`) — `/admin/agent`
- The existing ask / actions / sources / limitations / feedback screen restyled; "What the agent can see" card (scope + Rule 1 status: student data never leaves ap-south-1); answers link to the screen that acts.
- **Existing API:** `/api/agent/*`. **Needs:** `B16` (scope text from capabilities; no new endpoints).

## 24. My account (`Account.html`) — `/account` (new; today only `/account/password`)
- Sign-in & security (Google link / unlink, Change password = code + new password), Sessions (one-device rule, Sign out everywhere), Signature (existing PUT/DELETE), Email notifications (digests), Recent sign-ins.
- **Existing API:** `/api/auth/change-password*`, `/api/staff/signature`. **Needs:** `B15`.

## 25–27. Dialogs
- **Promote batch** (`PromoteBatch.html`): "58 students · semester 3 → 4 · effective today"; checks: course total semesters, imported results for the semester, stage rule; per-student exceptions (hold back); nothing deleted; history row per student → `B4.3`.
- **Graduate batch** (`GraduateBatch.html`): final semester + end-date checks, alumni profile linked to the student record, role STUDENT → ALUMNI, logins and history untouched, reversible 30 days → `B4.4`.
- **Disable faculty** (`OffboardFaculty.html`): sign-in stops, mentees released (with handover), functions revoked, history kept, reversible 90 days → `B3.3`.

## 28–30. Reference boards
- **Design system** (`DesignSystem.html`) — see `01-design-system.md`.
- **Coverage** (`Coverage.html`) — every backlog item traced to a screen (Covered / Partial / Backend-only / Not in this design); reproduced in `07-acceptance-checklist.md` §3.
- **Compatibility** (`Compatibility.html`) — per flow: what main does today, what changes, the guardrail; reproduced in `07-acceptance-checklist.md` §4 — these guardrails are acceptance criteria.

## Faculty screens (no redesign)
`features/mentor/*` (Notebook, Mentee Log, Verifications, Leave, Upskilling, Signature, Agent) keep their templates. They inherit the new shell and tokens automatically (same colours). Only these behaviours change under them, from the backend tasks: functions instead of the MENTOR baseline (`B2.3` — links hide via `session.capabilities`), leave approval chain (`B10.1`), mentor history/handover reads (`B9.1`), SWOC author/date (`B7`). Do not add screens to the faculty console in this release.
