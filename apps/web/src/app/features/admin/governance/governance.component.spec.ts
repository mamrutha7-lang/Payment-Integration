import { provideHttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';

import { endOfLocalDay } from '../../../core/calendar-day';
import { GovernanceComponent } from './governance.component';

/**
 * A grant's dates are the END of the day the office typed, on the office's own
 * clock — the calendar the grant list prints them back in. The form used to
 * send `<day>T23:59:59Z`, which in India is 05:29 the next morning, so "Expires
 * 30 Nov" read back as 01 Dec and the grant ran into a day nobody chose.
 *
 * The exact-body assertions hold in every zone this runs in; the read-back
 * ones are the symptom, and bite wherever the runner is east of UTC.
 */

const REASON = 'Covering the placement drive while the coordinator is away.';

interface Posted {
  path: string;
  body: Record<string, unknown>;
}

function grantOut(
  id: string,
  expiresAt: string | null,
  capability = 'admin.analytics',
  subjectId = 'u1',
): Record<string, unknown> {
  return {
    id,
    capability,
    capability_label: capability === 'admin.jobs' ? 'Jobs sheet' : 'Analytics',
    scope: 'PROGRAMME',
    scope_level: null,
    scope_id: null,
    scope_label: null,
    carries_pii: false,
    approval_state: 'active',
    approved_by: null,
    approved_at: null,
    review_at: expiresAt ?? '2027-03-01T00:00:00Z',
    subject_kind: 'USER',
    subject_id: subjectId,
    subject_label: subjectId === 'u1' ? 'Asha Rao' : 'Ravi Kumar',
    reason: REASON,
    granted_by: 'Main Admin',
    granted_at: '2026-09-01T00:00:00Z',
    expires_at: expiresAt,
  };
}

function scriptedFetch(posted: Posted[], grants: Record<string, unknown>[]): typeof fetch {
  const reply = (status: number, body: unknown) =>
    ({ ok: status < 400, status, json: async () => body }) as unknown as Response;
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.slice(url.indexOf('/api') + 4);
    if ((init?.method ?? 'GET') === 'POST') {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      posted.push({ path, body });
      if (path === '/admin/governance/grants') {
        // What the API does with it: one row per access type, each storing
        // the instant and listed back.
        const keys = (body['capabilities'] as string[] | undefined) ?? ['admin.analytics'];
        const made = keys.map((key, i) =>
          grantOut(`g-new-${i}`, (body['expires_at'] as string | undefined) ?? null, key),
        );
        grants.push(...made);
        return reply(201, made);
      }
      if (path === '/admin/governance/grants/revoke') {
        const ids = new Set(body['grant_ids'] as string[]);
        const revoked = grants.filter((g) => ids.has(g['id'] as string));
        for (const g of revoked) grants.splice(grants.indexOf(g), 1);
        return reply(200, { revoked, already_revoked: [] });
      }
      return reply(200, grants[0]);
    }
    if (path === '/admin/governance/catalogue') {
      return reply(200, {
        capabilities: [
          { key: 'admin.analytics', label: 'Analytics', scope: 'PROGRAMME', carries_pii: false },
          { key: 'admin.jobs', label: 'Jobs sheet', scope: 'PROGRAMME', carries_pii: false },
        ],
        features: [],
        min_reason_chars: 20,
      });
    }
    if (path === '/admin/governance/staff') {
      return reply(200, [
        { user_id: 'u1', name: 'Asha Rao', email: 'asha.rao@bgscet.ac.in', role: 'MENTOR' },
        { user_id: 'u2', name: 'Ravi Kumar', email: 'ravi.kumar@bgscet.ac.in', role: 'MENTOR' },
      ]);
    }
    // A fresh array each time, as a response is: the same reference would
    // not move the component's signal.
    if (path === '/admin/governance/grants') return reply(200, [...grants]);
    if (path === '/admin/governance/review') {
      return reply(200, { horizon_days: 30, pending: [], expiring: [] });
    }
    if (
      path === '/admin/governance/groups' ||
      path === '/admin/governance/features' ||
      path === '/admin/governance/hierarchy'
    ) {
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

/** The day as the grant list prints it, in this runner's own calendar. */
function printed(calendarDay: string): string {
  const [year, month, day] = calendarDay.split('-').map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

describe('Who can do what · the dates a grant is given', () => {
  const realFetch = globalThis.fetch;
  let posted: Posted[];
  let grants: Record<string, unknown>[];

  beforeEach(async () => {
    posted = [];
    grants = [];
    globalThis.fetch = scriptedFetch(posted, grants);
    await TestBed.configureTestingModule({
      imports: [GovernanceComponent],
      providers: [provideRouter([]), provideHttpClient()],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('sends the typed expiry as the end of that day on this clock, and lists it as that day', async () => {
    const fixture = TestBed.createComponent(GovernanceComponent);
    const c = fixture.componentInstance;
    await until(() => c.state() === 'ready');

    c.pickedUserIds.set(['u1']);
    c.pickedKeys.set(['admin.analytics']);
    c.grantReason.set(REASON);
    c.grantExpiry.set('2026-11-30');
    expect(c.canGrant()).toBe(true);
    await c.grant();

    const sent = posted.find((one) => one.path === '/admin/governance/grants');
    expect(sent?.body['expires_at']).toBe(endOfLocalDay('2026-11-30'));
    expect(sent?.body['expires_at']).not.toBe('2026-11-30T23:59:59Z');
    await until(() => c.grantRows().length === 1);
    expect(c.grantRows()[0].expiresLabel).toBe(printed('2026-11-30'));
  });

  it('sends no expiry when the field is left empty', async () => {
    const fixture = TestBed.createComponent(GovernanceComponent);
    const c = fixture.componentInstance;
    await until(() => c.state() === 'ready');

    c.pickedUserIds.set(['u1']);
    c.pickedKeys.set(['admin.analytics']);
    c.grantReason.set(REASON);
    await c.grant();

    const sent = posted.find((one) => one.path === '/admin/governance/grants');
    expect(sent?.body).not.toHaveProperty('expires_at');
  });

  it('extends with both dates as the end of the typed days on this clock', async () => {
    grants.push(grantOut('g1', '2026-10-15T18:29:59.999Z'));
    const fixture = TestBed.createComponent(GovernanceComponent);
    const c = fixture.componentInstance;
    await until(() => c.state() === 'ready');

    c.actionKind.set('extend');
    c.actionIds.set(['g1']);
    c.actionReason.set(REASON);
    c.actionExpiry.set('2026-12-31');
    c.actionReview.set('2026-12-15');
    expect(c.canRunAction()).toBe(true);
    await c.runAction();

    const sent = posted.find((one) => one.path === '/admin/governance/grants/g1/extend');
    expect(sent?.body['expires_at']).toBe(endOfLocalDay('2026-12-31'));
    expect(sent?.body['review_at']).toBe(endOfLocalDay('2026-12-15'));
  });
});

describe('Who can do what · many at a time', () => {
  const realFetch = globalThis.fetch;
  let posted: Posted[];
  let grants: Record<string, unknown>[];

  beforeEach(async () => {
    posted = [];
    grants = [];
    globalThis.fetch = scriptedFetch(posted, grants);
    await TestBed.configureTestingModule({
      imports: [GovernanceComponent],
      providers: [provideRouter([]), provideHttpClient()],
    }).compileComponents();
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('gives several kinds of access to several people in ONE request', async () => {
    const fixture = TestBed.createComponent(GovernanceComponent);
    const c = fixture.componentInstance;
    await until(() => c.state() === 'ready');

    expect(c.canGrant()).toBe(false);
    expect(c.grantBlockedBecause()).toBe('Choose at least one person');
    c.toggleAllShown();
    expect(c.pickedUserIds()).toEqual(['u1', 'u2']);
    expect(c.grantBlockedBecause()).toBe('Choose at least one kind of access');
    c.toggleKey('admin.analytics');
    c.toggleKey('admin.jobs');
    c.grantReason.set(REASON);
    expect(c.grantSummary()).toBe(
      '2 people will get 2 kinds of access for all students, with no end date.',
    );
    await c.grant();

    const sent = posted.filter((one) => one.path === '/admin/governance/grants');
    expect(sent.length).toBe(1);
    expect(sent[0].body['capabilities']).toEqual(['admin.analytics', 'admin.jobs']);
    expect(sent[0].body['user_ids']).toEqual(['u1', 'u2']);
    expect(sent[0].body).not.toHaveProperty('capability');
    // The form is cleared and the list of holders is shown.
    expect(c.pickedKeys()).toEqual([]);
    expect(c.tab()).toBe('holders');
  });

  it('removes one access type from several people in ONE request', async () => {
    grants.push(
      grantOut('a1', null, 'admin.analytics', 'u1'),
      grantOut('a2', null, 'admin.analytics', 'u2'),
      grantOut('j1', null, 'admin.jobs', 'u1'),
    );
    const fixture = TestBed.createComponent(GovernanceComponent);
    const c = fixture.componentInstance;
    await until(() => c.state() === 'ready');

    const analytics = c.holderGroups().find((group) => group.key === 'admin.analytics');
    expect(analytics?.rows.map((row) => row.id).sort()).toEqual(['a1', 'a2']);
    c.toggleGroupSelection(analytics!);
    expect(c.removeLabelFor(analytics!)).toBe('Remove Analytics from 2 people');
    c.startRemoveForGroup(analytics!);
    expect(c.actionKind()).toBe('revoke');
    expect(c.actionIds().sort()).toEqual(['a1', 'a2']);
    c.actionReason.set(REASON);
    await c.runAction();

    const sent = posted.filter((one) => one.path.startsWith('/admin/governance/grants/'));
    expect(sent.map((one) => one.path)).toEqual(['/admin/governance/grants/revoke']);
    expect((sent[0].body['grant_ids'] as string[]).sort()).toEqual(['a1', 'a2']);
    await until(() => c.grantRows().length === 1);
    expect(c.grantRows()[0].id).toBe('j1');
    expect(c.flash()).toBe('Removed “Analytics” from 2 people.');
    expect(c.selectedGrantIds()).toEqual([]);
  });

  it('opens on the tab an older link names', async () => {
    TestBed.overrideProvider(ActivatedRoute, {
      useValue: { snapshot: { queryParamMap: convertToParamMap({ tab: 'review' }) } },
    });
    const fixture = TestBed.createComponent(GovernanceComponent);
    expect(fixture.componentInstance.tab()).toBe('attention');
  });
});
