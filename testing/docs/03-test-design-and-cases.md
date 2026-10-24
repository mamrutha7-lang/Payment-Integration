# 03 — Test Design Specification and Test Case Specification

| | |
|---|---|
| Standard followed | ISO/IEC/IEEE 29119-3:2021 §8 (*Test Design Specification*: feature sets and test conditions) and §9 (*Test Case Specification*), techniques from ISO/IEC/IEEE 29119-4:2021 |
| Parent | [02 — Test Plan](02-test-plan.md) |

Each automated test's **name starts with its case ID** (for example
`test_api_03_007_...` or `NFR-PERF-01 ...`), so a failure in any report points
back to a row here. The existing functional e2e cases TC-001 to TC-799 are
specified in `test-management/cases/` and are not repeated here.

## 1. Requirements (test basis) used for traceability

| Req ID | Requirement | Source |
|---|---|---|
| REQ-AUTH-1 | A valid password sign-in issues an httpOnly, SameSite=Lax `reep_session` cookie | AGENTS.md "Auth" |
| REQ-AUTH-2 | Wrong credentials are refused without saying which half was wrong (no user enumeration) | login screen copy, `routers/auth.py` |
| REQ-AUTH-3 | One device at a time: a new sign-in retires older sessions, which get 401 with `X-Reep-Session: retired` | AGENTS.md "ONE DEVICE AT A TIME" |
| REQ-AUTH-4 | 10 failures per account per 15 minutes, then 429 | AGENTS.md brute-force limiter |
| REQ-RBAC-1 | A student never reads staff areas; Governance is the Main Admin's alone | AGENTS.md rule 2 |
| REQ-RBAC-2 | A student cannot read another student's record (object-level authorisation) | AGENTS.md rule 2, OWASP API1:2023 |
| REQ-API-1 | The OpenAPI document is valid, and every operation declares a success response and a unique operationId | OpenAPI 3.1, FastAPI |
| REQ-API-2 | Responses conform to the schemas the document declares; no 5xx on any documented GET | OpenAPI contract |
| REQ-VAL-1 | Leave: `to_date ≥ from_date`, and `from == to` is legal; `reason` has 1..2000 chars; `leave_kind` comes from a closed list | `LeaveIn` in `routers/leave.py` |
| REQ-VAL-2 | Registration requires its CV and photo | AGENTS.md "EVERY BOX ... COMPULSORY" |
| REQ-SEC-1 | `nosniff`, `X-Frame-Options: DENY`, an `X-Request-ID` on every response; CORS only for `WEB_ORIGIN` | `app/main.py` |
| REQ-UI-1 | Every student sidebar screen opens with an `<h1>` | AGENTS.md reachability audit |
| REQ-UI-2 | A form's submit is owned (the page never navigates to `?`) | AGENTS.md "SOMETHING MUST OWN EVERY `<form>`'s SUBMIT" |
| REQ-UI-3 | Below 900 px the sidebar is a drawer, and no screen scrolls sideways | AGENTS.md "The phone" |
| REQ-PERF-1 | Busy-hour load (50 concurrent students): p95 < 1000 ms, errors < 1 % | Test plan SLA |
| REQ-PERF-2 | 100 students signing in through a browser at once: ≥ 95 % succeed | Test plan |
| REQ-A11Y-1 | No critical WCAG 2.2 A/AA violation; sign-in works by keyboard alone | AGENTS.md accessibility notes, WCAG 2.2 |
| REQ-REL-1 | With the API down, the SPA shows a message and does not hang or blank | ISO 25010 fault tolerance |
| REQ-MAIL-1 | The Main Admin can read the mail log and ask about suppression at `/admin/mail` | AGENTS.md "AND THE OFFICE CAN NOW SEE ANY OF THIS" |

## 2. API test cases: Swagger / OpenAPI (TS-API-01, TS-API-02)

| Case ID | Title | Technique | Expected result | Req |
|---|---|---|---|---|
| API-01-001 | The OpenAPI document is valid | Specification review | `openapi` is 3.x and validates against the meta-schema | REQ-API-1 |
| API-01-002 | Every operation has a unique operationId | Specification review | No missing or duplicate ids | REQ-API-1 |
| API-01-003 | Every operation documents a 2xx | Specification review | No operation without a success response | REQ-API-1 |
| API-01-004 | Swagger UI and ReDoc are served | Smoke | `/docs` and `/redoc` return 200 with their UI | REQ-API-1 |
| API-01-005 | Inventory of paths and operations | Measurement | More than 100 operations are recorded | — |
| API-02-anon-* | Each parameter-free GET under `/api`, anonymous | Decision table (row: anonymous) | Never 5xx; a protected area answers 401 or 403 | REQ-API-2, REQ-RBAC-1 |
| API-02-owner-* | Each parameter-free GET under `/api/{student,mentor,admin}`, as its owner role | Contract / decision table | 200, and the body validates against the declared response schema (403 for an ungranted mentor capability and 404 for a student with no record count as correct) | REQ-API-2 |
| API-02-rbac-* | Each staff GET, as a student | Decision table (row: student) | Never 2xx, never 5xx | REQ-RBAC-1 |
| API-02-gov-* | Each governance GET, as a mentor | Decision table (row: mentor) | 401, 403 or 404 | REQ-RBAC-1 |
| API-05 | Schemathesis over every GET: public, student, admin | Syntax testing / property-based | No 5xx; status codes, content types and bodies conform to the document | REQ-API-2 |

## 3. API test cases: functional and security (TS-API-03, TS-API-04)

| Case ID | Title | Technique | Input | Expected result | Req |
|---|---|---|---|---|---|
| API-03-001 | A valid sign-in sets a hardened cookie | EP (valid) | Reserved student, correct password | 200; body role STUDENT; cookie `HttpOnly`, `SameSite=lax` | REQ-AUTH-1 |
| API-03-002 | Wrong password | EP (invalid) | Correct email, wrong password | 401 | REQ-AUTH-2 |
| API-03-003 | Unknown address and wrong password answer identically | EP + error guessing | Unknown email / known email, same wrong password | Both 401 with the same body | REQ-AUTH-2 |
| API-03-004 | Malformed bodies | EP (invalid structure) | `{}`, missing fields, nulls, non-JSON | 422, never 500 | REQ-API-2 |
| API-03-005 | Empty and oversized credentials | BVA | `""`/`""`; 5000-char email and password | 401 or 422 | REQ-API-2 |
| API-03-006 | `/auth/me` needs a session; an unsigned JWT is refused | Error guessing | No cookie; `alg=none` token | 401, 401 | REQ-AUTH-1 |
| API-03-007 | A second sign-in retires the first | State transition | Two sessions, one account | The first session gets 401 + `X-Reep-Session: retired` within the revocation-cache window; the second session works | REQ-AUTH-3 |
| API-03-008 | Sign-out ends the session | State transition | Log in, log out | That session gets 401 | REQ-AUTH-1 |
| API-03-009 | Brute force is throttled per account | BVA on the limit | 25 wrong attempts on one address | 429 arrives by attempt 10 × workers + 1; only 401 or 429 | REQ-AUTH-4 |
| API-03-010 | The password is never echoed | Error guessing | Near-miss password | Absent from the response | REQ-AUTH-1 |
| API-04-001 | Leave dates running backwards | BVA (to = from − 10) | from = +40 d, to = +30 d | 422 with a `detail` list | REQ-VAL-1 |
| API-04-002 | Leave reason boundaries | BVA (0, 2001) | `""`, 2001 × "x" | 422, 422 | REQ-VAL-1 |
| API-04-003 | Leave kind outside the closed list | EP (invalid) | `HOLIDAY` | 422 | REQ-VAL-1 |
| API-04-004 | A one-day leave (`from == to`) is accepted, then withdrawn | BVA (on the boundary) | from = to = +60 d | 200/201, listed in `mine`, withdrawn | REQ-VAL-1 |
| API-04-005 | Registration without its files | EP (invalid) | name + email only | 422 | REQ-VAL-2 |
| API-04-006 | Security headers everywhere | Checklist | Three paths | `nosniff`, `DENY` and `X-Request-ID` present | REQ-SEC-1 |
| API-04-007 | CORS refuses a foreign origin | Error guessing | `Origin: https://evil.example` preflight | Not echoed; not `*` | REQ-SEC-1 |
| API-04-008 | BOLA: a student reads another student | Error guessing (OWASP API1) | Another student's id on the 360, ledger and file paths | 401, 403 or 404 | REQ-RBAC-2 |
| API-04-009 | Injection strings are data | Error guessing | `' OR '1'='1`, `; DROP TABLE users;--` | No 5xx; the table is still there | REQ-SEC-1 |

## 4. Selenium test cases (TS-SEL-01: single instance, Page Object Model)

| Case ID | Title | Steps (summary) | Expected result | Req |
|---|---|---|---|---|
| SEL-001 | The login page renders | Open `/login` | ID, password (type=password) and Sign in are present; DOMContentLoaded < 10 s | REQ-UI-1 |
| SEL-002 | An empty submit is refused in place | Press Sign in with empty fields | Field errors are shown (role=alert); still on `/login` | REQ-UI-2 |
| SEL-003 | A wrong password shows the invalid-credentials alert | Reserved student + wrong password | "That email and password did not match an account…" | REQ-AUTH-2 |
| SEL-004 | A student signs in and lands home | Correct credentials | URL `/student`; h1 starts "Welcome back" | REQ-AUTH-1 |
| SEL-005 | Every student sidebar screen has a heading | Visit each nav link | Each shows an h1; none signs the student out | REQ-UI-1 |
| SEL-006 | The time ledger shows the day | Open `/student/time-log` | Heading, hours and day controls | REQ-UI-1 |
| SEL-007 | Sign-out, then Back does not re-enter | Sign out, then press browser Back | Stays on `/login` | REQ-AUTH-3 |
| SEL-008 | A deep link without a session goes to login | Open `/student/jobs` cold | Redirected to `/login` | REQ-RBAC-1 |
| SEL-009 | The register form refuses an empty submission in place | Submit an empty `/register` | Alert shown; URL has no `?` | REQ-UI-2, REQ-VAL-2 |
| SEL-010 | The Main Admin signs in through the admin door | Admin door, then admin credentials | `/admin` with an h1 | REQ-AUTH-1 |
| SEL-011 | A student cannot open an admin screen | Student opens `/admin/students` | Not left on `/admin/*` | REQ-RBAC-1 |
| SEL-012 | No sideways scroll on a phone (×2: `/login`, `/register`) | 390×844 mobile emulation | scrollWidth − clientWidth ≤ 1 | REQ-UI-3 |
| SEL-013 | The phone shell uses the drawer | Sign in at 390 px | Hamburger visible; no overflow | REQ-UI-3 |

### TS-SEL-02: concurrent browser instances

| Case ID | Title | Setup | Expected result | Req |
|---|---|---|---|---|
| SEL-P-001 | Single-instance baseline journey | 1 browser: login → home → jobs → time sheet | Passes; its timings are the baseline | REQ-PERF-2 |
| SEL-P-100 | 100 concurrent browser journeys | 100 headless Chromium, 100 different students | ≥ 95 % pass; timings recorded per step (ms) | REQ-PERF-2 |

## 5. Playwright non-functional test cases (TS-PW-NFR)

| Case ID | Title | ISO 25010 | Expected result | Req |
|---|---|---|---|---|
| NFR-PERF-01 ×2 | `/login`, `/register` cold load | Time behaviour | DCL < 4 s; LCP < 4 s (dev-server budget) | — |
| NFR-PERF-02 ×5 | Student screens: time to heading | Time behaviour | < 6 s | — |
| NFR-PERF-03 | SPA route changes | Time behaviour | Each < 3 s | — |
| NFR-A11Y-01 ×5 | axe WCAG 2.2 A/AA on public and student screens | Accessibility | No critical violation; serious ones are recorded | REQ-A11Y-1 |
| NFR-A11Y-02 | Keyboard-only sign-in | Accessibility | Tab/Enter reaches `/student` | REQ-A11Y-1 |
| NFR-RESP-01 ×12 | 360 / 768 / 1366 px × four screens | Adaptability | No horizontal overflow | REQ-UI-3 |
| NFR-REL-01 | Sign-in with the API unreachable | Fault tolerance | An alert is shown; still on `/login` | REQ-REL-1 |
| NFR-REL-02 | One endpoint returns 500 | Fault tolerance | The screen still renders its heading; no sign-out | REQ-REL-1 |
| NFR-SEC-01 | The session cookie is httpOnly | Confidentiality | Not in `document.cookie`; `httpOnly`, `Lax` | REQ-AUTH-1 |
| NFR-SEC-02 | Nothing sensitive in web storage | Confidentiality | No password or JWT in local or session storage | REQ-AUTH-1 |
| NFR-SEC-03 | Reflected script in a query string | Integrity | No dialog fires | REQ-SEC-1 |

## 6. Performance test cases (TS-PERF, JMeter)

| Case ID | Scenario | Pass criteria | Req |
|---|---|---|---|
| PERF-SMOKE | 1 VU, 30 s | 0 errors | — |
| PERF-LOAD | 50 VU, 3 min, 0.5–1 s think time | Errors < 1 %, overall p95 < 1000 ms | REQ-PERF-1 |
| PERF-STRESS | 100 VU, 3 min, no think time | Degrades gracefully: no 5xx storm, errors < 5 %, the system recovers | REQ-PERF-1 |
| PERF-SPIKE | 100 VU arriving within 2 s | Errors < 1 %; the logins complete | REQ-PERF-1 |
| PERF-SOAK | 30 VU, 10 min | Errors < 1 %; no upward drift in p95 between the first and last minute | REQ-PERF-1 |

## 7. Traceability matrix (requirements → cases)

| Req | Cases |
|---|---|
| REQ-AUTH-1 | API-03-001, 006, 008, 010; SEL-004, 010; NFR-SEC-01, 02; e2e TC-001.. (module 01) |
| REQ-AUTH-2 | API-03-002, 003; SEL-003 |
| REQ-AUTH-3 | API-03-007; SEL-007; backend `test_single_device_session.py` |
| REQ-AUTH-4 | API-03-009; backend `test_auth_rbac.py` |
| REQ-RBAC-1 | API-02-anon/rbac/gov-*; SEL-008, 011; backend `test_no_director_privilege.py` |
| REQ-RBAC-2 | API-04-008 |
| REQ-API-1 | API-01-001..004 |
| REQ-API-2 | API-02-*, API-03-004/005, API-05 (Schemathesis) |
| REQ-VAL-1 | API-04-001..004 |
| REQ-VAL-2 | API-04-005; SEL-009 |
| REQ-SEC-1 | API-04-006, 007, 009; NFR-SEC-03 |
| REQ-UI-1 | SEL-001, 005, 006 |
| REQ-UI-2 | SEL-002, 009 |
| REQ-UI-3 | SEL-012, 013; NFR-RESP-01 |
| REQ-PERF-1 | PERF-LOAD, STRESS, SPIKE, SOAK |
| REQ-PERF-2 | SEL-P-001, SEL-P-100 |
| REQ-A11Y-1 | NFR-A11Y-01, 02 |
| REQ-REL-1 | NFR-REL-01, 02 |
| REQ-MAIL-1 | API-02-owner `/api/admin/mail-log` (found DEF-001); backend `test_admin_mail_log.py` |
