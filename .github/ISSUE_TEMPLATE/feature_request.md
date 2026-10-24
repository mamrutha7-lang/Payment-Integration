---
name: Feature request
about: A change to what REEP does. Say who it is for and what they cannot do today.
title: ""
labels: enhancement
assignees: ""
---

## Who is this for, and what can they not do today?

<!--
  Name the role. In this codebase the role IS the design: the same screen behind
  require_mentor shows a mentor only their own group and a director the whole cohort,
  and getting that wrong is rule 2, not a UI detail.
-->

**Role:** STUDENT / MENTOR / DIRECTOR / ADMIN / ALUMNI

**Today they have to:**

**Instead they should be able to:**

## What would it touch?

Tick what you already know. "Not sure" is a fine answer — leave a box untouched and say
so; a guess here sends the work in the wrong direction more expensively than a blank.

- [ ] A new or changed **screen** in `apps/web/src/app/features/…`
- [ ] A new or changed **endpoint** in `apps/api-py/app/routers/…`
- [ ] **New tables or columns** — which means a model under `app/models/`, imported in
      `app/models/__init__.py` so autogenerate sees it, and a revision in
      `apps/api-py/migrations/versions/`
- [ ] **A model call** — an LLM writes, scores or summarises something
- [ ] **A new dependency**

## The two rules

Both are invisible in a diff unless someone volunteers them, which is why they are asked
here and not at review time.

- [ ] **Rule 1 — student data must not leave the machine unbidden.** Does this send a
      student's own records (name, USN, marks, attendance, a resume brief) to a model? If
      yes it must go through `complete_chat(..., carries_student_data=True)` or
      `stream_chat(...)` in `apps/api-py/app/ai/llm.py`, and it must still work when the
      gate REFUSES — `/student/resume/generate` composes the resume deterministically and
      says `used_ai=false`. Describe the refused path here; a feature with no answer for
      it is a feature that breaks on every default deployment. Public data (a job posting,
      approved KB policy text) does not need the gate.
- [ ] **Rule 2 — staff scope is decided by role, not a missing field.** Does this let one
      person read a row belonging to another student? If yes it goes through
      `_assert_can_access_student` in `app/routers/mentor.py` — imported, never
      reimplemented — and a MENTOR with no `Mentor` group must see NOBODY, never the whole
      programme.

## If it needs a dependency

Say which manifest, because the split is enforced by CI rather than by convention:

- `apps/api-py/requirements.txt` — **runtime only**, pinned `==`. It is what the Dockerfile
  installs, and the "API (dependency completeness)" job imports every module under `app/`
  against this file ALONE. A lazy import inside a request handler does not exempt it; it
  only moves the crash from boot to the first student who reaches that path.
- `apps/api-py/requirements-dev.txt` — test-only. A test runner has no business in a
  production image.
- `apps/web/package.json` — the Angular build enforces a **bundle budget**. Initial is
  ~419 kB (framework included) against a 610 kB cap, so a heavy library reaching an
  eager path fails `ng build` in CI rather than shipping quietly.

## What would it deliberately NOT do?

<!--
  The scope you are cutting on purpose. Writing it down here is what stops it being read
  six months later as an oversight and re-added by someone who was not in this
  conversation.
-->
