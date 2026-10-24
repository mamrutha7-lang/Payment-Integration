/**
 * Faculty directory — the Main Admin's roster of staff accounts (spec §7,
 * board `design/admin/Faculty.html`).
 *
 * WHAT IS LIVE, AND ON WHICH ENDPOINT.
 *   GET   /api/admin/faculty                        the grid: every MENTOR-role
 *                                                   account, its designation,
 *                                                   where it is filed and
 *                                                   whether it is disabled
 *   GET   /api/admin/departments                    the College / Department
 *                                                   filters and the drawer's
 *                                                   Department picker
 *   GET   /api/admin/mentor-load                    the mentor function and the
 *                                                   mentee count per account
 *   PATCH /api/admin/faculty/{id}                   name, email, designation and
 *                                                   department (B3.5)
 *   POST  /api/admin/users/{id}/activation-link     mint (or re-mint) the
 *                                                   activation link and show it
 *   POST  /api/admin/users/{id}/disable             offboard, with a reason
 *   POST  /api/admin/users/{id}/enable              within 90 days of that
 *   POST  /api/admin/users/{id}/sign-out-everywhere retire every session
 *
 * WHAT IS NOT HERE. "Grant function" (a disabled button pointing at
 * Governance) and the Sign-in column (always a dash: no admin endpoint reads
 * another account's sign-ins) were drawn for board fidelity and removed on
 * 2026-09-15 — a control that can never work is the first one a first-day
 * clerk presses. Nothing on this screen names a phase.
 *
 * "Review expiring grants" WAS the third, on the grounds that B2.4's endpoint
 * existed but nothing drew its queue. Governance drew it, so the button is a
 * link now — `/admin/governance?tab=review`, which opens the queue rather than
 * landing the admin on that screen's front page to find the tab themselves. A plausible "Active · today 09:12"
 * in a screenshot is indistinguishable from working software; so is a button
 * that opens a screen nobody has built.
 *
 * THE ONE ACT ON THIS SCREEN THAT CANNOT BE UNDONE BY CLICKING AGAIN is
 * disabling an account, and it is the one that goes through a dialog rather
 * than a button: `disable-faculty-dialog.component.ts` states what happens,
 * takes the reason the server requires, and posts it itself.
 *
 * THE ONE-SCREEN ACTIVATION LINK IS NOT A STOPGAP. AGENTS.md is explicit: when
 * somebody says "the email never arrived" — and somebody will — the admin reads
 * them this link rather than waiting on a mail queue. So it is a first-class
 * control here, with its expiry and whether mail actually went, and the copy
 * says that each new link supersedes the previous one.
 *
 * THIS SCREEN IS MAIN-ADMIN-ONLY IN PRACTICE, AND ITS ROUTE GUARD IS NOT.
 * The route asks for `admin.mentors` (spec §7), but FOUR of the five endpoints
 * above are `require_admin` — the office account alone: `GET /admin/faculty`
 * and `PATCH /admin/faculty/{id}` (routers/admin_faculty.py) and
 * `POST /users/{id}/activation-link` (routers/admin.py). The other two ask for
 * different capabilities again: `/admin/departments` for `admin.institution`
 * and `/admin/mentor-load` for `admin.analytics`. So a faculty member granted
 * only `admin.mentors` in Governance passes the guard and is refused the LIST
 * — not just a column. `directoryRefused` is why that reads as a refusal and
 * never as an empty roster. Closing the gap is a decision for the owner: give
 * the directory its own capability, or hand the three out together.
 *
 * The row shapes and the grid's columns live beside this file, in
 * `faculty-row.ts` and `faculty-grid.ts`; the offboarding dialog is
 * `disable-faculty-dialog.component.ts`.
 */

import { Component, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AgGridAngular } from 'ag-grid-angular';
import type { CellClickedEvent, GetRowIdParams, GridApi, GridReadyEvent } from 'ag-grid-community';

import { environment } from '../../../../environments/environment';
import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { reepGridTheme } from '../../../shared/grid/reep-grid-theme';
import { plural } from '../../../shared/text/plural.pipe';
import {
  AdminDeleteDialogComponent,
  type DeleteOutcome,
  type DeleteTarget,
} from '../../../shared/admin-delete-dialog/admin-delete-dialog.component';
import { DisableFacultyDialogComponent } from './disable-faculty-dialog.component';
import {
  DEFAULT_FACULTY_COLUMN,
  FACULTY_COLUMNS,
  FACULTY_ROW_SELECTION,
  FACULTY_SELECTION_COLUMN,
  TOGGLEABLE_FACULTY_COLUMNS,
} from './faculty-grid';
import {
  COMFORTABLE_ROW_HEIGHT_PX,
  COMPACT_ROW_HEIGHT_PX,
  DEFAULT_PAGE_SIZE,
  EMPTY_PROFILE_DRAFT,
  INITIALLY_HIDDEN_COLUMN_IDS,
  NOT_READABLE,
  PAGE_SIZES,
  dayLabelOf,
  escapeHtml,
  identityLineOf,
  initialsOf,
  type AccountStateApi,
  type ActivationLinkApi,
  type CollegeOption,
  type DepartmentPickerApiRow,
  type DrawerTab,
  type FacultyApiRow,
  type FacultyProfileDraft,
  type FacultyRow,
  type MentorLoadApiRow,
} from './faculty-row';

/** The board's Status filter. Two values, not the board's three: "Invited" is
 *  not reported by anything (faculty-grid.ts says where that was checked), and
 *  a filter offering a value no row can ever hold is a filter that answers
 *  "none" forever. */
type StatusFilter = '' | 'active' | 'disabled' | 'removed';

/** The board's Function filter, over the one function this screen can read.
 *  `GET /api/admin/mentor-load` says who holds a mentor group; HOD, placement
 *  officer and verifier are capability grants and live in Governance. */
type FunctionFilter = '' | 'mentor' | 'none';


/** B3.3's window, mirrored from `admin_faculty.ENABLE_WINDOW_DAYS`. The server
 *  is the authority — it refuses past it, with a sentence this screen shows
 *  verbatim — and this constant only writes the promise on screen BEFORE the
 *  office presses anything. */
const ENABLE_WINDOW_DAYS = 90;

/** What `GET /api/admin/mentor-load` says about one account, once this screen
 *  has read it. Absent from the map means the account is not in that list. */
interface MentorGroupFact {
  holdsMentorGroup: boolean;
  menteeCount: number;
}

@Component({
  selector: 'app-admin-faculty',
  standalone: true,
  // RouterLink is REQUIRED for the "Add faculty" and "Mentor mapping" links: a
  // `routerLink` in a standalone component that does not import it is inert
  // markup — it renders, it looks like a link, and clicking it does nothing.
  imports: [RouterLink, AgGridAngular, DisableFacultyDialogComponent, AdminDeleteDialogComponent],
  templateUrl: './faculty.component.html',
  styleUrl: './faculty.component.scss',
})
export class AdminFacultyComponent {
  readonly gridTheme = reepGridTheme;
  readonly pageSizes = PAGE_SIZES;
  readonly notReadable = NOT_READABLE;
  readonly enableWindowDays = ENABLE_WINDOW_DAYS;

  // --- what the server said ----------------------------------------------

  private readonly apiRows = signal<FacultyApiRow[] | null>(null);
  private readonly departmentPicker = signal<DepartmentPickerApiRow[]>([]);
  /** Null means the assignment list could not be read — not "no mentors". */
  private readonly mentorGroups = signal<Map<string, MentorGroupFact> | null>(null);

  // --- what the reader chose ---------------------------------------------

  readonly collegeFilter = signal('');
  readonly departmentFilter = signal('');
  /** Both of these narrow WHAT WAS ALREADY FETCHED. `GET /api/admin/faculty`
   *  takes one parameter, `?unfiled=`, and neither of these is it — so a
   *  refetch would change nothing and only make the list flicker. */
  readonly statusFilter = signal<StatusFilter>('');
  readonly functionFilter = signal<FunctionFilter>('');
  readonly quickFilter = signal('');

  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  /** True when `GET /api/admin/faculty` itself was refused. Separate from an
   *  empty list: "the office has no faculty accounts" and "this account may
   *  not read them" must never share a rendering, and the endpoint is
   *  `require_admin` while the ROUTE only asks for `admin.mentors`, so a
   *  granted faculty member reaches this screen and is refused the list. */
  readonly directoryRefused = signal(false);

  // --- the grid -----------------------------------------------------------

  private gridApi: GridApi<FacultyRow> | null = null;
  readonly selectedRows = signal<FacultyRow[]>([]);
  readonly pageSize = signal(DEFAULT_PAGE_SIZE);
  readonly rowHeight = signal(COMFORTABLE_ROW_HEIGHT_PX);
  readonly isCompact = computed(() => this.rowHeight() === COMPACT_ROW_HEIGHT_PX);
  readonly filteredRowCount = signal(0);
  readonly currentPage = signal(0);
  readonly totalPages = signal(0);
  readonly columnsPanelOpen = signal(false);
  readonly hiddenColumnIds = signal<string[]>([...INITIALLY_HIDDEN_COLUMN_IDS]);

  readonly rowSelection = FACULTY_ROW_SELECTION;
  readonly selectionColumn = FACULTY_SELECTION_COLUMN;
  readonly defaultColumn = DEFAULT_FACULTY_COLUMN;
  readonly columns = FACULTY_COLUMNS;
  readonly toggleableColumns = TOGGLEABLE_FACULTY_COLUMNS;

  // --- the drawer ---------------------------------------------------------

  readonly openUserId = signal<string | null>(null);
  readonly activeTab = signal<DrawerTab>('profile');
  readonly draft = signal<FacultyProfileDraft>({ ...EMPTY_PROFILE_DRAFT });
  readonly activationLink = signal<ActivationLinkApi | null>(null);

  /** The account the Disable dialog is open for, or null. */
  readonly disablingUserId = signal<string | null>(null);

  constructor() {
    registerReepGrid();
    void this.loadEverything();
  }

  // ==================================================== derived state ====

  /** Every faculty account the server returned, dressed for the grid. */
  readonly allRows = computed<FacultyRow[]>(() => {
    const rows = this.apiRows();
    if (rows === null) return [];
    const groups = this.mentorGroups();
    return rows.map((row) => this.toFacultyRow(row, groups));
  });

  /** What the grid shows: narrowed by the two filters the API has no parameter
   *  for. Both read a field the row already carries. */
  readonly visibleRows = computed<FacultyRow[]>(() => {
    const college = this.collegeFilter();
    const department = this.departmentFilter();
    const accountStatus = this.statusFilter();
    const fn = this.functionFilter();
    return this.allRows().filter((row) => {
      if (college !== '' && row.collegeId !== college) return false;
      if (department !== '' && row.departmentId !== department) return false;
      if (accountStatus === 'active' && row.isDisabled) return false;
      if (accountStatus === 'disabled' && !row.isDisabled) return false;
      // 'removed' is a different list from the server (`?removed=true`).
      // `holdsMentorGroup` is null when the assignment list was refused, and a
      // null passes BOTH of these rather than being read as a false: filtering
      // somebody out on a fact nobody could read is how a roster loses a row
      // silently. The select is disabled in that case anyway; this is the
      // belt to that brace.
      if (fn === 'mentor' && row.holdsMentorGroup !== true) return false;
      if (fn === 'none' && row.holdsMentorGroup !== false) return false;
      return true;
    });
  });

  readonly isLoading = computed(() => this.apiRows() === null);

  readonly facultyCount = computed(() => this.visibleRows().length);

  readonly mentorCount = computed(
    () => this.visibleRows().filter((row) => row.holdsMentorGroup === true).length,
  );

  readonly unfiledCount = computed(() => this.visibleRows().filter((row) => !row.isFiled).length);

  readonly disabledCount = computed(() => this.visibleRows().filter((row) => row.isDisabled).length);

  readonly mentorGroupsAreReadable = computed(() => this.mentorGroups() !== null);

  /** The sub-line under the h1. It says only what was read: the board's "2
   *  invited" half still cannot be computed by anything (nothing reports
   *  whether an activation link has been redeemed), but "1 disabled" can, and
   *  it appears ONLY when there is one — a roster carrying a permanent "0
   *  disabled" trains the eye to skip the very number it is there to surface.
   *  Count and word agree, the verb included: a fresh install has one faculty
   *  account holding one mentor group, so this is the line that would otherwise
   *  greet the office with "1 faculty accounts · 1 hold a mentor group". */
  readonly summaryLine = computed(() => {
    const parts = [plural(this.facultyCount(), 'faculty account')];
    if (this.mentorGroupsAreReadable()) {
      parts.push(`${plural(this.mentorCount(), 'holds', 'hold')} a mentor group`);
    }
    parts.push(`${this.unfiledCount()} filed in no department`);
    if (this.disabledCount() > 0) {
      parts.push(`${this.disabledCount()} disabled`);
    }
    return parts.join(' · ');
  });

  readonly selectedCount = computed(() => this.selectedRows().length);

  readonly colleges = computed<CollegeOption[]>(() => {
    const byId = new Map<string, string>();
    for (const department of this.departmentPicker()) {
      byId.set(department.college_id, department.college_name);
    }
    const options: CollegeOption[] = [];
    for (const [id, name] of byId) {
      options.push({ id, name });
    }
    options.sort((one, other) => one.name.localeCompare(other.name));
    return options;
  });

  /** Every department in every college — what the DRAWER offers, because moving
   *  a faculty member to another college is a legitimate edit and the grid's
   *  College filter has nothing to do with it. */
  readonly departmentPickerOptions = computed<DepartmentPickerApiRow[]>(() =>
    this.departmentPicker(),
  );

  /** The department picker, narrowed to the chosen college so the two selects
   *  read the way the board does. */
  readonly departmentOptions = computed<DepartmentPickerApiRow[]>(() => {
    const college = this.collegeFilter();
    if (college === '') return this.departmentPicker();
    return this.departmentPicker().filter((department) => department.college_id === college);
  });

  readonly collegeFilterLabel = computed(() => {
    const chosen = this.colleges().find((college) => college.id === this.collegeFilter());
    return chosen?.name ?? 'All colleges';
  });

  readonly departmentFilterLabel = computed(() => {
    const chosen = this.departmentPicker().find(
      (department) => department.id === this.departmentFilter(),
    );
    return chosen?.name ?? 'All departments';
  });

  readonly statusFilterLabel = computed(() => {
    if (this.statusFilter() === 'active') return 'Active';
    if (this.statusFilter() === 'disabled') return 'Disabled';
    if (this.statusFilter() === 'removed') return 'Removed';
    return 'All';
  });

  /** The account the Delete dialog is open for (2026-09-16), or null. */
  readonly deletingUserId = signal<string | null>(null);

  readonly deletingFaculty = computed<DeleteTarget | null>(() => {
    const userId = this.deletingUserId();
    if (userId === null) return null;
    const row = this.allRows().find((candidate) => candidate.userId === userId);
    if (row === undefined) return null;
    return { kind: 'faculty', id: row.userId, name: row.name, subLine: identityLineOf(row) };
  });

  readonly functionFilterLabel = computed(() => {
    if (!this.mentorGroupsAreReadable()) return this.notReadable;
    if (this.functionFilter() === 'mentor') return 'Mentor';
    if (this.functionFilter() === 'none') return 'No mentor group';
    return 'All';
  });

  /** Main's own words for each empty case. Order matters: the narrower the
   *  cause, the earlier it is named, so the reader is told which control to
   *  undo rather than being told the roster is empty. */
  readonly emptyMessage = computed(() => {
    if (this.directoryRefused()) {
      return 'The faculty list could not be read — see the message above. This is not an empty roster.';
    }
    if (this.quickFilter().trim() !== '') {
      return `No faculty member matches “${this.quickFilter().trim()}”.`;
    }
    if (this.statusFilter() === 'disabled') {
      return 'No faculty account is disabled.';
    }
    if (this.statusFilter() === 'active') {
      return 'Every faculty account here is disabled.';
    }
    if (this.functionFilter() === 'mentor') {
      return 'Nobody here holds a mentor group. A faculty account becomes a mentor when the office assigns it a student.';
    }
    if (this.functionFilter() === 'none') {
      return 'Every faculty member here holds a mentor group.';
    }
    if (this.departmentFilter() !== '' || this.collegeFilter() !== '') {
      return 'No faculty member is filed in this department.';
    }
    return 'No faculty accounts yet. Add one to hand over its activation link.';
  });

  /** AG Grid's no-rows overlay takes a string of HTML, so the message is
   *  escaped here — it can carry whatever the reader typed into the filter. */
  readonly emptyOverlay = computed(
    () =>
      `<span style="font-size: 13px; color: var(--muted);">${escapeHtml(this.emptyMessage())}</span>`,
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

  // --- the drawer's subject ------------------------------------------------

  readonly openFaculty = computed<FacultyRow | null>(() => {
    const userId = this.openUserId();
    if (userId === null) return null;
    return this.allRows().find((row) => row.userId === userId) ?? null;
  });

  readonly disablingFaculty = computed<FacultyRow | null>(() => {
    const userId = this.disablingUserId();
    if (userId === null) return null;
    return this.allRows().find((row) => row.userId === userId) ?? null;
  });

  readonly openFacultyIdentityLine = computed(() => {
    const faculty = this.openFaculty();
    if (faculty === null) return '';
    return identityLineOf(faculty);
  });

  readonly openFacultyMenteeLine = computed(() => {
    const faculty = this.openFaculty();
    if (faculty === null) return '';
    if (faculty.holdsMentorGroup === null) {
      return 'The assignment list is not readable with this account’s access.';
    }
    if (!faculty.holdsMentorGroup) {
      return 'No mentor group. This account becomes a mentor when the office assigns it a student.';
    }
    const mentees = faculty.menteeCount ?? 0;
    return `${plural(mentees, 'mentee')} in their group.`;
  });

  /** "Disabled on 12 Sep 2026 — Left the institution", or the empty string for
   *  an account that signs in as usual. The date and the reason travel
   *  together: "disabled" on its own is the fact the office has six months
   *  later and cannot act on. */
  readonly openFacultyStatusLine = computed(() => {
    const faculty = this.openFaculty();
    if (faculty === null) return '';
    if (faculty.isRemoved) {
      const on = `Removed from the roster on ${dayLabelOf(faculty.deletedAt)}`;
      return faculty.deleteReason === null ? `${on}.` : `${on} — ${faculty.deleteReason}`;
    }
    if (!faculty.isDisabled) return '';
    const on = `Disabled on ${dayLabelOf(faculty.disabledAt)}`;
    return faculty.disableReason === null ? `${on}.` : `${on} — ${faculty.disableReason}`;
  });

  /** True once the address in the drawer differs from the one on the account.
   *  It gates both the warning that this signs every device out and the pair of
   *  controls that let an off-domain address through — neither of which has
   *  anything to say while the address is untouched. */
  readonly emailChanged = computed(() => {
    const faculty = this.openFaculty();
    if (faculty === null) return false;
    return this.draft().email.trim().toLowerCase() !== faculty.email.trim().toLowerCase();
  });

  readonly canSaveProfile = computed(() => {
    if (this.openFaculty() === null) return false;
    if (this.busy()) return false;
    const draft = this.draft();
    // The same two floors the server's validators hold, so the refusal for a
    // blank name arrives before the request rather than as a 422 list.
    if (draft.name.trim() === '') return false;
    const email = draft.email.trim();
    if (!email.includes('@') || email.startsWith('@') || email.endsWith('@')) return false;
    // B3.2: an off-domain address needs BOTH the tick and a written reason, and
    // the server refuses the tick without one.
    if (draft.allowExternal && draft.externalReason.trim() === '') return false;
    return true;
  });

  /** The one ticked row, when exactly one is ticked. The toolbar's actions name
   *  a person, so anything else is no subject at all. */
  readonly selectedRow = computed<FacultyRow | null>(() => {
    const selected = this.selectedRows();
    return selected.length === 1 ? selected[0] : null;
  });

  /** Why the toolbar's Disable is grey, or the empty string when it is not.
   *  A destructive button that is disabled for an unexplained reason is read as
   *  broken, and this one has two different reasons. */
  readonly disableHint = computed(() => {
    const row = this.selectedRow();
    if (row === null) return 'Tick exactly one faculty member to disable.';
    if (row.isDisabled) {
      return `${row.name} is already disabled. Open the row to switch the account back on.`;
    }
    return '';
  });

  readonly canDisableSelection = computed(() => this.disableHint() === '');

  // =========================================================== loading ====

  private async loadEverything(): Promise<void> {
    await Promise.all([this.loadDepartments(), this.loadMentorGroups()]);
    await this.reloadFaculty();
  }

  async reloadFaculty(): Promise<void> {
    try {
      const removed = this.statusFilter() === 'removed' ? '?removed=true' : '';
      const response = await fetch(`${environment.apiBase}/admin/faculty${removed}`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      this.apiRows.set((await response.json()) as FacultyApiRow[]);
      this.directoryRefused.set(false);
      // Clear the grid's ticks, not only this component's mirror of them: with
      // `getRowId` the grid KEEPS a selection across a data update and fires no
      // selectionChanged, so wiping the mirror alone leaves a ticked row the
      // status bar counts as zero and the toolbar refuses to act on.
      this.clearSelection();
    } catch (failure) {
      this.apiRows.set([]);
      this.directoryRefused.set(true);
      this.clearSelection();
      this.error.set(failure instanceof Error ? failure.message : 'Could not load the faculty list.');
    }
  }

  private async loadDepartments(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/departments`, {
        credentials: 'include',
      });
      if (!response.ok) return;
      this.departmentPicker.set((await response.json()) as DepartmentPickerApiRow[]);
    } catch {
      /* the grid still works; the two filters offer nothing and say so */
    }
  }

  /**
   * Who holds a mentor group, and how many students are in it.
   *
   * This read needs `admin.analytics` while the screen needs `admin.mentors`,
   * so a faculty member granted only this screen is refused it. A refusal
   * leaves the map NULL rather than empty, and every consumer branches on that:
   * "nobody is a mentor" and "this account may not read the assignment list"
   * are opposite facts.
   */
  private async loadMentorGroups(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/mentor-load`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.mentorGroups.set(null);
        return;
      }
      const rows = (await response.json()) as MentorLoadApiRow[];
      const facts = new Map<string, MentorGroupFact>();
      for (const row of rows) {
        facts.set(row.user_id, {
          holdsMentorGroup: row.mentor_id !== null,
          menteeCount: row.mentee_count,
        });
      }
      this.mentorGroups.set(facts);
    } catch {
      this.mentorGroups.set(null);
    }
  }

  // ======================================================= the filters ====

  setCollegeFilter(collegeId: string): void {
    this.collegeFilter.set(collegeId);
    // A department outside the new college would silently show nothing.
    this.departmentFilter.set('');
  }

  setDepartmentFilter(departmentId: string): void {
    this.departmentFilter.set(departmentId);
  }

  /** Two of the values narrow what is drawn; REMOVED is a different list from
   *  the server (`?removed=true`, `users.deleted_at` set), so crossing into or
   *  out of it refetches. */
  setStatusFilter(value: string): void {
    const wasRemoved = this.statusFilter() === 'removed';
    this.statusFilter.set(value as StatusFilter);
    if (wasRemoved !== (value === 'removed')) void this.reloadFaculty();
  }

  setFunctionFilter(value: string): void {
    this.functionFilter.set(value as FunctionFilter);
  }

  onQuickFilterInput(event: Event): void {
    this.quickFilter.set(this.inputValue(event));
  }

  // ========================================================== the grid ====

  readonly rowId = (params: GetRowIdParams<FacultyRow>): string => params.data.userId;

  onGridReady(event: GridReadyEvent<FacultyRow>): void {
    this.gridApi = event.api;
    this.refreshPagerState();
  }

  /** Both halves of the selection: the grid's ticks and this screen's mirror.
   *  `deselectAll` fires selectionChanged, which sets the mirror; the explicit
   *  set is for the first load, when there is no grid yet. */
  private clearSelection(): void {
    this.gridApi?.deselectAll();
    this.selectedRows.set([]);
  }

  onSelectionChanged(): void {
    if (this.gridApi === null) return;
    this.selectedRows.set(this.gridApi.getSelectedRows());
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

  /** The name column and the chevron both arrive here, and both open the
   *  drawer: the row IS the record, and a second way in is a second thing to
   *  keep working. */
  onCellClicked(event: CellClickedEvent<FacultyRow>): void {
    const row = event.data;
    if (!row) return;
    const columnId = event.column.getColId();
    if (columnId !== 'faculty' && columnId !== 'actions') return;
    this.openDrawer(row);
  }

  // ========================================================== the drawer ==

  openDrawer(row: FacultyRow): void {
    this.openUserId.set(row.userId);
    this.activeTab.set('profile');
    this.activationLink.set(null);
    this.error.set(null);
    this.seedDraft(row);
  }

  /** The draft is re-seeded after every successful save as well as on open, so
   *  the inputs show what the SERVER stored — it collapses whitespace and
   *  lower-cases the address — rather than what was typed at it. */
  private seedDraft(row: FacultyRow): void {
    this.draft.set({
      name: row.name,
      email: row.email,
      departmentId: row.departmentId ?? '',
      designation: row.designation ?? '',
      allowExternal: false,
      externalReason: '',
    });
  }

  closeDrawer(): void {
    this.openUserId.set(null);
    this.activationLink.set(null);
  }

  showTab(tab: DrawerTab): void {
    this.activeTab.set(tab);
  }

  setDraftName(name: string): void {
    this.draft.update((draft) => ({ ...draft, name }));
  }

  setDraftEmail(email: string): void {
    this.draft.update((draft) => ({ ...draft, email }));
  }

  setDraftDepartment(departmentId: string): void {
    this.draft.update((draft) => ({ ...draft, departmentId }));
  }

  setDraftDesignation(designation: string): void {
    this.draft.update((draft) => ({ ...draft, designation }));
  }

  setDraftAllowExternal(allowExternal: boolean): void {
    this.draft.update((draft) => ({ ...draft, allowExternal }));
  }

  setDraftExternalReason(externalReason: string): void {
    this.draft.update((draft) => ({ ...draft, externalReason }));
  }

  /**
   * Save the profile. B3.5 added name and email to this endpoint.
   *
   * `PATCH /api/admin/faculty/{id}` takes name, email, designation, department
   * and department_id. `department_id` is the real pointer; the free-text
   * `department` line the leave form prints is kept in step by the server,
   * which is why this sends only the id.
   *
   * CHANGING THE ADDRESS IS A DIFFERENT ACT and the confirmation says so: the
   * session cookie carries `email` as a claim, so the server bumps
   * `token_version` and every device holding the account is signed out. An
   * admin who corrects a typo and hears nothing back would never guess that.
   */
  async saveProfile(): Promise<void> {
    const faculty = this.openFaculty();
    if (faculty === null) return;
    const draft = this.draft();
    const addressChanged = this.emailChanged();
    await this.run(async () => {
      const response = await fetch(`${environment.apiBase}/admin/faculty/${faculty.userId}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: draft.name.trim(),
          email: draft.email.trim().toLowerCase(),
          department_id: draft.departmentId === '' ? null : draft.departmentId,
          designation: draft.designation.trim() === '' ? null : draft.designation.trim(),
          allow_external: draft.allowExternal,
          external_reason: draft.externalReason.trim() === '' ? null : draft.externalReason.trim(),
        }),
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      this.flash.set(
        addressChanged
          ? `Saved ${draft.name.trim()}. The address changed, so every device holding this account has been signed out.`
          : `Saved ${draft.name.trim()}.`,
      );
      await this.reloadFaculty();
      const saved = this.openFaculty();
      if (saved !== null) this.seedDraft(saved);
    });
  }

  /**
   * Mint (or re-mint) the activation link, and show it.
   *
   * Each call supersedes every older live link, so "re-send" hands over exactly
   * one that works. `emailed` says whether a mail transport was configured and
   * the link actually went; when it is false the link on screen is the delivery
   * channel, which AGENTS.md is explicit is a permanent arrangement and not a
   * stopgap.
   */
  async mintActivationLink(): Promise<void> {
    const faculty = this.openFaculty();
    if (faculty === null) return;
    await this.run(async () => {
      const response = await fetch(
        `${environment.apiBase}/admin/users/${faculty.userId}/activation-link`,
        { method: 'POST', credentials: 'include' },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      this.activationLink.set((await response.json()) as ActivationLinkApi);
      this.flash.set(`A new activation link for ${faculty.name} is ready to hand over.`);
    });
  }

  async copyActivationLink(): Promise<void> {
    const minted = this.activationLink();
    if (minted === null) return;
    try {
      await navigator.clipboard.writeText(minted.link);
      this.flash.set('Activation link copied.');
    } catch {
      this.error.set('The clipboard is not available here — select the link and copy it.');
    }
  }

  // ============================================== the account itself (B3.3) ==

  /**
   * Switch a disabled account back on.
   *
   * ONE BUTTON AND NO CONFIRMATION, deliberately, and it is not an oversight
   * beside the dialog its opposite gets: re-enabling restores a login and
   * nothing else — the grants are NOT re-granted, which is the server's own
   * asymmetry — and the undo is the Disable button that is already there. The
   * ninety-day window is the server's; past it the refusal is a sentence
   * naming the date, and it is shown verbatim rather than reworded.
   */
  async enableAccount(): Promise<void> {
    const faculty = this.openFaculty();
    if (faculty === null) return;
    await this.run(async () => {
      const response = await fetch(`${environment.apiBase}/admin/users/${faculty.userId}/enable`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      const state = (await response.json()) as AccountStateApi;
      this.flash.set(state.detail);
      await this.reloadFaculty();
    });
  }

  /**
   * Retire every session the account holds, and nothing else (B3.6).
   *
   * No password reset, no disable, nothing to undo — which is what makes it the
   * right first move for "I left myself signed in on the lab machine". The
   * account can sign in again immediately, so this is a button and not a
   * dialog.
   *
   * THE STUDENT SCREEN DOES ASK, on the same endpoint — see
   * `features/admin/student-detail`, whose button stands alone among disabled
   * controls where a misclick is likelier, and which has no Disable dialog
   * beside it to set the gradient. Both readings are defensible and the
   * divergence is deliberate rather than an oversight; it is cross-referenced
   * here and there so that whoever settles it changes both, and does not
   * "fix" one screen into disagreeing with the other a second time.
   */
  async signOutEverywhere(): Promise<void> {
    const faculty = this.openFaculty();
    if (faculty === null) return;
    await this.run(async () => {
      const response = await fetch(
        `${environment.apiBase}/admin/users/${faculty.userId}/sign-out-everywhere`,
        { method: 'POST', credentials: 'include' },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      const state = (await response.json()) as AccountStateApi;
      this.flash.set(state.detail);
    });
  }

  // ============================================= remove, delete, restore ==

  openDeleteDialog(userId: string): void {
    this.deletingUserId.set(userId);
  }

  closeDeleteDialog(): void {
    this.deletingUserId.set(null);
  }

  /** The dialog posted it and the server answered; `detail` is the server's
   *  own sentence. The roster is reread so the row leaves (or is gone). */
  async onDeleteDone(outcome: DeleteOutcome): Promise<void> {
    this.deletingUserId.set(null);
    this.error.set(null);
    this.closeDrawer();
    await this.reloadFaculty();
    this.flash.set(outcome.detail);
  }

  /** `POST /admin/users/{id}/restore` — back on the list, nothing lost. The
   *  account comes back DISABLED if it was disabled before it was removed;
   *  the server's sentence says which. */
  async restoreAccount(): Promise<void> {
    const faculty = this.openFaculty();
    if (faculty === null || !faculty.isRemoved) return;
    await this.run(async () => {
      const response = await fetch(`${environment.apiBase}/admin/users/${faculty.userId}/restore`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      const state = (await response.json()) as { detail: string };
      this.closeDrawer();
      this.flash.set(state.detail);
      await this.reloadFaculty();
    });
  }

  // ==================================================== the disable dialog ==

  openDisableDialog(userId: string): void {
    this.disablingUserId.set(userId);
  }

  /** The toolbar's Disable acts on the one ticked row, for the same reason the
   *  drawer's does: offboarding names a person. */
  openDisableDialogForSelection(): void {
    const row = this.selectedRow();
    if (row === null || row.isDisabled) return;
    this.openDisableDialog(row.userId);
  }

  closeDisableDialog(): void {
    this.disablingUserId.set(null);
  }

  /** The dialog posted it and the server answered. `detail` is the server's own
   *  sentence about what it just did and it is shown as it arrived; the roster
   *  is reread so the Status column and the summary line agree with it. */
  async onDisabled(state: AccountStateApi): Promise<void> {
    this.disablingUserId.set(null);
    this.error.set(null);
    await this.reloadFaculty();
    this.flash.set(state.detail);
  }

  // ============================================================ helpers ====

  inputValue(event: Event): string {
    const target = event.target as HTMLInputElement;
    return target.value;
  }

  checkboxValue(event: Event): boolean {
    return (event.target as HTMLInputElement).checked;
  }

  selectValue(event: Event): string {
    const target = event.target as HTMLSelectElement;
    return target.value;
  }

  numberValue(event: Event): number {
    return Number(this.selectValue(event));
  }

  private toFacultyRow(
    row: FacultyApiRow,
    groups: Map<string, MentorGroupFact> | null,
  ): FacultyRow {
    // Null all the way down when the assignment list was refused; otherwise an
    // account missing from it genuinely holds no group, which is a real false
    // and not a failed read.
    let holdsMentorGroup: boolean | null = null;
    let menteeCount: number | null = null;
    if (groups !== null) {
      const fact = groups.get(row.user_id);
      holdsMentorGroup = fact === undefined ? false : fact.holdsMentorGroup;
      menteeCount = fact === undefined ? 0 : fact.menteeCount;
    }
    return {
      userId: row.user_id,
      name: row.name,
      email: row.email,
      initials: initialsOf(row.name),
      designation: row.designation,
      departmentLine: this.departmentLineOf(row),
      departmentId: row.placement.department_id,
      collegeId: row.placement.college_id,
      collegeName: row.placement.college_name,
      isFiled: row.placement.filed,
      holdsMentorGroup,
      menteeCount,
      disabledAt: row.disabled_at,
      disableReason: row.disable_reason,
      // The column IS the state (B3.3): a timestamp means disabled, null means
      // the account signs in as usual. Nothing else on the row is consulted.
      isDisabled: row.disabled_at !== null,
      isRemoved: row.deleted_at !== null,
      deletedAt: row.deleted_at,
      deleteReason: row.delete_reason,
    };
  }

  /** The filed department wins; the free-text line the leave form prints is the
   *  fallback; "Not filed" is the honest third case and the one the office is
   *  here to clear. */
  private departmentLineOf(row: FacultyApiRow): string {
    if (row.placement.filed && row.placement.department_name !== null) {
      return row.placement.department_name;
    }
    if (row.department !== null) return row.department;
    return 'Not filed';
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
   *  reads "[object Object]". Its refusals name the reason, so the server's own
   *  sentence is kept wherever there is one. */
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
