/**
 * What the Promote batch and Graduate batch dialogs are allowed to say about a
 * batch — and nothing more.
 *
 * Every field here is READ, not guessed: the counts come from the roster rows
 * the grid already holds (`GET /api/admin/students?cohort_id=`), the course,
 * specialization and "still running" flag come from the institution hierarchy
 * (`GET /api/register/hierarchy`). The facts the boards also draw — how many
 * semesters the course has, how many students have their results imported,
 * when this batch was last promoted, how many are placed — have no endpoint on
 * `main` today, so they are NOT on this interface. A field here that nothing
 * can fill is how a dialog ends up showing a number nobody computed.
 */

/** One row of the batch's stage distribution: "18 students on Excel-Adv". */
export interface StageTally {
  label: string;
  count: number;
}

export interface BatchSummary {
  batchId: string;
  /** The batch's own name, as the office wrote it: the year, "2025-27", plus
   *  anything they added to tell two sections of it apart. */
  batchName: string;
  /** That name with its spine composed back on from the links —
   *  "General MBA - Finance · 2025-27". What a heading shows. */
  batchDisplayLabel: string;
  /** The academic year label beside it: "2025-27". */
  batchLabel: string;
  departmentName: string;
  courseName: string | null;
  /** "UG" or "PG", off the batch row. */
  degreeLevel: string | null;
  specializationName: string | null;
  /** The batch's end date is still ahead. */
  isRunning: boolean;
  studentCount: number;
  studentsWithoutAMentor: number;
  /** Distinct faculty members currently holding a student in this batch. */
  facultyCount: number;
  /** "3", or "2 to 4" when the batch's students do not share one semester. */
  currentSemesterLabel: string;
  /** The semester the batch would move to — null when the batch is mixed. */
  nextSemester: number | null;
  stageTallies: StageTally[];
}
