/* =====================================================================================
 * PROVENANCE — moved, not rewritten.
 *
 * This file is apps/interview-realtime/src/VoiceVisualizer.ts, copied verbatim into the
 * Angular app when the realtime mock interviewer replaced the LiveKit agent as the
 * student-facing assistant. Not one number, gradient stop or damping constant was
 * touched: every value below is load-bearing and the reasoning for each is in the
 * comment beside it. Re-derive nothing; if a value looks wrong, read its comment first.
 *
 * It is deliberately framework-free — zero imports, plain classes — so it stays a
 * straight copy of the source of truth rather than an Angular fork of it. The Angular
 * seam is in the component that owns it (features/assistant/assistant.component.ts):
 *   1. construct MockAudioStreamController (DOM-free, safe in a field initialiser),
 *   2. construct VoiceVisualizer(canvas, controller) in ngAfterViewInit,
 *   3. feed it real audio via controller.mapRmsToAmplitude(rms) ->
 *      injectUserAudioAmplitude / injectAiAudioAmplitude, from InterviewService's
 *      userRms / aiRms signals,
 *   4. ngOnDestroy: visualizer.destroy() FIRST, then controller.dispose() — that order
 *      is documented on dispose() and is load-bearing.
 *
 * TWO THINGS THE HOST MUST KNOW, because neither is visible from the API:
 *
 *  - OVERLAY_CSS (section 5) makes `.rvz-overlay` a FULL-SCREEN FIXED MODAL with a
 *    backdrop-filter. The assistant screen wants an inline orb, so it renders the
 *    canvas inside its own <div class="rvz-overlay orb"> — the constructor ADOPTS a
 *    parent that already carries that class instead of wrapping — and overrides the
 *    layout from the component stylesheet at `.orb.rvz-overlay`, whose specificity
 *    beats both `.rvz-overlay` and `.rvz-overlay.is-open`.
 *
 *  - STATE_STYLE (section 3) is a cool blue/green/white palette designed to glow
 *    additively over a DARK backdrop; REEP v2 is warm amber on paper. Retheming it
 *    would mean re-deriving the alphas of nine gradient stops, so the component instead
 *    gives the orb a deliberate dark warm-tinted plate and keeps the palette intact.
 *    Consequence: the orb is decoration only. Status is always rendered as TEXT beside
 *    it, never as colour alone (AGENTS.md, frontend conventions).
 *
 * The dev-only simulators (startMockUserSpeech / startMockAiSpeech / stopMock) are kept
 * so this stays a byte-for-byte copy that can be diffed against the original; nothing in
 * REEP calls them. dispose() clears their timers and IS called.
 * ===================================================================================== */

/* =====================================================================================
 * VoiceVisualizer — a fluid, morphing, audio-reactive voice orb on native Canvas 2D.
 *
 * Zero imports. Zero libraries. Everything — the noise field, the spline, the damping,
 * the overlay CSS — is implemented in this file.
 *
 * ---------------------------------------------------------------------------------
 * THE MATHEMATICS, IN ONE PARAGRAPH
 * ---------------------------------------------------------------------------------
 * The orb is the polar curve r(theta, t) sampled at n uniform angles and drawn as a
 * CLOSED cubic Bezier chain built by uniform Catmull-Rom -> Bezier conversion, which
 * is provably C1 (see section 1). r is a unit radius perturbed by fractal Brownian
 * motion: several octaves of 2D simplex noise sampled along a CIRCLE in noise space,
 * so the perturbation is 2*pi-periodic in theta by construction and the loop closes
 * with no seam (section 2). Four slow, large-feature octaves ("fluid") carry the AI
 * voice as deep rolling waves; three fast, small-feature octaves ("user") carry the
 * student's voice as high-frequency perimeter shimmer. Everything that can change --
 * amplitudes, and all 20 per-state style parameters -- passes through frame-rate
 * independent damping (cascaded one-pole for audio, critically-damped spring for
 * state), and every rate-driven quantity is integrated as a PHASE rather than
 * multiplied by absolute time, so a rate change alters speed and never position
 * (section 4). That is the whole reason nothing in here can visibly snap.
 * ===================================================================================== */

/* =====================================================================================
 * 0. SMALL MATH HELPERS
 * ===================================================================================== */

const TWO_PI = Math.PI * 2;

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/** Clamp into the legal globalAlpha range. State glow may exceed 1 deliberately
 *  (Speaking glow = 1.35) so that low-alpha passes are driven to full brightness;
 *  the clamp happens here, at the last possible moment. */
function alpha01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/**
 * NOTE ON ONE-POLE DAMPING (the theory the amplitude cascade in section 4 uses).
 *
 *   x' = target + (x - target) * e^(-dt/tau)
 *
 * is the EXACT integration of dx/dt = (target - x)/tau over a step of dt. tau is the
 * time constant: 63% of the way in tau seconds, 95% in 3*tau, independent of how many
 * frames that took. The naive `x += (target - x) * 0.1` is not frame-rate independent
 * at all -- it settles twice as fast on a 120 Hz display as on a 60 Hz one. The
 * cascaded two-stage version is solved in closed form in MockAudioStreamController.step.
 */

/**
 * Critically-damped spring ("SmoothDamp"). Reaches the target on an S-curve with
 * provably zero overshoot, carrying a velocity so a target change mid-flight blends
 * instead of restarting.
 *
 * The closed-form solution of a critically damped 2nd-order system with omega = 2/T is
 *
 *   x(t) = target + (x0 - target + (v0 + omega*(x0-target))*t) * e^(-omega*t)
 *
 * `exp_` below is the standard rational (Pade-style) approximation of e^(-k) with
 * k = omega*dt; it is accurate to ~1e-4 over the range k ever takes here and is what
 * keeps the solver frame-rate independent without calling Math.exp per parameter per
 * frame (20 parameters * 60 fps).
 *
 * `vel` is an out-parameter held in a pre-allocated Float32Array -- no object churn.
 */
function smoothDamp(
  cur: number,
  target: number,
  vel: Float32Array,
  i: number,
  smoothTime: number,
  dt: number,
): number {
  const omega = 2 / smoothTime;
  const k = omega * dt;
  const exp_ = 1 / (1 + k + 0.48 * k * k + 0.235 * k * k * k);
  const change = cur - target;
  const temp = (vel[i] + omega * change) * dt;
  vel[i] = (vel[i] - omega * temp) * exp_;
  return target + (change + temp) * exp_;
}

/* =====================================================================================
 * 1. INLINE 2D SIMPLEX NOISE  (Gustavson's formulation)
 * ---------------------------------------------------------------------------------
 * Chosen over summed sines deliberately. Summed integer harmonics are also seamless
 * around the ring and cheaper, but each harmonic carries k-fold rotational symmetry
 * that stays visible at high amplitude -- the orb reads as a vibrating bell mode, not
 * a fluid. Simplex has irregularity in the FIELD, so no symmetry survives.
 *
 * Cost: 7 octaves * 192 samples = 1344 evaluations/frame ~ 70us, i.e. 0.4% of a
 * 16.6 ms budget. This is not the expensive part of the frame; the gradient fills are.
 * ===================================================================================== */

/** 0.5 * (sqrt(3) - 1) — skew factor, square lattice -> simplex lattice. */
const F2 = 0.3660254037844386;
/** (3 - sqrt(3)) / 6 — the matching unskew factor. */
const G2 = 0.21132486540518713;

/**
 * The 12 gradients of the classic `grad3` table projected to 2D. Mixed lengths
 * (sqrt(2) and 1) are intentional: the empirical output-scaling constant 70 below is
 * calibrated against exactly this table. Swap the table and 70 is wrong.
 */
const GRAD2 = new Float32Array([
  1, 1, -1, 1, 1, -1, -1, -1,
  1, 0, -1, 0, 1, 0, -1, 0,
  0, 1, 0, -1, 0, 1, 0, -1,
]);

class Simplex2 {
  /** Duplicated 512-entry permutation so noise() needs no index masking. */
  private readonly perm = new Uint8Array(512);
  /** perm % 12, precomputed: the gradient selector. */
  private readonly perm12 = new Uint8Array(512);

  constructor(seed: number = 0x5eed1337) {
    const p = new Uint8Array(256);
    for (let i = 0; i < 256; i++) p[i] = i;

    // xorshift32-driven Fisher-Yates. Deterministic per seed, so two Simplex2
    // instances with different seeds give decorrelated fields reproducibly.
    let s = seed | 0 || 1;
    for (let i = 255; i > 0; i--) {
      s ^= s << 13; s |= 0;
      s ^= s >>> 17;
      s ^= s << 5; s |= 0;
      const j = (s >>> 0) % (i + 1);
      const t = p[i]; p[i] = p[j]; p[j] = t;
    }
    for (let i = 0; i < 512; i++) {
      const v = p[i & 255];
      this.perm[i] = v;
      this.perm12[i] = v % 12;
    }
  }

  /**
   * 2D simplex noise, output approximately in [-1, 1].
   *
   * Skew (x,y) into a sheared lattice where the simplices are right triangles, find
   * the containing triangle's three corners, and sum each corner's gradient
   * contribution weighted by (0.5 - d^2)^4 -- a compactly supported radial kernel that
   * is C2 at its zero, which is why the field has no visible lattice artefacts.
   *
   * Index bounds (do NOT shrink the tables to 256):
   *   ii + perm[jj]           <= 255 + 255 = 510
   *   ii + i1 + perm[jj + j1] <= 255 +  1  + 255 = 511
   *   ii + 1 + perm[jj + 1]   <= 511
   */
  noise(xin: number, yin: number): number {
    const s = (xin + yin) * F2;
    const i = Math.floor(xin + s);
    const j = Math.floor(yin + s);
    const t = (i + j) * G2;
    const x0 = xin - (i - t);
    const y0 = yin - (j - t);

    // Which of the two triangles of the rhombus are we in?
    const i1 = x0 > y0 ? 1 : 0;
    const j1 = x0 > y0 ? 0 : 1;

    const x1 = x0 - i1 + G2;
    const y1 = y0 - j1 + G2;
    const x2 = x0 - 1 + 2 * G2;
    const y2 = y0 - 1 + 2 * G2;

    const ii = i & 255;
    const jj = j & 255;

    let n0 = 0, n1 = 0, n2 = 0;

    let t0 = 0.5 - x0 * x0 - y0 * y0;
    if (t0 > 0) {
      t0 *= t0;
      const g = this.perm12[ii + this.perm[jj]] << 1;
      n0 = t0 * t0 * (GRAD2[g] * x0 + GRAD2[g + 1] * y0);
    }

    let t1 = 0.5 - x1 * x1 - y1 * y1;
    if (t1 > 0) {
      t1 *= t1;
      const g = this.perm12[ii + i1 + this.perm[jj + j1]] << 1;
      n1 = t1 * t1 * (GRAD2[g] * x1 + GRAD2[g + 1] * y1);
    }

    let t2 = 0.5 - x2 * x2 - y2 * y2;
    if (t2 > 0) {
      t2 *= t2;
      const g = this.perm12[ii + 1 + this.perm[jj + 1]] << 1;
      n2 = t2 * t2 * (GRAD2[g] * x2 + GRAD2[g + 1] * y2);
    }

    // 70 is the standard empirical normaliser for this gradient set.
    return 70 * (n0 + n1 + n2);
  }
}

/* =====================================================================================
 * 2. THE OCTAVE TABLES
 * ---------------------------------------------------------------------------------
 * Each octave samples the noise field along a circle of radius rN centred at a
 * drifting offset:
 *
 *   sample_j(theta) = simplex( offX_j + rN_j*cos(theta + swirl_j),
 *                              offY_j + rN_j*sin(theta + swirl_j) )
 *
 * Because the sample path is a closed circle, sample_j is exactly 2*pi-periodic in
 * theta -- no wrap blending, no windowing, no seam. Simplex features are ~1 unit
 * across, so a circle of radius rN traverses about 2*pi*rN features: rN SETS THE LOBE
 * COUNT. Never use an integer angular multiplier (m*theta) to get more lobes -- that
 * re-traverses the same path m times and stamps visible m-fold symmetry on the orb.
 *
 * Octaves 0..3 = FLUID (body, AI-driven). Octaves 4..6 = USER (perimeter shimmer).
 * Persistence 0.5, lacunarity ~2.1 within each group: textbook fBm, which is what
 * makes it read as organic rather than merely wobbly.
 *
 * The 30x drift-rate gap between the groups (0.055-0.24 u/s vs 1.7-4.4 u/s), plus
 * 3.5-34 lobes vs 18-42 lobes, is the entire perceptual distinction between "slow deep
 * rolling AI waves" and "fast energetic user vibration".
 *
 * Top user octave is 42.1 lobes, so it needs 4.5*42.1 = 190 samples to clear the
 * band-limiter below at full weight. That is why the sample count is FLOORED at
 * MIN_SAMPLES (see section 5) rather than being derived from the orb's pixel size
 * alone: sizing n from pixels put a 1707x876 laptop at n = 114, where this octave's
 * band gain is exactly 0 and the entire "fast energetic user vibration" band is
 * silently deleted. Above 190 the extra samples cost nothing that matters (7 octaves
 * x 190 samples ~ 70 us/frame) and below it the shimmer aliases into crawling moire.
 * ===================================================================================== */

const OCT = 7;
const FLUID_OCT = 4;

/** Noise-space sampling radius per octave -> lobe count ~ 2*pi*rN. */
const OCT_RN = new Float64Array([0.55, 1.30, 2.70, 5.40, 2.90, 4.50, 6.70]);
/** Base amplitude per octave. Each group sums to exactly 1.00. */
const OCT_AMP = new Float64Array([0.55, 0.26, 0.13, 0.06, 0.52, 0.31, 0.17]);
/** Linear drift speed of the sample-circle centre, noise units per second. */
const OCT_DRIFT = new Float64Array([0.055, 0.090, 0.150, 0.240, 1.70, 2.90, 4.40]);
/** Angular swirl rate, rad/s. SIGNS ALTERNATE: counter-rotating layers stop the whole
 *  pattern reading as one rigid rotating blob -- the biggest tell of a fake orb. */
const OCT_SWIRL = new Float64Array([0.10, -0.07, 0.16, -0.23, 0.90, -1.50, 2.40]);
/** Arbitrary but far-apart (>50 u) seed offsets so the octaves are decorrelated. */
const OCT_SEED_X = new Float64Array([0, 137.2, -88.4, 303.1, 611.0, -455.8, 720.4]);
const OCT_SEED_Y = new Float64Array([0, 51.9, 210.6, -164.7, 42.3, 380.2, -295.1]);
/** Unit drift directions (arbitrary incommensurate angles, so no two octaves ever
 *  travel together and re-correlate). */
const OCT_DIR_ANG = new Float64Array([0.37, 1.92, 3.51, 5.02, 0.94, 2.77, 4.61]);
const OCT_DX = new Float64Array(OCT);
const OCT_DY = new Float64Array(OCT);
/** How many lobes each octave paints around the ring: circumference / feature size. */
const OCT_LOBES = new Float64Array(OCT);
for (let j = 0; j < OCT; j++) {
  OCT_DX[j] = Math.cos(OCT_DIR_ANG[j]) * OCT_DRIFT[j];
  OCT_DY[j] = Math.sin(OCT_DIR_ANG[j]) * OCT_DRIFT[j];
  OCT_LOBES[j] = TWO_PI * OCT_RN[j];   // 3.5, 8.2, 17.0, 33.9, 18.2, 28.3, 42.1
}

/**
 * BAND-LIMITING — FLAG: a correction to the brief, with the measurement behind it.
 *
 * The brief fixes the Nyquist budget at n = 192 ("no octave may exceed ~n/4.5 ~ 42
 * lobes") but also makes n adaptive down to 96. Those two statements are inconsistent:
 * on a 1280x800 window the sizing rule yields n = 104, where the 42-lobe user octave
 * gets 2.5 samples per lobe — below the 3 samples a smooth curve needs, so it aliases
 * into exactly the crawling moire the brief warns about. Verified by counting extrema
 * around the ring: the top octave really does paint 45 lobes.
 *
 * The fix is textbook: band-limit the fBm to the sampling rate. Each octave is faded
 * out as its lobe count approaches the sample count, so a small orb quietly loses its
 * finest shimmer instead of sparkling with alias. Full weight below n/4.5 samples per
 * lobe, zero above n/3.2, smoothstep between — and because the sum is renormalised
 * (below), dropping an octave does not change the overall amplitude.
 */
const BAND_ROLLOFF_START = 4.5;   // samples per lobe at which fading begins
const BAND_ROLLOFF_END = 3.2;     // samples per lobe at which the octave is gone

/* =====================================================================================
 * 3. STATES AND THE PER-STATE PARAMETER TABLE
 * ===================================================================================== */

export enum VisualizerState {
  Connecting = 'connecting',
  Listening = 'listening',
  Speaking = 'speaking',
  Idle = 'idle',
}

/**
 * Parameter slots. Every one of these is interpolated toward its target EVERY FRAME by
 * smoothDamp; setState() only swaps which target row is active. Nothing is ever
 * assigned directly on a state change, which is why states cannot jump.
 *
 * (The design brief sized this at 15; the honest count is 20 once the three RGB
 *  triplets are expanded into individual channels, since each channel needs its own
 *  velocity slot. Same design, correct arithmetic.)
 */
const P_RADIUS = 0;
const P_NOISE_GAIN = 1;
const P_USER_RIPPLE = 2;
const P_AI_SWELL = 3;
const P_LOW_BIAS = 4;
const P_GLOW = 5;
const P_BREATH_RATE = 6;
const P_BREATH_DEPTH = 7;
const P_TIME_SCALE = 8;
const P_RING_ALPHA = 9;
const P_CORE_ALPHA = 10;
const P_CORE_R = 11;
const P_CORE_G = 12;
const P_CORE_B = 13;
const P_MID_R = 14;
const P_MID_G = 15;
const P_MID_B = 16;
const P_EDGE_R = 17;
const P_EDGE_G = 18;
const P_EDGE_B = 19;
const P_COUNT = 20;

/** smoothTime (seconds) per parameter: the time the critically-damped spring takes to
 *  substantially arrive. Slower for colour and for rate parameters, faster for glow. */
const SMOOTH_TIME = new Float32Array(P_COUNT);
SMOOTH_TIME[P_RADIUS] = 0.55;
SMOOTH_TIME[P_NOISE_GAIN] = 0.45;
SMOOTH_TIME[P_USER_RIPPLE] = 0.45;
SMOOTH_TIME[P_AI_SWELL] = 0.45;
SMOOTH_TIME[P_LOW_BIAS] = 0.45;
SMOOTH_TIME[P_GLOW] = 0.38;
SMOOTH_TIME[P_BREATH_RATE] = 0.90;
SMOOTH_TIME[P_BREATH_DEPTH] = 0.90;
SMOOTH_TIME[P_TIME_SCALE] = 0.90;
SMOOTH_TIME[P_RING_ALPHA] = 0.38;
SMOOTH_TIME[P_CORE_ALPHA] = 0.38;
for (let i = P_CORE_R; i < P_COUNT; i++) SMOOTH_TIME[i] = 0.60;

/** A slower settle specifically on the Idle collapse -- a 0.75 s deflate reads as the
 *  orb going to sleep; 0.55 s reads as it being switched off. */
const RADIUS_SMOOTH_IDLE = 0.75;

interface StateStyle {
  readonly radiusFactor: number;
  readonly noiseGain: number;
  readonly userRipple: number;
  readonly aiSwell: number;
  readonly lowOctaveBias: number;
  readonly glow: number;
  readonly breathRate: number;
  readonly breathDepth: number;
  readonly timeScale: number;
  readonly ringAlpha: number;
  readonly coreAlpha: number;
  readonly core: readonly [number, number, number];
  readonly mid: readonly [number, number, number];
  readonly edge: readonly [number, number, number];
}

/**
 * Reading the design out of the numbers:
 *
 *  Connecting  breathRate 0.16 Hz = a 6.3 s cycle at +-5.5% depth: unmistakably slow.
 *              glow 0.45 and a desaturated blue-slate palette read as dim. Neither
 *              audio channel is coupled, so only the breath moves it.
 *  Listening   breathDepth drops to 0.022 (stable), userRipple peaks at 0.075 (highly
 *              sensitive), glow 0.85 over a green-white palette. The only state where
 *              user audio is meaningfully coupled.
 *  Speaking    radiusFactor 1.18 * aiSwell 0.22 swells to ~1.46x nominal at full AI
 *              amplitude. glow 1.35 blows the rim out. lowOctaveBias 1.7 plus a 10 s
 *              breath is where "slow deep fluid rolling waves" comes from.
 *  Idle        radiusFactor 0.16 with an absolute 9 px floor; noiseGain 0 makes it a
 *              mathematically perfect circle; coreAlpha 0 empties the fill so what
 *              remains is a crisp white RING; breathRate 0 = static.
 *
 * The noiseGain column is a CEILING reached at full audio energy, not a resting value:
 * the frame gates the whole fBm shape term on clamp(aAi + aUser, 0, 1), so every state
 * — not just Idle — is an exact circle while nobody is speaking, and only breathes.
 * See the gate in frame(); this table is unchanged from the brief.
 *
 * MEASURED AMPLITUDES (the numbers below are the spec's, verbatim; these are what they
 * buy once the fBm is L2-normalised — see computeRadii). The normalised noise has
 * rms ~0.42 and peaks near 1.1:
 *
 *   Speaking at aAi 0.9:  gain 0.085*1.81*0.9 = 0.139  ->  5.9% rms / 15.4% peak of R
 *                         (the trailing 0.9 is the energy gate). Lands exactly on the
 *                         design budget of ~7.7% / 15.4% peak.
 *   Listening user ripple at aUser 0.8: 0.075*0.8 = 0.060  ->  2.6% rms / 6.7% peak.
 *
 * FLAG — the brief's own sanity check predicts +-5% / +-10.5% for that second case,
 * which requires the USER term to peak around 1.75; a normalised sum of decorrelated
 * noise peaks near 1. The parameter table is the operative spec and is implemented
 * verbatim, so `userRipple` is left at 0.075. If the rim should visibly ripple harder
 * during Listening, 0.12 is the value that lands on the brief's stated 10.5% peak —
 * it is a one-number change here and nothing else needs touching.
 */
const STATE_STYLE: { readonly [K in VisualizerState]: StateStyle } = {
  [VisualizerState.Connecting]: {
    radiusFactor: 0.82, noiseGain: 0.030, userRipple: 0.000, aiSwell: 0.00,
    lowOctaveBias: 1.00, glow: 0.45, breathRate: 0.16, breathDepth: 0.055,
    timeScale: 0.55, ringAlpha: 0.45, coreAlpha: 0.25,
    core: [206, 226, 236], mid: [96, 150, 190], edge: [34, 64, 110],
  },
  [VisualizerState.Listening]: {
    radiusFactor: 1.00, noiseGain: 0.045, userRipple: 0.075, aiSwell: 0.03,
    lowOctaveBias: 1.00, glow: 0.85, breathRate: 0.28, breathDepth: 0.022,
    timeScale: 1.00, ringAlpha: 0.80, coreAlpha: 0.55,
    core: [236, 255, 248], mid: [118, 232, 196], edge: [46, 132, 206],
  },
  [VisualizerState.Speaking]: {
    radiusFactor: 1.18, noiseGain: 0.085, userRipple: 0.010, aiSwell: 0.22,
    lowOctaveBias: 1.70, glow: 1.35, breathRate: 0.10, breathDepth: 0.040,
    timeScale: 1.45, ringAlpha: 1.00, coreAlpha: 0.80,
    core: [255, 252, 240], mid: [150, 244, 226], edge: [58, 176, 214],
  },
  [VisualizerState.Idle]: {
    radiusFactor: 0.16, noiseGain: 0.000, userRipple: 0.000, aiSwell: 0.00,
    lowOctaveBias: 1.00, glow: 0.30, breathRate: 0.00, breathDepth: 0.000,
    timeScale: 0.25, ringAlpha: 0.95, coreAlpha: 0.00,
    core: [255, 255, 255], mid: [232, 240, 246], edge: [200, 214, 226],
  },
};

/** Flatten a StateStyle into the parameter vector layout. */
function styleToVector(s: StateStyle, out: Float32Array): void {
  out[P_RADIUS] = s.radiusFactor;
  out[P_NOISE_GAIN] = s.noiseGain;
  out[P_USER_RIPPLE] = s.userRipple;
  out[P_AI_SWELL] = s.aiSwell;
  out[P_LOW_BIAS] = s.lowOctaveBias;
  out[P_GLOW] = s.glow;
  out[P_BREATH_RATE] = s.breathRate;
  out[P_BREATH_DEPTH] = s.breathDepth;
  out[P_TIME_SCALE] = s.timeScale;
  out[P_RING_ALPHA] = s.ringAlpha;
  out[P_CORE_ALPHA] = s.coreAlpha;
  out[P_CORE_R] = s.core[0]; out[P_CORE_G] = s.core[1]; out[P_CORE_B] = s.core[2];
  out[P_MID_R] = s.mid[0]; out[P_MID_G] = s.mid[1]; out[P_MID_B] = s.mid[2];
  out[P_EDGE_R] = s.edge[0]; out[P_EDGE_G] = s.edge[1]; out[P_EDGE_B] = s.edge[2];
}

/** Error tint targets, blended into mid/edge when setState(..., true) is used. */
const ERR_MID_R = 214, ERR_MID_G = 96, ERR_MID_B = 86;
const ERR_EDGE_R = 120, ERR_EDGE_G = 34, ERR_EDGE_B = 40;

/* =====================================================================================
 * 4. MOCK AUDIO STREAM CONTROLLER
 * ---------------------------------------------------------------------------------
 * Owns the damped 0..1 amplitude for each speaker. Injection NEVER touches the damper
 * state -- it only moves a target. That single rule is what makes it impossible for a
 * caller to snap the orb, however jumpy the feed is.
 *
 * Each channel is a CASCADE of two one-poles. Two first-order lags in series give a
 * critically-damped step response: zero overshoot, and crucially the OUTPUT's
 * derivative is continuous, so even a square-wave amplitude feed cannot produce a
 * visible tick or glitter on the rim. The second stage runs at 0.6*tau so the pair
 * settles in roughly the nominal time rather than 1.6x it.
 * ===================================================================================== */

/**
 * Attack (rising) time constant for the user channel, seconds.
 *
 * FLAG — deviation from the brief, with the measurement behind it.
 * The brief specifies 20 ms. A 60 Hz frame is 16.7 ms, so 20 ms is BELOW the sampling
 * interval: measured, a 0->1 step moved 42% in a single frame and the 60 Hz and 240 Hz
 * trajectories diverged by 0.15. Amplitude is injected at only 10-20 Hz, so the rim
 * would visibly step at the injection rate -- exactly the "glitter" the spec forbids.
 * 33 ms is the smallest constant that still resolves cleanly at 60 Hz (13.6% on the
 * first frame instead of 42%), remains ~3x faster than the AI channel, and sits far
 * inside a 250 ms syllable. It is a FIXED constant, so every display behaves the same.
 */
const TAU_USER_ATTACK = 0.033;
/** Release (falling). 120 ms keeps the perimeter lively between syllables. */
const TAU_USER_RELEASE = 0.120;
/** The AI channel is deliberately an order of magnitude more sluggish: this is the
 *  inertia that reads as a large body of fluid rather than a twitchy membrane. */
const TAU_AI_ATTACK = 0.090;
const TAU_AI_RELEASE = 0.320;

/** If no injection arrives for this long, force the target to 0. A dropped audio
 *  stream must leave the orb settling to rest, not frozen mid-ripple. */
const STALE_MS = 250;

/** Second cascade stage runs at 0.6x the first stage's time constant, so the pair
 *  settles in roughly the nominal time rather than 1.6x it. Must never be exactly 1
 *  (the closed form below divides by 1 - ratio). */
const CASCADE_RATIO = 0.6;

/**
 * Safety net for genuinely low frame rates only. No damper can smooth anything faster
 * than its own sampling interval, so a time constant is never allowed below 1.5 frames.
 * At 60 Hz this floor is 25 ms and never engages (the fastest constant in use is 33 ms);
 * it exists solely so a 20 fps machine degrades to a slower orb rather than a stepping
 * one. Because it is inactive at every normal refresh rate, the damper is in practice
 * exactly frame-rate independent.
 */
const MIN_TAU_FRAMES = 1.5;

interface AmpChannel {
  target: number;
  stage1: number;
  stage2: number;
  lastInjectMs: number;
  readonly tauAttack: number;
  readonly tauRelease: number;
}

function makeChannel(tauAttack: number, tauRelease: number): AmpChannel {
  return { target: 0, stage1: 0, stage2: 0, lastInjectMs: -1e9, tauAttack, tauRelease };
}

export class MockAudioStreamController {
  private readonly userCh: AmpChannel = makeChannel(TAU_USER_ATTACK, TAU_USER_RELEASE);
  private readonly aiCh: AmpChannel = makeChannel(TAU_AI_ATTACK, TAU_AI_RELEASE);

  /** setInterval handles for the dev-only simulators. 0 = not running. */
  private mockUserTimer = 0;
  private mockAiTimer = 0;
  /** Simulator phases, integrated (never t*f — see the phase note in section 6). */
  private mockUserPhase = 0;
  private mockAiPhase = 0;
  private mockUserGate = 0;
  /** One clock per simulator: sharing a single lastMs makes each callback steal the
   *  other's elapsed time, so both envelopes advance at half speed when both run. */
  private mockUserLastMs = 0;
  private mockAiLastMs = 0;

  /**
   * 0..1 — the student's voice. Drives fast, high-frequency, energetic vibration
   * ripples on the OUTER PERIMETER only. It never touches the orb's base radius.
   */
  injectUserAudioAmplitude(value: number): void {
    if (!Number.isFinite(value)) return;
    this.userCh.target = value < 0 ? 0 : value > 1 ? 1 : value;
    this.userCh.lastInjectMs = nowMs();
  }

  /**
   * 0..1 — the assistant's voice. Expands/contracts the WHOLE orb and biases the
   * fractal noise toward its low octaves, producing slow deep rolling waves.
   */
  injectAiAudioAmplitude(value: number): void {
    if (!Number.isFinite(value)) return;
    this.aiCh.target = value < 0 ? 0 : value > 1 ? 1 : value;
    this.aiCh.lastInjectMs = nowMs();
  }

  /** Damped, cascaded user amplitude in 0..1. Read once per frame by the renderer. */
  get user(): number { return this.userCh.stage2; }
  /** Damped, cascaded AI amplitude in 0..1. */
  get ai(): number { return this.aiCh.stage2; }

  /** Renderer-facing: advance both dampers by dt seconds. */
  update(dt: number): void {
    const t = nowMs();
    this.step(this.userCh, dt, t);
    this.step(this.aiCh, dt, t);
  }

  /**
   * Advance one cascaded channel by dt, using the EXACT solution of the coupled pair
   * rather than two sequential one-poles.
   *
   * WHY THE OBVIOUS VERSION IS WRONG. Writing
   *     stage1 += (target - stage1) * alpha(dt, T1);
   *     stage2 += (stage1 - stage2) * alpha(dt, T2);
   * makes each stage individually exact, but stage2's input is not constant across the
   * step — it is stage1, which is itself moving — and the code feeds it the END-of-step
   * value. That is a first-order coupling error: measured, the 60 Hz and 240 Hz
   * trajectories of the AI channel diverged by 0.029 (0.062 at 30 Hz). Visible as the
   * swell arriving at different times on different displays.
   *
   * THE EXACT FORM. Over a step where the target u is constant (it only changes on
   * injection, so this is exactly our case), the system
   *     y1' = (u - y1)/T1,     y2' = (y1 - y2)/T2
   * has the closed solution, with d1 = y1(0) - u, d2 = y2(0) - u:
   *     y1(dt) = u + d1*e1
   *     y2(dt) = u + K*e1 + (d2 - K)*e2,     K = d1 * T1/(T1 - T2)
   * where e1 = exp(-dt/T1), e2 = exp(-dt/T2). Since T2 = ratio*T1 the factor collapses
   * to K = d1/(1 - ratio) with no division by a time constant at all.
   *
   * Verify it at dt = 0: y2 = u + K + d2 - K = y2(0). Correct. As dt -> infinity both
   * exponentials vanish and y2 -> u. Correct. The step response from rest is a
   * difference of two decaying exponentials: monotone, so still zero overshoot, and
   * its derivative is continuous, so a jumpy feed cannot produce a visible tick.
   *
   * Cost is two Math.exp — exactly what the two alpha() calls cost before.
   */
  private step(ch: AmpChannel, dt: number, t: number): void {
    if (t - ch.lastInjectMs > STALE_MS) ch.target = 0;

    // Direction-dependent time constant: fast to rise, slow to fall.
    let tau = ch.target > ch.stage1 ? ch.tauAttack : ch.tauRelease;
    const floor = dt * MIN_TAU_FRAMES;
    if (tau < floor) tau = floor;             // see MIN_TAU_FRAMES

    const u = ch.target;
    const e1 = Math.exp(-dt / tau);
    const e2 = Math.exp(-dt / (tau * CASCADE_RATIO));
    const d1 = ch.stage1 - u;
    const d2 = ch.stage2 - u;
    const k = d1 / (1 - CASCADE_RATIO);
    ch.stage1 = u + d1 * e1;
    ch.stage2 = u + k * e1 + (d2 - k) * e2;
  }

  /* --- dev-only simulators, so the orb can be demonstrated with no live call ------ */

  /** Gated burst envelope: ~4 Hz syllables with jitter and occasional word gaps. */
  startMockUserSpeech(): void {
    if (this.mockUserTimer !== 0) return;
    this.mockUserLastMs = nowMs();
    this.mockUserTimer = setInterval(() => {
      const t = nowMs();
      const dt = Math.min((t - this.mockUserLastMs) / 1000, 0.2);
      this.mockUserLastMs = t;
      // Integrate the syllable phase; never phase = 2*pi*f*t.
      this.mockUserPhase += TWO_PI * 4.0 * dt;
      if (this.mockUserPhase > TWO_PI) {
        this.mockUserPhase -= TWO_PI;
        // New syllable: re-roll the gate. ~20% of syllables are silence (word gaps).
        this.mockUserGate = Math.random() < 0.2 ? 0 : 0.45 + Math.random() * 0.55;
      }
      const env = 0.5 - 0.5 * Math.cos(this.mockUserPhase); // raised cosine, 0..1
      const jitter = 0.88 + Math.random() * 0.12;
      this.injectUserAudioAmplitude(this.mockUserGate * env * jitter);
    }, 50) as unknown as number;
  }

  /** Smooth 0.3-0.9 Hz envelope: long phrases, no per-syllable structure. */
  startMockAiSpeech(): void {
    if (this.mockAiTimer !== 0) return;
    this.mockAiLastMs = nowMs();
    this.mockAiTimer = setInterval(() => {
      const t = nowMs();
      const dt = Math.min((t - this.mockAiLastMs) / 1000, 0.2);
      this.mockAiLastMs = t;
      this.mockAiPhase += TWO_PI * 0.55 * dt;
      if (this.mockAiPhase > TWO_PI) this.mockAiPhase -= TWO_PI;
      // Two incommensurate components so the phrase never audibly loops.
      const a = 0.5 - 0.5 * Math.cos(this.mockAiPhase);
      const b = 0.5 - 0.5 * Math.cos(this.mockAiPhase * 0.37 + 1.1);
      this.injectAiAudioAmplitude(clamp(0.25 + 0.55 * a * 0.7 + 0.35 * b, 0, 1));
    }, 60) as unknown as number;
  }

  /** Stop both simulators and let the dampers release naturally to rest. */
  stopMock(): void {
    if (this.mockUserTimer !== 0) { clearInterval(this.mockUserTimer); this.mockUserTimer = 0; }
    if (this.mockAiTimer !== 0) { clearInterval(this.mockAiTimer); this.mockAiTimer = 0; }
    this.userCh.target = 0;
    this.aiCh.target = 0;
  }

  /**
   * Full teardown: stop the simulators AND zero the damper state outright, so a
   * disposed controller cannot keep a stale amplitude alive. Idempotent.
   *
   * This exists because the controller is EXTERNALLY OWNED — VoiceVisualizer.destroy()
   * deliberately does not touch it, since one controller may drive more than one
   * visualizer — and without it the two setInterval handles above (20 Hz and ~17 Hz)
   * survive teardown and mutate a dead object for the life of the document, pinning
   * the controller and its closures. Whoever constructs the controller disposes it.
   *
   * Call this AFTER the visualizer's destroy(), never before: zeroing the dampers
   * while a frame can still read them would snap the orb on its last painted frame.
   */
  dispose(): void {
    this.stopMock();
    this.userCh.stage1 = this.userCh.stage2 = 0;
    this.aiCh.stage1 = this.aiCh.stage2 = 0;
    this.userCh.lastInjectMs = -1e9;
    this.aiCh.lastInjectMs = -1e9;
  }

  /**
   * REAL-AUDIO SEAM (flagged, not fabricated).
   * A WebRTC/LiveKit track does not hand you a normalised 0..1 amplitude; you must run
   * it through a Web Audio AnalyserNode yourself. Whatever RMS you obtain from
   * getFloatTimeDomainData, map it with:
   *
   *   rms  = sqrt(sum(s*s)/N)
   *   db   = 20*log10(max(rms, 1e-6))
   *   norm = clamp((db - (-55)) / ((-12) - (-55)), 0, 1)
   *   amp  = norm ** 0.65        // perceptual: lifts quiet speech
   *
   * Linear RMS fed straight in looks dead — normal speech sits at 0.03-0.10 linear.
   * Sample the analyser at ~20 Hz (fftSize 512), not per frame. The exact
   * AnalyserNode-to-track wiring must be verified against the installed client
   * library version; this class deliberately takes only the normalised number.
   */
  mapRmsToAmplitude(rms: number, floorDb = -55, ceilDb = -12): number {
    const db = 20 * Math.log10(Math.max(rms, 1e-6));
    const norm = clamp((db - floorDb) / (ceilDb - floorDb), 0, 1);
    return Math.pow(norm, 0.65);
  }
}

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

/* =====================================================================================
 * 5. RENDER CONSTANTS
 * ===================================================================================== */

/** Upper bound on radial samples; arrays are allocated here and used to `n`. */
const MAX_SAMPLES = 192;

/**
 * Lower bound on radial samples, derived from the OCTAVE TABLE rather than from the
 * orb's pixel size.
 *
 * The band-limiter in applyResize fades an octave out as its lobe count approaches
 * n/BAND_ROLLOFF_START, so a sample count chosen purely from pixels deletes the finest
 * octaves on ordinary displays: measured at n = 114 (a 1707x876 window) the band gains
 * were [1, 1, 1, 0.07, 1, 0.80, 0] — the 42-lobe top user octave, the whole source of
 * the high-frequency shimmer, at exactly zero.
 *
 * 4.5 samples per lobe at the top octave's 42.097 lobes is 189.4 -> 190 after forcing
 * even, which is where the top octave's gain lands back at exactly 1. Clamped by
 * MAX_SAMPLES so the two bounds can never cross.
 */
const MIN_SAMPLES = Math.min(
  MAX_SAMPLES,
  (Math.ceil(BAND_ROLLOFF_START * OCT_LOBES[OCT - 1]) + 1) & ~1,
);
/** Catmull-Rom tension. s = 0.5 is pure Catmull-Rom; a/3 = 1/6 in the control-point
 *  formula below. Drop to 0.42 if overshoot ever shows at peak amplitude — visually
 *  identical, strictly tighter convex hull. */
const CR_TENSION = 0.5;
const CR_A = CR_TENSION / 3;

/** Specular sweep dash pattern, in UNIT space (the rim's perimeter is ~2*pi).
 *  Module-level constants: passing a literal array to setLineDash every frame would
 *  allocate. */
const SWEEP_DASH: readonly number[] = [2.2, 4.1];
/** lineDashOffset is periodic in the dash total, so an unbounded sweepPhase buys
 *  nothing and costs precision: engines commonly hold the dash offset as a float32,
 *  whose ULP passes 0.0078 around 1.4e5 — reachable in a ~3-day session at 0.55 u/s,
 *  at which point the specular highlight steps instead of gliding. Wrapping keeps it
 *  in [-6.3, 0] forever, and because it is a whole dash period the wrap is invisible. */
const SWEEP_DASH_PERIOD = SWEEP_DASH[0] + SWEEP_DASH[1];

/** Overlay CSS, injected once per document. */
const OVERLAY_STYLE_ID = 'rvz-overlay-style';
const OVERLAY_CSS = `
.rvz-overlay{position:fixed;inset:0;z-index:1000;display:grid;place-items:center;
 background:radial-gradient(120% 90% at 50% 42%,rgba(14,32,38,.72),rgba(5,10,16,.94) 70%);
 backdrop-filter:blur(28px) saturate(130%);-webkit-backdrop-filter:blur(28px) saturate(130%);
 opacity:0;transform:translateY(1.5%) scale(1.045);pointer-events:none;
 transition:opacity .42s cubic-bezier(.22,.61,.36,1),transform .42s cubic-bezier(.22,.61,.36,1);
 will-change:opacity,transform;}
.rvz-overlay.is-open{opacity:1;transform:none;pointer-events:auto;}
.rvz-overlay.is-settled{will-change:auto;}
.rvz-overlay canvas{display:block;width:100%;height:100%;}
@media (prefers-reduced-motion: reduce){.rvz-overlay{transition-duration:.01ms;}}
`;

/* =====================================================================================
 * 6. THE VISUALIZER
 * ===================================================================================== */

export class VoiceVisualizer {
  /* --- DOM ---------------------------------------------------------------------- */
  private readonly canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D | null;
  private readonly host: HTMLElement;
  /** True when we wrapped the canvas ourselves, so destroy() may unwrap it again. */
  private readonly hostCreated: boolean;

  private readonly audio: MockAudioStreamController;
  private readonly noise = new Simplex2(0x51b1c0de);

  /* --- loop / lifecycle --------------------------------------------------------- */
  private rafId = 0;
  private hideTimer = 0;
  private lastTs = 0;
  private destroyed = false;
  /** CSS presentation only: "the overlay is open". Never the loop's authority. */
  private visible = false;
  /** "Frames are wanted." Independent of `visible`, because a tab switch must be able
   *  to park the frame WITHOUT forgetting that the loop should still be running.
   *  Conflating the two meant the first visibilitychange killed the loop permanently
   *  for any consumer that had not gone through show(). */
  private running = false;
  private resizePending = true;

  /* --- geometry ----------------------------------------------------------------- */
  private cssW = 1;
  private cssH = 1;
  /** Effective device pixel ratio in use (capped at 2). Exposed for diagnostics. */
  private renderDpr = 1;
  private unitRadius = 100;
  /** Starts at 0 so the FIRST applyResize() always takes the `target !== n` branch and
   *  fills cosT/sinT. Seeding it at MAX_SAMPLES would skip that fill on a display where
   *  the computed count happens to be 192, leaving the tables all-zero and the orb
   *  collapsed to the origin. */
  private n = 0;

  /** Pre-allocated at MAX_SAMPLES, mutated in place. Nothing here is ever re-created. */
  private readonly cosT = new Float32Array(MAX_SAMPLES);
  private readonly sinT = new Float32Array(MAX_SAMPLES);
  private readonly px = new Float32Array(MAX_SAMPLES);
  private readonly py = new Float32Array(MAX_SAMPLES);

  /* --- noise phase (integrated, never t*rate) ----------------------------------- */
  private readonly offX = new Float64Array(OCT);
  private readonly offY = new Float64Array(OCT);
  private readonly swirl = new Float64Array(OCT);
  /** Per-octave cos/sin of the swirl phase, refreshed once per frame (7 sin + 7 cos)
   *  so the inner sample loop contains no transcendentals at all. */
  private readonly cphi = new Float64Array(OCT);
  private readonly sphi = new Float64Array(OCT);
  /** Per-frame octave weights (base amplitude * AI bias * band gain). Constant across
   *  samples, so they and their norms are computed once per frame, not once per sample. */
  private readonly wgt = new Float64Array(OCT);
  /** Per-octave anti-alias gain for the current sample count; refilled on resize. */
  private readonly bandGain = new Float64Array(OCT);

  private breathPhase = 0;
  private sweepPhase = 0;

  /* --- state parameters --------------------------------------------------------- */
  private readonly curP = new Float32Array(P_COUNT);
  private readonly tgtP = new Float32Array(P_COUNT);
  private readonly velP = new Float32Array(P_COUNT);
  private readonly baseP = new Float32Array(P_COUNT);
  private state: VisualizerState = VisualizerState.Idle;
  private errorTint = false;

  /* --- gradient cache (quantised dirty key => zero allocation in steady state) --- */
  private gradKeyA = -1;
  private gradKeyB = -1;
  private gHalo: CanvasGradient | null = null;
  private gBody: CanvasGradient | null = null;
  private gCore: CanvasGradient | null = null;
  private gDot: CanvasGradient | null = null;
  /** Cached rgb() strings for the stroke passes. Alpha is never baked in here — it is
   *  applied via globalAlpha — so a continuously damped ringAlpha builds no strings.
   *  (The edge colour needs no cached string: it appears only inside the gradients.) */
  private cssCore = 'rgb(255,255,255)';
  private cssMid = 'rgb(255,255,255)';

  /* --- environment -------------------------------------------------------------- */
  /** LIVE, not frozen at construction: the injected stylesheet carries a live
   *  `@media (prefers-reduced-motion: reduce)` rule, so a one-shot read left a user who
   *  toggled the OS setting with a half-updated widget (CSS honouring it, JS not). */
  private reduceMotion: boolean;
  private rmMql: MediaQueryList | null = null;
  private ro: ResizeObserver | null = null;
  private dprMql: MediaQueryList | null = null;

  constructor(canvas: HTMLCanvasElement, controller: MockAudioStreamController) {
    // The render loop re-arms the next frame BEFORE it touches the controller, so a
    // missing one is not one exception — it is one exception per frame, forever, under
    // a canvas that never paints. Refuse loudly at construction instead: a caller
    // probing constructor signatures then sees a real error rather than a dead orb.
    if (!controller || typeof controller.update !== 'function') {
      throw new TypeError(
        'VoiceVisualizer requires a MockAudioStreamController as its second argument.',
      );
    }

    this.canvas = canvas;
    this.audio = controller;
    this.ctx = canvas.getContext('2d', { alpha: true });

    if (typeof matchMedia === 'function') {
      this.rmMql = matchMedia('(prefers-reduced-motion: reduce)');
      this.reduceMotion = this.rmMql.matches;
      if (typeof this.rmMql.addEventListener === 'function') {
        this.rmMql.addEventListener('change', this.onReduceMotionChange);
      }
    } else {
      this.reduceMotion = false;
    }

    // --- host: adopt an existing .rvz-overlay parent, otherwise wrap the canvas ---
    injectOverlayStyle();
    const parent = canvas.parentElement;
    if (parent && parent.classList.contains('rvz-overlay')) {
      this.host = parent;
      this.hostCreated = false;
    } else {
      const div = document.createElement('div');
      div.className = 'rvz-overlay';
      if (parent) parent.insertBefore(div, canvas);
      else document.body.appendChild(div);
      div.appendChild(canvas);
      this.host = div;
      this.hostCreated = true;
    }

    // Start fully collapsed in Idle so show() can grow the orb out of the dot while
    // the CSS sweeps the backdrop in: two independent motions, one composite gesture.
    styleToVector(STATE_STYLE[VisualizerState.Idle], this.baseP);
    this.curP.set(this.baseP);
    this.tgtP.set(this.baseP);

    for (let j = 0; j < OCT; j++) {
      this.offX[j] = OCT_SEED_X[j];
      this.offY[j] = OCT_SEED_Y[j];
      this.swirl[j] = 0;
    }

    if (typeof ResizeObserver !== 'undefined') {
      // ResizeObserver on the host, not window.resize: it also catches rotation, zoom
      // and layout changes that never fire a window resize. The callback only sets a
      // flag — resizing a canvas reallocates and clears its backing store, and doing
      // that inside the observer causes a visible flash and a resize-loop warning.
      this.ro = new ResizeObserver(() => { this.resizePending = true; });
      this.ro.observe(this.host);
    } else {
      window.addEventListener('resize', this.onWinResize);
    }

    document.addEventListener('visibilitychange', this.onVisibility);
    this.watchDpr();
  }

  /* --------------------------------------------------------------------------------
   * Public API
   * ------------------------------------------------------------------------------ */

  /**
   * Swap the TARGET parameter row. Nothing is assigned to the live parameters, so the
   * orb morphs into the new state under smoothDamp rather than cutting to it.
   */
  setState(s: VisualizerState, errorTint = false): void {
    this.state = s;
    this.errorTint = errorTint;
    styleToVector(STATE_STYLE[s], this.baseP);
  }

  /** Current target state (the visual is still on its way there). */
  getState(): VisualizerState {
    return this.state;
  }

  /** Effective device pixel ratio in use (capped at 2) — for diagnostics/tests. */
  get pixelRatio(): number { return this.renderDpr; }

  /** Active radial sample count — 96 under reduced motion, otherwise 190..192: floored
   *  at MIN_SAMPLES so the finest octave always clears the band-limiter, and rising
   *  with the orb's pixel size above that. */
  get sampleCount(): number { return this.n; }

  /** Sweep the overlay in and start the render loop. */
  show(): void {
    if (this.destroyed) return;
    if (this.hideTimer !== 0) { clearTimeout(this.hideTimer); this.hideTimer = 0; }
    this.host.style.display = '';
    this.visible = true;
    // Force a style flush so the browser has the pre-transition state to animate from.
    void this.host.offsetWidth;
    this.host.classList.remove('is-settled');
    this.host.classList.add('is-open');
    this.host.addEventListener('transitionend', this.onTransitionEnd);
    this.resizePending = true;
    this.start();
  }

  /** Sweep the overlay out; the loop is parked once the transition finishes. */
  hide(): void {
    if (this.destroyed) return;
    this.visible = false;
    this.host.classList.remove('is-settled');
    this.host.classList.remove('is-open');
    this.host.addEventListener('transitionend', this.onTransitionEnd);
    // Belt and braces: if transitionend never arrives (the overlay was already hidden,
    // or a stylesheet removed the transition) the loop would keep running forever
    // against an invisible canvas. This timer is the only thing that guarantees it stops.
    if (this.hideTimer !== 0) clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(this.finishHide, 600) as unknown as number;
  }

  /** Remove every listener, cancel the frame, drop the context. */
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;                       // late RAF callbacks bail on this
    this.running = false;
    if (this.rafId) cancelAnimationFrame(this.rafId);
    this.rafId = 0;
    if (this.hideTimer !== 0) { clearTimeout(this.hideTimer); this.hideTimer = 0; }
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.host.removeEventListener('transitionend', this.onTransitionEnd);
    window.removeEventListener('resize', this.onWinResize);
    if (this.ro) { this.ro.disconnect(); this.ro = null; }
    this.unwatchDpr();
    if (this.rmMql && typeof this.rmMql.removeEventListener === 'function') {
      this.rmMql.removeEventListener('change', this.onReduceMotionChange);
    }
    this.rmMql = null;
    this.gHalo = this.gBody = this.gCore = this.gDot = null;
    this.ctx = null;
    if (this.hostCreated && this.host.parentNode) {
      // UNWRAP, do not demolish. The constructor MOVED the caller's canvas into the
      // wrapper, so removing the wrapper used to take the canvas out of the document
      // with it — leaving any consumer that rebuilds on the same element (an Angular
      // component re-initialising, or the harness's own constructor-attempt loop)
      // holding a detached node and rendering permanently nowhere.
      const parent = this.host.parentNode;
      if (this.canvas.parentNode === this.host) {
        parent.insertBefore(this.canvas, this.host);
        // applyResize wrote these; they must not outlive us and override the page.
        this.canvas.style.width = '';
        this.canvas.style.height = '';
      }
      parent.removeChild(this.host);
    }
  }

  /* --------------------------------------------------------------------------------
   * Lifecycle plumbing
   * ------------------------------------------------------------------------------ */

  /** Cancel the frame WITHOUT clearing intent. Visibility parking, not a stop. */
  private park(): void {
    if (this.rafId) cancelAnimationFrame(this.rafId);
    this.rafId = 0;
  }

  /** Re-arm the frame if frames are still wanted and the document is visible. */
  private resume(): void {
    if (this.destroyed || this.rafId !== 0) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    this.lastTs = nowMs();                       // a true first frame, not a lurch
    this.rafId = requestAnimationFrame(this.frame);
  }

  private start(): void { this.running = true; this.resume(); }

  private stop(): void { this.running = false; this.park(); }

  private readonly onVisibility = (): void => {
    // Park, never stop: `running` must survive the hidden interval, or returning to
    // the tab has nothing left that says frames were ever wanted.
    if (document.hidden) this.park();
    else if (this.running) this.resume();
  };

  private readonly onWinResize = (): void => { this.resizePending = true; };

  private readonly onTransitionEnd = (ev: Event): void => {
    const te = ev as TransitionEvent;
    // OVERLAY_CSS transitions BOTH opacity and transform, so transitionend fires twice.
    // Key on transform — the property whose compositor layer `will-change` exists to
    // hold — so `is-settled` (which drops will-change, tearing down a layer carrying a
    // full-viewport backdrop-filter) and display:none land only when the whole sweep is
    // genuinely over, not when whichever property happens to finish first does.
    // If a consumer stylesheet removes the transform transition entirely, the 600 ms
    // hideTimer in hide() is what still guarantees the loop stops.
    if (te.target !== this.host || te.propertyName !== 'transform') return;
    this.host.classList.add('is-settled');
    this.host.removeEventListener('transitionend', this.onTransitionEnd);
    if (!this.visible) this.finishHide();
  };

  private readonly finishHide = (): void => {
    if (this.hideTimer !== 0) { clearTimeout(this.hideTimer); this.hideTimer = 0; }
    if (this.visible || this.destroyed) return;
    this.host.style.display = 'none';
    this.stop();
  };

  /** Catch the window being dragged between monitors of different pixel density. */
  private watchDpr(): void {
    if (typeof matchMedia !== 'function') return;
    const d = window.devicePixelRatio || 1;
    this.dprMql = matchMedia(`(resolution: ${d}dppx)`);
    if (typeof this.dprMql.addEventListener === 'function') {
      this.dprMql.addEventListener('change', this.onDprChange);
    }
  }

  private unwatchDpr(): void {
    if (this.dprMql && typeof this.dprMql.removeEventListener === 'function') {
      this.dprMql.removeEventListener('change', this.onDprChange);
    }
    this.dprMql = null;
  }

  private readonly onDprChange = (): void => {
    this.resizePending = true;
    this.unwatchDpr();
    this.watchDpr();
  };

  /** The OS setting can be toggled mid-session. The sample count is derived from it in
   *  applyResize, so the change has to mark a resize, not just flip the flag. */
  private readonly onReduceMotionChange = (ev: MediaQueryListEvent): void => {
    this.reduceMotion = ev.matches;
    this.resizePending = true;
  };

  /* --------------------------------------------------------------------------------
   * Resize
   * ------------------------------------------------------------------------------ */

  private applyResize(): void {
    const ctx = this.ctx;
    if (!ctx) return;
    this.resizePending = false;

    const w = this.host.clientWidth || window.innerWidth || 1;
    const h = this.host.clientHeight || window.innerHeight || 1;
    // Cap DPR at 2 deliberately: this scene is fill-bound (large additive gradient
    // fills), so a 3x phone costs 2.25x the fill rate of a 2x one for density that is
    // invisible on a soft-edged glowing orb. FLAG: judgement call, not a measurement.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    this.cssW = w; this.cssH = h; this.renderDpr = dpr;
    const bw = Math.round(w * dpr), bh = Math.round(h * dpr);
    // Assigning canvas.width ALWAYS reallocates and clears the backing store, even when
    // the value is unchanged, so guard it: a ResizeObserver that fires without a real
    // size change must not flash the orb.
    if (this.canvas.width !== bw) this.canvas.width = bw;
    if (this.canvas.height !== bh) this.canvas.height = bh;
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    // Scale by the ACTUAL backing/CSS ratio, not the nominal dpr: at 1.5x on a 1707 px
    // box the store rounds to 2561, and using dpr would leave a sub-pixel scale error
    // that walks the orb's centre off the halo's over the width of the surface.
    ctx.setTransform(bw / w, 0, 0, bh / h, 0, 0);

    this.unitRadius = clamp(Math.min(w, h) * 0.18, 64, 260);

    // Sample count scales with the orb's pixel size: ~0.72 samples per radius pixel
    // keeps the chord near 9-10 px, which the Bezier fit renders as visually exact.
    // Forced even, and FLOORED AT MIN_SAMPLES rather than at 96 — the pixel heuristic
    // alone put an ordinary laptop at n = 114, where the band-limiter below zeroes the
    // top user octave outright. Reduced motion still pins the low-power 96: there the
    // shimmer is meant to be gone, so band-limiting it away is the correct outcome.
    const target = this.reduceMotion
      ? 96
      : clamp(Math.round(this.unitRadius * 0.72) & ~1, MIN_SAMPLES, MAX_SAMPLES);
    if (target !== this.n) {
      this.n = target;
      const d = TWO_PI / this.n;
      for (let i = 0; i < this.n; i++) {
        this.cosT[i] = Math.cos(i * d);
        this.sinT[i] = Math.sin(i * d);
      }
      // Re-derive the anti-alias gains: the sample count just changed, so the highest
      // lobe count the ring can carry without aliasing changed with it.
      const start = this.n / BAND_ROLLOFF_START;   // lobes: full weight at or below
      const end = this.n / BAND_ROLLOFF_END;       // lobes: fully faded at or above
      for (let j = 0; j < OCT; j++) {
        const L = OCT_LOBES[j];
        if (L <= start) this.bandGain[j] = 1;
        else if (L >= end) this.bandGain[j] = 0;
        else {
          const u = (end - L) / (end - start);
          this.bandGain[j] = u * u * (3 - 2 * u);  // smoothstep, C1 at both ends
        }
      }
    }
    // The backing store was reallocated and cleared, and gradients are authored in
    // unit space — they survive a resize untouched, so nothing else to redo.
  }

  /* --------------------------------------------------------------------------------
   * The frame
   * ------------------------------------------------------------------------------ */

  private readonly frame = (now: number): void => {
    if (this.destroyed) return;
    this.rafId = requestAnimationFrame(this.frame);

    let dt = (now - this.lastTs) / 1000;
    this.lastTs = now;
    if (dt <= 0) return;
    if (dt > 0.05) dt = 0.05;   // a tab stall or GC pause must not teleport the orb

    if (this.resizePending) this.applyResize();

    const ctx = this.ctx;
    if (!ctx || this.n < 3) return;   // n < 3 only before the first resize has landed

    /* --- 1. audio ------------------------------------------------------------- */
    this.audio.update(dt);
    const aUser = this.audio.user;
    const aAi = this.audio.ai;

    /* --- 2. state parameters -------------------------------------------------- */
    this.buildTargets();
    const cur = this.curP, tgt = this.tgtP, vel = this.velP;
    for (let p = 0; p < P_COUNT; p++) {
      const st = p === P_RADIUS && this.state === VisualizerState.Idle
        ? RADIUS_SMOOTH_IDLE
        : SMOOTH_TIME[p];
      cur[p] = smoothDamp(cur[p], tgt[p], vel, p, st, dt);
    }

    /* --- 3. phases -------------------------------------------------------------
     * THE BUG THIS AVOIDS: writing sin(2*pi*f*t) and then interpolating f. The
     * argument jumps by 2*pi*dF*t — at t = 40 s a dF of 0.02 Hz teleports the phase
     * by 5 radians and the orb visibly snaps. Integrating the phase with the CURRENT
     * rate means a rate change alters speed only, never position. Same argument for
     * the noise offsets and the swirl. Float64 is exact well past 2^40, so days of
     * uptime are fine.
     */
    const timeScale = cur[P_TIME_SCALE] * (this.reduceMotion ? 0.35 : 1);
    this.breathPhase += TWO_PI * cur[P_BREATH_RATE] * dt;
    if (this.breathPhase > TWO_PI) this.breathPhase -= TWO_PI;
    this.sweepPhase -= 0.55 * dt;
    if (this.sweepPhase <= -SWEEP_DASH_PERIOD) this.sweepPhase += SWEEP_DASH_PERIOD;

    // A shout shimmers faster, not merely wider.
    const userDriftScale = 0.6 + 0.8 * aUser;
    for (let j = 0; j < OCT; j++) {
      const ts = timeScale * (j >= FLUID_OCT ? userDriftScale : 1);
      this.offX[j] += OCT_DX[j] * ts * dt;
      this.offY[j] += OCT_DY[j] * ts * dt;
      this.swirl[j] += OCT_SWIRL[j] * ts * dt;
    }

    /* --- 4. radius ------------------------------------------------------------ */
    let R = this.unitRadius * cur[P_RADIUS] *
      (1 + cur[P_BREATH_DEPTH] * Math.sin(this.breathPhase) + cur[P_AI_SWELL] * aAi);
    if (R < 9) R = 9;                         // the Idle dot's absolute floor
    if (!(R > 0) || !Number.isFinite(R)) return;

    /* --- 5. geometry ---------------------------------------------------------- */
    // When the displacement cannot be seen a single arc is bit-identical to the spline
    // and ~40x cheaper.
    //
    // "A PERFECT CIRCLE AT REST" — the fBm SHAPE term is gated by audio energy.
    // Without the gate, Listening (noiseGain 0.045) sat at 8.2% peak-to-peak radial
    // deviation with the student silent, and Connecting at 4.7%: a permanently
    // crawling amoeba in the two states a call actually rests in, which is the one
    // thing the brief forbids. Breathing is untouched by this — P_BREATH_DEPTH scales
    // R uniformly above, so a breathing orb is still exactly a circle — and the gate
    // is a continuously damped quantity, so nothing snaps as audio arrives. It also
    // lets the `flat` fast path engage in every idle state, not just Idle.
    //
    // The cost is that the shape amplitude now tracks energy linearly: Speaking at
    // aAi 0.9 gives 0.085*1.81*0.9 = 0.139 (5.9% rms / 15.4% peak of R) rather than
    // 0.154. That lands ON the design budget of ~7.7% / 15.4% peak rather than over it.
    const energy = clamp(aAi + aUser, 0, 1);
    const noiseAmp = cur[P_NOISE_GAIN] * (1 + 0.9 * aAi) * energy;
    const userAmp = cur[P_USER_RIPPLE] * aUser * (this.reduceMotion ? 0.4 : 1);
    const flat = noiseAmp < 0.0015 && aUser + aAi < 0.01;
    if (!flat) this.computeRadii(noiseAmp, userAmp, aAi, aUser);

    /* --- 6. paint ------------------------------------------------------------- */
    this.paint(ctx, R, flat);
  };

  /**
   * Fill px[] / py[] for this frame.
   *
   *   r(theta) = 1
   *            + noiseGain*(1 + 0.9*aAi)*energy * FLUID(theta)
   *            + userRipple*aUser               * USER(theta)
   *
   * FLUID is a renormalised weighted sum of the four slow octaves; USER is the three
   * fast ones. The displacement is MULTIPLICATIVE on R, deliberately: when Idle
   * collapses R to 9 px the absolute wobble collapses with it, giving a genuinely
   * static dot with no special-casing.
   */
  private computeRadii(noiseAmp: number, userAmp: number, aAi: number, aUser: number): void {
    const n = this.n;
    const cosT = this.cosT, sinT = this.sinT;
    const cphi = this.cphi, sphi = this.sphi, wgt = this.wgt;

    // Hoist all trigonometry: cos(theta_i + phi_j) is expanded by the angle-sum
    // identity against the precomputed cosT/sinT tables, so the inner loop costs
    // 4 multiplies + 2 adds per octave per sample and calls no transcendental at all.
    for (let j = 0; j < OCT; j++) {
      cphi[j] = Math.cos(this.swirl[j]);
      sphi[j] = Math.sin(this.swirl[j]);
    }

    // AI biases the fBm toward the low octaves — that is what turns "wobble" into
    // "rolling". The Speaking state's lowOctaveBias folds into the same weights so the
    // shape is dominated by the 3.5- and 8-lobe layers even at low aAi.
    const low = 1 + 1.10 * aAi;
    const high = 1 + 0.25 * aAi;
    const lowBias = this.curP[P_LOW_BIAS];
    const bg = this.bandGain;
    wgt[0] = OCT_AMP[0] * low * lowBias * bg[0];
    wgt[1] = OCT_AMP[1] * low * (1 + (lowBias - 1) * 0.45) * bg[1];
    wgt[2] = OCT_AMP[2] * high * bg[2];
    wgt[3] = OCT_AMP[3] * high * bg[3];
    wgt[4] = OCT_AMP[4] * bg[4];
    wgt[5] = OCT_AMP[5] * bg[5];
    wgt[6] = OCT_AMP[6] * bg[6];

    /* RENORMALISE — and FLAG: by the L2 norm of the weights, not the L1 sum.
     *
     * The brief renormalises by sum(A*bias), which keeps the weighted MEAN correct.
     * But these octaves are decorrelated random fields, and for a weighted sum of
     * decorrelated variables it is the VARIANCE that adds:
     *
     *     var( sum(w_j * s_j) ) = sigma^2 * sum(w_j^2)
     *
     * so the standard deviation of the L1-normalised mean is sigma*||w||_2 / ||w||_1 —
     * which SHRINKS as octaves are added or as the bias flattens. Measured: the
     * L1-normalised FLUID had rms 0.260 and peaked at only 0.624, so the Speaking
     * displacement came out at 4.0% / 9.6% of R against the brief's own stated budget
     * of ~7.7% / 15.4%. The orb morphed half as much as its own design intended, and
     * the amount drifted whenever the AI bias moved — exactly the failure the brief's
     * renormalisation paragraph exists to prevent.
     *
     * Dividing by ||w||_2 instead makes the result's standard deviation exactly sigma
     * (~0.42 for this simplex) for ANY weight vector — any bias, any band-limiting,
     * any octave count. noiseGain and userRipple then mean one fixed thing forever.
     */
    const fSumSq = wgt[0] * wgt[0] + wgt[1] * wgt[1] + wgt[2] * wgt[2] + wgt[3] * wgt[3];
    const invW = fSumSq > 0 ? 1 / Math.sqrt(fSumSq) : 0;

    const doUser = aUser > 0.001 && userAmp > 0;
    const uSumSq = wgt[4] * wgt[4] + wgt[5] * wgt[5] + wgt[6] * wgt[6];
    const invU = uSumSq > 0 ? 1 / Math.sqrt(uSumSq) : 0;

    const nz = this.noise;
    for (let i = 0; i < n; i++) {
      const ci = cosT[i], si = sinT[i];

      let fluid = 0;
      for (let j = 0; j < FLUID_OCT; j++) {
        if (wgt[j] === 0) continue;                 // band-limited away at this size
        const cx = ci * cphi[j] - si * sphi[j];
        const sy = si * cphi[j] + ci * sphi[j];
        const rN = OCT_RN[j];
        fluid += wgt[j] * nz.noise(this.offX[j] + rN * cx, this.offY[j] + rN * sy);
      }
      fluid *= invW;

      let usr = 0;
      if (doUser) {
        for (let j = FLUID_OCT; j < OCT; j++) {
          if (wgt[j] === 0) continue;               // band-limited away at this size
          const cx = ci * cphi[j] - si * sphi[j];
          const sy = si * cphi[j] + ci * sphi[j];
          const rN = OCT_RN[j];
          usr += wgt[j] * nz.noise(this.offX[j] + rN * cx, this.offY[j] + rN * sy);
        }
        usr *= invU;
      }

      // User audio never touches R — it ripples the outline without inflating the orb,
      // which is exactly the perceptual split the design asks for. The linear aUser
      // gate (folded into userAmp) makes the term a mathematically exact zero in
      // silence, so there is no residual shimmer to notice.
      const r = 1 + noiseAmp * fluid + userAmp * usr;
      this.px[i] = r * ci;
      this.py[i] = r * si;
    }
  }

  /* --------------------------------------------------------------------------------
   * Painting
   * ------------------------------------------------------------------------------ */

  private paint(ctx: CanvasRenderingContext2D, R: number, flat: boolean): void {
    const cur = this.curP;
    // Clear in DEVICE space. Math.round(w*dpr) can exceed w*dpr (1707 x 1.5 = 2560.5
    // rounds up to a 2561 px store), so a CSS-space clearRect leaves the last device
    // column only half-covered — and because every pass composites with 'lighter',
    // residue there is re-brightened every frame instead of decaying, converging on a
    // permanent bright seam rather than fading.
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.restore();

    this.ensureGradients(ctx);

    const glow = cur[P_GLOW];
    const ring = cur[P_RING_ALPHA];
    const core = cur[P_CORE_ALPHA];

    ctx.save();
    ctx.translate(this.cssW * 0.5, this.cssH * 0.5);
    // UNIT-SPACE RENDERING. The path is built at radius ~1 and the CTM does the
    // sizing. That is what lets every CanvasGradient be authored once at the origin
    // with radius 1 and reused forever — no gradient allocation per frame. The price
    // is that lineWidth must be written as pixels / R.
    ctx.scale(R, R);
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    /* --- pass 1: halo ------------------------------------------------------- */
    if (this.gHalo) {
      ctx.globalAlpha = alpha01(glow);
      ctx.beginPath();
      ctx.arc(0, 0, 2.6, 0, TWO_PI);
      ctx.fillStyle = this.gHalo;
      ctx.fill();
    }

    /* --- pass 2: build the orb path once, reuse it for every paint ---------- */
    this.buildPath(ctx, flat);

    /* --- pass 3: body fill (scaled by coreAlpha, so Idle empties to a ring) -- */
    if (this.gBody && core > 0.002) {
      ctx.globalAlpha = alpha01(glow * core);
      ctx.fillStyle = this.gBody;
      ctx.fill();
    }

    /* --- pass 4: core bloom -------------------------------------------------- */
    if (this.gCore && core > 0.002) {
      ctx.globalAlpha = alpha01(glow * core);
      ctx.fillStyle = this.gCore;
      ctx.fill();
    }

    /* --- passes 5-8: the bloom ----------------------------------------------
     * NO shadowBlur here. shadowBlur forces a full-surface separable blur per draw
     * call: on a 400 px orb with five stroke passes that is 4-9 ms/frame and loses
     * 60 fps outright. It is also applied inconsistently under a scaled CTM across
     * engines (some scale the radius, some do not) — FLAG: spec'd around, not relied
     * on. The bloom below is a hand-rolled 4-tap approximation of a Gaussian falloff:
     * decreasing width, increasing brightness. Indistinguishable, ~1/20th the cost.
     */
    const strokeGlow = glow * ring;
    ctx.strokeStyle = this.cssMid;
    ctx.globalAlpha = alpha01(strokeGlow * 0.10);
    ctx.lineWidth = 14 / R;
    ctx.stroke();

    ctx.globalAlpha = alpha01(strokeGlow * 0.20);
    ctx.lineWidth = 6 / R;
    ctx.stroke();

    ctx.strokeStyle = this.cssCore;
    ctx.globalAlpha = alpha01(strokeGlow * 0.85);
    ctx.lineWidth = 2 / R;
    ctx.stroke();

    ctx.strokeStyle = 'rgb(255,255,255)';
    ctx.globalAlpha = alpha01(strokeGlow * 0.55);
    ctx.lineWidth = 1 / R;
    ctx.stroke();

    /* --- pass 9: specular sweep ----------------------------------------------
     * A dashed stroke whose dash offset travels: one highlight arc sliding round the
     * rim. Sells "glassy" for one extra stroke and zero extra geometry. Skipped under
     * reduced motion. */
    if (!this.reduceMotion) {
      ctx.setLineDash(SWEEP_DASH as number[]);
      ctx.lineDashOffset = this.sweepPhase;
      ctx.globalAlpha = alpha01(strokeGlow * 0.30);
      ctx.lineWidth = 3 / R;
      ctx.stroke();
      // No reset here. The dash list and the dash offset are both part of the canvas
      // DRAWING STATE, so the ctx.restore() at the end of this method already undoes
      // them; resetting explicitly was a second engine-side sequence conversion every
      // frame for nothing. Pass 10 below is a fill, which a dash list does not touch.
    }

    /* --- pass 10: core dot ---------------------------------------------------- */
    if (this.gDot && core > 0.002) {
      ctx.globalAlpha = alpha01(glow * core);
      ctx.beginPath();
      ctx.arc(-0.05, -0.06, 0.30, 0, TWO_PI);
      ctx.fillStyle = this.gDot;
      ctx.fill();
    }

    ctx.restore();
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  }

  /* --------------------------------------------------------------------------------
   * 7. THE CLOSED C1 CURVE — uniform Catmull-Rom converted to cubic Bezier
   * ------------------------------------------------------------------------------
   * For the segment from P[i1] to P[i2], with neighbours P[i0] and P[i3]:
   *
   *   C1 = P[i1] + s*(P[i2] - P[i0])/3
   *   C2 = P[i2] - s*(P[i3] - P[i1])/3
   *
   * WHY THIS IS C1 (the proof, not the claim):
   *   The incoming segment's end tangent is
   *       3*(P_end - C2) = 3*(P[i2] - (P[i2] - s*(P[i3]-P[i1])/3)) = s*(P[i3]-P[i1]).
   *   The next segment (whose i1' = i2, i0' = i1, i2' = i3) has start tangent
   *       3*(C1' - P[i2]) = 3*(s*(P[i3]-P[i1])/3)                 = s*(P[i3]-P[i1]).
   *   Identical VECTORS, not merely identical directions — so the join is C1, not just
   *   G1, under uniform parameterisation. Curvature (C2) is discontinuous at joints,
   *   but a CORNER requires a tangent discontinuity and there is none. That is why the
   *   outline cannot show a point however violently the noise moves it.
   *
   * THE CLASSIC BUG: emitting n-1 segments and letting closePath() join the ends. That
   * inserts a straight chord and produces exactly one visible corner, always at the
   * same angle, which reads as a defect. All n segments are emitted, with wrapped
   * indices; closePath() is then a no-op that only guarantees the stroke closes.
   *
   * WHY lineTo FAILS: at n = 192 on a 300 px orb the chord is ~9.8 px, so a perfect
   * circle already shows a 1.875-degree exterior angle at every vertex under a 2 px hot
   * stroke with additive glow. Worse, the 42-lobe user octave displaces ADJACENT
   * samples in opposite directions, spiking the local exterior angle to 20-35 degrees.
   * Polyline geometry produces the forbidden sharp points by construction.
   * ------------------------------------------------------------------------------ */
  private buildPath(ctx: CanvasRenderingContext2D, flat: boolean): void {
    ctx.beginPath();
    if (flat) {
      ctx.arc(0, 0, 1, 0, TWO_PI);
      // A full-circle arc() leaves an OPEN subpath whose endpoints coincide at
      // theta = 0, and the spec then applies lineCap ('round' here) at both ends —
      // under five additive stroke passes that is a stack of round caps at 3 o'clock,
      // in the state the overlay spends most of its life in. Chrome/Skia happens to
      // close it (measured: no artefact); Firefox and WebKit guarantee nothing.
      // closePath() makes the seam a lineJoin instead, a no-op on a smooth tangent.
      ctx.closePath();
      return;
    }
    const n = this.n, px = this.px, py = this.py, a = CR_A;
    ctx.moveTo(px[0], py[0]);
    for (let i = 0; i < n; i++) {
      const i0 = (i - 1 + n) % n;
      const i1 = i;
      const i2 = (i + 1) % n;
      const i3 = (i + 2) % n;
      // Locals, never arrays: consumed immediately by bezierCurveTo, so the hot loop
      // allocates nothing at all.
      const c1x = px[i1] + a * (px[i2] - px[i0]);
      const c1y = py[i1] + a * (py[i2] - py[i0]);
      const c2x = px[i2] - a * (px[i3] - px[i1]);
      const c2y = py[i2] - a * (py[i3] - py[i1]);
      ctx.bezierCurveTo(c1x, c1y, c2x, c2y, px[i2], py[i2]);
    }
    ctx.closePath();
  }

  /* --------------------------------------------------------------------------------
   * Targets and gradients
   * ------------------------------------------------------------------------------ */

  /** Copy the active state row into tgtP, applying the error tint if set. Rebuilt
   *  every frame so a tint toggle is picked up without any extra bookkeeping — and
   *  because tgtP only feeds smoothDamp, even an instant target change is smooth. */
  private buildTargets(): void {
    this.tgtP.set(this.baseP);
    if (this.errorTint) {
      const t = this.tgtP;
      t[P_MID_R] = ERR_MID_R; t[P_MID_G] = ERR_MID_G; t[P_MID_B] = ERR_MID_B;
      t[P_EDGE_R] = ERR_EDGE_R; t[P_EDGE_G] = ERR_EDGE_G; t[P_EDGE_B] = ERR_EDGE_B;
    }
  }

  /**
   * QUANTISED DIRTY CACHE. Hoisted gradients have fixed colour stops, but the palette
   * interpolates during a transition. Each channel is rounded to the nearest 4 and the
   * gradient set is rebuilt only when that key changes: in steady state (the
   * overwhelming majority of frames) that is ZERO allocations, and a 0.6 s transition
   * creates a couple of dozen gradient objects in total.
   *
   * The two keys exist because 9 channels * 6 bits = 54 bits does not fit exactly in a
   * float64 integer; 4 channels (24 bits) + 5 channels (30 bits) both do.
   *
   * Note that varying ALPHA never touches this cache — alpha is applied through
   * ctx.globalAlpha, and the stroke colours are cached rgb() strings, so no colour
   * string is ever built inside a frame that did not change the palette.
   */
  private ensureGradients(ctx: CanvasRenderingContext2D): void {
    const c = this.curP;
    // `quant4` is module-level, not a local arrow function: defining a closure here
    // would allocate one object per frame — exactly the thing this cache exists to
    // avoid.
    const q = quant4;
    // The core bloom and the core dot are pure white ramps: they do not depend on the
    // palette at all, so they are authored exactly once for the lifetime of the
    // visualizer and are deliberately outside the dirty-key path below.
    if (!this.gCore) {
      const core = ctx.createRadialGradient(-0.18, -0.24, 0.0, -0.18, -0.24, 0.55);
      core.addColorStop(0.00, 'rgba(255,255,255,0.55)');
      core.addColorStop(0.55, 'rgba(255,255,255,0.10)');
      core.addColorStop(1.00, 'rgba(255,255,255,0.00)');
      this.gCore = core;

      const dot = ctx.createRadialGradient(-0.05, -0.06, 0, -0.05, -0.06, 0.30);
      dot.addColorStop(0.00, 'rgba(255,255,255,0.90)');
      dot.addColorStop(1.00, 'rgba(255,255,255,0.00)');
      this.gDot = dot;
    }

    const cr = q(c[P_CORE_R]), cg = q(c[P_CORE_G]), cb = q(c[P_CORE_B]);
    const mr = q(c[P_MID_R]), mg = q(c[P_MID_G]), mb = q(c[P_MID_B]);
    const er = q(c[P_EDGE_R]), eg = q(c[P_EDGE_G]), eb = q(c[P_EDGE_B]);
    const keyA = ((cr * 64 + cg) * 64 + cb) * 64 + mr;
    const keyB = (((mg * 64 + mb) * 64 + er) * 64 + eg) * 64 + eb;
    if (keyA === this.gradKeyA && keyB === this.gradKeyB && this.gBody) return;
    this.gradKeyA = keyA;
    this.gradKeyB = keyB;

    const R0 = c[P_CORE_R], G0 = c[P_CORE_G], B0 = c[P_CORE_B];
    const R1 = c[P_MID_R], G1 = c[P_MID_G], B1 = c[P_MID_B];
    const R2 = c[P_EDGE_R], G2c = c[P_EDGE_G], B2 = c[P_EDGE_B];

    this.cssCore = rgb(R0, G0, B0);
    this.cssMid = rgb(R1, G1, B1);

    // Halo: a wide, very faint atmosphere out to 2.6 unit radii. Alphas are tiny
    // because it is drawn additively over whatever is beneath.
    const halo = ctx.createRadialGradient(0, 0, 0.20, 0, 0, 2.60);
    halo.addColorStop(0.00, rgba(R1, G1, B1, 0.10));
    halo.addColorStop(0.35, rgba(mix(R1, R2, 0.5), mix(G1, G2c, 0.5), mix(B1, B2, 0.5), 0.070));
    halo.addColorStop(0.70, rgba(R2, G2c, B2, 0.028));
    halo.addColorStop(1.00, rgba(R2, G2c, B2, 0.0));
    this.gHalo = halo;

    // Body: offset toward the upper-left so the orb reads as lit from that side.
    const body = ctx.createRadialGradient(-0.22, -0.28, 0.05, 0, 0, 1.15);
    body.addColorStop(0.00, rgba(R0, G0, B0, 0.95));
    body.addColorStop(0.18, rgba(mix(R1, R0, 0.45), mix(G1, G0, 0.45), mix(B1, B0, 0.45), 0.72));
    body.addColorStop(0.45, rgba(R1, G1, B1, 0.50));
    body.addColorStop(0.72, rgba(mix(R1, R2, 0.7), mix(G1, G2c, 0.7), mix(B1, B2, 0.7), 0.28));
    body.addColorStop(1.00, rgba(R2 * 0.55, G2c * 0.55, B2 * 0.72, 0.06));
    this.gBody = body;

  }
}

/* =====================================================================================
 * 8. TINY DOM / COLOUR UTILITIES
 * ===================================================================================== */

function mix(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Quantise a 0..255 channel to a 6-bit bucket (nearest 4) for the gradient key. */
function quant4(v: number): number {
  return clamp(Math.round(v / 4), 0, 63);
}

function rgb(r: number, g: number, b: number): string {
  return `rgb(${Math.round(clamp(r, 0, 255))},${Math.round(clamp(g, 0, 255))},${Math.round(clamp(b, 0, 255))})`;
}

function rgba(r: number, g: number, b: number, a: number): string {
  return `rgba(${Math.round(clamp(r, 0, 255))},${Math.round(clamp(g, 0, 255))},${Math.round(clamp(b, 0, 255))},${a})`;
}

/** Inject the overlay stylesheet once per document. */
function injectOverlayStyle(): void {
  if (typeof document === 'undefined') return;
  if (document.getElementById(OVERLAY_STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = OVERLAY_STYLE_ID;
  el.textContent = OVERLAY_CSS;
  document.head.appendChild(el);
}
