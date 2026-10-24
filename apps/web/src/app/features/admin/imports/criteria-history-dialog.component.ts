/**
 * Placement criteria · history — every set of gates ever written (B8.2).
 *
 * `GET /api/admin/criteria/history` is the whole table, newest first, narrowed
 * by `?course_id=` / `?college_id=` to one rung. The endpoint's own docstring
 * is the reason this screen exists: `POST /api/admin/criteria` writes a NEW ROW
 * every time and deactivates the one it supersedes, because a student who was
 * told in March that they did not qualify must still be explicable in
 * September. Editing in place would leave `updated_at` as the only trace.
 *
 * SUPERSEDED ROWS ARE IN THE LIST AND MARKED, never filtered out — a history
 * that hid them would be a list with one row in it. "Live" and "Superseded" are
 * a chip with a word in it as well as a colour, because that distinction is the
 * only thing separating the rule that governs a verdict today from the one that
 * governed it last term.
 *
 * THE RUNG IS SHOWN PER ROW. A programme-wide row and a course row can both be
 * live at once — they are different rungs of the chain `app/criteria.py`
 * resolves — so a list that did not say which would read as two contradictory
 * live rules.
 */

import { DatePipe } from '@angular/common';
import { Component, computed, input, output, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import {
  CRITERIA_SOURCE_LABELS,
  NOT_READABLE,
  type PlacementCriteriaHistoryRow,
} from './import-dataset';

@Component({
  selector: 'app-criteria-history-dialog',
  standalone: true,
  imports: [DatePipe],
  templateUrl: './criteria-history-dialog.component.html',
  styleUrl: './imports-dialog.scss',
  host: { '(document:keydown.escape)': 'dismissed.emit()' },
})
export class CriteriaHistoryDialogComponent {
  /** The course whose rung to narrow to, or '' for every rung ever written. */
  readonly courseId = input<string>('');
  /** What the picker on the screen behind calls that course, for the subtitle. */
  readonly courseLabel = input<string>('');

  readonly dismissed = output<void>();

  readonly titleId = 'criteria-history-title';
  readonly notReadable = NOT_READABLE;

  readonly rows = signal<PlacementCriteriaHistoryRow[] | null>(null);
  readonly error = signal<string | null>(null);

  constructor() {
    void this.load();
  }

  /** Null while the request is in flight, and only then — a failed read leaves
   *  it null too, but `error()` is what the template branches on first, so an
   *  empty table is never drawn over a request that did not answer. */
  readonly isLoading = computed(() => this.rows() === null && this.error() === null);
  readonly isEmpty = computed(() => (this.rows() ?? []).length === 0 && this.error() === null);

  rungLabel(source: string | null | undefined): string {
    if (!source) return 'Rung not recorded';
    return CRITERIA_SOURCE_LABELS[source] ?? source;
  }

  private async load(): Promise<void> {
    const course = this.courseId();
    const query = course ? `?course_id=${encodeURIComponent(course)}` : '';
    try {
      const response = await fetch(`${environment.apiBase}/admin/criteria/history${query}`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      this.rows.set((await response.json()) as PlacementCriteriaHistoryRow[]);
    } catch {
      this.error.set('Could not reach the server.');
    }
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
    if (response.status === 403) {
      return 'The criteria history needs the Analytics function as well as Upload spreadsheets. Ask the Main Admin to grant admin.analytics in Who can do what.';
    }
    return `The request was refused (${response.status}).`;
  }
}
