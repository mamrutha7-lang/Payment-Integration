# 08 · Tooling for Claude Code — MCP servers and traceability

Use these four MCP servers on every phase. They are configured once in the repo's `.mcp.json` (which already registers the Angular CLI MCP) and read by Claude Code at start. Secrets come from the environment, never from the file.

| Server | Use it for | Never for |
|---|---|---|
| **Context7** | current docs before using any library API: Angular 22, AG Grid, ECharts 6, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, boto3/Bedrock, Playwright | guessing an API from memory |
| **Postgres MCP** (Postgres MCP Pro, `postgres-mcp`) | inspecting the **dev** schema and data on `localhost:5433`, checking a migration + backfill did what it says, `EXPLAIN` on new list/scope queries, index advice for scoped filters | production (there is no prod route from a laptop; the dev URL is the only one in the config) |
| **FastAPI MCP** (`fastapi-mcp`) | calling the **dev** API's endpoints as tools during verification — read-only operations, signed in as a seeded role — so a screen's data can be checked against the endpoint without a browser | anything in `ENV=prod` (the mount only exists in dev) |
| **Sentry MCP** | traceability after deploy: issues by release, stack traces, request traces, the cron monitors; before a PR: confirm the change reports through the scrubbers | pasting a token into the repo |

## 1. `.mcp.json` (repo root — extend the existing file)

```json
{
  "mcpServers": {
    "angular-cli": { "command": "npx", "args": ["-y", "@angular/cli", "mcp"] },
    "context7": { "command": "npx", "args": ["-y", "@upstash/context7-mcp"] },
    "postgres": {
      "command": "postgres-mcp",
      "args": ["--access-mode=restricted"],
      "env": { "DATABASE_URI": "postgresql://reep:reep_dev_password@localhost:5433/reep_py" }
    },
    "reep-api": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:3300/mcp", "--header", "Cookie:${REEP_DEV_SESSION}"]
    },
    "sentry": {
      "url": "https://mcp.sentry.dev/mcp",
      "headers": { "Authorization": "Sentry-Bearer ${SENTRY_ACCESS_TOKEN}" }
    }
  }
}
```

- `postgres-mcp`: `pipx install postgres-mcp` (or `uv tool install postgres-mcp`). **Restricted mode** = read-only SQL with statement timeouts; switch to `--access-mode=unrestricted` only on a throwaway dev database when a task explicitly needs to run DDL from the tool (migrations are still written as Alembic files, never applied from the tool).
- `reep-api`: see §2 for the dev-only mount and how `REEP_DEV_SESSION` is minted.
- `sentry`: `SENTRY_ACCESS_TOKEN` is a Sentry auth token in the shell environment (`org:read`, `project:read`, `event:read`, `release` scopes). Org `bgs-college-of-engineering-and`, projects `reep-api`, `reep-web` (and the two job/worker projects). Alternative: `claude plugin marketplace add getsentry/sentry-mcp && claude plugin install sentry-mcp@sentry-mcp`.
- Context7 needs no key for normal use.

Add `.mcp.json` changes in Phase 0 (`chore(tooling): register MCP servers for the redesign`), and a `.env.example` line for the two variables.

## 2. FastAPI MCP — dev-only mount (part of Phase 0)

Add `fastapi-mcp` to `requirements-dev.txt` only (it must **not** be in `requirements.txt`, otherwise `check_api_imports` and the production image change). In `app/main.py`, after the routers are included:

```python
if settings.env_is_dev and settings.mcp_enabled:   # MCP_ENABLED=true in apps/api-py/.env, never in prod
    from fastapi_mcp import FastApiMCP           # lazy: dev dependency
    mcp = FastApiMCP(
        app,
        name="REEP dev API",
        include_tags=["student", "admin", "mentor", "interview-records", "governance"],  # read-side routers
        headers=["cookie"],                      # forward the reep_session cookie to the real handlers
    )
    mcp.mount_http(mount_path="/mcp")
```

Give the read-side endpoints `operation_id`s and tags as you touch them (Phase 2–4), and exclude write operations with `exclude_operations=[…]` — the tool is for reading what a screen shows, not for mutating data outside a test. Guard test: `tests/test_codebase_guards.py` asserts the mount is behind `env_is_dev` and that `fastapi_mcp` is absent from `requirements.txt`.

Session for the tool: a tiny dev helper `python -m app.dev_session --email admin@bgscet.ac.in` (refuses when `ENV` is not dev) prints `reep_session=<jwt>`; export it as `REEP_DEV_SESSION` before starting Claude Code. It mints the same 12-hour cookie `POST /api/auth/login` would, so Rule 2 and capabilities apply exactly as in the app.

## 3. How Claude Code must use them (working rules)

1. **Before writing code against a library** — Context7 `resolve-library-id` + `query-docs` for the exact call (AG Grid Theming API, ECharts multi-axis + legend emphasis, Angular signals/`@if`/`@for`, FastAPI dependencies, SQLAlchemy 2.0 `select()`, Alembic `op.*`, Pydantic v2 validators). Cite the doc in the PR when the API is non-obvious.
2. **Before and after every migration** — Postgres MCP: `\d`-style schema read of the touched tables, row counts before/after the backfill, a sample of backfilled rows, `EXPLAIN (ANALYZE)` on the new scoped list queries (`scope_filter`) and the interview summary/trend queries; propose indexes when the plan seq-scans a large table (`students`, `interview_sessions`, `redesign_audit_events`).
3. **While building a screen** — FastAPI MCP: call the endpoint the screen reads as the seeded role and compare the payload to the board's fields; for role/scope checks, mint a session for `mentor@bgscet.ac.in` and confirm the same call is narrowed or refused.
4. **After the owner deploys** — Sentry MCP: `find_releases` → the deploy's git sha; `search_issues` for `release:<sha>` on `reep-api` and `reep-web`; `get_issue_details` / `get_event_stacktrace` / `get_trace_details` for anything new; check the cron monitors (`reep-retention-daily`, and the new `reep-analytics-nightly`, `reep-alerts-nightly` once they exist). Report the findings in the phase summary; open a fix PR for regressions.
5. Never paste tokens, cookies or DSNs into files, commits, PR text or the audit log.

## 4. Traceability requirements (what the code must keep doing)

- Every request carries `X-Request-ID` (`traceability.RequestTraceMiddleware`) and one `reep.access` log line; new endpoints inherit this — do not add routers outside the app.
- Sentry stays **one init per process** (`observability.init_sentry`), PII off, all four scrub hooks; new fields that carry personal data (emails, USNs, phone, reasons on grants/leave) go through `telemetry_scrub.py` / `redaction.redact_pii` — extend the allowlist/regexes when you add a query parameter (`?q=`, `?college_id=` are fine; `?email=` is not).
- New background jobs (`app.analytics_job`, `app.alerts_job`) report as their own Sentry monitors with the `reep-scheduled-jobs` DSN, like `app.retention_job`.
- Releases: `deploy.yml` already tags `<git sha>` for `reep-api` and `reep-web`; every PR description names the migrations it ships so an issue on a release can be traced to its schema change.
- Audit trail: every write through `architecture_events.record_change` (actor, target, before/after) — the Audit log screen (B2.7) is the product-side view of the same traceability; keep the request id in the audit row's `session` field so a Sentry event and an audit row can be joined.
- Browser side: `apps/web/src/app/core/telemetry-scrub.ts` and `sentry-lazy.ts` — Session Replay stays unconstructed.
