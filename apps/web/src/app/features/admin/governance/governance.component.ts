/**
 * Who can do what — the Main Admin's access screen, redesigned 2026-10-16.
 *
 * WHY IT WAS REDESIGNED. The owner's words: "access and grants is too
 * confusing". The 2026-09 board put a nine-column grid (two of the columns,
 * "Scope" and "Reach", answering questions with near-identical names), a
 * 380px side panel that granted ONE function at a time from a dropdown with
 * optgroups the scope select beside it contradicted, a catalogue tab of
 * internal keys and "Server-enforced: Not reported here", and four filters,
 * all on one page. The office could not tell from it who could open what.
 *
 * THE SCREEN IS NOW THE THREE TASKS THE OFFICE ACTUALLY DOES, one tab each:
 *
 *   Give access      tick the people (or a group), tick every kind of access
 *                    they need, say which students it covers, until when and
 *                    why — ONE request, `POST /grants` with `capabilities`.
 *   Who has access   every grant, grouped by access type (or by person). Tick
 *                    the people in one access type and remove it from all of
 *                    them at once — ONE request, `POST /grants/revoke`.
 *   Needs attention  grants waiting for a second signature, and grants that
 *                    are ending or due a review.
 *
 * Groups keep their own tab. Student feature switches and the audit trail
 * stay screens of their own, linked from the header.
 *
 * WHAT DID NOT CHANGE, because each was right and is load-bearing:
 *  - Every list comes from the server. `/catalogue` serves the access types
 *    from the constants the API enforces; `/hierarchy` the students a grant
 *    can be narrowed to, with live counts.
 *  - A grant waiting for approval does NOTHING, and its row says so in words
 *    and in the risk tone; it is never painted as active (B2.4). The Main
 *    Admin's own grants are active at once; a deputy's personal-record grants
 *    wait (`initial_approval_state` on the server decides, and the flash
 *    reports what the server actually wrote).
 *  - Every write needs a reason past the server's floor; the button stays
 *    disabled and says why until it does, and the server's own refusal is
 *    what reaches the screen when it refuses anyway.
 *  - A typed date is the END of that day on this clock (`endOfLocalDay`), so
 *    the day that goes in is the day that is printed back.
 */

import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import { endOfLocalDay } from '../../../core/calendar-day';
import { GRANTABLE_ADMIN_SCREENS } from '../../../layout/app-shell.component';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';

// ---- exact snake_case shapes of the governance router's Out models ---------

/** A rung of the institutional spine — `ScopeLevel` in `models/governance.py`.
 *  A grant that covers every student hangs on NO rung: a null level and id. */
type ScopeLevel = 'COLLEGE' | 'DEPARTMENT' | 'COURSE' | 'SPECIALIZATION' | 'COHORT' | 'STUDENT';

interface CapabilityOut {
  key: string;
  label: string;
  scope: 'SCOPED' | 'PROGRAMME';
  carries_pii: boolean;
}

interface CatalogueOut {
  capabilities: CapabilityOut[];
  min_reason_chars: number;
}

/** One rung of `GET /hierarchy`, with its live student count. */
interface HierarchyNode {
  scope: ScopeLevel;
  id: string;
  label: string;
  parent_id: string | null;
  students: number;
}

interface StaffOut {
  user_id: string;
  name: string;
  email: string;
  role: string;
}

interface GrantOut {
  id: string;
  capability: string;
  capability_label: string;
  /** The access type's own kind, `SCOPED` (a faculty tool, narrowed to the
   *  holder's mentees) or `PROGRAMME` (an office screen). */
  scope: string;
  scope_level: ScopeLevel | null;
  scope_id: string | null;
  scope_label: string | null;
  carries_pii: boolean;
  approval_state: string;
  approved_by: string | null;
  approved_at: string | null;
  review_at: string | null;
  subject_kind: string;
  subject_id: string;
  subject_label: string;
  reason: string;
  granted_by: string | null;
  granted_at: string;
  expires_at: string | null;
}

interface RevokeManyOut {
  revoked: GrantOut[];
  already_revoked: string[];
}

/** `GET /review` — waiting for approval, and running out. Two lists because
 *  they need opposite actions. */
interface ReviewOut {
  horizon_days: number;
  pending: GrantOut[];
  expiring: GrantOut[];
}

interface GroupMemberOut {
  user_id: string;
  name: string;
  email: string;
}

interface GroupOut {
  id: string;
  name: string;
  description: string | null;
  members: GroupMemberOut[];
  capabilities: string[];
}

// ---- what the template reads ----------------------------------------------

/** One grant, with every cell already a sentence. */
export interface GrantRow {
  id: string;
  functionKey: string;
  functionLabel: string;
  /** `USER:<id>` or `GROUP:<id>` — what "by person" groups on. */
  subjectKey: string;
  subjectLabel: string;
  subjectInitials: string;
  subjectKindLabel: string;
  /** Which students it covers, in words: "All students", "Their mentees", or
   *  the rung it was narrowed to. */
  reachLabel: string;
  reachScopeId: string | null;
  grantedByLabel: string;
  expiresLabel: string;
  reviewLabel: string;
  statusLabel: string;
  statusTone: 'good' | 'warn' | 'risk';
  isPending: boolean;
  isExpiringSoon: boolean;
  isDueForReview: boolean;
  carriesPersonalData: boolean;
  reason: string;
  searchText: string;
}

/** A heading and the grants under it, on "Who has access". */
export interface HolderGroup {
  key: string;
  title: string;
  subtitle: string;
  rows: GrantRow[];
}

/** One tickable access type on "Give access". */
interface AccessChoice {
  key: string;
  label: string;
  /** The sidebar row it adds for a faculty member, where that differs. */
  opens: string | null;
  personal: boolean;
  /** How many people or groups hold it by a grant today. */
  holders: number;
}

interface AccessChoiceGroup {
  title: string;
  choices: AccessChoice[];
}

interface ScopeOption {
  key: string;
  label: string;
}

type ScreenState = 'loading' | 'ready' | 'error';
export type GovernanceTab = 'give' | 'holders' | 'attention' | 'groups';
type HolderView = 'access' | 'person';
type GrantSubjectMode = 'users' | 'groups';
type StatusFilter = 'all' | 'active' | 'ending' | 'pending';
/** The three lifecycle writes. They share one reason box. */
type GrantAction = 'approve' | 'extend' | 'revoke';

/** `APPROVAL_PENDING` in `models/governance.py`: listed, audited, holds
 *  nothing. The one state this screen must never render as active. */
const APPROVAL_PENDING = 'pending_approval';

/** The server owns the horizon (`/review`'s `horizon_days`); this is only what
 *  the rows use before that answer arrives. */
const EXPIRING_SOON_DAYS = 30;
const MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000;

/** The UI says "Course", "Specialization" and "Batch" (AGENTS.md). */
const RUNG_LABEL: Record<ScopeLevel, string> = {
  COLLEGE: 'College',
  DEPARTMENT: 'Department',
  COURSE: 'Course',
  SPECIALIZATION: 'Specialization',
  COHORT: 'Batch',
  STUDENT: 'Student',
};

/** Access types that, given for "every student", open only the holder's OWN
 *  mentees. `admin.student_records` is the one: rule 2 runs under it and only
 *  a grant that names a college, department, course or batch widens it
 *  (`policies.assert_student_scope`). Without saying so, "View student
 *  records · All students" reads as the opposite of what it does. */
const MENTEES_ONLY_UNLESS_NARROWED: ReadonlySet<string> = new Set(['admin.student_records']);

/** The sidebar label each grantable office key adds for a faculty member, so
 *  "Analytics" can say it opens "Charts & numbers". Read from the shell's own
 *  list, the one the sidebar is drawn from, never typed again here. */
const OPENS: ReadonlyMap<string, string> = (() => {
  // The FIRST screen a key opens names it: `admin.mentors` opens "Assign
  // faculty" before it opens anything else.
  const opens = new Map<string, string>();
  for (const screen of GRANTABLE_ADMIN_SCREENS) {
    const keys = typeof screen.capability === 'string' ? [screen.capability] : screen.capability;
    for (const key of keys) {
      if (!opens.has(key)) {
        opens.set(key, screen.label);
      }
    }
  }
  return opens;
})();

/** Two letters for the avatar: first and last word of the name. */
function initialsOf(name: string): string {
  const words = name.trim().split(' ').filter((word) => word.length > 0);
  if (words.length === 0) {
    return '?';
  }
  const first = words[0].charAt(0);
  if (words.length === 1) {
    return first.toUpperCase();
  }
  return `${first}${words[words.length - 1].charAt(0)}`.toUpperCase();
}

@Component({
  selector: 'app-governance',
  standalone: true,
  // RouterLink: the two header links out (feature switches, what changed).
  imports: [RouterLink, PluralPipe],
  templateUrl: './governance.component.html',
  styleUrl: './governance.component.scss',
})
export class GovernanceComponent {
  readonly state = signal<ScreenState>('loading');
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly tab = signal<GovernanceTab>('give');

  /** `?tab=` opens a tab directly. The old names still work, so a bookmark or
   *  another screen's link lands on the tab that now holds the same thing. */
  private readonly openedAt = inject(ActivatedRoute).snapshot.queryParamMap.get('tab');

  /** WHO is granting decides whether a personal-record grant waits; the
   *  server decides, this only lets the button say which act it is. */
  private readonly session = inject(AuthService).session;
  readonly isMainAdmin = computed(() => this.session()?.role === 'ADMIN');

  // ---- server data --------------------------------------------------------
  readonly capabilities = signal<CapabilityOut[]>([]);
  readonly minReason = signal(20);
  readonly staff = signal<StaffOut[]>([]);
  readonly grants = signal<GrantOut[]>([]);
  readonly groups = signal<GroupOut[]>([]);
  readonly hierarchy = signal<HierarchyNode[]>([]);

  // =========================================================================
  // the institutional spine
  // =========================================================================

  private readonly nodeById = computed(() => {
    const byId = new Map<string, HierarchyNode>();
    for (const node of this.hierarchy()) {
      byId.set(node.id, node);
    }
    return byId;
  });

  /** Every id from this rung up to its college, itself included. Guarded
   *  against a cycle: this walks server data. */
  private chainOf(id: string): Set<string> {
    const chain = new Set<string>();
    const byId = this.nodeById();
    let cursor: string | null = id;
    while (cursor && !chain.has(cursor)) {
      chain.add(cursor);
      cursor = byId.get(cursor)?.parent_id ?? null;
    }
    return chain;
  }

  /** Does a grant with this reach touch the chosen rung? In BOTH directions,
   *  and a grant covering every student matches everything: filtering to
   *  Civil must show the grant on one Civil batch, the one on the college
   *  Civil sits in, and the unnarrowed ones, because all three reach a Civil
   *  student. */
  private reachTouches(scopeId: string | null, filterId: string): boolean {
    if (!filterId || !scopeId) {
      return true;
    }
    return this.chainOf(scopeId).has(filterId) || this.chainOf(filterId).has(scopeId);
  }

  /** Every rung, depth-first, with its student count. */
  readonly scopeOptions = computed<ScopeOption[]>(() => {
    const childrenOf = new Map<string | null, HierarchyNode[]>();
    for (const node of this.hierarchy()) {
      const siblings = childrenOf.get(node.parent_id) ?? [];
      siblings.push(node);
      childrenOf.set(node.parent_id, siblings);
    }
    const out: ScopeOption[] = [];
    const seen = new Set<string>();
    const walk = (parent: string | null, depth: number): void => {
      for (const node of childrenOf.get(parent) ?? []) {
        if (seen.has(node.id)) {
          continue;
        }
        seen.add(node.id);
        out.push({
          key: `${node.scope}:${node.id}`,
          // NBSP, because a <select> collapses leading spaces in an <option>.
          label:
            `${'  '.repeat(depth)}${RUNG_LABEL[node.scope]} · ${node.label} — ` +
            `${plural(node.students, 'student')}`,
        });
        walk(node.id, depth + 1);
      }
    };
    walk(null, 0);
    return out;
  });

  /** Colleges and their departments, for the "Covers" filter on the list. */
  readonly whereOptions = computed<ScopeOption[]>(() =>
    this.scopeOptions().filter(
      (option) => option.key.startsWith('COLLEGE:') || option.key.startsWith('DEPARTMENT:'),
    ),
  );

  readonly totalStudents = computed(() =>
    this.hierarchy()
      .filter((node) => node.scope === 'COLLEGE')
      .reduce((sum, node) => sum + node.students, 0),
  );

  // =========================================================================
  // grants, as rows
  // =========================================================================

  private readonly roleByUserId = computed(() => {
    const byId = new Map<string, string>();
    for (const person of this.staff()) {
      byId.set(person.user_id, person.role);
    }
    return byId;
  });

  readonly grantRows = computed<GrantRow[]>(() =>
    this.grants().map((grant) => this.toGrantRow(grant)),
  );

  private toGrantRow(grant: GrantOut): GrantRow {
    const isGroup = grant.subject_kind === 'GROUP';
    const kindLabel = isGroup
      ? 'Group'
      : this.staffRoleLabel(this.roleByUserId().get(grant.subject_id));
    const isPending = grant.approval_state === APPROVAL_PENDING;
    const horizon = this.reviewHorizonDays();
    const expiringSoon = this.fallsWithin(grant.expires_at, horizon);
    const dueForReview = !expiringSoon && this.fallsWithin(grant.review_at, horizon);
    const isNarrowed = !!grant.scope_level && !!grant.scope_id;
    let reachLabel = 'All students';
    if (isNarrowed) {
      reachLabel = `${RUNG_LABEL[grant.scope_level as ScopeLevel]} · ${grant.scope_label ?? grant.scope_id}`;
    } else if (grant.scope === 'SCOPED' || MENTEES_ONLY_UNLESS_NARROWED.has(grant.capability)) {
      reachLabel = 'Their mentees';
    }
    // THREE STATES, NOT ONE: a pending grant does nothing; an ending one works
    // and will stop; one due for review works and has no end date.
    let statusLabel = 'Active';
    let statusTone: 'good' | 'warn' | 'risk' = 'good';
    if (isPending) {
      statusLabel = 'Not active yet — needs approval';
      statusTone = 'risk';
    } else if (expiringSoon) {
      statusLabel = 'Ending soon';
      statusTone = 'warn';
    } else if (dueForReview) {
      statusLabel = 'Review due';
      statusTone = 'warn';
    }
    return {
      id: grant.id,
      functionKey: grant.capability,
      functionLabel: grant.capability_label,
      subjectKey: `${grant.subject_kind}:${grant.subject_id}`,
      subjectLabel: grant.subject_label,
      subjectInitials: initialsOf(grant.subject_label),
      subjectKindLabel: kindLabel,
      reachLabel,
      reachScopeId: isNarrowed ? grant.scope_id : null,
      grantedByLabel: grant.granted_by ?? '—',
      expiresLabel: this.asDayLabel(grant.expires_at),
      reviewLabel: this.asDayLabel(grant.review_at),
      statusLabel,
      statusTone,
      isPending,
      isExpiringSoon: expiringSoon,
      isDueForReview: dueForReview,
      carriesPersonalData: grant.carries_pii,
      reason: grant.reason,
      searchText: [grant.subject_label, grant.capability_label, grant.reason, kindLabel, reachLabel]
        .join(' ')
        .toLowerCase(),
    };
  }

  /** Student / Faculty / Alumni and Main Admin on screen; "mentor" is a
   *  stored value, not a label (AGENTS.md). */
  private staffRoleLabel(role: string | undefined): string {
    if (role === 'ADMIN') {
      return 'Main Admin';
    }
    if (role === 'MENTOR') {
      return 'Faculty';
    }
    return 'Person';
  }

  private asDayLabel(value: string | null): string {
    if (!value) {
      return '—';
    }
    const when = new Date(value);
    if (Number.isNaN(when.getTime())) {
      return '—';
    }
    return when.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });
  }

  private fallsWithin(value: string | null, days: number): boolean {
    if (!value) {
      return false;
    }
    const when = new Date(value).getTime();
    if (Number.isNaN(when)) {
      return false;
    }
    return when <= Date.now() + days * MILLISECONDS_PER_DAY;
  }

  // =========================================================================
  // Give access — many people, many kinds of access, one request
  // =========================================================================

  readonly giveMode = signal<GrantSubjectMode>('users');
  readonly pickedUserIds = signal<string[]>([]);
  readonly pickedGroupIds = signal<string[]>([]);
  readonly peopleSearch = signal('');
  readonly pickedKeys = signal<string[]>([]);
  /** `LEVEL:id`, or '' for every student. '' posts NEITHER field: `GrantIn`
   *  refuses a level without an id and an id without a level. */
  readonly grantScope = signal('');
  readonly grantExpiry = signal('');
  readonly grantReason = signal('');
  readonly grantBusy = signal(false);

  /** Staff matching the search, the ones already ticked first so a tick does
   *  not vanish when the search changes. */
  readonly staffShown = computed(() => {
    const needle = this.peopleSearch().trim().toLowerCase();
    const picked = new Set(this.pickedUserIds());
    return this.staff().filter(
      (person) =>
        picked.has(person.user_id) ||
        needle.length === 0 ||
        `${person.name} ${person.email}`.toLowerCase().includes(needle),
    );
  });

  readonly allShownPicked = computed(() => {
    const shown = this.staffShown();
    const picked = new Set(this.pickedUserIds());
    return shown.length > 0 && shown.every((person) => picked.has(person.user_id));
  });

  isPersonPicked(id: string): boolean {
    return this.pickedUserIds().includes(id);
  }

  togglePerson(id: string): void {
    this.pickedUserIds.update((ids) =>
      ids.includes(id) ? ids.filter((one) => one !== id) : [...ids, id],
    );
  }

  /** Tick every person the search shows, or untick them if all are ticked. */
  toggleAllShown(): void {
    const shown = this.staffShown().map((person) => person.user_id);
    if (this.allShownPicked()) {
      const drop = new Set(shown);
      this.pickedUserIds.update((ids) => ids.filter((id) => !drop.has(id)));
      return;
    }
    this.pickedUserIds.update((ids) => [...new Set([...ids, ...shown])]);
  }

  isGroupPicked(id: string): boolean {
    return this.pickedGroupIds().includes(id);
  }

  toggleGroupPick(id: string): void {
    this.pickedGroupIds.update((ids) =>
      ids.includes(id) ? ids.filter((one) => one !== id) : [...ids, id],
    );
  }

  setGiveMode(mode: GrantSubjectMode): void {
    this.giveMode.set(mode);
  }

  /** The access types in two plain groups: the faculty member's own tools,
   *  and the office's screens. */
  readonly accessChoiceGroups = computed<AccessChoiceGroup[]>(() => {
    const holders = new Map<string, Set<string>>();
    for (const grant of this.grants()) {
      const set = holders.get(grant.capability) ?? new Set<string>();
      set.add(`${grant.subject_kind}:${grant.subject_id}`);
      holders.set(grant.capability, set);
    }
    const toChoice = (capability: CapabilityOut): AccessChoice => {
      const opens = OPENS.get(capability.key) ?? null;
      return {
        key: capability.key,
        label: capability.label,
        opens: opens && opens !== capability.label ? opens : null,
        personal: capability.carries_pii,
        holders: holders.get(capability.key)?.size ?? 0,
      };
    };
    const all = this.capabilities();
    return [
      {
        title: 'Office screens',
        choices: all.filter((one) => one.scope === 'PROGRAMME').map(toChoice),
      },
      {
        title: 'Faculty tools',
        choices: all.filter((one) => one.scope === 'SCOPED').map(toChoice),
      },
    ].filter((group) => group.choices.length > 0);
  });

  isKeyPicked(key: string): boolean {
    return this.pickedKeys().includes(key);
  }

  toggleKey(key: string): void {
    this.pickedKeys.update((keys) =>
      keys.includes(key) ? keys.filter((one) => one !== key) : [...keys, key],
    );
  }

  private readonly pickedCapabilities = computed(() => {
    const picked = new Set(this.pickedKeys());
    return this.capabilities().filter((one) => picked.has(one.key));
  });

  readonly chosenScopeNode = computed(() => {
    const key = this.grantScope();
    if (!key) {
      return null;
    }
    return this.nodeById().get(key.slice(key.indexOf(':') + 1)) ?? null;
  });

  readonly pickedCount = computed(() =>
    this.giveMode() === 'users' ? this.pickedUserIds().length : this.pickedGroupIds().length,
  );

  readonly grantReasonShort = computed(
    () => this.grantReason().trim().length < this.minReason(),
  );

  readonly grantBlockedBecause = computed(() => {
    if (this.pickedCount() === 0) {
      return this.giveMode() === 'users'
        ? 'Choose at least one person'
        : 'Choose at least one group';
    }
    if (this.pickedKeys().length === 0) {
      return 'Choose at least one kind of access';
    }
    if (this.grantReasonShort()) {
      return `A reason of at least ${plural(this.minReason(), 'character')} is required`;
    }
    return null;
  });

  readonly canGrant = computed(() => this.grantBlockedBecause() === null && !this.grantBusy());

  /** A deputy's grant of anything showing a student's own record waits for a
   *  second holder of Governance; the Main Admin's never does. */
  readonly grantNeedsApproval = computed(
    () => !this.isMainAdmin() && this.pickedCapabilities().some((one) => one.carries_pii),
  );

  /** What is about to happen, in one sentence: who, what, which students,
   *  until when. The number is the count of grants the request writes. */
  readonly grantSummary = computed(() => {
    const people = this.pickedCount();
    const keys = this.pickedCapabilities();
    if (people === 0 || keys.length === 0) {
      return null;
    }
    const who =
      this.giveMode() === 'users' ? plural(people, 'person', 'people') : plural(people, 'group');
    const what =
      keys.length === 1 ? `“${keys[0].label}”` : plural(keys.length, 'kind of access', 'kinds of access');
    const target = this.chosenScopeNode();
    const covers = target
      ? `for ${RUNG_LABEL[target.scope].toLowerCase()} ${target.label} (${plural(target.students, 'student')})`
      : 'for all students';
    const until = this.grantExpiry() ? `until ${this.asDayLabel(endOfLocalDay(this.grantExpiry()))}` : 'with no end date';
    const inherit =
      this.giveMode() === 'groups' ? ' Everyone in the group, now and later, gets it.' : '';
    return `${who} will get ${what} ${covers}, ${until}.${inherit}`;
  });

  /** The blast radius, where it is wide: personal records for every student. */
  readonly grantWarning = computed(() => {
    if (this.chosenScopeNode() || this.pickedCount() === 0) {
      return null;
    }
    const wide = this.pickedCapabilities().filter(
      (one) =>
        one.scope === 'PROGRAMME' && one.carries_pii && !MENTEES_ONLY_UNLESS_NARROWED.has(one.key),
    );
    if (wide.length === 0) {
      return null;
    }
    const names = wide.map((one) => one.label).join(', ');
    return (
      `${names} ${wide.length === 1 ? 'shows' : 'show'} students’ personal records for every ` +
      `student — ${plural(this.totalStudents(), 'student')} today. Choose part of the college ` +
      'under “Which students” to narrow it.'
    );
  });

  /** "View student records" for every student opens only each holder's own
   *  mentees, which is the opposite of what the words suggest. */
  readonly recordsHint = computed(() => {
    if (this.chosenScopeNode()) {
      return null;
    }
    const narrowable = this.pickedCapabilities().find((one) => MENTEES_ONLY_UNLESS_NARROWED.has(one.key));
    if (!narrowable) {
      return null;
    }
    return (
      `“${narrowable.label}” for all students opens only each person’s own mentees, which ` +
      'they can already open. Choose a college, department, course or batch to open more.'
    );
  });

  readonly grantButtonLabel = computed(() => {
    if (this.grantBusy()) {
      return 'Saving…';
    }
    return this.grantNeedsApproval() ? 'Send for approval' : 'Give access';
  });

  clearGrantForm(): void {
    this.pickedUserIds.set([]);
    this.pickedGroupIds.set([]);
    this.pickedKeys.set([]);
    this.peopleSearch.set('');
    this.grantScope.set('');
    this.grantExpiry.set('');
    this.grantReason.set('');
  }

  async grant(): Promise<void> {
    if (!this.canGrant()) {
      return;
    }
    this.grantBusy.set(true);
    const body: Record<string, unknown> = {
      capabilities: this.pickedKeys(),
      reason: this.grantReason().trim(),
      user_ids: this.giveMode() === 'users' ? this.pickedUserIds() : [],
      group_ids: this.giveMode() === 'groups' ? this.pickedGroupIds() : [],
    };
    // The END of the typed day on this browser's clock, the calendar
    // `asDayLabel` prints it back in.
    const expiresAt = endOfLocalDay(this.grantExpiry());
    if (expiresAt !== null) {
      body['expires_at'] = expiresAt;
    }
    const scope = this.grantScope();
    if (scope) {
      const split = scope.indexOf(':');
      body['scope_level'] = scope.slice(0, split);
      body['scope_id'] = scope.slice(split + 1);
    }
    const created = await this.write<GrantOut[]>('POST', '/admin/governance/grants', body);
    this.grantBusy.set(false);
    if (created === null) {
      return;
    }
    this.clearGrantForm();
    await this.refreshGrants();
    this.flash.set(this.grantFlash(created));
    if (created.length > 0) {
      this.holderView.set('access');
      this.tab.set('holders');
    }
  }

  /** Say what was actually written. A grant waiting for approval is never
   *  reported as given. */
  private grantFlash(created: GrantOut[]): string {
    if (created.length === 0) {
      return 'They already had that access — nothing changed.';
    }
    const pending = created.filter((g) => g.approval_state === APPROVAL_PENDING).length;
    if (pending === created.length) {
      return (
        `Sent for approval: ${plural(created.length, 'grant')}. ` +
        'Nothing changes until a different admin approves.'
      );
    }
    const live = created.length - pending;
    const waiting = pending > 0 ? ` ${pending} more ${pending === 1 ? 'waits' : 'wait'} for approval.` : '';
    if (live === 1 && pending === 0) {
      const one = created[0];
      return `Gave “${one.capability_label}” to ${one.subject_label}.`;
    }
    return `Gave access: ${plural(live, 'grant')}.${waiting}`;
  }

  // =========================================================================
  // Who has access — grouped, filtered, tick many and remove
  // =========================================================================

  readonly holderView = signal<HolderView>('access');
  readonly quickFilter = signal('');
  readonly statusFilter = signal<StatusFilter>('all');
  readonly whereFilter = signal('');
  readonly selectedGrantIds = signal<string[]>([]);

  readonly filteredGrants = computed<GrantRow[]>(() => {
    const needle = this.quickFilter().trim().toLowerCase();
    const status = this.statusFilter();
    const whereKey = this.whereFilter();
    const where = whereKey ? whereKey.slice(whereKey.indexOf(':') + 1) : '';
    return this.grantRows().filter((row) => {
      if (needle && !row.searchText.includes(needle)) {
        return false;
      }
      if (!this.reachTouches(row.reachScopeId, where)) {
        return false;
      }
      if (status === 'pending') {
        return row.isPending;
      }
      if (status === 'ending') {
        return !row.isPending && (row.isExpiringSoon || row.isDueForReview);
      }
      if (status === 'active') {
        return !row.isPending && !row.isExpiringSoon && !row.isDueForReview;
      }
      return true;
    });
  });

  readonly holderGroups = computed<HolderGroup[]>(() => {
    const byKey = new Map<string, HolderGroup>();
    const byAccess = this.holderView() === 'access';
    for (const row of this.filteredGrants()) {
      const key = byAccess ? row.functionKey : row.subjectKey;
      let group = byKey.get(key);
      if (!group) {
        group = {
          key,
          title: byAccess ? row.functionLabel : row.subjectLabel,
          subtitle: byAccess ? '' : row.subjectKindLabel,
          rows: [],
        };
        byKey.set(key, group);
      }
      group.rows.push(row);
    }
    const out = [...byKey.values()];
    for (const group of out) {
      group.rows.sort((a, b) =>
        (byAccess ? a.subjectLabel : a.functionLabel).localeCompare(
          byAccess ? b.subjectLabel : b.functionLabel,
        ),
      );
      group.subtitle = byAccess
        ? plural(new Set(group.rows.map((row) => row.subjectKey)).size, 'person', 'people')
        : `${group.subtitle} · ${plural(group.rows.length, 'kind of access', 'kinds of access')}`;
    }
    return out.sort((a, b) => a.title.localeCompare(b.title));
  });

  readonly selectedRows = computed<GrantRow[]>(() => {
    const ids = new Set(this.selectedGrantIds());
    return this.grantRows().filter((row) => ids.has(row.id));
  });

  setHolderView(view: HolderView): void {
    this.holderView.set(view);
  }

  setStatusFilter(value: string): void {
    this.statusFilter.set(value as StatusFilter);
  }

  isGrantSelected(id: string): boolean {
    return this.selectedGrantIds().includes(id);
  }

  toggleGrantSelection(id: string): void {
    this.selectedGrantIds.update((ids) =>
      ids.includes(id) ? ids.filter((one) => one !== id) : [...ids, id],
    );
  }

  selectedIn(group: HolderGroup): GrantRow[] {
    const ids = new Set(this.selectedGrantIds());
    return group.rows.filter((row) => ids.has(row.id));
  }

  allSelectedIn(group: HolderGroup): boolean {
    return group.rows.length > 0 && this.selectedIn(group).length === group.rows.length;
  }

  toggleGroupSelection(group: HolderGroup): void {
    const ids = group.rows.map((row) => row.id);
    if (this.allSelectedIn(group)) {
      const drop = new Set(ids);
      this.selectedGrantIds.update((all) => all.filter((id) => !drop.has(id)));
      return;
    }
    this.selectedGrantIds.update((all) => [...new Set([...all, ...ids])]);
  }

  clearSelection(): void {
    this.selectedGrantIds.set([]);
  }

  /** The button on a group's heading: what it removes, in words. */
  removeLabelFor(group: HolderGroup): string {
    const picked = this.selectedIn(group);
    const rows = picked.length > 0 ? picked : group.rows;
    if (this.holderView() === 'access') {
      const people = new Set(rows.map((row) => row.subjectKey)).size;
      const all = picked.length === 0 && people > 1 ? 'all ' : '';
      return `Remove ${group.title} from ${all}${plural(people, 'person', 'people')}`;
    }
    return picked.length === 0
      ? 'Remove all their access'
      : `Remove ${plural(picked.length, 'kind of access', 'kinds of access')}`;
  }

  // =========================================================================
  // Needs attention — B2.4's two lists
  // =========================================================================

  readonly reviewHorizonDays = signal(EXPIRING_SOON_DAYS);
  readonly reviewPending = signal<GrantOut[]>([]);
  readonly reviewExpiring = signal<GrantOut[]>([]);

  readonly reviewPendingRows = computed<GrantRow[]>(() =>
    this.reviewPending().map((grant) => this.toGrantRow(grant)),
  );
  readonly reviewExpiringRows = computed<GrantRow[]>(() =>
    this.reviewExpiring().map((grant) => this.toGrantRow(grant)),
  );
  readonly reviewCount = computed(
    () => this.reviewPending().length + this.reviewExpiring().length,
  );

  // =========================================================================
  // approve / extend / remove — three writes, one reason box
  // =========================================================================

  readonly actionKind = signal<GrantAction | null>(null);
  readonly actionIds = signal<string[]>([]);
  private readonly actionReasonBox =
    viewChild<ElementRef<HTMLTextAreaElement>>('actionReasonBox');
  readonly actionReason = signal('');
  readonly actionExpiry = signal('');
  readonly actionReview = signal('');
  readonly actionBusy = signal(false);

  /** The rows the open box acts on. Looked up in the register AND the review
   *  lists, because Approve and Extend can be pressed on either. */
  readonly actionRows = computed<GrantRow[]>(() => {
    const ids = new Set(this.actionIds());
    const seen = new Map<string, GrantRow>();
    for (const row of [
      ...this.grantRows(),
      ...this.reviewPendingRows(),
      ...this.reviewExpiringRows(),
    ]) {
      if (ids.has(row.id) && !seen.has(row.id)) {
        seen.set(row.id, row);
      }
    }
    return [...seen.values()];
  });

  readonly actionReasonShort = computed(
    () => this.actionReason().trim().length < this.minReason(),
  );

  readonly actionTitle = computed(() => {
    const count = this.actionRows().length;
    switch (this.actionKind()) {
      case 'approve':
        return `Approve ${plural(count, 'grant')}`;
      case 'extend':
        return `Extend ${plural(count, 'grant')}`;
      case 'revoke':
        return 'Remove access';
      default:
        return '';
    }
  });

  readonly actionVerb = computed(() => {
    const count = this.actionRows().length;
    switch (this.actionKind()) {
      case 'approve':
        return this.actionBusy() ? 'Approving…' : 'Approve';
      case 'extend':
        return this.actionBusy() ? 'Extending…' : 'Extend';
      default:
        return this.actionBusy()
          ? 'Removing…'
          : count > 1
            ? `Remove ${plural(count, 'grant')}`
            : 'Remove access';
    }
  });

  readonly actionIcon = computed(() => {
    switch (this.actionKind()) {
      case 'approve':
        return 'how_to_reg';
      case 'extend':
        return 'schedule';
      default:
        return 'block';
    }
  });

  readonly actionButtonClass = computed(() =>
    this.actionKind() === 'revoke' ? 'btn danger' : 'btn primary',
  );

  readonly actionReasonPlaceholder = computed(() => {
    switch (this.actionKind()) {
      case 'approve':
        return 'Why this is agreed to.';
      case 'extend':
        return 'Why it is still needed.';
      default:
        return 'Why this access is being taken away.';
    }
  });

  readonly actionBlockedBecause = computed(() => {
    if (this.actionReasonShort()) {
      return `A reason of at least ${plural(this.minReason(), 'character')} is required`;
    }
    if (this.actionKind() === 'extend' && !this.actionExpiry() && !this.actionReview()) {
      return 'Choose a new end date, a new review date, or both';
    }
    return null;
  });

  readonly canRunAction = computed(
    () => this.actionBlockedBecause() === null && !this.actionBusy(),
  );

  startAction(kind: GrantAction, rows: GrantRow[]): void {
    // Approve applies only to a grant that waits for it; the API answers 409
    // "already active" otherwise.
    const eligible = kind === 'approve' ? rows.filter((row) => row.isPending) : rows;
    if (eligible.length === 0) {
      return;
    }
    this.actionKind.set(kind);
    this.actionIds.set(eligible.map((row) => row.id));
    this.actionReason.set('');
    this.actionExpiry.set('');
    this.actionReview.set('');
    // The box opens above the list; a row far down the page is far from it.
    setTimeout(() => this.actionReasonBox()?.nativeElement.focus(), 0);
  }

  startActionForSelection(kind: GrantAction): void {
    this.startAction(kind, this.selectedRows());
  }

  /** A group's "Remove …" button: the ticked rows in it, or every row in it
   *  when none is ticked (removing one access type from everybody holding it
   *  is the commonest version of "remove from many"). */
  startRemoveForGroup(group: HolderGroup): void {
    const picked = this.selectedIn(group);
    this.startAction('revoke', picked.length > 0 ? picked : group.rows);
  }

  cancelAction(): void {
    this.actionKind.set(null);
    this.actionIds.set([]);
    this.actionReason.set('');
    this.actionExpiry.set('');
    this.actionReview.set('');
  }

  /** A removal is ONE request for every grant in the box (`/grants/revoke`,
   *  all or nothing). Approve and extend go one grant at a time, because each
   *  can be refused on its own (a lapsed grant, approving one's own). */
  async runAction(): Promise<void> {
    const kind = this.actionKind();
    if (!kind || !this.canRunAction()) {
      return;
    }
    const rows = this.actionRows();
    if (rows.length === 0) {
      return;
    }
    this.actionBusy.set(true);
    const reason = this.actionReason().trim();
    let flash: string | null = null;
    try {
      if (kind === 'revoke') {
        const done = await this.write<RevokeManyOut>('POST', '/admin/governance/grants/revoke', {
          grant_ids: rows.map((row) => row.id),
          reason,
        });
        if (done !== null) {
          flash = this.removeFlash(done, rows);
        }
      } else {
        const body: Record<string, unknown> = { reason };
        if (kind === 'extend') {
          const expiresAt = endOfLocalDay(this.actionExpiry());
          if (expiresAt !== null) {
            body['expires_at'] = expiresAt;
          }
          const reviewAt = endOfLocalDay(this.actionReview());
          if (reviewAt !== null) {
            body['review_at'] = reviewAt;
          }
        }
        let doneCount = 0;
        for (const row of rows) {
          const done = await this.write<GrantOut>(
            'POST',
            `/admin/governance/grants/${row.id}/${kind}`,
            body,
          );
          if (done === null) {
            break;
          }
          doneCount = doneCount + 1;
        }
        if (doneCount > 0) {
          flash = this.lifecycleFlash(kind, doneCount, rows[0]);
        }
      }
    } finally {
      this.actionBusy.set(false);
    }
    if (flash === null) {
      return;
    }
    this.cancelAction();
    this.selectedGrantIds.set([]);
    await this.refreshGrants();
    this.flash.set(flash);
  }

  private removeFlash(done: RevokeManyOut, asked: GrantRow[]): string {
    const count = done.revoked.length;
    if (count === 0) {
      return 'That access had already been removed — nothing changed.';
    }
    if (count === 1) {
      const one = done.revoked[0];
      return `Removed “${one.capability_label}” from ${one.subject_label}.`;
    }
    const kinds = new Set(done.revoked.map((g) => g.capability_label));
    const people = new Set(done.revoked.map((g) => `${g.subject_kind}:${g.subject_id}`));
    const what = kinds.size === 1 ? `“${[...kinds][0]}”` : plural(kinds.size, 'kind of access', 'kinds of access');
    const already = asked.length - count > 0 ? ` ${asked.length - count} had already been removed.` : '';
    return `Removed ${what} from ${plural(people.size, 'person', 'people')}.${already}`;
  }

  private lifecycleFlash(kind: GrantAction, count: number, first: GrantRow): string {
    if (kind === 'approve') {
      return count === 1
        ? `Approved “${first.functionLabel}” for ${first.subjectLabel}. It is active now.`
        : `Approved ${plural(count, 'grant')}. They are active now.`;
    }
    return count === 1
      ? `Extended “${first.functionLabel}” for ${first.subjectLabel}.`
      : `Extended ${plural(count, 'grant')}.`;
  }

  // =========================================================================
  // groups
  // =========================================================================

  readonly newGroupName = signal('');
  readonly groupBusy = signal(false);
  readonly memberGroupId = signal('');
  readonly memberUserId = signal('');
  readonly memberReason = signal('');
  readonly memberBusy = signal(false);

  readonly memberReasonShort = computed(
    () => this.memberReason().trim().length < this.minReason(),
  );
  readonly addMemberBlockedBecause = computed(() => {
    if (this.memberGroupId().length === 0) {
      return 'Choose a group';
    }
    if (this.memberUserId().length === 0) {
      return 'Choose a person';
    }
    if (this.memberReasonShort()) {
      return `A reason of at least ${plural(this.minReason(), 'character')} is required`;
    }
    return null;
  });
  readonly canAddMember = computed(
    () => this.addMemberBlockedBecause() === null && !this.memberBusy(),
  );

  async createGroup(): Promise<void> {
    const name = this.newGroupName().trim();
    if (name.length < 2 || this.groupBusy()) {
      return;
    }
    this.groupBusy.set(true);
    const made = await this.write<GroupOut>('POST', '/admin/governance/groups', { name });
    this.groupBusy.set(false);
    if (made === null) {
      return;
    }
    this.newGroupName.set('');
    await this.refreshGrants();
    this.flash.set(`Created the group “${made.name}”.`);
  }

  /** Joining a group hands over every access the group holds, which is why
   *  the API asks for a reason here too. */
  async addMemberToGroup(): Promise<void> {
    if (!this.canAddMember()) {
      return;
    }
    this.memberBusy.set(true);
    const done = await this.write<GroupOut>(
      'POST',
      `/admin/governance/groups/${this.memberGroupId()}/members`,
      { user_ids: [this.memberUserId()], reason: this.memberReason().trim() },
    );
    this.memberBusy.set(false);
    if (done === null) {
      return;
    }
    this.memberUserId.set('');
    this.memberReason.set('');
    await this.refreshGrants();
    this.flash.set(`Added to “${done.name}”.`);
  }

  async removeMemberFromGroup(group: GroupOut, member: GroupMemberOut): Promise<void> {
    const done = await this.write<GroupOut>(
      'DELETE',
      `/admin/governance/groups/${group.id}/members/${member.user_id}`,
      null,
    );
    if (done === null) {
      return;
    }
    await this.refreshGrants();
    this.flash.set(`Removed ${member.name} from “${group.name}”.`);
  }

  labelForFunctionKey(key: string): string {
    return this.capabilities().find((one) => one.key === key)?.label ?? key;
  }

  // =========================================================================
  // tabs and loading
  // =========================================================================

  openTab(tab: GovernanceTab): void {
    this.tab.set(tab);
  }

  constructor() {
    const asked = this.openedAt;
    // The old tab names still land somewhere sensible: `review` was the queue
    // and `grants` the register; the catalogue's facts are on "Give access".
    const known: Record<string, GovernanceTab> = {
      give: 'give',
      holders: 'holders',
      attention: 'attention',
      groups: 'groups',
      review: 'attention',
      grants: 'holders',
      catalogue: 'give',
    };
    if (asked && known[asked]) {
      this.tab.set(known[asked]);
    }
    void this.load();
  }

  private async load(): Promise<void> {
    this.state.set('loading');
    try {
      const [catalogue, staff, grants, groups, spine, review] = await Promise.all([
        this.get<CatalogueOut>('/admin/governance/catalogue'),
        this.get<StaffOut[]>('/admin/governance/staff'),
        this.get<GrantOut[]>('/admin/governance/grants'),
        this.get<GroupOut[]>('/admin/governance/groups'),
        this.get<HierarchyNode[]>('/admin/governance/hierarchy'),
        this.get<ReviewOut>('/admin/governance/review'),
      ]);
      this.capabilities.set(catalogue.capabilities);
      this.minReason.set(catalogue.min_reason_chars);
      this.staff.set(staff);
      this.grants.set(grants);
      this.groups.set(groups);
      this.hierarchy.set(spine);
      this.applyReview(review);
      this.state.set('ready');
    } catch {
      this.error.set('Could not load this screen. Reload the page to try again.');
      this.state.set('error');
    }
  }

  private applyReview(review: ReviewOut): void {
    this.reviewHorizonDays.set(review.horizon_days);
    this.reviewPending.set(review.pending);
    this.reviewExpiring.set(review.expiring);
  }

  /** Reload after a write. It cannot throw: a failed reload says so rather
   *  than leaving the screen showing the state from before the change. */
  private async refreshGrants(): Promise<void> {
    try {
      const [grants, groups, review] = await Promise.all([
        this.get<GrantOut[]>('/admin/governance/grants'),
        this.get<GroupOut[]>('/admin/governance/groups'),
        this.get<ReviewOut>('/admin/governance/review'),
      ]);
      this.grants.set(grants);
      this.groups.set(groups);
      this.applyReview(review);
      // A ticked row that left the register cannot stay ticked.
      const live = new Set(grants.map((grant) => grant.id));
      this.selectedGrantIds.update((ids) => ids.filter((id) => live.has(id)));
    } catch {
      this.error.set(
        'The change was saved, but this list could not be reloaded. Reload the page to see it.',
      );
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
    method: 'POST' | 'PUT' | 'DELETE',
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

  /** The server's own sentence where there is one. FastAPI answers a schema
   *  error with `detail` as a LIST, which rendered raw says "[object Object]". */
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
