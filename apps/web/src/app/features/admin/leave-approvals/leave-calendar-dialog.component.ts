/**
 * The academic calendar (B10.2) — which days the college is shut.
 *
 * `app/leave_policy.py::working_days` counts a leave span day by day and
 * subtracts only the days this table marks `holiday` at the applicant's own
 * college. That count is what a balance is measured against, so this dialog is
 * the difference between "three days" and "one day" on a request across a long
 * weekend.
 *
 * THERE IS NO WEEKDAY RULE, AND THAT IS DELIBERATE ON THE SERVER. Nothing in
 * REEP records which days the college works, and the applicant's own form
 * already shows them a calendar-day count computed from the two dates. A server
 * that silently subtracted Sundays would disagree with the number the applicant
 * was shown, on a refusal. So `working` exists as a value — "we are open on a
 * day you would assume shut" — and is never subtracted from anything; only
 * `holiday` is. The list says so per row rather than leaving the two words
 * looking symmetrical.
 *
 * ONE COLLEGE AT A TIME. `PUT /admin/leave-calendar/{college_id}` is keyed on
 * the college because a holiday is a college's own decision, and `GET
 * /leaves/calendar` answers the CALLER's college with no id at all. A deployment
 * with one college still picks it, because picking it is how the screen says
 * which one these days belong to.
 */

import { Component, computed, effect, input, output, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import type { LeaveCollegeSummary } from './leave-policy-dialog.component';

interface CalendarDay {
  id: string;
  college_id: string;
  day: string;
  kind: string;
  label: string | null;
}

@Component({
  selector: 'app-leave-calendar-dialog',
  standalone: true,
  imports: [],
  templateUrl: './leave-calendar-dialog.component.html',
  styleUrl: './leave-dialog.scss',
  host: { '(document:keydown.escape)': 'dismissed.emit()' },
})
export class LeaveCalendarDialogComponent {
  /** The colleges, as `GET /admin/leave-policy` already listed them — so this
   *  dialog adds no second way of asking what colleges exist. */
  readonly colleges = input.required<LeaveCollegeSummary[]>();
  readonly dismissed = output<void>();

  readonly titleId = 'leave-calendar-title';

  readonly collegeId = signal<string>('');
  readonly days = signal<CalendarDay[] | null>(null);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly busy = signal(false);

  /** The new-day form. */
  readonly newDay = signal<string>('');
  readonly newKind = signal<string>('holiday');
  readonly newLabel = signal<string>('');

  readonly canAdd = computed(() => this.collegeId().length > 0 && this.newDay().length === 10);

  readonly holidayCount = computed(
    () => (this.days() ?? []).filter((day) => day.kind === 'holiday').length,
  );

  constructor() {
    // The first college, so the dialog opens on something rather than on an
    // empty select the office has to notice.
    //
    // An `effect` and not the constructor body: `colleges` is a REQUIRED input
    // and reading a required input during construction throws, because Angular
    // has not bound it yet. The effect runs after it has, and the guard makes
    // it a one-shot — a later change to the input must not yank the office off
    // the college they are editing.
    effect(() => {
      const first = this.colleges()[0];
      if (first && this.collegeId().length === 0) {
        this.collegeId.set(first.college_id);
        void this.load();
      }
    });
  }

  async chooseCollege(collegeId: string): Promise<void> {
    this.collegeId.set(collegeId);
    await this.load();
  }

  async load(): Promise<void> {
    const collegeId = this.collegeId();
    if (collegeId.length === 0) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/leave-calendar/${encodeURIComponent(collegeId)}`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.error.set(await detailOf(response, 'Could not read the calendar.'));
        this.days.set([]);
        return;
      }
      this.days.set((await response.json()) as CalendarDay[]);
    } catch {
      this.error.set('Could not reach the server.');
      this.days.set([]);
    } finally {
      this.busy.set(false);
    }
  }

  async addDay(): Promise<void> {
    if (!this.canAdd()) return;
    this.busy.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/leave-calendar/${encodeURIComponent(this.collegeId())}`,
        {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            day: this.newDay(),
            kind: this.newKind(),
            label: this.newLabel().trim() || null,
          }),
        },
      );
      if (!response.ok) {
        this.error.set(await detailOf(response, 'Could not record that day.'));
        return;
      }
      this.flash.set(
        this.newKind() === 'holiday'
          ? `${this.newDay()} is recorded as a holiday and will not be counted against an allowance.`
          : `${this.newDay()} is recorded as a working day. Only holidays are subtracted from a leave span, so this changes no count — it is a note that the college is open.`,
      );
      this.newLabel.set('');
      await this.load();
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.busy.set(false);
    }
  }

  async removeDay(day: CalendarDay): Promise<void> {
    if (!window.confirm(`Remove ${day.day} from the calendar?`)) return;
    this.busy.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/leave-calendar/${encodeURIComponent(day.college_id)}/${encodeURIComponent(day.id)}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!response.ok) {
        this.error.set(await detailOf(response, 'Could not remove that day.'));
        return;
      }
      this.flash.set('Day removed.');
      await this.load();
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.busy.set(false);
    }
  }

  selectValue(event: Event): string {
    return (event.target as HTMLSelectElement).value;
  }

  inputValue(event: Event): string {
    return (event.target as HTMLInputElement).value;
  }
}

/** FastAPI's list-shaped `detail`, flattened to the sentence written for a
 *  person. Same helper as `leave.component.ts`'s. */
async function detailOf(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length && typeof detail[0]?.msg === 'string') {
      return String(detail[0].msg).replace(/^Value error,\s*/, '');
    }
  } catch {
    /* fall through */
  }
  return `${fallback} (${response.status})`;
}
