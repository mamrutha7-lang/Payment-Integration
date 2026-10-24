/**
 * Placement analytics — the Main Admin console's landing board.
 *
 * The approved design is `docs/redesign-2026-09/design/admin/Main.html` and its
 * brief is `02-admin-console-spec.md` §2. Four decisions in this build are
 * worth reading before changing it.
 *
 * ONE COMPOSITE CHART, AND IT IS THE COHORT'S NOW. Until Phase 4b there was no
 * programme-wide week anywhere in the product, so this chart drew ONE student's
 * six weeks and said so. `GET /api/admin/analytics/series` is that week: four
 * lines — attendance %, skilling hours, offers approved and placement-ready % —
 * over the reach the same capability narrows the tiles by. The per-student
 * six-week series is still reachable from the picker beside the chart, because
 * it is the drill-down a reader wants after the cohort line moves and because
 * it is what the Download CV button hangs off.
 *
 * EVERY NUMBER ON THIS SCREEN NOW COMES FROM THE SERVER, WITH ITS OWN REASON
 * WHEN IT IS ABSENT. The KPI strip is `GET /api/admin/analytics/kpis`, which
 * answers each tile as a value OR a null with a sentence saying why — readiness
 * has no history, the mock-interview definition belongs to the interview
 * records screen, no offer carried a CTC in this window. A null renders as an
 * em dash and that sentence, never as a confident zero: the attendance tile
 * used to be a mean this component computed over `/admin/mentor-load`, which
 * silently excluded every student without a mentor, and the tile said
 * "programme" anyway.
 *
 * A SERIES CARRIES ITS OWN SOURCE. `live`, `partial` and `unavailable` are three
 * different facts and the chart says which under it. Placement-ready % is
 * `partial` on every deployment today: it can be computed for THIS week and for
 * no earlier one, because nothing records what a student's marks were in week 7
 * — that is what the nightly analytics snapshot would fix, and no schedule for
 * it is deployed. The screen says that in words rather than drawing a line that
 * starts on the right-hand edge with no explanation.
 *
 * THREE FILTERS ARE DISABLED AND ONE IS LIVE. Period is `?weeks=` on both
 * analytics endpoints. Batch, Course and Track are not: neither endpoint takes
 * a cohort, a course or a track, and the aggregates are computed server-side so
 * there is nothing here to narrow client-side either. They carry a plain
 * `disabled` and the real reason — not a phase number, which would be a promise
 * nothing keeps.
 *
 * ECharts is imported through the narrow barrel (core, the two chart types and
 * the four components used) and AG Grid is registered from the shared
 * bootstrap; the route is lazy, so both libraries live in this chunk alone.
 */

import { DatePipe } from '@angular/common';
import { Component, ElementRef, OnDestroy, computed, effect, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AgGridAngular } from 'ag-grid-angular';
import type { ColDef, ICellRendererParams, ValueFormatterParams } from 'ag-grid-community';

import * as echarts from 'echarts/core';
import { BarChart, LineChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import {
  CATEGORICAL_PALETTE,
  REEP_CHART_THEME,
  STATUS_COLOURS,
  registerReepChartTheme,
} from '../../../shared/charts/reep-echarts-theme';
import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { reepGridTheme } from '../../../shared/grid/reep-grid-theme';
import { PluralPipe } from '../../../shared/text/plural.pipe';
import { AlertRulesDialogComponent } from './alert-rules-dialog.component';

// The design system's chart theme, registered once for this lazily-loaded
// chunk. Registration alone does nothing — ECharts applies a theme at init —
// so `echarts.init` below names it.
registerReepChartTheme(echarts);

echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  SVGRenderer,
]);

/** One assigned student, as the mentorship map returns them. */
interface Mentee {
  student_id: string;
  name: string;
  usn: string | null;
  stage: string | null;
  /** Null when nothing is recorded — which is not the same as 0 %. */
  attendance_percent: number | null;
  verified_skills: number;
  logged_hours: number;
}

/** One faculty account with the students it holds. */
interface MentorLoad {
  /** Null until the Main Admin assigns this faculty member their first student. */
  mentor_id: string | null;
  user_id: string;
  name: string;
  department: string | null;
  designation: string | null;
  capacity: number;
  mentee_count: number;
  mentees: Mentee[];
}

/** `GET /api/admin/analytics-summary` — the header line's counts. */
interface AnalyticsSummary {
  students_total: number;
  pending_registrations: number;
  mentors_total: number;
  mentees_per_mentor: number | null;
  badges_awarded: number;
  evidence_awaiting_verification: number;
  placed_students: number;
  placement_percent: number;
  approved_offers: number;
  semester: number | null;
  generated_at: string;
}

/** One tile of `GET /api/admin/analytics/kpis`. */
interface AnalyticsKpi {
  key: string;
  label: string;
  /** `percent`, `inr` or `count` — the tile reads its suffix from this rather
   *  than from the key, so a new KPI needs no client change to render. */
  unit: string;
  /** NULL means NOT MEASURED, and `note` says why. Never rendered as 0. */
  value: number | null;
  previous: number | null;
  delta: number | null;
  note: string | null;
}

interface AnalyticsKpis {
  weeks: number;
  period_start: string;
  previous_period_start: string;
  kpis: AnalyticsKpi[];
  generated_at: string;
}

/** One weekly line of `GET /api/admin/analytics/series`. */
interface AnalyticsSeries {
  key: string;
  label: string;
  unit: string;
  /** One point per week, oldest first. NULL is "not measured that week". */
  points: (number | null)[];
  /** `live`, `partial` or `unavailable` — never collapsed into one another. */
  source: string;
  note: string | null;
}

interface AnalyticsSeriesSheet {
  weeks: { label: string; start: string; end: string }[];
  series: AnalyticsSeries[];
  students_in_reach: number;
  generated_at: string;
}

/** `GET /api/admin/students/{id}/weekly` — the per-student drill-down. */
interface StudentWeekly {
  student_id: string;
  name: string;
  usn: string | null;
  weekly_hour_target: number;
  has_resume: boolean;
  weeks: { label: string; start: string; end: string }[];
  /** Per week; null means no classes that week, never 0 % attendance. */
  attendance_percent: (number | null)[];
  logged_hours: number[];
  skills_by_category: { category: string; count: number }[];
}

/** `GET /api/mentor/alerts` — the feed the board draws under "Alerts". */
interface ProgrammeAlert {
  id: string;
  student_id: string;
  student_name: string;
  rule_triggered: string;
  severity: string;
  message: string;
  triggered_at: string;
  resolved: boolean;
}

/** One row of the Mentor load grid, already reduced to what the board shows. */
interface MentorLoadRow {
  mentorName: string;
  initials: string;
  department: string | null;
  menteeCount: number;
  capacity: number;
  /** Averages across this mentor's students; null when nobody has a figure. */
  averageAttendancePercent: number | null;
  averageVerifiedSkills: number | null;
  averageLoggedHours: number | null;
  loadStatus: string;
  loadTone: 'good' | 'warn' | 'neutral';
}

/** A tile of the KPI strip, reduced to what the template draws. */
interface KpiTile {
  key: string;
  label: string;
  /** The figure, or an em dash when the server reported it unmeasured. */
  figure: string;
  /** The small suffix drawn in `.unit` — '%', ' L', '' — never part of `figure`. */
  unitSuffix: string;
  measured: boolean;
  note: string | null;
  /** Text AND colour together; null when the server compared nothing. */
  delta: { label: string; tone: 'good' | 'risk' | 'neutral'; icon: string } | null;
  /** A cross-link this reader's session actually holds, or null. */
  link: string | null;
  linkText: string | null;
}

/** One line of the composite chart, whichever subject it is drawn for. */
interface ChartLine {
  name: string;
  /** Appended in the tooltip: '%', ' h' or ''. The axes carry their own. */
  unit: string;
  kind: 'line' | 'bar';
  /** Which of the axes below this line is measured against. */
  axisUnit: string;
  points: (number | null)[];
  target: { value: number; label: string; colour: string } | null;
}

interface ChartModel {
  labels: string[];
  lines: ChartLine[];
  /** The chart's accessible name and the sentence under the card heading. */
  caption: string;
  /** Units in the order their axes are drawn, left first. */
  axisUnits: string[];
}

/** The chart's subject when nobody has picked a student: the whole reach. */
const EVERY_STUDENT = '';

/** What the Period select offers, and the `?weeks=` each sends. The server caps
 *  at 52 (`MAX_SERIES_WEEKS`), so 52 is the widest honest choice here. */
const PERIOD_CHOICES: readonly { weeks: number; label: string }[] = [
  { weeks: 6, label: 'Last 6 weeks' },
  { weeks: 12, label: 'Last 12 weeks' },
  { weeks: 26, label: 'Last 26 weeks' },
  { weeks: 52, label: 'Last 52 weeks' },
];

/** The board's default window. */
const DEFAULT_PERIOD_WEEKS = 6;

/** Where a KPI tile leads, and the capability whose absence hides that link.
 *  `capabilityGuard` answers a capability it does not hold with a silent
 *  redirect, so an unfiltered link is a button that throws the reader off the
 *  screen. Only the tiles whose subject IS another screen carry one. */
const KPI_LINKS: Readonly<
  Record<string, { path: string; capability: string; text: string }>
> = {
  placement_rate: { path: '/admin/placement', capability: 'admin.placement', text: 'Open placement' },
  median_ctc: { path: '/admin/placement', capability: 'admin.placement', text: 'Open placement' },
  highest_ctc: { path: '/admin/placement', capability: 'admin.placement', text: 'Open placement' },
  pending_approvals: {
    path: '/admin/registrations',
    capability: 'admin.registrations',
    text: 'Open registrations',
  },
};

/** The chart's unit vocabulary, shared by the cohort series and the drill-down
 *  so one week of hours is drawn against the same axis in both. */
const PERCENT = 'percent';
const HOURS = 'hours';
const COUNT = 'count';

/** The tooltip suffix for each unit. A count has none. */
const UNIT_SUFFIX: Readonly<Record<string, string>> = {
  [PERCENT]: '%',
  [HOURS]: ' h',
  [COUNT]: '',
};

/** The axis label formatter for each unit. */
const AXIS_FORMAT: Readonly<Record<string, string>> = {
  [PERCENT]: '{value}%',
  [HOURS]: '{value} h',
  [COUNT]: '{value}',
};

/** The four cohort lines, by the key the server gives them: how each is drawn.
 *  Colours come from the validated categorical palette. */
const SERIES_STYLE: Readonly<Record<string, { kind: 'line' | 'bar'; colour: string }>> = {
  attendance_pct: { kind: 'line', colour: CATEGORICAL_PALETTE[0] },
  readiness_pct: { kind: 'line', colour: CATEGORICAL_PALETTE[1] },
  skilling_hours: { kind: 'bar', colour: CATEGORICAL_PALETTE[3] },
  offers: { kind: 'bar', colour: CATEGORICAL_PALETTE[2] },
};

/** The two drill-down lines keep the colours they had. */
const ATTENDANCE_COLOUR = CATEGORICAL_PALETTE[0];
const HOURS_COLOUR = CATEGORICAL_PALETTE[3];

/** Mentors per page in the load grid — eight rows fit the board's card. */
const MENTORS_PER_PAGE = 8;

/** What the grid's page-size selector offers. */
const PAGE_SIZE_CHOICES = [8, 16, 32];

/** Initials for the grid's avatar: first and last word of the name. */
function initialsOf(fullName: string): string {
  const words = fullName.trim().split(/\s+/).filter((word) => word.length > 0);
  if (words.length === 0) return '?';
  const first = words[0].charAt(0);
  if (words.length === 1) return first.toUpperCase();
  const last = words[words.length - 1].charAt(0);
  return `${first}${last}`.toUpperCase();
}

/** The mean of the figures that exist, or null when none do. A student with no
 *  attendance recorded is left out rather than counted as a zero. */
function averageOf(values: (number | null)[]): number | null {
  const recorded = values.filter((value): value is number => value !== null);
  if (recorded.length === 0) return null;
  let total = 0;
  for (const value of recorded) {
    total += value;
  }
  return Math.round((total / recorded.length) * 10) / 10;
}

/** The board's three load states, as text and tone together. */
function loadStatusOf(menteeCount: number, capacity: number): { label: string; tone: 'good' | 'warn' | 'neutral' } {
  if (menteeCount === 0) return { label: 'No students yet', tone: 'neutral' };
  if (menteeCount >= capacity) return { label: 'At capacity', tone: 'warn' };
  return { label: 'On track', tone: 'good' };
}

/** AG Grid cell renderers build their own DOM, so a name out of the database
 *  reaches innerHTML: escape it here rather than trusting the roster. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderMentorNameCell(params: ICellRendererParams<MentorLoadRow>): string {
  const row = params.data;
  if (!row) return '';
  return `<span class="avatar">${escapeHtml(row.initials)}</span><span>${escapeHtml(row.mentorName)}</span>`;
}

function renderLoadStatusCell(params: ICellRendererParams<MentorLoadRow>): string {
  const row = params.data;
  if (!row) return '';
  // Label and tone are this file's own words, never roster text.
  return `<span class="chip dot ${row.loadTone}">${row.loadStatus}</span>`;
}

function formatDepartment(params: ValueFormatterParams<MentorLoadRow, string | null>): string {
  return params.value ?? 'Not on record';
}

function formatMenteeLoad(params: ValueFormatterParams<MentorLoadRow, number>): string {
  const row = params.data;
  if (!row) return '';
  return `${row.menteeCount} / ${row.capacity}`;
}

function formatPercent(params: ValueFormatterParams<MentorLoadRow, number | null>): string {
  if (params.value === null || params.value === undefined) return '—';
  return `${params.value}%`;
}

function formatCount(params: ValueFormatterParams<MentorLoadRow, number | null>): string {
  if (params.value === null || params.value === undefined) return '—';
  return `${params.value}`;
}

function formatHours(params: ValueFormatterParams<MentorLoadRow, number | null>): string {
  if (params.value === null || params.value === undefined) return '—';
  return `${params.value} h`;
}

function roundToOne(value: number): number {
  return Math.round(value * 10) / 10;
}

/** A rupee figure, split so the template can draw the magnitude large and the
 *  scale small. Indian placement figures are quoted in lakh; printing 1 200 000
 *  in a 27px numeral is a number nobody in the office reads at a glance. */
function formatRupees(value: number): { figure: string; suffix: string } {
  const magnitude = Math.abs(value);
  if (magnitude >= 100000) return { figure: `₹${roundToOne(value / 100000).toFixed(1)}`, suffix: ' L' };
  if (magnitude >= 1000) return { figure: `₹${Math.round(value / 1000)}`, suffix: 'k' };
  return { figure: `₹${Math.round(value)}`, suffix: '' };
}

/** A KPI's value in the unit the server declared it in. */
function formatKpiValue(unit: string, value: number): { figure: string; suffix: string } {
  if (unit === PERCENT) return { figure: String(roundToOne(value)), suffix: '%' };
  if (unit === 'inr') return formatRupees(value);
  return { figure: String(Math.round(value)), suffix: '' };
}

/** A delta, signed and in words. "No change" is spelled out rather than drawn
 *  as a 0 with an arrow, because an arrow is a claim about direction. */
function describeDelta(unit: string, delta: number): { label: string; tone: 'good' | 'risk' | 'neutral'; icon: string } {
  // `remove` is the horizontal dash, and it is used rather than `trending_flat`
  // because the icon font is SUBSET to the glyphs the templates already name —
  // a glyph outside `tools/fonts/icon-names*.txt` renders as nothing at all.
  if (delta === 0) return { label: 'No change', tone: 'neutral', icon: 'remove' };
  const sign = delta > 0 ? '+' : '−';
  const magnitude = Math.abs(delta);
  let reading: string;
  if (unit === PERCENT) {
    reading = `${sign}${roundToOne(magnitude)} pts`;
  } else if (unit === 'inr') {
    const money = formatRupees(magnitude);
    reading = `${sign}${money.figure}${money.suffix}`;
  } else {
    reading = `${sign}${Math.round(magnitude)}`;
  }
  return {
    label: reading,
    tone: delta > 0 ? 'good' : 'risk',
    icon: delta > 0 ? 'trending_up' : 'trending_down',
  };
}

/** One row of the crosshair tooltip, as ECharts hands it over. */
interface WeeklyTooltipPoint {
  axisValue: string;
  seriesName: string;
  marker: string;
  data: number | null;
}

/** Built per chart, because the units belong to the lines currently drawn. */
function weeklyTooltipFormatter(unitBySeries: Record<string, string>) {
  return (points: WeeklyTooltipPoint[]): string => {
    if (points.length === 0) return '';
    const lines = [`<b>Week of ${points[0].axisValue}</b>`];
    for (const point of points) {
      const unit = unitBySeries[point.seriesName] ?? '';
      const reading = point.data === null ? 'not measured' : `${point.data}${unit}`;
      lines.push(`${point.marker}${point.seriesName}: <b>${reading}</b>`);
    }
    return lines.join('<br/>');
  };
}

@Component({
  selector: 'app-admin-analytics',
  standalone: true,
  imports: [DatePipe, RouterLink, AgGridAngular, PluralPipe, AlertRulesDialogComponent],
  templateUrl: './analytics.component.html',
  styleUrl: './analytics.component.scss',
})
export class AdminAnalyticsComponent implements OnDestroy {
  private readonly healthChartHost = viewChild<ElementRef<HTMLDivElement>>('healthChart');

  readonly apiBase = environment.apiBase;
  readonly gridTheme = reepGridTheme;
  readonly mentorsPerPage = MENTORS_PER_PAGE;
  readonly pageSizes = PAGE_SIZE_CHOICES;
  readonly periods = PERIOD_CHOICES;
  readonly everyStudent = EVERY_STUDENT;

  readonly mentorLoad = signal<MentorLoad[] | null>(null);
  readonly summary = signal<AnalyticsSummary | null>(null);
  readonly alerts = signal<ProgrammeAlert[] | null>(null);
  readonly error = signal<string | null>(null);

  /** A READ THAT FAILED IS NOT A READ THAT RETURNED NOTHING.
   *
   *  Both of these used to be `set([])` in the catch and in the !ok branch,
   *  which is how "we could not reach the alert feed" reached the screen as the
   *  sentence "Nothing open." — a zero nobody counted, on the console's landing
   *  board, which is the invented figure this phase exists to prevent. The data
   *  signal now stays null on a failure and these say why, so the empty state
   *  is only ever drawn over an answer the server actually gave. */
  readonly mentorLoadFailed = signal(false);
  readonly alertsFailed = signal(false);

  /** The chart's own failure, kept apart from the screen-level `error()`.
   *  Sharing one signal meant a 404 on ONE student's six weeks raised a banner
   *  that nothing ever cleared: picking a student whose weeks load fine left
   *  "Could not load that student's six weeks." standing over their chart. */
  readonly weeklyError = signal<string | null>(null);

  /** The placement criteria's attendance floor — the one target line drawn. */
  readonly attendanceTarget = signal<number | null>(null);

  /** The window the Period select names, in weeks. Sent to BOTH analytics
   *  endpoints, so the strip's deltas and the chart's weeks are the same span. */
  readonly periodWeeks = signal(DEFAULT_PERIOD_WEEKS);

  /** The KPI strip and the cohort series, each with its own failure sentence.
   *  Null means "not answered", which the strip draws as dashed tiles with the
   *  reason rather than as an empty row. */
  readonly kpiSheet = signal<AnalyticsKpis | null>(null);
  readonly kpiError = signal<string | null>(null);
  readonly programmeSeries = signal<AnalyticsSeriesSheet | null>(null);
  readonly seriesError = signal<string | null>(null);
  /** TRUE FROM THE FIRST FRAME. The effect that fetches the window runs after
   *  the first change detection, so a signal starting at `false` leaves one
   *  render in which nothing has been asked for and nothing has arrived — and
   *  the chart's empty branch would flash "there is nothing to plot", which is
   *  a claim about the data rather than about the request. */
  readonly analyticsBusy = signal(true);

  /** Whose weeks the composite chart draws: the whole reach, or one student. */
  readonly chartSubject = signal<string>(EVERY_STUDENT);
  readonly studentWeekly = signal<StudentWeekly | null>(null);
  readonly weeklyBusy = signal(false);
  private readonly weeklyCache = new Map<string, StudentWeekly>();

  /** The grid's quick filter, as typed in the toolbar. */
  readonly quickFilter = signal('');

  /** The alert-rules editor, opened from the Alerts card. */
  readonly rulesOpen = signal(false);

  private readonly auth = inject(AuthService);

  /** THIS SCREEN IS NOT ONLY THE MAIN ADMIN'S. `admin.analytics` is grantable
   *  (console.py's own docstring: "the difference is a MENTOR an admin has
   *  granted admin.analytics to"), and every cross-link on this board leads to
   *  a route guarded by a DIFFERENT capability — `admin.placement`,
   *  `admin.registrations`, `admin.mentors`, `admin.exports`, `admin.imports`.
   *  `capabilityGuard` answers a capability it does not hold with a silent
   *  redirect to the reader's own home, so for a granted mentor those controls
   *  are a button that throws you off the screen. The shell's sidebar already
   *  filters its rows this way; the same filter belongs on the links. It is a
   *  CONVENIENCE, exactly as app-shell says — the route guard and the API are
   *  what refuse. */
  holds(capability: string): boolean {
    return this.auth.session()?.capabilities?.includes(capability) ?? false;
  }

  private healthChart: echarts.ECharts | null = null;
  private chartResizeObserver: ResizeObserver | null = null;

  /** Every faculty account, including one with no students: on a screen called
   *  "Mentor load" an empty mentor is the row that matters most. */
  readonly mentorRows = computed<MentorLoadRow[]>(() =>
    (this.mentorLoad() ?? []).map((mentor) => {
      const status = loadStatusOf(mentor.mentee_count, mentor.capacity);
      return {
        mentorName: mentor.name,
        initials: initialsOf(mentor.name),
        department: mentor.department,
        menteeCount: mentor.mentee_count,
        capacity: mentor.capacity,
        averageAttendancePercent: averageOf(mentor.mentees.map((student) => student.attendance_percent)),
        averageVerifiedSkills: averageOf(mentor.mentees.map((student) => student.verified_skills)),
        averageLoggedHours: averageOf(mentor.mentees.map((student) => student.logged_hours)),
        loadStatus: status.label,
        loadTone: status.tone,
      };
    }),
  );

  readonly mentorColumns: ColDef<MentorLoadRow>[] = [
    {
      field: 'mentorName',
      headerName: 'Mentor',
      pinned: 'left',
      minWidth: 210,
      flex: 1.4,
      cellStyle: { display: 'flex', alignItems: 'center', gap: '8px' },
      cellRenderer: renderMentorNameCell,
    },
    { field: 'department', headerName: 'Department', minWidth: 170, flex: 1.2, valueFormatter: formatDepartment },
    {
      field: 'menteeCount',
      headerName: 'Mentees',
      type: 'numericColumn',
      minWidth: 120,
      valueFormatter: formatMenteeLoad,
      headerTooltip: 'Assigned students against the programme mentor capacity',
    },
    {
      field: 'averageAttendancePercent',
      headerName: 'Attendance',
      type: 'numericColumn',
      minWidth: 130,
      valueFormatter: formatPercent,
      headerTooltip: 'Mean attendance of this mentor’s students that have any recorded',
    },
    {
      field: 'averageVerifiedSkills',
      headerName: 'Skills',
      type: 'numericColumn',
      minWidth: 110,
      valueFormatter: formatCount,
      headerTooltip: 'Verified skill badges per assigned student, on average',
    },
    {
      field: 'averageLoggedHours',
      headerName: 'Hours logged',
      type: 'numericColumn',
      minWidth: 140,
      valueFormatter: formatHours,
      // The board's column reads "Hrs / wk". The mentorship map returns hours
      // for ALL TIME, not per week, and labelling an all-time figure as weekly
      // is the kind of quiet wrong number this screen exists to avoid.
      headerTooltip: 'Ledger hours per assigned student, on average, over the whole record',
    },
    { field: 'loadStatus', headerName: 'Status', minWidth: 150, cellRenderer: renderLoadStatusCell },
  ];

  readonly defaultMentorColumn: ColDef<MentorLoadRow> = {
    sortable: true,
    resizable: true,
    filter: true,
    suppressHeaderMenuButton: false,
  };

  /** Every assigned student, by name — what the chart's picker lists under
   *  "Every student in reach". */
  readonly studentsWithAMentor = computed<Mentee[]>(() => {
    const students = (this.mentorLoad() ?? []).flatMap((mentor) => mentor.mentees);
    return [...students].sort((one, other) => one.name.localeCompare(other.name));
  });

  readonly selectedStudent = computed<Mentee | null>(
    () => this.studentsWithAMentor().find((student) => student.student_id === this.chartSubject()) ?? null,
  );

  /** The name on the chart's picker pill. */
  readonly chartSubjectName = computed<string>(() => {
    const student = this.selectedStudent();
    if (!student) return 'Every student in reach';
    return student.name;
  });

  readonly periodLabel = computed<string>(
    () => PERIOD_CHOICES.find((choice) => choice.weeks === this.periodWeeks())?.label ?? '',
  );

  /** What the header says while the figures are in flight, and when the call
   *  failed. Main's own two sentences. */
  readonly summaryPlaceholderNote = computed<string>(() => {
    if (this.error()) return 'Cohort figures unavailable.';
    return 'Loading the cohort figures…';
  });

  // --- the KPI strip ---------------------------------------------------------

  /** The server's tiles, in the server's order, each reduced to what is drawn.
   *  A tile whose value is null keeps its place and shows the dash and the
   *  reason — `_KPI_SHAPE` on the server keeps the strip's shape for exactly
   *  this, so a reader whose access reaches nothing sees the strip they would
   *  otherwise see rather than a row that failed to load. */
  readonly kpiTiles = computed<KpiTile[]>(() => {
    const sheet = this.kpiSheet();
    if (!sheet) return [];
    return sheet.kpis.map((kpi) => {
      const link = KPI_LINKS[kpi.key];
      const shown = link && this.holds(link.capability) ? link : null;
      if (kpi.value === null) {
        return {
          key: kpi.key,
          label: kpi.label,
          figure: '—',
          unitSuffix: '',
          measured: false,
          note: kpi.note,
          delta: null,
          link: shown?.path ?? null,
          linkText: shown?.text ?? null,
        };
      }
      const reading = formatKpiValue(kpi.unit, kpi.value);
      return {
        key: kpi.key,
        label: kpi.label,
        figure: reading.figure,
        unitSuffix: reading.suffix,
        measured: true,
        note: kpi.note,
        delta: kpi.delta === null ? null : describeDelta(kpi.unit, kpi.delta),
        link: shown?.path ?? null,
        linkText: shown?.text ?? null,
      };
    });
  });

  /** Only drawn while the strip has nothing in it: the strip's own state, not
   *  the screen's. */
  readonly kpiPlaceholderNote = computed<string>(() => {
    if (this.kpiError()) return this.kpiError() as string;
    return 'Counting the window…';
  });

  // --- the composite chart ---------------------------------------------------

  /** One shape for both subjects, so `drawWeeklyHealth` has a single input.
   *  The cohort sheet and the drill-down agree on units (`percent`, `hours`,
   *  `count`), which is what lets the axes be derived rather than hard-coded. */
  readonly chartModel = computed<ChartModel | null>(() => {
    const student = this.selectedStudent();
    if (student) {
      const weeks = this.studentWeekly();
      if (!weeks) return null;
      const floor = this.attendanceTarget();
      return {
        labels: weeks.weeks.map((week) => week.label),
        axisUnits: [PERCENT, HOURS],
        caption: `${weeks.name} · the last six weeks on record`,
        lines: [
          {
            name: 'Attendance',
            unit: UNIT_SUFFIX[PERCENT],
            kind: 'line',
            axisUnit: PERCENT,
            points: weeks.attendance_percent,
            target:
              floor === null
                ? null
                : { value: floor, label: `${floor}% floor`, colour: STATUS_COLOURS.risk },
          },
          {
            name: 'Hours logged / wk',
            unit: UNIT_SUFFIX[HOURS],
            kind: 'bar',
            axisUnit: HOURS,
            points: weeks.logged_hours,
            target: {
              value: weeks.weekly_hour_target,
              label: `${weeks.weekly_hour_target} h target`,
              colour: HOURS_COLOUR,
            },
          },
        ],
      };
    }

    const sheet = this.programmeSeries();
    if (!sheet) return null;
    const floor = this.attendanceTarget();
    const drawn = sheet.series.filter((series) => series.source !== 'unavailable');
    if (drawn.length === 0) return null;
    const units: string[] = [];
    for (const unit of [PERCENT, HOURS, COUNT]) {
      if (drawn.some((series) => series.unit === unit)) units.push(unit);
    }
    return {
      labels: sheet.weeks.map((week) => week.label),
      axisUnits: units,
      caption: `Every student in reach · ${sheet.students_in_reach} counted`,
      lines: drawn.map((series) => ({
        name: series.label,
        unit: UNIT_SUFFIX[series.unit] ?? '',
        kind: SERIES_STYLE[series.key]?.kind ?? 'line',
        axisUnit: series.unit,
        points: series.points,
        target:
          series.key === 'attendance_pct' && floor !== null
            ? { value: floor, label: `${floor}% floor`, colour: STATUS_COLOURS.risk }
            : null,
      })),
    };
  });

  /** The colours the chart is drawn in, in the order its lines are. */
  private readonly chartColours = computed<string[]>(() => {
    if (this.selectedStudent()) return [ATTENDANCE_COLOUR, HOURS_COLOUR];
    const sheet = this.programmeSeries();
    if (!sheet) return [];
    return sheet.series
      .filter((series) => series.source !== 'unavailable')
      .map((series, index) => SERIES_STYLE[series.key]?.colour ?? CATEGORICAL_PALETTE[index % CATEGORICAL_PALETTE.length]);
  });

  /** Every sentence a cohort line carries about itself, so a `partial` or
   *  `unavailable` series is explained under the chart rather than drawn as a
   *  line that simply stops. Empty while a student is the subject: the server
   *  said nothing about their weeks.
   *
   *  LINES THAT SHARE A REASON SHARE A SENTENCE. When the reach reaches nobody
   *  the server gives all four series the same note, and four identical
   *  paragraphs stacked under an empty chart read as four separate faults. */
  readonly seriesNotes = computed<{ key: string; label: string; source: string; note: string }[]>(() => {
    if (this.selectedStudent()) return [];
    const sheet = this.programmeSeries();
    if (!sheet) return [];
    const byNote = new Map<string, { labels: string[]; source: string }>();
    for (const series of sheet.series) {
      if (series.source === 'live' || series.note === null) continue;
      const held = byNote.get(series.note);
      if (held) {
        held.labels.push(series.label);
      } else {
        byNote.set(series.note, { labels: [series.label], source: series.source });
      }
    }
    return [...byNote.entries()].map(([note, grouped]) => ({
      key: grouped.labels.join('|'),
      label: grouped.labels.join(' · '),
      source: grouped.source,
      note,
    }));
  });

  readonly chartIsProgrammeWide = computed(() => this.selectedStudent() === null);
  readonly studentHasResume = computed(() => this.studentWeekly()?.has_resume ?? false);

  readonly mentorLoadLoaded = computed(() => this.mentorLoad() !== null);
  readonly rosterIsEmpty = computed(() => this.mentorLoadLoaded() && this.mentorRows().length === 0);

  readonly openAlerts = computed<ProgrammeAlert[]>(() => this.alerts() ?? []);
  readonly alertsAreEmpty = computed(() => this.alerts() !== null && this.openAlerts().length === 0);

  constructor() {
    // AG Grid 33+ refuses to draw until its modules are registered, and fails
    // as an empty rectangle rather than an exception (shared/grid docstring).
    registerReepGrid();
    void this.loadTheConsoleFigures();
    // Its own request, not part of the batch above: the alert feed is
    // `/api/mentor/alerts`, a different router behind a different gate, and it
    // is rerun on its own whenever the Rules dialog closes.
    void this.loadAlerts();

    // The window is the one input both analytics endpoints take, so one effect
    // reads it and refetches both. They are fetched together because the
    // strip's deltas and the chart's weeks must describe the same span; two
    // independent loaders would let a reader see a 12-week strip over a 6-week
    // chart while the second was in flight.
    effect(() => {
      const weeks = this.periodWeeks();
      void this.loadAnalyticsWindow(weeks);
    });

    // The drill-down is fetched once per student and kept, so picking a student
    // twice does not download their six weeks twice.
    effect(() => {
      const studentId = this.chartSubject();
      if (studentId === EVERY_STUDENT) {
        this.studentWeekly.set(null);
        this.weeklyError.set(null);
        return;
      }
      const alreadyFetched = this.weeklyCache.get(studentId);
      if (alreadyFetched) {
        this.studentWeekly.set(alreadyFetched);
        return;
      }
      // Clear the frame BEFORE the request. Leaving the previous student's
      // series up while the next one is in flight draws one student's six weeks
      // under another student's name in the picker beside it — and if the
      // request then fails it stays there, permanently mislabelled.
      this.studentWeekly.set(null);
      void this.fetchStudentWeeks(studentId);
    });

    // The chart element only exists while there is a series to draw, so the
    // chart is created when it appears and disposed when it goes.
    effect(() => {
      const host = this.healthChartHost();
      if (!host) {
        this.disposeHealthChart();
        return;
      }
      this.drawWeeklyHealth(host.nativeElement);
    });
  }

  ngOnDestroy(): void {
    this.disposeHealthChart();
  }

  /** The reader picked a window. */
  selectPeriod(event: Event): void {
    const chosen = Number((event.target as HTMLSelectElement).value);
    if (!PERIOD_CHOICES.some((choice) => choice.weeks === chosen)) return;
    this.periodWeeks.set(chosen);
  }

  /** The reader picked the chart's subject: everybody, or one student. */
  selectChartSubject(event: Event): void {
    this.chartSubject.set((event.target as HTMLSelectElement).value);
  }

  /** The reader typed in the Mentor load quick filter. */
  setQuickFilter(event: Event): void {
    const box = event.target as HTMLInputElement;
    this.quickFilter.set(box.value);
  }

  openRules(): void {
    this.rulesOpen.set(true);
  }

  /** The rules editor closed. The feed is reread, because enabling a rule
   *  changes nothing tonight but DISABLING one is a decision the reader has
   *  just made and the card beside it should not still argue with. */
  closeRules(): void {
    this.rulesOpen.set(false);
    void this.loadAlerts();
  }

  /** The chip tone for an alert's severity — text and colour together. */
  alertTone(severity: string): string {
    if (severity === 'CRITICAL') return 'risk';
    if (severity === 'WARNING') return 'warn';
    return 'neutral';
  }

  /** "ATTENDANCE_BELOW_THRESHOLD" reads as "Attendance below threshold". */
  alertRuleLabel(ruleKey: string): string {
    const words = ruleKey.toLowerCase().split('_');
    if (words.length === 0) return ruleKey;
    const first = words[0];
    const rest = words.slice(1).join(' ');
    const opening = first.charAt(0).toUpperCase() + first.slice(1);
    if (rest.length === 0) return opening;
    return `${opening} ${rest}`;
  }

  /** The tone for a series that is not `live`. `partial` is a warning — some of
   *  the line is real — and `unavailable` is a refusal. */
  seriesTone(source: string): string {
    return source === 'partial' ? 'warn' : 'neutral';
  }

  seriesSourceLabel(source: string): string {
    return source === 'partial' ? 'Partly measured' : 'Not measured';
  }

  cvUrl(): string {
    return `${this.apiBase}/admin/students/${this.chartSubject()}/resume.pdf`;
  }

  // --- the composite chart ---------------------------------------------------

  private drawWeeklyHealth(host: HTMLDivElement): void {
    const model = this.chartModel();
    if (!model) return;

    if (!this.healthChart) {
      this.healthChart = echarts.init(host, REEP_CHART_THEME, { renderer: 'svg' });
      // ECharts cannot size itself inside a flex/grid parent that changes
      // without the window doing so — the sidebar collapsing is exactly that.
      this.chartResizeObserver = new ResizeObserver(() => this.healthChart?.resize());
      this.chartResizeObserver.observe(host);
    }

    // One axis per unit actually drawn, the first on the left and the rest on
    // the right, each offset clear of the one before it. Derived rather than
    // declared, because the cohort chart carries three units and the per-student
    // drill-down carries two — a fixed pair would silently plot approved offers
    // against an axis labelled in hours.
    const axes = model.axisUnits.map((unit, index) => ({
      type: 'value' as const,
      position: index === 0 ? ('left' as const) : ('right' as const),
      offset: index <= 1 ? 0 : (index - 1) * 52,
      min: 0,
      max: unit === PERCENT ? 100 : undefined,
      interval: unit === PERCENT ? 25 : undefined,
      axisLabel: { formatter: AXIS_FORMAT[unit] ?? '{value}' },
      splitLine: { show: index === 0 },
    }));

    const unitBySeries: Record<string, string> = {};
    for (const line of model.lines) unitBySeries[line.name] = line.unit;

    this.healthChart.setOption(
      {
        color: this.chartColours(),
        legend: { top: 0, left: 0, itemGap: 20 },
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'cross' },
          formatter: weeklyTooltipFormatter(unitBySeries),
        },
        grid: {
          left: 46,
          right: 56 + Math.max(0, model.axisUnits.length - 2) * 52,
          top: 40,
          bottom: 58,
          containLabel: false,
        },
        xAxis: { type: 'category', boundaryGap: true, data: model.labels },
        yAxis: axes,
        dataZoom: [{ type: 'slider', bottom: 8, height: 18, start: 0, end: 100 }],
        series: model.lines.map((line) => ({
          name: line.name,
          type: line.kind,
          yAxisIndex: Math.max(0, model.axisUnits.indexOf(line.axisUnit)),
          // A week nobody measured is a gap, not a dive to zero.
          connectNulls: false,
          barMaxWidth: 26,
          itemStyle: line.kind === 'bar' ? { opacity: 0.55 } : undefined,
          data: line.points,
          emphasis: { focus: 'series', blurScope: 'global' },
          markLine:
            line.target === null
              ? undefined
              : {
                  silent: true,
                  symbol: 'none',
                  data: [{ yAxis: line.target.value, name: line.target.label }],
                  lineStyle: { color: line.target.colour, type: 'dashed', width: 1.5 },
                  // INSIDE the plot, not at the end of the line. A markLine
                  // label defaults to `position: 'end'`, which draws it past
                  // the grid's right edge and into the gutter the right-hand
                  // axis labels already occupy -- so "12 h target" rendered as
                  // "12 h targe", clipped by the card.
                  label: {
                    position: 'insideEndTop',
                    formatter: line.target.label,
                    color: line.target.colour,
                    fontSize: 11,
                  },
                },
        })),
      },
      true,
    );
  }

  private disposeHealthChart(): void {
    this.chartResizeObserver?.disconnect();
    this.chartResizeObserver = null;
    this.healthChart?.dispose();
    this.healthChart = null;
  }

  // --- reads -----------------------------------------------------------------

  private async loadTheConsoleFigures(): Promise<void> {
    try {
      const [loadResponse, summaryResponse, criteriaResponse] = await Promise.all([
        fetch(`${this.apiBase}/admin/mentor-load`, { credentials: 'include' }),
        fetch(`${this.apiBase}/admin/analytics-summary`, { credentials: 'include' }),
        fetch(`${this.apiBase}/admin/criteria`, { credentials: 'include' }),
      ]);

      if (!loadResponse.ok) {
        this.error.set('Could not load the mentorship map.');
        this.mentorLoadFailed.set(true);
      } else {
        this.mentorLoad.set((await loadResponse.json()) as MentorLoad[]);
      }

      if (summaryResponse.ok) {
        this.summary.set((await summaryResponse.json()) as AnalyticsSummary);
      } else if (!this.error()) {
        this.error.set('Could not load the cohort figures.');
      }

      // No active criteria is a legitimate state (404): then there is simply no
      // attendance floor to draw.
      if (criteriaResponse.ok) {
        const criteria = (await criteriaResponse.json()) as { min_attendance_pct?: number };
        if (typeof criteria.min_attendance_pct === 'number') {
          this.attendanceTarget.set(criteria.min_attendance_pct);
        }
      }
    } catch {
      this.error.set('Could not reach the server.');
      this.mentorLoadFailed.set(true);
    }
  }

  private async loadAlerts(): Promise<void> {
    try {
      const response = await fetch(`${this.apiBase}/mentor/alerts?open_only=true`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.alertsFailed.set(true);
        return;
      }
      this.alertsFailed.set(false);
      this.alerts.set((await response.json()) as ProgrammeAlert[]);
    } catch {
      this.alertsFailed.set(true);
    }
  }

  /** The KPI strip and the cohort series for one window. */
  private async loadAnalyticsWindow(weeks: number): Promise<void> {
    this.analyticsBusy.set(true);
    this.kpiError.set(null);
    this.seriesError.set(null);
    try {
      const [kpiResponse, seriesResponse] = await Promise.all([
        fetch(`${this.apiBase}/admin/analytics/kpis?weeks=${weeks}`, { credentials: 'include' }),
        fetch(`${this.apiBase}/admin/analytics/series?weeks=${weeks}`, { credentials: 'include' }),
      ]);

      if (kpiResponse.ok) {
        this.kpiSheet.set((await kpiResponse.json()) as AnalyticsKpis);
      } else {
        // The strip is left as it was rather than emptied: a failed refetch
        // must not turn measured tiles into dashes that read as "nothing to
        // measure". The sentence says which window failed.
        this.kpiError.set(await this.detailOf(kpiResponse));
      }

      if (seriesResponse.ok) {
        this.programmeSeries.set((await seriesResponse.json()) as AnalyticsSeriesSheet);
      } else {
        this.seriesError.set(await this.detailOf(seriesResponse));
      }
    } catch {
      this.kpiError.set('Could not reach the server.');
      this.seriesError.set('Could not reach the server.');
    } finally {
      this.analyticsBusy.set(false);
    }
  }

  private async fetchStudentWeeks(studentId: string): Promise<void> {
    this.weeklyBusy.set(true);
    // Whatever went wrong for the last student is not this student's state.
    this.weeklyError.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/students/${studentId}/weekly`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.weeklyError.set("Could not load that student's six weeks.");
        if (this.chartSubject() === studentId) this.studentWeekly.set(null);
        return;
      }
      const weeks = (await response.json()) as StudentWeekly;
      this.weeklyCache.set(studentId, weeks);
      // Still the selected student? The reader may have moved on mid-flight.
      if (this.chartSubject() === studentId) this.studentWeekly.set(weeks);
    } catch {
      this.weeklyError.set('Could not reach the server.');
      if (this.chartSubject() === studentId) this.studentWeekly.set(null);
    } finally {
      this.weeklyBusy.set(false);
    }
  }

  /** FastAPI answers a 422 with `detail` as a LIST; rendered raw it reads
   *  "[object Object]" on the screen of whoever is trying to fix the form. */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as { detail?: unknown };
      const detail = body.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail)) {
        const messages = detail
          .map((entry) => (entry as { msg?: string }).msg)
          .filter((message): message is string => typeof message === 'string');
        if (messages.length > 0) return messages.join(' ');
      }
    } catch {
      /* not JSON — fall through to the status */
    }
    return `The request was refused (${response.status}).`;
  }
}
