#!/usr/bin/env python
"""orc - dispatch a coding task to a worker model from the command line.

This is a DEV TOOL. It is not part of the REEP app and imports nothing from it.
The orchestrator (Claude Code) plans the work and calls this to have a worker
model write the code; the raw reply is saved, the proposed files are diffed
against the tree, and nothing is written until --apply is passed.

TWO BACKENDS, and the default is local:

  ollama      (default) a model on this machine, http://localhost:11434.
              No key, no egress, no cost. Slower, and the context window is
              small enough that it is a real constraint - see --ctx below.
  openrouter  a hosted model. Needs OPENROUTER_API_KEY in the environment or
              tools/coder/.env (gitignored by the repo's `**/.env` rule).

Pick with --provider, or $ORC_PROVIDER for a whole shell.

    python orc.py check
    python orc.py models
    python orc.py ask "why does this test hang?" -c apps/api-py/tests/test_x.py
    python orc.py code "add a --json flag" -c src/cli.py -w src/cli.py
    python orc.py code ... --apply
    python orc.py code ... --provider openrouter -m qwen/qwen3-coder

WRITE PROTOCOL - the worker must emit whole files, never diffs:

    === FILE: relative/path.py ===
    <complete file content>
    === END FILE ===

THE CONTEXT WINDOW IS THE THING THAT WILL BITE YOU. Ollama does not error when
a prompt overflows num_ctx - it silently drops the front of it, and the worker
then writes confident code against a file it never saw. So orc sizes the byte
budget from the model's real window and REFUSES to send an oversized prompt.
Raise --ctx if the model can take it and you have the VRAM; do not raise it
blindly, because a window the GPU cannot hold spills to system RAM and costs
an order of magnitude in speed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

TOOL_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOL_DIR.parent.parent
WORK_DIR = TOOL_DIR / ".orc"

OPENROUTER_API = "https://openrouter.ai/api/v1"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")

# qwen3:14b, on measurement. It is the stronger coder of the installed models
# (it is the only one declaring tools and thinking), and with the GPU to itself
# it runs at ~26 tok/s and sits 100% on the card at --ctx 16384.
#
# It is a tight fit: 10 GB resident leaves well under 1 GB spare on a 12 GB
# card, so anything else that grabs VRAM pushes it into system RAM. Measured on
# this machine with a video generator holding ~3 GB, the SAME model on the SAME
# prompt fell to 1.6 tok/s - a 16x collapse - while `ollama ps` still cheerfully
# reported "100% GPU". If generation is inexplicably slow, that is the first
# thing to check, and gemma3:12b (7.9 GB) is the fallback that still fits.
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
DEFAULT_OPENROUTER_MODEL = "qwen/qwen3-coder"

# Every change to num_ctx forces Ollama to unload and reload the model (~20s on
# a 14B). Keeping one value across calls is worth more than sizing it per task.
# 16k is what a 9 GB model plus its KV cache fits into a 12 GB card; raising it
# is the fastest way to push the model off the GPU and into a 20x slowdown.
DEFAULT_CTX = 16384
# The reply shares the window with the prompt, so the local ceiling has to be a
# fraction of DEFAULT_CTX, not the hosted-model figure. A local worker is not
# going to emit 16k tokens anyway - at 40 tok/s that is seven minutes.
DEFAULT_MAX_TOKENS_LOCAL = 6000
DEFAULT_MAX_TOKENS_REMOTE = 16000
# Keep the model resident between calls so a sequence of edits pays the load once.
DEFAULT_KEEP_ALIVE = "30m"
# Bytes per token, deliberately pessimistic: source code with indentation and
# punctuation tokenises worse than prose, and under-estimating here is the one
# error that produces silent truncation.
BYTES_PER_TOKEN = 3.0
# Hosted models have windows large enough that the old flat cap still applies.
OPENROUTER_MAX_CONTEXT_BYTES = 400_000

FILE_BLOCK = re.compile(
    r"^===\s*FILE:\s*(?P<path>.+?)\s*===\r?\n(?P<body>.*?)^===\s*END FILE\s*===",
    re.DOTALL | re.MULTILINE,
)

CODE_SYSTEM = """You are a senior engineer working inside an existing repository.
You are given the exact files you need. Read them before you write.

Rules:
1. Match the surrounding code - its naming, its idiom, its comment density, its
   error handling. Do not introduce a new style, a new dependency, or a new
   abstraction layer unless the task explicitly asks for one.
2. Change only what the task requires. Do not reformat untouched lines, do not
   reorder imports you did not add, do not "clean up" adjacent code.
3. If the task is impossible or the given context is insufficient, say so in
   plain prose and emit NO file blocks. A wrong guess is worse than a question.

Output format. For every file you are changing or creating, emit the COMPLETE
final file - never a diff, never an excerpt, never "... rest unchanged ...":

=== FILE: relative/path/from/repo/root.py ===
<the entire file, top to bottom>
=== END FILE ===

Write only to the paths you were told you may write. Put any explanation in
prose BEFORE the first file block, and keep it to a few sentences."""

ASK_SYSTEM = """You are a senior engineer answering a question about an existing
codebase. You are given the relevant files. Be concrete and cite file and line
where it helps. Do not emit file blocks - this is a question, not a change.
If the given context does not answer the question, say what is missing."""


def die(msg: str) -> None:
    print(f"orc: {msg}", file=sys.stderr)
    raise SystemExit(2)


def warn(msg: str) -> None:
    print(f"orc: warning: {msg}", file=sys.stderr)


def note(msg: str) -> None:
    print(f"[orc] {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- config


def provider_of(args) -> str:
    p = (getattr(args, "provider", None) or os.environ.get("ORC_PROVIDER", "") or "ollama").strip().lower()
    if p not in {"ollama", "openrouter"}:
        die(f"unknown provider '{p}' - expected 'ollama' or 'openrouter'")
    return p


def model_of(args, provider: str) -> str:
    explicit = getattr(args, "model", None) or os.environ.get("ORC_MODEL", "").strip()
    if explicit:
        return explicit
    return DEFAULT_OLLAMA_MODEL if provider == "ollama" else DEFAULT_OPENROUTER_MODEL


def max_tokens_of(args, provider: str) -> int:
    if getattr(args, "max_tokens", None):
        return int(args.max_tokens)
    return DEFAULT_MAX_TOKENS_LOCAL if provider == "ollama" else DEFAULT_MAX_TOKENS_REMOTE


def ctx_of(args) -> int:
    if getattr(args, "ctx", None):
        return int(args.ctx)
    env = os.environ.get("ORC_CTX", "").strip()
    return int(env) if env.isdigit() else DEFAULT_CTX


def load_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        envfile = TOOL_DIR / ".env"
        if envfile.exists():
            for line in envfile.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY"):
                    key = line.split("=", 1)[1].strip().strip("\"'")
                    break
    if not key:
        die(
            "No OPENROUTER_API_KEY.\n"
            f"  Put it in {TOOL_DIR / '.env'} as:  OPENROUTER_API_KEY=sk-or-v1-...\n"
            "  (that path is already gitignored), or export it in the shell.\n"
            "  Or stay local: drop --provider openrouter and use Ollama."
        )
    if not key.startswith("sk-or-"):
        warn(
            f"key does not start with 'sk-or-' (got '{key[:6]}...'). "
            "OpenRouter keys look like sk-or-v1-... - a Groq/OpenAI key will 401 here."
        )
    return key


# --------------------------------------------------------------------------- ollama


def ollama_get(path: str, timeout: float = 15.0) -> dict:
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(f"{OLLAMA_HOST}{path}")
    except httpx.HTTPError as exc:
        die(
            f"cannot reach Ollama at {OLLAMA_HOST} ({type(exc).__name__}).\n"
            "  Is it running?  Start it with:  ollama serve\n"
            "  (or set $OLLAMA_HOST if it listens elsewhere)"
        )
    if r.status_code != 200:
        die(f"Ollama {path} -> HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def ollama_show(model: str) -> dict:
    try:
        with httpx.Client(timeout=30.0) as c:
            r = c.post(f"{OLLAMA_HOST}/api/show", json={"model": model})
    except httpx.HTTPError as exc:
        die(f"cannot reach Ollama at {OLLAMA_HOST} ({type(exc).__name__}). Is `ollama serve` running?")
    if r.status_code == 404:
        installed = [m["name"] for m in ollama_get("/api/tags").get("models", [])]
        die(
            f"model '{model}' is not installed.\n"
            f"  Installed: {', '.join(installed) or '(none)'}\n"
            f"  Pull it with:  ollama pull {model}"
        )
    if r.status_code != 200:
        die(f"Ollama /api/show -> HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def model_facts(model: str) -> tuple[int, list[str]]:
    """(max context window in tokens, capabilities) for an installed model."""
    data = ollama_show(model)
    caps = data.get("capabilities") or []
    info = data.get("model_info") or {}
    win = 0
    for k, v in info.items():
        if k.endswith(".context_length") and isinstance(v, int):
            win = max(win, v)
    return win, caps


def ollama_chat(model: str, messages: list[dict], temperature: float, max_tokens: int,
                num_ctx: int, think: bool | None, keep_alive: str, stream: bool) -> tuple[str, dict]:
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": keep_alive,
        "options": {
            "num_ctx": num_ctx,
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if think is not None:
        payload["think"] = think

    if not stream:
        try:
            with httpx.Client(timeout=None) as c:
                r = c.post(f"{OLLAMA_HOST}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            die(f"Ollama request failed: {type(exc).__name__}: {exc}")
        if r.status_code != 200:
            die(f"Ollama /api/chat -> HTTP {r.status_code}: {r.text[:600]}")
        data = r.json()
        return (data.get("message") or {}).get("content") or "", data

    # Streaming. At local speeds a silent call is indistinguishable from a hang,
    # so a progress counter goes to stderr while the content accumulates.
    chunks: list[str] = []
    final: dict = {}
    started = time.time()
    last_tick = started
    try:
        with httpx.Client(timeout=None) as c:
            with c.stream("POST", f"{OLLAMA_HOST}/api/chat", json=payload) as r:
                if r.status_code != 200:
                    body = b"".join(r.iter_bytes()).decode("utf-8", "replace")
                    die(f"Ollama /api/chat -> HTTP {r.status_code}: {body[:600]}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        die(f"Ollama error: {obj['error']}")
                    piece = (obj.get("message") or {}).get("content") or ""
                    if piece:
                        chunks.append(piece)
                    if obj.get("done"):
                        final = obj
                        break
                    now = time.time()
                    if now - last_tick >= 2.0:
                        n = sum(len(x) for x in chunks)
                        el = now - started
                        sys.stderr.write(f"\r[orc] generating... {n:,} chars in {el:.0f}s ")
                        sys.stderr.flush()
                        last_tick = now
    except httpx.HTTPError as exc:
        die(f"Ollama stream failed: {type(exc).__name__}: {exc}")
    sys.stderr.write("\r" + " " * 60 + "\r")
    sys.stderr.flush()
    return "".join(chunks), final


def report_ollama_usage(data: dict, model: str) -> None:
    if not data:
        return
    ev = data.get("eval_count") or 0
    ed = data.get("eval_duration") or 0
    pe = data.get("prompt_eval_count") or 0
    rate = f"{ev / (ed / 1e9):.1f} tok/s" if ev and ed else "?"
    total = (data.get("total_duration") or 0) / 1e9
    load = (data.get("load_duration") or 0) / 1e9
    bits = [f"in={pe}", f"out={ev}", rate, f"{total:.1f}s total"]
    if load > 1.0:
        bits.append(f"({load:.0f}s of it model load)")
    print(f"\n[usage] {model}  " + "  ".join(bits), file=sys.stderr)
    if data.get("done_reason") == "length":
        warn("reply hit num_predict and was TRUNCATED - raise --max-tokens or split the task")


# --------------------------------------------------------------------------- openrouter


def post_chat_openrouter(key: str, model: str, messages: list[dict],
                         temperature: float, max_tokens: int) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/reep-dashboard",
        "X-Title": "reep-orc",
    }
    last = ""
    for attempt in range(4):
        try:
            with httpx.Client(timeout=300.0) as client:
                r = client.post(f"{OPENROUTER_API}/chat/completions", json=payload, headers=headers)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:600]}"
            if r.status_code in (408, 429, 500, 502, 503, 504) and attempt < 3:
                wait = 2 ** attempt * 3
                warn(f"{last}  - retrying in {wait}s ({attempt + 1}/3)")
                time.sleep(wait)
                continue
            die(last)
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < 3:
                wait = 2 ** attempt * 3
                warn(f"{last} - retrying in {wait}s ({attempt + 1}/3)")
                time.sleep(wait)
                continue
    die(f"gave up after 4 attempts. Last error: {last}")
    return {}


def report_openrouter_usage(data: dict) -> None:
    u = data.get("usage") or {}
    if not u:
        return
    bits = [f"in={u.get('prompt_tokens', '?')}", f"out={u.get('completion_tokens', '?')}"]
    cost = u.get("cost")
    if cost is not None:
        bits.append(f"cost=${float(cost):.5f}")
    print(f"\n[usage] {data.get('model', '?')}  " + "  ".join(bits), file=sys.stderr)


# --------------------------------------------------------------------------- context


def resolve(p: str) -> Path:
    path = Path(p)
    full = (path if path.is_absolute() else REPO_ROOT / path).resolve()
    try:
        full.relative_to(REPO_ROOT)
    except ValueError:
        die(f"path escapes the repo: {p}")
    return full


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def context_budget(provider: str, num_ctx: int, max_tokens: int) -> int:
    """Bytes of context the prompt may carry before it risks silent truncation."""
    if provider == "openrouter":
        return OPENROUTER_MAX_CONTEXT_BYTES
    # System prompt, task text and the model's own reply all live in the same
    # window. Reserve the reply plus a slab for the framing.
    usable = num_ctx - max_tokens - 1200
    if usable < 500:
        die(
            f"--ctx {num_ctx:,} leaves no room for a prompt once --max-tokens "
            f"{max_tokens:,} is reserved. Raise --ctx or lower --max-tokens."
        )
    return int(usable * BYTES_PER_TOKEN)


def build_context(paths: list[str], budget: int, provider: str, num_ctx: int) -> str:
    if not paths:
        return ""
    chunks, total = [], 0
    for p in paths:
        full = resolve(p)
        if not full.exists():
            die(f"context file not found: {p}")
        if full.is_dir():
            die(f"context must be a file, not a directory: {p}")
        text = full.read_text(encoding="utf-8", errors="replace")
        total += len(text)
        if total > budget:
            extra = (
                f"\n  The window is {num_ctx:,} tokens (--ctx). Ollama would DROP the "
                "overflow without saying so, and the worker would write against a file "
                "it never saw."
                if provider == "ollama" else ""
            )
            die(
                f"context is {total:,} bytes at {p}, over the {budget:,}-byte budget.{extra}\n"
                "  Send fewer files - a worker that is drowning writes worse code."
            )
        chunks.append(f"--- {rel(full)} ({len(text.splitlines())} lines) ---\n{text}")
    return "REPOSITORY CONTEXT\n\n" + "\n\n".join(chunks)


def read_task(task: str) -> str:
    if task == "-":
        return sys.stdin.read()
    p = Path(task)
    if p.suffix in {".md", ".txt"} and p.exists():
        return p.read_text(encoding="utf-8")
    return task


# --------------------------------------------------------------------------- apply


def parse_blocks(reply: str) -> list[tuple[str, str]]:
    out = []
    for m in FILE_BLOCK.finditer(reply):
        body = m.group("body")
        # strip a stray markdown fence the model wrapped the body in
        lines = body.splitlines(keepends=True)
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
        out.append((m.group("path").strip(), "".join(lines)))
    return out


def show_diff(path: Path, new: str) -> bool:
    old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if old == new:
        print(f"  = {rel(path)} (no change)")
        return False
    diff = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel(path)}" if old else "/dev/null",
            tofile=f"b/{rel(path)}",
            n=3,
        )
    )
    adds = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    dels = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    marker = "~" if old else "+"
    print(f"\n  {marker} {rel(path)}  (+{adds} -{dels})")
    sys.stdout.write("".join(diff))
    if diff and not diff[-1].endswith("\n"):
        sys.stdout.write("\n")
    return True


def handle_blocks(reply: str, allowed: list[str], apply: bool) -> int:
    blocks = parse_blocks(reply)
    if not blocks:
        print("\n[orc] no file blocks in the reply - nothing to apply.", file=sys.stderr)
        return 0
    allow = {resolve(a) for a in allowed} if allowed else None
    changed = 0
    for raw_path, content in blocks:
        full = resolve(raw_path)
        if allow is not None and full not in allow:
            warn(f"SKIPPED {raw_path} - not in --write list")
            continue
        if show_diff(full, content):
            changed += 1
            if apply:
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content, encoding="utf-8", newline="")
    print()
    if apply:
        print(f"[orc] applied {changed} file(s).")
    else:
        print(f"[orc] dry run - {changed} file(s) would change. Re-run with --apply to write.")
    return changed


# --------------------------------------------------------------------------- commands


def cmd_check(args) -> int:
    provider = provider_of(args)
    model = model_of(args, provider)
    if provider == "openrouter":
        key = load_key()
        with httpx.Client(timeout=30.0) as c:
            r = c.get(f"{OPENROUTER_API}/key", headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            die(f"key rejected - HTTP {r.status_code}: {r.text[:300]}")
        d = r.json().get("data", {})
        limit, usage = d.get("limit"), d.get("usage")
        print("orc: openrouter key OK")
        print(f"  label:     {d.get('label') or '(none)'}")
        print(f"  usage:     {usage if usage is None else '$' + str(usage)}")
        print(f"  limit:     {'unlimited' if limit is None else '$' + str(limit)}")
        print(f"  free tier: {d.get('is_free_tier')}")
        print(f"  model:     {model}  (override with --model or ORC_MODEL)")
        return 0

    tags = ollama_get("/api/tags")
    installed = [m["name"] for m in tags.get("models", [])]
    print(f"orc: ollama OK at {OLLAMA_HOST}")
    print(f"  installed: {', '.join(installed) or '(none)'}")
    if model not in installed:
        warn(f"default model '{model}' is NOT installed - pull it or pass --model")
        return 1
    win, caps = model_facts(model)
    num_ctx = ctx_of(args)
    print(f"  model:     {model}  (override with --model or ORC_MODEL)")
    print(f"  window:    {win:,} tokens max; orc will request --ctx {num_ctx:,}")
    print(f"  caps:      {', '.join(caps) or '(none reported)'}")
    max_tokens = max_tokens_of(args, "ollama")
    budget = context_budget("ollama", num_ctx, max_tokens)
    print(f"  context:   {budget:,} bytes of -c files at --max-tokens {max_tokens:,}")
    if win and num_ctx > win:
        warn(f"--ctx {num_ctx:,} exceeds the model's {win:,}-token window; Ollama will clamp it")

    ps = ollama_get("/api/ps").get("models", [])
    loaded = next((m for m in ps if m.get("name") == model), None)
    if loaded:
        vram = loaded.get("size_vram") or 0
        size = loaded.get("size") or 0
        where = "100% GPU" if size and vram >= size else (f"{100 * vram // size}% GPU" if size else "?")
        print(f"  resident:  yes, {where}, ctx {loaded.get('context_length', '?')}")
    else:
        print("  resident:  no (first call pays the model load, ~20s for a 14B)")
    return 0


def cmd_models(args) -> int:
    provider = provider_of(args)
    if provider == "openrouter":
        key = load_key()
        with httpx.Client(timeout=60.0) as c:
            r = c.get(f"{OPENROUTER_API}/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            die(f"HTTP {r.status_code}: {r.text[:300]}")
        rows = r.json().get("data", [])
        if args.grep:
            needle = args.grep.lower()
            rows = [m for m in rows if needle in m["id"].lower() or needle in (m.get("name") or "").lower()]
        rows.sort(key=lambda m: float((m.get("pricing") or {}).get("prompt") or 0))
        for m in rows[: args.limit]:
            pr = m.get("pricing") or {}
            pin = float(pr.get("prompt") or 0) * 1_000_000
            pout = float(pr.get("completion") or 0) * 1_000_000
            ctx = m.get("context_length") or 0
            tag = "FREE" if pin == 0 and pout == 0 else f"${pin:.2f}/${pout:.2f} per Mtok"
            print(f"{m['id']:<52} {ctx:>9,} ctx  {tag}")
        print(f"\n{min(len(rows), args.limit)} of {len(rows)} matching model(s).", file=sys.stderr)
        return 0

    rows = ollama_get("/api/tags").get("models", [])
    if args.grep:
        needle = args.grep.lower()
        rows = [m for m in rows if needle in m["name"].lower()]
    for m in sorted(rows, key=lambda m: m["name"]):
        det = m.get("details") or {}
        gb = (m.get("size") or 0) / 1e9
        win, caps = model_facts(m["name"])
        flags = ",".join(c for c in caps if c != "completion") or "-"
        print(f"{m['name']:<26} {gb:5.1f} GB  {det.get('parameter_size', '?'):>7}  "
              f"{det.get('quantization_level', '?'):<8} {win:>7,} ctx  {flags}")
    print(f"\n{len(rows)} local model(s). Pull more with `ollama pull <name>`.", file=sys.stderr)
    return 0


def run_chat(args, system: str, user: str) -> str:
    provider = provider_of(args)
    model = model_of(args, provider)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    note(f"{provider}:{model} - {len(user):,} chars of prompt")

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "last-request.md").write_text(user, encoding="utf-8")

    if provider == "ollama":
        num_ctx = ctx_of(args)
        win, caps = model_facts(model)
        if win and num_ctx > win:
            warn(f"--ctx {num_ctx:,} > the model's {win:,}-token window; clamping to {win:,}")
            num_ctx = win
        think = None
        if "thinking" in caps:
            think = bool(args.think)
            if not think:
                note("thinking disabled (--think to enable; it is slower but reasons harder)")
        elif args.think:
            warn(f"{model} has no thinking capability - --think ignored")
        reply, meta = ollama_chat(
            model, messages, args.temperature, max_tokens_of(args, provider),
            num_ctx, think, args.keep_alive, stream=not args.no_stream,
        )
        (WORK_DIR / "last-response.md").write_text(reply, encoding="utf-8")
        report_ollama_usage(meta, model)
    else:
        key = load_key()
        data = post_chat_openrouter(key, model, messages, args.temperature,
                                    max_tokens_of(args, provider))
        choices = data.get("choices") or []
        if not choices:
            die(f"no choices in response: {json.dumps(data)[:600]}")
        reply = choices[0].get("message", {}).get("content") or ""
        (WORK_DIR / "last-response.md").write_text(reply, encoding="utf-8")
        if choices[0].get("finish_reason") == "length":
            warn("reply was TRUNCATED (finish_reason=length) - raise --max-tokens or split the task")
        report_openrouter_usage(data)

    if not reply.strip():
        warn("the model returned an empty reply")
    note(f"raw reply saved to {rel(WORK_DIR / 'last-response.md')}")
    return reply


def cmd_ask(args) -> int:
    provider = provider_of(args)
    budget = context_budget(provider, ctx_of(args), max_tokens_of(args, provider))
    parts = [
        build_context(args.context, budget, provider, ctx_of(args)),
        f"QUESTION\n\n{read_task(args.task)}",
    ]
    print(run_chat(args, ASK_SYSTEM, "\n\n".join(p for p in parts if p)))
    return 0


def cmd_code(args) -> int:
    provider = provider_of(args)
    budget = context_budget(provider, ctx_of(args), max_tokens_of(args, provider))
    if not args.write:
        warn("no --write paths given; every file block the model emits will be diffed")
    parts = [build_context(args.context, budget, provider, ctx_of(args))]
    if args.write:
        allowed = "\n".join(f"- {rel(resolve(w))}" for w in args.write)
        parts.append(f"YOU MAY WRITE ONLY THESE PATHS\n\n{allowed}")
    parts.append(f"TASK\n\n{read_task(args.task)}")
    reply = run_chat(args, CODE_SYSTEM, "\n\n".join(p for p in parts if p))
    head = FILE_BLOCK.split(reply)[0].strip() if FILE_BLOCK.search(reply) else reply.strip()
    if head:
        print("\n--- worker's notes ---")
        print(head[:4000])
        print("--- end notes ---")
    handle_blocks(reply, args.write, args.apply)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="orc",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def shared(p, with_task=True):
        if with_task:
            p.add_argument("task", help="the task/question, a path to a .md/.txt brief, or '-' for stdin")
            p.add_argument(
                "-c", "--context", action="append", default=[], metavar="PATH",
                help="repo-relative file to send as context (repeatable)",
            )
        p.add_argument("--provider", default=None, choices=["ollama", "openrouter"],
                       help="worker backend (default ollama, or $ORC_PROVIDER)")
        p.add_argument("-m", "--model", default=None,
                       help=f"model id (default {DEFAULT_OLLAMA_MODEL} local / "
                            f"{DEFAULT_OPENROUTER_MODEL} hosted, or $ORC_MODEL)")
        p.add_argument("--ctx", type=int, default=None,
                       help=f"ollama context window in tokens (default {DEFAULT_CTX}, or $ORC_CTX). "
                            "Changing it forces a model reload.")
        p.add_argument("--think", action="store_true",
                       help="let a thinking-capable local model reason first (slower)")
        p.add_argument("--no-stream", action="store_true",
                       help="wait for the whole ollama reply instead of streaming progress")
        p.add_argument("--keep-alive", default=DEFAULT_KEEP_ALIVE,
                       help=f"how long ollama keeps the model resident (default {DEFAULT_KEEP_ALIVE})")
        p.add_argument("--temperature", type=float, default=0.1)
        p.add_argument("--max-tokens", type=int, default=None,
                       help=f"cap on reply tokens (default {DEFAULT_MAX_TOKENS_LOCAL} local / "
                            f"{DEFAULT_MAX_TOKENS_REMOTE} hosted)")

    p = sub.add_parser("check", help="verify the backend and show the active model")
    shared(p, with_task=False)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("models", help="list available models")
    p.add_argument("--grep", default=None, help="substring filter on id/name")
    p.add_argument("--limit", type=int, default=40)
    shared(p, with_task=False)
    p.set_defaults(fn=cmd_models)

    p = sub.add_parser("ask", help="ask a question about the code (never writes)")
    shared(p)
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("code", help="have the worker write code; dry-run unless --apply")
    shared(p)
    p.add_argument("-w", "--write", action="append", default=[], metavar="PATH",
                   help="path the worker may write (repeatable); anything else is skipped")
    p.add_argument("--apply", action="store_true", help="actually write the files (default: diff only)")
    p.set_defaults(fn=cmd_code)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
