/**
 * The Time Allocation Ledger — six slots covering a 24-hour day, five activity
 * heads, hours to the nearest half.
 *
 * THE SERVER OWNS THE ARITHMETIC. The metrics strip, the day band's proportions,
 * the per-slot mix bars, the legend totals and the submit gate all arrive
 * computed from `GET /api/student/ledger`; this component types figures into
 * cells, PUTs them, and renders what comes back. Deriving any of it a second
 * time in TypeScript is how a band ends up disagreeing with the number printed
 * above it — see the note at the top of app/routers/student_programme.py.
 *
 * THE SERVER OWNS THE CALENDAR TOO (2026-09-22). "Today" is the `today` every
 * response carries — the programme's day, in the college's zone — and the
 * first load asks for no day at all so the server picks it. The stepper moves
 * through `shiftIsoDay`, which never touches local time: the old
 * `new Date(...T00:00:00)` / `toISOString()` pair parsed local midnight and
 * printed UTC, so in India "Previous day" went back two days and "Next day"
 * did not move. That is how a student who had filled Monday in came to report
 * that their record had vanished — see ledger-days.ts.
 *
 * A DAY LOCKS. `editable` / `locked` / `lock_reason` come from the server, the
 * inputs enable off `editable` alone, and the strip of recent days
 * (`GET /api/student/ledger/history`) is the record of what was filled in:
 * submitted, draft, not logged, locked — the screen showed one day at a time
 * and nothing else, so a fortnight of entries had nowhere to be seen.
 *
 * ONLY THE LAST DAY ASKED FOR IS DRAWN. Each step or chip loads its day, and
 * the answers can arrive in any order. The screen used to draw whichever
 * arrived LAST, so a quick second click while the first day was still loading
 * could settle on the earlier day, date control and all. Every read and write
 * carries `loadSeq` at the moment it was asked, and an answer whose number is
 * no longer current is dropped, a refusal included. A write names its day when
 * it is asked, never after an await: see `submitDay`.
 *
 * EDITS ARE LOCAL UNTIL SAVED. `draft` holds what the student has typed; the
 * server's view is only replaced on a successful write. Re-rendering the whole
 * table from a response on every keystroke would move focus out of the cell
 * being typed in, and a PUT per keystroke would make the slot-capacity rule —
 * which is a property of the whole day — fire on half-typed numbers.
 */

import { Component, computed, signal } from '@angular/core';
import { DatePipe } from '@angular/common';

import { environment } from '../../../../environments/environment';
import { featureRefusal } from '../../../core/feature-refusal';
import { deviceTodayIso, isoAfter, shiftIsoDay } from './ledger-days';

type Tone = 'good' | 'warn' | 'risk' | 'neutral';

interface Segment {
  activity: string | null;
  label: string;
  colour: string | null;
  hours: number;
  percent: number;
}

interface Slot {
  key: string;
  label: string;
  icon: string | null;
  tick: string;
  capacity_hours: number;
  logged_hours: number;
  weight: number;
  state_label: string;
  state_tone: Tone;
  cells: Record<string, number>;
  mix: Segment[];
}

interface Metric {
  key: string;
  label: string;
  value: string;
  unit: string;
  sub: string;
  tone: Tone;
}

interface Activity {
  key: string;
  label: string;
  colour: string;
  productive: boolean;
}

interface Legend {
  activity: string | null;
  label: string;
  colour: string | null;
  hours: number;
}

interface Ledger {
  day: string;
  /** The programme's calendar day when this was read — the stepper's anchor. */
  today: string;
  status: 'DRAFT' | 'SUBMITTED';
  submitted_at: string | null;
  /** Not submitted, not locked, not in the future: the inputs enable off this. */
  editable: boolean;
  /** Past its edit window. `lock_reason` says so in a sentence. */
  locked: boolean;
  edit_until: string;
  edit_window_days: number;
  lock_reason: string | null;
  can_submit: boolean;
  submit_blocked_reason: string | null;
  total_hours: number;
  day_capacity_hours: number;
  unaccounted_hours: number;
  activities: Activity[];
  slots: Slot[];
  metrics: Metric[];
  legend: Legend[];
}

/** One day of `GET /api/student/ledger/history`. */
interface HistoryDay {
  day: string;
  status: 'EMPTY' | 'DRAFT' | 'SUBMITTED';
  logged_hours: number;
  editable: boolean;
  locked: boolean;
  submitted_at: string | null;
}

interface History {
  today: string;
  window_days: number;
  edit_window_days: number;
  days_submitted: number;
  days_logged: number;
  /** Most recent first, one entry per calendar day whether or not it was logged. */
  days: HistoryDay[];
}

/** `GET /api/student/timesheet` — SKILLING hours this week against the target.
 *
 *  Summed by the server from THIS ledger's SKILLING cells (and, for a day with
 *  no ledger row, from the old free-form time log's table). It used to read
 *  the old table alone, which nothing writes any more, so this strip sat at
 *  "0 h" under the very cells it should have been adding up. */
interface WeeklySkilling {
  skilling_hours: number;
  weekly_hour_target: number;
}

type State = 'loading' | 'data' | 'error';

const HISTORY_DAYS = 14;

@Component({
  selector: 'app-ledger',
  standalone: true,
  imports: [DatePipe],
  templateUrl: './ledger.component.html',
  styleUrl: './ledger.component.scss',
})
export class LedgerComponent {
  readonly state = signal<State>('loading');
  readonly error = signal<string | null>(null);
  /** The office's message when the ledger is switched off for the student;
   *  null for every other failed read, which keeps the screen's own line. */
  readonly refusal = signal<string | null>(null);
  readonly saving = signal(false);
  readonly ledger = signal<Ledger | null>(null);

  /** The programme's today, as the server last reported it. The device's own
   *  date stands in only until the first response arrives. */
  readonly today = signal<string>(deviceTodayIso());
  readonly day = signal<string>(this.today());

  readonly history = signal<History | null>(null);

  /** The semester in the eyebrow — read from the student's record, never typed
   *  into the template. Null until it arrives, and the eyebrow says "Daily log"
   *  alone rather than inventing a number. */
  readonly semester = signal<number | null>(null);

  /** What the student has typed, keyed `SLOT|ACTIVITY`. Cleared on every load
   *  so a stale edit cannot survive a date change. */
  private readonly draft = signal<Record<string, number>>({});
  readonly dirty = computed(() => Object.keys(this.draft()).length > 0);

  readonly submitted = computed(() => this.ledger()?.status === 'SUBMITTED');
  readonly locked = computed(() => this.ledger()?.locked === true);
  readonly editable = computed(() => this.ledger()?.editable === true);

  /** "Copy yesterday" is offered only where the server would take it: a day
   *  still open, whose previous day the strip reports SUBMITTED.
   *  `copy_yesterday` refuses a draft source (a half-finished day must not be
   *  spread forward), and the strip is the server's own word on each day, so
   *  a day it does not reach is simply not offered the button. */
  readonly canCopyYesterday = computed(() => {
    if (this.state() !== 'data' || !this.editable()) return false;
    const previous = shiftIsoDay(this.day(), -1);
    return this.history()?.days.find((d) => d.day === previous)?.status === 'SUBMITTED';
  });

  /** Bumped by every read of a day; see "ONLY THE LAST DAY ASKED FOR". */
  private loadSeq = 0;
  /** The same for the strip of recent days; see `loadHistory`. */
  private historySeq = 0;

  /** "Each day can be filled in for 2 days after it ends, then it locks." —
   *  the window the server applies, in one sentence for the history card. */
  readonly windowSentence = computed(() => {
    const n = this.history()?.edit_window_days ?? this.ledger()?.edit_window_days;
    if (n === undefined || n === null) return '';
    if (n === 0) return 'Each day can be filled in on the day itself, then it locks.';
    if (n === 1) return 'Each day can be filled in until the end of the next day, then it locks.';
    return `Each day can be filled in for ${n} days after it ends, then it locks.`;
  });

  readonly weekly = signal<WeeklySkilling | null>(null);
  readonly weeklyPercent = computed(() => {
    const w = this.weekly();
    if (!w || w.weekly_hour_target <= 0) return 0;
    return Math.min(100, Math.round((w.skilling_hours / w.weekly_hour_target) * 100));
  });

  constructor() {
    void this.load(true);
    void this.loadHistory();
    void this.loadWeekly();
    void this.loadSemester();
  }

  private async loadSemester(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/student/dashboard`, {
        credentials: 'include',
      });
      if (!res.ok) return;
      const d = (await res.json()) as { current_semester?: number };
      if (typeof d.current_semester === 'number') this.semester.set(d.current_semester);
    } catch {
      /* the eyebrow simply omits the semester */
    }
  }

  // --- reads ---------------------------------------------------------------

  /** Independent of the ledger's own state: a failure here hides one strip,
   *  it does not put the ledger into an error state over a summary. */
  private async loadWeekly(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/student/timesheet?days=7`, {
        credentials: 'include',
      });
      if (!res.ok) return;
      this.weekly.set((await res.json()) as WeeklySkilling);
    } catch {
      /* no strip */
    }
  }

  /** The strip of recent days. Same independence as the weekly strip: the
   *  ledger renders without it. Every write re-reads it, so a save and the
   *  submit behind it put two reads in flight, and only the later one may be
   *  drawn: the earlier would say the day just submitted is still a draft,
   *  and take "Copy yesterday" off the day after it. */
  async loadHistory(): Promise<void> {
    const seq = ++this.historySeq;
    try {
      const res = await fetch(
        `${environment.apiBase}/student/ledger/history?days=${HISTORY_DAYS}`,
        { credentials: 'include' },
      );
      if (!res.ok) return;
      const body = (await res.json()) as History;
      if (seq !== this.historySeq) return;
      this.history.set(body);
      this.today.set(body.today);
    } catch {
      /* no strip */
    }
  }

  /** `initial` asks for no day, so the SERVER decides what today is; every
   *  later load names the day the student stepped to. */
  async load(initial = false): Promise<void> {
    const seq = ++this.loadSeq;
    this.state.set('loading');
    this.error.set(null);
    this.refusal.set(null);
    this.draft.set({});
    try {
      const query = initial ? '' : `?day=${encodeURIComponent(this.day())}`;
      const res = await fetch(`${environment.apiBase}/student/ledger${query}`, {
        credentials: 'include',
      });
      if (!res.ok) {
        const refusal = await featureRefusal(res);
        if (seq !== this.loadSeq) return;
        this.refusal.set(refusal);
        this.state.set('error');
        return;
      }
      const body = (await res.json()) as Ledger;
      if (seq !== this.loadSeq) return;
      this.ledger.set(body);
      this.today.set(body.today);
      this.day.set(body.day);
      this.state.set('data');
    } catch {
      if (seq === this.loadSeq) this.state.set('error');
    }
  }

  step(days: number): void {
    const next = shiftIsoDay(this.day(), days);
    // A day that has not happened yet cannot be logged, and the server refuses
    // it — so the control refuses first rather than showing a 422.
    if (isoAfter(next, this.today())) return;
    this.day.set(next);
    void this.load();
  }

  /** Jump to a day from the history strip. */
  open(day: string): void {
    if (day === this.day()) return;
    this.day.set(day);
    void this.load();
  }

  get atToday(): boolean {
    return !isoAfter(this.today(), this.day());
  }

  /** The chip on a history day: what state the student left it in, and
   *  whether it can still change. Text and tone together, never colour alone. */
  historyChip(d: HistoryDay): { label: string; tone: Tone } {
    if (d.status === 'SUBMITTED') return { label: 'Submitted', tone: 'good' };
    if (d.status === 'DRAFT') {
      return d.locked
        ? { label: `${d.logged_hours} h · locked`, tone: 'risk' }
        : { label: `Draft · ${d.logged_hours} h`, tone: 'warn' };
    }
    return d.locked ? { label: 'Locked', tone: 'neutral' } : { label: 'Not logged', tone: 'neutral' };
  }

  // --- editing -------------------------------------------------------------

  /** The value a cell should show: the local edit if there is one, else the
   *  server's figure. */
  cellValue(slot: Slot, activity: string): number {
    const key = `${slot.key}|${activity}`;
    const local = this.draft()[key];
    return local ?? slot.cells[activity] ?? 0;
  }

  onCellInput(slot: Slot, activity: string, raw: string): void {
    const parsed = Number.parseFloat(raw);
    const hours = Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
    this.draft.update((d) => ({ ...d, [`${slot.key}|${activity}`]: hours }));
  }

  /** The running total for a slot as edited, so the "/4" figure and the chip
   *  respond while typing rather than only after a save. This is presentation
   *  of the student's own unsaved input — not a second copy of the server's
   *  arithmetic, which still decides everything on write. */
  slotTotal(slot: Slot): number {
    const acts = this.ledger()?.activities ?? [];
    return acts.reduce((sum, a) => sum + this.cellValue(slot, a.key), 0);
  }

  /** A column's running total across the six slots, for the footer row. Same
   *  reasoning as slotTotal: presentation of unsaved input, not a second copy
   *  of the server's arithmetic. */
  columnTotal(activity: string): number {
    const l = this.ledger();
    if (!l) return 0;
    return l.slots.reduce((sum, s) => sum + this.cellValue(s, activity), 0);
  }

  dayTotal(): number {
    const l = this.ledger();
    if (!l) return 0;
    return l.slots.reduce((sum, s) => sum + this.slotTotal(s), 0);
  }

  /** The footer chip beside the day total: what still separates the figures on
   *  screen from a day that reconciles. Presentation of the student's own
   *  unsaved input, like slotTotal — the server still decides on write. */
  dayState(): { label: string; tone: Tone } {
    const cap = this.ledger()?.day_capacity_hours ?? 24;
    const diff = this.round(cap - this.dayTotal());
    if (diff > 0) return { label: `${diff} h to reconcile`, tone: 'warn' };
    if (diff < 0) return { label: `${this.round(-diff)} h over`, tone: 'risk' };
    return { label: 'Reconciled', tone: 'good' };
  }

  /** Half-hour arithmetic on floats: 0.5 + 0.5 + 0.5 is exact, but a typed 0.3
   *  is not, and the chip must not print 0.30000000000000004. */
  private round(v: number): number {
    return Math.round(v * 100) / 100;
  }

  // --- writes --------------------------------------------------------------

  private cellsPayload(): { slot: string; activity: string; hours: number }[] {
    const l = this.ledger();
    if (!l) return [];
    const out: { slot: string; activity: string; hours: number }[] = [];
    for (const slot of l.slots) {
      for (const a of l.activities) {
        const hours = this.cellValue(slot, a.key);
        if (hours > 0) out.push({ slot: slot.key, activity: a.key, hours });
      }
    }
    return out;
  }

  private async write(path: string, body: unknown, method = 'POST'): Promise<boolean> {
    // The day this write is about. A step taken while it is in flight makes
    // its answer — a refusal as much as a saved day — a different day's, and
    // it must not be drawn over the day the student is now looking at.
    const seq = this.loadSeq;
    this.saving.set(true);
    this.error.set(null);
    try {
      const res = await fetch(`${environment.apiBase}${path}`, {
        method,
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        // The server's `detail` names the slot and the number — it is written to
        // be read by a student, so it is shown verbatim rather than replaced
        // with a generic failure line.
        const detail = await res
          .json()
          .then((b: { detail?: string }) => b.detail)
          .catch(() => null);
        if (seq === this.loadSeq) {
          this.error.set(detail || 'That could not be saved. Please try again.');
        }
        return false;
      }
      const saved = (await res.json()) as Ledger;
      if (seq === this.loadSeq) {
        this.ledger.set(saved);
        this.today.set(saved.today);
        this.draft.set({});
      }
      // The strip and the weekly figure are both sums over what was just
      // written; refresh them rather than let them lag the table.
      void this.loadHistory();
      void this.loadWeekly();
      return true;
    } catch {
      if (seq === this.loadSeq) this.error.set('Could not reach the server. Please try again.');
      return false;
    } finally {
      this.saving.set(false);
    }
  }

  save(): Promise<boolean> {
    return this.write('/student/ledger', { day: this.day(), cells: this.cellsPayload() }, 'PUT');
  }

  /** Prefill the day from the submitted day before it. What the student had
   *  typed or saved on this day is replaced, exactly as the server replaces
   *  the day's cells; the copy is a draft until it is submitted. */
  copyYesterday(): Promise<boolean> {
    return this.write('/student/ledger/copy-yesterday', { day: this.day() });
  }

  /** Save first, then submit. Submitting what is on screen rather than what was
   *  last written is the only behaviour that matches the button's label.
   *
   *  The day is the one on screen when the button was pressed, taken before
   *  the save is awaited. The stepper stays live while it saves, and reading
   *  `day()` afterwards submitted whichever day the student had stepped to —
   *  a day they never chose, and a submitted day cannot be reopened. If they
   *  have stepped away by the time the save answers, the submit is not sent
   *  at all: the save stands, the strip shows the day as a draft, and
   *  submitting it is one press when they go back to it. */
  async submitDay(): Promise<void> {
    const day = this.day();
    const seq = this.loadSeq;
    if (this.dirty() && !(await this.save())) return;
    if (seq !== this.loadSeq) return;
    await this.write('/student/ledger/submit', { day });
  }

  // --- helpers used by the template ---------------------------------------

  trackKey = (_: number, item: { key: string }) => item.key;
}
