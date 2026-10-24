import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { AgGridAngular } from 'ag-grid-angular';

import { AuthService } from '../../../core/auth.service';
import { AdminStudentsComponent } from './students.component';

/**
 * The roster against a scripted API: one batch holding one student who has
 * signed in, a second batch holding nobody, and one faculty member.
 */
interface Call {
  method: string;
  path: string;
}

const STUDENT = {
  student_id: 's1',
  user_id: 'u1',
  name: 'Test Student',
  email: 'student@bgscet.ac.in',
  usn: '1BG24MBA001',
  cohort_id: 'b1',
  batch: 'MBA · 2026-28',
  department: 'Management',
  department_id: 'd1',
  mentor_id: 'm1',
  mentor_user_id: 'f1',
  mentor_name: 'Test Mentor',
  current_stage: 'EXCEL_ADVANCED',
  current_semester: 2,
  enrolled_at: '2026-07-01T00:00:00Z',
  last_login_at: '2026-09-01T00:00:00Z',
  deleted_at: null,
  delete_reason: null,
};

function batch(id: string) {
  return {
    id,
    code: id.toUpperCase(),
    name: `2026-28 ${id}`,
    batch_label: '2026-28',
    department_id: 'd1',
    course_id: 'k1',
    specialization_id: null,
    course_name: 'MBA',
    specialization_name: null,
    display_label: `MBA · 2026-28 ${id}`,
    degree_level: 'PG',
    current: true,
  };
}

function scriptedFetch(calls: Call[]): typeof fetch {
  const reply = (status: number, body: unknown) =>
    ({ ok: status < 400, status, json: async () => body }) as unknown as Response;
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    const method = init?.method ?? 'GET';
    calls.push({ method, path });
    if (path === '/register/hierarchy') {
      return reply(200, {
        colleges: [
          {
            id: 'c1',
            code: 'BGSCET',
            name: 'BGSCET',
            departments: [
              {
                id: 'd1',
                code: 'MGMT',
                name: 'Management',
                courses: [{ id: 'k1', code: 'MBA', name: 'MBA', specializations: [] }],
                batches: [batch('b1'), batch('b2')],
              },
            ],
          },
        ],
      });
    }
    if (path === '/admin/mentor-load') {
      return reply(200, [
        {
          user_id: 'f1',
          name: 'Test Mentor',
          mentor_id: 'm1',
          department: 'Management',
          capacity: 20,
          mentee_count: 1,
        },
      ]);
    }
    if (method === 'GET' && path.startsWith('/admin/students?')) {
      const query = new URLSearchParams(path.slice(path.indexOf('?') + 1));
      if (query.get('removed') === 'true') return reply(200, []);
      if (query.get('cohort_id') === 'b2') return reply(200, []);
      return reply(200, [STUDENT]);
    }
    if (method === 'PATCH' && path === '/admin/students/s1') return reply(200, STUDENT);
    if (path.startsWith('/admin/cohorts/') && path.endsWith('/promotion-history')) {
      return reply(200, []);
    }
    return reply(404, { detail: `unscripted ${method} ${path}` });
  }) as typeof fetch;
}

/** Poll until `check` holds: the component's loads are plain fetch promises
 *  that `whenStable` does not track. */
async function until(check: () => boolean, tries = 400): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('the condition never held');
}

describe('Students roster', () => {
  const calls: Call[] = [];
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    calls.length = 0;
    globalThis.fetch = scriptedFetch(calls);
    await TestBed.configureTestingModule({
      imports: [AdminStudentsComponent],
      providers: [
        provideRouter([]),
        {
          provide: AuthService,
          useValue: {
            session: signal({
              role: 'ADMIN',
              capabilities: ['admin.students', 'admin.student_records'],
            }),
          },
        },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('does not call a batch empty when a Status filter hid its students', async () => {
    const fixture = TestBed.createComponent(AdminStudentsComponent);
    const c = fixture.componentInstance;
    await until(() => c.apiRows() !== null && c.batches().length === 2);

    c.setBatchFilter('b1');
    await until(() => calls.some((call) => call.path === '/admin/students?cohort_id=b1'));
    await until(() => c.allRows().length === 1);
    c.setStatusFilter('invited');
    expect(c.visibleRows()).toEqual([]);
    expect(c.emptyMessage()).toBe('No student matches these filters.');

    // The Removed list is a different list: an empty one says nothing about
    // who is on the batch's roster.
    c.setStatusFilter('removed');
    await until(() => c.apiRows()?.length === 0);
    expect(c.emptyMessage()).toBe('No student matches these filters.');

    // A batch the server returned nobody for IS empty, whatever the filters.
    c.setStatusFilter('invited');
    c.setBatchFilter('b2');
    await until(() => calls.some((call) => call.path === '/admin/students?cohort_id=b2'));
    await until(() => c.apiRows()?.length === 0);
    expect(c.emptyMessage()).toBe('Nobody is in this batch.');
  });

  it('keeps the batch controls off on the Removed list, which is not the roster', async () => {
    const fixture = TestBed.createComponent(AdminStudentsComponent);
    const c = fixture.componentInstance;
    await until(() => c.apiRows() !== null && c.batches().length === 2);

    c.setBatchFilter('b1');
    await until(() => calls.some((call) => call.path === '/admin/students?cohort_id=b1'));
    await until(() => c.allRows().length === 1);
    expect(c.hasOneBatchInView()).toBe(true);
    expect(c.batchSummary()?.studentCount).toBe(1);

    // b1's Removed list is empty while its roster holds Test Student. A batch
    // action writes to the roster and never to a removed student, so nothing
    // here may count that list as the batch, or call the batch empty.
    c.setStatusFilter('removed');
    await until(() =>
      calls.some((call) => call.path === '/admin/students?cohort_id=b1&removed=true'),
    );
    await until(() => c.apiRows()?.length === 0);
    expect(c.rosterIsNarrowed()).toBe(true);
    expect(c.batchSummary()).toBeNull();
    expect(c.hasOneBatchInView()).toBe(false);
    expect(c.canRemoveBatch()).toBe(false);
    expect(c.batchActionsHint()).toContain('Status');

    fixture.detectChanges();
    const toolbar = fixture.nativeElement.querySelectorAll(
      '.dt-toolbar button',
    ) as NodeListOf<HTMLButtonElement>;
    expect(
      Array.from(toolbar).map((button) => [button.textContent?.trim(), button.disabled]),
    ).toEqual([
      ['edit_note Batch actions', true],
      ['school Graduate batch', true],
      ['arrow_forward Promote batch', true],
    ]);
  });

  it('finds a student by address or name with the Student column hidden', async () => {
    const fixture = TestBed.createComponent(AdminStudentsComponent);
    const c = fixture.componentInstance;
    await until(() => c.apiRows() !== null);
    fixture.detectChanges();
    await fixture.whenStable();

    const grid = fixture.debugElement.query(By.directive(AgGridAngular))
      .componentInstance as AgGridAngular;
    await until(() => c.totalPages() === 1);
    c.toggleColumn('name');
    expect(grid.api.getColumn('name')?.isVisible()).toBe(false);

    // The server's `q` matched these; the grid must not hide the row it sent.
    for (const typed of ['student@bgscet.ac.in', 'Test Student', '1BG24MBA001']) {
      c.quickFilter.set(typed);
      fixture.detectChanges();
      await fixture.whenStable();
      expect(grid.api.getDisplayedRowCount(), typed).toBe(1);
    }
  });

  it("clears the grid's ticks, not only its count, after a selection action", async () => {
    const fixture = TestBed.createComponent(AdminStudentsComponent);
    const c = fixture.componentInstance;
    await until(() => c.apiRows() !== null && c.faculty().length === 1);
    fixture.detectChanges();
    await fixture.whenStable();

    const grid = fixture.debugElement.query(By.directive(AgGridAngular))
      .componentInstance as AgGridAngular;
    // `totalPages` is set by the screen's own gridReady handler.
    await until(() => c.totalPages() === 1);
    const tick = () => grid.api.getRowNode('s1');

    tick()?.setSelected(true);
    await until(() => c.selectedCount() === 1);

    c.openSelectionDialog('mentor');
    c.assignToFacultyId.set('f1');
    await c.applyToSelection();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(c.flash()).toBe('1 of 1 student: assigned to Test Mentor.');
    expect(c.selectedCount()).toBe(0);
    expect(tick()?.isSelected()).toBe(false);
    expect(grid.api.getSelectedRows()).toEqual([]);
  });
});
