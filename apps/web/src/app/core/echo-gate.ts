/**
 * The mock interview's echo gate, as two pure classes with no DOM and no
 * Web Audio, so the decision the uplink rests on is testable
 * (echo-gate.spec.ts) rather than something only a laptop in a hostel room
 * can exercise.
 *
 * WHY IT EXISTS. On speakers the student's microphone hears the interviewer.
 * Nova does its own barge-in on the uplink, so any of the interviewer's own
 * voice that reaches it is "the student interrupting": Nova abandons the
 * question, the relay tells the browser to flush, and the rest of the
 * sentence is gone. The browser's echo canceller removes most of that
 * leakage, not all of it, and on a phone (speaker centimetres from the mic)
 * the residue is loud. So the uplink is held while the interviewer is
 * audible, and reopened only for a voice that is clearly louder than the
 * echo could be.
 *
 * WHAT THIS REPLACED, AND WHY IT FAILED. The previous gate calibrated an echo
 * level from the first five mic chunks after the player had audio SCHEDULED.
 * Scheduled is not audible: the first buffer starts a jitter-buffer lead
 * (140-300 ms) in the future, and the device's output latency (up to ~150 ms
 * on Android, ~300 ms on Bluetooth) comes on top. So calibration measured the
 * silence before the voice, the threshold settled near the noise floor, and
 * the interviewer's own first syllables opened the gate -- on every question,
 * and again after every resume, which is the "voice drops" and the chop. The
 * learned level was also capped at 0.03 RMS, below a speaking voice, so a
 * loud phone speaker cleared it even when calibration happened to work.
 *
 * WHAT THIS DOES INSTEAD: a double-talk detector referenced to the FAR END
 * (Geigel's idea, applied to chunk RMS). The player records the level of
 * every buffer it puts on the audio clock (FarEndTimeline); for each captured
 * chunk the service asks for the loudest playback that could be arriving in
 * it now, given the output latency and a slack for the input path and the
 * room (`farRef`). The gate learns ONE number per session -- `coupling`, how
 * loud the echo is relative to that playback -- and a chunk counts as the
 * student only when it beats `ECHO_GATE_MARGIN x coupling x farRef`. The
 * threshold therefore follows the interviewer's loudness moment by moment
 * (loud syllable: high bar; pause between sentences: the student can come
 * in), and nothing has to be measured at the top of each response, which is
 * the moment the old gate got wrong.
 */

/** 40 ms: one uplink chunk. Mirrors CHUNK_MS in interview.service.ts. */
export const CHUNK_S = 0.04;

/** Far-end level below which the interviewer is treated as silent, so the
 *  gate is open and full duplex. Synthesised speech between words is digital
 *  near-silence; this sits just above it. */
export const FAR_END_SILENT_RMS = 0.002;

/** How far above the echo the playback predicts a chunk must be before it is
 *  believed to be the student. 3.0 linear is ~9.5 dB. */
export const ECHO_GATE_MARGIN = 3.0;

/** The same idea against the room: a chunk must beat the noise floor by this
 *  much. Wider than ECHO_GATE_MARGIN because a floor is steady and speech is
 *  peaky. */
export const NOISE_FLOOR_MARGIN = 4.0;

/** Hard floor on the threshold (~-42 dBFS), so a silent room cannot let a DC
 *  offset open the gate. */
export const GATE_ABSOLUTE_MIN_RMS = 0.008;

/** Consecutive over-threshold chunks before the gate opens: 200 ms. Longer
 *  than a cough, a chair or a keystroke; shorter than the first word of an
 *  answer. */
export const BARGE_IN_CONSECUTIVE_CHUNKS = 5;

/** The margins relax, over this many chunks of one continuous echo window,
 *  from their full values to ECHO_GATE_MARGIN_RELAXED. It bounds how long a
 *  coupling estimate that is wrong-HIGH (the session's first seconds, before
 *  the prior has come down, or after a student murmuring under the bar has
 *  pushed it up) can keep a student who is talking from being heard.
 *  50 chunks = 2 s. Relaxed to 2.0 (~6 dB), not the old gate's 1.5: the
 *  coupling is an upper envelope of the echo now, not a guess, and 6 dB over
 *  that envelope for 200 ms running is a voice, not a wobble in the echo
 *  canceller. */
export const GATE_MARGIN_RELAX_CHUNKS = 50;
export const ECHO_GATE_MARGIN_RELAXED = 2.0;

/**
 * The coupling a session starts from, before anything has been measured.
 *
 * CONSERVATIVE ON PURPOSE. Too low is the old bug -- the interviewer's own
 * voice clears the bar on the first question. Too high only means barge-in is
 * hard until the estimate has come down, which the release below does in
 * about two seconds of the interviewer speaking. 2.0 covers a phone at full
 * volume with its speaker beside the microphone and the echo canceller not
 * yet converged.
 */
export const COUPLING_PRIOR = 2.0;
export const COUPLING_MIN = 0.001;
export const COUPLING_MAX = 4.0;

/** Peak follower on the coupling ratio, per chunk: up quickly (a louder
 *  volume setting is learned within a few chunks), down slowly (the estimate
 *  is an upper envelope, never an average that half the echo exceeds). */
export const COUPLING_ATTACK = 0.3;
export const COUPLING_RELEASE = 0.05;

/** The coupling is learned only from chunks where the interviewer is loud
 *  enough that the echo, not the room, dominates what the mic hears. */
export const COUPLING_LEARN_MIN_FAR_RMS = 0.02;

/** Noise-floor follower: instant down, glacial up, capped. A floor that has
 *  learned the student's voice has stopped being a floor. */
export const NOISE_FLOOR_RISE = 0.002;
export const NOISE_FLOOR_CEILING = 0.02;

/** Beyond the device's reported output latency, how much further back a mic
 *  chunk may be hearing the playback: the input path, the room's reverb, and
 *  whatever a Bluetooth route adds that the browser does not report. */
export const ECHO_PATH_SLACK_S = 0.35;

/** Reported output latency is trusted up to this; past it the report is
 *  treated as noise rather than stretching the window without bound. */
export const MAX_REPORTED_OUTPUT_LATENCY_S = 0.3;

/**
 * The window of PLAYBACK clock time a mic chunk captured at `t` (context
 * clock, end of the chunk) can contain the echo of.
 *
 * The left edge reaches back past the chunk by the output latency plus
 * ECHO_PATH_SLACK_S: that is the echo's tail, still in the room after the
 * buffer that made it has finished. The right edge is `t` itself, NOT
 * `t - outputLatency`, although nothing scheduled after `t - outputLatency`
 * can have reached the speaker yet. A reported latency is an estimate, and
 * one that overestimated would leave the first syllables of every question
 * outside the window -- judged against a silent far end, sent straight to
 * Nova, the exact bug this module exists for. Reaching to `t` costs only a
 * gate that closes a few chunks before the echo arrives, which is the safe
 * side to be early on.
 */
export function echoWindow(t: number, outputLatency: number): { from: number; to: number } {
  const lat =
    Number.isFinite(outputLatency) && outputLatency > 0
      ? Math.min(outputLatency, MAX_REPORTED_OUTPUT_LATENCY_S)
      : 0;
  return { from: t - CHUNK_S - lat - ECHO_PATH_SLACK_S, to: t };
}

/**
 * What the player has put on the audio clock, and how loud each piece is.
 *
 * One entry per scheduled buffer (40 ms at 24 kHz). `truncate` is called when
 * playback is stopped, so audio that was cut never counts as echo.
 *
 * Pruned against the QUERY clock, never against the newest entry. Nova
 * delivers faster than realtime -- a twenty-second answer can arrive in three
 * -- so the newest entry may be many seconds in the future, and pruning
 * against it would discard audio that has not played yet: the gate would read
 * a silent far end under a loud interviewer and send the echo upstream.
 */
export class FarEndTimeline {
  /** How far behind the newest query an entry may end before it is dropped. */
  static readonly HORIZON_S = 3;
  /** Hard bound on entries: ~3 minutes of 40 ms buffers, far past any turn. */
  static readonly MAX_ENTRIES = 4500;

  private readonly starts: number[] = [];
  private readonly ends: number[] = [];
  private readonly levels: number[] = [];

  record(startAt: number, endAt: number, rms: number): void {
    if (!(endAt > startAt)) return;
    this.starts.push(startAt);
    this.ends.push(endAt);
    this.levels.push(Number.isFinite(rms) && rms > 0 ? rms : 0);
    if (this.starts.length > FarEndTimeline.MAX_ENTRIES) this.dropHead(1);
  }

  /** Playback stopped at `at`: nothing scheduled after it will sound, and a
   *  buffer cut mid-way sounds only up to it. */
  truncate(at: number): void {
    for (let i = this.starts.length - 1; i >= 0; i--) {
      if (this.starts[i] >= at) {
        this.starts.splice(i, 1);
        this.ends.splice(i, 1);
        this.levels.splice(i, 1);
      } else if (this.ends[i] > at) {
        this.ends[i] = at;
      }
    }
  }

  /** The loudest playback overlapping [from, to), or 0. Queries move forward
   *  with the capture clock, so what ended well before this one is dropped. */
  peak(from: number, to: number): number {
    const horizon = from - FarEndTimeline.HORIZON_S;
    let stale = 0;
    // Scheduling order is start order and buffers do not overlap, so the
    // entries that ended longest ago are at the head.
    while (stale < this.ends.length && this.ends[stale] < horizon) stale++;
    if (stale > 0) this.dropHead(stale);
    let best = 0;
    for (let i = 0; i < this.starts.length; i++) {
      if (this.starts[i] < to && this.ends[i] > from && this.levels[i] > best) {
        best = this.levels[i];
      }
    }
    return best;
  }

  clear(): void {
    this.starts.length = 0;
    this.ends.length = 0;
    this.levels.length = 0;
  }

  get size(): number {
    return this.starts.length;
  }

  private dropHead(n: number): void {
    this.starts.splice(0, n);
    this.ends.splice(0, n);
    this.levels.splice(0, n);
  }
}

/**
 * What to do with one captured chunk.
 *
 *   send     the interviewer is silent: full duplex.
 *   hold     over the threshold but not yet for long enough; keep the chunk
 *            (the service's primer) in case this is the start of a sentence.
 *   bargeIn  the student is talking over the interviewer: open the gate.
 *   suppress echo, or silence under echo: do not send.
 */
export type GateVerdict = 'send' | 'hold' | 'bargeIn' | 'suppress';

export class EchoGate {
  /** Echo level relative to the far end. See COUPLING_PRIOR. */
  coupling = COUPLING_PRIOR;
  /** Minimum-follower over chunk RMS while the interviewer is silent. */
  noiseFloor = 0;
  /** The threshold the last judged chunk was held to, for diagnostics. */
  lastThreshold = 0;

  private hot = 0;
  private windowChunks = 0;

  /**
   * Judge one chunk.
   *
   * @param micRms that chunk's RMS, 0..1
   * @param farRef the loudest playback that can be arriving in it, from
   *        FarEndTimeline.peak over echoWindow()
   */
  judge(micRms: number, farRef: number): GateVerdict {
    if (!(farRef >= FAR_END_SILENT_RMS)) {
      // Nothing audible can be reaching the microphone. Full duplex, and the
      // quiet is the chance to learn what quiet sounds like here.
      this.windowChunks = 0;
      this.hot = 0;
      this.trackNoiseFloor(micRms);
      return 'send';
    }

    this.windowChunks++;
    const relax = Math.min(1, this.windowChunks / GATE_MARGIN_RELAX_CHUNKS);
    const echoMargin = ECHO_GATE_MARGIN + (ECHO_GATE_MARGIN_RELAXED - ECHO_GATE_MARGIN) * relax;
    const floorMargin =
      NOISE_FLOOR_MARGIN + (ECHO_GATE_MARGIN_RELAXED - NOISE_FLOOR_MARGIN) * relax;
    const echoTerm = this.coupling * farRef * echoMargin;
    const threshold = Math.max(GATE_ABSOLUTE_MIN_RMS, this.noiseFloor * floorMargin, echoTerm);
    this.lastThreshold = threshold;

    if (micRms > threshold) {
      if (++this.hot >= BARGE_IN_CONSECUTIVE_CHUNKS) {
        this.bargedIn();
        return 'bargeIn';
      }
      // Not yet convinced -- and never learned: a chunk this loud may be the
      // student, and teaching the gate that their voice is echo would lock
      // them out.
      return 'hold';
    }

    this.hot = 0;
    // Learn only where the echo term is what set the bar. Where the room or
    // the absolute floor set it, the ratio says more about the room than
    // about the speaker, and would drag the coupling upward.
    if (farRef >= COUPLING_LEARN_MIN_FAR_RMS && echoTerm >= threshold) {
      this.trackCoupling(micRms / farRef);
    }
    return 'suppress';
  }

  /** The service opened the gate. The run and the relax restart; the coupling
   *  is a property of the room and the volume knob, so it is kept. */
  bargedIn(): void {
    this.hot = 0;
    this.windowChunks = 0;
  }

  reset(): void {
    this.coupling = COUPLING_PRIOR;
    this.noiseFloor = 0;
    this.lastThreshold = 0;
    this.hot = 0;
    this.windowChunks = 0;
  }

  private trackCoupling(ratio: number): void {
    if (!Number.isFinite(ratio)) return;
    const k = ratio > this.coupling ? COUPLING_ATTACK : COUPLING_RELEASE;
    const next = this.coupling + (ratio - this.coupling) * k;
    this.coupling = Math.min(COUPLING_MAX, Math.max(COUPLING_MIN, next));
  }

  private trackNoiseFloor(rms: number): void {
    if (rms < this.noiseFloor) {
      this.noiseFloor = rms; // a quieter room IS the new floor, at once
      return;
    }
    // Only chunks not already loud enough to BE speech may raise it: this runs
    // whenever the interviewer is silent, which is exactly when the student
    // talks.
    if (rms >= GATE_ABSOLUTE_MIN_RMS * NOISE_FLOOR_MARGIN) return;
    this.noiseFloor = Math.min(
      NOISE_FLOOR_CEILING,
      this.noiseFloor + (rms - this.noiseFloor) * NOISE_FLOOR_RISE,
    );
  }
}
