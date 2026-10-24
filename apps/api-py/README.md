# REEP API — Python / FastAPI

The backend for the REEP dashboard. The Next.js/NestJS/Prisma stack this replaced
has been fully migrated away and deleted — see [`AGENTS.md`](../../AGENTS.md) for
the canonical description of the stack and its two non-negotiable rules.

## Architecture

```
Angular 22 (standalone components · signals · ReactiveForms)
        │  HTTP REST + SSE, same-origin via proxy.conf.json
        ▼
FastAPI
  ├── /api/agent/*         grounded assistant (chat, ask, SSE stream, feedback)
  ├── /api/interview       WebSocket mock interviewer (Nova 2 Sonic, in-process)
  ├── /api/student/resume  ReportLab PDF (local — nothing leaves the machine)
  └── AI layer: app/ai/llm.py, an OpenAI-compatible universal adapter
                (Sakana → Groq → Mistral → OpenRouter → Gemini → Cohere)
                behind student_data_egress_allowed()
  DB: PostgreSQL 17 + pgvector via SQLAlchemy 2.0 + Alembic
```

There is no CrewAI and no hard dependency on Gemini: one set of keys drives any
provider with no code change.

## Stack

FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · psycopg 3 · pgvector · PyJWT ·
uvicorn. **Python 3.14.**

## Run it

```bash
# 1. Postgres, from the REPO ROOT. Creates reep_py and enables pgvector via
#    docker/initdb/. (There is no root package.json — `npm run db:up` does not
#    exist.)
docker compose up -d

# 2. From apps/api-py
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # runtime + pytest
# requirements.txt alone is the RUNTIME set — that is what the Dockerfile installs.

cp .env.example .env        # then set AUTH_SECRET

# 3. Alembic owns the schema. Migrate FIRST — app.seed inserts data only and
#    will fail against an empty database.
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.seed        # demo accounts + KB; REFUSES when ENV=prod
# Production instead runs only:  python -m app.seed_kb   (knowledge base, no accounts)

# 4. Serve. No --reload: on Windows it has repeatedly wedged a stale worker.
.venv/Scripts/python -m uvicorn app.main:app --port 3300
```

**Windows — port 3300 already in use** (the wedged-worker symptom):

```
netstat -ano | findstr :3300
taskkill /PID <pid> /F
```

- Liveness: <http://localhost:3300/health> · Readiness: <http://localhost:3300/ready>
- Interactive docs: <http://localhost:3300/docs>

**Tests:** `.venv/Scripts/python -m pytest`

## Runbook: the call sounded fine but saved nothing

Interview turn writes are deliberately fire-and-forget, so a failing write cannot
kill a live call — which also means it is silent. After a test interview:

```sql
select channel, count(*), max(created_at) from messages group by channel;
```

No `interview` rows (or a stale `max`) means turns are being dropped. The cause is
logged as `Dropped interview turn`, with its exception. `interview_sessions` also
records `turns_emitted` against `turns_persisted`, so the same gap is visible on
one row without a join.

## Production checklist

- `ENV=prod` — marks the session cookie `Secure` (so **TLS is required**, or every
  login silently behaves as logged-out) and makes `python -m app.seed` refuse.
- `AUTH_SECRET` — ≥32 random bytes. The default in `config.py` is in this repo;
  shipping it lets anyone forge a session cookie for any user.
- Mount a volume at `UPLOAD_DIR` — otherwise redeploys destroy student uploads.
- Run the worker with `start` under a supervisor.
- `VOICE_MAINTENANCE_MESSAGE` blank except during an incident.
- `python -m app.seed_kb` (never `app.seed`).

Per-image detail: [`docs/deployment-env.md`](../../docs/deployment-env.md).

## Auth

Passwords are `scrypt:salt:digest` (N=16384, r=8, p=1, dklen=64, hex salt).
Sessions are HS256 JWTs signed with `AUTH_SECRET`, carried in the httpOnly
`reep_session` cookie. The format is byte-compatible with the Node
implementation it replaced — retained so existing password hashes stayed valid
through the migration.

## The student-data egress gate

`student_data_egress_allowed(base_url)` in [`app/ai/llm.py`](app/ai/llm.py):
**loopback is always allowed; anything else requires
`LLM_ALLOW_REMOTE_STUDENT_DATA=true`.** Any path sending a student's private
records to a model must go through `complete_chat(..., carries_student_data=True)`
or `stream_chat(...)`, and is refused before leaving the process otherwise. When
refused, `/student/resume/generate` composes deterministically and says so
(`used_ai=false`).

Public data (a job posting, the approved knowledge base) does not need the gate.
Voice does not traverse it either — no student record is ever placed in the voice
prompt, which is why the worker seeds none. See AGENTS.md rule 1.
