/**
 * Set up a college — `/admin/setup`.
 *
 * One guided screen for the fifteen forms the spine used to take: the college,
 * its departments, their courses, the optional specializations, and one batch
 * per leaf, typed as a list and written in one press. It sends exactly the
 * requests College structure sends (the same five POSTs, the same payloads),
 * so nothing here is a new way onto the roster — it is the existing way, in
 * order, with the office typing each thing once.
 *
 * IT CONTINUES A COLLEGE AS READILY AS IT STARTS ONE. Step 1 offers every
 * college on the deployment; picking one loads what it already has, shows
 * those rows locked, and lets the office add what is missing. That is the
 * case the owner named as the main problem: a college half set up, with no
 * screen that says what is left.
 *
 * "CREATE EVERYTHING" IS ADDITIVE AND RE-RUNNABLE, the seeder's rule
 * (app/seed_catalogue.py): a row that already exists — a 409 from the API —
 * is looked up by its code and used, never rewritten; a row whose parent
 * failed is marked as such and skipped; pressing the button again after a
 * failure resumes from what landed. Every row shows its own outcome and the
 * server's own words for a refusal.
 *
 * THE LEAF'S CODE DECIDES THE MOCK INTERVIEW, and this screen says so per
 * leaf, as a chip — "Mock interview: Financial Analytics" or "Mock interview:
 * general" — read from the same enabled-tracks list and the same case-folded
 * comparison `_default_track` applies. A code that matches nothing is
 * legitimate (a track can be added later on Interview questions and the match
 * starts working with no change here); it must simply never be a surprise.
 *
 * Nothing on this screen explains itself. A step is a noun, a field is a
 * label, a chip is a fact.
 */

import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import { composeBatchLabel } from '../../../core/batch-label';
import {
  MAX_SPAN_YEARS,
  endYearOptions,
  formatYearSpan,
  parseYearSpan,
  startYearOptions,
  type YearSpan,
} from '../../../core/batch-year';
import { PluralPipe } from '../../../shared/text/plural.pipe';
import {
  batchCode,
  batchDates,
  batchLabel,
  batchName,
  parseDomains,
  trackFor,
  type DegreeLevel,
} from './college-setup.model';

// ---- what the API answers (the same shapes College structure types) --------

interface CollegeOut {
  id: string;
  code: string;
  name: string;
  campus: string | null;
  contact: string | null;
  email_domains: string[];
}
interface DepartmentOut {
  id: string;
  code: string;
  name: string;
  head: string | null;
}
interface AcademicCourseOut {
  id: string;
  code: string;
  name: string;
  duration_months: number | null;
}
interface AcademicSpecializationOut {
  id: string;
  course_id: string;
  code: string;
  name: string;
}
interface AdminCohortOut {
  id: string;
  code: string;
  course_id: string | null;
  specialization_id: string | null;
}
interface AdminTrackOut {
  code: string;
  label: string;
  enabled: boolean;
}

// ---- the rows the office types ----------------------------------------------

interface DeptRow {
  readonly key: string;
  code: string;
  name: string;
  head: string;
  /** Set when the row came from the server; such a row is locked. */
  existingId: string | null;
}
interface CourseRow {
  readonly key: string;
  readonly deptKey: string;
  code: string;
  name: string;
  degree: DegreeLevel;
  years: number;
  existingId: string | null;
}
interface SpecRow {
  readonly key: string;
  readonly courseKey: string;
  code: string;
  name: string;
  existingId: string | null;
}

/** A place a batch can hang: a specialization, or a course with none. */
interface Leaf {
  readonly key: string;
  readonly dept: DeptRow;
  readonly course: CourseRow;
  readonly spec: SpecRow | null;
}

/** One batch as it will be written, with what the office may change. */
export interface BatchPlan {
  readonly leaf: Leaf;
  readonly label: string;
  readonly code: string;
  /** What lands in `cohorts.name`: the year, and only the year. */
  readonly name: string;
  /** What the office will READ on every screen once this batch exists — the
   *  spine composed back on from the links this leaf carries
   *  ("General MBA - Finance · 2026-28"). Shown here rather than `name` alone
   *  so "Create everything" previews the sentence, not the column. */
  readonly display: string;
  /** The label read back as two years, or null when it is not a span at all.
   *  That is what the two selects are bound to; null is a real answer and
   *  leaves them showing nothing rather than a year nobody picked. */
  readonly span: YearSpan | null;
  /** What each select offers for THIS leaf — the default window with the row's
   *  own years folded in, so a span outside it is still selectable. */
  readonly startYears: readonly number[];
  readonly endYears: readonly number[];
  readonly entry: string | null;
  readonly completion: string | null;
  readonly include: boolean;
  /** The college already has a batch on this leaf. */
  readonly alreadyThere: boolean;
  readonly track: string | null;
}

type Outcome =
  | { status: 'created' | 'existed' }
  | { status: 'failed'; detail: string }
  | { status: 'skipped'; detail: string };

const STEPS = [
  'College',
  'Departments',
  'Courses',
  'Specializations',
  'Batches',
  'Create',
] as const;
const LAST_STEP = STEPS.length;

let nextKey = 0;
const newKey = (): string => `n${++nextKey}`;

/** A programme length the span rules can express: at least a year, at most
 *  `MAX_SPAN_YEARS`. A course row loaded from the server carries whatever
 *  `duration_months` says, which is not bounded to either. */
const clampSpan = (years: number): number =>
  Math.min(MAX_SPAN_YEARS, Math.max(1, Math.round(years) || 1));

@Component({
  selector: 'app-admin-college-setup',
  standalone: true,
  imports: [NgTemplateOutlet, RouterLink, PluralPipe],
  templateUrl: './college-setup.component.html',
  styleUrl: './college-setup.component.scss',
})
export class AdminCollegeSetupComponent {
  private readonly auth = inject(AuthService);
  /** `?college=<id>`: the Colleges card's "Continue setup" names the college
   *  to load on step 1, exactly as picking it in the select would. */
  private readonly wantedCollegeId = inject(ActivatedRoute).snapshot.queryParamMap.get('college');
  /** `?step=<2..5>` with `?college=`: College structure's "Add a department" /
   *  "Add a course" / "Add a specialization" land on that step with the college
   *  loaded, so adding one row is not six presses of Continue. Ignored without
   *  a college, because step 1 is where a NEW college is typed. */
  private readonly wantedStep = Number(inject(ActivatedRoute).snapshot.queryParamMap.get('step'));

  readonly steps = STEPS;
  readonly lastStep = LAST_STEP;
  readonly step = signal(1);

  // ---- step 1: the college ---------------------------------------------------
  readonly colleges = signal<CollegeOut[]>([]);
  readonly collegesFailed = signal(false);
  readonly pickedCollegeId = signal('');
  readonly existingCollege = signal<CollegeOut | null>(null);
  readonly loadingExisting = signal(false);
  readonly code = signal('');
  readonly name = signal('');
  readonly campus = signal('');
  readonly contact = signal('');
  readonly domains = signal('');

  // ---- steps 2–4: the rows ---------------------------------------------------
  readonly departments = signal<DeptRow[]>([
    { key: newKey(), code: '', name: '', head: '', existingId: null },
  ]);
  readonly courses = signal<CourseRow[]>([]);
  readonly specs = signal<SpecRow[]>([]);

  // ---- step 5: the batches ---------------------------------------------------
  /** Read once: the dropdowns' window is the year the office opened the screen
   *  in, and must not move under them if the tab is left open past midnight on
   *  31 December. */
  private readonly thisYear = new Date().getFullYear();
  readonly startYear = signal(this.thisYear);
  /** Label and include-flag overrides, keyed by leaf. */
  private readonly labelOverride = signal<Record<string, string>>({});
  private readonly includeOverride = signal<Record<string, boolean>>({});
  private readonly existingCohorts = signal<AdminCohortOut[]>([]);
  private readonly tracks = signal<AdminTrackOut[]>([]);

  // ---- step 6: the run -------------------------------------------------------
  readonly runState = signal<'idle' | 'running' | 'done' | 'failed'>('idle');
  readonly outcomes = signal<Record<string, Outcome>>({});
  /** Server ids for every row that landed, so a re-run resumes. */
  private readonly resolved = new Map<string, string>();

  constructor() {
    void this.loadColleges();
    void this.loadTracks();
  }

  // ---- derived -----------------------------------------------------------------

  readonly collegeCode = computed(() => (this.existingCollege()?.code ?? this.code()).trim());
  readonly collegeName = computed(() => (this.existingCollege()?.name ?? this.name()).trim());

  readonly leaves = computed<Leaf[]>(() => {
    const out: Leaf[] = [];
    const deptByKey = new Map(this.departments().map((d) => [d.key, d]));
    for (const course of this.courses()) {
      const dept = deptByKey.get(course.deptKey);
      if (!dept) continue;
      const specs = this.specs().filter((s) => s.courseKey === course.key);
      if (specs.length === 0) out.push({ key: course.key, dept, course, spec: null });
      for (const spec of specs) out.push({ key: spec.key, dept, course, spec });
    }
    return out;
  });

  readonly batches = computed<BatchPlan[]>(() => {
    const labels = this.labelOverride();
    const includes = this.includeOverride();
    const cohorts = this.existingCohorts();
    const tracks = this.tracks();
    return this.leaves().map((leaf) => {
      const label = labels[leaf.key] ?? batchLabel(this.startYear(), leaf.course.years);
      const dates = batchDates(label);
      const span = parseYearSpan(label);
      const alreadyThere = cohorts.some((c) =>
        leaf.spec
          ? c.specialization_id !== null && c.specialization_id === leaf.spec.existingId
          : c.course_id !== null &&
            c.course_id === leaf.course.existingId &&
            c.specialization_id === null,
      );
      return {
        leaf,
        label,
        code: batchCode([
          this.collegeCode(),
          leaf.dept.code,
          leaf.course.code,
          leaf.spec?.code,
          label,
        ]),
        name: batchName(label),
        display: composeBatchLabel(
          leaf.course.name,
          leaf.spec?.name ?? null,
          batchName(label),
          label,
        ),
        span,
        startYears: startYearOptions(this.thisYear, span?.start ?? null),
        // No readable start means nothing to end: `endYearOptions` answers an
        // empty list for a start that is not a number, which leaves End with
        // nothing to offer until a start is picked rather than a guessed range.
        endYears: endYearOptions(span ? span.start : Number.NaN, span?.end ?? null),
        entry: dates?.entry ?? null,
        completion: dates?.completion ?? null,
        include: includes[leaf.key] ?? !alreadyThere,
        alreadyThere,
        track: trackFor(leaf.spec?.code ?? leaf.course.code, tracks),
      };
    });
  });

  /** The years "Starting year" offers. The year in force is folded in, so the
   *  select always has an option equal to the value it is showing. */
  readonly startYearChoices = computed(() => startYearOptions(this.thisYear, this.startYear()));

  readonly includedBatches = computed(() => this.batches().filter((b) => b.include));
  readonly newDepartments = computed(() => this.departments().filter((d) => !d.existingId));
  readonly newCourses = computed(() => this.courses().filter((c) => !c.existingId));
  readonly newSpecs = computed(() => this.specs().filter((s) => !s.existingId));

  /** Whether the current step's rows are complete enough to leave it. */
  readonly stepComplete = computed(() => {
    switch (this.step()) {
      case 1:
        return (
          this.existingCollege() !== null ||
          (this.code().trim() !== '' && this.name().trim() !== '')
        );
      case 2:
        return (
          this.departments().length > 0 &&
          this.departments().every((d) => d.code.trim() && d.name.trim())
        );
      case 3:
        return (
          this.courses().length > 0 &&
          this.courses().every(
            (c) => c.code.trim() && c.name.trim() && c.years >= 1 && c.years <= 6,
          )
        );
      case 4:
        return this.specs().every((s) => s.code.trim() && s.name.trim());
      case 5:
        return this.includedBatches().every((b) => b.entry !== null);
      default:
        return true;
    }
  });

  /** How many things "Create everything" will write. */
  readonly toWrite = computed(
    () =>
      (this.existingCollege() ? 0 : 1) +
      this.newDepartments().length +
      this.newCourses().length +
      this.newSpecs().length +
      this.includedBatches().length,
  );

  readonly failures = computed(
    () => Object.values(this.outcomes()).filter((o) => o.status === 'failed').length,
  );

  // ---- step 1 ------------------------------------------------------------------

  setCode(v: string): void {
    this.code.set(v);
  }
  setName(v: string): void {
    this.name.set(v);
  }
  setCampus(v: string): void {
    this.campus.set(v);
  }
  setContact(v: string): void {
    this.contact.set(v);
  }
  setDomains(v: string): void {
    this.domains.set(v);
  }

  async pickCollege(id: string): Promise<void> {
    this.pickedCollegeId.set(id);
    this.resolved.clear();
    this.outcomes.set({});
    this.runState.set('idle');
    if (!id) {
      this.existingCollege.set(null);
      this.existingCohorts.set([]);
      this.departments.set([{ key: newKey(), code: '', name: '', head: '', existingId: null }]);
      this.courses.set([]);
      this.specs.set([]);
      return;
    }
    const college = this.colleges().find((c) => c.id === id) ?? null;
    this.existingCollege.set(college);
    if (!college) return;
    this.loadingExisting.set(true);
    try {
      const depts =
        (await this.get<DepartmentOut[]>(`/admin/colleges/${college.id}/departments`)) ?? [];
      const deptRows: DeptRow[] = depts.map((d) => ({
        key: d.id,
        code: d.code,
        name: d.name,
        head: d.head ?? '',
        existingId: d.id,
      }));
      const courseRows: CourseRow[] = [];
      const specRows: SpecRow[] = [];
      const cohorts: AdminCohortOut[] = [];
      for (const dept of depts) {
        const [courses, batches] = await Promise.all([
          this.get<AcademicCourseOut[]>(`/admin/departments/${dept.id}/academic-courses`),
          this.get<AdminCohortOut[]>(`/admin/departments/${dept.id}/cohorts`),
        ]);
        cohorts.push(...(batches ?? []));
        for (const course of courses ?? []) {
          courseRows.push({
            key: course.id,
            deptKey: dept.id,
            code: course.code,
            name: course.name,
            degree: 'PG',
            years: Math.max(1, Math.round((course.duration_months ?? 24) / 12)),
            existingId: course.id,
          });
          const specs = await this.get<AcademicSpecializationOut[]>(
            `/admin/academic-courses/${course.id}/academic-specializations`,
          );
          for (const spec of specs ?? []) {
            specRows.push({
              key: spec.id,
              courseKey: course.id,
              code: spec.code,
              name: spec.name,
              existingId: spec.id,
            });
          }
        }
      }
      this.departments.set(
        deptRows.length
          ? deptRows
          : [{ key: newKey(), code: '', name: '', head: '', existingId: null }],
      );
      this.courses.set(courseRows);
      this.specs.set(specRows);
      this.existingCohorts.set(cohorts);
    } finally {
      this.loadingExisting.set(false);
    }
  }

  // ---- steps 2–4: row editing --------------------------------------------------

  addDepartment(): void {
    this.departments.update((rows) => [
      ...rows,
      { key: newKey(), code: '', name: '', head: '', existingId: null },
    ]);
  }
  removeDepartment(key: string): void {
    this.departments.update((rows) => rows.filter((r) => r.key !== key));
    this.courses.update((rows) => rows.filter((r) => r.deptKey !== key));
  }
  setDepartment(key: string, field: 'code' | 'name' | 'head', value: string): void {
    this.departments.update((rows) =>
      rows.map((r) => (r.key === key ? { ...r, [field]: value } : r)),
    );
  }

  coursesOf(deptKey: string): CourseRow[] {
    return this.courses().filter((c) => c.deptKey === deptKey);
  }
  addCourse(deptKey: string): void {
    this.courses.update((rows) => [
      ...rows,
      { key: newKey(), deptKey, code: '', name: '', degree: 'PG', years: 2, existingId: null },
    ]);
  }
  removeCourse(key: string): void {
    this.courses.update((rows) => rows.filter((r) => r.key !== key));
    this.specs.update((rows) => rows.filter((r) => r.courseKey !== key));
  }
  setCourse(key: string, field: 'code' | 'name', value: string): void {
    this.courses.update((rows) => rows.map((r) => (r.key === key ? { ...r, [field]: value } : r)));
  }
  setCourseDegree(key: string, value: string): void {
    const degree: DegreeLevel = value === 'UG' ? 'UG' : 'PG';
    this.courses.update((rows) => rows.map((r) => (r.key === key ? { ...r, degree } : r)));
  }
  setCourseYears(key: string, value: string): void {
    const years = Math.min(6, Math.max(1, Math.round(Number(value) || 0)));
    this.courses.update((rows) => rows.map((r) => (r.key === key ? { ...r, years } : r)));
  }

  specsOf(courseKey: string): SpecRow[] {
    return this.specs().filter((s) => s.courseKey === courseKey);
  }
  addSpec(courseKey: string): void {
    this.specs.update((rows) => [
      ...rows,
      { key: newKey(), courseKey, code: '', name: '', existingId: null },
    ]);
  }
  removeSpec(key: string): void {
    this.specs.update((rows) => rows.filter((r) => r.key !== key));
  }
  setSpec(key: string, field: 'code' | 'name', value: string): void {
    this.specs.update((rows) => rows.map((r) => (r.key === key ? { ...r, [field]: value } : r)));
  }

  // ---- step 5 ------------------------------------------------------------------
  //
  // A BATCH IS A YEAR SPAN, SO IT IS PICKED AND NOT TYPED. The two selects are
  // the whole of what a batch is called; `labelOverride` still stores the LABEL
  // ("2026-28") because that is what `cohorts.batch_label` takes and what every
  // derivation below it — the dates, the code, the name, the plan — already
  // reads. The selects are its two halves, composed by `formatYearSpan` and
  // read back by `parseYearSpan`, so the malformed span is unrepresentable
  // rather than merely discouraged (core/batch-year.ts has the long version).

  setStartYear(value: string): void {
    const year = Number(value);
    if (Number.isInteger(year) && year >= 2000 && year <= 2100) {
      this.startYear.set(year);
      this.labelOverride.set({});
    }
  }

  /** A leaf's own start year. Moving the intake keeps the programme's LENGTH,
   *  so a two-year MBA moved from 2026 to 2027 becomes 2027-29 and not
   *  2027-28; a leaf whose label is not a span at all falls back to the
   *  course's own duration, the number `batchLabel` used for the default. */
  setBatchStartYear(leafKey: string, value: string): void {
    const row = this.batches().find((b) => b.leaf.key === leafKey);
    const start = Number(value);
    // Only a year this row actually offered — which is also what refuses the
    // placeholder option a leaf carries while its span is unreadable.
    if (!row || !row.startYears.includes(start)) return;
    const wanted = row.span ? row.span.end - row.span.start : row.leaf.course.years;
    // A course whose stored duration runs longer than a span is allowed to
    // cannot be written down at all; taking the ceiling leaves the office a
    // batch they can then correct on the End select, rather than two dropdowns
    // with no way out of a label nothing can parse.
    this.setBatchLabel(leafKey, formatYearSpan(start, start + clampSpan(wanted)));
  }

  setBatchEndYear(leafKey: string, value: string): void {
    const row = this.batches().find((b) => b.leaf.key === leafKey);
    const end = Number(value);
    if (!row?.span || !row.endYears.includes(end)) return;
    this.setBatchLabel(leafKey, formatYearSpan(row.span.start, end));
  }

  private setBatchLabel(leafKey: string, value: string): void {
    this.labelOverride.update((m) => ({ ...m, [leafKey]: value }));
  }
  setBatchIncluded(leafKey: string, include: boolean): void {
    this.includeOverride.update((m) => ({ ...m, [leafKey]: include }));
  }

  // ---- moving through the steps ---------------------------------------------

  goBack(): void {
    this.step.update((s) => Math.max(1, s - 1));
  }
  goNext(): void {
    if (!this.stepComplete()) return;
    this.step.update((s) => Math.min(LAST_STEP, s + 1));
  }
  goTo(step: number): void {
    if (step < this.step()) this.step.set(step);
  }

  outcome(key: string): Outcome | null {
    return this.outcomes()[key] ?? null;
  }

  startAnother(): void {
    void this.pickCollege('');
    this.code.set('');
    this.name.set('');
    this.campus.set('');
    this.contact.set('');
    this.domains.set('');
    this.labelOverride.set({});
    this.includeOverride.set({});
    this.step.set(1);
    void this.loadColleges();
  }

  // ---- step 6: create everything ----------------------------------------------

  async createEverything(): Promise<void> {
    if (this.runState() === 'running') return;
    this.runState.set('running');
    this.outcomes.set({});

    const collegeId = await this.ensureCollege();
    if (!collegeId) {
      this.runState.set('failed');
      return;
    }

    for (const dept of this.departments()) {
      await this.ensureDepartment(collegeId, dept);
    }
    for (const course of this.courses()) {
      const deptId = this.resolved.get(course.deptKey);
      if (!deptId) {
        this.record(course.key, { status: 'skipped', detail: 'Its department was not created.' });
        continue;
      }
      await this.ensureCourse(deptId, course);
    }
    for (const spec of this.specs()) {
      const courseId = this.resolved.get(spec.courseKey);
      if (!courseId) {
        this.record(spec.key, { status: 'skipped', detail: 'Its course was not created.' });
        continue;
      }
      await this.ensureSpec(courseId, spec);
    }
    for (const batch of this.includedBatches()) {
      await this.ensureBatch(batch);
    }
    this.runState.set(this.failures() > 0 ? 'failed' : 'done');
    void this.loadColleges();
  }

  private record(key: string, outcome: Outcome): void {
    this.outcomes.update((m) => ({ ...m, [key]: outcome }));
  }

  private async ensureCollege(): Promise<string | null> {
    const existing = this.existingCollege();
    if (existing) {
      this.resolved.set('college', existing.id);
      this.record('college', { status: 'existed' });
      return existing.id;
    }
    // Written by an earlier press: the plan says so, as it does for every
    // other row the re-run reuses, rather than falling back to "New".
    const known = this.resolved.get('college');
    if (known) {
      this.record('college', { status: 'existed' });
      return known;
    }
    const body = {
      code: this.code().trim(),
      name: this.name().trim(),
      campus: this.campus().trim() || null,
      contact: this.contact().trim() || null,
      email_domains: parseDomains(this.domains()),
    };
    const res = await this.post<CollegeOut>('/admin/colleges', body);
    if (res.ok) {
      this.resolved.set('college', res.body.id);
      this.record('college', { status: 'created' });
      return res.body.id;
    }
    if (res.status === 409) {
      const list = (await this.get<CollegeOut[]>('/admin/colleges')) ?? [];
      const hit = list.find((c) => c.code.trim().toUpperCase() === body.code.toUpperCase());
      if (hit) {
        this.resolved.set('college', hit.id);
        this.record('college', { status: 'existed' });
        return hit.id;
      }
    }
    this.record('college', { status: 'failed', detail: res.detail });
    return null;
  }

  private async ensureDepartment(collegeId: string, dept: DeptRow): Promise<void> {
    if (dept.existingId || this.resolved.has(dept.key)) {
      this.resolved.set(dept.key, dept.existingId ?? this.resolved.get(dept.key)!);
      this.record(dept.key, { status: 'existed' });
      return;
    }
    const body = { code: dept.code.trim(), name: dept.name.trim(), head: dept.head.trim() || null };
    const res = await this.post<DepartmentOut>(`/admin/colleges/${collegeId}/departments`, body);
    if (res.ok) {
      this.resolved.set(dept.key, res.body.id);
      this.record(dept.key, { status: 'created' });
      return;
    }
    if (res.status === 409) {
      const list =
        (await this.get<DepartmentOut[]>(`/admin/colleges/${collegeId}/departments`)) ?? [];
      const hit = list.find((d) => d.code.trim().toUpperCase() === body.code.toUpperCase());
      if (hit) {
        this.resolved.set(dept.key, hit.id);
        this.record(dept.key, { status: 'existed' });
        return;
      }
    }
    this.record(dept.key, { status: 'failed', detail: res.detail });
  }

  private async ensureCourse(deptId: string, course: CourseRow): Promise<void> {
    if (course.existingId || this.resolved.has(course.key)) {
      this.resolved.set(course.key, course.existingId ?? this.resolved.get(course.key)!);
      this.record(course.key, { status: 'existed' });
      return;
    }
    const body = {
      code: course.code.trim(),
      name: course.name.trim(),
      duration_months: course.years * 12,
    };
    const res = await this.post<AcademicCourseOut>(
      `/admin/departments/${deptId}/academic-courses`,
      body,
    );
    if (res.ok) {
      this.resolved.set(course.key, res.body.id);
      this.record(course.key, { status: 'created' });
      return;
    }
    if (res.status === 409) {
      const list =
        (await this.get<AcademicCourseOut[]>(`/admin/departments/${deptId}/academic-courses`)) ??
        [];
      const hit = list.find((c) => c.code.trim().toUpperCase() === body.code.toUpperCase());
      if (hit) {
        this.resolved.set(course.key, hit.id);
        this.record(course.key, { status: 'existed' });
        return;
      }
    }
    this.record(course.key, { status: 'failed', detail: res.detail });
  }

  private async ensureSpec(courseId: string, spec: SpecRow): Promise<void> {
    if (spec.existingId || this.resolved.has(spec.key)) {
      this.resolved.set(spec.key, spec.existingId ?? this.resolved.get(spec.key)!);
      this.record(spec.key, { status: 'existed' });
      return;
    }
    const body = { code: spec.code.trim(), name: spec.name.trim() };
    const res = await this.post<AcademicSpecializationOut>(
      `/admin/academic-courses/${courseId}/academic-specializations`,
      body,
    );
    if (res.ok) {
      this.resolved.set(spec.key, res.body.id);
      this.record(spec.key, { status: 'created' });
      return;
    }
    if (res.status === 409) {
      const list =
        (await this.get<AcademicSpecializationOut[]>(
          `/admin/academic-courses/${courseId}/academic-specializations`,
        )) ?? [];
      const hit = list.find((s) => s.code.trim().toUpperCase() === body.code.toUpperCase());
      if (hit) {
        this.resolved.set(spec.key, hit.id);
        this.record(spec.key, { status: 'existed' });
        return;
      }
    }
    this.record(spec.key, { status: 'failed', detail: res.detail });
  }

  private async ensureBatch(batch: BatchPlan): Promise<void> {
    const key = `batch:${batch.leaf.key}`;
    if (this.resolved.has(key)) {
      this.record(key, { status: 'existed' });
      return;
    }
    const deptId = this.resolved.get(batch.leaf.dept.key);
    const courseId = this.resolved.get(batch.leaf.course.key);
    const specId = batch.leaf.spec ? this.resolved.get(batch.leaf.spec.key) : null;
    if (!deptId || !courseId || (batch.leaf.spec && !specId) || !batch.entry || !batch.completion) {
      this.record(key, { status: 'skipped', detail: 'What it belongs to was not created.' });
      return;
    }
    // The deepest level only; the API derives the ancestors (_resolve_ancestry).
    const body = {
      code: batch.code,
      name: batch.name,
      batch_label: batch.label.trim(),
      degree_level: batch.leaf.course.degree,
      entry_date: batch.entry,
      expected_completion: batch.completion,
      course_id: courseId,
      specialization_id: specId ?? null,
    };
    const res = await this.post<AdminCohortOut>(`/admin/departments/${deptId}/cohorts`, body);
    if (res.ok) {
      this.resolved.set(key, res.body.id);
      this.record(key, { status: 'created' });
      return;
    }
    if (res.status === 409) {
      this.resolved.set(key, 'existing');
      this.record(key, { status: 'existed' });
      return;
    }
    this.record(key, { status: 'failed', detail: res.detail });
  }

  // ---- I/O ---------------------------------------------------------------------

  private async loadColleges(): Promise<void> {
    const list = await this.get<CollegeOut[]>('/admin/colleges');
    this.collegesFailed.set(list === null);
    this.colleges.set(list ?? []);
    const wanted = this.wantedCollegeId;
    if (wanted && !this.pickedCollegeId() && (list ?? []).some((c) => c.id === wanted)) {
      await this.pickCollege(wanted);
      if (this.wantedStep >= 2 && this.wantedStep < LAST_STEP) this.step.set(this.wantedStep);
    }
  }

  /** Best-effort: a session without the question-bank key sees no chips. */
  private async loadTracks(): Promise<void> {
    if (!this.auth.session()?.capabilities?.includes('admin.interview_questions')) return;
    const list = await this.get<AdminTrackOut[]>('/admin/interview-questions/tracks');
    this.tracks.set(list ?? []);
  }

  private async get<T>(path: string): Promise<T | null> {
    try {
      const res = await fetch(`${environment.apiBase}${path}`, { credentials: 'include' });
      if (!res.ok) return null;
      return (await res.json()) as T;
    } catch {
      return null;
    }
  }

  private async post<T>(
    path: string,
    body: unknown,
  ): Promise<{ ok: true; body: T } | { ok: false; status: number; detail: string }> {
    try {
      const res = await fetch(`${environment.apiBase}${path}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) return { ok: true, body: (await res.json()) as T };
      return { ok: false, status: res.status, detail: await this.detailOf(res) };
    } catch {
      return { ok: false, status: 0, detail: 'Could not reach the server.' };
    }
  }

  /** The API's own sentence for a refusal; a 422's list of field errors is
   *  joined into one line. */
  private async detailOf(res: Response): Promise<string> {
    try {
      const body = (await res.json()) as { detail?: unknown };
      const detail = body.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail)) {
        return detail
          .map((d) =>
            typeof d === 'object' && d && 'msg' in d
              ? String((d as { msg: unknown }).msg)
              : String(d),
          )
          .join(' ');
      }
    } catch {
      // fall through
    }
    return `The server answered ${res.status}.`;
  }
}
