/**
 * The institutional hierarchy's switch, as the form sees it.
 *
 * ONE SOURCE OF TRUTH, TWO READERS. `HIERARCHY_LEVELS` in
 * apps/api-py/app/models/institution.py says which of the optional levels
 * (Course, Specialization) a new batch must name. The API's own validator reads
 * it; this service reads it through GET /api/admin/hierarchy/levels; and the
 * batch form builds its `Validators.required` from what comes back. Nothing in
 * the client hardcodes a level name or a required flag, so flipping the
 * constant is genuinely one edit: the form grows a validator with no second
 * change here.
 *
 * WHY A RUNTIME FETCH AND NOT A SHARED CONSTANT. A `.ts` copy of the tuple is
 * two edits, not one — and CI ships the api and web jobs separately, so a tab
 * open across a deploy would hold a bundle saying "optional" against an API
 * saying "required", and the 422 would land with no field marked. The house
 * already answers this kind of question at runtime (`/auth/sso/status`,
 * `/interview/status`); this is the same shape.
 *
 * NO FALLBACK DEFAULT. If the schema cannot be fetched the screen is in error,
 * not in a guessed-optional mode: guessing "optional" lets an admin fill a form
 * the server rejects, guessing "required" blocks a console that works fine.
 * Memoised per page load only — never localStorage, for the same skew reason.
 */

import { Injectable } from '@angular/core';

import { environment } from '../../environments/environment';

/** Exact snake_case shape of HierarchyLevelOut. */
export interface HierarchyLevel {
  key: string;
  label: string;
  field: string;
  required: boolean;
}

@Injectable({ providedIn: 'root' })
export class HierarchySchemaService {
  private cached: HierarchyLevel[] | null = null;
  private inFlight: Promise<HierarchyLevel[]> | null = null;

  /** One fetch per page load. `force` is for the stale-schema 422 path. */
  load(force = false): Promise<HierarchyLevel[]> {
    if (force) {
      this.cached = null;
      this.inFlight = null;
    }
    if (this.cached) return Promise.resolve(this.cached);
    this.inFlight ??= (async () => {
      const res = await fetch(`${environment.apiBase}/admin/hierarchy/levels`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`hierarchy levels: ${res.status}`);
      const levels = (await res.json()) as HierarchyLevel[];
      this.cached = levels;
      return levels;
    })().finally(() => {
      this.inFlight = null;
    });
    return this.inFlight;
  }
}
