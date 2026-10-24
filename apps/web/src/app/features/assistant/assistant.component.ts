/**
 * The mock-interview PAGE, at /student/assistant.
 *
 * THE ROOM IS NOT HERE ANY MORE. It is `shared/interview-room/`, one component
 * rendered by this page and by the floating dock in the shell
 * (layout/agent-dock.component.ts), which combines it with the typed REEP
 * Agent under the one orb — so an interview can be started from any screen.
 * This page is the deep link the Interviews screen and the landing's Elevate
 * card point at; it keeps what is the PAGE's: the heading, the Rule 1 note,
 * and the saved conversation.
 *
 * THE SAVED CONVERSATION. Interview turns are persisted server-side through
 * app/conversations.py into the SAME `conversations` / `messages` tables the
 * text agent uses, so GET /api/agent/history returns them and the AGENTS.md
 * runbook query still works. It is re-read after every session, and "Clear
 * conversation" deletes it. That is the whole reason AgentHistoryService is
 * injected here.
 *
 * The sentence in the template about what Clear deletes is not optional
 * wording: the interview record (`interview_turns`) is keyed on the interview,
 * not the conversation, and deliberately SURVIVES Clear — being durable is
 * what makes it a record.
 */

import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AgentHistoryService, ChatTurn } from '../../core/agent-history.service';
import { InterviewService } from '../../core/interview.service';
import { PageIntroComponent } from '../../shared/kit/kit.components';
import { InterviewRoomComponent } from '../../shared/interview-room/interview-room.component';

@Component({
  selector: 'app-assistant',
  standalone: true,
  imports: [PageIntroComponent, RouterLink, InterviewRoomComponent],
  templateUrl: './assistant.component.html',
  styleUrl: './assistant.component.scss',
})
export class AssistantComponent {
  private readonly interview = inject(InterviewService);
  /** Only for the persisted conversation: loadHistory + clearConversation. */
  private readonly chat = inject(AgentHistoryService);

  readonly active = this.interview.active;

  readonly history = this.chat.chatHistory;
  readonly historyError = signal<string | null>(null);
  readonly historyOpen = signal(false);

  readonly canClear = computed(() => !this.active() && this.history().length > 0);

  constructor() {
    void this.loadHistory();
    // Re-read the persisted conversation when a session finishes. Interview
    // turns are written server-side as they arrive; the client never posts a
    // transcript. This is the reconciliation that makes them appear below.
    effect(() => {
      if (this.interview.completedSessions() === 0) return;
      void this.loadHistory();
    });
  }

  toggleHistory(): void {
    this.historyOpen.update((open) => !open);
  }

  /** The text of one persisted turn, whichever shape it arrived in. */
  turnText(turn: ChatTurn): string {
    return turn.structured?.answer ?? turn.content;
  }

  /** Discard the server-owned conversation — text turns and interview turns
   *  alike. This is the student's delete control over their own transcripts. */
  async clearConversation(): Promise<void> {
    if (this.active()) return;
    this.historyError.set(null);
    try {
      await this.chat.clearConversation();
    } catch {
      this.historyError.set('Could not clear the conversation. Please try again.');
    }
  }

  private async loadHistory(): Promise<void> {
    try {
      await this.chat.loadHistory();
    } catch {
      /* fresh session, or the API is unreachable — nothing to restore */
    }
  }
}
