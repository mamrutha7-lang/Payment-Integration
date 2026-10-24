# Chapter 1 — The Stack, End to End

> **HISTORICAL — this book was written against the codebase as it stood in 2026-08
> and has not been maintained since. `AGENTS.md` is the current truth; where this
> chapter and that file disagree, this chapter is wrong.** It is kept for the
> *reasoning*, which is still this codebase's reasoning. `README.md` in this
> directory lists everything the book describes that no longer exists — the
> LiveKit voice stack, the DIRECTOR role, the `openai` interview engine,
> `ThemeService` / `IconComponent` / four of the five `shared/kit/` components.

> This is the keystone chapter. After reading it you should be able to draw REEP on a
> whiteboard from memory, start every process on a clean machine in the right order, trace a
> single HTTP request from a mouse click to a Postgres row and back, and — most importantly —
> know which of the two inviolable rules you are about to break before you break it.

---

## 1. What REEP is, and who uses it

REEP is a placement-readiness dashboard for a college. It exists to answer one question for
one student — *am I employable yet, and what is the next thing I should do about it?* — and to
give the staff above that student the aggregate view of the same question. The domain is
academic records (semester results, CGPA, backlogs, attendance), skills and certifications,
job postings and applications, leave and time-sheets, a resume builder, and a grounded AI
assistant that can be reached by typing or by talking.

Three audiences use it, and the difference between them is the second of the two rules this
chapter ends with. A **STUDENT** sees exactly their own record and nothing else. A **MENTOR**
is staff, and sees the students in *their own mentor group* — not the programme. A
**DIRECTOR** (and **ADMIN**, which shares the director's home screen and permissions) sees
every student's marks, attendance and USN. That escalation is why the director account is
treated as radioactive in the seed script, and why the phrase "a MENTOR with no mentor group
sees nobody" appears three times in this book.

### Where the role vocabulary comes from

The four role names are a Python `str` enum on the server
([app/models/user.py:23-27](apps/api-py/app/models/user.py#L23-L27)):

```python
class Role(str, enum.Enum):
    STUDENT = "STUDENT"
    MENTOR = "MENTOR"
    DIRECTOR = "DIRECTOR"
    ADMIN = "ADMIN"
```

Subclassing `str` matters more than it looks: it means `Role.STUDENT == "STUDENT"` is true, so
the same value survives being written into a JWT claim, read back as a plain string, and
compared against a literal — which is exactly what every guard in this codebase does.

The role reaches the browser through the session payload built by `_payload_for`
([auth.py:29-40](apps/api-py/app/routers/auth.py#L29-L40)):

```python
def _payload_for(user: User) -> dict:
    payload = {
        "userId": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role.value,
    }
    if user.student is not None:
        payload["studentId"] = user.student.id
    if user.mentor is not None:
        payload["mentorId"] = user.mentor.id
    return payload
```

Read the two `if`s carefully, because §4 and §6 hang real authorisation decisions on them.
`studentId` is present **only** when the user row has a linked `Student`, and `mentorId` only
when it has a linked `Mentor`. That is why "is this caller a student?" is answered by asking
whether the `studentId` claim exists, and why "which students may this mentor see?" is answered
by the presence — or absence — of `mentorId`. Those two conditional keys are the entire
mechanism behind both trust boundaries.

The client mirrors the union verbatim in
[apps/web/src/app/core/session.ts:8](apps/web/src/app/core/session.ts#L8):

```ts
export type Role = 'STUDENT' | 'MENTOR' | 'DIRECTOR' | 'ADMIN';
```

One caveat before you go looking for the staff screens. The mentor and director surfaces exist
in the **API** today (`/api/mentor`, `/api/director` are fully implemented routers); in the
Angular app **15 of the 17** staff routes are still placeholders — `placeholder('mentor', 'Cohort')`,
`placeholder('director', 'Analytics')` and thirteen siblings
([app.routes.ts:135-161](apps/web/src/app/app.routes.ts#L135-L161)) — each of which lazily
loads one shared `PlaceholderComponent`
([app.routes.ts:25-32](apps/web/src/app/app.routes.ts#L25-L32)). The two exceptions are
`mentor/assistant` ([app.routes.ts:141-145](apps/web/src/app/app.routes.ts#L141-L145)) and
`director/assistant` ([app.routes.ts:156-160](apps/web/src/app/app.routes.ts#L156-L160)), which
load the real `AssistantComponent`. Staff functionality in REEP is
an API contract with a built UI pending, not a built UI. Only `login`, `register`, `assistant`
and the thirteen `student/*` screens are real components.

---

## 2. The four processes

REEP in development is **four operating-system processes on one host**, not one app. Nothing
about that is incidental — each boundary exists because of a specific constraint, and the one
process people forget is the fourth, because nothing in the UI says it exists (see §8).

| # | Process | Command | Port | Language / env |
|---|---|---|---|---|
| 1 | Postgres 17 + pgvector | `docker compose up -d` | host **5433** → container 5432 | `pgvector/pgvector:pg17`, container `reep-postgres` |
| 2 | FastAPI API | `.venv/Scripts/python -m uvicorn app.main:app --port 3300` | **3300** | Python **3.14**, venv `apps/api-py/.venv` |
| 3 | Angular dev server | `npx ng serve` | **4200** | Node **22** in CI ([ci.yml:124](.github/workflows/ci.yml#L124)); `apps/web/package.json` has **no `engines` pin**, so nothing in the repo enforces it locally |
| 4 | Voice worker | `.venv-voice/Scripts/python voice_agent.py dev` | **none** (dials out) | Python **3.12**, venv `apps/api-py/.venv-voice` |
| 5 | *(optional)* Ollama | `ollama serve` | `127.0.0.1:11434` | Local model host — see below |

### The fifth, optional process, and why it matters

A local [Ollama](ollama/reep-gemma3.Modelfile) server is wholly optional and matters far out of
proportion to its optionality, because **a loopback model is the only LLM endpoint that may
receive student records without an explicit override** (§6). The repo ships exactly one
artefact for it, [ollama/reep-gemma3.Modelfile](ollama/reep-gemma3.Modelfile). The port 11434
is Ollama's own default, and **nothing in REEP's code derives it**: the adapter's actual
provider table — `_PROVIDERS` ([app/ai/llm.py:61-70](apps/api-py/app/ai/llm.py#L61-L70)), the
list auto-select walks — has no Ollama or loopback entry at all. The port appears in this repo
only as an illustrative row in the adapter's module **docstring**
([app/ai/llm.py:6-12](apps/api-py/app/ai/llm.py#L6-L12)), i.e. documentation, not a constant:

```
    http://127.0.0.1:11434/v1                    <ollama model>                 local Ollama / LM Studio
```

To route REEP at it, set the **explicit trio minus the key**:

```
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=<the model you pulled>
```

No `LLM_API_KEY` is needed, and that is not an accident of leniency —
[llm.py:88](apps/api-py/app/ai/llm.py#L88) waives it for exactly this case:

```python
    if base and model and (key or is_loopback(base)):
```

A remote base URL with no key falls through to auto-select; a loopback base URL with no key is
accepted as configured.

```mermaid
flowchart LR
  subgraph Browser["Browser (localhost:4200)"]
    SPA["Angular SPA<br/>apiBase = '/api'"]
  end

  subgraph Host["Developer machine"]
    NG["ng serve :4200<br/>proxy.conf.json"]
    API["uvicorn app.main:app :3300<br/>Python 3.14 / .venv"]
    W["voice_agent.py<br/>Python 3.12 / .venv-voice<br/>NO inbound port"]
    OL["ollama :11434<br/>(optional, loopback)"]
  end

  DB[("Postgres 17 + pgvector<br/>reep-postgres :5433<br/>database reep_py")]
  LK["LiveKit Cloud"]
  GQ["Groq / Mistral / …<br/>remote model providers"]

  SPA -->|"fetch /api/... credentials:'include'"| NG
  NG -->|"verbatim, no path rewrite"| API
  API -->|"psycopg 3, pool_pre_ping"| DB
  SPA -.->|"WebRTC, LiveKit AccessToken"| LK
  W -->|"dials OUT, registers as agent 'reep-voice'"| LK
  W -->|"POST /api/voice/heartbeat + /transcript<br/>X-Voice-Worker-Secret, stdlib urllib"| API
  API -.->|"public KB text + the student's<br/>own typed messages —<br/>NO REEP-injected records"| GQ
  API -.->|"student PII allowed here"| OL
```

Read the arrows carefully. **The API never calls the worker, and never calls LiveKit's server
API.** `from livekit import api` at
[voice.py:25](apps/api-py/app/routers/voice.py#L25) is used only to *sign* an `AccessToken`
locally — a pure cryptographic operation on the key pair, with no network call. The worker is
observed indirectly, through a heartbeat row it asks the API to write. That shape is stated in
[Dockerfile.voice:34-36](apps/api-py/Dockerfile.voice#L34-L36):

> *"No EXPOSE and no healthcheck port: the worker has no inbound HTTP surface. It dials out to
> LiveKit and is dispatched by name. Its liveness is observed indirectly, through the heartbeat
> row that GET /api/voice/status reads."*

Note also what the dotted line to the remote providers actually carries. It is **not** "public
KB text only". `/api/agent/chat` sends the caller's own typed conversation to whatever provider
is configured ([agent.py:170](apps/api-py/app/routers/agent.py#L170)); what never crosses that
line is a record REEP *injects* — marks, CGPA, attendance, USN. §6 explains why that
distinction is the whole design and not a loophole.

### The operator's view: what `/ready` actually returns

`/ready` is the only place an operator sees this four-process stack summarised. Its voice block
is [health.py:53-62](apps/api-py/app/routers/health.py#L53-L62):

```python
    # Soft dependency: voice degrades on its own and says so via /api/voice/status.
    checks["voice"] = {
        "livekit_configured": settings.livekit_ready,
        "speech_key_configured": settings.voice_model_key_present,
        # Surfaced because a blank worker secret is an OPEN ingestion endpoint
        # in production — worth seeing on a readiness dashboard, not only in
        # the 500 the endpoint itself returns.
        "worker_auth_configured": bool(settings.voice_worker_secret),
        "maintenance": bool(settings.voice_maintenance_message.strip()),
    }
```

Four keys, each answering a different question:

- **`livekit_configured`** — are all three `LIVEKIT_*` values set? (Without them no token can be
  signed at all.)
- **`speech_key_configured`** — is `GROQ_API_KEY` present? That is the key the *worker's* speech
  cascade needs; see the `voice_model_key_present` discussion in §5.
- **`worker_auth_configured`** — is `VOICE_WORKER_SECRET` non-blank? Hold on to this one: it is
  the exact nuance the §6 callout argues about, surfaced here precisely because the endpoint's
  own 500 is not visible on a dashboard.
- **`maintenance`** — is the kill switch engaged?

None of the four can fail the probe. Only the database check flips `healthy` to false
([health.py:44-51](apps/api-py/app/routers/health.py#L44-L51)).

### What degrades when each is missing

**Postgres down.** `GET /health` still returns 200 — it is deliberately dependency-free
([health.py:27-30](apps/api-py/app/routers/health.py#L27-L30)) — while `GET /ready` reports
`checks["database"] = "error: OperationalError"` and sets 503
([health.py:64-65](apps/api-py/app/routers/health.py#L64-L65)). The split is not fussiness:

> **Why it is like this.** From the module docstring at
> [health.py:13-15](apps/api-py/app/routers/health.py#L13-L15):
> *"Conflating the two is a common outage amplifier: if liveness checks the DB, a brief
> Postgres wobble restarts every API container at once and turns a recoverable dependency blip
> into a full outage."* The production compose healthcheck therefore probes `/health`, never
> `/ready`.

Note also that `/ready` reports the exception **type** only, never its message
([health.py:49-50](apps/api-py/app/routers/health.py#L49-L50)) — a connection string carrying
a password must not leak through a public probe.

**API down.** Everything is down — and it presents as being *logged out*, not as an error. The
mechanism is two files. The route guard asks the service for a session and, failing that,
navigates to `/login` ([auth.guard.ts:15-24](apps/web/src/app/core/auth.guard.ts#L15-L24)):

```ts
export const authGuard: CanActivateFn = async (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  if (auth.isSignedIn()) return true;

  const session = await auth.refresh();
  if (session) return true;

  return router.createUrlTree(['/login'], { queryParams: { next: state.url } });
};
```

And `refresh()` swallows *any* failure — network error, 401, 500, DNS — into the same `null`
([auth.service.ts:46-59](apps/web/src/app/core/auth.service.ts#L46-L59)):

```ts
  async refresh(): Promise<SessionPayload | null> {
    try {
      const session = await firstValueFrom(
        this.http.get<SessionPayload>(`${environment.apiBase}/auth/me`, {
          withCredentials: true,
        }),
      );
      this._session.set(session);
      return session;
    } catch {
      this._session.set(null);
      return null;
    }
  }
```

A dead API and an expired cookie are therefore indistinguishable to the user. That is worth
knowing before you debug a "I keep getting logged out" report. (`withCredentials: true` is
Angular `HttpClient`'s spelling of `fetch`'s `credentials: 'include'` — the same idea in two
APIs. The choice between them splits by **layer, not by call**: the root-provided `core/`
services use `HttpClient` + `withCredentials` at all eleven of their call sites — three in
[auth.service.ts](apps/web/src/app/core/auth.service.ts) (login `:37`, this `refresh()` `:50`,
logout `:63`) and eight in
[chat-voice.service.ts](apps/web/src/app/core/chat-voice.service.ts) (`:221`, `:231`, `:327`,
`:335`, `:355`, `:417`, `:460`, `:551`, covering `/api/agent/history`, `/api/agent/chat`,
`/api/agent/feedback`, `/api/agent/conversation`, `/api/voice/consent`, `/api/voice/status`
and `/api/voice/token`) — while the `features/` components use bare `fetch` +
`credentials: 'include'`.)

**Voice worker down.** Voice, and only voice, is unavailable — and understanding *how* the API
notices requires assembling three pieces that live in two processes.

1. **The worker beats.** A daemon thread inside the worker POSTs `/api/voice/heartbeat` with its
   own `worker_id` every `VOICE_HEARTBEAT_INTERVAL_SECONDS`, default **10**
   ([voice_agent.py:151](apps/api-py/voice_agent.py#L151),
   [voice_agent.py:294-317](apps/api-py/voice_agent.py#L294-L317)). It is a plain
   `threading.Thread(..., daemon=True)`, not an asyncio task, so it is beating before any call
   arrives and keeps beating between calls.
2. **The API writes the row on its behalf.** The worker holds no database connection at all
   (§3). The API upserts one `VoiceWorkerHeartbeat` row per `worker_id`
   ([voice.py:134-143](apps/api-py/app/routers/voice.py#L134-L143)) and opportunistically reaps
   rows older than `HEARTBEAT_REAP_AFTER = timedelta(hours=1)`
   ([voice.py:152-156](apps/api-py/app/routers/voice.py#L152-L156)) so that a fresh random
   `worker_id` per process restart does not grow the table forever.
3. **Readiness asks one question.** `_worker_healthy` asks whether **any** row's `last_seen`
   falls inside a 30-second window
   ([voice.py:174-181](apps/api-py/app/routers/voice.py#L174-L181)):

```python
def _worker_healthy(db: Session) -> bool:
    cutoff = _now() - timedelta(seconds=HEARTBEAT_FRESH_SECONDS)
    fresh = db.scalar(
        select(VoiceWorkerHeartbeat)
        .where(VoiceWorkerHeartbeat.last_seen >= cutoff)
        .limit(1)
    )
    return fresh is not None
```

A 10-second beat inside a 30-second window means one missed beat is not an outage — that
headroom is stated at [voice.py:41-43](apps/api-py/app/routers/voice.py#L41-L43). But note the
word **any**: the query is not scoped to a particular worker. Anyone who can POST a heartbeat
can make voice look available. Hold that thought for §6, where it is the reachable abuse.

Shutdown is not merely going quiet. When the SDK begins draining, the beat loop exits and posts
one final `{"draining": true}` ([voice_agent.py:307-314](apps/api-py/voice_agent.py#L307-L314)),
and the API **deletes** the row rather than tombstoning it
([voice.py:125-132](apps/api-py/app/routers/voice.py#L125-L132)) — withdrawing readiness in
about a second instead of after the full 30-second window, during which students would be handed
tokens into rooms nobody will join.

With no worker at all, `_compute_status` reports `available=False,
reason="Voice worker offline."` ([voice.py:200-201](apps/api-py/app/routers/voice.py#L200-L201))
and `POST /api/voice/token` answers **409**. Every other screen is untouched; `/ready` reports
voice but *never fails on it* ([health.py:38-40](apps/api-py/app/routers/health.py#L38-L40)).

**Angular dev server down.** Irrelevant to the API. The API is reachable directly on 3300,
including its OpenAPI docs at `/docs`.

**Ollama absent (or no provider configured at all).** `POST /api/student/resume/generate`
composes the resume deterministically and reports `used_ai: false`. That is a *successful*
response, not an error: the UI renders a `Deterministic draft` chip
([preview.component.html:129](apps/web/src/app/features/student/resume/views/preview.component.html#L129))
followed by the words `composed on this machine`
([preview.component.html:131](apps/web/src/app/features/student/resume/views/preview.component.html#L131)).

---

## 3. Why the voice worker is a separate process on a different Python

The reason is a single line of package metadata, and it is worth stating flatly because it is
the sort of constraint people try to "clean up".

> **Why it is like this.** [Dockerfile.voice:3-6](apps/api-py/Dockerfile.voice#L3-L6):
> *"Why 3.12 and not the API's 3.14: livekit-agents declares `Requires-Python: <3.15,>=3.10`,
> a ceiling the API does not have, and 3.12 is the version this worker's SDK contract was
> actually verified against. Pinning it here means a future API bump to 3.15 cannot silently
> break the worker."*

So the API cannot import `livekit-agents`, and the worker cannot run on the API's interpreter.
CI enforces the split independently of the Dockerfiles: the backend job runs
`python-version: "3.14"` ([ci.yml:53](.github/workflows/ci.yml#L53)) while the voice job runs
`python-version: "3.12"` ([ci.yml:89](.github/workflows/ci.yml#L89)), with the comment *"The
worker's audio/ML stack wants 3.12, unlike the API."* Two interpreters is not a local quirk; it
is a tested invariant.

Two more constraints reinforce the split.

**Size.** The API image is ~370 MB (dominated by `litellm` and `google-adk`), the worker ~505 MB
(dominated by `livekit` and `onnxruntime`), and merging them
"would ship ~800 MB of site-packages to both a stateless web pod and a long-lived audio pod"
([Dockerfile:3-7](apps/api-py/Dockerfile#L3-L7)).

**Base image.** Neither image is Alpine, but for **two different reasons**, and conflating them
is a mistake. The worker cannot be Alpine because `livekit-plugins-silero` depends on
`onnxruntime`, which publishes no musl wheels at all
([Dockerfile.voice:8-11](apps/api-py/Dockerfile.voice#L8-L11)):

> *"Why NOT Alpine: livekit-plugins-silero depends on onnxruntime, which publishes no musl
> wheels at all. On Alpine the install either fails outright or falls back to a source build
> needing CMake and a C++ toolchain. glibc >= 2.28 (bookworm) is required."*

The API depends on neither silero nor onnxruntime. It avoids Alpine on a completely separate
argument ([Dockerfile:9-11](apps/api-py/Dockerfile#L9-L11)):

> *"glibc base, not Alpine: psycopg has musl wheels but the shared toolchain and
> ca-certificates story is simpler here, and libpq — unlike the worker's TLS — has no certifi
> fallback to lean on."*

### The worker is database-free, by construction

It imports nothing from `app/`; the voice image copies exactly one source file
([Dockerfile.voice:27-29](apps/api-py/Dockerfile.voice#L27-L29)). Its only client dependency is
the standard library, and the manifest says so at
[requirements-voice.txt:62-64](apps/api-py/requirements-voice.txt#L62-L64):

```
# No HTTP client is listed: the worker talks to the FastAPI server with stdlib
# urllib only (POST /api/voice/transcript + /api/voice/heartbeat), so it carries
# no DB deps and no extra HTTP dependency.
```

This is why §2's heartbeat description is careful to say the *API* writes the row. All
persistence policy — final-turns-only, deduplication, length caps — lives on the server, so the
worker stays thin and structurally cannot bypass it.

### It reads the API's `.env` without importing the API's config

Because it runs in its own venv, the worker cannot `import app.config`. It re-implements the
`.env` read in stdlib in `_load_env_file`
([voice_agent.py:87-111](apps/api-py/voice_agent.py#L87-L111)), whose key statement is at
[voice_agent.py:100](apps/api-py/voice_agent.py#L100):

```python
    env_path = Path(__file__).resolve().parent / ".env"
```

Pinned to *this file's* directory for the same reason `app/config.py` pins its own (§5): a bare
`".env"` resolves against the process CWD and would pick up the wrong file when the worker is
started from the repo root. The loader uses `os.environ.setdefault`
([voice_agent.py:111](apps/api-py/voice_agent.py#L111)), so — in the docstring's words — *"A
real environment variable always wins — `setdefault`, never overwrite — so per-process overrides
(REEP_API_URL, VOICE_WORKER_ID) still work."* That is what lets both halves share one
credentials file while still differing where they must.

### One compile-time coupling to know about

The dispatch name is hard-coded on **both** sides: `VOICE_AGENT_NAME = "reep-voice"` at
[voice.py:58](apps/api-py/app/routers/voice.py#L58) and
`@server.rtc_session(agent_name="reep-voice")` at
[voice_agent.py:626](apps/api-py/voice_agent.py#L626).

> **Why it is like this.** [voice.py:53-57](apps/api-py/app/routers/voice.py#L53-L57):
> *"MUST match the agent_name the worker registers under (@server.rtc_session in
> voice_agent.py). Naming an agent opts it OUT of LiveKit's automatic dispatch: a named worker
> never joins a room on its own, so the token has to request it explicitly via
> RoomConfiguration.agents. Without this the student joins, the worker sits idle with no job,
> and the call is silence with no error anywhere."*

`.env.example` explicitly forbids making this an environment variable
([.env.example:97-101](apps/api-py/.env.example#L97-L101)):

> *"Do NOT set LIVEKIT_AGENT_NAME or similar. The dispatch name is compile-time on both sides
> (VOICE_AGENT_NAME in app/routers/voice.py, matched by the worker's registration and pinned by
> a test). Making it an env var invites the two to disagree, and that failure is silent: the
> token mints, the room opens, the microphone publishes, and no agent ever joins."*

---

## 4. The request lifecycle

Take one concrete request: a signed-in student's browser loading the jobs board, i.e.
`GET /api/student/jobs`.

```mermaid
sequenceDiagram
    autonumber
    participant C as JobsComponent
    participant P as ng serve :4200<br/>(proxy.conf.json)
    participant U as uvicorn :3300<br/>FastAPI
    participant D as get_db()<br/>SessionLocal
    participant PG as Postgres reep_py

    C->>P: fetch('/api/student/jobs',<br/>{credentials:'include'})<br/>Cookie: reep_session
    Note over C,P: relative URL ⇒ same-origin ⇒<br/>no CORS, no preflight
    P->>U: GET /api/student/jobs (path verbatim)
    U->>U: route match: "/api" + "/student" + "/jobs"
    U->>U: Depends(get_current_session)<br/>jwt.decode(HS256) → claims dict
    U->>D: Depends(get_db) → SessionLocal()
    U->>U: _require_student(session)<br/>403 if no studentId claim
    U->>PG: 7 SELECTs (Skill⋈StudentSkill,<br/>SemesterResult ×2, JobApplication,<br/>PlacementCriteria, AcademicGap, Job)
    PG-->>U: rows
    U-->>P: 200 JSON, list[JobRowOut]
    P-->>C: 200 JSON
    U->>D: finally: db.close()
```

**Step 1 — the client.** The component calls
`fetch(\`${environment.apiBase}/student/jobs\`, { credentials: 'include' })`
([jobs.component.ts:191](apps/web/src/app/features/student/jobs/jobs.component.ts#L191)), and
`environment.apiBase` is the string `'/api'`
([environment.ts:11](apps/web/src/environments/environment.ts#L11)). Because that is a
*relative* path, the request goes to the dev server's own origin, `http://localhost:4200`.
`credentials: 'include'` attaches the httpOnly `reep_session` cookie — without it the browser
would send the request anonymously and every authenticated call would 401.

This idiom is repeated across the app — **43 occurrences of `credentials: 'include'` across 21
files** under `apps/web/src` at the time of writing — and the house error-handling shape is
worth internalising, because it distinguishes two failures a naive `catch` would fold into one:

```ts
try {
  const res = await fetch(`${environment.apiBase}/student/jobs`, { credentials: 'include' });
  if (!res.ok) {
    this.error.set('Could not load the jobs board.');
    return;
  }
  this.jobs.set((await res.json()) as JobRow[]);
} catch {
  this.error.set('Could not reach the server.');
} finally {
  this.loading.set(false);
}
```

`fetch` rejects only when the request never completed — DNS failure, connection refused, the API
process not running. A 401, 404 or 500 **resolves** with `res.ok === false`. Two branches, two
messages: *"Could not load"* means the server answered and said no; *"Could not reach"* means
nothing answered.

**Step 2 — the dev proxy, and the `/api` prefix scheme.** Here is
[apps/web/proxy.conf.json](apps/web/proxy.conf.json) in full — eight lines, one key:

```json
{
  "/api": {
    "target": "http://localhost:3300",
    "secure": false,
    "changeOrigin": true,
    "ws": true
  }
}
```

There is **no `pathRewrite`**, and that is the whole design. Three facts are arranged so that
nothing anywhere rewrites a path:

- `environment.apiBase === '/api'`;
- the proxy key is `"/api"`;
- every client-callable FastAPI route already begins with `/api`.

(`"ws": true` forwards WebSocket upgrades as well. Nothing in REEP currently needs it: the only
realtime connection is the LiveKit WebRTC session, which the browser makes *directly* to LiveKit
Cloud and which never passes through this proxy — see the dotted `SPA -.-> LK` arrow in §2.)

The API achieves the third fact in two different styles, both documented at the mount site in
[app/main.py:69-82](apps/api-py/app/main.py#L69-L82). Six domain routers declare a bare segment
(`/auth`, `/student`, `/mentor`, `/director`, `/leaves`, `/register`) and are mounted with
`prefix="/api"`. The `agent` and `voice` routers were added later and spell `/api` *inside*
themselves (`APIRouter(prefix="/api/agent")`, `APIRouter(prefix="/api/voice")`), so they are
mounted with no prefix — adding one would produce `/api/api/voice`. The comment exists precisely
so nobody "fixes" the inconsistency and breaks those two.

Health is the deliberate exception: `app.include_router(health.router)` with no prefix at all
([main.py:69-70](apps/api-py/app/main.py#L69-L70)), so `/health` and `/ready` are *not* matched
by the proxy's `"/api"` key. A load balancer talks to :3300 directly; a browser on :4200 cannot
reach them. That is the correct shape for an infrastructure probe — and those two are the **only**
paths in this book you should ever write without the `/api` prefix.

**Step 3 — CORS, which in normal development is dead code.** The only middleware in the entire
application is [app/main.py:60-67](apps/api-py/app/main.py#L60-L67):

```python
# Credentials are sent (the session cookie), so the origin must be explicit, not "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

The comment states a browser-enforced hard rule, not a preference: the CORS specification
forbids `Access-Control-Allow-Origin: *` together with `Access-Control-Allow-Credentials: true`,
so a wildcard would strip the cookie from every cross-origin request. But in the normal dev
workflow the proxy makes the browser see the API as same-origin, so no preflight ever occurs and
this middleware never fires. It exists for the deployment where the SPA is served from a
genuinely different origin — which is why `WEB_ORIGIN` is configurable at all.

There is no auth middleware, no request-id middleware, no rate limiter and no custom exception
handler. Authentication is a per-route dependency; error shaping is FastAPI's default
(`HTTPException` → `{"detail": …}`, validation failure → 422).

### An aside you cannot skip: what `Depends(...)` actually does

Steps 4, 5 and 6 are built entirely on FastAPI's dependency injection, so here is the mechanism
before the vocabulary.

FastAPI inspects the handler's **signature**. Any parameter whose default value is `Depends(f)`
means: *call `f` first, and pass its return value in as that parameter.* The handler never
fetches anything itself. In

```python
def my_jobs(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[JobRowOut]:
```

([student.py:546-548](apps/api-py/app/routers/student.py#L546-L548)) both `get_current_session`
and `get_db` run **before** a single line of the handler body, in the order they are declared,
and either one can abort the whole request by raising `HTTPException`. That is why a missing
cookie produces a 401 without `my_jobs` ever executing, and it is why the parameter named
`session` is a plain `dict` — it is whatever `get_current_session` returned.

A dependency written as a **generator** gets a second half. Everything before its `yield` runs on
the way in; the yielded value is what the handler receives; everything after the `yield` runs
once the response is finished. That is the entire mechanism behind "one Session per request,
always closed": nothing in the handler closes the session, and nothing has to.

**Step 4 — the session cookie.** `Depends(get_current_session)` is six lines
([app/identity.py:8-13](apps/api-py/app/identity.py#L8-L13)):

```python
def get_current_session(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    payload = verify_session_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required.")
    return payload
```

Note that a missing cookie and a bad cookie collapse into the *same* 401 `"Sign in required."`.
Verification is `jwt.decode(token, settings.auth_secret, algorithms=["HS256"])` wrapped in
`except jwt.PyJWTError: return None`
([security.py:51-55](apps/api-py/app/security.py#L51-L55)) — so a forged signature, a malformed
token and an expired token all become `None` and then the same 401. The explicit
`algorithms=["HS256"]` list is the standard defence against the `alg: none` /
algorithm-confusion class of attack: without it, a library may accept a token that tells it
which algorithm to use.

The cookie is set once, by `POST /api/auth/login`
([auth.py:68-76](apps/api-py/app/routers/auth.py#L68-L76)):

```python
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
        max_age=SESSION_TTL_SECONDS,
    )
```

`secure=settings.is_prod` is what makes dev work over plain HTTP and production refuse to send
the cookie over anything but TLS. `max_age` is the same `SESSION_TTL_SECONDS = 60 * 60 * 12`
(12 hours) used for the JWT's own `exp`
([security.py:21](apps/api-py/app/security.py#L21),
[security.py:47](apps/api-py/app/security.py#L47)), so the browser drops the cookie at the moment
the token stops verifying — there is no window in which a stale cookie is sent and 401s. There is
**no server-side session store and no revocation list**: `logout` is
`response.delete_cookie(SESSION_COOKIE, path="/")` and nothing more
([auth.py:85-88](apps/api-py/app/routers/auth.py#L85-L88)), so a token captured before logout
stays valid until its 12-hour expiry. That is a real property of the design, not an oversight.

Login itself folds both failure modes into one message
([auth.py:47-52](apps/api-py/app/routers/auth.py#L47-L52) — *"One message for both cases — never
reveal which of email/password was wrong"*), and passwords are `scrypt:salt:digest` with
`N=16384, r=8, p=1, dklen=64`, the salt hashed **as its hex string** (`salt.encode()`, not
`bytes.fromhex(salt)`) so hashes minted by Node's `scryptSync` verify unchanged
([security.py:28-42](apps/api-py/app/security.py#L28-L42)). Comparison is
`hmac.compare_digest`, constant-time.

**Step 5 — the database session.** `get_db()` is the generator dependency described above
([app/db.py:24-30](apps/api-py/app/db.py#L24-L30)):

```python
def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

Three details of the engine shape how handlers are written
([db.py:20-21](apps/api-py/app/db.py#L20-L21)):

```python
engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
```

The engine is **synchronous**, which is why nearly every handler in this codebase is a plain
`def` rather than `async def` — FastAPI then runs it in a threadpool, and the blocking psycopg
calls never stall the event loop. (An `async def` handler making blocking database calls would
block the loop for every other request; the plain `def` is the correct pairing, not laziness.)
`pool_pre_ping=True` issues a cheap liveness check before handing out a pooled connection,
preventing the classic stale-connection failure after a Postgres restart. `autoflush=False`
means a pending `db.add()` is *not* flushed before a later `select()`, so code needing a
generated primary key mid-transaction must call `db.flush()` explicitly; `autocommit=False`
means every writing handler calls `db.commit()` itself.

**Step 6 — the handler.** `my_jobs` at
[student.py:545-548](apps/api-py/app/routers/student.py#L545-L548) calls
`_require_student(session)` first
([student.py:118-122](apps/api-py/app/routers/student.py#L118-L122)):

```python
def _require_student(session: dict) -> str:
    student_id = session.get("studentId")
    if not student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a student account.")
    return student_id
```

This is the `studentId` claim from §1 doing its job. A director's cookie is perfectly valid — it
authenticates fine, so no 401 — but carries no `studentId`, hence **403**, not 401. Authenticated
is not authorised.

It then issues **seven** database round-trips before it loops
([student.py:553-595](apps/api-py/app/routers/student.py#L553-L595)):

1. the student's skill slugs (`Skill` joined to `StudentSkill`);
2. the latest `SemesterResult`, for CGPA;
3. a `func.sum` over `SemesterResult.live_backlogs`;
4. the applied `JobApplication.job_id` set;
5. the active `PlacementCriteria` row, supplying defaults when a posting has no override;
6. `db.get(AcademicGap, student_id)`, which feeds the `max_gap_months` eligibility reason;
7. `select(Job).order_by(Job.posted_on.desc())` — the postings themselves.

All seven are issued **eagerly, per request, with no caching layer of any kind**. That is the
shape you should copy into the next handler you write: fetch the small reference sets up front,
then decide in Python. The per-row eligibility verdict and skill-match percentage are computed in
the loop that follows, not in SQL.

The handler returns `list[JobRowOut]`; Pydantic serialises against the declared `response_model`;
`get_db`'s `finally` closes the session. A read-only handler never commits, and the uncommitted
transaction is simply discarded on close.

---

## 5. Configuration

All settings live in one pydantic-settings class,
[apps/api-py/app/config.py](apps/api-py/app/config.py), instantiated exactly once as a
process-wide singleton at [config.py:152](apps/api-py/app/config.py#L152). **Field names map to
environment variables case-insensitively** (`database_url` ← `DATABASE_URL`), so the field name
*is* the operator-facing variable name: renaming a field renames a deployment variable.

The file an operator actually edits is `apps/api-py/.env`, which does not exist in a fresh
checkout. [apps/api-py/.env.example](apps/api-py/.env.example) is the template, and its first
line says so: *"FastAPI backend (fresh Python schema). Copy to apps/api-py/.env and fill in."*
§8 step 2a shows the copy.

The env file is pinned by absolute path, and the comment records exactly why
([config.py:10-14](apps/api-py/app/config.py#L10-L14)):

```python
# Pin the env file to THIS app's directory. A bare ".env" resolves against the
# process CWD, which — run from the repo root — is the Next.js/Prisma .env, whose
# `postgresql://…?schema=public` URL selects psycopg2 (not installed) and carries
# a Prisma-only query param. This app reads its own file or nothing.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
```

> **Why it is like this.** That hazard is still live. A legacy Prisma-era `.env` remains at the
> repo root today and still contains `schema=public`. Without this pin, running `uvicorn` from
> the repo root would pick it up and fail on a driver that is not installed. The companion
> setting `extra="ignore"` at [config.py:18](apps/api-py/app/config.py#L18) is what allows the
> single `apps/api-py/.env` file to carry worker-only keys (`REEP_API_URL`, `VOICE_TTS`) and
> stale ones without pydantic raising.

### Every `Settings` field

All twenty-four, in declaration order.

| Field | Env var | Default | Controls |
|---|---|---|---|
| `database_url` | `DATABASE_URL` | `postgresql+psycopg://reep:reep_dev_password@localhost:5433/reep_py` | The Postgres DSN. Note the default already names the `+psycopg` driver, host port 5433 and database **`reep_py`**. |
| `auth_secret` | `AUTH_SECRET` | `reep-dev-secret-change-me-in-production-0123456789abcdef` | HS256 signing key for the `reep_session` JWT. |
| `web_origin` | `WEB_ORIGIN` | `http://localhost:4200` | The single allowed CORS origin. Cannot be `*` — credentials are sent. |
| `env` | `ENV` | `dev` | `prod` flips the `Secure` cookie flag, the voice-worker fail-closed branch, and the seed refusal. |
| `llm_base_url` | `LLM_BASE_URL` | `""` | Explicit OpenAI-compatible endpoint. |
| `llm_model` | `LLM_MODEL` | `""` | Explicit model id. Ignored in auto-select mode. |
| `llm_api_key` | `LLM_API_KEY` | `""` | Explicit key; optional when the base URL is loopback ([llm.py:88](apps/api-py/app/ai/llm.py#L88)). |
| `llm_timeout_ms` | `LLM_TIMEOUT_MS` | `300000` | Per-call model timeout (5 min), floored at 1 s by `_timeout_s()` ([llm.py:73-74](apps/api-py/app/ai/llm.py#L73-L74)). |
| `llm_allow_remote_student_data` | `LLM_ALLOW_REMOTE_STUDENT_DATA` | `""` | **The egress override.** Opens the gate only when it reads `true`. |
| `groq_api_key` | `GROQ_API_KEY` | `""` | Auto-select provider key; also the voice cascade's key. |
| `mistral_api_key` | `MISTRAL_API_KEY` | `""` | Auto-select provider key; also auto-selects the KB embedder. |
| `openrouter_api_key` | `OPENROUTER_API_KEY` | `""` | Auto-select provider key. |
| `cohere_api_key` | `COHERE_API_KEY` | `""` | Auto-select provider key. |
| `gemini_api_key` | `GEMINI_API_KEY` | `""` | Auto-select provider key. |
| `sakana_api_key` | `SAKANA_API_KEY` | `""` | Auto-select provider key — checked **first** ([llm.py:62-64](apps/api-py/app/ai/llm.py#L62-L64)), as Sakana Fugu is itself a router. |
| `embedding_base_url` | `EMBEDDING_BASE_URL` | `""` | Explicit embeddings endpoint. Blank ⇒ KB retrieval is full-text only. |
| `embedding_model` | `EMBEDDING_MODEL` | `""` | Explicit embedding model id. |
| `embedding_api_key` | `EMBEDDING_API_KEY` | `""` | Explicit embeddings key (optional). |
| `livekit_url` | `LIVEKIT_URL` | `""` | LiveKit Cloud project URL, returned to the browser with the token. |
| `livekit_api_key` | `LIVEKIT_API_KEY` | `""` | Token-signing key. |
| `livekit_api_secret` | `LIVEKIT_API_SECRET` | `""` | Token-signing secret. |
| `voice_worker_secret` | `VOICE_WORKER_SECRET` | `""` | Shared secret on `X-Voice-Worker-Secret`. Blank ⇒ open in dev, **500** in prod. |
| `voice_maintenance_message` | `VOICE_MAINTENANCE_MESSAGE` | `""` | Kill switch. Non-blank forces voice unavailable and is shown verbatim to the student. |
| `upload_dir` | `UPLOAD_DIR` | `""` | On-disk file store. Blank ⇒ `apps/api-py/var/uploads` (gitignored). |

### Every derived property

All seven `@property` members on the same class, with their real consumers.

| Property | Definition | Used by |
|---|---|---|
| `gemini_key_present` ([config.py:68-77](apps/api-py/app/config.py#L68-L77)) | field **or** raw `GEMINI_API_KEY` **or** raw `GOOGLE_API_KEY` | **Nothing — no call site anywhere in the codebase.** `grep -rn "gemini_key_present" apps/api-py` returns exactly one hit: its own definition. It is the only code that would honour `GOOGLE_API_KEY`, left behind by the retired native speech-to-speech path that `voice_model_key_present` replaced. Dead, and interesting *because* its sibling explicitly documents why the Gemini key must not be consulted. |
| `voice_model_key_present` ([config.py:79-90](apps/api-py/app/config.py#L79-L90)) | `groq_api_key` **or** raw `os.getenv("GROQ_API_KEY")` | `_compute_status` ([voice.py:187](apps/api-py/app/routers/voice.py#L187)), `/ready` ([health.py:56](apps/api-py/app/routers/health.py#L56)) |
| `livekit_ready` ([config.py:92-94](apps/api-py/app/config.py#L92-L94)) | all three `LIVEKIT_*` truthy | `_compute_status`, `/ready` |
| `is_prod` ([config.py:100-102](apps/api-py/app/config.py#L100-L102)) | `env.lower() == "prod"` | cookie `Secure` ([auth.py:73](apps/api-py/app/routers/auth.py#L73)), `require_voice_worker` ([voice.py:82](apps/api-py/app/routers/voice.py#L82)), the `lifespan` warning ([main.py:48](apps/api-py/app/main.py#L48)), the seed refusal |
| `uploads_path` ([config.py:104-109](apps/api-py/app/config.py#L104-L109)) | `Path(upload_dir)` or `apps/api-py/var/uploads` | `app/document_store.py` |
| `allow_remote_student_data` ([config.py:111-113](apps/api-py/app/config.py#L111-L113)) | `llm_allow_remote_student_data.strip().lower() == "true"` | the egress gate ([llm.py:110](apps/api-py/app/ai/llm.py#L110)) |
| `sqlalchemy_url` ([config.py:119-149](apps/api-py/app/config.py#L119-L149)) | normalised DSN (below) | `db.py:20`, `migrations/env.py:17` |

### The four non-obvious ones

**`llm_allow_remote_student_data` is a `str`, not a `bool`.** This looks like sloppiness and is
not. [config.py:32-33](apps/api-py/app/config.py#L32-L33): *"A string (not bool) so a blank
value is valid and safely means 'off', matching the Next.js gate where only the exact string
'true' enables it."* A `bool` field would make pydantic *reject* an empty value outright —
crashing boot on a blank line — and would also accept `1`, `yes` and `on`. The `str` keeps
blank-means-off valid and narrows the opt-in. **Precise behaviour, from the code rather than the
comment:** the property lowercases after stripping
([config.py:113](apps/api-py/app/config.py#L113)), so ` TRUE ` also opens the gate. State that,
not the stricter claim.

**`voice_model_key_present` deliberately ignores the Gemini key.** Its docstring
([config.py:81-87](apps/api-py/app/config.py#L81-L87)) — reproduced with the file's own ASCII
arrows — reads:

```
        Voice runs as a cascade (silero VAD -> Groq Whisper -> Groq Llama ->
        TTS), so GROQ_API_KEY is what makes it work. This deliberately does NOT
        check the Gemini key: that was the old native speech-to-speech path, and
        gating on it would report voice "not configured" on a machine where it
        runs perfectly — or, worse, report it ready on one where it cannot.
```

That retired path is also what `gemini_key_present` was for, which is why it is now unreferenced.

**`sqlalchemy_url` is a URL launderer, and it hides the worst near-miss in the repo.** It
rewrites a bare `postgresql://` to `postgresql+psycopg://` and then strips *only* the params in
`_PRISMA_ONLY_PARAMS = frozenset({"schema", "connection_limit", "pgbouncer"})`
([config.py:117](apps/api-py/app/config.py#L117)).

> **Why it is like this.** [config.py:127-134](apps/api-py/app/config.py#L127-L134):
> *"It drops ONLY those. This used to end `return url.split("?", 1)[0]`, discarding the entire
> query string — which silently threw away `sslmode`. Every managed Postgres (Neon, RDS,
> Supabase, Cloud SQL) hands you `...?sslmode=require`, so the connection fell back to libpq's
> default `prefer`: TLS opportunistic, server certificate never verified, nothing logged and
> nothing failed. An operator who set sslmode=require in the secret had every reason to believe
> it applied while student records crossed the network on an unauthenticated channel."*
>
> Anyone reading `db.py` alone sees `create_engine(settings.sqlalchemy_url, …)` and has no idea
> a rewriter sits behind that property. That is the point of raising it here.

**Every field has a default — which is itself the hazard.** As
[docs/deployment-env.md:16-19](docs/deployment-env.md#L16-L19) puts it, *"Every field has a
default, which is the hazard this document exists to address: a missing variable does not crash,
it silently selects a development default."* `AUTH_SECRET` is the sharp edge: there is no startup
guard on it — the `lifespan` handler checks only `VOICE_WORKER_SECRET`
([main.py:48](apps/api-py/app/main.py#L48)) — so a production deploy that forgets it boots
happily with a signing key published in this repository.

---

## 6. The trust boundaries

Two rules govern this codebase, and each is enforced by a named function. Learn both names, and
the address of every guard that reads them.

### Rule 1 — student data must not leave the machine unbidden

`LLM_BASE_URL` is a URL, not a promise. It may point at a free tier that trains on submissions.
A resume brief carries a student's name, USN, marks and attendance.

The gate is six lines — three of them the rule — at
[apps/api-py/app/ai/llm.py:105-110](apps/api-py/app/ai/llm.py#L105-L110):

```python
def student_data_egress_allowed(base_url: str) -> bool:
    """Loopback is always fine; any off-machine model needs the explicit flag —
    identical policy to studentDataEgressAllowed() in the Next.js app."""
    if is_loopback(base_url):
        return True
    return settings.allow_remote_student_data
```

`is_loopback` ([llm.py:101-102](apps/api-py/app/ai/llm.py#L101-L102)) tests
`urlparse(base_url).hostname` — lowercased — for membership in `_LOOPBACK_HOSTS`
([llm.py:31](apps/api-py/app/ai/llm.py#L31)):

```python
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
```

The membership is **exact string equality against the parsed hostname**, which is what makes it
trustworthy: `127.0.0.2` is not loopback, `http://[::1]:8080/v1` is (urlparse strips the
brackets, yielding `::1`), and a host like `127.0.0.1.evil.com` correctly is not — a substring
test would have let that one through.

The entry that should stop a careful reader is `0.0.0.0`, because it is **not a loopback address
at all** — it is the wildcard bind address, meaning "every interface on this machine". It is in
the set because operators routinely write `http://0.0.0.0:11434` when they mean a server bound
on this machine. Treating it as local is a deliberate convenience, and it is the one entry in the
set that is a judgement call rather than a fact.

**Understand what the gate can and cannot see.** Its only input is a URL string. It never sees
the messages, the student, or the caller's role. **The boundary is declaration-based, not
detection-based** — it trusts what the caller says about the payload. `app/redaction.py` says so
of itself at [redaction.py:9](apps/api-py/app/redaction.py#L9): *"Not a security boundary — the
egress gate (app/ai/llm.py) is. This is hygiene on stored free text."*

#### The gate is consulted in two different shapes — know both

**Shape one: the adapter checks for you, and raises.** Call
`complete_chat(messages, carries_student_data=True, …)` (or `stream_chat(...)`) and that boolean
changes exactly one thing — a pre-flight check *before any socket is opened*
([llm.py:130-135](apps/api-py/app/ai/llm.py#L130-L135)):

```python
    if carries_student_data and not student_data_egress_allowed(cfg.base_url):
        raise StudentDataEgressRefused(
            f"The model at {cfg.base_url} runs off this machine; student data will "
            "not be sent unless LLM_ALLOW_REMOTE_STUDENT_DATA=true. Use a local "
            "model or a paid key."
        )
```

It does not alter the payload, the headers or the model — the request body is byte-identical
either way. The flag only decides whether the request happens at all.

**Shape two: the caller asks the predicate directly, and nothing raises.** This is what the
resume path does, and it is worth being precise because it is *not* an example of the exception
above. `POST /api/student/resume/generate` consults the gate itself, ahead of the call
([student.py:958-959](apps/api-py/app/routers/student.py#L958-L959)):

```python
    cfg = llm_config()
    if cfg is not None and student_data_egress_allowed(cfg.base_url):
```

On the blocked path `complete_chat` is never reached, so no `StudentDataEgressRefused` is ever
raised and there is no `except` clause to go looking for. The deterministic
`_compose_resume_markdown` result simply stands, and the `else` branch attaches a `note` that
names the way to change it ([student.py:976-981](apps/api-py/app/routers/student.py#L976-L981)):

```python
    else:
        note = (
            "AI generation skipped: the resume carries student data and the configured "
            "model runs off this machine. Composed deterministically. Set "
            "LLM_ALLOW_REMOTE_STUDENT_DATA=true or use a local model to enable AI."
        )
```

The response carries `used_ai: false` and that note, and the SPA renders it as a normal
successful outcome (§2). **Asking the predicate up front is fine and often better — it lets the
handler degrade gracefully. The exception exists so that *forgetting* to ask still fails closed.**

Route **any new student-PII-to-model path** through this gate, in either shape. Public data — a
job posting, an approved policy chunk — does not need it.

```mermaid
flowchart TB
  subgraph M["This machine"]
    PII["Student PII<br/>marks · CGPA · attendance · USN · skills"]
    KB["Knowledge Base<br/>APPROVED public policy text"]
    JOB["Job postings (public)"]
    GATE{"student_data_egress_allowed(base_url)<br/>app/ai/llm.py:105"}
    DET["Deterministic composition<br/>used_ai = false"]
  end

  LOOP["Loopback model<br/>127.0.0.1 · localhost · ::1 · 0.0.0.0"]
  REM["Remote provider<br/>Groq · Mistral · OpenRouter · Gemini · Cohere · Sakana"]
  EMB["Remote /embeddings endpoint"]

  PII --> GATE
  GATE -->|"loopback ⇒ always allowed"| LOOP
  GATE -->|"remote AND LLM_ALLOW_REMOTE_STUDENT_DATA=true"| REM
  GATE -->|"remote, flag off ⇒ REFUSED"| DET
  KB -->|"carries_student_data = False"| REM
  KB --> EMB
  JOB --> REM
```

Two clarifications the diagram cannot carry.

**First, the Knowledge Base is deliberately stored in tables separate from every student-fact
table** ([app/models/knowledge.py:1-14](apps/api-py/app/models/knowledge.py#L1-L14)):
*"This is DELIBERATELY separate from every student-fact table. … Keeping the two apart is what
lets the KB text be embedded and even sent to a remote embedder (it is public policy) while
student PII stays behind the egress gate."* That separation is what licenses the remote embedder.

**Second, the gate covers records REEP *injects*, not free text a student volunteers.** Both
`/api/agent/chat` ([agent.py:170](apps/api-py/app/routers/agent.py#L170)) and
`/api/agent/chat/stream` ([agent.py:231](apps/api-py/app/routers/agent.py#L231)) call the adapter
with `carries_student_data` at its `False` default, so the student's own typed conversation does
go to the remote provider. [agent.py:14-16](apps/api-py/app/routers/agent.py#L14-L16) treats that
as intentional scope: *"The egress gate still applies: this is a general conversational
assistant, so `carries_student_data` stays False; wire it True on any path that injects a
student's private records."* The student chose to type it; REEP did not attach their transcript
to it.

Voice is protected architecturally rather than by the gate
([voice_agent.py:160-165](apps/api-py/voice_agent.py#L160-L165)): *"The privacy guarantee is
ARCHITECTURAL, not a matter of asking the model nicely: no student record is ever placed in this
prompt, so there is nothing personal for the remote model to receive, memorise or leak."*

### Rule 2 — staff scope is decided by role, not by a missing field

Three guards, three addresses:

- `require_mentor(session)` admits **MENTOR, DIRECTOR and ADMIN**
  ([mentor.py:28-34](apps/api-py/app/routers/mentor.py#L28-L34)), raising 403
  `"Staff access required."` otherwise.
- `require_director(session)` admits **DIRECTOR and ADMIN** only, gated on
  `_DIRECTORS = {"DIRECTOR", "ADMIN"}`
  ([mentor.py:230-238](apps/api-py/app/routers/mentor.py#L230-L238)), raising 403
  `"Director access required."`.
- `_require_student(session)` admits anyone holding a `studentId` claim
  ([student.py:118-122](apps/api-py/app/routers/student.py#L118-L122)), raising 403
  `"Not a student account."`.

None of the three narrows to a *particular* student. The helper that does is
`_assert_can_access_student(session, student_id, db)` at
[mentor.py:72-84](apps/api-py/app/routers/mentor.py#L72-L84):

```python
def _assert_can_access_student(session: dict, student_id: str, db: Session) -> None:
    """Staff only, and a MENTOR only for a student in their own group."""
    require_mentor(session)
    if session["role"] in ("DIRECTOR", "ADMIN"):
        if db.get(Student, student_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
        return
    mentor_id = session.get("mentorId")
    student = db.get(Student, student_id)
    if not mentor_id or student is None or student.mentor_id != mentor_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not in your mentor group."
        )
```

Note the deliberate **404, not 403** on the mentor branch — a mentor must not be able to confirm
the *existence* of a student outside their group. A 403 would say "this student exists and you
may not see them"; the 404 says nothing at all. The list endpoint expresses the same rule in one
line ([mentor.py:52-55](apps/api-py/app/routers/mentor.py#L52-L55)):

```python
    if session["role"] == "MENTOR":
        mentor_id = session.get("mentorId")
        if not mentor_id:
            return []  # no Mentor group => nobody (never the whole programme)
```

**Never read "no mentor group" as "whole programme".** That inversion is the failure the rule
prevents, and it is a full-cohort disclosure: every student's marks, attendance and USN handed to
a staff account that was never assigned anyone. The `if not mentor_id` in
`_assert_can_access_student` (line 81) is the same guard on the single-student path.

One mechanical warning that costs people a security bug. `require_mentor`, `require_director`
and `_require_student` are **not FastAPI dependencies**. They take a plain `session: dict` and
are called imperatively as the first statement of a handler body. Only `get_current_session`,
`get_db` and `require_voice_worker` are wired through `Depends(...)`. So role enforcement is
per-handler discipline: forgetting the call in a new handler produces a route any signed-in user
can reach, and nothing in the routing layer catches it. When you add a staff route, the first
line of the body is the security control.

> **Note for the reader.** AGENTS.md says the `require_*` dependencies live in
> `apps/api-py/app/identity.py`. The code disagrees: `identity.py` contains exactly one function,
> `get_current_session`. All three `require_*` guards live in routers —
> `require_mentor` and `require_director` in `mentor.py`, `require_voice_worker` in `voice.py` —
> and `director.py`, `leave.py` and `registration.py` import them across router modules. That
> makes `mentor.py` the single home of the staff-scope vocabulary.

### The third boundary: the worker link

`require_voice_worker` ([voice.py:65-93](apps/api-py/app/routers/voice.py#L65-L93)) reads the
`X-Voice-Worker-Secret` header and has three outcomes: blank secret **and** `ENV=prod` → **500**;
blank secret in dev → open, as documented; secret set but mismatched → **401**. It rejects at
request time rather than at boot for a stated reason
([voice.py:77-80](apps/api-py/app/routers/voice.py#L77-L80)): *"Rejecting at request time rather
than refusing to boot is deliberate: the API serves the whole dashboard, and a misconfigured
voice secret should disable voice ingestion, not take the site down."*

The complementary boot-time signal is the `lifespan` warning in
[main.py:31-55](apps/api-py/app/main.py#L31-L55), whose docstring names the abuse precisely:

> *"The reachable abuse is the forged HEARTBEAT: the body accepts any worker_id, so a stranger
> can make _worker_healthy() true and students are then handed tokens into rooms no agent ever
> joins — voice looks available and silently is not. (Forged TRANSCRIPTS are much harder: an
> unknown conversation id 404s, and ids are uuid4 hex.)"*

This is the §2 heartbeat mechanism read adversarially. `_worker_healthy` asks only whether *any*
row is fresh, and `HeartbeatIn` accepts any non-empty `worker_id`
([voice.py:101-107](apps/api-py/app/routers/voice.py#L101-L107)) — so one forged POST is enough.

> **A nuance the docstring glosses.** That docstring says a blank secret "leaves BOTH worker
> endpoints open" in production — but `require_voice_worker`'s prod branch returns 500 to *every*
> caller, the real worker included ([voice.py:81-86](apps/api-py/app/routers/voice.py#L81-L86)).
> So under `ENV=prod` the observable effect of a blank secret is dead voice ingestion (heartbeats
> 500 → `worker_healthy` false → `/token` 409 forever), not an open door. The genuinely open
> configuration is blank secret **plus** `ENV=dev`. Both halves of the docstring are true of
> different configurations; the wording invites misreading. This is also exactly why
> `/ready` surfaces `worker_auth_configured` as its own key (§2): the operator needs to see the
> blank secret on a dashboard, because in production the endpoint's own answer is an opaque 500
> and in development it is a silent success.

### Reference: the status codes worth recognising, and what they mean

| Code | Detail message | Raised at | Read it as |
|---|---|---|---|
| **201** | — (returns the created `NoteOut`) | [mentor.py:126-128](apps/api-py/app/routers/mentor.py#L126-L128) | A mentor note was written. The only non-200 success in the mentor router. |
| **204** | — (empty body) | [agent.py:352-359](apps/api-py/app/routers/agent.py#L352-L359) | `DELETE /api/agent/conversation` soft-cleared the caller's thread. Soft, not hard — the rows are tombstoned, not dropped. |
| **400** | `Only a mentor (with a Mentor profile) can author notes.` | [mentor.py:138-141](apps/api-py/app/routers/mentor.py#L138-L141) | Staff, and allowed to *see* the student, but no `mentorId` claim — a DIRECTOR/ADMIN cannot author a note, because a note needs an owning `Mentor` row. |
| **401** | `Sign in required.` | [identity.py:12](apps/api-py/app/identity.py#L12) | No cookie, or a cookie that does not verify. Both cases, one message. |
| **401** | `Invalid email or password.` | [auth.py:49-52](apps/api-py/app/routers/auth.py#L49-L52) | Login failed. Deliberately does not say which half was wrong. |
| **401** | `Invalid voice worker secret.` | [voice.py:89-93](apps/api-py/app/routers/voice.py#L89-L93) | The worker's `X-Voice-Worker-Secret` does not match the API's. The classic cause of "the call sounded fine but saved nothing" (§8). |
| **403** | `Not a student account.` | [student.py:121](apps/api-py/app/routers/student.py#L121) | Authenticated, but no `studentId` claim — e.g. a director on a student route. |
| **403** | `Staff access required.` | [mentor.py:33](apps/api-py/app/routers/mentor.py#L33) | Role is not MENTOR/DIRECTOR/ADMIN. |
| **403** | `Director access required.` | [mentor.py:234-236](apps/api-py/app/routers/mentor.py#L234-L236) | Role is not DIRECTOR/ADMIN. |
| **403** | `Voice is a student feature.` | [voice.py:220-224](apps/api-py/app/routers/voice.py#L220-L224), [voice.py:252-256](apps/api-py/app/routers/voice.py#L252-L256) | Staff have no voice assistant, by design. |
| **404** | `Student not found.` | [mentor.py:76-77](apps/api-py/app/routers/mentor.py#L76-L77) | DIRECTOR/ADMIN branch: the id genuinely does not exist. |
| **404** | `Student not in your mentor group.` | [mentor.py:82-84](apps/api-py/app/routers/mentor.py#L82-L84) | MENTOR branch. **404 on purpose** — a 403 would confirm the student exists. |
| **404** | `Alert not found.` | [mentor.py:217](apps/api-py/app/routers/mentor.py#L217) | Resolving an alert id that does not exist. Note the order: the 404 fires *before* `_assert_can_access_student`, so it leaks the non-existence of an alert but never its owner. |
| **404** | `Conversation not found.` | [voice.py:456-458](apps/api-py/app/routers/voice.py#L456-L458) | A transcript POST naming a conversation that is absent or soft-deleted. This is the check that makes forged transcripts hard (see below) — and, per the comment above it, the reason a bare 404 here would silently discard the rest of a live call. |
| **409** | the `reason` from `_compute_status` | [voice.py:263-268](apps/api-py/app/routers/voice.py#L263-L268) | Provider configured, no worker heartbeat. **Start the fourth process.** |
| **422** | FastAPI's validation envelope | framework default (no custom handler) | A malformed request body — e.g. an empty `worker_id`, which `HeartbeatIn` declares `min_length=1`. |
| **422** | `Invalid linked_action.` | [mentor.py:145-147](apps/api-py/app/routers/mentor.py#L145-L147) | A **hand-raised** 422, not the framework envelope above: `linked_action` is typed `str` on the schema and only checked by `MentorAction(...)` in the handler, so the body is a flat `{"detail": …}`, not a `detail` array. |
| **500** | `Voice worker authentication is not configured.` | [voice.py:81-86](apps/api-py/app/routers/voice.py#L81-L86) | Blank `VOICE_WORKER_SECRET` under `ENV=prod`. Fails closed. |
| **502** | `The assistant is temporarily unavailable, please try again.` | [agent.py:174-176](apps/api-py/app/routers/agent.py#L174-L176) | Provider/network/quota failure. Provider detail is logged, never returned. |
| **503** | the `reason` from `_compute_status` | [voice.py:263-268](apps/api-py/app/routers/voice.py#L263-L268) | LiveKit or Groq unconfigured, or the maintenance message is set. **Fix configuration.** |
| **503** | `{"status": "degraded", …}` body | [health.py:64-65](apps/api-py/app/routers/health.py#L64-L65) | `/ready` only, and only for the database. Voice never fails this probe. |

---

## 7. Where everything lives

```
reep-dashboard/
├─ apps/
│  ├─ web/                    Angular 22 SPA — standalone components, signals
│  │  ├─ src/app/core/        Root-provided singletons: auth.service.ts, auth.guard.ts,
│  │  │                       theme.service.ts, chat-voice.service.ts, session.ts (wire contract)
│  │  ├─ src/app/layout/      app-shell.component.ts — the eager desktop frame every guarded
│  │  │                       route renders into; the ONE legitimate `component:` in the router
│  │  ├─ src/app/features/    login/ · register/ · assistant/ · student/<screen>/ (13 screens)
│  │  │                       · placeholder/ — which is what 15 of the 17 mentor and director
│  │  │                       routes load today; the other two (mentor/assistant,
│  │  │                       director/assistant) reuse assistant/. There is no
│  │  │                       features/mentor or features/director.
│  │  ├─ src/app/shared/      Reusable presentational bits: icon.component.ts, kit/, charts/
│  │  ├─ src/styles/          Global CSS: reep-theme.scss, reep-v2.scss, reep-v2-resume.scss
│  │  ├─ src/environments/    environment.ts — the ONLY environment file; apiBase = '/api'
│  │  ├─ proxy.conf.json      Dev-only /api → :3300 forward, no path rewrite
│  │  └─ angular.json         Builder config and the bundle budgets that police lazy routing
│  └─ api-py/                 FastAPI back end
│     ├─ app/main.py          Entrypoint: the lifespan warning, the ONLY middleware (CORS),
│     │                       and the router mount table (§4 step 2)
│     ├─ app/config.py        The Settings class + the process-wide `settings` singleton (§5)
│     ├─ app/db.py            engine · SessionLocal · Base · get_db (§4 step 5)
│     ├─ app/identity.py          get_current_session — the ONLY session dependency, 6 lines
│     ├─ app/security.py      scrypt password hashing + HS256 session tokens (§4 step 4)
│     ├─ app/routers/         auth · student · mentor · director · leave · registration ·
│     │                       agent · voice · health. Each exposes a module-level `router`,
│     │                       and each declares its own Pydantic `*In`/`*Out` models inline.
│     ├─ app/models/          SQLAlchemy 2.0 models — the schema's source of truth.
│     │                       Every module is re-exported from models/__init__.py so Alembic
│     │                       autogenerate can see it (Chapter 2).
│     ├─ app/schemas/         auth.py ONLY (plus __init__.py). Do not look here for a
│     │                       response model — see the note below.
│     ├─ app/ai/              llm.py (adapter + THE GATE), embeddings.py, orchestrator.py,
│     │                       agents.py + adk.py (Phase-4 scaffolding, not on a live path)
│     ├─ app/knowledge.py     Hybrid KB retrieval: Postgres full-text blended with pgvector
│     ├─ app/conversations.py Server-owned conversation get_or_create / append_message
│     ├─ app/memory.py        Durable assistant memory
│     ├─ app/assistant_tools.py  The tool surface the grounded assistant may call
│     ├─ app/redaction.py     Best-effort PII scrub on stored free text. NOT a boundary.
│     ├─ app/retention.py     Retention / purge policy for conversation data
│     ├─ app/document_store.py     On-disk uploads, rooted at settings.uploads_path
│     ├─ app/mailer.py        Outbound email
│     ├─ app/resume_pdf.py    Markdown → PDF for the resume builder
│     ├─ app/seed.py          DEV seed — three demo logins; REFUSES to run when ENV=prod
│     ├─ app/seed_kb.py       Production-safe seed — the Knowledge Base, no accounts
│     ├─ app/eval/            golden.py — the assistant regression gate
│     ├─ migrations/          Alembic; env.py:17 injects settings.sqlalchemy_url
│     ├─ tests/               pytest; conftest.py holds the requires_db gate
│     ├─ voice_agent.py       THE FOURTH PROCESS. Imports nothing from app/.
│     ├─ .env.example         The template for apps/api-py/.env — copy it, then edit
│     ├─ requirements.txt     Runtime only, pinned `==` — what the Dockerfile installs
│     ├─ requirements-dev.txt `-r requirements.txt` + pytest — never enters an image
│     ├─ requirements-voice.txt  The 3.12 worker venv
│     ├─ Dockerfile           API image, python:3.14-slim-bookworm
│     └─ Dockerfile.voice     Worker image, python:3.12-slim-bookworm
├─ .github/workflows/ci.yml   Three jobs pinning Python 3.14, Python 3.12 and Node 22
├─ docker/initdb/             01-create-reep-py.sh — creates reep_py + CREATE EXTENSION vector
├─ docs/                      deployment-env.md, design-v2/ mockups, codebase-mahabharath/ (this book)
├─ ollama/                    reep-gemma3.Modelfile — the optional loopback model
├─ docker-compose.yml         Dev: Postgres only
├─ docker-compose.prod.yml    Prod: db → migrate → api → voice-worker
└─ AGENTS.md                  The house rules. This chapter must not contradict it.
```

**The `app/schemas/` trap.** The directory name promises more than it holds. It contains
`__init__.py` and `auth.py`, and nothing else. Every other request/response model is declared
**inline in its router module, immediately above the handler that returns it** — `JobRowOut` in
`student.py`, `MenteeOut` in `mentor.py`, `StatusOut`/`TokenOut`/`HeartbeatIn` in `voice.py`. If
you are looking for the shape of a response, read the router, not this folder.

**Routes are lazy.** [app.routes.ts](apps/web/src/app/app.routes.ts) uses `loadComponent`
everywhere; the only static imports of app code at the top of that file are the shell and the
guard ([app.routes.ts:3-4](apps/web/src/app/app.routes.ts#L3-L4)).

> **Why it is like this.** [app.routes.ts:13-18](apps/web/src/app/app.routes.ts#L13-L18):
> *"A static `import` at the top of this file pulls the component into the initial bundle no
> matter which route the user visits — which is how every screen in the app ended up in one
> 1.23 MB `main` chunk with no lazy chunks at all. A student on a phone was downloading the
> mentor and director UIs, plus the resume builder and the assistant, before the login form
> could paint."*

Read that comment as history, not as a description of today: the mentor and director UIs it
refers to were the pre-migration app's screens. In the Angular app those routes currently load
the shared placeholder (§1).

The production budgets in `angular.json` are `initial` 250 kB warning / 400 kB error
([angular.json:38-43](apps/web/angular.json#L38-L43)), and a measured `ng build` reports
**141.80 kB** initial total. The heaviest lazy chunk, `assistant-component`, is **554.35 kB**
on its own — so re-eager-ing it would blow the 400 kB error ceiling outright. Be precise, though:
re-eager-ing a *small* route (`academics-component` measures **11.77 kB**) trips nothing, and a
medium one may only trip the 250 kB warning. The guardrail is real for the heavy screens; it is
not a universal tripwire.

**Migrations are their own deployment unit**, never run from the API entrypoint.
[docker-compose.prod.yml:35-37](docker-compose.prod.yml#L35-L37): *"Migrations run ONCE, as their
own unit — never from the API entrypoint. Every API replica running `alembic upgrade head` on
boot races on the version table, and the loser can fail in ways that leave the schema
half-applied."*

---

## 8. Your first hour

Run these in order. Steps 1–3 are mandatory; step 4 is optional and only affects voice.

**1. Database.**

```bash
docker compose up -d
```

This starts `reep-postgres` on host port **5433** (5433, not 5432, so it never collides with a
local PostgreSQL service). On an *empty* data volume, `docker/initdb/01-create-reep-py.sh` runs
and creates the `reep_py` database plus `CREATE EXTENSION vector`.

> **Why it is like this.** [docker/initdb/01-create-reep-py.sh:4-18](docker/initdb/01-create-reep-py.sh#L4-L18):
> the image's `POSTGRES_DB` is `reep_dev` ([docker-compose.yml:13](docker-compose.yml#L13)) but
> `config.py` defaults to `reep_py` — so a fresh `docker compose up -d` used to produce a server
> *without* the app's database and the API failed with
> `FATAL: database "reep_py" does not exist`. AGENTS.md papered over it with a manual `createdb`
> step every new machine had to remember. The init script replaced that. The caveat that trips
> people: *"/docker-entrypoint-initdb.d runs ONLY when the data directory is empty. An existing
> reep_pgdata volume will not re-run this — that is Postgres behaviour, not a bug here. For an
> existing volume, run the same two statements by hand once."* The extension needs superuser
> because `vector.control` is not marked `trusted`, which is why the KB migration cannot create
> it itself.

**2a. Configuration — do this before anything Python.**

```bash
cd apps/api-py
cp .env.example .env      # then edit: paste ONE provider key
```

[apps/api-py/.env.example](apps/api-py/.env.example) is the annotated template and its first line
says exactly this. A fresh checkout has no `.env`, and every `Settings` field has a default, so
the API will boot without one — it will simply use development defaults for everything, including
the published `AUTH_SECRET` (§5). Paste any single provider key (`GROQ_API_KEY` is the easiest,
and doubles as the voice cascade's key) and the LLM adapter auto-selects it with no other change.
This is also the file the fourth process reads in step 4.

**2b. Back end** — from `apps/api-py`, venv at `.venv`, Python 3.14:

```bash
.venv/Scripts/pip install -r requirements-dev.txt   # runtime + pytest
.venv/Scripts/python -m alembic upgrade head        # apply migrations
.venv/Scripts/python -m app.seed                    # idempotent dev seed
.venv/Scripts/python -m uvicorn app.main:app --port 3300
```

Docs at `http://127.0.0.1:3300/docs`. **Do not use `--reload`** — on this platform it has wedged
a stale worker ([main.py:6](apps/api-py/app/main.py#L6)); after editing backend files, kill port
3300 and restart.

`python -m app.seed` **refuses to run when `ENV=prod`**, and there is no override flag.

> **Why it is like this.** [app/seed.py:1-20](apps/api-py/app/seed.py#L1-L20): the seed creates a
> DIRECTOR who by Rule 2 reads every student's marks, attendance and USN, behind a password
> published in AGENTS.md — *"It is not a 'weak default' to be rotated later; it is a credential
> that must never be created there at all."* The production-safe counterpart is
> `python -m app.seed_kb`, which loads the assistant's Knowledge Base and creates no accounts.
> Production needs the KB and must never need the demo users, which is why they no longer travel
> together.

**3. Front end** — from `apps/web`:

```bash
npx ng serve      # http://localhost:4200
```

**4. Voice worker (optional)** — from `apps/api-py`, in its **own** venv on Python 3.12:

```bash
py -3.12 -m venv .venv-voice                              # once
.venv-voice/Scripts/pip install -r requirements-voice.txt # once
.venv-voice/Scripts/python voice_agent.py dev             # `start` in production
```

It reads the **same** `apps/api-py/.env` you created in step 2a (§3 explains how, without
importing `app.config`) and POSTs to `REEP_API_URL`, default `http://localhost:3300`
([voice_agent.py:142](apps/api-py/voice_agent.py#L142)) — so credentials are entered once.

**Seeded logins:** `student@bgscet.ac.in` / `student123`, `mentor@bgscet.ac.in` / `mentor123`,
`director@bgscet.ac.in` / `director123`.

**Tests:** `cd apps/api-py && .venv/Scripts/python -m pytest`. Front end:
`cd apps/web && npx ng build`.

### The three most common "why is it broken" reports

**1. "Voice says the worker is offline."** You did not start the fourth process, and nothing in
the UI says a fourth process exists. `POST /api/voice/token` returns **409** in exactly this
case — provider configured, no worker listening. If instead you get **503**, the cause is
different: one of `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` is blank, or
`GROQ_API_KEY` is missing, or `VOICE_MAINTENANCE_MESSAGE` is set. The discriminator is one
expression at [voice.py:263-268](apps/api-py/app/routers/voice.py#L263-L268):

```python
        code = (
            status.HTTP_409_CONFLICT
            if (st.provider_ready and st.maintenance_message is None)
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
```

Read it as **409 = start the process; 503 = fix configuration**. `GET /api/voice/status` will
already have told the student which, in a `reason` string written to be shown verbatim
([voice.py:193-203](apps/api-py/app/routers/voice.py#L193-L203)).

**2. "The call sounded fine but saved nothing."** This is the worst failure mode in the stack,
because it is silent: transcript POSTs are deliberately fire-and-forget so a bad write can never
kill a live call. After a test call:

```sql
select channel, count(*), max(created_at) from messages group by channel;
```

No `voice` rows, or a stale `max(created_at)`, means turns are being dropped. Two causes, in
order of likelihood: (a) **`VOICE_WORKER_SECRET` differs between the API and the worker**, so
every POST 401s ([voice.py:89-93](apps/api-py/app/routers/voice.py#L89-L93)) — and the worker
still connects to LiveKit and answers normally, so nothing looks wrong from the outside;
(b) **`REEP_API_URL` is wrong**, usually `localhost` from inside a container, so the POSTs never
arrive. Both now appear in the worker log as
`ERROR POST /api/voice/transcript -> HTTP 401: …`, *with the status code* — they used to be a
single WARNING that folded every cause into one line.

**3. "The API will not start / it cannot find the database."** Almost always one of three things.
You ran `uvicorn` from the repo root and expected it to read a root `.env` — it does not, by
design (`_ENV_FILE` is pinned, §5). Or your `reep_pgdata` volume predates the init script, so
`reep_py` does not exist. Or you have a local PostgreSQL on 5432 and pointed `DATABASE_URL` at it
instead of the container's **5433**.

---

## Where this chapter is uncertain

Four things I could not settle, recorded rather than smoothed over:

1. **The production ingress is not described in this repository.** `docker-compose.prod.yml`'s
   `api` service declares no `ports:`, there is no web/nginx/TLS service, and `apps/web` has no
   Dockerfile — yet `ENV=prod` marks the session cookie `Secure` (which requires TLS in front)
   and the API CMD passes `--proxy-headers`. How the SPA is built and served in production, and
   what terminates TLS, is assumed to exist outside the compose file.
2. **Nothing in the repo pins a Node version for local development.** `apps/web/package.json`
   has no `engines` field and no `.nvmrc`, `.node-version` or `volta` block exists. The only Node
   version stated anywhere is CI's `node-version: "22"`
   ([ci.yml:124](.github/workflows/ci.yml#L124)). Treat 22 as the version the build is *known* to
   pass on, not as an enforced floor — because it is not enforced.
3. **Documentation drift I verified.** AGENTS.md and the README say "Angular 20";
   `apps/web/package.json:16` pins `@angular/core: ^22.1.0` and the lockfile resolves 22.1.1. Two
   comments ([voice_agent.py:38](apps/api-py/voice_agent.py#L38),
   [voice.py:150](apps/api-py/app/routers/voice.py#L150)) say the heartbeat runs every ~15 s while
   the live default is 10 ([voice_agent.py:151](apps/api-py/voice_agent.py#L151)).
   [docs/deployment-env.md:120](docs/deployment-env.md#L120) gives the worker's
   `stop_grace_period` as 300 s while
   [docker-compose.prod.yml:131](docker-compose.prod.yml#L131) sets 960 s with a written
   derivation — the compose file is what runs.
   [auth.guard.ts:6](apps/web/src/app/core/auth.guard.ts#L6) names a `GET /api/auth/session`
   endpoint that does not exist; the real call is `/api/auth/me`
   ([auth.service.ts:49](apps/web/src/app/core/auth.service.ts#L49)).
4. **The demo accounts hard-coded in the login screen** (`ananya.r@bgscet.ac.in` and siblings,
   with `DEMO_PASSWORD = 'reep2026'` at
   [login.component.ts:29-32](apps/web/src/app/features/login/login.component.ts#L29-L32)) do not
   match the three seeded logins above. One of the two sources is stale; I could not determine
   which from the code alone.

---

*Next: **Chapter 2 — The Data Model**, where the tables behind every number in this chapter get
their own treatment.*
