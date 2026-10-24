/**
 * Student records — the list of students this account may open (2026-10-15).
 *
 * The Main Admin finds a student on the roster. A faculty member the office
 * gave "View student records" for a batch, department or college has no
 * roster (that is the editor, a different permission), so this list is how
 * they find the students the grant covers. `GET /api/admin/student-records`
 * returns exactly the set the record page opens for: the students the grant
 * reaches and the faculty member's own mentees.
 */

import { Component, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import type { AdminStudentOut } from '../student-detail/student-record';

@Component({
  selector: 'app-student-records',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="panel">
      <div class="dt-header">
        <div>
          <h1 class="dt-title">Student records</h1>
          <div class="dt-sub">The students you can open. Read only.</div>
        </div>
      </div>

      <div class="card">
        <div class="field search">
          <label for="records-search">Find a student</label>
          <input
            #search
            id="records-search"
            type="search"
            placeholder="Name, USN or email"
            (input)="onSearch(search.value)"
          />
        </div>

        @if (error()) {
          <div class="notice" role="alert">
            <span class="icon" aria-hidden="true">error</span><span>{{ error() }}</span>
          </div>
        } @else if (rows() === null) {
          <div class="loading-line" role="status">Reading the list…</div>
        } @else if (rows()!.length === 0) {
          <div class="empty">
            <span class="icon" aria-hidden="true">person</span>
            {{ query() ? 'Nobody matches that search.' : 'No students are open to you yet.' }}
          </div>
        } @else {
          <div class="table-scroll">
            <table class="dt-table">
              <caption class="sr-only">Students you can open</caption>
              <thead>
                <tr>
                  <th scope="col">Student</th>
                  <th scope="col">USN</th>
                  <th scope="col">Batch</th>
                  <th scope="col">Department</th>
                  <th scope="col">Faculty mentor</th>
                </tr>
              </thead>
              <tbody>
                @for (row of rows(); track row.student_id) {
                  <tr>
                    <td>
                      <a [routerLink]="['/admin/students', row.student_id]">{{ row.name }}</a>
                    </td>
                    <td class="num">{{ row.usn ?? '—' }}</td>
                    <td>{{ row.batch ?? 'No batch yet' }}</td>
                    <td>{{ row.department ?? '—' }}</td>
                    <td>{{ row.mentor_name ?? 'Not assigned' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          @if (capped()) {
            <p class="more">Showing the first {{ limit }}. Search to find somebody else.</p>
          }
        }
      </div>
    </div>
  `,
  styles: `
    .search {
      max-width: 420px;
      margin-bottom: 12px;
    }
    .num {
      font-variant-numeric: tabular-nums;
    }
    .more {
      margin: 10px 0 0;
      font-size: 12.5px;
      color: var(--muted);
    }
  `,
})
export class StudentRecordsComponent {
  /** The server's cap; a full page means there may be more. */
  readonly limit = 200;

  readonly rows = signal<AdminStudentOut[] | null>(null);
  readonly error = signal<string | null>(null);
  readonly query = signal('');
  readonly capped = computed(() => (this.rows()?.length ?? 0) >= this.limit);

  private timer: ReturnType<typeof setTimeout> | null = null;
  private run = 0;

  constructor() {
    void this.load();
  }

  onSearch(value: string): void {
    this.query.set(value.trim());
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.load(), 250);
  }

  private async load(): Promise<void> {
    const run = ++this.run;
    const q = this.query();
    const url = `${environment.apiBase}/admin/student-records${q ? `?q=${encodeURIComponent(q)}` : ''}`;
    try {
      const response = await fetch(url, { credentials: 'include' });
      if (run !== this.run) return;
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
        this.error.set(
          typeof body.detail === 'string' ? body.detail : `The list could not be read (${response.status}).`,
        );
        return;
      }
      this.error.set(null);
      this.rows.set((await response.json()) as AdminStudentOut[]);
    } catch {
      if (run === this.run) this.error.set('The server could not be reached.');
    }
  }
}
