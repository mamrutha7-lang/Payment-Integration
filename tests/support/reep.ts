/**
 * What every REEP end-to-end spec shares: the seeded accounts, a `signIn`
 * fixture, and the pre-condition check that runs before every test.
 *
 * Import `test` and `expect` from HERE, never from '@playwright/test'. This
 * `test` carries the auto fixture that checks the environment before each
 * test and marks it Blocked when a pre-condition does not hold. A spec that
 * imports Playwright's own `test` silently loses that, and against a stopped
 * API it produces failures that read like product bugs.
 *
 * WHY A BLOCKED TEST STILL FAILS. A test that cannot run because the
 * environment is wrong has not passed. Playwright has no "blocked" outcome, so
 * the test throws, and the `blocked` annotation is what the CSV reporter
 * (tests/reporters/manual-csv-reporter.ts) reads to write Blocked rather than
 * Failed. A run against a stopped API is red, never a green row of skips.
 */
import { test as base, expect, type APIRequestContext, type TestInfo } from '@playwright/test';

/**
 * The accounts `python -m app.seed` creates. The seed refuses to run on
 * ENV=prod, so these credentials exist only on dev/CI databases. `home` is
 * where each role lands after signing in (`HOME_FOR_ROLE` in
 * apps/web/src/app/core/session.ts, with `/mentor` redirected to its notebook).
 */
export const ACCOUNTS = {
  student: {
    email: 'student@bgscet.ac.in',
    password: 'student123',
    name: 'Test Student',
    role: 'STUDENT',
    home: '/student',
  },
  mentor: {
    email: 'mentor@bgscet.ac.in',
    password: 'mentor123',
    name: 'Test Mentor',
    role: 'MENTOR',
    home: '/mentor/notebook',
  },
  alumni: {
    email: 'alumni@bgscet.ac.in',
    password: 'alumni123',
    name: 'Test Alumnus',
    role: 'ALUMNI',
    home: '/alumni',
  },
  admin: {
    email: 'admin@bgscet.ac.in',
    password: 'admin123',
    name: 'Main Admin (seed)',
    role: 'ADMIN',
    home: '/admin',
  },
} as const;

export type AccountKey = keyof typeof ACCOUNTS;

/** What `POST /api/auth/login` answers with a session (`SessionUser`). */
export interface SessionUser {
  userId: string;
  email: string;
  name: string;
  role: string;
  studentId?: string | null;
  mentorId?: string | null;
  capabilities?: string[];
}

/** Stops the test and records it as Blocked by a pre-condition, not Failed. */
export function block(testInfo: TestInfo, problem: string): never {
  testInfo.annotations.push({ type: 'blocked', description: problem });
  throw new Error(`Blocked by a pre-condition: ${problem}`);
}

/**
 * The pre-conditions every case shares, in the order they fail:
 *   1. the web app answers, and the API answers behind it;
 *   2. the API offers password sign-in. The login screen hides the password
 *      form, and "Forgot password?" with it, unless `GET /api/auth/sso/status`
 *      says the password door is open;
 *   3. the dev seed's student signs in with its seed password. That one request
 *      proves the database is up and the seed was applied. It also proves the
 *      account's failure budget is not spent, and a success returns that
 *      budget, so a test of a wrong password, run over and over, never meets
 *      the limiter.
 * The sign-in uses the `request` fixture, whose cookie jar is not the page's,
 * so every test's page still starts with no session.
 *
 * A pre-condition that goes unchecked turns into a false result: the API
 * refuses an UNKNOWN account with the same 401 sentence as a wrong password
 * (deliberately, see `login` in apps/api-py/app/routers/auth.py), so a
 * wrong-password test would "pass" against a database the seed never reached.
 */
export async function preconditionProblem(request: APIRequestContext): Promise<string | null> {
  let response;
  try {
    response = await request.get('/api/auth/sso/status');
  } catch (error) {
    return `the web app did not answer (${(error as Error).message.split('\n')[0]}). Start it with "npx ng serve" in apps/web.`;
  }
  if (!response.ok()) {
    return (
      `GET /api/auth/sso/status answered ${response.status()}, so the API is not reachable behind the web app. ` +
      'Start it on port 3300 (see "Setup" in test-management/manual-test-cases.md).'
    );
  }
  let status: { password_login_available?: unknown };
  try {
    status = (await response.json()) as typeof status;
  } catch {
    return 'GET /api/auth/sso/status did not answer JSON, so REEP_BASE_URL is not pointing at a REEP web app.';
  }
  if (status.password_login_available !== true) {
    return 'the server does not offer password sign-in. Run the API with ENV=dev, as in "Setup".';
  }
  return signInProblem(
    await request.post('/api/auth/login', {
      data: { email: ACCOUNTS.student.email, password: ACCOUNTS.student.password },
    }),
    ACCOUNTS.student.email,
  );
}

/** Why a seeded account's sign-in did not give a session, or null if it did. */
async function signInProblem(
  response: Awaited<ReturnType<APIRequestContext['post']>>,
  email: string,
): Promise<string | null> {
  if (response.status() === 429) {
    return (
      `the API has paused password sign-in for ${email} after 10 failed attempts. ` +
      'Wait 15 minutes, or restart the API, which clears the count.'
    );
  }
  if (response.status() === 401) {
    return `${email} does not sign in with its seed password. Run "python -m app.seed" in apps/api-py.`;
  }
  if (response.status() === 403) {
    return `${email} is disabled or removed, so it cannot sign in. Restore it in the console, or seed a fresh database.`;
  }
  if (!response.ok()) {
    return (
      `POST /api/auth/login answered ${response.status()}, so the API cannot read its database. ` +
      'Start Postgres with "docker compose up -d", then apply migrations and the dev seed.'
    );
  }
  if ('otp_required' in ((await response.json()) as object)) {
    return 'the server asks for an emailed code after the password (OTP_REQUIRED), which these cases do not cover.';
  }
  return null;
}

export const test = base.extend<{
  /** Runs before every test; see `preconditionProblem`. */
  _preconditions: void;
  /**
   * Signs the page's browser context in as a seeded account, without the
   * login screen: `page.request` shares the context's cookie jar, so the
   * `reep_session` cookie the API sets is the one the page then carries. Use
   * the login screen itself only in the cases that test it.
   *
   * REEP keeps ONE live session per account, so this signs out any other
   * browser holding that account, including a developer's own.
   */
  signIn: (account: AccountKey) => Promise<SessionUser>;
}>({
  _preconditions: [
    async ({ request }, use, testInfo) => {
      const problem = await preconditionProblem(request);
      if (problem) block(testInfo, problem);
      await use();
    },
    { auto: true },
  ],
  signIn: async ({ page }, use, testInfo) => {
    await use(async (account) => {
      const { email, password } = ACCOUNTS[account];
      const response = await page.request.post('/api/auth/login', { data: { email, password } });
      const problem = await signInProblem(response, email);
      if (problem) block(testInfo, problem);
      return (await response.json()) as SessionUser;
    });
  },
});

export { expect };
