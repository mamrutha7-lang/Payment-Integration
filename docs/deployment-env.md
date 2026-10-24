# Environment contract, per image

REEP deploys as **two different images with two different environments**. They
are not interchangeable, and most production incidents in this stack come from
giving a variable to one and not the other.

| Image | Built from | Runs | Reaches |
|---|---|---|---|
| API | `apps/api-py/Dockerfile` (Python 3.14) | `uvicorn app.main:app` on :3300 | Postgres, LLM providers, Bedrock |
| Web | `apps/web/Dockerfile` (node build → nginx) | nginx on :80 — the **only published port** in the stack | the API over the compose network |

**There were three.** A `Voice worker` image — `Dockerfile.voice`, Python 3.12,
`voice_agent.py start`, reaching LiveKit and Groq — was the third, and this
document described it in four sections after the LiveKit stack had already been
deleted from the repository. There is no fourth process and no second venv: the
mock interviewer is a WebSocket **inside the API process** (Amazon Nova 2 Sonic
on Bedrock, no API key — it is signed with SigV4 from the task's own AWS chain).
Phase 5 deleted `Dockerfile.voice` and the compose service with it.

The API image also runs as two sidecars in `docker-compose.prod.yml`: `migrate`
(one-shot `alembic upgrade head`) and `retention` (`python -m app.retention_job`, daily —
see "Backups and retention" below). Both read the same variables the `api`
service does.

`app/config.py` maps field names to env vars case-insensitively, so
`database_url` reads `DATABASE_URL`. Every field has a default, which is the
hazard this document exists to address: a missing variable does not crash, it
silently selects a development default.

---

## API image

### Required in production

| Variable | Why it cannot be defaulted |
|---|---|
| `DATABASE_URL` | Default points at `localhost:5433` — a container's own loopback, where nothing is listening. |
| `AUTH_SECRET` | Default is a literal string in `config.py`. Sessions are HS256 JWTs signed with it, so shipping the default lets **anyone who has read this repo mint a valid session cookie for any user, including the Main Admin**. Use ≥32 random bytes. On `ENV=prod` the process now REFUSES TO BOOT on a bad one — `Settings.production_boot_failures()`, raised from the lifespan, so uvicorn never binds a port. |
| `WEB_ORIGIN` | CORS + cookie scope. Wrong value ⇒ the browser drops the session cookie and every login appears to work and then behaves as logged-out. |
| `ENV=prod` | Marks the cookie `Secure`, shuts the password door (`settings.password_login_allowed` is an allowlist of dev/CI names, never `not is_prod`), makes `python -m app.seed` refuse, and arms the boot guard above. Leaving it `dev` disables all four at once. |

**`ENV=prod` requires TLS in front of the API.** A `Secure` cookie is not sent
over plain HTTP, so serving prod without HTTPS breaks authentication in a way
that looks like a backend bug.

### Optional

| Variable | Default behaviour when unset |
|---|---|
| `WEB_CONCURRENCY` | One uvicorn worker. The Dockerfile CMD deliberately omits `--workers`, and uvicorn (0.52.3, pinned) reads `$WEB_CONCURRENCY` as that flag's default — so the same image is single-worker on a laptop and a fleet in production with no rebuild. **Every per-worker limit in `config.py` multiplies by this number.** The prod compose sets 4. |
| `INTERVIEW_MAX_SESSIONS` | 100 — and it is **per worker**, so fleet interview capacity = `WEB_CONCURRENCY × INTERVIEW_MAX_SESSIONS`. The prod compose pairs 4 workers with `50` for the 200-interview target; leaving the default there would quietly advertise 400, twice the upstream audio-token budget. |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_S` | Per-worker SQLAlchemy pool. The budget that matters: `workers × (pool + overflow)` must stay under Postgres `max_connections` with headroom for the sidecars, migrations and a human's psql — prod is 4 × (10 + 10) = 80 against `max_connections=200`. |
| `NOVA_SONIC_REGION` | Falls back to `BEDROCK_REGION`, then `AWS_REGION`. One of the three must resolve — this process composes the Bedrock endpoint itself. Nothing resolving ⇒ `GET /api/interview/status` reports unavailable and the interview socket closes 4001. The mock interviewer, and only it, is off. **There is no API key**: the stream is signed with SigV4 from the standard AWS chain, so an AWS-hosted REEP grants the task role `bedrock:InvokeModelWithBidirectionalStream` and pastes nothing. |
| `UPLOAD_DIR` | The image sets `/var/reep/uploads`. **Mount a volume there.** Unmounted, uploads land in the container layer and every redeploy destroys student files. |
| `INTERVIEW_AUDIO_DIR` | The audio store resolves to a **sibling of `UPLOAD_DIR`** (`/var/reep/interview-audio` in the image) — a path no volume covers by default, so recordings would die with the container. The prod compose sets it explicitly and mounts `reep_interview_audio` there, plus a one-shot `interview-audio-init` chown (a named volume's mountpoint is created root-owned; the API runs as uid 10001). Capture itself still requires `INTERVIEW_RECORDING_ENABLED=true` **and** the student's `scope_store_audio` grant. |
| `GROQ_API_KEY` | One of the LLM adapter's providers (`app/ai/llm.py`), nothing to do with voice. Unset just means the adapter auto-selects a different one. |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | Explicit provider. Unset ⇒ auto-select the first per-provider key present (Sakana → Groq → Mistral → OpenRouter → Gemini → Cohere). |
| `LLM_ALLOW_REMOTE_STUDENT_DATA` | Unset ⇒ **student PII never leaves the process** for a non-loopback model. Only the exact string `true` opens the gate. See rule 1 in `AGENTS.md`; setting it means a student's name, USN, marks and attendance go to that provider. |
| `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY` | KB retrieval falls back to Postgres full-text. Degrades answer quality; nothing breaks. The KB is public policy text, so it carries no PII to the embedder. |
| `PGSSLMODE`, `PGSSLROOTCERT` | Read by libpq directly. Set `require` (or stricter) for a managed database on an untrusted network. |

---

## Web image

nginx serving the compiled Angular SPA, and the stack's **single front door**:
it proxies everything under `/api` (including the `/api/interview` WebSocket
upgrade) to the api service. This is not a convenience — the session is an
httpOnly `SameSite=Lax` cookie, so the SPA and the API **must be one origin**,
and in production this image is that origin. It takes no environment variables;
its behaviour lives in `apps/web/nginx.conf`.

**TLS terminates here.** `ENV=prod` marks the cookie `Secure`, and a `Secure`
cookie is silently dropped over plain HTTP. The 443 server block in
`nginx.conf` and the 443 port + certificate mount on the `web` service are
committed as comments; enable the three together.

---


## Backups and retention

Two sidecars in `docker-compose.prod.yml` keep the data honest. Before them
there were **no backups of any kind**, and the 180-day retention promise
stamped on every interview row executed never.

- **`db-backup`** — nightly `pg_dump -Fc` of `reep_py` into the `reep_backups`
  volume, 14-day rotation, and the **first dump runs at container start** so a
  fresh deploy proves the path before anyone trusts it. One `db-backup OK:` /
  `db-backup FAILED:` line per attempt, shaped to survive a log aggregator.
  The volume lives on the **same host as the database**, so it answers "we
  dropped a table", not "the machine died": replicate it off-box
  (rsync/rclone/object storage) as the immediate next step, and **rehearse a
  restore** before one is needed —

      pg_restore -d reep_py <file>

  A backup that has never been restored is a hope, not a backup.

- **`retention`** — the API image running `python -m app.retention_job` once a day
  (first run at start): `retention.purge_expired` walks conversations and
  interview records through soft-delete, PII-scrub and hard-delete on their
  90/180-day clocks, and `retention.redact_expired_runs` strips aged `AgentRun`
  free text while keeping the metrics. The trigger lives in the compose file on
  purpose — `app/retention.py` refuses to run these from API boot, because that
  would make the amount of data destroyed a function of how often someone
  restarts the API. The sidecar mounts the interview-audio volume because
  expiring recordings are deleted **through the filesystem**: unmounted, every
  missing file reads as "already deleted" while the bytes survive on the api's
  volume.

---

## Startup order

1. `db` — healthy (`pg_isready` against **`reep_py`**, not `reep_dev`).
2. `migrate` — one-shot `alembic upgrade head`, must exit 0.
3. `interview-audio-init` — one-shot `chown` of the interview-audio volume
   (docker creates a fresh named volume's mountpoint root-owned; the API runs
   as uid 10001 and could otherwise never write a recording).
4. `api` — after migrations and the chown complete.
5. `web` — nginx, after the API starts. The only published port.
6. `db-backup`, `retention` — after `db` (and `migrate`, for retention); each
   runs immediately, then daily.

There is no seventh. `voice-worker` stood here behind a `voice` compose profile
as "the LiveKit rollback path"; the image it built could not build at all by
then, because `requirements-voice.txt` and `voice_agent.py` were already gone.

**Migrations run once, as their own service.** Running them from the API
entrypoint means every replica races on the Alembic version table on boot, and
the loser can fail in ways that leave the schema half-applied.

Seeding is separate and manual. `python -m app.seed_kb` populates the Knowledge
Base and is safe in production. `python -m app.seed_roster` is the other
production-safe one: it derives student accounts from their USNs, no passwords.
`python -m app.seed` creates the demo accounts and **refuses to run when
`ENV=prod`** — it would otherwise create `admin@bgscet.ac.in` / `admin123`, the
Main Admin, who by rule 2 reads every student's marks, attendance and USN, behind
a password published in `AGENTS.md`. (This paragraph named `director@bgscet.ac.in`
/ `director123` until Phase 5; that role and that account were removed on
2026-09-10 — `director123` still sits on `set_password`'s refusal list, because
the string was published even though the account is gone.)

---

## Shutdown

| Service | `stop_grace_period` | Why that number |
|---|---|---|
| api | 120s | Above uvicorn's `--timeout-graceful-shutdown 110`, so it drains first. `/api/agent/chat/stream` persists the assistant turn only after the last delta — a hard kill mid-stream loses it. |

**The interview socket outlives that grace period, and compose is not where it is
solved.** A Nova session runs up to `NOVA_SONIC_CONNECTION_SECONDS` (480 s,
Bedrock's own ceiling) inside the api process, which is longer than the 120 s
above — and on Fargate `stopTimeout` caps at 120 s regardless. What actually
keeps a live interview alive through a deploy is the target group's **600 s
deregistration delay**; `apps/api-py/tests/test_codebase_guards.py` compares that
CDK constant against `nova_sonic_connection_seconds` so the two cannot drift.

---

## Health endpoints

- `GET /health` — **liveness.** Touches no dependencies. This is what the
  container healthcheck and any restart policy must use.
- `GET /ready` — **readiness.** 503 on hard dependencies only; use it for
  load-balancer membership.

Pointing a restart-triggering healthcheck at `/ready` turns a brief Postgres
wobble into a restart storm across every replica — a recoverable blip becomes an
outage.

---

## Secrets hygiene

Secrets come from the orchestrator's secret store, never from a committed file
and never baked into an image. `docker-compose.prod.yml` declares them as
`${VAR:?message}`, so a missing one fails the deploy loudly instead of starting a
half-configured stack.

`apps/api-py/.env` is gitignored and is for local development only. Anything that
has ever been in a local `.env` and then discussed, pasted, or logged should be
treated as disclosed and rotated at the provider.
