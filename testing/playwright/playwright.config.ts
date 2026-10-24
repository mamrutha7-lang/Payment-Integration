import { defineConfig, devices } from '@playwright/test';
import { globSync } from 'node:fs';

/**
 * Non-functional Playwright suite for REEP (ISO/IEC 25010:2023 characteristics:
 * performance efficiency, interaction capability / accessibility, flexibility /
 * adaptability, reliability / fault tolerance, security).
 *
 * The FUNCTIONAL end-to-end suite is the repository's root `tests/` (one test per
 * manual case, see test-management/). This one lives apart because that suite's
 * reporter refuses any test not tagged with a manual case ID.
 *
 * Runs against the live stack: SPA on 4200 in front of the API on 3300.
 */
const baseURL = process.env.REEP_BASE_URL ?? 'http://localhost:4200';
const chromium = process.env.CHROME_BIN ?? globSync('/opt/pw-browsers/chromium-*/chrome-linux/chrome').sort().pop();

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.nfr\.ts/,
  fullyParallel: false,
  workers: 1, // one session per account: a second worker would sign the first out
  retries: 0,
  timeout: 60_000,
  reporter: [
    ['list'],
    ['html', { outputFolder: '../results/playwright-nfr/html', open: 'never' }],
    ['junit', { outputFile: '../results/playwright-nfr/junit.xml' }],
    ['json', { outputFile: '../results/playwright-nfr/results.json' }],
  ],
  outputDir: '../results/playwright-nfr/artifacts',
  use: {
    baseURL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: chromium ? { executablePath: chromium } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
