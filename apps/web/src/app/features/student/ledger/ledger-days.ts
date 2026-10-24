/**
 * Calendar arithmetic on `YYYY-MM-DD` strings, with no timezone in it.
 *
 * WHY A FILE FOR TWO FUNCTIONS. The ledger's date stepper used to do
 * `new Date(`${day}T00:00:00`)`, add the days, and read the result back with
 * `toISOString().slice(0, 10)`. The first call parses LOCAL midnight; the
 * last one prints UTC. In India — UTC+5:30, every student this app has — local
 * midnight is 18:30 the previous day in UTC, so the printed date was one day
 * short of the arithmetic: "Previous day" jumped TWO days back, and "Next day"
 * landed on the same day it started from, every time. A student who filled
 * Monday in, opened the screen on Tuesday and pressed the back arrow saw
 * Sunday, empty, and reported that their record was gone. It never was; the
 * stepper could not reach it. `ng build` cannot see this and neither can a
 * test run in UTC, which is why the arithmetic below never touches local time
 * at all.
 */

/** `iso` shifted by `days` calendar days — "2026-09-30" + 1 is "2026-10-01",
 *  in every timezone. */
export function shiftIsoDay(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const shifted = new Date(Date.UTC(y, m - 1, d + days));
  return formatUtcDate(shifted);
}

/** The device's calendar day as `YYYY-MM-DD`, from its local clock. Used only
 *  until the server's own `today` arrives; after that the server's wins. */
export function deviceTodayIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** `a` is strictly after `b`; both `YYYY-MM-DD`, which sort as text. */
export function isoAfter(a: string, b: string): boolean {
  return a > b;
}

function formatUtcDate(date: Date): string {
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}
