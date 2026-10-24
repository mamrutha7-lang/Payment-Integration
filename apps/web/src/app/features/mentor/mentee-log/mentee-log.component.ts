/**
 * Faculty Mentee Log — the staff side of the student's Mentor Meeting Log.
 *
 * Left: the mentees this staff member may see (GET /mentor/mentees — a MENTOR
 * gets only their own group, the Main Admin the whole programme, and a MENTOR
 * with no group gets an honest empty state, never everybody). Right: the
 * selected mentee's meeting notes (GET /mentor/students/{id}/notes), a form to
 * add one (POST, NoteIn shape from routers/mentor.py — note_text required,
 * title optional so the student's log never shows an invented heading), and a
 * delete per note (DELETE, which the server allows only to the note's author).
 *
 * THE LINKED-ACTION VOCABULARY IS THE SERVER'S. The handoff's select lists
 * programme actions ("Attendance follow-up", "Skilling plan", ...); the
 * `mentor_action` enum the notes are stored in has NONE / FLAGGED / NUDGE_SENT /
 * ONE_ON_ONE_SCHEDULED, and widening a Postgres enum is a migration. The
 * options here are that enum, translated the same way the student's log
 * translates it, so a note reads identically on both screens.
 *
 * Markup reuses the global reep-v2 classes; the scss only lays out the split.
 */

import { Component, computed, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';

interface Mentee {
  student_id: string;
  name: string;
  usn: string | null;
  current_stage: string;
  current_semester: number;
  /** Whether "Full record" will open for this account. The server answers it
   *  with the rule the record page applies, so the link never leads to a
   *  refusal (a grant for another department opens none of these). */
  record_openable: boolean;
}

interface Note {
  id: string;
  note_text: string;
  linked_action: string;
  title: string | null;
  location: string | null;
  meeting_at: string;
  created_at: string;
}

/** Server vocabulary -> what renders. Same translation the student log uses. */
const ACTION_LABEL: Record<string, string> = {
  NONE: 'None',
  FLAGGED: 'Flagged for follow-up',
  NUDGE_SENT: 'Nudge sent',
  ONE_ON_ONE_SCHEDULED: '1:1 scheduled',
};

@Component({
  selector: 'app-mentee-log',
  standalone: true,
  // RouterLink: the "Full record" link below. Without it the `routerLink`
  // attribute is inert markup that renders and does nothing.
  imports: [DatePipe, FormsModule, RouterLink],
  templateUrl: './mentee-log.component.html',
  styleUrl: './mentee-log.component.scss',
})
export class MenteeLogComponent {
  private readonly apiBase = environment.apiBase;

  readonly mentees = signal<Mentee[] | null>(null);
  readonly error = signal<string | null>(null);

  readonly selectedId = signal<string | null>(null);
  readonly selected = computed(
    () => this.mentees()?.find((m) => m.student_id === this.selectedId()) ?? null,
  );

  readonly notes = signal<Note[] | null>(null);
  readonly notesError = signal<string | null>(null);

  readonly filter = signal('');
  readonly filtered = computed(() => {
    const list = this.mentees() ?? [];
    const q = this.filter().trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (m) => m.name.toLowerCase().includes(q) || (m.usn ?? '').toLowerCase().includes(q),
    );
  });

  // --- add-note form state ---
  noteText = '';
  noteTitle = '';
  noteAction = 'NONE';
  readonly saving = signal(false);
  /// The server's refusal, shown in full.
  readonly saveError = signal<string | null>(null);
  /// The two transient chips the handoff draws beside Save note.
  readonly savedFlash = signal(false);
  readonly emptyFlash = signal(false);
  readonly removingId = signal<string | null>(null);

  readonly actionOptions = Object.entries(ACTION_LABEL).map(([value, label]) => ({ value, label }));

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const res = await fetch(`${this.apiBase}/mentor/mentees`, { credentials: 'include' });
      if (!res.ok) {
        this.error.set('Could not load your mentees.');
        return;
      }
      const list = (await res.json()) as Mentee[];
      this.mentees.set(list);
      if (list.length && !this.selectedId()) this.select(list[0].student_id);
    } catch {
      this.error.set('Could not reach the server.');
    }
  }

  select(studentId: string): void {
    if (this.selectedId() === studentId) return;
    this.selectedId.set(studentId);
    this.notes.set(null);
    this.notesError.set(null);
    this.saveError.set(null);
    void this.loadNotes(studentId);
  }

  private async loadNotes(studentId: string): Promise<void> {
    try {
      const res = await fetch(`${this.apiBase}/mentor/students/${studentId}/notes`, {
        credentials: 'include',
      });
      if (this.selectedId() !== studentId) return; // stale response for a previous selection
      if (!res.ok) {
        this.notesError.set('Could not load the meeting log for this mentee.');
        return;
      }
      this.notes.set((await res.json()) as Note[]);
    } catch {
      if (this.selectedId() === studentId) this.notesError.set('Could not reach the server.');
    }
  }

  async addNote(): Promise<void> {
    const studentId = this.selectedId();
    const text = this.noteText.trim();
    if (!studentId) return;
    if (!text) {
      this.flash(this.emptyFlash);
      return;
    }
    this.saving.set(true);
    this.saveError.set(null);
    try {
      const res = await fetch(`${this.apiBase}/mentor/students/${studentId}/notes`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          note_text: text,
          linked_action: this.noteAction,
          title: this.noteTitle.trim() || null,
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        this.saveError.set(detail?.detail ?? 'Could not save the note.');
        return;
      }
      this.noteText = '';
      this.noteTitle = '';
      this.noteAction = 'NONE';
      this.flash(this.savedFlash);
      await this.loadNotes(studentId);
    } catch {
      this.saveError.set('Could not reach the server.');
    } finally {
      this.saving.set(false);
    }
  }

  /** Take a note back. The student may already have read it, hence the confirm. */
  async remove(n: Note): Promise<void> {
    const studentId = this.selectedId();
    if (!studentId) return;
    const ok = window.confirm(
      'Delete this note? It disappears from the student’s Mentor Meeting Log as well.',
    );
    if (!ok) return;
    this.removingId.set(n.id);
    this.notesError.set(null);
    try {
      const res = await fetch(`${this.apiBase}/mentor/students/${studentId}/notes/${n.id}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!res.ok && res.status !== 404) {
        const detail = await res.json().catch(() => null);
        this.notesError.set(detail?.detail ?? 'Could not delete the note.');
        return;
      }
      await this.loadNotes(studentId);
    } catch {
      this.notesError.set('Could not reach the server.');
    } finally {
      this.removingId.set(null);
    }
  }

  actionLabel(action: string): string {
    return ACTION_LABEL[action] ?? action;
  }

  /** "1:1 review · Cabin 3" heading, with the linked action (or "Meeting note")
   *  as the fallback — the same rule the student's log applies, so both screens
   *  agree on what sits above each note. */
  heading(n: Note): string {
    const parts = [n.title, n.location].filter(Boolean);
    if (parts.length) return parts.join(' · ');
    return n.linked_action !== 'NONE' ? this.actionLabel(n.linked_action) : 'Meeting note';
  }

  private flash(sig: ReturnType<typeof signal<boolean>>): void {
    sig.set(true);
    setTimeout(() => sig.set(false), 2500);
  }
}
