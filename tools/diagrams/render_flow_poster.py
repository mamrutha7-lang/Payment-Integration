#!/usr/bin/env python3
"""Render the REEP flow poster — A3 PORTRAIT, icon-driven, numbered steps.

The third poster, in the style of a vendor architecture diagram: big flat
icons, two-line captions, numbered steps following one request from a person
to the data behind it, dotted groups, and a legend. Where the other two sheets
are dense reference (render_architecture.py, render_stack_interaction.py), this
one is the sheet you can read from across the room.

Output: docs/diagrams/reep-flow-a3.svg — 297 x 420 mm, one unit = 0.25 mm.
Regenerate with:  python tools/diagrams/render_flow_poster.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import icon_kit as ico  # noqa: E402
from poster_kit import FAINT, INK, MUTED, Poster  # noqa: E402

BRAND = "#6d28d9"
AWS = "#ff9900"
CLIENT = "#1d4ed8"
GH = "#24292e"
ACTIONS = "#2088ff"
GOOGLE = "#4285f4"
PG = "#336791"
OPENAI = "#10a37f"
SENTRY = "#362d59"

poster = Poster(width=1188, height=1680,
                palette=[BRAND, AWS, CLIENT, GH, ACTIONS, GOOGLE, PG, OPENAI, SENTRY])
W, H = poster.w, poster.h
text, add, arrow, line = poster.text, poster.add, poster.arrow, poster.line

LEFT, MID, RIGHT = 250, 594, 938
S = 96  # icon box


def node(cx, cy, icon, color, caption, step=None):
    """One icon with its caption, and optionally the step number beside it."""
    icon(poster, cx, cy, S, color)
    y = cy + S * 0.62 + 14
    for ln in caption:
        text(cx, y, ln, 11.5, INK, 600, anchor="middle")
        y += 15
    if step is not None:
        text(cx + S * 0.60, cy - S * 0.30, str(step), 26, INK, 800)


def group(x1, y1, x2, y2, label, step=None):
    add(f'<rect x="{x1}" y="{y1}" width="{x2 - x1}" height="{y2 - y1}" rx="10" fill="none" '
        f'stroke="{INK}" stroke-width="2" stroke-dasharray="8 6"/>')
    text(x1 + 16, y1 + 24, label, 12, MUTED, 800, ls=1.1)
    if step is not None:
        text(x2 - 20, y1 + 28, str(step), 26, INK, 800, anchor="end")


# ------------------------------------------------------------------ title ---
text(W / 2, 58, "REEP — Architecture Diagram", 31, BRAND, 800, anchor="middle")
text(W / 2, 86, "Placement-readiness platform for BGSCET MBA · Angular + FastAPI + PostgreSQL on AWS",
     12.5, MUTED, 500, anchor="middle")

# ------------------------------------------------------------------- rows ---
RA, RB, RC, RD, RE, RF = 175, 410, 645, 880, 1115, 1346

node(LEFT, RA, ico.people, CLIENT, ["Students · Faculty · Alumni", "Main Admin (placement office)"], 1)
node(MID, RA, ico.browser, BRAND, ["REEP Dashboard", "in the browser"], 2)
node(RIGHT, RA, ico.codebranch, GH, ["GitHub", "darshani8/reep-"])

node(MID, RB, ico.cloud, AWS, ["Amazon CloudFront", "+ AWS WAF"], 3)
node(RIGHT, RB, ico.pipeline, ACTIONS, ["GitHub Actions CI", "4 jobs · tests + build"])

node(LEFT, RC, ico.bucket, AWS, ["Amazon S3", "Angular 22 SPA"], 4)
node(MID, RC, ico.balancer, AWS, ["Application", "Load Balancer"], 5)
node(RIGHT, RC, ico.registry, AWS, ["Amazon ECR", "container image"])

node(LEFT, RD, ico.key, GOOGLE, ["Google Workspace", "Sign-in · roster gate"])
node(MID, RD, ico.containers, AWS, ["ECS Fargate — FastAPI", "autoscales 2 → 10 tasks"], 6)

node(210, RE, ico.database, PG, ["PostgreSQL 17", "+ pgvector"], 7)
node(430, RE, ico.drive, AWS, ["Amazon EFS", "uploads + interview audio"], 8)
group(590, 1046, 1110, 1212, "AI PLANE", 9)
node(720, RE, ico.chip, AWS, ["Amazon Bedrock", "Nova"])
node(980, RE, ico.mic, OPENAI, ["OpenAI Realtime", "voice interview"])

node(LEFT, RF, ico.clock, BRAND, ["Daily retention task", "03:00 IST · Fargate"])
group(590, 1262, 1110, 1444, "OBSERVABILITY & TRACEABILITY", 10)
node(720, RF, ico.bug, SENTRY, ["Sentry", "errors + traces"])
node(980, RF, ico.bars, AWS, ["Amazon CloudWatch", "logs + alarms"])

# ------------------------------------------------------------------ arrows --
A = 2.6
# OUT is where a downward arrow may start: below the icon AND below its
# two-line caption. Leaving from the icon edge instead drew every vertical
# arrow straight through the words underneath it.
OUT, IN_ = 100, 52
arrow([(LEFT + 50, RA), (MID - IN_, RA)], INK, A)                      # people → browser
arrow([(MID, RA + OUT), (MID, RB - IN_)], INK, A)                      # browser → CloudFront
arrow([(MID, RB + OUT), (MID, RB + 140), (LEFT, RB + 140), (LEFT, RC - IN_)], INK, A)
arrow([(MID, RB + OUT), (MID, RC - IN_)], INK, A)                      # CloudFront → ALB
arrow([(RIGHT, RA + OUT), (RIGHT, RB - IN_)], INK, A)                  # GitHub → Actions
arrow([(RIGHT, RB + OUT), (RIGHT, RC - IN_)], INK, A)                  # Actions → ECR
arrow([(RIGHT, RC + OUT), (RIGHT, RD), (MID + IN_, RD)], INK, A)       # ECR → Fargate (deploy)
arrow([(MID, RC + OUT), (MID, RD - IN_)], INK, A)                      # ALB → Fargate
arrow([(MID - IN_, RD - 12), (LEFT + IN_, RD - 12)], INK, 2.2)         # API → Google
arrow([(LEFT + IN_, RD + 12), (MID - IN_, RD + 12)], INK, 1.7, "5 4")  # Google → API

BUS = 1020
arrow([(MID, RD + OUT), (MID, BUS)], INK, A)                           # API → the bus
line([(210, BUS), (1150, BUS)], INK, A)
arrow([(210, BUS), (210, RE - IN_)], INK, A)
arrow([(430, BUS), (430, RE - IN_)], INK, A)
arrow([(890, BUS), (890, 1046)], INK, A)
arrow([(1150, BUS), (1150, RF), (1110, RF)], INK, A)

# ------------------------------------------------------------------ legend --
add(f'<rect x="250" y="1470" width="690" height="92" rx="8" fill="none" stroke="{INK}" '
    f'stroke-width="2" stroke-dasharray="8 6"/>')
add(f'<rect x="272" y="1494" width="22" height="22" rx="4" fill="{AWS}"/>')
text(308, 1511, "Orange icons are AWS services, provisioned by Terraform in infra/aws —",
     11.5, INK, 500)
text(308, 1529, "see docs/aws-deployment.md for the runbook.", 11.5, INK, 500)
text(308, 1549, "Numbered steps follow ONE request, from a person to the data behind it.",
     11.5, MUTED, 600)

text(22, 1640, "Generated by tools/diagrams/render_flow_poster.py", 10.5, FAINT)
text(W - 22, 1640, "https://github.com/darshani8/reep-", 10.5, FAINT, anchor="end")

dest = Path(__file__).resolve().parents[2] / "docs/diagrams/reep-flow-a3.svg"
warnings = poster.save(dest)
print(f"wrote {dest} ({dest.stat().st_size // 1024} kB)")
for problem in warnings:
    print("  !", problem)
print("  clean" if not warnings else f"  {len(warnings)} geometry problem(s)")
