/**
 * Resume Builder · Attachments section (rb-attachments).
 *
 * A read-only ledger of every document the student has submitted, sourced from
 * GET /student/uploads (UploadRowOut[]). Files are uploaded from the section
 * they belong to, so this panel never uploads — it only lists Document / Status
 * / Uploaded with a status chip, and shows the mockup's "Other documents"
 * dropzone as an inert placeholder (binary upload is not built on the server).
 *
 * It does NOT touch the shared ResumeBuilderService map. Matches the
 * `data-p="attachments"` panel in docs/design-v2/resume-builder.html, reusing
 * the global reep-v2 classes (.notice/.card/.tbl/.chip/.empty/.dropzone).
 */

import { Component, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';

import { environment } from '../../../../../environments/environment';
import { failedReadMessage } from '../../../../core/feature-refusal';
import { ResumeBuilderService } from '../resume-builder.service';

/** One row of GET /student/uploads (UploadRowOut, snake_case verbatim). */
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

interface StatusMeta {
  label: string;
  tone: 'good' | 'warn' | 'risk';
  icon: string;
}

const STATUS: Record<string, StatusMeta> = {
  PENDING_REVIEW: { label: 'Pending mentor check', tone: 'warn', icon: 'hourglass_top' },
  VERIFIED: { label: 'Verified', tone: 'good', icon: 'check_circle' },
  REJECTED: { label: 'Rejected', tone: 'risk', icon: 'error' },
};

const KIND_LABEL: Record<string, string> = {
  CERTIFICATE_PROOF: 'Certification / Assessments',
  RESUME: 'Resume',
  PROFILE_PHOTO: 'Basic Details',
  DOCUMENT: 'Other documents',
};

@Component({
  selector: 'rb-attachments',
  standalone: true,
  imports: [DatePipe],
  template: `
    <div class="notice info">
      <span class="icon">info</span>
      <div>
        This screen is a <b>read-only ledger</b>. Each file is uploaded from the section it belongs
        to, so a document can never be attached to the wrong record.
      </div>
    </div>

    <div class="card">
      <h3>
        Document ledger
        <span class="chip neutral"
          ><span class="icon">link</span>managed in their own sections</span
        >
      </h3>

      @if (error()) {
        <div class="empty" style="padding:26px;">
          <span class="icon">error</span>
          <p>{{ error() }}</p>
        </div>
      } @else if (rows() === null) {
        <div class="empty" style="padding:26px;">
          <span class="icon">hourglass_top</span>
          <p>Loading your documents…</p>
        </div>
      } @else if (rows()!.length === 0) {
        <div class="empty">
          <span class="icon">folder_open</span>
          <p>No documents on your record yet. Upload them from the section each one belongs to.</p>
        </div>
      } @else {
        <table class="tbl">
          <tr>
            <th>Document</th>
            <th>Source section</th>
            <th>Status</th>
            <th>Uploaded</th>
          </tr>
          @for (u of rows()!; track u.id) {
            <tr>
              <td>
                <div>{{ u.title }}</div>
                <div style="color:var(--faint); font-size:11px;">{{ u.original_name }}</div>
              </td>
              <td>{{ kindLabel(u.kind) }}</td>
              <td>
                @let meta = status(u.status);
                <span class="chip {{ meta.tone }}"
                  ><span class="icon">{{ meta.icon }}</span
                  >{{ meta.label }}</span
                >
              </td>
              <td>{{ u.uploaded_at | date: 'd MMM y' }}</td>
            </tr>
          }
        </table>
      }
    </div>

    <div class="card">
      <h3>Other documents</h3>
      <div class="desc">
        Anything that has no home elsewhere — organizational study report, ID proof, consent forms.
        Uploads are added from the owning section.
      </div>
      <div class="dropzone">
        <span class="icon">cloud_upload</span>Documents are uploaded from the section they belong to
        <small>PDF, JPG, PNG · up to 10MB</small>
      </div>
    </div>
  `,
})
export class RbAttachmentsComponent {
  private readonly svc = inject(ResumeBuilderService);

  /** null while loading; an array (possibly empty) once the fetch resolves. */
  readonly rows = signal<UploadRow[] | null>(null);
  readonly error = signal<string | null>(null);

  readonly verifiedCount = computed(
    () => this.rows()?.filter((u) => u.status === 'VERIFIED').length ?? 0,
  );

  constructor() {
    void this.load();
  }

  status(status: string): StatusMeta {
    return STATUS[status] ?? { label: status, tone: 'warn', icon: 'help' };
  }

  kindLabel(kind: string): string {
    return KIND_LABEL[kind] ?? kind;
  }

  private async load(): Promise<void> {
    this.error.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/uploads`, { credentials: 'include' });
      if (!res.ok) {
        this.error.set(await failedReadMessage(res, 'Could not load your documents.'));
        return;
      }
      const rows = (await res.json()) as UploadRow[];
      this.rows.set(rows);
      // A read-only mirror writes nothing into the builder map, so it reports
      // its own stepper state; deriving it from `data` left the dot on "Not
      // started" beside a ledger of four documents.
      this.svc.reportMirrorState('attachments', rows.length ? 'done' : 'empty');
    } catch {
      this.error.set('Could not reach the server.');
    }
  }
}
