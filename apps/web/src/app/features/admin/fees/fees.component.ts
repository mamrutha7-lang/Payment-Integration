/**
 * Student fees — the office raises them here and watches them get paid.
 *
 * The screen is a front for endpoints that already exist (`routers/fees.py`):
 * `POST /admin/fees` raises, `GET /admin/fees` lists, `POST /admin/fees/{id}/cancel`
 * withdraws an unpaid one. Main Admin alone, as the API is (`require_admin`), so
 * the route guard and the sidebar row are `ADMIN` / `mainAdminOnly` and are
 * filters, never the gate.
 *
 * The amount is typed in rupees and sent as rupees: the server converts to paise
 * (`_paise`), so no float arithmetic happens in this file. A fee targets EITHER
 * a whole batch (`cohort_id`) OR named students (`student_ids`), never both —
 * the API refuses both and neither, and the form cannot build either.
 */

import { Component, computed, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';

interface Cohort {
  id: string;
  display_label: string;
  student_count: number;
}

interface RosterStudent {
  student_id: string;
  name: string;
  usn: string | null;
  batch: string | null;
}

interface FeeRow {
  id: string;
  title: string;
  amount: string;
  due_date: string | null;
  status: 'PENDING' | 'PAID' | 'CANCELLED';
  overdue: boolean;
  paid_at: string | null;
  student_name: string;
  usn: string | null;
}

type Target = 'batch' | 'students';

/** The API caps `student_ids` here; the picker stops one short of a refusal. */
const MAX_STUDENTS = 2000;

async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
    // FastAPI answers a schema error with `detail` as a LIST of objects.
    if (Array.isArray(body?.detail) && body.detail[0]?.msg) return String(body.detail[0].msg);
  } catch {
    /* fall through */
  }
  return fallback;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const at = new Date(iso);
  return Number.isNaN(at.getTime())
    ? '—'
    : at.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

@Component({
  selector: 'app-admin-fees',
  standalone: true,
  templateUrl: './fees.component.html',
  styleUrl: './fees.component.scss',
})
export class AdminFeesComponent {
  // ---- the form ----
  readonly title = signal('');
  readonly amount = signal('');
  readonly dueDate = signal('');
  readonly target = signal<Target>('batch');
  readonly cohortId = signal('');
  readonly cohorts = signal<Cohort[]>([]);

  /** Students found by the search, and the ones ticked (kept across searches). */
  readonly query = signal('');
  readonly found = signal<RosterStudent[]>([]);
  readonly searching = signal(false);
  readonly picked = signal<Map<string, RosterStudent>>(new Map());
  readonly pickedList = computed(() => [...this.picked().values()]);

  readonly raising = signal(false);
  readonly formError = signal<string | null>(null);
  readonly raised = signal<string | null>(null);

  // ---- the list ----
  readonly fees = signal<FeeRow[]>([]);
  readonly statusFilter = signal('');
  readonly loading = signal(false);
  readonly listError = signal<string | null>(null);
  readonly loaded = signal(false);
  /** The fee whose Cancel was pressed once; the second press does it. */
  readonly confirming = signal<string | null>(null);
  readonly cancelling = signal<string | null>(null);

  readonly formatDate = formatDate;

  readonly chosenBatch = computed(() => this.cohorts().find((c) => c.id === this.cohortId()));
  readonly recipientCount = computed(() =>
    this.target() === 'batch' ? (this.chosenBatch()?.student_count ?? 0) : this.picked().size,
  );
  readonly canRaise = computed(
    () =>
      !this.raising() &&
      this.title().trim().length > 0 &&
      Number(this.amount()) > 0 &&
      (this.target() === 'batch' ? !!this.cohortId() : this.picked().size > 0),
  );

  constructor() {
    void this.loadCohorts();
    void this.load();
  }

  private async loadCohorts(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/admin/cohorts`, { credentials: 'include' });
      if (res.ok) this.cohorts.set((await res.json()) as Cohort[]);
    } catch {
      /* the batch picker stays empty; "Chosen students" still works */
    }
  }

  async load(): Promise<void> {
    if (this.loading()) return;
    this.loading.set(true);
    this.listError.set(null);
    const query = new URLSearchParams({ limit: '500' });
    if (this.statusFilter()) query.set('status_filter', this.statusFilter());
    try {
      const res = await fetch(`${environment.apiBase}/admin/fees?${query}`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.listError.set(await detailOf(res, `Could not read the fees (${res.status}).`));
        return;
      }
      this.fees.set((await res.json()) as FeeRow[]);
      this.loaded.set(true);
    } catch {
      this.listError.set('Could not reach the server.');
    } finally {
      this.loading.set(false);
    }
  }

  async search(): Promise<void> {
    const q = this.query().trim();
    if (!q || this.searching()) return;
    this.searching.set(true);
    this.formError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/admin/students?q=${encodeURIComponent(q)}`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.formError.set(await detailOf(res, `Could not search students (${res.status}).`));
        return;
      }
      this.found.set(((await res.json()) as RosterStudent[]).slice(0, 50));
    } catch {
      this.formError.set('Could not reach the server.');
    } finally {
      this.searching.set(false);
    }
  }

  isPicked(id: string): boolean {
    return this.picked().has(id);
  }

  toggle(student: RosterStudent): void {
    const next = new Map(this.picked());
    if (next.has(student.student_id)) next.delete(student.student_id);
    else if (next.size < MAX_STUDENTS) next.set(student.student_id, student);
    this.picked.set(next);
  }

  async raise(): Promise<void> {
    if (!this.canRaise()) return;
    this.raising.set(true);
    this.formError.set(null);
    this.raised.set(null);
    const body: Record<string, unknown> = {
      title: this.title().trim(),
      amount: this.amount().trim(),
    };
    if (this.dueDate()) body['due_date'] = this.dueDate();
    if (this.target() === 'batch') body['cohort_id'] = this.cohortId();
    else body['student_ids'] = [...this.picked().keys()];
    try {
      const res = await fetch(`${environment.apiBase}/admin/fees`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        this.formError.set(await detailOf(res, `Could not raise the fee (${res.status}).`));
        return;
      }
      const made = ((await res.json()) as { created: number }).created;
      this.raised.set(`Raised “${this.title().trim()}” for ${made} student${made === 1 ? '' : 's'}.`);
      this.title.set('');
      this.amount.set('');
      this.dueDate.set('');
      this.picked.set(new Map());
      await this.load();
    } catch {
      this.formError.set('Could not reach the server.');
    } finally {
      this.raising.set(false);
    }
  }

  async cancel(fee: FeeRow): Promise<void> {
    if (this.confirming() !== fee.id) {
      this.confirming.set(fee.id);
      return;
    }
    this.cancelling.set(fee.id);
    this.listError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/admin/fees/${fee.id}/cancel`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) {
        this.listError.set(await detailOf(res, `Could not cancel the fee (${res.status}).`));
        return;
      }
      this.confirming.set(null);
      await this.load();
    } catch {
      this.listError.set('Could not reach the server.');
    } finally {
      this.cancelling.set(null);
    }
  }
}
