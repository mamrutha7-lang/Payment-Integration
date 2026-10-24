# Handoff: REEP Student App (v2 UI)

## Overview
Student-facing web app for the REEP employability programme. A student signs in and lands on a dashboard, then works across: job/internship opportunities, skilling badges, leaderboards, a daily **Time Allocation Ledger**, an **English Baseline (AI)** assessment, a mentor meeting log, document uploads, a resume builder, and a **REEP Agent** AI assistant (text screen + full-screen voice mode reachable from a floating orb on every page).

Aesthetic: a restrained Y2K-chrome / glass look — soft lilac-to-pink page wash, white translucent cards with 1px lavender hairlines and layered soft shadows, `Orbitron` for headings/labels, `Chakra Petch` for body, and a purple→magenta gradient reserved for primary actions and the active nav pill.

## About the design files
The files in this bundle are **design references authored in HTML** — prototypes that show intended look, structure and behaviour. They are **not production code to copy directly**.

The task is to **recreate these designs in the target codebase's existing environment** (React, Vue, Angular, SwiftUI, native — whatever the project uses), using its established routing, state, component library and styling conventions. If no environment exists yet, pick the most appropriate framework for the project and implement the designs there.

Two implementation notes about the prototype format, so it isn't mistaken for an app architecture:
- `REEP Student App.dc.html` is a **single-file component prototype**. `<x-dc>` wraps the template; the `<script data-dc-script>` block at the bottom holds a React-class-like logic object (`state`, `setState`, `renderVals()`). `support.js` is only the prototype runtime — **do not port it**.
- `<sc-if value="{{ x }}">` = conditional render; `<sc-for list="{{ xs }}" as="item">` = list render; `{{ path }}` = a value from `renderVals()`; `style-hover="…"` = the `:hover` style. Translate these to the target framework's idioms.
- **All styling is inline in the prototype by necessity.** Do not reproduce that in production — extract the token table below into the codebase's real theme/token layer and build proper components.

## Fidelity
**High-fidelity (hifi).** Colours, type, spacing, radii, shadows, states and copy are final and intentional. Recreate the UI pixel-faithfully using the codebase's own libraries and patterns. Where the prototype's inline values conflict with an existing house component, prefer the house component's structure and match its visuals to the tokens below.

---

## Design tokens

### Colour
| Token | Value | Use |
| --- | --- | --- |
| Brand purple | `#552C7E` | Primary brand, gradient start, coursework series |
| Brand magenta | `#BA2185` | Primary accent, gradient end, skilling series |
| Purple mid | `#7a2f9e` | Gradient mid, link default, lectures series |
| Magenta mid | `#a0248f` | Gradient mid-late |
| Ink | `#3a1f52` | Body text, primary headings, dark borders |
| Ink soft | `#4a2668` | Legend / small labels |
| Ink nav | `#5b3080` | Inactive nav label, icon buttons |
| Muted | `#7a6392` | Secondary text, sub-labels |
| Faint | `#a08cb2` | Tertiary text, tick labels, units |
| Series light | `#b9a8c9` | Sleep band |
| Series pale | `#d9c8e6` | Travel / personal band |
| Surface | `#ffffff` | Card base |
| Surface tint | `#efe4f6`, `#f5edfa`, `#faf6fd` | Wells, hover fills |
| Hairline | `rgba(160,138,178,.38)` (cards) / `.42` / `.45` / `.26` (table rules) | Borders |
| Success | `#137a4a` on `rgba(19,122,74,.12)` | Completed, streak |
| Warning | `#8f6100`, dot `#d99a00` | Pending, unaccounted hours |
| Danger | `#ad2452` on `rgba(173,36,82,.1)` | Errors |
| Page background | `linear-gradient(180deg,#ece4f5 0%,#f2e7f8 45%,#fbe6f2 100%)`, `background-attachment: fixed` | App canvas |
| Primary gradient | `linear-gradient(120deg,#552C7E,#7a2f9e 38%,#a0248f 68%,#BA2185)` | Primary buttons, active nav pill |
| Card gradient | `linear-gradient(180deg,rgba(255,255,255,.86),rgba(245,237,250,.7))` | Standard card fill |
| Voice overlay | `radial-gradient(120% 85% at 50% 12%,rgba(96,45,138,.96),rgba(26,10,42,.98))` + `backdrop-filter: blur(8px)` | Voice mode scrim |

Links: `a { color:#7a2f9e }`, `a:hover { color:#552C7E }`.

### Typography
- **Display / labels:** `Orbitron` — 500, 700, 900.
  - Page title: 22px / 900 / `letter-spacing:.005em` / `line-height:1.15`
  - Dashboard greeting: 19px / 900
  - Card heading: 14px / 700 / `letter-spacing:.03em`
  - Metric number: 27px / 800, `font-variant-numeric: tabular-nums`
  - Eyebrow / column label: 9.5px / 700 / uppercase / `letter-spacing:.14em`
  - Sidebar group label: 10.5px / 700 / uppercase / `letter-spacing:.12em`
- **Body:** `Chakra Petch` — 400/500/600/700.
  - Body: 13px; secondary 12.5–12.8px; caption 11.5px; micro 10–11px
  - Nav item: 13px / 600 · Titlebar: 12.5px / 700 · Button: 12.5–13px / 700
- **Icons:** `Material Symbols Rounded`, `font-variation-settings:'opsz' 24,'wght' 400,'FILL' 0,'GRAD' 0`; 14px (titlebar/chips), 15–16px (buttons), 18px (steppers), 20px (nav), 25px (voice controls), 52px (avatar).

Google Fonts: `Orbitron:wght@500;700;900`, `Chakra+Petch:wght@400;500;600;700`, `Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,300..600,0..1`.

### Radius
`999px` pills / nav items / bars · `22px` standard card · `20px` ledger card · `18px` profile card · `14px` alert · `12px` button + segmented control · `8px` icon-button, avatar frame · `6px` day-band segment · `3px` legend swatch.

### Shadow
- Card: `0 10px 26px rgba(58,31,82,.1), inset 0 1px 0 #fff`
- Control: `0 2px 6px rgba(58,31,82,.06), inset 0 1px 0 #fff`
- Primary button: `0 8px 18px rgba(85,44,126,.32), inset 0 1px 0 rgba(255,255,255,.5)`; hover `0 10px 22px rgba(85,44,126,.42), inset 0 1px 0 rgba(255,255,255,.6)`
- Active nav pill: `0 5px 14px rgba(85,44,126,.42), inset 0 1px 0 rgba(255,255,255,.55)`
- Inset well: `inset 0 1px 2px rgba(58,31,82,.12)`
- Voice control: `0 10px 24px rgba(12,4,22,.4), inset 0 1px 0 rgba(255,255,255,.22)`

### Spacing
4 / 6 / 8 / 10 / 12 / 14 / 16 / 18 / 20 / 22px. Card padding 18px (ledger 20px 22px). Grid gaps 14–16px. Section heading bottom margin 18–20px.

---

## Shell

**Titlebar** (toggleable, default on): full width, `linear-gradient(180deg,#faf6fd,#ece0f2)`, 9px 18px, 12.5px/700 ink, bottom hairline. `school` icon + "REEP · STUDENT"; right-aligned "Sign out" pill with `logout` icon at 75% opacity.

**Sidebar**: fixed width, default **220px** (range 180–300). Contains, top to bottom:
1. Profile card — 18px radius, white, hairline: 88×106 avatar frame (`#efe4f6`, `person` glyph 52px `#a08cb2`), name "Asha Rao" 13px/800 centered.
2. Primary items: Home (`home`), Jobs (`work`), Skilling (`verified`), Leaderboards (`leaderboard`), Time Sheet (`schedule`).
3. Group **Programme**: English Baseline (`record_voice_over`), Mentor Meeting Log (`event_note`).
4. Group **Documents**: Uploads (`upload_file`), Resume (`description`).
5. Group **More**: Profile (`person`).

Nav item: flex row, `gap:12px`, `padding:10px 12px`, `border-radius:999px`, 13px/600, 2px bottom margin. Inactive `color:#5b3080`, transparent background. Active: primary gradient background, `#fff` text, active-pill shadow.

**Content area**: scrolls independently; page header is a `space-between` flex row (title block left, actions right, `flex-wrap: wrap`).

**Floating agent orb**: circular, fixed bottom-right on **every** screen, freely draggable via pointer events (clamped to the viewport, ~110px margin). Drag threshold 4px — a pointer-up under threshold is treated as a tap and opens voice mode. Position held in state (`bx`, `by`) as a `translate()`.

---

## Screens

Route keys: `landing · jobs · skilling · leaderboards · timesheet · certifications · courses · records · mentor · english · uploads · resume · agent · profile`. Default `landing`.

### 1. Landing (Home)
Greeting "Welcome back, Asha" (19px/900 Orbitron) with sub-line "Excel-Adv stage · Semester 2 · 1BG24MBA001"; right side a success chip (`local_fire_department`, streak). Below, a **3-column card grid** (`1fr 1fr 1fr`, gap 16px). Each card = 22px radius, card gradient, hairline, 18px padding, card shadow; heading 14px/700 Orbitron with a bottom hairline; then a vertical list of link rows.

Link row: `chevron_right` (16px, `#a08cb2`) + label (12.8px/600, `#7a2f9e`, `flex:1`) + status glyph — `check_circle` `#137a4a` (title "Completed") or `pending` `#8f6100` (title "In progress"). The "Reboot" card lists REE 101, REE 102, "English Baseline · AI" (this row navigates to the English screen).

### 2. Jobs
Tabbed: **Opportunities / Applications / Offers** (underline tabs — active `#3a1f52` text + 1px `#3a1f52` bottom border; inactive `#7a6392`, transparent border). Secondary **UG / PG** level toggle (active `#3a1f52` text + `#3a1f52` border; inactive `#7a6392` + `rgba(58,31,82,.12)` border). Offers tab has a collapsible "add offer" form (`offerForm` boolean).

### 3. Skilling
Badge/track cards with progress. Target of the English assessment's "next steps".

### 4. Leaderboards
Ranked table of peers.

### 5. Time Sheet — **Time Allocation Ledger** (the most detailed screen)
Header: eyebrow "Daily log · Semester 2"; title "Time Allocation Ledger"; sub "Six slots covering a 24-hour day · five activity heads · hours to the nearest half". Actions: a **date stepper** (12px-radius white control, `chevron_left` / `Thu · 20 Aug 2026` 12.5px/700 tabular-nums / `chevron_right`, 28×28 icon buttons with `#f5edfa` hover), a secondary **Copy yesterday** button (`content_copy`), and the primary **Submit day** button (`task_alt`, primary gradient).

**Metrics strip** (inside the ledger card, 4 equal columns separated by `1px rgba(160,138,178,.26)` left borders, 2px 22px padding):
| Metric | Value | Sub |
| --- | --- | --- |
| Day accounted (amber `#d99a00` dot) | `23.5 / 24 h` | "0.5 h to reconcile" in `#8f6100`/700 |
| Productive | `12.5 h` | "Lectures · coursework · skilling" |
| Waking utilisation | `78 %` | "of 16.0 h awake" |
| Rest | `8.0 h` | "Against an 8 h benchmark" |

Numbers: 27px/800 Orbitron, tabular-nums, unit suffix in Chakra Petch 14px/600 `#a08cb2`.

**Day band** ("Day at a glance", right caption "Chronological · 5 am to 5 am"): six flex tracks with flex weights `4 / 3 / 3 / 3 / 4 / 7` (proportional to slot duration), each a 22px-tall 6px-radius inset well containing proportional colour segments. Below, a tick row with the same weights: `5 am · 9 am · 12 pm · 3 pm · 6 pm · 10 pm`, each 10.5px `#a08cb2` with a left hairline. Unaccounted time is a hatch: `repeating-linear-gradient(135deg,rgba(143,97,0,.32) 0 4px,rgba(143,97,0,.1) 4px 8px)`.

**Legend** (above the table, wrapping flex, `gap:10px 20px`, 11.5px/600 `#4a2668`, 10×10 3px-radius swatches): Sleep `#b9a8c9` · Travel/personal `#d9c8e6` · Lectures `#7a2f9e` · Coursework `#552C7E` · Skilling `#BA2185` · Unaccounted (hatch, label in `#8f6100`).

**The table** — six slot rows × five activity columns, sized to fit ~660px without scrolling:
- Column heads: a coloured 22×3 999px-radius rule above a 9.5px/700 uppercase Orbitron label (`letter-spacing:.14em`, `#4a2668`).
- Row 1 col: slot time range + a small state chip (10px/700, 999px radius) beneath.
- Activity cells: 48px-wide numeric fields, 6px cell padding, tabular-nums, native number spinners suppressed.
- Last column **Mix**: fixed `width:120px`, a 10px-tall 999px-radius composition bar showing that slot's split.
- Zebra: odd `#fff`, even `rgba(250,246,253,.7)`; the final total row `rgba(85,44,126,.045)`. Row rules `1px solid rgba(160,138,178,.26)`.
- Dawn/night slots carry a leading icon.
- Six slots cover 5am→5am: 5–9am, 9am–12pm, 12–3pm, 3–6pm, 6–10pm, 10pm–5am.

### 6. English Baseline (AI)
CEFR **band dial** as the hero; four skill cards — Reading **B2**, Writing **B1**, Listening **B1+**, Speaking **pending**; then AI feedback with next steps that link through to Skilling.

### 7. Mentor Meeting Log
Chronological list of mentor meetings with notes.

### 8. Uploads
Document upload list with per-document state.

### 9. Resume Builder
Pill-tab switcher at top (primary-gradient active pill, 999px radius, 8px 16px, 12.8px/600). Sections include **Personal** with a 2-column field grid.

### 10. Profile
Student details.

### 11. REEP Agent (text)
Full-height flex column (`height:100%`), header block with 40px bottom margin, conversation area below.

### 12. Agent Voice (overlay, not a route)
`position: fixed; inset: 0; z-index: 100`, voice-overlay gradient + 8px backdrop blur. Top row: label left, close affordance right (max-width 900px). Centre: the orb, `30vh`. Below it the **waveform** — a centred row of 5px-wide 999px-radius bars, `linear-gradient(180deg,#e9c9f5,#BA2185)`, heights 26–92px, `max-height:100%`, container `clamp(40px,11vh,104px)`; each bar animates `rp-wave` with durations cycling 1.05/1.23/1.41/1.59/1.77s and staggered delays. Muted state replaces the waveform with a single 220×4 `rgba(255,255,255,.22)` rule at the same height.

Three 60×60 circular controls, `gap:18px`: **Mute** (`mic_off`) and **Type instead** (`keyboard`) are `rgba(255,255,255,.09)` on a `rgba(255,255,255,.22)` border, hover `rgba(255,255,255,.18)` + `translateY(-1px)`; **End session** (`call_end`) is `linear-gradient(135deg,#a0248f,#BA2185)`. Footer disclaimer, 11.5px `rgba(255,255,255,.6)`, `max-width:52ch`, centered: "REEP Agent answers on programme rules and deadlines. It does not see your marks, attendance or USN."

Scrollbars are suppressed inside this overlay; all sizing is viewport-relative so it never overflows a short window.

---

## Interactions & behaviour
- **Navigation** is local state, not URL routing in the prototype. In production wire each route key to a real route.
- **Agent orb**: `pointerdown` starts a drag; `pointermove` updates the translate, clamped to `-(window.innerWidth-110)`…`0` and `-(window.innerHeight-110)`…`0`; `pointerup` with <4px total movement opens voice mode. Listeners are attached to `document` and removed on release.
- **Escape** closes voice mode (global `keydown` listener added on mount, removed on unmount).
- **Mute** toggles the waveform to its muted rule. **Type instead** closes voice mode and navigates to the Agent text screen.
- **Hover** on nav/buttons: primary buttons deepen their shadow; secondary buttons go `#faf6fd` with a `rgba(122,47,158,.45)` border; icon buttons fill `#f5edfa`; voice controls lift 1px.
- **Animations**: `rp-wave` — `0%,100% { transform: scaleY(.16) } 50% { transform: scaleY(1) }`, `ease-in-out`, `infinite`, 1.05–1.77s, staggered delays. `rp-fade` — `opacity 0 → 1` with `translateY(8px) → none`, used for entrances.
- **Data states** — every data screen renders four states, driven by one `dataState` value:
  - `loading` — a plain 13px `#7a6392` line ("Loading today's time sheet…")
  - `empty` — empty-state copy in place of content
  - `error` — a 14px-radius `rgba(173,36,82,.1)` / `#ad2452` alert with an `info` glyph (e.g. "Could not load today's time sheet.")
  - `data` — the full screen
- **Responsive**: header rows wrap; ledger table is width-constrained rather than horizontally scrolled; voice overlay sizes off `vh` with `clamp()`.

## State
| Name | Type | Notes |
| --- | --- | --- |
| `route` | route key \| null | Falls back to the `screen` prop, then `landing` |
| `tab` | `opps` \| `apps` \| `offers` | Jobs tab |
| `level` | `UG` \| `PG` | Jobs level filter |
| `offerForm` | boolean | Offer form disclosure |
| `voice` | boolean | Voice overlay open |
| `muted` | boolean | Mic muted |
| `bx`, `by` | number | Orb drag offset in px |

Configuration exposed as props on the prototype (make these real settings/route params/feature flags as appropriate): `screen` (initial route), `dataState` (`data|loading|empty|error` — a demo affordance, replace with real fetch state), `showTitlebar` (boolean), `navWidth` (180–300px, default 220).

**Data fetching**: the prototype has none — all content is static. Production needs endpoints for: dashboard programme progress, jobs/applications/offers, skilling tracks, leaderboard, the day's timesheet (read + save + "copy yesterday" + submit), English baseline results, mentor meetings, uploads, resume data, profile, and an agent conversation/voice channel.

## Validation rules to implement (implied by the design, not enforced in the prototype)
- Timesheet hours to the nearest **0.5 h**; per-slot total must not exceed slot capacity; day total must reconcile to 24 h before **Submit day** is allowed (the "0.5 h to reconcile" line is the nudge).
- "Copy yesterday" prefills all six slots from the previous day's submitted ledger.

## Assets
No image files. All iconography is **Material Symbols Rounded** (Google Fonts) — substitute the codebase's existing icon set if it has one, keeping the same glyph semantics. All fonts are Google-hosted; self-host in production. Avatars are a glyph placeholder — wire to the real student photo.

## Files in this bundle
- `REEP Student App.dc.html` — the current, canonical design. Open in a browser to view.
- `REEP Student App Y2K.dc.html` — earlier, more heavily chromed exploration. **Reference only** — superseded; do not build from it.
- `support.js` — prototype runtime only. **Do not port.**
