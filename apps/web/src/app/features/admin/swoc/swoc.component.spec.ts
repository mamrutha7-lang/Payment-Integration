import { TestBed } from '@angular/core/testing';

import { AdminSwocComponent } from './swoc.component';

/**
 * The reach chip is read off the list response's X-Reep-Scope header, and the
 * empty list says which of two opposite facts it is showing: a grant that
 * reaches nobody is not an empty college.
 */
function scriptedFetch(scope: string, rows: unknown[]): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    if (path.startsWith('/admin/swoc')) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ 'X-Reep-Scope': scope }),
        json: async () => rows,
      } as unknown as Response;
    }
    return { ok: false, status: 404, headers: new Headers(), json: async () => ({}) } as Response;
  }) as typeof fetch;
}

async function until(check: () => boolean, tries = 400): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('the condition never held');
}

describe('SWOC notes · the reach chip and the empty list', () => {
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [AdminSwocComponent] }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('says "every department" for a programme-wide grant and counts the notes', async () => {
    globalThis.fetch = scriptedFetch('programme', [
      {
        student_id: 's1',
        name: 'Asha Rao',
        usn: '1MP25MDM01',
        batch: 'MBA-2026-A',
        entries: [
          {
            id: 'e1',
            kind: 'STRENGTH',
            source: 'PLACEMENT',
            text: 'Clear presenter.',
            weight: 3,
            author: 'Office',
            author_recorded: true,
            recorded_at: '2026-09-01T00:00:00Z',
            updated_at: '2026-09-01T00:00:00Z',
            semester: 3,
            acknowledged_at: null,
          },
        ],
      },
    ]);
    const fixture = TestBed.createComponent(AdminSwocComponent);
    const c = fixture.componentInstance;
    await until(() => c.rows() !== null);
    expect(c.scopeChip()?.label).toBe('Reach · every department');
    expect(c.studentCount()).toBe(1);
    expect(c.writtenCount()).toBe(1);
    expect(c.semesterOptions()).toEqual([3]);
  });

  it('says the grant reaches nobody rather than showing an empty college', async () => {
    globalThis.fetch = scriptedFetch('none', []);
    const fixture = TestBed.createComponent(AdminSwocComponent);
    const c = fixture.componentInstance;
    await until(() => c.rows() !== null);
    expect(c.scopeChip()?.label).toBe('Reach · nobody');
    expect(c.emptyListNote()).toBe('Your access reaches no student.');
  });
});
