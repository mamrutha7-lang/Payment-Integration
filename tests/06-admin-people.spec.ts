/**
 * Admin: people and access — the automated twins of the cases in
 * test-management/cases/06-admin-people.md.
 *
 * THE TAG IS THE LINK. Each test's title ends with the `@TC-NNN` ID of the case
 * it automates, its top-level `test.step()` titles are that case's steps word
 * for word, and every assertion message names the expected result it checks.
 * The CSV reporter fails the run when the two drift apart.
 *
 * WHAT THESE TESTS CREATE, AND WHY IT IS SAFE TO RUN THEM TWICE. A case that
 * needs a faculty member or a student of its own makes one through the API
 * first (a faculty account through `POST /api/admin/faculty`, a student through
 * the public registration form and an approval, which is the only way a student
 * account comes into existence), with a name unique to the run, and REMOVES it
 * again at the end: remove is the console's own "off every list, record kept",
 * and delete-for-good needs a code emailed to the office that no test may read.
 * So every run leaves its throwaway accounts on the Removed lists and nothing
 * else. A case that changes the seeded student or the seeded mentor pairing
 * puts it back, in the UI where that is the case's own last step and through
 * the API in a `finally` otherwise, so a failure half-way does not leave the
 * next case looking at a different roster.
 *
 * THE MAIN ADMIN HOLDS ONE SESSION. Every admin API call here goes through
 * `page.request`, which shares the page's cookie jar: signing the admin in on
 * a second request context would retire the page's own session. A faculty
 * member's browser is always a separate context.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import { ACCOUNTS, expect, test } from './support/reep';
import type {
  APIRequestContext,
  APIResponse,
  Browser,
  BrowserContext,
  Locator,
  Page,
  PlaywrightWorkerArgs,
} from '@playwright/test';

// ------------------------------------------------------------ test data --

/** The dev seed's student, batch and faculty member (apps/api-py/app/seed.py). */
const SEED = {
  student: {
    name: ACCOUNTS.student.name,
    email: ACCOUNTS.student.email,
    usn: '1BG24MBA001',
    stageLabel: 'Excel-Adv',
    stageKey: 'EXCEL_ADVANCED',
    semester: 2,
    city: 'Bengaluru',
    summary: 'MBA finance candidate seeking placement.',
  },
  mentor: { name: ACCOUNTS.mentor.name, email: ACCOUNTS.mentor.email },
  admin: { name: ACCOUNTS.admin.name, email: ACCOUNTS.admin.email },
  /** `batch_labels.compose` of the seeded cohort: course - specialization · name. */
  batch: 'Master of Business Administration - Finance · 2024-26 Section B',
  batchYear: '2024-26 Section B',
  department: 'Department of Management Studies',
  departmentOption: 'MGMT · Department of Management Studies',
  college: 'BGS College of Engineering and Technology',
  collegeOption: 'BGSCET · BGS College of Engineering and Technology',
} as const;

/** The batch as the roster's Batch filter offers it: by its year, which the
 *  Course and Specialization boxes beside it narrow (2026-10-06), marked
 *  because it ended on 31 Jul 2026. */
const BATCH_OPTION = `${SEED.batchYear} (ended)`;

/** A password for a throwaway faculty account: 12 characters or more, and not
 *  one of the demo passwords `set_password` refuses. */
const FACULTY_PASSWORD = 'e2e faculty passphrase 2026';

const FIXTURES = path.join(__dirname, 'fixtures');

/** Unique to this run, so repeated runs never collide on a name or an address. */
const RUN = Date.now().toString(36);
let serial = 0;
function nextTag(): string {
  serial += 1;
  return `${RUN}${serial.toString(36)}`;
}

// ------------------------------------------------------------ API setup --

interface StudentApiRow {
  student_id: string;
  user_id: string;
  name: string;
  email: string;
  usn: string | null;
  cohort_id: string | null;
  mentor_user_id: string | null;
  current_stage: string;
  current_semester: number;
}

interface MentorLoadApiRow {
  mentor_id: string | null;
  user_id: string;
  name: string;
}

interface Seeded {
  studentId: string;
  mentorUserId: string;
  mentorId: string;
  departmentId: string;
  cohortId: string;
  courseId: string;
}

/** A batch as `GET /api/register/hierarchy` serves it. */
interface HierarchyBatchRow {
  id: string;
  course_id: string | null;
  display_label: string;
  current: boolean;
}

/** A batch a test made for itself: what the roster's Batch filter offers. */
interface Batch {
  id: string;
  /** "Master of Business Administration · 2026-28 E2E …", the server's own label. */
  label: string;
  /** "2026-28 E2E …": the batch as the roster's Batch box lists it, by year
   *  (2026-10-06). Unique to this test's batch, so picking it picks the batch. */
  year: string;
}

interface Faculty {
  userId: string;
  name: string;
  email: string;
  link: string;
}

interface CreatedStudent {
  studentId: string;
  name: string;
  email: string;
}

async function ok<T>(response: APIResponse, what: string): Promise<T> {
  if (!response.ok()) {
    throw new Error(`${what} answered ${response.status()}: ${await response.text()}`);
  }
  return (response.status() === 204 ? undefined : await response.json()) as T;
}

/** The ids the cases need, read from the API as the Main Admin. */
async function seeded(api: APIRequestContext): Promise<Seeded> {
  const students = await ok<StudentApiRow[]>(
    await api.get('/api/admin/students', { params: { q: SEED.student.usn } }),
    'GET /api/admin/students',
  );
  const load = await ok<MentorLoadApiRow[]>(
    await api.get('/api/admin/mentor-load'),
    'GET /api/admin/mentor-load',
  );
  const departments = await ok<{ id: string; name: string }[]>(
    await api.get('/api/admin/departments'),
    'GET /api/admin/departments',
  );
  const student = students.find((row) => row.usn === SEED.student.usn);
  const mentor = load.find((row) => row.name === SEED.mentor.name);
  const department = departments.find((row) => row.name === SEED.department);
  // The seeded batch is found by its own label, NEVER by where the student
  // sits now. A run stopped mid-case can leave Test Student in a test's
  // throwaway batch, and reading the batch off the student made every later
  // `restoreSeededStudent` "restore" them into that batch for good.
  const batch = (await hierarchyBatches(api)).find((row) => row.display_label === SEED.batch);
  if (!student || !batch || !mentor?.mentor_id || !department) {
    throw new Error('The dev seed is missing its student, batch, mentor group or department.');
  }
  if (!batch.course_id) throw new Error('The seeded batch names no course.');
  return {
    studentId: student.student_id,
    mentorUserId: mentor.user_id,
    mentorId: mentor.mentor_id,
    departmentId: department.id,
    cohortId: batch.id,
    courseId: batch.course_id,
  };
}

/** Every batch on the deployment, as the roster's Batch filter reads them. */
async function hierarchyBatches(api: APIRequestContext): Promise<HierarchyBatchRow[]> {
  const body = await ok<{ colleges: { departments: { batches: HierarchyBatchRow[] }[] }[] }>(
    await api.get('/api/register/hierarchy'),
    'GET /api/register/hierarchy',
  );
  return body.colleges.flatMap((college) =>
    college.departments.flatMap((department) => department.batches),
  );
}

/**
 * A running batch of the test's own, under the seeded department and course.
 *
 * A batch action writes to EVERY student on its batch's roster, and earlier
 * modules leave approved students (and removed ones) in the seeded batch. A
 * case about a batch action therefore works on a batch that holds only the
 * students it put there, and deletes it afterwards.
 */
async function createBatch(api: APIRequestContext, ids: Seeded): Promise<Batch> {
  const tag = nextTag();
  const name = `2026-28 E2E ${tag}`;
  const created = await ok<{ id: string }>(
    await api.post(`/api/admin/departments/${ids.departmentId}/cohorts`, {
      data: {
        code: `E2E-${tag}`,
        name,
        batch_label: '2026-28',
        degree_level: 'PG',
        entry_date: '2026-07-01',
        expected_completion: '2028-06-30',
        course_id: ids.courseId,
      },
    }),
    'POST /api/admin/departments/{id}/cohorts',
  );
  const row = (await hierarchyBatches(api)).find((batch) => batch.id === created.id);
  if (!row?.current) throw new Error('The new batch is not listed as running.');
  return { id: created.id, label: row.display_label, year: name };
}

async function seat(
  api: APIRequestContext,
  studentId: string,
  batchId: string | null,
): Promise<void> {
  await ok(
    await api.patch(`/api/admin/students/${studentId}`, { data: { cohort_id: batchId } }),
    'PATCH /api/admin/students/{id}',
  );
}

/** Takes every student, removed ones included, out of a test's own batch and
 *  deletes it: a batch can only be deleted empty. */
async function deleteBatch(api: APIRequestContext, batchId: string): Promise<void> {
  for (const removed of [false, true]) {
    const rows = await ok<StudentApiRow[]>(
      await api.get('/api/admin/students', {
        params: { cohort_id: batchId, removed: String(removed) },
      }),
      'GET /api/admin/students',
    );
    for (const row of rows) await seat(api, row.student_id, null);
  }
  const response = await api.delete(`/api/admin/cohorts/${batchId}`);
  if (!response.ok() && response.status() !== 404) {
    throw new Error(
      `Deleting batch ${batchId} answered ${response.status()}: ${await response.text()}`,
    );
  }
}

/** A faculty member's load, as the assignment screens print it. */
async function mentorLoad(
  api: APIRequestContext,
  userId: string,
): Promise<{ menteeCount: number; capacity: number }> {
  const rows = await ok<(MentorLoadApiRow & { mentee_count: number; capacity: number })[]>(
    await api.get('/api/admin/mentor-load'),
    'GET /api/admin/mentor-load',
  );
  const row = rows.find((candidate) => candidate.user_id === userId);
  if (!row) throw new Error(`${userId} is not on the mentor load.`);
  return { menteeCount: row.mentee_count, capacity: row.capacity };
}

/** "1 student", "5 students": the app's own plural. */
function plural(count: number, noun: string, many = `${noun}s`): string {
  return `${count} ${count === 1 ? noun : many}`;
}

async function seededStudentRow(api: APIRequestContext): Promise<StudentApiRow> {
  const rows = await ok<StudentApiRow[]>(
    await api.get('/api/admin/students', { params: { q: SEED.student.usn } }),
    'GET /api/admin/students',
  );
  const row = rows.find((candidate) => candidate.usn === SEED.student.usn);
  if (!row) throw new Error(`${SEED.student.usn} is not on the roster.`);
  return row;
}

/** A faculty account filed under the seeded department, and its activation link. */
async function createFaculty(api: APIRequestContext, departmentId: string): Promise<Faculty> {
  const tag = nextTag();
  const created = await ok<{
    user_id: string;
    name: string;
    email: string;
    activation_link: string;
  }>(
    await api.post('/api/admin/faculty', {
      data: {
        name: `E2E Faculty ${tag}`,
        email: `e2e.faculty.${tag}@bgscet.ac.in`,
        department_id: departmentId,
      },
    }),
    'POST /api/admin/faculty',
  );
  return {
    userId: created.user_id,
    name: created.name,
    email: created.email,
    link: created.activation_link,
  };
}

/** Redeems an activation link on a request context of its own: the link signs
 *  the faculty member in, and the admin's cookie jar must not receive that. */
async function activate(
  playwright: PlaywrightWorkerArgs['playwright'],
  baseURL: string,
  link: string,
): Promise<void> {
  const token = new URL(link).searchParams.get('token');
  const context = await playwright.request.newContext({ baseURL });
  try {
    await ok(
      await context.post('/api/auth/activate', { data: { token, password: FACULTY_PASSWORD } }),
      'POST /api/auth/activate',
    );
  } finally {
    await context.dispose();
  }
}

/** A student account, made the one way the product allows: the public form,
 *  then the Main Admin's approval. No USN pattern matches a rule that seats
 *  them, so they arrive with no batch and no faculty member. */
async function createStudent(api: APIRequestContext): Promise<CreatedStudent> {
  const tag = nextTag();
  const name = `E2E Student ${tag}`;
  const email = `e2e.student.${tag}@bgscet.ac.in`;
  const application = await ok<{ id: string }>(
    await api.post('/api/register', {
      multipart: {
        name,
        email,
        usn: `E2E${tag.toUpperCase()}`,
        phone: '9876543210',
        personal_email: `e2e.student.${tag}@example.com`,
        linkedin_url: `linkedin.com/in/e2e-${tag}`,
        cv: {
          name: 'cv.pdf',
          mimeType: 'application/pdf',
          buffer: fs.readFileSync(path.join(FIXTURES, 'sample.pdf')),
        },
        photo: {
          name: 'photo.png',
          mimeType: 'image/png',
          buffer: fs.readFileSync(path.join(FIXTURES, 'sample.png')),
        },
      },
    }),
    'POST /api/register',
  );
  const decided = await ok<{ approved_student_id: string | null }>(
    await api.post(`/api/register/${application.id}/decision`, { data: { decision: 'APPROVE' } }),
    'POST /api/register/{id}/decision',
  );
  if (!decided.approved_student_id) throw new Error('Approving the application made no student.');
  return { studentId: decided.approved_student_id, name, email };
}

const CLEANUP_REASON = 'E2E cleanup: throwaway account from an automated run';

/** Off every list, record kept. Already removed, or gone, is fine. */
async function removeUser(api: APIRequestContext, userId: string): Promise<void> {
  const response = await api.post(`/api/admin/users/${userId}/remove`, {
    data: { reason: CLEANUP_REASON },
  });
  if (!response.ok() && response.status() !== 409 && response.status() !== 404) {
    throw new Error(`Removing ${userId} answered ${response.status()}: ${await response.text()}`);
  }
}

async function removeStudent(api: APIRequestContext, studentId: string): Promise<void> {
  const response = await api.post(`/api/admin/students/${studentId}/remove`, {
    data: { reason: CLEANUP_REASON },
  });
  if (!response.ok() && response.status() !== 409 && response.status() !== 404) {
    throw new Error(
      `Removing ${studentId} answered ${response.status()}: ${await response.text()}`,
    );
  }
}

/** Puts the seeded student back with the seeded mentor, at the seeded stage
 *  and semester, whatever a failed case left behind. */
async function restoreSeededStudent(api: APIRequestContext, ids: Seeded): Promise<void> {
  const row = await seededStudentRow(api);
  if (row.mentor_user_id !== ids.mentorUserId) {
    await ok(
      await api.post(`/api/admin/students/${ids.studentId}/mentor`, {
        data: { mentor_id: ids.mentorId, reason: 'E2E cleanup: back with the seeded mentor' },
      }),
      'POST /api/admin/students/{id}/mentor',
    );
  }
  const patch: Record<string, unknown> = {};
  if (row.current_stage !== SEED.student.stageKey) patch['current_stage'] = SEED.student.stageKey;
  if (row.current_semester !== SEED.student.semester) {
    patch['current_semester'] = SEED.student.semester;
  }
  if (row.cohort_id !== ids.cohortId) patch['cohort_id'] = ids.cohortId;
  if (row.email !== SEED.student.email) patch['email'] = SEED.student.email;
  if (Object.keys(patch).length > 0) {
    await ok(
      await api.patch(`/api/admin/students/${ids.studentId}`, { data: patch }),
      'PATCH /api/admin/students/{id}',
    );
  }
}

/** Revokes whatever a failed case left granted to a throwaway account. */
async function revokeGrantsOf(api: APIRequestContext, userId: string): Promise<void> {
  const grants = await ok<{ id: string; subject_id: string }[]>(
    await api.get('/api/admin/governance/grants'),
    'GET /api/admin/governance/grants',
  );
  for (const grant of grants.filter((row) => row.subject_id === userId)) {
    await ok(
      await api.post(`/api/admin/governance/grants/${grant.id}/revoke`, {
        data: { reason: 'E2E cleanup: revoking a throwaway grant' },
      }),
      'POST /api/admin/governance/grants/{id}/revoke',
    );
  }
}

/** Deletes every Leaderboards rule, which the seed has none of. */
async function clearLeaderboardRules(api: APIRequestContext): Promise<void> {
  const rules = await ok<{ id: string; feature: string }[]>(
    await api.get('/api/admin/governance/features'),
    'GET /api/admin/governance/features',
  );
  for (const rule of rules.filter((row) => row.feature === 'student.leaderboards')) {
    await ok(
      await api.delete(`/api/admin/governance/features/${rule.id}`),
      'DELETE /api/admin/governance/features/{id}',
    );
  }
}

/** A second browser signed in as somebody else, for the cases that watch what
 *  an admin's act does to another person's session. */
async function openBrowserAs(
  browser: Browser,
  baseURL: string,
  email: string,
  password: string,
): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ baseURL, viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();
  await ok(
    await page.request.post('/api/auth/login', { data: { email, password } }),
    `signing in as ${email}`,
  );
  return { context, page };
}

/** yyyy-mm-dd for today plus `days`, in this machine's calendar. */
function isoDayFromToday(days: number): string {
  const when = new Date();
  when.setDate(when.getDate() + days);
  const month = `${when.getMonth() + 1}`.padStart(2, '0');
  const day = `${when.getDate()}`.padStart(2, '0');
  return `${when.getFullYear()}-${month}-${day}`;
}

// ------------------------------------------------------------ locators --

/** One cell of an AG Grid row. The grid keys every row by id (`getRowId`) and
 *  every cell by column id, and renders pinned columns in the same row. */
const gridCell = (scope: Page | Locator, rowId: string, colId: string): Locator =>
  scope.locator(`.ag-row[row-id="${rowId}"] [col-id="${colId}"]`);
const gridRow = (scope: Page | Locator, rowId: string): Locator =>
  scope.locator(`.ag-row[row-id="${rowId}"]`);

/**
 * A grid cell's text after the grid's rows were replaced. For a moment after a
 * reload AG Grid can hold a row's old cells beside its new ones, and a plain
 * `toHaveText` then fails at once on two matches. So the new text is waited
 * for first, then for the old cell to leave, and only then is the whole text
 * compared.
 */
async function expectCell(
  cell: Locator,
  text: string,
  message: string,
  match: 'exact' | 'contains' = 'exact',
): Promise<void> {
  await expect(cell.filter({ hasText: text }), message).toBeVisible();
  await expect(cell, message).toHaveCount(1);
  if (match === 'exact') await expect(cell, message).toHaveText(text);
  else await expect(cell, message).toContainText(text);
}

const notice = (page: Page, text: string | RegExp): Locator =>
  page.getByRole('status').filter({ hasText: text });
const alert = (page: Page, text: string | RegExp): Locator =>
  page.getByRole('alert').filter({ hasText: text });
const heading = (page: Page, name: string): Locator =>
  page.getByRole('heading', { level: 1, name, exact: true });
const rowsCount = (page: Page): Locator =>
  page.locator('.dt-statusbar span').filter({ hasText: /^Rows:/ });

// The roster
const rosterSearch = (page: Page) =>
  page.getByRole('searchbox', { name: 'Search students by name, email or USN' });
// The faculty directory
const facultySearch = (page: Page) =>
  page.getByRole('searchbox', { name: 'Search faculty by name, email, department or designation' });
const facultyDrawer = (page: Page) => page.getByRole('complementary', { name: 'Faculty member' });

// ------------------------------------------------------------ the cases --

test.describe('Admin: people and access', () => {
  // Every roster column on screen at once: AG Grid only draws the columns in
  // view, and a narrower window scrolls the grid sideways.
  test.use({ viewport: { width: 1920, height: 1080 } });

  // ================================================================ home ==

  test('The Main Admin home shows the waiting counts and every task @TC-500', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const count = async (url: string): Promise<number> =>
      (await ok<unknown[]>(await api.get(url), `GET ${url}`)).length;
    const review = await ok<{ pending: unknown[]; expiring: unknown[] }>(
      await api.get('/api/admin/governance/review'),
      'GET /api/admin/governance/review',
    );
    // What each queue screen lists right now, read the way the queue screen
    // reads it, so the count on the button can be checked against it.
    const queues: [string, number][] = [
      ['New student applications', await count('/api/register/pending')],
      ['Leave requests', await count('/api/leaves/pending')],
      ['Students without a faculty member', await count('/api/admin/unassigned-students')],
      ['Job offers to approve', await count('/api/mentor/offers/pending')],
      ['Access requests to review', review.pending.length + review.expiring.length],
    ];
    const groups: [string, string[]][] = [
      [
        'Students',
        [
          'Approve new students',
          'Find a student',
          'Assign faculty to students',
          'Upload marks & attendance',
        ],
      ],
      ['Faculty', ['Add a faculty member', 'See all faculty', 'Approve leave']],
      ['Jobs', ['Post a job', 'Approve job offers']],
      ['Interviews', ['Edit interview questions', 'See interview records', 'Write SWOC notes']],
      ['Reports', ['See charts & numbers', 'Download a report']],
      [
        'Setup',
        [
          'Set up a college',
          'Colleges',
          'Departments, courses & batches',
          'Subjects & certificates',
          'Decide who can do what',
          'See what changed',
          'Ask REEP',
        ],
      ],
    ];
    const home = page.locator('[data-p="admin-home"]');

    await test.step('1. Open /admin', async () => {
      await page.goto('/admin');
      await expect(
        heading(page, 'What do you want to do?'),
        'TC-500 ER-1: the home is headed "What do you want to do?"',
      ).toBeVisible();
      await expect(
        page.getByRole('searchbox', { name: 'Find a task' }),
        'TC-500 ER-1: there is a "Find a task" box',
      ).toBeVisible();
      await expect(
        home.getByRole('heading', { level: 2, name: 'Waiting for you' }),
        'TC-500 ER-2: the counts sit under "Waiting for you"',
      ).toBeVisible();
      for (const [label, waiting] of queues) {
        await expect(
          home.getByRole('link', { name: `${waiting} ${label}`, exact: true }),
          `TC-500 ER-2/ER-3: "${label}" shows ${waiting}, the number waiting on its screen`,
        ).toBeVisible();
      }
      for (const [group, tiles] of groups) {
        await expect(
          home.getByRole('heading', { level: 2, name: group, exact: true }),
          `TC-500 ER-4: the "${group}" group is shown`,
        ).toBeVisible();
        for (const tile of tiles) {
          await expect(
            home.getByRole('link', { name: tile, exact: true }),
            `TC-500 ER-4: "${tile}" is a button under ${group}`,
          ).toBeVisible();
        }
      }
    });
  });

  test('A waiting count and a task button open their screens @TC-501', async ({ page, signIn }) => {
    await signIn('admin');
    const home = page.locator('[data-p="admin-home"]');

    await test.step('1. Open /admin', async () => {
      await page.goto('/admin');
    });

    await test.step('2. Click the "New student applications" count', async () => {
      await home.getByRole('link', { name: /New student applications$/ }).click();
      await expect(page, 'TC-501 ER-1: the count opens /admin/registrations').toHaveURL(
        /\/admin\/registrations$/,
      );
      await expect(
        heading(page, 'New applications'),
        'TC-501 ER-1: the "New applications" queue is shown',
      ).toBeVisible();
    });

    await test.step('3. Open /admin again', async () => {
      await page.goto('/admin');
    });

    await test.step('4. Click "Find a student"', async () => {
      await home.getByRole('link', { name: 'Find a student', exact: true }).click();
      await expect(page, 'TC-501 ER-2: the task opens /admin/students').toHaveURL(
        /\/admin\/students$/,
      );
      await expect(
        heading(page, 'Students & batches'),
        'TC-501 ER-2: the roster is shown',
      ).toBeVisible();
    });

    await test.step('5. Open /admin again', async () => {
      await page.goto('/admin');
    });

    await test.step('6. Click "Decide who can do what"', async () => {
      await home.getByRole('link', { name: 'Decide who can do what', exact: true }).click();
      await expect(page, 'TC-501 ER-3: the task opens /admin/governance').toHaveURL(
        /\/admin\/governance$/,
      );
      await expect(
        heading(page, 'Who can do what'),
        'TC-501 ER-3: the "Who can do what" screen is shown',
      ).toBeVisible();
    });
  });

  test('Finding a task narrows the buttons @TC-502', async ({ page, signIn }) => {
    await signIn('admin');
    const home = page.locator('[data-p="admin-home"]');
    const finder = page.getByRole('searchbox', { name: 'Find a task' });
    const waiting = home.getByRole('heading', { level: 2, name: 'Waiting for you' });

    await test.step('1. Open /admin', async () => {
      await page.goto('/admin');
      await expect(waiting, 'TC-502 (arrange): the waiting counts are shown').toBeVisible();
    });

    await test.step('2. Type audit into "Find a task"', async () => {
      await finder.fill('audit');
      await expect(home.getByRole('link'), 'TC-502 ER-1: one task button is left').toHaveCount(1);
      await expect(
        home.getByRole('link', { name: 'See what changed', exact: true }),
        'TC-502 ER-1: the one left is "See what changed"',
      ).toBeVisible();
      await expect(waiting, 'TC-502 ER-1: the waiting counts are hidden').toBeHidden();
    });

    await test.step('3. Replace the text with zzqx', async () => {
      await finder.fill('zzqx');
      await expect(
        home.getByRole('status').filter({ hasText: 'Nothing called “zzqx”.' }),
        'TC-502 ER-2: the screen says nothing is called that',
      ).toBeVisible();
      await expect(home.getByRole('link'), 'TC-502 ER-2: no task button is left').toHaveCount(0);
    });

    await test.step('4. Clear the box', async () => {
      await finder.fill('');
      await expect(waiting, 'TC-502 ER-3: the waiting counts are back').toBeVisible();
      await expect(
        home.getByRole('link', { name: 'Find a student', exact: true }),
        'TC-502 ER-3: every task button is back',
      ).toBeVisible();
    });
  });

  // ============================================================== roster ==

  test('The roster lists the seeded student with their details @TC-503', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const ids = await seeded(page.request);

    await test.step('1. Open /admin/students', async () => {
      await page.goto('/admin/students');
      await expect(
        heading(page, 'Students & batches'),
        'TC-503 ER-1: the roster is shown',
      ).toBeVisible();
      const cell = (colId: string) => gridCell(page, ids.studentId, colId);
      await expect(cell('usn'), 'TC-503 ER-1: USN').toHaveText(SEED.student.usn);
      await expect(cell('name'), 'TC-503 ER-1: the Student column names them').toContainText(
        SEED.student.name,
      );
      await expect(cell('name'), 'TC-503 ER-1: with their college email').toContainText(
        SEED.student.email,
      );
      await expect(cell('specialization'), 'TC-503 ER-1: Spec.').toHaveText('FIN');
      await expect(cell('semester'), 'TC-503 ER-1: Sem').toHaveText(`${SEED.student.semester}`);
      await expect(cell('stage'), 'TC-503 ER-1: Stage').toHaveText(SEED.student.stageLabel);
      await expect(cell('mentor'), 'TC-503 ER-1: Faculty').toHaveText(SEED.mentor.name);
      await expect(cell('status'), 'TC-503 ER-1: Status').toHaveText('Active');
      await expect(
        page.getByRole('button', { name: /add student/i }),
        'TC-503 ER-2: there is no "Add student" control',
      ).toHaveCount(0);
      await expect(
        page.getByRole('link', { name: 'Students arrive from Registrations' }),
        'TC-503 ER-2: a link says students arrive from Registrations',
      ).toHaveAttribute('href', '/admin/registrations');
    });
  });

  test('Searching the roster by USN and by name @TC-504', async ({ page, signIn }) => {
    await signIn('admin');
    const ids = await seeded(page.request);
    const row = gridRow(page, ids.studentId);

    await test.step('1. Open /admin/students', async () => {
      await page.goto('/admin/students');
      await expect(row, 'TC-504 (arrange): the seeded student is listed').toBeVisible();
    });

    await test.step('2. Type 1BG24MBA001 into "Quick filter…"', async () => {
      await rosterSearch(page).fill(SEED.student.usn);
      await expect(rowsCount(page), 'TC-504 ER-1: one row is left').toHaveText('Rows: 1');
      await expect(row, 'TC-504 ER-1: the row is Test Student').toBeVisible();
    });

    await test.step('3. Replace the text with no-such-student-zz', async () => {
      await rosterSearch(page).fill('no-such-student-zz');
      await expect(
        page.getByText('No student matches “no-such-student-zz”.'),
        'TC-504 ER-2: the grid says no student matches',
      ).toBeVisible();
      await expect(rowsCount(page), 'TC-504 ER-2: no row is left').toHaveText('Rows: 0');
    });

    await test.step('4. Replace the text with Test Student', async () => {
      await rosterSearch(page).fill(SEED.student.name);
      await expect(row, 'TC-504 ER-3: Test Student is found by name').toBeVisible();
      await expect(rowsCount(page), 'TC-504 ER-3: one row is listed').toHaveText('Rows: 1');
    });
  });

  test('Searching the roster by college email finds the student @TC-505', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const ids = await seeded(page.request);
    const row = gridRow(page, ids.studentId);

    await test.step('1. Open /admin/students', async () => {
      await page.goto('/admin/students');
      await expect(row, 'TC-505 (arrange): the seeded student is listed').toBeVisible();
    });

    await test.step('2. Type student@bgscet.ac.in into "Quick filter…"', async () => {
      await rosterSearch(page).fill(SEED.student.email);
      await expect(
        page.getByText(/^1 student in view across every batch/),
        'TC-505 ER-1 (arrange): the server found the student by address',
      ).toBeVisible();
      await expect(row, 'TC-505 ER-1: Test Student is listed').toBeVisible();
      await expect(rowsCount(page), 'TC-505 ER-1: the bar reads "Rows: 1"').toHaveText('Rows: 1');
    });
  });

  test('Filtering the roster by batch and status @TC-506', async ({ page, signIn }) => {
    await signIn('admin');
    const ids = await seeded(page.request);
    // The case reads Test Student in the seeded batch; a run stopped mid-case
    // elsewhere can have left them in another one.
    await restoreSeededStudent(page.request, ids);
    const row = gridRow(page, ids.studentId);
    const rail = page.locator('.roster-rail');
    const batchFilter = page.getByRole('combobox', { name: 'Batch', exact: true });
    const statusFilter = page.getByRole('combobox', { name: 'Status', exact: true });

    await test.step('1. Open /admin/students', async () => {
      await page.goto('/admin/students');
      await expect(row, 'TC-506 (arrange): the seeded student is listed').toBeVisible();
    });

    await test.step(`2. Under "Batch", choose "${BATCH_OPTION}"`, async () => {
      await batchFilter.selectOption({ label: BATCH_OPTION });
      await expect(
        rail.getByRole('heading', { name: `Batch · ${SEED.batchYear}` }),
        'TC-506 ER-1: the side card names the batch',
      ).toBeVisible();
      await expect(rail, 'TC-506 ER-1: the course and degree level').toContainText(
        'Master of Business Administration · PG',
      );
      await expect(rail, 'TC-506 ER-1: the specialization').toContainText('Finance');
      await expect(rail, 'TC-506 ER-1: the department').toContainText(SEED.department);
      await expect(rail, 'TC-506 ER-1: the term').toContainText('Ended · 2024-26');
      await expect(row, 'TC-506 ER-1: Test Student is in the batch').toBeVisible();
    });

    await test.step('3. Under "Status", choose "Invited · not signed in yet"', async () => {
      await statusFilter.selectOption({ label: 'Invited · not signed in yet' });
      await expect(row, 'TC-506 ER-2: Test Student, who has signed in, is hidden').toBeHidden();
      await expect(
        page.locator('.ag-row [col-id="status"]').filter({ hasNotText: /^Invited$/ }),
        'TC-506 ER-2: every student still listed reads Invited',
      ).toHaveCount(0);
    });

    await test.step('4. Under "Status", choose "All"', async () => {
      await statusFilter.selectOption({ label: 'All' });
      await expect(row, 'TC-506 ER-3: Test Student is listed again').toBeVisible();
    });

    await test.step('5. Under "Batch", choose "No batch yet"', async () => {
      await batchFilter.selectOption({ label: 'No batch yet' });
      await expect(
        page.getByText(/with no batch yet · \d+ seated with a faculty member$/),
        'TC-506 ER-4: the line under the heading counts the students with no batch',
      ).toBeVisible();
      await expect(row, 'TC-506 ER-4: Test Student, who is in a batch, is not listed').toHaveCount(
        0,
      );
    });
  });

  test('A batch whose students are all filtered out is not called empty @TC-507', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    // Test Student, who has signed in, is the batch's only student. A batch of
    // the test's own, so that nobody another module seated can be listed.
    const batch = await createBatch(api, ids);
    const row = gridRow(page, ids.studentId);

    try {
      await seat(api, ids.studentId, batch.id);

      await test.step('1. Open /admin/students', async () => {
        await page.goto('/admin/students');
      });

      await test.step('2. Under "Batch", choose the new batch', async () => {
        await page.getByRole('combobox', { name: 'Batch', exact: true }).selectOption(batch.year);
        await expect(row, 'TC-507 (arrange): Test Student is in the batch').toBeVisible();
      });

      await test.step('3. Under "Status", choose "Invited · not signed in yet"', async () => {
        await page
          .getByRole('combobox', { name: 'Status', exact: true })
          .selectOption({ label: 'Invited · not signed in yet' });
        await expect(
          row,
          'TC-507 (arrange): Test Student, who has signed in, is hidden',
        ).toBeHidden();
        await expect(
          page.getByText('No student matches these filters.'),
          'TC-507 ER-1: the grid says no student matches these filters',
        ).toBeVisible();
        await expect(
          page.getByText('Nobody is in this batch.'),
          'TC-507 ER-1: it does not claim the batch is empty',
        ).toHaveCount(0);
      });
    } finally {
      await restoreSeededStudent(api, ids);
      await deleteBatch(api, batch.id);
    }
  });

  test('Editing a student and putting the change back @TC-508', async ({ page, signIn }) => {
    await signIn('admin');
    const ids = await seeded(page.request);
    const editButton = page.getByRole('button', {
      name: `Edit ${SEED.student.name}`,
      exact: true,
    });
    const dialog = page.getByRole('dialog', { name: `Edit ${SEED.student.name}` });
    const stage = gridCell(page, ids.studentId, 'stage');
    const chosen = (name: string) =>
      dialog.getByRole('combobox', { name }).locator('option:checked');

    try {
      await test.step('1. Open /admin/students', async () => {
        await page.goto('/admin/students');
        await expect(stage, 'TC-508 (arrange): Test Student is at Excel-Adv').toHaveText(
          SEED.student.stageLabel,
        );
      });

      await test.step("2. Click the pencil on Test Student's row", async () => {
        await editButton.click();
        await expect(dialog, 'TC-508 ER-1: the "Edit Test Student" dialog opens').toBeVisible();
        await expect(dialog, 'TC-508 ER-1: it says nothing here sets a password').toContainText(
          'Nothing here sets a password.',
        );
        await expect(
          dialog.getByRole('textbox', { name: 'Name' }),
          'TC-508 ER-1: Name',
        ).toHaveValue(SEED.student.name);
        await expect(
          dialog.getByRole('textbox', { name: 'College email' }),
          'TC-508 ER-1: College email',
        ).toHaveValue(SEED.student.email);
        await expect(dialog.getByRole('textbox', { name: 'USN' }), 'TC-508 ER-1: USN').toHaveValue(
          SEED.student.usn,
        );
        await expect(chosen('Faculty member'), 'TC-508 ER-1: Faculty member').toHaveText(
          SEED.mentor.name,
        );
        await expect(chosen('Stage'), 'TC-508 ER-1: Stage').toHaveText(SEED.student.stageLabel);
        await expect(chosen('Semester'), 'TC-508 ER-1: Semester').toHaveText(
          `${SEED.student.semester}`,
        );
      });

      await test.step('3. Under "Stage", choose "Elevate"', async () => {
        await dialog.getByRole('combobox', { name: 'Stage' }).selectOption({ label: 'Elevate' });
      });

      await test.step('4. Click Save', async () => {
        await dialog.getByRole('button', { name: 'Save', exact: true }).click();
        await expect(dialog, 'TC-508 ER-2: the dialog closes').toBeHidden();
        await expect(notice(page, 'Saved.'), 'TC-508 ER-2: the screen says "Saved."').toBeVisible();
        await expectCell(stage, 'Elevate', 'TC-508 ER-2: the Stage column reads Elevate');
      });

      await test.step("5. Click the pencil on Test Student's row again", async () => {
        await editButton.click();
        await expect(chosen('Stage'), 'TC-508 ER-3: the dialog opens at Elevate').toHaveText(
          'Elevate',
        );
      });

      await test.step('6. Under "Stage", choose "Excel-Adv"', async () => {
        await dialog
          .getByRole('combobox', { name: 'Stage' })
          .selectOption({ label: SEED.student.stageLabel });
      });

      await test.step('7. Click Save', async () => {
        await dialog.getByRole('button', { name: 'Save', exact: true }).click();
        await expect(dialog, 'TC-508 ER-4: the dialog closes').toBeHidden();
        await expect(notice(page, 'Saved.'), 'TC-508 ER-4: the screen says "Saved."').toBeVisible();
        await expectCell(
          stage,
          SEED.student.stageLabel,
          'TC-508 ER-4: the Stage column reads Excel-Adv again',
        );
      });
    } finally {
      await restoreSeededStudent(page.request, ids);
    }
  });

  test('Editing a student refuses an address off the college domain @TC-509', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const ids = await seeded(page.request);
    const dialog = page.getByRole('dialog', { name: `Edit ${SEED.student.name}` });
    const offDomain = 'test.student@example.com';

    try {
      await test.step('1. Open /admin/students', async () => {
        await page.goto('/admin/students');
      });

      await test.step("2. Click the pencil on Test Student's row", async () => {
        await page.getByRole('button', { name: `Edit ${SEED.student.name}`, exact: true }).click();
        await expect(dialog, 'TC-509 (arrange): the edit dialog is open').toBeVisible();
      });

      await test.step('3. Replace the "College email" with test.student@example.com', async () => {
        await dialog.getByRole('textbox', { name: 'College email' }).fill(offDomain);
      });

      await test.step('4. Click Save', async () => {
        await dialog.getByRole('button', { name: 'Save', exact: true }).click();
        await expect(
          alert(
            page,
            `${offDomain} is not on a college domain (bgscet.ac.in). A student account is a ` +
              'sign-in, and only a college address gets one.',
          ),
          'TC-509 ER-1: the save is refused with the reason',
        ).toBeVisible();
        await expect(dialog, 'TC-509 ER-1: the dialog stays open').toBeVisible();
      });

      await test.step('5. Click Cancel', async () => {
        await dialog.getByRole('button', { name: 'Cancel' }).click();
        await expect(dialog, 'TC-509 ER-2: the dialog closes').toBeHidden();
        await expect(
          gridCell(page, ids.studentId, 'name'),
          'TC-509 ER-2: the row still shows the college address',
        ).toContainText(SEED.student.email);
        await expect(
          gridCell(page, ids.studentId, 'name'),
          'TC-509 ER-2: the refused address was not stored',
        ).not.toContainText(offDomain);
      });
    } finally {
      await restoreSeededStudent(page.request, ids);
    }
  });

  test('A batch action sets the semester and the stage for the whole batch @TC-510', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    // Test Student is moved into a batch of the test's own, so the action
    // reaches nobody another module seated, and is put back when the case ends.
    const batch = await createBatch(api, ids);
    const dialog = page.getByRole('dialog', { name: `Batch actions · ${batch.label}` });
    const semester = gridCell(page, ids.studentId, 'semester');
    const stage = gridCell(page, ids.studentId, 'stage');
    const setTo = async (control: string, value: { label: string }) => {
      const action = dialog
        .locator('.batch-action')
        .filter({ has: page.getByRole('combobox', { name: control }) });
      await action.getByRole('combobox', { name: control }).selectOption(value);
      await action.getByRole('button', { name: 'Set', exact: true }).click();
    };

    try {
      await seat(api, ids.studentId, batch.id);

      await test.step('1. Open /admin/students', async () => {
        await page.goto('/admin/students');
      });

      await test.step('2. Under "Batch", choose the new batch', async () => {
        await page.getByRole('combobox', { name: 'Batch', exact: true }).selectOption(batch.year);
        await expect(semester, 'TC-510 (arrange): Test Student is in semester 2').toHaveText(
          String(SEED.student.semester),
        );
        await expect(stage, 'TC-510 (arrange): at stage Excel-Adv').toHaveText(
          SEED.student.stageLabel,
        );
      });

      await test.step('3. Click "Batch actions"', async () => {
        await page.getByRole('button', { name: 'Batch actions' }).click();
        await expect(dialog, 'TC-510 ER-1: the batch actions dialog names the batch').toBeVisible();
        await expect(
          dialog,
          'TC-510 ER-1: it says every action touches the whole batch',
        ).toContainText(
          'Every action here touches all 1 student in this batch — the filters above do not narrow it.',
        );
      });

      await test.step('4. Under "Set semester", choose 3 and click the Set button beside it', async () => {
        await setTo('Set semester', { label: '3' });
        await expect(dialog, 'TC-510 ER-2: the dialog closes').toBeHidden();
        await expect(
          notice(page, '1 student: set to semester 3.'),
          'TC-510 ER-2: the screen says the batch moved to semester 3',
        ).toBeVisible();
        await expectCell(semester, '3', "TC-510 ER-2: Test Student's Sem column reads 3");
      });

      await test.step('5. Click "Batch actions" again', async () => {
        await page.getByRole('button', { name: 'Batch actions' }).click();
        await expect(dialog, 'TC-510 ER-3: the dialog opens again').toBeVisible();
      });

      await test.step('6. Under "Set stage", choose "Elevate" and click the Set button beside it', async () => {
        await setTo('Set stage', { label: 'Elevate' });
        await expect(
          notice(page, '1 student: set to Elevate.'),
          'TC-510 ER-4: the screen says the batch moved to Elevate',
        ).toBeVisible();
        await expectCell(
          stage,
          'Elevate',
          "TC-510 ER-4: Test Student's Stage column reads Elevate",
        );
      });
    } finally {
      await restoreSeededStudent(api, ids);
      await deleteBatch(api, batch.id);
    }
  });

  test('A batch action writes only to the students its dialog counts @TC-539', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    // Test Student is on the roster; a new student is seated beside them and
    // removed. Both are in a batch of the test's own.
    const batch = await createBatch(api, ids);
    const dialog = page.getByRole('dialog', { name: `Batch actions · ${batch.label}` });
    let removed: CreatedStudent | null = null;
    const removedId = () => {
      if (removed === null) throw new Error('The removed student was not created.');
      return removed.studentId;
    };

    try {
      await seat(api, ids.studentId, batch.id);
      removed = await createStudent(api);
      await seat(api, removed.studentId, batch.id);
      await removeStudent(api, removed.studentId);

      await test.step('1. Open /admin/students', async () => {
        await page.goto('/admin/students');
      });

      await test.step('2. Under "Batch", choose the new batch', async () => {
        await page.getByRole('combobox', { name: 'Batch', exact: true }).selectOption(batch.year);
        await expect(
          gridRow(page, ids.studentId),
          'TC-539 (arrange): Test Student, on the roster, is listed',
        ).toBeVisible();
      });

      await test.step('3. Click "Batch actions"', async () => {
        await page.getByRole('button', { name: 'Batch actions' }).click();
        await expect(
          dialog,
          'TC-539 ER-1: the dialog counts the one student on the roster',
        ).toContainText(
          'Every action here touches all 1 student in this batch — the filters above do not narrow it.',
        );
      });

      await test.step('4. Under "Set semester", choose 3 and click the Set button beside it', async () => {
        const action = dialog
          .locator('.batch-action')
          .filter({ has: page.getByRole('combobox', { name: 'Set semester' }) });
        await action.getByRole('combobox', { name: 'Set semester' }).selectOption({ label: '3' });
        await action.getByRole('button', { name: 'Set', exact: true }).click();
        await expect(
          notice(page, '1 student: set to semester 3.'),
          'TC-539 ER-2: the screen reports the one student the dialog counted',
        ).toBeVisible();
      });

      await test.step('5. Under "Status", choose "Removed · off the roster, record kept"', async () => {
        await page
          .getByRole('combobox', { name: 'Status', exact: true })
          .selectOption({ label: 'Removed · off the roster, record kept' });
        await expect(
          gridRow(page, removedId()),
          'TC-539 (arrange): the removed student is listed',
        ).toBeVisible();
        await expect(
          gridCell(page, removedId(), 'semester'),
          'TC-539 ER-3: the removed student is still in semester 1',
        ).toHaveText('1');
      });
    } finally {
      await restoreSeededStudent(api, ids);
      await deleteBatch(api, batch.id);
      if (removed !== null) await removeStudent(api, removed.studentId);
    }
  });

  test('Assigning a faculty member to the ticked students @TC-511', async ({ page, signIn }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    // The loads the dialog prints beside each name, read the way it reads them.
    const theirs = await mentorLoad(api, ids.mentorUserId);
    const fresh = await mentorLoad(api, faculty.userId).catch(() => ({
      menteeCount: 0,
      capacity: theirs.capacity,
    }));
    const dialog = page.getByRole('dialog', { name: 'Assign a faculty member' });
    const editDialog = page.getByRole('dialog', { name: `Edit ${SEED.student.name}` });
    const mentorCell = gridCell(page, ids.studentId, 'mentor');
    const assignButton = page.getByRole('button', { name: 'Assign faculty to 1 selected' });

    try {
      await test.step('1. Open /admin/students', async () => {
        await page.goto('/admin/students');
        await expect(mentorCell, 'TC-511 (arrange): Test Student is with Test Mentor').toHaveText(
          SEED.mentor.name,
        );
      });

      await test.step("2. Tick Test Student's row", async () => {
        await gridRow(page, ids.studentId).getByRole('checkbox').check();
        await expect(
          assignButton,
          'TC-511 ER-1: the toolbar counts one ticked student',
        ).toBeEnabled();
        await expect(
          page.locator('.dt-statusbar span').filter({ hasText: /^Selected:/ }),
          'TC-511 ER-1: the bar under the grid reads "Selected: 1"',
        ).toHaveText('Selected: 1');
      });

      await test.step('3. Click "Assign faculty to 1 selected"', async () => {
        await assignButton.click();
        await expect(
          dialog,
          'TC-511 ER-2: the "Assign a faculty member" dialog opens',
        ).toBeVisible();
        await expect(dialog, 'TC-511 ER-2: it counts the ticked students').toContainText(
          '1 student ticked in the grid.',
        );
        await expect(
          dialog.getByRole('combobox', { name: 'Faculty member' }).getByRole('option', {
            name: `${SEED.mentor.name} — ${theirs.menteeCount} of ${theirs.capacity}`,
          }),
          'TC-511 ER-2: each faculty member is offered with their load',
        ).toHaveCount(1);
      });

      await test.step('4. Under "Faculty member", choose the new faculty member and click "Apply to 1 student"', async () => {
        await dialog
          .getByRole('combobox', { name: 'Faculty member' })
          .selectOption({ label: `${faculty.name} — ${fresh.menteeCount} of ${fresh.capacity}` });
        await dialog.getByRole('button', { name: 'Apply to 1 student' }).click();
        await expect(dialog, 'TC-511 ER-3: the dialog closes').toBeHidden();
        await expect(
          notice(page, `1 of 1 student: assigned to ${faculty.name}.`),
          'TC-511 ER-3: the screen says how many were assigned, and to whom',
        ).toBeVisible();
        await expectCell(mentorCell, faculty.name, 'TC-511 ER-3: the Faculty column names them');
      });

      await test.step("5. Click the pencil on Test Student's row", async () => {
        await page.getByRole('button', { name: `Edit ${SEED.student.name}`, exact: true }).click();
        await expect(
          editDialog.getByRole('combobox', { name: 'Faculty member' }).locator('option:checked'),
          'TC-511 ER-4: the edit dialog shows the new faculty member',
        ).toHaveText(faculty.name);
      });

      await test.step('6. Under "Faculty member", choose "Test Mentor" and click Save', async () => {
        await editDialog
          .getByRole('combobox', { name: 'Faculty member' })
          .selectOption({ label: SEED.mentor.name });
        await editDialog.getByRole('button', { name: 'Save', exact: true }).click();
        await expect(notice(page, 'Saved.'), 'TC-511 ER-5: the screen says "Saved."').toBeVisible();
        await expectCell(
          mentorCell,
          SEED.mentor.name,
          'TC-511 ER-5: the Faculty column reads Test Mentor again',
        );
      });
    } finally {
      await restoreSeededStudent(api, ids);
      await removeUser(api, faculty.userId);
    }
  });

  test("After a selection action the grid's ticks match the toolbar @TC-512", async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const tick = gridRow(page, ids.studentId).getByRole('checkbox');
    const dialog = page.getByRole('dialog', { name: 'Assign a faculty member' });
    const load = await mentorLoad(api, ids.mentorUserId);

    try {
      await test.step('1. Open /admin/students', async () => {
        await page.goto('/admin/students');
      });

      await test.step("2. Tick Test Student's row", async () => {
        await tick.check();
        await expect(tick, 'TC-512 (arrange): the row is ticked').toBeChecked();
      });

      await test.step('3. Click "Assign faculty to 1 selected"', async () => {
        await page.getByRole('button', { name: 'Assign faculty to 1 selected' }).click();
        await expect(dialog, 'TC-512 (arrange): the dialog is open').toBeVisible();
      });

      await test.step('4. Under "Faculty member", choose "Test Mentor", who already mentors them, and click "Apply to 1 student"', async () => {
        await dialog
          .getByRole('combobox', { name: 'Faculty member' })
          .selectOption({ label: `${SEED.mentor.name} — ${load.menteeCount} of ${load.capacity}` });
        await dialog.getByRole('button', { name: 'Apply to 1 student' }).click();
        await expect(
          notice(page, `1 of 1 student: assigned to ${SEED.mentor.name}.`),
          'TC-512 (arrange): the action finished',
        ).toBeVisible();
        await expect(
          page.locator('.dt-statusbar span').filter({ hasText: /^Selected:/ }),
          'TC-512 ER-1 (arrange): the bar reads "Selected: 0"',
        ).toHaveText('Selected: 0');
        await expect(tick, 'TC-512 ER-1: the row is no longer ticked either').not.toBeChecked();
      });
    } finally {
      await restoreSeededStudent(api, ids);
    }
  });

  // ========================================================= student 360 ==

  test("The View button opens the student's full record @TC-513", async ({ page, signIn }) => {
    await signIn('admin');
    const ids = await seeded(page.request);

    await test.step('1. Open /admin/students', async () => {
      await page.goto('/admin/students');
    });

    await test.step("2. Click the eye on Test Student's row", async () => {
      await page.getByRole('button', { name: `View ${SEED.student.name}'s record` }).click();
      await expect(page, 'TC-513 ER-1: the record opens at /admin/students/<id>').toHaveURL(
        new RegExp(`/admin/students/${ids.studentId}$`),
      );
      await expect(
        heading(page, SEED.student.name),
        'TC-513 ER-1: headed with their name',
      ).toBeAttached();
      const summary = page.getByRole('complementary', { name: 'Student summary' });
      await expect(
        summary.getByRole('heading', { level: 2, name: SEED.student.name, exact: true }),
        'TC-513 ER-1: the summary card names the student',
      ).toBeVisible();
      await expect(summary, 'TC-513 ER-1: with their USN').toContainText(SEED.student.usn);
      const chips = summary.locator('.chip-row .chip');
      await expect(
        chips.filter({ hasText: /^Active$/ }),
        'TC-513 ER-1: an "Active" chip',
      ).toBeVisible();
      await expect(
        chips.filter({ hasText: new RegExp(`^${SEED.student.stageLabel} stage$`) }),
        'TC-513 ER-1: the stage chip',
      ).toBeVisible();
      const fact = (term: string) =>
        summary.locator('.rail-facts > div').filter({ has: page.getByRole('term').getByText(term, { exact: true }) }).getByRole('definition');
      await expect(fact('Batch'), 'TC-513 ER-1: Batch').toHaveText(SEED.batch);
      await expect(fact('Department'), 'TC-513 ER-1: Department').toHaveText(SEED.department);
      await expect(fact('Semester'), 'TC-513 ER-1: Semester').toHaveText(
        String(SEED.student.semester),
      );
      await expect(fact('Faculty mentor'), 'TC-513 ER-1: Faculty mentor').toContainText(
        SEED.mentor.name,
      );
      await expect(
        page.getByRole('tablist', { name: 'Parts of the record' }).getByRole('tab'),
        'TC-513 ER-2: the record has its nine tabs, in order',
      ).toHaveText([
        'Overview',
        'Resume',
        'Skills',
        'Leaderboard',
        'Academics',
        'Interviews',
        'Time sheet',
        'Documents',
        'Access',
      ]);
    });
  });

  test('The full record shows the profile, results, documents and mentor history @TC-514', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const ids = await seeded(page.request);
    const card = (title: string) =>
      page
        .locator('.card')
        .filter({ has: page.getByRole('heading', { level: 2, name: title, exact: true }) });
    const tabs = page.getByRole('tablist', { name: 'Parts of the record' });
    // The seeded uploads, by title. Their verdicts are read from the record
    // itself: module 05's verification cases may have decided one since the seed.
    const verdictLabel: Record<string, string> = {
      PENDING_REVIEW: 'Pending review',
      VERIFIED: 'Verified',
      NEEDS_CHANGES: 'Needs changes',
      REJECTED: 'Rejected',
    };
    const record = await ok<{
      documents: { title: string; status: string; review_note: string | null }[];
    }>(await page.request.get(`/api/admin/students/${ids.studentId}/360`), 'GET 360');
    const documents = [
      { title: 'Profile photo', file: 'me.png', kind: 'Photo' },
      { title: 'Leadership certificate', file: 'leadership_completion.pdf', kind: 'Certificate' },
      { title: 'Existing CV', file: 'resume_v1.pdf', kind: 'CV' },
    ].map((seededDocument) => {
      const onRecord = record.documents.find((row) => row.title === seededDocument.title);
      if (!onRecord) throw new Error(`The seeded upload "${seededDocument.title}" is missing.`);
      return {
        ...seededDocument,
        status: verdictLabel[onRecord.status] ?? onRecord.status,
        note: onRecord.review_note || '',
      };
    });

    await test.step("1. Open Test Student's full record with the eye on the roster", async () => {
      await page.goto('/admin/students');
      await page.getByRole('button', { name: `View ${SEED.student.name}'s record` }).click();
      await expect(
        heading(page, SEED.student.name),
        'TC-514 (arrange): the record is open',
      ).toBeAttached();
    });

    await test.step('2. Read the "Contact", "Profile as filled in" and "Faculty mentor" cards', async () => {
      await expect(
        card('Contact').getByRole('listitem').filter({ hasText: /^\s*City/ }),
        'TC-514 ER-1: City reads Bengaluru',
      ).toContainText(SEED.student.city);
      await expect(
        card('Profile as filled in')
          .locator('.rail-facts > div')
          .filter({ hasText: 'Career summary' })
          .getByRole('definition'),
        'TC-514 ER-1: the career summary as the student wrote it',
      ).toHaveText(SEED.student.summary);
      const mentor = card('Faculty mentor');
      await expect(
        mentor.locator('.mentor-row b'),
        'TC-514 ER-2: the card names Test Mentor',
      ).toHaveText(SEED.mentor.name);
      const current = mentor.locator('.mentor-spells li').first();
      await expect(
        current,
        'TC-514 ER-2: the newest entry under "Mentor history" is Test Mentor',
      ).toContainText(SEED.mentor.name);
      await expect(current.locator('.chip'), 'TC-514 ER-2: marked current').toHaveText('Current');
    });

    await test.step('3. Click the Academics tab', async () => {
      await tabs.getByRole('tab', { name: 'Academics', exact: true }).click();
      const semesterOne = page
        .getByRole('table', { name: 'Semester results' })
        .getByRole('row')
        .filter({ has: page.getByRole('cell', { name: '1', exact: true }) });
      await expect(
        semesterOne.getByRole('cell').nth(1),
        'TC-514 ER-3: semester 1 SGPA is 8.2',
      ).toHaveText('8.2');
      await expect(
        semesterOne.getByRole('cell').nth(2),
        'TC-514 ER-3: semester 1 CGPA is 8.2',
      ).toHaveText('8.2');
    });

    await test.step('4. Click the Documents tab', async () => {
      await tabs.getByRole('tab', { name: 'Documents', exact: true }).click();
      const table = page.getByRole('table', { name: `Documents uploaded by ${SEED.student.name}` });
      for (const document of documents) {
        const row = table.getByRole('row').filter({ hasText: document.file });
        await expect(row, `TC-514 ER-4: "${document.file}" is listed`).toHaveCount(1);
        await expect(row.getByRole('cell').nth(0), `TC-514 ER-4: kind ${document.kind}`).toHaveText(
          document.kind,
        );
        const file = row.getByRole('cell').nth(1);
        if (document.note) {
          await expect(file, "TC-514 ER-4: the reviewer's note under the file").toContainText(
            document.note,
          );
        } else {
          await expect(file, 'TC-514 ER-4: no note under the file').toHaveText(document.file);
        }
        await expect(
          row.getByRole('cell').nth(3),
          `TC-514 ER-4: status ${document.status}`,
        ).toHaveText(document.status);
      }
    });

    await test.step('5. Click the download button on the resume_v1.pdf row', async () => {
      const download = page.waitForEvent('download');
      await page
        .getByRole('table', { name: `Documents uploaded by ${SEED.student.name}` })
        .getByRole('link', { name: 'Download resume_v1.pdf' })
        .click();
      const file = await download;
      expect(file.suggestedFilename(), 'TC-514 ER-5: the CV is downloaded').toBe('resume_v1.pdf');
      const bytes = fs.readFileSync((await file.path()) ?? '');
      expect(bytes.subarray(0, 5).toString(), 'TC-514 ER-5: as a PDF').toBe('%PDF-');
    });
  });

  test('Removing a student and restoring them from the Removed list @TC-515', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const student = await createStudent(api);
    const reason = 'E2E: left the programme';
    const row = gridRow(page, student.studentId);
    const editDialog = page.getByRole('dialog', { name: `Edit ${student.name}` });
    const deleteDialog = page.getByRole('dialog', { name: `Remove or delete ${student.name}` });
    const removeButton = deleteDialog.getByRole('button', { name: 'Remove from the roster' });

    try {
      await test.step('1. Open /admin/students', async () => {
        await page.goto('/admin/students');
      });

      await test.step('2. Type the student\'s name into "Quick filter…"', async () => {
        await rosterSearch(page).fill(student.name);
        await expect(row, 'TC-515 (arrange): the new student is listed').toBeVisible();
      });

      await test.step('3. Click the pencil on their row', async () => {
        await page.getByRole('button', { name: `Edit ${student.name}`, exact: true }).click();
        await expect(editDialog, 'TC-515 (arrange): their edit dialog opens').toBeVisible();
      });

      await test.step('4. Click "Remove or delete…"', async () => {
        await editDialog.getByRole('button', { name: 'Remove or delete…' }).click();
        await expect(
          deleteDialog,
          'TC-515 ER-1: a "Remove or delete <name>" dialog opens',
        ).toBeVisible();
        await expect(
          deleteDialog.getByRole('radio', { name: /Remove — keep the record/ }),
          'TC-515 ER-1: "Remove — keep the record" is chosen',
        ).toBeChecked();
        for (const sentence of [
          'Off every list, picker and screen at once.',
          'Cannot sign in by any door — REEP password and Google both — and every device is signed out.',
          'Every record is kept exactly as it is: marks, uploads, notes, interviews, leave.',
          'Restore brings the account back with nothing lost.',
        ]) {
          await expect(deleteDialog, `TC-515 ER-1: it says "${sentence}"`).toContainText(sentence);
        }
        await expect(removeButton, 'TC-515 ER-1: Remove waits for a reason').toBeDisabled();
      });

      await test.step('5. Type ab into "Reason"', async () => {
        await deleteDialog.getByRole('textbox', { name: 'Reason' }).fill('ab');
        await expect(removeButton, 'TC-515 ER-2: two letters are not a reason').toBeDisabled();
      });

      await test.step(`6. Replace the reason with ${reason} and click "Remove from the roster"`, async () => {
        await deleteDialog.getByRole('textbox', { name: 'Reason' }).fill(reason);
        await expect(removeButton, 'TC-515 ER-3 (arrange): a reason enables Remove').toBeEnabled();
        await removeButton.click();
        await expect(deleteDialog, 'TC-515 ER-3: the dialog closes').toBeHidden();
        await expect(
          notice(
            page,
            `${student.name} is off every screen and cannot sign in. Every record is kept; ` +
              'Restore brings them back.',
          ),
          'TC-515 ER-3: the screen says what removing did',
        ).toBeVisible();
        await expect(row, 'TC-515 ER-3: the student leaves the roster').toHaveCount(0);
      });

      await test.step('7. Under "Status", choose "Removed · off the roster, record kept"', async () => {
        await page
          .getByRole('combobox', { name: 'Status', exact: true })
          .selectOption({ label: 'Removed · off the roster, record kept' });
        await expect(row, 'TC-515 ER-4: the student is on the Removed list').toBeVisible();
        await expectCell(
          gridCell(page, student.studentId, 'status'),
          'Removed',
          'TC-515 ER-4: their Status reads Removed',
        );
      });

      await test.step('8. Click the pencil on their row', async () => {
        await page.getByRole('button', { name: `Edit ${student.name}`, exact: true }).click();
        await expect(
          editDialog.getByRole('button', { name: 'Restore to the roster' }),
          'TC-515 ER-5: the dialog offers "Restore to the roster"',
        ).toBeVisible();
        await expect(
          editDialog.getByRole('button', { name: 'Remove or delete…' }),
          'TC-515 ER-5: and not "Remove or delete…"',
        ).toHaveCount(0);
        await expect(
          editDialog.getByRole('button', { name: 'Save', exact: true }),
          'TC-515 ER-5: Save is off for a removed student',
        ).toBeDisabled();
      });

      await test.step('9. Click "Restore to the roster"', async () => {
        await editDialog.getByRole('button', { name: 'Restore to the roster' }).click();
        await expect(editDialog, 'TC-515 ER-6: the dialog closes').toBeHidden();
        await expect(
          notice(
            page,
            `${student.name} is back on the screens with every record. They can sign in again.`,
          ),
          'TC-515 ER-6: the screen says they are back',
        ).toBeVisible();
        await expect(row, 'TC-515 ER-6: they leave the Removed list').toHaveCount(0);
      });
    } finally {
      await removeStudent(api, student.studentId);
    }
  });

  test("Disabling a student's account from their record and enabling it again @TC-541", async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const student = await createStudent(api);
    const reason = 'E2E: password shared with another student';
    const summary = page.getByRole('complementary', { name: 'Student summary' });
    const status = summary.locator('.chip-row .chip').first();
    const more = page.getByRole('button', { name: 'More', exact: true });
    const disableBox = page.getByRole('dialog', { name: `Disable ${student.name}'s account?` });
    const disableButton = disableBox.getByRole('button', { name: 'Disable account' });
    const enableBox = page.getByRole('alertdialog', { name: `Enable ${student.name}'s account?` });

    try {
      await test.step("1. Open the student's record at /admin/students/<id>", async () => {
        await page.goto(`/admin/students/${student.studentId}`);
        await expect(status, 'TC-541 (arrange): the record has loaded').toHaveText('Not signed in yet');
      });

      await test.step('2. Click "More", then "Disable account"', async () => {
        await more.click();
        await page.getByRole('menuitem', { name: 'Disable account' }).click();
        await expect(disableBox, "TC-541 ER-1: a \"Disable <name>'s account?\" box opens").toBeVisible();
        await expect(
          disableBox,
          'TC-541 ER-1: it says what the student will be told',
        ).toContainText(
          'they are told the account was disabled for a security reason and to contact the placement office',
        );
        await expect(disableButton, 'TC-541 ER-1: Disable waits for a reason').toBeDisabled();
      });

      await test.step(`3. Type ${reason} into "Reason" and click "Disable account" in the box`, async () => {
        await disableBox.getByRole('textbox', { name: 'Reason' }).fill(reason);
        await disableButton.click();
        await expect(disableBox, 'TC-541 ER-2: the box closes').toBeHidden();
        await expect(
          notice(page, `${student.email} can no longer sign in, and every device it held has been signed out.`),
          'TC-541 ER-2: the screen says what disabling did',
        ).toBeVisible();
        await expect(status, 'TC-541 ER-2: the status reads Disabled').toHaveText('Disabled');
        await expect(
          summary.locator('.account-line'),
          'TC-541 ER-2: the line under it gives the reason',
        ).toContainText(reason);
      });

      await test.step('4. Click "More", then "Enable account"', async () => {
        await more.click();
        await expect(
          page.getByRole('menuitem', { name: 'Disable account' }),
          'TC-541 ER-3: Disable is not offered twice',
        ).toHaveCount(0);
        await page.getByRole('menuitem', { name: 'Enable account' }).click();
        await expect(enableBox, "TC-541 ER-3: an \"Enable <name>'s account?\" box opens").toBeVisible();
      });

      await test.step('5. Click "Enable account" in the box', async () => {
        await enableBox.getByRole('button', { name: 'Enable account' }).click();
        await expect(enableBox, 'TC-541 ER-4: the box closes').toBeHidden();
        await expect(
          notice(page, `${student.email} can sign in again.`),
          'TC-541 ER-4: the screen says they can sign in again',
        ).toBeVisible();
        await expect(status, 'TC-541 ER-4: the status no longer reads Disabled').toHaveText(
          'Not signed in yet',
        );
      });
    } finally {
      // Removing keeps a disabled account disabled, which is all a throwaway needs.
      await removeStudent(api, student.studentId);
    }
  });

  // ============================================================= faculty ==

  test('The faculty list shows the seeded faculty member @TC-516', async ({ page, signIn }) => {
    await signIn('admin');
    const ids = await seeded(page.request);
    const mentees = (await mentorLoad(page.request, ids.mentorUserId)).menteeCount;
    const drawer = facultyDrawer(page);
    const cell = (colId: string) => gridCell(page, ids.mentorUserId, colId);

    await test.step('1. Open /admin/faculty', async () => {
      await page.goto('/admin/faculty');
      await expect(
        heading(page, 'Faculty'),
        'TC-516 ER-1: the page is headed Faculty',
      ).toBeVisible();
      await expect(cell('faculty'), 'TC-516 ER-1: Test Mentor is listed').toContainText(
        SEED.mentor.name,
      );
      await expect(cell('faculty'), 'TC-516 ER-1: with their address').toContainText(
        SEED.mentor.email,
      );
      await expect(cell('department'), 'TC-516 ER-1: Department').toHaveText('Not filed');
      await expect(cell('designation'), 'TC-516 ER-1: Designation').toHaveText('Not on record');
      await expect(cell('functions'), 'TC-516 ER-1: Mentor group').toHaveText(
        `Mentor · ${mentees}`,
      );
      await expect(cell('status'), 'TC-516 ER-1: Status').toHaveText('Active');
    });

    await test.step("2. Click Test Mentor's row", async () => {
      await page.getByRole('button', { name: `Open ${SEED.mentor.name}` }).click();
      await expect(drawer, 'TC-516 ER-2: a side panel opens for them').toContainText(
        SEED.mentor.name,
      );
      for (const tab of ['Profile', 'Access', 'Sessions']) {
        await expect(
          drawer.getByRole('button', { name: tab, exact: true }),
          `TC-516 ER-2: a "${tab}" tab`,
        ).toBeVisible();
      }
      await expect(drawer.getByRole('textbox', { name: 'Name' }), 'TC-516 ER-2: Name').toHaveValue(
        SEED.mentor.name,
      );
      await expect(
        drawer.getByRole('textbox', { name: 'Official email' }),
        'TC-516 ER-2: Official email',
      ).toHaveValue(SEED.mentor.email);
      await expect(
        drawer.getByRole('textbox', { name: 'College' }),
        'TC-516 ER-2: College',
      ).toHaveValue('Not filed');
    });

    await test.step('3. Click the Access tab', async () => {
      await drawer.getByRole('button', { name: 'Access', exact: true }).click();
      await expect(drawer, 'TC-516 ER-3: their mentor group is counted').toContainText(
        `${plural(mentees, 'mentee')} in their group.`,
      );
      await expect(
        drawer.getByRole('link', { name: 'Who can do what' }),
        'TC-516 ER-3: access is given on Who can do what',
      ).toHaveAttribute('href', '/admin/governance');
    });
  });

  test('Adding a faculty member hands over an activation link @TC-517', async ({
    page,
    signIn,
    baseURL,
  }) => {
    await signIn('admin');
    const api = page.request;
    const tag = nextTag();
    const name = `E2E Faculty ${tag}`;
    const email = `e2e.faculty.${tag}@bgscet.ac.in`;
    const wizard = page.locator('[data-p="faculty-new"]');
    let createdUserId: string | null = null;
    const colleges = await ok<{ code: string; email_domains: string[] }[]>(
      await api.get('/api/admin/colleges'),
      'GET /api/admin/colleges',
    );
    const domains = colleges.find((college) => college.code === 'BGSCET')?.email_domains ?? [];
    const department = wizard.getByRole('combobox', { name: /^Department/ });

    try {
      await test.step('1. Open /admin/faculty', async () => {
        await page.goto('/admin/faculty');
      });

      await test.step('2. Click "Add faculty"', async () => {
        await page.getByRole('link', { name: 'Add faculty' }).click();
        await expect(page, 'TC-517 ER-1: the wizard opens at /admin/faculty/new').toHaveURL(
          /\/admin\/faculty\/new$/,
        );
        await expect(
          heading(page, 'Add faculty member'),
          'TC-517 ER-1: headed "Add faculty member"',
        ).toBeVisible();
        await expect(wizard, 'TC-517 ER-1: step 1 of 3').toContainText('Step 1 of 3');
        await expect(
          wizard.getByRole('combobox', { name: /^College/ }).locator('option', {
            hasText: SEED.collegeOption,
          }),
          'TC-517 ER-1: BGSCET is offered under College',
        ).toHaveCount(1);
      });

      await test.step('3. Under "College", choose "BGSCET · BGS College of Engineering and Technology"', async () => {
        await wizard
          .getByRole('combobox', { name: /^College/ })
          .selectOption({ label: SEED.collegeOption });
        await expect(department, 'TC-517 ER-2: Department can be chosen now').toBeEnabled();
        await expect(
          department.locator('option', { hasText: SEED.departmentOption }),
          "TC-517 ER-2: and offers the college's MGMT department",
        ).toHaveCount(1);
      });

      await test.step('4. Under "Department", choose "MGMT · Department of Management Studies"', async () => {
        await department.selectOption({ label: SEED.departmentOption });
      });

      await test.step('5. Click Continue', async () => {
        await wizard.getByRole('button', { name: 'Continue' }).click();
        await expect(wizard, 'TC-517 ER-3: step 2 of 3').toContainText('Step 2 of 3');
        await expect(wizard, 'TC-517 ER-3: which addresses the college admits').toContainText(
          domains.length === 0
            ? 'BGSCET has no domains of its own, so the deployment’s list applies.'
            : `BGSCET admits addresses on ${domains.join(', ')}.`,
        );
        await expect(wizard, 'TC-517 ER-3: "Filed under" names the department').toContainText(
          SEED.departmentOption,
        );
      });

      await test.step('6. Enter the name in "Full name" and the address in "College email"', async () => {
        await wizard.getByRole('textbox', { name: /^Full name/ }).fill(name);
        await wizard.getByRole('textbox', { name: /^College email/ }).fill(email);
      });

      await test.step('7. Click Continue', async () => {
        await wizard.getByRole('button', { name: 'Continue' }).click();
        await expect(wizard, 'TC-517 ER-4: step 3 of 3').toContainText('Step 3 of 3');
        const review = wizard.locator('.summary-card').filter({ hasText: 'Review' });
        for (const [label, value] of [
          ['Name', name],
          ['College email', email],
          ['College', SEED.collegeOption],
          ['Department', SEED.departmentOption],
          ['Designation', '—'],
          ['Role', 'Faculty · no functions'],
        ]) {
          await expect(
            review
              .locator('div')
              .filter({ has: page.getByText(label, { exact: true }) })
              .locator('dd'),
            `TC-517 ER-4: the review lists ${label}`,
          ).toHaveText(value);
        }
      });

      await test.step('8. Click "Create account & invite"', async () => {
        await wizard.getByRole('button', { name: 'Create account & invite' }).click();
        await expect(
          notice(
            page,
            `${name} now has a Faculty account on ${email}. Nothing can sign in to it until ` +
              'they redeem the invitation and set their own password.',
          ),
          'TC-517 ER-5: the account is created',
        ).toBeVisible();
        await expect(
          notice(
            page,
            `No mail was sent. The link below is the only way ${name} gets in — hand it over ` +
              'yourself. It expires in 168 hours and is shown once.',
          ),
          'TC-517 ER-5: the link is shown because no mail was sent',
        ).toBeVisible();
        await expect(
          wizard.locator('.activation-link'),
          'TC-517 ER-5: the activation link is on screen',
        ).toHaveText(new RegExp(`^${baseURL ?? ''}/activate\\?token=\\S+$`));
        await expect(
          wizard.getByRole('button', { name: 'Add another' }),
          'TC-517 ER-5: "Add another" is offered',
        ).toBeVisible();
      });

      await test.step('9. Click Done', async () => {
        await wizard.getByRole('link', { name: 'Done' }).click();
        await expect(page, 'TC-517 ER-6: the faculty list opens').toHaveURL(/\/admin\/faculty$/);
        const listed = await ok<{ user_id: string; email: string }[]>(
          await api.get('/api/admin/faculty'),
          'GET /api/admin/faculty',
        );
        createdUserId = listed.find((row) => row.email === email)?.user_id ?? null;
        expect(createdUserId, 'TC-517 ER-6: the account exists').not.toBeNull();
        await facultySearch(page).fill(name);
        const cell = (colId: string) => gridCell(page, createdUserId ?? '', colId);
        await expect(
          cell('faculty'),
          'TC-517 ER-6: the new faculty member is listed',
        ).toContainText(email);
        await expect(cell('department'), 'TC-517 ER-6: filed under the department').toHaveText(
          SEED.department,
        );
        await expect(cell('designation'), 'TC-517 ER-6: no designation').toHaveText(
          'Not on record',
        );
        await expect(cell('functions'), 'TC-517 ER-6: no mentor group yet').toHaveText(
          'No mentor group',
        );
        await expect(cell('status'), 'TC-517 ER-6: Status Active').toHaveText('Active');
      });
    } finally {
      if (createdUserId === null) {
        const listed = await ok<{ user_id: string; email: string }[]>(
          await api.get('/api/admin/faculty'),
          'GET /api/admin/faculty',
        );
        createdUserId = listed.find((row) => row.email === email)?.user_id ?? null;
      }
      if (createdUserId !== null) await removeUser(api, createdUserId);
    }
  });

  test('Adding a faculty member refuses missing or invalid details @TC-518', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const wizard = page.locator('[data-p="faculty-new"]');
    const visitor = 'visitor@example.com';
    // The refusal lists the college's own domains, or the deployment's when it
    // has none; another module may have given BGSCET a domain of its own.
    const colleges = await ok<{ code: string; email_domains: string[] }[]>(
      await page.request.get('/api/admin/colleges'),
      'GET /api/admin/colleges',
    );
    const ownDomains = colleges.find((college) => college.code === 'BGSCET')?.email_domains ?? [];
    const listed = ownDomains.length > 0 ? [...ownDomains].sort().join(', ') : 'bgscet.ac.in';

    await test.step('1. Open /admin/faculty/new', async () => {
      await page.goto('/admin/faculty/new');
      await expect(wizard, 'TC-518 (arrange): step 1 of 3').toContainText('Step 1 of 3');
    });

    await test.step('2. Under "College", choose "BGSCET · BGS College of Engineering and Technology"', async () => {
      await wizard
        .getByRole('combobox', { name: /^College/ })
        .selectOption({ label: SEED.collegeOption });
      await expect(
        wizard.getByRole('combobox', { name: /^Department/ }),
        'TC-518 (arrange): Department can be chosen now',
      ).toBeEnabled();
    });

    await test.step('3. Click Continue without choosing a department', async () => {
      await wizard.getByRole('button', { name: 'Continue' }).click();
      await expect(
        alert(
          page,
          'A department is required: it is how this account reaches its college, and the ' +
            'college decides which addresses may hold one.',
        ),
        'TC-518 ER-1: a department is required',
      ).toBeVisible();
      await expect(wizard, 'TC-518 ER-1: the wizard stays on step 1').toContainText('Step 1 of 3');
    });

    await test.step('4. Under "Department", choose "MGMT · Department of Management Studies" and click Continue', async () => {
      await wizard
        .getByRole('combobox', { name: /^Department/ })
        .selectOption({ label: SEED.departmentOption });
      await wizard.getByRole('button', { name: 'Continue' }).click();
      await expect(wizard, 'TC-518 (arrange): step 2 of 3').toContainText('Step 2 of 3');
    });

    await test.step('5. Click Continue with "Full name" and "College email" empty', async () => {
      await wizard.getByRole('button', { name: 'Continue' }).click();
      await expect(
        alert(page, 'A name is required.'),
        'TC-518 ER-2: a name is required',
      ).toBeVisible();
      await expect(
        alert(page, 'Type a full email address — this is the address they sign in with.'),
        'TC-518 ER-2: an address is required',
      ).toBeVisible();
      await expect(wizard, 'TC-518 ER-2: the wizard stays on step 2').toContainText('Step 2 of 3');
    });

    await test.step('6. Enter E2E Visitor in "Full name" and visitor@example.com in "College email", then click Continue', async () => {
      await wizard.getByRole('textbox', { name: /^Full name/ }).fill('E2E Visitor');
      await wizard.getByRole('textbox', { name: /^College email/ }).fill(visitor);
      await wizard.getByRole('button', { name: 'Continue' }).click();
      await expect(wizard, 'TC-518 (arrange): step 3 of 3').toContainText('Step 3 of 3');
    });

    await test.step('7. Click "Create account & invite"', async () => {
      await wizard.getByRole('button', { name: 'Create account & invite' }).click();
      await expect(
        alert(
          page,
          `${visitor} is not on this college's domains (${listed}). Tick "outside the ` +
            'college domain" and give a reason if that is deliberate.',
        ),
        'TC-518 ER-3: an address off the college domain is refused',
      ).toBeVisible();
      await expect(
        wizard.getByRole('button', { name: 'Create account & invite' }),
        'TC-518 ER-3: nothing was created, so the create button is still offered',
      ).toBeVisible();
    });

    await test.step('8. Click Back', async () => {
      await wizard.getByRole('button', { name: 'Back' }).click();
      await expect(wizard, 'TC-518 (arrange): back on step 2').toContainText('Step 2 of 3');
    });

    await test.step('9. Replace "College email" with mentor@bgscet.ac.in and click Continue', async () => {
      await wizard.getByRole('textbox', { name: /^College email/ }).fill(SEED.mentor.email);
      await wizard.getByRole('button', { name: 'Continue' }).click();
    });

    await test.step('10. Click "Create account & invite"', async () => {
      await wizard.getByRole('button', { name: 'Create account & invite' }).click();
      await expect(
        alert(page, `${SEED.mentor.email} already belongs to a MENTOR account.`),
        'TC-518 ER-4: an address that already has an account is refused',
      ).toBeVisible();
    });
  });

  test("Editing a faculty member's profile @TC-519", async ({ page, signIn }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    const drawer = facultyDrawer(page);

    try {
      await test.step('1. Open /admin/faculty', async () => {
        await page.goto('/admin/faculty');
        await facultySearch(page).fill(faculty.name);
      });

      await test.step("2. Click the new faculty member's row", async () => {
        await page.getByRole('button', { name: `Open ${faculty.name}` }).click();
        await expect(
          drawer.getByRole('textbox', { name: 'Name' }),
          'TC-519 ER-1: Name',
        ).toHaveValue(faculty.name);
        await expect(
          drawer.getByRole('textbox', { name: 'Official email' }),
          'TC-519 ER-1: Official email',
        ).toHaveValue(faculty.email);
        await expect(
          drawer.getByRole('textbox', { name: 'College' }),
          'TC-519 ER-1: College, read from the department',
        ).toHaveValue(SEED.college);
        await expect(
          drawer.getByRole('combobox', { name: 'Department' }).locator('option:checked'),
          'TC-519 ER-1: Department',
        ).toHaveText(`${SEED.department} · BGSCET`);
      });

      await test.step('3. Type Assistant Professor into "Designation" and click "Save changes"', async () => {
        await drawer.getByRole('textbox', { name: 'Designation' }).fill('Assistant Professor');
        await drawer.getByRole('button', { name: 'Save changes' }).click();
        await expect(
          notice(page, `Saved ${faculty.name}.`),
          'TC-519 ER-2: the screen says it saved',
        ).toBeVisible();
        await expectCell(
          gridCell(page, faculty.userId, 'designation'),
          'Assistant Professor',
          'TC-519 ER-2: the Designation column reads Assistant Professor',
        );
        await expect(
          drawer,
          'TC-519 ER-2: the panel names them with the new designation',
        ).toContainText(`Assistant Professor · ${SEED.department} · ${SEED.college}`);
      });
    } finally {
      await removeUser(api, faculty.userId);
    }
  });

  test('A new activation link replaces the earlier one @TC-520', async ({
    page,
    signIn,
    browser,
    baseURL,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    const drawer = facultyDrawer(page);
    const other = await browser.newContext({ baseURL, viewport: { width: 1920, height: 1080 } });
    const outside = await other.newPage();
    let newLink = '';
    const setPassword = async () => {
      await outside.getByLabel('New password', { exact: true }).fill(FACULTY_PASSWORD);
      await outside.getByLabel('Type it again', { exact: true }).fill(FACULTY_PASSWORD);
      await outside.getByRole('button', { name: 'Set password and sign in' }).click();
    };

    try {
      await test.step('1. Open /admin/faculty', async () => {
        await page.goto('/admin/faculty');
        await facultySearch(page).fill(faculty.name);
      });

      await test.step("2. Click the new faculty member's row, then the Sessions tab", async () => {
        await page.getByRole('button', { name: `Open ${faculty.name}` }).click();
        await drawer.getByRole('button', { name: 'Sessions', exact: true }).click();
        await expect(drawer, 'TC-520 (arrange): the Sessions tab is open').toContainText(
          'A new link replaces every older one.',
        );
      });

      await test.step('3. Click "Make a sign-in link"', async () => {
        await drawer.getByRole('button', { name: 'Make a sign-in link' }).click();
        await expect(
          notice(page, `A new activation link for ${faculty.name} is ready to hand over.`),
          'TC-520 ER-1: the screen says a new link is ready',
        ).toBeVisible();
        const box = drawer.getByRole('textbox', { name: 'Hand this over' });
        await expect(box, 'TC-520 ER-1: the link is in "Hand this over"').toHaveValue(
          /\/activate\?token=\S+$/,
        );
        newLink = await box.inputValue();
        expect(newLink, 'TC-520 ER-1: it is a different link from the first').not.toBe(
          faculty.link,
        );
        await expect(
          drawer.locator('.chip').filter({ hasText: 'Expires in 168 h' }),
          'TC-520 ER-1: it expires in 168 hours',
        ).toBeVisible();
        await expect(
          drawer.locator('.chip').filter({ hasText: 'Mail is off — read it out' }),
          'TC-520 ER-1: no mail was sent',
        ).toBeVisible();
      });

      await test.step("4. In a private window, open the link from the account's creation and set a password", async () => {
        await outside.goto(faculty.link);
        await setPassword();
        await expect(
          outside.getByText(
            'This link has already been used. If that was not you, ask for a new one.',
          ),
          'TC-520 ER-2: the earlier link no longer works',
        ).toBeVisible();
        await expect(
          outside.getByText('Ask the placement office to send you a new activation link.'),
          'TC-520 ER-2: the page says who to ask',
        ).toBeVisible();
      });

      await test.step('5. In the private window, open the new link and set a password', async () => {
        await outside.goto(newLink);
        await setPassword();
        await expect(outside, 'TC-520 ER-3: they land on their notebook').toHaveURL(
          /\/mentor\/notebook$/,
        );
        await expect(
          heading(outside, 'Faculty notebook'),
          'TC-520 ER-3: signed in as faculty',
        ).toBeVisible();
      });
    } finally {
      await other.close();
      await removeUser(api, faculty.userId);
    }
  });

  test('Disabling a faculty account needs a reason, and enabling it again @TC-521', async ({
    page,
    signIn,
    browser,
    baseURL,
    playwright,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    await activate(playwright, baseURL ?? '', faculty.link);
    const reason = 'E2E: on sabbatical for a term';
    const drawer = facultyDrawer(page);
    const dialog = page.getByRole('dialog', { name: `Disable ${faculty.name}’s account` });
    const confirm = dialog.getByRole('button', { name: 'Disable account' });
    const status = gridCell(page, faculty.userId, 'status');
    const statusFilter = page.getByRole('combobox', { name: 'Status', exact: true });
    const other = await browser.newContext({ baseURL, viewport: { width: 1920, height: 1080 } });
    const outside = await other.newPage();

    try {
      await test.step('1. Open /admin/faculty', async () => {
        await page.goto('/admin/faculty');
        await facultySearch(page).fill(faculty.name);
        await expect(status, 'TC-521 (arrange): the account is Active').toHaveText('Active');
      });

      await test.step("2. Click the new faculty member's row", async () => {
        await page.getByRole('button', { name: `Open ${faculty.name}` }).click();
      });

      await test.step('3. Click "Disable account"', async () => {
        await drawer.getByRole('button', { name: 'Disable account' }).click();
        await expect(
          dialog,
          'TC-521 ER-1: a "Disable <name>’s account" dialog opens',
        ).toBeVisible();
        await expect(dialog, 'TC-521 ER-1: it says sign-in stops at once').toContainText(
          'Sign-in stops immediately — REEP password and Google both. Every device it holds is signed out.',
        );
        await expect(dialog, 'TC-521 ER-1: the reason is required').toContainText('Required.');
        await expect(confirm, 'TC-521 ER-1: Disable waits for a reason').toBeDisabled();
      });

      await test.step('4. Type ab into "Reason"', async () => {
        await dialog.getByRole('textbox', { name: 'Reason' }).fill('ab');
        await expect(confirm, 'TC-521 ER-2: two letters are not a reason').toBeDisabled();
      });

      await test.step(`5. Replace the reason with ${reason} and click "Disable account" in the dialog`, async () => {
        await dialog.getByRole('textbox', { name: 'Reason' }).fill(reason);
        await confirm.click();
        await expect(dialog, 'TC-521 ER-3: the dialog closes').toBeHidden();
        await expect(
          notice(
            page,
            `${faculty.email} can no longer sign in, and every device it held has been signed ` +
              'out. Nothing they wrote has been removed.',
          ),
          'TC-521 ER-3: the screen says what disabling did',
        ).toBeVisible();
        await expectCell(status, 'Disabled', 'TC-521 ER-3: Status reads Disabled', 'contains');
        await expect(drawer, 'TC-521 ER-3: the panel says when and why').toContainText(
          new RegExp(`Disabled on .+ — ${reason}`),
        );
      });

      await test.step('6. In a private window, sign in on /login as the faculty member with their password', async () => {
        await outside.goto('/login');
        await outside.getByRole('radio', { name: /^Faculty\b/ }).click();
        await outside.getByLabel('Institutional email', { exact: true }).fill(faculty.email);
        await outside.getByPlaceholder('Enter your password').fill(FACULTY_PASSWORD);
        await outside.getByRole('button', { name: 'Sign in', exact: true }).click();
        await expect(
          outside.getByRole('alert'),
          'TC-521 ER-4: the sign-in is refused with a message',
        ).toBeVisible();
        await expect(outside, 'TC-521 ER-4: the page stays on /login').toHaveURL(/\/login$/);
      });

      await test.step('7. Under "Status", choose "Disabled"', async () => {
        await statusFilter.selectOption({ label: 'Disabled' });
        await expectCell(
          status,
          'Disabled',
          'TC-521 ER-5: the account is listed under Disabled',
          'contains',
        );
      });

      await test.step('8. Click "Enable account" in the side panel', async () => {
        await drawer.getByRole('button', { name: 'Enable account' }).click();
        await expect(
          notice(
            page,
            `${faculty.email} can sign in again. Capability grants were not restored - grant ` +
              'what is still needed in Governance.',
          ),
          'TC-521 ER-6: the screen says the account can sign in again',
        ).toBeVisible();
        await expect(status, 'TC-521 ER-6: it leaves the Disabled list').toHaveCount(0);
      });

      await test.step('9. Under "Status", choose "All"', async () => {
        await statusFilter.selectOption({ label: 'All' });
        await expectCell(status, 'Active', 'TC-521 ER-7: Status reads Active');
      });
    } finally {
      await other.close();
      await removeUser(api, faculty.userId);
    }
  });

  test("The disable dialog says what happens to the faculty member's mentees @TC-522", async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    await ok(
      await api.post(`/api/admin/students/${ids.studentId}/mentor`, {
        data: { mentor_user_id: faculty.userId, reason: 'E2E: a mentee for the disable dialog' },
      }),
      'POST /api/admin/students/{id}/mentor',
    );
    const drawer = facultyDrawer(page);
    const dialog = page.getByRole('dialog', { name: `Disable ${faculty.name}’s account` });

    try {
      await test.step('1. Open /admin/faculty', async () => {
        await page.goto('/admin/faculty');
        await facultySearch(page).fill(faculty.name);
        await expect(
          gridCell(page, faculty.userId, 'functions'),
          'TC-522 (arrange): the new faculty member mentors one student',
        ).toHaveText('Mentor · 1');
      });

      await test.step('2. Click their row and click "Disable account"', async () => {
        await page.getByRole('button', { name: `Open ${faculty.name}` }).click();
        await drawer.getByRole('button', { name: 'Disable account' }).click();
        await expect(dialog, 'TC-522 (arrange): the disable dialog is open').toBeVisible();
        await expect
          .soft(dialog, 'TC-522 ER-1: the dialog does not say the mentee stays filed under them')
          .not.toContainText('still filed under them');
        await expect
          .soft(dialog, 'TC-522 ER-1: the dialog does not say the mentor group is kept')
          .not.toContainText('mentor group is NOT released');
      });

      await test.step('3. Type E2E: checking the mentee sentence into "Reason" and click "Disable account" in the dialog', async () => {
        await dialog
          .getByRole('textbox', { name: 'Reason' })
          .fill('E2E: checking the mentee sentence');
        await dialog.getByRole('button', { name: 'Disable account' }).click();
        await expect(
          notice(
            page,
            '1 student(s) were released back to the unassigned pool and need a new faculty member.',
          ),
          'TC-522 ER-2: disabling released the mentee',
        ).toBeVisible();
      });
    } finally {
      await restoreSeededStudent(api, ids);
      await removeUser(api, faculty.userId);
    }
  });

  test('A disabled faculty member is told their account is disabled @TC-523', async ({
    page,
    signIn,
    baseURL,
    playwright,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    await activate(playwright, baseURL ?? '', faculty.link);
    await ok(
      await api.post(`/api/admin/users/${faculty.userId}/disable`, {
        data: { reason: 'E2E: checking the sign-in message' },
      }),
      'POST /api/admin/users/{id}/disable',
    );
    // The login screen is tested signed out, as the faculty member meets it.
    await page.context().clearCookies();

    try {
      await test.step('1. Open /login', async () => {
        await page.goto('/login');
      });

      await test.step('2. Under "Choose your portal", select Faculty', async () => {
        await page.getByRole('radio', { name: /^Faculty\b/ }).click();
      });

      await test.step("3. Enter the faculty member's address and password", async () => {
        await page.getByLabel('Institutional email', { exact: true }).fill(faculty.email);
        await page.getByPlaceholder('Enter your password').fill(FACULTY_PASSWORD);
      });

      await test.step('4. Click Sign in', async () => {
        await page.getByRole('button', { name: 'Sign in', exact: true }).click();
        await expect(
          page.getByRole('alert').filter({
            hasText:
              'Your account has been disabled for security reasons. Contact the placement office (Main Admin) to resolve the issue.',
          }),
          'TC-523 ER-1: the page says the account is disabled',
        ).toBeVisible();
      });
    } finally {
      await signIn('admin');
      await removeUser(page.request, faculty.userId);
    }
  });

  test('Signing a faculty member out of every device @TC-524', async ({
    page,
    signIn,
    browser,
    baseURL,
    playwright,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    await activate(playwright, baseURL ?? '', faculty.link);
    const drawer = facultyDrawer(page);
    const theirs = await openBrowserAs(browser, baseURL ?? '', faculty.email, FACULTY_PASSWORD);

    try {
      await test.step('1. In a second browser, sign in as the new faculty member and open their notebook', async () => {
        await theirs.page.goto('/mentor/notebook');
        await expect(
          heading(theirs.page, 'Faculty notebook'),
          'TC-524 ER-1: the faculty member is signed in',
        ).toBeVisible();
      });

      await test.step('2. In the admin browser, open /admin/faculty', async () => {
        await page.goto('/admin/faculty');
        await facultySearch(page).fill(faculty.name);
      });

      await test.step("3. Click the faculty member's row, then the Sessions tab", async () => {
        await page.getByRole('button', { name: `Open ${faculty.name}` }).click();
        await drawer.getByRole('button', { name: 'Sessions', exact: true }).click();
      });

      await test.step('4. Click "Sign out everywhere" in the Sessions card', async () => {
        await drawer
          .locator('.drawer-card')
          .filter({ has: page.getByRole('heading', { name: 'Sessions', exact: true }) })
          .getByRole('button', { name: 'Sign out everywhere' })
          .click();
        await expect(
          notice(page, `Every device holding ${faculty.email} has been signed out.`),
          'TC-524 ER-2: the screen says every device was signed out',
        ).toBeVisible();
        await expect(
          gridCell(page, faculty.userId, 'status'),
          'TC-524 ER-2: the account itself stays Active',
        ).toHaveText('Active');
      });

      await test.step('5. In the second browser, reload the page', async () => {
        await theirs.page.reload();
        await expect(theirs.page, 'TC-524 ER-3: the faculty member is sent to sign in').toHaveURL(
          /\/login\?next=%2Fmentor%2Fnotebook/,
        );
      });
    } finally {
      await theirs.context.close();
      await removeUser(api, faculty.userId);
    }
  });

  test('A faculty member signed in when their account is disabled is told why @TC-542', async ({
    page,
    signIn,
    browser,
    baseURL,
    playwright,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    await activate(playwright, baseURL ?? '', faculty.link);
    const theirs = await openBrowserAs(browser, baseURL ?? '', faculty.email, FACULTY_PASSWORD);

    try {
      await test.step('1. In a second browser, sign in as the new faculty member and open their notebook', async () => {
        await theirs.page.goto('/mentor/notebook');
        await expect(
          heading(theirs.page, 'Faculty notebook'),
          'TC-542 ER-1: the faculty member is signed in',
        ).toBeVisible();
      });

      await test.step('2. In the admin browser, disable their account with the reason E2E: checking the signed-out note', async () => {
        await ok(
          await api.post(`/api/admin/users/${faculty.userId}/disable`, {
            data: { reason: 'E2E: checking the signed-out note' },
          }),
          'POST /api/admin/users/{id}/disable',
        );
      });

      await test.step('3. In the second browser, reload the page', async () => {
        await theirs.page.reload();
        await expect(
          theirs.page,
          'TC-542 ER-2: they are sent to sign in, and the address says why',
        ).toHaveURL(/\/login\?next=%2Fmentor%2Fnotebook&signedOut=disabled$/);
        await expect(
          theirs.page.getByRole('alert').filter({
            hasText:
              'Your account has been disabled for security reasons. Contact the placement office (Main Admin) to resolve the issue.',
          }),
          'TC-542 ER-2: the page says the account was disabled and who resolves it',
        ).toBeVisible();
      });
    } finally {
      await theirs.context.close();
      await removeUser(api, faculty.userId);
    }
  });

  test('Removing a faculty account and restoring it @TC-525', async ({ page, signIn }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    const reason = 'E2E: left the college';
    const drawer = facultyDrawer(page);
    const dialog = page.getByRole('dialog', { name: `Remove or delete ${faculty.name}` });
    const confirm = dialog.getByRole('button', { name: 'Remove from the roster' });
    const status = gridCell(page, faculty.userId, 'status');
    const statusFilter = page.getByRole('combobox', { name: 'Status', exact: true });

    try {
      await test.step('1. Open /admin/faculty', async () => {
        await page.goto('/admin/faculty');
        await facultySearch(page).fill(faculty.name);
        await expect(status, 'TC-525 (arrange): the account is listed').toHaveText('Active');
      });

      await test.step("2. Click the new faculty member's row", async () => {
        await page.getByRole('button', { name: `Open ${faculty.name}` }).click();
      });

      await test.step('3. Click "Remove or delete…"', async () => {
        await drawer.getByRole('button', { name: 'Remove or delete…' }).click();
        await expect(dialog, 'TC-525 ER-1: a "Remove or delete <name>" dialog opens').toBeVisible();
        await expect(
          dialog.getByRole('radio', { name: /Remove — keep the record/ }),
          'TC-525 ER-1: "Remove — keep the record" is chosen',
        ).toBeChecked();
        await expect(dialog, 'TC-525 ER-1: it says their mentees are released').toContainText(
          'Their mentees are released to the unassigned pool and need a new faculty member.',
        );
        await expect(confirm, 'TC-525 ER-1: Remove waits for a reason').toBeDisabled();
      });

      await test.step(`4. Type ${reason} into "Reason" and click "Remove from the roster"`, async () => {
        await dialog.getByRole('textbox', { name: 'Reason' }).fill(reason);
        await confirm.click();
        await expect(dialog, 'TC-525 ER-2: the dialog closes').toBeHidden();
        await expect(
          notice(
            page,
            `${faculty.name} is off every screen and cannot sign in. Every record is kept; ` +
              'Restore brings them back.',
          ),
          'TC-525 ER-2: the screen says what removing did',
        ).toBeVisible();
        await expect(status, 'TC-525 ER-2: the account leaves the list').toHaveCount(0);
      });

      await test.step('5. Under "Status", choose "Removed · off the roster, record kept"', async () => {
        await statusFilter.selectOption({ label: 'Removed · off the roster, record kept' });
        await expectCell(
          status,
          'Removed',
          'TC-525 ER-3: the account is on the Removed list',
          'contains',
        );
      });

      await test.step('6. Click their row and click "Restore to the roster"', async () => {
        await page.getByRole('button', { name: `Open ${faculty.name}` }).click();
        await drawer.getByRole('button', { name: 'Restore to the roster' }).click();
        await expect(
          notice(
            page,
            `${faculty.name} is back on the screens with every record. They can sign in again.`,
          ),
          'TC-525 ER-4: the screen says they are back',
        ).toBeVisible();
      });

      await test.step('7. Under "Status", choose "All"', async () => {
        await statusFilter.selectOption({ label: 'All' });
        await expectCell(status, 'Active', 'TC-525 ER-5: the account is listed once, Active');
      });
    } finally {
      await removeUser(api, faculty.userId);
    }
  });

  test('Deleting for good refuses a wrong code @TC-526', async ({ page, signIn }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    const drawer = facultyDrawer(page);
    const dialog = page.getByRole('dialog', { name: `Remove or delete ${faculty.name}` });
    const confirm = dialog.getByRole('button', { name: 'Delete for good' });

    try {
      await test.step('1. Open /admin/faculty', async () => {
        await page.goto('/admin/faculty');
        await facultySearch(page).fill(faculty.name);
      });

      await test.step('2. Click the new faculty member\'s row and click "Remove or delete…"', async () => {
        await page.getByRole('button', { name: `Open ${faculty.name}` }).click();
        await drawer.getByRole('button', { name: 'Remove or delete…' }).click();
        await expect(dialog, 'TC-526 (arrange): the dialog is open').toBeVisible();
      });

      await test.step('3. Choose "Delete for good"', async () => {
        await dialog.getByRole('radio', { name: /Delete for good/ }).check();
        await expect(
          dialog,
          'TC-526 ER-1: the dialog says the audit trail keeps the act',
        ).toContainText(
          "The audit trail keeps that this was done, by whom and why; the person's name leaves every other record.",
        );
        await expect(
          dialog.getByRole('textbox', { name: 'Code from your email' }),
          'TC-526 ER-1: it asks for the code from the email',
        ).toBeVisible();
        await expect(
          dialog.getByRole('button', { name: 'Email me a code' }),
          'TC-526 ER-1: and offers to email one',
        ).toBeVisible();
        await expect(
          confirm,
          'TC-526 ER-1: "Delete for good" waits for a reason and a code',
        ).toBeDisabled();
      });

      await test.step('4. Type E2E: duplicate account into "Reason" and 000000 into "Code from your email"', async () => {
        await dialog.getByRole('textbox', { name: 'Reason' }).fill('E2E: duplicate account');
        await dialog.getByRole('textbox', { name: 'Code from your email' }).fill('000000');
        await expect(confirm, 'TC-526 ER-2: "Delete for good" is enabled').toBeEnabled();
      });

      await test.step('5. Click "Delete for good"', async () => {
        await confirm.click();
        await expect(
          dialog.getByRole('alert').filter({
            hasText:
              "That code is not right, has expired, or was already used. Ask for a new one - it is emailed to the office account's own address.",
          }),
          'TC-526 ER-3: the wrong code is refused',
        ).toBeVisible();
        const listed = await ok<{ user_id: string }[]>(
          await api.get('/api/admin/faculty'),
          'GET /api/admin/faculty',
        );
        expect(
          listed.some((row) => row.user_id === faculty.userId),
          'TC-526 ER-3: the account is still there',
        ).toBe(true);
      });
    } finally {
      await removeUser(api, faculty.userId);
    }
  });

  // ============================================================= mentors ==

  test("The mentor load shows each faculty member's students against capacity @TC-528", async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const unassigned = (
      await ok<unknown[]>(await api.get('/api/admin/unassigned-students'), 'GET unassigned')
    ).length;
    const ids = await seeded(api);
    const load = await mentorLoad(api, ids.mentorUserId);
    const rail = page.getByRole('region', { name: /^Mentors · / });
    const testMentor = rail.getByRole('button', { name: /^TM Test Mentor\b/ });

    await test.step('1. Open /admin/mentors', async () => {
      await page.goto('/admin/mentors');
      await expect(
        heading(page, 'Assign faculty'),
        'TC-528 ER-1: headed "Assign faculty"',
      ).toBeVisible();
      const summary =
        unassigned === 0
          ? 'Every student has a faculty member.'
          : `${unassigned} ${unassigned === 1 ? 'student has' : 'students have'} no faculty member`;
      await expect(
        page.locator('.dt-sub'),
        'TC-528 ER-1: the line under the heading counts the students without a faculty member',
      ).toHaveText(summary);
      await expect(testMentor, 'TC-528 ER-2: Test Mentor is on the Mentors list').toBeVisible();
      await expect(testMentor, 'TC-528 ER-2: with their load against capacity').toContainText(
        `${load.menteeCount}/${load.capacity}`,
      );
      await expect(testMentor, 'TC-528 ER-2: and the places free').toContainText(
        load.menteeCount >= load.capacity
          ? `At capacity — ${load.capacity}, the programme default`
          : `${plural(load.capacity - load.menteeCount, 'place')} free of ${load.capacity}, the programme default`,
      );
      await expect(testMentor, 'TC-528 ER-2: and where they are filed').toContainText(
        'Department not on record',
      );
    });

    await test.step('2. Click Test Mentor on the Mentors list', async () => {
      await testMentor.click();
      await expect(testMentor, 'TC-528 ER-3: Test Mentor is selected').toHaveAttribute(
        'aria-pressed',
        'true',
      );
      await expect(
        rail.getByRole('heading', { name: `Current mentees · ${load.menteeCount}` }),
        'TC-528 ER-3: their current mentees are counted',
      ).toBeVisible();
      const mentee = rail.locator('.mentee').filter({ hasText: SEED.student.name });
      await expect(mentee, 'TC-528 ER-3: Test Student, with USN and stage').toContainText(
        `${SEED.student.usn} · ${SEED.student.stageLabel}`,
      );
      await expect(mentee, 'TC-528 ER-3: and three headline numbers').toContainText(
        /(\d+(\.\d+)?% attendance|Attendance not recorded) · \d+ verified skills? · [\d.,]+ h logged/,
      );
    });
  });

  test('Releasing a student and assigning them again, with the history @TC-529', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const rail = page.getByRole('region', { name: /^Mentors · / });
    const testMentor = rail.getByRole('button', { name: /^TM Test Mentor\b/ });
    const release = page.getByRole('button', {
      name: `Remove ${SEED.student.name} from ${SEED.mentor.name}`,
    });
    const menteeRow = rail.locator('.mentee').filter({ hasText: SEED.student.name });
    const releaseReason = menteeRow.getByRole('textbox', {
      name: `Why is ${SEED.student.name} being released?`,
    });
    const confirmRelease = menteeRow.getByRole('button', { name: 'Release', exact: true });
    const reason = page.getByRole('textbox', { name: 'Reason', exact: true });
    const poolRow = page.locator('.pool-grid').locator(`.ag-row[row-id="${ids.studentId}"]`);
    const assign = page.getByRole('button', { name: `Assign 1 selected to ${SEED.mentor.name}` });
    const load = await mentorLoad(api, ids.mentorUserId);
    const released = 'E2E: moving between groups';
    const reseated = 'E2E: back with their mentor';

    try {
      await test.step('1. Open /admin/mentors', async () => {
        await page.goto('/admin/mentors');
      });

      await test.step('2. Click Test Mentor on the Mentors list', async () => {
        await testMentor.click();
        await expect(release, 'TC-529 (arrange): Test Student is in their group').toBeVisible();
      });

      await test.step("3. Click the release button on Test Student's row", async () => {
        await release.click();
        await expect(
          releaseReason,
          'TC-529 ER-1: a box on Test Student\'s row asks why they are being released',
        ).toBeVisible();
        await expect(
          confirmRelease,
          'TC-529 ER-1: "Release" is disabled until a reason is typed',
        ).toBeDisabled();
        await expect(release, 'TC-529 ER-1: Test Student is still in the group').toBeVisible();
      });

      await test.step(`4. Type ${released} into "Why is Test Student being released?" and click "Release"`, async () => {
        await releaseReason.fill(released);
        await confirmRelease.click();
        await expect(
          notice(page, `${SEED.student.name} released from ${SEED.mentor.name}`),
          'TC-529 ER-2: the screen says the student was released',
        ).toBeVisible();
        await expect(
          poolRow,
          'TC-529 ER-2: Test Student is in "Unassigned students"',
        ).toBeVisible();
        await expect(testMentor, 'TC-529 ER-2: Test Mentor holds one student fewer').toContainText(
          `${load.menteeCount - 1}/${load.capacity}`,
        );
      });

      await test.step('5. Tick Test Student in "Unassigned students"', async () => {
        await poolRow.locator('[col-id="name"]').click();
        await expect(
          assign,
          'TC-529 ER-3: the button names the count and Test Mentor',
        ).toBeVisible();
        await expect(assign, 'TC-529 ER-3: and waits for a reason').toBeDisabled();
      });

      await test.step(`6. Type ${reseated} into "Reason" and click "Assign 1 selected to Test Mentor"`, async () => {
        await reason.fill(reseated);
        await assign.click();
        await expect(
          notice(page, `1 student assigned to ${SEED.mentor.name}`),
          'TC-529 ER-4: the screen says the student was assigned',
        ).toBeVisible();
        await expect(release, 'TC-529 ER-4: Test Student is back in the group').toBeVisible();
        await expect(testMentor, 'TC-529 ER-4: Test Mentor holds as many as before').toContainText(
          `${load.menteeCount}/${load.capacity}`,
        );
      });

      await test.step("7. Click the history button on Test Student's row", async () => {
        await page
          .getByRole('button', { name: `Assignment history for ${SEED.student.name}` })
          .click();
        const history = page.getByRole('region', {
          name: `Assignment history · ${SEED.student.name}`,
        });
        const spells = history.locator('.spell');
        await expect(
          spells.first(),
          'TC-529 ER-5: the newest spell is the current one',
        ).toContainText('Current');
        await expect(
          spells.first(),
          'TC-529 ER-5: assigned by the Main Admin, with the reason',
        ).toContainText(`Assigned by ${SEED.admin.name} — “${reseated}”`);
        await expect(spells.nth(1), 'TC-529 ER-5: the one before it has ended').toContainText(
          'Ended',
        );
        await expect(
          spells.nth(1),
          'TC-529 ER-5: released by the Main Admin, with the reason',
        ).toContainText(`Released to the unassigned pool by ${SEED.admin.name} — “${released}”`);
      });
    } finally {
      await restoreSeededStudent(api, ids);
    }
  });

  // ========================================================== governance ==

  test('Giving one kind of access with a reason, scope and dates, then removing it @TC-530', async ({
    page,
    signIn,
    browser,
    baseURL,
    playwright,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    await activate(playwright, baseURL ?? '', faculty.link);
    const theirs = await openBrowserAs(browser, baseURL ?? '', faculty.email, FACULTY_PASSWORD);
    const form = page.getByRole('region', { name: 'Give access' });
    const give = form.getByRole('button', { name: 'Give access' });
    const blocked = form.locator('.gv-blocked');
    const grantRow = page
      .getByRole('region', { name: 'Analytics', exact: true })
      .getByRole('row')
      .filter({ hasText: faculty.name });
    const grantReason = 'E2E: reviewing placement numbers this term';
    const extendReason = 'E2E: still reviewing the placement numbers';
    const revokeReason = 'E2E: the review of the numbers is finished';

    try {
      await test.step('1. Open /admin/governance', async () => {
        await page.goto('/admin/governance');
        await expect(give, 'TC-530 ER-1: "Give access" waits for a person').toBeDisabled();
        await expect(blocked, 'TC-530 ER-1: and says so').toHaveText('Choose at least one person');
      });

      await test.step('2. Under "Who", search for the new faculty member and tick them', async () => {
        await form.getByRole('searchbox', { name: 'Search people' }).fill(faculty.email);
        await form.getByRole('checkbox', { name: faculty.name }).check();
        await expect(form, 'TC-530 ER-2: one person is chosen').toContainText('1 chosen');
        await expect(blocked, 'TC-530 ER-2: the form now asks for the access').toHaveText(
          'Choose at least one kind of access',
        );
      });

      await test.step('3. Under "What they can open", tick "Analytics"', async () => {
        const analytics = form.getByRole('checkbox', { name: /^Analytics\b/ });
        await analytics.check();
        await expect(
          analytics,
          'TC-530 ER-3: Analytics says which screen it opens',
        ).toHaveAccessibleName(/Opens “Charts & numbers”/);
      });

      await test.step('4. Under "Which students", choose "Department · Department of Management Studies"', async () => {
        await form
          .getByRole('combobox', { name: 'Which students' })
          .selectOption(`DEPARTMENT:${ids.departmentId}`);
        await expect(form, 'TC-530 ER-4: the summary names who, what and which students').toContainText(
          new RegExp(
            `1 person will get “Analytics” for department ${SEED.department} \\(\\d+ students?\\), with no end date\\.`,
          ),
        );
      });

      await test.step('5. Type E2E: short into "Reason"', async () => {
        await form.getByRole('textbox', { name: 'Reason' }).fill('E2E: short');
        await expect(give, 'TC-530 ER-5: a short reason is refused').toBeDisabled();
        await expect(blocked, 'TC-530 ER-5: and the form says how long it must be').toHaveText(
          'A reason of at least 20 characters is required',
        );
      });

      await test.step(`6. Set "Ends on" to a date 60 days ahead and replace the reason with ${grantReason}`, async () => {
        await form.getByLabel('Ends on (optional)').fill(isoDayFromToday(60));
        await form.getByRole('textbox', { name: 'Reason' }).fill(grantReason);
        await expect(give, 'TC-530 ER-6: "Give access" is enabled').toBeEnabled();
      });

      await test.step('7. Click "Give access"', async () => {
        await give.click();
        await expect(
          notice(page, `Gave “Analytics” to ${faculty.name}.`),
          'TC-530 ER-7: the access is given',
        ).toBeVisible();
        await expect(
          page.getByRole('tab', { name: /^Who has access/ }),
          'TC-530 ER-7: "Who has access" opens',
        ).toHaveAttribute('aria-selected', 'true');
        await expect(grantRow, 'TC-530 ER-7: they are listed under Analytics').toHaveCount(1);
        const cells = grantRow.getByRole('cell');
        await expect(cells.nth(1), 'TC-530 ER-7: the faculty member, as Faculty').toContainText(
          'Faculty',
        );
        await expect(cells.nth(2), 'TC-530 ER-7: Students is the department').toHaveText(
          `Department · ${SEED.department}`,
        );
        await expect(cells.nth(3), 'TC-530 ER-7: it has an end date').not.toHaveText('—');
        await expect(cells.nth(4), 'TC-530 ER-7: Status Active').toHaveText('Active');
      });

      await test.step("8. In the faculty member's browser, open /admin/analytics", async () => {
        await theirs.page.goto('/admin/analytics');
        await expect(
          heading(theirs.page, 'Charts & numbers'),
          'TC-530 ER-8: the grant works at once: Analytics opens for them',
        ).toBeVisible();
      });

      await test.step("9. Click Extend on the faculty member's row", async () => {
        await grantRow.getByRole('button', { name: 'Extend' }).click();
        await expect(
          page.getByRole('region', { name: 'Extend 1 grant' }),
          'TC-530 ER-9: an "Extend 1 grant" box opens',
        ).toBeVisible();
      });

      await test.step(`10. Set "New review date" to a date 10 days ahead, type ${extendReason} into "Reason" and click Extend`, async () => {
        const box = page.getByRole('region', { name: 'Extend 1 grant' });
        await box.getByLabel('New review date').fill(isoDayFromToday(10));
        await box.getByRole('textbox', { name: 'Reason' }).fill(extendReason);
        await box.getByRole('button', { name: 'Extend', exact: true }).click();
        await expect(
          notice(page, `Extended “Analytics” for ${faculty.name}.`),
          'TC-530 ER-10: the grant is extended',
        ).toBeVisible();
        await expect(
          grantRow.getByRole('cell').nth(4),
          'TC-530 ER-10: its Status reads "Review due"',
        ).toHaveText('Review due');
      });

      await test.step('11. Click the "Needs attention" tab', async () => {
        await page.getByRole('tab', { name: /^Needs attention/ }).click();
        const expiring = page.getByRole('region', {
          name: /^Ending or due for review in the next 30 days/,
        });
        const row = expiring.getByRole('row').filter({ hasText: faculty.name });
        await expect(row, 'TC-530 ER-11: the grant is on the review list').toHaveCount(1);
        await expect(row, 'TC-530 ER-11: as Analytics').toContainText('Analytics');
      });

      await test.step('12. Click the "Who has access" tab and click "Remove" on the faculty member\'s row', async () => {
        await page.getByRole('tab', { name: /^Who has access/ }).click();
        await grantRow.getByRole('button', { name: 'Remove', exact: true }).click();
        await expect(
          page.getByRole('region', { name: 'Remove access' }),
          'TC-530 ER-12: a "Remove access" box opens naming the grant',
        ).toContainText(faculty.name);
      });

      await test.step(`13. Type ${revokeReason} into "Reason" and click "Remove access"`, async () => {
        const box = page.getByRole('region', { name: 'Remove access' });
        await box.getByRole('textbox', { name: 'Reason' }).fill(revokeReason);
        await box.getByRole('button', { name: 'Remove access' }).click();
        await expect(
          notice(page, `Removed “Analytics” from ${faculty.name}.`),
          'TC-530 ER-13: the access is removed',
        ).toBeVisible();
        await expect(grantRow, 'TC-530 ER-13: it leaves the list').toHaveCount(0);
      });

      await test.step("14. In the faculty member's browser, reload /admin/analytics", async () => {
        await theirs.page.goto('/admin/analytics');
        await expect(
          theirs.page,
          'TC-530 ER-14: Analytics is closed to them again; they land on their notebook',
        ).toHaveURL(/\/mentor\/notebook$/);
      });
    } finally {
      await theirs.context.close();
      await revokeGrantsOf(api, faculty.userId);
      await removeUser(api, faculty.userId);
    }
  });

  test.describe('in India Standard Time', () => {
    test.use({ timezoneId: 'Asia/Kolkata' });

    test("A grant's end date reads back as the day that was typed @TC-531", async ({
      page,
      signIn,
    }) => {
      await signIn('admin');
      const api = page.request;
      const ids = await seeded(api);
      const faculty = await createFaculty(api, ids.departmentId);
      const form = page.getByRole('region', { name: 'Give access' });
      const grantRow = page
        .getByRole('region', { name: 'Analytics', exact: true })
        .getByRole('row')
        .filter({ hasText: faculty.name });
      const typed = isoDayFromToday(45);
      const [year, month, day] = typed.split('-').map(Number);

      try {
        await test.step('1. Open /admin/governance', async () => {
          await page.goto('/admin/governance');
        });

        await test.step('2. Under "Who", search for the new faculty member and tick them', async () => {
          await form.getByRole('searchbox', { name: 'Search people' }).fill(faculty.email);
          await form.getByRole('checkbox', { name: faculty.name }).check();
        });

        await test.step('3. Under "What they can open", tick "Analytics"', async () => {
          await form.getByRole('checkbox', { name: /^Analytics\b/ }).check();
        });

        await test.step('4. Set "Ends on" to a date 45 days ahead', async () => {
          await form.getByLabel('Ends on (optional)').fill(typed);
        });

        await test.step('5. Type E2E: checking the expiry date into "Reason" and click "Give access"', async () => {
          await form.getByRole('textbox', { name: 'Reason' }).fill('E2E: checking the expiry date');
          await form.getByRole('button', { name: 'Give access' }).click();
          await expect(
            notice(page, `Gave “Analytics” to ${faculty.name}.`),
            'TC-531 (arrange): granted',
          ).toBeVisible();
          // The day typed, in the format the screen prints dates, in this
          // browser's own calendar.
          const expected = await page.evaluate(
            ([y, m, d]) =>
              new Date(y, m - 1, d).toLocaleDateString(undefined, {
                day: '2-digit',
                month: 'short',
                year: 'numeric',
              }),
            [year, month, day],
          );
          await expect(
            grantRow.getByRole('cell').nth(3),
            'TC-531 ER-1: Ends shows the day that was typed',
          ).toHaveText(expected);
        });
      } finally {
        await revokeGrantsOf(api, faculty.userId);
        await removeUser(api, faculty.userId);
      }
    });
  });

  test('Giving two kinds of access to two people at once, then removing one from both @TC-540', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const first = await createFaculty(api, ids.departmentId);
    const second = await createFaculty(api, ids.departmentId);
    const form = page.getByRole('region', { name: 'Give access' });
    const analytics = page.getByRole('region', { name: 'Analytics', exact: true });
    const jobs = page.getByRole('region', { name: 'Jobs sheet', exact: true });
    const rowOf = (section: Locator, name: string): Locator =>
      section.getByRole('row').filter({ hasText: name });
    const grantReason = 'E2E: placement drive coordinators this term';
    const removeReason = 'E2E: the drive no longer needs the charts';

    try {
      await test.step('1. Open /admin/governance', async () => {
        await page.goto('/admin/governance');
      });

      await test.step('2. Under "Who", search for "E2E Faculty" and tick both new faculty members', async () => {
        await form.getByRole('searchbox', { name: 'Search people' }).fill('E2E Faculty');
        await form.getByRole('checkbox', { name: first.name }).check();
        await form.getByRole('checkbox', { name: second.name }).check();
        await expect(form, 'TC-540 ER-1: two people are chosen').toContainText('2 chosen');
      });

      await test.step('3. Under "What they can open", tick "Analytics" and "Jobs sheet"', async () => {
        await form.getByRole('checkbox', { name: /^Analytics\b/ }).check();
        await form.getByRole('checkbox', { name: /^Jobs sheet\b/ }).check();
        await expect(form, 'TC-540 ER-2: the summary counts both').toContainText(
          '2 people will get 2 kinds of access for all students, with no end date.',
        );
      });

      await test.step(`4. Type ${grantReason} into "Reason" and click "Give access"`, async () => {
        await form.getByRole('textbox', { name: 'Reason' }).fill(grantReason);
        await form.getByRole('button', { name: 'Give access' }).click();
        await expect(
          notice(page, 'Gave access: 4 grants.'),
          'TC-540 ER-3: four grants in one go',
        ).toBeVisible();
        for (const person of [first, second]) {
          await expect(rowOf(analytics, person.name), 'TC-540 ER-3: both under Analytics').toHaveCount(1);
          await expect(rowOf(jobs, person.name), 'TC-540 ER-3: both under Jobs sheet').toHaveCount(1);
        }
      });

      await test.step('5. Under "Analytics", tick both new faculty members and click "Remove Analytics from 2 people"', async () => {
        for (const person of [first, second]) {
          await analytics.getByRole('checkbox', { name: `Tick Analytics for ${person.name}` }).check();
        }
        await analytics.getByRole('button', { name: 'Remove Analytics from 2 people' }).click();
        const box = page.getByRole('region', { name: 'Remove access' });
        await expect(box, 'TC-540 ER-4: the box names the first').toContainText(first.name);
        await expect(box, 'TC-540 ER-4: and the second').toContainText(second.name);
      });

      await test.step(`6. Type ${removeReason} into "Reason" and click "Remove 2 grants"`, async () => {
        const box = page.getByRole('region', { name: 'Remove access' });
        await box.getByRole('textbox', { name: 'Reason' }).fill(removeReason);
        await box.getByRole('button', { name: 'Remove 2 grants' }).click();
        await expect(
          notice(page, 'Removed “Analytics” from 2 people.'),
          'TC-540 ER-5: one request removed both',
        ).toBeVisible();
        for (const person of [first, second]) {
          await expect(rowOf(analytics, person.name), 'TC-540 ER-5: gone from Analytics').toHaveCount(0);
          await expect(rowOf(jobs, person.name), 'TC-540 ER-5: still under Jobs sheet').toHaveCount(1);
        }
      });
    } finally {
      for (const person of [first, second]) {
        await revokeGrantsOf(api, person.userId);
        await removeUser(api, person.userId);
      }
    }
  });

  // ==================================================== feature switches ==

  test('Switching a student feature off for a batch and removing the rule @TC-532', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    // How many students the batch holds, as this screen counts them: every
    // student seated in it, removed ones included, which earlier modules leave.
    const spine = await ok<{ scope: string; id: string; students: number }[]>(
      await api.get('/api/admin/governance/hierarchy'),
      'GET /api/admin/governance/hierarchy',
    );
    const seated = spine.find((node) => node.scope === 'COHORT' && node.id === ids.cohortId);
    if (!seated) throw new Error('The seeded batch is not in the hierarchy.');
    const reach = plural(seated.students, 'student');
    const panel = page.getByRole('region', { name: 'Override' });
    const save = panel.getByRole('button', { name: 'Save', exact: true });
    const blocked = panel.locator('.fs-blocked');
    const leaderboards = page.getByRole('row').filter({ hasText: 'student.leaderboards' });
    const reason = 'E2E: leaderboards paused during the audit';
    const message = 'Leaderboards are paused this week.';

    try {
      await test.step('1. Open /admin/governance/features', async () => {
        await page.goto('/admin/governance/features');
        await expect(
          leaderboards.getByRole('cell').nth(3),
          'TC-532 (arrange): Leaderboards is on everywhere',
        ).toHaveText('On everywhere');
      });

      await test.step('2. Click "Leaderboards" in the table', async () => {
        await leaderboards.getByRole('button').click();
        await expect(
          panel.getByRole('heading', { name: 'Override · Leaderboards' }),
          'TC-532 ER-1: the panel is for Leaderboards',
        ).toBeVisible();
        await expect(panel, 'TC-532 ER-1: the switch is server-enforced').toContainText(
          'The API checks this switch before it serves the feature.',
        );
        await expect(panel, 'TC-532 ER-1: no rule yet').toContainText(
          'No rule on this feature — it is on for every student.',
        );
        await expect(save, 'TC-532 ER-1: Save waits for a target').toBeDisabled();
        await expect(blocked, 'TC-532 ER-1: and says so').toHaveText('Pick who this applies to.');
      });

      await test.step('3. Under "Scope", choose "Batch"', async () => {
        await panel.getByRole('combobox', { name: 'Scope' }).selectOption({ label: 'Batch' });
      });

      await test.step(`4. Under "Applies to", choose "${SEED.batch}"`, async () => {
        await panel.getByRole('combobox', { name: 'Applies to' }).selectOption(ids.cohortId);
        await expect(panel, 'TC-532 ER-2: the reach is counted').toContainText(
          `${reach} · beaten only by a student-level rule`,
        );
      });

      await test.step('5. Type E2E: short into "Reason"', async () => {
        await panel.getByRole('textbox', { name: /^Reason/ }).fill('E2E: short');
        await expect(save, 'TC-532 ER-3: a short reason is refused').toBeDisabled();
        await expect(blocked, 'TC-532 ER-3: and the panel says how long it must be').toHaveText(
          'A reason of at least 20 characters is required.',
        );
      });

      await test.step(`6. Replace the reason with ${reason}, type ${message} into "Student-facing message" and click Save`, async () => {
        await panel.getByRole('textbox', { name: /^Reason/ }).fill(reason);
        await panel.getByRole('textbox', { name: 'Student-facing message' }).fill(message);
        await save.click();
        await expect(
          notice(page, `Leaderboards is off for ${SEED.batchYear} — ${reach}.`),
          'TC-532 ER-4: the rule is saved',
        ).toBeVisible();
        await expect(
          leaderboards.getByRole('cell').nth(2),
          'TC-532 ER-4: the table says where it applies',
        ).toHaveText(`Batch · ${SEED.batchYear} · ${reach}`);
        await expect(leaderboards.getByRole('cell').nth(3), 'TC-532 ER-4: Value Off').toHaveText(
          'Off',
        );
        const rule = panel.locator('.fs-rule');
        await expect(
          panel.getByRole('heading', { name: '1 rule in force' }),
          'TC-532 ER-4: the panel lists one rule in force',
        ).toBeVisible();
        await expect(rule, 'TC-532 ER-4: on the batch').toContainText(`Batch · ${SEED.batchYear}`);
        await expect(rule, 'TC-532 ER-4: with the reason').toContainText(reason);
        await expect(rule, 'TC-532 ER-4: and what students are shown').toContainText(
          `Students are shown: “${message}”`,
        );
      });

      await test.step('7. Click "Remove override" on the rule', async () => {
        await panel.locator('.fs-rule').getByRole('button', { name: 'Remove override' }).click();
        await expect(
          panel.getByRole('status').filter({
            hasText: `Remove this rule? The next rung up decides again for ${reach}.`,
          }),
          'TC-532 ER-5: it asks before removing',
        ).toBeVisible();
      });

      await test.step('8. Click "Remove override" in the confirmation', async () => {
        await panel
          .locator('.fs-rule-confirm')
          .getByRole('button', { name: 'Remove override' })
          .click();
        await expect(
          notice(
            page,
            `Rule removed for ${SEED.batchYear}. The next rung up decides again for ${reach}.`,
          ),
          'TC-532 ER-6: the rule is removed',
        ).toBeVisible();
        await expect(
          leaderboards.getByRole('cell').nth(3),
          'TC-532 ER-6: Leaderboards is on everywhere again',
        ).toHaveText('On everywhere');
        await expect(panel, 'TC-532 ER-6: no rule is left').toContainText(
          'No rule on this feature — it is on for every student.',
        );
      });
    } finally {
      await clearLeaderboardRules(api);
    }
  });

  test('Every student feature switch is enforced by the API @TC-533', async ({ page, signIn }) => {
    await signIn('admin');
    const switches = [
      'Voice interviewer (Mock Interview)',
      'REEP Agent (chat)',
      'Resume Builder',
      'English baseline test',
      'Jobs feed & applications',
      'Leaderboards',
      'Document uploads',
      'Time allocation ledger',
      'Skilling & badges',
      'Mentor meeting log',
    ];
    const table = page.getByRole('table', { name: 'Student feature switches' });

    await test.step('1. Open /admin/governance/features', async () => {
      await page.goto('/admin/governance/features');
      await expect(
        page.getByText('All 10 switches are enforced by the API.'),
        'TC-533 ER-1: the headline says all ten are enforced',
      ).toBeVisible();
      for (const label of switches) {
        const row = table.getByRole('row').filter({ hasText: label });
        await expect(row, `TC-533 ER-1: "${label}" is listed`).toHaveCount(1);
        await expect(
          row.getByRole('cell').nth(4),
          `TC-533 ER-1: "${label}" is server-enforced`,
        ).toHaveText('Server-enforced');
      }
    });

    await test.step('2. Under "Enforcement", choose "Not wired yet"', async () => {
      await page.getByRole('combobox', { name: 'Enforcement' }).selectOption({
        label: 'Not wired yet',
      });
      await expect(
        table.getByText('No switch matches these filters.'),
        'TC-533 ER-2: no switch is unwired',
      ).toBeVisible();
    });
  });

  test("A student refused a switched-off feature reads the office's message @TC-534", async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const ids = await seeded(page.request);
    const message = 'Leaderboards are paused this week.';

    try {
      await test.step(`1. As the Main Admin, switch "Leaderboards" off for the batch "${SEED.batch}", with a student-facing message`, async () => {
        await ok(
          await page.request.put('/api/admin/governance/features', {
            data: {
              feature: 'student.leaderboards',
              scope: 'COHORT',
              target_id: ids.cohortId,
              enabled: false,
              reason: 'E2E: checking what the student is told',
              student_message: message,
            },
          }),
          'PUT /api/admin/governance/features',
        );
      });

      await test.step('2. Sign in as the student and open /student/leaderboards', async () => {
        await signIn('student');
        await page.goto('/student/leaderboards');
        await expect(
          page.getByText(message),
          "TC-534 ER-1: the student reads the office's message",
        ).toBeVisible();
      });
    } finally {
      await signIn('admin');
      await clearLeaderboardRules(page.request);
    }
  });

  // =============================================================== audit ==

  test('The audit trail shows a console change and opens its event @TC-535', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    const reason = 'E2E: an event for the trail';
    await ok(
      await api.post(`/api/admin/users/${faculty.userId}/disable`, { data: { reason } }),
      'POST /api/admin/users/{id}/disable',
    );
    const target = `user · ${faculty.userId}`;
    const eventCard = page.getByRole('complementary').filter({
      has: page.getByRole('heading', { name: 'Event', exact: true }),
    });

    try {
      await test.step('1. Open /admin/audit', async () => {
        await page.goto('/admin/audit');
        await expect(
          heading(page, 'What changed'),
          'TC-535 ER-1: headed "What changed"',
        ).toBeVisible();
        await expect(
          page.getByText('Every change made in this console, newest first.'),
          'TC-535 ER-1: newest first',
        ).toBeVisible();
        await expect(
          page.getByRole('combobox', { name: 'Date range' }).locator('option:checked'),
          'TC-535 ER-1: the last 7 days are shown',
        ).toHaveText('Last 7 days');
      });

      await test.step('2. Under "What", choose "DISABLE"', async () => {
        await page.getByRole('combobox', { name: 'Action' }).selectOption({ label: 'DISABLE' });
        const actions = page.locator('.ag-row [col-id="action"]');
        await expect(actions.first(), 'TC-535 ER-2: the trail shows DISABLE events').toHaveText(
          'DISABLE',
        );
        await expect(
          actions.filter({ hasNotText: /^DISABLE$/ }),
          'TC-535 ER-2: and nothing else',
        ).toHaveCount(0);
      });

      await test.step('3. Type the faculty member\'s id into "Filter the events on this page…"', async () => {
        await page
          .getByRole('searchbox', { name: 'Filter the events on this page' })
          .fill(faculty.userId);
        const row = page.locator('.ag-row').filter({ hasText: target });
        await expect(row, 'TC-535 ER-3: the disable is on the trail').toHaveCount(1);
        await expect(
          row.locator('[col-id="actorLabel"]'),
          'TC-535 ER-3: by the Main Admin',
        ).toHaveText(SEED.admin.name);
        await expect(row.locator('[col-id="action"]'), 'TC-535 ER-3: action DISABLE').toHaveText(
          'DISABLE',
        );
        await expect(
          row.locator('[col-id="route"]'),
          'TC-535 ER-3: through the disable route',
        ).toHaveText(`/api/admin/users/${faculty.userId}/disable`);
      });

      await test.step("4. Click the row whose Target is user · the faculty member's id", async () => {
        await page
          .locator('.ag-row')
          .filter({ hasText: target })
          .locator('[col-id="targetLabel"]')
          .click();
        await expect(eventCard, 'TC-535 ER-4: the event names who did it').toContainText(
          SEED.admin.name,
        );
        await expect(eventCard, 'TC-535 ER-4: what was done').toContainText('DISABLE');
        await expect(eventCard, 'TC-535 ER-4: to what').toContainText(target);
        const [before, after] = [
          eventCard.locator('.diff-side').nth(0),
          eventCard.locator('.diff-side').nth(1),
        ];
        await expect(before, 'TC-535 ER-4: before, no reason was recorded').toContainText(
          '"disable_reason": null',
        );
        await expect(after, 'TC-535 ER-4: after, the reason given').toContainText(
          `"disable_reason": "${reason}"`,
        );
      });

      await test.step('5. Click "Open Faculty"', async () => {
        await eventCard.getByRole('button', { name: 'Open Faculty' }).click();
        await expect(page, 'TC-535 ER-5: the Faculty screen opens').toHaveURL(/\/admin\/faculty$/);
      });
    } finally {
      await removeUser(api, faculty.userId);
    }
  });

  test('Exporting the audit trail as a CSV file @TC-536', async ({ page, signIn }) => {
    await signIn('admin');
    const api = page.request;
    const ids = await seeded(api);
    const faculty = await createFaculty(api, ids.departmentId);
    await ok(
      await api.post(`/api/admin/users/${faculty.userId}/disable`, {
        data: { reason: 'E2E: an event for the export' },
      }),
      'POST /api/admin/users/{id}/disable',
    );

    try {
      await test.step('1. Open /admin/audit', async () => {
        await page.goto('/admin/audit');
        await expect(
          page.getByRole('link', { name: 'Export range' }),
          'TC-536 (arrange): there are events to export',
        ).toBeVisible();
      });

      await test.step('2. Click "Export range"', async () => {
        const [download] = await Promise.all([
          page.waitForEvent('download'),
          page.getByRole('link', { name: 'Export range' }).click(),
        ]);
        expect(download.suggestedFilename(), 'TC-536 ER-1: the file is audit-log.csv').toBe(
          'audit-log.csv',
        );
        const text = fs.readFileSync(await download.path(), 'utf8');
        const lines = text.trim().split(/\r?\n/);
        expect(lines[0], 'TC-536 ER-1: the first line names the columns').toBe(
          'When,Actor,Actor email,Actor type,Action,Target type,Target id,Route,Request id',
        );
        const disable = lines.find(
          (line) => line.includes(faculty.userId) && line.includes(',DISABLE,'),
        );
        expect(disable, "TC-536 ER-1: the file has the faculty member's DISABLE row").toBeDefined();
        expect(disable, 'TC-536 ER-1: by the Main Admin').toContain(
          `${SEED.admin.name},${SEED.admin.email}`,
        );
      });

      await test.step('3. Reload the page', async () => {
        await page.reload();
        const newest = page.locator('.ag-row[row-index="0"]');
        await expect(
          newest.locator('[col-id="action"]'),
          'TC-536 ER-2: the newest event is the export itself',
        ).toHaveText('EXPORTED');
        await expect(
          newest.locator('[col-id="targetLabel"]'),
          'TC-536 ER-2: of the audit trail',
        ).toContainText('audit_export · ');
        await expect(
          newest.locator('[col-id="actorLabel"]'),
          'TC-536 ER-2: by the Main Admin',
        ).toHaveText(SEED.admin.name);
      });
    } finally {
      await removeUser(api, faculty.userId);
    }
  });

  // ============================================================ refusals ==

  test('A faculty member cannot open the admin console @TC-537', async ({ page, signIn }) => {
    await signIn('mentor');
    for (const [n, path] of [
      [1, '/admin'],
      [2, '/admin/students'],
      [3, '/admin/faculty'],
      [4, '/admin/governance'],
      [5, '/admin/audit'],
    ] as const) {
      await test.step(`${n}. Open ${path}`, async () => {
        await page.goto(path);
        await expect(page, `TC-537 ER-${n}: ${path} sends them to their notebook`).toHaveURL(
          /\/mentor\/notebook$/,
        );
        await expect(
          heading(page, 'Faculty notebook'),
          `TC-537 ER-${n}: the faculty notebook is shown, not the console`,
        ).toBeVisible();
      });
    }
  });

  test('A student cannot open the admin console @TC-538', async ({ page, signIn }) => {
    await signIn('student');
    for (const [n, path] of [
      [1, '/admin'],
      [2, '/admin/students'],
      [3, '/admin/governance'],
    ] as const) {
      await test.step(`${n}. Open ${path}`, async () => {
        await page.goto(path);
        await expect(page, `TC-538 ER-${n}: ${path} sends them to their home`).toHaveURL(
          /\/student$/,
        );
        await expect(
          heading(page, 'Welcome back, Test'),
          `TC-538 ER-${n}: the student home is shown, not the console`,
        ).toBeVisible();
      });
    }
  });
});
