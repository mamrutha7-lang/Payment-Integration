/**
 * The words for WHO a leaderboard ranks the student against.
 *
 * `GET /api/student/leaderboards` answers with a `scope`: "batch" when the
 * student is seated in one, "department" when they are seated in no batch but
 * named a department on the registration form, and "none" when neither is
 * recorded. Those are three different situations with three different fixes
 * — nothing, "ask the office to seat you", "ask the office to file you" — so
 * the screen says which one it is instead of drawing an empty board that
 * reads as "nobody has done anything yet" in all three.
 *
 * Pure, so the sentences are pinned by a spec rather than by a screenshot.
 */

export type LeaderboardScope = 'batch' | 'department' | 'none';

/** The line under the title. */
export function scopeSentence(scope: LeaderboardScope, label: string | null): string {
  switch (scope) {
    case 'batch':
      return label
        ? `Ranked within your batch · ${label} — everyone in it sees the same names, positions and totals.`
        : 'Ranked within your batch — everyone in it sees the same names, positions and totals.';
    case 'department':
      return label
        ? `You’re not seated in a batch yet, so you’re ranked within your department · ${label}. The placement office seats you in a batch.`
        : 'You’re not seated in a batch yet, so you’re ranked within your department. The placement office seats you in a batch.';
    default:
      return 'You’re not seated in a batch or filed under a department yet, so there is nobody to rank you against. The placement office seats you when your record is set up.';
  }
}

/** The noun the encouragement lines use: "leading your batch". */
export function scopeNoun(scope: LeaderboardScope): string {
  return scope === 'department' ? 'department' : 'batch';
}

/** The empty-board card's body when nobody in the scope is ranked. */
export function emptyBoardSentence(
  scope: LeaderboardScope,
  boardLabel: string,
  classmates: number,
): string {
  if (scope === 'none') {
    return 'Once the placement office seats you in a batch, your batch mates and their positions appear here.';
  }
  const who = scope === 'department' ? 'your department' : 'your batch';
  if (classmates <= 1) {
    return `You’re the only student in ${who} on the roster so far. Positions appear here as classmates are seated.`;
  }
  return `Nobody in ${who} has a record on the ${boardLabel} board yet. The ${classmates} of you are listed below; positions appear as records land.`;
}
