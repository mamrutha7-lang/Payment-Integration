import { describe, expect, it } from 'vitest';

import { deviceTodayIso, isoAfter, shiftIsoDay } from './ledger-days';

describe('shiftIsoDay', () => {
  it('steps one day in each direction', () => {
    expect(shiftIsoDay('2026-09-22', -1)).toBe('2026-09-21');
    expect(shiftIsoDay('2026-09-22', 1)).toBe('2026-09-23');
  });

  it('crosses month and year boundaries', () => {
    expect(shiftIsoDay('2026-09-30', 1)).toBe('2026-10-01');
    expect(shiftIsoDay('2026-10-01', -1)).toBe('2026-09-30');
    expect(shiftIsoDay('2026-12-31', 1)).toBe('2027-01-01');
    expect(shiftIsoDay('2027-01-01', -1)).toBe('2026-12-31');
    expect(shiftIsoDay('2028-02-28', 1)).toBe('2028-02-29');
  });

  it('is exact on the round trip that the old stepper got wrong', () => {
    // Back one and forward one lands where it started. The old code, run in
    // any positive-offset zone, went back two and then forward zero.
    const start = '2026-09-22';
    expect(shiftIsoDay(shiftIsoDay(start, -1), 1)).toBe(start);
    let day = start;
    for (let i = 0; i < 40; i++) day = shiftIsoDay(day, -1);
    expect(day).toBe('2026-08-13');
    for (let i = 0; i < 40; i++) day = shiftIsoDay(day, 1);
    expect(day).toBe(start);
  });

  it('pads single-digit months and days', () => {
    expect(shiftIsoDay('2026-01-09', 1)).toBe('2026-01-10');
    expect(shiftIsoDay('2026-01-10', -1)).toBe('2026-01-09');
  });
});

describe('deviceTodayIso', () => {
  it('formats the local date as YYYY-MM-DD', () => {
    const now = new Date();
    const expected = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, '0'),
      String(now.getDate()).padStart(2, '0'),
    ].join('-');
    expect(deviceTodayIso()).toBe(expected);
  });
});

describe('isoAfter', () => {
  it('compares calendar days as text', () => {
    expect(isoAfter('2026-09-23', '2026-09-22')).toBe(true);
    expect(isoAfter('2026-09-22', '2026-09-22')).toBe(false);
    expect(isoAfter('2026-10-01', '2026-09-30')).toBe(true);
  });
});
