/**
 * Student leaderboards — the "Leaderboards" panel (mockup data-p="leaderboards").
 *
 * Five boards as a .tabs-row — Overall / Skills / VTU results / Streak / Mocks
 * taken — each rendering the .lb-row ranking visual — rank pill, initials
 * avatar, name, and the metric total, with the viewer's own row highlighted
 * (.me).
 *
 * Reworked for motivation + privacy (UX audit):
 *   - a prominent "You're Rank N of {cohort_size}" card for the viewer's own
 *     position, with encouraging framing for lower ranks (top X% / a nudge);
 *   - batch context ("of N") wherever a rank appears;
 *   - a per-board scoring + refresh explainer;
 *   - a "Hide me from leaderboards" control that drives
 *     PUT /student/leaderboard-visibility and reflects the opt-out state.
 *
 * Wired to GET /student/leaderboards?board=<key>, which ranks the caller's
 * batch and honours the leaderboard opt-out in both directions (an opted-out
 * student sees no ranks, and appears on none).
 *
 * ONLY A CLASSMATE WITH A RECORD ON A BOARD IS RANKED, AND EQUAL TOTALS SHARE A
 * RANK (2026-09-17). The server used to rank the whole roster at zero, so the
 * "not ranked here yet" card below was unreachable and "Rank 2 of 30" sat under
 * a student holding nothing. Rows are tracked by `student_id` because two rows
 * can now carry the same rank, and `cohort_size` is the number of students
 * ranked on this board — which the column header and the explainer now say.
 *
 * THE BOARD IS THE BATCH, AND THE BATCH IS ON THE SCREEN (2026-09-22). The
 * server says WHO the student is ranked against (`scope`: their batch, else the
 * department they named, else nobody) and the line under the title prints it,
 * because "your batch has no results yet" and "you are not seated in a batch"
 * look identical as an empty table and are fixed by different people. The
 * batch mates who hold nothing on a board yet are listed BELOW the ranking by
 * name — still not ranked, still no confident zero, but no longer invisible,
 * which is what a fresh batch used to be. And `overall` is the rank across the
 * four component boards, each worth up to 25 points scaled to the batch's
 * best; it is the tab the screen opens on.
 */

import { Component, computed, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { failedReadMessage } from '../../../core/feature-refusal';
import {
  LeaderboardScope,
  emptyBoardSentence,
  scopeNoun,
  scopeSentence,
} from './leaderboard-scope';

/** One ranked batch peer — matches the FastAPI LeaderRow. */
interface LbEntry {
  rank: number;
  student_id: string;
  initials: string;
  name: string;
  is_me: boolean;
  value_label: string;
}

/** A batch mate with nothing on this board yet — the FastAPI LeaderPeer. */
interface LbPeer {
  student_id: string;
  initials: string;
  name: string;
  is_me: boolean;
}

/** GET /student/leaderboards?board= — the LeaderboardOut shape. */
interface LeaderboardResponse {
  board: string;
  opted_out: boolean;
  scope: LeaderboardScope;
  scope_label: string | null;
  classmates: number;
  cohort_size: number;
  rows: LbEntry[];
  unranked: LbPeer[];
  unranked_total: number;
}

interface Tab {
  key: string;
  /** Tab label. */
  label: string;
  /** How this board is scored — shown in the explainer note. */
  scored: string;
  /** What puts a student on this board — the "not ranked yet" card's sentence. */
  first: string;
}

@Component({
  selector: 'app-student-leaderboards',
  standalone: true,
  templateUrl: './leaderboards.component.html',
  styleUrl: './leaderboards.component.scss',
})
export class LeaderboardsComponent {
  // Certificates came out of the board set: a certificate is evidence for a
  // skill rather than an achievement of its own, and the Skills board already
  // ranks on what those certificates were verified into.
  readonly tabs: Tab[] = [
    {
      key: 'overall',
      label: 'Overall',
      scored:
        'skills, VTU results, streak and mocks together — each is worth up to 25 points, scaled against the best in your batch, and the four add up to 100',
      first:
        'Any record puts you here — a verified skill, a semester result, a sign-in streak or a finished mock — and each one adds points.',
    },
    {
      key: 'skills',
      label: 'Skills',
      scored: 'skills verified on Skilling',
      first: 'Get a skill verified on Skilling and you’ll appear on this board.',
    },
    {
      key: 'vtu',
      label: 'VTU results',
      scored: 'your latest recorded CGPA',
      first: 'You’ll appear here once a semester result is recorded for you.',
    },
    {
      key: 'streak',
      label: 'Streak',
      scored: 'active-day count',
      first: 'Your sign-ins are counted from today — you’ll appear here shortly.',
    },
    {
      key: 'mocks',
      label: 'Mocks taken',
      scored: 'mocks completed',
      first: 'Finish a mock interview through to its verdict and you’ll appear on this board.',
    },
  ];

  readonly active = signal<string>('overall');
  readonly rows = signal<LbEntry[]>([]);
  readonly cohortSize = signal(0);
  readonly loading = signal(true);
  readonly optedOut = signal(false);
  readonly error = signal<string | null>(null);

  // Who the board covers, in the server's words.
  readonly scope = signal<LeaderboardScope>('batch');
  readonly scopeLabel = signal<string | null>(null);
  readonly classmates = signal(0);
  readonly unranked = signal<LbPeer[]>([]);
  readonly unrankedTotal = signal(0);

  // Hide-me control state.
  readonly savingVisibility = signal(false);
  readonly visibilityMsg = signal<string | null>(null);

  readonly activeTab = computed(
    () => this.tabs.find((t) => t.key === this.active()) ?? this.tabs[0],
  );
  readonly activeLabel = computed(() => this.activeTab().label);

  /** The line under the title: which batch (or department) this ranks. */
  readonly scopeLine = computed(() => scopeSentence(this.scope(), this.scopeLabel()));
  readonly emptyLine = computed(() =>
    emptyBoardSentence(this.scope(), this.activeLabel(), this.classmates()),
  );

  /** The viewer's own ranked row on the current board, if present. */
  readonly myRow = computed(() => this.rows().find((r) => r.is_me) ?? null);

  /** Encouraging, batch-aware framing of the viewer's own position. */
  readonly ownStanding = computed<{
    ranked: boolean;
    headline: string;
    encouragement: string;
  }>(() => {
    const me = this.myRow();
    const n = this.cohortSize();
    if (!me) {
      return {
        ranked: false,
        headline: 'You’re not ranked here yet',
        encouragement: this.activeTab().first,
      };
    }
    const headline = `You’re Rank ${me.rank} of ${n}`;
    return { ranked: true, headline, encouragement: this.encourage(me.rank, n) };
  });

  /** The scoring + refresh explainer for the active board. */
  readonly explainer = computed(
    () =>
      `Ranked by ${this.activeTab().scored}. Only classmates with a record here are ranked; equal totals share a rank. Updates as records change.`,
  );

  constructor() {
    void this.load();
  }

  setTab(key: string): void {
    if (key === this.active()) return;
    this.active.set(key);
    void this.load();
  }

  /** Constructive framing so a lower rank reads as progress, not a verdict. */
  private encourage(rank: number, ranked: number): string {
    const group = scopeNoun(this.scope());
    if (ranked <= 1) {
      return 'You’re the only ranked student here right now — a strong start.';
    }
    if (rank === 1) {
      return `Top of the board — leading your ${group}. Keep it up.`;
    }
    const pct = Math.max(1, Math.round((rank / ranked) * 100));
    if (pct <= 25) {
      return `You’re in the top ${pct}% of your ${group} — strong standing.`;
    }
    if (rank <= Math.ceil(ranked / 2)) {
      return `You’re in the top half of your ${group}. Keep going.`;
    }
    const ahead = rank - 1;
    return `Keep going — ${ahead} ${ahead === 1 ? 'peer is' : 'peers are'} ahead. Every record you add climbs the board.`;
  }

  /** Bumped by every read. Tabs can be pressed faster than boards answer,
   *  and the answers arrive in any order: only the board asked for last is
   *  drawn, or a quick Skills-then-Streak could end on Skills' rows under the
   *  Streak tab. */
  private loadSeq = 0;

  private async load(): Promise<void> {
    const seq = ++this.loadSeq;
    this.loading.set(true);
    this.error.set(null);
    try {
      const res = await fetch(
        `${environment.apiBase}/student/leaderboards?board=${encodeURIComponent(this.active())}`,
        { credentials: 'include' },
      );
      if (!res.ok) {
        const message = await failedReadMessage(res, 'Could not load the leaderboard.');
        if (seq !== this.loadSeq) return;
        this.error.set(message);
        this.clear();
        return;
      }
      const body = (await res.json()) as LeaderboardResponse;
      if (seq !== this.loadSeq) return;
      this.optedOut.set(body.opted_out);
      this.scope.set(body.scope ?? 'batch');
      this.scopeLabel.set(body.scope_label ?? null);
      this.classmates.set(body.classmates ?? 0);
      this.cohortSize.set(body.cohort_size ?? 0);
      this.rows.set(body.rows ?? []);
      this.unranked.set(body.unranked ?? []);
      this.unrankedTotal.set(body.unranked_total ?? 0);
    } catch {
      if (seq !== this.loadSeq) return;
      this.error.set('Could not reach the server.');
      this.clear();
    } finally {
      if (seq === this.loadSeq) this.loading.set(false);
    }
  }

  private clear(): void {
    this.rows.set([]);
    this.cohortSize.set(0);
    this.unranked.set([]);
    this.unrankedTotal.set(0);
  }

  /** Flip the leaderboard opt-out via PUT /student/leaderboard-visibility, then
   * reload so the board reflects the new state. */
  async setHidden(hidden: boolean): Promise<void> {
    if (this.savingVisibility()) return;
    this.savingVisibility.set(true);
    this.visibilityMsg.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/leaderboard-visibility`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hidden }),
      });
      if (!res.ok) {
        this.visibilityMsg.set('Could not update your visibility. Please try again.');
        return;
      }
      const body = (await res.json()) as { hidden: boolean };
      this.optedOut.set(body.hidden);
      this.visibilityMsg.set(
        body.hidden
          ? 'You’re now hidden from the leaderboards.'
          : 'You’re now visible on the leaderboards.',
      );
      await this.load();
    } catch {
      this.visibilityMsg.set('Could not reach the server.');
    } finally {
      this.savingVisibility.set(false);
    }
  }
}
