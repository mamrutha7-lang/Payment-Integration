/**
 * A batch's year span, as two numbers the office picks rather than a string it
 * types.
 *
 * A BATCH IS A YEAR. A student who joined a two-year MBA in 2026 is in
 * "2026-28", and that span is the whole of what the row is — the course and the
 * specialization are the links it hangs off (`core/batch-label.ts`, and
 * `app/batch_labels.py` for the long version). This module is the other half of
 * that: if the span is what a batch IS, then entering it as free text is asking
 * the office to hand-write the primary key of the thing.
 *
 * WHY THIS IS NOT JUST VALIDATION. `cohorts.batch_label` is free text on the
 * wire too — `AdminCohortIn` bounds it at 1..64 characters and checks nothing
 * else, and no endpoint anywhere derives a year from it. So a typo does not
 * bounce; it is stored, sorted on (three admin lists order by `batch_label`),
 * written into the identity ledger as the student's batch, and printed on the
 * student's own profile card. Two dropdowns make the malformed span
 * unrepresentable instead of merely discouraged.
 *
 * THE PARSER STILL HAS TO COPE WITH WHAT IS ALREADY STORED, which is the part
 * that is easy to get wrong. Existing rows are NOT all spans: `batch_labels`'
 * own reasoning turns on "Chain Batch" and "2026-28 Section B", and the test
 * suite posts labels like "X". `parseYearSpan` answers null for those rather
 * than guessing, and every caller is expected to keep showing the stored label
 * rather than overwrite it — an edit form that silently rewrote an unparseable
 * label into a span it invented would be the migration's "never overwrite what
 * the office wrote", broken from the UI instead of from SQL.
 *
 * The rules match `app/seed_catalogue.py`'s `batch_dates` exactly, because a
 * batch made on screen and one made by the seeder must be the same row:
 * a two- or four-digit end year, the span strictly positive and at most ten
 * years, and the academic year running 1 July to 30 June.
 */

/** The month an academic year begins. `ACADEMIC_YEAR_START_MONTH` in
 *  app/seed_catalogue.py; July, 1-based. */
export const ACADEMIC_YEAR_START_MONTH = 7;

/** The longest span `batch_dates` will accept. A programme longer than this is
 *  a typo far more often than it is a degree. */
export const MAX_SPAN_YEARS = 10;

/** How far back and forward the Start year dropdown reaches by default. Back
 *  ten years so a batch that is still being corrected is reachable; forward
 *  five because an office does set up next year's intake early, and a list that
 *  stops at today makes that impossible rather than merely unusual. */
export const YEARS_BACK = 10;
export const YEARS_AHEAD = 5;

export interface YearSpan {
  readonly start: number;
  readonly end: number;
}

/**
 * `(2026, 2028)` → `"2026-28"` — `seed_catalogue.batch_label`'s shape, and the
 * string that goes in `cohorts.batch_label`.
 *
 * IT EMITS A FOUR-DIGIT END YEAR WHEN A TWO-DIGIT ONE WOULD NOT READ BACK, and
 * that one branch is worth the words. The century expansion both parsers do --
 * `end += start - (start % 100)` in `parseYearSpan` here and in
 * `seed_catalogue.batch_dates` -- puts a two-digit end in the START's century,
 * so a span crossing a century boundary comes back wrong: (2099, 2101) written
 * as "2099-01" reads as 2001, which is before the start, so the parser refuses
 * it. The label would be stored, sorted on, and then unreadable by the form
 * that has to edit it.
 *
 * Writing "2099-2101" instead costs nothing and needs no change on either
 * parser, because both already accept a four-digit end (`\d{2}|\d{4}`). The
 * alternative -- fixing the expansion to add a century when it lands before the
 * start -- would have to be made in `app/seed_catalogue.py` at the same time or
 * the two sides would disagree about what "2099-01" means, and AGENTS.md is
 * explicit that a batch made on screen and one made by the seeder are the same
 * row. This keeps them agreeing.
 *
 * Note `college-setup.model.ts`'s older `batchLabel(start, years)` still emits
 * the bare two-digit form and its spec pins `batchLabel(2099, 2) === '2099-01'`;
 * that helper composes a label from a DURATION and is not a round trip, so it
 * is left alone.
 */
export function formatYearSpan(start: number, end: number): string {
  const short = `${start}-${String(end).slice(-2)}`;
  const span = parseYearSpan(short);
  return span && span.start === start && span.end === end ? short : `${start}-${end}`;
}

/**
 * `"2026-28"` → `{start: 2026, end: 2028}`, and `null` for anything that is not
 * a span.
 *
 * NULL IS A REAL ANSWER AND NOT A FAILURE. A batch may legitimately be called
 * "Chain Batch" or "2026-28 Section B"; the office typed that, and the caller's
 * job is to leave it alone, not to coerce it. A two-digit end year is expanded
 * into the start year's century, the same arithmetic `batch_dates` does, so
 * "2099-01" means 2099→2101 rather than 2099→1901.
 */
export function parseYearSpan(label: string): YearSpan | null {
  const m = /^(\d{4})-(\d{2}|\d{4})$/.exec((label ?? '').trim());
  if (!m) return null;
  const start = Number(m[1]);
  let end = Number(m[2]);
  if (end < 100) end += start - (start % 100);
  if (!(start < end && end <= start + MAX_SPAN_YEARS)) return null;
  return { start, end };
}

/**
 * The dates a span implies: entry on 1 July of the first year, completion on
 * the day before the academic year would next begin (30 June of the last).
 *
 * Returned as `YYYY-MM-DD`, which is what `<input type="date">` reads and what
 * `AdminCohortIn`'s two date fields take. Built in UTC deliberately: these are
 * calendar dates, and a local-time construction slides a day west of Greenwich.
 */
export function spanDates(span: YearSpan): { entry: string; completion: string } {
  const month = String(ACADEMIC_YEAR_START_MONTH).padStart(2, '0');
  // Day 0 of the start month is the last day of the month before it.
  const lastDay = new Date(Date.UTC(span.end, ACADEMIC_YEAR_START_MONTH - 1, 0));
  return { entry: `${span.start}-${month}-01`, completion: lastDay.toISOString().slice(0, 10) };
}

/**
 * The years the Start dropdown offers, oldest first.
 *
 * `include` is what keeps an EDIT honest: a batch that started in 2009 is
 * outside the default window, and a select whose value is not among its options
 * renders blank — so opening that batch and pressing Save would move it,
 * silently, to whatever the office happened to leave in the box. Any year
 * passed here is folded in and the list stays sorted, so the form always has
 * an option equal to the value it was given.
 */
export function startYearOptions(currentYear: number, include?: number | null): number[] {
  const years = new Set<number>();
  for (let y = currentYear - YEARS_BACK; y <= currentYear + YEARS_AHEAD; y++) years.add(y);
  if (include !== null && include !== undefined && Number.isFinite(include)) years.add(include);
  return [...years].sort((a, b) => a - b);
}

/**
 * The years the End dropdown offers for a given start — one to ten years later,
 * the window `batch_dates` will accept.
 *
 * `include` folds in an existing end year the same way and for the same reason.
 * A start year that is not a number yields an empty list rather than a guess,
 * which is what leaves the End select correctly empty until a start is chosen.
 */
export function endYearOptions(start: number, include?: number | null): number[] {
  if (!Number.isFinite(start)) return [];
  const years = new Set<number>();
  for (let y = start + 1; y <= start + MAX_SPAN_YEARS; y++) years.add(y);
  if (include !== null && include !== undefined && Number.isFinite(include) && include > start) {
    years.add(include);
  }
  return [...years].sort((a, b) => a - b);
}
