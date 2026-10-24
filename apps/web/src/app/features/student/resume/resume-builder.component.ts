/**
 * Resume Builder shell — the /student/resume route target.
 *
 * Renders inside the app-shell's <router-outlet>, so it repeats none of the
 * desktop titlebar/rail. Instead it owns three things:
 *   - a `step` signal ('build' | 'tailor' | 'preview' | 'export') driving the
 *     numbered flow across the top. The four steps are an ORDER, not a set of
 *     views: content is written, then aimed at a posting, then read as the
 *     employer will read it, then sent. The old three tabs (Profile & Builder /
 *     All Resumes / Generated Resume) named three places with no sequence
 *     between them, so nothing told a student what to do after filling the form;
 *   - the GOAL STRIP above them, which every later step reads — a resume is only
 *     good relative to a target, and there was none;
 *   - a `step` signal (one of the 15 section keys) driving the left stepper and
 *     the @switch that mounts exactly one section component in the .body;
 *   - the .main-head title/sub (from STEPS' meta) and the .footbar save action.
 *
 * Section components read/write shared state through ResumeBuilderService; the
 * shell only calls svc.load() on init and svc.save() from the footbar. The
 * 'education' step swaps "Save section" for an approval-styled button, because
 * academic edits route to a mentor (both paths call save() at this layer).
 */

import { Component, computed, inject, signal, type OnDestroy } from '@angular/core';

import { ResumeBuilderService } from './resume-builder.service';

// 15 section components (sections/<name>.component.ts) — created in parallel.
import { RbBasicComponent } from './sections/basic.component';
import { RbContactComponent } from './sections/contact.component';
import { RbFamilyComponent } from './sections/family.component';
import { RbEducationComponent } from './sections/education.component';
import { RbAttachmentsComponent } from './sections/attachments.component';
import { ExperienceSection } from './sections/experience.component';
import { InternshipSection } from './sections/internship.component';
import { ProjectsSection } from './sections/projects.component';
import { PublicationsSection } from './sections/publications.component';
import { SeminarsSection } from './sections/seminars.component';
import { RbCertificationsComponent } from './sections/certifications.component';
import { PorSection } from './sections/por.component';
import { RbOtherComponent } from './sections/other.component';
import { RbReferencesComponent } from './sections/references.component';
import { RbPolicyComponent } from './sections/policy.component';

// 2 view components (views/<name>.component.ts) — created in parallel.
import { RbPreviewComponent } from './views/preview.component';
import { RbTailorComponent } from './views/tailor.component';
import { RbExportComponent } from './views/export.component';
import { RbEvidenceSkillsComponent } from './sections/evidence-skills.component';
import { ResumeGoalService } from './resume-goal.service';
import { ResumeEvidenceService } from './resume-evidence.service';

/** The four steps of the flow, in order. */
type ResumeStep = 'build' | 'tailor' | 'preview' | 'export';

interface Step {
  /** section key, matches svc.section(key) and the @switch cases */
  key: string;
  /** label shown in the left stepper */
  label: string;
  /** .main-head title when this step is active */
  title: string;
  /** .main-head subtitle when this step is active */
  sub: string;
}

interface StepGroup {
  name: string;
  steps: Step[];
}

/**
 * Leaf keys the FORM fills in, not the student. Mirrors `_STRUCTURAL_LEAF_KEYS`
 * in apps/api-py/app/routers/student.py — the server computes the percentage
 * and the client colours the dots, and the two have to mean the same thing.
 */
const STRUCTURAL_LEAF_KEYS = new Set(['country', 'code']);

/**
 * Sections that mirror another domain: they hold none of the student's typing,
 * so "Not started" is the wrong word for them. See stepHint().
 */
const MIRROR_SECTIONS = new Set(['education', 'attachments', 'certifications']);

/**
 * The 15 steps in their 5 groups. `title`/`sub` are the mockup's meta map (they
 * differ from the shorter stepper `label` for publications and seminars).
 */
const STEP_GROUPS: StepGroup[] = [
  {
    name: 'Identity',
    steps: [
      {
        key: 'basic',
        label: 'Basic Details',
        title: 'Basic Details',
        sub: 'Identity and demographics. Fields synced from the university record are locked.',
      },
      {
        key: 'contact',
        label: 'Contact Details',
        title: 'Contact Details',
        sub: 'Phone, email, links and addresses. Repeatable rows for anything you have more than one of.',
      },
      {
        key: 'family',
        label: 'Family Details',
        title: 'Family Details',
        sub: 'Next-of-kin information required by the placement office.',
      },
    ],
  },
  {
    name: 'Academics',
    steps: [
      {
        key: 'education',
        label: 'Education',
        title: 'Education',
        sub: 'Semester record, prior qualifications and declared academic gaps. Edits here need approval.',
      },
      {
        key: 'attachments',
        label: 'Attachments',
        title: 'Attachments',
        sub: 'A ledger of every document, routed from the section that owns it.',
      },
      {
        // Sits in Academics rather than a group of its own: it is the record of
        // what REEP can vouch for, next to the record of what the university
        // can. Both are things the student does not type.
        key: 'evidence_skills',
        label: 'Evidence-backed Skills',
        title: 'Evidence-backed Skills',
        sub: 'Skills a mentor has verified, and which of them this resume claims.',
      },
    ],
  },
  {
    name: 'Experience',
    steps: [
      {
        key: 'experience',
        label: 'Professional Experience',
        title: 'Professional Experience',
        sub: 'Full-time roles, with description bullets that feed the resume directly.',
      },
      {
        key: 'internship',
        label: 'Internship',
        title: 'Internship',
        sub: 'Internships, tracked separately from full-time experience.',
      },
      {
        key: 'projects',
        label: 'Projects',
        title: 'Projects',
        sub: 'Academic, capstone and personal projects.',
      },
    ],
  },
  {
    name: 'Achievement',
    steps: [
      {
        key: 'publications',
        label: 'Publications / Research',
        title: 'Publications / Research / White Papers',
        sub: 'Published or in-review research output.',
      },
      {
        key: 'seminars',
        label: 'Seminars / Trainings',
        title: 'Seminars / Trainings / Workshops',
        sub: 'Short-form learning that is not a full certification.',
      },
      {
        key: 'certifications',
        label: 'Certification / Assessments',
        title: 'Certification / Assessments',
        sub: 'REEP certifications sync automatically; outside ones are added manually.',
      },
      {
        key: 'por',
        label: 'Positions of Responsibility',
        title: 'Positions of Responsibility',
        sub: 'Leadership and committee roles.',
      },
    ],
  },
  {
    name: 'Final',
    steps: [
      {
        key: 'other',
        label: 'Other Details',
        title: 'Other Details',
        sub: 'Objective, key expertise, achievements, awards and activities.',
      },
      {
        key: 'references',
        label: 'References',
        title: 'References',
        sub: 'Referees a recruiter may contact.',
      },
      {
        key: 'policy',
        label: 'Placement Policy',
        title: 'Placement Policy',
        sub: 'Controls which opportunities you appear against.',
      },
    ],
  },
];

@Component({
  selector: 'app-resume-builder',
  standalone: true,
  imports: [
    RbBasicComponent,
    RbContactComponent,
    RbFamilyComponent,
    RbEducationComponent,
    RbAttachmentsComponent,
    ExperienceSection,
    InternshipSection,
    ProjectsSection,
    PublicationsSection,
    SeminarsSection,
    RbCertificationsComponent,
    PorSection,
    RbOtherComponent,
    RbReferencesComponent,
    RbPolicyComponent,
    RbPreviewComponent,
    RbTailorComponent,
    RbExportComponent,
    RbEvidenceSkillsComponent,
  ],
  templateUrl: './resume-builder.component.html',
  styleUrl: './resume-builder.component.scss',
})
export class ResumeBuilderComponent implements OnDestroy {
  readonly svc = inject(ResumeBuilderService);

  readonly groups = STEP_GROUPS;

  readonly goalSvc = inject(ResumeGoalService);
  readonly ev = inject(ResumeEvidenceService);

  readonly flowStep = signal<ResumeStep>('build');
  readonly step = signal<string>('basic');

  /** Set by "Import verified record" so the sidebar can report what it did. */
  readonly importedCount = signal<number | null>(null);

  /**
   * Steps that MIRROR another domain and own no editable field, so the footbar
   * offers no save. Education's button used to read "Update & request approval"
   * and do neither.
   */
  readonly readOnlyStep = computed(() => this.step() === 'education' || this.step() === 'attachments');

  /** The active step's meta (title/sub for the .main-head). */
  readonly current = computed<Step>(() => {
    const key = this.step();
    for (const g of STEP_GROUPS) {
      const found = g.steps.find((s) => s.key === key);
      if (found) return found;
    }
    return STEP_GROUPS[0].steps[0];
  });

  /**
   * Per-section fill state for the stepper dots, keyed by section key:
   * 'done' (every leaf filled), 'partial' (some filled) or 'empty' (none).
   * Derived purely from svc.section(key); read-only mirror sections
   * (education / attachments / certifications) live in other domains and so
   * stay 'empty' here until their slice is populated.
   */
  readonly stepStates = computed<Record<string, 'done' | 'partial' | 'empty'>>(() => {
    // touch the map so this recomputes on every patch/load
    this.svc.data();
    const mirrors = this.svc.mirrorStates();
    const out: Record<string, 'done' | 'partial' | 'empty'> = {};
    for (const g of STEP_GROUPS) {
      for (const s of g.steps) {
        // Education / Attachments / Certifications mirror other domains and
        // write nothing into `data`, so their own components report their state
        // (ResumeBuilderService.mirrorStates). Reading `data` for them returned
        // "Not started" forever, whatever the record held.
        if (s.key in mirrors) {
          out[s.key] = mirrors[s.key];
          continue;
        }
        const { filled, total } = this.countLeaves(this.svc.section(s.key, null));
        out[s.key] = filled === 0 ? 'empty' : total > 0 && filled >= total ? 'done' : 'partial';
      }
    }
    return out;
  });

  /**
   * The stepper dot's tooltip.
   *
   * "Not started" is a judgement about the STUDENT'S work, and it was being
   * applied to three sections that hold no work of theirs: Education,
   * Attachments and Certifications mirror the university record, the document
   * ledger and the programme's certifications. Each reports its real state once
   * it has been opened (ResumeBuilderService.mirrorStates); until then the
   * honest answer is what the section is, not an accusation that the student
   * has neglected it.
   *
   * Deliberately NOT solved by having the shell pre-fetch all three on mount:
   * that is four network round-trips on every visit to colour three dots.
   */
  stepHint(key: string): string {
    const state = this.stepStates()[key];
    if (state === 'done') return 'Complete';
    if (state === 'partial') return 'Partly filled';
    if (MIRROR_SECTIONS.has(key)) return 'Imported from your record — open to see';
    return 'Not started';
  }

  /**
   * Save-bar state, in priority order: a save in flight, then unsaved local
   * edits, then a clean/saved profile. Text + colour together — never colour
   * alone — are chosen from this in the template.
   */
  readonly saveState = computed<'saving' | 'unsaved' | 'saved' | 'clean'>(() => {
    if (this.svc.saving()) return 'saving';
    if (this.svc.dirty()) return 'unsaved';
    return this.svc.savedAt() ? 'saved' : 'clean';
  });

  /** Re-evaluated every half minute so a relative label ("Saved 2 minutes
   *  ago") keeps moving while the student reads without saving again. */
  private readonly tick = signal(0);
  private readonly ticker = setInterval(() => this.tick.update((n) => n + 1), 30_000);

  /** "Saved just now" inside a minute, "Saved N minutes ago" inside the hour,
   *  then the clock time — the design's chip, kept truthful as time passes. */
  readonly savedLabel = computed<string>(() => {
    this.tick();
    const iso = this.svc.savedAt();
    if (!iso) return '';
    const then = new Date(iso);
    const secs = (Date.now() - then.getTime()) / 1000;
    if (secs < 60) return 'Saved just now';
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `Saved ${mins} minute${mins === 1 ? '' : 's'} ago`;
    const hh = String(then.getHours()).padStart(2, '0');
    const mm = String(then.getMinutes()).padStart(2, '0');
    return `Saved at ${hh}:${mm}`;
  });

  ngOnDestroy(): void {
    clearInterval(this.ticker);
  }

  constructor() {
    void this.svc.load();
    void this.goalSvc.load();
    void this.ev.load();
  }

  save(): void {
    void this.svc.save();
  }

  go(step: ResumeStep): void {
    this.flowStep.set(step);
  }

  /**
   * Move to a section and retire the import receipt.
   *
   * "1 verified skill(s) added ✓" sat in the sidebar for the rest of the
   * session, under every subsequent section, long after the student had stopped
   * caring — a confirmation that outlives its action reads as a status.
   */
  goToStep(key: string): void {
    this.step.set(key);
    this.importedCount.set(null);
  }

  /** Pull every mentor-verified skill into the resume in one action. */
  importVerified(): void {
    this.importedCount.set(this.ev.importVerified());
  }

  /**
   * Count filled vs total CONTENT leaves in a stored section slice.
   *
   * Only strings are content. Booleans and numbers used to count as filled
   * "(a deliberate choice)", and with the form's own defaults they made a dot
   * go amber for nothing typed: adding an empty phone row writes
   * `{code: "+91", number: ""}` and unticking a checkbox writes
   * `permanent_same: false`. The API applies the same rule when it computes
   * completeness (`_section_filled` in routers/student.py), and the two must
   * agree or the sidebar's percentage and its dots describe different profiles.
   */
  private countLeaves(v: unknown, key?: string): { filled: number; total: number } {
    if (v === null || v === undefined) return { filled: 0, total: 0 };
    if (Array.isArray(v)) {
      return v.reduce(
        (acc, item) => {
          const c = this.countLeaves(item);
          return { filled: acc.filled + c.filled, total: acc.total + c.total };
        },
        { filled: 0, total: 0 },
      );
    }
    if (typeof v === 'object') {
      return Object.entries(v as Record<string, unknown>).reduce<{
        filled: number;
        total: number;
      }>(
        (acc, [k, item]) => {
          const c = this.countLeaves(item, k);
          return { filled: acc.filled + c.filled, total: acc.total + c.total };
        },
        { filled: 0, total: 0 },
      );
    }
    if (typeof v === 'string') {
      // The dial code and the country are supplied by the form, not the student.
      if (STRUCTURAL_LEAF_KEYS.has(key ?? '')) return { filled: 0, total: 0 };
      return { filled: v.trim() ? 1 : 0, total: 1 };
    }
    // Booleans and numbers are structure, not content.
    return { filled: 0, total: 0 };
  }
}
