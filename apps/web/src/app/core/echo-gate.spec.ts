import {
  BARGE_IN_CONSECUTIVE_CHUNKS,
  CHUNK_S,
  COUPLING_ATTACK,
  COUPLING_PRIOR,
  ECHO_GATE_MARGIN,
  EchoGate,
  FarEndTimeline,
  GateVerdict,
  echoWindow,
} from './echo-gate';

/**
 * The gate is judged against a simulated room: an interviewer whose level is
 * speech-shaped (syllables, gaps, sentence pauses), a speaker path that
 * delays and scales it by a true coupling, and optionally a student. What
 * matters is what the old gate got wrong -- the interviewer's own voice must
 * never open the uplink, on the first question of a session, whatever the
 * delay -- and that a student talking over it still does.
 */

/** Deterministic PRNG, so a failing case is the same case on every run. */
function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

/** Per-chunk RMS of synthesised speech: syllables of 0.05-0.2, short gaps,
 *  and a 600 ms pause between sentences. */
function speech(chunks: number, seed = 1): number[] {
  const r = rng(seed);
  const out: number[] = [];
  while (out.length < chunks) {
    const sentence = 40 + Math.floor(r() * 60); // 1.6-4 s
    for (let k = 0; k < sentence && out.length < chunks; k++) {
      out.push(r() < 0.15 ? 0.004 : 0.05 + r() * 0.15);
    }
    for (let k = 0; k < 15 && out.length < chunks; k++) out.push(0);
  }
  return out;
}

interface Room {
  /** true speaker-to-mic coupling */
  coupling: number;
  /** seconds from scheduled time to the mic hearing it */
  delay: number;
  /** what the browser reports as outputLatency */
  reportedLatency: number;
  /** jitter-buffer lead before the first buffer sounds */
  lead?: number;
  /** room noise RMS at the mic */
  noise?: number;
}

/**
 * Drive one response through a fresh (or given) gate and return every
 * verdict. `student(k)` is the student's own RMS at mic chunk k.
 */
function run(
  room: Room,
  far: number[],
  student: (k: number) => number = () => 0,
  gate = new EchoGate(),
): GateVerdict[] {
  const timeline = new FarEndTimeline();
  const lead = room.lead ?? 0.14;
  const noise = room.noise ?? 0.002;
  far.forEach((rms, i) => timeline.record(lead + i * CHUNK_S, lead + (i + 1) * CHUNK_S, rms));

  const verdicts: GateVerdict[] = [];
  const total = far.length + Math.ceil((lead + room.delay + 0.5) / CHUNK_S);
  for (let k = 0; k < total; k++) {
    const t = (k + 1) * CHUNK_S; // context time at the end of mic chunk k
    // The echo in this chunk is the playback scheduled `delay` earlier.
    const src = Math.floor((t - CHUNK_S / 2 - room.delay - lead) / CHUNK_S);
    const echo = src >= 0 && src < far.length ? room.coupling * far[src] : 0;
    const s = student(k);
    const mic = Math.sqrt(echo * echo + s * s + noise * noise);
    const w = echoWindow(t, room.reportedLatency);
    verdicts.push(gate.judge(mic, timeline.peak(w.from, w.to)));
  }
  return verdicts;
}

describe('EchoGate: the interviewer never interrupts itself', () => {
  const far = speech(500); // 20 s of interviewer

  for (const coupling of [0.05, 0.3, 1.2, 3.0]) {
    for (const [delay, reported] of [
      [0.02, 0.01], // laptop, wired
      [0.15, 0.1], // Android speaker
      [0.3, 0.02], // Bluetooth that under-reports its latency
      [0.4, 0.3], // Bluetooth that reports it
    ]) {
      it(`coupling ${coupling}, delay ${delay}s: never barges in, first response included`, () => {
        const verdicts = run({ coupling, delay, reportedLatency: reported }, far);
        expect(verdicts).not.toContain('bargeIn');
      });
    }
  }

  it('does not reopen on the second response either (the learning carries over)', () => {
    const gate = new EchoGate();
    const room = { coupling: 1.2, delay: 0.15, reportedLatency: 0.1 };
    run(room, speech(200, 3), undefined, gate);
    expect(run(room, speech(300, 4), undefined, gate)).not.toContain('bargeIn');
  });

  it('holds the uplink shut while the echo is audible', () => {
    const verdicts = run({ coupling: 1.2, delay: 0.15, reportedLatency: 0.1 }, speech(100));
    expect(verdicts.filter((v) => v === 'suppress').length).toBeGreaterThan(80);
  });
});

describe('EchoGate: the student can still interrupt', () => {
  it('opens within BARGE_IN_CONSECUTIVE_CHUNKS once the student talks over it', () => {
    const gate = new EchoGate();
    const room = { coupling: 0.3, delay: 0.15, reportedLatency: 0.1 };
    // Let the gate learn this room on one question first.
    run(room, speech(250, 7), undefined, gate);

    const far = new Array(200).fill(0.1); // a steady interviewer
    const startAt = 100;
    const verdicts = run(room, far, (k) => (k >= startAt ? 0.15 : 0), gate);
    const first = verdicts.indexOf('bargeIn');
    expect(first).toBeGreaterThanOrEqual(startAt);
    expect(first - startAt).toBeLessThan(BARGE_IN_CONSECUTIVE_CHUNKS + 1);
    // Every chunk before it was held rather than thrown away: the primer.
    expect(verdicts.slice(first - (BARGE_IN_CONSECUTIVE_CHUNKS - 1), first)).toEqual(
      new Array(BARGE_IN_CONSECUTIVE_CHUNKS - 1).fill('hold'),
    );
  });

  it('sends a student speaking into an interviewer pause at once', () => {
    const gate = new EchoGate();
    expect(gate.judge(0.12, 0)).toBe('send');
  });

  it('recovers from a murmur that pushed the coupling up, within seconds of echo', () => {
    // The worst case of learning from sub-threshold chunks: a voice kept just
    // under the bar teaches the gate the echo is louder than it is. It must
    // cost barge-in for a moment, never for the rest of the session.
    const gate = new EchoGate();
    for (let k = 0; k < 150; k++) gate.judge(0.03, 0.1); // the room: ratio 0.3
    const learned = gate.coupling;
    gate.judge(0, 0); // an interviewer pause restarts the relax
    for (let k = 0; k < 8; k++) gate.judge(gate.coupling * 0.1 * ECHO_GATE_MARGIN * 0.9, 0.1);
    expect(gate.coupling).toBeGreaterThan(learned * 2);
    gate.judge(0, 0);
    for (let k = 0; k < 75; k++) gate.judge(0.03, 0.1); // three seconds of echo alone
    expect(gate.coupling).toBeLessThan(learned * 1.5);
    gate.judge(0, 0);
    const verdicts = Array.from({ length: BARGE_IN_CONSECUTIVE_CHUNKS }, () =>
      gate.judge(0.15, 0.1),
    );
    expect(verdicts[verdicts.length - 1]).toBe('bargeIn');
  });

  it('at the very start of a session, hears the student no later than the end of the sentence', () => {
    // The prior is deliberately conservative, so a student talking over the
    // FIRST question may not be able to cut in. What must hold is that they
    // are never locked out past it: once the interviewer is silent, the gate
    // sends.
    const room = { coupling: 0.3, delay: 0.15, reportedLatency: 0.1 };
    const far = new Array(100).fill(0.1);
    const verdicts = run(room, far, (k) => (k >= 20 ? 0.2 : 0));
    const lastAudible = verdicts.lastIndexOf('suppress');
    expect(verdicts.slice(lastAudible + 1)).toContain('send');
    expect(verdicts.slice(lastAudible + 1)).not.toContain('suppress');
  });
});

describe('EchoGate: earphones and the quiet room', () => {
  it('sends a breath when nothing is playing, and never holds it', () => {
    const gate = new EchoGate();
    for (let k = 0; k < 10; k++) expect(gate.judge(0.01, 0)).toBe('send');
  });

  it('learns the noise floor only while the interviewer is silent', () => {
    const gate = new EchoGate();
    for (let k = 0; k < 20; k++) gate.judge(0.003, 0);
    expect(gate.noiseFloor).toBeGreaterThan(0);
    expect(gate.noiseFloor).toBeLessThanOrEqual(0.003);
  });
});

describe('EchoGate: the coupling estimate', () => {
  it('starts at the conservative prior and comes down on quiet echo', () => {
    const gate = new EchoGate();
    expect(gate.coupling).toBe(COUPLING_PRIOR);
    run({ coupling: 0.1, delay: 0.1, reportedLatency: 0.05 }, speech(250, 9), undefined, gate);
    expect(gate.coupling).toBeLessThan(0.5);
    expect(gate.coupling).toBeGreaterThan(0.05);
  });

  it('grows by at most one bounded step per chunk under a murmur', () => {
    const gate = new EchoGate();
    for (let k = 0; k < 200; k++) gate.judge(0.01, 0.1); // settle low
    const settled = gate.coupling;
    // A chunk just under the full margin is the most a single step can learn.
    gate.judge(settled * 0.1 * ECHO_GATE_MARGIN * 0.99, 0.1);
    const bound = settled * (1 + (ECHO_GATE_MARGIN - 1) * COUPLING_ATTACK) * 1.0001;
    expect(gate.coupling).toBeLessThanOrEqual(bound);
  });

  it('keeps the coupling across a barge-in, and resets it only with the session', () => {
    const gate = new EchoGate();
    for (let k = 0; k < 200; k++) gate.judge(0.01, 0.1);
    const learned = gate.coupling;
    gate.bargedIn();
    expect(gate.coupling).toBe(learned);
    gate.reset();
    expect(gate.coupling).toBe(COUPLING_PRIOR);
  });
});

describe('FarEndTimeline', () => {
  it('reports the loudest playback overlapping a window', () => {
    const tl = new FarEndTimeline();
    tl.record(0, 0.04, 0.1);
    tl.record(0.04, 0.08, 0.3);
    tl.record(0.08, 0.12, 0.2);
    expect(tl.peak(0.05, 0.1)).toBe(0.3);
    expect(tl.peak(0.09, 0.11)).toBe(0.2);
    expect(tl.peak(0.2, 0.3)).toBe(0);
  });

  it('forgets what was stopped: truncate removes the future and cuts the present', () => {
    const tl = new FarEndTimeline();
    for (let i = 0; i < 10; i++) tl.record(i * 0.04, (i + 1) * 0.04, 0.2);
    tl.truncate(0.1);
    expect(tl.size).toBe(3); // [0,0.04) [0.04,0.08) [0.08,0.1)
    expect(tl.peak(0.1, 1)).toBe(0);
    expect(tl.peak(0.09, 0.1)).toBe(0.2);
  });

  it('keeps audio delivered ahead of the clock: a burst is not pruned before it plays', () => {
    // Nova sends a whole answer faster than realtime; twenty seconds of it
    // may be on the clock while the first second plays.
    const tl = new FarEndTimeline();
    for (let i = 0; i < 500; i++) tl.record(i * 0.04, (i + 1) * 0.04, 0.1);
    expect(tl.peak(0.5, 0.6)).toBe(0.1);
    expect(tl.peak(19.5, 19.6)).toBe(0.1);
  });

  it('drops what ended well behind the query clock', () => {
    const tl = new FarEndTimeline();
    for (let i = 0; i < 1000; i++) tl.record(i * 0.04, (i + 1) * 0.04, 0.1);
    tl.peak(39, 40);
    expect(tl.size).toBeLessThanOrEqual(Math.ceil((FarEndTimeline.HORIZON_S + 1) / 0.04) + 1);
  });
});

describe('echoWindow', () => {
  it('reaches back by the output latency plus the slack, and forward to the chunk', () => {
    const w = echoWindow(10, 0.1);
    expect(w.to).toBe(10);
    expect(w.from).toBeLessThan(10 - CHUNK_S - 0.1);
  });

  it('ignores a missing or absurd latency report', () => {
    expect(echoWindow(10, Number.NaN).from).toBeCloseTo(echoWindow(10, 0).from);
    expect(echoWindow(10, 5).from).toBeCloseTo(echoWindow(10, 0.3).from);
  });
});
