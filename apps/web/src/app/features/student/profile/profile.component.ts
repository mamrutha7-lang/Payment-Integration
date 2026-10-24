/**
 * Student profile (REEP v2) — the panel behind data-p="profile".
 *
 * Renders the editable student record as v2 cards using the global reep-v2
 * classes (.card, .field, .reg-grid, .chip, .dt-btn, .dropzone …). The scalar
 * fields ProfileUpdateIn allows (phone, email, the three links, city, career
 * summary, the two interest flags and the leaderboard opt-out) are editable and
 * saved in one PUT /student/profile. Skills are synced from the Skilling page
 * and shown read-only. placement_eligible is admin-set and shown as a chip,
 * never editable.
 *
 * IDENTITY IS NOT A PREFERENCE. Name and USN are the student's identity, not
 * fields they choose: the USN comes off the roster row created before they ever
 * signed in. They arrive ALREADY FILLED IN and are rendered as readonly inputs —
 * filled, selectable, copyable, impossible to type into. (The v2 handoff shows
 * exactly these two; the Google address the session was signed in with is no
 * longer repeated here — the shell's profile block already carries it.) Nothing on this screen
 * sends them anywhere: PUT /student/profile has no `usn` or `name` field, and
 * no route in the API lets a student set their own USN (it is written only by
 * the roster seed and the registration flow), so a client that tried would be
 * ignored server-side anyway. The lock here is the honest UI for that fact.
 *
 * When the student record cannot be read, the card says so. It must never fall
 * back to a blank USN, which reads as "you have no USN" and sends the student
 * to the office over a failed fetch.
 *
 * This screen adds, over the raw form:
 *   - field-level validation (phone / email / the three links) shown inline on
 *     blur and enforced before save;
 *   - a save-state machine (unsaved → saving → saved / error) driven off a
 *     baseline snapshot of the last-loaded values;
 *   - a placement-completion meter over the fields that materially help
 *     placements;
 *   - privacy and placement-preference controls split into separate groups.
 */

import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { environment } from '../../../../environments/environment';

/** GET/PUT /student/profile — exact snake_case shape from ProfileOut. */
interface ProfileOut {
  student_id: string;
  phone: string | null;
  email: string | null;
  linkedin_url: string | null;
  github_url: string | null;
  portfolio_url: string | null;
  city: string | null;
  career_summary: string | null;
  placement_eligible: boolean;
  interested_in_jobs: boolean;
  interested_in_internships: boolean;
  education: unknown[];
  experience: unknown[];
  projects: unknown[];
  skills: unknown[];
  achievements: unknown[];
  leaderboard_opt_out: boolean;
  institution: InstitutionOut;
}

/** One row of the locked institution card, with its state. See InstitutionLevelOut. */
interface InstitutionLevel {
  key: string;
  label: string;
  value: string | null;
  state: 'set' | 'pending' | 'not_in_use';
}

/** The "verified by Main Admin" card. Every flat field nullable; `levels` is
 *  what the card renders, in the server's order with the server's labels. */
interface InstitutionOut {
  college_name: string | null;
  college_code: string | null;
  department_name: string | null;
  course_name: string | null;
  specialization_name: string | null;
  batch_label: string | null;
  entry_date: string | null;
  expected_completion: string | null;
  levels: InstitutionLevel[];
  not_in_use_note: string | null;
}

/** GET /student/dashboard — only name + usn are read here. */
interface DashboardOut {
  name: string;
  usn: string | null;
}

/** The editable scalar values, snapshotted to detect unsaved changes. */
interface Snapshot {
  phone: string;
  email: string;
  linkedinUrl: string;
  githubUrl: string;
  portfolioUrl: string;
  city: string;
  careerSummary: string;
  interestedInJobs: boolean;
  interestedInInternships: boolean;
  leaderboardOptOut: boolean;
}

/** Which editable text field a validator/inline-error belongs to. */
type FieldKey = 'phone' | 'email' | 'linkedinUrl' | 'githubUrl' | 'portfolioUrl';

// --- Field validators. Each returns null when the value is acceptable and a
// short human message otherwise. Empty is always acceptable: these fields are
// optional, so blank means "not provided", never "invalid". ---

/** Phone: digits, spaces and + only, with 7–15 actual digits. */
function validatePhone(raw: string): string | null {
  const v = raw.trim();
  if (!v) return null;
  if (!/^[+\d\s]+$/.test(v)) return 'Use only digits, spaces or a leading +.';
  const digits = v.replace(/\D/g, '').length;
  if (digits < 7 || digits > 15) return 'Enter between 7 and 15 digits.';
  return null;
}

/** Contact email: a single name@domain.tld. */
function validateEmail(raw: string): string | null {
  const v = raw.trim();
  if (!v) return null;
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v)) {
    return 'Enter a valid email, e.g. name@domain.com.';
  }
  return null;
}

/** A URL whose hostname ends with the given site (protocol optional). */
function validateSiteUrl(raw: string, site: string, label: string): string | null {
  const v = raw.trim();
  if (!v) return null;
  const withProto = /^https?:\/\//i.test(v) ? v : `https://${v}`;
  let host: string;
  try {
    host = new URL(withProto).hostname.toLowerCase();
  } catch {
    return `Enter a valid ${label} link.`;
  }
  if (host !== site && !host.endsWith(`.${site}`)) {
    return `Enter a ${site} link.`;
  }
  return null;
}

/** Any http(s) website URL with a real host. */
function validateHttpUrl(raw: string): string | null {
  const v = raw.trim();
  if (!v) return null;
  const withProto = /^https?:\/\//i.test(v) ? v : `https://${v}`;
  try {
    const u = new URL(withProto);
    if (!u.hostname.includes('.')) return 'Enter a valid website URL.';
  } catch {
    return 'Enter a valid website URL.';
  }
  return null;
}

@Component({
  selector: 'app-student-profile',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './profile.component.html',
  styleUrl: './profile.component.scss',
})
export class ProfileComponent {
  readonly loaded = signal(false);
  readonly error = signal<string | null>(null);
  readonly saving = signal(false);
  readonly saveError = signal<string | null>(null);
  readonly savedAt = signal<Date | null>(null);

  // Read-only, synced from the student record.
  readonly name = signal<string>('');
  readonly usn = signal<string | null>(null);
  readonly placementEligible = signal(false);
  readonly skills = signal<string[]>([]);

  /**
   * How the identity read went. Three outcomes that must never be conflated:
   * still in flight, read fine, could not be read. Blank is not one of them —
   * an em dash for a failed fetch reads as "you have no USN" and sends the
   * student to the office over a network blip.
   */
  readonly identityState = signal<'loading' | 'ready' | 'error'>('loading');

  /** The locked institution card. Null until the profile loads; a 404 (no
   *  profile row yet) leaves it null and the card says so. */
  readonly institution = signal<InstitutionOut | null>(null);
  /** Rows to draw: every level except the ones this institution does not use,
   *  which are named once in the footnote instead of drawn as dashes. */
  readonly institutionRows = computed(
    () => (this.institution()?.levels ?? []).filter((lv) => lv.state !== 'not_in_use'),
  );
  readonly institutionDates = computed(() => {
    const i = this.institution();
    if (!i) return null;
    return { entry: i.entry_date, done: i.expected_completion };
  });

  /** What goes in the USN box, in every state. */
  readonly usnValue = computed(() => {
    switch (this.identityState()) {
      case 'loading':
        return 'Loading…';
      case 'error':
        return 'Could not be read';
      default:
        return this.usn() ?? 'Not on your record yet';
    }
  });

  readonly nameValue = computed(() => {
    switch (this.identityState()) {
      case 'loading':
        return 'Loading…';
      case 'error':
        return 'Could not be read';
      default:
        return this.name() || 'Not on your record yet';
    }
  });

  /** True only when the record really loaded and genuinely carries no USN —
   *  the one case worth telling the student to go and ask about. */
  readonly usnMissing = computed(() => this.identityState() === 'ready' && !this.usn());

  /** True when the identity read itself failed; retryable, not a data problem. */
  readonly identityUnreadable = computed(() => this.identityState() === 'error');

  // Editable form model — signals so validation, dirty-state and the
  // completion meter all recompute the moment a field changes.
  readonly phone = signal('');
  readonly email = signal('');
  readonly linkedinUrl = signal('');
  readonly githubUrl = signal('');
  readonly portfolioUrl = signal('');
  readonly city = signal('');
  readonly careerSummary = signal('');
  readonly interestedInJobs = signal(true);
  readonly interestedInInternships = signal(true);
  readonly leaderboardOptOut = signal(false);

  /** The last-loaded / last-saved values, used to detect unsaved changes. */
  private readonly baseline = signal<Snapshot | null>(null);

  /** Fields the user has blurred; used to hold inline errors until then. */
  private readonly touched = signal<ReadonlySet<FieldKey>>(new Set());
  /** Set once Save is attempted, so every error becomes visible at once. */
  private readonly triedSave = signal(false);

  // --- Field-level validation ---------------------------------------------

  readonly errors = computed<Record<FieldKey, string | null>>(() => ({
    phone: validatePhone(this.phone()),
    email: validateEmail(this.email()),
    linkedinUrl: validateSiteUrl(this.linkedinUrl(), 'linkedin.com', 'LinkedIn'),
    githubUrl: validateSiteUrl(this.githubUrl(), 'github.com', 'GitHub'),
    portfolioUrl: validateHttpUrl(this.portfolioUrl()),
  }));

  readonly anyError = computed(() => Object.values(this.errors()).some((e) => e !== null));

  /** Show an inline error only once the field is blurred or a save was tried. */
  showError(field: FieldKey): boolean {
    return this.errors()[field] !== null && (this.touched().has(field) || this.triedSave());
  }

  errorText(field: FieldKey): string | null {
    return this.errors()[field];
  }

  touch(field: FieldKey): void {
    if (this.touched().has(field)) return;
    this.touched.update((s) => new Set(s).add(field));
  }

  // --- Save-state machine --------------------------------------------------

  private snapshot(): Snapshot {
    return {
      phone: this.phone(),
      email: this.email(),
      linkedinUrl: this.linkedinUrl(),
      githubUrl: this.githubUrl(),
      portfolioUrl: this.portfolioUrl(),
      city: this.city(),
      careerSummary: this.careerSummary(),
      interestedInJobs: this.interestedInJobs(),
      interestedInInternships: this.interestedInInternships(),
      leaderboardOptOut: this.leaderboardOptOut(),
    };
  }

  private setBaseline(): void {
    this.baseline.set(this.snapshot());
  }

  readonly dirty = computed(() => {
    const base = this.baseline();
    if (!base) return false;
    const now = this.snapshot();
    return (Object.keys(now) as (keyof Snapshot)[]).some((k) => now[k] !== base[k]);
  });

  /** One derived status covering the whole save lifecycle. */
  readonly saveStatus = computed<{
    kind: 'saving' | 'error' | 'unsaved' | 'saved' | 'clean';
    icon: string;
    text: string;
  }>(() => {
    if (this.saving()) return { kind: 'saving', icon: 'progress_activity', text: 'Saving…' };
    if (this.saveError()) return { kind: 'error', icon: 'error', text: this.saveError()! };
    if (this.dirty()) return { kind: 'unsaved', icon: 'edit', text: 'Unsaved changes' };
    if (this.savedAt()) return { kind: 'saved', icon: 'check_circle', text: 'Saved just now' };
    return { kind: 'clean', icon: 'check', text: 'Up to date' };
  });

  readonly savedAtLabel = computed(() => {
    const t = this.savedAt();
    return t ? t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  });

  /** Save is blocked while loading, in flight, invalid, or with nothing to save. */
  readonly canSave = computed(
    () => this.loaded() && !this.saving() && !this.anyError() && this.dirty(),
  );

  // --- Placement-completion meter -----------------------------------------
  // Only the fields that materially help placements count. A filled-but-invalid
  // field does not count as complete.

  readonly completion = computed(() => {
    const items = [
      { label: 'phone', ok: this.phone().trim().length > 0 && !this.errors().phone },
      { label: 'contact email', ok: this.email().trim().length > 0 && !this.errors().email },
      { label: 'LinkedIn', ok: this.linkedinUrl().trim().length > 0 && !this.errors().linkedinUrl },
      { label: 'city', ok: this.city().trim().length > 0 },
      { label: 'career summary', ok: this.careerSummary().trim().length > 0 },
      { label: 'a jobs preference', ok: this.interestedInJobs() },
      { label: 'an internships preference', ok: this.interestedInInternships() },
    ];
    const done = items.filter((i) => i.ok).length;
    const pct = Math.round((done / items.length) * 100);
    const missing = items.filter((i) => !i.ok).map((i) => i.label);
    return { pct, done, total: items.length, missing };
  });

  readonly completionHint = computed(() => {
    const missing = this.completion().missing;
    if (missing.length === 0) return 'Every placement field is filled in.';
    const shown = missing.slice(0, 3).join(', ');
    const more = missing.length > 3 ? `, and ${missing.length - 3} more` : '';
    return `Add ${shown}${more} to strengthen your placement profile.`;
  });

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const [pRes, dRes] = await Promise.all([
        fetch(`${environment.apiBase}/student/profile`, { credentials: 'include' }),
        fetch(`${environment.apiBase}/student/dashboard`, { credentials: 'include' }),
      ]);

      // Locked identity. Still best-effort — it must never block the editable
      // form — but a failure is now recorded rather than rendered as an empty
      // USN, which a student would read as a missing record.
      if (dRes.ok) {
        const d = (await dRes.json()) as DashboardOut;
        this.name.set(d.name ?? '');
        this.usn.set(d.usn ?? null);
        this.identityState.set('ready');
      } else {
        this.identityState.set('error');
      }

      // No profile row yet: render the empty form so the first save creates one.
      if (pRes.status === 404) {
        this.setBaseline();
        this.loaded.set(true);
        return;
      }
      if (!pRes.ok) {
        this.error.set('Could not load your profile.');
        return;
      }

      this.apply((await pRes.json()) as ProfileOut);
      this.loaded.set(true);
    } catch {
      this.identityState.set('error');
      this.error.set('Could not reach the server.');
    }
  }

  private apply(p: ProfileOut): void {
    this.institution.set(p.institution ?? null);
    this.phone.set(p.phone ?? '');
    this.email.set(p.email ?? '');
    this.linkedinUrl.set(p.linkedin_url ?? '');
    this.githubUrl.set(p.github_url ?? '');
    this.portfolioUrl.set(p.portfolio_url ?? '');
    this.city.set(p.city ?? '');
    this.careerSummary.set(p.career_summary ?? '');
    this.interestedInJobs.set(p.interested_in_jobs);
    this.interestedInInternships.set(p.interested_in_internships);
    this.leaderboardOptOut.set(p.leaderboard_opt_out);
    this.placementEligible.set(p.placement_eligible);
    this.skills.set(this.readSkills(p.skills));
    // Everything now matches the server: this is the new clean baseline.
    this.setBaseline();
  }

  /** The skills blob is JSON of unknown shape — accept plain strings or {name}. */
  private readSkills(raw: unknown): string[] {
    if (!Array.isArray(raw)) return [];
    return raw
      .map((s) => {
        if (typeof s === 'string') return s;
        if (s && typeof s === 'object' && 'name' in s) return String((s as { name: unknown }).name);
        return '';
      })
      .filter((s) => s.length > 0);
  }

  private trimmed(v: string): string | null {
    const t = v.trim();
    return t.length > 0 ? t : null;
  }

  async save(): Promise<void> {
    if (this.saving()) return;
    // Surface every field error before committing to the request.
    this.triedSave.set(true);
    if (this.anyError()) return;

    this.saving.set(true);
    this.saveError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/profile`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: this.trimmed(this.phone()),
          email: this.trimmed(this.email()),
          linkedin_url: this.trimmed(this.linkedinUrl()),
          github_url: this.trimmed(this.githubUrl()),
          portfolio_url: this.trimmed(this.portfolioUrl()),
          city: this.trimmed(this.city()),
          career_summary: this.trimmed(this.careerSummary()),
          interested_in_jobs: this.interestedInJobs(),
          interested_in_internships: this.interestedInInternships(),
          leaderboard_opt_out: this.leaderboardOptOut(),
        }),
      });
      if (!res.ok) {
        this.saveError.set('Could not save your profile. Please try again.');
        return;
      }
      // Reflect the server's canonical view (skills, eligibility) back and
      // reset the baseline so the form reads as saved.
      this.apply((await res.json()) as ProfileOut);
      this.loaded.set(true);
      this.savedAt.set(new Date());
    } catch {
      this.saveError.set('Could not reach the server.');
    } finally {
      this.saving.set(false);
    }
  }

  /** Any edit clears a stale save error so the status reflects the live edit. */
  onEdit(): void {
    if (this.saveError()) this.saveError.set(null);
  }
}
