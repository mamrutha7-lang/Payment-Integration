/**
 * The register form's Batch box is the YEAR and nothing else (2026-09-23).
 *
 * It used to list every batch with its spine composed on — "General MBA -
 * Finance · 2026-28", "General MBA - Marketing · 2026-28" — which repeated the
 * Course and Specialization boxes directly above it, and for a student who
 * ticked two specializations it drew the same year twice. A batch IS a year;
 * which course and specialization it belongs to is already on the form.
 *
 * So the box offers each year once, and the year is turned back into a batch
 * from what the student ticked: the batch of their FIRST specialization, else
 * of the second, else a batch of the whole course, else one of the whole
 * department. Pure functions with a spec, `core/specializations.ts`'s shape.
 */

/** The fields of a hierarchy batch these decisions read. */
export interface YearBatch {
  id: string;
  name: string;
  batch_label: string;
  course_id: string | null;
  specialization_id: string | null;
  current: boolean;
}

/** "2026-28": the batch's label, or its name where a row has no label. */
export function batchYear(batch: Pick<YearBatch, 'name' | 'batch_label'>): string {
  return (batch.batch_label ?? '').trim() || (batch.name ?? '').trim();
}

export interface YearOption {
  year: string;
  /** False only when every batch of that year has ended. */
  current: boolean;
}

/** Each year once, running years first, in the order the batches came. */
export function yearOptions(batches: readonly YearBatch[]): YearOption[] {
  const byYear = new Map<string, YearOption>();
  for (const batch of batches) {
    const year = batchYear(batch);
    if (!year) continue;
    const seen = byYear.get(year);
    if (seen) seen.current = seen.current || batch.current;
    else byYear.set(year, { year, current: batch.current });
  }
  return [...byYear.values()].sort((a, z) => Number(z.current) - Number(a.current));
}

/** How well a batch fits the ticks: lower is better; `UNTICKED` is a batch
 *  of a specialization the student did not tick. */
const UNTICKED = 4;
function fit(batch: YearBatch, picks: readonly string[]): number {
  if (batch.specialization_id) {
    if (batch.specialization_id === picks[0]) return 0;
    if (picks.includes(batch.specialization_id)) return 1;
    return UNTICKED;
  }
  return batch.course_id ? 2 : 3;
}

/**
 * The batch a picked year means, given the ticked specializations — or null
 * when the year names none, or several equally and nothing ticked tells them
 * apart (the form then asks for the specialization).
 *
 * A single batch of an unticked specialization is returned: it is the only
 * batch that year, and picking it ticks its specialization, as picking a
 * batch always has.
 */
export function batchForYear(
  batches: readonly YearBatch[],
  year: string,
  picks: readonly string[],
): YearBatch | null {
  if (!year) return null;
  const candidates = batches.filter((batch) => batchYear(batch) === year);
  if (candidates.length === 0) return null;
  const best = Math.min(...candidates.map((batch) => fit(batch, picks)));
  const tied = candidates
    .filter((batch) => fit(batch, picks) === best)
    .sort((a, z) => Number(z.current) - Number(a.current));
  if (best === UNTICKED) {
    const streams = new Set(tied.map((batch) => batch.specialization_id));
    if (streams.size > 1) return null;
  }
  return tied[0];
}
