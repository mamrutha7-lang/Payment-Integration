/**
 * The REEP chart theme: ECharts wearing the design system.
 *
 * Registered once per chunk that draws a chart, then named on every
 * `echarts.init` in it. Registration ALONE changes nothing — ECharts applies a
 * theme at init, so a chart created with `init(el, undefined, …)` stays on the
 * library's defaults however many themes are registered. That was the state of
 * the analytics screen before this file: two charts, both initialised with
 * `undefined`.
 *
 * WHY THE COLOURS ARE LITERALS. ECharts draws to a canvas or to SVG it
 * generates itself, and it resolves nothing through the CSS cascade: a
 * `var(--brand-purple)` handed to a series is passed through to the renderer
 * and drawn as an invalid colour. These are the values reep-v2.scss declares,
 * and tools/ci/check_theme_tokens.py fails if the two disagree.
 *
 * WHAT IS NOT IN HERE. The status colours and the per-track colours are
 * exported as named maps below rather than as extra keys on the theme object.
 * The design kit's JSON carried them as `_status` and `_tracks`; ECharts
 * copies unknown top-level keys onto the option, where they do nothing, and a
 * chart that needs "the colour for Business Analytics" wants to look it up by
 * name rather than by palette position.
 *
 * WHERE THIS MAY BE IMPORTED. Lazily-loaded routes only — see the same note in
 * shared/grid/reep-grid-theme.ts. The initial bundle is held to 610 kB and
 * ECharts is far larger than that on its own.
 */

import type { EChartsCoreOption } from 'echarts/core';

/** The one name the theme is registered under. */
export const REEP_CHART_THEME = 'reep';

const INK = '#1e1b29';
const AXIS_TEXT = '#7c7891';
const SPLIT_LINE = 'rgba(160, 138, 178, 0.26)';
const HAIRLINE = 'rgba(160, 138, 178, 0.42)';
const TINT_ONE = '#efe4f6';
const TINT_THREE = '#faf6fd';
const MUTED = '#585566';
const SEQUENTIAL_LIGHT = '#c9b8d8';
const SEQUENTIAL_DEEP = '#552c7e';

/**
 * The validated categorical palette.
 *
 * Four hues, searched in OKLCH anchored on the brand hue: every pair passes at
 * deltaE 10.1 for the common colour-vision deficiencies and 21.9 for normal
 * vision, and each clears 3:1 against the table ground. A fifth series folds
 * into "Other" rather than extending the ramp, because a fifth hue that passes
 * all four existing pairs does not exist in this space.
 */
export const CATEGORICAL_PALETTE = ['#9e6cd5', '#c47c00', '#009592', '#89275b'] as const;

/**
 * The colour of an interview track, fixed so a track reads the same on every
 * screen it appears on. Keyed by the track codes the matrix uses
 * (app/interview_matrix.py) rather than by the display label, which changes.
 */
export const TRACK_COLOURS: Readonly<Record<string, string>> = {
  fa: CATEGORICAL_PALETTE[0],
  hr: CATEGORICAL_PALETTE[1],
  dm: CATEGORICAL_PALETTE[2],
  ba: CATEGORICAL_PALETTE[3],
};

/**
 * Status on a chart. Separate from the categorical palette because a status is
 * not a series: two bars in the same chart may be "on track" and "at risk",
 * and they must not be told apart by their position in a ramp.
 */
export const STATUS_COLOURS = {
  good: '#137a4a',
  warn: '#d99a00',
  risk: '#ad2452',
  neutral: '#c9b8d8',
} as const;

/** The single-hue ramp for magnitude — hours logged, load, intensity. */
export const SEQUENTIAL_RAMP = [SEQUENTIAL_LIGHT, SEQUENTIAL_DEEP] as const;

/**
 * The theme itself. ECharts types this as a loose dictionary, so the shape is
 * not checked by the compiler; it is the standard theme-builder shape.
 */
export const reepChartTheme = {
  color: [...CATEGORICAL_PALETTE],
  backgroundColor: 'transparent',
  textStyle: {
    fontFamily: 'Inter, system-ui, sans-serif',
    color: INK,
  },
  title: {
    textStyle: {
      fontFamily: 'Plus Jakarta Sans, system-ui, sans-serif',
      fontWeight: 700,
      fontSize: 13.5,
      color: INK,
    },
  },
  legend: {
    textStyle: { color: INK, fontSize: 12 },
    inactiveColor: AXIS_TEXT,
    itemWidth: 16,
    itemHeight: 8,
    icon: 'roundRect',
    selectedMode: true,
  },
  tooltip: {
    backgroundColor: 'rgba(255,255,255,0.96)',
    borderColor: HAIRLINE,
    borderWidth: 1,
    textStyle: { color: INK, fontSize: 11.5 },
    extraCssText: 'box-shadow:0 6px 18px rgba(58,31,82,.16);border-radius:8px;',
    axisPointer: {
      type: 'cross',
      lineStyle: { color: AXIS_TEXT, type: 'dashed' },
      crossStyle: { color: AXIS_TEXT },
    },
  },
  grid: { left: 44, right: 24, top: 24, bottom: 36, containLabel: false },
  categoryAxis: {
    axisLine: { lineStyle: { color: AXIS_TEXT, opacity: 0.5 } },
    axisTick: { show: false },
    axisLabel: { color: AXIS_TEXT, fontSize: 10.5 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: AXIS_TEXT, fontSize: 10.5 },
    splitLine: { lineStyle: { color: SPLIT_LINE } },
  },
  line: {
    smooth: false,
    symbol: 'circle',
    symbolSize: 6,
    lineStyle: { width: 2 },
    itemStyle: { borderWidth: 2, borderColor: '#fff' },
    emphasis: { focus: 'series', blurScope: 'global' },
  },
  bar: {
    itemStyle: { borderRadius: [3, 3, 0, 0] },
    barMaxWidth: 28,
    emphasis: { focus: 'series', blurScope: 'global' },
  },
  pie: {
    itemStyle: { borderColor: '#fff', borderWidth: 2 },
  },
  dataZoom: {
    borderColor: HAIRLINE,
    backgroundColor: TINT_THREE,
    fillerColor: TINT_ONE,
    handleStyle: { color: SEQUENTIAL_DEEP },
    dataBackground: {
      lineStyle: { color: SEQUENTIAL_LIGHT },
      areaStyle: { color: TINT_ONE },
    },
    textStyle: { color: MUTED, fontSize: 10 },
  },
  visualMap: {
    inRange: { color: [...SEQUENTIAL_RAMP] },
    textStyle: { color: MUTED },
  },
} satisfies EChartsCoreOption;

/**
 * Register the theme with an ECharts core instance.
 *
 * Takes the module rather than importing it, so this file never pulls ECharts
 * into whatever imports it — the chunk that draws the chart already has the
 * library, and a screen that only needs TRACK_COLOURS should not download it.
 *
 * Registering twice is harmless: ECharts overwrites the entry.
 */
export function registerReepChartTheme(echarts: {
  registerTheme: (name: string, theme: object) => void;
}): void {
  echarts.registerTheme(REEP_CHART_THEME, reepChartTheme);
}
