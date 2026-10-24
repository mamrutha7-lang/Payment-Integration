# Admin: daily operations

| Field | Value |
|---|---|
| Screens | `/admin/analytics`, `/admin/leave-approvals`, `/admin/jobs`, `/admin/placement`, `/admin/exports`, `/admin/imports`, `/admin/swoc`, `/admin/agent` |
| Automated tests | [`tests/07-admin-operations.spec.ts`](../../tests/07-admin-operations.spec.ts) |
| ID range | TC-600 to TC-699 |

Setup, the seeded accounts and the rules that link these cases to their
automated tests are in [the index](../manual-test-cases.md).

---

## TC-600 — Analytics shows the programme's figures from the database

| Field | Value |
|---|---|
| ID | TC-600 |
| Module | Admin: analytics |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-600` |

### Pre-conditions

1. The web app and the API are running with a development `ENV`, and the dev
   seed has been applied.
2. You are signed in as the Main Admin.
3. For the expected figures, open these API addresses in the same browser
   while signed in, and note what they answer:
   `/api/admin/analytics-summary` (`students_total`, `mentors_total`),
   `/api/admin/analytics/kpis?weeks=6` (the value of each tile),
   `/api/admin/mentor-load` (each faculty account's name, `mentee_count` and
   `capacity`) and `/api/mentor/alerts?open_only=true` (each open alert's
   `message` and `student_name`).

### Test data

| Field | Value |
|---|---|
| Account | Main Admin, `admin@bgscet.ac.in` |
| Seeded faculty account | Test Mentor, capacity 20, one student (Test Student) |

### Steps

1. Open `/admin/analytics`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page heading is "Charts & numbers", and the line under it starts "Programme-wide · N student · M mentor with a group", with N and M the `students_total` and `mentors_total` from the API (a count other than one takes the plural: "students", "mentors"). |
| ER-2 | 1 | The key-figure tiles are shown in this order: Placement rate, Median CTC, Highest CTC, Placement ready %, Attendance average, Mock interviews, Pending approvals. Each measured tile shows the API's value (a percentage with "%", a count as a whole number), and each tile the API left unmeasured shows "—" with the API's reason under it. |
| ER-3 | 1 | The "Mentor load" card is headed "Mentor load · N faculty account", N being the number of accounts `/api/admin/mentor-load` lists ("accounts" when N is not one). Its table shows them 8 to a page, in the API's order, by name and with their mentees as "count / capacity": on the seed, the one row reads "Test Mentor" and "1 / 20". |
| ER-4 | 1 | The "Alerts · open" card lists every open alert with its message and the student's name, for example "Attendance in Managerial Economics dropped below 80%." for Test Student. When the API lists no open alert, the card says "Nothing open." instead. |

---

## TC-601 — The analytics period filter re-counts the figures

| Field | Value |
|---|---|
| ID | TC-601 |
| Module | Admin: analytics |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-601` |

### Pre-conditions

1. As TC-600. For the expected figures, note what
   `/api/admin/analytics/kpis?weeks=12` answers for the "Pending approvals"
   and "Placement rate" tiles.

### Test data

| Field | Value |
|---|---|
| Period | Last 12 weeks |

### Steps

1. Open `/admin/analytics`.
2. In Period, choose "Last 12 weeks".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | Period reads "Last 6 weeks", and the note beside it reads "Every comparison below is against the 6 weeks before this one." |
| ER-2 | 2 | Period reads "Last 12 weeks", and the note reads "Every comparison below is against the 12 weeks before this one." |
| ER-3 | 2 | The "Pending approvals" and "Placement rate" tiles show the values `/api/admin/analytics/kpis?weeks=12` answers. |

---

## TC-602 — The weekly chart can be drawn for one student, and the mentor load can be searched

| Field | Value |
|---|---|
| ID | TC-602 |
| Module | Admin: analytics |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-602` |

### Pre-conditions

1. As TC-600. Test Student has a faculty member (Test Mentor), so the chart's
   "Drawn for" list offers them.

### Test data

| Field | Value |
|---|---|
| Student | Test Student |
| Mentor search | `no such mentor`, then `Test Mentor` |

### Steps

1. Open `/admin/analytics`.
2. In "Drawn for", choose "Test Student".
3. In "Drawn for", choose "Every student in reach".
4. Type "no such mentor" in the search box of the "Mentor load" card.
5. Replace the search with "Test Mentor".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Placement health · weekly" card's chart is labelled "Weekly placement health · Every student in reach · N counted". |
| ER-2 | 2 | The line under the card heading starts "Test Student · the last six weeks on record", and the chart is labelled "Weekly placement health · Test Student · the last six weeks on record". |
| ER-3 | 3 | The chart is labelled for "Every student in reach" again. |
| ER-4 | 4 | No row is left in the Mentor load table: the Test Mentor row is gone. |
| ER-5 | 5 | The table shows the "Test Mentor" row, and every row it shows is a mentor whose name matches. |

---

## TC-603 — Changing and restoring an alert rule for a batch

| Field | Value |
|---|---|
| ID | TC-603 |
| Module | Admin: analytics, alert rules |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-603` |

### Pre-conditions

1. As TC-600. The seeded batch, "Master of Business Administration - Finance
   · 2024-26 Section B", has its "No sign-in for N days" rule set. Note its
   "Days of silence" from `/api/admin/alert-rules?cohort_id=<the batch id>`
   (the seed sets 5), and how many of the five rules are enabled (the seed
   enables 3).

### Test data

| Field | Value |
|---|---|
| Rule | No sign-in for N days |
| New "Days of silence" | the current value plus 1 (6 on a fresh seed) |

### Steps

1. Open `/admin/analytics`.
2. In the "Alerts · open" card, click Rules.
3. In Batch, choose the seeded batch.
4. In the "No sign-in for N days" rule, change "Days of silence" to the new value, then click Save in that rule.
5. Click Close, click Rules again and choose the seeded batch in Batch.
6. Put "Days of silence" back to its original value, then click Save in that rule.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The "Alert rules" dialog opens, with a Batch list of the batches on the deployment. |
| ER-2 | 3 | The dialog reads "3 of 5 rules will be evaluated for this batch." (the number of enabled rules). The "No sign-in for N days" rule is marked "Evaluated", and its "Days of silence" shows the original value. |
| ER-3 | 4 | The rule shows "Saved. The next sweep evaluates this rule for this batch." |
| ER-4 | 5 | The reopened dialog shows the new "Days of silence" value, so the change was stored. |
| ER-5 | 6 | The rule shows "Saved. The next sweep evaluates this rule for this batch." again, and the API reports the original value. |

### Post-conditions

The rule is back to its original "Days of silence". Its stored row was
updated twice; a rule can be switched off but not removed.

---

## TC-610 — The leave queue lists a faculty member's pending request

| Field | Value |
|---|---|
| ID | TC-610 |
| Module | Admin: leave approvals |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-610` |

### Pre-conditions

1. The web app and the API are running with a development `ENV`, and the dev
   seed has been applied.
2. As the faculty member `mentor@bgscet.ac.in` (Test Mentor), apply for the
   leave in the test data, either on `/mentor/leave` or with
   `POST /api/leaves`. The automated run applies through the API.
3. Test Mentor has no leave allowance recorded for the kind and academic year
   of the test dates, so nothing refuses the request. The dev seed records
   none.
4. Then sign in as the Main Admin.

### Test data

| Field | Value |
|---|---|
| Leave type | Casual |
| Dates | two consecutive days far in the future, unique to the run (the automated run picks them from the run id) |
| Purpose | `E2E leave <test run id>` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/leave-approvals`.
2. On the Test Mentor row with the test dates, click the requester's name.
3. In Type, choose "Permission".
4. In Type, choose "Casual".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The Pending tab is selected, and its label counts the pending requests ("Pending · N"). The request is listed with "Test Mentor", type "Casual", the dates written as "d–d Mon yyyy · 2 days", status "Awaiting your decision" and approval chain "Your one signature sanctions or refuses it". |
| ER-2 | 2 | The side panel reads "Test Mentor · Casual leave", shows the purpose under "Reason · visible to approvers only", and its approval chain has two steps: "Applied and signed" and "Sanction", "Your one signature decides it.". A Remarks box and the buttons Reject, Sanction and PDF are shown. |
| ER-3 | 3 | The request is no longer listed: it is not a Permission request. |
| ER-4 | 4 | The request is listed again. |

### Post-conditions

The request is still pending. Withdraw it as Test Mentor on `/mentor/leave`,
or decide it. The automated run withdraws it through the API.

---

## TC-611 — Sanctioning a leave request

| Field | Value |
|---|---|
| ID | TC-611 |
| Module | Admin: leave approvals |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-611` |

### Pre-conditions

1. As TC-610: Test Mentor has applied for the leave in the test data, and you
   are signed in as the Main Admin.

### Test data

| Field | Value |
|---|---|
| Leave type | Casual |
| Dates | two consecutive days far in the future, unique to the run |
| Purpose | `E2E sanction <test run id>` |
| Remarks | `Sanctioned in E2E <test run id>` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/leave-approvals`.
2. On the Test Mentor row with the test dates, click the requester's name.
3. Type the remarks in the Remarks box.
4. Click Sanction.
5. Click Confirm sanction.
6. Click the Approved tab.
7. On the Test Mentor row with the test dates, click the requester's name.
8. Click "Open the signed form (PDF)".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The panel asks for confirmation: "Sanctions the leave. Your name, the time and your uploaded signature image print in the PROGRAM DIRECTOR block of the paper (add the image under Signature in the account menu)." with the buttons Cancel and Confirm sanction. |
| ER-2 | 5 | A message reads "Sanctioned and signed for Test Mentor.", the request is gone from the Pending queue, and the panel is back to "Pick a request". |
| ER-3 | 6 | The Approved tab lists the request with status "Sanctioned" and approval chain "Sanctioned by Main Admin (seed)". |
| ER-4 | 7 | The panel shows "Sanctioned — remarks" with the remarks you typed, and the "Sanction" step of the chain reads "Main Admin (seed) · as Main Admin · " followed by the time. The Reject and Sanction buttons are gone. |
| ER-5 | 8 | The browser saves a PDF file named `leave-test-mentor-<first date>.pdf`. |
| ER-6 | 8 | **Manual only.** The PDF is the college's own leave form with the request written onto it: Test Mentor's name, the dates and the purpose, "Sanctioned", and the Main Admin's name and the decision time in the PROGRAM DIRECTOR block. |

### Post-conditions

The request stays sanctioned. A sanctioned request cannot be withdrawn or
deleted from the console, so each run adds one row to the Approved tab.

---

## TC-612 — Rejecting a leave request needs remarks

| Field | Value |
|---|---|
| ID | TC-612 |
| Module | Admin: leave approvals |
| Priority | P1 |
| Type | Functional, negative and positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-612` |

### Pre-conditions

1. As TC-610: Test Mentor has applied for the leave in the test data, and you
   are signed in as the Main Admin.

### Test data

| Field | Value |
|---|---|
| Leave type | Permission |
| Dates | two consecutive days far in the future, unique to the run |
| Purpose | `E2E reject <test run id>` |
| Remarks | `Rejected in E2E <test run id>` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/leave-approvals`.
2. On the Test Mentor row with the test dates, click the requester's name.
3. Click Reject.
4. Click Confirm reject without typing any remarks.
5. Type the remarks in the Remarks box.
6. Click Confirm reject.
7. Click the Rejected tab.
8. On the Test Mentor row with the test dates, click the requester's name.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The panel reads "The applicant is told the form was not sanctioned, and reads your remarks. Nothing else reaches them." with the buttons Cancel and Confirm reject. |
| ER-2 | 4 | "Remarks are required." appears under the Remarks box, and the request is still in the Pending queue. |
| ER-3 | 6 | A message reads "Not sanctioned — Test Mentor has your remarks.", and the request is gone from the Pending queue. |
| ER-4 | 7 | The Rejected tab lists the request with status "Not sanctioned" and approval chain "Refused by Main Admin (seed)". |
| ER-5 | 8 | The panel shows "Rejected — remarks" with the remarks you typed. |
| ER-6 | 6 | **Manual only.** Test Mentor sees the request as "Not sanctioned" with your remarks on `/mentor/leave`, and, on a server with mail switched on, is emailed the decision. |

### Post-conditions

The request stays rejected, and each run adds one row to the Rejected tab.

---

## TC-613 — Downloading a pending request's leave paper

| Field | Value |
|---|---|
| ID | TC-613 |
| Module | Admin: leave approvals |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-613` |

### Pre-conditions

1. As TC-610: Test Mentor has applied for the leave in the test data, and you
   are signed in as the Main Admin.

### Test data

| Field | Value |
|---|---|
| Leave type | OOD |
| Dates | two consecutive days far in the future, unique to the run |
| Purpose | `E2E paper <test run id>` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/leave-approvals`.
2. On the Test Mentor row with the test dates, click the requester's name.
3. Click PDF.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The browser saves a PDF file named `leave-test-mentor-<first date>.pdf`. The request stays pending. |
| ER-2 | 3 | **Manual only.** The PDF is the college's leave form with Test Mentor's name, the dates, the purpose and the applicant's signature time, and its "Sanctioned" cell reads "Pending". |

### Post-conditions

The request is still pending. The automated run withdraws it as Test Mentor
through the API.

---

## TC-614 — Recording, correcting and removing a leave allowance

| Field | Value |
|---|---|
| ID | TC-614 |
| Module | Admin: leave approvals, leave policy |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-614` |

### Pre-conditions

1. Signed in as the Main Admin.
2. Test Student is in the Department of Management Studies (through the seeded
   batch) and holds no RH allowance for the current academic year. The
   automated run removes one an earlier failed run left behind before it
   starts.

### Test data

| Field | Value |
|---|---|
| Department | Department of Management Studies · BGSCET |
| Type | RH |
| Days | 3, corrected to 5 |
| Academic year | the current one, as the dialog fills it in (from `/api/admin/leave-policy`) |

### Steps

1. Open `/admin/leave-approvals`.
2. Click Leave policy.
3. Under "Record one allowance for a department", choose "Department of Management Studies · BGSCET", choose RH as the type and enter 3 in Days.
4. Untick "Faculty in this department" and tick "Students in this department".
5. Click Record.
6. In the Type box at the top of the dialog, choose RH, then click Show.
7. Change Test Student's entitled days to 5, then click Save on that row.
8. Click the delete button on Test Student's RH row, and confirm.
9. Click Close.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The "Leave policy" dialog opens with the current academic year filled in, and the line under the filters reads "<year> is how REEP spells the current year." |
| ER-2 | 5 | A message reads "N allowance(s) recorded. M people already had one for RH <year> and were left exactly as they were." |
| ER-3 | 6 | The "Allowances recorded" table lists Test Student ("Student") with type RH, 3 days entitled, 0 taken and "3" left. |
| ER-4 | 7 | "Allowance saved." is shown, and Test Student's row shows "5" left. |
| ER-5 | 8 | "Allowance removed." is shown, and Test Student's RH row is gone. |
| ER-6 | 9 | The dialog closes. |

### Post-conditions

Test Student holds no RH allowance, as before. Any other student of the
department who got an allowance in step 5 still holds it; the automated run
removes those through the API.

---

## TC-615 — Recording and removing an academic calendar day

| Field | Value |
|---|---|
| ID | TC-615 |
| Module | Admin: leave approvals, academic calendar |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-615` |

### Pre-conditions

1. Signed in as the Main Admin.
2. The test date is not already on BGS College of Engineering and
   Technology's calendar.

### Test data

| Field | Value |
|---|---|
| College | BGS College of Engineering and Technology (on the seed, the only college) |
| Date | a day far in the future, unique to the run |
| The college is | Shut (holiday) |
| Label | `E2E holiday <test run id>` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/leave-approvals`.
2. Click Calendar.
3. In College, choose "BGS College of Engineering and Technology".
4. Enter the test date in Date, keep "Shut (holiday)", and type the label in Label.
5. Click Record.
6. Click the delete button on the test date's row, and confirm.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The "Academic calendar" dialog opens on the first college on the deployment (on the seed, the only one), and its College list offers BGS College of Engineering and Technology. |
| ER-2 | 3 | College reads "BGS College of Engineering and Technology", and the test date is not among the days listed. |
| ER-3 | 5 | A message reads "<date> is recorded as a holiday and will not be counted against an allowance.", and the day is listed with "Shut · not counted" and the label. |
| ER-4 | 6 | "Day removed." is shown, and the day is gone from the list. |

### Post-conditions

The calendar is as it was before the case.

---

## TC-616 — The allowances table stays readable in a short window

| Field | Value |
|---|---|
| ID | TC-616 |
| Module | Admin: leave approvals, leave policy |
| Priority | P3 |
| Type | Usability, layout |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-616` |

### Pre-conditions

1. Signed in as the Main Admin.
2. The browser window is 1280 × 600 px inside the browser, about what a
   1366 × 768 laptop screen leaves once the browser's own bars are drawn.

### Test data

| Field | Value |
|---|---|
| Window | 1280 × 600 |

### Steps

1. Open `/admin/leave-approvals`.
2. Click Leave policy.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The "Allowances recorded" table is shown at its full height, up to its own 300 px scrolling limit: its header row (Person, Type, Entitled, Taken, Left) and its lines, or its "No allowance is recorded …" line, can be read, and it is the dialog body that scrolls to reach it. |

---

## TC-620 — The jobs sheet lists the postings on record

| Field | Value |
|---|---|
| ID | TC-620 |
| Module | Admin: jobs sheet |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-620` |

### Pre-conditions

1. Signed in as the Main Admin, with the dev seed applied. The seed posts
   "Financial Analyst" at Acme Capital, Bengaluru; "BI Developer" at
   DataWorks, Remote; and "Junior Accountant" at LedgerCo, Mysuru.
2. For the expected figures, note what `/api/admin/jobs` answers: every
   posting's title, company, location and status.

### Test data

| Field | Value |
|---|---|
| Search | `Financial Analyst` |

### Steps

1. Open `/admin/jobs`.
2. Type "Financial Analyst" in "Search postings…".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The heading is "Job postings", and "N on the boards" counts the postings the API lists whose status is not closed. |
| ER-2 | 1 | The first page lists the API's postings in the API's order, up to 10 of them, each as a row with its title and "Company · Location". |
| ER-3 | 2 | Only postings matching the search are listed: the Financial Analyst row, reading "Acme Capital · Bengaluru" with status "Open · no deadline", stays, and the BI Developer row is gone. |

---

## TC-621 — Posting a job puts it on the sheet and on the student's board

| Field | Value |
|---|---|
| ID | TC-621 |
| Module | Admin: jobs sheet |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-621` |

### Pre-conditions

1. Signed in as the Main Admin. Test Student's batch is the Finance
   specialization, whose code is `FIN`.

### Test data

| Field | Value |
|---|---|
| Title | `E2E Analyst <test run id>` |
| Company | `E2E Corp <test run id>` |
| Location | `Mysuru` |
| Tracks | `fin` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/jobs`.
2. Click Post a job.
3. Enter the title, the company and the location.
4. Type "fin" in Tracks.
5. Click Publish.
6. Type the title in "Search postings…".
7. Sign in as the student and open `/student/jobs`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The "Post a job" panel opens, the header button now reads "Close the form", and Publish is disabled while Title and Company are empty. |
| ER-2 | 4 | The help under Tracks reads "Specialization codes, comma separated. Sent as FIN." |
| ER-3 | 5 | A message reads "<title> at <company> is on the sheet.", and the panel closes. |
| ER-4 | 6 | One row is listed: the title with "<company> · Mysuru", track "FIN", 0 applied and status "Open · no deadline". |
| ER-5 | 7 | The student's Jobs board lists the posting with its title and company. |

### Post-conditions

The posting stays listed. Remove it (TC-625). The automated run removes it
through the API.

---

## TC-622 — A posting is not published without a title, a company and a web apply link

| Field | Value |
|---|---|
| ID | TC-622 |
| Module | Admin: jobs sheet |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-622` |

### Pre-conditions

1. Signed in as the Main Admin.

### Test data

| Field | Value |
|---|---|
| Title | `E2E Refused <test run id>` |
| Company | `E2E Corp <test run id>` |
| Apply link | `careers.example.com/e2e` (no `http://` or `https://`) |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/jobs`.
2. Click Post a job.
3. Enter the title.
4. Enter the company.
5. Enter the apply link.
6. Click Publish.
7. Click Cancel.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | Publish is still disabled: the company is missing. |
| ER-2 | 4 | Publish is enabled. |
| ER-3 | 6 | The panel shows "The apply link must start with http:// or https://" and stays open. Nothing is published. |
| ER-4 | 7 | The panel closes, and no posting with the title is on the sheet. |

---

## TC-623 — Duplicating a posting copies it into the form

| Field | Value |
|---|---|
| ID | TC-623 |
| Module | Admin: jobs sheet |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-623` |

### Pre-conditions

1. Signed in as the Main Admin, and a posting with the test data is on the
   sheet: post it (TC-621), or create it with `POST /api/admin/jobs`. The
   automated run creates it through the API.

### Test data

| Field | Value |
|---|---|
| Title | `E2E Duplicate <test run id>` |
| Company | `E2E Corp <test run id>` |
| Location | `Remote` |
| Tracks | `FIN` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/jobs`.
2. Type the title in "Search postings…".
3. Tick the posting's row.
4. Click Duplicate.
5. Click Cancel.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | "Selected: 1" is shown, and Duplicate, Close posting and Remove become enabled. |
| ER-2 | 4 | A message reads "Copied into the form. Check the closing date, then Publish.", and the "Post a job" panel opens holding the posting's title, company, location and tracks. |
| ER-3 | 5 | The panel closes, and the sheet still lists exactly one posting with the title: nothing was published. |

### Post-conditions

The original posting is still listed. The automated run removes it through the
API.

---

## TC-624 — Closing a posting takes it off the student's board

| Field | Value |
|---|---|
| ID | TC-624 |
| Module | Admin: jobs sheet |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-624` |

### Pre-conditions

1. Signed in as the Main Admin, and a posting with the test data is on the
   sheet and on Test Student's Jobs board (it names no college, course or
   track). The automated run creates it through the API and checks the
   student's board lists it.

### Test data

| Field | Value |
|---|---|
| Title | `E2E Close <test run id>` |
| Company | `E2E Corp <test run id>` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/jobs`.
2. Type the title in "Search postings…".
3. Tick the posting's row.
4. Click Close posting, and confirm.
5. In Status, choose "On the boards".
6. In Status, choose "Withdrawn".
7. Sign in as the student and open `/student/jobs`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The browser asks "Take “<title>” at <company> off the student and alumni boards? There is no reopen — republishing is the only way back." After confirming, a message reads "<title> at <company> is off both boards. Applications against it are untouched.", and the row's status is "Withdrawn". |
| ER-2 | 5 | The posting is not listed. |
| ER-3 | 6 | The posting is listed, with status "Withdrawn". |
| ER-4 | 7 | The student's Jobs board does not list the posting. |

### Post-conditions

The posting stays on the sheet as Withdrawn. There is no reopen. Remove it
(TC-625); the automated run removes it through the API.

---

## TC-625 — Removing a posting nobody has applied to

| Field | Value |
|---|---|
| ID | TC-625 |
| Module | Admin: jobs sheet |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-625` |

### Pre-conditions

1. Signed in as the Main Admin, and a posting with the test data, with no
   applications, is on the sheet. The automated run creates it through the
   API.

### Test data

| Field | Value |
|---|---|
| Title | `E2E Remove <test run id>` |
| Company | `E2E Corp <test run id>` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/jobs`.
2. Type the title in "Search postings…".
3. Tick the posting's row.
4. Click Remove.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | Remove is enabled: nobody has applied. |
| ER-2 | 4 | A message reads "<title> at <company> was removed from the sheet.", and the posting is no longer listed ("Rows: 0" under the search). |
| ER-3 | 4 | **Manual only.** Remove asks for no confirmation. On a posting somebody has applied to, Remove is disabled with the tooltip "Cannot be removed — students have applied"; the automated run does not apply, because an applied-to posting can never be removed again. |

---

## TC-626 — Searching the jobs sheet by company or location

| Field | Value |
|---|---|
| ID | TC-626 |
| Module | Admin: jobs sheet |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-626` |

### Pre-conditions

1. As TC-620: the seeded posting "Financial Analyst" at Acme Capital,
   Bengaluru, is on the sheet.

### Test data

| Field | Value |
|---|---|
| Searches | `Acme Capital`, then `Bengaluru` |

### Steps

1. Open `/admin/jobs`.
2. Type "Acme Capital" in "Search postings…".
3. Replace the search with "Bengaluru".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The Financial Analyst row is listed: the search box is labelled "Search postings by role, company or location", and Acme Capital is its company. |
| ER-2 | 3 | The Financial Analyst row is listed: Bengaluru is its location. |

---

## TC-630 — Placement shows the funnel and the offers from the database

| Field | Value |
|---|---|
| ID | TC-630 |
| Module | Admin: placement and offers |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-630` |

### Pre-conditions

1. Signed in as the Main Admin, with the dev seed applied.
2. For the expected figures, note what `/api/admin/placement` answers
   (`eligible`, `applied`, `offered_students`, `approved_students`,
   `placement_rate_pct`, `by_track`) and how many offers
   `/api/mentor/offers/pending` lists.

### Test data

| Field | Value |
|---|---|
| Seeded track | Finance (Test Student's batch) |

### Steps

1. Open `/admin/placement`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The heading is "Placement & offers". The funnel chart is labelled "Placement funnel, counts are distinct students. Eligible students: N students; Applied to ≥1 job: …; Holding an offer: …; Placed (approved offer): …" with the API's four counts. |
| ER-2 | 1 | The funnel notes "Interviewed — not counted." with the reason that no recruiter interview round is recorded in REEP. |
| ER-3 | 1 | The "Offers on record" card shows the API's placement rate and "<approved> of <eligible> eligible". |
| ER-4 | 1 | The "By track" table has a row per track in the API's `by_track`, including "Finance" with its placed and eligible counts. |
| ER-5 | 1 | The Offers tab reads "Offers · N pending", N being the number of offers `/api/mentor/offers/pending` lists. |

---

## TC-631 — Placement's batch and period filters, and the offers export

| Field | Value |
|---|---|
| ID | TC-631 |
| Module | Admin: placement and offers |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-631` |

### Pre-conditions

1. Signed in as the Main Admin.
2. At least one offer has been submitted this year, so Period has a year to
   offer. The automated run makes sure: as Test Student it records an offer
   from `E2E Filter Org <test run id>` and submits it, and as the Main Admin
   it rejects it through the API with a remark.

### Test data

| Field | Value |
|---|---|
| Batch | Master of Business Administration - Finance · 2024-26 Section B |
| Period | the current calendar year |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/placement`.
2. In Batch, choose "Master of Business Administration - Finance · 2024-26 Section B".
3. In Period, choose the current year.
4. Click "Export this batch".
5. In Batch, choose "All batches", and in Period, choose "Whole record".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | Batch reads "All batches", Period reads "Whole record", the figures card is headed "Offers on record", and the export button reads "Export offers". |
| ER-2 | 2 | The line under the heading ends "· 2024-26 Section B", and the export button reads "Export this batch". |
| ER-3 | 3 | The figures card is headed "Offers in <year>" over "offers submitted in <year>, over the students on the roll today", and the line under the heading ends "· 2024-26 Section B · offers submitted in <year>". |
| ER-4 | 4 | The browser saves `reep-placement-summary.csv`, whose header row is "Student,USN,Company,Role,Role type,CTC (INR),Status,Submitted,Decided". |
| ER-5 | 5 | The card is headed "Offers on record" again, and the export button reads "Export offers". |

### Post-conditions

The automated run leaves one rejected offer from `E2E Filter Org <test run id>`
on Test Student's record. Offers cannot be deleted.

---

## TC-632 — Rejecting a submitted offer needs remarks

| Field | Value |
|---|---|
| ID | TC-632 |
| Module | Admin: placement and offers |
| Priority | P1 |
| Type | Functional, negative and positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-632` |

### Pre-conditions

1. As Test Student, record an offer from the test organisation and submit it
   for approval (`POST /api/student/offers`, then
   `POST /api/student/offers/<id>/submit`). The automated run does this
   through the API.
2. Then sign in as the Main Admin.

### Test data

| Field | Value |
|---|---|
| Organisation | `E2E Reject Org <test run id>` |
| Role | `E2E Associate`, full-time, CTC 450000 |
| Remarks | `Offer letter not attached (E2E <test run id>)` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/placement`.
2. Type the organisation in "Search offers…".
3. Tick the offer's row.
4. Click Reject.
5. Click Confirm reject without typing any remarks.
6. Type the remarks in the Remarks box, then click Confirm reject.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The offer is listed: "Test Student", the organisation, "E2E Associate · Full-time" and status "Awaiting approval". |
| ER-2 | 3 | "1 selected · only an offer awaiting approval can be ticked" is shown, and Reject and "Approve offer" become enabled. |
| ER-3 | 5 | "Remarks are required." is shown, and the offer is still "Awaiting approval". |
| ER-4 | 6 | A message reads "Test Student's offer from <organisation> was not approved; they have your remarks.", the offer's status is "Not approved", and the Offers tab's pending count is one lower than before. |

### Post-conditions

The rejected offer stays on Test Student's record. Offers cannot be deleted.

---

## TC-633 — Approving a submitted offer counts the student as placed

| Field | Value |
|---|---|
| ID | TC-633 |
| Module | Admin: placement and offers |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | None (manual only): an approved offer cannot be undone or deleted from any screen or endpoint, and it changes the placement rate, the funnel and the student's own record for every later test, so it is run by hand on a database that is reset afterwards. |

### Pre-conditions

1. A database that will be reset after the case (`python -m app.seed` on a
   fresh database).
2. As Test Student, an offer from the test organisation has been recorded and
   submitted for approval, as in TC-632.
3. Signed in as the Main Admin.

### Test data

| Field | Value |
|---|---|
| Organisation | `Manual Approve Org` |
| Role | `Manual Associate`, full-time, CTC 600000 |

### Steps

1. Open `/admin/placement` and note the placement rate and the "Placed (approved offer)" count.
2. Type the organisation in "Search offers…", and tick the offer's row.
3. Click "Approve offer".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | **Manual only.** A message reads "Test Student's offer from Manual Approve Org is approved and counts towards placement.", and the offer's status is "Approved". |
| ER-2 | 3 | **Manual only.** The funnel's "Placed (approved offer)" count and the placement rate go up, "Top recruiters" lists "Manual Approve Org · 1", and Median CTC and Highest are taken over the approved offers now including this one (a single approved offer of 600000 shows "₹ 6.0 LPA"). |

### Post-conditions

Test Student counts as placed from now on. Reset the database.

---

## TC-640 — Downloading each report, with its receipt in the history

| Field | Value |
|---|---|
| ID | TC-640 |
| Module | Admin: exports |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-640` |

### Pre-conditions

1. Signed in as the Main Admin, who holds the Exports, Interviews and roster
   functions, so every card offers its file with the name and USN columns.
2. The newest row of "Download history" is not a Students download (it never
   is after this case, which downloads Students first and Skills & badges
   last).

### Test data

| Field | Value |
|---|---|
| Account | Main Admin, `admin@bgscet.ac.in` |

### Steps

1. Open `/admin/exports`.
2. In the Students card, click Download CSV.
3. In the Placement card, click Download CSV.
4. In the Ledger card, click Download CSV.
5. In the Interviews card, click Download CSV.
6. In the "Skills & badges" card, click Download CSV.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The heading is "Download reports". Five cards are shown (Students, Placement, Ledger, Interviews, Skills & badges), each marked "Carries name & USN", and Download history's reach reads "Whole programme". |
| ER-2 | 2 | The browser saves `reep-students-mentor-map.csv`, whose header row is "Name,USN,REEP stage,Semester,Cohort,Mentor" and which has a row for Test Student ("1BG24MBA001"). "Students requested — your browser is saving the file." is shown. |
| ER-3 | 2 | Within a few seconds, the newest row of Download history reads Students, "Whole programme", "Main Admin (seed)" and "PII · audited". |
| ER-4 | 3 | The browser saves `reep-placement-summary.csv` with the header row "Student,USN,Company,Role,Role type,CTC (INR),Status,Submitted,Decided", and the newest history row is Placement. |
| ER-5 | 4 | The browser saves `reep-ledger-compliance.csv` with the header row "Name,USN,Days logged,Days submitted,Hours logged,Productive hours", and the newest history row is Ledger. |
| ER-6 | 5 | The browser saves `reep-interview-scores.csv` with the header row "Name,USN,Started,Track,Status,Overall,Communication,Domain,Structure,Record", and the newest history row is Interviews. |
| ER-7 | 6 | The browser saves `reep-cohort-skill-report.csv`, whose header row starts "Name,USN,REEP stage,Points" and ends "Mean growth from T0", and the newest history row is "Skills & badges". |

### Post-conditions

Five receipts are added to the download history. Receipts are kept for the life
of the deployment.

---

## TC-650 — The import wizard asks for a batch, a semester and a file before it checks one

| Field | Value |
|---|---|
| ID | TC-650 |
| Module | Admin: spreadsheet imports |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-650` |

### Pre-conditions

1. Signed in as the Main Admin, with the dev seed applied.

### Test data

| Field | Value |
|---|---|
| Batch | Master of Business Administration - Finance · 2024-26 Section B (listed with "(ended)" once its end date has passed) |
| Semester | 2 |

### Steps

1. Open `/admin/imports`.
2. In Batch, choose the seeded batch.
3. In Semester, choose 2.
4. In "File contains", choose Attendance.
5. Click "Download template".
6. Click "New import".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The heading is "Upload spreadsheets". "File contains" is "Semester marks", Check file is disabled, and the wizard says "Choose the batch this file is for." |
| ER-2 | 2 | Check file is still disabled, and the wizard says "A marks file must name the semester it is for." |
| ER-3 | 3 | The wizard says "Choose the spreadsheet to check." |
| ER-4 | 4 | The wizard explains "An attendance file needs no semester." and the file shape reads "One row per student per subject: usn, subject_code, sessions_held, sessions_attended." |
| ER-5 | 5 | The browser saves `reep-attendance-template.xlsx`. |
| ER-6 | 6 | "The import wizard is back at step 1, with nothing chosen." is shown, "File contains" is "Semester marks" again, and Batch reads "Choose a batch". |

---

## TC-651 — Checking and importing an attendance spreadsheet

| Field | Value |
|---|---|
| ID | TC-651 |
| Module | Admin: spreadsheet imports |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-651` |

### Pre-conditions

1. Signed in as the Main Admin, with the dev seed applied: Test Student
   (USN `1BG24MBA001`) is in the seeded batch and already has attendance on
   file for subject `22MBA11`, 18 of 20 sessions.
2. The attendance file below is saved as a CSV. The automated run builds it
   inside the test.

### Test data

"Test run id" below is any short text unique to this run of the case, such as
the current time in base 36.

The file `e2e-attendance-<test run id>.csv`:

| usn | subject_code | sessions_held | sessions_attended |
|---|---|---|---|
| 1BG24MBA001 | 22MBA11 | 20 | 18 |
| 1BG24MBA999 | 22MBA11 | 20 | 18 |
| 1BG24MBA001 | 22MBA12 | 20 | 25 |

Line 2 re-states what is on file, so it is flagged as an overwrite and
changes no percentage. Line 3 names nobody in the batch, and line 4 claims
more sessions attended than held; both are refused.

### Steps

1. Open `/admin/imports`.
2. In "File contains", choose Attendance.
3. In Batch, choose the seeded batch.
4. Click "Choose file" and choose the attendance file.
5. Click Check file.
6. Click "Error report".
7. Click "Import rows".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The file's name and size are shown beside the button, and the wizard says "<file name> is ready. Press Check file to have the server read it." |
| ER-2 | 5 | The wizard says "3 lines read · 1 would be written. Nothing has been saved yet.", the preview is headed "Preview · <file name>", and the counts read "3 lines read", "0 ok", "1 flagged", "2 refused" and "1 would be written". |
| ER-3 | 5 | The preview lists line 2 as "Warning" for Test Student, and lines 3 and 4 as "Error". |
| ER-4 | 5 | "Recent imports" lists the run first: Attendance, status "Previewed", "3 read · 0 written", "0 ok · 1 flagged · 2 refused". |
| ER-5 | 6 | The browser saves `import-attendance-<first 8 characters of the import run id>-errors.csv` with the header row "Line,Check,USN,Student,Subject,Message" and three lines: line 2 "22MBA11 — overwrites the record already on file", line 3 "1BG24MBA999 is not a student in this batch", and line 4 "sessions_attended: 25 attended is more than the 20 held". |
| ER-6 | 7 | A message reads "20 records written across 1 student, from 1 line.", the counts show "1 written", and the button reads "Imported" and is disabled. |
| ER-7 | 7 | "Recent imports" lists the run first with status "Imported" and "3 read · 1 written". |

### Post-conditions

Test Student's `22MBA11` attendance is rewritten with the same 18 of 20
sessions, dated today, so the percentage is unchanged. The run and its
receipt stay in the history.

---

## TC-652 — A spreadsheet with no data rows is refused

| Field | Value |
|---|---|
| ID | TC-652 |
| Module | Admin: spreadsheet imports |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-652` |

### Pre-conditions

1. Signed in as the Main Admin, with the dev seed applied.

### Test data

"Test run id" below is any short text unique to this run of the case, such as
the current time in base 36.

The file `e2e-empty-<test run id>.csv`, holding only the header row
`usn,subject_code,sessions_held,sessions_attended`.

### Steps

1. Open `/admin/imports`.
2. In "File contains", choose Attendance.
3. In Batch, choose the seeded batch.
4. Click "Choose file" and choose the empty file.
5. Click Check file.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 5 | The wizard shows "The file has a header row and no data rows." as an error, no counts are shown, and "Import rows" stays disabled. |
| ER-2 | 5 | "Recent imports" lists the refused file first with status "Could not read" and "0 read · 0 written" (`/api/admin/imports` lists it first, by its file name, with status `failed`). |

### Post-conditions

The refused run stays in the history as "Could not read". Nothing is written
to any student's record.

---

## TC-660 — SWOC notes show the student's four quadrants

| Field | Value |
|---|---|
| ID | TC-660 |
| Module | Admin: SWOC notes |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-660` |

### Pre-conditions

1. Signed in as the Main Admin, with the dev seed applied. The seed writes four
   lines about Test Student, one per quadrant, for example the strength
   "Strong analytical and quantitative skills.".
2. For the expected lines, note what `/api/admin/swoc` answers for Test
   Student.

### Test data

| Field | Value |
|---|---|
| Student | Test Student, `1BG24MBA001` |
| Search | `1BG24MBA001`, then `no such student` |

### Steps

1. Open `/admin/swoc`.
2. Type "no such student" in "Name, USN or batch".
3. Replace the search with "1BG24MBA001".
4. Click Test Student.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The heading is "SWOC notes", the line under it counts the students and how many have notes, and the list shows Test Student with "1BG24MBA001", the batch and "N notes · K read". |
| ER-2 | 2 | The list says "No student matches this filter." |
| ER-3 | 3 | Test Student is listed again. |
| ER-4 | 4 | The editor is headed "Test Student", shows the four quadrants Strengths, Weaknesses, Opportunities and Challenges, and each line the API lists is shown in its quadrant. |

---

## TC-661 — Adding a SWOC line shows it to the student

| Field | Value |
|---|---|
| ID | TC-661 |
| Module | Admin: SWOC notes |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-661` |

### Pre-conditions

1. Signed in as the Main Admin.

### Test data

| Field | Value |
|---|---|
| Quadrant | Strengths |
| Line | `E2E strength <test run id>: leads the case-study team` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/swoc`.
2. Click Test Student.
3. Click Add in the Strengths quadrant.
4. Type the line in "New strengths note".
5. Click Save.
6. Sign in as the student and open `/student`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | A "New strengths note" box opens with the hint "Something they do well, and what you saw that shows it." and the counter "0/400". |
| ER-2 | 4 | The counter shows the line's length out of 400. |
| ER-3 | 5 | A message reads "Added to Test Student's strengths.", and the line is shown in Strengths marked "Placement cell" and "Not acknowledged", with "Main Admin (seed)" as its author. |
| ER-4 | 6 | The Strength tile of the student's SWOC card includes the line. |

### Post-conditions

The line stays on Test Student's record. Remove it (TC-663); the automated run
removes it through the API.

---

## TC-662 — Editing a SWOC line records the change in the edit history

| Field | Value |
|---|---|
| ID | TC-662 |
| Module | Admin: SWOC notes |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-662` |

### Pre-conditions

1. Signed in as the Main Admin, and the original line below is in Test
   Student's Opportunities with weight 3. Add it (TC-661), or with
   `POST /api/admin/swoc/<student id>`; the automated run uses the API.

### Test data

| Field | Value |
|---|---|
| Original line | `E2E opportunity <test run id>` |
| Edited line | `E2E opportunity <test run id>, edited` |
| New weight | 5 |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/swoc`.
2. Click Test Student.
3. Replace the original line's text with the edited line, then click outside the box.
4. Change the line's weight to 5.
5. Click "Edit history".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | "Saved." is shown, and the line's author line ends with "edited" and the date. |
| ER-2 | 4 | "Saved." is shown again, and the weight reads 5. |
| ER-3 | 5 | The "Edit history" panel lists “<edited line>” by "Main Admin (seed)" with the changes "Text <original line> → <edited line>", and a second edit with "Weight 3 → 5". |

### Post-conditions

The edited line stays on Test Student's record. The automated run removes it
through the API; its edit history is kept.

---

## TC-663 — Removing a SWOC line takes it off the student's home

| Field | Value |
|---|---|
| ID | TC-663 |
| Module | Admin: SWOC notes |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-663` |

### Pre-conditions

1. Signed in as the Main Admin, and the line below is in Test Student's
   Challenges, which Test Student's home shows. The automated run adds it
   through the API and checks the student's home lists it.

### Test data

| Field | Value |
|---|---|
| Line | `E2E challenge <test run id>` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/swoc`.
2. Click Test Student.
3. Click "Remove this entry" on the line.
4. Sign in as the student and open `/student`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | "Removed." is shown, and the line is gone from Challenges. There is no confirmation. |
| ER-2 | 4 | The Challenge tile of the student's SWOC card no longer includes the line. |

---

## TC-664 — A SWOC line cannot be emptied

| Field | Value |
|---|---|
| ID | TC-664 |
| Module | Admin: SWOC notes |
| Priority | P3 |
| Type | Functional, negative |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-664` |

### Pre-conditions

1. Signed in as the Main Admin, and the line below is in Test Student's
   Weaknesses. The automated run adds it through the API.

### Test data

| Field | Value |
|---|---|
| Line | `E2E weakness <test run id>` |
| Test run id | any short text unique to this run of the case, such as the current time in base 36 |

### Steps

1. Open `/admin/swoc`.
2. Click Test Student.
3. Clear the line's text, then click outside the box.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | "An entry cannot be blank — use Remove to delete this line." is shown as an error, and the box shows the line's text again. |
| ER-2 | 3 | The line is unchanged on the server: `/api/admin/swoc` still returns its text. |

### Post-conditions

The line stays on Test Student's record. The automated run removes it through
the API.

---

## TC-670 — Asking the REEP Agent a policy question

| Field | Value |
|---|---|
| ID | TC-670 |
| Module | Admin: REEP Agent |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-670` |

### Pre-conditions

1. Signed in as the Main Admin, and the Main Admin's agent conversation is
   empty (click "Clear conversation" first if it is not). The automated run
   clears it through the API.
2. The Knowledge Base has been seeded (`python -m app.seed_kb`), so the agent
   has approved policy text to answer from. With no model configured the
   agent answers from that text directly.

### Test data

| Field | Value |
|---|---|
| Question | "How do I verify a skill?" (a suggested question) |

### Steps

1. Open `/admin/agent`.
2. Click the suggested question "How do I verify a skill?".
3. Click the Helpful button under the answer.
4. Click "Clear conversation".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The heading is "REEP Agent". The side card "What the agent can see" reads "Signed in as" "Main Admin (seed) · Main Admin", and the empty conversation says "How can I help today?". |
| ER-2 | 2 | The question appears as your message, and the agent answers about raising a skill claim with proof, citing "Source: Verifying a skill (e.g. Power BI)". |
| ER-3 | 2 | **Manual only.** On a server with a language model configured, the answer is written by the model from the same approved sources instead of quoting them. |
| ER-4 | 3 | "Thanks for the feedback" is shown, and the Helpful button is marked pressed. |
| ER-5 | 4 | The conversation is empty again: "How can I help today?" is shown. |

---

## TC-671 — The REEP Agent does not give the office personalised student answers

| Field | Value |
|---|---|
| ID | TC-671 |
| Module | Admin: REEP Agent |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-671` |

### Pre-conditions

1. As TC-670.

### Test data

| Field | Value |
|---|---|
| Question | `Am I placement-ready?` |

### Steps

1. Open `/admin/agent`.
2. Type the question in "Message the REEP Agent…".
3. Click Send.
4. Click "Clear conversation".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Send becomes enabled. |
| ER-2 | 3 | The agent answers "Personalised insights — placement readiness, your next steps, eligible jobs, skills, profile and deadlines — are available on student accounts. I can still answer policy and how-to questions." and notes "Personalised tools are student-only." |
| ER-3 | 4 | The conversation is empty again. |

---

## TC-680 — A faculty member cannot open the daily-operations screens

| Field | Value |
|---|---|
| ID | TC-680 |
| Module | Admin: access |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-680` |

### Pre-conditions

1. Signed in as the faculty member `mentor@bgscet.ac.in` (Test Mentor), who
   holds none of these screens' functions: the Main Admin has granted Test
   Mentor nothing in Governance.

### Test data

| Field | Value |
|---|---|
| Account | Faculty, `mentor@bgscet.ac.in` |

### Steps

1. Open `/admin/analytics`.
2. Open `/admin/leave-approvals`.
3. Open `/admin/jobs`.
4. Open `/admin/placement`.
5. Open `/admin/exports`.
6. Open `/admin/imports`.
7. Open `/admin/swoc`.
8. Open `/admin/agent`.
9. Open `/api/admin/exports/students.csv`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The app sends the browser to the faculty home, `/mentor/notebook`, instead. |
| ER-2 | 2 | The app sends the browser to `/mentor/notebook`. |
| ER-3 | 3 | The app sends the browser to `/mentor/notebook`. |
| ER-4 | 4 | The app sends the browser to `/mentor/notebook`. |
| ER-5 | 5 | The app sends the browser to `/mentor/notebook`. |
| ER-6 | 6 | The app sends the browser to `/mentor/notebook`. |
| ER-7 | 7 | The app sends the browser to `/mentor/notebook`. |
| ER-8 | 8 | The app sends the browser to `/mentor/notebook`. |
| ER-9 | 9 | The API refuses the file with status 403 and the message "You do not hold the 'Exports' capability. An administrator can grant it in Governance." No CSV is saved. |

---

## TC-681 — A student cannot open the daily-operations screens

| Field | Value |
|---|---|
| ID | TC-681 |
| Module | Admin: access |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/07-admin-operations.spec.ts`, title tagged `@TC-681` |

### Pre-conditions

1. Signed in as the student `student@bgscet.ac.in` (Test Student).

### Test data

| Field | Value |
|---|---|
| Account | Student, `student@bgscet.ac.in` |

### Steps

1. Open `/admin/analytics`.
2. Open `/admin/leave-approvals`.
3. Open `/admin/jobs`.
4. Open `/admin/placement`.
5. Open `/admin/exports`.
6. Open `/admin/imports`.
7. Open `/admin/swoc`.
8. Open `/admin/agent`.
9. Open `/api/admin/placement`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The app sends the browser to the student home, `/student`, instead. |
| ER-2 | 2 | The app sends the browser to `/student`. |
| ER-3 | 3 | The app sends the browser to `/student`. |
| ER-4 | 4 | The app sends the browser to `/student`. |
| ER-5 | 5 | The app sends the browser to `/student`. |
| ER-6 | 6 | The app sends the browser to `/student`. |
| ER-7 | 7 | The app sends the browser to `/student`. |
| ER-8 | 8 | The app sends the browser to `/student`. |
| ER-9 | 9 | The API refuses with status 403 and the message "You do not hold the 'Placement' capability. An administrator can grant it in Governance." No placement figures are returned. |
