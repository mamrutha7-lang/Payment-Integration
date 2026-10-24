/**
 * Authentication: the automated twins of the cases in
 * test-management/cases/01-authentication.md.
 *
 * THE TAG IS THE LINK. Each test's title ends with the `@TC-NNN` ID of the case
 * it automates, its top-level `test.step()` titles are that case's steps word
 * for word, and every assertion message names the expected result it checks
 * (`TC-001 ER-4: ...`). The CSV reporter (tests/reporters/manual-csv-reporter.ts)
 * FAILS THE RUN when a tag names no case in the manual files or a test's steps
 * have drifted from the manual steps, so the two suites cannot quietly
 * disagree. Change a case and its test in the same commit.
 *
 * These drive the REAL app, with nothing mocked, because a mocked
 * `/api/auth/login` would test the mock. The shared pre-conditions are checked
 * before every test by the `test` imported from ./support/reep, which marks
 * the test Blocked when they do not hold.
 */
import { ACCOUNTS, block, expect, test } from './support/reep';
import type { APIRequestContext, Page, PlaywrightWorkerArgs, TestInfo } from '@playwright/test';

/** The "Test data" tables: the dev seed's student. */
const STUDENT = { ...ACCOUNTS.student, firstName: 'Test' } as const;
const WRONG_PASSWORD = 'wrong-password';

/** The app's own words, so a copy change fails a named expected result
 *  instead of drifting away from the manual file unnoticed.
 *  `passwordErrorFor` in apps/web/src/app/features/login/login.component.ts: */
const INVALID_CREDENTIALS =
  'That email and password did not match an account. Check both, or use Continue with Google.';
/** `_FORGOT_ANSWER` in apps/api-py/app/routers/passwords.py, which the login
 *  screen shows verbatim: */
const RESET_ANSWER =
  "If that address belongs to a REEP account, we've emailed it a link to reset your " +
  'password - or to set one up, if you have not yet.';

const idField = (page: Page) => page.getByLabel('Institutional email or USN');
// By placeholder, not by label: the label also contains the "Show password"
// toggle, whose aria-label would match a `getByLabel('Password')` too.
const passwordField = (page: Page) => page.getByPlaceholder('Enter your password');
// `exact`: "Already approved? Sign in →" is a button whose name contains this one.
const signInButton = (page: Page) => page.getByRole('button', { name: 'Sign in', exact: true });
const studentPortal = (page: Page) => page.getByRole('radio', { name: /^Student\b/ });
const greeting = (page: Page) =>
  page.getByRole('heading', { level: 1, name: `Welcome back, ${STUDENT.firstName}`, exact: true });

// ---------------------------------------------------------------------------
// TC-004 onward. Everything below is local to this module: the words the
// screens say (quoted from the templates and routers named beside each), the
// locators, and the few API calls that set a case up or clean it away. Those
// API calls are never a manual step; they stand in for a pre-condition.
// ---------------------------------------------------------------------------

/** Unique to this run, so repeated runs never collide on created data. */
const RUN_ID = Date.now().toString(36);

const FACULTY = { ...ACCOUNTS.mentor, roleLabel: 'Faculty' } as const;
const ALUMNUS = { ...ACCOUNTS.alumni, roleLabel: 'Alumni' } as const;
const MAIN_ADMIN = { ...ACCOUNTS.admin, roleLabel: 'Main Admin' } as const;
/** `stu.usn` in apps/api-py/app/seed.py. */
const STUDENT_USN = '1BG24MBA001';

/** The ID field's wording per portal: `PORTALS` and `ADMIN_DOOR` in
 *  apps/web/src/app/features/login/login.component.ts. */
const PORTAL_WORDING = {
  student: {
    label: 'Institutional email or USN',
    placeholder: 'asha.rao@bgscet.ac.in or 1BG24MBA001',
    helper: 'Use your BGSCET student email or University Seat Number.',
    workspace: 'Student',
  },
  mentor: {
    label: 'Institutional email',
    placeholder: 'kavya.n@bgscet.ac.in',
    helper: 'Use your faculty email.',
    workspace: 'Faculty',
  },
  alumni: {
    label: 'Registered email',
    placeholder: 'rohan.shetty@alumni.bgscet.ac.in',
    helper: 'Use the email from your alumni registration.',
    workspace: 'Alumni',
  },
  admin: {
    label: 'Institutional email',
    placeholder: 'placement.admin@bgscet.ac.in',
    helper: 'Use your admin email.',
    workspace: 'Admin',
  },
} as const;

/** login.component.html. */
const SIGNED_OUT_ELSEWHERE =
  'You were signed out because this account was signed in on another device. REEP allows ' +
  'one device at a time — sign in again here to carry on, and the other device will be ' +
  'signed out.';
const SUBTITLE_NEXT = 'Sign in to carry on to the page you asked for.';
const SUBTITLE_PLAIN = 'Sign in to continue to your REEP workspace.';

/** `messageFor` in login.component.ts, with the dev roster domain. */
const SSO_NOT_ENROLLED =
  'That Google account is not on the programme roster, so it cannot be signed in. Use your ' +
  'college account — the one ending @bgscet.ac.in. Access is by roster only; there is no ' +
  'self-registration. If you should be on it, ask the placement office to add you.';
const SSO_DENIED =
  'You stopped at the Google screen, so nothing was signed in. Choose Sign in with Google ' +
  'again when you are ready.';
const SSO_UNKNOWN = (code: string) =>
  `Sign-in did not complete, and the reason given (${code}) is not one this page knows. ` +
  'Try again, and quote that wording if you need to report it.';

/** account.component.html. */
const SESSION_NOT_LIVE =
  'This session is no longer live, so none of the details below could be read. That usually ' +
  'means this account was signed in on another device — REEP allows one at a time. Sign in ' +
  'again to carry on.';
const SIGN_OUT_EVERYWHERE_WARNING =
  'This ends every session this account holds, including this one — you will be taken to ' +
  'the sign-in page and will need to sign in again.';
/** `NOTIFICATION_PREFS` in apps/api-py/app/routers/auth.py. */
const SIGN_IN_ALERTS_LABEL = 'Email me when my account is signed in';

/** apps/api-py/app/routers/passwords.py and account_links.py. */
const WRONG_PROOF = 'That is not right. Check the code, or the current password.';
const LINK_NOT_VALID = 'This link is not valid. Ask for a new one.';
const LINK_USED = 'This link has already been used. If that was not you, ask for a new one.';
const TOO_MANY_RESETS = 'Too many reset requests. Please wait an hour and try again.';
/** password-link.component.{ts,html}. */
const NEEDS_THE_LINK = 'This page needs the link from your email — open it from there.';
const ASK_THE_OFFICE = 'Ask the placement office to send you a new activation link.';
const ASK_FOR_A_RESET = 'Ask for a new one from the sign-in page — “Forgot password?”.';
const NEEDS_12 = 'Needs 12 characters or more.';
const MISMATCH = 'The two passwords don’t match.';

const idFieldLabelled = (page: Page, label: string) => page.getByLabel(label, { exact: true });
const portal = (page: Page, label: 'Student' | 'Faculty' | 'Alumni') =>
  page.getByRole('radio', { name: new RegExp(`^${label}\\b`) });
const mainAdminDoor = (page: Page) =>
  page.getByRole('button', { name: 'Main Admin — open the REEP Admin Console' });
const rememberMe = (page: Page) => page.getByRole('checkbox', { name: 'Remember me' });
const googleLink = (page: Page) => page.getByRole('link', { name: 'Continue with Google' });
const googleLine = (page: Page, workspace: string) =>
  page.getByText(`Continues to your ${workspace} workspace`, { exact: true });
const cardSubtitle = (page: Page, text: string) => page.getByText(text, { exact: true });
const alertSaying = (page: Page, text: string) => page.getByRole('alert').filter({ hasText: text });
const heading = (page: Page, name: string) =>
  page.getByRole('heading', { level: 1, name, exact: true });
/** The account block's button for staff, alumni and the Main Admin: its name is
 *  the person's name and role label (the avatar initials are aria-hidden). */
const accountButton = (page: Page, name: string, roleLabel: string) =>
  page.getByRole('banner').getByRole('button', { name: `${name} ${roleLabel}`, exact: true });
const brand = (page: Page) => page.getByRole('link', { name: 'REEP home' });
const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
/** The page's URL is exactly this path (and query), on the app's own origin. */
const exactly = (pathAndQuery: string) =>
  new RegExp(`^https?://[^/]+${escapeRegExp(pathAndQuery)}$`);

/** Fills and submits the sign-in form that is already on screen, under the
 *  portal whose ID-field wording is showing. */
async function submitSignIn(
  page: Page,
  shownPortal: keyof typeof PORTAL_WORDING,
  email: string,
  password: string,
): Promise<void> {
  await idFieldLabelled(page, PORTAL_WORDING[shownPortal].label).fill(email);
  await passwordField(page).fill(password);
  await signInButton(page).click();
}

/** Opens the account menu behind the name in the top bar, and signs out. */
async function signOutFromMenu(page: Page, name: string, roleLabel: string): Promise<void> {
  await accountButton(page, name, roleLabel).click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
}

/** How account.component.ts prints `login_events.at`, in the browser's zone. */
async function signInClock(page: Page): Promise<string> {
  return page.evaluate(() =>
    new Date().toLocaleString('en-IN', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    }),
  );
}

/** The first row of the "Recent sign-ins" table, as [When, Door] cells. */
const firstSignInRow = (page: Page) =>
  page.getByRole('region', { name: 'Recent sign-ins' }).getByRole('row').nth(1).getByRole('cell');

/** A signed-in API client for the Main Admin, used only to set a case up or
 *  clean it away (creating and removing a throwaway faculty account). Its own
 *  cookie jar, so it never touches the page's session. */
async function mainAdminApi(
  playwright: PlaywrightWorkerArgs['playwright'],
  baseURL: string | undefined,
  testInfo: TestInfo,
): Promise<APIRequestContext> {
  const api = await playwright.request.newContext({ baseURL });
  const response = await api.post('/api/auth/login', {
    data: { email: MAIN_ADMIN.email, password: MAIN_ADMIN.password },
  });
  if (!response.ok()) {
    await api.dispose();
    block(testInfo, `the Main Admin could not sign in to set the case up (${response.status()}).`);
  }
  return api;
}

/**
 * GET /api/auth/sso/status: TC-034 and TC-039 need the Google door open.
 *
 * SKIPPED, not Blocked, when it is shut. Every other pre-condition here
 * describes a broken environment, but a blank GOOGLE_CLIENT_ID is a supported
 * way to run REEP (the button renders disabled with its reason, and TC-037
 * covers that state by hand). Blocking would turn every run on such a machine
 * red for a feature it deliberately does not have.
 */
async function skipUnlessGoogleConfigured(page: Page, testInfo: TestInfo): Promise<void> {
  const status = (await (await page.request.get('/api/auth/sso/status')).json()) as {
    google_available?: boolean;
  };
  testInfo.skip(
    status.google_available !== true,
    'Google sign-in is not configured on this server (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are blank); TC-037 covers that state by hand.',
  );
}

test.describe('Authentication', () => {
  test('Successful login with valid credentials @TC-001', async ({ page }) => {
    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Welcome back', exact: true }),
        'TC-001 ER-1: the "Welcome back" sign-in card is shown',
      ).toBeVisible();
      await expect(
        idField(page),
        'TC-001 ER-1: the "Institutional email or USN" field is shown',
      ).toBeVisible();
      await expect(passwordField(page), 'TC-001 ER-1: the "Password" field is shown').toBeVisible();
      await expect(signInButton(page), 'TC-001 ER-1: the Sign in button is shown').toBeVisible();
    });

    await test.step('2. Under "Choose your portal", select Student', async () => {
      // Student is the default portal, so move off it first. Otherwise ER-2
      // would pass even if clicking a portal card did nothing.
      await page.getByRole('radio', { name: /^Faculty\b/ }).click();
      await expect(
        studentPortal(page),
        'TC-001 ER-2 (arrange): another portal is selected',
      ).not.toBeChecked();
      await studentPortal(page).click();
      await expect(
        studentPortal(page),
        'TC-001 ER-2: the Student portal card is selected',
      ).toBeChecked();
      await expect(
        idField(page),
        'TC-001 ER-2: the ID field reads "Institutional email or USN"',
      ).toBeVisible();
    });

    await test.step('3. Enter the email address in the "Institutional email or USN" field', async () => {
      await idField(page).fill(STUDENT.email);
    });

    await test.step('4. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(STUDENT.password);
      await expect(passwordField(page), 'TC-001 ER-3: the password is masked').toHaveAttribute(
        'type',
        'password',
      );
    });

    await test.step('5. Click Sign in', async () => {
      await signInButton(page).click();
      await expect(page, 'TC-001 ER-4: the browser lands on the student home').toHaveURL(
        /\/student$/,
      );
      await expect(
        greeting(page),
        'TC-001 ER-4: the home greets the student by first name',
      ).toBeVisible();
    });

    await test.step('6. Reload the page', async () => {
      await page.reload();
      await expect(
        page,
        'TC-001 ER-5: the student is still on /student after the reload',
      ).toHaveURL(/\/student$/);
      await expect(
        greeting(page),
        'TC-001 ER-5: the greeting is shown again, so the session held',
      ).toBeVisible();
    });
  });

  test('Login is refused for an invalid password @TC-002', async ({ page }) => {
    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Under "Choose your portal", select Student', async () => {
      await studentPortal(page).click();
    });

    await test.step('3. Enter the email address in the "Institutional email or USN" field', async () => {
      await idField(page).fill(STUDENT.email);
    });

    await test.step('4. Enter the invalid password in the "Password" field', async () => {
      await passwordField(page).fill(WRONG_PASSWORD);
    });

    await test.step('5. Click Sign in', async () => {
      await signInButton(page).click();
      // ER-1 first: the alert is what proves the request came back, so the
      // URL check after it cannot pass merely because nothing happened yet.
      await expect(
        page.getByRole('alert').filter({ hasText: INVALID_CREDENTIALS }),
        'TC-002 ER-1: the card shows the invalid-credentials message',
      ).toBeVisible();
      await expect(page, 'TC-002 ER-2: the page stays on /login').toHaveURL(/\/login$/);
      await expect(passwordField(page), 'TC-002 ER-3: the password field is emptied').toHaveValue(
        '',
      );
      await expect(idField(page), 'TC-002 ER-3: the email address stays filled in').toHaveValue(
        STUDENT.email,
      );
    });

    await test.step('6. Open /student directly in the same tab', async () => {
      await page.goto('/student');
      await expect(
        page,
        'TC-002 ER-4: no session was created, so the app sends the browser back to sign in',
      ).toHaveURL(/\/login\?next=%2Fstudent$/);
      await expect(
        signInButton(page),
        'TC-002 ER-4: the sign-in form is shown again',
      ).toBeVisible();
    });
  });

  test('Password reset request @TC-003', async ({ page }) => {
    // ER-4, the email itself, is checked by hand; the CSV's "Manual-only
    // Checks" column reads it from the manual file. The address is a new,
    // unregistered one per run. The API allows 3 reset requests per address
    // per hour, and answers every address with the same sentence, so this
    // checks what the page shows without using up that limit. See the note
    // under the case's "Test data".
    const email = `tc-003-${Date.now().toString(36)}@example.invalid`;
    const emailField = page.getByLabel('Email address', { exact: true });
    const sendButton = page.getByRole('button', { name: 'Send reset link' });

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Click "Forgot password?"', async () => {
      await page.getByRole('button', { name: 'Forgot password?' }).click();
      await expect(
        emailField,
        'TC-003 ER-1: the reset form shows an "Email address" field',
      ).toBeVisible();
      await expect(
        sendButton,
        'TC-003 ER-1: Send reset link is disabled while the field is empty',
      ).toBeDisabled();
    });

    await test.step('3. Enter the email address in the "Email address" field', async () => {
      await emailField.fill(email);
      await expect(sendButton, 'TC-003 ER-2: Send reset link becomes enabled').toBeEnabled();
    });

    await test.step('4. Click Send reset link', async () => {
      await sendButton.click();
      await expect(
        page.getByRole('status').filter({ hasText: RESET_ANSWER }),
        'TC-003 ER-3: the form is replaced by the one answer given for every address',
      ).toHaveText(RESET_ANSWER);
      await expect(emailField, 'TC-003 ER-3: the form itself is gone').toBeHidden();
      await expect(page, 'TC-003 ER-3: the page stays on /login').toHaveURL(/\/login$/);
    });
  });

  test('Sign-in form asks for a missing ID and password @TC-004', async ({ page }) => {
    // Every POST to /api/auth/login this page makes. The empty form must send none.
    const signInRequests: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/api/auth/login')) {
        signInRequests.push(request.url());
      }
    });
    const idMessage = 'Enter your institutional email or usn.';
    const passwordMessage = 'Enter your password.';

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
      await expect(signInButton(page), 'TC-004 (arrange): the sign-in form is shown').toBeVisible();
    });

    await test.step('2. Click Sign in without entering anything', async () => {
      await signInButton(page).click();
      await expect(
        alertSaying(page, idMessage),
        'TC-004 ER-1: the ID field asks for the ID, in its own (lower-cased) wording',
      ).toHaveText(idMessage);
      await expect(
        alertSaying(page, passwordMessage),
        'TC-004 ER-1: the password field asks for the password',
      ).toHaveText(passwordMessage);
      await expect(
        page.getByRole('alert'),
        'TC-004 ER-1: exactly the two field messages',
      ).toHaveCount(2);
      await expect(idField(page), 'TC-004 ER-1: the ID field is marked invalid').toHaveAttribute(
        'aria-invalid',
        'true',
      );
      await expect(
        passwordField(page),
        'TC-004 ER-1: the password field is marked invalid',
      ).toHaveAttribute('aria-invalid', 'true');
      expect(signInRequests, 'TC-004 ER-2: nothing was sent to the server').toHaveLength(0);
      await expect(page, 'TC-004 ER-2: the page stays on /login').toHaveURL(exactly('/login'));
    });

    await test.step('3. Under "Choose your portal", select Faculty', async () => {
      await portal(page, 'Faculty').click();
      await expect(
        alertSaying(page, 'Enter your institutional email.'),
        "TC-004 ER-3: the ID message is in the Faculty portal's wording",
      ).toHaveText('Enter your institutional email.');
    });

    await test.step('4. Select Alumni', async () => {
      await portal(page, 'Alumni').click();
      await expect(
        alertSaying(page, 'Enter your registered email.'),
        "TC-004 ER-4: the ID message is in the Alumni portal's wording",
      ).toHaveText('Enter your registered email.');
    });
  });

  test('Portal cards and the Main Admin door relabel the ID field @TC-005', async ({ page }) => {
    /** The ID field, its placeholder, its hint and the Google line, for one portal. */
    async function expectWording(key: keyof typeof PORTAL_WORDING, er: string) {
      const wording = PORTAL_WORDING[key];
      const field = idFieldLabelled(page, wording.label);
      await expect(field, `${er}: the ID field is labelled "${wording.label}"`).toBeVisible();
      await expect(field, `${er}: the placeholder is the ${key} example`).toHaveAttribute(
        'placeholder',
        wording.placeholder,
      );
      await expect(
        page.getByText(wording.helper, { exact: true }),
        `${er}: the hint reads "${wording.helper}"`,
      ).toBeVisible();
      await expect(
        googleLine(page, wording.workspace),
        `${er}: the Google line names the ${wording.workspace} workspace`,
      ).toBeVisible();
    }

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
      await expect(
        page.getByRole('radio'),
        'TC-005 ER-1: three portal cards are offered',
      ).toHaveCount(3);
      await expect(
        page.getByRole('radio', { name: /^Student\b.*Track skills, jobs & growth/ }),
        'TC-005 ER-1: the Student card and its description',
      ).toBeChecked();
      await expect(
        page.getByRole('radio', { name: /^Faculty\b.*Guide students, verify skills/ }),
        'TC-005 ER-1: the Faculty card and its description',
      ).not.toBeChecked();
      await expect(
        page.getByRole('radio', { name: /^Alumni\b.*Explore roles, stay connected/ }),
        'TC-005 ER-1: the Alumni card and its description',
      ).not.toBeChecked();
      await expectWording('student', 'TC-005 ER-1');
    });

    await test.step('2. Under "Choose your portal", select Faculty', async () => {
      await portal(page, 'Faculty').click();
      await expect(portal(page, 'Faculty'), 'TC-005 ER-2: Faculty is selected').toBeChecked();
      await expect(portal(page, 'Student'), 'TC-005 ER-2: Student is not').not.toBeChecked();
      await expectWording('mentor', 'TC-005 ER-2');
    });

    await test.step('3. Select Alumni', async () => {
      await portal(page, 'Alumni').click();
      await expectWording('alumni', 'TC-005 ER-3');
    });

    await test.step('4. Click "Main Admin — open the REEP Admin Console"', async () => {
      await mainAdminDoor(page).click();
      await expect(
        mainAdminDoor(page),
        'TC-005 ER-4: the Main Admin door is pressed',
      ).toHaveAttribute('aria-pressed', 'true');
      await expect(
        page.getByRole('radio', { checked: true }),
        'TC-005 ER-4: no portal card is selected',
      ).toHaveCount(0);
      await expectWording('admin', 'TC-005 ER-4');
      await expect(
        idFieldLabelled(page, PORTAL_WORDING.admin.label),
        'TC-005 ER-4: the cursor is in the ID field',
      ).toBeFocused();
    });

    await test.step('5. Select Student', async () => {
      await portal(page, 'Student').click();
      await expect(portal(page, 'Student'), 'TC-005 ER-5: Student is selected again').toBeChecked();
      await expect(
        mainAdminDoor(page),
        'TC-005 ER-5: the Main Admin door is no longer pressed',
      ).toHaveAttribute('aria-pressed', 'false');
      await expect(
        idFieldLabelled(page, PORTAL_WORDING.student.label),
        'TC-005 ER-5: the ID field reads "Institutional email or USN" again',
      ).toBeVisible();
    });
  });

  test('Show password and Hide password @TC-006', async ({ page }) => {
    const typed = 'Typed-but-not-sent-1';
    const show = page.getByRole('button', { name: 'Show password' });
    const hide = page.getByRole('button', { name: 'Hide password' });

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(typed);
      await expect(passwordField(page), 'TC-006 ER-1: the password is masked').toHaveAttribute(
        'type',
        'password',
      );
    });

    await test.step('3. Click the eye button, "Show password", at the end of the field', async () => {
      // A click lands on whatever is drawn on top at that point, and Playwright
      // refuses to click when that is another element - which is what the
      // focused Password field used to be. Such a refusal is reported against
      // ER-2 within the usual expect timeout rather than after the whole test's.
      await show.click({ timeout: 5_000 }).catch((error: Error) => {
        throw new Error(
          `TC-006 ER-2: the "Show password" button did not receive the click. ${error.message}`,
        );
      });
      await expect(
        passwordField(page),
        'TC-006 ER-2: the password is readable text',
      ).toHaveAttribute('type', 'text');
      await expect(hide, 'TC-006 ER-2: the button is now named "Hide password"').toBeVisible();
      await expect(hide, 'TC-006 ER-2: the button is shown as pressed').toHaveAttribute(
        'aria-pressed',
        'true',
      );
    });

    await test.step('4. Click the same button again, now named "Hide password"', async () => {
      await hide.click();
      await expect(
        passwordField(page),
        'TC-006 ER-3: the password is masked again',
      ).toHaveAttribute('type', 'password');
      await expect(show, 'TC-006 ER-3: the button is named "Show password" again').toHaveAttribute(
        'aria-pressed',
        'false',
      );
      await expect(passwordField(page), 'TC-006 ER-3: the typed value is unchanged').toHaveValue(
        typed,
      );
    });
  });

  test('Remember me keeps the ID and portal on this device @TC-007', async ({ page }) => {
    const facultyId = idFieldLabelled(page, PORTAL_WORDING.mentor.label);

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Under "Choose your portal", select Faculty', async () => {
      await portal(page, 'Faculty').click();
    });

    await test.step('3. Enter the email address in the "Institutional email" field', async () => {
      await facultyId.fill(FACULTY.email);
    });

    await test.step('4. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(FACULTY.password);
    });

    await test.step('5. Tick "Remember me"', async () => {
      await rememberMe(page).check();
    });

    await test.step('6. Click Sign in', async () => {
      await signInButton(page).click();
      await expect(page, 'TC-007 ER-1: the faculty member lands on the notebook').toHaveURL(
        exactly('/mentor/notebook'),
      );
    });

    await test.step('7. Open the account menu (the name in the top right) and click Sign out', async () => {
      await signOutFromMenu(page, FACULTY.name, FACULTY.roleLabel);
      await expect(page, 'TC-007 ER-2: the sign-in page opens').toHaveURL(exactly('/login'));
      await expect(
        portal(page, 'Faculty'),
        'TC-007 ER-2: the Faculty portal is remembered',
      ).toBeChecked();
      await expect(facultyId, 'TC-007 ER-2: the email address is remembered').toHaveValue(
        FACULTY.email,
      );
      await expect(rememberMe(page), 'TC-007 ER-2: "Remember me" is still ticked').toBeChecked();
      await expect(
        passwordField(page),
        'TC-007 ER-2: the password is never remembered',
      ).toHaveValue('');
    });

    await test.step('8. Untick "Remember me", enter the password again and click Sign in', async () => {
      await rememberMe(page).uncheck();
      await passwordField(page).fill(FACULTY.password);
      await signInButton(page).click();
      await expect(page, 'TC-007 ER-3: the faculty member is signed in again').toHaveURL(
        exactly('/mentor/notebook'),
      );
    });

    await test.step('9. Open the account menu and click Sign out', async () => {
      await signOutFromMenu(page, FACULTY.name, FACULTY.roleLabel);
      await expect(page, 'TC-007 ER-4: the sign-in page opens').toHaveURL(exactly('/login'));
      await expect(
        portal(page, 'Student'),
        'TC-007 ER-4: Student is selected, as for a new visitor',
      ).toBeChecked();
      await expect(idField(page), 'TC-007 ER-4: the ID field is empty').toHaveValue('');
      await expect(rememberMe(page), 'TC-007 ER-4: "Remember me" is unticked').not.toBeChecked();
    });
  });

  test('Remember me through the Main Admin door keeps the admin wording @TC-008', async ({
    page,
  }) => {
    const adminId = idFieldLabelled(page, PORTAL_WORDING.admin.label);

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Click "Main Admin — open the REEP Admin Console"', async () => {
      await mainAdminDoor(page).click();
    });

    await test.step('3. Enter the email address in the "Institutional email" field', async () => {
      await adminId.fill(MAIN_ADMIN.email);
    });

    await test.step('4. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(MAIN_ADMIN.password);
    });

    await test.step('5. Tick "Remember me"', async () => {
      await rememberMe(page).check();
    });

    await test.step('6. Click Sign in', async () => {
      await signInButton(page).click();
      await expect(page, 'TC-008 ER-1: the Main Admin lands on /admin').toHaveURL(
        exactly('/admin'),
      );
    });

    await test.step('7. Open the account menu (the name in the top right) and click Sign out', async () => {
      await signOutFromMenu(page, MAIN_ADMIN.name, MAIN_ADMIN.roleLabel);
      await expect(page, 'TC-008 ER-2: the sign-in page opens').toHaveURL(exactly('/login'));
      await expect(
        page.getByRole('textbox', { name: /email/i }),
        'TC-008 ER-2: the email address is remembered',
      ).toHaveValue(MAIN_ADMIN.email);
      await expect(rememberMe(page), 'TC-008 ER-2: "Remember me" is still ticked').toBeChecked();
      await expect(
        mainAdminDoor(page),
        'TC-008 ER-3: the Main Admin door is pressed again',
      ).toHaveAttribute('aria-pressed', 'true');
      await expect(
        adminId,
        'TC-008 ER-3: the ID field is labelled "Institutional email"',
      ).toHaveValue(MAIN_ADMIN.email);
    });
  });

  test('Faculty member signs in and lands on the faculty notebook @TC-009', async ({ page }) => {
    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Under "Choose your portal", select Faculty', async () => {
      await portal(page, 'Faculty').click();
    });

    await test.step('3. Enter the email address in the "Institutional email" field', async () => {
      await idFieldLabelled(page, PORTAL_WORDING.mentor.label).fill(FACULTY.email);
    });

    await test.step('4. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(FACULTY.password);
    });

    await test.step('5. Click Sign in', async () => {
      await signInButton(page).click();
      await expect(page, 'TC-009 ER-1: the browser lands on /mentor/notebook').toHaveURL(
        exactly('/mentor/notebook'),
      );
      await expect(
        heading(page, 'Faculty notebook'),
        'TC-009 ER-1: headed "Faculty notebook"',
      ).toBeVisible();
      await expect(
        brand(page),
        'TC-009 ER-2: the top bar reads "REEP Faculty console"',
      ).toContainText('Faculty console');
      await expect(
        accountButton(page, FACULTY.name, FACULTY.roleLabel),
        'TC-009 ER-2: the account block shows "Test Mentor" and "Faculty"',
      ).toBeVisible();
      await expect(page, 'TC-009 ER-2: the tab is titled "REEP · Faculty"').toHaveTitle(
        'REEP · Faculty',
      );
      const nav = page.getByRole('navigation', { name: 'Main' });
      for (const item of ['Notebook', 'Mentee Log', 'Leave Requests']) {
        await expect(
          nav.getByRole('link', { name: item, exact: true }),
          `TC-009 ER-3: the sidebar offers ${item}`,
        ).toBeVisible();
      }
    });
  });

  test('Alumnus signs in and lands on the alumni profile @TC-010', async ({ page }) => {
    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Under "Choose your portal", select Alumni', async () => {
      await portal(page, 'Alumni').click();
    });

    await test.step('3. Enter the email address in the "Registered email" field', async () => {
      await idFieldLabelled(page, PORTAL_WORDING.alumni.label).fill(ALUMNUS.email);
    });

    await test.step('4. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(ALUMNUS.password);
    });

    await test.step('5. Click Sign in', async () => {
      await signInButton(page).click();
      await expect(page, 'TC-010 ER-1: the browser lands on /alumni').toHaveURL(exactly('/alumni'));
      await expect(heading(page, 'My Profile'), 'TC-010 ER-1: headed "My Profile"').toBeVisible();
      await expect(brand(page), 'TC-010 ER-2: the top bar reads "REEP Alumni"').toContainText(
        'Alumni',
      );
      await expect(
        accountButton(page, ALUMNUS.name, ALUMNUS.roleLabel),
        'TC-010 ER-2: the account block shows "Test Alumnus" and "Alumni"',
      ).toBeVisible();
      await expect(page, 'TC-010 ER-2: the tab is titled "REEP · Alumni"').toHaveTitle(
        'REEP · Alumni',
      );
      await expect(
        page.getByRole('navigation', { name: 'Main' }).getByRole('link'),
        'TC-010 ER-3: the sidebar offers the two alumni screens',
      ).toHaveText([/My Profile/, /Jobs Sheet/]);
    });
  });

  test('Main Admin signs in through the Main Admin door @TC-011', async ({ page }) => {
    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Click "Main Admin — open the REEP Admin Console"', async () => {
      await mainAdminDoor(page).click();
    });

    await test.step('3. Enter the email address in the "Institutional email" field', async () => {
      await idFieldLabelled(page, PORTAL_WORDING.admin.label).fill(MAIN_ADMIN.email);
    });

    await test.step('4. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(MAIN_ADMIN.password);
    });

    await test.step('5. Click Sign in', async () => {
      await signInButton(page).click();
      await expect(page, 'TC-011 ER-1: the browser lands on /admin').toHaveURL(exactly('/admin'));
      await expect(
        heading(page, 'What do you want to do?'),
        'TC-011 ER-1: headed "What do you want to do?"',
      ).toBeVisible();
      await expect(
        brand(page),
        'TC-011 ER-2: the top bar reads "REEP Admin console"',
      ).toContainText('Admin console');
      await expect(
        page.getByRole('banner').getByText('Scope · Whole programme'),
        'TC-011 ER-2: the top bar carries "Scope · Whole programme"',
      ).toBeVisible();
      await expect(
        accountButton(page, MAIN_ADMIN.name, MAIN_ADMIN.roleLabel),
        'TC-011 ER-2: the account block shows "Main Admin (seed)" and "Main Admin"',
      ).toBeVisible();
      await expect(page, 'TC-011 ER-2: the tab is titled "REEP · Main Admin"').toHaveTitle(
        'REEP · Main Admin',
      );
    });
  });

  test('The portal picked does not decide where a user lands @TC-012', async ({ page }) => {
    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Under "Choose your portal", select Alumni', async () => {
      await portal(page, 'Alumni').click();
    });

    await test.step('3. Enter the faculty member\'s email address in the "Registered email" field', async () => {
      await idFieldLabelled(page, PORTAL_WORDING.alumni.label).fill(FACULTY.email);
    });

    await test.step('4. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(FACULTY.password);
    });

    await test.step('5. Click Sign in', async () => {
      await signInButton(page).click();
      await expect(page, 'TC-012 ER-1: the faculty member lands on the faculty home').toHaveURL(
        exactly('/mentor/notebook'),
      );
      await expect(
        heading(page, 'Faculty notebook'),
        'TC-012 ER-1: the faculty notebook, not the alumni profile',
      ).toBeVisible();
    });
  });

  test('Sign-in returns to the page that was asked for @TC-013', async ({ page }) => {
    await test.step('1. Open /student/records directly', async () => {
      await page.goto('/student/records');
      await expect(
        page,
        'TC-013 ER-1: the app sends the browser to sign in, carrying the page',
      ).toHaveURL(exactly('/login?next=%2Fstudent%2Frecords'));
      await expect(
        cardSubtitle(page, SUBTITLE_NEXT),
        'TC-013 ER-2: the card says it will carry on to the page asked for',
      ).toBeVisible();
    });

    await test.step('2. Enter the email address in the "Institutional email or USN" field', async () => {
      await idField(page).fill(STUDENT.email);
    });

    await test.step('3. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(STUDENT.password);
    });

    await test.step('4. Click Sign in', async () => {
      await signInButton(page).click();
      await expect(page, 'TC-013 ER-3: the student lands on the page asked for').toHaveURL(
        exactly('/student/records'),
      );
      await expect(heading(page, 'Records'), 'TC-013 ER-3: headed "Records"').toBeVisible();
    });
  });

  test('A next address that leaves REEP is ignored after sign-in @TC-014', async ({
    page,
    baseURL,
  }) => {
    await test.step('1. Open the sign-in page at /login?next=%2F%2Fexample.com%2Fphish', async () => {
      await page.goto('/login?next=%2F%2Fexample.com%2Fphish');
      await expect(
        cardSubtitle(page, SUBTITLE_PLAIN),
        'TC-014 ER-1: the card reads as for a visit with no page asked for',
      ).toBeVisible();
      await expect(
        googleLink(page),
        'TC-014 ER-1: the Google link does not carry the address on',
      ).toHaveAttribute('href', '/api/auth/sso/google');
    });

    await test.step('2. Enter the email address in the "Institutional email or USN" field', async () => {
      await idField(page).fill(STUDENT.email);
    });

    await test.step('3. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(STUDENT.password);
    });

    await test.step('4. Click Sign in', async () => {
      await signInButton(page).click();
      await expect(page, 'TC-014 ER-2: the student lands on the student home').toHaveURL(
        exactly('/student'),
      );
      await expect(greeting(page), 'TC-014 ER-2: the home greets the student').toBeVisible();
      expect(new URL(page.url()).origin, 'TC-014 ER-2: the browser never left REEP').toBe(
        new URL(baseURL ?? page.url()).origin,
      );
    });
  });

  test('Student signs out from the top bar @TC-015', async ({ page, signIn }) => {
    const bar = page.getByRole('banner');

    await test.step('1. Sign in as the student and open the student home at /student', async () => {
      await signIn('student');
      await page.goto('/student');
      await expect(greeting(page), 'TC-015 ER-1: the home greets the student').toBeVisible();
      await expect(
        bar.getByText(STUDENT.name, { exact: true }),
        'TC-015 ER-1: the top bar shows "Test Student"',
      ).toBeVisible();
      await expect(
        bar.getByText(STUDENT_USN, { exact: true }),
        "TC-015 ER-1: the top bar shows the student's USN",
      ).toBeVisible();
    });

    await test.step('2. Click Sign out at the right of the top bar', async () => {
      await bar.getByRole('button', { name: 'Sign out' }).click();
      await expect(page, 'TC-015 ER-2: the browser goes to the sign-in page').toHaveURL(
        exactly('/login'),
      );
      await expect(
        heading(page, 'Welcome back'),
        'TC-015 ER-2: the "Welcome back" card is shown',
      ).toBeVisible();
    });

    await test.step('3. Open /student directly in the same tab', async () => {
      await page.goto('/student');
      await expect(
        page,
        'TC-015 ER-3: the session has ended, so the app asks for a sign-in',
      ).toHaveURL(exactly('/login?next=%2Fstudent'));
      await expect(signInButton(page), 'TC-015 ER-3: the sign-in form is shown').toBeVisible();
      await expect(
        page.getByText(SIGNED_OUT_ELSEWHERE),
        'TC-015 ER-3: no "signed in on another device" note',
      ).toHaveCount(0);
    });
  });

  test('Faculty member signs out from the account menu @TC-016', async ({ page, signIn }) => {
    const menu = page.getByRole('menu');

    await test.step('1. Sign in as the faculty member and open /mentor/notebook', async () => {
      await signIn('mentor');
      await page.goto('/mentor/notebook');
      await expect(
        heading(page, 'Faculty notebook'),
        'TC-016 (arrange): the notebook is shown',
      ).toBeVisible();
    });

    await test.step('2. Click the account block, "Test Mentor", at the right of the top bar', async () => {
      await accountButton(page, FACULTY.name, FACULTY.roleLabel).click();
      await expect(menu, 'TC-016 ER-1: the menu is headed by the name and role').toContainText(
        new RegExp(`^\\s*${FACULTY.name}\\s*${FACULTY.roleLabel}`),
      );
      await expect(menu.getByRole('menuitem'), 'TC-016 ER-1: the menu has four items').toHaveCount(
        4,
      );
      for (const item of ['My account', 'Password', 'Signature', 'Sign out']) {
        await expect(
          menu.getByRole('menuitem', { name: item, exact: true }),
          `TC-016 ER-1: the menu offers ${item}`,
        ).toBeVisible();
      }
    });

    await test.step('3. Click Sign out in the menu', async () => {
      await menu.getByRole('menuitem', { name: 'Sign out', exact: true }).click();
      await expect(page, 'TC-016 ER-2: the browser goes to the sign-in page').toHaveURL(
        exactly('/login'),
      );
    });

    await test.step('4. Open /mentor/notebook directly in the same tab', async () => {
      await page.goto('/mentor/notebook');
      await expect(
        page,
        'TC-016 ER-3: the session has ended, so the app asks for a sign-in',
      ).toHaveURL(exactly('/login?next=%2Fmentor%2Fnotebook'));
      await expect(signInButton(page), 'TC-016 ER-3: the sign-in form is shown').toBeVisible();
    });
  });

  test('A signed-out visitor is sent to sign in from protected pages @TC-017', async ({ page }) => {
    await test.step('1. Open /mentor/notebook directly', async () => {
      await page.goto('/mentor/notebook');
      await expect(
        page,
        'TC-017 ER-1: the notebook is not shown; the browser is asked to sign in',
      ).toHaveURL(exactly('/login?next=%2Fmentor%2Fnotebook'));
      await expect(heading(page, 'Faculty notebook'), 'TC-017 ER-1: no notebook').toHaveCount(0);
    });

    await test.step('2. Open /admin/students directly', async () => {
      await page.goto('/admin/students');
      await expect(
        page,
        'TC-017 ER-2: the roster is not shown; the browser is asked to sign in',
      ).toHaveURL(exactly('/login?next=%2Fadmin%2Fstudents'));
    });

    await test.step('3. Open /account/password directly', async () => {
      await page.goto('/account/password');
      await expect(
        page,
        'TC-017 ER-3: the change-password screen is not shown; the browser is asked to sign in',
      ).toHaveURL(exactly('/login?next=%2Faccount%2Fpassword'));
      await expect(
        cardSubtitle(page, SUBTITLE_NEXT),
        'TC-017 ER-3: the card says it will carry on to the page asked for',
      ).toBeVisible();
    });
  });

  test('A student cannot open faculty, admin or alumni screens @TC-018', async ({
    page,
    signIn,
  }) => {
    await test.step('1. Sign in as the student', async () => {
      await signIn('student');
    });

    const steps = [
      ['2. Open /admin directly', '/admin', 'ER-1', 'the admin home'],
      ['3. Open /mentor/notebook directly', '/mentor/notebook', 'ER-2', 'the faculty notebook'],
      ['4. Open /admin/governance directly', '/admin/governance', 'ER-3', 'Governance'],
      ['5. Open /alumni directly', '/alumni', 'ER-4', 'the alumni profile'],
    ] as const;
    for (const [title, path, er, screen] of steps) {
      await test.step(title, async () => {
        await page.goto(path);
        await expect(
          page,
          `TC-018 ${er}: ${screen} is not shown; the student is sent home`,
        ).toHaveURL(exactly('/student'));
        await expect(greeting(page), `TC-018 ${er}: the student home is shown`).toBeVisible();
      });
    }
  });

  test('A faculty member cannot open admin or student screens @TC-019', async ({
    page,
    signIn,
  }) => {
    await test.step('1. Sign in as the faculty member', async () => {
      await signIn('mentor');
    });

    const steps = [
      ['2. Open /admin/governance directly', '/admin/governance', 'ER-1', 'Governance'],
      ['3. Open /admin/analytics directly', '/admin/analytics', 'ER-2', 'Analytics'],
      ['4. Open /student/time-log directly', '/student/time-log', 'ER-3', 'the student time sheet'],
    ] as const;
    for (const [title, path, er, screen] of steps) {
      await test.step(title, async () => {
        await page.goto(path);
        await expect(
          page,
          `TC-019 ${er}: ${screen} is not shown; the faculty member is sent to the notebook`,
        ).toHaveURL(exactly('/mentor/notebook'));
        await expect(
          heading(page, 'Faculty notebook'),
          `TC-019 ${er}: the notebook is shown`,
        ).toBeVisible();
      });
    }
  });

  test('The Main Admin and an alumnus are kept to their own screens @TC-020', async ({
    page,
    signIn,
  }) => {
    await test.step('1. Sign in as the Main Admin', async () => {
      await signIn('admin');
    });

    await test.step('2. Open /student/time-log directly', async () => {
      await page.goto('/student/time-log');
      await expect(
        page,
        'TC-020 ER-1: the time sheet is not shown; the admin is sent home',
      ).toHaveURL(exactly('/admin'));
      await expect(
        heading(page, 'What do you want to do?'),
        'TC-020 ER-1: the admin home',
      ).toBeVisible();
    });

    await test.step('3. Open /alumni directly', async () => {
      await page.goto('/alumni');
      await expect(page, 'TC-020 ER-2: the alumni profile is not shown').toHaveURL(
        exactly('/admin'),
      );
      await expect(
        heading(page, 'What do you want to do?'),
        'TC-020 ER-2: the admin home',
      ).toBeVisible();
    });

    await test.step('4. Sign in as the alumnus instead', async () => {
      await signIn('alumni');
    });

    await test.step('5. Open /mentor/notebook directly', async () => {
      await page.goto('/mentor/notebook');
      await expect(
        page,
        'TC-020 ER-3: the notebook is not shown; the alumnus is sent home',
      ).toHaveURL(exactly('/alumni'));
      await expect(heading(page, 'My Profile'), 'TC-020 ER-3: the alumni home').toBeVisible();
    });

    await test.step('6. Open /admin directly', async () => {
      await page.goto('/admin');
      await expect(page, 'TC-020 ER-4: the admin home is not shown').toHaveURL(exactly('/alumni'));
      await expect(heading(page, 'My Profile'), 'TC-020 ER-4: the alumni home').toBeVisible();
    });
  });

  test('Unknown addresses lead to the home page or to sign-in @TC-021', async ({ page }) => {
    await test.step('1. Open /no-such-page directly', async () => {
      await page.goto('/no-such-page');
      await expect(page, 'TC-021 ER-1: a signed-out visitor is asked to sign in').toHaveURL(
        exactly('/login?next=%2F'),
      );
      await expect(
        signInButton(page),
        'TC-021 ER-1: the sign-in form, not an error page',
      ).toBeVisible();
    });

    await test.step("2. Enter the student's email address and password, and click Sign in", async () => {
      await submitSignIn(page, 'student', STUDENT.email, STUDENT.password);
      await expect(page, 'TC-021 ER-2: the student lands on the student home').toHaveURL(
        exactly('/student'),
      );
      await expect(greeting(page), 'TC-021 ER-2: the home greets the student').toBeVisible();
    });

    await test.step('3. Open /no-such-page directly again', async () => {
      await page.goto('/no-such-page');
      await expect(page, 'TC-021 ER-3: a signed-in visitor is sent to their own home').toHaveURL(
        exactly('/student'),
      );
      await expect(greeting(page), 'TC-021 ER-3: the student home is shown').toBeVisible();
    });
  });

  test('Signing in on a second device signs the first one out, with a note @TC-022', async ({
    page,
    signIn,
    browser,
    baseURL,
  }) => {
    const second = await browser.newContext({ baseURL });
    const other = await second.newPage();
    try {
      await test.step('1. In the first browser, sign in as the student and open /student', async () => {
        await signIn('student');
        await page.goto('/student');
        await expect(
          greeting(page),
          'TC-022 ER-1: the first browser shows the student home',
        ).toBeVisible();
      });

      await test.step('2. In the second browser, open /login', async () => {
        await other.goto('/login');
      });

      await test.step('3. In the second browser, sign in as the same student on the sign-in page', async () => {
        await submitSignIn(other, 'student', STUDENT.email, STUDENT.password);
        await expect(other, 'TC-022 ER-2: the second browser lands on /student').toHaveURL(
          exactly('/student'),
        );
        await expect(
          greeting(other),
          'TC-022 ER-2: the second browser greets the student',
        ).toBeVisible();
      });

      await test.step('4. In the first browser, reload the page', async () => {
        await page.reload();
        await expect(page, 'TC-022 ER-3: the first browser is signed out, and told why').toHaveURL(
          exactly('/login?next=%2Fstudent&signedOut=elsewhere'),
        );
        await expect(
          page.getByRole('status').filter({ hasText: 'You were signed out because' }),
          'TC-022 ER-3: the note explains the one-device rule',
        ).toHaveText(SIGNED_OUT_ELSEWHERE);
      });

      await test.step('5. In the first browser, sign in as the student again on the sign-in page', async () => {
        await submitSignIn(page, 'student', STUDENT.email, STUDENT.password);
        await expect(
          page,
          'TC-022 ER-4: the first browser is back on the page it asked for',
        ).toHaveURL(exactly('/student'));
        await expect(greeting(page), 'TC-022 ER-4: the student home is shown').toBeVisible();
      });

      await test.step('6. In the second browser, reload the page', async () => {
        await other.reload();
        await expect(other, 'TC-022 ER-5: now the second browser is signed out').toHaveURL(
          exactly('/login?next=%2Fstudent&signedOut=elsewhere'),
        );
        await expect(
          other.getByRole('status').filter({ hasText: 'You were signed out because' }),
          'TC-022 ER-5: with the same note',
        ).toHaveText(SIGNED_OUT_ELSEWHERE);
      });
    } finally {
      await second.close();
    }
  });

  test("My account shows a faculty member's own details @TC-023", async ({ page, signIn }) => {
    let clockBefore = '';

    await test.step('1. Sign in as the faculty member with the email address and password', async () => {
      clockBefore = await signInClock(page);
      await signIn('mentor');
      await page.goto('/mentor/notebook');
    });

    await test.step('2. Open the account menu (the name in the top right) and click My account', async () => {
      await accountButton(page, FACULTY.name, FACULTY.roleLabel).click();
      await page.getByRole('menuitem', { name: 'My account', exact: true }).click();
      await expect(page, 'TC-023 ER-1: the browser is on /account').toHaveURL(exactly('/account'));
      await expect(heading(page, 'My account'), 'TC-023 ER-1: headed "My account"').toBeVisible();
      await expect(
        page.getByText(`${FACULTY.roleLabel} · ${FACULTY.email} · sign-in, password,`),
        'TC-023 ER-1: the line under the heading names the role, the address and the signature',
      ).toHaveText(
        `${FACULTY.roleLabel} · ${FACULTY.email} · sign-in, password, signature and notifications for your own login.`,
      );
      const security = page.getByRole('region', { name: 'Sign-in & security' });
      await expect(
        security.getByText(`Google · ${FACULTY.email}`),
        'TC-023 ER-2: the Google row names the account address',
      ).toBeVisible();
      await expect(
        security.getByText('Not linked', { exact: true }),
        'TC-023 ER-2: the account is not linked to Google',
      ).toBeVisible();
      await expect(
        security.getByText('Password · 12 characters or more', { exact: true }),
        'TC-023 ER-2: the password row states the 12-character floor',
      ).toBeVisible();
      await expect(
        security.getByRole('button', { name: 'Email me a code' }),
        'TC-023 ER-2: the change-password button is offered',
      ).toBeVisible();
      const sessions = page.getByRole('region', { name: 'Sessions' });
      await expect(
        sessions.getByText(`Signed in as ${FACULTY.name} · ${FACULTY.roleLabel}`, { exact: true }),
        'TC-023 ER-3: Sessions names the person and role',
      ).toBeVisible();
      await expect(
        sessions.getByText('This device', { exact: true }),
        'TC-023 ER-3: the "This device" chip',
      ).toBeVisible();
      await expect(
        sessions.getByRole('button', { name: 'Sign out everywhere' }),
        'TC-023 ER-3: the "Sign out everywhere" button',
      ).toBeVisible();
      await expect(
        page.getByRole('region', { name: 'Signature' }),
        'TC-023 ER-4: faculty get a Signature card',
      ).toBeVisible();
      const clockAfter = await signInClock(page);
      await expect(
        firstSignInRow(page).nth(0),
        'TC-023 ER-5: the newest sign-in is the one from step 1',
      ).toHaveText(new RegExp(`^(${escapeRegExp(clockBefore)}|${escapeRegExp(clockAfter)})$`));
      await expect(
        firstSignInRow(page).nth(1),
        'TC-023 ER-5: through the door "Email and password"',
      ).toHaveText('Email and password');
    });

    await test.step('3. Click Sign out at the top right of the My account page', async () => {
      await page.getByRole('main').getByRole('button', { name: 'Sign out', exact: true }).click();
      await expect(page, 'TC-023 ER-6: the browser goes to the sign-in page').toHaveURL(
        exactly('/login'),
      );
    });

    await test.step('4. Open /account directly', async () => {
      await page.goto('/account');
      await expect(
        page,
        'TC-023 ER-7: the session has ended, so the app asks for a sign-in',
      ).toHaveURL(exactly('/login?next=%2Faccount'));
    });
  });

  test("My account shows a student's own details, without a signature card @TC-024", async ({
    page,
    signIn,
  }) => {
    let clockBefore = '';

    await test.step('1. Sign in as the student with the email address and password', async () => {
      clockBefore = await signInClock(page);
      await signIn('student');
    });

    await test.step('2. Open /account directly. A student has no account menu; the page is reached by its address', async () => {
      await page.goto('/account');
      await expect(heading(page, 'My account'), 'TC-024 ER-1: headed "My account"').toBeVisible();
      await expect(
        page.getByText(`Student · ${STUDENT.email} · sign-in, password,`),
        'TC-024 ER-1: the line under the heading names the role and address, and no signature',
      ).toHaveText(
        `Student · ${STUDENT.email} · sign-in, password, sessions and notifications for your own login.`,
      );
      await expect(
        page
          .getByRole('region', { name: 'Sessions' })
          .getByText(`Signed in as ${STUDENT.name} · Student`, { exact: true }),
        'TC-024 ER-2: Sessions names the student',
      ).toBeVisible();
      // The Sessions card rendering proves the page has drawn its cards, so the
      // absence below is a real absence and not a page still loading.
      await expect(
        page.getByRole('region', { name: 'Signature' }),
        'TC-024 ER-3: a student has no Signature card',
      ).toHaveCount(0);
      const clockAfter = await signInClock(page);
      await expect(
        firstSignInRow(page).nth(0),
        'TC-024 ER-4: the newest sign-in is the one from step 1',
      ).toHaveText(new RegExp(`^(${escapeRegExp(clockBefore)}|${escapeRegExp(clockAfter)})$`));
      await expect(
        firstSignInRow(page).nth(1),
        'TC-024 ER-4: through the door "Email and password"',
      ).toHaveText('Email and password');
    });
  });

  test('My account says so when the session ended on another device @TC-025', async ({
    page,
    signIn,
    browser,
    baseURL,
  }) => {
    const second = await browser.newContext({ baseURL });
    const other = await second.newPage();
    try {
      await test.step("1. In the first browser, sign in as the faculty member and open the account menu's My account", async () => {
        await signIn('mentor');
        await page.goto('/mentor/notebook');
        await accountButton(page, FACULTY.name, FACULTY.roleLabel).click();
        await page.getByRole('menuitem', { name: 'My account', exact: true }).click();
        await expect(
          page
            .getByRole('region', { name: 'Sessions' })
            .getByText(`Signed in as ${FACULTY.name} · Faculty`),
          'TC-025 ER-1: My account is shown for Test Mentor',
        ).toBeVisible();
      });

      await test.step('2. In the second browser, sign in as the same faculty member on the sign-in page', async () => {
        await other.goto('/login');
        await portal(other, 'Faculty').click();
        await submitSignIn(other, 'mentor', FACULTY.email, FACULTY.password);
        await expect(other, 'TC-025 (arrange): the second browser is signed in').toHaveURL(
          exactly('/mentor/notebook'),
        );
      });

      await test.step('3. In the first browser, click Refresh on the "Recent sign-ins" card', async () => {
        await page
          .getByRole('region', { name: 'Recent sign-ins' })
          .getByRole('button', { name: 'Refresh' })
          .click();
        await expect(
          page.getByRole('alert').filter({ hasText: 'This session is no longer live' }),
          'TC-025 ER-2: the page says the session is no longer live, and why',
        ).toContainText(SESSION_NOT_LIVE);
        await expect(
          page.getByRole('button', { name: 'Go to sign in' }),
          'TC-025 ER-2: with a "Go to sign in" button',
        ).toBeVisible();
      });

      await test.step('4. In the first browser, click "Go to sign in"', async () => {
        await page.getByRole('button', { name: 'Go to sign in' }).click();
        await expect(page, 'TC-025 ER-3: the first browser goes to the sign-in page').toHaveURL(
          exactly('/login'),
        );
      });

      await test.step('5. In the second browser, reload the page', async () => {
        await other.reload();
        await expect(other, 'TC-025 ER-4: the second browser is still signed in').toHaveURL(
          exactly('/mentor/notebook'),
        );
        await expect(
          heading(other, 'Faculty notebook'),
          'TC-025 ER-4: on the faculty notebook',
        ).toBeVisible();
      });
    } finally {
      await second.close();
    }
  });

  test('Sign-in alert email switch on My account @TC-026', async ({ page, signIn }) => {
    const box = page.getByRole('checkbox', { name: SIGN_IN_ALERTS_LABEL });
    const pref = page.locator('.account-pref', { hasText: SIGN_IN_ALERTS_LABEL });
    const saved = (state: 'on' | 'off') =>
      page
        .getByRole('status')
        .filter({ hasText: `Saved — ${SIGN_IN_ALERTS_LABEL.toLowerCase()}: ${state}.` });

    await test.step("1. Sign in as the alumnus and open the account menu's My account", async () => {
      await signIn('alumni');
      // Pre-condition 2: the switch starts off. An earlier run that stopped
      // between steps 2 and 4 would have left it on.
      const me = (await (await page.request.get('/api/auth/me')).json()) as {
        notification_prefs?: Record<string, { label: string; enabled: boolean }>;
      };
      const key = Object.entries(me.notification_prefs ?? {}).find(
        ([, p]) => p.label === SIGN_IN_ALERTS_LABEL,
      )?.[0];
      if (!key) throw new Error(`GET /api/auth/me offers no "${SIGN_IN_ALERTS_LABEL}" preference`);
      if (me.notification_prefs?.[key]?.enabled) {
        const reset = await page.request.put('/api/auth/notification-prefs', {
          data: { prefs: { [key]: false } },
        });
        expect(reset.ok(), 'TC-026 (arrange): the switch was put back to off').toBe(true);
      }
      await page.goto('/alumni');
      await accountButton(page, ALUMNUS.name, ALUMNUS.roleLabel).click();
      await page.getByRole('menuitem', { name: 'My account', exact: true }).click();
      await expect(box, 'TC-026 ER-1: the switch is listed, unticked').not.toBeChecked();
      await expect(
        pref.getByText('Off', { exact: true }),
        'TC-026 ER-1: with the chip "Off"',
      ).toBeVisible();
    });

    await test.step('2. Tick "Email me when my account is signed in" on the "Email notifications" card', async () => {
      await box.check();
      await expect(saved('on'), 'TC-026 ER-2: the page confirms the switch is on').toBeVisible();
      await expect(
        pref.getByText('On', { exact: true }),
        'TC-026 ER-2: the chip reads "On"',
      ).toBeVisible();
    });

    await test.step('3. Reload the page', async () => {
      await page.reload();
      await expect(box, 'TC-026 ER-3: still ticked after the reload').toBeChecked();
      await expect(
        pref.getByText('On', { exact: true }),
        'TC-026 ER-3: the chip still reads "On"',
      ).toBeVisible();
    });

    await test.step('4. Untick "Email me when my account is signed in"', async () => {
      await box.uncheck();
      await expect(saved('off'), 'TC-026 ER-4: the page confirms the switch is off').toBeVisible();
      await expect(
        pref.getByText('Off', { exact: true }),
        'TC-026 ER-4: the chip reads "Off"',
      ).toBeVisible();
    });
  });

  test('Sign out everywhere from My account @TC-027', async ({ page, signIn }) => {
    const everywhere = page.getByRole('button', { name: 'Sign out everywhere', exact: true });
    const confirm = page.getByRole('button', { name: 'Yes, sign out everywhere' });
    const warning = page.getByRole('status').filter({ hasText: 'This ends every session' });

    await test.step("1. Sign in as the alumnus and open the account menu's My account", async () => {
      await signIn('alumni');
      await page.goto('/alumni');
      await accountButton(page, ALUMNUS.name, ALUMNUS.roleLabel).click();
      await page.getByRole('menuitem', { name: 'My account', exact: true }).click();
      await expect(
        heading(page, 'My account'),
        'TC-027 (arrange): My account is shown',
      ).toBeVisible();
    });

    await test.step('2. Click "Sign out everywhere"', async () => {
      await everywhere.click();
      await expect(warning, 'TC-027 ER-1: the page warns that this device goes too').toContainText(
        SIGN_OUT_EVERYWHERE_WARNING,
      );
      await expect(confirm, 'TC-027 ER-1: a "Yes, sign out everywhere" button').toBeVisible();
      await expect(
        page.getByRole('region', { name: 'Sessions' }).getByRole('button', { name: 'Cancel' }),
        'TC-027 ER-1: and a Cancel button',
      ).toBeVisible();
    });

    await test.step('3. Click Cancel', async () => {
      await page
        .getByRole('region', { name: 'Sessions' })
        .getByRole('button', { name: 'Cancel' })
        .click();
      await expect(warning, 'TC-027 ER-2: the confirmation closes').toHaveCount(0);
      await expect(
        everywhere,
        'TC-027 ER-2: the "Sign out everywhere" button is back',
      ).toBeVisible();
      await expect(page, 'TC-027 ER-2: still on /account').toHaveURL(exactly('/account'));
    });

    await test.step('4. Click "Sign out everywhere", then "Yes, sign out everywhere"', async () => {
      await everywhere.click();
      await confirm.click();
      await expect(page, 'TC-027 ER-3: the browser goes to the sign-in page').toHaveURL(
        exactly('/login'),
      );
    });

    await test.step('5. Open /account directly', async () => {
      await page.goto('/account');
      await expect(page, "TC-027 ER-4: this browser's session has ended too").toHaveURL(
        exactly('/login?next=%2Faccount'),
      );
    });
  });

  test('Change password refuses a short password, a mismatch and a wrong code @TC-028', async ({
    page,
    signIn,
  }) => {
    const codeField = page.getByLabel('Code from the email', { exact: true });
    const newPassword = page.getByLabel('New password', { exact: true });
    const again = page.getByLabel('Type it again', { exact: true });
    const change = page.getByRole('button', { name: 'Change password' });
    const emailMeACode = page.getByRole('button', { name: 'Email me a code' });
    const valid = 'a-new-password-e2e';

    await test.step('1. Sign in as the faculty member with the email address and password', async () => {
      await signIn('mentor');
      await page.goto('/mentor/notebook');
    });

    await test.step('2. Open the account menu and click Password', async () => {
      await accountButton(page, FACULTY.name, FACULTY.roleLabel).click();
      await page.getByRole('menuitem', { name: 'Password', exact: true }).click();
      await expect(page, 'TC-028 ER-1: the browser is on /account/password').toHaveURL(
        exactly('/account/password'),
      );
      await expect(
        heading(page, 'Change password'),
        'TC-028 ER-1: headed "Change password"',
      ).toBeVisible();
      await expect(emailMeACode, 'TC-028 ER-1: with an "Email me a code" button').toBeVisible();
    });

    await test.step('3. Click "Email me a code"', async () => {
      await emailMeACode.click();
      await expect(
        page.getByText(`We have emailed a code to ${FACULTY.email}. It expires in 10 minutes.`, {
          exact: true,
        }),
        'TC-028 ER-2: the page names the address and the expiry',
      ).toBeVisible();
      await expect(codeField, 'TC-028 ER-2: the "Code from the email" field').toBeVisible();
      await expect(newPassword, 'TC-028 ER-2: the "New password" field').toBeVisible();
      await expect(again, 'TC-028 ER-2: the "Type it again" field').toBeVisible();
    });

    await test.step('4. Enter the bad code, the short new password and the different repeat in "Code from the email", "New password" and "Type it again", then click outside the fields', async () => {
      await codeField.fill('12345');
      await newPassword.fill('short');
      await again.fill('shorter');
      await heading(page, 'Change password').click();
      await expect(
        page.getByText('The code is six digits.'),
        'TC-028 ER-3: the code is refused',
      ).toBeVisible();
      await expect(
        page.getByText(NEEDS_12),
        'TC-028 ER-3: the short password is refused',
      ).toBeVisible();
      await expect(page.getByText(MISMATCH), 'TC-028 ER-3: the mismatch is refused').toBeVisible();
      await expect(change, 'TC-028 ER-3: Change password stays disabled').toBeDisabled();
    });

    await test.step('5. Replace them with the wrong code and the new password in both password fields', async () => {
      await codeField.fill('000000');
      await newPassword.fill(valid);
      await again.fill(valid);
      await expect(
        page.getByText('The code is six digits.'),
        'TC-028 ER-4: the code message clears',
      ).toHaveCount(0);
      await expect(page.getByText(NEEDS_12), 'TC-028 ER-4: the length message clears').toHaveCount(
        0,
      );
      await expect(
        page.getByText(MISMATCH),
        'TC-028 ER-4: the mismatch message clears',
      ).toHaveCount(0);
      await expect(change, 'TC-028 ER-4: Change password becomes enabled').toBeEnabled();
    });

    await test.step('6. Click Change password', async () => {
      await change.click();
      await expect(
        page.getByRole('alert').filter({ hasText: WRONG_PROOF }),
        'TC-028 ER-5: the server refuses the code',
      ).toBeVisible();
      await expect(emailMeACode, 'TC-028 ER-5: "Email me a code" is offered again').toBeVisible();
      await expect(codeField, 'TC-028 ER-5: the form has closed').toHaveCount(0);
    });

    await test.step("7. Sign out from the account menu, then sign in again with the faculty member's email address and the original password", async () => {
      await signOutFromMenu(page, FACULTY.name, FACULTY.roleLabel);
      await expect(page, 'TC-028 (arrange): signed out').toHaveURL(exactly('/login'));
      await portal(page, 'Faculty').click();
      await submitSignIn(page, 'mentor', FACULTY.email, FACULTY.password);
      await expect(page, 'TC-028 ER-6: the original password still signs in').toHaveURL(
        exactly('/mentor/notebook'),
      );
    });
  });

  test('A new faculty member sets a first password from the activation link @TC-030', async ({
    page,
    playwright,
    baseURL,
  }, testInfo) => {
    const name = `E2E Activation ${RUN_ID}`;
    const email = `e2e-activate-${RUN_ID}@bgscet.ac.in`;
    const firstPassword = `first-password-${RUN_ID}`;
    const secondPassword = 'another-password-e2e';

    // Pre-condition 2: a new faculty account and its activation link, made
    // through the API the "Add faculty member" screen calls.
    const admin = await mainAdminApi(playwright, baseURL, testInfo);
    let userId: string | undefined;
    try {
      const departments = (await (await admin.get('/api/admin/departments')).json()) as {
        id: string;
        code: string;
        college_code: string;
      }[];
      const department = departments.find((d) => d.code === 'MGMT' && d.college_code === 'BGSCET');
      if (!department) {
        block(
          testInfo,
          "the dev seed's Department of Management Studies (BGSCET, MGMT) is missing.",
        );
      }
      const created = await admin.post('/api/admin/faculty', {
        data: { name, email, department_id: department.id },
      });
      if (created.status() !== 201) {
        block(
          testInfo,
          `POST /api/admin/faculty answered ${created.status()}: ${await created.text()}`,
        );
      }
      const account = (await created.json()) as { user_id: string; activation_link: string };
      userId = account.user_id;
      const token = new URL(account.activation_link).searchParams.get('token') ?? '';
      const link = `/activate?token=${encodeURIComponent(token)}`;
      const setPassword = page.getByRole('button', { name: 'Set password and sign in' });
      const newPassword = page.getByLabel('New password', { exact: true });
      const again = page.getByLabel('Type it again', { exact: true });

      await test.step('1. Open the activation link', async () => {
        await page.goto(link);
        await expect(
          heading(page, 'Set up your REEP password'),
          'TC-030 ER-1: headed "Set up your REEP password"',
        ).toBeVisible();
        await expect(
          page.getByText(
            'At least 12 characters. A memorable phrase of four or five words clears that easily and is stronger than a short scrambled one.',
          ),
          'TC-030 ER-1: the password rule is stated',
        ).toBeVisible();
        await expect(newPassword, 'TC-030 ER-1: the "New password" field').toBeVisible();
        await expect(again, 'TC-030 ER-1: the "Type it again" field').toBeVisible();
        await expect(setPassword, 'TC-030 ER-1: the button starts disabled').toBeDisabled();
      });

      await test.step('2. Enter the first password in the "New password" field', async () => {
        await newPassword.fill(firstPassword);
      });

      await test.step('3. Enter the same password in the "Type it again" field', async () => {
        await again.fill(firstPassword);
        await expect(
          setPassword,
          'TC-030 ER-2: "Set password and sign in" becomes enabled',
        ).toBeEnabled();
      });

      await test.step('4. Click "Set password and sign in"', async () => {
        await setPassword.click();
        await expect(page, 'TC-030 ER-3: the new faculty member lands on the notebook').toHaveURL(
          exactly('/mentor/notebook'),
        );
        await expect(
          heading(page, 'Faculty notebook'),
          'TC-030 ER-3: "Faculty notebook"',
        ).toBeVisible();
        await expect(
          accountButton(page, name, 'Faculty'),
          'TC-030 ER-3: their name and "Faculty" are in the top bar',
        ).toBeVisible();
      });

      await test.step('5. Open the account menu and click Sign out', async () => {
        await signOutFromMenu(page, name, 'Faculty');
        await expect(page, 'TC-030 (arrange): signed out').toHaveURL(exactly('/login'));
      });

      await test.step('6. Open the activation link again', async () => {
        await page.goto(link);
      });

      await test.step('7. Enter the second password in both fields', async () => {
        await newPassword.fill(secondPassword);
        await again.fill(secondPassword);
      });

      await test.step('8. Click "Set password and sign in"', async () => {
        await setPassword.click();
        await expect(
          page.getByText(LINK_USED),
          'TC-030 ER-4: the used link is refused',
        ).toBeVisible();
        await expect(
          page.getByText(ASK_THE_OFFICE),
          'TC-030 ER-4: and says who can send a new one',
        ).toBeVisible();
        await expect(newPassword, 'TC-030 ER-4: the form is gone').toHaveCount(0);
        await expect(page, 'TC-030 ER-4: the page stays on /activate').toHaveURL(
          /\/activate\?token=/,
        );
      });

      await test.step("9. Sign in on the sign-in page with the new account's email address and the first password", async () => {
        await page.goto('/login');
        await portal(page, 'Faculty').click();
        await submitSignIn(page, 'mentor', email, firstPassword);
        await expect(page, 'TC-030 ER-5: the first password signs the faculty member in').toHaveURL(
          exactly('/mentor/notebook'),
        );
      });
    } finally {
      // Post-condition: the throwaway account leaves every list.
      if (userId) {
        await admin.post(`/api/admin/users/${userId}/remove`, {
          data: { reason: `Removed by the automated run of TC-030 (${RUN_ID}).` },
        });
      }
      await admin.dispose();
    }
  });

  test('Set-password pages refuse a missing or unknown link @TC-031', async ({ page }) => {
    const bogus = 'not-a-real-link-token-0123456789abcdef';
    const valid = 'a-valid-password-e2e';
    const newPassword = page.getByLabel('New password', { exact: true });
    const again = page.getByLabel('Type it again', { exact: true });
    const setNew = page.getByRole('button', { name: 'Set new password' });

    await test.step('1. Open /activate with no link', async () => {
      await page.goto('/activate');
      await expect(
        heading(page, 'Set up your REEP password'),
        'TC-031 ER-1: the activation title',
      ).toBeVisible();
      await expect(
        page.getByText(NEEDS_THE_LINK),
        'TC-031 ER-1: the page asks for the link',
      ).toBeVisible();
      await expect(
        page.getByText(ASK_THE_OFFICE),
        'TC-031 ER-1: and names the placement office',
      ).toBeVisible();
      await expect(newPassword, 'TC-031 ER-1: no password fields').toHaveCount(0);
    });

    await test.step('2. Open /reset with no link', async () => {
      await page.goto('/reset');
      await expect(
        heading(page, 'Choose a new password'),
        'TC-031 ER-2: the reset title',
      ).toBeVisible();
      await expect(
        page.getByText(NEEDS_THE_LINK),
        'TC-031 ER-2: the page asks for the link',
      ).toBeVisible();
      await expect(
        page.getByText('Ask for a new one from', { exact: false }),
        'TC-031 ER-2: and points to "Forgot password?"',
      ).toHaveText(ASK_FOR_A_RESET);
      await expect(newPassword, 'TC-031 ER-2: no password fields').toHaveCount(0);
    });

    await test.step(`3. Open /reset?token=${bogus}`, async () => {
      await page.goto(`/reset?token=${bogus}`);
      await expect(newPassword, 'TC-031 ER-3: the form is shown').toBeVisible();
      await expect(setNew, 'TC-031 ER-3: "Set new password" starts disabled').toBeDisabled();
    });

    await test.step('4. Enter the short password in "New password" and its different repeat in "Type it again", then click outside the fields', async () => {
      await newPassword.fill('short');
      await again.fill('shorter');
      await heading(page, 'Choose a new password').click();
      await expect(page.getByText(NEEDS_12), 'TC-031 ER-4: the length rule').toBeVisible();
      await expect(page.getByText(MISMATCH), 'TC-031 ER-4: the mismatch').toBeVisible();
      await expect(setNew, 'TC-031 ER-4: "Set new password" stays disabled').toBeDisabled();
    });

    await test.step('5. Enter the valid password in both fields', async () => {
      await newPassword.fill(valid);
      await again.fill(valid);
      await expect(setNew, 'TC-031 (arrange): the button is enabled').toBeEnabled();
    });

    await test.step('6. Click "Set new password"', async () => {
      await setNew.click();
      await expect(
        page.getByText(LINK_NOT_VALID),
        'TC-031 ER-5: the link is refused',
      ).toBeVisible();
      await expect(
        page.getByText('Ask for a new one from', { exact: false }),
        'TC-031 ER-5: with the pointer to "Forgot password?"',
      ).toHaveText(ASK_FOR_A_RESET);
      await expect(newPassword, 'TC-031 ER-5: the form is gone').toHaveCount(0);
    });

    await test.step(`7. Open /activate?token=${bogus}, enter the valid password in both fields and click "Set password and sign in"`, async () => {
      await page.goto(`/activate?token=${bogus}`);
      await newPassword.fill(valid);
      await again.fill(valid);
      await page.getByRole('button', { name: 'Set password and sign in' }).click();
      await expect(
        page.getByText(LINK_NOT_VALID),
        'TC-031 ER-6: the activation page refuses it too',
      ).toBeVisible();
      await expect(
        page.getByText(ASK_THE_OFFICE),
        'TC-031 ER-6: and names the placement office',
      ).toBeVisible();
      await expect(page, 'TC-031 ER-6: the page stays on /activate').toHaveURL(
        exactly(`/activate?token=${bogus}`),
      );
      const me = await page.request.get('/api/auth/me');
      expect(me.status(), 'TC-031 ER-6: nobody is signed in').toBe(401);
    });
  });

  test('A shortened reset link is reported as a link problem @TC-032', async ({ page }) => {
    const valid = 'a-valid-password-e2e';

    await test.step('1. Open /reset?token=abc123', async () => {
      await page.goto('/reset?token=abc123');
    });

    await test.step('2. Enter the password in "New password" and again in "Type it again"', async () => {
      await page.getByLabel('New password', { exact: true }).fill(valid);
      await page.getByLabel('Type it again', { exact: true }).fill(valid);
    });

    await test.step('3. Click "Set new password"', async () => {
      await page.getByRole('button', { name: 'Set new password' }).click();
      // Wait for the answer first, whichever it is, so the assertion below
      // judges the page's reply rather than the moment before it.
      await expect(page.locator('.notice'), 'TC-032 (arrange): the page answered').toBeVisible();
      await expect(
        page.getByText(LINK_NOT_VALID),
        'TC-032 ER-1: the link is refused as a link',
      ).toBeVisible();
      await expect(
        page.getByLabel('New password', { exact: true }),
        'TC-032 ER-1: the form is gone',
      ).toHaveCount(0);
    });
  });

  test('Continue with Google hands the browser to Google @TC-034', async ({ page }, testInfo) => {
    await skipUnlessGoogleConfigured(page, testInfo);

    await test.step('1. Open /student/records directly', async () => {
      await page.goto('/student/records');
      await expect(
        googleLink(page),
        'TC-034 ER-1: an active "Continue with Google" button',
      ).toBeVisible();
      await expect(
        googleLine(page, 'Student'),
        'TC-034 ER-1: "Continues to your Student workspace" under it',
      ).toBeVisible();
      await expect(
        googleLink(page),
        'TC-034 ER-1: the button carries the page asked for on to Google',
      ).toHaveAttribute('href', '/api/auth/sso/google?next=%2Fstudent%2Frecords');
    });

    await test.step('2. Click Continue with Google', async () => {
      // The request the browser makes to Google is the evidence. What Google
      // then shows (ER-3) is checked by hand: page.route cannot stand in for
      // a redirect target, and nothing here signs in to Google.
      const toGoogle = page.waitForRequest((request) =>
        request.url().startsWith('https://accounts.google.com/'),
      );
      await googleLink(page).click();
      const request = await toGoogle;
      expect(
        request.redirectedFrom()?.url() ?? '',
        "TC-034 ER-2: REEP's /api/auth/sso/google sent the browser on to Google",
      ).toMatch(/\/api\/auth\/sso\/google\?next=%2Fstudent%2Frecords$/);
      expect(request.isNavigationRequest(), 'TC-034 ER-2: the whole page goes to Google').toBe(
        true,
      );
      const asked = new URL(request.url()).searchParams;
      expect(asked.get('scope'), 'TC-034 ER-2: it asks for openid email profile').toBe(
        'openid email profile',
      );
      expect(asked.get('prompt'), 'TC-034 ER-2: it always offers the account chooser').toBe(
        'select_account',
      );
      expect(
        asked.get('redirect_uri') ?? '',
        "TC-034 ER-2: it names REEP's callback as the address to return to",
      ).toMatch(/\/api\/auth\/sso\/google\/callback$/);
    });
  });

  test('Google refusals are explained on the sign-in page @TC-035', async ({ page }) => {
    await test.step('1. Open the sign-in page at /login?error=sso_not_enrolled', async () => {
      await page.goto('/login?error=sso_not_enrolled');
      await expect(
        page.getByRole('alert'),
        'TC-035 ER-1: the not-on-the-roster message',
      ).toContainText(SSO_NOT_ENROLLED);
    });

    await test.step('2. Open the sign-in page at /login?error=sso_denied', async () => {
      await page.goto('/login?error=sso_denied');
      await expect(
        page.getByRole('alert'),
        'TC-035 ER-2: the stopped-at-Google message',
      ).toContainText(SSO_DENIED);
    });

    await test.step('3. Open the sign-in page at /login?error=made_up_code', async () => {
      await page.goto('/login?error=made_up_code');
      await expect(
        page.getByRole('alert'),
        'TC-035 ER-3: the unknown code is quoted back',
      ).toContainText(SSO_UNKNOWN('made_up_code'));
    });

    await test.step("4. Enter the student's email address and the wrong password, and click Sign in", async () => {
      await submitSignIn(page, 'student', STUDENT.email, WRONG_PASSWORD);
      await expect(page.getByRole('alert'), 'TC-035 ER-4: the password answer alone').toContainText(
        INVALID_CREDENTIALS,
      );
      await expect(
        page.getByRole('alert'),
        'TC-035 ER-4: only one error is on the card',
      ).toHaveCount(1);
    });
  });

  test('Reset requests are limited per address @TC-038', async ({ page }, testInfo) => {
    const email = `tc-038-${RUN_ID}@example.invalid`;
    // Pre-condition 2: three requests for the address in the last hour.
    for (let i = 0; i < 3; i += 1) {
      const response = await page.request.post('/api/auth/forgot', { data: { email } });
      if (response.status() !== 202) {
        block(
          testInfo,
          `POST /api/auth/forgot answered ${response.status()}: the API's 100-an-hour reset limit is spent. Restart the API, or wait an hour.`,
        );
      }
    }
    const emailField = page.getByLabel('Email address', { exact: true });

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Under the card, next to "Need help signing in?", click "Reset your password"', async () => {
      await page.getByRole('button', { name: 'Reset your password' }).click();
      await expect(
        emailField,
        'TC-038 ER-1: the reset form opens with "Email address"',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Send reset link' }),
        'TC-038 ER-1: and a Send reset link button',
      ).toBeVisible();
    });

    await test.step('3. Enter the email address in the "Email address" field', async () => {
      await emailField.fill(email);
    });

    await test.step('4. Click Send reset link', async () => {
      await page.getByRole('button', { name: 'Send reset link' }).click();
      await expect(
        page.getByRole('status').filter({ hasText: TOO_MANY_RESETS }),
        'TC-038 ER-2: the fourth request within the hour is refused',
      ).toHaveText(TOO_MANY_RESETS);
      await expect(emailField, 'TC-038 ER-2: the form is replaced by the answer').toHaveCount(0);
    });
  });

  test('The links around the sign-in card @TC-039', async ({ page }, testInfo) => {
    await skipUnlessGoogleConfigured(page, testInfo);
    const emailField = page.getByLabel('Email address', { exact: true });
    const forgot = page.getByRole('button', { name: 'Forgot password?' });

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Under "Choose your portal", select Faculty', async () => {
      await portal(page, 'Faculty').click();
      await expect(portal(page, 'Faculty'), 'TC-039 (arrange): Faculty is selected').toBeChecked();
    });

    await test.step('3. Click "Already approved? Sign in →"', async () => {
      await page.getByRole('button', { name: 'Already approved? Sign in →' }).click();
      await expect(portal(page, 'Student'), 'TC-039 ER-1: Student is selected again').toBeChecked();
      await expect(
        googleLink(page),
        'TC-039 ER-1: the focus is on "Continue with Google"',
      ).toBeFocused();
    });

    await test.step('4. Click "Forgot password?"', async () => {
      await forgot.click();
      await expect(emailField, 'TC-039 ER-2: the reset form opens').toBeVisible();
    });

    await test.step('5. Click "Forgot password?" again', async () => {
      await forgot.click();
      await expect(emailField, 'TC-039 ER-3: the reset form closes').toHaveCount(0);
    });

    await test.step('6. Click "New student? Register →"', async () => {
      await page.getByRole('link', { name: 'New student? Register →' }).click();
      await expect(page, 'TC-039 ER-4: the public application form').toHaveURL(
        exactly('/register'),
      );
      await expect(
        page.getByRole('heading', { name: 'Student registration' }),
        'TC-039 ER-4: headed "Student registration"',
      ).toBeVisible();
    });
  });

  test('Field messages clear once the fields are filled @TC-040', async ({ page }) => {
    const idMessage = 'Enter your institutional email or usn.';
    const passwordMessage = 'Enter your password.';

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Click Sign in without entering anything', async () => {
      await signInButton(page).click();
      await expect(
        alertSaying(page, idMessage),
        'TC-040 ER-1: the ID message appears',
      ).toBeVisible();
      await expect(
        alertSaying(page, passwordMessage),
        'TC-040 ER-1: the password message appears',
      ).toBeVisible();
    });

    await test.step('3. Enter the email address in the "Institutional email or USN" field', async () => {
      await idField(page).fill(STUDENT.email);
      await expect(alertSaying(page, idMessage), 'TC-040 ER-2: the ID message goes').toHaveCount(0);
      await expect(
        idField(page),
        'TC-040 ER-2: the ID field is no longer marked invalid',
      ).not.toHaveAttribute('aria-invalid', 'true');
    });

    await test.step('4. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(WRONG_PASSWORD);
      await expect(
        alertSaying(page, passwordMessage),
        'TC-040 ER-3: the password message goes',
      ).toHaveCount(0);
    });
  });

  test('Show password from the keyboard @TC-041', async ({ page }) => {
    const typed = 'Typed-but-not-sent-1';
    const show = page.getByRole('button', { name: 'Show password' });
    const hide = page.getByRole('button', { name: 'Hide password' });

    await test.step('1. Open the sign-in page at /login', async () => {
      await page.goto('/login');
    });

    await test.step('2. Enter the password in the "Password" field', async () => {
      await passwordField(page).fill(typed);
    });

    await test.step('3. Press Tab to move to the "Show password" button, then press Enter', async () => {
      await page.keyboard.press('Tab');
      await expect(
        show,
        'TC-041 ER-1: the focus is on the eye button after the field',
      ).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(
        passwordField(page),
        'TC-041 ER-1: the password is readable text',
      ).toHaveAttribute('type', 'text');
      await expect(
        hide,
        'TC-041 ER-1: the button is named "Hide password" and pressed',
      ).toHaveAttribute('aria-pressed', 'true');
    });

    await test.step('4. Press Enter again', async () => {
      await page.keyboard.press('Enter');
      await expect(
        passwordField(page),
        'TC-041 ER-2: the password is masked again',
      ).toHaveAttribute('type', 'password');
      await expect(
        show,
        'TC-041 ER-2: the button is named "Show password" and not pressed',
      ).toHaveAttribute('aria-pressed', 'false');
      await expect(passwordField(page), 'TC-041 ER-2: the typed value is unchanged').toHaveValue(
        typed,
      );
    });
  });
});
