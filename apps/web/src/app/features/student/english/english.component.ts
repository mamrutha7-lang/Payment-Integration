/**
 * English Proficiency Baseline — the Reboot stage's CEFR-aligned assessment.
 *
 * A PENDING SECTION IS NOT A ZERO, and this screen is where that matters most:
 * the score is printed in a 27px numeral, so a speaking section that has not
 * been taken rendering as a confident "0" tells a student they failed something
 * they have not sat. The API sends `score: null` for a pending section
 * (app/models/english_baseline.py explains why the column is nullable), and the
 * template branches on `status`, never on a falsy score — `score || '--'` would
 * also swallow a genuine 0.
 *
 * "Provisional band" is likewise derived server-side from how many sections are
 * scored, so the word cannot outlive the section that resolves it.
 */

import { Component, signal } from '@angular/core';
import { DatePipe, LowerCasePipe } from '@angular/common';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { featureRefusal } from '../../../core/feature-refusal';

interface Subscore {
  label: string;
  value: number | null;
}

interface Section {
  skill: string;
  label: string;
  icon: string;
  status: 'SCORED' | 'PENDING';
  score: number | null;
  band: string | null;
  minutes: number;
  subscores: Subscore[];
  has_report: boolean;
  ai_report: string | null;
}

interface NextStep {
  title: string;
  sub: string;
  target: string | null;
}

interface Baseline {
  exists: boolean;
  status: string;
  overall_score: number | null;
  band: string | null;
  band_label: string | null;
  provisional: boolean;
  taken_on: string | null;
  sections_scored: number;
  sections_total: number;
  progress_percent: number;
  pending_label: string | null;
  report_available: boolean;
  strengths: string[];
  focus_areas: string[];
  next_steps: NextStep[];
  sections: Section[];
}

@Component({
  selector: 'app-english-baseline',
  standalone: true,
  imports: [DatePipe, LowerCasePipe, RouterLink],
  templateUrl: './english.component.html',
  styleUrl: './english.component.scss',
})
export class EnglishBaselineComponent {
  readonly state = signal<'loading' | 'data' | 'error'>('loading');
  readonly data = signal<Baseline | null>(null);
  /** The office's message when this screen is switched off for the student;
   *  null for every other failure, which keeps the screen's own line. */
  readonly refusal = signal<string | null>(null);
  readonly busy = signal(false);
  readonly notice = signal<string | null>(null);

  /** Which section's assessor note is expanded. One at a time — these are
   *  paragraphs, and four open at once turns a scannable row of cards into a
   *  wall the student has to hunt through. */
  readonly openReport = signal<string | null>(null);

  constructor() {
    void this.load();
  }

  /** The first section still to be sat, lower-cased for the hero's sentence
   *  ("until the speaking section is submitted"). Read from the sections, not
   *  parsed out of `pending_label`, so a relabelled chip cannot break it. */
  firstPending(d: Baseline): string | null {
    const s = d.sections.find((x) => x.status !== 'SCORED');
    return s ? s.label.toLowerCase() : null;
  }

  toggleReport(skill: string): void {
    this.openReport.update((cur) => (cur === skill ? null : skill));
  }

  /** Open this semester's attempt, or resume the one already open. The endpoint
   *  is idempotent, so a double-tap is harmless and needs no guard beyond the
   *  busy flag that stops the second spinner. */
  async startOrResume(): Promise<void> {
    this.busy.set(true);
    this.notice.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/english-baseline/start`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) throw new Error(String(res.status));
      const body = (await res.json()) as { created: boolean; baseline: Baseline };
      this.data.set(body.baseline);
      this.notice.set(
        body.created
          ? 'Assessment opened. Your sections are listed below — take them in any order.'
          : 'Resuming the attempt already open for this semester.',
      );
    } catch {
      this.notice.set('Could not open the assessment. Please try again.');
    } finally {
      this.busy.set(false);
    }
  }

  /** The PDF is rendered server-side and streamed back. Fetched as a blob rather
   *  than linked directly so the session cookie rides along and a failure can be
   *  reported in the page instead of replacing it with a browser error. */
  async downloadReport(): Promise<void> {
    this.busy.set(true);
    this.notice.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/english-baseline/report`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(String(res.status));
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'english-baseline.pdf';
      a.click();
      // Revoked on the next tick: revoking synchronously can beat the click in
      // some browsers and hand the user an empty file.
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch {
      this.notice.set('Could not produce the report just now. Please try again.');
    } finally {
      this.busy.set(false);
    }
  }

  async load(): Promise<void> {
    this.state.set('loading');
    this.refusal.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/english-baseline`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.refusal.set(await featureRefusal(res));
        this.state.set('error');
        return;
      }
      this.data.set((await res.json()) as Baseline);
      this.state.set('data');
    } catch {
      this.state.set('error');
    }
  }
}
