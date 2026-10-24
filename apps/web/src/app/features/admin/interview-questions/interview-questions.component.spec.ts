import { TestBed } from '@angular/core/testing';

import { InterviewQuestionsComponent } from './interview-questions.component';

/**
 * The track tabs and the questions under the picked one: the screen lands on
 * the first track the server names, and picking another reads that track's
 * questions and nothing else.
 */
function track(key: string, label: string, count: number) {
  return {
    key,
    label,
    phases: ['opening', 'probing', 'deep_dive', 'wrap_up'],
    count,
    enabled_count: count,
    id: `t-${key}`,
    code: key,
    persona: `a ${label} interviewer`,
    frameworks: ['STAR'],
    sample_question: 'Tell me about a time you led a team through a change.',
    nova_voice: '',
    syllabus: [],
    enabled: true,
    position: 0,
    college_id: null,
    course_id: null,
    specialization_id: null,
    source: 'table',
    editable: true,
  };
}

function question(id: string, trackKey: string, text: string) {
  return {
    id,
    track: trackKey,
    phase: 'probing',
    text,
    position: 0,
    enabled: true,
    created_at: '2026-09-01T00:00:00Z',
  };
}

function scriptedFetch(calls: string[]): typeof fetch {
  const reply = (status: number, body: unknown) =>
    ({ ok: status < 400, status, json: async () => body }) as unknown as Response;
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    calls.push(`${init?.method ?? 'GET'} ${path}`);
    if (path === '/admin/interview-questions/tracks') {
      return reply(200, [track('hr', 'Human Resources', 2), track('fa', 'Financial Analytics', 1)]);
    }
    if (path === '/admin/interview-questions?track=hr') {
      return reply(200, [
        question('q1', 'hr', 'Describe a conflict you resolved at work.'),
        question('q2', 'hr', 'How do you give difficult feedback?'),
      ]);
    }
    if (path === '/admin/interview-questions?track=fa') {
      return reply(200, [question('q3', 'fa', 'Walk me through a DCF.')]);
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

describe('Interview questions · tracks and their questions', () => {
  const calls: string[] = [];
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    calls.length = 0;
    globalThis.fetch = scriptedFetch(calls);
    await TestBed.configureTestingModule({
      imports: [InterviewQuestionsComponent],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('lands on the first track and lists its questions; picking another reads only that one', async () => {
    const fixture = TestBed.createComponent(InterviewQuestionsComponent);
    const c = fixture.componentInstance;
    await until(() => c.questionCount() === 2);
    expect(c.selectedTrackKey()).toBe('hr');
    expect(c.trackCount()).toBe(2);
    expect(c.questionsAcrossTracks()).toBe(3);
    expect(c.pageRows().map((row) => row.number)).toEqual([1, 2]);
    expect(c.draftPersona()).toBe('a Human Resources interviewer');

    await c.selectTrack('fa');
    await until(() => c.questionCount() === 1);
    expect(c.selectedTrackLabel()).toBe('Financial Analytics');
    expect(c.draftPersona()).toBe('a Financial Analytics interviewer');
    expect(calls.filter((k) => k.startsWith('GET /admin/interview-questions?track='))).toEqual([
      'GET /admin/interview-questions?track=hr',
      'GET /admin/interview-questions?track=fa',
    ]);
  });
});

/**
 * An inline edit the bank did not take is put back in the grid: the text box
 * and the phase select hold what the reader did to them, so the row is rebuilt
 * from the stored question — and only that row, so the reader's next box is
 * left alone.
 */
describe('Interview questions · a refused inline edit is put back', () => {
  const calls: string[] = [];
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    calls.length = 0;
    const scripted = scriptedFetch(calls);
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        calls.push(`PATCH ${String(input).slice(String(input).indexOf('/api') + 4)}`);
        return {
          ok: false,
          status: 422,
          json: async () => ({ detail: 'That phase is not on this track.' }),
        } as unknown as Response;
      }
      return scripted(input, init);
    }) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [InterviewQuestionsComponent],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  async function rendered() {
    const fixture = TestBed.createComponent(InterviewQuestionsComponent);
    const c = fixture.componentInstance;
    await until(() => c.questionCount() === 2);
    fixture.detectChanges();
    const root = fixture.nativeElement as HTMLElement;
    return {
      fixture,
      c,
      texts: () => [...root.querySelectorAll<HTMLTextAreaElement>('textarea.bank-question-text')],
      phases: () => [...root.querySelectorAll<HTMLSelectElement>('select.bank-phase-select')],
    };
  }

  it('puts back a question typed below 8 characters, and saves nothing', async () => {
    const { fixture, c, texts } = await rendered();
    const [first, second] = texts();
    expect(first.value).toBe('Describe a conflict you resolved at work.');

    first.value = 'Why?';
    first.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    expect(c.error()).toBe('A question needs at least 8 characters. Put back as it was.');
    expect(texts()[0].value).toBe('Describe a conflict you resolved at work.');
    expect(texts()[1]).toBe(second);
    expect(calls.filter((k) => k.startsWith('PATCH'))).toEqual([]);
  });

  it('puts back a phase the server refused', async () => {
    const { fixture, c, phases } = await rendered();
    const [first, second] = phases();
    expect(first.value).toBe('probing');

    first.value = 'deep_dive';
    first.dispatchEvent(new Event('change'));
    await until(() => c.error() !== null && !c.busy());
    fixture.detectChanges();

    expect(c.error()).toBe('That phase is not on this track.');
    expect(phases()[0].value).toBe('probing');
    expect(phases()[1]).toBe(second);
    expect(calls.filter((k) => k.startsWith('PATCH'))).toEqual([
      'PATCH /admin/interview-questions/q1',
    ]);
  });
});

/**
 * A track picked while the screen is still reading the first one's questions
 * keeps its own list, whichever read answers last.
 */
describe('Interview questions · the list belongs to the track picked last', () => {
  const realFetch = globalThis.fetch;
  const held: Array<() => void> = [];

  beforeEach(async () => {
    held.length = 0;
    const scripted = scriptedFetch([]);
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      // The first track's questions answer late: after the reader has moved on.
      if (String(input).endsWith('/admin/interview-questions?track=hr')) {
        await new Promise<void>((resolve) => held.push(resolve));
      }
      return scripted(input, init);
    }) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [InterviewQuestionsComponent],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('keeps the picked track’s questions when the first track’s read answers after them', async () => {
    const c = TestBed.createComponent(InterviewQuestionsComponent).componentInstance;
    await until(() => c.tracks() !== null && held.length === 1);

    await c.selectTrack('fa');
    expect(c.questionCount()).toBe(1);

    held.splice(0).forEach((release) => release());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(c.selectedTrackKey()).toBe('fa');
    expect(c.questionCount()).toBe(1);
    expect(c.pageRows().map((row) => row.question.id)).toEqual(['q3']);
  });
});
