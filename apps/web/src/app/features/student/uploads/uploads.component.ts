/**
 * Student uploads — the Angular port of the `data-p="uploads"` mockup panel,
 * reworked for a confident, explicit flow.
 *
 * The screen now walks the student through three visible steps
 *   1 Choose document type → 2 Upload file → 3 In review
 * and keeps them honest with a missing-documents checklist for the three
 * placement-critical kinds. Uploading accepts both click-to-pick and
 * drag-and-drop, shows an "Uploading…" state, previews the stored file
 * (image thumbnail via GET /uploads/{id}/file, doc icon for PDF) and surfaces
 * the server's magic-byte rejection message verbatim.
 *
 * Existing documents render as cards showing the review status prominently
 * (text + colour), the reviewer's comment when present, and Replace / Remove
 * actions. Remove confirms then calls DELETE /student/uploads/{id} and
 * refreshes. Data shaping stays here so the template renders ready cells; the
 * snake_case fields are the ones FastAPI's UploadRowOut returns verbatim.
 */

import { Component, ErrorHandler, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';

import { environment } from '../../../../environments/environment';
import { failedReadMessage } from '../../../core/feature-refusal';
import {
  UNREADABLE_FILE_MESSAGE,
  UPLOAD_DROPPED_MESSAGE,
  readPickedFile,
} from '../../../core/picked-file';

interface UploadRow {
  id: string;
  kind: string;
  cert_code: string | null;
  title: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  status: string;
  review_note: string | null;
  reviewed_at: string | null;
  uploaded_at: string;
}

const KIND_LABEL: Record<string, string> = {
  CERTIFICATE_PROOF: 'Certificate proof',
  RESUME: 'Resume / CV',
  PROFILE_PHOTO: 'Profile photo',
  DOCUMENT: 'Document',
};

interface StatusMeta {
  label: string;
  tone: 'good' | 'warn' | 'risk';
  icon: string;
}

const STATUS: Record<string, StatusMeta> = {
  PENDING_REVIEW: { label: 'Pending review', tone: 'warn', icon: 'hourglass_top' },
  VERIFIED: { label: 'Verified', tone: 'good', icon: 'verified' },
  REJECTED: { label: 'Rejected', tone: 'risk', icon: 'error' },
};

/** The placement-critical documents the checklist tracks. */
const REQUIRED_KINDS: { kind: string; label: string; hint: string }[] = [
  { kind: 'PROFILE_PHOTO', label: 'Profile photo', hint: 'A clear headshot for your profile.' },
  { kind: 'RESUME', label: 'Resume / CV', hint: 'Your latest CV as a PDF.' },
  {
    kind: 'CERTIFICATE_PROOF',
    label: 'Certificate proof',
    hint: 'Proof for a certification you have claimed.',
  },
];

const MAX_BYTES = 10 * 1024 * 1024; // 10 MB — matches the server cap.

@Component({
  selector: 'app-student-uploads',
  standalone: true,
  imports: [DatePipe],
  templateUrl: './uploads.component.html',
  styleUrl: './uploads.component.scss',
})
export class UploadsComponent {
  readonly apiBase = environment.apiBase;
  private readonly errorHandler = inject(ErrorHandler);

  /** null while loading; an array (possibly empty) once the fetch resolves. */
  readonly rows = signal<UploadRow[] | null>(null);
  readonly error = signal<string | null>(null);

  /** The kind the next picked file is uploaded as, and the upload's state. */
  readonly kind = signal<string>('CERTIFICATE_PROOF');
  readonly uploading = signal(false);
  readonly uploadError = signal<string | null>(null);
  /** The row that was just created — drives the success preview and step 3. */
  readonly justUploaded = signal<UploadRow | null>(null);
  /** true while a file is dragged over the dropzone. */
  readonly dragOver = signal(false);

  /**
   * The row the pending Replace points at.
   *
   * It is READ AND CLEARED by `onReplacePick`, the change handler of the
   * replace-only picker, and by nothing else — which is the whole guard. A
   * cancelled file dialog fires no change event at all, so this used to stay
   * armed after "Replace" was pressed and abandoned, and the NEXT ordinary
   * upload (dropzone, drag-and-drop, any file) deleted the abandoned row: a
   * document the student never touched again, gone with no prompt and no undo,
   * because DELETE /student/uploads/{id} unlinks the bytes and drops the row.
   * Two pickers is what makes that unreachable rather than merely unlikely —
   * the input whose selection can delete something is the input only `replace`
   * ever opens, so a stale target can only ever be consumed by a Replace press
   * that has just overwritten it.
   */
  private readonly replaceTargetId = signal<string | null>(null);
  /** The row currently being removed, so its card can disable + show progress. */
  readonly removingId = signal<string | null>(null);
  readonly actionError = signal<string | null>(null);

  readonly kindOptions = [
    { value: 'CERTIFICATE_PROOF', label: 'Certificate proof' },
    { value: 'RESUME', label: 'Resume / CV' },
    { value: 'PROFILE_PHOTO', label: 'Profile photo' },
    { value: 'DOCUMENT', label: 'Other document' },
  ];

  /** Which of the three visible steps the student is on right now. */
  readonly currentStep = computed(() => {
    if (this.uploading()) return 2;
    if (this.justUploaded()) return 3;
    return 1;
  });

  /** Present/missing state for each placement-critical kind. */
  readonly checklist = computed(() => {
    const list = this.rows() ?? [];
    return REQUIRED_KINDS.map((r) => ({
      ...r,
      present: list.some((u) => u.kind === r.kind),
    }));
  });

  readonly missingCount = computed(() => this.checklist().filter((c) => !c.present).length);

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const res = await fetch(`${this.apiBase}/student/uploads`, { credentials: 'include' });
      if (!res.ok) {
        this.error.set(await failedReadMessage(res, 'Could not load your uploads.'));
        return;
      }
      this.rows.set((await res.json()) as UploadRow[]);
      this.error.set(null);
    } catch {
      this.error.set('Could not reach the server.');
    }
  }

  setKind(kind: string): void {
    this.kind.set(kind);
    // Changing the type is a step-1 action — step back from the review preview.
    this.justUploaded.set(null);
    this.uploadError.set(null);
    this.replaceTargetId.set(null);
  }

  // --- drag and drop -------------------------------------------------------

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    if (!this.uploading()) this.dragOver.set(true);
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) void this.uploadFile(file);
  }

  onPick(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) void this.uploadFile(file);
    input.value = ''; // allow re-picking the same file
  }

  /**
   * The replace picker's own change event — the ONE file selection allowed to
   * delete an existing row, and it takes the target away with it before the
   * upload starts. Read-and-clear, not read-then-clear-on-success: if the
   * upload fails the student is back on step 1 with nothing armed, which is the
   * state they can see.
   */
  onReplacePick(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    const replacing = this.replaceTargetId();
    this.replaceTargetId.set(null);
    if (file) void this.uploadFile(file, replacing);
    input.value = ''; // allow re-picking the same file
  }

  /** The dialog was dismissed, so the Replace never happened — disarm it now
   *  rather than leaving a delete waiting on a document the student has
   *  stopped thinking about. (The two-picker split above already makes a stale
   *  target unusable; this is the same fact said where it happens, and it is
   *  what keeps the screen's state equal to what the student did.) */
  cancelReplace(): void {
    this.replaceTargetId.set(null);
  }

  /// POST the file as multipart; the server sniffs the type and stores it.
  /// `replacing` is the row this upload supersedes, passed in from the one
  /// selection event that armed it — never read back off component state,
  /// which outlives the gesture that set it.
  private async uploadFile(file: File, replacing: string | null = null): Promise<void> {
    this.uploadError.set(null);
    this.actionError.set(null);
    this.justUploaded.set(null);

    if (file.size > MAX_BYTES) {
      this.uploadError.set('That file is over 10 MB. Please upload a smaller PDF, PNG or JPEG.');
      return;
    }

    this.uploading.set(true);
    try {
      // Read the bytes first and upload the in-memory copy
      // (core/picked-file.ts): on Android a file held by Drive, Photos or
      // another app can fail to be read at send time, which reached this
      // screen as a thrown fetch. A file unreadable even now is refused here,
      // about the file, before a Replace deletes anything.
      const copy = await readPickedFile(file);
      if (!copy) {
        this.uploadError.set(UNREADABLE_FILE_MESSAGE);
        this.errorHandler.handleError(
          new Error('Document upload: the picked file could not be read on the device'),
        );
        return;
      }
      const form = new FormData();
      form.append('file', copy);
      form.append('kind', this.kind());
      form.append('title', file.name);
      const res = await fetch(`${this.apiBase}/student/uploads`, {
        method: 'POST',
        credentials: 'include',
        body: form,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        // The server's sentence, or the STATUS: a refusal from the edge has no
        // JSON body, and a sentence about file types would blame the file.
        this.uploadError.set(
          typeof detail?.detail === 'string'
            ? detail.detail
            : `The file could not be uploaded (${res.status}). Please try again in a minute.`,
        );
        return;
      }
      const created = (await res.json()) as UploadRow;
      // A true replace: drop the old document now the new one is safely stored.
      // In this order, always — a failure here costs the student a duplicate
      // they can remove, where the other order costs them the document.
      if (replacing) await this.deleteUpload(replacing).catch(() => undefined);
      this.justUploaded.set(created); // shows step 3 + the preview
      await this.load(); // reflect the new PENDING_REVIEW row
    } catch {
      this.uploadError.set(UPLOAD_DROPPED_MESSAGE);
      // Reported because no server saw it. A fixed sentence, nothing from
      // the student or the file.
      this.errorHandler.handleError(
        new Error('Document upload failed: no HTTP response (network error on the device)'),
      );
    } finally {
      this.uploading.set(false);
    }
  }

  // --- per-row actions -----------------------------------------------------

  /** Replace: re-use the row's kind and open the REPLACE picker; on success the
   * old row is deleted so the record shows a single, current document. The
   * picker handed in is `#replacePicker` and never the dropzone's own — see
   * `replaceTargetId`. Setting the type selector to the row's kind stays
   * truthful whatever happens next: it is on screen, it is what the dropzone's
   * hint reads back, and it is what an upload started from anywhere else on
   * this panel will be filed as. */
  replace(row: UploadRow, picker: HTMLInputElement): void {
    this.kind.set(row.kind);
    this.replaceTargetId.set(row.id);
    this.uploadError.set(null);
    this.actionError.set(null);
    picker.click();
  }

  async remove(row: UploadRow): Promise<void> {
    const ok = window.confirm(
      `Remove "${row.title}"? This permanently deletes the file from your record.`,
    );
    if (!ok) return;
    this.actionError.set(null);
    this.removingId.set(row.id);
    try {
      const res = await this.deleteUpload(row.id);
      if (!res.ok && res.status !== 404) {
        this.actionError.set('Could not remove that document. Please try again.');
        return;
      }
      if (this.justUploaded()?.id === row.id) this.justUploaded.set(null);
      await this.load();
    } catch {
      this.actionError.set('Could not reach the server.');
    } finally {
      this.removingId.set(null);
    }
  }

  private deleteUpload(id: string): Promise<Response> {
    return fetch(`${this.apiBase}/student/uploads/${id}`, {
      method: 'DELETE',
      credentials: 'include',
    });
  }

  // --- view helpers --------------------------------------------------------

  fileUrl(id: string): string {
    return `${this.apiBase}/student/uploads/${id}/file`;
  }

  isImage(mime: string): boolean {
    return typeof mime === 'string' && mime.startsWith('image/');
  }

  kindLabel(kind: string): string {
    return KIND_LABEL[kind] ?? this.humanize(kind);
  }

  statusLabel(status: string): string {
    return STATUS[status]?.label ?? this.humanize(status);
  }

  statusTone(status: string): string {
    return STATUS[status]?.tone ?? 'neutral';
  }

  statusIcon(status: string): string {
    return STATUS[status]?.icon ?? 'help';
  }

  /// Bytes -> "820 B" / "12.4 KB" / "1.4 MB", one decimal only under 10.
  formatSize(bytes: number): string {
    const b = bytes || 0;
    if (b < 1024) return `${b} B`;
    const kb = b / 1024;
    if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
    const mb = kb / 1024;
    return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
  }

  private humanize(value: string): string {
    if (!value) return value;
    return value.charAt(0) + value.slice(1).toLowerCase().replace(/_/g, ' ');
  }
}
