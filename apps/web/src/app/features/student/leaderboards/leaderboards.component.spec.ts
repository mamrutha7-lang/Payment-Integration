import { TestBed } from '@angular/core/testing';

import { LeaderboardsComponent } from './leaderboards.component';

/**
 * A board the office switched off for this student is refused with the
 * office's own sentence (`require_feature`, a 403 marked
 * `X-Reep-Feature-Disabled`), and the screen must print that sentence rather
 * than its own "Could not load the leaderboard.", which reads as a broken app.
 */
function answering(status: number, body: unknown, headers: Record<string, string> = {}) {
  return (async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json', ...headers },
    })) as typeof fetch;
}

async function until(check: () => boolean, tries = 400): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('the condition never held');
}

describe('Leaderboards · a refused read', () => {
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [LeaderboardsComponent] }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it("prints the office's message when the feature is switched off", async () => {
    globalThis.fetch = answering(
      403,
      { detail: 'Leaderboards are paused this week.' },
      { 'X-Reep-Feature-Disabled': 'student.leaderboards' },
    );
    const c = TestBed.createComponent(LeaderboardsComponent).componentInstance;
    await until(() => c.error() !== null);
    expect(c.error()).toBe('Leaderboards are paused this week.');
  });

  it('keeps its own line for any other failure', async () => {
    globalThis.fetch = answering(500, { detail: 'boom' });
    const c = TestBed.createComponent(LeaderboardsComponent).componentInstance;
    await until(() => c.error() !== null);
    expect(c.error()).toBe('Could not load the leaderboard.');
  });
});

describe('Leaderboards · tabs pressed faster than the boards answer', () => {
  const realFetch = globalThis.fetch;
  const pending = new Map<string, (response: Response) => void>();

  function board(key: string, name: string) {
    return new Response(
      JSON.stringify({
        board: key,
        opted_out: false,
        scope: 'batch',
        scope_label: '2026-28',
        classmates: 2,
        cohort_size: 1,
        rows: [{ rank: 1, student_id: key, initials: 'AB', name, is_me: false, value_label: '1' }],
        unranked: [],
        unranked_total: 0,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }

  beforeEach(async () => {
    pending.clear();
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const key = new URL(String(input), 'http://x').searchParams.get('board') ?? '';
      return new Promise<Response>((resolve) => pending.set(key, resolve));
    }) as typeof fetch;
    await TestBed.configureTestingModule({ imports: [LeaderboardsComponent] }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('draws the tab pressed last, whichever board answers last', async () => {
    const c = TestBed.createComponent(LeaderboardsComponent).componentInstance;
    await until(() => pending.has('overall'));
    c.setTab('skills');
    c.setTab('streak');
    await until(() => pending.has('skills') && pending.has('streak'));

    // Skills answers while Streak is still loading: nothing is drawn yet.
    pending.get('skills')!(board('skills', 'Skills leader'));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(c.rows()).toEqual([]);
    expect(c.loading()).toBe(true);

    pending.get('streak')!(board('streak', 'Streak leader'));
    await until(() => c.rows()[0]?.name === 'Streak leader');
    pending.get('overall')!(board('overall', 'Overall leader'));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(c.activeLabel()).toBe('Streak');
    expect(c.rows().map((r) => r.name)).toEqual(['Streak leader']);
    expect(c.loading()).toBe(false);
  });
});
