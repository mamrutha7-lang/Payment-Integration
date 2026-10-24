import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AuthService } from '../../../core/auth.service';
import { AdminRegistrationsComponent } from './registrations.component';

/** The office account, holding the two capabilities this screen reads. */
function authAs(capabilities: string[]) {
  return { provide: AuthService, useValue: { session: signal({ role: 'ADMIN', capabilities }) } };
}

/**
 * The screen asks for the rules and for the batch names at the same moment it
 * opens, and which answer lands first is chance. A rule's "Seats in" was worked
 * out once, when the rules landed, so a rule read before the names read
 * "Batch 9b799137" for as long as the screen stayed open — while the queue
 * beside it named the same batch correctly.
 */
const BATCH_ID = '9b799137-5c2e-4a51-9f0e-2d7c1a3b4e5f';
const BATCH_LABEL = 'Master of Business Administration - Finance · 2024-26 Section B';

function scriptedFetch(batchNamesHeld: Promise<void>): typeof fetch {
  const reply = (body: unknown) =>
    ({ ok: true, status: 200, headers: new Headers(), json: async () => body }) as Response;
  return (async (input: RequestInfo | URL) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    if (path === '/register/pending') return reply([]);
    if (path === '/register/rules') {
      return reply([
        {
          id: 'r1',
          name: 'MBA 2024-26 auto-admit',
          enabled: true,
          email_domain: 'bgscet.ac.in',
          usn_pattern: null,
          degree_level: 'PG',
          cohort_id: BATCH_ID,
          auto_approve: true,
          priority: 10,
          created_at: '2026-09-01T00:00:00Z',
        },
      ]);
    }
    if (path === '/register/hierarchy') {
      await batchNamesHeld;
      return reply({
        colleges: [
          {
            id: 'c1',
            name: 'BGSCET',
            departments: [
              {
                batches: [
                  {
                    id: BATCH_ID,
                    name: '2024-26 Section B',
                    batch_label: '2024-26',
                    display_label: BATCH_LABEL,
                  },
                ],
              },
            ],
          },
        ],
      });
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

describe('Registrations · the rules dialog', () => {
  const realFetch = globalThis.fetch;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AdminRegistrationsComponent],
      providers: [provideRouter([]), authAs(['admin.registrations'])],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('names the batch a rule seats in when the batch names arrive after the rules', async () => {
    let releaseBatchNames = (): void => undefined;
    const batchNamesHeld = new Promise<void>((resolve) => (releaseBatchNames = resolve));
    globalThis.fetch = scriptedFetch(batchNamesHeld);

    const c = TestBed.createComponent(AdminRegistrationsComponent).componentInstance;
    await until(() => c.seatingRules() !== null);
    // Before the names: the id is all this screen has, and it says so.
    expect(c.seatingRules()?.[0].seatsIn).toBe('Batch 9b799137');

    releaseBatchNames();
    await until(() => c.batchNames().size > 0);
    expect(c.seatingRules()?.[0].seatsIn).toBe(BATCH_LABEL);
  });
});

/**
 * A USN another student already holds (2026-10-16). Approve refused, the
 * checklist said "correct the USN on this application", and nothing on the
 * screen could: Reject was the only button left. These pin the correction and
 * the link to the record of whoever holds the number.
 */
const TAKEN = '1MP25BA033';
const HOLDER_ID = 'student-holder-1';

interface UsnCheck {
  status: 'ok' | 'warn' | 'blocked';
  label: string;
  detail: string;
  student_id?: string | null;
}

function application(usn: string, usnCheck: UsnCheck) {
  return {
    id: 'reg-1',
    name: 'Prajwal Gowda',
    email: 'prajwal.g@bgscet.ac.in',
    usn,
    phone: '9000000000',
    personal_email: 'prajwal@example.com',
    linkedin_url: 'https://www.linkedin.com/in/prajwal',
    degree_level: 'PG',
    status: 'PENDING_REVIEW',
    cohort_id: null,
    matched_rule_id: null,
    decision_reason: null,
    created_at: '2026-10-14T09:00:00Z',
    documents: ['CV', 'PHOTO'],
    college_name: 'BGSCET',
    department_name: 'MBA',
    course_name: null,
    specialization_name: null,
    second_specialization_name: null,
    requested_batch: null,
    checks: [{ key: 'usn_unique', ...usnCheck }],
    hold_note: null,
    held_by_id: null,
    held_at: null,
  };
}

const BLOCKED = application(TAKEN, {
  status: 'blocked',
  label: `USN ${TAKEN} already belongs to Prajwal G (1mp25ba033@bgscet.ac.in)`,
  detail: 'Approve refuses this (409).',
  student_id: HOLDER_ID,
});

interface Sent {
  method: string;
  path: string;
  body: unknown;
}

function queueFetch(sent: Sent[], patchReply: { status: number; body: unknown }): typeof fetch {
  const reply = (status: number, body: unknown) =>
    ({ ok: status < 400, status, headers: new Headers(), json: async () => body }) as Response;
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    const method = init?.method ?? 'GET';
    sent.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (method === 'PATCH') return reply(patchReply.status, patchReply.body);
    if (path === '/register/pending') return reply(200, [BLOCKED]);
    if (path === '/register/rules') return reply(200, []);
    if (path === '/register/hierarchy') return reply(200, { colleges: [] });
    return reply(404, {});
  }) as typeof fetch;
}

describe('Registrations · correcting a USN another student holds', () => {
  const realFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  async function open(capabilities: string[], patchReply: { status: number; body: unknown }) {
    const sent: Sent[] = [];
    globalThis.fetch = queueFetch(sent, patchReply);
    await TestBed.configureTestingModule({
      imports: [AdminRegistrationsComponent],
      providers: [provideRouter([]), authAs(capabilities)],
    }).compileComponents();
    const c = TestBed.createComponent(AdminRegistrationsComponent).componentInstance;
    await until(() => (c.applications()?.length ?? 0) > 0);
    c.focusedRegistrationId.set('reg-1');
    return { c, sent };
  }

  it('saves the corrected USN and draws the verdict the server sent back', async () => {
    const corrected = application('1MP25BA034', {
      status: 'ok',
      label: 'USN 1MP25BA034 is free',
      detail: 'No other student on the roster holds it.',
    });
    const { c, sent } = await open(['admin.registrations'], { status: 200, body: corrected });
    expect(c.blockingChecks().length).toBe(1);

    c.startUsnCorrection(BLOCKED);
    expect(c.usnDraft()).toBe(TAKEN);
    c.usnDraft.set('1mp25ba034');
    await c.saveUsnCorrection(BLOCKED);

    const patch = sent.find((call) => call.method === 'PATCH');
    expect(patch).toEqual({ method: 'PATCH', path: '/register/reg-1', body: { usn: '1mp25ba034' } });
    expect(c.usnEditingId()).toBeNull();
    expect(c.reviewedApplication()?.usn).toBe('1MP25BA034');
    expect(c.blockingChecks().length).toBe(0);
  });

  it("shows the server's refusal under the box and keeps the application as it was", async () => {
    const { c } = await open(['admin.registrations'], {
      status: 409,
      body: { detail: 'USN 1MP25BA035 already belongs to another student.' },
    });

    c.startUsnCorrection(BLOCKED);
    c.usnDraft.set('1MP25BA035');
    await c.saveUsnCorrection(BLOCKED);

    expect(c.usnError()).toBe('USN 1MP25BA035 already belongs to another student.');
    expect(c.usnEditingId()).toBe('reg-1');
    expect(c.reviewedApplication()?.usn).toBe(TAKEN);
  });

  it("links the clash to the holder's record only for a session that can open it", async () => {
    const withRecords = await open(['admin.registrations', 'admin.student_records'], {
      status: 200,
      body: BLOCKED,
    });
    const line = withRecords.c.checks().find((check) => check.headline.startsWith(`USN ${TAKEN}`));
    expect(line?.recordLink).toBe(`/admin/students/${HOLDER_ID}`);

    TestBed.resetTestingModule();
    const withoutRecords = await open(['admin.registrations'], { status: 200, body: BLOCKED });
    const bare = withoutRecords.c.checks().find((check) => check.headline.startsWith(`USN ${TAKEN}`));
    expect(bare?.recordLink ?? null).toBeNull();
  });
});
