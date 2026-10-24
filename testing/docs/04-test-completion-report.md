# 04 — Test Completion Report

| | |
|---|---|
| Document | REEP Test Completion Report (Test Summary Report), cycle 2026-09-29 |
| Standard followed | ISO/IEC/IEEE 29119-3:2021 §10 *Test Completion Report* (IEEE 829's *Test Summary Report*) |
| Build tested | `main@1685e67`, plus branch `claude/gallant-cori-t4dbxw` (fix for DEF-001) |
| Test bed | 4 vCPU / 15 GB Linux VM; PostgreSQL 16 + pgvector; API on Python 3.14.7 with 2 uvicorn workers; SPA on the Angular **dev server**; Chromium 141 ([02 §4](02-test-plan.md#4-test-environment-test-bed)) |
| Plans | [01 Strategy](01-test-strategy.md) · [02 Plan](02-test-plan.md) · [03 Cases](03-test-design-and-cases.md) · [05 Incidents](05-incident-reports.md) |

## 1. Summary

REEP's functional behaviour is in good shape. Across **2,882 automated test executions
at four levels**, the only product failures are the ten defects listed in §5. Every
one of them sits at an edge: a malformed input, an outage, contrast, or an
endpoint no test had called. None is in a main user journey. Under load the API
is fast and error-free: **118,725 JMeter requests, 100 % HTTP 200**. At a
busy-hour load of 50 concurrent students, the p95 is **21 ms**. At 100
concurrent students with no think time, the p95 is 508 ms. **100 of 100**
Selenium browsers completed a full sign-in journey.

The most important findings:

1. **DEF-001 (Major, fixed):** the admin *Email delivery* screen had never
   worked. All three endpoints answered 422 to everyone. The Swagger contract
   suite found it by calling every documented GET, which no existing test did.
2. **DEF-002…007 (Medium/Low, fixed in the follow-up, §5.1):** seven endpoints turn bad input into a
   **500** (database constraint and range errors) instead of a 422. Schemathesis
   found them from the OpenAPI document alone.
3. **DEF-010 (Medium, fixed in the follow-up, §5.1):** WCAG AA colour-contrast failures on the sidebar
   labels of every student screen and on the login helper text.
4. **Capacity note (OBS-002):** sign-in is the one CPU-bound request (scrypt).
   When 100 students arrive within 2 seconds, sign-in averages **1.9 s** while
   every other endpoint stays at a 73 ms median.

**Recommendation:** release-ready for its current functional scope. All ten defects are fixed on this branch, each with a regression test (§5.1). Re-run the performance scenarios from a separate load host against a production build before a results-day capacity sign-off (§6).

## 2. Results by suite

| Suite | Level / type | Tool | Total | Passed | Failed | Skipped / known | Verdict |
|---|---|---|---:|---:|---:|---:|---|
| TS-UNIT-API: backend unit + integration | Unit, integration | pytest 9.1 | 1,923 | 1,920 | 0 | 3 skipped | ✅ |
| TS-UNIT-API: new regression test for DEF-001 | Regression | pytest | 2 | 2 | 0 | — | ✅ (fails on the old code) |
| TS-UNIT-WEB: web unit (32 files) | Unit | Vitest via `ng test` | 221 | 221 | 0 | — | ✅ |
| TS-API-01..04: Swagger contract, functional, security | System | pytest + requests + jsonschema | 283 | 281 | 0 | 2 skipped (correct 403/404) | ✅ after the DEF-001 fix |
| TS-API-05: Schemathesis, GET only | System, property-based | Schemathesis 4.28 | 2,089 generated cases / 102 operations | — | 3 server errors, 6 schema mismatches | — | ❌ → DEF-006, DEF-008 |
| TS-SEL-01: Selenium, single instance | System, UI functional | Selenium 4.49 | 14 | 14 | 0 | — | ✅ |
| TS-SEL-02: Selenium, 1 instance | System, performance | Selenium | 1 | 1 | 0 | — | ✅ |
| TS-SEL-02: Selenium, 100 instances | System, performance | Selenium | 100 | **100** | 0 | — | ✅ (≥ 95 % required) |
| TS-SEL-02: Selenium, 50 simultaneous (`--barrier`) | System, performance | Selenium | 50 | 46 | 4 timeouts | — | ⚠️ load-generator saturation (§4.3) |
| TS-PW-NFR: Playwright non-functional | System, NFR | Playwright 1.63 + axe 4.13 | 32 | 31 | 0 | 1 known failure (DEF-009) | ✅ gate; serious a11y → DEF-010 |
| TS-PW-E2E: Playwright functional e2e (existing, 273 manual cases) | System, functional | Playwright 1.63 | 256 | 242 → **256** | 14 → 0 | 17 cases are manual-only | ✅ after isolating the test data (TW-008) |
| TS-PERF: JMeter (5 scenarios) | System, performance | JMeter 5.6.3 | 118,725 requests | 118,693 | 0 HTTP errors | 32 over the 1 s SLA | ✅ (§4) |

### 2.1 Functional end-to-end (existing Playwright suite)

The suite's own reporter linked every test to its manual case
(`test-management/cases/`) and wrote `manual-test-results.csv` (273 rows).

| Run | Environment | Passed | Failed / timed out | Not automated |
|---|---|---:|---:|---:|
| Full run (11.5 min) | Shared DB with 110 load-test students; API with 2 workers | 242 | 10 / 4 | 17 |
| Re-run of the 14 | Fresh `reep_e2e` DB (seed only); API with 1 worker, the documented dev setup | **14** | 0 | — |

**All 256 automated cases pass in the documented environment.** The 14 were
test-environment effects (TW-008): the roster grid virtualises rows, so the
seeded student fell outside the rendered window among 110 extra students; and
three auth cases assume one API process. Those three are worth reading as
product information too. On a multi-process deployment, the reset-link
limiter and the "signed out on another device" note are per-process
(**OBS-005**), and production runs two or more tasks.

| Module | Cases | Result |
|---|---:|---|
| 01 Authentication and access | 41 | all automated pass (TC-022, 025, 038 on a single worker) |
| 02 Registration and onboarding | 24 | pass |
| 03 Student home and progress | 28 | pass |
| 04 Student profile, jobs and tools | 30 | pass |
| 05 Faculty and alumni | 32 | pass |
| 06 Admin people and access | 40 | pass (11 needed the isolated DB) |
| 07 Admin daily operations | 35 | pass |
| 08 Admin college setup and interviews | 43 | pass |

## 3. API: Swagger / OpenAPI results

- **Document:** OpenAPI 3.1, **318 paths / 370 operations**. It validates
  against the meta-schema. Every operation has a unique `operationId` and a
  documented 2xx. Swagger UI (`/docs`) and ReDoc (`/redoc`) are served.
- **Contract (TS-API-02):** 118 parameter-free GETs (84 of them in a role-owned area) were called as anonymous,
  as their owner role, and as a student (RBAC). No GET returned 5xx. No
  staff-area GET returned 2xx to a student. Every owner-role 200 validated
  against its declared response schema. **One path failed before the fix:**
  `/api/admin/mail-log` → DEF-001.
- **Functional and security (TS-API-03/04):** all 19 pass. Wrong password and
  unknown email answer identically. An `alg=none` token is refused. Brute force
  gets a 429 within 10 × workers + 1 attempts. A second sign-in retires the
  first **within 60.4 s** (OBS-001). Leave dates are validated at the
  boundaries. Security headers are present. A foreign CORS origin is refused.
  BOLA probes are refused. Injection strings come back as data.
- **Schemathesis (TS-API-05):** 2,089 generated GET requests across 102
  operations. There were **3 server errors** (integer overflow in query
  parameters → DEF-006) and **6 response-schema violations**, where a 422 with
  a string `detail` sits against a declared array (DEF-008). About 250
  "undocumented status code" reports are the 401/403 responses FastAPI does not
  document (OBS-003). The accidental first run, which also sent writes
  (TW-003), found 120 + 14 + 3 + 1 + 1 + 1 + 1 further 500s → DEF-002…005,
  DEF-007.

## 4. Performance results (JMeter), milliseconds

All five scenarios ran on a freshly restarted API with the corrected plan
(TW-001). **Every one of the 118,725 responses was HTTP 200.** "SLA fails" are
responses slower than the plan's 1,000 ms duration assertion.

| Scenario | VUs | Duration | Requests | Throughput mean / peak (req/s) | Mean | Median | p90 | p95 | p99 | Max | Sign-in mean | SLA fails |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Smoke | 1 + 5 | 30 s | 17,529 | 586.5 / 678 | 9.9 | 6 | 19 | 23 | 39 | 357 | 196 | 0 |
| **Load** | 50 + 5 | 180 s | 11,947 | 66.6 / 82 | **8.8** | **7** | **15** | **21** | **46** | 165 | 51 | **0** |
| Stress | 100 + 5 | 180 s | 52,417 | 290.8 / 398 | 301.9 | 316 | 458 | 508 | 682 | 1,134 | 360 | 23 (0.04 %) |
| Spike | 100 + 5 in 2 s | 90 s | 23,299 | 260.2 / 304 | 100.0 | 73 | 188 | 233 | 667 | 2,477 | **1,884** | 9 (0.04 %) |
| Soak | 30 + 5 | 600 s | 13,533 | 22.6 / 32 | 7.9 | 7 | 12 | 16 | 35 | 180 | 60 | 0 |

### 4.1 Busy-hour load (50 VUs), per endpoint

| Endpoint | Samples | Mean ms | Median | p90 | p95 | p99 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| POST /api/auth/login | 50 | 51.2 | 44 | 77 | 85 | 97 | 101 |
| GET /api/auth/me | 1,099 | 10.8 | 8 | 17 | 22 | 69 | 165 |
| GET /api/student/dashboard | 1,096 | 7.0 | 5 | 12 | 18 | 39 | 81 |
| GET /api/student/programme | 1,091 | 6.3 | 5 | 11 | 16 | 27 | 60 |
| GET /api/student/jobs | 1,083 | 13.2 | 10 | 21 | 28 | 49 | 117 |
| GET /api/student/leaderboards | 1,081 | 7.7 | 6 | 12 | 16 | 37 | 65 |
| GET /api/student/ledger | 1,073 | 7.7 | 6 | 12 | 18 | 35 | 71 |
| GET /api/student/badges | 1,070 | 10.9 | 8 | 17 | 25 | 49 | 135 |
| GET /api/student/timesheet | 1,066 | 7.4 | 5 | 12 | 17 | 32 | 89 |
| GET /api/student/placement-readiness | 1,062 | 11.2 | 8 | 18 | 25 | 48 | 149 |
| GET /api/student/profile | 1,054 | 7.5 | 5 | 12 | 19 | 45 | 77 |
| GET /health | 375 | 2.4 | 2 | 4 | 6 | 22 | 66 |
| GET /api/auth/sso/status | 374 | 2.7 | 2 | 4 | 6 | 15 | 51 |
| GET /api/register/hierarchy | 373 | 9.0 | 7 | 15 | 21 | 47 | 74 |

The per-endpoint tables for every scenario are in
`results/jmeter/<scenario>-summary.md`. The interactive JMeter HTML dashboards
(response time over time, percentiles, throughput, latency versus load) are in
`results/jmeter/dashboards/<scenario>.zip` (unzip, open `index.html`).

### 4.2 Interpretation

- **Time behaviour:** at the expected busy-hour load the whole student surface
  answers with a p95 of 21 ms, far inside the 1,000 ms SLA. **REQ-PERF-1 met.**
- **Knee:** with 100 users and no think time, throughput reaches about
  290 req/s and latency rises to a median of 300 ms. The API is CPU-bound on 2
  workers here, since the load generator shares the same 4 vCPUs. It degrades
  gracefully: no errors, no timeouts, and a p99 under 700 ms.
- **Spike:** the 100 logins that land within 2 s queue for CPU (scrypt), with a
  mean of 1.9 s and a max of 2.5 s. Nothing else is affected much (median
  73 ms). This is OBS-002.
- **Endurance:** p95 by minute over the 10-minute soak was 16 → 14 → 12 ms
  (minutes 1, 5 and 9), so there is no upward drift. No errors, and no pool
  exhaustion.

### 4.3 Browser concurrency (Selenium), milliseconds

| Run | Result | Browser start | Login page | Sign-in → home | Jobs page | Time sheet page |
|---|---|---:|---:|---:|---:|---:|
| 1 instance (baseline) | 1/1 | 259 | 846 | 720 | 646 | 578 |
| 100 instances, 100 threads (serialised launch) | **100/100**, 104 s wall | 50,284 mean* | 2,154 mean / 2,965 p95 | 2,386 mean / 3,559 p95 | 1,364 mean | 1,274 mean |
| 50 simultaneous (`--barrier`, `LIGHT_CHROME`) | 46/50 | 10,610 | 37,898 | 22,908 | 18,557 | 11,236 |

\* In the 100-instance run, "browser start" includes waiting for a launch slot:
ChromeDriver launches are serialised at about 1 s each, which is why the
figure grows to 100 s for the last browser.

The 100-thread run keeps about 20–30 browsers alive at once as launches
overlap journeys. The user-facing steps stayed between 1.3 and 2.4 s, against
0.6–0.85 s for a single browser. Holding **50 browsers truly simultaneous**
saturated the host: load average 128 on 4 vCPU and 13.2 GB RAM. The API's own
access log for that window shows a **p50 of 10 ms and a p95 of 220 ms**, so the
slow browser timings are the load generator rendering an unminified dev bundle,
not REEP. **100 truly simultaneous browsers need about 15 GB or more for
Chromium alone** (TW-006). Use `selenium/grid/docker-compose.yml` on a larger
host for that measurement.

### 4.4 Page performance (Playwright, dev server)

| Page | TTFB | DOMContentLoaded | LCP | Heading visible (signed in) |
|---|---:|---:|---:|---:|
| /login | 3 | 266 | 440 | — |
| /register | 2 | 203 | 396 | — |
| /student | 2 | 223 | 404 | 414 |
| /student/jobs | 3 | 194 | 396 | 469 |
| /student/time-log | 3 | 233 | 384 | 430 |
| /student/leaderboards | 3 | 241 | 392 | 396 |
| /student/skilling | 2 | 190 | 356 | 389 |

SPA route changes took 54–124 ms. Every page is under 0.5 s LCP (a Core Web
Vitals "good" rating is ≤ 2.5 s) even on the dev server. Its unminified bundle
is about 7.2–7.8 MB of JavaScript (the production initial bundle is about
142 kB per AGENTS.md), so measure bundle weight against `ng build` output, not
against this.

## 5. Defects and incidents

| ID | Title | Severity | Status |
|---|---|---|---|
| DEF-001 | `/admin/mail-log` + suppression endpoints answer 422 to everyone | Major | **Fixed** on this branch, with a regression test |
| DEF-002 | `PUT /student/profile` with a `null` boolean → 500 | Medium | **Fixed** (follow-up, 2026-09-30) |
| DEF-003 | `POST /student/checkin` with an unknown course → 500 | Medium | **Fixed** (follow-up, 2026-09-30) |
| DEF-004 | `POST /admin/jobs`, `/admin/criteria` with an empty or unknown `course_id` → 500 | Medium | **Fixed** (follow-up, 2026-09-30) |
| DEF-005 | `PATCH /admin/swoc/entries/{id}` with an unknown linked id → 500 | Medium | **Fixed** (follow-up, 2026-09-30) |
| DEF-006 | GET `audit` / `swoc` / `placement` with huge integers → 500 | Medium | **Fixed** (follow-up, 2026-09-30) |
| DEF-007 | A NUL byte or an invalid escape in text → 500 | Low | **Fixed** (follow-up, 2026-09-30) |
| DEF-008 | 422s with a string `detail` break the documented schema | Low | **Fixed** (follow-up, 2026-09-30) |
| DEF-009 | An unreachable API is shown as "This server signs in with Google only" | Low | **Fixed** (follow-up, 2026-09-30) |
| DEF-010 | WCAG AA colour contrast (2.75–4.34 : 1) on login and student screens | Medium | **Fixed** (follow-up, 2026-09-30) |

Details, reproduction steps and suggested fixes are in
[05-incident-reports.md](05-incident-reports.md), together with OBS-001…005 and the eight testware incidents (TW-001…008). The testware incidents are part
of this report on purpose. TW-001 alone would have reported a **44.9 % error
rate** as a capacity result if the per-thread 401 pattern had not been
investigated before it was believed.

### 5.1 Follow-up: DEF-002…010 fixed (2026-09-30)

All nine open defects were fixed on the same branch, each with a test that
**fails on the old code and passes on the fix** (checked by stashing the fix):

| Evidence | Before | After |
|---|---|---|
| `apps/api-py/tests/test_input_hardening.py` (DEF-002…008) | 8 failed | 8 passed |
| Full backend suite | — | 0 failures, 3 skipped |
| Schemathesis, GET only (`results/api/schemathesis-after-fixes/`) | 3 server errors, 6 schema violations | **0 and 0** (2,130 generated requests; what remains is OBS-003, the undocumented 401/403) |
| Playwright NFR-REL-01b (DEF-009) | known failure | passed |
| axe `color-contrast` on /login, /register, /student, /student/jobs, /student/time-log (DEF-010) | 12 elements, 2.75–4.34 : 1 | **0**, and now a gate |
| Web unit (`ng test`) / production build / four style guards | — | 221/221 · built · all pass |

## 6. Deviations from the plan, and residual risk

| Deviation | Consequence | Residual risk and follow-up |
|---|---|---|
| The root e2e suite run through a temporary config pointing Playwright 1.63 at the installed Chromium 141 (its own headless-shell build was not installed, and `playwright install` is unavailable in this sandbox) | None seen: the same binary ran the NFR suite | None |
| PostgreSQL 16 instead of 17 (the container had no Docker daemon) | The schema is identical; the planner may differ slightly | Low |
| The Angular dev server, not a production build | Page timings are pessimistic; the bundle size is not representative | Re-run TS-PW-NFR against `ng build` output |
| Load generator on the same 4-vCPU host as the system under test | The stress and spike numbers are **lower bounds on capacity** | Re-run TS-PERF from a separate host against a staging environment with production sizing |
| 100 truly simultaneous browsers not possible (RAM) | Measured 100 with overlapping launches, and 50 simultaneous | Run TS-SEL-02 `--barrier` on the Selenium Grid |
| Schemathesis limited to GET after TW-003 | Write-path robustness is only partly covered (by the accidental run 1) | Run a write-fuzzing pass on a throwaway DB, then fix DEF-002…007 |
| Code coverage not measured | Unknown structural coverage | Add `pytest-cov` / `ng test --coverage` in a follow-up |
| Realtime interviewer, Google, SES, S3 not exercised live | Covered only by the backend suite's fakes | Accept for this cycle |

## 7. Exit criteria assessment

| Criterion ([01 §7](01-test-strategy.md#7-entry-and-exit-criteria-29119-3-test-plan-content)) | Result |
|---|---|
| Backend unit and integration: 0 failures | ✅ 1,923 run, 0 failed |
| API functional and contract: 0 failures, or each raised | ✅ 0 failures after the fix; every Schemathesis failure raised (DEF-002…008) |
| Selenium single: 0 failures; ×100 ≥ 95 % | ✅ 14/14; ✅ 100/100 |
| JMeter load: errors < 1 %, p95 < 1000 ms | ✅ 0 %, p95 21 ms |
| No critical axe violation | ✅ none (serious ones raised as DEF-010) |
| Functional e2e (existing suite) | ✅ 256/256 automated cases in the documented environment |
| Every defect logged | ✅ DEF-001…010 |

**Exit criteria met for this cycle**, with the open defects above carried into the next one.

## 8. Where the evidence is

| Evidence | Path |
|---|---|
| Backend JUnit + console | `results/unit-integration/pytest-junit.xml`, `pytest-output.txt` |
| Web unit console | `results/web-unit-console.txt` |
| API suite HTML + JUnit | `results/api/report.html`, `results/api/junit.xml` |
| Schemathesis (read-only run) | `results/api/schemathesis/`, `results/api/schemathesis-console.txt` |
| Schemathesis run 1 (writes included, TW-003) | `results/api/schemathesis-run1-writes-included/` |
| JMeter dashboards and summaries | `results/jmeter/dashboards/<scenario>.zip` (unzip, open `index.html`), `results/jmeter/<scenario>-summary.md`, `results/jmeter-<scenario>-console.txt` |
| Selenium | `results/selenium/report.html`, `junit.xml`, `parallel-*.{md,csv,json}`, `*-memory.txt` |
| Playwright NFR | `results/playwright-nfr/html/index.html`, `junit.xml`, `results.json` |
| Playwright e2e | `results/playwright-e2e-console.txt`, `results/manual-test-results-full-run.csv` (full run), `results/manual-test-results-rerun-14.csv` (isolated re-run) |
