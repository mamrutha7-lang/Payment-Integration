/**
 * The server-owned agent conversation: read it, clear it.
 *
 * This is what survived `ChatVoiceService` when the LiveKit voice stack was
 * removed from the repo. That service was ~840 lines, most of it a WebRTC
 * session lifecycle (`livekit-client`, room events, mic permission, mute,
 * reconnect) that nothing calls any more. Three members were NOT voice — a
 * conversation signal and two HTTP calls — and the mock interviewer screen
 * depends on them, so they live here instead of being deleted with the rest.
 *
 * WHY THE INTERVIEWER NEEDS THIS. Interview turns are persisted server-side by
 * app/conversations.py into the SAME `conversations` / `messages` tables the
 * text agent writes, so `GET /api/agent/history` returns them and the AGENTS.md
 * runbook query still answers "did it save anything". The interview screen
 * re-reads the history after every session, and its "Clear conversation" button
 * deletes the server's copy — not merely the local view.
 */

import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

export type ChatRole = 'user' | 'assistant';

/** A routed next-step the agent suggests, rendered as an action card. */
export interface AgentAction {
  label: string;
  route: string;
  reason: string;
}

/** Where an answer came from — tints the source chip in the UI. */
export interface AgentSource {
  label: string;
  type: 'student-record' | 'policy' | 'general';
}

/** The structured payload attached to a fresh /ask assistant turn. */
export interface StructuredAnswer {
  answer: string;
  actions: AgentAction[];
  sources: AgentSource[];
  limitations: string[];
  model: string | null;
  /** Identifies the agent run this answer came from — needed to send feedback. */
  run_id?: string;
}

export interface ChatTurn {
  role: ChatRole;
  content: string;
  /** Present only on fresh /ask assistant turns; historical turns are plain text. */
  structured?: StructuredAnswer;
  /** Set on a user turn whose /ask request failed ('failed') or was stopped ('stopped'). */
  status?: 'failed' | 'stopped';
}

interface HistoryResponse {
  conversation_id: string;
  turns: ChatTurn[];
}

@Injectable({ providedIn: 'root' })
export class AgentHistoryService {
  private readonly http = inject(HttpClient);

  readonly chatHistory = signal<ChatTurn[]>([]);

  /** Load the server-owned conversation into chatHistory. */
  async loadHistory(): Promise<void> {
    const res = await firstValueFrom(
      this.http.get<HistoryResponse>('/api/agent/history', { withCredentials: true }),
    );
    this.chatHistory.set(res.turns);
  }

  /** Discard the server-owned conversation and clear the local view. */
  async clearConversation(): Promise<void> {
    await firstValueFrom(this.http.delete('/api/agent/conversation', { withCredentials: true }));
    this.chatHistory.set([]);
  }
}
