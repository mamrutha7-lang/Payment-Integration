/**
 * Alumni Profile — the first screen an alumnus lands on after sign-in.
 *
 * GET /alumni/profile answers `created: false` until they have saved a profile,
 * and that flag (never a falsy company string) is what switches this screen
 * between the FIRST-LOGIN SETUP FORM — current company + current resume, both
 * required to create — and the profile view with an Edit toggle. Saving POSTs
 * one multipart form; on update the resume is optional and omitting it keeps
 * the one on file.
 */

import { Component, ErrorHandler, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { environment } from '../../../../environments/environment';
import { UNREADABLE_FILE_MESSAGE, readPickedFile } from '../../../core/picked-file';

interface ResumeMeta {
  original_name: string;
  mime_type: string;
  size_bytes: number;
}

interface AlumniProfileOut {
  created: boolean;
  name: string;
  email: string;
  company: string | null;
  designation: string | null;
  joined_on: string | null;
  graduation_year: number | null;
  resume: ResumeMeta | null;
  updated_at: string | null;
}

const MAX_BYTES = 10 * 1024 * 1024; // matches the server cap

/** `fetch` threw on Save: no HTTP answer at all. It used to say "Could not
 *  reach the server.", which blamed the server for a phone's connection. */
const SAVE_DROPPED_MESSAGE =
  'Your profile was not saved: the connection dropped. Check your internet ' +
  '(Wi-Fi works best for files) and try again. Everything you filled in is still here.';

@Component({
  selector: 'app-alumni-profile',
  standalone: true,
  imports: [DatePipe, FormsModule],
  templateUrl: './alumni-profile.component.html',
})
export class AlumniProfileComponent {
  readonly apiBase = environment.apiBase;
  private readonly errorHandler = inject(ErrorHandler);

  /** null while the first GET is in flight. */
  readonly profile = signal<AlumniProfileOut | null>(null);
  readonly error = signal<string | null>(null);

  /** true when the setup/edit form is showing (always true before creation). */
  readonly editing = signal(false);

  // Form fields.
  company = '';
  designation = '';
  /** ISO yyyy-MM-dd, which is what <input type="date"> reads and writes. */
  joinedOn = '';
  graduationYear = '';
  /**
   * The resume as an in-memory copy, made when it is picked (core/picked-file.ts).
   * The form is filled in AFTER the resume and sent on Save, and on Android a
   * file held by Drive or another app can stop being readable in between. A
   * signal, because it is set after an await and this app has no zone.js to
   * notice a plain field changing.
   */
  readonly pickedFile = signal<File | null>(null);
  /** true while a picked resume is being read into memory; Save waits for it. */
  readonly resumeReading = signal(false);
  /** Bumped on every pick, so a slow read of an earlier pick cannot win. */
  private pickSeq = 0;

  readonly saving = signal(false);
  readonly saveError = signal<string | null>(null);
  readonly savedFlash = signal(false);

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const res = await fetch(`${this.apiBase}/alumni/profile`, { credentials: 'include' });
      if (!res.ok) {
        this.error.set('Could not load your profile.');
        return;
      }
      const prof = (await res.json()) as AlumniProfileOut;
      this.profile.set(prof);
      this.error.set(null);
      if (!prof.created) {
        this.editing.set(true);
      } else {
        this.company = prof.company ?? '';
        this.designation = prof.designation ?? '';
        this.joinedOn = prof.joined_on ?? '';
        this.graduationYear = prof.graduation_year ? String(prof.graduation_year) : '';
      }
    } catch {
      this.error.set('Could not reach the server.');
    }
  }

  startEdit(): void {
    this.saveError.set(null);
    this.clearPick();
    this.editing.set(true);
  }

  cancelEdit(): void {
    const prof = this.profile();
    if (!prof?.created) return; // nothing to fall back to before creation
    this.company = prof.company ?? '';
    this.designation = prof.designation ?? '';
    this.joinedOn = prof.joined_on ?? '';
    this.graduationYear = prof.graduation_year ? String(prof.graduation_year) : '';
    this.clearPick();
    this.saveError.set(null);
    this.editing.set(false);
  }

  async onPick(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    const seq = ++this.pickSeq;
    this.resumeReading.set(false);
    if (file && file.size > MAX_BYTES) {
      this.saveError.set('That file is over 10 MB. Please upload a smaller PDF.');
      input.value = '';
      return;
    }
    this.saveError.set(null);
    if (!file) {
      this.pickedFile.set(null);
      return;
    }
    this.resumeReading.set(true);
    const copy = await readPickedFile(file);
    if (seq !== this.pickSeq) return; // a later pick (or Cancel) owns the form now
    this.resumeReading.set(false);
    if (!copy) {
      this.pickedFile.set(null);
      this.saveError.set(UNREADABLE_FILE_MESSAGE);
      input.value = '';
      // A fixed sentence: nothing from the form or the file name.
      this.errorHandler.handleError(
        new Error('Alumni profile: the picked resume could not be read on the device'),
      );
      return;
    }
    this.pickedFile.set(copy);
  }

  private clearPick(): void {
    this.pickSeq++;
    this.resumeReading.set(false);
    this.pickedFile.set(null);
  }

  async save(): Promise<void> {
    if (this.saving() || this.resumeReading()) return;
    this.saveError.set(null);
    const company = this.company.trim();
    if (!company) {
      this.saveError.set('Tell us the company you currently work in.');
      return;
    }
    const isFirstSave = !this.profile()?.created;
    const resume = this.pickedFile();
    if (isFirstSave && !resume) {
      this.saveError.set('Upload your current resume to create your profile.');
      return;
    }
    const year = this.graduationYear.trim();
    if (year && !/^\d{4}$/.test(year)) {
      this.saveError.set('Graduation year should be a four-digit year.');
      return;
    }

    this.saving.set(true);
    try {
      const form = new FormData();
      form.append('company', company);
      form.append('designation', this.designation.trim());
      form.append('joined_on', this.joinedOn);
      // Omitted when blank — the server parses it as an int and an empty
      // string would 422 instead of meaning "not given".
      if (year) form.append('graduation_year', year);
      if (resume) form.append('resume', resume);
      const res = await fetch(`${this.apiBase}/alumni/profile`, {
        method: 'POST',
        credentials: 'include',
        body: form,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        // The server's sentence, or the STATUS: a refusal from the edge has no
        // JSON body, and a list `detail` would print as [object Object].
        this.saveError.set(
          typeof detail?.detail === 'string'
            ? detail.detail
            : `Your profile could not be saved (${res.status}). Please try again in a minute.`,
        );
        return;
      }
      this.profile.set((await res.json()) as AlumniProfileOut);
      this.clearPick();
      this.editing.set(false);
      this.savedFlash.set(true);
      setTimeout(() => this.savedFlash.set(false), 2500);
    } catch {
      this.saveError.set(SAVE_DROPPED_MESSAGE);
      // Reported because no server saw it. A fixed sentence, nothing from the form.
      this.errorHandler.handleError(
        new Error('Alumni profile save failed: no HTTP response (network error on the device)'),
      );
    } finally {
      this.saving.set(false);
    }
  }

  resumeUrl(): string {
    return `${this.apiBase}/alumni/profile/resume`;
  }

  formatSize(bytes: number): string {
    const b = bytes || 0;
    if (b < 1024) return `${b} B`;
    const kb = b / 1024;
    if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
    const mb = kb / 1024;
    return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
  }
}
