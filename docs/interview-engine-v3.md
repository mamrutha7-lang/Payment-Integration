# Interview Engine v3 — the relay owns the turn

> **THE ENGINE THIS SPECIFIES WAS DELETED IN 2026-09. THE DOCUMENT IS KEPT ON
> PURPOSE — read it for the ARGUMENT, not as a description of running code.**
>
> Everything below is written against the `openai` engine: `app/interview_relay.py`
> (3 968 lines), `wss://api.openai.com/v1/realtime`, `OPENAI_API_KEY`,
> `session.update`, `turn_detection.create_response: false` and `response.create`.
> That module and its test module are gone, along with the realtime model/voice
> settings, the server-VAD tuning and the per-row OpenAI `voice` column.
>
> **What outlived the mechanism is the reasoning**, and the current engine
> inherits it: the phase machine (`opening → probing → deep_dive → wrap_up`), the
> deterministic word-count gate, "one open question at a time" as a property of
> the call graph rather than a sentence in the persona, and the scorecard as a
> separate strict-JSON artefact that is never spoken. **Read this document before
> changing the arc.** AGENTS.md says so in as many words.
>
> The live engine is **Amazon Nova 2 Sonic** on Bedrock, in-process, signed with
> SigV4 and holding no API key (`app/interview_nova.py`, with the shared contract
> in `app/interview_core.py`). Where Nova differs it is forced by that API and
> the difference is written down in AGENTS.md: no `create_response: false` and no
> `session.update`, so steering arrives as a cross-modal `[INTERVIEW CONTROL]`
> note; the scorecard is a `submit_scorecard` **tool call**; Bedrock closes the
> stream at 8 minutes, so the wrap-up is forced 90 s early.
>
> **§8.4 is overridden.** It argues against capturing audio. Audio capture was
> built, behind three independent switches, none of them on by default — read
> §8.4 anyway, because it is why every one of those guards exists.
>

The realtime mock interviewer promises one thing: **ask one question, wait for the
whole answer, judge it, ask the next, and end with a verdict and a written record.**
Today it cannot keep that promise, and no amount of prompt wording will make it —
because the decision it depends on is taken in someone else's process.

This document is the implementable spec for fixing that. It assumes
`docs/interview-assistant.md` (what the relay is) and `AGENTS.md` (the two rules).
Read those first; nothing here overrides them.

The whole change turns on one boolean, `turn_detection.create_response`, at
`apps/api-py/app/interview_relay.py:715`. Everything else in this document is a
consequence of moving that decision into this process — and most of it is the
work of making the new failure modes visible instead of silent.

---

## 1. What was wrong, as the wire sequence that produced it

Three emitters: **B** = browser, **R** = this relay, **U** = `api.openai.com`.

### The handshake, once (`_handshake`, `interview_relay.py:847`)

| # | Dir | Event | Anchor |
|---|---|---|---|
| H1 | R→U | WS connect, `Authorization: Bearer` | `_upstream_connector` |
| H2 | U→R | `session.created` | :847 |
| H3 | R→U | `session.update` — `server_vad`, **`create_response: true`**, `interrupt_response: false` | `_session_update_payload` :693, the flag :715 |
| H4 | U→R | `session.updated` → `_verify_turn_detection` :923 (checks `type == "server_vad"` **and nothing else**) | :923 |
| H5 | R→U | `response.create {"response":{"conversation":"auto"}}` — the opening question | :880-891 |
| H6 | R→B | `reep.ready` | :893 |

### One student turn, N

| # | Dir | Event | What the relay does |
|---|---|---|---|
| 1 | U→R | `response.created` (`resp_A`) | `_active_response_id = resp_A`; forwarded (:1126) |
| 2 | U→R | `response.output_audio.delta` ×N | `_forward_model_audio` :1225 → binary PCM to B |
| 3 | U→R | `response.output_audio_transcript.delta` ×N | accumulated into `_assistant_text[resp_A]` (:1141) |
| 4 | U→R | `response.done` (`resp_A`) | `_on_response_done` :1270 → `_emit_turn("assistant", …, "a:resp_A")` |
| 5 | B→R | PCM at ~25 frames/s | `_forward_client_audio` → `input_audio_buffer.append` (:1002) |
| 6 | U→R | `input_audio_buffer.speech_started` | `_on_speech_started` :1240 — flush B, then `response.cancel` |
| 7 | U→R | `input_audio_buffer.speech_stopped` | forwarded verbatim (:1120). B enters `thinking` |
| 8 | U→R | `input_audio_buffer.committed {item_id}` | **not in `_HANDLED_UPSTREAM` :150** → logged once, dropped (:1219) |
| 9 | U→R | **`response.created` (`resp_B`) — the NEXT question** | fires *here*, at VAD commit, because `create_response: true` |
| 10 | U→R | audio deltas for `resp_B` | the interviewer is already asking |
| 11 | U→R | `conversation.item.input_audio_transcription.completed {item_id, transcript}` | :1162 — `_emit_turn("user", …)`, then `student_answered()`, then `_push_phase()` :783 |

**Steps 9 and 11 come from two independent subsystems inside U, and there is no
happens-before edge between them.** On a real session the ASR result lands
hundreds of milliseconds to several seconds after the commit, so the next
question is typically half-spoken before the relay learns what the student said.

### The four things that follow, none of them fixable in a prompt

1. **The phase directive is structurally one response late — audit M7.**
   `_push_phase` sends an instructions-only `session.update` at step 11.
   `resp_B` was created at step 9. A `session.update` steers responses created
   *after* U processes it; there is no event that amends a response already
   generating. So the directive computed for answer N steers the response to
   answer N+1. The tail of that bug is the product requirement failing outright:
   after answer 5 the machine reaches `WRAP_UP` (`interview_matrix.py`,
   `_WRAP_UP_AFTER_ANSWERS = 5`), but the in-flight response, composed under the
   deep-dive directive, asks a *sixth question*. The verdict needs a sixth
   answer — and if `interview_max_seconds` (900) lands first, **there is no
   verdict at all.**

2. **"One question at a time" is unenforceable.** `_INTERVIEWER_PERSONA`
   (:98) already says *"Ask one clear question at a time."* The persona is not
   the failure. Instructions are the **content** of one response; the flag
   decides **how many responses exist and when**. Any VAD-committed segment —
   a cough, a "sorry, one more thing", speaker-borne echo — creates one.

3. **The relay cannot consult its own state at the moment that matters.**
   Whether this is answer #3 or #5, whether it was substantive, whether it
   answered a question or interrupted one: all of that is state in *this*
   process, and at the instant the response is created the relay has not yet
   seen the transcript. Two processes, one decision; only one of them can own it.

4. **A transcript arriving after `response.done` for `resp_B` is written out of
   causal order.** `Message.created_at` records arrival, not utterance.

There is no upstream knob that says "create the response, but only once
transcription lands". `create_response` is a boolean, and the only correct value
for this product is `false`.

### The safety property we are giving up, named before anything else

Today a transcriber outage degrades the record and freezes the phase machine
(audit M5) but **does not stop the conversation** — U keeps creating responses,
so the interview continues. Under `create_response: false` a transcription that
never completes is an interview that never continues.

**That is why §2's deadlines are not optional. A `create_response: false` build
without them is strictly worse than today, and the flag flip must never ship in
a commit that can be merged apart from them.**

---

## 2. The v3 turn protocol

One literal changes at `:715`. Then the relay owns everything between "the
student stopped talking" and "the interviewer speaks".

### 2.1 What upstream still does

With `server_vad` and `create_response: false`, U **still**: runs VAD; emits
`speech_started` / `speech_stopped`; **commits the input buffer** and emits
`input_audio_buffer.committed {item_id}`; creates the user
`conversation.item.created`; runs input transcription and emits `.delta` /
`.completed` / `.failed`. It **stops** creating responses on its own.

> **Stated uncertainty.** `create_response` names response creation only, so the
> commit should be unaffected — but this has not been verified against a live
> socket on both the beta and GA surfaces, and neither has the exact name
> `input_audio_buffer.committed`. **The design therefore never depends on it:**
> the answer deadline is armed from `speech_stopped`, which is already handled
> and already in `_HANDLED_UPSTREAM`. `committed` only *refines* the pending
> entry with its `item_id`. If it never arrives, the interview still runs; the
> turn is recorded under a synthetic id and the record says so.

### 2.2 Handshake, after

| # | Dir | Event | Change |
|---|---|---|---|
| H1–H2 | | connect, `session.created` | unchanged |
| H3 | R→U | `session.update` with **`create_response: false`** | one literal at :715; rewrite the comment above it (§2.7) |
| H4 | U→R | `session.updated` → `_verify_turn_detection` | **extended**: if the echo contains `create_response` and it is not `false`, close **4002**. If the field is absent, log the existing "unverified" warning and fall through to the runtime guard in D2 |
| H5 | R→U | `response.create` — the opening question | **unchanged, and now more necessary.** Under `create_response: false` there is no VAD path that could ever produce a first response; removing it is a guaranteed dead interview. `instructions` stays omitted — supplying it here REPLACES the session persona, and the OPENING directive already reached the model in H3 |
| | R | arm `_create_deadline`; `_expecting_response = True` | new |
| H6 | R→B | `reep.ready` | unchanged. **Do not** wait for `response.created` first: that adds a round trip to the one moment the student is already watching a spinner, and the deadline covers the failure |

Rewrite the comment at :880-891. It no longer reads "exactly once per session";
it reads **"the first of the relay's own `response.create` calls; every later one
comes from `_advance_turn`."** That single sentence is what tells the next editor
that response creation is now this process's responsibility with one entry point.

### 2.3 The loop

```
 (i)    U→R  response.created(R_k)
              _expecting_response = False; disarm _create_deadline
              _active_response_id = R_k                                    :1126
 (ii)   U→R  audio + transcript deltas                            unchanged :1225/:1141
 (iii)  U→R  response.done(R_k, status)                     _on_response_done :1270
              R_k == _report_response_id  -> settle the report, DO NOT _emit_turn
              else  -> _emit_turn("assistant", spoken, f"a:{R_k}")
                       _question_open = (status == "completed")
                       status "incomplete" -> treat as asked, log
                       status "cancelled"  -> NOT asked; _partial_question = True
                       status "failed"     -> NOT asked; retry the create once
              if _deferred is not None: fire it now (DF4)
 (iv)   student speaks
 (v)    U→R  input_audio_buffer.speech_started       _on_speech_started :1240
              unchanged EXCEPT: never cancel when _active_response_id ==
              _report_response_id (§5.5)
 (vi)   U→R  input_audio_buffer.speech_stopped
              forwarded as today  +  ARM the answer deadline:
                _pending[<synthetic key>] = _Pending(deadline = now + T_ASR)
 (vii)  U→R  input_audio_buffer.committed {item_id}                  NEW BRANCH
              re-key the newest unresolved entry to item_id; re-arm its deadline
 (viii) exactly ONE of three resolutions per entry, first one wins:
          a. conversation.item.input_audio_transcription.completed  -> transcript, "ok"
          b. conversation.item.input_audio_transcription.failed     -> "", "failed"   NEW (M5)
          c. the entry's deadline expires                           -> "", "timeout"  NEW
 (ix)   R:   _drain_pending() pops from the HEAD while the head is resolved
 (x)    R:   per popped entry -> _emit_turn("user", transcript, f"u:{item_id}")
 (xi)   R:   classify_answer(joined transcripts)  -> deterministic, no model call (§4)
 (xii)  R:   accepted AND _question_open:
                machine.student_answered() -> if True: _push_phase()   :783
                                              (session.update + reep.phase)
             not _question_open, or not accepted: no tick
 (xiii) R→U  ONE response.create from _advance_turn — never one per item
              _pending_create_id = event_id; _expecting_response = True
              arm _create_deadline; _question_open = False
 (xiv)  -> (i)
```

**`_advance_turn` is the single call site for `response.create` after the
handshake.** The "one open question at a time" invariant is enforced by the call
graph, not by a flag anyone can forget to check. If a second call site for
`response.create` ever appears the invariant is gone and no test will notice —
say exactly that in the comment above it.

### 2.4 Wrap-up and the report

> **AMENDMENT, 2026-08-21 — wrap_up is now a TWO-BEAT close.** The sequence
> below jumps from the tick into WRAP_UP straight to the spoken verdict. The
> shipped engine inserts one turn between them: the create at (xiii) carries
> the `invite_questions` turn directive — *"any questions for you about the
> role or the company?"* — and the student's reply (ANY reply: it bypasses
> `classify_answer` entirely, because "no, I'm good" is filler words to the
> word gate and must not earn a clarify loop on the final turn) is what
> produces the verdict R_v. `_request_report` is gated on `_verdict_requested`,
> not on the phase, so the invite's `response.done` does NOT fire (xvii); only
> the verdict's does. `_force_wrap_up` skips the beat — the clock path has no
> time for it — and, if the reserve deadline lands while the student is
> thinking of a question to ask, ends the beat and goes straight to the
> verdict. The local engine (`interview_local.py`) mirrors the same two beats
> in its own turn loop.
>
> The same amendment adds two smaller things, overriding §2.1/§2.2 where they
> say the opposite by omission. **Voices are per-specialization**: each matrix
> row carries a `voice` (HR `coral`, DM `marin`, BA `cedar`, FA `ash`) sent in
> the single startup `session.update`; the generic track keeps
> `OPENAI_REALTIME_VOICE`, now validated against the known set with a logged
> fallback to `alloy`, because upstream answers an unknown voice with a silent
> default. And **`input_audio_transcription.delta` is now allowlisted** and
> forwarded to the browser as `reep.transcript.delta`, so the "You" line
> revises live; deltas are never persisted and never touch the arc —
> `.completed` remains the only point a student turn is recorded or judged.

```
 (xv)   machine.phase is WRAP_UP -> the create at (xiii) is the spoken verdict R_v
 (xvi)  U→R  response.done(R_v)  -> _emit_turn assistant  (the verdict IS the transcript)
 (xvii) R→U  response.create — text-only, strict-JSON scorecard (§5)
              _report_response_id learned at its response.created
              arm the report deadline (interview_report_timeout_ms)
 (xviii)U→R  response.done(R_report) -> parse -> AWAIT the interview_evaluations write
 (xix)  R→B  reep.report {available: true|false, …}
 (xx)   R:   raise _SessionEnded(_CLOSE_OK, "Interview complete")
```

### 2.5 Where the deadlines live — and why not in the watchdog

The deadline handler must **mutate turn state** (pop `_pending`, tick the
machine) and **send downstream** (`_push_phase` sends `reep.phase`). Running it
from `_watchdog` :1396 would make the watchdog a second mutator of turn state and
a second downstream sender, breaking the invariant documented at :656-660
(*"exactly one task ever sends downstream"*) — and it would turn §2.6's
pop-based idempotency guard from a proof into a hope, because two tasks could
then reach the same entry.

**Restructure `_pump_upstream_to_client` :1093** from

```python
async for raw in upstream:
    await self._handle_upstream_event(json.loads(raw))
```

to an explicit loop that computes
`deadline = min(pending entry deadlines, _create_deadline, report deadline)` and
wraps one `await upstream.recv()` in `asyncio.timeout_at(deadline)`; on
`TimeoutError`, `await self._on_deadline()` **on the same task**, then continue.
When nothing is armed there is no timeout at all, so the idle path costs
precisely nothing — the same reasoning `_WATCHDOG_INTERVAL_S` :297 gives for
polling monotonic timestamps instead of arming a timer per audio frame.

Cost: an explicit `except ConnectionClosed` to reproduce the clean-close raise
at :1101, and the docstring at :1095-1097 (*"`async for` is the single permitted
consumer"*) must be rewritten rather than deleted — the constraint it names
(concurrent `recv()` on one `ClientConnection` raises `RuntimeError`) is still
true and is exactly why this loop must not become two.

`_watchdog` keeps its two caps and gains exactly one new job, S8's report
reserve, and it does that by **signalling** (a flag the pump reads on its next
iteration, the same shape as `request_stop`), never by sending.

### 2.6 Every way this stalls, deadlocks, double-fires or loses a turn

#### Stalls

| # | Failure | Mitigation |
|---|---|---|
| **S1** | **Transcription never completes and never fails.** The interview waits forever. **4008 does not save us**: the browser's echo-gate keepalive (`interview.service.ts:1899`, `ECHO_GATE_KEEPALIVE_MS = 10_000`) feeds zeroed frames, so `_last_audio_at` (:1002) stays fresh and the idle watchdog never fires. Only the 900 s cap ends it — with no verdict. | The **answer deadline**, `interview_transcription_timeout_ms` (8000, already in config). Armed at `speech_stopped`, refined at `committed`, disarmed by whichever of `.completed`/`.failed` fires. On expiry: resolve with `transcript=""`, `transcription_status='timeout'`, and **create the response anyway**. Add one comment at :1420 saying "idle" means *no frames*, not *no speech*, so the next reader does not assume the watchdog covers this. |
| **S2** | **Our `response.create` is silently dropped** — no `response.created`, no `error`. | `_create_deadline` (`interview_response_create_timeout_ms`, 10000 — new, §9). Armed on send, disarmed on `response.created`. On expiry: one retry with a fresh `event_id`; second expiry → `_SessionEnded(_CLOSE_TURN_STALLED)` = **4011**. |
| **S3** | **Upstream refuses our create with `error`.** Most likely `conversation_already_has_active_response`. The `error` branch (:1198) is cosmetic today — it forwards `code`/`param` and continues. Under v3 an error on *our* create is a stall, not a banner. | Extend :1198 to match `err["event_id"] == _pending_create_id`. `conversation_already_has_active_response` → **do not retry, do not cancel**; disarm and wait for the in-flight `response.done`, which fires the deferred create. Any other code echoing our id → retry once, then 4011. **Cancelling here would kill the question currently being asked** — the worst outcome available. |
| **S4** | The phase `session.update` is rejected just before the create. | **Do not gate the create on the `session.updated` echo.** Waiting adds a round trip at every phase boundary and introduces a *new* stall if the echo never arrives. Fire the update, then create; log if an `error` echoes the phase update's event id. More correctness here buys a worse failure mode. |
| **S5** | **The generic (no `?specialization=`) interview goes permanently silent.** The phase tick is gated on `specialization is not None` (:1176). Gating the *create* on the same condition ships a v3 that is catastrophically broken for that path. | **The create is unconditional; only the phase tick stays gated.** Say so in the comment — it is the single easiest way to get this wrong. |
| **S6** | The WRAP_UP verdict is cancelled by barge-in and never re-created ⇒ no verdict, no scorecard. | **One** bounded re-create with a per-response "finish the verdict" override, then proceed to the scorecard regardless. |
| **S7** | The scorecard response never completes. | `interview_report_timeout_ms` (20000, already in config) on the same deadline mechanism → `reep.report {available:false, reason:"timeout"}`, persist `report_status='timeout'`, close **1000**. Not an error code: the interview completed. |
| **S8** | **The hard cap lands during the wrap-up or the scorecard**, so a five-answer interview ends with nothing. | In `_watchdog`: when `now - _started_at >= session_cap - _REPORT_RESERVE_S` (a module constant, `interview_report_timeout_ms / 1000 + 25`) and the machine is not yet WRAP_UP, **set a flag** the pump reads to force the transition and create the verdict. Turns M7's tail ("no verdict at all") into "a slightly early verdict". This is the *one* place the watchdog influences turn state, and it must do it by signalling only. Note the risk **shrinks** under v3: WRAP_UP is reached a full turn earlier, because v3 no longer burns a response on question six. |

#### Deadlocks

| # | Failure | Mitigation |
|---|---|---|
| **D1** | **Clarification loop.** Two-word answer → re-ask → two-word answer → … until the hard cap, with no verdict. | `_clarifications`, reset on each accepted answer, capped by `interview_max_clarifications_per_question` (1, already in config). At the cap: accept whatever arrived, tick, move on. **A student who genuinely cannot answer must not be trapped.** |
| **D2** | **`create_response: false` did not take** (an old API date, a surface that ignores it) and both parties create a response. Two questions per turn: the exact bug we set out to kill, doubled. **This is the one upstream behaviour that cannot be verified from here.** | Two layers. (1) `_verify_turn_detection` :923 fails the handshake **4002** if the echo contains `create_response` and it is not `false` — refusing beats running a known-double interview. (2) If the field is *absent* from the echo (the existing warning path at :928-934), a **runtime guard**: `response.created` arriving while `_expecting_response is False` is a server-created response → log loudly, set `_last_error = "vad:server_created_response"`, and **skip our own create for that turn**, degrading to today's behaviour rather than doubling. Realtime does not echo our `event_id` on `response.created`, so the correlation is positional and therefore a heuristic — good enough to detect and log, which is the point. |
| **D3** | **`_question_open` never becomes True**: a noisy room where VAD fires constantly, so every response ends `cancelled`. Fifteen minutes of interrupted questions and no answers. | Count consecutive responses cancelled before any audio was emitted; after 3, **stop cancelling on barge-in** for the rest of the session and let the model finish. `_last_error = "bargein:thrashing"`. `gateCloses` in the summary line (:1611) is the instrument that diagnoses it. |
| **D4** | **Head-of-queue block.** Entry A hangs; B and C queue behind it and blow their own windows. | **Arm each entry's deadline at its own arm-time, not at head-of-queue time.** One line, easy to get wrong. |

#### Double-fire

| # | Failure | Mitigation |
|---|---|---|
| **DF1** | `.completed` and the deadline both fire for one entry. | `_pending.pop(key, None)` is the **single point of consumption**; the loser finds nothing and returns. Genuinely atomic because §2.5 keeps both paths on one task and `_handle_upstream_event` is awaited to completion before the next event is read. |
| **DF2** | `.completed` delivered twice, or `.failed` after `.completed`. | Same pop. Plus the database: `f"u:{item_id}"` → `append_message` dedup + `UniqueConstraint(conversation_id, provider_turn_id)` with `IntegrityError` swallowed as the no-op it is. **The pop protects the state machine; the unique index protects the record.** Two layers, deliberately. |
| **DF3** | **Two `response.create` for one turn** — the drain pops two resolved entries and creates per entry. | `_drain_pending()` returns a **list**; `_advance_turn(entries)` runs **once**, transcripts joined with a space. `_emit_turn` is still called **per entry**, so the dedup keys and the row-per-utterance record are unchanged. This is also the fix for "the student paused mid-answer, VAD split it in two, and the interviewer asked two questions" — a real complaint about the current build. |
| **DF4** | **The student speaks again while a response is live**, so a second create collides → `conversation_already_has_active_response` → a warning banner mid-interview. | `_active_response_id is not None` ⇒ **do not create; store `_deferred = <turn record>`** and fire it from `_on_response_done` :1270. Store the *kind* (`next` / `clarify` / `unheard` / `resume` / `verdict`), not a bare bool, so the deferred create carries the right instructions. Fire it **unconditionally when `_deferred` is set**, whatever the status — otherwise a `cancelled` response on a path where `_active_response_id` was already `None` loses the turn. Last-answer-wins on a barge-in is correct: the student's newer words *are* the answer. |
| **DF5** | `_emit_turn("assistant")` fires for the scorecard, so raw JSON lands in the student's chat and in `GET /api/agent/history`. | `_on_response_done` :1270 branches on `response_id == _report_response_id` and returns **before** `_emit_turn`. Four lines, and easy to miss. |
| **DF6** | The evaluation row is written twice (the report `response.done` arrives after the timeout already wrote `report_status='timeout'`). | `_report_settled` checked-and-set at the single settlement point, **plus** a UNIQUE constraint on `interview_evaluations.interview_session_id` with `IntegrityError` swallowed — the same shape as the turn writer. |

#### Lost turns

| # | Failure | Mitigation |
|---|---|---|
| **L1** | A committed item whose transcription lands after the socket closed (the student pressed End). `_drain_writes` :1371 drains *writes*, not pending transcriptions, and cannot wait on upstream. | Accept the loss, but **say so**: add `pending=%d` to the end-of-interview line (:1611). Today the same loss is invisible. |
| **L2** | A cancelled question's partial text. | Already correct at :1301-1304, and the comment there is right — do not regress it. Add `is_partial` to the `interview_turns` row so the record distinguishes "asked" from "cut off". |
| **L3** | The evaluation write is the last thing to happen and therefore the most likely to be cancelled by `_TURN_WRITE_DRAIN_S = 2.0` (:1371). | **Await the evaluation write; do not fire-and-forget it.** It is one row, it happens once, and the interview is already over — the reason fire-and-forget exists (*"the student is mid-sentence and cannot be helped by an exception"*, :1311) does not apply. Bound it ~3 s, and on failure still send `reep.report` so the student sees the scorecard even if Postgres did not take it. **A deliberate, documented divergence from the house rule; say so at the call site.** |
| **L4** | **A timed-out or failed transcript is `""`, and `_emit_turn` returns early on `not text.strip()` (:1330).** So the very turns v3 adds a status column for would leave **no row at all**. | The blank-text guard applies to the `messages` insert **only**. The `interview_turns` row is written regardless — `content = ''` is legal there, `transcription_status` says why, and that row is the entire reason the table exists. **This is the trap in this change most likely to ship unnoticed.** |
| **L5** | **`append_message` now raises `ConversationGone`** (Track A's M6 fix, already on the working tree in `app/conversations.py`), and `_make_turn_writer` in `routers/interview.py` has **not** been updated to its call-site contract. | Adopt the contract: let it propagate to `_run_turn_write`'s `except Exception`, log it with the connection id, and **end the session** — `request_stop(_CLOSE_OK, "Conversation cleared")`. Safe from there: `_run_turn_write` catches on the event loop, not in the worker thread. This is also why `interview_turns` is keyed on `interview_session_id` and never on `conversation_id`: the interview record survives a conversation that has stopped accepting turns. |

### 2.7 The comments that become false, and must be rewritten in the same commit

A stale *why* in this codebase is worse than no comment.

- **:710-714** — *"The server commits the buffer AND creates the response itself.
  This is why the relay never sends commit or response.create per turn — doing
  so double-commits…"* Rewrite it around the new failure mode
  (`conversation_already_has_active_response` from creating while one is open)
  and **preserve the distinction that keeps this a small diff: we still never
  send a manual `input_audio_buffer.commit`.** Server VAD still commits. Only
  response creation moves.
- **:1120-1122** — *"Forward only. The server commits the buffer and creates the
  response itself; a manual commit here races the automatic one."* This is now
  where the answer deadline is armed.
- **:880-891** — the opening `response.create`, per §2.2.
- **:1095-1097** — the `async for` docstring, per §2.5.

### 2.8 Latency — the honest number, and measure it before you flip

Today the next response is created at VAD commit, i.e.
`interview_vad_silence_duration_ms` (700) after the student's last phoneme, plus
model time-to-first-byte. Under v3 the ASR round trip is added in series:

```
today   ~= 700 ms (VAD tail) + ~400 ms (TTFB)              ~= 1.1 s
v3      ~= 700 ms (VAD tail) + T_asr + ~400 ms (TTFB)      ~= 1.1 s + T_asr
```

**There is no measured `T_asr` for this deployment and this document will not
invent one.** Hosted Whisper-class ASR is roughly linear in segment duration plus
a fixed queueing cost, which puts a five-second answer somewhere under a second
and a twenty-second answer at a few — worst on the longest answers, which are
exactly the answers a good candidate gives.

So **the first commit in §9 is telemetry with the flag still `true`**: stamp the
monotonic clock at `speech_stopped`, record `t_completed − t_stopped` at
`.completed`, and add `asrP50=… asrMax=…` to the end-of-interview line (:1611).
Five lines, zero behaviour change. One cohort of real interviews then *tells* you
`T_asr` and validates `interview_transcription_timeout_ms = 8000` against reality
before that number becomes load-bearing.

Two mitigations, in the order they should be considered:

- **Cover the silence honestly (client-side, no protocol change).** The front
  end **already** enters `thinking` on `input_audio_buffer.speech_stopped`
  (`interview.service.ts`), so the UI state is already correct for a longer
  wait. What is missing is *duration*: three seconds of `thinking` behind a
  static orb reads as "it broke". A progress affordance after ~1.2 s in
  `thinking` costs the server nothing. Optionally forward
  `conversation.item.input_audio_transcription.delta` (one allowlist entry, one
  forward) so the student's own words appear as they are recognised — dead air
  becomes visible progress. *Nothing depends on that delta arriving.*
- **Cut the VAD tail, not the ASR — later, with the telemetry in hand.**
  `interview_vad_silence_duration_ms = 700` was chosen because "the persona
  promises not to interrupt" (see the essay above it in `config.py`). Under v3
  *the relay* decides whether to answer, so a premature commit is no longer a
  premature interruption — just an early transcript the relay can merge with the
  next one. 700 → 500 becomes safe. **This is a follow-on tuning change, not
  part of the spine.**

Net: v3 adds real silence proportional to answer length, roughly 200 ms of it
reclaimable later, the rest covered perceptually rather than eliminated. Worth
paying, because today's alternative is an interview that structurally cannot
deliver the verdict it promises.

---

## 3. The state set

The relay's real state is a handful of mostly-orthogonal variables, several
already present. An enumerated 12-state machine was considered and **rejected**:
it is a product of orthogonal booleans, it would have to keep every one of these
variables anyway (`_active_response_id` is not optional), and it would therefore
be *12 states **plus** the existing machinery* — a parallel mechanism, which is
exactly what this design is trying not to build.

Everything a 12-state design would name is already derivable and must not be
stored: `connecting`/`handshaking`/`ready` is the `_handshake` coroutine's own
program counter; `listening`/`thinking`/`speaking` already exists in the browser
(`interview.service.ts`), and duplicating it server-side buys nothing and creates
a second thing to keep in sync; `awaiting_transcript` is `_pending`;
`response_in_flight` is `_active_response_id`; `clarifying` is a counter;
`ended`/`errored` is the `(code, reason)` tuple `run()` already returns.

| Variable | Values | Status | The invariant it protects |
|---|---|---|---|
| `InterviewPhase` (`interview_matrix.py`) | 5 | exists, unit-tested | The model's instructions match the stage of the arc. **Do not extend it.** |
| `_active_response_id: str \| None` (:565) | nullable id | exists | (a) audio from a cancelled response never reaches a flushed browser (`_forward_model_audio` :1227); (b) **new** — at most one response is in flight, so we never collide with our own create. |
| `_question_open: bool` | 2 | **new** | **Only an answer to a question that was actually ASKED advances the arc.** Set from `response.done.status == "completed"`; cleared at `response.create`. |
| `_pending: dict[str, _Pending]`, insertion-ordered, capped at 8 | 0..8 | **new** | Exactly one `response.create` per drainable batch, in commit order, and no committed segment waits forever. `_Pending` is a small mutable record: `(item_id, deadline_at, transcript, status)`. |
| `_deferred: _DeferredTurn \| None` | nullable record | **new** | A create that would collide with a live response is queued, not dropped. |
| `_expecting_response: bool` + `_create_deadline: float \| None` | | **new** | Our create was accepted, or the session ends 4011 rather than hanging. Also the D2 runtime guard. |
| `_clarifications: int` | 0..cap | **new** | The arc terminates. |
| `_report_response_id: str \| None` + `_report_settled: bool` | | **new** | The scorecard is neither spoken, nor persisted as an interviewer turn, nor written twice. |
| `_stop_requested` / `_stop_outcome` (:583) | Event + tuple | exists | The session ends with a real close code, never a bare 1006. |

All new fields go in `__slots__` (:505) and `__init__` (:531).

### Legal transitions

```
_question_open
    False --[response.done(status="completed") on a non-report response]--> True
    True  --[_advance_turn sends response.create]-------------------------> False
    True  --[socket closing]---------------------------------------------> False
  A response ending "cancelled" or "failed" does NOT open a question.
  A response ending "incomplete" DOES (the student heard most of it).

_pending[key]
    (absent) --[speech_stopped]--> armed(synthetic key)
    armed    --[committed]-------> armed(item_id)      re-key, re-arm
    armed    --[.completed]------> resolved(ok)
    armed    --[.failed]---------> resolved(failed)
    armed    --[deadline]--------> resolved(timeout)
    resolved --[_drain_pending pops from the head]--> (absent)
  Resolution is a pop from a single point. A second resolution finds nothing.

_active_response_id
    None --[response.created]--> R      (also: _expecting_response -> False)
    R    --[response.done]-----> None
    R    --[speech_started]----> None   cleared BEFORE the cancel is sent,
                                        which is what arms the drop-filter

_deferred
    None   --[create requested while _active_response_id is not None]--> record
    record --[response.done fires it]---------------------------------> None

phase (interview_matrix.py, unchanged)
    opening --1 accepted answer--> probing --3--> deep_dive --5--> wrap_up
    any --> ended.   wrap_up is sticky: student_answered() returns False there.
```

---

## 4. Answer validation

Deterministic, local, and **never a hot-path model call** — this runs between the
student finishing and the interviewer replying, so a round trip here is latency
on every single turn.

### 4.1 The rule

Lives in `app/interview_matrix.py` (no I/O, unit-testable with no fixtures — the
same property that module's docstring already claims):

```python
def classify_answer(transcript: str) -> str:
    """'accepted' | 'empty' | 'filler' | 'too_short'."""
    words = re.findall(r"[a-z0-9']+", transcript.casefold())
    if not words:
        return "empty"
    floor = settings.interview_min_answer_words        # 0 disables the gate
    if floor <= 0:
        return "accepted"
    if all(w in _FILLER_WORDS for w in words):
        return "filler"
    if len(words) < floor:
        return "too_short"
    return "accepted"
```

`_FILLER_WORDS` is a small frozen set — `um, uh, erm, hmm, mm, mhm, yeah, yes,
no, ok, okay, sure, right, thanks, thank, you, sorry, what, huh, pardon` — and it
is deliberately short. Its job is to catch a cough and an acknowledgement, not to
judge content. `interview_min_answer_words = 4` (already in config) clears "yes",
"I don't know" and a cough transcribed as "uh", while leaving a real short answer
("I led the campus fintech club") intact.

### 4.2 The order of the two checks, which is not interchangeable

**`_question_open` is checked BEFORE the validator, and they answer different
questions.** A three-word answer to a real question needs a clarification; a
three-word interjection over an unfinished question needs *the question
finished*. Conflating them re-introduces M7 from the other direction — the arc
advancing on utterances that answered nothing.

```
if not _question_open:            -> kind = "resume"    (finish/reframe the question)
elif status in {failed, timeout}: -> kind = "unheard"   (ask the student to repeat)
elif classify != "accepted":      -> kind = "clarify"
else:                             -> kind = "next"      (the only kind that ticks)
```

Only `kind == "next"` calls `machine.student_answered()`. Every other kind
creates a response and does not advance the arc.

### 4.3 The clarification path

`kind in {"clarify", "unheard", "resume"}` increments `_clarifications` and
creates the response with a **per-response `instructions` override**. Two rules
on that override, both load-bearing:

1. **Compose it in `app/interview_matrix.py`, not inline in the relay**, so
   instruction composition stays in one module and the base persona keeps
   arriving first and verbatim — the property `build_instructions`' docstring
   promises. The override **replaces** the session persona for that response
   (the hazard the handshake comment at :880 already names), so it must be
   self-contained: `build_instructions(spec, persona, phase)` plus a fixed
   `## This turn` block.
2. **It must NOT quote the student's transcript back.** It is unnecessary — the
   audio is already in the conversation and the model has it — and it preserves
   the property the matrix module header states: *the only thing this app authors
   upstream is fixed strings*. Rule 1 is not violated by the student's own words,
   which are already upstream in that same session; but **the shape of the code
   is the guardrail**, and the moment student text is composed into
   `instructions`, the next editor composes a resume into it.

When `_clarifications` reaches `interview_max_clarifications_per_question` (1),
the relay **accepts whatever it got**, ticks the machine and moves on.
`_clarifications` resets to 0 on every accepted answer.

### 4.4 Does an unheard turn advance the arc?

**Not on its first occurrence; yes once the clarification budget is spent.**

Never ticking means a degraded transcriber produces an interview that runs its
full fifteen minutes and delivers no verdict — a stall by another name. Always
ticking means a cough counts as an answer. The clarification cap is already the
mechanism that resolves both: an unknown transcript spends a clarification, and
when there are none left the turn is accepted-as-unknown, ticks, and carries
`transcription_status='timeout'|'failed'` with `answer_quality` NULL on its
`interview_turns` row. **The arc always terminates within (cap + 1) turns, and
the record is always honest about why.** After two consecutive unheard turns,
also set `_last_error = "transcriber:timeout"` so the summary line (:1611) names
it without anyone having to query the database.

### 4.5 What does NOT count as an answer

- An utterance while `_question_open is False` — barge-in over an unfinished
  question, or speech that begins before the opening question has finished. A
  browser that starts sending audio the instant `reep.ready` lands produces
  exactly this, and it is handled with no special case.
- An utterance the validator rejects (`empty`, `filler`, `too_short`).
- An unheard turn, until the clarification budget is spent (§4.4).
- Anything spoken during WRAP_UP — `student_answered()` already returns `False`
  there, and that stickiness is why a student thanking the interviewer cannot
  re-arm a question phase.
- Anything spoken during the scorecard. Nothing is being asked, and nothing is
  being said aloud.

---

## 5. The final report

At WRAP_UP the model speaks its verdict exactly as it does today — and that
spoken verdict **is** persisted as an assistant turn, because it is part of the
interview the student heard. When its `response.done` lands, the relay issues one
further `response.create`: **text-only, in the same session, which already holds
the entire transcript.** No second provider, no second pipeline, no new egress.

### 5.1 The `response.create`, both generations

Envelope: `{"type":"response.create","event_id":<_next_event_id("report")>,"response":{…}}`.
The generation split lives inside `response`, mirroring what
`_session_update_payload` already does at :740 vs :752.

```jsonc
// beta  (settings.realtime_beta_header truthy)
{"type": "response.create",
 "event_id": "reep-<conn>-<n>-report",
 "response": {
   "conversation": "auto",
   "modalities": ["text"],
   "instructions": "<fixed scorecard directive>",
   "max_output_tokens": 800
 }}

// GA — identical, with output_modalities in place of modalities
{"type": "response.create",
 "event_id": "reep-<conn>-<n>-report",
 "response": {
   "conversation": "auto",
   "output_modalities": ["text"],
   "instructions": "<fixed scorecard directive>",
   "max_output_tokens": 800
 }}
```

Decisions, each with its reason:

- **`"conversation": "auto"`, not `"none"`.** An out-of-band response is the
  theoretically tidy choice, but on some surfaces it requires an explicit `input`
  array — and a scorecard generated with no knowledge of the interview is the
  whole feature failing silently. `"auto"` is guaranteed to have the transcript
  as context. The cost is that the JSON is appended to the conversation as an
  assistant turn, which we suppress **locally** (DF5) — and that branch is needed
  either way, so `"auto"` costs nothing extra. Note `"none"` as an optimisation
  to *test*, never to assume.
- **`modalities: ["text"]` is what makes it silent** — and it is not trusted.
  If a surface refuses a text-only response mid-session because the session
  declared audio output, `_forward_model_audio` :1225 **drops audio whose
  `response_id == _report_response_id`**, so the worst case is a scorecard the
  student does not hear rather than a robot reading JSON aloud. If the create is
  refused outright, the `error` branch fires → `report_status='rejected'`,
  `reep.report {available:false}`, close **1000**.
- **Omit `temperature`.** Beta accepts it on the response object; GA is
  unverified. Fewer fields, fewer rejections.
- **Do NOT use `response_format` / `text.format` json_schema** unless verified on
  both generations. A rejected param costs the entire report. Demand JSON in the
  instructions and parse defensively.
- **`max_output_tokens`** bounds a model that decides to write an essay and holds
  the socket past the student's patience.
- On GA the response object's `"type"` discriminator is **omitted**. The GA
  *session* object requires one (:751); the response object is unverified, and
  sending an unknown extra field is the riskier direction. If upstream rejects
  it, the `error` branch already logs `param`, so the fix is one line and the
  failure is visible on the first run rather than silent.

### 5.2 The JSON demanded

```json
{
  "overall": 0,
  "communication": 0,
  "domain": 0,
  "structure": 0,
  "strengths": ["", ""],
  "improvements": [""],
  "drill": "",
  "summary": ""
}
```

Integers are 0–100. `strengths` is 2–3 items, `improvements` 1–2, `drill` is one
concrete practice task, `summary` is one student-facing paragraph. The directive
demands *"a single JSON object and nothing else — no prose, no code fence"* and
is a **fixed string**: it names no student and quotes no transcript, because the
model already has the conversation.

### 5.3 Parse and degrade

Read the text from **`response.done`**, not from the deltas: walk
`response["output"][*]["content"][*]` for parts whose `type` is `text` or
`output_text` and take `.text`. One event, atomic, present on both generations.
Add `_TEXT_DELTA_TYPES = {"response.text.delta", "response.output_text.delta"}`
to `_HANDLED_UPSTREAM` :150 next to `_AUDIO_DELTA_TYPES` :131 and accumulate them
**only as the fallback** for when `output` comes back empty — two lines, and the
difference between a report and no report on a surface that behaves differently.
Nothing depends on them arriving.

```
strip ``` fences -> take the first "{" to the last "}" -> json.loads
  -> validate through a Pydantic model (ints clamped 0..100, list lengths and
     string lengths bounded)
```

Every failure has a status, and **none of them fails the interview**:

| `report_status` | Cause | What the socket does |
|---|---|---|
| `ok` | parsed and validated | `reep.report {available:true, …}`, close **1000** |
| `unparseable` | no JSON found, or validation failed | row written with `raw_response` truncated; `reep.report {available:false, reason:"unparseable"}`, close **1000** |
| `timeout` | the report deadline expired | same shape, `reason:"timeout"`, close **1000** |
| `rejected` | upstream refused the text-only create | same shape, `reason:"rejected"`, close **1000** |
| `unavailable` | WRAP_UP never reached (cap, disconnect, upstream close) | row written at finalization, so a mentor sees *"no report — hit the 15-minute cap"* rather than a blank screen |

**A row is written in every case.** A row saying "unavailable" is the record that
a report was attempted; a missing row says nothing at all.

There is deliberately **no** `4015 REPORT_UNAVAILABLE` close code. The brief
requires the session to *complete* when the report fails; a close code here would
make a completed interview read as a failure. The bad news travels in the payload.

### 5.4 What the student sees, and what staff see

**The student sees their own report, including the scores.** The counter-argument
is real — a 43/100 from a model that heard a nervous nineteen-year-old through a
laptop mic, with no human in the loop, is a number they will screenshot and
believe. Withholding it is still worse, for three reasons:

1. **The model already speaks a verdict aloud** at WRAP_UP
   (`interview_matrix.py`, the WRAP_UP directive). The student has heard the
   judgement. Hiding the number hides only the number.
2. A score the student cannot see but their mentor can is **a secret file on a
   student**. That is the one arrangement here that would be genuinely hard to
   defend.
3. The honest fix is calibration copy, not concealment. Render it as *"practice
   score — generated by an AI from one 15-minute session. It is not a placement
   decision and nobody grades you on it,"* next to `improvements`, never alone.

The student does **not** see `raw_response`. It is a debugging artefact that
routinely contains the model's private reasoning about them, and it earns
DIRECTOR/ADMIN scope on that basis alone.

Staff see everything the student sees, through the rule-2 gate (§7), plus
`raw_response` for DIRECTOR/ADMIN.

### 5.5 The two guards that protect the scorecard from the student's own voice

Both are small and both are easy to miss:

- **`_on_speech_started` :1240 must not cancel when
  `_active_response_id == _report_response_id`.** The scorecard is text-only and
  makes no sound — there is nothing to barge in over. Without this guard a
  student saying "thanks, bye" while the JSON generates destroys the feature's
  headline output. **This is the only place in the design where the correct
  action is to ignore the student's voice.**
- **`_on_response_done` :1270 must return before `_emit_turn` for that same id**
  (DF5), or the raw JSON lands in the student's chat history as an interviewer
  message.

---

## 6. The four new tables

New module `apps/api-py/app/models/interview.py`, registered in
`app/models/__init__.py`. One Alembic migration on head **`6afb55d18ed8`**
(`migrations/versions/6afb55d18ed8_one_active_conversation_per_owner.py`).

**These are IN ADDITION to `messages`, never instead of it.** Interview turns
keep writing a `messages` row with `channel='interview'`, so
`GET /api/agent/history` and the AGENTS.md runbook query
(`select channel, count(*), max(created_at) from messages group by channel;`)
are unchanged. That contract does not bend.

### 6.1 Every vocabulary column is a plain `String`, not a PG enum

`status`, `specialization`, `final_phase`, `speaker`, `transcription_status`,
`answer_quality`, `report_status`. Precedent: `Message.channel` and
`Conversation.consent_state` (`app/models/conversation.py`). Reason: all three of
AGENTS.md's Alembic enum gotchas are `CREATE TYPE` ordering pain, and **these
vocabularies will move** — a fifth specialization is a data change today
(`interview_matrix.py`), and turning it into a migration is a regression.
`Upload.status` earned its enum because it is a frozen three-state review
workflow; none of these are. The vocabulary goes in a comment next to the column,
which is where the next editor will look.

### 6.2 `interview_sessions`

| column | type | null | notes |
|---|---|---|---|
| `id` | `String` PK, `uuid4().hex` | — | |
| `student_id` | `String` FK `students.id` **ON DELETE CASCADE** | NOT NULL | the subject. Rule 2's gate takes a `student_id`, and every staff read is "this student's interviews" |
| `conversation_id` | `String` FK `conversations.id` **ON DELETE SET NULL** | NULL | `retention.purge_expired` hard-deletes conversations; that must not destroy the interview record, which has its own longer clock |
| `specialization` | `String` | NULL | `hr`/`dm`/`ba`/`fa`; NULL is the generic interview |
| `status` | `String`, default + `server_default` `'running'` | NOT NULL | `running`, `completed`, `abandoned`, `failed` |
| `terminal_reason` | `String` | NULL | e.g. `'4008 No audio received for 2 minutes'` |
| `final_phase` | `String` | NULL | the `InterviewPhase` value at close |
| `answers_accepted` | `Integer`, default 0 | NOT NULL | accepted answers only; matches the machine's counter |
| `turns_emitted` / `turns_persisted` | `Integer`, default 0 | NOT NULL | the pair that answers "sounded fine, saved nothing" with no join |
| `close_code` | `Integer` | NULL | |
| `conn_id` | `String` | NULL | the 12-hex relay id — joins this row to the log lines |
| `upstream_session_id` | `String` | NULL | OpenAI's session id, the support handle (:861) |
| `consent_id` | `String` FK `interview_consents.id` **ON DELETE SET NULL** | NULL | the exact grant that was live at open |
| `audio_recorded` | `Boolean`, default + `server_default` `false` | NOT NULL | **always false today — see §8** |
| `started_at` | `DateTime(tz)`, default now | NOT NULL | |
| `heartbeat_at` | `DateTime(tz)`, default now | NOT NULL | what makes orphan detection possible (§6.7) |
| `ended_at` | `DateTime(tz)` | NULL | |
| `retention_until` | `DateTime(tz)` | NULL | `started_at + interview_retention_days` |
| `deleted_at` | `DateTime(tz)` | NULL | soft-delete, the same lifecycle shape as `Conversation` |

Indexes:

```python
Index("ix_interview_session_student_started", "student_id", "started_at")
Index("ix_interview_session_orphans", "heartbeat_at",
      postgresql_where=text("status = 'running'"))
Index("ix_interview_session_retention", "retention_until",
      postgresql_where=text("deleted_at IS NULL"))
```

**No unique constraint beyond the PK, deliberately.** "One running interview per
student" is enforced by the per-user cap in the router, *not* by a partial unique
index — because a killed process leaves a `running` row behind, and an index
would then lock that student out of the feature entirely until the sweeper ran.
The index would convert a crash into a support ticket.

### 6.3 `interview_turns`

| column | type | null | notes |
|---|---|---|---|
| `id` | `String` PK | — | |
| `interview_session_id` | `String` FK **ON DELETE CASCADE** | NOT NULL | |
| `seq` | `Integer` | NOT NULL | 1-based, assigned by the relay |
| `speaker` | `String` | NOT NULL | `student` / `interviewer` |
| `phase` | `String` | NOT NULL | the phase at the time of the turn — the thing a shared `messages` row can never carry |
| `content` | `Text` | NOT NULL | **an empty string is legal** — that is a failed or timed-out transcription (L4) |
| `transcription_status` | `String`, default `'ok'` | NOT NULL | `ok`, `failed`, `timeout`, `not_applicable` (interviewer turns) |
| `answer_quality` | `String` | NULL | `accepted`, `empty`, `filler`, `too_short`; NULL on interviewer turns and on unheard turns |
| `counted_as_answer` | `Boolean`, default false | NOT NULL | whether this ticked the machine — what makes the phase arc auditable |
| `is_partial` | `Boolean`, default false | NOT NULL | an interviewer turn cut off by barge-in (L2) |
| `provider_turn_id` | `String` | NULL | `u:item_x` / `a:resp_y` — **the same single namespace `messages` uses** (:1169-1173). Do not invent a second correlation scheme |
| `message_id` | `String` FK `messages.id` **ON DELETE SET NULL** | NULL | free, because the two inserts share one transaction (§6.5) |
| `created_at` | `DateTime(tz)`, default now | NOT NULL | |

```python
UniqueConstraint("interview_session_id", "provider_turn_id",
                 name="uq_interview_turn_provider")
Index("ix_interview_turn_session_seq", "interview_session_id", "seq")   # NOT unique
```

Two notes that will otherwise be got wrong:

- The `seq` index is **not** unique. Ordering is a display concern; uniqueness of
  the *record* is `provider_turn_id`'s job. Postgres treats NULLs as distinct, so
  a turn with no upstream id does not collide. **A fire-and-forget writer must
  never be able to raise on a constraint whose violation is cosmetic.**
- `message_id`'s `ondelete="SET NULL"` must be **in the DDL**, not only in the
  ORM: `retention.purge_expired` issues a bulk `delete(Message)` that bypasses
  ORM cascades entirely.

### 6.4 `interview_evaluations`

| column | type | null | notes |
|---|---|---|---|
| `id` | `String` PK | — | |
| `interview_session_id` | `String` FK **ON DELETE CASCADE**, **UNIQUE** | NOT NULL | one evaluation per interview; the unique constraint is what makes the write idempotent under retry (DF6) |
| `report_status` | `String` | NOT NULL | `ok`, `unparseable`, `timeout`, `rejected`, `unavailable` |
| `overall_score`, `communication_score`, `domain_score`, `structure_score` | `Integer` 0–100 | NULL | **nullable even when `report_status='ok'`** — a missing score and a zero mean opposite things to a mentor. Never fabricate one |
| `strengths`, `improvements` | `JSONB`, default `list` | NULL | the type `AgentRun.trace` already uses |
| `drill` | `Text` | NULL | the one concrete practice task |
| `summary` | `Text` | NULL | the student-facing paragraph |
| `raw_response` | `Text` | NULL | the model's exact output, for debugging a bad parse — **DIRECTOR/ADMIN only** |
| `model` | `String` | NULL | |
| `generated_at` | `DateTime(tz)`, default now | NOT NULL | |

`raw_response` is the model's words about the student's words, not a database
student record, so rule 1 is untouched. But it is exactly what a student would
object to a peer reading, so it is scoped like everything else and redacted by
the retention job the way `AgentRun.question` already is.

### 6.5 One write path, one transaction — not a second writer

`_emit_turn` (:1311) gains one keyword-only frozen record
(`phase`, `seq`, `transcription_status`, `answer_quality`, `counted_as_answer`,
`is_partial`), and `_make_turn_writer`'s inner `write(...)` takes the same and
does **two inserts in one `SessionLocal()` and one `commit()`**.

A separate fire-and-forget task per table was considered and rejected:

- Two tasks per turn is two `SessionLocal()`s, two pooled connections and two
  commits. At `interview_max_sessions = 100` that doubles exactly the pool
  pressure `_make_turn_writer`'s docstring is written to avoid.
- **They can partially succeed.** `messages` has the turn and `interview_turns`
  does not, or the reverse — so the runbook's `group by channel` query reports
  "saved fine" while the reviewable record is missing turns. That is the precise
  silent-failure shape the runbook exists to catch, reintroduced somewhere new.
- Two drop-logs to correlate, and two places to remember the `u:`/`a:`
  convention.

The one cost of sharing is that an `IntegrityError` on either table rolls back
both. Mitigated by giving `interview_turns` the same
`(session, provider_turn_id)` unique shape: a retry of one turn is a no-op either
way, because the turn is already stored by the winner. **The entire value of
`interview_turns` is being the record you can trust when `messages` cannot be; a
record that can silently disagree with `messages` about which turns happened has
no such value.**

One consequence, stated plainly: `interview_turns.interview_session_id` requires
an `interview_sessions` row to exist **before** the first turn. Open it in
`routers/interview.py` beside `_open_conversation` — same `to_thread`, same
failure handling (a failed open already closes 1011) — and hand its id to
`_make_turn_writer`. That is **one extra insert per interview, not per turn.**

### 6.6 `interview_consents`

| column | type | null | notes |
|---|---|---|---|
| `id` | `String` PK | — | |
| `user_id` | `String` FK `users.id` **ON DELETE CASCADE** | NOT NULL | keyed on the *user*, not the student: a grant belongs to a person and must not be destroyed by a `Student` row rewrite |
| `version` | `String` | NOT NULL | `settings.interview_consent_version` (`"2026-08"`, already in config) |
| `scope_live_ai` | `Boolean` | NOT NULL | |
| `scope_store_transcript` | `Boolean` | NOT NULL | |
| `scope_store_audio` | `Boolean` | NOT NULL | **always false in v1 — see §8** |
| `granted_at` | `DateTime(tz)`, default now | NOT NULL | |
| `revoked_at` | `DateTime(tz)` | NULL | |
| `user_agent` | `String` | NULL | |
| `source_ip_hash` | `String` | NULL | salted hash, never the address |

```python
Index("uq_interview_consent_active", "user_id", "version", unique=True,
      postgresql_where=text("revoked_at IS NULL"))
```

Identical shape and identical reasoning to `uq_conversation_one_active_per_owner`
(`app/models/conversation.py`): a double-clicked button otherwise leaves two live
grants, and revocation clears one.

### 6.7 Finalization — three layers, and what happens when each fails

A `running` row that is never closed is worse than no row: it is a record that
lies. Three layers, in order of how much of the process has to survive.

**Layer 1 — the relay's own finalizer.** `_interview()` :1538 already funnels
*every* exit through one point, after the `except*` clauses settle
`(code, reason)`. Insert `await self._finalize_session(code, reason)` **after
`_drain_writes()`** (:1585, so `turns_persisted` is accurate) and **before** the
summary line (:1600, so that line carries the terminal status). Runs on
`asyncio.to_thread` like every other write, wrapped in
`try/except Exception: log.error(...)` — it must never replace the close code
with a teardown detail, the same discipline `_close_downstream` documents at
:1631-1636. Covers: clean end, guardrails, disconnect, upstream close, 1011.

**Layer 2 — the router's backstop.** `routers/interview.py`'s existing `finally:`
already runs on `CancelledError` (which re-raises *after* the finally) and on any
exception. Add
`await asyncio.to_thread(_finalize_if_running, interview_session_id, code, reason)`:
one `UPDATE interview_sessions SET status=…, ended_at=… WHERE id=:id AND
status='running'`. **The `AND status='running'` predicate is what makes it
idempotent against Layer 1** — if the relay already finalized, this updates zero
rows and says nothing. Covers: the relay raising before its own finalizer, and
shutdown cancellation.

**Layer 3 — the sweeper, which is the answer to "the process was killed".** New
in `app/retention.py`, matching that module's stated contract (idempotent, a pure
function of `now` so a test can pin the clock):

```python
def finalize_orphaned_interviews(db, now=None, grace_seconds=None) -> int
```

Predicate: `status='running' AND heartbeat_at < now - grace`
(`interview_orphan_grace_seconds`, default 1200 = `interview_max_seconds + 300`).
Sets `status='abandoned'`, `terminal_reason='orphaned (no heartbeat)'`, and
`ended_at = heartbeat_at` — **not `now`**. The interview ended when the heartbeat
stopped; stamping `now` would inflate every orphaned session's duration by
however long the sweeper took to run.

The heartbeat that makes it possible: `_watchdog` :1396 already wakes every 5 s.
Gate one `UPDATE interview_sessions SET heartbeat_at = now()` behind a
`_HEARTBEAT_WRITE_INTERVAL_S = 60.0` module constant, fire-and-forget on a
thread, failures logged not raised. At the 100-session cap that is 1.7
writes/second. This is a write issued from the watchdog, and it is permitted
precisely because it touches **no turn state and nothing downstream**.

Call the sweeper **once at startup** from `app/main.py`'s `lifespan`, on a
thread, wrapped so a database hiccup cannot stop the app booting. A worker that
just restarted is exactly the process that knows the previous one died, so this
catches the overwhelmingly common case (a deploy, a crash-restart) with no cron
at all. Cron wiring stays a deployment concern, as `retention.py` already says of
its existing jobs.

**If the heartbeat write fails all session long**, `heartbeat_at` stays at
`started_at` and the sweeper marks a healthy session `abandoned`. That is
wrong-but-present, and it is the correct direction to fail: a session wrongly
marked abandoned is visible and arguable, while a session stuck at `running`
forever is invisible. Say exactly that in the docstring.

### 6.8 Retention

The window is `settings.interview_retention_days` (180, already in config). 90
days (the conversation window) is short for an assessment artefact reviewed
across a semester; years is far too long for a voice-derived transcript.

`purge_expired` gains a third step with the same two-stage shape conversations
already have: past `retention_until` → soft-delete + `redact_pii` over
`interview_turns.content` and `interview_evaluations.raw_response`; soft-deleted
past `SOFT_DELETE_GRACE_DAYS` → hard-delete. **One retention story in this
codebase, not two.**

---

## 7. Access control

**Rule 2 is not re-implemented here.** `_assert_can_access_student` is imported
from `app/routers/mentor.py` and called as the **first line** of every staff
endpoint. `app/routers/leave.py` already establishes both the precedent and the
reasoning: *a second copy here would be the copy that stops tracking the first.*
A MENTOR with no `Mentor` group lands in that function's own 404 branch and sees
nobody, which is the point.

New module `app/routers/interview_records.py`. It declares **two** routers — one
at `/api/interview` and one at `/api/mentor` — so that `app/routers/mentor.py` is
**not touched at all**, and this work never collides with Track A. Both are
included from `app/main.py`.

### 7.1 Student endpoints — own record only

| endpoint | dependency | rule-2 justification |
|---|---|---|
| `GET /api/interview/sessions` | `Depends(get_current_session)`, role must be STUDENT | Filtered on `student_id == session["studentId"]`. The subject is taken from the **session**, never from a query param — the same rule `POST /api/agent/ask` and `POST /api/voice/token` follow. |
| `GET /api/interview/sessions/{id}` | same | **404, never 403**, when the row belongs to someone else. `conversations.assert_owner` already sets the no-existence-leak rule; a 403 would confirm that another student's session id exists. |
| `GET /api/interview/sessions/{id}/transcript` | same | as above. Returns `interview_turns` ordered by `seq`. |
| `GET /api/interview/sessions/{id}/report` | same | as above, minus `raw_response` (§5.4). |
| `GET /api/interview/consent` | `Depends(get_current_session)` | the caller's own live grant, or `null`. |
| `POST /api/interview/consent` | `Depends(get_current_session)`, STUDENT | Body carries the **version string the client displayed**; the server **422s a version it does not know**. That is the one thing a version string is for — a stale cached SPA must not be able to grant against copy the student never saw. |
| `DELETE /api/interview/consent` | same | Stamps `revoked_at` **and** calls `stop_sessions_for_user(user_id)` → close 4014. |

A STUDENT session with **no `studentId` claim** cannot open an interview at all
(`interview_sessions.student_id` is NOT NULL) and is refused at the socket with
the existing 1008. Add that check next to the existing role check; it is one line
and it prevents a NOT NULL violation reaching the student as a 1011.

### 7.2 Staff endpoints — through the one gate

| endpoint | dependency | rule-2 justification |
|---|---|---|
| `GET /api/mentor/students/{student_id}/interviews` | `_assert_can_access_student(session, student_id, db)` **first line** | MENTOR sees only their own group; DIRECTOR/ADMIN see all; a MENTOR with no group sees nobody. |
| `GET /api/mentor/students/{student_id}/interviews/{session_id}` | same | **and then** verify the row's own `student_id` matches the path. Never trust a session id in the path to imply its subject. |
| `.../interviews/{session_id}/transcript` | same | same two checks. |
| `.../interviews/{session_id}/report` | same | `raw_response` is included **only** when `require_director(session)` also passes. Both gates, not either: `require_director` says *which role*, `_assert_can_access_student` says *which student*, and neither answers the other's question. |

### 7.3 Two things that must not be got wrong

- **The path's `student_id` is the gate's input, and the row's `student_id` is
  the second check.** Gating on the path and then loading a session id from
  anywhere else is a horizontal-privilege bug wearing a correct-looking first
  line.
- **The student's own endpoints never take a `student_id`.** Adding one "for
  symmetry" would make every student endpoint an IDOR the moment someone forgets
  the filter.

---

## 8. Consent and recording — the honest, current state

### 8.1 Consent is a row, and today it is not

Today the interview consent panel writes `localStorage`
(`assistant.component.ts`, `CONSENT_KEY_PREFIX`). Nothing on the server reads it,
the student can clear it, and on a shared lab machine it belongs to the browser
rather than the person. That is a cache, not consent.

**Do not reuse `Conversation.consent_state`.** It is destroyed by "Clear
conversation" and hard-deleted 30 days later — a consent record that the
subject's own unrelated action destroys is not a consent record. `voice.py`
already documents that field, in the codebase's own voice, as non-enforcing
scaffolding; reusing it inherits that reputation.

**Three booleans, not one**, because they are three different disclosures and a
student may reasonably accept two and refuse the third. One boolean makes "they
consented" unfalsifiable.

**Revocation is expressible because a grant is a row.** Revoking stamps
`revoked_at` and leaves the historical row; `interview_sessions.consent_id` pins
the grant that was live when that interview opened, so *"was this student
consented at the time of interview X"* is answerable years later. A boolean on
`users` cannot answer that.

### 8.2 The wording, and the one sentence that becomes false

- **`scope_live_ai`** — "While the interview is running, your microphone audio is
  streamed to OpenAI's realtime model so it can hear you and reply. Nothing from
  your student record — marks, attendance, USN, resume — is sent with it, and the
  interviewer cannot look any of it up. Your own words are the only thing that
  leaves this machine. Please don't say marks, medical information, passwords or
  other sensitive details out loud."
- **`scope_store_transcript`** — "A written transcript of the interview — your
  answers and the interviewer's questions — is stored on the college's server as
  part of your interview record, together with an AI-generated practice score.
  Your mentor and the placement director can read it. **Clearing your
  conversation on this page does not delete the interview record**; ask the
  placement cell to remove it. It is deleted automatically after 180 days."
- **`scope_store_audio`** — **not offered.** No sentence about keeping audio
  appears in the panel, because nothing keeps it.

**That bolded clause is the most important line in this section.**
`assistant.component.html` currently promises *"The transcript is saved to your
conversation on this page and can be deleted at any time with Clear
conversation."* The moment `interview_turns` exists, **that sentence is false** —
the interview record is keyed on `interview_session_id`, survives the soft-delete
and is exactly what makes it a durable record. Shipping the tables without
changing that copy is a broken promise to the student, not a documentation
lapse. **Ship them in the same commit.**

### 8.3 Enforcement, and the ordering constraint that will otherwise lock everyone out

The socket **requires** a live consent row for the current version and closes
**4013** without one. But the client must be posting consent **before** that gate
turns on, or every existing student is locked out on deploy. The order in §9 is
therefore: client posts consent (step 8) → the router enforces it (step 9).
Do not merge those two into one commit.

### 8.4 Audio — no

> **AMENDMENT, 2026-08-20 — OVERRIDDEN BY THE PRODUCT OWNER. Capture was built.**
>
> The decision below was reversed: a stored recording is required for authorised
> review of the interview engine. It is implemented in
> **`apps/api-py/app/interview_audio.py`**, with the relay's capture path, the
> `audio_*` columns on `interview_sessions`, the DIRECTOR-only download at
> `GET /api/mentor/students/{sid}/interviews/{id}/audio`, and deletion through
> `retention.purge_expired`. `docs/interview-assistant.md` carries the operator
> account of it.
>
> **Read the rest of this section anyway.** Every one of its four objections is
> answered in that module's header, in code, and the answers are why capture is
> off by default (`INTERVIEW_RECORDING_ENABLED=false`), why it additionally
> requires a live `scope_store_audio` grant with its own unticked checkbox and
> its own sentence, why the byte cap sets a truncation flag instead of
> truncating silently, why `document_store.py` was not reused, and why the download
> needs both `require_director` and `_assert_can_access_student`. This section is
> the record of *why the default is off and every guard exists*, and that
> reasoning is still what protects the student. Two paragraphs below are now
> factually superseded and are marked where they stand: the "always false"
> promise about `audio_recorded`, and "no `audio_path`, `audio_bytes` or
> `audio_duration_ms` columns" — those columns arrived with the migration that
> added capture, exactly as the closing paragraph said they should.
>
> **SECOND AMENDMENT, 2026-08-20 — the two tracks are now also mixed into one
> file, and the product owner chose that over a stereo L/R variant.** The
> refusal to mix was NOT waved off; it was *answered*. Its premise was that the
> uplink and the downlink are not time-aligned, and under the first
> implementation that was arithmetic rather than risk: `feed()` appended only
> the bytes it was handed, so each file was a speech-only concatenation with
> every silence squeezed out. A real interview produced a 141.3 s session
> holding a 51.7 s student track and a 116.7 s interviewer track — 168.4 s of
> audio inside 141.3 s of wall clock — and any merge of those two puts the
> student's answers under the wrong questions, exactly as this section warned.
>
> `app/interview_audio.py` now stamps the recorder with one monotonic clock and
> pads each track's gaps with zeroed samples, so both files carry the SESSION's
> timeline; only then are they summed, with saturating arithmetic, into a third
> `mixed` file that `GET .../audio` serves by default. The per-speaker files
> stay and `?track=student|interviewer` is unchanged — they are the faithful
> record and the mix is a derived listening copy. The alignment is to *when the
> bytes reached the relay*, which is a beat and not a frame, and the module
> header says so rather than implying more. Padding is charged to
> `interview_recording_max_bytes` like any other byte, so `audio_bytes` (disk
> occupied, now three files) and the cap (captured PCM only) are deliberately
> different numbers; the note at `interview_sessions.audio_bytes` is where that
> decision is written down.

**Do not implement byte capture in this pass.** Three reasons:

1. **Capture is the easy part and everything around it is missing.** Raw PCM16 at
   24 kHz mono is 48 kB/s — about 43 MB per 15-minute interview, 4.3 GB/hour at
   100 concurrent sessions, onto a box whose file store has no per-student quota.
   There is no encoder, no cap-truncation semantics, no retrieval path, no
   deletion path. And `app/document_store.py` **cannot** be reused: it decides type by
   magic bytes and accepts only PDF/PNG/JPEG, so admitting audio means loosening
   the one control that makes that store trustworthy.
2. **Voice is biometric-adjacent.** A stored voice recording of a named student,
   readable by staff, is a materially different consent and legal posture from a
   transcript. It is not something to ship as a side effect of a turn-protocol
   refactor.
3. The brief says *"where consented and enabled"* — which is permission to ship
   it **off**.

**So that nobody ever believes audio exists when it does not:**

- ~~**`interview_sessions.audio_recorded` exists**, `Boolean NOT NULL DEFAULT
  false`, and in this pass is **written false and never written true**.~~
  *(Superseded: it is written true when a recording was kept. The column is
  still the one to branch on, for exactly the reason given here.)* Its
  comment, in the house voice: *"Always false today. No audio bytes are captured
  anywhere in this codebase. This column exists so that on the day capture is
  built, every pre-existing interview honestly reads 'not recorded' rather than
  'unknown' — a column added later cannot backfill a fact nobody recorded."*
- ~~**`interview_consents.scope_store_audio` exists** and is **always sent false by
  the v1 client**, because the v1 copy does not mention audio. The server does
  not refuse a `true`, but nothing reads it~~ *(Superseded: the consent panel now
  carries its own unticked checkbox with its own sentence, and `recorder_for()`
  refuses to record without a live grant whose `scope_store_audio` is true. The
  requirement that made this a separate boolean rather than one — three
  disclosures a student may answer differently — is exactly what let capture be
  added without re-asking for the other two.)* — and the model docstring says so
  with the bluntness `voice.py` already uses for its own non-enforcing field.
- ~~**`settings.interview_recording_enabled` and
  `settings.interview_recording_max_bytes` already exist in `app/config.py`
  (Track A landed them) and are READ BY NO CODE.**~~ *(Superseded: both are read
  by `app/interview_audio.py`. The underlying rule stands and is why this was
  written down — a setting that exists and does nothing is a trap — so
  `docs/interview-assistant.md` now states what they actually do instead.)*
- ~~**No `audio_path`, `audio_bytes` or `audio_duration_ms` columns.**~~
  *(Superseded: they exist, and `audio_truncated` with them. The warning was
  right and is why `audio_recorded` is the column every reader must branch on —
  a NULL path still collapses four different facts into one.)* A nullable
  path column is the single most misleading thing that could be added here: every
  future reader takes a NULL to mean *"this one wasn't recorded"* rather than
  *"recording does not exist"*. Those columns belong in the migration that adds
  capture, not before it.
- ~~`docs/interview-assistant.md` gets a short section headed **"Audio is not
  recorded"** that says it flatly, so a search for either setting finds the denial
  rather than nothing.~~ *(Superseded: that doc now carries a section headed
  **"Audio — off by default, and 'off' is two independent switches"**. The
  requirement is unchanged in substance — a search for either setting must find
  the truth rather than nothing — and a doc that denies a feature that now exists
  would be the same trap pointing the other way.)*

**If a later pass builds it** (a sketch, not a commitment): a sibling of
`document_store.py` with its own root, files named `{interview_session_id}.opus`, a
hard per-session cap after which capture stops and a truncation flag is set
(never a silent truncation), retention through the same `retention_until` column,
and retrieval only at
`GET /api/mentor/students/{sid}/interviews/{id}/audio` behind `require_director`
**and** `_assert_can_access_student`.

---

## 9. Implementation order

Ten steps. Steps that name **disjoint file sets** can be assigned to different
implementers and worked in parallel; the dependency arrows say what must land
first.

**Coordination, before anything starts.** Track A (the audit remediation) has
already modified `app/config.py`, `app/conversations.py`, `app/main.py`,
`app/routers/voice.py` and others on the working tree. `interview_relay.py`,
`routers/interview.py` and `interview_matrix.py` are **untouched**, so Track B
owns those outright. **Two files are shared and must be edited by exactly one
person at a time: `app/config.py` (step 2) and `app/main.py` (step 6).**

### Close codes — assigned once, here, so two tracks cannot collide

Added to `interview_relay.py:316-325`, and **every one of them must also get an
entry in `CLOSE_MESSAGES` at `interview.service.ts:359`.** An unmapped code falls
through to *"The interview connection closed unexpectedly"*, which is the exact
degradation 4003 and 4010 were added to prevent.

| code | constant | meaning | student-facing text |
|---|---|---|---|
| 4011 | `_CLOSE_TURN_STALLED` | the relay could not advance the interview (S2/S3) | error — *"The interviewer stopped responding. Nothing you said was lost — start a new interview."* |
| 4012 | **RESERVED for Track A's H1** (per-user session cap) | — | Track B must not use it |
| 4013 | `_CLOSE_CONSENT_REQUIRED` | no live consent row | warn — *"Please accept the interview terms before starting."* |
| 4014 | `_CLOSE_CONSENT_REVOKED` | consent withdrawn mid-interview | info — *"You withdrew consent, so the interview ended."* |

4011 is deliberately distinct from 4002: 4002 means *upstream unavailable, retry
shortly*; a turn stall on a **working** socket is a different operator diagnosis
(our sequencing) and deserves its own log line and its own sentence. `detail:
true` only for codes whose reason names a configurable figure — 4008 and 4009 do,
these do not.

Two meanings also shift without a new code, and both need a client-side edit:

- **1000** becomes the *normal* end of a completed interview: the relay closes it
  itself after `reep.report`. `CLOSE_MESSAGES[1000]` should read **"Interview
  complete — your report is ready."** rather than "Interview ended."
- **4008** must be understood as **not** a backstop for a transcription stall
  (S1). One comment at :1420.

### The steps

1. **Telemetry, with the flag still `true`.** *(files: `app/interview_relay.py`)*
   Stamp the monotonic clock at `speech_stopped`, measure to `.completed`, add
   `asrP50`/`asrMax` to the summary line (:1611). No behaviour change. This is
   what turns §2.8's central question from an argument into a number, and it
   validates `interview_transcription_timeout_ms = 8000` before that number
   becomes load-bearing. **Ship it first, or at worst in the same commit as
   step 3.**

2. **Config.** *(files: `app/config.py`, `.env.example` — **shared with Track A**)*
   Add exactly **two** settings, because Track A already landed the rest:
   `interview_response_create_timeout_ms` (10000) and
   `interview_orphan_grace_seconds` (1200). **Both must be added to BOTH
   validator name-lists — `_blank_is_default` (`config.py:446`) and
   `_must_be_positive` (`config.py:499`).** A blank line in the shared `.env`
   otherwise raises a `ValidationError` at import, before uvicorn binds, and
   takes the whole dashboard down at boot. Everything else maps onto existing
   settings: `interview_transcription_timeout_ms`, `interview_min_answer_words`,
   `interview_max_clarifications_per_question`, `interview_report_timeout_ms`,
   `interview_retention_days`, `interview_consent_version`.

3. **The relay spine.** *(files: `app/interview_relay.py`,
   `app/interview_matrix.py`, `tests/test_interview_relay.py` (new),
   `tests/test_interview_matrix.py`)* Depends on step 2.
   Flip `:715` to `false`; extend `_verify_turn_detection` :923 and add the
   `_expecting_response` runtime guard (D2); add `input_audio_buffer.committed`
   and `conversation.item.input_audio_transcription.failed` to `_HANDLED_UPSTREAM`
   :150 with branches; restructure `_pump_upstream_to_client` :1093 per §2.5; add
   `_pending`, `_question_open`, `_deferred`, `_clarifications` and the deadline
   handling; add `_advance_turn` as the single post-handshake `response.create`
   call site; move the phase tick ahead of the create; add `classify_answer` and
   the clarification override to `interview_matrix.py`; rewrite the four comments
   in §2.7.
   **`.failed` (M5) and the deadline must ship in the SAME commit as the flag
   flip.** A `create_response: false` build without both is strictly worse than
   today. If one thing from this document survives triage, it is that ordering.
   **There is no `tests/test_interview_relay.py` today** — the only relay import
   anywhere in `tests/` is `_INTERVIEWER_PERSONA` — so this diff breaks zero
   existing tests, which is itself the risk. The new module drives
   `_handle_upstream_event` against a fake upstream: **no database, no socket**,
   the same "no I/O by design" property `test_interview_matrix.py` already claims.

4. **The report.** *(files: `app/interview_relay.py` — sequential with step 3,
   same file, same implementer)*
   The text-only `response.create` at WRAP_UP, `_TEXT_DELTA_TYPES`, the
   defensive parse, the two scorecard guards (§5.5), `reep.report`, and close
   1000 with 4011 added to the close-code block.

5. **Schema.** *(files: `app/models/interview.py` (new),
   `app/models/__init__.py`, `migrations/versions/<new>_interview_record.py`
   (new), `tests/test_interview_records.py` (new))* Parallel with steps 3–4.
   Four tables per §6, `down_revision = "6afb55d18ed8"`. All-`String`
   vocabularies, so there is no `CREATE TYPE` in the migration and none of
   AGENTS.md's three enum gotchas apply.

6. **The record write path and the session row.** *(files:
   `app/routers/interview.py`, `app/interview_relay.py`)* Depends on 3, 4, 5.
   Open the `interview_sessions` row beside `_open_conversation`; extend
   `_emit_turn` with the meta record; make `_make_turn_writer` do two inserts in
   one transaction; **apply the blank-text carve-out (L4)**; adopt the
   `ConversationGone` contract (L5); add `_finalize_session` (Layer 1) and the
   router backstop (Layer 2); add the heartbeat write to `_watchdog`.

7. **Retention, the sweeper and app wiring.** *(files: `app/retention.py`,
   `app/main.py` — **shared with Track A**)* Depends on 5.
   `finalize_orphaned_interviews`, the third `purge_expired` stage, the
   startup call in `lifespan`, and the two `interview_records` router includes.

8. **Read endpoints.** *(files: `app/routers/interview_records.py` (new),
   `tests/test_interview_access.py` (new))* Depends on 5.
   Everything in §7. Imports `_assert_can_access_student` from `.mentor`
   read-only; **does not edit `mentor.py`.** The test module must cover the three
   rule-2 cases explicitly: MENTOR-in-group sees it, MENTOR-not-in-group 404s,
   MENTOR-with-no-group 404s.

9. **Client.** *(files: `apps/web/src/app/core/interview.service.ts`,
   `apps/web/src/app/features/assistant/assistant.component.{ts,html,scss}`,
   a new lazy `features/student/interviews/` component,
   `apps/web/src/app/app.routes.ts`)* Depends on 4, 8.
   `reep.report` rendering with the calibration copy (§5.4); the three new close
   codes plus the reworded 1000; the `thinking` progress affordance (§2.8); the
   corrected consent copy (§8.2) posting to `POST /api/interview/consent`; the
   interview-history and report screens.
   **The new route MUST be `loadComponent`.** The initial bundle is ~142 kB and
   the budget is set close enough that one re-eager-ed route fails `ng build` in
   CI.

10. **Enforce consent, then document.** *(files: `app/routers/interview.py`,
    `docs/interview-assistant.md`, `AGENTS.md`)* Depends on 9.
    Only now does the socket require a consent row (4013) and revocation stop
    live sessions (4014) — §8.3. Then the docs: the v3 turn protocol summary, the
    four close codes, the four tables, ~~the **"Audio is not recorded"** section
    naming `interview_recording_enabled` / `interview_recording_max_bytes` as
    read by no code~~ (**see the amendment at §8.4** — capture was built and that
    section now describes what is actually captured, when, and who may hear it),
    and one paragraph in AGENTS.md's interview section.

**Verification at every step:** `cd apps/api-py && .venv/Scripts/python -m pytest`
and, from step 9, `cd apps/web && npx ng build`.

---

## 10. What is deliberately NOT done in this pass

Each of these was proposed, considered, and declined. The reason matters more
than the decision, because the reason is what tells a future editor whether the
decision has expired.

**A 12-state machine.** The relay's real state is the handful of orthogonal
variables in §3, and an enumerated state set would have to keep every one of them
anyway — so it is not a replacement, it is a *second* mechanism running alongside
the first. Half of what it would name already exists in the browser
(`listening`/`thinking`/`speaking`) or is a coroutine's program counter. Revisit
only if a genuine state is discovered that is not derivable from §3's table.

**Six tables.** Four. `interview_consents` earns its place because
`Conversation.consent_state` cannot record *who consented, when, to what version,
from where* and is destroyed by the subject's own "Clear conversation". A fifth
and sixth were not identified as recording anything the four cannot.

**Audio capture.** §8.4. Not the encoder, not the store, not the columns. The
schema and the docs say so in words rather than leaving a nullable path column
that reads as "this one wasn't recorded".

**A separate evaluation pipeline or a second provider.** The session that
conducted the interview already holds the transcript. A second model call would
be a second egress surface to reason about under rule 1, for an artefact the
first one can produce for the price of one more `response.create`.

**`response_format` / `text.format` json_schema on the report.** Unverified on
both API generations, and a rejected param costs the entire report. Demand JSON
in the instructions and parse defensively instead. Revisit when someone has
confirmed it on both surfaces — the parse path stays either way, because
"degrade, never assert".

**`conversation: "none"` for the report.** Tidier in theory; on some surfaces it
requires an explicit `input` array, and a scorecard composed with no knowledge of
the interview is the feature failing silently. Worth *testing* later; never worth
assuming.

**Gating the `response.create` on the phase `session.updated` echo.** It would
add a round trip at every phase boundary and a brand-new stall if the echo never
arrives (S4). Fire the update, then create, and log a rejection. More correctness
here buys a worse failure mode.

**Retuning `interview_vad_silence_duration_ms` from 700 to 500.** Safe under v3
in principle (§2.8), but it is a tuning change and it must wait for step 1's
telemetry. Changing a VAD constant in the same commit as the turn protocol makes
every regression un-bisectable.

**Forwarding `conversation.item.input_audio_transcription.delta` as a
requirement.** It is the highest-value perceived-latency mitigation available and
it costs one allowlist entry — but it is unverified on both generations, so the
`thinking` affordance must cover the gap on its own and nothing may depend on
the delta arriving.

**A `4015 REPORT_UNAVAILABLE` close code.** The interview *completed*; only the
scorecard failed. A close code would make a successful interview read as a
failure. The bad news travels in the `reep.report` payload, and the socket closes
1000.

**A partial unique index for "one running interview per student".** A killed
process leaves a `running` row, and the index would then lock that student out of
the feature until the sweeper ran — converting a crash into a support ticket. The
per-user cap in the router is the right place for that rule.

**Changing anything about `messages`.** Same channel, same `u:`/`a:` namespace,
same fire-and-forget writer. `GET /api/agent/history` and the runbook's
`group by channel` query must return exactly what they return today. The new
tables are **in addition**.

**Touching the LiveKit voice stack, `voice_agent.py`, `/api/voice/*`, or
`POST /api/agent/ask`.** They are the rollback path, not dead code (AGENTS.md).
Nothing in v3 reaches them.

**Track A's findings.** C1, H1, H2, M1–M4, M8–M11 and the LOW list are the other
track's work. Two of them are load-bearing here and are named rather than fixed:
**M6** is already fixed in `app/conversations.py` on the working tree and this
spec adopts its contract (L5); **H1** owns close code 4012 and the per-user cap,
so Track B must not grow `_ConnectionLimiter` in a second shape. Note that
`routers/voice.py`'s `_TOKEN_GRANTS.try_acquire(user_id, limit)` is a
TTL-expiring grant for a stateless token and is **not** reusable for a held
WebSocket; `_ConnectionLimiter` should gain its own per-user `dict[str, int]`.

---

## Appendix — the two rules, checked

**Rule 1 (no student record leaves the machine unbidden).** Unchanged and still
structural. Everything this design authors upstream is a fixed string: the base
persona, a specialization block, a phase directive, the clarification override
and the scorecard directive. **No student transcript is ever composed into
`instructions`** (§4.3) — not because rule 1 would technically be violated (the
student's own words are already upstream in that same realtime session, so
reusing them costs no new egress) but because the shape of the code is the
guardrail, and the moment student text is composed into an instruction string,
the next editor composes a resume into it. `interview_relay.py` still imports no
ORM model, no `assistant_tools`, no `knowledge` and no `app.ai.llm`.

**Rule 2 (staff scope is decided by role).** Every new staff endpoint opens with
`_assert_can_access_student` imported from `app/routers/mentor.py` — never a
second copy — and then re-checks that the row's own `student_id` matches the
path. `raw_response` additionally requires `require_director`. A MENTOR with no
`Mentor` group sees nobody, by the existing function, with nothing added.
