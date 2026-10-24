# 05 — Incident Reports

| | |
|---|---|
| Standard followed | ISO/IEC/IEEE 29119-3:2021 §11 *Test Incident Report* (IEEE 829's "anomaly report") |
| Cycle | 2026-09-29, build `main@1685e67` |
| Severity scale | **Critical**: data loss or a security breach · **Major**: a feature unusable, no workaround · **Medium**: wrong behaviour with a workaround, or a 500 reachable from a normal client · **Low**: cosmetic, a documentation or contract mismatch |
| Priority scale | P1 fix now · P2 next release · P3 when convenient |

Incidents are in four groups. **DEF** is a product defect. **OBS** is an
observation that is not a defect but that a reader of the numbers needs.
**TW** is a defect in the testware or test environment. Each TW is recorded
because it produced misleading results before it was caught, which is the
lesson worth keeping.

## Product defects

### DEF-001: The whole "Email delivery" screen answers 422 to everyone · Major · P1 · **FIXED on this branch**

| Field | Value |
|---|---|
| Found by | API-02-owner `/api/admin/mail-log` (Swagger contract suite), and API-02-anon on the same path (anonymous got 422, not 401) |
| Endpoints | `GET /api/admin/mail-log`, `GET /api/admin/mail-log/suppression`, `DELETE /api/admin/mail-log/suppression` |
| Steps | Sign in as the Main Admin → `GET /api/admin/mail-log` |
| Expected | 200 with the log (the office's only view of `mail_logs`) |
| Actual | `422 {"detail":[{"loc":["body"],"msg":"Field required"}]}` for the admin, a mentor, a student and an anonymous caller alike |
| Root cause | `routers/admin_mail.py` passed `require_admin`, a plain function `require_admin(session: dict)`, straight to `Depends(...)`. FastAPI reads its un-annotated `session: dict` parameter as a **required JSON request body**. Validation fails before any authentication runs. A browser `fetch` GET sends no body, so `/admin/mail` could never load a row. No test called these endpoints. |
| Impact | The feature AGENTS.md describes as the answer to "the code never came" (the mail log plus the SES suppression lookup and lift) has been unusable since it shipped |
| Fix | A `_office` dependency (`Depends(get_current_session)` → `require_admin`) used by all three endpoints |
| Confirmation | The live API returns 422 before the fix and 200 after it (`mail-log after fix: 200`). The new `apps/api-py/tests/test_admin_mail_log.py` **fails on the old code and passes on the fix**. The codebase guards (`test_codebase_guards.py`, `test_capability_enforcement.py`) are still green |

### DEF-002: `PUT /api/student/profile` answers 500 when a boolean is sent as `null` · Medium · P2 · **FIXED 2026-09-30**

| Field | Value |
|---|---|
| **Fix (2026-09-30)** | `update_profile` refuses `null` on any column the table marks NOT NULL, with a 422 naming the field. Regression test: `apps/api-py/tests/test_input_hardening.py` |
| Found by | Schemathesis (run 1) |
| Reproduce | `PUT /api/student/profile` as a student, body `{"interested_in_jobs": null}` (also `interested_in_internships`, `leaderboard_opt_out`) |
| Expected | 422 (or the field ignored) |
| Actual | 500. `IntegrityError: NotNullViolation: null value in column "interested_in_jobs" of relation "student_profiles"`. There were 120 occurrences in one run |
| Root cause | The Pydantic input model declares these fields `bool \| None` and the handler copies `None` onto NOT NULL columns |
| Suggested fix | Skip `None` for the NOT NULL booleans (as `exclude_none` would), or type them `bool` with a default |

### DEF-003: `POST /api/student/checkin` answers 500 for an unknown course code · Medium · P2 · **FIXED 2026-09-30**

| Field | Value |
|---|---|
| **Fix (2026-09-30)** | `check_in` looks the course up first and answers 422 naming the code. Regression test: `apps/api-py/tests/test_input_hardening.py` |
| Reproduce | As a student: `{"course_code": "", "module": ""}` |
| Actual | 500. `ForeignKeyViolation ... "lab_sessions_course_code_fkey"` (14 occurrences) |
| Expected | 404 or 422 naming the course |
| Suggested fix | Look the course up first and refuse it in words |

### DEF-004: `POST /api/admin/jobs` and `POST /api/admin/criteria` answer 500 for an empty or unknown `course_id` · Medium · P2 · **FIXED 2026-09-30**

| Field | Value |
|---|---|
| **Fix (2026-09-30)** | `JobIn` and `CriteriaIn` turn a blank id into `None` (`_blank_is_none`), so the existing existence checks run. Regression test: `apps/api-py/tests/test_input_hardening.py` |
| Reproduce | As the Main Admin: `POST /api/admin/jobs` with `"course_id": ""` (the same for `/api/admin/criteria`) |
| Actual | 500. `ForeignKeyViolation "fk_jobs_course"` / `"fk_placement_criteria_course"` |
| Expected | 422, like the handler's other checks (for example, the `apply_url` check) |
| Note | An empty string is exactly what an HTML `<select>` with a blank option posts, so a real client can reach this |
| Suggested fix | Treat `""` as `None`, and resolve the id through the existing spine resolver before insert |

### DEF-005: `PATCH /api/admin/swoc/entries/{id}` answers 500 for an unknown `linked_*_id` · Medium · P2 · **FIXED 2026-09-30**

| Field | Value |
|---|---|
| **Fix (2026-09-30)** | `_Linkable` turns a blank link into `None`; it is still in `model_fields_set`, so a blank PATCH still removes the link. Regression test: `apps/api-py/tests/test_input_hardening.py` |
| Reproduce | As the Main Admin: `{"linked_job_id": ""}` (or `linked_skill_id` / `linked_session_id`) |
| Actual | 500. `ForeignKeyViolation "fk_swoc_linked_job"` (and the same for skill and session) |
| Expected | 422 |

### DEF-006: GET list endpoints answer 500 for out-of-range integers in the query string · Medium · P2 · **FIXED 2026-09-30**

| Field | Value |
|---|---|
| **Fix (2026-09-30)** | `page` bounded at `MAX_PAGE` (100,000) on audit, SWOC and mentor-load; SWOC `semester` 1–20; placement `year` 1900–9999. Regression test: `apps/api-py/tests/test_input_hardening.py` |
| Found by | Schemathesis run 2 (**GET only**, so reachable by a read-only admin screen or a crafted URL) |
| Reproduce | `GET /api/admin/audit?page=92917030724915670548480` · `GET /api/admin/swoc?semester=-954555328274038435872768&page=3214` · `GET /api/admin/placement?year=-5466912982786` |
| Actual | 500. `DataError: NumericValueOutOfRange: integer out of range` / `bigint out of range` |
| Expected | 422 from the query schema |
| Suggested fix | Bound the `Query(...)` parameters (`ge=1, le=…`) as `/admin/mail-log`'s `limit` already is |

### DEF-007: Text containing a NUL byte or an invalid `\u` escape answers 500 · Low · P3 · **FIXED 2026-09-30**

| Field | Value |
|---|---|
| **Fix (2026-09-30)** | one `DataError` handler in `app/main.py` answers 422 and logs only the error class and path, never the value. Regression test: `apps/api-py/tests/test_input_hardening.py` |
| Endpoints seen | `POST /api/admin/catalogue/copy`, `POST /api/admin/catalogue/subjects/import` |
| Actual | 500. `DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes`, and `UntranslatableCharacter: unsupported Unicode escape sequence` |
| Expected | 422 |
| Suggested fix | One shared validator that rejects `\x00` in free text, applied in the input models or the import parser |

### DEF-008: Many 422 responses break the documented error schema · Low · P3 · **FIXED 2026-09-30**

| Field | Value |
|---|---|
| **Fix (2026-09-30)** | the published `HTTPValidationError.detail` is `anyOf [array, string]`, which is what the API sends. Regression test: `apps/api-py/tests/test_input_hardening.py` |
| Found by | Schemathesis `response_schema_conformance`: 41 violations in run 1 and 6 in run 2 |
| Example | `GET /api/student/leaderboards?board=__main__` → `422 {"detail": "Unknown board. One of: …"}`, while the document declares 422 as `HTTPValidationError` whose `detail` is an **array** |
| Impact | A client generated from the Swagger document (or the SPA's `detailOf` helper) must handle two shapes. The SPA already does, which is why this is Low |
| Suggested fix | Either raise these as 400 (a business-rule refusal, not schema validation), or declare `responses={422: {"model": ErrorOut}}` on those routes |

### DEF-009: With the API unreachable, the login screen says "This server signs in with Google only" · Low · P3 · **FIXED 2026-09-30**

| Field | Value |
|---|---|
| **Fix (2026-09-30)** | the login probe has a `probeFailed` state with its own sentence and a Try again button; NFR-REL-01b is no longer a known failure. Regression test: `testing/playwright/responsive-resilience-security.nfr.ts` NFR-REL-01b |
| Found by | NFR-REL-01 (Playwright, `page.route('**/api/**', abort)`). Kept as the **known failure** NFR-REL-01b (`test.fail`, per the repository's convention) |
| Steps | Make `/api` unreachable (a network drop, an API restart, a deploy) → open `/login` |
| Expected | The sign-in screen renders, **and** says the server could not be reached (or says nothing about which doors exist) |
| Actual | The screen renders, but the password form is hidden and the copy reads *"This server signs in with Google only. A REEP password, when one is issued to you, comes from the placement cell."* (screenshot: `results/playwright-nfr/…/test-failed-1.png`, first run) |
| Root cause | `LoginComponent.probe()` fails **closed** for the password form when `/api/auth/sso/status` cannot be fetched. That is deliberate, and it is right. But the template's `@else` branch uses the copy for "the server told us passwords are off", so "we could not ask" is printed as a fact about the server. That is the `X-Reep-Scope` rule in AGENTS.md ("we asked and it is fine" and "we could not ask" are opposite facts) applied to the login screen |
| Impact | During an outage or a deploy, a student with a password is told they do not have one and is sent to the placement cell |
| Suggested fix | A third state for the probe, `unknown`, with its own sentence ("We could not reach REEP just now — try again in a minute") |

### DEF-010: Text below the WCAG 2.2 AA contrast ratio on the login and student screens · Medium · P2 · **FIXED 2026-09-30**

| Field | Value |
|---|---|
| **Fix (2026-09-30)** | `--faint` `#67637c` (4.65:1 on the wash), `--good` `#107045` (4.96:1), login helper text `#716484` (5.44:1), the SWOC empty note uses `--faint` instead of opacity; axe now gates `color-contrast`. Regression test: `testing/playwright/accessibility.nfr.ts` NFR-A11Y-01 |
| Found by | NFR-A11Y-01 (axe-core 4.13, rule `color-contrast`, impact **serious**). No *critical* violation anywhere, so the gate passed. `/register` had none at all |
| Where (element · foreground/background · ratio, need ≥ 4.5:1) | `/login` `.google__sub`, `.or > span`, `.field__help` · `#a596b3` on `#ffffff` · **2.75** · `/student`, `/student/jobs`, `/student/time-log` sidebar `.sec-label` · `#7c7891` on `#ece4f5` · **3.42** · `/student` `.swoc-empty` · `#8a8894` on `#fff` · **3.48** · `/student` header `.chip.good` · `#137a4a` on `#ece4f5` · **4.34** |
| Impact | Low-vision users (WCAG 1.4.3 Contrast (Minimum), Level AA). The sidebar labels appear on every student screen |
| Suggested fix | Darken the muted-ink tokens in `reep-v2.scss` (for example, `#a596b3` → about `#76688a` reaches 4.5:1 on white) and re-run `testing/playwright/accessibility.nfr.ts` |

## Observations (not defects)

| ID | Observation | Evidence | Why it matters |
|---|---|---|---|
| OBS-001 | With 2 API workers, a session retired by a newer sign-in keeps working on the **other** worker for up to `AUTH_REVOCATION_CACHE_SECONDS` (60 s) | API-03-007 measured **60.4 s** | This is by design (a per-process cache that saves one query per request). But "one device at a time" is only eventually consistent, within 60 s. The test asserts the window, not immediacy |
| OBS-002 | Sign-in is the one CPU-heavy request: scrypt (N=16384) per login | Login mean 51 ms at 50 VUs; **1,884 ms mean at a 100-user spike** (all other endpoints stayed at a 73 ms median) | On a results-day spike, logins queue behind each other on the workers' CPU. That argues for autoscaling on CPU and for keeping sessions long, not for a weaker hash |
| OBS-003 | FastAPI does not add 401 or 403 to the OpenAPI document | Schemathesis "Undocumented HTTP status code" (≈250 per admin run), almost all 401 or 403 | The contract is incomplete, not wrong. A global `responses={401: …, 403: …}` would make it complete |
| OBS-005 | Every in-process limiter and cache multiplies by the number of API processes. That covers the reset-link limit (`_forgot_per_address` in `routers/passwords.py`), the login limiter, and the revocation cache | e2e TC-038 ("the fourth reset request within the hour is refused") **failed with 2 workers and passed with 1**. TC-022 and TC-025 (the older device is told at once that it was signed out) behaved the same way | Production runs `apiMinTasks = 2` or more on Fargate, so a student there gets about 3 × tasks reset mails per hour, and the "signed out elsewhere" note can lag by up to 60 s. AGENTS.md already says this for the login limiter. The e2e suite assumes one process, which is the documented dev setup; a note in `tests/support/reep.ts`'s pre-check would stop the next person hitting it |
| OBS-004 | Sign-in happily accepts a 5,000-character email and password (401, no 422) | API-03-005 | Harmless (the password is not hashed before lookup fails), but a length bound would be cheap defence in depth |

## Testware and environment incidents

| ID | What happened | Effect before it was caught | Fix |
|---|---|---|---|
| TW-001 | The JMeter plan fed accounts from a CSV Data Set. JMeter hands out a **new line on every iteration**, so later virtual users signed in as accounts earlier ones were still using, and REEP's one-session rule retired them | The first stress run reported **44.9 % errors** (all 401). That was a scripting fault, not a capacity result | Virtual user N signs in as `loadtestNNN` (a JSR223 pre-processor, deterministic). All scenarios were re-run on a freshly restarted API: **0 HTTP errors** |
| TW-002 | The API suite ran during the first load run and signed in as two of JMeter's accounts | 810 401s in that load run | Disjoint account pools: 101–110 are reserved and never in the CSV. The load run was repeated |
| TW-003 | Schemathesis 4.28 filter semantics: include filters of different kinds are OR'ed and exclude filters are **AND'ed**, and `--exclude-method-regex` is case-sensitive against lower-case method names | Two runs meant to be GET-only also fuzzed POST/PUT/PATCH/DELETE against the **local dev database** (≈5,700 writes). That is how DEF-002..005 and 007 were found | One case-insensitive method exclude, path exclusions moved into a lookahead on the include, no stateful phase. Verified by the API log: **0 writes** besides the two logins. The dev database was dropped and rebuilt before any later suite ran. It was never a shared or production database |
| TW-004 | Selenium: one of 100 journeys hit `StaleElementReferenceException` (Angular re-rendered the login form between find and type) | 99/100 | `LoginPage.sign_in` re-finds and retries on a stale element |
| TW-005 | `apps/api-py/.env` copied verbatim from `.env.example` has inline `# comments` after bare values, and pydantic-settings reads them as the value (for example, an AWS region of `# falls back to …`) | 41 backend tests failed on the first run, all in mail sending | Use only the quoted `KEY="value"` lines (or no `.env` at all, as CI does). Worth a follow-up in `.env.example` |
| TW-006 | 100 Chromium instances **alive at the same moment** (`--barrier`) do not fit the 16 GB test bed: 14.9 GB and ≈800 Chrome processes with default flags, and 13.6 GB and 639 processes with `LIGHT_CHROME=1` before the watchdog stopped it | Two all-at-once runs were stopped before the out-of-memory killer reached Postgres. The API and DB were checked healthy after each | 100 instances are run with 100 concurrent threads and a serialised launch (100/100 passed), and **50 truly simultaneous** browsers run with `--barrier` (46/50; the 4 timeouts came from host CPU saturation, load average 128 on 4 vCPU, while the API's own p50 stayed at 10 ms). Genuinely simultaneous 100: `selenium/grid/docker-compose.yml` on a larger host |
| TW-008 | The e2e suite ran on the database that also held the 110 load-test students, against an API with 2 workers | 14 of 256 e2e cases failed. 11 were in the admin roster (TC-503…514, 529): the ag-grid virtualises its rows and the seeded student was no longer in the rendered window. 3 were in auth (TC-022, 025, 038, per OBS-005) | The same 14 re-run on a fresh `reep_e2e` database (migrate + seed only) with the documented single-process API: **14/14 passed** (`results/manual-test-results-rerun-14.csv`). Lesson: give load-test data its own database, as AGENTS.md already asks of concurrent pytest runs |
| TW-007 | The Selenium runner read the student home's `<h1>` as soon as one was visible. That is the LOADING placeholder, "Landing", so 4/100 journeys "failed" on a heading that was about to become "Welcome back" | 96/100, then 98/100 (a stale read inside the new wait) | `BasePage.student_home_outcome()` waits for the terminal state (data or error), tolerates the `@switch` swapping the node, and counts the error state as a real failure: **100/100** |
