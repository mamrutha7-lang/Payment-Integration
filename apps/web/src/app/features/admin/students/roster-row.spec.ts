import { batchYears, secondSpecializationOptions, type BatchOption } from './roster-row';

/**
 * The roster's Batch select lists each year ONCE, as /register does
 * (2026-10-06). It printed "General MBA - Finance · 2025-27" and five more
 * options for one year, repeating the Course and Specialization selects.
 */
describe('the roster Batch select', () => {
  const batch = (id: string, yearLabel: string, isRunning = true) => ({ id, yearLabel, isRunning });

  it('draws a year shared by six batches once, covering all six', () => {
    const six = ['hr', 'mkt', 'fin', 'ba', 'dm', 'lscm'].map((id) => batch(id, '2025-27'));
    const years = batchYears(six);
    expect(years.map((y) => y.label)).toEqual(['2025-27']);
    expect(years[0].batchIds).toEqual(['hr', 'mkt', 'fin', 'ba', 'dm', 'lscm']);
  });

  it('keeps different years, and a named section, apart in the order given', () => {
    const years = batchYears([
      batch('a', '2026-28'),
      batch('b', '2025-27'),
      batch('c', '2026-28 Section B'),
      batch('d', '2026-28'),
    ]);
    expect(years.map((y) => [y.label, y.batchIds])).toEqual([
      ['2026-28', ['a', 'd']],
      ['2025-27', ['b']],
      ['2026-28 Section B', ['c']],
    ]);
  });

  it('calls a year ended only when every batch of it has ended', () => {
    expect(batchYears([batch('a', '2023-25', false), batch('b', '2023-25', true)])[0].isRunning).toBe(true);
    expect(batchYears([batch('a', '2023-25', false)])[0].isRunning).toBe(false);
  });
});

/**
 * The edit dialog's "Second specialization" (2026-09-23): the streams of the
 * batch's course minus the batch's own, which is already the student's first
 * — the same rule `dual_specialization.refusal` holds on the server.
 */
describe('secondSpecializationOptions', () => {
  const specs = [
    { id: 'fin', name: 'Finance', code: 'FIN', courseId: 'mba' },
    { id: 'mkt', name: 'Marketing', code: 'MKT', courseId: 'mba' },
    { id: 'ds', name: 'Data Science', code: 'DS', courseId: 'mca' },
  ];
  const courses = [
    { id: 'mba', name: 'MBA', departmentId: 'mgmt' },
    { id: 'mca', name: 'MCA', departmentId: 'cs' },
  ];
  const batch = (over: Partial<BatchOption>): BatchOption =>
    ({ departmentId: 'mgmt', courseId: 'mba', specializationId: 'fin', ...over }) as BatchOption;

  it('offers the other streams of the batch course, never the batch own', () => {
    expect(secondSpecializationOptions(specs, courses, batch({}), '').map((s) => s.id)).toEqual(['mkt']);
  });

  it('falls back to the department when the batch names no course, or there is no batch', () => {
    const ids = (b: BatchOption | null, dept: string) =>
      secondSpecializationOptions(specs, courses, b, dept).map((s) => s.id);
    expect(ids(batch({ courseId: null, specializationId: null }), '')).toEqual(['fin', 'mkt']);
    expect(ids(null, 'cs')).toEqual(['ds']);
    expect(ids(null, '')).toEqual([]);
  });
});
