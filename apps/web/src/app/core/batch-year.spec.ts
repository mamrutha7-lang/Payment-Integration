import {
  endYearOptions,
  formatYearSpan,
  MAX_SPAN_YEARS,
  parseYearSpan,
  spanDates,
  startYearOptions,
} from './batch-year';

/**
 * The rules here have to match `app/seed_catalogue.py`'s `batch_dates` exactly,
 * because a batch made on screen and one made by the seeder are the same row.
 * `apps/api-py/tests/test_seed_catalogue.py` pins the Python side, including
 * its refusal list.
 */
describe('a batch year span', () => {
  it('formats a span the way the seeder writes the label', () => {
    expect(formatYearSpan(2026, 2028)).toBe('2026-28');
    expect(formatYearSpan(2026, 2027)).toBe('2026-27');
    // Century rollover: "2099-01" would read back as 2001 — before the start —
    // so the four-digit form is emitted instead. Both parsers already take it.
    expect(formatYearSpan(2099, 2101)).toBe('2099-2101');
  });

  it('round-trips every span it emits', () => {
    for (let start = 2015; start <= 2105; start++) {
      for (let end = start + 1; end <= start + MAX_SPAN_YEARS; end++) {
        expect(parseYearSpan(formatYearSpan(start, end))).toEqual({ start, end });
      }
    }
  });

  it('reads a span back, expanding a two-digit end into the start century', () => {
    expect(parseYearSpan('2026-28')).toEqual({ start: 2026, end: 2028 });
    expect(parseYearSpan('2025-2027')).toEqual({ start: 2025, end: 2027 });
    expect(parseYearSpan(' 2026-28 ')).toEqual({ start: 2026, end: 2028 });
    // The shape `formatYearSpan` avoids emitting, asserted so the avoidance has
    // a reason on the record: this is what a century rollover would cost.
    expect(parseYearSpan('2099-01')).toBeNull();
    expect(parseYearSpan('2099-2101')).toEqual({ start: 2099, end: 2101 });
  });

  it('answers null for a label that is not a span, rather than guessing', () => {
    // These are not malformed input — they are labels the office legitimately
    // typed, and `app/batch_labels.py`'s whole argument turns on them. A caller
    // must keep showing the stored label, never overwrite it with an invention.
    expect(parseYearSpan('Chain Batch')).toBeNull();
    expect(parseYearSpan('2026-28 Section B')).toBeNull();
    expect(parseYearSpan('X')).toBeNull();
    // And the seeder's own refusals, which this must agree with exactly.
    expect(parseYearSpan('2026')).toBeNull();
    expect(parseYearSpan('26-28')).toBeNull();
    expect(parseYearSpan('2028-26')).toBeNull();
    expect(parseYearSpan('2026-26')).toBeNull();
    expect(parseYearSpan(`2026-${2026 + MAX_SPAN_YEARS + 1}`)).toBeNull();
    expect(parseYearSpan('')).toBeNull();
  });

  it('runs the academic year from 1 July to 30 June', () => {
    expect(spanDates({ start: 2026, end: 2028 })).toEqual({
      entry: '2026-07-01',
      completion: '2028-06-30',
    });
    // A leap year must not move the last day of June.
    expect(spanDates({ start: 2023, end: 2024 }).completion).toBe('2024-06-30');
  });

  it('offers a window of start years around today', () => {
    const years = startYearOptions(2026);
    expect(years[0]).toBe(2016);
    expect(years[years.length - 1]).toBe(2031);
    expect(years).toEqual([...years].sort((a, b) => a - b));
  });

  it('folds an out-of-window year in, so an edit cannot silently move a batch', () => {
    // A select whose value is not among its options renders blank — open that
    // batch, press Save, and it moves to whatever was left in the box.
    const years = startYearOptions(2026, 2009);
    expect(years).toContain(2009);
    expect(years[0]).toBe(2009);
    expect(years.filter((y) => y === 2020)).toHaveLength(1);
  });

  it('offers end years only inside the span the parser will accept', () => {
    const ends = endYearOptions(2026);
    expect(ends[0]).toBe(2027);
    expect(ends[ends.length - 1]).toBe(2026 + MAX_SPAN_YEARS);
    expect(ends).not.toContain(2026);
  });

  it('has no end years until a start is chosen, and folds an older end in', () => {
    expect(endYearOptions(Number.NaN)).toEqual([]);
    expect(endYearOptions(2026, 2040)).toContain(2040);
    // An "include" at or before the start is not a span and is refused.
    expect(endYearOptions(2026, 2026)).not.toContain(2026);
  });
});
