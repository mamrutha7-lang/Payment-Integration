/**
 * InterviewService — the browser half of REEP's realtime AI mock interviewer.
 *
 * It owns ONE live interview: a WebSocket to `/api/interview`, an AudioWorklet that
 * captures 24 kHz mono PCM16 from the microphone, a gapless playback scheduler for the
 * interviewer's voice, and the barge-in flush that silences that scheduler the instant
 * the student talks over it. State comes out as Angular signals; nothing in here touches
 * the DOM beyond the AudioContext graph, so the component owns every pixel.
 *
 * This is a port of apps/interview-realtime/public/app.js into REEP's house style. The
 * audio code below is PHYSICS, not convention — the clamp-before-Int16 rule, the
 * anti-alias biquad, the odd-byte carry, the never-schedule-in-the-past guard and the
 * GainNode-swap flush were each derived from a real audible failure. Every one of those
 * reasons is written beside its constant. Adapting them means re-deriving them.
 *
 * THREE THINGS THAT ARE NOT OBVIOUS AND ARE LOAD-BEARING:
 *
 * 1. AUTH IS REEP'S, AND IT IS THE COOKIE. A browser WebSocket cannot set headers, so
 *    there is no Authorization to send — but it DOES send cookies on a same-origin
 *    connection. apps/web/proxy.conf.json maps `/api` to the FastAPI process with
 *    `"ws": true`, so this socket is same-origin to the page and the httpOnly,
 *    SameSite=Lax `reep_session` cookie rides the handshake. That is verified fact, not
 *    an assumption: a path outside `/api` would lose both the proxy and the cookie, and
 *    is why the URL below is built from `environment.apiBase` rather than hard-coded.
 *
 * 2. NO STUDENT RECORD REACHES THE MODEL. The Realtime session is a REMOTE provider.
 *    This client sends microphone audio and nothing else — never a mark, an attendance
 *    figure, a CGPA, a USN or any resume text. The interviewer persona is authored
 *    server-side and states that it cannot see the dashboard. If a future change wants
 *    to personalise the interview with a student fact, it does NOT go on this socket:
 *    it goes through complete_chat(..., carries_student_data=True) in
 *    apps/api-py/app/ai/llm.py, which is the egress gate (AGENTS.md rule 1).
 *
 * 3. A FAILED WEBSOCKET HANDSHAKE CARRIES NO CODE AND NO REASON. The browser reports it
 *    as a bare 1006, so "not signed in", "not a student" and "not configured" are
 *    indistinguishable from the socket alone. `GET /api/interview/status` is therefore
 *    consulted BEFORE connecting and is the discriminator — exactly the pattern
 *    `/api/voice/status` already sets in ChatVoiceService.startVoiceSession.
 */

import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import { environment } from '../../environments/environment';
import { BARGE_IN_CONSECUTIVE_CHUNKS, EchoGate, FarEndTimeline, echoWindow } from './echo-gate';

/* ============================================================================
   Tunables. Every number here has a reason; none of them are inline literals.
   ========================================================================== */

/** The whole pipeline is 24 kHz mono PCM16 — mic, wire, and playback alike.
 *  Changing it means changing the relay's session.update too, so it is one
 *  constant rather than a value repeated at four call sites. */
const SAMPLE_RATE = 24000;

/** 40 ms = 960 frames = 1920 bytes per uplink message = 25 messages/s.
 *  20 ms doubles the message rate for latency the ear cannot hear; 100 ms adds
 *  a perceptible delay to server-VAD barge-in detection, because the server
 *  cannot see speech in audio we have not sent yet. */
const CHUNK_MS = 40;

/** Jitter buffer, ADAPTIVE. The first buffer of a response is scheduled this
 *  far in the future, so a frame arriving late still lands ahead of the play
 *  cursor instead of being scheduled into the past.
 *
 *  It starts at 140 ms rather than the 80 ms this file shipped with. 80 ms left
 *  30 ms of margin over the ~50 ms at which ordinary Wi-Fi underruns, and every
 *  underrun re-arms the cursor — which is audible as the gap-and-click the
 *  student reports as "breaking, flickering". 140 ms is ~3x the observed jitter
 *  on a congested campus link and still well inside the ~200 ms at which the
 *  interviewer starts to feel laggy in conversation. */
const PLAYBACK_LEAD_MIN_S = 0.14;

/** Ceiling on the adaptive lead. 300 ms is the point at which a turn-taking
 *  conversation stops feeling like one; a link that still underruns at 300 ms is
 *  not going to be rescued by buffering, and the honest report is the counter.
 *  This bounds the worst case: 300 - 140 = 160 ms of extra first-audio delay
 *  over a whole session, and it is only ever reached by a link that has already
 *  produced four audible dropouts. */
const PLAYBACK_LEAD_MAX_S = 0.30;

/** Growth per underrun: one CHUNK_MS. Growing by the size of the unit that
 *  arrived late is the smallest step that can actually cover the miss, and four
 *  steps reach the cap — fast enough that a bad link settles inside one answer,
 *  slow enough that a single hiccup does not cost the whole latency budget. */
const PLAYBACK_LEAD_STEP_S = 0.04;

/** De-click fade applied to everything still scheduled when the student barges
 *  in. Stopping a buffer mid-waveform is a step discontinuity, i.e. an audible
 *  click; 12 ms is inaudible as a fade and inaudible as a delay. */
const FLUSH_RAMP_S = 0.012;

/** Uplink backpressure ceiling. If the socket's send buffer exceeds this the
 *  network cannot keep up, and queueing more microphone audio only makes the
 *  interview more stale — it never makes it arrive. ~0.5 s of PCM16 at 24 kHz.
 *  Dropping here is the only place in this file that discards audio, and it is
 *  bounded and counted rather than silent. */
const MAX_UPLINK_BUFFERED_BYTES = 24000;

/** Mic-meter repaint cap. Chunks arrive 25x/s; the METER bar is a rendered DOM
 *  element, and repainting it that often is main-thread cost for motion the eye
 *  cannot resolve past ~15 Hz. The ORB is deliberately NOT throttled by this —
 *  `userRms` is published on every chunk, because it only moves a damper target
 *  inside the visualizer and the damper is what smooths it. */
const METER_MIN_INTERVAL_MS = 66;

/** Peak-hold decay for the meter, per second. A raw RMS reading flickers to
 *  zero between syllables and reads as a broken microphone; decaying the peak
 *  gives the bar the ballistics of a real VU meter. */
const METER_DECAY_PER_S = 2.2;

/** RMS of speech sits around 0.05-0.2, so a linear 0..1 bar barely moves.
 *  This maps RMS onto the meter with a compressive curve tuned so normal
 *  speaking voice lands in the middle of the bar. */
const METER_GAIN = 3.2;
const METER_CURVE = 0.65;

/** Interviewer-amplitude sampling period, ms (20 Hz), and the analyser window.
 *
 *  Measured off an AnalyserNode on the PLAYBACK bus, not off arriving frames.
 *  The relay sends audio faster than realtime, so most of a response is
 *  scheduled-but-unplayed: an amplitude taken from the arriving bytes would
 *  swell the orb seconds before the student heard anything. 20 Hz is the rate
 *  VoiceVisualizer's own real-audio note specifies, and fftSize 512 at 24 kHz is
 *  a 21 ms window — long enough for a stable RMS, short enough to track a
 *  syllable. */
const AI_LEVEL_INTERVAL_MS = 50;
const AI_ANALYSER_FFT = 512;

/** Session clock fallback ONLY. The real cap is whatever the relay reports in
 *  `reep.ready.limits.session_max_seconds`; the warning turns on two minutes
 *  early so the interview ending is expected rather than a mystery disconnect.
 *  Reading the wrong field here is what made the clock display "/ 15:00" on a
 *  relay configured for ten minutes, so `limits` is read as a nested object. */
const DEFAULT_SESSION_MAX_S = 900;
const SESSION_WARN_LEAD_S = 120;

/** Repaint cap for the gate's counters and the playback stats. They are
 *  written on the 25 Hz uplink path and polled off the playback clock, but a
 *  human reading "frames withheld" or "buffer 180 ms" cannot use more than a few
 *  updates a second, and every signal write costs a change-detection pass. */
const DIAGNOSTIC_PUBLISH_MS = 250;

/* ----------------------------------------------------------------------------
   The `thinking` affordance — the UX half of the v3 protocol change.

   BEFORE v3 the next question was created upstream at VAD commit, i.e. ~700 ms
   after the student's last phoneme, in parallel with transcription. UNDER v3
   the relay owns turn-taking, so the question is created only AFTER the
   transcript lands: the ASR round trip is now in SERIES, and it is roughly
   linear in answer length — worst on the longest answers, which are exactly the
   answers a good candidate gives.

   That silence is real and it is not going away. What must not happen is the
   student reading it as a broken app: three seconds behind a static orb is
   indistinguishable from a dead socket, and a student who starts talking again
   to "wake it up" produces a second utterance the relay must then merge.

   So the wait gets a visible clock. Nothing here talks to the server — it is
   perceptual cover, and it costs the relay nothing.
   -------------------------------------------------------------------------- */

/** How long `thinking` may last before the UI starts SHOWING that it is
 *  working. Not zero: a normal short answer resolves well inside this, and an
 *  affordance that flashes on every single turn is noise that trains the
 *  student to ignore it. 1.2 s is past the "instant" threshold and short enough
 *  that the affordance is on screen before doubt sets in. */
const THINKING_AFFORDANCE_AFTER_MS = 1200;

/** Poll period for the thinking clock. It writes a signal only when a RENDERED
 *  value changes — the whole-second counter, and the one-way `slow` flip — so
 *  the true cost is ~1 change-detection pass per second, not 5. 200 ms bounds
 *  the error on that 1.2 s threshold at a fifth of a second, which no one can
 *  see. */
const THINKING_TICK_MS = 200;

/** How long the UI may sit in `connecting` before giving up. Generous, because
 *  it spans the browser's own microphone prompt — which the student may take a
 *  while to answer — and must not cut a slow-but-working connection short.
 *  Matches ChatVoiceService's CONNECT_TIMEOUT_MS for the same reason. */
const CONNECT_TIMEOUT_MS = 30_000;

/* ============================================================================
   Echo suppression - half-duplex WITHOUT muting the microphone.

   THE BUG THIS EXISTS FOR. The student is usually on laptop SPEAKERS. Browser
   AEC is tuned for a locally-rendered loopback and is unreliable against a
   REMOTELY-rendered voice it never saw as a reference signal; AGC then boosts
   whatever residue survives. The model's own turn detection runs on the uplink,
   so the moment it hears the model's own voice it opens a turn and the
   interviewer answers itself. That is the self-talk loop, and no server-side
   knob can close it: turn detection sees one mono stream in which the
   model's own voice IS speech. Only two things discriminate - energy at the
   microphone, and duplex state - and both live on this side of the wire.

   THE TRADE-OFF, NAMED. Gating the uplink delays the moment server VAD can see
   the student, because the server cannot detect speech in audio we never sent.
   That cost is BARGE_IN_CONSECUTIVE_CHUNKS x CHUNK_MS = 200 ms. Against it, the
   design REMOVES a larger delay: today a barge-in costs uplink -> server VAD
   integration -> `reep.audio.flush` back down, i.e. a full round trip, typically
   250-450 ms. The gate detects speech LOCALLY in 200 ms and flushes the player
   itself. Net: barge-in is no slower than the round trip it sits inside, and
   the counters below are the instrument that proves it rather than the claim
   that asserts it.

   WHAT IS DELIBERATELY NOT DONE. The microphone is never muted, the track is
   never disabled, the worklet never stops. Gating capture would destroy the very
   energy signal the gate decides on - a muted microphone cannot detect the
   speech it is supposed to reopen for. Only sendAudio() is gated.
   ========================================================================== */

/** Master switch, and the default. ON, because the failure it prevents (an
 *  interviewer interviewing itself) is total. On earphones there is no acoustic
 *  path from the speaker back to the microphone, so the gate protects against
 *  nothing and only costs: a breath or an "mm" on a headset mic opened it and
 *  paused the interviewer. The room's Speaker / Earphones switch
 *  (shared/interview-room/audio-route.ts) turns it off through
 *  setEchoSuppression(); until 2026-09-22 nothing called that method at all. */
const ECHO_SUPPRESSION_DEFAULT = true;

/** AGC has a multi-hundred-millisecond release, so across a stretch of
 *  echo-only input it ramps gain UP and drags echo toward the level of speech,
 *  destroying the 15-25 dB separation this gate lives on - it would be actively
 *  fighting the fix. It is therefore off while suppression is armed, and the
 *  server VAD's own threshold (INTERVIEW_VAD_THRESHOLD) is the level control
 *  instead. echoCancellation and noiseSuppression STAY on: they help, they are
 *  simply not sufficient alone, which is the whole reason this gate exists. */
const ECHO_SUPPRESSION_DISABLES_AGC = true;

/* The THRESHOLD itself -- the margins, the coupling it multiplies, the
   consecutive-chunk run, the relax, the noise floor -- lives in
   ./echo-gate.ts, as pure classes with a spec. It used to be calibrated here
   from the first chunks after audio was SCHEDULED, which is 140-300 ms plus
   the device's output latency before it is AUDIBLE: calibration measured the
   silence before the voice, and the interviewer's own echo then opened the
   gate on nearly every question on speakers. That module's header is the
   record. What stays here is how the service acts on its verdict. */

/** Withheld chunks held while the gate is still deciding, and replayed the
 *  instant it opens. These are the chunks ALREADY above threshold while the gate
 *  was unconvinced, i.e. the first 80 ms of the student's sentence. Dropping them
 *  is not latency, it is LOST AUDIO: INTERVIEW_VAD_PREFIX_PADDING_MS is pulled
 *  from the UPSTREAM append buffer and cannot restore bytes that never left this
 *  process, so the transcriber hears "...ctually" and server VAD gets a weaker
 *  onset — which is also what delays the confirmation the whole hold depends on.
 *  Bounded at 2 x 1920 B, and it costs zero added latency. */
const BARGE_IN_PRIMER_CHUNKS = BARGE_IN_CONSECUTIVE_CHUNKS - 1;

/** Once genuine speech is detected, send unconditionally for this long. A spoken
 *  answer contains 150-300 ms inter-syllable gaps; without a hangover the gate
 *  would re-close inside the student's own sentence and chop it. */
const ECHO_GATE_HANGOVER_MS = 600;

/** After a LOCAL barge-in, interviewer audio still arriving is HELD, unplayed,
 *  until the relay confirms with `reep.audio.flush`. This is load-bearing and
 *  easy to miss: silencing the player alone is undone within one frame, because
 *  the relay keeps streaming and onMessage re-enqueues. The stream only stops at
 *  source when the model itself notices the interruption, which it can only do
 *  from the audio we have just resumed sending. 700 ms = one generous round trip
 *  plus the model's own endpointing plus integration.
 *
 *  Expiring unconfirmed means the detection was a false positive — echo, a
 *  chair, a cough — and the interviewer was mid-sentence. Everything held is
 *  then put back on the scheduler in order: the unplayed remainder of what was
 *  stopped, then every frame that arrived meanwhile. So the cost of a false
 *  positive is a pause of this length, and it is counted rather than silent.
 *
 *  It used to be DISCARDED, on the reasoning that the cost was "bounded at
 *  700 ms of skipped audio". That was true of a relay streaming near realtime
 *  and false of an engine that delivers a whole sentence in a burst: the flush
 *  destroyed everything already scheduled — typically the rest of the question
 *  — and the 700 ms of arrivals on top, so one false positive on laptop
 *  speakers was a question that vanished mid-word and never came back. */
const LOCAL_BARGE_IN_HOLD_MS = 700;

/** The relay's idle watchdog advances its last-audio clock ONLY on an inbound
 *  audio frame, and closes the session (4008) after INTERVIEW_IDLE_SECONDS.
 *  Gating the uplink therefore stops that clock. A ZEROED chunk every 10 s keeps
 *  it alive: digital silence provably cannot open a server-VAD turn, and 10 s is
 *  an order of magnitude inside the smallest sane idle cap. */
const ECHO_GATE_KEEPALIVE_MS = 10_000;

/** Control frame announcing which side of the duplex we are on. The relay
 *  COUNTS it and prints the totals in its end-of-interview line, which is how a
 *  support report says "the gate was shut for most of this session" without a
 *  browser console. It deliberately does NOT touch the upstream append buffer:
 *  clearing it at gate close would discard a barge-in the student had already
 *  begun but server VAD had not yet committed, which is the one thing this whole
 *  file exists to protect. It equally does not advance the relay's idle clock —
 *  a text frame that did would let a client hold a billing session open with no
 *  audio at all. Sent on TRANSITIONS ONLY, roughly twice per response. */
const GATE_CONTROL_TYPE = 'reep.mic.gate';

/** The session's audio counters, sent to the relay after every interviewer
 *  turn and once more on End, and written into its end-of-interview log line.
 *  Until 2026-09-22 they existed only as a console line on the student's own
 *  machine, so "the voice kept breaking" could be answered by nobody who had
 *  not been sitting at it. Numbers and one word, never audio or text: rule 1. */
const CLIENT_STATS_TYPE = 'reep.client.stats';

/** Output buffer size requested from the device. 'balanced', not
 *  'interactive': the smallest buffer is the one that crackles on a low-end
 *  phone while the orb is painting, and the conversational latency here is
 *  set by Nova's ~2 s endpointing, beside which a few tens of milliseconds of
 *  output buffer are inaudible. The echo gate reads the device's reported
 *  outputLatency, so it follows whatever buffer the device picks. */
const AUDIO_LATENCY_HINT: AudioContextLatencyCategory = 'balanced';

/** Shown while the device holds the AudioContext paused and a resume attempt
 *  has not brought it back. */
const AUDIO_PAUSED_NOTICE = 'Audio is paused by the device. Tap anywhere on the page to resume.';

/* ============================================================================
   Wire vocabulary.

   The relay decodes the model's audio server-side and sends it DOWNSTREAM AS
   BINARY, so there is no base64 audio frame to handle here. TWO SPELLINGS of
   each JSON name are accepted, and that is deliberate: they were the two OpenAI
   Realtime API generations this client was written against, the server now
   speaks Amazon Nova 2 Sonic, and the whole point of matching a SET is that a
   change of engine upstream can never silently mute the interviewer.
   ========================================================================== */

const AUDIO_DONE_TYPES: ReadonlySet<string> = new Set([
  'response.audio.done',
  'response.output_audio.done',
]);
const ASSISTANT_TRANSCRIPT_DELTA_TYPES: ReadonlySet<string> = new Set([
  'response.audio_transcript.delta',
  'response.output_audio_transcript.delta',
]);

export type NoticeTone = 'info' | 'warn' | 'error';

/**
 * Close codes the relay defines, mapped to wording a student can act on. The
 * relay's own reason string is clipped to 123 bytes by RFC 6455 and written for
 * an operator; these are written for the person in the chair.
 *
 * `detail: true` appends the relay's reason, because both cap figures are
 * configurable server-side (SESSION_MAX_SECONDS / IDLE_MAX_SECONDS) and the
 * relay puts the CONFIGURED figure in the reason. Hard-coding "15 minutes" here
 * told students they had hit a limit they had not.
 */
interface CloseMessage {
  readonly tone: NoticeTone;
  readonly text: string;
  readonly detail?: boolean;
}

const CLOSE_MESSAGES: ReadonlyMap<number, CloseMessage> = new Map<number, CloseMessage>([
  // 1000 CHANGED MEANING under the v3 turn protocol. The relay no longer waits
  // to be closed: at wrap-up it speaks its verdict, generates the scorecard,
  // sends `reep.report` and then closes 1000 ITSELF. So a 1000 arriving here is
  // normally the successful end of a complete interview, not a shrug.
  //
  // It is still not the ONLY 1000 — `request_stop(1000, "Conversation cleared")`
  // uses it too, and that interview has no report — so onClose() picks between
  // this text and two honest alternatives depending on whether `reep.report`
  // actually arrived. See reportCloseNotice(). Promising "your report is ready"
  // on a socket that never sent one would be the same class of lie the copy on
  // the consent panel was just fixed for.
  [1000, { tone: 'info', text: 'Interview complete — your report is ready.' }],
  [
    1006,
    {
      tone: 'error',
      text: 'The connection dropped. Check your network and start a new interview.',
    },
  ],
  // 1008 is the code FastAPI's own WebSocket handlers use for a policy refusal,
  // and the interview router reuses it for "no session" and "not a student".
  // Reachable only when the socket was ACCEPTED and then closed; a rejected
  // handshake reaches the browser as 1006 with no reason at all, which is
  // exactly why GET /api/interview/status is consulted first.
  //
  // `detail: true` — one of the very few places it is right for a code whose
  // reason names no configurable figure. 1008 now covers TWO refusals with
  // opposite remedies: "Mock interviews are a student feature." and "Your
  // student profile is incomplete; ask the placement cell." (a STUDENT session
  // with no `studentId`, refused at the socket so the NOT NULL on
  // interview_sessions.student_id never reaches the student as an opaque 1011).
  // The old wording asserted the first, which sent the second student to sign in
  // again — the one action that cannot fix a missing Student row — instead of to
  // the placement cell. Both reasons are complete sentences authored in
  // routers/interview.py, not echoes of anything upstream, so appending the
  // server's own is safe and is the only way this banner can be true twice.
  [
    1008,
    {
      tone: 'error',
      // The lead sentence carries a remedy of its own, because `detail` is only
      // appended when a reason actually arrives — a banner that says "no" and
      // stops is a dead end for the student holding it.
      text: 'This account cannot start a mock interview. Ask the placement cell if that looks wrong.',
      detail: true,
    },
  ],
  [
    1011,
    {
      tone: 'error',
      text: 'The interview service hit an internal error. Please start a new interview.',
    },
  ],
  // 1001 is what the relay sends when it is asked to stop and gets to close its
  // own sockets (_CLOSE_GOING_AWAY in app/interview_relay.py); 1012 is what
  // uvicorn sends when its teardown outruns that drain and it fails every live
  // WebSocket itself. BOTH mean the same thing to a student — a routine deploy —
  // and under uvicorn 1012 is in fact the usual one. Without these entries a
  // deploy was reported, mid-question, as "closed unexpectedly" in an error
  // state.
  [
    1001,
    {
      tone: 'warn',
      text: 'The interview server is restarting. Start a new interview in a moment — nothing you said was lost.',
    },
  ],
  [
    1012,
    {
      tone: 'warn',
      text: 'The interview server is restarting. Start a new interview in a moment — nothing you said was lost.',
    },
  ],
  [
    1013,
    {
      tone: 'warn',
      text: 'Too many interviews are running right now. Please try again in a few minutes.',
    },
  ],
  [
    4001,
    {
      tone: 'error',
      text: 'The interview service is not configured on the server (no model credential). Ask your administrator to set it up.',
    },
  ],
  [
    4002,
    {
      tone: 'error',
      text: 'The interview service is temporarily unavailable. Please try again shortly.',
    },
  ],
  // 4003: the interview router refused this page's Origin (it did not match the
  // server's WEB_ORIGIN). Always a deployment mistake and never something the
  // student can act on, so it must not read as a network blip they should retry.
  [
    4003,
    {
      tone: 'error',
      text: 'This page is not allowed to start an interview from its current address. Ask your administrator to check the server configuration.',
    },
  ],
  [
    4008,
    {
      tone: 'warn',
      text: 'No speech was heard for a while, so the session ended. Start a new interview when you are ready.',
      detail: true,
    },
  ],
  [
    4009,
    {
      tone: 'info',
      text: 'You reached the session time limit. Start a new interview to continue practising.',
      detail: true,
    },
  ],
  // 4010: the relay does not know the ?specialization= this bundle asked for. In
  // practice a CACHED client against a matrix row that was renamed or retired,
  // so the fix is a reload — not a retry, and not a support ticket. `detail`
  // because the relay names the offending key in its reason.
  [
    4010,
    {
      tone: 'warn',
      text: 'That interview track is no longer available. Reload the page and pick a track again.',
      detail: true,
    },
  ],
  // 4011: under v3 the RELAY creates every question, so a create that upstream
  // never acknowledges is an interview that can never continue — it stalls
  // silently rather than erroring. Deliberately not 4002: 4002 means "upstream
  // is unavailable, retry shortly", this means a working socket on which our
  // own sequencing came apart. The student needs to know their answers were
  // still recorded, because the interview stopping mid-question reads as loss.
  [
    4011,
    {
      tone: 'error',
      text: 'The interviewer stopped responding. Nothing you said was lost — start a new interview.',
    },
  ],
  // 4012: this student already holds a live interview on this worker — almost
  // always a second tab, or a first tab closed without the socket noticing yet.
  // NOT 1013: 1013 is "the server is full, everyone is affected", and telling a
  // student the service is overloaded when the other end of the problem is
  // their own tab sends them to support instead of to the tab.
  [
    4012,
    {
      tone: 'warn',
      text: 'You already have a mock interview open. Close the other tab and try again.',
    },
  ],
  // 4013/4014 are the consent pair. They cannot fire until the socket enforces
  // consent (spec step 10) — which is deliberately AFTER this client started
  // posting consent rows, or every existing student would have been locked out
  // on the deploy that turned enforcement on. Mapping them now is what makes
  // that later commit a one-line server change with no client edit: an unmapped
  // code falls through to "closed unexpectedly", which is the exact degradation
  // 4003 and 4010 were added to prevent.
  [
    4013,
    {
      tone: 'warn',
      text: 'Please accept the interview terms before starting.',
    },
  ],
  // 4014 SAID "You withdrew consent" UNTIL B6.1, AND THAT SENTENCE IS NOW FALSE.
  // A student cannot withdraw: `DELETE /api/interview/consent` is gone and the
  // two storage scopes are the college's. What reaches this code today is the
  // other half of the same gate — the acknowledgement the interview was pinned
  // to was replaced by one covering LESS, which is the college changing its
  // policy mid-call. Telling a student they did that themselves would send them
  // looking for a control that does not exist.
  [
    4014,
    {
      tone: 'info',
      text: "Your college's interview settings changed, so this interview ended. You can start a new one.",
    },
  ],
  // 4015: the volume cap — the server counted this student's interviews over the
  // last 24 h and refused BEFORE anything was billed or written. The sibling of
  // 4012 (concurrency), with a different sentence: "your other tab is open" is
  // fixable now, "you have done today's quota" is fixable tomorrow.
  // `warn`, not `error`: nothing is broken, and support cannot raise it.
  //
  // B6.4 MADE IT TWO CEILINGS and the server now sends the sentence itself —
  // the practice allowance and the spend ceiling are different things to be told
  // and only one of them is the student's own doing. This entry is the fallback
  // for a close frame that arrives without a reason.
  [
    4015,
    {
      tone: 'warn',
      // `detail: true` for the same reason the session-length codes carry it:
      // the figure and now the CEILING are both server-side. B6.4 made this two
      // walls — the practice allowance (completed interviews, the one a college
      // sets for its students) and the spend ceiling (every attempt, the one
      // that stops a reconnect loop) — and the server sends the sentence that
      // names which was hit. The lead below is true of both and stands alone
      // when a close frame arrives with no reason at all.
      text: "Your college limits how many mock interviews you can run in a day.",
      detail: true,
    },
  ],
]);

/* ============================================================================
   The AudioWorklet processor, as source.

   Inlined and loaded from a Blob URL so there is no second asset for the
   Angular build to copy, hash, cache-bust or 404. It runs on the audio
   rendering thread, where `sampleRate` is a global and nothing the main thread
   does can starve it. A ScriptProcessorNode would run this loop on the main
   thread behind layout and change detection, and every jank would be *dropped*
   microphone samples — a clipped word the model then mishears.
   ========================================================================== */

const WORKLET_SRC = `
const DEFAULT_TARGET_RATE = 24000;
const DEFAULT_CHUNK_FRAMES = 960;

/** RBJ-cookbook 2nd-order Butterworth low-pass, transposed direct form II.
 *  Only used when downsampling: decimating without it folds everything above
 *  the new Nyquist back into the speech band as aliasing, which a model's
 *  transcriber hears as consonant mush. */
class Biquad {
  constructor(fc, fs) {
    const w0 = (2 * Math.PI * fc) / fs;
    const cw = Math.cos(w0);
    const alpha = Math.sin(w0) / (2 * Math.SQRT1_2);
    const a0 = 1 + alpha;
    this.b0 = ((1 - cw) / 2) / a0;
    this.b1 = (1 - cw) / a0;
    this.b2 = this.b0;
    this.a1 = (-2 * cw) / a0;
    this.a2 = (1 - alpha) / a0;
    this.z1 = 0;
    this.z2 = 0;
  }
  processInPlace(buf) {
    let z1 = this.z1, z2 = this.z2;
    const b0 = this.b0, b1 = this.b1, b2 = this.b2, a1 = this.a1, a2 = this.a2;
    for (let i = 0; i < buf.length; i++) {
      const x = buf[i];
      const y = b0 * x + z1;
      z1 = b1 * x - a1 * y + z2;
      z2 = b2 * x - a2 * y;
      buf[i] = y;
    }
    this.z1 = z1;
    this.z2 = z2;
  }
}

class PcmRecorder extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.targetRate = o.targetRate || DEFAULT_TARGET_RATE;
    this.chunkFrames = o.chunkFrames || DEFAULT_CHUNK_FRAMES;

    // \`sampleRate\` is the AudioContext's ACTUAL rate. The constructor option is
    // a request, not a contract, so the resampling path must exist even though
    // it is dead code whenever the browser honoured 24 kHz.
    this.ratio = sampleRate / this.targetRate;
    this.needsResample = Math.abs(this.ratio - 1) > 1e-9;

    this.filters = [];
    if (this.ratio > 1) {
      const fc = 0.45 * this.targetRate;
      for (let i = 0; i < 3; i++) this.filters.push(new Biquad(fc, sampleRate));
    }

    this.pos = 1;   // fractional read cursor into [prev, ...input]
    this.prev = 0;  // last input sample of the previous render quantum
    this.scratch = null;

    this.acc = new Int16Array(this.chunkFrames);
    this.accLen = 0;
    this.running = true;

    this.port.onmessage = (e) => {
      if (e.data === 'stop') {
        this.emit();          // flush the partial chunk rather than lose it
        this.running = false; // process() returns false -> node is collectable
      }
    };
  }

  push(sample) {
    let s = sample;
    if (!(s === s)) s = 0;                 // NaN guard: one NaN would poison a chunk
    if (s > 1) s = 1; else if (s < -1) s = -1;
    // Clamp first, ALWAYS. Int16Array assignment wraps modulo 2^16, so an
    // unclamped 1.02 becomes -32114: a full-scale polarity inversion heard as a
    // loud click on every AGC transient. The 32768/32767 split is exact at both
    // rails (-1 -> -32768, +1 -> 32767) instead of overflowing the positive one.
    this.acc[this.accLen++] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
    if (this.accLen === this.chunkFrames) this.emit();
  }

  emit() {
    if (this.accLen === 0) return;
    const out = new Int16Array(this.accLen);
    out.set(this.acc.subarray(0, this.accLen));
    this.accLen = 0;
    // Transferred, not copied. The buffer is freshly allocated each time, so
    // nothing on this side can observe the detached buffer. \`t\` is the
    // context clock at capture: the echo gate asks the player's timeline what
    // was PLAYING then, which the main thread's own clock, read whenever the
    // message happens to be delivered, cannot answer.
    this.port.postMessage({ pcm: out.buffer, t: currentTime }, [out.buffer]);
  }

  process(inputs) {
    if (!this.running) return false;

    const ch = inputs[0] && inputs[0][0];
    // No input connected yet, or a muted track: stay alive, emit nothing.
    if (!ch || ch.length === 0) return true;

    const n = ch.length;

    if (!this.needsResample) {
      for (let i = 0; i < n; i++) this.push(ch[i]);
      return true;
    }

    let src = ch;
    if (this.filters.length) {
      if (!this.scratch || this.scratch.length !== n) this.scratch = new Float32Array(n);
      this.scratch.set(ch);
      for (let f = 0; f < this.filters.length; f++) this.filters[f].processInPlace(this.scratch);
      src = this.scratch;
    }

    // Linear interpolation over a virtual buffer E where E[0] is the previous
    // quantum's last sample and E[k] = src[k-1]. Carrying \`pos\` across quanta
    // is what keeps the stream drift-free at non-integer ratios (44100/24000).
    let p = this.pos;
    const r = this.ratio;
    while (p < n) {
      const i = p | 0;
      const t = p - i;
      const a = i === 0 ? this.prev : src[i - 1];
      const b = src[i];
      this.push(a + (b - a) * t);
      p += r;
    }
    this.pos = p - n;
    this.prev = src[n - 1];
    return true;
  }
}

registerProcessor('pcm-recorder', PcmRecorder);
`;

/* ============================================================================
   Public types.
   ========================================================================== */

/**
 * Explicit lifecycle of one interview. Driven by relay events, never inferred:
 *   idle        no session
 *   connecting  mic starting, socket opening, model not configured yet
 *   ready       the relay has configured the model; the first question is coming
 *   listening   the student's turn
 *   thinking    the student stopped; the model has not started replying
 *   speaking    the interviewer's audio is playing
 *   ended       the session closed
 *   error       the session failed to start or dropped unrecoverably
 */
export type InterviewState =
  | 'idle'
  | 'connecting'
  | 'ready'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'ended'
  | 'error';

export interface InterviewNotice {
  tone: NoticeTone;
  text: string;
}

/** One rendered transcript line. `key` is stable so a streaming turn updates in
 *  place instead of appending a near-duplicate on every delta. */
export interface InterviewLine {
  key: string;
  role: 'interviewer' | 'you' | 'session';
  text: string;
  /** Still streaming — the template renders a caret. */
  partial: boolean;
}

interface InterviewStatus {
  available: boolean;
  reason?: string;
}

/**
 * The practice scorecard, exactly as `reep.report` carries it.
 *
 * EVERY SCORE IS NULLABLE, AND A NULL IS NOT A ZERO. The relay refuses to
 * invent a score the model did not give (`_report_score` returns None rather
 * than defaulting), and `interview_evaluations` keeps the columns nullable for
 * the same reason: to a student reading this screen, "not scored" and "scored
 * zero" are opposite sentences. Anything rendering these MUST be able to draw a
 * blank — coercing with `?? 0` here would erase the distinction at the one
 * place it is still recoverable.
 */
export interface InterviewReport {
  overall: number | null;
  communication: number | null;
  domain: number | null;
  structure: number | null;
  strengths: string[];
  improvements: string[];
  drill: string;
  summary: string;
}

/**
 * The outcome of the scorecard step, available or not.
 *
 * `available: false` IS NOT A FAILED INTERVIEW, and no UI may present it as
 * one. The interview completed — the relay closes 1000 in every one of these
 * cases and deliberately defines no error code for a missing report, because a
 * close code would make a successful interview read as a failure. The bad news
 * travels here, in the payload, and `reason` names which of the four it was.
 */
export interface InterviewReportResult {
  available: boolean;
  /** null when available; otherwise 'unparseable' | 'timeout' | 'rejected'. */
  reason: string | null;
  report: InterviewReport | null;
}

/* ============================================================================
   PCM helpers.
   ========================================================================== */

/**
 * RMS of a PCM16 LE buffer, 0..1. DataView rather than Int16Array because the
 * incoming buffer may be unaligned, and because LE is then explicit rather than
 * inherited from the platform.
 */
function rmsOfPcm16(buf: ArrayBuffer): number {
  const dv = new DataView(buf);
  const n = buf.byteLength >> 1;
  if (n === 0) return 0;
  let sum = 0;
  for (let i = 0; i < n; i++) {
    const s = dv.getInt16(i * 2, true) / 32768;
    sum += s * s;
  }
  return Math.sqrt(sum / n);
}

/** RMS of a float time-domain window, 0..1. */
function rmsOfFloat(buf: Float32Array): number {
  if (buf.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

/* ============================================================================
   Microphone capture.
   ========================================================================== */

interface MicCaptureOptions {
  /**
   * One captured chunk, ready for the uplink.
   *
   * @param rms that chunk's RMS, 0..1. Passed alongside the samples rather than
   *        recomputed at the send site: it is measured exactly once, here, and
   *        the echo gate is the only reason the uplink needs it. Handing it over
   *        is what lets the gate cost zero extra DSP.
   * @param t the AudioContext time at which the worklet captured it.
   */
  onChunk: (pcm: ArrayBuffer, rms: number, t: number) => void;
  onLevel: (rms: number) => void;
  onError: (err: Error) => void;
}

class MicCapture {
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;
  private stopped = true;
  private starting: Promise<void> | null = null;

  /**
   * @param ctx shared with playback: one hardware clock, one resume, one close.
   *            The context is NOT owned here.
   */
  constructor(
    private readonly ctx: AudioContext,
    private readonly opts: MicCaptureOptions,
    /**
     * Whether this session runs with the echo gate armed. Read ONCE, at
     * getUserMedia time, because it decides the AGC constraint and re-negotiating
     * a live track to flip one boolean is a bigger risk than leaving the level
     * control where the session started. It gates NOTHING here: capture is
     * unconditional, and the uplink is the only thing the gate touches.
     */
    private readonly suppressEcho: boolean,
  ) {}

  /** Must be reached from a user gesture: getUserMedia and ctx.resume() both
   *  depend on one. Idempotent, and safe to interleave with stop(). */
  async start(): Promise<void> {
    if (this.starting) return this.starting;
    this.stopped = false;
    this.starting = this.doStart().finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  private async doStart(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      // Not a code bug and must not be reported as one: mediaDevices is
      // undefined on any http:// origin other than localhost, which is exactly
      // what happens when someone opens `ng serve` from a phone by IP.
      throw new Error('Microphone unavailable: open REEP over HTTPS, or from localhost.');
    }
    if (!this.ctx.audioWorklet) {
      throw new Error('This browser does not support AudioWorklet, which the interview needs.');
    }

    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Bare values, never `exact`: {sampleRate:{exact:24000}} throws
          // OverconstrainedError on most hardware and yields NO microphone at
          // all. As hints they are honoured where possible and ignored
          // otherwise, and the graph resamples either way.
          echoCancellation: true, // mandatory when the student is on speakers
          noiseSuppression: true, // hostel fan, keyboard, corridor noise
          // AGC is OFF while the echo gate is armed. Its multi-hundred-ms
          // release ramps gain up across an echo-only stretch and destroys the
          // 15-25 dB separation the gate discriminates on, so leaving it on
          // would be fighting the fix. With the gate off (headphones) there is
          // no echo to separate, so it is restored and a quiet student is
          // levelled for the server VAD, which is far more level-sensitive than
          // AGC-artefact-sensitive.
          autoGainControl: !(this.suppressEcho && ECHO_SUPPRESSION_DISABLES_AGC),
          channelCount: 1,
          sampleRate: SAMPLE_RATE,
        },
        video: false,
      });
      if (this.stopped) throw new DOMException('cancelled', 'AbortError');

      const blobUrl = URL.createObjectURL(
        new Blob([WORKLET_SRC], { type: 'application/javascript' }),
      );
      try {
        await this.ctx.audioWorklet.addModule(blobUrl);
      } finally {
        // Revoke as soon as the module is parsed, whether or not it parsed — an
        // un-revoked object URL lives as long as the document.
        URL.revokeObjectURL(blobUrl);
      }
      if (this.stopped) throw new DOMException('cancelled', 'AbortError');

      if (this.ctx.state !== 'running') await this.ctx.resume();
      if (this.stopped) throw new DOMException('cancelled', 'AbortError');

      const chunkFrames = Math.max(128, Math.round((CHUNK_MS * SAMPLE_RATE) / 1000));
      const node = new AudioWorkletNode(this.ctx, 'pcm-recorder', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        channelCount: 1,
        // `channelCount: 1` on getUserMedia is only a hint and a stereo capture
        // device may ignore it. 'explicit' here is what actually guarantees the
        // processor sees one channel instead of silently sending half a mic.
        channelCountMode: 'explicit',
        channelInterpretation: 'speakers',
        processorOptions: { targetRate: SAMPLE_RATE, chunkFrames },
      });

      node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; t: number }>) => {
        if (this.stopped) return;
        const { pcm, t } = e.data;
        // Measured ONCE. The meter and the echo gate are both downstream of this
        // single number; computing it twice would run the same loop over 960
        // samples 25 times a second for nothing.
        const rms = rmsOfPcm16(pcm);
        this.opts.onLevel(rms);
        this.opts.onChunk(pcm, rms, t);
      };
      node.onprocessorerror = () => {
        this.opts.onError(new Error('The audio processor stopped unexpectedly.'));
      };

      const source = this.ctx.createMediaStreamSource(stream);
      // A silent sink: some engines only pull a worklet that reaches the
      // destination. Gain 0 means the student never hears their own mic.
      const sink = this.ctx.createGain();
      sink.gain.value = 0;
      source.connect(node);
      node.connect(sink);
      sink.connect(this.ctx.destination);

      for (const track of stream.getAudioTracks()) {
        // Fires when the mic is unplugged or claimed exclusively by another
        // app. Without this the audio simply goes silent and looks like a bug
        // in the relay.
        track.onended = () => this.opts.onError(new Error('The microphone was disconnected.'));
      }

      this.stream = stream;
      this.source = source;
      this.node = node;
      this.sink = sink;
    } catch (err) {
      // Every failure path releases the microphone. Leaving it open would keep
      // the browser's recording indicator lit after a failed start, which users
      // reasonably read as spyware.
      if (stream) for (const t of stream.getTracks()) t.stop();
      throw err;
    }
  }

  /** Idempotent, and safe to call while start() is still in flight — the
   *  `stopped` checks after every await unwind that case. */
  stop(): void {
    if (this.stopped) return;
    this.stopped = true;

    const { node, source, sink, stream } = this;
    this.node = null;
    this.source = null;
    this.sink = null;
    this.stream = null;

    if (node) {
      node.port.onmessage = null;
      node.onprocessorerror = null;
      try {
        node.port.postMessage('stop');
        node.port.close();
      } catch {
        // The port is already closed (context torn down first). Nothing to do,
        // and nothing to report: the node is being discarded either way.
      }
      node.disconnect();
    }
    if (source) source.disconnect();
    if (sink) sink.disconnect();
    if (stream) {
      for (const t of stream.getTracks()) {
        t.onended = null;
        t.stop();
      }
    }
  }
}

/* ============================================================================
   Playback: a gapless scheduler on the AudioContext clock.
   ========================================================================== */

class PcmPlayer {
  /**
   * The PERSISTENT output bus.
   *
   * flush() swaps the per-response GainNode (see below), so nothing downstream
   * can hold a stable reference to `gain`. The analyser that drives the orb's
   * "interviewer speaking" swell needs exactly such a reference — and it must
   * tap what is actually AUDIBLE, not what has arrived — so every response gain
   * feeds this one node, and this one node feeds the destination.
   */
  readonly out: GainNode;

  private gain: GainNode;
  /**
   * Every buffer put on the clock, with its level: the echo gate's far-end
   * reference. It asks what was PLAYING at the moment a microphone chunk was
   * captured. The gate this replaced asked "is anything scheduled?", which is
   * true a jitter-buffer lead and the device's output latency before anything
   * is audible -- and it calibrated on exactly that silence.
   */
  readonly timeline = new FarEndTimeline();
  /** node -> its scheduled start time on the ctx clock, and the buffer it
   *  carries, so suspend() can hand back what has not sounded yet. */
  private readonly live = new Map<AudioBufferSourceNode, { startAt: number; buffer: AudioBuffer }>();
  private cursor = 0;
  private scheduledSec = 0;
  private remainder: Uint8Array | null = null;
  private closed = false;
  /** Times the scheduler fell behind the play cursor mid-response. Diagnostics
   *  only — but counted rather than swallowed, because "the interviewer sounds
   *  choppy" has no other observable cause on this side of the wire. */
  private underruns = 0;
  /**
   * The live jitter buffer, in seconds. Grows by PLAYBACK_LEAD_STEP_S on every
   * underrun and NEVER shrinks for the life of this player (one player = one
   * interview). A link that underran once will underrun again, and shrinking
   * back simply re-earns the same audible gap; the total cost of never shrinking
   * is bounded by PLAYBACK_LEAD_MAX_S. Measuring it beats guessing it, which is
   * what a single fixed constant was.
   */
  private lead = PLAYBACK_LEAD_MIN_S;

  /** @param ctx shared with capture; not owned here. */
  constructor(private readonly ctx: AudioContext) {
    this.out = ctx.createGain();
    this.out.connect(ctx.destination);
    this.gain = ctx.createGain();
    this.gain.connect(this.out);
  }

  /** @param pcm raw PCM16 LE mono @ 24 kHz */
  enqueue(pcm: ArrayBuffer | Uint8Array): void {
    const buffer = this.decode(pcm);
    if (buffer) this.schedule(buffer);
  }

  /**
   * Decode one frame WITHOUT scheduling it. The odd-byte carry is consumed and
   * refilled here exactly as it is for a frame that is scheduled at once, which
   * is what lets a frame held during a local barge-in be decoded on arrival —
   * in stream order, so the carry stays right — and scheduled later.
   */
  decode(pcm: ArrayBuffer | Uint8Array): AudioBuffer | null {
    if (this.closed) return null;
    return this.toAudioBuffer(pcm);
  }

  /** Put one decoded buffer on the clock, gaplessly after whatever is scheduled. */
  schedule(buffer: AudioBuffer): void {
    if (this.closed) return;
    const now = this.ctx.currentTime;
    if (this.cursor < now + 0.005) {
      // First buffer of a response, or we fell behind. Never schedule in the
      // past: start(t) with t < currentTime plays immediately AND truncates the
      // head of the buffer, so the student loses the first syllable and hears a
      // click. Re-arm the lead and count it instead.
      if (this.scheduledSec > 0) {
        this.underruns++;
        // Widen BEFORE re-arming, so the re-arm that answers this underrun
        // already carries the bigger margin rather than repeating the same
        // too-small guess and underrunning again one frame later.
        this.lead = Math.min(PLAYBACK_LEAD_MAX_S, this.lead + PLAYBACK_LEAD_STEP_S);
      }
      this.cursor = now + this.lead;
    }

    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.gain);
    const startAt = this.cursor;
    src.onended = () => {
      this.live.delete(src);
      src.onended = null;
      src.disconnect();
    };
    src.start(startAt);
    this.live.set(src, { startAt, buffer });
    this.timeline.record(startAt, startAt + buffer.duration, rmsOfFloat(buffer.getChannelData(0)));

    this.cursor += buffer.duration;
    this.scheduledSec += buffer.duration;
  }

  /**
   * A barge-in CONFIRMED by the relay, or teardown. The relay sends audio faster
   * than realtime, so at the moment the student starts speaking most of the
   * response is SCHEDULED but not played; setting gain to zero would leave those
   * nodes to talk over the student when gain came back. They have to be stopped
   * explicitly, and what they carried is gone for good. For the LOCAL, not yet
   * confirmed case see suspend().
   */
  flush(): void {
    this.stopAll();
  }

  /**
   * Barge-in detected LOCALLY, not yet confirmed. Stops exactly as flush() does
   * — the student is talking, and every millisecond still scheduled is audible
   * — but hands back what had not yet sounded, in scheduling order, so that an
   * unconfirmed detection can put it back rather than lose it. The node that
   * was sounding is cut at the instant of the call; the FLUSH_RAMP_S fade after
   * that instant therefore plays twice on a resume, which no ear can hear,
   * rather than once too few.
   */
  suspend(): AudioBuffer[] {
    const cut = this.ctx.currentTime;
    const held: AudioBuffer[] = [];
    for (const { startAt, buffer } of this.stopAll()) {
      if (startAt >= cut) {
        held.push(buffer);
        continue;
      }
      const played = Math.floor((cut - startAt) * SAMPLE_RATE);
      const remaining = buffer.length - played;
      if (remaining <= 0) continue;
      const rest = this.ctx.createBuffer(1, remaining, SAMPLE_RATE);
      rest.copyToChannel(buffer.getChannelData(0).subarray(played), 0);
      held.push(rest);
    }
    return held;
  }

  /**
   * Stop and retire every scheduled node, on the audio clock, and reset the
   * cursor. Returns what the nodes carried, in scheduling order; the caller
   * decides whether that audio is worth anything.
   */
  private stopAll(): Array<{ startAt: number; buffer: AudioBuffer }> {
    if (this.live.size === 0) {
      this.resetCursor();
      return [];
    }
    const t = this.ctx.currentTime;
    const stopAt = t + FLUSH_RAMP_S;
    // What is stopped will never sound, so it must stop counting as echo --
    // otherwise the gate would hold the student's own answer shut against an
    // interviewer who has already gone quiet.
    this.timeline.truncate(stopAt);

    // Fade the CURRENT gain node out on the audio clock, then swap in a fresh
    // one, so the next response starts at unity even if it begins during the
    // fade. Ramping the shared node and reusing it would mute the next turn.
    const dying = this.gain;
    dying.gain.cancelScheduledValues(t);
    dying.gain.setValueAtTime(dying.gain.value, t);
    dying.gain.linearRampToValueAtTime(0, stopAt);

    this.gain = this.ctx.createGain();
    this.gain.connect(this.out);

    const nodes = [...this.live.entries()];
    this.live.clear();
    let pending = nodes.length;
    const retire = () => {
      if (--pending <= 0) dying.disconnect();
    };

    for (const [node, { startAt }] of nodes) {
      node.onended = () => {
        node.onended = null;
        node.disconnect();
        retire();
      };
      try {
        // stopTime must be >= startTime. For a node whose start is still in the
        // future the two collapse to startAt, and per spec it then never
        // sounds — precisely the barge-in outcome we want.
        node.stop(Math.max(stopAt, startAt));
      } catch {
        // Already stopped or never started on this engine; retire it by hand so
        // the dying gain node is still released.
        node.onended = null;
        node.disconnect();
        retire();
      }
    }
    // Belt and braces: an engine that skips `ended` for a node that never
    // sounded would otherwise leak one GainNode per barge-in.
    setTimeout(() => {
      if (pending > 0) {
        pending = 0;
        dying.disconnect();
      }
    }, 1000);

    this.resetCursor();
    return nodes.map(([, entry]) => entry);
  }

  /**
   * The model finished an utterance normally: let the tail play out.
   *
   * The cursor is deliberately NOT zeroed. `response.audio.done` fires when the
   * last delta was SENT, and the relay streams faster than realtime, so that is
   * typically SECONDS before the tail finishes playing. Zeroing made the next
   * enqueue() take the re-arm branch and schedule audio at `now + lead` ON TOP of
   * a tail still scheduled against the old cursor — two voices at once — and it
   * silently suppressed the underrun counter for that re-arm, so the one
   * instrument for "the interviewer sounds choppy" read zero on the exact path
   * that caused it. enqueue()'s own `cursor < now` guard re-arms once the tail
   * has genuinely drained, which is the only moment re-arming is correct.
   *
   * Only the underrun accounting resets here, so that legitimate re-arm is not
   * miscounted as the scheduler falling behind.
   */
  endResponse(): void {
    this.scheduledSec = 0;
  }

  /**
   * A NEW response is about to start streaming: drop any odd-byte carry.
   *
   * `remainder` is the low byte of a sample whose high byte is the first byte of
   * the NEXT frame of the SAME stream. Across a response boundary there is no
   * next byte — the response was cancelled, or simply ended — so carrying it
   * would prepend one orphan byte to the new response and byte-misalign every
   * sample after it, which decodes as white noise. This is the one boundary at
   * which dropping it is right; resetCursor() is not (see there).
   */
  beginResponse(): void {
    this.remainder = null;
  }

  /** How many times this player fell behind. See `underruns`. */
  get underrunCount(): number {
    return this.underruns;
  }

  /** The live jitter buffer in seconds. Exposed so the session summary can say
   *  the buffer had to grow, which is the difference between "the network was
   *  bad" and "the audio code is wrong". */
  get leadSeconds(): number {
    return this.lead;
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.flush();
    this.timeline.clear();
    this.gain.disconnect();
    this.out.disconnect();
    this.remainder = null;
  }

  private resetCursor(): void {
    this.cursor = 0;
    this.scheduledSec = 0;
    // `remainder` is deliberately NOT dropped here. It is the low byte of a
    // sample whose high byte is the first byte of the next frame; dropping it
    // mid-stream byte-misaligns every sample after it and the rest of the
    // response decodes as white noise. After an UNCONFIRMED local barge-in the
    // SAME response keeps streaming straight through flush() -> resetCursor(),
    // so this path is live rather than theoretical. The cross-response case,
    // where the carry really must go, is beginResponse().
  }

  private toAudioBuffer(pcm: ArrayBuffer | Uint8Array): AudioBuffer | null {
    let bytes = pcm instanceof Uint8Array ? pcm : new Uint8Array(pcm);
    if (this.remainder?.length) {
      const joined = new Uint8Array(this.remainder.length + bytes.length);
      joined.set(this.remainder, 0);
      joined.set(bytes, this.remainder.length);
      bytes = joined;
      this.remainder = null;
    }
    const frames = bytes.length >> 1;
    // Nothing guarantees the relay frames on a sample boundary. An odd trailing
    // byte carried into the next chunk is one sample delayed; the same byte
    // dropped is a click and a permanent half-sample phase error.
    if (bytes.length & 1) this.remainder = bytes.slice(frames * 2);
    if (frames === 0) return null; // createBuffer(1, 0, rate) throws

    // 24 kHz is correct even if ctx.sampleRate is not: an AudioBufferSourceNode
    // resamples, and buffer.duration stays frames/24000, so all the scheduling
    // arithmetic above is independent of the hardware rate.
    const buffer = this.ctx.createBuffer(1, frames, SAMPLE_RATE);
    const channel = buffer.getChannelData(0);
    const dv = new DataView(bytes.buffer, bytes.byteOffset, frames * 2);
    for (let i = 0; i < frames; i++) {
      // Divide by 32768 for BOTH signs: -32768 maps to exactly -1.0 and 32767
      // to 0.99997, so decoding can never clip in the mixer.
      channel[i] = dv.getInt16(i * 2, true) / 32768;
    }
    return buffer;
  }
}

/* ============================================================================
   The service.
   ========================================================================== */

@Injectable({ providedIn: 'root' })
export class InterviewService {
  private readonly http = inject(HttpClient);

  // ------------------------------------------------------------------ //
  // Reactive surface                                                   //
  // ------------------------------------------------------------------ //

  private readonly _state = signal<InterviewState>('idle');
  /** Explicit lifecycle of the live interview. */
  readonly state = this._state.asReadonly();

  private readonly _detail = signal<string | null>(null);
  /** Overrides the state's default label when there is something more precise
   *  to say ("Preparing the interviewer…"). */
  readonly detail = this._detail.asReadonly();

  private readonly _notice = signal<InterviewNotice | null>(null);
  /** A dismissible banner: why the session ended, or what is degrading it. */
  readonly notice = this._notice.asReadonly();

  private readonly _lines = signal<InterviewLine[]>([]);
  /** The live transcript of THIS session. The durable copy is written by the
   *  server into the shared conversation and re-read from /api/agent/history. */
  readonly lines = this._lines.asReadonly();

  private readonly _userRms = signal(0);
  /**
   * Raw microphone RMS, 0..1, published every 40 ms chunk.
   *
   * Deliberately RAW, not an already-mapped 0..1 "amplitude": the perceptual dB
   * curve lives on MockAudioStreamController.mapRmsToAmplitude in
   * shared/voice-visualizer.ts, which documents it and is its single source of
   * truth. Duplicating the formula here would give the orb two definitions of
   * loud that could drift apart.
   */
  readonly userRms = this._userRms.asReadonly();

  private readonly _aiRms = signal(0);
  /** Interviewer RMS, 0..1, measured at 20 Hz on the PLAYBACK bus so it tracks
   *  what is audible rather than what has arrived. Same mapping note as above. */
  readonly aiRms = this._aiRms.asReadonly();

  private readonly _micLevel = signal(0);
  /** VU-ballistic 0..1 for the mic meter bar (throttled; see METER_MIN_INTERVAL_MS). */
  readonly micLevel = this._micLevel.asReadonly();

  private readonly _echoSuppression = signal(ECHO_SUPPRESSION_DEFAULT);
  /**
   * Whether the half-duplex echo gate is armed.
   *
   * On laptop SPEAKERS it is what stops the interviewer interviewing itself. On
   * HEADPHONES there is no acoustic path from the speaker back to the
   * microphone, so it protects against nothing and costs 200 ms of barge-in
   * latency - which is the entire reason it is a switch and not a constant.
   */
  readonly echoSuppression = this._echoSuppression.asReadonly();

  private readonly _echoGateOpen = signal(true);
  /**
   * False while the uplink is suppressed because the interviewer is audible.
   *
   * This is NEVER a muted microphone: capture, resampling, the RMS measurement
   * and the meter all keep running at full rate while it is false - that is what
   * lets the gate hear the student well enough to reopen. A template that
   * renders this must say "not sending", never "muted", or it describes the
   * wrong thing to the person in the chair.
   */
  readonly echoGateOpen = this._echoGateOpen.asReadonly();

  private readonly _suppressedFrames = signal(0);
  /**
   * Chunks captured but deliberately not sent, this session.
   *
   * Read beside the barge-in counts in the end-of-session line, this is the
   * diagnosis: a high count with few UNCONFIRMED local barge-ins is the gate
   * working as designed; a high count with many is a threshold set too low,
   * firing on echo. One number cannot distinguish those, which is why the
   * summary prints both.
   */
  readonly suppressedFrames = this._suppressedFrames.asReadonly();

  /**
   * The microphone is live but the uplink is held. The one derivation a template
   * actually wants: the "listening" affordance can stay lit, because capture
   * really is running, while an unobtrusive marker explains why the interviewer
   * is not reacting yet.
   */
  readonly uplinkSuppressed = computed(() => this._echoSuppression() && !this._echoGateOpen());

  private readonly _specialization = signal<string | null>(null);
  /**
   * The matrix row the RELAY confirmed for this interview, as its label, or null
   * for the generic interview. Deliberately server-reported rather than echoed
   * from the local selection: the relay refuses a key it does not know (close
   * 4010), so echoing the request back would name a track that never ran.
   */
  readonly specialization = this._specialization.asReadonly();

  private readonly _phase = signal<string | null>(null);
  /** The state machine's current phase KEY, exactly as app/interview_matrix.py
   *  names it (`opening`, `probing`, `deep_dive`, `wrap_up`, `ended`). The
   *  component owns the wording, so a phase added server-side degrades to its
   *  raw key rather than to a blank pill. */
  readonly phase = this._phase.asReadonly();

  private readonly _playbackLeadMs = signal(Math.round(PLAYBACK_LEAD_MIN_S * 1000));
  /**
   * The live jitter buffer, in ms. Starts at PLAYBACK_LEAD_MIN_S and grows with
   * every underrun. Read beside `underruns` it is the whole answer to "why did
   * the voice break up": a lead still at its minimum with zero underruns means
   * the network was never the problem.
   */
  readonly playbackLeadMs = this._playbackLeadMs.asReadonly();

  private readonly _underruns = signal(0);
  /** Times the playback scheduler fell behind, this session. Each one is an
   *  audible gap and click — the "breaking, flickering" symptom, counted. */
  readonly underruns = this._underruns.asReadonly();

  private readonly _elapsedSeconds = signal(0);
  readonly elapsedSeconds = this._elapsedSeconds.asReadonly();

  private readonly _sessionMaxSeconds = signal(DEFAULT_SESSION_MAX_S);
  readonly sessionMaxSeconds = this._sessionMaxSeconds.asReadonly();

  private readonly _completedSessions = signal(0);
  /** Bumped once per finished session. The component watches it to re-read the
   *  persisted conversation, which is where interview turns actually live. */
  readonly completedSessions = this._completedSessions.asReadonly();

  private readonly _report = signal<InterviewReportResult | null>(null);
  /**
   * This session's practice scorecard, once `reep.report` has arrived.
   *
   * Null means the report step has not happened — a session still running, or
   * one that ended before wrap-up (the cap, a disconnect). It is NOT cleared by
   * teardown(): the report arrives immediately BEFORE the relay closes 1000, so
   * clearing it on close would erase the payload the interview existed to
   * produce, a few milliseconds after it landed. It is cleared at start(), when
   * a new interview genuinely supersedes it.
   *
   * The durable copy is `interview_evaluations`, readable at
   * GET /api/interview/sessions/{id}/report. This signal is what the student
   * sees the moment the interview ends — and, if that one row failed to write,
   * it is the only place the scorecard exists at all.
   */
  readonly report = this._report.asReadonly();

  private readonly _composingReport = signal(false);
  /**
   * The interview is over and the scorecard is being written.
   *
   * This exists because of a silence v3 creates that nothing else covers. At
   * WRAP_UP the relay speaks its verdict and then issues ONE more
   * `response.create` for the scorecard — text-only, so no audio arrives, and
   * the relay deliberately forwards neither its `response.created` nor its
   * `response.done` (the browser renders `response.created` as "the interviewer
   * is speaking", which would be a twenty-second silent "speaking"). The last
   * thing this client sees is the verdict's `response.done`, which used to send
   * it straight back to `listening` — telling the student to go ahead and speak
   * for up to twenty seconds, during which §5.5 has the relay deliberately
   * IGNORING their voice so a "thanks, bye" cannot destroy the scorecard.
   *
   * So the client names the wait instead. Learned from `reep.phase` reaching
   * `wrap_up`, which is the only signal available and is enough: it is pushed
   * ahead of the verdict's create.
   */
  readonly composingReport = this._composingReport.asReadonly();

  private readonly _thinkingSlow = signal(false);
  /**
   * The student has been waiting long enough in `thinking` that the UI must
   * show it is working. See THINKING_AFFORDANCE_AFTER_MS.
   *
   * One-way within a turn: it flips true once and is cleared only when the
   * state leaves `thinking`. A flag that flickered would be worse than none.
   */
  readonly thinkingSlow = this._thinkingSlow.asReadonly();

  private readonly _thinkingSeconds = signal(0);
  /** Whole seconds elapsed in the current `thinking`. Rendered beside the
   *  affordance so the wait is a number the student can watch move, rather than
   *  an animation that could equally be a hung page. */
  readonly thinkingSeconds = this._thinkingSeconds.asReadonly();

  /** True from the moment Start is pressed until the socket is fully closed. */
  readonly active = computed(() => {
    const s = this._state();
    return s !== 'idle' && s !== 'ended' && s !== 'error';
  });

  /** mm:ss elapsed. */
  readonly clockLabel = computed(() => formatClock(this._elapsedSeconds()));
  /** mm:ss cap, as reported by the relay. */
  readonly capLabel = computed(() => formatClock(this._sessionMaxSeconds()));
  /** Within two minutes of the cap — warn before it cuts the student off. */
  readonly clockWarning = computed(
    () => this._elapsedSeconds() >= this._sessionMaxSeconds() - SESSION_WARN_LEAD_S,
  );

  /**
   * getUserMedia and AudioWorklet both require a secure context. Read once: it
   * is a deployment fact, not a runtime condition, and the single most common
   * "the mic is broken" report is `ng serve` opened from a phone by IP.
   */
  readonly secureContext = window.isSecureContext;

  // ------------------------------------------------------------------ //
  // Session-scoped state                                               //
  // ------------------------------------------------------------------ //

  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private mic: MicCapture | null = null;
  private player: PcmPlayer | null = null;
  private aiAnalyser: AnalyserNode | null = null;
  private readonly aiWindow = new Float32Array(AI_ANALYSER_FFT);

  private clockTimer: ReturnType<typeof setInterval> | null = null;
  private aiLevelTimer: ReturnType<typeof setInterval> | null = null;
  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private thinkingTimer: ReturnType<typeof setInterval> | null = null;
  private thinkingStartedAt = 0;
  private pageHideHandler: (() => void) | null = null;
  /** Resumes a device-paused AudioContext on return to the tab or on a tap. */
  private audioResumeHandler: (() => void) | null = null;

  /**
   * Identifies the current start attempt. Bumped by teardown() and by each new
   * start(), so an in-flight start can tell it has been superseded — the same
   * discipline ChatVoiceService.startVoiceSession uses, and for the same reason:
   * every `await` below is a point at which the student may have pressed End,
   * and without it the superseded attempt goes on to open a socket and a
   * microphone that nothing holds a handle to.
   */
  private sessionGen = 0;

  private startedAt = 0;
  /** Audio is dropped until the relay says the model is configured. Queueing it
   *  instead would be an unbounded buffer full of a student's voice, and the
   *  relay drops pre-ready frames anyway. */
  private ready = false;
  private ending = false;
  private droppedChunks = 0;

  /** The matrix key requested for THIS session. A browser WebSocket cannot set
   *  headers, so the query string is the only channel to the relay; held on the
   *  instance because socketUrl() runs after start()'s awaits. */
  private requestedSpecialization: string | null = null;

  // ---- Echo gate, session-scoped ---------------------------------------- //
  // All of it is plain fields rather than signals: it is read and written 25
  // times a second on the uplink path, and only the two figures a human would
  // look at (gate state, suppressed count) are mirrored into signals.

  /** The double-talk detector. Owns the threshold, the learned echo coupling
   *  and the noise floor; see ./echo-gate.ts. One per service, reset per
   *  session, because the coupling is a fact about this room and this volume
   *  that the NEXT response should not have to re-learn. */
  private readonly echoGate = new EchoGate();
  /** Send unconditionally until this instant — the student is mid-sentence. */
  private gateHangoverUntil = 0;
  /** Hold arriving interviewer audio until this instant, or until the relay
   *  confirms the barge-in. See LOCAL_BARGE_IN_HOLD_MS. */
  private localBargeInHoldUntil = 0;
  /** What is being held: the unplayed remainder of what suspend() stopped, then
   *  every frame that arrived while holding, in order. Dropped on confirmation,
   *  put back on the scheduler on expiry. */
  private heldPlayback: AudioBuffer[] = [];
  /** Expires the hold even when no further frame arrives to trip the check in
   *  onMessage — the relay may already have sent the whole sentence, which on a
   *  burst-delivering engine is the usual case. */
  private holdTimer: ReturnType<typeof setTimeout> | null = null;
  /** performance.now() of the last byte actually put on the wire, real or
   *  keepalive. Drives ECHO_GATE_KEEPALIVE_MS. */
  private lastUplinkAt = 0;
  /** The gate state last announced to the relay, so the control frame is sent on
   *  transitions only. `null` = nothing announced yet this session. */
  private gateSignalled: boolean | null = null;
  /** One reusable zeroed chunk for the keepalive: allocating the same silence 25
   *  times a second would be pure GC pressure on the audio path. */
  private silence: ArrayBuffer | null = null;
  /**
   * Above-threshold chunks held while the gate is still deciding, replayed in
   * order the instant it opens. See BARGE_IN_PRIMER_CHUNKS. Cleared whenever the
   * run of hot chunks breaks, so it can never carry audio from an earlier,
   * abandoned candidate barge-in into a later one.
   *
   * Holding these buffers is safe: WORKLET_SRC allocates a fresh Int16Array per
   * emit() and TRANSFERS it, so nothing on the audio thread can observe or reuse
   * one after it arrives here.
   */
  private readonly primer: ArrayBuffer[] = [];

  // Counters. Plain numbers — nothing renders them per frame; they are read once
  // at the end of the session, which is the only moment they mean anything.
  /** Chunks the gate withheld. Mirrored into `suppressedFrames`. */
  private suppressedChunks = 0;
  /** Zeroed chunks sent purely to keep the relay's idle watchdog alive. */
  private keepaliveChunks = 0;
  /** Times local energy opened the gate and suspended the player. */
  private localBargeIns = 0;
  /** Of those, the ones the relay then agreed with. localBargeIns minus this is
   *  the false-positive count, and the number that tunes ECHO_GATE_MARGIN. */
  private confirmedBargeIns = 0;
  /** Interviewer frames held inside a local barge-in hold window — replayed if
   *  the relay never confirmed, dropped if it did. */
  private heldPlaybackFrames = 0;
  /** Chunks the gate has seen at all — counted whether or not suppression is
   *  armed, so `mode=off` in the summary is a reachable, meaningful line rather
   *  than a field that could only ever print `on`. Zero means no session ran,
   *  which is how teardown() knows not to log a summary for an interview that
   *  never started. */
  private gateChunks = 0;
  /** Withheld onset chunks actually replayed on gate open. The instrument for
   *  the primer: zero here alongside a non-zero localBargeIns means the replay
   *  is not firing and the student's word onsets are being lost again. */
  private replayedPrimerChunks = 0;
  /** performance.now() of the last diagnostic signal write. See
   *  DIAGNOSTIC_PUBLISH_MS. */
  private lastDiagnosticPublish = 0;

  /** Meter ballistics — owned here so a level left over from a previous
   *  interview cannot bleed into the next one's first frame. */
  private meterLevel = 0;
  private meterLastUpdate = 0;
  private meterLastPaint = 0;

  /** Streaming interviewer transcript, keyed by response id, plus a counter for
   *  system lines (which carry no upstream id but still need a stable key). */
  private readonly assistantText = new Map<string, string>();
  /** Streaming STUDENT transcript, keyed by upstream item id. Same revise-in-
   *  place pattern as assistantText: `reep.transcript.delta` events grow one
   *  pending "You" line, and the final `.completed` replaces it. Empty on a
   *  surface that emits no deltas (beta), where the completed-only behaviour
   *  below is the whole story, exactly as before deltas were forwarded. */
  private readonly studentText = new Map<string, string>();
  private systemLineSeq = 0;

  // ------------------------------------------------------------------ //
  // Lifecycle                                                          //
  // ------------------------------------------------------------------ //

  /**
   * Open one interview. MUST be called from a user gesture — getUserMedia and
   * AudioContext.resume() both require one.
   *
   * Resolves once the socket has been opened, or once the attempt has failed;
   * the interview itself then proceeds over the signals above. It never throws:
   * every failure lands in `state === 'error'` with a `notice` the student can
   * act on, which is the only terminal state Start is offered from again.
   */
  async start(specialization: string | null = null): Promise<void> {
    if (this.active()) return;

    if (!this.secureContext) {
      this.fail(
        'REEP must be open over HTTPS (or from localhost) before the microphone can be used.',
      );
      return;
    }

    // Fresh session — clear residue from a prior attempt. teardown() bumps
    // sessionGen, so this MUST come before the generation is captured.
    this.teardown();
    const gen = ++this.sessionGen;
    const cancelled = () => gen !== this.sessionGen;

    this._notice.set(null);
    this._lines.set([]);
    this._state.set('connecting');
    this._detail.set(null);
    // The ONLY place the previous interview's scorecard is discarded. teardown()
    // deliberately leaves it alone — see the field's own note.
    this._report.set(null);
    this._composingReport.set(false);
    this._elapsedSeconds.set(0);
    this._sessionMaxSeconds.set(DEFAULT_SESSION_MAX_S);
    // Requested here, CONFIRMED by the relay in reep.ready. Both signals stay
    // null until it answers, so the UI can never name a track the server refused.
    this.requestedSpecialization = specialization;
    this._specialization.set(null);
    this._phase.set(null);
    this._playbackLeadMs.set(Math.round(PLAYBACK_LEAD_MIN_S * 1000));
    this._underruns.set(0);
    this.droppedChunks = 0;
    this.ready = false;
    this.ending = false;
    this.resetEchoGate();
    this.assistantText.clear();
    this.studentText.clear();

    // Release the microphone if the tab closes or the browser navigates away.
    // Nothing else covers that path — ngOnDestroy does not run on tab close —
    // and without it the recording indicator stays lit on a page that is gone.
    // pagehide, NOT visibilitychange: tabbing away mid-interview is normal.
    this.pageHideHandler = () => this.end('Page closed');
    window.addEventListener('pagehide', this.pageHideHandler);

    // Nothing below may hang forever. A wedged uvicorn is a documented failure
    // mode on this platform (AGENTS.md), and it used to park the UI on
    // "Connecting…" with no way out but a page reload.
    this.connectTimer = setTimeout(() => {
      if (cancelled()) return;
      if (this._state() === 'connecting') {
        this.fail('The interview took too long to connect. Press Start to try again.');
      }
    }, CONNECT_TIMEOUT_MS);

    // 1) Readiness. A rejected WebSocket handshake reaches the browser as a bare
    //    1006 with no code and no reason, so this HTTP probe is the ONLY place
    //    the student can be told *why* — not configured, not signed in, not a
    //    student. An unreachable or unrecognised probe is treated as "unknown"
    //    and the socket is attempted anyway: refusing to start because a
    //    diagnostic endpoint 404'd would be a worse failure than the one it
    //    exists to report.
    try {
      const status = await firstValueFrom(
        this.http.get<InterviewStatus>(`${environment.apiBase}/interview/status`, {
          withCredentials: true,
        }),
      );
      if (cancelled()) return;
      if (status?.available === false) {
        this.fail(status.reason ?? 'Mock interviews are not available right now.');
        return;
      }
    } catch {
      /* probe unavailable — fall through and let the socket speak for itself */
    }
    if (cancelled()) return;

    // 2) Audio graph. ONE AudioContext for capture and playback: one hardware
    //    clock (so the playback scheduler and the mic agree on time), one resume
    //    from the click, one close on teardown. Browsers also cap concurrent
    //    contexts, and a leak shows up as a constructor failure around the
    //    seventh interview of a session.
    let ctx: AudioContext;
    try {
      ctx = new AudioContext({ sampleRate: SAMPLE_RATE, latencyHint: AUDIO_LATENCY_HINT });
    } catch {
      // Some engines throw NotSupportedError for a non-native rate; others
      // silently hand back the hardware rate. Only ctx.sampleRate is the truth,
      // and the worklet resamples from whatever it turns out to be.
      ctx = new AudioContext({ latencyHint: AUDIO_LATENCY_HINT });
    }
    this.ctx = ctx;
    ctx.onstatechange = () => {
      if (this.ctx !== ctx || this.ending) return;
      if (ctx.state === 'running') {
        if (this._notice()?.text === AUDIO_PAUSED_NOTICE) this._notice.set(null);
        return;
      }
      // 'suspended' (a locked screen, a backgrounded tab, a notification) or
      // iOS's 'interrupted' (a call, Siri). The context's CLOCK stops, so what
      // is scheduled is not lost: it waits, and plays on from where it was
      // when the context resumes. This used to flush the player here, which
      // threw the rest of the question away -- and nothing ever called
      // resume(), so the interview stayed silent after the student came back.
      void this.resumeAudio();
    };
    this.audioResumeHandler = () => {
      if (document.visibilityState === 'visible') void this.resumeAudio();
    };
    document.addEventListener('visibilitychange', this.audioResumeHandler);
    // A tap is the user gesture some engines insist on before resume().
    document.addEventListener('pointerdown', this.audioResumeHandler, true);

    const player = new PcmPlayer(ctx);
    this.player = player;
    const analyser = ctx.createAnalyser();
    analyser.fftSize = AI_ANALYSER_FFT;
    player.out.connect(analyser);
    this.aiAnalyser = analyser;

    // Held in a LOCAL as well as on `this`. A concurrent teardown() nulls the
    // field, and the cancellation branch below would then dereference null on
    // the one path it exists to clean up. MicCapture.stop() is idempotent, so
    // stopping through the local is safe even when teardown already stopped it.
    const mic = new MicCapture(
      ctx,
      {
        onChunk: (pcm, rms, t) => this.sendAudio(pcm, rms, t),
        onLevel: (rms) => this.pushLevel(rms),
        onError: (err) => this.fail(err.message),
      },
      // Fixed for the life of this session: the AGC constraint is negotiated
      // once, at getUserMedia time. Toggling suppression mid-interview still
      // takes effect on the GATE immediately (it is read per chunk); only the
      // microphone's own level control waits for the next Start.
      this._echoSuppression(),
    );
    this.mic = mic;

    try {
      await mic.start();
    } catch (err) {
      mic.stop();
      if (cancelled()) return;
      if (err instanceof DOMException && err.name === 'AbortError') return;
      this.fail(describeMicError(err));
      return;
    }
    if (cancelled()) {
      // The permission prompt can sit open a long time; the student may have
      // pressed End before granting it. teardown() has already run and dropped
      // its reference, so release what this superseded attempt just built.
      mic.stop();
      return;
    }

    // 3) Socket. The cookie rides it because /api is same-origin — through the
    //    dev proxy in development (proxy.conf.json, "ws": true) and by
    //    deployment in production.
    this.startClock();
    this.startAiLevelSampling();
    this.openSocket();
  }

  /** Student pressed End, or the page is going away. Always closes cleanly. */
  end(reason = 'Ended by student'): void {
    // Nothing live and nothing to close: return WITHOUT writing a terminal
    // state. Otherwise merely navigating away from an idle screen would stamp
    // "Interview ended.", bump completedSessions, and make the component re-read
    // a conversation that has not changed.
    if (!this.active() && this.ws === null) return;
    this.ending = true;
    this.ready = false;
    // Before the socket is released: the relay logs the last copy it received.
    this.sendClientStats();

    const ws = this.ws;
    this.ws = null;
    this.teardown();

    if (ws) {
      // Detach BEFORE closing: onClose must not run for a close we asked for,
      // or the student gets a banner for their own button press.
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        try {
          // 1000 with a short reason: the relay logs it, persists what it has,
          // and closes its own upstream socket in the same teardown.
          ws.close(1000, reason);
        } catch {
          // Closing a socket still in CONNECTING throws on some engines; the
          // handlers are already detached, so it is collected either way.
        }
      }
    }

    this._state.set('ended');
    this._detail.set(null);
    this.writeLine(this.nextSystemKey(), 'session', 'Interview ended.', false);
    this._completedSessions.update((n) => n + 1);
  }

  /** Dismiss the banner without touching the session. */
  dismissNotice(): void {
    this._notice.set(null);
  }

  // ------------------------------------------------------------------ //
  // Uplink                                                             //
  // ------------------------------------------------------------------ //

  /**
   * @param pcm one 40 ms chunk, PCM16 LE mono @ 24 kHz
   * @param rms that chunk's RMS, 0..1, measured once in MicCapture
   * @param t the AudioContext time the worklet captured it at
   */
  private sendAudio(pcm: ArrayBuffer, rms: number, t: number): void {
    const ws = this.ws;
    if (!ws || !this.ready || ws.readyState !== WebSocket.OPEN) return;

    // Half-duplex FIRST, backpressure second: a chunk the gate withholds must
    // not also be counted as a chunk the network dropped, or the two diagnostics
    // become one indistinguishable number.
    if (!this.gateAllows(pcm, rms, t, ws)) return;

    if (ws.bufferedAmount > MAX_UPLINK_BUFFERED_BYTES) {
      // The uplink cannot keep up. Buffering more only makes the interview more
      // stale; it never makes this audio arrive on time. Bounded and counted —
      // the only place in this file that discards audio.
      this.droppedChunks++;
      if (this.droppedChunks === 1) {
        this._notice.set({
          tone: 'warn',
          text: 'Your connection is struggling, so some audio is being skipped.',
        });
      }
      return;
    }
    // Binary only. The relay's base64 uplink exists to survive an intermediary
    // that mangles binary frames; REEP's proxy does not, and halving the
    // accepted-input surface halves the bounds-checking that has to be right.
    ws.send(pcm);
    // Real audio is what the relay's idle watchdog is actually waiting for, so
    // it resets the keepalive interval too - otherwise the first suppressed
    // chunk after a long answer would fire a pointless silent frame.
    this.lastUplinkAt = performance.now();
  }

  // ------------------------------------------------------------------ //
  // Echo gate                                                          //
  // ------------------------------------------------------------------ //

  /**
   * Arm or disarm half-duplex echo suppression.
   *
   * Takes effect on the GATE immediately - it is read from the signal on every
   * chunk - so a student who plugs headphones in mid-interview stops paying the
   * 200 ms and gets full-duplex sending back at once. The microphone's own AGC
   * constraint is negotiated at getUserMedia time and therefore waits for the
   * next Start; re-negotiating a live track to flip one boolean is a bigger risk
   * than the mismatch it would fix.
   *
   * Turning it OFF re-opens the gate and clears any hold, so no state from the
   * suppressed period can strand the uplink shut.
   */
  setEchoSuppression(on: boolean): void {
    if (this._echoSuppression() === on) return;
    this._echoSuppression.set(on);
    if (!on) {
      this.echoGate.bargedIn();
      // Full duplex from here; whatever the gate was holding is the interviewer
      // mid-sentence, so it is played, not stranded.
      this.releaseHold();
      // Withheld audio from the suppressed period is stale the moment full
      // duplex resumes; replaying it later would inject an old syllable into the
      // middle of a live sentence.
      this.primer.length = 0;
      this.setGate(true);
    }
  }

  /**
   * Decide whether ONE captured chunk goes upstream. Capture never stops; this
   * is the only thing the gate touches.
   *
   * The verdict is EchoGate's (./echo-gate.ts): the chunk is compared with the
   * loudest playback that can be arriving in it -- read off the player's
   * timeline at the chunk's own capture time, reaching back by the device's
   * output latency -- scaled by the echo coupling learned for this room. What
   * this method owns is acting on the verdict: the primer, the hangover, the
   * keepalive and the gate announcement.
   */
  private gateAllows(pcm: ArrayBuffer, rms: number, t: number, ws: WebSocket): boolean {
    // Counted BEFORE the early-out, so a headphone session (suppression off)
    // still logs a summary and `mode=off` is a line that can actually appear.
    this.gateChunks++;
    if (!this._echoSuppression()) return true;

    const now = performance.now();
    // Inside a hangover opened by a detected barge-in: the student is
    // mid-sentence, and re-closing on an inter-syllable gap would chop it.
    if (now < this.gateHangoverUntil) {
      this.setGate(true);
      return true;
    }

    const span = echoWindow(t, this.outputLatency());
    const farRef = this.player?.timeline.peak(span.from, span.to) ?? 0;

    switch (this.echoGate.judge(rms, farRef)) {
      case 'send':
        // Nothing audible can be reaching the microphone: full duplex.
        this.primer.length = 0;
        this.setGate(true);
        return true;

      case 'bargeIn':
        this.openGateForBargeIn(now);
        // The head of the sentence goes out BEFORE the chunk that proved it, so
        // the uplink carries one contiguous waveform.
        this.flushPrimer(ws);
        return true;

      case 'hold':
        // Not yet convinced: HOLD the samples rather than destroy them, and no
        // keepalive, because the next chunk may be real audio.
        this.setGate(false);
        if (this.primer.length >= BARGE_IN_PRIMER_CHUNKS) this.primer.shift();
        this.primer.push(pcm);
        this.suppress();
        return false;

      case 'suppress':
        // Echo, or silence under echo. The run broke, so whatever was held was
        // echo after all, and replaying echo upstream is the exact loop this
        // gate exists to break.
        this.setGate(false);
        this.primer.length = 0;
        this.suppress();
        this.keepAlive(ws, now);
        return false;
    }
  }

  /** The device's own report of how long scheduled audio takes to reach the
   *  speaker. `outputLatency` where the engine has it (Chrome, Firefox),
   *  `baseLatency` otherwise; echoWindow() bounds whatever comes back. */
  private outputLatency(): number {
    const ctx = this.ctx;
    if (!ctx) return 0;
    return ctx.outputLatency || ctx.baseLatency || 0;
  }

  /**
   * Replay the withheld head of a barge-in, oldest first.
   *
   * Order is the whole point: PCM appended out of order is a click and a garbled
   * first word. Skipped entirely under backpressure - stale audio queued behind a
   * full send buffer helps nobody, and the ordinary drop counter already covers
   * that case - and cleared either way, so nothing can be replayed twice.
   */
  private flushPrimer(ws: WebSocket): void {
    if (this.primer.length === 0) return;
    if (ws.bufferedAmount <= MAX_UPLINK_BUFFERED_BYTES) {
      for (const chunk of this.primer) {
        ws.send(chunk);
        this.replayedPrimerChunks++;
      }
      this.lastUplinkAt = performance.now();
    } else {
      this.droppedChunks += this.primer.length;
    }
    this.primer.length = 0;
  }

  /**
   * Local barge-in: BARGE_IN_CONSECUTIVE_CHUNKS in a row well above the echo
   * the playback predicts.
   *
   * Suspend FIRST - the student is already talking over the interviewer and
   * every millisecond still scheduled is audible - then hold arriving audio
   * until the relay confirms. Silencing alone is not enough: the relay keeps
   * streaming and onMessage re-enqueues within one frame. Only the model
   * noticing the interruption stops it at source, and that cannot happen until
   * the audio this method unblocks has reached it.
   *
   * SUSPEND, NOT FLUSH. Nothing on this side can tell a real barge-in from echo
   * or a cough; the relay's confirmation is the only arbiter, and on this
   * engine it arrives only when the model itself hears the student. So what is
   * stopped here is kept, and a hold that expires unconfirmed puts it back —
   * see LOCAL_BARGE_IN_HOLD_MS for what discarding it used to cost.
   */
  private openGateForBargeIn(now: number): void {
    this.gateHangoverUntil = now + ECHO_GATE_HANGOVER_MS;
    this.localBargeInHoldUntil = now + LOCAL_BARGE_IN_HOLD_MS;
    this.localBargeIns++;
    this.setGate(true);
    // suspend() also truncates the player's timeline, so what it stopped stops
    // counting as echo the moment it stops sounding.
    this.heldPlayback = this.player?.suspend() ?? [];
    this.armHoldTimer();
    if (this._state() === 'speaking') this.setState('listening');
  }

  /**
   * The relay agreed: whatever opened the gate was real speech, and the response
   * has been cancelled at source. Stop holding, and drop what was held — it is
   * the audio the student talked over, and the (now finite) stream ends
   * naturally without it.
   */
  private confirmBargeIn(): void {
    if (this.localBargeInHoldUntil === 0) return;
    this.confirmedBargeIns++;
    this.localBargeInHoldUntil = 0;
    this.heldPlayback = [];
    this.clearHoldTimer();
  }

  /**
   * The hold expired with no confirmation: the detection was a false positive
   * and the interviewer was still mid-sentence. Put everything held back on the
   * scheduler, in order, so the sentence resumes where it stopped instead of
   * the rest of it being lost. It stays counted as an unconfirmed local
   * barge-in in the summary line, which is the number that tunes
   * ECHO_GATE_MARGIN. Reached from the timer, from the next arriving frame
   * (whichever is first) and from suppression being switched off mid-hold;
   * idempotent, so the order does not matter.
   */
  private releaseHold(): void {
    this.clearHoldTimer();
    if (this.localBargeInHoldUntil === 0) return;
    this.localBargeInHoldUntil = 0;
    const held = this.heldPlayback;
    this.heldPlayback = [];
    const player = this.player;
    if (!player) return;
    for (const buffer of held) player.schedule(buffer);
  }

  private armHoldTimer(): void {
    this.clearHoldTimer();
    this.holdTimer = setTimeout(() => this.releaseHold(), LOCAL_BARGE_IN_HOLD_MS);
  }

  private clearHoldTimer(): void {
    if (this.holdTimer !== null) {
      clearTimeout(this.holdTimer);
      this.holdTimer = null;
    }
  }

  /** Gate state, mirrored to the signal and announced to the relay - on
   *  transitions only, never per frame. */
  private setGate(open: boolean): void {
    if (this._echoGateOpen() === open && this.gateSignalled === open) return;
    this._echoGateOpen.set(open);
    if (this.gateSignalled === open) return;
    this.gateSignalled = open;
    const ws = this.ws;
    if (!ws || !this.ready || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({ type: GATE_CONTROL_TYPE, state: open ? 'open' : 'suppressed' }));
    } catch {
      // A control frame is advisory: the relay ignores an event it does not
      // know, and the client-side gate is correct without it. Never let the
      // announcement break the interview it is describing.
    }
  }

  /** One withheld chunk. The signal is published on the DIAGNOSTIC_PUBLISH_MS
   *  cadence, not per chunk: this runs 25 times a second and every set() is a
   *  change-detection pass for a number no eye can read at that rate. The
   *  authoritative total is the plain field, and teardown() reads it directly. */
  private suppress(): void {
    this.suppressedChunks++;
    const now = performance.now();
    if (now - this.lastDiagnosticPublish < DIAGNOSTIC_PUBLISH_MS) return;
    this.lastDiagnosticPublish = now;
    this._suppressedFrames.set(this.suppressedChunks);
  }

  /**
   * Keep the relay's idle watchdog alive while the uplink is held. Its last-audio
   * clock advances only on an inbound audio frame, so an interviewer monologue
   * longer than INTERVIEW_IDLE_SECONDS would otherwise be closed 4008 mid-answer.
   * ZEROS, never the captured echo: digital silence provably cannot open a
   * server-VAD turn, whereas forwarding the echo would re-create the very loop
   * this gate exists to break.
   */
  private keepAlive(ws: WebSocket, now: number): void {
    if (now - this.lastUplinkAt < ECHO_GATE_KEEPALIVE_MS) return;
    if (ws.bufferedAmount > MAX_UPLINK_BUFFERED_BYTES) return;
    this.silence ??= new ArrayBuffer(Math.round((CHUNK_MS * SAMPLE_RATE) / 1000) * 2);
    try {
      ws.send(this.silence);
    } catch {
      return;
    }
    this.keepaliveChunks++;
    this.lastUplinkAt = now;
  }

  /** Every gate field back to first-run values. State from a previous interview
   *  must not bleed into the next one's first frame - the same reason the meter
   *  ballistics are reset in teardown(). */
  private resetEchoGate(): void {
    this.echoGate.reset();
    this.gateHangoverUntil = 0;
    this.localBargeInHoldUntil = 0;
    this.clearHoldTimer();
    this.heldPlayback = [];
    // NOT zero: a fresh session has just sent nothing, and `now - 0` is already
    // past the keepalive interval on any page open longer than ten seconds,
    // which would fire a pointless silent chunk before the first real one.
    this.lastUplinkAt = performance.now();
    this.gateSignalled = null;
    this.primer.length = 0;
    this.suppressedChunks = 0;
    this.keepaliveChunks = 0;
    this.localBargeIns = 0;
    this.confirmedBargeIns = 0;
    this.heldPlaybackFrames = 0;
    this.gateChunks = 0;
    this.replayedPrimerChunks = 0;
    this.lastDiagnosticPublish = 0;
    this._suppressedFrames.set(0);
    this._echoGateOpen.set(true);
  }

  /**
   * One line, once, at the end of one session - so a support report can say WHY
   * the audio was poor instead of guessing.
   *
   *   gate on/off        was suppression armed at all
   *   suppressed/judged  how much uplink was withheld, out of how much was seen
   *   keepalive          silent chunks sent to hold the relay's idle watchdog
   *   bargeIn n/m        relay-confirmed / locally detected. n far below m means
   *                      the threshold is too low and the gate is firing on echo
   *                      or room noise: raise ECHO_GATE_MARGIN. n at m with a
   *                      large m is simply a talkative student.
   *   heldFrames         interviewer frames held during a hold (replayed when
   *                      unconfirmed). Large with a poor confirm rate is the
   *                      same diagnosis.
   *   replay             onset chunks replayed on gate open. Zero with a
   *                      non-zero bargeIn denominator means the primer is not
   *                      firing and word onsets are being lost.
   *   coupling/floor     the two measurements the threshold was built from:
   *                      echo level relative to the playback (the prior, 2.0,
   *                      means it never measured any echo) and the room.
   *   lead/underruns     the ADAPTIVE jitter buffer, and how often playback fell
   *                      behind. A lead still at its floor with zero underruns
   *                      means the network was never the reason the voice broke
   *                      up; a lead at PLAYBACK_LEAD_MAX_S means it certainly
   *                      was, and buffering could not save it.
   */
  private logEchoGateSummary(): void {
    const unconfirmed = this.localBargeIns - this.confirmedBargeIns;
    console.info(
      '[interview] echo gate: ' +
        `mode=${this._echoSuppression() ? 'on' : 'off'} ` +
        `suppressed=${this.suppressedChunks}/${this.gateChunks} ` +
        `keepalive=${this.keepaliveChunks} ` +
        `bargeIn=${this.confirmedBargeIns}/${this.localBargeIns} (unconfirmed=${unconfirmed}) ` +
        `heldFrames=${this.heldPlaybackFrames} replay=${this.replayedPrimerChunks} ` +
        `dropped=${this.droppedChunks} ` +
        `coupling=${this.echoGate.coupling.toFixed(3)} floor=${this.echoGate.noiseFloor.toFixed(4)} ` +
        `lead=${this._playbackLeadMs()}ms underruns=${this._underruns()}`,
    );
  }

  // ------------------------------------------------------------------ //
  // Socket                                                             //
  // ------------------------------------------------------------------ //

  private socketUrl(): string {
    // Built from environment.apiBase, never hard-coded: a path outside /api
    // loses both the dev proxy and — being cross-origin — the reep_session
    // cookie this handshake authenticates with.
    const url = new URL(`${environment.apiBase}/interview`, location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    // A browser WebSocket cannot set headers, so the query string is the only
    // channel. The relay reads it off query_params and closes 4010 on a key it
    // does not know, so an OMITTED key is the generic interview and a WRONG key
    // is loud — never a silent downgrade to a persona the student did not pick.
    if (this.requestedSpecialization) {
      url.searchParams.set('specialization', this.requestedSpecialization);
    }
    return url.href;
  }

  private openSocket(): void {
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.socketUrl());
    } catch {
      // Only a malformed URL reaches here; a refused connection arrives later as
      // a close event, not as a throw.
      this.fail('The interview address is invalid. Reload the page and try again.');
      return;
    }
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    ws.onopen = () => {
      // Still "connecting" for the student: the socket is open but the model is
      // not configured until the relay sends reep.ready, and speaking before
      // then would be audio nobody hears.
      this._detail.set('Preparing the interviewer…');
    };
    ws.onmessage = (event: MessageEvent) => this.onMessage(event);
    ws.onerror = () => {
      // The Error event carries no detail by design (it would leak cross-origin
      // information). The close event that follows carries the code, so the
      // message is written there rather than duplicated here.
    };
    ws.onclose = (event: CloseEvent) => this.onClose(event);
  }

  private onMessage(event: MessageEvent): void {
    // Binary frame = raw PCM from the relay, already decoded server-side so the
    // browser does not base64-decode 48 kB/s.
    if (event.data instanceof ArrayBuffer) {
      // A local barge-in has suspended the player and is waiting for the relay
      // to cancel the response at source. Everything arriving in that window
      // is HELD, not played: playing it would undo the suspend within one
      // frame, and discarding it — as this used to — lost the rest of the
      // question on every false positive. Bounded by LOCAL_BARGE_IN_HOLD_MS
      // and counted. Decoded now rather than at release, because the odd-byte
      // carry has to be consumed in arrival order.
      if (this.localBargeInHoldUntil > 0) {
        if (performance.now() < this.localBargeInHoldUntil) {
          const buffer = this.player?.decode(event.data);
          if (buffer) this.heldPlayback.push(buffer);
          this.heldPlaybackFrames++;
          return;
        }
        // Expired unconfirmed: the detection was a false positive. Put the held
        // audio back first, so this frame lands after it, and let the summary's
        // bargeIn ratio say so.
        this.releaseHold();
      }
      this.player?.enqueue(event.data);
      if (this._state() !== 'speaking') this.setState('speaking');
      return;
    }
    if (typeof event.data !== 'string') return;

    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(event.data) as Record<string, unknown>;
    } catch {
      // A non-JSON text frame is a relay bug, not something the student can act
      // on. Ignoring it keeps the interview running.
      return;
    }
    const type = str(msg['type']);
    if (!type) return;

    if (AUDIO_DONE_TYPES.has(type)) {
      // Let the tail play out — endResponse only drops the scheduling cursor.
      this.player?.endResponse();
      return;
    }

    if (ASSISTANT_TRANSCRIPT_DELTA_TYPES.has(type)) {
      const key = str(msg['response_id']) ?? str(msg['item_id']) ?? 'current';
      const delta = str(msg['delta']);
      if (!delta) return;
      const next = (this.assistantText.get(key) ?? '') + delta;
      this.assistantText.set(key, next);
      this.writeLine(`a:${key}`, 'interviewer', next, true);
      return;
    }

    switch (type) {
      case 'reep.ready': {
        this.ready = true;
        this.clearConnectTimer();
        // The relay NESTS these under `limits`. Reading them flat silently left
        // the clock on its built-in 900 s default, so a relay configured for ten
        // minutes counted past a cap it never displayed and cut the student off
        // with no two-minute warning.
        const cap = num(obj(msg['limits'])?.['session_max_seconds']);
        if (cap !== null && cap > 0) this._sessionMaxSeconds.set(cap);
        // Server-reported, alongside `limits`, for the same reason: the relay is
        // the authority on what this session is ACTUALLY running. It answers
        // null for the generic interview, which is exactly what the picker's
        // `key: null` row means.
        this._specialization.set(str(msg['specialization']));
        this._phase.set(str(msg['phase']));
        this.setState('ready');
        this.writeLine(
          this.nextSystemKey(),
          'session',
          'Interview started. Speak naturally — you may interrupt.',
          false,
        );
        break;
      }

      case 'reep.phase': {
        // Pushed between turns when the server-side state machine advances. The
        // label is resent with it so a UI that missed reep.ready still names the
        // track rather than showing a phase with no interview attached to it.
        this._phase.set(str(msg['phase']));
        const label = str(msg['specialization']);
        if (label) this._specialization.set(label);
        break;
      }

      case 'input_audio_buffer.speech_started':
      case 'reep.audio.flush': {
        // Barge-in. Flush FIRST: the student is already talking over the
        // interviewer and every millisecond of unflushed queue is audible.
        this.player?.flush();
        // The relay agrees. If this flush is answering a barge-in the gate
        // detected locally, stop holding: the response is cancelled at source,
        // so what arrives next is the end of a finite stream, not a voice
        // talking over the student.
        this.confirmBargeIn();
        if (type === 'input_audio_buffer.speech_started') this.setState('listening');
        break;
      }

      case 'input_audio_buffer.speech_stopped':
        this.setState('thinking');
        break;

      case 'response.created':
        // A new PCM stream begins here, so any odd-byte carry left over from the
        // previous one must go: it has no next byte, and prepending it would
        // byte-misalign this whole response into white noise.
        this.player?.beginResponse();
        this.setState('thinking');
        break;

      case 'response.done': {
        this.sendClientStats();
        // Finalise every partial line so a completed answer never keeps a caret.
        // The relay does not forward response.audio_transcript.done, so this
        // sweep is the ONLY thing that de-carets an interviewer turn.
        for (const [key, text] of this.assistantText) {
          this.writeLine(`a:${key}`, 'interviewer', text, false);
        }
        this.assistantText.clear();
        if (this.ending) break;
        if (this._phase() === 'wrap_up') {
          // The verdict has finished playing and the relay's very next act is
          // the silent scorecard. Saying "go ahead, the interviewer is
          // listening" here would invite the student to talk into a response
          // that is deliberately deaf to them — see `composingReport`.
          this._composingReport.set(true);
          this.setState('thinking');
          this._detail.set('Writing your report…');
        } else {
          this.setState('listening');
        }
        break;
      }

      case 'reep.transcript.delta': {
        // The student's own speech, transcribing live. The relay aliases the
        // upstream delta to this reep.* name; it is NEVER persisted server-side
        // — the `.completed` event below remains the only point a student turn
        // is recorded — so this line is a preview and stays marked partial.
        const key = str(msg['item_id']);
        const delta = str(msg['delta']);
        if (!key || !delta) break;
        const next = (this.studentText.get(key) ?? '') + delta;
        this.studentText.set(key, next);
        this.writeLine(`u:${key}`, 'you', next, true);
        break;
      }

      case 'conversation.item.input_audio_transcription.completed': {
        // The FINAL transcript. Where deltas arrived it replaces the pending
        // line keyed by the same item id; where they did not (the beta surface
        // may not emit them) this is the first time the student line appears,
        // which is exactly the pre-delta behaviour.
        const key = str(msg['item_id']) ?? 'you';
        const text = str(msg['transcript']);
        const hadPending = this.studentText.delete(key);
        if (text) this.writeLine(`u:${key}`, 'you', text, false);
        else if (hadPending) this.removeLine(`u:${key}`);
        break;
      }

      case 'reep.report': {
        // The last thing the relay sends before it closes 1000. Everything the
        // student came for is in this one frame, so it is read defensively: a
        // field that arrives in an unexpected shape must cost that field, never
        // the scorecard. Same posture as the relay's own parse of the model.
        const result = readReport(msg);
        this._report.set(result);
        this._composingReport.set(false);
        this.writeLine(
          this.nextSystemKey(),
          'session',
          result.available
            ? 'Interview complete. Your practice report is below.'
            : 'Interview complete. The practice report could not be generated, but your transcript is saved.',
          false,
        );
        break;
      }

      case 'reep.error': {
        // The relay sends {scope, code, param} and deliberately NO message, so
        // an upstream error string can never echo request content back into the
        // page. Report the code — an operator can look it up — and keep the
        // interview running, because most turn-level errors recover.
        const code = str(msg['code']);
        this._notice.set({
          tone: 'warn',
          text: code
            ? `The interviewer hit a problem with that turn (${code}). Keep going — it usually recovers.`
            : 'The interviewer hit a problem with that turn. Keep going — it usually recovers.',
        });
        break;
      }

      default:
        // Everything else in the Realtime vocabulary (item lifecycle, rate
        // limits, content parts) is tolerated, not handled. Silence is correct.
        break;
    }
  }

  private onClose(event: CloseEvent): void {
    // Captured BEFORE the reset: a session that never reached `reep.ready`
    // produced no turns, so there is nothing new for the component to re-read.
    // Bumping regardless meant every failed connect (a 1006 against a wedged
    // server, a refused origin) sent the assistant screen back to
    // GET /api/agent/history for a session that wrote nothing.
    const produced = this.ready;
    this.ready = false;
    this.ws = null;
    this.ending = true;
    this.teardown();

    const known = CLOSE_MESSAGES.get(event.code);
    if (known) {
      // The relay fills the close reason with the CONFIGURED figure (e.g. "No
      // audio received for 2 minutes"). Appending it beats hard-coding a number
      // here that every deployment is free to change.
      const detail = known.detail && event.reason.trim() ? ` (${event.reason.trim()})` : '';
      const override = event.code === 1000 ? this.reportCloseNotice() : null;
      this._notice.set(override ?? { tone: known.tone, text: known.text + detail });
      this._state.set(known.tone === 'error' ? 'error' : 'ended');
    } else {
      const reason = event.reason ? ` (${event.reason})` : '';
      this._notice.set({
        tone: 'error',
        text: `The interview connection closed unexpectedly${reason}. Start a new interview to continue.`,
      });
      this._state.set('error');
    }
    this._detail.set(null);
    if (produced) this._completedSessions.update((n) => n + 1);
  }

  // ------------------------------------------------------------------ //
  // Internals                                                          //
  // ------------------------------------------------------------------ //

  private setState(next: InterviewState): void {
    // The thinking clock is armed HERE rather than at the one event that starts
    // the wait, because two events lead into `thinking` (speech_stopped, and
    // response.created once the transcript has landed) and under v3 they are
    // seconds apart. Arming on the state is what makes the clock measure the
    // silence the STUDENT experiences instead of one leg of it.
    if (next === 'thinking') this.armThinkingClock();
    else this.disarmThinkingClock();
    this._state.set(next);
    this._detail.set(null);
  }

  /**
   * Start timing the current wait — or leave a running clock alone.
   *
   * NOT restarted when we are already thinking. `response.created` arrives
   * mid-wait under v3 (the relay creates the question only after the transcript
   * resolves), and resetting the counter there would hide exactly the delay this
   * affordance exists to make honest: the student would watch it climb to two
   * seconds, snap back to zero and climb again.
   */
  private armThinkingClock(): void {
    if (this.thinkingTimer !== null) return;
    this.thinkingStartedAt = performance.now();
    this._thinkingSlow.set(false);
    this._thinkingSeconds.set(0);
    this.thinkingTimer = setInterval(() => {
      const ms = performance.now() - this.thinkingStartedAt;
      if (ms >= THINKING_AFFORDANCE_AFTER_MS && !this._thinkingSlow()) {
        this._thinkingSlow.set(true);
      }
      // Written only when the RENDERED second changes: at THINKING_TICK_MS this
      // is four no-ops for every write, and a signal write is a change-detection
      // pass over a screen that is also painting an orb at 60 fps.
      const secs = Math.floor(ms / 1000);
      if (secs !== this._thinkingSeconds()) this._thinkingSeconds.set(secs);
    }, THINKING_TICK_MS);
  }

  private disarmThinkingClock(): void {
    if (this.thinkingTimer !== null) {
      clearInterval(this.thinkingTimer);
      this.thinkingTimer = null;
    }
    if (this._thinkingSlow()) this._thinkingSlow.set(false);
    if (this._thinkingSeconds() !== 0) this._thinkingSeconds.set(0);
  }

  /**
   * What a close code 1000 should actually say, when the map's wording would be
   * a claim rather than a fact. Null = use the map.
   *
   * CLOSE_MESSAGES[1000] reads "your report is ready" because that is what a
   * completed v3 interview means, and it is the sentence the student should see
   * for it. But 1000 covers two other shapes:
   *
   *  - `reep.report {available:false}` — the interview completed and only the
   *    SCORECARD failed. The relay closes 1000 on purpose there, and defines no
   *    error code for it, precisely so a successful interview does not read as a
   *    failure. Saying "your report is ready" would then be false in the one
   *    banner the student reads.
   *  - No report at all, e.g. `request_stop(1000, "Conversation cleared")`. The
   *    old, honest wording still fits: it ended, nothing claims a report exists.
   */
  private reportCloseNotice(): InterviewNotice | null {
    const result = this._report();
    if (result === null) return { tone: 'info', text: 'Interview ended.' };
    if (result.available) return null;
    const why =
      result.reason === 'timeout'
        ? 'the model took too long'
        : result.reason === 'rejected'
          ? 'the model refused the request'
          : 'the model’s answer could not be read';
    return {
      tone: 'warn',
      text: `Interview complete. The practice report could not be generated (${why}) — your answers and transcript are still saved.`,
    };
  }

  /** An unrecoverable local problem (secure context, mic, worklet, readiness). */
  private fail(message: string): void {
    this.ending = true;
    const ws = this.ws;
    this.ws = null;
    this.teardown();
    if (ws) {
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      try {
        ws.close(1000, 'Client error');
      } catch {
        /* already closing — the handlers are detached either way */
      }
    }
    this._notice.set({ tone: 'error', text: message });
    this._state.set('error');
    this._detail.set(null);
  }

  /**
   * Release every audio resource and timer. Idempotent, safe from synchronous
   * error paths, and deliberately does NOT set a terminal state — the caller
   * owns that, exactly as ChatVoiceService.teardown does.
   */
  private teardown(): void {
    // Invalidate any start attempt still in flight, so it cannot resurrect the
    // session after this teardown has run.
    this.sessionGen++;

    // Exactly once per interview that actually ran: teardown() is reached from
    // end(), onClose(), fail() AND the top of start(), and gateChunks is reset
    // immediately below, so a session that judged no audio logs nothing. The
    // final poll happens FIRST because the player is disposed further down and
    // the lead it grew to is half of what the summary line is for.
    this.publishPlaybackStats();
    this._suppressedFrames.set(this.suppressedChunks);
    if (this.gateChunks > 0) this.logEchoGateSummary();
    this.resetEchoGate();

    if (this.pageHideHandler) {
      window.removeEventListener('pagehide', this.pageHideHandler);
      this.pageHideHandler = null;
    }
    if (this.audioResumeHandler) {
      document.removeEventListener('visibilitychange', this.audioResumeHandler);
      document.removeEventListener('pointerdown', this.audioResumeHandler, true);
      this.audioResumeHandler = null;
    }
    this.clearConnectTimer();
    // Cleared here as well as in setState, because onClose() and fail() write
    // `_state` DIRECTLY rather than through setState — a socket that dropped
    // while the student was waiting would otherwise leave the interval running
    // for the life of the tab, ticking a counter under a dead session.
    this.disarmThinkingClock();
    if (this.clockTimer !== null) {
      clearInterval(this.clockTimer);
      this.clockTimer = null;
    }
    if (this.aiLevelTimer !== null) {
      clearInterval(this.aiLevelTimer);
      this.aiLevelTimer = null;
    }
    if (this.mic) {
      this.mic.stop();
      this.mic = null;
    }
    if (this.player) {
      this.player.close();
      this.player = null;
    }
    if (this.aiAnalyser) {
      this.aiAnalyser.disconnect();
      this.aiAnalyser = null;
    }
    if (this.ctx) {
      const ctx = this.ctx;
      this.ctx = null;
      ctx.onstatechange = null;
      // Fire-and-forget: close() is async but nothing after this depends on it,
      // and an engine that rejects a double close must not surface in the
      // student's face.
      void ctx.close().catch(() => undefined);
    }
    this.assistantText.clear();
    this.studentText.clear();
    this.meterLevel = 0;
    this.meterLastUpdate = 0;
    this.meterLastPaint = 0;
    this._micLevel.set(0);
    // Zero both amplitudes so the orb settles to rest rather than freezing
    // mid-ripple on the last value it was given.
    this._userRms.set(0);
    this._aiRms.set(0);
  }

  private clearConnectTimer(): void {
    if (this.connectTimer !== null) {
      clearTimeout(this.connectTimer);
      this.connectTimer = null;
    }
  }

  private startClock(): void {
    this.startedAt = Date.now();
    this._elapsedSeconds.set(0);
    this.clockTimer = setInterval(() => {
      this._elapsedSeconds.set(Math.floor((Date.now() - this.startedAt) / 1000));
    }, 1000);
  }

  private startAiLevelSampling(): void {
    this.aiLevelTimer = setInterval(() => {
      const analyser = this.aiAnalyser;
      if (!analyser) return;
      analyser.getFloatTimeDomainData(this.aiWindow);
      this._aiRms.set(rmsOfFloat(this.aiWindow));
      this.publishPlaybackStats();
    }, AI_LEVEL_INTERVAL_MS);
  }

  /**
   * Bring a device-paused AudioContext back. Tried at once, again when the tab
   * becomes visible and again on the next tap, because engines differ on which
   * of those they accept. The notice stays up only while it is still paused.
   */
  private async resumeAudio(): Promise<void> {
    const ctx = this.ctx;
    if (!ctx || this.ending || ctx.state === 'running' || ctx.state === 'closed') return;
    try {
      await ctx.resume();
    } catch {
      // Needs a gesture, or the OS still holds the device (a call in progress).
    }
    if (this.ctx !== ctx || this.ending) return;
    // Re-read after the await; TypeScript still holds the narrowing from above.
    if ((ctx.state as string) !== 'running') {
      this._notice.set({ tone: 'warn', text: AUDIO_PAUSED_NOTICE });
    }
  }

  /** See CLIENT_STATS_TYPE. Best-effort: a stats frame must never break the
   *  interview it is describing. */
  private sendClientStats(): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN || this.gateChunks === 0) return;
    const player = this.player;
    try {
      ws.send(
        JSON.stringify({
          type: CLIENT_STATS_TYPE,
          route: this._echoSuppression() ? 'speaker' : 'earphones',
          judged: this.gateChunks,
          suppressed: this.suppressedChunks,
          keepalive: this.keepaliveChunks,
          local_barge_ins: this.localBargeIns,
          confirmed_barge_ins: this.confirmedBargeIns,
          held_frames: this.heldPlaybackFrames,
          dropped: this.droppedChunks,
          underruns: player?.underrunCount ?? this._underruns(),
          lead_ms: Math.round((player?.leadSeconds ?? PLAYBACK_LEAD_MIN_S) * 1000),
          coupling: Math.round(this.echoGate.coupling * 1000) / 1000,
          output_latency_ms: Math.round(this.outputLatency() * 1000),
        }),
      );
    } catch {
      /* advisory */
    }
  }

  /**
   * Mirror the scheduler's two diagnostics into signals.
   *
   * Polled off the timer that already runs rather than pushed from enqueue(),
   * which is on the audio path — and written ONLY when the value moved, so a
   * clean session costs zero change-detection passes no matter how long it runs.
   */
  private publishPlaybackStats(): void {
    const player = this.player;
    if (!player) return;
    const underruns = player.underrunCount;
    if (underruns !== this._underruns()) this._underruns.set(underruns);
    const leadMs = Math.round(player.leadSeconds * 1000);
    if (leadMs !== this._playbackLeadMs()) this._playbackLeadMs.set(leadMs);
  }

  /**
   * One microphone chunk's RMS: straight to the orb, VU-shaped and throttled to
   * the meter bar.
   */
  private pushLevel(rms: number): void {
    this._userRms.set(rms);

    const shaped = Math.min(1, Math.pow(Math.min(1, rms * METER_GAIN), METER_CURVE));
    const now = performance.now();
    // dt is measured from the last UPDATE, not the last paint: paints are
    // throttled, and decaying by "time since last paint" on every chunk would
    // apply the same interval two or three times and collapse the bar.
    const dt = this.meterLastUpdate ? (now - this.meterLastUpdate) / 1000 : 0;
    this.meterLastUpdate = now;
    // Fast attack, slow release — the ballistics of a real VU meter. A raw RMS
    // reading drops to zero between syllables and reads as a dead microphone.
    this.meterLevel =
      shaped > this.meterLevel
        ? shaped
        : Math.max(shaped, this.meterLevel - METER_DECAY_PER_S * dt);
    if (now - this.meterLastPaint < METER_MIN_INTERVAL_MS) return;
    this.meterLastPaint = now;
    this._micLevel.set(this.meterLevel);
  }

  private nextSystemKey(): string {
    return `s:${++this.systemLineSeq}`;
  }

  /** Append or update one transcript line, keyed so a streaming turn revises in
   *  place instead of appending a near-duplicate on every delta. */
  private writeLine(
    key: string,
    role: InterviewLine['role'],
    text: string,
    partial: boolean,
  ): void {
    this._lines.update((lines) => {
      const idx = lines.findIndex((l) => l.key === key);
      if (idx < 0) return [...lines, { key, role, text, partial }];
      const copy = [...lines];
      copy[idx] = { key, role, text, partial };
      return copy;
    });
  }

  /** Remove one transcript line — used when a delta preview's final transcript
   *  arrives EMPTY (the transcriber heard nothing), so the screen does not keep
   *  a partial "You" line for words that were never said. */
  private removeLine(key: string): void {
    this._lines.update((lines) => lines.filter((l) => l.key !== key));
  }
}

/* ============================================================================
   Small helpers.
   ========================================================================== */

function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const mm = String(Math.floor(s / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

/** A wire field read as a non-empty string, or null. */
function str(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null;
}

/** A wire field read as a finite number, or null. */
function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** A wire field read as a plain object, or null. */
function obj(v: unknown): Record<string, unknown> | null {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

/** A wire field read as an array of non-empty strings. Never null: an absent
 *  list and an empty list mean the same thing to the template, and a `?? []` at
 *  every read site is three chances to forget one. */
function strList(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === 'string' && x.trim().length > 0);
}

/**
 * `reep.report` -> a scorecard the template can render without guarding.
 *
 * DEGRADE, NEVER ASSERT — the same rule the relay parses the model's JSON by.
 * The relay has already validated and bounded these fields; this pass exists
 * because the alternative is a template that throws on one unexpected type and
 * takes the whole screen down at the exact moment the student is owed their
 * report. A field in the wrong shape costs that field.
 *
 * `num()` is what protects the scores: it answers null for anything that is not
 * a finite number, so a missing score stays missing. Do not add `?? 0` — see
 * InterviewReport.
 */
function readReport(msg: Record<string, unknown>): InterviewReportResult {
  const available = msg['available'] === true;
  const body = obj(msg['report']);
  if (!available || body === null) {
    return { available: false, reason: str(msg['reason']) ?? 'unparseable', report: null };
  }
  return {
    available: true,
    reason: null,
    report: {
      overall: num(body['overall']),
      communication: num(body['communication']),
      domain: num(body['domain']),
      structure: num(body['structure']),
      strengths: strList(body['strengths']),
      improvements: strList(body['improvements']),
      drill: str(body['drill']) ?? '',
      summary: str(body['summary']) ?? '',
    },
  };
}

/**
 * Microphone failures the student can actually act on. A raw DOMException name
 * in a banner is not one of them.
 */
function describeMicError(err: unknown): string {
  // Both branches: getUserMedia rejects with a DOMException, and although
  // DOMException inherits from Error in current engines, older ones do not make
  // that true — reading the name through only one of the two checks is how a
  // blocked microphone got reported as a generic failure.
  const name = err instanceof DOMException || err instanceof Error ? err.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Microphone access was blocked. Allow the microphone for this site, then press Start again.';
  }
  if (name === 'NotFoundError') {
    return 'No microphone was found. Connect one and press Start again.';
  }
  return err instanceof Error && err.message
    ? err.message
    : 'The microphone could not be started.';
}
