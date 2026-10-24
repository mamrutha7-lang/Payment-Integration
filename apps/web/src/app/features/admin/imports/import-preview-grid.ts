/**
 * The preview grid's columns, and the history grid's.
 *
 * THE COLUMNS ARE THE SERVER'S PAYLOAD, ONE FOR ONE. Every field below is a
 * field of `ImportPreviewRowOut` — the shape `POST /api/admin/imports/preview`
 * answers with after reading the file against the batch's roster. Nothing is
 * derived here and nothing is defaulted: a number the parser did not read comes
 * back null and is drawn as a dash, because a 0 in a Total column is a claim
 * that the sheet said zero.
 *
 * LINE NUMBER IS THE FIRST COLUMN AND IT IS NOT DECORATION. The only thing a
 * reviewer can do with a refusal is find that line in their own spreadsheet and
 * fix it, which is also why the USN is pinned and printed exactly as it was
 * typed rather than as the roster spells it.
 *
 * AG Grid builds cells outside Angular's template compiler, so a
 * component-scoped class never reaches them; the renderers below therefore use
 * the GLOBAL `.chip` classes (styles/reep-v2.scss), exactly as the roster grid
 * does. They interpolate only this file's own literal words — never a value off
 * a row — so there is nothing here to escape.
 */

import type { ColDef, ICellRendererParams, ValueFormatterParams } from 'ag-grid-community';

import {
  NOT_READABLE,
  type ImportPreviewRowOut,
  type ImportRowVerdict,
  type RecentImportRow,
} from './import-dataset';

/** Status is shape and text together, never colour alone: `.chip.dot` puts a
 *  7px dot before a word that also says what happened. */
const VERDICT_CHIPS: Record<ImportRowVerdict, { label: string; tone: string }> = {
  ok: { label: 'OK', tone: 'good' },
  warning: { label: 'Warning', tone: 'warn' },
  error: { label: 'Error', tone: 'risk' },
};

function renderVerdictCell(params: ICellRendererParams<ImportPreviewRowOut>): string {
  const row = params.data;
  if (!row) return '';
  const chip = VERDICT_CHIPS[row.verdict];
  if (!chip) return '';
  return `<span class="chip dot ${chip.tone}">${chip.label}</span>`;
}

function renderRunStatusCell(params: ICellRendererParams<RecentImportRow>): string {
  const row = params.data;
  if (!row) return '';
  // Both halves are this file's or the component's own literals, never server
  // prose: `statusLabel` is chosen from a fixed map in imports.component.ts.
  return `<span class="chip dot ${row.statusTone}">${row.statusLabel}</span>`;
}

function formatNumberOrDash(
  params: ValueFormatterParams<ImportPreviewRowOut, number | null>,
): string {
  if (params.value === null || params.value === undefined) return NOT_READABLE;
  return String(params.value);
}

function formatPercentOrDash(
  params: ValueFormatterParams<ImportPreviewRowOut, number | null>,
): string {
  if (params.value === null || params.value === undefined) return NOT_READABLE;
  return `${params.value}%`;
}

/** The subject as the sheet named it: the code always, the name when the file
 *  carried one. A valueFormatter's result is written as TEXT by the grid, not
 *  as HTML — which is why these two values off the uploaded row need no
 *  escaping here, and why they must never be moved into a cellRenderer without
 *  it. */
function formatSubject(params: ValueFormatterParams<ImportPreviewRowOut, string | null>): string {
  const row = params.data;
  if (!row || !row.subject_code) return NOT_READABLE;
  if (!row.subject_name) return row.subject_code;
  return `${row.subject_code} · ${row.subject_name}`;
}

export const DEFAULT_IMPORT_COLUMN: ColDef = {
  sortable: true,
  resizable: true,
  suppressHeaderMenuButton: false,
};

/** Shared by both datasets: where the line is, what the verdict is, and who it
 *  is about. The USN is pinned, because it is the column that says which
 *  student a message is refusing and it must not scroll away from the message. */
const WHO_THE_ROW_IS_ABOUT: ColDef<ImportPreviewRowOut>[] = [
  {
    colId: 'line_no',
    field: 'line_no',
    headerName: 'Line',
    type: 'numericColumn',
    minWidth: 86,
    headerTooltip: 'The line in your own spreadsheet, so a refusal can be found and fixed there',
  },
  {
    colId: 'verdict',
    field: 'verdict',
    headerName: 'Check',
    minWidth: 110,
    cellRenderer: renderVerdictCell,
    headerTooltip: 'OK is written as it stands · Warning overwrites what is on file · Error is skipped',
  },
  {
    colId: 'usn',
    field: 'usn',
    headerName: 'USN',
    pinned: 'left',
    minWidth: 150,
    cellStyle: { fontVariantNumeric: 'tabular-nums' },
    headerTooltip: 'Exactly as the sheet typed it, not as the roster spells it',
  },
  {
    colId: 'student_name',
    field: 'student_name',
    headerName: 'Student',
    minWidth: 150,
    flex: 1,
    valueFormatter: (params: ValueFormatterParams<ImportPreviewRowOut, string | null>) =>
      params.value ?? NOT_READABLE,
    headerTooltip: 'The roster’s answer to that USN — a dash means no student in this batch has it',
  },
];

/** The message column, last in both datasets: it is the widest and the one a
 *  reviewer reads after the verdict has told them to. */
const MESSAGE_COLUMN: ColDef<ImportPreviewRowOut> = {
  colId: 'message',
  field: 'message',
  headerName: 'What will happen',
  minWidth: 280,
  flex: 2,
  headerTooltip: 'What this line writes, or the reason it was refused',
};

export const MARKS_PREVIEW_COLUMNS: ColDef<ImportPreviewRowOut>[] = [
  ...WHO_THE_ROW_IS_ABOUT,
  {
    colId: 'subject',
    field: 'subject_code',
    headerName: 'Subject',
    minWidth: 200,
    flex: 1.2,
    valueFormatter: formatSubject,
  },
  {
    colId: 'credits',
    field: 'credits',
    headerName: 'Credits',
    type: 'numericColumn',
    minWidth: 100,
    valueFormatter: formatNumberOrDash,
  },
  {
    colId: 'internal',
    field: 'internal',
    headerName: 'Internal',
    type: 'numericColumn',
    minWidth: 105,
    valueFormatter: formatNumberOrDash,
  },
  {
    colId: 'external',
    field: 'external',
    headerName: 'External',
    type: 'numericColumn',
    minWidth: 105,
    valueFormatter: formatNumberOrDash,
  },
  {
    colId: 'total',
    field: 'total',
    headerName: 'Total',
    type: 'numericColumn',
    minWidth: 95,
    valueFormatter: formatNumberOrDash,
  },
  {
    colId: 'sgpa',
    field: 'sgpa',
    headerName: 'SGPA',
    type: 'numericColumn',
    minWidth: 95,
    valueFormatter: formatNumberOrDash,
  },
  {
    colId: 'cgpa',
    field: 'cgpa',
    headerName: 'CGPA',
    type: 'numericColumn',
    minWidth: 95,
    valueFormatter: formatNumberOrDash,
  },
  {
    colId: 'live_backlogs',
    field: 'live_backlogs',
    headerName: 'Backlogs',
    type: 'numericColumn',
    minWidth: 110,
    valueFormatter: formatNumberOrDash,
    headerTooltip: 'Live backlogs — the count the placement criteria read',
  },
  MESSAGE_COLUMN,
];

export const ATTENDANCE_PREVIEW_COLUMNS: ColDef<ImportPreviewRowOut>[] = [
  ...WHO_THE_ROW_IS_ABOUT,
  {
    colId: 'subject',
    field: 'subject_code',
    headerName: 'Subject',
    minWidth: 200,
    flex: 1.2,
    valueFormatter: formatSubject,
  },
  {
    colId: 'sessions_held',
    field: 'sessions_held',
    headerName: 'Sessions held',
    type: 'numericColumn',
    minWidth: 130,
    valueFormatter: formatNumberOrDash,
  },
  {
    colId: 'sessions_attended',
    field: 'sessions_attended',
    headerName: 'Attended',
    type: 'numericColumn',
    minWidth: 120,
    valueFormatter: formatNumberOrDash,
  },
  {
    colId: 'attendance_percent',
    field: 'attendance_percent',
    headerName: 'Attendance %',
    type: 'numericColumn',
    minWidth: 130,
    valueFormatter: formatPercentOrDash,
  },
  MESSAGE_COLUMN,
];

export const RECENT_IMPORT_COLUMNS: ColDef<RecentImportRow>[] = [
  {
    colId: 'datasetLabel',
    field: 'datasetLabel',
    headerName: 'Dataset',
    minWidth: 200,
    flex: 1.4,
  },
  {
    colId: 'batchLabel',
    field: 'batchLabel',
    headerName: 'Batch',
    minWidth: 160,
    flex: 1,
  },
  {
    colId: 'statusLabel',
    field: 'statusLabel',
    headerName: 'Status',
    minWidth: 140,
    cellRenderer: renderRunStatusCell,
    headerTooltip: 'Previewed means read and judged, nothing written · Imported means written',
  },
  {
    colId: 'rowsLabel',
    field: 'rowsLabel',
    headerName: 'Rows',
    minWidth: 170,
    headerTooltip:
      'Lines read from the file, and lines written into student records. A run nobody applied has written 0.',
  },
  {
    colId: 'checksLabel',
    field: 'checksLabel',
    headerName: 'Checks',
    minWidth: 220,
    headerTooltip:
      'A flagged line overwrites what was on file and IS written; a refused line is skipped.',
  },
  {
    colId: 'byName',
    field: 'byName',
    headerName: 'By',
    minWidth: 150,
    flex: 1,
  },
  {
    colId: 'whenLabel',
    field: 'whenLabel',
    headerName: 'When',
    minWidth: 150,
  },
];
