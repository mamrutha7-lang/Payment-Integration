/**
 * The interview room — REEP's realtime AI mock interviewer, as ONE component
 * rendered in two places.
 *
 * Press Start, the microphone opens, a WebSocket to /api/interview carries
 * 24 kHz PCM both ways, and an interviewer calibrated to a Tier-1
 * multinational's campus round asks one question at a time and critiques the
 * answer. Everything audio lives in InterviewService; this component owns the
 * stage (the orb, the status, the captions, the phase stepper, the clock, the
 * mic meter), the track picker, the consent dialog, the transcript and the
 * practice report.
 *
 * It was the body of features/assistant/assistant.component.ts. That page
 * still exists at /student/assistant (the deep link the Interviews screen and
 * the landing's Elevate card use) and renders this component in `page` mode
 * under its heading and the saved-conversation panel. The floating dock in the
 * shell (layout/agent-dock.component.ts) renders it in `dock` mode beside the
 * typed REEP Agent, so an interview can be started from any screen. One
 * component, because a consent dialog, a close-code table and a scorecard
 * parse maintained twice would disagree on the first fix to either.
 *
 * THE DOCK SERVICE MIRROR. The orb in the shell pulses while an interview is
 * live and shows its clock, and it must not import InterviewService to learn
 * that (the service and the visualizer are ~5 000 lines that the initial
 * bundle does not carry — the dock `@defer`s this component). So an effect
 * below writes a three-word state and the clock into AgentDockService, and
 * ngOnDestroy clears them, which is also the moment the microphone is
 * released. The room is the ONLY writer.
 *
 * WHAT IS NOT HERE. The saved conversation (GET /api/agent/history) and its
 * Clear control belong to the page: they are the text agent's thread, which
 * the interviewer's turns are persisted into server-side, and the dock shows
 * that thread on its other tab.
 *
 * CONSENT IS A ROW ON THE SERVER, NOT A FLAG IN THIS BROWSER. It used to be a
 * localStorage key nothing on the server ever read: on a shared lab PC that
 * grant belonged to the machine rather than the person. Now `GET
 * /api/interview/consent` says which version the server is asking for and
 * whether this student holds a live grant for it; `POST` records one; and the
 * interview socket itself refuses (4013) without one. Accepting FAILS CLOSED:
 * if the grant cannot be recorded, the thing it authorises does not run.
 *
 * Status as TEXT AND COLOUR, never colour alone (AGENTS.md). The orb is
 * decoration; the pill beside it carries the words.
 */

import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../environments/environment';
import { AgentDockService, DockLiveState } from '../../core/agent-dock.service';
import { AuthService } from '../../core/auth.service';
import { InterviewService, InterviewState } from '../../core/interview.service';
// Shared with the interview-history screen rather than duplicated. The two
// screens render the SAME scorecard from two sources, and the calibration copy
// on it is the thing §5.4 traded the score's visibility for — maintaining that
// sentence in two templates is how one of them ends up without it.
import {
  InterviewReportCardComponent,
  ReportCardView,
} from '../../features/student/interviews/interview-report-card.component';
import { MockAudioStreamController, VisualizerState, VoiceVisualizer } from '../voice-visualizer';
import {
  AudioRoute,
  defaultDeviceLabels,
  readStoredRoute,
  routeFromDeviceLabels,
  storeRoute,
} from './audio-route';
import { grantMatchesPolicy, recordingLabel } from './consent-sync';

/** One consent grant, verbatim from ConsentOut in
 *  apps/api-py/app/routers/interview_records.py. */
interface ConsentGrant {
  id: string;
  version: string;
  scope_live_ai: boolean;
  scope_store_transcript: boolean;
  scope_store_audio: boolean;
  granted_at: string;
}

/**
 * GET /api/interview/policy, verbatim from StudentPolicyOut (B6.1 / B5.3 / B6.4).
 *
 * THE STUDENT DOES NOT CHOOSE ANY OF THIS. The two storage scopes are the
 * COLLEGE's decision, taken once in the console for everybody on a course, so
 * the consent panel states them rather than offering them — which is why there
 * is no audio checkbox and no "Withdraw" control: `DELETE /api/interview/consent`
 * answers 405 for everyone.
 */
interface InterviewPolicyCard {
  consent_version: string;
  provider_label: string;
  policy: {
    store_transcript: boolean;
    store_audio: boolean;
    retention_days: number;
    daily_cap: number;
    attempt_cap: number;
    time_limit_seconds: number;
    source: string;
  };
  usage: {
    completed: number;
    attempts: number;
    daily_cap: number;
    attempt_cap: number;
    reset_applied: boolean;
  };
  acknowledged: boolean;
  /** The operator's `INTERVIEW_RECORDING_ENABLED` — the first of the three
   *  gates on a recording. Optional because a server older than the field
   *  omits it, and absent must read as "unknown", never as "off". */
  recording_enabled_on_server?: boolean;
  /** B5.3: the track this student's batch implies, or null. NULL is a real
   *  answer and the picker stays — it must never be filled with a guess. */
  default_track: string | null;
  tracks: { code: string; label: string }[];
}

/**
 * GET /api/interview/consent, verbatim from ConsentStateOut.
 *
 * `version` must be read from here rather than baked into this bundle: POST
 * refuses a version string it does not know, precisely so a stale cached SPA
 * cannot grant against copy the student never saw.
 */
interface ConsentState {
  version: string;
  consent: ConsentGrant | null;
  /** WHO receives the student's voice, in words, from the server — because the
   *  browser cannot know which engine is running. */
  provider?: string;
}

/** The pill's wording. The pill carries colour; this carries meaning, and both
 *  are always set together — colour alone is never a status in this repo. */
const STATE_LABELS: Record<InterviewState, string> = {
  idle: 'Not connected',
  connecting: 'Connecting…',
  ready: 'Connected',
  listening: 'Listening',
  thinking: 'Thinking…',
  speaking: 'Interviewer speaking',
  ended: 'Interview ended',
  error: 'Problem',
};

const STATE_CAPTIONS: Record<InterviewState, string> = {
  idle: 'Pick a round, then press Start when you are ready.',
  connecting: 'Setting up your microphone and connecting…',
  ready: 'Connected. The interviewer will open shortly.',
  listening: 'Go ahead — the interviewer is listening.',
  // The next question is created only AFTER your answer has been transcribed,
  // so this wait is real and it grows with the length of the answer. Saying
  // what is happening is what stops the silence reading as a hung page.
  thinking: 'You have stopped speaking. The interviewer replies once it has your whole answer.',
  speaking: 'Listen to the question. You can interrupt at any time.',
  ended: 'The interview has ended. Start another whenever you like.',
  error: 'Something went wrong. See the message above.',
};

/** Past this many seconds in `thinking`, the affordance stops naming what it is
 *  doing and starts explaining WHY it is taking this long. */
const THINKING_LONG_AFTER_S = 6;

/**
 * The Specialization Matrix, as offered on the picker. `key: null` is the
 * generic interview that predates the matrix; the four keyed rows mirror
 * SPECIALIZATIONS in apps/api-py/app/interview_matrix.py, which is the source
 * of truth — the server refuses a key it does not know (close 4010), so a row
 * added here without the backend row fails loudly rather than silently.
 *
 * `interviewer` and `round` are the briefing card's words: who is on the other
 * side of the table and which round this rehearses. They mirror each row's
 * `persona` server-side and are copy, not behaviour — the persona the model
 * plays is composed on the server from the key alone.
 */
export interface SpecializationOption {
  key: string | null;
  label: string;
  short: string;
  interviewer: string;
  round: string;
  blurb: string;
}

export const SPECIALIZATION_OPTIONS: readonly SpecializationOption[] = [
  {
    key: null,
    label: 'General interview',
    short: 'General',
    interviewer: 'Campus panel interviewer',
    round: 'General placement round',
    blurb: 'Placement readiness across the board — no single track, and no scored report.',
  },
  {
    key: 'hr',
    label: 'Human Resources (HR)',
    short: 'HR',
    interviewer: 'Chief Human Resources Officer',
    round: 'HR & behavioural round',
    blurb: 'STAR method, labour law, conflict resolution, talent acquisition.',
  },
  {
    key: 'dm',
    label: 'Digital Marketing (DM)',
    short: 'Marketing',
    interviewer: 'Chief Marketing Officer',
    round: 'Growth & marketing round',
    blurb: 'CAC/LTV, ROAS, SEO/SEM, A/B testing, funnels, brand positioning.',
  },
  {
    key: 'ba',
    label: 'Business Analytics (BA)',
    short: 'Analytics',
    interviewer: 'Director of Analytics',
    round: 'Technical analytics round',
    blurb: 'SQL/Python, data modelling, predictive analytics, visualisation.',
  },
  {
    key: 'fa',
    label: 'Financial Analytics (FA)',
    short: 'Finance',
    interviewer: 'Managing Director, Finance',
    round: 'Finance & valuation round',
    blurb: 'DCF modelling, financial ratios, risk, valuation, M&A.',
  },
];

/** The arc, in order, for the stepper. Every InterviewPhase member in
 *  apps/api-py/app/interview_matrix.py is here or is `ended`. */
const PHASES: readonly { key: string; label: string }[] = [
  { key: 'opening', label: 'Opening' },
  { key: 'probing', label: 'Probing' },
  { key: 'deep_dive', label: 'Deep dive' },
  { key: 'wrap_up', label: 'Wrap-up' },
];

export interface PhaseStep {
  key: string;
  label: string;
  status: 'done' | 'current' | 'todo';
}

/**
 * InterviewState -> the orb's four visual states.
 *
 * `thinking` maps to Connecting rather than Listening on purpose: Connecting is
 * the dim, slightly deflated, slow-breath row, which reads as considering —
 * and it is visibly distinct from Listening, so the student can tell at a
 * glance whether it is their turn. `error` keeps the Idle geometry and asks
 * for the error tint instead of a fifth state; the pill still says "Problem".
 */
const ORB_STATE: Record<InterviewState, VisualizerState> = {
  idle: VisualizerState.Idle,
  connecting: VisualizerState.Connecting,
  ready: VisualizerState.Listening,
  listening: VisualizerState.Listening,
  thinking: VisualizerState.Connecting,
  speaking: VisualizerState.Speaking,
  ended: VisualizerState.Idle,
  error: VisualizerState.Idle,
};

/** What the orb in the shell is told. Coarser than InterviewState on purpose:
 *  it has one pulse and one clock to show. */
const DOCK_LIVE: Record<InterviewState, DockLiveState> = {
  idle: null,
  connecting: 'connecting',
  ready: 'listening',
  listening: 'listening',
  thinking: 'thinking',
  speaking: 'speaking',
  ended: null,
  error: null,
};

export type InterviewRoomVariant = 'page' | 'dock';

@Component({
  selector: 'app-interview-room',
  standalone: true,
  imports: [RouterLink, InterviewReportCardComponent],
  templateUrl: './interview-room.component.html',
  styleUrl: './interview-room.component.scss',
  host: { '[class.is-dock]': 'variant() === "dock"' },
})
export class InterviewRoomComponent implements AfterViewInit, OnDestroy {
  private readonly interview = inject(InterviewService);
  private readonly dock = inject(AgentDockService);
  /** Only to know whether this is a student. */
  private readonly auth = inject(AuthService);

  readonly variant = input<InterviewRoomVariant>('page');

  // -- interview state, straight through from the service ---------------- //
  readonly state = this.interview.state;
  readonly notice = this.interview.notice;
  readonly lines = this.interview.lines;
  readonly micLevel = this.interview.micLevel;
  readonly clockLabel = this.interview.clockLabel;
  readonly capLabel = this.interview.capLabel;
  readonly clockWarning = this.interview.clockWarning;
  readonly active = this.interview.active;
  readonly secureContext = this.interview.secureContext;
  /** The matrix row and phase of the LIVE interview, as the relay reports them. */
  readonly liveSpecialization = this.interview.specialization;
  readonly livePhase = this.interview.phase;
  readonly thinkingSlow = this.interview.thinkingSlow;
  readonly thinkingSeconds = this.interview.thinkingSeconds;
  /** The verdict has been spoken and the scorecard is being written. */
  readonly composingReport = this.interview.composingReport;

  /** The picker's selection, applied to the next Start. null = General. */
  readonly selectedSpecialization = signal<string | null>(null);
  readonly specializationOptions = SPECIALIZATION_OPTIONS;

  /**
   * Speaker or earphones (audio-route.ts). Speaker arms the echo gate that
   * keeps the interviewer's own voice off the uplink; earphones switch it off,
   * so a breath on a headset mic can no longer pause the interviewer. Takes
   * effect on the gate at once, mid-interview included; the microphone's
   * auto-gain follows at the next Start.
   *
   * READ FROM the service rather than held here: the page and the dock can
   * both render this component, and one switch must not show a different
   * answer from the other.
   */
  readonly audioRoute = computed<AudioRoute>(() =>
    this.interview.echoSuppression() ? 'speaker' : 'earphones',
  );
  private readonly onDeviceChange = () => void this.preselectRoute();

  // -- consent, which is a server row (see the file header) --------------- //
  readonly showConsent = signal(false);
  readonly consentVersion = signal<string | null>(null);
  /** The provider named in the consent copy, from the server. The default is
   *  deliberately vague rather than a guessed company name. */
  readonly consentProvider = signal<string>("the interviewer's speech model");
  readonly consent = signal<ConsentGrant | null>(null);
  readonly policy = signal<InterviewPolicyCard | null>(null);
  readonly consentBusy = signal(false);
  readonly consentError = signal<string | null>(null);

  /** Mock interviews are a student feature; the backend refuses anyone else —
   *  except the Main Admin's REHEARSAL, below. */
  readonly isStudent = computed(() => this.auth.session()?.role === 'STUDENT');

  /**
   * THE MAIN ADMIN'S REHEARSAL (2026-09-16). The office can sit the interview
   * to hear the persona and see the scorecard arrive, and the server stores
   * NOTHING for it — no conversation, no interview record, no turns, no
   * report row, no recording, no consent row (`_is_rehearsal` in
   * app/routers/interview.py is the record of why). So this screen asks for
   * no consent (there is no row to write one against), shows no "saved to
   * your past interviews" link (nothing was saved), and says so in one line
   * above the stage. The socket is the gate; this flag only shapes the copy.
   */
  readonly isRehearsal = computed(() => this.auth.session()?.role === 'ADMIN');

  /** Who may press Start at all: a student, or the Main Admin rehearsing. */
  readonly canSit = computed(() => this.isStudent() || this.isRehearsal());

  /** The briefing card: the option selected, or the live one once running. */
  readonly briefing = computed<SpecializationOption>(() => {
    const key = this.selectedSpecialization();
    return SPECIALIZATION_OPTIONS.find((o) => o.key === key) ?? SPECIALIZATION_OPTIONS[0];
  });

  /** The pill's words: the service's own detail when it has one, else the
   *  state's label. */
  readonly statusLabel = computed(() => this.interview.detail() ?? STATE_LABELS[this.state()]);

  /** The caption under the pill, overridden for the scorecard wait — the
   *  `thinking` caption describes the wrong wait entirely there. */
  readonly statusCaption = computed(() =>
    this.composingReport() && this.state() === 'thinking'
      ? 'Your answers are all in. The interviewer is writing your practice report — you do not need to say anything.'
      : STATE_CAPTIONS[this.state()],
  );

  readonly showWait = computed(
    () => this.state() === 'thinking' && (this.thinkingSlow() || this.composingReport()),
  );

  /** The four-stage arc as a stepper. Before the relay names a phase every
   *  step is `todo`; after `ended` every step is `done`. */
  readonly steps = computed<PhaseStep[]>(() => {
    const phase = this.livePhase();
    const at = phase === 'ended' ? PHASES.length : PHASES.findIndex((p) => p.key === phase);
    return PHASES.map((p, i) => ({
      key: p.key,
      label: p.label,
      status: at < 0 ? 'todo' : i < at ? 'done' : i === at ? 'current' : 'todo',
    }));
  });

  /** The live caption: the newest interviewer line, and the student's own
   *  newest line while it is still being transcribed. The transcript below
   *  keeps everything; this is the one line a person looks at mid-answer. */
  readonly caption = computed(() => {
    const lines = this.lines();
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i];
      if (line.role === 'interviewer' && line.text.trim()) return line;
    }
    return null;
  });

  /** Start is offered from every terminal state, and only from a terminal one. */
  readonly canStart = computed(() => !this.active() && this.canSit() && this.secureContext);

  /** Mic level as a 0..100 integer, for the meter's aria-valuenow and width. */
  readonly micPercent = computed(() => Math.round(this.micLevel() * 100));

  readonly thinkingHint = computed(() => {
    if (this.composingReport()) return 'Writing your practice report — this takes a few seconds.';
    return this.thinkingSeconds() >= THINKING_LONG_AFTER_S
      ? 'Still transcribing — a long answer takes longer. You have not been missed.'
      : 'Transcribing your answer…';
  });

  /**
   * This session's scorecard in the shared card's shape, or null before one
   * arrives. `available: false` maps onto the card's status vocabulary rather
   * than being flattened into "no report": the interview COMPLETED and only the
   * scorecard did not, and the card has a distinct sentence for each cause.
   */
  readonly reportView = computed<ReportCardView | null>(() => {
    const result = this.interview.report();
    if (result === null) return null;
    const r = result.report;
    return {
      status: result.available ? 'ok' : (result.reason ?? 'unavailable'),
      overall: r?.overall ?? null,
      communication: r?.communication ?? null,
      domain: r?.domain ?? null,
      structure: r?.structure ?? null,
      strengths: r?.strengths ?? [],
      improvements: r?.improvements ?? [],
      drill: r?.drill ?? '',
      summary: r?.summary ?? '',
      generatedAt: null,
    };
  });

  /** The word after "recording" on the Start line: what the NEXT interview
   *  will do, read from the policy and the operator's switch, never from the
   *  consent row alone (consent-sync.ts says why). */
  readonly recordingLabel = computed(() => recordingLabel(this.consent(), this.policy()));

  /** "12 Aug 2026" for the live grant, or null. */
  readonly consentGrantedLabel = computed(() => {
    const row = this.consent();
    if (!row) return null;
    const d = new Date(row.granted_at);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  });

  private readonly orbCanvas = viewChild<ElementRef<HTMLCanvasElement>>('orbCanvas');
  private readonly transcript = viewChild<ElementRef<HTMLElement>>('transcript');
  private readonly consentCard = viewChild<ElementRef<HTMLElement>>('consentCard');
  /** What had focus before the consent dialog opened, so it can be restored. */
  private consentReturnFocus: HTMLElement | null = null;

  /** The orb's amplitude source. DOM-free, so it is safe in a field
   *  initialiser; the canvas-bound visualizer is built in ngAfterViewInit. */
  private readonly orb = new MockAudioStreamController();
  private visualizer: VoiceVisualizer | null = null;

  constructor() {
    void this.loadConsent();
    void this.loadPolicy();

    // --- speaker or earphones ------------------------------------------- //
    const stored = readStoredRoute();
    if (stored) this.applyRoute(stored);
    else void this.preselectRoute();
    // Earphones plugged in or pulled out: follow them, unless the student
    // has chosen.
    navigator.mediaDevices?.addEventListener?.('devicechange', this.onDeviceChange);

    // --- drive the orb ------------------------------------------------- //
    // Injection only moves a damper TARGET; the visualizer's own render loop
    // advances the damper, so publishing at the feeds' own rates is right.
    effect(() => {
      this.orb.injectUserAudioAmplitude(this.orb.mapRmsToAmplitude(this.interview.userRms()));
    });
    effect(() => {
      this.orb.injectAiAudioAmplitude(this.orb.mapRmsToAmplitude(this.interview.aiRms()));
    });
    effect(() => {
      const s = this.state();
      this.visualizer?.setState(ORB_STATE[s], s === 'error');
    });

    // --- keep the live transcript pinned to the newest line ------------- //
    effect(() => {
      this.lines();
      const el = this.transcript()?.nativeElement;
      if (el) queueMicrotask(() => (el.scrollTop = el.scrollHeight));
    });

    // --- tell the shell's orb what is happening ------------------------- //
    effect(() => {
      this.dock.reportLive(DOCK_LIVE[this.state()], this.clockLabel());
    });

    // --- move focus INTO the consent dialog when it opens --------------- //
    effect(() => {
      const card = this.consentCard()?.nativeElement;
      if (this.showConsent() && card) queueMicrotask(() => card.focus());
    });
  }

  ngAfterViewInit(): void {
    const canvas = this.orbCanvas()?.nativeElement;
    if (!canvas) return;
    // The canvas's parent already carries .rvz-overlay, so the constructor
    // ADOPTS it instead of wrapping the canvas in a new full-screen div — which
    // is what lets the component stylesheet lay the orb out inline.
    this.visualizer = new VoiceVisualizer(canvas, this.orb);
    const s = this.state();
    this.visualizer.setState(ORB_STATE[s], s === 'error');
    this.visualizer.show();
  }

  /**
   * Leaving must release the microphone.
   *
   * InterviewService is root-provided, so it OUTLIVES this component. Without
   * this, closing the dock or navigating away left a live socket publishing
   * the student's voice with no visible indication and no control to stop it.
   * AGENTS.md rule 1 is about student data not leaving unbidden, and a hot mic
   * is the most literal form of it.
   */
  ngOnDestroy(): void {
    navigator.mediaDevices?.removeEventListener?.('devicechange', this.onDeviceChange);
    this.interview.end('Left the interview room');
    this.dock.reportLive(null, null);
    // destroy() FIRST, dispose() second — see MockAudioStreamController.dispose.
    this.visualizer?.destroy();
    this.visualizer = null;
    this.orb.dispose();
  }

  // ------------------------------------------------------------------ //
  // Controls                                                           //
  // ------------------------------------------------------------------ //

  /** Start / End. Not awaited from the template: the click handler must return
   *  promptly so the gesture is not held open across the getUserMedia prompt. */
  toggle(): void {
    if (this.active()) {
      this.interview.end();
      return;
    }
    if (!this.canStart()) return;
    // A rehearsal has no consent row to write — the server keeps nothing it
    // could be consented to — so it starts straight away.
    if (this.isRehearsal()) {
      this.begin();
      return;
    }
    // A standing grant is a COPY of the college's policy on the day it was
    // made, and the policy can have changed since (consent-sync.ts). Start on
    // it only while the policy still says what it says; otherwise the terms
    // are shown again, and "I agree" posts the acknowledgement the server then
    // supersedes. Judged on the card already loaded — nothing is fetched here,
    // because start() must run inside the click's user gesture — and the card
    // is re-read in the background so the NEXT press judges fresh data. With
    // no card at all (the fetch failed) the row is the only fact there is,
    // and the server enforces the policy regardless.
    const grant = this.consent();
    const card = this.policy();
    if (grant && (card === null || grantMatchesPolicy(grant, card.policy))) {
      this.begin();
      void this.loadPolicy();
      return;
    }
    // No live grant for the current terms, or one that no longer matches the
    // policy — show the disclosure before touching the microphone.
    this.openConsent();
  }

  /** Start, from inside the click's gesture (start() must run in it). Once
   *  it has the microphone, device names are readable, so the route is
   *  preselected again for a student who has not chosen one. */
  private begin(): void {
    void this.interview.start(this.selectedSpecialization()).then(() => this.preselectRoute());
  }

  /** The Speaker / Earphones switch. Remembered on this device, and from
   *  then on never overridden by a device name. */
  chooseRoute(route: AudioRoute): void {
    storeRoute(route);
    this.applyRoute(route);
  }

  private applyRoute(route: AudioRoute): void {
    this.interview.setEchoSuppression(route === 'speaker');
  }

  /** For a student who has not chosen: earphones when the browser's device
   *  names say so, otherwise speaker -- the default, and the side that keeps
   *  the interviewer's echo off the uplink when the names say nothing. */
  private async preselectRoute(): Promise<void> {
    if (readStoredRoute()) return;
    const route = routeFromDeviceLabels(await defaultDeviceLabels()) ?? 'speaker';
    if (readStoredRoute()) return;
    this.applyRoute(route);
  }

  /** The track picker. Selecting never starts anything on its own. */
  pick(key: string | null): void {
    if (this.active()) return;
    this.selectedSpecialization.set(key);
  }

  dismissNotice(): void {
    this.interview.dismissNotice();
  }

  /** Whole minutes, for the panel. A method rather than a pipe so the template
   *  cannot silently render nothing over a pipe missing from `imports`. */
  minutesOf(seconds: number): number {
    return Math.max(1, Math.round(seconds / 60));
  }

  /** Open the disclosure — before the first interview, or from "Read again". */
  openConsent(): void {
    // Re-read first so the copy they agree to is the copy that is in force.
    void this.loadPolicy();
    this.consentError.set(null);
    this.consentReturnFocus = document.activeElement as HTMLElement | null;
    this.showConsent.set(true);
  }

  /**
   * Consent panel — "I agree". Records the grant, THEN starts the interview.
   * FAILS CLOSED: if the grant cannot be recorded, the interview does not run.
   */
  async acceptConsent(): Promise<void> {
    const version = this.consentVersion();
    if (!version) {
      this.consentError.set('Could not read the interview terms. Please try again.');
      void this.loadConsent();
      return;
    }
    this.consentBusy.set(true);
    this.consentError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/interview/consent`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        // THE VERSION STRING AND NOTHING ELSE (B6.1). The three scopes are read
        // off the college's policy server-side and written onto the row there.
        body: JSON.stringify({ version }),
      });
      if (res.status === 422) {
        this.consentError.set(
          'The interview terms have changed since this page loaded. Reload the page to read the new ones.',
        );
        return;
      }
      if (res.status === 403) {
        this.consentError.set('Mock interviews are a student feature.');
        return;
      }
      if (!res.ok) {
        this.consentError.set('Could not record your consent. Please try again.');
        return;
      }
      this.consent.set((await res.json()) as ConsentGrant);
      this.showConsent.set(false);
      // Focus goes back where it came from — the Start button, about to become
      // End — rather than being dropped on <body> with a live microphone.
      this.consentReturnFocus?.focus();
      this.consentReturnFocus = null;
      this.begin();
    } catch {
      this.consentError.set('Could not reach the server. Please try again.');
    } finally {
      this.consentBusy.set(false);
    }
  }

  cancelConsent(): void {
    this.showConsent.set(false);
    this.consentError.set(null);
    this.consentReturnFocus?.focus();
    this.consentReturnFocus = null;
  }

  /**
   * Keep Tab inside the dialog. Bound to BOTH `(keydown.tab)` and
   * `(keydown.shift.tab)`: Angular matches modifiers exactly, so `keydown.tab`
   * does not fire when Shift is held.
   */
  trapConsentTab(event: Event): void {
    const ev = event as KeyboardEvent;
    const card = this.consentCard()?.nativeElement;
    if (!card) return;
    const focusable = [...card.querySelectorAll<HTMLElement>('button, a[href], input')].filter(
      (el) => !el.hasAttribute('disabled'),
    );
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const activeEl = document.activeElement as HTMLElement | null;

    if (ev.shiftKey && (activeEl === first || activeEl === card)) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && activeEl === last) {
      ev.preventDefault();
      first.focus();
    }
  }

  // ------------------------------------------------------------------ //
  // Server state                                                       //
  // ------------------------------------------------------------------ //

  /**
   * The college's policy, the student's spend today and the tracks on offer.
   * Silent on failure: an unreachable endpoint is not something to interrupt a
   * student with before they have pressed anything; the panel says so on Start.
   */
  private async loadPolicy(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/interview/policy`, {
        credentials: 'include',
      });
      if (!res.ok) return;
      const card = (await res.json()) as InterviewPolicyCard;
      this.policy.set(card);
      if (card.provider_label) this.consentProvider.set(card.provider_label);
      // B5.3: preselect the track this student's batch implies — only when they
      // have not already picked one, and only when the server named one.
      if (card.default_track && this.selectedSpecialization() === null) {
        this.selectedSpecialization.set(card.default_track);
      }
    } catch {
      /* offline or not signed in — the panel handles it on Start */
    }
  }

  /** The caller's own grant, and the version the server is asking for. A
   *  failure leaves `consentVersion` null, which makes acceptConsent() refuse
   *  rather than guess. */
  private async loadConsent(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/interview/consent`, {
        credentials: 'include',
      });
      if (!res.ok) return;
      const state = (await res.json()) as ConsentState;
      this.consentVersion.set(state.version);
      this.consent.set(state.consent);
      if (state.provider) this.consentProvider.set(state.provider);
    } catch {
      /* offline or not signed in — the panel handles it on Start */
    }
  }
}
