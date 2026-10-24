---
name: Bug report
about: Something in REEP behaves wrongly. Start with the process table - it settles most reports.
title: ""
labels: bug
assignees: ""
---

## Which processes were running?

<!--
  This section is first because it closes the most common report in this stack.
  "Voice is broken" is almost always the OPTIONAL FOURTH PROCESS simply not being
  started: AGENTS.md step 4 is a separate venv on a separate Python, and NOTHING IN
  THE UI SAYS A FOURTH PROCESS EXISTS. Tick what was actually running, not what you
  meant to start.
-->

- [ ] **Postgres** — `docker compose up -d` (container `reep-postgres`, host port **5433**, database `reep_py`)
- [ ] **API** — from `apps/api-py`: `.venv/Scripts/python -m uvicorn app.main:app --port 3300`
- [ ] **Web** — from `apps/web`: `npx ng serve` (port 4200, proxying `/api` to `http://localhost:3300`)
- [ ] **Interview engine** — *not a process.* It runs INSIDE the API and needs no extra venv.
      On the default `INTERVIEW_ENGINE=nova` it needs AWS credentials that can reach Bedrock
      and a `NOVA_SONIC_REGION` that serves Nova 2 Sonic. Unavailable means the socket closes **4001**.

Anything **not** ticked above: was it deliberate, or had you forgotten it?

## What the server says about itself

Four probes answer most of this report before anyone reads the rest. Paste the output.

```
curl -s http://127.0.0.1:3300/health
curl -s http://127.0.0.1:3300/api/voice/status
curl -s http://127.0.0.1:3300/api/interview/status
curl -s http://127.0.0.1:3300/api/auth/sso/status
```

```
<paste here>
```

`ENV` value in `apps/api-py/.env`: `______`

<!--
  ENV is load-bearing and its failures do not look like ENV failures.
  password_login_allowed is an ALLOWLIST of dev/CI names, so a blank or typo'd ENV
  hides the login form entirely and makes POST /api/auth/login answer 403 — which
  reads to a user as "my password is wrong". `python -m app.seed` refuses to run on
  ENV=prod. The boot guard fires only on prod, and when it does the process never
  binds a port at all.
-->

## What happened

**Expected:**

**Actual:**

**Steps to reproduce** (the exact clicks, or the exact `curl`):

1.
2.
3.

## Evidence

- **Which role were you signed in as?** STUDENT / MENTOR / DIRECTOR / ADMIN / ALUMNI.
  Rule 2 means the same URL answers differently per role, and "a mentor sees nothing"
  is the CORRECT behaviour for a MENTOR with no `Mentor` group.
- Browser console errors, if this is a web bug:
- API log lines around the failure (the whole traceback, not the last line):
- If a request failed: the method, the path, and the **status code**.

## If the voice call or the interview sounded fine but saved nothing

The worst failure mode in this stack is silent: the conversation is perfect in the room
and empty in the database, because transcript POSTs are deliberately fire-and-forget so
a bad write can never kill a live call. Run the runbook query and paste it:

```sql
select channel, count(*), max(created_at) from messages group by channel;
```

```
<paste here>
```

No `interview` rows, or a stale `max(created_at)`, means turns are being dropped. The
writes are deliberately fire-and-forget so a bad write can never kill a live call, which
is exactly why the failure is silent: the conversation sounds perfect in the room and
saves nothing. The cause is logged as `Dropped interview turn`.

## Environment

- OS and shell:
- Python (`.venv/Scripts/python --version`) and Node (`node --version`):
- Commit (`git rev-parse --short HEAD`) and branch:
