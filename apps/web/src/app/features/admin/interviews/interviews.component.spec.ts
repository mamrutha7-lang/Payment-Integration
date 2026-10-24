import { TestBed } from '@angular/core/testing';

import { InterviewRecordsComponent } from './interviews.component';

/**
 * The policy card is always open, so its college list is read when the
 * screen opens, and picking a college reads that college's sheet. A college
 * nobody has configured reads "Not configured" with the deployment's
 * defaults in the boxes — never as a stored row.
 */
function scriptedFetch(calls: string[]): typeof fetch {
  const reply = (status: number, body: unknown) =>
    ({ ok: status < 400, status, json: async () => body }) as unknown as Response;
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    calls.push(`${init?.method ?? 'GET'} ${path}`);
    if (path.startsWith('/admin/interviews/summary')) {
      return reply(200, {
        interviews: 0,
        completed: 0,
        abandoned: 0,
        failed: 0,
        running: 0,
        students: 0,
        recorded: 0,
        scored: 0,
        average_overall: null,
      });
    }
    if (path.startsWith('/admin/interviews?')) {
      return reply(200, { rows: [], next_cursor: null, page_size: 200 });
    }
    if (path === '/admin/cohorts') return reply(200, []);
    if (path === '/admin/colleges') {
      return reply(200, [{ id: 'c1', code: '1MP', name: 'BGSCET', status: 'ACTIVE' }]);
    }
    if (path === '/admin/interview-policies/c1') {
      return reply(200, {
        college_id: 'c1',
        college_name: 'BGSCET',
        default: null,
        courses: [],
        effective_default: {
          store_transcript: true,
          store_audio: false,
          retention_days: 180,
          daily_cap: 8,
          attempt_cap: 20,
          time_limit_seconds: 900,
          source: 'default',
        },
      });
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

describe('Interview records · the policy card', () => {
  const calls: string[] = [];
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    calls.length = 0;
    globalThis.fetch = scriptedFetch(calls);
    await TestBed.configureTestingModule({
      imports: [InterviewRecordsComponent],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('reads the colleges when the screen opens, opens the only one, and reads an unconfigured college as the defaults', async () => {
    const fixture = TestBed.createComponent(InterviewRecordsComponent);
    const c = fixture.componentInstance;
    await until(() => c.colleges() !== null && !c.loading());
    expect(calls).toContain('GET /admin/colleges');
    expect(c.collegesBlocked()).toBeNull();
    // ONE college on the deployment: the card opens on it rather than on
    // "Choose…", because the office came here to tick a box, not to answer a
    // question with one possible answer.
    await until(() => c.policySheet() !== null);
    expect(c.policyCollege()).toBe('c1');
    expect(calls).toContain('GET /admin/interview-policies/c1');

    const pick = { target: { value: 'c1' } } as unknown as Event;
    await c.setPolicyCollege(pick);
    expect(c.policySheet()?.college_name).toBe('BGSCET');
    expect(c.policyIsConfigured()).toBe(false);
    expect(c.draftRetentionDays()).toBe(180);
    expect(c.draftAttemptCap()).toBe(20);
    expect(c.policyCapsAreOrdered()).toBe(true);
    expect(c.canSavePolicy()).toBe(true);
  });
});

/**
 * Two filter changes in quick succession put two reads in flight. The screen
 * must end on the list and the tiles of the filters chosen LAST, whichever
 * answer happens to arrive last (TC-730).
 */
describe('Interview records · the filters', () => {
  const realFetch = globalThis.fetch;
  const held: Array<() => void> = [];
  let holdUntracked = false;

  const summary = (interviews: number) => ({
    interviews,
    completed: 0,
    abandoned: 0,
    failed: interviews,
    running: 0,
    students: 1,
    recorded: 0,
    scored: 0,
    average_overall: null,
  });
  const record = (session_id: string, specialization: string) => ({
    session_id,
    student_id: 'st1',
    student_name: 'Test Student',
    usn: '1BG24MBA001',
    specialization,
    status: 'failed',
    audio_recorded: false,
    audio_skipped_reason: 'operator_off',
    started_at: '2026-09-22T10:00:00Z',
    ended_at: '2026-09-22T10:00:05Z',
    overall_score: null,
    report_status: null,
  });
  const hr = record('s-hr', 'hr');
  const dm = record('s-dm', 'dm');

  beforeEach(async () => {
    held.length = 0;
    holdUntracked = false;
    const scripted = scriptedFetch([]);
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.slice(url.indexOf('/api') + 4);
      if (!path.startsWith('/admin/interviews?') && !path.startsWith('/admin/interviews/summary')) {
        return scripted(input, init);
      }
      const tracked = new URL(url, 'http://x').searchParams.get('track') === 'dm';
      // The read with no Track filter is the slow one, when the test says so.
      if (!tracked && holdUntracked) await new Promise<void>((resolve) => held.push(resolve));
      const body = path.startsWith('/admin/interviews/summary')
        ? summary(tracked ? 1 : 2)
        : { rows: tracked ? [dm] : [hr, dm], next_cursor: null, page_size: 200 };
      return { ok: true, status: 200, json: async () => body } as unknown as Response;
    }) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [InterviewRecordsComponent],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('keeps the list and the tiles of the filters chosen last when an older read answers after them', async () => {
    const c = TestBed.createComponent(InterviewRecordsComponent).componentInstance;
    await until(() => !c.loading() && c.kpis() !== null);
    expect(c.records()?.length).toBe(2);

    holdUntracked = true;
    c.setStatusFilter({ target: { value: 'failed' } } as unknown as Event);
    c.setTrackFilter({ target: { value: 'dm' } } as unknown as Event);
    await until(() => !c.loading() && c.kpis()?.interviews === 1);
    expect(c.records()?.map((row) => row.session_id)).toEqual(['s-dm']);

    // The Status-only read answers now, last.
    held.splice(0).forEach((release) => release());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(c.trackFilter()).toBe('dm');
    expect(c.records()?.map((row) => row.session_id)).toEqual(['s-dm']);
    expect(c.kpis()?.interviews).toBe(1);
    expect(c.loading()).toBe(false);
  });
});
