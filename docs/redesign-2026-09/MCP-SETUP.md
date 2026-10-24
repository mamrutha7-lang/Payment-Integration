# MCP servers for this repository

`.mcp.json` at the repository root registers five servers. Secrets come from
the environment; nothing in that file is a credential.

| Server | What it is for | Needs |
|---|---|---|
| `angular-cli` | Angular workspace questions, best practices, examples | nothing |
| `context7` | current library documentation before calling an API you are not certain of | nothing |
| `postgres` | reading the **dev** schema and data, `EXPLAIN` on a new scoped query, checking a migration and its backfill did what it says | `pipx install postgres-mcp`, and Postgres up on 5433 |
| `reep-api` | calling this repository's **dev** API as a signed-in role, so a screen's data can be checked against the endpoint it reads | the API running with the dev MCP surface on, and a session header file |
| `sentry` | issues by release after a deploy, stack traces, traces, cron monitors | `SENTRY_ACCESS_TOKEN` in the shell |

## The dev API server

Two things have to be true before `reep-api` can connect.

**1. The API must be mounting the surface.** It is off by default and refuses
outside a development environment. In `apps/api-py/.env`:

```
ENV=dev
MCP_DEV_SURFACE=true
```

Then start the API as usual. It logs `Dev MCP mounted at /mcp with N
read-only tools` when the mount succeeds.

Only `GET` paths under `/api` become tools, derived from the app's own OpenAPI
schema. That is the mount's safety property: a write endpoint cannot be reached
by forgetting to exclude it.

**2. You must hand it a session.** The surface forwards your `Cookie` header
into the real handlers, so a tool call runs as whoever that cookie belongs to,
through the same `require_*` dependencies and with rule 2's narrowing applied.
Mint one and put it in a file:

```
mkdir -p ~/.reep
python -m app.dev_session --email admin@bgscet.ac.in \
  | sed 's/^/Cookie: /' > ~/.reep/dev-session-headers.txt
chmod 600 ~/.reep/dev-session-headers.txt
export REEP_DEV_SESSION_HEADERS=~/.reep/dev-session-headers.txt
```

A FILE, not a `--header` argument. An argument is visible to every other user
on the machine through the process list, and the value is a bearer token for a
whole account.

`app.dev_session` mints at the account's current `token_version` without
advancing it, so the cookie is valid alongside your browser session on the same
account. It dies the next time anybody signs in to that account anywhere, which
is REEP's one-device rule working correctly — re-run the command for another.

To check what a narrower role sees, mint one for `mentor@bgscet.ac.in` instead
and ask the same question. A mentor with no group sees nobody; that is the
answer the screen must show too.

## Sentry

```
export SENTRY_ACCESS_TOKEN=...   # org:read, project:read, event:read, release
```

Organisation `bgs-college-of-engineering-and`; projects `reep-api`, `reep-web`,
`reep-scheduled-jobs`, `reep-interview-worker`.

## Postgres

```
uv tool install --with 'mcp<2' postgres-mcp
docker compose up -d
```

**The `mcp<2` pin is required, and a plain install does not work.** `postgres-mcp`
declares its `mcp` dependency without an upper bound, so a resolver picks the
2.x line, which renamed `FastMCP` to `MCPServer`. The server then dies at
startup on `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` — before
it reads the database URI, so the error says nothing about Postgres.

This is the same failure `fastapi-mcp` has, for the same reason, and it is why
`requirements-dev.txt` pins `mcp==1.30.0` beside it. If a third MCP tool is
added here, check its floor before trusting its install line.

Restricted mode is read-only with a statement timeout. Migrations are written
as Alembic files and applied with `alembic upgrade head`, never from the tool.

## Checking it all works

Two probes in `apps/api-py/tools/` speak the protocol directly, so a broken
mount is a clear error rather than a silent absence:

```
python tools/mcp_probe.py --list                                  # this API's tools
python tools/mcp_probe.py --email student@bgscet.ac.in --call my_profile_api_student_profile_get
python tools/pg_mcp_probe.py --list                               # postgres-mcp's tools
python tools/pg_mcp_probe.py --sql "select count(*) from users"
```

The second form is the check worth running while building a screen: call the
endpoint the screen reads, as the seeded role, and compare the payload with the
board. To check a narrower role sees less, mint for `mentor@bgscet.ac.in` and
ask the same question.
