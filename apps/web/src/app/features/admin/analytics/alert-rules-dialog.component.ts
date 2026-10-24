/**
 * Alert rules — the thresholds the nightly sweep evaluates, per batch (B8.3).
 *
 * `02-admin-console-spec.md` §2 draws a "Rules" button on the Alerts card and
 * says nothing about what is behind it. What is behind it is
 * `alert_rule_configs`: ONE ROW PER (batch, rule), carrying `enabled`, a
 * `severity` and a free-form `params` object whose shape differs per rule. This
 * dialog is the only screen that writes them —
 * `PUT /api/admin/alert-rules` — and it reads them back from
 * `GET /api/admin/alert-rules?cohort_id=`.
 *
 * A RULE WITH NO ROW IS NEVER EVALUATED, AND THAT IS THE MOST IMPORTANT
 * SENTENCE ON THIS SCREEN. `app/alerts.py::evaluate_alerts` selects the
 * ENABLED config rows and iterates those; a batch nobody has configured
 * produces no findings at all, for ever, and the Alerts card beside this dialog
 * says "Nothing open" the whole time. So every rule is listed here whether or
 * not it has a row, and the ones that do not are labelled "Not set" rather than
 * drawn as though they were running at their documented default.
 *
 * NOTHING RUNS ON A CLOCK YET, AND THE DIALOG SAYS SO. The engine exists and is
 * complete — `python -m app.alerts_job` sweeps every enabled rule — but no
 * EventBridge schedule invokes it: that is a change to `infra/cdk/reep_core`,
 * which needs the owner's go, and `app/alerts_job.py`'s own docstring is where
 * the reasoning lives. Saving a threshold here therefore changes what the NEXT
 * sweep finds, and says that, rather than implying tonight.
 *
 * A BLANK THRESHOLD IS NOT A ZERO. `params` is operator-entered JSONB and the
 * engine reads each key through `_int_param`/`_float_param`, which fall back to
 * a documented default when the key is missing. So a field left empty is sent
 * as an ABSENT key and the engine uses that default — which the hint under each
 * field names. Sending 0 instead would be a threshold of zero, which for
 * `minAttendancePct` means a rule that can never fire and for `days` means one
 * that fires for everybody.
 *
 * THERE IS NO DELETE. The endpoint upserts and there is no route that removes a
 * config row, so a rule that has been set can be switched OFF but not unset.
 * The dialog says which, because "disabled" and "never configured" read
 * identically on a card that only shows the ones that fired.
 */

import { Component, computed, output, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';

/** One threshold inside a rule's `params` object. */
interface RuleParamSpec {
  readonly key: string;
  readonly label: string;
  /** What the engine does when this is left blank — its documented default. */
  readonly hint: string;
  readonly step: string;
}

/** One rule of `app/models/alert.py::AlertRuleKey`, as the office reads it. */
interface RuleSpec {
  readonly key: string;
  readonly label: string;
  readonly description: string;
  /** What the rule declines to interpret — the absence it will not alert on.
   *  Every one of the five has one, and it is the reason a quiet feed is not
   *  proof that nobody is behind. */
  readonly skips: string;
  readonly params: readonly RuleParamSpec[];
}

/** The five keys, their thresholds and their documented defaults, read out of
 *  `app/alerts.py`. The defaults are quoted in the hints and NEVER pre-filled
 *  into the inputs: a pre-filled value would be saved as an explicit threshold
 *  the office never chose, and `params` would then stop tracking the code's
 *  default the day somebody tuned it. */
const RULES: readonly RuleSpec[] = [
  {
    key: 'NO_CHECKIN_N_DAYS',
    label: 'No sign-in for N days',
    description:
      'Nobody has seen this student sign in for N days. A student who has never signed in at all is the loudest case this rule has, and their clock runs from when the account was created.',
    skips: 'Nothing. An account provisioned and never opened is exactly what this rule is for.',
    params: [{ key: 'days', label: 'Days of silence', hint: 'Blank uses 5.', step: '1' }],
  },
  {
    key: 'ATTENDANCE_BELOW_THRESHOLD',
    label: 'Attendance below the cut-off',
    description:
      'Recorded attendance under the percentage you set, once at least the minimum number of sessions has been recorded.',
    skips:
      'A student with NO attendance records is skipped. Their percentage is unknown, not zero — on a deployment where no attendance has been imported, alerting on it would flag every student at once.',
    params: [
      { key: 'minAttendancePct', label: 'Attendance floor %', hint: 'Blank uses 75.', step: '0.5' },
      { key: 'minSessions', label: 'Minimum sessions recorded', hint: 'Blank uses 1.', step: '1' },
    ],
  },
  {
    key: 'CERT_OVERDUE',
    label: 'Certification overdue',
    description:
      'A certification is past its due date and not completed, after the grace period you set.',
    skips: 'A student with nothing enrolled has nothing overdue, so they never match.',
    params: [{ key: 'graceDays', label: 'Grace days', hint: 'Blank uses 3.', step: '1' }],
  },
  {
    key: 'PACE_BELOW_THRESHOLD',
    label: 'Skilling pace below target',
    description:
      'Skilling minutes logged over the trailing seven days, against the student’s own weekly hour target, short by more than the deviation you set.',
    skips:
      'A student who has never logged a timesheet entry is skipped — zero hours there is a fact about the screen, not about their pace. One who used to log and stopped does fire.',
    params: [
      { key: 'deviationPct', label: 'Deviation % below target', hint: 'Blank uses 25.', step: '1' },
    ],
  },
  {
    key: 'LOW_FOCUS_QUALITY',
    label: 'Low focus quality',
    description:
      'Productive heads — lectures, coursework, skilling — as a share of every half hour on SUBMITTED ledger days in the window.',
    skips:
      'Only submitted days count, and a student with none in the window is skipped. A draft day is a day somebody is part way through typing.',
    params: [
      { key: 'minProductivePct', label: 'Productive floor %', hint: 'Blank uses 40.', step: '1' },
      { key: 'days', label: 'Window in days', hint: 'Blank uses 14.', step: '1' },
      { key: 'minDays', label: 'Minimum submitted days', hint: 'Blank uses 3.', step: '1' },
    ],
  },
];

/** `app/models/alert.py::AlertSeverity`, in the order the feed sorts them. */
const SEVERITIES = [
  { id: 'INFO', label: 'Info' },
  { id: 'WARNING', label: 'Warning' },
  { id: 'CRITICAL', label: 'Critical' },
] as const;

const DEFAULT_SEVERITY = 'WARNING';

/** `GET /api/admin/cohorts`. */
interface CohortOption {
  id: string;
  code: string;
  name: string;
  batch_label: string;
  /** The spine and the year in one sentence, composed by the server from the
   *  batch's own links: "General MBA - Finance · 2026-28". `name` is the YEAR
   *  alone, so it cannot tell two batches of one department apart. */
  display_label: string;
  degree_level: string;
  student_count: number;
}

/** `GET /api/admin/alert-rules`. */
interface AlertRuleConfig {
  id: string;
  cohort_id: string;
  rule_key: string;
  enabled: boolean;
  params: Record<string, unknown>;
  severity: string;
}

/** One rule as this dialog draws and edits it. */
interface RuleRow {
  spec: RuleSpec;
  /** A row exists on the server for this (batch, rule). */
  configured: boolean;
  enabled: boolean;
  severity: string;
  /** Values as TYPED, so a half-entered number is not silently rounded. */
  values: Record<string, string>;
  busy: boolean;
  flash: string | null;
  error: string | null;
}

function blankRow(spec: RuleSpec): RuleRow {
  return {
    spec,
    configured: false,
    enabled: false,
    severity: DEFAULT_SEVERITY,
    values: Object.fromEntries(spec.params.map((param) => [param.key, ''])),
    busy: false,
    flash: null,
    error: null,
  };
}

@Component({
  selector: 'app-alert-rules-dialog',
  standalone: true,
  imports: [],
  templateUrl: './alert-rules-dialog.component.html',
  styleUrl: './alert-rules-dialog.scss',
  host: { '(document:keydown.escape)': 'dismissed.emit()' },
})
export class AlertRulesDialogComponent {
  /** Emitted on close. The Alerts card reloads its feed from it. */
  readonly dismissed = output<void>();

  readonly titleId = 'alert-rules-title';
  readonly severities = SEVERITIES;

  readonly batches = signal<CohortOption[] | null>(null);
  readonly batchId = signal<string>('');
  readonly rows = signal<RuleRow[]>(RULES.map(blankRow));

  readonly error = signal<string | null>(null);
  readonly loadingRules = signal(false);

  constructor() {
    void this.loadBatches();
  }

  readonly batchLabel = computed<string>(() => {
    const chosen = (this.batches() ?? []).find((batch) => batch.id === this.batchId());
    if (!chosen) return 'Choose a batch';
    return chosen.display_label;
  });

  readonly chosenBatch = computed<CohortOption | null>(
    () => (this.batches() ?? []).find((batch) => batch.id === this.batchId()) ?? null,
  );

  /** How many of the five will actually be evaluated for this batch tonight —
   *  configured AND enabled. Drawn as a sentence, because "3 of 5" on its own
   *  reads as progress rather than as coverage. */
  readonly liveRuleCount = computed<number>(
    () => this.rows().filter((row) => row.configured && row.enabled).length,
  );

  // ---------------------------------------------------------------- reads --

  private async loadBatches(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/cohorts`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      const batches = (await response.json()) as CohortOption[];
      this.batches.set(batches);
      if (batches.length > 0) {
        this.batchId.set(batches[0].id);
        await this.loadRules(batches[0].id);
      }
    } catch {
      this.error.set('Could not reach the server.');
    }
  }

  private async loadRules(cohortId: string): Promise<void> {
    this.loadingRules.set(true);
    this.error.set(null);
    // Back to "not set" for every rule BEFORE the request: leaving the previous
    // batch's thresholds on screen under the new batch's name would invite
    // somebody to press Save and write one batch's numbers onto another.
    this.rows.set(RULES.map(blankRow));
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/alert-rules?cohort_id=${encodeURIComponent(cohortId)}`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      const configs = (await response.json()) as AlertRuleConfig[];
      const byKey = new Map(configs.map((config) => [config.rule_key, config]));
      this.rows.set(
        RULES.map((spec) => {
          const config = byKey.get(spec.key);
          if (!config) return blankRow(spec);
          return {
            spec,
            configured: true,
            enabled: config.enabled,
            severity: config.severity,
            values: Object.fromEntries(
              spec.params.map((param) => {
                const stored = config.params?.[param.key];
                return [param.key, stored === undefined || stored === null ? '' : String(stored)];
              }),
            ),
            busy: false,
            flash: null,
            error: null,
          };
        }),
      );
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.loadingRules.set(false);
    }
  }

  // --------------------------------------------------------------- edits --

  chooseBatch(event: Event): void {
    const chosen = (event.target as HTMLSelectElement).value;
    this.batchId.set(chosen);
    if (chosen) void this.loadRules(chosen);
  }

  setEnabled(key: string, event: Event): void {
    const checked = (event.target as HTMLInputElement).checked;
    this.patch(key, (row) => ({ ...row, enabled: checked, flash: null, error: null }));
  }

  setSeverity(key: string, event: Event): void {
    const severity = (event.target as HTMLSelectElement).value;
    this.patch(key, (row) => ({ ...row, severity, flash: null, error: null }));
  }

  setParam(key: string, paramKey: string, event: Event): void {
    const typed = (event.target as HTMLInputElement).value;
    this.patch(key, (row) => ({
      ...row,
      values: { ...row.values, [paramKey]: typed },
      flash: null,
      error: null,
    }));
  }

  private patch(key: string, change: (row: RuleRow) => RuleRow): void {
    this.rows.update((rows) => rows.map((row) => (row.spec.key === key ? change(row) : row)));
  }

  // --------------------------------------------------------------- write --

  async save(key: string): Promise<void> {
    const cohortId = this.batchId();
    const row = this.rows().find((candidate) => candidate.spec.key === key);
    if (!row || !cohortId) return;

    const params: Record<string, number> = {};
    for (const param of row.spec.params) {
      const typed = (row.values[param.key] ?? '').trim();
      // BLANK IS AN ABSENT KEY, NOT A ZERO. The engine falls back to the
      // documented default for a key it does not find; a 0 written here would
      // be an explicit threshold, and for a percentage floor that is a rule
      // which can never fire.
      if (typed === '') continue;
      const value = Number(typed);
      if (!Number.isFinite(value)) {
        this.patch(key, (current) => ({
          ...current,
          error: `${param.label} must be a number, or left blank to use the default.`,
        }));
        return;
      }
      params[param.key] = value;
    }

    this.patch(key, (current) => ({ ...current, busy: true, flash: null, error: null }));
    try {
      const response = await fetch(`${environment.apiBase}/admin/alert-rules`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cohort_id: cohortId,
          rule_key: key,
          params,
          enabled: row.enabled,
          severity: row.severity,
        }),
      });
      if (!response.ok) {
        const detail = await this.detailOf(response);
        this.patch(key, (current) => ({ ...current, busy: false, error: detail }));
        return;
      }
      const written = (await response.json()) as AlertRuleConfig;
      this.patch(key, (current) => ({
        ...current,
        busy: false,
        configured: true,
        enabled: written.enabled,
        severity: written.severity,
        flash: written.enabled
          ? 'Saved. The next sweep evaluates this rule for this batch.'
          : 'Saved and switched off. This rule raises nothing for this batch until it is switched back on.',
      }));
    } catch {
      this.patch(key, (current) => ({
        ...current,
        busy: false,
        error: 'Could not reach the server.',
      }));
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
      return 'Alert rules need the Analytics function. Ask the Main Admin to grant admin.analytics in Who can do what.';
    }
    return `The request was refused (${response.status}).`;
  }
}
