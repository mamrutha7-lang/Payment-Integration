/**
 * Resume Builder · Certification / Assessments section (rb-certifications).
 *
 * Hybrid section: REEP programme certifications are provider-verified and read
 * from GET /student/certifications (CertProgressOut[]) — they render as locked
 * .entry cards carrying the "From REEP record" evidence tag and cannot be
 * edited. Certifications earned OUTSIDE the programme are user-managed and live
 * in the shared ResumeBuilderService map under the `external_certs` key: "Add
 * certification" appends one and patches the slice, so the shell's Save flushes
 * them with the rest of the profile.
 *
 * Matches the `data-p="certifications"` panel in docs/design-v2/resume-builder.html,
 * reusing the global reep-v2 classes (.notice.evi/.card/.entry/.tag/.chip).
 */

import { Component, computed, effect, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { environment } from '../../../../../environments/environment';
import { ResumeBuilderService } from '../resume-builder.service';

/** One row of GET /student/certifications (CertProgressOut, snake_case). */
interface CertRow {
  code: string;
  name: string;
  provider: string;
  status: string;
  progress_pct: number;
  hours_logged: number;
  required_hours: number;
  due_date: string;
  self_reported: boolean;
}

/** A manually-added, outside-the-programme certification (this section owns it). */
interface ExternalCert {
  name: string;
  provider: string;
  year: string;
  link: string;
}

const STATUS_LABEL: Record<string, string> = {
  NOT_STARTED: 'Not started',
  IN_PROGRESS: 'In progress',
  COMPLETED: 'Completed',
  OVERDUE: 'Overdue',
};

@Component({
  selector: 'rb-certifications',
  standalone: true,
  imports: [DatePipe, FormsModule],
  template: `
    <div class="notice evi">
      <span class="icon">bolt</span>
      <div>
        <b>REEP programme certifications appear here automatically.</b> They are provider-verified,
        so they carry an evidence tag and cannot be edited — only certifications earned outside the
        programme are added manually.
      </div>
    </div>

    <div class="card">
      <h3>
        Certifications &amp; assessments
        <button
          type="button"
          class="btn primary right"
          style="padding:7px 13px;"
          (click)="toggleForm()"
        >
          <span class="icon">add</span> Add certification
        </button>
      </h3>

      <!-- Add-external inline form -->
      @if (formOpen()) {
        <div class="entry" style="border-style:dashed;">
          <div class="grid2">
            <div class="field">
              <label>Certification name <span class="req">*</span></label>
              <input
                class="ctrl"
                [(ngModel)]="form.name"
                name="extName"
                placeholder="e.g. Google Data Analytics"
              />
            </div>
            <div class="field">
              <label>Provider / issuer</label>
              <input
                class="ctrl"
                [(ngModel)]="form.provider"
                name="extProvider"
                placeholder="e.g. Google · Coursera"
              />
            </div>
            <div class="field">
              <label>Year</label>
              <input class="ctrl" [(ngModel)]="form.year" name="extYear" placeholder="e.g. 2026" />
            </div>
            <div class="field">
              <label>Credential link</label>
              <input class="ctrl" [(ngModel)]="form.link" name="extLink" placeholder="https://…" />
            </div>
          </div>
          @if (formError()) {
            <div class="desc" style="color:var(--risk); margin:0 0 10px;">{{ formError() }}</div>
          }
          <div style="display:flex; gap:8px;">
            <button
              type="button"
              class="btn primary"
              style="padding:7px 13px;"
              (click)="addExternal()"
            >
              <span class="icon">check</span> Add
            </button>
            <button type="button" class="btn" style="padding:7px 13px;" (click)="closeForm()">
              Cancel
            </button>
          </div>
        </div>
      }

      @if (error()) {
        <div class="empty" style="padding:26px;">
          <span class="icon">error</span>
          <p>{{ error() }}</p>
        </div>
      } @else if (rows() === null) {
        <div class="empty" style="padding:26px;">
          <span class="icon">hourglass_top</span>
          <p>Loading your certifications…</p>
        </div>
      } @else {
        <!-- REEP programme certs (locked, provider-verified) -->
        @for (c of rows()!; track c.code) {
          <div class="entry">
            @if (c.self_reported) {
              <h4>
                {{ c.name }}
                @if (c.status === 'COMPLETED') {
                  <span class="chip good" style="margin-left:8px;"
                    ><span class="icon">check_circle</span>Self-reported · complete</span
                  >
                } @else {
                  <span class="chip warn" style="margin-left:8px;"
                    ><span class="icon">hourglass_top</span>Self-reported · awaiting
                    verification</span
                  >
                }
              </h4>
              <div class="org">{{ c.provider }}</div>
              <div class="meta">
                {{ statusLabel(c.status) }} — {{ c.progress_pct }}% · {{ c.hours_logged }} /
                {{ c.required_hours }} hrs · due {{ c.due_date | date: 'd MMM y' }}
              </div>
            } @else {
              <h4>
                {{ c.name }}
                <span class="tag evi"
                  ><span class="icon" style="font-size:12px">bolt</span>From REEP record</span
                >
                <span class="tag lock">Locked</span>
              </h4>
              <div class="org">{{ c.provider }}</div>
              <div class="meta">
                @if (c.status === 'COMPLETED') {
                  Completed · {{ c.hours_logged }} / {{ c.required_hours }} hrs · provider-verified
                } @else {
                  {{ statusLabel(c.status) }} — {{ c.progress_pct }}% · {{ c.hours_logged }} /
                  {{ c.required_hours }} hrs · provider-verified
                }
              </div>
            }
          </div>
        }

        <!-- Manually-added external certs (ResumeBuilderService.external_certs) -->
        @for (c of externals(); track $index) {
          <div class="entry">
            <div class="tools">
              <button type="button" (click)="removeExternal($index)">
                <span class="icon" style="font-size:17px">delete</span>
              </button>
            </div>
            <h4>{{ c.name }} <span class="tag">Self-added</span></h4>
            @if (c.provider) {
              <div class="org">{{ c.provider }}</div>
            }
            <div class="meta">
              {{ c.year ? c.year : 'Year not set' }}
              @if (c.link) {
                ·
                <a [href]="c.link" target="_blank" rel="noopener" style="color:var(--purple-mid);"
                  >credential</a
                >
              }
            </div>
          </div>
        }

        @if (rows()!.length === 0 && externals().length === 0) {
          <div class="empty">
            <span class="icon">workspace_premium</span>
            <p>
              No certifications yet. Programme certifications sync automatically; add outside ones
              above.
            </p>
          </div>
        }
      }
    </div>
  `,
})
export class RbCertificationsComponent {
  private readonly svc = inject(ResumeBuilderService);

  /** null while loading; an array (possibly empty) once the fetch resolves. */
  readonly rows = signal<CertRow[] | null>(null);
  readonly error = signal<string | null>(null);

  /** The user-managed slice, read reactively from the shared profile map. */
  readonly externals = computed<ExternalCert[]>(
    () => this.svc.section('external_certs', []) as ExternalCert[],
  );

  readonly formOpen = signal(false);
  readonly formError = signal<string | null>(null);
  form: ExternalCert = this.blank();

  constructor() {
    void this.load();
  }

  statusLabel(status: string): string {
    return STATUS_LABEL[status] ?? status;
  }

  toggleForm(): void {
    this.formOpen.update((v) => !v);
    if (this.formOpen()) {
      this.form = this.blank();
      this.formError.set(null);
    }
  }

  closeForm(): void {
    this.formOpen.set(false);
  }

  addExternal(): void {
    const name = this.form.name.trim();
    if (!name) {
      this.formError.set('A certification name is required.');
      return;
    }
    const next: ExternalCert = {
      name,
      provider: this.form.provider.trim(),
      year: this.form.year.trim(),
      link: this.form.link.trim(),
    };
    this.svc.patch('external_certs', [...this.externals(), next]);
    this.form = this.blank();
    this.formOpen.set(false);
  }

  removeExternal(index: number): void {
    this.svc.patch(
      'external_certs',
      this.externals().filter((_, i) => i !== index),
    );
  }

  private blank(): ExternalCert {
    return { name: '', provider: '', year: '', link: '' };
  }

  private async load(): Promise<void> {
    this.error.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/certifications`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.error.set('Could not load your certifications.');
        return;
      }
      this.rows.set((await res.json()) as CertRow[]);
    } catch {
      this.error.set('Could not reach the server.');
    }
  }

  /**
   * The stepper dot for this section.
   *
   * HALF of it lives in the builder map (`external_certs`) and half comes from
   * `GET /student/certifications`, and the shell only reads the map — under the
   * key `certifications`, which nothing ever writes. So the dot said "Not
   * started" even with a programme certification synced and a certificate the
   * student had just added by hand. Reported here, where both halves are known,
   * and re-reported whenever either changes.
   */
  private readonly _reportState = effect(() => {
    const programme = this.rows();
    if (programme === null) return; // still loading; say nothing yet
    const total = programme.length + this.externals().length;
    this.svc.reportMirrorState('certifications', total ? 'done' : 'empty');
  });
}
