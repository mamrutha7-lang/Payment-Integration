/**
 * Leave policy — the office's allowances, per person and per department (B10.2).
 *
 * `02-admin-console-spec.md:56` asks for policy "per department (balances per
 * kind, academic year)", and that whole sentence is `leave_balances`: one row
 * per (account, printed option, academic year), carrying what was granted and
 * what has been taken. This dialog is the only screen that writes them.
 *
 * THE YEAR IS A SPELLING, AND IT IS THE ONE THING THAT CAN SILENTLY BREAK THIS.
 * `app/leave_policy.py::academic_year_for` produces "2026-27" and the submit
 * path looks a row up by that exact string; a miss switches the balance check
 * OFF rather than refusing, which is the safe failure but an invisible one. So
 * the field is PRE-FILLED from `GET /admin/leave-policy`, which returns the
 * server's own spelling for today, and the dialog says in words what a
 * different spelling does. Typing "2026-2027" here is not an error and never
 * will be — it is simply an allowance no request will ever be measured against.
 *
 * THE BULK WRITE CREATES AND NEVER OVERWRITES, and the server is what enforces
 * that; this screen reports both numbers it answers with ("14 recorded, 9
 * already had one") rather than a single "done". A bulk write that reset
 * `consumed_days` in December would hand back days a whole department had
 * already taken, and the one thing worse than not saying so is saying "saved".
 *
 * DELETE IS NOT "SET TO ZERO" AND THE CONFIRM SAYS SO. With no row the submit
 * path stops checking that person's requests of that kind altogether; with a
 * row at zero every one of them is refused. Two opposite outcomes behind one
 * bin icon, which is why the sentence is spelled out before it is pressed.
 *
 * MAIN ADMIN ONLY. Every write here is `require_admin` server-side, and the
 * department picker is `admin.institution`. The button that opens this dialog
 * is disabled for anybody else with the reason on it, rather than opening a
 * dialog whose every control answers 403.
 */

import { Component, computed, output, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';

/** The five printed options on the college's form, in its order. */
const KINDS = [
  { id: 'CASUAL', label: 'Casual' },
  { id: 'PERMISSION', label: 'Permission' },
  { id: 'OOD', label: 'OOD' },
  { id: 'RH', label: 'RH' },
  { id: 'LOP', label: 'LOP' },
] as const;

const EVERY_KIND = '';
const EVERY_DEPARTMENT = '';

export interface LeaveCollegeSummary {
  college_id: string;
  college_name: string | null;
  holidays: number;
  working_days: number;
}

interface PolicySheet {
  academic_year: string;
  kinds: string[];
  balances_recorded: number;
  people_with_balances: number;
  colleges: LeaveCollegeSummary[];
}

interface Balance {
  id: string;
  user_id: string;
  user_name: string | null;
  user_email: string | null;
  user_role: string | null;
  kind: string;
  academic_year: string;
  entitled_days: number;
  consumed_days: number;
  remaining_days: number;
}

interface DepartmentOption {
  id: string;
  label: string;
}

@Component({
  selector: 'app-leave-policy-dialog',
  standalone: true,
  imports: [],
  templateUrl: './leave-policy-dialog.component.html',
  styleUrl: './leave-dialog.scss',
  host: { '(document:keydown.escape)': 'dismissed.emit()' },
})
export class LeavePolicyDialogComponent {
  /** Emitted on close. The parent reloads its own sheet from it, because a
   *  bulk write changes the counts the policy card prints. */
  readonly dismissed = output<void>();

  readonly titleId = 'leave-policy-title';
  readonly kinds = KINDS;
  readonly everyKind = EVERY_KIND;
  readonly everyDepartment = EVERY_DEPARTMENT;

  readonly sheet = signal<PolicySheet | null>(null);
  readonly balances = signal<Balance[] | null>(null);
  readonly departments = signal<DepartmentOption[]>([]);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly busy = signal(false);

  /** The year every read and write on this dialog uses. */
  readonly year = signal<string>('');
  readonly kindFilter = signal<string>(EVERY_KIND);
  readonly departmentFilter = signal<string>(EVERY_DEPARTMENT);

  /** The bulk form. */
  readonly bulkDepartment = signal<string>('');
  readonly bulkKind = signal<string>('CASUAL');
  readonly bulkDays = signal<string>('12');
  readonly bulkStaff = signal(true);
  readonly bulkStudents = signal(false);

  /** Per-row edits, keyed on the balance id — a row is only written when its
   *  own Save is pressed, so an abandoned edit changes nothing. */
  readonly entitledEdits = signal<Record<string, string>>({});
  readonly consumedEdits = signal<Record<string, string>>({});

  readonly canBulk = computed(
    () =>
      this.bulkDepartment().length > 0 &&
      this.year().trim().length >= 4 &&
      Number.isFinite(Number(this.bulkDays())) &&
      Number(this.bulkDays()) >= 0 &&
      (this.bulkStaff() || this.bulkStudents()),
  );

  constructor() {
    void this.load();
  }

  // ------------------------------------------------------------- reading --

  private async load(): Promise<void> {
    this.busy.set(true);
    try {
      const response = await fetch(`${environment.apiBase}/admin/leave-policy`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.error.set(await detailOf(response, 'Could not read the leave policy.'));
        this.balances.set([]);
        return;
      }
      const sheet = (await response.json()) as PolicySheet;
      this.sheet.set(sheet);
      // The SERVER's spelling of this year, which is the one the submit path
      // looks a row up by. Never composed here.
      if (this.year().length === 0) this.year.set(sheet.academic_year);
      await Promise.all([this.loadBalances(), this.loadDepartments()]);
    } catch {
      this.error.set('Could not reach the server.');
      this.balances.set([]);
    } finally {
      this.busy.set(false);
    }
  }

  private async loadDepartments(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/departments`, {
        credentials: 'include',
      });
      if (!response.ok) return;
      const rows = (await response.json()) as { id: string; label: string }[];
      this.departments.set(rows.map((row) => ({ id: row.id, label: row.label })));
    } catch {
      // A missing picker is a bulk form that cannot be used; the per-person
      // table still works, and the error line is reserved for a failed WRITE.
    }
  }

  async loadBalances(): Promise<void> {
    this.busy.set(true);
    try {
      const query = new URLSearchParams({ academic_year: this.year().trim() });
      if (this.kindFilter() !== EVERY_KIND) query.set('kind', this.kindFilter());
      if (this.departmentFilter() !== EVERY_DEPARTMENT) {
        query.set('department_id', this.departmentFilter());
      }
      const response = await fetch(
        `${environment.apiBase}/admin/leave-balances?${query.toString()}`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.error.set(await detailOf(response, 'Could not read the allowances.'));
        this.balances.set([]);
        return;
      }
      this.balances.set((await response.json()) as Balance[]);
      this.entitledEdits.set({});
      this.consumedEdits.set({});
    } catch {
      this.error.set('Could not reach the server.');
      this.balances.set([]);
    } finally {
      this.busy.set(false);
    }
  }

  // ------------------------------------------------------------- writing --

  entitledOf(row: Balance): string {
    return this.entitledEdits()[row.id] ?? String(row.entitled_days);
  }

  consumedOf(row: Balance): string {
    return this.consumedEdits()[row.id] ?? String(row.consumed_days);
  }

  setEntitled(row: Balance, value: string): void {
    this.entitledEdits.update((edits) => ({ ...edits, [row.id]: value }));
  }

  setConsumed(row: Balance, value: string): void {
    this.consumedEdits.update((edits) => ({ ...edits, [row.id]: value }));
  }

  isEdited(row: Balance): boolean {
    return (
      this.entitledOf(row) !== String(row.entitled_days) ||
      this.consumedOf(row) !== String(row.consumed_days)
    );
  }

  async saveRow(row: Balance): Promise<void> {
    const entitled = Number(this.entitledOf(row));
    const consumed = Number(this.consumedOf(row));
    if (!Number.isInteger(entitled) || entitled < 0 || !Number.isInteger(consumed) || consumed < 0) {
      this.error.set('Days must be whole numbers, and neither can be negative.');
      return;
    }
    await this.write('PUT', '/admin/leave-balances', {
      user_id: row.user_id,
      kind: row.kind,
      academic_year: row.academic_year,
      entitled_days: entitled,
      consumed_days: consumed,
    });
  }

  async deleteRow(row: Balance): Promise<void> {
    const who = row.user_name || row.user_email || 'this person';
    const agreed = window.confirm(
      `Remove ${who}'s ${row.kind} allowance for ${row.academic_year}?\n\n` +
        'This is NOT the same as setting it to zero. With no row REEP stops ' +
        'checking their requests of this kind at all; with a row at zero every ' +
        'one of them is refused.',
    );
    if (!agreed) return;
    await this.write('DELETE', `/admin/leave-balances/${row.id}`, null);
  }

  async recordForDepartment(): Promise<void> {
    if (!this.canBulk()) return;
    this.busy.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/admin/leave-balances/bulk`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          department_id: this.bulkDepartment(),
          kind: this.bulkKind(),
          academic_year: this.year().trim(),
          entitled_days: Number(this.bulkDays()),
          include_staff: this.bulkStaff(),
          include_students: this.bulkStudents(),
        }),
      });
      if (!response.ok) {
        this.error.set(await detailOf(response, 'Could not record the allowances.'));
        return;
      }
      const result = (await response.json()) as { created: number; skipped: number };
      // BOTH numbers, always. "Saved" over a run that wrote nothing because
      // everybody already had a row is the report that hides the design.
      this.flash.set(
        `${result.created} allowance${result.created === 1 ? '' : 's'} recorded. ` +
          `${result.skipped} ${result.skipped === 1 ? 'person' : 'people'} already had one for ` +
          `${this.bulkKind()} ${this.year().trim()} and were left exactly as they were.`,
      );
      await this.loadBalances();
      await this.refreshSheet();
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.busy.set(false);
    }
  }

  private async write(method: 'PUT' | 'DELETE', path: string, body: unknown): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}${path}`, {
        method,
        credentials: 'include',
        headers: body === null ? undefined : { 'Content-Type': 'application/json' },
        body: body === null ? undefined : JSON.stringify(body),
      });
      if (!response.ok) {
        this.error.set(await detailOf(response, 'Could not save that allowance.'));
        return;
      }
      this.flash.set(method === 'DELETE' ? 'Allowance removed.' : 'Allowance saved.');
      await this.loadBalances();
      await this.refreshSheet();
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.busy.set(false);
    }
  }

  private async refreshSheet(): Promise<void> {
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/leave-policy?academic_year=${encodeURIComponent(this.year().trim())}`,
        { credentials: 'include' },
      );
      if (!response.ok) return;
      this.sheet.set((await response.json()) as PolicySheet);
    } catch {
      // The counts stay as they were; the table below is the live half.
    }
  }

  // ------------------------------------------------------------- helpers --

  selectValue(event: Event): string {
    return (event.target as HTMLSelectElement).value;
  }

  inputValue(event: Event): string {
    return (event.target as HTMLInputElement).value;
  }

  checked(event: Event): boolean {
    return (event.target as HTMLInputElement).checked;
  }

  /** The server's own spelling of today's academic year, for the hint. */
  serverYear(): string {
    return this.sheet()?.academic_year ?? '';
  }

  yearIsServerSpelling(): boolean {
    const server = this.serverYear();
    return server.length === 0 || this.year().trim() === server;
  }

  roleWord(row: Balance): string {
    switch (row.user_role) {
      case 'STUDENT':
        return 'Student';
      case 'MENTOR':
        return 'Faculty';
      case 'ADMIN':
        return 'Main Admin';
      case 'ALUMNI':
        return 'Alumni';
      default:
        return 'Not on record';
    }
  }
}

/** The server's own sentence, whichever shape it arrives in — FastAPI answers a
 *  schema refusal with `detail` as a LIST, and rendering that raw is how a form
 *  says "[object Object]". Same helper as `leave.component.ts`'s. */
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
