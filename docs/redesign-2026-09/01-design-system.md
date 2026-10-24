# 01 · Design system (admin console + student portal)

One language for both consoles. It keeps the main-branch colour code (`apps/web/src/styles/reep-v2.scss` tokens) and adds the enterprise dress: Plus Jakarta Sans + Inter, AG Grid-style tables, ECharts-style charts, one primary action per view, status always as dot + label. The reference board is `design/admin/DesignSystem.html`; the generator that produced every board is `build2.py` (tokens `T`, `BASE_CSS`) — the CSS below is that file, expressed as the tokens the app already has.

## 1. Tokens

Existing names in `reep-v2.scss` are kept; new ones are added. Drop-in file: `design/tokens.css`.

| Token | Value | Use |
|---|---|---|
| `--brand-purple` | `#552c7e` | links, selection, active rail item, series 1, focus ring |
| `--brand-magenta` | `#ba2185` | **only inside the primary gradient** — never on text, chips or charts |
| `--purple-mid` / `--magenta-mid` | `#7a2f9e` / `#a0248f` | gradient stops only |
| `--ink` | `#1e1b29` | body text |
| `--ink-soft` | `#4a2668` | table headers, labels, secondary text on tint |
| `--ink-nav` | `#5b3080` | sidebar items, icon buttons |
| `--muted` / `--faint` | `#585566` / `#7c7891` | sub-lines / hints, axis text |
| `--surface` | `#ffffff` | inputs, table body, tiles |
| `--tint-1` / `--tint-2` / `--tint-3` | `#efe4f6` / `#f5edfa` / `#faf6fd` | selected pill, selected row, table header / toolbar |
| `--hairline` (+ `-26`, `-42`, `-45`) | `rgba(160,138,178,.38)` (.26 / .42 / .45) | borders: card / row dividers / controls / emphasised |
| `--good` / `--good-bg` | `#137a4a` / `rgba(19,122,74,.12)` | status good |
| `--warn` / `--warn-dot` / `--warn-bg` | `#8f6100` / `#d99a00` / `rgba(217,154,0,.12)` | status warn (text / dot / bg) |
| `--risk` / `--risk-bg` | `#ad2452` / `rgba(173,36,82,.1)` | status risk, danger buttons |
| `--page-wash` | `linear-gradient(180deg,#ece4f5 0%,#f2e7f8 45%,#fbe6f2 100%)` | page background |
| `--primary-gradient` | `linear-gradient(120deg,#552c7e,#7a2f9e 38%,#a0248f 68%,#ba2185)` | primary button, active nav pill, active tab, brand mark, meters — **the only place magenta appears** |
| `--card-gradient` | `linear-gradient(180deg,rgba(255,255,255,.86),rgba(245,237,250,.7))` | cards |
| `--titlebar-gradient` | `linear-gradient(180deg,#faf6fd,#ece0f2)` | app bar |
| `--sh-card` / `--sh-ctrl` / `--sh-primary` / `--sh-navpill` / `--sh-well` | see `tokens.css` | card / control / primary button / nav pill / input inset |
| `--cat-1 … --cat-4` | `#9e6cd5` `#c47c00` `#009592` `#89275b` | the validated categorical palette (all pairs pass on `#faf6fd`). Fixed per track: FIN = 1, HR = 2, MKT = 3, BA = 4 |
| `--seq` / `--seq-light` | `#552c7e` / `#c9b8d8` | single-hue sequential ramp |
| `--axis` / `--split` | `#7c7891` / `rgba(160,138,178,.26)` | chart axis text / grid lines |
| `--st-good` / `--st-warn` / `--st-risk` / `--st-neutral` | `#137a4a` / `#d99a00` / `#ad2452` / `#c9b8d8` | chart status colours |

**Colour rules (from the Design system board).** Primary gradient in exactly three kinds of place: the one primary button of a view, the active nav pill / tab, the brand mark (plus meters). Brand purple for links, selection and series 1. Magenta never stands alone. Neutrals are lilac tints, never grey. Semantic status colours only with an icon or dot and a label. Charts use the categorical four for tracks, the sequential ramp for intensity, status colours for status — never brand purple for a data series other than series 1.

## 2. Typography & rhythm

- Display: `'Plus Jakarta Sans'` 500–800 — h1 (22 px / 700 / −0.02 em), card titles (13.5 px / 700), KPI numerals (24 px / 800), eyebrows (10.5 px / 700 / +0.12 em uppercase), nav labels.
- UI: `'Inter'` 400–700 — body 13 px / 1.45, sub-lines 12.5 px, table cells 12.5 px, chips 11.5 px / 700, hints 11.5 px. Numerals `font-variant-numeric: tabular-nums` everywhere numbers align.
- Radii: card 16, control/input/button 12, small tile 8–10, pill 999. Spacing scale 4 / 6 / 8 / 10 / 12 / 14 / 16 / 24.
- Fonts stay self-hosted (`apps/web/public/fonts/`, `tools/fonts/fetch-fonts.sh`). Orbitron and Chakra Petch are retired from the shell; the resume builder and login also move to Plus Jakarta Sans + Inter.

## 3. Layout

- **App bar** 52 px, titlebar gradient, 1 px hairline bottom: brand mark (26 px gradient square "R") + "REEP" + console name; admin adds the environment pill, the ⌘K search (300 px), the **scope control** (College / Department — the multi-college scope bar), bell + help, avatar + name + role. Student adds only avatar + name/USN and **Sign out**.
- **Sidebar** 220 px: groups with uppercase labels, pill items (999 radius), active item = primary gradient + nav-pill shadow. Student sidebar keeps main's exact items (Home, Jobs, Skilling, Leaderboards, Time Sheet; PROGRAMME → Faculty / TPO Log; DOCUMENTS → Resume Builder) plus the profile card on top. Admin sidebar (exactly as on the boards): OVERVIEW Analytics · INSTITUTION Colleges, Structure, Faculty, Students & batches, Mentor mapping, Catalogue · OPERATIONS Registrations, Data imports, Leave approvals, Jobs & placement, Exports · STUDENT INSIGHT Question bank, Interview records, SWOC notes · GOVERNANCE Roles & functions, Audit log (Feature switches is reached from Roles & functions) · TOOLS REEP Agent; My account under the avatar menu; Placement is reached from Jobs & placement and the Analytics tiles. A faculty account with granted functions sees the granted admin links under "Granted access" (unchanged behaviour). **Superseded 2026-09-15:** the Main Admin lands on a task-list Home at `/admin` (Analytics is at `/admin/analytics`), the rows are relabelled in plain words and regrouped as People · Every day · Interviews · College setup · Settings, and Placement has its own row — see AGENTS.md, "the Main Admin lands on a list of tasks". The screens, guards and capabilities are unchanged.
- **Content** column: padding 16 / 20 (admin, fixed 900 px viewport-height boards with internal scroll) or 16 / 24 with natural height (student, page scrolls). Page head = crumb (11.5 px faint) → h1 → sub-line, with the view's actions right-aligned and bottom-aligned.
- Side panels (drawers) are 380 px; with a panel open a grid shows at most six columns.
- Mobile: the student sign-in has a 390 px layout; everything else is desktop-first (the shell does not collapse today — unchanged in this release).

## 4. Components

| Component | Spec |
|---|---|
| **Card** | card gradient, 1 px hairline, radius 16, `--sh-card`; padding 16 (14 dense, 0 for a card that is only a grid); optional title row (card-title + faint sub, actions right). |
| **KPI tile** | card, padding 11/14: eyebrow → numeral 22–24 px (+ optional sparkline right) → delta (icon ↑↓ + value, good/risk) · sub. |
| **Chip** | pill, 3/10 padding, 11.5 px 700; tones neutral (tint-2 / ink-soft), good, warn, risk, accent (tint-1 / brand purple), navy (brand purple bg / white); **dot** variant for status (7 px dot before the label). |
| **Buttons** | height 34 (28 sm), radius 12, 12.5 px 700, icon 15 px. *primary*: gradient + `--sh-primary`, white text — one per view. *secondary*: white, hairline-42, `--sh-ctrl`. *danger*: white, risk border + text. *ghost*: transparent, brand purple. *icon*: 34 × 34 white bordered. Disabled = opacity .55, no shadow. |
| **Select** | 34 px, white, hairline-42, radius 12: faint label · bold value · chevron. |
| **Field / input** | label (11.5 px 700 ink-soft; required `*` in risk) → input 34 px min, white, hairline-42, radius 12, `--sh-well`; `.multi` 56 px+; help 11.5 muted; error 11.5 risk with icon; synced/locked fields on tint-2 with a lock icon and a "Synced" chip. |
| **Note** | 10/12 padding, radius 12, icon + text; tones accent (tint-1), warn, good, risk. |
| **Steps** | numbered 22 px circles: active gradient, done good, pending hairline. |
| **Tabs** | pill tabs; active = gradient. |
| **Rail / tree row** | pill rows 6/10; selected = tint-1 + brand purple 700 (resume rail: selected = brand purple bg, white). |
| **Meter** | 6–7 px track tint-1, fill brand purple or gradient. |
| **Avatar** | circle, tint-1 bg, brand purple initials. |
| **Data table (AG Grid look)** | container hairline-42 radius 12 white; optional toolbar (quick filter 240 px, Columns / Density / Export icon buttons); header 40 px tint-3, 12 px 600 ink-soft, column borders hairline-26; rows 40 px (36 compact) hairline-26 dividers, cells 12.5 px nowrap ellipsis; pinned first column with inset right shadow; selected row = tint-2 + 3 px brand purple inset bar; floating filter row; status bar 38 px (rows · selected · page size · pager); side panel (Columns / Filters). The student console uses the same look without toolbar/status when the table is short. |
| **Chart** | ECharts styling: split lines `--split`, axis text `--axis` 10.5 px, legend pills with toggles and the emphasised series on tint-1, crosshair tooltip (white, hairline, 8 px radius, 11.5 px), dataZoom strip, categorical palette per track. One composite chart per analytics view (legend toggles, opacity emphasis, multi-Y axes, tooltip) — never a wall of small charts. |
| **Dark stage** (mock interview) | radius 16, `linear-gradient(135deg,#1b1526,#2a1c3f 55%,#3d1f4c)`, white text, status pill on `rgba(255,255,255,.1)`, orb 112 px with gradient ring. |
| **Badge emblem** | hexagonal shield SVG: outlined lilac when available, solid brand purple with a green verified seal when earned (see `design/student/SkillingRedesign.html`). |

## 5. AG Grid and ECharts in the Angular app

The boards draw the AG Grid **look**; the app may either adopt the real libraries or reproduce the classes. Recommendation (already in the design intent): use the real libraries where the screen is a data grid or a chart, with the theme files in `design/`:

- **AG Grid Community** (`ag-grid-angular`, `ag-grid-community`) with the Quartz theme and the params in `design/ag-grid-theme.ts` (accent = brand purple, backgrounds tint-3 / white, borders hairline, Inter 12.5 px, header 40, row 40, radius 12). Use it on: Analytics mentor load, Students & batches, Faculty, Mentor mapping, Registrations, Data imports preview, Jobs sheet, Placement offers, Interview records, Audit log, Exports history. Student tables stay plain HTML with the same classes.
- **ECharts** (already a dependency, `echarts ^6`) with the theme in `design/echarts-theme.json` registered once (`echarts.registerTheme('reep', theme)`). One composite chart per analytics view; legend `selectedMode: true`, `emphasis.focus: 'series'` with `blurScope: 'global'`, up to three y-axes (percent left, hours right, count right-offset), `tooltip.trigger: 'axis'` with `axisPointer: 'cross'`, `dataZoom` slider. Lazy-load the analytics chunk (it already is).
- Bundle budget: the 400 kB initial budget is unchanged. Both libraries must live in lazily loaded chunks (`loadComponent` routes) — never in the initial bundle.

## 6. Icons

The app renders **Material Symbols Rounded** from ligatures with a subset (`tools/fonts/icon-names.txt`, `collect-icon-names.py`). The boards use stroke icons of the same meaning; map each to the Material glyph (e.g. `home`, `work`, `verified`, `leaderboard`, `schedule`, `event_note`, `description`, `person`, `logout`, `search`, `filter_list`, `tune`, `download`, `upload`, `visibility`, `lock`, `check_circle`, `pending`, `error`, `info`, `bolt`, `mic`, `stop`, `send`, `thumb_up`, `thumb_down`, `content_copy`, `chevron_left/right`, `calendar_today`, `hourglass_top`, `shield_person`, `delete`, `edit`, `add`, `school`, `apartment`, `group`, `key`, `history`, `flag`, `bedtime`, `wb_twilight`). Every new glyph is added to `icon-names.txt` and the font regenerated (`fetch-fonts.sh`), otherwise it renders as nothing. Keep `aria-hidden="true"` on decorative icons and an `aria-label` on icon-only buttons.

## 7. Accessibility & states

Real `<h1>` per screen; `role="status"` / `role="alert"` live regions for notices; radiogroups for tile choices; focus-visible ring 2 px brand purple at 25 %; reduced-motion respected (orb, meters); geometry-hidden file inputs. Every screen designs its empty, loading and error states with the same components (note tone accent for empty, warn for blocked, risk for errors) — the boards show the populated state; use main's existing copy for empty/error states.
