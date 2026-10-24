# REEP manual test cases

This is the index of REEP's manual test cases. The cases themselves live in
one file per module under [`cases/`](cases/), and every automated case has a
twin Playwright test under [`tests/`](../tests/).

| Application under test | REEP web app: the Angular SPA and the FastAPI API behind it |
|---|---|
| Environment | Local development: `npx ng serve` on http://localhost:4200, API on port 3300 with `ENV=dev`, dev seed applied |
| Results | `manual-test-results.csv` at the repository root, written by every `npm run test:e2e` |

## Modules

| # | Module | Cases | Automated tests | IDs | Screens |
|---|---|---|---|---|---|
| 01 | Authentication and access | [cases/01-authentication.md](cases/01-authentication.md) | `tests/auth-sync.spec.ts` | TC-001 to TC-099 | `/login`, sign-out, each role's landing page, route guards, `/account`, `/account/password`, `/activate`, `/reset` |
| 02 | Registration and onboarding | [cases/02-registration.md](cases/02-registration.md) | `tests/02-registration.spec.ts` | TC-100 to TC-199 | `/register`, `/admin/registrations`, `/onboard` |
| 03 | Student: home and progress | [cases/03-student-progress.md](cases/03-student-progress.md) | `tests/03-student-progress.spec.ts` | TC-200 to TC-299 | `/student`, `/student/skilling`, `/student/time-log`, `/student/courses`, `/student/records`, `/student/leaderboards` |
| 04 | Student: profile, jobs and tools | [cases/04-student-tools.md](cases/04-student-tools.md) | `tests/04-student-tools.spec.ts` | TC-300 to TC-399 | `/student/profile`, `/student/uploads`, `/student/resume`, `/student/jobs`, `/student/english`, `/student/mentor-log`, `/student/interviews`, `/student/assistant`, `/student/agent` |
| 05 | Faculty and alumni | [cases/05-faculty-alumni.md](cases/05-faculty-alumni.md) | `tests/05-faculty-alumni.spec.ts` | TC-400 to TC-499 | `/mentor/notebook`, `/mentor/mentees`, `/mentor/verifications`, `/mentor/upskilling`, `/mentor/signature`, `/mentor/leave`, `/mentor/agent`, `/alumni`, `/alumni/jobs` |
| 06 | Admin: people and access | [cases/06-admin-people.md](cases/06-admin-people.md) | `tests/06-admin-people.spec.ts` | TC-500 to TC-599 | `/admin`, `/admin/students`, `/admin/students/:id`, `/admin/faculty`, `/admin/faculty/new`, `/admin/mentors`, `/admin/governance`, `/admin/governance/features`, `/admin/audit` |
| 07 | Admin: daily operations | [cases/07-admin-operations.md](cases/07-admin-operations.md) | `tests/07-admin-operations.spec.ts` | TC-600 to TC-699 | `/admin/analytics`, `/admin/leave-approvals`, `/admin/jobs`, `/admin/placement`, `/admin/exports`, `/admin/imports`, `/admin/swoc`, `/admin/agent` |
| 08 | Admin: college setup and interviews | [cases/08-admin-setup.md](cases/08-admin-setup.md) | `tests/08-admin-setup.spec.ts` | TC-700 to TC-799 | `/admin/colleges`, `/admin/setup`, `/admin/institution`, `/admin/catalogue`, `/admin/interviews`, `/admin/interview-questions` |

## How the manual and automated suites stay linked

1. **Every case has a permanent ID, `TC-NNN`,** inside its module's range. An
   ID is never reused or renumbered, even when its case is retired.
2. **The automated test for a case carries the ID as a tag in its `test()`
   title**, for example `... @TC-002`. To run one case:
   `npx playwright test --grep @TC-002`. To run one module:
   `npx playwright test tests/auth-sync.spec.ts`. The module specs are
   numbered so a full run takes them in module order, with
   `tests/auth-sync.spec.ts` last; module 04 runs before module 08 on purpose,
   because module 08 gives the seeded student an interview record that module
   04's "before any interview" cases need to be absent.
3. **The automated test's `test.step()` titles are the case's steps, word for
   word**, numbered the same way, and every assertion is labelled with the
   expected result it checks (`TC-001 ER-4: ...`). A failure in the HTML report
   or the CSV names the step and the expected result that failed.
4. **`manual-test-results.csv` has a row for every case in `cases/`**,
   carrying the result of the test tagged with its ID. A second test with the
   same tag, or a second browser project, adds a row. A case whose "Automated
   test" field names no tag is listed as `Not automated`. A case whose test
   was left out of the run (by `--grep`, for example) is listed as `Not run`.
   The run fails, and the case's "Sync Problems" cell says why, when a test's
   title has no `@TC-NNN` tag or names a case missing from `cases/`, when its
   steps differ from the case's steps, when two cases share an ID, or when a
   full run has no test for a case whose field names one.
5. **A change to a case's steps or expected results changes its automated test
   in the same commit, and the other way round.**

## What each status in the CSV means

| Status | Meaning |
|---|---|
| Passed | The automated test ran and every expected result held. |
| Failed | An expected result did not hold. "Failed Step" and "Error" say which. |
| Blocked | A pre-condition did not hold (the API was down, the seed was missing, an account was locked). The run still fails. |
| Known failure | The test is marked `test.fail()` for a known bug, and failed as expected. |
| Skipped | The test did not run, and "Error" says why: an optional feature is switched off on this server (Google sign-in), or the test data it needs was used up by earlier runs (open ledger days, a student who has never taken an interview). Reset the database to run it again. |
| Did not run | Playwright never started it, because a hook, a serial sibling or the worker failed first. |
| Not run | No test for the case was in this run. |
| Not automated | The case is run by hand only. Its "Automated test" field says why. |

"Manual-only Checks" lists the expected results marked **Manual only.** in a
case: the ones a browser test cannot observe, such as whether an email
arrived. Check those by hand, even when the row says Passed.

## Setup

These steps prepare the environment once. Each case's own pre-conditions
list what must be true before that case starts.

1. Start the database: `docker compose up -d`.
2. From `apps/api-py`, apply migrations and seed the dev accounts:
   `python -m alembic upgrade head`, then `python -m app.seed`.
3. Start the API with a development `ENV`:
   `python -m uvicorn app.main:app --port 3300`.
4. From `apps/web`, start the web app: `npx ng serve`.

To run the automated suite, from the repository root: `npm ci`, then
`npx playwright install chromium` once, then `npm run test:e2e`. Playwright
starts the web app itself when nothing is serving port 4200; the API must
already be running. Run the suite on its own: REEP keeps one live session per
account, and the tests sign in as the seeded accounts, so a pytest run or a
browser signed in as the same account at the same time signs the tests out.

## Seeded accounts

The dev seed creates these accounts. It refuses to run when `ENV=prod`, so
these passwords never exist on a production server.

| Role | Email | Password | Name | Lands on |
|---|---|---|---|---|
| Student | `student@bgscet.ac.in` | `student123` | Test Student | `/student` |
| Faculty (MENTOR) | `mentor@bgscet.ac.in` | `mentor123` | Test Mentor | `/mentor/notebook` |
| Alumni | `alumni@bgscet.ac.in` | `alumni123` | Test Alumnus | `/alumni` |
| Main Admin | `admin@bgscet.ac.in` | `admin123` | Main Admin (seed) | `/admin` |

## Writing a case

Copy the layout of an existing case in [cases/01-authentication.md](cases/01-authentication.md):

- a `## TC-NNN — Title` heading;
- a field table whose "Automated test" row names the spec file and the tag,
  or says `None (manual only)` and why;
- **Pre-conditions**, **Test data**, numbered **Steps**, and an **Expected
  results** table with `ER-n`, the step it is checked after, and the result.
  Start a result with **Manual only.** when a browser test cannot observe it;
- **Post-conditions**, when the case leaves something changed.

A case must be repeatable on the same database: a case that creates something
uses a name unique to the run, and a case that changes seeded data puts it
back, or says in its post-conditions what it leaves behind.
