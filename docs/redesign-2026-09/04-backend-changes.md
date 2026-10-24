# 04 · Backend changes — the future-implementation plan as engineering tasks

Verified against `main @ d471c69`. Every task: what exists, what changes, the migration + backfill, the endpoints, the guardrail from the Compatibility board, and the tests. Ids (`B1.1` …) are referenced from `02`, `03`, `05`, `06`, `07`. Paths are `apps/api-py/app/…` unless stated.

Conventions for every task: Alembic migration with `downgrade()`; backfill in the same migration (or a one-off `python -m app.<backfill>` refused in prod without `--apply`); audit every mutation with `architecture_events.record_change`; `require_capability` for capability-gated endpoints **and** a role gate; scope checks through one helper (`B1.2`), never re-implemented; tests in `apps/api-py/tests/test_<area>.py` plus a line in `tests/test_codebase_guards.py` when a rule must not regress.

---

## B1 · Multi-college scoping (the college is the tenant)

**B1.1 Registration domains on the college.** `colleges.email_domains text[]` (new, default `{}`); migration seeds BGSCET with the two env domains (`GOOGLE_ALLOWED_DOMAIN`, `ROSTER_EMAIL_DOMAIN`) so auto-approval behaves the same on day one. `Settings.provisionable_email_domains` becomes a fallback only; `registration._provision_student` GUARD 1, `admin_students` email fence and `admin_faculty` (B3.2) read the college's list (resolved from the claim's college / the account's department → college). Endpoints: `PATCH /api/admin/colleges/{id}` accepts `email_domains`. Tests: provisioning refused off-domain per college; env fallback when a college has none.

**B1.2 Scope targets on grants.** `capability_grants.scope_type text` (`programme|college|department|course|cohort`) + `scope_id` (nullable for programme); migration backfills **every existing grant with scope = BGSCET (college)** so nobody loses access. `governance.require_capability(db, session, key, *, target=None)` gains a scope check: the caller's effective grants for `key` must cover the target's ancestry (student → cohort → course/department → college; faculty → department → college). Add `policies.scope_filter(db, session, key)` returning the SQLAlchemy filter for lists (colleges/departments the caller may see). ADMIN role still sees everything. Tests: a department-scoped grant sees only its department's students in every scoped endpoint (parametrised over B1.4's list).

**B1.3 College admin as functions, not a role.** No second ADMIN. A FACULTY account holding scoped `admin.*` grants (registrations, students, mentors, analytics, exports, institution-for-own-college, jobs, placement, swoc, interview questions, catalogue, **imports**) is the "college admin". *(Corrected in Phase 4b. The set in code is `COLLEGE_ADMIN_CAPABILITIES` in `app/routers/admin.py` and it is the authority. Two entries here were wrong: "interviews" named `admin.interviews`, which did not exist and still does not — 4c's B6.7 adds it with the records grid, and the granted interview key meanwhile is `admin.interview_questions`; and B8.1's `admin.imports` was missing, which the owner has since decided a college admin holds. Six of the twelve `carries_pii`, so a freshly appointed college admin is normally PART live until a second `admin.governance` holder approves the rest — `approval_state` is reported per key for exactly that reason.)* Add `GET /api/admin/colleges/{id}/admins` and `POST …/admins {user_id}` (creates the scoped grant set with one reason; audited). Governance (`admin.governance`) and platform cards stay Main Admin only. Guard test: `_refuse_second_main_admin` unchanged.

**B1.4 Scoped lists, queues, exports, analytics.** Apply `scope_filter` to: `registration.pending`, `console.mentor_load`, `console.unassigned_students`, `admin_students.list`, `console.exports_*`, `console.analytics_summary` + B8.5 series, `leave.pending/history`, `swoc` GET, `interview_records` staff grid, `admin_faculty` GET, jobs/placement. Every response carries `scope: {college, department}` so the app bar's scope control can show it; the client sends `?college_id=&department_id=` only to narrow **within** its scope (server re-validates). Tests per endpoint.

**B1.5 Mentor must be in the student's college.** `console.assign_mentor`: refuse (422) when the mentor's `users.department_id` college ≠ student's college; same-department is the default filter for the picker (`GET /unassigned-students?department_id=`). Existing cross-department pairs are kept and flagged (`cross_department: true` in mentor-load) — never broken.

---

## B2 · Governance — role is identity, function is a scoped grant

**B2.1 Enforce or delete every catalogue key.** For each of the 16 unenforced keys: either add `require_capability` at the endpoint (`mentor.notebook` in `redesign.py`, `mentor.leave_approve` in `leave.py` decision + pending, `mentor.agent` in `agent.py` for staff) or remove the key from `CAPABILITIES` (the 10 `student.*` keys move to B2.2 features). Guard test: every key in the catalogue appears in a `require_capability(` call (AST scan like `check_pii_gate.py`).

**B2.2 Feature switches become real.** `governance.features_for(student)` is called in the student routers that the 10 features gate (jobs, leaderboards, mock interview, resume generate, agent, uploads, english, skilling, time-log, mentor-log) → 403 `feature_disabled` with the override's `student_message`; `GET /api/auth/me` returns `features` for STUDENT sessions; the client hides/greys the nav item and shows the message. `feature_overrides` gains `reason`, `expires_at`, `student_message`. Catalogue entry gains `enforced: bool`; a feature that is not wired is reported `enforced=false` and **cannot be turned off** (422) — the switches screen shows "Not wired yet · hidden".

**B2.3 Functions instead of the MENTOR baseline.** `ROLE_BASELINE["MENTOR"]` shrinks to `{mentor.agent, mentor.upskilling}` (own instruments). `console.ensure_mentor_group` grants `mentor.mentees`, `mentor.notebook`, `mentor.verifications`, `mentor.leave_approve` (scope = the mentor's group/department) with reason "mentor group created"; releasing the last mentee revokes them. **Backfill before the baseline shrinks:** one grant per existing `mentors` row. Client: staff links already hide via `session.capabilities`. Guard test: a faculty account with no group has none of the four; with a group has all four. Label: the DB role value stays `MENTOR` (UI says Faculty) — no migration of `users.role`.

**B2.4 Expiry, review, second approval.** `capability_grants.review_at` (default expiry + 180 d), `approval_state` (`active|pending_approval`), `approved_by`; capabilities flagged `carries_pii` require a second Main-Admin/deputy approval before `active` (`POST /grants/{id}/approve`); `GET /api/admin/governance/review` lists expiring (≤ 30 d) and pending grants; `POST /grants/{id}/extend`. A reminder mail 14 days before expiry (uses B3.7).

**B2.5 Grants follow the role.** `granted_capabilities` joins `users.role` and ignores grants whose `role_at_grant` no longer matches; `grant_access` role change revokes grants and group memberships (audited).

**B2.6 Delegable governance (partial).** New capability `admin.governance` (Main Admin baseline; grantable to one deputy with a reason and second approval); route guard becomes `capabilityGuard('admin.governance')`. Break-glass = the deputy. `_refuse_second_main_admin` stays.

**B2.7 Audit log API.** `GET /api/admin/audit?actor=&action=&target_type=&target_id=&from=&to=&page=&page_size=` over `redesign_audit_events` (scoped by B1.2 for college admins, all for Main Admin), `GET /api/admin/audit/{id}` (before/after), `GET /api/admin/audit/export.csv` (audited itself). Retention: append-only, never purged by `retention.py` (guard test).

---

## B3 · Faculty account lifecycle

**B3.1 Institution-first creation.** `AdminFacultyIn.department_id` required (422 without); `POST /api/admin/faculty` resolves college from the department; `GET /api/admin/colleges/{id}/departments` already exists for the cascade. Existing unfiled accounts are listed by `GET /api/admin/faculty?unfiled=true` for the admin to file.

**B3.2 Faculty email domain policy.** Refuse a faculty email outside the college's `email_domains` unless the request sets `allow_external=true` with a reason (audited). Policy decision recorded in the audit row.

**B3.3 Offboarding.** `users.disabled_at`, `disabled_by`, `disable_reason`; checked in `security.verify_session_token` (fail closed), `auth.login`, `google_auth callback`, `onboarding/activation`; `POST /api/admin/users/{id}/disable {reason}` bumps `token_version`, releases mentees (B9.1 history rows with `reason=faculty_disabled`), revokes grants + group memberships (B2.5), keeps every record; `POST …/enable` within 90 days restores the login only (grants are re-granted explicitly). `purge_people` verdicts updated for the new columns (its test fails otherwise).

**B3.4 Activation link hardening.** `passwords.activate` refuses accounts that already hold a `scrypt:` hash (410 with a "use forgot password" hint); `POST /api/admin/users/{id}/activation-link` is audited (`record_change`), notifies the account's email, TTL becomes 48 h (`ACTIVATION_LINK_HOURS` default 48), and the response marks `shown_once=true` (client shows it once, never lists it again).

**B3.5 Editable identity.** `PATCH /api/admin/faculty/{id}` accepts `name`, `email` (lower-cased, uniqueness check, domain policy, bumps `token_version`, audited with before/after).

**B3.6 Sign out everywhere.** `POST /api/admin/users/{id}/sign-out-everywhere` (Main Admin / college admin within scope) and `POST /api/auth/sign-out-everywhere` (self): `token_version++`, audited.

**B3.7 Mail transport in production (infra).** Either deploy the CDK harden phase (`infra/cdk`, `phase=harden`) or move `SES_FROM_ADDRESS` + the `send-mail` policy into the import phase of `reep_core/stack.py`; verify the sender in SES; `GET /api/admin/platform/status` reports `mail: ses|console`. Until this lands, invite links are shown on screen (B3.4 "shown once") — the Add-faculty wizard and Registrations must not promise an email that cannot be sent.

---

## B4 · Course level, semesters, promotion, graduation

**B4.1 Course carries the shape.** `academic_courses.degree_level` (UG/PG, enum value reused from `DegreeLevel` with `create_type=False`), `duration_years`, `total_semesters`; batches inherit (`cohorts.degree_level` becomes derived; keep the column, backfill from the course, stop writing it). Migration: courses without a level get `total_semesters` from their batches' degree level (UG 8 / PG 4).

**B4.2 Semester validation.** `admin_students.MAX_SEMESTER` removed; `current_semester ≤ course.total_semesters` (422); students in a batch without a course keep the old bound.

**B4.3 Promote batch.** `POST /api/admin/cohorts/{id}/promote {effective_on, hold_back:[student_id], reason}` → for each student: `student_semester_history(student_id, from_semester, to_semester, effective_on, by_user_id, reason, kind=promote)`, `current_semester+1`, optional stage rule (from the catalogue's stage rules, B13), audit `STUDENTS_PROMOTE`. Checks reported before applying (`?dry_run=true`): course total semesters, imported results for the current semester (B8.1), open holds. **Nothing is rewritten**: results, ledger, interviews, badges keep their semester numbers (guard test).

**B4.4 Graduate batch.** `POST /api/admin/cohorts/{id}/graduate {effective_on, reason}`: checks final semester + `cohorts.end_date`; per student: `students.status = GRADUATED` (new column), `users.role = ALUMNI`, `alumni_profiles` created with `student_id` (new FK) if missing, `token_version++`, history row `kind=graduate`; batch status `GRADUATED`. Reversible for 30 days (`POST …/ungraduate`). Client: `homeRedirectGuard` sends ALUMNI to `/alumni`; the alumni profile shows the linked student history read-only (`GET /api/alumni/history`).

**B4.5 Student 360 read.** `GET /api/admin/students/{id}/360`: identity & login (google linked, last sign-in, token_version, disabled), semester timeline (per semester: results, ledger reconciled days, interviews + best score, badges earned), mentor history (B9.1), readiness (B6.3), open items (pending uploads/claims, unsubmitted ledger days, missing profile fields), recent audit events. Scoped by B1.2.

---

## B5 · Interview tracks and question bank

**B5.1 Admin-managed tracks.** New table `interview_tracks(id, code, label, persona, frameworks text[], sample_question, nova_voice, college_id, course_id, specialization_id nullable, enabled, position)`; migration seeds the four MBA rows from `interview_matrix.SPECIALIZATIONS` (codes hr/dm/ba/fa unchanged so `?specialization=` keeps working) mapped to BGSCET's specializations. `interview_matrix.specialization_for(code, db)` reads the table (code fallback to the constant while the table is empty). CRUD under `/api/admin/interview-questions/tracks` (cap `admin.interview_questions`, scoped).

**B5.2 Bank questions belong to a track.** `interview_bank_questions.track_id` FK (backfilled from `track` code), `college_id`; `with_question_bank` filters by track row; capability scoped by B1.2.

**B5.3 Default track from the batch.** `GET /api/interview/policy` (B6.1) returns `default_track` = the track mapped to the student's `cohort.specialization_id` (else `general`), plus the enabled tracks list for "Practise another track". The client preselects it; the `?specialization=` param stays.

**B5.4 One catalogue.** The voice platform's `platform_specializations/questions` read from `interview_tracks` / bank (compile step in `voice_platform`), and their CRUD becomes an alias or is retired; per-degree time limits and recording policies stay in `platform_time_limits` / `platform_recording_policies` (B6.1 reads them).

**B5.5 Question effectiveness.** `interview_turns.question_id` (nullable FK, B6.6); `GET /api/admin/interview-questions?track=` returns `asked_count` and `avg_score` (join evaluations of sessions where the question was asked).

---

## B6 · Interview records and policy

**B6.1 Policy replaces student consent toggles.** New table `interview_policies(college_id, course_id nullable, store_transcript bool default true, store_audio bool default false, retention_days default 180, daily_cap default 8, time_limit_seconds default 480, updated_by, updated_at)` with `PUT /api/admin/interview-policies/{college}[/{course}]` (cap `admin.interviews`, audited). `GET /api/interview/policy` (student) returns the effective policy, `provider_label`, today's completed count, default/available tracks. **Enforcement:** `_open_records` reads the policy; the client posts `POST /api/interview/consent` at Start with the policy's scopes (an acknowledgement of version `INTERVIEW_CONSENT_VERSION`), never from a form; `interview_sessions.consent_id` still pins the row. Turn writer skips `messages`/`interview_turns` when `store_transcript=false` (the report is still written); `interview_audio.recorder_for` requires `store_audio` **and** `INTERVIEW_RECORDING_ENABLED`. The student `DELETE /consent` route is removed from the UI and answers 405 for STUDENT (policy is the college's). Compat: existing consent rows keep working for the running version; the next version bump asks once.

**B6.2 Retained score summary.** New table `interview_score_summaries(student_id, session_id, track_code, started_at, overall, communication, domain, structure, status)` written at finalization; `retention.purge_expired` deletes transcripts/audio/evaluations on the 180-day clock but **never** the summary; backfill from `interview_evaluations` before the next purge (`python -m app.backfill_interview_summaries`). `GET /api/interview/progress` (student) and `GET /api/mentor/students/{id}/interviews/progress` (staff, scoped).

**B6.3 Evaluations feed readiness / next actions / Home.** `student.placement_readiness` gains an interview component (best overall of the last 90 days, weight 2; "unassessed" when none); `next_actions` adds the report's `drill` as an action while it is unaddressed; `student.mocks` (Home chart) counts `interview_sessions` completed **plus** legacy `mock_attempts` with a `source` field, one series. SWOC linkage: B7.6.

**B6.4 Daily cap counts completed only; admin reset.** `_open_records` counts sessions with `status=completed` in the last 24 h (value stays 8); `POST /api/admin/students/{id}/interview-cap/reset {reason}` writes `interview_cap_resets` (audited) and the count excludes sessions before the reset. Compat: a student mid-day never loses attempts already counted.

**B6.5 Access log.** `interview_record_views(session_id, viewer_user_id, what transcript|report|audio, at)` written by every staff read; `GET /api/interview/sessions/{id}/views` for the student (name, what, date); shown on the admin record panel too.

**B6.6 Question ids on turns.** The engine tags an interviewer turn with the bank question it injected (`interview_turns.question_id`); `reep.transcript.delta` carries `question_id` so the live transcript can show "Q2 · bank BA-07".

**B6.7 Records grid, export, trend.** `GET /api/admin/interviews?college=&cohort=&track=&status=&from=&to=&page=&page_size=` (new cap `admin.interviews`; audio download keeps `admin.interview_audio`), KPI endpoint (`/api/admin/interviews/summary`), `GET /api/admin/interviews/export.csv` (summary rows only; audited), per-student trend from B6.2. The 200-row cap goes away with pagination.

---

## B7 · SWOC

**B7.1 Scope.** `swoc` GET/POST scoped by B1.2 (department / mentor group); `_source_for` stamps MENTOR only when the author mentors the student, else PLACEMENT; `PM` retired from the writer (kept as a legal stored value).
**B7.2 Ownership.** PATCH/DELETE allowed for the author or a Main Admin/college admin (403 otherwise).
**B7.3 History.** `swoc_entries.updated_at`; `swoc_entry_revisions(entry_id, before, after, by, at)` written on PATCH; `GET /api/admin/swoc/{student_id}/history`.
**B7.4 Semester view.** `swoc_entries.semester` (default the student's current semester at write time; backfill from `recorded_at` vs history); `GET …?semester=`.
**B7.5 Student sees author, date, acknowledges.** `SwocItemOut` gains `author_name`, `recorded_at`, `acknowledged_at`; `POST /api/student/swoc/{entry_id}/acknowledge`. Existing entries get their author from `author_user_id` — no re-entry.
**B7.6 Linked.** Optional `linked_skill_id` / `linked_session_id` / `linked_job_id`; readiness and next-actions read weight ≥ 4 weaknesses as actions.
**B7.7 Scale.** Pagination + `?cohort_id=&q=` on the admin list.

---

## B8 · Analytics ingestion, criteria, alerts, series

**B8.1 Imports.** Tables `import_runs(kind attendance|marks, college_id, cohort_id, semester, filename, status previewed|applied|failed, rows, errors, by, at)` and `import_rows` (parsed row + validation result). Endpoints (cap `admin.imports`, new key): `POST /api/admin/imports/preview` (multipart CSV/XLSX → parsed + validated, nothing written), `POST /api/admin/imports/{run_id}/apply` (writes `attendance_records` / `semester_results` + `subject_marks` in one transaction; audited), `GET /api/admin/imports`, `GET /imports/{id}/errors.csv`, `GET /api/admin/imports/templates/{kind}.xlsx`. Validation: USN exists in the batch, semester ≤ course, marks 0–100, attendance 0–100, subject codes in `courses`. openpyxl added to `requirements.txt` (and therefore `check_api_imports`).
**B8.2 Placement criteria per course.** `placement_criteria` gains `college_id`, `course_id`, `effective_from`, `created_by`; `GET/POST /api/admin/criteria?course_id=` with history; readiness/eligibility resolve the student's course row, fallback the programme row, fallback the hard-coded defaults (6.0 · 0 · 75 % · 50 %).
**B8.3 Alert engine or delete.** Nightly job `app.alerts_job` evaluates `alert_rule_configs` (attendance below target, ledger unsubmitted N days, readiness drop, cap resets) into `alerts` (idempotent per rule/student/day); mentor UI reads `GET /api/mentor/alerts` (already exists). If not built in Phase 4, delete `alert_rule_configs`, `alerts` and their endpoints — no decorative rules.
**B8.4 Dead endpoints.** Remove `/admin/overview`, `/admin/mail`, `/admin/job-imports` and `job_import_runs` (verdict in `purge_people` updated).
**B8.5 Scoped analytics series + KPIs.** `GET /api/admin/analytics/series?scope&weeks=12` (readiness %, attendance %, skilling hours, offers per week) and `/analytics/kpis` (placement rate = placed / eligible, median CTC from offers, placement-ready %, attendance avg, mock interviews count, pending approvals) with deltas vs the previous period; mentor load paginated (`/mentor-load?page=`).
**B8.6 Nightly snapshots.** `analytics_snapshots(scope_type, scope_id, week, metrics jsonb)` written by `app.analytics_job` (EventBridge Scheduler like `reep-retention-daily`, 02:00 IST) so series are cheap and stable.

---

## B9 · Mentor mapping

**B9.1 History + handover.** `mentor_assignments(student_id, mentor_id, from_at, to_at, by_user_id, reason, kind assign|release|reassign|faculty_disabled)`; `students.mentor_id` stays the current pointer; migration seeds one open row per current pair. A 90-day **handover read grant**: the previous mentor keeps read access to the student's notes/records until `to_at + 90 d` (`policies.assert_student_scope` honours it). Nothing re-keyed or deleted.
**B9.2 Validation, audit, notify.** Assignment requires `reason`; same college (B1.5); capacity from a governance setting `mentor_capacity` per department (default `MENTOR_CAPACITY`); audited; both people emailed (B3.7).
**B9.3 Bulk assign by batch.** `POST /api/admin/cohorts/{id}/students/bulk {action: mentor, mentor_user_id, student_ids?}` already exists — add validation + history + audit.
**B9.4 Filters + pagination.** `mentor-load` and `unassigned-students` accept `college_id/department_id/cohort_id/q/page`.

---

## B10 · Leave

**B10.1 Approval chain by function.** Decision requires `mentor.leave_approve` (B2.1) with a scope; first signature = mentor or HOD of the requester's department, second = principal function or the Main Admin; the Main Admin may sign **either** step; a staff member's own request follows the same chain (fixes the FIRST_APPROVED deadlock). Compat: existing FIRST_APPROVED rows are decidable by the Main Admin immediately.
**B10.2 Types, balances, calendar.** `leave_balances(user_id, academic_year, kind, entitled, used)`, `academic_calendar(college_id, date, kind holiday|working)`; overlap check (422); balance check with LOP fallback.
**B10.3 Attachments.** `leave_attachments` through `document_store` (PDF/JPEG/PNG ≤ 10 MB), listed on the request.
**B10.4 Cancel / withdraw.** `POST /api/leaves/{id}/cancel` by the requester while not APPROVED (status CANCELLED already exists in the enum).
**B10.5 Notifications.** Mail on submit / first sign / decision (B3.7).
**B10.6 Alternate arrangement.** `alt_rows[].user_id` links a faculty account; `POST /api/leaves/{id}/alternate/accept` by that account; the paper prints the name.
**B10.7 Scope + privacy.** Queues scoped by B1.2 (department); `reason` and attachments returned only to holders of `mentor.leave_approve` in scope.
**B10.8 Paper PDF.** `leave_paper.py` prints the function that signed (Mentor / HOD / Principal / Main Admin) instead of a fixed PROGRAM DIRECTOR label, and refuses to render a decided request whose signer has no signature image (422 with a hint) — PDFs already generated are not regenerated.

---

## B11 · Registrations

**B11.1** Queue scoped by college (B1.2); `checks[]` in each pending row: domain in the college's `email_domains` (B1.1), USN pattern, matched rule, duplicate account. **B11.2** `status HOLD` + `hold_note`; `POST /{id}/hold`. **B11.3** `registration_rules` CRUD (`GET/POST/PATCH/DELETE /api/register/rules`, cap `admin.registrations`, audited). **B11.4** Fix the client copy: approval provisions the account and emails the onboarding link (the server already does).

## B12 · Jobs & placement

**B12.1** `jobs.college_id`, `course_id`, `tracks text[]`; the student feed filters by the student's college/course and track; eligibility from B8.2 criteria. **B12.2** `jobs.status open|closed` + `POST /api/admin/jobs/{id}/close`; DELETE stays 409 once applied. **B12.3** `GET /api/admin/placement?cohort_id=` returns the funnel (eligible → applied → interviewed → offered → placed, distinct students), yearly KPIs (rate, median CTC, highest, multiple offers) and the by-track split; `GET /api/admin/placement/offers.csv`.

## B13 · Catalogue

`approved_certifications.college_id/course_id`; `badge_course_map(badge_code, course_id, enabled)` so the code-defined 48 badges are enabled per course; `stage_rules(course_id, semester, stage)` used by B4.3; `POST /api/admin/catalogue/copy {from_course, to_course, parts[]}`; `POST /api/admin/catalogue/subjects/import` (CSV → `courses`). Existing programme-wide rows are attached to BGSCET/MBA by migration.

## B14 · Exports

Every export endpoint applies `scope_filter`, requires the PII-carrying function for personal columns (else those columns are omitted), writes `export_events(user_id, kind, filters, rows, at)` (audited), and the Interviews extract is summary-only (B6.2). `GET /api/admin/exports/history`.

## B15 · My account

`GET /api/auth/me` gains `google_linked`, `last_sign_ins[]` (from a new `login_events` table written by `_record_login`), `notification_prefs`; `POST /api/auth/google/unlink` (only when a scrypt hash exists), `PUT /api/auth/notification-prefs`, `POST /api/auth/sign-out-everywhere` (B3.6). Signature endpoints unchanged.

## B16 · Agent

No new endpoints. `POST /api/agent/ask` answers already carry `sources[]` and `limitations[]`; the staff answer adds `scope` (from B1.2) to the response so the "What the agent can see" card is server-stated; Rule 1 status = `student_data_egress_allowed` reported as a boolean.

## B17 · Student portal (for the mock-interview screens)

`GET /api/interview/policy` (B6.1 / B5.3 / B6.4), `GET /api/interview/progress` (B6.2), `GET /api/interview/sessions/{id}/views` (B6.5), `question_id` on transcript events (B6.6). Everything else on the student portal is a restyle with no API change.

---

## Cross-cutting guardrails (from the Compatibility board — these are acceptance criteria)

| Flow | Guardrail |
|---|---|
| Faculty sign-in & home | `users.role` stays MENTOR; routes unchanged; only the label and the baseline change |
| Faculty with mentees | one `mentor.*` grant per existing `mentors` row **before** the baseline shrinks |
| Mentor assignment | existing cross-department pairs kept, flagged, never broken |
| Student sign-in & home | no student route or API change for scoping — scope applies to staff queries |
| Student semester | batches without a course level get `total_semesters` from degree level at migration |
| Records after promotion | results, ledger, interviews, badges never change semester numbers |
| Graduation | login and USN identical; student screens read-only; jobs sheet stays |
| Registration | migration seeds the env domains onto BGSCET |
| Faculty leave | existing FIRST_APPROVED rows decidable by the Main Admin at once |
| Mock interview track | old codes hr/dm/ba/fa map 1:1 to the seeded tracks |
| Policy vs consent | existing consent rows keep working; the next version asks once; a policy change stops a running session with 4014 only when it removes a scope |
| Grants after scoping | every existing grant backfilled to scope = BGSCET |
| Feature switches | an unwired switch cannot be turned off for anyone |
| Daily cap | value stays 8; counted attempts are never lost mid-day |
| Interview retention | summaries backfilled before the next purge |
| Mentor notes after reassignment | 90-day handover read grant; nothing re-keyed |
| Disabled faculty | active accounts untouched; `token_version` bumps only on disable |
| Activation link | fresh invites as before; links already sent stay valid to their expiry |
| SWOC to student | author from `author_user_id`; no re-entry |
| Leave paper | old PDFs not regenerated |
| Placement criteria | hard-coded defaults remain the fallback |
| Attendance & marks | screens show "no import yet" instead of zeros; readiness "unassessed" until data exists |
