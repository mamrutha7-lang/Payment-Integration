import { createGrid, type GridApi } from 'ag-grid-community';

import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { toJobPostingRow, type JobPostingApiRow, type JobPostingRow } from './job-posting-row';
import { DEFAULT_JOBS_COLUMN, JOBS_COLUMNS } from './jobs-grid';

/**
 * The search box over the sheet is the grid's quick filter, and its label
 * promises "role, company or location". The Posting column is keyed on the
 * title and draws the company and location with a renderer, which the quick
 * filter never reads — so "Acme Capital" answered "Rows: 0" over a sheet that
 * listed a posting at Acme Capital. These run the real grid over the real
 * columns, because the defect lived in what AG Grid does with them.
 */
function posting(title: string, company: string, location: string | null): JobPostingApiRow {
  return {
    id: title,
    title,
    company,
    degree_level: 'PG',
    location,
    apply_url: null,
    required_skills: [],
    posted_on: '2026-09-01',
    closes_on: null,
    min_cgpa: null,
    max_live_backlogs: null,
    applicants: 0,
    college_id: null,
    college_name: null,
    course_id: null,
    course_name: null,
    tracks: [],
    status: 'open',
  };
}

describe('the jobs sheet search', () => {
  let host: HTMLElement;
  let grid: GridApi<JobPostingRow>;

  beforeEach(() => {
    registerReepGrid();
    host = document.createElement('div');
    document.body.append(host);
    grid = createGrid<JobPostingRow>(host, {
      columnDefs: JOBS_COLUMNS,
      defaultColDef: DEFAULT_JOBS_COLUMN,
      rowData: [
        posting('Financial Analyst', 'Acme Capital', 'Bengaluru'),
        posting('BI Developer', 'Northwind Data', 'Pune'),
        posting('HR Associate', 'Contoso People', null),
      ].map(toJobPostingRow),
    });
  });

  afterEach(() => {
    grid.destroy();
    host.remove();
  });

  function listedFor(search: string): string[] {
    grid.setGridOption('quickFilterText', search);
    const titles: string[] = [];
    grid.forEachNodeAfterFilter((node) => {
      if (node.data) titles.push(node.data.title);
    });
    return titles;
  }

  it('finds a posting by its company', () => {
    expect(listedFor('Acme Capital')).toEqual(['Financial Analyst']);
  });

  it('finds a posting by its location', () => {
    expect(listedFor('Bengaluru')).toEqual(['Financial Analyst']);
    expect(listedFor('pune')).toEqual(['BI Developer']);
  });

  it('still finds a posting by its role, and one with no location by its company', () => {
    expect(listedFor('Analyst')).toEqual(['Financial Analyst']);
    expect(listedFor('Contoso')).toEqual(['HR Associate']);
  });
});
