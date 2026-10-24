/**
 * Student: home and progress — the automated twins of the cases in
 * test-management/cases/03-student-progress.md.
 *
 * THE TAG IS THE LINK. Each test's title ends with the `@TC-NNN` ID of its
 * case, its top-level `test.step()` titles are that case's steps word for word,
 * and every assertion names the expected result it checks. The CSV reporter
 * fails the run when the two drift apart. Change a case and its test together.
 *
 * Everything here drives the REAL app as the dev seed's student, with nothing
 * mocked. Where a case needs data the seed does not hold (a batch mate, a
 * hidden student), the test creates it through the same API the app uses, and
 * puts back what it changed when it ends.
 *
 * THE LEDGER'S SUBMIT CANNOT BE UNDONE. Nothing in the product reopens a
 * submitted day, and only today and the two days before it are open. So the
 * ledger tests do not assume which of those days the seed left open (that
 * depends on the day the template database was seeded): each one asks
 * `GET /api/student/ledger/history` which days are open, picks its day by a
 * rule its case states, and restores that day's hours afterwards. TC-228, the
 * one test that submits, takes the OLDEST open day and runs last, so the other
 * ledger tests keep a day to work on for as long as possible. Each run uses one
 * open day; when none is left the ledger tests are Blocked, saying why.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { APIRequestContext, Page, PlaywrightWorkerArgs, TestInfo } from '@playwright/test';
import { ACCOUNTS, block, expect, test, type AccountKey } from './support/reep';

/** A suffix unique to this run, for names the tests create. */
const RUN = Date.now().toString(36);

const FIXTURES = path.join(__dirname, 'fixtures');
const SAMPLE_PDF = path.join(FIXTURES, 'sample.pdf');
const SAMPLE_PNG = path.join(FIXTURES, 'sample.png');

/** The student's batch, as `batch_labels.compose` writes it. */
const BATCH_LABEL = 'Master of Business Administration - Finance · 2024-26 Section B';

type PW = PlaywrightWorkerArgs['playwright'];

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** A GET that must succeed: setup and expected values, never an assertion. */
async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const response = await request.get(url);
  if (!response.ok()) {
    throw new Error(`GET ${url} answered ${response.status()}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

/** A second, independent API session as another seeded account. Signing the
 *  mentor or the Main Admin in here does not touch the student's session. */
async function apiAs(
  playwright: PW,
  baseURL: string | undefined,
  account: AccountKey,
): Promise<APIRequestContext> {
  const context = await playwright.request.newContext({ baseURL });
  const { email, password } = ACCOUNTS[account];
  const response = await context.post('/api/auth/login', { data: { email, password } });
  if (!response.ok()) {
    await context.dispose();
    throw new Error(`${email} could not sign in: ${response.status()}`);
  }
  return context;
}

// ---------------------------------------------------------------------------
// Calendar strings, exactly as the ledger screen prints them. Arithmetic is on
// `YYYY-MM-DD` in UTC, so the machine's own time zone never shifts a day.
// ---------------------------------------------------------------------------

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

function dateParts(iso: string) {
  const [y, m, d] = iso.split('-').map(Number);
  const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return { y, m, d, weekday };
}

function shiftIso(iso: string, days: number): string {
  const { y, m, d } = dateParts(iso);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

/** The date control, Angular's `'EEE · d MMM y'`: "Wed · 23 Sep 2026". */
function controlDate(iso: string): string {
  const { y, m, d, weekday } = dateParts(iso);
  return `${WEEKDAYS[weekday].slice(0, 3)} · ${d} ${MONTHS[m - 1].slice(0, 3)} ${y}`;
}

/** A strip chip's label before the dash, `'EEEE d MMMM'`: "Wednesday 23 September". */
function chipDate(iso: string): string {
  const { m, d, weekday } = dateParts(iso);
  return `${WEEKDAYS[weekday]} ${d} ${MONTHS[m - 1]}`;
}

/** The "Open until" chip, `'EEE d MMM'`: "Fri 25 Sep". */
function shortDate(iso: string): string {
  const { m, d, weekday } = dateParts(iso);
  return `${WEEKDAYS[weekday].slice(0, 3)} ${d} ${MONTHS[m - 1].slice(0, 3)}`;
}

/** The lock sentence's dates, Python's `%d %b %Y`: "05 Sep 2026". */
function lockDate(iso: string): string {
  const { y, m, d } = dateParts(iso);
  return `${String(d).padStart(2, '0')} ${MONTHS[m - 1].slice(0, 3)} ${y}`;
}

// ---------------------------------------------------------------------------
// The Time Allocation Ledger
// ---------------------------------------------------------------------------

/** `settings.ledger_edit_window_days`, the default the cases are written for. */
const EDIT_WINDOW_DAYS = 2;

const SLOTS = [
  { key: 'DAWN', label: '5:00 – 9:00 am', capacity: 4 },
  { key: 'MORNING', label: '9:00 am – 12:00 pm', capacity: 3 },
  { key: 'MIDDAY', label: '12:00 – 3:00 pm', capacity: 3 },
  { key: 'AFTERNOON', label: '3:00 – 6:00 pm', capacity: 3 },
  { key: 'EVENING', label: '6:00 – 10:00 pm', capacity: 4 },
  { key: 'NIGHT', label: '10:00 pm – 5:00 am', capacity: 7 },
] as const;
const ACTIVITIES = [
  { key: 'SLEEPING', label: 'Sleep' },
  { key: 'LEISURE', label: 'Travel / personal' },
  { key: 'LECTURES', label: 'Lectures' },
  { key: 'COURSEWORK', label: 'Coursework' },
  { key: 'SKILLING', label: 'Skilling' },
] as const;
type SlotLabel = (typeof SLOTS)[number]['label'];
type ActivityLabel = (typeof ACTIVITIES)[number]['label'];
type Plan = readonly (readonly [SlotLabel, ActivityLabel, number])[];

/** TC-222's test data: 13.5 hours. */
const DRAFT_DAY: Plan = [
  ['10:00 pm – 5:00 am', 'Sleep', 7],
  ['9:00 am – 12:00 pm', 'Lectures', 3],
  ['12:00 – 3:00 pm', 'Coursework', 2],
  ['3:00 – 6:00 pm', 'Skilling', 1.5],
];
/** TC-224's test data: 23.5 hours, half an hour short. */
const SHORT_DAY: Plan = [
  ['5:00 – 9:00 am', 'Travel / personal', 1],
  ['5:00 – 9:00 am', 'Coursework', 3],
  ['9:00 am – 12:00 pm', 'Lectures', 3],
  ['12:00 – 3:00 pm', 'Lectures', 1],
  ['12:00 – 3:00 pm', 'Coursework', 2],
  ['3:00 – 6:00 pm', 'Skilling', 2.5],
  ['6:00 – 10:00 pm', 'Travel / personal', 3],
  ['6:00 – 10:00 pm', 'Skilling', 1],
  ['10:00 pm – 5:00 am', 'Sleep', 7],
];
/** TC-228's test data: TC-224's day with 3 hours of afternoon Skilling. */
const FULL_DAY: Plan = SHORT_DAY.map(([slot, activity, hours]) =>
  slot === '3:00 – 6:00 pm' && activity === 'Skilling'
    ? ([slot, activity, 3] as const)
    : ([slot, activity, hours] as const),
);
/** TC-225's test data. */
const WEEKLY_DAY: Plan = [['5:00 – 9:00 am', 'Skilling', 4]];

interface HistoryDay {
  day: string;
  status: 'EMPTY' | 'DRAFT' | 'SUBMITTED';
  logged_hours: number;
  editable: boolean;
  locked: boolean;
}
interface LedgerHistory {
  today: string;
  edit_window_days: number;
  days_submitted: number;
  days_logged: number;
  days: HistoryDay[];
}
interface LedgerDay {
  day: string;
  today: string;
  status: 'DRAFT' | 'SUBMITTED';
  editable: boolean;
  total_hours: number;
  slots: { key: string; cells: Record<string, number> }[];
}
interface CellIn {
  slot: string;
  activity: string;
  hours: number;
}

const ledgerHistory = (page: Page) =>
  getJson<LedgerHistory>(page.request, '/api/student/ledger/history?days=14');
const ledgerDay = (page: Page, day: string) =>
  getJson<LedgerDay>(page.request, `/api/student/ledger?day=${day}`);

function cellsOf(ledger: LedgerDay): CellIn[] {
  return ledger.slots.flatMap((slot) =>
    Object.entries(slot.cells)
      .filter(([, hours]) => hours > 0)
      .map(([activity, hours]) => ({ slot: slot.key, activity, hours })),
  );
}

function cellsOfPlan(plan: Plan): CellIn[] {
  return plan.map(([slot, activity, hours]) => ({
    slot: SLOTS.find((s) => s.label === slot)!.key,
    activity: ACTIVITIES.find((a) => a.label === activity)!.key,
    hours,
  }));
}

async function saveDay(page: Page, day: string, cells: CellIn[]): Promise<void> {
  const response = await page.request.put('/api/student/ledger', { data: { day, cells } });
  if (!response.ok()) {
    throw new Error(`saving ${day} answered ${response.status()}: ${await response.text()}`);
  }
}

/** Puts a day's hours back, if the day can still be written. */
async function restoreDay(page: Page, day: string, cells: CellIn[]): Promise<void> {
  if ((await ledgerDay(page, day)).editable) await saveDay(page, day, cells);
}

/** The most recent (or oldest) day in the strip that is neither submitted nor
 *  locked.
 *
 *  SKIPPED, not Blocked, when every open day is already submitted. That is
 *  test data this suite used up (a submitted day cannot be reopened, and only
 *  today and the 2 days before it are open), not a broken environment, so it
 *  must not turn a third run in one day red. A fresh database always has open
 *  days, so a clean run always exercises these cases. */
async function openDay(
  page: Page,
  testInfo: TestInfo,
  which: 'newest' | 'oldest',
): Promise<string> {
  const open = (await ledgerHistory(page)).days.filter((d) => d.editable);
  testInfo.skip(
    open.length === 0,
    'every day in the ledger edit window (today and the 2 days before it) is already submitted, ' +
      'and a submitted day cannot be reopened. Run again tomorrow, or reset the database to a fresh seed.',
  );
  return which === 'newest' ? open[0].day : open[open.length - 1].day;
}

/** What a strip chip says about a day (`historyChip` in ledger.component.ts). */
function chipLabel(d: HistoryDay): string {
  if (d.status === 'SUBMITTED') return 'Submitted';
  if (d.status === 'DRAFT')
    return d.locked ? `${d.logged_hours} h · locked` : `Draft · ${d.logged_hours} h`;
  return d.locked ? 'Locked' : 'Not logged';
}

const dateControl = (page: Page) => page.locator('.stepper-ctrl .lbl');
const previousDay = (page: Page) => page.getByRole('button', { name: 'Previous day' });
const nextDay = (page: Page) => page.getByRole('button', { name: 'Next day' });
/** The header's primary button: "Submit day", "Submitted" or "Locked". */
const headerButton = (page: Page) => page.locator('.dt-toolbar button.btn.primary');
const saveDraft = (page: Page) => page.getByRole('button', { name: 'Save draft' });
const dayChip = (page: Page, iso: string) =>
  page
    .getByRole('list', { name: 'Recent days' })
    .getByRole('listitem', { name: new RegExp(`^${chipDate(iso)} — `) });
const hoursCell = (page: Page, slot: SlotLabel, activity: ActivityLabel) =>
  page.getByRole('spinbutton', { name: `${slot} · ${activity} hours`, exact: true });
const slotRow = (page: Page, slot: SlotLabel) =>
  page.getByRole('row').filter({ has: hoursCell(page, slot, 'Sleep') });
const logged = (page: Page, slot: SlotLabel) => slotRow(page, slot).locator('.logged-val');
const slotState = (page: Page, slot: SlotLabel) => slotRow(page, slot).locator('.slot-chip');
const dayTotal = (page: Page) => page.locator('tr.total-row .logged-val');
const dayState = (page: Page) => page.locator('tr.total-row .slot-chip');
const tile = (page: Page, label: string) => page.locator('.kpi-tile').filter({ hasText: label });
const footChip = (page: Page) => page.locator('.footbar .chip.warn');

/**
 * The ledger's reads are let finish, one at a time, the way a person waits
 * for the table before clicking again. Waiting on the response itself, not on
 * a label the click sets before the request even leaves, is what keeps these
 * tests from typing into a table that has not been drawn yet, and keeps a
 * write from landing on another day.
 */
function dayLoaded(page: Page, day?: string) {
  const suffix = day ? `/api/student/ledger?day=${day}` : '/api/student/ledger';
  return page.waitForResponse((r) => r.url().endsWith(suffix) && r.request().method() === 'GET');
}

/** Opens (or reloads) the ledger and waits for today's table and the strip. */
async function ledgerReady(page: Page, open: () => Promise<unknown>): Promise<void> {
  const loaded = dayLoaded(page);
  await open();
  await loaded;
  await expect(hoursCell(page, '5:00 – 9:00 am', 'Sleep')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Recent days' }).getByRole('listitem')).toHaveCount(
    14,
  );
}

/** Opens a day from the strip and waits until its table has loaded. */
async function openFromStrip(page: Page, day: string): Promise<void> {
  const shown = (await dateControl(page).textContent())?.trim() === controlDate(day);
  const loaded = shown ? null : dayLoaded(page, day);
  await dayChip(page, day).click();
  if (loaded) await loaded;
  await expect(dateControl(page)).toHaveText(controlDate(day));
  await expect(hoursCell(page, '5:00 – 9:00 am', 'Sleep')).toBeVisible();
}

/** Clicks Previous day or Next day and waits for that day's table. */
async function stepTo(page: Page, button: ReturnType<typeof nextDay>, day: string): Promise<void> {
  const loaded = dayLoaded(page, day);
  await button.click();
  await loaded;
}

/** Types a plan into all thirty cells: its hours where it has them, blank elsewhere. */
async function enterPlan(page: Page, plan: Plan): Promise<void> {
  const wanted = new Map(plan.map(([slot, activity, hours]) => [`${slot}|${activity}`, hours]));
  for (const slot of SLOTS) {
    for (const activity of ACTIVITIES) {
      const hours = wanted.get(`${slot.label}|${activity.label}`);
      await hoursCell(page, slot.label, activity.label).fill(
        hours === undefined ? '' : String(hours),
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Skilling
// ---------------------------------------------------------------------------

interface EvidenceRow {
  id: string;
  status: string;
  upload_id: string | null;
}
interface BadgeRow {
  code: string;
  name: string;
  status: string;
  evidence: EvidenceRow[];
}
interface BadgeBoard {
  categories: { key: string; label: string; badges: BadgeRow[] }[];
}

const CLEANUP_NOTE = 'E2E cleanup: rejected by the automated run.';

async function badgeNamed(page: Page, name: string): Promise<BadgeRow> {
  const board = await getJson<BadgeBoard>(page.request, '/api/student/badges');
  const badge = board.categories.flatMap((c) => c.badges).find((b) => b.name === name);
  if (!badge) throw new Error(`no badge named "${name}" in GET /api/student/badges`);
  return badge;
}

/**
 * Clears every claim of the badge that is waiting for the mentor: the mentor
 * rejects it (a rejection needs a note) and the student deletes the
 * certificate behind it, so the badge reads "Not claimed" again and the
 * student's 40-file shelf does not fill up across runs.
 */
async function withdrawPendingClaims(
  page: Page,
  playwright: PW,
  baseURL: string | undefined,
  name: string,
): Promise<void> {
  const pending = (await badgeNamed(page, name)).evidence.filter(
    (e) => e.status === 'PENDING_VERIFICATION',
  );
  if (pending.length === 0) return;
  const mentor = await apiAs(playwright, baseURL, 'mentor');
  try {
    for (const evidence of pending) {
      const review = await mentor.post(`/api/mentor/badge-evidence/${evidence.id}/review`, {
        data: { decision: 'REJECT', note: CLEANUP_NOTE },
      });
      if (!review.ok()) throw new Error(`rejecting claim ${evidence.id}: ${review.status()}`);
      if (evidence.upload_id) {
        const removed = await page.request.delete(`/api/student/uploads/${evidence.upload_id}`);
        if (!removed.ok() && removed.status() !== 404) {
          throw new Error(`deleting upload ${evidence.upload_id}: ${removed.status()}`);
        }
      }
    }
  } finally {
    await mentor.dispose();
  }
}

/** A badge tile, found by its name whatever state it is in. Its accessible
 *  name is the badge's name and then its state, e.g. "Strategic Thinking Not claimed". */
const badgeTile = (page: Page, name: string) =>
  page.getByRole('button', {
    name: new RegExp(`^${escapeRe(name)} (Verified|Waiting for review|Not claimed|Preview)$`),
  });
const illuminated = (page: Page) => page.locator('.board-foot > div').first();
const uploadBox = (page: Page) =>
  page.getByRole('button', { name: /^Click to upload or drop a file/ });
const categorySelect = (page: Page) => page.getByRole('combobox', { name: 'Skill category' });
const badgeSelect = (page: Page) => page.getByRole('combobox', { name: 'Skill badge' });
const submitClaim = (page: Page) => page.getByRole('button', { name: 'Submit claim' });

/** Clicks the upload box and answers the file dialog. */
async function chooseCertificate(
  page: Page,
  file: string | { name: string; mimeType: string; buffer: Buffer },
): Promise<void> {
  const chooser = page.waitForEvent('filechooser');
  await uploadBox(page).click();
  await (await chooser).setFiles(file);
}

// ---------------------------------------------------------------------------
// Leaderboards
// ---------------------------------------------------------------------------

/**
 * Each board is cached per API worker for 60 seconds (`_ranked_board`), so a
 * batch mate approved a moment ago is not on a board read a moment ago. The
 * manual cases wait a minute. The one write that drops the cache is the
 * student's own visibility setting, and setting it to what it already is
 * ("visible") changes nothing else, so the automated tests do that instead of
 * waiting.
 */
async function refreshBoards(page: Page): Promise<void> {
  const response = await page.request.put('/api/student/leaderboard-visibility', {
    data: { hidden: false },
  });
  if (!response.ok()) throw new Error(`PUT leaderboard-visibility: ${response.status()}`);
}

interface Hierarchy {
  colleges: {
    id: string;
    departments: { id: string; batches: { id: string; display_label: string }[] }[];
  }[];
}

interface BatchMate {
  name: string;
  studentId: string;
}

/**
 * Seats a new student in the seeded student's batch, the only way the product
 * mints one: an application (`POST /api/register`, with the CV and photo it
 * requires) that the Main Admin approves. The new student never signs in, so
 * they hold nothing on any board.
 */
async function seatBatchMate(
  page: Page,
  admin: APIRequestContext,
  testInfo: TestInfo,
  tag: string,
): Promise<BatchMate> {
  const hierarchy = await getJson<Hierarchy>(page.request, '/api/register/hierarchy');
  let target: { college: string; department: string; batch: string } | undefined;
  for (const college of hierarchy.colleges) {
    for (const department of college.departments) {
      const batch = department.batches.find((b) => b.display_label === BATCH_LABEL);
      if (batch) target = { college: college.id, department: department.id, batch: batch.id };
    }
  }
  if (!target)
    block(
      testInfo,
      `the batch "${BATCH_LABEL}" from the dev seed is not offered by the registration form.`,
    );
  const id = `${RUN}${tag}`;
  const name = `E2E Mate ${id}`;
  const applied = await admin.post('/api/register', {
    multipart: {
      name,
      email: `e2e-mate-${id}@bgscet.ac.in`,
      usn: `E2E${id}`.toUpperCase(),
      phone: '9876543210',
      personal_email: `e2e-mate-${id}@example.com`,
      linkedin_url: `linkedin.com/in/e2e-mate-${id}`,
      degree_level: 'PG',
      college_id: target.college,
      department_id: target.department,
      requested_cohort_id: target.batch,
      cv: { name: 'cv.pdf', mimeType: 'application/pdf', buffer: fs.readFileSync(SAMPLE_PDF) },
      photo: { name: 'photo.png', mimeType: 'image/png', buffer: fs.readFileSync(SAMPLE_PNG) },
    },
  });
  if (applied.status() === 429) {
    block(
      testInfo,
      'POST /api/register allows 20 applications per 10 minutes from one address. Wait, or restart the API.',
    );
  }
  if (!applied.ok())
    throw new Error(`POST /api/register answered ${applied.status()}: ${await applied.text()}`);
  const registration = (await applied.json()) as { id: string };
  const decided = await admin.post(`/api/register/${registration.id}/decision`, {
    data: { decision: 'APPROVE' },
  });
  if (!decided.ok())
    throw new Error(`approving ${name}: ${decided.status()}: ${await decided.text()}`);
  const { approved_student_id: studentId } = (await decided.json()) as {
    approved_student_id: string | null;
  };
  if (!studentId) throw new Error(`approving ${name} provisioned no student`);
  return { name, studentId };
}

async function removeBatchMate(
  admin: APIRequestContext,
  mate: BatchMate,
  reason: string,
): Promise<void> {
  const response = await admin.post(`/api/admin/students/${mate.studentId}/remove`, {
    data: { reason },
  });
  if (!response.ok()) throw new Error(`removing ${mate.name}: ${response.status()}`);
}

const boardTab = (page: Page, name: string) =>
  page
    .getByRole('tablist', { name: 'Choose a leaderboard' })
    .getByRole('tab', { name, exact: true });
const ownValue = (page: Page) => page.locator('.lb-me-card__val');
const boardNote = (page: Page) => page.getByRole('note');
const unrankedList = (page: Page) => page.locator('.lb-unranked__list');

// ===========================================================================

test.describe('Student: home and progress', () => {
  // --- the landing ---------------------------------------------------------

  test('Student home greets the student with their stage, semester, USN and login streak @TC-200', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    // The streak is counted in days, so its expected length is read, not assumed.
    const streak = await getJson<{ current: number }>(page.request, '/api/student/streak');

    await test.step('1. Open the student home at /student', async () => {
      await page.goto('/student');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Welcome back, Test', exact: true }),
        'TC-200 ER-1: the heading greets the student by first name',
      ).toBeVisible();
      await expect(
        page.locator('.dt-header .dt-sub'),
        'TC-200 ER-2: stage, semester and USN under the greeting',
      ).toHaveText('Excel-Adv stage · Semester 2 · 1BG24MBA001');
      const chip = page.locator('.dt-header .chip.good').filter({ hasText: 'login streak' });
      await expect(chip, 'TC-200 ER-3: the streak chip gives the streak in days').toHaveText(
        new RegExp(`(^|\\s)${streak.current}-day login streak\\s*$`),
      );
    });
  });

  test("The programme stage cards show each module's status @TC-201", async ({ page, signIn }) => {
    await signIn('student');
    const stages: Record<string, [string, string][]> = {
      Reboot: [
        ['REE 101', 'Completed'],
        ['REE 102', 'Completed'],
        ['English Baseline · AI', 'In progress'],
      ],
      Excel: [
        ['PEEP 1', 'Completed'],
        ['PEEP 2', 'In progress'],
        ['VTU 1', 'Completed'],
        ['VTU 2', 'In progress'],
      ],
      Elevate: [
        ['Hippo', 'In progress'],
        ['Mock GDS', 'Not started yet'],
        ['Mock Interview', 'Not started yet'],
        ['Aptitude training', 'Not started yet'],
      ],
    };

    await test.step('1. Open the student home at /student', async () => {
      await page.goto('/student');
      for (const [stage, rows] of Object.entries(stages)) {
        const card = page
          .locator('.grid-3 > .card')
          .filter({ has: page.getByRole('heading', { level: 4, name: stage, exact: true }) });
        await expect(
          card.locator('.link-row .lbl'),
          `TC-201 ER-1: the ${stage} card lists its modules`,
        ).toHaveText(rows.map(([label]) => label));
        for (const [label, status] of rows) {
          const row = card.locator('.link-row').filter({
            has: page.locator('.lbl', { hasText: new RegExp(`^\\s*${escapeRe(label)}\\s*$`) }),
          });
          await expect(
            row.locator('.icon.state'),
            `TC-201 ER-2: the status tooltip of "${label}"`,
          ).toHaveAttribute('title', status);
        }
      }
      await expect(
        page.getByLabel('Status key'),
        'TC-201 ER-3: the status key under the cards',
      ).toHaveText(/Completed\s*.*In progress\s*.*Not started yet/);
      await expect(
        page.locator('.stage-rows a.link-row'),
        'TC-201 ER-4: exactly two rows are links',
      ).toHaveText(['English Baseline · AI', 'Mock Interview'].map((l) => new RegExp(escapeRe(l))));
      await expect(
        page.getByRole('link', { name: 'REE 101' }),
        'TC-201 ER-4: a row without a screen is not a link',
      ).toHaveCount(0);
    });
  });

  test('A stage card row that has its own screen opens it @TC-202', async ({ page, signIn }) => {
    await signIn('student');

    await test.step('1. Open the student home at /student', async () => {
      await page.goto('/student');
    });

    await test.step('2. In the Reboot card, click "English Baseline · AI"', async () => {
      await page.getByRole('link', { name: 'English Baseline · AI' }).click();
      await expect(page, 'TC-202 ER-1: the English baseline screen opens').toHaveURL(
        /\/student\/english$/,
      );
      await expect(
        page.getByRole('heading', { level: 1, name: 'English Proficiency Baseline' }),
        'TC-202 ER-1: headed "English Proficiency Baseline"',
      ).toBeVisible();
    });

    await test.step("3. Go back to the student home with the browser's Back button", async () => {
      await page.goBack();
      await expect(page, 'TC-202 ER-2: back on the student home').toHaveURL(/\/student$/);
      await expect(
        page.getByRole('heading', { level: 1, name: 'Welcome back, Test' }),
        'TC-202 ER-2: the student home is shown',
      ).toBeVisible();
    });

    await test.step('4. In the Elevate card, click "Mock Interview"', async () => {
      await page.getByRole('link', { name: 'Mock Interview', exact: true }).click();
      await expect(page, 'TC-202 ER-3: the mock interviewer opens').toHaveURL(
        /\/student\/assistant$/,
      );
      await expect(
        page.getByRole('heading', { level: 1, name: 'Mock interview', exact: true }),
        'TC-202 ER-3: headed "Mock interview"',
      ).toBeVisible();
    });
  });

  test('The placement readiness card shows the score, the band and every factor @TC-203', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const factors: [string, string, string, string][] = [
      ['CGPA', 'Met', 'good', 'CGPA 8.2 meets the 6.0 cut-off'],
      ['Live backlogs', 'Met', 'good', '0 live backlog(s); limit is 0'],
      ['Attendance', 'Met', 'good', 'Attendance 85.0% vs required 85.0%'],
      [
        'Certification completion',
        'Not met',
        'risk',
        '0.0% of certifications completed vs required 75.0%',
      ],
      ['Placement profile', 'Not met', 'risk', 'Add your phone number and LinkedIn URL'],
      ['Resume profile', 'Not met', 'risk', 'Resume profile 0% complete (target 70%)'],
      [
        'Mock interview',
        'Not measured',
        'neutral',
        'No scored mock interview in the last 90 days — practise one and 60 is the target',
      ],
    ];

    await test.step('1. Open the student home at /student and scroll to the "Placement readiness" card', async () => {
      await page.goto('/student');
      const card = page
        .locator('.card')
        .filter({ has: page.getByRole('heading', { level: 4, name: 'Placement readiness' }) });
      await card.scrollIntoViewIfNeeded();
      await expect(card.locator('.readiness-num'), 'TC-203 ER-1: the score').toHaveText('67');
      await expect(card.locator('.readiness-score .dt-sub'), 'TC-203 ER-1: out of 100').toHaveText(
        '/ 100',
      );
      const band = card.locator('.readiness-band .chip');
      await expect(band, 'TC-203 ER-1: the band').toHaveText(/On track\s*$/);
      await expect(band, 'TC-203 ER-1: the band chip is green').toHaveClass(/\bgood\b/);
      await expect(card.locator('.readiness-band .dt-sub'), 'TC-203 ER-1: the summary').toHaveText(
        '67/100 — On track. 3 of 6 placement checks met. 1 check(s) not measured yet, and are not counted either way.',
      );
      await expect(
        card.locator('.factor-label'),
        'TC-203 ER-2: the seven checks, in order',
      ).toHaveText(factors.map(([label]) => label));
      for (const [label, verdict, tone, detail] of factors) {
        const row = card.locator('.factor-row').filter({
          has: page.locator('.factor-label', { hasText: new RegExp(`^\\s*${label}\\s*$`) }),
        });
        await expect(row.locator('.chip'), `TC-203 ER-2: ${label} is "${verdict}"`).toHaveText(
          new RegExp(`${verdict}\\s*$`),
        );
        await expect(
          row.locator('.factor-text .dt-sub'),
          `TC-203 ER-2: ${label}'s detail`,
        ).toHaveText(detail);
        await expect(row.locator('.chip'), `TC-203 ER-3: ${label}'s chip colour`).toHaveClass(
          new RegExp(`\\b${tone}\\b`),
        );
      }
    });
  });

  test('A recommendation opens the screen that acts on it @TC-204', async ({ page, signIn }) => {
    await signIn('student');
    const card = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { level: 4, name: 'Recommended for you' }) });

    await test.step('1. Open the student home at /student', async () => {
      await page.goto('/student');
      const recommendation = card
        .locator('.reco-row')
        .filter({ hasText: 'Complete your resume profile' });
      await expect(
        recommendation.locator('.reco-title'),
        'TC-204 ER-1: the recommendation',
      ).toHaveText('Complete your resume profile');
      await expect(
        recommendation.locator('.dt-sub'),
        'TC-204 ER-1: why it is recommended',
      ).toHaveText('A fuller resume profile means a stronger auto-generated CV');
      await expect(
        recommendation.getByRole('link', { name: 'Complete', exact: true }),
        'TC-204 ER-1: a Complete button',
      ).toBeVisible();
    });

    await test.step('2. In the "Recommended for you" card, click Complete', async () => {
      await card.getByRole('link', { name: 'Complete', exact: true }).click();
      await expect(page, 'TC-204 ER-2: the Resume Builder opens').toHaveURL(/\/student\/resume$/);
      await expect(
        page.getByRole('heading', { level: 1, name: 'Resume Builder' }),
        'TC-204 ER-2: headed "Resume Builder"',
      ).toBeVisible();
    });
  });

  test("The landing shows the student's SWOC, attendance, marks and academic history @TC-205", async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const swoc: [string, string][] = [
      ['Strength', 'Strong analytical and quantitative skills.'],
      ['Weakness', 'Needs structured problem-solving practice.'],
      ['Opportunity', 'Fintech internships opening this quarter.'],
      ['Challenge', 'Public speaking under time pressure.'],
    ];

    await test.step('1. Open the student home at /student', async () => {
      await page.goto('/student');
      for (const [label, text] of swoc) {
        const swocTile = page
          .locator('.swoc-tile')
          .filter({ has: page.locator('b', { hasText: label }) });
        await expect(swocTile.locator(':scope > div'), `TC-205 ER-1: the ${label} tile`).toHaveText(
          text,
        );
      }
      await expect(
        page.getByRole('img', { name: '22MBA11 90%' }),
        'TC-205 ER-2: 22MBA11 at 90%',
      ).toBeVisible();
      await expect(
        page.getByRole('img', { name: '22MBA12 80%' }),
        'TC-205 ER-2: 22MBA12 at 80%',
      ).toBeVisible();
      await expect(
        page.getByRole('img', { name: 'CGPA by semester: Sem 1 8.2; Sem 2–4 not published yet' }),
        'TC-205 ER-3: semester 1 plotted at 8.2',
      ).toBeVisible();
      await expect(page.locator('.chart-caption'), 'TC-205 ER-3: the chart caption').toHaveText(
        'CGPA out of 10 · Sem 2–4 not published yet',
      );
      const history = page.locator('section.acad');
      await expect(history.locator('.acad-level'), 'TC-205 ER-4: three qualifications').toHaveText([
        '10th Standard',
        '12th Standard',
        'Undergraduate',
      ]);
      await expect(history.locator('.acad-card .chip.year'), 'TC-205 ER-4: the years').toHaveText([
        '2018',
        '2020',
        '2024',
      ]);
      await expect(history.locator('.acad-inst'), 'TC-205 ER-4: the institutions').toHaveText([
        "St. Joseph's High School",
        'Sri Chaitanya PU College',
        'Bangalore University',
      ]);
      await expect(history.locator('.acad-board'), 'TC-205 ER-4: the boards').toHaveText([
        'CBSE',
        'Karnataka PUC',
      ]);
      await expect(history.locator('.acad-pct'), 'TC-205 ER-4: the percentages').toHaveText([
        '88%',
        '82%',
        '72%',
      ]);
      await expect(history.locator('.acad-marks'), 'TC-205 ER-4: the marks').toHaveText([
        '88 / 100',
        '82 / 100',
        '72 / 100',
      ]);
      await expect(history.locator('.acad-meta'), 'TC-205 ER-4: the medium').toHaveText([
        'Medium: English',
        'Medium: English',
      ]);
      await expect(history.locator('.gap-head .chip'), 'TC-205 ER-5: no gaps declared').toHaveText(
        /No gaps declared\s*$/,
      );
      await expect(
        history.locator('.gap-note'),
        'TC-205 ER-5: the timeline is continuous',
      ).toHaveText('Your academic timeline is continuous — no gaps to declare.');
    });
  });

  // --- Skilling --------------------------------------------------------------

  test("The badge board shows the student's verified badges and open claims @TC-210", async ({
    page,
    signIn,
  }) => {
    await signIn('student');

    await test.step('1. Open the Skilling screen at /student/skilling', async () => {
      await page.goto('/student/skilling');
      await expect(page.locator('.board-cat-name'), 'TC-210 ER-1: the five categories').toHaveText([
        'Managerial Skills',
        'Sectoral Skills',
        'Platform / Technical Skills',
        'Thinking Skills',
        'Interview Readiness',
      ]);
      await expect(
        page.locator('.board-cat-note'),
        'TC-210 ER-1: the category captions',
      ).toHaveText([
        '12 badges · 1 earned',
        '16 badges',
        '10 badges · 1 earned',
        '6 badges',
        '4 readiness badges',
      ]);
      for (const name of ['Business Communication', 'Microsoft Excel – Foundation']) {
        await expect(
          badgeTile(page, name),
          `TC-210 ER-2: "${name}" reads Verified`,
        ).toHaveAccessibleName(`${name} Verified`);
        await expect(
          badgeTile(page, name),
          `TC-210 ER-2: "${name}" carries the blue tick`,
        ).toHaveClass(/badge-tile--verified/);
      }
      await expect(
        badgeTile(page, 'Business Analytics Fundamentals'),
        'TC-210 ER-2: the seeded claim reads "Waiting for review"',
      ).toHaveAccessibleName('Business Analytics Fundamentals Waiting for review');
      await expect(
        badgeTile(page, 'Strategic Thinking'),
        'TC-210 ER-2: an unclaimed badge reads "Not claimed"',
      ).toHaveAccessibleName('Strategic Thinking Not claimed');
      await expect(illuminated(page), 'TC-210 ER-3: two skills lit').toHaveText(
        '2 skills currently illuminated',
      );
      await expect(
        page.getByRole('row', {
          name: 'Business Analytics Fundamentals Waiting for review —',
          exact: true,
        }),
        'TC-210 ER-4: the claim waiting for the mentor',
      ).toBeVisible();
      await expect(
        page.getByRole('row', {
          name: "Critical Thinking Needs changes Add the certificate or the organiser's result sheet.",
          exact: true,
        }),
        "TC-210 ER-4: the claim sent back for changes, with the mentor's note",
      ).toBeVisible();
    });
  });

  test('Tapping a badge previews its earned state @TC-211', async ({ page, signIn }) => {
    await signIn('student');
    const badge = badgeTile(page, 'Strategic Thinking');

    await test.step('1. Open the Skilling screen at /student/skilling', async () => {
      await page.goto('/student/skilling');
      await expect(badge, 'TC-211 (arrange): the badge starts unclaimed').toHaveAccessibleName(
        'Strategic Thinking Not claimed',
      );
    });

    await test.step('2. Click the "Strategic Thinking" badge', async () => {
      await badge.click();
      await expect(badge, 'TC-211 ER-1: the badge reads "Preview"').toHaveAccessibleName(
        'Strategic Thinking Preview',
      );
      await expect(badge, 'TC-211 ER-1: the tooltip').toHaveAttribute(
        'title',
        'Preview — tap again to clear',
      );
      await expect(badge, 'TC-211 ER-1: lit, without the verified tick').toHaveClass(
        /badge-tile--preview/,
      );
      await expect(badge, 'TC-211 ER-1: lit, without the verified tick').not.toHaveClass(
        /badge-tile--verified/,
      );
      await expect(illuminated(page), 'TC-211 ER-1: one more skill lit').toHaveText(
        '3 skills currently illuminated',
      );
    });

    await test.step('3. Click the "Strategic Thinking" badge again', async () => {
      await badge.click();
      await expect(badge, 'TC-211 ER-2: the badge reads "Not claimed" again').toHaveAccessibleName(
        'Strategic Thinking Not claimed',
      );
      await expect(illuminated(page), 'TC-211 ER-2: the count returns').toHaveText(
        '2 skills currently illuminated',
      );
    });
  });

  test('The claim form asks for a category before a badge and offers no readiness badges @TC-212', async ({
    page,
    signIn,
  }) => {
    await signIn('student');

    await test.step('1. Open the Skilling screen at /student/skilling', async () => {
      await page.goto('/student/skilling');
      await expect(
        categorySelect(page).locator('option'),
        'TC-212 ER-1: the claimable categories',
      ).toHaveText([
        'Select a category',
        'Managerial Skills',
        'Sectoral Skills',
        'Platform / Technical Skills',
        'Thinking Skills',
      ]);
      await expect(badgeSelect(page), 'TC-212 ER-2: Skill badge is disabled').toBeDisabled();
      await expect(
        badgeSelect(page).locator('option'),
        'TC-212 ER-2: "Pick a category first"',
      ).toHaveText(['Pick a category first']);
      await expect(submitClaim(page), 'TC-212 ER-2: Submit claim is disabled').toBeDisabled();
    });

    await test.step('2. In "Skill category", select Sectoral Skills', async () => {
      await categorySelect(page).selectOption({ label: 'Sectoral Skills' });
      await expect(badgeSelect(page), 'TC-212 ER-3: Skill badge is enabled').toBeEnabled();
      const options = badgeSelect(page).locator('option');
      await expect(options, 'TC-212 ER-3: "Select a badge" and the 16 Sectoral badges').toHaveCount(
        17,
      );
      await expect(options.first(), 'TC-212 ER-3: "Select a badge"').toHaveText('Select a badge');
      await expect(options.nth(1), 'TC-212 ER-3: each badge with its track').toHaveText(
        'Financial Statement Analysis · Finance',
      );
      await expect(options.nth(5), 'TC-212 ER-3: each badge with its track').toHaveText(
        'Talent Acquisition · Human Resources',
      );
    });

    await test.step('3. In "Skill badge", select Financial Statement Analysis · Finance', async () => {
      await badgeSelect(page).selectOption({ label: 'Financial Statement Analysis · Finance' });
      await expect(
        badgeSelect(page).locator('option:checked'),
        'TC-212 (arrange): the badge is chosen',
      ).toHaveText('Financial Statement Analysis · Finance');
      await expect(
        submitClaim(page),
        'TC-212 ER-4: no certificate, so Submit claim stays disabled',
      ).toBeDisabled();
    });

    await test.step('4. Change "Skill category" to Platform / Technical Skills', async () => {
      await categorySelect(page).selectOption({ label: 'Platform / Technical Skills' });
      await expect(
        badgeSelect(page).locator('option:checked'),
        'TC-212 ER-5: the badge is cleared',
      ).toHaveText('Select a badge');
      const options = badgeSelect(page).locator('option');
      await expect(options, 'TC-212 ER-5: the 10 Platform / Technical badges').toHaveCount(11);
      await expect(options.nth(1), 'TC-212 ER-5: from Microsoft Excel – Foundation').toHaveText(
        'Microsoft Excel – Foundation',
      );
      await expect(options.last(), 'TC-212 ER-5: to AI for Analysis & Decision-Making').toHaveText(
        'AI for Analysis & Decision-Making',
      );
    });
  });

  test('Filing a claim with a certificate sends the badge for review @TC-213', async ({
    page,
    signIn,
    playwright,
    baseURL,
  }) => {
    await signIn('student');
    const issuer = `E2E Institute ${RUN}`;
    const note = `E2E claim ${RUN}`;
    // A claim left behind by an interrupted run would make the badge read
    // "Waiting for review" before this one starts.
    await withdrawPendingClaims(page, playwright, baseURL, 'Design Thinking');

    try {
      await test.step('1. Open the Skilling screen at /student/skilling', async () => {
        await page.goto('/student/skilling');
        await expect(
          badgeTile(page, 'Design Thinking'),
          'TC-213 (pre-condition): the badge is not claimed',
        ).toHaveAccessibleName('Design Thinking Not claimed');
      });

      await test.step('2. Click "Click to upload or drop a file" and choose the certificate file', async () => {
        await chooseCertificate(page, SAMPLE_PDF);
        await expect(
          page.locator('.cert-name'),
          'TC-213 ER-1: the file name replaces the box',
        ).toHaveText('sample.pdf');
        await expect(
          page.getByRole('button', { name: 'Replace', exact: true }),
          'TC-213 ER-1: a Replace button',
        ).toBeVisible();
        await expect(submitClaim(page), 'TC-213 ER-1: Submit claim stays disabled').toBeDisabled();
      });

      await test.step('3. In "Skill category", select Thinking Skills', async () => {
        await categorySelect(page).selectOption({ label: 'Thinking Skills' });
      });

      await test.step('4. In "Skill badge", select Design Thinking', async () => {
        await badgeSelect(page).selectOption({ label: 'Design Thinking' });
        await expect(submitClaim(page), 'TC-213 ER-2: Submit claim is enabled').toBeEnabled();
      });

      await test.step('5. Enter the test data in "Issued by" and "Note for the reviewer"', async () => {
        await page.getByRole('textbox', { name: 'Issued by' }).fill(issuer);
        await page.getByRole('textbox', { name: 'Note for the reviewer' }).fill(note);
      });

      await test.step('6. Click Submit claim', async () => {
        await submitClaim(page).click();
        await expect(
          page.locator('.claim-done p'),
          'TC-213 ER-3: the claim is confirmed',
        ).toHaveText(
          'Claim submitted. A faculty reviewer will check the certificate and verify the badge.',
        );
        await expect(
          page.getByRole('button', { name: 'Claim another skill' }),
          'TC-213 ER-3: "Claim another skill"',
        ).toBeVisible();
        await expect(
          badgeTile(page, 'Design Thinking'),
          'TC-213 ER-4: the badge is with the mentor',
        ).toHaveAccessibleName('Design Thinking Waiting for review');
        await expect(
          page.getByRole('row', { name: 'Design Thinking Waiting for review —', exact: true }),
          'TC-213 ER-4: the claim is listed as in progress',
        ).toBeVisible();
      });
    } finally {
      await withdrawPendingClaims(page, playwright, baseURL, 'Design Thinking');
    }
  });

  test('The claim form refuses a certificate of the wrong type or size @TC-214', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const refusal = page.locator('.claim-card').getByRole('alert');

    await test.step('1. Open the Skilling screen at /student/skilling', async () => {
      await page.goto('/student/skilling');
      await expect(uploadBox(page), 'TC-214 (arrange): the upload box is offered').toBeVisible();
    });

    await test.step('2. Click "Click to upload or drop a file" and choose the PNG file', async () => {
      await chooseCertificate(page, SAMPLE_PNG);
      await expect(refusal, 'TC-214 ER-1: a PNG is refused').toHaveText(
        /That file is not a PDF or a JPEG\. Attach the certificate as one of those\.\s*$/,
      );
      await expect(uploadBox(page), 'TC-214 ER-1: the box still invites a file').toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Replace', exact: true }),
        'TC-214 ER-1: no file is attached',
      ).toHaveCount(0);
    });

    await test.step('3. Click "Click to upload or drop a file" and choose the PDF larger than 5 MB', async () => {
      await chooseCertificate(page, {
        name: 'over-5-mb.pdf',
        mimeType: 'application/pdf',
        buffer: Buffer.alloc(5 * 1024 * 1024 + 1),
      });
      await expect(refusal, 'TC-214 ER-2: a file over 5 MB is refused').toHaveText(
        /That file is over 5 MB\. Export a smaller PDF or JPEG and try again\.\s*$/,
      );
      await expect(
        page.getByRole('button', { name: 'Replace', exact: true }),
        'TC-214 ER-2: still no file is attached',
      ).toHaveCount(0);
    });
  });

  // --- the Time Allocation Ledger ---------------------------------------------

  test('The Time Allocation Ledger opens on today with the last 14 days @TC-220', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const history = await ledgerHistory(page);
    const today = await ledgerDay(page, history.today);

    await test.step('1. Open the Time Allocation Ledger at /student/time-log', async () => {
      await ledgerReady(page, () => page.goto('/student/time-log'));
      await expect(
        page.getByRole('heading', { level: 1, name: 'Time Allocation Ledger' }),
        'TC-220 ER-1: the heading',
      ).toBeVisible();
      await expect(page.locator('.ledger-head .eyebrow'), 'TC-220 ER-1: the eyebrow').toHaveText(
        'Daily log · Semester 2',
      );
      await expect(dateControl(page), 'TC-220 ER-1: the date control shows today').toHaveText(
        controlDate(history.today),
      );
      await expect(nextDay(page), 'TC-220 ER-1: Next day is disabled').toBeDisabled();

      const chips = page.getByRole('list', { name: 'Recent days' }).getByRole('listitem');
      await expect(
        chips,
        'TC-220 ER-2: fourteen days, today first, each with its state',
      ).toHaveCount(14);
      for (const [i, d] of history.days.entries()) {
        await expect(chips.nth(i), `TC-220 ER-2: the chip for ${d.day}`).toHaveAttribute(
          'aria-label',
          `${chipDate(d.day)} — ${chipLabel(d)}`,
        );
      }
      await expect(
        chips.first().locator('.day-chip__dow'),
        'TC-220 ER-2: the first chip is today',
      ).toHaveText('Today');
      await expect(page.locator('.history-sum'), 'TC-220 ER-3: the counts').toHaveText(
        `${history.days_submitted} submitted · ${history.days_logged} with entries`,
      );
      await expect(page.locator('.history-rule'), 'TC-220 ER-3: the edit window').toHaveText(
        /Each day can be filled in for 2 days after it ends, then it locks\.\s*$/,
      );

      await expect(page.getByRole('columnheader'), 'TC-220 ER-4: the activity columns').toHaveText([
        'Slot',
        ...ACTIVITIES.map((a) => a.label),
        'Logged',
      ]);
      for (const slot of SLOTS) {
        await expect(
          slotRow(page, slot.label).locator('.slot-name'),
          `TC-220 ER-4: the ${slot.label} row and its capacity`,
        ).toHaveText(new RegExp(`${escapeRe(slot.label)}\\s*${slot.capacity} h capacity\\s*$`));
      }
      await expect(page.locator('.kpi-tile .eyebrow'), 'TC-220 ER-4: the four tiles').toHaveText([
        'Day accounted',
        'Productive',
        'Waking utilisation',
        'Rest',
      ]);
      await expect(
        tile(page, 'Day accounted').locator('.kpi'),
        "TC-220 ER-4: today's logged hours",
      ).toHaveText(String(today.total_hours));
      await expect(
        tile(page, 'Day accounted').locator('.kpi-unit'),
        'TC-220 ER-4: out of 24 h',
      ).toHaveText('/ 24 h');
    });
  });

  test('The day stepper moves one calendar day at a time and stops at today @TC-221', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const { today } = await ledgerHistory(page);
    const yesterday = shiftIso(today, -1);
    const dayBefore = shiftIso(today, -2);

    await test.step('1. Open the Time Allocation Ledger at /student/time-log', async () => {
      await ledgerReady(page, () => page.goto('/student/time-log'));
      await expect(dateControl(page), 'TC-221 (arrange): the ledger opens on today').toHaveText(
        controlDate(today),
      );
    });

    await test.step('2. Click Previous day', async () => {
      await stepTo(page, previousDay(page), yesterday);
      await expect(dateControl(page), 'TC-221 ER-1: one day back, to yesterday').toHaveText(
        controlDate(yesterday),
      );
      await expect(
        dayChip(page, yesterday),
        "TC-221 ER-1: yesterday's chip is highlighted",
      ).toHaveAttribute('aria-current', 'date');
      await expect(nextDay(page), 'TC-221 ER-1: Next day is enabled').toBeEnabled();
    });

    await test.step('3. Click Previous day again', async () => {
      await stepTo(page, previousDay(page), dayBefore);
      await expect(dateControl(page), 'TC-221 ER-2: the day before yesterday').toHaveText(
        controlDate(dayBefore),
      );
      await expect(
        dayChip(page, dayBefore),
        "TC-221 ER-2: that day's chip is highlighted",
      ).toHaveAttribute('aria-current', 'date');
    });

    await test.step('4. Click Next day', async () => {
      await stepTo(page, nextDay(page), yesterday);
      await expect(dateControl(page), 'TC-221 ER-3: yesterday again').toHaveText(
        controlDate(yesterday),
      );
    });

    await test.step('5. Click Next day again', async () => {
      await stepTo(page, nextDay(page), today);
      await expect(dateControl(page), 'TC-221 ER-4: today again').toHaveText(controlDate(today));
      await expect(
        dayChip(page, today),
        'TC-221 ER-4: the "Today" chip is highlighted',
      ).toHaveAttribute('aria-current', 'date');
      await expect(nextDay(page), 'TC-221 ER-4: Next day is disabled at today').toBeDisabled();
    });
  });

  test("Saving a draft keeps the hours and updates the day's totals @TC-222", async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('student');
    const day = await openDay(page, testInfo, 'newest');
    const before = cellsOf(await ledgerDay(page, day));

    try {
      await test.step('1. Open the Time Allocation Ledger at /student/time-log', async () => {
        await ledgerReady(page, () => page.goto('/student/time-log'));
      });

      await test.step('2. In the "Last 14 days" strip, click the most recent day that is neither "Submitted" nor locked', async () => {
        await openFromStrip(page, day);
        await expect(dateControl(page), 'TC-222 ER-1: the date control shows the day').toHaveText(
          controlDate(day),
        );
        await expect(
          dayChip(page, day),
          "TC-222 ER-1: the day's chip is highlighted",
        ).toHaveAttribute('aria-current', 'date');
        await expect(
          page.locator('.open-until'),
          'TC-222 ER-1: open until two days after it',
        ).toHaveText(new RegExp(`Open until ${shortDate(shiftIso(day, EDIT_WINDOW_DAYS))}\\s*$`));
      });

      await test.step('3. Enter the test data hours, leaving every other cell blank', async () => {
        await enterPlan(page, DRAFT_DAY);
        const rows: [SlotLabel, string, string][] = [
          ['10:00 pm – 5:00 am', '7 /7', 'Balanced'],
          ['9:00 am – 12:00 pm', '3 /3', 'Balanced'],
          ['12:00 – 3:00 pm', '2 /3', '1 h open'],
          ['3:00 – 6:00 pm', '1.5 /3', '1.5 h open'],
          ['5:00 – 9:00 am', '0 /4', 'Empty'],
        ];
        for (const [slot, total, state] of rows) {
          await expect(logged(page, slot), `TC-222 ER-2: ${slot} logs ${total}`).toHaveText(total);
          await expect(slotState(page, slot), `TC-222 ER-2: ${slot} reads "${state}"`).toHaveText(
            state,
          );
        }
        await expect(dayTotal(page), 'TC-222 ER-2: the day total').toHaveText('13.5 /24');
        await expect(dayState(page), 'TC-222 ER-2: what is left to reconcile').toHaveText(
          '10.5 h to reconcile',
        );
        await expect(saveDraft(page), 'TC-222 ER-2: Save draft is enabled').toBeEnabled();
      });

      await test.step('4. Click Save draft', async () => {
        await saveDraft(page).click();
        await expect(saveDraft(page), 'TC-222 ER-3: Save draft is disabled again').toBeDisabled();
        await expect(
          tile(page, 'Day accounted').locator('.kpi'),
          'TC-222 ER-3: Day accounted',
        ).toHaveText('13.5');
        await expect(
          tile(page, 'Day accounted').locator('.kpi-sub'),
          'TC-222 ER-3: to reconcile',
        ).toHaveText('10.5 h to reconcile');
        await expect(
          tile(page, 'Productive').locator('.kpi'),
          'TC-222 ER-3: Productive',
        ).toHaveText('6.5');
        await expect(footChip(page), 'TC-222 ER-3: the chip under the table').toHaveText(
          /10\.5 h to reconcile before you can submit\.\s*$/,
        );
        await expect(dayChip(page, day), "TC-222 ER-3: the day's chip").toHaveAttribute(
          'aria-label',
          `${chipDate(day)} — Draft · 13.5 h`,
        );
      });

      await test.step('5. Reload the page and click the same day in the strip again', async () => {
        await ledgerReady(page, () => page.reload());
        await openFromStrip(page, day);
        const kept: [SlotLabel, ActivityLabel, string][] = [
          ['10:00 pm – 5:00 am', 'Sleep', '7'],
          ['9:00 am – 12:00 pm', 'Lectures', '3'],
          ['12:00 – 3:00 pm', 'Coursework', '2'],
          ['3:00 – 6:00 pm', 'Skilling', '1.5'],
        ];
        for (const [slot, activity, value] of kept) {
          await expect(
            hoursCell(page, slot, activity),
            `TC-222 ER-4: ${slot} · ${activity} kept`,
          ).toHaveValue(value);
        }
        await expect(dayTotal(page), 'TC-222 ER-4: the day total kept').toHaveText('13.5 /24');
      });
    } finally {
      await restoreDay(page, day, before);
    }
  });

  test('A slot cannot be saved with more hours than its capacity @TC-223', async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('student');
    const day = await openDay(page, testInfo, 'newest');
    const before = await ledgerDay(page, day);
    const morningBefore = before.slots.find((s) => s.key === 'MORNING')!.cells;
    const morning = '9:00 am – 12:00 pm' as const;

    await test.step('1. Open the Time Allocation Ledger at /student/time-log', async () => {
      await ledgerReady(page, () => page.goto('/student/time-log'));
    });

    await test.step('2. In the "Last 14 days" strip, click the most recent day that is neither "Submitted" nor locked', async () => {
      await openFromStrip(page, day);
    });

    await test.step('3. In the "9:00 am – 12:00 pm" row, enter 5 under Lectures and clear the other four cells', async () => {
      for (const activity of ACTIVITIES) {
        await hoursCell(page, morning, activity.label).fill(
          activity.label === 'Lectures' ? '5' : '',
        );
      }
      await expect(logged(page, morning), 'TC-223 ER-1: the row logs 5 of 3 hours').toHaveText(
        '5 /3',
      );
      await expect(slotState(page, morning), 'TC-223 ER-1: the row is 2 h over').toHaveText(
        '2 h over',
      );
    });

    await test.step('4. Click Save draft', async () => {
      await saveDraft(page).click();
      await expect(
        page.locator('.ledger-notice[role="alert"]'),
        'TC-223 ER-2: the save is refused, naming the slot and the figure',
      ).toHaveText(/9:00 am – 12:00 pm holds 3 h — Lectures cannot be 5 h\.\s*$/);
    });

    await test.step('5. Reload the page and click the same day in the strip again', async () => {
      await ledgerReady(page, () => page.reload());
      await openFromStrip(page, day);
      for (const activity of ACTIVITIES) {
        const hours = morningBefore[activity.key] ?? 0;
        await expect(
          hoursCell(page, morning, activity.label),
          `TC-223 ER-3: ${activity.label} still holds what it had`,
        ).toHaveValue(hours ? String(hours) : '');
      }
    });
  });

  test('A day that does not add up to 24 hours cannot be submitted @TC-224', async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('student');
    const day = await openDay(page, testInfo, 'newest');
    const before = cellsOf(await ledgerDay(page, day));
    const afternoon = '3:00 – 6:00 pm' as const;

    try {
      await test.step('1. Open the Time Allocation Ledger at /student/time-log', async () => {
        await ledgerReady(page, () => page.goto('/student/time-log'));
      });

      await test.step('2. In the "Last 14 days" strip, click the most recent day that is neither "Submitted" nor locked', async () => {
        await openFromStrip(page, day);
      });

      await test.step('3. Enter the test data hours, leaving every other cell blank', async () => {
        await enterPlan(page, SHORT_DAY);
        await expect(dayTotal(page), 'TC-224 ER-1: the day total').toHaveText('23.5 /24');
        await expect(dayState(page), 'TC-224 ER-1: half an hour to reconcile').toHaveText(
          '0.5 h to reconcile',
        );
        await expect(logged(page, afternoon), 'TC-224 ER-1: the afternoon').toHaveText('2.5 /3');
        await expect(slotState(page, afternoon), 'TC-224 ER-1: the afternoon').toHaveText(
          '0.5 h open',
        );
        await expect(headerButton(page), 'TC-224 ER-1: Submit day is disabled').toBeDisabled();
      });

      await test.step('4. Click Save draft', async () => {
        await saveDraft(page).click();
        await expect(
          tile(page, 'Day accounted').locator('.kpi'),
          'TC-224 ER-2: Day accounted',
        ).toHaveText('23.5');
        await expect(
          tile(page, 'Day accounted').locator('.kpi-sub'),
          'TC-224 ER-2: to reconcile',
        ).toHaveText('0.5 h to reconcile');
        await expect(footChip(page), 'TC-224 ER-2: the chip under the table').toHaveText(
          /0\.5 h to reconcile before you can submit\.\s*$/,
        );
        await expect(
          headerButton(page),
          'TC-224 ER-2: Submit day is still disabled',
        ).toBeDisabled();
      });

      await test.step('5. Change the Skilling hours in the "3:00 – 6:00 pm" row from 2.5 to 3', async () => {
        await hoursCell(page, afternoon, 'Skilling').fill('3');
        await expect(dayTotal(page), 'TC-224 ER-3: the day total').toHaveText('24 /24');
        await expect(dayState(page), 'TC-224 ER-3: the day reconciles').toHaveText('Reconciled');
        await expect(logged(page, afternoon), 'TC-224 ER-3: the afternoon').toHaveText('3 /3');
        await expect(slotState(page, afternoon), 'TC-224 ER-3: the afternoon').toHaveText(
          'Balanced',
        );
        await expect(headerButton(page), 'TC-224 ER-3: Submit day is enabled').toBeEnabled();
        await expect(headerButton(page), 'TC-224 ER-3: Submit day is enabled').toHaveText(
          /Submit day\s*$/,
        );
      });
    } finally {
      await restoreDay(page, day, before);
    }
  });

  test('The weekly skilling strip adds up the Skilling hours logged this week @TC-225', async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('student');
    const day = await openDay(page, testInfo, 'newest');
    const before = cellsOf(await ledgerDay(page, day));
    const week = await getJson<{
      skilling_hours: number;
      weekly_hour_target: number;
      entries: { day: string; activity: string; minutes: number }[];
    }>(page.request, '/api/student/timesheet?days=7');
    const skillingMinutes = (onDay?: string) =>
      week.entries
        .filter((e) => e.activity === 'SKILLING' && (onDay === undefined || e.day === onDay))
        .reduce((sum, e) => sum + e.minutes, 0);
    const percent = (hours: number) =>
      `${Math.min(100, Math.round((hours / week.weekly_hour_target) * 100))}%`;
    const after = (skillingMinutes() - skillingMinutes(day) + 4 * 60) / 60;
    const strip = page.locator('.weekly-card .dt-sub');
    const stripChip = page.locator('.weekly-row > .chip');

    try {
      await test.step('1. Open the Time Allocation Ledger at /student/time-log and note the hours in "Skilling this week"', async () => {
        await ledgerReady(page, () => page.goto('/student/time-log'));
        await expect(strip, "TC-225 ER-1: the week's Skilling hours against the target").toHaveText(
          `· ${week.skilling_hours} h of a ${week.weekly_hour_target} h target`,
        );
        await expect(stripChip, 'TC-225 ER-1: as a percentage').toHaveText(
          percent(week.skilling_hours),
        );
      });

      await test.step('2. In the "Last 14 days" strip, click the most recent day that is neither "Submitted" nor locked', async () => {
        await openFromStrip(page, day);
      });

      await test.step('3. Enter the test data hours, leaving every other cell blank', async () => {
        await enterPlan(page, WEEKLY_DAY);
      });

      await test.step('4. Click Save draft', async () => {
        await saveDraft(page).click();
        await expect(strip, "TC-225 ER-2: the day's previous Skilling out, 4 hours in").toHaveText(
          `· ${after} h of a ${week.weekly_hour_target} h target`,
        );
        await expect(stripChip, 'TC-225 ER-2: the new percentage').toHaveText(percent(after));
      });
    } finally {
      await restoreDay(page, day, before);
    }
  });

  test('A day past its edit window is locked @TC-226', async ({ page, signIn }, testInfo) => {
    await signIn('student');
    const history = await ledgerHistory(page);
    const lockedDay = history.days.find((d) => d.locked && d.status !== 'SUBMITTED');
    if (!lockedDay) {
      block(testInfo, 'the last 14 days hold no locked day that was never submitted.');
    }
    const day = lockedDay.day;
    // The contrast for ER-2's "no Save draft": an open day has the button. Only
    // an OPEN today can show it; once earlier runs have submitted today there
    // is no contrast to draw, and the lock sentence, the "Locked" button and
    // the thirty disabled cells below still identify a locked day on their own.
    const todayIsOpen = history.days.find((d) => d.day === history.today)?.editable === true;

    await test.step('1. Open the Time Allocation Ledger at /student/time-log', async () => {
      await ledgerReady(page, () => page.goto('/student/time-log'));
      if (todayIsOpen) {
        await expect(
          saveDraft(page),
          'TC-226 (arrange): today is open, with Save draft',
        ).toBeVisible();
      }
    });

    await test.step('2. In the "Last 14 days" strip, click the most recent locked day that was not submitted', async () => {
      await openFromStrip(page, day);
      await expect(
        page.locator('.footbar .chip.risk'),
        'TC-226 ER-1: the lock sentence',
      ).toHaveText(
        new RegExp(
          escapeRe(
            `${lockDate(day)} is locked — it could be filled in until ${lockDate(shiftIso(day, EDIT_WINDOW_DAYS))}. ` +
              'A day stays open for 2 days after it ends.',
          ) + '\\s*$',
        ),
      );
      await expect(headerButton(page), 'TC-226 ER-2: the button reads "Locked"').toHaveText(
        /Locked\s*$/,
      );
      await expect(headerButton(page), 'TC-226 ER-2: and is disabled').toBeDisabled();
      const cells = page.getByRole('spinbutton');
      await expect(cells, 'TC-226 ER-2: thirty hour cells').toHaveCount(30);
      for (const cell of await cells.all()) {
        await expect(cell, 'TC-226 ER-2: every hour cell is disabled').toBeDisabled();
      }
      await expect(saveDraft(page), 'TC-226 ER-2: there is no Save draft').toHaveCount(0);
    });
  });

  test('Copy yesterday fills an open day from the previous submitted day @TC-227', async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('student');
    const history = await ledgerHistory(page);
    const status = new Map(history.days.map((d) => [d.day, d]));
    let target = history.days.find(
      (d) => d.editable && status.get(shiftIso(d.day, -1))?.status === 'SUBMITTED',
    );
    if (!target) {
      // No open day follows a submitted one: submit the oldest open day whose
      // next day is open too, as the pre-condition allows.
      const source = [...history.days]
        .reverse()
        .find((d) => d.editable && status.get(shiftIso(d.day, 1))?.editable);
      testInfo.skip(
        !source,
        'no open ledger day follows another day that could be submitted (earlier runs used them up). ' +
          'Run again tomorrow, or reset the database to a fresh seed.',
      );
      if (!source) return;
      await saveDay(page, source.day, cellsOfPlan(FULL_DAY));
      const submitted = await page.request.post('/api/student/ledger/submit', {
        data: { day: source.day },
      });
      if (!submitted.ok()) throw new Error(`submitting ${source.day}: ${submitted.status()}`);
      target = status.get(shiftIso(source.day, 1))!;
    }
    const day = target.day;
    const before = cellsOf(await ledgerDay(page, day));

    try {
      await test.step('1. Open the Time Allocation Ledger at /student/time-log', async () => {
        await ledgerReady(page, () => page.goto('/student/time-log'));
      });

      await test.step('2. In the "Last 14 days" strip, click the most recent open day whose previous day reads "Submitted"', async () => {
        await openFromStrip(page, day);
      });

      await test.step('3. Click Copy yesterday', async () => {
        const copy = page.getByRole('button', { name: 'Copy yesterday' });
        await expect(copy, 'TC-227 ER-1: a Copy yesterday button is offered').toBeVisible();
        await copy.click();
        await expect(
          dayTotal(page),
          "TC-227 ER-1: the previous day's 24 hours are copied",
        ).toHaveText('24 /24');
        await expect(dayState(page), 'TC-227 ER-1: the day reconciles').toHaveText('Reconciled');
        await expect(headerButton(page), 'TC-227 ER-1: Submit day is enabled').toBeEnabled();
        await expect(
          dayChip(page, day),
          'TC-227 ER-1: the copy is a draft, not submitted',
        ).toHaveAttribute('aria-label', `${chipDate(day)} — Draft · 24 h`);
      });
    } finally {
      await restoreDay(page, day, before);
    }
  });

  test('Submitting a day that adds up to 24 hours closes it @TC-228', async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('student');
    const day = await openDay(page, testInfo, 'oldest');
    const submittedBefore = (await ledgerHistory(page)).days_submitted;

    await test.step('1. Open the Time Allocation Ledger at /student/time-log', async () => {
      await ledgerReady(page, () => page.goto('/student/time-log'));
    });

    await test.step('2. In the "Last 14 days" strip, click the oldest day that is neither "Submitted" nor locked', async () => {
      await openFromStrip(page, day);
      await expect(dateControl(page), 'TC-228 ER-1: the date control shows the day').toHaveText(
        controlDate(day),
      );
      await expect(
        dayChip(page, day),
        "TC-228 ER-1: the day's chip is highlighted",
      ).toHaveAttribute('aria-current', 'date');
    });

    await test.step('3. Enter the test data hours, leaving every other cell blank', async () => {
      await enterPlan(page, FULL_DAY);
      await expect(dayTotal(page), 'TC-228 ER-2: the day total').toHaveText('24 /24');
      await expect(dayState(page), 'TC-228 ER-2: the day reconciles').toHaveText('Reconciled');
      await expect(
        page.locator('tbody .slot-chip'),
        'TC-228 ER-2: every slot is balanced',
      ).toHaveText(SLOTS.map(() => 'Balanced'));
      await expect(headerButton(page), 'TC-228 ER-2: Submit day is enabled').toBeEnabled();
    });

    await test.step('4. Click Submit day', async () => {
      await headerButton(page).click();
      await expect(headerButton(page), 'TC-228 ER-3: the button reads "Submitted"').toHaveText(
        /Submitted\s*$/,
      );
      await expect(headerButton(page), 'TC-228 ER-3: and is disabled').toBeDisabled();
      await expect(
        page.locator('.footbar .chip.good'),
        'TC-228 ER-3: the day is closed',
      ).toHaveText(/Submitted — this day is closed\s*$/);
      for (const cell of await page.getByRole('spinbutton').all()) {
        await expect(cell, 'TC-228 ER-3: every hour cell is disabled').toBeDisabled();
      }
      await expect(saveDraft(page), 'TC-228 ER-3: Save draft is gone').toHaveCount(0);
      await expect(
        dayChip(page, day),
        "TC-228 ER-4: the day's chip reads Submitted",
      ).toHaveAttribute('aria-label', `${chipDate(day)} — Submitted`);
      await expect(page.locator('.history-sum'), 'TC-228 ER-4: one more submitted day').toHaveText(
        new RegExp(`^${submittedBefore + 1} submitted · \\d+ with entries\\s*$`),
      );
    });
  });

  // --- Courses and Records -----------------------------------------------------

  test('The Courses screen shows progress on each enrolled course @TC-230', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const courses: {
      name: string;
      code: string;
      pct: string;
      next: string;
      lectures: string;
      left: string;
    }[] = [
      {
        name: 'Management & Organisational Behaviour',
        code: '22MBA11 · Sem 1 · Excel',
        pct: '90',
        next: 'Next: Attend lecture 19 of 20',
        lectures: '18/20 lectures',
        left: '2 lectures left',
      },
      {
        name: 'Managerial Economics',
        code: '22MBA12 · Sem 1 · Excel',
        pct: '80',
        next: 'Next: Attend lecture 17 of 20',
        lectures: '16/20 lectures',
        left: '4 lectures left',
      },
    ];

    await test.step('1. Open the Records screen at /student/records', async () => {
      await page.goto('/student/records');
    });

    await test.step('2. Click "Subject-by-subject progress"', async () => {
      await page.getByRole('link', { name: 'Subject-by-subject progress' }).click();
      await expect(page, 'TC-230 ER-1: the Courses screen opens').toHaveURL(/\/student\/courses$/);
      await expect(
        page.getByRole('heading', { level: 1, name: 'Courses' }),
        'TC-230 ER-1: the heading',
      ).toBeVisible();
      await expect(page.locator('.dt-header .dt-sub'), 'TC-230 ER-1: the counts').toHaveText(
        '2 enrolled · 2 in progress · 0 completed',
      );
      for (const [i, course] of courses.entries()) {
        const er = `TC-230 ER-${i + 2}`;
        const card = page
          .locator('.plan-card')
          .filter({ has: page.locator('.c-name', { hasText: course.name }) });
        await expect(card.locator('.c-code'), `${er}: code, semester and stage`).toHaveText(
          course.code,
        );
        await expect(card.locator('.plan-top .chip'), `${er}: in progress`).toHaveText(
          /In progress\s*$/,
        );
        await expect(card.locator('.plan-pct'), `${er}: the percentage`).toHaveText(
          `${course.pct}% complete`,
        );
        await expect(card.getByRole('progressbar'), `${er}: the progress bar`).toHaveAttribute(
          'aria-valuenow',
          course.pct,
        );
        await expect(card.locator('.plan-next'), `${er}: the next task`).toHaveText(
          new RegExp(`${escapeRe(course.next)}\\s*$`),
        );
        await expect(card.locator('.fact'), `${er}: lectures attended and left`).toHaveText([
          new RegExp(`${course.lectures}\\s*$`),
          new RegExp(`${course.left}\\s*$`),
        ]);
        await expect(card.locator('.unlocks'), `${er}: what it unlocks`).toHaveText(
          /Unlocks: Progresses your Excel stage\s*$/,
        );
        await expect(
          card.getByRole('link', { name: `Continue ${course.name}` }),
          `${er}: Continue`,
        ).toBeVisible();
      }
    });
  });

  test("Records show the student's VTU results, attendance and academic history @TC-240", async ({
    page,
    signIn,
  }) => {
    await signIn('student');

    await test.step('1. Open the Records screen at /student/records', async () => {
      await page.goto('/student/records');
      await expect(page.locator('.rec-stats .lbl'), 'TC-240 ER-1: the four figures').toHaveText([
        'Latest CGPA',
        'Semesters on record',
        'Live backlogs',
        'Overall attendance',
      ]);
      await expect(page.locator('.rec-stats .val'), 'TC-240 ER-1: their values').toHaveText([
        '8.2',
        '1',
        '0',
        '85%',
      ]);
      const semester = page.locator('.sem-card');
      await expect(semester.locator('.sem-name'), 'TC-240 ER-2: one semester').toHaveText([
        'Semester 1',
      ]);
      await expect(semester.locator('.sem-meta'), 'TC-240 ER-2: CGPA, SGPA and class').toHaveText(
        'CGPA 8.2 · SGPA 8.2 · FIRST CLASS WITH DISTINCTION',
      );
      await expect(semester.locator('.sem-top .chip'), 'TC-240 ER-2: no live backlogs').toHaveText(
        /No live backlogs\s*$/,
      );
      await expect(semester.locator('tbody tr'), 'TC-240 ER-2: the subject marks').toHaveText([
        /^22MBA11\s*Management & Organisational Behaviour\s*4\s*42\s*40\s*82\s*.*Pass\s*$/,
        /^22MBA12\s*Managerial Economics\s*4\s*38\s*36\s*74\s*.*Pass\s*$/,
      ]);
      await expect(page.locator('.att-big'), 'TC-240 ER-3: overall attendance').toHaveText('85%');
      await expect(page.locator('.att-fine'), 'TC-240 ER-3: classes attended').toHaveText(
        '34 of 40 classes attended',
      );
      await expect(page.locator('.att-overall-head .chip'), 'TC-240 ER-3: on track').toHaveText(
        /85% · On track\s*$/,
      );
      await expect(page.locator('.att-course-code'), 'TC-240 ER-3: by course').toHaveText([
        '22MBA11',
        '22MBA12',
      ]);
      await expect(page.locator('.att-course-fig'), 'TC-240 ER-3: classes per course').toHaveText([
        '18/20',
        '16/20',
      ]);
      await expect(
        page.locator('.att-course-chip'),
        'TC-240 ER-3: percentage per course',
      ).toHaveText([/90%\s*$/, /80%\s*$/]);
      await expect(page.locator('.qual-level'), 'TC-240 ER-4: the three qualifications').toHaveText(
        ['10th Standard', '12th Standard', 'Undergraduate'],
      );
      await expect(page.locator('.gap-card .chip'), 'TC-240 ER-4: no gaps declared').toHaveText(
        /No gaps declared\s*$/,
      );
    });
  });

  // --- Leaderboards --------------------------------------------------------------

  test("Leaderboards open on the Overall board, ranked within the student's batch @TC-250", async ({
    page,
    signIn,
  }) => {
    await signIn('student');

    await test.step('1. Open the Leaderboards screen at /student/leaderboards', async () => {
      await page.goto('/student/leaderboards');
      await expect(
        page.locator('.dt-header .dt-sub'),
        'TC-250 ER-1: ranked within the batch',
      ).toHaveText(
        new RegExp(
          `Ranked within your batch · ${escapeRe(BATCH_LABEL)} — everyone in it sees the same names, positions and totals\\.\\s*$`,
        ),
      );
      await expect(
        page.getByRole('tablist', { name: 'Choose a leaderboard' }).getByRole('tab'),
        'TC-250 ER-2: the five boards',
      ).toHaveText(['Overall', 'Skills', 'VTU results', 'Streak', 'Mocks taken']);
      await expect(boardTab(page, 'Overall'), 'TC-250 ER-2: Overall is selected').toHaveAttribute(
        'aria-selected',
        'true',
      );
      await expect(
        page.locator('.lb-me-card__headline'),
        "TC-250 ER-3: the student's rank",
      ).toHaveText('You’re Rank 1 of 1');
      await expect(page.locator('.lb-me-card__hint'), 'TC-250 ER-3: the encouragement').toHaveText(
        'You’re the only ranked student here right now — a strong start.',
      );
      await expect(ownValue(page), 'TC-250 ER-3: the total').toHaveText('100 pts');
      await expect(boardNote(page), 'TC-250 ER-4: how the board is scored').toHaveText(
        /Ranked by skills, VTU results, streak and mocks together — each is worth up to 25 points, scaled against the best in your batch, and the four add up to 100\. Only classmates with a record here are ranked; equal totals share a rank\. Updates as records change\.\s*$/,
      );
      await expect(page.locator('.lb-table tbody tr'), 'TC-250 ER-5: one ranked row').toHaveCount(
        1,
      );
      await expect(
        page.getByRole('row', { name: '1 Test Student You of 1 100 pts', exact: true }),
        'TC-250 ER-5: the student, marked You',
      ).toBeVisible();
    });
  });

  test('Each leaderboard tab ranks the student by its own measure @TC-251', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const { days_active: activeDays } = await getJson<{ days_active: number }>(
      page.request,
      '/api/student/streak',
    );
    // The boards are cached for 60 s; a sign-in on a new day must show.
    await refreshBoards(page);
    const boards: [number, string, string, string][] = [
      [2, 'Skills', '2 skills', 'Ranked by skills verified on Skilling.'],
      [3, 'VTU results', 'CGPA 8.20', 'Ranked by your latest recorded CGPA.'],
      [
        4,
        'Streak',
        `${activeDays} ${activeDays === 1 ? 'active day' : 'active days'}`,
        'Ranked by active-day count.',
      ],
      [5, 'Mocks taken', '2 mocks', 'Ranked by mocks completed.'],
    ];

    await test.step('1. Open the Leaderboards screen at /student/leaderboards', async () => {
      await page.goto('/student/leaderboards');
      await expect(ownValue(page), 'TC-251 (arrange): the Overall board has loaded').toHaveText(
        '100 pts',
      );
    });

    for (const [step, tab, total, note] of boards) {
      await test.step(`${step}. Click the ${tab} tab`, async () => {
        await boardTab(page, tab).click();
        const er = `TC-251 ER-${step - 1}`;
        await expect(boardTab(page, tab), `${er}: the ${tab} tab is selected`).toHaveAttribute(
          'aria-selected',
          'true',
        );
        await expect(ownValue(page), `${er}: the student's ${tab} total`).toHaveText(total);
        await expect(boardNote(page), `${er}: how ${tab} is scored`).toHaveText(
          new RegExp(`${escapeRe(note)} `),
        );
      });
    }
  });

  test('A batch mate with no record is listed as not ranked @TC-252', async ({
    page,
    signIn,
    playwright,
    baseURL,
  }, testInfo) => {
    await signIn('student');
    const admin = await apiAs(playwright, baseURL, 'admin');
    let mate: BatchMate | undefined;
    try {
      mate = await seatBatchMate(page, admin, testInfo, 'a');
      await refreshBoards(page);
      const { name } = mate;

      await test.step('1. Open the Leaderboards screen at /student/leaderboards', async () => {
        await page.goto('/student/leaderboards');
        await expect(ownValue(page), 'TC-252 (arrange): the Overall board has loaded').toHaveText(
          '100 pts',
        );
      });

      await test.step('2. Click the Skills tab', async () => {
        await boardTab(page, 'Skills').click();
        await expect(ownValue(page), 'TC-252 (arrange): the Skills board has loaded').toHaveText(
          '2 skills',
        );
        await expect(
          page.locator('.lb-unranked__title'),
          'TC-252 ER-1: the "Not ranked on this board yet" card',
        ).toHaveText(/Not ranked on this board yet\s*$/);
        await expect(
          unrankedList(page).getByRole('listitem').filter({ hasText: name }),
          'TC-252 ER-1: the batch mate is listed by name',
        ).toHaveText(new RegExp(`${escapeRe(name)}\\s*$`));
        await expect(
          page.locator('.lb-unranked .dt-sub'),
          'TC-252 ER-1: what puts them on the board',
        ).toHaveText(
          /^\s*\d+ of \d+ classmates · Get a skill verified on Skilling and you’ll appear on this board\.\s*$/,
        );
        await expect(
          page.getByRole('row', { name: '1 Test Student You of 1 2 skills', exact: true }),
          'TC-252 ER-2: the student is ranked on skills',
        ).toBeVisible();
        await expect(
          page.locator('.lb-table'),
          'TC-252 ER-2: the batch mate is not ranked',
        ).not.toContainText(name);
      });
    } finally {
      if (mate) await removeBatchMate(admin, mate, 'E2E: TC-252 is done with this batch mate');
      await admin.dispose();
    }
  });

  test('A removed batch mate leaves the leaderboard @TC-253', async ({
    page,
    signIn,
    playwright,
    baseURL,
  }, testInfo) => {
    await signIn('student');
    const admin = await apiAs(playwright, baseURL, 'admin');
    let mate: BatchMate | undefined;
    let removed = false;
    try {
      mate = await seatBatchMate(page, admin, testInfo, 'b');
      const { name } = mate;
      await refreshBoards(page);

      await test.step('1. Open the Leaderboards screen at /student/leaderboards', async () => {
        await page.goto('/student/leaderboards');
        await expect(
          unrankedList(page).getByRole('listitem').filter({ hasText: name }),
          'TC-253 ER-1: the batch mate is listed as not ranked',
        ).toBeVisible();
      });

      await test.step('2. As the Main Admin, remove the batch mate from the roster with the reason in the test data', async () => {
        await removeBatchMate(admin, mate!, 'E2E: removed to check the leaderboard');
        removed = true;
      });

      await test.step('3. Wait a minute, then reload the leaderboard', async () => {
        await refreshBoards(page);
        await page.reload();
        await expect(ownValue(page), 'TC-253 (arrange): the board has reloaded').toHaveText(
          '100 pts',
        );
        await expect(
          page.getByText(name),
          'TC-253 ER-2: the removed batch mate is not listed',
        ).toHaveCount(0);
      });
    } finally {
      if (mate && !removed) await removeBatchMate(admin, mate, 'E2E: TC-253 stopped early');
      await admin.dispose();
    }
  });

  test('A student hidden from the leaderboards can take part again @TC-254', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const hidden = await page.request.put('/api/student/leaderboard-visibility', {
      data: { hidden: true },
    });
    if (!hidden.ok()) throw new Error(`hiding the student: ${hidden.status()}`);

    try {
      await test.step('1. Open the Leaderboards screen at /student/leaderboards', async () => {
        await page.goto('/student/leaderboards');
        await expect(
          page.locator('.lb-empty__title'),
          'TC-254 ER-1: the student is hidden',
        ).toHaveText('You’re hidden from the leaderboards');
        await expect(
          page.getByText(
            'You don’t appear on any board and, in turn, you don’t see peer rankings.',
          ),
          'TC-254 ER-1: what hiding means',
        ).toBeVisible();
        await expect(
          page.getByText(
            'Your mentors and placement staff can always see your records — the opt-out only hides you from classmates on these boards.',
          ),
          'TC-254 ER-1: the privacy note',
        ).toBeVisible();
        await expect(page.locator('.lb-table'), 'TC-254 ER-1: no ranking is shown').toHaveCount(0);
      });

      await test.step('2. Click Take part again', async () => {
        await page.getByRole('button', { name: 'Take part again' }).click();
        await expect(
          page.locator('.lb-note--feedback[role="status"]'),
          'TC-254 ER-2: the student is visible again',
        ).toHaveText(/You’re now visible on the leaderboards\.\s*$/);
        await expect(
          page.getByRole('row', { name: /^1 Test Student You of \d+ / }),
          'TC-254 ER-2: the Overall board ranks the student again',
        ).toBeVisible();
      });
    } finally {
      await refreshBoards(page);
    }
  });
});
