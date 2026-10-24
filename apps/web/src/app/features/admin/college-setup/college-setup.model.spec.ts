import {
  batchCode,
  batchDates,
  batchLabel,
  batchName,
  parseDomains,
  trackFor,
} from './college-setup.model';
import { composeBatchLabel } from '../../../core/batch-label';

/**
 * The rules "Set up a college" derives a batch from, pinned to the seeder's:
 * app/seed_catalogue.py's `batch_code`, `batch_name` and `batch_dates` produce
 * the same strings for the same inputs, so a batch made on screen and one
 * made from code read identically on the Batches screen.
 */
describe('a batch derived from the codes the office typed', () => {
  it('labels a two-year programme starting in 2026 as 2026-28', () => {
    expect(batchLabel(2026, 2)).toBe('2026-28');
    expect(batchLabel(2099, 2)).toBe('2099-01');
  });

  it('runs from 1 July of the first year to 30 June of the last', () => {
    expect(batchDates('2026-28')).toEqual({ entry: '2026-07-01', completion: '2028-06-30' });
    expect(batchDates('2025-2027')).toEqual({ entry: '2025-07-01', completion: '2027-06-30' });
  });

  it('refuses a label it cannot read rather than guessing the dates', () => {
    expect(batchDates('2026')).toBeNull();
    expect(batchDates('26-28')).toBeNull();
    expect(batchDates('2028-26')).toBeNull();
    expect(batchDates('2026-40')).toBeNull();
    expect(batchDates('')).toBeNull();
  });

  it('codes the whole path, upper-cased, skipping a missing specialization', () => {
    expect(batchCode(['1mp', 'mba', 'mba', 'fa', '2026-28'])).toBe('1MP-MBA-MBA-FA-2026-28');
    expect(batchCode(['1mp', 'mba', 'dm', null, '2026-28'])).toBe('1MP-MBA-DM-2026-28');
    expect(batchCode([' 1mp ', '', 'mba', undefined, '2026-28'])).toBe('1MP-MBA-2026-28');
  });

  it('names the batch the year and nothing else', () => {
    // A BATCH IS A YEAR. The course and the specialization are the links the
    // five POSTs set, not words inside the name — `composeBatchLabel` puts
    // them back together at read time, which is the assertion below.
    expect(batchName('2026-28')).toBe('2026-28');
    expect(batchName('  2026-28 ')).toBe('2026-28');
  });

  it('reads back as the spine and the year, from the links', () => {
    expect(composeBatchLabel('General MBA', 'Finance', batchName('2026-28'), '2026-28')).toBe(
      'General MBA - Finance \u00b7 2026-28',
    );
    // A two-year MBA in Digital Marketing IS the qualification: no
    // specialization is a real shape, not an omission.
    expect(composeBatchLabel('Digital Marketing', null, batchName('2026-28'), '2026-28')).toBe(
      'Digital Marketing \u00b7 2026-28',
    );
    // Course is optional (HIERARCHY_LEVELS), so a batch may hang at department
    // level and read as the year alone.
    expect(composeBatchLabel(null, null, batchName('2026-28'), '2026-28')).toBe('2026-28');
  });

  it('keeps the year even when the batch has a name of its own', () => {
    // A section already says which year, so the label beside it would stutter.
    expect(composeBatchLabel('General MBA', null, '2026-28 Section B', '2026-28')).toBe(
      'General MBA \u00b7 2026-28 Section B',
    );
    // A name that says nothing about the span must not swallow it.
    expect(composeBatchLabel(null, null, 'Chain Batch', '2024-26')).toBe(
      '2024-26 \u00b7 Chain Batch',
    );
  });
});

describe('the rest of the form', () => {
  it('reads domains off a comma list, lower-cased, without duplicates', () => {
    expect(parseDomains('BGSCET.ac.in, sjbit.ac.in ;@bgscet.ac.in')).toEqual([
      'bgscet.ac.in',
      'sjbit.ac.in',
    ]);
    expect(parseDomains('')).toEqual([]);
  });

  it('matches a leaf code to an enabled track exactly and case-folded', () => {
    const tracks = [
      { code: 'fa', label: 'Financial Analytics', enabled: true },
      { code: 'dm', label: 'Digital Marketing', enabled: false },
    ];
    expect(trackFor('FA', tracks)).toBe('Financial Analytics');
    expect(trackFor('fa ', tracks)).toBe('Financial Analytics');
    expect(trackFor('dm', tracks)).toBeNull();
    expect(trackFor('FIN', tracks)).toBeNull();
    expect(trackFor('', tracks)).toBeNull();
  });
});
