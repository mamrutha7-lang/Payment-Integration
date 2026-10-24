/**
 * The Main Admin's Delete dialog (2026-09-16), shared by the Students,
 * Faculty and Colleges screens.
 *
 * TWO ANSWERS BEHIND ONE BUTTON, and they are drawn as two choices rather than
 * two buttons because they are not degrees of one thing:
 *
 *   - REMOVE keeps the record. `POST /api/admin/{users|students}/{id}/remove`
 *     writes `users.deleted_at`; the person leaves every screen and every door,
 *     every row they own stays, and Restore brings them back. It asks for a
 *     REASON in words, disabling's rule.
 *   - DELETE FOR GOOD is `POST .../delete`: the rows, the files, the
 *     recordings, gone. It asks for the reason AND the six-digit CODE the
 *     office requests from here (`POST /api/admin/deletions/code`), which is
 *     mailed to the office account's own address and works once. The dialog
 *     cannot make that mail arrive faster; it says where it went.
 *
 * A COLLEGE HAS ONLY THE SECOND ANSWER: there is no "remove" for a structure,
 * and its plan carries BLOCKERS — people seated or filed under it, applications
 * waiting — which disable the button with the server's own sentence, because
 * the server refuses the same way whatever this panel shows.
 *
 * WHAT IT PRINTS COMES FROM THE PLAN ENDPOINT, not from this file. `GET
 * .../delete-plan` is the same walk the delete runs, and its `consequences`
 * are the sentences; a dialog that composed its own from the counts would
 * drift from what actually goes the first time a table is added.
 *
 * The chrome is `disable-faculty-dialog.component`'s, copied rather than
 * shared: the design system has no dialog vocabulary yet and a global class is
 * its to add.
 */

import {
  Component,
  ElementRef,
  afterNextRender,
  computed,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

import { environment } from '../../../environments/environment';

/** What is being deleted. `id` is the student id, the user id or the college
 *  id, as the target's own endpoints take it. */
export interface DeleteTarget {
  kind: 'student' | 'faculty' | 'college';
  id: string;
  name: string;
  subLine: string;
}

/** What the dialog tells the screen when it is done. `permanent` says which
 *  answer was taken so the screen can refetch the right list. */
export interface DeleteOutcome {
  permanent: boolean;
  detail: string;
}

/** `AccountDeletePlanOut` / `CollegeDeletePlanOut` in
 *  `app/routers/admin_deletion.py`, the fields this dialog reads. */
interface DeletePlan {
  total_rows: number;
  files?: number;
  mentees_released?: number;
  consequences: string[];
  deletable?: boolean;
  refusal?: string | null;
  blockers?: Record<string, number>;
}

type Mode = 'remove' | 'delete';

/** `_clean_reason` in the router folds whitespace and refuses under three
 *  characters; mirrored so the refusal lands on the field. */
const MINIMUM_REASON_LENGTH = 3;

@Component({
  selector: 'app-admin-delete-dialog',
  standalone: true,
  imports: [],
  templateUrl: './admin-delete-dialog.component.html',
  styleUrl: './admin-delete-dialog.component.scss',
  host: { '(document:keydown.escape)': 'dismiss()' },
})
export class AdminDeleteDialogComponent {
  readonly target = input.required<DeleteTarget>();
  readonly dismissed = output<void>();
  readonly done = output<DeleteOutcome>();

  private readonly panel = viewChild.required<ElementRef<HTMLElement>>('panel');

  readonly titleId = 'admin-delete-title';

  readonly mode = signal<Mode>('remove');
  readonly plan = signal<DeletePlan | null>(null);
  readonly planError = signal<string | null>(null);
  readonly reason = signal('');
  readonly code = signal('');
  readonly codeNote = signal<string | null>(null);
  readonly sendingCode = signal(false);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);

  constructor() {
    afterNextRender(() => {
      this.panel().nativeElement.focus();
      if (this.target().kind === 'college') this.mode.set('delete');
      void this.loadPlan();
    });
  }

  readonly isCollege = computed(() => this.target().kind === 'college');

  readonly headline = computed(() => {
    const t = this.target();
    if (t.kind === 'college') return `Delete ${t.name}`;
    return `Remove or delete ${t.name}`;
  });

  readonly reasonIsUsable = computed(
    () => this.reason().trim().split(/\s+/).join(' ').length >= MINIMUM_REASON_LENGTH,
  );

  readonly codeIsUsable = computed(() => /^\d{6}$/.test(this.code().replace(/\s+/g, '')));

  /** The server's own reason a college cannot go, or null. */
  readonly blocked = computed(() => {
    const plan = this.plan();
    if (plan === null || !this.isCollege()) return null;
    return plan.deletable === false ? (plan.refusal ?? 'This college still has people under it.') : null;
  });

  readonly canConfirm = computed(() => {
    if (this.busy() || this.blocked() !== null) return false;
    if (!this.reasonIsUsable()) return false;
    if (this.mode() === 'delete') return this.codeIsUsable();
    return true;
  });

  readonly confirmLabel = computed(() => {
    if (this.busy()) return 'Working…';
    if (this.mode() === 'remove') return 'Remove from the roster';
    return this.isCollege() ? 'Delete this college for good' : 'Delete for good';
  });

  /** The remove answer's consequences are fixed and stated here; the delete
   *  answer's come from the plan. */
  readonly removeConsequences = computed<string[]>(() => {
    const t = this.target();
    const out = [
      'Off every list, picker and screen at once.',
      'Cannot sign in by any door — REEP password and Google both — and every device is signed out.',
      'Every record is kept exactly as it is: marks, uploads, notes, interviews, leave.',
      'Restore brings the account back with nothing lost.',
    ];
    if (t.kind === 'faculty') {
      out.splice(2, 0, 'Their mentees are released to the unassigned pool and need a new faculty member.');
    }
    return out;
  });

  readonly planConsequences = computed<string[]>(() => this.plan()?.consequences ?? []);

  dismiss(): void {
    if (this.busy()) return;
    this.dismissed.emit();
  }

  setMode(mode: Mode): void {
    this.mode.set(mode);
    this.error.set(null);
  }

  setReason(event: Event): void {
    this.reason.set((event.target as HTMLInputElement).value);
  }

  setCode(event: Event): void {
    this.code.set((event.target as HTMLInputElement).value);
  }

  private planUrl(): string {
    const t = this.target();
    if (t.kind === 'student') return `${environment.apiBase}/admin/students/${t.id}/delete-plan`;
    if (t.kind === 'faculty') return `${environment.apiBase}/admin/users/${t.id}/delete-plan`;
    return `${environment.apiBase}/admin/colleges/${t.id}/delete-plan`;
  }

  private actionUrl(mode: Mode): string {
    const t = this.target();
    const verb = mode === 'remove' ? 'remove' : 'delete';
    if (t.kind === 'student') return `${environment.apiBase}/admin/students/${t.id}/${verb}`;
    if (t.kind === 'faculty') return `${environment.apiBase}/admin/users/${t.id}/${verb}`;
    return `${environment.apiBase}/admin/colleges/${t.id}/delete`;
  }

  private async loadPlan(): Promise<void> {
    this.planError.set(null);
    try {
      const response = await fetch(this.planUrl(), { credentials: 'include' });
      if (!response.ok) {
        this.planError.set(await this.detailOf(response));
        return;
      }
      this.plan.set((await response.json()) as DeletePlan);
    } catch {
      this.planError.set('The server could not be reached, so what a delete would take is not known.');
    }
  }

  /** `POST /api/admin/deletions/code` — to the office's OWN address. */
  async sendCode(): Promise<void> {
    if (this.sendingCode()) return;
    this.sendingCode.set(true);
    this.error.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/admin/deletions/code`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      const body = (await response.json()) as { message: string };
      this.codeNote.set(body.message);
    } catch {
      this.error.set('The server could not be reached. No code was sent.');
    } finally {
      this.sendingCode.set(false);
    }
  }

  async confirm(): Promise<void> {
    if (!this.canConfirm()) return;
    const mode = this.mode();
    const body: Record<string, string> = { reason: this.reason().trim() };
    if (mode === 'delete') body['code'] = this.code().replace(/\s+/g, '');
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(this.actionUrl(mode), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      const result = (await response.json()) as { detail: string };
      this.done.emit({ permanent: mode === 'delete', detail: result.detail });
    } catch {
      this.error.set('The server could not be reached. Nothing has been changed.');
    } finally {
      this.busy.set(false);
    }
  }

  /** FastAPI answers a schema error with `detail` as a LIST; rendered raw it
   *  reads "[object Object]". Its refusals here name the reason — a wrong
   *  code, people still under a college, the office's own account — so the
   *  server's sentence is kept wherever there is one. */
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
    return `The request was refused (${response.status}).`;
  }
}
