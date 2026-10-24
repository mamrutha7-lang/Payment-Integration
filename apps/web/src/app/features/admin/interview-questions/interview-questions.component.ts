/**
 * Interview question bank — /admin/interview-questions.
 *
 * Board: docs/redesign-2026-09/design/admin/InterviewBank.html
 * Spec:  docs/redesign-2026-09/02-admin-console-spec.md §16
 *
 * WHAT THIS IS NOT: a script. The interviewer stays free-style; these rows
 * become its "guide to coverage" — worked in, in this order, at the phase in
 * brackets, rephrased naturally, with follow-ups on what the student says. An
 * admin decides WHAT is covered, the model decides HOW it is asked.
 *
 * WHAT IS LIVE HERE, and it is the whole existing surface:
 * `/api/admin/interview-questions` — `/tracks`, the list, POST, `/bulk`,
 * PATCH, DELETE and `/reorder`. Search, the column and density choices and the
 * page size are all done over rows already loaded, so they need no endpoint of
 * their own.
 *
 * THERE IS NO EXPORT BUTTON ON THIS GRID, and that is deliberate: the board
 * draws Columns and Density and nothing else, and 02-admin-console-spec.md §22
 * makes every extract a card on the Exports screen, behind `admin.exports`,
 * carrying a PII flag and an audited download history. A client-side CSV built
 * from the rows in view hands the same data out from a screen gated on
 * `admin.interview_questions`, with no audit row and no flag — it routes around
 * the control §22 exists to impose. An extract of this bank belongs there.
 *
 * A TRACK IS A ROW NOW. `/api/admin/interview-questions/tracks` is a full
 * GET/POST/PATCH/DELETE surface behind the same `admin.interview_questions`
 * key, so the persona, the frameworks, the voice, the sample question, the
 * syllabus and whether the track is offered at all are edited here. Four things
 * about it are load-bearing and none of them are visible at the call site:
 *
 *   · A CODE WITH NO ROW IS STILL LISTED, with `source: 'code'` and `id: null`.
 *     It is the constant in `app/interview_matrix.py`, `resolve_specialization`
 *     still falls back to it, and it is running interviews right now — so it is
 *     shown, and shown as not editable, rather than hidden or offered a PATCH
 *     with no id to send.
 *   · THE VOICE IS A CLOSED SET. Nova answers an unknown `voiceId` with a
 *     ValidationException that kills the stream during the handshake — an
 *     interview that never starts, for every student on the track, with nothing
 *     on any screen naming the cause. The field is a `<select>` over
 *     `KNOWN_NOVA_VOICES` for that reason and not for tidiness; the server
 *     refuses the same set, and this is the half that stops it being typed.
 *   · THE PERSONA IS A NOUN PHRASE. `build_instructions` embeds it as "you are
 *     {persona}", so a trailing full stop composes into "you are a sharp CFO.."
 *     and an imperative composes into nonsense. The server refuses both; the
 *     help text under the field is what stops an admin meeting that refusal.
 *   · A USED TRACK IS RETIRED, NEVER DELETED. `interview_sessions` files every
 *     interview under the track CODE, so deleting a used row orphans a cohort's
 *     records from their own track. The server refuses it and the card offers
 *     the checkbox instead.
 *
 * THE SESSION CAP IS NOT ON THIS SCREEN. A cap is not a property of a track:
 * the time limit and the daily and attempt ceilings are `interview_policies`,
 * one row per (college, course), edited on Interview records.
 *
 * THERE IS NO "ASKED" OR "AVG SCORE" COLUMN (2026-09-15). Both were drawn and
 * both read a permanent dash with a tooltip saying why: attributing a turn to
 * the question that produced it (B6.6) was examined in Phase 4 and found
 * impossible without scripting the interview, because the interviewer works
 * the bank in freely and rephrases. A column that can never hold a number is
 * the grey control the owner's rule for the console refuses, so the two went,
 * and the column chooser and density toggle whose only job was to hide them
 * went with them. The reasoning stays here; if the column ever comes back it
 * will be as an ESTIMATE the screen labels as one.
 *
 * ONE COLUMN, TOP TO BOTTOM (2026-09-15). The 290px track rail is a row of
 * pill tabs, and under it come one card for the track and one for its
 * questions; the add-one and add-many forms open inside the questions card
 * rather than as cards of their own. Every control the board had is here;
 * what left is the help text under each field (the placeholder carries the
 * example instead), the footnote and the duplicate "Add many" button.
 *
 * Reached by the `admin.interview_questions` capability: the Main Admin by
 * baseline, a mentor only when granted — which is how faculty are given this.
 */

import { Component, computed, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';

/** One interview track, as `GET /api/admin/interview-questions/tracks` returns
 *  it. The first five fields are the shape this screen was built against and
 *  are unchanged; everything below them arrived with B5.1. */
interface InterviewTrack {
  key: string;
  label: string;
  phases: string[];
  count: number;
  enabled_count: number;
  /** Null for a track that is still only the constant in the matrix — see
   *  `source`. Everything that writes needs an id. */
  id: string | null;
  code: string;
  persona: string;
  frameworks: string[];
  sample_question: string;
  nova_voice: string;
  syllabus: string[];
  enabled: boolean;
  position: number;
  college_id: string | null;
  course_id: string | null;
  specialization_id: string | null;
  /** `table` — a row, editable. `code` — the matrix constant, which still runs
   *  real interviews and must therefore be shown. */
  source: string;
  /** Does this session's grant reach this track? A convenience for the form,
   *  never the fence — `require_capability(..., target=…)` on the server is. */
  editable: boolean;
}

/** What a create or an edit answers with: the row, plus anything worth saying
 *  about it that was not worth refusing over. */
interface TrackWriteResult {
  track: InterviewTrack;
  warnings: string[];
}

/** One question in the bank, in the order the interviewer works it in. */
interface BankQuestion {
  id: string;
  track: string;
  phase: string;
  text: string;
  position: number;
  enabled: boolean;
  created_at: string;
}

/** A question with the number it carries in its track's working order. The
 *  number is the row's place in the WHOLE track, not in the filtered page, so
 *  a search does not renumber the bank under the reader. */
interface QuestionRow {
  question: BankQuestion;
  number: number;
  /** How many times this row has been put back; part of its `track` key. */
  generation: number;
}

const PHASE_LABEL: Record<string, string> = {
  opening: 'Opening',
  probing: 'Probing',
  deep_dive: 'Deep dive',
  wrap_up: 'Wrap-up',
};

/** The voices Amazon Nova 2 Sonic accepts (`KNOWN_NOVA_VOICES` in
 *  `app/interview_matrix.py`), sorted as the server sorts them in its refusal.
 *  COPIED DELIBERATELY, and the server is still the one that refuses: this list
 *  only stops the admin typing a voice that would end the stream during the
 *  handshake. An entry added here that Nova does not know is refused by the
 *  PATCH; one missing here is a voice this form cannot offer — which is why the
 *  empty option ("deployment default") always exists. */
const NOVA_VOICES: readonly string[] = [
  'ambre',
  'amy',
  'arjun',
  'beatrice',
  'carlos',
  'carolina',
  'florian',
  'kiara',
  'lennart',
  'leo',
  'lorenzo',
  'lupe',
  'matthew',
  'olivia',
  'tiffany',
  'tina',
];

/** One of the matrix's own four, the persona field's placeholder, so the
 *  grammar rule (a noun phrase) is visible rather than discovered through a
 *  422. */
const PERSONA_EXAMPLE = 'an empathetic yet compliant Chief Human Resources Officer';

/** The server's own bounds on a new track (`AdminTrackIn`), so the form refuses
 *  before the request rather than after it. */
const MINIMUM_TRACK_CODE_CHARS = 2;
const MINIMUM_TRACK_LABEL_CHARS = 2;
const MINIMUM_PERSONA_CHARS = 3;
const MINIMUM_SAMPLE_QUESTION_CHARS = 12;

/** The design system fixes one categorical colour per track (01 §1): FIN is
 *  cat-1, HR cat-2, MKT cat-3, BA cat-4. The class is the track's name, so the
 *  colour is decided here once and painted in this screen's stylesheet. */
const TRACK_DOT_CLASS: Record<string, string> = {
  fa: 'bank-dot--finance',
  hr: 'bank-dot--people',
  dm: 'bank-dot--marketing',
  ba: 'bank-dot--analytics',
};

const PAGE_SIZES = [10, 25, 50, 100];

/** The server's own floor and ceiling (`BankQuestionIn`), stated here so the
 *  form refuses before the request rather than after it. */
const MINIMUM_QUESTION_CHARS = 8;
const MAXIMUM_QUESTION_CHARS = 600;

@Component({
  selector: 'app-interview-questions',
  standalone: true,
  imports: [PluralPipe],
  templateUrl: './interview-questions.component.html',
  styleUrl: './interview-questions.component.scss',
})
export class InterviewQuestionsComponent {
  readonly pageSizes = PAGE_SIZES;
  readonly maximumQuestionChars = MAXIMUM_QUESTION_CHARS;
  readonly novaVoices = NOVA_VOICES;
  readonly personaExample = PERSONA_EXAMPLE;

  readonly tracks = signal<InterviewTrack[] | null>(null);
  readonly selectedTrackKey = signal<string>('hr');
  readonly questions = signal<BankQuestion[] | null>(null);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly busy = signal(false);

  /** Add one. */
  readonly addFormOpen = signal(false);
  readonly newQuestionPhase = signal('probing');
  readonly newQuestionText = signal('');

  /** The track card's draft — the selected track's fields as the form holds
   *  them, filled from the server on every load and every track change. Kept as
   *  separate signals rather than one object so a field binding is one read:
   *  the inputs are uncontrolled `[value]` + `(input)`, exactly like the bulk
   *  panel's. */
  readonly draftPersona = signal('');
  readonly draftSample = signal('');
  readonly draftFrameworks = signal('');
  readonly draftVoice = signal('');
  readonly draftSyllabus = signal('');
  readonly draftEnabled = signal(true);
  /** What the server said about the last save that it did not refuse over. */
  readonly trackWarnings = signal<string[]>([]);

  /** Add a track. */
  readonly addTrackOpen = signal(false);
  readonly newTrackCode = signal('');
  readonly newTrackLabel = signal('');
  readonly newTrackPersona = signal('');
  readonly newTrackSample = signal('');
  readonly newTrackFrameworks = signal('');
  readonly newTrackVoice = signal('');

  /** Add many, from a paste or from a .txt / .csv read in the browser. */
  readonly bulkOpen = signal(false);
  readonly bulkText = signal('');
  readonly bulkSkipped = signal<string[]>([]);

  /** The grid's own furniture: everything here works over rows already loaded. */
  readonly quickFilter = signal('');
  readonly pageSize = signal(PAGE_SIZES[0]);
  readonly pageIndex = signal(0);
  readonly selectedQuestionIds = signal<ReadonlySet<string>>(new Set<string>());

  /** Bumped for a question when an inline edit on it was REFUSED, and carried
   *  on its row into the grid's `track` expression so that row is rebuilt from
   *  the server's copy.
   *
   *  The two inline editors write straight into the DOM — a `<select>` the
   *  browser has already moved, a `<textarea>` the reader has already typed in
   *  — and their bindings (`[value]`, `[selected]`) are unchanged when the
   *  PATCH fails, so Angular has nothing to re-apply and the row keeps showing
   *  an edit the bank never took. That is the worst kind of lie a console can
   *  tell: it looks saved. Rebuilding the row is what puts the truth back.
   *
   *  It rides ON THE ROW and not beside it. `@for` keys the row it drew and
   *  the row it is handed with the same track expression, evaluated now, so a
   *  counter read from the component moves both keys together and rebuilds
   *  nothing. Per question, so putting one row back leaves alone the row the
   *  reader has just clicked into. */
  private readonly rowGenerations = signal<ReadonlyMap<string, number>>(new Map());

  // ----------------------------------------------------------- the rail --

  readonly selectedTrack = computed<InterviewTrack | null>(() => {
    const found = (this.tracks() ?? []).find((track) => track.key === this.selectedTrackKey());
    return found ?? null;
  });

  /** The track's own label, or — before `/tracks` has answered — the key the
   *  screen is about to ask for. "Track · HR" is true while it loads;
   *  "Track · Track" was not about anything. */
  readonly selectedTrackLabel = computed(
    () => this.selectedTrack()?.label ?? this.trackCode(this.selectedTrackKey()),
  );

  readonly phases = computed<string[]>(() => {
    const phasesOnTrack = this.selectedTrack()?.phases;
    if (phasesOnTrack && phasesOnTrack.length > 0) return phasesOnTrack;
    return ['opening', 'probing', 'deep_dive', 'wrap_up'];
  });

  readonly trackCount = computed(() => (this.tracks() ?? []).length);

  /** No college pointer means every college sees this track. Read from the
   *  pointer rather than from a name, because the list endpoint carries ids and
   *  this screen's capability does not reach the college catalogue — "one
   *  college" is the true thing it can say without inventing which. */
  readonly selectedTrackIsProgrammeWide = computed(
    () => (this.selectedTrack()?.college_id ?? null) === null,
  );

  /** May this session edit the selected track AT ALL — is there a row, and does
   *  the grant reach it. Never the fence: `require_capability(..., target=…)`
   *  is, and this only decides what the form offers. */
  readonly canEditTrack = computed<boolean>(() => {
    const track = this.selectedTrack();
    if (track === null) return false;
    return track.id !== null && track.editable;
  });

  /** The sentence that goes on the disabled controls and in the card's notice.
   *  Null when the track is editable, so the template renders neither. */
  readonly trackEditBlockedReason = computed<string | null>(() => {
    const track = this.selectedTrack();
    if (track === null) return null;
    if (track.id === null) {
      return 'Built-in track, not editable here. A track added with the same code takes it over.';
    }
    if (!track.editable) {
      return 'Read-only: your access does not reach this track’s college.';
    }
    return null;
  });

  readonly canSaveTrack = computed<boolean>(() => {
    if (!this.canEditTrack() || this.busy()) return false;
    if (this.draftPersona().trim().length < MINIMUM_PERSONA_CHARS) return false;
    return this.draftSample().trim().length >= MINIMUM_SAMPLE_QUESTION_CHARS;
  });

  /** Why Save is grey, on Save itself. A disabled control whose reason is
   *  somewhere else is a control the reader argues with. */
  readonly trackSaveBlockedReason = computed<string | null>(() => {
    const blocked = this.trackEditBlockedReason();
    if (blocked !== null) return blocked;
    if (this.draftPersona().trim().length < MINIMUM_PERSONA_CHARS) {
      return 'Interviewer role is required.';
    }
    if (this.draftSample().trim().length < MINIMUM_SAMPLE_QUESTION_CHARS) {
      return `Sample question needs at least ${MINIMUM_SAMPLE_QUESTION_CHARS} characters.`;
    }
    return null;
  });

  readonly canCreateTrack = computed<boolean>(() => {
    if (this.busy()) return false;
    if (this.newTrackCode().trim().length < MINIMUM_TRACK_CODE_CHARS) return false;
    if (this.newTrackLabel().trim().length < MINIMUM_TRACK_LABEL_CHARS) return false;
    if (this.newTrackPersona().trim().length < MINIMUM_PERSONA_CHARS) return false;
    return this.newTrackSample().trim().length >= MINIMUM_SAMPLE_QUESTION_CHARS;
  });

  readonly questionsAcrossTracks = computed(() => {
    let total = 0;
    for (const track of this.tracks() ?? []) {
      total += track.count;
    }
    return total;
  });

  // ---------------------------------------------------------- the table --

  readonly rows = computed<QuestionRow[]>(() => {
    const generations = this.rowGenerations();
    return (this.questions() ?? []).map((question, index) => ({
      question,
      number: index + 1,
      generation: generations.get(question.id) ?? 0,
    }));
  });

  readonly matchingRows = computed<QuestionRow[]>(() => {
    const needle = this.quickFilter().trim().toLowerCase();
    if (needle === '') return this.rows();
    return this.rows().filter((row) => this.rowMatches(row, needle));
  });

  readonly pageCount = computed(() =>
    Math.max(1, Math.ceil(this.matchingRows().length / this.pageSize())),
  );

  /** The page actually shown: a delete or a search can leave `pageIndex` past
   *  the end, and a table that renders nothing at all reads as a failure. */
  readonly currentPage = computed(() => Math.min(this.pageIndex(), this.pageCount() - 1));

  readonly pageRows = computed<QuestionRow[]>(() => {
    const firstRow = this.currentPage() * this.pageSize();
    return this.matchingRows().slice(firstRow, firstRow + this.pageSize());
  });

  readonly rangeLabel = computed(() => {
    const total = this.matchingRows().length;
    if (total === 0) return '0 of 0';
    const firstRow = this.currentPage() * this.pageSize() + 1;
    const lastRow = Math.min(firstRow + this.pageSize() - 1, total);
    return `${firstRow} to ${lastRow} of ${total}`;
  });

  readonly isLoading = computed(() => this.questions() === null);
  readonly questionCount = computed(() => (this.questions() ?? []).length);
  readonly enabledCount = computed(() => (this.questions() ?? []).filter((q) => q.enabled).length);
  readonly pausedCount = computed(() => this.questionCount() - this.enabledCount());
  readonly selectedCount = computed(() => this.selectedQuestionIds().size);

  readonly hasSelection = computed(() => this.selectedCount() > 0);

  readonly everyRowOnPageIsSelected = computed(() => {
    const rowsOnPage = this.pageRows();
    if (rowsOnPage.length === 0) return false;
    return rowsOnPage.every((row) => this.selectedQuestionIds().has(row.question.id));
  });

  readonly canAddQuestion = computed(
    () => !this.busy() && this.newQuestionText().trim().length >= MINIMUM_QUESTION_CHARS,
  );

  readonly canAddBulk = computed(() => !this.busy() && this.bulkText().trim().length > 0);

  /** Reorder names EVERY id on the track, so it can only be driven from the
   *  unfiltered order — otherwise "move up" swaps with a row the reader cannot
   *  see. */
  readonly reorderIsAvailable = computed(() => this.quickFilter().trim() === '');

  readonly reorderHint = computed(() => {
    if (this.reorderIsAvailable()) return 'Move this question in the interviewer’s order';
    return 'Clear the search to reorder — the order covers the whole track';
  });

  readonly addToggleLabel = computed(() => (this.addFormOpen() ? 'Close' : 'Add question'));
  readonly addToggleIcon = computed(() => (this.addFormOpen() ? 'close' : 'add'));

  constructor() {
    void this.loadTracksAndQuestions();
  }

  phaseLabel(phase: string): string {
    return PHASE_LABEL[phase] ?? phase;
  }

  /** The track's short code, as the rail and the boards write it: "FIN · …". */
  trackCode(trackKey: string): string {
    return trackKey.toUpperCase();
  }

  /** Both classes, so a static `class` beside a `[class]` binding cannot be
   *  the thing that decides whether the dot has a colour. */
  trackDotClass(trackKey: string): string {
    const colour = TRACK_DOT_CLASS[trackKey] ?? '';
    return `bank-dot ${colour}`.trim();
  }

  isRowSelected(question: BankQuestion): boolean {
    return this.selectedQuestionIds().has(question.id);
  }

  dismissError(): void {
    this.error.set(null);
  }

  dismissFlash(): void {
    this.flash.set(null);
  }

  // ------------------------------------------------------ picking a track --

  async selectTrack(trackKey: string): Promise<void> {
    if (trackKey === this.selectedTrackKey()) return;
    this.selectedTrackKey.set(trackKey);
    this.flash.set(null);
    this.bulkSkipped.set([]);
    this.selectedQuestionIds.set(new Set<string>());
    this.pageIndex.set(0);
    this.syncTrackDraft();
    await this.loadQuestions();
  }

  // -------------------------------------------------------- the track --

  toggleAddTrackForm(): void {
    this.addTrackOpen.update((open) => !open);
    if (this.addTrackOpen()) {
      this.addFormOpen.set(false);
      this.bulkOpen.set(false);
    }
  }

  /** Save the four fields the card holds.
   *
   *  `label` and the three spine pointers are NOT SENT. The card has no field
   *  for either, and on `AdminTrackPatch` an explicitly-sent null spine pointer
   *  is how a track is WIDENED — so a form that posted its whole state would
   *  quietly move a college's track to every college the first time somebody
   *  pressed Save on it. Only what the card edits travels. */
  async saveTrack(): Promise<void> {
    const track = this.selectedTrack();
    if (track === null || track.id === null || !this.canSaveTrack()) return;
    await this.whileBusy(async () => {
      const response = await fetch(
        `${environment.apiBase}/admin/interview-questions/tracks/${track.id}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            persona: this.draftPersona().trim(),
            sample_question: this.draftSample().trim(),
            frameworks: splitList(this.draftFrameworks()),
            nova_voice: this.draftVoice(),
            syllabus: splitList(this.draftSyllabus()),
            enabled: this.draftEnabled(),
          }),
        },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      const result = (await response.json()) as TrackWriteResult;
      this.applyTrack(result);
      this.flash.set('Track saved.');
    });
  }

  /** Create a track, programme-wide. The code is a slug and cannot be changed
   *  afterwards — it is what `?specialization=` carries and what every past
   *  interview is filed under — so the server's own refusal is the message. */
  async createTrack(): Promise<void> {
    if (!this.canCreateTrack()) return;
    await this.whileBusy(async () => {
      const response = await this.postJson(
        `${environment.apiBase}/admin/interview-questions/tracks`,
        {
          code: this.newTrackCode().trim().toLowerCase(),
          label: this.newTrackLabel().trim(),
          persona: this.newTrackPersona().trim(),
          sample_question: this.newTrackSample().trim(),
          frameworks: splitList(this.newTrackFrameworks()),
          nova_voice: this.newTrackVoice(),
        },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      const result = (await response.json()) as TrackWriteResult;
      const created = result.track.key;
      this.newTrackCode.set('');
      this.newTrackLabel.set('');
      this.newTrackPersona.set('');
      this.newTrackSample.set('');
      this.newTrackFrameworks.set('');
      this.newTrackVoice.set('');
      this.addTrackOpen.set(false);
      await this.loadTracks();
      this.selectedTrackKey.set(created);
      this.syncTrackDraft();
      this.trackWarnings.set(result.warnings ?? []);
      this.flash.set('Track created.');
      await this.loadQuestions();
    });
  }

  /** Remove a track NO INTERVIEW HAS EVER BEEN HELD ON. The server refuses one
   *  that has been used and says so in words; the checkbox on the card is the
   *  answer in that case, and the questions survive either way
   *  (`interview_bank_questions.track_id` is ON DELETE SET NULL). */
  async removeTrack(): Promise<void> {
    const track = this.selectedTrack();
    if (track === null || track.id === null || !this.canEditTrack() || this.busy()) return;
    const confirmed = confirm(`Remove the ${track.label} track? Its questions stay in the bank.`);
    if (!confirmed) return;
    await this.whileBusy(async () => {
      const response = await fetch(
        `${environment.apiBase}/admin/interview-questions/tracks/${track.id}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      this.trackWarnings.set([]);
      await this.loadTracks();
      this.syncTrackDraft();
      this.flash.set('Track removed.');
      await this.loadQuestions();
    });
  }

  /** Put one written row back into the list and into the card's draft. */
  private applyTrack(result: TrackWriteResult): void {
    this.tracks.update((list) =>
      (list ?? []).map((row) => (row.key === result.track.key ? result.track : row)),
    );
    this.trackWarnings.set(result.warnings ?? []);
    this.syncTrackDraft();
  }

  /** Fill the card from the server's copy of the selected track. Called on
   *  every load and every track change, so an abandoned edit is never carried
   *  across to another track — which would read as that track's own text. */
  private syncTrackDraft(): void {
    const track = this.selectedTrack();
    this.draftPersona.set(track?.persona ?? '');
    this.draftSample.set(track?.sample_question ?? '');
    this.draftFrameworks.set((track?.frameworks ?? []).join(', '));
    this.draftVoice.set(track?.nova_voice ?? '');
    this.draftSyllabus.set((track?.syllabus ?? []).join(', '));
    this.draftEnabled.set(track?.enabled ?? true);
  }

  // ------------------------------------------------------- one at a time --

  toggleAddForm(): void {
    this.addFormOpen.update((open) => !open);
    if (this.addFormOpen()) this.bulkOpen.set(false);
  }

  async addQuestion(): Promise<void> {
    if (!this.canAddQuestion()) return;
    const text = this.newQuestionText().trim();
    const read = this.questionsRead;
    await this.whileBusy(async () => {
      const response = await this.postJson(`${environment.apiBase}/admin/interview-questions`, {
        track: this.selectedTrackKey(),
        phase: this.newQuestionPhase(),
        text,
      });
      if (!response.ok) throw new Error(await this.detailOf(response));
      const added = (await response.json()) as BankQuestion;
      if (read === this.questionsRead) this.questions.update((list) => [...(list ?? []), added]);
      this.newQuestionText.set('');
      this.flash.set('Question added.');
      await this.loadTracks();
    });
  }

  // ------------------------------------------------------------ in bulk --

  toggleBulkPanel(): void {
    this.bulkOpen.update((open) => !open);
    if (this.bulkOpen()) this.addFormOpen.set(false);
  }

  async onBulkFile(event: Event): Promise<void> {
    const picker = event.target as HTMLInputElement;
    const file = picker.files?.[0];
    if (!file) return;
    // Read in the browser: it is text, and the server takes lines, not files —
    // the same rules apply to a paste and to a .txt/.csv.
    this.bulkText.set(await file.text());
    picker.value = '';
  }

  async addBulk(): Promise<void> {
    if (!this.canAddBulk()) return;
    const lines = this.bulkText().trim();
    const read = this.questionsRead;
    await this.whileBusy(async () => {
      const response = await this.postJson(
        `${environment.apiBase}/admin/interview-questions/bulk`,
        {
          track: this.selectedTrackKey(),
          lines,
        },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      const result = (await response.json()) as { added: BankQuestion[]; skipped: string[] };
      if (read === this.questionsRead) {
        this.questions.update((list) => [...(list ?? []), ...result.added]);
      }
      this.bulkSkipped.set(result.skipped);
      this.flash.set(this.bulkFlashFor(result.added.length, result.skipped.length));
      if (result.skipped.length === 0) this.bulkText.set('');
      await this.loadTracks();
    });
  }

  // ------------------------------------------------------- edit in place --

  async toggleEnabled(question: BankQuestion): Promise<void> {
    await this.whileBusy(async () => {
      await this.patchQuestion(question, { enabled: !question.enabled });
      await this.loadTracks();
    });
  }

  async setPhase(question: BankQuestion, phase: string): Promise<void> {
    if (phase === question.phase) return;
    await this.whileBusy(async () => {
      await this.patchQuestion(question, { phase });
    });
  }

  async saveText(question: BankQuestion, text: string): Promise<void> {
    const trimmed = text.trim();
    if (trimmed === question.text) {
      this.refreshRow(question.id);
      return;
    }
    if (trimmed.length < MINIMUM_QUESTION_CHARS) {
      // Silently declining would leave the reader's too-short text sitting in
      // the grid as though the bank held it.
      this.refreshRow(question.id);
      this.error.set(
        `A question needs at least ${MINIMUM_QUESTION_CHARS} characters. Put back as it was.`,
      );
      return;
    }
    await this.whileBusy(async () => {
      await this.patchQuestion(question, { text: trimmed });
      this.flash.set('Question saved.');
    });
  }

  /** The ticked rows, enabled or paused together. One PATCH each, because that
   *  is the endpoint there is; the audit trail then names every one of them. */
  async setEnabledOnSelection(enabled: boolean): Promise<void> {
    const chosen = (this.questions() ?? []).filter(
      (question) => this.selectedQuestionIds().has(question.id) && question.enabled !== enabled,
    );
    if (chosen.length === 0 || this.busy()) return;
    await this.whileBusy(async () => {
      for (const question of chosen) {
        await this.patchQuestion(question, { enabled });
      }
      this.flash.set(this.selectionFlashFor(chosen.length, enabled));
      await this.loadTracks();
    });
  }

  async removeQuestion(question: BankQuestion): Promise<void> {
    const confirmed = confirm('Remove this question?');
    if (!confirmed) return;
    await this.whileBusy(async () => {
      const response = await fetch(
        `${environment.apiBase}/admin/interview-questions/${question.id}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      this.questions.update((list) => (list ?? []).filter((row) => row.id !== question.id));
      this.forgetSelection(question.id);
      this.flash.set('Question removed.');
      await this.loadTracks();
    });
  }

  // ------------------------------------------------------------- order --

  async moveQuestionUp(question: BankQuestion): Promise<void> {
    await this.moveQuestion(question, -1);
  }

  async moveQuestionDown(question: BankQuestion): Promise<void> {
    await this.moveQuestion(question, 1);
  }

  // ------------------------------------------------------ grid furniture --

  onQuickFilterInput(event: Event): void {
    this.quickFilter.set(this.inputValue(event));
    this.pageIndex.set(0);
  }

  setPageSize(size: number): void {
    this.pageSize.set(size);
    this.pageIndex.set(0);
  }

  goToPreviousPage(): void {
    this.pageIndex.set(Math.max(0, this.currentPage() - 1));
  }

  goToNextPage(): void {
    this.pageIndex.set(Math.min(this.pageCount() - 1, this.currentPage() + 1));
  }

  toggleRowSelection(question: BankQuestion): void {
    this.selectedQuestionIds.update((selected) => {
      const next = new Set(selected);
      if (next.has(question.id)) {
        next.delete(question.id);
      } else {
        next.add(question.id);
      }
      return next;
    });
  }

  toggleSelectionOnPage(): void {
    const idsOnPage = this.pageRows().map((row) => row.question.id);
    const clearing = this.everyRowOnPageIsSelected();
    this.selectedQuestionIds.update((selected) => {
      const next = new Set(selected);
      for (const id of idsOnPage) {
        if (clearing) {
          next.delete(id);
        } else {
          next.add(id);
        }
      }
      return next;
    });
  }

  // --------------------------------------------------------- event reads --

  inputValue(event: Event): string {
    const target = event.target as HTMLInputElement;
    return target.value;
  }

  textAreaValue(event: Event): string {
    const target = event.target as HTMLTextAreaElement;
    return target.value;
  }

  selectValue(event: Event): string {
    const target = event.target as HTMLSelectElement;
    return target.value;
  }

  numberValue(event: Event): number {
    return Number(this.selectValue(event));
  }

  checkboxValue(event: Event): boolean {
    return (event.target as HTMLInputElement).checked;
  }

  // ------------------------------------------------------------ plumbing --

  private rowMatches(row: QuestionRow, needle: string): boolean {
    if (row.question.text.toLowerCase().includes(needle)) return true;
    return this.phaseLabel(row.question.phase).toLowerCase().includes(needle);
  }

  private async moveQuestion(question: BankQuestion, direction: -1 | 1): Promise<void> {
    if (!this.reorderIsAvailable()) return;
    const ordered = [...(this.questions() ?? [])];
    const from = ordered.findIndex((row) => row.id === question.id);
    const to = from + direction;
    if (from < 0 || to < 0 || to >= ordered.length) return;
    const moved = ordered[from];
    ordered[from] = ordered[to];
    ordered[to] = moved;
    const read = this.questionsRead;
    await this.whileBusy(async () => {
      const response = await this.postJson(
        `${environment.apiBase}/admin/interview-questions/reorder`,
        { track: this.selectedTrackKey(), ids: ordered.map((row) => row.id) },
      );
      if (!response.ok) throw new Error(await this.detailOf(response));
      const reordered = (await response.json()) as BankQuestion[];
      if (read === this.questionsRead) this.questions.set(reordered);
    });
  }

  private async patchQuestion(
    question: BankQuestion,
    change: { phase?: string; text?: string; enabled?: boolean },
  ): Promise<void> {
    const response = await fetch(
      `${environment.apiBase}/admin/interview-questions/${question.id}`,
      {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(change),
      },
    );
    if (!response.ok) {
      this.refreshRow(question.id);
      throw new Error(await this.detailOf(response));
    }
    const updated = (await response.json()) as BankQuestion;
    this.questions.update((list) =>
      (list ?? []).map((row) => (row.id === updated.id ? updated : row)),
    );
  }

  /** Rebuild one question's row from the questions signal. */
  private refreshRow(questionId: string): void {
    this.rowGenerations.update((generations) =>
      new Map(generations).set(questionId, (generations.get(questionId) ?? 0) + 1),
    );
  }

  private forgetSelection(questionId: string): void {
    this.selectedQuestionIds.update((selected) => {
      const next = new Set(selected);
      next.delete(questionId);
      return next;
    });
  }

  private bulkFlashFor(addedCount: number, skippedCount: number): string {
    const added = `${plural(addedCount, 'question')} added.`;
    if (skippedCount === 0) return added;
    return `${added} ${plural(skippedCount, 'line')} skipped.`;
  }

  private selectionFlashFor(count: number, enabled: boolean): string {
    const questions = plural(count, 'question');
    if (enabled) return `${questions} enabled.`;
    return `${questions} paused.`;
  }

  private async whileBusy(work: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await work();
    } catch (failure) {
      this.error.set(failure instanceof Error ? failure.message : 'Could not reach the server.');
    } finally {
      this.busy.set(false);
    }
  }

  private postJson(url: string, body: unknown): Promise<Response> {
    return fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  private async loadTracksAndQuestions(): Promise<void> {
    await this.loadTracks();
    await this.loadQuestions();
  }

  private async loadTracks(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/interview-questions/tracks`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error(String(response.status));
      const loaded = (await response.json()) as InterviewTrack[];
      this.tracks.set(loaded);
      // The first key is a guess until /tracks answers - the matrix is code and
      // could be renamed there. Landing on a track the server does not have
      // means every later request answers 422 and the screen reads as broken,
      // so the guess yields to the first track the server actually named.
      if (loaded.length > 0 && !loaded.some((track) => track.key === this.selectedTrackKey())) {
        this.selectedTrackKey.set(loaded[0].key);
      }
      this.syncTrackDraft();
    } catch {
      this.error.set('Could not load the interview tracks. Reload the page to try again.');
      this.tracks.set([]);
    }
  }

  /** Which read of the list is the one on screen. Every `loadQuestions()`
   *  takes the next number, and an answer under an older one is dropped: the
   *  list it describes is no longer the one being looked at.
   *
   *  THE LAST ANSWER IS NOT THE LAST TRACK PICKED. The screen reads the first
   *  track's questions as it opens, and a tab pressed before that read answers
   *  used to be overwritten by it: "Questions · 0" under a track whose tab
   *  said 1. An add or a reorder carries the number it started under for the
   *  same reason — its answer belongs to the list it was made against, and a
   *  track picked meanwhile has already been read from the server. */
  private questionsRead = 0;

  private async loadQuestions(): Promise<void> {
    const read = ++this.questionsRead;
    this.questions.set(null);
    const track = encodeURIComponent(this.selectedTrackKey());
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/interview-questions?track=${track}`,
        { credentials: 'include' },
      );
      if (read !== this.questionsRead) return;
      if (!response.ok) throw new Error(String(response.status));
      const loaded = (await response.json()) as BankQuestion[];
      if (read !== this.questionsRead) return;
      this.questions.set(loaded);
    } catch {
      if (read !== this.questionsRead) return;
      this.error.set('Could not load the questions for this track.');
      this.questions.set([]);
    }
  }

  /** FastAPI answers a schema error with `detail` as a LIST; rendered raw it
   *  reads "[object Object]". Its refusals name the rule ("track must be one
   *  of …"), which a generic message would throw away. */
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
    return `The request was refused (${response.status}).`;
  }
}

/** A comma- or newline-separated field as the list the API takes. Empty entries
 *  are dropped here as well as on the server, so a trailing comma is not a
 *  blank framework the interviewer would try to work through. */
function splitList(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}
