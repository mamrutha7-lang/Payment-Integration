/**
 * Institution structure — the Main Admin's College → Department → Course →
 * Specialization → Batch console.
 *
 * THIS IS THE FIRST CALLER OF /api/admin/*. The write layer shipped with
 * twenty operations and no screen, which meant "optional to fill in the UI"
 * was true only in the sense that mattered least — there was no UI. Every
 * level here is created, listed and edited through those endpoints; nothing
 * is built through a seed.
 *
 * ONE COLLEGE AT A TIME, TOP TO BOTTOM (2026-09-15). The screen used to be a
 * rail of indented tree rows beside a stack of detail cards, with the add
 * forms opening inside the rail. It is now the setup screen's shape: the
 * college is picked in ONE select at the top (`?college=<id>` preselects it,
 * which is how the Colleges card's "Open" lands here), and under it come five
 * sections in the spine's own order — the college and its email domains, its
 * departments, the picked department's courses (the picked course editable in
 * place), the picked course's specializations with their mock interview, and
 * the picked department's batches with seating. A department or course is a
 * bordered row that opens on press. Each level is still one fetch keyed on its
 * parent — /colleges/{id}/departments, /departments/{id}/academic-courses —
 * so the screen shows exactly what the API can answer and never a branch
 * nobody asked for.
 *
 * THIS SCREEN SEES AND CHANGES; IT DOES NOT ADD. The inline "Add college",
 * "Add department", "Add course" and "Add specialization" forms it carried
 * were the same five POSTs the setup flow makes, typed one at a time on a
 * second screen, and the owner named the repetition. Each "Add a …" here is
 * now a link into `/admin/setup?college=<id>&step=<n>`, which loads this
 * college and opens on that step. What stays is everything the setup flow
 * cannot do: edit or archive a course, map a mock interview, edit a batch,
 * add a NON-standard batch (a section, odd dates — the setup flow only writes
 * one per leaf on the academic year), seat students, and the email domains.
 *
 * THE BATCH FORM'S VALIDATORS COME FROM THE SERVER. `HierarchySchemaService`
 * fetches which of Course / Specialization is required; `buildBatchForm`
 * attaches `Validators.required` from that, and the "Required" / "Optional"
 * chip beside each select is the same flag. The word "Course" is never typed
 * in this file — it arrives as `level.label`. Flip the constant on the API and
 * this form follows with no edit.
 *
 * A BATCH IS A YEAR, AND THE FORM ASKS FOR IT AS TWO. The batch label is a
 * span — "2026-28" — and the course and the specialization it hangs off are
 * the selects below it, not part of its name. So the label is picked as a
 * Start year and an End year (`core/batch-year.ts`, which agrees with
 * `app/seed_catalogue.py`'s `batch_dates`) and composed on save, and the entry
 * and completion dates fill themselves in from the span while staying
 * editable — this screen is where a NON-STANDARD batch with odd dates is
 * added. A row whose stored label is not a span keeps the text box; see
 * `setLabelEditor`.
 *
 * GRANDFATHERED ROWS STAY EDITABLE. A batch created while a level was optional
 * is blank there after the flip. The server lists it under
 * /cohorts/incomplete, flags it in `missing_levels`, and still accepts any
 * PATCH that does not widen the gap. So this form marks the control as
 * required (the admin should know) but excludes it from the save gate (the row
 * must not be bricked) — a validator's job is to TELL, the gate's job is to
 * REFUSE, and conflating them is how a switch becomes a lockout.
 *
 * CASCADING SELECTS ENFORCE CONTIGUITY. A specialization's parent is a course,
 * so "specialization, no course" is not representable in the database. The
 * specialization select is disabled until a course is picked, and changing the
 * course clears it. The server checks the same thing (a 422 from
 * _resolve_ancestry); the cascade just makes it unreachable from here.
 *
 * WHAT THE BOARD DRAWS, AND WHAT IS BEHIND EACH PIECE OF IT (2026-09-13).
 * Three of the board's facts had no endpoint when this screen was built.
 * Phase 3 and Phase 4 have both landed and they did not land equally:
 *
 *   - the college's registration email domains (B1.1) are REAL —
 *     `colleges.email_domains`, read and written through
 *     `PATCH /api/admin/colleges/{id}`;
 *   - the specialization's INTERVIEW TRACK is REAL (B5.1). The mapping is the
 *     TRACK's field, not the specialization's, so the Track column is read
 *     from `GET /api/admin/interview-questions/tracks` and "Map track" writes
 *     `PATCH .../tracks/{id}` with `{specialization_id}`. That endpoint asks
 *     for a different capability from this screen's, so the column has THREE
 *     states and not two — see the B5.1 block below;
 *   - the course's DEGREE LEVEL and TOTAL SEMESTERS are columns that exist and
 *     are read, with no endpoint that writes them, and SEMESTERS PER YEAR is
 *     not a field anywhere. Those three boxes are plainly `disabled` with the
 *     reason on them and carry NO phase number — see `COURSE_SHAPE_REASON` and
 *     `SEMESTERS_PER_YEAR_REASON`. The batches grid still has no "semester x
 *     of N" column, and now for a sharper reason: a batch has no semester at
 *     all, a STUDENT does.
 *
 * The board's "chart colour" per track is drawn nowhere here, because there is
 * no colour on `interview_tracks` and none anywhere in `apps/api-py/app`. The
 * card's subtitle says what the mapping actually decides instead.
 *
 * Degree level on a BATCH is real today (`cohorts.degree_level`) and stays an
 * ordinary control on the batch form. It is the COURSE-level one the board
 * shows that has nothing to write to.
 */

import { DatePipe } from '@angular/common';
import { Component, OnDestroy, computed, inject, signal } from '@angular/core';
import {
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { environment } from '../../../../environments/environment';
import {
  endYearOptions,
  formatYearSpan,
  parseYearSpan,
  spanDates,
  startYearOptions,
  type YearSpan,
} from '../../../core/batch-year';
import { HierarchyLevel, HierarchySchemaService } from '../../../core/hierarchy-schema.service';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';

// ---- exact snake_case shapes of the admin router's Out models -------------

interface CollegeOut {
  id: string;
  code: string;
  name: string;
  campus: string | null;
  contact: string | null;
  status: string;
  department_count: number;
  /** B1.1. The addresses this college will admit an applicant on.
   *
   *  EMPTY IS NOT "NO FENCE". `app/institution_domains.py` reads an empty list
   *  as "this college has named none of its own", and falls back to the
   *  deployment's `provisionable_email_domains` — so an applicant is still
   *  fenced, by the environment rather than by the row. The API's own schema
   *  comment says to render it that way and never as "nobody may join", and
   *  this screen used to print a hardcoded "None recorded" chip that said the
   *  opposite of both. */
  email_domains: string[];
}

interface DepartmentOut {
  id: string;
  college_id: string;
  code: string;
  name: string;
  head: string | null;
  status: string;
  cohort_count: number;
}

interface AcademicCourseOut {
  id: string;
  department_id: string;
  code: string;
  name: string;
  duration_months: number | null;
  status: string;
  specialization_count: number;
}

interface AcademicSpecializationOut {
  id: string;
  course_id: string;
  code: string;
  name: string;
  status: string;
  cohort_count: number;
}

interface AdminStudentRowOut {
  student_id: string;
  name: string;
  email: string;
  usn: string | null;
  current_stage: string | null;
  cohort_id: string | null;
}

interface AdminCohortOut {
  id: string;
  department_id: string | null;
  course_id: string | null;
  specialization_id: string | null;
  code: string;
  /** The batch itself: a YEAR, "2026-28". */
  name: string;
  batch_label: string;
  course_name: string | null;
  specialization_name: string | null;
  /** The spine and the year in one sentence, composed by the server from the
   *  three links above: "General MBA - Finance · 2026-28". Render this — one
   *  department's four batches all say "2026-28" on their own. */
  display_label: string;
  degree_level: string;
  entry_date: string;
  expected_completion: string;
  student_count: number;
  missing_levels: string[];
}

/** One row of `GET /api/admin/interview-questions/tracks` (B5.1), cut down to
 *  what this card needs. The full `AdminTrackOut` carries the persona, the
 *  frameworks, the Nova voice and the syllabus — those belong to the Question
 *  bank screen, and reading fields this card never renders would invite
 *  editing them from a screen whose capability is a different one.
 *
 *  `id` IS NULLABLE AND THAT IS THE WHOLE FILTER. A null id is a track that is
 *  still only a constant in `app/interview_matrix.py` (`source: 'code'`): it
 *  runs real interviews and there is no row to PATCH, so it can be shown and
 *  never mapped. `editable` is the server's own answer to "does this session's
 *  grant reach this track" — a convenience for the form, never the fence; the
 *  endpoint refuses regardless. */
interface AdminTrackOut {
  id: string | null;
  code: string;
  label: string;
  enabled: boolean;
  specialization_id: string | null;
  source: string;
  editable: boolean;
}

/** The shape `PATCH /tracks/{id}` answers with: the row, plus advice that was
 *  not worth refusing over (`AdminTrackWriteResult`). */
interface AdminTrackWriteResult {
  track: AdminTrackOut;
  warnings: string[];
}

type ScreenState = 'loading' | 'ready' | 'error';

/** The three optional-level keys the form binds; anything else is a bug in HIERARCHY_LEVELS. */
const LEVEL_OPTIONS: Record<string, 'courses' | 'specializations'> = {
  course: 'courses',
  specialization: 'specializations',
};

/** The two values PATCH accepts for any level's `status` (_SETTABLE_STATUSES). */
const COURSE_STATUSES = ['ACTIVE', 'ARCHIVED'];

/** The year the Start dropdown's window is built around, read once at load.
 *  A console left open across New Year offers one stale year at each end of a
 *  sixteen-year list, and `startYearOptions`' `include` keeps every batch this
 *  screen can open selectable regardless — so re-reading the clock per render
 *  would buy nothing and make the option list change under a mouse. */
const THIS_YEAR = new Date().getFullYear();

/** SEMESTERS PER YEAR IS NOT A DIFFERENT PHASE, IT IS NOT A FIELD.
 *
 *  There is no `semesters_per_year` column anywhere in `apps/api-py/app`, and
 *  04-backend-changes.md never asks for one — the 4a decisions go the other
 *  way and strike `duration_years` for the same reason, that one fact stored
 *  twice is one fact that drifts. `duration_months` and `total_semesters` are
 *  how a programme's shape is recorded; a third number derived from them would
 *  round 18 months into the wrong answer. The box stays because the board
 *  draws it, and says what it is. */
/** The three reasons `tracks()` can be null. They are separate sentences on
 *  purpose: a refusal is a fact about this account's grant that the reader can
 *  act on, a failure is worth retrying, and "not attempted" is neither. None
 *  of them may render as "Not mapped", which is a claim about the data. */
const TRACKS_NOT_READ_NOTE =
  'The interview tracks have not been read yet, so this column is not ' +
  'reporting whether a specialization is mapped.';
const TRACKS_REFUSED_NOTE =
  'The interview tracks are read through Interview questions, which is a ' +
  'different function from this screen\'s. Ask the Main Admin for ' +
  'admin.interview_questions in Who can do what.';
const TRACKS_FAILED_NOTE =
  'The interview tracks could not be read, so this column is not reporting ' +
  'whether a specialization is mapped. Reload the screen to try again.';

@Component({
  selector: 'app-admin-institution',
  standalone: true,
  imports: [DatePipe, ReactiveFormsModule, RouterLink, PluralPipe],
  templateUrl: './institution.component.html',
  styleUrl: './institution.component.scss',
})
export class AdminInstitutionComponent implements OnDestroy {
  private readonly schema = inject(HierarchySchemaService);
  /** `?college=<id>`: the Colleges card's "Open" names the college to pick.
   *  Read once; an id that is not on the list falls back to the first. */
  private readonly wantedCollegeId = inject(ActivatedRoute).snapshot.queryParamMap.get('college');

  /** Read by the template so the phase sentence is written once, here. */
  // ------------------------------------------------------- B1.1 domains --
  //
  // The college's own fence. Held as a draft so the input is not a write on
  // every keystroke, and committed through PATCH /admin/colleges/{id}, whose
  // `email_domains` is explicitly NOT in NON_NULLABLE: the list is emptied by
  // sending `[]`, and emptying it restores the deployment fallback, which is a
  // real thing an admin may want and is not the same as sending null.
  readonly domainDraft = signal('');
  readonly domainBusy = signal(false);
  readonly domainError = signal<string | null>(null);

  /** The fence as a sentence, because an empty list and a recorded one are
   *  different facts and a bare chip count cannot say which. */
  readonly domainSummary = computed(() => {
    const college = this.selectedCollege();
    if (!college) return '';
    return college.email_domains.length
      ? `${college.email_domains.length} recorded on this college`
      : 'None on this college — the deployment\'s own list applies';
  });

  async addDomain(): Promise<void> {
    const college = this.selectedCollege();
    const typed = this.domainDraft().trim().replace(/^@/, '').toLowerCase();
    if (!college || !typed) return;
    if (college.email_domains.includes(typed)) {
      this.domainError.set(`${typed} is already on this college.`);
      return;
    }
    await this.saveDomains(college, [...college.email_domains, typed]);
    this.domainDraft.set('');
  }

  async removeDomain(domain: string): Promise<void> {
    const college = this.selectedCollege();
    if (!college) return;
    await this.saveDomains(
      college,
      college.email_domains.filter((d) => d !== domain),
    );
  }

  /** ONE writer for both directions. The server normalises and de-duplicates
   *  (`_clean_domains`), so the row it returns is the truth and is what the
   *  screen adopts — echoing the list that was sent would show an admin their
   *  own typing rather than what was stored. */
  private async saveDomains(college: CollegeOut, domains: string[]): Promise<void> {
    this.domainBusy.set(true);
    this.domainError.set(null);
    try {
      const saved = await this.patch<CollegeOut>(`/admin/colleges/${college.id}`, {
        email_domains: domains,
      });
      if (!saved) {
        this.domainError.set(this.error());
        this.error.set(null);
        return;
      }
      this.colleges.update((list) => list.map((c) => (c.id === saved.id ? saved : c)));
    } finally {
      this.domainBusy.set(false);
    }
  }

  readonly courseStatuses = COURSE_STATUSES;


  // ======================================================================
  // B5.1 — which interview a specialization's students sit
  //
  // THE MAPPING IS THE TRACK'S FIELD, NOT THE SPECIALIZATION'S.
  // `interview_tracks.specialization_id` is a nullable pointer on the track
  // row, so the write is `PATCH /api/admin/interview-questions/tracks/{id}`
  // with `{specialization_id}` and the server derives the course and the
  // college above it (`_resolve_spine`). There is no column on
  // `academic_specializations` for this and the card must not invent one: a
  // second place holding the same pairing is a second place to edit and one
  // to forget.
  //
  // THE READ IS A DIFFERENT CAPABILITY FROM THIS SCREEN'S.
  // `/admin/interview-questions/tracks` asks for `admin.interview_questions`
  // while this screen opens on `admin.institution`, so a granted faculty
  // member reaches the structure and is refused the tracks. That is a fact
  // about the grant, not a failure, and it must not render as "Not mapped" —
  // "we did not read this" and "nothing is mapped" are opposite facts about
  // every row in the column. `tracks()` stays NULL when the read did not
  // happen and the cells say "Not read" with the reason on them.
  // ======================================================================

  /** Every track this session may see, or NULL when the read did not happen —
   *  refused, failed, or not yet attempted. Never `[]` for those: an empty
   *  array means "read, and there are none". */
  readonly tracks = signal<AdminTrackOut[] | null>(null);
  /** Why `tracks()` is null, in the words the cells and the notice both use. */
  readonly trackReadNote = signal<string>(TRACKS_NOT_READ_NOTE);
  /** The read has FINISHED, whatever it answered. The notice waits for this so
   *  the half-second before the fetch resolves does not announce "the tracks
   *  have not been read" about a read that is in flight. */
  readonly tracksChecked = signal(false);
  readonly trackPanelOpen = signal(false);
  /** The track being mapped — its `id`, so a constant-only track cannot be
   *  chosen at all. */
  readonly trackChoice = signal<string>('');
  /** The specialization it maps to; empty string is "not mapped". */
  readonly trackTarget = signal<string>('');
  readonly trackBusy = signal(false);
  readonly trackError = signal<string | null>(null);

  /** The tracks this card can actually write: a row (not a constant) that the
   *  server says this session's grant reaches. */
  readonly mappableTracks = computed(() =>
    (this.tracks() ?? []).filter((t) => t.id !== null && t.editable),
  );

  readonly canMapTracks = computed(() => this.mappableTracks().length > 0);

  /** The disabled button's own sentence. Three different reasons reach here
   *  and they are not interchangeable: not read at all, read and empty, read
   *  and every row belongs to somebody else's college. */
  mapTrackTitle(): string {
    if (this.tracks() === null) return this.trackReadNote();
    if (!this.mappableTracks().length) {
      return (
        'No interview track is yours to edit here. The four shipped tracks are ' +
        'constants in code until the office saves one as a row on the Question ' +
        'bank screen, and a track filed under another college is not reached by ' +
        'your grant.'
      );
    }
    return 'Map an interview track to one of this course\'s specializations';
  }

  /** The tracks mapped to one specialization. Empty is a real answer and the
   *  template distinguishes it from `tracks() === null`. */
  tracksFor(specializationId: string): AdminTrackOut[] {
    return (this.tracks() ?? []).filter((t) => t.specialization_id === specializationId);
  }

  toggleTrackPanel(): void {
    const opening = !this.trackPanelOpen();
    this.trackPanelOpen.set(opening);
    this.trackError.set(null);
    if (!opening) return;
    const first = this.mappableTracks()[0];
    this.pickTrack(first?.id ?? '');
  }

  /** Choosing a track PRE-FILLS its current mapping, so the panel opens on the
   *  truth rather than on "Not mapped" — saving without touching the second
   *  select would otherwise unmap a track the admin only meant to look at. */
  pickTrack(trackId: string): void {
    this.trackChoice.set(trackId);
    const track = this.mappableTracks().find((t) => t.id === trackId) ?? null;
    this.trackTarget.set(track?.specialization_id ?? '');
    this.trackError.set(null);
  }

  /** One PATCH for both directions.
   *
   *  CLEARING SENDS `course_id: null` TOO. `_resolve_spine` merges what was
   *  not sent with the row as it stands, so clearing the specialization alone
   *  would leave the course pointer the specialization had derived and the
   *  track would still be narrowed — to the course this time, silently, with
   *  the cell reading "Not mapped". Both pointers go together; the college, if
   *  the admin filed one, is not touched. */
  async saveTrackMapping(): Promise<void> {
    const trackId = this.trackChoice();
    if (!trackId) return;
    const target = this.trackTarget();
    this.trackBusy.set(true);
    this.trackError.set(null);
    this.flash.set(null);
    try {
      const res = await fetch(
        `${environment.apiBase}/admin/interview-questions/tracks/${trackId}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(
            target ? { specialization_id: target } : { specialization_id: null, course_id: null },
          ),
        },
      );
      if (!res.ok) {
        this.trackError.set(await this.detailOf(res));
        return;
      }
      const saved = (await res.json()) as AdminTrackWriteResult;
      this.tracks.update((list) =>
        (list ?? []).map((t) => (t.id === saved.track.id ? saved.track : t)),
      );
      const spec = this.specializations().find((sp) => sp.id === target);
      this.flash.set(
        spec
          ? `${saved.track.label} is the interview for ${spec.code}.`
          : `${saved.track.label} is offered to every specialization again.`,
      );
      this.trackPanelOpen.set(false);
    } catch {
      this.trackError.set('Could not reach the server.');
    } finally {
      this.trackBusy.set(false);
    }
  }

  /** Read once, at load. Its own try/catch and never `this.error`: the tracks
   *  are one column of one card, and a screen that refused to draw the
   *  institution because the Question bank said 403 would be reporting an
   *  outage that is not happening. */
  private async loadTracks(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/admin/interview-questions/tracks`, {
        credentials: 'include',
      });
      if (res.status === 403) {
        this.tracks.set(null);
        this.trackReadNote.set(TRACKS_REFUSED_NOTE);
        return;
      }
      if (!res.ok) {
        this.tracks.set(null);
        this.trackReadNote.set(TRACKS_FAILED_NOTE);
        return;
      }
      this.tracks.set((await res.json()) as AdminTrackOut[]);
    } catch {
      this.tracks.set(null);
      this.trackReadNote.set(TRACKS_FAILED_NOTE);
    } finally {
      this.tracksChecked.set(true);
    }
  }

  // ---- screen -------------------------------------------------------------
  readonly state = signal<ScreenState>('loading');
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);
  readonly flash = signal<string | null>(null);

  /** The switch, as served. Labels and required flags both come from here. */
  readonly levels = signal<HierarchyLevel[]>([]);

  // ---- the hierarchy ------------------------------------------------------
  readonly colleges = signal<CollegeOut[]>([]);
  readonly selectedCollegeId = signal<string | null>(null);
  readonly departments = signal<DepartmentOut[]>([]);
  readonly selectedDepartmentId = signal<string | null>(null);
  readonly courses = signal<AcademicCourseOut[]>([]);
  readonly selectedCourseId = signal<string | null>(null);
  readonly specializations = signal<AcademicSpecializationOut[]>([]);

  readonly selectedCollege = computed(
    () => this.colleges().find((c) => c.id === this.selectedCollegeId()) ?? null,
  );
  readonly selectedDepartment = computed(
    () => this.departments().find((d) => d.id === this.selectedDepartmentId()) ?? null,
  );
  readonly selectedCourse = computed(
    () => this.courses().find((c) => c.id === this.selectedCourseId()) ?? null,
  );

  // ---- the rail's finder --------------------------------------------------
  /** What was typed into the finder.
   *
   *  IT DOES NOT FILTER COLLEGES: the college is picked in its own select, and
   *  the finder narrows the departments, courses, specializations and batches
   *  under it. Filtering the top level too reads as a bug the moment anyone
   *  uses it — typing a department's name would hide the college it is under,
   *  and the branch the admin was looking for goes with it. */
  readonly finderText = signal('');

  readonly visibleDepartments = computed(() =>
    this.departments().filter((d) => this.rowMatchesFinder(d.code, d.name)),
  );
  readonly visibleCourses = computed(() =>
    this.courses().filter((c) => this.rowMatchesFinder(c.code, c.name)),
  );
  readonly visibleSpecializations = computed(() =>
    this.specializations().filter((s) => this.rowMatchesFinder(s.code, s.name)),
  );
  readonly visibleBatches = computed(() =>
    // `display_label` and not `name`: the batch's name is the year, so a finder
    // over it alone could not answer "Finance" — the course and the
    // specialization live on the links, and the composed label is where they
    // are words again.
    this.batches().filter((b) => this.rowMatchesFinder(b.code, b.display_label)),
  );

  // ---- batches ------------------------------------------------------------
  readonly batches = signal<AdminCohortOut[]>([]);
  /** Batches missing a now-required level — the morning-after-the-flip inbox. */
  readonly incomplete = signal<AdminCohortOut[]>([]);
  /** Batches filed under no department — the other inbox. */
  readonly unassigned = signal<AdminCohortOut[]>([]);

  /** Course options for the batch form (the selected department's), and the
   *  specialization options for whichever course the FORM has picked — which
   *  may differ from the structure column's selection. */
  readonly formSpecializations = signal<AcademicSpecializationOut[]>([]);

  readonly batchMode = signal<'closed' | 'create' | 'edit'>('closed');
  readonly editingBatchId = signal<string | null>(null);
  /** Levels required NOW that were blank on the row AS LOADED. See class doc. */
  readonly grandfathered = signal<Set<string>>(new Set());
  readonly batchError = signal<string | null>(null);

  // ---- the batch's year span ---------------------------------------------
  //
  // A BATCH IS A YEAR, so the office picks two of them rather than typing the
  // string. `batch_label` is still the one carrier of what is stored and sent
  // — the selects write it through `syncSpan` — because the wire format,
  // `saveBatch` and every reader of the row are unchanged by this; what
  // changed is only who composes it. `core/batch-year.ts` holds the rules and
  // agrees with `app/seed_catalogue.py`'s `batch_dates`, so a batch made here
  // and one made by the seeder are the same row.
  //
  // THE SELECTS ARE NOT ALWAYS THE EDITOR. `cohorts.batch_label` is free text
  // on the wire (`AdminCohortIn` bounds it 1..64 and checks nothing else), and
  // rows that are not spans are deliberate: "Chain Batch", "2026-28 Section
  // B". Opening one of those falls back to the text box — see `setLabelEditor`.
  readonly spanStart = signal<number | null>(null);
  readonly spanEnd = signal<number | null>(null);
  /** The span the open row arrived with, or null for a create and for a label
   *  that is not a span. It is the `include` both option lists take, so a
   *  batch that started outside the default window still has its own years to
   *  select. */
  private readonly loadedSpan = signal<YearSpan | null>(null);
  /** True while `batch_label` is edited as text rather than as two years. */
  readonly labelIsFreeText = signal(false);
  /** Whether a human has typed in each date box. See `markDateEdited`. */
  private readonly entryDateEdited = signal(false);
  private readonly completionDateEdited = signal(false);

  readonly startYearChoices = computed(() =>
    startYearOptions(THIS_YEAR, this.loadedSpan()?.start ?? null),
  );

  readonly endYearChoices = computed(() => {
    const start = this.spanStart();
    if (start === null) return [];
    // The stored end year is folded in only while the start is still the
    // stored one. It is there so an existing out-of-window span stays
    // selectable; once the admin has moved the start it is not this select's
    // value any more, just a year out of a span that no longer exists.
    const loaded = this.loadedSpan();
    return endYearOptions(start, loaded && loaded.start === start ? loaded.end : null);
  });

  /** The cascade, the specialization select's rule applied to years: an end
   *  year means nothing without a start, and `endYearOptions` answers an empty
   *  list for one, so the control would otherwise be an empty box that looks
   *  broken rather than one that is waiting. */
  readonly endYearDisabled = computed(() => this.spanStart() === null);

  // --- seating: who is in a batch, and who is in none ---------------------
  // The panel for one batch. Opening it loads two lists that must agree with
  // the write: a student is in exactly one of them.
  readonly seatingBatch = signal<AdminCohortOut | null>(null);
  readonly seated = signal<AdminStudentRowOut[]>([]);
  readonly unseated = signal<AdminStudentRowOut[]>([]);
  readonly seatPick = signal<string | null>(null);

  batchForm: FormGroup = this.buildBatchForm([]);

  // ---- the course card ----------------------------------------------------
  /** Code / name / duration / status — the four the PATCH accepts today. */
  readonly courseForm = new FormGroup({
    code: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    name: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    duration_months: new FormControl('', { nonNullable: true }),
    status: new FormControl('ACTIVE', { nonNullable: true }),
  });
  readonly courseError = signal<string | null>(null);
  readonly courseSaveBlocked = computed(() => this.busy() || !this.courseFormIsComplete());
  private readonly courseFormIsComplete = signal(false);

  readonly degreeLevels = ['UG', 'PG'];

  /** Keeps `courseFormIsComplete` honest while the admin types. A form's
   *  validity is not a signal, so a `computed` over it would cache the answer
   *  from before the field was cleared and leave Save enabled. */
  private readonly courseFormWatch: Subscription;

  constructor() {
    this.courseFormWatch = this.courseForm.statusChanges.subscribe(() => {
      this.courseFormIsComplete.set(this.courseForm.valid);
    });
    void this.load();
  }

  ngOnDestroy(): void {
    this.courseFormWatch.unsubscribe();
    this.batchFormWatch?.unsubscribe();
  }

  // =========================================================================
  // loading
  // =========================================================================

  async load(): Promise<void> {
    this.state.set('loading');
    this.error.set(null);
    try {
      const [levels, colleges, incomplete, unassigned] = await Promise.all([
        this.schema.load(),
        this.get<CollegeOut[]>('/admin/colleges'),
        this.get<AdminCohortOut[]>('/admin/cohorts/incomplete'),
        this.get<AdminCohortOut[]>('/admin/cohorts/unassigned'),
      ]);
      this.levels.set(levels);
      this.batchForm = this.buildBatchForm(levels);
      this.colleges.set(colleges);
      // Deliberately NOT in the Promise.all above and deliberately not
      // awaited-into the failure path: `loadTracks` swallows its own errors
      // because a 403 from the Question bank is a fact about this account's
      // grant, not a reason to tell an admin the institution could not load.
      void this.loadTracks();
      this.incomplete.set(incomplete);
      this.unassigned.set(unassigned);
      if (colleges.length && !this.selectedCollegeId()) {
        const wanted = colleges.find((c) => c.id === this.wantedCollegeId);
        await this.pickCollege((wanted ?? colleges[0]).id);
      }
      this.state.set('ready');
    } catch (e) {
      this.error.set(
        e instanceof TypeError
          ? 'Could not reach the server.'
          : 'The institution structure could not be loaded.',
      );
      this.state.set('error');
    }
  }

  /** Every drill-down read goes through here.
   *
   *  `get` throws on a non-2xx, and these methods are called straight from
   *  click handlers. Without this the rejection is unhandled: the rail simply
   *  stays empty, with no notice and no console state saying why a 403 on
   *  `admin.institution` or a 500 stopped it. A screen that fails silently is
   *  worse than one that fails loudly, because nobody files it.
   */
  private async guarded(work: () => Promise<void>): Promise<void> {
    try {
      await work();
    } catch (e) {
      this.error.set(
        e instanceof TypeError
          ? 'Could not reach the server.'
          : 'That part of the institution structure could not be loaded.',
      );
    }
  }

  async pickCollege(id: string | null): Promise<void> {
    this.selectedCollegeId.set(id);
    this.selectedDepartmentId.set(null);
    this.departments.set([]);
    this.courses.set([]);
    this.specializations.set([]);
    this.batches.set([]);
    this.closeBatchForm();
    this.closeSeating();
    if (!id) return;
    await this.guarded(async () => {
      const departments = await this.get<DepartmentOut[]>(`/admin/colleges/${id}/departments`);
      this.departments.set(departments);
      if (departments.length) await this.pickDepartment(departments[0].id);
    });
  }

  async pickDepartment(id: string | null): Promise<void> {
    this.selectedDepartmentId.set(id);
    this.selectedCourseId.set(null);
    this.courses.set([]);
    this.specializations.set([]);
    this.batches.set([]);
    this.closeBatchForm();
    this.closeSeating();
    if (!id) return;
    await this.guarded(async () => {
      const [courses, batches] = await Promise.all([
        this.get<AcademicCourseOut[]>(`/admin/departments/${id}/academic-courses`),
        this.get<AdminCohortOut[]>(`/admin/departments/${id}/cohorts`),
      ]);
      this.courses.set(courses);
      this.batches.set(batches);
      // The grid names each batch's specialization, and a batch may sit under
      // a course other than the selected one — so the names for the whole
      // department are learned here rather than from the selected course only.
      await this.learnSpecializationCodes(courses);
      if (courses.length) await this.pickCourse(courses[0].id);
    });
  }

  async pickCourse(id: string | null): Promise<void> {
    this.selectedCourseId.set(id);
    this.specializations.set([]);
    this.courseError.set(null);
    if (!id) return;
    await this.guarded(async () => {
      const specializations = await this.get<AcademicSpecializationOut[]>(
        `/admin/academic-courses/${id}/academic-specializations`,
      );
      this.rememberSpecializationCodes(specializations);
      this.specializations.set(specializations);
      this.fillCourseForm();
    });
  }

  /** Code by specialization id, for every specialization this screen has read.
   *
   *  `AdminCohortOut` names its specialization by ID only, and
   *  `specializations()` holds one course's worth. Resolving the grid's
   *  "Course · Spec." column out of that signal printed the course alone for
   *  every batch under a course that was not selected — the batch looked as
   *  though it had no specialization at all. */
  private readonly specializationCodes = new Map<string, string>();

  private rememberSpecializationCodes(rows: AcademicSpecializationOut[]): void {
    for (const s of rows) this.specializationCodes.set(s.id, s.code);
  }

  /** Learn the codes for every course in the department, so the batches grid
   *  can name a specialization under a course nobody has selected.
   *
   *  Its own try/catch on purpose: this is a NAMING read, not a structural
   *  one. If it fails the drill-down must still finish — the grid falls back
   *  to "…" for that one cell rather than the department refusing to open. */
  private async learnSpecializationCodes(courses: AcademicCourseOut[]): Promise<void> {
    try {
      const lists = await Promise.all(
        courses
          .filter((c) => c.specialization_count > 0)
          .map((c) =>
            this.get<AcademicSpecializationOut[]>(
              `/admin/academic-courses/${c.id}/academic-specializations`,
            ),
          ),
      );
      for (const list of lists) this.rememberSpecializationCodes(list);
    } catch {
      /* names only; the structure below is already on screen */
    }
  }

  /** The finder: does this row's code or name contain what was typed? */
  private rowMatchesFinder(code: string, name: string): boolean {
    const typed = this.finderText().trim().toLowerCase();
    if (!typed) return true;
    if (code.toLowerCase().includes(typed)) return true;
    return name.toLowerCase().includes(typed);
  }

  // =========================================================================
  // the course card — the four fields PATCH /academic-courses/{id} accepts
  // =========================================================================

  private fillCourseForm(): void {
    const course = this.selectedCourse();
    if (!course) return;
    this.courseForm.setValue({
      code: course.code,
      name: course.name,
      duration_months: course.duration_months === null ? '' : String(course.duration_months),
      status: course.status === 'ARCHIVED' ? 'ARCHIVED' : 'ACTIVE',
    });
    this.courseFormIsComplete.set(this.courseForm.valid);
  }

  async saveCourse(): Promise<void> {
    const course = this.selectedCourse();
    if (!course) return;
    this.courseError.set(null);
    const typed = this.courseForm.getRawValue();
    const saved = await this.patchCourse(course.id, {
      code: typed.code,
      name: typed.name,
      duration_months: typed.duration_months ? Number(typed.duration_months) : null,
      status: typed.status,
    });
    if (!saved) return;
    this.flash.set(`Saved course ${saved.code}`);
  }

  async archiveCourse(): Promise<void> {
    const course = this.selectedCourse();
    if (!course) return;
    this.courseError.set(null);
    const archived = await this.patchCourse(course.id, { status: 'ARCHIVED' });
    if (!archived) return;
    this.flash.set(`Archived course ${archived.code}`);
  }

  private async patchCourse(
    courseId: string,
    body: Record<string, unknown>,
  ): Promise<AcademicCourseOut | null> {
    const saved = await this.patch<AcademicCourseOut>(`/admin/academic-courses/${courseId}`, body);
    if (!saved) {
      this.courseError.set(this.error());
      this.error.set(null);
      return null;
    }
    this.courses.update((list) => list.map((c) => (c.id === saved.id ? saved : c)));
    this.fillCourseForm();
    return saved;
  }

  // =========================================================================
  // the batch form
  // =========================================================================

  /** Controls for the fixed fields plus ONE per served level, validators from
   *  `required`. Nothing here names a level. */
  private buildBatchForm(levels: HierarchyLevel[]): FormGroup {
    const controls: Record<string, FormControl> = {
      code: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
      // NOT REQUIRED, and that is the point: a batch IS a year. Leave the name
      // blank and `saveBatch` sends the label, which is what a standard batch
      // is called. It is there for the one batch that needs more than the span
      // to be told apart — a section, "2024-26 Section B".
      name: new FormControl('', { nonNullable: true }),
      // DERIVED, and still the one carrier. `syncSpan` writes the composed
      // span here and `saveBatch` reads it in both modes, so the free-text
      // fallback is this same control with a different editor on it rather
      // than a second field the save path has to know about.
      batch_label: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
      start_year: new FormControl<number | null>(null, [Validators.required]),
      end_year: new FormControl<number | null>(null, [Validators.required]),
      degree_level: new FormControl('PG', { nonNullable: true, validators: [Validators.required] }),
      entry_date: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
      expected_completion: new FormControl('', {
        nonNullable: true,
        validators: [Validators.required],
      }),
    };
    for (const lv of levels) {
      controls[lv.field] = new FormControl<string | null>(
        null,
        lv.required ? [Validators.required] : [],
      );
    }
    return new FormGroup(controls);
  }

  levelControl(lv: HierarchyLevel): FormControl {
    return this.batchForm.get(lv.field) as FormControl;
  }

  levelOptions(lv: HierarchyLevel): { id: string; code: string; name: string }[] {
    const which = LEVEL_OPTIONS[lv.key];
    if (which === 'courses') return this.courses();
    if (which === 'specializations') return this.formSpecializations();
    return [];
  }

  /** A level is disabled until its parent has a value — the cascade. */
  levelDisabled(lv: HierarchyLevel): boolean {
    if (lv.key === 'specialization') return !this.batchForm.get('course_id')?.value;
    return false;
  }

  /**
   * Put every conditionally-disabled control of the batch form into the state
   * its rule says it should be in.
   *
   * `[attr.disabled]` DOES NOT WORK ON A CONTROL THAT CARRIES
   * `formControlName`, and that is not a style preference — it is measured.
   * `setUpControl` calls `valueAccessor.setDisabledState(control.disabled)` at
   * directive setup (`callSetDisabledState: 'always'` is the default from
   * Angular 16), `SelectControlValueAccessor.setDisabledState` sets the
   * `disabled` PROPERTY, and setting that property to false removes the
   * content attribute the binding just wrote. A throwaway spec in this repo's
   * own harness, on this form's exact shape (a `@if` that opens on a later
   * change-detection cycle), rendered `disabled === false` and
   * `getAttribute('disabled') === null` with the expression TRUE.
   *
   * So three selects on this form looked locked and were not:
   *   - End year, until a start year is chosen (added with the year selects);
   *   - Specialization, until a course is chosen — the cascade this file's own
   *     class docstring calls "CASCADING SELECTS ENFORCE CONTIGUITY", which
   *     the server then refuses with a 422 from `_resolve_ancestry`;
   *   - Degree level on an EDIT, where `saveBatch` deliberately never puts
   *     `degree_level` on the wire — so the admin could change UG to PG, be
   *     told "Saved", and watch the reload put the old value back.
   * The reactive form is the one that actually holds, so the rule is applied
   * here and the bindings are gone from the template.
   *
   * `emitEvent: false` throughout: this is bookkeeping about what is
   * REACHABLE, and a status round-trip per control would fire the form watch
   * for every one of them on every open.
   */
  private syncBatchDisabled(): void {
    const setEnabled = (name: string, enabled: boolean): void => {
      const c = this.batchForm.get(name);
      if (!c) return;
      if (enabled && c.disabled) c.enable({ emitEvent: false });
      else if (!enabled && c.enabled) c.disable({ emitEvent: false });
    };
    // A year is not an end until something is a start.
    if (!this.labelIsFreeText()) setEnabled('end_year', this.spanStart() !== null);
    // The level cascade, from the same rule the template used to ask for.
    for (const lv of this.levels()) {
      if (this.levelDisabled(lv)) setEnabled(lv.field, false);
      else setEnabled(lv.field, true);
    }
    // Create-only, and now create-only in the model rather than in a binding
    // that does nothing. `code` is disabled by `openEditBatch` directly, as it
    // always was.
    setEnabled('degree_level', this.batchMode() !== 'edit');
  }

  async onLevelChange(lv: HierarchyLevel): Promise<void> {
    if (lv.key !== 'course') return;
    // Changing the course clears anything under it and reloads its options.
    const courseId = this.batchForm.get('course_id')?.value as string | null;
    this.batchForm.get('specialization_id')?.setValue(null);
    // Specialization becomes reachable or unreachable with the course above it.
    this.syncBatchDisabled();
    if (!courseId) {
      this.formSpecializations.set([]);
      return;
    }
    await this.guarded(async () => {
      const rows = await this.get<AcademicSpecializationOut[]>(
        `/admin/academic-courses/${courseId}/academic-specializations`,
      );
      this.rememberSpecializationCodes(rows);
      this.formSpecializations.set(rows);
    });
  }

  // ---- the year span ------------------------------------------------------

  /** The Start select moved.
   *
   *  The end year is cleared only when it is no longer a legal end for the new
   *  start. Clearing it unconditionally — the course → specialization
   *  cascade's rule one field up — would throw away a still-true end every
   *  time somebody corrected 2025 to 2026 on a two-year batch; leaving an
   *  illegal one would put a value in the select that is not among its
   *  options, which renders blank and is the trap `include` exists to avoid. */
  onStartYearChange(): void {
    this.spanStart.set(this.numberValue('start_year'));
    const end = this.numberValue('end_year');
    if (end !== null && !this.endYearChoices().includes(end)) {
      this.batchForm.get('end_year')?.setValue(null);
      this.spanEnd.set(null);
    }
    // End year has just become reachable, or stopped being.
    this.syncBatchDisabled();
    this.syncSpan();
  }

  onEndYearChange(): void {
    this.spanEnd.set(this.numberValue('end_year'));
    this.syncSpan();
  }

  /** Compose the label the row is stored under, and fill the dates the span
   *  implies. A half-chosen span writes an EMPTY label rather than a partial
   *  one: `batch_label` is required, so the save gate refuses until both years
   *  are picked, and nothing stale can be sent in the meantime. */
  private syncSpan(): void {
    const start = this.spanStart();
    const end = this.spanEnd();
    const label = this.batchForm.get('batch_label');
    if (start === null || end === null) {
      label?.setValue('');
      return;
    }
    label?.setValue(formatYearSpan(start, end));
    const dates = spanDates({ start, end });
    if (!this.entryDateEdited()) this.batchForm.get('entry_date')?.setValue(dates.entry);
    if (!this.completionDateEdited()) {
      this.batchForm.get('expected_completion')?.setValue(dates.completion);
    }
  }

  /** A human typed in one of the date boxes, so the span stops filling it.
   *
   *  `(input)` fires for a person and never for `setValue`, which is exactly
   *  the distinction needed and is why this is a listener rather than a
   *  comparison against the derived value: the two are equal for as long as
   *  the office agrees with the academic year, and a date typed back to 1 July
   *  is still a date somebody chose. College structure is deliberately the
   *  screen where a NON-STANDARD batch is added — a section, odd dates — so
   *  these two fields stay editable and what is typed in them survives a later
   *  change of span. */
  markDateEdited(which: 'entry' | 'completion'): void {
    if (which === 'entry') this.entryDateEdited.set(true);
    else this.completionDateEdited.set(true);
  }

  /** Which editor owns `batch_label`: the two year selects, or the text box.
   *
   *  DISABLING IS THE MECHANISM, NOT DECORATION. `saveBlocked` walks the
   *  controls and skips the disabled ones, so whichever editor is not on
   *  screen cannot hold the save gate on a `required` nobody can reach — and
   *  `getRawValue()` carries the label out either way. */
  private setLabelEditor(freeText: boolean): void {
    this.labelIsFreeText.set(freeText);
    const label = this.batchForm.get('batch_label');
    const start = this.batchForm.get('start_year');
    const end = this.batchForm.get('end_year');
    if (freeText) {
      label?.enable({ emitEvent: false });
      start?.disable({ emitEvent: false });
      end?.disable({ emitEvent: false });
    } else {
      label?.disable({ emitEvent: false });
      start?.enable({ emitEvent: false });
      // NOT enabled here — `syncBatchDisabled` owns End's own rule, and
      // enabling it unconditionally is what would put an End select in front
      // of somebody who has not picked a Start.
      end?.disable({ emitEvent: false });
    }
    this.syncBatchDisabled();
  }

  /** A select's value as a number. `[ngValue]` binds the numbers themselves,
   *  so this narrows rather than parses, and anything else is "not chosen". */
  private numberValue(name: string): number | null {
    const v = this.batchForm.get(name)?.value as unknown;
    return typeof v === 'number' && Number.isFinite(v) ? v : null;
  }

  openCreateBatch(): void {
    this.batchForm = this.buildBatchForm(this.levels());
    this.formSpecializations.set([]);
    this.grandfathered.set(new Set());
    // A new batch starts with neither year chosen and both dates blank. The
    // office picks the span deliberately; defaulting to this year would file
    // next year's intake under this one for anybody who did not look.
    this.spanStart.set(null);
    this.spanEnd.set(null);
    this.loadedSpan.set(null);
    this.entryDateEdited.set(false);
    this.completionDateEdited.set(false);
    this.setLabelEditor(false);
    this.watchBatchForm();
    this.editingBatchId.set(null);
    this.batchError.set(null);
    this.batchMode.set('create');
    // LAST, because `batchMode` is one of the rules it reads and it is only
    // true from the line above. The same reason it is repeated at the foot of
    // `openEditBatch`, where Degree level's create-only rule depends on it.
    this.syncBatchDisabled();
  }

  async openEditBatch(b: AdminCohortOut): Promise<void> {
    this.batchForm = this.buildBatchForm(this.levels());
    const span = parseYearSpan(b.batch_label);
    this.batchForm.patchValue({
      code: b.code,
      name: b.name,
      batch_label: b.batch_label,
      start_year: span?.start ?? null,
      end_year: span?.end ?? null,
      degree_level: b.degree_level,
      entry_date: b.entry_date,
      expected_completion: b.expected_completion,
      course_id: b.course_id,
      specialization_id: b.specialization_id,
    });
    this.spanStart.set(span?.start ?? null);
    this.spanEnd.set(span?.end ?? null);
    this.loadedSpan.set(span);
    // WHAT IS STORED IS WHAT IS SHOWN. `parseYearSpan` answering null is a real
    // answer about a label somebody typed on purpose — "Chain Batch", "2026-28
    // Section B" — and two year selects cannot represent one. The text box is
    // what stops Save rewriting that row into a span this form invented, which
    // would be `app/batch_labels.py`'s "never overwrite what the office wrote"
    // broken from the console instead of from SQL.
    this.setLabelEditor(span === null);
    // A date is the span's to refill only while it still IS the span's. An odd
    // date on the row was chosen by somebody — this screen is where a batch
    // with odd dates is added — so it must survive a later change of span, and
    // a row whose label is not a span has no derived date to compare against
    // at all.
    const derived = span ? spanDates(span) : null;
    this.entryDateEdited.set(!derived || derived.entry !== b.entry_date);
    this.completionDateEdited.set(!derived || derived.completion !== b.expected_completion);
    this.watchBatchForm();
    this.batchForm.get('code')?.disable(); // the code is the batch's handle; not edited here
    this.formSpecializations.set([]);
    if (b.course_id) {
      const courseId = b.course_id;
      await this.guarded(async () => {
        const rows = await this.get<AcademicSpecializationOut[]>(
          `/admin/academic-courses/${courseId}/academic-specializations`,
        );
        this.rememberSpecializationCodes(rows);
        this.formSpecializations.set(rows);
      });
    }
    // Required NOW, blank on the row AS LOADED. Anything the admin clears
    // during this edit is NOT grandfathered — that is a fresh gap, and it is
    // refused normally.
    this.grandfathered.set(
      new Set(
        this.levels()
          .filter((lv) => lv.required && !this.levelValueOf(b, lv))
          .map((lv) => lv.field),
      ),
    );
    this.editingBatchId.set(b.id);
    this.batchError.set(null);
    this.batchMode.set('edit');
    // LAST: `syncBatchDisabled` reads `batchMode` for Degree level's
    // create-only rule, and `levels()` for the specialization cascade, both of
    // which are only settled here.
    this.syncBatchDisabled();
  }

  /** The batch's value at one hierarchy level, without naming the level. */
  private levelValueOf(batch: AdminCohortOut, lv: HierarchyLevel): string | null {
    if (lv.field === 'course_id') return batch.course_id;
    if (lv.field === 'specialization_id') return batch.specialization_id;
    return null;
  }

  closeBatchForm(): void {
    this.batchMode.set('closed');
    this.editingBatchId.set(null);
    this.batchError.set(null);
  }

  /** Bumped whenever the batch form's validity is recalculated.
   *
   *  A FORM'S VALIDITY IS NOT A SIGNAL — the same trap `courseFormIsComplete`
   *  is mirrored out of, one form up this file. `saveBlocked` is a `computed`,
   *  and the only signals it read were `busy` and `grandfathered`, neither of
   *  which moves while somebody types: it answered with whatever the form
   *  looked like the moment it opened, which is EMPTY, therefore invalid,
   *  therefore Save disabled for as long as the form was open. This counter
   *  carries no meaning beyond "the form changed"; it exists so the gate is
   *  recomputed. */
  private readonly batchFormTick = signal(0);
  private batchFormWatch?: Subscription;

  /** Re-point that watch at the form `buildBatchForm` has just replaced. The
   *  old form is thrown away whole on every open, so its subscription goes
   *  with it rather than accumulating one per edit. */
  private watchBatchForm(): void {
    this.batchFormWatch?.unsubscribe();
    this.batchFormWatch = this.batchForm.statusChanges.subscribe(() =>
      this.batchFormTick.update((n) => n + 1),
    );
    this.batchFormTick.update((n) => n + 1);
  }

  /** A validator TELLS; this gate REFUSES. Grandfathered blanks are excluded
   *  from the gate so a legacy batch is never bricked by the flip. */
  readonly saveBlocked = computed(() => {
    this.batchFormTick();
    if (this.busy()) return true;
    const form = this.batchForm;
    const grand = this.grandfathered();
    for (const name of Object.keys(form.controls)) {
      const c = form.get(name);
      if (c && c.invalid && !c.disabled && !grand.has(name)) return true;
    }
    return false;
  });

  isGrandfathered(lv: HierarchyLevel): boolean {
    return this.grandfathered().has(lv.field);
  }

  async saveBatch(): Promise<void> {
    const department = this.selectedDepartmentId();
    if (!department) return;
    this.batchError.set(null);
    const raw = this.batchForm.getRawValue() as Record<string, unknown>;

    // Send the DEEPEST level only; the API derives its ancestors. Sending
    // both is also fine — they are checked, not trusted — but sending only
    // the leaf is the contract stated plainly.
    // A blank name means "just the year", which is what a batch is. The API
    // needs `name` NOT NULL, so the label stands in for it — never a
    // manufactured "Course - Specialization 2024-26", which is the duplication
    // the spine's links already carry (`app/batch_labels.py`).
    // `batch_label` is composed by `syncSpan` from the two year selects, or
    // typed for a row whose stored label is not a span; either way it is this
    // one control, so the wire is exactly what it was before the selects.
    const label = String(raw['batch_label'] ?? '').trim();
    const typedName = String(raw['name'] ?? '').trim();
    const body: Record<string, unknown> = {
      name: typedName || label,
      batch_label: label,
      entry_date: raw['entry_date'],
      expected_completion: raw['expected_completion'],
    };
    if (this.batchMode() === 'create') {
      body['code'] = raw['code'];
      body['degree_level'] = raw['degree_level'];
    }
    for (const lv of this.levels()) {
      const v = raw[lv.field] as string | null;
      // Omit-means-keep on edit; on create a null is simply "not chosen".
      if (this.batchMode() === 'create' ? v : v !== undefined) body[lv.field] = v || null;
    }

    const res =
      this.batchMode() === 'create'
        ? await this.post<AdminCohortOut>(`/admin/departments/${department}/cohorts`, body, true)
        : await this.patch<AdminCohortOut>(`/admin/cohorts/${this.editingBatchId()}`, body, true);
    if (!res) return;
    this.flash.set(this.batchMode() === 'create' ? `Created batch ${res.code}` : `Saved ${res.code}`);
    this.closeBatchForm();
    await Promise.all([this.reloadBatches(), this.reloadInboxes(), this.refreshCounts()]);
  }

  async fileUnassigned(b: AdminCohortOut): Promise<void> {
    const department = this.selectedDepartmentId();
    if (!department) return;
    const res = await this.patch<AdminCohortOut>(`/admin/cohorts/${b.id}`, {
      department_id: department,
    });
    if (!res) return;
    this.flash.set(`Filed ${res.code} under ${this.selectedDepartment()?.code ?? 'department'}`);
    await Promise.all([this.reloadBatches(), this.reloadInboxes(), this.refreshCounts()]);
  }

  // =========================================================================
  // seating
  // =========================================================================

  async openSeating(b: AdminCohortOut): Promise<void> {
    this.seatingBatch.set(b);
    this.seatPick.set(null);
    await this.reloadSeating();
  }

  closeSeating(): void {
    this.seatingBatch.set(null);
    this.seated.set([]);
    this.unseated.set([]);
    this.seatPick.set(null);
  }

  private async reloadSeating(): Promise<void> {
    const b = this.seatingBatch();
    if (!b) return;
    try {
      const [inBatch, pool] = await Promise.all([
        this.get<AdminStudentRowOut[]>(`/admin/cohorts/${b.id}/students`),
        this.get<AdminStudentRowOut[]>('/admin/students/unseated'),
      ]);
      this.seated.set(inBatch);
      this.unseated.set(pool);
    } catch {
      this.error.set('Could not load the students for this batch.');
    }
  }

  /** PUT /admin/students/{id}/cohort with the open batch. */
  async seat(): Promise<void> {
    const b = this.seatingBatch();
    const sid = this.seatPick();
    if (!b || !sid) return;
    const who = this.unseated().find((s) => s.student_id === sid);
    const ok = await this.put<unknown>(`/admin/students/${sid}/cohort`, { cohort_id: b.id });
    if (ok === null) return;
    this.seatPick.set(null);
    this.flash.set(`${who?.name ?? 'Student'} seated in ${b.code}.`);
    await Promise.all([this.reloadSeating(), this.reloadBatches()]);
  }

  /** The same PUT with null — an explicit un-seat, never "absent means keep". */
  async release(s: AdminStudentRowOut): Promise<void> {
    const b = this.seatingBatch();
    if (!b) return;
    const ok = await this.put<unknown>(`/admin/students/${s.student_id}/cohort`, { cohort_id: null });
    if (ok === null) return;
    this.flash.set(`${s.name} released from ${b.code}.`);
    await Promise.all([this.reloadSeating(), this.reloadBatches()]);
  }

  stageLabel(stage: string | null): string {
    return stage ? stage.replace('_', '-').toLowerCase().replace(/(^|-)\w/g, (c) => c.toUpperCase()) : '—';
  }

  private async reloadBatches(): Promise<void> {
    const department = this.selectedDepartmentId();
    if (!department) return;
    await this.guarded(async () => {
      this.batches.set(await this.get<AdminCohortOut[]>(`/admin/departments/${department}/cohorts`));
    });
  }

  private async reloadInboxes(): Promise<void> {
    await this.guarded(async () => {
      const [incomplete, unassigned] = await Promise.all([
        this.get<AdminCohortOut[]>('/admin/cohorts/incomplete'),
        this.get<AdminCohortOut[]>('/admin/cohorts/unassigned'),
      ]);
      this.incomplete.set(incomplete);
      this.unassigned.set(unassigned);
    });
  }

  /** Re-read the rows that CARRY THE COUNTS the rail prints, without moving
   *  the selection.
   *
   *  `department_count`, `cohort_count` and `specialization_count` are computed
   *  server-side per row, so every inline create leaves the parent's count line
   *  stale: add the first department to a college and the rail went on saying
   *  "0 departments". A wrong number on screen is the same failure as an
   *  invented one, and incrementing it locally would be inventing it. */
  private async refreshCounts(): Promise<void> {
    const college = this.selectedCollegeId();
    const department = this.selectedDepartmentId();
    await this.guarded(async () => {
      this.colleges.set(await this.get<CollegeOut[]>('/admin/colleges'));
      if (college) {
        this.departments.set(
          await this.get<DepartmentOut[]>(`/admin/colleges/${college}/departments`),
        );
      }
      if (department) {
        this.courses.set(
          await this.get<AcademicCourseOut[]>(`/admin/departments/${department}/academic-courses`),
        );
      }
    });
  }

  // =========================================================================
  // display helpers
  // =========================================================================

  /** "MBA · FIN", or "—" for a batch attached at department level.
   *
   *  Read from the learned map, never from `specializations()` — that signal
   *  holds the SELECTED course's rows, so a batch under a sibling course would
   *  print "MBA" and read as having no specialization. An id whose code is
   *  somehow still unknown prints "…", never nothing: the cell must not deny a
   *  level the row actually names. */
  levelsOf(b: AdminCohortOut): string {
    const course = this.courses().find((c) => c.id === b.course_id)?.code;
    const spec = b.specialization_id
      ? (this.specializationCodes.get(b.specialization_id) ?? '…')
      : undefined;
    const parts = [course, spec].filter((p): p is string => !!p);
    return parts.length ? parts.join(' · ') : '—';
  }

  /** The department row's count: "4 batches". */
  batchCountOf(d: DepartmentOut): string {
    return plural(d.cohort_count, 'batch', 'batches');
  }

  /** The course row's count: "24 months · 4 specializations".
   *
   *  The board writes this row as "PG · 2 yrs · 4 sems". Level and semesters
   *  are B4.1 and are not shown at all rather than guessed; `duration_months`
   *  IS a real column, so it is shown — in months, the unit stored, because
   *  dividing 18 by 12 to print "1 yr" would round a real answer into a wrong
   *  one. */
  specializationCountOf(c: AcademicCourseOut): string {
    const counted = plural(c.specialization_count, 'specialization');
    return c.duration_months === null
      ? counted
      : `${plural(c.duration_months, 'month')} · ${counted}`;
  }

  readonly requiredLabels = computed(() =>
    this.levels()
      .filter((lv) => lv.required)
      .map((lv) => lv.label),
  );


  // =========================================================================
  // http
  // =========================================================================

  private async get<T>(path: string): Promise<T> {
    const res = await fetch(`${environment.apiBase}${path}`, { credentials: 'include' });
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return (await res.json()) as T;
  }

  private post<T>(path: string, body: unknown, toBatchError = false): Promise<T | null> {
    return this.write<T>('POST', path, body, toBatchError);
  }

  private patch<T>(path: string, body: unknown, toBatchError = false): Promise<T | null> {
    return this.write<T>('PATCH', path, body, toBatchError);
  }

  private put<T>(path: string, body: unknown): Promise<T | null> {
    return this.write<T>('PUT', path, body, false);
  }

  /** Writes surface their 4xx detail as text — the API's messages are written
   *  for the person reading them, so they are shown verbatim. */
  private async write<T>(
    method: 'POST' | 'PATCH' | 'PUT',
    path: string,
    body: unknown,
    toBatchError: boolean,
  ): Promise<T | null> {
    this.busy.set(true);
    this.flash.set(null);
    if (!toBatchError) this.error.set(null);
    try {
      const res = await fetch(`${environment.apiBase}${path}`, {
        method,
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await this.detailOf(res);
        if (toBatchError) this.batchError.set(detail);
        else this.error.set(detail);
        // A 422 naming a required level means the switch moved under us; the
        // next form build reads the fresh answer.
        if (res.status === 422 && /required/i.test(detail)) {
          this.levels.set(await this.schema.load(true));
        }
        return null;
      }
      // 204 has no body (the seating PUT). Returning {} rather than null keeps
      // "null means the write was refused" true for every caller.
      if (res.status === 204) return {} as T;
      return (await res.json()) as T;
    } catch {
      const msg = 'Could not reach the server.';
      if (toBatchError) this.batchError.set(msg);
      else this.error.set(msg);
      return null;
    } finally {
      this.busy.set(false);
    }
  }

  private async detailOf(res: Response): Promise<string> {
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === 'string') return j.detail;
      if (Array.isArray(j.detail)) {
        // pydantic's shape: [{msg, loc}] — the msg is what the validator wrote.
        return j.detail
          .map((d: { msg?: string }) => d.msg ?? '')
          .filter(Boolean)
          .join(' ')
          .replace(/^Value error, /, '');
      }
    } catch {
      /* fall through */
    }
    return `Request failed (${res.status}).`;
  }
}
