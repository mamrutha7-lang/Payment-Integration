/**
 * Admin: college setup and interviews — the automated twins of the cases in
 * test-management/cases/08-admin-setup.md.
 *
 * THE TAG IS THE LINK. Each test's title ends with the `@TC-NNN` ID of the case
 * it automates, its top-level `test.step()` titles are that case's steps word
 * for word, and every assertion message names the expected result it checks.
 * The CSV reporter fails the run when the two drift apart.
 *
 * EVERYTHING A CASE CREATES CARRIES THIS RUN'S ID. A college, department,
 * course or batch cannot be deleted from the console without a code emailed to
 * the office, so the colleges these cases create stay on the database. Their
 * codes and names are unique to the run, and no case asserts a count that
 * another run's leftovers could change. What the console CAN undo (a track, a
 * question, a certification, a stage rule, a seat, a policy tick, a college
 * admin's grants) is undone at the end of the case that made it, in a
 * `finally` where a half-finished case would otherwise leave seeded data
 * changed.
 */
import { ACCOUNTS, block, expect, test } from './support/reep';
import type { APIResponse, Browser, Locator, Page } from '@playwright/test';

// ---------------------------------------------------------------- run data --

/** Unique to this run, upper-case so it survives the API's `code.upper()`. */
const RUN = Date.now().toString(36).toUpperCase();
let serial = 0;
/** A new code for this run: `E2E` + the run + a counter, at most 32 characters. */
function newCode(prefix = 'E2E'): string {
  serial += 1;
  return `${prefix}${RUN}${serial}`;
}

/** The dev seed's institution (apps/api-py/app/seed.py). */
const SEED = {
  collegeCode: 'BGSCET',
  collegeName: 'BGS College of Engineering and Technology',
  campus: 'Bengaluru',
  departmentCode: 'MGMT',
  departmentName: 'Department of Management Studies',
  departmentHead: 'Dr. Kavya N',
  courseCode: 'MBA',
  courseName: 'Master of Business Administration',
  specializationCode: 'FIN',
  specializationName: 'Finance',
  batchCode: 'MBA-2026-B',
  batchDisplay: 'Master of Business Administration - Finance · 2024-26 Section B',
  studentName: ACCOUNTS.student.name,
  studentUsn: '1BG24MBA001',
} as const;

// ------------------------------------------------------------- API helpers --
// Setup that is not a manual step goes through the same /api the console
// calls, as the signed-in Main Admin (`page.request` shares the page's cookie).

async function body<T>(response: APIResponse, what: string): Promise<T> {
  if (!response.ok()) {
    throw new Error(`${what} answered ${response.status()}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

interface Row {
  id: string;
}

async function createCollege(
  page: Page,
  college: { code: string; name: string; campus?: string; contact?: string; domains?: string[] },
): Promise<Row> {
  return body<Row>(
    await page.request.post('/api/admin/colleges', {
      data: {
        code: college.code,
        name: college.name,
        campus: college.campus ?? null,
        contact: college.contact ?? null,
        email_domains: college.domains ?? [],
      },
    }),
    'POST /api/admin/colleges',
  );
}

async function createDepartment(
  page: Page,
  collegeId: string,
  department: { code: string; name: string; head?: string },
): Promise<Row> {
  return body<Row>(
    await page.request.post(`/api/admin/colleges/${collegeId}/departments`, {
      data: { code: department.code, name: department.name, head: department.head ?? null },
    }),
    'POST a department',
  );
}

async function createCourse(
  page: Page,
  departmentId: string,
  course: { code: string; name: string; months?: number },
): Promise<Row> {
  return body<Row>(
    await page.request.post(`/api/admin/departments/${departmentId}/academic-courses`, {
      data: { code: course.code, name: course.name, duration_months: course.months ?? 24 },
    }),
    'POST a course',
  );
}

async function createSpecialization(
  page: Page,
  courseId: string,
  spec: { code: string; name: string },
): Promise<Row> {
  return body<Row>(
    await page.request.post(`/api/admin/academic-courses/${courseId}/academic-specializations`, {
      data: spec,
    }),
    'POST a specialization',
  );
}

async function createBatch(
  page: Page,
  departmentId: string,
  batch: {
    code: string;
    label: string;
    entry: string;
    completion: string;
    courseId?: string;
    specializationId?: string;
  },
): Promise<Row> {
  return body<Row>(
    await page.request.post(`/api/admin/departments/${departmentId}/cohorts`, {
      data: {
        code: batch.code,
        name: batch.label,
        batch_label: batch.label,
        degree_level: 'PG',
        entry_date: batch.entry,
        expected_completion: batch.completion,
        course_id: batch.courseId ?? null,
        specialization_id: batch.specializationId ?? null,
      },
    }),
    'POST a batch',
  );
}

/** A college with one department and one course under it, and optionally a
 *  specialization: the smallest spine most cases need. */
interface Spine {
  collegeId: string;
  collegeCode: string;
  collegeName: string;
  departmentId: string;
  courseId: string;
  specializationId: string | null;
}

async function createSpine(
  page: Page,
  options: { name: string; specialization?: { code: string; name: string } },
): Promise<Spine> {
  const collegeCode = newCode();
  const college = await createCollege(page, { code: collegeCode, name: options.name });
  const department = await createDepartment(page, college.id, {
    code: 'MGT',
    name: 'Management Studies',
  });
  const course = await createCourse(page, department.id, { code: 'MBA', name: 'General MBA' });
  const spec = options.specialization
    ? await createSpecialization(page, course.id, options.specialization)
    : null;
  return {
    collegeId: college.id,
    collegeCode,
    collegeName: options.name,
    departmentId: department.id,
    courseId: course.id,
    specializationId: spec?.id ?? null,
  };
}

/** "1 batch", "5 batches": the console's own plural (shared/text/plural.pipe.ts). */
function counted(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The seeded college as the API reports it now. Other modules' cases add
 *  people and applications to it, so its counts are read, never assumed. */
async function seededCollege(page: Page): Promise<{ id: string; department_count: number }> {
  const colleges = await body<{ id: string; code: string; department_count: number }[]>(
    await page.request.get('/api/admin/colleges'),
    'GET /api/admin/colleges',
  );
  return colleges.find((c) => c.code === SEED.collegeCode)!;
}

/** The seeded batch `MBA-2026-B`, found through the departments it hangs off. */
async function seededBatch(
  page: Page,
): Promise<{ id: string; department_id: string; course_id: string | null }> {
  const departments = await body<{ id: string; code: string; college_code: string }[]>(
    await page.request.get('/api/admin/departments'),
    'GET /api/admin/departments',
  );
  const department = departments.find(
    (d) => d.code === SEED.departmentCode && d.college_code === SEED.collegeCode,
  )!;
  const batches = await body<
    { id: string; code: string; department_id: string; course_id: string | null }[]
  >(
    await page.request.get(`/api/admin/departments/${department.id}/cohorts`),
    'GET the seeded batches',
  );
  return batches.find((b) => b.code === SEED.batchCode)!;
}

/**
 * Takes a mock interview as the seeded student, through the same socket the
 * interview room opens (`/api/interview?specialization=hr`), and returns once
 * the server has closed it. The student signs in in a SECOND browser context,
 * so the page under test stays the Main Admin's.
 *
 * On a development server with no AWS credentials the interviewer cannot reach
 * Bedrock, so the API writes the `interview_sessions` row and closes the socket
 * at once with 4002: a real record, status Failed. Should the engine ever
 * answer, the socket is closed as soon as it does, so the record is still one
 * short interview rather than an open one.
 *
 * The student has 20 attempts a day. When earlier runs have used them up, the
 * Main Admin gives them back first (TC-735's action) rather than letting the
 * socket refuse with 4015 and write nothing.
 */
/** The interview a case created: whose it was and the session's own id. */
interface TakenInterview {
  studentId: string;
  sessionId: string;
}

async function takeMockInterview(
  page: Page,
  browser: Browser,
  baseURL: string,
): Promise<TakenInterview> {
  const context = await browser.newContext({ baseURL });
  try {
    const student = await body<{ studentId: string }>(
      await context.request.post('/api/auth/login', {
        data: { email: ACCOUNTS.student.email, password: ACCOUNTS.student.password },
      }),
      'the student signing in',
    );
    const policy = await body<{ usage: { attempts: number; attempt_cap: number } }>(
      await context.request.get('/api/interview/policy'),
      'GET /api/interview/policy',
    );
    if (policy.usage.attempts >= policy.usage.attempt_cap) {
      await body(
        await page.request.post(`/api/admin/students/${student.studentId}/interview-cap/reset`, {
          data: { reason: 'Automated run: earlier runs used up the day’s practice attempts' },
        }),
        'resetting the interview cap',
      );
    }
    const consent = await body<{ version: string }>(
      await context.request.get('/api/interview/consent'),
      'GET /api/interview/consent',
    );
    await body(
      await context.request.post('/api/interview/consent', { data: { version: consent.version } }),
      'POST /api/interview/consent',
    );
    const tab = await context.newPage();
    // Any page on the app's origin: the socket's Origin must be the app's own.
    await tab.goto('/api/auth/sso/status');
    const closeCode = await tab.evaluate(
      () =>
        new Promise<number>((resolve) => {
          const socket = new WebSocket(
            `${location.origin.replace(/^http/, 'ws')}/api/interview?specialization=hr`,
          );
          socket.onmessage = () => socket.close(1000);
          socket.onclose = (event) => resolve(event.code);
        }),
    );
    if (closeCode === 4015 || closeCode === 4013 || closeCode === 1008) {
      throw new Error(`The interview socket refused the student (close ${closeCode}).`);
    }
    // The newest of the student's interviews is the one just taken: the cases
    // run one at a time, and other modules' interviews are older.
    const sessions = await body<{ id: string; started_at: string }[]>(
      await page.request.get(`/api/mentor/students/${student.studentId}/interviews`),
      'GET the student’s interviews',
    );
    const newest = sessions.reduce((a, b) => (a.started_at > b.started_at ? a : b));
    return { studentId: student.studentId, sessionId: newest.id };
  } finally {
    await context.close();
  }
}

/** An interview track made for one case, so the shipped four stay as seeded. */
interface Track {
  id: string;
  code: string;
  label: string;
}

async function createTrack(page: Page, label: string): Promise<Track> {
  serial += 1;
  const code = `t${RUN.toLowerCase()}${serial}`;
  const made = await body<{ track: { id: string } }>(
    await page.request.post('/api/admin/interview-questions/tracks', {
      data: {
        code,
        label,
        persona: 'an exacting but fair Head of Operations',
        sample_question: 'Walk me through how you would cut a supplier lead time by a week.',
        frameworks: ['SIPOC', 'OEE', 'root-cause analysis', 'Little’s Law'],
        nova_voice: 'matthew',
      },
    }),
    'POST a track',
  );
  return { id: made.track.id, code, label };
}

async function addQuestions(page: Page, track: Track, lines: string[]): Promise<void> {
  await body(
    await page.request.post('/api/admin/interview-questions/bulk', {
      data: { track: track.code, lines: lines.join('\n') },
    }),
    'POST questions in bulk',
  );
}

/** Deletes a case's track and every question left on it. */
async function removeTrack(page: Page, track: Track): Promise<void> {
  const questions = await page.request.get(
    `/api/admin/interview-questions?track=${encodeURIComponent(track.code)}`,
  );
  if (questions.ok()) {
    for (const question of (await questions.json()) as { id: string }[]) {
      await page.request.delete(`/api/admin/interview-questions/${question.id}`);
    }
  }
  await page.request.delete(`/api/admin/interview-questions/tracks/${track.id}`);
}

/** How many interviews the API lists for one Interview records filter, read
 *  the way the screen reads them (up to 200 on its first page). */
async function interviewsListed(page: Page, filter: Record<string, string>): Promise<number> {
  const query = new URLSearchParams({ ...filter, page_size: '200' });
  const listed = await body<{ rows: unknown[] }>(
    await page.request.get(`/api/admin/interviews?${query}`),
    'GET /api/admin/interviews',
  );
  return listed.rows.length;
}

/** The Interview records grid agrees with the API: the empty sentence when the
 *  filter matches nothing, otherwise the status bar's row count. */
async function expectListed(page: Page, count: number, message: string): Promise<void> {
  if (count === 0) {
    await expect(page.getByText('No interview matches these filters.'), message).toBeVisible();
  } else {
    await expect(page.getByText(`Rows: ${count}`, { exact: true }), message).toBeVisible();
  }
}

// ------------------------------------------------------------ UI helpers --

/**
 * Chooses one filter on Interview records and waits for the list the screen
 * asked for with it, as a person waits for the list to change before touching
 * the next filter. The screen does not discard an older, slower answer (see the
 * note on TC-730 in the manual file), so two changes in quick succession can
 * leave it showing the first one's list; waiting here keeps the steps apart.
 */
async function chooseRecordsFilter(
  page: Page,
  filter: 'Status' | 'Track',
  label: string,
  expected: { status: string | null; track: string | null },
): Promise<void> {
  const listed = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === '/api/admin/interviews' &&
      url.searchParams.get('status') === expected.status &&
      url.searchParams.get('track') === expected.track
    );
  });
  await page.getByRole('combobox', { name: filter }).selectOption({ label });
  await listed;
}

/** One college card on /admin/colleges, found by its heading. */
function collegeCard(page: Page, name: string): Locator {
  return page
    .getByRole('article')
    .filter({ has: page.getByRole('heading', { level: 2, name, exact: true }) });
}

/** The value beside a `<dt>` term inside a card or a facts list. */
function fact(scope: Locator, term: string): Locator {
  return scope.getByRole('definition').filter({
    has: scope.page().locator(`xpath=preceding-sibling::dt[normalize-space(.)="${term}"]`),
  });
}

/** The footer button that moves the setup flow on. */
function continueButton(page: Page): Locator {
  return page.getByRole('button', { name: 'Continue' });
}

/** One row of the plan on step 6, by the words it shows. */
function planRow(page: Page, what: string): Locator {
  return page.getByRole('region', { name: 'Step 6: create' }).getByRole('listitem').filter({
    hasText: what,
  });
}

/** One leaf of step 5, by the words in its checkbox label. */
function leaf(page: Page, title: string): Locator {
  return page
    .getByRole('region', { name: 'Step 5: batches' })
    .locator('.leaf')
    .filter({ has: page.getByRole('checkbox', { name: title, exact: true }) });
}

// =============================================================================

test.describe('Admin: college setup and interviews', () => {
  // ---------------------------------------------------------------- Colleges

  test('Colleges lists every college as a card with its facts @TC-700', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const name = `E2E Card College ${RUN}`;
    const code = newCode();
    const contact = `office-${RUN.toLowerCase()}@example.invalid`;
    const domain = `e2e-${RUN.toLowerCase()}.example.invalid`;
    const college = await createCollege(page, {
      code,
      name,
      campus: 'Test Campus',
      contact,
      domains: [domain],
    });
    await createDepartment(page, college.id, { code: 'MGT', name: 'Management Studies' });

    const departments = (await seededCollege(page)).department_count;
    const seeded = collegeCard(page, SEED.collegeName);
    const mine = collegeCard(page, name);

    await test.step('1. Open /admin/colleges', async () => {
      await page.goto('/admin/colleges');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Colleges' }),
        'TC-700 ER-1: the Colleges screen opens',
      ).toBeVisible();
      await expect(
        seeded.getByText(
          `${SEED.collegeCode} · ${SEED.campus} · ${counted(departments, 'department')}`,
        ),
        'TC-700 ER-1: the seeded college card shows its code, campus and department count',
      ).toBeVisible();
      await expect(
        seeded.getByText('Active', { exact: true }),
        'TC-700 ER-1: status Active',
      ).toBeVisible();
      await expect(
        fact(seeded, 'Email domains'),
        'TC-700 ER-1: no domains of its own reads "Deployment list"',
      ).toHaveText('Deployment list');
      await expect(fact(seeded, 'Contact'), 'TC-700 ER-1: no contact reads as a dash').toHaveText(
        '—',
      );
      await expect(
        seeded.getByRole('link', { name: 'Open' }),
        'TC-700 ER-1: the card offers Open',
      ).toBeVisible();
      await expect(
        seeded.getByRole('link', { name: 'Add departments, courses, batches' }),
        'TC-700 ER-1: the card offers "Add departments, courses, batches"',
      ).toBeVisible();
    });

    await test.step('2. Find the card of the college in the test data', async () => {
      await expect(
        mine.getByText(`${code} · Test Campus · 1 department`),
        'TC-700 ER-2: the card shows the code, campus and one department',
      ).toBeVisible();
      await expect(fact(mine, 'Email domains'), 'TC-700 ER-2: the domain is listed').toHaveText(
        domain,
      );
      await expect(fact(mine, 'Contact'), 'TC-700 ER-2: the contact is listed').toHaveText(contact);
      await expect(
        fact(mine, 'College admin'),
        'TC-700 ER-2: nobody runs the new college yet',
      ).toContainText('Not appointed');
    });

    await test.step('3. Press the "Draft" status filter', async () => {
      await page
        .getByRole('group', { name: 'Status' })
        .getByRole('button', { name: 'Draft' })
        .click();
      await expect(
        page.getByText('No college matches this filter.'),
        'TC-700 ER-3: no college is a draft, so the filter says so',
      ).toBeVisible();
      await expect(seeded, 'TC-700 ER-3: the seeded card is hidden').toBeHidden();
      await expect(mine, 'TC-700 ER-3: the new card is hidden').toBeHidden();
    });

    await test.step('4. Press the "All" status filter', async () => {
      await page
        .getByRole('group', { name: 'Status' })
        .getByRole('button', { name: 'All' })
        .click();
      await expect(seeded, 'TC-700 ER-4: the seeded card is back').toBeVisible();
      await expect(mine, 'TC-700 ER-4: the new card is back').toBeVisible();
    });

    await test.step('5. Choose "Test Campus" in Campus', async () => {
      await page.getByRole('combobox', { name: 'Campus' }).selectOption('Test Campus');
      await expect(mine, 'TC-700 ER-5: the college on that campus stays').toBeVisible();
      await expect(seeded, 'TC-700 ER-5: the Bengaluru college is hidden').toBeHidden();
    });
  });

  test('A college card opens its structure and its setup @TC-701', async ({ page, signIn }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Open College ${RUN}` });
    const card = collegeCard(page, spine.collegeName);

    await test.step('1. Open /admin/colleges', async () => {
      await page.goto('/admin/colleges');
      await expect(card, 'TC-701 ER-1: the college has a card').toBeVisible();
    });

    await test.step('2. On the card of the college in the test data, press "Open"', async () => {
      await card.getByRole('link', { name: 'Open' }).click();
      await expect(page, 'TC-701 ER-2: College structure opens for that college').toHaveURL(
        new RegExp(`/admin/institution\\?college=${spine.collegeId}$`),
      );
      await expect(
        page.getByRole('combobox', { name: 'College' }),
        'TC-701 ER-2: the College picker has that college chosen',
      ).toHaveValue(spine.collegeId);
      await expect(
        page.getByRole('heading', { level: 2, name: spine.collegeName, exact: true }),
        'TC-701 ER-2: the first section is headed with the college name',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: /^MGT Management Studies/ }),
        'TC-701 ER-2: its department is listed',
      ).toBeVisible();
    });

    await test.step('3. Go back to /admin/colleges', async () => {
      await page.goto('/admin/colleges');
      await expect(card, 'TC-701 ER-3: the card is shown again').toBeVisible();
    });

    await test.step('4. On the same card, press "Add departments, courses, batches"', async () => {
      await card.getByRole('link', { name: 'Add departments, courses, batches' }).click();
      await expect(page, 'TC-701 ER-4: Set up a college opens for that college').toHaveURL(
        new RegExp(`/admin/setup\\?college=${spine.collegeId}$`),
      );
      const facts = page.getByRole('region', { name: 'Step 1: college' });
      await expect(fact(facts, 'Code'), 'TC-701 ER-4: its code is shown, not typed').toHaveText(
        spine.collegeCode,
      );
      await expect(fact(facts, 'Name'), 'TC-701 ER-4: its name is shown, not typed').toHaveText(
        spine.collegeName,
      );
      await expect(
        page.getByRole('textbox', { name: 'Code *' }),
        'TC-701 ER-4: there is no Code box to type into',
      ).toBeHidden();
      await expect(continueButton(page), 'TC-701 ER-4: Continue is available').toBeEnabled();
      await expect(
        page.getByText('Step 1 of 6'),
        'TC-701 ER-4: the flow opens on step 1',
      ).toBeVisible();
    });
  });

  test('Appoint a faculty member to run a college @TC-702', async ({ page, signIn }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Appoint College ${RUN}` });
    const facultyName = `E2E Faculty ${RUN}`;
    const faculty = await body<{ user_id: string }>(
      await page.request.post('/api/admin/faculty', {
        data: {
          name: facultyName,
          email: `e2e-faculty-${RUN.toLowerCase()}@bgscet.ac.in`,
          department_id: spine.departmentId,
        },
      }),
      'POST /api/admin/faculty',
    );
    const reason = 'Runs the new college for the E2E check';
    const card = collegeCard(page, spine.collegeName);
    const appointButton = card.getByRole('button', { name: 'Appoint', exact: true });

    try {
      await test.step('1. Open /admin/colleges', async () => {
        await page.goto('/admin/colleges');
        await expect(
          fact(card, 'College admin'),
          'TC-702 ER-1: nobody runs the college yet',
        ).toContainText('Not appointed');
      });

      await test.step('2. On the card of the college in the test data, press "Appoint"', async () => {
        await appointButton.click();
        await expect(
          card.getByRole('combobox', { name: 'Faculty account' }),
          'TC-702 ER-2: the form asks for a faculty account',
        ).toBeVisible();
        await expect(
          card.getByRole('textbox', { name: 'Reason *' }),
          'TC-702 ER-2: the form asks for a reason',
        ).toBeVisible();
        await expect(
          card.getByText('0 of 20 characters'),
          'TC-702 ER-2: the reason counter starts at 0 of 20',
        ).toBeVisible();
        await expect(appointButton, 'TC-702 ER-2: Appoint is not available yet').toBeDisabled();
      });

      await test.step('3. In "Faculty account", choose the faculty member in the test data', async () => {
        await card
          .getByRole('combobox', { name: 'Faculty account' })
          .selectOption({ value: faculty.user_id });
      });

      await test.step('4. Type "Too short" in "Reason"', async () => {
        await card.getByRole('textbox', { name: 'Reason *' }).fill('Too short');
        await expect(
          card.getByText('9 of 20 characters'),
          'TC-702 ER-3: the counter reads 9 of 20',
        ).toBeVisible();
        await expect(
          appointButton,
          'TC-702 ER-3: a reason under 20 characters does not unlock Appoint',
        ).toBeDisabled();
      });

      await test.step('5. Replace the reason with the one in the test data', async () => {
        await card.getByRole('textbox', { name: 'Reason *' }).fill(reason);
        await expect(
          card.getByText(`${reason.length} of 20 characters`),
          'TC-702 ER-4: the counter counts the new reason',
        ).toBeVisible();
        await expect(appointButton, 'TC-702 ER-4: Appoint becomes available').toBeEnabled();
      });

      await test.step('6. Press Appoint', async () => {
        await appointButton.click();
        await expect(
          page.getByRole('status').filter({
            hasText: `${facultyName} appointed to ${spine.collegeName} with 14 functions.`,
          }),
          'TC-702 ER-5: the screen confirms the appointment, all 14 functions live at once',
        ).toBeVisible();
        await expect(
          fact(card, 'College admin'),
          'TC-702 ER-5: the card names the new college admin',
        ).toContainText(facultyName);
        await expect(
          fact(card, 'College admin').getByText('14 functions'),
          'TC-702 ER-5: the chip says how many functions are live',
        ).toBeVisible();
        await expect(
          card.getByRole('combobox', { name: 'Faculty account' }),
          'TC-702 ER-5: the form closes',
        ).toBeHidden();
      });
    } finally {
      // Undo: revoke every grant the appointment wrote, then take the account
      // off the roster, so the seeded faculty list is as it was.
      const admins = await page.request.get(`/api/admin/colleges/${spine.collegeId}/admins`);
      if (admins.ok()) {
        for (const admin of (await admins.json()) as {
          capabilities: { grant_id: string }[];
        }[]) {
          for (const grant of admin.capabilities) {
            await page.request.post(`/api/admin/governance/grants/${grant.grant_id}/revoke`, {
              data: { reason: 'End of the automated appointment check' },
            });
          }
        }
      }
      await page.request.post(`/api/admin/users/${faculty.user_id}/remove`, {
        data: { reason: 'Account made by the automated appointment check' },
      });
    }
  });

  test('Deleting a college that still has people under it is refused @TC-703', async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('admin');
    // Other modules approve students into the seeded batch and file faculty
    // under the college, so the refusal's numbers are read from the same plan
    // the dialog asks for, and only Test Student's seat is taken for granted.
    const plan = await body<{ blockers: Record<string, number>; refusal: string | null }>(
      await page.request.get(`/api/admin/colleges/${(await seededCollege(page)).id}/delete-plan`),
      'GET the seeded college delete-plan',
    );
    const seated = plan.blockers['students.cohort_id'] ?? 0;
    if (seated < 1) block(testInfo, 'Test Student is not seated in the seeded batch MBA-2026-B.');
    const card = collegeCard(page, SEED.collegeName);
    const dialog = page.getByRole('dialog', {
      name: `Delete ${SEED.collegeCode} · ${SEED.collegeName}`,
    });
    const confirmButton = dialog.getByRole('button', { name: 'Delete this college for good' });

    await test.step('1. Open /admin/colleges', async () => {
      await page.goto('/admin/colleges');
      await expect(card, 'TC-703 ER-1: the seeded college has a card').toBeVisible();
    });

    await test.step('2. On the "BGS College of Engineering and Technology" card, press "Delete college…"', async () => {
      await card.getByRole('button', { name: 'Delete college…' }).click();
      await expect(dialog, 'TC-703 ER-2: the Delete dialog opens for that college').toBeVisible();
      const refusal = dialog.getByRole('alert').filter({ hasText: 'still has people attached' });
      await expect(
        refusal,
        'TC-703 ER-2: the dialog shows the server refusing, in its own words',
      ).toContainText(`${SEED.collegeCode} still has people attached:`);
      await expect(
        refusal,
        'TC-703 ER-2: the refusal counts the students seated in its batches, Test Student among them',
      ).toContainText(`${seated} student(s) seated in its batches`);
      await expect(
        refusal,
        'TC-703 ER-2: the dialog shows the server’s whole sentence, word for word',
      ).toContainText(plan.refusal!);
      await expect(
        refusal,
        'TC-703 ER-2: the refusal says what to do and that nothing changed',
      ).toContainText('Move them to another college first');
      await expect(refusal, 'TC-703 ER-2: nothing was changed').toContainText(
        'nothing was changed.',
      );
      await expect(
        confirmButton,
        'TC-703 ER-2: "Delete this college for good" is not available',
      ).toBeDisabled();
    });

    await test.step('3. Type "Checking the refusal" in "Reason" and "123456" in "Code from your email"', async () => {
      await dialog.getByRole('textbox', { name: 'Reason' }).fill('Checking the refusal');
      await dialog.getByRole('textbox', { name: 'Code from your email' }).fill('123456');
      await expect(
        confirmButton,
        'TC-703 ER-3: a reason and a well-formed code still do not unlock a refused college',
      ).toBeDisabled();
    });

    await test.step('4. Press Cancel', async () => {
      await dialog.getByRole('button', { name: 'Cancel' }).click();
      await expect(dialog, 'TC-703 ER-4: the dialog closes').toBeHidden();
      await expect(card, 'TC-703 ER-4: the college is still listed').toBeVisible();
    });
  });

  // ------------------------------------------------------ Set up a college

  test('Set up a new college in six steps @TC-705', async ({ page, signIn }) => {
    await signIn('admin');
    const code = newCode();
    const name = `E2E Setup College ${RUN}`;
    const domain = `e2e-${RUN.toLowerCase()}.example.invalid`;
    const faBatch = `${code}-MGT-MBA-FA-2026-28`;
    const mktBatch = `${code}-MGT-MBA-MKT-2026-28`;
    const step = (n: number) => page.getByText(`Step ${n} of 6`, { exact: true });

    await test.step('1. Open /admin/setup', async () => {
      await page.goto('/admin/setup');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Set up a college' }),
        'TC-705 ER-1: the setup screen opens',
      ).toBeVisible();
      await expect(step(1), 'TC-705 ER-1: on step 1 of 6').toBeVisible();
      await expect(
        page.getByRole('combobox', { name: 'Continue a college that is already here' }),
        'TC-705 ER-1: a new college is the default',
      ).toHaveValue('');
      await expect(
        continueButton(page),
        'TC-705 ER-1: Continue waits for a code and a name',
      ).toBeDisabled();
    });

    await test.step('2. Type the Code and Name from the test data', async () => {
      await page.getByRole('textbox', { name: 'Code *' }).fill(code);
      await page.getByRole('textbox', { name: 'Name *' }).fill(name);
      await expect(continueButton(page), 'TC-705 ER-2: Continue becomes available').toBeEnabled();
    });

    await test.step('3. Type the Campus, Contact and Email domains from the test data, then press Continue', async () => {
      await page.getByRole('textbox', { name: 'Campus' }).fill('Test Campus');
      await page.getByRole('textbox', { name: 'Contact' }).fill('office@example.invalid');
      await page.getByRole('textbox', { name: 'Email domains' }).fill(domain);
      await continueButton(page).click();
      await expect(step(2), 'TC-705 ER-3: on step 2 of 6').toBeVisible();
      await expect(
        page
          .getByRole('region', { name: 'Step 2: departments' })
          .getByRole('textbox', { name: 'Code *' }),
        'TC-705 ER-3: one empty department row',
      ).toHaveValue('');
      await expect(
        continueButton(page),
        'TC-705 ER-3: Continue waits for the department',
      ).toBeDisabled();
    });

    await test.step("4. Type the department's Code, Name and Head, then press Continue", async () => {
      const region = page.getByRole('region', { name: 'Step 2: departments' });
      await region.getByRole('textbox', { name: 'Code *' }).fill('MGT');
      await region.getByRole('textbox', { name: 'Name *' }).fill('Management Studies');
      await region.getByRole('textbox', { name: 'Head' }).fill('Dr. Test Head');
      await continueButton(page).click();
      const courses = page.getByRole('region', { name: 'Step 3: courses' });
      await expect(step(3), 'TC-705 ER-4: on step 3 of 6').toBeVisible();
      await expect(
        courses.getByRole('heading', { level: 2, name: 'Management Studies' }),
        'TC-705 ER-4: a group for the new department',
      ).toBeVisible();
      await expect(
        courses.getByText('No course yet.'),
        'TC-705 ER-4: with no course yet',
      ).toBeVisible();
      await expect(continueButton(page), 'TC-705 ER-4: Continue waits for a course').toBeDisabled();
    });

    await test.step('5. Press "Add a course", type the course\'s Code and Name, keep Degree PG and Years 2, then press Continue', async () => {
      const courses = page.getByRole('region', { name: 'Step 3: courses' });
      await courses.getByRole('button', { name: 'Add a course' }).click();
      await courses.getByRole('textbox', { name: 'Code *' }).fill('MBA');
      await courses.getByRole('textbox', { name: 'Name *' }).fill('General MBA');
      await expect(
        courses.getByRole('combobox', { name: 'Degree' }),
        'TC-705 ER-5: PG by default',
      ).toHaveValue('PG');
      await expect(
        courses.getByRole('spinbutton', { name: 'Years' }),
        'TC-705 ER-5: 2 years by default',
      ).toHaveValue('2');
      await continueButton(page).click();
      const specs = page.getByRole('region', { name: 'Step 4: specializations' });
      await expect(step(4), 'TC-705 ER-5: on step 4 of 6').toBeVisible();
      await expect(
        specs.getByRole('heading', { level: 2, name: 'General MBA' }),
        'TC-705 ER-5: a group for the new course',
      ).toBeVisible();
      await expect(
        specs.getByText('No specialization — the course is the whole programme.'),
        'TC-705 ER-5: with no specialization yet',
      ).toBeVisible();
    });

    await test.step('6. Press "Add a specialization" twice, type the two specializations from the test data, then press Continue', async () => {
      const specs = page.getByRole('region', { name: 'Step 4: specializations' });
      await specs.getByRole('button', { name: 'Add a specialization' }).click();
      await specs.getByRole('button', { name: 'Add a specialization' }).click();
      await specs.getByRole('textbox', { name: 'Code *' }).nth(0).fill('fa');
      await specs.getByRole('textbox', { name: 'Name *' }).nth(0).fill('Financial Analytics');
      await specs.getByRole('textbox', { name: 'Code *' }).nth(1).fill('mkt');
      await specs.getByRole('textbox', { name: 'Name *' }).nth(1).fill('Marketing');
      await continueButton(page).click();
      await expect(step(5), 'TC-705 ER-6: on step 5 of 6').toBeVisible();
      await expect(
        leaf(page, 'General MBA · Financial Analytics'),
        'TC-705 ER-6: one batch per specialization — Financial Analytics',
      ).toBeVisible();
      await expect(
        leaf(page, 'General MBA · Marketing'),
        'TC-705 ER-6: one batch per specialization — Marketing',
      ).toBeVisible();
    });

    await test.step('7. Choose 2026 in "Starting year"', async () => {
      await page.getByRole('combobox', { name: 'Starting year' }).selectOption('2026');
      const fa = leaf(page, 'General MBA · Financial Analytics');
      const mkt = leaf(page, 'General MBA · Marketing');
      await expect(
        fact(fa, 'Code'),
        'TC-705 ER-7: the batch code is COLLEGE-DEPT-COURSE-SPEC-YEARS',
      ).toHaveText(faBatch);
      await expect(
        fact(fa, 'Reads as'),
        'TC-705 ER-7: it reads as course - specialization · years',
      ).toHaveText('General MBA - Financial Analytics · 2026-28');
      await expect(
        fact(fa, 'Runs'),
        'TC-705 ER-7: it runs July to the last day of June',
      ).toHaveText('2026-07-01 → 2028-06-30');
      await expect(fact(fa, 'Degree'), 'TC-705 ER-7: degree from the course').toHaveText('PG');
      await expect(
        fa.getByText('Mock interview: Financial Analytics (FA)'),
        'TC-705 ER-7: the code fa selects the Financial Analytics interview',
      ).toBeVisible();
      await expect(fact(mkt, 'Code'), 'TC-705 ER-7: the Marketing batch code').toHaveText(mktBatch);
      await expect(
        mkt.getByText('Mock interview: general'),
        'TC-705 ER-7: the code mkt matches no track, so the chip says general',
      ).toBeVisible();
    });

    await test.step('8. Press Continue', async () => {
      await continueButton(page).click();
      await expect(step(6), 'TC-705 ER-8: on step 6 of 6').toBeVisible();
      for (const what of [
        `${code} · ${name}`,
        'MGT · Management Studies',
        'MBA · General MBA · PG · 2 years',
        'fa · Financial Analytics',
        'mkt · Marketing',
        `${faBatch} · General MBA - Financial Analytics · 2026-28`,
        `${mktBatch} · General MBA - Marketing · 2026-28`,
      ]) {
        await expect(
          planRow(page, what).getByText('New', { exact: true }),
          `TC-705 ER-8: the plan lists "${what}" as New`,
        ).toBeVisible();
      }
      await expect(
        page.getByRole('button', { name: 'Create everything · 7' }),
        'TC-705 ER-8: seven things will be written',
      ).toBeEnabled();
    });

    await test.step('9. Press "Create everything · 7"', async () => {
      await page.getByRole('button', { name: 'Create everything · 7' }).click();
      await expect(
        page.getByRole('status').filter({ hasText: `${name} is set up.` }),
        'TC-705 ER-9: the screen says the college is set up',
      ).toBeVisible();
      await expect(
        page.getByRole('region', { name: 'Step 6: create' }).getByText('Created', { exact: true }),
        'TC-705 ER-9: all seven rows read Created',
      ).toHaveCount(7);
      await expect(
        page.getByRole('link', { name: 'Open College structure' }),
        'TC-705 ER-9: the next steps are offered',
      ).toBeVisible();
      await expect(
        page.getByRole('link', { name: 'Approve new students' }),
        'TC-705 ER-9: the next steps are offered',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Set up another college' }),
        'TC-705 ER-9: the next steps are offered',
      ).toBeVisible();
    });

    await test.step("10. Open /admin/colleges and find the new college's card", async () => {
      await page.goto('/admin/colleges');
      const card = collegeCard(page, name);
      await expect(
        card.getByText(`${code} · Test Campus · 1 department`),
        'TC-705 ER-10: the new college is listed with its department',
      ).toBeVisible();
      await expect(fact(card, 'Email domains'), 'TC-705 ER-10: with its email domain').toHaveText(
        domain,
      );
      await expect(fact(card, 'Contact'), 'TC-705 ER-10: with its contact').toHaveText(
        'office@example.invalid',
      );
    });
  });

  test('Creating a college that is already there changes nothing and says "Already there" @TC-706', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    // The college as TC-705 would have left it: a department, a course, one
    // specialization and its batch, written through the same five POSTs.
    const spine = await createSpine(page, {
      name: `E2E Rerun College ${RUN}`,
      specialization: { code: 'fa', name: 'Financial Analytics' },
    });
    const batchCode = `${spine.collegeCode}-MGT-MBA-FA-2026-28`;
    await createBatch(page, spine.departmentId, {
      code: batchCode,
      label: '2026-28',
      entry: '2026-07-01',
      completion: '2028-06-30',
      specializationId: spine.specializationId!,
    });
    const plan = page.getByRole('region', { name: 'Step 6: create' });

    await test.step('1. Open /admin/setup', async () => {
      await page.goto('/admin/setup');
      await expect(page.getByText('Step 1 of 6'), 'TC-706 ER-1: on step 1 of 6').toBeVisible();
    });

    await test.step('2. Type the Code and Name of the college in the test data, then press Continue', async () => {
      await page.getByRole('textbox', { name: 'Code *' }).fill(spine.collegeCode);
      await page.getByRole('textbox', { name: 'Name *' }).fill(spine.collegeName);
      await continueButton(page).click();
      await expect(page.getByText('Step 2 of 6'), 'TC-706 ER-2: on step 2 of 6').toBeVisible();
    });

    await test.step("3. Type the department's Code and Name, then press Continue", async () => {
      const region = page.getByRole('region', { name: 'Step 2: departments' });
      await region.getByRole('textbox', { name: 'Code *' }).fill('MGT');
      await region.getByRole('textbox', { name: 'Name *' }).fill('Management Studies');
      await continueButton(page).click();
      await expect(page.getByText('Step 3 of 6'), 'TC-706 ER-3: on step 3 of 6').toBeVisible();
    });

    await test.step('4. Press "Add a course", type the course\'s Code and Name, then press Continue', async () => {
      const courses = page.getByRole('region', { name: 'Step 3: courses' });
      await courses.getByRole('button', { name: 'Add a course' }).click();
      await courses.getByRole('textbox', { name: 'Code *' }).fill('MBA');
      await courses.getByRole('textbox', { name: 'Name *' }).fill('General MBA');
      await continueButton(page).click();
      await expect(page.getByText('Step 4 of 6'), 'TC-706 ER-4: on step 4 of 6').toBeVisible();
    });

    await test.step('5. Press "Add a specialization", type its Code and Name, then press Continue', async () => {
      const specs = page.getByRole('region', { name: 'Step 4: specializations' });
      await specs.getByRole('button', { name: 'Add a specialization' }).click();
      await specs.getByRole('textbox', { name: 'Code *' }).fill('fa');
      await specs.getByRole('textbox', { name: 'Name *' }).fill('Financial Analytics');
      await continueButton(page).click();
      await expect(page.getByText('Step 5 of 6'), 'TC-706 ER-5: on step 5 of 6').toBeVisible();
    });

    await test.step('6. Choose 2026 in "Starting year", then press Continue', async () => {
      await page.getByRole('combobox', { name: 'Starting year' }).selectOption('2026');
      await continueButton(page).click();
      await expect(
        planRow(page, `${batchCode} · General MBA - Financial Analytics · 2026-28`).getByText(
          'New',
          {
            exact: true,
          },
        ),
        'TC-706 ER-6: typed as new, the plan cannot know the batch exists yet',
      ).toBeVisible();
      await expect(
        plan.getByText('New', { exact: true }),
        'TC-706 ER-6: five rows marked New',
      ).toHaveCount(5);
      await expect(
        page.getByRole('button', { name: 'Create everything · 5' }),
        'TC-706 ER-6: five things to write',
      ).toBeEnabled();
    });

    await test.step('7. Press "Create everything · 5"', async () => {
      await page.getByRole('button', { name: 'Create everything · 5' }).click();
      await expect(
        page.getByRole('status').filter({ hasText: `${spine.collegeName} is set up.` }),
        'TC-706 ER-7: the run finishes without an error',
      ).toBeVisible();
      await expect(
        plan.getByText('Already there', { exact: true }),
        'TC-706 ER-7: all five rows read "Already there"',
      ).toHaveCount(5);
      await expect(
        plan.getByText('Created', { exact: true }),
        'TC-706 ER-7: nothing was created',
      ).toHaveCount(0);
    });

    await test.step("8. Open /admin/colleges and find the college's card", async () => {
      await page.goto('/admin/colleges');
      const card = collegeCard(page, spine.collegeName);
      await expect(card, 'TC-706 ER-8: the college has one card, not two').toHaveCount(1);
      await expect(
        card.getByText(`${spine.collegeCode} · 1 department`),
        'TC-706 ER-8: still one department',
      ).toBeVisible();
    });

    await test.step('9. Press "Open" on that card', async () => {
      await collegeCard(page, spine.collegeName).getByRole('link', { name: 'Open' }).click();
      await expect(
        page.getByRole('button', { name: 'MGT Management Studies 1 batch' }),
        'TC-706 ER-9: the department still has one batch',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'MBA General MBA 24 months · 1 specialization' }),
        'TC-706 ER-9: the course still has one specialization',
      ).toBeVisible();
    });
  });

  test('Continue a half-set-up college from College structure @TC-707', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Continue College ${RUN}` });
    const batchCode = `${spine.collegeCode}-MGT-MBA-BA-2026-28`;
    const plan = page.getByRole('region', { name: 'Step 6: create' });

    await test.step('1. Open /admin/institution and choose the college in the test data in "College"', async () => {
      await page.goto('/admin/institution');
      await page
        .getByRole('combobox', { name: 'College' })
        .selectOption({ label: `${spine.collegeCode} · ${spine.collegeName}` });
      await expect(
        page.getByRole('button', { name: 'MBA General MBA 24 months · 0 specializations' }),
        'TC-707 ER-1: the course has no specialization yet',
      ).toBeVisible();
    });

    await test.step('2. Under "Specializations", press "Add a specialization"', async () => {
      await page
        .getByRole('region', { name: 'Specializations' })
        .getByRole('link', { name: 'Add a specialization' })
        .click();
      await expect(page, 'TC-707 ER-2: setup opens for this college on step 4').toHaveURL(
        new RegExp(`/admin/setup\\?college=${spine.collegeId}&step=4$`),
      );
      await expect(page.getByText('Step 4 of 6'), 'TC-707 ER-2: on step 4 of 6').toBeVisible();
      const specs = page.getByRole('region', { name: 'Step 4: specializations' });
      await expect(
        specs.getByRole('heading', { level: 2, name: 'General MBA' }),
        'TC-707 ER-2: the existing course is loaded',
      ).toBeVisible();
      await expect(
        specs.getByText('No specialization — the course is the whole programme.'),
        'TC-707 ER-2: with no specialization',
      ).toBeVisible();
    });

    await test.step('3. Press "Add a specialization", type Code "ba" and Name "Business Analytics", then press Continue', async () => {
      const specs = page.getByRole('region', { name: 'Step 4: specializations' });
      await specs.getByRole('button', { name: 'Add a specialization' }).click();
      await specs.getByRole('textbox', { name: 'Code *' }).fill('ba');
      await specs.getByRole('textbox', { name: 'Name *' }).fill('Business Analytics');
      await continueButton(page).click();
      const ba = leaf(page, 'General MBA · Business Analytics');
      await expect(ba, 'TC-707 ER-3: a batch for the new specialization').toBeVisible();
      await expect(
        ba.getByText('Mock interview: Business Analytics (BA)'),
        'TC-707 ER-3: the code ba selects the Business Analytics interview',
      ).toBeVisible();
    });

    await test.step('4. Choose 2026 in "Starting year", then press Continue', async () => {
      await page.getByRole('combobox', { name: 'Starting year' }).selectOption('2026');
      await continueButton(page).click();
      for (const what of [
        `${spine.collegeCode} · ${spine.collegeName}`,
        'MGT · Management Studies',
        'MBA · General MBA · PG · 2 years',
      ]) {
        await expect(
          planRow(page, what).getByText('Already there', { exact: true }),
          `TC-707 ER-4: "${what}" is marked Already there`,
        ).toBeVisible();
      }
      await expect(
        planRow(page, 'ba · Business Analytics').getByText('New', { exact: true }),
        'TC-707 ER-4: the specialization is New',
      ).toBeVisible();
      await expect(
        planRow(page, `${batchCode} · General MBA - Business Analytics · 2026-28`).getByText(
          'New',
          {
            exact: true,
          },
        ),
        'TC-707 ER-4: its batch is New',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Create everything · 2' }),
        'TC-707 ER-4: only the two new rows will be written',
      ).toBeEnabled();
    });

    await test.step('5. Press "Create everything · 2"', async () => {
      await page.getByRole('button', { name: 'Create everything · 2' }).click();
      await expect(
        page.getByRole('status').filter({ hasText: `${spine.collegeName} is set up.` }),
        'TC-707 ER-5: the run finishes',
      ).toBeVisible();
      await expect(
        planRow(page, 'ba · Business Analytics').getByText('Created', { exact: true }),
        'TC-707 ER-5: the specialization was created',
      ).toBeVisible();
      await expect(
        planRow(page, batchCode).getByText('Created', { exact: true }),
        'TC-707 ER-5: its batch was created',
      ).toBeVisible();
      await expect(
        plan.getByText('Already there', { exact: true }),
        'TC-707 ER-5: the three existing rows were left alone',
      ).toHaveCount(3);
    });
  });

  test('A refused row stops only what hangs under it, and "Create again" resumes @TC-708', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const code = newCode();
    const name = `E2E Resume College ${RUN}`;
    const tooLong = 'MBA-THIS-COURSE-CODE-IS-FAR-TOO-LONG';

    await test.step('1. Open /admin/setup', async () => {
      await page.goto('/admin/setup');
      await expect(page.getByText('Step 1 of 6'), 'TC-708 ER-1: on step 1 of 6').toBeVisible();
    });

    await test.step('2. Type the Code and Name from the test data, then press Continue', async () => {
      await page.getByRole('textbox', { name: 'Code *' }).fill(code);
      await page.getByRole('textbox', { name: 'Name *' }).fill(name);
      await continueButton(page).click();
    });

    await test.step("3. Type the department's Code and Name, then press Continue", async () => {
      const region = page.getByRole('region', { name: 'Step 2: departments' });
      await region.getByRole('textbox', { name: 'Code *' }).fill('MGT');
      await region.getByRole('textbox', { name: 'Name *' }).fill('Management Studies');
      await continueButton(page).click();
    });

    await test.step('4. Press "Add a course", type the too-long course Code and the Name, then press Continue', async () => {
      const courses = page.getByRole('region', { name: 'Step 3: courses' });
      await courses.getByRole('button', { name: 'Add a course' }).click();
      await courses.getByRole('textbox', { name: 'Code *' }).fill(tooLong);
      await courses.getByRole('textbox', { name: 'Name *' }).fill('General MBA');
      await continueButton(page).click();
      await expect(
        page.getByText('Step 4 of 6'),
        'TC-708 ER-2: the screen does not check the length',
      ).toBeVisible();
    });

    await test.step('5. Press Continue without adding a specialization', async () => {
      await continueButton(page).click();
      await expect(
        leaf(page, 'General MBA'),
        'TC-708 ER-3: one batch, on the course itself',
      ).toBeVisible();
    });

    await test.step('6. Choose 2026 in "Starting year", then press Continue', async () => {
      await page.getByRole('combobox', { name: 'Starting year' }).selectOption('2026');
      await continueButton(page).click();
      await expect(
        page.getByRole('button', { name: 'Create everything · 4' }),
        'TC-708 ER-4: four things to write',
      ).toBeEnabled();
    });

    await test.step('7. Press "Create everything · 4"', async () => {
      await page.getByRole('button', { name: 'Create everything · 4' }).click();
      await expect(
        page.getByRole('alert').filter({ hasText: 'could not be created' }),
        'TC-708 ER-5: the screen says one row failed and what landed stays',
      ).toContainText('1 row could not be created. Fix and press Create again; what landed stays.');
      await expect(
        planRow(page, `${code} · ${name}`).getByText('Created', { exact: true }),
        'TC-708 ER-5: the college was created',
      ).toBeVisible();
      await expect(
        planRow(page, 'MGT · Management Studies').getByText('Created', { exact: true }),
        'TC-708 ER-5: the department was created',
      ).toBeVisible();
      await expect(
        planRow(page, `${tooLong} · General MBA`),
        'TC-708 ER-5: the course failed, with the server’s own words',
      ).toContainText('Failed · String should have at most 32 characters');
      await expect(
        planRow(page, `${code}-MGT-${tooLong}-2026-28`).getByText('Skipped', { exact: true }),
        'TC-708 ER-5: the batch under it was skipped',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Create again' }),
        'TC-708 ER-5: the button now reads Create again',
      ).toBeEnabled();
    });

    await test.step('8. Press "3 Courses" in the list of steps', async () => {
      await page.getByRole('button', { name: '3 Courses' }).click();
      await expect(page.getByText('Step 3 of 6'), 'TC-708 ER-6: back on step 3').toBeVisible();
      await expect(
        page
          .getByRole('region', { name: 'Step 3: courses' })
          .getByRole('textbox', { name: 'Code *' }),
        'TC-708 ER-6: the course code can still be corrected',
      ).toHaveValue(tooLong);
    });

    await test.step('9. Replace the course Code with MBA, then press Continue until "Step 6 of 6" is shown', async () => {
      await page
        .getByRole('region', { name: 'Step 3: courses' })
        .getByRole('textbox', { name: 'Code *' })
        .fill('MBA');
      await continueButton(page).click();
      await continueButton(page).click();
      await continueButton(page).click();
      await expect(page.getByText('Step 6 of 6'), 'TC-708 ER-7: on step 6 of 6').toBeVisible();
    });

    await test.step('10. Press "Create again"', async () => {
      await page.getByRole('button', { name: 'Create again' }).click();
      await expect(
        page.getByRole('status').filter({ hasText: `${name} is set up.` }),
        'TC-708 ER-8: the second press finishes the college',
      ).toBeVisible();
      await expect(
        planRow(page, 'MBA · General MBA').getByText('Created', { exact: true }),
        'TC-708 ER-8: the corrected course is created',
      ).toBeVisible();
      await expect(
        planRow(page, `${code}-MGT-MBA-2026-28`).getByText('Created', { exact: true }),
        'TC-708 ER-8: its batch is created',
      ).toBeVisible();
      await expect(
        planRow(page, 'MGT · Management Studies').getByText('Already there', { exact: true }),
        'TC-708 ER-8: the department from the first press is reused, not written twice',
      ).toBeVisible();
      await expect(
        planRow(page, `${code} · ${name}`).getByText('Already there', { exact: true }),
        'TC-708 ER-8: the college from the first press reads Already there',
      ).toBeVisible();
    });
  });

  test('Setup opened for an existing college shows that college in the picker @TC-709', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Picker College ${RUN}` });
    const card = collegeCard(page, spine.collegeName);

    await test.step('1. Open /admin/colleges', async () => {
      await page.goto('/admin/colleges');
      await expect(card, 'TC-709 ER-1: the college has a card').toBeVisible();
    });

    await test.step('2. On the card of the college in the test data, press "Add departments, courses, batches"', async () => {
      await card.getByRole('link', { name: 'Add departments, courses, batches' }).click();
      await expect(
        fact(page.getByRole('region', { name: 'Step 1: college' }), 'Name'),
        'TC-709 ER-2: the college is loaded',
      ).toHaveText(spine.collegeName);
      await expect(
        page.getByRole('combobox', { name: 'Continue a college that is already here' }),
        'TC-709 ER-2: the picker names the loaded college, not "— New college —"',
      ).toHaveValue(spine.collegeId);
    });
  });

  // ---------------------------------------------------- College structure

  test('College structure shows the seeded college top to bottom @TC-710', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    // The seed's names, codes and dates are facts; its COUNTS are not, because
    // other modules seat students in the seeded batch. They are read from the
    // same endpoints the screen reads, before the screen is opened.
    const college = await seededCollege(page);
    const department = (
      await body<{ id: string; code: string; cohort_count: number }[]>(
        await page.request.get(`/api/admin/colleges/${college.id}/departments`),
        'GET the seeded departments',
      )
    ).find((d) => d.code === SEED.departmentCode)!;
    const course = (
      await body<{ id: string; code: string; specialization_count: number }[]>(
        await page.request.get(`/api/admin/departments/${department.id}/academic-courses`),
        'GET the seeded courses',
      )
    ).find((c) => c.code === SEED.courseCode)!;
    const finance = (
      await body<{ code: string; cohort_count: number }[]>(
        await page.request.get(`/api/admin/academic-courses/${course.id}/academic-specializations`),
        'GET the seeded specializations',
      )
    ).find((sp) => sp.code === SEED.specializationCode)!;
    const departmentBatches = await body<{ code: string; student_count: number }[]>(
      await page.request.get(`/api/admin/departments/${department.id}/cohorts`),
      'GET the seeded batches',
    );
    const seated = departmentBatches.find((b) => b.code === SEED.batchCode)!.student_count;
    const specs = page.getByRole('region', { name: 'Specializations' });
    const batches = page.getByRole('region', { name: 'Batches' });
    const batchRow = batches.getByRole('row').filter({ hasText: SEED.batchCode });
    const departmentButton = page.getByRole('button', {
      name: `${SEED.departmentCode} ${SEED.departmentName} ${SEED.departmentHead} ${counted(department.cohort_count, 'batch', 'batches')}`,
    });
    const courseButton = page.getByRole('button', {
      name: `${SEED.courseCode} ${SEED.courseName} 24 months · ${counted(course.specialization_count, 'specialization')}`,
    });

    await test.step('1. Open /admin/institution and choose "BGSCET · BGS College of Engineering and Technology" in "College"', async () => {
      await page.goto('/admin/institution');
      await page
        .getByRole('combobox', { name: 'College' })
        .selectOption({ label: `${SEED.collegeCode} · ${SEED.collegeName}` });
      const section = page.getByRole('region', { name: SEED.collegeName });
      await expect(
        section.getByText(
          `${SEED.collegeCode} · ${SEED.campus} · ${counted(college.department_count, 'department')}`,
        ),
        'TC-710 ER-1: the college section gives its code, campus and department count',
      ).toBeVisible();
      await expect(
        section.getByText('Deployment list'),
        'TC-710 ER-1: no domains of its own',
      ).toBeVisible();
      await expect(
        departmentButton,
        'TC-710 ER-1: the seeded department, its head and its batch count',
      ).toBeVisible();
    });

    await test.step('2. Under "Departments", press "MGMT Department of Management Studies", then under "Courses", press "MBA Master of Business Administration"', async () => {
      await departmentButton.click();
      // The department opens on its first course; press MBA once that has drawn.
      await page.getByRole('heading', { level: 3 }).waitFor();
      await courseButton.click();
      await expect(departmentButton, 'TC-710 ER-2: the department is picked').toHaveAttribute(
        'aria-pressed',
        'true',
      );
      await expect(
        courseButton,
        'TC-710 ER-2: the course, its length and its specialization count, picked',
      ).toHaveAttribute('aria-pressed', 'true');
      await expect(
        page.getByRole('heading', { level: 3, name: `${SEED.courseCode} · ${SEED.courseName}` }),
        'TC-710 ER-2: the picked course opens for editing',
      ).toBeVisible();
      await expect(
        page.getByRole('spinbutton', { name: 'Months' }),
        'TC-710 ER-2: Months 24',
      ).toHaveValue('24');
      await expect(
        specs.getByRole('row', {
          name: `${SEED.specializationCode} ${SEED.specializationName} Not mapped ${finance.cohort_count}`,
        }),
        'TC-710 ER-2: the specialization, no mock interview mapped, and its batch count',
      ).toBeVisible();
      await expect(
        batchRow,
        'TC-710 ER-2: the batch reads as its spine and its year',
      ).toContainText(SEED.batchDisplay);
      await expect(
        batchRow.getByRole('cell').nth(1),
        'TC-710 ER-2: course and specialization codes',
      ).toHaveText('MBA · FIN');
      await expect(
        batchRow.getByRole('cell').nth(2),
        'TC-710 ER-2: as many students as are seated in it, Test Student among them',
      ).toHaveText(String(seated));
      await expect(batchRow.getByRole('cell').nth(3), 'TC-710 ER-2: ends 31 Jul 2026').toHaveText(
        '31 Jul 2026',
      );
    });

    await test.step('3. Type "Finance" in "Find"', async () => {
      await page.getByRole('searchbox', { name: 'Find' }).fill('Finance');
      await expect(
        page.getByRole('region', { name: 'Departments' }).getByText('Nothing matches “Finance”.'),
        'TC-710 ER-3: no department matches',
      ).toBeVisible();
      await expect(
        specs.getByRole('row', { name: /^FIN Finance/ }),
        'TC-710 ER-3: the Finance specialization still shows',
      ).toBeVisible();
      await expect(
        batchRow,
        'TC-710 ER-3: the batch still shows, found through its spine',
      ).toBeVisible();
    });

    await test.step('4. Replace it with "no-such-thing"', async () => {
      await page.getByRole('searchbox', { name: 'Find' }).fill('no-such-thing');
      await expect(
        batches.getByText(
          `None of the ${counted(departmentBatches.length, 'batch', 'batches')} under MGMT match “no-such-thing”.`,
        ),
        'TC-710 ER-4: the batches section says nothing matches',
      ).toBeVisible();
      await expect(batchRow, 'TC-710 ER-4: the batch is hidden').toBeHidden();
    });
  });

  test('Edit and archive a course @TC-711', async ({ page, signIn }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Course College ${RUN}` });
    const courses = page.getByRole('region', { name: 'Courses' });
    const save = courses.getByRole('button', { name: 'Save changes' });

    await test.step('1. Open /admin/institution and choose the college in the test data in "College"', async () => {
      await page.goto('/admin/institution');
      await page
        .getByRole('combobox', { name: 'College' })
        .selectOption({ label: `${spine.collegeCode} · ${spine.collegeName}` });
      await expect(
        courses.getByRole('heading', { level: 3, name: 'MBA · General MBA' }),
        'TC-711 ER-1: the course opens for editing',
      ).toBeVisible();
      await expect(
        courses.getByRole('textbox', { name: 'Name' }),
        'TC-711 ER-1: its name',
      ).toHaveValue('General MBA');
      await expect(
        courses.getByRole('spinbutton', { name: 'Months' }),
        'TC-711 ER-1: 24 months',
      ).toHaveValue('24');
      await expect(
        courses.getByRole('combobox', { name: 'Status' }),
        'TC-711 ER-1: Active',
      ).toHaveValue('ACTIVE');
    });

    await test.step('2. Change Name to "General MBA (Revised)" and Months to 18, then press "Save changes"', async () => {
      await courses.getByRole('textbox', { name: 'Name' }).fill('General MBA (Revised)');
      await courses.getByRole('spinbutton', { name: 'Months' }).fill('18');
      await save.click();
      await expect(
        page.getByRole('status').filter({ hasText: 'Saved course MBA' }),
        'TC-711 ER-2: the save is confirmed',
      ).toBeVisible();
      await expect(
        courses.getByRole('button', {
          name: 'MBA General MBA (Revised) 18 months · 0 specializations',
        }),
        'TC-711 ER-2: the course row shows the new name and length',
      ).toBeVisible();
    });

    await test.step('3. Clear the Name box', async () => {
      await courses.getByRole('textbox', { name: 'Name' }).fill('');
      await expect(save, 'TC-711 ER-3: a course cannot be saved without a name').toBeDisabled();
    });

    await test.step('4. Press Archive', async () => {
      await courses.getByRole('button', { name: 'Archive', exact: true }).click();
      await expect(
        page.getByRole('status').filter({ hasText: 'Archived course MBA' }),
        'TC-711 ER-4: the archive is confirmed',
      ).toBeVisible();
      await expect(
        courses.getByRole('button', { name: /^MBA General MBA \(Revised\) Archived/ }),
        'TC-711 ER-4: the course row carries an Archived chip',
      ).toBeVisible();
      await expect(
        courses.getByRole('combobox', { name: 'Status' }),
        'TC-711 ER-4: Status reads Archived',
      ).toHaveValue('ARCHIVED');
      await expect(
        courses.getByRole('textbox', { name: 'Name' }),
        'TC-711 ER-4: the cleared name was not saved; the stored one is back',
      ).toHaveValue('General MBA (Revised)');
      await expect(
        courses.getByRole('button', { name: 'Archive', exact: true }),
        'TC-711 ER-4: Archive is spent',
      ).toBeDisabled();
    });
  });

  test('Map a mock interview to a specialization, and unmap it @TC-712', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const spine = await createSpine(page, {
      name: `E2E Mapping College ${RUN}`,
      specialization: { code: 'ops', name: 'Operations' },
    });
    const trackCode = `t${RUN.toLowerCase()}`;
    const trackLabel = `E2E Track ${RUN}`;
    const track = await body<{ track: { id: string } }>(
      await page.request.post('/api/admin/interview-questions/tracks', {
        data: {
          code: trackCode,
          label: trackLabel,
          persona: 'an exacting but fair Head of Operations',
          sample_question: 'Walk me through how you would cut a supplier lead time by a week.',
          frameworks: ['SIPOC', 'OEE', 'root-cause analysis'],
          nova_voice: 'matthew',
        },
      }),
      'POST a track',
    );
    const specs = page.getByRole('region', { name: 'Specializations' });
    const opsRow = specs.getByRole('row').filter({ hasText: 'Operations' });

    try {
      await test.step('1. Open /admin/institution and choose the college in the test data in "College"', async () => {
        await page.goto('/admin/institution');
        await page
          .getByRole('combobox', { name: 'College' })
          .selectOption({ label: `${spine.collegeCode} · ${spine.collegeName}` });
        await expect(opsRow, 'TC-712 ER-1: the specialization has no mock interview').toHaveText(
          /OPS\s*Operations\s*Not mapped\s*0/,
        );
      });

      await test.step('2. Press "Map mock interview"', async () => {
        await specs.getByRole('button', { name: 'Map mock interview' }).click();
        await expect(
          specs.getByRole('combobox', { name: 'Mock interview' }),
          'TC-712 ER-2: a picker of tracks opens',
        ).toBeVisible();
        await expect(
          specs.getByRole('combobox', { name: 'For' }).getByRole('option'),
          'TC-712 ER-2: "For" offers every specialization, or this one',
        ).toHaveText(['Every specialization', 'OPS · Operations']);
      });

      await test.step('3. Choose the track in the test data in "Mock interview" and "OPS · Operations" in "For", then press Save', async () => {
        await specs
          .getByRole('combobox', { name: 'Mock interview' })
          .selectOption({ label: `${trackLabel} (${trackCode})` });
        await specs
          .getByRole('combobox', { name: 'For' })
          .selectOption({ label: 'OPS · Operations' });
        await specs.getByRole('button', { name: 'Save' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: `${trackLabel} is the interview for OPS.` }),
          'TC-712 ER-3: the mapping is confirmed',
        ).toBeVisible();
        await expect(opsRow, 'TC-712 ER-3: the row names the track').toContainText(trackLabel);
        await expect(opsRow, 'TC-712 ER-3: and is no longer unmapped').not.toContainText(
          'Not mapped',
        );
        await expect(
          specs.getByRole('combobox', { name: 'Mock interview' }),
          'TC-712 ER-3: the picker closes',
        ).toBeHidden();
      });

      await test.step('4. Press "Map mock interview" again, choose the same track, choose "Every specialization" in "For", then press Save', async () => {
        await specs.getByRole('button', { name: 'Map mock interview' }).click();
        await specs
          .getByRole('combobox', { name: 'Mock interview' })
          .selectOption({ label: `${trackLabel} (${trackCode})` });
        await expect(
          specs.getByRole('combobox', { name: 'For' }),
          'TC-712 ER-4: the picker opens on the track’s current mapping',
        ).toHaveValue(spine.specializationId!);
        await specs
          .getByRole('combobox', { name: 'For' })
          .selectOption({ label: 'Every specialization' });
        await specs.getByRole('button', { name: 'Save' }).click();
        await expect(
          page
            .getByRole('status')
            .filter({ hasText: `${trackLabel} is offered to every specialization again.` }),
          'TC-712 ER-4: the unmapping is confirmed',
        ).toBeVisible();
        await expect(opsRow, 'TC-712 ER-4: the row reads Not mapped again').toContainText(
          'Not mapped',
        );
      });
    } finally {
      await page.request.delete(`/api/admin/interview-questions/tracks/${track.track.id}`);
    }
  });

  test('Add a non-standard batch, then edit it @TC-713', async ({ page, signIn }) => {
    await signIn('admin');
    const spine = await createSpine(page, {
      name: `E2E Batch College ${RUN}`,
      specialization: { code: 'fin', name: 'Finance' },
    });
    const batchCode = `${spine.collegeCode}-SEC-Z`;
    const batches = page.getByRole('region', { name: 'Batches' });
    const row = batches.getByRole('row').filter({ hasText: batchCode });

    await test.step('1. Open /admin/institution and choose the college in the test data in "College"', async () => {
      await page.goto('/admin/institution');
      await page
        .getByRole('combobox', { name: 'College' })
        .selectOption({ label: `${spine.collegeCode} · ${spine.collegeName}` });
      await expect(
        batches.getByText('No batch under MGT yet.'),
        'TC-713 ER-1: the department has no batch',
      ).toBeVisible();
    });

    await test.step('2. Under "Batches", press "Add batch"', async () => {
      await batches.getByRole('button', { name: 'Add batch' }).click();
      await expect(
        batches.getByRole('heading', { level: 4, name: 'New batch' }),
        'TC-713 ER-2: the New batch form opens',
      ).toBeVisible();
      await expect(
        batches.getByRole('combobox', { name: 'End year' }),
        'TC-713 ER-2: End year waits for a start year',
      ).toBeDisabled();
      await expect(
        batches.getByText('Needs a start year first.'),
        'TC-713 ER-2: and says so',
      ).toBeVisible();
      await expect(
        batches.getByRole('combobox', { name: /^Specialization/ }),
        'TC-713 ER-2: Specialization waits for a course',
      ).toBeDisabled();
      await expect(
        batches.getByRole('button', { name: 'Create batch' }),
        'TC-713 ER-2: nothing to create yet',
      ).toBeDisabled();
    });

    await test.step('3. Type the Code from the test data, choose 2026 in "Start year" and 2028 in "End year"', async () => {
      await batches.getByRole('textbox', { name: 'Code' }).fill(batchCode);
      await batches.getByRole('combobox', { name: 'Start year' }).selectOption('2026');
      await batches.getByRole('combobox', { name: 'End year' }).selectOption('2028');
      await expect(
        batches.getByLabel('Entry date'),
        'TC-713 ER-3: the entry date fills in from the span',
      ).toHaveValue('2026-07-01');
      await expect(
        batches.getByLabel('Expected completion'),
        'TC-713 ER-3: the completion date fills in from the span',
      ).toHaveValue('2028-06-30');
    });

    await test.step('4. Type Name "2026-28 Section Z", change "Entry date" to 2026-08-15, choose "MBA · General MBA" in Course and "FIN · Finance" in Specialization, then press "Create batch"', async () => {
      await batches.getByRole('textbox', { name: /^Name/ }).fill('2026-28 Section Z');
      await batches.getByLabel('Entry date').fill('2026-08-15');
      await batches
        .getByRole('combobox', { name: /^Course/ })
        .selectOption({ label: 'MBA · General MBA' });
      await batches
        .getByRole('combobox', { name: /^Specialization/ })
        .selectOption({ label: 'FIN · Finance' });
      await batches.getByRole('button', { name: 'Create batch' }).click();
      await expect(
        page.getByRole('status').filter({ hasText: `Created batch ${batchCode}` }),
        'TC-713 ER-4: the batch is created',
      ).toBeVisible();
      await expect(row, 'TC-713 ER-4: it reads as its spine and its own name').toContainText(
        'General MBA - Finance · 2026-28 Section Z',
      );
      await expect(
        row.getByRole('cell').nth(1),
        'TC-713 ER-4: course and specialization',
      ).toHaveText('MBA · FIN');
      await expect(row.getByRole('cell').nth(2), 'TC-713 ER-4: nobody seated').toHaveText('0');
      await expect(row.getByRole('cell').nth(3), 'TC-713 ER-4: ends 30 Jun 2028').toHaveText(
        '30 Jun 2028',
      );
      await expect(
        page.getByRole('button', { name: 'MGT Management Studies 1 batch' }),
        'TC-713 ER-4: the department counts one batch',
      ).toBeVisible();
    });

    await test.step('5. Press Edit on the new batch', async () => {
      await row.getByRole('button', { name: 'Edit' }).click();
      await expect(
        batches.getByRole('heading', { level: 4, name: 'Edit batch' }),
        'TC-713 ER-5: the Edit batch form opens',
      ).toBeVisible();
      await expect(
        batches.getByRole('textbox', { name: 'Code' }),
        'TC-713 ER-5: the code cannot be changed',
      ).toBeDisabled();
      await expect(
        batches.getByLabel('Entry date'),
        'TC-713 ER-5: the odd entry date was kept',
      ).toHaveValue('2026-08-15');
      await expect(
        batches.getByRole('textbox', { name: /^Name/ }),
        'TC-713 ER-5: its name',
      ).toHaveValue('2026-28 Section Z');
    });

    await test.step('6. Change Name to "2026-28 Section Y", then press Save', async () => {
      await batches.getByRole('textbox', { name: /^Name/ }).fill('2026-28 Section Y');
      await batches.getByRole('button', { name: 'Save', exact: true }).click();
      await expect(
        page.getByRole('status').filter({ hasText: `Saved ${batchCode}` }),
        'TC-713 ER-6: the edit is saved',
      ).toBeVisible();
      await expect(row, 'TC-713 ER-6: the batch reads with its new name').toContainText(
        'General MBA - Finance · 2026-28 Section Y',
      );
    });
  });

  test('A batch with a taken code or dates that run backwards is refused @TC-714', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Refusal College ${RUN}` });
    const batches = page.getByRole('region', { name: 'Batches' });
    const department = page.getByRole('button', { name: /^MGT Management Studies \d+ batch/ });

    await test.step('1. Open /admin/institution and choose the college in the test data in "College"', async () => {
      await page.goto('/admin/institution');
      await page
        .getByRole('combobox', { name: 'College' })
        .selectOption({ label: `${spine.collegeCode} · ${spine.collegeName}` });
      await expect(department, 'TC-714 ER-1: the department has no batch').toHaveAccessibleName(
        'MGT Management Studies 0 batches',
      );
    });

    await test.step('2. Press "Add batch", type Code "MBA-2026-B", choose 2026 in "Start year" and 2028 in "End year", then press "Create batch"', async () => {
      await batches.getByRole('button', { name: 'Add batch' }).click();
      await batches.getByRole('textbox', { name: 'Code' }).fill(SEED.batchCode);
      await batches.getByRole('combobox', { name: 'Start year' }).selectOption('2026');
      await batches.getByRole('combobox', { name: 'End year' }).selectOption('2028');
      await batches.getByRole('button', { name: 'Create batch' }).click();
      await expect(
        batches.getByRole('alert'),
        'TC-714 ER-2: a batch code is unique across every college',
      ).toContainText(`A batch with code ${SEED.batchCode} already exists.`);
      await expect(
        batches.getByRole('heading', { level: 4, name: 'New batch' }),
        'TC-714 ER-2: the form stays open with what was typed',
      ).toBeVisible();
      await expect(department, 'TC-714 ER-2: no batch was created').toHaveAccessibleName(
        'MGT Management Studies 0 batches',
      );
    });

    await test.step('3. Replace the Code with the one in the test data, set "Expected completion" to 2026-06-30, then press "Create batch"', async () => {
      await batches.getByRole('textbox', { name: 'Code' }).fill(`${spine.collegeCode}-BACKWARDS`);
      await batches.getByLabel('Expected completion').fill('2026-06-30');
      await batches.getByRole('button', { name: 'Create batch' }).click();
      await expect(
        batches.getByRole('alert'),
        'TC-714 ER-3: an end before the start is refused',
      ).toContainText('Expected completion must be after the entry date.');
      await expect(department, 'TC-714 ER-3: still no batch').toHaveAccessibleName(
        'MGT Management Studies 0 batches',
      );
    });

    await test.step('4. Press Cancel', async () => {
      await batches.getByRole('button', { name: 'Cancel' }).click();
      await expect(
        batches.getByText('No batch under MGT yet.'),
        'TC-714 ER-4: the form closes and nothing was added',
      ).toBeVisible();
    });
  });

  test('Take a student out of a batch and seat them again @TC-715', async ({ page, signIn }) => {
    await signIn('admin');
    const batch = await seededBatch(page);
    // Other modules approve students into this batch, so how many sit in it is
    // read here, and the case asserts the change Test Student makes to it.
    const seated = await body<{ student_id: string; email: string }[]>(
      await page.request.get(`/api/admin/cohorts/${batch.id}/students`),
      'GET the seeded batch students',
    );
    const studentId = seated.find((s) => s.email === ACCOUNTS.student.email)!.student_id;
    const before = seated.length;
    const batches = page.getByRole('region', { name: 'Batches' });
    const batchRow = batches.getByRole('row').filter({ hasText: SEED.batchCode });
    const seating = batches.locator('.seating');
    // By address: another case's student could carry "Test Student" in a name.
    const seatedRow = seating.getByRole('row').filter({ hasText: ACCOUNTS.student.email });
    const offered = seating
      .getByRole('combobox', { name: 'Add a student' })
      .locator('option', { hasText: `${SEED.studentName} · ${SEED.studentUsn}` });

    try {
      await test.step('1. Open /admin/institution and choose "BGSCET · BGS College of Engineering and Technology" in "College"', async () => {
        await page.goto('/admin/institution');
        await page
          .getByRole('combobox', { name: 'College' })
          .selectOption({ label: `${SEED.collegeCode} · ${SEED.collegeName}` });
        await expect(
          batchRow.getByRole('cell').nth(2),
          'TC-715 ER-1: the batch counts everyone seated in it',
        ).toHaveText(String(before));
      });

      await test.step(`2. On the batch MBA-2026-B, press "Students"`, async () => {
        await batchRow.getByRole('button', { name: 'Students' }).click();
        await expect(
          seating.getByText(`Students in ${SEED.batchCode}`),
          'TC-715 ER-2: the seating panel opens for the batch',
        ).toBeVisible();
        await expect(
          seatedRow,
          'TC-715 ER-2: Test Student is seated, with address, USN and stage',
        ).toContainText(SEED.studentName);
        await expect(seatedRow, 'TC-715 ER-2: USN').toContainText(SEED.studentUsn);
        await expect(seatedRow, 'TC-715 ER-2: stage').toContainText('Excel-Advanced');
        // About THIS student only: whether anyone else is unseated depends on
        // what other cases left behind, so it is not this case's to assert.
        await expect(
          offered,
          'TC-715 ER-2: "Add a student" does not offer Test Student, who is already seated',
        ).toHaveCount(0);
      });

      await test.step('3. Press Remove on Test Student’s row', async () => {
        await seatedRow.getByRole('button', { name: 'Remove' }).click();
        await expect(
          page
            .getByRole('status')
            .filter({ hasText: `${SEED.studentName} released from ${SEED.batchCode}.` }),
          'TC-715 ER-3: the release is confirmed',
        ).toBeVisible();
        await expect(seatedRow, 'TC-715 ER-3: Test Student leaves the panel').toHaveCount(0);
        await expect(
          batchRow.getByRole('cell').nth(2),
          'TC-715 ER-3: the count drops by exactly one',
        ).toHaveText(String(before - 1));
        await expect(
          offered,
          'TC-715 ER-3: "Add a student" now offers Test Student, who is in no batch',
        ).toHaveCount(1);
      });

      await test.step(`4. Choose "Test Student · 1BG24MBA001" in "Add a student", then press Add`, async () => {
        await seating
          .getByRole('combobox', { name: 'Add a student' })
          .selectOption({ label: `${SEED.studentName} · ${SEED.studentUsn}` });
        await seating.getByRole('button', { name: 'Add', exact: true }).click();
        await expect(
          page
            .getByRole('status')
            .filter({ hasText: `${SEED.studentName} seated in ${SEED.batchCode}.` }),
          'TC-715 ER-4: the seat is confirmed',
        ).toBeVisible();
        await expect(seatedRow, 'TC-715 ER-4: Test Student is listed again').toBeVisible();
        await expect(
          batchRow.getByRole('cell').nth(2),
          'TC-715 ER-4: the count is back where it started',
        ).toHaveText(String(before));
      });
    } finally {
      // Whatever happened above, the seeded student ends in the seeded batch.
      await page.request.put(`/api/admin/students/${studentId}/cohort`, {
        data: { cohort_id: batch.id },
      });
    }
  });

  test('Add and remove a college’s email domain @TC-716', async ({ page, signIn }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Domain College ${RUN}` });
    const domain = `e2e-${RUN.toLowerCase()}.example.invalid`;
    const typed = `@E2E-${RUN}.Example.Invalid`;
    const college = page.getByRole('region', { name: spine.collegeName });
    const domainBox = page.getByRole('textbox', { name: 'Add an email domain' });

    await test.step('1. Open /admin/institution and choose the college in the test data in "College"', async () => {
      await page.goto('/admin/institution');
      await page
        .getByRole('combobox', { name: 'College' })
        .selectOption({ label: `${spine.collegeCode} · ${spine.collegeName}` });
      await expect(
        college.getByText('Deployment list'),
        'TC-716 ER-1: no domain of its own yet',
      ).toBeVisible();
      await expect(
        college.getByRole('button', { name: 'Add domain' }),
        'TC-716 ER-1: Add domain waits for a domain',
      ).toBeDisabled();
    });

    await test.step('2. Type the domain from the test data in "Add an email domain" and press "Add domain"', async () => {
      await domainBox.fill(typed);
      await college.getByRole('button', { name: 'Add domain' }).click();
      await expect(
        college.getByRole('listitem').filter({ hasText: domain }),
        'TC-716 ER-2: the domain is stored lower-case, without the @',
      ).toBeVisible();
      await expect(
        college.getByText('Deployment list'),
        'TC-716 ER-2: the deployment list no longer applies',
      ).toBeHidden();
      await expect(domainBox, 'TC-716 ER-2: the box empties').toHaveValue('');
    });

    await test.step('3. Type the same domain again and press "Add domain"', async () => {
      await domainBox.fill(domain);
      await college.getByRole('button', { name: 'Add domain' }).click();
      await expect(
        college.getByRole('alert'),
        'TC-716 ER-3: a domain cannot be added twice',
      ).toContainText(`${domain} is already on this college.`);
      await expect(
        college.getByRole('listitem').filter({ hasText: domain }),
        'TC-716 ER-3: it is still listed once',
      ).toHaveCount(1);
    });

    await test.step('4. Press the remove button on the domain', async () => {
      await college
        .getByRole('button', { name: `Remove ${domain} from ${spine.collegeName}` })
        .click();
      await expect(
        college.getByText('Deployment list'),
        'TC-716 ER-4: with no domain of its own, the deployment list applies again',
      ).toBeVisible();
      await expect(
        college.getByRole('listitem').filter({ hasText: domain }),
        'TC-716 ER-4: the domain is gone',
      ).toHaveCount(0);
    });
  });

  // ------------------------------------------------------------ Catalogue

  test('Catalogue lists the seeded subjects, and the search narrows them @TC-720', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    // Names, codes and hours are the seed's; how many students are enrolled,
    // how many certifications map and how many subjects mention "Economics"
    // are read from the catalogue, because other cases can change them.
    const catalogue = await body<
      {
        code: string;
        name: string;
        dimension: string;
        enrolled: number;
        certifications: unknown[];
      }[]
    >(await page.request.get('/api/admin/catalogue'), 'GET /api/admin/catalogue');
    const ob = catalogue.find((c) => c.code === '22MBA11')!;
    const me = catalogue.find((c) => c.code === '22MBA12')!;
    const economics = catalogue.filter((c) =>
      `${c.code} ${c.name} ${c.dimension}`.toLowerCase().includes('economics'),
    ).length;
    const panel = page.getByRole('tabpanel', { name: 'Subjects' });
    const obRow = panel
      .getByRole('row')
      .filter({ hasText: '22MBA11 · Management & Organisational Behaviour' });
    const meRow = panel.getByRole('row').filter({ hasText: '22MBA12 · Managerial Economics' });
    const search = page.getByRole('searchbox', {
      name: 'Search subjects by code, name or dimension',
    });

    await test.step('1. Open /admin/catalogue', async () => {
      await page.goto('/admin/catalogue');
      await expect(
        page.getByRole('tab', { name: /^Subjects/ }),
        'TC-720 ER-1: the Subjects tab is open',
      ).toHaveAttribute('aria-selected', 'true');
      await expect(
        obRow.getByRole('cell'),
        'TC-720 ER-1: 22MBA11 with its model, length, dimension, stage, semester, hours, enrolment and certification',
      ).toHaveText([
        '22MBA11 · Management & Organisational Behaviour Instructor Led · 16 weeks',
        'Professional',
        'Excel',
        '1',
        '60',
        String(ob.enrolled),
        ob.certifications.length
          ? counted(ob.certifications.length, 'certification')
          : 'None mapped',
      ]);
      await expect(
        meRow.getByRole('cell'),
        'TC-720 ER-1: 22MBA12, with no certification mapped',
      ).toHaveText([
        '22MBA12 · Managerial Economics Teaching Plus Self Learn · 16 weeks',
        'Thinking',
        'Excel',
        '1',
        '60',
        String(me.enrolled),
        me.certifications.length
          ? counted(me.certifications.length, 'certification')
          : 'None mapped',
      ]);
    });

    await test.step('2. Type "Economics" in the subject search', async () => {
      await search.fill('Economics');
      await expect(meRow, 'TC-720 ER-2: Managerial Economics matches').toBeVisible();
      await expect(obRow, 'TC-720 ER-2: the other subject is hidden').toBeHidden();
      await expect(
        panel.getByText(`Subjects: ${economics}`, { exact: true }),
        'TC-720 ER-2: the status bar counts only the subjects that mention it',
      ).toBeVisible();
    });

    await test.step('3. Replace it with "no-such-subject"', async () => {
      await search.fill('no-such-subject');
      await expect(
        panel.getByText('No subject matches “no-such-subject”.'),
        'TC-720 ER-3: the table says nothing matches',
      ).toBeVisible();
    });
  });

  test('Import subjects: check first, then import, then update @TC-721', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const code = newCode('E2ESUB');
    const name = `E2E Subject ${RUN}`;
    const header =
      'code,name,stage,dimension,semester,model_type,teaching_hours,self_learning_hours_required';
    const first = {
      name: 'subjects-new.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(`${header}\n${code},${name},EXCEL,PROFESSIONAL,2,INSTRUCTOR_LED,30,10\n`),
    };
    const second = {
      name: 'subjects-renamed.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(
        `${header}\n${code},${name} (renamed),EXCEL,PROFESSIONAL,2,INSTRUCTOR_LED,30,10\n`,
      ),
    };
    const checkFirst = page.getByRole('button', { name: 'Check first' });
    const importButton = page.getByRole('button', { name: 'Import', exact: true });
    const report = page.getByRole('status').filter({ hasText: /Dry run ·|Imported ·/ });
    const subjects = page.getByRole('tabpanel', { name: 'Subjects' });

    await test.step('1. Open /admin/catalogue and press "Import subjects"', async () => {
      await page.goto('/admin/catalogue');
      await page.getByRole('button', { name: 'Import subjects' }).click();
      await expect(
        page.getByRole('heading', { level: 2, name: 'Import subjects' }),
        'TC-721 ER-1: the import panel opens',
      ).toBeVisible();
      await expect(
        page.getByText('code, name, stage, dimension, semester, model_type', { exact: true }),
        'TC-721 ER-1: it names the columns a file needs',
      ).toBeVisible();
      await expect(page.getByText('No file chosen'), 'TC-721 ER-1: no file yet').toBeVisible();
      await expect(checkFirst, 'TC-721 ER-1: Check first waits for a file').toBeDisabled();
      await expect(importButton, 'TC-721 ER-1: Import waits for a dry run').toBeDisabled();
    });

    await test.step('2. Choose the first CSV in the test data with "Choose a CSV"', async () => {
      await page.getByLabel('Choose a CSV').setInputFiles(first);
      await expect(
        page.getByText('subjects-new.csv'),
        'TC-721 ER-2: the file name is shown',
      ).toBeVisible();
      await expect(checkFirst, 'TC-721 ER-2: Check first is available').toBeEnabled();
      await expect(importButton, 'TC-721 ER-2: Import still waits for a dry run').toBeDisabled();
    });

    await test.step('3. Press "Check first"', async () => {
      await checkFirst.click();
      await expect(report, 'TC-721 ER-3: the dry run reports one row to add').toContainText(
        'Dry run · 1 to add, 0 to update, 0 in error. Nothing has been written yet.',
      );
      await expect(
        page.getByRole('row', { name: `2 ${code} create` }),
        'TC-721 ER-3: line 2 of the file would be created',
      ).toBeVisible();
      await expect(
        subjects.getByRole('row').filter({ hasText: code }),
        'TC-721 ER-3: nothing is in the catalogue yet',
      ).toHaveCount(0);
      await expect(importButton, 'TC-721 ER-3: Import becomes available').toBeEnabled();
    });

    await test.step('4. Press Import', async () => {
      await importButton.click();
      await expect(
        page.getByRole('status').filter({ hasText: '1 subject added, 0 updated.' }),
        'TC-721 ER-4: the import is confirmed',
      ).toBeVisible();
      await expect(report, 'TC-721 ER-4: the report now reads Imported').toContainText(
        'Imported · 1 to add, 0 to update, 0 in error.',
      );
      await expect(
        subjects
          .getByRole('row')
          .filter({ hasText: `${code} · ${name}` })
          .getByRole('cell'),
        'TC-721 ER-4: the subject is in the catalogue as the file described it',
      ).toHaveText([
        `${code} · ${name} Instructor Led · 16 weeks`,
        'Professional',
        'Excel',
        '2',
        '40',
        '0',
        'None mapped',
      ]);
    });

    await test.step('5. Choose the second CSV in the test data with "Choose a CSV", then press "Check first"', async () => {
      await page.getByLabel('Choose a CSV').setInputFiles(second);
      await checkFirst.click();
      await expect(report, 'TC-721 ER-5: the dry run reports one row to update').toContainText(
        'Dry run · 0 to add, 1 to update, 0 in error.',
      );
      await expect(
        page.getByRole('row', { name: `2 ${code} update` }),
        'TC-721 ER-5: line 2 would update the existing subject',
      ).toBeVisible();
    });

    await test.step('6. Press Import', async () => {
      await importButton.click();
      await expect(
        page.getByRole('status').filter({ hasText: '0 subjects added, 1 updated.' }),
        'TC-721 ER-6: the update is confirmed',
      ).toBeVisible();
      await expect(
        subjects.getByRole('row').filter({ hasText: `${code} · ${name} (renamed)` }),
        'TC-721 ER-6: the subject carries its new name',
      ).toBeVisible();
    });
  });

  test('Import refuses a file with missing columns and reports a bad row @TC-722', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const code = newCode('E2ESUB');
    const missing = {
      name: 'two-columns.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(`code,name\n${code},Missing columns\n`),
    };
    const badRow = {
      name: 'bad-stage.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(
        `code,name,stage,dimension,semester,model_type\n${code},Bad stage,LUNCH,PROFESSIONAL,1,INSTRUCTOR_LED\n`,
      ),
    };
    const importButton = page.getByRole('button', { name: 'Import', exact: true });

    await test.step('1. Open /admin/catalogue and press "Import subjects"', async () => {
      await page.goto('/admin/catalogue');
      await page.getByRole('button', { name: 'Import subjects' }).click();
      await expect(
        page.getByRole('heading', { level: 2, name: 'Import subjects' }),
        'TC-722 ER-1: the import panel opens',
      ).toBeVisible();
    });

    await test.step('2. Choose the two-column CSV in the test data with "Choose a CSV", then press "Check first"', async () => {
      await page.getByLabel('Choose a CSV').setInputFiles(missing);
      await page.getByRole('button', { name: 'Check first' }).click();
      await expect(
        page.getByRole('alert').filter({ hasText: 'missing these columns' }),
        'TC-722 ER-2: the file is refused, naming the missing columns',
      ).toContainText('The CSV is missing these columns: dimension, model_type, semester, stage.');
      await expect(importButton, 'TC-722 ER-2: Import stays unavailable').toBeDisabled();
    });

    await test.step('3. Choose the bad-stage CSV in the test data with "Choose a CSV", then press "Check first"', async () => {
      await page.getByLabel('Choose a CSV').setInputFiles(badRow);
      await page.getByRole('button', { name: 'Check first' }).click();
      await expect(
        page.getByRole('status').filter({ hasText: 'Dry run ·' }),
        'TC-722 ER-3: the dry run counts one row in error',
      ).toContainText('Dry run · 0 to add, 0 to update, 1 in error.');
      const row = page.getByRole('row').filter({ hasText: code });
      await expect(row, 'TC-722 ER-3: the row is marked error').toContainText('error');
      await expect(row, 'TC-722 ER-3: the detail names the column and the value').toContainText(
        "stage: 'LUNCH' is not one of",
      );
    });

    await test.step('4. Press Cancel', async () => {
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      await expect(
        page.getByRole('heading', { level: 2, name: 'Import subjects' }),
        'TC-722 ER-4: the panel closes',
      ).toBeHidden();
      await expect(
        page.getByRole('tabpanel', { name: 'Subjects' }).getByRole('row').filter({ hasText: code }),
        'TC-722 ER-4: nothing was imported',
      ).toHaveCount(0);
    });
  });

  test('Certifications ↔ badges shows which certifications count towards each badge @TC-723', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    // How many students have claimed a badge through a certification is other
    // cases' doing, so the Claims counts are read from the catalogue itself.
    const certifications = await body<{ name: string; badge_code: string; claims: number }[]>(
      await page.request.get('/api/admin/approved-certifications'),
      'GET /api/admin/approved-certifications',
    );
    const claimsOn = (badge: string) =>
      certifications.filter((c) => c.badge_code === badge).reduce((n, c) => n + c.claims, 0);
    const negotiation = certifications.find(
      (c) => c.name === 'Successful Negotiation: Essential Strategies and Skills',
    )!;
    const panel = page.getByRole('tabpanel', { name: 'Certifications and badges' });
    const badgeRow = (name: string) =>
      panel.getByRole('row').filter({ has: page.getByRole('button', { name, exact: true }) });

    await test.step('1. Open /admin/catalogue and open the "Certifications ↔ badges" tab', async () => {
      await page.goto('/admin/catalogue');
      await page.getByRole('tab', { name: /^Certifications ↔ badges/ }).click();
      await expect(
        panel.getByText(
          'Badges cannot be added or edited here. This screen decides which certifications count towards each.',
        ),
        'TC-723 ER-1: the badges themselves are not editable here',
      ).toBeVisible();
      await expect(
        badgeRow('Microsoft Excel – Foundation TECH-EXCEL-FOUNDATION').getByRole('cell'),
        'TC-723 ER-1: the Excel badge with its seeded certification',
      ).toHaveText([
        /^Microsoft Excel – Foundation\s*TECH-EXCEL-FOUNDATION$/,
        'Platform / Technical Skills',
        /Excel Skills for Business: Essentials/,
        'Mentor',
        String(claimsOn('TECH-EXCEL-FOUNDATION')),
        'Active',
      ]);
      await expect(
        badgeRow('Interview Ready RDY-INTERVIEW').getByRole('cell'),
        'TC-723 ER-1: a readiness badge is awarded by staff and takes no certification',
      ).toHaveText([
        /^Interview Ready\s*RDY-INTERVIEW$/,
        'Interview Readiness',
        'None mapped',
        'Award',
        String(claimsOn('RDY-INTERVIEW')),
        'Active',
      ]);
    });

    await test.step('2. Choose "Mapped" in the Certifications filter', async () => {
      await page
        .getByRole('combobox', {
          name: 'Filter badges by whether certifications are mapped to them',
        })
        .selectOption('MAPPED');
      await expect(
        badgeRow('Negotiation MGR-NEGOTIATION'),
        'TC-723 ER-2: Negotiation has one',
      ).toBeVisible();
      await expect(
        badgeRow('Data Visualisation SEC-BA-DATA-VISUALISATION'),
        'TC-723 ER-2: Data Visualisation has one',
      ).toBeVisible();
      await expect(
        badgeRow('Interview Ready RDY-INTERVIEW'),
        'TC-723 ER-2: badges with none are hidden',
      ).toBeHidden();
    });

    await test.step('3. Press the badge "Negotiation"', async () => {
      await page.getByRole('button', { name: 'Negotiation MGR-NEGOTIATION', exact: true }).click();
      const drawer = page.getByRole('complementary', { name: 'Badge detail' });
      await expect(
        drawer.getByRole('heading', { level: 2, name: 'Badge · Negotiation' }),
        'TC-723 ER-3: the badge opens beside the table',
      ).toBeVisible();
      await expect(drawer, 'TC-723 ER-3: its code, area, stage and points').toContainText(
        /Code\s*MGR-NEGOTIATION\s*Skill area\s*Managerial Skills\s*Stage\s*Elevate\s*Points\s*15/,
      );
      await expect(
        drawer.getByRole('link', {
          name: 'Successful Negotiation: Essential Strategies and Skills',
        }),
        'TC-723 ER-3: the accepted certification links to the course',
      ).toHaveAttribute('href', 'https://www.coursera.org/learn/negotiation-skills');
      await expect(
        drawer.getByText(
          `University of Michigan / Coursera · ${counted(negotiation.claims, 'claim')} ·`,
        ),
        'TC-723 ER-3: its provider and claim count',
      ).toBeVisible();
      await expect(
        drawer.getByText('Programme-wide', { exact: true }),
        'TC-723 ER-3: its scope',
      ).toBeVisible();
    });
  });

  test('Add a certification to a badge, take it off the catalogue and restore it @TC-724', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const name = `E2E Certificate ${RUN}`;
    const drawer = page.getByRole('complementary', { name: 'Badge detail' });
    const powerBi = page
      .getByRole('tabpanel', { name: 'Certifications and badges' })
      .getByRole('row')
      .filter({ has: page.getByRole('button', { name: 'Power BI TECH-POWER-BI', exact: true }) });
    let createdId: string | null = null;
    page.on('response', async (response) => {
      if (
        response.request().method() === 'POST' &&
        response.url().endsWith('/api/admin/approved-certifications') &&
        response.ok()
      ) {
        createdId = ((await response.json()) as { id: string }).id;
      }
    });

    try {
      await test.step('1. Open /admin/catalogue and press "Add certification"', async () => {
        await page.goto('/admin/catalogue');
        await page.getByRole('button', { name: 'Add certification' }).click();
        await expect(
          page.getByRole('tab', { name: /^Certifications ↔ badges/ }),
          'TC-724 ER-1: the screen moves to the badges tab',
        ).toHaveAttribute('aria-selected', 'true');
        await expect(
          page.getByRole('heading', { level: 2, name: 'New certification' }),
          'TC-724 ER-1: the form opens',
        ).toBeVisible();
      });

      await test.step('2. Type the name, provider and link from the test data, choose "Power BI · 15 pts" in "Badge it counts towards" and keep "Every college and course" in "Applies to"', async () => {
        await page.getByRole('textbox', { name: 'Certification' }).fill(name);
        await page.getByRole('textbox', { name: 'Provider' }).fill('E2E Provider');
        await page
          .getByRole('textbox', { name: 'Link (optional)' })
          .fill('https://example.invalid/course');
        await page
          .getByRole('combobox', { name: 'Badge it counts towards' })
          .selectOption({ label: 'Power BI · 15 pts' });
        await expect(
          page.getByRole('combobox', { name: 'Applies to' }),
          'TC-724 ER-2: programme-wide by default',
        ).toHaveValue('');
        await expect(
          page.getByText(
            'Category Platform / Technical Skills · 15 points · Excel stage — read off the badge, never typed.',
          ),
          'TC-724 ER-2: the category, points and stage come from the badge',
        ).toBeVisible();
      });

      await test.step('3. Press "Add to catalogue"', async () => {
        await page.getByRole('button', { name: 'Add to catalogue' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: `${name} now counts towards Power BI.` }),
          'TC-724 ER-3: the addition is confirmed',
        ).toBeVisible();
        await expect(
          page.getByRole('heading', { level: 2, name: 'New certification' }),
          'TC-724 ER-3: the form closes',
        ).toBeHidden();
        await expect(
          drawer.getByRole('heading', { name: 'Badge · Power BI' }),
          'TC-724 ER-3: the badge opens',
        ).toBeVisible();
        await expect(
          drawer.getByRole('link', { name }),
          'TC-724 ER-3: the certification is accepted there',
        ).toBeVisible();
        await expect(powerBi, 'TC-724 ER-3: the badge row lists it').toContainText(name);
      });

      await test.step('4. Press Remove on the new certification', async () => {
        await drawer.getByRole('button', { name: `Remove ${name} from the catalogue` }).click();
        await expect(
          page
            .getByRole('status')
            .filter({ hasText: `${name} is off the catalogue — evidence already filed keeps it.` }),
          'TC-724 ER-4: the removal is confirmed',
        ).toBeVisible();
        await expect(
          drawer.getByRole('button', { name: `Restore ${name} to the catalogue` }),
          'TC-724 ER-4: it can be restored',
        ).toBeVisible();
        await expect(powerBi, 'TC-724 ER-4: the badge row no longer lists it').not.toContainText(
          name,
        );
      });

      await test.step('5. Press Restore', async () => {
        await drawer.getByRole('button', { name: `Restore ${name} to the catalogue` }).click();
        await expect(
          page.getByRole('status').filter({ hasText: `${name} is back in the catalogue.` }),
          'TC-724 ER-5: the restore is confirmed',
        ).toBeVisible();
        await expect(
          drawer.getByRole('button', { name: `Remove ${name} from the catalogue` }),
          'TC-724 ER-5: it is on the catalogue again',
        ).toBeVisible();
        await expect(powerBi, 'TC-724 ER-5: the badge row lists it again').toContainText(name);
      });
    } finally {
      if (createdId) {
        const row = await page.request.get('/api/admin/approved-certifications');
        const all = (await row.json()) as Record<string, unknown>[];
        const mine = all.find((r) => r.id === createdId);
        if (mine && mine.active) {
          const keep = [
            'name',
            'provider',
            'badge_code',
            'evidence_type',
            'stage',
            'duration_text',
            'is_free',
            'url',
            'college_id',
            'course_id',
          ];
          await page.request.patch(`/api/admin/approved-certifications/${createdId}`, {
            data: { ...Object.fromEntries(keep.map((k) => [k, mine[k]])), active: false },
          });
        }
      }
    }
  });

  test('A certification needs a name and a web link @TC-725', async ({ page, signIn }) => {
    await signIn('admin');
    const name = `E2E Unsaved Certificate ${RUN}`;
    const form = page.getByRole('heading', { level: 2, name: 'New certification' });

    await test.step('1. Open /admin/catalogue and press "Add certification"', async () => {
      await page.goto('/admin/catalogue');
      await page.getByRole('button', { name: 'Add certification' }).click();
      await expect(form, 'TC-725 ER-1: the form opens').toBeVisible();
    });

    await test.step('2. Press "Add to catalogue" with every box empty', async () => {
      await page.getByRole('button', { name: 'Add to catalogue' }).click();
      await expect(
        page.getByRole('alert').filter({ hasText: 'Give it a name first' }),
        'TC-725 ER-2: a name is required',
      ).toBeVisible();
    });

    await test.step('3. Type the name from the test data and "www.example.com" in "Link (optional)", then press "Add to catalogue"', async () => {
      await page.getByRole('textbox', { name: 'Certification' }).fill(name);
      await page.getByRole('textbox', { name: 'Link (optional)' }).fill('www.example.com');
      await page.getByRole('button', { name: 'Add to catalogue' }).click();
      await expect(
        page.getByRole('alert').filter({ hasText: 'The link must start with http:// or https://' }),
        'TC-725 ER-3: a link must be a web address',
      ).toBeVisible();
      await expect(form, 'TC-725 ER-3: the form stays open').toBeVisible();
    });

    await test.step('4. Press Cancel', async () => {
      await page.getByRole('button', { name: 'Cancel' }).click();
      await expect(form, 'TC-725 ER-4: the form closes').toBeHidden();
    });

    await test.step('5. Type the name from the test data in the badge search', async () => {
      await page
        .getByRole('searchbox', { name: 'Search badges and the certifications mapped to them' })
        .fill(name);
      await expect(
        page.getByText('No badge matches these filters.'),
        'TC-725 ER-5: nothing was added',
      ).toBeVisible();
    });
  });

  test('Set a stage rule, change it and remove it @TC-726', async ({ page, signIn }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Stage College ${RUN}` });
    const programme = `MBA · ${spine.collegeName}`;
    const panel = page.getByRole('tabpanel', { name: 'Stage rules' });
    const ruleRow = panel.getByRole('row').filter({ hasText: programme });

    await test.step('1. Open /admin/catalogue and open the "Stage rules" tab', async () => {
      await page.goto('/admin/catalogue');
      await page.getByRole('tab', { name: 'Stage rules' }).click();
      await expect(
        panel.getByRole('button', { name: 'Set rule' }),
        'TC-726 ER-1: Set rule waits for a programme',
      ).toBeDisabled();
    });

    await test.step('2. Choose the programme in the test data in "Programme"', async () => {
      await panel
        .getByRole('combobox', { name: 'Programme' })
        .selectOption({ label: `MBA — General MBA · ${spine.collegeName}` });
      await expect(
        panel.getByText(
          `No stage rule on ${programme} yet. Promotion will move its semester and leave every student's stage alone.`,
        ),
        'TC-726 ER-2: the programme has no rule yet',
      ).toBeVisible();
    });

    await test.step('3. Type 3 in "Semester", choose "Excel-Adv" in "Stage", then press "Set rule"', async () => {
      await panel.getByRole('spinbutton', { name: 'Semester' }).fill('3');
      await panel.getByRole('combobox', { name: 'Stage' }).selectOption({ label: 'Excel-Adv' });
      await panel.getByRole('button', { name: 'Set rule' }).click();
      await expect(
        page.getByRole('status').filter({ hasText: `Semester 3 of ${programme} is Excel-Adv.` }),
        'TC-726 ER-3: the rule is confirmed',
      ).toBeVisible();
      await expect(ruleRow.getByRole('cell'), 'TC-726 ER-3: the rule is listed').toHaveText([
        programme,
        '3',
        'Excel-Adv',
        /Remove/,
      ]);
      await expect(
        ruleRow.getByRole('button', { name: `Remove the rule for semester 3 of ${programme}` }),
        'TC-726 ER-3: with a Remove button',
      ).toBeVisible();
    });

    await test.step('4. Choose "Elevate" in "Stage", then press "Set rule" again', async () => {
      await panel.getByRole('combobox', { name: 'Stage' }).selectOption({ label: 'Elevate' });
      await panel.getByRole('button', { name: 'Set rule' }).click();
      await expect(
        page.getByRole('status').filter({ hasText: `Semester 3 of ${programme} is Elevate.` }),
        'TC-726 ER-4: the change is confirmed',
      ).toBeVisible();
      await expect(ruleRow, 'TC-726 ER-4: still one rule for semester 3, not two').toHaveCount(1);
      await expect(ruleRow.getByRole('cell').nth(2), 'TC-726 ER-4: now Elevate').toHaveText(
        'Elevate',
      );
    });

    await test.step('5. Press Remove on the rule for semester 3', async () => {
      await ruleRow
        .getByRole('button', { name: `Remove the rule for semester 3 of ${programme}` })
        .click();
      await expect(
        page.getByRole('status').filter({ hasText: 'Semester 3 no longer names a stage.' }),
        'TC-726 ER-5: the removal is confirmed',
      ).toBeVisible();
      await expect(
        panel.getByText(`No stage rule on ${programme} yet.`, { exact: false }),
        'TC-726 ER-5: the programme has no rule again',
      ).toBeVisible();
    });
  });

  test('Interview tracks are listed read-only on the Catalogue @TC-727', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const panel = page.getByRole('tabpanel', { name: 'Interview tracks' });

    await test.step('1. Open /admin/catalogue and open the "Interview tracks" tab', async () => {
      await page.goto('/admin/catalogue');
      await page.getByRole('tab', { name: /^Interview tracks/ }).click();
      for (const [label, key] of [
        ['Human Resources (HR)', 'hr'],
        ['Digital Marketing (DM)', 'dm'],
        ['Business Analytics (BA)', 'ba'],
        ['Financial Analytics (FA)', 'fa'],
      ]) {
        const row = panel.getByRole('row').filter({ hasText: label });
        await expect(
          row.getByRole('cell').nth(1),
          `TC-727 ER-1: ${label} has the key ${key}`,
        ).toHaveText(key);
        await expect(
          row.getByRole('cell').nth(2),
          `TC-727 ER-1: ${label} runs the four phases`,
        ).toHaveText('opening · probing · deep_dive · wrap_up');
      }
      await expect(
        panel.getByText('Read-only here. Tracks are edited on Interview questions.'),
        'TC-727 ER-1: the tab says where tracks are edited',
      ).toBeVisible();
      await expect(
        panel.getByRole('button'),
        'TC-727 ER-1: nothing here can be changed',
      ).toHaveCount(0);
    });
  });

  test('Copy one programme’s catalogue into another @TC-728', async ({ page, signIn }) => {
    await signIn('admin');
    const from = await createSpine(page, { name: `E2E Copy From ${RUN}` });
    const to = await createSpine(page, { name: `E2E Copy To ${RUN}` });
    await body(
      await page.request.put('/api/admin/catalogue/stage-rules', {
        data: { course_id: from.courseId, semester: 2, stage: 'EXCEL' },
      }),
      'PUT a stage rule',
    );
    const certificate = await body<{ id: string }>(
      await page.request.post('/api/admin/approved-certifications', {
        data: {
          name: `E2E Programme Certificate ${RUN}`,
          provider: 'E2E Provider',
          badge_code: 'TECH-SQL',
          stage: 'EXCEL',
          url: null,
          course_id: from.courseId,
        },
      }),
      'POST a certification for one course',
    );
    const copy = page.getByRole('heading', {
      level: 3,
      name: 'Copy a catalogue between programmes',
    });
    const fromLabel = `MBA — General MBA · ${from.collegeName}`;
    const toLabel = `MBA — General MBA · ${to.collegeName}`;

    try {
      await test.step('1. Open /admin/catalogue, open the "Certifications ↔ badges" tab and press "Copy to course…"', async () => {
        await page.goto('/admin/catalogue');
        await page.getByRole('tab', { name: /^Certifications ↔ badges/ }).click();
        await page.getByRole('button', { name: 'Copy to course…' }).click();
        await expect(copy, 'TC-728 ER-1: the copy panel opens').toBeVisible();
        for (const part of ['Approved certifications', 'Badge map', 'Stage rules']) {
          await expect(
            page.getByRole('checkbox', { name: new RegExp(`^${part}`) }),
            `TC-728 ER-1: "${part}" is ticked`,
          ).toBeChecked();
        }
        await expect(
          page.getByRole('button', { name: 'Check first' }),
          'TC-728 ER-1: nothing to check yet',
        ).toBeDisabled();
      });

      await test.step('2. Choose the source programme in "Copy from" and the destination in "Copy into"', async () => {
        await page.getByRole('combobox', { name: 'Copy from' }).selectOption({ label: fromLabel });
        await page.getByRole('combobox', { name: 'Copy into' }).selectOption({ label: toLabel });
        await expect(
          page.getByText(
            `${from.collegeName} · Management Studies — 1 certification · 0 badge overrides · 1 stage rule`,
          ),
          'TC-728 ER-2: the source holds one certification and one stage rule',
        ).toBeVisible();
        await expect(
          page.getByText(
            `${to.collegeName} · Management Studies — 0 certifications · 0 badge overrides · 0 stage rules`,
          ),
          'TC-728 ER-2: the destination holds nothing yet',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Copy', exact: true }),
          'TC-728 ER-2: Copy waits for a dry run',
        ).toBeDisabled();
      });

      await test.step('3. Press "Check first"', async () => {
        await page.getByRole('button', { name: 'Check first' }).click();
        const preview = page.getByRole('status').filter({ hasText: 'Dry run ·' });
        await expect(preview, 'TC-728 ER-3: one certification would be copied').toContainText(
          'certifications: 1 copied, 0 already there',
        );
        await expect(preview, 'TC-728 ER-3: one stage rule would be copied').toContainText(
          'stage_rules: 1 copied, 0 already there',
        );
        await expect(preview, 'TC-728 ER-3: nothing written yet').toContainText(
          '— nothing has been written.',
        );
      });

      await test.step('4. Press Copy', async () => {
        await page.getByRole('button', { name: 'Copy', exact: true }).click();
        await expect(
          page
            .getByRole('status')
            .filter({ hasText: `2 rows copied into MBA · ${to.collegeName}.` }),
          'TC-728 ER-4: the copy is confirmed',
        ).toBeVisible();
      });

      await test.step('5. Open the "Stage rules" tab and choose the destination programme in "Programme"', async () => {
        await page.getByRole('tab', { name: 'Stage rules' }).click();
        const panel = page.getByRole('tabpanel', { name: 'Stage rules' });
        await panel.getByRole('combobox', { name: 'Programme' }).selectOption({ label: toLabel });
        await expect(
          panel
            .getByRole('row')
            .filter({ hasText: `MBA · ${to.collegeName}` })
            .getByRole('cell')
            .nth(2),
          'TC-728 ER-5: the destination now has the rule, semester 2 is Excel',
        ).toHaveText('Excel');
      });
    } finally {
      const rules = await page.request.get('/api/admin/catalogue/stage-rules');
      if (rules.ok()) {
        for (const rule of (await rules.json()) as { id: string; course_id: string }[]) {
          if (rule.course_id === from.courseId || rule.course_id === to.courseId) {
            await page.request.delete(`/api/admin/catalogue/stage-rules/${rule.id}`);
          }
        }
      }
      const certificates = await page.request.get('/api/admin/approved-certifications');
      if (certificates.ok()) {
        const keep = [
          'name',
          'provider',
          'badge_code',
          'evidence_type',
          'stage',
          'duration_text',
          'is_free',
          'url',
          'college_id',
          'course_id',
        ];
        for (const row of (await certificates.json()) as Record<string, unknown>[]) {
          if (row.active && (row.id === certificate.id || row.course_id === to.courseId)) {
            await page.request.patch(`/api/admin/approved-certifications/${row.id}`, {
              data: { ...Object.fromEntries(keep.map((k) => [k, row[k]])), active: false },
            });
          }
        }
      }
    }
  });

  // ---------------------------------------------------- Interview records

  test('Interview records lists a student’s mock interview, and the filters narrow it @TC-730', async ({
    page,
    signIn,
    browser,
    baseURL,
  }) => {
    await signIn('admin');
    const taken = await takeMockInterview(page, browser, baseURL!);
    // THIS case's interview, by the session id the grid keys its rows on:
    // other cases leave interviews of their own on the database.
    const mine = page.getByRole('row').and(page.locator(`[row-id="${taken.sessionId}"]`));

    await test.step('1. Open /admin/interviews', async () => {
      await page.goto('/admin/interviews');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Interview records' }),
        'TC-730 ER-1: the Interview records screen opens',
      ).toBeVisible();
      await expect(
        mine.getByRole('gridcell', { name: SEED.studentUsn }),
        'TC-730 ER-1: the interview is listed with the student’s USN',
      ).toBeVisible();
      await expect(mine, 'TC-730 ER-1: with the student’s name').toContainText(SEED.studentName);
      await expect(
        mine.getByRole('gridcell', { name: 'HR', exact: true }),
        'TC-730 ER-1: on the HR track',
      ).toBeVisible();
      await expect(
        mine.getByRole('gridcell', { name: '—', exact: true }),
        'TC-730 ER-1: not scored',
      ).toBeVisible();
      await expect(
        mine.getByRole('gridcell', { name: 'No audio' }),
        'TC-730 ER-1: no recording',
      ).toBeVisible();
    });

    await test.step('2. Choose "Failed" in Status', async () => {
      await chooseRecordsFilter(page, 'Status', 'Failed', { status: 'failed', track: null });
      await expect(
        mine,
        'TC-730 ER-2: the interview that could not reach the interviewer is listed',
      ).toBeVisible();
    });

    await test.step('3. Choose "Completed" in Status', async () => {
      const completed = await interviewsListed(page, { status: 'completed' });
      await chooseRecordsFilter(page, 'Status', 'Completed', { status: 'completed', track: null });
      await expect(mine, 'TC-730 ER-3: the failed interview is not listed').toHaveCount(0);
      await expectListed(
        page,
        completed,
        'TC-730 ER-3: the list holds only completed interviews, or says none matches',
      );
    });

    await test.step('4. Choose "All" in Status', async () => {
      await chooseRecordsFilter(page, 'Status', 'All', { status: null, track: null });
      await expect(mine, 'TC-730 ER-4: the interview is listed again').toBeVisible();
    });

    await test.step('5. Choose "Digital Marketing" in Track', async () => {
      const marketing = await interviewsListed(page, { track: 'dm' });
      await chooseRecordsFilter(page, 'Track', 'Digital Marketing', { status: null, track: 'dm' });
      await expect(mine, 'TC-730 ER-5: the HR interview is not listed').toHaveCount(0);
      await expectListed(
        page,
        marketing,
        'TC-730 ER-5: the list holds only Digital Marketing interviews, or says none matches',
      );
    });

    await test.step('6. Choose "Human Resources" in Track', async () => {
      await chooseRecordsFilter(page, 'Track', 'Human Resources', { status: null, track: 'hr' });
      await expect(mine, 'TC-730 ER-6: the HR interview is listed again').toBeVisible();
    });
  });

  test('Open an interview record: how it ended, its report and its transcript @TC-731', async ({
    page,
    signIn,
    browser,
    baseURL,
  }) => {
    await signIn('admin');
    const taken = await takeMockInterview(page, browser, baseURL!);
    // The row of THIS case's interview, and what the student's whole history
    // holds, for the score chart's count: other cases add interviews of their own.
    const mine = page.getByRole('row').and(page.locator(`[row-id="${taken.sessionId}"]`));
    const history = await body<{ overall_score: number | null }[]>(
      await page.request.get(`/api/mentor/students/${taken.studentId}/interviews`),
      'GET the student’s interviews',
    );
    const scored = history.filter((h) => h.overall_score !== null).length;
    const record = page.getByRole('region', { name: SEED.studentName });

    await test.step('1. Open /admin/interviews', async () => {
      await page.goto('/admin/interviews');
      await expect(
        mine.getByRole('gridcell', { name: SEED.studentName }),
        'TC-731 ER-1: the student’s interview is listed',
      ).toBeVisible();
    });

    await test.step('2. Press the newest row for Test Student', async () => {
      await mine.getByRole('gridcell', { name: SEED.studentName }).click();
      await expect(
        record.getByRole('heading', { level: 2, name: SEED.studentName }),
        'TC-731 ER-2: the record opens under the list',
      ).toBeVisible();
      await expect(
        record.getByText(`${SEED.studentUsn} · Human Resources ·`),
        'TC-731 ER-2: USN, track, start and duration',
      ).toHaveText(new RegExp(`^\\s*${SEED.studentUsn} · Human Resources · .+ · \\d+:\\d{2}\\s*$`));
      await expect(
        record.getByText('Failed', { exact: true }),
        'TC-731 ER-2: status Failed',
      ).toBeVisible();
      await expect(
        record.getByText('No audio', { exact: true }),
        'TC-731 ER-2: no recording',
      ).toBeVisible();
      await expect(
        record.getByRole('button', { name: 'Download recording' }),
        'TC-731 ER-2: nothing to download, so no Download button',
      ).toHaveCount(0);
      await expect(
        record.getByRole('status').filter({ hasText: 'INTERVIEW_RECORDING_ENABLED' }),
        'TC-731 ER-2: the record says which switch kept the audio',
      ).toContainText(
        'Voice recording is switched off on this server (INTERVIEW_RECORDING_ENABLED), so no college can record until the operator turns it on.',
      );
      await expect(
        record.getByText(
          /^\s*Ended: .+ · stopped in opening · 0 answers counted · 0 turns, 0 saved\s*$/,
        ),
        'TC-731 ER-2: how it ended, where it stopped, and turns emitted against turns saved',
      ).toBeVisible();
      await expect(
        record.getByRole('status').filter({ hasText: 'No readable report' }),
        'TC-731 ER-2: the Report tab says no report could be read',
      ).toContainText('No readable report (unavailable). The transcript is still saved.');
      await expect(
        record.getByText(`${counted(history.length, 'interview')} · ${scored} scored`, {
          exact: true,
        }),
        'TC-731 ER-2: the score chart counts the student’s interviews and how many were scored',
      ).toBeVisible();
    });

    await test.step('3. Press the Transcript tab', async () => {
      await record.getByRole('button', { name: 'Transcript', exact: true }).click();
      await expect(
        record.getByRole('status').filter({ hasText: 'No turns were saved for this interview.' }),
        'TC-731 ER-3: nothing was said, so no turn was saved',
      ).toBeVisible();
    });

    await test.step('4. Press "Close this record"', async () => {
      await record.getByRole('button', { name: 'Close this record' }).click();
      await expect(record, 'TC-731 ER-4: the record closes').toBeHidden();
    });
  });

  test('Export the interview records as CSV @TC-732', async ({
    page,
    signIn,
    browser,
    baseURL,
  }) => {
    await signIn('admin');
    await takeMockInterview(page, browser, baseURL!);

    await test.step('1. Open /admin/interviews', async () => {
      await page.goto('/admin/interviews');
      await expect(
        page.getByRole('button', { name: 'Export CSV' }),
        'TC-732 ER-1: Export CSV is available once the list has loaded',
      ).toBeEnabled();
    });

    await test.step('2. Press "Export CSV"', async () => {
      const downloading = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Export CSV' }).click();
      const download = await downloading;
      expect(download.suggestedFilename(), 'TC-732 ER-2: the file is reep-interviews.csv').toBe(
        'reep-interviews.csv',
      );
      const text = await (
        await download.createReadStream()
      )
        .toArray()
        .then((chunks) => Buffer.concat(chunks as Buffer[]).toString('utf8'));
      const [header, ...lines] = text.trim().split(/\r?\n/);
      expect(header, 'TC-732 ER-2: summary columns only').toBe(
        'Name,USN,Started,Track,Status,Overall,Communication,Domain,Structure,Record',
      );
      expect(
        lines.some(
          (line) =>
            line.startsWith(`${SEED.studentName},${SEED.studentUsn},`) &&
            line.includes(',hr,failed,'),
        ),
        'TC-732 ER-2: the student’s failed HR interview is a row',
      ).toBe(true);
      await expect(
        page
          .getByRole('status')
          .filter({ hasText: 'Saved. Summary rows only, and on the export history.' }),
        'TC-732 ER-2: the screen confirms the extract',
      ).toBeVisible();
    });
  });

  test('Allow voice recording for a college, then switch it off again @TC-733', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Policy College ${RUN}` });
    const card = page.getByRole('region', { name: 'Interview policy' });
    const recording = card.getByRole('checkbox', { name: 'Allow voice recording' });
    const chooseCollege = async () => {
      await card
        .getByRole('combobox', { name: 'College' })
        .selectOption({ label: spine.collegeName });
    };

    await test.step('1. Open /admin/interviews and choose the college in the test data in the policy card’s "College"', async () => {
      await page.goto('/admin/interviews');
      await chooseCollege();
      await expect(
        card.getByText('Not configured'),
        'TC-733 ER-1: nobody has set this college’s policy',
      ).toBeVisible();
      await expect(
        card.getByText('deployment defaults'),
        'TC-733 ER-1: so the deployment defaults apply',
      ).toBeVisible();
      await expect(
        card
          .getByRole('status')
          .filter({ hasText: 'Voice recording is switched off on this server' }),
        'TC-733 ER-1: the card says the server itself records nothing',
      ).toContainText(
        'Voice recording is switched off on this server (INTERVIEW_RECORDING_ENABLED). “Allow voice recording” below records nothing until the operator turns it on.',
      );
      await expect(
        card.getByRole('checkbox', { name: 'Keep the transcript' }),
        'TC-733 ER-1: transcripts kept',
      ).toBeChecked();
      await expect(recording, 'TC-733 ER-1: recording off').not.toBeChecked();
      await expect(
        card.getByRole('spinbutton', { name: 'Keep for (days)' }),
        'TC-733 ER-1: 180 days',
      ).toHaveValue('180');
      await expect(
        card.getByRole('spinbutton', { name: 'Time limit (seconds)' }),
        'TC-733 ER-1: 480 seconds',
      ).toHaveValue('480');
      await expect(
        card.getByRole('spinbutton', { name: 'Completed per day' }),
        'TC-733 ER-1: 8 a day',
      ).toHaveValue('8');
      await expect(
        card.getByRole('spinbutton', { name: 'Attempts per day' }),
        'TC-733 ER-1: 20 attempts',
      ).toHaveValue('20');
    });

    await test.step('2. Tick "Allow voice recording" and press "Save policy"', async () => {
      await recording.check();
      await card.getByRole('button', { name: 'Save policy' }).click();
      await expect(
        card
          .getByRole('status')
          .filter({ hasText: 'Saved. Applies to interviews started from now on.' }),
        'TC-733 ER-2: the save is confirmed',
      ).toBeVisible();
      await expect(
        card.getByText('Configured', { exact: true }),
        'TC-733 ER-2: the college now has a policy',
      ).toBeVisible();
      await expect(
        card.getByText(`last changed by ${ACCOUNTS.admin.name}`),
        'TC-733 ER-2: and says who set it',
      ).toBeVisible();
    });

    await test.step('3. Reload the page and choose the college again', async () => {
      await page.reload();
      await chooseCollege();
      await expect(
        card.getByText('Configured', { exact: true }),
        'TC-733 ER-3: the policy was stored',
      ).toBeVisible();
      await expect(recording, 'TC-733 ER-3: recording is allowed').toBeChecked();
    });

    await test.step('4. Untick "Allow voice recording" and press "Save policy"', async () => {
      await recording.uncheck();
      await card.getByRole('button', { name: 'Save policy' }).click();
      await expect(
        card
          .getByRole('status')
          .filter({ hasText: 'Saved. Applies to interviews started from now on.' }),
        'TC-733 ER-4: the save is confirmed',
      ).toBeVisible();
    });

    await test.step('5. Reload the page and choose the college again', async () => {
      await page.reload();
      await chooseCollege();
      // The stored policy first: until it has loaded, the box shows the
      // screen's own default, which is also unticked.
      await expect(
        card.getByText('Configured', { exact: true }),
        'TC-733 ER-5: the college keeps a stored policy',
      ).toBeVisible();
      await expect(recording, 'TC-733 ER-5: recording is off again').not.toBeChecked();
    });
  });

  test('Attempts per day cannot be lower than completed per day @TC-734', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const spine = await createSpine(page, { name: `E2E Caps College ${RUN}` });
    const card = page.getByRole('region', { name: 'Interview policy' });

    await test.step('1. Open /admin/interviews and choose the college in the test data in the policy card’s "College"', async () => {
      await page.goto('/admin/interviews');
      await card
        .getByRole('combobox', { name: 'College' })
        .selectOption({ label: spine.collegeName });
      await expect(card.getByText('Not configured'), 'TC-734 ER-1: no policy yet').toBeVisible();
      await expect(
        card.getByRole('button', { name: 'Save policy' }),
        'TC-734 ER-1: Save policy is available',
      ).toBeEnabled();
    });

    await test.step('2. Type 10 in "Completed per day" and 5 in "Attempts per day"', async () => {
      await card.getByRole('spinbutton', { name: 'Completed per day' }).fill('10');
      await card.getByRole('spinbutton', { name: 'Attempts per day' }).fill('5');
      await expect(
        card
          .getByRole('alert')
          .filter({ hasText: 'Attempts per day cannot be lower than completed per day.' }),
        'TC-734 ER-2: the card refuses the pair',
      ).toBeVisible();
      await expect(
        card.getByRole('spinbutton', { name: 'Attempts per day' }),
        'TC-734 ER-2: the attempts box is marked invalid',
      ).toHaveAttribute('aria-invalid', 'true');
      await expect(
        card.getByRole('button', { name: 'Save policy' }),
        'TC-734 ER-2: Save policy is disabled',
      ).toBeDisabled();
    });

    await test.step('3. Reload the page and choose the college again', async () => {
      await page.reload();
      await card
        .getByRole('combobox', { name: 'College' })
        .selectOption({ label: spine.collegeName });
      await expect(
        card.getByText('Not configured'),
        'TC-734 ER-3: nothing was saved',
      ).toBeVisible();
      await expect(
        card.getByRole('spinbutton', { name: 'Completed per day' }),
        'TC-734 ER-3: still 8',
      ).toHaveValue('8');
      await expect(
        card.getByRole('spinbutton', { name: 'Attempts per day' }),
        'TC-734 ER-3: still 20',
      ).toHaveValue('20');
    });
  });

  test('Give a student their day’s interview attempts back, with a reason @TC-735', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const batch = await seededBatch(page);
    const seated = await body<{ student_id: string; email: string }[]>(
      await page.request.get(`/api/admin/cohorts/${batch.id}/students`),
      'GET the seeded batch students',
    );
    const studentId = seated.find((s) => s.email === ACCOUNTS.student.email)!.student_id;
    // The two ceilings are the college's policy (its MBA row, else its default
    // row, else the deployment's), which another case may have set: read them.
    const college = await seededCollege(page);
    const sheet = await body<{
      default: { daily_cap: number; attempt_cap: number } | null;
      courses: { course_id: string; daily_cap: number; attempt_cap: number }[];
      effective_default: { daily_cap: number; attempt_cap: number };
    }>(
      await page.request.get(`/api/admin/interview-policies/${college.id}`),
      'GET the seeded college’s interview policy',
    );
    const caps =
      sheet.courses.find((c) => c.course_id === batch.course_id) ??
      sheet.default ??
      sheet.effective_default;
    const reason = 'Two interviews dropped when the lab wifi went down';
    const give = page.getByRole('button', { name: 'Give the attempts back' });
    const capFact = fact(page.getByRole('main'), 'Interview cap today');

    await test.step('1. Open Test Student’s record at /admin/students/<student id>', async () => {
      await page.goto(`/admin/students/${studentId}`);
      await expect(capFact, 'TC-735 ER-1: the cap is not read until a reset reports it').toHaveText(
        'Not readable — see below',
      );
    });

    await test.step('2. Press "Reset daily cap"', async () => {
      await page.getByRole('button', { name: 'Reset daily cap' }).click();
      await expect(
        page.getByRole('textbox', { name: 'Why are you giving these attempts back?' }),
        'TC-735 ER-2: the screen asks why',
      ).toBeVisible();
      await expect(
        page.getByText('Attempts already counted are kept; the count restarts from now.'),
        'TC-735 ER-2: and says what the reset does',
      ).toBeVisible();
      await expect(give, 'TC-735 ER-2: nothing to confirm without a reason').toBeDisabled();
    });

    await test.step('3. Type three spaces in the box', async () => {
      await page
        .getByRole('textbox', { name: 'Why are you giving these attempts back?' })
        .fill('   ');
      await expect(give, 'TC-735 ER-3: blank is not a reason').toBeDisabled();
    });

    await test.step('4. Replace them with the reason in the test data', async () => {
      await page
        .getByRole('textbox', { name: 'Why are you giving these attempts back?' })
        .fill(reason);
      await expect(give, 'TC-735 ER-4: the confirm becomes available').toBeEnabled();
    });

    await test.step('5. Press "Give the attempts back"', async () => {
      await give.click();
      await expect(
        page.getByRole('status').filter({ hasText: 'may practise again' }),
        'TC-735 ER-5: the reset is confirmed with the time the window now starts',
      ).toHaveText(
        new RegExp(
          `${SEED.studentName} may practise again — the 24-hour window is now counted from \\d{2} \\w+ \\d{4}, \\d{2}:\\d{2}, and your reason is on the audit trail\\.$`,
        ),
      );
      await expect(
        capFact,
        'TC-735 ER-5: nothing counted since the reset, out of the default ceilings',
      ).toHaveText(
        new RegExp(
          `^0 of ${caps.daily_cap} completed · 0 of ${caps.attempt_cap} attempts · counted from \\d{2} \\w+ \\d{4}, \\d{2}:\\d{2}$`,
        ),
      );
      await expect(
        page.getByRole('textbox', { name: 'Why are you giving these attempts back?' }),
        'TC-735 ER-5: the box closes',
      ).toBeHidden();
    });
  });

  // -------------------------------------------------- Interview questions

  test('Interview questions shows the tracks as tabs, each with its interviewer @TC-740', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const tabs = page.getByRole('tablist', { name: 'Interview tracks' });
    const card = page.getByRole('region', { name: /\(HR\)$|\(FA\)$/ });

    await test.step('1. Open /admin/interview-questions', async () => {
      await page.goto('/admin/interview-questions');
      for (const label of [
        'Human Resources (HR)',
        'Digital Marketing (DM)',
        'Business Analytics (BA)',
        'Financial Analytics (FA)',
      ]) {
        await expect(
          tabs.getByRole('tab', { name: new RegExp(`^${label.replace(/[()]/g, '\\$&')} \\d+$`) }),
          `TC-740 ER-1: a tab for ${label}, with its question count`,
        ).toBeVisible();
      }
      await expect(
        tabs.getByRole('tab', { name: /^Human Resources \(HR\)/ }),
        'TC-740 ER-1: Human Resources is open first',
      ).toHaveAttribute('aria-selected', 'true');
      await expect(
        card.getByRole('heading', { level: 2, name: 'Human Resources (HR)' }),
        'TC-740 ER-1: its card',
      ).toBeVisible();
      await expect(card.getByText('Every college'), 'TC-740 ER-1: programme-wide').toBeVisible();
      await expect(
        card.getByText('Offered', { exact: true }),
        'TC-740 ER-1: offered to students',
      ).toBeVisible();
      await expect(
        card.getByRole('textbox', { name: 'Interviewer role' }),
        'TC-740 ER-1: the interviewer it plays',
      ).toHaveValue('an empathetic yet compliant Chief Human Resources Officer (CHRO)');
      await expect(
        card.getByRole('combobox', { name: 'Voice' }),
        'TC-740 ER-1: its voice',
      ).toHaveValue('kiara');
    });

    await test.step('2. Press the "Financial Analytics (FA)" tab', async () => {
      await tabs.getByRole('tab', { name: /^Financial Analytics \(FA\)/ }).click();
      await expect(
        tabs.getByRole('tab', { name: /^Financial Analytics \(FA\)/ }),
        'TC-740 ER-2: the tab is selected',
      ).toHaveAttribute('aria-selected', 'true');
      await expect(
        card.getByRole('heading', { level: 2, name: 'Financial Analytics (FA)' }),
        'TC-740 ER-2: its card',
      ).toBeVisible();
      await expect(card.getByText('FA', { exact: true }), 'TC-740 ER-2: its code').toBeVisible();
      await expect(
        card.getByRole('textbox', { name: 'Interviewer role' }),
        'TC-740 ER-2: the CFO persona',
      ).toHaveValue('a sharp, risk-conscious Managing Director / CFO');
      await expect(
        card.getByRole('textbox', { name: 'Sample question' }),
        'TC-740 ER-2: its sample question',
      ).toHaveValue(
        'Walk me through how a $10 depreciation expense flows through the three financial statements.',
      );
      await expect(
        card.getByRole('textbox', { name: 'Frameworks' }),
        'TC-740 ER-2: its frameworks',
      ).toHaveValue(
        'DCF modeling, financial ratios, risk mitigation, valuation techniques, M&A frameworks',
      );
      await expect(
        card.getByRole('combobox', { name: 'Voice' }),
        'TC-740 ER-2: its voice',
      ).toHaveValue('matthew');
      await expect(
        page.getByRole('region', { name: /^Questions · \d+$/ }),
        'TC-740 ER-2: the questions card follows the tab',
      ).toBeVisible();
    });
  });

  test('Add one question to a track @TC-741', async ({ page, signIn }) => {
    await signIn('admin');
    const track = await createTrack(page, `E2E Questions ${RUN}`);
    const questions = page.getByRole('region', { name: /^Questions · \d+$/ });
    const text = 'Tell me about a supplier you had to replace at short notice.';
    const addButton = page.getByRole('button', { name: `Add to ${track.label}` });

    try {
      await test.step('1. Open /admin/interview-questions and press the tab of the track in the test data', async () => {
        await page.goto('/admin/interview-questions');
        await page.getByRole('tab', { name: new RegExp(`^${track.label}`) }).click();
        await expect(
          page.getByRole('heading', { level: 2, name: 'Questions · 0' }),
          'TC-741 ER-1: no question yet',
        ).toBeVisible();
        await expect(
          questions.getByText('0 asked · 0 paused'),
          'TC-741 ER-1: none asked or paused',
        ).toBeVisible();
        await expect(
          questions.getByText('No question on this track yet.'),
          'TC-741 ER-1: the table says so',
        ).toBeVisible();
      });

      await test.step('2. Press "Add question"', async () => {
        await questions.getByRole('button', { name: 'Add question' }).click();
        await expect(
          questions.getByRole('combobox', { name: 'Phase' }).first(),
          'TC-741 ER-2: Probing by default',
        ).toHaveValue('probing');
        await expect(
          questions.getByText('0/600'),
          'TC-741 ER-2: the counter starts at 0/600',
        ).toBeVisible();
        await expect(addButton, 'TC-741 ER-2: nothing to add yet').toBeDisabled();
      });

      await test.step('3. Choose "Opening" in Phase and type "Short" in Question', async () => {
        await questions
          .getByRole('combobox', { name: 'Phase' })
          .first()
          .selectOption({ label: 'Opening' });
        await questions.getByRole('textbox', { name: 'Question' }).fill('Short');
        await expect(
          questions.getByText('5/600'),
          'TC-741 ER-3: the counter counts 5',
        ).toBeVisible();
        await expect(
          addButton,
          'TC-741 ER-3: a question needs at least 8 characters',
        ).toBeDisabled();
      });

      await test.step('4. Replace it with the question in the test data', async () => {
        await questions.getByRole('textbox', { name: 'Question' }).fill(text);
        await expect(addButton, 'TC-741 ER-4: the question can be added').toBeEnabled();
      });

      await test.step(`5. Press "Add to <track name>"`, async () => {
        await addButton.click();
        await expect(
          page.getByRole('status').filter({ hasText: 'Question added.' }),
          'TC-741 ER-5: the question is added',
        ).toBeVisible();
        const row = questions
          .getByRole('row')
          .filter({ has: page.getByRole('cell', { name: '1', exact: true }) });
        await expect(
          row.getByRole('combobox', { name: 'Phase' }),
          'TC-741 ER-5: row 1 is an Opening question',
        ).toHaveValue('opening');
        await expect(
          row.getByRole('textbox', { name: 'Question text' }),
          'TC-741 ER-5: with its text',
        ).toHaveValue(text);
        await expect(
          row.getByRole('button', { name: 'Pause this question' }),
          'TC-741 ER-5: and is asked',
        ).toContainText('Asked');
        await expect(
          page.getByRole('heading', { level: 2, name: 'Questions · 1' }),
          'TC-741 ER-5: one question',
        ).toBeVisible();
        await expect(
          questions.getByText('1 asked · 0 paused'),
          'TC-741 ER-5: one asked',
        ).toBeVisible();
        await expect(
          page.getByRole('tab', { name: `${track.label} 1` }),
          'TC-741 ER-5: the tab counts it',
        ).toBeVisible();
      });
    } finally {
      await removeTrack(page, track);
    }
  });

  test('Add many questions at once; a line with an unknown phase is skipped @TC-742', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const track = await createTrack(page, `E2E Bulk ${RUN}`);
    const questions = page.getByRole('region', { name: /^Questions · \d+$/ });
    const lines = [
      '[opening] Walk me through your background and why operations.',
      'probing | Describe a time you cut waste from a process.',
      '[lunch] What would you order for lunch today?',
    ];

    try {
      await test.step('1. Open /admin/interview-questions and press the tab of the track in the test data', async () => {
        await page.goto('/admin/interview-questions');
        await page.getByRole('tab', { name: new RegExp(`^${track.label}`) }).click();
        await expect(
          page.getByRole('heading', { level: 2, name: 'Questions · 0' }),
          'TC-742 ER-1: no question yet',
        ).toBeVisible();
      });

      await test.step('2. Press "Add many"', async () => {
        await questions.getByRole('button', { name: 'Add many' }).click();
        await expect(
          questions.getByRole('textbox', { name: 'One question per line' }),
          'TC-742 ER-2: a box for many lines',
        ).toBeVisible();
        await expect(
          questions.getByRole('button', { name: 'Add all' }),
          'TC-742 ER-2: nothing to add yet',
        ).toBeDisabled();
      });

      await test.step('3. Type the three lines in the test data in "One question per line"', async () => {
        await questions
          .getByRole('textbox', { name: 'One question per line' })
          .fill(lines.join('\n'));
        await expect(
          questions.getByRole('button', { name: 'Add all' }),
          'TC-742 ER-3: Add all is available',
        ).toBeEnabled();
      });

      await test.step('4. Press "Add all"', async () => {
        await questions.getByRole('button', { name: 'Add all' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: '2 questions added. 1 line skipped.' }),
          'TC-742 ER-4: two added, one skipped',
        ).toBeVisible();
        await expect(
          questions.getByRole('alert').filter({ hasText: 'Not added:' }),
          'TC-742 ER-4: the skipped line is named with its reason',
        ).toContainText(
          "line 3: unknown phase 'lunch' (use opening, probing, deep_dive or wrap_up)",
        );
        await expect(
          questions.getByRole('textbox', { name: 'Question text' }),
          'TC-742 ER-4: two questions listed',
        ).toHaveCount(2);
        await expect(
          questions.getByRole('textbox', { name: 'Question text' }).nth(0),
          'TC-742 ER-4: the first, from the bracket',
        ).toHaveValue('Walk me through your background and why operations.');
        await expect(
          questions.getByRole('combobox', { name: 'Phase' }).nth(0),
          'TC-742 ER-4: filed under Opening',
        ).toHaveValue('opening');
        await expect(
          questions.getByRole('textbox', { name: 'Question text' }).nth(1),
          'TC-742 ER-4: the second, from "probing |"',
        ).toHaveValue('Describe a time you cut waste from a process.');
        await expect(
          questions.getByRole('combobox', { name: 'Phase' }).nth(1),
          'TC-742 ER-4: filed under Probing',
        ).toHaveValue('probing');
        await expect(
          page.getByRole('heading', { level: 2, name: 'Questions · 2' }),
          'TC-742 ER-4: two questions',
        ).toBeVisible();
      });

      await test.step('5. Tick "Select every question on this page" and press Pause', async () => {
        await questions
          .getByRole('checkbox', { name: 'Select every question on this page' })
          .check();
        await expect(
          questions.getByText('Selected: 2', { exact: true }),
          'TC-742 ER-5: both are selected',
        ).toBeVisible();
        await questions.getByRole('button', { name: 'Pause', exact: true }).click();
        await expect(
          page.getByRole('status').filter({ hasText: '2 questions paused.' }),
          'TC-742 ER-5: both are paused at once',
        ).toBeVisible();
        await expect(
          questions.getByText('0 asked · 2 paused'),
          'TC-742 ER-5: none asked, two paused',
        ).toBeVisible();
      });
    } finally {
      await removeTrack(page, track);
    }
  });

  test('Edit a question’s phase and text, pause it and find it @TC-743', async ({
    page,
    signIn,
  }) => {
    await signIn('admin');
    const track = await createTrack(page, `E2E Edit ${RUN}`);
    await addQuestions(page, track, [
      '[opening] Walk me through your background and why operations.',
      '[probing] Describe a time you cut waste from a process.',
    ]);
    const questions = page.getByRole('region', { name: /^Questions · \d+$/ });
    const texts = questions.getByRole('textbox', { name: 'Question text' });
    const phases = questions.getByRole('combobox', { name: 'Phase' });
    const newText = 'Walk me through the process you know best, end to end.';
    const openTrack = async () => {
      await page.getByRole('tab', { name: new RegExp(`^${track.label}`) }).click();
      await texts.first().waitFor();
    };

    try {
      await test.step('1. Open /admin/interview-questions and press the tab of the track in the test data', async () => {
        await page.goto('/admin/interview-questions');
        await openTrack();
        await expect(texts, 'TC-743 ER-1: two questions').toHaveCount(2);
        await expect(
          questions.getByText('2 asked · 0 paused'),
          'TC-743 ER-1: both asked',
        ).toBeVisible();
      });

      await test.step('2. In row 1, choose "Deep dive" in Phase', async () => {
        await phases.nth(0).selectOption({ label: 'Deep dive' });
        await expect(phases.nth(0), 'TC-743 ER-2: row 1 is a Deep dive question').toHaveValue(
          'deep_dive',
        );
      });

      await test.step('3. In row 1, replace the question text with the one in the test data and move out of the box', async () => {
        await texts.nth(0).fill(newText);
        await texts.nth(0).blur();
        await expect(
          page.getByRole('status').filter({ hasText: 'Question saved.' }),
          'TC-743 ER-3: the text is saved',
        ).toBeVisible();
      });

      await test.step('4. In row 2, press the "Asked" status', async () => {
        await questions.getByRole('button', { name: 'Pause this question' }).nth(1).click();
        await expect(
          questions.getByRole('button', { name: 'Enable this question' }),
          'TC-743 ER-4: row 2 now reads Paused',
        ).toContainText('Paused');
        await expect(
          questions.getByText('1 asked · 1 paused'),
          'TC-743 ER-4: one asked, one paused',
        ).toBeVisible();
      });

      await test.step('5. Type "waste" in the question search', async () => {
        await questions
          .getByRole('searchbox', { name: 'Search the questions on this track' })
          .fill('waste');
        await expect(texts, 'TC-743 ER-5: only the matching question is listed').toHaveCount(1);
        await expect(texts.first(), 'TC-743 ER-5: the one about waste').toHaveValue(
          'Describe a time you cut waste from a process.',
        );
        await expect(
          questions.getByText('Rows: 1', { exact: true }),
          'TC-743 ER-5: the status bar counts one',
        ).toBeVisible();
      });

      await test.step('6. Reload the page and press the tab of the track again', async () => {
        await page.reload();
        await openTrack();
        await expect(phases.nth(0), 'TC-743 ER-6: the phase change was stored').toHaveValue(
          'deep_dive',
        );
        await expect(texts.nth(0), 'TC-743 ER-6: the new text was stored').toHaveValue(newText);
        await expect(
          questions.getByRole('button', { name: 'Enable this question' }),
          'TC-743 ER-6: row 2 is still paused',
        ).toContainText('Paused');
      });
    } finally {
      await removeTrack(page, track);
    }
  });

  test('A question edited below 8 characters is put back @TC-744', async ({ page, signIn }) => {
    await signIn('admin');
    const track = await createTrack(page, `E2E Short ${RUN}`);
    const original = 'Describe a time you cut waste from a process.';
    await addQuestions(page, track, [`[probing] ${original}`]);
    const questions = page.getByRole('region', { name: /^Questions · \d+$/ });
    const text = questions.getByRole('textbox', { name: 'Question text' });

    try {
      await test.step('1. Open /admin/interview-questions and press the tab of the track in the test data', async () => {
        await page.goto('/admin/interview-questions');
        await page.getByRole('tab', { name: new RegExp(`^${track.label}`) }).click();
        await expect(text, 'TC-744 ER-1: the question is listed').toHaveValue(original);
      });

      await test.step('2. Replace the question text with "Why?" and move out of the box', async () => {
        await text.fill('Why?');
        await text.blur();
        await expect(
          page
            .getByRole('alert')
            .filter({ hasText: 'A question needs at least 8 characters. Put back as it was.' }),
          'TC-744 ER-2: the edit is refused',
        ).toBeVisible();
        await expect(text, 'TC-744 ER-2: the row shows the question as it was').toHaveValue(
          original,
        );
      });

      await test.step('3. Reload the page and press the tab of the track again', async () => {
        await page.reload();
        await page.getByRole('tab', { name: new RegExp(`^${track.label}`) }).click();
        await expect(text, 'TC-744 ER-3: nothing was saved').toHaveValue(original);
      });
    } finally {
      await removeTrack(page, track);
    }
  });

  test('Remove a question after confirming @TC-745', async ({ page, signIn }) => {
    await signIn('admin');
    const track = await createTrack(page, `E2E Remove ${RUN}`);
    const question = 'Describe a time you cut waste from a process.';
    await addQuestions(page, track, [`[probing] ${question}`]);
    const questions = page.getByRole('region', { name: /^Questions · \d+$/ });
    const dialogs: string[] = [];

    try {
      await test.step('1. Open /admin/interview-questions and press the tab of the track in the test data', async () => {
        await page.goto('/admin/interview-questions');
        await page.getByRole('tab', { name: new RegExp(`^${track.label}`) }).click();
        await expect(
          questions.getByRole('textbox', { name: 'Question text' }),
          'TC-745 ER-1: the question is listed',
        ).toHaveValue(question);
      });

      await test.step('2. Press "Remove this question" and answer Cancel to the browser’s question', async () => {
        page.once('dialog', async (dialog) => {
          dialogs.push(dialog.message());
          await dialog.dismiss();
        });
        await questions.getByRole('button', { name: 'Remove this question' }).click();
        await expect
          .poll(() => dialogs, 'TC-745 ER-2: the browser asks first')
          .toEqual(['Remove this question?']);
        await expect(
          questions.getByRole('textbox', { name: 'Question text' }),
          'TC-745 ER-2: the question stays',
        ).toHaveValue(question);
      });

      await test.step('3. Press "Remove this question" again and answer OK', async () => {
        page.once('dialog', (dialog) => dialog.accept());
        await questions.getByRole('button', { name: 'Remove this question' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: 'Question removed.' }),
          'TC-745 ER-3: the question is removed',
        ).toBeVisible();
        await expect(
          questions.getByText('No question on this track yet.'),
          'TC-745 ER-3: the track is empty',
        ).toBeVisible();
        await expect(
          page.getByRole('tab', { name: `${track.label} 0` }),
          'TC-745 ER-3: the tab counts none',
        ).toBeVisible();
      });
    } finally {
      await removeTrack(page, track);
    }
  });

  test('Add a track, improve it and remove it @TC-746', async ({ page, signIn }) => {
    await signIn('admin');
    serial += 1;
    const code = `ops${RUN.toLowerCase()}${serial}`.slice(0, 20);
    const label = `E2E Operations ${RUN}`;
    const card = page.getByRole('region', { name: label });
    const created: { id?: string } = {};
    page.on('response', async (response) => {
      if (
        response.request().method() === 'POST' &&
        response.url().endsWith('/api/admin/interview-questions/tracks') &&
        response.ok()
      ) {
        created.id = ((await response.json()) as { track: { id: string } }).track.id;
      }
    });

    try {
      await test.step('1. Open /admin/interview-questions and press "Add track"', async () => {
        await page.goto('/admin/interview-questions');
        await page.getByRole('button', { name: 'Add track' }).click();
        await expect(
          page.getByRole('heading', { level: 2, name: 'New track' }),
          'TC-746 ER-1: the New track card opens',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Create track' }),
          'TC-746 ER-1: nothing to create yet',
        ).toBeDisabled();
      });

      await test.step('2. Type the Code, Name, Interviewer role, Sample question and the two Frameworks from the test data, and choose "matthew" in Voice', async () => {
        const form = page.getByRole('region', { name: 'New track' });
        await form.getByRole('textbox', { name: 'Code *' }).fill(code);
        await form.getByRole('textbox', { name: 'Name *' }).fill(label);
        await form
          .getByRole('textbox', { name: 'Interviewer role *' })
          .fill('an exacting but fair Head of Operations');
        await form
          .getByRole('textbox', { name: 'Sample question *' })
          .fill('Walk me through how you would cut a supplier lead time by a week.');
        await form.getByRole('textbox', { name: 'Frameworks' }).fill('SIPOC, OEE');
        await form.getByRole('combobox', { name: 'Voice' }).selectOption('matthew');
        await expect(
          page.getByRole('button', { name: 'Create track' }),
          'TC-746 ER-2: Create track is available',
        ).toBeEnabled();
      });

      await test.step('3. Press "Create track"', async () => {
        await page.getByRole('button', { name: 'Create track' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: 'Track created.' }),
          'TC-746 ER-3: the track is created',
        ).toBeVisible();
        await expect(
          page.getByRole('tab', { name: `${label} 0` }),
          'TC-746 ER-3: it has its own tab, selected',
        ).toHaveAttribute('aria-selected', 'true');
        await expect(
          card.getByText(code.toUpperCase(), { exact: true }),
          'TC-746 ER-3: its code',
        ).toBeVisible();
        await expect(card.getByText('Every college'), 'TC-746 ER-3: programme-wide').toBeVisible();
        await expect(
          card.getByText('Offered', { exact: true }),
          'TC-746 ER-3: offered to students',
        ).toBeVisible();
        await expect(
          card.getByRole('status').filter({ hasText: 'Saved, with a note:' }),
          'TC-746 ER-3: the server advises, without refusing, that two frameworks are too few',
        ).toContainText(
          'Only 2 framework(s). PROBING and DEEP_DIVE work through these one at a time; the shipped tracks carry 4 or more, and a track with fewer runs out of interview before wrap-up.',
        );
      });

      await test.step('4. Replace Frameworks with the four in the test data and press Save', async () => {
        await card
          .getByRole('textbox', { name: 'Frameworks' })
          .fill('SIPOC, OEE, root-cause analysis, Little’s Law');
        await card.getByRole('button', { name: 'Save', exact: true }).click();
        await expect(
          page.getByRole('status').filter({ hasText: 'Track saved.' }),
          'TC-746 ER-4: the edit is saved',
        ).toBeVisible();
        await expect(
          card.getByText('Saved, with a note:'),
          'TC-746 ER-4: the note is gone',
        ).toBeHidden();
      });

      await test.step('5. Press Remove and answer OK to the browser’s question', async () => {
        const asked: string[] = [];
        page.once('dialog', async (dialog) => {
          asked.push(dialog.message());
          await dialog.accept();
        });
        await card.getByRole('button', { name: 'Remove' }).click();
        await expect(
          page.getByRole('status').filter({ hasText: 'Track removed.' }),
          'TC-746 ER-5: the track is removed',
        ).toBeVisible();
        expect(asked, 'TC-746 ER-5: the browser asked first').toEqual([
          `Remove the ${label} track? Its questions stay in the bank.`,
        ]);
        await expect(
          page.getByRole('tab', { name: new RegExp(`^${label}`) }),
          'TC-746 ER-5: its tab is gone',
        ).toHaveCount(0);
      });
    } finally {
      if (created.id)
        await page.request.delete(`/api/admin/interview-questions/tracks/${created.id}`);
    }
  });

  test('Move a question down the interviewer’s order @TC-747', async ({ page, signIn }) => {
    await signIn('admin');
    const track = await createTrack(page, `E2E Order ${RUN}`);
    const first = 'Walk me through your background and why operations.';
    const second = 'Describe a time you cut waste from a process.';
    await addQuestions(page, track, [`[opening] ${first}`, `[probing] ${second}`]);
    const questions = page.getByRole('region', { name: /^Questions · \d+$/ });
    const texts = questions.getByRole('textbox', { name: 'Question text' });
    const openTrack = async () => {
      await page.getByRole('tab', { name: new RegExp(`^${track.label}`) }).click();
      await texts.first().waitFor();
    };

    try {
      await test.step('1. Open /admin/interview-questions and press the tab of the track in the test data', async () => {
        await page.goto('/admin/interview-questions');
        await openTrack();
        await expect(texts.nth(0), 'TC-747 ER-1: row 1 is the opening question').toHaveValue(first);
        await expect(texts.nth(1), 'TC-747 ER-1: row 2 is the probing question').toHaveValue(
          second,
        );
        await expect(
          questions.getByRole('button', { name: 'Move this question up' }).first(),
          'TC-747 ER-1: the first question cannot move up',
        ).toBeDisabled();
        await expect(
          questions.getByRole('button', { name: 'Move this question down' }).last(),
          'TC-747 ER-1: the last question cannot move down',
        ).toBeDisabled();
      });

      await test.step('2. Press "Move this question down" on row 1', async () => {
        await questions.getByRole('button', { name: 'Move this question down' }).first().click();
        await expect(texts.nth(0), 'TC-747 ER-3: the probing question moves to row 1').toHaveValue(
          second,
        );
        await expect(texts.nth(1), 'TC-747 ER-3: the opening question moves to row 2').toHaveValue(
          first,
        );
      });

      await test.step('3. Type "waste" in the question search', async () => {
        await questions
          .getByRole('searchbox', { name: 'Search the questions on this track' })
          .fill('waste');
        await expect(
          questions.getByRole('button', { name: 'Move this question down' }),
          'TC-747 ER-4: a filtered list cannot be reordered',
        ).toBeDisabled();
        await expect(
          questions.getByRole('button', { name: 'Move this question down' }),
          'TC-747 ER-4: and the button says why',
        ).toHaveAttribute(
          'title',
          'Clear the search to reorder — the order covers the whole track',
        );
      });

      await test.step('4. Reload the page and press the tab of the track again', async () => {
        await page.reload();
        await openTrack();
        await expect(texts.nth(0), 'TC-747 ER-5: the new order was stored — row 1').toHaveValue(
          second,
        );
        await expect(texts.nth(1), 'TC-747 ER-5: the new order was stored — row 2').toHaveValue(
          first,
        );
      });
    } finally {
      await removeTrack(page, track);
    }
  });

  // ------------------------------------------------------------ Refusals

  const SCREENS = [
    '/admin/setup',
    '/admin/institution',
    '/admin/catalogue',
    '/admin/interviews',
    '/admin/interview-questions',
  ] as const;
  const API_REFUSAL =
    "You do not hold the 'Institution hierarchy' capability. An administrator can grant it in Governance.";

  test('A faculty member cannot open the college setup and interview screens @TC-750', async ({
    page,
    signIn,
  }) => {
    await signIn('mentor');
    const home = page.getByRole('heading', { level: 1, name: 'Faculty notebook' });

    await test.step('1. Open /admin/colleges', async () => {
      await page.goto('/admin/colleges');
      await expect(page, 'TC-750 ER-1: the faculty member is sent to their own home').toHaveURL(
        /\/mentor\/notebook$/,
      );
      await expect(home, 'TC-750 ER-1: the Faculty notebook').toBeVisible();
      await expect(
        page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Colleges' }),
        'TC-750 ER-1: the sidebar offers no Colleges screen',
      ).toHaveCount(0);
    });

    await test.step('2. Open /admin/setup, /admin/institution, /admin/catalogue, /admin/interviews and /admin/interview-questions in turn', async () => {
      for (const screen of SCREENS) {
        await page.goto(screen);
        await expect(page, `TC-750 ER-2: ${screen} sends the faculty member home`).toHaveURL(
          /\/mentor\/notebook$/,
        );
        await expect(home, `TC-750 ER-2: ${screen} shows the Faculty notebook`).toBeVisible();
      }
    });

    await test.step('3. Open /api/admin/colleges in the same browser', async () => {
      const response = await page.goto('/api/admin/colleges');
      expect(response?.status(), 'TC-750 ER-3: the API refuses with 403').toBe(403);
      expect(
        await response?.json(),
        'TC-750 ER-3: and names the capability that is missing',
      ).toEqual({
        detail: API_REFUSAL,
      });
    });
  });

  test('A student cannot open the college setup and interview screens @TC-751', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const home = page.getByRole('heading', { level: 1, name: 'Welcome back, Test', exact: true });

    await test.step('1. Open /admin/colleges', async () => {
      await page.goto('/admin/colleges');
      await expect(page, 'TC-751 ER-1: the student is sent to their own home').toHaveURL(
        /\/student$/,
      );
      await expect(home, 'TC-751 ER-1: the student home').toBeVisible();
    });

    await test.step('2. Open /admin/setup, /admin/institution, /admin/catalogue, /admin/interviews and /admin/interview-questions in turn', async () => {
      for (const screen of SCREENS) {
        await page.goto(screen);
        await expect(page, `TC-751 ER-2: ${screen} sends the student home`).toHaveURL(/\/student$/);
        await expect(home, `TC-751 ER-2: ${screen} shows the student home`).toBeVisible();
      }
    });

    await test.step('3. Open /api/admin/colleges in the same browser', async () => {
      const response = await page.goto('/api/admin/colleges');
      expect(response?.status(), 'TC-751 ER-3: the API refuses with 403').toBe(403);
      expect(
        await response?.json(),
        'TC-751 ER-3: and names the capability that is missing',
      ).toEqual({
        detail: API_REFUSAL,
      });
    });
  });
});
