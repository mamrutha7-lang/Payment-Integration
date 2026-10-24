# REEP — Complete Architecture & Tech Stack

> **PARTLY STALE — last verified against the code 2026-08. Read `AGENTS.md` first
> where the two disagree.** It was generated from the repository rather than a
> whiteboard, and that is exactly why it rots: it describes what was there on the
> day it was read. Three things have changed underneath it since, each of which
> appears in the diagrams below as if it still existed:
>
> - **The LiveKit voice stack is gone** (2026-09). No `voice-worker` service, no
>   `Dockerfile.voice`, no `voice_agent.py`, no `/api/voice/*`, no fourth process
>   and no second venv. The one voice experience is `/student/assistant`, a
>   WebSocket inside the API process speaking to Amazon Nova 2 Sonic.
> - **The `worker-imports` CI job went with it.** CI has five jobs: `api`,
>   `pii-gate`, `api-imports`, `web`, `cdk`.
> - **The `openai` interview engine is gone** (2026-09). `interview_relay.py` was
>   deleted; nothing reads `OPENAI_API_KEY`. The reasoning survives in
>   `docs/interview-engine-v3.md`, which is a design record, not a description of
>   running code. `apps/interview-realtime/` was deleted too.
>
> A fourth — **DIRECTOR is not a role** (2026-09-10) — was corrected in place on
> 2026-10-08 rather than banner'd: every diagram below now says Main Admin (role
> ADMIN, the placement office) and Faculty (role MENTOR), the vocabulary of
> `AGENTS.md` "Who is who" (repeated in §5), with `/api/admin/*` and `require_admin`
> where `/api/director/*` and `require_director` stood.
>
> Everything else was accurate when written and most of it still is. Corrected in
> place rather than banner'd: `docs/deployment-env.md`.
>
> Companion: `AGENTS.md` (the operating rules), `docs/interview-engine-v3.md`,
> `docs/google-sign-in.md`, `docs/deployment-env.md`.

---

## 0. One-paragraph summary

REEP is a college placement-readiness dashboard: an **Angular 22 SPA** talking over
same-origin HTTP + WebSocket to a **Python 3.14 / FastAPI** monolith, on
**PostgreSQL 17 with pgvector**. The API is the only process that holds credentials —
it relays the browser's microphone to the **OpenAI Realtime API** for the mock
interviewer, brokers **LiveKit** tokens for the (retained, UI-less) voice stack, verifies
**Google OIDC** ID tokens for sign-in, and fronts a provider-agnostic LLM adapter
behind a hard **student-data egress gate**. An optional **fourth process** (Python 3.12)
runs the LiveKit voice agent cascade. Everything else — auth, RBAC, retention,
uploads, resume PDF rendering, the knowledge base — lives in the one API process.

---

## 1. Process & deployment topology

```mermaid
flowchart TB
    subgraph browser["🖥️ BROWSER — Student · Faculty (role MENTOR) · Main Admin (role ADMIN) · Alumni"]
        SPA["<b>Angular 22.1 SPA</b><br/>standalone components · signals · ReactiveForms<br/>@angular/build (esbuild) · TypeScript 6.0<br/>lazy routes → ~142 kB initial<br/>apexcharts · ng-apexcharts · livekit-client · rxjs 7.8"]
        AW["<b>AudioWorklet</b> 'pcm-recorder'<br/>mic → PCM16 LE mono 24 kHz<br/>resample 44.1k→24k, 20 ms chunks"]
        PB["<b>PCM playback scheduler</b><br/>AudioContext @24 kHz<br/>jitter buffer + underrun tracking"]
        SPA --- AW
        SPA --- PB
    end

    subgraph edge["DEV EDGE"]
        PROXY["<b>ng serve :4200</b><br/>proxy.conf.json → /api → :3300<br/>ws:true · changeOrigin · same-origin cookie"]
    end

    subgraph api["⚙️ PROCESS 1 — REEP API  (python:3.14-slim, uvicorn :3300, UID 10001)"]
        direction TB
        MW["CORS middleware (allow_origins=[WEB_ORIGIN], credentials)<br/>lifespan: boot guard → voice-secret warning → orphan sweep"]
        R1["<b>Routers</b> /api/auth · /api/student · /api/mentor<br/>/api/admin · /api/leaves · /api/register<br/>/api/agent · /api/voice · /api/interview · /health /ready"]
        REL["<b>app/interview_relay.py</b><br/>in-process WS relay (NOT a 5th process)"]
        SVC["<b>Services</b> conversations · knowledge · document_store<br/>retention · mailer · redaction · resume_pdf<br/>security · google_auth · interview_audio · interview_matrix"]
        AI["<b>app/ai/</b> llm.py (egress gate) · embeddings.py<br/>orchestrator.py · adk.py · agents.py"]
        ORM["SQLAlchemy 2.0.52 ORM · Alembic 1.19.1 · Pydantic 2.13.4"]
        MW --> R1 --> SVC --> ORM
        R1 --> REL
        SVC --> AI
    end

    subgraph worker["🎙️ PROCESS 4 — Voice worker (OPTIONAL, python:3.12-slim, own venv)"]
        VW["<b>voice_agent.py</b> · livekit-agents 1.6.10<br/>NO database access · stdlib urllib only<br/>heartbeat thread every ~15 s"]
        CASCADE["4-stage cascade:<br/>BVC noise-cancel → Silero VAD →<br/>Groq whisper-large-v3-turbo →<br/>Groq llama-3.3-70b-versatile → Groq TTS (or edge-tts, opt-in)"]
        VW --- CASCADE
    end

    subgraph data["🗄️ DATA"]
        PG[("<b>PostgreSQL 17</b><br/>image pgvector/pgvector:pg17<br/>container reep-postgres<br/>host :5433 → 5432 · DB <code>reep_py</code><br/>55 tables · CREATE EXTENSION vector<br/>volume reep_pgdata")]
        FS[("<b>Upload store</b> UPLOAD_DIR<br/>/var/reep/uploads (volume reep_uploads)<br/>magic-byte typed · random stored_name<br/>PDF/PNG/JPEG · ≤10 MB")]
        AUD[("<b>Interview audio</b> (OFF by default)<br/>2 WAV per interview, never mixed<br/>PCM16 24 kHz · ≤64 MB · 180-day clock")]
    end

    subgraph ext["☁️ EXTERNAL SERVICES"]
        GOOG["<b>Google Identity</b><br/>accounts.google.com/o/oauth2/v2/auth<br/>oauth2.googleapis.com/token · JWKS"]
        OAI["<b>OpenAI Realtime</b><br/>wss://api.openai.com/v1/realtime<br/>PCM16 24 kHz duplex"]
        LK["<b>LiveKit</b> SFU / Cloud<br/>WebRTC rooms"]
        GROQ["<b>Groq</b> STT + LLM + TTS"]
        LLMP["<b>LLM providers</b> (auto-select)<br/>Sakana → Groq → Mistral →<br/>OpenRouter → Gemini → Cohere"]
        EMB["<b>Mistral embeddings</b><br/>mistral-embed"]
        OLL["<b>Ollama</b> (loopback)<br/>gemma3:12b · num_ctx 16384<br/>127.0.0.1:11434/v1"]
    end

    SPA -->|"HTTPS/JSON · credentials:'include'<br/>httpOnly cookie reep_session"| PROXY
    AW -->|"WS /api/interview?specialization=<br/>binary PCM16 frames"| PROXY
    SPA -->|"WebRTC (retained, no UI caller)"| LK
    PROXY --> MW

    REL <-->|"WSS · Authorization: Bearer OPENAI_API_KEY<br/>(never leaves this process)"| OAI
    R1 -->|"ID-token verify RS256 · code exchange"| GOOG
    R1 -->|"AccessToken mint (livekit-api 1.2.0)"| LK
    AI -->|"POST /chat/completions (OpenAI-compatible)"| LLMP
    AI -->|"POST /embeddings"| EMB
    AI -.->|"loopback — always allowed by the gate"| OLL
    ORM -->|"psycopg 3.3.4 (binary)"| PG
    SVC --> FS
    REL --> AUD
    VW <-->|"WebRTC audio"| LK
    VW -->|"POST /api/voice/transcript<br/>POST /api/voice/heartbeat<br/>X-Worker-Secret"| R1
    CASCADE --> GROQ

    classDef proc fill:#eef4ff,stroke:#3b6fd4,stroke-width:2px
    classDef store fill:#fff7e6,stroke:#c98a1b,stroke-width:2px
    classDef extn fill:#f3f0ff,stroke:#7a5af8,stroke-width:2px
```

**Process count is a fact people get wrong, so state it plainly:**

| # | Process | Runtime | Required? | Notes |
|---|---------|---------|-----------|-------|
| 1 | PostgreSQL 17 + pgvector | container | **yes** | `docker compose up -d`, host port **5433** |
| 2 | FastAPI API (`uvicorn app.main:app --port 3300`) | Python **3.14**, `.venv` | **yes** | includes the interview relay |
| 3 | Angular dev server (`npx ng serve`) | Node 22, npm 11.16 | dev only | prod serves the built bundle statically |
| 4 | LiveKit voice worker (`voice_agent.py`) | Python **3.12**, `.venv-voice` | optional | `livekit-agents` requires `<3.15` |

The mock interviewer is **not** a fifth process. It runs inside process 2.

---

## 2. Front end — micro detail

```mermaid
flowchart LR
    subgraph boot["Bootstrap"]
        M["main.ts → bootstrapApplication"]
        CFG["app.config.ts<br/>provideRouter · provideHttpClient"]
    end
    subgraph core["core/"]
        AG["auth.guard.ts (eager)"]
        AS["auth.service.ts<br/>signal&lt;SessionPayload&gt; · /auth/me · /auth/login · /auth/logout"]
        IS["<b>interview.service.ts</b><br/>WS state machine · AudioWorklet · PCM scheduler<br/>close-code → student sentence map"]
        CV["chat-voice.service.ts<br/>livekit-client Room · VoiceState machine"]
        TH["theme.service.ts"]
        SE["session.ts"]
    end
    subgraph shell["layout/"]
        SH["app-shell.component (eager — first frame)"]
    end
    subgraph feats["features/ — every route lazy via loadComponent"]
        L["login · register"]
        ST["student/: overview · academics · certifications · skilling ·<br/>time-log · courses · records · leaderboards · uploads ·<br/>resume (17 section components + preview + all-resumes) ·<br/>jobs · offers · profile · <b>interviews</b>"]
        AST["assistant/ — /student/assistant, the mock interviewer<br/>agent/ — ONE chat chunk for /student/agent, /mentor/agent, /admin/agent"]
        AD["admin/ — the Main Admin console (was director/)"]
    end
    subgraph shared["shared/ + styles"]
        KIT["kit.components.ts · tone.ts · icon.component<br/>charts/bar-chart · voice-visualizer"]
        CSS["styles/reep-v2.scss (global design system)<br/>reep-theme.scss · reep-v2-resume.scss<br/>.card .dt-table .chip good/warn/risk/neutral .dense-*"]
    end
    M --> CFG --> AG --> SH --> feats
    core --> feats
    shared --> feats
```

| Concern | Detail |
|---|---|
| Framework | Angular **22.1**, standalone components, **signals**, ReactiveForms |
| Build | `@angular/build:application` (esbuild), TypeScript **~6.0.2**, SCSS |
| Bundle budget | initial **warn 250 kB / error 400 kB**; anyComponentStyle 16 kB / 32 kB; `outputHashing: all` |
| Why lazy | every route was once `component:` → one **1.23 MB** `main` chunk; now **~142 kB** initial. One static import fails CI. |
| API access | `fetch(\`${environment.apiBase}/...\`, { credentials: 'include' })`, `apiBase = '/api'` |
| Dev proxy | `proxy.conf.json`: `/api` → `http://localhost:3300`, `ws: true`, `changeOrigin: true` |
| Charts | apexcharts 6.8 / ng-apexcharts 3.0 |
| Realtime audio | `AudioContext({ sampleRate: 24000 })`, AudioWorklet `pcm-recorder`, `MAX_UPLINK_BUFFERED_BYTES = 24000` backpressure |
| Test / lint | vitest 4, jsdom 28, prettier 3.8, `npx ng test --watch=false` |
| Status colour rule | status is **text + colour together**, never colour alone |

---

## 3. Back end — module map

```mermaid
flowchart TB
    subgraph entry["app/main.py — lifespan gates, in order"]
        G0["logging: websockets logger pinned to INFO<br/>(DEBUG would print Authorization: Bearer OPENAI_API_KEY)"]
        G1["1 · settings.production_boot_failures() → RuntimeError<br/>uvicorn never binds a port"]
        G2["2 · blank VOICE_WORKER_SECRET on non-dev ENV → WARNING"]
        G3["3 · retention.finalize_orphaned_interviews() on a thread"]
        G4["shutdown: interview.shutdown_interviews()"]
        G0 --> G1 --> G2 --> G3 --> G4
    end

    subgraph routers["app/routers/ — mounted surface"]
        H["health.py · GET /health (liveness, no deps)<br/>GET /ready (per-dependency, 503 on DB down)"]
        A["auth.py /api/auth · login(403 in prod) · sso/status<br/>sso/google · sso/google/callback · me · logout"]
        S["student.py /api/student — 43 endpoints"]
        ME["mentor.py /api/mentor — mentees, notes, alerts,<br/>offers, focus, uploads, skill-claims"]
        D["console.py + admin_*.py /api/admin — the Main Admin console:<br/>overview, cohorts, criteria, mail, alert-rules, job-imports"]
        LV["leave.py /api/leaves"]
        RG["registration.py /api/register"]
        AGR["agent.py /api/agent — chat, chat/stream, ask,<br/>history, runs, knowledge/search, feedback, metrics"]
        V["voice.py /api/voice — heartbeat, status, token,<br/>consent, transcript"]
        I["interview.py /api/interview — GET /status, <b>WS ''</b>"]
        IR["interview_records.py — student_router /api/interview<br/>staff_router /api/mentor (DISCOVERED, not named:<br/>every public APIRouter with an /api/ prefix is mounted;<br/>import guarded on Exception, not ImportError)"]
    end

    subgraph domain["Services"]
        C["conversations.py — the ONE writer<br/>append_message · assert_owner · ConversationGone"]
        K["knowledge.py — HYBRID retrieval<br/>0.5·cosine + 0.5·ts_rank, gate _MAX_VEC_DISTANCE=0.32"]
        F["document_store.py — magic-byte typing, random names"]
        RT["retention.py — purge_expired · orphan sweep"]
        SEC["security.py — scrypt + HS256 + token_version"]
        GA["google_auth.py — FastAPI-free OIDC verifier"]
        IM["interview_matrix.py — 4 specializations + phase FSM"]
        IA["interview_audio.py — two-WAV sibling of document_store"]
        RED["redaction.py · mailer.py · resume_pdf.py · grant_access.py"]
    end

    subgraph aidir["app/ai/"]
        LLM["llm.py — provider auto-select + <b>egress gate</b><br/>complete_chat / stream_chat"]
        EM["embeddings.py — explicit EMBEDDING_* else Mistral<br/>none ⇒ full-text only"]
        OR["orchestrator.py — tool-backed, rule-routed intents<br/>(retained behind POST /api/agent/ask)"]
        ADK["adk.py + agents.py — google-adk 2.7 via litellm 1.96"]
        EV["eval/golden.py — contract regression set"]
    end

    subgraph cli["Operational entry points (python -m …)"]
        SD["app.seed — REFUSES when ENV=prod (3 demo logins)"]
        SK["app.seed_kb — production-safe KB seed"]
        SR["app.seed_roster — USN→email, --rekey-domain"]
        GR["app.grant_access — one row = one login"]
        AL["alembic upgrade head — 42 migrations"]
    end

    entry --> routers --> domain --> aidir
```

### Runtime pins (`requirements.txt`, exact `==`)

| Package | Version | Why it is pinned here |
|---|---|---|
| fastapi | 0.141.1 | app framework |
| uvicorn[standard] | 0.52.3 | ASGI server; `--timeout-graceful-shutdown 110` in the image |
| **websockets** | **15.0.1** | relay uses `additional_headers=` (renamed in ≥14) and the asyncio client API |
| sqlalchemy | 2.0.52 | ORM, `Mapped[...]` style |
| alembic | 1.19.1 | migrations |
| pydantic | 2.13.4 | schemas |
| pydantic-settings | 2.15.0 | `Settings` |
| psycopg[binary] | 3.3.4 | Postgres driver (`postgresql+psycopg://`) |
| pgvector | 0.5.0 | `vector` column + SQLAlchemy adapter |
| pyjwt[crypto] | 2.13.0 | HS256 session **and** RS256 Google ID token |
| cryptography | 50.0.0 | pinned directly — was only transitive |
| python-multipart | 0.0.32 | uploads |
| httpx | 0.28.1 | LLM calls, OAuth token exchange, JWKS |
| reportlab | 5.0.0 | local resume PDF (no network → gate N/A) |
| google-adk | 2.7.0 | agent framework |
| litellm | 1.96.2 | ADK → non-Gemini providers |
| livekit-api | 1.2.0 | mints voice room tokens |

`requirements-dev.txt` = `-r requirements.txt` + **pytest 9.1.1**, and nothing else.
The Dockerfile installs `requirements.txt` **alone** — no test runner in a production image.

### Voice worker pins (`requirements-voice.txt`, Python 3.12)

`livekit-agents==1.6.10` · `livekit-plugins-groq==1.6.10` · `livekit-plugins-silero==1.6.10` ·
`livekit-plugins-noise-cancellation==0.3.0` · `edge-tts==7.2.8` (opt-in only).
No HTTP client: stdlib `urllib`. No DB driver: it never touches Postgres.

---

## 4. Data layer

```mermaid
erDiagram
    users ||--o| students : "1:1 (role STUDENT)"
    users ||--o| mentors : "1:1 (role MENTOR)"
    mentors ||--o{ students : "mentor group — scope for rule 2"
    cohorts ||--o{ students : ""
    students ||--o{ semester_results : ""
    semester_results ||--o{ subject_marks : ""
    students ||--o{ attendance_records : ""
    students ||--o{ student_profiles : ""
    students ||--o{ resume_profiles : ""
    students ||--o{ resumes : ""
    students ||--o{ uploads : "magic-byte typed files"
    students ||--o{ skill_claims : ""
    students ||--o{ student_skills : ""
    skills ||--o{ student_skills : ""
    students ||--o{ job_applications : ""
    jobs ||--o{ job_applications : ""
    job_import_runs ||--o{ jobs : ""
    students ||--o{ placement_offers : ""
    students ||--o{ registrations : ""
    registration_rules ||--o{ registrations : ""
    students ||--o{ leave_requests : ""
    students ||--o{ mentor_notes : ""
    students ||--o{ alerts : ""
    alert_rule_configs ||--o{ alerts : ""
    students ||--o{ swoc_entries : ""
    students ||--o{ mock_attempts : ""
    students ||--o{ lab_sessions : ""
    students ||--o{ time_sheet_entries : ""
    students ||--o{ certification_progress : ""
    certifications ||--o{ certification_progress : ""
    courses ||--o{ enrollments : ""
    students ||--o{ enrollments : ""
    students ||--o{ academic_qualifications : ""
    students ||--o{ academic_gaps : ""
    students ||--o{ schedule_items : ""
    users ||--o{ conversations : "one active per owner"
    conversations ||--o{ messages : "channel = text | voice | interview"
    conversations ||--o{ assistant_feedback : ""
    agent_runs ||--o{ assistant_feedback : "(no new rows — superseded)"
    knowledge_documents ||--o{ knowledge_chunks : "embedding vector (pgvector)"
    students ||--o{ interview_consents : "3 booleans, versioned"
    interview_consents ||--o{ interview_sessions : "consent_id pins the exact grant"
    interview_sessions ||--o{ interview_turns : ""
    interview_sessions ||--o| interview_evaluations : "nullable scores"
    mail_logs ||--|| mail_logs : "unique dedupe_key = send-exactly-once"
```

**55 tables**, all defined in `apps/api-py/app/models/` (source of truth) and
registered in `models/__init__.py` so Alembic autogenerate sees them:

```
academic_gaps · academic_qualifications · agent_runs · alert_rule_configs ·
alerts · assistant_feedback · attendance_records · certification_progress ·
certifications · cohorts · conversations · courses · email_verifications ·
english_baseline_sections · english_baselines · enrollments ·
interview_consents · interview_evaluations · interview_sessions ·
interview_turns · job_applications · job_import_runs · jobs · knowledge_chunks ·
knowledge_documents · lab_sessions · leave_requests · login_days · mail_logs ·
mentor_notes · mentors · messages · mock_attempts · placement_criteria ·
placement_offers · registration_rules · registrations · resume_profiles ·
resumes · schedule_items · semester_results · skill_claims · skills ·
student_milestones · student_profiles · student_skills · students ·
subject_marks · swoc_entries · time_ledger_cells · time_ledger_days ·
time_sheet_entries · uploads · users · voice_worker_heartbeats
```

**Alembic enum gotchas** (hit repeatedly, so they are written down):
1. adding an enum *column* to an existing table does **not** auto-`CREATE TYPE` — create it first;
2. a *new table* reusing an *existing* enum must use `postgresql.ENUM(..., name='x', create_type=False)` — autogenerate emits a bare `sa.Enum` that errors "type already exists";
3. two columns sharing one enum reuse a **single** `Enum` instance.

**Knowledge base = pgvector.** `KnowledgeChunk.embedding` is a *dimensionless* `vector`.
Retrieval is hybrid: Postgres full-text (`ts_rank` over `to_tsvector('english', …)`)
blended `0.5 / 0.5` with pgvector cosine (`embedding <=> :query_vec`), and a vector
match only counts when its cosine **distance ≤ 0.32** — otherwise an off-topic query
would always drag in a nearest neighbour instead of hitting the honest
"no approved answer" fallback. **No embedder configured ⇒ full-text only**, so the KB
never stops working. KB text is APPROVED PUBLIC POLICY, which is why embedding it is
outside the student-data egress gate.

---

## 5. Authentication & authorization

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as FastAPI /api/auth
    participant G as Google Identity
    participant DB as Postgres users

    B->>A: GET /api/auth/sso/status
    A-->>B: {available, reason} — blank client id ⇒ button renders disabled
    B->>A: GET /api/auth/sso/google
    A-->>B: 302 → accounts.google.com  (+ single-use state cookie, nonce)
    B->>G: consent
    G-->>A: 302 /api/auth/sso/google/callback?code&state
    A->>A: state cookie matches? (key derived from AUTH_SECRET)
    A->>G: POST oauth2.googleapis.com/token  (client_id + secret + code)
    G-->>A: id_token (RS256)
    A->>G: GET JWKS
    A->>A: verify signature · aud == GOOGLE_CLIENT_ID · iss == accounts.google.com<br/>· not expired · email_verified == true · nonce matches
    A->>DB: SELECT * FROM users WHERE lower(email) = lower(claims.email)
    alt no row
        A-->>B: 302 /login?error=sso_not_enrolled  (nothing self-provisions)
    else row found
        A->>A: create_session_token(userId,email,name,role,studentId?,mentorId?)
        A-->>B: Set-Cookie reep_session=<HS256 JWT>; HttpOnly; SameSite; Secure(prod); 12 h
    end
```

| Element | Detail |
|---|---|
| Cookie | `reep_session`, **httpOnly**, `Secure` on every non-dev `ENV`, 12-hour HS256 JWT |
| Claims | `userId, email, name, role, studentId?, mentorId?` + `iat/exp` + token version |
| Signing | `AUTH_SECRET` — the *same* secret also derives the OAuth flow-cookie key |
| Passwords | `scrypt:<salt_hex>:<digest_hex>`, N=16384 r=8 p=1 dklen=64, salt as a **hex string** — byte-compatible with Node `scryptSync`, so hashes migrated without a reset |
| Revocation | `users.token_version` rides in the token; `AUTH_REVOCATION_CACHE_SECONDS` (default **60**) bounds how long a logged-out token still works. DB read failure **admits** the session (fails open) and logs. |
| Password login | `POST /api/auth/login` **kept**, refused with 403 when `settings.password_login_allowed` is false. That is an **allowlist of dev/CI env names**, not `not is_prod` — an unrecognised `ENV` ("staging", "uat", blank) shuts the door rather than opening it. |
| Roster | `python -m app.seed_roster` derives `1MP25MDM01` → `1mp25mdm01@bgscet.ac.in` (`ROSTER_EMAIL_DOMAIN`, alias `COLLEGE_EMAIL_DOMAIN`, `--rekey-domain` to move a batch) |
| Allowlist | the `users` table itself. `GOOGLE_ALLOWED_DOMAIN` is a **label, not a fence**. |

### Who is who — the role vocabulary every diagram here uses

| On screen | Stored role | Gate | Reach |
|---|---|---|---|
| Student | `STUDENT` | `_require_student` | their own records |
| Faculty | `MENTOR` | `require_mentor` | their own mentee group, and **nobody** without one (rule 2) |
| a student's mentor | `MENTOR` plus a `mentors` row (`students.mentor_id`) | `require_mentor` + `_assert_can_access_student` | that student |
| Alumni | `ALUMNI` | — | their own profile and the jobs sheet |
| Main Admin — the placement office | `ADMIN` | `require_admin` (the one console gate) | every student; one account per deployment |

"Mentor" is a stored value and a relationship, never the name of an account kind —
the account is Faculty, and it mentors nobody until the Main Admin assigns it a
student. "Placement office", "placement cell" and "TPO" on the screens all mean the
Main Admin. DIRECTOR is not a role (2026-09-10). `AGENTS.md` "Who is who" is the
reference.

### Rule 2 — staff scope is decided by role, never by a missing field

```mermaid
flowchart LR
    RQ["request + reep_session"] --> DP["deps.get_current_session<br/>(HTTP) / get_ws_session (WS)"]
    DP --> RM{"require_mentor"}
    RM -->|"MENTOR (Faculty) ✓ ADMIN (Main Admin) ✓"| SC["_assert_can_access_student()<br/>in routers/mentor.py"]
    RM -->|"STUDENT ✗"| F403["403"]
    SC --> M1{"role"}
    M1 -->|"MENTOR — a faculty member"| G1["only students in their OWN Mentor group<br/><b>no group ⇒ NOBODY</b> (never 'whole programme')"]
    M1 -->|"ADMIN — the Main Admin"| G2["all students"]
    RD{"require_admin"} -->|"ADMIN ✓ only — the one console gate"| G2
```

---

## 6. Rule 1 — the student-data egress gate

```mermaid
flowchart TB
    CALL["any path that would send a student's<br/>name · USN · marks · attendance · resume text"]
    CALL --> API["complete_chat(messages, carries_student_data=True)<br/>stream_chat(...)  — app/ai/llm.py"]
    API --> CFG["llm_config(): explicit LLM_BASE_URL+LLM_MODEL+LLM_API_KEY<br/>else first configured provider in order"]
    CFG --> GATE{"student_data_egress_allowed(base_url)"}
    GATE -->|"hostname ∈ {127.0.0.1, localhost, ::1, 0.0.0.0}"| OK["ALLOWED — loopback always"]
    GATE -->|"remote AND LLM_ALLOW_REMOTE_STUDENT_DATA=true"| OK
    GATE -->|"remote otherwise"| NO["StudentDataEgressRefused — refused<br/>BEFORE anything leaves the process"]
    NO --> DET["/student/resume/generate composes the resume<br/><b>DETERMINISTICALLY</b> and returns used_ai=false"]
    OK --> POST["POST {base_url}/chat/completions"]

    subgraph order["Auto-select order (each OpenAI-compatible)"]
      P1["1 sakana   api.sakana.ai/v1 · fugu-ultra"]
      P2["2 groq     api.groq.com/openai/v1 · llama-3.3-70b-versatile"]
      P3["3 mistral  api.mistral.ai/v1 · mistral-small-latest"]
      P4["4 openrouter openrouter.ai/api/v1 · llama-3.3-70b-instruct:free"]
      P5["5 gemini   generativelanguage.googleapis.com/v1beta/openai · gemini-2.5-flash"]
      P6["6 cohere   api.cohere.ai/compatibility/v1 · command-r"]
    end
    CFG -.-> order
```

`LLM_BASE_URL` is a URL, **not a promise** — it may point at a free tier that trains on
submissions. Public data (a job posting, an approved KB chunk) does not need the gate;
a resume brief does. Route every *new* student-PII-to-model path through it.
`resume_pdf.py` is local ReportLab rendering — no network, so the gate does not apply,
and nothing remote may be added to that module.

---

## 7. The mock interviewer (Interview Engine v3) — the deep detail

```mermaid
sequenceDiagram
    autonumber
    participant S as Student (Angular)
    participant WS as routers/interview.py
    participant R as interview_relay.py
    participant OA as OpenAI Realtime (wss)
    participant DB as Postgres

    S->>WS: GET /api/interview/status
    WS-->>S: {available} — blank OPENAI_API_KEY ⇒ unavailable
    S->>WS: WS /api/interview?specialization=hr|dm|ba|fa
    WS->>WS: Origin ∈ WEB_ORIGIN? else close 4003
    WS->>WS: reep_session cookie → STUDENT? · per-user cap · worker cap
    WS->>DB: live interview_consents row for INTERVIEW_CONSENT_VERSION?
    Note over WS,DB: opening FAILS CLOSED — no readable grant ⇒ close 4013
    WS->>DB: INSERT interview_sessions (status='running', consent_id=…)
    WS->>R: hand off the socket
    R->>OA: connect + Authorization: Bearer OPENAI_API_KEY
    OA-->>R: session.created
    R->>OA: session.update {instructions, voice, turn_detection.create_response=false,<br/>input_audio_transcription}
    OA-->>R: session.updated  (else close 4002)
    R->>OA: response.create  ← the FIRST of exactly one call site

    loop each student answer
        S->>R: binary PCM16 24 kHz frames → input_audio_buffer.append
        OA-->>R: speech_started / speech_stopped / input_audio_buffer.committed
        OA-->>R: conversation.item.input_audio_transcription.completed | .failed
        Note over R: wait on a DEADLINE (INTERVIEW_TRANSCRIPTION_TIMEOUT_MS=8000)
        R->>R: deterministic word-count gate (INTERVIEW_MIN_ANSWER_WORDS=4)<br/>— no hot-path model call
        R->>R: InterviewStateMachine.advance() on an ACCEPTED answer
        R->>OA: instructions-only session.update (phase directive)
        R->>OA: <b>one</b> response.create — ONE call site, or the invariant is gone
        OA-->>R: audio deltas → S (playback scheduler)
        R--)DB: fire-and-forget: messages(channel='interview') + interview_turns
    end

    Note over R,OA: wrap_up → model SPEAKS the verdict
    R->>OA: one further TEXT-ONLY response.create → strict-JSON scorecard
    OA-->>R: JSON (parsed defensively · never spoken · never in chat history)
    R->>DB: interview_evaluations (scores NULLABLE — missing ≠ zero)
    R->>DB: UPDATE interview_sessions SET status=<terminal>, audio_* … WHERE status='running'
```

### Phase machine (`app/interview_matrix.py`)

```mermaid
stateDiagram-v2
    [*] --> opening
    opening --> probing : accepted answer
    opening --> wrap_up : force
    probing --> deep_dive : accepted answer
    probing --> wrap_up : force
    deep_dive --> wrap_up : accepted answer
    wrap_up --> ended
    opening --> ended
    probing --> ended
    deep_dive --> ended
    ended --> [*]
```

Specializations, from the matrix — an unknown key is **refused (4010)**, and *no*
`?specialization=` runs the generic interview, which never reaches wrap-up and so
never produces a report:

| key | label | persona |
|---|---|---|
| `hr` | Human Resources (HR) | senior HR / people-function interviewer |
| `dm` | Digital Marketing (DM) | a growth-oriented, data-driven CMO |
| `ba` | Business Analytics (BA) | a highly technical, problem-solving Director of Analytics |
| `fa` | Financial Analytics (FA) | a sharp, risk-conscious Managing Director / CFO |

### WebSocket close-code vocabulary (one list the client mirrors)

| Code | Meaning |
|---|---|
| 1000 | interview complete |
| 1001 | server shutting down |
| 1011 | unexpected error on our side |
| 1012 | ASGI server restarting (client has a sentence for it) |
| 1013 | per-worker concurrency cap (`INTERVIEW_MAX_SESSIONS`, default 100) |
| 4001 | not configured / upstream 401 |
| 4002 | upstream 403/429/5xx/handshake failure |
| 4003 | Origin not in `WEB_ORIGIN` |
| 4008 | idle — no inbound audio (`INTERVIEW_IDLE_SECONDS`, 120) |
| 4009 | hard wall-clock cap (`INTERVIEW_MAX_SECONDS`, 900) |
| 4010 | unknown `?specialization=` |
| 4011 | turn stalled — *our* `response.create` never acknowledged (deliberately not 4002) |
| 4012 | this student already holds `INTERVIEW_MAX_SESSIONS_PER_USER` (2) — deliberately not 1013 |
| 4013 | no live consent row → refused **before anything is written** |
| 4014 | consent revoked mid-interview → heartbeat notices within a minute |

### Three finalization layers, one idempotency predicate

```mermaid
flowchart LR
    L1["Layer 1 — relay finalizer<br/>(normal end)"] --> P
    L2["Layer 2 — router finally:<br/>(exception / disconnect)"] --> P
    L3["Layer 3 — retention orphan sweeper at boot<br/>(kill -9, OOM, container cut)<br/>grace INTERVIEW_ORPHAN_GRACE_SECONDS=1200"] --> P
    P["all three UPDATE … WHERE status = 'running'<br/>⇒ idempotent against each other"]
    P --> T["a 'running' row that is never closed is a record that LIES"]
```

### Consent — a row, three booleans, two fail directions

`interview_consents` carries **three separate booleans** (live AI · store transcript ·
store audio) because they are three different disclosures and one boolean makes
"they consented" unfalsifiable. `interview_sessions.consent_id` pins the exact grant,
so *"was this student consented, to what wording, at the time of interview X"* stays
answerable after revocation. **Opening fails closed; the mid-interview check fails
open** — "we could not check whether they agreed" must never *start* an interview, and
a database hiccup must never *end* one a real grant authorised.

### Audio — off, and "off" is two independent switches

`INTERVIEW_RECORDING_ENABLED=true` **and** a live grant with `scope_store_audio`.
Neither is true in a default deployment. Then `app/interview_audio.py` writes
**two WAV files per interview, one per speaker, never mixed** (the directions are not
time-aligned) — RIFF/WAVE around the PCM16 LE mono 24 kHz already crossing the relay,
stdlib `wave`, no encoder, no transcode. Capped at `INTERVIEW_RECORDING_MAX_BYTES`
(64 MB) with a **truncation flag rather than a silent cut**. Retrievable only by
the Main Admin (role ADMIN) — a faculty member only by an explicit
`admin.interview_audio` grant. Deleted on the same **180-day** clock (`INTERVIEW_RETENTION_DAYS`).
**Branch on `interview_sessions.audio_recorded`, never on `audio_path IS NOT NULL`** —
a NULL path collapses four different facts into one.

### Interview record tables — *in addition to* `messages`, never instead

| Table | Holds |
|---|---|
| `interview_sessions` | terminal status, `terminal_reason`, `final_phase`, `answers_accepted`, **`turns_emitted` vs `turns_persisted`**, `close_code`, `conn_id`, `upstream_session_id`, `consent_id`, five `audio_*` columns, `started_at/heartbeat_at/ended_at`, `retention_until`, `deleted_at` |
| `interview_turns` | `seq`, `speaker`, **`phase`**, `content` (empty is legal), `transcription_status`, `answer_quality`, **`counted_as_answer`**, `is_partial`, `provider_turn_id`, `message_id` |
| `interview_evaluations` | `report_status`, **nullable** `overall/communication/domain/structure` scores, `strengths`/`improvements` (JSONB), `drill`, `summary`, `raw_response`, `model` |
| `interview_consents` | the versioned three-boolean grant |

---

## 8. Voice stack (retained, mounted, **no UI caller**)

```mermaid
sequenceDiagram
    autonumber
    participant S as Student
    participant A as /api/voice
    participant LK as LiveKit
    participant W as voice_agent.py
    participant GR as Groq

    W->>A: POST /api/voice/heartbeat {worker_id} every ~15 s (X-Worker-Secret)
    A->>A: upsert voice_worker_heartbeats
    S->>A: GET /api/voice/status
    A-->>S: worker_healthy=false ⇒ POST /token returns 409<br/>missing LIVEKIT_*/GROQ_API_KEY or VOICE_MAINTENANCE_MESSAGE ⇒ 503
    S->>A: POST /api/voice/token
    A->>A: room AND identity = the server-owned conversation id
    A-->>S: short-TTL LiveKit AccessToken (VOICE_MAX_SESSIONS_PER_USER=2)
    S->>LK: join room (livekit-client)
    W->>LK: join as the named agent
    S->>W: audio
    W->>GR: BVC → Silero VAD → whisper-large-v3-turbo → llama-3.3-70b-versatile → TTS
    W--)A: POST /api/voice/transcript (FIRE-AND-FORGET, both sides, final only)
    A->>A: conversations.append_message(channel='voice')
    Note over W,A: on SIGTERM: final heartbeat {draining:true} → readiness withdraws in ~1 s<br/>stop_grace_period 960 s = drain 900 + 2×20 + slack
```

**The runbook everyone needs.** The worst failure in this stack is silent: the call
sounds perfect and the database is empty, because transcript POSTs are *deliberately*
fire-and-forget so a bad write can never kill a live call.

```sql
select channel, count(*), max(created_at) from messages group by channel;
```

No `voice` rows (or a stale `max(created_at)`) means turns are being dropped:

1. **`VOICE_WORKER_SECRET` differs** between API and worker → every POST 401s. The
   worker still connects and answers normally, so nothing looks wrong from outside.
2. **`REEP_API_URL` is wrong** (usually `localhost` from inside a container) → the
   POSTs never arrive.

Both now log `ERROR POST /api/voice/transcript -> HTTP 401: …` **with the status code**.
The same query answers the interview question — group by `channel` and look for
`interview`; its drops log as `Dropped interview turn`.

**Knock-on effects of the supersession, no longer silent:**
`AGENT_RUNS_COLLECTED = False` in `app/routers/agent.py` gates a **404** on
`POST /api/agent/feedback` naming the supersession, and `collected: false` on
`GET /api/agent/metrics`, so a frozen history is not read as a live zero.
Flip that one constant back to `True` on rollback — no second edit. `voice_turns`
(off `Message.channel`) keeps working.

---

## 9. Configuration surface (`app/config.py`, `pydantic-settings`)

| Group | Keys |
|---|---|
| Core | `DATABASE_URL` · `AUTH_SECRET` · `WEB_ORIGIN` (default `http://localhost:4200`) · `ENV` (default `dev`) · `DOCS_ENABLED` · `AUTH_REVOCATION_CACHE_SECONDS` (60) · `UPLOAD_DIR` |
| Google | `GOOGLE_CLIENT_ID` · `GOOGLE_CLIENT_SECRET` · `GOOGLE_REDIRECT_URI` · `GOOGLE_ALLOWED_DOMAIN` · `ROSTER_EMAIL_DOMAIN` |
| LLM | `LLM_BASE_URL` · `LLM_MODEL` · `LLM_API_KEY` · `LLM_TIMEOUT_MS` (300000) · **`LLM_ALLOW_REMOTE_STUDENT_DATA`** |
| Provider keys | `SAKANA_API_KEY` · `GROQ_API_KEY` · `MISTRAL_API_KEY` · `OPENROUTER_API_KEY` · `GEMINI_API_KEY` · `COHERE_API_KEY` |
| Embeddings | `EMBEDDING_BASE_URL` · `EMBEDDING_MODEL` · `EMBEDDING_API_KEY` |
| LiveKit / voice | `LIVEKIT_URL` · `LIVEKIT_API_KEY` · `LIVEKIT_API_SECRET` · `VOICE_WORKER_SECRET` · `VOICE_MAINTENANCE_MESSAGE` · `VOICE_MAX_SESSIONS_PER_USER` (2) · `VOICE_MAX_CALL_SECONDS` (900) · `VOICE_TTS` (worker, default `groq`) · `REEP_API_URL` (worker) |
| Interview | `OPENAI_API_KEY` · `OPENAI_REALTIME_MODEL` · `OPENAI_REALTIME_BASE_URL` · `OPENAI_REALTIME_VOICE` (`alloy`) · `OPENAI_REALTIME_BETA_HEADER` · `INTERVIEW_MAX_SECONDS` (900) · `INTERVIEW_IDLE_SECONDS` (120) · `INTERVIEW_MAX_SESSIONS` (100) · `INTERVIEW_MAX_SESSIONS_PER_USER` (2) · `INTERVIEW_VAD_THRESHOLD` (0.5) · `INTERVIEW_VAD_PREFIX_PADDING_MS` (300) · `INTERVIEW_VAD_SILENCE_DURATION_MS` (700) · `INTERVIEW_TRANSCRIPTION_MODEL` · `INTERVIEW_TRANSCRIPTION_TIMEOUT_MS` (8000) · `INTERVIEW_RESPONSE_CREATE_TIMEOUT_MS` (10000) · `INTERVIEW_MIN_ANSWER_WORDS` (4) · `INTERVIEW_MAX_CLARIFICATIONS_PER_QUESTION` (1) · `INTERVIEW_REPORT_TIMEOUT_MS` (20000) · `INTERVIEW_RECORDING_ENABLED` (false) · `INTERVIEW_RECORDING_MAX_BYTES` (64000000) · `INTERVIEW_RETENTION_DAYS` (180) · `INTERVIEW_ORPHAN_GRACE_SECONDS` (1200) · `INTERVIEW_CONSENT_VERSION` (`2026-08`) |

### The production boot guard

`Settings.production_boot_failures()` is raised from `main.py`'s lifespan, so **uvicorn
never binds a port** and the log names every problem it found. It fires on:

- `AUTH_SECRET` blank, still the value published in this repo / `.env.example`, an
  obvious placeholder, or **shorter than 32 characters**;
- `DATABASE_URL` still carrying this repo's dev password.

It is a **refusal, not a warning**: `AUTH_SECRET` signs `reep_session`, so a production
host on the repo default is one forged `{"role":"ADMIN"}` cookie away from every
student's marks, attendance and USN — no login, no database row involved. On every
development `ENV` it returns nothing at all, and `tests/test_boot_guard.py` pins that
as hard as it pins the refusal: **a guard that trips on a laptop gets deleted by
whoever is trying to ship that afternoon.**

Three more fail-closed switches keyed on the same dev-env allowlist:
`password_login_allowed` · `insecure_cookies_allowed` · `worker_auth_optional`.
`docs_exposed` mounts `/docs`, `/redoc`, `/openapi.json` in dev and **off in production**
unless `DOCS_ENABLED` is set deliberately — the endpoints stay authenticated either
way; what an open schema hands out is the **map**.

---

## 10. Build, test & CI

```mermaid
flowchart LR
    subgraph ci[".github/workflows/ci.yml — push:main · PR · dispatch, concurrency cancel-in-progress"]
      direction TB
      J1["<b>api</b> — ubuntu-latest<br/>service: pgvector/pgvector:pg17 (NOT stock pg17 —<br/>migration b7e2f4a19c33 runs CREATE EXTENSION vector)<br/>Python 3.14 · pip cache<br/>REEP_REQUIRE_DB=1 ⇒ a skipped DB test is a FAILURE<br/>alembic upgrade head → app.seed → pytest -q"]
      J2["<b>worker-imports</b> — Python 3.12<br/>install requirements-voice.txt into a CLEAN env<br/>exec voice_agent.py — proves the manifest is complete<br/>(it once declared only livekit-agents while importing<br/>groq, silero, noise_cancellation, edge_tts)"]
      J3["<b>web</b> — Node 22 · npm ci<br/>tsc --noEmit -p tsconfig.app.json<br/>ng test --watch=false (vitest)<br/><b>ng build</b> — enforces the bundle budget"]
    end
```

Local commands:

```bash
docker compose up -d                                   # Postgres 17 + pgvector on :5433
cd apps/api-py
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.seed                       # refuses when ENV=prod
.venv/Scripts/python -m uvicorn app.main:app --port 3300
.venv/Scripts/python -m pytest                         # 33 test modules

cd ../web && npx ng serve                              # :4200, proxies /api
cd ../web && npx ng build                              # budget gate
```

**Windows note:** `uvicorn --reload` has wedged a stale worker here — after editing
backend files, kill port 3300 and restart rather than relying on `--reload`.

Backend test modules pin the invariants this document describes:
`test_boot_guard` · `test_egress_gate` · `test_auth_rbac` · `test_sso_contract` ·
`test_google_callback` · `test_seed_guard` · `test_interview_relay` ·
`test_interview_consent_gate` · `test_interview_write_path` · `test_interview_audio` ·
`test_interview_records` · `test_interview_access` · `test_interview_matrix` ·
`test_voice_gates` · `test_voice_transcript` · `test_voice_worker_source` ·
`test_knowledge` · `test_retention` · `test_conversations` · `test_orchestrator` ·
`test_assistant_eval` · `test_metrics` · `test_mailer` · `test_resume_pdf` ·
`test_registration_rules` · `test_feedback` · `test_voice` · `test_voice_worker_core`.

---

## 11. Production composition (`docker-compose.prod.yml`)

```mermaid
flowchart TB
    TLS["TLS terminator / reverse proxy<br/>(REQUIRED — ENV=prod marks the cookie Secure;<br/>without HTTPS the browser drops it and every login<br/>appears to succeed then behaves as logged-out)"]
    subgraph net["compose network 'reep'"]
      DB[("db · pgvector/pgvector:pg17<br/><b>no published port</b><br/>healthcheck pg_isready -d reep_py<br/>limits 2 CPU / 2G")]
      MIG["migrate · runs ONCE, its own unit<br/>python -m alembic upgrade head<br/>restart: no · depends_on db healthy"]
      API["api · Dockerfile (python:3.14-slim, USER 10001)<br/>uvicorn --proxy-headers --forwarded-allow-ips *<br/>--timeout-graceful-shutdown 110 · stop_grace 120 s<br/>healthcheck = /health (LIVENESS only, never /ready)<br/>limits 2 CPU / 1G, reserve 512M<br/>volume reep_uploads → /var/reep/uploads"]
      VW["voice-worker · Dockerfile.voice (python:3.12-slim)<br/>REEP_API_URL=http://api:3300 (service name, not localhost)<br/>no healthcheck — no inbound HTTP surface<br/>stop_grace 960 s · limits 2 CPU / 3G, reserve 1G"]
    end
    TLS --> API
    MIG -->|"service_completed_successfully"| API
    DB --> MIG
    DB --> API
    API --> VW
```

Every value without a default uses `${VAR:?set VAR}` — compose fails loudly on a
missing one rather than starting a half-configured stack. **Migrations never run from
the API entrypoint**: every replica running `alembic upgrade head` on boot races on the
version table, and the loser can leave the schema half-applied. Health-checking `/ready`
instead of `/health` would restart every API container during a brief Postgres wobble
and turn a recoverable blip into an outage.

---

## 12. Cross-cutting invariants — the short list

| # | Invariant | Enforced by |
|---|---|---|
| 1 | Student PII never reaches a remote model unbidden | `student_data_egress_allowed()` in `app/ai/llm.py`; `/student/resume/generate` degrades to `used_ai=false` |
| 2 | Staff scope is role-decided; a MENTOR with no group sees **nobody** | `require_mentor` / `require_admin` / `_assert_can_access_student` |
| 3 | Exactly **one** `response.create` call site after the handshake | `app/interview_relay.py` — a second site kills "one open question at a time" and no test would notice |
| 4 | No student transcript is ever composed into model instructions | relay builds persona + specialization block + phase directive **only** |
| 5 | A `running` interview row is always closed | three idempotent layers, one `AND status='running'` predicate |
| 6 | Conversations have exactly one writer | `app/conversations.py`; `app/memory.py` is a tombstone, do not use |
| 7 | Uploads are typed by **magic bytes**, stored under random names | `app/document_store.py` |
| 8 | A mail with a given `dedupe_key` is sent at most once | unique index; the losing racer catches `IntegrityError` |
| 9 | Routes are lazy | `loadComponent` everywhere; the budget in `angular.json` fails CI otherwise |
| 10 | Production refuses repo credentials at boot | `production_boot_failures()` raised from the lifespan |

---

## 13. Migrated away — ignore any lingering reference

Next.js · React · NestJS · Prisma · `server-only` · `apps/api`. That stack was fully
migrated and deleted. `apps/interview-realtime/` — the superseded standalone prototype
with no authentication and no database — was deleted in 2026-09 along with `ollama/`
and `tools/cascade`; the in-process relay is the only interviewer.
