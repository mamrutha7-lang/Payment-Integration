/**
 * NFR-A11Y  Automated WCAG 2.2 A/AA checks with axe-core.
 *
 * Automated checks find roughly a third to a half of WCAG issues; they are a
 * floor, not an audit. The gate is: no CRITICAL violation. SERIOUS and below
 * are recorded in the report (attached JSON) as findings to triage, except
 * colour contrast, which is gated since DEF-010 was fixed.
 */
import AxeBuilder from '@axe-core/playwright';
import { test, expect } from '@playwright/test';
import { signIn } from './support';

const PAGES: Array<[string, boolean]> = [
  ['/login', false], ['/register', false],
  ['/student', true], ['/student/jobs', true], ['/student/time-log', true],
];

for (const [path, auth] of PAGES) {
  test(`NFR-A11Y-01 ${path} has no critical WCAG 2.2 AA violation`, async ({ page }, info) => {
    if (auth) await signIn(page);
    await page.goto(path);
    await page.getByRole('heading', { level: 1 }).first().waitFor({ timeout: 20_000 }).catch(() => undefined);
    await page.waitForTimeout(500);
    const r = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
      .analyze();
    const summary = r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help }));
    await info.attach('axe-violations', { body: JSON.stringify(summary, null, 2), contentType: 'application/json' });
    console.log(`A11Y ${path} ${JSON.stringify(summary)}`);
    expect(r.violations.filter((v) => v.impact === 'critical'), JSON.stringify(summary)).toEqual([]);
    // DEF-010, fixed 2026-09-30: colour contrast is now held to AA here as well.
    expect(r.violations.filter((v) => v.id === 'color-contrast'), JSON.stringify(summary)).toEqual([]);
  });
}

test('NFR-A11Y-02 the login form is operable by keyboard alone', async ({ page }) => {
  await page.goto('/login');
  const id = page.locator('input[name="id"]');
  await id.waitFor();
  await id.focus();
  await page.keyboard.type('loadtest104@bgscet.ac.in');
  await page.keyboard.press('Tab');
  await page.keyboard.type('LoadTest#2026');
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/student/, { timeout: 20_000 });
});
