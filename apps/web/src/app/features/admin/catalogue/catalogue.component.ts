/**
 * Catalogue — what students may enrol in, and which certifications count.
 *
 * The board (docs/redesign-2026-09/design/admin/Catalogue.html) draws four
 * tabs over two different kinds of thing, and only two of the four have a
 * backend today. What each one is, and why it renders the way it does:
 *
 *  SUBJECTS are the curriculum, grouped by semester, with the certifications
 *  mapped to each and how many students are enrolled. There is still no
 *  per-subject form, and the reason is unchanged: a subject carries a code, a
 *  stage, a dimension, a semester and a delivery model, and a four-field form
 *  cannot supply those without inventing them. What B13 added is the way an
 *  office actually maintains a curriculum — "Import subjects", a CSV through
 *  `POST /admin/catalogue/subjects/import`, whose `dry_run` DEFAULTS TO TRUE
 *  on the server because its input is a file somebody exported from a
 *  spreadsheet. The panel follows that: the Import button does nothing until a
 *  dry run has reported, every row is listed including the failures, and a
 *  subject code being global across the deployment means a scoped grant is
 *  refused rather than writing a row every other college would then see.
 *
 *  B13 ALSO MOVED THE CERTIFICATION LIST ONTO THIS SCREEN'S OWN FUNCTION.
 *  The three `/admin/approved-certifications` handlers were `require_admin`
 *  while the screen itself opened on the grantable `admin.catalogue`, and this
 *  file carried a signal and a paragraph apologising for the split. They are
 *  `require_capability(db, session, "admin.catalogue")` now, so a 403 there
 *  means the grant does not REACH the row — the defensive branch stays,
 *  because a refusal must not render as "no certifications", but its sentence
 *  says the true thing.
 *
 *  CERTIFICATIONS ↔ BADGES is the Approved Certification Catalogue
 *  (framework §12), the list administration genuinely maintains: which
 *  external certificates count as evidence towards which badge. The grid is by
 *  BADGE because that is the question the screen answers — which of the 48
 *  badges a student can actually reach, and which have nothing mapped to them
 *  yet. THE BADGE CATALOGUE IS CODE (AGENTS.md, models/badge.py), so this
 *  screen offers no add, no edit and no retire for a badge; the board's "Add
 *  badge" and "Retire badge" would be buttons with no endpoint behind them and
 *  never will have one. What it does offer is the mapping: "Add certification"
 *  writes an approved certification against a badge, and "Remove" DEACTIVATES
 *  rather than deletes, so evidence students have already filed keeps its
 *  reference and the row can be brought back.
 *
 *  Category and Points are read off the badge (never typed), so the two can
 *  never disagree with the catalogue.
 *
 *  STAGE RULES are real: `GET/PUT/DELETE /admin/catalogue/stage-rules`, one
 *  row per (course, semester), and `POST /admin/cohorts/{id}/promote` reads
 *  them. The PUT is an UPSERT because `uq_stage_rule_course_semester` makes a
 *  second rule for one semester an IntegrityError — the unique key IS the
 *  identity of the thing being edited — and the DELETE is a real delete,
 *  because nothing hangs off a rule and a promotion with no rule simply leaves
 *  the stage alone. The tab keeps THREE states apart: not read, read and
 *  empty, and read with rows. The first two must never render the same
 *  sentence.
 *
 *  THE TWO SCOPE PILLS filter on `college_id` / `course_id`, which every
 *  certification row now carries — and BOTH NULL IS PROGRAMME-WIDE, so
 *  neither pill hides such a row. That is the same reading
 *  `list_approved_certifications` gives `?course_id=` ("the course's own rows
 *  PLUS the programme-wide ones"), and a client filter that read it any other
 *  way would disagree with the endpoint beside it and empty the grid on the
 *  day somebody first picked a college. The college list is learned from the
 *  course rows rather than fetched: `GET /admin/colleges` is
 *  `admin.institution`, a different function from this screen's.
 *
 *  "COPY TO COURSE…" IS ONE FLAT SEARCHABLE LIST, NOT THE BOARD'S TWO-STEP.
 *  The endpoint is `{from_course, to_course, parts[]}` — the owner's settled
 *  decision — so a college select followed by a course select would be two
 *  controls and two round trips to answer one question on a deployment with
 *  one college. Every row carries its college's name and the search matches
 *  it. The copy is additive and never overwrites; the dry run is offered
 *  first, because the useful moment to learn that eleven of twelve rows
 *  already exist is before the copy.
 *
 *  INTERVIEW TRACKS are real and READ-ONLY here: the four tracks the mock
 *  interviewer runs, from `GET /api/admin/interview-questions/tracks`. That
 *  endpoint is the Question bank's, and its capability is
 *  `admin.interview_questions` — an account holding only `admin.catalogue`
 *  gets a 403, which is a fact about the grant and not an error, so it is
 *  reported as a note rather than as a failure.
 *
 * ONE COLUMN DIFFERS FROM THE BOARD ON PURPOSE. The board's "Earned" column
 * needs a per-badge earned count, which no endpoint computes today. The column
 * shows CLAIMS instead — evidence rows filed against this badge's
 * certifications, a real number that arrives with the certifications — and the
 * note above the grid says so. A dash in every row would have been the other
 * honest option; a number nobody computed would not.
 */

import { Component, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { PluralPipe } from '../../../shared/text/plural.pipe';

/** A certification mapped to a taught subject, as the subjects table shows it. */
interface SubjectCertification {
  code: string;
  name: string;
  provider: string;
  required_hours: number;
  is_optional: boolean;
  link: string | null;
}

/** A taught subject — `Course` in the API, "subject" on the board and in the
 *  office's vocabulary, because "Course" is MBA in the institutional spine. */
interface Subject {
  code: string;
  name: string;
  stage: string;
  dimension: string;
  semester: number;
  teaching_hours: number;
  self_learning_hours_required: number;
  model_type: string;
  duration_weeks: number;
  /** Enrolment fee in paise; 0 = free. */
  fee_paise: number;
  enrolled: number;
  certifications: SubjectCertification[];
}

/** One programme, from `GET /api/admin/catalogue/courses` (B13).
 *
 *  FLAT AND ACROSS COLLEGES, which is the owner's settled decision (7) and the
 *  endpoint's own docstring: the board draws a college select and then a
 *  course select, two controls and two round trips to answer one question on a
 *  deployment that has one college. The college's NAME is on every row, so one
 *  search box finds "MBA" and "BGSCET" equally. */
interface CatalogueCourse {
  id: string;
  code: string;
  name: string;
  department_id: string | null;
  department: string | null;
  college_id: string | null;
  college: string | null;
  degree_level: string | null;
  total_semesters: number | null;
  certifications: number;
  badge_overrides: number;
  stage_rules: number;
}

/** "Semester N of this course is stage S" — `GET/PUT /admin/catalogue/stage-rules`. */
interface StageRule {
  id: string;
  course_id: string;
  semester: number;
  stage: string;
}

/** One part of a catalogue copy, as the server counts it. `skipped` is
 *  reported rather than swallowed: "copied 0, skipped 12" and "copied 0" are
 *  different answers and only one of them means the copy did nothing. */
interface CopyPartResult {
  part: string;
  copied: number;
  skipped: number;
}

interface CopyResult {
  from_course: string;
  to_course: string;
  dry_run: boolean;
  parts: CopyPartResult[];
}

/** One line of a subject-import report. `outcome` is `create`, `update` or
 *  `error` — three words and not a boolean, because "this row would CHANGE an
 *  existing subject" is the one an office reader needs before saying yes. */
interface SubjectImportRow {
  line: number;
  code: string;
  outcome: string;
  detail: string | null;
}

interface SubjectImportResult {
  dry_run: boolean;
  created: number;
  updated: number;
  errors: number;
  rows: SubjectImportRow[];
}

/** A row of the Approved Certification Catalogue. */
interface ApprovedCertification {
  id: string;
  name: string;
  provider: string;
  badge_code: string;
  badge_name: string;
  badge_category: string;
  badge_points: number;
  evidence_type: string;
  stage: string;
  duration_text: string | null;
  is_free: boolean;
  url: string | null;
  active: boolean;
  claims: number;
  /** B13's scope, resolved by the server. BOTH NULL IS PROGRAMME-WIDE and is
   *  what every row written before B13 means, so a scope filter must keep such
   *  a row visible — `list_approved_certifications` does exactly that for
   *  `?course_id=`, and the two filters on this screen follow it rather than
   *  inventing a second reading. `scope_label` is the sentence to render: it
   *  says "Programme-wide" rather than leaving a blank cell, because a blank
   *  in a scope column reads as "not set yet" on a row whose scope IS set, and
   *  set wide. */
  college_id: string | null;
  college: string | null;
  course_id: string | null;
  course: string | null;
  scope_label: string;
}

/** One of the 48 code-defined badges. */
interface Badge {
  code: string;
  name: string;
  category: string;
  category_label: string;
  stage: string;
  points: number;
}

/** A mock-interview track, read from the question bank. */
interface InterviewTrack {
  key: string;
  label: string;
  phases: string[];
  count: number;
  enabled_count: number;
}

type Tab = 'subjects' | 'badges' | 'stages' | 'tracks';

/** Whether a badge's certifications are mapped, as the grid filter asks it. */
type MappingFilter = 'ALL' | 'MAPPED' | 'UNMAPPED';

/** The catalogue's four stages, as the office says them. */
const STAGE_LABEL: Record<string, string> = {
  REBOOT: 'Reboot',
  EXCEL: 'Excel',
  EXCEL_ADVANCED: 'Excel-Adv',
  ELEVATE: 'Elevate',
};

/** §8: readiness badges refuse evidence — staff award them when the
 *  assessment thresholds are met, so their verifier is not a mentor queue. */
const READINESS_CATEGORY = 'READINESS';

/** How many certification chips a grid row shows before it counts the rest. */
const CERTIFICATION_CHIPS_PER_ROW = 2;

/** B13 LANDED AND ALL FOUR OF THESE ARE REAL NOW. What used to be one phase
 *  constant on four disabled controls is four wired endpoints:
 *
 *    GET  /admin/catalogue/courses          the flat course list, across colleges
 *    POST /admin/catalogue/subjects/import  "Import subjects", DRY RUN BY DEFAULT
 *    POST /admin/catalogue/copy             "Copy to course…", `?dry_run=`
 *    GET/PUT/DELETE /admin/catalogue/stage-rules
 *
 *  The constant is deleted rather than renumbered: a phase badge pointing at a
 *  phase that has arrived is the stale label commit 45b91a9 fixed.
 *
 *  THE COPY IS ONE FLAT SEARCHABLE COURSE PICKER, NOT THE BOARD'S TWO-STEP.
 *  The endpoint is `{from_course, to_course, parts[]}` (the owner's settled
 *  decision 7, and the endpoint's own docstring), and every course row carries
 *  its college's name — so one search box over one list answers "which MBA,
 *  whose?" in one control instead of two selects and two round trips on a
 *  deployment that has one college. */

/** The parts a copy knows how to move. Named, in the server's own spelling and
 *  order, because `CopyPart` is a `Literal` there and a part the server does
 *  not recognise is a 422 rather than a silent no-op reported as success. */
const COPY_PARTS: { key: string; label: string; help: string }[] = [
  {
    key: 'certifications',
    label: 'Approved certifications',
    help: 'Rows pinned to the source course. Programme-wide rows are not copied — they already apply.',
  },
  {
    key: 'badges',
    label: 'Badge map',
    help:
      'Which of the 48 badges are switched off for the course. No control on this screen ' +
      'writes one of those rows; this moves the ones that exist.',
  },
  {
    key: 'stage_rules',
    label: 'Stage rules',
    help: 'Which REEP stage each semester of the course sits in.',
  },
];

/** The four stages a rule may name — `models/badge.py::Stage`, the same list
 *  `STAGE_LABEL` above renders. Written once and read by both. */
const STAGE_VALUES = ['REBOOT', 'EXCEL', 'EXCEL_ADVANCED', 'ELEVATE'];

/** The columns `POST /admin/catalogue/subjects/import` requires, in the
 *  server's own spelling (`REQUIRED_SUBJECT_COLUMNS`). Shown on the panel so a
 *  sheet is fixed before it is uploaded rather than after the 422. */
const SUBJECT_IMPORT_COLUMNS = 'code, name, stage, dimension, semester, model_type';

function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

@Component({
  selector: 'app-admin-catalogue',
  standalone: true,
  imports: [RouterLink, PluralPipe],
  templateUrl: './catalogue.component.html',
  styleUrl: './catalogue.component.scss',
})
export class AdminCatalogueComponent {
  readonly apiBase = environment.apiBase;
  readonly copyParts = COPY_PARTS;
  readonly stageValues = STAGE_VALUES;
  readonly subjectImportColumns = SUBJECT_IMPORT_COLUMNS;

  // ---- B13: the courses a catalogue can be scoped, copied or ruled by ----
  /** Every programme this account's grant reaches, flat and across colleges.
   *  NULL means the read has not happened or did not succeed — never `[]`,
   *  which means "read, and this grant reaches none". */
  readonly courses = signal<CatalogueCourse[] | null>(null);

  /** The two scope pills. EMPTY IS "ALL", and neither hides a PROGRAMME-WIDE
   *  certification: `list_approved_certifications` reads `?course_id=` as "the
   *  course's own rows PLUS the programme-wide ones", because that is the set
   *  that actually applies to a student on that course, and a client filter
   *  that read it any other way would disagree with the endpoint beside it. */
  readonly collegeFilter = signal('');
  readonly courseFilter = signal('');

  // ---- B13: stage rules --------------------------------------------------
  readonly stageRules = signal<StageRule[] | null>(null);
  readonly stageRuleCourse = signal('');
  readonly stageRuleSemester = signal(1);
  readonly stageRuleStage = signal(STAGE_VALUES[0]);
  readonly stageBusy = signal(false);
  readonly stageError = signal<string | null>(null);

  // ---- B13: subject import ----------------------------------------------
  readonly importOpen = signal(false);
  readonly importFileName = signal<string | null>(null);
  readonly importResult = signal<SubjectImportResult | null>(null);
  readonly importBusy = signal(false);
  readonly importError = signal<string | null>(null);
  private importFile: File | null = null;

  // ---- B13: copy ---------------------------------------------------------
  readonly copyOpen = signal(false);
  readonly copyQuery = signal('');
  readonly copyFrom = signal('');
  readonly copyTo = signal('');
  readonly copySelectedParts = signal<string[]>(COPY_PARTS.map((part) => part.key));
  readonly copyPreview = signal<CopyResult | null>(null);
  readonly copyBusy = signal(false);
  readonly copyError = signal<string | null>(null);

  readonly tab = signal<Tab>('subjects');
  readonly subjects = signal<Subject[] | null>(null);
  readonly certifications = signal<ApprovedCertification[] | null>(null);
  readonly badges = signal<Badge[]>([]);
  readonly tracks = signal<InterviewTrack[] | null>(null);
  /** True when this account may read the catalogue but not the question bank. */
  readonly tracksNeedQuestionBank = signal(false);
  /** True when the tracks read FAILED (not a 403 — that is the grant note
   *  above). Without it the tab's else-branch says "Loading the interview
   *  tracks…" for ever, which is a spinner that is telling a lie. */
  readonly tracksFailed = signal(false);
  /** True when the Approved Certification Catalogue refused this account: it is
   *  the Main Admin's list (`require_admin`), while the screen itself opens on
   *  the grantable `admin.catalogue`. A granted faculty member therefore reads
   *  the subjects and is told plainly why the mapping is not theirs to edit —
   *  which is a fact about the grant, not a failure to load. */
  readonly certificationsAreMainAdminOnly = signal(false);

  /** The first read failed. It is NOT the same as "the catalogue is empty", and
   *  the two must not render the same sentence: `subjects.set([])` on a failed
   *  read made the table say "No courses in the catalogue yet" about a
   *  catalogue nobody could read. Failure leaves the signals null and this
   *  true, and every empty state asks this first. */
  readonly loadFailed = signal(false);

  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly loading = signal(true);

  readonly query = signal('');
  readonly skillArea = signal('ALL');
  readonly mapping = signal<MappingFilter>('ALL');
  readonly selectedBadgeCode = signal<string | null>(null);

  readonly formOpen = signal(false);
  readonly formName = signal('');
  readonly formProvider = signal('');
  readonly formBadgeCode = signal('');
  readonly formUrl = signal('');
  /** B13's scope on the ADD form. Empty is programme-wide, which is what every
   *  row written before B13 means and what this form has always sent — and
   *  which `_resolve_certification_scope` REFUSES from a narrowed holder, with
   *  a sentence telling them to name the course. So the control exists rather
   *  than the 403 being the first time anybody hears about it. */
  readonly formCourseId = signal('');
  readonly formError = signal<string | null>(null);
  readonly saving = signal(false);
  readonly busyCertificationId = signal<string | null>(null);

  /** Every skill area in the catalogue, for the filter — from the badges
   *  themselves, so a new category in models/badge.py appears here with it. */
  readonly skillAreas = computed(() => {
    const byValue = new Map<string, string>();
    for (const badge of this.badges()) {
      byValue.set(badge.category, badge.category_label);
    }
    return [...byValue].map(([value, label]) => ({ value, label }));
  });

  /** The skill-area pill reads the LABEL. The signal holds the badge category's
   *  raw value (`PLATFORM`), which is what the API filters on but not a word to
   *  put on a pill beside a dropdown that offered "Platform / Technical Skills". */
  readonly skillAreaLabel = computed(() => {
    const value = this.skillArea();
    if (value === 'ALL') return 'All';
    return this.skillAreas().find((area) => area.value === value)?.label ?? value;
  });

  /** The certifications the two scope pills leave standing. EVERY reader goes
   *  through here — the grid's chips, the Claims column and the drawer — so a
   *  narrowed screen cannot show one number in a cell and a different list
   *  behind it. */
  readonly scopedCertifications = computed(() =>
    (this.certifications() ?? []).filter((row) => this.certificationInScope(row)),
  );

  readonly activeCertifications = computed(() =>
    this.scopedCertifications().filter((certification) => certification.active),
  );

  readonly removedCertifications = computed(() =>
    this.scopedCertifications().filter((certification) => !certification.active),
  );

  /** The badge rows the grid shows, after the quick filter and the two live
   *  filters. The quick filter searches the certifications too, because "where
   *  did we put PL-300" is the question that brings people to this screen. */
  readonly visibleBadges = computed(() => {
    const needle = this.query().trim().toLowerCase();
    const area = this.skillArea();
    const mapping = this.mapping();
    return this.badges().filter((badge) => {
      if (area !== 'ALL' && badge.category !== area) return false;
      const mapped = this.activeCertificationsFor(badge.code).length > 0;
      if (mapping === 'MAPPED' && !mapped) return false;
      if (mapping === 'UNMAPPED' && mapped) return false;
      if (!needle) return true;
      return this.badgeMatches(badge, needle);
    });
  });

  readonly visibleSubjects = computed(() => {
    const needle = this.query().trim().toLowerCase();
    const subjects = this.subjects() ?? [];
    if (!needle) return subjects;
    return subjects.filter((subject) => {
      const haystack = `${subject.code} ${subject.name} ${subject.dimension}`.toLowerCase();
      return haystack.includes(needle);
    });
  });

  readonly selectedBadge = computed(() => {
    const code = this.selectedBadgeCode();
    if (code === null) return null;
    return this.badges().find((badge) => badge.code === code) ?? null;
  });

  /** The selected badge's certifications, removed ones included: the drawer is
   *  where a removed row is brought back, so it has to be able to show one. */
  readonly selectedBadgeCertifications = computed(() => {
    const badge = this.selectedBadge();
    if (badge === null) return [];
    return this.certificationsFor(badge.code);
  });

  /** The evidence rules in play for the selected badge, read off the
   *  certifications mapped to it rather than guessed. */
  readonly selectedBadgeEvidenceRules = computed(() => {
    const rules = new Set<string>();
    for (const certification of this.selectedBadgeCertifications()) {
      if (certification.active) rules.add(certification.evidence_type);
    }
    return [...rules];
  });

  /** The badge the form maps to, for the derived Category / Points line. */
  readonly formBadge = computed(
    () => this.badges().find((badge) => badge.code === this.formBadgeCode()) ?? null,
  );

  constructor() {
    void this.load();
  }

  // --- reading -------------------------------------------------------------

  stageLabel(stage: string): string {
    return STAGE_LABEL[stage] ?? stage;
  }

  dimensionLabel(dimension: string): string {
    return titleCase(dimension);
  }

  evidenceRuleLabel(evidenceType: string): string {
    return titleCase(evidenceType);
  }

  subjectHours(subject: Subject): string {
    const total = subject.teaching_hours + subject.self_learning_hours_required;
    return `${Math.round(total * 10) / 10}`;
  }

  /** Save a subject's enrolment fee (rupees; blank or 0 = free). On failure the
   *  input is put back to the stored value rather than left showing a fee that
   *  was never saved. */
  async saveFee(subject: Subject, input: HTMLInputElement): Promise<void> {
    const rupees = input.value.trim() === '' ? 0 : Number(input.value);
    const restore = () => (input.value = subject.fee_paise ? String(subject.fee_paise / 100) : '');
    if (!Number.isFinite(rupees) || rupees < 0) {
      restore();
      return;
    }
    try {
      const response = await fetch(`${this.apiBase}/admin/courses/${subject.code}/fee`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: rupees || null }),
      });
      if (!response.ok) {
        restore();
        return;
      }
      const saved = (await response.json()) as { fee_paise: number };
      subject.fee_paise = saved.fee_paise;
    } catch {
      restore();
    }
  }

  certificationsFor(badgeCode: string): ApprovedCertification[] {
    const rows = this.scopedCertifications().filter((row) => row.badge_code === badgeCode);
    const active = rows.filter((row) => row.active);
    const removed = rows.filter((row) => !row.active);
    return [...active, ...removed];
  }

  activeCertificationsFor(badgeCode: string): ApprovedCertification[] {
    return this.scopedCertifications().filter(
      (row) => row.badge_code === badgeCode && row.active,
    );
  }

  /** The first few chips a grid row shows. */
  chipCertificationsFor(badgeCode: string): ApprovedCertification[] {
    return this.activeCertificationsFor(badgeCode).slice(0, CERTIFICATION_CHIPS_PER_ROW);
  }

  /** How many more there are than the row has room for. */
  extraCertificationCount(badgeCode: string): number {
    const total = this.activeCertificationsFor(badgeCode).length;
    return Math.max(0, total - CERTIFICATION_CHIPS_PER_ROW);
  }

  /** Evidence rows students have filed against this badge's certifications,
   *  in any review state. NOT the number of students who earned the badge —
   *  see the module comment. */
  claimsFor(badgeCode: string): number {
    let claims = 0;
    for (const certification of this.certificationsFor(badgeCode)) {
      claims += certification.claims;
    }
    return claims;
  }

  badgeIsAwarded(badge: Badge): boolean {
    return badge.category === READINESS_CATEGORY;
  }

  verifierLabel(badge: Badge): string {
    return this.badgeIsAwarded(badge) ? 'Award' : 'Mentor';
  }

  verifierExplanation(badge: Badge): string {
    if (this.badgeIsAwarded(badge)) {
      return 'Readiness badges refuse evidence — staff award them when the assessment thresholds are met.';
    }
    return 'A student files evidence against one of these certifications; a mentor or the Main Admin approves it.';
  }

  // --- the grid and the drawer --------------------------------------------

  setTab(tab: Tab): void {
    this.tab.set(tab);
    this.query.set('');
    this.formOpen.set(false);
    this.formError.set(null);
    if (tab === 'tracks' && this.tracks() === null && !this.tracksNeedQuestionBank()) {
      void this.loadTracks();
    }
    if (tab === 'stages' && this.stageRules() === null) {
      void this.loadStageRules();
    }
  }

  /** The tracks read failed; the tab offers the read again rather than sitting
   *  on a spinner. Real work, so it is a real button. */
  retryTracks(): void {
    void this.loadTracks();
  }

  selectBadge(badgeCode: string): void {
    const alreadyOpen = this.selectedBadgeCode() === badgeCode;
    this.selectedBadgeCode.set(alreadyOpen ? null : badgeCode);
  }

  closeDrawer(): void {
    this.selectedBadgeCode.set(null);
  }

  // --- the one write path: mapping a certification to a badge --------------

  openForm(badgeCode?: string): void {
    if (badgeCode !== undefined) this.formBadgeCode.set(badgeCode);
    if (!this.formBadgeCode() && this.badges().length) {
      this.formBadgeCode.set(this.badges()[0].code);
    }
    this.tab.set('badges');
    this.formOpen.set(true);
    this.formError.set(null);
  }

  closeForm(): void {
    this.formOpen.set(false);
    this.formError.set(null);
  }

  async addCertification(): Promise<void> {
    const name = this.formName().trim();
    if (!name) {
      this.formError.set('Give it a name first');
      return;
    }
    if (!this.formBadgeCode()) {
      this.formError.set('Pick the badge it counts towards');
      return;
    }
    const url = this.formUrl().trim();
    if (url && !/^https?:\/\//i.test(url)) {
      this.formError.set('The link must start with http:// or https://');
      return;
    }
    this.saving.set(true);
    this.formError.set(null);
    this.flash.set(null);
    try {
      const badge = this.formBadge();
      const response = await fetch(`${this.apiBase}/admin/approved-certifications`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          // The API requires a provider; "Unspecified" is what an empty field
          // honestly is, and it can be edited later.
          provider: this.formProvider().trim() || 'Unspecified',
          badge_code: this.formBadgeCode(),
          stage: badge?.stage ?? 'EXCEL',
          url: url || null,
          // BOTH OMITTED IS PROGRAMME-WIDE and the server derives the college
          // from the course, so only the course is sent — sending a college
          // beside it is the contradiction `_resolve_certification_scope`
          // answers with a 422.
          course_id: this.formCourseId() || null,
        }),
      });
      if (!response.ok) {
        this.formError.set(await this.detailOf(response, 'Could not add that certification.'));
        return;
      }
      const row = (await response.json()) as ApprovedCertification;
      this.certifications.update((rows) => [row, ...(rows ?? [])]);
      this.formName.set('');
      this.formProvider.set('');
      this.formUrl.set('');
      this.formOpen.set(false);
      this.selectedBadgeCode.set(row.badge_code);
      this.flash.set(`${row.name} now counts towards ${row.badge_name}.`);
    } catch {
      this.formError.set('Could not reach the server.');
    } finally {
      this.saving.set(false);
    }
  }

  /** Remove from the catalogue = deactivate. The row keeps its history. */
  async setCertificationActive(
    certification: ApprovedCertification,
    active: boolean,
  ): Promise<void> {
    this.busyCertificationId.set(certification.id);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(
        `${this.apiBase}/admin/approved-certifications/${certification.id}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: certification.name,
            provider: certification.provider,
            badge_code: certification.badge_code,
            evidence_type: certification.evidence_type,
            stage: certification.stage,
            duration_text: certification.duration_text,
            is_free: certification.is_free,
            url: certification.url,
            active,
            // THE SCOPE IS ECHOED BACK UNCHANGED. `ApprovedCertificationIn` is
            // a whole-row body, so omitting these two would rewrite the row as
            // programme-wide — a certification quietly widened to every
            // college by the button that was meant to retire it.
            college_id: certification.college_id,
            course_id: certification.course_id,
          }),
        },
      );
      if (!response.ok) {
        this.error.set(await this.detailOf(response, 'Could not update that certification.'));
        return;
      }
      const row = (await response.json()) as ApprovedCertification;
      this.certifications.update((rows) =>
        (rows ?? []).map((existing) => (existing.id === row.id ? row : existing)),
      );
      if (active) {
        this.flash.set(`${row.name} is back in the catalogue.`);
      } else {
        this.flash.set(`${row.name} is off the catalogue — evidence already filed keeps it.`);
      }
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.busyCertificationId.set(null);
    }
  }

  // --- loading -------------------------------------------------------------

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    this.loadFailed.set(false);
    try {
      const [subjectsResponse, certificationsResponse, badgesResponse] = await Promise.all([
        fetch(`${this.apiBase}/admin/catalogue`, { credentials: 'include' }),
        fetch(`${this.apiBase}/admin/approved-certifications`, { credentials: 'include' }),
        fetch(`${this.apiBase}/admin/badge-catalogue`, { credentials: 'include' }),
      ]);
      const certificationsRefused = certificationsResponse.status === 403;
      if (!subjectsResponse.ok || (!certificationsResponse.ok && !certificationsRefused)) {
        this.error.set('Could not load the catalogue.');
        this.loadFailed.set(true);
        return;
      }
      this.subjects.set((await subjectsResponse.json()) as Subject[]);
      if (certificationsRefused) {
        this.certificationsAreMainAdminOnly.set(true);
        this.certifications.set([]);
      } else {
        this.certifications.set((await certificationsResponse.json()) as ApprovedCertification[]);
      }
      if (badgesResponse.ok) {
        const badges = (await badgesResponse.json()) as Badge[];
        this.badges.set(badges);
        if (!this.formBadgeCode() && badges.length) this.formBadgeCode.set(badges[0].code);
      }
      // B13's flat course list. Not in the Promise.all above and never allowed
      // to fail the screen: the two scope pills, the copy picker and the stage
      // rules all need it, and none of them is worth blanking the subjects for.
      await this.loadCourses();
    } catch {
      this.error.set('Could not reach the server.');
      this.loadFailed.set(true);
    } finally {
      this.loading.set(false);
    }
  }

  /** The four tracks the mock interviewer runs. The endpoint belongs to the
   *  Question bank screen and asks for its capability, so a 403 here means
   *  "this account was not granted that function" — a note, not an error. */
  private async loadTracks(): Promise<void> {
    this.tracksFailed.set(false);
    this.error.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/interview-questions/tracks`, {
        credentials: 'include',
      });
      if (response.status === 403) {
        this.tracksNeedQuestionBank.set(true);
        return;
      }
      if (!response.ok) {
        this.error.set('Could not load the interview tracks.');
        this.tracksFailed.set(true);
        return;
      }
      this.tracks.set((await response.json()) as InterviewTrack[]);
    } catch {
      this.error.set('Could not reach the server.');
      this.tracksFailed.set(true);
    }
  }


  // =======================================================================
  // B13 — scope, stage rules, subject import and the catalogue copy
  // =======================================================================

  /** The colleges to offer in the College pill, learned from the course list
   *  rather than fetched: `GET /admin/colleges` is `admin.institution`, a
   *  different function from this screen's, and asking for it would 403 a
   *  granted holder out of a filter they can otherwise use. */
  readonly collegeOptions = computed(() => {
    const byId = new Map<string, string>();
    for (const course of this.courses() ?? []) {
      if (course.college_id) byId.set(course.college_id, course.college ?? course.college_id);
    }
    return [...byId].map(([id, name]) => ({ id, name }));
  });

  /** The courses the Course pill offers, narrowed by the College pill so the
   *  two cannot name a contradiction. */
  readonly courseOptions = computed(() => {
    const college = this.collegeFilter();
    return (this.courses() ?? []).filter((c) => !college || c.college_id === college);
  });

  collegeFilterLabel(): string {
    const id = this.collegeFilter();
    if (!id) return 'All colleges';
    return this.collegeOptions().find((c) => c.id === id)?.name ?? id;
  }

  courseFilterLabel(): string {
    const id = this.courseFilter();
    if (!id) return 'All courses';
    return (this.courses() ?? []).find((c) => c.id === id)?.code ?? id;
  }

  /** Changing the college clears a course under a different one, rather than
   *  leaving a pair that names two places at once. */
  setCollegeFilter(id: string): void {
    this.collegeFilter.set(id);
    const course = (this.courses() ?? []).find((c) => c.id === this.courseFilter());
    if (course && id && course.college_id !== id) this.courseFilter.set('');
  }

  /** Does this certification apply where the two pills are pointed?
   *
   *  A PROGRAMME-WIDE ROW ALWAYS DOES, which is the endpoint's own reading and
   *  the reading every pre-B13 row carries. Hiding those would empty the grid
   *  on the day somebody first picked a college. */
  private certificationInScope(row: ApprovedCertification): boolean {
    const course = this.courseFilter();
    const college = this.collegeFilter();
    if (!course && !college) return true;
    if (row.course_id === null && row.college_id === null) return true;
    if (course) return row.course_id === course;
    return row.college_id === college;
  }

  /** True when either pill is pointed somewhere — the grid says so in words
   *  above the table, because a narrowed list that looks like the whole list
   *  is how somebody concludes a certification was deleted. */
  readonly scopeIsNarrowed = computed(() => !!this.collegeFilter() || !!this.courseFilter());

  // ---- stage rules -------------------------------------------------------

  /** The rules for the course the tab has picked, in semester order. */
  readonly visibleStageRules = computed(() => {
    const course = this.stageRuleCourse();
    const rules = this.stageRules() ?? [];
    return rules
      .filter((rule) => !course || rule.course_id === course)
      .slice()
      .sort((a, b) => a.semester - b.semester);
  });

  courseLabel(courseId: string): string {
    const course = (this.courses() ?? []).find((c) => c.id === courseId);
    if (!course) return courseId;
    return course.college ? `${course.code} · ${course.college}` : course.code;
  }

  /** Upsert. The server keys on (course, semester) because
   *  `uq_stage_rule_course_semester` makes a second rule for one semester an
   *  IntegrityError, so editing semester 3 is the same request as creating it. */
  async saveStageRule(): Promise<void> {
    const course_id = this.stageRuleCourse();
    if (!course_id) {
      this.stageError.set('Pick the programme this rule is for.');
      return;
    }
    const semester = Number(this.stageRuleSemester());
    if (!Number.isInteger(semester) || semester < 1 || semester > 20) {
      this.stageError.set('The semester must be a whole number between 1 and 20.');
      return;
    }
    this.stageBusy.set(true);
    this.stageError.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/catalogue/stage-rules`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ course_id, semester, stage: this.stageRuleStage() }),
      });
      if (!response.ok) {
        this.stageError.set(await this.detailOf(response, 'Could not save that rule.'));
        return;
      }
      const saved = (await response.json()) as StageRule;
      this.stageRules.update((rules) => {
        const rest = (rules ?? []).filter(
          (rule) => !(rule.course_id === saved.course_id && rule.semester === saved.semester),
        );
        return [...rest, saved];
      });
      this.flash.set(
        `Semester ${saved.semester} of ${this.courseLabel(saved.course_id)} is ` +
          `${this.stageLabel(saved.stage)}.`,
      );
    } catch {
      this.stageError.set('Could not reach the server.');
    } finally {
      this.stageBusy.set(false);
    }
  }

  /** A real DELETE, not a deactivation: nothing hangs off a stage rule, and a
   *  promotion with no rule for that semester simply leaves the stage alone —
   *  which is the same thing as never having had one. */
  async deleteStageRule(rule: StageRule): Promise<void> {
    this.stageBusy.set(true);
    this.stageError.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/catalogue/stage-rules/${rule.id}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!response.ok) {
        this.stageError.set(await this.detailOf(response, 'Could not remove that rule.'));
        return;
      }
      this.stageRules.update((rules) => (rules ?? []).filter((r) => r.id !== rule.id));
      this.flash.set(`Semester ${rule.semester} no longer names a stage.`);
    } catch {
      this.stageError.set('Could not reach the server.');
    } finally {
      this.stageBusy.set(false);
    }
  }

  // ---- subject import ----------------------------------------------------

  toggleImport(): void {
    const opening = !this.importOpen();
    this.importOpen.set(opening);
    this.importError.set(null);
    if (!opening) {
      this.importResult.set(null);
      this.importFile = null;
      this.importFileName.set(null);
    }
  }

  pickImportFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.importFile = file;
    this.importFileName.set(file?.name ?? null);
    // A new file invalidates the report of the previous one. Leaving it on
    // screen would let somebody read "12 create, 0 error" about a sheet they
    // have already replaced and then press Import.
    this.importResult.set(null);
    this.importError.set(null);
  }

  /** DRY RUN FIRST, ALWAYS, and the server agrees: `dry_run` defaults to true
   *  on this endpoint alone, because its input is a file somebody exported
   *  from a spreadsheet and the commonest mistake is the wrong file. The
   *  "Import" button only appears once a dry run has reported. */
  async runImport(dryRun: boolean): Promise<void> {
    const file = this.importFile;
    if (!file) {
      this.importError.set('Choose a CSV first.');
      return;
    }
    this.importBusy.set(true);
    this.importError.set(null);
    this.flash.set(null);
    try {
      const body = new FormData();
      body.append('file', file);
      const response = await fetch(
        `${this.apiBase}/admin/catalogue/subjects/import?dry_run=${dryRun}`,
        { method: 'POST', credentials: 'include', body },
      );
      if (!response.ok) {
        this.importError.set(await this.detailOf(response, 'That file could not be read.'));
        return;
      }
      const result = (await response.json()) as SubjectImportResult;
      this.importResult.set(result);
      if (!result.dry_run) {
        this.flash.set(
          `${result.created} subject${result.created === 1 ? '' : 's'} added, ` +
            `${result.updated} updated.`,
        );
        await this.load();
      }
    } catch {
      this.importError.set('Could not reach the server.');
    } finally {
      this.importBusy.set(false);
    }
  }

  // ---- the catalogue copy ------------------------------------------------

  toggleCopy(): void {
    const opening = !this.copyOpen();
    this.copyOpen.set(opening);
    this.copyError.set(null);
    this.copyPreview.set(null);
    if (!opening) return;
    this.copyQuery.set('');
    if (this.courseFilter()) this.copyTo.set(this.courseFilter());
  }

  /** ONE FLAT SEARCHABLE LIST ACROSS COLLEGES (owner decision 7). The needle
   *  is matched against the code, the name, the department AND the college, so
   *  "MBA" and "BGSCET" both find the same row. */
  readonly copyCandidates = computed(() => {
    const needle = this.copyQuery().trim().toLowerCase();
    const rows = this.courses() ?? [];
    if (!needle) return rows;
    return rows.filter((course) =>
      `${course.code} ${course.name} ${course.department ?? ''} ${course.college ?? ''}`
        .toLowerCase()
        .includes(needle),
    );
  });

  copyCourseLine(course: CatalogueCourse): string {
    const where = [course.college, course.department].filter(Boolean).join(' · ');
    const counts =
      `${course.certifications} certification${course.certifications === 1 ? '' : 's'} · ` +
      `${course.badge_overrides} badge override${course.badge_overrides === 1 ? '' : 's'} · ` +
      `${course.stage_rules} stage rule${course.stage_rules === 1 ? '' : 's'}`;
    return where ? `${where} — ${counts}` : counts;
  }

  toggleCopyPart(key: string): void {
    this.copySelectedParts.update((parts) =>
      parts.includes(key) ? parts.filter((p) => p !== key) : [...parts, key],
    );
    // The preview counted the parts that were ticked when it ran, so it is no
    // longer about the request that would be sent.
    this.copyPreview.set(null);
  }

  copyPartIsOn(key: string): boolean {
    return this.copySelectedParts().includes(key);
  }

  readonly sourceCourse = computed(
    () => (this.courses() ?? []).find((c) => c.id === this.copyFrom()) ?? null,
  );

  readonly destinationCourse = computed(
    () => (this.courses() ?? []).find((c) => c.id === this.copyTo()) ?? null,
  );

  readonly canRunCopy = computed(
    () =>
      !!this.copyFrom() &&
      !!this.copyTo() &&
      this.copyFrom() !== this.copyTo() &&
      this.copySelectedParts().length > 0,
  );

  /** `?dry_run=true` answers with the same counts and writes nothing, because
   *  the useful moment to learn that eleven of twelve rows already exist is
   *  before the copy, not after it. The copy itself is ADDITIVE and never
   *  overwrites: a row the destination already has is skipped and counted. */
  async runCopy(dryRun: boolean): Promise<void> {
    if (!this.canRunCopy()) return;
    this.copyBusy.set(true);
    this.copyError.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/catalogue/copy?dry_run=${dryRun}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from_course: this.copyFrom(),
          to_course: this.copyTo(),
          parts: this.copySelectedParts(),
        }),
      });
      if (!response.ok) {
        this.copyError.set(await this.detailOf(response, 'That copy could not be made.'));
        return;
      }
      const result = (await response.json()) as CopyResult;
      this.copyPreview.set(result);
      if (!result.dry_run) {
        const copied = result.parts.reduce((total, part) => total + part.copied, 0);
        this.flash.set(
          `${copied} row${copied === 1 ? '' : 's'} copied into ${this.courseLabel(result.to_course)}.`,
        );
        await this.load();
        await this.loadStageRules();
      }
    } catch {
      this.copyError.set('Could not reach the server.');
    } finally {
      this.copyBusy.set(false);
    }
  }

  // ---- reads -------------------------------------------------------------

  /** The flat course list and the stage rules. Their own try/catch: both are
   *  `admin.catalogue`, the same key the screen opens on, so a failure here is
   *  a failure and not a refusal — but it must not blank the subjects and the
   *  badges, which loaded perfectly well. */
  private async loadCourses(): Promise<void> {
    try {
      const response = await fetch(`${this.apiBase}/admin/catalogue/courses`, {
        credentials: 'include',
      });
      if (!response.ok) return;
      this.courses.set((await response.json()) as CatalogueCourse[]);
    } catch {
      /* the pills stay on "All", and every control that needs a course says so */
    }
  }

  private async loadStageRules(): Promise<void> {
    try {
      const response = await fetch(`${this.apiBase}/admin/catalogue/stage-rules`, {
        credentials: 'include',
      });
      if (!response.ok) return;
      this.stageRules.set((await response.json()) as StageRule[]);
    } catch {
      /* the tab distinguishes "not read" from "none on record" */
    }
  }

  // --- helpers -------------------------------------------------------------

  private badgeMatches(badge: Badge, needle: string): boolean {
    const badgeText = `${badge.code} ${badge.name} ${badge.category_label}`.toLowerCase();
    if (badgeText.includes(needle)) return true;
    for (const certification of this.certificationsFor(badge.code)) {
      const certificationText = `${certification.name} ${certification.provider}`.toLowerCase();
      if (certificationText.includes(needle)) return true;
    }
    return false;
  }

  /** The server's own sentence where there is one. FastAPI answers a 422 with
   *  `detail` as a LIST, which renders as "[object Object]" if it is read
   *  straight onto the screen. */
  private async detailOf(response: Response, fallback: string): Promise<string> {
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail) && detail.length && typeof detail[0]?.msg === 'string') {
        return detail[0].msg;
      }
    } catch {
      /* fall through to the fallback sentence */
    }
    return fallback;
  }
}
