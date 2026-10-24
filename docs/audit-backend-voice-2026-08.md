# REEP Backend + Voice/Interview Audit — 2026-08-19

Full audit of `apps/api-py` (FastAPI/SQLAlchemy backend) and the voice + interview-assistant stack. Every finding below was traced through the complete code path by a dedicated reviewer; the two headline security rules (student-data egress gate, staff role scoping) and the auth rules were each checked against the tests that pin them.

**This was an audit deliverable when it was written. It is no longer one.** Most of
it has since been remediated — see the status block immediately below, which is
the current truth about this document and takes precedence over the wording of
any finding it names.

## Remediation status — 2026-08-20

Checked against the code, finding by finding, not against anybody's summary of
the work. Where a finding is split, the split is stated rather than rounded up:
an audit that overstates its own remediation is worse than one nobody updated.

**Headline: C1, H1, H2, M1–M11 are all FIXED. Eleven of the eighteen LOW items
are fixed; seven remain open, as do three of the four observations.** The suite
is **454 passed, 0 failed, 0 skipped** with `REEP_REQUIRE_DB=1` and Postgres up —
including the two failures this document recorded, which were an environment
artefact and are gone.

### Critical and High

| # | status | where |
|---|---|---|
| **C1** | **FIXED** | `Settings.production_boot_failures()` (`app/config.py`) refuses to start on the repo's `AUTH_SECRET`, a placeholder, a blank, or anything under 32 characters — and on the repo's `DATABASE_URL` password. `app/main.py`'s `lifespan` raises, so the process never binds a port. Pinned by `tests/test_boot_guard.py`, which also pins "empty on every development ENV" — a guard that trips on a laptop gets deleted by whoever is shipping that afternoon. |
| **H1** | **FIXED, with one sub-point deliberately left** | `_ConnectionLimiter(limit, per_user_limit)` (`app/interview_relay.py`) owns a per-user `dict[str, int]`; one `try_acquire` checks both caps (a two-step acquire leaks slots), the worker cap first so a genuinely full process still says "the server is full". Refusal is close **4012** with its own sentence, and `GET /api/interview/status` says the same thing in words. `tests/test_interview_write_path.py::TestPerUserSessionCap`. **Left deliberately:** the all-zero-frame weakness in the idle cap. An energy gate would close healthy sessions, because the browser's echo-gate keepalive sends zeroed frames *by design* while the interviewer speaks. The abuse it would prevent is bounded by `interview_max_seconds` and now by the per-user cap; the reasoning is written into the watchdog's idle comment. |
| **H2** | **FIXED** | `_TokenGrantLedger.try_acquire(user_id, limit)` (`app/routers/voice.py`) caps concurrent token grants per student, and `VOICE_MAX_CALL_SECONDS` is now **enforced in the worker** (`voice_agent.py::_end_call_at_max_duration`) rather than left to a token TTL that only bounds join time. The header there states the resulting arithmetic instead of leaving it to be discovered. |

### Medium — all eleven fixed

| # | status | where |
|---|---|---|
| **M1** | **FIXED** | The gate is `settings.worker_auth_optional` — the dev/CI allowlist — not `is_prod`. An unrecognised `ENV` with a blank secret now shuts the door instead of opening it, the same fail-closed shape `password_login_allowed` uses. |
| **M2** | **FIXED** | `_cookie_secure()` in `app/routers/auth.py`, used at all four cookie sites (set and delete, session and flow), so the attributes cannot drift apart again. |
| **M3** | **FIXED** | `app/routers/leave.py` imports `_assert_can_access_student` from `.mentor` — never a second copy — and calls it. A MENTOR with no group again sees nobody. |
| **M4** | **FIXED** | `validate_usn_pattern()` at rule-write time: a length cap, a refusal of quantifiers applied to groups, and `re.error` turned into a `ValueError` the writer sees. The public endpoint no longer inherits an unvalidated pattern. |
| **M5** | **FIXED** | `conversation.item.input_audio_transcription.failed` is in `_HANDLED_UPSTREAM` with a branch. A failed or timed-out transcript resolves its pending entry, the turn is still recorded (with `transcription_status` saying why), and the interview continues. |
| **M6** | **FIXED** | `ConversationGone` (`app/conversations.py`) is raised rather than written into a soft-deleted row; the interview writer translates it to `_TurnWriteRefused` at the boundary where it holds a Session, and the relay logs it against the connection id and ends the session — instead of one dropped-turn line per turn for the rest of a fifteen-minute call. |
| **M7** | **FIXED, and it was the reason for the v3 engine** | `turn_detection.create_response` is `false`; the relay creates every response from one call site, and the phase tick runs **ahead of** the create. The directive computed for answer N now steers the response to answer N. See `docs/interview-engine-v3.md`. |
| **M8** | **FIXED** | `users.token_version` rides in the session token and `POST /api/auth/logout` bumps it (`app/routers/auth.py`), with `current_token_version` checked in `verify_session_token`. A token captured before logout is refused. |
| **M9** | **FIXED** | `users.google_sub` is persisted on first sign-in and pinned thereafter; a verified `sub` that does not match the stored one is **refused**, so a re-provisioned institutional address cannot inherit the previous student's record. |
| **M10** | **FIXED** | The default line logs role, character count and item id. Spoken text is logged only behind an explicit `VOICE_LOG_TRANSCRIPTS` flag, with the consequence written next to it. |
| **M11** | **FIXED** | `VOICE_TTS` defaults to `groq`. edge-tts is opt-in, and the function that builds it says so. |

### Low — eleven fixed, seven open

**Fixed:** constant-time worker-secret compare (`hmac.compare_digest`) · the
unreferenced disconnect task (now `_spawn`, with a strong ref and an observed
exception) · the heartbeat upsert's `IntegrityError` (rollback and retry as an
UPDATE) · the blank-env crash at import (`_int_env` in `voice_agent.py`, the
same treatment `_blank_is_default` gives the API) · `/docs` and `/openapi.json`
disabled in production · public-registration rate limiting (a bounded per-IP
window that cannot grow without limit) · the unchecked `None` on the caller's own
`Student` in `GET /student/leaderboards` (404, matching its siblings) ·
`create_offer`'s unvalidated `job_id` (404) · the per-student upload quota (409
before the body is buffered, so an over-quota student never gets their megabytes
read into memory) · the time-based path to WRAP_UP (`_wrap_up_deadline`, the
report reserve — a five-answer interview no longer ends with nothing because the
cap landed during the wrap-up) · `logout`'s cookie deletion now mirrors its
set-time attributes.

**Open, and each for a reason worth knowing:**

- **The registration email-existence oracle.** A duplicate application still
  409s. The endpoint's own comment names the trade — a fresh submission 201s and
  this one does not — and the only complete fix is answering identically in both
  cases, which costs the applicant the "you already applied" message.
- **The 404-detail existence leak to out-of-group mentors.** `mentor.py` still
  answers "Student not found." to a DIRECTOR and "Student not in your mentor
  group." to a MENTOR, which distinguishes the two. Minimal, because the ids are
  uuid4 — but the newer `interview_records.py` endpoints deliberately do **not**
  copy it.
- **The client ignores the advertised audio format.** `interview.service.ts`
  still hard-codes `SAMPLE_RATE = 24000` and does not read `reep.ready`'s
  `audio.sample_rate`. A server-side rate change would silently pitch-shift.
- **`InterviewStateMachine.end()` / `InterviewPhase.ENDED` are still
  unreachable** in production. The v3 finalizer deliberately does **not** call
  `end()` — stamping `ended` on every row would erase the one fact
  `final_phase` exists for, which is how far the arc actually got.
- **`GET /api/interview/status` still presents per-worker counters as global.**
  With N uvicorn workers `available:true` can precede a 1013. The per-user cap
  (H1) removed the common way to hit it, not the arithmetic.
- **Unbounded log volume on the two hostile-client frame paths.** Both oversized
  frame branches in `interview_relay.py` still log one WARNING per frame with no
  budget, unlike the 32-entry cap on the unknown-control memo a few hundred lines
  above them.
- **No dedicated CSRF token.** Unchanged and accepted: protection still rests on
  `SameSite=Lax` plus explicit-origin CORS, and a future `SameSite=None` route
  would silently lose it.

### Observations — three stand, one was answered

Three are unchanged, and all three were decisions rather than defects:
transcripts still replay to a remote LLM with `carries_student_data=False`
(they are the student's own words, so Rule 1 as written holds); the leaderboard
still shares peer CGPA by default with an enforced opt-out; and the voice
worker's absence is still invisible in a UI that has no voice caller at all.

The fourth — `POST /api/agent/feedback` 404ing on every request and the
`AgentRun` counters in `GET /api/agent/metrics` reading 0 — was answered by
making both **say so** rather than by making them work, which is the honest fix
for a surface that is deliberately unfed. `AGENT_RUNS_COLLECTED = False`
(`app/routers/agent.py`) gates a 404 detail that names the supersession, and the
metrics payload carries `collected: false` plus `SUPERSEDED_RUNS_NOTE` so a
frozen history is not read as a live zero. Both retire themselves on rollback:
flip the constant back to `True` and the plain answers return with no second
edit. The behaviour the observation described is unchanged — the *silence* is
what was fixed.

### What this wave added that the audit did not ask for

The interview engine was rebuilt to v3 (`docs/interview-engine-v3.md`) rather
than patched: the relay owns response creation, answers are gated
deterministically before they advance the arc, and the interview ends with a
persisted scorecard. With it came four tables, a real consent row enforced at the
socket (4013/4014), a three-layer finalizer and an orphan sweeper, a 180-day
reaper, and — by a later product-owner override of §8.4 — an off-by-default audio
recording store. `docs/interview-assistant.md` is the operator account of all of
it.

The consent gates are pinned by `tests/test_interview_consent_gate.py`, and it is
worth saying why they got a module of their own rather than a case in the write
path. The two gates fail in **opposite** directions on purpose — opening fails
closed, the mid-interview revocation check fails open — which is exactly the kind
of asymmetry a later editor tidies into consistency because it looks like an
oversight. The tests also pin that a refusal writes **nothing**: no conversation,
no `interview_sessions` row, so a student who never agreed leaves behind no
record of an interview that never happened for the orphan sweeper to eventually
mark `abandoned` and a mentor to read as someone who walked out.

## How it was checked

Four independent reviewers, one per surface, plus the backend test suite run against a live Postgres (`REEP_REQUIRE_DB=1`, so the RBAC/voice/conversation tests could not silently skip):

1. **Auth / SSO** — `google_auth.py`, `routers/auth.py`, `security.py`, `identity.py`, `config.py`, the seeds.
2. **Voice stack** — `voice_agent.py` (LiveKit worker), `routers/voice.py`, worker-secret auth, LiveKit token scoping.
3. **Interview relay** — `interview_relay.py`, `routers/interview.py`, `interview_matrix.py`, the WebSocket to the OpenAI Realtime API.
4. **Core API + data layer** — role scoping / IDOR across every router, the egress gate and all its callers, KB retrieval, migrations, file store.

## The two must-not-break rules — both hold (with one exception)

- **Rule 1 — student data does not leave the machine unbidden: CONFIRMED, no bypass.** The gate (`app/ai/llm.py:105-110`) is enforced pre-flight in both `complete_chat` (`:130`) and `stream_chat` (`:173`). The only `carries_student_data=True` paths are resume generation (`student.py:965-971`) and orchestrator polish (`orchestrator.py:573-578`), both double-gated, both degrading to a deterministic answer with an honest `used_ai=false`. The loopback allowlist matches on literal host, so a DNS alias pointing at localhost fails **closed**. The interview and voice model paths carry no DB student record into instructions.
- **Rule 2 — staff scope decided by role, not a missing field: CONFIRMED, except leave (M3 — since fixed; `leave.py` now calls the one gate).** `_assert_can_access_student` (`mentor.py:72-84`) guards all seven per-student staff endpoints; "no mentor group → nobody" (`return []`) is explicit at every list site; no student-side IDOR was found. The one gap: **leave requests skip the group check entirely** (see M3).

## Findings by severity

### CRITICAL

**C1 — Default `AUTH_SECRET` boots in production with no guard.** `config.py:70`
The default `"reep-dev-secret-change-me-in-production-0123456789abcdef"` is committed to the repo (and `.env.example:11`). It signs the HS256 `reep_session` JWTs (`security.py:48`) and derives the OAuth flow-cookie key (`google_auth.py:201`). Nothing validates it at startup — the `lifespan` hook (`main.py:63`) checks only `VOICE_WORKER_SECRET`.
*Scenario:* a prod deploy that forgets to set `AUTH_SECRET` boots normally; anyone who has seen the repo mints `{"userId":"x","role":"DIRECTOR",...}` signed with the default, drops it in the cookie, and gets full DIRECTOR access to every student's marks, attendance and USN — plus forgeable OAuth state. *(Flagged independently by both the auth and core-API reviewers.)*
*Fix shape:* refuse to boot when `is_prod` and `auth_secret` equals the default / is too short.

### HIGH

**H1 — No per-user cap on concurrent interviews: one student can hold every slot and multiply OpenAI spend.** `routers/interview.py:269`, `interview_relay.py:421-457`
The limiter counts sessions **globally per worker**, never per `userId` (`_LIVE_SESSIONS` is an unkeyed set, `interview.py:92`). *Scenario:* any enrolled student loops `new WebSocket('/api/interview')` from devtools — each socket auths, takes a slot, and the upstream handshake fires `response.create` (`interview_relay.py:886-894`), so the model speaks and **bills immediately with zero microphone input**. A 1920-byte all-zero frame every <120 s advances `_last_audio_at` (`interview_relay.py:1003`, no energy check), defeating the idle cap; the 15-min hard cap is answered by reopening. Result: all `interview_max_sessions` (default 100) held by one account, everyone else gets 1013.

**H2 — Voice path mirrors it: unlimited tokens, no call-duration cap.** `routers/voice.py:241`, `voice_agent.py:590-594`
Unlimited tokens minted per student (each a fresh room); token TTL bounds *join* time, not call length. Contrast the interview stack's `interview_max_seconds=900` / `interview_max_sessions=100`. *Scenario:* one scripted session loops mint-token + join → worker memory exhaustion and unbounded Groq spend. Only mitigated today because the voice UI has no caller (rollback path).

### MEDIUM

**M1 — Worker-auth fail-closed is keyed on `is_prod`, not the dev allowlist.** `routers/voice.py:81-87`
An unrecognized `ENV` (`staging`, a typo, blank from a broken deploy) with a blank `VOICE_WORKER_SECRET` leaves `/heartbeat` and `/transcript` **fully unauthenticated** — anyone can forge heartbeats (voice reports available, students handed tokens into silent rooms) and write forged assistant-labelled turns into any conversation whose 32-hex id they observe (turns render in the UI and replay into later LLM prompts). The startup warning (`main.py:63-69`) is also `is_prod`-gated, so nothing warns. Contradicts the fail-closed philosophy the codebase applies to `password_login_allowed`.

**M2 — Session + OAuth-state cookies lack `Secure` on any environment not named exactly prod.** `auth.py:143,197,344`
`secure=settings.is_prod`, and `is_prod` is true only for `{prod, production, prd, live}` (`config.py:55`). A `staging`/`uat`/`demo` box on HTTPS holding real roster rows issues the session cookie **without `Secure`** → sniffable on any downgraded/plain-HTTP request.

**M3 — Leave requests bypass mentor-group scoping entirely.** `leave.py:79-98`, `:106-163`
`pending_leaves` and `decide_leave` gate only on `require_mentor`, never `_assert_can_access_student`. *Scenario:* a MENTOR with no group — who by Rule 2 must see nobody — lists every pending leave programme-wide, including the free-text `reason` (often medical/personal), and can approve or reject any student's leave. The only student-touching surface where "no group" ≠ "nobody". (Mitigated: `LeaveOut` omits the requester's identity, which also makes the approval flow oddly anonymous.)

**M4 — DB-sourced regex evaluated on the public registration endpoint.** `registration.py:41`
`re.search(rule.usn_pattern, usn)` runs over all enabled rules on the unauthenticated `POST /api/register`. An invalid pattern (`[`) raises `re.error` → 500 on every USN-carrying application; a catastrophic pattern is a ReDoS on the public front door. Requires a director/DB write to plant, but the blast radius is total loss of sign-up with nothing pointing at the bad rule.

**M5 — Interview transcriber failure is unhandled: the phase machine silently freezes and the student's half of the record is dropped.** `interview_relay.py:150-165`
`conversation.item.input_audio_transcription.failed` is absent from `_HANDLED_UPSTREAM` and has no branch. The user turn is persisted, and the state machine ticks, only from the `.completed` branch. *Scenario:* the transcription backend degrades mid-session → the interviewer keeps asking questions, the phase never leaves OPENING/PROBING (no wrap-up verdict), the DB records assistant-only turns, and the browser shows no "You" lines with no error surfaced.

**M6 — Interview/voice turns keep writing into a soft-deleted conversation.** `interview.py:312`, `conversations.py:92-130`
`conversation_id` is resolved once at socket open and reused for the whole session; `append_message` never checks `deleted_at`. *Scenario:* mid-interview the student hits `DELETE /api/agent/conversation` from a second tab → every subsequent turn lands in the soft-deleted row, invisible to `GET /api/agent/history` forever, while the runbook's `group by channel` query still counts them (masking the loss).

**M7 — Phase directives land one response late; the wrap-up verdict effectively needs a sixth answer.** `interview_relay.py:1162-1184` vs `:715`
The machine ticks on `_USER_TRANSCRIPT_DONE`, but the session runs `create_response:True`, so the response to answer N is created at VAD commit *before* transcription completes; `_push_phase`'s instructions-only `session.update` steers only the *following* response. After answer 5 the machine moves to WRAP_UP but the in-flight response (composed under the deep-dive directive) asks another question; the student needs a 6th answer to hear the verdict — and if the hard cap lands first, no verdict at all.

**M8 — Stateless sessions have no server-side revocation.** `auth.py:490-493`
`logout` only deletes the cookie; a token captured before logout stays valid for the full 12h TTL (`security.py:21`). No `jti`/deny-list. "Log out on a shared lab machine" does not invalidate a copied token.

**M9 — Identity keyed on email string only; Google `sub` verified but never persisted.** `auth.py:469-481`, `:413`
The roster match is on `lower(email)`. If the college re-provisions an institutional address to a new person, the new Google account inherits the prior student's marks, uploads and mentor notes with a fully valid sign-in. The code documents the fix (pin `users.google_sub`) but doesn't do it.

**M10 — Voice worker logs spoken text at INFO — a shadow transcript outside every retention control.** `voice_agent.py:810-815`
The first 60 chars of every turn are logged. `conversations.clear` soft-delete, `RETENTION_DAYS`, and the deleted-conversation refusal none touch log files. A student who uses "Clear conversation" still leaves their spoken words in the worker log indefinitely.

**M11 — `VOICE_TTS=edge` default routes assistant replies to an unofficial, no-terms Microsoft endpoint.** `voice_agent.py:127`
`requirements-voice.txt:56-60` itself declares edge-tts unfit for a student cohort, yet it remains the default. Accepted today (no UI caller); must flip before any re-enable. *(Note: the student's own microphone audio also egresses to LiveKit Cloud + Groq ungated — inherent to a cloud voice stack, and the consent endpoint at `voice.py:342-360` is non-enforcing by its own docstring.)*

### LOW

- **Non-constant-time worker-secret compare** — `voice.py:89` uses `!=` not `hmac.compare_digest` (house style is constant-time everywhere else).
- **Unreferenced disconnect task** — `voice_agent.py:677` `asyncio.create_task(ctx.room.disconnect())` keeps no strong ref / never observes the exception (the same GC hazard the file documents elsewhere); on failure the room stays up discarding every turn.
- **Heartbeat upsert has no `IntegrityError` handling** — `voice.py:134-157` read-then-insert can race a retry into a unique violation → spurious 500 → the alarming log line the runbook tells operators to hunt.
- **Blank-env crash at import** — `voice_agent.py:151` `int(os.getenv("VOICE_HEARTBEAT_INTERVAL_SECONDS","10"))` throws on a blank line in the shared `.env` — the exact failure `_blank_is_default` prevents for the API process.
- **`/docs` and `/openapi.json` exposed in production** — `main.py:88` (endpoints stay auth'd, but the full surface is disclosed).
- **Public registration: no rate limit + email-existence oracle** — `registration.py:118-122` (DB flood; 409 confirms an application email exists).
- **Unchecked `None` on the caller's own Student row** — `student.py:1709` → a session outliving a deleted Student 500s `GET /student/leaderboards` (sibling endpoints guard this).
- **`create_offer` accepts an unvalidated `job_id`** — `student.py:702-735` → IntegrityError 500 instead of 404/422.
- **No per-student upload quota** — `document_store.py:31` caps one file at 10 MB, but `student.py:1351` has no count/total cap → an authenticated student can exhaust disk.
- **404-detail existence leak to out-of-group mentors** — `mentor.py:215-218,365-368,448-451,548-551` ("X not found" vs "not in your group"); uuid4 ids make it minimal.
- **Client ignores the advertised audio format** — `interview.service.ts:54` hard-codes `SAMPLE_RATE=24000`, ignoring `reep.ready` → a server rate change would silently pitch-shift audio.
- **Dead terminal state** — `InterviewStateMachine.end()`/`InterviewPhase.ENDED` are unreachable in production (only a unit test calls them).
- **No time-based path to WRAP_UP** — `interview_matrix.py:169-171` is answer-count-only; a deliberate student giving 3 long answers is cut off at 15 min with no verdict.
- **`/api/interview/status` presents per-worker counters as global** — `interview.py:83-87`; with N uvicorn workers `available:true` can precede a 1013.
- **Unbounded log volume on two hostile-client paths** — `interview_relay.py:979-988` and `:1037-1052` log a WARNING per frame (uncapped, unlike the 32-cap unknown-control memo).
- **`logout` cookie deletion doesn't mirror set-time attributes** — `auth.py:492` (works today; asymmetric with `_clear_flow_cookie`).
- **No dedicated CSRF token** — protection rests entirely on `SameSite=Lax` + explicit-origin CORS; a future `SameSite=None` route would silently lose it.
- **Default `DATABASE_URL` carries a committed dev password** — `config.py:68` (same "prod on a repo default, no guard" pattern as C1, but fails safer).

## Observations (not rule violations, worth a decision)

- **Transcripts replay to a remote LLM without the egress flag.** `POST /api/agent/chat` replays conversation history channel-agnostically (`conversations.py:133-143` filters on `is_final` only), so interview/voice transcripts go to a remote LLM with `carries_student_data=False` (`agent.py:231,292`). That content is the student's own spoken/typed words, not DB records, so **Rule 1 as written holds** — but a transcript in which a student recites their CGPA aloud travels to a free-tier provider.
- **Leaderboard shares peer CGPA by default.** `student.py:1649-1659` exposes every non-opted-out cohort peer's latest CGPA with full name to any student in the cohort. Opt-out exists and is enforced both directions (`:1713-1727`), but the default shares marks-derived data peer-to-peer.
- **`/api/agent/feedback` 404s on every request and `/api/agent/metrics` AgentRun counters read 0** — expected since the interviewer replaced the agent UI (no `AgentRun` rows written), but silent; a tombstone response or a metric-source migration would stop it reading as breakage.
- **The voice worker's absence is invisible in the UI** — no worker → `POST /api/voice/token` 409, and nothing tells anyone a fourth process exists (the top "why is it broken" report). Surfacing `worker_healthy` would fix it. (Note: the voice UI currently has no caller at all — rollback path only.)

## Test suite

*(As audited on 2026-08-19. Both failures are gone: the suite is 454 passed, 0
failed, 0 skipped as of 2026-08-20 — the LLM stub is now kept alive across the
provider-outage patch, so neither test makes a real outbound call. The
observation about `/chat` not degrading on a provider connection error was the
real finding here, and it is **fixed**: `POST /api/agent/chat` now catches
`httpx.TransportError` separately from a provider that answered with a failure,
and returns 200 with `used_ai=false` and an honest note rather than propagating
the connection error. The distinction is the point — a box with no internet is an
operational condition, and a provider that answered with an error is a bug that
must stay loud.)*

Run with `REEP_REQUIRE_DB=1` and Postgres up: **2 failures, both environment-caused, not code bugs.** The local `.env` has a live LLM provider configured, so `test_greeting_survives_a_failed_first_turn` and `test_semantic_query_with_no_shared_tokens_lands_on_the_right_doc` make real outbound network calls that get reset (`httpx.ConnectError`, WinError 10054). Worth noting from the trace: in `test_greeting` the `ConnectError` propagated **unhandled** out of `complete_chat` at `agent.py:231` — the `/chat` endpoint does not degrade gracefully on a provider connection error. The rest of the suite passes.

## Notably done right

- **Server owns everything on the token path** — no client-named room or conversation; per-call room nonce; minimal LiveKit grants; 10-min TTL (`voice.py:241-320`). Room isolation genuinely holds.
- **Auth is fail-closed by design** — password login is an *allowlist* of dev names, not `not is_prod`, so a typo'd ENV shuts the door; the guard fires before any DB read, killing account-existence probing.
- **Google verification is complete** — RS256 pinned twice (header pre-check + `algorithms=`), `aud`/`iss`/`exp`/`email_verified` all checked, required-claims force `exp` presence, nonce timing-safe; non-enrolled → `sso_not_enrolled` with no cookie.
- **Login-CSRF / session-fixation defense is textbook** — signed single-use state cookie cleared on every outcome, timing-safe compares with an `isascii()` guard; flow-cookie/session cross-use closed in both directions.
- **Egress gate degrades deterministically** — refuses before the bytes leave the process, then composes a working answer and says `used_ai=false` rather than failing.
- **Fire-and-forget done properly** on both voice and interview — strong refs on write tasks with done-callback cleanup, bounded drain under the shutdown timeout; transcript dedup is three-deep (check + `UniqueConstraint` + `IntegrityError` no-op).
- **Interview relay: backpressure by construction** both directions, a genuinely subtle base64→JSON-breakout vector closed on the hot path, barge-in race handled, VAD verified at handshake (fails 4002 rather than muting the interviewer).
- **File store hardening** — magic-byte type decisions, random stored names, separator rejection on read *and* delete, RFC 6266 Content-Disposition.
- **Migrations follow the documented enum discipline**; `models/__init__.py` imports all 31 modules; `db.py` uses `pool_pre_ping` + per-request close; `sqlalchemy_url` preserves `sslmode` (guarding the silent-TLS-downgrade bug it documents).

## Suggested fix order (when you choose to act)

*(Historical. Steps 1–5 were all done on 2026-08-20 — see the remediation status
block near the top. What is left of step 6 is the seven open LOW items and the
three standing observations, listed there rather than here.)*

1. **C1** — prod boot guard on `AUTH_SECRET` (and ideally `DATABASE_URL`). One check, highest impact.
2. **H1 / H2** — per-user session caps + a max call/interview duration before either realtime path is exposed to students at scale.
3. **M1 / M2** — move the worker-auth fail-closed and the cookie `Secure` flag off the narrow `is_prod` name test onto the dev allowlist.
4. **M3** — apply `_assert_can_access_student` to the leave endpoints.
5. **M4** — validate `usn_pattern` at rule-write time and wrap the per-rule `re.search`.
6. The remaining M/L items and observations as capacity allows.
