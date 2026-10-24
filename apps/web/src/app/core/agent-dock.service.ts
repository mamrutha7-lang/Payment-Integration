/**
 * The floating assistant's state — shared between the orb that opens it, the
 * dock that renders it, and the two panels inside it.
 *
 * ONE ASSISTANT, TWO MODES. The design put a single orb on every screen and
 * the product had two assistants behind two routes: the typed REEP Agent
 * (POST /api/agent/ask) and the spoken mock interviewer (a WebSocket to
 * /api/interview). The dock combines them under one button — "Ask REEP" and
 * "Mock interview" are tabs of one panel — so this service is the one place
 * that knows which is showing and whether the interview inside it is live.
 *
 * WHY THE ORB READS THE INTERVIEW'S STATE FROM HERE AND NOT FROM
 * InterviewService. The orb is in the shell, which is in the INITIAL bundle;
 * InterviewService is ~2 900 lines of audio pipeline and the visualizer behind
 * it is another ~2 000. The dock loads both through `@defer`, so the initial
 * chunk never carries them — and the orb, which only needs "is an interview
 * running, and how long has it run", reads two signals the room component
 * mirrors into this service while it is mounted. The room is the only writer
 * of `liveState` / `liveClock`; when it is destroyed the interview has ended
 * (its ngOnDestroy releases the microphone) and it clears them on the way out.
 *
 * The typed agent's pages (/student/agent, /mentor/agent, /admin/agent) and
 * the interviewer's page (/student/assistant) still exist and render the same
 * two panel components in page mode. The dock is the on-every-screen way in;
 * the pages are the deep links. Neither is a copy of the other.
 */

import { Injectable, computed, signal } from '@angular/core';

/** Which panel the dock shows. */
export type DockMode = 'ask' | 'interview';

/** What the orb needs to know about the interview, in three words. `null`
 *  means the room is not mounted, which is the same as "no interview". */
export type DockLiveState = 'connecting' | 'listening' | 'thinking' | 'speaking' | null;

@Injectable({ providedIn: 'root' })
export class AgentDockService {
  private readonly _open = signal(false);
  private readonly _mode = signal<DockMode>('ask');
  private readonly _liveState = signal<DockLiveState>(null);
  private readonly _liveClock = signal<string | null>(null);
  private readonly _confirmClose = signal(false);

  readonly open = this._open.asReadonly();
  readonly mode = this._mode.asReadonly();
  /** The live interview's coarse state, mirrored by the room while mounted. */
  readonly liveState = this._liveState.asReadonly();
  /** "04:12" while an interview runs, else null. */
  readonly liveClock = this._liveClock.asReadonly();
  /** True while the room reports any live state — the dock refuses to close
   *  silently over it, and the orb pulses. */
  readonly live = computed(() => this._liveState() !== null);
  /** The "closing ends the interview" question is pending. The dock renders
   *  it; the orb and the dock's own Close both raise it via requestClose(). */
  readonly confirmClose = this._confirmClose.asReadonly();

  /** Open the dock, optionally on a given tab. */
  show(mode?: DockMode): void {
    if (mode) this._mode.set(mode);
    this._open.set(true);
  }

  hide(): void {
    this._open.set(false);
    this._confirmClose.set(false);
  }

  /**
   * Close — or, over a live interview, ask first. Hiding the dock destroys
   * the room, whose ngOnDestroy ends the interview and releases the
   * microphone; that is the one moment the microphone is released without an
   * End press, so it is never silent.
   */
  requestClose(): void {
    if (this.live()) {
      this._confirmClose.set(true);
      return;
    }
    this.hide();
  }

  /** The "Keep going" answer. */
  dismissCloseQuestion(): void {
    this._confirmClose.set(false);
  }

  setMode(mode: DockMode): void {
    this._mode.set(mode);
  }

  /** The room's mirror. Called from an effect in InterviewRoomComponent and
   *  from nowhere else. */
  reportLive(state: DockLiveState, clock: string | null): void {
    this._liveState.set(state);
    this._liveClock.set(state === null ? null : clock);
  }
}
