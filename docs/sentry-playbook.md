# Sentry — the observability playbook (2026-09)

Sentry is REEP's single observability tool: errors *and* performance traces, for
the FastAPI API and the Angular SPA, on one account. This document is the record
of what is wired, what is not, and what to do about the difference.

**Read the first section before proposing anything.** REEP is not a greenfield
Sentry install. Two SDKs are pinned and initialised, a DSN reaches the container
from Secrets Manager, `app/tracing.py` instruments the WebSocket paths the HTTP
integration cannot see, `app/traceability.py` joins Sentry events to CloudWatch
log lines by one request id, and a guard test in `tests/test_codebase_guards.py`
pins the two flags that keep a student's transcript off a third party's servers.
Several of those decisions were made after an incident. Re-deriving them from
scratch is how they get undone.

The organising principle is **rule 1 applied to telemetry**. AGENTS.md's rule 1
stops at the model adapter — `student_data_egress_allowed` gates `complete_chat`
and nothing else. Sentry is a second door into the same room, and no
`carries_student_data=True` gate guards it. Every configuration choice below is
answerable to that.

### Provenance

Everything here was measured on **2026-09-08** against `feat/institutional-spine`
at `61e13af` — **not** against `main` (`c7cbf49`), which is a strict ancestor and
does not contain `app/tracing.py`, `tests/test_tracing.py`, the CDK core stack,
or the two rule-1 flags on `sentry_sdk.init`. Line numbers are branch-local.
Re-measure after the merge; each section carries its own re-check commands.

Pinned versions at that commit: `sentry-sdk[fastapi]==2.68.1`
(`apps/api-py/requirements.txt:80`) and `@sentry/angular` `^10.71.0`, resolved to
exactly `10.71.0` by `package-lock.json`.

### The state of play

| | wired | gap |
| --- | --- | --- |
| **API SDK** | `sentry_sdk.init` at import time, `send_default_pii=False`, `include_local_variables=False`, `max_request_body_size="never"`, guarded by a test | no `release`, no `tracesSampler` (flat 0.2), no `before_send` |
| **SPA SDK** | dynamic-imported only when a DSN is set, `browserTracingIntegration`, `tracePropagationTargets` scoped to `/api`, Angular `ErrorHandler` | no `release`, **reports `environment: 'development'` in production**, no `TraceService`, no source maps |
| **Tracing** | WebSocket-lifetime transactions and external-call spans in `app/tracing.py`; browser→API is already one trace | Bedrock, the LLM adapter, the KB query, S3/SQS/DynamoDB are uninstrumented |
| **Correlation** | `X-Request-ID` echoed on every response and stamped on the `reep.access` log line, tagged onto Sentry events | — |
| **Privacy** | two constructor flags plus call-site discipline | no last-line scrubber; a student's words in an exception *message* would ship |
| **Delivery** | `SENTRY_DSN` from Secrets Manager (`stack.py:933`), `WEB_SENTRY_DSN` sed'd into the bundle at build time | no release creation, no commit association, no source-map upload |
| **Operations** | nine CloudWatch alarms on an SNS topic | no Sentry alert rules, no ownership rules, no cron monitor on the nightly retention job, no uptime check |

The single highest-value gap is source maps: every production browser stack trace
Sentry holds today is minified single-letter frames against hashed chunk names.
Phase 2 of the rollout fixes it.

### What changed on 2026-09-09 — the table above is the BEFORE picture

The proposals in this document were implemented on the branch that day. The
table above is kept as it was measured on 2026-09-08, because the reasoning in
every later section starts from it; this is the AFTER, with the status markers
`docs/deployment-process.md` defines. Build log entries L4-14 to L4-16 carry the
per-file reasoning.

| | now | marker |
| --- | --- | --- |
| **API SDK** | `init_sentry(service, dsn)` in `app/observability.py` is the ONE init for three processes (`reep-api`, `reep-scheduled-jobs`, `reep-interview-worker`), idempotent, refuses a second service per process; `traces_sampler` per the table in this document; `release` from `SENTRY_RELEASE` (a `--build-arg` in the Dockerfile); `environment` = `ENV` unless `SENTRY_ENVIRONMENT` overrides it; continuous profiling behind `SENTRY_PROFILES_SAMPLE_RATE` (this document proposed `SENTRY_PROFILE_SESSION_SAMPLE_RATE`; the shorter name was chosen to match the brief, the SDK option is unchanged); `SENTRY_SEND_DEFAULT_PII` recognised and refused; `SENTRY_LOGS_ENABLED` off | **[IN FORCE]** |
| **Privacy** | `app/telemetry_scrub.py` behind `before_send`, `before_send_transaction`, `before_breadcrumb` and `before_send_log`, exactly as the "scrubber to add" section specifies, plus exception messages, `extra`, `threads` frames and credential-named keys; the `Dropped interview turn` and `sso/status -> 200 unavailable` templates silenced; a raising scrubber drops the event; the guards in "The guards to add" exist in `tests/test_codebase_guards.py` and `tests/test_observability.py` proves it end to end through the FastAPI integration with a capturing transport | **[IN FORCE]** |
| **Scheduled jobs** | `app/retention_job.py` initialises `reep-scheduled-jobs` from `SENTRY_JOBS_DSN` (never `SENTRY_DSN`) and reports the `reep-retention-daily` check-in pair with the exact `monitor_config` in the cron section; a run that holds rows back is ERROR and still exits 0; a guard compares the crontab to the CDK schedule | **[IN FORCE]** in code. **The DSN is not in the secret**: `SENTRY_JOBS_DSN` must be added to `reep/external`, then `sentryJobsDsn: true` in `cdk.json` and a `reep-core` deploy — in that order, because a task referencing a missing key fails to start |
| **Interview worker** | `app/voice_platform/queue/worker.py` initialises `reep-interview-worker` from `SENTRY_INTERVIEW_WORKER_DSN`; one transaction per non-empty cycle, one span per message, `failure.kind` permanent/retryable, validation errors grouped under one issue per stream | **[IN FORCE]** in code; the worker still has no trigger (see the cron section) and no runtime carries its DSN |
| **SPA SDK** | `main.ts` per "The proposed main.ts": `sentry-lazy.ts`, `TraceService`, `tracesSampler` with a stamped base rate, `release` and `environment` from `environment.ts`, `enableLogs`/`enableMetrics` off, the `ignoreErrors`/`denyUrls` lists, a guarded import (a chunk 404 no longer blanks the app), `initialScope` tags `service=reep-web`; `telemetry-scrub.ts` behind all three hooks with a vitest spec; Replay unconstructed | **[IN FORCE]** |
| **Delivery** | `deploy.yml`: `--build-arg GIT_SHA`, release/environment/rate stamped beside the DSN, hidden source maps uploaded with `sentry-cli@3.7.0` when `SENTRY_AUTH_TOKEN` exists and deleted unconditionally before publish, a `release` job (new, set-commits, finalize, deploy marker); `angular.json` production `sourceMap` hidden with `sourcesContent` | **[IN FORCE]** in the workflow; **[ADMIN — NOT YET APPLIED]** for `SENTRY_AUTH_TOKEN`, `WEB_SENTRY_DSN` (the repository has no secrets at all as of 2026-09-09) and the three optional variables |
| **Operations** | Sentry has one default "high priority issues" rule per project and no metric rules; the alert-rule table below is still the plan | **[ADMIN — NOT YET APPLIED]** |

### How this document was checked

Each section was drafted against the repo and then re-checked by a second reader
whose only job was to break it — opening every file cited, resolving every line
number, and testing every SDK option name against the pinned versions. That pass
found blocking errors in all nine sections, including a proposed CI job whose
`if:` condition would have fired on failure, a `before_send` matcher that could
never have matched the log line it targeted, and a tree-shaking claim that does
not survive an actual `ng build`. Those corrections are applied. Claims that
could not be verified either way are marked inline as unverified — prices and
plan tiers especially, which change without notice and are linked rather than
asserted.

## Contents

- [What is already wired (and why)](#what-is-already-wired-and-why)
  - [The settings and secrets that exist today](#the-settings-and-secrets-that-exist-today)
  - [The API init — at import time, and two flags carry rule 1](#the-api-init--at-import-time-and-two-flags-carry-rule-1)
  - [The trace helpers, and every place they are called](#the-trace-helpers-and-every-place-they-are-called)
  - [The join key: one `X-Request-ID` from a click to a log line](#the-join-key-one-x-request-id-from-a-click-to-a-log-line)
  - [The guard tests](#the-guard-tests)
  - [The SPA — a dynamic import behind a build-time `sed`](#the-spa--a-dynamic-import-behind-a-build-time-sed)
  - [What is NOT wired](#what-is-not-wired)
- [Projects, environments and releases](#projects-environments-and-releases)
  - [Two projects — the split is a record, the two slugs are not](#two-projects--the-split-is-a-record-the-two-slugs-are-not)
  - [The media bridge belongs in `reep-api`, and the reason is mechanical](#the-media-bridge-belongs-in-reep-api-and-the-reason-is-mechanical)
  - [The Lambda is the only honest candidate for a third project](#the-lambda-is-the-only-honest-candidate-for-a-third-project)
  - [Environments are REEP's `ENV` values, verbatim, and today they disagree](#environments-are-reeps-env-values-verbatim-and-today-they-disagree)
  - [Releases: `reep@<git sha>`, and the sha has to be baked into the image](#releases-reepgit-sha-and-the-sha-has-to-be-baked-into-the-image)
  - [One trace crosses both projects, and origin is what breaks it — not project count](#one-trace-crosses-both-projects-and-origin-is-what-breaks-it--not-project-count)
- [The API: completing the backend SDK](#the-api-completing-the-backend-sdk)
  - [What is there now, and the three lines that must not move](#what-is-there-now-and-the-three-lines-that-must-not-move)
  - [The replacement](#the-replacement)
  - [The sampler, and the trap that makes half of them dead code](#the-sampler-and-the-trap-that-makes-half-of-them-dead-code)
  - [`before_send`: defence in depth, and the one filter that has to live here](#before_send-defence-in-depth-and-the-one-filter-that-has-to-live-here)
  - [The logging integration, and the exact risk it carries](#the-logging-integration-and-the-exact-risk-it-carries)
  - [Expected refusals: what the SDK already ignores, and the four it does not](#expected-refusals-what-the-sdk-already-ignores-and-the-four-it-does-not)
  - [Profiling: which API is current](#profiling-which-api-is-current)
  - [The eight-minute transaction, which is the hard one](#the-eight-minute-transaction-which-is-the-hard-one)
  - [Extending `app/tracing.py`: the call sites, with their wrappers](#extending-apptracingpy-the-call-sites-with-their-wrappers)
  - [The settings](#the-settings)
- [The SPA: completing the Angular SDK](#the-spa-completing-the-angular-sdk)
  - [What is there today, and the three things it is missing](#what-is-there-today-and-the-three-things-it-is-missing)
  - [The bundle budget — measured, and the number in `AGENTS.md` is not the ceiling](#the-bundle-budget--measured-and-the-number-in-agentsmd-is-not-the-ceiling)
  - [`release` and `environment` must come from the build](#release-and-environment-must-come-from-the-build)
  - [`TraceService` — the navigation spans that silently do not exist](#traceservice--the-navigation-spans-that-silently-do-not-exist)
  - [The sampler — what the browser's rate governs, and what it does not](#the-sampler--what-the-browsers-rate-governs-and-what-it-does-not)
  - [Noise — what the SDK already drops, what it does not, and what to fix instead](#noise--what-the-sdk-already-drops-what-it-does-not-and-what-to-fix-instead)
  - [Session Replay — no, and here is the condition](#session-replay--no-and-here-is-the-condition)
  - [The proposed `apps/web/src/main.ts`](#the-proposed-appswebsrcmaints)
  - [The settings, in one table](#the-settings-in-one-table)
- [Releases, source maps and suspect commits](#releases-source-maps-and-suspect-commits)
  - [The release name is the commit SHA, and that choice has consequences](#the-release-name-is-the-commit-sha-and-that-choice-has-consequences)
  - [Angular must emit source maps, and they must be hidden](#angular-must-emit-source-maps-and-they-must-be-hidden)
  - [The maps must never reach CloudFront, and the obvious guard is the wrong one](#the-maps-must-never-reach-cloudfront-and-the-obvious-guard-is-the-wrong-one)
  - [The `web` job: build, inject, upload, delete, publish](#the-web-job-build-inject-upload-delete-publish)
  - [The `release` job: nothing runs after both halves today](#the-release-job-nothing-runs-after-both-halves-today)
  - [The api's release is a build arg, and the CDK does not change](#the-apis-release-is-a-build-arg-and-the-cdk-does-not-change)
  - [The secrets and variables to create](#the-secrets-and-variables-to-create)
  - [Suspect commits needs four things and two of them are console work](#suspect-commits-needs-four-things-and-two-of-them-are-console-work)
  - [After one deploy, prove it — do not assume it](#after-one-deploy-prove-it--do-not-assume-it)
- [Rule 1 applied to telemetry](#rule-1-applied-to-telemetry)
  - [The leak table](#the-leak-table)
  - [The `?token=` rows: what actually happens](#the-token-rows-what-actually-happens)
  - [The scrubber to add](#the-scrubber-to-add)
  - [Server-side scrubbing: a backstop, and where the `**` selector lies](#server-side-scrubbing-a-backstop-and-where-the--selector-lies)
  - [Data residency — decided once, at organization creation](#data-residency--decided-once-at-organization-creation)
  - [The guards to add](#the-guards-to-add)
- [Alerts, ownership, cron and uptime](#alerts-ownership-cron-and-uptime)
  - [Uptime monitors — and the one that would be a permanent false green](#uptime-monitors--and-the-one-that-would-be-a-permanent-false-green)
  - [Cron monitors — one real job, and four things that are not scheduled](#cron-monitors--one-real-job-and-four-things-that-are-not-scheduled)
  - [Alert rules — page-now is four lines long, and one inbox reads all of them](#alert-rules--page-now-is-four-lines-long-and-one-inbox-reads-all-of-them)
  - [Ownership rules — the file syntax, and what it is honestly worth here](#ownership-rules--the-file-syntax-and-what-it-is-honestly-worth-here)
  - [CloudWatch keeps what Sentry cannot see, and nothing pages from both](#cloudwatch-keeps-what-sentry-cannot-see-and-nothing-pages-from-both)
- [Noise, sampling and the bill](#noise-sampling-and-the-bill)
  - [What is already quiet, and must not be "fixed"](#what-is-already-quiet-and-must-not-be-fixed)
  - [The noise table](#the-noise-table)
  - [Sampling: what actually moves the bill](#sampling-what-actually-moves-the-bill)
  - [Which categories REEP actually meters](#which-categories-reep-actually-meters)
  - [The plan, the prices, and the recommendation](#the-plan-the-prices-and-the-recommendation)
  - [Quota mechanics, and the three that surprise people](#quota-mechanics-and-the-three-that-surprise-people)
  - [Seer: not now](#seer-not-now)
- [Rollout and verification](#rollout-and-verification)
  - [The order, and why it is this order](#the-order-and-why-it-is-this-order)
  - [Phase 1 — One release string, one environment name, both halves](#phase-1--one-release-string-one-environment-name-both-halves)
  - [Phase 2 — Source maps: hidden, uploaded, then destroyed before the sync](#phase-2--source-maps-hidden-uploaded-then-destroyed-before-the-sync)
  - [Phase 3 — A dead-man's switch on the one job nothing watches](#phase-3--a-dead-mans-switch-on-the-one-job-nothing-watches)
  - [Phase 4 — An uptime check that is actually checking the API](#phase-4--an-uptime-check-that-is-actually-checking-the-api)
  - [Phase 5 — Sampling and the last-line scrubber](#phase-5--sampling-and-the-last-line-scrubber)
  - [After every deploy](#after-every-deploy)
  - [Once a month](#once-a-month)
  - [Things that will bite you here specifically](#things-that-will-bite-you-here-specifically)

## What is already wired (and why)

REEP already runs Sentry. Not a stub, not a half-finished branch: two SDKs are
pinned, initialised, guarded by tests, fed a DSN from a Secrets Manager secret,
and joined to CloudWatch by a shared request id. Nothing in this section is a
proposal, and nothing in it should be re-implemented — the rest of this
playbook builds on top of it.

**Everything below was measured on 2026-09-08 against `feat/institutional-spine`
at `61e13af`, not against `main`.** That distinction is not pedantry. `main`
(`c7cbf49`) is a strict ancestor of this branch and does **not** contain four of
the things this section describes: `apps/api-py/app/tracing.py`,
`apps/api-py/tests/test_tracing.py`, `infra/cdk/reep_core/stack.py` (on main the
core stack is still `infra/aws/*.tf`), and the two rule-1 flags on
`sentry_sdk.init` — on main that call takes four arguments, not six. Line
numbers are branch-local too (`sentry_dsn` is `config.py:304` on main, not
`:333`). Re-measure after the merge; the re-check commands are at the end of the
section.

### The settings and secrets that exist today

Four, and only four. The API's live under `Settings` in
`apps/api-py/app/config.py`, whose `model_config` (`:112`) leaves
pydantic-settings' default `case_sensitive=False` in place, so `sentry_dsn`
reads `SENTRY_DSN`.

| setting | default | blank means | where it is set |
| --- | --- | --- | --- |
| `SENTRY_DSN` | `""` (`app/config.py:333`) | The SDK is **never initialised**. Every `sentry_sdk` call downstream — the request-id tag, every span, every `capture` — is a documented no-op. A laptop and CI pay nothing. In production it means the API runs with all telemetry off and says so only by the *absence* of one log line. | An ECS `secrets` entry, `ecs.Secret.from_secrets_manager(external_secret, "SENTRY_DSN")`, in `infra/cdk/reep_core/stack.py:933`. It is documented in `apps/api-py/.env.example` since 2026-09-09 — blank, beside the production path and a warning against the onboarding snippet's `send_default_pii=True` — so a developer wiring telemetry locally finds the line and the reason for the three flags together. The `secrets` entry does render into the task-definition JSON — `infra/cdk/cdk.out/reep-core.template.json:1672-1673` carries `{"Name": "SENTRY_DSN", "ValueFrom": "arn:…:secret:reep/external-…:SENTRY_DSN::"}` — it is the *value* that is absent, not the reference. The secret is `reep/external-…`, which the operator owns and which is deliberately never imported into CloudFormation: `docs/cdk-cutover.md` §"What is left unmanaged, on purpose" (`:55-68`) explains that the stack reads both secrets by ARN and writes neither. |
| `SENTRY_TRACES_SAMPLE_RATE` | `"0.2"` — a **string**, not a float (`app/config.py:337`) | Falls back to `0.2`. The `settings.sentry_traces_rate` property (`:339-345`) parses it, returns `0.2` on `ValueError`, and clamps the result to `[0.0, 1.0]`. The string type is deliberate: a blank line in `.env` must read as "use the default" rather than raising a pydantic float error at import. | **Only as a commented line in `apps/api-py/.env.example` (2026-09-09).** `grep -rn SENTRY_TRACES_SAMPLE_RATE` otherwise returns the field name in `config.py` and nothing else — no workflow, and not in `stack.py`'s `api_environment` (`:282-292`). The effective production value is the default, `0.2`. |
| `WEB_SENTRY_DSN` | unset | `.github/workflows/deploy.yml:197-199` prints `no WEB_SENTRY_DSN secret; shipping without client telemetry` and exits 0. The SPA ships with no browser telemetry at all. | A GitHub **repository secret** — and the only `secrets.` reference in any of the four workflow files (`cdk-deploy.yml`, `ci.yml`, `deploy.yml`, `ops-task.yml`). Every other identifier in `deploy.yml` is a repository *variable* with a hard-coded fallback. |
| `environment.sentryDsn` | `''` (`apps/web/src/environments/environment.ts:16`) | `@sentry/angular` is **never fetched by the browser** — `main.ts` dynamic-imports it only when this is truthy. The chunk is still emitted into `dist/` by `ng build`; nothing downloads it. | Rewritten at build time by `sed` from `WEB_SENTRY_DSN`. See "The SPA" below. |

The runtime dependencies are `sentry-sdk[fastapi]==2.68.1`
(`apps/api-py/requirements.txt:80`; 2.68.1 is a real PyPI release, latest is
2.69.1 as of 2026-09-08) and `@sentry/angular`, pinned as `^10.71.0` in
`apps/web/package.json:20` and **resolved to exactly 10.71.0** by
`package-lock.json` (published 2026-08-24; latest is 10.73.0).

One correction to carry, because the repo states it wrongly: the `[fastapi]`
extra does **not** pull an integration. In sentry-sdk 2.68.1 that extra is
literally `"fastapi>=0.79.0"` — a version floor on the framework this app
already depends on. `FastApiIntegration` and `StarletteIntegration` ship inside
`sentry-sdk` itself and auto-enable when the framework is importable.
`requirements.txt:79`'s comment says otherwise and is wrong.

### The API init — at import time, and two flags carry rule 1

`sentry_sdk.init(...)` runs in `apps/api-py/app/main.py:59-87`, at **import
time, before the FastAPI object exists**. That placement is the point: the SDK
is already live when the process tries to boot, so a failure inside `lifespan`
reaches it. That is not theoretical here — the boot guard calls
`log.critical(refusal)` at `main.py:168` before raising, and CRITICAL clears the
default `LoggingIntegration`'s `event_level` of `ERROR`, so a refused production
boot becomes a Sentry event and `AtexitIntegration` flushes it on the way out.

Six arguments, three of them load-bearing for rule 1:

| argument | value | why |
| --- | --- | --- |
| `dsn` | `settings.sentry_dsn.strip()` | The whole block is inside `if settings.sentry_dsn.strip():`, so blank is off. |
| `environment` | `settings.env.strip() or "development"` | Production tasks carry `ENV=prod` from `stack.py:283`, so live API events are tagged `prod`. |
| `traces_sample_rate` | `settings.sentry_traces_rate` | 0.2, flat, everywhere. |
| `send_default_pii` | `False` | With it off the SDK does not attach cookies — **the `reep_session` token** — headers, IP or user context. |
| `include_local_variables` | `False` | **`send_default_pii=False` does not cover this, and that is the whole reason the flag is written out.** It is a separate switch and it *defaults to `True`* ([Python SDK options](https://docs.sentry.io/platforms/python/configuration/options/)), attaching every stack frame's locals to every captured exception. On this codebase those locals are a student speaking: `student_text` in the interview turn writer, `raw` holding a scorecard, `payload` holding a Nova transcript event. Demonstrated with the real SDK before the line was written: a `RuntimeError` raised in a function whose local was `"My CGPA is 8.7 and I was rejected by Infosys last week"` put that string verbatim into the event's frame vars. |
| `max_request_body_size` | `"never"` | Same shape, different door. The default is `"medium"`, and body capture is gated by this option **alone** — `sentry_sdk/integrations/_wsgi_common.py` checks `should_send_default_pii()` for cookies, never for the body. `POST /student/resume/generate` carries a brief with a name, USN, marks and attendance. A stack trace is worth having; the payload that caused it is not. |

**This is rule 1, applied to telemetry.** AGENTS.md's rule 1 stops at the model
adapter — `student_data_egress_allowed` gates `complete_chat`. Sentry is a
second door to the same room, and no `carries_student_data=True` gate guards
it. Dropping locals and bodies costs an operator nothing they need: the
function, file and line number of every frame survive.

There is **no `integrations=` argument and no `default_integrations=False`**, so
integration selection is entirely SDK defaults. That matters more than it
looks — see the note on `log.exception` below.

On success the process logs `Sentry initialised (traces_sample_rate=%s)` at
`main.py:87`. That line is the *only* signal that telemetry is on. There is no
counterpart when it is off: `main.py:153-160` deliberately announces
`outbound mail: NO TRANSPORT` at boot for exactly this reason, and Sentry has
no equivalent.

### The trace helpers, and every place they are called

`apps/api-py/app/tracing.py` is five functions, each a no-op when
`sentry_sdk.is_initialized()` is false — `enabled()`, `transaction()`,
`span()`, `tag_connection()`, `capture()`. **No call site checks `enabled()`
first**, so inertness is a contract, not a convenience, and `tests/test_tracing.py`
pins it.

The module docstring is explicit about what it is *not* duplicating. The
FastAPI integration already traces HTTP requests and already continues a trace
the browser started. What it cannot see is a WebSocket's lifetime — to it,
`/api/interview` is one upgrade request that never returns — and the calls made
*inside* a request. Those are the two things these helpers exist for.

**The module lives at the app root on purpose.** It began under
`app/voice_platform/monitoring/`, which meant the core interview path could
only trace itself by importing a feature subpackage — a dependency pointing the
wrong way. `app/voice_platform/monitoring/sentry.py` is now an 11-line
re-export so the platform's existing callers are unchanged; new code imports
`app.tracing` directly.

Every instrumented call site on this branch today:

| where | what | tags / span data |
| --- | --- | --- |
| `app/routers/interview.py:1032` | `transaction("interview {spec\|generic}", op="websocket.server")` around `relay.run()` | `conn_id`, `interview_session_id`, `engine` |
| `app/interview_nova.py:884` | `span("bedrock.invoke_bidirectional_stream", "open + handshake")` | `region`, `model`, `timeout_s` |
| `app/ai/llm.py:234` | `span("llm.complete", "bedrock {model}")` | `provider`, `model`, `messages` (a count), `carries_student_data` |
| `app/ai/llm.py:256` | `span("llm.complete", "{provider} {model}")` — the OpenAI-compatible HTTP path | the same, plus `timeout_s` |
| `app/ai/embeddings.py:93` | `span("embeddings.embed", "{model} x{n}")` | `model`, `texts` (a count) |
| `app/voice_platform/api/media_bridge.py:252` | `tag_connection(...)`, onto the isolation scope | `conn_id`, `degree`, `specialization`, `call_id` |
| `app/voice_platform/api/media_bridge.py:339` | `transaction("platform.media_bridge.{degree}")` | `degree`, `call_id` (the other two ride the scope tags above) |
| `app/voice_platform/api/call_close.py:105, :109, :193, :257` | `transaction("platform.call_close.{level}", op="task")` with child spans `audio.render`, `aws.s3`, `aws.dynamodb` | `session_id`, `key` |

(`@handler_span` in `voice_platform/api/admin.py` and `calls.py` is **not** on
this list — it is `monitoring/cloudwatch.py`'s EMF decorator and never touches
Sentry.)

Two of those carry reasoning worth not rediscovering. The interview
transaction starts **after** the refusal path — auth, consent, the caps — and
not at the top of the handler, because a transaction spanning that would report
a five-millisecond 4013 close as an interview; the engine's spans attach to it
without being passed anything, because the SDK carries the active transaction
in a context variable and the `TaskGroup` children created inside `relay.run()`
inherit that context. And `carries_student_data` is recorded as a span
attribute deliberately: **it makes rule 1 auditable in the trace** — you can see
which model calls carried a student's record and where they went — without any
of the content travelling.

Names, counts, durations and identifiers. Nothing in this table is a student's
words.

**`capture()` and `enabled()` have zero production call sites.** Error paths on
the interview and media-bridge sockets use `log.exception` instead
(`routers/interview.py:1048`, `media_bridge.py:212/:247/:265/:345/:354/:360`,
`call_close.py:119`). Those **do** become Sentry issues — `LoggingIntegration`
is in `_DEFAULT_INTEGRATIONS` with `DEFAULT_EVENT_LEVEL = logging.ERROR`, and
`log.exception` attaches the exception from `sys.exc_info()` — so the gap is not
arrival. The gap is that every one of them lands carrying none of the
`conn_id` / `call_id` / `interview_session_id` tags that `capture(exc, **tags)`
was written to attach, on exactly the two sockets where an untagged traceback
tells you nothing about which interview broke.

### The join key: one `X-Request-ID` from a click to a log line

`RequestTraceMiddleware` in `apps/api-py/app/traceability.py` mints or accepts
a request id (sanitised to `[A-Za-z0-9._-]`, 64 chars), and does three things
with it:

- `sentry_sdk.get_isolation_scope().set_tag("request_id", rid)` (`:51-56`),
  inside a `try/except: pass` because **telemetry must never fail a request**;
- echoes it as the `X-Request-ID` response header (`:71`);
- prints `rid=<id>` on one `reep.access` log line (`:74-82`), with `/health`
  excluded — it is the load balancer talking to itself every few seconds and
  logging it would bury the lines the file exists to make findable.

That is the join between the two telemetry planes: a Sentry event tagged
`request_id=<rid>` and the CloudWatch line in `/reep/api` reading `rid=<rid>`
are the same request. Searching `request_id:<id>` in Sentry is what starts a
trace thread. (The echoed header is server-side evidence today. `CORSMiddleware`
at `main.py:252-258` sets no `expose_headers` and the default is empty, so a
genuinely cross-origin browser could not read it back — it costs nothing while
CloudFront and the dev proxy keep the app same-origin.)

It is registered at `main.py:262`, after `CORSMiddleware` — **which makes it the
OUTERMOST middleware, not the innermost.** Starlette's `add_middleware` is
`user_middleware.insert(0, …)` (`starlette/applications.py:107`) and
`build_middleware_stack` wraps `reversed(middleware)` (`:81`), so the last one
added is the first one entered. The outcome the code wants is still the one it
gets — being outermost is precisely why `X-Request-ID` is written *after* CORS
has stamped its headers, and why the preflight responses CORS short-circuits
carry it too. But `main.py:260-262`'s own comment says "runs INSIDE it", and
that comment is wrong; do not reason from it when ordering the next middleware.

### The guard tests

Two modules, and they pin different halves.

`tests/test_codebase_guards.py:600`,
`test_sentry_never_ships_local_variables_or_request_bodies`, reads
`app/main.py` **as text** and asserts that `sentry_sdk.init(` is present and
that both `include_local_variables=False` and `max_request_body_size="never"`
appear in it. A textual assertion rather than a behavioural one, for the same
reason `/auth/activate`'s ordering is pinned textually: the flags must be
unmissable to the next person editing the init, and a behavioural test could
pass for the wrong reason.

`tests/test_tracing.py` has three: the helpers are inert without a DSN (that one
skips if a DSN is configured); a captured transaction — built against a real
`sentry_sdk.Client` with `before_send_transaction` collecting the payload —
carries names, counts and ids and **not** the string
`"My CGPA is 8.7 and I was rejected by Infosys last week"`; and the
voice-platform shim's five names are identity-equal to `app.tracing`'s.

Both run in CI's `API (FastAPI + Postgres)` job (`.github/workflows/ci.yml:45`),
which runs `python -m pytest -q` from `apps/api-py`, where `pytest.ini` sets
`testpaths = tests` — so both modules are inside the collected set. The job's
`env` block sets no `SENTRY_DSN`, so the inert-path test really runs rather than
skipping.

### The SPA — a dynamic import behind a build-time `sed`

`apps/web/src/main.ts` initialises Sentry **before** `bootstrapApplication`
(`:39`), so framework errors are captured through Angular's `ErrorHandler` and
not only as uncaught browser errors. The SDK is a dynamic
`import('@sentry/angular')` (`:13-15`) taken only when `environment.sentryDsn`
is non-empty. A dynamic import is a lazy chunk, and Angular's `initial` budget
counts initial-load files only, so it does not spend the budgeted
resource — `initial` warns at 250 kB and errors at 400 kB
(`apps/web/angular.json:38-43`). *(Reasoned from the builder's semantics and the
`environment.ts:12-15` / `main.ts:7-11` comments; not measured against a build in
this pass.)*

The init (`:18-26`) passes `integrations: [sentry.browserTracingIntegration()]`,
`tracePropagationTargets: [/^\/api(?:\/|$)/]` — distributed-trace headers are
restricted to this app's own API surface, so the SPA does not stamp trace
headers on anything else — `tracesSampleRate: 0.2` **hardcoded**, and
`sendDefaultPii: false`. When it is live it also provides
`{ provide: ErrorHandler, useValue: sentry.createErrorHandler() }` (`:34`).

What it does **not** provide is `TraceService`. The Angular SDK's own
`tracing.d.ts` says of `browserTracingIntegration`: "Use this integration in
combination with `TraceService`", which is the class that "creates a new
transaction for every route change". `grep -rn "TraceService" apps/web/src`
returns nothing, so pageloads are traced and Angular router navigations are not
— in an app where every route is lazy.

The DSN gets there through `.github/workflows/deploy.yml:192-203`, the step
named **"Point the SPA at Sentry"**, which runs before `npx ng build` (`:205`):

```bash
if [ -z "$WEB_SENTRY_DSN" ]; then
  echo "no WEB_SENTRY_DSN secret; shipping without client telemetry"
  exit 0
fi
sed -i "s|sentryDsn: ''|sentryDsn: '$WEB_SENTRY_DSN'|" \
  src/environments/environment.ts
grep -q "sentryDsn: 'https" src/environments/environment.ts   # prove it landed
```

Two things in that step are deliberate and one is fragile. The secret is
routed through `env:` rather than tested in an `if:`, because `secrets` is not
a named value the workflow parser admits inside an `if:` — the whole file fails
to parse and **every** dispatch is rejected before any job starts. The `grep`
is there so a substitution that silently missed fails the deploy rather than
shipping a DSN-less bundle. The fragility: the `sed` matches a literal source
string, so any reformatting of `environment.ts:16` (double quotes, a trailing
comment, prettier) makes it a no-op — and the `grep` that would catch that only
runs when the secret is set.

### What is NOT wired

Facts, not opinions. Each is a gap this playbook closes later.
1. **No `release` on either SDK.** Neither `sentry_sdk.init` nor the SPA's
   `sentry.init` passes one; nothing in the container environment, the
   Dockerfile, `angular.json` or `environment.ts` carries a version or sha.
   `deploy.yml` already logs the same `${{ github.sha }}` for both halves
   (`:162`, `:231`) — the identifier exists and is simply never handed over.
   Be precise about what this costs, because it is easy to overstate:
   `docs/deployment-process.md` §8.5's abort criterion — *"A new Sentry issue
   type first seen after this deploy, on any path that reads a student row"* —
   **is** evaluable today, because `age:` and `firstSeen:` are first-class issue
   search properties independent of releases
   ([searchable properties](https://docs.sentry.io/concepts/search/searchable-properties/issues/)).
   What a `release` adds is `firstRelease:`, regression detection, suspect
   commits and release health — the difference between "new in the last thirty
   minutes" and "new in *this build*, and here is the commit".
   → *later: releases and commit association*.
2. **The SPA reports `environment: 'development'` in production.**
   `main.ts:20` reads `environment.production`, `environment.ts:7` hardcodes
   `production: false`, `angular.json` has no `fileReplacements`, and there is
   no second environment file — `apps/web/src/environments/` contains exactly
   one. The API is tagged `prod`. The two halves of one distributed trace land
   in different Sentry environments, and any filter or rule keyed on
   `production` misses the entire web project.
   → *later: the SPA's build-time configuration*.
3. **No source maps.** `sourceMap` appears once in `apps/web/angular.json`, at
   line 55, inside the **development** configuration; the production block sets
   only `budgets` and `outputHashing: "all"`. Every browser stack trace Sentry
   receives is minified single-letter frames against hashed chunk names.
   → *later: source maps, and the exclusion that must go with them*.
4. **No `tracesSampler`.** Both SDKs use a flat 0.2. An eight-minute interview
   WebSocket transaction and a five-millisecond `GET /api/student/programme`
   are sampled identically, so four in five of the traces the interview
   instrumentation exists for are discarded. → *later: sampling*.
5. **No `before_send` or `before_send_transaction`.** Every PII defence today
   is a constructor flag plus discipline at call sites. There is no last-line
   scrubber, so a future path that puts student text into an exception
   *message* — rather than a local — ships it. → *later: the last-line scrubber*.
6. **No Session Replay.** `replayIntegration` appears nowhere in
   `apps/web/src`, though `@sentry/replay` is present in `node_modules` as a
   transitive dependency of `@sentry/browser` and is re-exported by
   `@sentry/angular` — so this is a decision not to use it, not an install step.
   → *later: session replay, and why it is not simply "on"*.
7. **No cron or uptime monitors.** No `monitor_slug`, `sentry_sdk.crons` or
   check-in call exists anywhere. Three things run on a clock, and they are not
   equally watched: `reep-retention-daily` (an `AWS::Scheduler::Schedule` at
   `stack.py:1089-1092` that deletes student data) has no alarm of its own at
   all, and the AWS Backup plan and the weekly restore test
   (`CfnRestoreTestingPlan`, `stack.py:661`) have alarms that ship only under
   `if harden:` — a phase that has never been deployed. `stack.py:617-628`
   records what that costs: every Sunday's RDS backup job failed silently,
   because the alarm that would have reported it was in the undeployed phase and
   the EFS half of the same plan kept succeeding.
   → *later: cron monitors for the jobs nobody watches*.
8. **No ownership or routing rules, and less than it looks.** There is no
   `.sentry/` directory and no issue-owners file. `.github/CODEOWNERS` exists
   and has no path rule matching `sentry`, `tracing`, `traceability` or
   `monitoring` — but the catch-all `*  @darshani8` at `:41` matches every file,
   so `app/main.py` and `app/tracing.py` are nominally owned. Read CODEOWNERS'
   own header before relying on that: it says the file "has no teeth until a
   ruleset on `main` turns on 'require review from Code Owners'" (`:7-14`), and
   then, in the half that usually gets dropped, that the path rules **signal
   nothing in the review UI either**, because every rule resolves to the same
   single account (`:16-24`). Two concrete gaps on top: `app/main.py`'s init and
   `app/tracing.py` are the two files where a one-line edit re-enables shipping
   a transcript to a third party and neither is on the inventory; and
   `infra/aws/` is on it (`:152`) while `infra/cdk/` — which now holds the entire
   stack, `SENTRY_DSN` included — is not.
   → *later: ownership and who gets the issue*.
9. **No alerts.** Nothing in Sentry notifies anybody. The only notification
   path in the deployment is the `reep-alerts` SNS topic (`stack.py:506`). It is
   fed by **six** alarms live and **nine** in the synthesised template: the six
   `docs/deployment-process.md` §8.5 tables, plus `reep-backup-job-failed`,
   `reep-backup-copy-failed` and `reep-backup-restore-test-failed`
   (`stack.py:1322-1339`), which exist only in the harden phase and are
   therefore not deployed — the same fact as item 7. (`-c blueGreen=true` adds
   five more; `cdk.json` sets it `false`.) The only alarm text that mentions
   Sentry is `reep-api-cpu-at-max`'s description, which *points an operator at*
   Sentry (`stack.py:1264`) rather than being fed by it. Note also that §8.5
   still locates those alarms in `infra/aws/observability.tf`, a file this
   branch deleted — they are `stack.py:1223-1266` now.
   → *later: alerts, and the inbox question*.

Re-measure rather than trusting this list:

```bash
# From the repo root, on feat/institutional-spine. Each should print nothing today.
grep -rn "release=\|release:" apps/api-py/app/main.py apps/web/src/main.ts
grep -rn "before_send\|beforeSend\|tracesSampler\|replayIntegration" \
  apps/api-py/app apps/web/src
grep -rn "SENTRY_AUTH_TOKEN\|sentry-cli\|SENTRY_ORG" .github/workflows
grep -rn "TraceService" apps/web/src                # route changes are untraced
grep -n "sourceMap" apps/web/angular.json           # only :55, under development
ls .sentry 2>/dev/null                              # no such directory

# And confirm which tree you are measuring, because main is behind:
git rev-parse --abbrev-ref HEAD
git log --oneline -1 main
```
---

## Projects, environments and releases

Two projects, one environment vocabulary borrowed from `ENV`, one release string
cut from the git sha. The property that has to survive all three: a student's
click and the FastAPI request it causes are **one trace**, and the org shape must
not be what breaks that. This section owns the topology. Sampling rates, the
PII flags and WebSocket instrumentation are their own sections; markers below
(**[IN FORCE]**, **[NOT WRITTEN]**, **[ADMIN — NOT YET APPLIED]**) are the ones
defined in `docs/deployment-process.md` §Status markers — with one extension
stated here rather than assumed: that document defines **[ADMIN — NOT YET
APPLIED]** as *a GitHub setting only the repository owner can apply by hand*, and
this section reuses it for a **Sentry-org** setting, which has the same
load-bearing property (no committed file can turn it on) and a different console.

### Two projects — the split is a record, the two slugs are not

**Two projects is settled; `reep-api` and `reep-web` are proposed names.**
`apps/api-py/app/config.py:326-327` says *"errors AND performance traces, api and
web in one project each"*, and both SDKs are wired for two DSNs: the API reads
`SENTRY_DSN` from the `reep/external` secret (`infra/cdk/reep_core/stack.py:933`),
the SPA reads `environment.sentryDsn`, baked at build time from the
`WEB_SENTRY_DSN` repository secret (`.github/workflows/deploy.yml:192-203`).
**[IN FORCE]** — the wiring, not the contents: both DSNs live outside the tree
(Secrets Manager, a GitHub secret), so this repository cannot tell you whether
either is populated.

**Checked against the org on 2026-09-09, and the names exist.** The org is
`bgs-college-of-engineering-and` (US region) and it holds five projects:
`reep-api`, `reep-web`, `reep-interview-worker`, `reep-scheduled-jobs` and
`node`. The two proposed slugs are therefore real. The next two are projects this
playbook did not plan for — the nightly retention run has no home below, and
`reep-ingest` is still "do not create yet" — and `node` is the onboarding
default; decide what each is for before an alert rule names it. `reep-api` has
one key, named `Default`, and it was handed over that day with Sentry's stock
install snippet. **It is not yet in the `reep/external` secret**: the secret's
`SENTRY_DSN` key was read the same day (key names only, never values) and found
present and blank. Writing it is an operator's console or CLI action — that one
key, the other four untouched — and it takes effect on the next task launch,
because ECS resolves secrets when a task starts. **It was written that afternoon
and went in malformed** — the DSN pasted into the middle of itself — and the
redeploy that followed put the API on a hostname that does not resolve while
`sentry_sdk.init` reported nothing wrong and zero spans arrived (build log
L4-15). Write it with `jq` on the retrieved JSON, not in the console's edit box,
and prove delivery by spans arriving from the tasks, never by an event sent from
a laptop with the tags typed in. No real DSN is committed in the
tree, and that stays true after the write: the only DSN strings are still the
fake in `apps/api-py/tests/test_tracing.py:54`
(`https://abc123@o0.ingest.sentry.io/0`) and the placeholder in
`docs/aws-deployment.md:103`.

They are two because they are two runtimes with two release artifacts and two
kinds of diagnosability. An API error is a Python traceback carrying a
`request_id` tag (`app/traceability.py:54`) that joins straight to a `/reep/api`
CloudWatch line (`stack.py:504`); a browser error is a single-letter frame in a
hashed chunk on a student's phone. Sentry's splitting guidance is code
boundaries, and "a monolithic codebase (separate backend and frontend) — set up
separate projects for the backend and frontend" is one of the four cases it names
(alongside multiple repositories, microservices and multiple languages:
[docs.sentry.io/organization/getting-started](https://docs.sentry.io/organization/getting-started/)).
Stage is emphatically *not* the axis — that is what environments are for, below.

The slugs are proposed to match names the account already uses: `reep-api` is the
ECS task family (`stack.py:952`, `deploy.yml:96`), `reep-web-…` is the bucket
(`deploy.yml:221`). One word, one runtime, everywhere.

| project | what it receives | who is alerted |
| --- | --- | --- |
| `reep-api` | Everything the **uvicorn process** emits on one DSN: errors, `http.server` transactions for every router under `/api`, the `websocket.server` transaction the interview opens (`app/routers/interview.py:1032`), the media bridge's (`app/voice_platform/api/media_bridge.py:339`) and the `task` transaction on call close (`call_close.py:105`). **Not** the nightly retention run — see the gap below. | **[ADMIN — NOT YET APPLIED]** No alert rule exists. Until one does, route new-issue alerts to the single address in `infra/cdk/cdk.context.json:6` (`alertEmail`, today `bdarshan5@bgscet.ac.in`) — the only inbox this repository can identify. **This is an assumption**: the repo names that address solely as the CloudWatch/SNS destination, and nothing in the tree is a Sentry notification target. |
| `reep-web` | Browser errors through `createErrorHandler()` (`apps/web/src/main.ts:34`) and `pageload` transactions from `browserTracingIntegration()` (`:21`). Nothing else. `@sentry/angular`'s own `browserTracingIntegration()` passes `instrumentNavigation: false` down to the core integration, so `TraceService` is the *only* thing that can start a navigation span — and `main.ts` provides none. | **Nobody, on purpose, until source maps ship.** The production Angular config sets no `sourceMap` (`apps/web/angular.json:37-51`), so every frame is minified against a hashed chunk. An alert nobody can act on is how a team learns to ignore the pager. Make this a place you go, not a place that calls you, and turn alerting on in the same change that uploads maps. **That change is [NOT WRITTEN]**: there is no `sentry-cli`, no bundler plugin in `apps/web/package.json` and no upload step in any workflow. It costs nothing at the budget — the SDK is a dynamic import (`main.ts:13-15`), so it never enters the 400 kB initial budget (`angular.json:38-43`), and `.map` files are not counted. |
| `reep-ingest` | **Do not create it yet.** See below. | — |

**The gap this table exposes: two production processes emit nothing.**
`sentry_sdk.init` is called in exactly one place, at import time in
`app/main.py:62` (verified: one hit repo-wide). Neither of these imports it:

* the nightly retention run — `stack.py:1108` overrides the command to
  `python -m app.retention_job`, and `app/retention_job.py` imports only
  `. retention` and `.db`. Its docstring is candid: the container's stdout "is
  this process's entire observability story."
* the candidate drain worker — `python -m app.voice_platform.queue.worker`, a
  separate process per degree stream (`worker.py:1-11`), same situation.

Both run the `reep-api` image and would report to the same DSN for the cost of a
two-line `sentry_sdk.init` in each entrypoint. Neither does today. **[NOT
WRITTEN]** — and until it is, an empty "no issues from retention" is
uninstrumented, not healthy.

### The media bridge belongs in `reep-api`, and the reason is mechanical

**It is not a second service. It is a router in the same process.**
`app/main.py:302` does `app.include_router(platform_bridge.router)`; the socket
answers at `/ws/media-bridge` and `/api/platform/media-bridge`
(`app/voice_platform/api/media_bridge.py:364-365`) from the same uvicorn worker,
under the same import-time `sentry_sdk.init`, on the same `SENTRY_DSN` injected
by the same `_api_task_def` helper (`stack.py:899`, `:933`).

Giving it its own project means a second `sentry_sdk.Client` inside one process
and teaching `app/tracing.py`'s helpers which client to bind to. That is not a
guess: `Scope.set_client` exists in the pinned 2.68.1
(`sentry_sdk/scope.py:495`), and `tests/test_tracing.py:53` already builds a
standalone `Client` to capture events — so the mechanism is real, available, and
exactly as invasive as it sounds. Those helpers are shared by the interview relay
and the bridge *deliberately*: `app/voice_platform/monitoring/sentry.py` is an
11-line re-export whose entire purpose is that the two do not fork.

The stronger argument is the data. A platform call **is** a real interview: the
bridge compiles a catalogue row into an `interview_matrix.Specialization`, runs
`NovaSonicSession`, and calls the interview router's own `_open_records`
(`media_bridge.py:71`, `:225`) and writers, leaving an `interview_sessions` row
with a `platform_call_sessions` row beside it. Split the projects and you have
put two halves of one interview in two places, joined only by a trace id, for the
sake of a filter you could have written on the `degree` tag the bridge already
sets (`media_bridge.py:252`). **Filter on the tag. Do not split the process.**

### The Lambda is the only honest candidate for a third project

**`reep-voice-candidate-ingest`** — not `reep-candidate-ingest`; the stack's
prefix is `f"{project}-voice"` (`infra/cdk/reep_voice_platform/stack.py:72`,
function name at `:140`) — is a Python 3.12 Lambda fired by S3 `OBJECT_CREATED`
under `uploads/` (`:137-153`, `:159-163`), deployed by `cdk-deploy.yml`: a
different workflow, a different runtime, a different release cadence from
`deploy.yml`. That is the shape that earns a project.

**Do not create it yet, and the reason is the asset.** The function is
`Code.from_asset` over `app/voice_platform/queue/`, a plain directory with no
dependency install; `lambda_handler.py` imports the standard library, `boto3`
(present in every Lambda Python runtime) and its two siblings `sqs.py` and
`validation.py`, which are themselves stdlib-only (`lambda_handler.py:19-31`).
`sentry-sdk` is not there and cannot get there without a layer or a bundled
build, and neither exists. An empty Sentry project is worse than no project: it
is a tile that reads "0 issues" whether the code is healthy or uninstrumented.
Create the third project (`reep-ingest` is the proposed slug, deliberately
shorter than the function name) in the same change that ships the layer, not
before. **[NOT WRITTEN]**

### Environments are REEP's `ENV` values, verbatim, and today they disagree

**The Sentry environment is whatever `ENV` says — do not add a second setting.**
`app/main.py:64` passes `environment=settings.env.strip() or "development"`, and
`ENV` already decides five other things: the `is_prod` predicate
(`config.py:1063`), three fail-closed dev-affordance guards
(`password_login_allowed`, `insecure_cookies_allowed`, `docs_exposed`), and two
refusals (the production boot guard in `app/main.py`, and `app.seed`'s). Adding
`SENTRY_ENVIRONMENT` beside it creates two sources of truth for one fact so that
a dashboard filter can read a prettier word, and the pretty word wins the moment
they drift.

| `ENV` | `config.py` reads it as | Sentry environment | who sets it |
| --- | --- | --- | --- |
| unset | dev — `env: str = "dev"` (`:148`) | `dev` | nothing; it is the field default |
| `dev` | dev — in `_DEV_ENV_NAMES` (`:66`) | `dev` | `apps/api-py/.env.example:52`, and CI **explicitly** (`.github/workflows/ci.yml:73`) |
| `prod` | production — `_PROD_ENV_NAMES` (`:50`) | **`prod`** | `infra/cdk/reep_core/stack.py:283` (`api_environment`, shared by the `reep-api`, `-blue` and `-green` task definitions — and so by the retention run, which reuses `reep-api`'s task definition); `docker-compose.prod.yml:130`, `:171`, `:259` |
| `development` `test` `testing` `ci` `local` | dev affordances kept — `_DEV_ENV_NAMES` | that string | nothing in this repo sets them; they exist so a typo cannot |
| `production` `prd` `live` | production — also `_PROD_ENV_NAMES` | that string, verbatim | nobody; the deployed value is `prod` |
| `staging`, `uat`, anything else | in neither list: `is_prod` is **False**, so the boot guard does not fire, but all three dev affordances fail closed | that string, verbatim | nobody — there is no staging deployment |

**The production environment is `prod`, four characters, not `production`** —
because `stack.py:283` hardcodes `prod`, even though `_PROD_ENV_NAMES` would
accept `production` too. Every alert rule, issue-owner rule, saved search and
environment filter that names `production` silently matches nothing. This is the
single cheapest way to build a detection plane that reports zero forever, so
write `prod` and re-read this table before typing the other word.

**Measured 2026-09-08: the SPA reports `development` from the production site,
and it is a one-line bug.** `apps/web/src/main.ts:20` reads
`environment.production ? 'production' : 'development'`;
`apps/web/src/environments/environment.ts:7` hardcodes `production: false`; and
`apps/web/angular.json` has no `fileReplacements` and there is no second
environment file. So the two halves of one distributed trace land in two Sentry
environments — `prod` and `development` — and no environment filter can ever see
both. `environment.production` has **exactly one reader in the whole SPA**: that
ternary. The flag exists only to mislabel Sentry. Delete it, add
`sentryEnvironment: ''` beside `sentryDsn`, and `sed` it in the same
"Point the SPA at Sentry" step that already writes the DSN and already
`grep`s to prove the rewrite landed (`deploy.yml:192-203`). **[NOT WRITTEN]**

One constraint worth knowing before anyone experiments: **an environment
auto-creates on its first event and can never be deleted, only hidden — and
hidden ones still count against quota**
([environments](https://docs.sentry.io/concepts/key-terms/environments/)). Names
are case-sensitive, capped at 64 characters, and may not contain spaces,
newlines or forward slashes, nor be the literal string `"None"`
([platform docs](https://docs.sentry.io/platforms/python/configuration/environments/)).
`ENV` is free text, so a typo there is now permanent clutter in Sentry as well as
a guard failing closed. Never derive an environment from a sha, a hostname or a
PR number.

### Releases: `reep@<git sha>`, and the sha has to be baked into the image

`${{ github.sha }}` is already the deploy identity on both halves — it tags the
image twice, and both jobs print it (`deploy.yml:84-86`, `:162`, `:231`). It is
simply never handed to either SDK: **neither `sentry_sdk.init` nor the SPA's
`sentry.init` passes a `release`.** `apps/web/package.json` is version `0.0.0`
and the repository has zero git tags, so the sha is not one option among several
— it is the only build identifier this repository has.

Use the full 40-character sha, one string, in both projects:

```
reep@<github.sha>          # e.g. reep@09cfcae…  (the full 40 hex, not abbreviated)
```

Both halves must carry the *same* string or "first seen in release" cannot be
asked of a trace that crosses them. One release can be associated with several
projects — `sentry-cli releases new -p reep-api -p reep-web <version>`, the `-p`
flag repeated ([docs.sentry.io/cli/releases](https://docs.sentry.io/cli/releases/)).

**The API cannot learn its own commit from anything AWS knows.** The task
definitions reference a fixed tag, never the sha: `stack.py:938` takes the tag as
a parameter, and the call sites pass `"latest"` for the `reep-api` family
(`:952`) and `"blue"` / `"green"` for the colour families (`:956-957`). The
service is rolled with `--force-new-deployment` (`deploy.yml:147`). So the
release must be baked at build time — an `ARG GIT_SHA` in
`apps/api-py/Dockerfile` (which takes no build args today) becoming
`ENV SENTRY_RELEASE`, passed as `--build-arg` at `deploy.yml:84`. **That needs no
change to `sentry_sdk.init`**: `get_default_release()`
(`sentry_sdk/utils.py:148-158`) reads `SENTRY_RELEASE` from the environment
before it tries anything else. The alternative, a `SENTRY_RELEASE` entry in
`stack.py`'s `api_environment`, is wrong for this pipeline: `deploy.yml` deploys
**code, never infrastructure** (`deploy.yml:9-11`), and that route would make
every code deploy a CDK deploy. **[NOT WRITTEN]**

**The SPA half needs its own line.** The browser SDK has no environment to read
from, and a plain `ng build` runs no bundler plugin that could inject one, so the
sha must be passed explicitly as `release:` in `sentry.init` and `sed` in beside
`sentryDsn` at `deploy.yml:192-203` — the same rewrite, one more field.
**[NOT WRITTEN]**

**What the release buys here, concretely.** A fixed tag plus a rolling deploy
means old and new tasks serve simultaneously for the target group's
deregistration delay: `stack.py:768` synthesises `DEREGISTRATION_DELAY_SECONDS`
(600, `:140`) when `hardenEcs` is on and **30 s** otherwise. The live value is
*unverified* — `cdk.context.json`'s `liveDbMultiAz: false` and
`liveBackupRetentionDays: 1` say the stack is pre-harden, and `deploy.yml:149-155`
asserts 600 s in a comment, so the repo contradicts itself. Settle it with one
read-only call before relying on either number:
`aws elbv2 describe-target-group-attributes --target-group-arn <api tg>`.
Without a release tag that overlap is invisible and reads as a flapping issue;
with it, the two versions are two rows. It is also what makes
`docs/deployment-process.md:796`'s own rollback trigger — *"a new Sentry issue
type first seen after this deploy"* — an answerable question rather than a
sentence.

### One trace crosses both projects, and origin is what breaks it — not project count

**A trace is not scoped to a project.** `sentry-trace` propagates a 128-bit
`trace_id` and spans carrying it render in one trace view no matter which project
received them — with one caveat Sentry states itself: project permissions can
stop a given viewer from seeing another project's transactions inside a trace
([trace view](https://docs.sentry.io/concepts/key-terms/tracing/trace-view/)).
Grant both projects to whoever debugs, and splitting `reep-api` from `reep-web`
costs nothing here. What splits a trace is the *origin*, and REEP is safe by
construction today: CloudFront serves the SPA from S3 on the default behaviour
(`stack.py:1159-1169`) and proxies `/api/*` to the ALB as an additional behaviour
(`:1170-1191`), so the browser only ever sees `https://reep.sast-skills.com`
(`cdk.context.json:domainName`). Same origin.

Three things hold that up, and each fails silently if changed:

1. **`tracePropagationTargets: [/^\/api(?:\/|$)/]`** (`apps/web/src/main.ts:23`)
   is correct rather than lucky. The browser SDK's default since v8 is
   same-origin only, and when the option IS set the SDK matches the full
   resolved URL *or* — only for a same-origin request — the pathname
   (`@sentry/browser`'s `shouldAttachHeaders`). So this regex matches
   `/api/…` today and stops matching the moment anyone moves the API to
   `api.<domain>`: the request is no longer same-origin, the pathname branch is
   skipped, every backend transaction becomes a trace root, and nothing anywhere
   reports an error. That move also needs
   `Access-Control-Allow-Headers: sentry-trace, baggage` — and getting *that*
   wrong blocks the API call itself, not merely the trace.
2. **The `/api/*` behaviour uses the AllViewer origin request policy**
   (`_ORIGIN_ALL_VIEWER`, `stack.py:149`, `:1187-1189`), so CloudFront forwards
   `sentry-trace` and `baggage` to the ALB unmodified. Narrowing that policy to
   a header allowlist strips them, and the only symptom is traces that quietly
   stop reaching the API.
3. **Sampling is head-based**, so the SPA's rate decides for anything a student
   starts, and the API's `traces_sample_rate` is *inherited past* for those
   requests: with a `traces_sample_rate` and no `traces_sampler` — which is what
   `app/main.py:62-65` configures — the SDK takes the parent decision from the
   incoming `sentry-trace`
   ([sampling precedence](https://docs.sentry.io/platforms/python/configuration/sampling/)).
   The API's rate governs only inbound work with no incoming trace header: the
   ALB's `/health` probes and the SSO redirect chain. It does **not** govern the
   retention run or the SQS drain worker, which reach Sentry not at all (see the
   gap under the project table). Raising `SENTRY_TRACES_SAMPLE_RATE` to chase
   more browser-initiated traces buys nothing.

The one place head continuation genuinely cannot work is the interview socket:
`apps/web/src/app/core/interview.service.ts:2253` opens it with the browser
`WebSocket` constructor, which has no header argument, so the handshake carries
no `sentry-trace` and `interview.py:1032`'s transaction is always a trace root.
That is a property of the browser, not of this topology, and the workaround is
its own section.
---

## The API: completing the backend SDK

`sentry_sdk.init` in `apps/api-py/app/main.py` sets six options and stops.
Three of those six are rule 1 applied to telemetry and must survive every edit
below untouched. This section owns what the other three should become, plus the
call sites `app/tracing.py` does not yet reach.

**Everything after "What is there now" is [NOT WRITTEN]** — a proposal, not a
description of the running system. The marker taxonomy is defined in
`docs/deployment-process.md` §"Status markers" (line 19); it is used here for the
same reason it exists there, because a playbook that mixes what runs with what
should run teaches its reader to trust neither half.

### What is there now, and the three lines that must not move

```python
# apps/api-py/app/main.py:59-87 — verbatim, comments elided.
if settings.sentry_dsn.strip():
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn.strip(),
        environment=settings.env.strip() or "development",
        traces_sample_rate=settings.sentry_traces_rate,
        send_default_pii=False,
        include_local_variables=False,   # rule 1 — frame locals
        max_request_body_size="never",   # rule 1 — request bodies
    )
    log.info("Sentry initialised (traces_sample_rate=%s)", settings.sentry_traces_rate)
```

`include_local_variables=False` and `max_request_body_size="never"` are pinned
by `tests/test_codebase_guards.py::test_sentry_never_ships_local_variables_or_request_bodies`
(`:600`), which reads `app/main.py` **as text** and asserts both strings plus
`sentry_sdk.init(` appear in it. That shape has a consequence for everything
below: the init call stays in `app/main.py` and the two flags stay written out
inline. Move the init into a helper module and the guard fails loudly, which is
correct; move only the flags and it fails just as loudly, which is also correct.
Neither is a reason to route around the guard.

Both defaults the guard exists to override are still the defaults on this pin:
`include_local_variables: Optional[bool] = True` and
`max_request_body_size: str = "medium"` (`sentry_sdk/consts.py:1341`, `:1314`).

Three absences drive the rest of this section. There is no `release`. There is
no `traces_sampler` — the rate is flat, `SENTRY_TRACES_SAMPLE_RATE` is set
nowhere in the repository (not in `apps/api-py/.env.example`, not in
`infra/cdk/reep_core/stack.py`'s `api_environment` at `:282`, where the only
Sentry entry is the `SENTRY_DSN` secret at `:933`), so production runs on the
`"0.2"` default in `app/config.py:337`. And there is no `before_send`, which
means the only PII defence is three constructor flags and reviewer discipline.

### The replacement

```python
# apps/api-py/app/main.py — replaces lines 59-87. The preamble comment at
# :49-58 and the two rule-1 comment blocks at :67-79 and :81-84 are unchanged
# and are elided here; they are the reason those flags are still in this call.
if settings.sentry_dsn.strip():
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    # The sampler and the scrubber are 60 lines of policy, and policy that
    # lives inside a call argument cannot be unit-tested. app/observability.py
    # is a new module beside app/tracing.py; tests/test_tracing.py is already
    # the home for "prove telemetry carries shape and never content".
    from .observability import before_send, traces_sampler

    sentry_sdk.init(
        dsn=settings.sentry_dsn.strip(),
        environment=settings.env.strip() or "development",
        # EXPLICIT, because the SDK's fallback is a trap in a container. With
        # `release` unset, get_default_release() (sentry_sdk/utils.py:148-155)
        # tries $SENTRY_RELEASE and then THE GIT REVISION OF THE WORKING TREE —
        # so an image built with a .git directory present silently reports a
        # release nobody chose, and one built without it reports none at all.
        # deploy.yml already knows the answer: it tags the image
        # ${{ github.sha }} (.github/workflows/deploy.yml:84-85) and prints
        # "API deployed: <sha>" (:162). Hand that same string to Sentry or the
        # "first seen in this release" that docs/deployment-process.md:785,:796
        # names as a rollback trigger cannot be read. Blue/green makes it
        # sharper: three task families, three image tags, and without this every
        # event from all three looks identical.
        release=settings.sentry_release.strip() or None,
        # STILL PASSED, and not as decoration. traces_sampler wins outright
        # whenever it is callable (sentry_sdk/tracing.py:1215), so this is dead
        # weight on the happy path — but when the sampler RAISES, the SDK falls
        # back to parent_sampled and then to exactly this option (:1223-1227).
        # Leave it out and that fallback is None, which fails
        # is_valid_sample_rate (utils.py:1700-1715) and DISCARDS the
        # transaction. A crash in the sampler must cost a sample rate, not the
        # trace.
        traces_sample_rate=settings.sentry_traces_rate,
        # REPLACES the flat rate as the decision-maker. A flat rate spent most
        # of its budget on an ALB probe of /ready every 15s
        # (infra/cdk/reep_core/stack.py:760-762) and threw away four out of five
        # of the eight-minute interviews this instrumentation exists for.
        traces_sampler=traces_sampler,
        # Continuous profiling. Nothing is profiled while this is 0.0, whatever
        # profile_lifecycle says, so the default deployment pays nothing.
        profile_session_sample_rate=settings.sentry_profile_rate,
        profile_lifecycle="trace",
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        # The fourth PII switch, and the one none of the other three covers: an
        # exception MESSAGE. `ValueError(f"could not parse {transcript!r}")` is
        # not a local, not a body, not a header. On this pin the default is
        # DEFAULT_MAX_VALUE_LENGTH = None (sentry_sdk/consts.py:5), and
        # strip_string returns the value untouched when max_length is None
        # (utils.py:1242-1246) — so today that message is shipped whole. This
        # does not stop the leak — nothing can — but it bounds it, and it bounds
        # a span description built from user input the same way.
        max_value_length=2048,
        before_send=before_send,
        integrations=[
            # All four of these except AsyncioIntegration are already active.
            # StarletteIntegration and FastApiIntegration are AUTO-ENABLING
            # (sentry_sdk/integrations/__init__.py:105, :81) and are listed only
            # to pin the two defaults this codebase depends on (see "Expected
            # refusals" below) so a future SDK cannot change them underneath us
            # silently. transaction_style="url" is already the default
            # (integrations/starlette.py:113).
            StarletteIntegration(transaction_style="url"),
            FastApiIntegration(transaction_style="url"),
            # LoggingIntegration is a DEFAULT integration
            # (integrations/__init__.py:61) — on even if you pass
            # integrations=[]. Both arguments below are its own defaults
            # (DEFAULT_LEVEL = INFO, DEFAULT_EVENT_LEVEL = ERROR,
            # integrations/logging.py:26-27). Listed for the same reason, and
            # because event_level is what decides which of this codebase's 82
            # ERROR-or-above log sites become issues. See "The logging
            # integration" below.
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            # NOT auto-enabling; genuinely absent today. task_spans defaults to
            # True (integrations/asyncio.py:230), so False here is a real
            # choice — see below, it is not free on the interview path.
            AsyncioIntegration(task_spans=False),
        ],
    )
    log.info(
        "Sentry initialised (release=%s profile_rate=%s)",
        settings.sentry_release.strip() or "<unset>",
        settings.sentry_profile_rate,
    )
```

The log line changes because the old one printed `traces_sample_rate`, and after
this that number is a fallback bucket rather than the rate. Printing `<unset>`
for a missing release is deliberate: `app/main.py:153-160` already announces
"outbound mail: NO TRANSPORT" at boot for exactly this reason, and a release
nobody set is the same class of silent-off.

### The sampler, and the trap that makes half of them dead code

**Inside `traces_sampler` the transaction name is the raw full URL, not the
route template.** A sampler written as `if name == "/ready": return 0.0` never
fires: the ASGI middleware runs before routing, so `_get_transaction_name_and_source`
(`sentry_sdk/integrations/asgi.py:455-490`) finds no `route` in the scope, falls
through to `_get_url(...)` and sets `source = TransactionSource.URL`. The
template is applied later and is what you see on the envelope, not what the
sampler sees.

The reliable key is `sampling_context["asgi_scope"]["path"]`, which the ASGI
integration sets as a custom sampling context. On this pin the live line is
`sentry_sdk/integrations/asgi.py:326` — the transaction-mode branch, which
covers `http` and `websocket` together. `:276` and `:287` are the span-streaming
equivalents (`:276` for http and websocket, `:287` for every other scope type,
lifespan included); they matter only after the `trace_lifecycle="stream"` move
described at the end of this section, and they are why the key survives it.

It is *not* present on the interview's own transaction. `tracing.transaction()`
calls `sentry_sdk.start_transaction(name=..., op=...)` (`app/tracing.py:53`) with
no custom sampling context, so for the transaction started at
`app/routers/interview.py:1032` the sampler sees an empty scope and an empty
path. That is why the `op` branch below exists and why it is not decoration.

```python
# apps/api-py/app/observability.py

# Checked FIRST, and the order is load-bearing: "/api/interview" is an
# always-traced prefix and "/api/interview/status" is a poll the client makes
# before every Start press.
_NEVER_TRACED = frozenset({
    # The ALB probes /ready every 15s from each AZ node against 2-10 tasks
    # (infra/cdk/reep_core/stack.py:760-762, :789-791).
    # app/traceability.py:72-74 already carves /health out of the access log
    # for the same reason; that argument stopped at the log line.
    "/health",
    "/ready",
    # The login screen probes this on every render, unauthenticated
    # (app/routers/auth.py:658, and its alias at :667).
    "/api/auth/sso/status",
    "/api/auth/google/status",
    "/api/interview/status",
})

# 1.0, because these are the paths where a failure is a person locked out, a
# record lost, or a file that never arrived — and they are all low-volume.
_ALWAYS_TRACED = frozenset({
    "/api/auth/login", "/api/auth/login/code",
    "/api/auth/activate", "/api/auth/forgot", "/api/auth/reset",
    "/api/auth/change-password",
    "/api/student/uploads", "/api/staff/upskilling", "/api/alumni/profile",
    "/api/platform/admin/candidates/bulk",
})
_ALWAYS_TRACED_PREFIX = (
    "/api/interview",              # the socket; /status is in _NEVER_TRACED
    "/ws/media-bridge",
    "/api/platform/media-bridge",
    "/api/register",
    "/api/auth/sso/google",
    "/api/auth/google/",
)
# NOT in either list, deliberately: /api/auth/me. authGuard short-circuits on
# `auth.isSignedIn()` before calling refresh()
# (apps/web/src/app/core/auth.guard.ts:12-14), so in-app navigation reuses the
# cached signal and the endpoint is reached once per hard page load, plus the
# two password screens that read the cookie back
# (account/change-password.component.ts:93, login/password-link.component.ts:130).
# Once per hard load across every signed-in user is still the wrong thing to pin
# at 1.0 because the word "auth" is in it.


def traces_sampler(sampling_context: dict) -> float:
    # asgi_scope, never transaction_context["name"] — the name here is the raw
    # URL and every route-template comparison against it is dead code.
    path = (sampling_context.get("asgi_scope") or {}).get("path") or ""

    # app/main.py:310 mounts the auth router a second time under /api/v1. Two
    # transaction names for one endpoint is how a latency query silently misses
    # half its traffic; normalise here rather than duplicating both lists. Note
    # this also folds redesign.router (mounted at the same prefix,
    # app/main.py:311) into /api/* names that may not exist — harmless against
    # the lists above, and the reason to re-read this line when a route moves.
    if path.startswith("/api/v1/"):
        path = "/api" + path[len("/api/v1"):]

    if path in _NEVER_TRACED:
        return 0.0
    if path in _ALWAYS_TRACED or path.startswith(_ALWAYS_TRACED_PREFIX):
        return 1.0

    # The interview's own transaction (app/routers/interview.py:1032) and the
    # platform's (voice_platform/api/media_bridge.py:339, call_close.py:105,
    # the latter op="task") are started by hand, so they arrive with no
    # asgi_scope and an empty path. transaction_context is Transaction.to_json()
    # (sentry_sdk/scope.py:1182-1184, tracing.py:705-718), so "op" is there.
    # They are the whole reason this instrumentation exists; keep every one.
    op = (sampling_context.get("transaction_context") or {}).get("op")
    if op in ("websocket.server", "task"):
        return 1.0

    # Respect the browser. With a traces_sampler the parent decision is NOT
    # inherited automatically — the sampler overrides it (sentry_sdk/tracing.py:
    # 1186-1189, :1215-1233) — so honouring it is something you write down or
    # lose. Anything above this line deliberately overrules it, at the cost of a
    # server transaction with no browser half.
    parent = sampling_context.get("parent_sampled")
    if parent is not None:
        return 1.0 if parent else 0.0

    # Everything else, plus work the API starts itself. Still the same clamped
    # property; only its scope has narrowed.
    return settings.sentry_traces_rate
```

A sampler that raises does not fail a request — the SDK logs a warning and falls
back to the parent decision, then to `traces_sample_rate`
(`sentry_sdk/tracing.py:1218-1227`), which is why that option is still passed
above. That is a reason to keep the sampler total and pure, not a reason to skip
testing it: it is a function from a dict to a float, and `tests/test_tracing.py`
is where a table of paths against expected rates belongs.

### `before_send`: defence in depth, and the one filter that has to live here

Two jobs, and they are separate.

The first is rule 1 as a floor rather than a flag. `send_default_pii=False`,
`include_local_variables=False` and `max_request_body_size="never"` are
constructor options; if a future SDK default moves, or an integration attaches
frame vars of its own, nothing today notices. Stripping them again on the way
out costs microseconds on an error path and makes the invariant hold rather than
merely be configured.

The second is the noise that cannot be silenced any other way. `ignore_logger`
(`sentry_sdk/integrations/logging.py:68`) works on a whole logger name, and
**the interview relay's worst noise and its best signal come out of the same
logger.** `app/interview_nova.py` binds `logging.getLogger(__name__)` through the
`_ConnLog` adapter (`app/interview_core.py:431`), so `"Dropped interview turn"`
at `app/interview_nova.py:1951` and `"Nova interview failed"` at `:934` are both
`app.interview_nova`. Silencing the logger silences the relay's only real crash
report. The filter has to be per-message, and `before_send` is the only place
that sees one.

```python
# apps/api-py/app/observability.py

# Matched against record.msg — the TEMPLATE, never record.getMessage(). The
# formatted message is where the argument values are, and reading them here to
# decide whether to drop an event is exactly the leak this function exists to
# stop.
_SILENCED_TEMPLATES = (
    # app/interview_nova.py:1951, inside a bare `except Exception` whose entire
    # design is that a dropped turn never ends a call. One database blip is one
    # event per turn for the rest of that interview, times every concurrent
    # interview (interview_max_sessions = 100, app/config.py:376). It is also
    # already detected: the reep-interview-dropped-turns CloudWatch alarm is a
    # metric filter on this literal string
    # (infra/cdk/reep_core/stack.py:1216-1225, and
    # docs/deployment-process.md:746), and interview_sessions carries
    # turns_emitted vs turns_persisted per session. Sentry adds a third copy and
    # no new fact.
    "Dropped interview turn:",
    # app/routers/auth.py:689. Fires on every render of the login screen when
    # ENV=prod and GOOGLE_CLIENT_ID is unset — a static configuration state,
    # already reported honestly in the endpoint's own 200 body. Drop it here,
    # and demote it to WARNING at the source; the operator's copy is the boot
    # log, not an issue that re-fires per unauthenticated page view.
    "GET /api/auth/sso/status -> 200 unavailable",
)


def _silenced(template: str) -> bool:
    """`in`, NOT `startswith`, and this is the whole point of the helper.

    app/interview_nova.py logs through the _ConnLog LoggerAdapter, whose
    process() returns f"[conn={conn_id} session={session_id}] {msg}"
    (app/interview_core.py:438-448). The template Sentry receives is therefore
    PREFIXED, and `record.msg.startswith("Dropped interview turn:")` is False on
    every single event — a filter that reads correctly and does nothing. It was
    written that way first.
    """
    return any(fragment in template for fragment in _SILENCED_TEMPLATES)


def before_send(event, hint):
    # hint is always a dict: the client calls before_send(event, hint or {})
    # (sentry_sdk/client.py:925), and LoggingIntegration puts the record at
    # hint["log_record"] (integrations/logging.py:308).
    record = hint.get("log_record")
    if record is not None and isinstance(record.msg, str):
        if _silenced(record.msg):
            return None

    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)   # the reep_session token
        request.pop("data", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in ("cookie", "Cookie", "authorization", "Authorization"):
                headers.pop(name, None)

    # The include_local_variables invariant, enforced rather than configured.
    for entry in (event.get("exception") or {}).get("values") or []:
        for frame in (entry.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)

    return event
```

`before_send` sees error and message events only. Transactions go to
`before_send_transaction` and structured logs to `before_send_log`, and neither
sees the other's payload. Neither is proposed here: the sampler drops the
transactions at zero cost before they are built, and structured logs are covered
below.

### The logging integration, and the exact risk it carries

`LoggingIntegration` is a **default** integration
(`sentry_sdk/integrations/__init__.py:61`) — installed even if you pass
`integrations=[]`, unless you pass `default_integrations=False`. Its
`event_level` is `logging.ERROR` (`DEFAULT_EVENT_LEVEL`, `integrations/logging.py:27`).
So today, with no `integrations=` argument at all, **every `log.error` and
`log.exception` under `app/` is already a Sentry issue**, with no exception
object required, and that has been true since the DSN was first set. Adding
`LoggingIntegration(...)` to the list does not turn anything on; it writes down
what is already deciding your error reporting.

There are 82 such sites today — 35 `log.error`/`_log.error` and 47
`log.exception`/`_log.exception` — countable with

```
grep -rn "log\.error(\|log\.exception(\|_log\.error(\|_log\.exception(" app/ --include=*.py | wc -l
```

That is also the whole reason `tracing.capture()` has no production callers:
`app/tracing.py:80` was written to attach `conn_id` / `interview_session_id`
tags to a captured exception, and every error path in the interview and
media-bridge sockets reaches Sentry through `log.exception` instead, carrying
none of them.

**Do not answer the dropped-turn flood by raising `event_level`.** Setting it to
`logging.CRITICAL` would silence `"Dropped interview turn"` and, in the same
stroke, `"Nova interview failed: %s"` at `app/interview_nova.py:934` — the
relay's deliberate catch-all whose comment says "anything not matched above is a
bug HERE rather than peer behaviour, so the traceback is the point" — plus
`"Could not open the Nova Sonic stream"` at `:845` and the other `log.exception`
sites that are the only record of why an interview failed. The whole codebase
would go quiet and look healthy. The targeted filter above is the answer; the
global lever is not.

Structured logs are a separate pipeline and are **off** on this pin
(`enable_logs: bool = False`, `sentry_sdk/consts.py:1361`, and no
`capture_sentry_logs` is set on `LoggingIntegration`), which is the right state:
stdout already reaches CloudWatch and `docs/aws-deployment.md` §5
("Observability + traceability, in practice") keeps raw logs there on purpose.
One caveat worth knowing before anyone reaches for it — **`sentry_sdk.logger.*`
sends its message body regardless of `enable_logs` or `capture_sentry_logs`.**
Verified in the installed 2.68.1: `sentry_sdk/logger.py` calls
`Scope._capture_log` unconditionally, the client's `LogBatcher` is constructed
with no gate (`client.py:651`), and `_capture_telemetry` (`client.py:1219-1306`)
runs `before_send_log` and then adds to that batcher. `enable_logs` is read in
exactly two places, both of them the *stdlib-to-Sentry-log* bridge
(`integrations/logging.py:409`, `integrations/loguru.py:156`) — never on the
direct API. Under rule 1 that makes `sentry_sdk.logger` a call nobody should add
to `app/` without adding `before_send_log` first.

`SqlalchemyIntegration` is **auto-enabling** and already running — it is in
`_AUTO_ENABLING_INTEGRATIONS` (`integrations/__init__.py:104`) and activates
because SQLAlchemy is importable. Listing it changes nothing and it is not in the
block above. It is what gives every `/ready` probe a `SELECT 1` span, and what
gives the interview transaction most of its spans. `Boto3Integration` is
auto-enabling for the same reason (`:73`). There is no psycopg/psycopg3
integration in 2.68.1 (the `integrations/` directory has `asyncpg` and
`sqlalchemy`, nothing else for Postgres); SQL spans on this stack come from
SQLAlchemy's engine events, so anything issued through raw psycopg outside
SQLAlchemy is unspanned.

`AsyncioIntegration` is the only genuinely absent one — it is in neither the
default nor the auto-enabling list. Be honest about what it buys here, which is
less than it first appears. Its exception-capture half buys almost nothing:
`_run_turn_write` (`app/interview_nova.py:1918`, scheduled fire-and-forget at
`:1912`) and the heartbeat task (`:2011`) both catch everything themselves, and
`_flush` (`app/interview_audio.py:832`) delegates to `_flush_locked` (`:836`),
whose disk write is *already* wrapped in `except Exception` → `self._stop(...)`
→ `log.exception("Could not write interview audio for %s", ...)` — which is an
ERROR log and therefore already an issue. What escapes today is only a failure
in the buffer-planning arithmetic before that `try`, which surfaces as asyncio's
own "Task exception was never retrieved". Its `task_spans=True` half (the
default, `integrations/asyncio.py:230`) is *not* free: one span per write task is
another few dozen spans (estimate, not counted) on an interview transaction that
already carries a couple of hundred. Start at `task_spans=False`; turn it on
deliberately, if at all.

### Expected refusals: what the SDK already ignores, and the four it does not

**Do not add an `ignore_errors` list for the 4xx control flow.** The Starlette
integration captures an exception only when it has an integer `status_code` in
`failed_request_status_codes` (`integrations/starlette.py:316-319`), which
defaults to `_DEFAULT_FAILED_REQUEST_STATUS_CODES = frozenset(range(500, 600))`
(`integrations/__init__.py:12`). Every 401, 403, 404, 409, 422 and both 429s
already produce a transaction and never an issue. A filter that duplicates that
is a filter someone will later widen.

**The WebSocket close codes are not exceptions at all.** 4001, 4003, 4010, 4012,
4013, 4014 and 4015 (`app/interview_core.py:125-160`) are `websocket.close(code=...)`
calls; there is nothing to capture. `WebSocketException` carries `.code`, not
`.status_code`, so the Starlette patch skips it too. The only Sentry events from
that whole refusal path come through `LoggingIntegration`, from the two sites
that log at ERROR where their eleven neighbours in the same handler log at
WARNING:

| site | condition | what it should be |
| --- | --- | --- |
| `app/routers/interview.py:745` | 1008 (`_CLOSE_NOT_A_STUDENT`, `:133`) — a STUDENT session with no `studentId` claim | WARNING with the account id as a tag. The state is a `User` row with no `Student` row, unfixable by the student, so they retry and each retry is an event for one broken account forever. |
| `app/routers/interview.py:762` | 4001 (`_CLOSE_NOT_CONFIGURED`, `app/interview_core.py:132`) — `settings.interview_ready` is false | WARNING at the request site, ERROR once at boot beside the other lifespan gates. It is a deployment state, not a fault, and it is already answered by `GET /api/interview/status`. |

The four places that raise a deliberate 5xx **are** captured, and should be:

- `app/routers/agent.py:306` (`/agent/chat`) and `:399` (`/agent/chat/stream`) — 503, no LLM provider configured.
- `app/routers/agent.py:372` — 502 after a provider failure.
- `app/voice_platform/api/admin.py:717` — 503, no SQS queue configured.

The 502 is the one to look at, because it is reported twice: `log.exception` at
`app/routers/agent.py:370` produces one event through the logging integration,
and the `HTTPException(502)` two lines later produces a second through the
Starlette integration. Same failure, two issues, different fingerprints. Keep
the `log.exception` — it carries the traceback — and let the 502 be the one that
is filtered, or drop the log and let the HTTPException carry it. Not both.

### Profiling: which API is current

There are two, and the one most snippets show is the legacy one.
`profiles_sample_rate` / `profiles_sampler` is transaction-based: the profiler
runs for the transaction's duration and **stops at 30 seconds** —
`MAX_PROFILE_DURATION_NS = int(3e10)  # 30 seconds` in
`sentry_sdk/profiler/transaction_profiler.py:195`, enforced at `:375` — which
makes it useless for a 480-second interview socket, precisely the path worth
profiling. Continuous profiling (`profile_session_sample_rate` +
`profile_lifecycle`, stable since 2.24.1, per
[docs.sentry.io/platforms/python/profiling](https://docs.sentry.io/platforms/python/profiling/))
is the current API and the only one that covers a whole session. Use
`sentry_sdk.profiler.start_profiler()` / `stop_profiler()` if you ever go
manual; `start_profile_session` / `stop_profile_session` are deprecated aliases
on this pin (`profiler/continuous_profiler.py:156`, `:172`).

`profile_lifecycle="trace"` profiles whenever a span is active, and it does
inherit the sampler above — confirmed in the installed source, not assumed:
`try_profile_lifecycle_trace_start()` is called only inside `if transaction.sampled:`
(`sentry_sdk/scope.py:1201-1210`), and `ContinuousScheduler.auto_start` returns
`None` unless the session itself was sampled and the lifecycle is `"trace"`
(`profiler/continuous_profiler.py:257-262`). So the health checks that sample at
0.0 are never profiled, and the interview always is. **Start at
`SENTRY_PROFILE_SESSION_SAMPLE_RATE=0.1`,** after the sampler has run for a week
and the trace volume is understood. Nothing is profiled at `0.0` whatever
`profile_lifecycle` says, so shipping the option with a zero default and turning
it up from the environment is a decision an operator can make without a deploy.
On rule 1: the profiler samples stack frames — function, module, line — not
values, so it is in the same class as the stack traces the current config
already sends.

### The eight-minute transaction, which is the hard one

Four separate facts collide on `/api/interview`.

**One, there are already two transactions per interview, not one.** The comment
at `app/routers/interview.py:1018-1031` and the docstring at
`app/tracing.py:12-15` both say the FastAPI integration sees the socket as "a
single upgrade request that never returns". **That premise is false on
sentry-sdk 2.68.1 and I am contradicting it deliberately.** The Starlette
integration wraps `Starlette.__call__` in `SentryAsgiMiddleware` for *every*
scope type (`integrations/starlette.py:454-483`), and in
`sentry_sdk/integrations/asgi.py:269-273` the guard is
`if ty in ("http", "websocket"): if ty == "websocket" or method in
self.http_methods_to_capture:` — the websocket branch short-circuits the method
allowlist, so a `websocket.server` transaction spanning the socket's whole life
is created unconditionally, and `tracing.transaction()` starts a second one
inside it. Two transactions, two names, doubled cost, and the same interview
split across both. Fix it by keeping the outer one and setting the tags and name
on it, or by keeping the inner one and dropping the outer. Not by leaving both —
and note that the "drop it in `before_send_transaction`" option is
transaction-mode only: under the stream mode proposed in point four there are no
transaction events, spans are flushed one at a time through `before_send_span`,
and that hook cannot drop a span
([Span Lifecycle](https://docs.sentry.io/platforms/python/tracing/span-lifecycle/)).
If stream mode is the destination, fix this by reusing the outer segment.

**Two, transaction mode caps a transaction at 1000 spans**, and spans past the
cap are dropped silently — `max_spans = client.options["_experiments"].get("max_spans") or 1000`
(`sentry_sdk/scope.py:1220`), fed to `_SpanRecorder`, which increments
`dropped_spans` and drops rather than raises (`tracing.py:202-222`). The
interview does not hit it today — the two inserts per turn plus a heartbeat
SELECT+UPDATE per minute put it in the low hundreds (~120-200 spans is an
estimate carried from the audit, not a recount) — but there is no headroom rule,
and the real ceiling arrives sooner: an event over **1 MB decompressed, or 200 KB
compressed, is rejected outright**
([Size Limits](https://docs.sentry.io/concepts/data-management/size-limits/)),
and the whole transaction goes with it. The practical rule is a sentence, not a
setting: **never add a span per audio frame, per PCM chunk, or per watchdog
tick.** A span per *turn* is the right granularity and it is the one the record
already uses (`turns_emitted` / `turns_persisted`).

**Three, nothing on the server ends a long transaction early.** The
`idleTimeout` / `finalTimeout` knobs are browser-only — there is no
`idle_timeout` or `final_timeout` anywhere in `sentry_sdk/`. So in transaction
mode the interview is invisible in Sentry until minute eight, and if the task is
killed mid-call — an OOM, a deploy that outran the drain — the transaction is
lost entirely, taking every span with it. That is the opposite of what you want
from the least observable path in the system.

**Four, the fix has an ordering constraint that will bite anyone who applies it
in the obvious order.** `trace_lifecycle="stream"` (available on this pin —
`Optional[Literal["static", "stream"]]`, `sentry_sdk/consts.py:1324`) is the real
answer: it removes the per-transaction span recorder, and its batcher flushes
every five seconds (`_span_batcher.py:31`, `FLUSH_WAIT_TIME = 5.0`) so a live
interview is watchable and a killed worker loses only the last window. (It is not
unbounded: the batcher drops at 2000 buffered spans between flushes,
`_span_batcher.py:28` — a rate ceiling rather than a per-trace one.) But in
streaming mode `Scope.start_transaction` returns a `NoOpSpan` after emitting a
`DeprecationWarning` — read it in `sentry_sdk/scope.py:1153-1159`.
`app/tracing.py:53` is built on `start_transaction`. **Turning on stream mode
before rewriting `tracing.transaction()` on `start_span` silently deletes the
interview transaction, the media-bridge transaction and the call-close
transaction, and `tests/test_tracing.py` notices nothing — its inert test skips
when a DSN is present, and its live test builds its own `sentry_sdk.Client`
without `trace_lifecycle`.** The order is: rewrite `tracing.transaction()`, prove
it under `trace_lifecycle="stream"` in a test that actually sets the option, then
flip it — and `trace_lifecycle` is deliberately **not** an environment variable
for that reason. It is a code change that travels with the rewrite, or it is an
outage in the telemetry nobody notices for a month.

### Extending `app/tracing.py`: the call sites, with their wrappers

First, a correction to the premise. `app/tracing.py:16-17` lists "Bedrock, the
LLM adapter, the knowledge base, S3, SQS, DynamoDB" as unseen. Three of those
six are partly seen already: `Boto3Integration` is auto-enabling and patches
`botocore.client.BaseClient.__init__` (`integrations/boto3.py:39-54`), registering
a `request-created` hook that opens a span with `op=OP.HTTP_CLIENT` and
description `aws.<service-id>.<OperationName>` (`:59-111`). That is read out of
the integration source, not from a live probe against this repo — but it holds
here on one condition worth writing down: the patch is on `__init__`, so only
clients built *after* `sentry_sdk.init` are instrumented. Every AWS client in
this codebase qualifies — the init is at import of `app/main.py:62`, and
`session_store_for`, `candidate_queue` and `recording_store` are all lazy, with
`platform_ssm_config.load()` running from `lifespan` at `app/main.py:130`.

What a hand-rolled span adds there is therefore not the span — it is the
REEP-side key: which object, which queue, which table. Wrap for that, or do not
wrap.

Bedrock is the exception, and it is a real hole. The Nova stream is opened by
`aws_sdk_bedrock_runtime.client.AsyncBedrockRuntimeClient` over
`smithy_http.aio.crt.AWSCRTHTTPClient` (`app/interview_nova.py:461-525`) — not
`botocore.BaseClient`. `Boto3Integration` cannot see it, and the only span on
that path is the open-and-handshake at `app/interview_nova.py:884`. The eight
minutes after it are unspanned.

```python
# 1. app/interview_nova.py — the per-turn Bedrock hop. The open+handshake span
#    at :884 is the only Bedrock span today; everything after it is dark, and
#    Boto3Integration cannot help because Nova does not go through botocore.
#    gen_ai is the conventional namespace and "aws.bedrock" is a defined
#    provider value, so these land in Sentry's AI view for free.
with span("gen_ai.chat", f"chat {settings.nova_sonic_model}",
          **{"gen_ai.operation.name": "chat",
             "gen_ai.provider.name": "aws.bedrock",
             "gen_ai.request.model": settings.nova_sonic_model,
             "gen_ai.response.streaming": True,
             "interview.phase": phase, "interview.advanced": advanced}):
# NEVER gen_ai.input.messages / gen_ai.output.messages. Bedrock is already
# off-machine for the model call; Sentry is a THIRD destination and a different
# disclosure from the one interview_consents covers.

# 2. app/ai/llm.py:299 — stream_chat's Bedrock branch (`yield from
#    _bedrock_stream(...)`). Its non-streaming sibling has a span at :234; this
#    one has none.
with span("llm.stream", f"bedrock {cfg.model}", provider="bedrock",
          model=cfg.model, messages=len(messages),
          carries_student_data=carries_student_data):

# 3. app/ai/llm.py:315 — stream_chat's OpenAI-compatible branch. Wrap the
#    `with httpx.stream(...)` block, not the generator function: stream_chat IS
#    a generator, and a caller that abandons it must not leave the span open.
#    Measures time-to-last-token, which is the number that matters for a chat
#    screen.
with span("llm.stream", f"{cfg.provider} {cfg.model}", provider=cfg.provider,
          model=cfg.model, messages=len(messages),
          carries_student_data=carries_student_data, timeout_s=cfg.timeout_s):
# carries_student_data on both, for the reason llm.py:230-233 already gives:
# it makes rule 1 auditable in the trace. The streaming path takes the same
# argument and passes the same gate, and today leaves no evidence at all.

# 4. app/knowledge.py:74 — search(). The embed() hop inside is already spanned
#    (app/ai/embeddings.py:93); the full-text + pgvector blend around it is not,
#    and it is two or three statements plus a Python-side cosine merge.
with span("kb.search", f"{audience} limit={limit}",
          audience=audience, limit=limit, embedder=embedder_configured()):
    ...
    tracing.annotate(results=len(out), floor_rejected=rejected)

# 5. app/voice_platform/storage/s3.py:127 — the put_object call inside
#    RecordingStore.upload_bytes (:117). The key is the point.
with span("aws.s3", "put_object", key=key, bytes=len(body)):
# NOT s3.py:80's generate_presigned_url inside make_presigned_url (:71) — it
# signs locally and makes no network call, as that function's own docstring
# says. A span there measures string concatenation and teaches a reader that
# presigning is a round trip.

# 6. app/voice_platform/storage/dynamodb.py:116 / :133 / :147 — the put_item,
#    update_item and get_item calls inside the DynamoDB-backed store's
#    put() (:114), update() (:122) and get() (:145).
with span("aws.dynamodb", "put_item", table=self.name):
with span("aws.dynamodb", "update_item", table=self.name):
with span("aws.dynamodb", "get_item", table=self.name):

# 7. app/voice_platform/queue/sqs.py:77 (inside push_many's flush() closure,
#    :74), :107 (pull) and :126 (ack), and the same three operations in
#    app/workers/transport.py:24 / :31 / :39.
with span("aws.sqs", "send_message_batch", degree=degree_level, n=len(batch)):
with span("aws.sqs", "receive_message", degree=degree_level):
with span("aws.sqs", "delete_message", degree=degree_level):
```

Item 4 needs one addition to `app/tracing.py`, because `span()` yields `None`
when Sentry is off and a call site should not have to branch on that:

```python
def annotate(**data: Any) -> None:
    """Attach data to whatever span is active. No-op when Sentry is off."""
    if not enabled():
        return
    current = sentry_sdk.get_current_span()
    if current is None:
        return
    for key, value in data.items():
        try:
            # set_data on this pin (sentry_sdk/tracing.py:604). The stream-mode
            # rewrite above changes this to set_attribute; it is one of the
            # things that rewrite has to carry.
            current.set_data(key, value)
        except Exception:  # noqa: BLE001 - never let observability raise
            pass
```

Every one of these carries counts, identifiers, models and keys. None carries a
student's words, which is the property `tests/test_tracing.py` already asserts
and which every wrapper above has to keep true.

### The settings

All read by `Settings` in `apps/api-py/app/config.py`, which is
`SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")` (`:112`) with no
aliases — so **the field name IS the environment variable name**, matched
case-insensitively. That is why the traces rate is a string FIELD plus a clamped
PROPERTY (`sentry_traces_sample_rate` at `:337`, `sentry_traces_rate` at `:340`)
and not one name doing both jobs: a blank line in `.env` should mean "use the
default", not a pydantic validation error at import. The profile rate copies that
pair exactly; naming the field `sentry_profile_rate` would silently bind it to
`SENTRY_PROFILE_RATE` and leave `SENTRY_PROFILE_SESSION_SAMPLE_RATE` doing
nothing.

| setting | env var | default | what breaks when it is not set |
| --- | --- | --- | --- |
| `sentry_release` | `SENTRY_RELEASE` | `""` | The SDK falls back to `$SENTRY_RELEASE`, then **the git revision of the working tree** (`sentry_sdk/utils.py:148-155`). An image built with a `.git` present reports a release nobody chose; one built without reports none. Either way "first seen in this release" — the rollback trigger `docs/deployment-process.md:785,:796` names — cannot be evaluated, and blue/green's three image tags are indistinguishable. Set it to the same `${{ github.sha }}` `deploy.yml:84` already tags the image with. |
| `sentry_traces_sample_rate` → property `sentry_traces_rate` | `SENTRY_TRACES_SAMPLE_RATE` | `"0.2"` | **Exists today; its meaning narrows.** It stops being the rate for everything and becomes the sampler's fallback bucket plus the SDK's own fallback if the sampler raises. It is set nowhere in the repo, so `0.2` is the effective production value — add it to `api_environment` in `infra/cdk/reep_core/stack.py:282` so a quota emergency is an environment change rather than a task-definition revision, which `deploy.yml` cannot ship. |
| `sentry_profile_session_sample_rate` → property `sentry_profile_rate` | `SENTRY_PROFILE_SESSION_SAMPLE_RATE` | `"0.0"` | Nothing is profiled, whatever `profile_lifecycle` says (`sentry_sdk/profiler/continuous_profiler.py:257-259`). That is the correct default: ship it at zero and raise it to `0.1` once the sampler's volume is understood. |

Two things are deliberately **not** settings. The sampler's path lists live in
`app/observability.py` because they are claims about this application's routes,
and a route added without a matching entry should be reviewed in a diff, not
discovered in a config box. And `trace_lifecycle` is a code change that must
travel with the `tracing.transaction()` rewrite, for the `NoOpSpan` reason
above — an environment variable that silently deletes three transactions is
worse than no switch at all.
---

## The SPA: completing the Angular SDK

The browser half of REEP's telemetry is wired and working, and it is missing
four things: a release, an honest environment tag, router instrumentation, and
a sampler. This section owns `apps/web/src/main.ts`, two fields of
`apps/web/src/environments/environment.ts`, and the deploy steps in
`.github/workflows/deploy.yml` that feed them. It does not own source maps or
the `sentry-cli` upload — without those every trace below arrives as minified
single-letter frames, and that is a separate change with a separate approval.

**Nothing in this section is written.** Every line is a diff against
`apps/web/src/main.ts` (a client-only change that ships through `deploy.yml`'s
`web` job) plus one new file and the release plumbing, which touches the
workflow too.

### What is there today, and the three things it is missing

`apps/web/src/main.ts:12-39` — the file's own indentation is irregular, so this
is reformatted, not byte-for-byte:

```typescript
async function bootstrap(): Promise<void> {
  const sentry = environment.sentryDsn
    ? await import('@sentry/angular')
    : null;

  if (sentry) {
    sentry.init({
      dsn: environment.sentryDsn,
      environment: environment.production ? 'production' : 'development',
      integrations: [sentry.browserTracingIntegration()],
      // Restrict distributed-trace headers to this app's API surface.
      tracePropagationTargets: [/^\/api(?:\/|$)/],
      tracesSampleRate: 0.2,
      sendDefaultPii: false,
    });
  }

  const config = sentry
    ? { ...appConfig, providers: [...appConfig.providers,
        { provide: ErrorHandler, useValue: sentry.createErrorHandler() }] }
    : appConfig;

  await bootstrapApplication(App, config);
}
```

Three of those lines are already right and should not be touched by anyone
"modernising" this file.

`browserTracingIntegration` is imported from `@sentry/angular` and not
`@sentry/browser` — the Angular package re-exports the browser SDK wholesale
(`node_modules/@sentry/angular/fesm2015/sentry-angular.mjs:3`,
`export * from '@sentry/browser'`) but shadows that one symbol with its own
wrapper, and swapping it produces URL-named navigation spans with no route
parameterization.

`sendDefaultPii: false` is the browser twin of the API's flag. It is already
marked `@deprecated` in the pinned version — `@sentry/core/build/types/types/options.d.ts:350-356`
says it will be removed in v11 — so expect the strikethrough in your editor and
**do not "fix" it by passing a bare `dataCollection` object.** That successor's
per-category defaults are *permissive*, and the switch is not per field but per
presence: `node_modules/@sentry/core/build/cjs/utils/data-collection/resolveDataCollectionOptions.js`
reads `options.dataCollection != null ? DEFAULTS : defaultPiiToCollectionOptions(sendDefaultPii)`,
where `DEFAULTS` is `userInfo: true`, `cookies: true`, both `httpHeaders`, all
four `httpBodies`, `urlQueryParams: true`, `databaseQueryData: true`,
`stackFrameVariables: true`. Passing `dataCollection: {}` therefore opts you
into strictly more than `sendDefaultPii: false` sends now. Background:
[the dataCollection announcement](https://blog.sentry.io/datacollection-control-panel/).

And `tracePropagationTargets: [/^\/api(?:\/|$)/]` genuinely works, though not
for the reason the comment gives. `@sentry/browser/build/npm/esm/dev/tracing/request.js:131`
is the whole rule:

```javascript
return stringMatchesSomePattern(resolvedUrl.toString(), tracePropagationTargets)
  || isSameOriginRequest && stringMatchesSomePattern(resolvedUrl.pathname, tracePropagationTargets);
```

The resolved *absolute* URL is matched first (`https://…/api/student/jobs`,
which an anchored `/^\/api/` cannot match) and the pathname is a fallback for
**same-origin** requests only. `apiBase` is the relative `'/api'` and CloudFront
serves both halves from one distribution — verified at
`infra/cdk/reep_core/stack.py:1170-1191`, where the `/api/*` behaviour points at
an `HttpOrigin` on the ALB and the AllViewer origin-request policy forwards the
`sentry-trace` and `baggage` headers through to it — so the fallback branch
carries it and the headers survive the CDN.

The day `apiBase` becomes an absolute `https://api.…` host, that fallback is
skipped, the regex fails, and trace propagation stops with no error anywhere.
**Do not pre-empt that with a wildcard-origin pattern.** A regex like
`/^https?:\/\/[^/]+\/api(?:\/|$)/` matches any host's `/api` path and would put
this deployment's trace ids on a third party's request — the exact thing the
line's comment says it exists to prevent. When the split-origin day comes, add
that host *literally*, in the same change that adds
`Access-Control-Allow-Headers: sentry-trace, baggage` to the API's preflight
answer — without it the browser blocks the REQUEST, not just the header.

What is missing:

1. **No `release`.** `deploy.yml` already logs `SPA deployed: ${{ github.sha }}`
   and tags the API image with the same sha. The identifier exists and is never
   handed to either SDK, so "first seen in release" and regression detection are
   unavailable on both halves of the same deploy.
2. **The environment tag is a lie in production.** `environment.production` is
   hard-coded `false` in the one and only environment file (`src/environments/`
   contains exactly `environment.ts`), `angular.json` has no `fileReplacements`,
   and `deploy.yml`'s sed rewrites `sentryDsn` and nothing else. Every browser
   event from the live CloudFront site is filed under `development` while the
   API — `ENV=prod` from the CDK task definition — is filed under `prod`. The
   two ends of one distributed trace land in different Sentry environments, and
   any alert rule, quota control or issue filter scoped to production silently
   sees no web traffic at all.
3. **No `TraceService`,** so there are no navigation spans. Not
   unparameterized ones — none. See below; this one fails silently by
   construction.

### The bundle budget — measured, and the number in `AGENTS.md` is not the ceiling

**The claim that the dynamic import keeps Sentry out of the initial chunk is
true, and it is not the whole story.** Measured against
`apps/web/dist/web/browser`, rebuilt 2026-09-08 with `npx ng build`
(`@angular/build` 22.1.3):

`index.html` references exactly one script (`main-TOJPYNOO.js`, 127,572 B) and
one stylesheet (`styles-PMMW2R37.css`, 51,322 B), with no `modulepreload`
links. The Sentry code lives in `chunk-Dx0mqGc0.js`, reached only through the
`await import()` in `main.ts`. Angular's `initial` budget sums only chunks
flagged initial (`@angular/build/src/utils/bundle-calculator.js:155-166`), so a
lazy chunk never counts — and `@angular/build/src/tools/esbuild/budget-stats.js`
skips anything that is not `.js` or `.css` outright, which is why a `.map` can
never break a budget either. That half of the claim holds.

The other half does not. The chunk is **460,113 B raw, 152,367 B gzip,
129.40 kB brotli**, and `bootstrap()` *awaits it before `bootstrapApplication`*
— so on any deployment where `WEB_SENTRY_DSN` is set, a student on a phone
waits out a second round trip and ~129 kB of Sentry (CloudFront's behaviours
set `compress=True`, and Angular's own "estimated transfer size" column is
brotli — `@angular/build/src/tools/esbuild/utils.js:115`) before Angular renders
its first frame. The comment at `environment.ts:12-15` — "the initial bundle
pays nothing" — is true of the budget and true when the DSN is blank, and false
about what the student waits for.

**The reason it is that large is the namespace import, and the obvious fix does
not work.** `const sentry = await import('@sentry/angular')` makes that module a
bundler entry point whose every export must be retained — and because
`sentry-angular.mjs:3` is `export * from '@sentry/browser'`, "every export"
means Session Replay's rrweb recorder, the User Feedback widget and the
profiling integration, none of which REEP calls.

Destructuring in a `.then()` does **not** undo that. Measured by building the
proposed `main.ts` verbatim, twice, with the repo's own `ng build`:

| what `main.ts` does | raw | gzip | brotli | Replay shipped? |
| --- | --- | --- | --- | --- |
| `await import('@sentry/angular')` (today) | 460,113 B | 152,367 B | 129.40 kB | **yes** |
| `.then(({ init, … }) => ({ … }))` | 276,331 B | **92,792 B** | 80.06 kB | **yes** |
| `await import('./app/core/sentry-lazy')` | **151,343 B** | **51,235 B** | **45.14 kB** | no |

A dynamic import materializes the module namespace; what the caller destructures
from the resulting promise is a runtime detail the bundler cannot use. The
esbuild bisect is unambiguous — namespace 469,376 B raw, `.then`-destructure
469,533 B raw, i.e. very slightly *worse* than doing nothing. Angular's extra
passes recover about half of it in a real build, and leave the recorder in.

What works is a module the bundler *can* tree-shake, because its own export list
is the whole surface. One new file:

```typescript
// apps/web/src/app/core/sentry-lazy.ts
//
// The ONLY place @sentry/angular is named. main.ts dynamic-imports THIS module,
// not the package: a dynamic import retains the imported module's whole
// namespace, and @sentry/angular re-exports all of @sentry/browser, so
// `await import('@sentry/angular')` ships rrweb, the feedback widget and the
// profiler to reach four symbols. Measured 2026-09-08 with `ng build`:
// 460,113 B raw that way, 151,343 B this way.
//
// NOTHING may import this file statically — that would put the SDK in the
// initial chunk, which is the one thing the lazy import exists to prevent.
export {
  init,
  browserTracingIntegration,
  createErrorHandler,
  TraceService,
} from '@sentry/angular';
```

51,235 B gz lines up with the SDK's own CI ceiling of **52 KB** for
`@sentry/browser (incl. Tracing)` on npm (54 KB for the CDN bundle), and the
91 KB ceiling with Replay explains the middle row:
[`.size-limit.js`](https://github.com/getsentry/sentry-javascript/blob/master/.size-limit.js).
**REEP is currently paying more for tracing alone than tracing-plus-replay would
cost if it were imported properly** — worth knowing before anyone reads the
Replay verdict below as a cost argument.

**The `~142 kB` in `AGENTS.md` is a measurement, not the budget, and the
sentence attached to it overstates the margin.** Measured 2026-09-08 the initial
payload is 178,894 B (127,572 B JS + 51,322 B CSS), and
`apps/web/angular.json`'s production budget is `initial` at 250 kB warning /
**400 kB error**. Angular's kB is 1000 bytes
(`bundle-calculator.js:17`, `BYTES_IN_KILOBYTE = 1000`), so that is 221 kB of
headroom to the error, not "close enough that one re-eager-ed route fails
`ng build`". A route re-eagered with a `component:` reference will usually pass.
The 1.23 MB regression the rule was written for would fail loudly, so the rule
stands and lazy routing stays mandatory; what does not stand is the belief that
CI is watching it closely — and CI *is* running the check
(`.github/workflows/ci.yml:200`, `npx ng build` in the `web` job), which is what
makes the loose budget the whole story. That belief is repeated in
`AGENTS.md:45`, in
`apps/web/src/app/features/student/interviews/interviews.component.ts:19` and in
`app.routes.ts:272`/`:295` — re-measure rather than trusting any of them.
Nothing here changes if you tighten the budget; if you want the guard the
sentence describes, `maximumError: "220kB"` (220,000 B, ~41 kB of headroom) is
the number that produces it. *Unverified: nobody has run that value against a
real re-eager-ed route.*

### `release` and `environment` must come from the build

Both are one field in `environment.ts` and a sed in `deploy.yml`, each proved by
a `grep` — follow the existing "Point the SPA at Sentry" step's pattern exactly,
because a sed against a source literal silently no-ops when someone reformats
the file, and the grep is the only thing that catches it.

```typescript
// apps/web/src/environments/environment.ts
export const environment = {
  production: false,   // rewritten to true by deploy.yml
  apiBase: '/api',
  sentryDsn: '',       // rewritten by deploy.yml from WEB_SENTRY_DSN
  release: 'dev',      // rewritten by deploy.yml to the commit sha
};
```

**These two rewrites are a step of their own, placed BEFORE "Point the SPA at
Sentry".** That step's first line is `if [ -z "$WEB_SENTRY_DSN" ]; then … exit 0;
fi`, so anything added after it is skipped entirely on a deployment without
client telemetry — and `environment.production` is not Sentry's alone. Only the
`sentryDsn` rewrite belongs behind the DSN guard.

```yaml
# .github/workflows/deploy.yml — new step, immediately before "Point the SPA at Sentry"
- name: Stamp the build (release + environment)
  working-directory: apps/web
  run: |
    # Unconditional, and NOT inside the DSN guard below: a deployment without
    # client telemetry must still ship a production build. Each sed is proved
    # by the grep under it — a rewrite that silently misses is a production
    # bundle that believes it is a dev build, which is the state this exists
    # to prevent.
    sed -i "s|production: false|production: true|"           src/environments/environment.ts
    sed -i "s|release: 'dev'|release: '${{ github.sha }}'|"  src/environments/environment.ts
    grep -q "production: true"                src/environments/environment.ts
    grep -q "release: '[0-9a-f]\{7,\}'"       src/environments/environment.ts
```

The `release` value must be the same string `sentry-cli` uploads maps under, or
the maps resolve against nothing. That is the source-map section's contract, not
this one's; what this section owes it is a release string that exists.

### `TraceService` — the navigation spans that silently do not exist

**REEP has no navigation spans, and the SDK will never tell you.** Verified in
the installed `@sentry/angular@10.71.0`
(`node_modules/@sentry/angular/fesm2015/sentry-angular.mjs:243-246`, quoted in
its downlevelled fesm2015 form): the Angular package's
`browserTracingIntegration` is a wrapper that hard-disables the browser SDK's
own history instrumentation and hands the job to `TraceService`:

```javascript
if (options.instrumentNavigation !== false) {
    instrumentationInitialized = true;
}
return browserTracingIntegration$1(Object.assign(Object.assign({}, options), { instrumentNavigation: false }));
```

`TraceService` is `@Injectable({providedIn: 'root'})` (line 397), so it is never
constructed unless something injects it, and nothing in `apps/web/src` mentions
it. The one diagnostic the SDK emits for this — *"Angular integration has
tracing enabled, but Tracing integration is not configured"* (line 279) — is
gated on `instrumentationInitialized` being **false** (line 277), and REEP sets
it true by calling `browserTracingIntegration()` normally. So the failure mode
is exactly the one that has no symptom: a pageload span, no `navigation` spans,
no `ui.angular.routing` children, no route templates, and no warning in the
console. Every one of the 44 `loadComponent` routes in `app.routes.ts` is
invisible, which means the chunk-load failures described below produce no trace
of the navigation that triggered them.

The fix is **one provider**, and it must go in `main.ts` rather than
`app.config.ts` — `app.config.ts` cannot import the SDK without dragging it into
the initial chunk, which is the one thing the dynamic import is there to prevent.

```typescript
provideAppInitializer(() => { inject(sentry.TraceService); }),
```

`TraceService` carries its own factory with `deps: [{ token: Router }]`
(`sentry-angular.mjs:396`) and is `providedIn: 'root'`, so the
`{ provide: TraceService, deps: [Router] }` provider that older guides show is
redundant; the initializer alone constructs it, and dropping it is the class
never being instantiated and no router event observed. Angular 22 uses
`provideAppInitializer`, not the `APP_INITIALIZER` +
`useFactory: () => () => {}` form. Sentry's own snippet
([`packages/angular/README.md`](https://github.com/getsentry/sentry-javascript/blob/master/packages/angular/README.md))
writes it as an expression body, `provideAppInitializer(() => inject(TraceService))`,
which returns the service instance; the parameter is typed
`() => Observable<unknown> | Promise<unknown> | void`
(`@angular/core/types/core.d.ts:1986`), so use a block body.

### The sampler — what the browser's rate governs, and what it does not

**The flat `tracesSampleRate: 0.2` in the browser decides what the API records
for every `/api/*` request the SPA makes — and nothing at all for the
interview.** The distinction matters, because the obvious version of this
argument is wrong.

For fetch and XHR, sampling is head-based and the SPA is the trace root (static
bundle, no SSR, no `<meta name="sentry-trace">` in the built `index.html`). The
browser stamps its decision into `sentry-trace`, CloudFront's AllViewer policy
forwards it, and sentry_sdk honours a parent decision over its own rate
(`.venv/Lib/site-packages/sentry_sdk/tracing.py:1228-1233`). For those, raising
`SENTRY_TRACES_SAMPLE_RATE` on the API recovers nothing the browser dropped.

The interview socket is a different trace. `@sentry/browser@10.71.0` instruments
fetch and XHR only — the sole `WebSocket` reference in the package is inside
`browserApiErrorsIntegration`, which `@sentry/angular`'s
`getDefaultIntegrations()` deliberately omits — and
`apps/web/src/app/core/interview.service.ts:2253` opens a bare
`new WebSocket(...)`, on which a browser cannot set headers anyway. So no
`sentry-trace` reaches `continue_trace(_get_headers(scope))` in sentry_sdk's
ASGI middleware, and the `websocket.server` transaction opened at
`apps/api-py/app/routers/interview.py:1032` is a fresh root sampled at the API's
own rate. **Seeing the server half of an interview is a change to
`SENTRY_TRACES_SAMPLE_RATE`, not to anything here.** What the browser sampler
buys is the client half: the pageload, the consent and status fetches, the
transcript reads.

```typescript
tracesSampler: (ctx) => {
  // Root spans only, and in this SPA every root is named with a pathname:
  // pageload is `WINDOW.location.pathname` (@sentry/browser
  // tracing/browserTracingIntegration.js:278), navigation is `parsed?.pathname`
  // (line 300), and TraceService names its own from
  // stripUrlQueryAndFragment(navigationEvent.url) (sentry-angular.mjs:287) —
  // Angular router URLs being root-relative. The ROUTE TEMPLATE is applied
  // later, on ResolveEnd, so match paths here, never route patterns.
  //
  // The SPA is the trace root, so this decision is what the API inherits for
  // /api/* fetches. It does NOT reach the interview WebSocket, which the
  // browser SDK does not instrument.
  if (ctx.name.startsWith('/student/assistant')) return 1.0;
  if (ctx.name.startsWith('/student/interviews')) return 1.0;
  if (ctx.name === '/login') return 0.05;
  return 0.1;
},
```

Keep the interview screens at 1.0: they are the path the tracing exists for, and
the client trace is the only record of what the student's browser did around a
480-second Nova session (`nova_sonic_connection_seconds`, `app/config.py:510`).
*Unverified: "a handful of interviews a day" is an assumption about volume, not
a measurement — no Sentry project data was available to this audit.* Everything
else can go *down* from 0.2 — `/login` in particular is the highest-volume
unauthenticated pageload in the deployment and its trace answers nothing.

Three options to set at the same time, all rule 1 applied to telemetry:

- **`enableLogs: false`.** Verified in the installed `@sentry/core@10.71.0`
  (`build/cjs/client.js:119`): `enableLogs` resolves to `true` when unset, and
  Sentry's docs confirm the flip landed in exactly that version —
  ["enabled by default in version 10.71.0 and above"](https://docs.sentry.io/platforms/javascript/logs/) —
  which is the floor in `apps/web/package.json`. Nothing is captured *today*,
  because nothing calls `Sentry.logger.*` and `consoleLoggingIntegration` is not
  in the Angular package's default set. It is one line away from browser console
  output leaving the machine, and logs bypass `beforeSend` entirely (they have
  their own `beforeSendLog` hook).
- **`enableMetrics: false`.** The same flip, one line down: `client.js:123`
  resolves `enableMetrics` to `true` when unset (`options.d.ts:544-549`,
  `@default true`). Nothing emits metrics today either. The API's posture is a
  set of explicit `False`s with the incident written above them; the SPA's
  should match, for both channels.
- **`beforeSend`,** as the last stop rather than the only one. There is no
  `before_send` on the API either, so every PII defence in this stack is a
  constructor flag plus discipline at call sites. Strip the query string from
  `event.request.url` **and from the breadcrumb trail** — the fetch/XHR
  breadcrumbs record the full request URL, query included
  (`@sentry/browser/.../integrations/breadcrumbs.js:142-145`), so stripping only
  `event.request.url` leaves open the exact case this closes: a future screen
  that puts a USN in a query param.

### Noise — what the SDK already drops, what it does not, and what to fix instead

**Most of the classic browser noise is already filtered and adding it to
`ignoreErrors` would be cargo cult.** Read out of the installed
`@sentry/core@10.71.0` (`build/cjs/integrations/eventFilters.js:10-31`), the
default filter — active, because `@sentry/angular`'s `getDefaultIntegrations()`
includes `inboundFiltersIntegration()`, the deprecated alias for the same
integration — already drops `Script error.`, `ResizeObserver loop completed with
undelivered notifications.`, the googletag redefine, the `solana` extension
redefine, `_AutofillCallbackHandler`, the CEFSharp Java exception, the Instagram
webview `Object Not Found Matching Id`, the Google Search App
`undefined is not an object`, and two more. The Angular package also
deliberately omits `browserApiErrorsIntegration` (its `getDefaultIntegrations`
says why, at `sentry-angular.mjs:29-36`), so nothing wraps
`setTimeout`/`addEventListener` to catch errors ahead of Angular's
`ErrorHandler` at lower fidelity.

What is left is four things, and **three of them are bugs, not noise.** Naming
them honestly matters: the deployment has never had a confirmed DSN in the tree,
so these are predictions from the shape of the code, not counts from an issue
stream.

**1. Chunk-load failure after every deploy — a real break, and not the error
name you expect.** `app.routes.ts` has 44 `loadComponent` routes, and
`deploy.yml`'s publish step runs `aws s3 sync . --delete`, which removes the
previous build's hashed chunks the instant the new build lands. Every tab open
across a deploy throws on its next navigation. The workflow's own comment
identifies the mechanism — "a browser keeps loading the old app shell that
points at chunks the new deploy has already deleted" — and the `no-cache` on
`index.html` fixes it for *new* page loads only.

The error name matters, because the usual filter list is written for webpack and
this app has none: `dist/web/browser/main-TOJPYNOO.js` contains 43 native
``import(`./chunk-….js`)`` sites and zero `__webpack` references, so
`ChunkLoadError` and `Loading chunk N failed` **cannot occur here**. What you
will see is the browser's own wording, and it differs per engine. Filter those,
but fix the symptom: a `RouterEvent`/`ErrorHandler` branch that detects the
failure and reloads the page is what the student needs. Ignoring the event
without that leaves them staring at a dead sidebar. *Unverified: the exact
strings are the documented cross-engine set, not observations of REEP's own
events. Confirm against a real issue stream before trimming the list.*

**2. An unhandled rejection on sign-out.** `app-shell.component.ts:107-110`:

```typescript
async signOut(): Promise<void> {
  await this.auth.logout();
  await this.router.navigate(['/login']);
}
```

`auth.logout()` is `firstValueFrom(this.http.post(\`${environment.apiBase}/auth/logout\`, …))`
with no catch anywhere in the chain (`auth.service.ts:81-86`), and the template
binds `(click)="signOut()"` (`app-shell.component.html:13`), which discards the
promise. Any non-2xx or network blip becomes an unhandled rejection — reachable
twice, once through `provideBrowserGlobalErrorListeners()`
(`app.config.ts:9`; Angular's listener calls `e.preventDefault()`, which does
not stop Sentry's own `globalHandlersIntegration` listener) and once through
that integration directly. `dedupeIntegration` compares only against the
immediately previous event
(`@sentry/core/build/cjs/integrations/dedupe.js:9-28`), and the two captures
have different shapes for a non-`Error` rejection, so it will not reliably
collapse them. It is also a genuine bug: `_session.set(null)` never runs, the
navigation never happens, and the student is left on the page with no
explanation. `try { … } finally { navigate }` fixes both.

**3. `ResizeObserver loop limit exceeded`,** the *legacy* message, which the
default filter does not cover — it only covers the modern
"loop completed with undelivered notifications". The source is REEP's own
`features/admin/analytics/analytics.component.ts:316` (this read `features/director/` until the 2026-09-10 role removal renamed the directory), which calls
`this.sun?.resize(); this.bar?.resize();` synchronously inside the observer
callback. The correct pattern is one file away in
`shared/voice-visualizer.ts:954`, which sets a flag and resizes on the next
frame precisely because "doing that inside the observer causes a visible flash
and a resize-loop warning". Fix the component; do not add the string to
`ignoreErrors`.

**4. Browser-extension frames**, which need `denyUrls` and not `ignoreErrors`.
`denyUrls` matches `_getEventFilterUrl(event)` — the last non-`<anonymous>`,
non-`[native code]` frame of the root exception's stacktrace
(`eventFilters.js:144-160`) — and only for error events, never transactions.
There is no default. *Unverified: that this is the largest remaining category
for a public, student-facing SPA is a prediction from the deployment's shape. No
Sentry project data was available to this audit.*

Aborted operations, for the record, are **not** a source and should not be
filtered: `interview.service.ts:1790` returns on an `AbortError` from
`mic.start()`, `agent.component.ts:278` marks the turn stopped, the SSO probe,
the sidebar USN fetch and the interview status probe all swallow deliberately
with a comment saying why, and the student landing page uses
`Promise.allSettled` (`home.component.ts:506`). The 88 `await fetch(` sites in
`apps/web/src` are quiet on purpose. Adding a broad network-error filter would
hide a future regression and buy nothing today.

That yields a short and defensible list:

```typescript
ignoreErrors: [
  // Native dynamic import(), so these are the BROWSER's words, one per engine:
  // Chrome/Edge, Firefox, Safari. 'ChunkLoadError' and /Loading chunk \S+
  // failed/ are webpack names and are deliberately absent — @angular/build is
  // esbuild, and the built main chunk has 43 native import() sites and zero
  // __webpack references, so neither string can ever be produced here.
  // Filtered so one deploy does not bury a week of genuine issues; the fix is
  // a reload handler, not silence.
  /Failed to fetch dynamically imported module/,
  /error loading dynamically imported module/,
  /Importing a module script failed/,
  // The LEGACY ResizeObserver message. The modern one is in the SDK's own
  // default list; this one is not, and analytics.component.ts still emits it.
  'ResizeObserver loop limit exceeded',
],
denyUrls: [
  /extensions\//i, /^chrome:\/\//i, /^chrome-extension:\/\//i,
  /^moz-extension:\/\//i, /^safari-(web-)?extension:\/\//i,
],
```

### Session Replay — no, and here is the condition

**REEP should not enable Session Replay, and the reason is not the bundle.**

The default privacy posture is genuinely strong — verified in the installed
`@sentry/replay` (`build/npm/esm/index.js:8147-8156`): `maskAllText = true`,
`maskAllInputs = true`, `blockAllMedia = true`, `networkDetailAllowUrls = []`,
`networkDetailDenyUrls = []`. Resting the decision on those defaults is the
mistake. They are defaults; the whole point of a config option is that someone
can change it, and the change that ends this badly is a single line.
`networkDetailAllowUrls: [window.location.origin]` is the example in Sentry's
own documentation, `networkCaptureBodies` is already `true` (line 8156), and on
this app that one line ships full `/api/*` request and response bodies — marks,
attendance, USN, the interview transcript — to a third party, up to
`NETWORK_BODY_MAX_SIZE` = 150,000 characters each (line 15). The API's
equivalent door is bolted shut by `max_request_body_size="never"` *and* pinned
textually by `tests/test_codebase_guards.py:600`
(`test_sentry_never_ships_local_variables_or_request_bodies`). Replay would open
a door of the same shape on the browser side with nothing pinning it.

The screens make it concrete. `/student/profile` renders a USN.
`/student/records` and `/student/english` render marks. `/student/interviews`
fetches `…/transcript` (`interviews.component.ts:279`) and renders the student's
spoken answers verbatim in the DOM; `/director/interviews` is the staff
Interview Records screen, every interview named and downloadable;
`/director/exports` renders cohort data; `/mentor/mentees` renders meeting
notes. A replay is a reconstruction of that DOM.

And **there is no way to block a screen outright.** Replay is per *session*, not
per route: masking and blocking are CSS-selector sets applied to a recording
that is already running. Stopping it on navigation (`Sentry.getReplay()?.stop()`)
is a hand-rolled guard that has to fire before the first paint of the very
routes that matter, on a lazily-loaded component, on every one of 44 routes,
forever — and a guard that must run before a lazy chunk resolves is a guard that
will one day not. The safe version of "enable Replay except on the PII screens"
does not exist in this SDK.

The disclosure settles it. `interview_consents` carries three booleans — live
AI, store transcript, store audio — and
`features/student/interviews/interviews.component.ts:13-14` says in as many
words that the transcript "is kept on the college's server and that their mentor
and the placement director can read it". A replay puts a rendering of that same
transcript on Sentry's servers, which is a fourth disclosure nobody has made.
The three booleans exist precisely so that "they consented" cannot be an
unfalsifiable claim; adding a fourth destination without a fourth checkbox
makes it one again.

**Replay becomes acceptable when all four of these are true, and not before:**

1. The disclosure exists — a scope the student can see and refuse, in the same
   shape as `scope_store_audio`, or a plainly worded notice on the screens that
   render their records.
2. The configuration is pinned by a test the way `include_local_variables=False`
   is pinned, asserting textually that `maskAllText`, `maskAllInputs` and
   `blockAllMedia` are `true`, that `networkDetailAllowUrls` is empty, and that
   `networkDetailDenyUrls` contains `/\/api\//`. A privacy default nothing
   guards is a preference.
3. `replaysSessionSampleRate: 0` and `replaysOnErrorSampleRate` low — record the
   sessions that actually broke, never a sample of ordinary use.
4. `sentry-lazy.ts` has landed, so the marginal cost is a measured ~41 kB gz
   (51,235 → ~92,000 B, the middle row of the table above being roughly what
   errors+tracing+replay costs) rather than a fresh 92 kB on top of a chunk that
   is already carrying the recorder unused.

Until then the correct configuration is the one that exists: `replayIntegration`
is never constructed, and importing through `sentry-lazy.ts` stops shipping the
recorder to students who will never trigger it. Note that
`replaysSessionSampleRate` and `replaysOnErrorSampleRate` have **no** `0`
default — with both unset the integration disables itself and logs "Replay is
disabled because neither … are set" (`@sentry/replay/build/npm/esm/index.js:8402-8408`).
Not constructing the integration is the control; those two options are inert.

### The proposed `apps/web/src/main.ts`

The icon-font block below `bootstrap()` is untouched and omitted here. This
exact file plus `sentry-lazy.ts` was compiled with `npx ng build` on 2026-09-08:
it type-checks, and it produces a 151,343 B / 51,235 B gz `sentry-lazy` chunk
with no Replay in it, at a cost of well under 1 kB on the initial bundle.

```typescript
import { ErrorHandler, inject, provideAppInitializer } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';
import { environment } from './environments/environment';

/**
 * Sentry is initialized before Angular bootstraps so framework errors are
 * captured through Angular's ErrorHandler, not only uncaught browser errors.
 * The SDK remains a dynamic import when no DSN is configured.
 *
 * THROUGH ./app/core/sentry-lazy, NOT '@sentry/angular' DIRECTLY. A dynamic
 * import retains the imported module's entire namespace, and @sentry/angular is
 * `export * from '@sentry/browser'` — so importing the package here ships
 * rrweb, the feedback widget and the profiler to reach four symbols. Measured
 * 2026-09-08 with ng build: 460,113 B raw / 152,367 B gz that way, 151,343 B /
 * 51,235 B gz through the re-export module. Destructuring in a .then() does NOT
 * fix it (276,331 B / 92,792 B gz, recorder still included) — the bundler
 * cannot use a runtime property access. The chunk is lazy either way and never
 * counts against the initial budget, but it IS awaited before
 * bootstrapApplication, so it is a round trip the student waits through.
 */
async function bootstrap(): Promise<void> {
  const sentry = environment.sentryDsn
    ? await import('./app/core/sentry-lazy')
    : null;

  if (sentry) {
    sentry.init({
      dsn: environment.sentryDsn,
      environment: environment.production ? 'production' : 'development',
      // The commit sha, rewritten by deploy.yml. Must equal what sentry-cli
      // uploads the source maps under, or every prod frame stays minified.
      release: environment.release,

      // MUST be @sentry/angular's wrapper, not @sentry/browser's: it forces
      // instrumentNavigation:false and hands navigation spans to TraceService.
      integrations: [sentry.browserTracingIntegration()],

      // ONE pattern, matched against the PATHNAME and only for same-origin
      // requests — which is what CloudFront gives us (the /api/* behaviour and
      // the SPA share one distribution; infra/cdk/reep_core/stack.py). Do NOT
      // add a wildcard-origin regex "for the future": it would attach
      // sentry-trace to any host serving a /api path. The day apiBase becomes
      // an absolute cross-origin URL, add THAT host literally, together with
      //   Access-Control-Allow-Headers: sentry-trace, baggage
      // on the API's preflight — without it the browser blocks the REQUEST.
      tracePropagationTargets: [/^\/api(?:\/|$)/],

      // Root spans are named with a pathname here (pageload, navigation and
      // TraceService alike), so match paths, never route templates. This
      // decision is what the API inherits for /api/* FETCHES. It does not
      // reach the interview WebSocket — the browser SDK does not instrument
      // WebSocket, so that transaction is sampled by the API's own
      // SENTRY_TRACES_SAMPLE_RATE and must be raised there.
      tracesSampler: (ctx) => {
        if (ctx.name.startsWith('/student/assistant')) return 1.0;
        if (ctx.name.startsWith('/student/interviews')) return 1.0;
        if (ctx.name === '/login') return 0.05;
        return 0.1;
      },

      sendDefaultPii: false,   // deprecated in 10.71.0; do NOT swap for a bare
                               // `dataCollection`, whose base defaults are
                               // permissive the moment the key is present
      enableLogs: false,       // defaults to TRUE from 10.71.0
      enableMetrics: false,    // same, one line down in the same resolver

      ignoreErrors: [
        /Failed to fetch dynamically imported module/,
        /error loading dynamically imported module/,
        /Importing a module script failed/,
        'ResizeObserver loop limit exceeded',
      ],
      denyUrls: [
        /extensions\//i, /^chrome:\/\//i, /^chrome-extension:\/\//i,
        /^moz-extension:\/\//i, /^safari-(web-)?extension:\/\//i,
      ],

      // Last stop before anything leaves the machine (rule 1, applied to
      // telemetry). The constructor flags above are the defence; this is the
      // backstop for the path nobody has written yet. Breadcrumbs are included
      // deliberately: the fetch/xhr breadcrumb records the full request URL.
      beforeSend(event) {
        if (event.request?.url) event.request.url = event.request.url.split('?')[0];
        for (const crumb of event.breadcrumbs ?? []) {
          const url = crumb.data?.['url'];
          if (typeof url === 'string') crumb.data!['url'] = url.split('?')[0];
        }
        delete event.user;
        return event;
      },
    });
  }

  const config = sentry
    ? {
        ...appConfig,
        providers: [
          ...appConfig.providers,
          { provide: ErrorHandler, useValue: sentry.createErrorHandler() },
          // Without this there are no navigation spans at all, and the SDK
          // logs nothing: its own warning is gated on a flag that
          // browserTracingIntegration() has already set true. TraceService is
          // providedIn:'root' with Router in its own factory, so forcing
          // construction is the entire fix — no extra provider needed.
          provideAppInitializer(() => {
            inject(sentry.TraceService);
          }),
        ],
      }
    : appConfig;

  await bootstrapApplication(App, config);
}
```

One trade-off this keeps rather than fixes: `init()` runs after the dynamic
import resolves, so anything thrown before that — module evaluation, the
icon-font block — is not captured. Moving `init` earlier means a static import
and ~51 kB gz on the initial chunk. The lazy chunk is the right call; the gap is
worth knowing about when an error "should have been reported and wasn't".

### The settings, in one table

| option | today | proposed | what the change buys |
| --- | --- | --- | --- |
| `release` | absent | `environment.release`, the commit sha from `deploy.yml` | Without it "first seen in this release" cannot be evaluated, regression detection is off, and uploaded source maps have no release to group under. The sha is already logged by both deploy jobs. |
| `environment` | `'development'` in production | `'production'`, via a sed in a new unconditional "Stamp the build" step | Today every live browser event is filed under `development` while the API is filed under `prod`, so the two halves of one trace are in different environments and every production-scoped alert rule sees an empty web project. |
| import form | `await import('@sentry/angular')` | `await import('./app/core/sentry-lazy')` | 460,113 → 151,343 B raw, 152,367 → 51,235 B gz, 129.40 → 45.14 kB brotli on a chunk that is awaited before first paint, and the Replay recorder stops shipping. A `.then()` destructure does **not** achieve this (276,331 B, recorder still present). |
| `TraceService` | not provided | `provideAppInitializer(() => { inject(sentry.TraceService); })` | There are currently **no** navigation spans — the Angular wrapper disables the browser SDK's own instrumentation and nothing takes over. The SDK's warning for this is unreachable, so the gap has no symptom. |
| `tracesSampleRate` | flat `0.2` | replaced by `tracesSampler` | Governs the client half of every trace, and — head-based — what the API records for `/api/*` fetches. Keeps `/student/assistant` at 1.0 and drops `/login` to 0.05. It does **not** govern the interview WebSocket; that is `SENTRY_TRACES_SAMPLE_RATE` on the API. |
| `tracePropagationTargets` | `[/^\/api(?:\/|$)/]` | **unchanged** | Correct today via the same-origin pathname fallback, which CloudFront's single distribution guarantees. Do not add a wildcard-origin pattern — it would attach trace headers to third parties. Revisit only if `apiBase` goes cross-origin, alongside the CORS header. |
| `enableLogs` | unset ⇒ **`true`** since 10.71.0 | `false` | Captures nothing today because nothing calls `Sentry.logger.*`. One such call, or one `consoleLoggingIntegration`, and browser console output leaves the machine — and logs bypass `beforeSend`, having their own `beforeSendLog`. |
| `enableMetrics` | unset ⇒ **`true`** since 10.71.0 | `false` | The same default flip in the same resolver, one line down. Nothing emits metrics today; the API's posture is explicit `False`s and the SPA's should match on both channels. |
| `beforeSend` | absent | strip the query string from `event.request.url` **and every breadcrumb URL**, delete `event.user` | Every PII defence in the SPA is currently a constructor flag. This is the backstop for a URL that carries a USN — including in the fetch breadcrumb trail, which records the full request URL. |
| `ignoreErrors` / `denyUrls` | absent | the lists above | The SDK's default filter already covers `Script error.` and the modern ResizeObserver message. What it does not cover is dynamic-import failure after `s3 sync --delete` (three browser-specific strings, none of them the webpack `ChunkLoadError`), the legacy ResizeObserver message, and extension frames. |
| `replayIntegration` | not used | **stays unused** | The screens render USNs, marks and interview transcripts; the disclosure covers the college's server, not Sentry's; and Replay cannot be blocked per route. See the four conditions above. |
| `replaysSessionSampleRate` / `replaysOnErrorSampleRate` | unset (no `0` default) | **leave unset** | With both unset the integration disables itself and says so. Stating them is optional theatre; leaving Replay unconstructed is the actual control. |
---

## Releases, source maps and suspect commits

**Neither SDK is given a `release`, and the identifier has existed all along.**
`.github/workflows/deploy.yml` already writes `${{ github.sha }}` into four
places — the sha image tag (built at :84, pushed at :85), `API deployed: <sha>`
(:162) and `SPA deployed: <sha>` (:231) — and hands it to nothing. The second
image tag is `:latest`, which is what the task definition references and is not
an identity. The consequence is not cosmetic. `docs/deployment-process.md` §8.5
makes "a new Sentry issue type first seen after this deploy" an *abort
criterion*, and today that sentence can only be evaluated by wall-clock time —
which is what §8.5 tells you to do, and it is the weakest form of the check.
A release buys the three things a clock cannot: `firstRelease:` as a query term,
Sentry's regression detection, and a deploy marker that says which build the
window belongs to. It also tells task families apart — if `blueGreen` is ever
turned on (`infra/cdk/reep_core/stack.py:240`, default false, and it needs
`hardenEcs`), three families running three tags produce events that are
identical in every field.

The second half is worse and quieter. `apps/web/angular.json`'s `production`
configuration sets `budgets` and `outputHashing: "all"` and **does not set
`sourceMap`** — it appears only under `development`, and the
`@angular/build:application` schema default is `false`
(`apps/web/node_modules/@angular/build/src/builders/application/schema.json`,
`properties.sourceMap.default`). So every browser stack trace Sentry receives
through `createErrorHandler()` is single-letter frames against hashed chunk
names. The client half of the pane of glass `docs/aws-deployment.md` §5 promises
is, today, unreadable.

Everything in this section is **[NOT WRITTEN]** in the sense
`docs/deployment-process.md` §"Status markers" defines: none of it exists in the
tree. There is no `sentry-cli`, no `SENTRY_AUTH_TOKEN`, no `SENTRY_ORG`, no
`SENTRY_PROJECT`, no `SENTRY_RELEASE`, and no `release` option passed to either
SDK, anywhere under `.github/`, `apps/web/src` or `apps/api-py/app`. (Grep for
the bare word `release` and you will hit `interview.service.ts`'s compressor
ballistics and the director screens' `release(student)` — neither is Sentry.)
Nothing has to be undone; nothing can be assumed present.

**Check the premise before building on it.** `docs/deploy-from-chrome.md:102`
seeds the operator-owned `reep/external` secret with `"SENTRY_DSN":""`, and
`apps/api-py/app/main.py:59` initialises the SDK only when that value is
non-empty. Whether the live secret still holds `""` is **unverified from this
repository** — it is one read, and if it is blank then every step below records
releases for a project that has never seen an event:

```bash
aws secretsmanager get-secret-value --secret-id reep/external \
  --query SecretString --output text | python -c "import json,sys;print(bool(json.load(sys.stdin)['SENTRY_DSN']))"
```

### The release name is the commit SHA, and that choice has consequences

One name, `${{ github.sha }}`, shared by the api and web projects. Sentry
recommends either `package@version` semver or the identifying hash
([naming releases](https://docs.sentry.io/product/releases/naming-releases/)),
and the hash is the right one here: this repo has no version to bump
(`apps/web/package.json` is `"0.0.0"`), the sha is already the deploy identity in
four places, and it is reproducible from the build with no bookkeeping.

**Take the consequence knowingly.** Sentry detects whether a project is on
semantic or SHA/time-based versioning and changes regression detection and
`release:latest` sorting accordingly; a bare SHA is not semver, so ordering falls
back to release date. That is correct for a continuously deployed service, and it
is exactly why `releases finalize` below is not a formality — Sentry's own words
are that finalizing "populate[s] a second timestamp on the release record, which
is prioritized over `date_created` when sorting releases"
([cli/releases](https://docs.sentry.io/cli/releases/)) — and it defines "the next
release" for resolve-in-next-release. Tagging `v1.0.0` instead would be the worst
of both: Sentry does not read `v1.0.0` as semver either, so you would get date
ordering anyway, with a name that lies about it.

Release names cannot contain newlines, tabs, forward or back slashes, cannot be
`.`, `..` or a space, and cap at 200 characters. A SHA is safe; a branch name is
not.

### Angular must emit source maps, and they must be hidden

```diff
             "production": {
               "budgets": [
                 …
               ],
-              "outputHashing": "all"
+              "outputHashing": "all",
+              "sourceMap": {
+                "scripts": true,
+                "styles": false,
+                "hidden": true,
+                "sourcesContent": true
+              }
             },
```

Every key above is in the builder's schema (`schema.json`,
`properties.sourceMap`), including `sourcesContent`, whose schema default is
already `true` — it is written out because the next subsection turns on it.

`hidden: true` emits the maps and suppresses the `//# sourceMappingURL=`
comment, so a browser never asks for a file that is not going to be there. In
the builder it resolves to esbuild's `sourcemap: 'external'`
(`src/tools/esbuild/application-code-bundle.js:445`) — the `.map` is still
written, only the reference is dropped.
`styles: false` because a CSS map has never resolved a stack trace — measured on
this repo, it is 53 files and 453 807 bytes of output nobody reads.
`sourcesContent: true` is what puts the *actual source lines* in the Sentry
frame rather than a filename and a column number, and it is also precisely why
the next subsection exists.

**Angular 22.1.3 already stamps ECMA-426 Debug IDs, and the gate is this exact
setting.**
`apps/web/node_modules/@angular/build/src/builders/application/execute-post-bundle.js:46`
runs `injectDebugIds(outputFiles)` when `sourcemapOptions.scripts` is true; the
injector (`src/builders/application/inject-debug-ids.js`) writes
`//# debugId=<uuid>` into each **browser** `.js` that has a `.map` sibling and a
top-level `"debugId"` into that map, derived as a UUIDv5 of the map bytes so a
rebuild of the same source yields the same id. `hidden: true` does not defeat it
— read `src/utils/debug-id.js`: with no `sourceMappingURL` line to sit above, the
comment is appended at the end of the file. Debug IDs are what Sentry matches on
now; Sentry's own troubleshooting page says "creating a release is no longer
required" and describes the release link as a *weak association*, so `--release`
and `--dist` at upload time are organisational, not the matching key.

Measured on this tree, 2026-09-08 — re-measure rather than trusting the numbers.
`npx ng build --source-map` produced 44 `.js.map` (10 655 235 B) and 53
`.css.map` (453 807 B), taking `dist/web/browser` from 3 522 260 B to
14 631 302 B, and `tail -c 300 main-*.js` carried
`//# debugId=1746ea45-310a-5e94-be44-82ad24a44f42`, matching `"debugId"` in the
map byte for byte. (`du -ch` reports those as 11M, 569K, 3.6M and 15M — block
allocation, not bytes; over 53 small `.css.map` files the 4 kB cluster inflates
the figure by a quarter. Quote one method or the other, not both.)

**Source maps cannot break the bundle budget, and the exclusion is structural
rather than a threshold.**
`apps/web/node_modules/@angular/build/src/tools/esbuild/budget-stats.js` opens
its loop with `if (!file.endsWith('.js') && !file.endsWith('.css')) continue;`
before it produces a stat, so a multi-megabyte `.map` contributes zero to the
250 kB warning / 400 kB error on `initial`. That is a vendored Angular file, not
a REEP one — re-read it after `npm ci`, not in `tools/`. The one cost you do pay
is in CI: `.github/workflows/ci.yml`'s `Web (Angular)` job runs the same
`npx ng build` (line 200) and will start emitting the same ~10.7 MB it then
throws away. That is the price of the production config being the config CI
actually proves.

**Do not turn on `subresourceIntegrity` while a post-build inject step exists.**
It is a real option on this builder (`schema.json`, `properties`), it is not set
today, and it must not be gained. Angular injects debug IDs *before*
`generateIndexHtml` computes SRI hashes, on purpose — the comment above
`injectDebugIds` says so in as many words. `sentry-cli sourcemaps inject` will
not fight that in the shape below, because it **skips** any file that already
carries a debug id (`src/utils/sourcemaps.rs` in 3.7.0 records it as
`previously_injected` and `continue`s; a second guard does the same on
`source_file.debug_id().is_some()`). The hazard is every file it does *not* skip
— one with no map sibling, or any future build where `sourceMap.scripts` is off
— because there it prepends a `_sentryDebugIds` snippet and rewrites the bytes.
Every SRI hash in `index.html` would then be wrong: a blank white app, on every
browser, with a console error about integrity.

### The maps must never reach CloudFront, and the obvious guard is the wrong one

`aws s3 sync . --delete --exclude "index.html"` publishes everything else, and
the SPA-fallback CloudFront function (`infra/cdk/reep_core/stack.py:151`,
`_SPA_FALLBACK_JS`) rewrites to `/index.html` **only when the last path segment
has no dot**. `main-XXXX.js.map` has a dot. It passes straight through to the S3
origin and is served — a public download of the entire SPA source, with
`sourcesContent` inlined, on a dashboard whose screens are a student's marks,
attendance and USN.

The step you would reach for first is this, and it has a trap in it:

```bash
# DO NOT do it this way.
aws s3 sync . "s3://$WEB_BUCKET" --delete --exclude "index.html" --exclude "*.map"
```

AWS documents the behaviour on the `--delete` option itself: "Files that exist in
the destination but not in the source are deleted during sync. **Note that files
excluded by filters are excluded from deletion.**"
([s3 sync reference](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html))
So `--delete` will never remove a `.map` that is already in the bucket. One
accidental publish becomes permanent, and the guard you added is the reason the
cleanup cannot run. Delete the maps from `dist` after the upload instead, and
keep `.map` out of the filter so `--delete` sweeps any historical one out on the
next deploy. (`--exclude "index.html"` stays — that one is protecting the
`no-cache` copy the second command uploads, and is meant to survive `--delete`.)

**The delete runs unconditionally — it fails closed.** If it sat inside the
`SENTRY_AUTH_TOKEN` branch, a missing secret would skip the upload *and* publish
~10.7 MB of source. Not uploading is an inconvenience; publishing is the incident.

### The `web` job: build, inject, upload, delete, publish

Three real steps of context; the additions are the `+` lines.

```diff
       - name: Point the SPA at Sentry
         env:
           WEB_SENTRY_DSN: ${{ secrets.WEB_SENTRY_DSN }}
         working-directory: apps/web
         run: |
           if [ -z "$WEB_SENTRY_DSN" ]; then
             echo "no WEB_SENTRY_DSN secret; shipping without client telemetry"
             exit 0
           fi
           sed -i "s|sentryDsn: ''|sentryDsn: '$WEB_SENTRY_DSN'|" \
             src/environments/environment.ts
           grep -q "sentryDsn: 'https" src/environments/environment.ts
+          # The release and the environment travel in the same file, by the
+          # same sed, guarded by the same grep — and behind the same `exit 0`
+          # above, which is right: with no DSN main.ts never imports the SDK,
+          # so there is nothing for a release to label.
+          # `prod` is not a typo: it is stack.py:283's api_environment["ENV"],
+          # so the two halves of one distributed trace land in ONE Sentry
+          # environment.
+          sed -i "s|sentryRelease: ''|sentryRelease: '${{ github.sha }}'|" \
+            src/environments/environment.ts
+          sed -i "s|sentryEnvironment: 'development'|sentryEnvironment: 'prod'|" \
+            src/environments/environment.ts
+          grep -q "sentryRelease: '${{ github.sha }}'" src/environments/environment.ts
+          grep -q "sentryEnvironment: 'prod'" src/environments/environment.ts

       - name: Build (the bundle budget is enforced here too)
         working-directory: apps/web
         run: npx ng build

+      # Debug IDs are the matching key, so this must run AFTER the bundler and
+      # BEFORE anything is published: the file that reaches CloudFront has to
+      # be the file whose id was uploaded. Angular already stamped ids (see
+      # the section above) and `inject` skips files that carry one, so here it
+      # is a no-op that costs a second and covers the case where it is not.
+      - name: Upload source maps to Sentry
+        env:
+          SENTRY_AUTH_TOKEN: ${{ secrets.SENTRY_AUTH_TOKEN }}
+          SENTRY_ORG: ${{ vars.SENTRY_ORG }}
+          SENTRY_PROJECT: ${{ vars.SENTRY_PROJECT_WEB }}
+        working-directory: apps/web
+        run: |
+          if [ -z "$SENTRY_AUTH_TOKEN" ]; then
+            echo "no SENTRY_AUTH_TOKEN; browser stack traces will stay minified"
+          else
+            CLI="npx --yes @sentry/cli@3.7.0"
+            $CLI sourcemaps inject dist/web/browser
+            # --strict is not advisory: upload_strict turns a zero-file upload
+            # into `Err("No files to upload (strict mode).")`, so a revert of
+            # angular.json's sourceMap block breaks here rather than shipping
+            # an empty artifact bundle nobody notices for a month.
+            $CLI sourcemaps upload --release "${{ github.sha }}" --strict \
+              dist/web/browser
+          fi
+          # UNCONDITIONAL, and outside the branch on purpose: a missing secret
+          # must not publish ~10.7 MB of application source to CloudFront.
+          find dist/web/browser -name '*.map' -delete

       - uses: aws-actions/configure-aws-credentials@v4
```

The `Publish` step is **unchanged**. No `--exclude "*.map"` — see above.

`--release` and `--strict` are both real on 3.7.0: `--strict`/`-s` is declared on
`sourcemaps upload`, and `--release`/`-r` is a `.global(true)` argument from
`ArgExt::release_arg`, so it is accepted on the subcommand.

The SPA has to be able to report the release, which is two lines of client code:

```diff
   sentryDsn: '',
+  /// The commit this bundle was built from, and the Sentry environment name.
+  /// Both are rewritten by deploy.yml's "Point the SPA at Sentry" step.
+  /// `sentryEnvironment` must equal the API's ENV or one trace splits in two.
+  sentryRelease: '',
+  sentryEnvironment: 'development',
 };
```

```diff
         sentry.init({
                 dsn: environment.sentryDsn,
-                environment: environment.production ? 'production' : 'development',
+                environment: environment.sentryEnvironment,
+                release: environment.sentryRelease || undefined,
                 integrations: [sentry.browserTracingIntegration()],
```

**That `environment.production` line is a pre-existing bug and the release work
is what exposes it.** `apps/web/src/environments/environment.ts` hard-codes
`production: false`, `angular.json` has no `fileReplacements`, and there is no
second environment file — so every production browser event is currently filed
under `development`, while the api reports `prod`
(`app/main.py:64`, `environment=settings.env.strip() or "development"`, against
`stack.py:283`'s `"ENV": "prod"`). Any alert rule, issue-owner rule or
environment filter keyed on the live environment silently misses the entire web
project. Reading the name out of the same file the DSN comes from removes the
flag rather than fixing it, which is why the diff deletes it: one string, written
once, by the step that already writes to that file. **Delete the field in the
same commit** — `grep -rn 'environment.production' apps/web/src` returns exactly
`main.ts:20`, so after this diff `production: false` is a hard-coded lie with no
readers, which is the same trap in a new place.

### The `release` job: nothing runs after both halves today

This is a structural addition, not a step. `api` and `web` are parallel siblings
that both hang off `guard`, and **no job runs after both** — so there is no
existing home for a call that must happen once per deploy, after the code is
actually live. Either can also be skipped, by `target: api-only` / `web-only`.

```diff
       - name: Invalidate the entry point
         run: |
           aws cloudfront create-invalidation \
             --distribution-id "${{ vars.CLOUDFRONT_ID || 'EW482RJIK9CVE' }}" \
             --paths "/" "/index.html" >/dev/null
           echo "SPA deployed: ${{ github.sha }}"
+
+  release:
+    name: Sentry release
+    needs: [guard, api, web]
+    # THREE clauses, and each one is a different failure.
+    #  * `needs` on a SKIPPED job skips this one too, and api-only / web-only
+    #    deploys skip a sibling by design — hence `!cancelled()` and a gate on
+    #    failure rather than on completion.
+    #  * But `skipped` is NOT `failure`, and a failed `guard` skips BOTH
+    #    siblings: without the guard clause this job would cheerfully record a
+    #    release, a commit association and `Deploys: 1` for a run that typed
+    #    the confirmation word wrong and shipped nothing.
+    #  * And at least one half must actually have succeeded, or the same thing
+    #    happens by another route.
+    if: >-
+      ${{ !cancelled()
+          && needs.guard.result == 'success'
+          && needs.api.result != 'failure'
+          && needs.web.result != 'failure'
+          && (needs.api.result == 'success' || needs.web.result == 'success') }}
+    runs-on: ubuntu-latest
+    steps:
+      - uses: actions/checkout@v4
+        with:
+          # `set-commits --auto` diffs against history. The default depth is 1,
+          # which is one commit and nothing to diff — the association silently
+          # comes back empty and Suspect Commits stays blank forever.
+          fetch-depth: 0
+
+      - name: Create, associate, finalize, mark deployed
+        env:
+          SENTRY_AUTH_TOKEN: ${{ secrets.SENTRY_AUTH_TOKEN }}
+          SENTRY_ORG: ${{ vars.SENTRY_ORG }}
+        run: |
+          if [ -z "$SENTRY_AUTH_TOKEN" ]; then
+            echo "no SENTRY_AUTH_TOKEN; no release was recorded"
+            exit 0
+          fi
+          CLI="npx --yes @sentry/cli@3.7.0"
+          SHA="${{ github.sha }}"
+          # One release, both projects: an interview that fails half in the
+          # browser and half in the api is one deploy, not two. Repeated -p is
+          # supported — the `releases` group declares its project argument with
+          # ArgAction::Append (src/utils/args.rs, project_arg(true)).
+          $CLI releases new -p "${{ vars.SENTRY_PROJECT_API }}" \
+                            -p "${{ vars.SENTRY_PROJECT_WEB }}" "$SHA"
+          # --ignore-missing survives the first release and a rebased history,
+          # where a referenced commit is not in the tree and set-commits
+          # otherwise fails the whole job over bookkeeping.
+          $CLI releases set-commits "$SHA" --auto --ignore-missing
+          # Finalize AFTER both halves are live. Finalizing at build time marks
+          # a release shipped that a circuit-breaker rollback then un-shipped,
+          # and this timestamp is what orders SHA releases.
+          $CLI releases finalize "$SHA"
+          $CLI deploys new --release "$SHA" -e prod
```

Note the ordering the two jobs produce: `sourcemaps upload --release` in `web`
runs first, so by the time `releases new` runs the release name may already
exist from that weak association — with only the web project attached. `releases
new -p api -p web` is what puts both on it, which is exactly what checklist item
6 below reads back. **Watch the first run's log for this step**: whether Sentry
answers a POST for an existing release with a 2xx or an error is the one thing
here that is easier to observe once than to argue about.

The optional-secret shape is copied from `Point the SPA at Sentry`, including
its reason: `secrets` is not a value the workflow parser admits inside an `if:`,
and using one there fails the whole file to parse and rejects every dispatch
before any job starts. Route it through `env:` and test the variable.

`sentry-cli` 3.x is a deliberate pin, and **3.7.0 is current** — released
2026-08-28, still `latest` on npm and on
[getsentry/sentry-cli](https://github.com/getsentry/sentry-cli/releases) as of
2026-09-08. The `sentry-cli releases files …` and `sentry-cli files …`
subcommands, and `sourcemaps explain`, were **removed** in 3.0.0, and API-key
authentication went with them — auth tokens only. Anything you find online using
`releases files upload-sourcemaps` is not legacy, it does not run.

### The api's release is a build arg, and the CDK does not change

**`SENTRY_RELEASE` cannot come from CDK, and that is the correct answer rather
than a limitation.** `infra/cdk/reep_core/stack.py`'s `api_environment` (line
282) is a dict fixed at synth time, so the only value it could carry is a
constant — useless for a per-deploy sha. And `deploy.yml` deploys code, never
infrastructure, while `cdk-deploy.yml`'s stack menu deliberately omits `core`
(`options: [voice-platform, edge-waf, dr-vault]`, with the reason in the comment
above it) until step 9b of `docs/cdk-cutover.md` has run, because a bare
`cdk deploy reep-core` is the full harden — a Multi-AZ database conversion and an
ECS roll in one update. Wiring a release through CDK would mean a CloudFormation
deploy per code deploy, from the one workflow that must never do one.

Nothing in the CDK changes. `SENTRY_DSN` keeps arriving as an ECS secret from the
operator-owned `reep/external` Secrets Manager secret, on every api family and
the retention schedule alike (`stack.py:899`, `_api_task_def` — one helper builds
all three families, and the nightly retention job runs the `reep-api` family it
produces). The release rides in the image:

```diff
 ENV FORWARDED_ALLOW_IPS="10.42.0.0/16"
+
+# The commit this image was built from, and nothing else. Declared here, after
+# every COPY and RUN, so a changed value invalidates only these two trivial
+# layers and never the pip install. Blank is a supported value: an empty
+# SENTRY_RELEASE is falsy, the SDK finds no release, and behaviour is exactly
+# what it is today.
+ARG GIT_SHA=""
+ENV SENTRY_RELEASE=$GIT_SHA
 CMD ["python", "-m", "uvicorn", "app.main:app", \
```

```diff
       - name: Build and push the image
         run: |
           IMAGE="${{ vars.ECR_REPOSITORY || '445363794125.dkr.ecr.ap-south-1.amazonaws.com/reep/api' }}"
-          docker build -t "$IMAGE:${{ github.sha }}" -t "$IMAGE:latest" apps/api-py
+          docker build --build-arg GIT_SHA="${{ github.sha }}" \
+            -t "$IMAGE:${{ github.sha }}" -t "$IMAGE:latest" apps/api-py
           docker push "$IMAGE:${{ github.sha }}"
           docker push "$IMAGE:latest"
```

The task definition sets `environment=dict(api_environment)` and `SENTRY_RELEASE`
is not one of its keys, so the image's `ENV` survives rather than being
overridden.

**No Python change is needed, and the mechanism is named rather than assumed.**
`sentry_sdk.init` in `apps/api-py/app/main.py:62` passes no `release`;
`sentry_sdk/client.py:326` fills it — `if rv["release"] is None: rv["release"] =
get_default_release()` — and `get_default_release()` reads `SENTRY_RELEASE` from
the environment first (`sentry_sdk/utils.py:150`, pinned 2.68.1). That is an SDK
default this deployment now depends on — worth one line in the init comment,
because "the release appears by itself" is the kind of fact that survives exactly
until someone upgrades the SDK.

One thing this shape costs you: the release is baked into the **image**, not into
the task definition, so `aws ecs describe-task-definition` will never show it.
The way to check which release is running is a Sentry event, not the ECS console.

### The secrets and variables to create

The house split holds: credentials are repository **secrets**, identifiers are
repository **variables**. The existing variables all carry a hard-coded fallback;
these three cannot, because this repository has never recorded a Sentry org or
project slug and inventing one would upload a student dashboard's source into a
stranger's organisation.

| name | kind | what breaks if it is missing |
| --- | --- | --- |
| `SENTRY_AUTH_TOKEN` | secret | Both new steps print a note and exit 0. Browser traces stay minified and no release, deploy marker or commit association is recorded — silently, on a green run. Create it as an **Organization Auth Token**: sentry.io → **Settings → Developer Settings → Organization Tokens**. Its permissions are fixed to the CI set and are not editable, the value is shown **once**, and every org owner is emailed when one is created. |
| `SENTRY_ORG` | variable | `sentry-cli` cannot resolve the organisation and every command in both steps fails; the `web` job fails at `sourcemaps upload`, after the build and before `Publish`, so nothing ships. |
| `SENTRY_PROJECT_WEB` | variable | The artifact bundle lands nowhere resolvable and browser frames stay minified even though the upload appeared to succeed. |
| `SENTRY_PROJECT_API` | variable | The release exists but the api project is not associated with it, so "first seen in release" is empty on the half that raises Python tracebacks — the half `docs/deployment-process.md` §8.5's abort criterion is about. |

**Do not use a personal token.** Sentry's own warning is the operational one: if
the person who created it is removed from the organisation, the token stops
working — and what it stops is the deploy job, at 9 a.m., for a reason nobody will
guess. An Organization Token carries the fixed **`org:ci`** scope, which covers
source-map upload, release creation and code mappings
([permissions & scopes](https://docs.sentry.io/api/permissions/)). If you build
an internal integration instead — the route Sentry names when you need broader
API access than an org token grants — you pick permission *categories*, not raw
scopes: **Releases: Admin** (which is `project:releases`, and Sentry documents it
as reaching both the project *and* the organization release endpoints) plus
**Organization: Read**.

### Suspect commits needs four things and two of them are console work

Commit association alone is not enough. Sentry
[documents](https://docs.sentry.io/product/issues/suspect-commits/) four
requirements, and `set-commits` is only the first.

1. **Commit data on the release.** That is `releases set-commits --auto` above,
   and it is why `fetch-depth: 0` is not optional.
2. **The GitHub integration, installed and pointed at this repository.**
   sentry.io → **Settings → Integrations → GitHub → Install**, then
   **Configurations → Configure** and add the repository. Without it Sentry falls
   back to the release's commit list and can suggest a commit, but it cannot read
   blame for the exact file and line, and it cannot suggest an assignee. Confirm
   the repository is listed before moving on.
3. **Code mappings, which this repo needs in both projects.** Sentry
   auto-creates mappings for JavaScript and Python projects once the GitHub
   integration is installed; whether the mapping it guesses is right **here** is
   unverified — check it at **Settings → Integrations → GitHub → Configurations
   → Configure → Code Mappings**. What is measured is that neither project's
   runtime path equals its repository path, so a mapping is required either way:
   - **web** — the maps' `sources` entries are `../src/app/…` relative to
     `dist/web/browser` (measured 2026-09-08; there is no `sourceRoot`). The
     source-code root is `apps/web/src/`; read the stack-trace root off the
     first real event rather than assuming, because `sourcemaps upload` rewrites
     source paths by default.
   - **api** — the Dockerfile is `WORKDIR /app` + `COPY app ./app`, so a frame's
     filename is `app/routers/…`, against `apps/api-py/app/routers/…` in the
     repository.

   A wrong mapping does not error — it just produces an issue with no suspect
   commit and no explanation.
4. **The issue must have been created after the integration was set up.** Every
   issue already in the stream stays blank no matter how correct steps 1–3 are,
   which is why step 7 below is run on an issue first seen on this release.

Suspect-commit authors are what feed suggested assignees, and — only if you
enable it in the project's ownership rules, where the choice is "auto-assign to
suspect commits" / "auto-assign to issue owner" / off — auto-assignment. Note the
standing gap that makes that worth thinking about: `.github/CODEOWNERS` exists
but names one account throughout and has no ruleset enforcing it, and **no path
rule beyond the catch-all `*`** matches `app/main.py`, `app/tracing.py` or
`app/traceability.py` — the three files where a one-line edit puts a student's
transcript on a third party's servers. That is the file's own second caveat,
applied to these three paths.

### After one deploy, prove it — do not assume it

Run this list once, on the first deploy that carries the changes above. Every
item ends in something you can read off a screen or a terminal.

0. **The api's DSN is not blank.** The `aws secretsmanager get-secret-value`
   command at the top of this section prints `True`. If it prints `False`,
   everything below can pass on the web half and the api project will still be
   empty — and §8.5's abort criterion is about the api half.
1. **The workflow ran the new steps.** `Upload source maps to Sentry` and the
   `Sentry release` job both show green, and neither printed its
   `no SENTRY_AUTH_TOKEN` line. A skipped step here looks identical to success
   in the run list.
2. **The deployed JavaScript carries a debug id.** Take the `main-*.js` filename
   out of the live `index.html` and
   `curl -s https://<domain>/<main-XXXX.js> | tail -c 300 | grep -o '//# debugId=[0-9a-f-]*'`.
   No comment means no matching, whatever the release says.
3. **The bundle arrived.** Sentry → **Project Settings → <web project> →
   Source Maps → Artifact Bundles**, and the id from step 2 is in it.
4. **No map is public, and none is in the bucket.**
   `curl -s -o /dev/null -w '%{http_code}\n' https://<domain>/<main-XXXX.js>.map`
   **must not be 200** — the exact code an S3-via-OAC origin returns for a
   missing key is unverified here, which is why this reads "not 200" rather than
   naming 403 — and
   `aws s3 ls "s3://${WEB_BUCKET:-reep-web-20260827213147410900000003}" --recursive | grep -c '\.map$'`
   must print `0`. Run the second one even if the first looks right: it is the
   one that catches a map left behind by an earlier deploy.
5. **A browser frame resolves.** Trigger a deliberate client error on a real
   screen and open the event. The top frame must name a real `.ts` file with a
   line number and show the surrounding source — not `main-XXXX.js:1:127433`.
   Do **not** expect the path to read `apps/web/src/…`: the maps carry
   `../src/app/…`, so whatever Sentry renders here is the *stack-trace root* you
   type into the code mapping in step 3 of the previous subsection.
6. **The release is one release, over two projects.** Sentry → **Releases** →
   the sha. It carries both projects, a finalize timestamp, a non-zero commit
   count, and **Deploys: 1** in `prod`.
7. **Suspect commits resolve.** Open an issue **first seen on this release** and
   confirm the **Suspect Commits** panel names a commit and an author. Empty here
   after steps 1–6 pass means the code mapping, not the release.
8. **Both halves are in one environment.** Filter the issue stream on
   `environment:prod` and confirm api *and* web events appear. If only the api
   does, the `sentryEnvironment` sed did not land — which is exactly the failure
   the `grep -q` in that step exists to prevent, so check the step's log too.
9. **The api actually reports it.** Any api event's `release` equals the sha.
   It will not appear in `describe-task-definition`; the release lives in the
   image, and a Sentry event is the only place it is visible.
---

## Rule 1 applied to telemetry

**Sentry is a second HTTP transport out of this process, carrying the same
student text as the first, and `student_data_egress_allowed` does not guard
it.** That gate (`apps/api-py/app/ai/llm.py`) is reached only from
`complete_chat` and `stream_chat`. The SDK sends on its own schedule, from
frames the author never chose, with no `carries_student_data=` keyword to
declare. `apps/api-py/app/main.py` and `apps/api-py/app/tracing.py` already say
so in prose, and
`tests/test_codebase_guards.py::test_sentry_never_ships_local_variables_or_request_bodies`
already makes two of the three constructor flags executable. This section owns
the rest: which carriers are still uncovered, what closes them, and what a test
can hold onto afterwards.

Status markers are the ones defined in `docs/deployment-process.md` §Status
markers, with two stretches named rather than hidden: that document writes
**[ADMIN — NOT YET APPLIED]** as *a GitHub setting only `darshani8` can apply by
hand*, and the Sentry-console rows below use it in the wider sense — a setting
no committed file can turn on. **[IN FORCE] by absence** is a compound that
document does not define; it means *true today only because no code does the
thing*, which is a weaker guarantee than a mechanism and is marked as such.

Every SDK claim below was read out of the pinned trees, not remembered:
`apps/api-py/.venv/Lib/site-packages/sentry_sdk` (2.68.1) and
`apps/web/node_modules/@sentry/browser` (10.71.0).

### The leak table

Every carrier below is a real variable or field in this repo. "Ships as" is the
Sentry mechanism that would carry it — not a category, the actual one.

| carrier | ships as | control | on today |
| --- | --- | --- | --- |
| `text` in `_run_turn_write` (`app/interview_nova.py:1919`), `raw` / `self._report_raw` (the scorecard, `interview_nova.py:1658-1659`), `content` in `conversations.append_message` (`app/conversations.py:131`), `markdown` / `prompt` in `generate_resume` (`app/routers/student.py:1261,1276`), `payload` and `headers` (`Bearer <key>`) in `app/ai/llm.py:246-254` | **frame locals** on every captured exception — and `log.exception` on these paths *is* a captured event, because `LoggingIntegration` is in `_DEFAULT_INTEGRATIONS` (`sentry_sdk/integrations/__init__.py:61`) with `DEFAULT_EVENT_LEVEL = ERROR` (`integrations/logging.py:27`) and `app/main.py` passes no `integrations=` | `include_local_variables=False` | **[IN FORCE]**, pinned textually at `tests/test_codebase_guards.py:600` |
| `ResumeProfileIn.data` (`app/routers/student.py:1926`), `ProfileUpdateIn` (`:1080`), `LinkPasswordIn.token` (`app/routers/passwords.py:97-98` — the ONE body model both `/auth/activate` and `/auth/reset` take), `LedgerSaveIn.cells` (`app/routers/student_programme.py:114-122`) | **request body** on the event's `request.data` | `max_request_body_size="never"` | **[IN FORCE]**, same test. Survives even if `data_collection` is later enabled — `starlette.py:511,766` call `request_body_within_bounds` unconditionally, and that helper reads `max_request_body_size` alone (`_wsgi_common.py:55-65`) |
| the `reep_session` cookie, the `authorization` header | **request headers / cookies** | `send_default_pii=False`, which is what makes `_filter_headers` substitute every member of the static `SENSITIVE_HEADERS` tuple — `COOKIE`, `SET_COOKIE`, `AUTHORIZATION`, `PROXY_AUTHORIZATION`, `X_API_KEY`, `X_FORWARDED_FOR`, `X_REAL_IP` (`_wsgi_common.py:25-40,244-259`). The same flag is why Starlette never extracts `request.cookies` at all | **[IN FORCE]** but **not pinned** — the guard asserts the other two flags only |
| `token` on `GET /api/register/verify?token=<raw>` (`app/routers/registration.py:565`, a bare `str` param, so FastAPI binds it from the query string) | **`request.query_string`, verbatim** | none | **NO.** See below |
| `code` and `state` on `GET /api/auth/sso/google/callback` (`app/routers/auth.py:739-740`, both `Query(None)`) — a Google **authorization code** and the single-use CSRF state | **`request.query_string`, verbatim** | none | **NO.** And neither word is in the SDK's `_SENSITIVE_DENYLIST` (`sentry_sdk/data_collection.py:62-79`), so the `data_collection` experiment rejected below would not have caught these either |
| `token` on `/activate?token=` and `/reset?token=` (built by `_link` at `app/account_links.py:109-110`; Angular routes at `apps/web/src/app/app.routes.ts:39,47`) | **`event.request.url`** — the browser SDK's `httpContextIntegration` sets it from `getLocationHref()` on *every* event (`@sentry/browser .../integrations/httpcontext.js`, `helpers.js:86-99`), and writes the same value as the `url.full` span attribute; plus the **navigation breadcrumb's `from`/`to`**, which are `parseUrl(href).relative` = path **+ query + fragment** (`integrations/breadcrumbs.js:215-240`, `@sentry/core .../utils/url.js:96-108`) | none | **NO.** No `beforeSend` exists in `apps/web/src/main.ts` |
| `text` at `app/mail_transport.py:84` — the whole outbound mail body: the raw activation link, the raw reset link, or the six-digit code plus the recipient's name | **breadcrumb.** `LoggingIntegration`'s `DEFAULT_LEVEL` is `INFO` (`integrations/logging.py:26`), and `_breadcrumb_from_record` stores `record.name` as `category` and `record.message` as the message — the *formatted* string, arguments interpolated (`:371-379`) | none | **NO.** Fires whenever `SES_FROM_ADDRESS` is blank, which no boot guard requires |
| `user.email` at `app/routers/passwords.py:140, 221, 275` (INFO) and the normalised `email` local at `app/routers/auth.py:484, 549` | **breadcrumb**, same mechanism | none | **NO.** And on this deployment the email *is* the USN (`1mp25mdm01@bgscet.ac.in`), so "password reset for …" is a student identifier in telemetry |
| `identity.email`, `user.google_sub`, `identity.sub` at `app/routers/auth.py:864-872` | **event**, not a breadcrumb — that call is `log.error`, at `DEFAULT_EVENT_LEVEL`. It lands in `event.logentry.formatted` **and**, uninterpolated, in `event.logentry.params` (`integrations/logging.py:326-330`) | none | **NO.** The `params` half is the one a `formatted`-only scrubber misses |
| `q` on `GET /api/agent/knowledge/search?q=<free text>` (`app/routers/agent.py:635`) and on `GET /api/platform/admin/candidates?q=` (`app/voice_platform/api/admin.py:627`) | **`request.query_string`** | none | **NO** — but latent: `grep -rn "knowledge/search\|platform/admin" apps/web/src` returns nothing, so no client sends a student's typed words there *today* |
| span data on the four traced paths: `provider`, `model`, `messages` (a count), `carries_student_data`, `texts` (a count), `region`, `key` | **span data / transaction tags** | discipline at the call sites, pinned behaviourally by `tests/test_tracing.py::test_a_captured_trace_carries_shape_and_never_content` | **[IN FORCE]** |
| interview WAVs (`app/interview_audio.py`), generated resume PDFs | **attachments** | `grep -rn "add_attachment" --include=*.py --include=*.ts apps/ infra/ tools/` returns nothing outside `apps/web/dist/` and `apps/web/.angular/cache/` (bundled SDK internals), and it must stay that way: Sentry scrubs an attachment only when an advanced rule names it by a **non-wildcard filename** ([attachment-scrubbing](https://docs.sentry.io/security-legal-pii/scrubbing/attachment-scrubbing/): *"Only attachments that are explicitly selected by an Advanced Data Scrubbing rule with a non-wildcard filename will actually be scrubbed."*) | **[IN FORCE] by absence** |
| the DOM of `/student/assistant`, `/student/resume`, any mentor screen | **Session Replay** | `replayIntegration()` is not in `apps/web/src/main.ts`'s `integrations` array and `@sentry/replay@10.71.0` — present in `node_modules` as a transitive dependency — is never constructed | **[IN FORCE] by absence.** Note that the user's `integrations:` array MERGES with `getDefaultIntegrations()` (`@sentry/browser .../sdk.js:16-35`) rather than replacing it — which is why `httpContextIntegration` above is live, and why replay stays off only because nothing constructs it. Replay masks all text and blocks all media by default but *never* masks the URL — turning it on ships the `?token=` above again, through a third door |

**Three rows in that table are not "PII in a log". They are working
credentials**, and they are the reason this section leads.

### The `?token=` rows: what actually happens

**A single-use account token in a query string reaches Sentry raw, and
`send_default_pii=False` does not stop it.** In `sentry-sdk 2.68.1` the ASGI
integration filters the query string only when `_experiments["data_collection"]`
is set — `has_data_collection_enabled` (`sentry_sdk/utils.py:2073-2077`) is
literally `"data_collection" in options.get("_experiments", {})`. `app/main.py`
sets no `_experiments`, so `_asgi_common.py:140-141` takes the else-branch:
`request_data["query_string"] = _get_query(asgi_scope)`, URL-decoded, whole. It
is attached by an `event_processor` (`integrations/asgi.py:232,418-425`), and
`Scope.apply_to_event` runs event processors for transactions too
(`scope.py:1910`, outside the `if not is_transaction` guard) — so it rides error
events **and** the `traces_sample_rate`-sampled transactions. A token does not
need an exception to leak, it needs a dice roll on a successful 302. The same
`type in ("http", "websocket")` branch covers the interview socket, so a
WebSocket's query string travels by the identical route.

**The transaction *name* is clean, and that is exactly why this is easy to
miss.** `FastApiIntegration` overrides only `setup_once` and inherits
`StarletteIntegration.__init__`, whose default `transaction_style` is **`"url"`**
— not `endpoint` (`integrations/starlette.py:115`, `integrations/fastapi.py:43-48`).
So the transaction is named from the route *pattern*: `/api/register/verify`.
The browser SDK names its pageload span `WINDOW.location.pathname`
(`.../tracing/browserTracingIntegration.js:278`) — `/reset`. Neither style
embeds a query string in the name.

Nor do the span attributes, on the API side: `_get_request_attributes` sets
`http.query` and `url.full` only under `should_send_default_pii()` or
`data_collection` (`_asgi_common.py:182-231`), and neither is on. There is
therefore **exactly one place** it lands on the API — `event.request.query_string`
— and two on the browser — `event.request.url` and the navigation breadcrumb.
Anyone auditing by reading transaction names, or by reading spans, concludes the
tokens are not travelling. They are one panel over.

What it costs when it lands: the reset link lives
`password_reset_minutes = 60`, activation `activation_link_hours = 168` (seven
days), email verification `email_verification_hours = 24` (all in
`app/config.py:136-143`) — and any project member with read access can open a
Sentry issue and click first. Consuming a reset link takes over a staff account
and `note_revocation` signs every real device out; consuming
`/api/register/verify` confirms an address and can provision an account through
the auto-approve path; a Google `code` is single-use and short-lived but is a
live credential for as long as it is not spent. The genuine recipient's only
symptom is a link that says it has expired. The event itself then sits in Sentry
for the plan's retention — 30 days on Developer, 90 on Team and above, **stamped
at ingest**, so a plan change moves only new data
([data-retention-periods](https://docs.sentry.io/security-legal-pii/security/data-retention-periods/)).
Sentry publishes no self-serve control over that number, and **no org or plan is
committed anywhere in this tree**, so which number applies here is *unverified*
until someone reads it off the billing page.

**Do not reach for `_experiments={"data_collection": {...}}` as the fix,
however tempting.** It exists in the pinned SDK and its `_SENSITIVE_DENYLIST`
does contain `"token"`, so it would filter *that* query parameter — though not
`code` or `state`, which are not in the list. It would also make
`send_default_pii=False` *ignored* (a `DeprecationWarning`, and
`data_collection` wins — `data_collection.py:14-23`), and `_resolve_explicit`
(`:191-236`) fills every omitted field with the permissive spec default:
`user_info: True`, `cookies` and `http_headers` in `denylist` mode rather than
off, `gen_ai.inputs/outputs: True`, and `database_query_data: True` — which, via
`record_sql_queries` (`sentry_sdk/tracing_utils.py:137-177`), starts attaching
**SQLAlchemy bound parameters** as `db.params` on spans, through the
auto-enabled `SqlalchemyIntegration` (`integrations/__init__.py:104`). On this
codebase that is a student's email in the `WHERE` clause of the login lookup. An
experimental option whose defaults are more permissive than the option it
replaces is not a scrubber. Write the scrubber.

### The scrubber to add

**[NOT WRITTEN].** Put the functions in a new `apps/api-py/app/telemetry_scrub.py`
and leave `sentry_sdk.init` where it is — the init is at import time, before the
app object, on purpose, and the existing guard reads `app/main.py` as text.
Pure functions, no `sentry_sdk` import, so they are unit-testable with no DSN
and no database.

```python
# apps/api-py/app/telemetry_scrub.py
"""Last-line PII scrubbing for Sentry events, breadcrumbs and transactions.

Rule 1 applied to telemetry. The three constructor flags in app/main.py cover
frame locals, request bodies and cookies; NOTHING covers the query string, and
nothing covers a log record whose formatted message IS the secret. Those are
carried by mechanisms the SDK enables by default, so they must be removed by a
hook rather than by an option that a future SDK release can rename underneath us.

No sentry_sdk import: these are plain dict transforms, so the tests run with no
DSN, no network and no database.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .redaction import redact_pii

_FILTERED = "[Filtered]"  # the SDK's own SENSITIVE_DATA_SUBSTITUTE, spelled the same

#: An ALLOWLIST, not a denylist, because a denylist has to be right about the
#: parameter the NEXT feature adds. These are the ones this repo actually sends,
#: enumerated — not guessed — and each is an enumeration or a small integer, never
#: free text. Regenerate with:
#:     grep -rnoE "[?&][a-zA-Z_]+=" apps/web/src
#:     grep -rn "Query(" apps/api-py/app/routers apps/api-py/app/voice_platform
#:     grep -rn "query_params.get" apps/api-py/app
#:
#: DELIBERATELY ABSENT, each for its own reason:
#:   token, code, state — credentials (registration.py:565, auth.py:739-740).
#:   q               — free text a person typed (agent.py:635, platform admin).
#:   next            — auth.guard.ts:17 sets it to `state.url`, the WHOLE url of
#:                     the route that bounced, query string included. /activate
#:                     and /reset are unguarded today so no token can reach it,
#:                     but that is a fact about the route table, not a property.
_QUERY_ALLOW = frozenset(
    {
        "board",             # /student/leaderboards?board=
        "day",               # /student/ledger?day=
        "days",              # /student/timesheet?days=, mentee_records.py:91
        "degree",            # /ws/media-bridge?degree=UG
        "download",          # /interviews/{id}/audio?download=1
        "error",             # /login?error=<sso code>
        "include_inactive",
        "limit",
        "mode",
        "specialization",
        "status",
        "track",             # ?track=mixed|student|assistant
        "verified",          # /login?verified=1|0
        "why",               # /login?verified=0&why=expired_or_used
    }
)

#: Loggers whose records ARE the sensitive thing rather than a description of
#: it. app/mail_transport.py:84 logs the complete outbound message body — the
#: raw activation link, the raw reset link, the one-time code — and that record
#: becomes a breadcrumb on the next captured event.
#:
#: The cost is honest and temporary: the SES-failure line uses the same logger,
#: so muting the module also mutes the breadcrumb an operator wants when a
#: student says the link never arrived. The real fix is upstream (below); this
#: is the layer that holds until that lands.
_MUTED_LOGGERS = frozenset({"app.mail_transport"})


def _filter_query(qs: str | None) -> str | None:
    if not qs:
        return qs
    return urlencode(
        [
            (k, v if k in _QUERY_ALLOW else _FILTERED)
            for k, v in parse_qsl(qs, keep_blank_values=True)
        ]
    )


def _filter_url(url: str | None) -> str | None:
    """The browser SDK sets request.url from location.href, so the SPA's
    /reset?token=... arrives here whole. The API's own _get_url deliberately
    excludes the query string (_asgi_common.py:56-78), so this is a no-op there;
    filtering both costs one branch and removes the distinction.

    The fragment goes too. Angular routes by path here, so nothing is lost —
    and a fragment is one more place a link could carry a secret."""
    if not url or "?" not in url:
        return url
    parts = urlsplit(url)
    return urlunsplit(parts._replace(query=_filter_query(parts.query) or "", fragment=""))


def _redact_params(params: Any) -> Any:
    """`record.args`, verbatim: a tuple for %s-style, a Mapping for %(name)s."""
    if isinstance(params, dict):
        return {k: redact_pii(v) if isinstance(v, str) else v for k, v in params.items()}
    if isinstance(params, (list, tuple)):
        return [redact_pii(v) if isinstance(v, str) else v for v in params]
    return params


def scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """before_send AND before_send_transaction. Both, because request data is
    attached by a shared event_processor and a sampled 200 carries it too."""
    request = event.get("request")
    if isinstance(request, dict):
        if "query_string" in request:
            request["query_string"] = _filter_query(request["query_string"])
        if "url" in request:
            request["url"] = _filter_url(request["url"])
        # max_request_body_size="never" already removes this. Popped anyway:
        # this hook is the layer that still holds if that flag is ever dropped.
        request.pop("data", None)
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        # THREE fields, not one. EventHandler._emit writes
        #   {"message": <raw template>, "formatted": record.getMessage(),
        #    "params": record.args}
        # (sentry_sdk/integrations/logging.py:326-330). Scrubbing `formatted`
        # alone leaves the value sitting one key over, uninterpolated —
        # app/routers/auth.py:864 is a log.error whose args are identity.email,
        # user.google_sub and identity.sub, and ERROR is a captured EVENT.
        for key in ("formatted", "message"):
            if isinstance(logentry.get(key), str):
                logentry[key] = redact_pii(logentry[key])
        if "params" in logentry:
            logentry["params"] = _redact_params(logentry["params"])
    return event


def scrub_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """before_breadcrumb. Returning None DROPS the breadcrumb.

    `category` is the LOGGER NAME for a logging breadcrumb — _breadcrumb_from_record
    sets `"category": record.name` (integrations/logging.py:371-379)."""
    if crumb.get("category") in _MUTED_LOGGERS:
        return None
    message = crumb.get("message")
    if isinstance(message, str):
        # redact_pii already knows this deployment's three shapes — email,
        # phone, 10-char VTU USN — and the email shape is the USN shape here.
        # Reused rather than reimplemented; a second regex for the same
        # identifier is a second one to get wrong.
        crumb["message"] = redact_pii(message)
    data = crumb.get("data")
    if isinstance(data, dict):
        # 'from'/'to' on a navigation breadcrumb are parseUrl(href).relative —
        # path AND query AND fragment.
        for key in ("from", "to", "url"):
            if isinstance(data.get(key), str):
                data[key] = _filter_url(data[key])
    return crumb
```

Wire it into the existing call, adding three lines and changing nothing else:

```python
# apps/api-py/app/main.py, inside the existing sentry_sdk.init(...)
        before_send=scrub_event,
        before_send_transaction=scrub_event,   # a sampled 200 carries request data too
        before_breadcrumb=scrub_breadcrumb,
```

Both hooks run *after* the scope's event processors (`client.py:782` then
`:917`/`:951`), so they see the request data the ASGI processor attached — which
is the whole reason a hook works here and an option would not.

Two honest costs. `redact_pii`'s phone pattern
(`(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,12}\d`) matches a 10–13 digit run, plus an
optional country prefix, and tolerates internal spaces and hyphens — so a long
integer or several digit groups in a breadcrumb can come back as `[redacted]`.
Its USN pattern is broader still: **any** 10-character alphanumeric run holding
both a letter and a digit (`app/redaction.py:31,34-38`), which includes a 10-char
hex id or a truncated request id. A mangled breadcrumb is recoverable from the
CloudWatch line the `request_id` tag joins to; a USN on a third party's servers
is not.

**One thing this hook does NOT cover, on purpose, and it is worth knowing.**
`LoggingIntegration` also builds a `SentryLogsHandler` at INFO
(`integrations/logging.py:125`) that stores `record.message` as a structured log
body (`:488`). It is inert only because `enable_logs` defaults to `False`
(`consts.py:1361`), and `before_breadcrumb` does not apply to logs —
`before_send_log` does. Turning Sentry Logs on reopens the mail-body door behind
this hook.

**Two fixes belong upstream of the scrubber and are cheaper than it.** Stop
logging the body at `app/mail_transport.py:84` — log `to` and `subject` only,
since the bounded `outbox` deque is already how a developer and the test suite
read the link, and that also gives `_MUTED_LOGGERS` back the SES-failure
breadcrumb. And add `SES_FROM_ADDRESS` to `Settings.production_boot_failures()`
(`app/config.py:1183`), so a production host cannot silently fall back to the
console transport at all. Both are **[NOT WRITTEN]**.

The SPA needs its own, because the token lands in the browser first. Put the two
helpers in a new `apps/web/src/app/core/telemetry-scrub.ts` with the same
allowlist:

```typescript
// apps/web/src/main.ts, inside the existing sentry.init({ ... })
  // sendDefaultPii governs IP and user identity, NOT the URL. httpContextIntegration
  // is a DEFAULT integration and the explicit `integrations:` array merges with the
  // defaults rather than replacing them, so it is live: it sets request.url from
  // location.href on every event, and /reset?token=<raw> ships without this.
  beforeSend: (event) => scrubUrls(event),
  beforeSendTransaction: (event) => scrubUrls(event),
  beforeBreadcrumb: (crumb) => scrubCrumb(crumb),
```

`main.ts` is in the initial chunk, so this is bundle-budget territory
(AGENTS.md): a few hundred bytes against `apps/web/angular.json`'s initial
`maximumWarning: 250kB` / `maximumError: 400kB`. It fits — but check the number,
do not assume it.

### Server-side scrubbing: a backstop, and where the `**` selector lies

**[ADMIN — NOT YET APPLIED]**, in the wider sense noted at the top: a setting no
committed file can turn on. No Sentry org, project slug or rule is committed
anywhere in this tree, so nothing below can be verified from the repository.
Re-check each in the console rather than trusting this page.

Advanced Data Scrubbing runs on Sentry's ingest pipeline *before storage*. The
payload has already crossed the public internet and been received. It is a
backstop for the case where the SDK-side hook is wrong, never a substitute for
it; the only thing that scrubs before data leaves your network is a self-hosted
Relay, which REEP does not run.

Authored in **Settings → Security & Privacy → Data Scrubbing** as
`[Method] [Data Type] from [Source]`:

```text
[Replace] [Auth in URLs]                        from [$string]
[Mask]    [Email Addresses]                     from [$string]
[Replace] [Regex Matches: (?i)\b\d[a-z]{2}\d{2}[a-z]{2,4}\d{2,3}\b] from [$string]

[Remove]  [Anything] from [$frame.vars.**]      # backstop for include_local_variables
[Remove]  [Anything] from [$http.data]          # backstop for max_request_body_size
[Remove]  [Anything] from [$http.cookies]       # reep_session matches NO default scrubber
[Remove]  [Anything] from [extra.**]
[Remove]  [Anything] from [contexts.trace.data.**]
```

Three things about that list matter more than its contents.

**`[Remove][Anything] from [**]` does not work, and believing it does is the
usual way a team thinks it is scrubbing everything.** Sentry documents that
`**` selectors *"only apply to the default event PII fields. Fields not on that
list will not be scrubbed automatically"* — its own example is that
`[$frame.**]` misses `filename` and `abs_path`
([advanced-datascrubbing](https://docs.sentry.io/security-legal-pii/scrubbing/advanced-datascrubbing/)).
The paths above are enumerated because they have to be.

**The default scrubbers protect nothing here.** They match, in both key names
**and values**, any of: `password`, `secret`, `passwd`, `api_key`, `apikey`,
`auth`, `credentials`, `mysql_pwd`, `privatekey`, `private_key`, `token`,
`bearer` — plus a credit-card regex
([server-side-scrubbing](https://docs.sentry.io/security-legal-pii/scrubbing/server-side-scrubbing/)).
Nothing in that list matches `usn`, `cgpa`, `attendance`, `transcript`,
`scorecard`, or `reep_session`. `AUTH_SECRET` and `GOOGLE_CLIENT_SECRET` are
covered by accident of naming; `DATABASE_URL` is not, which is why **Auth in
URLs** is on the list — the dev password lives in that string and the boot
guard's refusal is about serving it, not about telemetry.

**Additional Sensitive Fields matches by substring on the VALUE as well as the
key, so keep it narrow.** Sentry's wording: an entry like `mysekret` *"will
cause the removal of any field named `mysekret`, but also removes any field
value that contains `mysekret`"* — which is why its own example, `exp`, strips
the string `Unexpected error`. So `reep_session`, `cgpa`, `sgpa`, `scorecard`.
Do **not** add `marks` (it matches `remarks` and `bookmarks` as keys) and think
twice about `usn` (as a *value* substring it hits every field containing those
three letters anywhere). Organization-level settings override project-level
ones — *"If organization-wide settings exist, they'll override any project
settings, so be sure to disable them first"* — so set these at the level you
intend and check the other.

### Data residency — decided once, at organization creation

**There are two regions, neither is India, and the choice cannot be changed
afterwards.** US (Iowa, `us.sentry.io`) and EU (Frankfurt, `de.sentry.io`).
Sentry's wording: *"once selected, your data storage location can't be changed.
The only way to switch it is by creating a new organization"*
([data-storage-location](https://docs.sentry.io/organization/data-storage-location/)).
A new organization means a new DSN, a redeploy of both halves, and no migration
of the history. For a Bengaluru college whose telemetry can carry a student's
USN, this is a decision to make deliberately and once.

Region governs where error events, transactions, spans, replays, attachments
and backups live. It does **not** cover everything: user accounts and 2FA
authenticators, organization integration metadata, access tokens, organization
settings and teams, audit logs, **project metadata**, DSN keys, detailed usage
data, Sentry applications and SSO/SAML/SCIM metadata are replicated to the US
regardless, because that metadata is what lets an organization log in.

The repository cannot tell you which region is in use — no org or project slug
is committed. The DSN host is the answer, and a DSN is not a secret (Sentry:
*"DSNs are safe to keep public because they only allow submission of new events
and related event data; they do not allow read access to any information"*), so
printing the host is safe. The JSON **key** is verified —
`infra/cdk/reep_core/stack.py:933` is
`ecs.Secret.from_secrets_manager(external_secret, "SENTRY_DSN")`, and that second
argument *is* the key. The secret's **name** is not: `externalSecretArn` is a CDK
context value whose committed default is a placeholder
(`infra/cdk/reep_core/stack.py:272-275`), `infra/cdk/cdk.json` carries no real
value, and `reep/external` appears only in a synthetic test fixture
(`infra/cdk/tests/test_cutover_tools.py:69`). **Treat the `--secret-id` below as
unverified** and confirm it first:

```bash
# Which secret? (the name below is inferred from the CDK placeholder, not measured)
aws secretsmanager list-secrets --query "SecretList[?starts_with(Name,'reep/')].Name"

# Which region is this deployment actually shipping to? Measure; do not assume.
aws secretsmanager get-secret-value --secret-id reep/external \
  --query SecretString --output text \
  | python -c "import json,sys; print(json.load(sys.stdin)['SENTRY_DSN'].split('@')[-1].split('/')[0])"
# ...ingest.de.sentry.io -> Frankfurt.  ...ingest.us.sentry.io -> Iowa.
```

Whether either region satisfies the college's obligations under India's DPDP
Act 2023 is a legal question this document does not answer and nobody in this
repository has answered. It is named here so the gap is visible rather than
assumed away.

### The guards to add

**[NOT WRITTEN].** They belong in `tests/test_codebase_guards.py`, beside the
existing Sentry guard — `APP` and `REPO` are already defined there (lines
18-20), and none of these needs a database or a DSN. Same house style: state the
incident, then assert it cannot recur.

```python
def test_sentry_scrubs_before_the_event_leaves_the_process() -> None:
    """The query string is NOT covered by any flag in the init.

    THE INCIDENT: `send_default_pii=False`, `include_local_variables=False` and
    `max_request_body_size="never"` were read as "Sentry gets no PII", and
    app/config.py said so in a comment. In sentry-sdk 2.68.1 the ASGI
    integration filters the query string ONLY when `_experiments["data_collection"]`
    is set; nothing sets it, so `_get_request_data` takes the raw else-branch
    (integrations/_asgi_common.py:140-141). `GET /api/register/verify?token=<raw>`
    therefore shipped a live, single-use, account-provisioning token — and
    `GET /api/auth/sso/google/callback?code=&state=` a Google authorization
    code — on error events AND on sampled transactions, since request data is
    attached by an event_processor and Scope.apply_to_event runs those for
    transactions too (scope.py:1910).

    The three hooks are asserted together because the transaction hook is the
    one that gets forgotten: a token does not need an exception to leak.
    """
    source = (APP / "main.py").read_text(encoding="utf-8")
    assert "sentry_sdk.init(" in source, "the Sentry init moved; this guard needs updating"
    for hook in ("before_send=", "before_send_transaction=", "before_breadcrumb="):
        assert hook in source, (
            f"app/main.py's sentry_sdk.init must set {hook} — the constructor "
            "flags cover locals, bodies and cookies and cover NOTHING else. "
            "See this test's docstring."
        )


def test_the_scrubber_drops_account_tokens_and_keeps_the_screen_selectors() -> None:
    """Behavioural, not textual: the hook above can exist and do nothing.

    Both halves are asserted, and `board` is asserted because the first draft of
    _QUERY_ALLOW held five names and would have blanked it. A scrubber that
    deletes the whole query string passes the first assert and makes
    `interview hr` vs `interview generic`, or which leaderboard was slow,
    unanswerable in a trace — which is the observability this instrumentation
    was added for.
    """
    from app.telemetry_scrub import scrub_event

    event = scrub_event(
        {
            "request": {
                "query_string": "token=deadbeefcafe&code=4/0Axyz&specialization=hr&board=cgpa"
            }
        },
        {},
    )
    qs = event["request"]["query_string"]
    assert "deadbeefcafe" not in qs
    assert "4/0Axyz" not in qs and "4%2F0Axyz" not in qs
    assert "specialization=hr" in qs
    assert "board=cgpa" in qs


def test_an_error_log_ships_its_arguments_too_not_only_the_formatted_string() -> None:
    """LoggingIntegration's EventHandler writes THREE fields, not one:
    `event["logentry"] = {"message": <raw template>, "formatted": record.getMessage(),
    "params": record.args}` (sentry_sdk/integrations/logging.py:326-330).

    THE INCIDENT this anticipates: app/routers/auth.py:864 is a `log.error`
    whose arguments are `identity.email`, `user.google_sub` and `identity.sub`.
    ERROR is at DEFAULT_EVENT_LEVEL, so that record is a captured EVENT, and on
    this deployment the email IS the USN. A hook that scrubs `formatted` alone
    leaves it sitting one key over, uninterpolated.
    """
    from app.telemetry_scrub import scrub_event

    event = scrub_event(
        {
            "logentry": {
                "message": "%s is pinned to Google sub %s",
                "formatted": "1mp25mdm01@bgscet.ac.in is pinned to Google sub 118…",
                "params": ["1mp25mdm01@bgscet.ac.in", "118…"],
            }
        },
        {},
    )
    logentry = event["logentry"]
    assert "1mp25mdm01" not in logentry["formatted"]
    assert "1mp25mdm01" not in logentry["params"][0]


def test_the_mail_body_never_becomes_a_breadcrumb() -> None:
    """app/mail_transport.py:84 logs the ENTIRE outbound message at INFO — the
    raw activation link, the raw reset link, the one-time code. Sentry's
    LoggingIntegration is in _DEFAULT_INTEGRATIONS with DEFAULT_LEVEL=INFO and
    _breadcrumb_from_record stores record.message, the FORMATTED string, under
    `category = record.name`. It fires whenever SES_FROM_ADDRESS is blank, which
    no boot guard requires.

    The logger name is asserted too: mail_transport uses `logging.getLogger(__name__)`,
    so moving or renaming that module silently unmutes it.
    """
    from app.telemetry_scrub import _MUTED_LOGGERS, scrub_breadcrumb

    assert "app.mail_transport" in _MUTED_LOGGERS
    assert (APP / "mail_transport.py").exists(), "module moved; _MUTED_LOGGERS is now stale"
    assert scrub_breadcrumb(
        {"category": "app.mail_transport", "message": "MAIL ... /reset?token=deadbeef"}, {}
    ) is None


def test_the_spa_scrubs_the_token_out_of_its_own_url() -> None:
    """/activate?token= and /reset?token= are ANGULAR routes: the token is in
    the browser's location.href before it is ever in a request body. The browser
    SDK's httpContextIntegration is a DEFAULT integration — and an explicit
    `integrations:` array MERGES with the defaults rather than replacing them —
    so it sets event.request.url from location.href on every event, and the
    navigation breadcrumb's `from` is parseUrl(href).relative: path AND query.
    `sendDefaultPii: false` governs IP and user identity, not the URL.

    The api has a guard for its half of this and the web had none, which is why
    this one reads across the app boundary.
    """
    source = (REPO / "apps" / "web" / "src" / "main.ts").read_text(encoding="utf-8")
    assert "sentry.init(" in source, "the SPA Sentry init moved; this guard needs updating"
    for hook in ("beforeSend:", "beforeSendTransaction:", "beforeBreadcrumb:"):
        assert hook in source, (
            f"apps/web/src/main.ts's sentry.init must set {hook} — see this "
            "test's docstring."
        )
```

The first and last are textual, like the guard they sit beside, and carry the
same "the init moved" escape hatch. The middle three are behavioural on purpose:
a flag can be present and inert, and the failure this section exists to prevent
is precisely the one where the control was there and did not cover what everyone
believed it covered.
---

## Alerts, ownership, cron and uptime

**Almost nothing in this section is a file you can commit, and that is the
reason it needs writing down.** Monitors, alert rules and ownership rules are
console configuration in a third-party SaaS; the repository cannot enforce a
single one of them, and `grep` cannot tell you whether one exists. So every
rule below carries a marker from `docs/deployment-process.md` §"Status
markers" — **[IN FORCE]**, **[NOT WRITTEN]**, **[ADMIN — NOT YET APPLIED]** —
and every measured fact carries the command that re-checks it. Read a claim
without a marker as a claim nobody has verified. One widening, stated once:
that file defines **[ADMIN — NOT YET APPLIED]** as *a GitHub setting only the
repository owner can apply by hand*; here it means any console-only setting,
GitHub **or** Sentry. The other three markers it defines — [IN THIS BRANCH],
[ASPIRATIONAL] — are unused below.

Sentry split detection from routing on 2026-05-07 (early access 2026-04-20):
**Monitors** (Uptime, Cron, Metric) detect and create issues; **Alerts** run
when an issue matches and perform actions. One alert can be connected to many
monitors, so routing is configured once and reused. The old `/product/crons/`
and `/product/alerts/` doc paths still resolve but serve reorganised content;
the current pages are under `/product/monitors-and-alerts/`.

### Uptime monitors — and the one that would be a permanent false green

**Do not point an uptime monitor at `https://reep.sast-skills.com/health` or
`/ready`.** It returns HTTP 200 forever, from S3, whether or not a single API
task is alive. This is the same trap `docs/deployment-process.md` §8.2 tells an
operator not to curl by hand, and three facts compose into it, each in the
tree: `app.include_router(health.router)` mounts both probes **unprefixed**
(`apps/api-py/app/main.py:292`); the CloudFront distribution has exactly one
additional behavior, `/api/*` → origin `api-alb`
(`infra/cdk/reep_core/stack.py:1170-1171`); and the `reep-spa-fallback` viewer
function rewrites any URI whose last segment contains no `.` to `/index.html`
(`infra/cdk/reep_core/stack.py:151-167`). A monitor on `/health` is therefore
measuring S3 and the CDN and reporting it as the API — the worst possible
outcome for a tool whose whole job is to be believed. `/api/health` is no
better and fails differently: it reaches the API and 404s, because no route by
that name exists.

**Nor can you go round the CDN to the ALB.** `cdk.context.json` sets
`restrictAlbToCloudfront: true` and `cloudfrontPrefixListId: pl-9aa247f3`, so
`cf_ingress()` writes the ALB security group with `sourcePrefixListId` instead
of `cidrIp: 0.0.0.0/0` (`infra/cdk/reep_core/stack.py:404-412`). Sentry
publishes ONE IP list for all regions, at
`https://sentry.io/api/0/uptime-ips/`, and its own troubleshooting page says
those addresses "can change without notice" — so there is no stable set of /32s
to add beside the prefix list, and every checker is refused at the security
group. `origin.reep.sast-skills.com` is not a monitorable target and should not
be made one.

`/ready` is not wasted — it is the ALB target-group health check (path
`/ready`, interval 15 s, timeout 5 s, healthy 2, unhealthy 3, matcher `200`,
port 3300; `infra/cdk/reep_core/stack.py:752-798`). It is watched from *inside*
the VPC by `reep-no-healthy-api`. Sentry's job is the half CloudWatch cannot
see: DNS, the certificate, CloudFront, and the `reep-edge` WAF.

**[ADMIN — NOT YET APPLIED]** Two monitors:

| monitor | target | interval | what a red proves | what it does not prove |
| --- | --- | --- | --- | --- |
| `reep-api-edge` | `https://reep.sast-skills.com/api/auth/sso/status` | 1 min | DNS → CloudFront → WAF → ALB → a live Fargate task → **Postgres**. `sso_status` calls `password_door_open(db)`, which on a default production host falls through to `password_keys_exist(db)`, a real `SELECT` (`apps/api-py/app/routers/auth.py:249-272`), and nothing catches it — a dead database is an unhandled 500, not a cheerful 200. | The interview engine, SES, S3, DynamoDB or SSM. Those degrade on their own and say so at their own status endpoints; that is deliberate, and `apps/api-py/app/routers/health.py` explains why. |
| `reep-spa-edge` | `https://reep.sast-skills.com/` | 5 min | The S3 origin, the OAC, the SPA fallback function and the certificate. | Anything at all about the API. |

**The database half of that first row is conditional, and nothing would report
it if the condition changed.** `password_door_open` returns *before* touching
the database when `settings.password_login_allowed` is true or when
`PASSWORD_LOGIN=false` (`auth.py:264-272`), and `get_db` hands back a lazy
`Session` that never connects on its own (`app/db.py:37-43`). Setting
`PASSWORD_LOGIN=false` on the task would silently demote `reep-api-edge` from a
database probe to a process probe. If that ever happens, this monitor's row
above is a lie and only this paragraph says so.

`GET /api/auth/sso/status` is the only unauthenticated, side-effect-free **GET**
on `/api` — which is the property a monitor needs, not the same thing as the
only public path. `POST /api/register`, `GET /api/register/verify`
(`apps/api-py/app/routers/registration.py:283`, `:564`), `POST /api/auth/activate`,
`/auth/forgot`, `/auth/reset` (`app/routers/passwords.py`), `POST /api/auth/login`
and the Google start/callback pair are all public too; `verify` is even a GET,
but it consumes a single-use token, so polling it burns activation links. The
authenticated status endpoints are not candidates either
(`/api/interview/status` needs a session, `/api/platform/admin/status` and
`/api/agent/metrics` need the Main Admin). `sso/status` was written as a
login-screen feature probe — its docstring says "Unauthenticated by design" —
and it is being borrowed. Sentry Uptime does support custom request headers, so
an authenticated probe is technically possible — **do not build one.** REEP's
session is an httpOnly `reep_session` cookie, so "authenticated probe" means
pasting a long-lived signed session for a real account into a third-party
monitor's configuration. That is precisely the disclosure `send_default_pii=False`
exists to prevent, arriving through a door the SDK never touches.

Settings that matter, and why:

- **Failure Tolerance 3** (the default) and Recovery Tolerance 1 (also the
  default). Three consecutive failures at a 1-minute interval is ~3 minutes to
  an issue — no faster than `reep-no-healthy-api` (3 × 1 min, missing data
  BREACHING), which is correct: CloudWatch is the in-VPC signal with an
  explicit OK action, Sentry is the outside-in one that sees what CloudWatch
  cannot.
- **Timeout**: the docs give both 10 s ("Each request has a timeout threshold
  of 10 seconds") and "up to 30 seconds" (configurable). Leave it at the
  default; a `/api/auth/sso/status` that takes more than 10 s is a fault worth
  reporting. Which number the org's UI actually shows is
  [worth confirming there](https://docs.sentry.io/product/monitors-and-alerts/monitors/uptime-monitoring/).
- **The status-code check is what catches a misroute; a response assertion is a
  bonus and may not be available.** Do not repeat the argument that removing the
  `/api/*` behavior would serve `index.html` with a 200 — on this distribution
  it would not. `_SPA_FALLBACK_JS` returns the request unchanged for any URI
  starting with `/api/` (`stack.py:155-160`, the "Belt and braces" branch), and
  the distribution declares no `error_responses` at all, so the request would
  reach S3 through the OAC and come back 403. The default status assertion
  reds on that. A `content-type contains application/json` assertion is still
  worth adding if you can — it catches an HTML interstitial from a proxy or the
  WAF, and it catches the day someone adds a 403→`/index.html` error response
  "to fix SPA deep links" — but Sentry documents response assertions (status
  codes, header keys and values, JSON bodies) as limited to **Early Adopter
  program participants**, so check whether the org has the option before
  planning around it.
- **Allowlisting**: `reep-edge` is attached to the distribution (`wafWebAclArn`
  in `cdk.context.json`, and `infra/cdk/edge-import-map.json` shows that ACL id
  was adopted, so the CDK file mirrors what is live). It carries three rules
  (`infra/cdk/reep_core/edge.py:55-69`): `AWSManagedRulesCommonRuleSet`,
  `AWSManagedRulesKnownBadInputsRuleSet`, and **a rate-based rule** — `rate-limit`,
  action BLOCK, `limit=2000`, `aggregate_key_type="IP"`. At one request per
  minute per checker that limit is three orders of magnitude away and will not
  trip, and the checker sends a User-Agent so CommonRuleSet's
  `NoUserAgent_HEADER` does not either. If the limit is ever lowered, or a bot
  control group added, allowlist the User-Agent
  `SentryUptimeBot/1.0 (+https://docs.sentry.io/product/monitors-and-alerts/monitors/uptime-monitoring/)`,
  **not** IPs — Sentry's own troubleshooting says the IPs at
  `https://sentry.io/api/0/uptime-ips/` "can change without notice", and a
  blocked checker produces exactly the three consecutive failures that page
  someone at 3 a.m.

Billing: every plan includes one uptime monitor and one cron monitor. Two
uptime monitors is one extra at **$1.00/monitor**, available through
pay-as-you-go budget only — it cannot be pre-purchased as reserved volume, and
**the PAYG budget itself is only available on paid plans**, so on a Developer
plan `reep-spa-edge` cannot be created at any price. The check's own uptime
*request* spans are free and do not count against the span quota; spans and
errors captured *during* a check are billed as ordinary events. That is not
academic here — Sentry adds a `Sentry-Trace` header to every check and the API
runs `traces_sample_rate` 0.2 (`apps/api-py/app/config.py:337`), so a 1-minute
monitor can produce a billed API transaction on each check. Watch the first
day's volume rather than assuming either way.

### Cron monitors — one real job, and four things that are not scheduled

**REEP has exactly one production scheduled job.** `reep-retention-daily`, an
EventBridge Scheduler schedule firing `cron(30 21 * * ? *)` (21:30 UTC =
03:00 IST) at ECS `RunTask` on cluster `reep`, task family `reep-api`, with
`containerOverrides` `["python", "-m", "app.retention_job"]` and
`flexible_time_window` mode `OFF` (`infra/cdk/reep_core/stack.py:1069-1113`).
It is live, not aspirational: both it and the `reep-scheduler` role are in
`infra/cdk/import-map.json`, which only lists resources CloudFormation adopted
from the live account.

**It has no failure detection of any kind today.** `main()` in
`apps/api-py/app/retention_job.py` carefully returns 1 on any exception and
logs `ERROR` when `interviews_hard_delete_blocked` is non-zero — but an
EventBridge Scheduler ECS target does not surface a non-zero container exit as
a schedule failure, and no alarm in `stack.py` watches the scheduler, the task,
or a retention log pattern (the only log-derived alarm is
`reep-interview-dropped-turns`, `stack.py:1212-1229`). A schedule that stops
firing, a task that cannot pull the image, and a run that exits 1 are all
completely silent. This job deletes conversations at 90 days, interview
transcripts and recordings at `INTERVIEW_RETENTION_DAYS` (180), hard-deletes
30 days after a soft delete, and sweeps dead login codes. Its docstring already
names the hole — *"the compose loop today, a cron with alerting tomorrow"* —
and the comment on its own `except` says the cost:
*"a retention job that fails silently is a 180-day promise quietly not being kept."*

**[NOT WRITTEN]** The monitor cannot be created until the process initialises
the SDK. `python -m app.retention_job` imports only `retention` and `db`
(`apps/api-py/app/retention_job.py:30-31`); it never imports `app.main`, so
`sentry_sdk.init` is never called and every `sentry_sdk` call in that process
is a no-op — including a check-in. `SENTRY_DSN` is in its environment (the
retention task runs `task_def`, built by the shared `_api_task_def` helper,
which mounts it from the external secret at `stack.py:933`) and does nothing.
That is a code change, not console configuration, and it must land first.

| slug | schedule | timezone | `checkin_margin` | `max_runtime` | a red means |
| --- | --- | --- | --- | --- | --- |
| `reep-retention-daily` | crontab `30 21 * * *` | `Etc/UTC` | 15 | 30 | Nobody deleted anything last night. Every retention clock in the system — 30-day soft-delete grace, 90-day agent-run redaction, 180-day interview and audio purge — stopped advancing, and no other signal exists. |

Both numbers are minutes, not seconds, and both are chosen against this job:

- **`checkin_margin` 15.** `flexible_time_window` is `OFF`, so the schedule
  fires on the minute; the delay that varies is Fargate task placement, ENI
  attachment and image pull. Fifteen minutes absorbs a cold pull without
  losing a whole night to a false MISSED.
- **`max_runtime` 30.** `purge_expired` takes no batching parameter and does
  the entire backlog in one transaction — its caller says so and names the fix
  ("add a limit to `purge_expired` itself and drain it in a loop HERE"). Thirty
  minutes bounds the first run against a neglected backlog. If it is genuinely
  exceeded, apply that fix; do not raise the ceiling.
- `failure_issue_threshold` **1** and `recovery_threshold` **1**. The job runs
  once a day. A threshold of 2 buys two days of silence for nothing.

The decorator, in `apps/api-py/app/retention_job.py`, after the SDK is
initialised in that process. Passing `monitor_config` **upserts** the monitor,
which makes this file the source of truth for the schedule rather than a
console form somebody edited once:

```python
from __future__ import annotations

from sentry_sdk.crons import monitor

# sentry_sdk 2.68.1's MonitorConfig TypedDict has exactly seven keys, all
# snake_case: schedule, timezone, checkin_margin, max_runtime,
# failure_issue_threshold, recovery_threshold, owner. It lives under
# TYPE_CHECKING in the private sentry_sdk._types, so it is named here rather
# than imported — a camelCase key is accepted by the dict and ignored by the
# ingest, which is the failure mode this comment exists to prevent.
#
# The schedule here MUST equal RetentionSchedule's cron() in
# infra/cdk/reep_core/stack.py:1093. Two sources of truth for one clock is how
# a monitor ends up reporting MISSED every night against a job that ran fine.
_MONITOR = {
    "schedule": {"type": "crontab", "value": "30 21 * * *"},
    "timezone": "Etc/UTC",          # the schedule is cron(30 21 * * ? *) UTC
    "checkin_margin": 15,           # MINUTES late before MISSED — Fargate pull
    "max_runtime": 30,              # MINUTES before TIMEOUT — one transaction
    "failure_issue_threshold": 1,   # runs daily; a second chance is a lost day
    "recovery_threshold": 1,
}


@monitor(monitor_slug="reep-retention-daily", monitor_config=_MONITOR)
def main() -> int:
    ...
```

`@monitor` reports `error` when the wrapped call raises and `ok` otherwise
(`sentry_sdk/crons/decorator.py`, `__exit__`) — but `main()` **catches** its
exception and returns 1, so the decorator alone would report a failed sweep as
OK. Either re-raise after logging, or send the terminal check-in by hand. The
same applies to `interviews_hard_delete_blocked`: a run that completes while a
student's recording could not be destroyed is a `MonitorStatus.ERROR`, not a
success, and `retention_job.py` already computes that number.

**[NOT WRITTEN]** Four things look like cron candidates and are not. **A cron
monitor for a job with no schedule is a lie, and it fails in the direction that
destroys trust in every other monitor** — it reports MISSED on quiet days,
someone mutes it, and Sentry does the same thing by itself: 14 days of
consistent failures marks a monitor environment broken and emails you, and a
further 14 (28 total) auto-mutes it. It then stops paging without anyone
deciding to stop it, and **muting does not stop the $0.78 either** — only
deactivating or deleting does.

| candidate | why not | what to do instead |
| --- | --- | --- |
| `retention.finalize_orphaned_interviews` | Boot-only, by design. `apps/api-py/app/main.py` runs it from the lifespan and says why: *"Startup is where this belongs, with no cron at all, because a worker that has just booted is precisely the process that knows the previous one died."* A crontab monitor would go MISSED on every day nobody deploys. | Nothing. If you want it monitored, **schedule it first** — and read that comment before deciding to. |
| `python -m app.voice_platform.queue.worker --degree UG\|PG` | A complete long-running SQS consumer with **no trigger anywhere**: no compose service, no ECS service, no schedule, and the CDK Lambda asset explicitly excludes `worker.py` from the zip (`infra/cdk/reep_voice_platform/stack.py:145`). A monitor would certify the absence of a job that was never started. | Give it a trigger first. Until then the honest signal is queue depth / oldest-message-age / DLQ depth on `reep-voice-candidates-ug` and `-pg` — CloudWatch's job, not Sentry's. |
| AWS Backup `reep-daily` (`cron(0 19 * * ? *)`, `stack.py:629`) and `reep_weekly_restore` (`cron(0 4 ? * SUN *)`, `stack.py:665`) | AWS-managed. There is no process of ours to check in from, and a check-in nobody can send is a monitor that is red by construction. | The three `NumberOf*JobsFailed` alarms at `stack.py:1322-1339`. They are inside `if harden:` and **are not deployed** — see the harden note below. That is exactly how the 2026-09-06 RDS backup failure stayed invisible until it was found by hand on 2026-09-07. Sentry cannot cover for that. |
| `docker-compose.prod.yml`'s `retention` and `db-backup` sidecars | `while true; do …; sleep 86400; done` is an **interval**, not a schedule, and its phase moves on every container restart. A crontab monitor goes MISSED after the first restart. | If that path is ever monitored, use `{"type": "interval", "value": 1, "unit": "day"}` with a generous `checkin_margin`, and wrap the command rather than editing it. |

**The harden phase, stated as measured rather than inferred.** Do not read the
absence of those three alarms from `import-map.json` — that file lists only what
CloudFormation *adopted* at cutover and could never list a later create. The
citable evidence is in the tree: commit `d234710` (2026-09-08) says outright
that "the alarm that would report it ships in the harden phase, **which has
never been deployed**". What *has* run is cutover step 8 — `drVaultArn` in
`cdk.context.json` is the output of a real `reep-vault-dr` in ap-southeast-1 —
and step 9a, which failed and rolled back cleanly because the AWS account is on
the **Free** plan, which caps `BackupRetentionPeriod` and forbids Multi-AZ. The
alarms sit inside the same `if harden:` as that RDS change, so "just deploy
them" is not available: it needs an account-plan upgrade, or
`-c dbMultiAz=false -c backupRetentionDays=N` — and note that second key drives
**both** RDS retention and the AWS Backup rule (`stack.py:44`), so lowering it
to fit the free plan also shortens the existing 35-day rule. Confirm against the
account before acting:

```bash
aws cloudformation describe-stack-resources --stack-name reep-core \
  --query "StackResources[?contains(LogicalResourceId,'JobsFailedAlarm')].[LogicalResourceId,ResourceStatus]" \
  --output text     # empty output = the three alarms are not there
```

**No workflow in `.github/workflows/` has a `schedule:` trigger**, so there is
no GitHub-Actions cron monitor to create today (measured 2026-09-08; re-check
with `grep -rn "schedule:" .github/workflows/`). There are four workflows:
`deploy.yml`, `ops-task.yml` and `cdk-deploy.yml` are `workflow_dispatch`, and
`ci.yml` runs on push to `main`, on pull requests and on dispatch. The one-off
ECS tasks the dispatch workflows run (`alembic upgrade head`, `app.seed_kb`,
`app.seed_roster`) are deploy steps, not scheduled jobs — a cron monitor on a
manually-triggered workflow reports MISSED every day nobody deploys. The curl
form below is for the first job that *is* scheduled — a nightly `cdk diff` drift
check is the obvious candidate, since nothing in this repo runs on a clock to
notice drift between the CDK definition and the live account:

```bash
# All four parts come from the DSN. Do NOT echo it; put it in a repo secret.
#   https://<public-key>@o<orgId>.ingest.sentry.io/<project-id>
SENTRY_CRONS="https://o<orgId>.ingest.sentry.io/api/<project-id>/cron/<slug>/<public-key>/"

# It is `monitor_config` in a JSON BODY that performs the upsert — which is why
# this has to be a POST. snake_case keys (checkin_margin, max_runtime), never
# camelCase: a camelCase key is accepted and ignored, so the monitor is created
# with no margin at all and MISSES on the first slow night.
curl -sS -X POST "$SENTRY_CRONS" -H 'Content-Type: application/json' --data-raw \
  '{"monitor_config":{"schedule":{"type":"crontab","value":"0 2 * * *"},
    "timezone":"Etc/UTC","checkin_margin":10,"max_runtime":20},"status":"in_progress"}'

<the job>
rc=$?   # captured HERE. `$?` read later is whatever ran last, which is how a
        # log line inserted between the job and the check-in reports every
        # failure as ok.
if [ "$rc" -eq 0 ]; then s=ok; else s=error; fi
curl -sS "$SENTRY_CRONS?status=$s"
```

Rate limit is **6 check-ins per minute per monitor environment**. For a shell
job, `sentry-cli monitors run <slug> -- <cmd>` wraps the command and sends both
check-ins from the exit code; its flags are `-s/--schedule`,
`--check-in-margin`, `--max-runtime`, `--timezone` (one word) and
`-e/--environment` — it documents **no** `--failure-issue-threshold` or
`--recovery-threshold`, so set those in the upsert body or the UI.

Billing: one cron monitor is included on every plan, so `reep-retention-daily`
is free. Extras are **$0.78/monitor**, PAYG only (and PAYG needs a paid plan),
and a monitor deleted mid-period still counts for that period.

### Alert rules — page-now is four lines long, and one inbox reads all of them

**There is one alert channel and it is email to one address.** No Slack,
PagerDuty, Opsgenie or Jira integration is configured or referenced anywhere in
the tree; the only alert *receiver* committed in this repository is
`alertEmail: "bdarshan5@bgscet.ac.in"` in `infra/cdk/cdk.context.json`, the
intended sole subscriber to the `reep-alerts` SNS topic. `.github/CODEOWNERS`
names the same person — as a GitHub account, `@darshani8` — as the owner of
every path in the repository. So the page-now / notify-only / ticket split below
is **a discipline, not a routing**: today all three arrive in the same inbox.
Write it down anyway, because the first thing that happens when a second
maintainer appears is that somebody asks which of these should wake a person,
and the answer should not have to be reconstructed.

**[ADMIN — NOT YET APPLIED]**

| rule | condition | class | frequency | why |
| --- | --- | --- | --- | --- |
| `reep — the site is unreachable` | Uptime monitor `reep-api-edge` opens an issue (3 consecutive failures) | **page-now** | 30 | This is the only signal for a DNS, certificate, CloudFront or WAF failure. Five of the six CloudWatch alarms are dimensioned on the ALB, the ECS service or the RDS instance; the sixth is a log metric filter that simply goes quiet. All six stay green while the whole edge is down. |
| `reep — retention did not run` | Cron monitor `reep-retention-daily` MISSED, TIMEOUT or ERROR | **page-now** | 1440 | Nothing else in the system can express "the job stopped running", and its failure is a data-protection promise silently broken. Once a day is the right frequency for a job that runs once a day. |
| `reep — the API refused to boot` | Issue first-seen, `logger:reep.startup`, `level:fatal`, `environment:prod` | **page-now** | 60 | `production_boot_failures()` is raised from the lifespan and `log.critical(refusal)` is emitted first (`apps/api-py/app/main.py:161-169`, logger `reep.startup` at `:47`). Sentry is initialised at **import** time, before the app object (`:59-87`), and `sentry_sdk.init` is called with no `integrations=` argument, so the default `LoggingIntegration` is installed at its `DEFAULT_EVENT_LEVEL = logging.ERROR` — CRITICAL clears it and arrives as `fatal`. Structurally this is captured today; it has not been fired against a live DSN, so send one test event before trusting the rule. It matters because the failure it describes is the one `docs/deployment-process.md` §8 documents as invisible: the tasks exit, the circuit breaker throws the deployment away, the service reconverges on the old tasks, and the deploy reports success. |
| `reep — nobody can sign in` | Issue matching `GET /api/auth/sso/status -> 200 unavailable` | **page-now**, once | 1440 | `apps/api-py/app/routers/auth.py:689` logs ERROR on every call when `ENV=prod` and Google is unconfigured, and its own comment says why: *"Nothing works. Say so loudly here, because there is no other door."* One event per login-page render, so the frequency throttle is doing all the work — page once a day, not once a visitor. |
| `reep — new issue type in production` | `FirstSeenEventCondition`, `environment:prod` | notify-only | 60 | This is `docs/deployment-process.md` §8.5's stated rollback trigger. It cannot be evaluated as written until events carry a `release`; see the release section of this document. |
| `reep — interview turns are being dropped` | Issue matching `Dropped interview turn` | **ticket, never a page** | 1440 | CloudWatch already pages for this (`reep-interview-dropped-turns`). See the next subsection. |
| `reep — model provider failing` | Issues from `app/ai/llm.py`, `app/ai/orchestrator.py`, `app/routers/agent.py` | ticket | 1440 | A rate-limited free-tier provider produces one event per student message; `llm_requests_per_minute: 5` bounds each user, not the cohort. This is a capacity fact, not an incident. |
| `reep — browser errors` | Any issue in the web project | ticket | 1440 | Today these frames are minified against hashed chunk names and are largely undiagnosable; see the source-map section. **Confirm the project receives anything at all first** — `apps/web/src/environments/environment.ts` ships `sentryDsn: ''` and `.github/workflows/deploy.yml:191-203` seds in `WEB_SENTRY_DSN` only when that repo secret exists, printing "shipping without client telemetry" otherwise. An empty stream reads as "no browser errors" either way. |

**The page-now list is four entries and each one is a failure with no second
witness.** Everything else in this deployment already has a witness — a
CloudWatch alarm, a log line, a status endpoint, or a student who will say so —
and a pager that fires for things somebody else is already watching stops being
read. That is the same argument `AGENTS.md` makes about the boot guard: a guard
that trips on a laptop gets deleted by whoever is trying to ship that afternoon.

Two mechanics that will silently break a rule you have just written:

- **`frequency` is minutes**, an integer valid from **5 to 43200** (30 days),
  throttling one issue per rule. The UI dropdown is presets over that range
  (5/10/30/60 minutes, 3/12/24 hours, 7/30 days) — every value in the table
  above is one of them. `actionMatch` is required and takes `all` | `any` |
  `none`; `filterMatch` becomes required the moment `filters` is non-empty.
- **`environment: production` matches nothing in this deployment.** The API's
  Sentry environment tag is `settings.env.strip() or "development"`
  (`app/main.py:64`), and the CDK sets `api_environment["ENV"] = "prod"`
  (`infra/cdk/reep_core/stack.py:283`) — the literal string is **`prod`**. The
  SPA's is **`development`**, in production, because `main.ts` writes
  `environment.production ? 'production' : 'development'`, `environment.ts`
  hardcodes `production: false`, and `angular.json` has no `fileReplacements`.
  Every rule above is written against `prod` for that reason, and none can
  usefully be scoped to the web project until that is fixed.

### Ownership rules — the file syntax, and what it is honestly worth here

Sentry's ownership rules are `type:pattern owners`, one per line, with exactly
four matcher types: `path` (file paths in the event's stack trace), `module`,
`url` (the event's request URL) and `tags.TAG_NAME`. Patterns are **unix glob**
(`*` and `?`) — the docs say plainly "This is not regex". The **last** matching
rule wins for assignment, and within that rule the **leftmost** owner is
assigned. Ownership rules take precedence over CODEOWNERS, not the other way
round.

**[ADMIN — NOT YET APPLIED]** For the `reep-api` project:

```text
# type:pattern owners — glob, never regex. LAST match wins; leftmost owner is
# assigned. The leading */ is load-bearing: the container is WORKDIR /app with
# COPY app ./app (apps/api-py/Dockerfile:21,27), so a frame's path should be
# /app/app/routers/interview.py — a pattern copied from .github/CODEOWNERS
# ("apps/api-py/app/...") matches nothing at all. UNVERIFIED against a real
# event: */app/... is written to tolerate either that or a sys.path-relative
# app/..., but read one frame in the issue stream before trusting these.

path:*/app/routers/*                 bdarshan5@bgscet.ac.in
path:*/app/models/*                  bdarshan5@bgscet.ac.in

# Rule 1's surface. A one-line edit in either of the first two re-enables
# shipping a student's transcript to a third party; tests/test_codebase_guards.py
# (test_sentry_never_ships_local_variables_or_request_bodies) and
# tests/test_tracing.py exist because that already happened once.
path:*/app/main.py                   bdarshan5@bgscet.ac.in
path:*/app/tracing.py                bdarshan5@bgscet.ac.in
path:*/app/ai/llm.py                 bdarshan5@bgscet.ac.in

# The interview: the longest-lived path, the only one holding live audio.
path:*/app/interview_nova.py         bdarshan5@bgscet.ac.in
path:*/app/interview_core.py         bdarshan5@bgscet.ac.in
path:*/app/interview_audio.py        bdarshan5@bgscet.ac.in
path:*/app/routers/interview.py      bdarshan5@bgscet.ac.in

# Rule 2's gate, and the reaper that deletes on a clock.
path:*/app/routers/mentor.py         bdarshan5@bgscet.ac.in
path:*/app/retention*.py             bdarshan5@bgscet.ac.in

path:*/app/voice_platform/*          bdarshan5@bgscet.ac.in

# url: needs no stack frames, so it is the matcher that still works when the
# exception is raised somewhere generic (a middleware, the ORM, httpx).
url:*/api/interview*                 bdarshan5@bgscet.ac.in
url:*/api/agent/*                    bdarshan5@bgscet.ac.in
url:*/api/auth/*                     bdarshan5@bgscet.ac.in
url:*/api/platform/*                 bdarshan5@bgscet.ac.in
```

**This file assigns identically for every event, and saying otherwise would
repeat a mistake this repository has already corrected in writing.**
`.github/CODEOWNERS` opens with the caveat: `gh api
repos/darshani8/reep-/collaborators` returns exactly one account, every rule
names it, so *"the owner set shown on a one-character edit to
`student_data_egress_allowed` is byte-identical to the owner set shown on a typo
fix in a comment."* The same is true here the moment you paste it. Its value
today is the same value that file claims for itself — **a reviewable inventory
of the paths where a wrong line has a different consequence** — and the day a
second person exists, changing the owner on the sharp paths is the whole
migration.

Three further constraints, each of which will waste an afternoon if discovered
later:

- **`path:` rules cannot work on the web project.** The production SPA is built
  with `outputHashing: "all"`, optimisation on and no `sourceMap` in the
  production configuration (`apps/web/angular.json`), and nothing uploads source
  maps — so browser frames are single letters against hashed chunk names.
  `url:` is the only matcher that functions on `reep-web` until that changes.
- **Do not import `.github/CODEOWNERS`.** The integration requires a Business
  or Enterprise plan plus code mappings and external team/user mappings, and
  what it would import is a catch-all naming one GitHub account. It buys
  nothing and adds a second place where ownership can be wrong. (The plan
  requirement is current in Sentry's ownership-rules doc; the org's own tier is
  not recorded anywhere in this repository — check it in **Settings →
  Subscription** before planning around any plan-gated feature, including the
  PAYG budget the second uptime monitor needs.)
- **`tags.` is not usable here.** REEP's Sentry tags are `request_id`
  (`app/traceability.py:54`), `conn_id`, `interview_session_id` and `engine`
  (`app/routers/interview.py:1032-1036`), `degree`, `specialization` and
  `call_id` (`app/voice_platform/api/media_bridge.py:252`), and `session_id`
  (`app/voice_platform/api/call_close.py:105`). `engine`, `degree` and
  `specialization` are the bounded ones — two engines, two degree levels, four
  specializations — and the rest are per-request identifiers with unbounded
  vocabularies. Neither kind helps: an unbounded tag cannot be an ownership key,
  and a bounded one routes to the same single owner as everything else.
- **Auto-assignment is one-shot per issue.** Once an issue has been assigned by
  any means, manual or automatic, future auto-assignment is off for that issue
  and new events will not change the assignee. Editing these rules does not
  re-assign anything that already exists.

### CloudWatch keeps what Sentry cannot see, and nothing pages from both

**The six live CloudWatch alarms already exist, already fire into
`reep-alerts`, and are not being replaced.** `reep-no-healthy-api`,
`reep-alb-5xx`, `reep-interview-dropped-turns`, `reep-rds-low-storage`,
`reep-rds-cpu` and `reep-api-cpu-at-max` are all in `import-map.json`, meaning
CloudFormation adopted them from the live account. The handoff shape is already
built too: `reep-api-cpu-at-max`'s own description reads *"Raise the ceiling or
find the hot path in Sentry."* (`stack.py:1264`). Copy that shape — an alarm
that pages and names Sentry as the place to look — rather than inventing a
second pager.

The dividing line, stated once: **CloudWatch pages for anything measured about
infrastructure it can see from inside the account. Sentry pages for the absence
of a job and the absence of the site — the two things CloudWatch structurally
cannot express here. Nothing pages from both.**

| signal | pages from | why not the other one |
| --- | --- | --- |
| Fewer than one healthy target behind the ALB | CloudWatch `reep-no-healthy-api` (3 × 1 min, missing data BREACHING, OK action on — `ok=True` at `stack.py:1247`) | Sentry's uptime check would notice the same outage no sooner, and it resolves an issue rather than mailing an explicit OK the way this alarm's OK action does. |
| DNS, certificate, CloudFront, WAF | **Sentry Uptime `reep-api-edge`** | Five of the six alarms are dimensioned on the ALB, the ECS service or the RDS instance; the sixth is a log metric filter that treats missing data as NOT_BREACHING. A WAF rule that blocks every student leaves all six green — the log alarm most of all, because it goes quiet exactly when nobody can reach the site. |
| Target 5xx, ALB 5xx, RDS storage and CPU, ECS CPU | CloudWatch | Infra metrics. No process raises an exception when a disk fills. |
| `Dropped interview turn` | CloudWatch `reep-interview-dropped-turns` (metric filter on the log literal, `REEP/AI` namespace, 5-min Sum ≥ 1, `stack.py:1212-1229`) | Sentry **also** receives every one of these as an issue, because the SDK installs `LoggingIntegration` at its default `event_level=ERROR` and the writer logs at ERROR on the per-turn hot path. That issue is the diagnosis surface — the traceback, the `request_id` tag, the `interview_session_id` — and it must be **muted from paging**, or one database blip during one 8-minute interview pages once per dropped turn for the rest of the call. AGENTS.md's runbook query stays the confirmation either way. |
| AWS Backup job / copy / restore-test failures | CloudWatch, once `harden` is deployed | AWS-managed jobs. There is no process of ours to check in from. These three alarms are **not deployed** — commit `d234710` says the harden phase never has been, and step 9a's attempt rolled back on the account's Free-plan retention cap — which is how the 2026-09-06 RDS backup failure stayed invisible. |
| `reep-retention-daily` stopped running | **Sentry Crons**, and only Sentry Crons | An EventBridge Scheduler ECS `RunTask` target does not surface a non-zero container exit as a schedule failure, and CloudWatch has no metric for "a schedule did not fire". Absence-of-check-in is the only mechanism that expresses it. |
| A stack trace, an issue owner, a first-seen-in-release | Sentry | CloudWatch holds the raw log line the trace is joined back to, through the `X-Request-ID` tag; it holds no frames. |

**One last thing to verify before believing any of this pages anybody.** The
SNS email subscription is created only inside `if alert_email and harden:`
(`infra/cdk/reep_core/stack.py:507-512`), because CloudFormation cannot adopt
one — and the harden deploy that would have created it rolled back. A
Terraform-created subscription may still be live and unmanaged:
`tools/import_map.py` reads `alertEmail` out of a subscription in the released
state, which is where `cdk.context.json`'s value came from. This repository
cannot tell you whether that subscription is confirmed. Ask the account:

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn arn:aws:sns:ap-south-1:445363794125:reep-alerts \
  --query 'Subscriptions[].[Endpoint,SubscriptionArn]' --output text
# A SubscriptionArn of "PendingConfirmation" is a tripwire with the bell cut off.
```

Do the same for Sentry before trusting the four page-now rules: send one test
notification per rule and confirm it arrives in a mailbox a human reads. A
detection mechanism with no receiver is the failure
`docs/deployment-process.md` §1 already writes down for SNS, one layer up.
---

## Noise, sampling and the bill

**Read this precondition first: none of what follows bills anything unless a
DSN is actually set.** `main.py:59` gates `sentry_sdk.init` on
`settings.sentry_dsn.strip()`, and the API's `SENTRY_DSN` arrives as an ECS
**secret** — `infra/cdk/reep_core/stack.py:933`, from the operator-owned
`reep/external` Secrets Manager entry — whose value in the last committed
Terraform state is `""`. The SPA's DSN is not in the tree either:
`apps/web/src/environments/environment.ts:16` is `sentryDsn: ''` and
`.github/workflows/deploy.yml:194-201` seds a `WEB_SENTRY_DSN` GitHub secret
into it at build time, logging *"no WEB_SENTRY_DSN secret; shipping without
client telemetry"* when it is absent. So each half of this section is
conditional on one secret an operator sets by hand. Check both before
budgeting against any number here.

**`sentry_sdk.init` in `apps/api-py/app/main.py` passes six options and none of
them is a volume control.** There is no `integrations=`, no `before_send`, no
`traces_sampler`, no `ignore_errors`. The three flags that *are* there
(`send_default_pii=False`, `include_local_variables=False`,
`max_request_body_size="never"`) are rule-1 guards on what each event
*contains*; they say nothing about how many events there are. (For the
span side, 2.68.1 also carries `ignore_spans`, `before_send_span` and
`trace_ignore_status_codes` — all validated in
`sentry_sdk/client.py:387-399`. Reach for those rather than a 1.x-era
`before_send_transaction` recipe.) The consequence is one sentence and it
drives everything below: because `integrations=` is omitted, the SDK installs
`LoggingIntegration` with its default `event_level=logging.ERROR`
(`sentry_sdk/integrations/logging.py:27`), so **every `log.error` and
`log.exception` anywhere under `app/` is a billable Sentry *event*** — no
exception object required. Errors are metered per event and *grouped* into
issues, which is why this is easy to miss: nine thousand events can sit behind
one unremarkable-looking row in the issue list. Several of the loudest ones
have comments beside them saying the condition is expected.

### What is already quiet, and must not be "fixed"

Three things are right by accident of good design and a reader coming to cut
noise will otherwise cut them.

**A 4xx `HTTPException` is not captured.** Starlette's patched exception
handler captures an exception that reaches a registered handler only when its
`status_code` is in `failed_request_status_codes`, which defaults to
`frozenset(range(500, 600))` (`sentry_sdk/integrations/__init__.py:12`, applied
at `sentry_sdk/integrations/starlette.py:315-322`). So the 401/403/404/409/422
control flow, and both 429 limiters, produce transactions and never issues. (A
genuinely unhandled exception with no `status_code` is still captured — this
rule is about `HTTPException`, not about everything.) The LLM limiter
(`app/ratelimit.py:75-83`) does not log at all; the login brute-force limiter
(`app/routers/auth.py:484-492`) logs WARNING, which is a breadcrumb.

**Every WebSocket refusal is a WARNING, deliberately.** Close 4003, 4010,
4012, 4013, 4014 and 4015 in `app/routers/interview.py` are all logged
WARNING, with the 4013 site saying why in its own comment (`interview.py:853-856`):
*"this is a refusal, not a fault... there is no traceback worth printing."* A
normal browser disconnect never raises — `WebSocketDisconnect` gets its own
`except*` arm and an INFO line (`app/interview_nova.py:922-927`). The two
exceptions are the two ERROR sites in the table below, and they are the
outliers, not the pattern.

**A blank DSN costs nothing.** `main.py:59` skips `init` entirely, so every
downstream `sentry_sdk` call is a no-op. Laptops and CI are already free and
`tests/test_tracing.py:28` pins it.

### The noise table

Volumes are **arithmetic over committed constants, computed 2026-09-08 — not
observed traffic**. No live Sentry stat and no CloudWatch metric was read for
any figure here. Two inputs have no source in the repo at all and are marked
*(assumed)* where they appear. The health-check rate additionally assumes one
probe per load-balancer node per AZ per target every 15 s — **AWS does not
publish a guarantee that node count equals AZ count**, so the multiplier can
differ in practice. Recompute rather than trusting the column.

Markers are `docs/deployment-process.md`'s taxonomy: [IN FORCE] is live in the
tree, [NOT WRITTEN] is a code change that does not exist. That document defines
[ADMIN — NOT YET APPLIED] as a *GitHub* setting only the owner can apply
(`deployment-process.md:30`); it is borrowed here for a **Sentry-console**
change with the same meaning — no committed file can turn it on.

| source | estimated volume | control | where the control lives |
| --- | --- | --- | --- |
| `GET /ready` — the ALB health check (`infra/cdk/reep_core/stack.py:760-767`, 15 s interval, 2 AZs, `api_min=2` at `stack.py:205`) | ~16 req/min ⇒ ~700k/month at min scale; ~3.5M at `api_max=10`. At the flat 0.2 that is ~140k transactions/month, each carrying its own `SELECT 1` SQL span (`app/routers/health.py`) from the auto-enabled `SqlalchemyIntegration` ⇒ **~280k–420k spans/month**, ×5 at max scale, doubled while both blue and green target groups hold healthy tasks — they carry the same probe | Sentry's built-in **health-check transaction filter**. Its default glob list is *"\*healthcheck\*, \*health-check\*, \*heartbeat\*, \*/health, \*/healthy, \*/healthz, \*/\_health, \*/[\_health], \*/live, \*/livez, \*/ready, \*/readyz, \*/ping, \*/up"* — both REEP paths match with nothing to write. Filtered events do not consume quota | **inbound filter**, console-only, free on every tier, no deploy. [ADMIN — NOT YET APPLIED]. Then a `traces_sampler` returning 0.0 for those paths, so they stop leaving the process at all — [NOT WRITTEN] |
| `GET /api/auth/sso/status` → `log.error` on **every call**, but only while `ENV=prod` *and* `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are blank (`app/routers/auth.py:687-689`; both are wired from the same secret at `stack.py:931-932`) | One error event per render of `/login` — the probe at `login.component.ts:424` — indefinitely. At 300 login-page loads/day *(assumed; no source in the repo)* that is **~9k events/month** — nearly twice Developer's entire 5k allowance, ~18% of a paid plan's 50k — for a condition already reported honestly in the 200 body | Latch it: log once at boot beside the other lifespan gates, WARNING at the request site | **SDK / code**. [NOT WRITTEN]. Note the tier consequence: dropping it server-side by message needs a **custom inbound filter, available only on Trial, Business or Enterprise** — on Team the only fix is the code change |
| `Dropped interview turn` (`app/interview_nova.py:1949-1955`) | Zero when healthy. During one database incident: ~30 turns × 8 concurrent interviews ⇒ **~240 events in eight minutes**, repeating for the length of the incident. The bare `except Exception` guarantees it repeats — that is the design | Count drops and report **one** event at finalize. `interview_sessions.turns_emitted` vs `turns_persisted` already records exactly this | **SDK / code**. [NOT WRITTEN]. The runbook query in `AGENTS.md` still answers "did it save anything"; nothing here weakens it |
| `Nova interview failed` — `log.exception` on the TaskGroup catch-all (`app/interview_nova.py:929-939`) | One per affected interview. Bounded by `interview_max_per_student_per_day=8` and the per-worker `interview_max_sessions=100` (`app/config.py:396, 376`) | **None. Leave it.** Its comment is right: anything reaching that arm is a bug here, and the traceback is the point | — |
| `WS /api/interview` — the only two ERROR sites among the close codes, and they are **two different codes**: **4001** engine not configured (`interview.py:762-767`) and **1008** STUDENT with no `studentId` (`interview.py:743-749`; `_CLOSE_NOT_A_STUDENT = 1008` at `interview.py:133`) | One per socket attempt, per retry. The second is a permanently broken account whose owner cannot fix it, so they retry | WARNING with the **student/user id** as a tag — never the email, which would walk PII back in through the door `send_default_pii=False` closed. That matches the 1008 role refusal at `interview.py:723`, which already reasons this way | **SDK / code**. [NOT WRITTEN] |
| LLM provider failure — `app/routers/agent.py:370, 445` and `app/ai/orchestrator.py:243, 482, 530, 585` (all six confirmed by grep) | `llm.py:271` calls `raise_for_status()`, so a free-tier 429 raises into six `log.exception` handlers. `llm_requests_per_minute=5` (`config.py:296`) bounds a **user**, not a cohort: a rate-limited key during a 60-student lab session is **hundreds of identical events in an hour** | `orchestrator.py:585` at minimum should be WARNING — the `_finalize` docstring above it says the polish is optional and *"the deterministic text is authoritative — any failure or a refused provider keeps it unchanged"*. An ERROR for a documented no-op is the clearest case in the tree | **SDK / code**. [NOT WRITTEN] |
| `Could not open the Nova Sonic stream` (`interview_nova.py:845`) / `Bedrock refused the stream` (`:1259`) | One per Start press during a throttle. `interview_max_sessions=100` is a **per-worker** cap, so the fleet ceiling is 100 × tasks; whether that sits above the account's Nova Sonic bidirectional-stream quota is **unverified** — read it from Service Quotas rather than from here | Rate-limit or tag by exception name; the underlying signal belongs on a CloudWatch Bedrock-quota metric, not an issue tracker | **SDK / code**. [NOT WRITTEN] |
| **ChunkLoadError** after every deploy (browser — only if `WEB_SENTRY_DSN` is set) | Every route is `loadComponent`, and `.github/workflows/deploy.yml:221` runs `aws s3 sync . --delete`, which removes the previous build's hashed chunks the moment the new build lands. Volume = open tabs at deploy time × navigations, **and it fires again on every deploy**. This is the predicted top browser issue by count | Sentry ships a **built-in ChunkLoadErrors inbound filter** — console-only, no deploy, free. Turn it on first. Then, separately, a reload-on-chunk-error handler in the app, because the filter hides the report and the student's tab is still broken | **inbound filter** [ADMIN — NOT YET APPLIED] **+ code** [NOT WRITTEN] (`apps/web/src/main.ts:18-26`) |
| Unhandled rejection on sign-out (`apps/web/src/app/layout/app-shell.component.ts:107-110`) | Low. It may reach the SDK twice — once through `provideBrowserGlobalErrorListeners()` (`app/app.config.ts:9`) → `createErrorHandler()`, once through the default `globalHandlersIntegration` — but `dedupeIntegration` is also a default (`@sentry/browser/build/npm/cjs/prod/sdk.js`) and drops an event identical to the **immediately preceding** one, which is exactly where the second capture lands. Assume one event and let the issue's event count settle it | `try/finally` and navigate regardless. It is a real bug either way: `auth.logout()` rejecting leaves the cookie cleared and the SPA sitting on the page | **SDK / code**. [NOT WRITTEN] |
| `ResizeObserver loop limit exceeded` on `/admin/analytics` (`features/admin/analytics/analytics.component.ts:316-321` resizes ECharts synchronously inside the observer) | Legacy Chrome/Firefox only. The browser SDK's `DEFAULT_IGNORE_ERRORS` filters `/^ResizeObserver loop completed with undelivered notifications.$/` but **not** the older `loop limit exceeded` message — read out of the installed `@sentry/core/build/cjs/integrations/eventFilters.js:13` | Use the pattern already in the tree one file away: `apps/web/src/app/shared/voice-visualizer.ts:950-954` sets a flag and resizes on the next frame | **SDK / code**. [NOT WRITTEN] |

Two spans-only line items that are not "noise" but are bill:

- **The interview socket opens two transactions per connection.** The ASGI
  integration captures `ty == "websocket"` unconditionally, bypassing
  `http_methods_to_capture` (`sentry_sdk/integrations/asgi.py:269-273`, and
  again at `:300-305` on the non-span-streaming path), and `interview.py:1032`
  starts a second one inside it. The premise in `app/tracing.py:12-15` — *"to
  the HTTP integration it is a single upgrade request that never returns"* —
  **is not true of sentry-sdk 2.68.1**. That is a stale comment. But it does
  **not** double the cost: the engine's child spans attach to the *inner*
  transaction, because the SDK carries the active transaction in a context
  variable, so the outer one is essentially one extra root span per interview.
  Worth removing for the sake of a readable trace, not for the invoice.
- **~120–200 spans ride on each interview transaction**, against the SDK's
  `max_spans` default of 1000 (`sentry_sdk/scope.py:1220`, an `_experiments`
  option; the recorder actually keeps `maxlen - 1`, `tracing.py:207-213`):
  four statements per turn over 20–40 turns, plus a heartbeat write every 60 s
  (`_HEARTBEAT_WRITE_INTERVAL_S`, `interview_nova.py:185`), plus `_open_records`
  and the finalizer commits. That is the correct amount of detail for an
  8-minute call — it is only worth knowing because spans are the unit the meter
  counts.

### Sampling: what actually moves the bill

**Tracing is metered in SPANS, not transactions, and 5M spans a month are
included on every plan including the free one.** Transactions survive as a UI
grouping and as a legacy billing mode some older accounts are still on; the
quota page is now titled *[Manage Your Span
Quota](https://docs.sentry.io/product/accounts/quotas/manage-transaction-quota/)*.
Anything that budgets REEP's tracing in transactions is counting the wrong
thing.

**Dynamic Sampling does not reduce your bill and does not replace
`traces_sample_rate`.** Sentry's own wording: *"metering is based on
received, not stored events"*
([docs](https://docs.sentry.io/organization/dynamic-sampling/)). Dynamic
sampling is a server-side retention priority deciding what you can still browse
at volume. The SDK-side rate is the only lever that changes the invoice, and
REEP has it set flat at 0.2 on both halves (`settings.sentry_traces_rate`,
`app/config.py:337-342`, and a hardcoded `0.2` at `apps/web/src/main.ts:24`).

**A flat rate is the wrong shape here, and this section is proposing to
change that decision.** 0.2 treats an 8-minute interview WebSocket and a 5 ms
`SELECT 1` probe as equally worth keeping. Do the arithmetic both ways: the
health check at 0.2 and minimum scale costs roughly **280k–420k spans a
month**; every interview at **full fidelity**, at 30 interviews a day
*(assumed; no source in the repo)* and 160 spans each, costs about **144k**.
The probe at one fifth costs about twice what the whole mock interviewer costs
at 1.0 — while discarding four out of five traces of the path `app/tracing.py`
was written for.

```python
# apps/api-py/app/main.py — define this ABOVE the `sentry_sdk.init(...)` call
# inside the `if settings.sentry_dsn.strip():` block at line 59, and pass it as
# traces_sampler= in place of traces_sample_rate=.
#
# The sampling context is real in 2.68.1: sentry_sdk/scope.py:1182 and
# tracing_utils.py:1608 both build {"transaction_context": ..., "parent_sampled":
# ...}, and the ASGI integration merges {"asgi_scope": scope} into it
# (asgi.py:276, :287, :326) — so .get() it; a non-HTTP transaction has no key.
#
# KEY ON THE PATH, NOT ONLY THE NAME. The OUTER websocket transaction is named
# by the FastAPI integration's transaction_style; only the INNER transaction
# started at interview.py:1032 is called "interview <key>". Matching the name
# alone would leave the outer one at 0.2 and split the interview's trace.
_FULL_FIDELITY_PATHS = ("/api/interview", "/ws/media-bridge", "/api/platform/media-bridge")


def _traces_sampler(ctx: dict) -> float | bool:
    path = (ctx.get("asgi_scope") or {}).get("path", "")
    if path in ("/health", "/ready"):
        return 0.0                      # never sent, never metered
    if path in _FULL_FIDELITY_PATHS:
        return 1.0                      # the reason the instrumentation exists
    name = (ctx.get("transaction_context") or {}).get("name") or ""
    if name.startswith(("interview ", "platform.")):
        return 1.0                      # the inner transaction, same trace
    if ctx.get("parent_sampled") is not None:
        return ctx["parent_sampled"]    # keep a browser-started trace whole
    return settings.sentry_traces_rate  # everything else, still the env knob
```

Two caveats on that snippet. It does **not** remove the need for the built-in
health-check inbound filter: the filter needs no deploy and works today,
where this needs a release. And whether dropping a health-check transaction
at ingest also drops its child SQL span is **not something the docs answer** —
neither [the filtering
page](https://docs.sentry.io/concepts/data-management/filtering/) nor the span
quota page says. Treat the filter as certain relief for transactions and
probable relief for their spans, and verify in the Inbound Data Filters graph
after turning it on.

**`SENTRY_TRACES_SAMPLE_RATE` is read by `app/config.py:337` and set nowhere
in the repository** — not in `.env.example`, not in
`infra/cdk/reep_core/stack.py`'s `api_environment` (nine keys at
`stack.py:282-292`, plus a conditional `SES_FROM_ADDRESS` at `:897`). Note what
that phrasing does *not* mean: `SENTRY_DSN` **is** wired, as an ECS secret at
`stack.py:933`. It is the sample rate specifically that has no writer, so 0.2
is the effective production value and the only way to change it is a
CloudFormation deploy of the task definition — and per `AGENTS.md`,
`deploy.yml` ships code, never infrastructure. Adding one key to
`api_environment` turns "we are burning quota" from a task-definition
revision and a service roll into an environment change. [NOT WRITTEN]

### Which categories REEP actually meters

Ten billable units appear on the pricing page. REEP sends **two**, and knowing
that is what makes the estimate tractable. Volumes below are from
<https://sentry.io/pricing/> and <https://docs.sentry.io/pricing/>, read
2026-09-08.

| category | unit | included / month | REEP sends it? |
| --- | --- | --- | --- |
| Errors | events | 5k Developer / 50k Team and Business | **yes** |
| Tracing | **spans** (not transactions) | 5M, identical on all three tiers | **yes** |
| Session Replay | replays | 50, identical on all three | no — `replayIntegration` is not passed, and it is **not** in `getDefaultIntegrations()` (verified against the installed `@sentry/browser/build/npm/cjs/prod/sdk.js`, which lists eleven and not Replay) |
| Logs | GB | 5GB | no — Sentry-side logging is not enabled |
| Application metrics | GB | 5GB | no |
| Attachments | GB | 1GB | no |
| Cron monitors | monitors | 1 (more requires attaching a PAYG budget) | no — nothing calls `capture_checkin` anywhere (grep is empty) |
| Uptime monitors | monitors | 1 (same) | no |
| Size analysis builds | builds | 100 | no |
| Profile hours (UI and Continuous are priced separately) | hours | **none — PAYG-only on every tier** | no — no `profiles_sample_rate` in the tree |

The consequence for a plan decision: **Team and Business include identical
volumes of everything, errors included — 50k on both**. Business buys features
and retention. It does not buy more room.

### The plan, the prices, and the recommendation

**Prices read 2026-09-08 from <https://sentry.io/pricing/> and
<https://docs.sentry.io/pricing/>. Re-read them before you budget; a stale
price in a document is worse than a link.**

| plan | headline | billing period | errors | error retention | span retention |
| --- | --- | --- | --- | --- | --- |
| Developer | $0 | — | 5k | 30 days | 30 days |
| Team | $26/mo | **annual only** — "When billed annually with default pre-paid data" | 50k | 90 days | **30 days** |
| Business | $80/mo | **annual**, same qualifier | 50k | 90 days | 30 days full + 13 months sampled |
| Enterprise | custom | — | — | 90 days | 30 days + 13 months sampled |

**The month-to-month prices for Team and Business are UNVERIFIED and are not
quoted here.** The pricing page renders the annual figures in static HTML and
computes the monthly ones client-side; no docs page states them. Both
qualifiers above are Sentry's own words. Assume monthly is higher and get the
number from the billing screen, not from this table.

Two retention facts that contradict what most write-ups say, and both matter
to REEP — all of the following is from [Data Retention
Periods](https://docs.sentry.io/security-legal-pii/security/data-retention-periods/):
**spans get 30 days on Developer *and* Team** (the 90-day figure on Team and
Business applies to errors, replays and attachments; on Developer those three
are 30 days as well) — and retention is fixed at ingest from the then-current
plan, so upgrading does not extend data already stored.

**Buy Team, and buy it for the seat cap and the safety net, not for the
volume.** With the noise above fixed, REEP's steady state is comfortably
inside Developer's free 5M spans and plausibly inside its 5k errors. Volume
is not the argument. These two are — and note this supersedes
`docs/aws-deployment.md:296` ("Sentry's developer tier is free and sufficient
to start"), which is true about volume and silent about both of these:

1. **Developer is capped at one user** — "Limited to one user" on the pricing
   page. REEP already has an operator and a developer. (The Sentry MCP
   connector documented in `docs/aws-deployment.md` §6 is *not* a third seat —
   it authorises as whichever member connects it.) One seat is the constraint
   that bites first.
2. **Developer cannot attach a pay-as-you-go budget at all** — PAYG is a paid-plan
   feature. Exceeding a free quota drops data until the cycle resets; you go
   blind, silently, for the rest of the month. Note the shape of REEP's largest
   predicted issue: the `sso/status` error can burn 5k in under a week without a
   single thing being wrong with the application.

90-day error retention is a third, smaller reason — it spans a semester's worth
of "has this recurred", where 30 days does not survive an exam break. It is not
worth the price on its own.

**Do not buy Business.** The only thing REEP would use it for is custom
inbound filters — dropping a specific noisy error message server-side, which
is Trial/Business/Enterprise only — and REEP owns every one of those call sites
and can fix them in code, which is the better fix anyway. Business's per-unit
overage rates are higher than Team's, but by how much is **unverified here** —
read it off the billing screen if it matters. SAML2 is not a college project's
problem.

### Quota mechanics, and the three that surprise people

**Reserved volume and the PAYG budget are different things and the budget is
shared.** Reserved volume is prepaid, discounted, and **expires monthly** —
*"any unused reserved volume will expire at the end of each billing month"*.
The pay-as-you-go budget is *"shared among all categories on a first-come,
first-served basis"*, which is the trap: on a plan where you have enabled
Replay or Logs later, a spike in either can eat the budget that was meant for
errors. When both reserved and PAYG are exhausted, *"any data sent... will be
dropped and you won't be charged for it"*.

**Spike Protection is already ON — check it, then turn on its notifications.**
Sentry's docs say *"All projects have Spike Protection enabled by default"*,
and it is configured **per project** now, not per org. What is **off** by
default is the alerting: *"Notifications for Spike Protection are turned off by
default to avoid excessive noise."* So the work is (a) confirm both projects
are toggled on in Settings → Spike Protection and (b) add email/Slack
Notification Actions, which is the half nobody has done. It covers errors,
transactions or spans, and attachments — exactly the two categories REEP sends.
The threshold is the larger of a floor and a usage-based figure: the floor is
itself the max of one tenth of the Developer plan's minimum reserved volume for
that category and `(3 × your quota) / (720 × number of projects)` with the
project count capped at 5; the usage-based figure is a 7-day weighted average
times a multiplier that is 5× the past week's standard deviation, bounded
between 3 and 6. Dropped-event contribution decays to ~10% weight 24 hours
after a spike, so one bad deploy does not train the ceiling upward.
Notifications: [ADMIN — NOT YET APPLIED]. Source:
<https://docs.sentry.io/pricing/quotas/spike-protection/>

**Marking an issue Ignored does not stop the meter.** Sentry's own quick
reference: a repeated event for a *previously resolved* issue counts, and a
repeated event for an issue *you've set to Ignore* counts. Only **Delete &
Discard** stops it. That is a triage habit, not a setting: if a whole issue
class is decided noise and the code fix has not shipped yet, Delete & Discard
it — Ignore just mutes your own notification while the bill keeps running.

**Everything else that drops an event is free.** From the same table, none of
these count: spike protection at threshold, quota already exceeded, a project
rate limit (errors and attachments), Delete & Discard repeats, inbound filters,
SDK sample rate, SDK filters, SDK configuration, size limits.

Sentry's ordered playbook, easiest to hardest, is nine steps and this is the
list verbatim ([Billing Quota
Management](https://docs.sentry.io/pricing/quotas/)):

1. Ensure spike protection is enabled
2. Adjust your quota
3. Rate limit your events or attachments
4. Review repeated events
5. Filter your events
6. Update your SDK sample rate
7. Apply SDK filtering (`beforeSend` and `beforeSendTransaction`)
8. Update your SDK configuration
9. Manage data size

Note where the line falls: **the first five need no deploy and the last four
do.** When the bill spikes mid-semester, work that order. (The error-specific
[Manage Your Error Quota](https://docs.sentry.io/pricing/quotas/manage-event-stream-guide/)
guide opens with two "review usage" steps and puts inbound filters ahead of rate
limiting — a slightly different order for a narrower question. The nine above
are the org-wide list.)

Per-key rate limits are worth knowing about because they are the one control
that is per-DSN rather than per-project: you can hold several client keys on
one project, give each its own `{"window": 60, "count": N}`, and rate-limited
errors do not count toward quota. Sentry's wording is *"the maximum volume of
**error events** a project key will accept"* — spans, replays and logs are not
covered — so they are a blast-radius cap on the error side, not a tracing
control.

### Seer: not now

**$40 per active contributor per month**, where *"an active contributor is
defined as any user who makes 2 or more PRs to a Seer-Enabled repository"*,
counted once across repos, reset monthly. *"Seer usage is billed separately as
its own monthly charge and does not count against your PAYG budget"*, and it is
an add-on to an existing Team or Business plan. (Whether the pre-2026
$20/month-plus-credits model is fully retired is **unverified** — the current
page describes only the per-contributor model; check
<https://docs.sentry.io/pricing/> before quoting the old one either way.)

**The verdict for REEP is no, and the reason is arithmetic before it is
judgement.** One active contributor is $40/month, which is more than the Team
plan Seer would sit on top of; two is $80. Set against that: REEP's error
surface is small, every failure path in the interview and agent stacks
already carries a comment explaining what it means, and the current top issue
by predicted count is a config state that fires once per login-page render.
Buying an AI debugger before fixing the noise floor spends it on
`GET /api/auth/sso/status`. Revisit after the table above is worked through
and the issue stream is honest — Seer's Code Review on pull requests is the
half most likely to earn $40 here, and it is worth nothing until the
issues it reads from are real.
---

## Rollout and verification

**Phase 1 is not "install the SDK".** Both SDKs are installed, initialised and
carrying REEP-specific reasoning: `sentry-sdk[fastapi]==2.68.1` initialises at
import time in `apps/api-py/app/main.py` with the three rule-1 flags
(`send_default_pii=False`, `include_local_variables=False`,
`max_request_body_size="never"`), and `@sentry/angular` 10.71.0 is
dynamic-imported in `apps/web/src/main.ts` only when a DSN is baked in. Nothing
below re-does that work, and nothing below relaxes a flag those files argue for.
What follows fills the holes the audit found, ordered by what a hole costs
against what closing it costs.

Every phase here is **[NOT WRITTEN]** in `docs/deployment-process.md`'s sense —
described here, implemented nowhere. The Sentry-console half of each is
**[ADMIN — NOT YET APPLIED]** *stretched to a second console*: that marker is
defined at `docs/deployment-process.md` lines 30–32 as a **GitHub** setting only
the repository owner can apply by hand, and Sentry's console is not GitHub. The
property that makes the marker fit is the one that matters — no committed file
can create a project, an alert rule, a cron monitor or an uptime check — but say
you are extending it, because quietly widening a marker's meaning is precisely
the failure that document's own `[ON MERGE]` post-mortem is about.

Nothing about the Sentry side can be verified from this tree: no org slug,
project slug, plan or DSN is committed anywhere. Two things that plan-shaped
worry usually turns on *can* be settled without it — see phases 3 and 4.

### The order, and why it is this order

1. **Release and environment first**, because they cost two build-time strings
   and they are what every later phase is keyed on. Neither SDK sets a
   `release` today (nothing in the repo sets `SENTRY_RELEASE` or passes
   `release=`), and the two halves disagree about what environment they are in:
   the API sends **`prod`** — `sentry_sdk.init(environment=settings.env…)` at
   `app/main.py:64`, with `api_environment["ENV"] = "prod"` at
   `infra/cdk/reep_core/stack.py:283` — while the SPA sends `development` from
   production, because `environment.production` is `false` in the only
   environments file there is. So no single environment filter covers this
   deployment, and `docs/deployment-process.md`'s rollback trigger, "a new
   Sentry issue type first seen after this deploy", can only be approximated by
   wall-clock today.
2. **Source maps second.** The production Angular config emits none, and the
   build runs `outputHashing: "all"` — so every browser stack trace Sentry has
   ever received is single-letter frames against hashed chunk names. Half the
   pane of glass is currently decorative.
3. **The retention cron monitor third**, because it is the only place where
   Sentry catches something no existing control catches at all.
4. **Uptime fourth**, because `reep-no-healthy-api` already covers most of it.
5. **Sampling and the last-line scrubber last**, because both need a month of
   honest volume data to size, and on this stack the API's sample rate cannot
   be changed from a browser at all (see below).

Phases 3 and 4 ask for exactly one cron monitor and one uptime monitor, and
[every Sentry plan includes one of each](https://docs.sentry.io/pricing/) — the
free Developer plan included; additional ones are PAYG only ($0.78 and $1.00 per
monitor). So neither phase is blocked on a plan question this repository cannot
answer.

### Phase 1 — One release string, one environment name, both halves

`${{ github.sha }}` is already the deploy identity: it tags the image and it is
printed in both success lines (`.github/workflows/deploy.yml`). It is simply
never handed to either SDK. A bare 40-hex SHA is one of the two release names
[Sentry recommends](https://docs.sentry.io/product/releases/naming-releases/)
and is legal — the forbidden characters are newlines, tabs, `/` and `\`, and the
limit is exactly 200 characters. It is **not** semver, and Sentry detects semver
from a project's recent releases and otherwise orders by date, which is the
right behaviour for a continuously-deployed service and is why finalizing after
a successful roll matters more than it looks.

**The API needs no code change for the release.** `sentry-sdk` 2.68.1 fills a
missing `release` from `get_default_release()`, whose first source is the
`SENTRY_RELEASE` environment variable (`sentry_sdk/utils.py:150`, applied at
`sentry_sdk/client.py:326`). Its second source is `get_git_revision()`, and the
image has no `.git` — not merely absent from the image but absent from the build
*context*, since `docker build … apps/api-py` roots the context two levels below
the repository's `.git`, so that source can never fire. Bake it in, after the
last `RUN` so a new SHA invalidates only a metadata layer rather than the
`groupadd`/`chown` one:

```dockerfile
# apps/api-py/Dockerfile — after the last RUN, immediately before EXPOSE/CMD.
# Blank is safe: get_default_release tests `if release:`, so an empty value
# falls through instead of stamping every event with an empty release.
ARG GIT_SHA=""
ENV SENTRY_RELEASE=$GIT_SHA
```

```bash
# .github/workflows/deploy.yml, in "Build and push the image"
docker build --build-arg GIT_SHA=${{ github.sha }} \
  -t "$IMAGE:${{ github.sha }}" -t "$IMAGE:latest" apps/api-py
```

**Build arg, not task definition, and that is not a style preference.**
`SENTRY_RELEASE` in the CDK's `api_environment` would make every release a
`cdk deploy` of `reep-core` — and `cdk-deploy.yml`'s dropdown offers
`voice-platform`, `edge-waf` and `dr-vault` only, so that deploy is not
reachable from the browser at all. `deploy.yml` ships code, never
infrastructure; the release belongs where the code is. It also comes free to
the nightly retention task and every `ops-task.yml` one-off, because all three
run the `reep-api` task definition, which references `:latest`
(`stack.py:538-544` says so in as many words).

**The SPA needs two fields and one deletion, and the environment value is
`prod`, not `production`.** `environment.production` has exactly one reader in
the whole app — `main.ts:20`, the Sentry environment tag itself — so adding a
second environment file and `fileReplacements` would be a build-config change to
solve a one-line problem, and would leave `production: false` sitting in the
tree still lying to the next reader. Delete the derivation, add
`sentryEnvironment: ''` and `sentryRelease: ''` beside `sentryDsn`, read them in
`main.ts`, and extend the existing "Point the SPA at Sentry" step, which already
has the right shape:

```bash
sed -i "s|sentryEnvironment: ''|sentryEnvironment: 'prod'|" src/environments/environment.ts
sed -i "s|sentryRelease: ''|sentryRelease: '$GITHUB_SHA'|"  src/environments/environment.ts
grep -q "sentryEnvironment: 'prod'"              src/environments/environment.ts
grep -q "sentryRelease: '[0-9a-f]\{40\}'"        src/environments/environment.ts
```

**`prod`, because the API's name is not movable and this one is.** The API's
environment tag is `settings.env`, and `ENV=prod` in `api_environment` also
drives `is_prod` (`config.py:1063`, `_PROD_ENV_NAMES`), the production boot
guard, `password_login_allowed` and `app.seed`'s ENV=prod refusal — it is not a
label, it is a switch, and moving it needs a `cdk deploy reep-core` the deploy
button cannot run. The SPA's name is a build-time string with no other reader.
Get this backwards once and you own it forever:
[Sentry environments cannot be deleted](https://docs.sentry.io/concepts/key-terms/environments/),
only hidden, so `production` and `prod` would sit side by side in every filter
dropdown from then on.

Add the resolved release to the boot line so the API states its own identity —
`log.info("Sentry initialised (release=%s, environment=%s, traces_sample_rate=%s)", ...)`,
read back with `sentry_sdk.get_client().options["release"]`.

**Verification.**

```bash
# 1. the image knows its own commit
docker run --rm --entrypoint printenv "$IMAGE:$(git rev-parse HEAD)" SENTRY_RELEASE

# 2. the running task says so, in /reep/api
aws logs filter-log-events --log-group-name /reep/api \
  --filter-pattern "Sentry initialised" --limit 5
```

Then in Sentry: **Releases** lists the SHA, and the release row names **both**
projects. If only the API appears, the web half's step exited 0 on a blank
`WEB_SENTRY_DSN` — which also skips the two new substitutions, so a DSN-less
build silently ships without release or environment too. Then check the
environment dropdown on both projects reads `prod` and nothing else.

### Phase 2 — Source maps: hidden, uploaded, then destroyed before the sync

Add to `apps/web/angular.json`'s `production` configuration:

```json
"sourceMap": { "scripts": true, "styles": false, "hidden": true, "vendor": true }
```

`hidden: true` emits the maps but suppresses the `//# sourceMappingURL=`
comment, so nothing in the shipped bundle points at a file the browser could
fetch — for scripts and styles both (`@angular/build`'s
`application-code-bundle.js:445` and `global-styles.js:38` each translate it to
esbuild's `'external'`). `styles: false` because Sentry has no use for a CSS map
and `sentry-cli`'s default extension list would upload every one of them.

Matching still works, because **Angular 22 injects TC39 debug IDs itself**:
`execute-post-bundle.js:47` calls `injectDebugIds(outputFiles)` gated only on
`sourcemapOptions.scripts`, writing `//# debugId=<uuid>` into each browser `.js`
and a top-level `"debugId"` into its map, derived UUIDv5 from the map bytes.
Verified on this tree by building with source maps: `main-<hash>.js` ends
`//# debugId=1746ea45-310a-5e94-be44-82ad24a44f42` on the line above
`//# sourceMappingURL=`, and the map carries the same id under `"debugId"`.
[sentry-cli 3.0.0](https://github.com/getsentry/sentry-cli/releases/tag/3.0.0)
moved to exactly that spelling per the TC39 proposal and still reads the legacy
`debug_id`, so the two sides agree on the field name. Debug IDs are what Sentry
matches on; the release is organisational. **Unverified:** the ingestion round
trip against a live project — no org, project or DSN is committed here, so the
symbolicated-frame check below is the arbiter, not the field names.

**Do not run `sentry-cli sourcemaps inject`.** Angular has already done it, and
it did so inside the build, before `generateIndexHtml` reads the bytes — that is
the Angular source's own stated reason for the call site: "before any consumer
reads the bytes (in particular `generateIndexHtml` below, which computes SRI
hashes from the on-disk content)". A post-build inject rewrites the emitted
bundles. Today that is merely redundant (`subresourceIntegrity` defaults to
`false` in the application builder's schema and is unset here); the day someone
turns SRI on, a post-build rewrite invalidates the hash in `index.html` and the
browser refuses to run the app — a blank dashboard shipped by an observability
step.

A new step between "Build" and "Publish", and the destruction is unconditional:

```yaml
- name: Ship the source maps to Sentry, then destroy them
  env:
    SENTRY_AUTH_TOKEN: ${{ secrets.SENTRY_AUTH_TOKEN }}   # an ORG auth token
    SENTRY_ORG: ${{ vars.SENTRY_ORG || 'reep' }}
    SENTRY_PROJECT: ${{ vars.SENTRY_PROJECT_WEB || 'reep-web' }}
  working-directory: apps/web/dist/web/browser
  run: |
    if [ -z "$SENTRY_AUTH_TOKEN" ]; then
      echo "no SENTRY_AUTH_TOKEN; maps not uploaded"
    else
      # --strict: an angular.json edit that stops emitting maps fails HERE,
      # rather than uploading nothing and looking green.
      npx --yes @sentry/cli@3 sourcemaps upload --release "$GITHUB_SHA" --strict .
    fi
    # Unconditional, and outside the if. sourcesContent defaults to true, so
    # every .map embeds the original TypeScript verbatim — a .map that survives
    # this line is synced to S3 in the next step under a one-year immutable
    # cache header and served to anyone who asks.
    find . -name '*.map' -delete
```

`--strict` is documented as "fail with a non-zero exit code if there are no
sourcemaps to upload" ([docs.sentry.io/cli/releases](https://docs.sentry.io/cli/releases/)).
`SENTRY_AUTH_TOKEN` is the second repository secret this repo will hold —
`WEB_SENTRY_DSN` is currently the only one — and it must be an **Organization
Auth Token**, not a personal one, so it does not die with an account.
`sentry-cli` 3.x is auth-token-only: 3.0.0 "removed support for the legacy API
key authentication method", and every `sentry-cli files …` and
`sentry-cli releases files …` subcommand is gone, so any older recipe you find
will fail with an unknown-subcommand error. `releases finalize` survives. Note
also that 3.x officially supports Sentry SaaS and self-hosted ≥ 25.11.1 only —
if this org ever runs an older self-hosted instance, that pin has to move.
`@sentry/cli`'s current `latest` is 3.7.0, so `@3` resolves today.

`sourcemaps upload --release` creates the release implicitly; **[NOT WRITTEN]**
is `sentry-cli releases finalize "$GITHUB_SHA"` after the roll succeeds, which
is what makes a release's window mean "this deploy" rather than "since first
event".

**Delete the maps; do not add `--exclude "*.map"` to the sync.** The sync runs
with `--delete`, and the filter applies to the destination too — [files excluded
by filters are excluded from deletion](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html),
so a `.map` that had already reached the bucket would be protected from cleanup
and become permanent. Deleting them from `dist` before the sync leaves
`--delete` free to clean up anything historical.

**Verification.**

```bash
# the bundle and its map carry the SAME id
grep -o '//# debugId=[0-9a-f-]*'   apps/web/dist/web/browser/main-*.js
grep -o '"debugId":"[0-9a-f-]*"'   apps/web/dist/web/browser/main-*.js.map

# and nothing with a .map extension is public
aws s3 ls "s3://$WEB_BUCKET" --recursive | grep '\.map$'   # expect: no output
curl -sS -o /dev/null -w '%{http_code}\n' "https://<domain>/main-<hash>.js.map"
```

In Sentry, **Project Settings → Source Maps** shows an artifact bundle for this
release (menu naming is from the current console and is *unverified* against
this org's UI). The arbiter is neither of those: throw a real error from a real
screen and confirm the top frame names a `.ts` file, a real function and a line
number. If it is still minified while the bundle is listed, the debug-ID
handshake did not take — only then add a `sentry-cli sourcemaps inject .`
before the upload, and re-check that SRI is still off.

### Phase 3 — A dead-man's switch on the one job nothing watches

`reep-retention-daily` fires `cron(30 21 * * ? *)` (`stack.py:1089-1112`) and
runs `python -m app.retention_job` — the job that keeps the 90-day conversation
and 180-day interview promises and destroys named students' stored recordings.
**No alarm, no SNS action, no check-in of any kind watches it**: every alarm in
the stack's observability block is keyed on the ALB, RDS, ECS CPU or the
dropped-turn metric filter, and none of them can see a schedule. An EventBridge
Scheduler ECS target does not surface a non-zero container exit as a schedule
failure, so a schedule that stops firing, an image it cannot pull, and a run
that exits 1 are all equally silent. A Sentry cron monitor fails on the
*absence* of a check-in, which is exactly the shape a missing alarm cannot
express.

**This is a code change, not a console change, and here is why.**
`app/retention_job.py` imports `logging`, `datetime`, `.retention` and `.db` —
it never imports `app.main`, so `sentry_sdk.init` never runs in that process
and every `sentry_sdk` call in it is currently a no-op. The job has to
initialise the SDK itself.

That creates a second `sentry_sdk.init(` site, and
`test_sentry_never_ships_local_variables_or_request_bodies`
(`tests/test_codebase_guards.py:600`) reads `app/main.py` **as text**. A second
init would be entirely outside the guard that exists to stop
`include_local_variables` shipping a student's transcript. So phase 3 includes
widening that test to walk every module containing `sentry_sdk.init(` rather
than only `app/main.py`. Skip that and this phase opens the exact door that test
was written to close.

**The check-in status must not be the exit code.** `main()` returns 0 while
logging an ERROR when `summary["interviews_hard_delete_blocked"]` is non-zero —
a run where a named student's recording could not be destroyed and its row was
deliberately held back. A monitor keyed on the exception alone reports green on
precisely the day retention did not complete. Report `error` when the sweep
raised **and** when that counter is non-zero.

The API is not top-level in this SDK version — `import sentry_sdk` exposes
`monitor` only:

```python
from sentry_sdk.crons import MonitorStatus, capture_checkin
```

and the statuses are `MonitorStatus.IN_PROGRESS` / `.OK` / `.ERROR`. The
schedule travels with the check-in as `monitor_config`, whose fields are
`schedule`, `timezone`, `checkin_margin` and `max_runtime`
(`sentry_sdk/_types.py:455`, and
[docs.sentry.io/product/crons/getting-started/http](https://docs.sentry.io/product/crons/getting-started/http/)).
EventBridge's `cron(30 21 * * ? *)` is UTC with the AWS six-field `?` quirk, so
Sentry's five-field crontab is:

```python
monitor_config = {
    "schedule": {"type": "crontab", "value": "30 21 * * *"},
    "timezone": "UTC",
    "checkin_margin": 15,   # minutes Sentry waits before calling it missed
    "max_runtime": 30,      # minutes before a started run is called failed
}
```

`checkin_margin` has to clear a Fargate cold start plus the image pull;
`max_runtime` has to clear the sweep itself, which on a long-neglected backlog
does the whole thing in one transaction (`retention_job.py` says so).

**Verification.** Locally, with a DSN set:
`cd apps/api-py && SENTRY_DSN=... .venv/Scripts/python -m app.retention_job` —
Sentry **Crons** shows the monitor with one check-in. In production, the only
browser-reachable proof is waiting for 21:30 UTC: `app.retention_job` is not on
`ops-task.yml`'s fixed menu — that menu is `seed-roster-dry-run`, `seed-roster`,
`seed-kb`, `grant-access` — and adding it there is a separate decision, because
that menu is the list of things anyone who can press the button may run against
production data. Prove the red path from a shell with credentials instead, with
an `aws ecs run-task` whose `DATABASE_URL` override points nowhere, and confirm
the monitor goes red rather than quiet.

### Phase 4 — An uptime check that is actually checking the API

**Do not point an uptime monitor at `https://<domain>/health`.** CloudFront
forwards exactly one behaviour to the ALB, `/api/*`; `health.router` is mounted
bare (`app.include_router(health.router)` at `main.py:292`, "Health is infra
liveness — unprefixed at /health"); and the `reep-spa-fallback` CloudFront
Function rewrites any path whose last segment has no dot to `/index.html`
(`_SPA_FALLBACK_JS`, `stack.py:151`). So `/health` and `/ready` both return the
Angular shell from S3 with a 200 — a check that stays green while every API task
is dead.

The fix is one line — mount the same router a second time under the prefix
CloudFront actually forwards:

```python
app.include_router(health.router)                 # /health, /ready — the ALB probes these
app.include_router(health.router, prefix="/api")  # /api/health, /api/ready — reachable through the CDN
```

Leave the ALB target group alone: it probes the container directly on port 3300
at `/ready`, `healthy_http_codes="200"`, and must keep doing so.

**Know what this exposes.** `/ready` is not internet-reachable today — the ALB
security group admits 443 from the CloudFront prefix list once
`restrictToCloudfront` is on (`stack.py:404-413`), and CloudFront forwards only
`/api/*`. This mount makes readiness public. That is acceptable because the body
is deliberately terse — `{"status":"ok","checks":{"database":"ok"}}`, and on
failure an exception *type* name and never a message, a host or a URL
(`routers/health.py`) — and it must stay that way. The `/api/*` behaviour uses
the CACHE_DISABLED policy, so the check is never answered from cache; note also
that every poll runs a real `SELECT 1`.

**Verification.**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "https://<domain>/api/ready"  # 200
curl -sS "https://<domain>/api/ready"        # {"status":"ok","checks":{"database":"ok"}}
curl -sS "https://<domain>/ready" | head -c 20   # <!doctype html> — why the alias exists
```

Then a Sentry uptime monitor on `/api/ready`. [By default a check passes on any
2xx](https://docs.sentry.io/product/monitors-and-alerts/monitors/uptime-monitoring/);
interval is 1 minute to 1 hour, timeout up to 30 s, and an issue opens after
three consecutive failures. 2xx alone is already right for the 503 `/ready`
returns when Postgres is down, but add a **Verification** assertion on the
response body containing `"database":"ok"` — that is the assertion that stays
honest if the `/api` mount is ever lost and the SPA fallback starts answering
200 with `index.html` again, which is the exact failure this phase exists to
close.

It overlaps `reep-no-healthy-api` on purpose: that alarm sees the ALB's view of
the targets, an uptime check sees the student's — CloudFront, the certificate,
DNS and the WAF are all inside the second and outside the first. Know what it
does *not* assert: `/ready` checks one Postgres `SELECT 1` and nothing else, so
a wedged event loop, an unreachable Bedrock region or a missing SES transport
all report healthy.

### Phase 5 — Sampling and the last-line scrubber

These are owned by the sampling and PII sections above; what belongs here is
why they come last and what it costs to move them. `apps/api-py/.env.example`
documents **no `SENTRY_*` variable at all** — not `SENTRY_TRACES_SAMPLE_RATE`,
not even `SENTRY_DSN`, which reaches the task as an ECS *secret* from the
`reep/external` secret (`stack.py:933`) rather than through `api_environment`.
So the API's effective 0.2 comes from `Settings`' own default
(`config.py:337`) and **cannot be changed from the browser at all**: the rate is
not a secret, so it belongs in `api_environment`, and putting it there needs a
`cdk deploy reep-core`, which `cdk-deploy.yml` deliberately does not offer. Add
the variable during a phase that is already touching the core stack, then tune
the number once phases 1–4 have produced a month of honest volume.

### After every deploy

This overlaps `docs/deployment-process.md` §8.5's first thirty minutes and does
not repeat it. (One caveat while you are there: §8.5's alarm table cites
`infra/aws/observability.tf`, which no longer exists — the alarms are in
`infra/cdk/reep_core/stack.py` under `# --- observability ---`, same names.)
Five Sentry-specific checks:

1. **The release exists and names both projects.** Releases → the SHA. Only the
   API listed means the web job's Sentry step exited 0 on a blank secret.
2. **Search `firstRelease:<sha>`.** This is what makes §8.5's rollback trigger —
   "a new Sentry issue type first seen after this deploy" — exact. Today it can
   only be approximated with a wall-clock filter (`firstSeen:-30m`), which
   cannot separate two deploys inside one window, nor an issue that first
   appeared during the ten-minute drain.
3. **One browser error in this release, and look at the top frame.** A frame
   reading `main-<hash>.js:1:…` means the upload did nothing; check the
   release's artifact bundles before suspecting the SDK.
4. **The retention monitor's next check-in lands inside `checkin_margin`.** A
   deploy changes the image the 21:30 schedule runs.
5. **`Sentry initialised (release=…, environment=prod…)` appears in `/reep/api`
   for the new tasks.** Its absence is the only signal that `SENTRY_DSN` went
   blank.

### Once a month

1. **Quota.** It is org-level: every project draws from one pool, so a noisy
   project starves the others and splitting projects does not partition
   anything — [Spend Allocation](https://docs.sentry.io/pricing/quotas/spend-allocation/)
   is the one exception, and it is Enterprise-only. Read Stats and look at the
   *filtered* column first — [inbound filters run at ingest and filtered events
   do not consume quota](https://docs.sentry.io/concepts/data-management/filtering/),
   so a filter is a free settings change while a sample rate is a redeploy, and
   on the API a `cdk deploy` of the core stack.
2. **Alert noise: which rules fired, and who actually read them.** The address
   is not the open question it used to be — `infra/cdk/cdk.context.json` commits
   `alertEmail`, read at `stack.py:252`. What is open is whether anything is
   subscribed and whether that inbox is read: `alerts.add_subscription(...)` is
   guarded by `if alert_email and harden` because CloudFormation cannot adopt an
   SNS email subscription, so today's subscription is whatever survived the
   import from Terraform and the harden deploy has not run.
   `docs/deployment-process.md` already names the failure mode — an alarm paging
   an unread address is a tripwire with the bell cut off — and its own text
   about `var.alert_email` is stale, because `infra/aws/` no longer contains a
   single `.tf` file. Confirm with `aws sns list-subscriptions-by-topic`, and
   write down who reads it, or the whole playbook is a detection mechanism with
   no receiver. The Sentry notification target is the same question one layer up.
3. **Scrub rules, by running the guards, not by reading them.**
   `cd apps/api-py && .venv/Scripts/python -m pytest tests/test_tracing.py
   tests/test_codebase_guards.py -k sentry`. Then re-read every module
   containing `sentry_sdk.init(` —
   `grep -rn "sentry_sdk.init(" apps/api-py/app/` — and confirm the guard covers
   each one.
4. **Environments.** They auto-create on first event and **cannot be deleted,
   only hidden** — and hiding is a UI change only: events still sent to a hidden
   environment keep counting against quota. After phase 1 the list should be
   `prod` and nothing else, plus `dev`/`development` if someone has run a laptop
   or the SPA locally with a DSN set. A stray `production` means somebody
   re-typed the phase-1 string, and it is now permanent.
5. **Releases with no artifact bundle.** A month of those is a month of
   deploys where the upload step was skipped and nobody noticed, because a
   minified frame looks like a hard bug rather than a missing upload.

### Things that will bite you here specifically

**The bundle budget will not catch a source-map mistake, and does not need to.**
`generateBudgetStats` skips every output file not ending `.js` or `.css` before
producing a single stat, so a 4 MB `.map` contributes zero to the `initial`
budget (250 kB warning / 400 kB error). What *does* ride that budget is the SDK.
Measured on this tree with `npx ng build`: **initial total 179.09 kB raw**, and
`@sentry/angular` sitting in its own lazy chunk at **460.20 kB raw** because the
import is dynamic and gated on a non-blank DSN. Convert it to a static import
"to set the release earlier" and `initial` becomes roughly 639 kB against a
400 kB error ceiling — the build fails, in the CI job whose whole purpose is
failing on exactly that.

**The boot guard will never tell you Sentry is off.**
`Settings.production_boot_failures()` checks `AUTH_SECRET` and `DATABASE_URL`
and nothing else, and the documented bootstrap for the `reep/external` secret
seeds `"SENTRY_DSN":""` (`docs/deploy-from-chrome.md:102`). So the documented
setup path produces a production API with observability entirely off, booting
clean and serving students, and the only signal is the *absence* of one log
line — `app/main.py` has no `else` branch on the DSN check. **Do not add Sentry
to the boot guard** — refusing to serve students because telemetry is
unconfigured is the wrong trade, and that guard's docstring scopes it to
"configuration this process must not serve real people with". Announce it
instead, the way outbound mail already announces `NO TRANSPORT` in the first
screen of the log (`main.py:157`).

**Fire-and-forget writes are invisible to Sentry by design, and that is not a
bug to fix casually.** Interview turn writes are deliberately swallowed so a bad
write can never kill a live call; the cause is logged as `Dropped interview
turn` and the tripwire is the `interview-dropped-turns` CloudWatch metric
filter, not a Sentry issue. `tracing.capture()` was written for this and has
zero call sites — the only `capture` in `app/` outside `tracing.py` is
`interview_nova.py`'s unrelated audio `_capture`. If you wire it in at the
writer, understand the volume you are buying: one event per dropped turn, and a
Bedrock hiccup inside one interview is dozens. Give that project its own DSN key
with a minute-windowed rate limit before you find out on the quota page.

**The 8-minute socket outlives the things you expect it to fit inside.** One
interview is one transaction (`routers/interview.py:1032`), started deliberately
*after* the refusal path so a 4013 is not filed as a five-millisecond interview —
the comment three lines up says exactly that. A flat `traces_sample_rate=0.2`
therefore discards four of every five traces the instrumentation exists for,
while sampling in thousands of 5 ms GETs. And the drain that lets a live
interview survive a deploy is the target group's 600 s deregistration delay
(`DEREGISTRATION_DELAY_SECONDS`, pinned against `nova_sonic_connection_seconds`
by a test) — so an interview in flight during a deploy belongs to the **old**
release and is tagged with it. That is correct behaviour, and it is why a
release's error count keeps moving for ten minutes after the deploy reports
finished.

**`uvicorn --reload` on Windows will make you debug the wrong process.** After
editing the init block, kill port 3300 and restart; a stale worker holds the
*previous* `sentry_sdk.init`, so the DSN you just set is not the one running.
`AGENTS.md` already names this hazard, and it bites hardest here, because the
symptom of a stale init is silence — which is also the symptom of a wrong DSN, a
blocked egress and a typo in the environment name.

**The SPA's Sentry config is injected by `sed` against a literal source
string.** Reformat `environment.ts` — prettier, double quotes, a trailing
comment — and every substitution becomes a silent no-op. The `grep -q` lines
catch it, but only on a build where the secret is set; with no secret the step
`exit 0`s before any of them run. If you add fields in phase 1, add their greps
in the same commit.
