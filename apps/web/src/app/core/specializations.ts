/**
 * The register form's Specialization CHECKLIST (2026-09-22), and the one
 * sentence the applicant's result card and the reviewer's panel both print
 * for it.
 *
 * The box was a <select>, and a student who opted for a DUAL specialization
 * — Finance and Marketing, say — could name only one of the two. It is a list
 * of tick boxes now, capped at what the server says (`max_specializations` on
 * `GET /api/register/hierarchy`, the twin of the API's
 * `MAX_SPECIALIZATIONS_PER_APPLICATION`); the API refuses a longer list, so
 * the cap here is the same number met a step earlier, not a second rule.
 *
 * Pure functions with a spec, `consent-sync.ts`'s shape: the component holds
 * the signals and these hold the decisions.
 */

/** The cap until the hierarchy has loaded (or when it failed to). The
 *  pickers are empty in both cases, so nothing can be ticked past it. */
export const MAX_SPECIALIZATIONS_FALLBACK = 2;

/**
 * One tick on the checklist: the picks after `id` is ticked (`checked`) or
 * unticked, in the order ticked. A tick past `max` is ignored rather than
 * evicting an earlier one — the box is drawn disabled at that point, and a
 * list that silently dropped the first choice would record a fact the
 * student did not state. Never mutates `picks`.
 */
export function togglePick(picks: readonly string[], id: string, checked: boolean, max: number): string[] {
  if (!checked) return picks.filter((p) => p !== id);
  if (picks.includes(id) || picks.length >= max) return [...picks];
  return [...picks, id];
}

/**
 * "Finance and Marketing", "Finance", or null when neither is named. The
 * same " and " the server's `dual_specialization` check prints, so the fact
 * line and the check line under it read as one sentence.
 */
export function specializationLabel(
  first: string | null | undefined,
  second: string | null | undefined,
): string | null {
  const names = [first, second].filter((name): name is string => !!name);
  return names.length ? names.join(' and ') : null;
}
