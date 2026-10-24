/**
 * Student > Mock interviews — the durable record of every interview taken.
 *
 * The assistant screen is the live interview and forgets everything the moment
 * the tab closes. This screen is the other half: `interview_sessions`,
 * `interview_turns` and `interview_evaluations`, read back through the four
 * student endpoints in app/routers/interview_records.py. The subject is always
 * the caller — those endpoints take no `student_id` and adding one "for
 * symmetry" would make every one of them an IDOR the moment somebody forgot the
 * filter (docs/interview-engine-v3.md §7.3).
 *
 * WHY THIS SCREEN HAD TO EXIST THE DAY THE TABLES DID. The consent panel now
 * tells the student, in as many words, that their transcript is kept on the
 * college's server and that their mentor and the placement office can read
 * it. A promise like that with no way for the student to see the same record is
 * not a disclosure, it is a notice. This is where they read what was kept.
 *
 * LAZY, AND IT MUST STAY LAZY. Reached only through a `loadComponent` in
 * app.routes.ts. `main` is ~142 kB and the initial budget is a backstop against
 * a heavy route going eager, not a per-route lint — a static `component:`
 * reference here would pull this screen, the report card and everything they
 * import into the chunk a student downloads before the login form paints.
 */

import { Component, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { InterviewReportCardComponent, ReportCardView } from './interview-report-card.component';

/** Row shape of GET /api/interview/sessions, verbatim from InterviewSessionOut. */
interface SessionRow {
  id: string;
  specialization: string | null;
  status: string;
  terminal_reason: string | null;
  final_phase: string | null;
  answers_accepted: number;
  close_code: number | null;
  audio_recorded: boolean;
  started_at: string;
  ended_at: string | null;
  /** NULL means no evaluation row AT ALL — still running, or older than the
   *  scorecard. That is a different sentence from 'unavailable', which is the
   *  record that a report was attempted and did not arrive. */
  report_status: string | null;
  overall_score: number | null;
}

/** Row shape of .../transcript, verbatim from InterviewTurnOut. */
interface TurnRow {
  seq: number;
  speaker: string;
  phase: string;
  content: string;
  transcription_status: string;
  answer_quality: string | null;
  counted_as_answer: boolean;
  is_partial: boolean;
  created_at: string;
}

/** Row shape of .../report, verbatim from InterviewReportOut. No
 *  `raw_response` — that field exists only on the Main Admin's model, and its
 *  ABSENCE is how the server says "not yours to read" rather than "the model
 *  returned nothing". Do not add it here hoping it will arrive. */
interface ReportRow {
  report_status: string;
  overall_score: number | null;
  communication_score: number | null;
  domain_score: number | null;
  structure_score: number | null;
  strengths: string[] | null;
  improvements: string[] | null;
  drill: string | null;
  summary: string | null;
  model: string | null;
  generated_at: string;
}

/** A transcript line with the two things the raw row cannot say by itself. */
interface TurnView extends TurnRow {
  /** First turn of its phase — the template draws a divider, so the phase is
   *  named once per stage instead of on every line. */
  phaseStart: boolean;
  /** Non-null when the row has no text and the reason is worth saying out loud
   *  (L4). An empty `content` is NOT "they said nothing". */
  unheard: string | null;
  /** Non-null when a student turn did not advance the interview, and why. */
  discounted: string | null;
}

interface Chip {
  tone: 'good' | 'warn' | 'risk' | 'neutral';
  icon: string;
  label: string;
}

/**
 * The Specialization Matrix keys, in words. Mirrors SPECIALIZATIONS in
 * apps/api-py/app/interview_matrix.py, which is the source of truth; an unknown
 * key degrades to itself rather than to a blank cell, so a track added there and
 * not here is legible instead of invisible.
 */
const TRACK_LABELS: Record<string, string> = {
  hr: 'Human Resources',
  dm: 'Digital Marketing',
  ba: 'Business Analytics',
  fa: 'Financial Analytics',
};

/** InterviewPhase, in words. Same source, same degradation. */
const PHASE_LABELS: Record<string, string> = {
  opening: 'Opening question',
  probing: 'Framework probing',
  deep_dive: 'Deep dive',
  wrap_up: 'Wrap-up',
  ended: 'Finished',
};

/**
 * `interview_sessions.status`, as the student reads it.
 *
 * `abandoned` is the one worth care: it is the ordinary outcome of pressing End
 * or running out of time, not a fault, so it is 'warn' with a neutral word
 * rather than 'risk' with an alarming one. `failed` is genuinely a fault and
 * says so.
 */
const SESSION_STATUS: Record<string, Chip> = {
  running: { tone: 'neutral', icon: 'pending', label: 'In progress' },
  completed: { tone: 'good', icon: 'task_alt', label: 'Completed' },
  abandoned: { tone: 'warn', icon: 'logout', label: 'Ended early' },
  failed: { tone: 'risk', icon: 'error', label: 'Failed' },
};

/** `interview_evaluations.report_status` for the LIST column. The card itself
 *  carries the long-form explanation; this is the one-glance version. */
const REPORT_STATUS: Record<string, Chip> = {
  ok: { tone: 'good', icon: 'task_alt', label: 'Ready' },
  unavailable: { tone: 'neutral', icon: 'schedule', label: 'None' },
  timeout: { tone: 'warn', icon: 'hourglass_disabled', label: 'Unavailable' },
  unparseable: { tone: 'warn', icon: 'help', label: 'Unavailable' },
  rejected: { tone: 'warn', icon: 'block', label: 'Unavailable' },
};

/** Why a student turn did not advance the interview. NULL quality is not in
 *  here on purpose: it means the transcription never arrived, which `unheard`
 *  already explains in the student's own line. */
const ANSWER_QUALITY: Record<string, string> = {
  empty: 'Nothing was transcribed, so the interviewer asked again.',
  filler: 'Read as a filler word, so the interviewer asked again.',
  too_short: 'Too short to score, so the interviewer asked again.',
  skipped: 'You asked to move on, so this question was skipped and not scored.',
  echo: 'This was the interviewer’s own voice picked up by your microphone, so it was not counted.',
};

@Component({
  selector: 'app-student-interviews',
  standalone: true,
  imports: [RouterLink, InterviewReportCardComponent],
  templateUrl: './interviews.component.html',
  styleUrl: './interviews.component.scss',
})
export class InterviewsComponent {
  readonly sessions = signal<SessionRow[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  readonly selectedId = signal<string | null>(null);
  readonly detail = signal<SessionRow | null>(null);
  readonly turns = signal<TurnRow[]>([]);
  readonly report = signal<ReportRow | null>(null);
  /**
   * The report endpoint answered 404: there is no evaluation row at all. That
   * is NOT 'unavailable' — a row saying 'unavailable' is the record that a
   * report was attempted, and no row says nothing was ever attempted. The two
   * get different sentences below.
   */
  readonly reportMissing = signal(false);
  readonly detailLoading = signal(false);
  readonly detailError = signal<string | null>(null);

  // --- counts. Deliberately no "latest score" tile: a bare number with no
  // calibration beside it is exactly what §5.4 refused to ship, and a stat tile
  // has nowhere to put the sentence that makes it honest. Scores live in the
  // report card, where that sentence is.
  readonly totalCount = computed(() => this.sessions().length);
  readonly completedCount = computed(
    () => this.sessions().filter((s) => s.status === 'completed').length,
  );
  readonly reportCount = computed(
    () => this.sessions().filter((s) => s.report_status === 'ok').length,
  );

  /** The transcript, annotated with what the raw rows cannot say. */
  readonly turnViews = computed<TurnView[]>(() => {
    let phase = '';
    return this.turns().map((t) => {
      const phaseStart = t.phase !== phase;
      phase = t.phase;
      const blank = t.content.trim().length === 0;
      return {
        ...t,
        phaseStart,
        unheard: blank ? this.unheardReason(t) : null,
        discounted:
          t.speaker === 'student' && !t.counted_as_answer && !blank && t.answer_quality
            ? (ANSWER_QUALITY[t.answer_quality] ?? null)
            : null,
      };
    });
  });

  /** The report in the card's shape, or null when there is nothing to render. */
  readonly reportView = computed<ReportCardView | null>(() => {
    const r = this.report();
    if (r === null) return null;
    return {
      status: r.report_status,
      overall: r.overall_score,
      communication: r.communication_score,
      domain: r.domain_score,
      structure: r.structure_score,
      strengths: r.strengths ?? [],
      improvements: r.improvements ?? [],
      drill: r.drill ?? '',
      summary: r.summary ?? '',
      generatedAt: r.generated_at,
    };
  });

  constructor() {
    void this.loadSessions();
  }

  // ------------------------------------------------------------------ //
  // Loading                                                            //
  // ------------------------------------------------------------------ //

  private async loadSessions(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/interview/sessions`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.error.set('Could not load your interviews.');
        return;
      }
      this.sessions.set((await res.json()) as SessionRow[]);
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.loading.set(false);
    }
  }

  /** Open one interview. Clicking the open row closes it again. */
  async select(id: string): Promise<void> {
    if (this.selectedId() === id) {
      this.selectedId.set(null);
      return;
    }
    this.selectedId.set(id);
    this.detail.set(null);
    this.turns.set([]);
    this.report.set(null);
    this.reportMissing.set(false);
    this.detailError.set(null);
    this.detailLoading.set(true);

    // Three requests in parallel: they are independent reads and serialising
    // them would triple the wait for a screen that is already one navigation
    // deep. The report is allowed to 404 — that is a legitimate answer, not a
    // failure, so it is settled separately from the other two.
    try {
      const base = `${environment.apiBase}/interview/sessions/${encodeURIComponent(id)}`;
      const [detailRes, turnsRes, reportRes] = await Promise.all([
        fetch(base, { credentials: 'include' }),
        fetch(`${base}/transcript`, { credentials: 'include' }),
        fetch(`${base}/report`, { credentials: 'include' }),
      ]);
      // The student navigated on while this was in flight.
      if (this.selectedId() !== id) return;

      if (!detailRes.ok) {
        this.detailError.set('Could not load that interview.');
        return;
      }
      this.detail.set((await detailRes.json()) as SessionRow);
      if (turnsRes.ok) this.turns.set((await turnsRes.json()) as TurnRow[]);
      if (reportRes.ok) this.report.set((await reportRes.json()) as ReportRow);
      else if (reportRes.status === 404) this.reportMissing.set(true);
    } catch {
      if (this.selectedId() === id) this.detailError.set('Could not reach the server.');
    } finally {
      if (this.selectedId() === id) this.detailLoading.set(false);
    }
  }

  // ------------------------------------------------------------------ //
  // Presentation                                                       //
  // ------------------------------------------------------------------ //

  trackLabel(key: string | null): string {
    if (!key) return 'General interview';
    return TRACK_LABELS[key] ?? key;
  }

  phaseLabel(key: string | null): string {
    if (!key) return '';
    return PHASE_LABELS[key] ?? key;
  }

  statusChip(status: string): Chip {
    return SESSION_STATUS[status] ?? { tone: 'neutral', icon: 'help', label: status };
  }

  /** The report column. A NULL `report_status` is the "no row at all" case and
   *  gets a dash, not a chip — inventing a chip for it would make an interview
   *  that never reached the scorecard look like one that tried and failed. */
  reportChip(status: string | null): Chip | null {
    if (!status) return null;
    return REPORT_STATUS[status] ?? { tone: 'neutral', icon: 'help', label: status };
  }

  /** Minutes:seconds between start and end, or null while it is running. */
  durationLabel(row: SessionRow): string | null {
    if (!row.ended_at) return null;
    const start = new Date(row.started_at).getTime();
    const end = new Date(row.ended_at).getTime();
    if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
    const secs = Math.round((end - start) / 1000);
    return `${Math.floor(secs / 60)}m ${String(secs % 60).padStart(2, '0')}s`;
  }

  fmtDate(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  fmtTime(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }

  /**
   * A student turn with no text, explained.
   *
   * `content = ''` on an `interview_turns` row is legal and load-bearing: it is
   * a transcription that timed out or failed, and the row exists precisely so
   * the record can say so. Rendering it as a blank line would tell the student
   * they said nothing, which is the opposite of what happened.
   */
  private unheardReason(t: TurnRow): string {
    if (t.speaker !== 'student') return 'Nothing was recorded for this turn.';
    if (t.transcription_status === 'timeout')
      return 'We could not transcribe this answer in time — you were heard, but the words were not captured.';
    if (t.transcription_status === 'failed')
      return 'Transcription failed for this answer — you were heard, but the words were not captured.';
    return 'No words were captured for this turn.';
  }
}
