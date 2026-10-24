/**
 * Mentor Notebook — the staff-private mentoring log, one card, one student at
 * a time.
 *
 * Every entry is a row of the printed mentoring log the handoff draws: Date,
 * Key discussions, Follow up, Remarks (On track / Watch / Done / Escalate).
 * It is written to the Phase 4 notebook (`/v1/mentor/notebook/...`), which
 * already carries the private-draft-vs-published distinction as REAL columns
 * (`visibility`, `status`) rather than a flag this screen invents: an entry is
 * PRIVATE_STAFF + DRAFT until the mentor publishes it, and only then does the
 * student's Mentor Meeting Log read it. The footer's sentence about publishing
 * is therefore a description of the row's lifecycle, and the row carries the
 * control that performs it.
 *
 * The log's three extra cells ride in the entry's `structured_data` JSON —
 * `follow_up` and `remark` — with the discussion as the entry's `body`, so an
 * entry written from the older free-text compose still renders here (its
 * follow-up and remark are simply blank, shown as a dash, never invented).
 *
 * Rule 2 is the server's: a MENTOR with no group gets an empty mentee list and
 * this screen says so, rather than reading "nobody" as "everybody".
 */

import { DatePipe } from '@angular/common';
import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { environment } from '../../../../environments/environment';

interface Mentee {
  student_id: string;
  name: string;
  usn: string | null;
  current_stage: string;
  current_semester: number;
}

interface NotebookEntry {
  id: string;
  title: string | null;
  body: string;
  entry_type: string;
  structured_data: Record<string, unknown>;
  visibility: 'PRIVATE_STAFF' | 'STUDENT_VISIBLE';
  status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED';
  meeting_at: string | null;
  published_at: string | null;
  created_at: string;
  version: number;
}

/** The four remarks the printed log allows, in the order the sheet lists them. */
export const REMARKS = ['On track', 'Watch', 'Done', 'Escalate'] as const;
type Remark = (typeof REMARKS)[number];

/** Remark -> chip tone. Text and colour travel together, never colour alone. */
const REMARK_TONE: Record<Remark, 'good' | 'warn' | 'neutral' | 'risk'> = {
  'On track': 'good',
  Watch: 'warn',
  Done: 'neutral',
  Escalate: 'risk',
};

function todayIso(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

@Component({
  selector: 'app-mentor-notebook',
  standalone: true,
  imports: [DatePipe, FormsModule],
  templateUrl: './mentor-notebook.component.html',
  styleUrl: './mentor-notebook.component.scss',
})
export class MentorNotebookComponent {
  private readonly apiBase = environment.apiBase;
  readonly remarks = REMARKS;

  /// null = loading; [] = rule 2's honest "nobody".
  readonly mentees = signal<Mentee[] | null>(null);
  readonly selectedId = signal<string | null>(null);
  /// null = loading the selected student's log.
  readonly entries = signal<NotebookEntry[] | null>(null);
  readonly error = signal<string | null>(null);

  readonly formOpen = signal(false);
  readonly saving = signal(false);
  readonly formError = signal<string | null>(null);
  /// The entry a publish/remove request is in flight for.
  readonly busyId = signal<string | null>(null);

  readonly selected = computed(
    () => (this.mentees() ?? []).find((m) => m.student_id === this.selectedId()) ?? null,
  );

  // Log form. The date defaults to today because the design's brief is
  // "capture the meeting while it is fresh".
  logDate = todayIso();
  logDiscussion = '';
  logFollowup = '';
  logRemark: Remark = 'On track';

  constructor() {
    void this.loadMentees();
  }

  private async loadMentees(): Promise<void> {
    try {
      const res = await fetch(`${this.apiBase}/v1/mentor/mentees`, { credentials: 'include' });
      if (!res.ok) throw new Error('Could not load your mentees.');
      const list = (await res.json()) as Mentee[];
      this.mentees.set(list);
      if (list.length) this.select(list[0].student_id);
    } catch (error) {
      this.mentees.set([]);
      this.error.set(error instanceof Error ? error.message : 'Could not reach the server.');
    }
  }

  select(id: string): void {
    if (!id || id === this.selectedId()) return;
    this.selectedId.set(id);
    this.entries.set(null);
    this.formError.set(null);
    void this.loadEntries(id);
  }

  private async loadEntries(id: string): Promise<void> {
    try {
      const res = await fetch(`${this.apiBase}/v1/mentor/notebook/students/${id}/entries`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Could not load the mentoring log.');
      const list = (await res.json()) as NotebookEntry[];
      // Ignore a late response for a student the mentor has moved away from.
      if (this.selectedId() === id) this.entries.set(list);
    } catch (error) {
      if (this.selectedId() === id) {
        this.entries.set([]);
        this.error.set(error instanceof Error ? error.message : 'Could not reach the server.');
      }
    }
  }

  toggleForm(): void {
    this.formOpen.update((open) => !open);
    this.formError.set(null);
  }

  /** The row's cells, read off the entry. Missing = a dash, never a guess. */
  followup(entry: NotebookEntry): string {
    const v = entry.structured_data?.['follow_up'];
    return typeof v === 'string' && v.trim() ? v : '—';
  }

  remark(entry: NotebookEntry): Remark | null {
    const v = entry.structured_data?.['remark'];
    return (REMARKS as readonly string[]).includes(String(v)) ? (v as Remark) : null;
  }

  remarkTone(remark: Remark): string {
    return REMARK_TONE[remark];
  }

  isPublished(entry: NotebookEntry): boolean {
    return entry.status === 'PUBLISHED' && entry.visibility === 'STUDENT_VISIBLE';
  }

  async saveEntry(): Promise<void> {
    const studentId = this.selectedId();
    const discussion = this.logDiscussion.trim();
    if (!studentId) return;
    if (!discussion) {
      this.formError.set('Write the key discussions first.');
      return;
    }
    this.saving.set(true);
    this.formError.set(null);
    this.error.set(null);
    try {
      // Local noon, so the calendar date the mentor picked survives the trip
      // through UTC in every Indian and every other timezone.
      const meetingAt = new Date(`${this.logDate}T12:00:00`);
      const res = await fetch(`${this.apiBase}/v1/mentor/notebook/students/${studentId}/entries`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': crypto.randomUUID(),
        },
        body: JSON.stringify({
          entry_type: 'MEETING',
          template_key: 'meeting',
          title: null,
          body: discussion,
          structured_data: { follow_up: this.logFollowup.trim(), remark: this.logRemark },
          meeting_at: isNaN(meetingAt.getTime()) ? null : meetingAt.toISOString(),
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail ?? 'Could not save the entry.');
      }
      this.logDiscussion = '';
      this.logFollowup = '';
      this.logRemark = 'On track';
      this.formOpen.set(false);
      await this.loadEntries(studentId);
    } catch (error) {
      this.formError.set(error instanceof Error ? error.message : 'Could not save the entry.');
    } finally {
      this.saving.set(false);
    }
  }

  /** Publish: the one act that lets the student read the row. */
  async publish(entry: NotebookEntry): Promise<void> {
    await this.command(entry, 'publish', 'Could not publish the entry.');
  }

  /** Remove: the server archives (soft-deletes) — the revision history stays. */
  async remove(entry: NotebookEntry): Promise<void> {
    const published = this.isPublished(entry);
    const ok = window.confirm(
      published
        ? 'Remove this entry? The student can already see it on their Mentor Meeting Log; it will disappear from there too.'
        : 'Remove this entry from the log?',
    );
    if (!ok) return;
    await this.command(entry, 'archive', 'Could not remove the entry.');
  }

  private async command(entry: NotebookEntry, verb: string, failure: string): Promise<void> {
    this.busyId.set(entry.id);
    this.error.set(null);
    try {
      const res = await fetch(`${this.apiBase}/v1/mentor/notebook/entries/${entry.id}/${verb}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail ?? failure);
      }
      const id = this.selectedId();
      if (id) await this.loadEntries(id);
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : failure);
    } finally {
      this.busyId.set(null);
    }
  }
}
