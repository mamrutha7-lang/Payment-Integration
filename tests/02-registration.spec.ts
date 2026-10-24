/**
 * Registration and onboarding: the automated twins of the cases in
 * test-management/cases/02-registration.md.
 *
 * THE TAG IS THE LINK, exactly as in auth-sync.spec.ts: each title ends with
 * its case's `@TC-NNN`, the top-level `test.step()` titles are that case's
 * steps word for word, and every assertion names the expected result it
 * checks. The CSV reporter fails the run when the two files drift apart.
 *
 * WHAT THIS SPEC ARRANGES, AND WHY THROUGH THE API. The screens under test
 * need data the dev seed does not have: a department with no courses, a
 * course with three specializations, applications in known states. That data
 * is prepared through the same console API the screens call, signed in as the
 * Main Admin in a SEPARATE request context, and never through the database:
 * a row written behind the API's back would test a state the product cannot
 * reach. Every test undoes what it made in `afterEach`, so a run leaves no
 * pending application and no extra student on the roster behind it.
 *
 * THE FORM'S RATE LIMIT SHAPES THE FIXTURES. POST /api/register accepts 20
 * submissions per network address per 10 minutes, and every browser behind
 * the dev proxy is one address. So the applications the review cases decide
 * are REUSED across runs under fixed addresses: a case that holds or rejects
 * one puts it back, and the next run reopens it rather than submitting a new
 * one. A full run submits 10 applications on a fresh database and 7 after
 * that, which keeps two runs in a row under the limit. A submission that
 * meets the limit marks the test Blocked, not Failed.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import type {
  APIRequestContext,
  APIResponse,
  Download,
  Locator,
  Page,
  PlaywrightWorkerArgs,
  TestInfo,
} from '@playwright/test';
import { ACCOUNTS, block, expect, test } from './support/reep';

type PlaywrightApi = PlaywrightWorkerArgs['playwright'];

/** Unique to this run, for names, addresses and USNs the run creates. */
const RUN = Date.now().toString(36);
const RUN_UPPER = RUN.toUpperCase();

const FIXTURES = path.join(__dirname, 'fixtures');
const SAMPLE_PDF = path.join(FIXTURES, 'sample.pdf');
const SAMPLE_PNG = path.join(FIXTURES, 'sample.png');
const SAMPLE_JPG = path.join(FIXTURES, 'sample.jpg');

// --------------------------------------------------------------- the app's words
//
// Quoted from source so a copy change fails a named expected result instead of
// drifting away from the manual file unnoticed.

/** `reg-sub` in apps/web/src/app/features/register/registration.component.html. */
const FORM_INTRO =
  'Every box marked * is required — everything except Specialization — and so are your CV and your photo.';
/** `missing()` and `submit()` in registration.component.ts. */
const EVERYTHING_MISSING =
  'Please add your CV (PDF), your full name, your USN, your college email, your personal email, ' +
  'your phone number, your LinkedIn profile, your college, your department and your photo (PNG or JPG).';
const EVERYTHING_BUT_NAME_MISSING =
  'Please add your CV (PDF), your USN, your college email, your personal email, your phone number, ' +
  'your LinkedIn profile, your college, your department and your photo (PNG or JPG).';
const SPEC_NOTE = 'Tick one, or 2 if you opted for a dual specialization.';
/** The review rule's verdict, `_apply_rule` in apps/api-py/app/routers/registration.py. */
const HELD_CARD =
  "Held for review. Routed by rule 'College domain — route to review' — awaiting review.";
/** `submit()`'s 409 in registration.py. */
const DUPLICATE_REFUSAL =
  'This application could not be accepted. If you have already applied, or you think this is a ' +
  'mistake, contact the placement office.';
/** `_REFUSED` in apps/api-py/app/routers/onboarding.py. */
const SETUP_LINK_REFUSED =
  'That setup link is not valid, or the email address does not match it. Check the link in the ' +
  'email we sent you, or ask for a new one with "Forgot password?" on the sign-in screen.';
/** The constructor of apps/web/src/app/features/login/onboard/onboard.component.ts. */
const SETUP_LINK_MISSING =
  'This page needs the setup link from the email we sent you. Open that link again, or ask for a ' +
  'new one with "Forgot password?" on the sign-in screen.';

// ------------------------------------------------------------- the seeded spine

const SEEDED = {
  college: 'BGS College of Engineering and Technology',
  department: 'Department of Management Studies',
  course: 'MBA · Master of Business Administration',
  specialization: 'Finance',
  /** The Batch box on the public form is the YEAR and nothing else (2026-09-23). */
  batchOption: '2024-26',
  /** What the console's screens print for the same batch, spine included. */
  batchLabel: 'Master of Business Administration - Finance · 2024-26 Section B',
} as const;

// ----------------------------------------------- the test college (see the cases)

const E2E_COLLEGE = { code: 'E2E02', name: 'E2E Registration College' } as const;
const OPEN_DEPARTMENT = { code: 'OPEN', name: 'E2E Open Department' } as const;
const DUAL_DEPARTMENT = { code: 'DUAL', name: 'E2E Dual Department' } as const;
const DUAL_COURSE = {
  code: 'E2EDP',
  name: 'E2E Dual Programme',
  option: 'E2EDP · E2E Dual Programme',
} as const;
const DUAL_SPECIALIZATIONS = [
  { code: 'ANA', name: 'E2E Analytics' },
  { code: 'FIN', name: 'E2E Finance' },
  { code: 'MKT', name: 'E2E Marketing' },
] as const;

// ----------------------------------------------------------- the applicants

interface Applicant {
  name: string;
  email: string;
  usn: string;
  phone: string;
  personalEmail: string;
  linkedin: string;
}

/** Where an application says it belongs, as the API takes it. */
interface Claim {
  collegeId?: string;
  departmentId?: string;
  courseId?: string;
  specializationIds?: string[];
  cohortId?: string;
}

/** The pending application the review cases decide, reused across runs. */
const FIXTURE: Applicant = {
  name: 'E2E Fixture Applicant',
  email: 'e2e-m02-fixture@bgscet.ac.in',
  usn: 'E2EM02FIX',
  phone: '+91 90000 00002',
  personalEmail: 'e2e-m02-fixture@example.org',
  linkedin: 'linkedin.com/in/e2e-m02-fixture',
};
const REAPPLICANT: Applicant = {
  name: 'E2E Reapplicant',
  email: 'e2e-m02-reapply@bgscet.ac.in',
  usn: 'E2EM02RE2',
  phone: '+91 90000 00109',
  personalEmail: 'e2e-m02-reapply@example.org',
  linkedin: 'linkedin.com/in/e2e-m02-reapply',
};
const REAPPLY_REASON = 'E2E: the USN on the form was mistyped.';
const OFF_DOMAIN: Applicant = {
  name: 'E2E Off Domain',
  email: 'e2e-m02-offdomain@example.org',
  usn: 'E2EM02OFF',
  phone: '+91 90000 00116',
  personalEmail: 'e2e-m02-offdomain@example.com',
  linkedin: 'linkedin.com/in/e2e-m02-offdomain',
};
const CLEANUP_REASON = 'E2E cleanup: test application from tests/02-registration.spec.ts';

/** A new applicant whose every identifier is unique to this run. */
function runApplicant(key: string, name: string, phone: string): Applicant {
  return {
    name: `${name} ${RUN}`,
    email: `e2e-${RUN}-${key}@bgscet.ac.in`,
    usn: `E2E${RUN_UPPER}${key.slice(0, 1).toUpperCase()}`,
    phone,
    personalEmail: `e2e-${RUN}-${key}@example.org`,
    linkedin: `linkedin.com/in/e2e-${RUN}-${key}`,
  };
}

// ======================================================= the admin's API ====

/** `RegistrationOut` in apps/api-py/app/routers/registration.py, as far as used. */
interface Application {
  id: string;
  name: string;
  email: string;
  status: string;
  approved_student_id: string | null;
}

type QueueStatus = 'PENDING_REVIEW' | 'HOLD' | 'AUTO_APPROVED' | 'APPROVED' | 'REJECTED';

async function jsonOf<T>(response: APIResponse, what: string): Promise<T> {
  if (!response.ok()) {
    throw new Error(
      `${what} answered ${response.status()}: ${(await response.text()).slice(0, 300)}`,
    );
  }
  return (await response.json()) as T;
}

/**
 * A request context signed in as the Main Admin, apart from the page's own
 * cookie jar. REEP keeps one live session per account, so this signs out any
 * page signed in as the admin: tests arrange data BEFORE `signIn('admin')`
 * and clean up after the page is done.
 */
async function adminApi(
  playwright: PlaywrightApi,
  baseURL: string | undefined,
  testInfo: TestInfo,
): Promise<APIRequestContext> {
  const api = await playwright.request.newContext({ baseURL });
  const response = await api.post('/api/auth/login', {
    data: { email: ACCOUNTS.admin.email, password: ACCOUNTS.admin.password },
  });
  if (!response.ok()) {
    await api.dispose();
    block(
      testInfo,
      `the Main Admin (${ACCOUNTS.admin.email}) did not sign in (${response.status()}), so the ` +
        'test data could not be prepared. Run "python -m app.seed" in apps/api-py.',
    );
  }
  return api;
}

/** Runs `work` with an admin request context, then disposes of it. */
async function withAdmin<T>(
  playwright: PlaywrightApi,
  baseURL: string | undefined,
  testInfo: TestInfo,
  work: (api: APIRequestContext) => Promise<T>,
): Promise<T> {
  const api = await adminApi(playwright, baseURL, testInfo);
  try {
    return await work(api);
  } finally {
    await api.dispose();
  }
}

interface Row {
  id: string;
  code: string;
  status: string;
}

interface Structure {
  collegeId: string;
  openDepartmentId: string;
  dualDepartmentId: string;
  courseId: string;
  /** By specialization name. */
  specializationIds: Record<string, string>;
}

/** Set once this worker has made the test college active; `afterAll` archives it. */
let structureActivated = false;

/** The row with `code` at `listUrl`, created or made active as needed. Returns its id. */
async function ensureRow(
  api: APIRequestContext,
  listUrl: string,
  createUrl: string,
  patchUrl: (id: string) => string,
  code: string,
  body: Record<string, unknown>,
): Promise<string> {
  const rows = await jsonOf<Row[]>(await api.get(listUrl), `GET ${listUrl}`);
  const found = rows.find((row) => row.code === code);
  if (found === undefined) {
    const created = await jsonOf<Row>(
      await api.post(createUrl, { data: { code, ...body } }),
      `POST ${createUrl}`,
    );
    return created.id;
  }
  if (found.status !== 'ACTIVE') {
    await jsonOf(
      await api.patch(patchUrl(found.id), { data: { status: 'ACTIVE' } }),
      `PATCH ${patchUrl(found.id)}`,
    );
  }
  return found.id;
}

/** The test college, its two departments, the course and its three specializations. */
async function ensureStructure(api: APIRequestContext): Promise<Structure> {
  const collegeId = await ensureRow(
    api,
    '/api/admin/colleges',
    '/api/admin/colleges',
    (id) => `/api/admin/colleges/${id}`,
    E2E_COLLEGE.code,
    { name: E2E_COLLEGE.name },
  );
  structureActivated = true;
  const department = (code: string, name: string) =>
    ensureRow(
      api,
      `/api/admin/colleges/${collegeId}/departments`,
      `/api/admin/colleges/${collegeId}/departments`,
      (id) => `/api/admin/departments/${id}`,
      code,
      { name },
    );
  const openDepartmentId = await department(OPEN_DEPARTMENT.code, OPEN_DEPARTMENT.name);
  const dualDepartmentId = await department(DUAL_DEPARTMENT.code, DUAL_DEPARTMENT.name);
  const courseId = await ensureRow(
    api,
    `/api/admin/departments/${dualDepartmentId}/academic-courses`,
    `/api/admin/departments/${dualDepartmentId}/academic-courses`,
    (id) => `/api/admin/academic-courses/${id}`,
    DUAL_COURSE.code,
    { name: DUAL_COURSE.name, duration_months: 24 },
  );
  const specializationIds: Record<string, string> = {};
  for (const specialization of DUAL_SPECIALIZATIONS) {
    specializationIds[specialization.name] = await ensureRow(
      api,
      `/api/admin/academic-courses/${courseId}/academic-specializations`,
      `/api/admin/academic-courses/${courseId}/academic-specializations`,
      (id) => `/api/admin/academic-specializations/${id}`,
      specialization.code,
      { name: specialization.name },
    );
  }
  return { collegeId, openDepartmentId, dualDepartmentId, courseId, specializationIds };
}

/** The fixture application's claim: the dual course, two specializations, no batch. */
function dualClaim(structure: Structure): Claim {
  return {
    collegeId: structure.collegeId,
    departmentId: structure.dualDepartmentId,
    courseId: structure.courseId,
    specializationIds: [
      structure.specializationIds['E2E Analytics'],
      structure.specializationIds['E2E Finance'],
    ],
  };
}

/** The seeded college, department, course and batch, read off the public hierarchy. */
async function seededClaim(api: APIRequestContext, testInfo: TestInfo): Promise<Claim> {
  interface Hierarchy {
    colleges: Array<{
      id: string;
      code: string;
      departments: Array<{
        id: string;
        code: string;
        courses: Array<{ id: string; code: string }>;
        batches: Array<{ id: string; code: string }>;
      }>;
    }>;
  }
  const hierarchy = await jsonOf<Hierarchy>(
    await api.get('/api/register/hierarchy'),
    'GET /api/register/hierarchy',
  );
  const college = hierarchy.colleges.find((c) => c.code === 'BGSCET');
  const department = college?.departments.find((d) => d.code === 'MGMT');
  const course = department?.courses.find((c) => c.code === 'MBA');
  const batch = department?.batches.find((b) => b.code === 'MBA-2026-B');
  if (!college || !department || !course || !batch) {
    block(
      testInfo,
      'the seeded college BGSCET, department MGMT, course MBA or batch MBA-2026-B is missing ' +
        'from GET /api/register/hierarchy. Run "python -m app.seed" in apps/api-py.',
    );
  }
  return {
    collegeId: college.id,
    departmentId: department.id,
    courseId: course.id,
    cohortId: batch.id,
  };
}

/**
 * Submits an application through POST /api/register, as the form does: one
 * multipart request with both files. It counts toward the form's rate limit.
 */
async function submitApplication(
  api: APIRequestContext,
  applicant: Applicant,
  claim: Claim,
  testInfo: TestInfo,
): Promise<Application> {
  const form = new FormData();
  const fields: Record<string, string | undefined> = {
    name: applicant.name,
    email: applicant.email,
    usn: applicant.usn,
    phone: applicant.phone,
    personal_email: applicant.personalEmail,
    linkedin_url: applicant.linkedin,
    degree_level: 'PG',
    college_id: claim.collegeId,
    department_id: claim.departmentId,
    course_id: claim.courseId,
    requested_cohort_id: claim.cohortId,
  };
  for (const [key, value] of Object.entries(fields)) if (value) form.append(key, value);
  for (const id of claim.specializationIds ?? []) form.append('specialization_ids', id);
  form.append(
    'cv',
    new Blob([fs.readFileSync(SAMPLE_PDF)], { type: 'application/pdf' }),
    'sample.pdf',
  );
  form.append(
    'photo',
    new Blob([fs.readFileSync(SAMPLE_PNG)], { type: 'image/png' }),
    'sample.png',
  );
  const response = await api.post('/api/register', { multipart: form });
  if (response.status() === 429) blockOnRateLimit(testInfo);
  return jsonOf<Application>(response, 'POST /api/register');
}

function blockOnRateLimit(testInfo: TestInfo): never {
  block(
    testInfo,
    'POST /api/register refused a submission with 429: 20 applications have been submitted from ' +
      'this address in the last 10 minutes. Wait, or restart the API, which clears the count.',
  );
}

async function applicationsFor(
  api: APIRequestContext,
  email: string,
  status: QueueStatus,
): Promise<Application[]> {
  const url =
    status === 'PENDING_REVIEW'
      ? '/api/register/pending'
      : `/api/register/pending?status=${status}&limit=500`;
  const rows = await jsonOf<Application[]>(await api.get(url), `GET ${url}`);
  return rows.filter((row) => row.email === email.toLowerCase());
}

async function reopen(api: APIRequestContext, application: Application): Promise<Application> {
  return jsonOf<Application>(
    await api.post(`/api/register/${application.id}/reopen`),
    `POST /api/register/${application.id}/reopen`,
  );
}

async function reject(api: APIRequestContext, application: Application, reason: string) {
  await jsonOf(
    await api.post(`/api/register/${application.id}/decision`, {
      data: { decision: 'REJECT', note: reason },
    }),
    `POST /api/register/${application.id}/decision`,
  );
}

/**
 * A pending application for `applicant`: the one already pending, else the
 * held or the latest rejected one put back in the queue, else a new one.
 */
async function ensurePending(
  api: APIRequestContext,
  applicant: Applicant,
  claim: Claim,
  testInfo: TestInfo,
): Promise<Application> {
  const [pending] = await applicationsFor(api, applicant.email, 'PENDING_REVIEW');
  if (pending) return pending;
  const [held] = await applicationsFor(api, applicant.email, 'HOLD');
  if (held) return reopen(api, held);
  const [rejected] = await applicationsFor(api, applicant.email, 'REJECTED');
  if (rejected) return reopen(api, rejected);
  return submitApplication(api, applicant, claim, testInfo);
}

/** Rejects every live (pending or held) application for the address. */
async function rejectLive(api: APIRequestContext, email: string, reason: string): Promise<void> {
  for (const status of ['PENDING_REVIEW', 'HOLD'] as const) {
    for (const application of await applicationsFor(api, email, status)) {
      await reject(api, application, reason);
    }
  }
}

/** Takes the student an approval created off the roster (the console's Remove,
 *  which keeps the records). */
async function removeStudentOf(
  api: APIRequestContext,
  email: string,
  status: 'APPROVED' | 'AUTO_APPROVED',
): Promise<void> {
  for (const application of await applicationsFor(api, email, status)) {
    if (application.approved_student_id === null) continue;
    const response = await api.post(
      `/api/admin/students/${application.approved_student_id}/remove`,
      { data: { reason: CLEANUP_REASON } },
    );
    // 409: removed already, by an earlier cleanup.
    if (!response.ok() && response.status() !== 409) {
      throw new Error(`removing the student for ${email} answered ${response.status()}`);
    }
  }
}

/** A USN the MBA auto-admit rule matches and no student, current or removed, holds. */
async function freeMbaUsn(api: APIRequestContext): Promise<string> {
  for (let attempt = 0; attempt < 40; attempt++) {
    const year = 5 + Math.floor(Math.random() * 5);
    const serial = String(Math.floor(Math.random() * 1000)).padStart(3, '0');
    const usn = `1BG2${year}MBA${serial}`;
    const current = await jsonOf<unknown[]>(
      await api.get(`/api/admin/students?q=${usn}`),
      'GET /api/admin/students',
    );
    const removed = await jsonOf<unknown[]>(
      await api.get(`/api/admin/students?q=${usn}&removed=true`),
      'GET /api/admin/students?removed=true',
    );
    if (current.length === 0 && removed.length === 0) return usn;
  }
  throw new Error('Could not find a free 1BG2nMBAnnn USN in 40 tries.');
}

// ------------------------------------------------------------- cleanups ----

type Cleanup = { what: string; run: (api: APIRequestContext) => Promise<void> };
let cleanups: Cleanup[] = [];

/** Registered BEFORE the step that creates the data, so a failure still undoes it. */
function undoAfterTest(what: string, run: Cleanup['run']): void {
  cleanups.push({ what, run });
}

test.afterEach(async ({ playwright, baseURL }, testInfo) => {
  const due = cleanups.reverse();
  cleanups = [];
  if (due.length === 0) return;
  const problems: string[] = [];
  await withAdmin(playwright, baseURL, testInfo, async (api) => {
    for (const cleanup of due) {
      try {
        await cleanup.run(api);
      } catch (error) {
        problems.push(`${cleanup.what}: ${(error as Error).message}`);
      }
    }
  });
  if (problems.length > 0) {
    throw new Error(
      `Cleanup failed, so a rerun may not start from the same data: ${problems.join('; ')}`,
    );
  }
});

/** The public form must not offer the test college between runs. */
test.afterAll(async ({ playwright }, testInfo) => {
  if (!structureActivated) return;
  structureActivated = false;
  await withAdmin(playwright, testInfo.project.use.baseURL, testInfo, async (api) => {
    const colleges = await jsonOf<Row[]>(
      await api.get('/api/admin/colleges'),
      'GET /api/admin/colleges',
    );
    const college = colleges.find((row) => row.code === E2E_COLLEGE.code);
    if (college && college.status === 'ACTIVE') {
      await jsonOf(
        await api.patch(`/api/admin/colleges/${college.id}`, { data: { status: 'ARCHIVED' } }),
        'PATCH /api/admin/colleges (archive)',
      );
    }
  });
});

// ======================================================== the register form ====

const form = {
  heading: (page: Page) =>
    page.getByRole('heading', { level: 2, name: 'Student registration', exact: true }),
  fullName: (page: Page) => page.getByRole('textbox', { name: 'Full name *', exact: true }),
  usn: (page: Page) => page.getByRole('textbox', { name: 'USN *', exact: true }),
  collegeEmail: (page: Page) => page.getByRole('textbox', { name: 'College email *', exact: true }),
  personalEmail: (page: Page) =>
    page.getByRole('textbox', { name: 'Personal email *', exact: true }),
  phone: (page: Page) => page.getByRole('textbox', { name: 'Phone *', exact: true }),
  linkedin: (page: Page) => page.getByRole('textbox', { name: 'LinkedIn profile *', exact: true }),
  college: (page: Page) => page.getByRole('combobox', { name: 'College *', exact: true }),
  department: (page: Page) => page.getByRole('combobox', { name: 'Department *', exact: true }),
  /** Named "Course" or "Course *", depending on whether any are listed. */
  course: (page: Page) => page.getByRole('combobox', { name: /^Course( \*)?$/ }),
  batch: (page: Page) => page.getByRole('combobox', { name: /^Batch( \*)?$/ }),
  degree: (page: Page) => page.getByRole('combobox', { name: 'Degree level *', exact: true }),
  specializations: (page: Page) => page.getByRole('group', { name: 'Specialization', exact: true }),
  tick: (page: Page, name: string) =>
    page.getByRole('group', { name: 'Specialization', exact: true }).getByRole('checkbox', {
      name,
      exact: true,
    }),
  /** The file inputs are named by their dropzone, whose limits line never changes. */
  cvInput: (page: Page) => page.getByRole('button', { name: /PDF only · up to 10 MB/ }),
  photoInput: (page: Page) => page.getByRole('button', { name: /PNG or JPG · up to 10 MB/ }),
  cvDropzone: (page: Page) =>
    page
      .locator('label')
      .filter({ has: page.getByRole('button', { name: /PDF only · up to 10 MB/ }) }),
  photoDropzone: (page: Page) =>
    page
      .locator('label')
      .filter({ has: page.getByRole('button', { name: /PNG or JPG · up to 10 MB/ }) }),
  submit: (page: Page) => page.getByRole('button', { name: 'Submit registration' }),
  /** The form-level message, which is the one alert holding "Please add". */
  problem: (page: Page, text: string) => page.getByRole('alert').getByText(text, { exact: true }),
};

async function fillTyped(page: Page, applicant: Applicant): Promise<void> {
  await form.fullName(page).fill(applicant.name);
  await form.usn(page).fill(applicant.usn);
  await form.collegeEmail(page).fill(applicant.email);
  await form.personalEmail(page).fill(applicant.personalEmail);
  await form.phone(page).fill(applicant.phone);
  await form.linkedin(page).fill(applicant.linkedin);
}

async function chooseSeededCollegeAndDepartment(page: Page): Promise<void> {
  await form.college(page).selectOption({ label: SEEDED.college });
  await form.department(page).selectOption({ label: SEEDED.department });
}

async function chooseSeededPlace(page: Page, { withBatch }: { withBatch: boolean }) {
  await chooseSeededCollegeAndDepartment(page);
  await form.course(page).selectOption({ label: SEEDED.course });
  if (withBatch) await form.batch(page).selectOption({ label: SEEDED.batchOption });
}

async function chooseDualPlace(page: Page): Promise<void> {
  await form.college(page).selectOption({ label: E2E_COLLEGE.name });
  await form.department(page).selectOption({ label: DUAL_DEPARTMENT.name });
  await form.course(page).selectOption({ label: DUAL_COURSE.option });
}

/** Counts the POSTs to /api/register the page makes, for "nothing is sent". */
function countSubmissions(page: Page): () => number {
  let count = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/register') count++;
  });
  return () => count;
}

/** Clicks Submit registration and waits for the API's answer. */
async function submitForm(page: Page, testInfo: TestInfo): Promise<number> {
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/register',
    ),
    form.submit(page).click(),
  ]);
  if (response.status() === 429) blockOnRateLimit(testInfo);
  return response.status();
}

// ====================================================== the review queue ====

const queue = {
  heading: (page: Page) =>
    page.getByRole('heading', { level: 1, name: 'New applications', exact: true }),
  tab: (page: Page, name: string | RegExp) =>
    page.getByRole('group', { name: 'Registration queues' }).getByRole('button', {
      name,
      exact: typeof name === 'string' ? true : undefined,
    }),
  search: (page: Page) =>
    page.getByRole('searchbox', { name: 'Search applications by name, email or USN' }),
  /** The Applicant cell, which draws the name and the address. */
  applicant: (page: Page, email: string) => page.getByRole('gridcell').filter({ hasText: email }),
  cell: (page: Page, text: string) => page.getByRole('gridcell', { name: text, exact: true }),
  rows: (page: Page) => page.getByText(/^Rows: \d+$/),
  domainFilter: (page: Page) => page.getByRole('combobox', { name: 'Domain check', exact: true }),
  panel: (page: Page) => page.getByRole('complementary', { name: 'Decision' }),
  note: (page: Page) =>
    page
      .getByRole('complementary', { name: 'Decision' })
      .getByRole('textbox', { name: 'Decision note' }),
  panelButton: (page: Page, name: string) =>
    page
      .getByRole('complementary', { name: 'Decision' })
      .getByRole('button', { name, exact: true }),
  notice: (page: Page, text: string) => page.getByRole('status').getByText(text, { exact: true }),
  alert: (page: Page, text: string) => page.getByRole('alert').filter({ hasText: text }),
};

/** One fact of the panel's list, by its term ("Batch", "USN" …). */
function fact(page: Page, term: string): Locator {
  return queue
    .panel(page)
    .locator('dl > div')
    .filter({ has: page.getByRole('term').getByText(term, { exact: true }) })
    .getByRole('definition');
}

/**
 * The line under a hold note. The template breaks it across two lines and
 * Angular keeps the break, so the pattern allows any run of whitespace there.
 */
function holdLine(page: Page): Locator {
  return queue
    .panel(page)
    .getByText("Nothing was emailed and the applicant's own page is unchanged.");
}
const HOLD_LINE =
  /^\s*Held \d{1,2} [A-Z][a-z]{2,3} \d{4}, \d{2}:\d{2} · internal to the\s+office\. Nothing was emailed and the applicant's own page is unchanged\.\s*$/;

/** `text` as a whole-string pattern that tolerates the whitespace a template
 *  leaves around an interpolation. */
function exactly(text: string): RegExp {
  return new RegExp(`^\\s*${text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*$`);
}

/** The first five cells of a rule's row: Priority, Rule, Matches, Seats in, Decision. */
async function expectRuleRow(
  row: Locator,
  values: Array<string | null>,
  message: string,
): Promise<void> {
  for (const [index, value] of values.entries()) {
    // null: a cell this expected result says nothing about.
    if (value !== null) {
      await expect(row.getByRole('cell').nth(index), message).toHaveText(exactly(value));
    }
  }
}

/** One line of the panel's checklist, by its headline. */
function check(page: Page, headline: string): Locator {
  return queue
    .panel(page)
    .locator('.panel-check')
    .filter({ has: page.getByText(headline, { exact: true }) });
}

async function openQueueAsAdmin(page: Page, signIn: (a: 'admin') => Promise<unknown>) {
  await signIn('admin');
  await page.goto('/admin/registrations');
  await expect(queue.heading(page)).toBeVisible();
}

async function searchAndOpen(page: Page, email: string): Promise<void> {
  await queue.search(page).fill(email);
  await queue.applicant(page, email).click();
}

async function downloadBytes(download: Download): Promise<Buffer> {
  const saved = await download.path();
  return fs.readFileSync(saved);
}

// =============================================================== the cases ====

test.describe('Registration and onboarding', () => {
  test('The registration form lists the colleges, departments, courses and batches the office has set up @TC-100', async ({
    page,
  }) => {
    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(
        form.heading(page),
        'TC-100 ER-1: the page is headed "Student registration"',
      ).toBeVisible();
      await expect(
        page.getByText(FORM_INTRO, { exact: true }),
        'TC-100 ER-1: the intro line',
      ).toBeVisible();
      for (const field of [
        form.fullName,
        form.usn,
        form.collegeEmail,
        form.personalEmail,
        form.phone,
        form.linkedin,
      ]) {
        await expect(field(page), 'TC-100 ER-1: every typed field is marked *').toBeVisible();
      }
      await expect(form.college(page), 'TC-100 ER-1: College is marked *').toBeVisible();
      await expect(
        form.department(page),
        'TC-100 ER-1: Department is marked * and disabled',
      ).toBeDisabled();
      await expect(
        form.department(page),
        'TC-100 ER-1: Department reads "Choose a college first"',
      ).toHaveText('Choose a college first');
      await expect(
        form.course(page),
        'TC-100 ER-1: Course reads "Choose a department first"',
      ).toHaveText('Choose a department first');
      await expect(
        form.specializations(page).getByText('Choose a course first', { exact: true }),
        'TC-100 ER-1: Specialization says "Choose a course first"',
      ).toBeVisible();
      await expect(
        form.degree(page),
        'TC-100 ER-1: Degree level shows Post Graduate (PG)',
      ).toHaveValue('PG');
    });

    await test.step('2. In the College list, choose "BGS College of Engineering and Technology"', async () => {
      await form.college(page).selectOption({ label: SEEDED.college });
      await expect(
        form.department(page),
        'TC-100 ER-2: the Department list is enabled',
      ).toBeEnabled();
      await expect(
        form.department(page).getByRole('option', { name: SEEDED.department, exact: true }),
        'TC-100 ER-2: it offers the seeded department',
      ).toHaveCount(1);
    });

    await test.step('3. In the Department list, choose "Department of Management Studies"', async () => {
      await form.department(page).selectOption({ label: SEEDED.department });
      await expect(
        page.getByRole('combobox', { name: 'Course *', exact: true }),
        'TC-100 ER-3: Course is enabled and marked *',
      ).toBeEnabled();
      await expect(
        form.course(page).getByRole('option', { name: SEEDED.course, exact: true }),
        'TC-100 ER-3: Course offers the MBA',
      ).toHaveCount(1);
      await expect(
        page.getByRole('combobox', { name: 'Batch *', exact: true }),
        'TC-100 ER-3: Batch is marked *',
      ).toBeEnabled();
      await expect(
        form.batch(page).getByRole('option', { name: SEEDED.batchOption, exact: true }),
        'TC-100 ER-3: Batch offers the seeded batch by its year alone',
      ).toHaveCount(1);
    });

    await test.step('4. In the Course list, choose "MBA · Master of Business Administration"', async () => {
      await form.course(page).selectOption({ label: SEEDED.course });
      await expect(
        form.specializations(page).getByText(SPEC_NOTE, { exact: true }),
        'TC-100 ER-4: the checklist note',
      ).toBeVisible();
      await expect(
        form.tick(page, SEEDED.specialization),
        'TC-100 ER-4: a "Finance" box, not ticked',
      ).not.toBeChecked();
      await expect(
        form.tick(page, SEEDED.specialization),
        'TC-100 ER-4: the "Finance" box is enabled',
      ).toBeEnabled();
    });
  });

  test('An empty form is refused with a list of everything missing @TC-101', async ({ page }) => {
    const submissions = countSubmissions(page);

    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(form.heading(page)).toBeVisible();
    });

    await test.step('2. Click Submit registration without filling in anything', async () => {
      await form.submit(page).click();
      await expect(
        form.problem(page, EVERYTHING_MISSING),
        'TC-101 ER-1: the message names every missing box',
      ).toBeVisible();
      expect(submissions(), 'TC-101 ER-2: nothing is sent to the server').toBe(0);
      await expect(form.submit(page), 'TC-101 ER-2: the form stays on the page').toBeVisible();
    });

    await test.step('3. Type the full name in the Full name box', async () => {
      await form.fullName(page).fill('E2E Empty Form');
    });

    await test.step('4. Click Submit registration again', async () => {
      await form.submit(page).click();
      await expect(
        form.problem(page, EVERYTHING_BUT_NAME_MISSING),
        'TC-101 ER-3: the message no longer asks for the name',
      ).toBeVisible();
      expect(submissions(), 'TC-101 ER-3: nothing is sent to the server').toBe(0);
    });
  });

  test('Course and Batch are required only where the office has listed some @TC-102', async ({
    page,
    playwright,
    baseURL,
  }, testInfo) => {
    await withAdmin(playwright, baseURL, testInfo, ensureStructure);
    const submissions = countSubmissions(page);
    const applicant: Applicant = {
      name: 'E2E Course Optional',
      email: 'e2e-tc102@bgscet.ac.in',
      usn: 'E2E102',
      phone: '+91 90000 00102',
      personalEmail: 'e2e-tc102@example.org',
      linkedin: 'linkedin.com/in/e2e-tc102',
    };

    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(form.heading(page)).toBeVisible();
    });

    await test.step('2. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data', async () => {
      await fillTyped(page, applicant);
    });

    await test.step('3. Attach sample.pdf as the CV', async () => {
      await form.cvInput(page).setInputFiles(SAMPLE_PDF);
    });

    await test.step('4. Choose the college "BGS College of Engineering and Technology" and the department "Department of Management Studies"', async () => {
      await chooseSeededCollegeAndDepartment(page);
      await expect(
        page.getByRole('combobox', { name: 'Course *', exact: true }),
        'TC-102 ER-1: Course is marked *',
      ).toBeVisible();
      await expect(
        page.getByRole('combobox', { name: 'Batch *', exact: true }),
        'TC-102 ER-1: Batch is marked *',
      ).toBeVisible();
    });

    await test.step('5. Click Submit registration', async () => {
      await form.submit(page).click();
      await expect(
        form.problem(page, 'Please add your course, your batch and your photo (PNG or JPG).'),
        'TC-102 ER-2: a department with a course and a batch asks for both',
      ).toBeVisible();
    });

    await test.step('6. Choose the college "E2E Registration College" and the department "E2E Open Department"', async () => {
      await form.college(page).selectOption({ label: E2E_COLLEGE.name });
      await form.department(page).selectOption({ label: OPEN_DEPARTMENT.name });
      const course = page.getByRole('combobox', { name: 'Course', exact: true });
      await expect(course, 'TC-102 ER-3: Course has no * and is disabled').toBeDisabled();
      await expect(course, 'TC-102 ER-3: Course reads "No courses listed"').toHaveText(
        'No courses listed',
      );
      const batch = page.getByRole('combobox', { name: 'Batch', exact: true });
      await expect(batch, 'TC-102 ER-3: Batch has no * and is disabled').toBeDisabled();
      await expect(batch, 'TC-102 ER-3: Batch reads "No batches listed"').toHaveText(
        'No batches listed',
      );
      await expect(
        form.specializations(page).getByText('Choose a course first', { exact: true }),
        'TC-102 ER-3: Specialization says "Choose a course first"',
      ).toBeVisible();
    });

    await test.step('7. Click Submit registration', async () => {
      await form.submit(page).click();
      await expect(
        form.problem(page, 'Please add your photo (PNG or JPG).'),
        'TC-102 ER-4: only the photo is asked for, not a course or a batch',
      ).toBeVisible();
      expect(submissions(), 'TC-102 ER-4: nothing is sent to the server').toBe(0);
    });
  });

  test('The specialization checklist takes one tick, or two, and no more @TC-103', async ({
    page,
    playwright,
    baseURL,
  }, testInfo) => {
    await withAdmin(playwright, baseURL, testInfo, ensureStructure);
    const [analytics, finance, marketing] = DUAL_SPECIALIZATIONS.map((s) =>
      form.tick(page, s.name),
    );

    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(form.heading(page)).toBeVisible();
    });

    await test.step('2. Choose the college "E2E Registration College", the department "E2E Dual Department" and the course "E2EDP · E2E Dual Programme"', async () => {
      await chooseDualPlace(page);
      await expect(
        form.specializations(page).getByText(SPEC_NOTE, { exact: true }),
        'TC-103 ER-1: the checklist note',
      ).toBeVisible();
      for (const box of [analytics, finance, marketing]) {
        await expect(box, 'TC-103 ER-1: each box is shown, not ticked').not.toBeChecked();
        await expect(box, 'TC-103 ER-1: each box is enabled').toBeEnabled();
      }
      await expect(form.batch(page), 'TC-103 ER-1: Batch reads "No batches listed"').toHaveText(
        'No batches listed',
      );
    });

    await test.step('3. Tick "E2E Analytics"', async () => {
      await analytics.check();
      await expect(analytics, 'TC-103 ER-2: E2E Analytics is ticked').toBeChecked();
      await expect(finance, 'TC-103 ER-2: E2E Finance is still enabled').toBeEnabled();
      await expect(marketing, 'TC-103 ER-2: E2E Marketing is still enabled').toBeEnabled();
    });

    await test.step('4. Tick "E2E Finance"', async () => {
      await finance.check();
      await expect(finance, 'TC-103 ER-3: E2E Finance is ticked').toBeChecked();
      await expect(marketing, 'TC-103 ER-3: E2E Marketing cannot be ticked').toBeDisabled();
      await expect(analytics, 'TC-103 ER-3: E2E Analytics can still be unticked').toBeEnabled();
      await expect(finance, 'TC-103 ER-3: E2E Finance can still be unticked').toBeEnabled();
    });

    await test.step('5. Untick "E2E Finance"', async () => {
      await finance.uncheck();
      await expect(finance, 'TC-103 ER-4: E2E Finance is unticked').not.toBeChecked();
      await expect(marketing, 'TC-103 ER-4: E2E Marketing is enabled again').toBeEnabled();
      await expect(analytics, 'TC-103 ER-4: E2E Analytics is still ticked').toBeChecked();
    });
  });

  test('The CV and photo pickers state their limits and refuse the wrong file @TC-104', async ({
    page,
  }) => {
    const elevenMegabytes = 11 * 1024 * 1024;

    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(form.cvDropzone(page), 'TC-104 ER-1: the CV box asks for the CV').toContainText(
        'Attach your CV *',
      );
      await expect(
        form.cvDropzone(page),
        'TC-104 ER-1: the CV box states its limits',
      ).toContainText('PDF only · up to 10 MB');
      await expect(
        form.cvDropzone(page),
        'TC-104 ER-1: the CV box has a "Choose file" button',
      ).toContainText('Choose file');
      await expect(
        form.photoDropzone(page),
        'TC-104 ER-1: the photo box asks for a headshot',
      ).toContainText('Upload a headshot');
      await expect(
        form.photoDropzone(page),
        'TC-104 ER-1: the photo box states its limits',
      ).toContainText('PNG or JPG · up to 10 MB');
    });

    await test.step('2. Attach sample.png as the CV', async () => {
      await form.cvInput(page).setInputFiles(SAMPLE_PNG);
      await expect(
        page.getByText('sample.png is not a PDF. Choose a PDF.'),
        'TC-104 ER-2: the CV box refuses a PNG',
      ).toBeVisible();
      await expect(
        form.cvInput(page),
        'TC-104 ER-2: the refusal describes the CV box',
      ).toHaveAccessibleDescription('sample.png is not a PDF. Choose a PDF.');
      await expect(
        form.cvDropzone(page),
        'TC-104 ER-2: the CV box still asks for the CV',
      ).toContainText('Attach your CV');
    });

    await test.step('3. Attach big-cv.pdf as the CV', async () => {
      await form.cvInput(page).setInputFiles({
        name: 'big-cv.pdf',
        mimeType: 'application/pdf',
        buffer: Buffer.alloc(elevenMegabytes),
      });
      await expect(
        page.getByText('big-cv.pdf is 11.0 MB; the limit is 10 MB.'),
        'TC-104 ER-3: the CV box refuses a file over 10 MB',
      ).toBeVisible();
      await expect(form.cvDropzone(page), 'TC-104 ER-3: the file is not kept').toContainText(
        'Attach your CV',
      );
    });

    await test.step('4. Attach sample.pdf as the CV', async () => {
      await form.cvInput(page).setInputFiles(SAMPLE_PDF);
      await expect(
        page.getByText('big-cv.pdf is 11.0 MB; the limit is 10 MB.'),
        'TC-104 ER-4: the message goes away',
      ).toBeHidden();
      await expect(form.cvDropzone(page), 'TC-104 ER-4: the CV box shows the file').toContainText(
        'sample.pdf · 1 KB',
      );
      await expect(
        form.cvDropzone(page),
        'TC-104 ER-4: its button reads "Change file"',
      ).toContainText('Change file');
    });

    await test.step('5. Attach sample.pdf as the photo', async () => {
      await form.photoInput(page).setInputFiles(SAMPLE_PDF);
      await expect(
        page.getByText('sample.pdf is not a PNG or JPG. Choose a PNG or JPG.'),
        'TC-104 ER-5: the photo box refuses a PDF',
      ).toBeVisible();
    });

    await test.step('6. Attach sample.jpg as the photo', async () => {
      await form.photoInput(page).setInputFiles(SAMPLE_JPG);
      await expect(
        page.getByText('sample.pdf is not a PNG or JPG. Choose a PNG or JPG.'),
        'TC-104 ER-6: the message goes away',
      ).toBeHidden();
      await expect(
        form.photoDropzone(page),
        'TC-104 ER-6: the photo box shows the file',
      ).toContainText('sample.jpg · 3 KB');
    });
  });

  test('A complete application is received and held for review @TC-105', async ({
    page,
    playwright,
    baseURL,
  }, testInfo) => {
    await withAdmin(playwright, baseURL, testInfo, ensureStructure);
    const applicant = runApplicant('held', 'E2E Held', '+91 90000 00105');
    undoAfterTest('reject the held application', (api) =>
      rejectLive(api, applicant.email, CLEANUP_REASON),
    );

    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(form.heading(page)).toBeVisible();
    });

    await test.step('2. Attach sample.pdf as the CV', async () => {
      await form.cvInput(page).setInputFiles(SAMPLE_PDF);
    });

    await test.step('3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data', async () => {
      await fillTyped(page, applicant);
    });

    await test.step('4. Choose the college "E2E Registration College", the department "E2E Dual Department" and the course "E2EDP · E2E Dual Programme"', async () => {
      await chooseDualPlace(page);
    });

    await test.step('5. Tick "E2E Analytics" and "E2E Finance"', async () => {
      await form.tick(page, 'E2E Analytics').check();
      await form.tick(page, 'E2E Finance').check();
    });

    await test.step('6. Attach sample.jpg as the photo', async () => {
      await form.photoInput(page).setInputFiles(SAMPLE_JPG);
    });

    await test.step('7. Click Submit registration', async () => {
      await submitForm(page, testInfo);
      await expect(
        page.getByText(HELD_CARD, { exact: true }),
        'TC-105 ER-1: the result card says it is held for review',
      ).toBeVisible();
      await expect(form.submit(page), 'TC-105 ER-1: the card replaces the form').toBeHidden();
      await expect(
        page.getByText(
          'E2E Registration College · E2E Dual Department · E2E Dual Programme · E2E Analytics and E2E Finance',
        ),
        'TC-105 ER-2: the card names where they belong, with both specializations',
      ).toBeVisible();
      await expect(
        page.getByText('Your CV and photo came with the application.'),
        'TC-105 ER-3: both files came with it',
      ).toBeVisible();
      await expect(
        page.getByRole('link', { name: 'Continue to sign in' }),
        'TC-105 ER-3: "Continue to sign in"',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Submit another' }),
        'TC-105 ER-3: "Submit another"',
      ).toBeVisible();
    });

    await test.step('8. Click Continue to sign in', async () => {
      await page.getByRole('link', { name: 'Continue to sign in' }).click();
      await expect(page, 'TC-105 ER-4: the sign-in page opens').toHaveURL(/\/login$/);
      await expect(
        page.getByRole('heading', { level: 1, name: 'Welcome back', exact: true }),
        'TC-105 ER-4: headed "Welcome back"',
      ).toBeVisible();
    });
  });

  test('An application that an auto-approve rule matches is approved at once @TC-106', async ({
    page,
    playwright,
    baseURL,
    signIn,
  }, testInfo) => {
    const applicant: Applicant = {
      ...runApplicant('auto', 'E2E Auto', '+91 90000 00106'),
      usn: await withAdmin(playwright, baseURL, testInfo, freeMbaUsn),
    };
    undoAfterTest('remove the auto-approved student', (api) =>
      removeStudentOf(api, applicant.email, 'AUTO_APPROVED'),
    );
    undoAfterTest('reject the application if a guard held it', (api) =>
      rejectLive(api, applicant.email, CLEANUP_REASON),
    );

    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(form.heading(page)).toBeVisible();
    });

    await test.step('2. Attach sample.pdf as the CV and sample.png as the photo', async () => {
      await form.cvInput(page).setInputFiles(SAMPLE_PDF);
      await form.photoInput(page).setInputFiles(SAMPLE_PNG);
    });

    await test.step('3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data', async () => {
      await fillTyped(page, applicant);
    });

    await test.step('4. Choose the college "BGS College of Engineering and Technology", the department "Department of Management Studies" and the course "MBA · Master of Business Administration"', async () => {
      await chooseSeededPlace(page, { withBatch: false });
    });

    await test.step('5. Tick "Finance" and choose the batch "2024-26"', async () => {
      await form.tick(page, SEEDED.specialization).check();
      await form.batch(page).selectOption({ label: SEEDED.batchOption });
    });

    await test.step('6. Click Submit registration', async () => {
      await submitForm(page, testInfo);
      await expect(
        page.getByText(
          "A seating rule approved your application. Auto-approved by rule 'MBA 2024-26 auto-admit'. " +
            `We have emailed ${applicant.email} a setup link. Open it, confirm the address with the code ` +
            'we send you, and choose a password — approval created your account but no password, and ' +
            'that link is how you get one.',
          { exact: true },
        ),
        'TC-106 ER-1: the result card says a rule approved it and a setup link is on its way',
      ).toBeVisible();
      await expect(
        page.getByText(
          `${SEEDED.college} · ${SEEDED.department} · Master of Business Administration · Finance · ` +
            `Batch ${SEEDED.batchOption}`,
        ),
        'TC-106 ER-2: the card names the college, department, course, specialization and batch',
      ).toBeVisible();
    });

    await test.step('7. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
    });

    await test.step('8. Click the Auto-approved tab', async () => {
      await queue.tab(page, 'Auto-approved').click();
      await queue.search(page).fill(applicant.email);
      await expect(
        queue.applicant(page, applicant.email),
        'TC-106 ER-4: the Auto-approved tab lists the application',
      ).toContainText(applicant.name);
      await expect(queue.cell(page, applicant.usn), 'TC-106 ER-4: with its USN').toBeVisible();
    });
  });

  test('A second application from an address with a live one is refused without saying why @TC-107', async ({
    page,
    playwright,
    baseURL,
    signIn,
  }, testInfo) => {
    await withAdmin(playwright, baseURL, testInfo, async (api) =>
      ensurePending(api, FIXTURE, dualClaim(await ensureStructure(api)), testInfo),
    );
    undoAfterTest('reject the fixture application', (api) =>
      rejectLive(api, FIXTURE.email, CLEANUP_REASON),
    );
    const applicant: Applicant = {
      ...runApplicant('dup', 'E2E Duplicate', '+91 90000 00107'),
      email: FIXTURE.email,
    };

    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(form.heading(page)).toBeVisible();
    });

    await test.step('2. Attach sample.pdf as the CV and sample.jpg as the photo', async () => {
      await form.cvInput(page).setInputFiles(SAMPLE_PDF);
      await form.photoInput(page).setInputFiles(SAMPLE_JPG);
    });

    await test.step('3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data', async () => {
      await fillTyped(page, applicant);
    });

    await test.step('4. Choose the college "BGS College of Engineering and Technology", the department "Department of Management Studies", the course "MBA · Master of Business Administration" and the batch "2024-26"', async () => {
      await chooseSeededPlace(page, { withBatch: true });
    });

    await test.step('5. Click Submit registration', async () => {
      const status = await submitForm(page, testInfo);
      expect(status, 'TC-107 ER-1: the server refuses the application').toBe(409);
      await expect(
        form.problem(page, DUPLICATE_REFUSAL),
        'TC-107 ER-1: the refusal does not say an application exists',
      ).toBeVisible();
      await expect(
        page.getByText(HELD_CARD, { exact: true }),
        'TC-107 ER-2: no result card',
      ).toBeHidden();
      await expect(form.fullName(page), 'TC-107 ER-2: the form stays filled in').toHaveValue(
        applicant.name,
      );
    });

    await test.step('6. Sign in as the Main Admin, open /admin/registrations and type e2e-m02-fixture@bgscet.ac.in in the quick filter', async () => {
      await openQueueAsAdmin(page, signIn);
      await queue.search(page).fill(FIXTURE.email);
      await expect(
        queue.applicant(page, FIXTURE.email),
        'TC-107 ER-3: one pending application for the address, the original',
      ).toHaveCount(1);
      await expect(
        queue.applicant(page, FIXTURE.email),
        'TC-107 ER-3: it is the original',
      ).toContainText(FIXTURE.name);
      await expect(queue.rows(page), 'TC-107 ER-3: no second application').toHaveText('Rows: 1');
    });
  });

  test('A file that is not really a PDF is refused and leaves no application behind @TC-108', async ({
    page,
  }, testInfo) => {
    const applicant = runApplicant('file', 'E2E File Check', '+91 90000 00108');
    undoAfterTest('reject the accepted application', (api) =>
      rejectLive(api, applicant.email, CLEANUP_REASON),
    );

    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(form.heading(page)).toBeVisible();
    });

    await test.step('2. Attach not-a-pdf.pdf as the CV and sample.png as the photo', async () => {
      await form.cvInput(page).setInputFiles({
        name: 'not-a-pdf.pdf',
        mimeType: 'application/pdf',
        buffer: fs.readFileSync(SAMPLE_PNG),
      });
      await form.photoInput(page).setInputFiles(SAMPLE_PNG);
      await expect(
        form.cvDropzone(page),
        'TC-108 ER-1: the browser accepts the renamed file',
      ).toContainText('not-a-pdf.pdf · 1 KB');
    });

    await test.step('3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data', async () => {
      await fillTyped(page, applicant);
    });

    await test.step('4. Choose the college "BGS College of Engineering and Technology", the department "Department of Management Studies", the course "MBA · Master of Business Administration" and the batch "2024-26"', async () => {
      await chooseSeededPlace(page, { withBatch: true });
    });

    await test.step('5. Click Submit registration', async () => {
      const status = await submitForm(page, testInfo);
      expect(status, 'TC-108 ER-2: the server refuses the file').toBe(415);
      await expect(
        form.problem(page, 'The cv must be a PDF.'),
        'TC-108 ER-2: "The cv must be a PDF."',
      ).toBeVisible();
      await expect(
        page.getByText(HELD_CARD, { exact: true }),
        'TC-108 ER-2: no result card',
      ).toBeHidden();
    });

    await test.step('6. Attach sample.pdf as the CV instead', async () => {
      await form.cvInput(page).setInputFiles(SAMPLE_PDF);
    });

    await test.step('7. Click Submit registration again', async () => {
      const status = await submitForm(page, testInfo);
      expect(status, 'TC-108 ER-3: the second attempt is accepted').toBe(201);
      await expect(
        page.getByText(HELD_CARD, { exact: true }),
        'TC-108 ER-3: held for review, so the refused attempt left nothing behind',
      ).toBeVisible();
    });
  });

  test('A rejected applicant can apply again, and the reviewer sees the earlier rejection @TC-109', async ({
    page,
    playwright,
    baseURL,
    signIn,
  }, testInfo) => {
    // The address must have a rejected application and no live one: put any
    // live one out of the way, and on a fresh database make the first one.
    await withAdmin(playwright, baseURL, testInfo, async (api) => {
      await rejectLive(api, REAPPLICANT.email, REAPPLY_REASON);
      if ((await applicationsFor(api, REAPPLICANT.email, 'REJECTED')).length === 0) {
        const first = await submitApplication(
          api,
          { ...REAPPLICANT, usn: 'E2EM02RE1' },
          await seededClaim(api, testInfo),
          testInfo,
        );
        await reject(api, first, REAPPLY_REASON);
      }
    });
    undoAfterTest('reject the new application with the same reason', (api) =>
      rejectLive(api, REAPPLICANT.email, REAPPLY_REASON),
    );

    await test.step('1. Open the registration page at /register', async () => {
      await page.goto('/register');
      await expect(form.heading(page)).toBeVisible();
    });

    await test.step('2. Attach sample.pdf as the CV and sample.jpg as the photo', async () => {
      await form.cvInput(page).setInputFiles(SAMPLE_PDF);
      await form.photoInput(page).setInputFiles(SAMPLE_JPG);
    });

    await test.step('3. Fill in Full name, USN, College email, Personal email, Phone and LinkedIn profile with the test data', async () => {
      await fillTyped(page, REAPPLICANT);
    });

    await test.step('4. Choose the college "BGS College of Engineering and Technology", the department "Department of Management Studies", the course "MBA · Master of Business Administration" and the batch "2024-26"', async () => {
      await chooseSeededPlace(page, { withBatch: true });
    });

    await test.step('5. Click Submit registration', async () => {
      await submitForm(page, testInfo);
      await expect(
        page.getByText(HELD_CARD, { exact: true }),
        'TC-109 ER-1: accepted, not refused as a duplicate',
      ).toBeVisible();
    });

    await test.step('6. Sign in as the Main Admin, open /admin/registrations and type e2e-m02-reapply@bgscet.ac.in in the quick filter', async () => {
      await openQueueAsAdmin(page, signIn);
      await queue.search(page).fill(REAPPLICANT.email);
    });

    await test.step("7. Click the applicant's row", async () => {
      await queue.applicant(page, REAPPLICANT.email).click();
      const first = queue.panel(page).locator('.panel-check').first();
      await expect(
        first.locator('.check-headline'),
        'TC-109 ER-2: the first check says they applied before',
      ).toHaveText(
        /^Applied before - rejected (once|\d+ times), last on \d{2} [A-Z][a-z]{2} \d{4}$/,
      );
      await expect(
        first.locator('.check-detail'),
        'TC-109 ER-3: it quotes the reason given then',
      ).toHaveText(
        `The reason given then: "${REAPPLY_REASON}" This is a fresh application; the earlier one is on the Rejected tab.`,
      );
    });
  });

  test('The review queue lists pending, auto-approved and held applications on their own tabs @TC-110', async ({
    page,
    signIn,
  }) => {
    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
      await expect(
        page.getByText('Students who applied through the sign-up form.', { exact: true }),
        'TC-110 ER-1: the subtitle',
      ).toBeVisible();
      await expect(
        queue.tab(page, /^Pending · \d+$/),
        'TC-110 ER-1: the Pending tab carries a count',
      ).toBeVisible();
      for (const name of ['Auto-approved', 'Held', 'Rejected']) {
        await expect(queue.tab(page, name), `TC-110 ER-1: the ${name} tab`).toBeVisible();
      }
      await expect(
        page.getByText('1 rule auto-approves; everything else waits here for a decision.', {
          exact: true,
        }),
        'TC-110 ER-1: the rule line',
      ).toBeVisible();
      await expect(
        queue.applicant(page, 'ravi.kumar@bgscet.ac.in'),
        'TC-110 ER-1: Pending lists Ravi Kumar',
      ).toContainText('Ravi Kumar');
    });

    await test.step('2. Click the Auto-approved tab', async () => {
      await queue.tab(page, 'Auto-approved').click();
      await expect(
        queue.tab(page, 'Pending'),
        'TC-110 ER-2: the Pending tab loses its count',
      ).toBeVisible();
      await expect(
        queue.applicant(page, 'ravi.kumar@bgscet.ac.in'),
        'TC-110 ER-2: the pending application is not on this tab',
      ).toHaveCount(0);
    });

    await test.step("3. Type 1bg24mba045@bgscet.ac.in in the quick filter and click Asha Rao's row", async () => {
      await searchAndOpen(page, '1bg24mba045@bgscet.ac.in');
      await expect(queue.rows(page), 'TC-110 ER-3: one row is left').toHaveText('Rows: 1');
      await expect(
        queue.applicant(page, '1bg24mba045@bgscet.ac.in'),
        'TC-110 ER-3: it is Asha Rao',
      ).toContainText('Asha Rao');
      await expect(queue.cell(page, '1BG24MBA045'), 'TC-110 ER-3: with her USN').toBeVisible();
      await expect(
        queue.panel(page).getByText('Asha Rao', { exact: true }),
        'TC-110 ER-3: the panel shows her name',
      ).toBeVisible();
      await expect(
        queue.panel(page).getByText('1bg24mba045@bgscet.ac.in · applied'),
        'TC-110 ER-3: and her address',
      ).toHaveText(
        /^\s*1bg24mba045@bgscet\.ac\.in · applied \d{1,2} [A-Z][a-z]{2,3} \d{4}, \d{2}:\d{2}\s*$/,
      );
      await expect(fact(page, 'Batch'), 'TC-110 ER-3: her batch').toHaveText(SEEDED.batchLabel);
      await expect(
        queue
          .panel(page)
          .getByText(
            'No checklist: it is computed for applications still waiting on a decision, and this one has been decided.',
          ),
        'TC-110 ER-3: no checklist for a decided application',
      ).toBeVisible();
      await expect(
        queue.panelButton(page, 'Approve & invite'),
        'TC-110 ER-3: no Approve & invite',
      ).toHaveCount(0);
      await expect(queue.panelButton(page, 'Reject'), 'TC-110 ER-3: no Reject').toHaveCount(0);
      await expect(queue.panelButton(page, 'Hold'), 'TC-110 ER-3: no Hold').toHaveCount(0);
    });

    await test.step('4. Clear the quick filter and click the Held tab', async () => {
      await queue.search(page).fill('');
      await queue.tab(page, 'Held').click();
      await expect(
        queue.applicant(page, 'nikhil.shetty@bgscet.ac.in'),
        'TC-110 ER-4: Held lists Nikhil Shetty',
      ).toContainText('Nikhil Shetty');
      const toolbar = page.locator('.dt-gridbar');
      for (const name of ['Approve', 'Reject', 'Release hold']) {
        await expect(
          toolbar.getByRole('button', { name, exact: true }),
          `TC-110 ER-4: the toolbar offers ${name}`,
        ).toBeVisible();
      }
    });

    await test.step("5. Click Nikhil Shetty's row", async () => {
      await queue.applicant(page, 'nikhil.shetty@bgscet.ac.in').click();
      await expect(
        queue.panel(page).getByText('On hold', { exact: true }),
        'TC-110 ER-5: "On hold"',
      ).toBeVisible();
      await expect(
        queue
          .panel(page)
          .getByText(
            'No CV attached, and the USN on the form is one digit short. Asked him to resend both.',
            { exact: true },
          ),
        'TC-110 ER-5: the hold note',
      ).toBeVisible();
      await expect(holdLine(page), 'TC-110 ER-5: the hold line').toHaveText(HOLD_LINE);
      await expect(fact(page, 'Documents'), 'TC-110 ER-5: no documents').toHaveText('None');
    });
  });

  test("The reviewer's panel shows the applicant, the checks and the attached files @TC-111", async ({
    page,
    playwright,
    baseURL,
    signIn,
  }, testInfo) => {
    await withAdmin(playwright, baseURL, testInfo, async (api) =>
      ensurePending(api, FIXTURE, dualClaim(await ensureStructure(api)), testInfo),
    );
    undoAfterTest('reject the fixture application', (api) =>
      rejectLive(api, FIXTURE.email, CLEANUP_REASON),
    );

    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
    });

    await test.step('2. Type e2e-m02-fixture@bgscet.ac.in in the quick filter', async () => {
      await queue.search(page).fill(FIXTURE.email);
      await expect(queue.rows(page), 'TC-111 ER-1: one row is left').toHaveText('Rows: 1');
      await expect(
        queue.applicant(page, FIXTURE.email),
        'TC-111 ER-1: the fixture applicant',
      ).toContainText(FIXTURE.name);
      await expect(queue.cell(page, FIXTURE.usn), 'TC-111 ER-1: the USN').toBeVisible();
      await expect(
        page
          .getByRole('row')
          .filter({ has: queue.cell(page, FIXTURE.usn) })
          .getByRole('gridcell', { name: 'bgscet.ac.in', exact: true }),
        'TC-111 ER-1: the email domain',
      ).toBeVisible();
    });

    await test.step("3. Click the applicant's row", async () => {
      await queue.applicant(page, FIXTURE.email).click();
      const facts: Array<[string, string]> = [
        ['Batch', 'PG · no batch yet'],
        ['Specialization', 'E2E Analytics and E2E Finance'],
        ['College', E2E_COLLEGE.name],
        ['Department', DUAL_DEPARTMENT.name],
        ['USN', FIXTURE.usn],
        ['Phone', FIXTURE.phone],
        ['Personal email', FIXTURE.personalEmail],
        ['LinkedIn', 'https://www.linkedin.com/in/e2e-m02-fixture'],
        ['Documents', 'CV + photo'],
      ];
      for (const [term, value] of facts) {
        await expect(fact(page, term), `TC-111 ER-2: ${term} reads "${value}"`).toHaveText(value);
      }
      for (const headline of [
        "Routed by rule 'College domain — route to review'",
        'bgscet.ac.in is a college domain',
        'No account on this address',
        `USN ${FIXTURE.usn} is free`,
        'CV and photo attached',
        'No batch on this application',
        'CV attached',
        'Photo attached',
      ]) {
        await expect(check(page, headline), `TC-111 ER-3: the check "${headline}"`).toBeVisible();
      }
      await expect(
        check(page, 'Opted for a dual specialization'),
        'TC-111 ER-3: the dual-specialization check names both',
      ).toContainText(
        'They ticked E2E Analytics and E2E Finance. A batch hangs on one specialization at most, so ' +
          'Approve seats them by the batch and keeps the other as their second specialization.',
      );
      await expect(
        queue.panel(page).getByText(/^Approve will refuse this application/),
        'TC-111 ER-3: no warning that Approve will refuse it',
      ).toHaveCount(0);
    });

    await test.step('4. Click Open CV', async () => {
      const [download] = await Promise.all([
        page.waitForEvent('download'),
        queue.panel(page).getByRole('link', { name: 'Open CV' }).click(),
      ]);
      expect(download.suggestedFilename(), 'TC-111 ER-4: the CV downloads as sample.pdf').toBe(
        'sample.pdf',
      );
      expect(
        (await downloadBytes(download)).equals(fs.readFileSync(SAMPLE_PDF)),
        'TC-111 ER-4: byte for byte the file attached',
      ).toBe(true);
    });

    await test.step('5. Click Open photo', async () => {
      const [download] = await Promise.all([
        page.waitForEvent('download'),
        queue.panel(page).getByRole('link', { name: 'Open photo' }).click(),
      ]);
      expect(download.suggestedFilename(), 'TC-111 ER-5: the photo downloads as sample.png').toBe(
        'sample.png',
      );
      expect(
        (await downloadBytes(download)).equals(fs.readFileSync(SAMPLE_PNG)),
        'TC-111 ER-5: byte for byte the file attached',
      ).toBe(true);
    });
  });

  test('Search, filter and export the review queue @TC-112', async ({ page, signIn }) => {
    const ravi = 'ravi.kumar@bgscet.ac.in';

    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
      await expect(queue.applicant(page, ravi)).toBeVisible();
    });

    await test.step('2. Type ravi.kumar@bgscet.ac.in in the quick filter', async () => {
      await queue.search(page).fill(ravi);
      await expect(queue.rows(page), 'TC-112 ER-1: one row is left').toHaveText('Rows: 1');
      await expect(queue.applicant(page, ravi), "TC-112 ER-1: it is Ravi Kumar's").toContainText(
        'Ravi Kumar',
      );
    });

    await test.step('3. In the Domain check filter, choose "Off-domain (Approve refuses)"', async () => {
      await queue.domainFilter(page).selectOption({ label: 'Off-domain (Approve refuses)' });
      await expect(queue.applicant(page, ravi), 'TC-112 ER-2: no row is left').toHaveCount(0);
      await expect(
        page.getByText('No registration matches this filter.', { exact: true }),
        'TC-112 ER-2: the grid says nothing matches',
      ).toBeVisible();
      await expect(queue.rows(page), 'TC-112 ER-2: "Rows: 0"').toHaveText('Rows: 0');
    });

    await test.step('4. In the Domain check filter, choose "On a college domain"', async () => {
      await queue.domainFilter(page).selectOption({ label: 'On a college domain' });
      await expect(
        queue.applicant(page, ravi),
        "TC-112 ER-3: Ravi Kumar's row is back",
      ).toContainText('Ravi Kumar');
      await expect(queue.rows(page), 'TC-112 ER-3: "Rows: 1"').toHaveText('Rows: 1');
    });

    await test.step('5. Click Export', async () => {
      const [download] = await Promise.all([
        page.waitForEvent('download'),
        page.getByRole('button', { name: 'Export', exact: true }).click(),
      ]);
      expect(download.suggestedFilename(), 'TC-112 ER-4: the file is reep-registrations.csv').toBe(
        'reep-registrations.csv',
      );
      const lines = (await downloadBytes(download))
        .toString('utf8')
        .replace(/^﻿/, '')
        .split(/\r?\n/)
        .filter((line) => line.trim() !== '');
      expect(lines, 'TC-112 ER-4: a header row and one data row').toHaveLength(2);
      expect(lines[0], 'TC-112 ER-4: the header row').toMatch(/^"Applicant","USN","Email domain"/);
      expect(lines[1], "TC-112 ER-4: the data row is Ravi Kumar's").toMatch(/^"Ravi Kumar",/);
    });
  });

  test("Approving an application creates the student's account @TC-113", async ({
    page,
    playwright,
    baseURL,
    signIn,
  }, testInfo) => {
    const applicant = runApplicant('approve', 'E2E Approve', '+91 90000 00113');
    undoAfterTest('remove the approved student', (api) =>
      removeStudentOf(api, applicant.email, 'APPROVED'),
    );
    undoAfterTest('reject the application if it was not approved', (api) =>
      rejectLive(api, applicant.email, CLEANUP_REASON),
    );
    await withAdmin(playwright, baseURL, testInfo, async (api) =>
      submitApplication(api, applicant, await seededClaim(api, testInfo), testInfo),
    );

    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
    });

    await test.step("2. Type the applicant's college email in the quick filter and click their row", async () => {
      await searchAndOpen(page, applicant.email);
      await expect(
        check(page, 'No account on this address'),
        'TC-113 ER-1: no account on the address',
      ).toContainText('Approving creates one and emails the onboarding link.');
      await expect(
        check(page, `USN ${applicant.usn} is free`),
        'TC-113 ER-1: the USN is free',
      ).toBeVisible();
    });

    await test.step('3. Click Approve & invite', async () => {
      await queue.panelButton(page, 'Approve & invite').click();
      await expect(
        queue.notice(
          page,
          `${applicant.name} approved — the account is created and the onboarding link is on its way to their college email.`,
        ),
        'TC-113 ER-2: the notice says the account is created',
      ).toBeVisible();
      await expect(
        queue.applicant(page, applicant.email),
        'TC-113 ER-2: the row leaves Pending',
      ).toHaveCount(0);
    });

    await test.step("4. Open /admin/students and type the applicant's USN in the quick filter", async () => {
      await page.goto('/admin/students');
      await page
        .getByRole('searchbox', { name: 'Search students by name, email or USN' })
        .fill(applicant.usn);
      const grid = page.getByRole('grid');
      // The roster re-reads itself after the search; rows it drops fade out
      // first, so the counts below are waited for rather than read once.
      await expect(
        grid.getByText(applicant.usn, { exact: true }),
        'TC-113 ER-4: the roster lists the USN',
      ).toBeVisible();
      await expect(
        grid.getByText(applicant.name, { exact: true }),
        'TC-113 ER-4: with the name',
      ).toBeVisible();
      await expect(
        grid.getByText(applicant.email, { exact: true }),
        'TC-113 ER-4: with the college email',
      ).toBeVisible();
      await expect(
        grid.getByRole('gridcell', { name: 'FIN', exact: true }),
        'TC-113 ER-4: "FIN" under Spec.: seated in the Finance batch they asked for',
      ).toHaveCount(1);
      await expect(page.getByText(/^Rows: \d+$/), 'TC-113 ER-4: one student').toHaveText('Rows: 1');
    });
  });

  test('Rejecting needs a reason, and a rejection can be undone @TC-114', async ({
    page,
    playwright,
    baseURL,
    signIn,
  }, testInfo) => {
    await withAdmin(playwright, baseURL, testInfo, async (api) =>
      ensurePending(api, FIXTURE, dualClaim(await ensureStructure(api)), testInfo),
    );
    undoAfterTest('reject the fixture application', (api) =>
      rejectLive(api, FIXTURE.email, CLEANUP_REASON),
    );
    const reason = 'E2E: please apply again with a clearer photo.';

    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
    });

    await test.step("2. Type e2e-m02-fixture@bgscet.ac.in in the quick filter and click the applicant's row", async () => {
      await searchAndOpen(page, FIXTURE.email);
      await expect(queue.note(page)).toBeEnabled();
    });

    await test.step('3. Click Reject in the panel, with the Decision note empty', async () => {
      await queue.panelButton(page, 'Reject').click();
      await expect(
        queue.alert(page, 'A reason is required when rejecting an application.'),
        'TC-114 ER-1: a reason is required',
      ).toBeVisible();
      await expect(
        queue.applicant(page, FIXTURE.email),
        'TC-114 ER-1: the row stays',
      ).toBeVisible();
    });

    await test.step('4. Type the reason in Decision note', async () => {
      await queue.note(page).fill(reason);
    });

    await test.step('5. Click Reject in the panel', async () => {
      await queue.panelButton(page, 'Reject').click();
      await expect(
        queue.notice(page, `${FIXTURE.name} rejected — your reason has been emailed to them.`),
        'TC-114 ER-2: the notice says it is rejected',
      ).toBeVisible();
      await expect(
        page.getByRole('status').getByRole('button', { name: 'Undo' }),
        'TC-114 ER-2: with an Undo button',
      ).toBeVisible();
      await expect(
        queue.applicant(page, FIXTURE.email),
        'TC-114 ER-2: the row leaves Pending',
      ).toHaveCount(0);
    });

    await test.step('6. Click Undo in the notice', async () => {
      await page.getByRole('status').getByRole('button', { name: 'Undo' }).click();
      await expect(
        queue.notice(page, `${FIXTURE.name} is back in the queue.`),
        'TC-114 ER-4: the notice says it is back',
      ).toBeVisible();
      await expect(
        queue.applicant(page, FIXTURE.email),
        'TC-114 ER-4: the row is back in Pending',
      ).toBeVisible();
    });
  });

  test('Holding needs a note, and a held application can be released @TC-115', async ({
    page,
    playwright,
    baseURL,
    signIn,
  }, testInfo) => {
    await withAdmin(playwright, baseURL, testInfo, async (api) =>
      ensurePending(api, FIXTURE, dualClaim(await ensureStructure(api)), testInfo),
    );
    undoAfterTest('reject the fixture application', (api) =>
      rejectLive(api, FIXTURE.email, CLEANUP_REASON),
    );
    const note = 'E2E: waiting for a clearer photo.';

    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
    });

    await test.step("2. Type e2e-m02-fixture@bgscet.ac.in in the quick filter and click the applicant's row", async () => {
      await searchAndOpen(page, FIXTURE.email);
      await expect(queue.note(page)).toBeEnabled();
    });

    await test.step('3. Click Hold in the panel, with the Decision note empty', async () => {
      await queue.panelButton(page, 'Hold').click();
      await expect(
        queue.alert(
          page,
          'A note is required when holding an application — say what it is waiting on.',
        ),
        'TC-115 ER-1: a note is required',
      ).toBeVisible();
      await expect(
        queue.applicant(page, FIXTURE.email),
        'TC-115 ER-1: the row stays in Pending',
      ).toBeVisible();
    });

    await test.step('4. Type the note in Decision note', async () => {
      await queue.note(page).fill(note);
    });

    await test.step('5. Click Hold in the panel', async () => {
      await queue.panelButton(page, 'Hold').click();
      await expect(
        queue.notice(
          page,
          `${FIXTURE.name} is on hold — your note is on the application, and nothing was sent to them.`,
        ),
        'TC-115 ER-2: the notice says it is on hold',
      ).toBeVisible();
      await expect(
        queue.applicant(page, FIXTURE.email),
        'TC-115 ER-2: the row leaves Pending',
      ).toHaveCount(0);
    });

    await test.step("6. Click the Held tab and click the applicant's row", async () => {
      await queue.tab(page, 'Held').click();
      await queue.applicant(page, FIXTURE.email).click();
      await expect(
        queue.panel(page).getByText('On hold', { exact: true }),
        'TC-115 ER-4: "On hold"',
      ).toBeVisible();
      await expect(
        queue.panel(page).getByText(note, { exact: true }),
        'TC-115 ER-4: the note',
      ).toBeVisible();
      await expect(holdLine(page), 'TC-115 ER-4: the hold line').toHaveText(HOLD_LINE);
    });

    await test.step('7. Click Release hold in the panel', async () => {
      await queue.panelButton(page, 'Release hold').click();
      await expect(
        queue.notice(page, `${FIXTURE.name} released — back in the pending queue.`),
        'TC-115 ER-5: the notice says it is released',
      ).toBeVisible();
      await expect(
        queue.applicant(page, FIXTURE.email),
        'TC-115 ER-5: the row leaves Held',
      ).toHaveCount(0);
    });
  });

  test("Approve is refused for an address off the college's domains @TC-116", async ({
    page,
    playwright,
    baseURL,
    signIn,
  }, testInfo) => {
    await withAdmin(playwright, baseURL, testInfo, async (api) =>
      ensurePending(api, OFF_DOMAIN, await seededClaim(api, testInfo), testInfo),
    );
    undoAfterTest('reject the off-domain application', (api) =>
      rejectLive(api, OFF_DOMAIN.email, CLEANUP_REASON),
    );

    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
      await expect(queue.applicant(page, 'ravi.kumar@bgscet.ac.in')).toBeVisible();
    });

    await test.step('2. In the Domain check filter, choose "Off-domain (Approve refuses)"', async () => {
      await queue.domainFilter(page).selectOption({ label: 'Off-domain (Approve refuses)' });
      await expect(
        queue.applicant(page, OFF_DOMAIN.email),
        'TC-116 ER-1: the off-domain application',
      ).toBeVisible();
      await expect(
        queue.cell(page, 'example.org'),
        'TC-116 ER-1: its domain is example.org',
      ).toBeVisible();
      await expect(
        queue.applicant(page, 'ravi.kumar@bgscet.ac.in'),
        "TC-116 ER-1: Ravi Kumar's on-domain application is not shown",
      ).toHaveCount(0);
    });

    await test.step('3. Click the row for e2e-m02-offdomain@example.org', async () => {
      await queue.applicant(page, OFF_DOMAIN.email).click();
      await expect(
        check(page, 'example.org is not a college domain'),
        'TC-116 ER-2: the domain check blocks Approve',
      ).toContainText(
        "Approve refuses this (422). Only bgscet.ac.in may become a sign-in account here; a college's own list is set on Colleges.",
      );
      await expect(
        queue
          .panel(page)
          .getByText(
            'Approve will refuse this application while the line marked above stand. Fix the application ' +
              'or reject it — the server answers the same refusal, so approving anyway only returns the error.',
          ),
        'TC-116 ER-2: the warning under the checks',
      ).toBeVisible();
    });

    await test.step('4. Click Approve & invite', async () => {
      await queue.panelButton(page, 'Approve & invite').click();
      await expect(
        queue.alert(
          page,
          'This application cannot be approved: its email address is not on a college domain ' +
            '(bgscet.ac.in). Approving it would create a sign-in account. Staff accounts are created ' +
            'with `python -m app.grant_access`, not from this queue.',
        ),
        'TC-116 ER-3: the server refuses the approval',
      ).toBeVisible();
      await expect(
        queue.applicant(page, OFF_DOMAIN.email),
        'TC-116 ER-3: the row stays',
      ).toBeVisible();
    });
  });

  test('Create, edit, disable and delete an auto-approve rule @TC-117', async ({
    page,
    signIn,
  }) => {
    const name = `E2E rule ${RUN}`;
    undoAfterTest('delete the rule', async (api) => {
      const rules = await jsonOf<Array<{ id: string; name: string }>>(
        await api.get('/api/register/rules'),
        'GET /api/register/rules',
      );
      for (const rule of rules.filter((r) => r.name === name)) {
        await api.delete(`/api/register/rules/${rule.id}`);
      }
    });
    const dialog = page.getByRole('dialog', { name: 'Auto-approve rules' });
    const ruleRow = dialog.getByRole('row').filter({ hasText: name });

    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
    });

    await test.step('2. Click Auto-approve rules', async () => {
      await page.getByRole('button', { name: 'Auto-approve rules' }).click();
      await expect(dialog, 'TC-117 ER-1: the dialog opens').toBeVisible();
      await expect(
        dialog.getByText('Lowest priority wins; ties go to the older rule.', { exact: true }),
        'TC-117 ER-1: the ordering line',
      ).toBeVisible();
      await expectRuleRow(
        dialog.getByRole('row').filter({ hasText: 'MBA 2024-26 auto-admit' }),
        [
          '10',
          'MBA 2024-26 auto-admit',
          'email on bgscet.ac.in · USN like ^1BG2[0-9]MBA[0-9]{3}$ · PG',
          SEEDED.batchLabel,
          'Auto-approves',
        ],
        'TC-117 ER-1: the seeded auto-admit rule',
      );
      await expectRuleRow(
        dialog.getByRole('row').filter({ hasText: 'College domain — route to review' }),
        [
          '100',
          'College domain — route to review',
          'email on bgscet.ac.in',
          'No batch',
          'Waits for review',
        ],
        'TC-117 ER-1: the seeded review rule',
      );
    });

    await test.step('3. Click New rule', async () => {
      await dialog.getByRole('button', { name: 'New rule' }).click();
      await expect(
        dialog.getByText('New rule', { exact: true }),
        'TC-117 ER-2: a "New rule" form',
      ).toBeVisible();
      await expect(
        dialog.getByLabel('Name', { exact: true }),
        'TC-117 ER-2: Name is empty',
      ).toHaveValue('');
      await expect(
        dialog.getByLabel('Priority', { exact: true }),
        'TC-117 ER-2: Priority is 100',
      ).toHaveValue('100');
      await expect(
        dialog.getByLabel('Degree level', { exact: true }).locator('option:checked'),
        'TC-117 ER-2: any degree level',
      ).toHaveText('Any degree level');
      await expect(
        dialog.getByLabel('Seats in', { exact: true }).locator('option:checked'),
        'TC-117 ER-2: no batch',
      ).toHaveText('No batch');
      await expect(
        dialog.getByRole('checkbox', { name: /^Enabled/ }),
        'TC-117 ER-2: Enabled is ticked',
      ).toBeChecked();
      await expect(
        dialog.getByRole('checkbox', { name: /^Auto-approve a matching application/ }),
        'TC-117 ER-2: Auto-approve is not ticked',
      ).not.toBeChecked();
    });

    await test.step('4. Fill in Name, Priority and Email domain with the test data, and click Create rule', async () => {
      await dialog.getByLabel('Name', { exact: true }).fill(name);
      await dialog.getByLabel('Priority', { exact: true }).fill('9000');
      await dialog.getByLabel('Email domain', { exact: true }).fill('e2e-m02.invalid');
      await dialog.getByRole('button', { name: 'Create rule' }).click();
      await expect(
        dialog.getByRole('status').getByText(`Rule '${name}' is live.`),
        'TC-117 ER-3: the notice says the rule is live',
      ).toBeVisible();
      await expectRuleRow(
        ruleRow,
        ['9000', name, 'email on e2e-m02.invalid', 'No batch', 'Waits for review'],
        'TC-117 ER-3: the new row',
      );
    });

    await test.step("5. Click the rule's edit button, change Priority to 9001 and click Save rule", async () => {
      await dialog.getByRole('button', { name: `Edit the rule ${name}` }).click();
      await dialog.getByLabel('Priority', { exact: true }).fill('9001');
      await dialog.getByRole('button', { name: 'Save rule' }).click();
      await expect(
        dialog.getByRole('status').getByText(`Rule '${name}' saved.`),
        'TC-117 ER-4: the notice says it is saved',
      ).toBeVisible();
      await expect(
        ruleRow.getByRole('cell').first(),
        'TC-117 ER-4: the priority is 9001',
      ).toHaveText('9001');
    });

    await test.step("6. Click Disable on the rule's row", async () => {
      await ruleRow.getByRole('button', { name: 'Disable' }).click();
      await expect(
        dialog.getByRole('status').getByText(`Rule '${name}' is now off.`),
        'TC-117 ER-5: the notice says it is off',
      ).toBeVisible();
      await expect(
        ruleRow.getByText('Off', { exact: true }),
        'TC-117 ER-5: the row is labelled "Off"',
      ).toBeVisible();
      await expect(
        ruleRow.getByRole('button', { name: 'Enable' }),
        'TC-117 ER-5: its button reads "Enable"',
      ).toBeVisible();
    });

    await test.step("7. Click the rule's delete button and click OK in the confirmation", async () => {
      let question = '';
      page.once('dialog', (confirmation) => {
        question = confirmation.message();
        void confirmation.accept();
      });
      await dialog.getByRole('button', { name: `Delete the rule ${name}` }).click();
      await expect(
        dialog.getByRole('status').getByText(`Rule '${name}' is gone.`),
        'TC-117 ER-6: the notice says it is gone',
      ).toBeVisible();
      expect(question, 'TC-117 ER-6: the confirmation names the rule').toMatch(
        new RegExp(
          `^Delete the rule '${name}'\\?\\n\\nApplications it already routed will stop showing which rule routed them`,
        ),
      );
      await expect(ruleRow, 'TC-117 ER-6: the row is removed').toHaveCount(0);
    });
  });

  test('A rule that would auto-approve everyone, or that uses a dangerous USN pattern, is refused @TC-118', async ({
    page,
    signIn,
  }) => {
    const name = `E2E unsafe ${RUN}`;
    undoAfterTest('delete the rule if it was created', async (api) => {
      const rules = await jsonOf<Array<{ id: string; name: string }>>(
        await api.get('/api/register/rules'),
        'GET /api/register/rules',
      );
      for (const rule of rules.filter((r) => r.name === name)) {
        await api.delete(`/api/register/rules/${rule.id}`);
      }
    });
    const dialog = page.getByRole('dialog', { name: 'Auto-approve rules' });

    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
    });

    await test.step('2. Click Auto-approve rules, then click New rule', async () => {
      await page.getByRole('button', { name: 'Auto-approve rules' }).click();
      await dialog.getByRole('button', { name: 'New rule' }).click();
      await expect(dialog.getByText('New rule', { exact: true })).toBeVisible();
    });

    await test.step('3. Type the name, tick "Auto-approve a matching application", leave every condition empty and click Create rule', async () => {
      await dialog.getByLabel('Name', { exact: true }).fill(name);
      await dialog.getByRole('checkbox', { name: /^Auto-approve a matching application/ }).check();
      await dialog.getByRole('button', { name: 'Create rule' }).click();
      await expect(
        dialog.getByRole('alert').filter({
          hasText:
            'A rule that auto-approves with no conditions would provision an account for every ' +
            'application ever submitted. Name at least one condition — the email domain is the usual ' +
            'one — or leave auto-approve off.',
        }),
        'TC-118 ER-1: a conditionless auto-approve rule is refused',
      ).toBeVisible();
      await expect(
        dialog.getByRole('button', { name: 'Create rule' }),
        'TC-118 ER-1: the form stays open',
      ).toBeVisible();
    });

    await test.step('4. Type (a+)+$ in USN pattern and click Create rule', async () => {
      await dialog.getByLabel('USN pattern', { exact: true }).fill('(a+)+$');
      const [response] = await Promise.all([
        page.waitForResponse(
          (r) => r.request().method() === 'POST' && r.url().endsWith('/api/register/rules'),
        ),
        dialog.getByRole('button', { name: 'Create rule' }).click(),
      ]);
      expect(response.status(), 'TC-118 ER-2: the server refuses the rule').toBe(422);
      await expect(
        dialog.getByRole('alert').filter({
          hasText:
            'usn_pattern applies a quantifier to a group — refused as a catastrophic-backtracking risk ' +
            'on the public registration endpoint',
        }),
        "TC-118 ER-2: the server's reason is shown",
      ).toBeVisible();
      await expect(
        dialog.getByRole('button', { name: 'Create rule' }),
        'TC-118 ER-2: the form stays open',
      ).toBeVisible();
    });

    await test.step('5. Click Cancel', async () => {
      await dialog.getByRole('button', { name: 'Cancel' }).click();
      await expect(
        dialog.getByRole('button', { name: 'New rule' }),
        'TC-118 ER-3: the form closes',
      ).toBeVisible();
      await expect(
        dialog.getByRole('row').filter({ hasText: 'MBA 2024-26 auto-admit' }),
        'TC-118 ER-3: the rules table is back',
      ).toBeVisible();
      await expect(
        dialog.getByRole('row').filter({ hasText: name }),
        'TC-118 ER-3: no such rule',
      ).toHaveCount(0);
    });
  });

  test('A faculty member without the Registrations grant cannot open the review queue @TC-119', async ({
    page,
    signIn,
  }) => {
    await test.step('1. Sign in as the seeded faculty member', async () => {
      await signIn('mentor');
    });

    await test.step('2. Open /admin/registrations directly in the address bar', async () => {
      await page.goto('/admin/registrations');
      await expect(page, 'TC-119 ER-1: the faculty member is sent to their own home').toHaveURL(
        new RegExp(`${ACCOUNTS.mentor.home}$`),
      );
      await expect(queue.heading(page), 'TC-119 ER-1: the queue is not shown').toHaveCount(0);
    });
  });

  test('The setup page opened without a link says what is missing @TC-120', async ({ page }) => {
    await test.step('1. Open /onboard with no ?token= in the address', async () => {
      await page.goto('/onboard');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Confirm your email address', exact: true }),
        'TC-120 ER-1: the heading',
      ).toBeVisible();
      const alert = page.getByRole('alert');
      await expect(alert, 'TC-120 ER-1: the page says the link is missing').toContainText(
        SETUP_LINK_MISSING,
      );
      await expect(alert, 'TC-120 ER-1: and points at sign-in').toContainText(
        'If you have already set your password, just sign in.',
      );
      await expect(page.getByLabel('Your college email'), 'TC-120 ER-2: no email box').toHaveCount(
        0,
      );
      await expect(
        page.getByRole('button', { name: 'Send me a code' }),
        'TC-120 ER-2: no button',
      ).toHaveCount(0);
    });

    await test.step('2. Click the "sign in" link', async () => {
      await page.getByRole('alert').getByRole('link', { name: 'sign in' }).click();
      await expect(page, 'TC-120 ER-3: the sign-in page opens').toHaveURL(/\/login$/);
      await expect(
        page.getByRole('heading', { level: 1, name: 'Welcome back', exact: true }),
        'TC-120 ER-3: headed "Welcome back"',
      ).toBeVisible();
    });
  });

  test('The setup page refuses a link it does not recognise @TC-121', async ({ page }) => {
    const emailBox = page.getByLabel('Your college email');
    const sendButton = page.getByRole('button', { name: 'Send me a code' });
    let starts = 0;
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/api/auth/onboard/start') starts++;
    });

    await test.step('1. Open /onboard?token=e2e-not-a-real-setup-token-0000', async () => {
      await page.goto('/onboard?token=e2e-not-a-real-setup-token-0000');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Confirm your email address', exact: true }),
        'TC-121 ER-1: the heading',
      ).toBeVisible();
      await expect(
        page.getByText(
          'Type the college email address this link was sent to and we will send you a one-time code to confirm it.',
        ),
        'TC-121 ER-1: the instruction',
      ).toBeVisible();
      await expect(emailBox, 'TC-121 ER-1: the "Your college email" box').toBeVisible();
      await expect(sendButton, 'TC-121 ER-1: the "Send me a code" button').toBeVisible();
    });

    await test.step('2. Type not-an-email in "Your college email" and click Send me a code', async () => {
      await emailBox.fill('not-an-email');
      await sendButton.click();
      await expect(
        page.getByText('Enter the email address this link was sent to.'),
        'TC-121 ER-2: the address is refused on the page',
      ).toBeVisible();
      expect(starts, 'TC-121 ER-2: nothing is sent to the server').toBe(0);
      await expect(emailBox, 'TC-121 ER-2: the page stays on this step').toBeVisible();
    });

    await test.step('3. Replace it with student@bgscet.ac.in and click Send me a code', async () => {
      await emailBox.fill(ACCOUNTS.student.email);
      await sendButton.click();
      const alert = page.getByRole('alert');
      await expect(alert, 'TC-121 ER-3: the link is refused').toContainText(SETUP_LINK_REFUSED);
      await expect(alert, 'TC-121 ER-3: and points at sign-in').toContainText(
        'If you have already set your password, just sign in.',
      );
      await expect(emailBox, 'TC-121 ER-3: the email box is gone').toHaveCount(0);
      await expect(sendButton, 'TC-121 ER-3: the button is gone').toHaveCount(0);
    });
  });

  test('The rules dialog names the batch a rule seats in, even when the batch names arrive last @TC-123', async ({
    page,
    signIn,
  }) => {
    // The two lists are requested together when the page opens, and which one
    // lands first is chance. Hold the batch names back until the rules have
    // been answered, so the order the case is about happens every time. Both
    // responses are the API's own and are not changed.
    const rulesAnswered = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/register/rules' && r.request().method() === 'GET',
    );
    await page.route('**/api/register/hierarchy', async (route) => {
      await rulesAnswered;
      await route.continue();
    });
    const batchNamesAnswered = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/register/hierarchy',
    );
    const dialog = page.getByRole('dialog', { name: 'Auto-approve rules' });

    await test.step('1. Sign in as the Main Admin and open /admin/registrations', async () => {
      await openQueueAsAdmin(page, signIn);
      await batchNamesAnswered;
    });

    await test.step('2. Click Auto-approve rules', async () => {
      await page.getByRole('button', { name: 'Auto-approve rules' }).click();
      await expectRuleRow(
        dialog.getByRole('row').filter({ hasText: 'MBA 2024-26 auto-admit' }),
        [null, 'MBA 2024-26 auto-admit', null, SEEDED.batchLabel],
        'TC-123 ER-1: Seats in names the batch, not an id',
      );
    });
  });
});
