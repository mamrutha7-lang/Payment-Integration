/**
 * Set a password from a link — activation (first password) or reset.
 *
 * ONE SCREEN, TWO MODES, because the API is one shape twice: POST /auth/activate
 * and POST /auth/reset both take `{token, password}` and differ only in what
 * happens after. Activation signs you in (the response sets the session
 * cookie); a reset signs in NOBODY — every device is out, including this one,
 * and you sign in fresh with what you just chose. The mode comes from route
 * data, the token from `?token=`.
 *
 * THE LINK IS NOT BURNED BY A TYPO. The API checks the password against
 * policy BEFORE spending the token, so a 422 here means "try again with the
 * same link"; a 410 means the link itself is dead (used or expired) and the
 * only way forward is a new one. The screen says which — and a 422 whose
 * errors locate the TOKEN is the second kind, not the first (`namesTheToken`).
 *
 * Outside the shell, like /login and /register: nobody on this screen has a
 * session yet.
 */

import { Component, computed, inject, signal } from '@angular/core';
import {
  AbstractControl,
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import { HOME_FOR_ROLE } from '../../../core/session';

type Mode = 'activate' | 'reset';

/** The API's floor is 12 (app/set_password.py). Mirrored here only so the
 *  message appears before the round trip; the server's answer still wins. */
const MIN_LENGTH = 12;

/**
 * Whether a 422 is the API refusing the LINK rather than the password.
 *
 * A refused password is a sentence (`password_problem` in
 * app/set_password.py). A request the schema refused is FastAPI's list of
 * errors, each naming its field in `loc`, and one naming `token` is a broken
 * link: read as a refused password, it empties both fields and asks for
 * another one, which no password can ever satisfy.
 */
export function namesTheToken(detail: unknown): boolean {
  return (
    Array.isArray(detail) &&
    detail.some((error) => Array.isArray(error?.loc) && error.loc.includes('token'))
  );
}

function matching(group: AbstractControl): ValidationErrors | null {
  const a = group.get('password')?.value;
  const b = group.get('confirm')?.value;
  return a && b && a !== b ? { mismatch: true } : null;
}

@Component({
  selector: 'app-password-link',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink],
  templateUrl: './password-link.component.html',
  styleUrl: './password-link.component.scss',
})
export class PasswordLinkComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly auth = inject(AuthService);

  readonly mode: Mode = (this.route.snapshot.data['mode'] as Mode) ?? 'reset';
  readonly token = (this.route.snapshot.queryParamMap.get('token') ?? '').trim();

  readonly submitting = signal(false);
  readonly done = signal(false);
  /** A dead link: used or expired. Nothing to retry; needs a new one. */
  readonly linkError = signal<string | null>(null);
  /** A refused password: same link, try again. */
  readonly passwordError = signal<string | null>(null);
  readonly showPassword = signal(false);

  readonly title = computed(() =>
    this.mode === 'activate' ? 'Set up your REEP password' : 'Choose a new password',
  );
  readonly action = computed(() =>
    this.mode === 'activate' ? 'Set password and sign in' : 'Set new password',
  );

  readonly form = new FormGroup(
    {
      password: new FormControl('', {
        nonNullable: true,
        validators: [Validators.required, Validators.minLength(MIN_LENGTH)],
      }),
      confirm: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    },
    { validators: [matching] },
  );

  readonly minLength = MIN_LENGTH;

  constructor() {
    if (!this.token) {
      this.linkError.set('This page needs the link from your email — open it from there.');
    }
  }

  async submit(): Promise<void> {
    if (this.form.invalid || this.submitting() || !this.token) {
      this.form.markAllAsTouched();
      return;
    }
    this.submitting.set(true);
    this.passwordError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/auth/${this.mode}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: this.token, password: this.form.getRawValue().password }),
      });
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown; role?: string };
      const detail = typeof body.detail === 'string' ? body.detail : null;

      if (res.status === 410 || (res.status === 422 && namesTheToken(body.detail))) {
        this.linkError.set(detail ?? 'This link is no longer valid.');
        return;
      }
      if (res.status === 422) {
        this.passwordError.set(detail ?? 'That password was not accepted.');
        this.form.patchValue({ password: '', confirm: '' });
        return;
      }
      if (!res.ok) {
        this.passwordError.set(detail ?? `Something went wrong (${res.status}).`);
        return;
      }

      if (this.mode === 'activate') {
        // The response set the cookie; read the session back through the
        // service so the guard and the shell see it.
        const session = await this.auth.refresh();
        await this.router.navigateByUrl(session ? (HOME_FOR_ROLE[session.role] ?? '/') : '/login');
        return;
      }
      this.done.set(true);
    } catch {
      this.passwordError.set('Could not reach the server. Try again in a moment.');
    } finally {
      this.submitting.set(false);
    }
  }
}
