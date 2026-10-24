# 01 — Test Strategy

| | |
|---|---|
| Document | REEP Test Strategy (project level) |
| Standard followed | ISO/IEC/IEEE 29119-3:2021 §6 *Organizational Test Strategy* / *Test Strategy* content, ISO/IEC/IEEE 29119-2:2021 test processes, ISTQB® CTFL v4.0 (2023) terminology |
| Quality model | ISO/IEC 25010:2023 (product quality characteristics) |
| System under test | REEP placement-readiness dashboard: Angular 22 SPA (`apps/web`) + FastAPI API (`apps/api-py`) on PostgreSQL |
| Version tested | `main` at `1685e67`, plus the defect fix made on this branch |
| Author / date | Prepared with Claude Code for bdarshan5@bgscet.ac.in, 2026-09-29 |

## 1. Purpose

This strategy sets out how REEP is tested, at which levels, for which quality
characteristics, with which tools and techniques, and what "done" means. It is
the parent of the Test Plan ([02](02-test-plan.md)), the Test Design and Case
Specification ([03](03-test-design-and-cases.md)), the Test Completion Report
([04](04-test-completion-report.md)) and the Incident Reports
([05](05-incident-reports.md)).

## 2. Risk-based approach

ISO/IEC/IEEE 29119-1 makes risk the basis for how much testing each area gets.
The product risks below come from the architecture notes in `AGENTS.md`.
Likelihood (L) and impact (I) are scored 1–3, and the exposure is L × I.

| ID | Product risk | L | I | Exposure | Where it is tested |
|---|---|:-:|:-:|:-:|---|
| R1 | A student reads another student's marks, attendance or USN (broken access control, OWASP API1/API5) | 2 | 3 | **6** | API RBAC matrix (TS-API-02), BOLA probe (API-04-008), Selenium SEL-011, backend `test_auth_rbac.py`, `test_no_director_privilege.py` |
| R2 | Sign-in or sessions fail: a login door, single-device retirement, brute-force limiter | 2 | 3 | **6** | TS-API-03, Selenium SEL-002..007, Playwright e2e module 01, JMeter login sampler |
| R3 | The API slows down or fails under a results-day peak (100+ students at once) | 2 | 3 | **6** | JMeter load / stress / spike / soak, Selenium 100 instances |
| R4 | The API breaks its own published contract (Swagger/OpenAPI), so clients break | 2 | 2 | 4 | TS-API-01/02, Schemathesis |
| R5 | Invalid input is accepted (leave dates running backwards, oversized fields) | 2 | 2 | 4 | TS-API-04, backend unit tests |
| R6 | The SPA is unusable on a phone or with a keyboard or screen reader | 2 | 2 | 4 | Playwright NFR responsive and a11y, Selenium SEL-012/013 |
| R7 | The front end hangs or goes blank when the API is down | 1 | 2 | 2 | Playwright NFR-REL |
| R8 | Session material leaks to scripts or storage (XSS impact) | 1 | 3 | 3 | Playwright NFR-SEC, API-03-001 |

## 3. Test levels (ISTQB CTFL v4.0 §2.2)

| Level | Test basis | Test object | Tooling | Owner of the suite |
|---|---|---|---|---|
| **Unit (component)** | Function contracts, docstrings, business rules | Pure functions, models, Angular components/services | `pytest` (backend, 135 modules), Vitest via `ng test` (32 web specs) | Existing in the repository |
| **Integration (component integration)** | API/database interaction, router ↔ ORM ↔ Postgres | FastAPI routers on a real migrated PostgreSQL through `TestClient` | `pytest` + a real database (`reep_test`) | Existing in the repository |
| **System** | Requirements (`test-management/cases`), OpenAPI document, AGENTS.md rules | The deployed stack: SPA :4200 → API :3300 → Postgres | Playwright e2e (256 tests, 273 manual cases), Selenium WebDriver, `requests`+`pytest` API suite, Schemathesis | Playwright e2e existing; the rest added in `testing/` |
| **System (non-functional)** | ISO/IEC 25010 characteristics, performance budgets | Same deployed stack | Apache JMeter 5.6.3, Selenium ×100, Playwright + axe-core | Added in `testing/` |
| **Acceptance** | User stories and manual cases | Staging with real users | The manual cases in `test-management/` are the acceptance test basis | Out of scope for automation here (§6) |

## 4. Test types (ISTQB CTFL v4.0 §2.2.2)

### 4.1 Functional testing
This checks what the system does against its specification: the manual cases,
the OpenAPI document, and the rules in `AGENTS.md`. It is black-box at the
system level and white-box in the unit suite.

### 4.2 Non-functional testing (ISO/IEC 25010:2023)

| Characteristic | Sub-characteristic | Test | Tool |
|---|---|---|---|
| Performance efficiency | Time behaviour, capacity, resource utilisation | Load, stress, spike and soak scenarios, page timing budgets | JMeter, Selenium ×100, Playwright |
| Interaction capability (usability) | Accessibility, user error protection | WCAG 2.2 A/AA automated checks, keyboard-only sign-in | Playwright + axe-core |
| Flexibility | Adaptability | No horizontal overflow at 360, 768 and 1366 px; the phone drawer | Playwright, Selenium mobile emulation |
| Reliability | Fault tolerance, availability | API unreachable, a single endpoint returning 500, soak error rate | Playwright route interception, JMeter soak |
| Security | Confidentiality, integrity, authenticity, non-repudiation | RBAC matrix, BOLA, brute-force throttling, cookie flags, CORS, security headers, injection strings, XSS reflection | API suite, Playwright NFR-SEC |
| Compatibility | Interoperability | The OpenAPI 3.1 contract is valid, and responses match their schemas | openapi-spec-validator, jsonschema, Schemathesis |

### 4.3 Change-related testing
**Confirmation (re-)testing** runs the failing test again after a fix
(DEF-001 in [05](05-incident-reports.md)). **Regression testing** is the full
backend suite plus the new regression test `tests/test_admin_mail_log.py`.

## 5. Test design techniques (ISO/IEC/IEEE 29119-4:2021)

| Technique | 29119-4 clause | Applied to |
|---|---|---|
| Equivalence partitioning | 5.2.1 | Login: valid, wrong password, unknown address, malformed body (API-03-002..004) |
| Boundary value analysis | 5.2.3 | Leave `reason` at 0 and 2001 characters, `from == to` against `to < from`, empty and oversized credentials |
| Decision table | 5.2.6 | Role × area RBAC matrix: anonymous, student, mentor, admin × `/api/student`, `/api/mentor`, `/api/admin` |
| State transition | 5.2.8 | Session: signed out → signed in → retired by a second sign-in → 401 `X-Reep-Session: retired` |
| Syntax testing | 5.2.4 | Schemathesis generates valid and invalid inputs from the OpenAPI schemas |
| Scenario testing / use case | 5.2.9 | Selenium and Playwright journeys: sign in → home → jobs → time sheet → sign out |
| Error guessing | 5.4.1 | SQL-injection strings, `alg=none` JWT, `javascript:` redirect, reflected `<img onerror>` |
| Statement/branch coverage (structure-based) | 5.3 | Existing backend unit tests (not measured in this cycle, see §8) |

## 6. Scope boundaries

**In scope:** every HTTP surface under `/api`, the Swagger document, the SPA's
public and student screens (functional and non-functional), the admin sign-in
and RBAC, and API performance.

**Out of scope, with reasons:**
- **Write fuzzing (POST/PATCH/DELETE) with Schemathesis.** REEP's delete
  endpoints are destructive by design. Write fuzzing belongs on a throwaway
  database (see [02 §8](02-test-plan.md)).
- **The realtime mock interviewer (WebSocket → Amazon Nova Sonic).** It needs
  AWS Bedrock credentials. The unit and simulation suites cover it
  (`test_interview_nova.py`, `test_interview_simulation.py`).
- **Google sign-in, SES mail and S3.** These are external services with no
  credentials in the test bed. The backend suite covers them with fakes.
- **Production-scale capacity.** The test bed has 4 vCPU and 2 uvicorn
  workers, against Fargate with autoscaling. The numbers are relative, not a
  capacity guarantee (see [04 §6](04-test-completion-report.md)).

## 7. Entry and exit criteria (29119-3 Test Plan content)

**Entry:** the stack is up (`/health` returns 200, SPA returns 200), migrations
are at head, the dev seed and the load-test accounts are applied, and each
suite's environment pre-check passes. Otherwise the run is reported
**Blocked**, not Failed.

**Exit (per cycle):**
1. Backend unit and integration: 0 failures.
2. API functional and contract: 0 failures, or each failure raised as an incident.
3. Selenium single instance: 0 failures. Selenium ×100: pass rate ≥ 95 %.
4. JMeter load scenario: error rate < 1 %, p95 < 1000 ms (the SLA assertion in the plan).
5. Accessibility: no *critical* axe violation. *Serious* violations are recorded as incidents.
6. Every defect found is logged with severity and status.

## 8. Metrics collected

Metrics are pass/fail/skip counts per suite, defects by severity, and response
time per endpoint in **milliseconds** (mean, median, p90, p95, p99, max,
latency or TTFB, connect). They also include throughput (requests per second,
mean and peak), error rate, browser timings (browser start, login→home, page
heading visible), Navigation Timing (TTFB, DCL, load, LCP), and axe violation
counts. Code coverage was **not** collected in this cycle and is recommended as
a follow-up.

## 9. Tools

| Tool | Version | Why this tool |
|---|---|---|
| pytest | 9.1.1 | The backend's existing runner; also runs the API and Selenium suites |
| requests / jsonschema / openapi-spec-validator | 2.34 / 4.26 / 0.9 | HTTP client; validates responses against the Swagger schemas; validates the document itself |
| Schemathesis | 4.28.0 | Property-based API testing generated from the OpenAPI (Swagger) document |
| Swagger UI / ReDoc | FastAPI built-in (`/docs`, `/redoc`) | The human-facing API documentation; the same `/openapi.json` drives the automated contract tests |
| Apache JMeter | 5.6.3 | Load, stress, spike and soak; millisecond-resolution JTL; HTML dashboard |
| Selenium WebDriver | 4.49.0, ChromeDriver 141 | Browser automation with the Page Object Model; 1 and 100 concurrent instances; Grid config for scale-out |
| Playwright | 1.63.0 | The existing functional e2e suite (256 tests, 273 manual cases) and the new non-functional suite |
| axe-core (@axe-core/playwright) | 4.13 | WCAG 2.x A/AA automated checks |
| Vitest (Angular unit builder) | via `ng test` | Web unit tests |

## 10. References

- ISO/IEC/IEEE 29119-1:2022 *Software testing — General concepts*
- ISO/IEC/IEEE 29119-2:2021 *Test processes*
- ISO/IEC/IEEE 29119-3:2021 *Test documentation* (supersedes IEEE 829-2008)
- ISO/IEC/IEEE 29119-4:2021 *Test techniques*
- ISO/IEC 25010:2023 *Product quality model*
- ISTQB® Certified Tester Foundation Level Syllabus v4.0 (2023), and ISTQB Glossary
- ISTQB® Certified Tester Performance Testing (CT-PT) syllabus
- OWASP API Security Top 10 — 2023
- W3C WCAG 2.2 (2023); W3C Navigation Timing Level 2; Largest Contentful Paint
- OpenAPI Specification 3.1.0
- Apache JMeter User Manual, *Best Practices* (non-GUI runs, no listeners in the plan, HTML report generation)
- Selenium documentation: *Page Object Models*, *Selenium Grid 4*
- Playwright documentation: *Best Practices*, *Accessibility testing*
