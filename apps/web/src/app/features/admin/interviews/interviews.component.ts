/**
 * Interview records — every mock interview in scope, with the student named,
 * the record panel beside it and the recordings behind the same gate as ever.
 *
 * The approved board is `docs/redesign-2026-09/design/admin/InterviewRecords.html`
 * and the brief is `02-admin-console-spec.md` §17. Five decisions in this build
 * are worth reading before changing it.
 *
 * SCOPE IS THE SERVER'S. `GET /api/admin/interviews` applies BOTH fences in SQL
 * — rule 2 narrows a mentor to their own group and runs always, and the B1.2
 * reach narrows a grant somebody chose to hand over — and a caller who is both
 * gets the intersection. This screen renders whatever it is handed and never
 * widens it. The route is guarded on the same `admin.interviews` capability the
 * endpoint checks, so a grant that reports success in Governance opens the door
 * it names.
 *
 * THE FILTERS ARE THE SERVER'S TOO, AND THAT IS WHY THEY MOVED. Batch, track,
 * status, date and recorded-only are query parameters on `/api/admin/interviews`
 * and on its KPI and CSV siblings, so all three answer over the same rows: a
 * tile can never report a number the grid below it cannot produce, and the
 * extract is the list the operator was looking at. Filtering in the browser
 * could never have answered Batch at all — a record row carries no cohort.
 *
 * A SCORE IS IN THE PAYLOAD NOW, AND A NULL IS STILL A DASH. `overall_score` is
 * on every grid row (B6.7), so the Score column and the average tile are read
 * rather than reconstructed. NULLABLE, AND A NULL RENDERS AS A DASH:
 * `interview_evaluations.overall_score` is nullable even when the report
 * parsed, so a confident 0 in a 24px numeral would tell the office a student
 * failed something nobody scored. The same rule governs the average tile — an
 * average over no scored interview is `null`, never 0.
 *
 * PAGING IS A CURSOR, NOT A PAGE NUMBER. Interviews are written continuously,
 * so an offset taken against a list that grows at the top silently skips rows.
 * The server hands back `next_cursor`; "Load more" appends the next page, and
 * the grid paginates what has been loaded. There is deliberately no total
 * count anywhere — counting every interview to render "page 3 of 47" costs a
 * full scan on every keystroke — and the KPI tiles answer the question that
 * number stands in for, once and properly.
 *
 * "RECORDED" IS READ FROM `audio_recorded`, NEVER FROM A PATH. The flag is the
 * fact; a NULL `audio_path` collapses "capture disabled", "consent refused",
 * "the write failed" and "predates capture" into one silence (app/models/
 * interview.py). Nothing in this file reads a path, and the server does not
 * expose one.
 *
 * CONSENT IS SHOWN AS AN ENFORCED FACT, NOT A SWITCH. Nothing on this screen
 * can grant, edit or withdraw a scope. Since B6.1 what is consented to is the
 * COLLEGE'S decision and the student's row is an acknowledgement of it, so the
 * thing this office edits is the POLICY — and it edits it on the policy card
 * at the foot of the screen, never on a student's row. `DELETE
 * /api/interview/consent` does not exist and answers 405 for everyone.
 *
 * THE POLICY CARD MUST BE ABLE TO SAY "NOT CONFIGURED". No `interview_policies`
 * row is ever seeded, and the ABSENCE of one IS the default — a deployment that
 * never touches the card behaves exactly as it did before the table existed. So
 * `default: null` is rendered as "Not configured", with the numbers actually in
 * force in the boxes as the deployment's defaults. Drawing that state as a row
 * holding the defaults would report a decision nobody made.
 *
 * ONE COLUMN, TOP TO BOTTOM (2026-09-15). The side column — a record panel, a
 * progress card and a retention card with the policy behind a toggle — is
 * gone. The open record is a card under the list, with its report or
 * transcript on the left and the student's score-over-time chart on the
 * right; the policy is its own card, always open, with the college picker on
 * it. Every control the board had is here; what left is the prose — the
 * consent note, the chart note, the retention list and the two policy
 * paragraphs — the Transcript/Report buttons on the grid bar that duplicated
 * the record's own tabs, and the Batch filter for a session whose grant cannot
 * read the batch list, which was a grey control with the reason in a tooltip.
 *
 * DOWNLOAD, NOT DELETE. Per the owner's decision a recording can be downloaded
 * to the local machine and there is no delete here — recordings expire on the
 * deployment's retention clock, which is the only thing that removes them. One row
 * downloads the mixed track as an attachment; the selection posts its ids and
 * streams back one zip. Both go through `admin.interview_audio`, which the
 * Main Admin holds by baseline and a mentor only by an explicit grant.
 */

import {
  Component,
  ElementRef,
  OnDestroy,
  computed,
  effect,
  signal,
  viewChild,
} from '@angular/core';

import { AgGridAngular } from 'ag-grid-angular';
import type {
  ColDef,
  GridApi,
  GetRowIdParams,
  GridReadyEvent,
  ICellRendererParams,
  IRowNode,
  ModelUpdatedEvent,
  RowSelectionOptions,
  SelectionChangedEvent,
  SelectionColumnDef,
  ValueFormatterParams,
} from 'ag-grid-community';

import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';

import { environment } from '../../../../environments/environment';
import {
  REEP_CHART_THEME,
  STATUS_COLOURS,
  TRACK_COLOURS,
  registerReepChartTheme,
} from '../../../shared/charts/reep-echarts-theme';
import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { reepGridTheme } from '../../../shared/grid/reep-grid-theme';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';

// The design system's chart theme, registered once for this lazily-loaded
// chunk. Registration alone does nothing — ECharts applies a theme at init —
// so the one `echarts.init` below names it.
registerReepChartTheme(echarts);

echarts.use([LineChart, GridComponent, TooltipComponent, SVGRenderer]);

/** One interview as `GET /api/admin/interviews` returns it (`InterviewRecordRow`). */
interface InterviewRecord {
  session_id: string;
  student_id: string;
  student_name: string;
  usn: string | null;
  specialization: string | null;
  status: string;
  audio_recorded: boolean;
  /** Why `audio_recorded` is false — `app/interview_audio.py`'s SKIP_* words,
   *  written by the finalizer — or null on a recorded interview and on every
   *  row older than the column. See AUDIO_SKIP_REASONS. */
  audio_skipped_reason: string | null;
  started_at: string;
  ended_at: string | null;
  /** Nullable, and a null is a real "not scored" — never a zero. */
  overall_score: number | null;
  /** NULL means no evaluation row exists at all — still running, or older than
   *  the scorecard. Distinct from `'unavailable'`, which records that a report
   *  was attempted and did not arrive. */
  report_status: string | null;
}

/** One page of the grid. `next_cursor` is null on the last page, and there is
 *  no total count by design — see the file header. */
interface InterviewGridPage {
  rows: InterviewRecord[];
  next_cursor: string | null;
  page_size: number;
}

/** The tiles, computed over exactly the rows the grid would return. */
interface InterviewKpis {
  interviews: number;
  completed: number;
  abandoned: number;
  failed: number;
  running: number;
  /** Distinct students, not interviews: "sixty interviews" and "sixty students
   *  practising" are different facts and the office plans on the second. */
  students: number;
  recorded: number;
  scored: number;
  /** Null when nothing in this filter has been scored. Never 0.0. */
  average_overall: number | null;
}

/** One batch, from `GET /api/admin/cohorts` (`console.CohortOut`). */
interface CohortOption {
  id: string;
  code: string;
  name: string;
  batch_label: string;
  /** The spine and the year in one sentence, composed by the server from the
   *  batch's own links: "General MBA - Finance · 2026-28". `name` is the YEAR
   *  alone, so it cannot tell two batches of one department apart. */
  display_label: string;
  degree_level: string;
  student_count: number;
}

/** One college, from `GET /api/admin/colleges` (`admin.CollegeOut`), reduced to
 *  what the policy panel's picker needs. */
interface CollegeOption {
  id: string;
  code: string;
  name: string;
  status: string;
}

/** One stored `interview_policies` row. */
interface InterviewPolicy {
  id: string;
  college_id: string;
  course_id: string | null;
  course_name: string | null;
  store_transcript: boolean;
  store_audio: boolean;
  retention_days: number;
  daily_cap: number;
  attempt_cap: number;
  time_limit_seconds: number;
  updated_by: string | null;
  updated_by_name: string | null;
  updated_at: string | null;
}

/** What a student at this college gets when nothing above applies. `source` is
 *  'default' | 'college' | 'course'. */
interface EffectivePolicy {
  store_transcript: boolean;
  store_audio: boolean;
  retention_days: number;
  daily_cap: number;
  attempt_cap: number;
  time_limit_seconds: number;
  source: string;
}

/** Everything the policy panel needs for one college in one read. `default` is
 *  null for a college nobody has configured, which is a REAL answer. */
interface PolicySheet {
  college_id: string;
  college_name: string | null;
  default: InterviewPolicy | null;
  courses: InterviewPolicy[];
  effective_default: EffectivePolicy;
  /** The OPERATOR's switch, INTERVIEW_RECORDING_ENABLED. "Allow voice
   *  recording" below is one of three gates and this is another; the card
   *  says so when it is off, rather than let the office tick a box that can
   *  do nothing. Optional so a sheet from an older API reads as "unknown",
   *  which draws no warning. */
  recording_enabled_on_server?: boolean;
}

/** One interview as `GET /api/mentor/students/{id}/interviews` returns it —
 *  the same interview, read one student at a time, and the only staff read
 *  that carries the score. */
interface StudentInterviewSession {
  id: string;
  specialization: string | null;
  status: string;
  terminal_reason: string | null;
  final_phase: string | null;
  answers_accepted: number;
  /** AGENTS.md's runbook pair: every turn the engine saw, and the rows that
   *  landed. Both 0 on a row older than the counters; an older API omits them,
   *  which the facts line reads as absent rather than as zero. */
  turns_emitted?: number;
  turns_persisted?: number;
  audio_recorded: boolean;
  started_at: string;
  ended_at: string | null;
  report_status: string | null;
  overall_score: number | null;
}

/** One utterance. `content` may legitimately be empty — that is a turn the
 *  transcriber could not hear, which `transcription_status` names. */
interface InterviewTurn {
  seq: number;
  speaker: string;
  phase: string;
  content: string;
  transcription_status: string;
  counted_as_answer: boolean;
  created_at: string;
}

/** The scorecard. Every score is nullable even when the report parsed. */
interface InterviewReport {
  report_status: string;
  overall_score: number | null;
  communication_score: number | null;
  domain_score: number | null;
  structure_score: number | null;
  strengths: string[] | null;
  improvements: string[] | null;
  drill: string | null;
  summary: string | null;
  model: string | null;
  generated_at: string;
}

/** One row of the records grid, reduced to what the board draws. */
interface InterviewRecordRow {
  sessionId: string;
  studentId: string;
  studentName: string;
  usn: string;
  trackCode: string;
  trackLabel: string;
  trackColour: string;
  startedAt: string;
  durationSeconds: number | null;
  /** null until the student behind this row is opened — see the file header. */
  overallScore: number | null;
  audioRecorded: boolean;
  /** The sentence for a row with no recording: which switch was off, or, for a
   *  row the server has no word for, only that nothing was kept. Null when a
   *  recording exists. */
  audioReason: string | null;
  /** True when the reason is the college's own policy — the one case this
   *  screen can fix, on the policy card below. */
  audioFixableHere: boolean;
  statusLabel: string;
  statusTone: 'good' | 'warn' | 'risk' | 'neutral';
}

/** One point of the selected student's score trend. */
interface ScoreTrendPoint {
  when: string;
  score: number | null;
}

/** What the panel is showing under the record's summary. */
type RecordPanelTab = 'report' | 'transcript';

/** The interview tracks the matrix defines (app/interview_matrix.py), with the
 *  short code the grid shows and the name the panel reads out. */
const TRACK_LABELS: Record<string, { code: string; label: string }> = {
  hr: { code: 'HR', label: 'Human Resources' },
  dm: { code: 'DM', label: 'Digital Marketing' },
  ba: { code: 'BA', label: 'Business Analytics' },
  fa: { code: 'FA', label: 'Financial Analytics' },
};

/** An interview with no `?specialization=` is the generic one — a supported
 *  product state, not a missing value. */
const GENERIC_TRACK = { code: 'Generic', label: 'Generic interview' };
/** The neutral of the shared chart palette — the same grey-lilac every other
 *  screen draws "no category" in. Written as a literal here once, it would be
 *  one more copy of a token to keep in step (`check_theme_tokens.py`'s rule). */
const GENERIC_TRACK_COLOUR = STATUS_COLOURS.neutral;

/** `interview_sessions.status`, verbatim, with the office's words for it. */
const STATUS_LABELS: Record<string, { label: string; tone: 'good' | 'warn' | 'risk' | 'neutral' }> =
  {
    completed: { label: 'Completed', tone: 'good' },
    abandoned: { label: 'Abandoned', tone: 'warn' },
    failed: { label: 'Failed', tone: 'risk' },
    running: { label: 'Running', tone: 'neutral' },
  };

/** How many interviews one page of `GET /api/admin/interviews` carries. The
 *  server's own ceiling is `GRID_MAX_PAGE_SIZE`; asking for it means the grid
 *  usually holds the whole filtered list in one read, and "Load more" follows
 *  `next_cursor` when it does not. */
const RECORDS_PER_PAGE = 200;

/** Interviews per page in the grid, and what the page-size selector offers. */
const INTERVIEWS_PER_PAGE = 10;
const PAGE_SIZE_CHOICES = [10, 25, 50];

/** The PRODUCT default retention clock, in days — `Settings.interview_retention_days`
 *  in `app/config.py`. A deployment can change it and no endpoint reports the
 *  deployment-wide setting, so this number is only ever labelled "by default"
 *  until a college is chosen in the policy panel: `PolicySheet.effective_default`
 *  IS that college's clock, read from the server, and the card states it as a
 *  fact only then. A plausible number about how long a student's voice is kept
 *  is exactly the kind that must not be invented. */
const DEFAULT_RETENTION_DAYS = 180;

/** `PolicyIn`'s bounds, mirrored so the form refuses before the request rather
 *  than after it, and `ck_interview_policy_bounds`'s one cross-field rule:
 *  `attempt_cap >= daily_cap`, or the daily allowance is unreachable and the
 *  database refuses the row. */
const POLICY_BOUNDS = {
  retention_days: { min: 1, max: 3650 },
  daily_cap: { min: 1, max: 100 },
  attempt_cap: { min: 1, max: 500 },
  time_limit_seconds: { min: 60, max: 3600 },
} as const;

/** What `?track=` must be to ask for the interviews that ran with no track at
 *  all (`TRACK_GENERAL` in `app/routers/interview_records.py`). The filter's own
 *  option value is 'generic', which is this screen's word for the same thing;
 *  the two are mapped in one place rather than renamed on either side. */
const GENERIC_TRACK_PARAM = 'general';

/** The date filter's windows, in days. `null` is the whole loaded record. */
const DATE_WINDOWS: Record<string, number | null> = {
  all: null,
  '7': 7,
  '30': 30,
  '90': 90,
};

const MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000;

/** AG Grid cell renderers build their own DOM, so a name out of the roster
 *  reaches innerHTML: escape it here rather than trusting the roster. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function trackOf(specialization: string | null): { code: string; label: string; colour: string } {
  if (specialization === null) {
    return { ...GENERIC_TRACK, colour: GENERIC_TRACK_COLOUR };
  }
  const known = TRACK_LABELS[specialization];
  const colour = TRACK_COLOURS[specialization] ?? GENERIC_TRACK_COLOUR;
  if (!known) {
    return { code: specialization.toUpperCase(), label: specialization, colour };
  }
  return { code: known.code, label: known.label, colour };
}

function statusOf(status: string): { label: string; tone: 'good' | 'warn' | 'risk' | 'neutral' } {
  const known = STATUS_LABELS[status];
  if (!known) return { label: status, tone: 'neutral' };
  return known;
}

/** How long the interview lasted, or null while it is still running. */
function durationSecondsOf(startedAt: string, endedAt: string | null): number | null {
  if (endedAt === null) return null;
  const started = new Date(startedAt).getTime();
  const ended = new Date(endedAt).getTime();
  if (Number.isNaN(started) || Number.isNaN(ended)) return null;
  const seconds = Math.round((ended - started) / 1000);
  if (seconds < 0) return null;
  return seconds;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—';
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

/** "11 Sep 14:02" — local, human, and no date library in the bundle. */
function formatStartedAt(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '—';
  return when.toLocaleString(undefined, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

const FAINT_TEXT = 'color:var(--faint);font-size:11px';
const MONO_TEXT = 'font-variant-numeric:tabular-nums';

/** `app/interview_audio.py`'s SKIP_* vocabulary as sentences. Each names the
 *  gate that closed and, where this office can open it, where. A word this map
 *  does not know renders as the generic line rather than as nothing: a new
 *  reason on the server must not turn into a blank on the screen. */
const AUDIO_SKIP_REASONS: Record<string, string> = {
  operator_off:
    'Voice recording is switched off on this server (INTERVIEW_RECORDING_ENABLED), so no college can record until the operator turns it on.',
  policy_off:
    'This college’s interview policy did not allow voice recording when this interview ran. Tick “Allow voice recording” on the policy card below and the next interview is recorded.',
  no_consent:
    'The student had not acknowledged the current interview terms with recording on when this interview started. They are shown the terms again at their next Start.',
  store_full:
    'The recording store was out of disk space when this interview started, so capture was declined. The interview itself ran normally.',
  open_failed:
    'The recording store could not be opened for this interview; the API log has the cause. The interview itself ran normally.',
  nothing_captured:
    'Recording was allowed, but the session closed with nothing captured; the API log has the cause.',
};
const AUDIO_NOT_KEPT = 'No recording was kept for this interview.';

function audioReasonOf(record: InterviewRecord): string | null {
  if (record.audio_recorded) return null;
  const word = record.audio_skipped_reason;
  if (word === null) return AUDIO_NOT_KEPT;
  return AUDIO_SKIP_REASONS[word] ?? AUDIO_NOT_KEPT;
}

function renderUsnCell(params: ICellRendererParams<InterviewRecordRow>): string {
  const row = params.data;
  if (!row) return '';
  return `<span style="${MONO_TEXT}">${escapeHtml(row.usn)}</span>`;
}

function renderTrackCell(params: ICellRendererParams<InterviewRecordRow>): string {
  const row = params.data;
  if (!row) return '';
  // The dot carries the track's colour from the categorical palette; the code
  // beside it is what makes the track readable without it.
  const dot =
    `<span style="width:7px;height:7px;border-radius:999px;flex:none;` +
    `background:${row.trackColour}"></span>`;
  return `<span class="chip neutral" title="${escapeHtml(row.trackLabel)}">${dot}${escapeHtml(row.trackCode)}</span>`;
}

/** The score, or the dash that says nobody has scored this — never a zero. */
function renderScoreCell(params: ICellRendererParams<InterviewRecordRow>): string {
  const row = params.data;
  if (!row) return '';
  if (row.overallScore === null) return `<span style="${FAINT_TEXT}">—</span>`;
  return `<span style="${MONO_TEXT}">${row.overallScore}</span>`;
}

/** The audio scope as it was ENFORCED, which is the one consent fact this
 *  deployment can prove to staff today. */
function renderAudioCell(params: ICellRendererParams<InterviewRecordRow>): string {
  const row = params.data;
  if (!row) return '';
  if (row.audioRecorded) return '<span class="chip dot good">Audio stored</span>';
  // The reason is on the chip's title and spelled out on the open record; the
  // column itself stays one word so the grid reads at a glance.
  const title = escapeAttribute(row.audioReason ?? AUDIO_NOT_KEPT);
  return `<span class="chip dot neutral" title="${title}">No audio</span>`;
}

/** The reason is server-authored text from a fixed vocabulary, but it goes into
 *  an attribute of an HTML string, and the habit of escaping is worth more than
 *  the argument that this particular string is safe. */
function escapeAttribute(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderStatusCell(params: ICellRendererParams<InterviewRecordRow>): string {
  const row = params.data;
  if (!row) return '';
  return `<span class="chip dot ${row.statusTone}">${escapeHtml(row.statusLabel)}</span>`;
}

function formatStartedAtCell(
  params: ValueFormatterParams<InterviewRecordRow, string>,
): string {
  if (!params.value) return '—';
  return formatStartedAt(params.value);
}

function formatDurationCell(
  params: ValueFormatterParams<InterviewRecordRow, number | null>,
): string {
  if (params.value === null || params.value === undefined) return '—';
  return formatDuration(params.value);
}

@Component({
  selector: 'app-interview-records',
  standalone: true,
  imports: [AgGridAngular, PluralPipe],
  templateUrl: './interviews.component.html',
  styleUrl: './interviews.component.scss',
})
export class InterviewRecordsComponent implements OnDestroy {
  private readonly progressChartHost = viewChild<ElementRef<HTMLDivElement>>('progressChart');

  readonly gridTheme = reepGridTheme;
  readonly interviewsPerPage = INTERVIEWS_PER_PAGE;
  readonly pageSizes = PAGE_SIZE_CHOICES;
  readonly retentionDays = DEFAULT_RETENTION_DAYS;
  readonly policyBounds = POLICY_BOUNDS;

  readonly records = signal<InterviewRecord[] | null>(null);
  readonly loading = signal(true);
  readonly loadingMore = signal(false);
  readonly nextCursor = signal<string | null>(null);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);

  /** Every filter is the SERVER'S: each one re-reads the grid and its tiles, so
   *  the two can never disagree and the extract is the same list. */
  readonly cohortFilter = signal<string>('all');
  readonly trackFilter = signal<string>('all');
  readonly statusFilter = signal<string>('all');
  readonly recordingFilter = signal<string>('any');
  readonly dateFilter = signal<string>('all');
  readonly quickFilter = signal('');

  /** The tiles, read rather than recomputed from the loaded page — the page is
   *  one page and the tiles are about the whole filtered set. */
  readonly kpis = signal<InterviewKpis | null>(null);

  /** The batches the Batch filter offers. `GET /api/admin/cohorts` is gated on
   *  `admin.analytics`, NOT on this screen's `admin.interviews`, so a narrow
   *  grant can hold this screen and be refused the batch list. That is not an
   *  error to swallow: the filter is disabled and says which key it wants. */
  readonly cohorts = signal<CohortOption[] | null>(null);
  readonly cohortsBlocked = signal<string | null>(null);

  readonly selectedRows = signal<InterviewRecordRow[]>([]);
  readonly downloading = signal(false);
  readonly exporting = signal(false);

  /** Track codes seen in any page loaded this visit. The four shipped tracks
   *  are constants, but a college can add its own (B5.1), and a track filter
   *  that only offers the four would hide every interview held on a fifth. The
   *  set only grows: narrowing the filter to one track must not empty the list
   *  of tracks to widen it back to. */
  private readonly seenTrackCodes = signal<ReadonlySet<string>>(new Set<string>());

  /** Students whose sessions have been read, so a second click is free. They
   *  are read for the TREND CHART only — the grid's own scores come from the
   *  records payload. */
  private readonly sessionsByStudent = signal<Record<string, StudentInterviewSession[]>>({});

  // --- the policy panel (B6.1) ----------------------------------------------

  readonly colleges = signal<CollegeOption[] | null>(null);
  readonly collegesBlocked = signal<string | null>(null);
  readonly policyCollege = signal<string>('');
  readonly policySheet = signal<PolicySheet | null>(null);
  /** '' is the college's default row; anything else is that course's override. */
  readonly policyScope = signal<string>('');
  readonly policyLoading = signal(false);
  readonly policySaving = signal(false);
  readonly policyError = signal<string | null>(null);
  readonly policyFlash = signal<string | null>(null);

  readonly draftStoreTranscript = signal(true);
  readonly draftStoreAudio = signal(false);
  readonly draftRetentionDays = signal(DEFAULT_RETENTION_DAYS);
  readonly draftDailyCap = signal(8);
  readonly draftAttemptCap = signal(20);
  readonly draftTimeLimit = signal(900);

  readonly openRecord = signal<InterviewRecordRow | null>(null);
  readonly panelTab = signal<RecordPanelTab>('report');
  readonly transcript = signal<InterviewTurn[] | null>(null);
  readonly report = signal<InterviewReport | null>(null);
  readonly panelLoading = signal(false);
  readonly panelNote = signal<string | null>(null);

  /** The rows the GRID is showing — after the quick filter and the column
   *  filters, across every page. The four tiles count the records this screen
   *  holds; the status bar counts what is actually on the grid, because "Rows:
   *  118" over a quick-filtered grid showing three is a wrong number rather
   *  than a missing one. Null until the grid has built a model once. */
  private readonly displayedRows = signal<InterviewRecordRow[] | null>(null);

  private gridApi: GridApi<InterviewRecordRow> | null = null;
  private progressChart: echarts.ECharts | null = null;
  private progressResizeObserver: ResizeObserver | null = null;

  constructor() {
    // AG Grid 33+ refuses to draw until its modules are registered, and fails
    // as an empty rectangle rather than an exception (shared/grid docstring).
    registerReepGrid();
    void this.reload();
    void this.loadCohorts();
    void this.loadColleges();

    // The chart element exists only while a student is open, so the chart is
    // created when it appears and disposed when it goes.
    effect(() => {
      const host = this.progressChartHost();
      if (!host) {
        this.disposeProgressChart();
        return;
      }
      this.drawScoreTrend(host.nativeElement);
    });
  }

  ngOnDestroy(): void {
    this.disposeProgressChart();
  }

  // --- what the header says --------------------------------------------------

  /** The page(s) loaded. The server has already applied every filter, so this
   *  is not filtered again here — a second, browser-side copy of the same six
   *  rules is how a grid and its own extract come to disagree. */
  readonly recordsOnScreen = computed<InterviewRecord[]>(() => this.records() ?? []);

  readonly hasMorePages = computed(() => this.nextCursor() !== null);

  readonly anyFilterIsSet = computed(
    () =>
      this.cohortFilter() !== 'all' ||
      this.trackFilter() !== 'all' ||
      this.statusFilter() !== 'all' ||
      this.recordingFilter() !== 'any' ||
      this.dateFilter() !== 'all',
  );

  readonly noRecordsAtAll = computed(
    () => this.records()?.length === 0 && !this.anyFilterIsSet(),
  );

  readonly filtersHideEverything = computed(
    () => this.records()?.length === 0 && this.anyFilterIsSet(),
  );

  // --- the four tiles --------------------------------------------------------
  //
  // READ FROM `/interviews/summary`, NOT COUNTED FROM THE LOADED PAGE. The page
  // is one page; the tiles are about the whole filtered set, and a "Sessions"
  // number that meant "sessions downloaded so far" would fall as the operator
  // narrowed a filter and rise as they pressed Load more.

  readonly sessionCount = computed(() => this.kpis()?.interviews ?? 0);
  readonly studentCount = computed(() => this.kpis()?.students ?? 0);
  readonly completedCount = computed(() => this.kpis()?.completed ?? 0);
  readonly abandonedCount = computed(() => this.kpis()?.abandoned ?? 0);
  readonly failedCount = computed(() => this.kpis()?.failed ?? 0);
  readonly runningCount = computed(() => this.kpis()?.running ?? 0);
  readonly scoredCount = computed(() => this.kpis()?.scored ?? 0);
  readonly recordedCount = computed(() => this.kpis()?.recorded ?? 0);

  /** The average over the SCORED interviews only, and a dash when none are.
   *  Never 0: "nobody has been scored" and "everybody scored nothing" are
   *  opposite facts, and this is a judgement about people. */
  readonly averageScoreLabel = computed<string>(() => {
    const average = this.kpis()?.average_overall ?? null;
    if (average === null) return '—';
    return String(Math.round(average));
  });

  readonly averageScoreNote = computed<string>(() => {
    const summary = this.kpis();
    if (summary === null) return 'not read yet';
    if (summary.average_overall === null) {
      return summary.interviews === 0
        ? 'no interviews in this filter'
        : 'no interview in this filter has been scored';
    }
    return `over ${plural(summary.scored, 'scored interview')}`;
  });

  /** Completion over the interviews that have finished one way or another — a
   *  running interview has not failed to complete, it has not finished. */
  readonly completionPercent = computed<number | null>(() => {
    const summary = this.kpis();
    if (summary === null) return null;
    const finished = summary.interviews - summary.running;
    if (finished <= 0) return null;
    return Math.round((summary.completed / finished) * 100);
  });

  readonly completionLabel = computed<string>(() => {
    const percent = this.completionPercent();
    if (percent === null) return '—';
    return `${percent}%`;
  });

  /** What the status bar says, which is the grid's own count once it has one. */
  readonly rowsOnGrid = computed<number>(
    () => this.displayedRows()?.length ?? this.recordsOnScreen().length,
  );

  /** The status bar counts what is ON THE GRID, which is not the recorded tile:
   *  the tile is about the whole filtered set and this is about the rows in
   *  front of the reader, after the quick filter and one page of a cursor. */
  readonly recordedOnGrid = computed<number>(() => {
    const shown = this.displayedRows();
    if (shown === null) return this.rows().filter((row) => row.audioRecorded).length;
    return shown.filter((row) => row.audioRecorded).length;
  });

  // --- the grid --------------------------------------------------------------

  readonly rows = computed<InterviewRecordRow[]>(() =>
    this.recordsOnScreen().map((record) => {
      const track = trackOf(record.specialization);
      const status = statusOf(record.status);
      return {
        sessionId: record.session_id,
        studentId: record.student_id,
        studentName: record.student_name,
        usn: record.usn ?? '—',
        trackCode: track.code,
        trackLabel: track.label,
        trackColour: track.colour,
        startedAt: record.started_at,
        durationSeconds: durationSecondsOf(record.started_at, record.ended_at),
        overallScore: record.overall_score,
        audioRecorded: record.audio_recorded,
        audioReason: audioReasonOf(record),
        audioFixableHere: !record.audio_recorded && record.audio_skipped_reason === 'policy_off',
        statusLabel: status.label,
        statusTone: status.tone,
      };
    }),
  );

  readonly columns: ColDef<InterviewRecordRow>[] = [
    {
      field: 'usn',
      headerName: 'USN',
      pinned: 'left',
      minWidth: 150,
      cellRenderer: renderUsnCell,
      headerTooltip: 'University seat number, as the roster holds it',
    },
    { field: 'studentName', headerName: 'Student', minWidth: 170, flex: 1.2 },
    {
      field: 'trackCode',
      headerName: 'Track',
      minWidth: 120,
      cellRenderer: renderTrackCell,
      headerTooltip: 'The specialization the interview was run for',
    },
    {
      field: 'startedAt',
      headerName: 'Started',
      minWidth: 150,
      sort: 'desc',
      valueFormatter: formatStartedAtCell,
    },
    {
      field: 'durationSeconds',
      headerName: 'Duration',
      minWidth: 110,
      type: 'numericColumn',
      filter: 'agNumberColumnFilter',
      valueFormatter: formatDurationCell,
    },
    {
      field: 'overallScore',
      headerName: 'Score',
      minWidth: 100,
      type: 'numericColumn',
      filter: 'agNumberColumnFilter',
      cellRenderer: renderScoreCell,
      headerTooltip:
        'Out of 100, from the practice report. A dash is "not scored", which is not the same as a zero',
    },
    {
      field: 'audioRecorded',
      headerName: 'Audio',
      minWidth: 140,
      cellRenderer: renderAudioCell,
      headerTooltip:
        'Whether a recording was stored. Hover a "No audio" chip, or open the record, for which switch was off',
    },
    {
      field: 'statusLabel',
      headerName: 'Status',
      minWidth: 140,
      cellRenderer: renderStatusCell,
    },
  ];

  readonly defaultColumn: ColDef<InterviewRecordRow> = {
    sortable: true,
    resizable: true,
    filter: true,
    floatingFilter: true,
    suppressHeaderMenuButton: false,
  };

  readonly rowSelection: RowSelectionOptions<InterviewRecordRow> = {
    mode: 'multiRow',
    checkboxes: true,
    headerCheckbox: true,
    enableClickSelection: true,
  };

  readonly selectionColumn: SelectionColumnDef = { pinned: 'left', width: 46 };

  /** THE ROW IDENTITY, AND IT IS LOAD-BEARING. Opening a record fills in that
   *  student's scores, which rebuilds `rows()`; without an id AG Grid treats
   *  the new array as new rows, drops the selection and fires
   *  `selectionChanged` with nothing selected — which would close the panel
   *  the operator has just opened. With it the grid updates the rows it
   *  already has and the selection survives. */
  readonly getRowId = (params: GetRowIdParams<InterviewRecordRow>): string =>
    params.data.sessionId;

  onGridReady(event: GridReadyEvent<InterviewRecordRow>): void {
    this.gridApi = event.api;
  }

  /** Fired whenever the grid rebuilds its row model — new data, a sort, a
   *  column filter, a keystroke in the quick filter. */
  onModelUpdated(event: ModelUpdatedEvent<InterviewRecordRow>): void {
    const shown: InterviewRecordRow[] = [];
    event.api.forEachNodeAfterFilter((node: IRowNode<InterviewRecordRow>) => {
      if (node.data) shown.push(node.data);
    });
    this.displayedRows.set(shown);
  }

  /** The selection drives two things: the zip, and which record the panel
   *  shows. One row selected opens it; several mean "download these". */
  onSelectionChanged(event: SelectionChangedEvent<InterviewRecordRow>): void {
    const chosen = event.api.getSelectedRows();
    this.selectedRows.set(chosen);
    if (chosen.length === 1) {
      void this.showRecord(chosen[0]);
      return;
    }
    // Two or more ticked is the BULK state: the panel shows the selection's
    // summary, not whichever row happened to be opened first. Left open, the
    // side card would describe one interview while the toolbar acted on five.
    this.clearOpenRecord();
  }

  setQuickFilter(event: Event): void {
    const box = event.target as HTMLInputElement;
    this.quickFilter.set(box.value);
  }

  // --- the filters -----------------------------------------------------------

  /** The pill shows the chosen value; the native <select> laid over it is what
   *  the keyboard and the screen reader use (`.select` in reep-v2.scss). */
  readonly trackFilterLabel = computed<string>(() => {
    const chosen = this.trackFilter();
    if (chosen === 'all') return 'All tracks';
    if (chosen === 'generic') return GENERIC_TRACK.label;
    return TRACK_LABELS[chosen]?.label ?? chosen;
  });

  /** The four the matrix ships with, plus any track code an interview in this
   *  visit was actually held on. A college's own track (B5.1) would otherwise
   *  be un-filterable on the one screen that lists its interviews. */
  readonly trackOptions = computed<{ code: string; label: string }[]>(() => {
    const options = Object.entries(TRACK_LABELS).map(([code, names]) => ({
      code,
      label: names.label,
    }));
    const known = new Set(options.map((option) => option.code));
    for (const code of [...this.seenTrackCodes()].sort()) {
      if (!known.has(code)) options.push({ code, label: trackOf(code).label });
    }
    return options;
  });

  readonly cohortFilterLabel = computed<string>(() => {
    const chosen = this.cohortFilter();
    if (chosen === 'all') return 'All batches';
    const batch = (this.cohorts() ?? []).find((cohort) => cohort.id === chosen);
    return batch ? batch.display_label : 'One batch';
  });

  readonly statusFilterLabel = computed<string>(() => {
    const chosen = this.statusFilter();
    if (chosen === 'all') return 'All';
    return statusOf(chosen).label;
  });

  readonly recordingFilterLabel = computed<string>(() =>
    this.recordingFilter() === 'recorded' ? 'Recorded only' : 'Any',
  );

  readonly dateFilterLabel = computed<string>(() => {
    const chosen = this.dateFilter();
    if (chosen === 'all') return 'Whole record';
    return `Last ${chosen} days`;
  });

  // EVERY ONE OF THESE RE-READS. The filters are the server's, so a change to
  // any of them is a new list and a new set of tiles; narrowing them in the
  // browser instead would leave the tiles counting rows the grid is hiding.

  setCohortFilter(event: Event): void {
    this.cohortFilter.set((event.target as HTMLSelectElement).value);
    void this.reload();
  }

  setTrackFilter(event: Event): void {
    this.trackFilter.set((event.target as HTMLSelectElement).value);
    void this.reload();
  }

  setStatusFilter(event: Event): void {
    this.statusFilter.set((event.target as HTMLSelectElement).value);
    void this.reload();
  }

  setDateFilter(event: Event): void {
    this.dateFilter.set((event.target as HTMLSelectElement).value);
    void this.reload();
  }

  setRecordingFilter(event: Event): void {
    this.recordingFilter.set((event.target as HTMLSelectElement).value);
    void this.reload();
  }

  /** The six filters as the three endpoints take them.
   *
   *  ONE BUILDER FOR ALL THREE. The grid, its tiles and its extract must be the
   *  same list; two query strings written from the same description are how one
   *  of them quietly forgets a fence. `recorded_only` is the exception and it is
   *  the CALLER that leaves it out — the CSV is built from the score summaries,
   *  which outlive the audio, so a recording filter over them would change its
   *  answer every night at 02:00. */
  private filterQuery(): URLSearchParams {
    const query = new URLSearchParams();
    if (this.cohortFilter() !== 'all') query.set('cohort', this.cohortFilter());
    const track = this.trackFilter();
    if (track !== 'all') query.set('track', track === 'generic' ? GENERIC_TRACK_PARAM : track);
    if (this.statusFilter() !== 'all') query.set('status', this.statusFilter());
    const windowDays = DATE_WINDOWS[this.dateFilter()];
    if (windowDays !== null && windowDays !== undefined) {
      query.set('from', new Date(Date.now() - windowDays * MILLISECONDS_PER_DAY).toISOString());
    }
    return query;
  }

  // --- the record panel ------------------------------------------------------

  readonly selectedCount = computed(() => this.selectedRows().length);

  readonly selectedRecordedCount = computed(
    () => this.selectedRows().filter((row) => row.audioRecorded).length,
  );

  readonly canDownloadSelection = computed(
    () => this.selectedRecordedCount() > 0 && !this.downloading(),
  );

  readonly openRecordScoreLabel = computed<string>(() => {
    const record = this.openRecord();
    if (record === null || record.overallScore === null) return '—';
    return String(record.overallScore);
  });

  readonly openRecordDurationLabel = computed<string>(() => {
    const record = this.openRecord();
    if (record === null) return '—';
    return formatDuration(record.durationSeconds);
  });

  readonly openRecordStartedLabel = computed<string>(() => {
    const record = this.openRecord();
    if (record === null) return '—';
    return formatStartedAt(record.startedAt);
  });

  /** The open interview's own session row, once the student's list has been
   *  read for the trend chart — the same read, no second request. */
  readonly openSession = computed<StudentInterviewSession | null>(() => {
    const record = this.openRecord();
    if (record === null) return null;
    const sessions = this.sessionsByStudent()[record.studentId];
    return sessions?.find((session) => session.id === record.sessionId) ?? null;
  });

  /** How the interview ended and what the engine kept, as facts on one line
   *  (2026-09-17). A transcript with student turns and no interviewer turns
   *  is either an interviewer that never spoke or a write path that dropped
   *  its rows, and "N turns, M saved" is the one number that tells the office
   *  which — the AGENTS.md runbook signal, on the screen instead of in a
   *  database client. The close reason is the engine's own sentence. */
  readonly openSessionFacts = computed<string | null>(() => {
    const session = this.openSession();
    if (session === null) return null;
    const parts: string[] = [];
    parts.push(session.terminal_reason ? `Ended: ${session.terminal_reason}` : 'Still running');
    if (session.final_phase) parts.push(`stopped in ${session.final_phase.replace('_', ' ')}`);
    parts.push(`${session.answers_accepted} answer${session.answers_accepted === 1 ? '' : 's'} counted`);
    if (typeof session.turns_emitted === 'number' && typeof session.turns_persisted === 'number') {
      parts.push(`${session.turns_emitted} turns, ${session.turns_persisted} saved`);
    }
    return parts.join(' · ');
  });

  /** The selected student's interviews, oldest first — the trend the board
   *  draws beside the grid. */
  readonly scoreTrend = computed<ScoreTrendPoint[]>(() => {
    const record = this.openRecord();
    if (record === null) return [];
    const sessions = this.sessionsByStudent()[record.studentId];
    if (!sessions) return [];
    const ordered = [...sessions].sort((left, right) =>
      left.started_at.localeCompare(right.started_at),
    );
    return ordered.map((session) => ({
      when: new Date(session.started_at).toLocaleDateString(undefined, {
        day: '2-digit',
        month: 'short',
      }),
      score: session.overall_score,
    }));
  });

  readonly scoredSessionCount = computed(
    () => this.scoreTrend().filter((point) => point.score !== null).length,
  );

  readonly hasScoreTrend = computed(() => this.scoreTrend().length > 0);

  readonly transcriptTurns = computed<InterviewTurn[]>(() => this.transcript() ?? []);

  readonly reportScoreTiles = computed<{ label: string; value: number | null }[]>(() => {
    const report = this.report();
    if (report === null) return [];
    return [
      { label: 'Overall', value: report.overall_score },
      { label: 'Communication', value: report.communication_score },
      { label: 'Domain', value: report.domain_score },
      { label: 'Structure', value: report.structure_score },
    ];
  });

  readonly reportIsReadable = computed(() => this.report()?.report_status === 'ok');

  /** The two halves of the panel, as one named value each, so the template
   *  reads a signal rather than combining a tab and a payload inline. */
  readonly reportOnScreen = computed<InterviewReport | null>(() => {
    if (this.panelTab() !== 'report') return null;
    return this.report();
  });

  readonly transcriptOnScreen = computed<InterviewTurn[] | null>(() => {
    if (this.panelTab() !== 'transcript') return null;
    return this.transcript();
  });

  readonly reportStrengths = computed<string[]>(() => this.report()?.strengths ?? []);
  readonly reportImprovements = computed<string[]>(() => this.report()?.improvements ?? []);

  /** THE TAB OWNS THE NOTE, not the loader. A note is cleared when the operator
   *  asks for the other half — never inside a read, because `loadReport` runs
   *  immediately after `loadStudentSessions` and would erase the one sentence
   *  saying that read had failed; and never left standing across a tab change,
   *  because "Could not read the transcript." over a perfectly good report is a
   *  sentence about a screen the operator is no longer looking at. */
  showReport(): void {
    this.panelTab.set('report');
    this.panelNote.set(null);
    void this.loadReport();
  }

  showTranscript(): void {
    this.panelTab.set('transcript');
    this.panelNote.set(null);
    void this.loadTranscript();
  }

  /** Closing the panel UNTICKS the row as well. Left ticked, the same row
   *  cannot be clicked open again — the selection would not change, so the
   *  grid would raise no event and the panel would stay shut. */
  closeRecord(): void {
    this.clearOpenRecord();
    if (this.selectedRows().length > 0) {
      this.gridApi?.deselectAll();
    }
  }

  private clearOpenRecord(): void {
    this.openRecord.set(null);
    this.transcript.set(null);
    this.report.set(null);
    this.panelNote.set(null);
  }

  /** Open one interview: its student's sessions (which carry every score and
   *  the trend), then the report. */
  private async showRecord(row: InterviewRecordRow): Promise<void> {
    this.openRecord.set(row);
    this.transcript.set(null);
    this.report.set(null);
    this.panelNote.set(null);
    this.panelTab.set('report');
    await this.loadStudentSessions(row.studentId);
    // A SECOND CLICK DURING THAT READ WINS. Two rows opened in quick
    // succession leave two of these in flight; without this guard the slower
    // one finishes last and puts its own record back on screen, so the panel
    // describes an interview the operator closed a second ago.
    if (this.openRecord()?.sessionId !== row.sessionId) return;
    await this.loadReport();
  }

  // --- the reads -------------------------------------------------------------

  /** Which filters the answers on screen must belong to. Every reload takes
   *  the next number; a read that comes back under an older one describes
   *  filters no longer chosen and is dropped.
   *
   *  THE LAST ANSWER IS NOT THE LAST QUESTION. Status to "All" and then at
   *  once Track to "Digital Marketing" puts two reads in flight, and the
   *  slower "All" used to land second and fill the grid and the tiles with
   *  every interview under a Track filter reading "Digital Marketing". */
  private listGeneration = 0;

  /** The grid and its tiles, together, because they are one answer. */
  private async reload(): Promise<void> {
    this.clearOpenRecord();
    const generation = ++this.listGeneration;
    await Promise.all([this.loadRecords(null, generation), this.loadSummary(generation)]);
  }

  /** One page of `GET /api/admin/interviews`. `cursor` continues the list; its
   *  absence starts it again. A page that answers after the filters changed
   *  touches nothing, not even the spinners: the read that replaced it owns
   *  them. */
  private async loadRecords(
    cursor: string | null = null,
    generation = this.listGeneration,
  ): Promise<void> {
    if (cursor === null) {
      this.loading.set(true);
      this.error.set(null);
    } else {
      this.loadingMore.set(true);
    }
    const query = this.filterQuery();
    if (this.recordingFilter() === 'recorded') query.set('recorded_only', 'true');
    query.set('page_size', String(RECORDS_PER_PAGE));
    if (cursor !== null) query.set('cursor', cursor);
    try {
      const response = await fetch(`${environment.apiBase}/admin/interviews?${query}`, {
        credentials: 'include',
      });
      if (!response.ok) {
        const message = await detailOf(
          response,
          'Could not load interview records. Reload the page to try again.',
        );
        if (generation !== this.listGeneration) return;
        this.error.set(message);
      } else {
        const page = (await response.json()) as InterviewGridPage;
        if (generation !== this.listGeneration) return;
        this.records.update((loaded) =>
          cursor === null ? page.rows : [...(loaded ?? []), ...page.rows],
        );
        this.nextCursor.set(page.next_cursor);
        this.rememberTracks(page.rows);
      }
    } catch {
      if (generation !== this.listGeneration) return;
      this.error.set('Could not load interview records. Reload the page to try again.');
    }
    this.loading.set(false);
    this.loadingMore.set(false);
  }

  /** The next page, appended. The grid keeps its selection because every row
   *  carries its own id (`getRowId`). */
  async loadMore(): Promise<void> {
    const cursor = this.nextCursor();
    if (cursor === null || this.loadingMore() || this.loading()) return;
    await this.loadRecords(cursor);
  }

  private rememberTracks(rows: InterviewRecord[]): void {
    const codes = new Set(this.seenTrackCodes());
    let added = false;
    for (const row of rows) {
      if (row.specialization && !codes.has(row.specialization)) {
        codes.add(row.specialization);
        added = true;
      }
    }
    if (added) this.seenTrackCodes.set(codes);
  }

  /** The tiles. Same gate, same reach, same filters, same query builder — so a
   *  tile can never report a number the grid below it cannot produce. */
  private async loadSummary(generation = this.listGeneration): Promise<void> {
    const query = this.filterQuery();
    if (this.recordingFilter() === 'recorded') query.set('recorded_only', 'true');
    try {
      const response = await fetch(`${environment.apiBase}/admin/interviews/summary?${query}`, {
        credentials: 'include',
      });
      if (generation !== this.listGeneration) return;
      if (!response.ok) {
        // A DASH, NOT A STALE NUMBER. The tiles left showing the previous
        // filter's counts would be four confident numbers about a list nobody
        // is looking at.
        this.kpis.set(null);
        return;
      }
      const kpis = (await response.json()) as InterviewKpis;
      if (generation !== this.listGeneration) return;
      this.kpis.set(kpis);
    } catch {
      if (generation !== this.listGeneration) return;
      this.kpis.set(null);
    }
  }

  /** The batches the Batch filter offers. A 403 here is a real and reachable
   *  state — this list is gated on `admin.analytics` and this screen on
   *  `admin.interviews` — so it disables the filter with the reason rather than
   *  leaving an empty dropdown that looks like a deployment with no batches. */
  private async loadCohorts(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/cohorts`, {
        credentials: 'include',
      });
      if (response.status === 403) {
        this.cohortsBlocked.set('Filtering by batch needs the Analytics function.');
        this.cohorts.set([]);
        return;
      }
      if (!response.ok) {
        this.cohortsBlocked.set('The list of batches could not be read.');
        this.cohorts.set([]);
        return;
      }
      this.cohorts.set((await response.json()) as CohortOption[]);
    } catch {
      this.cohortsBlocked.set('The list of batches could not be read.');
      this.cohorts.set([]);
    }
  }

  /** EVERY interview of one student, which is what the trend chart is: their
   *  score across every interview they have taken, not across the page the grid
   *  happens to be holding. Read once per student and cached. The grid's own
   *  scores come from the records payload and do not wait for this. */
  private async loadStudentSessions(studentId: string): Promise<void> {
    if (this.sessionsByStudent()[studentId]) return;
    try {
      const response = await fetch(
        `${environment.apiBase}/mentor/students/${studentId}/interviews`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.panelNote.set('Could not read this student’s interviews.');
        return;
      }
      const sessions = (await response.json()) as StudentInterviewSession[];
      this.sessionsByStudent.update((known) => ({ ...known, [studentId]: sessions }));
    } catch {
      this.panelNote.set('Could not reach the server.');
    }
  }

  private async loadReport(): Promise<void> {
    const record = this.openRecord();
    if (record === null || this.report() !== null) return;
    this.panelLoading.set(true);
    try {
      const response = await fetch(
        `${environment.apiBase}/mentor/students/${record.studentId}` +
          `/interviews/${record.sessionId}/report`,
        { credentials: 'include' },
      );
      if (response.status === 404) {
        this.panelNote.set('No report was generated for this interview.');
      } else if (!response.ok) {
        this.panelNote.set(await detailOf(response, 'Could not read the report.'));
      } else {
        this.report.set((await response.json()) as InterviewReport);
      }
    } catch {
      this.panelNote.set('Could not reach the server.');
    }
    this.panelLoading.set(false);
  }

  private async loadTranscript(): Promise<void> {
    const record = this.openRecord();
    if (record === null || this.transcript() !== null) return;
    this.panelLoading.set(true);
    try {
      const response = await fetch(
        `${environment.apiBase}/mentor/students/${record.studentId}` +
          `/interviews/${record.sessionId}/transcript`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.panelNote.set(await detailOf(response, 'Could not read the transcript.'));
      } else {
        this.transcript.set((await response.json()) as InterviewTurn[]);
      }
    } catch {
      this.panelNote.set('Could not reach the server.');
    }
    this.panelLoading.set(false);
  }

  /** From an unrecorded record to the card that fixes it. A plain scroll: the
   *  card is on this screen, and the college picker on it is what the office
   *  has to touch. */
  goToPolicyCard(): void {
    document.getElementById('policy-college')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    document.getElementById('policy-college')?.focus();
  }

  // --- the downloads ---------------------------------------------------------

  /** One recording, as an attachment saved to the local machine. A plain GET
   *  the browser downloads; `download=1` flips the server's inline default to
   *  attachment. */
  downloadOpenRecording(): void {
    const record = this.openRecord();
    if (record === null || !record.audioRecorded) return;
    const url =
      `${environment.apiBase}/mentor/students/${record.studentId}` +
      `/interviews/${record.sessionId}/audio?track=mixed&download=1`;
    // A hidden anchor rather than window.open: keeps the current tab, and the
    // attachment disposition means the browser saves rather than navigates.
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }

  /** The selected recordings, bundled into one zip by the server and saved.
   *  Only the recorded rows are posted — a failed interview has nothing to
   *  download, and sending its id would ask the server to skip it silently. */
  async downloadSelected(): Promise<void> {
    const ids = this.selectedRows()
      .filter((row) => row.audioRecorded)
      .map((row) => row.sessionId);
    if (ids.length === 0 || this.downloading()) return;
    this.downloading.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/mentor/interviews/audio.zip`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_ids: ids, track: 'mixed' }),
      });
      if (!response.ok) {
        this.error.set(await detailOf(response, 'The download was refused.'));
      } else {
        await this.saveZip(response, ids.length);
      }
    } catch {
      this.error.set('The download could not be completed. Nothing was saved.');
    }
    this.downloading.set(false);
  }

  /** Stream the zip to a blob, then hand it to the browser as a save.
   *
   *  THE COUNT DESCRIBES THE REQUEST, NEVER THE FILE. The server skips an id
   *  whose recording has expired, whose row is soft-deleted or whose student is
   *  out of this caller's scope, and it reports no tally — a 200 carrying three
   *  files is indistinguishable here from one carrying five. So the sentence
   *  says what was asked for and warns what may be missing, rather than
   *  counting files nobody counted. */
  private async saveZip(response: Response, count: number): Promise<void> {
    await this.saveFile(response, 'reep-interview-recordings.zip');
    this.flash.set(`Saved a zip for ${plural(count, 'recording')}. An expired or out-of-scope one is not in it.`);
  }

  /** Stream a response to a blob and hand it to the browser as a save. */
  private async saveFile(response: Response, filename: string): Promise<void> {
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    // Revoked on the next tick: revoking synchronously can beat the click in
    // some browsers and save an empty file (features/student/english does the
    // same, and a zip of recordings is the largest blob this app hands over).
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  // --- the extract -----------------------------------------------------------

  readonly canExport = computed(() => !this.exporting() && !this.loading());

  /** `GET /api/admin/interviews/export.csv` — SUMMARY ROWS ONLY.
   *
   *  Fetched rather than linked, so a refusal is a sentence on this screen
   *  instead of a page of JSON in a new tab. It carries the same filters the
   *  grid is showing MINUS the recording one, and the flash says so: the file is
   *  built from `interview_score_summaries`, which outlive the transcripts and
   *  the audio, so "only the recorded ones" has no meaning over it and a filter
   *  that silently meant "only the interviews not yet reaped" would change its
   *  answer every night at 02:00. The same table is why the file can contain
   *  interviews this grid no longer shows.
   *
   *  The receipt is the server's: `record_export` commits and a failure to
   *  write it fails the download. Nothing here needs to know that beyond not
   *  pretending the file arrived when it did not. */
  async exportCsv(): Promise<void> {
    if (!this.canExport()) return;
    this.exporting.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/interviews/export.csv?${this.filterQuery()}`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.error.set(await detailOf(response, 'The extract was refused.'));
      } else {
        await this.saveFile(response, 'reep-interviews.csv');
        this.flash.set(
          'Saved. Summary rows only, and on the export history.' +
            (this.recordingFilter() === 'recorded' ? ' The recording filter is not applied to it.' : ''),
        );
      }
    } catch {
      this.error.set('The extract could not be downloaded. Nothing was saved.');
    }
    this.exporting.set(false);
  }

  // --- the interview policy (B6.1) -------------------------------------------

  /** The stored row the panel is editing, or null for a college nobody has
   *  configured. NULL IS A REAL ANSWER and the panel renders it as "not
   *  configured", never as a row holding the defaults: only one of those two is
   *  a decision somebody made. */
  readonly policyRow = computed<InterviewPolicy | null>(() => {
    const sheet = this.policySheet();
    if (sheet === null) return null;
    const scope = this.policyScope();
    if (scope === '') return sheet.default;
    return sheet.courses.find((row) => row.course_id === scope) ?? null;
  });

  readonly policyIsConfigured = computed(() => this.policyRow() !== null);
  /** True only when the server SAID recording is off; an older sheet without
   *  the field draws nothing. */
  readonly recordingOffOnServer = computed(
    () => this.policySheet()?.recording_enabled_on_server === false,
  );

  readonly policyEffective = computed<EffectivePolicy | null>(
    () => this.policySheet()?.effective_default ?? null,
  );

  /** `ck_interview_policy_bounds`: an attempt ceiling under the daily one makes
   *  the daily allowance unreachable, so the database refuses the row. Refused
   *  here too, on the field, rather than as a 500 from a CHECK. */
  readonly policyCapsAreOrdered = computed(
    () => this.draftAttemptCap() >= this.draftDailyCap(),
  );

  readonly canSavePolicy = computed<boolean>(() => {
    if (this.policyCollege() === '' || this.policySaving() || this.policyLoading()) return false;
    return this.policyCapsAreOrdered();
  });

  /** The colleges the picker offers. Gated on `admin.institution`, not on this
   *  screen's key, so a 403 is reachable and is said in words. */
  private async loadColleges(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/colleges`, {
        credentials: 'include',
      });
      if (response.status === 403) {
        this.collegesBlocked.set('Editing a policy needs the Institution function as well.');
        this.colleges.set([]);
        return;
      }
      if (!response.ok) {
        this.collegesBlocked.set('The list of colleges could not be read.');
        this.colleges.set([]);
        return;
      }
      const colleges = (await response.json()) as CollegeOption[];
      this.colleges.set(colleges);
      if (colleges.length === 1 && this.policyCollege() === '') {
        // One college on the deployment is one answer to "which college", so
        // the card opens on it rather than on "Choose…" with the one option
        // underneath. The office reached this card to tick a box and found a
        // picker first; with more than one college the picker is a real
        // question and stays.
        this.policyCollege.set(colleges[0].id);
        await this.loadPolicySheet();
      }
    } catch {
      this.collegesBlocked.set('The list of colleges could not be read.');
      this.colleges.set([]);
    }
  }

  async setPolicyCollege(event: Event): Promise<void> {
    this.policyCollege.set((event.target as HTMLSelectElement).value);
    this.policyScope.set('');
    this.policyFlash.set(null);
    await this.loadPolicySheet();
  }

  setPolicyScope(event: Event): void {
    this.policyScope.set((event.target as HTMLSelectElement).value);
    this.policyFlash.set(null);
    this.syncPolicyDraft();
  }

  private async loadPolicySheet(): Promise<void> {
    const college = this.policyCollege();
    if (college === '') {
      this.policySheet.set(null);
      return;
    }
    this.policyLoading.set(true);
    this.policyError.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/interview-policies/${encodeURIComponent(college)}`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.policySheet.set(null);
        this.policyError.set(await detailOf(response, 'This college’s policy could not be read.'));
      } else {
        this.policySheet.set((await response.json()) as PolicySheet);
        this.syncPolicyDraft();
      }
    } catch {
      this.policySheet.set(null);
      this.policyError.set('This college’s policy could not be read.');
    }
    this.policyLoading.set(false);
  }

  /** Fill the form from the stored row, or — when there is none — from the
   *  numbers actually in force, which the panel labels as exactly that. */
  private syncPolicyDraft(): void {
    const stored = this.policyRow();
    const effective = this.policyEffective();
    const from = stored ?? effective;
    if (!from) return;
    this.draftStoreTranscript.set(from.store_transcript);
    this.draftStoreAudio.set(from.store_audio);
    this.draftRetentionDays.set(from.retention_days);
    this.draftDailyCap.set(from.daily_cap);
    this.draftAttemptCap.set(from.attempt_cap);
    this.draftTimeLimit.set(from.time_limit_seconds);
  }

  /** Write the college's default row, or one course's override.
   *
   *  EVERY FIELD IS SENT, and that is safe here precisely because the form
   *  holds every field: `PolicyIn` treats an omitted field as "keep the number
   *  already governing these students", so a partial form would be the
   *  dangerous one. */
  async savePolicy(): Promise<void> {
    if (!this.canSavePolicy()) return;
    const college = encodeURIComponent(this.policyCollege());
    const scope = this.policyScope();
    const url =
      scope === ''
        ? `${environment.apiBase}/admin/interview-policies/${college}`
        : `${environment.apiBase}/admin/interview-policies/${college}/${encodeURIComponent(scope)}`;
    this.policySaving.set(true);
    this.policyError.set(null);
    this.policyFlash.set(null);
    try {
      const response = await fetch(url, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          store_transcript: this.draftStoreTranscript(),
          store_audio: this.draftStoreAudio(),
          retention_days: this.draftRetentionDays(),
          daily_cap: this.draftDailyCap(),
          attempt_cap: this.draftAttemptCap(),
          time_limit_seconds: this.draftTimeLimit(),
        }),
      });
      if (!response.ok) {
        this.policyError.set(await detailOf(response, 'The policy was not saved.'));
      } else {
        this.policyFlash.set('Saved. Applies to interviews started from now on.');
        await this.loadPolicySheet();
      }
    } catch {
      this.policyError.set('The policy was not saved.');
    }
    this.policySaving.set(false);
  }

  numberValue(event: Event): number {
    return Number((event.target as HTMLInputElement).value);
  }

  checkboxValue(event: Event): boolean {
    return (event.target as HTMLInputElement).checked;
  }

  // --- the trend chart -------------------------------------------------------

  /** One line: the selected student's overall score per interview, oldest
   *  first. A session with no score leaves a GAP rather than a zero — the same
   *  rule the Score column follows, drawn instead of written. */
  private drawScoreTrend(host: HTMLDivElement): void {
    const points = this.scoreTrend();
    if (points.length === 0) return;

    if (!this.progressChart) {
      this.progressChart = echarts.init(host, REEP_CHART_THEME, { renderer: 'svg' });
      // ECharts cannot size itself inside a flex parent that changes without
      // the window doing so — the sidebar collapsing is exactly that.
      this.progressResizeObserver = new ResizeObserver(() => this.progressChart?.resize());
      this.progressResizeObserver.observe(host);
    }

    this.progressChart.setOption({
      grid: { left: 34, right: 14, top: 16, bottom: 24 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: points.map((point) => point.when) },
      yAxis: { type: 'value', min: 0, max: 100, name: '' },
      series: [
        {
          name: 'Overall score',
          type: 'line',
          data: points.map((point) => point.score),
          connectNulls: false,
          symbolSize: 7,
          lineStyle: { width: 2 },
        },
      ],
    });
  }

  private disposeProgressChart(): void {
    this.progressResizeObserver?.disconnect();
    this.progressResizeObserver = null;
    this.progressChart?.dispose();
    this.progressChart = null;
  }
}

/** FastAPI answers a 422 with `detail` as a LIST, so a raw read of it renders
 *  "[object Object]" at the one moment the operator needs a sentence. */
async function detailOf(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item: { msg?: string }) => item?.msg)
        .filter((message): message is string => typeof message === 'string');
      if (messages.length > 0) return messages.join(' · ');
    }
  } catch {
    // Not JSON, or an empty body: the status line is all there is to say.
  }
  return `${fallback} (${response.status})`;
}
