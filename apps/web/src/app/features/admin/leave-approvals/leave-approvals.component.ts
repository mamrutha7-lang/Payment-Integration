/**
 * Leave approvals — the placement office's queue, in the 2026-09 console dress.
 *
 * The applicant signs the BGSCET form and sends it; this is where it is read and
 * decided. The board (`design/admin/Leave.html`) replaces the full-page sheet
 * this screen used to draw with a QUEUE beside a 380px decision panel, and the
 * document itself stays one click away as the paper PDF — `GET
 * /api/leaves/{id}/paper.pdf` renders the college's own form with the uploaded
 * signatures drawn in, which is the version that matters once it is filed.
 *
 * FOUR LIVE TABS (B10.4). Pending comes from /leaves/pending; Approved,
 * Rejected and Cancelled each come from /leaves/history, which now takes
 * `?status=`. The last one needed that parameter to exist at all: /pending
 * filters SUBMITTED and FIRST_APPROVED and /history used to filter APPROVED and
 * REJECTED, so a withdrawn request was returned by NEITHER and no amount of
 * client-side filtering could have lit the tab. The default (no `status=`) is
 * untouched, so the Approved and Rejected tabs still read the same array they
 * always did.
 *
 * ONE SIGNATURE, THE MAIN ADMIN'S (2026-09-16). This queue is the Main Admin's
 * alone — `GET /leaves/pending`, `/history` and `POST /{id}/decision` are
 * `require_admin` — and one APPROVE is the sanction. The two-signature chain
 * this screen used to walk (first signature, then sanction by a different
 * approver) is gone; a request signed once under it is still listed as
 * "Sanction pending" and one decision completes it.
 *
 * A REJECTION NEEDS A REASON. The applicant sees the remarks and the status and
 * nothing else, so refusing without words leaves them with a form marked "not
 * sanctioned" and no idea what to do next. On an approval the remarks are
 * optional and are still sent — `note` is stored for both decisions.
 *
 * THE PANEL READS THREE MORE ENDPOINTS, EACH BEHIND THE SAME GATE AS THE PAPER.
 * `GET /leaves/{id}/balance` (the applicant's allowances for the year the
 * request falls in), `GET /leaves/{id}/attachments` and `GET
 * /leaves/{id}/alternate` are all gated by `_assert_can_decide` server-side —
 * the same function, the same three doors, the same flattened 404 — so this
 * screen asks for them and reports what it is given rather than deciding
 * anything itself. A 403 or 404 on one of the three leaves that block absent;
 * it is never rendered as "none recorded", because those are opposite facts.
 *
 * NOTHING HERE IS INVENTED. Where the office has recorded no allowance the
 * panel says exactly that — "no allowance recorded, so nothing is measured
 * against one" — and never a zero, for `LeaveBalance`'s own reason: a missing
 * row and an exhausted allowance are opposite states and a 0 in a chip reads as
 * the second.
 *
 * THE SCOPE PILLS, WIRED — AND THEY ARE TWO DIFFERENT FACTS (B1.4). `GET
 * /leaves/pending` and `GET /leaves/history` take NO college or department
 * parameter. The server narrows the queue itself (`_narrow_to_scope` in
 * app/routers/leave.py) and states how far this session's grant reaches in
 * response headers — `X-Reep-Scope`, plus `X-Reep-Scope-Colleges` and
 * `X-Reep-Scope-Departments` when the reach names any. So:
 *
 *   - COLLEGE is a READ-OUT of that reach, not a picker. There is nothing to
 *     send, and a menu here would be this screen claiming it can change what
 *     you may see when all it could ever do is hide rows you are entitled to.
 *   - DEPARTMENT is a client-side filter over the rows already returned, and it
 *     narrows WHAT IS DRAWN, never what may be read. It is honest because the
 *     rows carry it: `LeaveOut.requester_department` is the requester's own
 *     department. It is NOT the spine department the grant's scope names —
 *     that one is free text on the `users` row and this one is an id — so the
 *     two are labelled separately and never added together.
 *
 * `scope: none` IS NOT AN EMPTY QUEUE. A grant that reaches nobody answers `[]`
 * exactly as a quiet Monday does; scope_views.py's own comment is that "may see
 * everything" and "may see nothing" are opposite facts that must never render
 * the same, so the empty state says which one this is. A response carrying NO
 * scope header at all makes no claim on this screen rather than a guessed
 * one; an absent header is not the word "none". Since 2026-09-16 the only
 * caller is the Main Admin and the server always answers `programme`.
 */

import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import { plural } from '../../../shared/text/plural.pipe';
import { LeaveCalendarDialogComponent } from './leave-calendar-dialog.component';
import {
  LeavePolicyDialogComponent,
  type LeaveCollegeSummary,
} from './leave-policy-dialog.component';

/** The three words `app/scope_views.py` writes into `X-Reep-Scope`. Three and
 *  not a boolean: `programme` and `none` are opposite facts, not two ends of
 *  one scale, and the screen must never render them the same. */
type ScopeWord = 'programme' | 'narrowed' | 'none';

interface ScopeReach {
  word: ScopeWord;
  /** Ids, not names — there is no catalogue on this screen to resolve them
   *  against, so they are counted and never printed. */
  colleges: string[];
  departments: string[];
}

const SCOPE_HEADER = 'X-Reep-Scope';
const SCOPE_COLLEGES_HEADER = 'X-Reep-Scope-Colleges';
const SCOPE_DEPARTMENTS_HEADER = 'X-Reep-Scope-Departments';

/** `MAX_SCOPE_IDS` in app/scope_views.py. The header stops at twenty ids, so a
 *  count standing exactly on it is a floor and is printed as "20+" rather than
 *  as a total this screen cannot know. */
const MAX_SCOPE_IDS = 20;

const MILLISECONDS_IN_A_DAY = 24 * 60 * 60 * 1000;

/** Short month names for `dateSpan`. Not `toLocaleString`: that needs a
 *  Date, and a date-only string through a Date is the UTC-midnight trap
 *  documented on `dateSpan`. */
const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const;

interface AltRow {
  date: string;
  staff_name: string;
  cls: string;
  time: string;
  remarks: string;
}

interface LeaveRow {
  id: string;
  from_date: string;
  to_date: string;
  reason: string;
  status: string;
  leave_kind: string | null;
  credit: string | null;
  alt_name: string | null;
  alt_rows: AltRow[];
  requester_name: string;
  requester_designation: string | null;
  requester_department: string | null;
  signed_at: string | null;
  director_name: string | null;
  director_decided_at: string | null;
  director_note: string | null;
  /** WHICH FUNCTION EACH SIGNATURE WAS GIVEN IN (B10.1). NULL on every row
   *  decided before those columns existed and on every step nobody has signed,
   *  and rendered as nothing at all — guessing a function from who the signer
   *  is today would put a claim about authority onto an old form. */
  first_signed_as: string | null;
  second_signed_as: string | null;
  /** The LATEST change to a decision after it was made (2026-10-13):
   *  REVOKED or REOPENED, with who, when and the reason that had to be given.
   *  All null on a request whose decision was never changed. */
  decision_change_kind: 'REVOKED' | 'REOPENED' | null;
  decision_changed_at: string | null;
  decision_change_reason: string | null;
  decision_changed_by_name: string | null;
}

/** One paper attached to a request (B10.3). */
interface LeaveAttachment {
  id: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  uploaded_at: string;
  uploaded_by_name: string | null;
  can_delete: boolean;
}

interface BalanceRow {
  kind: string;
  entitled_days: number;
  consumed_days: number;
  remaining_days: number;
}

interface BalanceSet {
  academic_year: string;
  balances: BalanceRow[];
}

/** One line of the Alternate Arrangements table, with the account link and the
 *  acceptance B10.6 added to the stored JSON. */
interface AlternateRow {
  index: number;
  date: string;
  staff_name: string;
  cls: string;
  time: string;
  remarks: string;
  user_id: string | null;
  user_name: string | null;
  accepted_at: string | null;
}

interface AlternateTable {
  rows: AlternateRow[];
}

/** The three words `app/leave_paper.py::SIGNED_AS_LABELS` prints, and the same
 *  three the model's `SIGNED_AS` tuple allows. A value this map does not know
 *  is printed as it arrived rather than dropped: a function the server records
 *  and this screen cannot name is a fact, and hiding it is worse than showing
 *  it raw. */
const SIGNED_AS_LABELS: Record<string, string> = {
  MENTOR: 'Mentor',
  DELEGATE: 'Delegated approver',
  MAIN_ADMIN: 'Main Admin',
};

/** The five printed options, in the order the college's form lists them. */
const KINDS = [
  { id: 'CASUAL', label: 'Casual' },
  { id: 'PERMISSION', label: 'Permission' },
  { id: 'OOD', label: 'OOD' },
  { id: 'RH', label: 'RH' },
  { id: 'LOP', label: 'LOP' },
] as const;

const EVERY_KIND = 'ALL';

/** The department filter's "no department chosen" value. A sentinel and not the
 *  word "ALL": `requester_department` is free text on the `users` row, so any
 *  readable word is a department somebody could have typed. */
const EVERY_DEPARTMENT = '*';

/** The bucket for a requester with no department on their row. `null` and `''`
 *  are the same fact here — nothing is recorded — so they share one option. */
const NO_DEPARTMENT = '';

/** `GET /leaves/history` is `.limit(200)` with no paging parameter. The
 *  status bar says "1 to N of N", which is a claim about completeness this
 *  screen cannot make once the cap is reached, so a decided queue sitting
 *  exactly on it says so instead of quietly dropping the 201st request. */
const HISTORY_SERVER_CAP = 200;

type QueueTab = 'pending' | 'approved' | 'rejected' | 'revoked' | 'cancelled';

/** `revoke` and `reopen` change a decision already made — `POST
 *  /leaves/{id}/change-decision`, always with a reason. */
type DecisionMode = 'idle' | 'approve' | 'reject' | 'revoke' | 'reopen';

/** One row of the panel's approval chain. `refused` is the rejected end of it —
 *  a step that happened and stopped the form, which is neither done nor next. */
type ChainState = 'done' | 'active' | 'pending' | 'refused';

interface ChainStep {
  label: string;
  detail: string;
  state: ChainState;
}

interface StatusChip {
  label: string;
  tone: 'good' | 'warn' | 'risk' | 'neutral';
}

const EMPTY_NOTE: Record<QueueTab, string> = {
  pending: 'No requests waiting on your signature.',
  approved: 'Nothing sanctioned yet.',
  rejected: 'No rejected requests.',
  revoked: 'No sanction has been revoked.',
  cancelled: 'Nobody has withdrawn a request.',
};

@Component({
  selector: 'app-admin-leave-approvals',
  standalone: true,
  imports: [DatePipe, LeaveCalendarDialogComponent, LeavePolicyDialogComponent],
  templateUrl: './leave-approvals.component.html',
  styleUrl: './leave-approvals.component.scss',
})
export class AdminLeaveApprovalsComponent {
  private readonly auth = inject(AuthService);
  readonly kinds = KINDS;
  readonly everyKind = EVERY_KIND;
  readonly everyDepartment = EVERY_DEPARTMENT;
  readonly noDepartment = NO_DEPARTMENT;

  readonly tabs: { key: QueueTab; label: string }[] = [
    { key: 'pending', label: 'Pending' },
    { key: 'approved', label: 'Approved' },
    { key: 'rejected', label: 'Rejected' },
    { key: 'revoked', label: 'Revoked' },
    { key: 'cancelled', label: 'Cancelled' },
  ];

  readonly tab = signal<QueueTab>('pending');
  readonly kindFilter = signal<string>(EVERY_KIND);
  readonly departmentFilter = signal<string>(EVERY_DEPARTMENT);
  /** What the server said this session's grant reaches, off the queue response.
   *  `null` means the response stated nothing, which is not "nothing". */
  readonly scope = signal<ScopeReach | null>(null);
  readonly pending = signal<LeaveRow[] | null>(null);
  readonly history = signal<LeaveRow[] | null>(null);
  /** `GET /leaves/history?status=CANCELLED` — its own read, because the default
   *  history deliberately does NOT include withdrawn requests. */
  readonly cancelled = signal<LeaveRow[] | null>(null);
  /** `GET /leaves/history?status=REVOKED`, its own read for Cancelled's reason:
   *  the default history is approved and refused only. */
  readonly revoked = signal<LeaveRow[] | null>(null);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly selectedId = signal<string | null>(null);
  readonly decisionMode = signal<DecisionMode>('idle');
  readonly remarks = signal<string>('');
  readonly remarksError = signal<string | null>(null);
  readonly deciding = signal<boolean>(false);
  /** Changing a decision: the reason (required) and, for a leave already under
   *  way, the office's explicit yes. */
  readonly changeReason = signal<string>('');
  readonly changeError = signal<string | null>(null);
  readonly confirmStarted = signal<boolean>(false);

  /** What the panel's three extra reads answered for the SELECTED request.
   *  `null` means "not answered", which is never rendered as "none recorded". */
  readonly attachments = signal<LeaveAttachment[] | null>(null);
  readonly balanceSet = signal<BalanceSet | null>(null);
  readonly alternate = signal<AlternateTable | null>(null);

  /** The two office dialogs (B10.2). */
  readonly policyOpen = signal(false);
  readonly calendarOpen = signal(false);
  readonly colleges = signal<LeaveCollegeSummary[]>([]);

  /** Every write behind those two dialogs is `require_admin` server-side, so a
   *  granted approver who is not the office gets the buttons disabled with the
   *  reason on them rather than a dialog whose every control answers 403. */
  readonly isMainAdmin = computed(() => this.auth.session()?.role === 'ADMIN');

  readonly isLoading = computed(
    () =>
      this.pending() === null ||
      this.history() === null ||
      this.cancelled() === null ||
      this.revoked() === null,
  );

  readonly counts = computed(() => {
    const decided = this.history() ?? [];
    return {
      pending: (this.pending() ?? []).length,
      approved: decided.filter((row) => row.status === 'APPROVED').length,
      rejected: decided.filter((row) => row.status === 'REJECTED').length,
      revoked: (this.revoked() ?? []).length,
      cancelled: (this.cancelled() ?? []).length,
    };
  });

  /** The rows of the open tab, narrowed by the type and department filters.
   *
   *  BOTH NARROW WHAT IS DRAWN, not what may be read: the server has already
   *  decided the second, and neither of these two pills can widen it. */
  readonly rows = computed<LeaveRow[]>(() => {
    let inTab = this.rowsInTab();
    const wantedKind = this.kindFilter();
    if (wantedKind !== EVERY_KIND) {
      inTab = inTab.filter((row) => row.leave_kind === wantedKind);
    }
    const wantedDepartment = this.departmentFilter();
    if (wantedDepartment !== EVERY_DEPARTMENT) {
      inTab = inTab.filter((row) => this.departmentOf(row) === wantedDepartment);
    }
    return inTab;
  });

  /** Every department named on a loaded request, from EVERY queue so the menu
   *  does not change shape when the tab does. */
  readonly departments = computed<string[]>(() => {
    const names = new Set<string>();
    for (const row of this.everyLoadedRow()) {
      const name = this.departmentOf(row);
      if (name !== NO_DEPARTMENT) names.add(name);
    }
    return Array.from(names).sort((left, right) => left.localeCompare(right));
  });

  /** True when some loaded request has no department, so the menu offers that
   *  bucket rather than leaving those rows reachable only under "All". */
  readonly hasUnstatedDepartment = computed(() =>
    this.everyLoadedRow().some((row) => this.departmentOf(row) === NO_DEPARTMENT),
  );

  private everyLoadedRow(): LeaveRow[] {
    return [
      ...(this.pending() ?? []),
      ...(this.history() ?? []),
      ...(this.revoked() ?? []),
      ...(this.cancelled() ?? []),
    ];
  }

  readonly departmentFilterLabel = computed(() => {
    const wanted = this.departmentFilter();
    if (wanted === EVERY_DEPARTMENT) return 'All';
    if (wanted === NO_DEPARTMENT) return 'Not on record';
    return wanted;
  });

  readonly selectedRequest = computed<LeaveRow | null>(() => {
    const id = this.selectedId();
    if (id === null) return null;
    return this.rows().find((row) => row.id === id) ?? null;
  });

  /** "Nobody is in your reach" and "nothing is waiting" are the two reasons a
   *  queue is empty, and they are opposite facts about this account. */
  readonly emptyTitle = computed(() =>
    this.scope()?.word === 'none' ? 'Nobody is in your reach.' : 'Nothing here.',
  );

  readonly emptyNote = computed(() => {
    if (this.scope()?.word === 'none') {
      return 'Your grant for leave approvals names no college, department, batch or student, so this queue can never fill — it is not that nothing is waiting.';
    }
    return EMPTY_NOTE[this.tab()];
  });

  /** The College pill: the caller's own reach, as text. */
  readonly scopeChip = computed<{ label: string; tone: 'neutral' | 'accent' | 'warn' } | null>(
    () => {
      const reach = this.scope();
      if (reach === null) return null;
      if (reach.word === 'programme') {
        return { label: 'Reach · every college', tone: 'neutral' };
      }
      if (reach.word === 'none') {
        return { label: 'Reach · nobody', tone: 'warn' };
      }
      const parts = [
        this.idCount(reach.colleges, 'college', 'colleges'),
        this.idCount(reach.departments, 'department', 'departments'),
      ].filter((part) => part !== null);
      if (parts.length === 0) return { label: 'Reach · narrowed', tone: 'accent' };
      return { label: `Reach · ${parts.join(' · ')}`, tone: 'accent' };
    },
  );

  /** The sentence under the filters. It says which of the two pills is a fact
   *  about permission and which is a fact about what is drawn. */
  readonly scopeNote = computed<{ text: string; tone: 'accent' | 'warn' } | null>(() => {
    const reach = this.scope();
    if (reach === null) return null;
    if (reach.word === 'programme') {
      return {
        tone: 'accent',
        text: 'Your grant reaches the whole programme — every college and department, staff leave included. The College pill states that; it is a read-out, not a filter, because the queue arrives already cut to what you may see. Department narrows what is drawn here and nothing else.',
      };
    }
    if (reach.word === 'none') {
      return {
        tone: 'warn',
        text: 'Your grant for leave approvals reaches nobody: it names no college, department, batch or student that still exists. The queue below is empty for that reason, not because no request is waiting. A Main Admin can give the grant a scope in Governance.',
      };
    }
    return {
      tone: 'accent',
      text: 'Your grant is narrowed, and the server has already cut this queue to it — the College pill counts what it reaches. Department below narrows what is drawn here; it is the department printed on the request, which is the requester\u2019s own, not the one your grant names.',
    };
  });

  readonly rowRangeLabel = computed(() => {
    const shown = this.rows().length;
    if (shown === 0) return 'No rows';
    return `1 to ${shown} of ${shown}`;
  });

  readonly kindFilterLabel = computed(() => {
    const wanted = this.kindFilter();
    if (wanted === EVERY_KIND) return 'All';
    return this.kindLabel(wanted);
  });

  /** The decision panel's chain, built only from what the row carries: the
   *  applicant's signature, then the office's ONE decision. */
  readonly chainSteps = computed<ChainStep[]>(() => {
    const request = this.selectedRequest();
    if (request === null) return [];
    const steps = [this.applicantStep(request), this.sanctionStep(request)];
    const change = this.changeStep(request);
    if (change !== null) {
      // A reopen comes BEFORE the decision that followed it (or is awaited);
      // a revocation comes after the sanction it took back.
      if (request.decision_change_kind === 'REOPENED') steps.splice(1, 0, change);
      else steps.push(change);
    }
    return steps;
  });

  readonly canDecide = computed(() => {
    const request = this.selectedRequest();
    if (request === null) return false;
    return this.isAwaitingSignature(request);
  });

  /** Which change the selected request can take: a sanction can be revoked; a
   *  refusal or a revocation can be reopened. `null` for anything else — a
   *  pending request is decided, and a withdrawn one was the applicant's act. */
  readonly changeAction = computed<'revoke' | 'reopen' | null>(() => {
    const request = this.selectedRequest();
    if (request === null) return null;
    if (request.status === 'APPROVED') return 'revoke';
    if (request.status === 'REJECTED' || request.status === 'REVOKED') return 'reopen';
    return null;
  });

  /** Has the selected leave's first day arrived? The server decides with the
   *  college's own date; this only chooses what to draw, and a disagreement
   *  near midnight comes back as the server's sentence. */
  readonly selectedHasBegun = computed(() => {
    const request = this.selectedRequest();
    return request !== null && request.from_date <= this.todayIso();
  });

  /** A leave under way is the Main Admin's alone to revoke. */
  readonly mayRevokeSelected = computed(() => !this.selectedHasBegun() || this.isMainAdmin());

  /** The button's own words: one signature, and it is the sanction. */
  readonly approveButtonLabel = computed(() => 'Sanction');

  readonly confirmApproveLabel = computed(() => `Confirm ${this.approveButtonLabel().toLowerCase()}`);

  readonly selectedCount = computed(() => (this.selectedRequest() === null ? 0 : 1));

  /** True when the queue on screen is standing on the server's row cap.
   *
   *  Measured against the ARRAY THIS TAB WAS SERVED, not against `history()`:
   *  Cancelled is its own request with its own `.limit(200)`, so reading the
   *  settled queue's length there would report a cap that belongs to a
   *  different response — and, on a deployment with 200 decided requests and
   *  three withdrawn ones, would tell the office three rows might be missing
   *  when none are. */
  readonly historyCapped = computed(() => {
    const tab = this.tab();
    if (tab === 'pending') return false;
    const served =
      tab === 'cancelled'
        ? (this.cancelled() ?? [])
        : tab === 'revoked'
          ? (this.revoked() ?? [])
          : (this.history() ?? []);
    return served.length >= HISTORY_SERVER_CAP;
  });

  readonly historyCap = HISTORY_SERVER_CAP;

  constructor() {
    void this.loadQueues();
  }

  // ----------------------------------------------------------- the queue --

  setTab(tab: QueueTab): void {
    this.tab.set(tab);
    this.clearSelection();
  }

  setKindFilter(kind: string): void {
    this.kindFilter.set(kind);
    this.clearSelection();
  }

  setDepartmentFilter(department: string): void {
    this.departmentFilter.set(department);
    this.clearSelection();
  }

  /** One bucket per requester: an absent, null or blank department is the same
   *  fact and must not become two options that each hold some of the rows. */
  private departmentOf(row: LeaveRow): string {
    return (row.requester_department ?? '').trim();
  }

  /** "3 departments", or "20+ colleges" when the header stood on its cap. */
  private idCount(ids: string[], one: string, many: string): string | null {
    if (ids.length === 0) return null;
    const capped = ids.length >= MAX_SCOPE_IDS ? `${MAX_SCOPE_IDS}+` : `${ids.length}`;
    return `${capped} ${ids.length === 1 ? one : many}`;
  }

  selectRequest(id: string): void {
    this.selectedId.set(id);
    this.decisionMode.set('idle');
    this.remarks.set('');
    this.remarksError.set(null);
    this.resetChange();
    this.clearPanelReads();
    void this.loadPanel(id);
  }

  clearSelection(): void {
    this.selectedId.set(null);
    this.decisionMode.set('idle');
    this.remarks.set('');
    this.remarksError.set(null);
    this.resetChange();
    this.clearPanelReads();
  }

  private clearPanelReads(): void {
    this.attachments.set(null);
    this.balanceSet.set(null);
    this.alternate.set(null);
  }

  /** The applicant's allowances, the attached papers and the alternate table.
   *
   *  EVERY ONE OF THE THREE IS OPTIONAL, and a refusal is silence rather than
   *  an empty list: all three are gated server-side by `_assert_can_decide`, so
   *  a 404 here means this account may not read that half of the request — not
   *  that the applicant attached nothing. Each result is discarded if the
   *  selection moved on while it was in flight, because a balance rendered
   *  under somebody else's name is the worst failure this panel has. */
  private async loadPanel(id: string): Promise<void> {
    const stillSelected = () => this.selectedId() === id;
    try {
      const [balance, attachments, alternate] = await Promise.all([
        fetch(`${environment.apiBase}/leaves/${id}/balance`, { credentials: 'include' }),
        fetch(`${environment.apiBase}/leaves/${id}/attachments`, { credentials: 'include' }),
        fetch(`${environment.apiBase}/leaves/${id}/alternate`, { credentials: 'include' }),
      ]);
      if (!stillSelected()) return;
      if (balance.ok) this.balanceSet.set((await balance.json()) as BalanceSet);
      if (!stillSelected()) return;
      if (attachments.ok) this.attachments.set((await attachments.json()) as LeaveAttachment[]);
      if (!stillSelected()) return;
      if (alternate.ok) this.alternate.set((await alternate.json()) as AlternateTable);
    } catch {
      // The panel's three extras stay absent. The decision controls above them
      // are unaffected — they were never gated on this.
    }
  }

  /** `GET /api/leaves/{id}/attachments/{aid}/file`. Served `Content-Disposition:
   *  attachment` whatever this link asks for; an inline PDF on the SPA's own
   *  origin is same-origin script execution (document_store says so). */
  attachmentUrl(request: LeaveRow, attachment: LeaveAttachment): string {
    return `${environment.apiBase}/leaves/${request.id}/attachments/${attachment.id}/file`;
  }

  fileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  /** The word the paper prints for a function, or the raw value when this
   *  screen has not been taught one. Never the empty string: a step nobody has
   *  signed has no function, and the template asks before it calls. */
  signedAsLabel(value: string | null): string {
    if (!value) return '';
    return SIGNED_AS_LABELS[value] ?? value;
  }

  /** The alternate table, preferring the server's own rows (which carry the
   *  account link and the acceptance) and falling back to what the queue row
   *  already printed. */
  readonly altRows = computed<AlternateRow[]>(() => {
    const table = this.alternate();
    if (table !== null) return table.rows;
    const request = this.selectedRequest();
    if (request === null) return [];
    return request.alt_rows.map((row, index) => ({
      index,
      date: row.date,
      staff_name: row.staff_name,
      cls: row.cls,
      time: row.time,
      remarks: row.remarks,
      user_id: null,
      user_name: null,
      accepted_at: null,
    }));
  });

  // -------------------------------------------------------- the two dialogs --

  openPolicy(): void {
    if (!this.isMainAdmin()) return;
    this.policyOpen.set(true);
  }

  openCalendar(): void {
    if (!this.isMainAdmin()) return;
    void this.loadColleges();
    this.calendarOpen.set(true);
  }

  closeDialogs(): void {
    this.policyOpen.set(false);
    this.calendarOpen.set(false);
  }

  /** The colleges the calendar dialog picks between, read from the policy sheet
   *  rather than from a second colleges endpoint — one question, one answer. */
  private async loadColleges(): Promise<void> {
    if (this.colleges().length > 0) return;
    try {
      const response = await fetch(`${environment.apiBase}/admin/leave-policy`, {
        credentials: 'include',
      });
      if (!response.ok) return;
      const sheet = (await response.json()) as { colleges: LeaveCollegeSummary[] };
      this.colleges.set(sheet.colleges ?? []);
    } catch {
      // The dialog then opens on an empty picker and says so.
    }
  }

  isSelected(row: LeaveRow): boolean {
    return this.selectedId() === row.id;
  }

  /** SUBMITTED and FIRST_APPROVED are the two states a signature can land on. */
  isAwaitingSignature(row: LeaveRow): boolean {
    return row.status === 'SUBMITTED' || row.status === 'FIRST_APPROVED';
  }

  // --------------------------------------------------- reading one request --

  initialsOf(row: LeaveRow): string {
    const words = row.requester_name.trim().split(/\s+/).filter((word) => word.length > 0);
    if (words.length === 0) return '—';
    const first = words[0].charAt(0);
    if (words.length === 1) return first.toUpperCase();
    const last = words[words.length - 1].charAt(0);
    return `${first}${last}`.toUpperCase();
  }

  /** The line under the name: the institutional identity the form prints. */
  requesterLine(row: LeaveRow): string {
    const parts: string[] = [];
    if (row.requester_designation) parts.push(row.requester_designation);
    if (row.requester_department) parts.push(row.requester_department);
    if (parts.length === 0) return 'Not on record';
    return parts.join(' · ');
  }

  kindLabel(kind: string | null): string {
    if (kind === null) return 'Not stated';
    const printed = KINDS.find((option) => option.id === kind);
    if (printed === undefined) return kind;
    return printed.label;
  }

  /** "16–17 Sep 2026", the way the board and the paper form write a span.
   *
   *  Parsed from the ISO parts by hand and NOT through `new Date('2026-09-16')`
   *  or DatePipe: a date-only string is parsed as UTC midnight, which renders
   *  as the 15th in every timezone west of Greenwich — a leave form that shows
   *  the applicant a day they did not ask for. Unparseable input falls back to
   *  the raw strings rather than inventing a date. */
  dateSpan(row: LeaveRow): string {
    const first = this.dateParts(row.from_date);
    const last = this.dateParts(row.to_date);
    if (first === null || last === null) {
      if (row.from_date === row.to_date) return row.from_date;
      return `${row.from_date} — ${row.to_date}`;
    }
    if (row.from_date === row.to_date) return this.printDate(first, true);
    if (first.year === last.year && first.month === last.month) {
      return `${first.day}–${this.printDate(last, true)}`;
    }
    if (first.year === last.year) {
      return `${this.printDate(first, false)} – ${this.printDate(last, true)}`;
    }
    return `${this.printDate(first, true)} – ${this.printDate(last, true)}`;
  }

  private dateParts(value: string): { year: number; month: number; day: number } | null {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? '');
    if (match === null) return null;
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    if (month < 1 || month > 12 || day < 1 || day > 31) return null;
    return { year, month, day };
  }

  private printDate(
    parts: { year: number; month: number; day: number },
    withYear: boolean,
  ): string {
    const month = MONTHS[parts.month - 1];
    if (withYear) return `${parts.day} ${month} ${parts.year}`;
    return `${parts.day} ${month}`;
  }

  /** Calendar days, inclusive of both ends, and LABELLED as calendar days.
   *
   *  The academic calendar exists now (the Calendar dialog writes it) and the
   *  server counts working days against it when it measures a request — but
   *  neither queue endpoint returns that number, and this screen will not fetch
   *  a college's holidays per row to compute a second one. Two independent
   *  counts of the same span is how the queue ends up disagreeing with the
   *  refusal the applicant was shown. What is drawn here is the span on the
   *  form, and it says so. */
  dayCountLabel(row: LeaveRow): string {
    const firstDay = Date.parse(`${row.from_date}T00:00:00Z`);
    const lastDay = Date.parse(`${row.to_date}T00:00:00Z`);
    if (Number.isNaN(firstDay) || Number.isNaN(lastDay)) return 'Dates not readable';
    const days = Math.round((lastDay - firstDay) / MILLISECONDS_IN_A_DAY) + 1;
    return plural(days, 'day');
  }

  statusChip(row: LeaveRow): StatusChip {
    switch (row.status) {
      case 'APPROVED':
        return { label: 'Sanctioned', tone: 'good' };
      case 'REJECTED':
        return { label: 'Not sanctioned', tone: 'risk' };
      case 'FIRST_APPROVED':
        return { label: 'Sanction pending', tone: 'warn' };
      case 'CANCELLED':
        return { label: 'Withdrawn', tone: 'neutral' };
      case 'REVOKED':
        return { label: 'Sanction revoked', tone: 'risk' };
      default:
        if (row.decision_change_kind === 'REOPENED') {
          return { label: 'Reopened · awaiting your decision', tone: 'warn' };
        }
        return { label: 'Awaiting your decision', tone: 'warn' };
    }
  }

  /** The grid's Approval chain cell, in one line. */
  chainSummary(row: LeaveRow): string {
    switch (row.status) {
      case 'APPROVED':
        return `Sanctioned by ${row.director_name || 'an approver'}`;
      case 'REJECTED':
        return `Refused by ${row.director_name || 'an approver'}`;
      case 'FIRST_APPROVED':
        return 'Signed once under the old chain → your decision completes it';
      case 'CANCELLED':
        return 'Withdrawn by the applicant';
      case 'REVOKED':
        return `Sanction revoked by ${row.decision_changed_by_name || 'an approver'}`;
      default:
        if (row.decision_change_kind === 'REOPENED') {
          return `Reopened by ${row.decision_changed_by_name || 'an approver'} → your decision`;
        }
        return 'Your one signature sanctions or refuses it';
    }
  }

  /** `director_note` is `second_note or first_note`; on a row signed once
   *  under the old chain it carries that earlier signer's remarks, which the
   *  office needs to read before completing it. Each state is named, because
   *  "Sanctioned — remarks" over a request that is not yet sanctioned is
   *  worse than no heading. */
  remarksLabel(row: LeaveRow): string {
    if (row.status === 'REJECTED') return 'Rejected — remarks';
    if (row.status === 'REVOKED') return 'Sanctioned — remarks, before it was revoked';
    if (row.status === 'FIRST_APPROVED') return 'Earlier signature — remarks';
    return 'Sanctioned — remarks';
  }

  /** Has anybody signed this form at all? A withdrawn request may never have
   *  been signed, and a submitted one certainly has not. */
  hasASignature(row: LeaveRow): boolean {
    return (
      row.status === 'FIRST_APPROVED' ||
      row.status === 'APPROVED' ||
      row.status === 'REJECTED' ||
      row.status === 'REVOKED'
    );
  }

  /** SUBMITTED has nothing decided, so any note on it would be nobody's. */
  hasApproverRemarks(row: LeaveRow): boolean {
    return !!row.director_note && row.status !== 'SUBMITTED';
  }

  /** What the confirm step promises, honestly: your one signature is the
   *  sanction, and it prints — name, time and your signature image, if you
   *  have uploaded one — in the PROGRAM DIRECTOR block of the paper. */
  signNote(row: LeaveRow): string {
    if (row.status === 'FIRST_APPROVED') {
      return 'An earlier signature is already on this form from before single-signature approval; yours completes the sanction and prints in the PROGRAM DIRECTOR block with the time.';
    }
    return 'Sanctions the leave. Your name, the time and your uploaded signature image print in the PROGRAM DIRECTOR block of the paper (add the image under Signature in the account menu).';
  }

  /** GET /api/leaves/{id}/paper.pdf — the sheet as a file, signatures drawn in. */
  paperUrl(row: LeaveRow): string {
    return `${environment.apiBase}/leaves/${row.id}/paper.pdf`;
  }

  selectValue(event: Event): string {
    const target = event.target as HTMLSelectElement;
    return target.value;
  }

  onRemarksInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.remarks.set(target.value);
    this.remarksError.set(null);
  }

  // ------------------------------------------------------- the decision --

  openApprove(): void {
    this.decisionMode.set('approve');
    this.remarksError.set(null);
  }

  openReject(): void {
    this.decisionMode.set('reject');
    this.remarksError.set(null);
  }

  cancelDecision(): void {
    this.decisionMode.set('idle');
    this.remarksError.set(null);
    this.resetChange();
  }

  // ------------------------------------------- changing a decision made --

  openChange(): void {
    const action = this.changeAction();
    if (action === null) return;
    this.resetChange();
    this.decisionMode.set(action);
  }

  onChangeReasonInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.changeReason.set(target.value);
    this.changeError.set(null);
  }

  onConfirmStartedChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.confirmStarted.set(target.checked);
    this.changeError.set(null);
  }

  async confirmChange(): Promise<void> {
    const request = this.selectedRequest();
    const mode = this.decisionMode();
    if (request === null || (mode !== 'revoke' && mode !== 'reopen')) return;
    const reason = this.changeReason().trim();
    if (reason.length === 0) {
      this.changeError.set('A reason is required. The applicant reads it.');
      return;
    }
    const startedLeave = mode === 'revoke' && this.selectedHasBegun();
    if (startedLeave && !this.confirmStarted()) {
      this.changeError.set('Tick the box to confirm revoking a leave that has already begun.');
      return;
    }
    this.deciding.set(true);
    this.error.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/leaves/${request.id}/change-decision`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: mode === 'revoke' ? 'REVOKE' : 'REOPEN',
          reason,
          confirm_started: startedLeave,
        }),
      });
      if (!response.ok) {
        this.changeError.set(await this.detailOf(response));
        return;
      }
      this.flash.set(
        mode === 'revoke'
          ? `Sanction revoked for ${request.requester_name}. They read your reason on their Leave Requests screen.`
          : `Reopened for ${request.requester_name}. It is back in Pending for a fresh decision.`,
      );
      this.clearSelection();
      await this.loadQueues();
    } catch {
      this.changeError.set('Could not reach the server.');
    } finally {
      this.deciding.set(false);
    }
  }

  private resetChange(): void {
    this.changeReason.set('');
    this.changeError.set(null);
    this.confirmStarted.set(false);
  }

  /** Today as YYYY-MM-DD in the browser's own zone, for comparing against a
   *  leave's ISO dates — never through `toISOString()`, which is UTC and is a
   *  day behind for the first five and a half hours of an Indian morning. */
  private todayIso(): string {
    const now = new Date();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    return `${now.getFullYear()}-${month}-${day}`;
  }

  async confirmApprove(): Promise<void> {
    const request = this.selectedRequest();
    if (request === null) return;
    const typedRemarks = this.remarks().trim();
    await this.decide(request, 'APPROVE', typedRemarks.length > 0 ? typedRemarks : null);
  }

  async confirmReject(): Promise<void> {
    const request = this.selectedRequest();
    if (request === null) return;
    const typedRemarks = this.remarks().trim();
    if (typedRemarks.length === 0) {
      this.remarksError.set('Remarks are required.');
      return;
    }
    await this.decide(request, 'REJECT', typedRemarks);
  }

  private async decide(
    row: LeaveRow,
    decision: 'APPROVE' | 'REJECT',
    note: string | null,
  ): Promise<void> {
    this.deciding.set(true);
    this.error.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/leaves/${row.id}/decision`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, note }),
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      const updated = (await response.json()) as LeaveRow;
      this.flash.set(this.decisionFlash(row, decision, updated));
      this.clearSelection();
      await this.loadQueues();
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.deciding.set(false);
    }
  }

  /** The server's status is the word: one signature sanctions since
   *  2026-09-16, and the fallback below is kept only for a response this
   *  screen has not been taught. */
  private decisionFlash(
    row: LeaveRow,
    decision: 'APPROVE' | 'REJECT',
    updated: LeaveRow,
  ): string {
    if (decision === 'REJECT') {
      return `Not sanctioned — ${row.requester_name} has your remarks.`;
    }
    if (updated.status === 'APPROVED') {
      return `Sanctioned and signed for ${row.requester_name}.`;
    }
    return `Your signature is recorded for ${row.requester_name} (status ${updated.status}).`;
  }

  /** FastAPI answers a schema refusal with `detail` as a LIST, and rendering
   *  that raw is how a form ends up saying "[object Object]". Its own sentence
   *  where there is one — it names what was wrong. */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail) && detail.length && typeof detail[0]?.msg === 'string') {
        return detail[0].msg;
      }
    } catch {
      /* fall through to the generic sentence */
    }
    return 'Could not record that decision.';
  }

  // ------------------------------------------------------------- loading --

  private rowsInTab(): LeaveRow[] {
    const tab = this.tab();
    if (tab === 'pending') return this.pending() ?? [];
    if (tab === 'cancelled') return this.cancelled() ?? [];
    if (tab === 'revoked') return this.revoked() ?? [];
    const wantedStatus = tab === 'approved' ? 'APPROVED' : 'REJECTED';
    return (this.history() ?? []).filter((row) => row.status === wantedStatus);
  }

  private applicantStep(row: LeaveRow): ChainStep {
    if (row.signed_at === null) {
      return { label: 'Applied', state: 'done', detail: 'Sent without a signature stamp.' };
    }
    return {
      label: 'Applied and signed',
      state: 'done',
      detail: `${row.requester_name} · ${this.stampOf(row.signed_at)}`,
    };
  }

  private sanctionStep(row: LeaveRow): ChainStep {
    const asWhat = this.signedAsLabel(row.second_signed_as || row.first_signed_as);
    const asPhrase = asWhat.length > 0 ? ` · as ${asWhat}` : '';
    if (row.status === 'APPROVED') {
      return {
        label: 'Sanction',
        state: 'done',
        detail: `${row.director_name || 'Approver'}${asPhrase} · ${this.stampOf(row.director_decided_at)}`,
      };
    }
    if (row.status === 'REJECTED') {
      return {
        label: 'Not sanctioned',
        state: 'refused',
        detail: `${row.director_name || 'Approver'}${asPhrase} · ${this.stampOf(row.director_decided_at)}`,
      };
    }
    if (row.status === 'REVOKED') {
      return {
        label: 'Sanctioned',
        state: 'done',
        detail: `${row.director_name || 'Approver'}${asPhrase} · ${this.stampOf(row.director_decided_at)}`,
      };
    }
    if (row.status === 'CANCELLED') {
      return {
        label: 'Withdrawn',
        state: 'refused',
        detail: 'The applicant took this request back before it was decided.',
      };
    }
    if (row.status === 'FIRST_APPROVED') {
      const earlier = this.signedAsLabel(row.first_signed_as);
      return {
        label: 'Sanction',
        state: 'active',
        detail: `Signed once${earlier ? ` as ${earlier}` : ''} before single-signature approval; your decision completes it.`,
      };
    }
    return { label: 'Sanction', state: 'active', detail: 'Your one signature decides it.' };
  }

  /** The change to a decision, as a step of the chain, or `null` when none
   *  was ever made. Only the latest change is on the row; the earlier ones are
   *  on the audit trail. */
  private changeStep(row: LeaveRow): ChainStep | null {
    if (row.decision_change_kind === null) return null;
    const who = row.decision_changed_by_name || 'An approver';
    const when = this.stampOf(row.decision_changed_at);
    if (row.decision_change_kind === 'REVOKED') {
      return { label: 'Sanction revoked', state: 'refused', detail: `${who} · ${when}` };
    }
    return { label: 'Reopened', state: 'done', detail: `${who} · ${when}` };
  }

  private stampOf(value: string | null): string {
    if (value === null) return 'time not recorded';
    const stamp = new Date(value);
    if (Number.isNaN(stamp.getTime())) return 'time not recorded';
    return stamp.toLocaleString(undefined, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  private async loadQueues(): Promise<void> {
    try {
      const [pendingResponse, historyResponse, cancelledResponse, revokedResponse] = await Promise.all([
        fetch(`${environment.apiBase}/leaves/pending`, { credentials: 'include' }),
        fetch(`${environment.apiBase}/leaves/history`, { credentials: 'include' }),
        // The withdrawn queue is a THIRD read and not a filter over the second:
        // `/history` without `?status=` answers the settled queue exactly as it
        // always has, and a CANCELLED row is in neither default queue.
        fetch(`${environment.apiBase}/leaves/history?status=CANCELLED`, {
          credentials: 'include',
        }),
        fetch(`${environment.apiBase}/leaves/history?status=REVOKED`, {
          credentials: 'include',
        }),
      ]);
      if (
        !pendingResponse.ok ||
        !historyResponse.ok ||
        !cancelledResponse.ok ||
        !revokedResponse.ok
      ) {
        this.error.set('Could not load the approvals queue.');
        this.pending.set([]);
        this.history.set([]);
        this.cancelled.set([]);
        this.revoked.set([]);
        return;
      }
      this.scope.set(this.readScope(pendingResponse) ?? this.readScope(historyResponse));
      this.pending.set((await pendingResponse.json()) as LeaveRow[]);
      this.history.set((await historyResponse.json()) as LeaveRow[]);
      this.cancelled.set((await cancelledResponse.json()) as LeaveRow[]);
      this.revoked.set((await revokedResponse.json()) as LeaveRow[]);
    } catch {
      this.error.set('Could not reach the server.');
      this.scope.set(null);
      this.pending.set([]);
      this.history.set([]);
      this.cancelled.set([]);
      this.revoked.set([]);
    }
  }

  /** The reach the server stated on this response, or `null` when it stated
   *  none. Readable because the SPA is same-origin through proxy.conf.json —
   *  a cross-origin fetch would need these three names on the CORS allowlist.
   *
   *  AN UNRECOGNISED WORD IS `null`, NOT A GUESS. The one thing this screen
   *  must never do is read a header it does not understand as "none" and tell
   *  an approver their grant reaches nobody. */
  private readScope(response: Response): ScopeReach | null {
    const word = response.headers.get(SCOPE_HEADER);
    if (word !== 'programme' && word !== 'narrowed' && word !== 'none') return null;
    return {
      word,
      colleges: this.scopeIds(response.headers.get(SCOPE_COLLEGES_HEADER)),
      departments: this.scopeIds(response.headers.get(SCOPE_DEPARTMENTS_HEADER)),
    };
  }

  private scopeIds(raw: string | null): string[] {
    if (raw === null) return [];
    return raw
      .split(',')
      .map((id) => id.trim())
      .filter((id) => id.length > 0);
  }
}
