# REEP Dashboard

A college **placement-readiness** platform for students, faculty, alumni and the placement office (the Main Admin).

- **Front end** — Angular 22 SPA (`apps/web`): the "REEP v2" desktop UI — a lilac Y2K-chrome / glass design system in global CSS classes — standalone components + signals, every route lazy.
- **Back end** — Python / FastAPI (`apps/api-py`): SQLAlchemy 2.0 + Alembic + Pydantic v2 on PostgreSQL, with a universal OpenAI-compatible LLM adapter behind a student-data egress gate.
- **Database** — PostgreSQL 17 via `docker-compose.yml`.

> This repo was migrated from a Next.js/React + NestJS + Prisma stack to Angular + FastAPI. The old stack has been removed.

## Who is who — the naming conventions

Four kinds of account, and the words the product uses for them. The on-screen name
and the stored role differ for two of them, and that gap is where every diagram and
document has drifted, so both are given here. Every diagram in this repository is
expected to use these words.

| On screen | Stored role (`users.role`) | In the code | What it is |
|---|---|---|---|
| **Student** | `STUDENT` | `/student/*`, `app/routers/student.py`, a `students` row, the session's `studentId` | A current student. The roster is the access control: an address with no `users` row cannot sign in. |
| **Faculty** | `MENTOR` | `/mentor/*`, `app/routers/mentor.py`, `require_mentor`, the console's `/admin/faculty` | A faculty account. It is **not a mentor by existing** — it has no `mentors` row and sees nobody until the Main Admin assigns it a student. |
| a student's **mentor** | `MENTOR` **plus a `mentors` row** | `students.mentor_id` → `mentors.id`, the session's `mentorId`, `app/mentor_functions.py` | The faculty member assigned to that student. "Your mentor" on the student's screens; the `Mentor` group row is what rule 2 scopes on, and the three `mentor.*` functions are derived from having a mentee. |
| **Alumni** | `ALUMNI` | `/alumni/*`, `app/routers/alumni.py` | A graduate: no `students` row, no `mentors` row, no staff scope. |
| **Main Admin** — the placement office | `ADMIN` | `/admin/*`, `app/routers/console.py` and `admin_*.py`, `require_admin`, `features/admin/`, the login's dashed "Main Admin" door | **One account per deployment.** "Placement office", "placement cell" and "TPO" on the screens all mean this account; it is the one role Governance admits, and faculty reach a console screen only as a grant it makes. |

Three things follow:

- **"Mentor" is a stored value and a relationship, never the name of an account
  kind.** The account is *Faculty*. The student's screens say "your mentor" and
  "Faculty / TPO Log" because, to that student, the assigned faculty member is one.
- **The placement office is the Main Admin**, role `ADMIN`, and there is exactly
  one. **DIRECTOR is not a role** (removed 2026-09-10): `Role.DIRECTOR` survives
  only as an enum value nothing admits, and PROGRAM DIRECTOR is a job title printed
  on the college's own leave form.
- The login's portal chooser offers Student / Faculty / Alumni; the office comes in
  through the dashed "Main Admin" door below it. The pick is descriptive — the role
  is always the roster's.

`AGENTS.md` carries the same table under "Who is who", and
`docs/institutional-spine-build-log.md` "Naming conventions" has it as L0 beside the
code conventions. Diagrams draw the pair, on-screen name first: *Faculty (role
MENTOR)*, *Main Admin (role ADMIN)*.

## Quick start

```bash
# 1. Postgres (host port 5433, container "reep-postgres")
docker compose up -d

# 2. API  (from apps/api-py; venv at .venv, Python 3.14)
cd apps/api-py
.venv/Scripts/python -m alembic upgrade head     # migrations
.venv/Scripts/python -m app.seed                 # idempotent dev seed
.venv/Scripts/python -m uvicorn app.main:app --port 3300
#   → http://127.0.0.1:3300/docs

# 3. Web  (from apps/web)
cd apps/web
npx ng serve                                     # → http://localhost:4200
```

The dev server proxies `/api` → `http://localhost:3300`, so the app is same-origin.

**Seeded logins.** `python -m app.seed` creates these and **refuses to run on
`ENV=prod`** — the passwords are published here, so the accounts must never exist
on a production host. In production the door is Google sign-in and the roster is
the access control; `POST /api/auth/login` answers 403 unless `PASSWORD_LOGIN=true`
or an operator has issued a real password with `python -m app.set_password`.

| On screen                       | Stored role | Email                 | Password   |
|---------------------------------|-------------|-----------------------|------------|
| Student                         | `STUDENT`   | student@bgscet.ac.in  | student123 |
| Faculty                         | `MENTOR`    | mentor@bgscet.ac.in   | mentor123  |
| Alumni                          | `ALUMNI`    | alumni@bgscet.ac.in   | alumni123  |
| Main Admin (the placement office) | `ADMIN`   | admin@bgscet.ac.in    | admin123   |

There is **no DIRECTOR role** — it was removed in 2026-09-10 along with
`director@bgscet.ac.in`, which this table listed for months afterwards. ADMIN is
the placement office — the Main Admin — and there is exactly one of it; faculty are
role MENTOR and reach a console screen only as a grant the Main Admin makes in
Governance (see "Who is who" above).

## AI features (optional)

Text chat + AI resume polish run through the universal LLM adapter — paste any one provider key into `apps/api-py/.env` (`GROQ_API_KEY`, `MISTRAL_API_KEY`, a local Ollama URL, …) and it works with no code change. **Student PII never goes to a remote free model** unless `LLM_ALLOW_REMOTE_STUDENT_DATA=true`; otherwise the resume composes deterministically.

**Voice is the mock interviewer at `/student/assistant`**, and it takes no API key. It is genuinely speech-to-speech — Amazon Nova 2 Sonic on Bedrock, over a WebSocket **inside the API process**, signed with SigV4 from the standard AWS chain; what must resolve is a region (`NOVA_SONIC_REGION`, falling back to `BEDROCK_REGION`, then `AWS_REGION`), not a secret. Blank means `GET /api/interview/status` reports unavailable and nothing else in the dashboard is affected.

> The LiveKit cascade (Silero VAD → Groq Whisper → Groq Llama → TTS, in a fourth process on its own Python 3.12 venv) was **removed in 2026-09** and this paragraph described it until Phase 5. There is no separate worker, no `voice_agent.py`, no `requirements-voice.txt` and no `/api/voice/*`.

## Tests

```bash
cd apps/api-py && .venv/Scripts/python -m pytest   # backend suite
cd apps/web && npx ng build                         # frontend compile
npm ci && npm run test:e2e                          # end-to-end, from the repo root, against the running app
```

The end-to-end suite is Playwright, and each of its tests automates one case in
[test-management/manual-test-cases.md](test-management/manual-test-cases.md)
(linked by a `@TC-NNN` tag). Every run writes `manual-test-results.csv`, with a
row for every case. Before the first run, install its browser with `npx playwright install chromium`.

`main` is gated by **five** required status checks, and `./tools/ci/preflight.sh`
runs all five locally in the order that fails fastest — read its exit code, not
its last line: `2` means nothing failed but something did not *run*, which is not
the same as passing. See [tools/ci/README.md](tools/ci/README.md).

See [AGENTS.md](AGENTS.md) for architecture rules (the egress gate, the staff-scope rule, Alembic enum gotchas) and [docs/](docs/) for the migration log and design references.
