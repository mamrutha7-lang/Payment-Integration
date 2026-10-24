# The REEP Codebase Bible

> **HISTORICAL RECORD — written against the codebase as it stood in 2026-08, and
> NOT maintained since. `AGENTS.md` is the current truth; where this book and
> that file disagree, this book is wrong.**
>
> It is kept because the *reasoning* in it is still the reasoning of this
> codebase, and because a 270,000-word account of why each decision was made is
> not reproducible by reading the diff. What it describes that no longer exists:
>
> - the **LiveKit voice stack** — `voice_agent.py`, `requirements-voice.txt`,
>   `.venv-voice`, `app/routers/voice.py`, `chat-voice.service.ts` and the
>   `worker-imports` CI job (removed 2026-09; ~280 references in here);
> - the **DIRECTOR role** — `require_director`, `/api/director/*`,
>   `features/director/`, `director@bgscet.ac.in` (removed 2026-09-10; ~117
>   references);
> - the **`openai` interview engine** — `app/interview_relay.py` (removed
>   2026-09; the design record that outlived it is
>   `docs/interview-engine-v3.md`);
> - `ThemeService`, `IconComponent` and four of the five `shared/kit/`
>   components (removed 2026-09 in the redesign's cleanup phase).
>
> Chapter 11 ("Voice") is about a subsystem that no longer exists at all. Read it
> as an account of a design that was tried and replaced, never as a map.

An exhaustive, teaching-first account of the REEP stack: what every module does, why
every line reads the way it does, which rule each piece of code exists to protect, and
the naming conventions that keep 34,000 lines legible.

**Fifteen chapters, 270,277 words, complete.** Chapters 15 and 16 were merged into one closing
volume covering both the rulebook and the runbooks.

This is written to be *read in order* by someone who has never opened the repo, and
*grepped out of order* by someone who has. Every chapter cites real files and real
symbols — `path/to/file.py:120` — so nothing here is a paraphrase you cannot check.

## How this book was built

Each chapter is produced by a **council**: a fan-out of independent reader agents that
map the code, a drafter that writes from those maps, a panel of judges that scores the
draft against a fixed rubric, and a reviser that rewrites against the panel's critique.
Every agent reasons privately (chain-of-thought) before answering, then self-scores and
revises — a reward loop applied to prose. A final verifier re-opens every file cited and
confirms each symbol, line number and claim actually exists. Chapters that fail
verification are rewritten, not shipped with a caveat.

## Reading order

| # | Chapter | Covers |
|---|---------|--------|
| 1 | [The Stack, End to End](01-stack-architecture.md) | The four processes, topology, ports, request lifecycle, trust boundaries, the two inviolable rules |
| 2 | [Backend Core](02-backend-core.md) | `db.py`, `document_store.py`, `mailer.py`, `resume_pdf.py`, schemas, error/logging conventions, the backend naming rulebook |
| 3 | [The Data Model](03-data-model.md) | Every model in `app/models/`, enums, relationships, table by table |
| 4 | [Migrations & Alembic](04-migrations.md) | All 40 revisions, the enum gotchas, ordering and autogenerate discipline |
| 5 | [Auth & RBAC](05-auth-rbac.md) | scrypt hashing, HS256 sessions, the cookie, `require_*`, `_assert_can_access_student` (Rule 2) |
| 6 | [The Student API](06-api-student.md) | All 49 student-reachable endpoints: `student.py`, `registration.py`, `leave.py`, and the rule engines behind readiness, next-actions and leaderboards |
| 7 | [HTTP API — Staff & Machine Surface](07-api-staff.md) | `mentor`, `director`, `agent`, `voice`, `health` routers |
| 8 | [The AI Layer](08-ai-layer.md) | `ai/llm.py`, the egress gate (Rule 1), `agents.py`, `orchestrator.py`, `adk.py`, `assistant_tools.py` |
| 9 | [Conversations, Memory & Governance](09-conversations-memory.md) | `conversations.py`, `memory.py`, `eval/`, `retention.py`, `redaction.py`, feedback |
| 10 | [Vector DB & the Knowledge Base](10-vector-kb.md) | pgvector, `ai/embeddings.py`, hybrid retrieval in `knowledge.py`, `seed_kb.py` |
| 11 | [The Voice Assistant](11-voice.md) | `routers/voice.py`, `voice_agent.py`, the state machine, consent, transcripts, heartbeats |
| 12 | [Frontend Architecture](12-frontend-architecture.md) | Bootstrap, lazy routes, `core/` services, guards, the shell, signals |
| 13 | [Frontend Features](13-frontend-features.md) | Every feature component, the resume builder, the assistant UI |
| 14 | [The Design System](14-design-system.md) | `reep-v2.scss`, the kit, `tone.ts`, charts, the colour-plus-text rule |
| 15 | [Conventions, Rules and Running It](15-conventions-rules-ops.md) | The complete naming compendium and rulebook, plus docker, env vars, the two requirements files, the two seeds, the suite, CI and every runbook |

Open questions and confirmed defects the council turned up while writing are tracked in
[FINDINGS.md](FINDINGS.md). Readers flag what they cannot verify rather than inventing it,
and some of those flags were real.

## Status

| # | Chapter | Status |
|---|---------|--------|
| 1 | The Stack, End to End | **done** — 9,980 words, 161 citations, 3 diagrams; 41 defects raised by the panel and fixed, 196 references verified |
| 2 | Backend Core | **done** — 14,189 words, 10 sections, 4 diagrams; 49 defects raised, 49 fixed, 4 judge claims rejected on evidence, 190 references verified |
| 3 | The Data Model | **done** — 16,501 words, 35 subsections, 255 table rows, 3 ER diagrams; 52 defects raised, 52 fixed, 3 judge claims rejected on evidence, 287 references verified |
| 4 | Migrations & Alembic | **done** — 15,314 words; all 40 revisions charted, the three enum gotchas explained against the migrations that prove them; 49 defects raised, 49 fixed, 4 judge claims rejected on evidence, 147 references verified |
| 5 | Auth & RBAC | **done** — 14,708 words; Rule 2 audited endpoint by endpoint and found clean; 41 defects raised, 39 fixed, 3 rejected after re-verification, 268 references verified |
| 6 | The Student API | **done** — 20,630 words; 45 defects raised, 45 fixed, **423 references verified** (the largest verification pass in the book) |
| 7 | The Staff & Machine API | **done** — 21,130 words, the longest chapter yet; 42 defects raised, 41 fixed, 1 rejected after re-verification, 237 references verified |
| 8 | The AI Layer | **done** — 14,706 words; every Rule 1 call site audited and tabulated; 42 defects raised, 39 fixed, 3 judge claims rejected after the reviser re-checked the source, 163 references verified |
| 9 | Conversations, Memory & Governance | **done** — 14,727 words; 45 defects raised, 45 fixed, 3 judge sub-claims rejected after the reviser recomputed them, 215 references verified |
| 10 | Vector DB & the Knowledge Base | **done** — 14,251 words; 40 defects raised, 38 fixed, 2 rejected after the reviser re-ran the query and checked the seed path, 235 references verified |
| 11 | The Voice Assistant | **done** — 16,790 words, 57 subsections; 44 defects raised, 44 fixed, 2 judge claims rejected on evidence, 158 references verified |
| 12 | Frontend Architecture | **done** — 19,005 words, 4 diagrams; 39 defects raised, 39 fixed, 5 judge claims rejected after the reviser re-checked the source, 238 references verified |
| 13 | Frontend Features | **done** — 23,425 words, 12 sections; 43 defects raised, 40 applied, 3 rejected on evidence, 241 references verified |
| 14 | The Design System | **done** — 21,539 words, 62 subsections; the colour-plus-text rule audited across every template and **four real violations found** |
| 15 | Conventions, Rules and Running It | **done** — 31,197 words, 13 sections; the complete rulebook, the env reference, the suite, and every runbook |

> Chapters 6 and 7 were interrupted mid-review by a session quota limit and later resumed
> from cache — the surveys and drafts replayed free, and only the lost judge, revise and
> verify agents re-ran. Both have now been through the full reward loop, and every chapter
> in this book meets the same bar.

