/**
 * The REEP data grid: AG Grid wearing the design system.
 *
 * The approved admin boards draw AG Grid's look for eleven screens — mentor
 * load, students and batches, faculty, mentor mapping, registrations, the
 * import preview, the jobs sheet, placement offers, interview records, the
 * audit log and export history (01-design-system.md §5). Rather than every one
 * of them re-deriving spacing and colour from the tokens, they import this
 * theme and pass it to `<ag-grid-angular [theme]="reepGridTheme">`.
 *
 * TOKENS ARE COPIED HERE AS LITERALS, and that is not an oversight. AG Grid's
 * Theming API writes its own CSS custom properties into a shadow root, and the
 * `--brand-purple` of the surrounding page does not cross that boundary, so a
 * `var(--brand-purple)` here resolves to nothing and the grid renders
 * unstyled. The values are the same ones reep-v2.scss declares, and
 * tools/ci/check_theme_tokens.py fails if the two ever disagree.
 *
 * WHERE THIS MAY BE IMPORTED. Lazily-loaded routes only. AG Grid is a large
 * dependency and the initial bundle is held to 610 kB, ~419 of it already the
 * framework; every admin screen is already a `loadComponent` route, so
 * importing it from one costs nothing on the login screen. Never import this
 * from the shell, a guard or a service the shell injects.
 *
 * HOW A SCREEN USES IT, once its Phase 2 board is built:
 *
 *     import { AgGridAngular } from 'ag-grid-angular';
 *     import { AllCommunityModule, ModuleRegistry } from 'ag-grid-community';
 *     import { reepGridTheme } from '../../shared/grid/reep-grid-theme';
 *
 *     ModuleRegistry.registerModules([AllCommunityModule]);   // once per app
 *
 * The registration is a runtime requirement of AG Grid 33 and later: without
 * it the grid renders empty and logs a module error rather than throwing, so
 * it fails as a blank rectangle on somebody's screen.
 */

import { themeQuartz } from 'ag-grid-community';

/**
 * The design tokens the grid needs, as literals.
 *
 * Named rather than inlined so the CI check has something to compare against
 * reep-v2.scss, and so a reader can see that the grid's accent IS the app's
 * brand purple rather than a colour that happens to look like it.
 */
export const GRID_TOKENS = {
  brandPurple: '#552c7e',
  ink: '#1e1b29',
  inkSoft: '#4a2668',
  surface: '#ffffff',
  tintTwo: '#f5edfa',
  tintThree: '#faf6fd',
  hairline42: 'rgba(160, 138, 178, 0.42)',
  hairline26: 'rgba(160, 138, 178, 0.26)',
} as const;

/**
 * The default density: 40px header, 40px rows, as the boards draw them.
 */
export const reepGridTheme = themeQuartz.withParams({
  accentColor: GRID_TOKENS.brandPurple,
  backgroundColor: GRID_TOKENS.surface,
  foregroundColor: GRID_TOKENS.ink,
  headerBackgroundColor: GRID_TOKENS.tintThree,
  headerTextColor: GRID_TOKENS.inkSoft,
  headerFontWeight: 600,
  headerHeight: 40,
  rowHeight: 40,
  borderColor: GRID_TOKENS.hairline42,
  rowBorder: { color: GRID_TOKENS.hairline26 },
  columnBorder: { color: GRID_TOKENS.hairline26 },
  headerColumnBorder: { color: GRID_TOKENS.hairline26 },
  rowHoverColor: GRID_TOKENS.tintThree,
  selectedRowBackgroundColor: GRID_TOKENS.tintTwo,
  chromeBackgroundColor: GRID_TOKENS.tintThree,
  fontFamily: 'Inter, system-ui, sans-serif',
  fontSize: 12.5,
  borderRadius: 8,
  wrapperBorderRadius: 12,
  inputBorder: { color: GRID_TOKENS.hairline42 },
  inputFocusBorder: { color: GRID_TOKENS.brandPurple },
  checkboxCheckedBackgroundColor: GRID_TOKENS.brandPurple,
  spacing: 8,
});

/**
 * The "Density" toolbar button on the boards switches to this: 36px rows, as
 * 01-design-system.md §4 specifies for a compact grid.
 */
export const reepGridThemeCompact = reepGridTheme.withParams({
  rowHeight: 36,
  spacing: 6,
});
