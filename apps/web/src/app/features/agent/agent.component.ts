/**
 * The REEP Agent page — the Knowledge-Base TEXT assistant from the design
 * handoff, at /student/agent, /mentor/agent and /admin/agent.
 *
 * THE THREAD IS NOT HERE ANY MORE. It is `shared/agent-chat/`, one component
 * rendered by this page and by the floating dock in the shell
 * (layout/agent-dock.component.ts), which combines it with the mock
 * interviewer under the one orb. This file keeps what is the PAGE's: the
 * heading, the Rule 1 disclaimer, the Clear button in the page head, and the
 * Main Admin's "What the agent can see" rail.
 *
 * ROLE-AGNOSTIC BY CONSTRUCTION. Nothing here reads the URL: the copy that
 * differs per role (the "your records" link is a student screen) is decided
 * from the session's role, and everything else is the same surface. The
 * server already degrades honestly for staff: personalised intents answer
 * with a stated limitation and a 200, never a 4xx.
 *
 * ONE CARD IS THE MAIN ADMIN'S ALONE. The admin board (02 §23,
 * design/admin/Agent.html) adds "What the agent can see" in a 320px rail to the
 * right of the thread — which is where it is, and not above it: the frame is
 * sized `calc(100vh - 330px)`, so a full-width block above it puts the composer
 * of the other two roles' identical screen below the fold on this one. It holds
 * the reader's own scope and Rule 1's status. It renders for role ADMIN only,
 * and everything it states is read from the session this client already holds
 * — the functions the server resolved on /auth/me — or is a rule written in
 * the code. It states NO REGION and no deployment setting: `student_data_
 * egress_allowed` (app/ai/llm.py) makes a narrower promise than the board's
 * chips — loopback is always allowed, and anything off-machine is refused
 * unless LLM_ALLOW_REMOTE_STUDENT_DATA is set. B16 is what makes the server
 * state its own scope and that flag as a boolean; until it lands the card says
 * which half is a rule and which half is not reported.
 */

import { Component, computed, inject, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AuthService } from '../../core/auth.service';
import { AgentChatComponent } from '../../shared/agent-chat/agent-chat.component';

// Re-exported so nothing that imported these from the page breaks.
export type {
  AgentAction,
  AgentMessage,
  AgentSource,
  Rating,
} from '../../shared/agent-chat/agent-chat.component';
export { STARTERS } from '../../shared/agent-chat/agent-chat.component';

@Component({
  selector: 'app-agent',
  standalone: true,
  imports: [RouterLink, AgentChatComponent],
  templateUrl: './agent.component.html',
  styleUrl: './agent.component.scss',
})
export class AgentComponent {
  private readonly auth = inject(AuthService);

  private readonly chat = viewChild(AgentChatComponent);

  /** Staff see a different disclaimer: "your records" is a student screen. */
  readonly isStudent = computed(() => this.auth.session()?.role === 'STUDENT');

  /** The Main Admin is the only role the "What the agent can see" card renders
   *  for — the student and faculty screens are unchanged by it. */
  readonly isMainAdmin = computed(() => this.auth.session()?.role === 'ADMIN');

  /** Who is reading, for the card's first line. */
  readonly readerName = computed(() => this.auth.session()?.name ?? 'This account');

  /** The functions this session holds, exactly as the server resolved them on
   *  /auth/me (the role baseline plus any grant). They are the only scope this
   *  screen can state. */
  readonly grantedFunctions = computed(() => {
    const functionsOnThisSession = this.auth.session()?.capabilities ?? [];
    return [...functionsOnThisSession].sort();
  });

  /** Whether the page-head Clear button has anything to clear. */
  readonly canClear = computed(() => this.chat()?.canClear() ?? false);

  clear(): void {
    void this.chat()?.clear();
  }
}
