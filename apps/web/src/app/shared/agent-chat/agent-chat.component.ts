/**
 * The REEP Agent thread — the typed Knowledge-Base assistant, as ONE component
 * rendered in two places.
 *
 * It was the body of features/agent/agent.component.ts. That page still
 * exists (routed at /student/agent, /mentor/agent and /admin/agent, with its
 * heading, its Rule 1 disclaimer and the Main Admin's scope rail), and now the
 * floating dock in the shell (layout/agent-dock.component.ts) renders the same
 * thread beside the mock interviewer under one orb. Two copies of a chat that
 * rates answers, replays history and aborts in-flight sends would drift on the
 * first fix to either; so the thread lives here and both hosts import it.
 *
 * WHAT IT TALKS TO. `POST /api/agent/ask` (apps/api-py/app/routers/agent.py):
 * every answer comes back with `actions` (routed next steps), `sources` (what
 * grounded it), `limitations` (what it could not do) and a `run_id` the
 * thumbs-up/down attach to. `GET /api/agent/history` replays the thread and
 * `DELETE /api/agent/conversation` clears it — the same conversation the mock
 * interviewer writes its turns into, which is why the interviewer's page can
 * still show "saved conversation".
 *
 * RULE 1. Nothing here composes a student record into a request: the body of
 * /ask is the typed message and nothing else. What the server does with it is
 * governed in the orchestrator, not here.
 *
 * `variant`: 'page' sizes the frame to the viewport under a page heading;
 * 'dock' fills whatever the dock gives it and carries its own one-line
 * "Clear" control, because the dock's header is the tab bar.
 *
 * Fetch, not HttpClient, with credentials: 'include' — the house pattern
 * (features/student/jobs/jobs.component.ts). The three states every list needs
 * are explicit: `historyState` for the initial load, `pending` while an answer
 * is in flight, `error` for the last failed send.
 */

import {
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/auth.service';
import { featureRefusal } from '../../core/feature-refusal';

/** One routed next step the agent suggests — rendered as an arrow card. */
export interface AgentAction {
  label: string;
  route: string;
  reason: string;
}

/** What grounded an answer. `policy` is an approved Knowledge-Base document. */
export interface AgentSource {
  label: string;
  type: string; // "policy" | "student-record" | anything the server adds later
}

/** How a fresh answer was rated — the wire values of FeedbackRating. */
export type Rating = 'HELPFUL' | 'NOT_HELPFUL' | 'REPORT';

export interface AgentMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  /** Only on a fresh /ask reply; replayed history is plain text. */
  actions?: AgentAction[];
  sources?: AgentSource[];
  limitations?: string[];
  /** The AgentRun this answer came from; present ⇒ it can be rated. */
  runId?: string;
  /** The rating this session cast, if any — drives "Thanks for the feedback". */
  rating?: Rating;
  /** Set on a user turn whose send did not complete. */
  status?: 'failed' | 'stopped';
}

interface AskOut {
  answer: string;
  actions: AgentAction[];
  sources: AgentSource[];
  limitations: string[];
  conversation_id: string;
  model: string;
  run_id: string;
}

interface HistoryOut {
  conversation_id: string;
  turns: { role: 'user' | 'assistant'; content: string }[];
}

/** The starters from the handoff, verbatim. Shown centred when the thread is
 *  empty and as a row above the composer once it is not. */
export const STARTERS: readonly string[] = [
  'What should I complete this week?',
  'Am I placement-ready?',
  'Show jobs I qualify for',
  'How do I verify a skill?',
];

const ERROR_TEXT = 'Could not reach the REEP Agent. Please try again.';
const RATE_LIMIT_TEXT =
  'You have asked a lot in the last minute — wait a moment and send it again.';

/** The composer grows with its content, to this many pixels. */
const COMPOSER_MAX_PX = 160;

export type AgentChatVariant = 'page' | 'dock';

@Component({
  selector: 'app-agent-chat',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './agent-chat.component.html',
  styleUrl: './agent-chat.component.scss',
  host: { '[class.is-dock]': 'variant() === "dock"' },
})
export class AgentChatComponent {
  private readonly auth = inject(AuthService);
  private readonly api = environment.apiBase;

  readonly variant = input<AgentChatVariant>('page');

  readonly starters = STARTERS;

  /** The thread, oldest first. */
  readonly messages = signal<AgentMessage[]>([]);
  /** The initial GET /history: its own state so a slow read shows as loading,
   *  not as an empty conversation. */
  readonly historyState = signal<'loading' | 'ready' | 'error'>('loading');
  /** True while a /ask request is in flight — Send becomes Stop. */
  readonly pending = signal(false);
  /** The last send's failure, cleared by the next send. */
  readonly error = signal<string | null>(null);
  /** What is typed in the composer. */
  readonly draft = signal('');
  /** The message whose answer was just copied, for the transient "Copied" label. */
  readonly copiedId = signal<number | null>(null);

  /** The dock's one-line footnote links to a STUDENT screen. */
  readonly isStudent = computed(() => this.auth.session()?.role === 'STUDENT');

  readonly isEmpty = computed(
    () => this.historyState() !== 'loading' && this.messages().length === 0 && !this.pending(),
  );
  readonly canSend = computed(() => this.draft().trim().length > 0 && !this.pending());
  /** Whether "Clear conversation" has anything to clear. Read by the hosts. */
  readonly canClear = computed(() => this.messages().length > 0 || this.pending());

  private readonly scroller = viewChild<ElementRef<HTMLDivElement>>('scroller');
  private readonly composer = viewChild<ElementRef<HTMLTextAreaElement>>('composer');

  private inflight: AbortController | null = null;
  private nextId = 1;
  private copiedTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    void this.loadHistory();
    // Keep the newest turn in view: every change to the thread or to the
    // pending indicator scrolls the message pane to its bottom.
    effect(() => {
      this.messages();
      this.pending();
      const el = this.scroller()?.nativeElement;
      if (el) queueMicrotask(() => (el.scrollTop = el.scrollHeight));
    });
  }

  // --- history -------------------------------------------------------------

  private async loadHistory(): Promise<void> {
    this.historyState.set('loading');
    try {
      const res = await fetch(`${this.api}/agent/history`, { credentials: 'include' });
      if (!res.ok) throw new Error(String(res.status));
      const body = (await res.json()) as HistoryOut;
      this.messages.set(
        body.turns
          .filter((t) => t.role === 'user' || t.role === 'assistant')
          .map((t) => ({ id: this.nextId++, role: t.role, content: t.content })),
      );
      this.historyState.set('ready');
    } catch {
      // A failed history read is not a failed conversation: the screen stays
      // usable, the error line says what happened, and a send will still work.
      this.historyState.set('error');
      this.error.set(ERROR_TEXT);
    }
  }

  async clear(): Promise<void> {
    this.stop();
    this.error.set(null);
    try {
      const res = await fetch(`${this.api}/agent/conversation`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!res.ok) throw new Error(String(res.status));
      this.messages.set([]);
      this.historyState.set('ready');
    } catch {
      this.error.set(ERROR_TEXT);
    }
  }

  // --- sending -------------------------------------------------------------

  /** A starter chip is just a pre-written message. */
  useStarter(text: string): void {
    if (this.pending()) return;
    void this.send(text);
  }

  submit(): void {
    const text = this.draft().trim();
    if (!text || this.pending()) return;
    this.draft.set('');
    this.resizeComposer();
    void this.send(text);
  }

  /** Enter sends; Shift+Enter inserts a newline (the textarea's default). */
  onComposerKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      this.submit();
    }
  }

  onComposerInput(event: Event): void {
    this.draft.set((event.target as HTMLTextAreaElement).value);
    this.resizeComposer();
  }

  private resizeComposer(): void {
    const el = this.composer()?.nativeElement;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_PX)}px`;
  }

  private async send(text: string): Promise<void> {
    this.error.set(null);
    const userTurn: AgentMessage = { id: this.nextId++, role: 'user', content: text };
    this.messages.update((list) => [...list, userTurn]);
    this.pending.set(true);

    const controller = new AbortController();
    this.inflight = controller;
    try {
      const res = await fetch(`${this.api}/agent/ask`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
        signal: controller.signal,
      });
      if (!res.ok) {
        this.markUser(userTurn.id, 'failed');
        // A student whose office switched the agent off reads the office's
        // message; "Could not reach the REEP Agent" would send them to support.
        this.error.set(
          res.status === 429 ? RATE_LIMIT_TEXT : ((await featureRefusal(res)) ?? ERROR_TEXT),
        );
        return;
      }
      const body = (await res.json()) as AskOut;
      this.messages.update((list) => [
        ...list,
        {
          id: this.nextId++,
          role: 'assistant',
          content: body.answer,
          actions: body.actions ?? [],
          sources: body.sources ?? [],
          limitations: body.limitations ?? [],
          runId: body.run_id,
        },
      ]);
    } catch (err) {
      if ((err as { name?: string })?.name === 'AbortError') {
        // Stopped by the student. The server may still have answered and saved
        // the turn — the next history read will show it — but nothing is
        // rendered here that they asked not to wait for.
        this.markUser(userTurn.id, 'stopped');
      } else {
        this.markUser(userTurn.id, 'failed');
        this.error.set(ERROR_TEXT);
      }
    } finally {
      if (this.inflight === controller) this.inflight = null;
      this.pending.set(false);
    }
  }

  /** Abort the in-flight /ask. Send turns back into Send. */
  stop(): void {
    this.inflight?.abort();
    this.inflight = null;
  }

  private markUser(id: number, status: 'failed' | 'stopped'): void {
    this.messages.update((list) => list.map((m) => (m.id === id ? { ...m, status } : m)));
  }

  // --- per-answer controls -------------------------------------------------

  async copy(message: AgentMessage): Promise<void> {
    try {
      await navigator.clipboard.writeText(message.content);
      this.copiedId.set(message.id);
      if (this.copiedTimer) clearTimeout(this.copiedTimer);
      this.copiedTimer = setTimeout(() => this.copiedId.set(null), 1600);
    } catch {
      // Clipboard access can be refused (insecure context, permissions). The
      // text is on screen and selectable; nothing else to do.
    }
  }

  /** Thumbs up / down / report. Optimistic: the acknowledgement shows at once
   *  and is withdrawn only if the server refuses. */
  async rate(message: AgentMessage, rating: Rating): Promise<void> {
    if (!message.runId) return;
    const previous = message.rating;
    this.setRating(message.id, rating);
    try {
      const res = await fetch(`${this.api}/agent/feedback`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: message.runId, rating }),
      });
      if (!res.ok) this.setRating(message.id, previous);
    } catch {
      this.setRating(message.id, previous);
    }
  }

  private setRating(id: number, rating: Rating | undefined): void {
    this.messages.update((list) => list.map((m) => (m.id === id ? { ...m, rating } : m)));
  }

  /** The source chip tone: an approved policy document is the grounding the
   *  handoff highlights; anything else (a student-record tool, a future type)
   *  is the quiet neutral chip. */
  sourceTone(source: AgentSource): 'policy' | 'other' {
    return source.type === 'policy' ? 'policy' : 'other';
  }
}
