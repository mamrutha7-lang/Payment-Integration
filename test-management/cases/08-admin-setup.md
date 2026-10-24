# Admin: college setup and interviews

| Field | Value |
|---|---|
| Screens | `/admin/colleges`, `/admin/setup`, `/admin/institution`, `/admin/catalogue`, `/admin/interviews`, `/admin/interview-questions` (and the "Reset daily cap" action on `/admin/students/:id`) |
| Automated tests | [`tests/08-admin-setup.spec.ts`](../../tests/08-admin-setup.spec.ts) |
| ID range | TC-700 to TC-799 |

Setup, the seeded accounts and the rules that link these cases to their
automated tests are in [the index](../manual-test-cases.md).

---

## TC-700 — Colleges lists every college as a card with its facts

| Field | Value |
|---|---|
| ID | TC-700 |
| Module | Admin: Colleges |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-700` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index, with the dev seed applied, so the college "BGS College of Engineering and Technology" (code `BGSCET`, campus Bengaluru) exists. It has one department on a fresh seed; other cases may add more, so note its `department_count` from `GET /api/admin/colleges` (the automated run reads it there).
2. You are signed in as the Main Admin (`admin@bgscet.ac.in`).
3. A second college exists for this case, with the campus, contact and email domain in the test data and one department under it. Create it on "Set up a college" (TC-705 shows how). The automated run creates it through the API.
4. No college is in the Draft state. The console has no control that makes a college a draft, so on a dev database none is.

### Test data

| Field | Value |
|---|---|
| College name | `E2E Card College <run id>` |
| Code | `E2E<run id>` (any code no other college uses) |
| Campus | `Test Campus` |
| Contact | `office-<run id>@example.invalid` |
| Email domain | `e2e-<run id>.example.invalid` |
| Department | `MGT` · Management Studies |

### Steps

1. Open `/admin/colleges`.
2. Find the card of the college in the test data.
3. Press the "Draft" status filter.
4. Press the "All" status filter.
5. Choose "Test Campus" in Campus.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Colleges" screen opens with one card per college. The seeded card is headed "BGS College of Engineering and Technology" and reads "BGSCET · Bengaluru · `<n>` department(s)" (the count from pre-condition 1), with the status chip "Active", "Email domains: Deployment list" (the college has named none of its own, so the deployment's list applies) and "Contact: —". The card has an "Open" link and an "Add departments, courses, batches" link. |
| ER-2 | 2 | The new college's card reads "`<code>` · Test Campus · 1 department", lists the email domain and the contact from the test data, and its "College admin" reads "Not appointed". |
| ER-3 | 3 | Both cards are hidden and the page says "No college matches this filter." |
| ER-4 | 4 | Both cards are shown again. |
| ER-5 | 5 | The new college's card stays and the Bengaluru college's card is hidden. The Campus filter is offered only when colleges sit on more than one campus. |

### Post-conditions

The new college stays on the database. Deleting a college needs a code emailed to the office (TC-704), so the automated run leaves the college in place, under a name unique to the run.

---

## TC-701 — A college card opens its structure and its setup

| Field | Value |
|---|---|
| ID | TC-701 |
| Module | Admin: Colleges |
| Priority | P2 |
| Type | Functional, navigation |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-701` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A college exists for this case with one department (`MGT` · Management Studies) and one course under it. The automated run creates it through the API.

### Test data

| Field | Value |
|---|---|
| College name | `E2E Open College <run id>` |
| Code | `E2E<run id>` |

### Steps

1. Open `/admin/colleges`.
2. On the card of the college in the test data, press "Open".
3. Go back to `/admin/colleges`.
4. On the same card, press "Add departments, courses, batches".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The college has a card. |
| ER-2 | 2 | College structure opens at `/admin/institution?college=<college id>`, with that college chosen in the "College" picker, its name as the first section's heading, and its department "MGT Management Studies" listed under "Departments". |
| ER-3 | 3 | The card is shown again. |
| ER-4 | 4 | "Set up a college" opens at `/admin/setup?college=<college id>` on "Step 1 of 6" with that college loaded: its Code and Name are shown as facts rather than as boxes to type into, and Continue is available. |

---

## TC-702 — Appoint a faculty member to run a college

| Field | Value |
|---|---|
| ID | TC-702 |
| Module | Admin: Colleges |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-702` |

### Pre-conditions

1. You are signed in as the Main Admin. The "Appoint" action is offered only to the Main Admin, who holds `admin.governance`.
2. A college exists for this case with one department, and nobody has been appointed to run it.
3. A faculty account exists that is filed under that department and runs no college. Create it on Faculty → "Add faculty member". The automated run creates both through the API.

### Test data

| Field | Value |
|---|---|
| College name | `E2E Appoint College <run id>` |
| Faculty account | `E2E Faculty <run id>` · `e2e-faculty-<run id>@bgscet.ac.in`, filed under the college's department |
| Short reason | `Too short` (9 characters) |
| Reason | `Runs the new college for the E2E check` |

### Steps

1. Open `/admin/colleges`.
2. On the card of the college in the test data, press "Appoint".
3. In "Faculty account", choose the faculty member in the test data.
4. Type "Too short" in "Reason".
5. Replace the reason with the one in the test data.
6. Press Appoint.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The card's "College admin" reads "Not appointed". |
| ER-2 | 2 | A form opens on the card with a "Faculty account" picker and a "Reason *" box. The help under the reason reads "0 of 20 characters", and the form's Appoint button is disabled. |
| ER-3 | 4 | The help reads "9 of 20 characters" and Appoint stays disabled: a reason must be at least 20 characters. |
| ER-4 | 5 | The help counts the new reason (38 of 20 characters) and Appoint becomes available. |
| ER-5 | 6 | The screen confirms "E2E Faculty `<run id>` appointed to E2E Appoint College `<run id>` with 14 functions." The Main Admin's appointment is live at once, so no function waits for a second approval. The card's "College admin" now names the faculty member with the chip "14 functions", and the form closes. |

### Post-conditions

The faculty account holds 14 functions scoped to the new college. Revoke them in "Who can do what" when the case is done, and remove the account from Faculty. The automated run revokes every grant and removes the account through the API.

---

## TC-703 — Deleting a college that still has people under it is refused

| Field | Value |
|---|---|
| ID | TC-703 |
| Module | Admin: Colleges |
| Priority | P1 |
| Type | Functional, negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-703` |

### Pre-conditions

1. You are signed in as the Main Admin. "Delete college…" is offered to the Main Admin alone.
2. The dev seed's student, Test Student, is seated in the seeded batch `MBA-2026-B` of "BGS College of Engineering and Technology" and filed under its department. Other cases may have approved more students into that batch, filed faculty under the college or left an application open; that changes the numbers in the refusal, not the refusal itself. Note the number of students seated in the college's batches from `GET /api/admin/colleges/<college id>/delete-plan` (`blockers["students.cohort_id"]`; the automated run reads it there). On a freshly seeded database it is 1.

### Test data

| Field | Value |
|---|---|
| College | BGS College of Engineering and Technology (`BGSCET`) |
| Reason | `Checking the refusal` |
| Code | `123456` (any six digits; nothing is sent) |

### Steps

1. Open `/admin/colleges`.
2. On the "BGS College of Engineering and Technology" card, press "Delete college…".
3. Type "Checking the refusal" in "Reason" and "123456" in "Code from your email".
4. Press Cancel.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The seeded college has a card. |
| ER-2 | 2 | A dialog headed "Delete BGSCET · BGS College of Engineering and Technology" opens. It lists what would go and shows the server's refusal word for word. The refusal starts "BGSCET still has people attached:", counts the students seated in its batches as "`<n>` student(s) seated in its batches" with the number noted in pre-condition 2 (at least 1, Test Student), names anything else filed under the college (students filed under its departments, faculty accounts, open applications), and ends "Move them to another college first (Students and Faculty screens) and decide the open applications; nothing was changed." The "Delete this college for good" button is disabled. |
| ER-3 | 3 | "Delete this college for good" stays disabled. For a college that may be deleted, a reason and six digits are what enable it; a refused college cannot be deleted whatever is typed. Do not press "Email me a code": no code is needed for this case. |
| ER-4 | 4 | The dialog closes and the college is still listed. |

---

## TC-704 — Delete an empty college with the code emailed to the office

| Field | Value |
|---|---|
| ID | TC-704 |
| Module | Admin: Colleges |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | None (manual only): the delete needs the six-digit code emailed to the office account, and reading a mailbox or the API's log is not something a browser test may do. |

### Pre-conditions

1. You are signed in as the Main Admin, and you can read the mailbox of `admin@bgscet.ac.in`. On a development server with no mail transport, the API logs each email to its console instead.
2. A college exists that nobody is filed under: no student seated in its batches or filed under its departments, no faculty account filed under it, and no application waiting in the review queue. A college made on "Set up a college" for this case, with one department, is enough.
3. Fewer than the hourly limit of deletion codes have been requested by this account in the last hour.

### Test data

| Field | Value |
|---|---|
| College | `E2E Delete College <date>`, set up for this case |
| Reason | `Made for a test and no longer needed` |

### Steps

1. Open `/admin/colleges`.
2. On the card of the college in the test data, press "Delete college…".
3. Type the reason in "Reason".
4. Press "Email me a code".
5. Type the six-digit code from the email in "Code from your email".
6. Press "Delete this college for good".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A dialog headed "Delete `<code>` · `<name>`" lists what would go (for example "1 department") and shows no refusal. "Delete this college for good" is disabled. |
| ER-2 | 4 | **Manual only.** A note confirms the code was sent, the link now reads "Send another code", and an email with a six-digit code reaches the office account's own address. |
| ER-3 | 5 | "Delete this college for good" becomes available once the reason and a six-digit code are both typed. |
| ER-4 | 6 | The dialog closes, the screen shows the server's confirmation, and the college's card is gone from the list. |

### Post-conditions

The college, its departments, courses, specializations and batches are deleted for good. There is no undo.

---

## TC-705 — Set up a new college in six steps

| Field | Value |
|---|---|
| ID | TC-705 |
| Module | Admin: Set up a college |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-705` |

### Pre-conditions

1. You are signed in as the Main Admin, who holds `admin.institution` and `admin.interview_questions`. Without the second, step 5 shows no mock-interview chips.
2. No college uses the code in the test data.
3. The four shipped interview tracks are enabled (the dev seed's state), and no enabled track uses the code `mkt`.

### Test data

| Field | Value |
|---|---|
| College | Code `E2E<run id>`, Name `E2E Setup College <run id>`, Campus `Test Campus`, Contact `office@example.invalid`, Email domains `e2e-<run id>.example.invalid` |
| Department | Code `MGT`, Name `Management Studies`, Head `Dr. Test Head` |
| Course | Code `MBA`, Name `General MBA`, Degree PG, Years 2 |
| Specializations | Code `fa`, Name `Financial Analytics`; Code `mkt`, Name `Marketing` |
| Starting year | 2026 |

### Steps

1. Open `/admin/setup`.
2. Type the Code and Name from the test data.
3. Type the Campus, Contact and Email domains from the test data, then press Continue.
4. Type the department's Code, Name and Head, then press Continue.
5. Press "Add a course", type the course's Code and Name, keep Degree PG and Years 2, then press Continue.
6. Press "Add a specialization" twice, type the two specializations from the test data, then press Continue.
7. Choose 2026 in "Starting year".
8. Press Continue.
9. Press "Create everything · 7".
10. Open `/admin/colleges` and find the new college's card.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Set up a college" opens on "Step 1 of 6" with "— New college —" chosen in "Continue a college that is already here". Continue is disabled. |
| ER-2 | 2 | Continue becomes available once both required fields hold text. |
| ER-3 | 3 | "Step 2 of 6" shows one empty department row, and Continue is disabled until its Code and Name are typed. |
| ER-4 | 4 | "Step 3 of 6" shows a group headed "Management Studies" reading "No course yet.", and Continue is disabled until a course is added. |
| ER-5 | 5 | The new course row starts on Degree "PG" and Years "2". "Step 4 of 6" shows a group headed "General MBA" reading "No specialization — the course is the whole programme." |
| ER-6 | 6 | "Step 5 of 6" shows one batch per specialization: "General MBA · Financial Analytics" and "General MBA · Marketing". |
| ER-7 | 7 | The Financial Analytics batch reads Code "`<code>`-MGT-MBA-FA-2026-28", Reads as "General MBA - Financial Analytics · 2026-28", Runs "2026-07-01 → 2028-06-30" and Degree "PG", with the chip "Mock interview: Financial Analytics (FA)", because its code `fa` matches that track. The Marketing batch's code is "`<code>`-MGT-MBA-MKT-2026-28" and its chip reads "Mock interview: general", because no track uses the code `mkt`. |
| ER-8 | 8 | "Step 6 of 6" lists what will be written, each marked "New": "`<code>` · E2E Setup College `<run id>`", "MGT · Management Studies", "MBA · General MBA · PG · 2 years", "fa · Financial Analytics", "mkt · Marketing" and the two batches with their codes and "Reads as" text. The button reads "Create everything · 7". |
| ER-9 | 9 | All seven rows read "Created", the screen says "E2E Setup College `<run id>` is set up.", and it offers "Open College structure", "Approve new students" and "Set up another college". |
| ER-10 | 10 | The new college's card reads "`<code>` · Test Campus · 1 department" and lists the email domain and the contact. |

### Post-conditions

The college, its department, course, two specializations and two batches stay on the database (see TC-704 for why a college is not deleted afterwards).

---

## TC-706 — Creating a college that is already there changes nothing and says "Already there"

| Field | Value |
|---|---|
| ID | TC-706 |
| Module | Admin: Set up a college |
| Priority | P1 |
| Type | Functional, re-run |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-706` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. The college in the test data is already set up: department `MGT`, course `MBA` (24 months), specialization `fa` and its 2026-28 batch "`<code>`-MGT-MBA-FA-2026-28". Set it up with TC-705's steps, or let the automated run create it through the API.

### Test data

| Field | Value |
|---|---|
| College | Code `E2E<run id>`, Name `E2E Rerun College <run id>` |
| Department | Code `MGT`, Name `Management Studies` |
| Course | Code `MBA`, Name `General MBA` |
| Specialization | Code `fa`, Name `Financial Analytics` |
| Starting year | 2026 |

### Steps

1. Open `/admin/setup`.
2. Type the Code and Name of the college in the test data, then press Continue.
3. Type the department's Code and Name, then press Continue.
4. Press "Add a course", type the course's Code and Name, then press Continue.
5. Press "Add a specialization", type its Code and Name, then press Continue.
6. Choose 2026 in "Starting year", then press Continue.
7. Press "Create everything · 5".
8. Open `/admin/colleges` and find the college's card.
9. Press "Open" on that card.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Set up a college" opens on "Step 1 of 6". |
| ER-2 | 2 | "Step 2 of 6" is shown. |
| ER-3 | 3 | "Step 3 of 6" is shown. |
| ER-4 | 4 | "Step 4 of 6" is shown. |
| ER-5 | 5 | "Step 5 of 6" is shown. |
| ER-6 | 6 | The plan lists five rows marked "New", including the batch "`<code>`-MGT-MBA-FA-2026-28 · General MBA - Financial Analytics · 2026-28": typed as a new college, the screen cannot know they exist yet. The button reads "Create everything · 5". |
| ER-7 | 7 | The run finishes with "E2E Rerun College `<run id>` is set up." and all five rows read "Already there". None reads "Created": each existing row was looked up by its code and used, never written again. |
| ER-8 | 8 | There is exactly one card for the college, and it still reads "`<code>` · 1 department". |
| ER-9 | 9 | College structure lists the department as "MGT Management Studies 1 batch" and the course as "MBA General MBA 24 months · 1 specialization": nothing was duplicated. |

---

## TC-707 — Continue a half-set-up college from College structure

| Field | Value |
|---|---|
| ID | TC-707 |
| Module | Admin: Set up a college |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-707` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A college exists for this case with department `MGT` and course `MBA` "General MBA" (24 months), and no specialization or batch yet. The automated run creates it through the API.
3. The shipped Business Analytics track (code `ba`) is enabled, as the dev seed leaves it.

### Test data

| Field | Value |
|---|---|
| College | `E2E Continue College <run id>` (code `E2E<run id>`) |
| Specialization | Code `ba`, Name `Business Analytics` |
| Starting year | 2026 |

### Steps

1. Open `/admin/institution` and choose the college in the test data in "College".
2. Under "Specializations", press "Add a specialization".
3. Press "Add a specialization", type Code "ba" and Name "Business Analytics", then press Continue.
4. Choose 2026 in "Starting year", then press Continue.
5. Press "Create everything · 2".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The course reads "MBA General MBA 24 months · 0 specializations". |
| ER-2 | 2 | "Set up a college" opens at `/admin/setup?college=<college id>&step=4`, straight on "Step 4 of 6", with the college's course "General MBA" loaded and reading "No specialization — the course is the whole programme." |
| ER-3 | 3 | Step 5 shows a batch "General MBA · Business Analytics" with the chip "Mock interview: Business Analytics (BA)". |
| ER-4 | 4 | The plan marks the college, "MGT · Management Studies" and "MBA · General MBA · PG · 2 years" as "Already there", and the specialization "ba · Business Analytics" and its batch "`<code>`-MGT-MBA-BA-2026-28 · General MBA - Business Analytics · 2026-28" as "New". The button reads "Create everything · 2". |
| ER-5 | 5 | The run finishes with "E2E Continue College `<run id>` is set up.": the specialization and the batch read "Created", and the three existing rows still read "Already there". |

### Post-conditions

The college now has the specialization `BA` and its 2026-28 batch.

---

## TC-708 — A refused row stops only what hangs under it, and "Create again" resumes

| Field | Value |
|---|---|
| ID | TC-708 |
| Module | Admin: Set up a college |
| Priority | P2 |
| Type | Functional, negative and recovery |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-708` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. No college uses the code in the test data.

### Test data

| Field | Value |
|---|---|
| College | Code `E2E<run id>`, Name `E2E Resume College <run id>` |
| Department | Code `MGT`, Name `Management Studies` |
| Too-long course code | `MBA-THIS-COURSE-CODE-IS-FAR-TOO-LONG` (36 characters; the API accepts at most 32) |
| Course name | `General MBA` |
| Starting year | 2026 |

### Steps

1. Open `/admin/setup`.
2. Type the Code and Name from the test data, then press Continue.
3. Type the department's Code and Name, then press Continue.
4. Press "Add a course", type the too-long course Code and the Name, then press Continue.
5. Press Continue without adding a specialization.
6. Choose 2026 in "Starting year", then press Continue.
7. Press "Create everything · 4".
8. Press "3 Courses" in the list of steps.
9. Replace the course Code with MBA, then press Continue until "Step 6 of 6" is shown.
10. Press "Create again".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Step 1 of 6" is shown. |
| ER-2 | 4 | The screen does not check the code's length and moves to "Step 4 of 6". |
| ER-3 | 5 | Step 5 shows one batch, on the course itself: "General MBA". |
| ER-4 | 6 | The button reads "Create everything · 4" (college, department, course, batch). |
| ER-5 | 7 | The college and the department read "Created". The course reads "Failed · String should have at most 32 characters", the server's own words. The batch under it reads "Skipped". The screen says "1 row could not be created. Fix and press Create again; what landed stays." and the button now reads "Create again". |
| ER-6 | 8 | "Step 3 of 6" is shown again, with the too-long code still in the course's Code box, ready to correct. |
| ER-7 | 9 | "Step 6 of 6" is shown. |
| ER-8 | 10 | The run finishes with "E2E Resume College `<run id>` is set up.". The corrected course "MBA · General MBA" and its batch "`<code>`-MGT-MBA-2026-28" read "Created". The department and the college, both written by the first press, read "Already there": the second press reused them rather than writing them again. |

### Post-conditions

The college, its department, its course and one batch stay on the database.

---

## TC-709 — Setup opened for an existing college shows that college in the picker

| Field | Value |
|---|---|
| ID | TC-709 |
| Module | Admin: Set up a college |
| Priority | P3 |
| Type | Functional, UI state |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-709` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A college exists for this case. The automated run creates it through the API.

### Test data

| Field | Value |
|---|---|
| College | `E2E Picker College <run id>` |

### Steps

1. Open `/admin/colleges`.
2. On the card of the college in the test data, press "Add departments, courses, batches".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The college has a card. |
| ER-2 | 2 | Step 1 shows the college's facts (its Name is the college in the test data), and the "Continue a college that is already here" picker names that college ("`<code>` · E2E Picker College `<run id>`"). |

---

## TC-710 — College structure shows the seeded college top to bottom

| Field | Value |
|---|---|
| ID | TC-710 |
| Module | Admin: College structure |
| Priority | P1 |
| Type | Functional, data display and search |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-710` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. The dev seed's institution keeps the names, codes and dates the seed gave it: department `MGMT` "Department of Management Studies" (head Dr. Kavya N) with course `MBA` "Master of Business Administration" (24 months), specialization `FIN` "Finance" with no interview track mapped to it, and batch `MBA-2026-B` (label 2024-26, named "2024-26 Section B", ending 31 July 2026) with Test Student seated in it.
3. Other cases may have added to it: students approved into `MBA-2026-B`, faculty filed under `MGMT`, more batches. So the COUNTS below are not fixed. Before you start, note them from the API (the automated run reads them): the college's department count (`GET /api/admin/colleges`), `MGMT`'s batch count, `MBA`'s specialization count, `FIN`'s batch count and the number of students seated in `MBA-2026-B` (the `…/departments`, `…/academic-courses`, `…/academic-specializations` and `…/departments/<MGMT id>/cohorts` reads under `/api/admin`). On a freshly seeded database each is 1.

### Test data

| Field | Value |
|---|---|
| College | BGSCET · BGS College of Engineering and Technology |
| Search words | `Finance`, then `no-such-thing` |

### Steps

1. Open `/admin/institution` and choose "BGSCET · BGS College of Engineering and Technology" in "College".
2. Under "Departments", press "MGMT Department of Management Studies", then under "Courses", press "MBA Master of Business Administration".
3. Type "Finance" in "Find".
4. Replace it with "no-such-thing".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The college's section reads "BGSCET · Bengaluru · `<n>` department(s)" and "Email domains: Deployment list". "Departments" lists "MGMT Department of Management Studies Dr. Kavya N `<n>` batch(es)". |
| ER-2 | 2 | Both rows are picked. The course reads "MBA Master of Business Administration 24 months · `<n>` specialization(s)" and is open for editing under the heading "MBA · Master of Business Administration", with Months "24". "Specializations" lists "FIN · Finance" with the mock interview "Not mapped" and its batch count. "Batches" lists `MBA-2026-B`, reading "Master of Business Administration - Finance · 2024-26 Section B", with "MBA · FIN", the number of students seated in it (Test Student among them), and the end date "31 Jul 2026". |
| ER-3 | 3 | The finder narrows every section below the college: "Departments" reads "Nothing matches “Finance”.", while the Finance specialization and the batch (found through its course and specialization) still show. |
| ER-4 | 4 | "Batches" reads "None of the `<n>` batch(es) under MGMT match “no-such-thing”." and the batch row is hidden. |

---

## TC-711 — Edit and archive a course

| Field | Value |
|---|---|
| ID | TC-711 |
| Module | Admin: College structure |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-711` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A college exists for this case with department `MGT` and course `MBA` "General MBA" (24 months, Active) and nothing else. The automated run creates it through the API. Use a college made for the case, not the seeded one: an archived course cannot be un-archived from this screen's Archive button.

### Test data

| Field | Value |
|---|---|
| College | `E2E Course College <run id>` |
| New name | `General MBA (Revised)` |
| New length | 18 months |

### Steps

1. Open `/admin/institution` and choose the college in the test data in "College".
2. Change Name to "General MBA (Revised)" and Months to 18, then press "Save changes".
3. Clear the Name box.
4. Press Archive.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The course is open for editing under "MBA · General MBA", with Name "General MBA", Months "24" and Status "Active". |
| ER-2 | 2 | The screen says "Saved course MBA" and the course row reads "MBA General MBA (Revised) 18 months · 0 specializations". |
| ER-3 | 3 | "Save changes" is disabled: a course cannot be saved without a name. |
| ER-4 | 4 | The screen says "Archived course MBA". The course row carries an "Archived" chip, Status reads "Archived", the Name box shows the stored "General MBA (Revised)" again (the cleared name was never saved), and Archive is disabled. |

### Post-conditions

The course stays archived under a college made for the case.

---

## TC-712 — Map a mock interview to a specialization, and unmap it

| Field | Value |
|---|---|
| ID | TC-712 |
| Module | Admin: College structure |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-712` |

### Pre-conditions

1. You are signed in as the Main Admin, who holds `admin.institution` and `admin.interview_questions`.
2. A college exists for this case with department `MGT`, course `MBA` and specialization `OPS` "Operations".
3. An interview track made for this case exists and is mapped to nothing. Add it on Interview questions with "Add track" (TC-745). The automated run creates both through the API. A track made for the case keeps the shipped four untouched: mapping a track to a specialization also files it under that specialization's college.

### Test data

| Field | Value |
|---|---|
| College | `E2E Mapping College <run id>` |
| Track | Code `t<run id>`, Name `E2E Track <run id>` |

### Steps

1. Open `/admin/institution` and choose the college in the test data in "College".
2. Press "Map mock interview".
3. Choose the track in the test data in "Mock interview" and "OPS · Operations" in "For", then press Save.
4. Press "Map mock interview" again, choose the same track, choose "Every specialization" in "For", then press Save.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Specializations" lists "OPS · Operations" with the mock interview "Not mapped" and 0 batches. |
| ER-2 | 2 | A panel opens with a "Mock interview" picker, listing tracks as "Name (code)", and a "For" picker offering "Every specialization" and "OPS · Operations". |
| ER-3 | 3 | The screen says "E2E Track `<run id>` is the interview for OPS.", the Operations row shows the track's name instead of "Not mapped", and the panel closes. |
| ER-4 | 4 | Choosing the track opens "For" on its current mapping, OPS · Operations. After Save the screen says "E2E Track `<run id>` is offered to every specialization again." and the row reads "Not mapped" again. |

### Post-conditions

Remove the track on Interview questions when done. The automated run deletes it through the API.

---

## TC-713 — Add a non-standard batch, then edit it

| Field | Value |
|---|---|
| ID | TC-713 |
| Module | Admin: College structure |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-713` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A college exists for this case with department `MGT`, course `MBA` "General MBA" and specialization `FIN` "Finance", and no batch. The automated run creates it through the API.
3. Course and Specialization are optional on a new batch (the API's `HIERARCHY_LEVELS` as shipped).

### Test data

| Field | Value |
|---|---|
| College | `E2E Batch College <run id>` |
| Code | `<college code>-SEC-Z` |
| Years | Start 2026, End 2028 |
| Name | `2026-28 Section Z`, then `2026-28 Section Y` |
| Entry date | 2026-08-15 (a date the academic year does not suggest) |

### Steps

1. Open `/admin/institution` and choose the college in the test data in "College".
2. Under "Batches", press "Add batch".
3. Type the Code from the test data, choose 2026 in "Start year" and 2028 in "End year".
4. Type Name "2026-28 Section Z", change "Entry date" to 2026-08-15, choose "MBA · General MBA" in Course and "FIN · Finance" in Specialization, then press "Create batch".
5. Press Edit on the new batch.
6. Change Name to "2026-28 Section Y", then press Save.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Batches" reads "No batch under MGT yet." |
| ER-2 | 2 | The "New batch" form opens. "End year" is disabled with "Needs a start year first.", Specialization is disabled until a course is chosen, and "Create batch" is disabled. |
| ER-3 | 3 | "Entry date" fills in as 2026-07-01 and "Expected completion" as 2028-06-30, from the span. |
| ER-4 | 4 | The screen says "Created batch `<code>`". The new row reads "General MBA - Finance · 2026-28 Section Z" with "MBA · FIN", 0 students and the end date "30 Jun 2028", and the department now reads "MGT Management Studies 1 batch". |
| ER-5 | 5 | The "Edit batch" form opens with the Code box disabled (a batch's code is not edited), the Name "2026-28 Section Z" and the entry date 2026-08-15 that was typed, not the one the span suggests. |
| ER-6 | 6 | The screen says "Saved `<code>`" and the row reads "General MBA - Finance · 2026-28 Section Y". |

### Post-conditions

The batch stays under the college made for the case.

---

## TC-714 — A batch with a taken code or dates that run backwards is refused

| Field | Value |
|---|---|
| ID | TC-714 |
| Module | Admin: College structure |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-714` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A college exists for this case with department `MGT` and no batch. The automated run creates it through the API.
3. The seeded batch `MBA-2026-B` exists (under the seeded college). A batch code is unique across every college.

### Test data

| Field | Value |
|---|---|
| College | `E2E Refusal College <run id>` |
| Taken code | `MBA-2026-B` |
| Free code | `<college code>-BACKWARDS` |
| Expected completion | 2026-06-30 (before the 2026-07-01 entry date the span fills in) |

### Steps

1. Open `/admin/institution` and choose the college in the test data in "College".
2. Press "Add batch", type Code "MBA-2026-B", choose 2026 in "Start year" and 2028 in "End year", then press "Create batch".
3. Replace the Code with the one in the test data, set "Expected completion" to 2026-06-30, then press "Create batch".
4. Press Cancel.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The department reads "MGT Management Studies 0 batches". |
| ER-2 | 2 | The form shows the server's refusal "A batch with code MBA-2026-B already exists." and stays open with what was typed. The department still reads "0 batches". |
| ER-3 | 3 | The form shows "Expected completion must be after the entry date." and the department still reads "0 batches". |
| ER-4 | 4 | The form closes and "Batches" reads "No batch under MGT yet." |

---

## TC-715 — Take a student out of a batch and seat them again

| Field | Value |
|---|---|
| ID | TC-715 |
| Module | Admin: College structure |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-715` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. Test Student (`student@bgscet.ac.in`, USN `1BG24MBA001`, stage Excel-Advanced) is seated in the seeded batch `MBA-2026-B`, as the dev seed leaves them. Other cases may have seated more students there: note the batch's Students count before step 3 (the automated run reads it from `GET /api/admin/cohorts/<batch id>/students`). On a freshly seeded database it is 1.

### Test data

| Field | Value |
|---|---|
| College | BGSCET · BGS College of Engineering and Technology |
| Batch | `MBA-2026-B` |
| Student | Test Student · 1BG24MBA001 |

### Steps

1. Open `/admin/institution` and choose "BGSCET · BGS College of Engineering and Technology" in "College".
2. On the batch MBA-2026-B, press "Students".
3. Press Remove on Test Student’s row.
4. Choose "Test Student · 1BG24MBA001" in "Add a student", then press Add.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The batch `MBA-2026-B` shows the number of students seated in it (`<n>`, Test Student among them). |
| ER-2 | 2 | A panel "Students in MBA-2026-B" opens, listing Test Student with `student@bgscet.ac.in`, USN `1BG24MBA001` and stage "Excel-Advanced". "Add a student" does not offer Test Student, who is already seated. |
| ER-3 | 3 | The screen says "Test Student released from MBA-2026-B.", Test Student leaves the panel (on a fresh database it then reads "Nobody in this batch yet."), the batch's Students count drops by exactly one to `<n>` − 1, and "Add a student" now offers "Test Student · 1BG24MBA001". |
| ER-4 | 4 | The screen says "Test Student seated in MBA-2026-B.", Test Student is listed again and the Students count is back to `<n>`. |

### Post-conditions

Test Student is back in `MBA-2026-B`, as the seed left them. If the case is abandoned after step 3, seat the student again (step 4); the automated run always does so through the API.

---

## TC-716 — Add and remove a college’s email domain

| Field | Value |
|---|---|
| ID | TC-716 |
| Module | Admin: College structure |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-716` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A college exists for this case with no email domain of its own. The automated run creates it through the API. Use a college made for the case: a domain on the seeded college would change which addresses may apply there.

### Test data

| Field | Value |
|---|---|
| College | `E2E Domain College <run id>` |
| Domain as typed | `@E2E-<run id>.Example.Invalid` |
| Domain as stored | `e2e-<run id>.example.invalid` |

### Steps

1. Open `/admin/institution` and choose the college in the test data in "College".
2. Type the domain from the test data in "Add an email domain" and press "Add domain".
3. Type the same domain again and press "Add domain".
4. Press the remove button on the domain.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Email domains" reads "Deployment list", and "Add domain" is disabled while the box is empty. |
| ER-2 | 2 | The domain is listed as "e2e-`<run id>`.example.invalid": lower-case and without the "@". "Deployment list" is gone, and the box empties. |
| ER-3 | 3 | The screen says "e2e-`<run id>`.example.invalid is already on this college." and the domain is still listed once. |
| ER-4 | 4 | The domain is gone and "Email domains" reads "Deployment list" again: with no domain of its own, the college falls back to the deployment's list. |

---

## TC-720 — Catalogue lists the seeded subjects, and the search narrows them

| Field | Value |
|---|---|
| ID | TC-720 |
| Module | Admin: Catalogue |
| Priority | P1 |
| Type | Functional, data display and search |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-720` |

### Pre-conditions

1. You are signed in as the Main Admin, who holds `admin.catalogue`.
2. The dev seed's two subjects are as the seed left them: `22MBA11` "Management & Organisational Behaviour" (Instructor Led, Professional, Excel, semester 1, 40 h taught + 20 h self-study, 16 weeks, with the certification "Leadership Foundations") and `22MBA12` "Managerial Economics" (Teaching Plus Self Learn, Thinking, Excel, semester 1, 40 + 20 h, 16 weeks, no certification), each with Test Student enrolled.
3. How many students are enrolled in each, and how many subjects mention "Economics", can change as other cases run. Note them from `GET /api/admin/catalogue` (the automated run reads them there). On a fresh database each subject has 1 student and one subject mentions "Economics".

### Test data

| Field | Value |
|---|---|
| Search words | `Economics`, then `no-such-subject` |

### Steps

1. Open `/admin/catalogue`.
2. Type "Economics" in the subject search.
3. Replace it with "no-such-subject".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Subjects" tab is open. `22MBA11 · Management & Organisational Behaviour` reads "Instructor Led · 16 weeks", Dimension "Professional", Stage "Excel", Semester 1, Hours 60, Enrolled `<n>` (its enrolment from pre-condition 3) and Evidence path "1 certification". `22MBA12 · Managerial Economics` reads "Teaching Plus Self Learn · 16 weeks", "Thinking", "Excel", 1, 60, its enrolment, and "None mapped". |
| ER-2 | 2 | Managerial Economics is listed and Management & Organisational Behaviour is hidden. The status bar counts only the subjects that mention "Economics" ("Subjects: 1" on a fresh database). |
| ER-3 | 3 | The table reads "No subject matches “no-such-subject”." |

---

## TC-721 — Import subjects: check first, then import, then update

| Field | Value |
|---|---|
| ID | TC-721 |
| Module | Admin: Catalogue |
| Priority | P1 |
| Type | Functional, positive (add and edit) |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-721` |

### Pre-conditions

1. You are signed in as the Main Admin. An import is a programme-wide change, so a grant scoped to one college is refused.
2. No subject uses the code in the test data.

### Test data

Two UTF-8 CSV files. `<code>` is a subject code no other subject uses (the automated run uses `E2ESUB<run id>`), and `<name>` is `E2E Subject <run id>`.

| File | Contents |
|---|---|
| `subjects-new.csv` | `code,name,stage,dimension,semester,model_type,teaching_hours,self_learning_hours_required` then `<code>,<name>,EXCEL,PROFESSIONAL,2,INSTRUCTOR_LED,30,10` |
| `subjects-renamed.csv` | The same header, then `<code>,<name> (renamed),EXCEL,PROFESSIONAL,2,INSTRUCTOR_LED,30,10` |

### Steps

1. Open `/admin/catalogue` and press "Import subjects".
2. Choose the first CSV in the test data with "Choose a CSV".
3. Press "Check first".
4. Press Import.
5. Choose the second CSV in the test data with "Choose a CSV", then press "Check first".
6. Press Import.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Import subjects" panel opens and names the columns a file needs: "code, name, stage, dimension, semester, model_type". It reads "No file chosen", and "Check first" and "Import" are both disabled. |
| ER-2 | 2 | The file name is shown. "Check first" becomes available; "Import" stays disabled until a dry run has reported. |
| ER-3 | 3 | The report reads "Dry run · 1 to add, 0 to update, 0 in error. Nothing has been written yet." with line 2, the code and the outcome "create". The subject is not in the table yet, and "Import" becomes available. |
| ER-4 | 4 | The screen says "1 subject added, 0 updated.", the report reads "Imported · 1 to add, 0 to update, 0 in error." and the Subjects table lists "`<code>` · `<name>`" with "Instructor Led · 16 weeks", Professional, Excel, semester 2, 40 hours, 0 enrolled and "None mapped". |
| ER-5 | 5 | The dry run reads "Dry run · 0 to add, 1 to update, 0 in error." with line 2 marked "update". |
| ER-6 | 6 | The screen says "0 subjects added, 1 updated." and the subject now reads "`<code>` · `<name>` (renamed)". |

### Post-conditions

The subject stays in the catalogue: the console has no way to delete a subject, which is a code shared by every college. The automated run uses a new code each run.

---

## TC-722 — Import refuses a file with missing columns and reports a bad row

| Field | Value |
|---|---|
| ID | TC-722 |
| Module | Admin: Catalogue |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-722` |

### Pre-conditions

1. You are signed in as the Main Admin.

### Test data

`<code>` is a subject code no other subject uses.

| File | Contents |
|---|---|
| `two-columns.csv` | `code,name` then `<code>,Missing columns` |
| `bad-stage.csv` | `code,name,stage,dimension,semester,model_type` then `<code>,Bad stage,LUNCH,PROFESSIONAL,1,INSTRUCTOR_LED` |

### Steps

1. Open `/admin/catalogue` and press "Import subjects".
2. Choose the two-column CSV in the test data with "Choose a CSV", then press "Check first".
3. Choose the bad-stage CSV in the test data with "Choose a CSV", then press "Check first".
4. Press Cancel.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Import subjects" panel opens. |
| ER-2 | 2 | The file is refused with "The CSV is missing these columns: dimension, model_type, semester, stage." and "Import" stays disabled. |
| ER-3 | 3 | The dry run reads "Dry run · 0 to add, 0 to update, 1 in error." The row for the code is marked "error", and its detail names the column and the value: "stage: 'LUNCH' is not one of …" followed by the stages the API accepts. |
| ER-4 | 4 | The panel closes and the subject is not in the catalogue. |

---

## TC-723 — Certifications ↔ badges shows which certifications count towards each badge

| Field | Value |
|---|---|
| ID | TC-723 |
| Module | Admin: Catalogue |
| Priority | P1 |
| Type | Functional, data display and filter |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-723` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. The dev seed's three approved certifications are on the catalogue, programme-wide: "Excel Skills for Business: Essentials" (Macquarie University / Coursera) for the badge "Microsoft Excel – Foundation", "Google Data Analytics: Share Data Through Visualization" for "Data Visualisation", and "Successful Negotiation: Essential Strategies and Skills" (University of Michigan / Coursera, `https://www.coursera.org/learn/negotiation-skills`) for "Negotiation". Students may have claimed badges through them: note each certification's `claims` from `GET /api/admin/approved-certifications` (the automated run reads them there; all 0 on a fresh database).

### Test data

None beyond the seeded catalogue.

### Steps

1. Open `/admin/catalogue` and open the "Certifications ↔ badges" tab.
2. Choose "Mapped" in the Certifications filter.
3. Press the badge "Negotiation".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The tab says "Badges cannot be added or edited here. This screen decides which certifications count towards each." The row "Microsoft Excel – Foundation" (`TECH-EXCEL-FOUNDATION`) reads Skill area "Platform / Technical Skills", Certifications listing "Excel Skills for Business: Essentials", Verifier "Mentor", Claims equal to the claims filed through that badge's certifications (pre-condition 2), Status "Active". The row "Interview Ready" (`RDY-INTERVIEW`) reads "Interview Readiness", "None mapped", Verifier "Award", Claims 0, "Active": a readiness badge is awarded by staff and takes no certification. |
| ER-2 | 2 | Negotiation and Data Visualisation are still listed; Interview Ready, which has none, is hidden. |
| ER-3 | 3 | A panel "Badge · Negotiation" opens beside the table with Code `MGR-NEGOTIATION`, Skill area "Managerial Skills", Stage "Elevate" and Points 15. Under "Accepted certifications" it lists "Successful Negotiation: Essential Strategies and Skills" as a link to its course, with "University of Michigan / Coursera · `<n>` claim(s) ·" (its claims from pre-condition 2) and the scope "Programme-wide". |

---

## TC-724 — Add a certification to a badge, take it off the catalogue and restore it

| Field | Value |
|---|---|
| ID | TC-724 |
| Module | Admin: Catalogue |
| Priority | P1 |
| Type | Functional, positive (add, remove, restore) |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-724` |

### Pre-conditions

1. You are signed in as the Main Admin, who may read and write the approved certification list.
2. No certification with the name in the test data exists.

### Test data

| Field | Value |
|---|---|
| Certification | `E2E Certificate <run id>` |
| Provider | `E2E Provider` |
| Link | `https://example.invalid/course` |
| Badge | Power BI · 15 pts |
| Applies to | Every college and course |

### Steps

1. Open `/admin/catalogue` and press "Add certification".
2. Type the name, provider and link from the test data, choose "Power BI · 15 pts" in "Badge it counts towards" and keep "Every college and course" in "Applies to".
3. Press "Add to catalogue".
4. Press Remove on the new certification.
5. Press Restore.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The screen moves to the "Certifications ↔ badges" tab and opens the "New certification" form. |
| ER-2 | 2 | "Applies to" reads "Every college and course", and the form reads "Category Platform / Technical Skills · 15 points · Excel stage — read off the badge, never typed." |
| ER-3 | 3 | The screen says "E2E Certificate `<run id>` now counts towards Power BI." The form closes, the panel "Badge · Power BI" opens listing the certification, and the Power BI row lists it under Certifications. |
| ER-4 | 4 | The screen says "E2E Certificate `<run id>` is off the catalogue — evidence already filed keeps it." The certification now offers Restore, and the Power BI row no longer lists it. |
| ER-5 | 5 | The screen says "E2E Certificate `<run id>` is back in the catalogue." It offers Remove again and the Power BI row lists it again. |

### Post-conditions

The certification is on the catalogue and counts towards Power BI. Press Remove on it to take it off again; a certification is never deleted, only removed. The automated run removes it through the API.

---

## TC-725 — A certification needs a name and a web link

| Field | Value |
|---|---|
| ID | TC-725 |
| Module | Admin: Catalogue |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-725` |

### Pre-conditions

1. You are signed in as the Main Admin.

### Test data

| Field | Value |
|---|---|
| Certification | `E2E Unsaved Certificate <run id>` |
| Link | `www.example.com` (no `http://` or `https://`) |

### Steps

1. Open `/admin/catalogue` and press "Add certification".
2. Press "Add to catalogue" with every box empty.
3. Type the name from the test data and "www.example.com" in "Link (optional)", then press "Add to catalogue".
4. Press Cancel.
5. Type the name from the test data in the badge search.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "New certification" form opens. |
| ER-2 | 2 | The form says "Give it a name first". |
| ER-3 | 3 | The form says "The link must start with http:// or https://" and stays open. |
| ER-4 | 4 | The form closes. |
| ER-5 | 5 | The table reads "No badge matches these filters.": nothing was added. |

---

## TC-726 — Set a stage rule, change it and remove it

| Field | Value |
|---|---|
| ID | TC-726 |
| Module | Admin: Catalogue |
| Priority | P2 |
| Type | Functional, positive (add, edit, remove) |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-726` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A college exists for this case with course `MBA` "General MBA" and no stage rule. The automated run creates it through the API; a college made for the case keeps the seeded programme's promotions as they are.

### Test data

| Field | Value |
|---|---|
| Programme | MBA — General MBA · `E2E Stage College <run id>` |
| Semester | 3 |
| Stage | Excel-Adv, then Elevate |

### Steps

1. Open `/admin/catalogue` and open the "Stage rules" tab.
2. Choose the programme in the test data in "Programme".
3. Type 3 in "Semester", choose "Excel-Adv" in "Stage", then press "Set rule".
4. Choose "Elevate" in "Stage", then press "Set rule" again.
5. Press Remove on the rule for semester 3.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Set rule" is disabled until a programme is chosen. |
| ER-2 | 2 | The tab reads "No stage rule on MBA · E2E Stage College `<run id>` yet. Promotion will move its semester and leave every student's stage alone." |
| ER-3 | 3 | The screen says "Semester 3 of MBA · E2E Stage College `<run id>` is Excel-Adv." and the table lists the programme, semester 3 and "Excel-Adv" with a Remove button. |
| ER-4 | 4 | The screen says "Semester 3 of MBA · E2E Stage College `<run id>` is Elevate." There is still one rule for semester 3, now reading "Elevate": setting a semester again replaces its rule. |
| ER-5 | 5 | The screen says "Semester 3 no longer names a stage." and the programme has no rule again. |

---

## TC-727 — Interview tracks are listed read-only on the Catalogue

| Field | Value |
|---|---|
| ID | TC-727 |
| Module | Admin: Catalogue |
| Priority | P3 |
| Type | Functional, data display |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-727` |

### Pre-conditions

1. You are signed in as the Main Admin, who holds `admin.interview_questions`. Without it, the tab says the account cannot open Interview questions.
2. The four shipped interview tracks exist, as the dev seed leaves them.

### Test data

None.

### Steps

1. Open `/admin/catalogue` and open the "Interview tracks" tab.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The tab lists "Human Resources (HR)" (key `hr`), "Digital Marketing (DM)" (`dm`), "Business Analytics (BA)" (`ba`) and "Financial Analytics (FA)" (`fa`), each running the phases "opening · probing · deep_dive · wrap_up", with their question counts. It says "Read-only here. Tracks are edited on Interview questions." and offers no button. |

---

## TC-728 — Copy one programme’s catalogue into another

| Field | Value |
|---|---|
| ID | TC-728 |
| Module | Admin: Catalogue |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-728` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. Two colleges exist for this case, each with course `MBA` "General MBA" under a department "Management Studies". The source course has one stage rule (semester 2 is Excel) and one approved certification pinned to it (`E2E Programme Certificate <run id>`, for the badge SQL); the destination has neither. The automated run creates all of it through the API.

### Test data

| Field | Value |
|---|---|
| Copy from | MBA — General MBA · `E2E Copy From <run id>` |
| Copy into | MBA — General MBA · `E2E Copy To <run id>` |

### Steps

1. Open `/admin/catalogue`, open the "Certifications ↔ badges" tab and press "Copy to course…".
2. Choose the source programme in "Copy from" and the destination in "Copy into".
3. Press "Check first".
4. Press Copy.
5. Open the "Stage rules" tab and choose the destination programme in "Programme".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The panel "Copy a catalogue between programmes" opens with "Approved certifications", "Badge map" and "Stage rules" ticked, and "Check first" is disabled. |
| ER-2 | 2 | Under the source it reads "E2E Copy From `<run id>` · Management Studies — 1 certification · 0 badge overrides · 1 stage rule", and under the destination "E2E Copy To `<run id>` · Management Studies — 0 certifications · 0 badge overrides · 0 stage rules". Copy is disabled until a dry run has reported. |
| ER-3 | 3 | The dry run reads "certifications: 1 copied, 0 already there" and "stage_rules: 1 copied, 0 already there", ending "— nothing has been written." |
| ER-4 | 4 | The screen says "2 rows copied into MBA · E2E Copy To `<run id>`." |
| ER-5 | 5 | The destination programme now has the rule: semester 2 is "Excel". |

### Post-conditions

Remove both programmes' stage rules on the Stage rules tab, and remove both certifications (TC-724). The automated run does both through the API.

---

## TC-730 — Interview records lists a student’s mock interview, and the filters narrow it

| Field | Value |
|---|---|
| ID | TC-730 |
| Module | Admin: Interview records |
| Priority | P1 |
| Type | Functional, data display and filter |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-730` |

### Pre-conditions

1. You are signed in as the Main Admin, who holds `admin.interviews`.
2. Test Student has taken at least one mock interview on the Human Resources track: signed in as the student, open `/student/assistant`, accept the interview terms if asked, choose Human Resources and press Start. The automated run opens the same interview socket from a second browser signed in as the student.
3. The API runs with no AWS credentials, as a development server does, so the interviewer cannot reach Amazon Bedrock: the interview is recorded and ends at once as Failed. Other interviews may be on record from other cases; the case follows the one just taken, which is the newest (the list is sorted by Started, newest first). Before steps 3 and 5, note how many interviews `GET /api/admin/interviews?status=completed` and `?track=dm` list (the automated run reads them there; on a development server both are usually 0).
4. Test Student has not used up the day's 20 interview attempts (TC-735 gives them back).

### Test data

| Field | Value |
|---|---|
| Student | Test Student · USN `1BG24MBA001` |
| Track | Human Resources (HR) |

### Steps

1. Open `/admin/interviews`.
2. Choose "Failed" in Status.
3. Choose "Completed" in Status.
4. Choose "All" in Status.
5. Choose "Digital Marketing" in Track.
6. Choose "Human Resources" in Track.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Interview records" lists the interview just taken with USN `1BG24MBA001`, Student "Test Student", Track "HR", Score "—" (not scored, which is not a zero) and Audio "No audio". |
| ER-2 | 2 | The interview is still listed: it ended as Failed. |
| ER-3 | 3 | The interview is not listed. The list holds exactly the completed interviews noted before the step ("Rows: `<n>`"), or reads "No interview matches these filters." when there are none. |
| ER-4 | 4 | The interview is listed again. |
| ER-5 | 5 | The interview is not listed. The list holds exactly the Digital Marketing interviews noted before the step, or reads "No interview matches these filters." when there are none. |
| ER-6 | 6 | The HR interview is listed again. |

### Post-conditions

The interview stays on the student's record until the retention sweep removes it (180 days by default). It used one of the student's 20 attempts for the day.

---

## TC-731 — Open an interview record: how it ended, its report and its transcript

| Field | Value |
|---|---|
| ID | TC-731 |
| Module | Admin: Interview records |
| Priority | P1 |
| Type | Functional, data display |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-731` |

### Pre-conditions

1. As TC-730's pre-conditions 1 to 4: an HR interview by Test Student that failed because the interviewer could not be reached, with nothing said.
2. The API's `INTERVIEW_RECORDING_ENABLED` is off, a development server's default.

### Test data

| Field | Value |
|---|---|
| Student | Test Student · USN `1BG24MBA001` |

### Steps

1. Open `/admin/interviews`.
2. Press the newest row for Test Student.
3. Press the Transcript tab.
4. Press "Close this record".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The student's interview is listed. |
| ER-2 | 2 | A record opens under the list, headed "Test Student" with the line "1BG24MBA001 · Human Resources · `<started>` · `<m:ss>`". It shows the chips "Failed" and "No audio" and no "Download recording" button, and it says why there is no audio: "Voice recording is switched off on this server (INTERVIEW_RECORDING_ENABLED), so no college can record until the operator turns it on." A line reads "Ended: `<reason>` · stopped in opening · 0 answers counted · 0 turns, 0 saved". The Report tab says "No readable report (unavailable). The transcript is still saved." and "Score over time" counts the student's whole history, "`<n>` interview(s) · `<s>` scored" (every interview Test Student has on record, and how many have a score: 0 on a server without Bedrock). |
| ER-3 | 3 | The Transcript tab says "No turns were saved for this interview." |
| ER-4 | 4 | The record closes. |

---

## TC-732 — Export the interview records as CSV

| Field | Value |
|---|---|
| ID | TC-732 |
| Module | Admin: Interview records |
| Priority | P2 |
| Type | Functional, download |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-732` |

### Pre-conditions

1. As TC-730's pre-conditions 1 to 4.

### Test data

None.

### Steps

1. Open `/admin/interviews`.
2. Press "Export CSV".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Export CSV" is available once the list has loaded. |
| ER-2 | 2 | The browser saves `reep-interviews.csv`, whose first line is `Name,USN,Started,Track,Status,Overall,Communication,Domain,Structure,Record` (summary rows only, no transcript) and which has a row for Test Student, `1BG24MBA001`, track `hr`, status `failed`. The screen says "Saved. Summary rows only, and on the export history." |

### Post-conditions

The extract is recorded on the export history (Download reports).

---

## TC-733 — Allow voice recording for a college, then switch it off again

| Field | Value |
|---|---|
| ID | TC-733 |
| Module | Admin: Interview records (interview policy) |
| Priority | P1 |
| Type | Functional, positive (edit and restore) |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-733` |

### Pre-conditions

1. You are signed in as the Main Admin, who holds `admin.interviews` and `admin.institution` (the policy card's college list needs the second).
2. A college exists for this case and nobody has set its interview policy. The automated run creates it through the API. The case can be run on the seeded college instead, but a policy once saved cannot be un-configured from the console, so the seeded college would read "Configured" from then on.
3. The API's `INTERVIEW_RECORDING_ENABLED` is off and the deployment defaults are unchanged (transcripts kept, 180 days, 480 seconds, 8 completed and 20 attempts a day).

### Test data

| Field | Value |
|---|---|
| College | `E2E Policy College <run id>` |

### Steps

1. Open `/admin/interviews` and choose the college in the test data in the policy card’s "College".
2. Tick "Allow voice recording" and press "Save policy".
3. Reload the page and choose the college again.
4. Untick "Allow voice recording" and press "Save policy".
5. Reload the page and choose the college again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The card reads "Not configured" and "deployment defaults", and says "Voice recording is switched off on this server (INTERVIEW_RECORDING_ENABLED). “Allow voice recording” below records nothing until the operator turns it on." "Keep the transcript" is ticked, "Allow voice recording" is not, and the boxes read Keep for 180 days, Time limit 480 seconds, 8 completed per day and 20 attempts per day. |
| ER-2 | 2 | The card says "Saved. Applies to interviews started from now on." and now reads "Configured", "last changed by Main Admin (seed)". |
| ER-3 | 3 | The card reads "Configured" with "Allow voice recording" ticked: the policy was stored. |
| ER-4 | 4 | The card says "Saved. Applies to interviews started from now on." again. |
| ER-5 | 5 | "Allow voice recording" is unticked, and the college keeps a stored policy ("Configured"). |

### Post-conditions

The college's policy is stored with voice recording off, as the deployment default had it.

---

## TC-734 — Attempts per day cannot be lower than completed per day

| Field | Value |
|---|---|
| ID | TC-734 |
| Module | Admin: Interview records (interview policy) |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-734` |

### Pre-conditions

1. As TC-733's pre-conditions: a college made for the case with no policy of its own.

### Test data

| Field | Value |
|---|---|
| College | `E2E Caps College <run id>` |
| Completed per day | 10 |
| Attempts per day | 5 |

### Steps

1. Open `/admin/interviews` and choose the college in the test data in the policy card’s "College".
2. Type 10 in "Completed per day" and 5 in "Attempts per day".
3. Reload the page and choose the college again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The card reads "Not configured" and "Save policy" is available. |
| ER-2 | 2 | The card says "Attempts per day cannot be lower than completed per day.", the "Attempts per day" box is marked invalid, and "Save policy" is disabled. |
| ER-3 | 3 | Nothing was saved: the card still reads "Not configured", with 8 completed and 20 attempts per day. |

---

## TC-735 — Give a student their day’s interview attempts back, with a reason

| Field | Value |
|---|---|
| ID | TC-735 |
| Module | Admin: Interview records (per-student cap reset on Student 360) |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-735` |

### Pre-conditions

1. You are signed in as the Main Admin, who holds `admin.student_records` (to open the record) and `admin.interviews` (to reset the cap).
2. Note the two ceilings that govern Test Student: the MBA course's row on BGSCET's interview policy, else the college's own row, else the deployment's defaults (8 completed and 20 attempts a day). `GET /api/admin/interview-policies/<college id>` shows all three; the automated run reads them there. On a fresh database no policy is set and the defaults apply.
3. Test Student has no interview running.

### Test data

| Field | Value |
|---|---|
| Student | Test Student. Open the record from Students & batches with the View button on the student's row; the address is `/admin/students/<student id>`. |
| Reason | `Two interviews dropped when the lab wifi went down` |

### Steps

1. Open Test Student’s record at `/admin/students/<student id>`.
2. Press "Reset daily cap".
3. Type three spaces in the box.
4. Replace them with the reason in the test data.
5. Press "Give the attempts back".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Interview cap today" reads "Not readable — see below": nothing reports the cap until a reset does. |
| ER-2 | 2 | A box asks "Why are you giving these attempts back?" and says "Attempts already counted are kept; the count restarts from now." "Give the attempts back" is disabled. |
| ER-3 | 3 | "Give the attempts back" stays disabled: blank is not a reason. |
| ER-4 | 4 | "Give the attempts back" becomes available. |
| ER-5 | 5 | The screen says "Test Student may practise again — the 24-hour window is now counted from `<dd Mon yyyy, hh:mm>`, and your reason is on the audit trail." "Interview cap today" reads "0 of `<daily ceiling>` completed · 0 of `<attempt ceiling>` attempts · counted from `<the same time>`" with the ceilings from pre-condition 2 ("0 of 8 completed · 0 of 20 attempts" by default), and the box closes. |

### Post-conditions

The reset and its reason are on the audit trail. The student's count for the next 24 hours starts from the reset.

---

## TC-736 — Listen to and download a recorded interview

| Field | Value |
|---|---|
| ID | TC-736 |
| Module | Admin: Interview records |
| Priority | P2 |
| Type | Functional, download |
| Automated test | None (manual only): a recording needs a live mock interview on Amazon Nova Sonic (AWS Bedrock credentials) with all three recording switches on, which a local run does not have. |

### Pre-conditions

1. The API has AWS credentials that reach Bedrock, and `INTERVIEW_RECORDING_ENABLED=true`.
2. Test Student's college has "Allow voice recording" ticked on its interview policy (TC-733), and Test Student has accepted the interview terms since it was ticked.
3. Test Student has taken a mock interview of at least a minute, speaking aloud, after all of the above.
4. You are signed in as the Main Admin, who holds `admin.interview_audio`.

### Test data

| Field | Value |
|---|---|
| Student | Test Student |

### Steps

1. Open `/admin/interviews`.
2. Choose "Recorded only" in Recording.
3. Press the newest row for Test Student.
4. Press "Download recording".
5. Tick two recorded interviews in the list and press "Download selected audio".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Only interviews with a recording are listed, and their Audio column reads "Audio stored". |
| ER-2 | 3 | The record shows the chips "Completed" (or how it ended) and "Audio stored", and a "Download recording" button. No sentence about a missing recording is shown. |
| ER-3 | 4 | **Manual only.** The browser saves the interview's audio, and it plays both voices. |
| ER-4 | 5 | **Manual only.** The browser saves `reep-interview-recordings.zip` holding both recordings, and the screen says "Saved a zip for 2 recordings. An expired or out-of-scope one is not in it." |

---

## TC-740 — Interview questions shows the tracks as tabs, each with its interviewer

| Field | Value |
|---|---|
| ID | TC-740 |
| Module | Admin: Interview questions |
| Priority | P1 |
| Type | Functional, data display |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-740` |

### Pre-conditions

1. You are signed in as the Main Admin, who holds `admin.interview_questions`.
2. The four shipped tracks are as the dev seed left them: programme-wide, offered to students, Human Resources with the voice `kiara` and Financial Analytics with the voice `matthew`.

### Test data

None.

### Steps

1. Open `/admin/interview-questions`.
2. Press the "Financial Analytics (FA)" tab.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The tracks are tabs, each with its question count: "Human Resources (HR)", "Digital Marketing (DM)", "Business Analytics (BA)" and "Financial Analytics (FA)". Human Resources is open: its card is headed "Human Resources (HR)" with the chips "Every college" and "Offered", "Interviewer role" reads "an empathetic yet compliant Chief Human Resources Officer (CHRO)" and Voice reads "kiara". |
| ER-2 | 2 | The Financial Analytics tab is selected and its card shows the code "FA", the Interviewer role "a sharp, risk-conscious Managing Director / CFO", the Sample question "Walk me through how a $10 depreciation expense flows through the three financial statements.", the Frameworks "DCF modeling, financial ratios, risk mitigation, valuation techniques, M&A frameworks" and the Voice "matthew". The questions card below shows this track's questions. |

---

## TC-741 — Add one question to a track

| Field | Value |
|---|---|
| ID | TC-741 |
| Module | Admin: Interview questions |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-741` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. An interview track made for this case exists with no question on it (add it with TC-746's steps). The automated run creates it through the API; using a track made for the case keeps the shipped tracks' questions as they are.

### Test data

| Field | Value |
|---|---|
| Track | `E2E Questions <run id>` |
| Phase | Opening |
| Question | `Tell me about a supplier you had to replace at short notice.` |

### Steps

1. Open `/admin/interview-questions` and press the tab of the track in the test data.
2. Press "Add question".
3. Choose "Opening" in Phase and type "Short" in Question.
4. Replace it with the question in the test data.
5. Press "Add to <track name>".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The questions card is headed "Questions · 0", reads "0 asked · 0 paused" and "No question on this track yet." |
| ER-2 | 2 | A form opens with Phase on "Probing", a Question box with the counter "0/600", and a disabled "Add to E2E Questions `<run id>`" button. |
| ER-3 | 3 | The counter reads "5/600" and the button stays disabled: a question needs at least 8 characters. |
| ER-4 | 4 | The button becomes available. |
| ER-5 | 5 | The screen says "Question added." Row 1 is an Opening question with the text from the test data and the status "Asked". The card is headed "Questions · 1" and reads "1 asked · 0 paused", and the track's tab counts 1. |

### Post-conditions

Remove the track (TC-746) and its question when done. The automated run deletes both through the API.

---

## TC-742 — Add many questions at once; a line with an unknown phase is skipped

| Field | Value |
|---|---|
| ID | TC-742 |
| Module | Admin: Interview questions |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-742` |

### Pre-conditions

1. As TC-741's: a track made for the case, with no question.

### Test data

| Field | Value |
|---|---|
| Track | `E2E Bulk <run id>` |
| Line 1 | `[opening] Walk me through your background and why operations.` |
| Line 2 | `probing \| Describe a time you cut waste from a process.` |
| Line 3 | `[lunch] What would you order for lunch today?` |

### Steps

1. Open `/admin/interview-questions` and press the tab of the track in the test data.
2. Press "Add many".
3. Type the three lines in the test data in "One question per line".
4. Press "Add all".
5. Tick "Select every question on this page" and press Pause.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The card is headed "Questions · 0". |
| ER-2 | 2 | A box "One question per line" opens and "Add all" is disabled. |
| ER-3 | 3 | "Add all" becomes available. |
| ER-4 | 4 | The screen says "2 questions added. 1 line skipped." and a "Not added:" list gives "line 3: unknown phase 'lunch' (use opening, probing, deep_dive or wrap_up)". Two questions are listed: "Walk me through your background and why operations." as Opening and "Describe a time you cut waste from a process." as Probing. The card is headed "Questions · 2". |
| ER-5 | 5 | The status bar reads "Selected: 2". After Pause the screen says "2 questions paused." and the card reads "0 asked · 2 paused". |

### Post-conditions

As TC-741's.

---

## TC-743 — Edit a question’s phase and text, pause it and find it

| Field | Value |
|---|---|
| ID | TC-743 |
| Module | Admin: Interview questions |
| Priority | P1 |
| Type | Functional, positive (edit and search) |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-743` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A track made for the case holds two asked questions: row 1 "Walk me through your background and why operations." (Opening) and row 2 "Describe a time you cut waste from a process." (Probing). The automated run creates them through the API.

### Test data

| Field | Value |
|---|---|
| Track | `E2E Edit <run id>` |
| New text for row 1 | `Walk me through the process you know best, end to end.` |
| Search word | `waste` |

### Steps

1. Open `/admin/interview-questions` and press the tab of the track in the test data.
2. In row 1, choose "Deep dive" in Phase.
3. In row 1, replace the question text with the one in the test data and move out of the box.
4. In row 2, press the "Asked" status.
5. Type "waste" in the question search.
6. Reload the page and press the tab of the track again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | Two questions are listed and the card reads "2 asked · 0 paused". |
| ER-2 | 2 | Row 1's Phase reads "Deep dive". |
| ER-3 | 3 | The screen says "Question saved." |
| ER-4 | 4 | Row 2's status reads "Paused" and the card reads "1 asked · 1 paused". |
| ER-5 | 5 | Only "Describe a time you cut waste from a process." is listed and the status bar reads "Rows: 1". |
| ER-6 | 6 | Every change was stored: row 1 is a Deep dive question with the new text, and row 2 is still Paused. |

### Post-conditions

As TC-741's.

---

## TC-744 — A question edited below 8 characters is put back

| Field | Value |
|---|---|
| ID | TC-744 |
| Module | Admin: Interview questions |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-744` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A track made for the case holds one question, "Describe a time you cut waste from a process.".

### Test data

| Field | Value |
|---|---|
| Track | `E2E Short <run id>` |
| Too-short text | `Why?` |

### Steps

1. Open `/admin/interview-questions` and press the tab of the track in the test data.
2. Replace the question text with "Why?" and move out of the box.
3. Reload the page and press the tab of the track again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The question is listed. |
| ER-2 | 2 | The screen says "A question needs at least 8 characters. Put back as it was." and the row shows the original text again. |
| ER-3 | 3 | The original text is still stored. |

### Post-conditions

As TC-741's.

---

## TC-745 — Remove a question after confirming

| Field | Value |
|---|---|
| ID | TC-745 |
| Module | Admin: Interview questions |
| Priority | P2 |
| Type | Functional, delete |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-745` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A track made for the case holds one question, "Describe a time you cut waste from a process.".

### Test data

| Field | Value |
|---|---|
| Track | `E2E Remove <run id>` |

### Steps

1. Open `/admin/interview-questions` and press the tab of the track in the test data.
2. Press "Remove this question" and answer Cancel to the browser’s question.
3. Press "Remove this question" again and answer OK.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The question is listed. |
| ER-2 | 2 | The browser asks "Remove this question?" and, answered Cancel, the question stays. |
| ER-3 | 3 | The screen says "Question removed.", the card reads "No question on this track yet." and the track's tab counts 0. |

### Post-conditions

Remove the track made for the case (TC-746). The automated run deletes it through the API.

---

## TC-746 — Add a track, improve it and remove it

| Field | Value |
|---|---|
| ID | TC-746 |
| Module | Admin: Interview questions |
| Priority | P1 |
| Type | Functional, positive (add, edit, delete) |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-746` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. No track uses the code in the test data. No interview has ever been held on it: a track that has run an interview can only be switched off, not removed.

### Test data

| Field | Value |
|---|---|
| Code | `ops<run id>` (2 to 20 lower-case letters, digits, `-` or `_`, starting with a letter) |
| Name | `E2E Operations <run id>` |
| Interviewer role | `an exacting but fair Head of Operations` |
| Sample question | `Walk me through how you would cut a supplier lead time by a week.` |
| Frameworks | `SIPOC, OEE`, then `SIPOC, OEE, root-cause analysis, Little’s Law` |
| Voice | matthew |

### Steps

1. Open `/admin/interview-questions` and press "Add track".
2. Type the Code, Name, Interviewer role, Sample question and the two Frameworks from the test data, and choose "matthew" in Voice.
3. Press "Create track".
4. Replace Frameworks with the four in the test data and press Save.
5. Press Remove and answer OK to the browser’s question.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | A "New track" card opens and "Create track" is disabled. |
| ER-2 | 2 | "Create track" becomes available. |
| ER-3 | 3 | The screen says "Track created." and the new track has its own tab, selected, counting 0 questions. Its card shows the code in capitals, "Every college" and "Offered". The server advises without refusing: "Saved, with a note:" then "Only 2 framework(s). PROBING and DEEP_DIVE work through these one at a time; the shipped tracks carry 4 or more, and a track with fewer runs out of interview before wrap-up." |
| ER-4 | 4 | The screen says "Track saved." and the note is gone. |
| ER-5 | 5 | The browser asks "Remove the E2E Operations `<run id>` track? Its questions stay in the bank." After OK the screen says "Track removed." and the track's tab is gone. |

---

## TC-747 — Move a question down the interviewer’s order

| Field | Value |
|---|---|
| ID | TC-747 |
| Module | Admin: Interview questions |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-747` |

### Pre-conditions

1. You are signed in as the Main Admin.
2. A track made for the case holds two questions in this order: "Walk me through your background and why operations." (Opening) and "Describe a time you cut waste from a process." (Probing).

### Test data

| Field | Value |
|---|---|
| Track | `E2E Order <run id>` |
| Search word | `waste` |

### Steps

1. Open `/admin/interview-questions` and press the tab of the track in the test data.
2. Press "Move this question down" on row 1.
3. Type "waste" in the question search.
4. Reload the page and press the tab of the track again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The two questions are listed in that order. Row 1's "Move this question up" and row 2's "Move this question down" are disabled. |
| ER-2 | 1 | **Manual only.** Each row's two move buttons show an up arrow and a down arrow, so a sighted user can tell them apart. |
| ER-3 | 2 | The two questions swap places. |
| ER-4 | 3 | The move buttons are disabled while the list is filtered, and hovering one says "Clear the search to reorder — the order covers the whole track". |
| ER-5 | 4 | The new order was stored. |

### Post-conditions

As TC-741's.

---

## TC-750 — A faculty member cannot open the college setup and interview screens

| Field | Value |
|---|---|
| ID | TC-750 |
| Module | Admin: access |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-750` |

### Pre-conditions

1. You are signed in as the dev seed's faculty member, Test Mentor (`mentor@bgscet.ac.in` / `mentor123`).
2. Nobody has granted Test Mentor any `admin.*` function in "Who can do what", and Test Mentor runs no college (TC-702 appoints a different account).

### Test data

| Field | Value |
|---|---|
| Screens | `/admin/colleges`, `/admin/setup`, `/admin/institution`, `/admin/catalogue`, `/admin/interviews`, `/admin/interview-questions` |

### Steps

1. Open `/admin/colleges`.
2. Open `/admin/setup`, `/admin/institution`, `/admin/catalogue`, `/admin/interviews` and `/admin/interview-questions` in turn.
3. Open `/api/admin/colleges` in the same browser.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The app sends the faculty member to their own home, `/mentor/notebook` ("Faculty notebook"), and the sidebar offers no Colleges screen. |
| ER-2 | 2 | Every address lands on `/mentor/notebook` in the same way. |
| ER-3 | 3 | The API itself refuses, whatever the screen does: it answers 403 with `{"detail":"You do not hold the 'Institution hierarchy' capability. An administrator can grant it in Governance."}` |

---

## TC-751 — A student cannot open the college setup and interview screens

| Field | Value |
|---|---|
| ID | TC-751 |
| Module | Admin: access |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/08-admin-setup.spec.ts`, title tagged `@TC-751` |

### Pre-conditions

1. You are signed in as the dev seed's student, Test Student (`student@bgscet.ac.in` / `student123`).

### Test data

| Field | Value |
|---|---|
| Screens | As TC-750's |

### Steps

1. Open `/admin/colleges`.
2. Open `/admin/setup`, `/admin/institution`, `/admin/catalogue`, `/admin/interviews` and `/admin/interview-questions` in turn.
3. Open `/api/admin/colleges` in the same browser.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The app sends the student to their own home, `/student`, which greets them "Welcome back, Test". |
| ER-2 | 2 | Every address lands on `/student` in the same way. |
| ER-3 | 3 | The API answers 403 with `{"detail":"You do not hold the 'Institution hierarchy' capability. An administrator can grant it in Governance."}` |
