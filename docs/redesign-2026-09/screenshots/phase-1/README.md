# Phase 1 — the seven student screens, as built

Eight images: the seven screens 06-phase-prompts.md lists, plus sign-in at
390px. Unlike the Phase 0 set beside them, **these are the real running app** —
Angular against the real API against a seeded Postgres, driven over CDP as the
seeded student, captured at 1440px wide.

Compare each against its board in `../../design/student/`:

| screenshot | board |
|---|---|
| `01-signin.png`, `01-signin-390.png` | `LoginRedesign.png`, `LoginMobile.png` |
| `02-time-sheet.png` | `TimeSheetRedesign.png` |
| `03-leaderboards.png` | `LeaderboardsRedesign.png` |
| `04-faculty-tpo-log.png` | `MentorLogRequest.png` |
| `05-skilling.png` | `SkillingRedesign.png` |
| `06-reep-agent.png` | `AgentRedesign.png` |
| `07-resume-builder.png` | `ResumeRedesign.png` |

## What the data means for reading them

The dev database holds **one student, one mentor, one admin, one alumnus and
one batch**. So Leaderboards shows a single ranked row and Faculty / TPO Log a
single meeting — those are the honest empty-ish states of a seeded database, not
screens that failed to load. The boards were drawn against the same seed, which
is why the numbers agree: "1 of 1", "3 skills", "23.5 / 24 h", "2 skills
currently illuminated".

Skilling and Resume Builder are captured taller than the window, because the
board for each is a full-page composition and cropping it at the fold would hide
the half that changed.

## What they do NOT show

The seven screens are restyles. No endpoint changed — `git diff origin/main..
-- apps/api-py/` over this branch is empty — so nothing here is evidence about
the backend. Nor are the mock-interview screens in this set: they are
plan-driven (03 §7-§8) and belong to Phase 4.
