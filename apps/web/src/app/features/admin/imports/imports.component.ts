/**
 * Data imports & criteria — spec §11, board `design/admin/DataImports.html`.
 *
 * THIS SCREEN IS LIVE END TO END NOW. Phase 4b built the five endpoints it was
 * drawn against, and this is what each control does:
 *
 *   GET  /api/register/hierarchy              the Course and Batch pickers
 *   GET  /api/admin/imports/templates/{k}.xlsx  "Download template"
 *   POST /api/admin/imports/preview           "Check file" → the preview grid
 *   POST /api/admin/imports/{id}/apply        "Import rows"
 *   GET  /api/admin/imports/{id}/errors.csv   "Error report"
 *   GET  /api/admin/imports                   "Recent imports"
 *   GET  /api/admin/criteria[?course_id=]     the criteria card
 *   POST /api/admin/criteria                  "Save"
 *   GET  /api/admin/criteria/history          "History"
 *
 * THE BROWSER STILL NEVER OPENS THE FILE, and that is the load-bearing
 * decision, not a leftover. Every verdict in the preview was reached on the
 * server against THIS batch's roster, THIS deployment's subject catalogue and
 * THIS course's semester count. A client that parsed the spreadsheet to fill
 * the grid faster would draw rows under a Check column that no validator ever
 * saw — and a screenshot of that is indistinguishable from working software.
 *
 * "CHECK FILE" IS A SEPARATE PRESS FROM "IMPORT ROWS", AND THE GAP IS THE
 * POINT. Preview writes a run and its judged lines and touches not one row of
 * `attendance_records`, `semester_results` or `subject_marks`; apply takes the
 * run id and writes the lines the reviewer has just read. That is what makes
 * "the rows I reviewed are the rows that were written" true, and it is why the
 * file is not uploaded the moment it is chosen.
 *
 * READ AND WRITTEN ARE DIFFERENT NUMBERS AND ARE NEVER PRINTED AS ONE.
 * `rows_total`, `rows_ok` and `rows_applied` are three counts: a refused line is
 * skipped, a flagged line IS written (a warning means "this overwrites what is
 * on file", which is what an import is for), and a run nobody applied has
 * `rows_applied = 0` for ever. The confirmation says how many records landed
 * across how many students, not "done".
 *
 * THE CRITERIA CARD WRITES A NEW ROW, NEVER AN EDIT. `POST /api/admin/criteria`
 * supersedes the live set at that rung and keeps the old one, because a student
 * told in March that they did not qualify must still be explicable in
 * September. The two thresholds this form does NOT show — REEP completion and
 * the core-certificate requirement — are carried over from the set being
 * replaced rather than reset, which is what `CriteriaIn`'s optional fields are
 * for, and the card says so.
 */

import { Component, ElementRef, computed, signal, viewChild } from '@angular/core';
import { AgGridAngular } from 'ag-grid-angular';
import type { GridApi, GridReadyEvent } from 'ag-grid-community';

import { environment } from '../../../../environments/environment';
import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { reepGridTheme } from '../../../shared/grid/reep-grid-theme';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';
import { CriteriaHistoryDialogComponent } from './criteria-history-dialog.component';
import {
  ACCEPTED_IMPORT_FILE_TYPES,
  CRITERIA_SOURCE_LABELS,
  DEFAULT_PREVIEW_PAGE_SIZE,
  IMPORT_DATASETS,
  MAX_UPLOAD_BYTES,
  PREVIEW_PAGE_SIZES,
  SEMESTERS,
  type BatchOption,
  type CourseOption,
  type CriteriaState,
  type HierarchyResponse,
  type ImportApplyOut,
  type ImportDatasetKind,
  type ImportPreviewOut,
  type ImportPreviewRowOut,
  type ImportRunOut,
  type ImportRunStatus,
  type PlacementCriteriaOut,
  type RecentImportRow,
} from './import-dataset';
import {
  ATTENDANCE_PREVIEW_COLUMNS,
  DEFAULT_IMPORT_COLUMN,
  MARKS_PREVIEW_COLUMNS,
  RECENT_IMPORT_COLUMNS,
} from './import-preview-grid';

const BYTES_IN_A_KILOBYTE = 1024;

/** How the history grid labels a run's status: a word AND a tone, never a
 *  colour alone. "Previewed" and "Imported" are the two states an office most
 *  needs to tell apart — one of them wrote nothing. */
const RUN_STATUS_CHIPS: Record<ImportRunStatus, { label: string; tone: string }> = {
  previewed: { label: 'Previewed', tone: 'neutral' },
  applied: { label: 'Imported', tone: 'good' },
  failed: { label: 'Could not read', tone: 'risk' },
};

/** The five thresholds this card writes. REEP completion and the core-cert
 *  requirement are deliberately absent: they are on the model, nothing on this
 *  board sets them, and `CriteriaIn` carries an omitted field over from the set
 *  being replaced rather than resetting it. */
const CRITERIA_FIELDS: readonly { key: string; label: string; step: string; suffix: string }[] = [
  { key: 'min_cgpa', label: 'Min CGPA', step: '0.1', suffix: '' },
  { key: 'max_live_backlogs', label: 'Max live backlogs', step: '1', suffix: '' },
  { key: 'min_attendance_pct', label: 'Min attendance', step: '0.5', suffix: '%' },
  { key: 'min_cert_completion_pct', label: 'Cert completion', step: '1', suffix: '%' },
  { key: 'max_gap_months', label: 'Max education gap', step: '1', suffix: ' months' },
];

/** One row of the numbered strip above the wizard. */
interface ImportWizardStep {
  readonly number: number;
  readonly label: string;
  /** What the reader has chosen for this step, or '' while it is unanswered. */
  readonly chosen: string;
  readonly state: 'done' | 'active' | 'pending';
}

/** One editable value in the placement-criteria card. */
interface PlacementCriteriaField {
  readonly key: string;
  readonly inputId: string;
  readonly label: string;
  readonly step: string;
  readonly suffix: string;
  readonly value: string;
}

function describeFileSize(sizeInBytes: number): string {
  if (sizeInBytes < BYTES_IN_A_KILOBYTE) {
    // Spelled out, so it has to agree; `kB` and `MB` below are symbols and
    // never do.
    return plural(sizeInBytes, 'byte');
  }
  const sizeInKilobytes = sizeInBytes / BYTES_IN_A_KILOBYTE;
  if (sizeInKilobytes < BYTES_IN_A_KILOBYTE) {
    return `${Math.round(sizeInKilobytes)} kB`;
  }
  const sizeInMegabytes = sizeInKilobytes / BYTES_IN_A_KILOBYTE;
  return `${sizeInMegabytes.toFixed(1)} MB`;
}

/** A timestamp in the grid, which has no access to Angular's DatePipe. */
function describeWhen(iso: string | null): string {
  if (!iso) return '—';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '—';
  return at.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

@Component({
  selector: 'app-admin-imports',
  standalone: true,
  imports: [AgGridAngular, PluralPipe, CriteriaHistoryDialogComponent],
  templateUrl: './imports.component.html',
  styleUrl: './imports.component.scss',
})
export class AdminImportsComponent {
  readonly datasets = IMPORT_DATASETS;
  readonly semesters = SEMESTERS;
  readonly previewPageSizes = PREVIEW_PAGE_SIZES;
  readonly acceptedFileTypes = ACCEPTED_IMPORT_FILE_TYPES;
  readonly gridTheme = reepGridTheme;
  readonly defaultColumn = DEFAULT_IMPORT_COLUMN;
  readonly recentImportColumns = RECENT_IMPORT_COLUMNS;
  readonly maxUploadMegabytes = MAX_UPLOAD_BYTES / BYTES_IN_A_KILOBYTE / BYTES_IN_A_KILOBYTE;

  private readonly filePicker = viewChild<ElementRef<HTMLInputElement>>('importFilePicker');

  // --- what the server said ------------------------------------------------

  readonly courses = signal<CourseOption[]>([]);
  readonly batches = signal<BatchOption[]>([]);
  /** The hierarchy request has come back, either way. Until it has, "no
   *  batches under this course" is not a fact about the institution, it is a
   *  fact about the network, and the board must not say the first. */
  readonly hierarchyLoaded = signal(false);
  readonly hierarchyError = signal('');

  readonly criteria = signal<PlacementCriteriaOut | null>(null);
  readonly criteriaState = signal<CriteriaState>('loading');
  readonly criteriaError = signal('');
  readonly criteriaFlash = signal('');
  readonly criteriaSaving = signal(false);
  readonly historyOpen = signal(false);

  /** The criteria form, as TYPED. A blank is an omitted field, which the server
   *  carries over from the set being replaced — never a zero. */
  readonly criteriaForm = signal<Record<string, string>>({});
  readonly criteriaName = signal('');
  readonly criteriaEffectiveFrom = signal('');

  /** The run this screen is looking at, and the lines it judged. */
  readonly previewRun = signal<ImportRunOut | null>(null);
  readonly previewRows = signal<ImportPreviewRowOut[]>([]);
  readonly rowsToWrite = signal(0);
  readonly previewBusy = signal(false);
  readonly previewError = signal('');

  readonly applyBusy = signal(false);
  readonly applyError = signal('');
  readonly applyFlash = signal('');

  readonly recentImports = signal<RecentImportRow[]>([]);
  readonly recentImportsLoaded = signal(false);
  readonly recentImportsError = signal('');

  // --- what the reader chose ----------------------------------------------

  readonly datasetKind = signal<ImportDatasetKind>(IMPORT_DATASETS[0].kind);
  /** Course narrows the Batch picker AND the criteria card: `GET /admin/criteria
   *  ?course_id=` resolves the chain (course row, then college, then programme),
   *  so the card shows what actually governs the students in this course rather
   *  than the widest row on the deployment. */
  readonly courseId = signal('');
  readonly batchId = signal('');
  readonly semester = signal<number | null>(null);
  readonly chosenFileName = signal('');
  readonly chosenFileSize = signal('');
  readonly wizardStatus = signal('');

  // --- the preview grid's pager -------------------------------------------

  readonly previewPageSize = signal(DEFAULT_PREVIEW_PAGE_SIZE);
  readonly previewPageIndex = signal(0);
  readonly previewPageCount = signal(1);
  private previewGrid: GridApi<ImportPreviewRowOut> | null = null;

  constructor() {
    registerReepGrid();
    void this.loadCoursesAndBatches();
    void this.loadPlacementCriteria();
    void this.loadRecentImports();
  }

  // ===================================================== what is on screen ==

  readonly datasetLabel = computed(() => {
    const chosen = this.datasets.find((dataset) => dataset.kind === this.datasetKind());
    if (!chosen) return '';
    return chosen.label;
  });

  readonly datasetFileShape = computed(() => {
    const chosen = this.datasets.find((dataset) => dataset.kind === this.datasetKind());
    if (!chosen) return '';
    return chosen.fileShape;
  });

  readonly courseLabel = computed(() => {
    const chosen = this.courses().find((course) => course.id === this.courseId());
    if (!chosen) return 'All courses';
    return chosen.name;
  });

  /** Batches of the chosen course, or every batch while no course is chosen. */
  readonly batchOptions = computed<BatchOption[]>(() => {
    const chosenCourseId = this.courseId();
    if (chosenCourseId === '') return this.batches();
    return this.batches().filter((batch) => batch.courseId === chosenCourseId);
  });

  readonly batchLabel = computed(() => {
    const chosen = this.batches().find((batch) => batch.id === this.batchId());
    if (!chosen) return '';
    return chosen.displayLabel;
  });

  readonly semesterLabel = computed(() => {
    const chosen = this.semester();
    if (chosen === null) return '';
    return String(chosen);
  });

  readonly chosenFileLabel = computed(() => {
    const name = this.chosenFileName();
    if (name === '') return 'No file chosen';
    return `${name} · ${this.chosenFileSize()}`;
  });

  readonly previewCardTitle = computed(() => {
    const run = this.previewRun();
    if (run) {
      return `Preview · ${run.filename ?? 'upload'}`;
    }
    const name = this.chosenFileName();
    if (name === '') return 'Preview';
    return `Preview · ${name}`;
  });

  readonly previewColumns = computed(() => {
    if (this.datasetKind() === 'attendance') return ATTENDANCE_PREVIEW_COLUMNS;
    return MARKS_PREVIEW_COLUMNS;
  });

  /** The board's numbered strip: dataset, batch, semester, file. */
  readonly wizardSteps = computed<ImportWizardStep[]>(() => {
    const answers = [
      { label: 'Dataset', chosen: this.datasetLabel() },
      { label: 'Batch', chosen: this.batchLabel() },
      { label: 'Semester', chosen: this.semesterLabel() },
      { label: 'File', chosen: this.chosenFileName() },
    ];
    const steps: ImportWizardStep[] = [];
    let activeStepPlaced = false;
    for (let index = 0; index < answers.length; index += 1) {
      const answer = answers[index];
      let state: ImportWizardStep['state'] = 'done';
      if (answer.chosen === '') {
        if (activeStepPlaced) {
          state = 'pending';
        } else {
          state = 'active';
          activeStepPlaced = true;
        }
      }
      steps.push({ number: index + 1, label: answer.label, chosen: answer.chosen, state });
    }
    return steps;
  });

  readonly hasAnySelection = computed(() => {
    if (this.batchId() !== '') return true;
    if (this.semester() !== null) return true;
    if (this.chosenFileName() !== '') return true;
    return this.courseId() !== '';
  });

  /** A marks file is written into `semester_results`, which is keyed on the
   *  semester, so the server refuses one without it. Attendance has no semester
   *  column at all — the value is recorded on the run as provenance and written
   *  nowhere — so it is optional there, and the card says which. */
  readonly semesterIsRequired = computed(() => this.datasetKind() === 'marks');

  /** Why "Check file" is not pressable yet, in the order a reader fills the
   *  wizard in. Null when it is. */
  readonly checkBlockedBecause = computed<string | null>(() => {
    if (this.batchId() === '') return 'Choose the batch this file is for.';
    if (this.semesterIsRequired() && this.semester() === null) {
      return 'A marks file must name the semester it is for.';
    }
    if (this.chosenFileName() === '') return 'Choose the spreadsheet to check.';
    return null;
  });

  /** "There are no batches here" is a claim about the institution and may only
   *  be made when the hierarchy actually arrived. `hierarchyLoaded` is set in a
   *  `finally`, so it is true after a FAILED request too — a failure must not
   *  be reported as an empty institution, which is exactly the invented fact
   *  the signal's own comment above disclaims. The error notice speaks then. */
  readonly hasNoBatches = computed(
    () => this.hierarchyLoaded() && this.hierarchyError() === '' && this.batchOptions().length === 0,
  );

  /** The grid is capped by the server at 1 000 lines; a bigger file is judged in
   *  full and reported in full in the error report, but only the first thousand
   *  are drawn. Saying so beats a row count that quietly disagrees with the
   *  run's own total. */
  readonly previewIsTruncated = computed(() => {
    const run = this.previewRun();
    if (!run) return false;
    return run.rows_total > this.previewRows().length;
  });

  readonly alreadyImported = computed(() => this.previewRun()?.status === 'applied');
  readonly runFailedToParse = computed(() => this.previewRun()?.status === 'failed');

  readonly flaggedRowCount = computed(() => {
    const run = this.previewRun();
    if (!run) return 0;
    return run.rows_warning + run.rows_rejected;
  });

  /** Why "Import rows" is not pressable, or null when it is. */
  readonly importBlockedBecause = computed<string | null>(() => {
    const run = this.previewRun();
    if (!run) return 'Check a file first — there is nothing judged to import.';
    if (run.status === 'failed') {
      return 'This file could not be read, so there is nothing to import.';
    }
    if (run.status === 'applied') {
      return 'This file has already been imported. Check the corrected file to import it again.';
    }
    if (this.rowsToWrite() === 0) {
      return 'Every line in this file was refused, so there is nothing to write.';
    }
    return null;
  });

  /** Why "Error report" is not pressable, or null when it is. */
  readonly reportBlockedBecause = computed<string | null>(() => {
    if (!this.previewRun()) return 'Check a file first — there is no run to report on.';
    if (this.flaggedRowCount() === 0) {
      return 'Every line passed with no warning, so the report would be empty.';
    }
    return null;
  });

  readonly criteriaCardTitle = computed(() => {
    const criteria = this.criteria();
    if (criteria === null) return 'Placement criteria';
    return `Placement criteria · ${criteria.name}`;
  });

  /** Which rung answered — course, college or programme. The chip matters:
   *  a programme-wide row and a course row can both be live, and a card that
   *  did not say which would read as the wrong rule for this course. */
  readonly criteriaRung = computed<string | null>(() => {
    const source = this.criteria()?.source;
    if (!source) return null;
    return CRITERIA_SOURCE_LABELS[source] ?? source;
  });

  /** What the Save button would write to, in words, so the office cannot set a
   *  course's gates while believing they are setting the programme's. */
  readonly criteriaTargetLabel = computed<string>(() => {
    if (this.courseId() === '') return 'the whole programme';
    return this.courseLabel();
  });

  readonly criteriaFields = computed<PlacementCriteriaField[]>(() => {
    const typed = this.criteriaForm();
    return CRITERIA_FIELDS.map((field) => ({
      key: field.key,
      inputId: `criteria-${field.key}`,
      label: field.label,
      step: field.step,
      suffix: field.suffix,
      value: typed[field.key] ?? '',
    }));
  });

  // --- the preview pager's labels -----------------------------------------

  readonly previewRowCount = computed(() => this.previewRows().length);
  readonly isFirstPreviewPage = computed(() => this.previewPageIndex() === 0);
  readonly isLastPreviewPage = computed(
    () => this.previewPageIndex() >= this.previewPageCount() - 1,
  );

  readonly previewPagerLabel = computed(() => {
    const pageCount = Math.max(this.previewPageCount(), 1);
    return `Page ${this.previewPageIndex() + 1} of ${pageCount}`;
  });

  readonly previewRangeLabel = computed(() => {
    const total = this.previewRowCount();
    if (total === 0) return 'No rows yet';
    const firstOnPage = this.previewPageIndex() * this.previewPageSize() + 1;
    const lastOnPage = Math.min(firstOnPage + this.previewPageSize() - 1, total);
    return `${firstOnPage} to ${lastOnPage} of ${total}`;
  });

  /** AG Grid's no-rows overlay. Plain sentences, because the overlay is built
   *  outside Angular and a component-scoped class would never reach it. */
  readonly previewEmptyOverlay = computed(() => {
    if (this.previewBusy()) return 'Reading the file on the server…';
    if (this.chosenFileName() === '') {
      return 'Choose a dataset, a batch, a semester and a file, then press Check file.';
    }
    return 'Press Check file. The server reads the spreadsheet against this batch and answers with these rows.';
  });

  /** AG Grid writes this overlay as HTML, so it carries only this file's own
   *  literal sentences — never a server message, which is rendered in the
   *  `role="alert"` notice above the grid instead. */
  readonly recentImportsEmptyOverlay = computed(() => {
    if (this.recentImportsError()) return 'The import history could not be read.';
    if (!this.recentImportsLoaded()) return 'Reading the import history…';
    return 'No file has been read into a batch you can see yet.';
  });

  // ================================================================ links ==

  /** The blank workbook for the chosen dataset, built from the parser's own
   *  columns so the file REEP hands out and the file REEP accepts cannot
   *  drift. A same-origin link, so the session cookie carries it. */
  templateUrl(): string {
    return `${environment.apiBase}/admin/imports/templates/${this.datasetKind()}.xlsx`;
  }

  /** Every line this run refused or flagged, as a file to fix and re-upload.
   *  The student NAME in it is withheld from a reader without the roster
   *  function; the USN they typed themselves is not. */
  errorsUrl(): string {
    const run = this.previewRun();
    if (!run) return '';
    return `${environment.apiBase}/admin/imports/${run.id}/errors.csv`;
  }

  // ============================================================== loading ==

  private async loadCoursesAndBatches(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/register/hierarchy`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      const body = (await response.json()) as HierarchyResponse;
      this.absorbHierarchy(body);
    } catch (failure) {
      this.hierarchyError.set(
        failure instanceof Error ? failure.message : 'Could not load the courses and batches.',
      );
    } finally {
      this.hierarchyLoaded.set(true);
    }
  }

  private absorbHierarchy(hierarchy: HierarchyResponse): void {
    const courses: CourseOption[] = [];
    const batches: BatchOption[] = [];
    for (const college of hierarchy.colleges) {
      for (const department of college.departments) {
        for (const course of department.courses) {
          courses.push({ id: course.id, code: course.code, name: course.name });
        }
        for (const batch of department.batches) {
          batches.push({
            id: batch.id,
            name: batch.name,
            displayLabel: batch.display_label,
            batchLabel: batch.batch_label,
            courseId: batch.course_id,
            isRunning: batch.current,
          });
        }
      }
    }
    this.courses.set(courses);
    this.batches.set(batches);
  }

  private async loadPlacementCriteria(): Promise<void> {
    this.criteriaState.set('loading');
    this.criteriaFlash.set('');
    const course = this.courseId();
    const query = course ? `?course_id=${encodeURIComponent(course)}` : '';
    try {
      const response = await fetch(`${environment.apiBase}/admin/criteria${query}`, {
        credentials: 'include',
      });
      if (response.status === 404) {
        // Nothing has been SET at any rung. The form is still offered — this is
        // the screen that sets them — but it opens empty rather than pre-filled
        // with the engine's fallbacks, which are not something the office chose
        // and must not be saved back as though they were.
        this.criteria.set(null);
        this.fillCriteriaForm(null);
        this.criteriaState.set('none');
        return;
      }
      if (response.status === 403) {
        this.criteriaState.set('refused');
        this.criteriaError.set(
          'Reading and setting the placement criteria needs the Analytics function as well as Upload spreadsheets. Ask the Main Admin to grant admin.analytics in Who can do what.',
        );
        return;
      }
      if (!response.ok) throw new Error(await this.detailOf(response));
      const criteria = (await response.json()) as PlacementCriteriaOut;
      this.criteria.set(criteria);
      this.fillCriteriaForm(criteria);
      this.criteriaState.set('ready');
    } catch (failure) {
      this.criteriaState.set('refused');
      this.criteriaError.set(
        failure instanceof Error ? failure.message : 'Could not load the placement criteria.',
      );
    }
  }

  private fillCriteriaForm(criteria: PlacementCriteriaOut | null): void {
    if (criteria === null) {
      this.criteriaForm.set(Object.fromEntries(CRITERIA_FIELDS.map((field) => [field.key, ''])));
      this.criteriaName.set('');
      this.criteriaEffectiveFrom.set('');
      return;
    }
    const values = criteria as unknown as Record<string, unknown>;
    this.criteriaForm.set(
      Object.fromEntries(
        CRITERIA_FIELDS.map((field) => {
          const held = values[field.key];
          return [field.key, held === null || held === undefined ? '' : String(held)];
        }),
      ),
    );
    this.criteriaName.set(criteria.name ?? '');
    this.criteriaEffectiveFrom.set('');
  }

  reloadPlacementCriteria(): void {
    this.criteriaError.set('');
    void this.loadPlacementCriteria();
  }

  private async loadRecentImports(): Promise<void> {
    this.recentImportsError.set('');
    try {
      const response = await fetch(`${environment.apiBase}/admin/imports`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.recentImportsError.set(await this.detailOf(response));
        return;
      }
      const runs = (await response.json()) as ImportRunOut[];
      this.recentImports.set(runs.map((run) => this.asHistoryRow(run)));
    } catch {
      this.recentImportsError.set('Could not reach the server.');
    } finally {
      this.recentImportsLoaded.set(true);
    }
  }

  private asHistoryRow(run: ImportRunOut): RecentImportRow {
    const dataset = IMPORT_DATASETS.find((option) => option.kind === run.kind);
    const chip = RUN_STATUS_CHIPS[run.status] ?? RUN_STATUS_CHIPS.previewed;
    const datasetLabel = run.semester
      ? `${dataset?.label ?? run.kind} · semester ${run.semester}`
      : (dataset?.label ?? run.kind);
    return {
      id: run.id,
      datasetLabel,
      // A run OUTLIVES its batch (`cohort_id` is SET NULL), keeping its counts.
      // "Batch deleted" is a fact; a blank cell would read as a bug.
      batchLabel: run.cohort_label ?? 'Batch deleted',
      statusLabel: chip.label,
      statusTone: chip.tone,
      rowsLabel: `${run.rows_total} read · ${run.rows_applied} written`,
      checksLabel: `${run.rows_ok} ok · ${run.rows_warning} flagged · ${run.rows_rejected} refused`,
      byName: run.by_name ?? 'Account removed',
      whenLabel: describeWhen(run.applied_at ?? run.created_at),
    };
  }

  // ================================================== what the reader does ==

  chooseDataset(kind: string): void {
    if (kind !== 'marks' && kind !== 'attendance') return;
    this.datasetKind.set(kind);
    this.wizardStatus.set('');
    // The judged lines belong to the dataset they were judged as, and the grid's
    // columns are about to change under them.
    this.forgetPreview();
  }

  chooseCourse(courseId: string): void {
    this.courseId.set(courseId);
    // A batch outside the new course would stay selected and read as this
    // import's target while no longer being offered.
    const stillOffered = this.batchOptions().some((batch) => batch.id === this.batchId());
    if (!stillOffered) {
      this.batchId.set('');
    }
    this.wizardStatus.set('');
    // The criteria card answers "what governs THIS course", so it is reread.
    void this.loadPlacementCriteria();
  }

  chooseBatch(batchId: string): void {
    this.batchId.set(batchId);
    this.wizardStatus.set('');
    this.forgetPreview();
  }

  /** Takes the select's raw value, not a number: "Choose a semester" sends the
   *  empty string, and `Number('')` is 0 — which would seat this import in a
   *  semester that does not exist rather than in none. */
  chooseSemester(value: string): void {
    if (value === '') {
      this.semester.set(null);
      return;
    }
    const chosen = Number(value);
    if (!SEMESTERS.includes(chosen)) {
      this.semester.set(null);
      return;
    }
    this.semester.set(chosen);
    this.wizardStatus.set('');
  }

  chooseFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = input.files;
    if (files === null || files.length === 0) {
      this.forgetChosenFile();
      return;
    }
    const chosen = files[0];
    this.chosenFileName.set(chosen.name);
    this.chosenFileSize.set(describeFileSize(chosen.size));
    this.forgetPreview();
    this.wizardStatus.set(`${chosen.name} is ready. Press Check file to have the server read it.`);
  }

  /** Clears the file the reader picked, and the input's own value with it —
   *  otherwise choosing the SAME file again fires no change event and the
   *  wizard silently keeps showing nothing. */
  forgetChosenFile(): void {
    const picker = this.filePicker();
    if (picker) {
      picker.nativeElement.value = '';
    }
    this.chosenFileName.set('');
    this.chosenFileSize.set('');
    this.forgetPreview();
  }

  /** The board's "New import": back to step 1 with nothing chosen. */
  startNewImport(): void {
    this.datasetKind.set(IMPORT_DATASETS[0].kind);
    this.courseId.set('');
    this.batchId.set('');
    this.semester.set(null);
    this.forgetChosenFile();
    this.wizardStatus.set('The import wizard is back at step 1, with nothing chosen.');
    void this.loadPlacementCriteria();
  }

  /** Drops the judged lines. Called whenever the thing they were judged
   *  AGAINST changes — the dataset, the batch, the file — because a grid of
   *  verdicts about a different file is worse than an empty one. */
  private forgetPreview(): void {
    this.previewRun.set(null);
    this.previewRows.set([]);
    this.rowsToWrite.set(0);
    this.previewError.set('');
    this.applyError.set('');
    this.applyFlash.set('');
  }

  // ========================================================== the preview ==

  /** Send the chosen spreadsheet to be read against the chosen batch. Nothing
   *  in a student's record is touched: this writes the run and its judged
   *  lines, which is what Import rows reads back. */
  async checkFile(): Promise<void> {
    const blocked = this.checkBlockedBecause();
    if (blocked) {
      this.previewError.set(blocked);
      return;
    }
    const file = this.filePicker()?.nativeElement.files?.[0];
    if (!file) {
      this.previewError.set('Choose the spreadsheet to check.');
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      // Refused here as well as on the server, so a wrong file is named before
      // ten megabytes go up a phone connection. The server's refusal is what
      // actually governs, and it is reported when it happens.
      this.previewError.set(`The file is larger than ${this.maxUploadMegabytes} MB.`);
      return;
    }

    this.previewBusy.set(true);
    this.previewError.set('');
    this.applyError.set('');
    this.applyFlash.set('');
    this.previewRows.set([]);
    this.previewRun.set(null);
    this.rowsToWrite.set(0);

    const form = new FormData();
    form.append('file', file);
    form.append('kind', this.datasetKind());
    form.append('cohort_id', this.batchId());
    const chosenSemester = this.semester();
    if (chosenSemester !== null) form.append('semester', String(chosenSemester));

    try {
      // NO Content-Type header: the browser sets it, with the multipart
      // boundary. Setting it by hand produces a body the server cannot parse.
      const response = await fetch(`${environment.apiBase}/admin/imports/preview`, {
        method: 'POST',
        credentials: 'include',
        body: form,
      });
      if (!response.ok) {
        this.previewError.set(await this.detailOf(response));
        // A file that could not be read AT ALL still leaves a receipt in the
        // history — the server commits the failed run before raising — so the
        // history is reread even on the failure path.
        void this.loadRecentImports();
        return;
      }
      const judged = (await response.json()) as ImportPreviewOut;
      this.previewRun.set(judged.run);
      this.previewRows.set(judged.rows);
      this.rowsToWrite.set(judged.rows_to_write);
      this.wizardStatus.set(
        `${plural(judged.run.rows_total, 'line')} read · ${judged.rows_to_write} would be written. Nothing has been saved yet.`,
      );
      void this.loadRecentImports();
    } catch {
      this.previewError.set('Could not reach the server.');
    } finally {
      this.previewBusy.set(false);
    }
  }

  /** Write the accepted lines of the run on screen. One transaction on the
   *  server; a second press is refused with 409 rather than repeated. */
  async importRows(): Promise<void> {
    const run = this.previewRun();
    const blocked = this.importBlockedBecause();
    if (!run || blocked) {
      if (blocked) this.applyError.set(blocked);
      return;
    }
    this.applyBusy.set(true);
    this.applyError.set('');
    this.applyFlash.set('');
    try {
      const response = await fetch(`${environment.apiBase}/admin/imports/${run.id}/apply`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) {
        this.applyError.set(await this.detailOf(response));
        void this.loadRecentImports();
        return;
      }
      const written = (await response.json()) as ImportApplyOut;
      this.previewRun.set(written.run);
      this.applyFlash.set(
        `${plural(written.records_written, 'record')} written across ${plural(
          written.students_touched,
          'student',
        )}, from ${plural(written.rows_applied, 'line')}.`,
      );
      void this.loadRecentImports();
    } catch {
      this.applyError.set('Could not reach the server.');
    } finally {
      this.applyBusy.set(false);
    }
  }

  // ========================================================= the criteria ==

  setCriteriaField(key: string, event: Event): void {
    const typed = (event.target as HTMLInputElement).value;
    this.criteriaForm.update((form) => ({ ...form, [key]: typed }));
    this.criteriaFlash.set('');
  }

  setCriteriaName(event: Event): void {
    this.criteriaName.set((event.target as HTMLInputElement).value);
    this.criteriaFlash.set('');
  }

  setCriteriaEffectiveFrom(event: Event): void {
    this.criteriaEffectiveFrom.set((event.target as HTMLInputElement).value);
    this.criteriaFlash.set('');
  }

  openHistory(): void {
    this.historyOpen.set(true);
  }

  closeHistory(): void {
    this.historyOpen.set(false);
  }

  /** Write a new set of gates at the rung the Course picker names. A blank
   *  field is OMITTED, and the server carries that threshold over from the set
   *  being replaced — sending 0 instead would be an explicit gate of zero. */
  async saveCriteria(): Promise<void> {
    const body: Record<string, unknown> = { name: this.criteriaName().trim() || 'Default' };
    if (this.courseId() !== '') body['course_id'] = this.courseId();
    if (this.criteriaEffectiveFrom() !== '') {
      body['effective_from'] = this.criteriaEffectiveFrom();
    }
    const typed = this.criteriaForm();
    for (const field of CRITERIA_FIELDS) {
      const raw = (typed[field.key] ?? '').trim();
      if (raw === '') continue;
      const value = Number(raw);
      if (!Number.isFinite(value)) {
        this.criteriaError.set(`${field.label} must be a number, or left blank to keep what governs these students now.`);
        return;
      }
      body[field.key] = value;
    }

    this.criteriaSaving.set(true);
    this.criteriaError.set('');
    this.criteriaFlash.set('');
    try {
      const response = await fetch(`${environment.apiBase}/admin/criteria`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        this.criteriaError.set(await this.detailOf(response));
        return;
      }
      const written = (await response.json()) as PlacementCriteriaOut;
      this.criteria.set(written);
      this.fillCriteriaForm(written);
      this.criteriaState.set('ready');
      this.criteriaFlash.set(
        `Saved for ${this.criteriaTargetLabel()}. The set it replaces is kept and marked superseded — it is in History.`,
      );
    } catch {
      this.criteriaError.set('Could not reach the server.');
    } finally {
      this.criteriaSaving.set(false);
    }
  }

  // ========================================================== the preview ==

  onPreviewGridReady(event: GridReadyEvent<ImportPreviewRowOut>): void {
    this.previewGrid = event.api;
    this.readPagerFromGrid();
  }

  onPreviewPaginationChanged(): void {
    this.readPagerFromGrid();
  }

  setPreviewPageSize(size: number): void {
    if (!PREVIEW_PAGE_SIZES.includes(size)) return;
    this.previewPageSize.set(size);
  }

  goToPreviousPreviewPage(): void {
    if (this.previewGrid === null) return;
    this.previewGrid.paginationGoToPreviousPage();
  }

  goToNextPreviewPage(): void {
    if (this.previewGrid === null) return;
    this.previewGrid.paginationGoToNextPage();
  }

  private readPagerFromGrid(): void {
    if (this.previewGrid === null) return;
    this.previewPageIndex.set(this.previewGrid.paginationGetCurrentPage());
    this.previewPageCount.set(this.previewGrid.paginationGetTotalPages());
  }

  // ============================================================== helpers ==

  selectValue(event: Event): string {
    return (event.target as HTMLSelectElement).value;
  }

  numberValue(event: Event): number {
    return Number((event.target as HTMLSelectElement).value);
  }

  /** FastAPI answers a 422 with `detail` as a LIST; rendered raw it reads
   *  "[object Object]" on the screen of whoever is trying to fix the form. */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as { detail?: unknown };
      const detail = body.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail)) {
        const messages = detail
          .map((entry) => (entry as { msg?: string }).msg)
          .filter((message): message is string => typeof message === 'string');
        if (messages.length > 0) return messages.join(' ');
      }
    } catch {
      /* not JSON — fall through to the status */
    }
    return `The request was refused (${response.status}).`;
  }
}
