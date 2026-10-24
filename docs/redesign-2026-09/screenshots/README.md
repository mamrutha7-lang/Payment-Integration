# Shell screenshots — Phase 0, before and after

Eight images, four roles, one pair each. `*-before.png` is the shell as it
stands on `main`; `*-after.png` is the shell this branch ships.

## How they were made, and what that means for reading them

These are **not** screenshots of the running application. Both sides are static
HTML rendered against the REAL compiled stylesheet and the REAL self-hosted
fonts, in headless Chromium at 1440x900:

- the *after* side against `apps/web/src/styles.scss` as compiled from this
  branch, with the shell's own markup;
- the *before* side against the same file compiled from `origin/main`, with
  `app-shell.component.html`'s markup from `origin/main` transcribed into plain
  HTML (the `@switch (navKind())` arms become four pages).

That is a deliberate choice and it has a cost worth stating. Rendering the real
app would need a signed-in session per role and four screens that mostly do not
exist yet — Phase 0 builds the shell, not the boards. What these images prove is
what Phase 0 changed: the chrome, the navigation, the tokens and the type. What
they do NOT prove is that any particular screen renders, and they must not be
read as evidence of that.

The fonts matter here and are the reason both sides are served over HTTP rather
than opened from disk. `@font-face` with a root-relative `/fonts/` URL does not
resolve under `file://`, and the icon face renders from LIGATURES — so a missing
icon font does not show a fallback glyph, it shows the literal word `leaderboard`
in the sidebar, which reads as a layout bug that is not there. The `.icon` gate
is `html.fonts-ready`, not `body`, which is its own way to get four blank
sidebars.

## What to look for

**The title bar became an app bar.** `main` has a 28px strip carrying
`REEP · ADMIN` and two text links. The design's app bar is a 52px band with the
brand mark, the environment pill, search, the scope selector, notifications,
help and an account menu — the admin's, at least; a student's carries their
identity and a visible Sign out, because a student signing out should not have
to find a menu first.

**The admin sidebar grew groups.** One flat list of fifteen links became six
labelled groups. Four rows in those groups are not clickable and say "Soon":
Colleges, Faculty, Data imports and Audit log arrive in Phase 2. An unbuilt
screen shown as a labelled dead row is the deliberate choice — the alternative,
omitting it, makes the sidebar change shape every fortnight.

**The faculty sidebar gained "Granted access".** That group is rendered from
the session's capabilities. It is a filter and not a gate: the API's
`require_capability` is what refuses the request.

**The type changed weight, not family.** Both sides are already Plus Jakarta
Sans over Inter — that pair landed with design-v4, before this branch.
