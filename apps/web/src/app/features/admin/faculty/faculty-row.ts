/**
 * The Faculty directory's row: what the API says, and what the grid draws.
 *
 * TWO ENDPOINTS MAKE ONE ROW. `GET /api/admin/faculty` is the directory — every
 * MENTOR-role account, its designation, its free-text department line and the
 * department it is FILED in, resolved through `users.department_id`.
 * `GET /api/admin/mentor-load` is the only thing that can answer "is this
 * person actually a mentor, and of how many students": a faculty account is not
 * a mentor by existing, it becomes one when the office assigns it a student
 * (AGENTS.md), and `mentor_id` is null until that happens.
 *
 * The second read needs `admin.analytics` while this screen needs
 * `admin.mentors`, so a faculty member who was granted only this screen can be
 * refused it. That is why `holdsMentorGroup` and `menteeCount` are NULLABLE and
 * why the screen says "not readable here" rather than drawing a confident zero:
 * "nobody has assigned them a student" and "this account may not read the
 * assignment list" are opposite facts and must not share a rendering.
 */

/** A cell nothing can answer: a refused read, or a column whose fact no
 *  endpoint reports. Never a confident zero and never a blank. */
export const NOT_READABLE = '—';

export const PAGE_SIZES = [10, 25, 50] as const;
export const DEFAULT_PAGE_SIZE = 10;

export const COMFORTABLE_ROW_HEIGHT_PX = 40;
export const COMPACT_ROW_HEIGHT_PX = 36;

/** Columns the Columns popover starts with UNTICKED. Empty on purpose: the
 *  board's six fit beside the 380px drawer (01-design-system.md §3 allows six
 *  with a panel open), so nothing needs hiding to make room. */
export const INITIALLY_HIDDEN_COLUMN_IDS: string[] = [];

// ------------------------------------------------------ what the API says --

/** `StaffPlacementOut` — where a faculty member is filed, read through the
 *  join and stored on nothing. `filed` is what a client branches on: a falsy
 *  `department_name` cannot tell "nobody has filed this person" apart from "a
 *  department whose name is blank". */
export interface StaffPlacementApi {
  filed: boolean;
  department_id: string | null;
  department_code: string | null;
  department_name: string | null;
  college_id: string | null;
  college_code: string | null;
  college_name: string | null;
}

/** `AdminFacultyRowOut` from `GET /api/admin/faculty`. */
export interface FacultyApiRow {
  user_id: string;
  name: string;
  email: string;
  designation: string | null;
  department: string | null;
  placement: StaffPlacementApi;
  /** B3.3. Null for an account that signs in as usual; a timestamp for one the
   *  office has offboarded. THE ACCOUNT STATE IS THIS FIELD — the 90-day
   *  re-enable window is measured from it, which is why it is a moment and not
   *  a boolean, and why the screen branches on it rather than on a flag it
   *  derives once and then has to keep in step. */
  disabled_at: string | null;
  disable_reason: string | null;
  /** REMOVED from the roster (2026-09-16): rows kept, off every list, listed
   *  only by `?removed=true` for Restore. */
  deleted_at: string | null;
  delete_reason: string | null;
}

/** `AccountStateOut` — the answer from disable, enable and sign-out-everywhere.
 *
 *  `detail` is the SERVER'S OWN SENTENCE about what it just did ("… can no
 *  longer sign in, and every device it held has been signed out"). It is shown
 *  verbatim as the confirmation: a client that composes its own version of that
 *  sentence is a second description of one act, and the two drift. */
export interface AccountStateApi {
  user_id: string;
  email: string;
  role: string;
  disabled: boolean;
  disabled_at: string | null;
  disable_reason: string | null;
  token_version: number;
  /** Live activation / reset / onboarding links killed with the account. */
  links_revoked: number;
  detail: string;
}

/** The fields this screen reads out of `GET /api/admin/mentor-load`. */
export interface MentorLoadApiRow {
  mentor_id: string | null;
  user_id: string;
  mentee_count: number;
}

/** `DepartmentPickerOut` from `GET /api/admin/departments` — every department
 *  in every college, already disambiguated into one `label`. */
export interface DepartmentPickerApiRow {
  id: string;
  code: string;
  name: string;
  college_id: string;
  college_code: string;
  college_name: string;
  label: string;
}

/** `ActivationLinkOut` from `POST /api/admin/users/{id}/activation-link`. */
export interface ActivationLinkApi {
  link: string;
  emailed: boolean;
  expires_in_hours: number;
}

// -------------------------------------------------- what the grid renders --

export interface FacultyRow {
  userId: string;
  name: string;
  email: string;
  initials: string;
  designation: string | null;
  /** The department this account is filed in, or the free-text line the leave
   *  form prints when it is filed nowhere. */
  departmentLine: string;
  departmentId: string | null;
  collegeId: string | null;
  collegeName: string | null;
  isFiled: boolean;
  /** Null when the assignment list could not be read — never false. */
  holdsMentorGroup: boolean | null;
  /** Null for the same reason. */
  menteeCount: number | null;
  /** B3.3, straight off the row: when the account was disabled, and why. */
  disabledAt: string | null;
  disableReason: string | null;
  isDisabled: boolean;
  /** Off the roster, rows kept (2026-09-16). Only ever true on the Removed list. */
  isRemoved: boolean;
  deletedAt: string | null;
  deleteReason: string | null;
}

/** A college, derived from the department picker rather than fetched again:
 *  every department carries the college it belongs to. */
export interface CollegeOption {
  id: string;
  name: string;
}

/** The four panes of the drawer the board draws. */
export type DrawerTab = 'profile' | 'functions' | 'sessions' | 'history';

/** What the Profile pane writes. B3.5 added the two identity fields, so
 *  `PATCH /api/admin/faculty/{id}` now accepts name, email, designation,
 *  department, department_id — plus the pair that lets an address off the
 *  college's domains through (B3.2).
 *
 *  CHANGING THE ADDRESS IS NOT AN ORDINARY EDIT and the draft carries the
 *  escape hatch because of it: the server fences a new address to the college's
 *  `email_domains` and refuses anything else unless BOTH `allow_external` and a
 *  written reason arrive with it. Without these two fields on the draft the
 *  refusal would name a remedy this screen has no control for. */
export interface FacultyProfileDraft {
  name: string;
  email: string;
  departmentId: string;
  designation: string;
  allowExternal: boolean;
  externalReason: string;
}

export const EMPTY_PROFILE_DRAFT: FacultyProfileDraft = {
  name: '',
  email: '',
  departmentId: '',
  designation: '',
  allowExternal: false,
  externalReason: '',
};

// ------------------------------------------------------------- helpers ----

/** AG Grid renderers return HTML strings, and a faculty member's name is
 *  somebody else's text. Everything interpolated into a renderer goes through
 *  this first. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** "12 Sep 2026" for a timestamp the server sent, and an em dash for a null —
 *  a date field that renders "Invalid Date" is how a reader learns to distrust
 *  every other date on the screen. */
export function dayLabelOf(value: string | null): string {
  if (value === null || value === '') return NOT_READABLE;
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return NOT_READABLE;
  return when.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function initialsOf(name: string): string {
  const words = name
    .trim()
    .split(/\s+/)
    .filter((word) => word.length > 0);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/** "Associate Professor · Management Studies · BGSCET", with the parts that
 *  are not on record left out rather than rendered as blanks. */
export function identityLineOf(row: FacultyRow): string {
  const parts: string[] = [];
  if (row.designation !== null) parts.push(row.designation);
  parts.push(row.departmentLine);
  if (row.collegeName !== null) parts.push(row.collegeName);
  return parts.join(' · ');
}
