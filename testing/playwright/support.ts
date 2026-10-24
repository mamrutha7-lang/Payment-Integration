import { Page, expect } from '@playwright/test';

/** A RESERVED load-test student (never in the JMeter CSV, never the seeded one). */
export const STUDENT = { email: 'loadtest104@bgscet.ac.in', password: 'LoadTest#2026' };

/** Sign in through the API, through the SPA's own /api proxy, so the httpOnly
 *  cookie lands on the page's origin exactly as the login form would put it. */
export async function signIn(page: Page, who = STUDENT): Promise<void> {
  const r = await page.request.post('/api/auth/login', { data: who });
  expect(r.status(), `BLOCKED: sign-in as ${who.email} answered ${r.status()}`).toBe(200);
}

export interface NavTiming { ttfb: number; dcl: number; load: number; lcp: number | null; jsBytes: number; requests: number }

export async function navTiming(page: Page): Promise<NavTiming> {
  return page.evaluate(async () => {
    const n = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
    const lcp = await new Promise<number | null>((resolve) => {
      let last: number | null = null;
      try {
        new PerformanceObserver((list) => {
          for (const e of list.getEntries()) last = e.startTime;
        }).observe({ type: 'largest-contentful-paint', buffered: true });
      } catch { /* not supported */ }
      setTimeout(() => resolve(last), 300);
    });
    const res = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
    const js = res.filter((r) => r.initiatorType === 'script' || r.name.endsWith('.js'));
    return {
      ttfb: Math.round(n.responseStart),
      dcl: Math.round(n.domContentLoadedEventEnd),
      load: Math.round(n.loadEventEnd),
      lcp: lcp === null ? null : Math.round(lcp),
      jsBytes: js.reduce((a, r) => a + (r.transferSize || r.encodedBodySize || 0), 0),
      requests: res.length,
    };
  });
}
