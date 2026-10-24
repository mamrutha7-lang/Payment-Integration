/**
 * A student screen whose feature the office switched off — and the sentence
 * the office wrote for exactly that moment.
 *
 * `require_feature` (apps/api-py/app/governance.py) refuses with a 403 whose
 * `detail` IS the rule's student-facing message, and marks the refusal with
 * `X-Reep-Feature-Disabled` so a client can tell "switched off for you" from
 * every other failure without parsing English. The student screens threw that
 * detail away on their first read and printed their own "Could not load …"
 * line, so a student refused on purpose was told the app was broken, which is
 * the support call the message exists to prevent — while the feature switches
 * screen promised the office it is "What the student reads when this rule
 * refuses them".
 *
 * ONE helper, read by every screen whose read can be refused this way, rather
 * than a copy per screen, so no two screens can disagree about what a refusal
 * is. Anything that is NOT a feature refusal keeps the screen's own line — a
 * 500's body is not a sentence written for a student.
 */

/** `FEATURE_DISABLED_HEADER` in app/governance.py. */
export const FEATURE_DISABLED_HEADER = 'X-Reep-Feature-Disabled';

/** The office's message when `response` is a switched-off feature, else null. */
export async function featureRefusal(response: Response): Promise<string | null> {
  if (response.status !== 403 || !response.headers.has(FEATURE_DISABLED_HEADER)) return null;
  try {
    const detail = (await response.json())?.detail;
    return typeof detail === 'string' && detail.trim() ? detail : null;
  } catch {
    return null;
  }
}

/** What a failed read says: the office's message for a switched-off feature,
 *  otherwise the screen's own `fallback`. */
export async function failedReadMessage(response: Response, fallback: string): Promise<string> {
  return (await featureRefusal(response)) ?? fallback;
}
