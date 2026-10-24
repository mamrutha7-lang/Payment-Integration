/**
 * Generated Resume — the resume-builder's "preview" view (mockup data-view="preview").
 *
 * A standalone "Generate Resume" flow. It does NOT auto-run on mount (each POST
 * creates a new Resume row, so generation is an explicit action): the student
 * types an optional title and presses Generate, which POSTs to
 * `${apiBase}/student/resume/generate` and renders the returned markdown in the
 * left .preview column. Download PDF opens `${apiBase}/student/resume/{id}/pdf`.
 *
 * The backend composes the resume deterministically when a model would take
 * student data off the machine (the AGENTS.md egress rule), and returns a `note`
 * saying so with `used_ai:false`. That is a normal, successful result here — the
 * Generation trace card surfaces the note rather than treating it as an error.
 *
 * The right-column cards read shared builder state through ResumeBuilderService:
 * "What would strengthen this" names the still-empty value-add sections and shows
 * the live completeness. Edit-profile requests a view change via the `navigate`
 * output, which the shell binds to its flow-step signal.
 */

import { Component, computed, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { environment } from '../../../../../environments/environment';
import { ResumeBuilderService, sectionHasContent } from '../resume-builder.service';
import { ResumeEvidenceService } from '../resume-evidence.service';
import { ResumeGoalService } from '../resume-goal.service';

type ResumeView = 'builder' | 'resumes' | 'preview';

/** Response shape of POST /student/resume/generate (verbatim from student.py). */
interface GenerateResult {
  id: string;
  version: number;
  generated_by: string;
  model: string | null;
  used_ai: boolean;
  note: string | null;
  markdown: string;
}

/** One inline run of text; `bold` marks a **…** span. */
interface Segment {
  text: string;
  bold: boolean;
}

/** A rendered markdown block. */
interface Block {
  kind: 'title' | 'section' | 'bullet' | 'para' | 'table';
  segs: Segment[];
  /** For a `table` block: the header row, then the body rows. */
  rows?: string[][];
}

/**
 * The value-add builder sections, keyed as ResumeBuilderService stores them, with
 * the label the "What would strengthen this" card shows when one is still empty.
 */
const VALUE_SECTIONS: { key: string; label: string }[] = [
  { key: 'experience', label: 'Professional Experience' },
  { key: 'internship', label: 'Internship' },
  { key: 'projects', label: 'Projects' },
  { key: 'publications', label: 'Publications / Research' },
  { key: 'seminars', label: 'Seminars / Trainings' },
  { key: 'por', label: 'Positions of Responsibility' },
  { key: 'references', label: 'References' },
];

/** Split a line into normal/bold runs on the `**` delimiter. */
function segmentsOf(text: string): Segment[] {
  return text
    .split('**')
    .map((t, i) => ({ text: t, bold: i % 2 === 1 }))
    .filter((s) => s.text.length > 0);
}

/** The cells of one `| a | b |` table line. */
function cellsOf(line: string): string[] {
  return line
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());
}

/** A `| --- | --- |` line, which separates a table's header from its body. */
function isSeparator(line: string): boolean {
  return cellsOf(line).every((c) => c.length > 0 && /^[-:\s]+$/.test(c));
}

/**
 * A tiny markdown reader: headings, bullets, paragraphs with inline bold, and
 * the `| … |` table the standard template draws Education as.
 */
function parseMarkdown(md: string): Block[] {
  const blocks: Block[] = [];
  let table: string[][] | null = null;
  for (const raw of md.split('\n')) {
    const line = raw.trim();
    if (line.startsWith('|')) {
      if (!table) {
        table = [];
        blocks.push({ kind: 'table', segs: [], rows: table });
      }
      if (!isSeparator(line)) table.push(cellsOf(line));
      continue;
    }
    table = null;
    if (!line) continue;
    if (line.startsWith('# ')) blocks.push({ kind: 'title', segs: segmentsOf(line.slice(2)) });
    else if (line.startsWith('## '))
      blocks.push({ kind: 'section', segs: segmentsOf(line.slice(3)) });
    else if (line.startsWith('### '))
      blocks.push({ kind: 'section', segs: segmentsOf(line.slice(4)) });
    else if (line.startsWith('- '))
      blocks.push({ kind: 'bullet', segs: segmentsOf(line.slice(2)) });
    else blocks.push({ kind: 'para', segs: segmentsOf(line) });
  }
  return blocks;
}

/** True when a builder section holds no real content (one shared rule). */
function isEmptySection(value: unknown): boolean {
  return !sectionHasContent(value);
}

/** Roughly how many rendered lines fit one A4 page of the PDF renderer. The
 *  count is an estimate for the "consider trimming" nudge, not a promise. */
const LINES_PER_PAGE = 46;

@Component({
  selector: 'rb-preview',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './preview.component.html',
  styleUrl: './preview.component.scss',
})
export class PreviewView {
  readonly svc = inject(ResumeBuilderService);
  private readonly goalSvc = inject(ResumeGoalService);
  private readonly ev = inject(ResumeEvidenceService);

  /** ATS-safe: a plain, single-column, system-font rendering of the same text —
   *  what an applicant-tracking parser sees. On by default, as the design has it. */
  readonly atsOn = signal(true);

  /** Estimated page count of the generated document; null before a generation. */
  readonly pageCount = computed<number | null>(() => {
    const r = this.result();
    if (!r) return null;
    const lines = r.markdown.split('\n').filter((l) => l.trim().length > 0);
    return Math.max(1, Math.ceil(lines.length / LINES_PER_PAGE));
  });

  /**
   * Warnings a recruiter would raise before a student does: no projects, and
   * the chosen posting's requirements that are still unverified. Both are
   * derived from the builder state and the goal — nothing is invented.
   */
  readonly warnings = computed<string[]>(() => {
    const out: string[] = [];
    if (isEmptySection(this.svc.data()['projects'])) {
      out.push('No projects added — recruiters for this role usually screen for at least one.');
    }
    const job = this.goalSvc.selectedJob();
    if (job) {
      // Slugs on both sides — see ResumeEvidenceService.includedSlugs. Comparing
      // a posting's `excel` against the display name "MS Excel" warned every
      // student that every requirement was unverified, including the ones they
      // had verified and included.
      const have = new Set(this.ev.includedSlugs().map((s) => s.toLowerCase()));
      const missing = job.required_skills.filter((s) => !have.has(s.toLowerCase())).length;
      if (missing > 0) {
        const role = this.goalSvc.goal().role || job.title;
        out.push(
          `${missing} required skill${missing > 1 ? 's' : ''} for ${role} still unverified.`,
        );
      }
    }
    return out;
  });

  /** Ask the shell to switch its top-level view. Inert until the shell binds it. */
  readonly navigate = output<ResumeView>();

  /** The generate form's title (optional; the API defaults it to "REEP Resume"). */
  title = '';

  readonly generating = signal(false);
  readonly result = signal<GenerateResult | null>(null);
  readonly error = signal<string | null>(null);

  /** The returned markdown, parsed into renderable blocks. */
  readonly blocks = computed<Block[]>(() => {
    const r = this.result();
    return r ? parseMarkdown(r.markdown) : [];
  });

  /** Value-add sections still empty in the builder, for the "strengthen" card. */
  readonly suggestions = computed<string[]>(() => {
    const data = this.svc.data();
    return VALUE_SECTIONS.filter((s) => isEmptySection(data[s.key])).map((s) => s.label);
  });

  constructor() {
    // The shell loads the profile on init; load defensively if arrived cold so
    // completeness() and the suggestions are populated for the right column.
    if (!this.svc.loaded()) void this.svc.load();
    void this.goalSvc.load();
    void this.ev.load();
  }

  async generate(): Promise<void> {
    if (this.generating()) return;
    this.generating.set(true);
    this.error.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/resume/generate`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: this.title.trim() || null }),
      });
      if (!res.ok) {
        const detail = ((await res.json().catch(() => ({}))) as { detail?: string }).detail;
        this.error.set(detail ?? 'Could not generate a resume.');
        return;
      }
      this.result.set((await res.json()) as GenerateResult);
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.generating.set(false);
    }
  }

  /** Open the server-rendered PDF for the generated resume in a new tab. */
  download(): void {
    const r = this.result();
    if (!r) return;
    window.open(`${environment.apiBase}/student/resume/${r.id}/pdf`, '_blank', 'noopener');
  }
}

// The shell imports this component under its house Rb* name; keep both exports
// pointing at the same class so shell wiring and the task's class name agree.
export { PreviewView as RbPreviewComponent };
