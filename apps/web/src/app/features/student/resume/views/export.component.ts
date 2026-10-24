/**
 * Export & share — step 4.
 *
 * The saved versions (GET /student/resume), the three things sharing needs —
 * which version, whether to attach a proof appendix, and an explicit
 * confirmation of what leaves the student's hands — and the export itself.
 *
 * THE CONFIRMATION IS NOT A FORMALITY. Exporting hands a document carrying the
 * student's contact details and academic record to an employer, and the proof
 * appendix adds their certificates to it. That is the one irreversible action on
 * this screen, so nothing exports until the student confirms the resume shares
 * only what they intend recruiters to see. The default is appendix OFF — the
 * handoff is explicit that the exported resume stays clean unless the student
 * chooses otherwise.
 *
 * "Export & share" opens the server-rendered PDF of the chosen version
 * (GET /student/resume/{id}/pdf — a local render, so rule 1's gate does not
 * apply). Posting a resume against an application has no endpoint yet, so "Use
 * for application" selects the version for export and says exactly that.
 *
 * Each version can also be RENAMED, FINALISED (locked: the copy the student has
 * decided to send), REGENERATED in the standard template from what the builder
 * holds now, and DELETED (PATCH / POST …/regenerate / DELETE
 * /student/resume/{id}). The document itself is never edited as text: its
 * "Verified Skills" heading is a mentor's claim, so the way to change what a
 * version says is the builder and Regenerate.
 */

import { DatePipe } from '@angular/common';
import { Component, computed, inject, output, signal } from '@angular/core';

import { environment } from '../../../../../environments/environment';
import { ResumeEvidenceService } from '../resume-evidence.service';
import { ResumeGoalService } from '../resume-goal.service';

type ResumeStep = 'build' | 'tailor' | 'preview' | 'export';

/** One row exactly as GET /student/resume returns it (ResumeOut, snake_case). */
interface ResumeRow {
  id: string;
  version: number;
  title: string;
  status: string;
  generated_by: string;
  model: string | null;
  created_at: string | null;
  updated_at?: string | null;
  target_role?: string | null;
  /** The layout it was composed in; null for a version older than the template. */
  template?: string | null;
}

/** FastAPI's `detail`, a string or a list of field errors, as one sentence. */
function detailOf(body: unknown, status: number): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d.length) {
    const m = (d[0] as { msg?: string }).msg;
    if (m) return m;
  }
  return `The request was refused (${status}).`;
}

@Component({
  selector: 'rb-export',
  standalone: true,
  imports: [DatePipe],
  templateUrl: './export.component.html',
  styleUrl: './export.component.scss',
})
export class RbExportComponent {
  readonly goalSvc = inject(ResumeGoalService);
  readonly ev = inject(ResumeEvidenceService);

  readonly navigate = output<ResumeStep>();

  /** The versions, or null while the first load is in flight. */
  readonly versions = signal<ResumeRow[] | null>(null);
  readonly versionsError = signal<string | null>(null);

  /** The version chosen for export — the newest until the student picks one. */
  readonly selectedId = signal<string | null>(null);
  readonly selected = computed<ResumeRow | null>(() => {
    const id = this.selectedId();
    return (this.versions() ?? []).find((v) => v.id === id) ?? null;
  });

  /** Off by default: the exported document is clean unless asked otherwise. */
  readonly proofAppendix = signal(false);
  readonly consented = signal(false);
  readonly exported = signal(false);

  /** The version being renamed, and the name typed so far. */
  readonly renamingId = signal<string | null>(null);
  readonly renameText = signal('');
  /** The version whose delete is waiting for a second press. */
  readonly confirmDeleteId = signal<string | null>(null);
  /** The version a request is in flight for, so its buttons disable. */
  readonly busyId = signal<string | null>(null);
  /** The last update/delete refusal, shown above the list. */
  readonly actionError = signal<string | null>(null);

  readonly job = computed(() => this.goalSvc.selectedJob());
  readonly canExport = computed(() => !!this.selected() && this.consented());

  /** How many pieces of proof the appendix would carry. */
  readonly proofCount = computed(
    () => (this.ev.rows() ?? []).filter((r) => r.included && r.proofUploadId).length,
  );

  constructor() {
    void this.goalSvc.load();
    void this.ev.load();
    void this.loadVersions();
  }

  private async loadVersions(): Promise<void> {
    this.versionsError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/resume`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.versionsError.set('Could not load your resumes.');
        return;
      }
      const list = (await res.json()) as ResumeRow[];
      this.versions.set(list);
      // Newest-first from the API, so the first row is the working default.
      if (!this.selectedId()) this.selectedId.set(list[0]?.id ?? null);
    } catch {
      this.versionsError.set('Could not reach the server.');
    }
  }

  isFinal(v: ResumeRow): boolean {
    return v.status === 'FINALISED';
  }

  startRename(v: ResumeRow): void {
    this.renamingId.set(v.id);
    this.renameText.set(v.title);
    this.confirmDeleteId.set(null);
  }

  cancelRename(): void {
    this.renamingId.set(null);
  }

  /** One request for one version; the row is replaced with what came back. */
  private async act(
    v: ResumeRow,
    path: string,
    init: RequestInit,
  ): Promise<ResumeRow | null | undefined> {
    this.busyId.set(v.id);
    this.actionError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/resume/${v.id}${path}`, {
        credentials: 'include',
        ...init,
      });
      if (!res.ok) {
        this.actionError.set(detailOf(await res.json().catch(() => null), res.status));
        return undefined;
      }
      if (res.status === 204) return null;
      const row = (await res.json()) as ResumeRow;
      this.versions.update((list) => (list ?? []).map((r) => (r.id === row.id ? row : r)));
      return row;
    } catch {
      this.actionError.set('Could not reach the server.');
      return undefined;
    } finally {
      this.busyId.set(null);
    }
  }

  private patch(v: ResumeRow, body: Record<string, unknown>): Promise<ResumeRow | null | undefined> {
    return this.act(v, '', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  async saveRename(v: ResumeRow): Promise<void> {
    const title = this.renameText().trim();
    if (!title) {
      this.actionError.set('A resume needs a name.');
      return;
    }
    if ((await this.patch(v, { title })) !== undefined) this.renamingId.set(null);
  }

  /** Finalise locks the version; un-finalising makes it a working copy again. */
  async toggleFinal(v: ResumeRow): Promise<void> {
    await this.patch(v, { status: this.isFinal(v) ? 'GENERATED' : 'FINALISED' });
  }

  /** Compose this version again, in the standard template, from the builder now. */
  async regenerate(v: ResumeRow): Promise<void> {
    if ((await this.act(v, '/regenerate', { method: 'POST' })) !== undefined) {
      this.exported.set(false);
    }
  }

  /** Delete asks twice: the first press arms it, the second deletes. */
  async remove(v: ResumeRow): Promise<void> {
    if (this.confirmDeleteId() !== v.id) {
      this.confirmDeleteId.set(v.id);
      this.renamingId.set(null);
      return;
    }
    this.confirmDeleteId.set(null);
    if ((await this.act(v, '', { method: 'DELETE' })) === null) {
      this.versions.update((list) => (list ?? []).filter((r) => r.id !== v.id));
      if (this.selectedId() === v.id) {
        this.selectedId.set(this.versions()?.[0]?.id ?? null);
        this.exported.set(false);
      }
    }
  }

  /** "Use for application" — the chosen version is the one that exports. */
  use(v: ResumeRow): void {
    this.selectedId.set(v.id);
    this.exported.set(false);
  }

  /** The label a version row prints beside its name. */
  versionLabel(v: ResumeRow): string {
    const status =
      v.status === 'FINALISED' ? 'Finalised' : v.status === 'DRAFT' ? 'Draft' : 'Generated';
    return `v${v.version} · ${status}`;
  }

  /**
   * Open the server-rendered PDF of the chosen version.
   *
   * `?appendix=true` is what the evidence checkbox now does. It used to do
   * nothing: this method opened the same URL either way, so the box changed one
   * sentence of consent copy and no bytes — and a student who ticked it stopped
   * attaching their certificates by hand, believing they were already in there.
   */
  exportPdf(): void {
    const v = this.selected();
    if (!v || !this.canExport()) return;
    const url =
      `${environment.apiBase}/student/resume/${v.id}/pdf` +
      (this.proofAppendix() ? '?appendix=true' : '');
    window.open(url, '_blank', 'noopener');
    this.exported.set(true);
  }
}
