/**
 * Mentor / TPO Log — the student's own 1:1 history, and the SWOC written about
 * them.
 *
 * Named for both because both write here: the assigned mentor and the placement
 * cell. It is VIEW-ONLY apart from requesting a meeting — nothing on this screen
 * approves, verifies or signs anything, and no admin content appears on it.
 *
 * Reads `mentor_notes` filtered to the signed-in student. The screen says
 * plainly that these notes are visible to the student and the placement office,
 * which is why nothing is filtered by author or hidden here: a note a student
 * cannot see is a note that should not have been written on that table.
 *
 * The mentor's internal vocabulary is translated server-side — FLAGGED reads as
 * "Flagged for follow-up" — so this component renders `action_label` and never
 * the raw enum.
 */

import { Component, computed, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { featureRefusal } from '../../../core/feature-refusal';

/**
 * One SWOC line, as /student/overview returns it — B7.5.
 *
 * IT USED TO BE `{ text }` AND THE FOUR BOXES JOINED THE TEXTS WITH A DOT.
 * That was a fair rendering of a payload that carried nothing else, and it is
 * why the quadrant now lists its lines instead: `author`, `recorded_at` and
 * `acknowledged_at` are per ENTRY, and a concatenated string has nowhere to put
 * them. Nothing about the four tiles, their colours or their order changes — the
 * board is the same board, each line simply keeps its own footer now.
 *
 * `author: null` MEANS NOBODY WAS RECORDED, and that is the only thing it can
 * mean: `swoc_entries.author_user_id` is ON DELETE SET NULL, so an account going
 * away clears the pointer rather than leaving a name to fail to resolve. "Author
 * not recorded" is therefore the honest wording; the admin board's old "Author
 * no longer on the roster" said somebody left, about rows nobody ever wrote.
 */
interface SwocItem {
  id: string;
  source: string;
  text: string;
  weight: number;
  author: string | null;
  author_recorded: boolean;
  recorded_at: string;
  acknowledged_at: string | null;
}

interface SwocBoard {
  strengths: SwocItem[];
  weaknesses: SwocItem[];
  opportunities: SwocItem[];
  challenges: SwocItem[];
}

interface Meeting {
  id: string;
  met_on: string;
  day: string;
  month: string;
  title: string;
  location: string | null;
  action: string;
  action_label: string;
  note: string;
  logged_by: string;
}

interface NextMeeting {
  title: string;
  location: string | null;
  starts_at: string;
}

interface MentorLog {
  mentor_name: string | null;
  meetings_logged: number;
  last_meeting: string | null;
  open_actions: number;
  next_meeting: NextMeeting | null;
  meetings: Meeting[];
}

/** The viewpoint, in the student's words rather than the enum's. PM is retired
 *  from the writer and still legal in storage, so it is answered here rather
 *  than left to render as a raw token on a seeded deployment. */
const SOURCE_LABEL: Record<string, string> = {
  MENTOR: 'Your mentor',
  PLACEMENT: 'Placement cell',
  PM: 'Programme',
};

function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? 'Placement cell';
}

/** "12 Mar 2026" — the date a line was written, in the format the meeting log
 *  above it already uses. An unparseable value renders as nothing rather than
 *  as "Invalid Date". */
function when(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime())
    ? ''
    : at.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

@Component({
  selector: 'app-mentor-log',
  standalone: true,
  templateUrl: './mentor-log.component.html',
  styleUrl: './mentor-log.component.scss',
})
export class MentorLogComponent {
  readonly state = signal<'loading' | 'data' | 'error'>('loading');
  readonly data = signal<MentorLog | null>(null);
  /** The office's message when this screen is switched off for the student;
   *  null for every other failure, which keeps the screen's own line. */
  readonly refusal = signal<string | null>(null);

  /** The request form is a DISCLOSURE, not a route. Asking for a 1:1 is three
   *  words and a send; a page transition for it loses the log the student is
   *  looking at while they decide what to say. */
  readonly requesting = signal(false);
  readonly sending = signal(false);
  readonly notice = signal<{ tone: 'good' | 'risk'; text: string } | null>(null);
  readonly reason = signal('');
  readonly preferred = signal('');

  /**
   * SWOC belongs with the mentor log, not on the dashboard.
   * It is written BY the mentor and the placement cell — the caption says so —
   * and on the landing screen it sat among things the student does, where a
   * judgement written about them read as another task. Here it is next to the
   * meetings where it was formed and where it gets revised.
   */
  readonly swoc = signal<SwocBoard | null>(null);

  /** The id of the line currently being acknowledged, so one button spins and
   *  the other three stay usable. */
  readonly acknowledging = signal<string | null>(null);

  readonly swocBoxes = computed(() => {
    const s = this.swoc();
    if (!s) return null;
    const box = (cls: string, title: string, items: SwocItem[]) => ({
      cls: `swoc-box ${cls}`,
      title,
      lines: items.map((item) => ({
        id: item.id,
        text: item.text,
        // WHO AND WHEN, on every line — B7.5, and the reason this card stopped
        // joining the four lists into four sentences. A judgement written about
        // a student with no name and no date on it is a rumour; these two are
        // the difference between "someone thinks this" and "your mentor wrote
        // this on 12 March".
        by: item.author ?? 'Author not recorded',
        when: when(item.recorded_at),
        source: sourceLabel(item.source),
        acknowledgedOn: item.acknowledged_at ? when(item.acknowledged_at) : null,
      })),
    });
    return [
      box('swoc-s', 'Strength', s.strengths),
      box('swoc-w', 'Weakness', s.weaknesses),
      box('swoc-o', 'Opportunity', s.opportunities),
      box('swoc-c', 'Challenge', s.challenges),
    ];
  });

  /**
   * "I have read this." — POST /student/swoc/{id}/acknowledge.
   *
   * ONE WAY ONLY, and the button disappears once it lands: the endpoint keeps
   * the FIRST timestamp and there is no un-acknowledge, because "I read it" is
   * not something a later click makes untrue. The board is patched in place
   * rather than refetched, so the four tiles do not blink for a one-field
   * change; a failure leaves the button where it was and says nothing, since
   * there is nothing the student can do differently.
   */
  async acknowledge(id: string): Promise<void> {
    if (this.acknowledging()) return;
    this.acknowledging.set(id);
    try {
      const res = await fetch(`${environment.apiBase}/student/swoc/${id}/acknowledge`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) return;
      const saved = (await res.json()) as SwocItem;
      const board = this.swoc();
      if (!board) return;
      const patch = (items: SwocItem[]) =>
        items.map((item) => (item.id === id ? { ...item, ...saved } : item));
      this.swoc.set({
        strengths: patch(board.strengths),
        weaknesses: patch(board.weaknesses),
        opportunities: patch(board.opportunities),
        challenges: patch(board.challenges),
      });
    } catch {
      /* the button stays where it was */
    } finally {
      this.acknowledging.set(null);
    }
  }

  /** Its own fetch, so a failing overview leaves the meeting log intact. */
  private async loadSwoc(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/student/overview`, {
        credentials: 'include',
      });
      if (!res.ok) return;
      const body = (await res.json()) as { swoc?: SwocBoard | null };
      this.swoc.set(body.swoc ?? null);
    } catch {
      /* the card simply does not render */
    }
  }

  constructor() {
    void this.load();
    void this.loadSwoc();
  }

  openRequest(): void {
    this.notice.set(null);
    this.requesting.set(true);
  }

  cancelRequest(): void {
    this.requesting.set(false);
    this.reason.set('');
    this.preferred.set('');
  }

  async sendRequest(): Promise<void> {
    const reason = this.reason().trim();
    if (!reason) return;
    this.sending.set(true);
    this.notice.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/mentor-meetings/request`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason, preferred: this.preferred().trim() || null }),
      });
      const body = (await res.json().catch(() => ({}))) as {
        detail?: string;
        detail_message?: string;
      };
      if (!res.ok) {
        // A student with no mentor gets a real explanation from the server —
        // showing it verbatim is more useful than "something went wrong".
        this.notice.set({ tone: 'risk', text: body.detail ?? 'Could not send that request.' });
        return;
      }
      this.cancelRequest();
      this.notice.set({
        tone: 'good',
        text: (body as { detail?: string }).detail ?? 'Request sent.',
      });
      await this.load();
    } catch {
      this.notice.set({ tone: 'risk', text: 'Could not reach the server. Please try again.' });
    } finally {
      this.sending.set(false);
    }
  }

  async load(): Promise<void> {
    this.state.set('loading');
    this.refusal.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/mentor-meetings`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.refusal.set(await featureRefusal(res));
        this.state.set('error');
        return;
      }
      this.data.set((await res.json()) as MentorLog);
      this.state.set('data');
    } catch {
      this.state.set('error');
    }
  }
}
