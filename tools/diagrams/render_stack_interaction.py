#!/usr/bin/env python3
"""Render the REEP technology-stack INTERACTION map as a print-exact A3 SVG.

Not a deployment picture — that is render_architecture.py. This one answers a
different question: which technologies are in play, and what actually travels
between them. Every wire in the two gutters carries its protocol, its payload
and the guard that sits on it, because an arrow that does not say what crosses
it is decoration.

Three lanes, read left to right:  the browser tab  →  the API process, drawn as
one request descending through the middleware and DI stack  →  the data stores
and external services it talks to.

Output: docs/diagrams/reep-tech-stack-a3.svg (420 x 297 mm, one unit = 0.25 mm).
Regenerate with:  python tools/diagrams/render_stack_interaction.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from poster_kit import FAINT, INK, MUTED, PAPER, Poster  # noqa: E402

C_CLIENT = "#1d4ed8"
C_HTTP = "#0f766e"
C_API = "#6d28d9"
C_DATA = "#b45309"
C_AI = "#be123c"
C_OBS = "#0369a1"
C_SEC = "#334155"

poster = Poster(palette=[C_CLIENT, C_HTTP, C_API, C_DATA, C_AI, C_OBS, C_SEC])
W, H = poster.w, poster.h
text, box, container, chip = poster.text, poster.box, poster.container, poster.chip
arrow, line, wire, add = poster.arrow, poster.line, poster.wire, poster.add

# lanes: client | gutter | api process | gutter | stores & services
AX, AW = 22, 398
G1 = (420, 560)
BX, BW = 560, 460
G2 = (1020, 1190)
CX, CW = 1190, 468
TOP, BOT = 112, 1006

# ------------------------------------------------------------------ title ---
text(22, 50, "REEP — Technology Stack & Interaction Map", 31, INK, 800)
text(22, 72, "Which technologies are in play, and exactly what travels between them", 14, MUTED, 500)
text(1658, 34, "Angular 22.1 · TypeScript 6.0 · FastAPI 0.141 · Python 3.14 · SQLAlchemy 2.0 · PostgreSQL 17",
     11.5, MUTED, 600, anchor="end")
text(1658, 52, "61 tables · 44 migrations · 19 student routes · 34 lazy chunks · 609 backend tests",
     11.5, MUTED, 400, anchor="end")
text(1658, 70, "Print A3 landscape (420 × 297 mm) at 100 % scale, no margins", 11.5, FAINT,
     400, anchor="end")
add(f'<line x1="22" y1="86" x2="1658" y2="86" stroke="{INK}" stroke-width="2"/>')

# =========================================================== LANE A — client =
container(AX, TOP, AW, BOT - TOP, "A · CLIENT RUNTIME — the browser tab", C_CLIENT)
box(40, 152, 362, 122, "ANGULAR 22.1 · TYPESCRIPT 6.0", [
    "Standalone components — no NgModules anywhere",
    "!Signals hold state; RxJS 7.8 only where HttpClient needs it",
    "Router: every route is loadComponent() ⇒ its own chunk",
    "ReactiveForms + FormsModule(ngModel) per screen",
    "app-shell picks one of three navs from the session role",
    ">apps/web/src/app/{core,layout,features}",
], C_CLIENT, body=10, lh=13)
box(40, 286, 362, 110, "SCREEN MAP", [
    "/student/* — 19 routes (badges, ledger, jobs, resume, …)",
    "/mentor/* — Faculty (MENTOR): mentees · leave · upskilling",
    "/admin/* — Main Admin (ADMIN): setup · people · exports",
    "/alumni · /alumni/jobs · /login · /register",
    "!'' → homeRedirectGuard sends each role to its own home",
], C_CLIENT, body=10, lh=13)
box(40, 408, 362, 122, "PLATFORM APIs THE SCREENS USE", [
    "fetch(url, {credentials:'include'}) — the house pattern",
    "FormData for uploads (certificates, resumes, photos)",
    "WebSocket + AudioWorklet — the live mock interview",
    "MediaDevices.getUserMedia → 24 kHz mono PCM16",
    "Blob + anchor for PDF reports and CSV exports",
    "~No token in JS: the session cookie is httpOnly",
], C_CLIENT, body=10, lh=13)
box(40, 542, 362, 110, "DESIGN SYSTEM & ASSETS", [
    "reep-v2.scss — global classes (.card .chip .dt-table)",
    "One committed theme · status is text + colour, never colour",
    "Self-hosted fonts; Material Symbols SUBSET (144 kB not 5.2 MB)",
    "!.icon stays hidden until FontFace reports itself loaded",
    "apexcharts / ng-apexcharts on the analytics screens",
], C_CLIENT, body=10, lh=13)
box(40, 664, 362, 110, "BUILD & CLIENT TELEMETRY", [
    "@angular/build 22.1 (esbuild) → dist/web/browser",
    "~172 kB initial · 34 lazy chunks · budgets enforced in CI",
    "Static files are served by CloudFront + S3, not by the API",
    "@sentry/angular 10.71 — dynamic import, only when a DSN is set",
    "vitest 4 + jsdom 28 for client unit tests",
], C_CLIENT, body=10, lh=13)
box(40, 786, 362, 98, "WHAT THE CLIENT NEVER DOES", [
    "!Never reads the session cookie — it cannot",
    "!Never calls a model directly; all AI goes through /api",
    "Never holds another student's row — scoping is server-side",
    "Its role check drives navigation only, never authorisation",
], C_CLIENT, body=10, lh=13)
box(40, 896, 362, 56, "RETAINED IN THE CLIENT", [
    "livekit-client 2.21 — the voice rollback path, no UI caller",
], C_CLIENT, body=10, lh=13)

# ====================================================== LANE B — API process =
container(BX, TOP, BW, BOT - TOP, "B · API PROCESS — one request, top to bottom", C_API)
PIPE = [
    (152, 78, "1. UVICORN 0.52.3 — ASGI SERVER", [
        "uvloop + httptools · HTTP/1.1 and WebSocket on :3300",
        "!Sync `def` endpoints run in a 40-thread pool — which is what",
        "keeps the event loop free for live interview audio",
    ]),
    (256, 78, "2. STARLETTE MIDDLEWARE — IN ORDER", [
        "CORSMiddleware — one explicit origin, credentials allowed",
        "!RequestTraceMiddleware — mints or keeps X-Request-ID, tags",
        "Sentry, echoes the header, prints one access line",
    ]),
    (360, 78, "3. FASTAPI 0.141.1 — ROUTING & DI", [
        "One APIRouter per domain, all mounted under /api",
        "Depends(get_current_session) on every protected route",
        "response_model=… ⇒ the response shape is the contract",
    ]),
    (464, 92, "4. IDENTITY & SESSION", [
        "PyJWT 2.13 HS256 verify over the reep_session cookie",
        "Claims: userId · email · name · role · studentId? · mentorId?",
        "token_version compared on the way in ⇒ logout can revoke",
        "~Password path (dev/CI only): scrypt N=16384 r=8 p=1 dklen=64",
    ]),
    (582, 78, "5. SCOPE GATES — THE TWO RULES, IN CODE", [
        "_require_student · require_mentor · require_admin",
        "!_assert_can_access_student — ONE implementation, imported",
        "Out-of-scope ids flatten to 404, never 403 (no membership oracle)",
    ]),
    (686, 78, "6. PYDANTIC 2.13 — VALIDATION & SETTINGS", [
        "Bodies parsed, coerced and rejected at the edge",
        "pydantic-settings 2.15 loads .env into one Settings object",
        "!Settings also owns the production boot refusal",
    ]),
    (790, 78, "7. SERVICE LAYER — WHERE THE WORK HAPPENS", [
        "document_store · ai/llm · knowledge · conversations · retention",
        "interview_relay · interview_audio · redaction · ratelimit",
        "resume_pdf / english_report — ReportLab 5.0, strictly local",
    ]),
    (894, 92, "8. PERSISTENCE — SQLALCHEMY 2.0.52", [
        "Session per request via Depends(get_db)",
        "2.0-style select(); models are the schema's source of truth",
        "psycopg 3.3.4 binary protocol · pool_pre_ping, sized by settings",
        "Alembic 1.19 owns DDL — one head, hand-written enum steps",
    ]),
]
for y, h, title, lines in PIPE:
    box(578, y, 424, h, title, lines, C_API, body=9.9, lh=12.6, head=24, title_size=11.5)
for i in range(len(PIPE) - 1):
    y_end = PIPE[i][0] + PIPE[i][1]
    arrow([(790, y_end + 2), (790, PIPE[i + 1][0] - 3)], C_API, 2.2)

# ================================================ LANE C — stores & services =
container(CX, TOP, CW, BOT - TOP, "C · DATA STORES & EXTERNAL SERVICES", C_SEC)
box(1208, 152, 432, 100, "GOOGLE IDENTITY", [
    "accounts.google.com — the only sign-in door in production",
    "Server-side code exchange, then RS256 verify against JWKS",
    "!A verified email with no users row is refused — no JIT signup",
    "users.google_sub pins the row to one Google account",
], C_SEC, body=9.9, lh=13)
box(1208, 264, 432, 106, "OPENAI REALTIME API", [
    "wss://api.openai.com/v1/realtime — model set per environment",
    "Server-to-server: the browser never sees this key",
    "Up: PCM16 frames · Down: audio deltas + transcript events",
    "!The relay owns turn-taking — exactly one response.create site",
    "Consent check and the phase machine live on this socket",
], C_AI, body=9.9, lh=13)
box(1208, 382, 432, 94, "AMAZON BEDROCK — NOVA", [
    "bedrock-runtime · converse / converse_stream (boto3 1.43)",
    "apac.amazon.nova-pro-v1:0 — an inference profile, per region",
    "!Credentials are the IAM task role — no API key exists",
    "Mapped at the edge: system → system[], turns → content[]",
], C_AI, body=9.9, lh=13)
box(1208, 488, 432, 94, "OPENAI-COMPATIBLE PROVIDERS", [
    "One adapter, many targets: Groq · Mistral · OpenRouter ·",
    "Gemini · Cohere · Sakana · a local Ollama / LM Studio",
    "POST /chat/completions, plus SSE streaming, over httpx 0.28",
    "!Loopback is always allowed; anything remote needs the flag",
], C_AI, body=9.9, lh=13)
box(1208, 594, 432, 80, "SENTRY", [
    "One org, two projects — api (Python SDK) and web",
    "Errors AND performance traces · send_default_pii = False",
    "Joined to the raw logs by the request_id tag",
], C_OBS, body=9.9, lh=13)
box(1208, 686, 432, 94, "FILE STORE — EFS in production", [
    "PDF · PNG · JPEG only, decided by MAGIC BYTES not the name",
    "Random stored names — an uploaded name cannot traverse",
    "!Per-owner quota checked BEFORE the body is buffered",
    "Two siblings: uploads/ and interview-audio/ (the recorder)",
], C_DATA, body=9.9, lh=13)
box(1208, 792, 432, 108, "POSTGRESQL 17 + pgvector 0.5", [
    "61 tables · every enum created by a hand-written migration",
    "Knowledge base: full-text blended with vector cosine (<=>)",
    "Embeddings mistral-embed, 1024-dim — and OPTIONAL: with no",
    "embedder configured retrieval degrades to full-text only",
    "!Rule 2 is enforced in SQL, never filtered in Python after",
], C_DATA, body=9.9, lh=13)
box(1208, 912, 432, 68, "RETAINED / OCCASIONAL", [
    "livekit-api 1.2 — room tokens for the voice rollback path",
    "SMTP mailer — job alerts, with deliver_once idempotency",
], C_SEC, body=9.9, lh=13)

# ============================================================ THE WIRES ======
# gutter 1 — browser <-> API
wire(G1[0], G1[1], 360, 1, C_HTTP, "HTTPS · JSON", "Cookie: reep_session",
     "X-Request-ID echoed back")
wire(G1[0], G1[1], 470, 2, C_HTTP, "multipart/form-data", "python-multipart 0.0.32",
     "type from magic bytes")
wire(G1[0], G1[1], 580, 3, C_HTTP, "WSS /api/interview", "PCM16 24 kHz · binary",
     "JSON control events", back=True)
wire(G1[0], G1[1], 700, 4, C_HTTP, "302 · OAuth 2.0", "state + nonce cookies",
     "lands on the role's home")

# gutter 2 — API <-> stores and services
wire(G2[0], G2[1], 190, 5, C_SEC, "HTTPS · OpenID Connect", "code exchange + JWKS fetch",
     "aud / iss / nonce verified")
wire(G2[0], G2[1], 302, 6, C_AI, "WSS · websockets 15.0.1", "Authorization: Bearer",
     "audio + events both ways", back=True)
wire(G2[0], G2[1], 420, 7, C_AI, "HTTPS · SigV4 (boto3)", "converse / converse_stream",
     "signed by the task role")
wire(G2[0], G2[1], 526, 8, C_AI, "HTTPS · JSON + SSE", "POST /chat/completions",
     "rule 1 gate runs first")
wire(G2[0], G2[1], 632, 9, C_OBS, "HTTPS · Sentry envelope", "async, batched, PII off")
wire(G2[0], G2[1], 724, 10, C_DATA, "POSIX file I/O", "sniff · quota · random name")
wire(G2[0], G2[1], 830, 11, C_DATA, "psycopg 3.3 binary", "pooled, pool_pre_ping",
     "SQLAlchemy 2.0 select()")
wire(G2[0], G2[1], 950, 12, C_SEC, "HTTPS / SMTP", "tokens · mail, best-effort")

# ====================================================== bottom — toolchain ===
box(22, 1024, 534, 146, "BUILD, TEST & CI — FOUR JOBS", [
    "pytest — 609 tests across 37 modules; TestClient boots the SAME ASGI app",
    "REEP_REQUIRE_DB=1 turns a missing Postgres into a hard failure, not a green skip",
    "api-imports — proves every app/ module imports against requirements.txt ALONE",
    "worker-imports — the same proof for the voice worker's separate manifest",
    "web — ng build, where a bundle budget fails a route that stopped being lazy",
    "vitest 4 + jsdom 28 for client unit tests · prettier for format",
], C_API, body=9.8, lh=12.4, head=22, title_size=11)
box(572, 1024, 534, 146, "PACKAGING & PROCESS TOPOLOGY", [
    "Dockerfile: python:3.14-slim-bookworm, dependency layer before code",
    "requirements.txt is runtime-only and pinned == ; -dev.txt adds pytest",
    "!Voice worker needs its OWN venv on Python 3.12 — livekit requires < 3.15",
    "Processes: the API (uvicorn) · a scheduled retention task · Postgres",
    "…and optionally the voice worker. The AI interviewer runs INSIDE the API.",
    "Terraform provisions the infrastructure; Alembic owns the schema",
], C_SEC, body=9.8, lh=12.4, head=22, title_size=11)
box(1122, 1024, 536, 146, "THE GUARDS THAT RIDE THESE WIRES", [
    "!RULE 1 — wires 7 & 8: student PII may not reach a remote model unless the",
    "egress flag is open; refused in-process, and the resume composes deterministically",
    "!RULE 2 — wire 11: staff scope narrowed in SQL, never filtered afterwards",
    "!CONSENT — wire 6: no live grant and the socket never opens (close 4013)",
    "TRACE — every wire: one X-Request-ID, echoed, tagged on Sentry, printed as rid=",
    "BOOT GUARD — the process refuses to start on repo-default secrets in production",
], C_AI, body=9.8, lh=12.4, head=22, title_size=11)

# ----------------------------------------------------------------- footer ---
add(f'<line x1="22" y1="1178" x2="1658" y2="1178" stroke="{FAINT}" stroke-width="1"/>')
text(22, 1186, "Numbered discs key each wire to its protocol · payload · guard.  "
               "A dashed return arrow means traffic flows both ways on the same connection.",
     9.5, FAINT)
text(1658, 1186, "Generated by tools/diagrams/render_stack_interaction.py — regenerate when the stack changes",
     9.5, FAINT, anchor="end")

dest = Path(__file__).resolve().parents[2] / "docs/diagrams/reep-tech-stack-a3.svg"
warnings = poster.save(dest)
print(f"wrote {dest} ({dest.stat().st_size // 1024} kB)")
for problem in warnings:
    print("  !", problem)
print("  clean" if not warnings else f"  {len(warnings)} geometry problem(s)")
