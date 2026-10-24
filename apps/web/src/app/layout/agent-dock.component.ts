/**
 * The floating assistant — the REEP Agent and the mock interviewer, in one
 * panel under the one orb.
 *
 * WHY ONE PANEL. The design put a single orb on every screen, and the product
 * had two assistants behind two routes: the typed REEP Agent
 * (POST /api/agent/ask) and the spoken mock interviewer (a WebSocket to
 * /api/interview). A student who wanted to rehearse had to know a screen
 * existed and find it from the landing card. The dock puts both where the orb
 * is: "Ask REEP" and "Mock interview" are two tabs of one panel, and the panel
 * opens on whatever screen the student is on.
 *
 * NOTHING IS COPIED. Each tab renders the same component its page renders —
 * `shared/agent-chat` (also /student/agent, /mentor/agent, /admin/agent) and
 * `shared/interview-room` (also /student/assistant) — in their `dock`
 * variant. The pages stay as the deep links; "Open as a page" in the bar goes
 * to them.
 *
 * BOTH TABS ARE `@defer`RED. The interview room pulls InterviewService
 * (~2 900 lines of audio pipeline) and the visualizer (~2 000) behind it; the
 * chat is smaller but not free. The shell is in the INITIAL bundle and the
 * production budget is set close to it (AGENTS.md, "Routes are lazy"), so a
 * tab's component is fetched the first time that tab is shown and not before.
 * `askSeen` / `roomSeen` are sticky: once a tab has been shown, its block is
 * rendered whichever tab is in front.
 *
 * THE INACTIVE TAB IS HIDDEN, NOT DESTROYED. A live interview must survive a
 * glance at the other tab: destroying the room ends the interview (its
 * ngOnDestroy releases the microphone — AGENTS.md rule 1), so the panels are
 * toggled with `hidden` and the room lives for as long as the dock is open.
 * Closing the dock DOES destroy it, and that is the one moment the microphone
 * is released without an End press — so closing over a live interview asks
 * first, in the bar, with the words saying what closing does. Escape asks the
 * same question.
 *
 * MOCK INTERVIEW IS A STUDENT TAB — AND THE MAIN ADMIN'S. The backend refuses
 * faculty and alumni, so they get the one tab rather than a second tab that
 * 403s; the Main Admin's interview is a rehearsal the server stores nothing
 * for (2026-09-16), so the office can hear the interviewer it deploys.
 */

import { Component, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AgentDockService } from '../core/agent-dock.service';
import { AuthService } from '../core/auth.service';
import type { Role } from '../core/session';
import { AgentChatComponent } from '../shared/agent-chat/agent-chat.component';
import { InterviewRoomComponent } from '../shared/interview-room/interview-room.component';

/** Each role's own REEP Agent page. */
export function agentRouteFor(role: Role | undefined): string {
  if (role === 'ADMIN') return '/admin/agent';
  if (role === 'MENTOR') return '/mentor/agent';
  return '/student/agent';
}

@Component({
  selector: 'app-agent-dock',
  standalone: true,
  imports: [RouterLink, AgentChatComponent, InterviewRoomComponent],
  templateUrl: './agent-dock.component.html',
  styleUrl: './agent-dock.component.scss',
})
export class AgentDockComponent {
  readonly dock = inject(AgentDockService);
  private readonly auth = inject(AuthService);

  readonly open = this.dock.open;
  readonly mode = this.dock.mode;
  readonly live = this.dock.live;

  readonly isStudent = computed(() => this.auth.session()?.role === 'STUDENT');
  /** Who gets the "Mock interview" tab: a student, and the Main Admin — whose
   *  interview is a REHEARSAL the server stores nothing for (see
   *  InterviewRoomComponent.isRehearsal). Faculty and alumni do not. */
  readonly canInterview = computed(() => {
    const role = this.auth.session()?.role;
    return role === 'STUDENT' || role === 'ADMIN';
  });

  /** The page behind the tab in front, or null when there is none: the
   *  interview page is a student route, and the Main Admin's rehearsal lives
   *  only here. */
  readonly pageRoute = computed<string | null>(() => {
    if (this.mode() !== 'interview') return agentRouteFor(this.auth.session()?.role);
    return this.isStudent() ? '/student/assistant' : null;
  });

  /** Sticky "has been shown": the `@defer` triggers. */
  readonly askSeen = signal(false);
  readonly roomSeen = signal(false);

  /** The "closing ends the interview" question, shown in the bar. Raised in
   *  the service so the orb can raise it too. */
  readonly confirmClose = this.dock.confirmClose;

  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');

  constructor() {
    effect(() => {
      if (!this.open()) return;
      const mode = this.mode();
      if (mode === 'ask') this.askSeen.set(true);
      // A non-student cannot show the room, whatever the mode says.
      if (mode === 'interview' && this.canInterview()) this.roomSeen.set(true);
    });
    // A non-student opened on the interview tab (a stale mode from an earlier
    // session, say) lands on the one tab they have.
    effect(() => {
      if (this.open() && this.mode() === 'interview' && !this.canInterview()) {
        this.dock.setMode('ask');
      }
    });
    // Focus moves INTO the panel when it opens, onto the selected tab, so a
    // keyboard user who pressed the orb is not left behind it.
    effect(() => {
      const el = this.panel()?.nativeElement;
      if (!this.open() || !el) return;
      queueMicrotask(() => {
        el.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus();
      });
    });
  }

  show(mode: 'ask' | 'interview'): void {
    this.dock.setMode(mode);
  }

  /** Close — or, over a live interview, ask first (the service decides). */
  requestClose(): void {
    this.dock.requestClose();
  }

  /** The "End and close" answer. Hiding destroys the room, whose ngOnDestroy
   *  ends the interview and releases the microphone. */
  closeNow(): void {
    this.dock.hide();
  }

  keepGoing(): void {
    this.dock.dismissCloseQuestion();
  }

  /**
   * The `@error` blocks' button. A deferred chunk 404s when this tab was opened
   * before a deploy and `outputHashing: "all"` has since renamed every file, so
   * the fix is to fetch index.html again and with it the current names —
   * `location.reload()` and not a retry of the import, which would ask for the
   * same dead URL and fail the same way.
   *
   * Destroying the room ends a live interview, so this is deliberately reached
   * only from a block that FAILED TO LOAD: there is no room to end.
   */
  reload(): void {
    location.reload();
  }
}
