# v2 UI design handoff — REEP Student App

The source of truth for the current student-facing look. Two files:

- **`HANDOFF.md`** — the handoff document itself: the token table (colour, type,
  radius, shadow, spacing), the shell, all fourteen screens, interactions,
  state, and the validation rules the design implies but does not enforce. This
  is the spec; where the implementation and this document disagree, this
  document is right and the implementation is a bug.
- **`student-app.dc.html`** — the canonical prototype. **A design reference
  authored in HTML, not production code.** Open it in a browser to view.

## What was NOT copied, and why

The prototype is a single-file format: `<x-dc>` wraps the template, a
`<script data-dc-script>` block holds React-class-like logic, `<sc-if>` /
`<sc-for>` are its conditional and list renderers, and **all styling is inline
by necessity of that format**. None of that is architecture. The handoff says so
explicitly, and it is reproduced here as a reference rather than a source:

- the runtime (`support.js`) is **not** in this repo and must not be ported;
- inline styles were extracted into real tokens and component classes in
  `apps/web/src/styles/reep-v2.scss`;
- `<sc-if>` / `<sc-for>` became Angular's `@if` / `@for`;
- navigation, which the prototype held in local state, is real routing.

The earlier, more heavily chromed `REEP Student App Y2K.dc.html` exploration is
**superseded** and deliberately not kept here — carrying a second, contradictory
reference is how two screens end up built from two different designs.

## Implementation status

Rebuilt to this spec: the shell (titlebar, sidebar profile card, grouped nav,
active pill), the floating agent orb and voice overlay, the landing stage cards,
the Time Allocation Ledger, the English Baseline and the Mentor Meeting Log.

Re-skinned through the global token layer, not rebuilt screen by screen: jobs,
skilling, leaderboards, certifications, courses, records, uploads, resume
builder, profile, assistant, login and register. They pick up the palette, type,
radii and shadows, and they use the shared component classes — but their layouts
are the ones they already had, not the handoff's. Bringing each to pixel
fidelity is per-screen work that has not been done.
