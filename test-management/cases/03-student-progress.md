# Student: home and progress

| Field | Value |
|---|---|
| Screens | `/student`, `/student/skilling`, `/student/time-log`, `/student/courses`, `/student/records`, `/student/leaderboards` |
| Automated tests | [`tests/03-student-progress.spec.ts`](../../tests/03-student-progress.spec.ts) |
| ID range | TC-200 to TC-299 |

Setup, the seeded accounts and the rules that link these cases to their
automated tests are in [the index](../manual-test-cases.md).

Every case here is run as the dev seed's student, Test Student
(`student@bgscet.ac.in` / `student123`, USN `1BG24MBA001`), signed in as in
TC-001 unless the case says otherwise. "Today" on the Time Allocation Ledger
is the programme's calendar day, which the server decides in the college's
time zone (`PROGRAMME_TIMEZONE`, Asia/Kolkata by default), not the tester's
computer clock.

---

## TC-200 — Student home greets the student with their stage, semester, USN and login streak

| Field | Value |
|---|---|
| ID | TC-200 |
| Module | Student home: header |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-200` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied.
2. The browser is signed in as the seeded student.

### Test data

| Field | Value |
|---|---|
| Name on record | Test Student |
| Stage | Excel-Adv (`EXCEL_ADVANCED`) |
| Semester | 2 |
| USN | `1BG24MBA001` |

### Steps

1. Open the student home at `/student`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page heading reads "Welcome back, Test". |
| ER-2 | 1 | The line under it reads "Excel-Adv stage · Semester 2 · 1BG24MBA001". |
| ER-3 | 1 | A green chip at the right of the header reads "N-day login streak". N counts the consecutive days, ending today or yesterday, on which the account signed in. The dev seed records five such days ending on the day it ran, and signing in records the current day, so on the day the seed ran the chip reads "5-day login streak". |

---

## TC-201 — The programme stage cards show each module's status

| Field | Value |
|---|---|
| ID | TC-201 |
| Module | Student home: programme stage cards |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-201` |

### Pre-conditions

1. The dev seed has been applied, and nobody has changed the student's
   programme milestones since.
2. The browser is signed in as the seeded student.

### Test data

None beyond the seeded student.

### Steps

1. Open the student home at `/student`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | Three cards are headed Reboot, Excel and Elevate. Reboot lists "REE 101", "REE 102" and "English Baseline · AI"; Excel lists "PEEP 1", "PEEP 2", "VTU 1" and "VTU 2"; Elevate lists "Hippo", "Mock GDS", "Mock Interview" and "Aptitude training". |
| ER-2 | 1 | Each row ends with a status icon whose tooltip (hover over it) gives the row's status: "Completed" for REE 101, REE 102, PEEP 1 and VTU 1; "In progress" for English Baseline · AI, PEEP 2, VTU 2 and Hippo; "Not started yet" for Mock GDS, Mock Interview and Aptitude training. |
| ER-3 | 1 | A status key under the cards reads "Completed", "In progress" and "Not started yet". |
| ER-4 | 1 | Only "English Baseline · AI" and "Mock Interview" are links. The other rows are plain text. |

---

## TC-202 — A stage card row that has its own screen opens it

| Field | Value |
|---|---|
| ID | TC-202 |
| Module | Student home: programme stage cards |
| Priority | P2 |
| Type | Functional, navigation |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-202` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.

### Test data

None beyond the seeded student.

### Steps

1. Open the student home at `/student`.
2. In the Reboot card, click "English Baseline · AI".
3. Go back to the student home with the browser's Back button.
4. In the Elevate card, click "Mock Interview".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The English baseline screen opens at `/student/english`, headed "English Proficiency Baseline". |
| ER-2 | 3 | The student home is shown again at `/student`. |
| ER-3 | 4 | The mock interviewer opens at `/student/assistant`, headed "Mock interview". |

---

## TC-203 — The placement readiness card shows the score, the band and every factor

| Field | Value |
|---|---|
| ID | TC-203 |
| Module | Student home: placement readiness |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-203` |

### Pre-conditions

1. The dev seed has been applied, and the student's record is as seeded:
   no phone number or LinkedIn URL on the placement profile, an empty resume
   profile, no certification completed and no scored mock interview.
2. The browser is signed in as the seeded student.

### Test data

None beyond the seeded student.

### Steps

1. Open the student home at `/student` and scroll to the "Placement readiness" card.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The card shows the score "67" over "/ 100", a green chip reading "On track", and the summary "67/100 — On track. 3 of 6 placement checks met. 1 check(s) not measured yet, and are not counted either way." |
| ER-2 | 1 | It lists seven checks, each with a chip and a detail line: CGPA, "Met", "CGPA 8.2 meets the 6.0 cut-off"; Live backlogs, "Met", "0 live backlog(s); limit is 0"; Attendance, "Met", "Attendance 85.0% vs required 85.0%"; Certification completion, "Not met", "0.0% of certifications completed vs required 75.0%"; Placement profile, "Not met", "Add your phone number and LinkedIn URL"; Resume profile, "Not met", "Resume profile 0% complete (target 70%)"; Mock interview, "Not measured", "No scored mock interview in the last 90 days — practise one and 60 is the target". |
| ER-3 | 1 | The "Met" chips are green, the "Not met" chips red, and the "Not measured" chip grey: a check with nothing recorded is left out of the score, not counted as a failure. |

---

## TC-204 — A recommendation opens the screen that acts on it

| Field | Value |
|---|---|
| ID | TC-204 |
| Module | Student home: recommendations |
| Priority | P2 |
| Type | Functional, navigation |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-204` |

### Pre-conditions

1. The dev seed has been applied, and the student's resume profile is less
   than 70% complete (it is empty on the seed).
2. The browser is signed in as the seeded student.

### Test data

None beyond the seeded student.

### Steps

1. Open the student home at `/student`.
2. In the "Recommended for you" card, click Complete.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The card recommends "Complete your resume profile", explains "A fuller resume profile means a stronger auto-generated CV", and offers a Complete button. |
| ER-2 | 2 | The Resume Builder opens at `/student/resume`, headed "Resume Builder". |

---

## TC-205 — The landing shows the student's SWOC, attendance, marks and academic history

| Field | Value |
|---|---|
| ID | TC-205 |
| Module | Student home: record summary |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-205` |

### Pre-conditions

1. The dev seed has been applied, and nobody has edited the student's SWOC
   lines, attendance, results or academic history since.
2. The browser is signed in as the seeded student.

### Test data

None beyond the seeded student.

### Steps

1. Open the student home at `/student`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The SWOC card shows four tiles: Strength "Strong analytical and quantitative skills.", Weakness "Needs structured problem-solving practice.", Opportunity "Fintech internships opening this quarter." and Challenge "Public speaking under time pressure." |
| ER-2 | 1 | The Attendance card shows 22MBA11 at 90% and 22MBA12 at 80%. |
| ER-3 | 1 | The VTU marks card plots semester 1 at 8.2 and is captioned "CGPA out of 10 · Sem 2–4 not published yet". |
| ER-4 | 1 | Academic History shows three cards: "10th Standard", 2018, St. Joseph's High School, CBSE, 88%, "88 / 100", "Medium: English"; "12th Standard", 2020, Sri Chaitanya PU College, Karnataka PUC, 82%, "82 / 100", "Medium: English"; "Undergraduate", 2024, Bangalore University, 72%, "72 / 100". |
| ER-5 | 1 | "Declared education gaps" carries a "No gaps declared" chip and the line "Your academic timeline is continuous — no gaps to declare." |

---

## TC-210 — The badge board shows the student's verified badges and open claims

| Field | Value |
|---|---|
| ID | TC-210 |
| Module | Skilling: badge board |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-210` |

### Pre-conditions

1. The dev seed has been applied, and no mentor has decided the seeded
   claims since: "Business Analytics Fundamentals" is still waiting for a
   verdict and "Critical Thinking" still needs changes.
2. The browser is signed in as the seeded student.

### Test data

None beyond the seeded student.

### Steps

1. Open the Skilling screen at `/student/skilling`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The badge board groups the badges into five categories, captioned "Managerial Skills" "12 badges · 1 earned", "Sectoral Skills" "16 badges", "Platform / Technical Skills" "10 badges · 1 earned", "Thinking Skills" "6 badges" and "Interview Readiness" "4 readiness badges". |
| ER-2 | 1 | "Business Communication" and "Microsoft Excel – Foundation" read "Verified" and carry the blue verified tick. "Business Analytics Fundamentals" reads "Waiting for review". A badge nobody has claimed, such as "Strategic Thinking", reads "Not claimed". |
| ER-3 | 1 | The line under the board reads "2 skills currently illuminated". |
| ER-4 | 1 | A "Claims in progress" table lists "Business Analytics Fundamentals" with the status "Waiting for review" and the note "—", and "Critical Thinking" with the status "Needs changes" and the note "Add the certificate or the organiser's result sheet." Earlier runs of TC-213 may have added "Design Thinking" rows reading "Not verified". |

---

## TC-211 — Tapping a badge previews its earned state

| Field | Value |
|---|---|
| ID | TC-211 |
| Module | Skilling: badge board |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-211` |

### Pre-conditions

1. The dev seed has been applied, and "Strategic Thinking" has never been
   claimed.
2. The browser is signed in as the seeded student.

### Test data

| Field | Value |
|---|---|
| Badge | Strategic Thinking (Thinking Skills) |

### Steps

1. Open the Skilling screen at `/student/skilling`.
2. Click the "Strategic Thinking" badge.
3. Click the "Strategic Thinking" badge again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The badge lights up and reads "Preview", without the blue verified tick. Hovering over it shows "Preview — tap again to clear". The line under the board rises by one, to "3 skills currently illuminated". |
| ER-2 | 3 | The badge reads "Not claimed" again, and the line returns to "2 skills currently illuminated". |

---

## TC-212 — The claim form asks for a category before a badge and offers no readiness badges

| Field | Value |
|---|---|
| ID | TC-212 |
| Module | Skilling: claim form |
| Priority | P2 |
| Type | Functional, validation |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-212` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.

### Test data

| Field | Value |
|---|---|
| First category | Sectoral Skills |
| Badge | Financial Statement Analysis · Finance |
| Second category | Platform / Technical Skills |

### Steps

1. Open the Skilling screen at `/student/skilling`.
2. In "Skill category", select Sectoral Skills.
3. In "Skill badge", select Financial Statement Analysis · Finance.
4. Change "Skill category" to Platform / Technical Skills.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Skill category" offers "Select a category", Managerial Skills, Sectoral Skills, Platform / Technical Skills and Thinking Skills. Interview Readiness is not offered: readiness badges are awarded on assessment thresholds and cannot be claimed. |
| ER-2 | 1 | "Skill badge" is disabled and reads "Pick a category first", and Submit claim is disabled. |
| ER-3 | 2 | "Skill badge" becomes enabled, reads "Select a badge", and lists the 16 Sectoral badges, each followed by its track, such as "Financial Statement Analysis · Finance" and "Talent Acquisition · Human Resources". |
| ER-4 | 3 | Submit claim stays disabled, because no certificate is attached. |
| ER-5 | 4 | "Skill badge" is cleared back to "Select a badge" and lists the 10 Platform / Technical badges, from "Microsoft Excel – Foundation" to "AI for Analysis & Decision-Making". |

---

## TC-213 — Filing a claim with a certificate sends the badge for review

| Field | Value |
|---|---|
| ID | TC-213 |
| Module | Skilling: claim form |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-213` |

### Pre-conditions

1. The dev seed has been applied, so the student has a mentor (Test Mentor).
2. The browser is signed in as the seeded student.
3. "Design Thinking" reads "Not claimed" on the board: no claim of it is
   waiting for the mentor.
4. The student has fewer than 40 uploaded files, the per-student limit.

### Test data

| Field | Value |
|---|---|
| Certificate | `tests/fixtures/sample.pdf` (a one-page PDF) |
| Skill category | Thinking Skills |
| Skill badge | Design Thinking |
| Issued by | `E2E Institute` and a run id, e.g. `E2E Institute k3x9a` |
| Note for the reviewer | `E2E claim` and the same run id |

### Steps

1. Open the Skilling screen at `/student/skilling`.
2. Click "Click to upload or drop a file" and choose the certificate file.
3. In "Skill category", select Thinking Skills.
4. In "Skill badge", select Design Thinking.
5. Enter the test data in "Issued by" and "Note for the reviewer".
6. Click Submit claim.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The upload box is replaced by the file name, "sample.pdf", and a Replace button. Submit claim stays disabled. |
| ER-2 | 4 | Submit claim becomes enabled. |
| ER-3 | 6 | The form is replaced by "Claim submitted. A faculty reviewer will check the certificate and verify the badge." and a "Claim another skill" button. |
| ER-4 | 6 | The "Design Thinking" badge now reads "Waiting for review", and "Claims in progress" lists "Design Thinking" with the status "Waiting for review" and the note "—". |
| ER-5 | 6 | **Manual only.** The claim waits in the queue at `/mentor/verifications` of every faculty member granted "Verify skills & evidence" for this student — on the dev seed, Test Mentor, whose grant covers the seeded college (module 05) — and the certificate is on the student's `/student/uploads` shelf, titled "Design Thinking — " followed by the issuer. Where mail is on for skill claims (`BADGE_MAIL_ENABLED=true`, or SES configured), each of them (Test Mentor on the dev seed) is emailed "Test Student has claimed the Design Thinking badge". |

### Post-conditions

The claim waits for a faculty reviewer, and the badge reads "Waiting for
review", until a reviewer decides it. To run the case again, Test Mentor rejects the
claim with a note on `/mentor/verifications`, and the student deletes the
certificate on `/student/uploads`. The automated test does both through the
API when it ends. The rejected claim stays in "Claims in progress" as
"Design Thinking", "Not verified", with the note "E2E cleanup: rejected by
the automated run.", and the badge reads "Not claimed" again.

---

## TC-214 — The claim form refuses a certificate of the wrong type or size

| Field | Value |
|---|---|
| ID | TC-214 |
| Module | Skilling: claim form |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-214` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.
3. The file dialog lists only PDF and JPEG files. Switch it to show all
   files to pick the PNG, or drag the file onto the upload box instead.

### Test data

| Field | Value |
|---|---|
| Wrong type | `tests/fixtures/sample.png` (a PNG image) |
| Too large | Any PDF larger than 5 MB. The automated test generates `over-5-mb.pdf`, 5 MB plus one byte. |

### Steps

1. Open the Skilling screen at `/student/skilling`.
2. Click "Click to upload or drop a file" and choose the PNG file.
3. Click "Click to upload or drop a file" and choose the PDF larger than 5 MB.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | An error reads "That file is not a PDF or a JPEG. Attach the certificate as one of those." No file is attached: the box still reads "Click to upload or drop a file", and there is no Replace button. |
| ER-2 | 3 | The error changes to "That file is over 5 MB. Export a smaller PDF or JPEG and try again." and still no file is attached. |

---

## TC-220 — The Time Allocation Ledger opens on today with the last 14 days

| Field | Value |
|---|---|
| ID | TC-220 |
| Module | Time Allocation Ledger |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-220` |

### Pre-conditions

1. The dev seed has been applied. It writes a submitted day for the day
   before it ran and a 23.5-hour draft for the day it ran.
2. The browser is signed in as the seeded student.

### Test data

None beyond the seeded student.

### Steps

1. Open the Time Allocation Ledger at `/student/time-log`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Time Allocation Ledger" under the eyebrow "Daily log · Semester 2". The date control shows today in the form "Wed · 23 Sep 2026", and Next day is disabled. |
| ER-2 | 1 | A "Last 14 days" card lists 14 day chips, today first, labelled "Today" and then by weekday and date. Each chip says what the day holds: "Submitted", "Draft · N h" (saved but not submitted), "Not logged", "Locked" (past its edit window with nothing logged) or "N h · locked". On the day the seed ran, the day before it reads "Submitted" and the seed's own day "Draft · 23.5 h". |
| ER-3 | 1 | The card's summary counts the submitted days and the days with any hours, "N submitted · M with entries", and says "Each day can be filled in for 2 days after it ends, then it locks." |
| ER-4 | 1 | The table has six slot rows, "5:00 – 9:00 am" (4 h capacity), "9:00 am – 12:00 pm" (3 h), "12:00 – 3:00 pm" (3 h), "3:00 – 6:00 pm" (3 h), "6:00 – 10:00 pm" (4 h) and "10:00 pm – 5:00 am" (7 h), and five activity columns, Sleep, Travel / personal, Lectures, Coursework and Skilling. Four tiles above it read Day accounted, Productive, Waking utilisation and Rest, and Day accounted shows today's logged hours "/ 24 h". |

---

## TC-221 — The day stepper moves one calendar day at a time and stops at today

| Field | Value |
|---|---|
| ID | TC-221 |
| Module | Time Allocation Ledger |
| Priority | P1 |
| Type | Functional, regression |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-221` |

Each click loads its day, and the screen draws only the day clicked last,
whatever order the loads finish in: click Next day twice quickly from the
day before yesterday and the screen settles on today, with Next day
disabled, even when yesterday's answer arrives after today's. The same holds
for the buttons that write: Submit day submits the day it was pressed on, and
a step taken while it is still saving leaves that day a saved draft and
submits nothing. The steps below wait for each day, as a person does; the
answers arriving out of order are covered by the unit tests in
`apps/web/src/app/features/student/ledger/ledger.component.spec.ts`.

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.
3. Worth running from a browser set to an Indian time zone as well: the
   stepper once skipped two days back and could not move forward there.
4. Wait for each day's hours to load before the next click.

### Test data

None beyond the seeded student.

### Steps

1. Open the Time Allocation Ledger at `/student/time-log`.
2. Click Previous day.
3. Click Previous day again.
4. Click Next day.
5. Click Next day again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The date control shows yesterday, yesterday's chip in the "Last 14 days" strip is highlighted as the day on screen, and Next day becomes enabled. |
| ER-2 | 3 | The date control shows the day before yesterday, and that day's chip is highlighted. |
| ER-3 | 4 | The date control shows yesterday again. |
| ER-4 | 5 | The date control shows today, the "Today" chip is highlighted, and Next day is disabled again, because a day that has not happened cannot be logged. |

---

## TC-222 — Saving a draft keeps the hours and updates the day's totals

| Field | Value |
|---|---|
| ID | TC-222 |
| Module | Time Allocation Ledger |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-222` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.
3. At least one day in the edit window (today and the 2 days before it) is
   neither submitted nor locked. Submitting cannot be undone (TC-228), so
   once all three are submitted this case waits for the next day. The
   automated run records it, and every ledger case that needs an open day,
   as Skipped until then, because used-up test data is not a broken
   environment. A freshly seeded database always has open days.

### Test data

| Slot | Activity | Hours |
|---|---|---|
| 10:00 pm – 5:00 am | Sleep | 7 |
| 9:00 am – 12:00 pm | Lectures | 3 |
| 12:00 – 3:00 pm | Coursework | 2 |
| 3:00 – 6:00 pm | Skilling | 1.5 |

Every other cell is left blank: 13.5 hours in all.

### Steps

1. Open the Time Allocation Ledger at `/student/time-log`.
2. In the "Last 14 days" strip, click the most recent day that is neither "Submitted" nor locked.
3. Enter the test data hours, leaving every other cell blank.
4. Click Save draft.
5. Reload the page and click the same day in the strip again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The date control shows that day, its chip in the strip is highlighted, and a chip under the table reads "Open until" and the date two days after that day, e.g. "Open until Fri 25 Sep" for 23 Sep. |
| ER-2 | 3 | The Logged column follows the typing: "10:00 pm – 5:00 am" reads "7 /7" "Balanced", "9:00 am – 12:00 pm" "3 /3" "Balanced", "12:00 – 3:00 pm" "2 /3" "1 h open", "3:00 – 6:00 pm" "1.5 /3" "1.5 h open", and "5:00 – 9:00 am" "0 /4" "Empty". The Day total row reads "13.5 /24" and "10.5 h to reconcile". Save draft is enabled. |
| ER-3 | 4 | The tiles update: Day accounted "13.5" "/ 24 h" with "10.5 h to reconcile", and Productive "6.5" "h". Under the table a chip reads "10.5 h to reconcile before you can submit." The day's chip in the strip reads "Draft · 13.5 h". Save draft is disabled again. |
| ER-4 | 5 | The hours are still there: the four cells hold 7, 3, 2 and 1.5, and the Day total row reads "13.5 /24". |

### Post-conditions

The day holds the 13.5-hour draft. The automated test puts back the hours
the day had before. A day that had nothing logged is left as a saved draft
with no hours, whose chip reads "Draft · 0 h".

---

## TC-223 — A slot cannot be saved with more hours than its capacity

| Field | Value |
|---|---|
| ID | TC-223 |
| Module | Time Allocation Ledger |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-223` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.
3. At least one day in the edit window is neither submitted nor locked (see
   TC-222).

### Test data

| Slot | Activity | Hours |
|---|---|---|
| 9:00 am – 12:00 pm (3 h capacity) | Lectures | 5 |

### Steps

1. Open the Time Allocation Ledger at `/student/time-log`.
2. In the "Last 14 days" strip, click the most recent day that is neither "Submitted" nor locked.
3. In the "9:00 am – 12:00 pm" row, enter 5 under Lectures and clear the other four cells.
4. Click Save draft.
5. Reload the page and click the same day in the strip again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The row's Logged column reads "5 /3" with the chip "2 h over". |
| ER-2 | 4 | The save is refused with the message "9:00 am – 12:00 pm holds 3 h — Lectures cannot be 5 h." |
| ER-3 | 5 | Nothing was saved: the "9:00 am – 12:00 pm" row holds the hours it had before step 3. |

---

## TC-224 — A day that does not add up to 24 hours cannot be submitted

| Field | Value |
|---|---|
| ID | TC-224 |
| Module | Time Allocation Ledger |
| Priority | P1 |
| Type | Functional, validation |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-224` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.
3. At least one day in the edit window is neither submitted nor locked (see
   TC-222).

### Test data

| Slot | Activity | Hours |
|---|---|---|
| 5:00 – 9:00 am | Travel / personal | 1 |
| 5:00 – 9:00 am | Coursework | 3 |
| 9:00 am – 12:00 pm | Lectures | 3 |
| 12:00 – 3:00 pm | Lectures | 1 |
| 12:00 – 3:00 pm | Coursework | 2 |
| 3:00 – 6:00 pm | Skilling | 2.5 |
| 6:00 – 10:00 pm | Travel / personal | 3 |
| 6:00 – 10:00 pm | Skilling | 1 |
| 10:00 pm – 5:00 am | Sleep | 7 |

Every other cell is left blank: 23.5 hours in all, half an hour short.

### Steps

1. Open the Time Allocation Ledger at `/student/time-log`.
2. In the "Last 14 days" strip, click the most recent day that is neither "Submitted" nor locked.
3. Enter the test data hours, leaving every other cell blank.
4. Click Save draft.
5. Change the Skilling hours in the "3:00 – 6:00 pm" row from 2.5 to 3.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The Day total row reads "23.5 /24" and "0.5 h to reconcile", the "3:00 – 6:00 pm" row reads "2.5 /3" "0.5 h open", and Submit day is disabled. |
| ER-2 | 4 | Day accounted reads "23.5" "/ 24 h" with "0.5 h to reconcile", a chip under the table reads "0.5 h to reconcile before you can submit.", and Submit day is still disabled. |
| ER-3 | 5 | The Day total row reads "24 /24" and "Reconciled", the "3:00 – 6:00 pm" row reads "3 /3" "Balanced", and Submit day becomes enabled. Do not click it: submitting cannot be undone (TC-228). |

### Post-conditions

The day holds the 23.5-hour draft saved in step 4. The automated test puts
back the hours the day had before.

---

## TC-225 — The weekly skilling strip adds up the Skilling hours logged this week

| Field | Value |
|---|---|
| ID | TC-225 |
| Module | Time Allocation Ledger: weekly skilling |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-225` |

### Pre-conditions

1. The dev seed has been applied. The student's weekly skilling target is
   12 hours.
2. The browser is signed in as the seeded student.
3. At least one day in the edit window is neither submitted nor locked (see
   TC-222).

### Test data

| Slot | Activity | Hours |
|---|---|---|
| 5:00 – 9:00 am | Skilling | 4 |

Every other cell is left blank.

### Steps

1. Open the Time Allocation Ledger at `/student/time-log` and note the hours in "Skilling this week".
2. In the "Last 14 days" strip, click the most recent day that is neither "Submitted" nor locked.
3. Enter the test data hours, leaving every other cell blank.
4. Click Save draft.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | Under the table, "Skilling this week · X h of a 12 h target" and a chip with X as a whole percentage of 12. X adds up the Skilling hours of today and the six days before it: a day's Skilling cells on the ledger, or, for a day with no hours on the ledger, its entries in the old time log. On the day the dev seed ran, X is 9.5 and the chip reads 79%. |
| ER-2 | 4 | X changes by exactly what the save changed: it loses the day's previous Skilling hours and gains 4. The chip shows the new X as a whole percentage of 12, capped at 100%. |

### Post-conditions

The day holds the 4-hour draft. The automated test puts back the hours the
day had before.

---

## TC-226 — A day past its edit window is locked

| Field | Value |
|---|---|
| ID | TC-226 |
| Module | Time Allocation Ledger |
| Priority | P1 |
| Type | Functional, negative |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-226` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.
3. The "Last 14 days" strip holds at least one locked day that was never
   submitted, marked "Locked" or "N h · locked". Every day older than the
   day before yesterday is locked.

### Test data

None beyond the seeded student.

### Steps

1. Open the Time Allocation Ledger at `/student/time-log`.
2. In the "Last 14 days" strip, click the most recent locked day that was not submitted.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The date control shows that day. A red chip under the table reads "DD Mon YYYY is locked — it could be filled in until DD Mon YYYY. A day stays open for 2 days after it ends.", with the day's date and the date two days after it, e.g. "20 Sep 2026 is locked — it could be filled in until 22 Sep 2026. A day stays open for 2 days after it ends." |
| ER-2 | 2 | The header button reads "Locked" and is disabled, every hour cell is disabled, and there is no Save draft button. |

---

## TC-227 — Copy yesterday fills an open day from the previous submitted day

| Field | Value |
|---|---|
| ID | TC-227 |
| Module | Time Allocation Ledger |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-227` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.
3. A day in the edit window is neither submitted nor locked, and the day
   before it is "Submitted". On the day the seed ran, the seed's own day
   qualifies. The automated test submits an earlier open day through the API
   when no day qualifies.

### Test data

None beyond the seeded student.

### Steps

1. Open the Time Allocation Ledger at `/student/time-log`.
2. In the "Last 14 days" strip, click the most recent open day whose previous day reads "Submitted".
3. Click Copy yesterday.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | Every cell takes the hours of the submitted day before it: the Day total row reads "24 /24" and "Reconciled", and Submit day is enabled. The copy does not submit the day: its chip reads "Draft · 24 h". |

### Post-conditions

The day holds the copied hours as a draft. The automated test puts back the
hours the day had before.

---

## TC-228 — Submitting a day that adds up to 24 hours closes it

| Field | Value |
|---|---|
| ID | TC-228 |
| Module | Time Allocation Ledger |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-228` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.
3. At least one day in the edit window is neither submitted nor locked (see
   TC-222).

### Test data

The 24-hour day: the test data of TC-224 with 3 hours, not 2.5, under
Skilling in the "3:00 – 6:00 pm" row.

### Steps

1. Open the Time Allocation Ledger at `/student/time-log`.
2. In the "Last 14 days" strip, click the oldest day that is neither "Submitted" nor locked.
3. Enter the test data hours, leaving every other cell blank.
4. Click Submit day.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The date control shows that day, and its chip in the strip is highlighted. |
| ER-2 | 3 | The Day total row reads "24 /24" and "Reconciled", every slot row reads "Balanced", and Submit day is enabled. |
| ER-3 | 4 | The button reads "Submitted" and is disabled, a chip under the table reads "Submitted — this day is closed", every hour cell is disabled, and Save draft is gone. |
| ER-4 | 4 | The day's chip in the strip reads "Submitted", and the strip's "N submitted" count is one higher than before. |

### Post-conditions

The day stays submitted for good: nothing in the product reopens a
submitted day. Each run uses up one of the at most three open days, so after
three runs in one day TC-222 to TC-228 wait for the next day, or a database
reset.

---

## TC-230 — The Courses screen shows progress on each enrolled course

| Field | Value |
|---|---|
| ID | TC-230 |
| Module | Courses |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-230` |

### Pre-conditions

1. The dev seed has been applied, with the student enrolled in 22MBA11 and
   22MBA12.
2. The browser is signed in as the seeded student.

### Test data

None beyond the seeded student.

### Steps

1. Open the Records screen at `/student/records`.
2. Click "Subject-by-subject progress".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The Courses screen opens at `/student/courses`, headed "Courses", with the line "2 enrolled · 2 in progress · 0 completed". |
| ER-2 | 2 | A card for "Management & Organisational Behaviour" reads "22MBA11 · Sem 1 · Excel", "In progress", "90% complete" beside a progress bar at 90, "Next: Attend lecture 19 of 20", "18/20 lectures", "2 lectures left" and "Unlocks: Progresses your Excel stage", with a Continue button. |
| ER-3 | 2 | A card for "Managerial Economics" reads "22MBA12 · Sem 1 · Excel", "In progress", "80% complete" beside a progress bar at 80, "Next: Attend lecture 17 of 20", "16/20 lectures", "4 lectures left" and "Unlocks: Progresses your Excel stage". |

---

## TC-240 — Records show the student's VTU results, attendance and academic history

| Field | Value |
|---|---|
| ID | TC-240 |
| Module | Records |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-240` |

### Pre-conditions

1. The dev seed has been applied, and no results, attendance or
   qualifications have been imported for the student since.
2. The browser is signed in as the seeded student.

### Test data

None beyond the seeded student.

### Steps

1. Open the Records screen at `/student/records`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | Four figures head the page: Latest CGPA "8.2", Semesters on record "1", Live backlogs "0" and Overall attendance "85%". |
| ER-2 | 1 | Under "Semester Results", a "Semester 1" card reads "CGPA 8.2 · SGPA 8.2 · FIRST CLASS WITH DISTINCTION" with a "No live backlogs" chip. Its table lists 22MBA11, Management & Organisational Behaviour, 4 credits, 42 internal, 40 external, 82 total, "Pass"; and 22MBA12, Managerial Economics, 4, 38, 36, 74, "Pass". |
| ER-3 | 1 | Under "Attendance": "85%", "34 of 40 classes attended" and a chip "85% · On track"; by course, 22MBA11 "18/20" "90%" and 22MBA12 "16/20" "80%". |
| ER-4 | 1 | Under "Academic History", the same three qualifications as on the student home ("10th Standard", "12th Standard" and "Undergraduate"), and "Declared education gaps" with "No gaps declared". |

---

## TC-241 — A student with nothing imported sees "not recorded yet", never a zero

| Field | Value |
|---|---|
| ID | TC-241 |
| Module | Records and student home: empty states |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | None (manual only): it needs a student account with no imported marks, attendance or qualifications. The dev seed has one student, who has all three, and a new student can only get a password through the codes and links the onboarding walk emails. |

### Pre-conditions

1. A student account exists with no VTU results, no attendance and no
   academic history on record: for example, an applicant approved on
   `/admin/registrations` who has completed the `/onboard` walk (module 02).
2. The browser is signed in as that student.

### Test data

The new student's own email address and password.

### Steps

1. Open the Records screen at `/student/records`.
2. Open the student home at `/student`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | No figures head the page, and each section says why it is empty: "No semester results yet. They appear here once the examination office imports your VTU marks.", "No attendance recorded yet. It appears here once classes are marked." and "No qualifications on record yet. Your 10th, 12th and prior-degree record appears here once the office adds it to your student record." |
| ER-2 | 2 | The Attendance card reads "No attendance recorded yet." and the VTU marks card "No semester results published yet." No attendance, CGPA or marks figure of zero is shown. |
| ER-3 | 2 | The readiness card shows "—" over "not scored", a grey "Not assessed yet" chip, and a summary beginning "Not assessed yet. Not enough is on record to score this yet — nothing is recorded for CGPA, Live backlogs, Attendance". CGPA, Live backlogs and Attendance each carry a grey "Not measured" chip, with "No semester results on record yet — the 6.0 cut-off has nothing to read", "No semester results on record yet; the limit is 0" and "No attendance recorded yet; 85.0% is required once it is". |

---

## TC-250 — Leaderboards open on the Overall board, ranked within the student's batch

| Field | Value |
|---|---|
| ID | TC-250 |
| Module | Leaderboards |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-250` |

### Pre-conditions

1. The dev seed has been applied. The student is seated in batch
   "2024-26 Section B" of the MBA (Finance), and no other student in that
   batch has a record on any board. Batch mates added by TC-252 and TC-253
   have none.
2. The browser is signed in as the seeded student, who is not hidden from
   the leaderboards.

### Test data

None beyond the seeded student.

### Steps

1. Open the Leaderboards screen at `/student/leaderboards`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The line under the heading reads "Ranked within your batch · Master of Business Administration - Finance · 2024-26 Section B — everyone in it sees the same names, positions and totals." |
| ER-2 | 1 | Five tabs are offered, Overall, Skills, VTU results, Streak and Mocks taken, and Overall is selected. |
| ER-3 | 1 | The student's own card reads "You’re Rank 1 of 1", "You’re the only ranked student here right now — a strong start." and "100 pts". |
| ER-4 | 1 | The note reads "Ranked by skills, VTU results, streak and mocks together — each is worth up to 25 points, scaled against the best in your batch, and the four add up to 100. Only classmates with a record here are ranked; equal totals share a rank. Updates as records change." |
| ER-5 | 1 | The table has one row: rank 1, "Test Student" with a "You" chip, "of 1", "100 pts". |

---

## TC-251 — Each leaderboard tab ranks the student by its own measure

| Field | Value |
|---|---|
| ID | TC-251 |
| Module | Leaderboards |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-251` |

### Pre-conditions

1. The dev seed has been applied: the student holds two verified badges, a
   semester 1 CGPA of 8.2 and two mock attempts.
2. The browser is signed in as the seeded student, who is not hidden from
   the leaderboards.

### Test data

None beyond the seeded student.

### Steps

1. Open the Leaderboards screen at `/student/leaderboards`.
2. Click the Skills tab.
3. Click the VTU results tab.
4. Click the Streak tab.
5. Click the Mocks taken tab.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The Skills tab is selected, the student's total reads "2 skills", and the note begins "Ranked by skills verified on Skilling." |
| ER-2 | 3 | The total reads "CGPA 8.20", and the note begins "Ranked by your latest recorded CGPA." |
| ER-3 | 4 | The total reads "N active days", N being the number of days the student has signed in on (5 on the day the dev seed ran), and the note begins "Ranked by active-day count." |
| ER-4 | 5 | The total reads "2 mocks", and the note begins "Ranked by mocks completed." |

---

## TC-252 — A batch mate with no record is listed as not ranked

| Field | Value |
|---|---|
| ID | TC-252 |
| Module | Leaderboards |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-252` |

### Pre-conditions

1. The dev seed has been applied.
2. A second student, with no verified skill, is seated in the student's
   batch, "2024-26 Section B": for example, an application naming that
   batch, approved by the Main Admin on `/admin/registrations`. The
   automated test creates one named "E2E Mate" and a run id, through
   `POST /api/register` and the Main Admin's approval.
3. At least a minute has passed since that approval, because each board is
   cached for 60 seconds.
4. The browser is signed in as the seeded student, who is not hidden from
   the leaderboards.

### Test data

| Field | Value |
|---|---|
| Batch mate | `E2E Mate` and a run id, e.g. `E2E Mate k3x9a` |

### Steps

1. Open the Leaderboards screen at `/student/leaderboards`.
2. Click the Skills tab.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Below the ranking, a "Not ranked on this board yet" card lists the batch mate by name, under the line "N of M classmates · Get a skill verified on Skilling and you’ll appear on this board.", N counting the unranked batch mates and M the whole batch. |
| ER-2 | 2 | The ranking table lists only students with a verified skill: "Test Student" is ranked with "2 skills", and the batch mate is not in the table. |

### Post-conditions

The batch mate stays seated in the batch. The automated test removes them
as the Main Admin, with a reason, when it ends.

---

## TC-253 — A removed batch mate leaves the leaderboard

| Field | Value |
|---|---|
| ID | TC-253 |
| Module | Leaderboards |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-253` |

### Pre-conditions

1. The dev seed has been applied.
2. A second student is seated in the student's batch, as in TC-252, and at
   least a minute has passed since their approval.
3. A second browser, or a private window, is signed in as the Main Admin
   (`admin@bgscet.ac.in` / `admin123`).
4. The student's browser is signed in as the seeded student, who is not
   hidden from the leaderboards.

### Test data

| Field | Value |
|---|---|
| Batch mate | `E2E Mate` and a run id |
| Reason for removal | `E2E: removed to check the leaderboard` |

### Steps

1. Open the Leaderboards screen at `/student/leaderboards`.
2. As the Main Admin, remove the batch mate from the roster with the reason in the test data.
3. Wait a minute, then reload the leaderboard.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The batch mate is listed under "Not ranked on this board yet". |
| ER-2 | 3 | The batch mate is no longer listed. |

### Post-conditions

The batch mate is removed: off the roster, with their record kept. The Main
Admin can restore them from the roster's "Removed" filter.

---

## TC-254 — A student hidden from the leaderboards can take part again

| Field | Value |
|---|---|
| ID | TC-254 |
| Module | Leaderboards |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/03-student-progress.spec.ts`, title tagged `@TC-254` |

### Pre-conditions

1. The dev seed has been applied.
2. The browser is signed in as the seeded student.
3. The student is hidden from the leaderboards: "Hide me from the
   leaderboards" is ticked and saved on `/student/profile`. The automated
   test sets it through `PUT /api/student/leaderboard-visibility`.

### Test data

None beyond the seeded student.

### Steps

1. Open the Leaderboards screen at `/student/leaderboards`.
2. Click Take part again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | No ranking is shown. A card reads "You’re hidden from the leaderboards" and "You don’t appear on any board and, in turn, you don’t see peer rankings.", with a "Take part again" button and the note "Your mentors and placement staff can always see your records — the opt-out only hides you from classmates on these boards." |
| ER-2 | 2 | A status line reads "You’re now visible on the leaderboards.", and the Overall board appears with "Test Student" ranked and marked "You". |

### Post-conditions

The student is visible on the leaderboards again, as on the seed.
