---
name: Agent task
about: A change small and clear enough to hand to Claude. Add the `agent` label (or comment @claude) and it opens a PR.
title: ""
labels: ""
assignees: ""
---

<!--
  The agent does exactly what this issue says and nothing it does not, so the
  quality of the PR is the quality of these four sections. Add the `agent`
  label when it is ready; that starts .github/workflows/claude.yml.

  Not for: anything needing a production credential, a migration that rewrites
  existing rows, an infra/ deploy, or a decision only a human can make. Those
  still go to a person — the agent will stop and say so if it meets one.
-->

## What should change

<!-- One or two sentences, from the user's side: who sees what, where. -->

## Where

<!-- Screens, routes, files or endpoints you already know are involved. "Not sure" is fine. -->

## Done when

<!-- Checkable statements. Each should be something a test or a click can prove. -->
- [ ]
- [ ]

## Rules this touches

- [ ] Student data reaches a model / mail / log (rule 1)
- [ ] A new staff read of a student's records (rule 2)
- [ ] A model or migration
- [ ] None of the above
