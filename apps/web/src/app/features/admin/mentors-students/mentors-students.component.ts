/**
 * Mentor mapping — the board at docs/redesign-2026-09/design/admin/MentorMapping.html
 * (02-admin-console-spec.md §9), on `/admin/mentors`.
 *
 * MENTOR-FIRST, deliberately. Pick a faculty member on the left rail; the
 * unassigned students sit on the right; tick the ones to seat and press the
 * view's one primary action. An earlier shape offered "assign a mentor to
 * students" and "assign students to a mentor" behind a toggle, which is the
 * same operation described twice.
 *
 * "N FREE" IS POLICY, NOT A ROW, AND IT IS STILL ADVISORY. The Mentor row has
 * no capacity; the figure is returned on every faculty member as `capacity`,
 * and nothing refuses an assignment past it — an admin who chooses to overload
 * one faculty member in a thin year should not have to edit .env first. The
 * rail says "At capacity" in the risk colour and lets them. No control on this
 * screen may imply otherwise.
 *
 * B9.2 changed only WHERE THE NUMBER COMES FROM, which is what that sentence
 * was really complaining about: `departments.mentor_capacity` now answers it
 * when the department has named one, falling back to the programme's
 * `settings.mentor_capacity` otherwise, so tuning it for one department is a
 * row and not a deploy. `capacity_source` says which, and the rail prints the
 * two differently: "25, set by Management Studies" and "25, the programme
 * default" are different sentences even when the figure matches.
 *
 * mentor_id IS THE SCOPE KEY. It is what rule 2 filters staff access on, so
 * every write here is Main-Admin-only server-side and a mentor cannot reach
 * it — a mentor able to set it could assign themselves any student in the
 * programme and then read everything about them.
 *
 * Both lists are re-fetched after a change rather than patched locally: a
 * roster and a pool that disagree about where a student is are worse than a
 * moment's wait.
 *
 * WHAT THE SERVER ANSWERS AND WHAT IT DOES NOT. `GET /admin/mentor-load` and
 * `GET /admin/unassigned-students` both take `?department_id=` and
 * `?cohort_id=` (B9.4/B1.5), so the board's first two filters are real. Neither
 * takes a specialization, so the board's third one is disabled with that as its
 * reason and no phase number on it — the treatment `45b91a9` gave the Faculty
 * screen's orphan controls. The board's per-FACULTY assignment history is the
 * same case: `GET /admin/students/{id}/mentor-history` answers for ONE STUDENT,
 * which is a different query, so the history card is wired per mentee and the
 * toolbar button says so rather than promising a feed nobody wrote.
 *
 * FACULTY ACCOUNTS MOVED. Creating a faculty login, re-minting an activation
 * link and filing somebody under a department were on this screen; they are
 * §7 Faculty (`/admin/faculty`) and §8 Add faculty (`/admin/faculty/new`) in
 * the redesign. This screen is the seating chart and nothing else.
 */

import { Component, computed, signal } from '@angular/core';
import { AgGridAngular } from 'ag-grid-angular';
import type {
  ColDef,
  GridApi,
  GridReadyEvent,
  ICellRendererParams,
  MultiRowSelectionOptions,
  SelectionChangedEvent,
  ValueFormatterParams,
} from 'ag-grid-community';

import { environment } from '../../../../environments/environment';
import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { reepGridTheme } from '../../../shared/grid/reep-grid-theme';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';

/**
 * A refusal names the function that was missing, because the feeds this screen
 * draws are gated on THREE DIFFERENT capabilities: `/unassigned-students` and
 * the assignment write take `admin.mentors` (the screen's own), `/mentor-load`
 * and `/cohorts` take `admin.analytics` (routers/console.py), and the batch bar
 * writes through `POST /admin/cohorts/{id}/students/bulk`, which is
 * `admin.students`. The Main Admin holds all three, so a faculty account
 * granted only one of them in Governance is the reader who meets these — and
 * "Could not load mentors and students." gives them nothing to take to the
 * office.
 */
const FORBIDDEN_READ =
  'Your account may not read one of these lists. The faculty load needs the ' +
  'admin.analytics function and the student pool needs admin.mentors — ask the Main Admin.';
const FORBIDDEN_WRITE = 'Your account may not change mentor assignments (admin.mentors).';
const FORBIDDEN_BATCH =
  'Your account may not edit the roster, which is what a batch assignment writes (admin.students).';

/** One student, as `/unassigned-students` returns them. */
interface Mentee {
  student_id: string;
  name: string;
  usn: string | null;
  stage: string | null;
}

/**
 * A mentee as `/mentor-load` returns them — the same person as `Mentee` plus
 * the three headline metrics and B1.5's flag.
 *
 * EVERY METRIC IS NULLABLE OR HAS A STATED ZERO, AND THE DIFFERENCE MATTERS.
 * `attendance_percent` is null when nothing has been recorded, which is not
 * 0.0: a 0 in that column draws a student as a total absentee on a deployment
 * that has simply never imported attendance.
 */
interface MenteeMetrics extends Mentee {
  attendance_percent: number | null;
  verified_skills: number;
  logged_hours: number;
  /** True only when BOTH sides are filed and they differ. False means "not
   *  something anyone can assert about this pair", never "same department". */
  cross_department: boolean;
}

/**
 * Where a faculty member is filed, resolved by the API through
 * `users.department_id`. `filed` is what the screen branches on: a falsy
 * `department_name` cannot tell "nobody has filed this person" apart from "a
 * department whose name is blank", and those mean opposite things on a screen
 * whose job is to get everyone seated.
 */
interface StaffPlacement {
  filed: boolean;
  department_id: string | null;
  department_code: string | null;
  department_name: string | null;
  college_id: string | null;
  college_code: string | null;
  college_name: string | null;
}

interface MentorLoad {
  /** Null until the first student is assigned: the assignment creates the group. */
  mentor_id: string | null;
  /** The users.id the assignment is addressed by when there is no group yet. */
  user_id: string;
  name: string;
  department: string | null;
  designation: string | null;
  /** Resolved institutional position. See StaffPlacement. */
  placement: StaffPlacement;
  capacity: number;
  /** 'department' or 'programme' — which of the two named the number above. */
  capacity_source: string;
  mentee_count: number;
  mentees: MenteeMetrics[];
}

/** A batch, as `GET /admin/cohorts` returns it. */
interface CohortRow {
  id: string;
  code: string;
  /** The batch itself: a YEAR, "2026-28". */
  name: string;
  batch_label: string;
  /** The spine and the year in one sentence, composed by the server from the
   *  batch's own links: "General MBA - Finance · 2026-28". */
  display_label: string;
  degree_level: string;
  student_count: number;
}

/**
 * One spell, as `GET /admin/students/{id}/mentor-history` returns it (B9.1).
 *
 * `from_at` NULL is "mentoring since before this was recorded" and must never
 * render as a blank or as the migration's run date; `to_at` NULL is the current
 * pair. The two sets of act columns are never rewritten: `kind`/`by_name`/
 * `reason` describe the act that OPENED the spell, `end_*` the one that closed
 * it.
 */
interface MentorSpell {
  id: string;
  mentor_id: string;
  mentor_name: string | null;
  from_at: string | null;
  to_at: string | null;
  kind: string;
  by_name: string | null;
  reason: string | null;
  end_kind: string | null;
  ended_by_name: string | null;
  end_reason: string | null;
}

const STAGE_LABEL: Record<string, string> = {
  REBOOT: 'Reboot',
  EXCEL: 'Excel',
  EXCEL_ADVANCED: 'Excel-Adv',
  ELEVATE: 'Elevate',
};

/** `app/models/mentor_assignment.py`'s OPEN_KINDS, in the office's words. */
const OPEN_KIND_LABEL: Record<string, string> = {
  assign: 'Assigned',
  reassign: 'Moved here from another faculty member',
};

/** …and its END_KINDS. `faculty_disabled` mints no handover grant, which is
 *  why it reads differently from a release: nobody kept read access. */
const END_KIND_LABEL: Record<string, string> = {
  release: 'Released to the unassigned pool',
  reassign: 'Moved on to another faculty member',
  faculty_disabled: 'Ended — the faculty account was disabled',
};

/** The filter value meaning "do not send this parameter at all". */
const EVERYTHING = '';

/** "Dr. Meera Rao" -> "MR", for the rail's avatar. */
function initialsOf(name: string): string {
  const words = name.split(' ').filter((word) => word.length > 0);
  const letters = words.map((word) => word[0].toUpperCase());
  return letters.slice(0, 2).join('');
}

/** AG Grid cell renderers build their own DOM, so a name out of the roster
 *  reaches innerHTML: escape it here rather than trusting the database. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatUsn(params: ValueFormatterParams<Mentee, string | null>): string {
  return params.value ?? 'No USN';
}

/** The REEP stage as a chip — text and colour together, never colour alone. */
function renderStageCell(params: ICellRendererParams<Mentee, string | null>): string {
  const stage = params.value;
  if (stage === null || stage === undefined) return '<span class="chip neutral">Stage not set</span>';
  const label = STAGE_LABEL[stage] ?? stage;
  return `<span class="chip dot accent">${escapeHtml(label)}</span>`;
}

@Component({
  selector: 'app-admin-mentors-students',
  standalone: true,
  imports: [AgGridAngular, PluralPipe],
  templateUrl: './mentors-students.component.html',
  styleUrl: './mentors-students.component.scss',
})
export class AdminMentorsStudentsComponent {
  readonly gridTheme = reepGridTheme;
  readonly everything = EVERYTHING;

  readonly mentors = signal<MentorLoad[] | null>(null);
  readonly pool = signal<Mentee[] | null>(null);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly busy = signal(false);
  /**
   * The last load did not answer. It is a THIRD state, beside "loading" and
   * "loaded", and it has to be: setting the two lists to `[]` on a failure made
   * the screen state, in its own words, "No faculty on the roster yet" and
   * "Every student has a mentor" — two assertions about the institution
   * produced by a 403 or a dropped connection.
   */
  readonly loadFailed = signal(false);

  readonly selectedMentorUserId = signal<string | null>(null);
  /** The student ids ticked in the grid, kept as the grid reports them. */
  readonly selectedStudentIds = signal<string[]>([]);
  /** The grid's quick filter, as typed in the card head. */
  readonly poolSearch = signal('');

  /**
   * THE BOARD'S FIRST TWO FILTERS, AND THEY GO TO THE SERVER (B9.4/B1.5).
   * Both endpoints take `?department_id=` and `?cohort_id=`, applied as extra
   * predicates over the caller's own reach — so an id outside the grant matches
   * nothing rather than somebody else's roster, and neither filter can widen
   * what this account may read.
   *
   * A BATCH IS A FACT ABOUT STUDENTS, NOT ABOUT FACULTY. `mentor-load` narrows
   * only the MENTEE rows by `cohort_id` and deliberately leaves the faculty
   * list whole; dropping faculty who happen to have nobody in the batch would
   * turn "show me who is mentoring 2026 MBA" into "hide every faculty member I
   * could seat them with", on the screen whose whole job is seating them. The
   * copy under the rail says that, so the two lists do not look inconsistent.
   */
  readonly departmentFilter = signal(EVERYTHING);
  readonly batchFilter = signal(EVERYTHING);

  /**
   * The batches, for the filter and for the batch bar. `GET /admin/cohorts` is
   * `admin.analytics`, the same key `mentor-load` takes, so a reader who can
   * see the rail can see this list; `null` means it did not answer and both
   * controls that read it say so rather than offering an empty menu.
   */
  readonly cohorts = signal<CohortRow[] | null>(null);

  /**
   * The departments to offer, derived from the faculty rail rather than fetched.
   *
   * `GET /admin/departments` is gated on `admin.institution`, a THIRD key this
   * screen would otherwise not need — a college admin granted Mentors &
   * students and Analytics would meet a 403 on a filter. The faculty rows
   * already carry their resolved placement, so the honest set of departments
   * this screen can narrow BY is the set its own rail is filed under.
   *
   * REBUILT ONLY FROM AN UNFILTERED LOAD. Once a department is chosen the
   * response holds that department alone, so rebuilding from it would collapse
   * the menu to the one option and strand the reader inside it.
   */
  readonly departmentOptions = signal<{ id: string; label: string }[]>([]);

  /**
   * B9.2's reason, and it is REQUIRED — `POST /admin/students/{id}/mentor`
   * answers 422 without one. This input and the server's `reason` field landed
   * in the same change on purpose: the endpoint was breaking, the live client
   * posted `{mentor_id}` and nothing else, and shipping either half alone is an
   * assign button that fails on a working console.
   */
  readonly assignReason = signal('');

  readonly hasReason = computed(() => this.assignReason().trim().length > 0);

  /**
   * THE RELEASE ASKS FOR ITS OWN REASON, ON THE MENTEE'S OWN ROW. The server
   * requires one on a release as much as on an assignment — a release is the
   * move nothing else on any screen reports — but the release used to read it
   * out of the ASSIGN card's "Why this seating" box, in the other column. That
   * box is emptied by every successful assignment, so the student the admin had
   * just seated could not be released: the icon sent nothing, and the only
   * answer was a notice at the top of the page asking them to "press Release
   * again" with a button no label called Release. The remove icon now opens a
   * box on that row, and its Release button is disabled until it holds a reason.
   */
  readonly releasingStudentId = signal<string | null>(null);
  readonly releaseReason = signal('');
  /** A refused release, said beside the box the admin is looking at. */
  readonly releaseError = signal<string | null>(null);

  readonly canRelease = computed(() => this.releaseReason().trim().length > 0 && !this.busy());

  /**
   * THE HISTORY CARD, PER STUDENT. B9.1 records every assignment, release and
   * handover, and the read it shipped is `GET /admin/students/{id}/mentor-history`
   * — one student at a time. The board draws this card per FACULTY MEMBER;
   * composing that from N per-student calls would answer only for the mentees
   * they hold NOW, which silently omits everybody they have handed over, and
   * the handovers are the part anybody opens a history for. So the card asks
   * the question the server actually answers and names the student it is about.
   */
  readonly historyStudent = signal<Mentee | null>(null);
  readonly history = signal<MentorSpell[] | null>(null);
  readonly historyError = signal<string | null>(null);
  readonly historyBusy = signal(false);

  /** The batch bar. Closed until asked for: it writes to every student in a
   *  batch at once and should not sit open beside the single-student action. */
  readonly batchOpen = signal(false);
  readonly batchCohortId = signal(EVERYTHING);
  readonly batchReason = signal('');
  readonly batchBusy = signal(false);

  private grid: GridApi<Mentee> | null = null;

  readonly selectedMentor = computed(
    () => (this.mentors() ?? []).find((one) => one.user_id === this.selectedMentorUserId()) ?? null,
  );
  readonly selectedMentorName = computed(() => this.selectedMentor()?.name ?? 'this faculty member');
  readonly unassignedCount = computed(() => (this.pool() ?? []).length);
  readonly selectedStudentCount = computed(() => this.selectedStudentIds().length);

  /** True when either server-side filter is on, so the counts on screen can say
   *  they are about a slice rather than about the deployment. */
  readonly narrowed = computed(
    () => this.departmentFilter() !== EVERYTHING || this.batchFilter() !== EVERYTHING,
  );

  /**
   * True while a BATCH is chosen — and this one changes what the rail may
   * claim. `mentor-load` narrows the MENTEE rows by cohort, so `mentee_count`
   * is then "how many of this batch they mentor" and not the size of their
   * group: drawing "3/25" and a quarter-full meter over that is the screen
   * reporting a load nobody has. While it is on, the rail states the batch
   * figure in words and the meter is not drawn at all.
   */
  readonly batchNarrowed = computed(() => this.batchFilter() !== EVERYTHING);

  /** The mentee list's heading, which must not say "current mentees" when it is
   *  holding one batch's worth of them. */
  readonly menteeListLabel = computed(() =>
    this.batchNarrowed() ? 'Mentees in this batch' : 'Current mentees',
  );

  readonly departmentFilterLabel = computed(() => {
    const chosen = this.departmentFilter();
    if (chosen === EVERYTHING) return 'All departments';
    return this.departmentOptions().find((one) => one.id === chosen)?.label ?? 'One department';
  });

  readonly batchFilterLabel = computed(() => {
    const chosen = this.batchFilter();
    if (chosen === EVERYTHING) return 'All batches';
    const batch = (this.cohorts() ?? []).find((one) => one.id === chosen);
    return batch ? this.batchLabel(batch) : 'One batch';
  });

  readonly canAssign = computed(() => {
    if (this.selectedMentor() === null) return false;
    if (this.selectedStudentCount() === 0) return false;
    if (!this.hasReason()) return false;
    return !this.busy();
  });

  /** Why the primary action is disabled, on the control rather than in a notice
   *  the reader has to hunt for. Empty when it is enabled. */
  readonly assignBlockedBecause = computed(() => {
    if (this.selectedMentor() === null) return 'Pick a faculty member first.';
    if (this.selectedStudentCount() === 0) return 'Tick at least one student.';
    if (!this.hasReason()) return 'Say why this student is being seated here.';
    return '';
  });

  /** The primary action's words, which name both halves of the act. */
  readonly assignActionLabel = computed(() => {
    const count = this.selectedStudentCount();
    if (count === 0) return 'Assign the selected students';
    return `Assign ${count} selected to ${this.selectedMentorName()}`;
  });

  /** The batch the bar is pointed at, so its warning can count the students. */
  readonly batchTarget = computed(
    () => (this.cohorts() ?? []).find((one) => one.id === this.batchCohortId()) ?? null,
  );

  readonly canApplyBatch = computed(() => {
    if (this.batchBusy() || this.busy()) return false;
    if (this.selectedMentor() === null) return false;
    return this.batchTarget() !== null;
  });

  readonly batchBlockedBecause = computed(() => {
    if (this.selectedMentor() === null) return 'Pick a faculty member on the left first.';
    if (this.batchTarget() === null) return 'Pick the batch to assign.';
    return '';
  });

  readonly columns: ColDef<Mentee>[] = [
    {
      field: 'usn',
      headerName: 'USN',
      pinned: 'left',
      minWidth: 170,
      flex: 1,
      valueFormatter: formatUsn,
    },
    { field: 'name', headerName: 'Student', minWidth: 190, flex: 1.4 },
    { field: 'stage', headerName: 'Stage', minWidth: 150, flex: 1, cellRenderer: renderStageCell },
  ];

  readonly defaultColumn: ColDef<Mentee> = {
    sortable: true,
    resizable: true,
    filter: true,
    suppressHeaderMenuButton: false,
  };

  /** Checkbox selection, with the bulk action under the grid as the board draws
   *  it. Clicking the row selects too: on a seating screen the whole row is the
   *  target a reader aims at. */
  readonly rowSelection: MultiRowSelectionOptions<Mentee> = {
    mode: 'multiRow',
    checkboxes: true,
    headerCheckbox: true,
    enableClickSelection: true,
  };

  /** AG Grid keeps the ticks across a refresh when rows have a stable id. */
  readonly rowIdOf = (params: { data: Mentee }): string => params.data.student_id;

  constructor() {
    registerReepGrid();
    void this.refresh();
    void this.loadCohorts();
  }

  onGridReady(event: GridReadyEvent<Mentee>): void {
    this.grid = event.api;
  }

  /**
   * The grid lives inside an `@if`, so seating the LAST student destroys it
   * while this component keeps the handle. AG Grid 36 answers a call on a dead
   * api with a console error, so ask it first. (`gridPreDestroy` is a grid
   * event but not an `ag-grid-angular` @Output, so binding it would have been a
   * listener that never fires — the dead control this phase forbids.)
   */
  private get liveGrid(): GridApi<Mentee> | null {
    const api = this.grid;
    if (api === null || api.isDestroyed()) return null;
    return api;
  }

  /** The failure card's one control: ask for both lists again. */
  async retry(): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      await this.refresh();
    } finally {
      this.busy.set(false);
    }
  }

  onSelectionChanged(event: SelectionChangedEvent<Mentee>): void {
    const rows = event.api.getSelectedRows();
    this.selectedStudentIds.set(rows.map((student) => student.student_id));
  }

  searchPool(value: string): void {
    this.poolSearch.set(value);
  }

  selectMentor(userId: string): void {
    this.selectedMentorUserId.set(userId);
    this.flash.set(null);
    this.cancelRelease();
  }

  isSelectedMentor(mentor: MentorLoad): boolean {
    return mentor.user_id === this.selectedMentorUserId();
  }

  initials(name: string): string {
    return initialsOf(name);
  }

  /** `<select>` hands its value back through the event; read once, here. */
  selectValue(event: Event): string {
    return (event.target as HTMLSelectElement).value;
  }

  async setDepartmentFilter(value: string): Promise<void> {
    if (value === this.departmentFilter()) return;
    this.departmentFilter.set(value);
    await this.refilter();
  }

  async setBatchFilter(value: string): Promise<void> {
    if (value === this.batchFilter()) return;
    this.batchFilter.set(value);
    await this.refilter();
  }

  /** "General MBA - Finance · 2024-26" — the batch as both controls print it.
   *  The spine comes down the batch's LINKS and is composed by the server;
   *  `name` on its own is the year, which cannot tell a department's four
   *  batches apart. */
  batchLabel(batch: CohortRow): string {
    return batch.display_label;
  }

  /** "Associate Professor · Management Studies", or what the roster holds. */
  facultyLine(mentor: MentorLoad): string {
    const parts: string[] = [];
    if (mentor.designation !== null && mentor.designation.trim().length > 0) {
      parts.push(mentor.designation);
    }
    if (mentor.placement.filed && mentor.placement.department_name !== null) {
      parts.push(mentor.placement.department_name);
    } else if (mentor.department !== null && mentor.department.trim().length > 0) {
      parts.push(mentor.department);
    }
    if (parts.length === 0) return 'Department not on record';
    return parts.join(' · ');
  }

  /** How full this faculty member's group is, as a percentage of the bar. */
  loadPercent(mentor: MentorLoad): number {
    if (mentor.capacity <= 0) return 0;
    return Math.min(100, Math.round((100 * mentor.mentee_count) / mentor.capacity));
  }

  isAtCapacity(mentor: MentorLoad): boolean {
    return mentor.mentee_count >= mentor.capacity;
  }

  /**
   * Where the capacity figure came from, in words. B9.2 returns
   * `capacity_source` precisely so a programme default is not presented as a
   * departmental decision — "Management said 25" and "nobody has said anything
   * and the default is 25" are different facts even when the number matches.
   */
  capacityNote(mentor: MentorLoad): string {
    if (mentor.capacity_source === 'department') {
      const named = mentor.placement.department_name;
      return named ? `${mentor.capacity}, set by ${named}` : `${mentor.capacity}, set by the department`;
    }
    return `${mentor.capacity}, the programme default`;
  }

  /** The one line under the name that says what the load means. Nothing here
   *  refuses an assignment past capacity, so the words never say "full". */
  loadNote(mentor: MentorLoad): string {
    if (mentor.mentor_id === null) return 'Becomes a mentor on first assignment';
    if (this.batchNarrowed()) {
      // See `batchNarrowed`: the count is this batch's, so neither "N free" nor
      // "at capacity" can be said about it without inventing a group size.
      return `${plural(mentor.mentee_count, 'mentee')} from this batch · whole-group load not shown`;
    }
    if (this.isAtCapacity(mentor)) return `At capacity — ${this.capacityNote(mentor)}`;
    const free = mentor.capacity - mentor.mentee_count;
    return `${plural(free, 'place')} free of ${this.capacityNote(mentor)}`;
  }

  menteeLine(student: Mentee): string {
    const parts = [student.usn ?? 'No USN'];
    if (student.stage !== null) parts.push(STAGE_LABEL[student.stage] ?? student.stage);
    return parts.join(' · ');
  }

  /**
   * The three headline metrics `mentor-load` returns for a mentee. A missing
   * attendance reads as "Attendance not recorded" and never as 0%: nothing
   * imported and nobody present are opposite facts.
   */
  menteeMetrics(student: MenteeMetrics): string {
    const attendance =
      student.attendance_percent === null
        ? 'Attendance not recorded'
        : `${student.attendance_percent}% attendance`;
    return [
      attendance,
      plural(student.verified_skills, 'verified skill'),
      `${student.logged_hours.toLocaleString('en')} h logged`,
    ].join(' · ');
  }

  // ------------------------------------------------------------- history --

  /** Open one student's spell list. Called from a mentee row, which is the only
   *  place on this screen that names a single student. */
  async openHistory(student: Mentee): Promise<void> {
    this.historyStudent.set(student);
    this.history.set(null);
    this.historyError.set(null);
    this.historyBusy.set(true);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/students/${student.student_id}/mentor-history`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.historyError.set(
          response.status === 403
            ? 'Your account may not read this student’s mentor history (admin.mentors).'
            : await this.detailOf(response),
        );
        return;
      }
      this.history.set((await response.json()) as MentorSpell[]);
    } catch {
      this.historyError.set('Could not reach the server.');
    } finally {
      this.historyBusy.set(false);
    }
  }

  closeHistory(): void {
    this.historyStudent.set(null);
    this.history.set(null);
    this.historyError.set(null);
  }

  /** True while this student's history is the one on screen. */
  isHistoryOpen(student: Mentee): boolean {
    return this.historyStudent()?.student_id === student.student_id;
  }

  spellOpen(spell: MentorSpell): boolean {
    return spell.to_at === null;
  }

  openedLabel(spell: MentorSpell): string {
    return OPEN_KIND_LABEL[spell.kind] ?? spell.kind;
  }

  endedLabel(spell: MentorSpell): string {
    if (spell.end_kind === null) return '';
    return END_KIND_LABEL[spell.end_kind] ?? spell.end_kind;
  }

  /**
   * A NULL `from_at` is "since before this was recorded" — the migration seeded
   * one open row per current pair and dated it from the ACCOUNT's creation
   * where it could, because `students` has no created_at and `now()` would have
   * told every reader the whole roster was seated on deploy day.
   */
  spellFrom(spell: MentorSpell): string {
    if (spell.from_at === null) return 'Since before this was recorded';
    return `From ${this.when(spell.from_at)}`;
  }

  spellTo(spell: MentorSpell): string {
    if (spell.to_at === null) return 'Current';
    return `Until ${this.when(spell.to_at)}`;
  }

  when(stamp: string): string {
    return new Date(stamp).toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
  }

  // --------------------------------------------------------- the writes --

  /** Move every ticked student in the pool onto the selected faculty member. */
  async assignSelectedStudents(): Promise<void> {
    const mentor = this.selectedMentor();
    const studentIds = this.selectedStudentIds();
    if (mentor === null || studentIds.length === 0) return;
    const failure = await this.writeMentor(studentIds, mentor, this.assignReason().trim());
    if (failure === null) {
      this.flash.set(`${plural(studentIds.length, 'student')} assigned to ${mentor.name}`);
      // Cleared only on success. After a failure the words stay in the box, or
      // the retry costs the admin the sentence they just typed.
      this.assignReason.set('');
    } else if (!this.loadFailed()) {
      // refresh() clears the error it does not raise; the write's reason is the
      // more specific one, unless the reload failed too and is already saying so.
      this.error.set(failure);
    }
  }

  /** The remove icon on a mentee row: open the release box on that row. */
  startRelease(student: Mentee): void {
    this.releasingStudentId.set(student.student_id);
    this.releaseReason.set('');
    this.releaseError.set(null);
    this.flash.set(null);
  }

  cancelRelease(): void {
    this.releasingStudentId.set(null);
    this.releaseReason.set('');
    this.releaseError.set(null);
  }

  isReleasing(student: Mentee): boolean {
    return this.releasingStudentId() === student.student_id;
  }

  /** Release one student back to the pool, with the reason typed on their row. */
  async releaseStudent(student: Mentee): Promise<void> {
    const reason = this.releaseReason().trim();
    if (reason.length === 0 || this.busy()) return;
    const mentor = this.selectedMentor();
    const from = mentor === null ? 'their mentor' : mentor.name;
    this.releaseError.set(null);
    const failure = await this.writeMentor([student.student_id], null, reason);
    if (failure === null) {
      this.cancelRelease();
      this.flash.set(`${student.name} released from ${from}`);
      return;
    }
    if (this.loadFailed()) return;
    // Said on the row while the row is still drawn, so the admin reads it where
    // they pressed; a row the reload removed has nowhere to say it but the top.
    const stillListed =
      this.selectedMentor()?.mentees.some((one) => one.student_id === student.student_id) ?? false;
    if (stillListed) {
      this.releaseError.set(failure);
    } else {
      this.cancelRelease();
      this.error.set(failure);
    }
  }

  toggleBatchBar(): void {
    this.batchOpen.update((open) => !open);
    this.flash.set(null);
  }

  /**
   * THE WHOLE BATCH ONTO ONE FACULTY MEMBER.
   * `POST /admin/cohorts/{id}/students/bulk` with `action: "mentor"` is the
   * single assignment repeated through the same helpers, so the same-college
   * rule and the history row apply per student exactly as they do above. It
   * touches EVERY student in the batch, including those who already have a
   * different mentor — the warning beside the control says so, because nothing
   * on this screen shows a student moving off somebody else's rail.
   *
   * `reason` IS OPTIONAL HERE AND REQUIRED ON THE SINGLE ENDPOINT, deliberately
   * (B9.2): a sentence asked once and applied to thirty people describes the
   * batch, not any student in it. It is recorded when given.
   */
  async applyBatch(): Promise<void> {
    const mentor = this.selectedMentor();
    const batch = this.batchTarget();
    if (mentor === null || batch === null || this.batchBusy()) return;
    const reason = this.batchReason().trim();
    this.batchBusy.set(true);
    this.error.set(null);
    this.flash.set(null);
    let failure: string | null = null;
    let affected = 0;
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/cohorts/${batch.id}/students/bulk`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            action: 'mentor',
            mentor_user_id: mentor.user_id,
            ...(reason ? { reason } : {}),
          }),
        },
      );
      if (!response.ok) {
        failure = response.status === 403 ? FORBIDDEN_BATCH : await this.detailOf(response);
      } else {
        affected = ((await response.json()) as { affected: number }).affected;
      }
    } catch {
      failure = 'Could not reach the server — nothing was changed.';
    }
    this.liveGrid?.deselectAll();
    this.selectedStudentIds.set([]);
    await this.refresh();
    if (failure === null) {
      this.flash.set(
        `${plural(affected, 'student')} in ${this.batchLabel(batch)} assigned to ${mentor.name}.`,
      );
      this.batchReason.set('');
      this.batchOpen.set(false);
    } else if (!this.loadFailed()) {
      this.error.set(failure);
    }
    this.batchBusy.set(false);
  }

  /** Null target releases. A target with no group yet is sent by USER id: the
   *  API creates the group on that first assignment, which is how a faculty
   *  account becomes a mentor — by this act, not by a flag at creation.
   *  Answers the refusal in words, or null when every request went through;
   *  the caller says which, because the assign card and a mentee row say it in
   *  different places. */
  private async writeMentor(
    studentIds: string[],
    target: MentorLoad | null,
    reason: string,
  ): Promise<string | null> {
    const body = this.assignmentBody(target, reason);
    this.busy.set(true);
    this.error.set(null);
    // A stale "3 students assigned to …" sitting under a red failure notice is
    // the screen telling the reader both that it worked and that it did not.
    this.flash.set(null);
    let failure: string | null = null;
    try {
      for (const studentId of studentIds) {
        const response = await fetch(`${environment.apiBase}/admin/students/${studentId}/mentor`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!response.ok) {
          // This is a request PER STUDENT, so a refusal partway through leaves
          // some of them seated. Stop, then RELOAD — returning without a
          // refresh leaves already-assigned students drawn in the pool, which
          // is the one list on this screen that must not lie.
          failure = response.status === 403 ? FORBIDDEN_WRITE : await this.detailOf(response);
          break;
        }
      }
    } catch {
      failure = 'Could not reach the server — the lists have been reloaded.';
    }
    this.liveGrid?.deselectAll();
    this.selectedStudentIds.set([]);
    await this.refresh();
    this.busy.set(false);
    return failure;
  }

  private assignmentBody(target: MentorLoad | null, reason: string): Record<string, string | null> {
    // `reason` on every body, release included: the server requires it on all
    // three shapes, which is the point — a release is the move that leaves no
    // other trace.
    if (target === null) return { mentor_id: null, reason };
    if (target.mentor_id !== null) return { mentor_id: target.mentor_id, reason };
    return { mentor_user_id: target.user_id, reason };
  }

  // ---------------------------------------------------------- the loads --

  /** A filter changed: drop the ticks (they name rows that may be gone from the
   *  new slice) and ask the server again. */
  private async refilter(): Promise<void> {
    this.liveGrid?.deselectAll();
    this.selectedStudentIds.set([]);
    this.flash.set(null);
    this.busy.set(true);
    try {
      await this.refresh();
    } finally {
      this.busy.set(false);
    }
  }

  /** The two filters, as the query string both endpoints read. Absent means
   *  "do not narrow", which is not the same as an empty value. */
  private filterQuery(): string {
    const params = new URLSearchParams();
    if (this.departmentFilter() !== EVERYTHING) {
      params.set('department_id', this.departmentFilter());
    }
    if (this.batchFilter() !== EVERYTHING) params.set('cohort_id', this.batchFilter());
    const query = params.toString();
    return query ? `?${query}` : '';
  }

  private async loadCohorts(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/cohorts`, {
        credentials: 'include',
      });
      // A failure leaves the signal null, which both the filter and the batch
      // bar render as "the batch list could not be read" — never as an empty
      // menu, which would read as a college with no batches in it.
      if (!response.ok) return;
      this.cohorts.set((await response.json()) as CohortRow[]);
    } catch {
      /* left null on purpose — see above */
    }
  }

  private async refresh(): Promise<void> {
    this.error.set(null);
    const query = this.filterQuery();
    try {
      const [loadResponse, poolResponse] = await Promise.all([
        fetch(`${environment.apiBase}/admin/mentor-load${query}`, { credentials: 'include' }),
        fetch(`${environment.apiBase}/admin/unassigned-students${query}`, {
          credentials: 'include',
        }),
      ]);
      if (!loadResponse.ok || !poolResponse.ok) {
        const forbidden = loadResponse.status === 403 || poolResponse.status === 403;
        this.error.set(forbidden ? FORBIDDEN_READ : 'Could not load mentors and students.');
        this.failLoad();
        return;
      }
      const mentors = (await loadResponse.json()) as MentorLoad[];
      this.mentors.set(mentors);
      this.pool.set((await poolResponse.json()) as Mentee[]);
      this.loadFailed.set(false);
      if (this.departmentFilter() === EVERYTHING) this.rememberDepartments(mentors);
      // Keep a selection across a refresh, and make one on first load so the
      // two cards are never an empty prompt when there is a faculty member.
      const stillListed = mentors.some((one) => one.user_id === this.selectedMentorUserId());
      if (!stillListed) {
        this.selectedMentorUserId.set(mentors.length > 0 ? mentors[0].user_id : null);
      }
      // The open history names a student who may not be on this slice any more.
      const student = this.historyStudent();
      if (student !== null) void this.openHistory(student);
    } catch {
      this.error.set('Could not reach the server.');
      this.failLoad();
    }
  }

  /** The department menu, from the rail's own placements. Only faculty who are
   *  FILED contribute one: an unfiled account hangs under nothing, and offering
   *  a blank option would be a filter that matches by accident. */
  private rememberDepartments(mentors: MentorLoad[]): void {
    const byId = new Map<string, string>();
    for (const mentor of mentors) {
      const where = mentor.placement;
      if (!where.filed || where.department_id === null) continue;
      const college = where.college_code ?? where.college_name;
      const name = where.department_name ?? where.department_code ?? 'Department';
      byId.set(where.department_id, college ? `${name} · ${college}` : name);
    }
    this.departmentOptions.set(
      Array.from(byId, ([id, label]) => ({ id, label })).sort((left, right) =>
        left.label.localeCompare(right.label),
      ),
    );
  }

  /** Nothing is known, so nothing is drawn: the two lists go back to "not
   *  loaded" rather than to "empty", which is a claim about the roster. */
  private failLoad(): void {
    this.mentors.set(null);
    this.pool.set(null);
    this.loadFailed.set(true);
  }

  /** The server's own sentence where there is one. FastAPI answers a 422 with
   *  `detail` as a LIST, which renders as "[object Object]" printed straight
   *  out, so the first message in it is what the admin reads. */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail) && detail.length && typeof detail[0]?.msg === 'string') {
        return detail[0].msg;
      }
    } catch {
      /* not JSON — fall through to the status */
    }
    return `The request was refused (${response.status}).`;
  }
}
