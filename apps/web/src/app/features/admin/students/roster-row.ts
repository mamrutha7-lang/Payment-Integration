/**
 * The roster row, and everything it is made of.
 *
 * One place for the three API payloads this screen reads — the roster itself
 * (`AdminStudentOut`), the institution hierarchy the register form also uses,
 * and the faculty list — and for the row the grid draws from them. The screen
 * component (`students.component.ts`) holds the behaviour; the shapes and the
 * small pure functions that dress them live here, so a reader chasing "what is
 * on a row" does not have to read a screen to find out.
 */

// ----------------------------------------------------------------- rules --

/** The REEP programme stages, as the office says them. */
export const STAGES: { key: string; label: string }[] = [
  { key: 'REBOOT', label: 'Reboot' },
  { key: 'EXCEL', label: 'Excel' },
  { key: 'EXCEL_ADVANCED', label: 'Excel-Adv' },
  { key: 'ELEVATE', label: 'Elevate' },
];

/** `AdminStudentPatch.current_semester` is bounded 1..8 on the API. */
export const HIGHEST_SEMESTER = 8;
export const SEMESTERS: number[] = Array.from({ length: HIGHEST_SEMESTER }, (_unused, index) => index + 1);

export const PAGE_SIZES = [10, 25, 50, 100];
export const DEFAULT_PAGE_SIZE = 10;

/** Rows per page in the comfortable and the compact density (01 §4). */
export const COMFORTABLE_ROW_HEIGHT_PX = 40;
export const COMPACT_ROW_HEIGHT_PX = 32;

/** Long enough that typing does not fire a request per keystroke, short enough
 *  that the roster has caught up before the reader looks away. */
export const SEARCH_DEBOUNCE_MS = 300;

/**
 * The categorical palette is FIXED PER TRACK (01 §1): FIN = 1, HR = 2,
 * MKT = 3, BA = 4. A specialization code the catalogue has not mapped gets the
 * sequential ramp's light end rather than a fifth colour, so an unmapped track
 * reads as "not one of the four" instead of as a track of its own.
 */
export const TRACK_COLOURS: { prefix: string; colour: string }[] = [
  { prefix: 'FIN', colour: 'var(--cat-1)' },
  { prefix: 'HR', colour: 'var(--cat-2)' },
  { prefix: 'MKT', colour: 'var(--cat-3)' },
  { prefix: 'DM', colour: 'var(--cat-3)' },
  { prefix: 'BA', colour: 'var(--cat-4)' },
];
export const UNMAPPED_TRACK_COLOUR = 'var(--seq-light)';

/** Batch is on the grid but off by default: with a batch selected every row
 *  carries the same one, and the rail card already names it. */
export const INITIALLY_HIDDEN_COLUMN_IDS = ['batch'];

/** What an unreadable cell says. Never a zero: a missing number and a zero
 *  mean opposite things to the office. */
export const NOT_READABLE = '—';

// ------------------------------------------------------- the API payloads --

/** `AdminStudentOut` in `app/routers/admin_students.py`. */
export interface StudentApiRow {
  student_id: string;
  user_id: string;
  name: string;
  email: string;
  usn: string | null;
  cohort_id: string | null;
  batch: string | null;
  department: string | null;
  department_id: string | null;
  mentor_id: string | null;
  mentor_user_id: string | null;
  mentor_name: string | null;
  current_stage: string;
  current_semester: number;
  /** The other half of a dual specialization (2026-09-23). The first is the
   *  batch's own; this is the second, null for a student who opted for one. */
  second_specialization_id?: string | null;
  second_specialization?: string | null;
  enrolled_at: string;
  last_login_at: string | null;
  /** REMOVED from the roster (2026-09-16): null on every row the default list
   *  returns; set on the rows `?removed=true` lists, for Restore. */
  deleted_at: string | null;
  delete_reason: string | null;
}

/** `PublicHierarchyOut` in `app/routers/registration.py`. */
export interface HierarchySpecialization {
  id: string;
  code: string;
  name: string;
}
export interface HierarchyCourse {
  id: string;
  code: string;
  name: string;
  specializations: HierarchySpecialization[];
}
export interface HierarchyBatch {
  id: string;
  code: string;
  /** The batch itself: a YEAR, "2026-28". */
  name: string;
  batch_label: string;
  department_id: string | null;
  course_id: string | null;
  specialization_id: string | null;
  course_name: string | null;
  specialization_name: string | null;
  /** The spine and the year in one sentence, composed by the server from the
   *  links above: "General MBA - Finance · 2026-28". Render this, never
   *  `name` beside `batch_label` — they are the same string now. */
  display_label: string;
  degree_level: string;
  current: boolean;
}
export interface HierarchyDepartment {
  id: string;
  code: string;
  name: string;
  courses: HierarchyCourse[];
  batches: HierarchyBatch[];
}
export interface HierarchyCollege {
  id: string;
  code: string;
  name: string;
  departments: HierarchyDepartment[];
}

/** `MentorLoadOut` in `app/routers/console.py`. */
export interface MentorLoadApiRow {
  user_id: string;
  name: string;
  mentor_id: string | null;
  department: string | null;
  capacity: number;
  mentee_count: number;
}

// ------------------------------------------------- what the screen holds --

export interface BatchOption {
  id: string;
  /** The batch itself: a YEAR. */
  name: string;
  /** "General MBA - Finance · 2026-28" — the spine composed back on, for the
   *  places that name a batch with no Course and Specialization select beside
   *  them: the two student dialogs and the batch-actions title. */
  displayLabel: string;
  /**
   * The same batch with NO spine — "2026-28", "2026-28 Section B", or
   * "2024-26 · Chain Batch" where the office's own name is not a span.
   *
   * What the filter row's Batch select draws, because Course and
   * Specialization are their own selects two places to its left: printing the
   * spine here spelled the same two facts twice in one row, and did it in the
   * one control where the year is the whole of what is being picked.
   * `composeBatchLabel` with both spine arguments null, so the YEAR IS NEVER
   * DROPPED — a batch whose name the parser cannot read keeps its label beside
   * it rather than being reduced to "Chain Batch", and two batches of one
   * course stay distinguishable.
   */
  yearLabel: string;
  batchLabel: string;
  collegeName: string;
  departmentId: string;
  departmentName: string;
  courseId: string | null;
  courseName: string | null;
  specializationId: string | null;
  specializationName: string | null;
  specializationCode: string | null;
  degreeLevel: string;
  isRunning: boolean;
}

export interface DepartmentOption {
  id: string;
  label: string;
}

export interface CourseOption {
  id: string;
  name: string;
  departmentId: string;
}

export interface SpecializationOption {
  id: string;
  name: string;
  code: string;
  courseId: string;
}

export interface FacultyOption {
  userId: string;
  name: string;
  menteeCount: number;
  capacity: number;
}

/** One row of the grid: the roster row plus everything the cells draw, resolved
 *  once here rather than in a renderer that runs on every repaint. */
export interface RosterRow {
  studentId: string;
  userId: string;
  name: string;
  email: string;
  initials: string;
  usn: string | null;
  cohortId: string | null;
  batchName: string | null;
  departmentId: string | null;
  departmentName: string | null;
  courseId: string | null;
  specializationId: string | null;
  specializationCode: string | null;
  specializationColour: string;
  /** The second of a dual specialization, drawn as a second chip. */
  secondSpecializationId: string | null;
  secondSpecializationCode: string | null;
  secondSpecializationColour: string;
  semester: number;
  stageKey: string;
  stageLabel: string;
  mentorUserId: string | null;
  mentorName: string | null;
  statusLabel: string;
  statusTone: string;
  lastLoginAt: string | null;
  /** Off the roster, rows kept (2026-09-16). Only ever true on the Removed list. */
  isRemoved: boolean;
  deletedAt: string | null;
  deleteReason: string | null;
}

/** The edit form behind the pencil on a row. */
export interface StudentDraft {
  name: string;
  email: string;
  usn: string;
  cohortId: string;
  departmentId: string;
  mentorUserId: string;
  stage: string;
  semester: number;
  /** The second specialization of a dual; '' for none. */
  secondSpecializationId: string;
}

export const EMPTY_DRAFT: StudentDraft = {
  name: '',
  email: '',
  usn: '',
  cohortId: '',
  departmentId: '',
  mentorUserId: '',
  stage: 'REBOOT',
  semester: 1,
  secondSpecializationId: '',
};

/**
 * What the edit dialog offers as the SECOND half of a dual specialization
 * (2026-09-23): the streams of the batch's course, minus the batch's own —
 * which is already the student's first. A batch with no course (or no batch)
 * offers the streams of the student's department instead. The same three
 * rules `dual_specialization.refusal` applies on the server, so nothing
 * offered here is refused there.
 */
export function secondSpecializationOptions(
  specializations: readonly SpecializationOption[],
  courses: readonly CourseOption[],
  batch: BatchOption | null,
  departmentId: string,
): SpecializationOption[] {
  if (batch !== null && batch.courseId !== null) {
    return specializations.filter((s) => s.courseId === batch.courseId && s.id !== batch.specializationId);
  }
  const department = batch?.departmentId || departmentId;
  if (!department) return [];
  const inDepartment = new Set(courses.filter((c) => c.departmentId === department).map((c) => c.id));
  return specializations.filter((s) => inDepartment.has(s.courseId) && s.id !== batch?.specializationId);
}

/** The whole-batch actions the bulk endpoint performs. */
export type BatchAction = 'move' | 'mentor' | 'stage' | 'semester';

/** Which dialog is open. One at a time, by construction. */
export type OpenDialog = 'promote' | 'graduate' | 'edit' | 'batch' | 'selection' | 'delete' | null;

/** What the selection dialog is about to do to the ticked students. */
export type SelectionAction = 'mentor' | 'move';

// ----------------------------------------------------------------- helpers --

/** A student's name and email are somebody else's text, and the grid's cell
 *  renderers build HTML by hand. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter((word) => word.length > 0);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

export function stageLabelOf(stageKey: string): string {
  const stage = STAGES.find((candidate) => candidate.key === stageKey);
  if (stage === undefined) return stageKey;
  return stage.label;
}

export function trackColourOf(specializationCode: string | null): string {
  if (specializationCode === null) return UNMAPPED_TRACK_COLOUR;
  const code = specializationCode.toUpperCase();
  const track = TRACK_COLOURS.find((candidate) => code.startsWith(candidate.prefix));
  if (track === undefined) return UNMAPPED_TRACK_COLOUR;
  return track.colour;
}

/** One entry of the roster's Batch select: a YEAR, and the batches it covers. */
export interface BatchYear {
  /** "2025-27", or "2025-27 · Section B" for a batch the office named. */
  label: string;
  batchIds: string[];
  /** False only when every batch of that year has ended. */
  isRunning: boolean;
}

/**
 * The roster's Batch select, drawn the way `/register` draws it: each year
 * ONCE (2026-10-06).
 *
 * It used to print one option per batch with the course and specialization
 * spelled in front ("General MBA - Finance · 2025-27"), which repeated the
 * Course and Specialization selects beside it and listed one year six times.
 * A batch IS a year; WHICH course and specialization is what the selects to
 * the left say. So a year here may cover several batches, and the component
 * resolves it to ONE only when the selects above narrow it that far -- which
 * is what the batch actions need, because each one writes to exactly one
 * batch. Keyed on `yearLabel`, so two sections of one year stay two options.
 */
export function batchYears(
  batches: readonly Pick<BatchOption, 'id' | 'yearLabel' | 'isRunning'>[],
): BatchYear[] {
  const byLabel = new Map<string, BatchYear>();
  for (const batch of batches) {
    const entry = byLabel.get(batch.yearLabel);
    if (entry === undefined) {
      byLabel.set(batch.yearLabel, {
        label: batch.yearLabel,
        batchIds: [batch.id],
        isRunning: batch.isRunning,
      });
    } else {
      entry.batchIds.push(batch.id);
      entry.isRunning ||= batch.isRunning;
    }
  }
  return [...byLabel.values()];
}
