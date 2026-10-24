/**
 * Graduate batch — the dialog from `design/admin/GraduateBatch.html` (spec §26).
 *
 * IT WRITES NOW (B4.4), AND IT SHOWS ITS WORKING FIRST.
 *
 *   POST /api/admin/cohorts/{id}/graduate?dry_run=true     the preflight
 *   POST /api/admin/cohorts/{id}/graduate                  the graduation
 *   POST /api/admin/cohorts/{id}/ungraduate?dry_run=true   the reversal's own
 *   POST /api/admin/cohorts/{id}/ungraduate                take it back
 *
 * WHAT GRADUATION ACTUALLY DOES, said on the dialog because it is four writes
 * and not one: `students.status` becomes GRADUATED (who is COUNTED as a current
 * student), `users.role` becomes ALUMNI (what the ACCOUNT may reach),
 * `token_version` is advanced so the live session is dropped on its next
 * request, and a history row records when, by whom and why. No student row is
 * deleted — it is the record of what they did here, and deleting it would take
 * their marks, badges and interview history with it.
 *
 * AND NO ALUMNI PROFILE IS CREATED. The previous version of this dialog said
 * "each student gets an alumni profile linked to this record" as a green tick,
 * and that was wrong in a way that matters: `alumni_profiles.company` is NOT
 * NULL, and mere ROW EXISTENCE is what `GET /api/alumni/profile`'s `created:`
 * flag reports — which is the whole trigger for the first-login create-profile
 * form. A row minted here would either invent a company or suppress that form
 * for ever. The graduate meets the form, which is what it is for.
 *
 * THE REVERSAL IS A REAL BUTTON AND NOT A SENTENCE. The preflight's own
 * "Reversible · this can be undone for 30 days" check is a promise, and until
 * this dialog offered the reversal there was nothing in the product that could
 * keep it. Graduating the wrong batch signs sixty people out and closes every
 * screen they use, and the console has no other way to put a role back. So when
 * the preflight reports that everybody here has already graduated, this dialog
 * asks the reversal's own dry run and offers it — including its 409, which
 * fires on the dry run too, so "these are older than 30 days and can no longer
 * be reversed from the console" is shown before the press rather than after it.
 *
 * WHAT THE REVERSAL CANNOT TAKE BACK is an `alumni_profiles` row a graduate has
 * filled in since. That row is theirs, they typed it, and it is KEPT — but
 * after the reversal `require_alumni` refuses them and the profile is out of
 * reach until they graduate again. The server counts those rows and this dialog
 * shows the count, because the office should not learn it from a support call.
 *
 * THIS DIALOG DOES NOT REFRESH THE ROSTER BEHIND IT — it emits `completed` and
 * the parent does. Students & batches binds it to `onBatchWriteCompleted()`,
 * which re-reads the grid AND the promotion history card; the result panel's
 * sentence says so. A second parent that mounts this dialog must bind
 * `completed` too, or its screen sits at the read it took before the write.
 */

import { Component, OnInit, computed, input, output, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { plural } from '../../../shared/text/plural.pipe';
import type { BatchSummary } from './batch-summary';

/** `admin_promotion.PreflightCheck`. */
interface PreflightCheck {
  key: string;
  label: string;
  status: 'ok' | 'warn' | 'blocked' | 'unavailable';
  detail: string;
}

/** `admin_promotion.GraduateStudentOut` — used by both the graduation and its
 *  reversal, which is why `role_before` / `role_after` are stated rather than
 *  assumed: the same row reads STUDENT → ALUMNI one way and ALUMNI → STUDENT
 *  the other. */
interface GraduateStudentOut {
  student_id: string;
  user_id: string;
  name: string;
  usn: string | null;
  semester: number;
  role_before: string;
  role_after: string;
  blocked_reason: string | null;
}

/** `admin_promotion.GraduateOut`. */
interface GraduateOut {
  cohort_id: string;
  batch: string;
  dry_run: boolean;
  affected: number;
  /** Students who have already graduated. On a batch where this is the whole
   *  roster, the act on offer is the REVERSAL and not another graduation. */
  skipped: number;
  cohort_status: string;
  checks: PreflightCheck[];
  students: GraduateStudentOut[];
}

/** `admin_promotion.UngraduateOut`. */
interface UngraduateOut {
  cohort_id: string;
  batch: string;
  dry_run: boolean;
  affected: number;
  cohort_status: string;
  /** Profiles these graduates typed themselves. Kept, and unreachable to them
   *  until they graduate again. */
  alumni_profiles_kept: number;
  students: GraduateStudentOut[];
}

const CHECK_ICONS: Record<PreflightCheck['status'], string> = {
  ok: 'check_circle',
  warn: 'warning',
  blocked: 'block',
  unavailable: 'hourglass_top',
};

const CHECK_TONES: Record<PreflightCheck['status'], string> = {
  ok: 'good',
  warn: 'warn',
  blocked: 'warn',
  unavailable: 'pending',
};

@Component({
  selector: 'app-graduate-batch-dialog',
  standalone: true,
  imports: [],
  templateUrl: './graduate-batch-dialog.component.html',
  styleUrls: ['./batch-dialog.scss', './graduate-batch-dialog.component.scss'],
  host: { '(document:keydown.escape)': 'dismissed.emit()' },
})
export class GraduateBatchDialogComponent implements OnInit {
  readonly batch = input.required<BatchSummary>();
  readonly dismissed = output<void>();
  /** Emitted once a graduation or a reversal has been written, so a parent can
   *  refetch the roster it read before it. */
  readonly completed = output<void>();

  readonly titleId = 'graduate-batch-title';

  readonly effectiveOn = signal(new Date().toISOString().slice(0, 10));
  readonly reason = signal('');

  readonly preview = signal<GraduateOut | null>(null);
  readonly reversal = signal<UngraduateOut | null>(null);
  /** Why the reversal cannot be offered, in the server's words — almost always
   *  its 409: the graduations are older than the 30-day window. */
  readonly reversalRefusal = signal<string | null>(null);

  readonly outcome = signal<GraduateOut | null>(null);
  readonly reversed = signal<UngraduateOut | null>(null);

  readonly checking = signal(true);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);

  private runId = 0;

  ngOnInit(): void {
    void this.runPreflight();
  }

  // --- which act this dialog is offering -----------------------------------

  /** A batch whose students have ALL already graduated is not a batch to
   *  graduate; the only act left on it is the reversal. Read off the server's
   *  own counts rather than from `cohorts.status`, because a batch can be part
   *  graduated after a reversal that covered only some of it. */
  readonly isReversing = computed(() => {
    const report = this.preview();
    return report !== null && report.affected === 0 && report.skipped > 0;
  });

  readonly headline = computed(() =>
    this.isReversing()
      ? `Reverse the graduation of ${this.batch().batchDisplayLabel}`
      : `Graduate ${this.batch().batchDisplayLabel}`,
  );

  readonly subLine = computed(() => {
    const batch = this.batch();
    const report = this.preview();
    if (report === null) {
      return `${plural(batch.studentCount, 'student')} · semester ${batch.currentSemesterLabel}`;
    }
    if (this.isReversing()) {
      return `${plural(report.skipped, 'graduate')} · their accounts go back to STUDENT and every device is signed out again`;
    }
    return (
      `${plural(report.affected, 'student')} · semester ${batch.currentSemesterLabel} · ` +
      'the batch becomes Graduated and its students become alumni'
    );
  });

  // --- the checks ----------------------------------------------------------

  readonly checks = computed<PreflightCheck[]>(() => this.preview()?.checks ?? []);

  iconFor(check: PreflightCheck): string {
    return CHECK_ICONS[check.status];
  }

  toneFor(check: PreflightCheck): string {
    return CHECK_TONES[check.status];
  }

  readonly rows = computed<GraduateStudentOut[]>(() =>
    this.isReversing()
      ? (this.reversal()?.students ?? [])
      : (this.preview()?.students ?? []),
  );

  readonly blocked = computed(() => this.rows().filter((row) => row.blocked_reason !== null));

  onEffectiveOn(value: string): void {
    if (value === '') {
      return;
    }
    this.effectiveOn.set(value);
    // The "expected completion" check compares the batch's end date against
    // THIS date, so the verdict changes with it and the preflight is asked
    // again rather than going stale under the reader's own edit.
    void this.runPreflight();
  }

  onReason(value: string): void {
    this.reason.set(value);
  }

  // --- the confirm ---------------------------------------------------------

  readonly confirmLabel = computed(() => {
    if (this.busy()) {
      return 'Working…';
    }
    if (this.isReversing()) {
      return `Put ${plural(this.reversal()?.affected ?? 0, 'account')} back to student`;
    }
    return `Graduate ${plural(this.preview()?.affected ?? 0, 'student')}`;
  });

  readonly refusal = computed<string | null>(() => {
    if (this.preview() === null) {
      return 'The preflight has not answered yet.';
    }
    if (this.isReversing()) {
      if (this.reversalRefusal() !== null) {
        return this.reversalRefusal();
      }
      if (this.reversal() === null) {
        return 'The reversal preflight has not answered yet.';
      }
      return null;
    }
    const blocked = this.blocked();
    if (blocked.length > 0) {
      return `${plural(blocked.length, 'record')} cannot be graduated: ${blocked[0].blocked_reason}`;
    }
    if ((this.preview()?.affected ?? 0) === 0) {
      return 'Nobody in this batch would graduate.';
    }
    return null;
  });

  // --- reading and writing -------------------------------------------------

  private graduateBody(): string {
    return JSON.stringify({
      effective_on: this.effectiveOn(),
      reason: this.reason().trim() === '' ? null : this.reason().trim(),
    });
  }

  private reversalBody(): string {
    return JSON.stringify({
      reason: this.reason().trim() === '' ? null : this.reason().trim(),
    });
  }

  private async runPreflight(): Promise<void> {
    const mine = (this.runId += 1);
    this.checking.set(true);
    this.error.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/cohorts/${this.batch().batchId}/graduate?dry_run=true`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: this.graduateBody(),
        },
      );
      if (mine !== this.runId) {
        return;
      }
      if (!response.ok) {
        this.preview.set(null);
        this.error.set(await detailOf(response));
        return;
      }
      const report = (await response.json()) as GraduateOut;
      this.preview.set(report);
      if (report.affected === 0 && report.skipped > 0) {
        await this.runReversalPreflight(mine);
      }
    } catch {
      if (mine === this.runId) {
        this.error.set('The server could not be reached. Nothing was changed.');
      }
    } finally {
      if (mine === this.runId) {
        this.checking.set(false);
      }
    }
  }

  /** The reversal's own dry run. Its 409 — "these graduations are older than
   *  30 days" — fires here too, which is the whole reason to ask before
   *  offering the button. */
  private async runReversalPreflight(mine: number): Promise<void> {
    this.reversalRefusal.set(null);
    const response = await fetch(
      `${environment.apiBase}/admin/cohorts/${this.batch().batchId}/ungraduate?dry_run=true`,
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: this.reversalBody(),
      },
    );
    if (mine !== this.runId) {
      return;
    }
    if (!response.ok) {
      this.reversal.set(null);
      this.reversalRefusal.set(await detailOf(response));
      return;
    }
    this.reversal.set((await response.json()) as UngraduateOut);
  }

  async confirm(): Promise<void> {
    if (this.busy() || this.refusal() !== null) {
      return;
    }
    const reversing = this.isReversing();
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/cohorts/${this.batch().batchId}/` +
          (reversing ? 'ungraduate' : 'graduate'),
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: reversing ? this.reversalBody() : this.graduateBody(),
        },
      );
      if (!response.ok) {
        this.error.set(await detailOf(response));
        return;
      }
      if (reversing) {
        this.reversed.set((await response.json()) as UngraduateOut);
      } else {
        this.outcome.set((await response.json()) as GraduateOut);
      }
      this.completed.emit();
    } catch {
      this.error.set('The server could not be reached. Nothing was changed.');
    } finally {
      this.busy.set(false);
    }
  }

  readonly settled = computed(() => this.outcome() !== null || this.reversed() !== null);

  close(): void {
    this.dismissed.emit();
  }

  valueOf(event: Event): string {
    return (event.target as HTMLInputElement).value;
  }
}

/** FastAPI answers a schema error with `detail` as a LIST; rendered raw it
 *  reads "[object Object]". */
async function detailOf(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail;
    if (typeof detail === 'string') {
      return detail;
    }
    if (Array.isArray(detail)) {
      const messages: string[] = [];
      for (const entry of detail) {
        const message = (entry as { msg?: string }).msg;
        if (typeof message === 'string') {
          messages.push(message);
        }
      }
      if (messages.length > 0) {
        return messages.join(' ');
      }
    }
  } catch {
    /* not JSON — fall through to the status */
  }
  return `The request was refused (${response.status}).`;
}
