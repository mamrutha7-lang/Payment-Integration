/**
 * Setting up an approved student's account: address, code, password.
 *
 * THREE STEPS ON ONE SCREEN, driven by a `step` signal rather than three
 * routes. Each step's proof is spent by the step after it, so a URL that could
 * land on step 2 or 3 directly would be a URL that skips a proof — and a
 * back-button press onto a dead step is a support call. There is one URL,
 * `/onboard?token=`, and the only way forward is through.
 *
 * The API is `routers/onboarding.py`:
 *   1. POST /auth/onboard/start     {token, email}   -> mails a code
 *   2. POST /auth/onboard/verify    {token, code}    -> a short-lived ticket
 *   3. POST /auth/onboard/password  {ticket, password}
 *
 * IT ENDS AT THE LOGIN SCREEN AND NOT IN THE APP. `/activate` signs a staff
 * member in because they plainly just chose that password; here the flow is
 * finished by typing it once at the ordinary front door, which applies the
 * ordinary brute-force limiter and single-device retirement. One door.
 *
 * WHY THE ADDRESS IS TYPED AT ALL when the link already identifies the
 * account: the link only proves somebody opened mail sent there, and a
 * forwarded link proves that too. The address plus the code is what proves
 * this person can read that mailbox now. The API answers a wrong address with
 * exactly what it answers a dead link, so this screen must not imply which.
 *
 * Outside the shell, like /login, /register and /activate: nobody here has a
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
import { ActivatedRoute, RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';

/** The API's floor is 12 (app/set_password.py). Mirrored so the message
 *  appears before the round trip; the server's answer still wins. */
const MIN_LENGTH = 12;

function matching(group: AbstractControl): ValidationErrors | null {
  const a = group.get('password')?.value;
  const b = group.get('confirm')?.value;
  return a && b && a !== b ? { mismatch: true } : null;
}

async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    // FastAPI answers a SCHEMA refusal with detail as a list of objects, which
    // renders as "[object Object]" if handed straight to a template.
    if (Array.isArray(detail) && detail.length && typeof detail[0]?.msg === 'string') {
      return String(detail[0].msg).replace(/^Value error,\s*/, '');
    }
  } catch {
    /* fall through */
  }
  return fallback;
}

@Component({
  selector: 'app-onboard',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink],
  templateUrl: './onboard.component.html',
  styleUrl: './onboard.component.scss',
})
export class OnboardComponent {
  private readonly route = inject(ActivatedRoute);

  readonly token = (this.route.snapshot.queryParamMap.get('token') ?? '').trim();
  readonly minLength = MIN_LENGTH;

  /** 1 address -> 2 code -> 3 password -> 4 done. Forward only. */
  readonly step = signal<1 | 2 | 3 | 4>(1);
  readonly busy = signal(false);
  readonly showPassword = signal(false);

  /** A dead link or a mismatched address. Nothing to retry on this screen. */
  readonly fatal = signal<string | null>(
    // A link-less visit is the commonest way to arrive here by accident.
    null,
  );
  /** A refusal the person can act on: wrong code, short password. */
  readonly error = signal<string | null>(null);
  /** What the server said it did — "we emailed a code to …". */
  readonly note = signal<string | null>(null);

  private ticket = '';

  /**
   * EVERY FORM ON THIS SCREEN CARRIES A `[formGroup]`, AND STEPS 1 AND 2 DID
   * NOT UNTIL 2026-09-17. `(ngSubmit)` is an output of `FormGroupDirective`
   * (ReactiveFormsModule) or of `NgForm` (FormsModule); this component imports
   * only the first, so on a bare `<form>` neither directive matched and
   * `(ngSubmit)` compiled to a DOM listener for an event nothing dispatches.
   * The handler never ran, nothing called `preventDefault`, and the submit
   * button did what a submit button does with no JavaScript in the way: a
   * native GET to the current URL, whose query string is REPLACED by the
   * form's named fields — of which there are none.
   *
   * So pressing "Send me a code" navigated to `/onboard?`, the token was gone,
   * and the student met "This page needs the setup link from the email we sent
   * you" one keystroke after the link had worked perfectly. It looked to the
   * office like the approval mail was broken; it was the first button.
   *
   * A single control does not need a group to work — `[formControl]` binds
   * fine on its own — which is exactly why this was invisible. The group is
   * here for the `<form>`, not for the control: it is what makes `(ngSubmit)`
   * a real output and the default prevented. Do not "simplify" it back.
   */
  readonly emailForm = new FormGroup({
    email: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.email],
    }),
  });
  readonly email = this.emailForm.controls.email;

  readonly codeForm = new FormGroup({
    code: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.pattern(/^\d{6}$/)],
    }),
  });
  readonly code = this.codeForm.controls.code;

  readonly passwordForm = new FormGroup(
    {
      password: new FormControl('', {
        nonNullable: true,
        validators: [Validators.required, Validators.minLength(MIN_LENGTH)],
      }),
      confirm: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    },
    { validators: matching },
  );

  readonly heading = computed(() => {
    switch (this.step()) {
      case 1:
        return 'Confirm your email address';
      case 2:
        return 'Enter the code we emailed you';
      case 3:
        return 'Choose your password';
      default:
        return 'Your account is ready';
    }
  });

  constructor() {
    if (!this.token) {
      this.fatal.set(
        'This page needs the setup link from the email we sent you. Open that link ' +
          'again, or ask for a new one with "Forgot password?" on the sign-in screen.',
      );
    }
  }

  // ---- step 1: the address -------------------------------------------------

  async sendCode(): Promise<void> {
    if (this.email.invalid || this.busy()) {
      this.email.markAsTouched();
      return;
    }
    await this.run(async () => {
      const res = await fetch(`${environment.apiBase}/auth/onboard/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: this.token, email: this.email.value.trim() }),
      });
      if (res.status === 410) {
        // The API deliberately cannot tell a dead link from a wrong address,
        // so neither can this screen. Both readings are offered.
        this.fatal.set(await detailOf(res, 'That setup link is no longer valid.'));
        return;
      }
      if (!res.ok) {
        this.error.set(await detailOf(res, 'Could not send the code. Try again.'));
        return;
      }
      this.note.set((await res.json()).message ?? null);
      this.step.set(2);
    });
  }

  /** Step 2's "send it again" — the same call, so the newest code is the only
   *  live one (the API supersedes on issue). */
  async resend(): Promise<void> {
    this.code.reset('');
    await this.sendCode();
    this.step.set(2);
  }

  // ---- step 2: the code ----------------------------------------------------

  async checkCode(): Promise<void> {
    if (this.code.invalid || this.busy()) {
      this.code.markAsTouched();
      return;
    }
    await this.run(async () => {
      const res = await fetch(`${environment.apiBase}/auth/onboard/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: this.token, code: this.code.value.trim() }),
      });
      if (res.status === 410) {
        this.fatal.set(await detailOf(res, 'That setup link is no longer valid.'));
        return;
      }
      if (!res.ok) {
        this.error.set(
          await detailOf(res, 'That code is not right, or it has expired.'),
        );
        return;
      }
      this.ticket = (await res.json()).ticket;
      this.note.set(null);
      this.step.set(3);
    });
  }

  // ---- step 3: the password ------------------------------------------------

  async setPassword(): Promise<void> {
    if (this.passwordForm.invalid || this.busy()) {
      this.passwordForm.markAllAsTouched();
      return;
    }
    await this.run(async () => {
      const res = await fetch(`${environment.apiBase}/auth/onboard/password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticket: this.ticket,
          password: this.passwordForm.controls.password.value,
        }),
      });
      if (res.status === 410) {
        this.fatal.set(await detailOf(res, 'This page has expired.'));
        return;
      }
      if (!res.ok) {
        // 422 is a policy refusal: the ticket is NOT spent, so the same form
        // works on a second try. Deliberately not a fatal.
        this.error.set(await detailOf(res, 'That password was not accepted.'));
        return;
      }
      this.step.set(4);
    });
  }

  private async run(work: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await work();
    } catch {
      this.error.set('Could not reach the server. Check your connection and try again.');
    } finally {
      this.busy.set(false);
    }
  }
}
