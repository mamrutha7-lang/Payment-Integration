/**
 * The instant a day typed into an `<input type="date">` ends.
 *
 * "Expires 30 Nov" means the thirtieth is covered — and the thirtieth in THE
 * OFFICE'S OWN CLOCK. Composing the instant as `…T23:59:59Z` is wrong
 * everywhere east of UTC, which includes every REEP deployment: 23:59:59Z on
 * 30 Nov is 05:29 on 1 Dec in IST, so an admin typed 30 Nov, saved, and the
 * row read back "01 Dec 2026" — and the rule or grant ran five and a half
 * hours into a day nobody chose. Every screen that prints these dates prints
 * them in the local calendar, so the day is written through the same calendar
 * and the day that goes in is the day that comes back.
 *
 * The feature switches were fixed for this first and the grant form was not;
 * both read this one function now, so a third date field has something to
 * import rather than a string to compose.
 */

const MILLISECONDS_PER_MINUTE = 60_000;

function partsOf(calendarDay: string): [number, number, number] | null {
  const parts = calendarDay.split('-').map(Number);
  if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) {
    return null;
  }
  const [year, month, day] = parts;
  return [year, month, day];
}

/** The last millisecond of `calendarDay` on a clock `minutesEastOfUtc` ahead of
 *  UTC (IST is 330), as the ISO instant the API takes. Null for a string that
 *  is not a `YYYY-MM-DD` day. */
export function endOfDayAt(calendarDay: string, minutesEastOfUtc: number): string | null {
  const parts = partsOf(calendarDay);
  if (parts === null) {
    return null;
  }
  const [year, month, day] = parts;
  const endInUtc = Date.UTC(year, month - 1, day, 23, 59, 59, 999);
  if (Number.isNaN(endInUtc)) {
    return null;
  }
  return new Date(endInUtc - minutesEastOfUtc * MILLISECONDS_PER_MINUTE).toISOString();
}

/** The same, on this browser's clock — the offset in force at the end of THAT
 *  day, so a date across a daylight-saving change still ends at 23:59:59. */
export function endOfLocalDay(calendarDay: string): string | null {
  const parts = partsOf(calendarDay);
  if (parts === null) {
    return null;
  }
  const [year, month, day] = parts;
  const localEnd = new Date(year, month - 1, day, 23, 59, 59, 999);
  if (Number.isNaN(localEnd.getTime())) {
    return null;
  }
  return endOfDayAt(calendarDay, -localEnd.getTimezoneOffset());
}
