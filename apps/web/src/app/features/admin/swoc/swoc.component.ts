/**
 * SWOC notes — the board at docs/redesign-2026-09/design/admin/Swoc.html
 * (02-admin-console-spec.md §18), on `/admin/swoc`.
 *
 *     Strengths · Weaknesses · Opportunities · Challenges
 *
 * One list of students, one editor of four quadrants. `GET /admin/swoc` is
 * every student with every entry written about them (the list IS the data —
 * picking a row fills the editor with no second read). `POST /admin/swoc/{id}`,
 * `PATCH` and `DELETE /admin/swoc/entries/{id}` do the writing. The route is
 * `capabilityGuard('admin.swoc')` and the API `require_capability` on the same
 * key, so a faculty member granted it in Governance reaches this screen with
 * nothing else changing.
 *
 * THE VIEWPOINT IS NOT A FIELD HERE. The API stamps it, and B7 made that stamp
 * mean something: MENTOR is written only when the author actually mentors THAT
 * student, so one granted lecturer no longer files every line on a department's
 * board as MENTOR about students they have never met. The editor only shows it,
 * as a chip.
 *
 * A LINE BELONGS TO WHOEVER WROTE IT (B7.2). The author edits and deletes their
 * own; the Main Admin edits any; an authorless line is the office's alone,
 * because treating "no author" as "everybody is the author" makes exactly the
 * rows nobody is answerable for the easiest to rewrite. The server answers 403
 * with a sentence saying so, and this screen prints that sentence rather than
 * pre-disabling the box: the response carries the author's NAME and not their
 * id, so a client guess would have to match on a name, and two colleagues who
 * share one would each be told the other's lines are theirs.
 *
 * THE DEPARTMENT PILL IS A READ-OUT, NOT A FILTER (B1.4). `GET /admin/swoc` is
 * scoped — `scope_filter` narrows the students AND their entries to the
 * caller's grant — and it takes no department parameter: the reach is stated on
 * the response instead, in `X-Reep-Scope` with `X-Reep-Scope-Colleges` and
 * `X-Reep-Scope-Departments` beside it. A picker was the wrong shape twice
 * over: there is nothing to send, and a row here carries no department to
 * filter on client-side either (`SwocStudentRow` is student, USN, batch and the
 * entries), so a menu could only have hidden students without saying why. What
 * the pill does instead is state how far the grant reaches, which is the
 * question the board's filter was really asking.
 *
 * AND "REACHES NOBODY" IS NOT "NOBODY IS ENROLLED". A grant whose only target
 * was a deleted department answers `[]`, exactly as a fresh deployment does.
 * The empty list says which of the two it is looking at, because on this screen
 * the first is a governance problem for one account and the second is a fact
 * about the college.
 *
 * WHAT THE BOARD DRAWS THAT NO ENDPOINT ANSWERS is not drawn. The MENTOR
 * filter (`SwocStudentRow` carries no mentor and `GET /admin/swoc` takes no
 * mentor parameter) and the EXPORT (there is no SWOC CSV anywhere in the API)
 * were grey controls with the reason in a tooltip, and the owner's rule for
 * the console removed them. The Semester filter is drawn only when a line on
 * the board carries a semester; before that it would be the same grey control.
 *
 * NO PROSE (2026-09-15). The three scope sentences, the semester-slice note,
 * the "each line shows its author…" notice and the "edits save when you click
 * away" footnote are gone; the reach chip, the counts and the chips on each
 * line say the same things as facts. The student rows are College structure's
 * bordered pick-rows, with the note count and the acknowledged count on the
 * row.
 */

import { Component, computed, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { PluralPipe } from '../../../shared/text/plural.pipe';

/** The three words `app/scope_views.py` writes into `X-Reep-Scope`. Three and
 *  not a boolean, for its reason: `programme` and `none` are opposite facts and
 *  must never render the same. */
type ScopeWord = 'programme' | 'narrowed' | 'none';

interface ScopeReach {
  word: ScopeWord;
  /** Ids, not names. This screen has no catalogue to resolve them against, so
   *  they are counted and never printed. */
  colleges: string[];
  departments: string[];
}

const SCOPE_HEADER = 'X-Reep-Scope';
const SCOPE_COLLEGES_HEADER = 'X-Reep-Scope-Colleges';
const SCOPE_DEPARTMENTS_HEADER = 'X-Reep-Scope-Departments';

/** `MAX_SCOPE_IDS` in app/scope_views.py — the header stops at twenty ids, so a
 *  count standing exactly on it is a floor and reads "20+". */
const MAX_SCOPE_IDS = 20;

interface SwocEntry {
  id: string;
  kind: 'STRENGTH' | 'WEAKNESS' | 'OPPORTUNITY' | 'CHALLENGE';
  source: string;
  text: string;
  weight: number;
  /** The author's NAME, or null. */
  author: string | null;
  /**
   * Whether an author was recorded at all. `author_user_id` is ON DELETE SET
   * NULL, so `author_recorded: true` with `author: null` is unreachable and a
   * null author means ONE thing — nobody was recorded. This flag is the stated
   * fact to branch on, rather than a falsy name.
   */
  author_recorded: boolean;
  recorded_at: string;
  /** Equal to `recorded_at` means "as written"; later means "edited then". */
  updated_at: string;
  /** B7.4. NULL on every line written before the column existed, and NEVER
   *  backfilled: there is no semester history to backfill from, and today's
   *  semester on a line written last year is a lie on the student's own
   *  screen. It renders as "semester not recorded". */
  semester: number | null;
  /** B7.5. When the STUDENT said they had read it. */
  acknowledged_at: string | null;
}

interface StudentRow {
  student_id: string;
  name: string;
  usn: string | null;
  batch: string | null;
  entries: SwocEntry[];
}

/** One edit of one line, from `GET /admin/swoc/{student_id}/history` (B7.3). */
interface SwocRevision {
  id: string;
  entry_id: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  by: string | null;
  changed_at: string;
}

/** One field that changed in a revision, as the panel prints it. */
interface FieldChange {
  field: string;
  from: string;
  to: string;
}

type SwocKind = SwocEntry['kind'];

/**
 * The four quadrants in the board's order, each with the tone it is drawn in
 * (the board's own four colours: good, risk, brand purple, warn) and the
 * prompt an empty composer shows the writer.
 */
interface Quadrant {
  key: SwocKind;
  label: string;
  tone: 'good' | 'risk' | 'accent' | 'warn';
  hint: string;
}

/**
 * The four quadrants, and what the composer prompts for in each.
 *
 * THE PROMPTS DESCRIBE A SENTENCE, THEY DO NOT WRITE ONE. The board fills these
 * boxes with finished assessments of its imaginary student — "Strong analytical
 * and quantitative skills — top quartile in Sem 3 accounting" — and those
 * sentences read exactly like something a mentor wrote about the student whose
 * name is at the top of this screen. A reader skimming a half-filled quadrant
 * cannot tell a grey example from a pale saved note, and the one thing a SWOC
 * note must never do is put words in a colleague's mouth about a named student.
 * So the placeholder asks for the shape of the line instead of supplying one,
 * which is also the more useful prompt: every quadrant here is worth writing
 * only when it names the evidence.
 */
const QUADRANTS: Quadrant[] = [
  {
    key: 'STRENGTH',
    label: 'Strengths',
    tone: 'good',
    hint: 'Something they do well, and what you saw that shows it.',
  },
  {
    key: 'WEAKNESS',
    label: 'Weaknesses',
    tone: 'risk',
    hint: 'A gap to work on, stated as a next step rather than a verdict.',
  },
  {
    key: 'OPPORTUNITY',
    label: 'Opportunities',
    tone: 'accent',
    hint: 'An opening ahead of them, and why they are placed to take it.',
  },
  {
    key: 'CHALLENGE',
    label: 'Challenges',
    tone: 'warn',
    hint: 'What stands in the way, and what would move it.',
  },
];

/** The viewpoint the API stamped, in the words the office uses for it. */
const SOURCE_LABEL: Record<string, string> = {
  PLACEMENT: 'Placement cell',
  MENTOR: 'Mentor',
  PM: 'Programme',
};

/** The `_snapshot` keys worth printing in an edit, in the order they read. The
 *  rest of the snapshot (student_id, source, the three links) either cannot
 *  change through this screen or is an id no reader can resolve. */
const REVISION_FIELDS: { key: string; label: string }[] = [
  { key: 'text', label: 'Text' },
  { key: 'weight', label: 'Weight' },
  { key: 'kind', label: 'Quadrant' },
  { key: 'semester', label: 'Semester' },
];

/** One quadrant line is a sentence or two, not an essay: the API refuses more. */
const MAX_ENTRY_CHARS = 400;

/** How strongly the author holds the line; the heaviest sit at the top. */
const WEIGHTS = [5, 4, 3, 2, 1];

/** The batch filter's "no batch chosen" value, so the empty option is not a name. */
const ALL_BATCHES = '';

/** The semester filter's "do not send the parameter" value. */
const ALL_SEMESTERS = '';

@Component({
  selector: 'app-admin-swoc',
  standalone: true,
  imports: [PluralPipe],
  templateUrl: './swoc.component.html',
  styleUrl: './swoc.component.scss',
})
export class AdminSwocComponent {
  readonly quadrants = QUADRANTS;
  readonly weights = WEIGHTS;
  readonly maxEntryChars = MAX_ENTRY_CHARS;
  readonly allSemesters = ALL_SEMESTERS;

  readonly rows = signal<StudentRow[] | null>(null);
  readonly query = signal('');
  readonly batchFilter = signal(ALL_BATCHES);
  readonly selectedStudentId = signal<string | null>(null);

  /**
   * B7.4's semester filter, and it goes to the SERVER — `GET /admin/swoc`
   * takes `?semester=`.
   *
   * IT NARROWS THE ENTRIES AND NOT THE STUDENTS, deliberately, and the copy
   * says so: a student with nothing written this term is still a row on the
   * board with an empty quadrant set, because dropping them would turn "show me
   * semester 3" into "hide everybody nobody has written about yet", which is
   * the cohort this screen exists to find. A line whose semester was never
   * recorded is not in any semester and so is not in this slice either.
   */
  readonly semesterFilter = signal(ALL_SEMESTERS);

  /**
   * The semesters to offer, derived from the lines the board is holding.
   *
   * REBUILT ONLY FROM AN UNFILTERED LOAD, for the mentor-mapping screen's
   * reason: once semester 3 is chosen the response holds semester 3 alone, so
   * rebuilding from it would collapse the menu to the one option and strand the
   * reader inside it.
   */
  readonly semesterOptions = signal<number[]>([]);

  /** The quadrant whose composer is open — at most one, so the view keeps its
   *  single primary action. */
  readonly composerFor = signal<SwocKind | null>(null);
  readonly draft = signal('');

  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);

  /** B7.3's edit history for the student on screen, and B7.4's per-semester
   *  read of their board. Both are panels rather than routes: they are about
   *  the student already selected. */
  readonly historyOpen = signal(false);
  readonly revisions = signal<SwocRevision[] | null>(null);
  readonly historyBusy = signal(false);
  readonly historyError = signal<string | null>(null);
  readonly semesterViewOpen = signal(false);

  /** What the server said this caller's grant reaches, read off the list
   *  response. `null` means it stated nothing, which is not "nothing". */
  readonly scope = signal<ScopeReach | null>(null);

  /** The pill where the board drew a Department menu: the reach, as text. */
  readonly scopeChip = computed<{ label: string; tone: 'neutral' | 'accent' | 'warn' } | null>(
    () => {
      const reach = this.scope();
      if (reach === null) return null;
      if (reach.word === 'programme') return { label: 'Reach · every department', tone: 'neutral' };
      if (reach.word === 'none') return { label: 'Reach · nobody', tone: 'warn' };
      const parts = [
        this.idCount(reach.colleges, 'college', 'colleges'),
        this.idCount(reach.departments, 'department', 'departments'),
      ].filter((part) => part !== null);
      if (parts.length === 0) return { label: 'Reach · narrowed', tone: 'accent' };
      return { label: `Reach · ${parts.join(' · ')}`, tone: 'accent' };
    },
  );

  /** Why the list is empty — the two reasons are opposite facts. */
  readonly emptyListNote = computed(() => {
    if (this.error()) return 'The list could not be loaded.';
    if (this.query() || this.batchFilter()) return 'No student matches this filter.';
    if (this.scope()?.word === 'none') return 'Your access reaches no student.';
    return 'No student yet.';
  });

  readonly studentCount = computed(() => (this.rows() ?? []).length);

  readonly writtenCount = computed(
    () => (this.rows() ?? []).filter((student) => student.entries.length > 0).length,
  );

  /** Every batch present in the loaded list, for the board's batch filter. */
  readonly batches = computed(() => {
    const names = new Set<string>();
    for (const student of this.rows() ?? []) {
      if (student.batch) names.add(student.batch);
    }
    return Array.from(names).sort();
  });

  readonly batchFilterLabel = computed(() => this.batchFilter() || 'All batches');

  readonly semesterFilterLabel = computed(() => {
    const chosen = this.semesterFilter();
    return chosen === ALL_SEMESTERS ? 'All semesters' : `Semester ${chosen}`;
  });

  readonly filtered = computed(() => {
    const needle = this.query().trim().toLowerCase();
    const batch = this.batchFilter();
    let students = this.rows() ?? [];
    if (batch) {
      students = students.filter((student) => student.batch === batch);
    }
    if (!needle) return students;
    return students.filter((student) => this.matchesSearch(student, needle));
  });

  readonly selected = computed(
    () => (this.rows() ?? []).find((student) => student.student_id === this.selectedStudentId()) ?? null,
  );

  /** The selected student's entries, bucketed by quadrant, heaviest first. */
  readonly buckets = computed(() => {
    const student = this.selected();
    const byQuadrant = {} as Record<SwocKind, SwocEntry[]>;
    for (const quadrant of QUADRANTS) {
      byQuadrant[quadrant.key] = (student?.entries ?? [])
        .filter((entry) => entry.kind === quadrant.key)
        .sort(
          (left, right) =>
            right.weight - left.weight || left.recorded_at.localeCompare(right.recorded_at),
        );
    }
    return byQuadrant;
  });

  /**
   * B7.4's semester view for the selected student: their lines grouped by the
   * semester each was STAMPED WITH AT WRITE TIME. "Not recorded" is a group of
   * its own and never folded into a number — a line written before the column
   * existed belongs to no semester, and putting it in the current one would be
   * inventing a fact on the student's own screen.
   */
  readonly semesterBreakdown = computed<{ key: string; label: string; count: number }[]>(() => {
    const entries = this.selected()?.entries ?? [];
    const counts = new Map<number, number>();
    let unrecorded = 0;
    for (const entry of entries) {
      if (entry.semester === null) unrecorded += 1;
      else counts.set(entry.semester, (counts.get(entry.semester) ?? 0) + 1);
    }
    const groups = Array.from(counts, ([semester, count]) => ({
      key: String(semester),
      label: `Semester ${semester}`,
      count,
    })).sort((left, right) => Number(left.key) - Number(right.key));
    if (unrecorded > 0) {
      groups.push({ key: '', label: 'Semester not recorded', count: unrecorded });
    }
    return groups;
  });

  readonly draftLength = computed(() => this.draft().trim().length);

  readonly canSaveDraft = computed(() => this.draftLength() > 0 && !this.busy());

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.error.set(null);
    const semester = this.semesterFilter();
    const query = semester === ALL_SEMESTERS ? '' : `?semester=${encodeURIComponent(semester)}`;
    try {
      const response = await fetch(`${environment.apiBase}/admin/swoc${query}`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      this.scope.set(this.readScope(response));
      const rows = (await response.json()) as StudentRow[];
      this.rows.set(rows);
      if (semester === ALL_SEMESTERS) this.rememberSemesters(rows);
    } catch (failure) {
      this.scope.set(null);
      this.rows.set([]);
      this.error.set(failure instanceof Error ? failure.message : 'Could not load students.');
    }
  }

  async setSemesterFilter(value: string): Promise<void> {
    if (value === this.semesterFilter()) return;
    this.semesterFilter.set(value);
    this.flash.set(null);
    await this.load();
  }

  /** `<select>` hands its value back through the event; read once, here. */
  selectValue(event: Event): string {
    return (event.target as HTMLSelectElement).value;
  }

  /** The filter holds the option's VALUE, which a `<select>` always spells as a
   *  string; the menu is built from numbers. Compared here rather than in the
   *  template, which has no `String`. */
  isSemesterFilter(semester: number): boolean {
    return this.semesterFilter() === String(semester);
  }

  /** Every semester any line on the board carries, for the filter's menu. A
   *  board where nothing has been stamped yet offers nothing, and the control
   *  says that rather than showing an empty menu. */
  private rememberSemesters(rows: StudentRow[]): void {
    const seen = new Set<number>();
    for (const student of rows) {
      for (const entry of student.entries) {
        if (entry.semester !== null) seen.add(entry.semester);
      }
    }
    this.semesterOptions.set(Array.from(seen).sort((left, right) => left - right));
  }

  /** The reach the server stated on this response, or `null` when it stated
   *  none. The headers are readable because the SPA is same-origin through
   *  proxy.conf.json; a cross-origin fetch would need them CORS-allowlisted.
   *
   *  AN UNRECOGNISED WORD IS `null`, NEVER A GUESS: reading a header this
   *  screen does not understand as "none" would tell a writer their grant
   *  reaches nobody on the strength of a typo. */
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

  /** "3 departments", or "20+ colleges" where the header stood on its cap. */
  private idCount(ids: string[], one: string, many: string): string | null {
    if (ids.length === 0) return null;
    const capped = ids.length >= MAX_SCOPE_IDS ? `${MAX_SCOPE_IDS}+` : `${ids.length}`;
    return `${capped} ${ids.length === 1 ? one : many}`;
  }

  selectStudent(student: StudentRow): void {
    this.selectedStudentId.set(student.student_id);
    this.closeComposer();
    this.flash.set(null);
    this.error.set(null);
    // Both panels are about the student who was selected when they opened.
    this.historyOpen.set(false);
    this.revisions.set(null);
    this.historyError.set(null);
    this.semesterViewOpen.set(false);
  }

  isSelected(student: StudentRow): boolean {
    return student.student_id === this.selectedStudentId();
  }

  openComposer(kind: SwocKind): void {
    this.composerFor.set(kind);
    this.draft.set('');
    this.flash.set(null);
  }

  closeComposer(): void {
    this.composerFor.set(null);
    this.draft.set('');
  }

  quadrantLabel(kind: SwocKind): string {
    const quadrant = QUADRANTS.find((candidate) => candidate.key === kind);
    return quadrant ? quadrant.label : kind;
  }

  sourceLabel(source: string): string {
    return SOURCE_LABEL[source] ?? source;
  }

  /** Two initials for the avatar, from the name the roster holds. */
  initials(name: string): string {
    const words = name.trim().split(/\s+/).slice(0, 2);
    const letters = words.map((word) => word.charAt(0).toUpperCase());
    return letters.join('') || '?';
  }

  when(recordedAt: string): string {
    return new Date(recordedAt).toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
  }

  // --------------------------------------------------- what a line says --

  /** Author, semester and date on one line, in the order a reader asks them. */
  entryBy(entry: SwocEntry): string {
    // "Author not recorded", NOT "no longer on the roster": `author_user_id` is
    // ON DELETE SET NULL, so a null author means nobody was recorded and cannot
    // mean anybody left. `author_recorded` is the stated fact to branch on.
    const who = entry.author_recorded && entry.author ? entry.author : 'Author not recorded';
    const term = entry.semester === null ? 'Semester not recorded' : `Semester ${entry.semester}`;
    const parts = [who, term, this.when(entry.recorded_at)];
    if (this.wasEdited(entry)) parts.push(`edited ${this.when(entry.updated_at)}`);
    return parts.join(' · ');
  }

  /** `updated_at` carries no `onupdate` on purpose — it would fire when the
   *  STUDENT acknowledges a line and report it as edited by nobody. So a later
   *  `updated_at` really does mean somebody rewrote the line. */
  wasEdited(entry: SwocEntry): boolean {
    return entry.updated_at !== entry.recorded_at;
  }

  /** B7.5. Whether the student has said they read this line — as text and
   *  colour together, never colour alone. */
  ackChip(entry: SwocEntry): { label: string; tone: 'good' | 'neutral' } {
    if (entry.acknowledged_at === null) return { label: 'Not acknowledged', tone: 'neutral' };
    return { label: `Acknowledged ${this.when(entry.acknowledged_at)}`, tone: 'good' };
  }

  /** How many of this student's lines they have acknowledged. */
  ackCount(student: StudentRow): number {
    return student.entries.filter((entry) => entry.acknowledged_at !== null).length;
  }

  // ------------------------------------------------------- the panels ----

  async toggleHistory(): Promise<void> {
    if (this.historyOpen()) {
      this.historyOpen.set(false);
      return;
    }
    const student = this.selected();
    if (student === null) return;
    this.historyOpen.set(true);
    this.semesterViewOpen.set(false);
    this.revisions.set(null);
    this.historyError.set(null);
    this.historyBusy.set(true);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/swoc/${student.student_id}/history`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.historyError.set(await this.detailOf(response));
        return;
      }
      this.revisions.set((await response.json()) as SwocRevision[]);
    } catch {
      this.historyError.set('Could not reach the server.');
    } finally {
      this.historyBusy.set(false);
    }
  }

  toggleSemesterView(): void {
    this.semesterViewOpen.update((open) => !open);
    if (this.semesterViewOpen()) this.historyOpen.set(false);
  }

  /**
   * The fields that actually changed in one revision, in a fixed order. The
   * snapshot carries more than a reader can use — the student id, the viewpoint
   * and three link ids — so only the four a person edits are printed, and only
   * where the two sides differ.
   */
  changesIn(revision: SwocRevision): FieldChange[] {
    const changes: FieldChange[] = [];
    for (const { key, label } of REVISION_FIELDS) {
      const from = this.snapshotValue(revision.before[key]);
      const to = this.snapshotValue(revision.after[key]);
      if (from === to) continue;
      changes.push({ field: label, from, to });
    }
    return changes;
  }

  private snapshotValue(value: unknown): string {
    if (value === null || value === undefined) return 'not recorded';
    return String(value);
  }

  /**
   * Which line an edit was to, named by the text it holds AFTER the edit. The
   * snapshot always carries `text`, so this identifies the row even for an edit
   * that only moved the weight — and even for a line that has since been
   * deleted, which is exactly the case a history is opened for.
   */
  revisionLine(revision: SwocRevision): string {
    const text = revision.after['text'];
    return typeof text === 'string' && text.length > 0 ? text : 'A line that no longer exists';
  }

  // -------------------------------------------------------- the writes --

  async addEntry(kind: SwocKind): Promise<void> {
    const student = this.selected();
    const text = this.draft().trim();
    if (!student || !text || this.busy()) return;
    await this.run(async () => {
      const response = await fetch(`${environment.apiBase}/admin/swoc/${student.student_id}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, text, weight: 3 }),
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      const saved = (await response.json()) as SwocEntry;
      this.patchStudent(student.student_id, (row) => ({ ...row, entries: [...row.entries, saved] }));
      this.closeComposer();
      this.flash.set(`Added to ${student.name}'s ${this.quadrantLabel(kind).toLowerCase()}.`);
    });
  }

  /**
   * The box is the line. Clearing it and clicking away used to return in
   * silence — the API refuses a blank entry, so nothing was written, but the
   * `[value]` binding had nothing to change either and the writer was left
   * looking at an empty box over a line that still says what it always said.
   * The stored sentence goes back in, and the control that does delete a line
   * is named.
   */
  async saveText(entry: SwocEntry, field: HTMLTextAreaElement): Promise<void> {
    const cleaned = field.value.trim();
    if (cleaned === entry.text) return;
    if (!cleaned) {
      field.value = entry.text;
      this.flash.set(null);
      this.error.set('An entry cannot be blank — use Remove to delete this line.');
      return;
    }
    await this.editEntry(entry, { text: cleaned }, () => {
      // B7.2. A refusal means somebody else's line: put their sentence back in
      // the box, or the screen shows this writer's draft over a line the server
      // kept exactly as it was.
      field.value = entry.text;
    });
  }

  /** A refused weight puts the picker back where the row still is: a select
   *  left showing 5 over an entry the server kept at 3 is a lie the next
   *  render has no reason to correct. */
  async setWeight(entry: SwocEntry, picker: HTMLSelectElement): Promise<void> {
    const chosen = Number(picker.value);
    if (!Number.isInteger(chosen) || chosen === entry.weight) return;
    await this.editEntry(entry, { weight: chosen }, () => {
      picker.value = String(entry.weight);
    });
  }

  async removeEntry(entry: SwocEntry): Promise<void> {
    const student = this.selected();
    if (!student || this.busy()) return;
    await this.run(async () => {
      const response = await fetch(`${environment.apiBase}/admin/swoc/entries/${entry.id}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!response.ok && response.status !== 404) throw new Error(await this.detailOf(response));
      this.patchStudent(student.student_id, (row) => ({
        ...row,
        entries: row.entries.filter((candidate) => candidate.id !== entry.id),
      }));
      this.flash.set('Removed.');
    });
  }

  private matchesSearch(student: StudentRow, needle: string): boolean {
    if (student.name.toLowerCase().includes(needle)) return true;
    if ((student.usn ?? '').toLowerCase().includes(needle)) return true;
    return (student.batch ?? '').toLowerCase().includes(needle);
  }

  private async editEntry(
    entry: SwocEntry,
    body: { text?: string; weight?: number },
    undo?: () => void,
  ): Promise<void> {
    const student = this.selected();
    if (!student || this.busy()) {
      undo?.();
      return;
    }
    const written = await this.run(async () => {
      const response = await fetch(`${environment.apiBase}/admin/swoc/entries/${entry.id}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      const saved = (await response.json()) as SwocEntry;
      this.patchStudent(student.student_id, (row) => ({
        ...row,
        entries: row.entries.map((candidate) => (candidate.id === saved.id ? saved : candidate)),
      }));
      this.flash.set('Saved.');
    });
    if (!written) undo?.();
  }

  private patchStudent(studentId: string, change: (row: StudentRow) => StudentRow): void {
    this.rows.update((rows) =>
      (rows ?? []).map((row) => (row.student_id === studentId ? change(row) : row)),
    );
  }

  /** True when the work went through. A caller that put a control into a
   *  state the server then refused needs to know to put it back. */
  private async run(work: () => Promise<void>): Promise<boolean> {
    this.busy.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      await work();
      return true;
    } catch (failure) {
      this.error.set(failure instanceof Error ? failure.message : 'Something went wrong.');
      return false;
    } finally {
      this.busy.set(false);
    }
  }

  /** The server's own sentence where there is one. FastAPI answers a 422 with
   *  `detail` as a LIST, which renders as "[object Object]" if it is printed
   *  straight out, so the first message in it is what the writer reads. */
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
