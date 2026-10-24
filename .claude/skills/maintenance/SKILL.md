---
name: maintenance
description: REEP's weekly upkeep sweep — outdated and vulnerable dependencies, stale PRs and issues, AGENTS.md drift — reported in one issue, with at most one small safe PR.
---

# Weekly maintenance

Report measurements, never recollections. Every section says "could not
measure" when its input is missing or empty.

## 1. Dependencies (from the MEASUREMENTS directory)

- `pip-audit.json`, `npm-audit.json`: every known vulnerability with its
  package, installed version, fixed version and severity. These come first.
- `pip-outdated.json`, `npm-outdated.json`: group by patch / minor / major.

## 2. Repository hygiene

- Open PRs with no activity for 14+ days: `gh pr list --state open`.
- Open issues labelled `incident`, `infra-drift` or `release` older than 7
  days — these are production signals nobody closed.
- AGENTS.md drift: pick three specific claims in it (a file path, a function
  name, a count) and check them against the code. Report any that are false.

## 3. One report issue

Create or update the open issue labelled `maintenance` titled
"Weekly maintenance — <date>" with sections 1 and 2, most urgent first
(a vulnerability with a fix available beats everything).

## 4. At most one PR

Only if a vulnerability has a PATCH-level fix: bump exactly that package.
- Python: `apps/api-py/requirements.txt` stays pinned `==` and runtime-only.
- Node: update `apps/web/package.json` AND regenerate the lockfile with
  `npm install` — never edit a lockfile by hand.
Run the checks in `.claude/skills/ship/SKILL.md`, open a DRAFT PR titled
`chore(deps): <package> <old> -> <new> (<advisory id>)` linking the report.
Minor or major upgrades are listed in the report for a human, not bumped.
Never commit the measurements directory. Never deploy.
