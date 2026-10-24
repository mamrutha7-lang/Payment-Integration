/**
 * The practice scorecard, rendered once and used twice.
 *
 * It appears on the assistant screen the moment `reep.report` lands, and again
 * on the interview history screen from
 * GET /api/interview/sessions/{id}/report — two sources, one shape
 * (`ReportCardView`), because the alternative was the same delicate copy
 * maintained in two templates. The two mappers below are the seam.
 *
 * THE COPY IN HERE IS NOT DECORATION, AND IT MUST NOT BE SOFTENED OR TRIMMED.
 * This is a number a nineteen-year-old will screenshot. It was produced by a
 * model that heard them once, through a laptop microphone, with no human in the
 * loop. docs/interview-engine-v3.md §5.4 weighed hiding the score and decided
 * against it — a score the student cannot see while their mentor can is a secret
 * file on a student — on the explicit condition that the honest fix is
 * CALIBRATION COPY, not concealment. That sentence is therefore rendered with
 * the scores, in the same block, and there is no code path that draws the
 * numbers without it.
 *
 * TWO THINGS THAT LOOK LIKE OVERSIGHTS AND ARE DECISIONS:
 *
 *  1. THE SCORES ARE NOT COLOUR-BANDED. No red for 40, no green for 85. The
 *     repo's rule is text-and-colour-together, never colour alone — and a red
 *     "risk" chip on an AI's guess at a nervous student's performance dresses a
 *     practice number up as a verdict, which is the exact harm §5.4 is guarding
 *     against. Status (did a report arrive at all) is colour-coded, because that
 *     is a fact about the system. The judgement is not.
 *  2. A NULL SCORE RENDERS AS "not scored", NEVER AS 0. The relay refuses to
 *     invent a score the model did not give, `interview_evaluations` keeps every
 *     score column nullable to preserve that, and this is the last place the
 *     distinction could be thrown away. To a student, "not scored" and "zero"
 *     are opposite sentences.
 */

import { Component, computed, input } from '@angular/core';

/** `interview_evaluations.report_status`, verbatim. Anything else renders
 *  through the generic branch rather than blanking the card. */
export type ReportStatus = 'ok' | 'unparseable' | 'timeout' | 'rejected' | 'unavailable';

/** The one shape this card renders. Both sources map onto it. */
export interface ReportCardView {
  status: ReportStatus | string;
  overall: number | null;
  communication: number | null;
  domain: number | null;
  structure: number | null;
  strengths: string[];
  improvements: string[];
  drill: string;
  summary: string;
  /** ISO timestamp, or null for the live socket payload (it is "just now"). */
  generatedAt: string | null;
}

interface ScoreTile {
  label: string;
  value: number | null;
}

interface StatusChip {
  tone: 'good' | 'warn' | 'risk' | 'neutral';
  icon: string;
  label: string;
}

/**
 * Why there is no report, in words the student can act on.
 *
 * EVERY ONE OF THESE OPENS BY SAYING THE INTERVIEW COMPLETED. That is not
 * padding: the relay closes 1000 on all of these and deliberately defines no
 * error close code for a missing scorecard, exactly so a finished interview does
 * not read as a failed one. Copy that led with the failure would undo that
 * decision in the only place the student actually looks.
 */
const MISSING_REPORT: Record<string, StatusChip & { body: string }> = {
  unavailable: {
    tone: 'neutral',
    icon: 'schedule',
    label: 'No report',
    body: 'This interview ended before the wrap-up, so there was nothing to score yet — usually the time limit, or ending early. Everything you said is still saved below.',
  },
  timeout: {
    tone: 'warn',
    icon: 'hourglass_disabled',
    label: 'Report unavailable',
    body: 'The interview finished, but the report took too long to come back. Your answers and the transcript are saved.',
  },
  unparseable: {
    tone: 'warn',
    icon: 'help',
    label: 'Report unavailable',
    body: 'The interview finished, but the report came back in a form this app could not read. Your answers and the transcript are saved.',
  },
  rejected: {
    tone: 'warn',
    icon: 'block',
    label: 'Report unavailable',
    body: 'The interview finished, but the model would not generate the report. Your answers and the transcript are saved.',
  },
};

const GENERIC_MISSING: StatusChip & { body: string } = {
  tone: 'neutral',
  icon: 'help',
  label: 'No report',
  body: 'The interview finished, but no report was generated for it. Your answers and the transcript are saved.',
};

@Component({
  selector: 'interview-report-card',
  standalone: true,
  template: `
    <div class="card rep">
      <div class="rep__head">
        <h4 class="rep__title">Practice report</h4>
        <span class="chip {{ chip().tone }}"
          ><span class="icon">{{ chip().icon }}</span
          >{{ chip().label }}</span
        >
      </div>

      @if (ok()) {
        @if (hasAnyScore()) {
          <div class="dense-grid rep__scores">
            @for (tile of tiles(); track tile.label) {
              <div class="dense-stat">
                <div class="lbl">{{ tile.label }}</div>
                <div class="val rep__score">{{ tile.value === null ? '—' : tile.value }}</div>
                <div class="rep__scorehint">
                  {{ tile.value === null ? 'not scored' : 'out of 100' }}
                </div>
              </div>
            }
          </div>
        } @else {
          <p class="rep__note">
            This report has no scores — the interviewer wrote its notes without them.
          </p>
        }

        <!--
          THE CALIBRATION LINE. It sits between the numbers and the advice, in
          the same block, so there is no arrangement of this card in which a
          student reads a score without it. Do not move it into a tooltip, an
          "info" icon or a collapsed panel: §5.4 accepted showing the number ONLY
          because this sentence travels with it.
        -->
        <p class="rep__calibration">
          <span class="icon">info</span>
          <span>
            Practice score — generated by an AI from one 15-minute session. It is not a placement
            decision and nobody grades you on it. Use the notes below, not the number.
          </span>
        </p>

        @if (view().summary) {
          <p class="rep__summary">{{ view().summary }}</p>
        }

        <div class="rep__cols">
          <div class="rep__col">
            <h5 class="rep__sub">What went well</h5>
            @if (view().strengths.length) {
              <ul class="rep__list">
                @for (item of view().strengths; track $index) {
                  <li>{{ item }}</li>
                }
              </ul>
            } @else {
              <p class="rep__note">The interviewer did not list any.</p>
            }
          </div>
          <div class="rep__col">
            <h5 class="rep__sub">What to work on</h5>
            @if (view().improvements.length) {
              <ul class="rep__list">
                @for (item of view().improvements; track $index) {
                  <li>{{ item }}</li>
                }
              </ul>
            } @else {
              <p class="rep__note">The interviewer did not list any.</p>
            }
          </div>
        </div>

        @if (view().drill) {
          <div class="rep__drill">
            <span class="rep__drilllabel">Practise this next</span>
            <span>{{ view().drill }}</span>
          </div>
        }
      } @else {
        <p class="rep__missing">{{ missing().body }}</p>
        <p class="rep__note">
          Start another mock interview whenever you like — each one is scored on its own.
        </p>
      }

      @if (generatedLabel(); as when) {
        <p class="rep__stamp">Generated {{ when }}</p>
      }
    </div>
  `,
  styles: [
    `
      .rep {
        margin-bottom: 0;
      }
      .rep__head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 14px;
      }
      .rep__title {
        margin: 0;
        font-size: 13.5px;
        font-weight: 700;
        color: var(--ink);
      }
      .rep__scores {
        margin-bottom: 12px;
      }
      /* Deliberately inherits the neutral ink of .dense-stat .val — see the
         "not colour-banded" note in the file header before adding a tint. */
      .rep__score {
        font-variant-numeric: tabular-nums;
      }
      .rep__scorehint {
        margin-top: 2px;
        font-size: 11px;
        color: var(--muted);
      }
      /* THE CALIBRATION LINE. The design system's warn note (01 §4), which is
         where a "read this number carefully" sentence belongs. It was a
         one-off amber nobody else used. Its position — between the scores and
         the advice, in the same block — is fixed by the file header above and
         must not move into a tooltip or a collapsed panel. */
      .rep__calibration {
        display: flex;
        gap: 9px;
        align-items: flex-start;
        margin: 0 0 14px;
        padding: 11px 13px;
        border-radius: var(--r-control);
        border-left: 3px solid var(--warn-dot);
        background: var(--warn-bg);
        color: var(--ink);
        font-size: 12.4px;
        line-height: 1.5;
      }
      .rep__calibration .icon {
        flex: 0 0 auto;
        font-size: 17px;
      }
      .rep__summary {
        margin: 0 0 14px;
        font-size: 13px;
        line-height: 1.6;
        color: var(--ink);
        white-space: pre-wrap;
      }
      .rep__cols {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 18px;
      }
      .rep__sub {
        margin: 0 0 6px;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--muted);
      }
      .rep__list {
        margin: 0;
        padding-left: 18px;
        font-size: 12.6px;
        line-height: 1.6;
        color: var(--ink);
      }
      .rep__list li {
        margin-bottom: 4px;
      }
      .rep__note {
        margin: 0;
        font-size: 12.4px;
        color: var(--muted);
      }
      .rep__missing {
        margin: 0 0 8px;
        font-size: 13px;
        line-height: 1.6;
        color: var(--ink);
      }
      .rep__drill {
        display: flex;
        flex-direction: column;
        gap: 3px;
        margin-top: 16px;
        padding: 12px 14px;
        border-radius: 10px;
        background: var(--tint-2);
        font-size: 12.8px;
        line-height: 1.55;
        color: var(--ink);
      }
      .rep__drilllabel {
        font-size: 10.8px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--muted);
      }
      .rep__stamp {
        margin: 14px 0 0;
        font-size: 11px;
        color: var(--muted);
      }
      @media (max-width: 720px) {
        .rep__cols {
          grid-template-columns: 1fr;
          gap: 14px;
        }
      }
    `,
  ],
})
export class InterviewReportCardComponent {
  readonly view = input.required<ReportCardView>();

  readonly ok = computed(() => this.view().status === 'ok');

  readonly tiles = computed<ScoreTile[]>(() => {
    const v = this.view();
    return [
      { label: 'Overall', value: v.overall },
      { label: 'Communication', value: v.communication },
      { label: 'Domain', value: v.domain },
      { label: 'Structure', value: v.structure },
    ];
  });

  readonly hasAnyScore = computed(() => this.tiles().some((t) => t.value !== null));

  readonly missing = computed(() => MISSING_REPORT[this.view().status] ?? GENERIC_MISSING);

  /** Status as text AND colour, always together. */
  readonly chip = computed<StatusChip>(() =>
    this.ok() ? { tone: 'good', icon: 'task_alt', label: 'Report ready' } : this.missing(),
  );

  readonly generatedLabel = computed(() => {
    const iso = this.view().generatedAt;
    if (!iso) return null;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleString(undefined, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  });
}
