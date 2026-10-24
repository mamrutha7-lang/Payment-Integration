import { TestBed } from '@angular/core/testing';

import { LedgerComponent } from './ledger.component';

/**
 * The ledger's reads and writes, against a scripted API whose answers the test
 * releases one at a time — so the ORDER they arrive in is the test's to choose.
 */

const TODAY = '2026-09-23';

interface Pending {
  path: string;
  method: string;
  body: unknown;
  resolve: (response: Response) => void;
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

/** A ledger day as `GET /student/ledger` answers it, `hours` all in one cell. */
function ledgerDay(day: string, hours = 0, status: 'DRAFT' | 'SUBMITTED' = 'DRAFT') {
  return {
    day,
    today: TODAY,
    status,
    submitted_at: null,
    editable: status === 'DRAFT',
    locked: false,
    edit_until: day,
    edit_window_days: 2,
    lock_reason: null,
    can_submit: false,
    submit_blocked_reason: null,
    total_hours: hours,
    day_capacity_hours: 24,
    unaccounted_hours: 24 - hours,
    activities: [{ key: 'SLEEPING', label: 'Sleep', colour: '#000', productive: false }],
    slots: [
      {
        key: 'NIGHT',
        label: '10:00 pm – 5:00 am',
        icon: null,
        tick: '10p',
        capacity_hours: 24,
        logged_hours: hours,
        weight: 1,
        state_label: 'Empty',
        state_tone: 'neutral',
        cells: hours ? { SLEEPING: hours } : {},
        mix: [],
      },
    ],
    metrics: [],
    legend: [],
  };
}

function history(days: { day: string; status: 'EMPTY' | 'DRAFT' | 'SUBMITTED' }[]) {
  return {
    today: TODAY,
    window_days: days.length,
    edit_window_days: 2,
    days_submitted: days.filter((d) => d.status === 'SUBMITTED').length,
    days_logged: days.filter((d) => d.status !== 'EMPTY').length,
    days: days.map((d) => ({
      ...d,
      logged_hours: d.status === 'EMPTY' ? 0 : 24,
      editable: d.status !== 'SUBMITTED',
      locked: false,
      submitted_at: null,
    })),
  };
}

/** Every request waits in `pending` until the test answers it. */
function scriptedApi() {
  const pending: Pending[] = [];
  const fetchStub = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    return new Promise<Response>((resolve) =>
      pending.push({
        path,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(String(init.body)) : null,
        resolve,
      }),
    );
  }) as typeof fetch;

  /** Answers the one request waiting on `path` (exact match). */
  function answer(path: string, response: Response, method = 'GET'): void {
    const index = pending.findIndex((p) => p.path === path && p.method === method);
    if (index < 0) {
      throw new Error(
        `nothing is waiting on ${method} ${path}: ${pending.map((p) => p.path).join(', ')}`,
      );
    }
    pending.splice(index, 1)[0].resolve(response);
  }

  /** Answers every waiting read the test does not care about. */
  function answerSideReads(): void {
    for (const p of [...pending]) {
      if (p.path.startsWith('/student/timesheet') || p.path === '/student/dashboard') {
        answer(p.path, json({}, 404), p.method);
      }
    }
  }

  return { fetchStub, pending, answer, answerSideReads };
}

async function until(check: () => boolean, tries = 400): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('the condition never held');
}

describe('Time Allocation Ledger', () => {
  const realFetch = globalThis.fetch;
  let api: ReturnType<typeof scriptedApi>;

  beforeEach(async () => {
    api = scriptedApi();
    globalThis.fetch = api.fetchStub;
    await TestBed.configureTestingModule({ imports: [LedgerComponent] }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  /** Opens the screen on today, with the strip loaded. */
  async function opened(days = history([{ day: TODAY, status: 'EMPTY' }])) {
    const c = TestBed.createComponent(LedgerComponent).componentInstance;
    await until(() => api.pending.length >= 4);
    api.answerSideReads();
    api.answer('/student/ledger', json(ledgerDay(TODAY)));
    api.answer('/student/ledger/history?days=14', json(days));
    await until(() => c.state() === 'data' && c.history() !== null);
    return c;
  }

  it('settles on the last day asked for when an earlier day answers after it', async () => {
    const c = await opened();
    c.step(-1); // 22 Sep
    c.step(-1); // 21 Sep
    await until(() => api.pending.length === 2);
    c.step(1); // 22 Sep
    c.step(1); // 23 Sep: the last click
    await until(() => api.pending.length === 4);

    // Today answers first, then the three older days, most recent click last.
    api.answer(`/student/ledger?day=${TODAY}`, json(ledgerDay(TODAY, 3)));
    await until(() => c.state() === 'data');
    api.answer('/student/ledger?day=2026-09-22', json(ledgerDay('2026-09-22', 5)));
    api.answer('/student/ledger?day=2026-09-21', json(ledgerDay('2026-09-21', 7)));
    api.answer('/student/ledger?day=2026-09-22', json(ledgerDay('2026-09-22', 5)));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(c.day()).toBe(TODAY);
    expect(c.ledger()?.day).toBe(TODAY);
    expect(c.dayTotal()).toBe(3);
    expect(c.atToday).toBe(true);
  });

  it('keeps an older answer from undoing the load state of the newer one', async () => {
    const c = await opened();
    c.step(-1); // 22 Sep
    c.step(-1); // 21 Sep, still loading when 22 Sep answers
    await until(() => api.pending.length === 2);
    api.answer('/student/ledger?day=2026-09-22', json(ledgerDay('2026-09-22', 5)));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(c.day()).toBe('2026-09-21');
    expect(c.state()).toBe('loading');
    api.answer('/student/ledger?day=2026-09-21', json(ledgerDay('2026-09-21', 7)));
    await until(() => c.state() === 'data');
    expect(c.ledger()?.day).toBe('2026-09-21');
  });

  it('draws the later of two reads of the strip, whichever answers last', async () => {
    const c = await opened();
    // A save and the submit behind it each re-read the strip.
    void c.loadHistory();
    void c.loadHistory();
    await until(() => api.pending.length === 2);
    const stale = history([
      { day: TODAY, status: 'EMPTY' },
      { day: '2026-09-22', status: 'DRAFT' },
    ]);
    const fresh = history([
      { day: TODAY, status: 'EMPTY' },
      { day: '2026-09-22', status: 'SUBMITTED' },
    ]);
    const [first, second] = api.pending.splice(0, 2);
    second.resolve(json(fresh));
    await until(() => c.history()?.days_submitted === 1);
    first.resolve(json(stale));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(c.history()?.days.find((d) => d.day === '2026-09-22')?.status).toBe('SUBMITTED');
    expect(c.canCopyYesterday()).toBe(true);
  });

  it('saves what is on screen and then submits the same day', async () => {
    const c = await opened();
    c.onCellInput(c.ledger()!.slots[0], 'SLEEPING', '24');
    const submitted = c.submitDay();
    await until(() => api.pending.length === 1);
    api.answer('/student/ledger', json(ledgerDay(TODAY, 24)), 'PUT');
    await until(() => api.pending.some((p) => p.path === '/student/ledger/submit'));
    const submit = api.pending.find((p) => p.path === '/student/ledger/submit')!;
    expect(submit.body).toEqual({ day: TODAY });
    api.answer('/student/ledger/submit', json(ledgerDay(TODAY, 24, 'SUBMITTED')), 'POST');
    await submitted;
    expect(c.submitted()).toBe(true);
  });

  it('submits nothing when the student steps away while Submit day is saving', async () => {
    const c = await opened();
    c.onCellInput(c.ledger()!.slots[0], 'SLEEPING', '24');
    const submitted = c.submitDay();
    await until(() => api.pending.length === 1);
    expect(api.pending[0]).toMatchObject({ path: '/student/ledger', method: 'PUT' });
    expect(api.pending[0].body).toMatchObject({ day: TODAY });

    c.step(-1); // 22 Sep, while the save for 23 Sep is still in flight
    await until(() => api.pending.length === 2);
    api.answer('/student/ledger', json(ledgerDay(TODAY, 24)), 'PUT');
    await new Promise((resolve) => setTimeout(resolve, 20));

    // The save stands; the submit, which cannot be undone, is not sent for
    // either day, and 23 Sep's answer is not drawn over 22 Sep.
    const submits = api.pending.filter((p) => p.path === '/student/ledger/submit');
    expect(submits.map((p) => p.body)).toEqual([]);
    await submitted;
    api.answer('/student/ledger?day=2026-09-22', json(ledgerDay('2026-09-22', 5)));
    await until(() => c.state() === 'data');
    expect(c.ledger()?.day).toBe('2026-09-22');
    expect(c.dayTotal()).toBe(5);
  });

  it('keeps a refusal about one day off the day the student stepped to', async () => {
    const c = await opened(
      history([
        { day: TODAY, status: 'EMPTY' },
        { day: '2026-09-22', status: 'SUBMITTED' },
      ]),
    );
    const copied = c.copyYesterday();
    await until(() => api.pending.length === 1);
    c.step(-1); // 22 Sep
    await until(() => api.pending.length === 2);
    api.answer(
      '/student/ledger/copy-yesterday',
      json({ detail: 'No submitted ledger for 22 Sep 2026 to copy from.' }, 404),
      'POST',
    );
    expect(await copied).toBe(false);
    api.answer('/student/ledger?day=2026-09-22', json(ledgerDay('2026-09-22', 24, 'SUBMITTED')));
    await until(() => c.state() === 'data');
    expect(c.error()).toBeNull();
  });

  it('offers Copy yesterday only on an open day whose previous day was submitted', async () => {
    const c = await opened(
      history([
        { day: TODAY, status: 'EMPTY' },
        { day: '2026-09-22', status: 'SUBMITTED' },
        { day: '2026-09-21', status: 'DRAFT' },
        { day: '2026-09-20', status: 'SUBMITTED' },
      ]),
    );
    expect(c.canCopyYesterday()).toBe(true);

    // The day before 22 Sep is a draft: the server would refuse to copy it.
    c.open('2026-09-22');
    await until(() => api.pending.length === 1);
    api.answer('/student/ledger?day=2026-09-22', json(ledgerDay('2026-09-22', 24, 'SUBMITTED')));
    await until(() => c.state() === 'data');
    expect(c.canCopyYesterday()).toBe(false); // submitted itself, and its source a draft

    c.open('2026-09-21');
    await until(() => api.pending.length === 1);
    expect(c.canCopyYesterday()).toBe(false); // nothing is offered while the day loads
    api.answer('/student/ledger?day=2026-09-21', json(ledgerDay('2026-09-21')));
    await until(() => c.state() === 'data');
    expect(c.canCopyYesterday()).toBe(true);
  });

  it('copies through the server and draws the day it answers with', async () => {
    const c = await opened(
      history([
        { day: TODAY, status: 'EMPTY' },
        { day: '2026-09-22', status: 'SUBMITTED' },
      ]),
    );
    const copied = c.copyYesterday();
    await until(() => api.pending.length === 1);
    expect(api.pending[0].body).toEqual({ day: TODAY });
    api.answer('/student/ledger/copy-yesterday', json(ledgerDay(TODAY, 24)), 'POST');
    expect(await copied).toBe(true);
    expect(c.dayTotal()).toBe(24);
    expect(c.dayState().label).toBe('Reconciled');
  });

  it("shows the server's sentence when a copy is refused", async () => {
    const c = await opened(
      history([
        { day: TODAY, status: 'EMPTY' },
        { day: '2026-09-22', status: 'SUBMITTED' },
      ]),
    );
    const copied = c.copyYesterday();
    await until(() => api.pending.length === 1);
    const refusal = 'No submitted ledger for 22 Sep 2026 to copy from.';
    api.answer('/student/ledger/copy-yesterday', json({ detail: refusal }, 404), 'POST');
    expect(await copied).toBe(false);
    expect(c.error()).toBe(refusal);
    expect(c.dayTotal()).toBe(0);
  });

  it("shows the office's message when the ledger is switched off", async () => {
    const c = TestBed.createComponent(LedgerComponent).componentInstance;
    await until(() => api.pending.length >= 4);
    api.answerSideReads();
    const office = 'The time sheet is paused for the audit.';
    const refused = { 'X-Reep-Feature-Disabled': 'student.time_log' };
    api.answer('/student/ledger', json({ detail: office }, 403, refused));
    api.answer('/student/ledger/history?days=14', json({ detail: office }, 403, refused));
    await until(() => c.state() === 'error');
    expect(c.refusal()).toBe(office);
  });
});
