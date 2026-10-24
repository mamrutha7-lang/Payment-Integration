/**
 * Change password — signed in, proves it with a code emailed to the account.
 *
 * THE CODE, NOT THE CURRENT PASSWORD (2026-09-10). A student who set their
 * first password last week does not reliably remember it, and "I forgot it
 * already" reaching the placement office as a support call is the failure this
 * screen exists to avoid. Being signed in is not the proof — the code is; the
 * mail says plainly what to do if you did not ask for one, because a signed-in
 * session on a shared lab machine is exactly the case this guards.
 *
 * The API still accepts `current_password` instead (`_authorised_to_change`
 * takes one proof or the other, never both), which is what six older test
 * modules use and what works when mail does not. This screen offers the code
 * because it is the one a student can always complete.
 *
 * OTHER devices are signed out while this one stays — the API re-issues this
 * cookie with the new token_version before answering, and the service's
 * `refresh()` reads it back so the shell never flickers to signed-out.
 *
 * An account holding no password at all (the unusable sentinel — Google-only,
 * or onboarding unfinished) gets a 409 and a sentence, not a form that cannot
 * succeed — and, since 2026-09-16, a button. `POST /auth/forgot` mails a
 * password-less STUDENT the setup link (the same `/onboard` walk approval
 * sends), so "Email me a setup link" posts that endpoint with the address on
 * the session. The address is the server's own idea of this account, read
 * from the session rather than typed, and the endpoint answers the same words
 * whatever it did, so nothing here can learn anything from it.
 */

import { Component, inject, signal } from '@angular/core';
import {
  AbstractControl,
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';

import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/auth.service';
import { PluralPipe } from '../../shared/text/plural.pipe';

const MIN_LENGTH = 12;

function matching(group: AbstractControl): ValidationErrors | null {
  const a = group.get('new_password')?.value;
  const b = group.get('confirm')?.value;
  return a && b && a !== b ? { mismatch: true } : null;
}

@Component({
  selector: 'app-change-password',
  standalone: true,
  imports: [ReactiveFormsModule, PluralPipe],
  templateUrl: './change-password.component.html',
  styleUrl: './change-password.component.scss',
})
export class ChangePasswordComponent {
  private readonly auth = inject(AuthService);

  readonly submitting = signal(false);
  readonly sending = signal(false);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  /** What the server said when it mailed the code ("we emailed a code to …"). */
  readonly note = signal<string | null>(null);
  /** 409 from the API: this account has no password to change. */
  readonly noPassword = signal(false);
  /** The setup-link request's outcome — the server's one sentence, or a
   *  transport failure. Non-null hides the button: asking again supersedes the
   *  link server-side, but a second press here is a second mail. */
  readonly setupNote = signal<string | null>(null);
  readonly setupBusy = signal(false);
  /** The code has been sent, so the form is worth showing. */
  readonly codeSent = signal(false);
  readonly minLength = MIN_LENGTH;

  readonly form = new FormGroup(
    {
      code: new FormControl('', {
        nonNullable: true,
        validators: [Validators.required, Validators.pattern(/^\d{6}$/)],
      }),
      new_password: new FormControl('', {
        nonNullable: true,
        validators: [Validators.required, Validators.minLength(MIN_LENGTH)],
      }),
      confirm: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    },
    { validators: [matching] },
  );

  /** Ask for the code. Re-asking supersedes the previous one server-side, so
   *  there is never more than one live code in the mailbox. */
  async sendCode(): Promise<void> {
    if (this.sending()) return;
    this.sending.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/auth/change-password/code`, {
        method: 'POST',
        credentials: 'include',
      });
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown; message?: string };
      if (res.status === 409) {
        this.noPassword.set(true);
        return;
      }
      if (!res.ok) {
        this.error.set(
          typeof body.detail === 'string'
            ? body.detail
            : `Could not send the code (${res.status}).`,
        );
        return;
      }
      this.note.set(body.message ?? null);
      this.codeSent.set(true);
      this.form.controls.code.reset('');
    } catch {
      this.error.set('Could not reach the server. Try again in a moment.');
    } finally {
      this.sending.set(false);
    }
  }

  /** Ask for the setup link on this account's own address. Same endpoint the
   *  login screen's "Forgot password?" form calls; same throttle. */
  async emailSetupLink(): Promise<void> {
    const email = this.auth.session()?.email?.trim().toLowerCase();
    if (!email || this.setupBusy()) return;
    this.setupBusy.set(true);
    try {
      const res = await fetch(`${environment.apiBase}/auth/forgot`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      this.setupNote.set(
        typeof body.detail === 'string'
          ? body.detail
          : res.ok
            ? `We have emailed ${email} a setup link.`
            : `Could not send the link just now (${res.status}). Try again in a moment.`,
      );
    } catch {
      this.setupNote.set('Could not reach the server. Try again in a moment.');
    } finally {
      this.setupBusy.set(false);
    }
  }

  async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    this.submitting.set(true);
    this.error.set(null);
    this.flash.set(null);
    const { code, new_password } = this.form.getRawValue();
    try {
      const res = await fetch(`${environment.apiBase}/auth/change-password`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, new_password }),
      });
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      const detail = typeof body.detail === 'string' ? body.detail : null;
      if (res.status === 409) {
        this.noPassword.set(true);
        return;
      }
      if (!res.ok) {
        this.error.set(detail ?? `Could not change the password (${res.status}).`);
        // A refused code is SPENT server-side, so leaving it in the field
        // invites a second submit that cannot work. Clear it and say so.
        this.form.patchValue({ code: '' });
        if (res.status === 403) {
          this.codeSent.set(false);
          this.note.set(null);
        }
        return;
      }
      await this.auth.refresh(); // the re-issued cookie, read back
      this.form.reset();
      this.codeSent.set(false);
      this.note.set(null);
      this.flash.set('Password changed. Your other devices have been signed out; this one stays.');
    } catch {
      this.error.set('Could not reach the server. Try again in a moment.');
    } finally {
      this.submitting.set(false);
    }
  }
}
