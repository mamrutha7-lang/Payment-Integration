/**
 * Colleges — one card per college, and the Add-college card above them.
 *
 * ONE CARD PER COLLEGE, NOT A ROW IN A TABLE (2026-09-15). The screen used to
 * be a four-column grid with a status bar, a selection that only re-titled an
 * explanatory card, and a 380px side panel for the form. It is now the setup
 * screen's shape: each college is a card carrying the facts a stored value can
 * answer — code, campus, department count, email domains, college admin,
 * contact, status — and two buttons: "Open" (College structure, with this
 * college picked) and "Continue setup" (Set up a college, with this college
 * loaded). The card that described what a college admin may and may not do
 * was prose about the capability catalogue, which Who can do what already
 * lists, and it is gone with the selection that titled it.
 *
 * THIS SCREEN CREATES NOTHING (2026-09-15). It carried its own Add-college
 * form beside the header's "Set up a college" button — two buttons that both
 * created a college, and the owner named the repetition. The setup flow's
 * step 1 now takes every field the form took (code, name, campus, contact,
 * email domains), so the form is gone and the one thing it did that the flow
 * cannot — appointing a college admin — is an action on each card instead,
 * which also makes it possible for a college that already exists rather than
 * only at the moment of creation.
 *
 * DELETE IS HERE (2026-09-16). "Delete college…" on each card opens the
 * shared Delete dialog against `GET/POST /api/admin/colleges/{id}/delete-plan`
 * and `/delete`: the structure (departments, courses, specializations,
 * batches and the configuration hung on them) goes for good, behind the code
 * emailed to the office. The server REFUSES while anybody is filed under the
 * college — students, faculty, open applications — and the dialog shows that
 * refusal in the server's words with the button off. Main Admin only, like
 * every delete. The bulk form ("every college except this one") is
 * `python -m app.purge_colleges --keep <code>`, on the Ops task menu.
 *
 * `PATCH /api/admin/colleges/{id}` exists and NOTHING here reaches it: renaming
 * a college and archiving one are API-only from this screen. The DOMAINS are
 * edited on `features/admin/institution`, on the college it has picked,
 * through that same PATCH; this screen only reads them back on the card. Two
 * screens, one column, one writer — which is only safe while they agree about
 * what an empty list means, so both say it in the same words.
 *
 *   - registered email domains (`B1.1`) are `colleges.email_domains`, carried
 *     on `CollegeOut` and accepted by `CollegeIn`. An EMPTY list is not "nobody
 *     may join" — `app/institution_domains.py` falls back to the deployment's
 *     own `provisionable_email_domains` — so the card says "Deployment list"
 *     rather than showing a dash, which would read as a fence that admits no
 *     one.
 *   - the college admin (`B1.3`) is a FACULTY account holding the eleven scoped
 *     `admin.*` grants `COLLEGE_ADMIN_CAPABILITIES` names. `GET` and `POST
 *     /api/admin/colleges/{id}/admins` read and write them, and the card's
 *     "Appoint" form is a picker over `GET /api/admin/faculty` plus the reason
 *     the endpoint records.
 *
 * BOTH ADMIN ENDPOINTS ARE GATED ON `admin.governance`, NOT ON THIS SCREEN'S
 * `admin.institution` (`require_governance` in `app/routers/governance.py`),
 * and that is deliberate on the API's side: who holds what is Governance's
 * subject, so renaming a department must not also read the access map. A
 * granted faculty member therefore reaches this screen and cannot see or write
 * the college admin. The client checks `session.capabilities` to decide
 * whether to ASK — a filter that keeps a 403 off the screen, never an
 * authorisation; the API refuses regardless.
 *
 */

import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import {
  AdminDeleteDialogComponent,
  type DeleteOutcome,
  type DeleteTarget,
} from '../../../shared/admin-delete-dialog/admin-delete-dialog.component';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';

/** `CollegeOut` in `app/routers/admin.py`, snake_case exactly as it arrives. */
interface CollegeOut {
  id: string;
  code: string;
  name: string;
  campus: string | null;
  contact: string | null;
  status: string;
  department_count: number;
  /** B1.1. Empty means the deployment's own list applies — never "nobody". */
  email_domains: string[];
}

/** `CollegeAdminGrantOut` — one of the eleven functions, and where it stands. */
interface CollegeAdminGrantOut {
  capability: string;
  label: string;
  /** `active` or `pending_approval`. Seven of the thirteen carry PII and wait
   *  when a DEPUTY appoints (B2.4); the Main Admin's appointment is live at
   *  once (2026-09-16). */
  approval_state: string;
  grant_id: string;
}

/** `CollegeAdminOut` — a faculty account running one college. */
interface CollegeAdminOut {
  user_id: string;
  name: string;
  email: string;
  department_id: string | null;
  capabilities: CollegeAdminGrantOut[];
  /** Keys of the set this person does NOT hold for this college. */
  missing: string[];
}

/** `AdminFacultyRowOut` in `app/routers/admin_faculty.py`, the fields the
 *  picker needs. A disabled account is listed and LABELLED rather than hidden:
 *  the API will accept it, so hiding it would leave "why is X not in the list"
 *  unanswerable on the screen that asks the question. */
interface FacultyRow {
  user_id: string;
  name: string;
  email: string;
  department: string | null;
  disabled_at: string | null;
}

type ScreenState = 'loading' | 'ready' | 'error';

/** Whether the college-admin column could be read at all, and how it went.
 *  `off` is the honest state for a caller who does not hold `admin.governance`
 *  — nothing was asked, so nothing is claimed. */
type AdminsState = 'off' | 'loading' | 'ready' | 'error';

/** `MIN_REASON_CHARS` in `app/routers/governance.py`. Checked here so the card
 *  can say why "Appoint" is not available before the request is sent. The
 *  server checks it again and is the authority. */
const MIN_REASON_CHARS = 20;

/** `require_governance` (`app/routers/governance.py`) gates both college-admin
 *  endpoints. Held here to decide whether to ASK, never whether to allow. */
const GOVERNANCE_CAPABILITY = 'admin.governance';

/** `STATUS_ACTIVE` / `STATUS_DRAFT` / `STATUS_ARCHIVED` in
 *  `app/models/institution.py`, in the words the console uses. */
const COLLEGE_STATUS_LABELS: Record<string, string> = {
  ACTIVE: 'Active',
  DRAFT: 'Draft',
  ARCHIVED: 'Archived',
};

/** Status is text AND colour, never colour alone — these are the chip tones. */
const COLLEGE_STATUS_TONES: Record<string, string> = {
  ACTIVE: 'good',
  DRAFT: 'neutral',
  ARCHIVED: 'warn',
};

/** How a college admin's eleven functions are summarised in one table cell. */
interface AdminSummary {
  label: string;
  tone: string;
}

@Component({
  selector: 'app-admin-colleges',
  standalone: true,
  imports: [RouterLink, PluralPipe, AdminDeleteDialogComponent],
  templateUrl: './colleges.component.html',
  styleUrl: './colleges.component.scss',
})
export class AdminCollegesComponent implements OnInit {
  private readonly auth = inject(AuthService);

  readonly minReasonChars = MIN_REASON_CHARS;

  readonly state = signal<ScreenState>('loading');
  readonly loadError = signal<string | null>(null);
  readonly colleges = signal<CollegeOut[]>([]);

  readonly statusFilter = signal<string>('all');
  readonly campusFilter = signal<string>('all');

  /** B1.3. College id -> the faculty accounts running it. Only ever populated
   *  from a real response; a college with no entry is not "nobody", which is
   *  what `adminsState` is for. */
  readonly collegeAdmins = signal<Record<string, CollegeAdminOut[]>>({});
  readonly adminsState = signal<AdminsState>('off');
  readonly adminsError = signal<string | null>(null);

  /** The card whose "Appoint" form is open, and the write in flight. */
  readonly appointingCollegeId = signal<string | null>(null);
  readonly appointBusy = signal(false);
  readonly appointNote = signal<string | null>(null);
  readonly appointError = signal<string | null>(null);

  /** The faculty account to appoint as the open card's admin, and the reason
   *  the appointment records. Both belong to the form, not to the college. */
  readonly appointUserId = signal<string>('');
  readonly appointReason = signal<string>('');
  readonly faculty = signal<FacultyRow[]>([]);
  readonly facultyError = signal<string | null>(null);
  readonly facultyLoading = signal(false);

  /** The faculty list behind the admin picker is Main-Admin-only on the API. */
  readonly isMainAdmin = computed(() => this.auth.session()?.role === 'ADMIN');

  /** The college the Delete dialog is open for (2026-09-16), or null. */
  readonly deletingCollege = signal<DeleteTarget | null>(null);
  readonly deleteNote = signal<string | null>(null);

  /** Whether to ASK for the college-admin data at all. A client-side capability
   *  check is a filter and never a gate — `require_governance` refuses the
   *  request regardless — but asking anyway would paint the column with a row
   *  of 403s for a reader who is doing nothing wrong. */
  readonly canReadCollegeAdmins = computed(
    () => this.auth.session()?.capabilities?.includes(GOVERNANCE_CAPABILITY) ?? false,
  );

  /** The campuses actually on record — the board's second filter, built from
   *  the data rather than from a list of states nothing stores. */
  readonly campusesOnRecord = computed(() => {
    const campuses = new Set<string>();
    for (const college of this.colleges()) {
      if (college.campus) {
        campuses.add(college.campus);
      }
    }
    return [...campuses].sort();
  });

  /** The status filter as four buttons — one press, no menu. */
  readonly statusOptions: { value: string; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'ACTIVE', label: 'Active' },
    { value: 'DRAFT', label: 'Draft' },
    { value: 'ARCHIVED', label: 'Archived' },
  ];

  readonly activeCount = computed(
    () => this.colleges().filter((college) => college.status === 'ACTIVE').length,
  );

  readonly visibleColleges = computed(() => {
    const status = this.statusFilter();
    const campus = this.campusFilter();
    return this.colleges().filter((college) => {
      const statusMatches = status === 'all' || college.status === status;
      const campusMatches = campus === 'all' || college.campus === campus;
      return statusMatches && campusMatches;
    });
  });

  readonly campusFilterLabel = computed(() => {
    const campus = this.campusFilter();
    if (campus === 'all') {
      return 'All';
    }
    return campus;
  });

  /** The picker is offered only where BOTH endpoints behind it can answer: the
   *  faculty list (Main Admin) and the appointment (`admin.governance`). */
  readonly canAppointAdmin = computed(() => this.canReadCollegeAdmins() && this.isMainAdmin());

  readonly appointReasonLength = computed(() => this.appointReason().trim().length);

  readonly appointReasonReady = computed(
    () => this.appointReasonLength() >= MIN_REASON_CHARS,
  );

  readonly canAppoint = computed(
    () => this.appointUserId() !== '' && this.appointReasonReady() && !this.appointBusy(),
  );

  ngOnInit(): void {
    void this.loadColleges();
  }

  async loadColleges(): Promise<void> {
    this.state.set('loading');
    this.loadError.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/admin/colleges`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.loadError.set(await this.detailOf(response));
        this.state.set('error');
        return;
      }
      const colleges = (await response.json()) as CollegeOut[];
      this.colleges.set(colleges);
      this.state.set('ready');
      void this.loadCollegeAdmins(colleges);
    } catch {
      this.loadError.set('The server could not be reached. No college was loaded.');
      this.state.set('error');
    }
  }

  /**
   * B1.3. Who runs each college, from `GET /api/admin/colleges/{id}/admins`.
   *
   * ONE REQUEST PER COLLEGE, because that is the endpoint's shape: it answers
   * for one college and there is no list-wide form of it. A tenant list is a
   * handful of rows, so the fan-out is bounded by the thing being rendered
   * rather than by a page size; the alternative — leaving the column empty
   * while the endpoint exists — is the dash that means two different things
   * this screen was written to avoid.
   *
   * A FAILURE EMPTIES THE COLUMN RATHER THAN FILLING IT PARTLY. Half a column
   * of admins and half of dashes is unreadable: the reader cannot tell the
   * college with no admin from the request that did not come back.
   */
  private async loadCollegeAdmins(colleges: CollegeOut[]): Promise<void> {
    if (!this.canReadCollegeAdmins()) {
      this.adminsState.set('off');
      this.collegeAdmins.set({});
      return;
    }
    if (colleges.length === 0) {
      this.adminsState.set('ready');
      this.collegeAdmins.set({});
      return;
    }
    this.adminsState.set('loading');
    this.adminsError.set(null);
    try {
      const responses = await Promise.all(
        colleges.map((college) =>
          fetch(`${environment.apiBase}/admin/colleges/${college.id}/admins`, {
            credentials: 'include',
          }),
        ),
      );
      const failed = responses.find((response) => !response.ok);
      if (failed) {
        this.adminsError.set(await this.detailOf(failed));
        this.collegeAdmins.set({});
        this.adminsState.set('error');
        return;
      }
      const bodies = (await Promise.all(
        responses.map((response) => response.json()),
      )) as CollegeAdminOut[][];
      const byCollege: Record<string, CollegeAdminOut[]> = {};
      colleges.forEach((college, index) => {
        byCollege[college.id] = bodies[index];
      });
      this.collegeAdmins.set(byCollege);
      this.adminsState.set('ready');
    } catch {
      this.adminsError.set('The server could not be reached. No college admin was read.');
      this.collegeAdmins.set({});
      this.adminsState.set('error');
    }
  }

  /** `GET /api/admin/faculty` — the accounts the picker offers. Main Admin
   *  only on the API (`require_admin`), which is why the field says so instead
   *  of rendering an empty list for anybody else. */
  private async loadFaculty(): Promise<void> {
    if (!this.canAppointAdmin() || this.faculty().length > 0 || this.facultyLoading()) {
      return;
    }
    this.facultyLoading.set(true);
    this.facultyError.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/admin/faculty`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.facultyError.set(await this.detailOf(response));
        return;
      }
      this.faculty.set((await response.json()) as FacultyRow[]);
    } catch {
      this.facultyError.set('The server could not be reached. No faculty account was listed.');
    } finally {
      this.facultyLoading.set(false);
    }
  }

  openAppoint(college: CollegeOut): void {
    this.appointingCollegeId.set(college.id);
    this.appointNote.set(null);
    this.appointError.set(null);
    this.appointUserId.set('');
    this.appointReason.set('');
    void this.loadFaculty();
  }

  closeAppoint(): void {
    this.appointingCollegeId.set(null);
    this.appointUserId.set('');
    this.appointReason.set('');
  }

  setAppointUserId(value: string): void {
    this.appointUserId.set(value);
  }

  setAppointReason(value: string): void {
    this.appointReason.set(value);
  }

  setStatusFilter(value: string): void {
    this.statusFilter.set(value);
  }

  setCampusFilter(value: string): void {
    this.campusFilter.set(value);
  }

  /**
   * POST /api/admin/colleges/{id}/admins with the picked faculty account and
   * the reason. The API's own refusal is shown verbatim. On success the card
   * shows the row the server returned, and the note says how many of the
   * eleven functions are live — five carry a student's own records and hold
   * nothing until a second holder of admin.governance approves each in Who
   * can do what.
   */
  async appoint(college: CollegeOut): Promise<void> {
    if (!this.canAppoint()) {
      return;
    }
    const userId = this.appointUserId();
    const person = this.faculty().find((row) => row.user_id === userId);
    this.appointBusy.set(true);
    this.appointNote.set(null);
    this.appointError.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/colleges/${college.id}/admins`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: userId, reason: this.appointReason().trim() }),
        },
      );
      if (!response.ok) {
        this.appointError.set(
          `${person?.name ?? 'That account'} was not appointed to ${college.name}: ${await this.detailOf(response)}`,
        );
        return;
      }
      const admin = (await response.json()) as CollegeAdminOut;
      this.collegeAdmins.update((map) => ({
        ...map,
        [college.id]: [
          ...(map[college.id] ?? []).filter((row) => row.user_id !== admin.user_id),
          admin,
        ],
      }));
      const pending = admin.capabilities.filter(
        (grant) => grant.approval_state !== 'active',
      ).length;
      const live = admin.capabilities.length - pending;
      this.appointNote.set(
        pending > 0
          ? `${admin.name} appointed to ${college.name}: ${live} of ${admin.capabilities.length} functions live, ${pending} awaiting a second approval in Who can do what.`
          : `${admin.name} appointed to ${college.name} with ${plural(admin.capabilities.length, 'function')}.`,
      );
      this.closeAppoint();
    } catch {
      this.appointError.set(
        `The server could not be reached. ${person?.name ?? 'That account'} was not appointed.`,
      );
    } finally {
      this.appointBusy.set(false);
    }
  }

  openDeleteDialog(college: CollegeOut): void {
    this.deletingCollege.set({
      kind: 'college',
      id: college.id,
      name: `${college.code} · ${college.name}`,
      subLine: this.collegeSubLine(college),
    });
  }

  closeDeleteDialog(): void {
    this.deletingCollege.set(null);
  }

  /** The server's own sentence, and the list reread so the card is gone. */
  async onDeleteDone(outcome: DeleteOutcome): Promise<void> {
    this.deletingCollege.set(null);
    this.deleteNote.set(outcome.detail);
    await this.loadColleges();
  }

  /** "BGSCET · Bengaluru · 3 departments" — every part of it a stored value. */
  collegeSubLine(college: CollegeOut): string {
    const departments = plural(college.department_count, 'department');
    const parts = [college.code];
    if (college.campus) {
      parts.push(college.campus);
    }
    parts.push(departments);
    return parts.join(' · ');
  }

  /** The Domains cell. An empty list is a real, documented setting — the
   *  deployment's own `provisionable_email_domains` applies — so it is stated
   *  rather than dashed. */
  domainsLabel(college: CollegeOut): string {
    return college.email_domains.length > 0
      ? college.email_domains.join(', ')
      : 'Deployment list';
  }

  hasDomains(college: CollegeOut): boolean {
    return college.email_domains.length > 0;
  }

  adminsFor(college: CollegeOut): CollegeAdminOut[] {
    return this.collegeAdmins()[college.id] ?? [];
  }

  /** One chip per college admin, saying how much of the set is actually live.
   *  "Appointed" alone would hide the five grants that are waiting for a second
   *  approval — which is the difference between a working console and a
   *  colleague asking why half of theirs answers 403. */
  adminSummary(admin: CollegeAdminOut): AdminSummary {
    const pending = admin.capabilities.filter((grant) => grant.approval_state !== 'active').length;
    const total = admin.capabilities.length + admin.missing.length;
    if (pending > 0) {
      return { label: `${pending} awaiting approval`, tone: 'warn' };
    }
    if (admin.missing.length > 0) {
      return { label: `${admin.capabilities.length} of ${total} functions`, tone: 'warn' };
    }
    return { label: plural(admin.capabilities.length, 'function'), tone: 'good' };
  }

  /** "Ravi Kumar · ravi@bgscet.ac.in" for the picker, with a disabled account
   *  named as one. */
  facultyLabel(row: FacultyRow): string {
    const where = row.department ? ` · ${row.department}` : '';
    const disabled = row.disabled_at ? ' · disabled' : '';
    return `${row.name} · ${row.email}${where}${disabled}`;
  }

  statusLabel(status: string): string {
    return COLLEGE_STATUS_LABELS[status] ?? status;
  }

  /** The status chip's classes. Status is text AND colour: the label is always
   *  rendered beside the tone, never the tone alone. */
  statusChipClass(status: string): string {
    const tone = COLLEGE_STATUS_TONES[status] ?? 'neutral';
    return `chip dot ${tone}`;
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
