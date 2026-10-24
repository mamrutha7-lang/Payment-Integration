> **STALE — raised against the tree as it stood in 2026-08, and several of the
> things they are findings ABOUT no longer exist.** Every `voice_agent.py`,
> `app/routers/voice.py` and LiveKit reference below points at code removed in
> 2026-09; DIRECTOR was removed as a role on 2026-09-10. A finding whose
> subject is deleted is not a finding — do not re-open one from this list
> without checking the path still exists. Companion to the book in this
> directory, which carries its own banner for the same reason.

# Open findings raised while writing the Bible

The council's readers flag anything they cannot verify rather than inventing a detail.
Several of those flags turned out to be real defects or real drift in the repo. This file
tracks them so they are not lost between chapters. Nothing here has been changed in the
code — these are reports.

## Confirmed defects

### CI cannot run the pgvector migration — `Apply migrations` should be failing

`.github/workflows/ci.yml:26` provisions the `postgres` service from **`postgres:17`**,
stock. But `apps/api-py/migrations/versions/b7e2f4a19c33_kb_embedding_pgvector.py:37`
executes:

```python
op.execute("CREATE EXTENSION IF NOT EXISTS vector")
```

`IF NOT EXISTS` suppresses "extension already exists". It does **not** help when the
extension is not installed on the server at all: stock `postgres:17` has no
`vector.control`, so the statement raises. CI runs `python -m alembic upgrade head` as a
required step (`ci.yml:68-70`), so that step fails, and every DB-backed test behind it
never runs — with `REEP_REQUIRE_DB: "1"` set specifically so skipped DB tests are a hard
failure.

The migration's own docstring says it "Requires the `pgvector/pgvector:pg17` docker image",
which is what `docker-compose.yml` uses and what `docker/initdb/01-create-reep-py.sh:27`
relies on. CI is the one place that was not updated when pgvector landed (commit
`87d3981`, one day before this was written).

**Fix:** one line — `image: pgvector/pgvector:pg17` in `.github/workflows/ci.yml:26`.

### A non-Latin-1 upload filename uploads fine and 500s on download

`POST /api/student/uploads` stores the client's filename verbatim —
`original_name=file.filename or stored_name`
([routers/student.py:1380](../../apps/api-py/app/routers/student.py#L1380)) — and the download
handler interpolates it straight into a response header
([routers/student.py:1411](../../apps/api-py/app/routers/student.py#L1411)):

```python
return Response(
    content=content,
    media_type=upload.mime_type,
    headers={"Content-Disposition": f'inline; filename="{upload.original_name}"'},
)
```

Starlette encodes header values as **latin-1**. A filename outside that range — Kannada,
Hindi, or an emoji — raises `UnicodeEncodeError` inside `Response.__init__`, before any
handler code can catch it, so the request dies as an unhandled 500. The file uploaded
successfully; only fetching it back fails. At a Bengaluru college a student naming a file
`ಪ್ರಮಾಣಪತ್ರ.pdf` is not a hypothetical.

The same line was first suspected of being a header-injection hole (a CRLF in the filename
splitting the response). It is not, and the reason is worth knowing: Starlette will encode
the CRLF into `raw_headers` without complaint, but uvicorn's h11 layer then rejects the
response with `LocalProtocolError('Illegal header value')`. Response splitting is blocked by
a dependency, not by this code — the defence is real but borrowed, and it would evaporate
under a different server. The encoding crash is the live defect.

**Fix:** RFC 6266 encoding — an ASCII-sanitised `filename=` plus `filename*=UTF-8''<percent-encoded>`.

The sibling site at [routers/student.py:1059](apps/api-py/app/routers/student.py#L1059) builds
its filename server-side, so it is not exposed the same way.

### The egress gate is case-insensitive; the comments say it is not

`app/config.py:113` is:

```python
return self.llm_allow_remote_student_data.strip().lower() == "true"
```

So `TRUE`, `True` and ` true ` all unlock remote student-data egress. The test comment at
`tests/test_egress_gate.py:32` says "Only the exact string `"true"`", and `AGENTS.md`
documents `LLM_ALLOW_REMOTE_STUDENT_DATA=true`. The behaviour is *more* permissive than
documented, not less. Not a hole — the gate still defaults closed and still requires an
affirmative value — but the comment is wrong about the mechanism, and a reader hardening
this would trust the comment.

### Four places convey status by colour alone, breaking the rule AGENTS.md states

`AGENTS.md` says status is *"always shown as **text + colour together**, never colour alone"*.
Chapter 14's council audited every template rather than restating the claim, and found four
places where colour is the only carrier of meaning. None is exotic — all four are ordinary
screens a student uses every week.

| # | Where | What only colour says |
|---|-------|----------------------|
| 1 | [resume-builder.component.html:41-59](apps/web/src/app/features/student/resume/resume-builder.component.html#L41-L59) + [reep-v2-resume.scss:134-149](apps/web/src/styles/reep-v2-resume.scss#L134-L149) | Which resume-builder steps are complete — the step dots differ only by fill |
| 2 | [student-overview.component.html:225-230](apps/web/src/app/features/student/overview/student-overview.component.html#L225-L230) | Which days the student logged in — the streak cells carry no text or label |
| 3 | [time-log.component.html:54-73](apps/web/src/app/features/student/time-log/time-log.component.html#L54-L73) + [.scss:44](apps/web/src/app/features/student/time-log/time-log.component.scss#L44) | Which activity each segment of the stacked bar represents |
| 4 | [app-shell.component.html:16-57](apps/web/src/app/layout/app-shell.component.html#L16-L57) | Which navigation item is the current page |

**Why it matters here specifically.** Roughly 1 in 12 men has a colour-vision deficiency, so in a
cohort of 1,000 students this is dozens of people. Placement reports get printed in monochrome
and screenshotted. And a nav bar whose current item is colour-only is a wayfinding failure, not
a cosmetic one.

The rule itself is sound and mechanised well elsewhere — `.chip` always carries a label, and
progress meters pair the fill with a number plus `role="progressbar"`. These four are gaps in
application, not in the design system.

The sharpest detail: **the rule is quoted in comments inside the very files that break it**,
twice over. Someone wrote the rule down and then, in the same file, shipped a violation.

**Fix:** each needs a text or ARIA carrier — a step label or `aria-current` on the dots, a
`title`/visually-hidden date on the streak cells, a legend or `aria-label` per bar segment, and
`aria-current="page"` on the active nav item.

**Not a violation, though an earlier draft claimed it was:** the assistant's live-audio stage
dot. The council re-checked and found it already pairs its colour with text, then corrected its
own chapter rather than leaving the false positive standing.

## Project state: features whose halves do not meet

These are not bugs. They are places where reading the code alone would badly mislead you
about what the running product does, so they belong in the book.

### The alerting feature has no producer

The alerts surface looks finished from every angle. There is an `Alert` model and an
`AlertRuleConfig` model ([models/alert.py:39,59](apps/api-py/app/models/alert.py#L39)), a
migration for each, director endpoints that list and upsert rule configs
([director.py:224-270](apps/api-py/app/routers/director.py#L224-L270)), mentor endpoints that
list and resolve alerts, and three seeded default rules —
`NO_CHECKIN_N_DAYS`, `ATTENDANCE_BELOW_THRESHOLD`, `CERT_OVERDUE`
([seed.py:536-538](apps/api-py/app/seed.py#L536-L538)).

Nothing evaluates any of it. Repo-wide, the only place an `Alert` row is ever constructed is
`seed.py:160`. There is no scheduler, no background job, no evaluation pass — grep finds no
`apscheduler`, no `celery`, and no cron entry that would run one.

So an administrator can configure a threshold and nothing will ever act on it, and the
mentor's `/alerts` queue shows only rows the dev seed planted. Since `python -m app.seed`
**refuses to run when `ENV=prod`**, on a production host that queue is permanently empty and
always will be. Anyone extending this needs to know they are writing the engine, not wiring
up an existing one.

### The mentor and director UIs do not exist yet

Every staff route in the Angular app is a placeholder:

```ts
// --- mentor ---
placeholder('mentor', 'Cohort'),
placeholder('mentor/student', 'Students'),
placeholder('mentor/alerts', 'Alerts'),
…
// --- director ---
placeholder('director', 'Analytics'),
```

([app.routes.ts:134-161](apps/web/src/app/app.routes.ts#L134-L161)) — only the two
`*/assistant` routes resolve to real components. And a grep across `apps/web/src` for
`/api/mentor` or `/api/director` returns **zero** hits.

The staff backend documented in Chapter 7 — all 13 endpoints of `mentor.py` plus the whole of
`director.py` — currently has no client. It is reachable only by direct HTTP. This is worth
stating plainly because `AGENTS.md` explains the lazy-route rule partly in terms of "not
downloading the mentor and director UIs", which reads as though they are built.

### `LeaderboardOut` ships peers' internal ids

`/api/student/leaderboards` includes a `student_id` for every cohort peer in the response.
The Angular row interface does not declare the field, so it appears to be sent and ignored.
Exposing other students' primary keys to a student client is not obviously intended; whether
it is deliberate or vestigial is not settled anywhere in the code. Flagged rather than
asserted.

### The retention sweep has no scheduler — nothing ever runs it

`app/retention.py` implements a complete three-stage data lifecycle: `purge_expired`
([retention.py:41](apps/api-py/app/retention.py#L41)) soft-deletes a conversation whose
`retention_until` has passed, scrubs its messages through `redact_pii`, and eventually hard-
deletes it; `redact_expired_runs` ([retention.py:107](apps/api-py/app/retention.py#L107))
walks the `AgentRun` audit trail and replaces the free text of aged runs while keeping every
metrics field. It is careful, idempotent work, and it is covered by 233 lines of tests.

Nothing calls it. A grep across the entire repository — `app/`, `voice_agent.py`,
`.github/workflows/`, both compose files, every Dockerfile, `docs/` — finds exactly one
caller: `tests/test_retention.py`. There is no endpoint, no cron, no compose service, no CI
step. There is no `if __name__ == "__main__"` block and no `argparse`, so even
`python -m app.retention` does nothing. No runbook in the repo mentions `purge_expired` or
`redact_expired_runs`.

The consequence is sharper than the alerting gap above, because this one is a data-protection
promise rather than a feature. Every conversation is stamped with an expiry at creation:

```python
retention_until=now + timedelta(days=RETENTION_DAYS),
```

([conversations.py:60](apps/api-py/app/conversations.py#L60)) — a deletion date that nothing
enforces. Student chat messages and voice transcripts accumulate indefinitely. The codebase
even reasons as though the sweep runs: a comment at
[voice.py:444](apps/api-py/app/routers/voice.py#L444) discusses what `retention.purge_expired`
"would not re-scrub", planning around a process that never starts.

**Fix:** the mechanism is finished and tested — it needs an entry point and a schedule. A
`if __name__ == "__main__"` block calling both functions, plus a cron entry or a compose
sidecar, would close it.

Note that `redact_pii` itself IS wired on the live path: `routers/agent.py:40` imports it and
applies it to feedback notes before they are stored. It is only the *aged-data* redaction,
which lives inside the unscheduled sweep, that never runs.

## Design risks (correct today, structurally fragile)

### The voice channel is outside Rule 1's enforcement mechanism

Rule 1 is enforced inside the universal adapter: `complete_chat`/`stream_chat` consult
`student_data_egress_allowed(cfg.base_url)` whenever `carries_student_data=True`
([llm.py:130,173](apps/api-py/app/ai/llm.py#L130)). Every HTTP-side call site is accounted
for, and the two that carry student records set the flag —
`orchestrator._finalize` ([orchestrator.py:572](apps/api-py/app/ai/orchestrator.py#L572),
commented "local/allowed only — gate re-checks") and `generate_resume`
([student.py:970](apps/api-py/app/routers/student.py#L970)).

The voice worker never enters that code path at all. It builds its model directly from the
LiveKit plugin:

```python
stt=groq.STT(model=GROQ_STT_MODEL),
llm=groq.LLM(model=GROQ_LLM_MODEL, temperature=0.6, max_completion_tokens=220),
```

([voice_agent.py:681,686](apps/api-py/voice_agent.py#L681)) — no `complete_chat`, no
`llm_config()`, no gate consultation. Voice always reaches Groq, a remote provider, by
design; it requires `GROQ_API_KEY` to run at all.

**This is not a live breach.** What keeps student records out of that prompt is that none are
ever put there: `BASE_INSTRUCTIONS` explicitly tells the model *"You cannot see this student's
marks, attendance, CGPA or any other record from their dashboard — those are not available to
you"* ([voice_agent.py:167](apps/api-py/voice_agent.py#L167)), and no grounding code injects
any. The student's own spoken words do go to Groq, but by this codebase's own reasoning
(`agent.py:15-16`) the caller's own utterances are not injected records — the same position
`/api/agent/chat` takes.

The risk is **future, and the gate cannot cover it**. "Why can't the voice assistant tell me my
attendance?" is an obvious feature request, and satisfying it means putting a student record
into a prompt on a code path that has no gate to refuse it. The protection here is a prompt
instruction plus the absence of a feature — not the mechanism that protects everywhere else.
Anyone adding grounding to voice must route it through `complete_chat(..., carries_student_data=True)`
or replicate the gate deliberately.

### The role guards are not dependencies, and not in `identity.py`

`AGENTS.md` describes "`require_*` dependencies in `apps/api-py/app/identity.py`". Both halves
of that are wrong, and the difference matters.

`app/identity.py` contains exactly one function, `get_current_session`. The role guards live in
the routers that own their area — `require_mentor`
([mentor.py:31](apps/api-py/app/routers/mentor.py#L31)), `require_director`
([mentor.py:233](apps/api-py/app/routers/mentor.py#L233)), `require_voice_worker`
([voice.py:65](apps/api-py/app/routers/voice.py#L65)).

More importantly, `require_mentor` and `require_director` are **not FastAPI dependencies**.
They are plain functions taking an already-resolved session dict, and they only run if the
handler body calls them:

```python
def mentees(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[MenteeOut]:
    require_mentor(session)
```

The only *declared* dependency is `get_current_session`, which authenticates but does not
authorise. So an endpoint that omits the one-line call is reachable by **any authenticated
user, including a STUDENT** — and it will look completely normal in review, because the
`Depends(...)` line that appears to guard it is still there.

Audited today, all 13 endpoints in `mentor.py` call their guard as the first statement, and
every list endpoint repeats the scope idiom correctly. The code is right. But nothing
*makes* it right: `dependencies=[Depends(require_mentor)]` on the router or the route would
make omission impossible, whereas the present shape makes it a one-line oversight. This is
the single highest-value hardening available in the codebase.

### How Rule 2 is actually kept (worth copying exactly)

`_assert_can_access_student` ([mentor.py:72-84](apps/api-py/app/routers/mentor.py#L72-L84))
calls `require_mentor` itself, so it is a complete guard rather than only a scope narrower —
endpoints that call it need no separate role check. It raises **404, not 403**
("Student not in your mentor group."), so a mentor cannot probe which student ids exist
outside their group.

List endpoints cannot use it — they return rows for many students — so each repeats the same
four-line idiom instead:

```python
if session["role"] == "MENTOR":
    mentor_id = session.get("mentorId")
    if not mentor_id:
        return []  # no Mentor group => nobody (never the whole programme)
    query = query.where(Student.mentor_id == mentor_id)
```

Verified present and identical at `/mentees`, `/alerts`, `/uploads/pending` and
`/skill-claims/pending`. The empty-list branch is Rule 2's whole point: the tempting
alternative — treating a missing `mentorId` as "unscoped" and skipping the `where` — hands a
director-level view of every student's marks, attendance and USN to any mentor whose group
was never set.

## Drift and stale comments

- **Heartbeat interval.** `voice_agent.py:38` and `app/routers/voice.py:150` both say the
  heartbeat runs "every ~15s"; `HEARTBEAT_INTERVAL_SECONDS` defaults to **10**. The 30s
  freshness window holds either way, so this is comment drift, not a bug.
- **`stop_grace_period`.** `docs/deployment-env.md:120` says 300s; `docker-compose.prod.yml:131`
  sets **960s** with a written derivation (900 drain + 2×20 shutdown + slack). The compose
  file is what runs; the doc is stale.
- **CrewAI.** `app/ai/llm.py:16-17` refers to "The CrewAI agent layer (Phase 4)…";
  `apps/api-py/README.md:22` says "There is no CrewAI", and it appears in no requirements
  file. The docstring line is stale.
- **The lifespan warning overstates the prod case.** `app/main.py`'s lifespan docstring says
  a blank `VOICE_WORKER_SECRET` "leaves BOTH worker endpoints open" in production. But
  `require_voice_worker` fails closed when `ENV=prod`, returning 500 to every caller
  including the real worker. The observable prod effect of a blank secret is therefore
  *dead voice ingestion* (heartbeats 500 → `worker_healthy` false → `/token` 409), not an
  open door. The forged-heartbeat abuse it describes is reachable in the blank-secret +
  `ENV=dev` configuration.

## Unresolved questions

- **How is the SPA served in production?** There is no Dockerfile for `apps/web` and no web
  or reverse-proxy service in `docker-compose.prod.yml`, yet `ENV=prod` marks the session
  cookie `Secure` and the API's CMD passes `--proxy-headers`. Nothing in the repo describes
  what builds the Angular bundle, what serves it, or what terminates TLS.
- **How does traffic reach the API?** The API image sets `EXPOSE 3300` but the `api` service
  in `docker-compose.prod.yml` declares no `ports:`.
- **`apps/api-py/KMS/`** contains only an empty `logs/` directory, is referenced by nothing,
  and is not in `.gitignore`.
- **`app/ai/adk.py` and `app/ai/agents.py` are imported by nothing** outside the `ai` package
  (verified by grep across `apps/api-py/app`), yet `google-adk` and `litellm` are pinned as
  *runtime* dependencies. They read as unwired Phase-4 scaffolding carried in the production
  image. To be settled in Chapter 8.
- **`/api/agent/chat` and `/chat/stream`** replay the caller's own typed conversation to the
  provider with `carries_student_data` left at its `False` default
  (`routers/agent.py:170, 231`). `agent.py:15-16` says to pass `True` "on any path that
  injects a student's private records", which reads as deliberate — the student's own typing
  is not an injected record. Flagged as a boundary nuance for Chapter 8 to settle, not a
  defect.
- **`llm_config()` tier 1 is skipped silently** when `LLM_BASE_URL` + `LLM_MODEL` are set for
  a remote host with a blank `LLM_API_KEY` (the `(key or is_loopback(base))` condition at
  `llm.py:88`), falling through to auto-select. No comment or test addresses this case.
- **No test covers the lifespan warning branch** (no `caplog` in `tests/`, nothing sets
  `ENV=prod` at app construction). Verified by grep; reported as a coverage gap.
