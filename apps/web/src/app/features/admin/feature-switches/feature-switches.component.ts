/**
 * Student feature switches — the Main Admin's student-facing switches.
 *
 * Board: docs/redesign-2026-09/design/admin/FeatureSwitches.html
 * Spec:  docs/redesign-2026-09/02-admin-console-spec.md §20
 *
 * ITS OWN SCREEN, NOT A TAB ON GOVERNANCE. Roles & functions answers "who may
 * SEE a console screen"; this one answers "what is a STUDENT shown". The two
 * instruments have opposite defaults — a capability is denied past the role
 * baseline, a feature is allowed until somebody switches it off — and one
 * screen carrying both puts "grant" and "take away" in the same place.
 *
 * THE ENFORCEMENT COLUMN IS THE POINT OF THIS SCREEN, and B2.2 made it the
 * SERVER'S answer rather than this screen's guess. `GET /governance/catalogue`
 * now serves `enforced` per feature and every override row carries
 * `feature_enforced`, so the column is read straight off the payload. The Phase
 * 2 screen carried a hand-audited `SERVER_ENFORCED_FEATURE_KEYS` constant with a
 * comment saying it was right only on the day it was written; it is deleted, not
 * edited, because that was always its own terms. A switch the server never reads
 * is not a switch — it is a row in a table and a promise to a student that
 * nothing keeps — and rendering every key as "Server-enforced" because the column
 * looks better that way would let an admin switch the mock interviewer off for a
 * first-semester batch, watch the row save, and find sixty students sitting
 * interviews the next morning.
 *
 * SO THE SWITCH IS DISABLED PER ROW, ON THE SERVER'S OWN FACT. `PUT /features`
 * answers **422** for a feature whose `enforced` is false — in BOTH directions,
 * because an `enabled: true` row on an unwired key is equally a promise the API
 * does not keep — so the rule form is drawn disabled for exactly those rows and
 * says why. Not a phase constant: every feature in today's catalogue is
 * enforced, so a blanket "Available with Phase 3" would grey out eleven working
 * switches, and the day a twelfth key is added unwired the screen has to notice
 * without anybody editing it. REMOVE stays live on an unwired row — DELETE has
 * no enforcement check and a stale rule must always be clearable.
 *
 * THE STUDENT-FACING MESSAGE IS EDITABLE (B2.2). `student_message` is a column,
 * it is on `OverrideIn`/`OverrideOut`, and the router writes it onto the audit
 * trail beside the rule, because "what were they told" is the question somebody
 * asks afterwards. It is NOT `reason`: the reason is the office's note to itself
 * ("withheld pending the disciplinary meeting" is a true reason and not a
 * sentence to put on somebody's screen), the message is what the student reads.
 * Blank is a real choice — the feature is then simply absent from their console.
 *
 * THE COLLEGE SELECT IS A SCOPE PICKER AND STAYS DISABLED — see
 * `COLLEGE_PICKER_IS_THE_SERVERS` below for why that is not a stale phase badge.
 */

import { Component, ElementRef, computed, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { endOfLocalDay } from '../../../core/calendar-day';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';

// ---- exact snake_case shapes of the governance router's Out models ---------

interface FeatureOut {
  key: string;
  label: string;
  /** Does a router actually ask about this key? B2.2 made the catalogue answer
   *  it, which is what the Enforcement column reads and what decides whether
   *  the rule form is writable for this row. */
  enforced: boolean;
}

interface CatalogueOut {
  features: FeatureOut[];
  min_reason_chars: number;
}

interface OverrideOut {
  id: string;
  feature: string;
  feature_label: string;
  /** The same fact the catalogue serves, repeated on the row so a rule written
   *  before a key was un-wired still reads honestly without a second lookup. */
  feature_enforced: boolean;
  scope: string;
  target_id: string;
  target_label: string;
  enabled: boolean;
  reason: string;
  student_message: string | null;
  set_by: string | null;
  set_at: string;
  expires_at: string | null;
  students_affected: number;
}

interface HierarchyNode {
  scope: string;
  id: string;
  label: string;
  parent_id: string | null;
  students: number;
}

/** `GET /api/admin/students?q=` — only the four fields the student picker needs.
 *  A STUDENT-scope rule hangs on `student_id`, which is what this returns. */
interface AdminStudentOut {
  student_id: string;
  name: string;
  usn: string | null;
  batch: string | null;
}

// ---- what the template reads ----------------------------------------------

/** One rule on one feature, with every cell already a sentence. */
interface OverrideRule {
  id: string;
  scope: string;
  scopeLabel: string;
  targetId: string;
  targetLabel: string;
  isSwitchedOff: boolean;
  valueLabel: string;
  studentsAffected: number;
  studentsLabel: string;
  untilLabel: string;
  expiryDate: string;
  /** The API lists EVERY row, expired ones included; `feature_enabled` ignores
   *  the expired ones. A lapsed rule drawn like a live one is this screen
   *  telling the office the Agent is still off for a batch that got it back in
   *  June. It stays in the list — it is the audit trail — and says so. */
  hasLapsed: boolean;
  setByLabel: string;
  changedLabel: string;
  reason: string;
  /** What the student is shown when this rule refuses them. `null` is a real
   *  answer and renders as its own sentence, never as an empty quote: the
   *  feature is simply absent from their console and nobody is told why. */
  studentMessage: string | null;
}

/** One row of the switches grid — one per feature the catalogue serves, always
 *  all of them. The count is the API's, not a number written down here: B2.2
 *  added `student.mentor_log` to a list this screen used to call "the ten". */
interface FeatureRow {
  key: string;
  label: string;
  ruleCount: number;
  scopeSummary: string;
  valueLabel: string;
  valueIsSwitchedOff: boolean;
  valueVaries: boolean;
  isServerEnforced: boolean;
  enforcementLabel: string;
  changedLabel: string;
  rules: OverrideRule[];
}

type ScreenState = 'loading' | 'ready' | 'error';
type EnforcementFilter = 'all' | 'enforced' | 'unwired';

/** Every feature is ON until a rule switches it off — the model's own default
 *  (`feature_enabled` returns True when no override covers the student). */
const DEFAULT_VALUE_LABEL = 'On';

/**
 * WHY THE COLLEGE SELECT IS STILL GREY, AND WHY IT IS NOT A PHASE BADGE.
 *
 * It was drawn through `PendingControlDirective` as "Available with Phase 3".
 * Phase 3 has landed and it is still grey, so that sentence is now a claim
 * about a release rather than a fact about a control, and the honest reason has
 * to replace it: THE SERVER DECIDES REACH (`B1.4`). `GET /governance/features`,
 * `/catalogue` and `/hierarchy` take no college parameter — they answer with
 * whatever the caller's own grants reach — so there is nothing for this control
 * to send, and posting a college the API would ignore is the dead-control
 * failure `PendingControlDirective` exists to prevent.
 *
 * Nor can it honestly become a client-side filter over the rows already
 * fetched. Every institutional rung resolves up to a college through
 * `hierarchy.parent_id`, but a STUDENT-scope rule does not: the hierarchy
 * endpoint does not list students, so a student-level rule has no college this
 * screen can name — and those are the most specific rules on the board, the
 * ones that beat every other. A filter that silently dropped them, or silently
 * kept them under every college, would be worse than one that says plainly that
 * the list is not narrowed by college.
 */
const COLLEGE_PICKER_IS_THE_SERVERS =
  'Every college your account reaches is shown. Which colleges those are is decided by your ' +
  'governance grants, not chosen here.';

/** The rungs `GET /admin/governance/hierarchy` serves, in resolution order,
 *  most general first. STUDENT is a real scope on the API and is NOT here:
 *  the hierarchy endpoint does not list students, so that one target is picked
 *  by searching the roster instead. */
const HIERARCHY_SCOPES = ['COLLEGE', 'DEPARTMENT', 'COURSE', 'SPECIALIZATION', 'COHORT'] as const;

/** The office's vocabulary, not the enum's: a COHORT is a Batch on every
 *  screen the placement cell reads. */
const SCOPE_LABELS: Record<string, string> = {
  COLLEGE: 'College',
  DEPARTMENT: 'Department',
  COURSE: 'Course',
  SPECIALIZATION: 'Specialization',
  COHORT: 'Batch',
  STUDENT: 'Student',
};

const ROWS_PER_PAGE_CHOICES = [10, 25, 50];
const DEFAULT_ROWS_PER_PAGE = 10;

/** Has this expiry already passed? `null` never lapses. */
function lapsedBy(isoTimestamp: string | null, nowMs: number): boolean {
  if (isoTimestamp === null || isoTimestamp === '') {
    return false;
  }
  const when = new Date(isoTimestamp);
  if (Number.isNaN(when.getTime())) {
    return false;
  }
  return when.getTime() <= nowMs;
}

const DAY_AND_MONTH = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short' });
const DAY_MONTH_YEAR = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
});

function dayAndMonthOf(isoTimestamp: string | null): string {
  if (isoTimestamp === null || isoTimestamp === '') {
    return '—';
  }
  const when = new Date(isoTimestamp);
  if (Number.isNaN(when.getTime())) {
    return '—';
  }
  return DAY_AND_MONTH.format(when);
}

function dayMonthYearOf(isoTimestamp: string | null): string {
  if (isoTimestamp === null || isoTimestamp === '') {
    return 'Until it is removed';
  }
  const when = new Date(isoTimestamp);
  if (Number.isNaN(when.getTime())) {
    return 'Until it is removed';
  }
  return DAY_MONTH_YEAR.format(when);
}

/** The `<input type="date">` value for an API timestamp: the LOCAL calendar
 *  day, matching `endOfLocalDay` and the day `dayMonthYearOf` prints.
 *  `toISOString().slice(0, 10)` here would hand the edit form a different day
 *  from the one the rule list shows, for the same row. */
function calendarDayOf(isoTimestamp: string | null): string {
  if (isoTimestamp === null || isoTimestamp === '') {
    return '';
  }
  const when = new Date(isoTimestamp);
  if (Number.isNaN(when.getTime())) {
    return '';
  }
  const month = `${when.getMonth() + 1}`.padStart(2, '0');
  const day = `${when.getDate()}`.padStart(2, '0');
  return `${when.getFullYear()}-${month}-${day}`;
}

function scopeLabelOf(scope: string): string {
  const known = SCOPE_LABELS[scope];
  if (known === undefined) {
    return scope;
  }
  return known;
}

@Component({
  selector: 'app-admin-feature-switches',
  standalone: true,
  // RouterLink is REQUIRED for the links back to Roles & functions and on to
  // the audit log: a routerLink in a standalone component that does not import
  // it is inert markup that renders and does nothing.
  imports: [RouterLink, PluralPipe],
  templateUrl: './feature-switches.component.html',
  styleUrl: './feature-switches.component.scss',
})
export class AdminFeatureSwitchesComponent {
  readonly collegePickerNote = COLLEGE_PICKER_IS_THE_SERVERS;
  readonly hierarchyScopes = HIERARCHY_SCOPES;
  readonly rowsPerPageChoices = ROWS_PER_PAGE_CHOICES;
  readonly defaultValueLabel = DEFAULT_VALUE_LABEL;

  readonly state = signal<ScreenState>('loading');
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly saving = signal(false);

  // ---- server data --------------------------------------------------------
  readonly features = signal<FeatureOut[]>([]);
  readonly overrides = signal<OverrideOut[]>([]);
  readonly hierarchy = signal<HierarchyNode[]>([]);
  readonly minReason = signal(20);
  /** When the rules list was read. Whether a rule has lapsed is a comparison
   *  against a clock, and a `computed` that read `Date.now()` directly would be
   *  a pure function of nothing — right once and then frozen. Stamped on every
   *  load and reload instead. */
  readonly asOf = signal(Date.now());

  private readonly scopeSelect = viewChild<ElementRef<HTMLSelectElement>>('scopeSelect');

  // =========================================================================
  // the grid
  // =========================================================================

  readonly scopeFilter = signal('');
  readonly enforcementFilter = signal<EnforcementFilter>('all');
  readonly rowsPerPage = signal(DEFAULT_ROWS_PER_PAGE);
  readonly pageIndex = signal(0);
  readonly selectedFeatureKey = signal<string | null>(null);

  private readonly rulesByFeature = computed(() => {
    const byFeature = new Map<string, OverrideRule[]>();
    const now = this.asOf();
    for (const override of this.overrides()) {
      const rule: OverrideRule = {
        id: override.id,
        scope: override.scope,
        scopeLabel: scopeLabelOf(override.scope),
        targetId: override.target_id,
        targetLabel: override.target_label,
        isSwitchedOff: !override.enabled,
        valueLabel: override.enabled ? 'On' : 'Off',
        studentsAffected: override.students_affected,
        studentsLabel: plural(override.students_affected, 'student'),
        untilLabel: dayMonthYearOf(override.expires_at),
        expiryDate: calendarDayOf(override.expires_at),
        hasLapsed: lapsedBy(override.expires_at, now),
        setByLabel: override.set_by === null ? 'the office' : override.set_by,
        changedLabel: `${dayAndMonthOf(override.set_at)} · ${override.set_by === null ? 'the office' : override.set_by}`,
        reason: override.reason,
        studentMessage: override.student_message,
      };
      const already = byFeature.get(override.feature);
      if (already === undefined) {
        byFeature.set(override.feature, [rule]);
      } else {
        already.push(rule);
      }
    }
    return byFeature;
  });

  readonly featureRows = computed<FeatureRow[]>(() => {
    const byFeature = this.rulesByFeature();
    return this.features().map((feature) => {
      const rules = byFeature.get(feature.key) ?? [];
      // The grid's Default/Scope/Value columns answer "what does a student get
      // TODAY", which is what `feature_enabled` answers: it skips every expired
      // row. Summarising all of them instead would report a batch as switched
      // off months after its rule lapsed.
      const live = rules.filter((rule) => !rule.hasLapsed);
      return {
        key: feature.key,
        label: feature.label,
        ruleCount: rules.length,
        scopeSummary: this.scopeSummaryOf(live),
        valueLabel: this.valueLabelOf(live),
        valueIsSwitchedOff: live.length > 0 && live.every((rule) => rule.isSwitchedOff),
        valueVaries: this.valuesDisagreeIn(live),
        // The catalogue's own answer, never a list kept here. `enforced` is
        // also what `PUT /features` checks before it will accept a rule, so
        // this one field decides both the column and whether the form below is
        // writable — the screen and the API cannot disagree about a key.
        isServerEnforced: feature.enforced,
        enforcementLabel: feature.enforced
          ? 'Server-enforced'
          : 'Not wired yet · cannot be switched',
        changedLabel: this.changedLabelOf(rules),
        rules,
      };
    });
  });

  private scopeSummaryOf(rules: OverrideRule[]): string {
    if (rules.length === 0) {
      return '—';
    }
    if (rules.length === 1) {
      // The API's own count, on the row, because "Batch · 2026 MDM" does not
      // tell the office whether that is six students or eighty-six. Not summed
      // across several rules: overlapping rungs would count a student twice and
      // an invented total is worse than no total.
      return `${rules[0].scopeLabel} · ${rules[0].targetLabel} · ${rules[0].studentsLabel}`;
    }
    return plural(rules.length, 'rule');
  }

  /** Two rules that both say "off" do not make a feature vary; they make it
   *  off in two places. "Varies by scope" on a pair that agrees sends an admin
   *  hunting for a disagreement that is not there. */
  private valuesDisagreeIn(rules: OverrideRule[]): boolean {
    return rules.some((rule) => rule.isSwitchedOff) && rules.some((rule) => !rule.isSwitchedOff);
  }

  private valueLabelOf(rules: OverrideRule[]): string {
    if (rules.length === 0) {
      return `${DEFAULT_VALUE_LABEL} everywhere`;
    }
    if (this.valuesDisagreeIn(rules)) {
      return 'Varies by scope';
    }
    if (rules.every((rule) => rule.isSwitchedOff)) {
      return 'Off';
    }
    return 'On';
  }

  private changedLabelOf(rules: OverrideRule[]): string {
    if (rules.length === 0) {
      return '—';
    }
    return rules[0].changedLabel;
  }

  readonly filteredRows = computed(() => {
    const scope = this.scopeFilter();
    const enforcement = this.enforcementFilter();
    return this.featureRows().filter((row) => {
      if (enforcement === 'enforced' && !row.isServerEnforced) {
        return false;
      }
      if (enforcement === 'unwired' && row.isServerEnforced) {
        return false;
      }
      if (scope === '') {
        return true;
      }
      return row.rules.some((rule) => rule.scope === scope);
    });
  });

  readonly pageCount = computed(() => {
    const pages = Math.ceil(this.filteredRows().length / this.rowsPerPage());
    if (pages < 1) {
      return 1;
    }
    return pages;
  });

  readonly pagedRows = computed(() => {
    const start = this.pageIndex() * this.rowsPerPage();
    return this.filteredRows().slice(start, start + this.rowsPerPage());
  });

  readonly firstRowOnPage = computed(() => {
    if (this.filteredRows().length === 0) {
      return 0;
    }
    return this.pageIndex() * this.rowsPerPage() + 1;
  });

  readonly lastRowOnPage = computed(() =>
    Math.min((this.pageIndex() + 1) * this.rowsPerPage(), this.filteredRows().length),
  );

  /** Rules in force. An `enabled: true` row is a rule too — it is how a broader
   *  "off" is switched back on for one batch — so counting only the off ones
   *  under the words "rules in force" undercounts the governance in effect.
   *  What is excluded is the EXPIRED row, which is in force nowhere. */
  readonly rulesInForce = computed(() => {
    const now = this.asOf();
    return this.overrides().filter((override) => !lapsedBy(override.expires_at, now)).length;
  });

  readonly selectedRow = computed(() => {
    const key = this.selectedFeatureKey();
    if (key === null) {
      return null;
    }
    return this.featureRows().find((row) => row.key === key) ?? null;
  });

  readonly rulesForSelectedFeature = computed(() => {
    const row = this.selectedRow();
    if (row === null) {
      return [];
    }
    return row.rules;
  });

  readonly scopeFilterLabel = computed(() => {
    if (this.scopeFilter() === '') {
      return 'All';
    }
    return scopeLabelOf(this.scopeFilter());
  });

  readonly enforcementFilterLabel = computed(() => {
    if (this.enforcementFilter() === 'enforced') {
      return 'Server-enforced';
    }
    if (this.enforcementFilter() === 'unwired') {
      return 'Not wired yet';
    }
    return 'All';
  });

  /** The headline this screen exists to give: how many of the ten switches the
   *  API reads at all. */
  readonly enforcementSummary = computed(() => {
    const total = this.features().length;
    const enforced = this.featureRows().filter((row) => row.isServerEnforced).length;
    if (total === 0) {
      return 'No switches are defined.';
    }
    if (enforced === 0) {
      return `None of the ${plural(total, 'switch', 'switches')} is enforced by the API yet, so none of them can be set.`;
    }
    if (enforced === total) {
      return `All ${plural(total, 'switch is', 'switches are')} enforced by the API.`;
    }
    return `${enforced} of the ${total} switches are enforced by the API; the rest cannot be set.`;
  });

  /** The office's word for a rung: a COHORT is a Batch on every screen the
   *  placement cell reads. */
  scopeLabelFor(scope: string): string {
    return scopeLabelOf(scope);
  }

  isRowSelected(key: string): boolean {
    return this.selectedFeatureKey() === key;
  }

  selectFeature(key: string): void {
    this.selectedFeatureKey.set(key);
    this.clearForm();
    this.flash.set(null);
  }

  setScopeFilter(value: string): void {
    this.scopeFilter.set(value);
    this.pageIndex.set(0);
  }

  setEnforcementFilter(value: string): void {
    if (value === 'enforced' || value === 'unwired') {
      this.enforcementFilter.set(value);
    } else {
      this.enforcementFilter.set('all');
    }
    this.pageIndex.set(0);
  }

  setRowsPerPage(value: string): void {
    const size = Number(value);
    if (Number.isNaN(size) || size <= 0) {
      return;
    }
    this.rowsPerPage.set(size);
    this.pageIndex.set(0);
  }

  goToPage(index: number): void {
    const last = this.pageCount() - 1;
    if (index < 0 || index > last) {
      return;
    }
    this.pageIndex.set(index);
  }

  // =========================================================================
  // the override panel
  // =========================================================================

  readonly formScope = signal<string>('COHORT');
  readonly formTargetId = signal('');
  readonly formSwitchedOn = signal(false);
  readonly formExpiryDate = signal('');
  readonly formReason = signal('');
  /** The sentence the student reads at the refusal — audited beside the rule.
   *  Blank is sent as `null`, which is what the API means by "say nothing". */
  readonly formStudentMessage = signal('');
  /** The rule being edited, so the panel can say "replace" rather than "add"
   *  and offer Remove. A PUT upserts on (feature, scope, target), so editing a
   *  rule and writing a new one at the same target are the same request. */
  readonly editingRuleId = signal<string | null>(null);
  readonly removingRuleId = signal<string | null>(null);

  readonly studentQuery = signal('');
  readonly studentResults = signal<AdminStudentOut[]>([]);
  readonly studentSearchError = signal<string | null>(null);
  readonly searchingStudents = signal(false);

  readonly targetOptions = computed(() =>
    this.hierarchy().filter((node) => node.scope === this.formScope()),
  );

  readonly scopeIsStudent = computed(() => this.formScope() === 'STUDENT');

  readonly selectedTarget = computed(() => {
    const targetId = this.formTargetId();
    if (targetId === '') {
      return null;
    }
    return this.hierarchy().find((node) => node.id === targetId) ?? null;
  });

  /** The blast radius, in the same words the API answers with. A student-level
   *  rule reaches exactly the one student; an institutional one reaches the
   *  count the hierarchy endpoint counted. */
  readonly reachLabel = computed(() => {
    if (this.formTargetId() === '') {
      return 'Pick a scope target to see how many students this reaches.';
    }
    if (this.scopeIsStudent()) {
      return '1 student · resolves before every other rule';
    }
    const target = this.selectedTarget();
    if (target === null) {
      return 'That target is no longer in the hierarchy.';
    }
    return `${plural(target.students, 'student')} · ${this.resolutionNoteFor(target.scope)}`;
  });

  private resolutionNoteFor(scope: string): string {
    if (scope === 'COLLEGE') {
      return 'the broadest rung — every other rule beats it';
    }
    if (scope === 'COHORT') {
      return 'beaten only by a student-level rule';
    }
    return `beaten by any rule below ${scopeLabelOf(scope).toLowerCase()} level`;
  }

  readonly reasonIsTooShort = computed(() => this.formReason().trim().length < this.minReason());

  readonly canSave = computed(() => {
    if (this.selectedFeatureKey() === null) {
      return false;
    }
    // The API's 422, made visible before the request rather than after it.
    if (this.selectedFeatureIsUnwired()) {
      return false;
    }
    if (this.formTargetId() === '') {
      return false;
    }
    if (this.reasonIsTooShort()) {
      return false;
    }
    return !this.saving();
  });

  readonly saveBlockedReason = computed(() => {
    if (this.selectedFeatureKey() === null) {
      return 'Pick a feature in the table first.';
    }
    if (this.selectedFeatureIsUnwired()) {
      return 'No part of the API reads this switch, so it cannot be set.';
    }
    if (this.formTargetId() === '') {
      return 'Pick who this applies to.';
    }
    if (this.reasonIsTooShort()) {
      return `A reason of at least ${plural(this.minReason(), 'character')} is required.`;
    }
    return '';
  });

  /** The rule `editRule` loaded, while the form still points at ITS target. */
  private readonly editedRule = computed(() => {
    const id = this.editingRuleId();
    if (id === null) {
      return null;
    }
    return this.rulesForSelectedFeature().find((rule) => rule.id === id) ?? null;
  });

  /** A PUT upserts on (feature, scope, target) — so a save REPLACES the rule
   *  being edited only while the scope and target are still that rule's. Move
   *  the target and the same button writes a SECOND rule and leaves the first
   *  one standing, which is a defensible thing to do and an indefensible thing
   *  to call "Replace this rule". The heading follows the request that will
   *  actually be sent. */
  readonly editKeepsSameTarget = computed(() => {
    const rule = this.editedRule();
    if (rule === null) {
      return false;
    }
    return rule.scope === this.formScope() && rule.targetId === this.formTargetId();
  });

  readonly formHeading = computed(() =>
    this.editKeepsSameTarget() ? 'Replace this rule' : 'Add a rule',
  );

  /** Said out loud when an edit has been retargeted, because the original rule
   *  is about to be left behind. */
  readonly editRetargetedNote = computed(() => {
    const rule = this.editedRule();
    if (rule === null || this.editKeepsSameTarget()) {
      return '';
    }
    return `This writes a new rule. The one on ${rule.scopeLabel} · ${rule.targetLabel} stays until you remove it.`;
  });

  /** Does the selected key gate anything? `false` here is the server's own
   *  `enforced`, and it disables the rule form rather than merely warning under
   *  it: `PUT /features` answers 422 for such a key in BOTH directions, so a
   *  live-looking Save would post a request that can only be refused. REMOVE is
   *  deliberately NOT disabled by it — DELETE has no enforcement check, and a
   *  rule left over from when a key was wired must always be clearable. */
  readonly selectedFeatureIsUnwired = computed(() => {
    const row = this.selectedRow();
    if (row === null) {
      return false;
    }
    return !row.isServerEnforced;
  });

  startNewOverride(): void {
    this.clearForm();
    this.flash.set(null);
    if (this.selectedFeatureKey() === null && this.featureRows().length > 0) {
      this.selectedFeatureKey.set(this.featureRows()[0].key);
    }
    // The panel only exists once a feature is selected, and the line above may
    // have just selected the first one — so on the first press the view child
    // is still undefined and the focus was silently dropped, leaving a keyboard
    // user on the toolbar with a form they were never taken to. Focus after the
    // render that creates it. On an unwired feature the select is disabled and
    // this is a no-op, which is correct: there is nothing in the form to take
    // anyone to, and the panel's notice says why.
    setTimeout(() => this.scopeSelect()?.nativeElement.focus());
  }

  editRule(rule: OverrideRule): void {
    this.editingRuleId.set(rule.id);
    this.removingRuleId.set(null);
    this.formScope.set(rule.scope);
    this.formTargetId.set(rule.targetId);
    this.formSwitchedOn.set(!rule.isSwitchedOff);
    this.formExpiryDate.set(rule.expiryDate);
    this.formReason.set(rule.reason);
    this.formStudentMessage.set(rule.studentMessage ?? '');
    if (rule.scope === 'STUDENT') {
      this.studentResults.set([]);
      this.studentQuery.set(rule.targetLabel);
    }
  }

  setFormScope(value: string): void {
    this.formScope.set(value);
    this.formTargetId.set('');
    this.studentResults.set([]);
    this.studentQuery.set('');
    this.studentSearchError.set(null);
  }

  setFormValue(value: string): void {
    this.formSwitchedOn.set(value === 'on');
  }

  pickStudent(student: AdminStudentOut): void {
    this.formTargetId.set(student.student_id);
    this.studentQuery.set(this.studentLabelOf(student));
    this.studentResults.set([]);
  }

  studentLabelOf(student: AdminStudentOut): string {
    if (student.usn === null || student.usn === '') {
      return student.name;
    }
    return `${student.name} — ${student.usn}`;
  }

  async searchStudents(): Promise<void> {
    const needle = this.studentQuery().trim();
    this.studentSearchError.set(null);
    if (needle.length < 2) {
      this.studentResults.set([]);
      this.studentSearchError.set('Type at least two characters of a name, email or USN.');
      return;
    }
    this.searchingStudents.set(true);
    try {
      const found = await this.get<AdminStudentOut[]>(
        `/admin/students?q=${encodeURIComponent(needle)}`,
      );
      this.studentResults.set(found);
      if (found.length === 0) {
        this.studentSearchError.set('No student matches that.');
      }
    } catch {
      this.studentSearchError.set(
        'The roster could not be searched. It needs the Students & batches function.',
      );
    } finally {
      this.searchingStudents.set(false);
    }
  }

  private clearForm(): void {
    this.editingRuleId.set(null);
    this.removingRuleId.set(null);
    this.formScope.set('COHORT');
    this.formTargetId.set('');
    this.formSwitchedOn.set(false);
    this.formExpiryDate.set('');
    this.formReason.set('');
    this.formStudentMessage.set('');
    this.studentQuery.set('');
    this.studentResults.set([]);
    this.studentSearchError.set(null);
  }

  /** Blank is `null`, not `''`. The API's own reading of null is "the feature
   *  is simply absent from their console", and an empty string would store a
   *  message that says nothing and print as an empty quotation on the rule. */
  private studentMessageOrNull(): string | null {
    const text = this.formStudentMessage().trim();
    if (text === '') {
      return null;
    }
    return text;
  }

  /** A message on an `enabled: true` rule is stored and never read: nothing
   *  refuses the student, so there is no refusal to explain. Said out loud
   *  rather than silently dropped — dropping a sentence an admin typed is how
   *  they find out months later that nobody was ever told anything. */
  readonly studentMessageWillNotBeShown = computed(
    () => this.formSwitchedOn() && this.formStudentMessage().trim() !== '',
  );

  private expiresAtOrNull(): string | null {
    const day = this.formExpiryDate().trim();
    if (day === '') {
      return null;
    }
    return endOfLocalDay(day);
  }

  async saveOverride(): Promise<void> {
    const featureKey = this.selectedFeatureKey();
    if (featureKey === null || !this.canSave()) {
      return;
    }
    this.saving.set(true);
    const saved = await this.write<OverrideOut>('PUT', '/admin/governance/features', {
      feature: featureKey,
      scope: this.formScope(),
      target_id: this.formTargetId(),
      enabled: this.formSwitchedOn(),
      reason: this.formReason().trim(),
      expires_at: this.expiresAtOrNull(),
      student_message: this.studentMessageOrNull(),
    });
    this.saving.set(false);
    if (saved === null) {
      return;
    }
    const value = saved.enabled ? 'on' : 'off';
    this.flash.set(
      `${saved.feature_label} is ${value} for ${saved.target_label} — ${plural(saved.students_affected, 'student')}.`,
    );
    this.clearForm();
    await this.reloadOverrides();
  }

  async removeRule(rule: OverrideRule): Promise<void> {
    this.saving.set(true);
    const done = await this.write<null>(
      'DELETE',
      `/admin/governance/features/${rule.id}`,
      null,
    );
    this.saving.set(false);
    if (done === null) {
      return;
    }
    this.removingRuleId.set(null);
    this.flash.set(
      `Rule removed for ${rule.targetLabel}. The next rung up decides again for ${rule.studentsLabel}.`,
    );
    this.clearForm();
    await this.reloadOverrides();
  }

  constructor() {
    void this.load();
  }

  // =========================================================================
  // loading
  // =========================================================================

  private async load(): Promise<void> {
    this.state.set('loading');
    try {
      const [catalogue, overrides, hierarchy] = await Promise.all([
        this.get<CatalogueOut>('/admin/governance/catalogue'),
        this.get<OverrideOut[]>('/admin/governance/features'),
        this.get<HierarchyNode[]>('/admin/governance/hierarchy'),
      ]);
      this.asOf.set(Date.now());
      this.features.set(catalogue.features);
      this.minReason.set(catalogue.min_reason_chars);
      this.overrides.set(overrides);
      this.hierarchy.set(hierarchy);
      this.state.set('ready');
    } catch {
      this.error.set('Could not load the feature switches. Reload the page to try again.');
      this.state.set('error');
    }
  }

  private async reloadOverrides(): Promise<void> {
    try {
      const fresh = await this.get<OverrideOut[]>('/admin/governance/features');
      this.asOf.set(Date.now());
      this.overrides.set(fresh);
    } catch {
      this.error.set('The change was saved, but the list could not be reloaded. Reload the page.');
    }
  }

  // =========================================================================
  // http
  // =========================================================================

  private async get<T>(path: string): Promise<T> {
    const response = await fetch(`${environment.apiBase}${path}`, { credentials: 'include' });
    if (!response.ok) {
      throw new Error(`${path}: ${response.status}`);
    }
    return (await response.json()) as T;
  }

  private async write<T>(
    method: 'PUT' | 'DELETE',
    path: string,
    body: unknown,
  ): Promise<T | null> {
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}${path}`, {
        method,
        credentials: 'include',
        headers: body === null ? {} : { 'Content-Type': 'application/json' },
        ...(body === null ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return null;
      }
      if (response.status === 204) {
        return undefined as unknown as T;
      }
      return (await response.json()) as T;
    } catch {
      this.error.set('The server could not be reached. Nothing was changed.');
      return null;
    }
  }

  /** The server's own sentence where there is one — its refusals name the
   *  numbers ("at least 20 characters") and a generic message would throw that
   *  away. FastAPI answers a schema error with `detail` as a LIST, which
   *  rendered raw says "[object Object]". */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === 'string') {
        return detail;
      }
      if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === 'string') {
        return detail[0].msg;
      }
    } catch {
      /* fall through to the status */
    }
    return `The request was refused (${response.status}).`;
  }
}
