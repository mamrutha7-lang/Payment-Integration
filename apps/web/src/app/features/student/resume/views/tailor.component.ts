/**
 * Tailor to opportunity — step 2 of the resume flow.
 *
 * Takes the posting chosen in the goal strip and says three things: whether the
 * student can actually apply, which of the role's requirements are still
 * missing (and where each claim stands), and which essential sections of the
 * resume are still empty.
 *
 * THE ELIGIBILITY LINE IS THE POINT. A student with a 90% skill match on a
 * posting whose CGPA cut-off they miss is not eligible, and a screen that
 * printed "90% match" and nothing else would read as an invitation. So the
 * verdict is rendered first and separately from the score, with the server's own
 * `reasons` listed verbatim — a missing requirement is named, not implied.
 *
 * Both numbers come from GET /student/jobs. Nothing here recomputes a match; a
 * second opinion that disagreed with the Jobs screen would be worse than none.
 * The guidance below it reflects the student's CURRENT verified skills, so
 * nothing on this step overstates their eligibility.
 */

import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, output } from '@angular/core';

import { ResumeBuilderService, sectionHasContent } from '../resume-builder.service';
import { ResumeEvidenceService } from '../resume-evidence.service';
import { ResumeGoalService } from '../resume-goal.service';

type ResumeStep = 'build' | 'tailor' | 'preview' | 'export';

interface MissingSkill {
  name: string;
  /** Where the student's claim on it stands — "Not started" when there is none. */
  status: string;
}

/**
 * The sections a recruiter expects to find filled, keyed as the builder stores
 * them. Education and certifications mirror other domains and are not held in
 * the profile map, so they are not judged here.
 */
const ESSENTIALS: { keys: string[]; label: string }[] = [
  { keys: ['basic'], label: 'Headline & basic details' },
  { keys: ['contact'], label: 'Contact details' },
  { keys: ['internship', 'experience'], label: 'Internship / experience' },
  { keys: ['projects'], label: 'Projects' },
];

/** True when a builder section holds no real content (one shared rule). */
function isEmptySection(value: unknown): boolean {
  return !sectionHasContent(value);
}

@Component({
  selector: 'rb-tailor',
  standalone: true,
  imports: [DecimalPipe],
  templateUrl: './tailor.component.html',
  styleUrl: './tailor.component.scss',
})
export class RbTailorComponent {
  readonly goalSvc = inject(ResumeGoalService);
  readonly ev = inject(ResumeEvidenceService);
  private readonly svc = inject(ResumeBuilderService);

  readonly navigate = output<ResumeStep>();

  /** The role the guidance is written for — the goal's, else the posting's. */
  readonly role = computed<string>(
    () => this.goalSvc.goal().role || this.goalSvc.selectedJob()?.title || '',
  );

  /**
   * Requirements of the chosen posting the student cannot yet evidence, each
   * with the state of their claim on it. Matched against INCLUDED verified
   * skills — a skill that exists but is switched off does not strengthen this
   * document, and the status says so ("Verified — not included") rather than
   * hiding a one-toggle fix behind the same words as a term's work.
   */
  readonly missing = computed<MissingSkill[]>(() => {
    const job = this.goalSvc.selectedJob();
    if (!job) return [];
    // SLUGS ON BOTH SIDES. `required_skills` is what the posting asks for in
    // the catalogue's own vocabulary (`excel`, `financial-modeling`); the old
    // comparison put those against display names ("MS Excel") and so reported
    // every requirement missing, directly under a verdict card saying the
    // student matched 100% of them.
    const have = new Set(this.ev.includedSlugs().map((s) => s.toLowerCase()));
    const rows = new Map((this.ev.rows() ?? []).map((r) => [r.slug.toLowerCase(), r]));
    return job.required_skills
      .filter((s) => !have.has(s.toLowerCase()))
      .map((slug) => {
        const row = rows.get(slug.toLowerCase());
        const name = this.ev.label(slug);
        if (!row) return { name, status: 'Not started' };
        if (row.includable) return { name, status: 'Verified — not included' };
        return { name, status: this.ev.chip(row.status).label };
      });
  });

  /** Essential sections still empty in the builder. */
  readonly missingEssentials = computed<string[]>(() => {
    const data = this.svc.data();
    return ESSENTIALS.filter((e) => e.keys.every((k) => isEmptySection(data[k]))).map(
      (e) => e.label,
    );
  });

  constructor() {
    void this.goalSvc.load();
    void this.ev.load();
    if (!this.svc.loaded()) void this.svc.load();
  }
}
