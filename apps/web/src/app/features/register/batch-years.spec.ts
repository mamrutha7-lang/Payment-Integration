import { batchForYear, batchYear, yearOptions, type YearBatch } from './batch-years';

/**
 * The Batch box is the year and nothing else; these prove each year is drawn
 * once and that a picked year comes back as the batch of the stream ticked.
 */
function batch(id: string, over: Partial<YearBatch> = {}): YearBatch {
  return {
    id,
    name: '2026-28',
    batch_label: '2026-28',
    course_id: 'mba',
    specialization_id: null,
    current: true,
    ...over,
  };
}

const finance = batch('fin-26', { specialization_id: 'fin' });
const marketing = batch('mkt-26', { specialization_id: 'mkt' });
const finance24 = batch('fin-24', { specialization_id: 'fin', name: '2024-26', batch_label: '2024-26', current: false });

describe('batchYear', () => {
  it('is the label, never the course or the specialization', () => {
    expect(batchYear({ name: 'General MBA - Finance 2026-28', batch_label: '2026-28' })).toBe('2026-28');
    expect(batchYear({ name: '2026-28', batch_label: '' })).toBe('2026-28');
  });
});

describe('yearOptions', () => {
  it('draws each year once, running years first', () => {
    expect(yearOptions([finance24, finance, marketing])).toEqual([
      { year: '2026-28', current: true },
      { year: '2024-26', current: false },
    ]);
  });

  it('marks a year ended only when every batch of it has ended', () => {
    const ended = batch('old', { current: false });
    expect(yearOptions([ended, batch('live')])).toEqual([{ year: '2026-28', current: true }]);
  });
});

describe('batchForYear', () => {
  it('is the batch of the first ticked specialization', () => {
    expect(batchForYear([finance, marketing], '2026-28', ['mkt', 'fin'])?.id).toBe('mkt-26');
    expect(batchForYear([finance, marketing], '2026-28', ['fin', 'mkt'])?.id).toBe('fin-26');
  });

  it('falls back to the second tick, then the course, then the department', () => {
    expect(batchForYear([marketing], '2026-28', ['fin', 'mkt'])?.id).toBe('mkt-26');
    const course = batch('course-26');
    const department = batch('dept-26', { course_id: null });
    expect(batchForYear([department, course], '2026-28', ['fin'])?.id).toBe('course-26');
    expect(batchForYear([department], '2026-28', ['fin'])?.id).toBe('dept-26');
  });

  it('asks for the specialization when nothing ticked tells two streams apart', () => {
    expect(batchForYear([finance, marketing], '2026-28', [])).toBeNull();
  });

  it('takes the one batch of a year even before anything is ticked', () => {
    expect(batchForYear([finance, finance24], '2026-28', [])?.id).toBe('fin-26');
  });

  it('names nothing for a year no batch carries', () => {
    expect(batchForYear([finance], '2030-32', ['fin'])).toBeNull();
    expect(batchForYear([finance], '', ['fin'])).toBeNull();
  });
});
