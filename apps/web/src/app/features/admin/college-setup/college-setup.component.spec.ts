import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';

import { AuthService } from '../../../core/auth.service';
import { AdminCollegeSetupComponent } from './college-setup.component';

/**
 * "Create everything" against a scripted API: the five POSTs College structure
 * makes, in parent-before-child order, with the same payloads — and a 409
 * answered by looking the row up rather than failing, which is what makes the
 * button safe to press twice.
 */
interface Call {
  method: string;
  path: string;
  body: unknown;
}

function scriptedFetch(calls: Call[]): typeof fetch {
  const reply = (status: number, body: unknown) =>
    ({ ok: status < 400, status, json: async () => body }) as unknown as Response;
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, path, body });
    if (method === 'GET' && path === '/admin/colleges') return reply(200, []);
    if (method === 'POST' && path === '/admin/colleges') {
      return reply(201, { id: 'c1', code: '1MP', name: 'BGSCET', campus: null, email_domains: [] });
    }
    if (method === 'POST' && path === '/admin/colleges/c1/departments') {
      return reply(201, { id: 'd1', code: 'MBA', name: 'Management', head: null });
    }
    if (method === 'POST' && path === '/admin/departments/d1/academic-courses') {
      return reply(201, { id: 'k1', code: 'MBA', name: 'General MBA', duration_months: 24 });
    }
    if (method === 'POST' && path === '/admin/academic-courses/k1/academic-specializations') {
      return reply(409, { detail: 'A specialization with code FA already exists.' });
    }
    if (method === 'GET' && path === '/admin/academic-courses/k1/academic-specializations') {
      return reply(200, [{ id: 's1', course_id: 'k1', code: 'FA', name: 'Financial Analytics' }]);
    }
    if (method === 'POST' && path === '/admin/departments/d1/cohorts') {
      return reply(201, { id: 'b1', code: body.code, course_id: 'k1', specialization_id: 's1' });
    }
    return reply(404, { detail: `unscripted ${method} ${path}` });
  }) as typeof fetch;
}

describe('Set up a college · Create everything', () => {
  const calls: Call[] = [];
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    calls.length = 0;
    globalThis.fetch = scriptedFetch(calls);
    await TestBed.configureTestingModule({
      imports: [AdminCollegeSetupComponent],
      providers: [
        provideRouter([]),
        {
          provide: AuthService,
          useValue: { session: signal({ role: 'ADMIN', capabilities: ['admin.institution'] }) },
        },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('writes college, department, course, specialization and batch in order, with the screens’ payloads', async () => {
    const fixture = TestBed.createComponent(AdminCollegeSetupComponent);
    const c = fixture.componentInstance;

    c.setCode('1mp');
    c.setName('BGSCET');
    c.setDomains('BGSCET.ac.in');
    const dept = c.departments()[0];
    c.setDepartment(dept.key, 'code', 'MBA');
    c.setDepartment(dept.key, 'name', 'Management');
    c.addCourse(dept.key);
    const course = c.courses()[0];
    c.setCourse(course.key, 'code', 'MBA');
    c.setCourse(course.key, 'name', 'General MBA');
    c.addSpec(course.key);
    const spec = c.specs()[0];
    c.setSpec(spec.key, 'code', 'fa');
    c.setSpec(spec.key, 'name', 'Financial Analytics');
    c.setStartYear('2026');

    expect(c.batches().map((b) => b.code)).toEqual(['1MP-MBA-MBA-FA-2026-28']);
    expect(c.toWrite()).toBe(5);

    await c.createEverything();

    expect(c.runState()).toBe('done');
    expect(c.outcome('college')).toEqual({ status: 'created' });
    expect(c.outcome(dept.key)).toEqual({ status: 'created' });
    expect(c.outcome(course.key)).toEqual({ status: 'created' });
    expect(c.outcome(spec.key)).toEqual({ status: 'existed' });
    expect(c.outcome('batch:' + spec.key)).toEqual({ status: 'created' });

    const posts = calls.filter((k) => k.method === 'POST');
    expect(posts.map((k) => k.path)).toEqual([
      '/admin/colleges',
      '/admin/colleges/c1/departments',
      '/admin/departments/d1/academic-courses',
      '/admin/academic-courses/k1/academic-specializations',
      '/admin/departments/d1/cohorts',
    ]);
    expect(posts[0].body).toEqual({
      code: '1mp',
      name: 'BGSCET',
      campus: null,
      contact: null,
      email_domains: ['bgscet.ac.in'],
    });
    expect(posts[1].body).toEqual({ code: 'MBA', name: 'Management', head: null });
    expect(posts[2].body).toEqual({ code: 'MBA', name: 'General MBA', duration_months: 24 });
    expect(posts[4].body).toEqual({
      code: '1MP-MBA-MBA-FA-2026-28',
      // THE NAME IS THE YEAR. The course and the specialization go down
      // `course_id` / `specialization_id` below, which is the same fact
      // stored once instead of twice; the screens compose them back on.
      name: '2026-28',
      batch_label: '2026-28',
      degree_level: 'PG',
      entry_date: '2026-07-01',
      expected_completion: '2028-06-30',
      course_id: 'k1',
      specialization_id: 's1',
    });
  });

  it('will not leave a step with a required box empty', () => {
    const fixture = TestBed.createComponent(AdminCollegeSetupComponent);
    const c = fixture.componentInstance;
    expect(c.stepComplete()).toBe(false);
    c.setCode('1MP');
    expect(c.stepComplete()).toBe(false);
    c.setName('BGSCET');
    expect(c.stepComplete()).toBe(true);
    c.goNext();
    expect(c.step()).toBe(2);
    expect(c.stepComplete()).toBe(false);
  });
});

/**
 * A refused row stops only what hangs under it, and the second press resumes:
 * the rows the first press wrote are reused rather than written again, and the
 * plan says so for the college exactly as it does for the department.
 */
describe('Set up a college · Create again', () => {
  const calls: Call[] = [];
  const realFetch = globalThis.fetch;

  function refusingFetch(): typeof fetch {
    const reply = (status: number, body: unknown) =>
      ({ ok: status < 400, status, json: async () => body }) as unknown as Response;
    return (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.slice(url.indexOf('/api') + 4);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ method, path, body });
      if (method === 'GET' && path === '/admin/colleges') return reply(200, []);
      if (method === 'POST' && path === '/admin/colleges') {
        return reply(201, {
          id: 'c1',
          code: 'E2E1',
          name: 'Resume',
          campus: null,
          email_domains: [],
        });
      }
      if (method === 'POST' && path === '/admin/colleges/c1/departments') {
        return reply(201, { id: 'd1', code: 'MGT', name: 'Management Studies', head: null });
      }
      if (method === 'POST' && path === '/admin/departments/d1/academic-courses') {
        if (body.code.length > 32) {
          return reply(422, {
            detail: [{ loc: ['body', 'code'], msg: 'String should have at most 32 characters' }],
          });
        }
        return reply(201, { id: 'k1', code: body.code, name: body.name, duration_months: 24 });
      }
      if (method === 'POST' && path === '/admin/departments/d1/cohorts') {
        return reply(201, { id: 'b1', code: body.code, course_id: 'k1', specialization_id: null });
      }
      return reply(404, { detail: `unscripted ${method} ${path}` });
    }) as typeof fetch;
  }

  beforeEach(async () => {
    calls.length = 0;
    globalThis.fetch = refusingFetch();
    await TestBed.configureTestingModule({
      imports: [AdminCollegeSetupComponent],
      providers: [
        provideRouter([]),
        {
          provide: AuthService,
          useValue: { session: signal({ role: 'ADMIN', capabilities: ['admin.institution'] }) },
        },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('reads "Already there" for the college and the department the first press wrote', async () => {
    const fixture = TestBed.createComponent(AdminCollegeSetupComponent);
    const c = fixture.componentInstance;
    c.setCode('E2E1');
    c.setName('Resume');
    const dept = c.departments()[0];
    c.setDepartment(dept.key, 'code', 'MGT');
    c.setDepartment(dept.key, 'name', 'Management Studies');
    c.addCourse(dept.key);
    const course = c.courses()[0];
    c.setCourse(course.key, 'code', 'MBA-THIS-COURSE-CODE-IS-FAR-TOO-LONG');
    c.setCourse(course.key, 'name', 'General MBA');
    c.setStartYear('2026');
    const batch = 'batch:' + course.key;

    await c.createEverything();

    expect(c.runState()).toBe('failed');
    expect(c.outcome('college')).toEqual({ status: 'created' });
    expect(c.outcome(dept.key)).toEqual({ status: 'created' });
    expect(c.outcome(course.key)).toEqual({
      status: 'failed',
      detail: 'String should have at most 32 characters',
    });
    expect(c.outcome(batch)?.status).toBe('skipped');

    c.setCourse(course.key, 'code', 'MBA');
    await c.createEverything();

    expect(c.runState()).toBe('done');
    expect(c.outcome('college')).toEqual({ status: 'existed' });
    expect(c.outcome(dept.key)).toEqual({ status: 'existed' });
    expect(c.outcome(course.key)).toEqual({ status: 'created' });
    expect(c.outcome(batch)).toEqual({ status: 'created' });
    // Reused, not written again.
    const posts = calls.filter((k) => k.method === 'POST').map((k) => k.path);
    expect(posts.filter((p) => p === '/admin/colleges')).toHaveLength(1);
    expect(posts.filter((p) => p === '/admin/colleges/c1/departments')).toHaveLength(1);
  });
});

/** Poll until `check` holds: the component's loads are plain fetch promises
 *  that `whenStable` does not track. */
async function until(check: () => boolean, tries = 400): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('the condition never held');
}

/** A deployment holding one college, 1MP, with a department and a course. */
function existingCollegeFetch(calls: Call[]): typeof fetch {
  const reply = (status: number, body: unknown) =>
    ({ ok: status < 400, status, json: async () => body }) as unknown as Response;
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    const method = init?.method ?? 'GET';
    calls.push({ method, path, body: null });
    switch (path) {
      case '/admin/colleges':
        return reply(200, [
          {
            id: 'c1',
            code: '1MP',
            name: 'BGSCET',
            campus: null,
            contact: null,
            email_domains: [],
          },
        ]);
      case '/admin/colleges/c1/departments':
        return reply(200, [{ id: 'd1', code: 'MBA', name: 'Management', head: null }]);
      case '/admin/departments/d1/academic-courses':
        return reply(200, [{ id: 'k1', code: 'MBA', name: 'General MBA', duration_months: 24 }]);
      case '/admin/departments/d1/cohorts':
        return reply(200, []);
      case '/admin/academic-courses/k1/academic-specializations':
        return reply(200, []);
    }
    return reply(404, { detail: `unscripted ${method} ${path}` });
  }) as typeof fetch;
}

/**
 * Opened from a college card or a College structure section: `?college=` loads
 * that college on step 1 exactly as picking it in the select would, and
 * `?step=` opens on that step — so "Add a course" on College structure is one
 * press away from the course rows rather than six.
 */
describe('Set up a college · opened with ?college= and ?step=', () => {
  const calls: Call[] = [];
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    calls.length = 0;
    globalThis.fetch = existingCollegeFetch(calls);
    await TestBed.configureTestingModule({
      imports: [AdminCollegeSetupComponent],
      providers: [
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { queryParamMap: convertToParamMap({ college: 'c1', step: '3' }) },
          },
        },
        {
          provide: AuthService,
          useValue: { session: signal({ role: 'ADMIN', capabilities: ['admin.institution'] }) },
        },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('loads the named college and opens on the named step', async () => {
    const fixture = TestBed.createComponent(AdminCollegeSetupComponent);
    const c = fixture.componentInstance;
    await until(() => c.step() === 3);
    expect(c.existingCollege()?.id).toBe('c1');
    expect(c.departments().map((d) => d.existingId)).toEqual(['d1']);
    expect(c.courses().map((k) => k.existingId)).toEqual(['k1']);
    expect(c.stepComplete()).toBe(true);
    expect(calls.filter((k) => k.method === 'POST')).toEqual([]);
  });
});

/**
 * Opened with `?college=` alone, on step 1: the picker names the college the
 * screen loaded. The list of colleges and the pick land in the same pass, so a
 * choice made before its option exists would leave "— New college —" showing
 * above the loaded college's facts.
 */
describe('Set up a college · the picker, opened with ?college=', () => {
  const calls: Call[] = [];
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    calls.length = 0;
    globalThis.fetch = existingCollegeFetch(calls);
    await TestBed.configureTestingModule({
      imports: [AdminCollegeSetupComponent],
      providers: [
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { queryParamMap: convertToParamMap({ college: 'c1' }) } },
        },
        {
          provide: AuthService,
          useValue: { session: signal({ role: 'ADMIN', capabilities: ['admin.institution'] }) },
        },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('shows the loaded college as the choice, and "— New college —" once it is let go', async () => {
    const fixture = TestBed.createComponent(AdminCollegeSetupComponent);
    const c = fixture.componentInstance;
    fixture.detectChanges();
    await until(() => c.existingCollege()?.id === 'c1' && !c.loadingExisting());
    fixture.detectChanges();
    const picker = () =>
      (fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>('#setup-existing')!;
    expect(c.step()).toBe(1);
    expect(picker().value).toBe('c1');

    await c.pickCollege('');
    fixture.detectChanges();
    expect(picker().value).toBe('');
  });
});
