import { FEATURE_DISABLED_HEADER, failedReadMessage, featureRefusal } from './feature-refusal';

function response(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

/**
 * `require_feature` refuses a switched-off feature with a 403 whose detail is
 * the office's message and whose `X-Reep-Feature-Disabled` header names the
 * feature. That, and only that, replaces a screen's own "Could not load" line.
 */
describe('a switched-off feature', () => {
  const office = 'Leaderboards are paused this week.';

  it("reads the office's message off a feature refusal", async () => {
    const refused = response(
      403,
      { detail: office },
      { [FEATURE_DISABLED_HEADER]: 'student.leaderboards' },
    );
    expect(await featureRefusal(refused)).toBe(office);
  });

  it('prints that message in place of the screen line', async () => {
    const refused = response(
      403,
      { detail: office },
      { [FEATURE_DISABLED_HEADER]: 'student.jobs' },
    );
    expect(await failedReadMessage(refused, 'Could not load the jobs board.')).toBe(office);
  });

  it('keeps the screen line for a 403 that is not a feature refusal', async () => {
    const other = response(403, { detail: 'Student access only.' });
    expect(await featureRefusal(other)).toBeNull();
    expect(await failedReadMessage(other, 'Could not load the leaderboard.')).toBe(
      'Could not load the leaderboard.',
    );
  });

  it('keeps the screen line for a server error, whatever its body says', async () => {
    const broken = response(500, 'Internal Server Error', { 'Content-Type': 'text/plain' });
    expect(await failedReadMessage(broken, 'Could not load your uploads.')).toBe(
      'Could not load your uploads.',
    );
  });

  it('keeps the screen line when a refusal carries no readable message', async () => {
    const headers = { [FEATURE_DISABLED_HEADER]: 'student.uploads' };
    expect(await featureRefusal(response(403, 'not json', headers))).toBeNull();
    expect(await featureRefusal(response(403, { detail: '   ' }, headers))).toBeNull();
    expect(await featureRefusal(response(403, { detail: [{ msg: 'x' }] }, headers))).toBeNull();
  });
});
