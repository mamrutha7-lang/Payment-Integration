/**
 * Registrations — the Main Admin's review queue (spec §10, board
 * `design/admin/Registrations.html`).
 *
 * WHAT APPROVAL ACTUALLY DOES, because this screen used to say the opposite.
 * `POST /api/register/{id}/decision` with APPROVE now mints the User row, the
 * Student row seated in the rule's batch and a profile row, moves the CV and
 * photo onto that student's uploads, and emails the onboarding link — all in
 * one transaction (`_provision_student` in `app/routers/registration.py`, and
 * AGENTS.md, "Approval provisions, and two guards make that safe"). The copy
 * here said "Approval PROVISIONS NOTHING on its own" and "creating the Student
 * row is a separate step server-side", which was true of the endpoint that
 * shipped first and has been wrong since. That is B11.4, and it is the one
 * task on this screen that is a client change: it is fixed here.
 *
 * WHAT IS LIVE, AND ON WHICH ENDPOINT.
 *   GET  /api/register/pending            the queue. NO PARAMETERS is the
 *                                         historical unbounded pending list and
 *                                         is what the Pending tab asks for;
 *                                         `?status=&limit=` is the other three
 *                                         tabs (B11.2)
 *   POST /api/register/{id}/decision      approve, and reject WITH A REASON —
 *                                         the API answers 422 without one and
 *                                         this screen refuses before posting
 *   POST /api/register/{id}/hold          park it WITH A NOTE, same shape: 422
 *                                         without one, refused here first
 *   POST /api/register/{id}/reopen        back in the queue — the Rejected
 *                                         tab's Undo AND the Held tab's release,
 *                                         because they are one act
 *   GET  /api/register/rules              the Seating rules drawer — NARROWED
 *                                         the same way the writes are, so every
 *                                         rule listed is one this account can
 *                                         edit
 *   POST/PATCH/DELETE /api/register/rules the drawer's editor (B11.3)
 *   GET  /api/register/hierarchy          batch names for the queue and the
 *                                         College / Batch filters (public, and
 *                                         reachable by a grantee who holds
 *                                         `admin.registrations` and nothing
 *                                         else — `/api/admin/cohorts` is not,
 *                                         it asks for `admin.analytics`)
 *   GET  /api/register/{id}/documents/{kind}/file
 *                                         the CV or the headshot the applicant
 *                                         attached, `kind` being `cv` or
 *                                         `photo` — same `admin.registrations`
 *                                         gate and the same reachability
 *                                         predicate the queue itself is drawn
 *                                         from, so a row this account cannot
 *                                         see is a file it cannot read by id
 *
 * THE REVIEWER CAN LOOK AT WHAT WAS ATTACHED, AND COULD NOT BEFORE. The panel
 * said "CV + photo" in a chip and "CV attached" in a check line, and neither
 * one opened anything: the Main Admin approved or rejected a person having seen
 * the WORD "CV" and never the file. Approve moves those exact bytes onto the
 * new student's own uploads as their first RESUME and PROFILE_PHOTO
 * (`_move_documents_to_uploads`), so a blank PDF or somebody else's headshot
 * lands on a real roster row and nothing catches it until the student does.
 * This decision is the only moment anybody can look.
 *
 * They are LINKS AND NOT A PREVIEW, because the endpoint answers
 * `Content-Disposition: attachment` — `document_store.content_disposition`'s
 * default, for the reason that module gives: every byte in that store was
 * uploaded by a stranger, here literally an unauthenticated one, and a PDF
 * rendered inline runs its embedded JavaScript in this SPA's own origin. So the
 * browser saves the file rather than drawing it, the glyph is `download`, and
 * there is no `target="_blank"` to flash a tab that closes itself. The chip and
 * the check lines stay exactly where they are: "is anything attached" is an
 * at-a-glance question and a different one from "is this the right file".
 *
 * THE CHECKS ARE THE SERVER'S NOW (B11.1). Every row of `GET /pending` carries
 * `checks[]`: the rule engine's verdict, the college domain fence, whose account
 * this address already is, and the USN. Three of those are the guards
 * `_provision_student` applies when Approve is pressed, read out BEFORE the
 * press — so a red line here and a 4xx there are one answer and not two, which
 * is only true because this screen renders what it is given and recomputes
 * nothing. The client's own guess at the rule check is gone for that reason.
 * The batch line and the two attachment lines stay client-side: neither is a
 * guard and neither can contradict a decision.
 *
 * THERE IS NO SEAT CHECK AND THERE IS NOT GOING TO BE ONE. The board draws
 * "60 of 60 seats"; `cohorts` has no capacity column and 04 §B11.1 asks for
 * none. A nullable one would render as "no check" on every batch nobody had
 * filled in — grey exactly where it would matter — and seating capacity is a
 * course-shape decision that belongs with the catalogue. The notice under the
 * checklist used to promise it under B11.1 and now says plainly that nothing
 * counts seats.
 *
 * THE FOUR TABS ARE LIVE (B11.2). They were drawn disabled because the queue
 * could only be asked for one hard-coded status; `?status=` is that gap closed.
 * HOLD is the new one and it is INTERNAL: a note a reviewer writes about an
 * application for colleagues, no mail, nothing the applicant can see. A held
 * application is still decidable, is still counted on the Analytics pending
 * tile, and can still receive the document it is being held for.
 *
 * THE SEATING RULES ARE EDITABLE NOW (B11.3), AND THAT IS THE SHARPEST CONTROL
 * ON THIS SCREEN. A rule with auto-approve and a batch provisions an account
 * with no human in the loop — the application never reaches the queue this
 * screen is — so the drawer says so in words beside the switch, refuses a
 * conditionless auto-approve in the server's own sentence before posting, and
 * offers Disable beside Delete because disabling is the reversible one:
 * `registrations.matched_rule_id` is ON DELETE SET NULL, so deleting a rule
 * blanks the Rule column on every application it ever routed.
 *
 * THE USN PATTERN IS A REGULAR EXPRESSION THE PUBLIC ENDPOINT RUNS. The server
 * validates it at write time and answers 422 with a sentence written for whoever
 * typed it; this screen prints that sentence verbatim rather than replacing it
 * with "Invalid pattern", because the author is the only person who can fix it.
 * There is deliberately no client-side regex check: a second opinion about what
 * `validate_usn_pattern` refuses is a second opinion that will be wrong.
 *
 * NOTHING ON THIS SCREEN IS DISABLED FOR A LATER PHASE ANY MORE.
 *
 * BULK IS THE LOOP, deliberately. There is no bulk decision endpoint; "Approve
 * 3 selected" is three POSTs and the notice says how many landed. A failure
 * stops the run rather than pressing on, because the reviewer needs to know
 * which application refused and why.
 */

import { DatePipe } from '@angular/common';
import { Component, Injector, afterNextRender, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AgGridAngular } from 'ag-grid-angular';
import type {
  CellClickedEvent,
  ColDef,
  GetQuickFilterTextParams,
  GetRowIdParams,
  GridApi,
  GridReadyEvent,
  ICellRendererParams,
  IRowNode,
  ProcessCellForExportParams,
  RowSelectionOptions,
  SelectionColumnDef,
  ValueFormatterParams,
} from 'ag-grid-community';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import { specializationLabel } from '../../../core/specializations';
import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { reepGridTheme, reepGridThemeCompact } from '../../../shared/grid/reep-grid-theme';
import { plural } from '../../../shared/text/plural.pipe';

// ----------------------------------------------------------------- rules --

/** The board's four tabs, and what each one asks the server for.
 *
 *  THE PENDING TAB SENDS NO `?status=` AT ALL, and that is deliberate rather
 *  than a shortcut: `GET /register/pending` answers its historical list — every
 *  pending application in reach, oldest first, UNBOUNDED — only when it is
 *  called with no parameters, and that list is the one the office works through
 *  every morning. Asking for it by name with a page size would silently cap it.
 *
 *  The other three are bounded, because AUTO_APPROVED and REJECTED grow for the
 *  life of the deployment. */
type QueueStatus = 'PENDING_REVIEW' | 'HOLD' | 'AUTO_APPROVED' | 'REJECTED';

const QUEUE_TABS: { key: QueueStatus; label: string }[] = [
  { key: 'PENDING_REVIEW', label: 'Pending' },
  { key: 'AUTO_APPROVED', label: 'Auto-approved' },
  { key: 'HOLD', label: 'Held' },
  { key: 'REJECTED', label: 'Rejected' },
];

/** How many rows a non-default tab asks for. The server clamps at 500 of its
 *  own accord; this is the screen saying what it can actually draw. */
const DECIDED_TAB_PAGE = 200;

/** The board's "Submitted" filter. Client-side, over the rows already loaded —
 *  `GET /pending` takes no date parameter. */
const SUBMITTED_WINDOWS: { key: string; label: string; days: number | null }[] = [
  { key: 'all', label: 'Any time', days: null },
  { key: 'week', label: 'Last 7 days', days: 7 },
  { key: 'month', label: 'Last 30 days', days: 30 },
  { key: 'quarter', label: 'Last 90 days', days: 90 },
];

const HOURS_IN_A_DAY = 24;
const MINUTES_IN_AN_HOUR = 60;
const SECONDS_IN_A_MINUTE = 60;
const MILLISECONDS_IN_A_SECOND = 1000;
const MILLISECONDS_IN_A_DAY =
  HOURS_IN_A_DAY * MINUTES_IN_AN_HOUR * SECONDS_IN_A_MINUTE * MILLISECONDS_IN_A_SECOND;

const PAGE_SIZES = [10, 25, 50, 100];
const DEFAULT_PAGE_SIZE = 10;

/** Rows per page in the comfortable and the compact density (01 §4). */
const COMFORTABLE_ROW_HEIGHT_PX = 40;
const COMPACT_ROW_HEIGHT_PX = 36;

/** Every college in the queue, every batch in the queue. */
const EVERY_COLLEGE = '';
const EVERY_BATCH = '';

/** What a field the application never carried reads as. Never a zero and never
 *  a guess: a missing USN and a wrong one are different facts. */
const NOT_ON_RECORD = 'Not on record';

/** The two things an applicant may attach, and the one place this screen turns
 *  the kind the payload carries into the path segment the file endpoint takes.
 *
 *  THEY ARE TWO VOCABULARIES THAT HAPPEN TO RHYME. `documents[]` carries the
 *  model's own constants — `DOCUMENT_KIND_CV` / `DOCUMENT_KIND_PHOTO` in
 *  `app/models/registration.py`, upper case — while `_DOCUMENT_ROUTES` keys the
 *  route on `cv` / `photo`. Lower-casing the payload would be right today and
 *  quietly wrong the day a third kind arrives whose two spellings differ by more
 *  than case, and it would be wrong as a 404 on a button the reviewer can see,
 *  which reads as a broken console rather than as a mapping nobody updated. A
 *  table makes adding that kind an edit here.
 *
 *  The order is the order the checklist reads them in, so the links and the two
 *  lines under them agree about which document is which. */
const OPENABLE_DOCUMENTS: { kind: string; segment: string; label: string }[] = [
  { kind: 'CV', segment: 'cv', label: 'Open CV' },
  { kind: 'PHOTO', segment: 'photo', label: 'Open photo' },
];

// ------------------------------------------------------- the API payloads --

/**
 * `CheckOut` in `app/routers/registration.py` — one line of the reviewer's
 * pre-decision checklist, computed by the SERVER.
 *
 * THE VOCABULARY IS THE SERVER'S AND IS NOT RESTATED HERE. "blocked" means
 * Approve will refuse this exact application with a 4xx, and every blocked
 * check is one-to-one with a guard in `_provision_student`; "warn" means Approve
 * will succeed but something is worth a human's eye; "ok" means nothing to
 * report. The screen branches on `key`, never on `label`, which is prose.
 *
 * The key set on a row is NOT fixed: `usn_pattern` appears only where a matched
 * rule declares a pattern. Render what arrives, in the order it arrives.
 */
interface CheckApiRow {
  key: string;
  status: 'ok' | 'warn' | 'blocked';
  label: string;
  detail: string;
  /** The student the check names (2026-10-16): on `usn_unique`, the one who
   *  already holds this USN, sent only to a programme-wide reviewer. */
  student_id?: string | null;
}

/** `RegistrationOut` in `app/routers/registration.py`. */
interface RegistrationApiRow {
  id: string;
  name: string;
  email: string;
  usn: string | null;
  /** How to reach the applicant (2026-09-16): required by the form, so null
   *  only on an application older than the rule. Staff-only on the server. */
  phone: string | null;
  personal_email: string | null;
  linkedin_url: string | null;
  degree_level: string;
  status: string;
  cohort_id: string | null;
  matched_rule_id: string | null;
  /** The rule engine's own verdict sentence, written at submit time. */
  decision_reason: string | null;
  created_at: string;
  /** Kinds attached with the application: "CV", "PHOTO". */
  documents: string[];
  college_name: string | null;
  department_name: string | null;
  course_name: string | null;
  specialization_name: string | null;
  /** The other tick of a dual specialization (2026-09-22); null when the
   *  applicant ticked one or none. The panel prints both as one line. */
  second_specialization_name: string | null;
  /** The batch the APPLICANT asked for; `cohort_id` is the rule's, and wins. */
  requested_batch: string | null;
  /** B11.1. NULL MEANS NOT COMPUTED — only a row somebody can still decide gets
   *  a checklist, and the single-row answers from `decision`, `hold` and
   *  `reopen` leave it null rather than answering `[]`, which would render as a
   *  clean bill of health for an application nobody checked. Treat null as "no
   *  checklist", never as "no problems". The Auto-approved and Rejected tabs
   *  carry null on every row for the same reason: a live "Approve will refuse
   *  this" is an answer to a question that was settled last month. */
  checks: CheckApiRow[] | null;
  /** B11.2, and staff-only — `PublicRegistrationOut` declares none of the three,
   *  so a hold note cannot reach the applicant it is written about. Null on
   *  every application that is not, and has never been, on hold. */
  hold_note: string | null;
  held_by_id: string | null;
  held_at: string | null;
}

/** `RuleOut` in `app/routers/registration.py`. */
interface SeatingRuleApiRow {
  id: string;
  name: string;
  enabled: boolean;
  email_domain: string | null;
  usn_pattern: string | null;
  degree_level: string | null;
  cohort_id: string | null;
  auto_approve: boolean;
  priority: number;
  /** The tiebreak `_pick_rule` uses when two rules share a priority — the older
   *  wins, "so a rule added later can't silently outrank an equal". The drawer
   *  shows it for that reason and for no other. */
  created_at: string;
}

/** `PublicHierarchyOut` in `app/routers/registration.py`, narrowed to the two
 *  levels this screen names: the college and the batch. */
interface HierarchyBatch {
  id: string;
  /** The batch itself: a YEAR, "2026-28". */
  name: string;
  batch_label: string;
  /** The spine and the year in one sentence, composed by the server from the
   *  batch's own links: "General MBA - Finance · 2026-28". The queue labels a
   *  batch with this — `name` alone is the year, and a reviewer deciding where
   *  an applicant lands cannot act on four options all reading "2026-28". */
  display_label: string;
}
interface HierarchyDepartment {
  batches: HierarchyBatch[];
}
interface HierarchyCollege {
  id: string;
  name: string;
  departments: HierarchyDepartment[];
}

// ------------------------------------------------- what the screen holds --

/** One row of the grid: the application plus everything a cell draws, resolved
 *  once here rather than in a renderer that runs on every repaint. */
interface QueueRow {
  registrationId: string;
  name: string;
  email: string;
  initials: string;
  usn: string | null;
  emailDomain: string;
  submittedAt: string;
  submittedLabel: string;
  collegeName: string | null;
  batchLabel: string;
  documentsLabel: string;
  documentsTone: string;
  ruleLabel: string;
  ruleTone: string;
  /** The server's `domain` check for this row, or null on a payload that
   *  carried no checklist. Null is NOT "the domain is fine" — the cell renders
   *  neutral and says so, which is the same distinction the server draws. */
  domainCheck: CheckApiRow | null;
  domainTone: string;
  application: RegistrationApiRow;
}

/** One line of the panel's Checks list.
 *
 *  Most of these now come from the SERVER (`checks[]`, B11.1) — the rule
 *  engine's verdict, the college domain fence, whose account this address
 *  already is and the USN. Two are still the client's, because the payload
 *  already answers them and the server does not: the batch a rule seated them
 *  in, and what they attached. The `risk` tone is the server's "blocked": a
 *  check that says Approve WILL refuse this application. */
interface ApplicationCheck {
  headline: string;
  detail: string;
  tone: 'good' | 'warn' | 'neutral' | 'risk';
  icon: string;
  /** Student 360 of the student the check names. Set only for a session
   *  holding `admin.student_records`, which that route asks for. */
  recordLink?: string | null;
}

/** The server's three verdicts, mapped onto this screen's tones and glyphs.
 *  One place, so a new verdict is a compile error rather than a silent grey. */
const CHECK_TONES: Record<CheckApiRow['status'], { tone: ApplicationCheck['tone']; icon: string }> = {
  ok: { tone: 'good', icon: 'check_circle' },
  warn: { tone: 'warn', icon: 'warning' },
  blocked: { tone: 'risk', icon: 'block' },
};

/** One attachment the panel offers to open: the label the reviewer reads and
 *  the URL behind it, resolved once per reviewed application rather than in a
 *  template expression that runs on every repaint. `kind` is carried through
 *  only so `@for` has something stable to track. */
interface OpenableDocument {
  kind: string;
  label: string;
  href: string;
}

/** The key of the server check the Email domain column colours itself from. */
const DOMAIN_CHECK_KEY = 'domain';

/** The board's "Domain check" filter, live since B11.1. */
const DOMAIN_FILTERS: { key: string; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'ok', label: 'On a college domain' },
  { key: 'blocked', label: 'Off-domain (Approve refuses)' },
];
const EVERY_DOMAIN_VERDICT = 'all';

/** A seating rule as the drawer reads it out — the rendered half AND the raw
 *  half, so Edit prefills the form from what is already loaded instead of
 *  fetching the row again and briefly showing a form full of nothing. */
interface SeatingRuleLine {
  id: string;
  name: string;
  priority: number;
  enabled: boolean;
  autoApprove: boolean;
  conditions: string;
  seatsIn: string;
  createdAt: string;
  emailDomain: string | null;
  usnPattern: string | null;
  degreeLevel: string | null;
  cohortId: string | null;
}

/** What the rule form holds. STRINGS THROUGHOUT, because a form field is a
 *  string: the conversion to the API's nulls, numbers and enum values happens
 *  once, in `saveRule`, rather than in eight input handlers that would each have
 *  their own idea of what an empty box means. */
interface RuleDraft {
  name: string;
  priority: string;
  emailDomain: string;
  usnPattern: string;
  degreeLevel: string;
  cohortId: string;
  autoApprove: boolean;
  enabled: boolean;
}

const EMPTY_RULE_DRAFT: RuleDraft = {
  name: '',
  priority: '100',
  emailDomain: '',
  usnPattern: '',
  degreeLevel: '',
  cohortId: '',
  autoApprove: false,
  enabled: true,
};

/** The degree levels a rule can require, plus "any". `DegreeLevel` in
 *  `app/models/job.py`; the empty key is the API's null. */
const RULE_DEGREE_LEVELS: { key: string; label: string }[] = [
  { key: '', label: 'Any degree level' },
  { key: 'UG', label: 'UG only' },
  { key: 'PG', label: 'PG only' },
];

// --------------------------------------------------------- cell renderers --
//
// AG Grid builds cell contents itself, outside Angular's template compiler, so
// a component-scoped class never reaches them. These renderers use GLOBAL
// classes (`.chip`, `.avatar` in styles/reep-v2.scss) and inline styles,
// exactly as the roster grid on Students & batches does.

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderApplicantCell(params: ICellRendererParams<QueueRow>): string {
  const row = params.data;
  if (row === undefined) return '';
  return (
    `<span class="avatar">${escapeHtml(row.initials)}</span>` +
    `<span style="display: inline-flex; flex-direction: column; line-height: 1.25; min-width: 0;">` +
    `<span style="font-weight: 600; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(row.name)}</span>` +
    `<span style="font-size: 11px; color: var(--faint); overflow: hidden; text-overflow: ellipsis;">${escapeHtml(row.email)}</span>` +
    `</span>`
  );
}

function renderUsnCell(params: ICellRendererParams<QueueRow>): string {
  const row = params.data;
  if (row === undefined) return '';
  if (row.usn === null) {
    return `<span style="color: var(--faint);">${NOT_ON_RECORD}</span>`;
  }
  return `<span style="font-variant-numeric: tabular-nums; letter-spacing: 0.01em;">${escapeHtml(row.usn)}</span>`;
}

/**
 * The domain the onboarding link will be sent to, coloured by the SERVER's
 * verdict (B11.1).
 *
 * It was neutral until B11.1, because nothing held the college's registered
 * domains and a green chip would have been a judgement no code made. The chip
 * is now the `domain` check that `domain_verdict` computed — the same function
 * GUARD 1 calls when Approve is pressed — so red here and a 422 there are one
 * answer rather than two. A row that carried no checklist stays neutral, for
 * the original reason.
 */
function renderEmailDomainCell(params: ICellRendererParams<QueueRow>): string {
  const row = params.data;
  if (row === undefined) return '';
  const title = row.domainCheck === null ? '' : ` title="${escapeHtml(row.domainCheck.label)}"`;
  return `<span class="chip ${row.domainTone}"${title}>${escapeHtml(row.emailDomain)}</span>`;
}

function renderDocumentsCell(params: ICellRendererParams<QueueRow>): string {
  const row = params.data;
  if (row === undefined) return '';
  return `<span class="chip dot ${row.documentsTone}">${escapeHtml(row.documentsLabel)}</span>`;
}

function renderRuleCell(params: ICellRendererParams<QueueRow>): string {
  const row = params.data;
  if (row === undefined) return '';
  return `<span class="chip dot ${row.ruleTone}">${escapeHtml(row.ruleLabel)}</span>`;
}

// ----------------------------------------------------------- the CSV file --

/**
 * A cell a spreadsheet must read as TEXT, never as a formula.
 *
 * Excel, Sheets and LibreOffice evaluate any cell whose value BEGINS with
 * `=`, `+`, `-` or `@` — and with a tab or carriage return, which get eaten on
 * the way in and leave the next character leading. So an applicant who types
 * `=HYPERLINK("http://evil","click")` into the name field of the PUBLIC
 * registration form has written live code into the office's spreadsheet. The
 * values in this export are the least trustworthy input in the product:
 * members of the public type every one of them, and nobody reviews the string
 * before the reviewer double-clicks the file.
 *
 * AG Grid wraps every value in double quotes and doubles the quotes inside it,
 * which makes the file parse correctly — quoting is not what stops a formula,
 * because a spreadsheet strips the quotes and then evaluates what is left.
 *
 * THE APOSTROPHE BELOW IS DELIBERATE AND IT IS NOT A BUG. A leading `'` is the
 * spreadsheets' own "the rest of this cell is text" marker: it lands INSIDE
 * the quotes AG Grid adds, so the file reads `"'=HYPERLINK(…)"`, and Excel and
 * Sheets show the cell as the literal text without displaying the apostrophe.
 * Anyone opening the CSV in a text editor will see it; that is the cost, and
 * it is the standard one. Cells that do not start with one of those characters
 * are passed through untouched.
 */
const FORMULA_LEAD = /^[=+\-@\t\r]/;

function csvCellAsText(value: string): string {
  return FORMULA_LEAD.test(value) ? `'${value}` : value;
}

/** What the quick filter matches on the Applicant column: the name AND the
 *  address the cell draws. */
function quickFilterApplicant(params: GetQuickFilterTextParams<QueueRow>): string {
  const row = params.data;
  if (row === undefined) return '';
  return `${row.name} ${row.email}`;
}

function formatSubmitted(params: ValueFormatterParams<QueueRow, string>): string {
  return params.data?.submittedLabel ?? '';
}

/** Sort Submitted by the timestamp, never by the "10 Sep" the cell shows. */
function compareSubmitted(
  _valueA: string,
  _valueB: string,
  nodeA: IRowNode<QueueRow>,
  nodeB: IRowNode<QueueRow>,
): number {
  const first = nodeA.data?.submittedAt ?? '';
  const second = nodeB.data?.submittedAt ?? '';
  if (first === second) return 0;
  if (first < second) return -1;
  return 1;
}

// ----------------------------------------------------------------- helpers --

function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter((word) => word.length > 0);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/** One server check off a row, by key. Null when the payload carried no
 *  checklist at all — which is a different fact from "that check passed". */
function checkOf(application: RegistrationApiRow, key: string): CheckApiRow | null {
  return application.checks?.find((check) => check.key === key) ?? null;
}

/** The chip class for a server check. NEUTRAL for a check that was not
 *  computed: a green chip there would be a verdict no code made, which is
 *  exactly what this cell was neutral to avoid before B11.1 existed. */
function toneOfCheck(check: CheckApiRow | null): string {
  if (check === null) return 'neutral';
  if (check.status === 'ok') return 'good';
  if (check.status === 'warn') return 'warn';
  return 'risk';
}

function emailDomainOf(email: string): string {
  const at = email.lastIndexOf('@');
  if (at === -1 || at === email.length - 1) return 'No domain';
  return email.slice(at + 1).toLowerCase();
}

function documentsLabelOf(kinds: string[]): string {
  const attached = new Set(kinds);
  if (attached.has('CV') && attached.has('PHOTO')) return 'CV + photo';
  if (attached.has('CV')) return 'CV only';
  if (attached.has('PHOTO')) return 'Photo only';
  return 'None';
}

function documentsToneOf(kinds: string[]): string {
  const attached = new Set(kinds);
  if (attached.has('CV') && attached.has('PHOTO')) return 'good';
  if (attached.size > 0) return 'warn';
  return 'neutral';
}

function shortDateOf(isoTimestamp: string): string {
  const submitted = new Date(isoTimestamp);
  if (Number.isNaN(submitted.getTime())) return isoTimestamp;
  return submitted.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
}

// ------------------------------------------------------------- the screen --

@Component({
  selector: 'app-admin-registrations',
  standalone: true,
  imports: [DatePipe, AgGridAngular, RouterLink],
  templateUrl: './registrations.component.html',
  styleUrl: './registrations.component.scss',
})
export class AdminRegistrationsComponent {
  private readonly auth = inject(AuthService);
  private readonly injector = inject(Injector);

  readonly queueTabs = QUEUE_TABS;
  readonly submittedWindows = SUBMITTED_WINDOWS;
  readonly domainFilters = DOMAIN_FILTERS;
  readonly ruleDegreeLevels = RULE_DEGREE_LEVELS;
  readonly everyDomainVerdict = EVERY_DOMAIN_VERDICT;
  readonly pageSizes = PAGE_SIZES;
  readonly everyCollege = EVERY_COLLEGE;
  readonly everyBatch = EVERY_BATCH;

  // --- what the server said ---------------------------------------------

  readonly applications = signal<RegistrationApiRow[] | null>(null);
  readonly batchNames = signal<Map<string, string>>(new Map());
  /** The rules as `GET /register/rules` answered them. The lines are DERIVED
   *  from these and the batch names, as `rows` is from `applications`: the two
   *  reads race when the screen opens, and a "Seats in" worked out once when
   *  the rules landed read "Batch 9b799137" after the names had arrived. */
  private readonly seatingRuleRows = signal<SeatingRuleApiRow[] | null>(null);
  readonly seatingRules = computed<SeatingRuleLine[] | null>(() => {
    const rules = this.seatingRuleRows();
    if (rules === null) return null;
    return rules.map((rule) => this.toSeatingRuleLine(rule));
  });
  readonly seatingRulesError = signal<string | null>(null);
  /** `X-Reep-Scope` off the rules list: `programme`, `narrowed` or `none`. */
  readonly rulesScope = signal<string | null>(null);

  // --- what the reviewer is doing ---------------------------------------

  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly deciding = signal(false);
  readonly undoing = signal(false);
  /** Which tab is open. One request per tab, on demand — four queues held at
   *  once would be four lists going stale behind three tabs nobody is looking
   *  at, and a decision taken on one of them would leave the others wrong. */
  readonly activeTab = signal<QueueStatus>('PENDING_REVIEW');
  readonly quickFilter = signal('');
  readonly collegeFilter = signal(EVERY_COLLEGE);
  readonly batchFilter = signal(EVERY_BATCH);
  readonly domainFilter = signal(EVERY_DOMAIN_VERDICT);
  readonly submittedFilter = signal(SUBMITTED_WINDOWS[0].key);
  readonly decisionNote = signal('');
  readonly noteError = signal<string | null>(null);
  /** The application whose USN is being corrected, by id, or null. Keyed by
   *  id so the box belongs to one application and not to the panel. */
  readonly usnEditingId = signal<string | null>(null);
  readonly usnDraft = signal('');
  readonly usnSaving = signal(false);
  readonly usnError = signal<string | null>(null);
  readonly seatingRulesOpen = signal(false);

  // --- the rule editor (B11.3) -------------------------------------------
  //
  // ONE FORM, TWO VERBS. `editingRuleId` null means POST and a rule id means
  // PATCH; the fields are identical either way, so a second "edit" form would be
  // the same eight inputs maintained twice. `ruleBusyId` is the row-level spinner
  // for Disable and Delete, which act without opening the form at all.
  readonly ruleFormOpen = signal(false);
  readonly editingRuleId = signal<string | null>(null);
  readonly ruleDraft = signal<RuleDraft>(EMPTY_RULE_DRAFT);
  readonly ruleSaving = signal(false);
  readonly ruleFormError = signal<string | null>(null);
  readonly ruleFlash = signal<string | null>(null);
  readonly ruleBusyId = signal<string | null>(null);
  readonly columnsPanelOpen = signal(false);
  readonly isCompact = signal(false);
  readonly pageSize = signal(DEFAULT_PAGE_SIZE);

  /** The application whose detail the panel shows when nothing is ticked. */
  readonly focusedRegistrationId = signal<string | null>(null);
  readonly selectedRows = signal<QueueRow[]>([]);

  /** The rejections the last action made, for the board's Undo. A rejection
   *  only stamps a row, so reopening it is exact; an APPROVE provisions a real
   *  account and emails its owner, and `POST /reopen` refuses it with 409. */
  readonly lastRejected = signal<RegistrationApiRow[]>([]);

  // --- the grid's own state ----------------------------------------------

  private gridApi: GridApi<QueueRow> | null = null;
  readonly filteredRowCount = signal(0);
  readonly currentPage = signal(0);
  readonly totalPages = signal(1);
  readonly hiddenColumnIds = signal<Set<string>>(new Set());

  constructor() {
    registerReepGrid();
    void this.loadQueue();
    void this.loadBatchNames();
    void this.loadSeatingRules();
  }

  // ============================================================ the rows ====

  readonly rows = computed<QueueRow[]>(() => {
    const loaded = this.applications();
    if (loaded === null) return [];
    return loaded.map((application) => this.toQueueRow(application));
  });

  private toQueueRow(application: RegistrationApiRow): QueueRow {
    return {
      registrationId: application.id,
      name: application.name,
      email: application.email,
      initials: initialsOf(application.name),
      usn: application.usn,
      emailDomain: emailDomainOf(application.email),
      submittedAt: application.created_at,
      submittedLabel: shortDateOf(application.created_at),
      collegeName: application.college_name,
      batchLabel: this.batchLabelOf(application),
      documentsLabel: documentsLabelOf(application.documents),
      documentsTone: documentsToneOf(application.documents),
      ruleLabel: application.matched_rule_id === null ? 'Needs a decision' : 'Rule matched',
      ruleTone: application.matched_rule_id === null ? 'warn' : 'good',
      domainCheck: checkOf(application, DOMAIN_CHECK_KEY),
      domainTone: toneOfCheck(checkOf(application, DOMAIN_CHECK_KEY)),
      application,
    };
  }

  // ============================================================== the tabs ==

  /** THE COUNT IS ON THE PENDING TAB ALONE, and that is honesty rather than
   *  laziness. The default queue is unbounded, so the number of rows loaded IS
   *  the number waiting; every other tab is a bounded page, where the same
   *  arithmetic would draw "Rejected · 200" over a table of four thousand. A
   *  count nobody can trust is worse than no count, and the board's numbers
   *  would be exactly that. The grid's own status bar says how many rows are in
   *  view, which is a claim this screen can actually make. */
  /** AND ONLY WHILE PENDING IS THE TAB THAT IS OPEN. `waitingCount()` counts the
   *  ONE list this screen holds, which belongs to whichever tab is showing — so
   *  reading it onto the Pending label from the Held tab would print the number
   *  of held applications beside the word "Pending". The count goes away with
   *  the list it describes rather than going stale. */
  tabLabel(tab: { key: QueueStatus; label: string }): string {
    if (tab.key !== 'PENDING_REVIEW' || this.activeTab() !== 'PENDING_REVIEW') return tab.label;
    if (this.applications() === null) return tab.label;
    return `${tab.label} · ${this.waitingCount()}`;
  }

  setTab(tab: QueueStatus): void {
    if (this.activeTab() === tab) return;
    this.activeTab.set(tab);
    // Everything below belongs to the tab being left. A decision note typed for
    // one application must not survive onto another, and an Undo offer for a
    // rejection made on the Pending tab must not sit above the Rejected list it
    // would now be acting on twice.
    this.applications.set(null);
    this.clearReview();
    this.decisionNote.set('');
    this.flash.set(null);
    this.error.set(null);
    this.lastRejected.set([]);
    void this.loadQueue();
  }

  /** Whether the rows on this tab can still be approved or rejected. The server
   *  is the authority — `POST /decision` answers 409 on a decided row — and this
   *  is the screen refusing first, in the same words. */
  readonly canDecide = computed(
    () => this.activeTab() === 'PENDING_REVIEW' || this.activeTab() === 'HOLD',
  );

  /** Hold applies to a waiting application only. A held one is re-held by
   *  releasing it first, which the server refuses to shortcut: two audit rows
   *  say what happened, where an in-place edit of the note would lose the first
   *  reviewer's words. */
  readonly canHold = computed(() => this.activeTab() === 'PENDING_REVIEW');

  /** Reopen is one verb with one meaning — "back in the queue" — so it releases
   *  a hold and undoes a rejection through the same route. An APPROVED
   *  application is not on either of these tabs, which is the only reason this
   *  button never meets the 409 that refuses it. */
  readonly canReopen = computed(
    () => this.activeTab() === 'HOLD' || this.activeTab() === 'REJECTED',
  );

  /** The panel's Specialization line: both ticks of a dual specialization as
   *  one sentence ("Finance and Marketing"), else the one tick, else the
   *  course, else "Not named" — the same fallback the line had as a
   *  one-name field. */
  specializationOf(application: RegistrationApiRow): string {
    return (
      specializationLabel(application.specialization_name, application.second_specialization_name) ??
      application.course_name ??
      'Not named'
    );
  }

  /** THE BATCH IS THE PROGRAMME, when a rule chose one. With no batch, the
   *  degree level is all the application knows, and that is what is shown. */
  batchLabelOf(application: RegistrationApiRow): string {
    if (application.cohort_id === null) {
      return `${application.degree_level} · no batch yet`;
    }
    const named = this.batchNames().get(application.cohort_id);
    if (named === undefined) {
      return `${application.degree_level} · batch ${application.cohort_id.slice(0, 8)}`;
    }
    return named;
  }

  readonly waitingCount = computed(() => this.rows().length);

  readonly collegeOptions = computed(() => {
    const named = new Set<string>();
    for (const row of this.rows()) {
      if (row.collegeName !== null) named.add(row.collegeName);
    }
    return [...named].sort();
  });

  readonly batchOptions = computed(() => {
    const named = new Set<string>();
    for (const row of this.rows()) {
      named.add(row.batchLabel);
    }
    return [...named].sort();
  });

  /** The four client-side filters the board draws above the grid. The quick
   *  filter is the grid's own and is not repeated here.
   *
   *  CLIENT-SIDE, INCLUDING THE DOMAIN ONE. `GET /pending` takes no query
   *  parameters and returns the reviewer's whole reach; every one of these
   *  narrows rows the caller was already entitled to see, so none of them is
   *  doing access control. What decides which applications arrive at all is
   *  `registration_scope_clause` on the server. */
  readonly visibleRows = computed(() => {
    const college = this.collegeFilter();
    const batch = this.batchFilter();
    const domain = this.domainFilter();
    const window = this.submittedWindow();
    return this.rows().filter((row) => {
      if (college !== EVERY_COLLEGE && row.collegeName !== college) return false;
      if (batch !== EVERY_BATCH && row.batchLabel !== batch) return false;
      if (domain !== EVERY_DOMAIN_VERDICT && (row.domainCheck?.status ?? null) !== domain) {
        return false;
      }
      if (!this.wasSubmittedWithin(row, window)) return false;
      return true;
    });
  });

  private submittedWindow(): number | null {
    const chosen = SUBMITTED_WINDOWS.find((window) => window.key === this.submittedFilter());
    if (chosen === undefined) return null;
    return chosen.days;
  }

  private wasSubmittedWithin(row: QueueRow, days: number | null): boolean {
    if (days === null) return true;
    const submitted = new Date(row.submittedAt).getTime();
    if (Number.isNaN(submitted)) return true;
    return Date.now() - submitted <= days * MILLISECONDS_IN_A_DAY;
  }

  readonly collegeFilterLabel = computed(() => {
    const chosen = this.collegeFilter();
    if (chosen === EVERY_COLLEGE) return 'All colleges';
    return chosen;
  });

  readonly batchFilterLabel = computed(() => {
    const chosen = this.batchFilter();
    if (chosen === EVERY_BATCH) return 'All batches';
    return chosen;
  });

  readonly domainFilterLabel = computed(() => {
    const chosen = DOMAIN_FILTERS.find((option) => option.key === this.domainFilter());
    if (chosen === undefined) return DOMAIN_FILTERS[0].label;
    return chosen.label;
  });

  readonly submittedFilterLabel = computed(() => {
    const chosen = SUBMITTED_WINDOWS.find((window) => window.key === this.submittedFilter());
    if (chosen === undefined) return SUBMITTED_WINDOWS[0].label;
    return chosen.label;
  });

  readonly isLoading = computed(() => this.applications() === null);

  /** What the queue says when the rule engine routed everything itself. Derived
   *  from the rules that are actually on record — never a sentence about a
   *  domain or a USN pattern this deployment may not have. */
  readonly autoApproveLine = computed(() => {
    // A FAILED READ IS NOT A FACT. `loadSeatingRules` sets an empty list on a
    // failure so the drawer can render its error, and the sentence below would
    // then read "No seating rule auto-approves" — a claim about a rule set this
    // screen never saw, printed where the board puts a fact. Silence instead;
    // the drawer carries the error in words.
    if (this.seatingRulesError() !== null) return null;
    const rules = this.seatingRules();
    if (rules === null) return null;
    const automatic = rules.filter((rule) => rule.enabled && rule.autoApprove);
    if (automatic.length === 0) {
      return 'No seating rule auto-approves, so every application waits here for a decision.';
    }
    const counted = plural(automatic.length, 'rule auto-approves', 'rules auto-approve');
    return `${counted}; everything else waits here for a decision.`;
  });

  // ==================================================== what a decision hits ==

  /** Ticked rows win; with nothing ticked, the row the reviewer clicked. */
  readonly decisionTargets = computed<QueueRow[]>(() => {
    const ticked = this.selectedRows();
    if (ticked.length > 0) return ticked;
    const focusedId = this.focusedRegistrationId();
    if (focusedId === null) return [];
    const focused = this.visibleRows().find((row) => row.registrationId === focusedId);
    if (focused === undefined) return [];
    return [focused];
  });

  readonly hasDecisionTarget = computed(() => this.decisionTargets().length > 0);
  readonly isReviewingOne = computed(() => this.decisionTargets().length === 1);
  readonly reviewedApplication = computed<QueueRow | null>(() => {
    const targets = this.decisionTargets();
    if (targets.length !== 1) return null;
    return targets[0];
  });

  readonly decisionSummary = computed(() => plural(this.decisionTargets().length, 'application'));

  readonly selectedCount = computed(() => this.selectedRows().length);

  readonly approveButtonLabel = computed(() => {
    const count = this.decisionTargets().length;
    if (count <= 1) return 'Approve & invite';
    return `Approve & invite ${count}`;
  });

  /** The panel's checklist: THE SERVER'S CHECKS FIRST, then the two this
   *  screen still answers for itself.
   *
   *  The rule engine's verdict, the college domain fence, whose account this
   *  address already is and the USN all come from `checks[]` (B11.1). They are
   *  not recomputed here and must not be: three of the four are guards
   *  `_provision_student` will apply when Approve is pressed, and a second
   *  reading of any of them disagrees with the button the first time the
   *  server's rule changes — showing as a green line above a request that
   *  refuses. The client's guess at the rule check was exactly that and is
   *  gone.
   *
   *  The batch and the two document lines stay client-side because the server
   *  does not answer them and the payload already does: neither is a guard, and
   *  neither can contradict a decision. */
  readonly checks = computed<ApplicationCheck[]>(() => {
    const reviewed = this.reviewedApplication();
    if (reviewed === null) return [];
    const application = reviewed.application;
    // NULL MEANS NOT COMPUTED, so the whole checklist goes rather than the
    // server's half of it. Every line here is written in the future tense about
    // a decision — "Approving seats them in…", "it moves onto the student's
    // uploads when this application is approved" — and on the Rejected tab that
    // decision was taken weeks ago. A partial checklist under a decided
    // application reads as advice; it is archaeology.
    if (application.checks === null) return [];
    const canOpenRecords = (this.auth.session()?.capabilities ?? []).includes(
      'admin.student_records',
    );
    const checks: ApplicationCheck[] = application.checks.map((check) => ({
      headline: check.label,
      detail: check.detail,
      tone: CHECK_TONES[check.status].tone,
      icon: CHECK_TONES[check.status].icon,
      recordLink: canOpenRecords && check.student_id ? `/admin/students/${check.student_id}` : null,
    }));
    checks.push(this.batchCheckFor(application, reviewed.batchLabel));
    checks.push(this.documentCheckFor(application, 'CV', 'CV'));
    checks.push(this.documentCheckFor(application, 'PHOTO', 'Photo'));
    return checks;
  });

  /** Whether anything on this application says Approve will refuse it. Drawn as
   *  a line above the buttons rather than used to disable them: the reviewer can
   *  still reject, and a control that vanishes explains nothing. */
  readonly blockingChecks = computed<ApplicationCheck[]>(() =>
    this.checks().filter((check) => check.tone === 'risk'),
  );

  /** The attachments this application actually carries, as links.
   *
   *  DRAWN FROM `documents[]` AND FROM NOTHING ELSE, which is the same array
   *  the chip and the two check lines are drawn from — so "Photo only" and an
   *  "Open CV" button can never appear together, and a link is never offered
   *  for a file the endpoint would answer 404 for. That matters more than it
   *  looks: an "Open photo" that 404s reads to the office as a broken screen,
   *  where an application that simply attached no photo is a fact the check
   *  line below already states in words.
   *
   *  It also means the links disappear on an APPROVED row without a branch
   *  here. `_move_documents_to_uploads` deletes the `registration_documents`
   *  row as it hands the bytes to the student's uploads, and `_doc_kinds` reads
   *  that table live, so the payload stops listing the kind at the same moment
   *  the endpoint stops serving it. A REJECTED application keeps its documents
   *  on purpose (`purge_rejected_documents` is what eventually destroys them),
   *  so they stay openable — which is what the Rejected tab's Undo needs, since
   *  the reviewer reopening a refusal is the person most likely to want a
   *  second look at the file. */
  readonly openableDocuments = computed<OpenableDocument[]>(() => {
    const reviewed = this.reviewedApplication();
    if (reviewed === null) return [];
    const attached = new Set(reviewed.application.documents);
    return OPENABLE_DOCUMENTS.filter((kind) => attached.has(kind.kind)).map((kind) => ({
      kind: kind.kind,
      label: kind.label,
      // Same-origin, so the httpOnly `reep_session` cookie rides along on the
      // browser's own navigation with nothing for this component to attach —
      // the idiom every other file link in this app uses
      // (`verifications.component.ts`'s `fileUrl`, `uploads.component.ts`'s).
      href: `${environment.apiBase}/register/${reviewed.registrationId}/documents/${kind.segment}/file`,
    }));
  });

  private batchCheckFor(application: RegistrationApiRow, batchLabel: string): ApplicationCheck {
    const asked = application.requested_batch;
    if (application.cohort_id === null) {
      return {
        headline: 'No batch on this application',
        detail:
          asked === null
            ? 'The applicant named no batch. Approving seats them unseated; Students & batches can move them.'
            : `The applicant asked for ${asked}, and no rule confirmed it.`,
        tone: 'warn',
        icon: 'warning',
      };
    }
    if (asked !== null && asked !== batchLabel) {
      return {
        headline: `Approving seats them in ${batchLabel}`,
        detail: `The applicant asked for ${asked}; the rule's batch wins.`,
        tone: 'warn',
        icon: 'warning',
      };
    }
    return {
      headline: `Approving seats them in ${batchLabel}`,
      detail: 'The batch the rule engine chose when the form was submitted.',
      tone: 'good',
      icon: 'check_circle',
    };
  }

  private documentCheckFor(
    application: RegistrationApiRow,
    kind: string,
    label: string,
  ): ApplicationCheck {
    const attached = application.documents.includes(kind);
    if (attached) {
      return {
        headline: `${label} attached`,
        detail: `It moves onto the student's own uploads when this application is approved.`,
        tone: 'good',
        icon: 'check_circle',
      };
    }
    return {
      headline: `No ${label.toLowerCase()} attached`,
      detail: 'The student can upload one themselves after onboarding.',
      tone: 'warn',
      icon: 'warning',
    };
  }

  // ======================================================= grid options ====

  readonly gridTheme = computed(() => {
    if (this.isCompact()) return reepGridThemeCompact;
    return reepGridTheme;
  });

  readonly rowHeight = computed(() => {
    if (this.isCompact()) return COMPACT_ROW_HEIGHT_PX;
    return COMFORTABLE_ROW_HEIGHT_PX;
  });

  readonly rowSelection: RowSelectionOptions<QueueRow> = {
    mode: 'multiRow',
    checkboxes: true,
    headerCheckbox: true,
    // Ticking is a deliberate act: the toolbar decides on the ticked rows, so a
    // click meant to READ an application must not add it to a bulk approval.
    enableClickSelection: false,
  };

  /** The tick column travels with the pinned Applicant column rather than
   *  scrolling away from the row it selects. */
  readonly selectionColumn: SelectionColumnDef = { pinned: 'left', width: 44 };

  readonly defaultColumn: ColDef<QueueRow> = {
    sortable: true,
    resizable: true,
    filter: true,
    floatingFilter: true,
  };

  readonly columns: ColDef<QueueRow>[] = [
    {
      colId: 'applicant',
      field: 'name',
      headerName: 'Applicant',
      pinned: 'left',
      minWidth: 240,
      flex: 1.4,
      cellStyle: { display: 'flex', alignItems: 'center', gap: '8px' },
      cellRenderer: renderApplicantCell,
      // THE EMAIL IS DRAWN BY A RENDERER, AND THE QUICK FILTER CANNOT SEE ONE.
      // AG Grid matches against column VALUES, and this column's value is the
      // name; the address only exists inside the renderer's HTML. So typing an
      // applicant's address into a box whose label promises "name, email or
      // USN" found nothing at all. This is what the filter reads instead.
      getQuickFilterText: quickFilterApplicant,
    },
    {
      colId: 'usn',
      field: 'usn',
      headerName: 'USN',
      minWidth: 160,
      cellRenderer: renderUsnCell,
      headerTooltip: 'The USN the applicant typed on the public form',
    },
    {
      colId: 'emailDomain',
      field: 'emailDomain',
      headerName: 'Email domain',
      minWidth: 170,
      cellRenderer: renderEmailDomainCell,
      headerTooltip:
        'The domain the onboarding link goes to, coloured by the server’s own check: red is an address Approve refuses because it is not on this college’s registered domains.',
    },
    {
      colId: 'submitted',
      field: 'submittedLabel',
      headerName: 'Submitted',
      minWidth: 130,
      valueFormatter: formatSubmitted,
      comparator: compareSubmitted,
    },
    {
      colId: 'documents',
      field: 'documentsLabel',
      headerName: 'Documents',
      minWidth: 150,
      cellRenderer: renderDocumentsCell,
      headerTooltip: 'What the applicant attached: a CV, a photo, both or neither',
    },
    {
      colId: 'rule',
      field: 'ruleLabel',
      headerName: 'Rule',
      minWidth: 160,
      cellRenderer: renderRuleCell,
      headerTooltip:
        'Whether a seating rule routed this application. Open a row for the full checklist — the rule’s verdict, the domain, the account and the USN.',
    },
  ];

  /** What the Columns popover lists. The selection column is the grid's own. */
  readonly toggleableColumns = this.columns.map((column) => ({
    id: column.colId ?? '',
    label: column.headerName ?? '',
  }));

  readonly rowId = (params: GetRowIdParams<QueueRow>): string => params.data.registrationId;

  /** THE EMPTY TAB SAYS WHICH EMPTY IT IS. "No applications waiting" under the
   *  Rejected tab would read as a claim about rejections; "nothing matches this
   *  filter" over an unfiltered empty list reads as a broken screen. The two
   *  cases are told apart by whether anything was loaded at all. */
  readonly emptyOverlay = computed(() => {
    const empty = {
      PENDING_REVIEW: 'No applications waiting.',
      AUTO_APPROVED: 'No application has been auto-approved by a seating rule.',
      HOLD: 'No application is on hold.',
      REJECTED: 'No application has been rejected.',
    }[this.activeTab()];
    if (this.waitingCount() === 0) {
      return `<span class="ag-overlay-no-rows-center">${empty}</span>`;
    }
    return '<span class="ag-overlay-no-rows-center">No registration matches this filter.</span>';
  });

  // ---- the grid's events -------------------------------------------------

  onGridReady(event: GridReadyEvent<QueueRow>): void {
    this.gridApi = event.api;
    this.readPaginationState();
  }

  onSelectionChanged(): void {
    if (this.gridApi === null) return;
    this.selectedRows.set(this.gridApi.getSelectedRows());
    this.noteError.set(null);
  }

  onCellClicked(event: CellClickedEvent<QueueRow>): void {
    if (event.data === undefined) return;
    this.focusedRegistrationId.set(event.data.registrationId);
    this.noteError.set(null);
  }

  onPaginationChanged(): void {
    this.readPaginationState();
  }

  private readPaginationState(): void {
    if (this.gridApi === null) return;
    this.filteredRowCount.set(this.gridApi.paginationGetRowCount());
    this.currentPage.set(this.gridApi.paginationGetCurrentPage());
    this.totalPages.set(this.gridApi.paginationGetTotalPages());
  }

  readonly isFirstPage = computed(() => this.currentPage() === 0);
  readonly isLastPage = computed(() => this.currentPage() >= this.totalPages() - 1);

  readonly rowRangeLabel = computed(() => {
    const total = this.filteredRowCount();
    if (total === 0) return '0 of 0';
    const first = this.currentPage() * this.pageSize() + 1;
    const last = Math.min(total, (this.currentPage() + 1) * this.pageSize());
    return `${first} to ${last} of ${total}`;
  });

  readonly pagerLabel = computed(() => `Page ${this.currentPage() + 1} of ${Math.max(1, this.totalPages())}`);

  goToPreviousPage(): void {
    this.gridApi?.paginationGoToPreviousPage();
  }

  goToNextPage(): void {
    this.gridApi?.paginationGoToNextPage();
  }

  setPageSize(size: number): void {
    this.pageSize.set(size);
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

  /**
   * The board's Export: the rows in view, as the reviewer filtered them. No
   * endpoint is involved — the grid writes the file.
   *
   * `processCellCallback` is the hook where a cell is serialised, so it is
   * where the formula guard belongs — one place, every column, rather than a
   * rule each row builder has to remember. `formatValue` is called first
   * because the callback path hands over the RAW value: without it the
   * Submitted column would export past its own `valueFormatter`.
   */
  exportVisibleRows(): void {
    this.gridApi?.exportDataAsCsv({
      fileName: 'reep-registrations.csv',
      processCellCallback: (params: ProcessCellForExportParams<QueueRow>): string =>
        csvCellAsText(params.formatValue(params.value) ?? ''),
    });
  }

  // ------- the plain inputs the template binds --------------------------

  onQuickFilterInput(event: Event): void {
    this.quickFilter.set(this.inputValue(event));
  }

  onDecisionNoteInput(event: Event): void {
    this.decisionNote.set(this.inputValue(event));
    this.noteError.set(null);
  }

  setCollegeFilter(value: string): void {
    this.collegeFilter.set(value);
  }

  setBatchFilter(value: string): void {
    this.batchFilter.set(value);
  }

  setDomainFilter(value: string): void {
    this.domainFilter.set(value);
  }

  setSubmittedFilter(value: string): void {
    this.submittedFilter.set(value);
  }

  inputValue(event: Event): string {
    const input = event.target as HTMLInputElement | HTMLTextAreaElement;
    return input.value;
  }

  selectValue(event: Event): string {
    const select = event.target as HTMLSelectElement;
    return select.value;
  }

  numberValue(event: Event): number {
    const select = event.target as HTMLSelectElement;
    return Number(select.value);
  }

  clearReview(): void {
    this.focusedRegistrationId.set(null);
    this.noteError.set(null);
    this.gridApi?.deselectAll();
    this.selectedRows.set([]);
  }

  // ========================================================= the decisions ==

  /**
   * Approve, carrying the note if one was typed.
   *
   * `decide()` stores `body.note` as `review_note` on EVERY decision, not only
   * on a rejection (`app/routers/registration.py`), and that row is the record
   * of why this application was let through. Sending `null` here regardless
   * threw away what the reviewer had already typed into a box labelled
   * "Decision note", silently, at the moment they committed the decision.
   * Empty stays `null` rather than `''` — a note nobody wrote is not a note.
   */
  async approveTargets(): Promise<void> {
    const targets = this.decisionTargets();
    if (targets.length === 0) return;
    const note = this.decisionNote().trim();
    await this.decide(targets, 'APPROVE', note === '' ? null : note);
  }

  /**
   * Reject, with the reason the API demands.
   *
   * `POST /decision` answers 422 — `detail` as a LIST, which is why `detailOf`
   * exists — when REJECT arrives without a note, and a rejected applicant is
   * not a user, so that note is the only channel the product has to them. The
   * screen refuses first, in the same words the server uses.
   */
  async rejectTargets(): Promise<void> {
    const targets = this.decisionTargets();
    if (targets.length === 0) return;
    const reason = this.decisionNote().trim();
    if (reason === '') {
      this.noteError.set('A reason is required when rejecting an application.');
      return;
    }
    await this.decide(targets, 'REJECT', reason);
  }

  private async decide(
    targets: QueueRow[],
    decision: 'APPROVE' | 'REJECT',
    note: string | null,
  ): Promise<void> {
    this.deciding.set(true);
    this.error.set(null);
    this.flash.set(null);
    const decided: RegistrationApiRow[] = [];
    try {
      for (const target of targets) {
        const response = await fetch(
          `${environment.apiBase}/register/${target.registrationId}/decision`,
          {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision, note }),
          },
        );
        if (!response.ok) {
          this.error.set(await detailOf(response));
          break;
        }
        decided.push(target.application);
      }
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.deciding.set(false);
    }
    if (decided.length === 0) return;
    this.dropDecidedRows(decided);
    this.flash.set(this.decisionLineFor(decided, decision));
    if (decision === 'REJECT') this.lastRejected.set(decided);
    else this.lastRejected.set([]);
  }

  private dropDecidedRows(decided: RegistrationApiRow[]): void {
    const decidedIds = new Set(decided.map((application) => application.id));
    this.applications.update((loaded) =>
      (loaded ?? []).filter((application) => !decidedIds.has(application.id)),
    );
    this.decisionNote.set('');
    this.noteError.set(null);
    this.focusedRegistrationId.set(null);
    this.gridApi?.deselectAll();
    this.selectedRows.set([]);
  }

  /**
   * What the reviewer is told, and it is no longer wrong.
   *
   * Approval provisions the account and emails the onboarding link — three
   * steps on one URL that end at the ordinary sign-in (AGENTS.md). The old
   * line said the student should "sign in with Google", which is a different
   * door from the one the mail actually opens.
   */
  private decisionLineFor(decided: RegistrationApiRow[], decision: 'APPROVE' | 'REJECT'): string {
    const who =
      decided.length <= 3
        ? decided.map((application) => application.name).join(', ')
        : `${decided.length} applicants`;
    if (decision === 'APPROVE') {
      return `${who} approved — the account is created and the onboarding link is on its way to their college email.`;
    }
    return `${who} rejected — your reason has been emailed to them.`;
  }

  /** Open the USN box on one application, with its USN typed in. */
  startUsnCorrection(application: RegistrationApiRow): void {
    this.usnEditingId.set(application.id);
    this.usnDraft.set(application.usn ?? '');
    this.usnError.set(null);
    afterNextRender(() => document.getElementById('usn-correction')?.focus(), {
      injector: this.injector,
    });
  }

  cancelUsnCorrection(): void {
    this.usnEditingId.set(null);
    this.usnError.set(null);
  }

  onUsnInput(event: Event): void {
    this.usnDraft.set((event.target as HTMLInputElement).value);
  }

  /**
   * Correct the USN on an application waiting for a decision (2026-10-16).
   *
   * `PATCH /register/{id}`. A USN another student held used to leave Reject as
   * the only button, though the checklist and the 409 both said to correct it.
   * The answer is the row WITH its checklist recomputed, so it replaces the
   * row in place and the panel shows the new verdict at once. A USN that is
   * also taken is a 409 whose sentence is shown under the box.
   */
  async saveUsnCorrection(application: RegistrationApiRow): Promise<void> {
    const usn = this.usnDraft().trim();
    if (usn === '') {
      this.usnError.set('Enter the USN.');
      return;
    }
    this.usnSaving.set(true);
    this.usnError.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/register/${application.id}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ usn }),
      });
      if (!response.ok) {
        this.usnError.set(await detailOf(response));
        return;
      }
      const corrected = (await response.json()) as RegistrationApiRow;
      this.applications.update((loaded) =>
        (loaded ?? []).map((row) => (row.id === corrected.id ? corrected : row)),
      );
      this.usnEditingId.set(null);
      // The banner Approve left is about the USN this replaced.
      this.error.set(null);
    } catch {
      this.usnError.set('Could not reach the server.');
    } finally {
      this.usnSaving.set(false);
    }
  }

  /**
   * Park the selected applications with the note the API demands (B11.2).
   *
   * THE NOTE IS REQUIRED AND THE SCREEN SAYS SO FIRST. `POST /{id}/hold` answers
   * 422 without one, because a HOLD carrying no words is indistinguishable from
   * PENDING_REVIEW on every screen here — same tab treatment, same buttons —
   * so the note is not a nicety on the feature, it IS the feature. Refusing
   * locally keeps the message the reviewer reads identical to the server's.
   *
   * NOTHING IS MAILED. A hold is a note to colleagues, not a notice to the
   * applicant: the applicant's result card is unchanged and their
   * `decision_reason` is untouched. The copy under the buttons says this,
   * because a reviewer who believes the applicant has been told will not tell
   * them.
   */
  async holdTargets(): Promise<void> {
    const targets = this.decisionTargets();
    if (targets.length === 0) return;
    const note = this.decisionNote().trim();
    if (note === '') {
      this.noteError.set('A note is required when holding an application — say what it is waiting on.');
      return;
    }
    await this.runOnEach(targets, 'hold', { note }, (held) =>
      held.length === 1
        ? `${held[0].name} is on hold — your note is on the application, and nothing was sent to them.`
        : `${held.length} applications are on hold. Nothing was sent to the applicants.`,
    );
  }

  /**
   * Put the selected applications back in the queue — the Held tab's release
   * and the Rejected tab's undo, through one endpoint.
   *
   * ONE VERB, ONE MEANING. `POST /{id}/reopen` is "back in the queue, waiting
   * for a decision", which is exactly what releasing a hold is and exactly what
   * undoing a rejection is. A second route doing the same thing to a different
   * status is how the two drift — and the half that drifts is the stamp, not
   * the status: a released hold whose `held_by_id` survived would draw a "held
   * by" on a row sitting in Pending.
   */
  async reopenTargets(): Promise<void> {
    const targets = this.decisionTargets();
    if (targets.length === 0) return;
    const released = this.activeTab() === 'HOLD';
    await this.runOnEach(targets, 'reopen', null, (rows) => {
      const what = released ? 'released' : 'reopened';
      return rows.length === 1
        ? `${rows[0].name} ${what} — back in the pending queue.`
        : `${rows.length} applications ${what} — back in the pending queue.`;
    });
  }

  /**
   * One POST per application, stopping at the first refusal.
   *
   * BULK IS THE LOOP, deliberately, and it stops rather than pressing on: there
   * is no bulk endpoint, and a run that swallowed one 409 to finish the rest
   * would leave the reviewer with a success notice and no idea which row did
   * not take. The rows that DID land leave this tab, because they no longer
   * belong to it.
   */
  private async runOnEach(
    targets: QueueRow[],
    path: 'hold' | 'reopen',
    body: object | null,
    line: (done: RegistrationApiRow[]) => string,
  ): Promise<void> {
    this.deciding.set(true);
    this.error.set(null);
    this.flash.set(null);
    const done: RegistrationApiRow[] = [];
    try {
      for (const target of targets) {
        const response = await fetch(
          `${environment.apiBase}/register/${target.registrationId}/${path}`,
          {
            method: 'POST',
            credentials: 'include',
            ...(body === null
              ? {}
              : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
          },
        );
        if (!response.ok) {
          this.error.set(await detailOf(response));
          break;
        }
        done.push((await response.json()) as RegistrationApiRow);
      }
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.deciding.set(false);
    }
    if (done.length === 0) return;
    this.dropDecidedRows(done);
    this.lastRejected.set([]);
    this.flash.set(line(done));
  }

  /** Reopen the rejections the last action made. One request per row; rows that
   *  reopen return to the queue. */
  async undoLastRejection(): Promise<void> {
    const rejected = this.lastRejected();
    if (rejected.length === 0 || this.undoing()) return;
    this.undoing.set(true);
    this.error.set(null);
    const reopened: RegistrationApiRow[] = [];
    try {
      for (const application of rejected) {
        const response = await fetch(
          `${environment.apiBase}/register/${application.id}/reopen`,
          { method: 'POST', credentials: 'include' },
        );
        if (!response.ok) {
          this.error.set(await detailOf(response));
          break;
        }
        reopened.push((await response.json()) as RegistrationApiRow);
      }
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.undoing.set(false);
    }
    if (reopened.length === 0) return;
    // Back onto the list only where a pending application belongs. Undoing a
    // rejection made from the HELD tab reopens the row to PENDING_REVIEW, so
    // putting it back here would draw a pending row under a heading that says
    // Held — the row is simply gone from this tab, correctly.
    if (this.activeTab() === 'PENDING_REVIEW') {
      this.applications.update((loaded) => [...reopened, ...(loaded ?? [])]);
    }
    this.lastRejected.set([]);
    this.flash.set(
      reopened.length === 1
        ? `${reopened[0].name} is back in the queue.`
        : `${reopened.length} applications are back in the queue.`,
    );
  }

  // =========================================================== seating rules ==

  openSeatingRules(): void {
    this.seatingRulesOpen.set(true);
    if (this.seatingRules() === null) void this.loadSeatingRules();
  }

  closeSeatingRules(): void {
    this.seatingRulesOpen.set(false);
    this.closeRuleForm();
    this.ruleFlash.set(null);
  }

  /** The batches a rule can seat into, from `/register/hierarchy` — the same
   *  read the queue's Batch column uses, so a rule cannot name a batch this
   *  screen would then fail to label. */
  readonly ruleBatchOptions = computed(() =>
    [...this.batchNames().entries()]
      .map(([id, label]) => ({ id, label }))
      .sort((a, b) => a.label.localeCompare(b.label)),
  );

  newRule(): void {
    this.ruleFlash.set(null);
    this.editingRuleId.set(null);
    this.ruleDraft.set(EMPTY_RULE_DRAFT);
    this.ruleFormError.set(null);
    this.ruleFormOpen.set(true);
  }

  editRule(rule: SeatingRuleLine): void {
    this.ruleFlash.set(null);
    this.editingRuleId.set(rule.id);
    this.ruleDraft.set({
      name: rule.name,
      priority: String(rule.priority),
      emailDomain: rule.emailDomain ?? '',
      usnPattern: rule.usnPattern ?? '',
      degreeLevel: rule.degreeLevel ?? '',
      cohortId: rule.cohortId ?? '',
      autoApprove: rule.autoApprove,
      enabled: rule.enabled,
    });
    this.ruleFormError.set(null);
    this.ruleFormOpen.set(true);
  }

  closeRuleForm(): void {
    this.ruleFormOpen.set(false);
    this.editingRuleId.set(null);
    this.ruleFormError.set(null);
  }

  onRuleField(field: 'name' | 'priority' | 'emailDomain' | 'usnPattern', event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.ruleDraft.update((draft) => ({ ...draft, [field]: value }));
  }

  onRuleChoice(field: 'degreeLevel' | 'cohortId', value: string): void {
    this.ruleDraft.update((draft) => ({ ...draft, [field]: value }));
  }

  onRuleSwitch(field: 'autoApprove' | 'enabled', event: Event): void {
    const checked = (event.target as HTMLInputElement).checked;
    this.ruleDraft.update((draft) => ({ ...draft, [field]: checked }));
  }

  /**
   * Write the rule — POST when it is new, PATCH when it is not.
   *
   * PATCH SENDS EVERY FIELD, on purpose. This is a whole-rule editor, so the
   * draft holds the complete rule, and an explicit `null` is how a condition is
   * CLEARED (the server distinguishes absent from null for exactly this). Sending
   * only what changed would need a diff against the loaded row, and a diff that
   * misses a cleared field leaves a condition the author believes they removed.
   *
   * THE THREE LOCAL REFUSALS ARE THE SERVER'S OWN SENTENCES. A rule needs a
   * name, a priority is a whole number, and an auto-approving rule with no
   * condition is an open door — all three are 422s from the API, said here first
   * so the author is not round-tripped to be told. Anything else the server
   * refuses is printed exactly as it wrote it, which is the whole point of
   * `detailOf`: `validate_usn_pattern`'s message names the actual problem with
   * the pattern and no client-side guess could.
   */
  async saveRule(): Promise<void> {
    const draft = this.ruleDraft();
    const name = draft.name.trim();
    const emailDomain = draft.emailDomain.trim();
    const usnPattern = draft.usnPattern.trim();
    if (name === '') {
      this.ruleFormError.set(
        'A rule needs a name — it is what the queue shows beside every application it routes.',
      );
      return;
    }
    const priority = Number(draft.priority);
    if (!Number.isInteger(priority) || priority < 0) {
      this.ruleFormError.set('Priority is a whole number, 0 or more. The lowest one wins.');
      return;
    }
    if (draft.autoApprove && emailDomain === '' && usnPattern === '' && draft.degreeLevel === '') {
      this.ruleFormError.set(
        'A rule that auto-approves with no conditions would provision an account for every ' +
          'application ever submitted. Name at least one condition — the email domain is the ' +
          'usual one — or leave auto-approve off.',
      );
      return;
    }

    const editing = this.editingRuleId();
    const body = {
      name,
      enabled: draft.enabled,
      email_domain: emailDomain === '' ? null : emailDomain,
      usn_pattern: usnPattern === '' ? null : usnPattern,
      degree_level: draft.degreeLevel === '' ? null : draft.degreeLevel,
      cohort_id: draft.cohortId === '' ? null : draft.cohortId,
      auto_approve: draft.autoApprove,
      priority,
    };

    this.ruleSaving.set(true);
    this.ruleFormError.set(null);
    try {
      const response = await fetch(
        editing === null
          ? `${environment.apiBase}/register/rules`
          : `${environment.apiBase}/register/rules/${editing}`,
        {
          method: editing === null ? 'POST' : 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
      );
      if (!response.ok) {
        this.ruleFormError.set(await detailOf(response));
        return;
      }
      this.closeRuleForm();
      this.ruleFlash.set(editing === null ? `Rule '${name}' is live.` : `Rule '${name}' saved.`);
      await this.loadSeatingRules();
    } catch {
      this.ruleFormError.set('Could not reach the server.');
    } finally {
      this.ruleSaving.set(false);
    }
  }

  /** The reversible one. A disabled rule stops firing, stays on this table where
   *  somebody can see why last month's intake behaved as it did, and keeps every
   *  `matched_rule_id` pointing at it. */
  async toggleRuleEnabled(rule: SeatingRuleLine): Promise<void> {
    await this.writeRule(rule, { enabled: !rule.enabled }, 'PATCH', `Rule '${rule.name}' is now ${rule.enabled ? 'off' : 'on'}.`);
  }

  /**
   * Delete, after saying what it costs.
   *
   * `registrations.matched_rule_id` is ON DELETE SET NULL, so every application
   * this rule routed loses the pointer that said so and the queue's Rule column
   * reads "Needs a decision" on rows a rule certainly did match. The audit trail
   * keeps the rule's whole text; this table will not.
   */
  async deleteRule(rule: SeatingRuleLine): Promise<void> {
    const agreed = window.confirm(
      `Delete the rule '${rule.name}'?\n\n` +
        'Applications it already routed will stop showing which rule routed them — that pointer ' +
        'is cleared by the database, not kept. Disable the rule instead if you only want it to ' +
        'stop firing.',
    );
    if (!agreed) return;
    await this.writeRule(rule, null, 'DELETE', `Rule '${rule.name}' is gone.`);
  }

  private async writeRule(
    rule: SeatingRuleLine,
    body: object | null,
    method: 'PATCH' | 'DELETE',
    flash: string,
  ): Promise<void> {
    this.ruleBusyId.set(rule.id);
    this.seatingRulesError.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/register/rules/${rule.id}`, {
        method,
        credentials: 'include',
        ...(body === null
          ? {}
          : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        this.seatingRulesError.set(await detailOf(response));
        return;
      }
      this.ruleFlash.set(flash);
      await this.loadSeatingRules();
    } catch {
      this.seatingRulesError.set('Could not reach the server.');
    } finally {
      this.ruleBusyId.set(null);
    }
  }

  // =============================================================== loading ==

  private async loadQueue(): Promise<void> {
    const tab = this.activeTab();
    // NO QUERY STRING ON THE PENDING TAB. See QUEUE_TABS: the parameterless
    // call is the one that answers the whole unbounded pending list, and this
    // screen must not be the thing that caps it.
    const url =
      tab === 'PENDING_REVIEW'
        ? `${environment.apiBase}/register/pending`
        : `${environment.apiBase}/register/pending?status=${tab}&limit=${DECIDED_TAB_PAGE}`;
    try {
      const response = await fetch(url, { credentials: 'include' });
      if (!response.ok) {
        this.error.set('Could not load applications.');
        this.applications.set([]);
        return;
      }
      // A tab switched while this was in flight would otherwise land the old
      // tab's rows under the new tab's heading.
      if (this.activeTab() !== tab) return;
      this.applications.set((await response.json()) as RegistrationApiRow[]);
    } catch {
      this.error.set('Could not reach the server.');
      this.applications.set([]);
    }
  }

  /** Batch names for the queue and the College filter. A nicety: the queue
   *  still reads without them, so a failure here is not the screen's error. */
  private async loadBatchNames(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/register/hierarchy`, {
        credentials: 'include',
      });
      if (!response.ok) return;
      const body = (await response.json()) as { colleges: HierarchyCollege[] };
      const named = new Map<string, string>();
      for (const college of body.colleges) {
        for (const department of college.departments) {
          for (const batch of department.batches) {
            named.set(batch.id, batch.display_label);
          }
        }
      }
      this.batchNames.set(named);
    } catch {
      /* the queue still reads without the batch names */
    }
  }

  private async loadSeatingRules(): Promise<void> {
    // CLEARED HERE, not only set. A failed Delete leaves its refusal on screen,
    // and the reload that follows would otherwise draw that sentence over a
    // table it has just read correctly.
    this.seatingRulesError.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/register/rules`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.seatingRulesError.set('Could not load the rules.');
        this.seatingRuleRows.set([]);
        return;
      }
      // THE LIST IS NARROWED THE SAME WAY THE WRITES ARE (decision 3), and the
      // header is how this screen can say which of the two empty tables it is
      // looking at: "no rules exist" and "no rules you can see" are opposite
      // facts. `programme` means the whole rule set.
      this.rulesScope.set(response.headers.get('X-Reep-Scope'));
      this.seatingRuleRows.set((await response.json()) as SeatingRuleApiRow[]);
    } catch {
      this.seatingRulesError.set('Could not reach the server.');
      this.seatingRuleRows.set([]);
    }
  }

  private toSeatingRuleLine(rule: SeatingRuleApiRow): SeatingRuleLine {
    const conditions: string[] = [];
    if (rule.email_domain !== null) conditions.push(`email on ${rule.email_domain}`);
    if (rule.usn_pattern !== null) conditions.push(`USN like ${rule.usn_pattern}`);
    if (rule.degree_level !== null) conditions.push(rule.degree_level);
    return {
      id: rule.id,
      name: rule.name,
      priority: rule.priority,
      enabled: rule.enabled,
      autoApprove: rule.auto_approve,
      conditions: conditions.length === 0 ? 'Matches every application' : conditions.join(' · '),
      seatsIn: this.seatLabelFor(rule.cohort_id),
      createdAt: rule.created_at,
      emailDomain: rule.email_domain,
      usnPattern: rule.usn_pattern,
      degreeLevel: rule.degree_level,
      cohortId: rule.cohort_id,
    };
  }

  private seatLabelFor(cohortId: string | null): string {
    if (cohortId === null) return 'No batch';
    const named = this.batchNames().get(cohortId);
    // Not "a batch this deployment no longer lists": the hierarchy read can
    // simply have failed, and this screen cannot tell that from a deletion.
    if (named === undefined) return `Batch ${cohortId.slice(0, 8)}`;
    return named;
  }
}

/**
 * The refusal the server wrote, not a generic one.
 *
 * FastAPI answers a schema error with `detail` as a LIST of objects, so
 * `body?.detail` renders "[object Object]" — the trap leave.component.ts hit
 * when `LeaveIn` grew a date check. Same helper as governance.component.ts's
 * `detailOf`; the sentences worth showing here name specifics ("A reason is
 * required when rejecting an application.").
 */
async function detailOf(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === 'string') {
      return String(detail[0].msg).replace(/^Value error,\s*/, '');
    }
  } catch {
    /* fall through to the status */
  }
  return `Could not record the decision (${response.status}).`;
}
