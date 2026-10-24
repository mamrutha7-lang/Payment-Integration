/**
 * Resume Builder — "Basic Details" section (stepper key `basic`).
 *
 * Editable demographics live in the shared profile map under `data.basic`
 * ({ middle_name, gender, dob, blood_group, marital_status, languages[],
 * dream_company, medical_history }). Identity fields the university owns — USN,
 * first/last name, course, specialization — are rendered LOCKED: the name comes
 * from the signed-in session (AuthService) and the USN is read best-effort from
 * GET /student/dashboard, mirroring student/profile.component.ts. Course and
 * specialization have no client-visible source yet, so they show as locked and
 * empty rather than inventing a value.
 *
 * The component owns a local model `m`, seeded from svc.section('basic', …) and
 * re-hydrated once svc.load() resolves; every edit writes the whole slice back
 * with svc.patch('basic', …). It never fetches or PUTs resume-profile itself.
 */

import { Component, ErrorHandler, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AuthService } from '../../../../core/auth.service';
import { environment } from '../../../../../environments/environment';
import {
  UNREADABLE_FILE_MESSAGE,
  UPLOAD_DROPPED_MESSAGE,
  readPickedFile,
} from '../../../../core/picked-file';
import { ResumeBuilderService } from '../resume-builder.service';
import { ResumeIdentityService } from '../resume-identity.service';

interface BasicData {
  middle_name: string;
  gender: string;
  dob: string;
  blood_group: string;
  marital_status: string;
  languages: string[];
  dream_company: string;
  medical_history: string;
  photo_upload_id: string;
}

@Component({
  selector: 'rb-basic',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './basic.component.html',
  styles: [
    `
      .dropzone--action {
        display: block;
        width: 100%;
        cursor: pointer;
        font-family: var(--font);
      }
      .dropzone--action:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
      .photo-preview {
        display: block;
        width: 120px;
        height: 120px;
        object-fit: cover;
        border-radius: 10px;
        border: 1px solid var(--hairline);
        margin-bottom: 8px;
      }
      .photo-err {
        display: block;
        color: var(--risk);
        margin-top: 6px;
      }
      /* the one-line "why we ask" under a field */
      .field-note {
        display: block;
        font-size: 11.5px;
        color: var(--muted);
        margin-top: 5px;
      }
      /* "why we ask" line above a sensitive field */
      .sensitive-note {
        display: flex;
        align-items: flex-start;
        gap: 5px;
        font-size: 11.5px;
        line-height: 1.4;
        color: var(--muted);
        margin-bottom: 6px;
      }
      .sensitive-note .icon {
        font-size: 14px;
        flex: 0 0 auto;
        margin-top: 1px;
      }
      /* small "why it matters" pill on placement-critical fields */
      .reqp {
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0;
        text-transform: none;
        color: var(--brand-purple);
        background: rgba(138, 90, 30, 0.1);
        padding: 2px 6px;
        border-radius: 5px;
        margin-left: 6px;
        white-space: nowrap;
      }
    `,
  ],
})
export class RbBasicComponent {
  private readonly svc = inject(ResumeBuilderService);
  private readonly auth = inject(AuthService);
  private readonly identity = inject(ResumeIdentityService);
  private readonly errorHandler = inject(ErrorHandler);

  /** Locked identity, synced from the student record (best-effort). */
  readonly usn = this.identity.usn;
  readonly firstName = computed(() => this.splitName().first);
  readonly lastName = computed(() => this.splitName().last);

  /**
   * Course and specialization, resolved through the cohort join.
   *
   * These two rendered as a dash behind a SYNCED badge for a student whose
   * record holds both — the endpoint has always returned them and this screen
   * never asked. A locked, empty, required-looking field tells a student the
   * university has lost their enrolment.
   */
  readonly courseName = this.identity.courseName;
  readonly specializationName = this.identity.specializationName;

  /** Draft text in the languages tag input (not persisted until Enter). */
  langDraft = '';

  /** Local, editable copy of the `basic` slice. */
  m: BasicData = this.normalize(this.svc.section('basic', {}));

  private hydrated = false;

  constructor() {
    // Re-seed from the server once the shell's load() has populated the map.
    effect(() => {
      if (this.svc.loaded() && !this.hydrated) {
        this.hydrated = true;
        this.m = untracked(() => this.normalize(this.svc.section('basic', {})));
        this.photoId.set(this.m.photo_upload_id);
      }
    });
    this.photoId.set(this.m.photo_upload_id);
    void this.identity.load();
  }

  /** Coerce an opaque stored slice into a fully-populated model. */
  private normalize(raw: Partial<BasicData>): BasicData {
    return {
      middle_name: raw.middle_name ?? '',
      gender: raw.gender ?? '',
      dob: raw.dob ?? '',
      blood_group: raw.blood_group ?? '',
      marital_status: raw.marital_status ?? '',
      languages: Array.isArray(raw.languages) ? [...raw.languages] : [],
      dream_company: raw.dream_company ?? '',
      medical_history: raw.medical_history ?? '',
      photo_upload_id: raw.photo_upload_id ?? '',
    };
  }

  // --- profile photo upload ---
  readonly uploadingPhoto = signal(false);
  readonly photoError = signal<string | null>(null);

  /** URL to preview the stored photo, or null when none is set. */
  readonly photoUrl = computed(() =>
    this.photoId() ? `${environment.apiBase}/student/uploads/${this.photoId()}/file` : null,
  );
  /** Signal mirror of m.photo_upload_id so photoUrl recomputes after upload. */
  private readonly photoId = signal<string>('');

  async onPhotoFile(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.uploadingPhoto.set(true);
    this.photoError.set(null);
    try {
      // Upload an in-memory copy (core/picked-file.ts): on Android a photo
      // held by Google Photos or another app can fail to be read at send time.
      const copy = await readPickedFile(file);
      if (!copy) {
        this.photoError.set(UNREADABLE_FILE_MESSAGE);
        this.errorHandler.handleError(
          new Error('Resume photo: the picked image could not be read on the device'),
        );
        return;
      }
      const form = new FormData();
      form.append('file', copy);
      form.append('kind', 'PROFILE_PHOTO');
      form.append('title', 'Profile photo');
      const res = await fetch(`${environment.apiBase}/student/uploads`, {
        method: 'POST',
        credentials: 'include',
        body: form,
      });
      if (!res.ok) {
        const d = await res.json().catch(() => null);
        // The server's sentence, or the STATUS: a refusal from the edge has no
        // JSON body, and a sentence about file types would blame the photo.
        this.photoError.set(
          typeof d?.detail === 'string'
            ? d.detail
            : `The photo could not be uploaded (${res.status}). Please try again in a minute.`,
        );
        return;
      }
      const previousId = this.m.photo_upload_id;
      const id = (await res.json()).id as string;
      this.m.photo_upload_id = id;
      this.photoId.set(id);
      this.push(); // the pointer, into the in-memory profile

      // "Replace photo" has to RETIRE the headshot it replaced, and until now
      // it only ever added another one. Each press left a second, third,
      // fourth PROFILE_PHOTO sitting PENDING_REVIEW on /student/uploads with
      // nothing on any screen saying which of them this resume points at; it
      // spent one of the student's 40 upload slots and up to 10 MB of their
      // 200 MB allowance every time, so a student who fiddles with their
      // headshot can meet a 409 on the marksheet they actually have to file;
      // and because document_store.save_bytes archives every write, each one
      // is a further permanent copy of the student's face in the Object-Locked
      // bucket, which nothing in the product can ever delete.
      //
      // AFTER THE POINTER IS ACTUALLY ON THE SERVER, and `push()` is not that.
      // `patch()` mutates the in-memory signal and arms a 1500 ms DEBOUNCED
      // autosave — so deleting here on the strength of `push()` destroys the
      // old headshot while the only record of its replacement is in this tab.
      // A student who closes it, refreshes, or loses the network inside that
      // window comes back to `photo_upload_id` still naming the file we just
      // unlinked: a broken image on their own resume, with the new upload
      // sitting unreferenced beside it. That is the same "student with no
      // photo" this ordering exists to prevent, reached from the other end,
      // and before this cleanup existed the identical race was harmless.
      //
      // So the save is AWAITED and its outcome is the condition. `save()`
      // reports through `error()` rather than throwing (it swallows both the
      // non-ok response and the network failure), so that signal is what has
      // to be read.
      await this.svc.save();
      if (this.svc.error() !== null) return;

      // BEST-EFFORT FROM HERE, by contract: the pointer is durable, so a
      // cleanup that 404s, 403s or cannot reach the server must not turn an
      // upload the student successfully made into an error message. The
      // response is not read and a rejected fetch is swallowed; the stale row
      // is then exactly what it was before this fix — one more card on
      // /student/uploads, removable by the student.
      if (previousId && previousId !== id) {
        await fetch(`${environment.apiBase}/student/uploads/${previousId}`, {
          method: 'DELETE',
          credentials: 'include',
        }).catch(() => undefined);
      }
    } catch {
      // The upload, or reading its answer, is what can throw here: save() and
      // the cleanup DELETE swallow their own failures. An unreadable answer to
      // an upload that worked would land here too; the API always answers
      // JSON, so that case is not told apart. Reported because no server saw it.
      this.photoError.set(UPLOAD_DROPPED_MESSAGE);
      this.errorHandler.handleError(
        new Error('Resume photo upload failed: no HTTP response (network error on the device)'),
      );
    } finally {
      this.uploadingPhoto.set(false);
      input.value = '';
    }
  }

  /** Flush the whole slice back to the shared map (deep-cloned, plain data). */
  push(): void {
    this.svc.patch('basic', JSON.parse(JSON.stringify(this.m)));
  }

  // --- languages tag input ---
  addLanguage(ev: Event): void {
    ev.preventDefault();
    const v = this.langDraft.trim();
    this.langDraft = '';
    if (!v || this.m.languages.includes(v)) return;
    this.m.languages = [...this.m.languages, v];
    this.push();
  }

  removeLanguage(i: number): void {
    this.m.languages = this.m.languages.filter((_, idx) => idx !== i);
    this.push();
  }

  // --- locked identity ---
  private splitName(): { first: string; last: string } {
    const name = (this.auth.session()?.name ?? '').trim();
    if (!name) return { first: '', last: '' };
    const parts = name.split(/\s+/);
    return { first: parts[0], last: parts.slice(1).join(' ') };
  }

}
