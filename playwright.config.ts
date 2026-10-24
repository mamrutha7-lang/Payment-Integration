/**
 * The Playwright end-to-end suite: `npm run test:e2e` from the repository root.
 *
 * It lives at the ROOT and not in apps/web on purpose. The Angular unit-test
 * builder collects `*.spec.ts` files in its own project, so a Playwright spec
 * under apps/web would be run by vitest. And an end-to-end run needs only a
 * URL: it drives the SPA and the API over HTTP, exactly as a person at a
 * browser does.
 *
 * Every test is the automated twin of a case in test-management/cases/ (the
 * index is test-management/manual-test-cases.md), linked by the `@TC-NNN` tag
 * in its title. The CSV reporter writes a row for every case to
 * manual-test-results.csv and fails the run if the two files have drifted apart.
 */
import { defineConfig, devices } from '@playwright/test';

/** The SPA. Its dev server proxies `/api` to the API on port 3300, so the
 *  session cookie is same-origin, as it is for a person at a browser. */
const baseURL = process.env.REEP_BASE_URL ?? 'http://localhost:4200';

export default defineConfig({
  testDir: './tests',
  // One test at a time, against one database. The cases sign in as the dev
  // seed's shared accounts, and REEP keeps ONE live session per account, so
  // two workers signing in as the same student would sign each other out
  // mid-test. AGENTS.md's "ONE THING AT A TIME TOUCHES ONE DATABASE" applies
  // to this suite as much as to pytest.
  fullyParallel: false,
  workers: 1,
  // No retries: a retry that passes turns a real fault into a "flaky" row.
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: [
    ['list'],
    // `open: 'never'`: after a failing run, the default serves the report and
    // waits, which hangs any run with no one at the keyboard.
    ['html', { open: 'never' }],
    [
      './tests/reporters/manual-csv-reporter.ts',
      {
        outputFile: 'manual-test-results.csv',
        manualCases: 'test-management/cases',
      },
    ],
  ],
  use: {
    baseURL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Starts the SPA when nothing is serving it yet, and reuses one that is.
  // The API is NOT started here, because it needs Postgres, migrations, the seed
  // and a virtualenv. The tests' pre-condition check reports a missing API as
  // Blocked, naming what to start.
  webServer: process.env.REEP_BASE_URL
    ? undefined
    : {
        command: 'npm start',
        cwd: 'apps/web',
        url: baseURL,
        reuseExistingServer: true,
        timeout: 180_000,
      },
});
