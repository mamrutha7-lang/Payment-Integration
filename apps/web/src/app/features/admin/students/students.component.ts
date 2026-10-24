/**
 * Students & batches — the Main Admin's roster (spec §5, board
 * `design/admin/Students.html`).
 *
 * NO CREATE AND NO DELETE (2026-09-10, AGENTS.md). A student account is minted
 * by exactly one path — approving a registration, which records an
 * application, a reason and a reviewer and then makes the person prove their
 * mailbox — so this screen draws no "Add student" control, only a link to that
 * queue. `POST /admin/students` and `DELETE /admin/students/{id}` answer 405,
 * deliberately, and a screen that drew either would be a button that can never
 * work. The one destructive action here is `DELETE /admin/cohorts/{id}` for an
 * EMPTY batch; empty a batch by MOVING its students out.
 *
 * WHAT IS LIVE, AND ON WHICH ENDPOINT.
 *   GET    /api/admin/students?cohort_id=|unseated=&q=  the grid
 *   PATCH  /api/admin/students/{id}                     edit one student, and
 *                                                       the selection actions,
 *                                                       one student at a time
 *   POST   /api/admin/cohorts/{id}/students/bulk        a whole batch at once
 *   DELETE /api/admin/cohorts/{id}                      an EMPTY batch
 *   GET    /api/register/hierarchy                      colleges → departments →
 *                                                       courses →
 *                                                       specializations →
 *                                                       batches
 *   GET    /api/admin/mentor-load                       every faculty account
 *   GET    /api/admin/cohorts/{id}/promotion-history    what has been done to
 *                                                       THIS batch
 *
 * THE PROMOTION HISTORY CARD IS LIVE (B4.3/B4.4). It is asked per batch,
 * because that is the only shape the endpoint answers, and it has four states
 * that are four different facts: no batch chosen, reading, read and empty, and
 * REFUSED. The endpoint deliberately refuses a holder who reaches only part of
 * a batch rather than returning the part they may see — so a refusal that
 * rendered as "nothing on record" would report the opposite of the truth.
 *
 * NO READINESS, CGPA OR ATTENDANCE COLUMNS. The board drew them and
 * `GET /api/admin/students` carries none of the three — it answers identity,
 * seating and stage — so they were always a dash. A roster that fetched them
 * from Student 360 would be one request per ROW on every page. Removed on
 * 2026-09-15; they are read per student on the student's own record.
 *
 * THE SELECTION ACTIONS ARE REAL, one PATCH per student. B9.3 will make
 * "assign N selected" a single audited request; until it exists the honest
 * version of that button is the loop, which writes exactly what the row editor
 * writes through exactly the same endpoint, and reports how many landed.
 *
 * The row shapes and the grid's columns live beside this file, in
 * `roster-row.ts` and `roster-grid.ts`.
 */

import { Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { AgGridAngular } from 'ag-grid-angular';
import type { CellClickedEvent, GetRowIdParams, GridApi, GridReadyEvent } from 'ag-grid-community';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import { composeBatchLabel } from '../../../core/batch-label';
import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { reepGridTheme } from '../../../shared/grid/reep-grid-theme';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';
import type { BatchSummary, StageTally } from './batch-summary';
import {
  AdminDeleteDialogComponent,
  type DeleteOutcome,
  type DeleteTarget,
} from '../../../shared/admin-delete-dialog/admin-delete-dialog.component';
import { GraduateBatchDialogComponent } from './graduate-batch-dialog.component';
import { PromoteBatchDialogComponent } from './promote-batch-dialog.component';
import {
  DEFAULT_ROSTER_COLUMN,
  ROSTER_COLUMNS,
  type RosterGridContext,
  type RosterRowAction,
  ROSTER_ROW_SELECTION,
  ROSTER_SELECTION_COLUMN,
  TOGGLEABLE_ROSTER_COLUMNS,
} from './roster-grid';
import {
  COMFORTABLE_ROW_HEIGHT_PX,
  COMPACT_ROW_HEIGHT_PX,
  DEFAULT_PAGE_SIZE,
  EMPTY_DRAFT,
  HIGHEST_SEMESTER,
  INITIALLY_HIDDEN_COLUMN_IDS,
  NOT_READABLE,
  PAGE_SIZES,
  SEARCH_DEBOUNCE_MS,
  SEMESTERS,
  STAGES,
  escapeHtml,
  initialsOf,
  secondSpecializationOptions,
  stageLabelOf,
  trackColourOf,
  type BatchAction,
  type BatchOption,
  batchYears,
  type CourseOption,
  type DepartmentOption,
  type FacultyOption,
  type HierarchyBatch,
  type HierarchyCollege,
  type HierarchyCourse,
  type HierarchySpecialization,
  type MentorLoadApiRow,
  type OpenDialog,
  type RosterRow,
  type SelectionAction,
  type SpecializationOption,
  type StudentApiRow,
  type StudentDraft,
} from './roster-row';

/** One row of `GET /api/admin/cohorts/{id}/promotion-history`
 *  (`admin_promotion.SemesterHistoryOut`). `kind` is the vocabulary of
 *  `app/models/semester_history.py` — `promote`, `graduate`, `hold_back`,
 *  `ungraduate` — and is rendered through `PROMOTION_KIND_LABELS` rather than
 *  printed raw; an unknown kind falls back to the stored word, because a new
 *  verdict the server learned is better shown as itself than as "Other". */
interface PromotionHistoryApiRow {
  id: string;
  student_id: string;
  student_name: string;
  usn: string | null;
  from_semester: number;
  to_semester: number;
  effective_on: string;
  kind: string;
  reason: string | null;
  by_user_id: string | null;
  by_name: string | null;
  created_at: string;
}

/** Text AND colour together, never colour alone (01 §4). */
const PROMOTION_KIND_LABELS: Record<string, { label: string; tone: 'good' | 'neutral' | 'warn' }> = {
  promote: { label: 'Promoted', tone: 'good' },
  graduate: { label: 'Graduated', tone: 'good' },
  hold_back: { label: 'Held back', tone: 'warn' },
  ungraduate: { label: 'Graduation reversed', tone: 'warn' },
};

// ------------------------------------------------------------- the screen --

@Component({
  selector: 'app-admin-students',
  standalone: true,
  // RouterLink is REQUIRED for the Registrations and Mentor mapping links: a
  // `routerLink` in a standalone component that does not import it is inert
  // markup — it renders, it looks like a link, and clicking it does nothing.
  imports: [
    RouterLink,
    AgGridAngular,
    PluralPipe,
    PromoteBatchDialogComponent,
    GraduateBatchDialogComponent,
    AdminDeleteDialogComponent,
  ],
  templateUrl: './students.component.html',
  styleUrls: ['./students.component.scss', './batch-dialog.scss'],
})
export class AdminStudentsComponent {
  private readonly router = inject(Router);
  private readonly auth = inject(AuthService);

  /**
   * What the cell renderers are told about this reader.
   *
   * `canOpenDetail` is a CONVENIENCE AND NOT A PERMISSION — /admin/students/:id
   * decides for itself, and so does every endpoint behind it. What it buys is
   * that the roster does not draw a link or a View button the guard would
   * refuse.
   *
   * It is a MIRROR of /admin/students/:id's route guard, which checks
   * `admin.student_records` (2026-09-17) — the read side of the roster, split
   * off this screen's own `admin.students` so the office can grant a faculty
   * member the record without the editor. The Main Admin holds both; a
   * granted faculty member may hold either, so the two keys are read
   * separately here. A mirror that stops tracking its subject is worse than
   * no mirror: change that guard and change this line in the same edit.
   */
  readonly gridContext = computed<RosterGridContext>(() => ({
    canOpenDetail: (this.auth.session()?.capabilities ?? []).includes('admin.student_records'),
  }));

  readonly stages = STAGES;
  readonly semesters = SEMESTERS;
  readonly pageSizes = PAGE_SIZES;
  readonly gridTheme = reepGridTheme;
  readonly notReadable = NOT_READABLE;

  // --- what the server said ---------------------------------------------

  readonly apiRows = signal<StudentApiRow[] | null>(null);
  readonly batches = signal<BatchOption[]>([]);
  readonly departments = signal<DepartmentOption[]>([]);
  readonly courses = signal<CourseOption[]>([]);
  readonly specializations = signal<SpecializationOption[]>([]);
  readonly faculty = signal<FacultyOption[]>([]);

  // --- what the reader chose --------------------------------------------

  /** '' = every student, 'unseated' = no batch yet, otherwise a batch id.
   *  This is the one filter the SERVER applies, and it is also what the batch
   *  actions act on, so "what I am looking at" and "what the action touches"
   *  are one thing. */
  readonly batchFilter = signal('');
  /** The YEAR picked in the Batch select ("2025-27"), or '' (2026-10-06).
   *  `batchFilter` is the one batch it resolves to through the selects to
   *  its left; while several batches share the year, `batchFilter` stays ''
   *  and the roster is narrowed to all of them on the client. */
  readonly yearFilter = signal('');
  readonly departmentFilter = signal('');
  readonly courseFilter = signal('');
  readonly specializationFilter = signal('');
  /** '' = every student, 'active' = has signed in, 'invited' = has not. */
  readonly statusFilter = signal('');
  /** Sent to the API as `q`. */
  readonly search = signal('');
  /** Given to the grid as its quick filter, so typing narrows before the
   *  request comes back. */
  readonly quickFilter = signal('');

  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);

  /** The two supporting reads can be refused while the roster itself is not:
   *  this route is `admin.students`, but `/api/register/hierarchy` and
   *  `/api/admin/mentor-load` are their own endpoints (mentor-load requires
   *  `admin.analytics`). A silently empty picker is indistinguishable from a
   *  college with no faculty, so each failure is said once, where it bites. */
  readonly hierarchyUnavailable = signal(false);
  readonly facultyUnavailable = signal(false);

  // --- the promotion history card ----------------------------------------
  //
  // `GET /api/admin/cohorts/{id}/promotion-history` answers per BATCH, so the
  // card has three states and they are three different facts: no batch chosen
  // (there is nothing to ask about), the batch has no rows (nothing has been
  // promoted or graduated yet) and the read was refused (this account may not
  // see it). The endpoint refuses a holder who reaches only PART of a batch
  // rather than handing back the part they may see, for exactly that reason —
  // so a refusal must never render as "no promotions on record".

  readonly promotionHistory = signal<PromotionHistoryApiRow[]>([]);
  readonly promotionHistoryState = signal<'no-batch' | 'loading' | 'ready' | 'refused'>('no-batch');
  readonly promotionHistoryError = signal<string | null>(null);

  // --- the grid ----------------------------------------------------------

  private gridApi: GridApi<RosterRow> | null = null;
  readonly selectedRows = signal<RosterRow[]>([]);
  readonly pageSize = signal(DEFAULT_PAGE_SIZE);
  readonly rowHeight = signal(COMFORTABLE_ROW_HEIGHT_PX);
  readonly isCompact = computed(() => this.rowHeight() === COMPACT_ROW_HEIGHT_PX);
  readonly filteredRowCount = signal(0);
  readonly currentPage = signal(0);
  readonly totalPages = signal(0);
  readonly columnsPanelOpen = signal(false);
  readonly hiddenColumnIds = signal<string[]>([...INITIALLY_HIDDEN_COLUMN_IDS]);

  // --- dialogs -----------------------------------------------------------

  readonly openDialog = signal<OpenDialog>(null);
  readonly selectionAction = signal<SelectionAction>('mentor');
  readonly editingStudentId = signal<string | null>(null);
  readonly draft = signal<StudentDraft>({ ...EMPTY_DRAFT });
  /** The row as the editor opened it, so a save can send the fields that
   *  actually changed and nothing else. */
  private readonly openedDraft = signal<StudentDraft | null>(null);
  /** The batch-actions dialog's second click before an empty batch is removed. */
  readonly confirmBatchRemoval = signal(false);

  // drafts for the whole-batch and selection actions
  readonly moveToBatchId = signal('');
  readonly assignToFacultyId = signal('');
  readonly setStageTo = signal('EXCEL');
  readonly setSemesterTo = signal(2);

  constructor() {
    registerReepGrid();
    void this.loadEverything();
  }

  // ==================================================== derived state ====

  readonly selectedBatch = computed<BatchOption | null>(() => {
    const chosen = this.batchFilter();
    return this.batches().find((batch) => batch.id === chosen) ?? null;
  });

  /** Every roster row the server returned, dressed for the grid. */
  readonly allRows = computed<RosterRow[]>(() => {
    const rows = this.apiRows();
    if (rows === null) return [];
    const batchesById = new Map(this.batches().map((batch) => [batch.id, batch]));
    return rows.map((row) => this.toRosterRow(row, batchesById));
  });

  /** What the grid shows: the server's rows, narrowed by the filters the API
   *  has no parameter for. Every one of them reads a field already on the row. */
  readonly visibleRows = computed<RosterRow[]>(() => {
    const department = this.departmentFilter();
    const course = this.courseFilter();
    const specialization = this.specializationFilter();
    const status = this.statusFilter();
    const yearBatches =
      this.yearFilter() !== '' && this.batchFilter() === '' ? new Set(this.yearBatchIds()) : null;
    return this.allRows().filter((row) => {
      if (yearBatches !== null && (row.cohortId === null || !yearBatches.has(row.cohortId))) {
        return false;
      }
      if (department !== '' && row.departmentId !== department) return false;
      if (course !== '' && row.courseId !== course) return false;
      if (
        specialization !== '' &&
        row.specializationId !== specialization &&
        row.secondSpecializationId !== specialization
      ) {
        return false;
      }
      if (status === 'active' && row.lastLoginAt === null) return false;
      if (status === 'invited' && row.lastLoginAt !== null) return false;
      // 'removed' is narrowed by the server (`?removed=true`), not here.
      return true;
    });
  });

  readonly isLoading = computed(() => this.apiRows() === null);

  /** Main's own words for each empty case, kept exactly.
   *
   *  "Nobody is in this batch" is a fact about what the SERVER returned, so it
   *  is said only when the roster it returned is empty: a batch whose students
   *  a Status filter hid is not empty, and the Removed list is a different
   *  list from the roster. Everything else the filters hid. */
  readonly emptyMessage = computed(() => {
    if (this.search().trim() !== '') return `No student matches “${this.search().trim()}”.`;
    const nobodyReturned = this.allRows().length === 0 && this.statusFilter() !== 'removed';
    if (this.batchFilter() === 'unseated' && nobodyReturned) return 'Every student is in a batch.';
    if (this.batchFilter() !== '' && nobodyReturned) return 'Nobody is in this batch.';
    if (this.hasNarrowingFilters()) return 'No student matches these filters.';
    return 'No students yet. They arrive by approving a registration.';
  });

  /** AG Grid's no-rows overlay takes a string of HTML, so the message is
   *  escaped here — it can carry whatever the reader typed into the filter. */
  readonly emptyOverlay = computed(
    () =>
      `<span style="font-size: 13px; color: var(--muted);">${escapeHtml(this.emptyMessage())}</span>`,
  );

  readonly studentCount = computed(() => this.visibleRows().length);
  readonly seatedCount = computed(
    () => this.visibleRows().filter((row) => row.mentorUserId !== null).length,
  );
  readonly unseatedCount = computed(() => this.studentCount() - this.seatedCount());

  /**
   * THE BATCH'S OWN ROSTER, not the view of it.
   *
   * `visibleRows()` is narrowed twice — by the server, through `q`, and by the
   * four client filters — and neither narrowing reaches the writes. A bulk
   * action touches EVERY student in the batch, so a dialog that counted the
   * rows on screen would understate what it is about to do: tick Status =
   * Invited on a batch of 58 and "every action here touches all 6 students"
   * would sit above a request that moves 58. These two read `allRows()`, and
   * `rosterIsNarrowed()` below is what stops even that from lying.
   */
  readonly batchRosterCount = computed(() => this.allRows().length);
  readonly batchUnseatedCount = computed(
    () => this.allRows().filter((row) => row.mentorUserId === null).length,
  );

  /** True when what is on screen is less than what the server returned, or less
   *  than the batch holds. Every batch-level control is off while it is true,
   *  because none of them can be described honestly from a partial roster.
   *  The Removed list is not the roster at all: a batch action writes to the
   *  roster and never to a removed student, so counting that list in the
   *  dialog would describe exactly the people the action leaves alone. */
  readonly rosterIsNarrowed = computed(
    () =>
      this.search().trim() !== '' ||
      this.statusFilter() === 'removed' ||
      this.visibleRows().length !== this.allRows().length,
  );
  readonly selectedCount = computed(() => this.selectedRows().length);
  readonly hasSelection = computed(() => this.selectedCount() > 0);

  /** "3", or "2 to 4" when the students in view are not on one semester. */
  readonly semesterLabel = computed(() => {
    const semesters = [...new Set(this.visibleRows().map((row) => row.semester))].sort(
      (one, other) => one - other,
    );
    if (semesters.length === 0) return NOT_READABLE;
    if (semesters.length === 1) return `${semesters[0]}`;
    return `${semesters[0]} to ${semesters[semesters.length - 1]}`;
  });

  readonly summaryLine = computed(() => {
    // "in view" whenever the count on screen is not the whole of what was asked
    // for: the same number means two different things with a filter on.
    const students =
      plural(this.studentCount(), 'student') + (this.rosterIsNarrowed() ? ' in view' : '');
    const seated = `${this.seatedCount()} seated with a faculty member`;
    const batch = this.selectedBatch();
    if (batch !== null) {
      return `${students} in ${batch.name} · semester ${this.semesterLabel()} · ${seated}`;
    }
    if (this.batchFilter() === 'unseated') {
      return `${students} with no batch yet · ${seated}`;
    }
    return `${students} across every batch · semester ${this.semesterLabel()} · ${seated}`;
  });

  /** The batch picker only offers batches inside the chosen department, course
   *  and specialization, so the four selects narrow each other down the spine
   *  in the order the row reads: Department → Course → Specialization → Batch.
   *  Specialization was the rung that was missing — a batch hangs off one by a
   *  real link, so leaving it out offered batches the specialization filter
   *  then emptied of every row. */
  readonly batchOptions = computed<BatchOption[]>(() => {
    const department = this.departmentFilter();
    const course = this.courseFilter();
    const specialization = this.specializationFilter();
    return this.batches().filter((batch) => {
      if (department !== '' && batch.departmentId !== department) return false;
      if (course !== '' && batch.courseId !== course) return false;
      if (specialization !== '' && batch.specializationId !== specialization) return false;
      return true;
    });
  });

  readonly courseOptions = computed<CourseOption[]>(() => {
    const department = this.departmentFilter();
    if (department === '') return this.courses();
    return this.courses().filter((course) => course.departmentId === department);
  });

  /**
   * Narrowed by the course, and — when no course is picked — by the DEPARTMENT
   * above it, which it was not.
   *
   * Every rung is supposed to narrow the one below, and this was the hole:
   * with a department chosen and Course still on "All", the Specialization
   * select listed every specialization on the deployment, another college's
   * included. That was merely untidy while Specialization only filtered rows
   * already loaded. It stopped being untidy when Batch started narrowing on it
   * too — picking a foreign specialization now empties the Batch select of
   * every real option and leaves a roster reading "No student matches", which
   * looks like a deployment with no data rather than a filter that cannot
   * match.
   */
  readonly specializationOptions = computed<SpecializationOption[]>(() => {
    const course = this.courseFilter();
    if (course !== '') {
      return this.specializations().filter((s) => s.courseId === course);
    }
    const department = this.departmentFilter();
    if (department === '') return this.specializations();
    const withinDepartment = new Set(
      this.courseOptions().map((c) => c.id),
    );
    return this.specializations().filter((s) => withinDepartment.has(s.courseId));
  });

  /** Every batch except the one in view — the destinations a move offers. */
  readonly otherBatches = computed(() =>
    this.batches().filter((batch) => batch.id !== this.batchFilter()),
  );

  // The bold half of each filter pill (01 §4: faint label · bold value).

  readonly departmentFilterLabel = computed(() => {
    const chosen = this.departments().find((entry) => entry.id === this.departmentFilter());
    return chosen?.label ?? 'All';
  });

  readonly courseFilterLabel = computed(() => {
    const chosen = this.courses().find((entry) => entry.id === this.courseFilter());
    return chosen?.name ?? 'All';
  });

  /** The Batch select: each year once, as `/register` draws it. */
  readonly batchYearOptions = computed(() => batchYears(this.batchOptions()));

  /** The batches the picked year covers, inside the selects to its left. */
  readonly yearBatchIds = computed(
    () => this.batchYearOptions().find((year) => year.label === this.yearFilter())?.batchIds ?? [],
  );

  readonly batchFilterLabel = computed(() => {
    if (this.batchFilter() === 'unseated') return 'No batch yet';
    return this.yearFilter() || 'All';
  });

  readonly specializationFilterLabel = computed(() => {
    const chosen = this.specializations().find((entry) => entry.id === this.specializationFilter());
    return chosen?.name ?? 'All';
  });

  readonly statusFilterLabel = computed(() => {
    if (this.statusFilter() === 'active') return 'Active';
    if (this.statusFilter() === 'invited') return 'Invited';
    if (this.statusFilter() === 'removed') return 'Removed';
    return 'All';
  });

  /** The student the Delete dialog is open for (2026-09-16): the one being
   *  edited, as a target the shared dialog understands. */
  readonly deletingStudent = computed<DeleteTarget | null>(() => {
    if (this.openDialog() !== 'delete') return null;
    const studentId = this.editingStudentId();
    if (studentId === null) return null;
    const row = this.allRows().find((candidate) => candidate.studentId === studentId);
    if (row === undefined) return null;
    return {
      kind: 'student',
      id: row.studentId,
      name: row.name,
      subLine: [row.email, row.usn ?? 'no USN', row.batchName ?? 'no batch'].join(' · '),
    };
  });

  /** The row under the edit dialog, for its Restore / Remove footer. */
  readonly editingRow = computed<RosterRow | null>(() => {
    const studentId = this.editingStudentId();
    if (studentId === null) return null;
    return this.allRows().find((candidate) => candidate.studentId === studentId) ?? null;
  });

  /** The header's primary action names the semester it would move the batch to,
   *  and can only do that when the batch is on one semester. */
  readonly promoteButtonLabel = computed(() => {
    const summary = this.batchSummary();
    if (summary === null || summary.nextSemester === null) return 'Promote batch';
    return `Promote to semester ${summary.nextSemester}`;
  });

  readonly hasOneBatchInView = computed(() => this.batchSummary() !== null);

  /** Why the three batch buttons are off, on the buttons themselves. */
  readonly batchActionsHint = computed(() => {
    const sharing = this.yearBatchIds().length;
    if (this.selectedBatch() === null && this.yearFilter() !== '' && sharing > 1) {
      return `${sharing} batches are ${this.yearFilter()}. Pick the Course and Specialization to act on one of them.`;
    }
    if (this.selectedBatch() === null) {
      return 'Pick one batch to promote, graduate or act on it.';
    }
    if (this.rosterIsNarrowed()) {
      return (
        'Clear the quick filter and the Specialization / Status filters to act on this batch: ' +
        'a batch action writes to every student in it, not to the rows in view.'
      );
    }
    return '';
  });

  /** A batch is removable only when it is EMPTY, and "empty" can only be read
   *  off an unnarrowed roster: with a search term on, `allRows()` is the
   *  matches, so a full batch reads as empty and the screen would offer to
   *  delete it (the API answers 409, which is a refusal nobody asked for). */
  readonly canRemoveBatch = computed(
    () => this.selectedBatch() !== null && !this.rosterIsNarrowed() && this.allRows().length === 0,
  );

  readonly pagerLabel = computed(() => {
    if (this.totalPages() === 0) return 'No pages';
    return `Page ${this.currentPage() + 1} of ${this.totalPages()}`;
  });

  readonly rowRangeLabel = computed(() => {
    const total = this.filteredRowCount();
    if (total === 0) return 'no rows';
    const firstRow = this.currentPage() * this.pageSize() + 1;
    const lastRow = Math.min(firstRow + this.pageSize() - 1, total);
    return `${firstRow} to ${lastRow} of ${total}`;
  });

  readonly isFirstPage = computed(() => this.currentPage() === 0);
  readonly isLastPage = computed(() => this.currentPage() + 1 >= this.totalPages());

  /** What the two batch dialogs are told. Null when no single batch is in view:
   *  neither dialog means anything across the whole roster. */
  readonly batchSummary = computed<BatchSummary | null>(() => {
    const batch = this.selectedBatch();
    if (batch === null) return null;
    // Every number below describes the WHOLE batch, so it may only be built
    // from a whole roster. While a search or a filter is narrowing the rows,
    // the batch-level controls are disabled rather than shown a subset.
    if (this.rosterIsNarrowed()) return null;
    const rows = this.allRows();
    const semesters = [...new Set(rows.map((row) => row.semester))].sort((one, other) => one - other);
    const facultyIds = new Set(
      rows.filter((row) => row.mentorUserId !== null).map((row) => row.mentorUserId),
    );
    return {
      batchId: batch.id,
      batchName: batch.name,
      batchDisplayLabel: batch.displayLabel,
      batchLabel: batch.batchLabel,
      departmentName: batch.departmentName,
      courseName: batch.courseName,
      degreeLevel: batch.degreeLevel,
      specializationName: batch.specializationName,
      isRunning: batch.isRunning,
      studentCount: rows.length,
      studentsWithoutAMentor: rows.filter((row) => row.mentorUserId === null).length,
      facultyCount: facultyIds.size,
      currentSemesterLabel: this.semesterLabelOf(semesters),
      nextSemester: this.nextSemesterAfter(semesters),
      stageTallies: this.stageTalliesOf(rows),
    };
  });

  readonly selectionSummary = computed(() => plural(this.selectedCount(), 'student'));

  // ======================================================= grid options ====
  //
  // The columns, the renderers and the selection rules live in
  // ./roster-grid.ts; this screen only names them.

  readonly rowSelection = ROSTER_ROW_SELECTION;
  readonly selectionColumn = ROSTER_SELECTION_COLUMN;
  readonly defaultColumn = DEFAULT_ROSTER_COLUMN;
  readonly columns = ROSTER_COLUMNS;
  readonly toggleableColumns = TOGGLEABLE_ROSTER_COLUMNS;

  readonly rowId = (params: GetRowIdParams<RosterRow>): string => params.data.studentId;

  // =========================================================== loading ====

  private async loadEverything(): Promise<void> {
    await Promise.all([this.loadHierarchy(), this.loadFaculty()]);
    await this.reloadRoster();
  }

  private async loadHierarchy(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/register/hierarchy`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.hierarchyUnavailable.set(true);
        return;
      }
      const body = (await response.json()) as { colleges: HierarchyCollege[] };
      this.absorbHierarchy(body.colleges);
      this.hierarchyUnavailable.set(false);
    } catch {
      /* the grid still works without the batch names — but say so */
      this.hierarchyUnavailable.set(true);
    }
  }

  private absorbHierarchy(colleges: HierarchyCollege[]): void {
    const batches: BatchOption[] = [];
    const departments: DepartmentOption[] = [];
    const courses: CourseOption[] = [];
    const specializations: SpecializationOption[] = [];

    for (const college of colleges) {
      for (const department of college.departments) {
        departments.push({ id: department.id, label: `${college.name} · ${department.name}` });
        const courseNames = new Map<string, string>();
        for (const course of department.courses) {
          courseNames.set(course.id, course.name);
          courses.push({ id: course.id, name: course.name, departmentId: department.id });
          for (const specialization of course.specializations) {
            specializations.push({
              id: specialization.id,
              name: specialization.name,
              code: specialization.code,
              courseId: course.id,
            });
          }
        }
        for (const batch of department.batches) {
          batches.push(
            this.toBatchOption(batch, college.name, department.name, courseNames, department.courses),
          );
        }
      }
    }

    this.batches.set(batches);
    this.departments.set(departments);
    this.courses.set(courses);
    this.specializations.set(specializations);
  }

  private toBatchOption(
    batch: HierarchyBatch,
    collegeName: string,
    departmentName: string,
    courseNames: Map<string, string>,
    coursesOfDepartment: HierarchyCourse[],
  ): BatchOption {
    const specialization = this.findSpecialization(coursesOfDepartment, batch.specialization_id);
    return {
      id: batch.id,
      name: batch.name,
      displayLabel: batch.display_label,
      // The spineless form, resolved once here rather than in the template:
      // the option list re-renders on every filter change and this is the same
      // string every time. Composed rather than read off `name`, because a
      // batch the office called "Chain Batch" would otherwise lose its year.
      yearLabel: composeBatchLabel(null, null, batch.name, batch.batch_label),
      batchLabel: batch.batch_label,
      collegeName,
      departmentId: batch.department_id ?? '',
      departmentName,
      courseId: batch.course_id,
      courseName: batch.course_id === null ? null : (courseNames.get(batch.course_id) ?? null),
      specializationId: batch.specialization_id,
      specializationName: specialization === null ? null : specialization.name,
      specializationCode: specialization === null ? null : specialization.code,
      degreeLevel: batch.degree_level,
      isRunning: batch.current,
    };
  }

  private findSpecialization(
    coursesOfDepartment: HierarchyCourse[],
    specializationId: string | null,
  ): HierarchySpecialization | null {
    if (specializationId === null) return null;
    for (const course of coursesOfDepartment) {
      const match = course.specializations.find((candidate) => candidate.id === specializationId);
      if (match !== undefined) return match;
    }
    return null;
  }

  private async loadFaculty(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/mentor-load`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.facultyUnavailable.set(true);
        return;
      }
      const body = (await response.json()) as MentorLoadApiRow[];
      this.faculty.set(
        body.map((member) => ({
          userId: member.user_id,
          name: member.name,
          menteeCount: member.mentee_count,
          capacity: member.capacity,
        })),
      );
      this.facultyUnavailable.set(false);
    } catch {
      /* the assignment selects stay empty — and the dialogs say why */
      this.facultyUnavailable.set(true);
    }
  }

  async reloadRoster(): Promise<void> {
    const query = new URLSearchParams();
    if (this.batchFilter() === 'unseated') query.set('unseated', 'true');
    else if (this.batchFilter() !== '') query.set('cohort_id', this.batchFilter());
    if (this.search().trim() !== '') query.set('q', this.search().trim());
    if (this.statusFilter() === 'removed') query.set('removed', 'true');
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/students?${query.toString()}`,
        { credentials: 'include' },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      this.apiRows.set((await response.json()) as StudentApiRow[]);
      this.clearSelection();
    } catch (failure) {
      this.apiRows.set([]);
      this.error.set(failure instanceof Error ? failure.message : 'Could not load students.');
    }
  }

  /**
   * What has been done to THIS batch — `GET /admin/cohorts/{id}/promotion-history`.
   *
   * Newest first, as the server returns it. It is asked only when exactly one
   * batch is in view, because that is the only shape the endpoint answers;
   * "All batches" and "No batch yet" are not a cohort id and must not be sent
   * as one.
   */
  async loadPromotionHistory(): Promise<void> {
    const batch = this.selectedBatch();
    if (batch === null) {
      this.promotionHistory.set([]);
      this.promotionHistoryError.set(null);
      this.promotionHistoryState.set('no-batch');
      return;
    }
    this.promotionHistoryState.set('loading');
    this.promotionHistoryError.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/cohorts/${batch.id}/promotion-history`,
        { credentials: 'include' },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      this.promotionHistory.set((await response.json()) as PromotionHistoryApiRow[]);
      this.promotionHistoryState.set('ready');
    } catch (failure) {
      this.promotionHistory.set([]);
      this.promotionHistoryError.set(
        failure instanceof Error ? failure.message : 'Could not read this batch’s history.',
      );
      this.promotionHistoryState.set('refused');
    }
  }

  /**
   * A promotion or a graduation has actually been written.
   *
   * The grid and the history card were both read BEFORE it, so both are re-read
   * here. The dialog stays open on its result panel — it is showing the
   * server's own counts, which are the only record of what landed — and closes
   * itself when the reader dismisses it.
   */
  onBatchWriteCompleted(): void {
    void Promise.all([this.reloadRoster(), this.loadPromotionHistory()]);
  }

  // ======================================================= the filters ====

  setBatchFilter(batchId: string): void {
    this.batchFilter.set(batchId);
    this.confirmBatchRemoval.set(false);
    this.openDialog.set(null);
    void this.reloadRoster();
    void this.loadPromotionHistory();
  }

  /** The Batch select's change: a sentinel, or a year. */
  setBatchYear(value: string): void {
    if (value === '' || value === 'unseated') {
      this.yearFilter.set('');
      this.setBatchFilter(value);
      return;
    }
    this.yearFilter.set(value);
    this.resolveYear();
  }

  /** Turn the picked year into ONE batch when the selects to its left narrow
   *  it that far; otherwise show every batch of that year and leave the batch
   *  actions off. A year the narrower selects no longer offer is dropped. */
  private resolveYear(): void {
    const year = this.yearFilter();
    if (year === '') {
      this.setBatchFilter('');
      return;
    }
    const ids = this.yearBatchIds();
    if (ids.length === 0) this.yearFilter.set('');
    this.setBatchFilter(ids.length === 1 ? ids[0] : '');
  }

  setDepartmentFilter(departmentId: string): void {
    this.departmentFilter.set(departmentId);
    // A course or a batch outside the new department would silently show
    // nothing, so the narrower choices are cleared with it.
    this.courseFilter.set('');
    this.specializationFilter.set('');
    this.resolveYear();
  }

  setCourseFilter(courseId: string): void {
    this.courseFilter.set(courseId);
    this.specializationFilter.set('');
    this.resolveYear();
  }

  setSpecializationFilter(specializationId: string): void {
    this.specializationFilter.set(specializationId);
    // The batch list is narrowed by specialization now, so the reason above
    // reaches this rung too: a batch outside the new specialization would stay
    // selected while no longer being offered, and the reader would be looking
    // at a roster the picker no longer admits was chosen. The YEAR stays: it
    // is re-resolved against the narrower list, the way /register turns a
    // year and a specialization into one batch.
    this.resolveYear();
  }

  /** Three of the four values narrow what is drawn; REMOVED is a different
   *  list from the server (`?removed=true`, `users.deleted_at` set), so
   *  crossing into or out of it refetches. */
  setStatusFilter(status: string): void {
    const wasRemoved = this.statusFilter() === 'removed';
    this.statusFilter.set(status);
    if (wasRemoved !== (status === 'removed')) void this.reloadRoster();
  }

  private searchTimer: ReturnType<typeof setTimeout> | null = null;

  onSearchInput(event: Event): void {
    const typed = this.inputValue(event);
    this.quickFilter.set(typed);
    if (this.searchTimer !== null) clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => {
      this.search.set(typed);
      void this.reloadRoster();
    }, SEARCH_DEBOUNCE_MS);
  }

  private hasNarrowingFilters(): boolean {
    return (
      this.departmentFilter() !== '' ||
      this.courseFilter() !== '' ||
      this.specializationFilter() !== '' ||
      this.statusFilter() !== '' ||
      this.quickFilter().trim() !== ''
    );
  }

  // ========================================================== the grid ====

  onGridReady(event: GridReadyEvent<RosterRow>): void {
    this.gridApi = event.api;
    this.refreshPagerState();
  }

  onSelectionChanged(): void {
    if (this.gridApi === null) return;
    this.selectedRows.set(this.gridApi.getSelectedRows());
  }

  /** Both halves of the selection: the grid's ticks and this screen's mirror.
   *  Clearing only the mirror left a row the reload kept (its id is stable)
   *  ticked beside "Selected: 0", and ticking it again UNticked it.
   *  `deselectAll` fires selectionChanged, which sets the mirror; the explicit
   *  set is for the first load, when there is no grid yet. */
  private clearSelection(): void {
    this.gridApi?.deselectAll();
    this.selectedRows.set([]);
  }

  onPaginationChanged(): void {
    this.refreshPagerState();
  }

  private refreshPagerState(): void {
    if (this.gridApi === null) return;
    this.filteredRowCount.set(this.gridApi.paginationGetRowCount());
    this.currentPage.set(this.gridApi.paginationGetCurrentPage());
    this.totalPages.set(this.gridApi.paginationGetTotalPages());
  }

  goToPreviousPage(): void {
    this.gridApi?.paginationGoToPreviousPage();
  }

  goToNextPage(): void {
    this.gridApi?.paginationGoToNextPage();
  }

  setPageSize(size: number): void {
    this.pageSize.set(size);
  }

  toggleDensity(): void {
    const next = this.isCompact() ? COMFORTABLE_ROW_HEIGHT_PX : COMPACT_ROW_HEIGHT_PX;
    this.rowHeight.set(next);
    this.gridApi?.resetRowHeights();
  }

  toggleColumnsPanel(): void {
    this.columnsPanelOpen.update((open) => !open);
  }

  isColumnVisible(columnId: string): boolean {
    return !this.hiddenColumnIds().includes(columnId);
  }

  toggleColumn(columnId: string): void {
    const wasVisible = this.isColumnVisible(columnId);
    this.hiddenColumnIds.update((hidden) => {
      if (wasVisible) return [...hidden, columnId];
      return hidden.filter((id) => id !== columnId);
    });
    this.gridApi?.setColumnsVisible([columnId], !wasVisible);
  }

  /** The Student column's link, the View eye and the Edit pencil all arrive
   *  here. The actions cell holds two buttons, so the one that was pressed is
   *  read off its `data-action`; a click on the cell's padding does nothing,
   *  because a whole-cell click that opened the editor was one stray tap away
   *  from a form nobody asked for. */
  onCellClicked(event: CellClickedEvent<RosterRow>): void {
    const row = event.data;
    if (!row) return;
    const columnId = event.column.getColId();
    if (columnId === 'name') {
      event.event?.preventDefault();
      // Same condition the renderer drew the anchor on: without it the cell is
      // plain text and a click here would navigate to a guard that bounces.
      this.openRecord(row);
      return;
    }
    if (columnId === 'actions') {
      const target = event.event?.target as HTMLElement | null;
      const action = target?.closest<HTMLElement>('[data-action]')?.dataset['action'] as
        | RosterRowAction
        | undefined;
      if (action === 'view') this.openRecord(row);
      else if (action === 'edit') this.startEdit(row);
    }
  }

  /** Student 360 for this row — only when the reader holds its key, the same
   *  condition the renderer drew the link and the View button on. */
  openRecord(row: RosterRow): void {
    if (this.gridContext().canOpenDetail) {
      void this.router.navigate(['/admin/students', row.studentId]);
    }
  }

  // ===================================================== editing a row ====

  startEdit(row: RosterRow): void {
    this.editingStudentId.set(row.studentId);
    const opened: StudentDraft = {
      name: row.name,
      email: row.email,
      usn: row.usn ?? '',
      cohortId: row.cohortId ?? '',
      departmentId: row.departmentId ?? '',
      mentorUserId: row.mentorUserId ?? '',
      stage: row.stageKey,
      semester: row.semester,
      secondSpecializationId: row.secondSpecializationId ?? '',
    };
    this.draft.set({ ...opened });
    this.openedDraft.set(opened);
    this.openDialog.set('edit');
  }

  setDraft<Key extends keyof StudentDraft>(key: Key, value: StudentDraft[Key]): void {
    this.draft.update((draft) => ({ ...draft, [key]: value }));
    // A new batch can make the second specialization its own, or sit under
    // another course: it is cleared rather than left pointing at a stream the
    // select no longer offers (the server applies the same rule on a move).
    if (key === 'cohortId' || key === 'departmentId') {
      const second = this.draft().secondSpecializationId;
      if (second !== '' && !this.secondSpecializationChoices().some((entry) => entry.id === second)) {
        this.draft.update((draft) => ({ ...draft, secondSpecializationId: '' }));
      }
    }
  }

  /** The edit dialog's "Second specialization" options, for the batch and
   *  department the draft names now. */
  readonly secondSpecializationChoices = computed<SpecializationOption[]>(() => {
    const draft = this.draft();
    const batch = this.batches().find((entry) => entry.id === draft.cohortId) ?? null;
    return secondSpecializationOptions(this.specializations(), this.courses(), batch, draft.departmentId);
  });

  /** The first specialization the draft's batch already gives, for the
   *  sentence beside the select. */
  readonly draftBatchSpecialization = computed<string | null>(() => {
    const batch = this.batches().find((entry) => entry.id === this.draft().cohortId) ?? null;
    return batch?.specializationName ?? null;
  });

  /**
   * ONLY WHAT CHANGED, and `department_id` is the reason why.
   *
   * `PATCH /admin/students/{id}` reads `model_fields_set`, and a student's
   * department is DERIVED from their batch whenever the batch has one: sending
   * both means sending a department the client did not choose, and moving a
   * student to a batch in another department then answers 422 ("department_id
   * … contradicts the batch you chose") for an edit the admin made correctly.
   * Sending the whole form back was also silently re-asserting every other
   * field, so an edit to a name wrote seven columns into the audit row.
   */
  private changedFields(): Record<string, unknown> {
    const draft = this.draft();
    const opened = this.openedDraft();
    const body: Record<string, unknown> = {};
    if (opened === null) return body;
    if (draft.name !== opened.name) body['name'] = draft.name;
    if (draft.email !== opened.email) body['email'] = draft.email;
    if (draft.usn !== opened.usn) body['usn'] = draft.usn === '' ? null : draft.usn;
    if (draft.cohortId !== opened.cohortId) body['cohort_id'] = draft.cohortId === '' ? null : draft.cohortId;
    if (draft.departmentId !== opened.departmentId) {
      body['department_id'] = draft.departmentId === '' ? null : draft.departmentId;
    }
    if (draft.mentorUserId !== opened.mentorUserId) {
      body['mentor_user_id'] = draft.mentorUserId === '' ? null : draft.mentorUserId;
    }
    if (draft.stage !== opened.stage) body['current_stage'] = draft.stage;
    if (draft.semester !== opened.semester) body['current_semester'] = draft.semester;
    if (draft.secondSpecializationId !== opened.secondSpecializationId) {
      body['second_specialization_id'] = draft.secondSpecializationId === '' ? null : draft.secondSpecializationId;
    }
    return body;
  }

  async saveEdit(): Promise<void> {
    const studentId = this.editingStudentId();
    if (studentId === null) return;
    const body = this.changedFields();
    if (Object.keys(body).length === 0) {
      this.closeDialog();
      this.flash.set('Nothing changed.');
      return;
    }
    await this.run(async () => {
      await this.patchStudent(studentId, body);
      this.closeDialog();
      this.flash.set('Saved.');
      await this.reloadRoster();
    });
  }

  // ================================================ remove / delete ====

  /** The edit dialog's "Remove or delete…" hands the student to the shared
   *  Delete dialog (2026-09-16). The edit stays open underneath so Cancel
   *  lands back on it. */
  openDeleteDialog(): void {
    if (this.editingStudentId() === null) return;
    this.openDialog.set('delete');
  }

  cancelDeleteDialog(): void {
    this.openDialog.set('edit');
  }

  /** The dialog posted it and the server answered; `detail` is the server's
   *  own sentence. The roster is reread so the row leaves (or, after a
   *  permanent delete, is gone). */
  async onDeleteDone(outcome: DeleteOutcome): Promise<void> {
    this.closeDialog();
    this.error.set(null);
    await this.reloadRoster();
    this.flash.set(outcome.detail);
  }

  /** `POST /admin/students/{id}/restore` — back on the roster, nothing lost. */
  async restoreStudent(): Promise<void> {
    const row = this.editingRow();
    if (row === null || !row.isRemoved) return;
    await this.run(async () => {
      const response = await fetch(`${environment.apiBase}/admin/students/${row.studentId}/restore`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      const state = (await response.json()) as { detail: string };
      this.closeDialog();
      this.flash.set(state.detail);
      await this.reloadRoster();
    });
  }

  // =============================================== the ticked students ====

  openSelectionDialog(action: SelectionAction): void {
    this.selectionAction.set(action);
    this.assignToFacultyId.set('');
    this.moveToBatchId.set('');
    this.openDialog.set('selection');
  }

  /**
   * Assign or move the ticked students, ONE PATCH EACH.
   *
   * B9.3 will make this a single audited request. Until it does, the loop is
   * the honest version: every write goes through the same endpoint the row
   * editor uses, and the flash reports how many landed rather than claiming
   * the whole selection succeeded.
   */
  async applyToSelection(): Promise<void> {
    const students = this.selectedRows();
    if (students.length === 0) return;
    const action = this.selectionAction();
    const body = this.selectionPatchBody(action);
    if (body === null) return;

    await this.run(async () => {
      let written = 0;
      let failure: string | null = null;
      for (const student of students) {
        try {
          await this.patchStudent(student.studentId, body);
        } catch (refusal) {
          // The writes before this one LANDED. Throwing here would lose the
          // count and leave the grid showing the state before any of them, so
          // the loop stops, the grid is refetched, and the message says how
          // far it got — which is the whole promise this control makes.
          failure = refusal instanceof Error ? refusal.message : 'A write was refused.';
          break;
        }
        written = written + 1;
      }
      this.closeDialog();
      const outcome = `${written} of ${plural(students.length, 'student')}: ${this.describeSelection(action)}`;
      if (failure === null) this.flash.set(`${outcome}.`);
      await Promise.all([this.reloadRoster(), this.loadFaculty()]);
      if (failure !== null) this.error.set(`${outcome}, then the next was refused: ${failure}`);
    });
  }

  private selectionPatchBody(action: SelectionAction): Record<string, unknown> | null {
    if (action === 'mentor') {
      return { mentor_user_id: this.assignToFacultyId() === '' ? null : this.assignToFacultyId() };
    }
    if (this.moveToBatchId() === '') return null;
    return { cohort_id: this.moveToBatchId() };
  }

  private describeSelection(action: SelectionAction): string {
    if (action === 'mentor') {
      if (this.assignToFacultyId() === '') return 'released from their faculty member';
      const member = this.faculty().find((candidate) => candidate.userId === this.assignToFacultyId());
      return `assigned to ${member?.name ?? 'the faculty member'}`;
    }
    const batch = this.batches().find((candidate) => candidate.id === this.moveToBatchId());
    return `moved to ${batch?.name ?? 'the batch'}`;
  }

  readonly canApplyToSelection = computed(() => {
    if (!this.hasSelection()) return false;
    if (this.selectionAction() === 'move') return this.moveToBatchId() !== '';
    return true;
  });

  // ================================================= the whole batch ====

  openBatchActions(): void {
    this.moveToBatchId.set('');
    this.assignToFacultyId.set('');
    this.confirmBatchRemoval.set(false);
    this.openDialog.set('batch');
  }

  async applyBatchAction(action: BatchAction): Promise<void> {
    const batch = this.selectedBatch();
    if (batch === null) return;
    const body: Record<string, unknown> = { action };
    if (action === 'move') body['cohort_id'] = this.moveToBatchId() === '' ? null : this.moveToBatchId();
    if (action === 'mentor') body['mentor_user_id'] = this.assignToFacultyId() === '' ? null : this.assignToFacultyId();
    if (action === 'stage') body['current_stage'] = this.setStageTo();
    if (action === 'semester') body['current_semester'] = this.setSemesterTo();

    await this.run(async () => {
      const response = await fetch(
        `${environment.apiBase}/admin/cohorts/${batch.id}/students/bulk`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      const result = (await response.json()) as { affected: number };
      this.closeDialog();
      this.flash.set(
        `${plural(result.affected, 'student')}: ${this.describeBatchAction(action)}.`,
      );
      await Promise.all([this.reloadRoster(), this.loadFaculty()]);
    });
  }

  private describeBatchAction(action: BatchAction): string {
    if (action === 'move') {
      const batch = this.batches().find((candidate) => candidate.id === this.moveToBatchId());
      return `moved to ${batch?.name ?? 'the batch'}`;
    }
    if (action === 'mentor') {
      if (this.assignToFacultyId() === '') return 'released from their faculty member';
      const member = this.faculty().find((candidate) => candidate.userId === this.assignToFacultyId());
      return `assigned to ${member?.name ?? 'the faculty member'}`;
    }
    if (action === 'stage') return `set to ${stageLabelOf(this.setStageTo())}`;
    return `set to semester ${this.setSemesterTo()}`;
  }

  async removeEmptyBatch(): Promise<void> {
    const batch = this.selectedBatch();
    if (batch === null) return;
    await this.run(async () => {
      const response = await fetch(`${environment.apiBase}/admin/cohorts/${batch.id}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      this.closeDialog();
      this.confirmBatchRemoval.set(false);
      this.flash.set(`Batch removed: ${batch.name}.`);
      this.batchFilter.set('');
      this.yearFilter.set('');
      await Promise.all([this.loadHierarchy(), this.reloadRoster()]);
    });
  }

  // ============================================================ dialogs ====

  openPromoteDialog(): void {
    this.openDialog.set('promote');
  }

  openGraduateDialog(): void {
    this.openDialog.set('graduate');
  }

  closeDialog(): void {
    this.openDialog.set(null);
    this.editingStudentId.set(null);
    this.openedDraft.set(null);
    this.confirmBatchRemoval.set(false);
  }

  // ============================================================ helpers ====

  /** A history row's verdict as words AND a tone, never a colour alone. An
   *  unrecognised `kind` is printed as the server stored it rather than being
   *  flattened into "Other": a verdict this client has not learned yet is
   *  still a real one. */
  promotionKind(kind: string): { label: string; tone: 'good' | 'neutral' | 'warn' } {
    return PROMOTION_KIND_LABELS[kind] ?? { label: kind, tone: 'neutral' };
  }

  /** "semester 3 → 4", or "semester 6" when a move does not change it —
   *  which is what a graduation and a reversal both look like. */
  promotionMove(row: PromotionHistoryApiRow): string {
    if (row.from_semester === row.to_semester) return `semester ${row.to_semester}`;
    return `semester ${row.from_semester} → ${row.to_semester}`;
  }

  /** Who ordered it. NULL is not "nobody" — it is an actor whose account has
   *  since been removed, or a row written before the column was filled — so it
   *  says that rather than leaving a blank the reader has to interpret. */
  promotionActor(row: PromotionHistoryApiRow): string {
    return row.by_name ?? 'Actor no longer on the roster';
  }

  when(stamp: string): string {
    return new Date(stamp).toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
  }

  /** Read a control's value without reaching for `any` in the template. */
  inputValue(event: Event): string {
    const target = event.target as HTMLInputElement;
    return target.value;
  }

  selectValue(event: Event): string {
    const target = event.target as HTMLSelectElement;
    return target.value;
  }

  numberValue(event: Event): number {
    return Number(this.selectValue(event));
  }

  private toRosterRow(row: StudentApiRow, batchesById: Map<string, BatchOption>): RosterRow {
    const batch = row.cohort_id === null ? null : (batchesById.get(row.cohort_id) ?? null);
    const hasSignedIn = row.last_login_at !== null;
    return {
      studentId: row.student_id,
      userId: row.user_id,
      name: row.name,
      email: row.email,
      initials: initialsOf(row.name),
      usn: row.usn,
      cohortId: row.cohort_id,
      batchName: row.batch,
      departmentId: row.department_id,
      departmentName: row.department,
      courseId: batch === null ? null : batch.courseId,
      specializationId: batch === null ? null : batch.specializationId,
      specializationCode: batch === null ? null : batch.specializationCode,
      specializationColour: trackColourOf(batch === null ? null : batch.specializationCode),
      ...this.secondSpecializationOf(row),
      semester: row.current_semester,
      stageKey: row.current_stage,
      stageLabel: stageLabelOf(row.current_stage),
      mentorUserId: row.mentor_user_id,
      mentorName: row.mentor_name,
      statusLabel: row.deleted_at !== null ? 'Removed' : hasSignedIn ? 'Active' : 'Invited',
      statusTone: row.deleted_at !== null ? 'risk' : hasSignedIn ? 'good' : 'warn',
      lastLoginAt: row.last_login_at,
      isRemoved: row.deleted_at !== null,
      deletedAt: row.deleted_at,
      deleteReason: row.delete_reason,
    };
  }

  /** The second of a dual specialization, as the grid's second chip. The
   *  code comes from the catalogue the filters already loaded; a stream the
   *  catalogue does not list (archived since) prints its name. */
  private secondSpecializationOf(
    row: StudentApiRow,
  ): Pick<RosterRow, 'secondSpecializationId' | 'secondSpecializationCode' | 'secondSpecializationColour'> {
    const id = row.second_specialization_id ?? null;
    if (id === null) {
      return { secondSpecializationId: null, secondSpecializationCode: null, secondSpecializationColour: trackColourOf(null) };
    }
    const known = this.specializations().find((entry) => entry.id === id);
    const code = known?.code ?? row.second_specialization ?? null;
    return { secondSpecializationId: id, secondSpecializationCode: code, secondSpecializationColour: trackColourOf(code) };
  }

  private semesterLabelOf(semesters: number[]): string {
    if (semesters.length === 0) return NOT_READABLE;
    if (semesters.length === 1) return `${semesters[0]}`;
    return `${semesters[0]} to ${semesters[semesters.length - 1]}`;
  }

  private nextSemesterAfter(semesters: number[]): number | null {
    if (semesters.length !== 1) return null;
    const semester = semesters[0];
    if (semester >= HIGHEST_SEMESTER) return null;
    return semester + 1;
  }

  private stageTalliesOf(rows: RosterRow[]): StageTally[] {
    const tallies: StageTally[] = [];
    for (const stage of STAGES) {
      const count = rows.filter((row) => row.stageKey === stage.key).length;
      if (count > 0) tallies.push({ label: stage.label, count });
    }
    return tallies;
  }

  private async patchStudent(studentId: string, body: Record<string, unknown>): Promise<void> {
    const response = await fetch(`${environment.apiBase}/admin/students/${studentId}`, {
      method: 'PATCH',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await this.detailOf(response));
  }

  private async run(work: () => Promise<void>): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      await work();
    } catch (failure) {
      this.error.set(failure instanceof Error ? failure.message : 'Something went wrong.');
    } finally {
      this.busy.set(false);
    }
  }

  /** FastAPI answers a schema error with `detail` as a LIST; rendered raw it
   *  reads "[object Object]". Its refusals name the numbers, so the server's
   *  own sentence is kept wherever there is one. */
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
