# Negative-scenario test: 10 colleges, different branches

**Date:** 2026-10-13 · **Build:** `main` at `dedc2b8` · **Stack:** local dev (PostgreSQL 16 + pgvector, API on :3300, `ENV=dev`, dev seed)
**Harnesses:** [`negative_multi_college.py`](../api/negative_multi_college.py) (round 1) and [`data_isolation_multi_college.py`](../api/data_isolation_multi_college.py) (round 2), both in `testing/api/` · **Raw results:** `testing/results/api/negative-multi-college-results.json` and `data-isolation-results.json`

## What was run

10 colleges, each with its own set of branches (departments), one course, one specialization and one batch per branch:

| College | Branches |
|---|---|
| C1 Alpha Institute of Technology | CSE, ECE, MECH |
| C2 Beta Engineering College | CIVIL, EEE |
| C3 Gamma College of Engineering | ISE, AIML, CSE |
| C4 Delta School of Management | MBA |
| C5 Epsilon Polytechnic | MECH, AUTO, CHEM |
| C6 Zeta Institute | MCA, CSE |
| C7 Eta Women's College | ECE, BT, EEE, CSE |
| C8 Theta Technical University | AERO, MECH |
| C9 Iota College | CSDS, CSBS |
| C10 Kappa Institute of Science | PHY, MATHS, CHEM |

That is 110 setup calls (all passed), then **166 negative probes**. Each probe has an expected answer; any 5xx counts as a failure. **144 passed and 22 failed.** The 22 failures come from **6 distinct bugs**. No probe returned a 5xx.

## Bugs found, worst first

### BUG-1 (high): a student can be moved into another college's batch, and keeps their old college's mentor

The rule that a mentor must be in the student's own college (B1.5, `_assert_same_college`) is checked only when a mentor is **assigned**. It is not checked when the **student** moves. Three endpoints move a student into another college's batch without any check:

| Endpoint | Got | Expected |
|---|---|---|
| `PUT /api/admin/students/{id}/cohort` (`routers/admin.py:1633`) | 204 | 422 |
| `PATCH /api/admin/students/{id}` with `cohort_id` (`routers/admin_students.py:503`) | 200 | 422 |
| `POST /api/admin/cohorts/{id}/students/bulk` with `move` (`routers/admin_students.py:659`) | 200 | 422 |

Steps to reproduce: approve a C1/CSE applicant, assign them a C1 faculty member, then move them into C2/CIVIL's batch. The database then shows a student seated in college C2 while a C1 faculty account is their mentor. Under rule 2, that faculty member keeps reading the C2 student's marks, attendance and USN. That is exactly what B1.5 exists to refuse.

Possible fix: when a move changes the student's college, either refuse it while the student has a mentor from another college, or release that mentor (with `mentor_history` recording why). Apply the same check to all three endpoints.

### BUG-2 (high): applications are accepted for an archived college or an archived branch

`GET /api/register/hierarchy` correctly lists only ACTIVE colleges, departments, courses and specializations. But `POST /api/register` checks the ids only with `db.get(...)` (`load()` in `_resolve_claim`, `routers/registration.py:1654`) and never looks at `status`. So:

* applying to an archived college returns 201 and the application lands in the review queue;
* applying to an archived department (branch) also returns 201.

Anyone holding an old form, a cached page, or a crafted request can apply to a college the office has closed. Approving that application then seats a student there. Fix: in `load()`, refuse any row whose `status` is not ACTIVE, with the same 422 wording ("Reload the form and choose again").

### BUG-3 (medium): blank names and codes are saved everywhere on the institution tree

Every create endpoint checks `min_length=1` first and **only then** strips the value (`code = body.code.strip().upper()` at `routers/admin.py:245, 760, 944, 1088, 1447`; names are stripped the same way). So `"   "` passes validation and is stored as `""`. PATCH does not strip at all, so `"   "` is stored as-is.

| Probe | Got | Expected |
|---|---|---|
| college: code `"   "` | 201 the first time (row with code `""`), then 409 "A college with code  already exists." | 422 |
| college: name `"   "` | 201, name `""` | 422 |
| college: PATCH name `"   "` | 200, name `"   "` | 422 |
| college: PATCH code `"   "` | 409 "code  already exists" | 422 |
| department: code / name `"  "` | 201, stored `""` | 422 |
| department: PATCH code `"   "` | 409 "department with code ." | 422 |
| course: code `"  "` | 201, code `""` | 422 |
| specialization: code `"   "` | 201, code `""` | 422 |
| batch: code `"   "` | 409 (a `""` batch already exists) | 422 |
| batch: name `"   "`, and PATCH name `"   "` | 201 / 200 | 422 |
| **public `/register`: name `"    "`** | 201, applicant named `""` | 422 |

What users see: blank entries in the public application form's pickers (8 of the 51 colleges served by `/register/hierarchy` had no name after this run), a review queue row with no applicant name, and a confusing 409 that names an empty code. Fix: strip first, then check length, using a validator (`RegisterIn` already does this for `usn`, `phone`, `personal_email` and `linkedin_url`, but not for `name`).

### BUG-4 (medium): a college's email domain list takes any text

`POST /api/admin/colleges` with `email_domains: ["not a domain", "http://x.com/", "@@", "gmail.com"]` returns 201. `normalise_domain` only lowercases and strips `@`. Two problems follow:

* entries like `not a domain` and `http://x.com/` can never match an address, so they do nothing and give no error;
* **`gmail.com` is accepted as a college domain.** Faculty creation and student provisioning both rely on this list. Listing `gmail.com` means any Gmail address passes the "is this a college address" fence for that college.

Fix: refuse anything that is not a bare hostname, and refuse the public-mail domains that `/register` already refuses in its college-email box.

### BUG-5 (low): phone number takes any text

`/register` with `phone="call me maybe"` returns 201. The phone number is copied onto the student profile at approval. Fix: accept only digits, spaces, `+`, `-` and brackets, with a sensible digit count.

### BUG-6 (low, consequence of BUG-3): blank colleges show in the public form

This is the same root cause as BUG-3, listed separately because it is the first thing an applicant would notice: empty options appear in the College and Department pickers on `/register`.

## What held up (passed)

* **Access control:** an anonymous user, a student and a mentor are all refused every admin write (401/403). A forged `reep_session` cookie and an `alg=none` cookie are both refused.
* **Duplicates:** duplicate college, department, course, specialization and batch codes are refused, case-insensitively and with padding. The same department code in two different colleges is allowed, which is correct.
* **Batches:** a batch whose end date is on or before its start date is refused. So are a specialization or course from another college or department, unknown ids, an invalid date, and an unknown degree level.
* **Courses:** a duration of 0, -12 or 121 months is refused.
* **Registration:** missing CV or photo, text renamed to `.pdf`, an empty file, a PDF passed as the photo, HTML passed as a PDF, and an 11 MB CV (the limit is 10 MB) are all refused.
* **Registration across colleges:** a branch, course, specialization or batch from another college is refused, as is a nonexistent college.
* **Registration fields:** a Gmail address as the college email, the same address in both email boxes, an Instagram or `javascript:` LinkedIn URL, and three distinct specializations (the limit is 2) are all refused.
* **Repeat applications:** a duplicate application is refused, including in upper case. Re-applying after a rejection is allowed, which is correct.
* **Review decisions:** a rejection without a reason, or with a whitespace-only reason, is refused. So are an unknown decision, a decision by a student or a mentor, a second approve, a reject after approval, and a hold after approval.
* **Faculty:** an address outside the college's domains, another college's domain, an outside address without a reason, a blank name, a malformed address, a missing or unknown department, and an address already used by a student or mentor account are all refused.
* **Mentors:** assigning another college's faculty member is refused, as are a missing or whitespace-only reason and a student or admin account given as the mentor.
* **Deleting a college with people under it** is refused and names what is still attached. A wrong code, a short code, a missing reason, and a non-admin caller are all refused. `POST` and `DELETE` on `/admin/students` return 405.
* **Leave:** dates that run backwards, or a missing reason, are refused.

## Round 2: data isolation between individuals, storage and logs

### Data isolation: 155 of 155 probes passed

The setup has two colleges. College X has students A and B, faculty member FX (A's mentor) and faculty member FZ (mentors nobody). College Y has student C and faculty member FY (C's mentor). The seeded alumnus and an anonymous caller are added as further outsiders. A first creates real records: an upload, a resume version and a leave request. FX writes a meeting note about A. Every other account then tries to read, change or delete those records by id.

* **Students (B in the same college, C in the other college), the alumnus and an anonymous caller** were all refused (401/403/404) on every one of these:
  * A's upload file, and deleting it;
  * A's resume: read, PDF, rename, regenerate and delete;
  * A's leave: paper, attachments, cancel, decide and balance;
  * A's application CV and photo;
  * all nine `/mentor/students/A/*` views, and `/admin/students/A/360`.
* **List endpoints do not leak.** B's upload, resume and leave lists do not contain A's records. Neither college's leaderboard shows the other college's student.
* **Faculty from the other college (FY), and faculty with no mentees (FZ),** were refused (403/404) on every one of these:
  * all ten mentee views and the notebook;
  * writing a note, and deleting FX's note;
  * reading or reviewing A's upload;
  * awarding a badge and recording an assessment;
  * A's leave paper, the 360 view and the application CV.

  A is not in either mentee list.
* **Positive controls passed.** A reads their own records, FX reads their mentee A, and FY sees their own mentee C. So the refusals above are real fence decisions and not a broken setup.
* **A student cannot open staff screens.** All five tried were refused.
* **One device per account.** Signing in again retires the older session (401, with `X-Reep-Session: retired`).

The four write probes that first came back as 422 were resent with valid bodies, so they reached the permission check. All four were then refused by it.

### Storage: consistent

* **Files on disk match the database.** Every file in `var/uploads` has a random `uuid4().hex` name with no personal data in it, and is mode 644 in one flat folder. Every live row in the six document tables (130) has its file, and none is missing.
* **The document archive index (`archived_documents`) is kept up to date.** Every file saved through the API has a row in it. The only 3 live rows without one are seed fixtures (`up_*.pdf`), which the seed writes directly; this is by design, since the index has no backfill.
* **Approval re-owns application files correctly.** 17 approvals re-owned exactly 34 files (CV and photo each) to the new student (`STUDENT_UPLOAD`, `owner_id` = the student). The 90 rows with no owner all belong to applications that are still pending, held or rejected, which is correct.
* **The 15 files on disk with no row** came from the pytest run. Its database was separate (`reep_test`), but it wrote into the same `var/uploads` folder and left its files there. That is a test-hygiene issue, not a product bug: tests should point `UPLOAD_DIR` at a temporary folder.

### Bugs found in round 2

#### BUG-7 (medium): a second access log records full query strings, including Google sign-in's `code` and `state`

`app/traceability.py`'s `reep.access` log deliberately leaves out the query string, the body and the cookie. However, uvicorn's own access log is still on: the Dockerfile `CMD` has no `--no-access-log`. Every request is therefore logged twice, and the uvicorn copy has the full URL. A request to `/api/register/hierarchy?token=SECRETTOKEN456` appeared in the log verbatim. `GET /api/auth/google/callback?code=…&state=…` is a real endpoint that carries a one-time OAuth code in its query string, so that code would reach CloudWatch. Fix: add `--no-access-log` to the Dockerfile `CMD`. `reep.access` already records every request, with a request id.

#### BUG-8 (medium): with no mail transport, sign-in links go into the log and `mail_logs` says SENT

When `SES_FROM_ADDRESS` is blank, `mail_transport.send` logs the **whole message body** at INFO (`mail_transport.py:227`). That body includes the onboarding, activation and reset links, and each link is a bearer token. `mailer` then records the message as `SENT` (39 SENT rows in this run, delivered to nobody). Nothing stops this on `ENV=prod`: `production_boot_failures()` does not check mail. Today's production task has SES set. A new deployment, or a task definition that loses the variable, would write every student's setup link into CloudWatch while the office's mail screen says the mail went out. Fix: refuse to boot on prod with no transport, or never log the body outside dev/CI and record `FAILED` instead of `SENT`.

#### BUG-9 (medium): changes to the institution tree leave no audit trail

In this run, 73 colleges were created and some were archived, and departments, courses, specializations and batches were created and edited. None of these wrote a `redesign_audit_events` row. In `routers/admin.py`, only `appoint_college_admin` calls `record_change`. That gap includes `PUT /api/admin/students/{id}/cohort`, the move in BUG-1. A student can be seated into another college's batch and nothing records who did it or when. Registration decisions, faculty creation, mentor assignment, student edits and bulk moves are audited, and all of those rows carry a `request_id`.

#### BUG-10 (low): personal data and multi-line entries in the application log

* `app.routers.registration` logs `application <id> from <applicant email> is PENDING_REVIEW` at INFO, and the mail line logs `to=<address>`. Elsewhere this codebase keeps addresses out of logs on purpose (the SES suppression code).
* Log entries are not always one per line. The console mail body spans many lines (216 continuation lines in this run).
* The log mixes two formats: uvicorn's `INFO:     …` and `timestamp LEVEL logger: …`. A CloudWatch metric filter or Logs Insights query cannot parse both reliably.

Fix: log the application id only, and log the mail body (in dev) as one escaped field.

### What held up in logs

* The `reep.access` lines are well structured: `method= path= status= duration_ms= rid=`, with no query string, and the `rid` links them to audit rows.
* No password, password hash, `scrypt:` string or session cookie appeared anywhere in about 3,400 log lines.

### Existing backend suite

`pytest` on its own database finished with exit code 0 (no failures, a few skipped).

## Status after the fixes (2026-10-13, same day)

All ten bugs are fixed in the same PR. `apps/api-py/tests/test_multi_college_negative_fixes.py` pins each one. All 12 of its tests fail on the code before the fix and pass after it.

| Bug | Fix |
|---|---|
| 1 | `admin_mentoring.assert_move_keeps_mentor_in_college` is called by `PUT /admin/students/{id}/cohort`, by `PATCH /admin/students/{id}` (unless the same request names a new mentor, which the mentor branch then judges), and by the bulk `move`, which checks every student before moving any. **A move that would leave a student with a mentor from another college is refused (422)**, and the office releases or reassigns the mentor first, with a reason. I chose refusing over silently releasing the mentor, because a batch edit should not change who can read a student's records as a side effect. |
| 2 | `_resolve_claim`'s `load()` refuses a college, department, course or specialization that is not ACTIVE. Batches are not judged, because the public hierarchy serves batches of every lifecycle status and the form and the API must agree. |
| 3 | The institution models use `StringConstraints(strip_whitespace=True, min_length=1)`, so text is stripped before the length check, on POST and PATCH alike. On `/register`, `name` joins the blank check. |
| 4 | `institution_domains.domain_problem` refuses anything that is not a bare hostname, and the public-mail list (moved there from the registration router, so both use one list). |
| 5 | The phone number may contain only digits and `+ - ( ) . space`, with 7–15 digits. |
| 6 | Follows from bug 3. Rows already saved blank before the fix must be renamed on screen; a fresh database has none. |
| 7 | `--no-access-log` in the Dockerfile `CMD`. |
| 8 | Outside dev/CI, with no transport, `mail_transport.send` logs a warning without the address or the body, then raises, so `deliver_once` records FAILED instead of SENT. In dev the line is a single line. |
| 9 | `_audit_institution` writes a before/after audit row on every create and edit of a college, department, course, specialization and batch, and on `PUT /students/{id}/cohort` (`COHORT_SET`). |
| 10 | The registration INFO and WARNING lines name the application id, not the address. |

**Retest on a fresh database after the fixes:**
* round 1: **276 of 276** probes pass (it was 254 of 276);
* round 2: **155 of 155** probes pass;
* the audit table now shows `college/department/academic_course/academic_specialization/cohort CREATED|UPDATED` and `student COHORT_SET` rows;
* the log has one line per event.

## Notes for whoever re-runs this

* **The registration limit is per address:** 20 attempts per 10 minutes. A run from one machine is cut off after 20 `/register` calls, which caused this run's first false failures. The harness now sends a random `X-Forwarded-For` (uvicorn trusts it from 127.0.0.1). In production `FORWARDED_ALLOW_IPS` is set in the Dockerfile, so real client addresses are used. A whole campus behind one NAT still shares one budget of 20.
* The harness leaves its colleges, applications and accounts behind. Use a throwaway database (`AGENTS.md`: "ONE THING AT A TIME TOUCHES ONE DATABASE").
