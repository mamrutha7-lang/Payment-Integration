import { createGrid, type GridApi } from 'ag-grid-community';

import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { DEFAULT_ROSTER_COLUMN, ROSTER_COLUMNS } from './roster-grid';
import type { RosterRow } from './roster-row';

/**
 * The quick filter is the grid's own, so it can only match what the columns
 * hand it. The Student cell draws the name AND the address, and the column's
 * value is the name alone: an address typed into "Quick filter…" hid every
 * row, including the one the server had just found by that address.
 */
function row(studentId: string, name: string, email: string, usn: string): RosterRow {
  return {
    studentId,
    userId: `u-${studentId}`,
    name,
    email,
    initials: 'TS',
    usn,
    cohortId: null,
    batchName: null,
    departmentId: null,
    departmentName: null,
    courseId: null,
    specializationId: null,
    specializationCode: null,
    specializationColour: 'var(--seq-light)',
    secondSpecializationId: null,
    secondSpecializationCode: null,
    secondSpecializationColour: 'var(--seq-light)',
    semester: 2,
    stageKey: 'EXCEL',
    stageLabel: 'Excel',
    mentorUserId: null,
    mentorName: null,
    statusLabel: 'Active',
    statusTone: 'good',
    lastLoginAt: '2026-09-01T00:00:00Z',
    isRemoved: false,
    deletedAt: null,
    deleteReason: null,
  };
}

describe('the roster quick filter', () => {
  let host: HTMLElement;
  let api: GridApi<RosterRow>;

  beforeEach(() => {
    registerReepGrid();
    host = document.createElement('div');
    document.body.appendChild(host);
    api = createGrid<RosterRow>(host, {
      columnDefs: ROSTER_COLUMNS,
      defaultColDef: DEFAULT_ROSTER_COLUMN,
      getRowId: (params) => params.data.studentId,
      rowData: [
        row('s1', 'Test Student', 'student@bgscet.ac.in', '1BG24MBA001'),
        row('s2', 'Asha Rao', 'asha.rao@bgscet.ac.in', '1BG24MBA002'),
      ],
    });
  });

  afterEach(() => {
    api.destroy();
    host.remove();
  });

  const shown = (): string[] => {
    const ids: string[] = [];
    api.forEachNodeAfterFilter((node) => {
      if (node.data) ids.push(node.data.studentId);
    });
    return ids;
  };

  it('finds a student by the college address the Student cell draws', () => {
    api.setGridOption('quickFilterText', 'student@bgscet.ac.in');
    expect(shown()).toEqual(['s1']);
  });

  it('still finds a student by name and by USN', () => {
    api.setGridOption('quickFilterText', 'Asha');
    expect(shown()).toEqual(['s2']);
    api.setGridOption('quickFilterText', '1BG24MBA001');
    expect(shown()).toEqual(['s1']);
  });
});
