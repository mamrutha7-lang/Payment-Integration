import { filterQuery, filterUrl, redactPii, scrubBreadcrumb, scrubEvent } from './telemetry-scrub';

/**
 * The browser half of rule 1 applied to telemetry. These are the strings that
 * must never reach Sentry from a student's browser: the account token in
 * /reset?token= and /activate?token=, the Google authorization code on the
 * SSO callback, a cookie, and an identifier — an email that IS the USN here.
 * Each test asserts the leak is gone AND that the screen selector beside it
 * survived, because a scrubber that blanks the whole query string passes the
 * first half and makes "which leaderboard was slow" unanswerable.
 */
describe('telemetry-scrub', () => {
  const TOKEN = 'deadbeefcafe0123';

  it('filters credentials out of a query string and keeps the screen selectors', () => {
    const qs = filterQuery(`token=${TOKEN}&code=4/0Axyz&specialization=hr&board=cgpa`);
    expect(qs).not.toContain(TOKEN);
    expect(qs).not.toContain('4/0Axyz');
    expect(qs).not.toContain('4%2F0Axyz');
    expect(qs).toContain('specialization=hr');
    expect(qs).toContain('board=cgpa');
    expect(qs).toContain('token=%5BFiltered%5D');
  });

  it('filters the url the SDK reads from location.href, fragment included', () => {
    expect(filterUrl(`https://reep.example/reset?token=${TOKEN}#section`)).toBe(
      'https://reep.example/reset?token=%5BFiltered%5D',
    );
    expect(filterUrl('/student/leaderboards?board=cgpa')).toBe('/student/leaderboards?board=cgpa');
    expect(filterUrl('/student/profile')).toBe('/student/profile');
    expect(filterUrl('/login?')).toBe('/login');
  });

  it('strips cookies, bodies, credential headers and the user from an event', () => {
    const event = scrubEvent({
      request: {
        url: `https://reep.example/activate?token=${TOKEN}`,
        query_string: `token=${TOKEN}`,
        headers: { Cookie: 'reep_session=secret', Authorization: 'Bearer x', 'User-Agent': 'ua' },
        cookies: { reep_session: 'secret' },
        data: { password: 'hunter2hunter2' },
      },
      user: { id: '1', email: 'someone@bgscet.ac.in' },
      message: 'reset failed for 1mp25mdm01@bgscet.ac.in',
      exception: { values: [{ value: 'USN 1MP25MDM01 not found' }] },
      breadcrumbs: [{ category: 'navigation', data: { from: `/reset?token=${TOKEN}`, to: '/login?verified=1' } }],
    });
    const blob = JSON.stringify(event);
    expect(blob).not.toContain(TOKEN);
    expect(blob).not.toContain('secret');
    expect(blob).not.toContain('Bearer x');
    expect(blob).not.toContain('hunter2');
    expect(blob).not.toContain('bgscet.ac.in');
    expect(blob).not.toContain('1MP25MDM01');
    expect(event.request?.headers?.['User-Agent']).toBe('ua');
    expect(event.user).toBeUndefined();
    expect(event.breadcrumbs?.[0].data?.['to']).toBe('/login?verified=1');
  });

  it('redacts a console breadcrumb and drops its arguments', () => {
    const crumb = scrubBreadcrumb({
      category: 'console',
      message: 'profile loaded for 1mp25mdm01@bgscet.ac.in (+91 98765 43210)',
      data: { arguments: [{ usn: '1MP25MDM01' }], logger: 'console' },
    });
    expect(crumb).not.toBeNull();
    expect(crumb?.message).not.toContain('bgscet');
    expect(crumb?.message).not.toContain('98765');
    expect(crumb?.data?.['arguments']).toBeUndefined();
    expect(crumb?.data?.['logger']).toBe('console');
  });

  it('leaves ordinary words alone', () => {
    expect(redactPii('Interview hr scheduled for tomorrow')).toBe('Interview hr scheduled for tomorrow');
  });
});
