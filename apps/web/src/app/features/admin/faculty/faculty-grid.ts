/**
 * The faculty directory grid: its columns, its cell renderers, its selection.
 *
 * AG Grid builds cell contents itself, outside Angular's template compiler, so
 * a component-scoped class never reaches them. Every renderer below uses GLOBAL
 * classes (`.chip`, `.avatar`, `.btn`, `.icon` in styles/reep-v2.scss) and
 * inline styles, exactly as the roster grid on Students & batches does — and
 * escapes every value it interpolates.
 *
 * STATUS IS REAL NOW; SIGN-IN IS STILL EMPTY, AND THEY ARE EMPTY FOR DIFFERENT
 * REASONS. B3.3 landed `users.disabled_at`, and `GET /api/admin/faculty`
 * carries it, so Status reads the account itself: a `.chip good` "Active" or a
 * `.chip risk` "Disabled" with the day it happened — text AND colour, never
 * colour alone. The board's third state, "Invited", is NOT drawn: nothing in
 * that payload says whether an account has ever redeemed its activation link,
 * and guessing it from a blank field would be exactly the plausible-looking
 * cell this file exists to refuse.
 *
 * There is no Sign-in column. Login events are recorded, but no admin endpoint
 * answers "when did somebody else last sign in", so a column would only ever
 * be a dash — and it was, until 2026-09-15.
 *
 * FUNCTIONS IS HALF ANSWERABLE AND SHOWS EXACTLY THAT HALF. The mentor function
 * is real today — it is the `Mentor` group the office creates by assigning the
 * first student — so a held group renders as the board's "Mentor · 14" chip.
 * HOD, placement officer and verifier are `B2.3` and are not guessed at; an
 * account with no group reads "No mentor group", which is what the API said,
 * and never "No function", which would be a claim about grants nothing here can
 * read.
 */

import type { ColDef, ICellRendererParams, RowSelectionOptions, SelectionColumnDef } from 'ag-grid-community';

import { NOT_READABLE, dayLabelOf, escapeHtml, type FacultyRow } from './faculty-row';

/** The header tooltip on Status: the one state of the board's three that
 *  nothing reports. */
export const STATUS_UNKNOWN_REASON =
  'Active and Disabled are read from the account itself. “Invited” — an account that has never redeemed its activation link — is not reported by any endpoint, so it is not shown.';

function renderNotReadable(): string {
  return `<span style="color: var(--faint);">${NOT_READABLE}</span>`;
}

/** Text AND colour. The reason rides along as the cell's tooltip, because a
 *  140px column cannot hold "Left the institution, September intake" and the
 *  office's next question after "disabled" is always "why". */
function renderStatusCell(params: ICellRendererParams<FacultyRow>): string {
  const row = params.data;
  if (!row) return '';
  if (row.isRemoved) {
    const why = row.deleteReason === null ? '' : ` title="${escapeHtml(row.deleteReason)}"`;
    return (
      `<span class="chip risk"${why}>Removed</span>` +
      `<span style="margin-left: 6px; font-size: 11px; color: var(--faint);">${escapeHtml(dayLabelOf(row.deletedAt))}</span>`
    );
  }
  if (!row.isDisabled) return `<span class="chip good">Active</span>`;
  const reason = row.disableReason === null ? '' : ` title="${escapeHtml(row.disableReason)}"`;
  return (
    `<span class="chip risk"${reason}>Disabled</span>` +
    `<span style="margin-left: 6px; font-size: 11px; color: var(--faint);">${escapeHtml(dayLabelOf(row.disabledAt))}</span>`
  );
}

function renderFacultyCell(params: ICellRendererParams<FacultyRow>): string {
  const row = params.data;
  if (!row) return '';
  return (
    `<span class="avatar">${escapeHtml(row.initials)}</span>` +
    `<span style="display: inline-flex; flex-direction: column; line-height: 1.25; min-width: 0;">` +
    `<span style="color: var(--brand-purple); font-weight: 600; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(row.name)}</span>` +
    `<span style="font-size: 11px; color: var(--faint); overflow: hidden; text-overflow: ellipsis;">${escapeHtml(row.email)}</span>` +
    `</span>`
  );
}

function renderDepartmentCell(params: ICellRendererParams<FacultyRow>): string {
  const row = params.data;
  if (!row) return '';
  if (row.isFiled) return escapeHtml(row.departmentLine);
  // Unfiled is not a blank: the API sorts these first precisely so somebody
  // files them, and the chip is what makes them findable in a long list.
  return `<span class="chip warn">${escapeHtml(row.departmentLine)}</span>`;
}

function renderDesignationCell(params: ICellRendererParams<FacultyRow>): string {
  const row = params.data;
  if (!row) return '';
  if (row.designation === null) {
    return `<span style="color: var(--faint);">Not on record</span>`;
  }
  return escapeHtml(row.designation);
}

function renderFunctionsCell(params: ICellRendererParams<FacultyRow>): string {
  const row = params.data;
  if (!row) return '';
  if (row.holdsMentorGroup === null) return renderNotReadable();
  if (!row.holdsMentorGroup) {
    return `<span class="chip">No mentor group</span>`;
  }
  const mentees = row.menteeCount ?? 0;
  return `<span class="chip accent">Mentor · ${mentees}</span>`;
}

function renderEditCell(params: ICellRendererParams<FacultyRow>): string {
  const row = params.data;
  if (!row) return '';
  return (
    `<button type="button" class="btn ghost sm" aria-label="Open ${escapeHtml(row.name)}">` +
    `<span class="icon" aria-hidden="true">chevron_right</span></button>`
  );
}

/** Ticking is a deliberate act: the toolbar's actions read the ticked rows, so
 *  a stray click on a cell must not change what they would touch. */
export const FACULTY_ROW_SELECTION: RowSelectionOptions<FacultyRow> = {
  mode: 'multiRow',
  checkboxes: true,
  headerCheckbox: true,
  enableClickSelection: false,
};

/** The tick travels with the pinned name column rather than scrolling away
 *  from the person it selects. */
export const FACULTY_SELECTION_COLUMN: SelectionColumnDef = { pinned: 'left', width: 44 };

export const DEFAULT_FACULTY_COLUMN: ColDef<FacultyRow> = {
  sortable: true,
  resizable: true,
  filter: true,
  floatingFilter: true,
  suppressHeaderMenuButton: false,
};

export const FACULTY_COLUMNS: ColDef<FacultyRow>[] = [
  {
    colId: 'faculty',
    field: 'name',
    headerName: 'Faculty',
    pinned: 'left',
    width: 250,
    cellRenderer: renderFacultyCell,
    cellClass: 'faculty-cell',
    // The quick filter matches the address as well as the name, because the
    // address is what the office has when somebody says "she cannot sign in".
    getQuickFilterText: (params) => `${params.data.name} ${params.data.email}`,
  },
  {
    colId: 'department',
    field: 'departmentLine',
    headerName: 'Department',
    width: 180,
    cellRenderer: renderDepartmentCell,
  },
  {
    colId: 'designation',
    field: 'designation',
    headerName: 'Designation',
    width: 165,
    cellRenderer: renderDesignationCell,
  },
  {
    colId: 'functions',
    headerName: 'Mentor group',
    width: 165,
    sortable: false,
    filter: false,
    floatingFilter: false,
    cellRenderer: renderFunctionsCell,
    headerTooltip:
      'The mentor function is the group the office assigns. HOD, placement officer and verifier arrive with B2.3 (Phase 3).',
  },
  {
    // Sortable and filterable now that it says something: a roster of two
    // hundred with three disabled accounts in it is a column you sort by.
    // `valueGetter` is what the sort, the column filter and the quick filter
    // all read — the renderer's chip is HTML and none of them can see it.
    colId: 'status',
    headerName: 'Status',
    width: 150,
    valueGetter: (params) =>
      params.data?.isRemoved ? 'Removed' : params.data?.isDisabled ? 'Disabled' : 'Active',
    cellRenderer: renderStatusCell,
    headerTooltip: STATUS_UNKNOWN_REASON,
  },
  {
    colId: 'actions',
    headerName: '',
    width: 60,
    sortable: false,
    filter: false,
    floatingFilter: false,
    resizable: false,
    cellRenderer: renderEditCell,
  },
];

/** What the Columns popover offers. The pinned name column is not among them:
 *  hiding the column that says who the row is leaves a grid of attributes. */
export const TOGGLEABLE_FACULTY_COLUMNS = [
  { id: 'department', label: 'Department' },
  { id: 'designation', label: 'Designation' },
  { id: 'functions', label: 'Mentor group' },
  { id: 'status', label: 'Status' },
];
