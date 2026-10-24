/**
 * Admin: daily operations — the automated twins of the cases in
 * test-management/cases/07-admin-operations.md.
 *
 * THE TAG IS THE LINK. Each test's title ends with the `@TC-NNN` ID of the case
 * it automates, its top-level `test.step()` titles are that case's steps word
 * for word, and every assertion message names the expected result it checks.
 * The CSV reporter fails the run when the two drift apart.
 *
 * Every figure a later test could change (students, pending requests, postings,
 * offers) is read from the API at the time of the test and then looked for on
 * the screen, never written in here. Everything a test creates carries `RUN` in
 * its name and is removed again through the API, except what the product keeps
 * on purpose: a decided leave request, a decided offer, an export receipt and
 * an import run. Each case's post-conditions say so.
 */
import * as fs from 'node:fs';
import type { APIResponse, Locator, Page } from '@playwright/test';
import { ACCOUNTS, block, expect, test, type AccountKey, type SessionUser } from './support/reep';

/** Unique to one run of this file: every name a test creates carries it. */
const RUN = Date.now().toString(36);

type SignIn = (account: AccountKey) => Promise<SessionUser>;

const MENTOR = ACCOUNTS.mentor;
const ADMIN = ACCOUNTS.admin;
const STUDENT = { ...ACCOUNTS.student, usn: '1BG24MBA001' } as const;

/** The seeded batch Test Student sits in (`app/seed.py`). */
const SEEDED_BATCH_NAME = '2024-26 Section B';
const SEEDED_BATCH_LABEL = 'Master of Business Administration - Finance · 2024-26 Section B';

// ------------------------------------------------------------------ helpers --

/** The body of a setup call that must succeed, or an error naming the call. */
async function ok<T>(response: APIResponse, what: string): Promise<T> {
  if (!response.ok()) {
    throw new Error(
      `${what} answered ${response.status()}: ${(await response.text()).slice(0, 300)}`,
    );
  }
  if (response.status() === 204) return undefined as T;
  return (await response.json()) as T;
}

function escapeRegExp(text: string): RegExp {
  return new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
}

/** `plural()` in apps/web/src/app/shared/text/plural.pipe.ts. */
function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString('en')} ${count === 1 ? one : many}`;
}

function csvLines(text: string): string[] {
  return text
    .replace(/^﻿/, '')
    .split(/\r?\n/)
    .filter((line) => line.length > 0);
}

/** Clicks something that downloads, and hands back the saved file. */
async function download(
  page: Page,
  click: () => Promise<void>,
): Promise<{ name: string; bytes: Buffer; text: string }> {
  const [file] = await Promise.all([page.waitForEvent('download'), click()]);
  const bytes = fs.readFileSync(await file.path());
  return { name: file.suggestedFilename(), bytes, text: bytes.toString('utf8') };
}

/** The value shown on a `.select` pill beside its native select. */
function pillValue(select: Locator): Locator {
  return select.locator('xpath=..').locator('.val');
}

/** The first data row of an AG Grid, in the grid's own order. */
function firstGridRow(grid: Locator): Locator {
  return grid.locator('[role="row"][row-index="0"]');
}

/**
 * Two consecutive days far in the future, unique to this second and `slot`,
 * so a request can be found in a queue that keeps every earlier run's rows.
 * Days 1..28 of a month, so the pair never crosses a month.
 */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
interface FarDays {
  from: string;
  to: string;
  /** How the leave queue writes the pair: "2–3 Mar 2104". */
  span: string;
}
function farDays(slot: number): FarDays {
  const index = (Math.floor(Date.now() / 1000) % 300_000) * 8 + slot;
  const day = 1 + (index % 27);
  const month = Math.floor(index / 27) % 12;
  const year = 2100 + Math.floor(index / (27 * 12));
  const iso = (d: number) =>
    `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  return { from: iso(day), to: iso(day + 1), span: `${day}–${day + 1} ${MONTHS[month]} ${year}` };
}

// --- analytics

interface Kpi {
  key: string;
  label: string;
  unit: string;
  value: number | null;
  note: string | null;
}

/** `formatKpiValue` in features/admin/analytics/analytics.component.ts. */
function kpiFigure(kpi: Kpi): string {
  if (kpi.value === null) return '—';
  const one = (value: number) => Math.round(value * 10) / 10;
  if (kpi.unit === 'percent') return `${one(kpi.value)}%`;
  if (kpi.unit === 'inr') {
    if (Math.abs(kpi.value) >= 100000) return `₹${one(kpi.value / 100000).toFixed(1)} L`;
    if (Math.abs(kpi.value) >= 1000) return `₹${Math.round(kpi.value / 1000)}k`;
    return `₹${Math.round(kpi.value)}`;
  }
  return String(Math.round(kpi.value));
}

function kpiTile(page: Page, label: string): Locator {
  return page
    .locator('.kpi-card')
    .filter({ has: page.locator('.eyebrow').getByText(label, { exact: true }) });
}

// --- leave

interface LeaveOut {
  id: string;
  status: string;
}

/** Test Mentor applies for leave through the API, and the page is signed back
 *  in as the Main Admin. */
async function applyAsMentor(
  page: Page,
  signIn: SignIn,
  days: FarDays,
  kind: string,
  reason: string,
): Promise<string> {
  await signIn('mentor');
  const leave = await ok<LeaveOut>(
    await page.request.post('/api/leaves', {
      data: { from_date: days.from, to_date: days.to, reason, leave_kind: kind },
    }),
    'POST /api/leaves as Test Mentor',
  );
  await signIn('admin');
  return leave.id;
}

/** Withdraws a request that is still pending; a decided one answers 409 and
 *  is left as it is, because nothing can take a decision back. */
async function withdrawAsMentor(page: Page, signIn: SignIn, id: string): Promise<void> {
  await signIn('mentor');
  await page.request.post(`/api/leaves/${id}/cancel`);
}

function leaveRow(page: Page, days: FarDays): Locator {
  return page
    .getByRole('row')
    .filter({ hasText: MENTOR.name })
    .filter({ hasText: `${days.span} · 2 days` });
}

function leavePanel(page: Page): Locator {
  return page.getByRole('complementary', { name: 'Leave request' });
}

// --- jobs

interface Job {
  id: string;
  title: string;
  company: string;
  location: string | null;
  status: string;
  closes_on: string | null;
}

async function postJobThroughApi(
  page: Page,
  title: string,
  company: string,
  extra: Record<string, unknown> = {},
): Promise<Job> {
  return ok<Job>(
    await page.request.post('/api/admin/jobs', {
      data: { title, company, degree_level: 'PG', ...extra },
    }),
    'POST /api/admin/jobs',
  );
}

/** Removes every posting with this title. Needs a Main Admin session. */
async function removeJobsTitled(page: Page, title: string): Promise<void> {
  const jobs = await ok<Job[]>(await page.request.get('/api/admin/jobs'), 'GET /api/admin/jobs');
  for (const job of jobs.filter((candidate) => candidate.title === title)) {
    await page.request.delete(`/api/admin/jobs/${job.id}`);
  }
}

async function studentBoardTitles(page: Page): Promise<string[]> {
  const rows = await ok<{ title: string }[]>(
    await page.request.get('/api/student/jobs'),
    'GET /api/student/jobs',
  );
  return rows.map((row) => row.title);
}

function jobSearch(page: Page): Locator {
  return page.getByRole('searchbox', { name: 'Search postings by role, company or location' });
}

function jobRow(page: Page, title: string): Locator {
  return page.getByRole('row', { name: escapeRegExp(title) });
}

// --- placement

interface Placement {
  eligible: number;
  applied: number;
  offered_students: number;
  approved_students: number;
  placement_rate_pct: number | null;
  by_track: { code: string | null; name: string; eligible: number; placed: number }[];
  years: number[];
}

/** Test Student records an offer and submits it for approval; the page is
 *  signed back in as the Main Admin. */
async function submitOfferAsStudent(
  page: Page,
  signIn: SignIn,
  organisation: string,
  jobTitle: string,
): Promise<string> {
  await signIn('student');
  const offer = await ok<{ id: string }>(
    await page.request.post('/api/student/offers', {
      data: { role_type: 'FULL_TIME', job_title: jobTitle, organisation, ctc_inr: 450000 },
    }),
    'POST /api/student/offers',
  );
  await ok(
    await page.request.post(`/api/student/offers/${offer.id}/submit`),
    'POST /api/student/offers/{id}/submit',
  );
  await signIn('admin');
  return offer.id;
}

async function pendingOfferCount(page: Page): Promise<number> {
  const pending = await ok<unknown[]>(
    await page.request.get('/api/mentor/offers/pending'),
    'GET /api/mentor/offers/pending',
  );
  return pending.length;
}

// --- cohorts, SWOC

interface Cohort {
  id: string;
  name: string;
  display_label: string;
}

async function seededCohort(page: Page): Promise<Cohort> {
  const cohorts = await ok<Cohort[]>(
    await page.request.get('/api/admin/cohorts'),
    'GET /api/admin/cohorts',
  );
  const cohort = cohorts.find((candidate) => candidate.name === SEEDED_BATCH_NAME);
  if (!cohort) throw new Error(`the seeded batch "${SEEDED_BATCH_NAME}" is not on this database`);
  return cohort;
}

interface SwocEntry {
  id: string;
  kind: string;
  text: string;
  weight: number;
}
interface SwocStudent {
  student_id: string;
  name: string;
  usn: string | null;
  entries: SwocEntry[];
}

const QUADRANT_OF: Record<string, string> = {
  STRENGTH: 'Strengths',
  WEAKNESS: 'Weaknesses',
  OPPORTUNITY: 'Opportunities',
  CHALLENGE: 'Challenges',
};

async function swocStudent(page: Page): Promise<SwocStudent> {
  const rows = await ok<SwocStudent[]>(
    await page.request.get('/api/admin/swoc'),
    'GET /api/admin/swoc',
  );
  const student = rows.find((row) => row.usn === STUDENT.usn);
  if (!student) throw new Error(`${STUDENT.name} is not on the SWOC board`);
  return student;
}

async function addSwocThroughApi(page: Page, kind: string, text: string): Promise<SwocEntry> {
  const student = await swocStudent(page);
  return ok<SwocEntry>(
    await page.request.post(`/api/admin/swoc/${student.student_id}`, {
      data: { kind, text, weight: 3 },
    }),
    'POST /api/admin/swoc/{student}',
  );
}

async function removeSwocThroughApi(page: Page, id: string): Promise<void> {
  await page.request.delete(`/api/admin/swoc/entries/${id}`);
}

/** The index of the line holding `text` among a quadrant's boxes, or -1. The
 *  text is a textarea's VALUE, which no text locator reads. */
async function swocLineIndex(page: Page, quadrant: string, text: string): Promise<number> {
  return page
    .getByRole('textbox', { name: `${quadrant} entry` })
    .evaluateAll(
      (boxes, wanted) => boxes.findIndex((box) => (box as HTMLTextAreaElement).value === wanted),
      text,
    );
}

/** The `<article>` of the line holding `text`, once it is on screen. Found
 *  afresh each time, because a weight change re-sorts the quadrant. */
async function swocLine(page: Page, quadrant: string, text: string): Promise<Locator> {
  await expect
    .poll(() => swocLineIndex(page, quadrant, text), {
      message: `the ${quadrant} line "${text}" is shown`,
    })
    .toBeGreaterThanOrEqual(0);
  const index = await swocLineIndex(page, quadrant, text);
  return page
    .getByRole('textbox', { name: `${quadrant} entry` })
    .nth(index)
    .locator('xpath=ancestor::article[1]');
}

function swocTile(page: Page, label: string): Locator {
  return page
    .locator('.swoc-tile')
    .filter({ has: page.locator('b', { hasText: new RegExp(`^${label}$`) }) });
}

async function studentSwocTexts(page: Page, quadrant: string): Promise<string[]> {
  const overview = await ok<{ swoc: Record<string, { text: string }[]> | null }>(
    await page.request.get('/api/student/overview'),
    'GET /api/student/overview',
  );
  return (overview.swoc?.[quadrant] ?? []).map((item) => item.text);
}

// ==================================================================== tests ==

test.describe('Admin: daily operations', () => {
  // ------------------------------------------------------------- analytics --

  test("Analytics shows the programme's figures from the database @TC-600", async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const summary = await ok<{ students_total: number; mentors_total: number }>(
      await page.request.get('/api/admin/analytics-summary'),
      'GET /api/admin/analytics-summary',
    );
    const sheet = await ok<{ kpis: Kpi[] }>(
      await page.request.get('/api/admin/analytics/kpis?weeks=6'),
      'GET /api/admin/analytics/kpis',
    );
    const load = await ok<{ name: string; mentee_count: number; capacity: number }[]>(
      await page.request.get('/api/admin/mentor-load'),
      'GET /api/admin/mentor-load',
    );
    const alerts = await ok<{ message: string; student_name: string }[]>(
      await page.request.get('/api/mentor/alerts?open_only=true'),
      'GET /api/mentor/alerts',
    );

    await test.step('1. Open /admin/analytics', async () => {
      await page.goto('/admin/analytics');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Charts & numbers' }),
        'TC-600 ER-1: the page heading is "Charts & numbers"',
      ).toBeVisible();
      await expect(
        page.getByText(/Programme-wide ·/),
        'TC-600 ER-1: the header counts the students and the mentors with a group, as the API does',
      ).toContainText(
        `Programme-wide · ${plural(summary.students_total, 'student')} · ${plural(summary.mentors_total, 'mentor')} with a group`,
      );

      await expect(
        page.locator('.kpi-card .eyebrow'),
        'TC-600 ER-2: the seven tiles, in the documented order',
      ).toHaveText([
        'Placement rate',
        'Median CTC',
        'Highest CTC',
        'Placement ready %',
        'Attendance average',
        'Mock interviews',
        'Pending approvals',
      ]);
      for (const kpi of sheet.kpis) {
        await expect(
          kpiTile(page, kpi.label).locator('.num'),
          `TC-600 ER-2: the ${kpi.label} tile shows the API's value`,
        ).toHaveText(kpiFigure(kpi));
        if (kpi.value === null && kpi.note) {
          await expect(
            kpiTile(page, kpi.label).locator('.sub'),
            `TC-600 ER-2: the unmeasured ${kpi.label} tile gives the API's reason`,
          ).toContainText(kpi.note);
        }
      }

      await expect(
        page.getByRole('heading', { level: 2, name: /^Mentor load/ }),
        'TC-600 ER-3: the Mentor load card counts every faculty account the API lists',
      ).toHaveText(`Mentor load · ${plural(load.length, 'faculty account')}`);
      for (const mentor of load.slice(0, 8)) {
        await expect(
          page
            .locator('.mentor-grid')
            .getByRole('row')
            .filter({ hasText: mentor.name })
            .filter({ hasText: `${mentor.mentee_count} / ${mentor.capacity}` })
            .first(),
          `TC-600 ER-3: ${mentor.name} is listed with their mentees as count / capacity`,
        ).toBeVisible();
      }

      const alertCard = page.locator('.alerts-card');
      if (alerts.length === 0) {
        await expect(alertCard, 'TC-600 ER-4: no open alert, so "Nothing open."').toContainText(
          'Nothing open.',
        );
      }
      for (const alert of alerts) {
        await expect(
          alertCard
            .getByRole('listitem')
            .filter({ hasText: alert.message })
            .filter({ hasText: alert.student_name })
            .first(),
          `TC-600 ER-4: the open alert "${alert.message}" is listed with ${alert.student_name}'s name`,
        ).toBeVisible();
      }
    });
  });

  test('The analytics period filter re-counts the figures @TC-601', async ({ page, signIn }) => {
    await signIn('admin');
    const period = page.getByRole('combobox', { name: /^Period/ });

    await test.step('1. Open /admin/analytics', async () => {
      await page.goto('/admin/analytics');
      await expect(pillValue(period), 'TC-601 ER-1: Period reads "Last 6 weeks"').toHaveText(
        'Last 6 weeks',
      );
      await expect(
        page.getByText('Every comparison below is against the 6 weeks before this one.'),
        'TC-601 ER-1: the note names the 6-week window',
      ).toBeVisible();
    });

    await test.step('2. In Period, choose "Last 12 weeks"', async () => {
      const answered = page.waitForResponse((response) =>
        response.url().includes('/api/admin/analytics/kpis?weeks=12'),
      );
      await period.selectOption({ label: 'Last 12 weeks' });
      const response = await answered;
      expect(response.ok(), 'TC-601 ER-3: the screen asked the API for the 12-week figures').toBe(
        true,
      );
      const sheet = (await response.json()) as { kpis: Kpi[] };

      await expect(pillValue(period), 'TC-601 ER-2: Period reads "Last 12 weeks"').toHaveText(
        'Last 12 weeks',
      );
      await expect(
        page.getByText('Every comparison below is against the 12 weeks before this one.'),
        'TC-601 ER-2: the note names the 12-week window',
      ).toBeVisible();
      for (const label of ['Pending approvals', 'Placement rate']) {
        const kpi = sheet.kpis.find((candidate) => candidate.label === label);
        if (!kpi) throw new Error(`the 12-week sheet has no "${label}" tile`);
        await expect(
          kpiTile(page, label).locator('.num'),
          `TC-601 ER-3: the ${label} tile shows the 12-week value`,
        ).toHaveText(kpiFigure(kpi));
      }
    });
  });

  test('The weekly chart can be drawn for one student, and the mentor load can be searched @TC-602', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const series = await ok<{ students_in_reach: number }>(
      await page.request.get('/api/admin/analytics/series?weeks=6'),
      'GET /api/admin/analytics/series',
    );
    const chart = page.getByRole('img', { name: /^Weekly placement health · / });
    const subject = page.getByRole('combobox', { name: 'Who the weekly chart is drawn for' });
    const search = page.getByRole('searchbox', { name: 'Search the mentor load table' });
    const mentorGrid = page.locator('.mentor-grid');

    await test.step('1. Open /admin/analytics', async () => {
      await page.goto('/admin/analytics');
      await expect(
        chart,
        'TC-602 ER-1: the chart is drawn for every student in reach',
      ).toHaveAttribute(
        'aria-label',
        `Weekly placement health · Every student in reach · ${series.students_in_reach} counted`,
      );
    });

    await test.step('2. In "Drawn for", choose "Test Student"', async () => {
      await subject.selectOption({ label: STUDENT.name });
      await expect(
        page.getByText(/Test Student · the last six weeks on record/),
        'TC-602 ER-2: the line under the heading names the student',
      ).toBeVisible();
      await expect(chart, 'TC-602 ER-2: the chart is labelled for the student').toHaveAttribute(
        'aria-label',
        'Weekly placement health · Test Student · the last six weeks on record',
      );
    });

    await test.step('3. In "Drawn for", choose "Every student in reach"', async () => {
      await subject.selectOption({ label: 'Every student in reach' });
      await expect(
        chart,
        'TC-602 ER-3: the chart is labelled for every student again',
      ).toHaveAttribute(
        'aria-label',
        `Weekly placement health · Every student in reach · ${series.students_in_reach} counted`,
      );
    });

    await test.step('4. Type "no such mentor" in the search box of the "Mentor load" card', async () => {
      await search.fill('no such mentor');
      await expect(
        mentorGrid.getByRole('row', { name: /Test Mentor/ }),
        'TC-602 ER-4: the Test Mentor row is gone',
      ).toHaveCount(0);
    });

    await test.step('5. Replace the search with "Test Mentor"', async () => {
      await search.fill('Test Mentor');
      await expect(
        mentorGrid.getByRole('row', { name: /Test Mentor/ }),
        'TC-602 ER-5: the Test Mentor row is back',
      ).toBeVisible();
      await expect(
        mentorGrid
          .getByRole('row')
          .filter({ has: page.getByRole('gridcell') })
          .filter({ hasNotText: 'Test Mentor' }),
        'TC-602 ER-5: every row shown matches the search',
      ).toHaveCount(0);
    });
  });

  test('Changing and restoring an alert rule for a batch @TC-603', async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('admin');
    const cohort = await seededCohort(page);
    type Rule = {
      rule_key: string;
      enabled: boolean;
      params: Record<string, number>;
      severity: string;
    };
    const rules = await ok<Rule[]>(
      await page.request.get(`/api/admin/alert-rules?cohort_id=${cohort.id}`),
      'GET /api/admin/alert-rules',
    );
    const original = rules.find((rule) => rule.rule_key === 'NO_CHECKIN_N_DAYS');
    if (!original || !original.enabled || typeof original.params['days'] !== 'number') {
      block(
        testInfo,
        'the seeded batch has no enabled "No sign-in for N days" rule with a day count. Seed a fresh database.',
      );
    }
    const originalDays = original.params['days'];
    const newDays = originalDays + 1;
    const liveRules = rules.filter((rule) => rule.enabled).length;

    const dialog = page.getByRole('dialog', { name: 'Alert rules' });
    const batch = dialog.getByRole('combobox', { name: 'Batch these rules apply to' });
    const rule = dialog
      .locator('section.rule')
      .filter({ has: page.getByRole('heading', { name: 'No sign-in for N days', exact: true }) });
    const days = rule.getByLabel('Days of silence');
    const savedNote = 'Saved. The next sweep evaluates this rule for this batch.';

    try {
      await test.step('1. Open /admin/analytics', async () => {
        await page.goto('/admin/analytics');
      });

      await test.step('2. In the "Alerts · open" card, click Rules', async () => {
        await page.getByRole('button', { name: 'Rules' }).click();
        await expect(dialog, 'TC-603 ER-1: the "Alert rules" dialog opens').toBeVisible();
        await expect(
          batch.locator('option', { hasText: cohort.display_label }),
          'TC-603 ER-1: the Batch list offers the seeded batch',
        ).toHaveCount(1);
      });

      await test.step('3. In Batch, choose the seeded batch', async () => {
        await batch.selectOption(cohort.id);
        await expect(
          dialog.getByText(/rules will be evaluated for this batch\./),
          'TC-603 ER-2: the dialog counts the enabled rules',
        ).toHaveText(`${liveRules} of 5 rules will be evaluated for this batch.`);
        await expect(
          rule.getByText('Evaluated', { exact: true }),
          'TC-603 ER-2: the rule is evaluated',
        ).toBeVisible();
        await expect(days, 'TC-603 ER-2: Days of silence shows the stored value').toHaveValue(
          String(originalDays),
        );
      });

      await test.step('4. In the "No sign-in for N days" rule, change "Days of silence" to the new value, then click Save in that rule', async () => {
        await days.fill(String(newDays));
        await rule.getByRole('button', { name: 'Save' }).click();
        await expect(
          rule.getByRole('status'),
          'TC-603 ER-3: the rule says it was saved',
        ).toContainText(savedNote);
      });

      await test.step('5. Click Close, click Rules again and choose the seeded batch in Batch', async () => {
        await dialog.getByRole('button', { name: 'Close', exact: true }).click();
        await expect(dialog, 'TC-603 ER-4 (arrange): the dialog closed').toBeHidden();
        await page.getByRole('button', { name: 'Rules' }).click();
        await batch.selectOption(cohort.id);
        await expect(days, 'TC-603 ER-4: the reopened dialog shows the new value').toHaveValue(
          String(newDays),
        );
      });

      await test.step('6. Put "Days of silence" back to its original value, then click Save in that rule', async () => {
        await days.fill(String(originalDays));
        await rule.getByRole('button', { name: 'Save' }).click();
        await expect(
          rule.getByRole('status'),
          'TC-603 ER-5: the rule says it was saved',
        ).toContainText(savedNote);
        const after = await ok<Rule[]>(
          await page.request.get(`/api/admin/alert-rules?cohort_id=${cohort.id}`),
          'GET /api/admin/alert-rules',
        );
        expect(
          after.find((candidate) => candidate.rule_key === 'NO_CHECKIN_N_DAYS')?.params['days'],
          'TC-603 ER-5: the API reports the original value again',
        ).toBe(originalDays);
      });
    } finally {
      await signIn('admin');
      await page.request.put('/api/admin/alert-rules', {
        data: {
          cohort_id: cohort.id,
          rule_key: original.rule_key,
          params: original.params,
          enabled: original.enabled,
          severity: original.severity,
        },
      });
    }
  });

  // ------------------------------------------------------ leave approvals --

  test("The leave queue lists a faculty member's pending request @TC-610", async ({
    page,
    signIn,
  }) => {
    const days = farDays(0);
    const reason = `E2E leave ${RUN}`;
    const id = await applyAsMentor(page, signIn, days, 'CASUAL', reason);
    const kind = page.getByRole('combobox', { name: 'Leave type' });

    try {
      await test.step('1. Open /admin/leave-approvals', async () => {
        const pending = await ok<unknown[]>(
          await page.request.get('/api/leaves/pending'),
          'GET /api/leaves/pending',
        );
        await page.goto('/admin/leave-approvals');
        const tab = page.getByRole('button', { name: /^Pending · \d+$/ });
        await expect(tab, 'TC-610 ER-1: the Pending tab counts the pending requests').toHaveText(
          `Pending · ${pending.length}`,
        );
        await expect(tab, 'TC-610 ER-1: the Pending tab is selected').toHaveAttribute(
          'aria-current',
          'true',
        );
        const row = leaveRow(page, days);
        await expect(row, 'TC-610 ER-1: the request is listed with type Casual').toContainText(
          'Casual',
        );
        await expect(row, 'TC-610 ER-1: the status reads "Awaiting your decision"').toContainText(
          'Awaiting your decision',
        );
        await expect(
          row,
          'TC-610 ER-1: the chain reads "Your one signature sanctions or refuses it"',
        ).toContainText('Your one signature sanctions or refuses it');
      });

      await test.step("2. On the Test Mentor row with the test dates, click the requester's name", async () => {
        await leaveRow(page, days)
          .getByRole('button', { name: /Test Mentor/ })
          .click();
        const panel = leavePanel(page);
        await expect(
          panel.getByText('Test Mentor · Casual leave'),
          'TC-610 ER-2: the panel names the request',
        ).toBeVisible();
        await expect(
          panel.getByText('Reason · visible to approvers only'),
          'TC-610 ER-2: the panel has a Reason section',
        ).toBeVisible();
        await expect(
          panel.getByText(reason, { exact: true }),
          'TC-610 ER-2: the panel shows the purpose',
        ).toBeVisible();
        await expect(
          panel.getByText('Applied and signed'),
          'TC-610 ER-2: the first step is "Applied and signed"',
        ).toBeVisible();
        await expect(
          panel.getByText('Your one signature decides it.'),
          'TC-610 ER-2: the second step is the one signature',
        ).toBeVisible();
        await expect(
          panel.getByLabel('Remarks'),
          'TC-610 ER-2: a Remarks box is shown',
        ).toBeVisible();
        await expect(
          panel.getByRole('button', { name: 'Reject', exact: true }),
          'TC-610 ER-2: Reject is offered',
        ).toBeVisible();
        await expect(
          panel.getByRole('button', { name: 'Sanction', exact: true }),
          'TC-610 ER-2: Sanction is offered',
        ).toBeVisible();
        await expect(
          panel.getByRole('link', { name: 'PDF', exact: true }),
          'TC-610 ER-2: PDF is offered',
        ).toBeVisible();
      });

      await test.step('3. In Type, choose "Permission"', async () => {
        await kind.selectOption({ label: 'Permission' });
        await expect(
          leaveRow(page, days),
          'TC-610 ER-3: a Casual request is not listed under Permission',
        ).toHaveCount(0);
      });

      await test.step('4. In Type, choose "Casual"', async () => {
        await kind.selectOption({ label: 'Casual' });
        await expect(
          leaveRow(page, days),
          'TC-610 ER-4: the request is listed again',
        ).toBeVisible();
      });
    } finally {
      await withdrawAsMentor(page, signIn, id);
    }
  });

  test('Sanctioning a leave request @TC-611', async ({ page, signIn }) => {
    const days = farDays(1);
    const remarks = `Sanctioned in E2E ${RUN}`;
    const id = await applyAsMentor(page, signIn, days, 'CASUAL', `E2E sanction ${RUN}`);
    const panel = leavePanel(page);

    try {
      await test.step('1. Open /admin/leave-approvals', async () => {
        await page.goto('/admin/leave-approvals');
      });

      await test.step("2. On the Test Mentor row with the test dates, click the requester's name", async () => {
        await leaveRow(page, days)
          .getByRole('button', { name: /Test Mentor/ })
          .click();
      });

      await test.step('3. Type the remarks in the Remarks box', async () => {
        await panel.getByLabel('Remarks').fill(remarks);
      });

      await test.step('4. Click Sanction', async () => {
        await panel.getByRole('button', { name: 'Sanction', exact: true }).click();
        await expect(
          panel.getByText(
            'Sanctions the leave. Your name, the time and your uploaded signature image print in the PROGRAM DIRECTOR block of the paper (add the image under Signature in the account menu).',
          ),
          'TC-611 ER-1: the panel asks for confirmation',
        ).toBeVisible();
        await expect(
          panel.getByRole('button', { name: 'Cancel' }),
          'TC-611 ER-1: Cancel is offered',
        ).toBeVisible();
      });

      await test.step('5. Click Confirm sanction', async () => {
        await panel.getByRole('button', { name: 'Confirm sanction' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: 'Sanctioned and signed for Test Mentor.' }),
          'TC-611 ER-2: the office is told it is sanctioned',
        ).toBeVisible();
        await expect(
          leaveRow(page, days),
          'TC-611 ER-2: the request left the Pending queue',
        ).toHaveCount(0);
        await expect(
          panel.getByText('Pick a request'),
          'TC-611 ER-2: the panel is back to "Pick a request"',
        ).toBeVisible();
      });

      await test.step('6. Click the Approved tab', async () => {
        await page.getByRole('button', { name: /^Approved · \d+$/ }).click();
        const row = leaveRow(page, days);
        await expect(row, 'TC-611 ER-3: the request is listed as Sanctioned').toContainText(
          'Sanctioned',
        );
        await expect(row, 'TC-611 ER-3: the chain names the Main Admin').toContainText(
          `Sanctioned by ${ADMIN.name}`,
        );
      });

      await test.step("7. On the Test Mentor row with the test dates, click the requester's name", async () => {
        await leaveRow(page, days)
          .getByRole('button', { name: /Test Mentor/ })
          .click();
        await expect(
          panel.getByText('Sanctioned — remarks'),
          'TC-611 ER-4: the remarks are headed',
        ).toBeVisible();
        await expect(
          panel.getByText(remarks, { exact: true }),
          'TC-611 ER-4: the remarks are shown',
        ).toBeVisible();
        await expect(
          panel,
          'TC-611 ER-4: the Sanction step names who signed and as what',
        ).toContainText(`${ADMIN.name} · as Main Admin · `);
        await expect(
          panel.getByRole('button', { name: 'Sanction', exact: true }),
          'TC-611 ER-4: nothing is left to decide',
        ).toHaveCount(0);
      });

      await test.step('8. Click "Open the signed form (PDF)"', async () => {
        const file = await download(page, () =>
          panel.getByRole('link', { name: 'Open the signed form (PDF)' }).click(),
        );
        expect(
          file.name,
          'TC-611 ER-5: the file is named after the requester and the first day',
        ).toBe(`leave-test-mentor-${days.from}.pdf`);
        expect(file.bytes.subarray(0, 5).toString('latin1'), 'TC-611 ER-5: the file is a PDF').toBe(
          '%PDF-',
        );
      });
    } finally {
      await withdrawAsMentor(page, signIn, id);
    }
  });

  test('Rejecting a leave request needs remarks @TC-612', async ({ page, signIn }) => {
    const days = farDays(2);
    const remarks = `Rejected in E2E ${RUN}`;
    const id = await applyAsMentor(page, signIn, days, 'PERMISSION', `E2E reject ${RUN}`);
    const panel = leavePanel(page);

    try {
      await test.step('1. Open /admin/leave-approvals', async () => {
        await page.goto('/admin/leave-approvals');
      });

      await test.step("2. On the Test Mentor row with the test dates, click the requester's name", async () => {
        await leaveRow(page, days)
          .getByRole('button', { name: /Test Mentor/ })
          .click();
      });

      await test.step('3. Click Reject', async () => {
        await panel.getByRole('button', { name: 'Reject', exact: true }).click();
        await expect(
          panel.getByText(
            'The applicant is told the form was not sanctioned, and reads your remarks. Nothing else reaches them.',
          ),
          'TC-612 ER-1: the panel says what the applicant is told',
        ).toBeVisible();
        await expect(
          panel.getByRole('button', { name: 'Confirm reject' }),
          'TC-612 ER-1: Confirm reject is offered',
        ).toBeVisible();
      });

      await test.step('4. Click Confirm reject without typing any remarks', async () => {
        await panel.getByRole('button', { name: 'Confirm reject' }).click();
        await expect(
          panel.getByRole('alert').filter({ hasText: 'Remarks are required.' }),
          'TC-612 ER-2: a refusal without remarks is stopped',
        ).toBeVisible();
        await expect(
          leaveRow(page, days),
          'TC-612 ER-2: the request is still pending',
        ).toContainText('Awaiting your decision');
      });

      await test.step('5. Type the remarks in the Remarks box', async () => {
        await panel.getByLabel('Remarks').fill(remarks);
      });

      await test.step('6. Click Confirm reject', async () => {
        await panel.getByRole('button', { name: 'Confirm reject' }).click();
        await expect(
          page
            .getByRole('status')
            .filter({ hasText: 'Not sanctioned — Test Mentor has your remarks.' }),
          'TC-612 ER-3: the office is told the request was not sanctioned',
        ).toBeVisible();
        await expect(
          leaveRow(page, days),
          'TC-612 ER-3: the request left the Pending queue',
        ).toHaveCount(0);
      });

      await test.step('7. Click the Rejected tab', async () => {
        await page.getByRole('button', { name: /^Rejected · \d+$/ }).click();
        const row = leaveRow(page, days);
        await expect(row, 'TC-612 ER-4: the request is listed as Not sanctioned').toContainText(
          'Not sanctioned',
        );
        await expect(row, 'TC-612 ER-4: the chain names the Main Admin').toContainText(
          `Refused by ${ADMIN.name}`,
        );
      });

      await test.step("8. On the Test Mentor row with the test dates, click the requester's name", async () => {
        await leaveRow(page, days)
          .getByRole('button', { name: /Test Mentor/ })
          .click();
        await expect(
          panel.getByText('Rejected — remarks'),
          'TC-612 ER-5: the remarks are headed',
        ).toBeVisible();
        await expect(
          panel.getByText(remarks, { exact: true }),
          'TC-612 ER-5: the remarks are shown',
        ).toBeVisible();
      });
    } finally {
      await withdrawAsMentor(page, signIn, id);
    }
  });

  test("Downloading a pending request's leave paper @TC-613", async ({ page, signIn }) => {
    const days = farDays(3);
    const id = await applyAsMentor(page, signIn, days, 'OOD', `E2E paper ${RUN}`);

    try {
      await test.step('1. Open /admin/leave-approvals', async () => {
        await page.goto('/admin/leave-approvals');
      });

      await test.step("2. On the Test Mentor row with the test dates, click the requester's name", async () => {
        await leaveRow(page, days)
          .getByRole('button', { name: /Test Mentor/ })
          .click();
        await expect(
          leavePanel(page).getByText('Test Mentor · OOD leave'),
          'TC-613 ER-1 (arrange): the request is open',
        ).toBeVisible();
      });

      await test.step('3. Click PDF', async () => {
        const file = await download(page, () =>
          leavePanel(page).getByRole('link', { name: 'PDF', exact: true }).click(),
        );
        expect(
          file.name,
          'TC-613 ER-1: the file is named after the requester and the first day',
        ).toBe(`leave-test-mentor-${days.from}.pdf`);
        expect(file.bytes.subarray(0, 5).toString('latin1'), 'TC-613 ER-1: the file is a PDF').toBe(
          '%PDF-',
        );
        await expect(leaveRow(page, days), 'TC-613 ER-1: the request stays pending').toContainText(
          'Awaiting your decision',
        );
      });
    } finally {
      await withdrawAsMentor(page, signIn, id);
    }
  });

  test('Recording, correcting and removing a leave allowance @TC-614', async ({ page, signIn }) => {
    await signIn('admin');
    const policy = await ok<{ academic_year: string }>(
      await page.request.get('/api/admin/leave-policy'),
      'GET /api/admin/leave-policy',
    );
    const year = policy.academic_year;
    type Balance = { id: string; user_email: string | null };
    const balancesPath = `/api/admin/leave-balances?academic_year=${encodeURIComponent(year)}&kind=RH`;
    // Pre-condition 2: a Test Student RH row left by an earlier failed run.
    for (const row of await ok<Balance[]>(
      await page.request.get(balancesPath),
      'GET /api/admin/leave-balances',
    )) {
      if (row.user_email === STUDENT.email)
        await page.request.delete(`/api/admin/leave-balances/${row.id}`);
    }
    const before = new Set(
      (
        await ok<Balance[]>(await page.request.get(balancesPath), 'GET /api/admin/leave-balances')
      ).map((row) => row.id),
    );

    const dialog = page.getByRole('dialog', { name: 'Leave policy' });
    const filters = dialog.locator('.row-fields').first();
    const bulk = dialog.locator('.row-fields').nth(1);
    const studentRow = dialog.getByRole('row', { name: /Test Student/ });

    try {
      await test.step('1. Open /admin/leave-approvals', async () => {
        await page.goto('/admin/leave-approvals');
      });

      await test.step('2. Click Leave policy', async () => {
        await page.getByRole('button', { name: 'Leave policy' }).click();
        await expect(
          dialog.getByLabel('Academic year'),
          'TC-614 ER-1: the current academic year is filled in',
        ).toHaveValue(year);
        await expect(
          dialog.getByText(`${year} is how REEP spells the current year.`),
          'TC-614 ER-1: the dialog says how the year is spelled',
        ).toBeVisible();
      });

      await test.step('3. Under "Record one allowance for a department", choose "Department of Management Studies · BGSCET", choose RH as the type and enter 3 in Days', async () => {
        await dialog
          .getByRole('combobox', { name: 'Department to record an allowance for' })
          .selectOption({ label: 'Department of Management Studies · BGSCET' });
        await bulk.getByRole('combobox', { name: 'Leave type' }).selectOption('RH');
        await bulk.getByLabel('Days', { exact: true }).fill('3');
      });

      await test.step('4. Untick "Faculty in this department" and tick "Students in this department"', async () => {
        await dialog.getByRole('checkbox', { name: 'Faculty in this department' }).uncheck();
        await dialog.getByRole('checkbox', { name: 'Students in this department' }).check();
      });

      await test.step('5. Click Record', async () => {
        await dialog.getByRole('button', { name: 'Record' }).click();
        await expect(
          dialog.getByRole('status').filter({ hasText: 'recorded.' }),
          'TC-614 ER-2: the office is told what was recorded and what was left alone',
        ).toContainText(
          new RegExp(
            `\\d+ allowances? recorded\\. \\d+ (person|people) already had one for RH ${year} and were left exactly as they were\\.`,
          ),
        );
      });

      await test.step('6. In the Type box at the top of the dialog, choose RH, then click Show', async () => {
        await filters.getByRole('combobox', { name: 'Leave type' }).selectOption('RH');
        await filters.getByRole('button', { name: 'Show' }).click();
        await expect(studentRow, 'TC-614 ER-3: Test Student is listed as a student').toContainText(
          'Student',
        );
        await expect(studentRow, 'TC-614 ER-3: the allowance is RH').toContainText('RH');
        await expect(
          studentRow.getByRole('spinbutton', { name: 'Days entitled for Test Student' }),
          'TC-614 ER-3: 3 days entitled',
        ).toHaveValue('3');
        await expect(
          studentRow.getByRole('spinbutton', { name: 'Days taken by Test Student' }),
          'TC-614 ER-3: 0 days taken',
        ).toHaveValue('0');
        await expect(studentRow.getByRole('cell').nth(4), 'TC-614 ER-3: 3 days left').toHaveText(
          '3',
        );
      });

      await test.step("7. Change Test Student's entitled days to 5, then click Save on that row", async () => {
        await studentRow
          .getByRole('spinbutton', { name: 'Days entitled for Test Student' })
          .fill('5');
        await studentRow.getByRole('button', { name: 'Save' }).click();
        await expect(
          dialog.getByRole('status').filter({ hasText: 'Allowance saved.' }),
          'TC-614 ER-4: the correction is saved',
        ).toBeVisible();
        await expect(studentRow.getByRole('cell').nth(4), 'TC-614 ER-4: 5 days left').toHaveText(
          '5',
        );
      });

      await test.step("8. Click the delete button on Test Student's RH row, and confirm", async () => {
        page.once('dialog', (confirm) => void confirm.accept());
        await studentRow.getByRole('button', { name: 'Remove the RH allowance' }).click();
        await expect(
          dialog.getByRole('status').filter({ hasText: 'Allowance removed.' }),
          'TC-614 ER-5: the allowance is removed',
        ).toBeVisible();
        await expect(studentRow, "TC-614 ER-5: Test Student's RH row is gone").toHaveCount(0);
      });

      await test.step('9. Click Close', async () => {
        await dialog.getByRole('button', { name: 'Close', exact: true }).click();
        await expect(dialog, 'TC-614 ER-6: the dialog closes').toBeHidden();
      });
    } finally {
      await signIn('admin');
      for (const row of await ok<Balance[]>(
        await page.request.get(balancesPath),
        'GET /api/admin/leave-balances',
      )) {
        if (!before.has(row.id)) await page.request.delete(`/api/admin/leave-balances/${row.id}`);
      }
    }
  });

  test('Recording and removing an academic calendar day @TC-615', async ({ page, signIn }) => {
    await signIn('admin');
    const policy = await ok<{ colleges: { college_id: string; college_name: string | null }[] }>(
      await page.request.get('/api/admin/leave-policy'),
      'GET /api/admin/leave-policy',
    );
    const first = policy.colleges[0];
    const college = policy.colleges.find(
      (candidate) => candidate.college_name === 'BGS College of Engineering and Technology',
    );
    if (!first || !college) throw new Error('the seeded college BGSCET is not on this database');
    const date = farDays(5).from;
    const label = `E2E holiday ${RUN}`;
    const dialog = page.getByRole('dialog', { name: 'Academic calendar' });
    const dayRow = dialog.getByRole('row', { name: escapeRegExp(date) });
    const picker = dialog.getByRole('combobox', { name: 'College whose calendar this is' });

    try {
      await test.step('1. Open /admin/leave-approvals', async () => {
        await page.goto('/admin/leave-approvals');
      });

      await test.step('2. Click Calendar', async () => {
        await page.getByRole('button', { name: 'Calendar' }).click();
        await expect(picker, 'TC-615 ER-1: the dialog opens on the first college').toHaveValue(
          first.college_id,
        );
        await expect(
          picker.locator('option', { hasText: 'BGS College of Engineering and Technology' }),
          'TC-615 ER-1: the College list offers BGS College of Engineering and Technology',
        ).toHaveCount(1);
      });

      await test.step('3. In College, choose "BGS College of Engineering and Technology"', async () => {
        const answered = page.waitForResponse((response) =>
          response.url().endsWith(`/api/admin/leave-calendar/${college.college_id}`),
        );
        await picker.selectOption({ label: 'BGS College of Engineering and Technology' });
        await answered;
        await expect(
          picker.locator('option:checked'),
          'TC-615 ER-2: College reads BGS College of Engineering and Technology',
        ).toHaveText('BGS College of Engineering and Technology');
        await expect(dayRow, 'TC-615 ER-2: the test date is not listed yet').toHaveCount(0);
      });

      await test.step('4. Enter the test date in Date, keep "Shut (holiday)", and type the label in Label', async () => {
        await dialog.getByLabel('Date', { exact: true }).fill(date);
        await expect(dialog.getByRole('combobox', { name: 'Kind of day' })).toHaveValue('holiday');
        await dialog.getByLabel('Label', { exact: true }).fill(label);
      });

      await test.step('5. Click Record', async () => {
        await dialog.getByRole('button', { name: 'Record' }).click();
        await expect(
          dialog.getByRole('status').filter({
            hasText: `${date} is recorded as a holiday and will not be counted against an allowance.`,
          }),
          'TC-615 ER-3: the office is told the day is a holiday',
        ).toBeVisible();
        await expect(dayRow, 'TC-615 ER-3: the day is listed as shut').toContainText(
          'Shut · not counted',
        );
        await expect(dayRow, 'TC-615 ER-3: the day carries its label').toContainText(label);
      });

      await test.step("6. Click the delete button on the test date's row, and confirm", async () => {
        page.once('dialog', (confirm) => void confirm.accept());
        await dialog.getByRole('button', { name: `Remove ${date} from the calendar` }).click();
        await expect(
          dialog.getByRole('status').filter({ hasText: 'Day removed.' }),
          'TC-615 ER-4: the day is removed',
        ).toBeVisible();
        await expect(dayRow, 'TC-615 ER-4: the day is gone from the list').toHaveCount(0);
      });
    } finally {
      await signIn('admin');
      const recorded = await ok<{ id: string; day: string }[]>(
        await page.request.get(`/api/admin/leave-calendar/${college.college_id}`),
        'GET /api/admin/leave-calendar',
      );
      for (const day of recorded.filter((candidate) => candidate.day === date)) {
        await page.request.delete(`/api/admin/leave-calendar/${college.college_id}/${day.id}`);
      }
    }
  });

  test('The allowances table stays readable in a short window @TC-616', async ({
    page,
    signIn,
  }) => {
    await page.setViewportSize({ width: 1280, height: 600 });
    await signIn('admin');
    const dialog = page.getByRole('dialog', { name: 'Leave policy' });
    const table = dialog.getByRole('table', { name: /^Leave allowances for / });
    const scroller = table.locator('xpath=..');

    await test.step('1. Open /admin/leave-approvals', async () => {
      await page.goto('/admin/leave-approvals');
    });

    await test.step('2. Click Leave policy', async () => {
      await page.getByRole('button', { name: 'Leave policy' }).click();
      await expect(
        table.getByRole('columnheader', { name: 'Person' }),
        'TC-616 ER-1 (arrange): the table is drawn',
      ).toBeAttached();
      await expect(
        dialog.getByText('Loading the allowances…'),
        'TC-616 ER-1 (arrange): the allowances have been read',
      ).toHaveCount(0);
      const drawn = await scroller.evaluate((box) => Math.min(box.scrollHeight, 300));
      await expect
        .poll(() => scroller.evaluate((box) => box.clientHeight), {
          message: `TC-616 ER-1: the table is shown at its full height (${drawn} px), not squeezed`,
        })
        .toBeGreaterThanOrEqual(drawn - 2);
    });
  });

  // ------------------------------------------------------------ jobs sheet --

  test('The jobs sheet lists the postings on record @TC-620', async ({ page, signIn }) => {
    await signIn('admin');
    const jobs = await ok<Job[]>(await page.request.get('/api/admin/jobs'), 'GET /api/admin/jobs');
    const analyst = jobs.find(
      (job) => job.title === 'Financial Analyst' && job.company === 'Acme Capital',
    );
    if (!analyst)
      throw new Error('the seeded posting "Financial Analyst" at Acme Capital is not on the sheet');

    await test.step('1. Open /admin/jobs', async () => {
      await page.goto('/admin/jobs');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Job postings' }),
        'TC-620 ER-1: the heading',
      ).toBeVisible();
      await expect(
        page.locator('.jobs-stats'),
        'TC-620 ER-1: the stat counts every posting the API lists that is not closed',
      ).toContainText(`${jobs.filter((job) => job.status !== 'closed').length} on the boards`);
      const firstPage = jobs.slice(0, 10);
      await expect(
        page
          .locator('.jobs-ag')
          .getByRole('row')
          .filter({ has: page.getByRole('checkbox', { name: /toggle row selection/ }) }),
        'TC-620 ER-2: the first page holds up to 10 postings',
      ).toHaveCount(firstPage.length);
      for (const job of firstPage) {
        await expect(
          jobRow(page, job.title)
            .filter({
              hasText: job.location === null ? job.company : `${job.company} · ${job.location}`,
            })
            .first(),
          `TC-620 ER-2: "${job.title}" is listed with its company and location`,
        ).toBeVisible();
      }
    });

    await test.step('2. Type "Financial Analyst" in "Search postings…"', async () => {
      await jobSearch(page).fill('Financial Analyst');
      await expect(
        jobRow(page, 'BI Developer'),
        'TC-620 ER-3: a posting that does not match is gone',
      ).toHaveCount(0);
      const row = jobRow(page, 'Financial Analyst');
      await expect(
        row,
        'TC-620 ER-3: the Financial Analyst row stays, at Acme Capital, Bengaluru',
      ).toContainText('Acme Capital · Bengaluru');
      const status =
        analyst.status === 'closed'
          ? 'Withdrawn'
          : analyst.closes_on === null
            ? 'Open · no deadline'
            : 'Open';
      await expect(row, 'TC-620 ER-3: its status is the one the API implies').toContainText(status);
    });
  });

  test("Posting a job puts it on the sheet and on the student's board @TC-621", async ({
    page,
    signIn,
  }) => {
    const title = `E2E Analyst ${RUN}`;
    const company = `E2E Corp ${RUN}`;
    await signIn('admin');

    try {
      await test.step('1. Open /admin/jobs', async () => {
        await page.goto('/admin/jobs');
      });

      await test.step('2. Click Post a job', async () => {
        await page.getByRole('button', { name: 'Post a job' }).click();
        await expect(
          page.getByRole('complementary', { name: 'Post a job' }),
          'TC-621 ER-1: the panel opens',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Close the form' }),
          'TC-621 ER-1: the header button reads "Close the form"',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Publish' }),
          'TC-621 ER-1: Publish is disabled while empty',
        ).toBeDisabled();
      });

      await test.step('3. Enter the title, the company and the location', async () => {
        await page.getByLabel(/^Title/).fill(title);
        await page.getByLabel(/^Company/).fill(company);
        await page.getByLabel('Location', { exact: true }).fill('Mysuru');
      });

      await test.step('4. Type "fin" in Tracks', async () => {
        await page.getByLabel('Tracks', { exact: true }).fill('fin');
        await expect(
          page.getByText(/Specialization codes, comma separated\./),
          'TC-621 ER-2: the help shows the code as it will be sent',
        ).toHaveText('Specialization codes, comma separated. Sent as FIN.');
      });

      await test.step('5. Click Publish', async () => {
        await page.getByRole('button', { name: 'Publish' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: `${title} at ${company} is on the sheet.` }),
          'TC-621 ER-3: the office is told the posting is on the sheet',
        ).toBeVisible();
        await expect(
          page.getByRole('complementary', { name: 'Post a job' }),
          'TC-621 ER-3: the panel closes',
        ).toBeHidden();
      });

      await test.step('6. Type the title in "Search postings…"', async () => {
        await jobSearch(page).fill(title);
        await expect(page.getByText('Rows: 1'), 'TC-621 ER-4: one row is listed').toBeVisible();
        const row = jobRow(page, title);
        await expect(row, 'TC-621 ER-4: with its company and location').toContainText(
          `${company} · Mysuru`,
        );
        await expect(
          row.getByRole('gridcell', { name: 'FIN', exact: true }),
          'TC-621 ER-4: track FIN',
        ).toBeVisible();
        await expect(row, 'TC-621 ER-4: status "Open · no deadline"').toContainText(
          'Open · no deadline',
        );
        await expect(
          row.getByRole('gridcell', { name: '0', exact: true }),
          'TC-621 ER-4: 0 applied',
        ).toBeVisible();
      });

      await test.step('7. Sign in as the student and open /student/jobs', async () => {
        await signIn('student');
        await page.goto('/student/jobs');
        const listed = page.getByRole('row', { name: escapeRegExp(title) });
        await expect(listed, "TC-621 ER-5: the posting is on the student's board").toBeVisible();
        await expect(listed, 'TC-621 ER-5: with its company').toContainText(company);
      });
    } finally {
      await signIn('admin');
      await removeJobsTitled(page, title);
    }
  });

  test('A posting is not published without a title, a company and a web apply link @TC-622', async ({
    page,
    signIn,
  }) => {
    const title = `E2E Refused ${RUN}`;
    const company = `E2E Corp ${RUN}`;
    await signIn('admin');
    const publish = page.getByRole('button', { name: 'Publish' });

    try {
      await test.step('1. Open /admin/jobs', async () => {
        await page.goto('/admin/jobs');
      });

      await test.step('2. Click Post a job', async () => {
        await page.getByRole('button', { name: 'Post a job' }).click();
      });

      await test.step('3. Enter the title', async () => {
        await page.getByLabel(/^Title/).fill(title);
        await expect(
          publish,
          'TC-622 ER-1: Publish is still disabled without a company',
        ).toBeDisabled();
      });

      await test.step('4. Enter the company', async () => {
        await page.getByLabel(/^Company/).fill(company);
        await expect(publish, 'TC-622 ER-2: Publish is enabled').toBeEnabled();
      });

      await test.step('5. Enter the apply link', async () => {
        await page.getByLabel('Apply link', { exact: true }).fill('careers.example.com/e2e');
      });

      await test.step('6. Click Publish', async () => {
        await publish.click();
        await expect(
          page
            .getByRole('alert')
            .filter({ hasText: 'The apply link must start with http:// or https://' }),
          'TC-622 ER-3: the link is refused in words',
        ).toBeVisible();
        await expect(
          page.getByRole('complementary', { name: 'Post a job' }),
          'TC-622 ER-3: the panel stays open',
        ).toBeVisible();
        const jobs = await ok<Job[]>(
          await page.request.get('/api/admin/jobs'),
          'GET /api/admin/jobs',
        );
        expect(
          jobs.filter((job) => job.title === title),
          'TC-622 ER-3: nothing was published',
        ).toHaveLength(0);
      });

      await test.step('7. Click Cancel', async () => {
        await page.getByRole('button', { name: 'Cancel' }).click();
        await expect(
          page.getByRole('complementary', { name: 'Post a job' }),
          'TC-622 ER-4: the panel closes',
        ).toBeHidden();
        await jobSearch(page).fill(title);
        await expect(
          page.getByText('Rows: 0'),
          'TC-622 ER-4: no posting with the title is on the sheet',
        ).toBeVisible();
      });
    } finally {
      await signIn('admin');
      await removeJobsTitled(page, title);
    }
  });

  test('Duplicating a posting copies it into the form @TC-623', async ({ page, signIn }) => {
    const title = `E2E Duplicate ${RUN}`;
    const company = `E2E Corp ${RUN}`;
    await signIn('admin');
    await postJobThroughApi(page, title, company, { location: 'Remote', tracks: ['FIN'] });

    try {
      await test.step('1. Open /admin/jobs', async () => {
        await page.goto('/admin/jobs');
      });

      await test.step('2. Type the title in "Search postings…"', async () => {
        await jobSearch(page).fill(title);
        await expect(
          jobRow(page, title),
          'TC-623 ER-1 (arrange): the posting is listed',
        ).toBeVisible();
      });

      await test.step("3. Tick the posting's row", async () => {
        await expect(
          page.getByRole('button', { name: 'Duplicate' }),
          'TC-623 ER-1 (arrange): nothing ticked yet',
        ).toBeDisabled();
        await jobRow(page, title).getByRole('checkbox').check();
        await expect(
          page.getByText('Selected: 1'),
          'TC-623 ER-1: one posting is selected',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Duplicate' }),
          'TC-623 ER-1: Duplicate is enabled',
        ).toBeEnabled();
        await expect(
          page.getByRole('button', { name: 'Close posting' }),
          'TC-623 ER-1: Close posting is enabled',
        ).toBeEnabled();
        await expect(
          page.getByRole('button', { name: 'Remove' }),
          'TC-623 ER-1: Remove is enabled',
        ).toBeEnabled();
      });

      await test.step('4. Click Duplicate', async () => {
        await page.getByRole('button', { name: 'Duplicate' }).click();
        await expect(
          page
            .getByRole('status')
            .filter({ hasText: 'Copied into the form. Check the closing date, then Publish.' }),
          'TC-623 ER-2: the office is told the posting was copied',
        ).toBeVisible();
        await expect(page.getByLabel(/^Title/), 'TC-623 ER-2: the title is copied').toHaveValue(
          title,
        );
        await expect(page.getByLabel(/^Company/), 'TC-623 ER-2: the company is copied').toHaveValue(
          company,
        );
        await expect(
          page.getByLabel('Location', { exact: true }),
          'TC-623 ER-2: the location is copied',
        ).toHaveValue('Remote');
        await expect(
          page.getByLabel('Tracks', { exact: true }),
          'TC-623 ER-2: the tracks are copied',
        ).toHaveValue('FIN');
      });

      await test.step('5. Click Cancel', async () => {
        await page.getByRole('button', { name: 'Cancel' }).click();
        await expect(
          page.getByRole('complementary', { name: 'Post a job' }),
          'TC-623 ER-3: the panel closes',
        ).toBeHidden();
        await expect(
          page.getByText('Rows: 1'),
          'TC-623 ER-3: still exactly one posting with the title',
        ).toBeVisible();
        const jobs = await ok<Job[]>(
          await page.request.get('/api/admin/jobs'),
          'GET /api/admin/jobs',
        );
        expect(
          jobs.filter((job) => job.title === title),
          'TC-623 ER-3: nothing was published',
        ).toHaveLength(1);
      });
    } finally {
      await signIn('admin');
      await removeJobsTitled(page, title);
    }
  });

  test("Closing a posting takes it off the student's board @TC-624", async ({ page, signIn }) => {
    const title = `E2E Close ${RUN}`;
    const company = `E2E Corp ${RUN}`;
    await signIn('admin');
    await postJobThroughApi(page, title, company);
    await signIn('student');
    expect(
      await studentBoardTitles(page),
      "TC-624 ER-4 (arrange): the student's board lists the posting",
    ).toContain(title);
    await signIn('admin');
    const status = page.getByRole('combobox', { name: 'Filter postings by status' });

    try {
      await test.step('1. Open /admin/jobs', async () => {
        await page.goto('/admin/jobs');
      });

      await test.step('2. Type the title in "Search postings…"', async () => {
        await jobSearch(page).fill(title);
      });

      await test.step("3. Tick the posting's row", async () => {
        await jobRow(page, title).getByRole('checkbox').check();
      });

      await test.step('4. Click Close posting, and confirm', async () => {
        const asked = new Promise<string>((resolve) => {
          page.once('dialog', (confirm) => {
            resolve(confirm.message());
            void confirm.accept();
          });
        });
        await page.getByRole('button', { name: 'Close posting' }).click();
        expect(
          await asked,
          'TC-624 ER-1: the browser asks first, and says there is no reopen',
        ).toBe(
          `Take “${title}” at ${company} off the student and alumni boards? There is no reopen — republishing is the only way back.`,
        );
        await expect(
          page.getByRole('status').filter({
            hasText: `${title} at ${company} is off both boards. Applications against it are untouched.`,
          }),
          'TC-624 ER-1: the office is told it is off both boards',
        ).toBeVisible();
        await expect(jobRow(page, title), 'TC-624 ER-1: the row reads Withdrawn').toContainText(
          'Withdrawn',
        );
      });

      await test.step('5. In Status, choose "On the boards"', async () => {
        await status.selectOption({ label: 'On the boards' });
        await expect(
          jobRow(page, title),
          'TC-624 ER-2: the posting is not on the boards',
        ).toHaveCount(0);
      });

      await test.step('6. In Status, choose "Withdrawn"', async () => {
        await status.selectOption({ label: 'Withdrawn' });
        await expect(
          jobRow(page, title),
          'TC-624 ER-3: the posting is listed as Withdrawn',
        ).toContainText('Withdrawn');
      });

      await test.step('7. Sign in as the student and open /student/jobs', async () => {
        await signIn('student');
        await page.goto('/student/jobs');
        await expect(
          page.getByRole('heading', { level: 1, name: 'Jobs' }),
          'TC-624 ER-4 (arrange): the board is shown',
        ).toBeVisible();
        await expect(
          page.getByRole('table').or(page.getByText('No roles on the board yet.')),
          'TC-624 ER-4 (arrange): the board has loaded',
        ).toBeVisible();
        await expect(
          page.getByText(title),
          "TC-624 ER-4: the posting is off the student's board",
        ).toHaveCount(0);
      });
    } finally {
      await signIn('admin');
      await removeJobsTitled(page, title);
    }
  });

  test('Removing a posting nobody has applied to @TC-625', async ({ page, signIn }) => {
    const title = `E2E Remove ${RUN}`;
    const company = `E2E Corp ${RUN}`;
    await signIn('admin');
    await postJobThroughApi(page, title, company);
    const remove = page.getByRole('button', { name: 'Remove' });

    try {
      await test.step('1. Open /admin/jobs', async () => {
        await page.goto('/admin/jobs');
      });

      await test.step('2. Type the title in "Search postings…"', async () => {
        await jobSearch(page).fill(title);
        await expect(
          remove,
          'TC-625 ER-1 (arrange): Remove is disabled with nothing ticked',
        ).toBeDisabled();
      });

      await test.step("3. Tick the posting's row", async () => {
        await jobRow(page, title).getByRole('checkbox').check();
        await expect(remove, 'TC-625 ER-1: Remove is enabled').toBeEnabled();
      });

      await test.step('4. Click Remove', async () => {
        await remove.click();
        await expect(
          page
            .getByRole('status')
            .filter({ hasText: `${title} at ${company} was removed from the sheet.` }),
          'TC-625 ER-2: the office is told it was removed',
        ).toBeVisible();
        await expect(
          page.getByText('Rows: 0'),
          'TC-625 ER-2: the posting is no longer listed',
        ).toBeVisible();
        const jobs = await ok<Job[]>(
          await page.request.get('/api/admin/jobs'),
          'GET /api/admin/jobs',
        );
        expect(
          jobs.filter((job) => job.title === title),
          'TC-625 ER-2: the API no longer lists it',
        ).toHaveLength(0);
      });
    } finally {
      await signIn('admin');
      await removeJobsTitled(page, title);
    }
  });

  test('Searching the jobs sheet by company or location @TC-626', async ({ page, signIn }) => {
    await signIn('admin');

    await test.step('1. Open /admin/jobs', async () => {
      await page.goto('/admin/jobs');
      await expect(
        jobRow(page, 'Financial Analyst'),
        'TC-626 (arrange): the seeded posting is listed',
      ).toBeVisible();
    });

    await test.step('2. Type "Acme Capital" in "Search postings…"', async () => {
      await jobSearch(page).fill('Acme Capital');
      // Waits for the (debounced) search to apply: a posting at another
      // company leaves the sheet whether or not the company is searched.
      await expect(
        jobRow(page, 'BI Developer'),
        'TC-626 ER-1: the search has narrowed the sheet',
      ).toHaveCount(0);
      await expect(
        jobRow(page, 'Financial Analyst'),
        'TC-626 ER-1: found by its company',
      ).toBeVisible();
    });

    await test.step('3. Replace the search with "Bengaluru"', async () => {
      await jobSearch(page).fill('Bengaluru');
      await expect(
        jobRow(page, 'Financial Analyst'),
        'TC-626 ER-2: found by its location',
      ).toBeVisible();
    });
  });

  // -------------------------------------------------------------- placement --

  test('Placement shows the funnel and the offers from the database @TC-630', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const figures = await ok<Placement>(
      await page.request.get('/api/admin/placement'),
      'GET /api/admin/placement',
    );
    const pending = await pendingOfferCount(page);

    await test.step('1. Open /admin/placement', async () => {
      await page.goto('/admin/placement');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Placement & offers' }),
        'TC-630 ER-1: the heading',
      ).toBeVisible();
      await expect(
        page.getByRole('img', { name: /^Placement funnel, counts are distinct students\./ }),
        "TC-630 ER-1: the funnel carries the API's four counts",
      ).toHaveAttribute(
        'aria-label',
        'Placement funnel, counts are distinct students. ' +
          `Eligible students: ${plural(figures.eligible, 'student')}; ` +
          `Applied to ≥1 job: ${plural(figures.applied, 'student')}; ` +
          `Holding an offer: ${plural(figures.offered_students, 'student')}; ` +
          `Placed (approved offer): ${plural(figures.approved_students, 'student')}.`,
      );
      await expect(
        page.getByText('Interviewed — not counted.'),
        'TC-630 ER-2: the funnel says the interviewed stage is not counted',
      ).toBeVisible();
      await expect(
        page.getByText(/No recruiter interview round is recorded anywhere in REEP\./),
        'TC-630 ER-2: and why',
      ).toBeVisible();

      const card = page
        .locator('.side-card')
        .filter({ has: page.getByRole('heading', { name: 'Offers on record' }) });
      await expect(
        card.locator('.kpi').first(),
        "TC-630 ER-3: the card shows the API's placement rate",
      ).toHaveText(figures.placement_rate_pct === null ? '—' : `${figures.placement_rate_pct}%`);
      await expect(
        card.locator('.kpi-sub').first(),
        'TC-630 ER-3: approved of eligible',
      ).toHaveText(`${figures.approved_students} of ${figures.eligible} eligible`);

      const tracks = page.locator('.track-split');
      // By position: the table draws the API's list in order, and two
      // colleges may each have a track with the same name.
      for (const [index, track] of figures.by_track.entries()) {
        const row = tracks.getByRole('row').nth(index + 1);
        await expect(
          row.getByRole('cell').first(),
          `TC-630 ER-4: row ${index + 1} is ${track.name}`,
        ).toHaveText(track.name);
        await expect(row.getByRole('cell').nth(1), `TC-630 ER-4: ${track.name} placed`).toHaveText(
          String(track.placed),
        );
        await expect(
          row.getByRole('cell').nth(2),
          `TC-630 ER-4: ${track.name} eligible`,
        ).toHaveText(String(track.eligible));
      }
      expect(
        figures.by_track.map((track) => track.name),
        'TC-630 ER-4: the API splits Test Student into Finance',
      ).toContain('Finance');

      await expect(
        page.getByRole('button', { name: `Offers · ${pending} pending` }),
        'TC-630 ER-5: the Offers tab counts the pending offers',
      ).toBeVisible();
    });
  });

  test("Placement's batch and period filters, and the offers export @TC-631", async ({
    page,
    signIn,
  }) => {
    const organisation = `E2E Filter Org ${RUN}`;
    const offerId = await submitOfferAsStudent(page, signIn, organisation, 'E2E Filter Associate');
    await ok(
      await page.request.post(`/api/mentor/offers/${offerId}/decision`, {
        data: { decision: 'REJECT', note: `E2E filter setup ${RUN}` },
      }),
      'POST /api/mentor/offers/{id}/decision',
    );
    const cohort = await seededCohort(page);
    const year = String(new Date().getUTCFullYear());
    const batch = page.getByRole('combobox', { name: /^Batch — narrows every figure/ });
    const period = page.getByRole('combobox', { name: /^Period — narrows the offer figures/ });
    const header = page.getByText(/Who got placed, and the offers waiting for a decision\./);

    await test.step('1. Open /admin/placement', async () => {
      await page.goto('/admin/placement');
      await expect(pillValue(batch), 'TC-631 ER-1: Batch reads "All batches"').toHaveText(
        'All batches',
      );
      await expect(pillValue(period), 'TC-631 ER-1: Period reads "Whole record"').toHaveText(
        'Whole record',
      );
      await expect(
        page.getByRole('heading', { name: 'Offers on record' }),
        'TC-631 ER-1: the card heading',
      ).toBeVisible();
      await expect(
        page.getByRole('link', { name: 'Export offers' }),
        'TC-631 ER-1: the export button',
      ).toBeVisible();
    });

    await test.step('2. In Batch, choose "Master of Business Administration - Finance · 2024-26 Section B"', async () => {
      await batch.selectOption({ label: cohort.display_label });
      await expect(header, 'TC-631 ER-2: the header names the batch').toContainText(
        `· ${cohort.name}`,
      );
      await expect(
        page.getByRole('link', { name: 'Export this batch' }),
        'TC-631 ER-2: the export button',
      ).toBeVisible();
    });

    await test.step('3. In Period, choose the current year', async () => {
      await period.selectOption({ label: year });
      await expect(
        page.getByRole('heading', { name: `Offers in ${year}` }),
        'TC-631 ER-3: the card is headed by the year',
      ).toBeVisible();
      await expect(
        page.getByText(`offers submitted in ${year}, over the students on the roll today`),
        'TC-631 ER-3: the card says what it counts',
      ).toBeVisible();
      await expect(header, 'TC-631 ER-3: the header names the batch and the year').toContainText(
        `· ${cohort.name} · offers submitted in ${year}`,
      );
    });

    await test.step('4. Click "Export this batch"', async () => {
      const file = await download(page, () =>
        page.getByRole('link', { name: 'Export this batch' }).click(),
      );
      expect(file.name, 'TC-631 ER-4: the file name').toBe('reep-placement-summary.csv');
      expect(csvLines(file.text)[0], 'TC-631 ER-4: the header row').toBe(
        'Student,USN,Company,Role,Role type,CTC (INR),Status,Submitted,Decided',
      );
    });

    await test.step('5. In Batch, choose "All batches", and in Period, choose "Whole record"', async () => {
      await batch.selectOption({ label: 'All batches' });
      await period.selectOption({ label: 'Whole record' });
      await expect(
        page.getByRole('heading', { name: 'Offers on record' }),
        'TC-631 ER-5: the card heading is back',
      ).toBeVisible();
      await expect(
        page.getByRole('link', { name: 'Export offers' }),
        'TC-631 ER-5: the export button is back',
      ).toBeVisible();
    });
  });

  test('Rejecting a submitted offer needs remarks @TC-632', async ({ page, signIn }) => {
    const organisation = `E2E Reject Org ${RUN}`;
    const remarks = `Offer letter not attached (E2E ${RUN})`;
    const offerId = await submitOfferAsStudent(page, signIn, organisation, 'E2E Associate');
    const pendingBefore = await pendingOfferCount(page);
    // Wide enough that the offers grid draws its last column, Status, without
    // scrolling: AG Grid does not render a column that is out of view.
    await page.setViewportSize({ width: 1600, height: 1000 });
    const row = page.getByRole('row', { name: escapeRegExp(organisation) });

    try {
      await test.step('1. Open /admin/placement', async () => {
        await page.goto('/admin/placement');
      });

      await test.step('2. Type the organisation in "Search offers…"', async () => {
        await page.getByRole('searchbox', { name: 'Search the offers table' }).fill(organisation);
        await expect(row, 'TC-632 ER-1: the offer names the student').toContainText(STUDENT.name);
        await expect(row, 'TC-632 ER-1: and the role').toContainText('E2E Associate · Full-time');
        await expect(row, 'TC-632 ER-1: and is awaiting approval').toContainText(
          'Awaiting approval',
        );
      });

      await test.step("3. Tick the offer's row", async () => {
        await row.getByRole('checkbox').check();
        await expect(
          page.getByText('1 selected · only an offer awaiting approval can be ticked'),
          'TC-632 ER-2: one offer is selected',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Reject', exact: true }),
          'TC-632 ER-2: Reject is enabled',
        ).toBeEnabled();
        await expect(
          page.getByRole('button', { name: 'Approve offer' }),
          'TC-632 ER-2: Approve offer is enabled',
        ).toBeEnabled();
      });

      await test.step('4. Click Reject', async () => {
        await page.getByRole('button', { name: 'Reject', exact: true }).click();
      });

      await test.step('5. Click Confirm reject without typing any remarks', async () => {
        await page.getByRole('button', { name: 'Confirm reject' }).click();
        await expect(
          page.getByRole('alert').filter({ hasText: 'Remarks are required.' }),
          'TC-632 ER-3: a refusal without remarks is stopped',
        ).toBeVisible();
        await expect(row, 'TC-632 ER-3: the offer is still awaiting approval').toContainText(
          'Awaiting approval',
        );
      });

      await test.step('6. Type the remarks in the Remarks box, then click Confirm reject', async () => {
        await page.getByLabel(/Remarks — why this offer is not approved/).fill(remarks);
        await page.getByRole('button', { name: 'Confirm reject' }).click();
        await expect(
          page.getByRole('status').filter({
            hasText: `Test Student's offer from ${organisation} was not approved; they have your remarks.`,
          }),
          'TC-632 ER-4: the office is told the offer was not approved',
        ).toBeVisible();
        await expect(row, 'TC-632 ER-4: the offer reads "Not approved"').toContainText(
          'Not approved',
        );
        await expect(
          page.getByRole('button', { name: `Offers · ${pendingBefore - 1} pending` }),
          'TC-632 ER-4: one offer fewer is pending',
        ).toBeVisible();
      });
    } finally {
      await signIn('admin');
      await page.request.post(`/api/mentor/offers/${offerId}/decision`, {
        data: { decision: 'REJECT', note: `E2E cleanup ${RUN}` },
      });
    }
  });

  // ---------------------------------------------------------------- exports --

  test('Downloading each report, with its receipt in the history @TC-640', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const history = page.locator('.history-grid');
    const newest = firstGridRow(history);

    const card = (title: string) =>
      page
        .locator('.extract-card')
        .filter({ has: page.getByRole('heading', { name: title, exact: true }) });

    async function downloadCard(
      title: string,
      filename: string,
      header: RegExp | string,
      er: string,
      receiptEr: string,
    ) {
      const file = await download(page, () =>
        card(title).getByRole('link', { name: 'Download CSV' }).click(),
      );
      expect(file.name, `TC-640 ${er}: the ${title} file name`).toBe(filename);
      const first = csvLines(file.text)[0];
      if (typeof header === 'string')
        expect(first, `TC-640 ${er}: the ${title} header row`).toBe(header);
      else expect(first, `TC-640 ${er}: the ${title} header row`).toMatch(header);
      await expect(
        newest.getByRole('gridcell').first(),
        `TC-640 ${receiptEr}: the newest receipt is the ${title} download`,
      ).toHaveText(title);
      return file;
    }

    await test.step('1. Open /admin/exports', async () => {
      await page.goto('/admin/exports');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Download reports' }),
        'TC-640 ER-1: the heading',
      ).toBeVisible();
      for (const title of ['Students', 'Placement', 'Ledger', 'Interviews', 'Skills & badges']) {
        await expect(
          card(title),
          `TC-640 ER-1: the ${title} card carries the name and USN`,
        ).toContainText('Carries name & USN');
      }
      await expect(
        page.locator('.history-scope').getByText('Whole programme', { exact: true }),
        'TC-640 ER-1: the reach reads "Whole programme"',
      ).toBeVisible();
      await expect(
        page.getByText(/downloads? shown|Nothing has been downloaded yet\./).first(),
        'TC-640 ER-3 (arrange): the history has been read',
      ).toBeVisible();
      if ((await newest.count()) > 0) {
        await expect(
          newest.getByRole('gridcell').first(),
          'TC-640 ER-3 (arrange): the newest receipt is not already a Students download',
        ).not.toHaveText('Students');
      }
    });

    await test.step('2. In the Students card, click Download CSV', async () => {
      const file = await downloadCard(
        'Students',
        'reep-students-mentor-map.csv',
        'Name,USN,REEP stage,Semester,Cohort,Mentor',
        'ER-2',
        'ER-3',
      );
      expect(file.text, 'TC-640 ER-2: Test Student is in the file').toContain(
        `${STUDENT.name},${STUDENT.usn},`,
      );
      await expect(
        page
          .getByRole('status')
          .filter({ hasText: 'Students requested — your browser is saving the file.' }),
        'TC-640 ER-2: the screen says the file is being saved',
      ).toBeVisible();
      await expect(newest, 'TC-640 ER-3: the receipt covers the whole programme').toContainText(
        'Whole programme',
      );
      await expect(newest, 'TC-640 ER-3: the receipt names the Main Admin').toContainText(
        ADMIN.name,
      );
      await expect(newest, 'TC-640 ER-3: the receipt is audited as carrying PII').toContainText(
        'PII · audited',
      );
    });

    await test.step('3. In the Placement card, click Download CSV', async () => {
      await downloadCard(
        'Placement',
        'reep-placement-summary.csv',
        'Student,USN,Company,Role,Role type,CTC (INR),Status,Submitted,Decided',
        'ER-4',
        'ER-4',
      );
    });

    await test.step('4. In the Ledger card, click Download CSV', async () => {
      await downloadCard(
        'Ledger',
        'reep-ledger-compliance.csv',
        'Name,USN,Days logged,Days submitted,Hours logged,Productive hours',
        'ER-5',
        'ER-5',
      );
    });

    await test.step('5. In the Interviews card, click Download CSV', async () => {
      await downloadCard(
        'Interviews',
        'reep-interview-scores.csv',
        'Name,USN,Started,Track,Status,Overall,Communication,Domain,Structure,Record',
        'ER-6',
        'ER-6',
      );
    });

    await test.step('6. In the "Skills & badges" card, click Download CSV', async () => {
      await downloadCard(
        'Skills & badges',
        'reep-cohort-skill-report.csv',
        /^Name,USN,REEP stage,Points,.*,Mean growth from T0$/,
        'ER-7',
        'ER-7',
      );
    });
  });

  // ---------------------------------------------------------------- imports --

  test('The import wizard asks for a batch, a semester and a file before it checks one @TC-650', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const cohort = await seededCohort(page);
    const dataset = page.getByRole('combobox', { name: 'Dataset to import' });
    const batch = page.getByRole('combobox', { name: 'Batch', exact: true });
    const check = page.getByRole('button', { name: 'Check file' });
    const wizardSays = (text: string) => page.getByRole('status').filter({ hasText: text });

    await test.step('1. Open /admin/imports', async () => {
      await page.goto('/admin/imports');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Upload spreadsheets' }),
        'TC-650 ER-1: the heading',
      ).toBeVisible();
      await expect(pillValue(dataset), 'TC-650 ER-1: the file contains semester marks').toHaveText(
        'Semester marks',
      );
      await expect(check, 'TC-650 ER-1: Check file is disabled').toBeDisabled();
      await expect(
        wizardSays('Choose the batch this file is for.'),
        'TC-650 ER-1: the wizard asks for the batch',
      ).toBeVisible();
    });

    await test.step('2. In Batch, choose the seeded batch', async () => {
      await batch.selectOption(cohort.id);
      await expect(check, 'TC-650 ER-2: Check file is still disabled').toBeDisabled();
      await expect(
        wizardSays('A marks file must name the semester it is for.'),
        'TC-650 ER-2: the wizard asks for the semester',
      ).toBeVisible();
    });

    await test.step('3. In Semester, choose 2', async () => {
      await page.getByRole('combobox', { name: 'Semester' }).selectOption('2');
      await expect(
        wizardSays('Choose the spreadsheet to check.'),
        'TC-650 ER-3: the wizard asks for the file',
      ).toBeVisible();
    });

    await test.step('4. In "File contains", choose Attendance', async () => {
      await dataset.selectOption({ label: 'Attendance' });
      await expect(
        page.getByText(/An attendance file needs no semester\./),
        'TC-650 ER-4: the wizard explains that attendance needs no semester',
      ).toBeVisible();
      await expect(
        page.getByText(
          'One row per student per subject: usn, subject_code, sessions_held, sessions_attended.',
        ),
        'TC-650 ER-4: the file shape is the attendance one',
      ).toBeVisible();
    });

    await test.step('5. Click "Download template"', async () => {
      const file = await download(page, () =>
        page.getByRole('link', { name: 'Download the Attendance template' }).click(),
      );
      expect(file.name, 'TC-650 ER-5: the attendance template').toBe(
        'reep-attendance-template.xlsx',
      );
      expect(file.bytes.subarray(0, 2).toString('latin1'), 'TC-650 ER-5: an xlsx is a zip').toBe(
        'PK',
      );
    });

    await test.step('6. Click "New import"', async () => {
      await page.getByRole('button', { name: 'New import' }).click();
      await expect(
        wizardSays('The import wizard is back at step 1, with nothing chosen.'),
        'TC-650 ER-6: the wizard says it started again',
      ).toBeVisible();
      await expect(
        pillValue(dataset),
        'TC-650 ER-6: the file contains semester marks again',
      ).toHaveText('Semester marks');
      await expect(pillValue(batch), 'TC-650 ER-6: no batch is chosen').toHaveText(
        'Choose a batch',
      );
    });
  });

  test('Checking and importing an attendance spreadsheet @TC-651', async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('admin');
    const cohort = await seededCohort(page);
    // Line 3 must name nobody: other modules create students with fresh USNs.
    for (const suffix of ['', '&removed=true']) {
      const holders = await ok<unknown[]>(
        await page.request.get(`/api/admin/students?q=1BG24MBA999${suffix}`),
        'GET /api/admin/students',
      );
      if (holders.length > 0)
        block(testInfo, 'a student holds USN 1BG24MBA999, which TC-651 needs to be free.');
    }
    const name = `e2e-attendance-${RUN}.csv`;
    const csv = [
      'usn,subject_code,sessions_held,sessions_attended',
      `${STUDENT.usn},22MBA11,20,18`,
      '1BG24MBA999,22MBA11,20,18',
      `${STUDENT.usn},22MBA12,20,25`,
    ].join('\n');
    const preview = page.locator('.preview-card');
    const recent = page.locator('.history-card');
    let runId = '';

    await test.step('1. Open /admin/imports', async () => {
      await page.goto('/admin/imports');
    });

    await test.step('2. In "File contains", choose Attendance', async () => {
      await page
        .getByRole('combobox', { name: 'Dataset to import' })
        .selectOption({ label: 'Attendance' });
    });

    await test.step('3. In Batch, choose the seeded batch', async () => {
      await page.getByRole('combobox', { name: 'Batch', exact: true }).selectOption(cohort.id);
    });

    await test.step('4. Click "Choose file" and choose the attendance file', async () => {
      await page
        .locator('input[type="file"]')
        .setInputFiles({ name, mimeType: 'text/csv', buffer: Buffer.from(csv) });
      await expect(
        page.getByText(new RegExp(`${name} · `)),
        'TC-651 ER-1: the file name and size are shown',
      ).toBeVisible();
      await expect(
        page
          .getByRole('status')
          .filter({ hasText: `${name} is ready. Press Check file to have the server read it.` }),
        'TC-651 ER-1: the wizard says the file is ready',
      ).toBeVisible();
    });

    await test.step('5. Click Check file', async () => {
      const answered = page.waitForResponse((response) =>
        response.url().endsWith('/api/admin/imports/preview'),
      );
      await page.getByRole('button', { name: 'Check file' }).click();
      runId = ((await (await answered).json()) as { run: { id: string } }).run.id;
      await expect(
        page
          .getByRole('status')
          .filter({ hasText: '3 lines read · 1 would be written. Nothing has been saved yet.' }),
        'TC-651 ER-2: the wizard sums up the check',
      ).toBeVisible();
      await expect(
        preview.getByRole('heading', { name: `Preview · ${name}` }),
        'TC-651 ER-2: the preview heading',
      ).toBeVisible();
      for (const chip of ['3 lines read', '0 ok', '1 flagged', '2 refused', '1 would be written']) {
        await expect(
          preview.getByText(chip, { exact: true }),
          `TC-651 ER-2: the count "${chip}"`,
        ).toBeVisible();
      }
      const warning = preview.getByRole('row').filter({ hasText: 'Warning' });
      await expect(warning, 'TC-651 ER-3: one line is flagged').toHaveCount(1);
      await expect(warning, 'TC-651 ER-3: line 2, for Test Student').toContainText(STUDENT.name);
      await expect(warning, 'TC-651 ER-3: the flagged line is USN 1BG24MBA001').toContainText(
        STUDENT.usn,
      );
      await expect(
        warning.getByRole('gridcell', { name: '2', exact: true }),
        'TC-651 ER-3: it is line 2',
      ).toBeVisible();
      await expect(
        preview.getByRole('row').filter({ hasText: 'Error' }),
        'TC-651 ER-3: two lines are refused',
      ).toHaveCount(2);
      await expect(
        preview.getByRole('row').filter({ hasText: '1BG24MBA999' }),
        'TC-651 ER-3: line 3 is refused',
      ).toContainText('Error');

      const newest = firstGridRow(recent);
      await expect(newest, 'TC-651 ER-4: the newest run is this attendance file').toContainText(
        'Attendance',
      );
      await expect(newest, 'TC-651 ER-4: previewed').toContainText('Previewed');
      await expect(newest, 'TC-651 ER-4: nothing written yet').toContainText('3 read · 0 written');
      await expect(newest, 'TC-651 ER-4: the checks').toContainText('0 ok · 1 flagged · 2 refused');
    });

    await test.step('6. Click "Error report"', async () => {
      const file = await download(page, () =>
        page.getByRole('link', { name: 'Download the error report for this run' }).click(),
      );
      expect(file.name, 'TC-651 ER-5: the file is named after the run').toBe(
        `import-attendance-${runId.slice(0, 8)}-errors.csv`,
      );
      const lines = csvLines(file.text);
      expect(lines[0], 'TC-651 ER-5: the header row').toBe(
        'Line,Check,USN,Student,Subject,Message',
      );
      expect(lines, 'TC-651 ER-5: three lines, one per flagged or refused line').toHaveLength(4);
      expect(lines[1], 'TC-651 ER-5: line 2 overwrites what is on file').toContain(
        '22MBA11 — overwrites the record already on file',
      );
      expect(lines[2], 'TC-651 ER-5: line 3 names nobody in the batch').toContain(
        '1BG24MBA999 is not a student in this batch',
      );
      expect(lines[3], 'TC-651 ER-5: line 4 attended more than held').toContain(
        'sessions_attended: 25 attended is more than the 20 held',
      );
    });

    await test.step('7. Click "Import rows"', async () => {
      await page.getByRole('button', { name: 'Import rows' }).click();
      await expect(
        page
          .getByRole('status')
          .filter({ hasText: '20 records written across 1 student, from 1 line.' }),
        'TC-651 ER-6: the office is told what was written',
      ).toBeVisible();
      await expect(
        preview.getByText('1 written', { exact: true }),
        'TC-651 ER-6: the count "1 written"',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Imported' }),
        'TC-651 ER-6: the button reads Imported',
      ).toBeDisabled();
      const newest = firstGridRow(recent);
      await expect(newest, 'TC-651 ER-7: the run is Imported').toContainText('Imported');
      await expect(newest, 'TC-651 ER-7: one line written').toContainText('3 read · 1 written');
    });
  });

  test('A spreadsheet with no data rows is refused @TC-652', async ({ page, signIn }) => {
    await signIn('admin');
    const cohort = await seededCohort(page);
    const name = `e2e-empty-${RUN}.csv`;

    await test.step('1. Open /admin/imports', async () => {
      await page.goto('/admin/imports');
    });

    await test.step('2. In "File contains", choose Attendance', async () => {
      await page
        .getByRole('combobox', { name: 'Dataset to import' })
        .selectOption({ label: 'Attendance' });
    });

    await test.step('3. In Batch, choose the seeded batch', async () => {
      await page.getByRole('combobox', { name: 'Batch', exact: true }).selectOption(cohort.id);
    });

    await test.step('4. Click "Choose file" and choose the empty file', async () => {
      await page.locator('input[type="file"]').setInputFiles({
        name,
        mimeType: 'text/csv',
        buffer: Buffer.from('usn,subject_code,sessions_held,sessions_attended\n'),
      });
    });

    await test.step('5. Click Check file', async () => {
      // The screen re-reads the history after a refusal; that answer is what
      // the "Recent imports" grid draws, and it names the file, which the
      // grid does not.
      const reread = page.waitForResponse(
        (response) =>
          response.url().endsWith('/api/admin/imports') && response.request().method() === 'GET',
      );
      await page.getByRole('button', { name: 'Check file' }).click();
      const runs = (await (await reread).json()) as { filename: string | null; status: string }[];
      expect(runs[0]?.filename, 'TC-652 ER-2: the newest run in the history is this file').toBe(
        name,
      );
      expect(runs[0]?.status, 'TC-652 ER-2: and it failed').toBe('failed');
      await expect(
        page.getByRole('alert').filter({ hasText: 'The file has a header row and no data rows.' }),
        'TC-652 ER-1: the file is refused in words',
      ).toBeVisible();
      await expect(
        page.getByText(/\d+ lines? read/),
        'TC-652 ER-1: no counts are shown',
      ).toHaveCount(0);
      await expect(
        page.getByRole('button', { name: 'Import rows' }),
        'TC-652 ER-1: Import rows stays disabled',
      ).toBeDisabled();
      const newest = firstGridRow(page.locator('.history-card'));
      await expect(newest, 'TC-652 ER-2: the refused file is the newest run').toContainText(
        'Could not read',
      );
      await expect(newest, 'TC-652 ER-2: nothing read or written').toContainText(
        '0 read · 0 written',
      );
    });
  });

  // ------------------------------------------------------------------- SWOC --

  test("SWOC notes show the student's four quadrants @TC-660", async ({ page, signIn }) => {
    await signIn('admin');
    const board = await ok<SwocStudent[]>(
      await page.request.get('/api/admin/swoc'),
      'GET /api/admin/swoc',
    );
    const student = await swocStudent(page);
    const search = page.getByRole('searchbox', { name: 'Search students' });
    const pick = page.getByRole('button', { name: new RegExp(`${STUDENT.name} ${STUDENT.usn}`) });

    await test.step('1. Open /admin/swoc', async () => {
      await page.goto('/admin/swoc');
      await expect(
        page.getByRole('heading', { level: 1, name: 'SWOC notes' }),
        'TC-660 ER-1: the heading',
      ).toBeVisible();
      const written = board.filter((row) => row.entries.length > 0).length;
      await expect(
        page.getByText(/\d+ students? · \d+ with notes/),
        'TC-660 ER-1: the students and how many have notes',
      ).toHaveText(`${plural(board.length, 'student')} · ${written} with notes`);
      await expect(
        pick,
        'TC-660 ER-1: Test Student is listed with the number of notes',
      ).toContainText(plural(student.entries.length, 'note'));
    });

    await test.step('2. Type "no such student" in "Name, USN or batch"', async () => {
      await search.fill('no such student');
      await expect(
        page.getByText('No student matches this filter.'),
        'TC-660 ER-2: nobody matches',
      ).toBeVisible();
      await expect(pick, 'TC-660 ER-2: Test Student is not listed').toHaveCount(0);
    });

    await test.step('3. Replace the search with "1BG24MBA001"', async () => {
      await search.fill(STUDENT.usn);
      await expect(pick, 'TC-660 ER-3: Test Student is listed again').toBeVisible();
    });

    await test.step('4. Click Test Student', async () => {
      await pick.click();
      await expect(
        page.getByRole('heading', { level: 2, name: STUDENT.name }),
        'TC-660 ER-4: the editor names the student',
      ).toBeVisible();
      for (const quadrant of Object.values(QUADRANT_OF)) {
        await expect(
          page.getByRole('heading', { level: 3, name: quadrant }),
          `TC-660 ER-4: the ${quadrant} quadrant`,
        ).toBeVisible();
      }
      for (const entry of student.entries) {
        const quadrant = QUADRANT_OF[entry.kind] ?? entry.kind;
        await expect
          .poll(() => swocLineIndex(page, quadrant, entry.text), {
            message: `TC-660 ER-4: "${entry.text}" is shown in ${quadrant}`,
          })
          .toBeGreaterThanOrEqual(0);
      }
    });
  });

  test('Adding a SWOC line shows it to the student @TC-661', async ({ page, signIn }) => {
    const line = `E2E strength ${RUN}: leads the case-study team`;
    await signIn('admin');
    const strengths = page.getByRole('region', { name: 'Strengths' });

    try {
      await test.step('1. Open /admin/swoc', async () => {
        await page.goto('/admin/swoc');
      });

      await test.step('2. Click Test Student', async () => {
        await page
          .getByRole('button', { name: new RegExp(`${STUDENT.name} ${STUDENT.usn}`) })
          .click();
      });

      await test.step('3. Click Add in the Strengths quadrant', async () => {
        await strengths.getByRole('button', { name: 'Add' }).click();
        const box = strengths.getByLabel('New strengths note');
        await expect(box, 'TC-661 ER-1: the "New strengths note" box opens').toBeVisible();
        await expect(box, 'TC-661 ER-1: with the hint').toHaveAttribute(
          'placeholder',
          'Something they do well, and what you saw that shows it.',
        );
        await expect(
          strengths.getByText('0/400'),
          'TC-661 ER-1: the counter starts at 0/400',
        ).toBeVisible();
      });

      await test.step('4. Type the line in "New strengths note"', async () => {
        await strengths.getByLabel('New strengths note').fill(line);
        await expect(
          strengths.getByText(`${line.length}/400`),
          'TC-661 ER-2: the counter counts the line',
        ).toBeVisible();
      });

      await test.step('5. Click Save', async () => {
        await strengths.getByRole('button', { name: 'Save' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: "Added to Test Student's strengths." }),
          'TC-661 ER-3: the office is told the line was added',
        ).toBeVisible();
        const saved = await swocLine(page, 'Strengths', line);
        await expect(saved, 'TC-661 ER-3: written as the placement cell').toContainText(
          'Placement cell',
        );
        await expect(saved, 'TC-661 ER-3: not acknowledged yet').toContainText('Not acknowledged');
        await expect(saved, 'TC-661 ER-3: by the Main Admin').toContainText(ADMIN.name);
      });

      await test.step('6. Sign in as the student and open /student', async () => {
        await signIn('student');
        await page.goto('/student');
        await expect(
          swocTile(page, 'Strength'),
          "TC-661 ER-4: the student's Strength tile includes the line",
        ).toContainText(line);
      });
    } finally {
      await signIn('admin');
      for (const entry of (await swocStudent(page)).entries.filter(
        (candidate) => candidate.text === line,
      )) {
        await removeSwocThroughApi(page, entry.id);
      }
    }
  });

  test('Editing a SWOC line records the change in the edit history @TC-662', async ({
    page,
    signIn,
  }) => {
    const original = `E2E opportunity ${RUN}`;
    const edited = `${original}, edited`;
    await signIn('admin');
    const entry = await addSwocThroughApi(page, 'OPPORTUNITY', original);

    try {
      await test.step('1. Open /admin/swoc', async () => {
        await page.goto('/admin/swoc');
      });

      await test.step('2. Click Test Student', async () => {
        await page
          .getByRole('button', { name: new RegExp(`${STUDENT.name} ${STUDENT.usn}`) })
          .click();
      });

      await test.step("3. Replace the original line's text with the edited line, then click outside the box", async () => {
        const line = await swocLine(page, 'Opportunities', original);
        await line.getByRole('textbox').fill(edited);
        await line.getByRole('textbox').blur();
        await expect(
          page.getByRole('status').filter({ hasText: 'Saved.' }),
          'TC-662 ER-1: the edit is saved',
        ).toBeVisible();
        await expect(
          await swocLine(page, 'Opportunities', edited),
          'TC-662 ER-1: the line says it was edited',
        ).toContainText(' · edited ');
      });

      await test.step("4. Change the line's weight to 5", async () => {
        const line = await swocLine(page, 'Opportunities', edited);
        await line.getByRole('combobox', { name: 'Weight' }).selectOption('5');
        await expect(
          page.getByRole('status').filter({ hasText: 'Saved.' }),
          'TC-662 ER-2: the weight is saved',
        ).toBeVisible();
        await expect
          .poll(
            async () =>
              (await swocStudent(page)).entries.find((candidate) => candidate.id === entry.id)
                ?.weight,
            {
              message: 'TC-662 ER-2: the API stores weight 5',
            },
          )
          .toBe(5);
        await expect(
          (await swocLine(page, 'Opportunities', edited)).getByRole('combobox', { name: 'Weight' }),
          'TC-662 ER-2: the weight reads 5',
        ).toHaveValue('5');
      });

      await test.step('5. Click "Edit history"', async () => {
        await page.getByRole('button', { name: 'Edit history' }).click();
        const panel = page.getByRole('region', { name: 'Edit history' });
        const edits = panel.getByRole('listitem').filter({ hasText: `“${edited}”` });
        await expect(
          edits.filter({ hasText: `${original} → ${edited}` }),
          'TC-662 ER-3: the text edit is recorded, before and after',
        ).toHaveCount(1);
        await expect(
          edits.filter({ hasText: '3 → 5' }),
          'TC-662 ER-3: the weight edit is recorded',
        ).toHaveCount(1);
        await expect(edits.first(), 'TC-662 ER-3: by the Main Admin').toContainText(ADMIN.name);
      });
    } finally {
      await signIn('admin');
      await removeSwocThroughApi(page, entry.id);
    }
  });

  test("Removing a SWOC line takes it off the student's home @TC-663", async ({ page, signIn }) => {
    const text = `E2E challenge ${RUN}`;
    await signIn('admin');
    const entry = await addSwocThroughApi(page, 'CHALLENGE', text);
    await signIn('student');
    expect(
      await studentSwocTexts(page, 'challenges'),
      "TC-663 ER-2 (arrange): the student's home lists the line",
    ).toContain(text);
    await signIn('admin');

    try {
      await test.step('1. Open /admin/swoc', async () => {
        await page.goto('/admin/swoc');
      });

      await test.step('2. Click Test Student', async () => {
        await page
          .getByRole('button', { name: new RegExp(`${STUDENT.name} ${STUDENT.usn}`) })
          .click();
      });

      await test.step('3. Click "Remove this entry" on the line', async () => {
        const line = await swocLine(page, 'Challenges', text);
        await line.getByRole('button', { name: 'Remove this entry' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: 'Removed.' }),
          'TC-663 ER-1: the line is removed',
        ).toBeVisible();
        await expect
          .poll(() => swocLineIndex(page, 'Challenges', text), {
            message: 'TC-663 ER-1: the line is gone from Challenges',
          })
          .toBe(-1);
      });

      await test.step('4. Sign in as the student and open /student', async () => {
        await signIn('student');
        await page.goto('/student');
        await expect(
          swocTile(page, 'Challenge'),
          'TC-663 ER-2 (arrange): the SWOC card is shown',
        ).toBeVisible();
        await expect(
          swocTile(page, 'Challenge'),
          'TC-663 ER-2: the Challenge tile no longer has the line',
        ).not.toContainText(text);
      });
    } finally {
      await signIn('admin');
      await removeSwocThroughApi(page, entry.id);
    }
  });

  test('A SWOC line cannot be emptied @TC-664', async ({ page, signIn }) => {
    const text = `E2E weakness ${RUN}`;
    await signIn('admin');
    const entry = await addSwocThroughApi(page, 'WEAKNESS', text);

    try {
      await test.step('1. Open /admin/swoc', async () => {
        await page.goto('/admin/swoc');
      });

      await test.step('2. Click Test Student', async () => {
        await page
          .getByRole('button', { name: new RegExp(`${STUDENT.name} ${STUDENT.usn}`) })
          .click();
      });

      await test.step("3. Clear the line's text, then click outside the box", async () => {
        const box = (await swocLine(page, 'Weaknesses', text)).getByRole('textbox');
        await box.fill('');
        await box.blur();
        await expect(
          page
            .getByRole('alert')
            .filter({ hasText: 'An entry cannot be blank — use Remove to delete this line.' }),
          'TC-664 ER-1: an empty line is refused in words',
        ).toBeVisible();
        await expect(box, 'TC-664 ER-1: the box shows the line again').toHaveValue(text);
        const stored = (await swocStudent(page)).entries.find(
          (candidate) => candidate.id === entry.id,
        );
        expect(stored?.text, 'TC-664 ER-2: the line is unchanged on the server').toBe(text);
      });
    } finally {
      await signIn('admin');
      await removeSwocThroughApi(page, entry.id);
    }
  });

  // ------------------------------------------------------------ REEP Agent --

  test('Asking the REEP Agent a policy question @TC-670', async ({ page, signIn }) => {
    await signIn('admin');
    await page.request.delete('/api/agent/conversation');
    const log = page.getByRole('log', { name: 'Conversation' });

    await test.step('1. Open /admin/agent', async () => {
      await page.goto('/admin/agent');
      await expect(
        page.getByRole('heading', { level: 1, name: 'REEP Agent' }),
        'TC-670 ER-1: the heading',
      ).toBeVisible();
      await expect(
        page.getByRole('region', { name: 'What the agent can see' }),
        'TC-670 ER-1: the side card names who is signed in',
      ).toContainText(`${ADMIN.name} · Main Admin`);
      await expect(
        log.getByText('How can I help today?'),
        'TC-670 ER-1: the conversation is empty',
      ).toBeVisible();
    });

    await test.step('2. Click the suggested question "How do I verify a skill?"', async () => {
      await log.getByRole('button', { name: 'How do I verify a skill?' }).click();
      await expect(
        log.locator('.ag-bubble--user').filter({ hasText: 'How do I verify a skill?' }),
        'TC-670 ER-2: the question is shown as your message',
      ).toBeVisible();
      await expect(
        log.getByText('Source: Verifying a skill (e.g. Power BI)'),
        'TC-670 ER-2: the answer cites the approved policy',
      ).toBeVisible();
      await expect(
        log,
        'TC-670 ER-2: the answer is about raising a skill claim with proof',
      ).toContainText('raise a skill claim');
    });

    await test.step('3. Click the Helpful button under the answer', async () => {
      const helpful = log.getByRole('button', { name: 'Helpful', exact: true });
      await helpful.click();
      await expect(
        log.getByText('Thanks for the feedback'),
        'TC-670 ER-4: the feedback is thanked',
      ).toBeVisible();
      await expect(helpful, 'TC-670 ER-4: Helpful is marked pressed').toHaveAttribute(
        'aria-pressed',
        'true',
      );
    });

    await test.step('4. Click "Clear conversation"', async () => {
      await page.getByRole('button', { name: 'Clear conversation' }).click();
      await expect(
        log.getByText('How can I help today?'),
        'TC-670 ER-5: the conversation is empty again',
      ).toBeVisible();
      await expect(
        log.getByText('Source: Verifying a skill (e.g. Power BI)'),
        'TC-670 ER-5: the answer is gone',
      ).toHaveCount(0);
    });
  });

  test('The REEP Agent does not give the office personalised student answers @TC-671', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    await page.request.delete('/api/agent/conversation');
    const log = page.getByRole('log', { name: 'Conversation' });
    const send = page.getByRole('button', { name: 'Send' });

    await test.step('1. Open /admin/agent', async () => {
      await page.goto('/admin/agent');
      await expect(
        send,
        'TC-671 ER-1 (arrange): Send is disabled with nothing typed',
      ).toBeDisabled();
    });

    await test.step('2. Type the question in "Message the REEP Agent…"', async () => {
      await page
        .getByRole('textbox', { name: 'Message the REEP Agent' })
        .fill('Am I placement-ready?');
      await expect(send, 'TC-671 ER-1: Send becomes enabled').toBeEnabled();
    });

    await test.step('3. Click Send', async () => {
      await send.click();
      await expect(
        log.getByText(
          'Personalised insights — placement readiness, your next steps, eligible jobs, skills, profile and deadlines — are available on student accounts. I can still answer policy and how-to questions.',
        ),
        'TC-671 ER-2: the office is told personalised answers are for students',
      ).toBeVisible();
      await expect(
        log.getByText('Personalised tools are student-only.'),
        'TC-671 ER-2: the limitation is noted',
      ).toBeVisible();
    });

    await test.step('4. Click "Clear conversation"', async () => {
      await page.getByRole('button', { name: 'Clear conversation' }).click();
      await expect(
        log.getByText('How can I help today?'),
        'TC-671 ER-3: the conversation is empty again',
      ).toBeVisible();
    });
  });

  // ----------------------------------------------------------------- access --

  const SCREENS = [
    '/admin/analytics',
    '/admin/leave-approvals',
    '/admin/jobs',
    '/admin/placement',
    '/admin/exports',
    '/admin/imports',
    '/admin/swoc',
    '/admin/agent',
  ] as const;

  test('A faculty member cannot open the daily-operations screens @TC-680', async ({
    page,
    signIn,
  }) => {
    await signIn('mentor');
    for (const [index, screen] of SCREENS.entries()) {
      await test.step(`${index + 1}. Open ${screen}`, async () => {
        await page.goto(screen);
        await expect(
          page,
          `TC-680 ER-${index + 1}: ${screen} sends the faculty member home`,
        ).toHaveURL(/\/mentor\/notebook$/);
      });
    }
    await test.step('9. Open /api/admin/exports/students.csv', async () => {
      const response = await page.goto('/api/admin/exports/students.csv');
      expect(response?.status(), 'TC-680 ER-9: the API refuses the file').toBe(403);
      await expect(
        page.locator('body'),
        'TC-680 ER-9: and names the missing function',
      ).toContainText(
        "You do not hold the 'Exports' capability. An administrator can grant it in Governance.",
      );
    });
  });

  test('A student cannot open the daily-operations screens @TC-681', async ({ page, signIn }) => {
    await signIn('student');
    for (const [index, screen] of SCREENS.entries()) {
      await test.step(`${index + 1}. Open ${screen}`, async () => {
        await page.goto(screen);
        await expect(page, `TC-681 ER-${index + 1}: ${screen} sends the student home`).toHaveURL(
          /\/student$/,
        );
      });
    }
    await test.step('9. Open /api/admin/placement', async () => {
      const response = await page.goto('/api/admin/placement');
      expect(response?.status(), 'TC-681 ER-9: the API refuses').toBe(403);
      await expect(
        page.locator('body'),
        'TC-681 ER-9: and names the missing function',
      ).toContainText(
        "You do not hold the 'Placement' capability. An administrator can grant it in Governance.",
      );
      await expect(
        page.locator('body'),
        'TC-681 ER-9: no placement figures are returned',
      ).not.toContainText('eligible');
    });
  });
});
