/**
 * NFR-PERF  Page performance budgets, measured with W3C Navigation Timing and
 * Largest Contentful Paint in a real Chromium.
 *
 * Budgets are for the DEV server (`ng serve`, unminified, no CDN), so they are
 * deliberately loose; the numbers are what the report records. Against a
 * production build set REEP_BASE_URL and tighten PERF_* env budgets.
 */
import { test, expect } from '@playwright/test';
import { navTiming, signIn } from './support';

const DCL_BUDGET = Number(process.env.PERF_DCL_MS ?? 4000);
const LCP_BUDGET = Number(process.env.PERF_LCP_MS ?? 4000);

const PUBLIC = ['/login', '/register'];
const STUDENT = ['/student', '/student/jobs', '/student/time-log', '/student/leaderboards', '/student/skilling'];

for (const path of PUBLIC) {
  test(`NFR-PERF-01 ${path} loads within budget (cold)`, async ({ page }, info) => {
    await page.goto(path, { waitUntil: 'load' });
    await page.locator('form').first().waitFor();
    const t = await navTiming(page);
    await info.attach('timing', { body: JSON.stringify(t, null, 2), contentType: 'application/json' });
    console.log(`PERF ${path} ${JSON.stringify(t)}`);
    expect(t.dcl, `DOMContentLoaded ${t.dcl} ms`).toBeLessThan(DCL_BUDGET);
    if (t.lcp !== null) expect(t.lcp, `LCP ${t.lcp} ms`).toBeLessThan(LCP_BUDGET);
  });
}

for (const path of STUDENT) {
  test(`NFR-PERF-02 ${path} renders its heading within budget (signed in)`, async ({ page }, info) => {
    await signIn(page);
    const started = Date.now();
    await page.goto(path, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { level: 1 }).first().waitFor();
    const heading = Date.now() - started;
    const t = await navTiming(page);
    await info.attach('timing', { body: JSON.stringify({ ...t, headingVisibleMs: heading }, null, 2), contentType: 'application/json' });
    console.log(`PERF ${path} heading=${heading}ms ${JSON.stringify(t)}`);
    expect(heading, `time to heading ${heading} ms`).toBeLessThan(DCL_BUDGET + 2000);
  });
}

test('NFR-PERF-03 client-side navigation between student screens is fast (SPA route change)', async ({ page }) => {
  await signIn(page);
  await page.goto('/student');
  await page.getByRole('heading', { level: 1 }).first().waitFor();
  const times: number[] = [];
  for (const name of ['Jobs', 'Time Sheet', 'Leaderboards', 'Home']) {
    const link = page.getByRole('navigation').getByRole('link', { name, exact: false }).first();
    if (!(await link.count())) continue;
    const t0 = Date.now();
    await link.click();
    await page.getByRole('heading', { level: 1 }).first().waitFor();
    times.push(Date.now() - t0);
  }
  console.log(`PERF route-changes ms ${JSON.stringify(times)}`);
  expect(times.length).toBeGreaterThan(0);
  for (const ms of times) expect(ms).toBeLessThan(3000);
});
