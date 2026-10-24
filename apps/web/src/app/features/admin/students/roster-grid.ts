/**
 * The roster grid: its columns, its cell renderers and its selection rules.
 *
 * AG Grid builds cell contents itself, outside Angular's template compiler, so
 * a component-scoped class never reaches them. Every renderer below therefore
 * uses GLOBAL classes (`.chip`, `.avatar`, `.btn`, `.icon` in
 * styles/reep-v2.scss) and inline styles, exactly as the mentor-load grid on
 * Analytics does — and escapes every value it interpolates, because a student's
 * name is somebody else's text.
 *
 * READINESS, CGPA AND ATTENDANCE ARE HERE AND THEY ARE EMPTY, AND THAT IS NOT
 * A PHASE. The board draws the three columns; `GET /api/admin/students` does
 * not carry any of them — it answers identity, seating and stage. All three
 * exist and are read per student on Student 360, one request per student, so a
 * roster that filled these cells would issue one request per ROW on every page.
 * Each therefore renders an em dash and its header tooltip says where the
 * number actually lives. The screen repeats that once in a `.notice.accent`. A
 * plausible number in a screenshot is indistinguishable from working software.
 */

import type {
  ColDef,
  GetQuickFilterTextParams,
  ICellRendererParams,
  RowSelectionOptions,
  SelectionColumnDef,
  ValueFormatterParams,
} from 'ag-grid-community';

import { INITIALLY_HIDDEN_COLUMN_IDS, NOT_READABLE, escapeHtml, type RosterRow } from './roster-row';

function renderUsnCell(params: ICellRendererParams<RosterRow>): string {
  const row = params.data;
  if (!row) return '';
  if (row.usn === null) {
    return `<span style="color: var(--faint);">Not on record</span>`;
  }
  return `<span style="font-variant-numeric: tabular-nums; letter-spacing: 0.01em;">${escapeHtml(row.usn)}</span>`;
}

/** What the roster hands its cell renderers. */
export interface RosterGridContext {
  /** Whether this reader can open Student 360 — see renderStudentCell and
   *  renderActionsCell. Mirrors the `admin.student_records` route guard. */
  readonly canOpenDetail: boolean;
}

/** The `data-action` a click in the actions column carries, so one cell can
 *  hold two buttons and the screen can tell which was pressed. */
export type RosterRowAction = 'view' | 'edit';

function renderStudentCell(params: ICellRendererParams<RosterRow>): string {
  const row = params.data;
  if (!row) return '';
  const identity =
    `<span style="font-size: 11px; color: var(--faint); overflow: hidden; text-overflow: ellipsis;">` +
    `${escapeHtml(row.email)}</span>`;

  // THE NAME IS A LINK ONLY WHEN THE READER CAN FOLLOW IT. Student 360 is
  // guarded by `admin.student_records` (2026-09-17) and this roster by
  // `admin.students`, and the Main Admin holds both — but a faculty member
  // granted the roster editor and not the record read is exactly the reader
  // this branch is for: a purple link on every row that bounced them back to
  // their own home is the dead link the navigation model exists to make
  // impossible. For a reader who cannot follow it, the name is plain text.
  const name = (params.context as RosterGridContext | undefined)?.canOpenDetail
    ? // A real anchor, so the keyboard reaches it and the status bar shows where
      // it goes; the click is intercepted into the router so the SPA does not
      // reload.
      `<a href="/admin/students/${encodeURIComponent(row.studentId)}" style="color: var(--brand-purple); font-weight: 600; text-decoration: none; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(row.name)}</a>`
    : `<span style="font-weight: 600; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(row.name)}</span>`;

  return (
    `<span class="avatar">${escapeHtml(row.initials)}</span>` +
    `<span style="display: inline-flex; flex-direction: column; line-height: 1.25; min-width: 0;">` +
    name +
    identity +
    `</span>`
  );
}

/** What the quick filter matches on the Student column: the name AND the
 *  address the cell draws. The column's value is the name alone, so without
 *  this an address typed into the box hid every row — including the one the
 *  server had just found by that address. */
export function quickFilterStudent(params: GetQuickFilterTextParams<RosterRow>): string {
  const row = params.data;
  if (row === undefined) return '';
  return `${row.name} ${row.email}`;
}

function specializationChip(code: string, colour: string): string {
  const dot =
    `<span style="width: 7px; height: 7px; border-radius: 4px; display: inline-block; ` +
    `margin-right: 6px; background: ${colour};"></span>`;
  return `<span class="chip">${dot}${escapeHtml(code)}</span>`;
}

/** The batch's specialization, and beside it the second of a dual one. */
function renderSpecializationCell(params: ICellRendererParams<RosterRow>): string {
  const row = params.data;
  if (!row) return '';
  const chips: string[] = [];
  if (row.specializationCode !== null) chips.push(specializationChip(row.specializationCode, row.specializationColour));
  if (row.secondSpecializationCode !== null) {
    chips.push(specializationChip(row.secondSpecializationCode, row.secondSpecializationColour));
  }
  if (chips.length === 0) return `<span style="color: var(--faint);">${NOT_READABLE}</span>`;
  return chips.join(' ');
}

function renderMentorCell(params: ICellRendererParams<RosterRow>): string {
  const row = params.data;
  if (!row) return '';
  if (row.mentorName === null) {
    return '<span class="chip warn">Not assigned</span>';
  }
  return escapeHtml(row.mentorName);
}

function renderStatusCell(params: ICellRendererParams<RosterRow>): string {
  const row = params.data;
  if (!row) return '';
  // Label and tone are this file's own words, never roster text.
  return `<span class="chip dot ${row.statusTone}">${row.statusLabel}</span>`;
}

/**
 * The row's two actions: VIEW the whole record, EDIT the roster row.
 *
 * View arrived on 2026-09-17 because the Student column's link was the only
 * way from this grid to a student's record, and that column can be hidden in
 * the Columns panel (the office had) — which left a roster with no way to open
 * anybody. A button in the pinned actions column cannot be hidden. It is drawn
 * only for a reader who can follow it, the name link's own rule; the pencil is
 * drawn for everyone who can see the grid, because the roster key IS the edit.
 * Each button says what it does in `data-action`, which is what the screen's
 * click handler reads to tell them apart.
 */
function renderActionsCell(params: ICellRendererParams<RosterRow>): string {
  const row = params.data;
  if (!row) return '';
  const canView = (params.context as RosterGridContext | undefined)?.canOpenDetail;
  const view = canView
    ? `<button type="button" class="btn ghost sm" data-action="view" ` +
      `aria-label="View ${escapeHtml(row.name)}'s record" title="View the full record">` +
      `<span class="icon" aria-hidden="true">visibility</span></button>`
    : '';
  const edit =
    `<button type="button" class="btn ghost sm" data-action="edit" ` +
    `aria-label="Edit ${escapeHtml(row.name)}" title="Edit this row">` +
    `<span class="icon" aria-hidden="true">edit</span></button>`;
  return view + edit;
}

function formatBatch(params: ValueFormatterParams<RosterRow, string | null>): string {
  return params.value ?? 'No batch';
}

export const ROSTER_ROW_SELECTION: RowSelectionOptions<RosterRow> = {
  mode: 'multiRow',
  checkboxes: true,
  headerCheckbox: true,
  // Ticking is a deliberate act on this screen: the toolbar acts on the
  // ticked rows, so a stray click on a cell must not change what a bulk
  // action would touch.
  enableClickSelection: false,
};

/** The tick column travels with the pinned USN column rather than scrolling
 *  away from the row it selects. */
export const ROSTER_SELECTION_COLUMN: SelectionColumnDef = { pinned: 'left', width: 44 };

export const DEFAULT_ROSTER_COLUMN: ColDef<RosterRow> = {
  sortable: true,
  resizable: true,
  filter: true,
  floatingFilter: true,
  suppressHeaderMenuButton: false,
};

export const ROSTER_COLUMNS: ColDef<RosterRow>[] = [
  {
    colId: 'usn',
    field: 'usn',
    headerName: 'USN',
    pinned: 'left',
    minWidth: 160,
    cellRenderer: renderUsnCell,
  },
  {
    colId: 'name',
    field: 'name',
    headerName: 'Student',
    minWidth: 220,
    flex: 1.4,
    cellStyle: { display: 'flex', alignItems: 'center', gap: '8px' },
    cellRenderer: renderStudentCell,
    getQuickFilterText: quickFilterStudent,
  },
  {
    colId: 'specialization',
    field: 'specializationCode',
    headerName: 'Spec.',
    minWidth: 110,
    cellRenderer: renderSpecializationCell,
    headerTooltip: 'The specialization named on the student’s batch, and the second of a dual one',
  },
  {
    colId: 'semester',
    field: 'semester',
    headerName: 'Sem',
    type: 'numericColumn',
    minWidth: 90,
  },
  {
    colId: 'stage',
    field: 'stageLabel',
    headerName: 'Stage',
    minWidth: 120,
    headerTooltip: 'The REEP programme stage: Reboot, Excel, Excel-Adv, Elevate',
  },
  {
    colId: 'mentor',
    field: 'mentorName',
    headerName: 'Faculty',
    minWidth: 170,
    flex: 1,
    cellRenderer: renderMentorCell,
  },
  {
    colId: 'batch',
    field: 'batchName',
    headerName: 'Batch',
    minWidth: 200,
    hide: INITIALLY_HIDDEN_COLUMN_IDS.includes('batch'),
    valueFormatter: formatBatch,
  },
  {
    colId: 'status',
    field: 'statusLabel',
    headerName: 'Status',
    minWidth: 150,
    cellRenderer: renderStatusCell,
    headerTooltip: 'Active once the student has signed in; Invited until they walk the setup link',
  },
  {
    colId: 'actions',
    headerName: '',
    pinned: 'right',
    width: 100,
    sortable: false,
    filter: false,
    floatingFilter: false,
    resizable: false,
    suppressHeaderMenuButton: true,
    cellStyle: { display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '2px' },
    cellRenderer: renderActionsCell,
  },
];

/** What the Columns panel lists. The selection and action columns are not on
 *  it: one is the grid's own, the other is the only way to open the editor —
 *  and, since 2026-09-17, the one way to the record that cannot be hidden. */
export const TOGGLEABLE_ROSTER_COLUMNS = ROSTER_COLUMNS
  .filter((column) => column.colId !== 'actions')
  .map((column) => ({ id: column.colId ?? '', label: column.headerName ?? '' }));
