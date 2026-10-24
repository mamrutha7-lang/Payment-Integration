/**
 * Resume Builder — shared state for the whole 15-section builder.
 *
 * One root singleton holds the entire resume profile as an opaque section map
 * (`data`: section-key -> object | array). The shell (resume-builder.component)
 * calls `load()` once; each standalone section component reads its own slice via
 * `section(key, fallback)` and writes it back via `patch(key, value)` — sections
 * never fetch or PUT resume-profile themselves, they only mutate this signal and
 * let the shell's "Save section" button flush the whole map with `save()`.
 *
 * Read-only sections that mirror another domain (education / attachments /
 * certifications) ignore this map and fetch their own endpoint instead.
 *
 * Endpoint: GET/PUT `${apiBase}/student/resume-profile`, cookie-authenticated.
 *   GET  -> { data: Record<string, unknown>, completeness: number }
 *   PUT  { data } -> { completeness: number }
 */

import { Injectable, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';

/**
 * Leaf keys the FORM supplies. Mirrors `_STRUCTURAL_LEAF_KEYS` in
 * apps/api-py/app/routers/student.py.
 */
const STRUCTURAL_LEAF_KEYS = new Set(['country', 'code']);

/**
 * Does a stored section hold anything the student actually typed?
 *
 * ONE definition, imported by everything that asks. It was written twice —
 * `isEmptySection` in tailor.component.ts and again in preview.component.ts —
 * and both copies counted a boolean or a form-supplied default as content, so
 * "Missing essentials" stopped listing Contact Details the moment a student
 * added an empty phone row. Two copies of a rule the API also implements is
 * three places for one answer to drift.
 */
export function sectionHasContent(value: unknown, key?: string): boolean {
  if (value === null || value === undefined) return false;
  if (Array.isArray(value)) return value.some((v) => sectionHasContent(v));
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).some(([k, v]) =>
      sectionHasContent(v, k),
    );
  }
  if (typeof value === 'string') {
    return !!value.trim() && !STRUCTURAL_LEAF_KEYS.has(key ?? '');
  }
  return false;
}

@Injectable({ providedIn: 'root' })
export class ResumeBuilderService {
  /** section-key -> value (object or array). The full resume profile. */
  readonly data = signal<Record<string, any>>({});
  /** Server-computed profile completeness, 0..100. */
  readonly completeness = signal<number>(0);
  /** True once the first load() has populated `data`. */
  readonly loaded = signal(false);
  /** True while a save() PUT is in flight. */
  readonly saving = signal(false);
  /** ISO timestamp of the last successful save, or null. */
  readonly savedAt = signal<string | null>(null);
  /** Last load/save error message, or null. */
  readonly error = signal<string | null>(null);
  /**
   * True when the local `data` has changed since the last successful save.
   * Flips true on every patch(), false when a save() succeeds. Drives the
   * sticky save bar's "Unsaved changes" state.
   */
  readonly dirty = signal(false);

  /**
   * Fill state for the sections that MIRROR another domain and so write nothing
   * into `data`: Education, Attachments and Certifications.
   *
   * Their stepper dots were derived from `data[key]`, which those sections never
   * touch, so all three read "Not started" permanently — Education beside a full
   * semester record, Attachments beside four uploaded documents, Certifications
   * beside a certificate the student had just added (it stores under
   * `external_certs`). Three dots that cannot change are three dots a student
   * learns to ignore, which is most of what the stepper is for.
   *
   * Each section reports its own state once it has loaded, because each one is
   * the only thing that knows: the shell would otherwise have to repeat their
   * three fetches to colour three dots.
   */
  readonly mirrorStates = signal<Record<string, 'done' | 'partial' | 'empty'>>({});

  reportMirrorState(key: string, state: 'done' | 'partial' | 'empty'): void {
    this.mirrorStates.update((m) => (m[key] === state ? m : { ...m, [key]: state }));
  }

  /** Pending autosave timer (debounced flush after patch()). */
  private autosaveTimer: ReturnType<typeof setTimeout> | null = null;
  /** Debounce window before an edit is autosaved, in ms. */
  private readonly AUTOSAVE_MS = 1500;

  /** Fetch the whole profile and completeness; sets `loaded` on success. */
  async load(): Promise<void> {
    this.error.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/resume-profile`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.error.set('Could not load your resume profile.');
        return;
      }
      const body = (await res.json()) as { data?: Record<string, any>; completeness?: number };
      this.data.set(body.data ?? {});
      this.completeness.set(body.completeness ?? 0);
      this.loaded.set(true);
    } catch {
      this.error.set('Could not reach the server.');
    }
  }

  /** Read one section's slice, or `fallback` when it has never been set. */
  section(key: string, fallback: any): any {
    return this.data()[key] ?? fallback;
  }

  /**
   * Replace one section's slice, leaving every other section untouched.
   * Marks the profile dirty and schedules a debounced autosave so typing is
   * not lost even if the student never presses Save.
   */
  patch(key: string, value: any): void {
    this.data.update((d) => ({ ...d, [key]: value }));
    this.dirty.set(true);
    this.scheduleAutosave();
  }

  /** (Re)arm the debounce timer; the last edit within the window wins. */
  private scheduleAutosave(): void {
    if (this.autosaveTimer) clearTimeout(this.autosaveTimer);
    this.autosaveTimer = setTimeout(() => {
      this.autosaveTimer = null;
      // Only fire when there is still something to save and no save is running;
      // a manual Save mid-window clears dirty and this becomes a no-op.
      if (this.dirty() && !this.saving()) void this.save();
    }, this.AUTOSAVE_MS);
  }

  /** Flush the whole profile map; updates `completeness` and `savedAt`. */
  async save(): Promise<void> {
    // A manual/auto save supersedes any pending debounce.
    if (this.autosaveTimer) {
      clearTimeout(this.autosaveTimer);
      this.autosaveTimer = null;
    }
    this.saving.set(true);
    this.error.set(null);
    // Snapshot the payload and optimistically clear dirty; edits made while the
    // PUT is in flight re-set dirty (and re-arm autosave) so nothing is lost.
    this.dirty.set(false);
    const payload = JSON.stringify({ data: this.data() });
    try {
      const res = await fetch(`${environment.apiBase}/student/resume-profile`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
      });
      if (!res.ok) {
        this.error.set('Could not save your changes. Please try again.');
        this.dirty.set(true);
        return;
      }
      const body = (await res.json()) as { completeness?: number };
      this.completeness.set(body.completeness ?? this.completeness());
      this.savedAt.set(new Date().toISOString());
    } catch {
      this.error.set('Could not reach the server.');
      this.dirty.set(true);
    } finally {
      this.saving.set(false);
    }
  }
}
