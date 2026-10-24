import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';

import { AdminInstitutionComponent } from './institution.component';

/**
 * The Colleges card's "Open" lands here with `?college=<id>`, and the screen
 * must open on THAT college rather than on the first of the list — otherwise
 * the button reads as broken on every deployment with more than one college.
 */
function college(id: string, code: string) {
  return {
    id,
    code,
    name: `${code} College`,
    campus: null,
    contact: null,
    status: 'ACTIVE',
    department_count: 1,
    email_domains: [],
  };
}

function scriptedFetch(calls: string[]): typeof fetch {
  const reply = (status: number, body: unknown) =>
    ({ ok: status < 400, status, json: async () => body }) as unknown as Response;
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    calls.push(`${init?.method ?? 'GET'} ${path}`);
    switch (path) {
      case '/admin/hierarchy/levels':
        return reply(200, [
          { key: 'course', label: 'Course', field: 'course_id', required: false },
          {
            key: 'specialization',
            label: 'Specialization',
            field: 'specialization_id',
            required: false,
          },
        ]);
      case '/admin/colleges':
        return reply(200, [college('c1', 'SJBIT'), college('c2', 'BGSCET')]);
      case '/admin/cohorts/incomplete':
      case '/admin/cohorts/unassigned':
        return reply(200, []);
      case '/admin/interview-questions/tracks':
        return reply(403, { detail: 'admin.interview_questions' });
      case '/admin/colleges/c2/departments':
        return reply(200, [
          {
            id: 'd1',
            college_id: 'c2',
            code: 'MBA',
            name: 'Management',
            head: null,
            status: 'ACTIVE',
            cohort_count: 0,
          },
        ]);
      case '/admin/departments/d1/academic-courses':
      case '/admin/departments/d1/cohorts':
        return reply(200, []);
    }
    return reply(404, { detail: `unscripted ${path}` });
  }) as typeof fetch;
}

async function until(check: () => boolean, tries = 400): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('the condition never held');
}

describe('College structure · opened with ?college=', () => {
  const calls: string[] = [];
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    calls.length = 0;
    globalThis.fetch = scriptedFetch(calls);
    await TestBed.configureTestingModule({
      imports: [AdminInstitutionComponent],
      providers: [
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { queryParamMap: convertToParamMap({ college: 'c2' }) } },
        },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('opens on the named college, not the first on the list', async () => {
    const fixture = TestBed.createComponent(AdminInstitutionComponent);
    const c = fixture.componentInstance;
    await until(() => c.state() === 'ready');
    expect(c.selectedCollegeId()).toBe('c2');
    expect(c.selectedDepartmentId()).toBe('d1');
    expect(calls).toContain('GET /admin/colleges/c2/departments');
    expect(calls).not.toContain('GET /admin/colleges/c1/departments');
  });
});
