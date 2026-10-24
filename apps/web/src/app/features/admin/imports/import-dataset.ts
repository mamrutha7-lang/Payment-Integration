/**
 * What a Data import is made of: the two datasets, the pickers' options, and
 * the shapes `app/routers/admin_imports.py` answers with.
 *
 * NOTHING HERE BUILDS A ROW. Every row shape below is the SERVER's — the
 * payloads of `POST /api/admin/imports/preview`, `POST /api/admin/imports/
 * {run_id}/apply` and `GET /api/admin/imports` — declared so the grids beside
 * them have real, typed columns. No function in this directory constructs one,
 * and the browser never opens the chosen spreadsheet: an import preview is what
 * the server answers after reading the file against the batch's roster, this
 * deployment's subject catalogue and the course's semester count, and a client
 * that parsed the file itself would draw rows under a Check column that no
 * validator ever saw.
 */

/** The two datasets B8.1 imports, and the only two the board offers. Spelled
 *  exactly as `app/models/data_import.py::IMPORT_KINDS` spells them, because
 *  the string is sent as `kind=` and appears in the template URL. */
export type ImportDatasetKind = 'marks' | 'attendance';

export interface ImportDatasetOption {
  readonly kind: ImportDatasetKind;
  readonly label: string;
  /** The columns the parser reads, in the parser's own words. This is a copy
   *  of what `imports_sheet.describe_shape` puts at the top of the downloadable
   *  template, so the two agree at a glance; the TEMPLATE is the authority and
   *  the button beside this sentence downloads it. */
  readonly fileShape: string;
}

export const IMPORT_DATASETS: readonly ImportDatasetOption[] = [
  {
    kind: 'marks',
    label: 'Semester marks',
    fileShape:
      'One row per student per subject: usn, subject_code, internal, external (optional: subject_name, credits, sgpa, cgpa, live_backlogs).',
  },
  {
    kind: 'attendance',
    label: 'Attendance',
    fileShape:
      'One row per student per subject: usn, subject_code, sessions_held, sessions_attended.',
  },
];

/** The widest semester this picker offers. It is `app/semester_bounds.py::
 *  DEFAULT_MAX_SEMESTER`, which is what the server falls back to when a course
 *  has not recorded its `total_semesters`. A COURSE THAT HAS RECORDED A SHORTER
 *  ONE IS REFUSED BY THE SERVER, with the sentence `semester_bounds.rejection`
 *  writes — the same sentence the roster editor refuses in — and this screen
 *  renders it. Nothing on `GET /api/register/hierarchy` carries the per-course
 *  count, so offering 1..8 and letting the server answer is the honest shape:
 *  the alternative is a picker that silently hides a semester a course really
 *  has. */
export const HIGHEST_SEMESTER = 8;

export const SEMESTERS: readonly number[] = Array.from(
  { length: HIGHEST_SEMESTER },
  (_unused, index) => index + 1,
);

/** What the office's spreadsheets are: the parser reads CSV and XLSX. */
export const ACCEPTED_IMPORT_FILE_TYPES = '.csv,.xlsx';

/** `app/imports_sheet.py::MAX_UPLOAD_BYTES`. Checked here too, so a wrong file
 *  is named before ten megabytes are pushed up a phone connection — the server
 *  refuses it either way, and its refusal is what the screen reports. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** Rows per page in the preview's status bar, as the board's pager offers. */
export const PREVIEW_PAGE_SIZES: readonly number[] = [10, 25, 50, 100];
export const DEFAULT_PREVIEW_PAGE_SIZE = 10;

/** Where a value that no endpoint reports is drawn. The same dash the roster
 *  uses, so the two screens say "not readable" the same way. */
export const NOT_READABLE = '—';

// --------------------------------------------------------- the pickers ----

export interface CourseOption {
  readonly id: string;
  readonly code: string;
  readonly name: string;
}

export interface BatchOption {
  readonly id: string;
  /** The batch itself: a YEAR. */
  readonly name: string;
  /** "General MBA - Finance · 2026-28" — what the picker shows. */
  readonly displayLabel: string;
  readonly batchLabel: string;
  readonly courseId: string | null;
  /** The batch is still running — its end date is ahead. Ended batches are
   *  listed and marked, never hidden: a late results file for a batch that
   *  finished last month is the Main Admin's call. */
  readonly isRunning: boolean;
}

// ------------------------------------- what GET /api/register/hierarchy says

export interface HierarchyCourse {
  id: string;
  code: string;
  name: string;
}

export interface HierarchyBatch {
  id: string;
  code: string;
  /** The batch itself: a YEAR, "2026-28". */
  name: string;
  batch_label: string;
  course_id: string | null;
  /** The spine and the year in one sentence, composed by the server from the
   *  batch's links: "General MBA - Finance · 2026-28". */
  display_label: string;
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

export interface HierarchyResponse {
  colleges: HierarchyCollege[];
}

// ------------------------------------------- what GET /api/admin/criteria says

export interface PlacementCriteriaOut {
  name: string;
  active: boolean;
  min_cgpa: number;
  max_live_backlogs: number;
  max_gap_months: number;
  min_attendance_pct: number;
  min_reep_completion_pct: number;
  min_cert_completion_pct: number;
  require_core_certs: boolean;
  // --- B8.2: which rung of the chain answered, and when it took effect ----
  id?: string | null;
  /** `course`, `college` or `programme`. Never `defaults`: the endpoint 404s
   *  rather than serving a set nobody chose. */
  source?: string | null;
  college_id?: string | null;
  course_id?: string | null;
  effective_from?: string | null;
}

/** One superseded or live set, as `GET /api/admin/criteria/history` answers. */
export interface PlacementCriteriaHistoryRow extends PlacementCriteriaOut {
  updated_at: string;
  created_by?: string | null;
  created_by_name?: string | null;
}

/** Where the criteria card is: reading, read, nothing set yet, or refused
 *  because this account holds `admin.imports` without `admin.analytics`. */
export type CriteriaState = 'loading' | 'ready' | 'none' | 'refused';

/** The rung a resolved set hangs on, in the office's words. */
export const CRITERIA_SOURCE_LABELS: Readonly<Record<string, string>> = {
  course: 'Set for this course',
  college: 'Set for this college',
  programme: 'Programme-wide',
};

// ----------------------------------------- what the import endpoints answer --

/** A row's verdict after the SERVER has validated it. */
export type ImportRowVerdict = 'ok' | 'warning' | 'error';

/** `app/models/data_import.py::IMPORT_STATUSES`. */
export type ImportRunStatus = 'previewed' | 'applied' | 'failed';

/** One run, as every import endpoint returns it. */
export interface ImportRunOut {
  id: string;
  kind: ImportDatasetKind;
  status: ImportRunStatus;
  college_id: string | null;
  cohort_id: string | null;
  /** The batch's label, or null when the batch has since been deleted — the
   *  run survives that, keeping its counts. */
  cohort_label: string | null;
  semester: number | null;
  filename: string | null;
  rows_total: number;
  rows_ok: number;
  rows_warning: number;
  rows_rejected: number;
  /** How many lines were WRITTEN. Zero on a run nobody applied, which is the
   *  resting state of a file somebody read and thought better of — and never
   *  the same number as `rows_ok`. */
  rows_applied: number;
  /** Why a run FAILED on the file rather than on a line. */
  error: string | null;
  by_user_id: string | null;
  by_name: string | null;
  created_at: string;
  applied_at: string | null;
}

/** One judged line. EVERY NUMBER IS NULLABLE and a missing one is drawn as a
 *  dash: a confident 0 where nothing was read tells the reviewer the file said
 *  zero. */
export interface ImportPreviewRowOut {
  line_no: number;
  verdict: ImportRowVerdict;
  /** The USN exactly as it was typed in the sheet — the whole value of the
   *  report is that it names what the operator can search for in Excel. */
  usn: string | null;
  student_id: string | null;
  student_name: string | null;
  /** What will be written, or why the line was refused. */
  message: string;
  subject_code: string | null;
  subject_name: string | null;
  credits: number | null;
  internal: number | null;
  external: number | null;
  total: number | null;
  sgpa: number | null;
  cgpa: number | null;
  live_backlogs: number | null;
  sessions_held: number | null;
  sessions_attended: number | null;
  attendance_percent: number | null;
}

export interface ImportPreviewOut {
  run: ImportRunOut;
  /** Capped by the server at 1 000; compare against `run.rows_total` to know
   *  whether the grid is showing all of them. */
  rows: ImportPreviewRowOut[];
  /** How many lines `apply` would write — ok plus warning. */
  rows_to_write: number;
}

export interface ImportApplyOut {
  run: ImportRunOut;
  rows_applied: number;
  students_touched: number;
  records_written: number;
}

/** One row of the "Recent imports" grid, reduced to what the board shows. */
export interface RecentImportRow {
  readonly id: string;
  readonly datasetLabel: string;
  readonly batchLabel: string;
  readonly statusLabel: string;
  readonly statusTone: string;
  /** Read and written — the two counts that must never be printed as one. */
  readonly rowsLabel: string;
  /** The verdict breakdown, kept in its own column so "written" is not read as
   *  "accepted": a flagged line IS written, a refused one is skipped. */
  readonly checksLabel: string;
  readonly byName: string;
  readonly whenLabel: string;
}
