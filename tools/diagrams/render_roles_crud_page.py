#!/usr/bin/env python3
"""Render the n8n roles -> features -> CRUD map as one phone-friendly HTML page.

The n8n workflow (render_n8n_workflow.py) is the full diagram, but it needs n8n
and a wide screen. This page carries the same content in a form a phone can
read: a tab per role, every feature as a row showing which of CREATE / READ /
UPDATE / DELETE that role is offered, and a tap to open its endpoints, screens,
code, gate, tables and AWS services. A last tab walks the request path and the
AWS estate.

It cannot disagree with the n8n file, and that is the point of how it is built:
the role lanes come from the same inventory through the same functions
(build_lanes, screens_for, ops_of), and the stack tab is read out of the
GENERATED n8n workflow itself -- its section notes, nodes and connections -- so
regenerate that first.

Inputs:  docs/diagrams/n8n/reep-feature-inventory.json
         docs/diagrams/n8n/reep-roles-features-crud.n8n.json
Outputs: docs/diagrams/n8n/reep-roles-features-crud.html   (a complete document)
         --fragment PATH  also writes the body-only variant the Artifact
                          publisher wraps in its own document skeleton

Regenerate with:  python tools/diagrams/render_n8n_workflow.py
                  python tools/diagrams/render_roles_crud_page.py
"""

from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_n8n_workflow as n8n  # noqa: E402

ROOT = n8n.ROOT
OUT = ROOT / "docs" / "diagrams" / "n8n" / "reep-roles-features-crud.html"
TITLE = "REEP Roles & CRUD Map"

# Tab order: the people first, the doors last.
LANE_ORDER = ["STUDENT", "FACULTY", "ALUMNI", "MAIN_ADMIN", "ACCOUNT", "PUBLIC"]
LANE_SHORT = {"STUDENT": "Student", "FACULTY": "Faculty", "ALUMNI": "Alumni",
              "MAIN_ADMIN": "Main Admin", "ACCOUNT": "Any account", "PUBLIC": "Public"}
LANE_SLUG = {"STUDENT": "student", "FACULTY": "faculty", "ALUMNI": "alumni",
             "MAIN_ADMIN": "admin", "ACCOUNT": "account", "PUBLIC": "public"}
LANE_BLURB = {
    "STUDENT": "A student signed in: their own records only.",
    "FACULTY": "A faculty member (role MENTOR): their own things, plus their mentee group while they mentor "
               "somebody. Console screens only when the Main Admin grants them.",
    "ALUMNI": "A graduate: their alumni profile, the jobs sheet, and what every signed-in account can do.",
    "MAIN_ADMIN": "The placement office, one account (role ADMIN): the whole console.",
    "ACCOUNT": "Things every signed-in account does to itself: session, sign-out, password, linked Google, "
               "notification preferences.",
    "PUBLIC": "No session at all: applicants, the sign-in doors, onboarding, password reset, health probes.",
}
CRUD_WORD = {"C": "Create", "R": "Read", "U": "Update", "D": "Delete", "A": "Action"}

E = html.escape


def md_inline(text: str) -> str:
    """The little markdown the n8n sticky notes use: **bold** and `code`."""
    out = E(text).replace(" -- ", " — ")
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return out


def crud_pills(ops: set[str], small: bool = False) -> str:
    pills = []
    for op in n8n.CRUD:
        letter = n8n.CRUD_SHORT[op]
        on = op in ops
        label = f"{CRUD_WORD[letter]}: {'offered' if on else 'not offered'}"
        pills.append(f'<b class="pill {"on" if on else "off"}" title="{label}" aria-label="{label}">{letter}</b>')
    if "ACTION" in ops:
        pills.append('<b class="pill on act" title="Action: offered" aria-label="Action: offered">A</b>')
    return f'<span class="crud{" sm" if small else ""}">{"".join(pills)}</span>'


def method_chip(method: str) -> str:
    return f'<span class="m m-{method.lower()}">{E(method)}</span>'


# ------------------------------------------------------------------ roles ---

def feature_row(lane: str, feature: str, eps: list[dict], routes: dict) -> tuple[str, str]:
    ops = {op for e in eps for op in n8n.ops_of(e)}
    order = {op: i for i, op in enumerate(n8n.CRUD + ["ACTION"])}
    rows, screens, search = [], [], [feature]
    for e in sorted(eps, key=lambda e: (order[e["crud"]], e["path"], e["method"])):
        sc = n8n.screens_for(e, lane, routes)
        for s in sc:
            if s not in screens:
                screens.append(s)
        op_label = "/".join(n8n.CRUD_SHORT[o] for o in n8n.ops_of(e))
        api_only = "" if sc else '<span class="tag" title="No screen in this role\'s navigation calls it">API only</span>'
        rows.append(
            f'<li><span class="op">{op_label}</span>{method_chip(e["method"])}'
            f'<code class="path">{E(e["path"])}</code>'
            f'<span class="verb">{E(n8n.verb_in(e, lane))}{api_only}</span></li>')
        search += [e["path"], n8n.verb_in(e, lane)]
    modules = sorted({e["module"] for e in eps})
    gates = sorted({n8n.gate_in(e["guard"], lane) for e in eps})
    caps = n8n.caps_in(eps, lane)
    tables = sorted({t for e in eps for t in e["tables"]})
    ext = [n8n.EXTERNAL[x]["label"] for x in sorted({x for e in eps for x in e["external"]}) if x in n8n.EXTERNAL]
    search += modules + tables + ext
    meta = [
        ("Screens", " ".join(f"<code>{E(s)}</code>" for s in screens) or "none for this role (API only)"),
        ("Code", " ".join(f"<code>{E(m)}</code>" for m in modules)),
        ("Gate", " · ".join(f"<code>{E(g)}</code>" for g in gates)
         + (" · capability " + " ".join(f"<code>{E(c)}</code>" for c in caps) if caps else "")),
        ("Tables", " ".join(f"<code>{E(t)}</code>" for t in tables) or "none"),
        ("AWS", " · ".join(E(x) for x in ext) or "none"),
    ]
    if (lane, feature) in n8n.CROSS_REFERENCES:
        meta.append(("Also", md_inline(n8n.CROSS_REFERENCES[(lane, feature)])))
    sub = f'{n8n.plural(len(eps), "endpoint")}' + (f' · {E(screens[0])}' if screens else " · API only")
    body = (f'<ul class="eps">{"".join(rows)}</ul>'
            f'<dl class="meta">{"".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in meta)}</dl>')
    html_row = (f'<details class="feat" data-q="{E(" ".join(search).lower())}">'
                f'<summary><span class="fname">{E(feature)}</span>{crud_pills(ops)}'
                f'<span class="fsub">{sub}</span></summary><div class="fbody">{body}</div></details>')
    return html_row, "".join(sorted({n8n.CRUD_SHORT[o] for o in ops}))


def grants_block(grants, routes) -> str:
    items = []
    for key, eps in grants.items():
        feats = sorted({e.get("grant_feature") or n8n.feature_in(e, "MAIN_ADMIN") for e in eps})
        ops = {op for e in eps for op in n8n.ops_of(e)}
        screens = n8n.faculty_screens_for_capability(key, routes)
        note = n8n.GRANT_NOTES.get(key, "")
        q = " ".join([key] + feats + screens).lower()
        items.append(
            f'<details class="feat" data-q="{E(q)}"><summary><span class="fname"><code>{E(key)}</code></span>'
            f'{crud_pills(ops)}<span class="fsub">{n8n.plural(len(eps), "endpoint")} · '
            f'{E(screens[0]) if screens else "API only"}</span></summary><div class="fbody">'
            f'<dl class="meta"><dt>Opens</dt><dd>{E(", ".join(feats))}</dd>'
            f'<dt>Screens</dt><dd>{" ".join(f"<code>{E(s)}</code>" for s in screens) or "none (API only)"}</dd>'
            + (f'<dt>Note</dt><dd>{md_inline(note)}</dd>' if note else "")
            + '</dl></div></details>')
    return (
        '<section class="area" data-area>'
        '<h3>Granted by the Main Admin <span class="count">'
        f'{n8n.plural(len(grants), "capability")}</span></h3>'
        '<p class="note">The Main Admin can hand any <code>admin.*</code> console capability to a named faculty '
        'account in <strong>Who can do what</strong>, with a reason and an expiry. The holder then uses the '
        'Main Admin\'s own endpoints, and rule 2 still limits anything about one student to their mentees.</p>'
        f'<div class="feats">{"".join(items)}</div></section>')


def role_panel(lane: str, groups, grants, routes) -> tuple[str, dict]:
    role = n8n.ROLES[lane]
    areas, n_feat, n_eps, crud_feats = [], 0, 0, Counter()
    for group, feats in groups.items():
        rows = []
        for feature, eps in feats.items():
            row, letters = feature_row(lane, feature, eps, routes)
            rows.append(row)
            n_feat += 1
            n_eps += len(eps)
            for ch in letters:
                crud_feats[ch] += 1
        areas.append(f'<section class="area" data-area><h3>{E(group)} '
                     f'<span class="count">{n8n.plural(len(feats), "feature")}</span></h3>'
                     f'<div class="feats">{"".join(rows)}</div></section>')
    if grants:
        areas.append(grants_block(grants, routes))
    tally = " ".join(f'<span class="t"><b>{crud_feats[ch]}</b> {CRUD_WORD[ch].lower()}</span>'
                     for ch in "CRUDA" if crud_feats[ch])
    head = (f'<header class="rolehead"><h2>{E(role["title"])}</h2>'
            f'<p>{E(LANE_BLURB[lane])}</p>'
            f'<p class="gate"><span class="lbl">Gate</span> {md_inline(role["gate"])}</p>'
            f'<p class="tally"><span class="t"><b>{n_feat}</b> features</span>'
            f'<span class="t"><b>{n_eps}</b> endpoints</span> <span class="sep">features that</span> {tally}</p>'
            '</header>')
    stats = {"features": n_feat, "endpoints": n_eps, "crud": crud_feats}
    return head + "".join(areas) + '<p class="empty" hidden>No feature in this role matches your search.</p>', stats


# ------------------------------------------------------------------ stack ---

SECTIONS = [  # sticky name in the n8n workflow -> tab heading
    ("Request path", "One request, phone to role"),
    ("AWS estate", "What the API task talks to"),
    ("Schedules", "What runs at night"),
    ("Backups", "Backups and disaster recovery"),
    ("Voice platform", "Voice platform"),
    ("CI/CD", "Shipping it"),
]


def stack_panel(wf: dict, inv: dict) -> str:
    nodes = {n["name"]: n for n in wf["nodes"]}
    stickies = {n["name"]: n for n in wf["nodes"] if n["type"].endswith("stickyNote")}
    out_edges: dict[str, list[str]] = {}
    for src, c in wf["connections"].items():
        out_edges[src] = [t["node"] for outs in c["main"] for t in outs]

    def inside(sticky, node):
        x, y = sticky["position"]
        w, h = sticky["parameters"]["width"], sticky["parameters"]["height"]
        nx, ny = node["position"]
        return x <= nx <= x + w and y <= ny <= y + h

    parts = []
    # Request path as a vertical walk, following the chain from the trigger.
    chain, cur, seen = [], "A person opens REEP", set()
    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        nxt = [t for t in out_edges.get(cur, []) if t in nodes and nodes[t]["position"][0] < 0]
        branch = [t for t in nxt if "S3 SPA" in t]
        if branch:
            chain.append(("branch", branch[0]))
        main = [t for t in nxt if "S3 SPA" not in t and "Edge request logs" not in t and "Sentry" not in t]
        cur = main[0] if main else None
    steps = []
    for item in chain:
        if isinstance(item, tuple):
            n = nodes[item[1]]
            steps.append(f'<li class="branch"><span class="nm">{E(n["name"])}</span>'
                         f'<span class="nt">static files: {E(n.get("notes", ""))}</span></li>')
            continue
        n = nodes[item]
        steps.append(f'<li><span class="nm">{E(n["name"])}</span><span class="nt">{E(n.get("notes", ""))}</span></li>')
    lanes_links = " ".join(f'<a class="chip" href="#{LANE_SLUG[l]}" data-go="{LANE_SLUG[l]}">{LANE_SHORT[l]}</a>'
                           for l in LANE_ORDER)
    rp = stickies["Request path"]["parameters"]["content"].split("\n", 1)[1]
    parts.append(f'<section class="area"><h3>One request, phone to role</h3><p class="note">{md_inline(rp)}</p>'
                 f'<ol class="walk">{"".join(steps)}</ol>'
                 f'<p class="note">Then the role decides the lane: {lanes_links}</p></section>')

    for sticky_name, heading in SECTIONS[1:]:
        st = stickies[sticky_name]
        text = st["parameters"]["content"].split("\n", 1)[1] if "\n" in st["parameters"]["content"] else ""
        members = [n for n in wf["nodes"] if not n["type"].endswith("stickyNote") and inside(st, n)]
        members.sort(key=lambda n: (n["position"][1] // 100, n["position"][0]))
        names = {n["name"] for n in members}
        # Reading order, then never a node before one that feeds it: a fan-out is drawn
        # centred on its source, so its first target sits above the source.
        feeds = {name: {s for s in names if name in out_edges.get(s, [])} for name in names}
        ordered, done = [], set()
        while members:
            i = next((i for i, n in enumerate(members) if feeds[n["name"]] <= done), 0)
            ordered.append(members.pop(i))
            done.add(ordered[-1]["name"])
        members = ordered
        items = []
        for n in members:
            targets = [t for t in out_edges.get(n["name"], []) if t in names or nodes[t]["position"][0] < 0]
            arrow = ""
            if targets:
                arrow = '<span class="to">' + ", ".join(E(t) for t in targets) + "</span>"
            off = ' <span class="tag">not running</span>' if n.get("disabled") else ""
            items.append(f'<li><span class="nm">{E(n["name"])}{off}</span>'
                         f'<span class="nt">{E(n.get("notes", ""))}</span>{arrow}</li>')
        parts.append(f'<section class="area"><h3>{E(heading)}</h3><p class="note">{md_inline(text)}</p>'
                     f'<ul class="nodes">{"".join(items)}</ul></section>')

    menu = inv.get("ops_task_menu", [])
    parts.append('<section class="area"><h3>Operator task menu</h3><p class="note"><code>ops-task.yml</code> '
                 'is a fixed list; each entry runs <code>python -m app.&lt;module&gt;</code> as a one-off task on '
                 'the API image, and a purge also asks for its own typed sentence.</p><p class="chips">'
                 + " ".join(f'<code>{E(m)}</code>' for m in menu) + "</p></section>")

    by_dom: dict[str, list[str]] = {}
    for t in inv["tables"]:
        by_dom.setdefault(t["domain"], []).append(t["table"])
    rows = "".join(f'<details class="feat" data-q="{E((d + " " + " ".join(ts)).lower())}"><summary>'
                   f'<span class="fname">{E(d)}</span><span class="fsub">{n8n.plural(len(ts), "table")}</span>'
                   f'</summary><div class="fbody"><p class="chips">'
                   + " ".join(f"<code>{E(t)}</code>" for t in sorted(ts)) + "</p></div></details>"
                   for d, ts in sorted(by_dom.items()))
    parts.append(f'<section class="area" data-area><h3>Every table in RDS <span class="count">'
                 f'{len(inv["tables"])} tables</span></h3><div class="feats">{rows}</div></section>')
    return ('<header class="rolehead"><h2>Stack &amp; AWS</h2><p>The path a request takes, and everything '
            'around the API task: data, files, mail, AI, observability, schedules, backups and the deploy '
            'pipeline.</p></header>' + "".join(parts))


# -------------------------------------------------------------- downloads ---
# Only the published (Artifact) variant carries these: the viewer's frame blocks
# a page-started download, so saving goes through the `downloads` capability,
# and the files are published next to the page and fetched on the tap. The
# repository copy IS the file, so it has nothing to offer.

GITHUB_RAW = "https://github.com/darshani8/reep-/raw/ccr-ea6bd00d-nusgh3/docs/diagrams/n8n/"
DOWNLOADS = [  # (published path, saved name, label); an Artifact cannot host a .zip
    ("reep-roles-features-crud.pdf", "reep-roles-features-crud.pdf", "Printable PDF (.pdf)"),
    ("reep-n8n-canvas.pdf", "reep-n8n-canvas.pdf", "The n8n diagram as n8n draws it (.pdf)"),
    ("reep-roles-features-crud.n8n.json", "reep-roles-features-crud.n8n.json", "n8n workflow (.json)"),
    ("reep-roles-features-crud.html", "reep-roles-features-crud.html", "This page (.html)"),
    ("reep-feature-inventory.json", "reep-feature-inventory.json", "Endpoint inventory (.json)"),
    ("README.md", "reep-n8n-diagram-README.md", "How to read it (.md)"),
]


def downloads_block() -> str:
    buttons = "".join(f'<button type="button" class="dlb" data-file="{E(path)}" data-name="{E(name)}">{E(label)}</button>'
                      for path, name, label in DOWNLOADS)
    links = "".join(f'<li><a href="{GITHUB_RAW}{E(name)}" target="_blank" rel="noopener">{E(name)}</a></li>'
                    for _path, name, _label in DOWNLOADS[:5])
    return (f'<section class="dl" id="downloads" aria-label="Download the files"><h2 class="dl-h">Download</h2>'
            f'<div class="dl-row" data-dl-buttons hidden>{buttons}</div>'
            '<p class="dl-note" data-dl-status role="status">Checking whether this view can save files…</p>'
            '<div class="dl-alt" data-dl-alt hidden><p class="dl-note">This view can\'t save files. The same files '
            f'are in the repository (sign in to GitHub first):</p><ul>{links}</ul></div></section>')


DOWNLOAD_SCRIPT = r"""
(async function () {
  var box = document.getElementById('downloads'); if (!box) return;
  var row = box.querySelector('[data-dl-buttons]'), status = box.querySelector('[data-dl-status]');
  var alt = box.querySelector('[data-dl-alt]');
  var GONE = ['unavailable', 'not_granted', 'capability_disabled', 'capability_removed'];
  function fallback() { row.hidden = true; status.hidden = true; alt.hidden = false; }
  var dl = null;
  try { dl = window.claude && window.claude.use ? await window.claude.use('downloads') : null; } catch (e) { dl = null; }
  if (!dl) { fallback(); return; }
  row.hidden = false; status.textContent = 'Tap a file. Your device asks before it saves anything.';
  row.querySelectorAll('button').forEach(function (b) {
    b.addEventListener('click', async function () {
      var name = b.dataset.name; b.disabled = true; status.textContent = 'Preparing ' + name + '…';
      try {
        var r = await fetch(b.dataset.file);
        if (!r.ok) throw { code: 'fetch', message: String(r.status) };
        await dl.save({ filename: name, data: await r.blob() });
        status.textContent = 'Saved ' + name + '.';
      } catch (e) {
        var c = e && e.code;
        if (GONE.indexOf(c) !== -1) { fallback(); return; }
        status.textContent = c === 'declined' ? name + ' was not saved.'
          : c === 'rate_limited' ? 'Another save prompt is still open. Finish it, then tap again.'
          : c === 'too_large' ? name + ' is too large for this destination.'
          : c === 'fetch' ? 'Could not load ' + name + ' (' + e.message + '). Reload the page and try again.'
          : 'Could not save ' + name + '. Reload the page and try again.';
      } finally { b.disabled = false; }
    });
  });
})();
"""


# ------------------------------------------------------------------- page ---

STYLE = r"""
/* Layout: one reading column; a sticky role switcher; each feature a row that opens in place. */
:root {
  --bg: #f3edf8; --wash: linear-gradient(180deg, #ece4f5 0%, #f2e7f8 45%, #fbe6f2 100%);
  --surface: #ffffff; --surface-2: #f8f3fb; --line: rgba(160, 138, 178, 0.38);
  --ink: #1e1b29; --muted: #585566; --faint: #67637c; --brand: #552c7e;
  --grad: linear-gradient(120deg, #552c7e, #7a2f9e 38%, #a0248f 68%, #ba2185); --on-grad: #ffffff;
  --off: #8b8597; --off-bg: transparent;
  --role: #552c7e; --role-bg: rgba(85, 44, 126, 0.12);
  --r-student: #137a4a; --r-student-bg: rgba(19, 122, 74, 0.12);
  --r-faculty: #2b5ca3; --r-faculty-bg: rgba(43, 92, 163, 0.12);
  --r-alumni: #ad2452; --r-alumni-bg: rgba(173, 36, 82, 0.1);
  --r-admin: #552c7e; --r-admin-bg: rgba(85, 44, 126, 0.12);
  --r-account: #8f6100; --r-account-bg: rgba(217, 154, 0, 0.14);
  --r-public: #4f5a69; --r-public-bg: rgba(79, 90, 105, 0.12);
  --r-stack: #7a2f9e; --r-stack-bg: rgba(122, 47, 158, 0.12);
  --m-get: #2b5ca3; --m-post: #137a4a; --m-put: #8f6100; --m-patch: #8f6100; --m-delete: #ad2452; --m-ws: #7a2f9e;
  --code-bg: rgba(85, 44, 126, 0.07); --shadow: 0 8px 22px rgba(58, 31, 82, 0.08);
  --font-display: 'Plus Jakarta Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-body: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #15111c; --wash: linear-gradient(180deg, #17121f 0%, #1a1322 50%, #1f1220 100%);
    --surface: #1f1929; --surface-2: #261f32; --line: rgba(190, 170, 215, 0.2);
    --ink: #f0eaf7; --muted: #bdb3cc; --faint: #a297b3; --brand: #cfaef0;
    --grad: linear-gradient(120deg, #6b3a9c, #8a3fb0 38%, #ad3a9c 68%, #c23a91); --on-grad: #ffffff;
    --off: #8d8399;
    --r-student: #6ad6a1; --r-student-bg: rgba(106, 214, 161, 0.14);
    --r-faculty: #94b8f2; --r-faculty-bg: rgba(148, 184, 242, 0.14);
    --r-alumni: #f392b4; --r-alumni-bg: rgba(243, 146, 180, 0.13);
    --r-admin: #cfaef0; --r-admin-bg: rgba(207, 174, 240, 0.14);
    --r-account: #e8bd55; --r-account-bg: rgba(232, 189, 85, 0.14);
    --r-public: #b2bccb; --r-public-bg: rgba(178, 188, 203, 0.13);
    --r-stack: #d7a6ef; --r-stack-bg: rgba(215, 166, 239, 0.13);
    --m-get: #94b8f2; --m-post: #6ad6a1; --m-put: #e8bd55; --m-patch: #e8bd55; --m-delete: #f392b4; --m-ws: #d7a6ef;
    --code-bg: rgba(207, 174, 240, 0.1); --shadow: 0 8px 22px rgba(0, 0, 0, 0.3);
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #15111c; --wash: linear-gradient(180deg, #17121f 0%, #1a1322 50%, #1f1220 100%);
  --surface: #1f1929; --surface-2: #261f32; --line: rgba(190, 170, 215, 0.2);
  --ink: #f0eaf7; --muted: #bdb3cc; --faint: #a297b3; --brand: #cfaef0;
  --grad: linear-gradient(120deg, #6b3a9c, #8a3fb0 38%, #ad3a9c 68%, #c23a91); --on-grad: #ffffff;
  --off: #8d8399;
  --r-student: #6ad6a1; --r-student-bg: rgba(106, 214, 161, 0.14);
  --r-faculty: #94b8f2; --r-faculty-bg: rgba(148, 184, 242, 0.14);
  --r-alumni: #f392b4; --r-alumni-bg: rgba(243, 146, 180, 0.13);
  --r-admin: #cfaef0; --r-admin-bg: rgba(207, 174, 240, 0.14);
  --r-account: #e8bd55; --r-account-bg: rgba(232, 189, 85, 0.14);
  --r-public: #b2bccb; --r-public-bg: rgba(178, 188, 203, 0.13);
  --r-stack: #d7a6ef; --r-stack-bg: rgba(215, 166, 239, 0.13);
  --m-get: #94b8f2; --m-post: #6ad6a1; --m-put: #e8bd55; --m-patch: #e8bd55; --m-delete: #f392b4; --m-ws: #d7a6ef;
  --code-bg: rgba(207, 174, 240, 0.1); --shadow: 0 8px 22px rgba(0, 0, 0, 0.3);
  color-scheme: dark;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); background-image: var(--wash); background-attachment: fixed;
  color: var(--ink); font: 15px/1.5 var(--font-body); -webkit-text-size-adjust: 100%; }
.wrap { max-width: 920px; margin: 0 auto; padding-inline: 16px; padding-block: 20px 48px; }
h1, h2, h3 { font-family: var(--font-display); text-wrap: balance; margin: 0; }
h1 { font-size: clamp(1.7rem, 6vw, 2.4rem); font-weight: 800; letter-spacing: -0.01em; line-height: 1.1; }
h2 { font-size: 1.35rem; font-weight: 800; color: var(--role); }
h3 { font-size: 1rem; font-weight: 700; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
code { font-family: var(--font-mono); font-size: 0.82em; background: var(--code-bg); padding: 1px 5px;
  border-radius: 5px; overflow-wrap: anywhere; }
a { color: var(--brand); }
.eyebrow { margin: 0 0 6px; font: 600 0.72rem/1 var(--font-display); letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--faint); }
.lede { margin: 10px 0 0; color: var(--muted); max-width: 62ch; }
.facts { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 14px 0 0; padding: 0; list-style: none;
  color: var(--faint); font-size: 0.85rem; font-variant-numeric: tabular-nums; }
.facts b { color: var(--ink); font-weight: 600; }
.bar { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; margin: 18px -16px 0;
  padding: 10px 16px; background: color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); border-bottom: 1px solid var(--line); }
.tabs { display: flex; gap: 6px; overflow-x: auto; scrollbar-width: none; padding-bottom: 2px; }
.tabs::-webkit-scrollbar { display: none; }
.tab { flex: 0 0 auto; font: 600 0.88rem/1 var(--font-display); color: var(--ink); background: var(--surface);
  border: 1px solid var(--line); border-radius: 999px; padding: 9px 14px; cursor: pointer; }
.tab[aria-selected="true"] { background: var(--grad); color: var(--on-grad); border-color: transparent; }
.tab:focus-visible, summary:focus-visible, .chip:focus-visible, #q:focus-visible {
  outline: 2px solid var(--brand); outline-offset: 2px; }
.search { margin-top: 10px; display: flex; gap: 8px; align-items: center; }
#q { flex: 1; min-width: 0; font: 0.95rem var(--font-body); color: var(--ink); background: var(--surface);
  border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; }
#q::placeholder { color: var(--faint); }
.panel { margin-top: 18px; display: grid; gap: 16px; }
.p-student { --role: var(--r-student); --role-bg: var(--r-student-bg); }
.p-faculty { --role: var(--r-faculty); --role-bg: var(--r-faculty-bg); }
.p-alumni { --role: var(--r-alumni); --role-bg: var(--r-alumni-bg); }
.p-admin { --role: var(--r-admin); --role-bg: var(--r-admin-bg); }
.p-account { --role: var(--r-account); --role-bg: var(--r-account-bg); }
.p-public { --role: var(--r-public); --role-bg: var(--r-public-bg); }
.p-stack { --role: var(--r-stack); --role-bg: var(--r-stack-bg); }
.rolehead { display: grid; gap: 6px; }
.rolehead p { margin: 0; color: var(--muted); max-width: 64ch; }
.gate { font-size: 0.88rem; }
.lbl { font: 600 0.68rem/1 var(--font-display); letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--role); background: var(--role-bg); padding: 3px 6px; border-radius: 5px; margin-right: 4px; }
.tally { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 0.85rem; font-variant-numeric: tabular-nums; }
.tally b { color: var(--ink); }
.tally .sep { color: var(--faint); }
.area { display: grid; gap: 8px; }
.area h3 .count { font: 500 0.78rem var(--font-body); color: var(--faint); }
.note { margin: 0; color: var(--muted); font-size: 0.9rem; max-width: 66ch; }
.feats { display: grid; gap: 6px; }
.feat { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow); }
.feat > summary { list-style: none; cursor: pointer; display: grid; grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 10px; align-items: center; padding: 12px 14px; }
.feat > summary::-webkit-details-marker { display: none; }
.fname { font: 650 0.95rem/1.3 var(--font-display); min-width: 0; overflow-wrap: anywhere; }
.fsub { grid-column: 1 / -1; color: var(--faint); font-size: 0.8rem; min-width: 0; overflow-wrap: anywhere; }
.feat[open] > summary { border-bottom: 1px dashed var(--line); }
.fbody { padding: 10px 14px 14px; display: grid; gap: 12px; min-width: 0; }
.crud { display: inline-flex; gap: 4px; }
.pill { font: 700 0.72rem/1 var(--font-display); width: 24px; height: 24px; display: inline-grid;
  place-items: center; border-radius: 7px; }
.pill.on { background: var(--role); color: var(--surface); }
.pill.off { color: var(--off); border: 1.5px dashed var(--off); text-decoration: line-through; }
.pill.act { background: var(--role-bg); color: var(--role); }
.eps { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.eps li { display: grid; grid-template-columns: auto auto minmax(0, 1fr); gap: 2px 8px; align-items: center; }
.op { font: 700 0.7rem/1 var(--font-display); color: var(--role); min-width: 22px; }
.m { font: 600 0.66rem/1 var(--font-mono); padding: 4px 0; width: 52px; text-align: center; border-radius: 5px;
  border: 1px solid currentColor; }
.m-get { color: var(--m-get); } .m-post { color: var(--m-post); } .m-put { color: var(--m-put); }
.m-patch { color: var(--m-patch); } .m-delete { color: var(--m-delete); } .m-ws { color: var(--m-ws); }
.path { background: none; padding: 0; font-size: 0.78rem; color: var(--ink); }
.verb { grid-column: 3; color: var(--muted); font-size: 0.82rem; display: flex; gap: 6px; flex-wrap: wrap;
  align-items: center; }
.tag { font: 600 0.62rem/1 var(--font-display); letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--faint); border: 1px solid var(--line); border-radius: 999px; padding: 3px 6px; }
.meta { margin: 0; display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px 12px;
  font-size: 0.82rem; }
.meta dt { font: 600 0.66rem/1.9 var(--font-display); letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--faint); }
.meta dd { margin: 0; color: var(--muted); min-width: 0; overflow-wrap: anywhere; }
.walk { list-style: none; margin: 0; padding: 0 0 0 18px; display: grid; gap: 0; position: relative; }
.walk::before { content: ""; position: absolute; left: 6px; top: 8px; bottom: 8px; width: 2px;
  background: var(--role-bg); }
.walk li { position: relative; display: grid; gap: 1px; padding: 8px 0; }
.walk li::before { content: ""; position: absolute; left: -17px; top: 14px; width: 10px; height: 10px;
  border-radius: 50%; background: var(--role); }
.walk li.branch { margin-left: 16px; }
.walk li.branch::before { background: var(--surface); border: 2px solid var(--role); }
.nm { font: 650 0.9rem/1.3 var(--font-display); overflow-wrap: anywhere; }
.nt { color: var(--faint); font-size: 0.8rem; overflow-wrap: anywhere; }
.nodes { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.nodes li { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px;
  display: grid; gap: 2px; min-width: 0; }
.to { font-size: 0.8rem; color: var(--muted); overflow-wrap: anywhere; }
.to::before { content: "→ "; color: var(--role); font-weight: 700; }
.chips { margin: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font: 600 0.8rem/1 var(--font-display); text-decoration: none; color: var(--role);
  background: var(--role-bg); padding: 7px 10px; border-radius: 999px; }
.empty { color: var(--faint); margin: 0; }
.dl { margin-top: 16px; display: grid; gap: 8px; }
.dl-h { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--faint); }
.dl-row { display: flex; flex-wrap: wrap; gap: 8px; }
.dlb { font: 600 0.85rem/1 var(--font-display); color: var(--ink); background: var(--surface);
  border: 1px solid var(--line); border-radius: 12px; padding: 11px 14px; cursor: pointer; box-shadow: var(--shadow); }
.dlb:first-child { background: var(--grad); color: var(--on-grad); border-color: transparent; }
.dlb:disabled { opacity: 0.6; cursor: progress; }
.dlb:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.dl-note { margin: 0; color: var(--faint); font-size: 0.82rem; }
.dl-alt ul { margin: 4px 0 0; padding-left: 18px; font-size: 0.85rem; overflow-wrap: anywhere; }
.legend { margin-top: 28px; padding-top: 16px; border-top: 1px solid var(--line); color: var(--faint);
  font-size: 0.82rem; display: grid; gap: 8px; }
.legend .crud { vertical-align: middle; }
@media (min-width: 720px) {
  .feat > summary { grid-template-columns: minmax(0, 1fr) auto; }
  .fsub { grid-column: 1; }
  .feat > summary .crud { grid-row: 1 / span 2; grid-column: 2; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""

SCRIPT = r"""
(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('.panel'));
  var q = document.getElementById('q');
  function show(slug, push) {
    var found = false;
    tabs.forEach(function (t) {
      var on = t.dataset.slug === slug; t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1; if (on) { found = true; t.scrollIntoView({ block: 'nearest', inline: 'center' }); }
    });
    if (!found) return show(tabs[0].dataset.slug, push);
    panels.forEach(function (p) { p.hidden = p.dataset.slug !== slug; });
    if (push) { try { history.replaceState(null, '', '#' + slug); } catch (e) {} }
    try { localStorage.setItem('reep-crud-tab', slug); } catch (e) {}
    filter();
  }
  function filter() {
    var term = (q.value || '').trim().toLowerCase();
    var panel = panels.filter(function (p) { return !p.hidden; })[0];
    if (!panel) return;
    var any = false;
    panel.querySelectorAll('[data-area]').forEach(function (area) {
      var shown = 0;
      area.querySelectorAll('.feat').forEach(function (f) {
        var hit = !term || f.dataset.q.indexOf(term) !== -1;
        f.hidden = !hit; if (hit) shown++;
        if (term && hit && term.length > 2) f.open = true;
      });
      area.hidden = shown === 0; if (shown) any = true;
    });
    var empty = panel.querySelector('.empty');
    if (empty) empty.hidden = any || !term;
  }
  tabs.forEach(function (t, i) {
    t.addEventListener('click', function () { show(t.dataset.slug, true); });
    t.addEventListener('keydown', function (e) {
      var d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (!d) return; e.preventDefault();
      var n = tabs[(i + d + tabs.length) % tabs.length]; n.focus(); show(n.dataset.slug, true);
    });
  });
  document.querySelectorAll('[data-go]').forEach(function (a) {
    a.addEventListener('click', function (e) { e.preventDefault(); show(a.dataset.go, true); window.scrollTo(0, 0); });
  });
  q.addEventListener('input', filter);
  var start = (location.hash || '').replace('#', '');
  if (!start) { try { start = localStorage.getItem('reep-crud-tab') || ''; } catch (e) {} }
  show(start || tabs[0].dataset.slug, false);
})();
"""


def build(fragment: bool) -> str:
    inv = json.loads(n8n.INVENTORY.read_text(encoding="utf-8"))
    wf = json.loads(n8n.OUT.read_text(encoding="utf-8"))
    routes = {r["path"]: r for r in inv["routes"]}
    lanes, grants = n8n.build_lanes(inv["endpoints"])

    tabs, panels, total_features = [], [], 0
    for i, lane in enumerate(LANE_ORDER):
        slug = LANE_SLUG[lane]
        body, stats = role_panel(lane, lanes[lane], grants if lane == "FACULTY" else None, routes)
        total_features += stats["features"]
        tabs.append(f'<button class="tab" role="tab" id="tab-{slug}" data-slug="{slug}" aria-controls="panel-{slug}" '
                    f'aria-selected="{"true" if i == 0 else "false"}">{LANE_SHORT[lane]}</button>')
        panels.append(f'<section class="panel p-{slug}" id="panel-{slug}" data-slug="{slug}" role="tabpanel" '
                      f'aria-labelledby="tab-{slug}"{"" if i == 0 else " hidden"}>{body}</section>')
    tabs.append('<button class="tab" role="tab" id="tab-stack" data-slug="stack" aria-controls="panel-stack" '
                'aria-selected="false">Stack &amp; AWS</button>')
    panels.append('<section class="panel p-stack" id="panel-stack" data-slug="stack" role="tabpanel" '
                  f'aria-labelledby="tab-stack" hidden>{stack_panel(wf, inv)}'
                  '<p class="empty" hidden>Nothing here matches your search.</p></section>')

    about = inv["about"]
    head = f"""<title>{TITLE}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500;600&family=Plus+Jakarta+Sans:wght@600;650;700;800&display=swap">
<style>{STYLE}</style>
"""
    body = f"""<main class="wrap">
  <header>
    <p class="eyebrow">REEP · roles, features and CRUD</p>
    <h1>Who can do what in REEP</h1>
    <p class="lede">Every role's features, and for each one which of create, read, update and delete that role is
      offered, with the API endpoints, screens, code, tables and AWS services behind it. Tap a feature to open it.</p>
    <ul class="facts">
      <li><b>{len(inv['endpoints'])}</b> endpoints</li>
      <li><b>{total_features}</b> role-features</li>
      <li><b>{len(inv['tables'])}</b> tables</li>
      <li>taken {E(about['taken_on'])} at <code>{E(about['commit'])}</code></li>
    </ul>
    {downloads_block() if fragment else ""}
  </header>
  <div class="bar">
    <div class="tabs" role="tablist" aria-label="Role">{''.join(tabs)}</div>
    <div class="search"><input id="q" type="search" placeholder="Search features, paths, tables" aria-label="Search this tab" autocomplete="off"></div>
  </div>
  {''.join(panels)}
  <footer class="legend">
    <p>{crud_pills({'CREATE', 'READ'}, small=True)} A filled letter is an operation this role is offered; a
      struck-out one is not. <b>A</b> marks an action such as a sign-in, a code or a live interview.
      <span class="tag">API only</span> marks an endpoint no screen in this role's navigation calls.</p>
    <p>Generated from <code>docs/diagrams/n8n/reep-feature-inventory.json</code> by
      <code>tools/diagrams/render_roles_crud_page.py</code>, with the same rules as the n8n workflow
      <code>reep-roles-features-crud.n8n.json</code>.</p>
  </footer>
</main>
<script>{SCRIPT}</script>
{f"<script>{DOWNLOAD_SCRIPT}</script>" if fragment else ""}
"""
    if fragment:
        return head + body
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            "<meta name=\"color-scheme\" content=\"light dark\">\n" + head + "</head>\n<body>\n"
            + body + "</body>\n</html>\n")


def main() -> int:
    OUT.write_text(build(fragment=False), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")
    if "--fragment" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--fragment") + 1])
        path.write_text(build(fragment=True), encoding="utf-8")
        print(f"wrote {path} (fragment for publishing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
