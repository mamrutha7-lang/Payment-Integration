/**
 * Promote batch — the dialog from `design/admin/PromoteBatch.html` (spec §25).
 *
 * IT WRITES NOW (B4.3), AND IT SHOWS ITS WORKING FIRST.
 *
 *   POST /api/admin/cohorts/{id}/promote?dry_run=true    the preflight
 *   POST /api/admin/cohorts/{id}/promote                 the promotion
 *
 * THE DRY RUN IS THE DIALOG, not a nicety. `?dry_run=true` runs every check and
 * every per-student calculation and returns WITHOUT writing — the same response
 * shape, `dry_run: true`, and `affected` reading what WOULD move. So the checks
 * on this screen are the server's own `PreflightCheck` rows rather than
 * anything this file reasons about, and the table under them names the students
 * by name with the semester each would land on. Nothing here is computed
 * client-side and nothing is invented: the previous version of this dialog drew
 * five checks it had worked out from the roster, and three of them were guesses
 * dressed as verdicts.
 *
 * IT RE-RUNS WHEN THE ANSWER CHANGES, AND ONLY THEN. Ticking somebody into the
 * hold-back list moves them out of `affected` and may clear a `blocked_reason`
 * — a student already at the last semester of their programme blocks the WHOLE
 * call, and holding them back is the fix the server's own message names, so a
 * preflight that did not re-run would go on showing the refusal after the
 * reader had fixed it. The date and the reason are written onto the history
 * rows and read by no check here, so neither asks again.
 *
 * `unavailable` IS A REAL CHECK STATUS AND NOT A FAILURE, and this dialog draws
 * it as neither a tick nor a cross. `attendance_records` carries a course code
 * and a session number and NO SEMESTER COLUMN AT ALL, so "is attendance
 * imported for the current semester" cannot be asked of this schema by any
 * query — the server says so in words and this screen prints that sentence.
 *
 * BLOCKED IS TEXT AND COLOUR TOGETHER, never colour alone: a blocked check
 * carries a "Blocked" chip beside its headline, and the confirm button is
 * disabled with the server's own reason on it rather than being left live to
 * post a 422 the reader could have been shown a moment earlier.
 *
 * THIS DIALOG DOES NOT REFRESH THE ROSTER BEHIND IT — it emits `completed` and
 * the parent does. Students & batches binds it to
 * `onBatchWriteCompleted()`, which re-reads the grid AND the promotion history
 * card; the result panel's sentence says so. Emitting rather than reaching out
 * is what keeps this dialog's only knowledge of the world the batch it was
 * handed. If a second parent ever mounts it, that parent must bind `completed`
 * too, or its screen sits at the read it took before the write.
 */

import { Component, OnInit, computed, inject, input, output, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { plural } from '../../../shared/text/plural.pipe';
import type { BatchSummary } from './batch-summary';

/** `admin_promotion.PreflightCheck`. `unavailable` is a real answer: the
 *  question cannot be asked of this schema, which is neither a pass nor a
 *  failure and must not render as either. */
interface PreflightCheck {
  key: string;
  label: string;
  status: 'ok' | 'warn' | 'blocked' | 'unavailable';
  detail: string;
}

/** `admin_promotion.PromotionStudentOut` — one student's half of the move. */
interface PromotionStudentOut {
  student_id: string;
  name: string;
  usn: string | null;
  from_semester: number;
  to_semester: number;
  from_stage: string;
  to_stage: string;
  held_back: boolean;
  /** Any non-null value here refuses the WHOLE call: a batch half promoted is
   *  the state the endpoint exists to avoid. */
  blocked_reason: string | null;
}

/** `admin_promotion.PromoteOut`. */
interface PromoteOut {
  cohort_id: string;
  batch: string;
  dry_run: boolean;
  affected: number;
  held_back: number;
  /** Students who have already GRADUATED — skipped rather than refused, which
   *  is a real state after a graduation was reversed for part of a batch. */
  skipped: number;
  ceiling: number;
  /** "course" when `academic_courses.total_semesters` answered, "default" when
   *  nothing did. The two mean different things to the reader and the dialog
   *  says which. */
  ceiling_source: string;
  checks: PreflightCheck[];
  students: PromotionStudentOut[];
}

/** How each server status is drawn. `blocked` borrows the warn icon colour and
 *  is told apart by its chip and its own glyph — a fourth colour in
 *  `batch-dialog.scss` would be a second owner for a stylesheet both dialogs
 *  and the roster's own share. */
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
  selector: 'app-promote-batch-dialog',
  standalone: true,
  imports: [],
  templateUrl: './promote-batch-dialog.component.html',
  styleUrls: ['./batch-dialog.scss', './promote-batch-dialog.component.scss'],
  host: { '(document:keydown.escape)': 'dismissed.emit()' },
})
export class PromoteBatchDialogComponent implements OnInit {
  readonly batch = input.required<BatchSummary>();
  readonly dismissed = output<void>();
  /** Emitted once a promotion has actually been written, so a parent can
   *  refetch the roster it read before it. */
  readonly completed = output<void>();

  readonly titleId = 'promote-batch-title';

  /** The date the office records against the promotion — routinely EARLIER
   *  than today, because a batch is promoted once the results are out. */
  readonly effectiveOn = signal(new Date().toISOString().slice(0, 10));
  readonly reason = signal('');
  readonly held = signal<ReadonlySet<string>>(new Set<string>());

  readonly preview = signal<PromoteOut | null>(null);
  readonly outcome = signal<PromoteOut | null>(null);
  readonly checking = signal(true);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);

  /** Which preflight is the current one. A tick and a date change in quick
   *  succession are two requests, and the slower one must not overwrite the
   *  newer answer with a report of a batch nobody is looking at. */
  private runId = 0;

  ngOnInit(): void {
    void this.runPreflight();
  }

  // --- what the head says --------------------------------------------------

  readonly headline = computed(() => {
    const batch = this.batch();
    if (batch.nextSemester === null) {
      return `Promote ${batch.batchDisplayLabel}`;
    }
    return `Promote ${batch.batchDisplayLabel} to semester ${batch.nextSemester}`;
  });

  readonly subLine = computed(() => {
    const batch = this.batch();
    const report = this.preview();
    const students = plural(batch.studentCount, 'student');
    if (report === null) {
      return `${students} · semester ${batch.currentSemesterLabel}`;
    }
    const ceiling =
      report.ceiling_source === 'course'
        ? `the course runs ${plural(report.ceiling, 'semester')}`
        : `no course length on record — the deployment default of ${report.ceiling} applies`;
    return `${students} · semester ${batch.currentSemesterLabel} · ${ceiling}`;
  });

  // --- the checks ----------------------------------------------------------

  readonly checks = computed<PreflightCheck[]>(() => this.preview()?.checks ?? []);

  iconFor(check: PreflightCheck): string {
    return CHECK_ICONS[check.status];
  }

  toneFor(check: PreflightCheck): string {
    return CHECK_TONES[check.status];
  }

  // --- the students --------------------------------------------------------

  readonly rows = computed<PromotionStudentOut[]>(() => this.preview()?.students ?? []);

  readonly blocked = computed(() =>
    this.rows().filter((row) => row.blocked_reason !== null),
  );

  isHeld(studentId: string): boolean {
    return this.held().has(studentId);
  }

  /** Ticking somebody changes the answer, so the preflight is asked again. */
  toggleHold(studentId: string): void {
    const next = new Set(this.held());
    if (next.has(studentId)) {
      next.delete(studentId);
    } else {
      next.add(studentId);
    }
    this.held.set(next);
    void this.runPreflight();
  }

  /** No re-run: not one promote check reads this date. It is written onto the
   *  history rows and nothing else, so asking again would be a request per
   *  keystroke for an answer that cannot have changed. (The graduate dialog
   *  DOES re-run on its date, because its "expected completion" check compares
   *  the batch's end date against it.) */
  onEffectiveOn(value: string): void {
    if (value === '') {
      return;
    }
    this.effectiveOn.set(value);
  }

  onReason(value: string): void {
    // The reason travels with the write and changes no check, so it does not
    // re-run the preflight — a request per keystroke for a field nothing reads.
    this.reason.set(value);
  }

  // --- the confirm ---------------------------------------------------------

  readonly affected = computed(() => this.preview()?.affected ?? 0);

  readonly confirmLabel = computed(() => {
    if (this.busy()) {
      return 'Working…';
    }
    return `Promote ${plural(this.affected(), 'student')}`;
  });

  /** The server's own sentence for why this cannot be pressed, or null when it
   *  can. Disabling it with the reason ON the control is the whole point of the
   *  preflight: the 422 is shown before the press, not after it. */
  readonly refusal = computed<string | null>(() => {
    if (this.preview() === null) {
      return 'The preflight has not answered yet.';
    }
    const blocked = this.blocked();
    if (blocked.length > 0) {
      return (
        `${plural(blocked.length, 'student')} cannot move: ` +
        `${blocked[0].blocked_reason} Hold them back, or graduate the batch.`
      );
    }
    if (this.affected() === 0) {
      return 'Nobody in this batch would move — everybody is held back or has already graduated.';
    }
    return null;
  });

  // --- reading and writing -------------------------------------------------

  private body(): string {
    return JSON.stringify({
      effective_on: this.effectiveOn(),
      hold_back: [...this.held()],
      reason: this.reason().trim() === '' ? null : this.reason().trim(),
    });
  }

  /** The same call the confirm makes, with `?dry_run=true`. Every refusal it
   *  can raise fires here too — that is the point: the dialog's job is to
   *  surface the 422 before the admin presses the button, not to hide it until
   *  afterwards. */
  private async runPreflight(): Promise<void> {
    const mine = (this.runId += 1);
    this.checking.set(true);
    this.error.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/cohorts/${this.batch().batchId}/promote?dry_run=true`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: this.body(),
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
      this.preview.set((await response.json()) as PromoteOut);
    } catch {
      if (mine === this.runId) {
        this.error.set('The server could not be reached. Nothing was promoted.');
      }
    } finally {
      if (mine === this.runId) {
        this.checking.set(false);
      }
    }
  }

  async promote(): Promise<void> {
    if (this.busy() || this.refusal() !== null) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/cohorts/${this.batch().batchId}/promote`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: this.body(),
        },
      );
      if (!response.ok) {
        this.error.set(await detailOf(response));
        return;
      }
      this.outcome.set((await response.json()) as PromoteOut);
      this.completed.emit();
    } catch {
      this.error.set('The server could not be reached. Nothing was promoted.');
    } finally {
      this.busy.set(false);
    }
  }

  close(): void {
    this.dismissed.emit();
  }

  /** Read a control's value without reaching for `any` in the template. */
  valueOf(event: Event): string {
    return (event.target as HTMLInputElement).value;
  }
}

/** FastAPI answers a schema error with `detail` as a LIST; rendered raw it
 *  reads "[object Object]". The server's refusals name the rule they are
 *  keeping, so its own sentence is kept wherever there is one. */
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
