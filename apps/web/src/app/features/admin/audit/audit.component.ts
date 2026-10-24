/**
 * Audit log — AuditLog.html, now reading the trail it always described.
 *
 * The approved board is `docs/redesign-2026-09/design/admin/AuditLog.html` and
 * the brief is `02-admin-console-spec.md` §21. Phase 2 drew this whole screen
 * over no rows, because `architecture_events.record_change` had been writing
 * `redesign_audit_events` for months and nothing in `apps/api-py/app/routers/`
 * could read a row back. `B2.7` landed that reader — `app/routers/audit.py` —
 * and this file is the screen wired onto it. What follows is what changed and,
 * more importantly, what deliberately did not.
 *
 * THREE ENDPOINTS, AND ALL THREE ARE READS.
 *
 *   GET /api/admin/audit              the filtered page: `items`, `page`,
 *                                     `page_size` and `total` — `total` being
 *                                     the WHOLE filtered set, which is what
 *                                     draws "1-50 of 412" and decides whether
 *                                     Next is live.
 *   GET /api/admin/audit/{id}         one event plus its `before` / `after` /
 *                                     `metadata`. The panel.
 *   GET /api/admin/audit/export.csv   the same filters as a file.
 *
 * There is no write endpoint on that router and there must never be one: an
 * audit trail a console can edit is not an audit trail. Nothing on this screen
 * posts anything.
 *
 * THE PAGING IS THE SERVER'S, SO AG GRID'S IS OFF. `[pagination]` is `false`
 * and the status bar carries the pager. The grid holds exactly the rows of one
 * server page; letting AG Grid paginate on top of that would slice fifty rows
 * into two client pages and label them "1 of 2" beside a server count of 412.
 *
 * THE ACTION AND TARGET LISTS ARE NOT INVENTED. `KNOWN_ACTIONS` and
 * `KNOWN_TARGET_TYPES` below are read out of the `record_change` call sites
 * themselves (enumerated against each one, and each entry says which module
 * writes it); anything else the loaded page actually contains is unioned in on
 * top. An option nobody ever wrote is a filter that always returns nothing,
 * and on this screen "no results" is read as "it did not happen".
 *
 * ONE ACTION VOCABULARY IS UNREACHABLE AND THAT IS A BACKEND DEFECT, NOT A
 * CHOICE HERE. `_filters` in `routers/audit.py` compares
 * `AuditEvent.action == action.strip().upper()`, but `routers/redesign.py`
 * writes its mentor-notebook actions in lower case (`created`, `updated`,
 * `published`, `archived`, `registered`). Upper-casing `created` yields
 * `CREATED`, which matches `interview_question` rows and never matches a
 * notebook row. Those five are therefore NOT offered in the Action select —
 * offering them would be the dead filter this screen must not have — and they
 * are listed in `UNFILTERABLE_LOWERCASE_ACTIONS` so the next reader finds the
 * reason rather than the omission. The rows themselves still list and still
 * open; only the Action filter cannot reach them.
 *
 * THE COLLEGE FILTER STAYS DISABLED, AND IT IS WAITING ON NO PHASE AT ALL.
 * See `AUDIT_COLLEGE_SCOPE_REASON`.
 *
 * THE GRID'S FIFTH COLUMN IS "Route", NOT THE BOARD'S "Summary". The endpoint
 * returns `route`, `request_id` and `correlation_id`; it returns no summary
 * sentence and there is nothing to compose one from without opening the event.
 * A column headed "Summary" filled with a URL path is a mislabel, and this is
 * the one screen where a heading that does not describe its cells is expensive.
 */

import { Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { AgGridAngular } from 'ag-grid-angular';
import type {
  CellClickedEvent,
  ColDef,
  GridApi,
  GridReadyEvent,
  ValueFormatterParams,
} from 'ag-grid-community';

import { environment } from '../../../../environments/environment';
import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { reepGridTheme, reepGridThemeCompact } from '../../../shared/grid/reep-grid-theme';

// =========================================================================
// the wire
// =========================================================================

/** `AuditRowOut` in `app/routers/audit.py`, verbatim. */
interface AuditRowWire {
  id: string;
  occurred_at: string;
  /** Null when the actor's account has been deleted — the FK is SET NULL and
   *  the event survives the person deliberately. */
  actor_user_id: string | null;
  actor_name: string | null;
  actor_email: string | null;
  actor_type: string;
  action: string;
  target_type: string;
  target_id: string;
  route: string | null;
  request_id: string | null;
  correlation_id: string | null;
}

/** `AuditDetailOut` — the row plus the pair the listing deliberately omits. */
interface AuditDetailWire extends AuditRowWire {
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
}

/** `AuditPageOut`. `total` is the size of the whole filtered set. */
interface AuditPageWire {
  items: AuditRowWire[];
  page: number;
  page_size: number;
  total: number;
}

/** One event as the grid reads it. Every field is already a string, so a null
 *  from the wire becomes a dash here rather than in five value formatters. */
export interface AuditEventRow {
  id: string;
  occurredAt: string;
  actorLabel: string;
  action: string;
  targetLabel: string;
  route: string;
  /** Kept off the columns and used by "Open the owning screen". */
  targetType: string;
  targetId: string;
}

// =========================================================================
// constants
// =========================================================================

/** `DEFAULT_PAGE_SIZE` / `MAX_PAGE_SIZE` in `app/routers/audit.py`. Asking for
 *  more than the ceiling is a 422, so the selector offers the ceiling and
 *  stops there. */
const EVENTS_PER_PAGE = 50;
const PAGE_SIZE_CHOICES = [25, 50, 100, 200];

/** `MAX_EXPORT_ROWS` in `app/routers/audit.py`. Over this the endpoint REFUSES
 *  with a 413 rather than truncating — and a refusal reached through an
 *  `<a download>` lands on the officer's disk named `.csv`, which the browser
 *  records as a successful download. So the count is checked here first and
 *  the link is disabled with the reason on it, exactly as the Exports screen
 *  guards its own Main-Admin-only card. */
const MAX_EXPORT_ROWS = 10_000;

/** The date ranges the board's Range pill offers. `from` is sent as an ISO
 *  instant; `to` is left open, because "up to now" is what every one of these
 *  means and a second bound would exclude an event written mid-read. */
interface RangeChoice {
  key: string;
  label: string;
  /** Null is "no lower bound" — the whole trail. */
  days: number | null;
}
const RANGE_CHOICES: RangeChoice[] = [
  { key: '1d', label: 'Last 24 hours', days: 1 },
  { key: '7d', label: 'Last 7 days', days: 7 },
  { key: '30d', label: 'Last 30 days', days: 30 },
  { key: '90d', label: 'Last 90 days', days: 90 },
  { key: 'all', label: 'All time', days: null },
];
const DEFAULT_RANGE_KEY = '7d';

/**
 * Every action `record_change` is actually called with, read off the call
 * sites rather than imagined. Grouped by the module that writes it so the next
 * person to add one knows where to look — and so that a value that disappears
 * from the API can be struck off here rather than lingering as a filter that
 * quietly matches nothing.
 */
const KNOWN_ACTIONS: string[] = [
  // routers/registration.py
  'APPROVED',
  'REJECTED',
  'REOPENED',
  // routers/governance.py + routers/admin.py + grant_access.py
  'GRANTED',
  'REVOKED',
  'EXTENDED',
  'CREATED',
  'MEMBER_ADDED',
  'MEMBER_REMOVED',
  'FEATURE_ENABLED',
  'FEATURE_DISABLED',
  'FEATURE_CLEARED',
  // routers/admin_faculty.py + routers/auth.py
  'CREATE',
  'UPDATE',
  'DISABLE',
  'ENABLE',
  'SIGN_OUT_EVERYWHERE',
  'GOOGLE_UNLINK',
  'NOTIFICATION_PREFS_SET',
  // routers/swoc.py + routers/interview_bank.py
  'DELETE',
  'UPDATED',
  'DELETED',
  // routers/console.py + routers/admin_students.py
  'MENTOR_ASSIGNED',
  'STUDENTS_MOVE',
  'STUDENTS_MENTOR',
  'STUDENTS_STAGE',
  'STUDENTS_SEMESTER',
  'STUDENTS_DEPARTMENT',
  // exports.py + routers/audit.py — a download is an act and is recorded
  'EXPORT_DOWNLOAD',
  'EXPORTED',
];

/**
 * The five `routers/redesign.py` writes in lower case. They are REAL actions on
 * real rows and they are not offered, because `_filters` upper-cases whatever
 * the select sends and no stored value would match. Named here so the gap is a
 * documented backend defect rather than an oversight in this list.
 */
const UNFILTERABLE_LOWERCASE_ACTIONS = [
  'created',
  'updated',
  'published',
  'archived',
  'registered',
];

/** Every `entity_type` the call sites write. `target_type` is compared exactly
 *  (no case coercion), so these are sent as they are stored. */
const KNOWN_TARGET_TYPES: string[] = [
  'access_group',
  'audit_export',
  'capability_grant',
  'cohort',
  'export',
  'faculty',
  'feature_override',
  'interview_question',
  'mentor_notebook_action',
  'mentor_notebook_attachment',
  'mentor_notebook_entry',
  'registration',
  'roster',
  'student',
  'swoc_entry',
  'user',
];

/**
 * Where a target of each kind is administered, for "Open the owning screen".
 *
 * NOT "open the record", which is what the board's button said: only `student`
 * has a console route that addresses one row (`/admin/students/:id`). Every
 * other kind is administered from a screen that lists its type, so the button
 * says what it does. A kind with no console screen at all — the mentor's
 * private notebook — maps to nothing and the button is disabled with that
 * reason on it, rather than navigating somewhere that does not hold the record.
 */
const TARGET_SCREENS: Record<string, { path: string; label: string; byId?: boolean }> = {
  access_group: { path: '/admin/governance', label: 'Who can do what' },
  audit_export: { path: '/admin/exports', label: 'Download reports' },
  capability_grant: { path: '/admin/governance', label: 'Who can do what' },
  cohort: { path: '/admin/institution', label: 'College structure' },
  export: { path: '/admin/exports', label: 'Download reports' },
  faculty: { path: '/admin/faculty', label: 'Faculty' },
  feature_override: { path: '/admin/governance/features', label: 'Feature switches' },
  interview_question: { path: '/admin/interview-questions', label: 'Interview questions' },
  registration: { path: '/admin/registrations', label: 'New applications' },
  roster: { path: '/admin/students', label: 'Students & batches' },
  student: { path: '/admin/students', label: 'the student', byId: true },
  swoc_entry: { path: '/admin/swoc', label: 'SWOC notes' },
  user: { path: '/admin/faculty', label: 'Faculty' },
};

/** Rows per page in the comfortable and the compact density, as §4 sets them
 *  and as the other console grids already use. */
const COMFORTABLE_ROW_HEIGHT_PX = 40;
const COMPACT_ROW_HEIGHT_PX = 36;

/** How long the clipboard confirmation stays in its live region. A `role`
 *  `status` that never clears reads, minutes later, as a fresh confirmation. */
const COPY_NOTE_MS = 4000;

/** "10 Sep 09:40" — local, human, and no date library in the bundle. */
function formatOccurredAt(params: ValueFormatterParams<AuditEventRow, string>): string {
  if (!params.value) return '—';
  const when = new Date(params.value);
  if (Number.isNaN(when.getTime())) return '—';
  return when.toLocaleString(undefined, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

@Component({
  selector: 'app-admin-audit-log',
  standalone: true,
  imports: [AgGridAngular],
  templateUrl: './audit.component.html',
  styleUrl: './audit.component.scss',
})
export class AdminAuditLogComponent {
  private readonly router = inject(Router);
  private gridApi: GridApi<AuditEventRow> | null = null;
  private copyNoteTimer: ReturnType<typeof setTimeout> | null = null;

  readonly pageSizes = PAGE_SIZE_CHOICES;
  readonly rangeChoices = RANGE_CHOICES;
  readonly maxExportRows = MAX_EXPORT_ROWS;
  readonly unfilterableActions = UNFILTERABLE_LOWERCASE_ACTIONS;

  // ----------------------------------------------------------- the query --

  /** A user id OR an email address — `_filters` resolves an address to an id
   *  before it compares, so the pill can offer people by the name they are
   *  known by. Empty is "anyone". */
  readonly actorFilter = signal('');
  readonly actionFilter = signal('');
  readonly targetTypeFilter = signal('');
  readonly rangeKey = signal(DEFAULT_RANGE_KEY);

  readonly page = signal(1);
  readonly pageSize = signal(EVENTS_PER_PAGE);

  readonly rows = signal<AuditEventRow[]>([]);
  readonly total = signal(0);
  readonly isLoading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * The actors the console has actually seen, accumulated across the pages
   * loaded in this session. There is no "distinct actors" endpoint and this
   * screen will not call the faculty directory to invent one: an actor who has
   * never written a row is a filter that returns nothing, and an actor whose
   * account has since been deleted still has rows and would be missing from
   * any directory. The key is what gets sent — an address where there is one,
   * the user id otherwise.
   */
  private readonly actorsSeen = signal<Map<string, string>>(new Map());

  readonly actorOptions = computed(() =>
    [...this.actorsSeen().entries()]
      .map(([key, label]) => ({ key, label }))
      .sort((a, b) => a.label.localeCompare(b.label)),
  );

  /** The vocabulary read off the call sites, plus anything this page actually
   *  contains that is not in it — but only if it survives the endpoint's
   *  `.upper()`, because a lower-case option could never match. */
  readonly actionOptions = computed(() => {
    const seen = new Set(KNOWN_ACTIONS);
    for (const row of this.rows()) {
      if (row.action && row.action === row.action.toUpperCase()) seen.add(row.action);
    }
    return [...seen].sort();
  });

  readonly targetTypeOptions = computed(() => {
    const seen = new Set(KNOWN_TARGET_TYPES);
    for (const row of this.rows()) {
      if (row.targetType) seen.add(row.targetType);
    }
    return [...seen].sort();
  });

  // --------------------------------------------------- what the pills say --

  readonly actorLabel = computed(() => {
    const key = this.actorFilter();
    if (!key) return 'Anyone';
    return this.actorsSeen().get(key) ?? key;
  });

  readonly actionLabel = computed(() => this.actionFilter() || 'All actions');
  readonly targetTypeLabel = computed(() => this.targetTypeFilter() || 'All types');
  readonly rangeLabel = computed(
    () => RANGE_CHOICES.find((choice) => choice.key === this.rangeKey())?.label ?? 'Last 7 days',
  );

  readonly hasFilters = computed(
    () =>
      this.actorFilter() !== '' ||
      this.actionFilter() !== '' ||
      this.targetTypeFilter() !== '' ||
      this.rangeKey() !== DEFAULT_RANGE_KEY,
  );

  // ------------------------------------------------------------ the pager --

  readonly pageCount = computed(() => {
    const size = this.pageSize();
    return Math.max(1, Math.ceil(this.total() / size));
  });

  readonly isFirstPage = computed(() => this.page() <= 1);
  readonly isLastPage = computed(() => this.page() >= this.pageCount());

  /** "1-50 of 412", or the honest empty sentence. Never a bare `0`. */
  readonly rowRangeLabel = computed(() => {
    const total = this.total();
    if (total === 0) return 'No events';
    const first = (this.page() - 1) * this.pageSize() + 1;
    const last = Math.min(this.page() * this.pageSize(), total);
    return `${first}–${last} of ${total}`;
  });

  readonly pagerLabel = computed(() => `Page ${this.page()} of ${this.pageCount()}`);

  // ------------------------------------------------------------ the export --

  /** Same filters as the listing, and deliberately no `page` — the file is the
   *  whole filtered set, which is why the row cap below matters. */
  readonly exportUrl = computed(
    () => `${environment.apiBase}/admin/audit/export.csv?${this.queryString()}`,
  );

  readonly exportRefused = computed(() => this.total() > MAX_EXPORT_ROWS);

  readonly exportReason = computed(() => {
    if (this.total() === 0) return 'There are no events in this range to export.';
    if (this.exportRefused()) {
      return (
        `This query matches ${this.total()} events and at most ${MAX_EXPORT_ROWS} can be ` +
        'exported at once. Narrow the range or the filters.'
      );
    }
    return '';
  });

  readonly exportDisabled = computed(() => this.total() === 0 || this.exportRefused());

  // ------------------------------------------------------------ the panel --

  readonly openEventId = signal<string | null>(null);
  readonly openEvent = signal<AuditDetailWire | null>(null);
  readonly isOpening = signal(false);
  readonly panelError = signal<string | null>(null);
  readonly copyNote = signal<string | null>(null);

  /** `before` and `after` are nullable and the two nulls mean different things:
   *  a creation has no before, a deletion has no after, and an event that
   *  changed no fields has neither. None of the three is an empty object, and
   *  drawing `{}` would say "these fields were all blanked". */
  readonly beforeText = computed(() => this.snapshotText(this.openEvent()?.before ?? null));
  readonly afterText = computed(() => this.snapshotText(this.openEvent()?.after ?? null));

  readonly metadataText = computed(() => {
    const meta = this.openEvent()?.metadata;
    if (!meta || Object.keys(meta).length === 0) return 'Nothing recorded';
    return JSON.stringify(meta, null, 2);
  });

  /** Where "Open the owning screen" would go, or null when no console screen
   *  administers that kind of record. */
  readonly owningScreen = computed(() => {
    const event = this.openEvent();
    if (event === null) return null;
    return TARGET_SCREENS[event.target_type] ?? null;
  });

  readonly owningScreenLabel = computed(() => {
    const screen = this.owningScreen();
    return screen === null ? 'Open the owning screen' : `Open ${screen.label}`;
  });

  // ------------------------------------------------------------- the grid --

  readonly isCompact = signal(false);
  readonly columnsPanelOpen = signal(false);
  private readonly hiddenColumnIds = signal<Set<string>>(new Set());

  /** Narrows the fifty rows the server sent, and says so on itself. The
   *  endpoint has no free-text search — only `target_id`, exact — so this box
   *  cannot be a search of the trail and is not labelled as one. */
  readonly quickFilter = signal('');

  readonly gridTheme = computed(() => (this.isCompact() ? reepGridThemeCompact : reepGridTheme));
  readonly rowHeight = computed(() =>
    this.isCompact() ? COMPACT_ROW_HEIGHT_PX : COMFORTABLE_ROW_HEIGHT_PX,
  );

  readonly columns: ColDef<AuditEventRow>[] = [
    {
      colId: 'occurredAt',
      field: 'occurredAt',
      headerName: 'When',
      flex: 1.7,
      minWidth: 150,
      valueFormatter: formatOccurredAt,
    },
    { colId: 'actorLabel', field: 'actorLabel', headerName: 'Actor', flex: 1.9, minWidth: 160 },
    { colId: 'action', field: 'action', headerName: 'Action', flex: 1.8, minWidth: 150 },
    { colId: 'targetLabel', field: 'targetLabel', headerName: 'Target', flex: 2.3, minWidth: 180 },
    { colId: 'route', field: 'route', headerName: 'Route', flex: 2.3, minWidth: 180 },
  ];

  /** Everything but When, which is the one column a trail is unreadable
   *  without. */
  readonly toggleableColumns = [
    { id: 'actorLabel', label: 'Actor' },
    { id: 'action', label: 'Action' },
    { id: 'targetLabel', label: 'Target' },
    { id: 'route', label: 'Route' },
  ];

  readonly defaultColumn: ColDef<AuditEventRow> = {
    sortable: false,
    resizable: true,
    suppressMovable: true,
  };

  /**
   * SORTING IS OFF, and that is not an omission. The server orders the trail
   * newest first with `id` as the tiebreaker, and it pages. A click that
   * re-sorted the fifty rows on screen would produce a listing that is ordered
   * within a page and unordered across them — which on an audit log reads as a
   * chronology and is not one.
   */
  readonly rowId = (params: { data: AuditEventRow }): string => params.data.id;

  readonly emptyOverlay = computed(() => {
    const message = this.hasFilters()
      ? 'No events match these filters. Widen the range or clear a filter — older events are still here.'
      : 'No events recorded yet.';
    return `<span style="color:var(--muted);font-size:12.5px">${message}</span>`;
  });

  constructor() {
    // AG Grid 33+ refuses to draw until its modules are registered, and fails
    // as an empty rectangle rather than an exception (shared/grid docstring).
    registerReepGrid();
    void this.load();
  }

  // =========================================================================
  // loading
  // =========================================================================

  private queryString(): string {
    const params = new URLSearchParams();
    const actor = this.actorFilter();
    if (actor) params.set('actor', actor);
    const action = this.actionFilter();
    if (action) params.set('action', action);
    const targetType = this.targetTypeFilter();
    if (targetType) params.set('target_type', targetType);
    const from = this.rangeFrom();
    // The query parameter is literally named `from` — it is a Python keyword,
    // so the handler declares `from_` with `alias="from"`.
    if (from !== null) params.set('from', from);
    return params.toString();
  }

  private rangeFrom(): string | null {
    const choice = RANGE_CHOICES.find((entry) => entry.key === this.rangeKey());
    if (!choice || choice.days === null) return null;
    const since = new Date(Date.now() - choice.days * 24 * 60 * 60 * 1000);
    return since.toISOString();
  }

  async load(): Promise<void> {
    this.isLoading.set(true);
    this.error.set(null);
    const query = this.queryString();
    const path =
      `/admin/audit?page=${this.page()}&page_size=${this.pageSize()}` +
      (query ? `&${query}` : '');
    try {
      const response = await fetch(`${environment.apiBase}${path}`, { credentials: 'include' });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        this.rows.set([]);
        this.total.set(0);
        return;
      }
      const body = (await response.json()) as AuditPageWire;
      this.rows.set(body.items.map((item) => this.toRow(item)));
      this.total.set(body.total);
      this.page.set(body.page);
      this.pageSize.set(body.page_size);
      this.rememberActors(body.items);
    } catch {
      this.error.set('The audit trail could not be read. The server could not be reached.');
      this.rows.set([]);
      this.total.set(0);
    } finally {
      this.isLoading.set(false);
    }
  }

  private toRow(item: AuditRowWire): AuditEventRow {
    return {
      id: item.id,
      occurredAt: item.occurred_at,
      actorLabel: this.actorNameOf(item),
      action: item.action,
      targetLabel: `${item.target_type} · ${item.target_id}`,
      route: item.route ?? '—',
      targetType: item.target_type,
      targetId: item.target_id,
    };
  }

  /** A deleted account is a stated fact, not a blank: the FK is SET NULL and
   *  the event outlives the person on purpose. */
  private actorNameOf(item: AuditRowWire): string {
    if (item.actor_name) return item.actor_name;
    if (item.actor_email) return item.actor_email;
    if (item.actor_user_id) return item.actor_user_id;
    return 'Account deleted';
  }

  private rememberActors(items: AuditRowWire[]): void {
    const next = new Map(this.actorsSeen());
    for (const item of items) {
      const key = item.actor_email ?? item.actor_user_id;
      if (!key) continue;
      const name = item.actor_name;
      next.set(key, name && item.actor_email ? `${name} (${item.actor_email})` : (name ?? key));
    }
    this.actorsSeen.set(next);
  }

  /** The server's own sentence where there is one. FastAPI answers a schema
   *  error with `detail` as a LIST, which rendered raw says "[object Object]". */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === 'string') {
        return detail[0].msg;
      }
    } catch {
      /* fall through to the status */
    }
    if (response.status === 403) {
      return 'The audit trail is readable by the Main Admin only.';
    }
    return `The request was refused (${response.status}).`;
  }

  // =========================================================================
  // the filters
  // =========================================================================

  /** Every filter change starts the listing again at page 1: staying on page 7
   *  of a narrower query shows an empty grid beside a non-zero total. */
  private refilter(): void {
    this.page.set(1);
    void this.load();
  }

  setActor(value: string): void {
    this.actorFilter.set(value);
    this.refilter();
  }

  setAction(value: string): void {
    this.actionFilter.set(value);
    this.refilter();
  }

  setTargetType(value: string): void {
    this.targetTypeFilter.set(value);
    this.refilter();
  }

  setRange(value: string): void {
    this.rangeKey.set(value);
    this.refilter();
  }

  clearFilters(): void {
    this.actorFilter.set('');
    this.actionFilter.set('');
    this.targetTypeFilter.set('');
    this.rangeKey.set(DEFAULT_RANGE_KEY);
    this.refilter();
  }

  setPageSize(size: number): void {
    this.pageSize.set(size);
    this.page.set(1);
    void this.load();
  }

  goToPreviousPage(): void {
    if (this.isFirstPage()) return;
    this.page.update((page) => page - 1);
    void this.load();
  }

  goToNextPage(): void {
    if (this.isLastPage()) return;
    this.page.update((page) => page + 1);
    void this.load();
  }

  selectValue(event: Event): string {
    return (event.target as HTMLSelectElement).value;
  }

  numberValue(event: Event): number {
    return Number((event.target as HTMLSelectElement).value);
  }

  onQuickFilterInput(event: Event): void {
    this.quickFilter.set((event.target as HTMLInputElement).value);
  }

  // =========================================================================
  // the grid's own chrome
  // =========================================================================

  onGridReady(event: GridReadyEvent<AuditEventRow>): void {
    this.gridApi = event.api;
  }

  onCellClicked(event: CellClickedEvent<AuditEventRow>): void {
    if (!event.data) return;
    void this.openDetail(event.data.id);
  }

  toggleDensity(): void {
    this.isCompact.update((compact) => !compact);
    this.gridApi?.resetRowHeights();
  }

  toggleColumnsPanel(): void {
    this.columnsPanelOpen.update((open) => !open);
  }

  isColumnVisible(columnId: string): boolean {
    return !this.hiddenColumnIds().has(columnId);
  }

  toggleColumn(columnId: string): void {
    const wasVisible = this.isColumnVisible(columnId);
    this.hiddenColumnIds.update((hidden) => {
      const next = new Set(hidden);
      if (wasVisible) next.add(columnId);
      else next.delete(columnId);
      return next;
    });
    this.gridApi?.setColumnsVisible([columnId], !wasVisible);
  }

  // =========================================================================
  // the event panel
  // =========================================================================

  async openDetail(eventId: string): Promise<void> {
    this.openEventId.set(eventId);
    this.isOpening.set(true);
    this.panelError.set(null);
    this.copyNote.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/admin/audit/${eventId}`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.openEvent.set(null);
        this.panelError.set(await this.detailOf(response));
        return;
      }
      this.openEvent.set((await response.json()) as AuditDetailWire);
    } catch {
      this.openEvent.set(null);
      this.panelError.set('That event could not be read. The server could not be reached.');
    } finally {
      this.isOpening.set(false);
    }
  }

  closeDetail(): void {
    this.openEventId.set(null);
    this.openEvent.set(null);
    this.panelError.set(null);
    this.copyNote.set(null);
  }

  private snapshotText(snapshot: Record<string, unknown> | null): string {
    if (snapshot === null) return 'Nothing recorded';
    if (Object.keys(snapshot).length === 0) return 'Nothing recorded';
    return JSON.stringify(snapshot, null, 2);
  }

  occurredAtLabel(): string {
    const event = this.openEvent();
    if (event === null) return '—';
    const when = new Date(event.occurred_at);
    if (Number.isNaN(when.getTime())) return '—';
    return when.toLocaleString();
  }

  actorPanelLabel(): string {
    const event = this.openEvent();
    return event === null ? '—' : this.actorNameOf(event);
  }

  /** The whole event as the endpoint returned it — the pair included. It is
   *  the browser's clipboard and nothing leaves the machine. */
  async copyEventJson(): Promise<void> {
    const event = this.openEvent();
    if (event === null) return;
    const text = JSON.stringify(event, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      this.flashCopyNote('Event JSON copied to the clipboard.');
    } catch {
      this.panelError.set(
        'This browser refused the clipboard. Select the fields you need and copy them by hand.',
      );
    }
  }

  private flashCopyNote(message: string): void {
    this.copyNote.set(message);
    if (this.copyNoteTimer !== null) clearTimeout(this.copyNoteTimer);
    this.copyNoteTimer = setTimeout(() => this.copyNote.set(null), COPY_NOTE_MS);
  }

  openOwningScreen(): void {
    const event = this.openEvent();
    const screen = this.owningScreen();
    if (event === null || screen === null) return;
    void this.router.navigate(screen.byId ? [screen.path, event.target_id] : [screen.path]);
  }
}
