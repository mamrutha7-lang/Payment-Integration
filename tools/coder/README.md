# orc — the coding worker

A dev tool, not part of the REEP app. It imports nothing from `apps/`, and the app
imports nothing from it. Its only job is to hand a scoped coding task to a worker
model and bring the result back as a reviewable diff.

The division of labour: **Claude Code orchestrates** — reads the repo, decides what
changes, picks the exact context files, writes the brief, reviews the worker's diff,
applies it, and runs the tests. **The worker model writes the code** for the one file
it was pointed at. It never sees the repo, never runs a command, and never writes a
byte on its own.

## Two backends, and the default is local

| | |
|---|---|
| `ollama` **(default)** | a model on this machine via `http://localhost:11434`. No key, no egress, no cost. Slower, and the context window is a real constraint. |
| `openrouter` | a hosted model. Needs a key. Large windows, fast, costs money. |

Pick per call with `--provider`, or for a whole shell with `ORC_PROVIDER`.

```
./apps/api-py/.venv/Scripts/python.exe tools/coder/orc.py check
./apps/api-py/.venv/Scripts/python.exe tools/coder/orc.py check --provider openrouter
```

Run it with the API venv's interpreter — it needs `httpx`, nothing else.

### Local setup

Nothing to configure. `ollama serve` must be running and the model pulled:

```
ollama pull qwen3:14b        # the current default
orc.py models                # what is installed, with window and capabilities
```

`OLLAMA_HOST` overrides the address if the daemon listens elsewhere.

### Hosted setup

```
tools/coder/.env      OPENROUTER_API_KEY=sk-or-v1-...
```

That path is covered by the repo's `**/.env` ignore rule. The environment variable
wins over the file. Get a key at https://openrouter.ai/keys.

## Commands

| | |
|---|---|
| `check` | verify the backend, show the model, its window, and the byte budget that follows from it |
| `models` | local: installed models with size, window, capabilities. hosted: cheapest first, with price |
| `ask <question> -c FILE...` | ask about the code; never writes |
| `code <task> -c FILE... -w FILE...` | have the worker write code |

`code` is **dry-run by default** — it prints a unified diff and stops. `--apply`
writes. `-w/--write` is an allowlist: a file block for any other path is skipped with
a warning, so a worker that decides to "also fix" something cannot.

Task text can be a string, `-` for stdin, or a path to a `.md`/`.txt` brief — briefs
are better for anything non-trivial, and they are reviewable before they are sent.

## The context window is the thing that will bite you

Ollama does **not** error when a prompt overflows `num_ctx`. It silently drops the
front of it, and the worker then writes confident code against a file it never saw.
That failure is invisible in the output and expensive in review, so orc computes a
byte budget from the model's real window and **refuses** to send an oversized prompt
rather than letting it be truncated:

```
orc: context is 65,933 bytes at apps/api-py/app/interview_audio.py,
     over the 27,552-byte budget.
```

The budget is `(--ctx − --max-tokens − framing) × 3 bytes/token`. At the defaults —
`--ctx 16384`, `--max-tokens 6000` — that is about **27 KB of context**, roughly one
600-line file in and one out. Send more than that and you must either split the task
or raise `--ctx`.

**Do not raise `--ctx` blindly.** The window's KV cache lives in VRAM alongside the
model. A 9 GB model on a 12 GB card has room for roughly 16k tokens of cache; ask for
32k and the whole thing spills to system RAM and generation drops by an order of
magnitude — `check` will still say `100% GPU` while it happens. Changing `--ctx` also
forces Ollama to unload and reload the model, about 20 s for a 14B, which is why the
default is one fixed value rather than something sized per task.

`--keep-alive` (default `30m`) keeps the model resident so a run of edits pays that
load once.

## Speed, and when to reach for the hosted backend

Measured on this machine, same model and same prompt, twice:

| GPU state | qwen3:14b |
|---|---|
| to itself | **25.9 tok/s**, 100% on the card |
| ~3 GB held by another app | **1.6 tok/s** — a 16× collapse |

`ollama ps` reported `100% GPU` in **both** cases. It is describing intent, not
residency: the model is nominally on the card while its weights thrash against
system RAM. So `100% GPU` is not evidence that anything is fine, and unexplained
slowness is almost always a second process holding VRAM. `nvidia-smi
--query-gpu=memory.used,memory.free --format=csv` is the check that actually
answers it — anything under ~1 GB free means the next model load will spill.

A 14B needs about 10 GB resident at `--ctx 16384`, so on a 12 GB card the margin
is well under a gigabyte. `gemma3:12b` (7.9 GB, ~24 tok/s) is the fallback that
still fits when something else wants the GPU; it has no tools or thinking, but a
weaker model answering in seconds beats a better one taking an hour.

So: local for small, well-scoped edits and for anything you would rather not send off
the machine. Hosted (`--provider openrouter`) when the context genuinely does not fit,
or when the GPU is busy.

`--think` lets a thinking-capable model (qwen3 has it, gemma3 does not) reason before
answering. It is better on hard logic and much slower; it is off by default. orc only
sends the flag to models that declare the capability, so it is safe to leave set.

## What the worker sees, and does not

Only the files passed with `-c`. There is no repo scan and no automatic context: if a
symbol's definition matters, it must be in a `-c` file, or the worker will invent one.

Every call saves its exact prompt and the raw reply to `tools/coder/.orc/`
(`last-request.md`, `last-response.md`), gitignored. When a diff looks wrong, read the
request first — it is nearly always an under-specified brief rather than a bad model.

## The rule this tool must not break

Rule 1 in `AGENTS.md` — student data must not leave the machine unbidden — is about
the *app's* runtime paths and the egress gate in `app/ai/llm.py`. It applies here by
the same logic and with no gate to enforce it: **never pass a file containing real
student records** (a database dump, a `var/` upload, a `.env`, a log with USNs) as
`-c` context. Source code and schema are fine; rows are not.

The local backend is the one case where that risk goes away on its own — an Ollama
model on `localhost` is the same loopback the egress gate in `app/ai/llm.py` allows
unconditionally. That is a reason to prefer it, not a reason to get careless: switch
back to `--provider openrouter` with the same `-c` list and the data leaves.
