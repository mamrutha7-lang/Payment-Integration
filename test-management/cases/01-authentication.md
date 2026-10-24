# Authentication and access

| Field | Value |
|---|---|
| Screens | `/login`, sign-out, each role's landing page, route guards, `/account`, `/account/password`, `/activate`, `/reset` |
| Automated tests | [`tests/auth-sync.spec.ts`](../../tests/auth-sync.spec.ts) |
| ID range | TC-001 to TC-099 |

Setup, the seeded accounts and the rules that link these cases to their
automated tests are in [the index](../manual-test-cases.md).

---

## TC-001 — Successful login with valid credentials

| Field | Value |
|---|---|
| ID | TC-001 |
| Module | Authentication: password sign-in |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-001` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and the API's `ENV` is a development one (`dev`). The dev seed
   that creates the test account refuses to run on `ENV=prod`, and a
   development `ENV` always offers password sign-in. Elsewhere the password
   form also appears when `PASSWORD_LOGIN=true`, or when that setting is
   blank and some account holds a real password, so a visible password form
   does not by itself mean the server is a development one.
2. The dev seed has been applied, so the student account in the test data
   exists.
3. The browser has no REEP session and no remembered sign-in: use a fresh
   private window, or sign out and clear this site's local storage (the
   `reep.login.id` and `reep.login.portal` keys that "Remember me" sets).
   Signing out alone leaves those keys, and the sign-in page then opens on
   the remembered portal with the ID already filled in.
4. The account has had fewer than 10 failed sign-in attempts in the last 15
   minutes. After 10, the API pauses password sign-in for that account. A
   successful sign-in resets the count.
5. Nothing else signs in as `student@bgscet.ac.in` while the case runs: no
   other browser or device, no pytest run and no other Playwright run
   against the same database. REEP keeps one live session per account, so
   another sign-in ends this one, either at once or within 60 seconds. The
   reload in step 6 would then land on `/login?next=%2Fstudent&signedOut=elsewhere`,
   and ER-5 would fail for a reason outside the app.

### Test data

| Field | Value |
|---|---|
| Portal | Student |
| Email | `student@bgscet.ac.in` |
| Password | `student123` |
| Name on record | Test Student |

### Steps

1. Open the sign-in page at `/login`.
2. Under "Choose your portal", select Student.
3. Enter the email address in the "Institutional email or USN" field.
4. Enter the password in the "Password" field.
5. Click Sign in.
6. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Welcome back" sign-in card is shown, with the "Institutional email or USN" field, the "Password" field and a Sign in button. |
| ER-2 | 2 | The Student portal card is marked as selected, and the ID field is labelled "Institutional email or USN". |
| ER-3 | 4 | The password is masked, not shown as readable text. |
| ER-4 | 5 | The browser leaves the sign-in page for the student home, `/student`, which greets the student by first name: "Welcome back, Test". |
| ER-5 | 6 | The student is still signed in after the reload: the page stays on `/student` and shows "Welcome back, Test" again. |

### Post-conditions

The account now holds one live session. REEP allows one device per account,
so any other browser signed in as this student is signed out on its next
request.

---

## TC-002 — Login is refused for an invalid password

| Field | Value |
|---|---|
| ID | TC-002 |
| Module | Authentication: password sign-in |
| Priority | P1 |
| Type | Functional, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-002` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`.
2. The dev seed has been applied, so the email in the test data belongs to a
   real account. The case checks a wrong password for an existing account.
3. The browser has no REEP session and no remembered sign-in: use a fresh
   private window, or sign out and clear this site's local storage (the
   `reep.login.id` and `reep.login.portal` keys that "Remember me" sets).
4. The account has had fewer than 10 failed sign-in attempts in the last 15
   minutes. Past that, the API answers 429, and the card shows "Too many
   failed attempts for this account or from this network, so password
   sign-in is paused for a few minutes…" instead of the message in ER-1.

### Test data

| Field | Value |
|---|---|
| Portal | Student |
| Email | `student@bgscet.ac.in` |
| Password | `wrong-password` (any value other than the account's real password) |

### Steps

1. Open the sign-in page at `/login`.
2. Under "Choose your portal", select Student.
3. Enter the email address in the "Institutional email or USN" field.
4. Enter the invalid password in the "Password" field.
5. Click Sign in.
6. Open `/student` directly in the same tab.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 5 | An error message appears on the card: "That email and password did not match an account. Check both, or use Continue with Google." It does not say which of the two was wrong. |
| ER-2 | 5 | The page stays on the sign-in page, `/login`. |
| ER-3 | 5 | The password field is emptied, and the email address stays filled in. |
| ER-4 | 6 | No session was created: the app sends the browser back to the sign-in page, and the address bar reads `/login?next=%2Fstudent`. |

### Post-conditions

One failed attempt is added to the account's count. The count lasts until
15 minutes after the first failure in the current window, not 15 minutes
after this attempt. The API keeps it in memory, so restarting the API clears
it, and so does a successful sign-in (TC-001).

---

## TC-003 — Password reset request

| Field | Value |
|---|---|
| ID | TC-003 |
| Module | Authentication: forgotten password |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-003` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`. The "Forgot password?" link sits under
   the password field, which is shown only when the server offers password
   sign-in.
2. In the last hour the API has received fewer than 3 reset requests for
   this address, and fewer than 100 reset requests in total, for any address.
   A request refused by the per-address limit still counts toward the 100.
   The API keeps both counts in memory, so restarting it clears them. Past
   either limit, it answers "Too many reset requests. Please wait an hour and
   try again."
3. For ER-4 only: access to the API's console output (a development server
   with no mail transport), or to the account's mailbox.

### Test data

| Field | Value |
|---|---|
| Email | `student@bgscet.ac.in` (the dev seed's student account) |

The automated run enters a new, unregistered address on every run instead,
of the form `tc-003-<run id>@example.invalid`. That keeps repeated runs under
the 3-per-hour limit, though each run still uses one of the 100 per hour. It
checks the same thing: the page shows the same sentence for every address,
registered or not (ER-3). The only difference a registered address makes is
the email itself, and ER-4 is checked by hand.

### Steps

1. Open the sign-in page at `/login`.
2. Click "Forgot password?".
3. Enter the email address in the "Email address" field.
4. Click Send reset link.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A reset form opens on the sign-in card, under the Sign in button, with an "Email address" field and a Send reset link button. The button is disabled while the field is empty. |
| ER-2 | 3 | The Send reset link button becomes enabled. |
| ER-3 | 4 | The form is replaced by exactly this sentence: "If that address belongs to a REEP account, we've emailed it a link to reset your password - or to set one up, if you have not yet." The page stays on `/login`. |
| ER-4 | 4 | **Manual only.** An email with the subject "Reset your REEP password" is sent to the address. It contains a single-use link to `/reset?token=…` that expires in 60 minutes. A development server with no mail transport (`SES_FROM_ADDRESS` blank) logs the email to the API console as `MAIL (no transport configured) to=student@bgscet.ac.in subject='Reset your REEP password'`. |

### Post-conditions

The account's password is unchanged until the emailed link is used. A new
reset request replaces any earlier link that has not been used yet.

---

## TC-004 — Sign-in form asks for a missing ID and password

| Field | Value |
|---|---|
| ID | TC-004 |
| Module | Authentication: password sign-in |
| Priority | P2 |
| Type | Functional, negative (field validation) |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-004` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, so the password form is shown.
2. The browser has no REEP session and no remembered sign-in (see TC-001,
   pre-condition 3).

### Test data

| Field | Value |
|---|---|
| Email | none: the fields stay empty |

### Steps

1. Open the sign-in page at `/login`.
2. Click Sign in without entering anything.
3. Under "Choose your portal", select Faculty.
4. Select Alumni.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Two messages appear under the fields: "Enter your institutional email or usn." and "Enter your password." (the first is built from the ID field's label, lower-cased, so "USN" reads "usn"). Both fields are marked invalid. |
| ER-2 | 2 | Nothing is sent to the server, and the page stays on `/login`. |
| ER-3 | 3 | The ID message follows the portal's wording: "Enter your institutional email." |
| ER-4 | 4 | The ID message reads "Enter your registered email." |

What happens once the fields are filled in is TC-040.

---

## TC-005 — Portal cards and the Main Admin door relabel the ID field

| Field | Value |
|---|---|
| ID | TC-005 |
| Module | Authentication: sign-in page |
| Priority | P3 |
| Type | Functional, UI |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-005` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`.
2. The browser has no REEP session and no remembered sign-in (see TC-001,
   pre-condition 3).

### Test data

None.

### Steps

1. Open the sign-in page at `/login`.
2. Under "Choose your portal", select Faculty.
3. Select Alumni.
4. Click "Main Admin — open the REEP Admin Console".
5. Select Student.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | Three portal cards are offered: Student ("Track skills, jobs & growth"), Faculty ("Guide students, verify skills") and Alumni ("Explore roles, stay connected"). Student is selected. The ID field is labelled "Institutional email or USN", with the placeholder "asha.rao@bgscet.ac.in or 1BG24MBA001" and the hint "Use your BGSCET student email or University Seat Number." The line under the Google button reads "Continues to your Student workspace". |
| ER-2 | 2 | Faculty is selected and Student is not. The ID field is labelled "Institutional email", with the placeholder "kavya.n@bgscet.ac.in" and the hint "Use your faculty email." The Google line reads "Continues to your Faculty workspace". |
| ER-3 | 3 | The ID field is labelled "Registered email", with the placeholder "rohan.shetty@alumni.bgscet.ac.in" and the hint "Use the email from your alumni registration." The Google line reads "Continues to your Alumni workspace". |
| ER-4 | 4 | The Main Admin door is shown as pressed, and no portal card is selected. The ID field is labelled "Institutional email", with the placeholder "placement.admin@bgscet.ac.in" and the hint "Use your admin email.", and the cursor is in it. The Google line reads "Continues to your Admin workspace". |
| ER-5 | 5 | Student is selected again, the Main Admin door is no longer pressed, and the ID field is labelled "Institutional email or USN". |

---

## TC-006 — Show password and Hide password

| Field | Value |
|---|---|
| ID | TC-006 |
| Module | Authentication: sign-in page |
| Priority | P3 |
| Type | Functional, UI |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-006` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`.

### Test data

| Field | Value |
|---|---|
| Password | `Typed-but-not-sent-1` (any text; nothing is submitted) |

### Steps

1. Open the sign-in page at `/login`.
2. Enter the password in the "Password" field.
3. Click the eye button, "Show password", at the end of the field.
4. Click the same button again, now named "Hide password".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The password is masked. |
| ER-2 | 3 | The password is shown as readable text, and the button is now named "Hide password" and shown as pressed. |
| ER-3 | 4 | The password is masked again, and the button is named "Show password" and no longer pressed. The typed value is unchanged. |

---

## TC-007 — Remember me keeps the ID and portal on this device

| Field | Value |
|---|---|
| ID | TC-007 |
| Module | Authentication: password sign-in |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-007` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`.
2. The browser has no REEP session and no remembered sign-in (see TC-001,
   pre-condition 3).
3. Nothing else signs in as `mentor@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Portal | Faculty |
| Email | `mentor@bgscet.ac.in` |
| Password | `mentor123` |

### Steps

1. Open the sign-in page at `/login`.
2. Under "Choose your portal", select Faculty.
3. Enter the email address in the "Institutional email" field.
4. Enter the password in the "Password" field.
5. Tick "Remember me".
6. Click Sign in.
7. Open the account menu (the name in the top right) and click Sign out.
8. Untick "Remember me", enter the password again and click Sign in.
9. Open the account menu and click Sign out.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 6 | The faculty member is signed in and lands on `/mentor/notebook`. |
| ER-2 | 7 | The sign-in page opens with the Faculty portal already selected, the email address already in the "Institutional email" field and "Remember me" ticked. The "Password" field is empty: the password is never remembered. |
| ER-3 | 8 | The faculty member is signed in again and lands on `/mentor/notebook`. |
| ER-4 | 9 | The sign-in page opens as for a new visitor: Student selected, the ID field empty and "Remember me" unticked. |

### Post-conditions

Nothing is remembered on the browser: step 8 cleared it. If the case stops
after step 7, the browser keeps the ID and portal (the `reep.login.id` and
`reep.login.portal` local-storage keys) until they are cleared.

---

## TC-008 — Remember me through the Main Admin door keeps the admin wording

| Field | Value |
|---|---|
| ID | TC-008 |
| Module | Authentication: password sign-in |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-008` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`.
2. The browser has no REEP session and no remembered sign-in (see TC-001,
   pre-condition 3).
3. Nothing else signs in as `admin@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Email | `admin@bgscet.ac.in` |
| Password | `admin123` |

### Steps

1. Open the sign-in page at `/login`.
2. Click "Main Admin — open the REEP Admin Console".
3. Enter the email address in the "Institutional email" field.
4. Enter the password in the "Password" field.
5. Tick "Remember me".
6. Click Sign in.
7. Open the account menu (the name in the top right) and click Sign out.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 6 | The Main Admin is signed in and lands on `/admin`. |
| ER-2 | 7 | The sign-in page opens with the email address already in the ID field and "Remember me" ticked. |
| ER-3 | 7 | The Main Admin door is shown as pressed and the ID field is labelled "Institutional email", as it was when the Main Admin signed in. |

### Post-conditions

The browser remembers `admin@bgscet.ac.in`. To clear it, sign in once with
"Remember me" unticked, or clear the `reep.login.id` and `reep.login.portal`
local-storage keys.

---

## TC-009 — Faculty member signs in and lands on the faculty notebook

| Field | Value |
|---|---|
| ID | TC-009 |
| Module | Authentication: password sign-in |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-009` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The browser has no REEP session and no remembered sign-in (see TC-001,
   pre-condition 3).
3. Nothing else signs in as `mentor@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Portal | Faculty |
| Email | `mentor@bgscet.ac.in` |
| Password | `mentor123` |
| Name on record | Test Mentor |

### Steps

1. Open the sign-in page at `/login`.
2. Under "Choose your portal", select Faculty.
3. Enter the email address in the "Institutional email" field.
4. Enter the password in the "Password" field.
5. Click Sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 5 | The browser lands on `/mentor/notebook`, headed "Faculty notebook". |
| ER-2 | 5 | The top bar reads "REEP Faculty console", and the account block shows "Test Mentor" with the role "Faculty". The browser tab is titled "REEP · Faculty". |
| ER-3 | 5 | The sidebar offers the faculty screens, among them Notebook, Mentee Log and Leave Requests. |

---

## TC-010 — Alumnus signs in and lands on the alumni profile

| Field | Value |
|---|---|
| ID | TC-010 |
| Module | Authentication: password sign-in |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-010` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The browser has no REEP session and no remembered sign-in (see TC-001,
   pre-condition 3).
3. Nothing else signs in as `alumni@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Portal | Alumni |
| Email | `alumni@bgscet.ac.in` |
| Password | `alumni123` |
| Name on record | Test Alumnus |

### Steps

1. Open the sign-in page at `/login`.
2. Under "Choose your portal", select Alumni.
3. Enter the email address in the "Registered email" field.
4. Enter the password in the "Password" field.
5. Click Sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 5 | The browser lands on `/alumni`, headed "My Profile". |
| ER-2 | 5 | The top bar reads "REEP Alumni", and the account block shows "Test Alumnus" with the role "Alumni". The browser tab is titled "REEP · Alumni". |
| ER-3 | 5 | The sidebar offers the two alumni screens, My Profile and Jobs Sheet. |

---

## TC-011 — Main Admin signs in through the Main Admin door

| Field | Value |
|---|---|
| ID | TC-011 |
| Module | Authentication: password sign-in |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-011` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The browser has no REEP session and no remembered sign-in (see TC-001,
   pre-condition 3).
3. Nothing else signs in as `admin@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Email | `admin@bgscet.ac.in` |
| Password | `admin123` |
| Name on record | Main Admin (seed) |

### Steps

1. Open the sign-in page at `/login`.
2. Click "Main Admin — open the REEP Admin Console".
3. Enter the email address in the "Institutional email" field.
4. Enter the password in the "Password" field.
5. Click Sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 5 | The browser lands on the admin home, `/admin`, headed "What do you want to do?". |
| ER-2 | 5 | The top bar reads "REEP Admin console" and carries the chip "Scope · Whole programme"; the account block shows "Main Admin (seed)" with the role "Main Admin". The browser tab is titled "REEP · Main Admin". |

---

## TC-012 — The portal picked does not decide where a user lands

| Field | Value |
|---|---|
| ID | TC-012 |
| Module | Authentication: password sign-in |
| Priority | P2 |
| Type | Functional, negative (role comes from the account) |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-012` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The browser has no REEP session and no remembered sign-in.
3. Nothing else signs in as `mentor@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Portal | Alumni (deliberately the wrong one) |
| Email | `mentor@bgscet.ac.in` |
| Password | `mentor123` |

### Steps

1. Open the sign-in page at `/login`.
2. Under "Choose your portal", select Alumni.
3. Enter the faculty member's email address in the "Registered email" field.
4. Enter the password in the "Password" field.
5. Click Sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 5 | The faculty member is signed in and lands on the faculty home, `/mentor/notebook` ("Faculty notebook"), not on the alumni profile: the portal only changes the field's wording, and the role comes from the account. |

---

## TC-013 — Sign-in returns to the page that was asked for

| Field | Value |
|---|---|
| ID | TC-013 |
| Module | Authentication: route guards |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-013` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The browser has no REEP session and no remembered sign-in.
3. Nothing else signs in as `student@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Page asked for | `/student/records` |
| Email | `student@bgscet.ac.in` |
| Password | `student123` |

### Steps

1. Open `/student/records` directly.
2. Enter the email address in the "Institutional email or USN" field.
3. Enter the password in the "Password" field.
4. Click Sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The app sends the browser to the sign-in page, and the address bar reads `/login?next=%2Fstudent%2Frecords`. |
| ER-2 | 1 | Under "Welcome back" the card reads "Sign in to carry on to the page you asked for." |
| ER-3 | 4 | The student lands on the page asked for, `/student/records`, headed "Records", and not on the student home. |

---

## TC-014 — A next address that leaves REEP is ignored after sign-in

| Field | Value |
|---|---|
| ID | TC-014 |
| Module | Authentication: route guards |
| Priority | P2 |
| Type | Security, negative (open redirect) |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-014` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The browser has no REEP session and no remembered sign-in.
3. Nothing else signs in as `student@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Sign-in address | `/login?next=%2F%2Fexample.com%2Fphish` (`next` is `//example.com/phish`, another site) |
| Email | `student@bgscet.ac.in` |
| Password | `student123` |

### Steps

1. Open the sign-in page at `/login?next=%2F%2Fexample.com%2Fphish`.
2. Enter the email address in the "Institutional email or USN" field.
3. Enter the password in the "Password" field.
4. Click Sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The card reads "Sign in to continue to your REEP workspace.", the wording for a visit with no page asked for, and the Continue with Google link does not carry the address on. |
| ER-2 | 4 | The student lands on the student home, `/student`, with "Welcome back, Test". The browser never leaves REEP for `example.com`. |

---

## TC-015 — Student signs out from the top bar

| Field | Value |
|---|---|
| ID | TC-015 |
| Module | Authentication: sign-out |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-015` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. Nothing else signs in as `student@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | the dev seed's student, `student@bgscet.ac.in` / `student123` |

### Steps

1. Sign in as the student and open the student home at `/student`.
2. Click Sign out at the right of the top bar.
3. Open `/student` directly in the same tab.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The home greets the student ("Welcome back, Test"), and the top bar shows "Test Student", the student's USN "1BG24MBA001" and a Sign out button. |
| ER-2 | 2 | The browser goes to the sign-in page, `/login`, with the "Welcome back" card. |
| ER-3 | 3 | The session has ended: the app sends the browser back to sign in, and the address bar reads `/login?next=%2Fstudent`. No "signed in on another device" note is shown, because nobody else signed in. |

---

## TC-016 — Faculty member signs out from the account menu

| Field | Value |
|---|---|
| ID | TC-016 |
| Module | Authentication: sign-out |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-016` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. Nothing else signs in as `mentor@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | the dev seed's faculty member, `mentor@bgscet.ac.in` / `mentor123` |

### Steps

1. Sign in as the faculty member and open `/mentor/notebook`.
2. Click the account block, "Test Mentor", at the right of the top bar.
3. Click Sign out in the menu.
4. Open `/mentor/notebook` directly in the same tab.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A menu opens under the name, headed "Test Mentor" and "Faculty", with four items: My account, Password, Signature and Sign out. |
| ER-2 | 3 | The browser goes to the sign-in page, `/login`. |
| ER-3 | 4 | The session has ended: the address bar reads `/login?next=%2Fmentor%2Fnotebook` and the sign-in form is shown. |

---

## TC-017 — A signed-out visitor is sent to sign in from protected pages

| Field | Value |
|---|---|
| ID | TC-017 |
| Module | Authentication: route guards |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-017` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.
2. The browser has no REEP session: use a fresh private window.

### Test data

| Field | Value |
|---|---|
| Pages | `/mentor/notebook`, `/admin/students`, `/account/password` |

### Steps

1. Open `/mentor/notebook` directly.
2. Open `/admin/students` directly.
3. Open `/account/password` directly.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The faculty notebook is not shown. The browser is on the sign-in page, and the address bar reads `/login?next=%2Fmentor%2Fnotebook`. |
| ER-2 | 2 | The student roster is not shown. The address bar reads `/login?next=%2Fadmin%2Fstudents`. |
| ER-3 | 3 | The change-password screen is not shown. The address bar reads `/login?next=%2Faccount%2Fpassword`, and the card reads "Sign in to carry on to the page you asked for." |

---

## TC-018 — A student cannot open faculty, admin or alumni screens

| Field | Value |
|---|---|
| ID | TC-018 |
| Module | Authentication: route guards |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-018` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. Nothing else signs in as `student@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | `student@bgscet.ac.in` / `student123` |

### Steps

1. Sign in as the student.
2. Open `/admin` directly.
3. Open `/mentor/notebook` directly.
4. Open `/admin/governance` directly.
5. Open `/alumni` directly.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The admin home is not shown. The browser is sent to the student's own home, `/student`, with "Welcome back, Test". |
| ER-2 | 3 | The faculty notebook is not shown. The browser is on `/student`. |
| ER-3 | 4 | Governance is not shown. The browser is on `/student`. |
| ER-4 | 5 | The alumni profile is not shown. The browser is on `/student`. |

---

## TC-019 — A faculty member cannot open admin or student screens

| Field | Value |
|---|---|
| ID | TC-019 |
| Module | Authentication: route guards |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-019` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The faculty member has been granted no console screen in Governance (the
   dev seed grants none). A granted screen opens for them, which is correct.
3. Nothing else signs in as `mentor@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | `mentor@bgscet.ac.in` / `mentor123` |

### Steps

1. Sign in as the faculty member.
2. Open `/admin/governance` directly.
3. Open `/admin/analytics` directly.
4. Open `/student/time-log` directly.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Governance is not shown. The browser is sent to the faculty home, `/mentor/notebook`, headed "Faculty notebook". |
| ER-2 | 3 | Analytics is not shown. The browser is on `/mentor/notebook`. |
| ER-3 | 4 | The student time sheet is not shown. The browser is on `/mentor/notebook`. |

---

## TC-020 — The Main Admin and an alumnus are kept to their own screens

| Field | Value |
|---|---|
| ID | TC-020 |
| Module | Authentication: route guards |
| Priority | P2 |
| Type | Security, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-020` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. Nothing else signs in as `admin@bgscet.ac.in` or `alumni@bgscet.ac.in`
   while the case runs.

### Test data

| Field | Value |
|---|---|
| Accounts | `admin@bgscet.ac.in` / `admin123`, then `alumni@bgscet.ac.in` / `alumni123` |

### Steps

1. Sign in as the Main Admin.
2. Open `/student/time-log` directly.
3. Open `/alumni` directly.
4. Sign in as the alumnus instead.
5. Open `/mentor/notebook` directly.
6. Open `/admin` directly.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The student time sheet is not shown. The browser is sent to the admin home, `/admin`, headed "What do you want to do?". |
| ER-2 | 3 | The alumni profile is not shown. The browser is on `/admin`. |
| ER-3 | 5 | The faculty notebook is not shown. The browser is sent to the alumni home, `/alumni`, headed "My Profile". |
| ER-4 | 6 | The admin home is not shown. The browser is on `/alumni`. |

---

## TC-021 — Unknown addresses lead to the home page or to sign-in

| Field | Value |
|---|---|
| ID | TC-021 |
| Module | Authentication: route guards |
| Priority | P3 |
| Type | Functional, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-021` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The browser starts with no REEP session.
3. Nothing else signs in as `student@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Unknown address | `/no-such-page` |
| Account | `student@bgscet.ac.in` / `student123` |

### Steps

1. Open `/no-such-page` directly.
2. Enter the student's email address and password, and click Sign in.
3. Open `/no-such-page` directly again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | There is no error page: the app sends the signed-out visitor to sign in, and the address bar reads `/login?next=%2F`. |
| ER-2 | 2 | The student lands on the student home, `/student`, with "Welcome back, Test". |
| ER-3 | 3 | A signed-in visitor is sent to their own home: the browser is on `/student` with "Welcome back, Test". |

---

## TC-022 — Signing in on a second device signs the first one out, with a note

| Field | Value |
|---|---|
| ID | TC-022 |
| Module | Authentication: one device at a time |
| Priority | P1 |
| Type | Functional, security |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-022` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. Two browsers that share no cookies: for example a normal window and a
   private window, or two different browsers.
3. Nothing else signs in as `student@bgscet.ac.in` while the case runs.
4. The API runs as one process, as in "Setup". With several API workers a
   retired session can still be accepted for up to 60 seconds by a worker
   that has not yet re-read the account, so step 4 may need a second reload.

### Test data

| Field | Value |
|---|---|
| Account | `student@bgscet.ac.in` / `student123`, in both browsers |

### Steps

1. In the first browser, sign in as the student and open `/student`.
2. In the second browser, open `/login`.
3. In the second browser, sign in as the same student on the sign-in page.
4. In the first browser, reload the page.
5. In the first browser, sign in as the student again on the sign-in page.
6. In the second browser, reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The first browser shows the student home, "Welcome back, Test". |
| ER-2 | 3 | The second browser lands on `/student` with "Welcome back, Test". |
| ER-3 | 4 | The first browser is signed out: the address bar reads `/login?next=%2Fstudent&signedOut=elsewhere`, and the card says "You were signed out because this account was signed in on another device. REEP allows one device at a time — sign in again here to carry on, and the other device will be signed out." |
| ER-4 | 5 | The first browser lands back on `/student` (the page it asked for) with "Welcome back, Test". |
| ER-5 | 6 | Now the second browser is signed out, with the same note, and its address bar reads `/login?next=%2Fstudent&signedOut=elsewhere`. The newest sign-in wins. |

### Post-conditions

The first browser holds the account's one live session.

---

## TC-023 — My account shows a faculty member's own details

| Field | Value |
|---|---|
| ID | TC-023 |
| Module | Account: My account |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-023` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The faculty member has never signed in with Google, so the account is not
   linked to a Google identity (true of the dev seed).
3. Nothing else signs in as `mentor@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | `mentor@bgscet.ac.in` / `mentor123`, name Test Mentor |

### Steps

1. Sign in as the faculty member with the email address and password.
2. Open the account menu (the name in the top right) and click My account.
3. Click Sign out at the top right of the My account page.
4. Open `/account` directly.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The browser is on `/account`, headed "My account", and the line under the heading reads "Faculty · mentor@bgscet.ac.in · sign-in, password, signature and notifications for your own login." |
| ER-2 | 2 | "Sign-in & security" shows "Google · mentor@bgscet.ac.in" with the chip "Not linked", the password row "Password · 12 characters or more" and an "Email me a code" button. |
| ER-3 | 2 | "Sessions" reads "Signed in as Test Mentor · Faculty", with a "This device" chip and a "Sign out everywhere" button. |
| ER-4 | 2 | A "Signature" card is shown, because faculty sign leave papers. |
| ER-5 | 2 | "Recent sign-ins" lists the sign-in from step 1 first, with the door "Email and password". |
| ER-6 | 3 | The browser goes to the sign-in page, `/login`. |
| ER-7 | 4 | The session has ended: the address bar reads `/login?next=%2Faccount`. |

---

## TC-024 — My account shows a student's own details, without a signature card

| Field | Value |
|---|---|
| ID | TC-024 |
| Module | Account: My account |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-024` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. Nothing else signs in as `student@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | `student@bgscet.ac.in` / `student123`, name Test Student |

### Steps

1. Sign in as the student with the email address and password.
2. Open `/account` directly. A student has no account menu; the page is reached by its address.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The page is headed "My account", and the line under the heading reads "Student · student@bgscet.ac.in · sign-in, password, sessions and notifications for your own login." |
| ER-2 | 2 | "Sessions" reads "Signed in as Test Student · Student". |
| ER-3 | 2 | There is no "Signature" card: a student signs no leave papers. |
| ER-4 | 2 | "Recent sign-ins" lists the sign-in from step 1 first, with the door "Email and password". |

---

## TC-025 — My account says so when the session ended on another device

| Field | Value |
|---|---|
| ID | TC-025 |
| Module | Account: My account |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-025` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. Two browsers that share no cookies (see TC-022, pre-condition 2).
3. Nothing else signs in as `mentor@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | `mentor@bgscet.ac.in` / `mentor123`, in both browsers |

### Steps

1. In the first browser, sign in as the faculty member and open the account menu's My account.
2. In the second browser, sign in as the same faculty member on the sign-in page.
3. In the first browser, click Refresh on the "Recent sign-ins" card.
4. In the first browser, click "Go to sign in".
5. In the second browser, reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The first browser shows "My account" for Test Mentor. |
| ER-2 | 3 | A notice appears at the top: "This session is no longer live, so none of the details below could be read. That usually means this account was signed in on another device — REEP allows one at a time. Sign in again to carry on." with a "Go to sign in" button. |
| ER-3 | 4 | The first browser goes to the sign-in page, `/login`. |
| ER-4 | 5 | The second browser is still signed in on the faculty notebook: leaving the dead session in the first browser did not sign out the live one. |

---

## TC-026 — Sign-in alert email switch on My account

| Field | Value |
|---|---|
| ID | TC-026 |
| Module | Account: My account |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-026` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. The alumnus's "Email me when my account is signed in" switch is off (its
   default). The automated run switches it off first if an earlier run left
   it on.
3. Nothing else signs in as `alumni@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | `alumni@bgscet.ac.in` / `alumni123` |

### Steps

1. Sign in as the alumnus and open the account menu's My account.
2. Tick "Email me when my account is signed in" on the "Email notifications" card.
3. Reload the page.
4. Untick "Email me when my account is signed in".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Email notifications" card lists "Email me when my account is signed in", unticked, with the chip "Off". |
| ER-2 | 2 | The page confirms "Saved — email me when my account is signed in: on." and the chip reads "On". |
| ER-3 | 3 | After the reload the box is still ticked and the chip reads "On": the choice was saved on the account. |
| ER-4 | 4 | The page confirms "Saved — email me when my account is signed in: off." and the chip reads "Off". |
| ER-5 | 2 | **Manual only.** From now until step 4, every sign-in to the account sends it an email. With no mail transport, the API console logs it. |

### Post-conditions

The switch is off again, as the seed left it.

---

## TC-027 — Sign out everywhere from My account

| Field | Value |
|---|---|
| ID | TC-027 |
| Module | Account: My account |
| Priority | P2 |
| Type | Functional, security |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-027` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. Nothing else signs in as `alumni@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | `alumni@bgscet.ac.in` / `alumni123` |

### Steps

1. Sign in as the alumnus and open the account menu's My account.
2. Click "Sign out everywhere".
3. Click Cancel.
4. Click "Sign out everywhere", then "Yes, sign out everywhere".
5. Open `/account` directly.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The page asks for confirmation first: "This ends every session this account holds, including this one — you will be taken to the sign-in page and will need to sign in again. …", with "Yes, sign out everywhere" and Cancel buttons. |
| ER-2 | 3 | The confirmation closes, the "Sign out everywhere" button is back, and the page is still `/account`: nothing was signed out. |
| ER-3 | 4 | The browser goes to the sign-in page, `/login`. |
| ER-4 | 5 | This browser's session has ended too: the address bar reads `/login?next=%2Faccount`. |

---

## TC-028 — Change password refuses a short password, a mismatch and a wrong code

| Field | Value |
|---|---|
| ID | TC-028 |
| Module | Account: change password |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-028` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied, so
   the faculty member holds the password `mentor123`.
2. Nothing else signs in as `mentor@bgscet.ac.in` while the case runs.

### Test data

| Field | Value |
|---|---|
| Account | `mentor@bgscet.ac.in` / `mentor123` |
| Bad entries | code `12345`, new password `short`, repeated as `shorter` |
| Wrong code | `000000` (a code that was not emailed) |
| New password | `a-new-password-e2e` in both fields |

### Steps

1. Sign in as the faculty member with the email address and password.
2. Open the account menu and click Password.
3. Click "Email me a code".
4. Enter the bad code, the short new password and the different repeat in "Code from the email", "New password" and "Type it again", then click outside the fields.
5. Replace them with the wrong code and the new password in both password fields.
6. Click Change password.
7. Sign out from the account menu, then sign in again with the faculty member's email address and the original password.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The browser is on `/account/password`, headed "Change password", with an "Email me a code" button. |
| ER-2 | 3 | The page says "We have emailed a code to mentor@bgscet.ac.in. It expires in 10 minutes." and shows the fields "Code from the email", "New password" and "Type it again". |
| ER-3 | 4 | Three messages appear: "The code is six digits.", "Needs 12 characters or more." and "The two passwords don’t match.". The Change password button stays disabled. |
| ER-4 | 5 | The messages clear and Change password becomes enabled. |
| ER-5 | 6 | The server refuses the code: "That is not right. Check the code, or the current password." The form closes and "Email me a code" is offered again. |
| ER-6 | 7 | The password was not changed: the original password still signs the faculty member in, to `/mentor/notebook`. |
| ER-7 | 3 | **Manual only.** An email with a six-digit code reaches `mentor@bgscet.ac.in`. With no mail transport, the API console logs it. |

---

## TC-029 — Change password with the emailed code

| Field | Value |
|---|---|
| ID | TC-029 |
| Module | Account: change password |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | None (manual only): the proof is a six-digit code sent by email, and the automated suite does not read mail. The screen offers no current-password route; the API's `current_password` option has no control on screen. |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`.
2. Access to the account's mailbox, or to the API console on a development
   server with no mail transport, where the code is logged.
3. **Use a throwaway faculty account, or a database you will reset
   afterwards.** The app cannot put a seeded password back: every password
   it accepts must be at least 12 characters and must not be one of the
   published demo passwords (`mentor123` fails both rules). A throwaway
   account comes from TC-030's first four steps.
4. The account is also signed in on a second browser, to check ER-3.

### Test data

| Field | Value |
|---|---|
| New password | any 12 or more characters, for example `a-changed-password-1` |

### Steps

1. Sign in as the faculty member and open the account menu's Password.
2. Click "Email me a code".
3. Enter the six-digit code from the email in "Code from the email".
4. Enter the new password in "New password" and again in "Type it again".
5. Click Change password.
6. In the second browser, reload the page.
7. Sign out, then try to sign in with the old password.
8. Sign in with the new password.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The page says "We have emailed a code to <address>. It expires in 10 minutes." and an email with the code arrives. |
| ER-2 | 5 | The page confirms "Password changed. Your other devices have been signed out; this one stays." and this browser stays signed in. |
| ER-3 | 6 | The second browser is signed out and lands on the sign-in page. The note it shows is the one-device note from TC-022 ("…signed in on another device…"), because the server reports every retired session the same way. |
| ER-4 | 7 | The old password is refused: "That email and password did not match an account. Check both, or use Continue with Google." |
| ER-5 | 8 | The new password signs the account in, to its home page. |

### Post-conditions

The account holds the new password. It cannot be changed back to a seeded
demo password through the app.

---

## TC-030 — A new faculty member sets a first password from the activation link

| Field | Value |
|---|---|
| ID | TC-030 |
| Module | Authentication: activation link |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-030` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the dev seed has been applied.
2. A new faculty account exists and its activation link is at hand. The Main
   Admin creates one on Faculty → "Add faculty member", in the Department of
   Management Studies (BGSCET), with an address ending `@bgscet.ac.in`; the
   screen shows the link once. The automated run creates the account through
   the same API.
3. The browser has no REEP session.

### Test data

| Field | Value |
|---|---|
| Name | `E2E Activation <run id>` |
| Email | `e2e-activate-<run id>@bgscet.ac.in` |
| First password | `first-password-<run id>` (12 or more characters) |
| Second password | `another-password-e2e` |

### Steps

1. Open the activation link.
2. Enter the first password in the "New password" field.
3. Enter the same password in the "Type it again" field.
4. Click "Set password and sign in".
5. Open the account menu and click Sign out.
6. Open the activation link again.
7. Enter the second password in both fields.
8. Click "Set password and sign in".
9. Sign in on the sign-in page with the new account's email address and the first password.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Set up your REEP password", says "At least 12 characters. A memorable phrase of four or five words clears that easily and is stronger than a short scrambled one.", and shows "New password", "Type it again" and a disabled "Set password and sign in" button. |
| ER-2 | 3 | "Set password and sign in" becomes enabled. |
| ER-3 | 4 | The new faculty member is signed in and lands on `/mentor/notebook` ("Faculty notebook"), with their name and "Faculty" in the top bar. |
| ER-4 | 8 | The used link is refused: "This link has already been used. If that was not you, ask for a new one." and "Ask the placement office to send you a new activation link." The form is gone and the page stays on `/activate`. |
| ER-5 | 9 | The first password works on the ordinary sign-in page: the faculty member lands on `/mentor/notebook`. |

### Post-conditions

The new faculty account exists with the first password. The Main Admin
removes it on the Faculty screen ("Remove or delete…", with a reason); the
automated run removes it through the same API. A removed account keeps its
row, off every list.

---

## TC-031 — Set-password pages refuse a missing or unknown link

| Field | Value |
|---|---|
| ID | TC-031 |
| Module | Authentication: activation and reset links |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-031` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.

### Test data

| Field | Value |
|---|---|
| Unknown link token | `not-a-real-link-token-0123456789abcdef` (issued by nobody) |
| Short password | `short`, repeated as `shorter` |
| Valid password | `a-valid-password-e2e` |

### Steps

1. Open `/activate` with no link.
2. Open `/reset` with no link.
3. Open `/reset?token=not-a-real-link-token-0123456789abcdef`.
4. Enter the short password in "New password" and its different repeat in "Type it again", then click outside the fields.
5. Enter the valid password in both fields.
6. Click "Set new password".
7. Open `/activate?token=not-a-real-link-token-0123456789abcdef`, enter the valid password in both fields and click "Set password and sign in".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Set up your REEP password" and says "This page needs the link from your email — open it from there." and "Ask the placement office to send you a new activation link." No password fields are shown. |
| ER-2 | 2 | The page is headed "Choose a new password", says "This page needs the link from your email — open it from there." and "Ask for a new one from the sign-in page — “Forgot password?”.", and shows no password fields. |
| ER-3 | 3 | The page cannot judge the link before it is used, so the form is shown, with a disabled "Set new password" button. |
| ER-4 | 4 | "Needs 12 characters or more." and "The two passwords don’t match." appear, and "Set new password" stays disabled. |
| ER-5 | 6 | The link is refused: "This link is not valid. Ask for a new one." with the pointer to "Forgot password?" on the sign-in page, and the form is gone. |
| ER-6 | 7 | The activation page refuses it the same way: "This link is not valid. Ask for a new one." and "Ask the placement office to send you a new activation link." Nobody is signed in and the page stays on `/activate`. |

---

## TC-032 — A shortened reset link is reported as a link problem

| Field | Value |
|---|---|
| ID | TC-032 |
| Module | Authentication: activation and reset links |
| Priority | P3 |
| Type | Functional, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-032` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index.

### Test data

| Field | Value |
|---|---|
| Shortened link | `/reset?token=abc123`, as a link cut short by a mail client would arrive (a real token is 43 characters) |
| Password | `a-valid-password-e2e` |

### Steps

1. Open `/reset?token=abc123`.
2. Enter the password in "New password" and again in "Type it again".
3. Click "Set new password".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The link is refused as a link: "This link is not valid. Ask for a new one." with the pointer to "Forgot password?", and the form is gone. |

---

## TC-033 — Reset a forgotten password from the emailed link

| Field | Value |
|---|---|
| ID | TC-033 |
| Module | Authentication: forgotten password |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | None (manual only): the reset link is sent by email, and the automated suite does not read mail. |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`.
2. Access to the account's mailbox, or to the API console on a development
   server with no mail transport, where the email is logged.
3. **Use a throwaway account, or a database you will reset afterwards**,
   for the reason in TC-029, pre-condition 3.
4. The account is signed in on a second browser, to check ER-3.

### Test data

| Field | Value |
|---|---|
| New password | any 12 or more characters |

### Steps

1. On the sign-in page, click "Forgot password?", enter the account's email address and click Send reset link.
2. Open the link in the email "Reset your REEP password".
3. Enter the new password in "New password" and again in "Type it again".
4. Click "Set new password".
5. In the second browser, reload the page.
6. Open the same link from the email again, enter a password in both fields and click "Set new password".
7. Sign in with the new password.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The page is headed "Choose a new password" and shows the form. |
| ER-2 | 4 | "Password updated. Every device has been signed out — including this one — so sign in with your new password." Nobody is signed in on this browser. |
| ER-3 | 5 | The second browser is signed out too. |
| ER-4 | 6 | The used link is refused: "This link has already been used. If that was not you, ask for a new one." |
| ER-5 | 7 | The new password signs the account in, to its home page. |

A link requested more than 60 minutes earlier is refused at step 4 with
"This link has expired. Ask for a new one." instead; check it with an older
link when one is at hand.

### Post-conditions

The account holds the new password.

---

## TC-034 — Continue with Google hands the browser to Google

| Field | Value |
|---|---|
| ID | TC-034 |
| Module | Authentication: Google sign-in |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-034` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, and Google sign-in is configured: `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET` are set for the API, so `GET /api/auth/sso/status`
   answers `"google_available": true`. When they are not, the automated run
   records this case as Skipped, because a server without Google sign-in is
   a supported set-up (TC-037 covers it), not a broken one.
2. The browser has no REEP session.

### Test data

| Field | Value |
|---|---|
| Page asked for | `/student/records` |

### Steps

1. Open `/student/records` directly.
2. Click Continue with Google.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The sign-in page shows an active "Continue with Google" button, with "Continues to your Student workspace" under it. The button carries the page asked for on to Google (its address is `/api/auth/sso/google?next=%2Fstudent%2Frecords`). |
| ER-2 | 2 | REEP's `/api/auth/sso/google?next=%2Fstudent%2Frecords` sends the browser straight on to Google's sign-in page at `accounts.google.com`. The request to Google asks for `openid email profile`, always offers the account chooser, and names REEP's `/api/auth/sso/google/callback` as the address to return to. |
| ER-3 | 2 | **Manual only.** Google shows its account chooser. What happens after an account is chosen is TC-036. |

### Post-conditions

The browser holds a ten-minute Google sign-in attempt cookie; nothing is
signed in.

---

## TC-035 — Google refusals are explained on the sign-in page

| Field | Value |
|---|---|
| ID | TC-035 |
| Module | Authentication: Google sign-in |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-035` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and the college domain is the dev
   default, `bgscet.ac.in`.
2. The browser has no REEP session and no remembered sign-in.

### Test data

| Field | Value |
|---|---|
| Refusal codes | `sso_not_enrolled`, `sso_denied`, and a code the page does not know, `made_up_code` |
| Password attempt | `student@bgscet.ac.in` with the wrong password `wrong-password` |

Google returns a refusal to `/login?error=<code>`. The case opens those
addresses directly, which shows the same page without a Google round trip.

### Steps

1. Open the sign-in page at `/login?error=sso_not_enrolled`.
2. Open the sign-in page at `/login?error=sso_denied`.
3. Open the sign-in page at `/login?error=made_up_code`.
4. Enter the student's email address and the wrong password, and click Sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The card shows: "That Google account is not on the programme roster, so it cannot be signed in. Use your college account — the one ending @bgscet.ac.in. Access is by roster only; there is no self-registration. If you should be on it, ask the placement office to add you." |
| ER-2 | 2 | The card shows: "You stopped at the Google screen, so nothing was signed in. Choose Sign in with Google again when you are ready." |
| ER-3 | 3 | The card shows: "Sign-in did not complete, and the reason given (made_up_code) is not one this page knows. Try again, and quote that wording if you need to report it." |
| ER-4 | 4 | The Google message is replaced by the password answer alone: "That email and password did not match an account. Check both, or use Continue with Google." Only one error is on the card. |

### Post-conditions

One failed password attempt is added to the student's count (see TC-002).

---

## TC-036 — Sign in with Google, on and off the roster

| Field | Value |
|---|---|
| ID | TC-036 |
| Module | Authentication: Google sign-in |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | None (manual only): it needs a real Google account and Google's own sign-in pages, which an automated run cannot drive. |

### Pre-conditions

1. The API has `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` set, and the
   Google console lists `<web origin>/api/auth/sso/google/callback` as an
   authorised redirect URI (`docs/google-sign-in.md`).
2. Two Google accounts: one whose address belongs to a REEP account (the
   roster), and one that does not, for example a personal Gmail.
3. The browser has no REEP session.

### Test data

| Field | Value |
|---|---|
| On the roster | a Google account whose address is a REEP account's email |
| Off the roster | any other Google account |

### Steps

1. Open `/student/records` directly, click Continue with Google and choose the account on the roster.
2. Sign out.
3. Click Continue with Google and choose the account that is not on the roster.
4. On the Google screen, click Cancel instead of choosing an account.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | Google returns the browser to REEP signed in, on `/student/records` for a student (or the role's home when that page is not theirs). "My account" then shows the Google row as "Linked", and "Recent sign-ins" lists the door "Google". |
| ER-2 | 3 | The browser returns to `/login?error=sso_not_enrolled` with the not-on-the-roster message from TC-035, ER-1. Nothing is signed in. |
| ER-3 | 4 | The browser returns to `/login?error=sso_denied` with "You stopped at the Google screen, so nothing was signed in. Choose Sign in with Google again when you are ready." |

### Post-conditions

The roster account is now linked to that Google identity. "Unlink" on My
account removes the link only when the account also holds a password.

---

## TC-037 — Google button when Google sign-in is not configured

| Field | Value |
|---|---|
| ID | TC-037 |
| Module | Authentication: Google sign-in |
| Priority | P3 |
| Type | Functional, configuration |
| Automated test | None (manual only): it needs the API restarted without Google credentials, and the automated run uses the API as it finds it. |

### Pre-conditions

1. The API is running with `GOOGLE_CLIENT_ID` or `GOOGLE_CLIENT_SECRET`
   blank, with a development `ENV`.

### Test data

None.

### Steps

1. Open the sign-in page at `/login`.
2. Open `/api/auth/sso/google` directly.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | "Continue with Google" is shown as a disabled button, with the reason under it: "Google sign-in is not configured on this server yet." The password form is still offered. |
| ER-2 | 2 | The browser is sent back to `/login?error=sso_config`, which says "Google sign-in is not switched on for this server yet, so there is nothing to sign in to. Tell whoever runs the dashboard." |

---

## TC-038 — Reset requests are limited per address

| Field | Value |
|---|---|
| ID | TC-038 |
| Module | Authentication: forgotten password |
| Priority | P3 |
| Type | Functional, negative (throttle) |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-038` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`.
2. Three reset requests have already been made for the address in the last
   hour: send three from this form first, or let the automated run send them
   through the API. Fewer than 96 reset requests in total have reached the
   API in the last hour (the overall limit is 100).

### Test data

| Field | Value |
|---|---|
| Email | a new, unregistered address per run, `tc-038-<run id>@example.invalid` |

### Steps

1. Open the sign-in page at `/login`.
2. Under the card, next to "Need help signing in?", click "Reset your password".
3. Enter the email address in the "Email address" field.
4. Click Send reset link.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The same reset form as "Forgot password?" opens, with the "Email address" field and a Send reset link button. |
| ER-2 | 4 | The fourth request within the hour is refused: the form is replaced by "Too many reset requests. Please wait an hour and try again." instead of the usual answer. |

### Post-conditions

The address cannot ask for another reset for an hour, and the request
counts toward the API's 100 an hour. Restarting the API clears both counts.

---

## TC-039 — The links around the sign-in card

| Field | Value |
|---|---|
| ID | TC-039 |
| Module | Authentication: sign-in page |
| Priority | P3 |
| Type | Functional, navigation |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-039` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, and Google sign-in is configured (see
   TC-034, pre-condition 1), so the Google button is a link that can take
   the focus. When it is not, the automated run records this case as
   Skipped.
2. The browser has no REEP session and no remembered sign-in.

### Test data

None.

### Steps

1. Open the sign-in page at `/login`.
2. Under "Choose your portal", select Faculty.
3. Click "Already approved? Sign in →".
4. Click "Forgot password?".
5. Click "Forgot password?" again.
6. Click "New student? Register →".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | Student is selected again, and the focus moves to the "Continue with Google" button, the way in for an approved student who has not set a password yet. |
| ER-2 | 4 | The reset form opens with the "Email address" field. |
| ER-3 | 5 | The reset form closes. |
| ER-4 | 6 | The browser goes to the public application form, `/register`, headed "Student registration". |

---

## TC-040 — Field messages clear once the fields are filled

| Field | Value |
|---|---|
| ID | TC-040 |
| Module | Authentication: password sign-in |
| Priority | P3 |
| Type | Functional, negative |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-040` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`, so the password form is shown.
2. The browser has no REEP session and no remembered sign-in.

### Test data

| Field | Value |
|---|---|
| Email | `student@bgscet.ac.in` |
| Password | `wrong-password` (nothing is submitted) |

### Steps

1. Open the sign-in page at `/login`.
2. Click Sign in without entering anything.
3. Enter the email address in the "Institutional email or USN" field.
4. Enter the password in the "Password" field.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | "Enter your institutional email or usn." and "Enter your password." appear (as in TC-004). |
| ER-2 | 3 | The ID message goes as soon as the field is filled, and the field is no longer marked invalid. |
| ER-3 | 4 | The password message goes too. |

---

## TC-041 — Show password from the keyboard

| Field | Value |
|---|---|
| ID | TC-041 |
| Module | Authentication: sign-in page |
| Priority | P3 |
| Type | Functional, accessibility |
| Automated test | `tests/auth-sync.spec.ts`, title tagged `@TC-041` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the
   index, with a development `ENV`.

### Test data

| Field | Value |
|---|---|
| Password | `Typed-but-not-sent-1` (any text; nothing is submitted) |

### Steps

1. Open the sign-in page at `/login`.
2. Enter the password in the "Password" field.
3. Press Tab to move to the "Show password" button, then press Enter.
4. Press Enter again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The focus is on the eye button straight after the field. The password is shown as readable text, and the button is named "Hide password" and shown as pressed. |
| ER-2 | 4 | The password is masked again, and the button is named "Show password" and no longer pressed. The typed value is unchanged. |
