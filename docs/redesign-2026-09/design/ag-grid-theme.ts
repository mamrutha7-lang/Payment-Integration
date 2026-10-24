// AG Grid theme for REEP — 2026-09 redesign (ag-grid-community >= 33, Theming API).
// import { themeQuartz } from 'ag-grid-community';
import { themeQuartz } from 'ag-grid-community';

export const reepGridTheme = themeQuartz.withParams({
  accentColor: '#552c7e',
  backgroundColor: '#ffffff',
  foregroundColor: '#1e1b29',
  headerBackgroundColor: '#faf6fd',
  headerTextColor: '#4a2668',
  headerFontWeight: 600,
  headerHeight: 40,
  rowHeight: 40,
  borderColor: 'rgba(160, 138, 178, 0.42)',
  rowBorder: { color: 'rgba(160, 138, 178, 0.26)' },
  columnBorder: { color: 'rgba(160, 138, 178, 0.26)' },
  headerColumnBorder: { color: 'rgba(160, 138, 178, 0.26)' },
  rowHoverColor: '#faf6fd',
  selectedRowBackgroundColor: '#f5edfa',
  rangeSelectionBorderColor: '#552c7e',
  chromeBackgroundColor: '#faf6fd',
  fontFamily: 'Inter, system-ui, sans-serif',
  fontSize: 12.5,
  borderRadius: 8,
  wrapperBorderRadius: 12,
  inputBorder: { color: 'rgba(160, 138, 178, 0.42)' },
  inputFocusBorder: { color: '#552c7e' },
  checkboxCheckedBackgroundColor: '#552c7e',
  spacing: 8,
});

// Density toggle (the "Density" toolbar button on the boards): 40 px rows by default, 36 px compact.
export const reepGridThemeCompact = reepGridTheme.withParams({ rowHeight: 36, spacing: 6 });
