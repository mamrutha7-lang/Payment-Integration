# 02 — Test Plan

| | |
|---|---|
| Document | REEP Master Test Plan, test cycle 2026-09-29 |
| Standard followed | ISO/IEC/IEEE 29119-3:2021 §7 *Test Plan* (the successor to IEEE 829-2008's Test Plan) |
| Parent | [01 — Test Strategy](01-test-strategy.md) |

## 1. Context

| Item | Value |
|---|---|
| Project | REEP, a college placement-readiness dashboard (student, faculty, alumni and admin portals) |
| Test items | `apps/api-py` (FastAPI, 318 paths / 370 operations in the OpenAPI document), `apps/web` (Angular 22 SPA), database schema at Alembic head |
| Build | `main@1685e67`, plus branch `claude/gallant-cori-t4dbxw` (defect fix DEF-001) |
| Stakeholders | Placement office (Main Admin), faculty, students, developers |

## 2. Scope

### 2.1 Features to be tested

| Feature | Levels | Types |
|---|---|---|
| Authentication (password door, session cookie, single-device rule, brute-force limiter, sign-out) | Unit, integration, system | Functional, security |
| Role-based access (student / mentor / admin; rule 2) | Integration, system | Functional, security |
| Public API contract (the Swagger document) | System | Functional (contract), compatibility |
| Student screens: home, jobs, time sheet, leaderboards, skilling | System | Functional, performance, accessibility, responsive |
| Registration form validation | System | Functional (negative) |
| Leave request validation | System (API) | Functional (negative, BVA) |
| API under concurrent load | System | Performance (load, stress, spike, soak) |
| Browser concurrency (100 students signing in at once) | System | Performance / scalability |
| Every existing backend and web unit test | Unit, integration | Regression |

### 2.2 Features not to be tested (and why)
See [01 §6](01-test-strategy.md#6-scope-boundaries): write fuzzing, the
realtime interviewer, Google, SES, S3 and production-scale capacity.

## 3. Test approach per suite

| Suite ID | Suite | Location | Level / type | Runs against |
|---|---|---|---|---|
| TS-UNIT-API | Backend unit and integration | `apps/api-py/tests` (136 modules incl. the new regression test) | Unit + integration | `reep_test` DB via `TestClient` |
| TS-UNIT-WEB | Web unit | `apps/web/src/**/*.spec.ts` | Unit | Vitest (jsdom) |
| TS-API-01..04 | Swagger/OpenAPI + functional API | `testing/api/test_0*.py` | System, functional + security | Live API :3300 |
| TS-API-05 | Schemathesis property-based | `testing/api/run_schemathesis.sh` | System, contract/robustness | Live API :3300, GET only |
| TS-SEL-01 | Selenium, single instance | `testing/selenium/test_single_instance.py` | System, functional UI | SPA :4200 |
| TS-SEL-02 | Selenium, 1 and 100 concurrent instances | `testing/selenium/run_parallel.py` | System, performance/scalability | SPA :4200 |
| TS-PW-E2E | Playwright functional e2e (existing) | root `tests/*.spec.ts` + `test-management/` | System, functional | SPA :4200 |
| TS-PW-NFR | Playwright non-functional | `testing/playwright/*.nfr.ts` | System, non-functional | SPA :4200 |
| TS-PERF | JMeter smoke / load / stress / spike / soak | `testing/jmeter/` | System, performance | Live API :3300 |

### 3.1 JMeter workload model

| Scenario | Virtual users | Ramp-up | Duration | Think time | Purpose (ISTQB CT-PT) |
|---|---:|---:|---:|---:|---|
| smoke | 1 (+5 public) | 1 s | 30 s | 0 | Proves the script and environment work |
| load | 50 (+5) | 30 s | 180 s | 500–1000 ms | Expected busy-hour load: time behaviour against the SLA |
| stress | 100 (+5) | 60 s | 180 s | 0 | Beyond the expected load, with no think time: find the knee and check error handling |
| spike | 100 (+5) | 2 s | 90 s | 200–400 ms | A sudden arrival (a results announcement) |
| soak | 30 (+5) | 30 s | 600 s | 1–2 s | Endurance: memory, pool exhaustion, latency drift |

Each virtual student signs in **once** (the `POST /api/auth/login` sampler,
which includes a scrypt hash) and then loops over 10 authenticated GETs: `me`,
`dashboard`, `programme`, `jobs`, `leaderboards`, `ledger`, `badges`,
`timesheet`, `placement-readiness` and `profile`. A parallel anonymous group
calls `/health`, `/api/auth/sso/status` and `/api/register/hierarchy`. Every
sampler asserts its HTTP status and an SLA duration assertion (`-Jsla_ms`,
default 1000 ms). **Virtual user N signs in as `loadtestNNN`**, because REEP
allows one live session per account (see TW-001 in [05](05-incident-reports.md)).

## 4. Test environment (test bed)

| Component | Configuration |
|---|---|
| Host | Linux VM, 4 vCPU, 15 GB RAM, no swap |
| Database | PostgreSQL 16 + pgvector 0.6 on :5433 (the project targets PG 17, and the schema is identical). Two databases, `reep_py` (live stack) and `reep_test` (pytest), because AGENTS.md forbids a pytest run beside a live API on one database |
| API | Python 3.14.7, `uvicorn app.main:app --port 3300 --workers 2`, `ENV=dev`, pool 20 + 20 per worker |
| Web | Angular dev server `ng serve` :4200 on Node 24.15 (not a production build), proxying `/api` to :3300 |
| Browsers | Chromium 141.0.7390.37 (headless), ChromeDriver 141.0.7390.122 |
| Load generator | JMeter 5.6.3 on OpenJDK 21, **on the same host** (this adds CPU contention, which the report notes) |
| Test data | The dev seed, plus 110 `loadtestNNN@bgscet.ac.in` students (`testing/tools/create_load_users.py`): 001–100 for JMeter and Selenium ×100, and 101–110 reserved for the functional suites |

## 5. Entry, exit, suspension and resumption criteria

**Entry and exit** are as in [01 §7](01-test-strategy.md#7-entry-and-exit-criteria-29119-3-test-plan-content).

**Suspension:** stop a performance run when the error rate exceeds 50 % in its
first minute. That points to a scripting or environment fault, not a
measurement, as the first stress run showed (TW-001). Never run two suites
that sign in as the same accounts at the same time.

**Resumption:** once the cause is fixed, re-run the whole scenario, not the
remainder of it.

## 6. Schedule (this cycle)

| Order | Activity | Why this order |
|---|---|---|
| 1 | Stack up, migrate, seed, create load accounts | Entry criteria |
| 2 | Backend unit + integration (own DB) | The cheapest feedback first |
| 3 | JMeter smoke → load → stress → spike → soak (nothing else running) | Measurements need an idle machine |
| 4 | API suite (TS-API-01..04), then Schemathesis | Functional system tests |
| 5 | Selenium single instance, then 1 and 100 instances | UI, then UI at scale |
| 6 | Playwright NFR, then the Playwright e2e suite | e2e last: it leaves data behind by design |
| 7 | Web unit tests | Independent of the stack |
| 8 | Completion report and incident reports | Exit |

## 7. Deliverables (29119-3 test documentation set)

| Document | File |
|---|---|
| Test strategy | [01-test-strategy.md](01-test-strategy.md) |
| Test plan | this file |
| Test design and test case specification, with the traceability matrix | [03-test-design-and-cases.md](03-test-design-and-cases.md) |
| Test completion report (test summary report) | [04-test-completion-report.md](04-test-completion-report.md) |
| Incident reports | [05-incident-reports.md](05-incident-reports.md) |
| Test logs and results | `testing/results/` (JUnit XML, JMeter JTL summaries and HTML dashboards, Selenium CSV/JSON, Playwright HTML/JUnit) |
| Testware | `testing/api`, `testing/selenium`, `testing/playwright`, `testing/jmeter`, `testing/tools` |

## 8. Project risks (risks to the testing itself)

| Risk | Mitigation |
|---|---|
| The load generator shares the host with the system under test, so the numbers are pessimistic | Stated with every number. Re-run from a separate host for capacity sign-off |
| One session per account makes suites interfere | Disjoint account pools per suite; strictly sequential runs |
| The dev server (`ng serve`) is slower than production | Budgets for page timing are loose and labelled as dev-server budgets |
| Destructive endpoints under fuzzing | Schemathesis limited to GET; write fuzzing only on a throwaway DB |
| Test data left behind (e2e by design; one withdrawn leave from API-04-004) | Run on the dev DB only, never production (`create_load_users.py` refuses `ENV=prod`) |
