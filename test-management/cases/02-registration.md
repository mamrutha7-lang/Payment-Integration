# Registration and onboarding

| Field | Value |
|---|---|
| Screens | `/register`, `/admin/registrations`, `/onboard` |
| Automated tests | [`tests/02-registration.spec.ts`](../../tests/02-registration.spec.ts) |
| ID range | TC-100 to TC-199 |

Setup, the seeded accounts and the rules that link these cases to their
automated tests are in [the index](../manual-test-cases.md).

What these cases rely on, in addition to the index's setup:

- **The seeded institution.** The dev seed creates one college, "BGS College
  of Engineering and Technology", with the department "Department of
  Management Studies", the course "Master of Business Administration" (code
  MBA), its one specialization "Finance", and one batch, "2024-26 Section B",
  which ended on 31 July 2026. It also seeds two auto-approve rules
  ("MBA 2024-26 auto-admit", priority 10, and "College domain — route to
  review", priority 100) and three applications: Asha Rao (auto-approved),
  Ravi Kumar (pending) and Nikhil Shetty (held).
- **The test college.** Several cases need a department with no courses and a
  course with three specializations, which the seed does not have. They use
  the college "E2E Registration College" (code `E2E02`), with no email domains
  of its own, and two departments: "E2E Open Department" (code `OPEN`), with no
  courses and no batches, and "E2E Dual Department" (code `DUAL`), with one
  course, "E2E Dual Programme" (code `E2EDP`), which has three specializations,
  "E2E Analytics", "E2E Finance" and "E2E Marketing", and no batch. The
  automated tests create whatever is missing through the console's API and
  archive the college when the spec finishes, so the public form does not
  offer it between runs. By hand, create it once on College setup
  (`/admin/setup`), or restore it on Colleges (`/admin/colleges`).
- **The rate limit on the form.** The API accepts 20 submissions of the
  registration form per network address per 10 minutes. It counts in memory,
  so restarting the API clears it. Behind the web app's development proxy,
  every browser on the machine counts as one address. Past the limit the form
  shows "Too many registration attempts from this network. Please try again
  shortly." A full automated run submits 10 applications on a fresh database
  and 7 after that, so two full runs within 10 minutes fit, and a third does
  not unless the API is restarted first. Dropping and recreating the database
  does not clear the count; only restarting the API does. The automated run
  marks a test Blocked, not Failed, if it meets the limit.
- **No mail transport.** On a development server with `SES_FROM_ADDRESS`
  blank, emails are not sent. The API logs each one to its console as
  `MAIL (no transport configured) to=<address> subject='<subject>'`, followed
  by the text. The expected results about email are marked **Manual only.**

---

## TC-100 — The registration form lists the colleges, departments, courses and batches the office has set up

| Field | Value |
|---|---|
| ID | TC-100 |
| Module | Registration: the public form |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-100` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied, so the seeded institution
   exists.
2. Nobody needs to be signed in. The form is public.

### Test data

| Field | Value |
|---|---|
| College | BGS College of Engineering and Technology |
| Department | Department of Management Studies |
| Course | MBA · Master of Business Administration |

### Steps

1. Open the registration page at `/register`.
2. In the College list, choose "BGS College of Engineering and Technology".
3. In the Department list, choose "Department of Management Studies".
4. In the Course list, choose "MBA · Master of Business Administration".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Student registration", with the line "Every box marked * is required — everything except Specialization — and so are your CV and your photo." Full name, USN, College email, Personal email, Phone, LinkedIn profile, College, Department and Degree level are each marked *. Degree level shows "Post Graduate (PG)". The Department list is disabled and reads "Choose a college first", the Course list reads "Choose a department first", and under Specialization it says "Choose a course first". |
| ER-2 | 2 | The College list offers "BGS College of Engineering and Technology". Once it is chosen, the Department list is enabled and offers "Department of Management Studies". |
| ER-3 | 3 | The Course list is enabled, is now marked * ("Course *") and offers "MBA · Master of Business Administration". The Batch list is marked * ("Batch *") and offers "2024-26". |
| ER-4 | 4 | Under Specialization, which has no *, the form says "Tick one, or 2 if you opted for a dual specialization." and shows one tick box, "Finance", not ticked. |

---

## TC-101 — An empty form is refused with a list of everything missing

| Field | Value |
|---|---|
| ID | TC-101 |
| Module | Registration: the public form |
| Priority | P1 |
| Type | Functional, negative |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-101` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.

### Test data

| Field | Value |
|---|---|
| Full name | `E2E Empty Form` |

### Steps

1. Open the registration page at `/register`.
2. Click Submit registration without filling in anything.
3. Type the full name in the Full name box.
4. Click Submit registration again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A message appears above the button: "Please add your CV (PDF), your full name, your USN, your college email, your personal email, your phone number, your LinkedIn profile, your college, your department and your photo (PNG or JPG)." |
| ER-2 | 2 | Nothing is sent to the server. The form stays on the page, with the Submit registration button. |
| ER-3 | 4 | The message now leaves out the name: "Please add your CV (PDF), your USN, your college email, your personal email, your phone number, your LinkedIn profile, your college, your department and your photo (PNG or JPG)." Nothing is sent to the server. |

---

## TC-102 — Course and Batch are required only where the office has listed some

| Field | Value |
|---|---|
| ID | TC-102 |
| Module | Registration: the public form |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-102` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied.
2. The test college exists and is active, with "E2E Open Department"
   holding no courses and no batches (see the notes at the top of this file).

### Test data

| Field | Value |
|---|---|
| Full name | `E2E Course Optional` |
| USN | `E2E102` |
| College email | `e2e-tc102@bgscet.ac.in` |
| Personal email | `e2e-tc102@example.org` |
| Phone | `+91 90000 00102` |
| LinkedIn profile | `linkedin.com/in/e2e-tc102` |
| CV | `tests/fixtures/sample.pdf` |
| Photo | none: it is left out on purpose, so the form is never submitted |

### Steps

1. Open the registration page at `/register`.
2. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data.
3. Attach `sample.pdf` as the CV.
4. Choose the college "BGS College of Engineering and Technology" and the department "Department of Management Studies".
5. Click Submit registration.
6. Choose the college "E2E Registration College" and the department "E2E Open Department".
7. Click Submit registration.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | Course and Batch are both marked * ("Course *", "Batch *"), because this department lists a course and a batch. |
| ER-2 | 5 | The message reads "Please add your course, your batch and your photo (PNG or JPG)." |
| ER-3 | 6 | The Course list is disabled, reads "No courses listed" and has no *. The Batch list is disabled, reads "No batches listed" and has no *. Under Specialization it says "Choose a course first". |
| ER-4 | 7 | The message reads "Please add your photo (PNG or JPG)." It no longer asks for a course or a batch. Nothing is sent to the server. |

---

## TC-103 — The specialization checklist takes one tick, or two, and no more

| Field | Value |
|---|---|
| ID | TC-103 |
| Module | Registration: the public form |
| Priority | P2 |
| Type | Functional, boundary |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-103` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. The test college exists and is active, with "E2E Dual Department" holding
   the course "E2E Dual Programme" and its three specializations (see the
   notes at the top of this file).

### Test data

| Field | Value |
|---|---|
| College | E2E Registration College |
| Department | E2E Dual Department |
| Course | E2EDP · E2E Dual Programme |
| Specializations | E2E Analytics, E2E Finance, E2E Marketing |

### Steps

1. Open the registration page at `/register`.
2. Choose the college "E2E Registration College", the department "E2E Dual Department" and the course "E2EDP · E2E Dual Programme".
3. Tick "E2E Analytics".
4. Tick "E2E Finance".
5. Untick "E2E Finance".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Under Specialization the form says "Tick one, or 2 if you opted for a dual specialization." and shows three tick boxes, "E2E Analytics", "E2E Finance" and "E2E Marketing", none ticked and all enabled. The Batch list reads "No batches listed". |
| ER-2 | 3 | "E2E Analytics" is ticked. "E2E Finance" and "E2E Marketing" are still enabled. |
| ER-3 | 4 | "E2E Finance" is ticked as well. "E2E Marketing" is now disabled and cannot be ticked. The two ticked boxes stay enabled, so either can be unticked. |
| ER-4 | 5 | "E2E Finance" is unticked and "E2E Marketing" is enabled again. "E2E Analytics" is still ticked. |

---

## TC-104 — The CV and photo pickers state their limits and refuse the wrong file

| Field | Value |
|---|---|
| ID | TC-104 |
| Module | Registration: the public form |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-104` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. The test files are at hand: `tests/fixtures/sample.pdf` (1,411 bytes),
   `sample.png` and `sample.jpg` (2,884 bytes), and a PDF larger than 10 MB.

### Test data

| Field | Value |
|---|---|
| Wrong type for the CV | `sample.png` |
| Too large for the CV | `big-cv.pdf`, 11 MB (11,534,336 bytes). The automated test builds it in memory. The browser checks only its name, type and size, so its content does not matter. |
| Right CV | `sample.pdf` |
| Wrong type for the photo | `sample.pdf` |
| Right photo | `sample.jpg` |

### Steps

1. Open the registration page at `/register`.
2. Attach `sample.png` as the CV.
3. Attach `big-cv.pdf` as the CV.
4. Attach `sample.pdf` as the CV.
5. Attach `sample.pdf` as the photo.
6. Attach `sample.jpg` as the photo.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The CV box reads "Attach your CV *" with the limits "PDF only · up to 10 MB" and a "Choose file" button. The photo box reads "Upload a headshot" with the limits "PNG or JPG · up to 10 MB". |
| ER-2 | 2 | Under the CV box: "sample.png is not a PDF. Choose a PDF." The CV box still reads "Attach your CV". |
| ER-3 | 3 | Under the CV box: "big-cv.pdf is 11.0 MB; the limit is 10 MB." The file is not kept. |
| ER-4 | 4 | The message goes away. The CV box shows "sample.pdf · 1 KB" and its button reads "Change file". |
| ER-5 | 5 | Under the photo box: "sample.pdf is not a PNG or JPG. Choose a PNG or JPG." |
| ER-6 | 6 | The message goes away and the photo box shows "sample.jpg · 3 KB". |

---

## TC-105 — A complete application is received and held for review

| Field | Value |
|---|---|
| ID | TC-105 |
| Module | Registration: the public form |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-105` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied, so the rule "College domain —
   route to review" is enabled.
2. The test college exists and is active (see the notes at the top of this
   file).
3. No application that is still pending or held uses the college email, and
   the form's rate limit has not been reached (see the notes at the top of
   this file).

### Test data

The automated run makes the name, the addresses and the USN unique to the
run, shown here as `<run>`. By hand, use any values not used before.

| Field | Value |
|---|---|
| CV | `sample.pdf` |
| Full name | `E2E Held <run>` |
| USN | `E2E<run>H`: any USN that does not look like `1BG2nMBAnnn`, so no rule approves it |
| College email | `e2e-<run>-held@bgscet.ac.in` |
| Personal email | `e2e-<run>-held@example.org` |
| Phone | `+91 90000 00105` |
| LinkedIn profile | `linkedin.com/in/e2e-<run>-held` |
| College, department, course | E2E Registration College, E2E Dual Department, E2EDP · E2E Dual Programme |
| Specializations | E2E Analytics and E2E Finance |
| Photo | `sample.jpg` |

### Steps

1. Open the registration page at `/register`.
2. Attach `sample.pdf` as the CV.
3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data.
4. Choose the college "E2E Registration College", the department "E2E Dual Department" and the course "E2EDP · E2E Dual Programme".
5. Tick "E2E Analytics" and "E2E Finance".
6. Attach `sample.jpg` as the photo.
7. Click Submit registration.
8. Click Continue to sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 7 | The form is replaced by a result card: "Held for review. Routed by rule 'College domain — route to review' — awaiting review." |
| ER-2 | 7 | The card names where the applicant said they belong, with both specializations: "E2E Registration College · E2E Dual Department · E2E Dual Programme · E2E Analytics and E2E Finance". |
| ER-3 | 7 | The card says "Your CV and photo came with the application." and offers "Continue to sign in" and "Submit another". |
| ER-4 | 8 | The sign-in page opens at `/login`, headed "Welcome back". |

### Post-conditions

A pending application for the college email is in the office's queue. The
automated run rejects it afterwards with the reason "E2E cleanup: test
application from tests/02-registration.spec.ts", so no pending application is
left behind. By hand, reject it on `/admin/registrations` or leave it for
TC-111.

---

## TC-106 — An application that an auto-approve rule matches is approved at once

| Field | Value |
|---|---|
| ID | TC-106 |
| Module | Registration: the public form and the rule engine |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-106` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied, so the rule "MBA 2024-26
   auto-admit" is enabled. It auto-approves an application with an
   `@bgscet.ac.in` address, a USN matching `^1BG2[0-9]MBA[0-9]{3}$` and the
   degree level PG, and seats it in the batch "2024-26 Section B".
2. No student, current or removed, holds the USN in the test data, and no
   account uses the college email. The automated run picks a free USN by
   searching the roster first.
3. The form's rate limit has not been reached.

### Test data

| Field | Value |
|---|---|
| CV and photo | `sample.pdf` and `sample.png` |
| Full name | `E2E Auto <run>` |
| USN | a free USN of the form `1BG2nMBAnnn`, for example `1BG29MBA317` |
| College email | `e2e-<run>-auto@bgscet.ac.in` |
| Personal email | `e2e-<run>-auto@example.org` |
| Phone | `+91 90000 00106` |
| LinkedIn profile | `linkedin.com/in/e2e-<run>-auto` |
| College, department, course | BGS College of Engineering and Technology, Department of Management Studies, MBA · Master of Business Administration |
| Specialization | Finance |
| Batch | 2024-26 |
| Degree level | Post Graduate (PG), the default |

### Steps

1. Open the registration page at `/register`.
2. Attach `sample.pdf` as the CV and `sample.png` as the photo.
3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data.
4. Choose the college "BGS College of Engineering and Technology", the department "Department of Management Studies" and the course "MBA · Master of Business Administration".
5. Tick "Finance" and choose the batch "2024-26".
6. Click Submit registration.
7. Sign in as the Main Admin and open `/admin/registrations`.
8. Click the Auto-approved tab.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 6 | The result card reads: "A seating rule approved your application. Auto-approved by rule 'MBA 2024-26 auto-admit'. We have emailed `<college email>` a setup link. Open it, confirm the address with the code we send you, and choose a password — approval created your account but no password, and that link is how you get one." |
| ER-2 | 6 | The card names "BGS College of Engineering and Technology · Department of Management Studies · Master of Business Administration · Finance · Batch 2024-26". |
| ER-3 | 6 | **Manual only.** An email with the subject "Your REEP account is approved - set it up" goes to the college email. It holds a link to `/onboard?token=…` that expires in 7 days. Without a mail transport, the API console logs `MAIL (no transport configured) to=<college email> subject='Your REEP account is approved - set it up'`. |
| ER-4 | 8 | The Auto-approved tab lists the application, with the applicant's name, college email and USN. |

### Post-conditions

A student account exists for the college email, with no password, seated in
the batch "2024-26 Section B", and the CV and photo are its first uploads.
The automated run then removes the account from the roster (the console's
Remove, which keeps its records), so the roster is as it was. The
application stays on the Auto-approved tab.

---

## TC-107 — A second application from an address with a live one is refused without saying why

| Field | Value |
|---|---|
| ID | TC-107 |
| Module | Registration: the public form |
| Priority | P1 |
| Type | Functional, negative, security |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-107` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. A pending application exists for `e2e-m02-fixture@bgscet.ac.in`, named
   "E2E Fixture Applicant". The automated run creates it through the API if
   it is missing, and puts it back in the queue if it was held or rejected.
   By hand, submit it on `/register` first, with the values in TC-111's test
   data.
3. The form's rate limit has not been reached.

### Test data

| Field | Value |
|---|---|
| College email | `e2e-m02-fixture@bgscet.ac.in`, the address that already has a live application |
| Full name | `E2E Duplicate <run>` |
| USN | `E2E<run>D` |
| Personal email | `e2e-<run>-dup@example.org` |
| Phone | `+91 90000 00107` |
| LinkedIn profile | `linkedin.com/in/e2e-<run>-dup` |
| College, department, course, batch | as in TC-106 |
| CV and photo | `sample.pdf` and `sample.jpg` |

### Steps

1. Open the registration page at `/register`.
2. Attach `sample.pdf` as the CV and `sample.jpg` as the photo.
3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data.
4. Choose the college "BGS College of Engineering and Technology", the department "Department of Management Studies", the course "MBA · Master of Business Administration" and the batch "2024-26".
5. Click Submit registration.
6. Sign in as the Main Admin, open `/admin/registrations` and type `e2e-m02-fixture@bgscet.ac.in` in the quick filter.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 5 | A message reads "This application could not be accepted. If you have already applied, or you think this is a mistake, contact the placement office." It does not say that an application exists for the address. |
| ER-2 | 5 | No result card appears. The form stays filled in, so the applicant can correct it. |
| ER-3 | 6 | The Pending tab has exactly one application for the address, the original from "E2E Fixture Applicant". No second application was created. |

### Post-conditions

The automated run rejects the fixture application afterwards, so no pending
application is left behind. Its next run puts it back in the queue.

---

## TC-108 — A file that is not really a PDF is refused and leaves no application behind

| Field | Value |
|---|---|
| ID | TC-108 |
| Module | Registration: the public form |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-108` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. No live application uses the college email, and the form's rate limit
   leaves room for two submissions.

### Test data

| Field | Value |
|---|---|
| Fake CV | `not-a-pdf.pdf`: a copy of `sample.png` renamed with a `.pdf` ending. The automated test sends `sample.png`'s bytes under that name. |
| Photo | `sample.png` |
| Real CV | `sample.pdf` |
| Full name | `E2E File Check <run>` |
| USN | `E2E<run>F` |
| College email | `e2e-<run>-file@bgscet.ac.in` |
| Personal email | `e2e-<run>-file@example.org` |
| Phone | `+91 90000 00108` |
| LinkedIn profile | `linkedin.com/in/e2e-<run>-file` |
| College, department, course, batch | as in TC-106 |

### Steps

1. Open the registration page at `/register`.
2. Attach `not-a-pdf.pdf` as the CV and `sample.png` as the photo.
3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data.
4. Choose the college "BGS College of Engineering and Technology", the department "Department of Management Studies", the course "MBA · Master of Business Administration" and the batch "2024-26".
5. Click Submit registration.
6. Attach `sample.pdf` as the CV instead.
7. Click Submit registration again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The browser accepts the renamed file, because it goes by the name: the CV box shows "not-a-pdf.pdf · 1 KB" and no message. |
| ER-2 | 5 | The server reads the file's contents and refuses it: "The cv must be a PDF." No result card appears. |
| ER-3 | 7 | The application is accepted: "Held for review. Routed by rule 'College domain — route to review' — awaiting review." The refused attempt left no application behind, or this one would have been refused as a duplicate (TC-107). |

### Post-conditions

A pending application for the college email is in the queue. The automated
run rejects it afterwards.

---

## TC-109 — A rejected applicant can apply again, and the reviewer sees the earlier rejection

| Field | Value |
|---|---|
| ID | TC-109 |
| Module | Registration: the public form and the review queue |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-109` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. The address `e2e-m02-reapply@bgscet.ac.in` has at least one rejected
   application, the latest rejected with the reason "E2E: the USN on the form
   was mistyped.", and no application that is pending or held. The automated
   run sets this up through the API: on a fresh database it submits an
   application and rejects it, and every run rejects its own new application
   afterwards, with the same reason.
3. The form's rate limit has not been reached.

### Test data

| Field | Value |
|---|---|
| College email | `e2e-m02-reapply@bgscet.ac.in` |
| Full name | `E2E Reapplicant` |
| USN | `E2EM02RE2`, the corrected USN |
| Personal email | `e2e-m02-reapply@example.org` |
| Phone | `+91 90000 00109` |
| LinkedIn profile | `linkedin.com/in/e2e-m02-reapply` |
| College, department, course, batch | as in TC-106 |
| CV and photo | `sample.pdf` and `sample.jpg` |
| Reason given for the earlier rejection | `E2E: the USN on the form was mistyped.` |

### Steps

1. Open the registration page at `/register`.
2. Attach `sample.pdf` as the CV and `sample.jpg` as the photo.
3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data.
4. Choose the college "BGS College of Engineering and Technology", the department "Department of Management Studies", the course "MBA · Master of Business Administration" and the batch "2024-26".
5. Click Submit registration.
6. Sign in as the Main Admin, open `/admin/registrations` and type `e2e-m02-reapply@bgscet.ac.in` in the quick filter.
7. Click the applicant's row.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 5 | The application is accepted, not refused as a duplicate: "Held for review. Routed by rule 'College domain — route to review' — awaiting review." |
| ER-2 | 7 | The first line under Checks reads "Applied before - rejected once, last on `<date>`", with the date as, for example, "22 Sep 2026". After repeated runs it reads "rejected 2 times", "3 times", and so on. |
| ER-3 | 7 | Its detail reads: 'The reason given then: "E2E: the USN on the form was mistyped." This is a fresh application; the earlier one is on the Rejected tab.' |

### Post-conditions

A new pending application exists for the address. The automated run rejects
it afterwards with the same reason, so the next run finds the same state with
one more rejection on record.

---

## TC-110 — The review queue lists pending, auto-approved and held applications on their own tabs

| Field | Value |
|---|---|
| ID | TC-110 |
| Module | Registrations: the review queue |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-110` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied, so Ravi Kumar's application is
   pending, Asha Rao's was auto-approved and Nikhil Shetty's is held.
2. The seeded rules are unchanged: exactly one enabled rule auto-approves.

### Test data

| Account | Main Admin: `admin@bgscet.ac.in` / `admin123` |
|---|---|

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. Click the Auto-approved tab.
3. Type `1bg24mba045@bgscet.ac.in` in the quick filter and click Asha Rao's row.
4. Clear the quick filter and click the Held tab.
5. Click Nikhil Shetty's row.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "New applications", with the line "Students who applied through the sign-up form." under it. The tabs are "Pending · `<n>`", "Auto-approved", "Held" and "Rejected", and the line "1 rule auto-approves; everything else waits here for a decision." is shown. The Pending tab lists Ravi Kumar, `ravi.kumar@bgscet.ac.in`, with the USN "Not on record" and the email domain "bgscet.ac.in". |
| ER-2 | 2 | The Pending tab's label loses its count and reads "Pending", and Ravi Kumar's pending application is no longer listed. |
| ER-3 | 3 | One row is left: Asha Rao, `1bg24mba045@bgscet.ac.in`, USN "1BG24MBA045". The panel shows her name, her address, the batch "Master of Business Administration - Finance · 2024-26 Section B" and, under Checks, "No checklist: it is computed for applications still waiting on a decision, and this one has been decided." It offers no Approve & invite, Reject or Hold button. |
| ER-4 | 4 | The Held tab lists Nikhil Shetty, `nikhil.shetty@bgscet.ac.in`, and the toolbar offers "Approve", "Reject" and "Release hold". |
| ER-5 | 5 | The panel shows an "On hold" section with the note "No CV attached, and the USN on the form is one digit short. Asked him to resend both." and the line "Held `<date, time>` · internal to the office. Nothing was emailed and the applicant's own page is unchanged." Documents reads "None". |

---

## TC-111 — The reviewer's panel shows the applicant, the checks and the attached files

| Field | Value |
|---|---|
| ID | TC-111 |
| Module | Registrations: the review queue |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-111` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. A pending application exists for `e2e-m02-fixture@bgscet.ac.in` with the
   values below, a CV and a photo. The automated run creates it through the
   API if it is missing, and puts it back in the queue if it was held or
   rejected. By hand, submit it on `/register`.

### Test data

The fixture application:

| Field | Value |
|---|---|
| Full name | `E2E Fixture Applicant` |
| USN | `E2EM02FIX` |
| College email | `e2e-m02-fixture@bgscet.ac.in` |
| Personal email | `e2e-m02-fixture@example.org` |
| Phone | `+91 90000 00002` |
| LinkedIn profile | `linkedin.com/in/e2e-m02-fixture` |
| College, department, course | E2E Registration College, E2E Dual Department, E2EDP · E2E Dual Programme |
| Specializations | E2E Analytics and E2E Finance |
| Batch | none |
| CV and photo | `sample.pdf` and `sample.png` |

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. Type `e2e-m02-fixture@bgscet.ac.in` in the quick filter.
3. Click the applicant's row.
4. Click Open CV.
5. Click Open photo.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | One row is left: E2E Fixture Applicant, `e2e-m02-fixture@bgscet.ac.in`, USN "E2EM02FIX", email domain "bgscet.ac.in". The status bar reads "Rows: 1". |
| ER-2 | 3 | The panel shows Batch "PG · no batch yet", Specialization "E2E Analytics and E2E Finance", College "E2E Registration College", Department "E2E Dual Department", USN "E2EM02FIX", Phone "+91 90000 00002", Personal email "e2e-m02-fixture@example.org", LinkedIn "https://www.linkedin.com/in/e2e-m02-fixture" (the form's short link, stored in full) and Documents "CV + photo". |
| ER-3 | 3 | Under Checks: "Routed by rule 'College domain — route to review'", "bgscet.ac.in is a college domain", "No account on this address", "USN E2EM02FIX is free", "CV and photo attached", "Opted for a dual specialization" with the detail "They ticked E2E Analytics and E2E Finance. A batch hangs on one specialization at most, so Approve seats them by the batch and keeps the other as their second specialization.", "No batch on this application", "CV attached" and "Photo attached". There is no warning that Approve will refuse the application. |
| ER-4 | 4 | The browser downloads `sample.pdf`, byte for byte the file the applicant attached. |
| ER-5 | 5 | The browser downloads `sample.png`, byte for byte the file the applicant attached. |

### Post-conditions

Nothing changes. The automated run rejects the fixture application
afterwards, so no pending application is left behind.

---

## TC-112 — Search, filter and export the review queue

| Field | Value |
|---|---|
| ID | TC-112 |
| Module | Registrations: the review queue |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-112` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied, so Ravi Kumar's application is
   pending.

### Test data

| Field | Value |
|---|---|
| Search | `ravi.kumar@bgscet.ac.in` |

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. Type `ravi.kumar@bgscet.ac.in` in the quick filter.
3. In the Domain check filter, choose "Off-domain (Approve refuses)".
4. In the Domain check filter, choose "On a college domain".
5. Click Export.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Only Ravi Kumar's row is left, and the status bar reads "Rows: 1". |
| ER-2 | 3 | No row is left. The grid says "No registration matches this filter." and the status bar reads "Rows: 0". |
| ER-3 | 4 | Ravi Kumar's row is back, and the status bar reads "Rows: 1". |
| ER-4 | 5 | The browser downloads `reep-registrations.csv`. It has a header row and one data row, for Ravi Kumar, and no other applicant. |

---

## TC-113 — Approving an application creates the student's account

| Field | Value |
|---|---|
| ID | TC-113 |
| Module | Registrations: the review queue |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-113` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. A pending application exists with the test data below, and no account or
   student holds its address or USN. The automated run submits it through the
   API (it counts toward the form's rate limit). By hand, submit it on
   `/register`.

### Test data

| Field | Value |
|---|---|
| Full name | `E2E Approve <run>` |
| College email | `e2e-<run>-approve@bgscet.ac.in` |
| USN | `E2E<run>A` |
| Personal email, phone, LinkedIn | `e2e-<run>-approve@example.org`, `+91 90000 00113`, `linkedin.com/in/e2e-<run>-approve` |
| College, department, course, batch | as in TC-106 |
| CV and photo | `sample.pdf` and `sample.png` |

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. Type the applicant's college email in the quick filter and click their row.
3. Click Approve & invite.
4. Open `/admin/students` and type the applicant's USN in the quick filter.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Under Checks: "No account on this address" ("Approving creates one and emails the onboarding link.") and "USN `<USN>` is free". |
| ER-2 | 3 | A notice reads "`<Full name>` approved — the account is created and the onboarding link is on its way to their college email." and the row leaves the Pending list. |
| ER-3 | 3 | **Manual only.** An email with the subject "Your REEP account is approved - set it up" goes to the college email, with a link to `/onboard?token=…` that expires in 7 days. |
| ER-4 | 4 | The roster lists one student: the applicant's USN, name and college email, with "FIN" under Spec., the specialization of the batch "2024-26 Section B" they asked for and were seated in. The status bar reads "Rows: 1". |

### Post-conditions

A student account exists for the address, with no password. The automated
run removes it from the roster afterwards (Remove, which keeps its records).
The application stays approved.

---

## TC-114 — Rejecting needs a reason, and a rejection can be undone

| Field | Value |
|---|---|
| ID | TC-114 |
| Module | Registrations: the review queue |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-114` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. The fixture application for `e2e-m02-fixture@bgscet.ac.in` is pending
   (see TC-111's pre-conditions).

### Test data

| Field | Value |
|---|---|
| Reason | `E2E: please apply again with a clearer photo.` |

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. Type `e2e-m02-fixture@bgscet.ac.in` in the quick filter and click the applicant's row.
3. Click Reject in the panel, with the Decision note empty.
4. Type the reason in Decision note.
5. Click Reject in the panel.
6. Click Undo in the notice.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | Under Decision note: "A reason is required when rejecting an application." The application is not rejected and its row stays. |
| ER-2 | 5 | A notice reads "E2E Fixture Applicant rejected — your reason has been emailed to them." with an Undo button, and the row leaves the Pending list. |
| ER-3 | 5 | **Manual only.** An email with the subject "About your REEP registration" goes to the college email, quoting the reason. The API sends it at most once per application (`deliver_once`, keyed on the application), so if this application was rejected before and then reopened, no second email goes out, although the notice in ER-2 says the reason has been emailed. The automated run rejects the same fixture application every time, so only its first rejection is ever mailed. |
| ER-4 | 6 | The notice reads "E2E Fixture Applicant is back in the queue." and the row is back in the Pending list. |

### Post-conditions

The fixture application is pending again. The automated run rejects it
afterwards, so no pending application is left behind.

---

## TC-115 — Holding needs a note, and a held application can be released

| Field | Value |
|---|---|
| ID | TC-115 |
| Module | Registrations: the review queue |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-115` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. The fixture application for `e2e-m02-fixture@bgscet.ac.in` is pending
   (see TC-111's pre-conditions).

### Test data

| Field | Value |
|---|---|
| Note | `E2E: waiting for a clearer photo.` |

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. Type `e2e-m02-fixture@bgscet.ac.in` in the quick filter and click the applicant's row.
3. Click Hold in the panel, with the Decision note empty.
4. Type the note in Decision note.
5. Click Hold in the panel.
6. Click the Held tab and click the applicant's row.
7. Click Release hold in the panel.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | Under Decision note: "A note is required when holding an application — say what it is waiting on." The row stays in the Pending list. |
| ER-2 | 5 | A notice reads "E2E Fixture Applicant is on hold — your note is on the application, and nothing was sent to them." and the row leaves the Pending list. |
| ER-3 | 5 | **Manual only.** No email is sent to the applicant when an application is held: the API console logs no `MAIL` line for the address. |
| ER-4 | 6 | The panel shows an "On hold" section with the note "E2E: waiting for a clearer photo." and the line "Held `<date, time>` · internal to the office. Nothing was emailed and the applicant's own page is unchanged." |
| ER-5 | 7 | A notice reads "E2E Fixture Applicant released — back in the pending queue." and the row leaves the Held list. |

### Post-conditions

The fixture application is pending again. The automated run rejects it
afterwards, so no pending application is left behind.

---

## TC-116 — Approve is refused for an address off the college's domains

| Field | Value |
|---|---|
| ID | TC-116 |
| Module | Registrations: the review queue |
| Priority | P1 |
| Type | Functional, negative, security |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-116` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index. The API's `GOOGLE_ALLOWED_DOMAIN` and `ROSTER_EMAIL_DOMAIN` are
   left at their defaults, so `bgscet.ac.in` is the only domain an
   application may be approved into. The seeded college has no domains of
   its own.
2. A pending application exists for `e2e-m02-offdomain@example.org`, naming
   the seeded college, department, course and batch. The automated run
   creates it through the API the first time and puts it back in the queue
   on later runs. By hand, submit it on `/register`.

### Test data

| Field | Value |
|---|---|
| Full name | `E2E Off Domain` |
| College email | `e2e-m02-offdomain@example.org` |
| USN | `E2EM02OFF` |

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. In the Domain check filter, choose "Off-domain (Approve refuses)".
3. Click the row for `e2e-m02-offdomain@example.org`.
4. Click Approve & invite.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The list shows the application from `e2e-m02-offdomain@example.org`, with the email domain "example.org", and no longer shows Ravi Kumar's on-domain application. |
| ER-2 | 3 | Under Checks: "example.org is not a college domain", with the detail "Approve refuses this (422). Only bgscet.ac.in may become a sign-in account here; a college's own list is set on Colleges." Under the checks: "Approve will refuse this application while the line marked above stand. Fix the application or reject it — the server answers the same refusal, so approving anyway only returns the error." |
| ER-3 | 4 | An error reads "This application cannot be approved: its email address is not on a college domain (bgscet.ac.in). Approving it would create a sign-in account. Staff accounts are created with `python -m app.grant_access`, not from this queue." The row stays in the list. |

### Post-conditions

Nothing changes. The automated run rejects the application afterwards, so no
pending application is left behind.

---

## TC-117 — Create, edit, disable and delete an auto-approve rule

| Field | Value |
|---|---|
| ID | TC-117 |
| Module | Registrations: auto-approve rules |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-117` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied, so its two rules exist
   unchanged.

### Test data

| Field | Value |
|---|---|
| Name | `E2E rule <run>` |
| Priority | `9000`, then `9001` |
| Email domain | `e2e-m02.invalid`: a domain no applicant uses, so the rule never routes a real application |
| Auto-approve | off |

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. Click Auto-approve rules.
3. Click New rule.
4. Fill in Name, Priority and Email domain with the test data, and click Create rule.
5. Click the rule's edit button, change Priority to 9001 and click Save rule.
6. Click Disable on the rule's row.
7. Click the rule's delete button and click OK in the confirmation.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A dialog "Auto-approve rules" opens, with the line "Lowest priority wins; ties go to the older rule." It lists the seeded rules: priority 10, "MBA 2024-26 auto-admit", matching "email on bgscet.ac.in · USN like ^1BG2[0-9]MBA[0-9]{3}$ · PG", seating in "Master of Business Administration - Finance · 2024-26 Section B", marked "Auto-approves"; and priority 100, "College domain — route to review", matching "email on bgscet.ac.in", seating in "No batch", marked "Waits for review". |
| ER-2 | 3 | A "New rule" form opens with an empty Name, Priority 100, Degree level "Any degree level", Seats in "No batch", "Enabled" ticked and "Auto-approve a matching application" not ticked. |
| ER-3 | 4 | The notice reads "Rule 'E2E rule `<run>`' is live." and the table has a row: 9000, the rule's name, "email on e2e-m02.invalid", "No batch", "Waits for review". |
| ER-4 | 5 | The notice reads "Rule 'E2E rule `<run>`' saved." and the row's priority is 9001. |
| ER-5 | 6 | The notice reads "Rule 'E2E rule `<run>`' is now off." The row carries an "Off" label and its button reads "Enable". |
| ER-6 | 7 | The confirmation asks "Delete the rule 'E2E rule `<run>`'?" and warns that applications it routed will stop showing which rule routed them. After OK, the notice reads "Rule 'E2E rule `<run>`' is gone." and the row is removed. |

### Post-conditions

None: the rule is deleted. If the case stops early, the automated run deletes
the rule afterwards.

---

## TC-118 — A rule that would auto-approve everyone, or that uses a dangerous USN pattern, is refused

| Field | Value |
|---|---|
| ID | TC-118 |
| Module | Registrations: auto-approve rules |
| Priority | P2 |
| Type | Functional, negative, security |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-118` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.

### Test data

| Field | Value |
|---|---|
| Name | `E2E unsafe <run>` |
| USN pattern | `(a+)+$`: a pattern whose matching time can explode on the public form |

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. Click Auto-approve rules, then click New rule.
3. Type the name, tick "Auto-approve a matching application", leave every condition empty and click Create rule.
4. Type `(a+)+$` in USN pattern and click Create rule.
5. Click Cancel.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The form stays open and says "A rule that auto-approves with no conditions would provision an account for every application ever submitted. Name at least one condition — the email domain is the usual one — or leave auto-approve off." |
| ER-2 | 4 | The server refuses the rule, and the form stays open and says "usn_pattern applies a quantifier to a group — refused as a catastrophic-backtracking risk on the public registration endpoint". |
| ER-3 | 5 | The form closes. The rules table has no rule named "E2E unsafe `<run>`". |

---

## TC-119 — A faculty member without the Registrations grant cannot open the review queue

| Field | Value |
|---|---|
| ID | TC-119 |
| Module | Registrations: access |
| Priority | P2 |
| Type | Security, negative |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-119` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied.
2. The seeded faculty account has not been granted "admin.registrations" in
   Governance (the seed grants it nothing).

### Test data

| Account | Faculty: `mentor@bgscet.ac.in` / `mentor123` |
|---|---|

### Steps

1. Sign in as the seeded faculty member.
2. Open `/admin/registrations` directly in the address bar.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The app does not show the queue. It sends the faculty member to their own home, `/mentor/notebook`, and the "New applications" heading never appears. |

---

## TC-120 — The setup page opened without a link says what is missing

| Field | Value |
|---|---|
| ID | TC-120 |
| Module | Onboarding: the setup page |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-120` |

### Pre-conditions

1. The web app is running as described in "Setup" in the index. Nobody needs
   to be signed in.

### Test data

None.

### Steps

1. Open `/onboard` with no `?token=` in the address.
2. Click the "sign in" link.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Confirm your email address" and shows the error "This page needs the setup link from the email we sent you. Open that link again, or ask for a new one with "Forgot password?" on the sign-in screen." followed by "If you have already set your password, just sign in." |
| ER-2 | 1 | There is no email box and no "Send me a code" button. |
| ER-3 | 2 | The sign-in page opens at `/login`, headed "Welcome back". |

---

## TC-121 — The setup page refuses a link it does not recognise

| Field | Value |
|---|---|
| ID | TC-121 |
| Module | Onboarding: the setup page |
| Priority | P2 |
| Type | Functional, negative, security |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-121` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.

### Test data

| Field | Value |
|---|---|
| Link | `/onboard?token=e2e-not-a-real-setup-token-0000`: 30 characters, long enough to be read as a link, and issued by nobody |
| Invalid address | `not-an-email` |
| Address | `student@bgscet.ac.in`, a real account's address |

### Steps

1. Open `/onboard?token=e2e-not-a-real-setup-token-0000`.
2. Type `not-an-email` in "Your college email" and click Send me a code.
3. Replace it with `student@bgscet.ac.in` and click Send me a code.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Confirm your email address", says "Type the college email address this link was sent to and we will send you a one-time code to confirm it.", and shows the box "Your college email" and the button "Send me a code". |
| ER-2 | 2 | Under the box: "Enter the email address this link was sent to." Nothing is sent to the server, and the page stays on this step. |
| ER-3 | 3 | The error "That setup link is not valid, or the email address does not match it. Check the link in the email we sent you, or ask for a new one with "Forgot password?" on the sign-in screen." is shown, followed by "If you have already set your password, just sign in." The email box and the button are gone. The same words appear for a real link with the wrong address, so the page does not reveal whether an address is enrolled. |

---

## TC-122 — An approved student sets up their account from the emailed link

| Field | Value |
|---|---|
| ID | TC-122 |
| Module | Onboarding: the setup page |
| Priority | P1 |
| Type | Functional, end to end |
| Automated test | None (manual only): the setup link and the six-digit code reach the student only by email, and a browser test cannot read a mailbox. Locally they appear only in the API's console log, which the automated tests must not read. TC-120 and TC-121 automate what the page shows without a real link. |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. An application was approved by TC-113 or auto-approved by TC-106, and the
   tester can read the mail sent to its college email. On a development
   server with no mail transport, that means the API's console output.
3. The account has not been removed. The automated runs of TC-106 and TC-113
   remove the accounts they create, so approve an application by hand for
   this case.
4. The setup link is less than 7 days old and has not been used.

### Test data

| Field | Value |
|---|---|
| College email | the address of the approved application |
| Wrong address | any other address, for example `someone.else@bgscet.ac.in` |
| Wrong code | `000000` |
| Short password | `short-pass` (10 characters) |
| New password | any 12 or more characters that are not a password published in AGENTS.md, for example `correct horse battery staple` |

### Steps

1. Open the link in the email "Your REEP account is approved - set it up".
2. Type the wrong address in "Your college email" and click Send me a code.
3. Open the link from the email again.
4. Type the college email in "Your college email" and click Send me a code.
5. Type `000000` in "Six-digit code" and click Confirm my email.
6. Type the code from the email "Your REEP verification code" and click Confirm my email.
7. Type the short password in "Password" and in "Type it again", and click Set my password.
8. Type the new password in both boxes and click Set my password.
9. Click "Sign in", choose the Student portal and sign in with the college email and the new password.
10. Open the link from the first email once more, type the college email and click Send me a code.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | **Manual only.** The error "That setup link is not valid, or the email address does not match it. Check the link in the email we sent you, or ask for a new one with "Forgot password?" on the sign-in screen." appears. The link is not used up by the mistake. |
| ER-2 | 4 | **Manual only.** The page is headed "Enter the code we emailed you" and says "We have emailed a 10-minute code to `<college email>`. Enter it below." An email with the subject "Your REEP verification code" arrives, holding a six-digit code that expires in 10 minutes. |
| ER-3 | 5 | **Manual only.** The error "That code is not right, or it has expired. Ask for a new one." appears and the page stays on the code step. |
| ER-4 | 6 | **Manual only.** The page is headed "Choose your password" and says "At least 12 characters. A memorable phrase of four or five words clears that easily and is stronger than a short scrambled one." |
| ER-5 | 7 | **Manual only.** Under Password: "Needs 12 characters or more." The password is not set. |
| ER-6 | 8 | **Manual only.** The page is headed "Your account is ready" and says "Your password is set. Sign in with your college email and the password you just chose." It does not sign the student in. |
| ER-7 | 9 | **Manual only.** The student lands on `/student`, greeted "Welcome back, `<first name>`". |
| ER-8 | 10 | **Manual only.** The link is spent: the page shows the same error as in ER-1. |

### Post-conditions

The student account has a password. Signing in with it on another device
signs this browser out, because REEP keeps one live session per account.

---

## TC-123 — The rules dialog names the batch a rule seats in, even when the batch names arrive last

| Field | Value |
|---|---|
| ID | TC-123 |
| Module | Registrations: auto-approve rules |
| Priority | P3 |
| Type | Functional, timing |
| Automated test | `tests/02-registration.spec.ts`, title tagged `@TC-123` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the dev seed has been applied, so the rule "MBA 2024-26
   auto-admit" seats applicants in the batch "2024-26 Section B".
2. The batch names reach the browser after the rules. Both requests are sent
   together, so this happens on some page loads and not on others. The
   automated test holds the batch names back until the rules have arrived,
   without changing either response, so it happens every time. By hand,
   reload the page a few times, or throttle the network in the browser's
   developer tools, which makes the larger batch list likelier to arrive
   last.

### Test data

| Account | Main Admin: `admin@bgscet.ac.in` / `admin123` |
|---|---|

### Steps

1. Sign in as the Main Admin and open `/admin/registrations`.
2. Click Auto-approve rules.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The row for "MBA 2024-26 auto-admit" shows "Master of Business Administration - Finance · 2024-26 Section B" under "Seats in", not an id such as "Batch 9b799137". |
