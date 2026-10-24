import { describe, expect, it } from 'vitest';

import { endOfDayAt, endOfLocalDay } from './calendar-day';

/** The calendar day an instant falls on in a named zone — how a browser set to
 *  that zone prints it on the grant list, whatever zone this runner is in. */
function dayIn(timeZone: string, isoInstant: string): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(isoInstant));
}

const IST = 330;

describe('endOfDayAt', () => {
  it('ends a typed day at 23:59:59.999 on the office clock in India', () => {
    expect(endOfDayAt('2026-11-30', IST)).toBe('2026-11-30T18:29:59.999Z');
  });

  it('reads back in India as the day that was typed', () => {
    const typed = '2026-11-30';
    const sent = endOfDayAt(typed, IST);
    expect(sent).not.toBeNull();
    expect(dayIn('Asia/Kolkata', sent as string)).toBe(typed);
  });

  it('is the fix for `…T23:59:59Z`, which India reads as the next day', () => {
    // What the grant form used to send, for the same typed day.
    expect(dayIn('Asia/Kolkata', '2026-11-30T23:59:59Z')).toBe('2026-12-01');
  });

  it('holds west of UTC and on UTC itself', () => {
    expect(endOfDayAt('2026-11-30', -300)).toBe('2026-12-01T04:59:59.999Z');
    expect(dayIn('America/New_York', endOfDayAt('2026-11-30', -300) as string)).toBe('2026-11-30');
    expect(endOfDayAt('2026-11-30', 0)).toBe('2026-11-30T23:59:59.999Z');
  });

  it('crosses a month and a year the way the calendar does', () => {
    expect(dayIn('Asia/Kolkata', endOfDayAt('2026-12-31', IST) as string)).toBe('2026-12-31');
    expect(dayIn('Asia/Kolkata', endOfDayAt('2027-01-01', IST) as string)).toBe('2027-01-01');
  });

  it('refuses a value that is not a day', () => {
    expect(endOfDayAt('', IST)).toBeNull();
    expect(endOfDayAt('30 Nov 2026', IST)).toBeNull();
    expect(endOfDayAt('2026-11', IST)).toBeNull();
  });
});

describe('endOfLocalDay', () => {
  it("is the end of the typed day on this runner's own clock", () => {
    const sent = endOfLocalDay('2026-11-30');
    expect(sent).not.toBeNull();
    const when = new Date(sent as string);
    expect([when.getFullYear(), when.getMonth() + 1, when.getDate()]).toEqual([2026, 11, 30]);
    expect([when.getHours(), when.getMinutes(), when.getSeconds()]).toEqual([23, 59, 59]);
  });

  it('agrees with endOfDayAt at the offset in force on that day', () => {
    const offset = -new Date(2026, 10, 30, 23, 59, 59).getTimezoneOffset();
    expect(endOfLocalDay('2026-11-30')).toBe(endOfDayAt('2026-11-30', offset));
  });

  it('sends nothing for an empty field', () => {
    expect(endOfLocalDay('')).toBeNull();
  });
});
