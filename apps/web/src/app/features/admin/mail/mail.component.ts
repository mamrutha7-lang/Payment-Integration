/**
 * Email delivery — what REEP tried to send, and whether it can arrive.
 *
 * THE SCREEN THAT DID NOT EXIST ON 2026-09-17. `mail_logs` had been written on
 * every send since the Prisma days and read by nothing: no endpoint, no screen,
 * no script. So when a student said "the code never came", the office could not
 * answer even the first question — did REEP try at all.
 *
 * The second question is the one that cost a student their account. A row
 * reading SENT means SES ACCEPTED the message, never that anybody received it.
 * An address on SES's account suppression list — put there by one hard bounce
 * or complaint, possibly months ago during the sandbox period — is accepted on
 * every later send and delivered nowhere, for ever, while the onboarding walk,
 * "Forgot password?", the change-password screen and the setup-link button all
 * say "we have emailed you". Google sign-in keeps working, because no mail is
 * involved. That is why this screen asks the PROVIDER about an address rather
 * than reading a column: the answer changes without REEP being involved, in
 * both directions, so a copy of it would be stale exactly when it mattered.
 *
 * "Not checked" is not "fine". `checked: false` means the question could not be
 * put, and it renders as its own state — the rule `X-Reep-Scope` keeps, where
 * "may see everything" and "may see nothing" must never draw the same.
 */

import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';

interface MailRow {
  id: string;
  kind: string;
  recipient: string;
  subject: string | null;
  status: 'SENT' | 'FAILED' | 'SUPPRESSED';
  error: string | null;
  sent_at: string;
}

interface Suppression {
  email: string;
  checked: boolean;
  suppressed: boolean;
  reason: string | null;
  since: string | null;
}

async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
  } catch {
    /* fall through */
  }
  return fallback;
}

@Component({
  selector: 'app-admin-mail',
  standalone: true,
  // The template's `| date` pipe. A standalone component with no `imports`
  // renders the pipe as nothing and the binding silently — AGENTS.md's
  // "inert markup that renders and does nothing".
  imports: [DatePipe],
  templateUrl: './mail.component.html',
  styleUrl: './mail.component.scss',
})
export class AdminMailComponent {
  private readonly auth = inject(AuthService);

  readonly rows = signal<MailRow[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly loaded = signal(false);

  readonly recipient = signal('');
  readonly kind = signal('');
  readonly failedOnly = signal(false);

  /** The suppression panel: its own address, so checking one does not disturb
   *  the list the office is reading. */
  readonly probeAddress = signal('');
  readonly probe = signal<Suppression | null>(null);
  readonly probing = signal(false);
  readonly probeError = signal<string | null>(null);
  readonly lifting = signal(false);
  readonly lifted = signal<string | null>(null);

  readonly failures = computed(() => this.rows().filter((r) => r.status === 'FAILED').length);

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    if (this.loading()) return;
    this.loading.set(true);
    this.error.set(null);
    const query = new URLSearchParams();
    if (this.recipient().trim()) query.set('recipient', this.recipient().trim());
    if (this.kind().trim()) query.set('kind', this.kind().trim());
    if (this.failedOnly()) query.set('failed_only', 'true');
    try {
      const res = await fetch(`${environment.apiBase}/admin/mail-log?${query}`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.error.set(await detailOf(res, `Could not read the mail log (${res.status}).`));
        return;
      }
      this.rows.set((await res.json()) as MailRow[]);
      this.loaded.set(true);
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.loading.set(false);
    }
  }

  /** Put an address from a row into the panel and ask about it in one press —
   *  the office reads the row first and the address is long and easy to mistype. */
  async checkRow(address: string): Promise<void> {
    this.probeAddress.set(address);
    await this.check();
  }

  async check(): Promise<void> {
    const email = this.probeAddress().trim().toLowerCase();
    if (!email || this.probing()) return;
    this.probing.set(true);
    this.probe.set(null);
    this.probeError.set(null);
    this.lifted.set(null);
    try {
      const res = await fetch(
        `${environment.apiBase}/admin/mail-log/suppression?email=${encodeURIComponent(email)}`,
        { credentials: 'include' },
      );
      if (!res.ok) {
        this.probeError.set(await detailOf(res, `Could not ask (${res.status}).`));
        return;
      }
      this.probe.set((await res.json()) as Suppression);
    } catch {
      this.probeError.set('Could not reach the server.');
    } finally {
      this.probing.set(false);
    }
  }

  async lift(): Promise<void> {
    const found = this.probe();
    if (!found?.suppressed || this.lifting()) return;
    this.lifting.set(true);
    this.probeError.set(null);
    try {
      const res = await fetch(
        `${environment.apiBase}/admin/mail-log/suppression?email=${encodeURIComponent(found.email)}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) {
        this.probeError.set(await detailOf(res, `Could not lift it (${res.status}).`));
        return;
      }
      this.probe.set((await res.json()) as Suppression);
      this.lifted.set(found.email);
    } catch {
      this.probeError.set('Could not reach the server.');
    } finally {
      this.lifting.set(false);
    }
  }

  /** The office account's own address, offered as the panel's starting value so
   *  a first visit can be tried against something known. */
  get ownAddress(): string {
    return this.auth.session()?.email ?? '';
  }
}
