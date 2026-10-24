#!/usr/bin/env python3
"""Render the REEP architecture poster as a print-exact A3 SVG.

Output: docs/diagrams/reep-architecture-a3.svg — 420 x 297 mm (A3 landscape),
viewBox 1680 x 1188, so one unit is exactly 0.25 mm. Print at 100 % scale with
no margins; body text is >= 2.4 mm tall, which is comfortably readable on a
wall from about a metre.

Regenerate whenever the architecture changes:  python tools/diagrams/render_architecture.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from poster_kit import FAINT, INK, MUTED, PAPER, Poster  # noqa: E402

C_USER = "#1d4ed8"
C_EDGE = "#0f766e"
C_COMPUTE = "#6d28d9"
C_DATA = "#b45309"
C_AI = "#be123c"
C_OBS = "#0369a1"
C_REC = "#7c2d12"
C_SEC = "#334155"

poster = Poster(palette=[C_USER, C_EDGE, C_COMPUTE, C_DATA, C_AI, C_OBS, C_REC, C_SEC])
W, H = poster.w, poster.h
add, text, box = poster.add, poster.text, poster.box
container, chip, arrow, line = poster.container, poster.chip, poster.arrow, poster.line

# ----------------------------------------------------------------- title ----
text(22, 50, "REEP — Placement-Readiness Platform", 31, INK, 800)
text(22, 72, "Complete system architecture, as deployed on AWS", 14, MUTED, 500)
text(1658, 38, "BGSCET MBA · github.com/darshani8/reep-", 11.5, MUTED, 600, anchor="end")
text(1658, 56, "Angular 22 SPA · FastAPI (Python 3.14) · PostgreSQL 17 + pgvector · AWS ap-south-1",
     11.5, MUTED, 400, anchor="end")
text(1658, 74, "Print A3 landscape (420 × 297 mm) at 100 % scale, no margins",
     11.5, FAINT, 400, anchor="end")
add(f'<line x1="22" y1="86" x2="1658" y2="86" stroke="{INK}" stroke-width="2"/>')

# ============================================================ MAIN COLUMN ===
# 1 — users -------------------------------------------------------------------
chip(22, 96, "1 · PEOPLE & ROLES", C_USER)
UX = [22, 295, 568, 841]
box(UX[0], 138, 259, 136, "STUDENT · role STUDENT", [
    "Google SSO · must exist on the roster",
    "Programme map · Reboot→Excel→Elevate",
    "Skills & Badge profile — 48 badges",
    "Time ledger · English baseline · records",
    "Jobs feed: match % + eligibility verdict",
    "Resume builder · document uploads",
    "AI mock interview + practice reports",
], C_USER, body=9.8, lh=12.6)
box(UX[1], 138, 259, 136, "FACULTY · role MENTOR", [
    "!A mentor only once assigned a mentee",
    "Mentee log — 1:1 notes students read",
    "Leave — apply; the Main Admin sanctions",
    "Badge Centre · assessments T0 → T4",
    "Upskilling — own certificates",
    "Scope: their own mentee group ONLY",
    "!No group ⇒ sees NOBODY (rule 2)",
], C_USER, body=9.8, lh=12.6)
box(UX[2], 138, 259, 136, "MAIN ADMIN · role ADMIN", [
    "!The placement office (TPO) — ONE account",
    "All a mentor sees, programme-wide",
    "Approved Certification Catalogue",
    "Manual award · revoke badges",
    "Placement criteria · offers · intake",
    "Interview audio (consented only)",
    "Grants console screens to faculty",
], C_USER, body=9.8, lh=12.6)
box(UX[3], 138, 259, 136, "ALUMNI · role ALUMNI", [
    "A role with no student or mentor row",
    "First login ⇒ create profile",
    "Company + current resume (required)",
    "Resume kept if omitted on update",
    "Jobs sheet — postings only",
    "No match % (needs a student's marks)",
    "No access to any student record",
], C_USER, body=9.8, lh=12.6)

# collector into the edge
line([(151, 274), (151, 292)], C_USER, 2)
line([(424, 274), (424, 292)], C_USER, 2)
line([(697, 274), (697, 292)], C_USER, 2)
line([(970, 274), (970, 292)], C_USER, 2)
line([(151, 292), (970, 292)], C_USER, 2)
arrow([(189, 292), (189, 316)], C_USER, 2.6)

# 2 — edge --------------------------------------------------------------------
chip(22, 300, "2 · EDGE — ONE PUBLIC DOOR", C_EDGE)
box(22, 326, 334, 150, "CLOUDFRONT + AWS WAF", [
    "PriceClass_200 — includes India POPs",
    "TLS 1.2+ · HTTP → HTTPS redirect",
    "WAF: Common + KnownBadInputs rules",
    "Rate limit 2 000 req / 5 min per IP",
    "403 / 404 → /index.html (SPA routes)",
    "WebSocket upgrade passes through",
    "~Nothing else is reachable from outside",
], C_EDGE)
box(376, 326, 334, 70, "S3 — ANGULAR SPA (private)", [
    "Origin Access Control · versioned bucket",
    "~172 kB initial · every route lazy-loaded",
], C_EDGE)
box(376, 406, 334, 70, "APPLICATION LOAD BALANCER", [
    "HTTPS TLS 1.3 → target group :3300",
    "Health /health · idle 300 s (interview WS)",
], C_EDGE)
box(730, 326, 370, 150, "DNS, CERTIFICATES & ORIGINS", [
    "Route 53 / college DNS → CloudFront",
    "ACM certificate in us-east-1 → CloudFront",
    "ACM certificate in ap-south-1 → ALB",
    "The CloudFront → ALB hop stays encrypted",
    "!Same-origin: the SPA and /api share a host,",
    "!so the httpOnly session cookie just works",
    "~Google redirect: /api/auth/sso/google/callback",
], C_EDGE)
arrow([(356, 361), (376, 361)], C_EDGE, 2.4)
arrow([(356, 441), (376, 441)], C_EDGE, 2.4)
# White plates behind the two routing labels: they sit in the 20-unit gutter
# between cards, and without a plate the second one lands on the ALB's dark
# header bar — dark text on dark, unreadable exactly where it matters.
for _lx, _ly, _lb, _lw in ((366, 349, "SPA", 26), (366, 429, "/api/*", 34)):
    add(f'<rect x="{_lx - _lw / 2}" y="{_ly - 9}" width="{_lw}" height="12" fill="{PAPER}"/>')
    text(_lx, _ly, _lb, 9, C_EDGE, 700, anchor="middle")
arrow([(543, 476), (543, 512)], C_EDGE, 2.8)

# 3 — compute -----------------------------------------------------------------
container(22, 512, 1078, 310, "3 · COMPUTE — ECS FARGATE CLUSTER (private subnets, 2 AZs)", C_COMPUTE)
box(40, 556, 590, 248, "API TASK — FastAPI + uvicorn on :3300", [
    "Python 3.14 · 0.5 vCPU / 1 GB · awsvpc, no public IP",
    "#ROUTERS",
    "auth · google_auth · identity — SSO, session, role gates",
    "student · student_programme — records, ledger, English baseline",
    "badges · badge_verification — 48-badge engine, evidence, growth",
    "mentor · mentee_records — mentee scope enforced (rule 2)",
    "alumni · staff_upskilling · leave — alumni + faculty surfaces",
    "console · admin_* (the Main Admin) · registration · health",
    "interview — WebSocket /api/interview, the realtime relay",
    "voice · agent — retained rollback path, no UI caller",
    "#SERVICE LAYER",
    "document_store — magic-byte sniffing, quotas, random names",
    "ai/llm — universal adapter · Bedrock transport · egress gate",
    "knowledge — hybrid pgvector + Postgres full-text retrieval",
    "retention · interview_audio · traceability middleware",
], C_COMPUTE, body=10.5, lh=14)
box(650, 556, 226, 120, "AUTOSCALING", [
    "Target tracking on ECS",
    "!CPU 60 % · Memory 75 %",
    "!2 → 10 tasks",
    "Out ~60 s · in after 300 s",
    "~Floor of 2: a restart is",
    "~never an outage",
], C_COMPUTE, body=10, lh=12.6)
box(650, 688, 226, 116, "RETENTION JOB", [
    "EventBridge Scheduler",
    "Daily at 03:00 IST",
    "python -m app.retention_job",
    "Purges expired records,",
    "sweeps orphaned interviews",
], C_COMPUTE, body=10, lh=12.6)
box(896, 556, 180, 120, "DEPLOY", [
    "ECR image push",
    "force-new-deployment",
    "!Circuit breaker +",
    "!automatic rollback",
    "Health-gated cutover",
], C_COMPUTE, body=10, lh=12.6)
box(896, 688, 180, 116, "SECRETS", [
    "AWS Secrets Manager",
    "app/ — generated:",
    "AUTH_SECRET, DB URL",
    "external/ — operator:",
    "API keys, Sentry DSN",
], C_COMPUTE, body=10, lh=12.6)

# 4 — data --------------------------------------------------------------------
chip(22, 838, "4 · STATE", C_DATA)
box(22, 866, 530, 188, "AMAZON RDS · POSTGRESQL 17 + pgvector", [
    "Private subnets · encrypted · reachable only from API tasks",
    "#CORE  users · students · mentors · cohorts",
    "#BADGES  badge_evidence · student_badges · capability_assessments",
    "#         approved_certifications",
    "#INTERVIEW  sessions · turns · evaluations · consents",
    "#PROGRAMME  time_ledger · english_baselines · milestones",
    "#DOCUMENTS  uploads · alumni_profiles · staff_upskilling_certs",
    "#KNOWLEDGE  knowledge_chunks — vector embeddings",
    "Storage autoscales 20 → 100 GB · Performance Insights on",
], C_DATA, body=10.3, lh=14)
box(572, 866, 528, 188, "AMAZON EFS · /data — persistent, encrypted", [
    "Mounted by every task · survives restarts and redeploys",
    "!/data/uploads",
    "marksheets · certificates · profile photos · student CVs ·",
    "faculty upskilling certificates · alumni resumes",
    "!/data/interview-audio",
    "per-speaker interview WAVs — the call recorder (panel →)",
    "Access point pins uid/gid · lifecycle → IA after 30 days",
    "Covered by the same AWS Backup plan as the database",
], C_DATA, body=10.3, lh=14)
arrow([(287, 822), (287, 866)], C_DATA, 2.6)
arrow([(836, 822), (836, 866)], C_DATA, 2.6)

# bottom strip ----------------------------------------------------------------
BX = [22, 386, 750]
box(BX[0], 1074, 348, 96, "AUTOSCALING, CAPACITY & COST", [
    "API 2 → 10 tasks · RDS storage 20 → 100 GB",
    "RDS compute is FIXED — resizing is a human call",
    "CloudFront · S3 · EFS · ALB scale themselves",
    "!≈ $130 / month infra before model tokens",
    "~A voice interview costs ≈ $3–5 — the real variable",
], C_COMPUTE, body=9.6, lh=12, head=22, title_size=11)
box(BX[1], 1074, 348, 96, "BACKUPS & DISASTER RECOVERY", [
    "RDS automated snapshots — 14-day point-in-time",
    "AWS Backup vault — daily, kept 35 days",
    "…covering BOTH the database and EFS",
    "Deletion protection + final snapshot on the instance",
    "!Two independent planes: one destroy loses nothing",
], C_DATA, body=9.6, lh=12, head=22, title_size=11)
box(BX[2], 1074, 350, 96, "SECURITY & ACCESS CONTROL", [
    "Google-only SSO — the roster IS the allowlist",
    "google_sub pins identity · token_version revokes",
    "!RULE 1  student PII reaches no model unless opened",
    "!RULE 2  staff scope by role, never by a missing field",
    "Boot guard refuses to start on repo-default secrets",
], C_SEC, body=9.6, lh=12, head=22, title_size=11)

# ============================================================ SIDE COLUMN ===
SX, SW = 1130, 528

container(SX, 138, SW, 458, "5 · AI & MODEL PLANE — called by the API tasks", C_AI)
box(SX + 22, 180, 484, 132, "AMAZON BEDROCK — NOVA", [
    "Model: apac.amazon.nova-pro-v1:0 (configurable)",
    "Transport: converse / converse_stream via boto3",
    "Powers: resume brief · grounded assistant · scoring",
    "!Auth is the IAM task role — no API key anywhere",
    "In-account: your traffic is not used for training",
    "~Rule 1 still applies — student PII needs the flag",
], C_AI, body=10.2, lh=13)
box(SX + 22, 324, 484, 176, "OPENAI REALTIME — THE AI MOCK INTERVIEWER", [
    "WebSocket /api/interview · 24 kHz PCM both ways",
    "!The relay owns the turn: create_response = false",
    "Word-count gate, then exactly ONE response.create",
    "Phases: opening → probing → deep_dive → wrap_up",
    "4 specialisations (HR · DM · BA · FA), voice per track",
    "Verdict spoken, then a strict-JSON scorecard (text only)",
    "Persists sessions · turns · evaluations, fire-and-forget",
    "Consent gate: no live grant ⇒ 4013 · revoked ⇒ 4014",
    "~MIGRATION → Amazon Nova Sonic (bidirectional stream).",
    "~IAM already allows it; the engine port is still to do.",
], C_AI, body=10.2, lh=13)
box(SX + 22, 512, 484, 70, "KNOWLEDGE BASE & RETAINED PATHS", [
    "KB: pgvector cosine blended with full-text, distance-floored",
    "Retained (no UI caller): LiveKit voice worker + orchestrator",
], C_AI, body=10.2, lh=13)

container(SX, 626, SW, 340, "6 · OBSERVABILITY & TRACEABILITY — fed by every request", C_OBS)
box(SX + 22, 668, 484, 106, "SENTRY — THE ONE TOOL", [
    "API (Python SDK) + SPA (lazy chunk) → one org",
    "Errors AND performance traces in one place",
    "!PII OFF — no cookies, no bodies, no student text",
    "Every event tagged request_id = X-Request-ID",
    "Claude reads it through the Sentry MCP connector",
], C_OBS, body=10.2, lh=13)
box(SX + 22, 786, 484, 88, "CLOUDWATCH — WHAT SENTRY CANNOT BE", [
    "/reep/api log group (30 d) — one access line per request",
    "Alarms → SNS email: no healthy task · ALB 5xx · RDS",
    "storage & CPU · CPU still pegged at max tasks",
    "!AI tripwire: “Dropped interview turn” metric filter",
], C_OBS, body=10.2, lh=13)
box(SX + 22, 884, 484, 76, "THE TRACE THREAD — ONE ID, FOUR HOPS", [
    "1 middleware mints or keeps X-Request-ID · 2 echoed on the",
    "response · 3 tagged on the Sentry event · 4 printed as rid=…",
    "in the access log — ALB logs in S3 close the loop",
], C_OBS, body=10.2, lh=13)

container(SX, 996, SW, 174, "7 · INTERVIEW CALL RECORDING", C_REC)
box(SX + 22, 1036, 484, 62, "TWO TRACKS, NEVER MIXED", [
    "One WAV for the student, one for the AI — kept separate",
    "because the directions are not time-aligned · size-capped",
], C_REC, body=10.2, lh=13)
box(SX + 22, 1102, 484, 64, "CONSENT IS A ROW, NOT A SETTING", [
    "!Two switches: the deployment flag AND the student's own",
    "store-audio tick · Main Admin only (faculty by grant) · 180-day clock",
], C_REC, body=10.2, lh=13)

# cross-column arrows
arrow([(1100, 600), (1130, 600)], C_AI, 2.8)
arrow([(1100, 726), (1130, 726)], C_OBS, 2.8)

# footer ----------------------------------------------------------------------
add(f'<line x1="22" y1="1178" x2="1658" y2="1178" stroke="{FAINT}" stroke-width="1"/>')
text(22, 1186, "Solid arrow = request / data path.  Dashed border = an AWS boundary.  "
                "Bold lines are the invariants that must not be broken.", 9.5, FAINT)
text(1658, 1186, "Generated by tools/diagrams/render_architecture.py — regenerate when the architecture changes",
     9.5, FAINT, anchor="end")

dest = Path(__file__).resolve().parents[2] / "docs/diagrams/reep-architecture-a3.svg"
warnings = poster.save(dest)
print(f"wrote {dest} ({dest.stat().st_size // 1024} kB)")
for problem in warnings:
    print("  !", problem)
print("  clean" if not warnings else f"  {len(warnings)} geometry problem(s)")
