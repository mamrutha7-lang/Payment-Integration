/**
 * Turning AG Grid on, once, from whichever admin screen loads first.
 *
 * AG Grid 33 and later ship as modules that must be registered before the
 * first `<ag-grid-angular>` renders. Miss it and the grid does not throw: it
 * paints an empty rectangle and logs a module error into a console nobody has
 * open, which reaches a reviewer as "the students screen is blank".
 *
 * WHY A FUNCTION AND NOT A TOP-LEVEL CALL. Eleven screens draw a grid
 * (01-design-system.md §5) and each is its own lazily loaded route, so there is
 * no single module every one of them passes through — `main.ts` is the only
 * such place and importing AG Grid there puts ~300 kB into the initial bundle,
 * against a 610 kB budget the framework already fills to ~419 kB. So each
 * grid screen calls this from its constructor instead, and the flag makes the
 * second through eleventh calls free.
 *
 * `AllCommunityModule` is the whole Community feature set, which is what the
 * boards use: quick filter, floating filters, checkbox selection, column menu,
 * pinned columns, pagination and the status bar. Registering a narrower list
 * would be smaller by nothing measurable — the modules are in the same chunk
 * either way — and would fail as a missing feature on one screen months later.
 */

import { AllCommunityModule, ModuleRegistry } from 'ag-grid-community';

let registered = false;

/** Register AG Grid's Community modules. Safe to call from every grid screen. */
export function registerReepGrid(): void {
  if (registered) return;
  ModuleRegistry.registerModules([AllCommunityModule]);
  registered = true;
}
