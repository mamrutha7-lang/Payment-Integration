/**
 * Sign in — Google only.
 *
 * There is one way into REEP: a college Google account that is already on the
 * programme roster. The email/password form this screen used to carry is gone
 * from the UI; `POST /api/auth/login` still exists on the API, but only as a
 * dev/CI affordance that production refuses, and nothing here calls it.
 *
 * The flow is backend-terminal, and that is what keeps this component small:
 *
 *   1. a full-page navigation to `/api/auth/sso/google` (an <a href>, not a
 *      fetch — OAuth needs a real top-level redirect, and an XHR cannot leave
 *      the origin and come back with a cookie);
 *   2. the API mints the OAuth `state`, sends the browser to Google, validates
 *      the callback, and sets the same httpOnly `reep_session` cookie the
 *      password path always set;
 *   3. it 302s back into the SPA, which cold-boots, `authGuard` runs,
 *      `AuthService.refresh()` reads `GET /api/auth/me` with the fresh cookie,
 *      and the session is live.
 *
 * So this component never sees a token, never sets a cookie, and does not route
 * by role — step 3 is an ordinary hard refresh, which the guard already handles.
 * The only thing that comes back to *this* screen is a refusal.
 *
 * REFUSALS ARRIVE AS `/login?error=<code>`, and nothing else. The codes in
 * `messageFor` are the contract with the callback docstring in
 * `app/routers/auth.py` — keep them byte-identical to it. An unrecognised code
 * still renders an honest, non-blaming message rather than nothing, but that
 * fallback is a bug report, not a feature: the first version of this screen
 * invented its own vocabulary (`not_on_roster`, `wrong_domain`, …) that shared
 * NOT ONE code with the seven the server emits, so every single refusal —
 * including the commonest one, a personal Gmail — rendered as "the reason given
 * is not one this page knows" and every honest message below was dead code.
 */

import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/auth.service';
import { HOME_FOR_ROLE } from '../../core/session';
import { isNativeApp } from '../../native/platform';

/**
 * `GET /api/auth/sso/status` — the capability probe.
 *
 * The field names are the API's, snake_case and verbatim (`SsoStatus` in
 * app/routers/auth.py). Renaming them on the way in is how a probe silently
 * stops working: `undefined === false` is `false`, so the button would stay
 * live forever on a server that cannot sign anybody in. Everything but
 * `google_available` is optional so the API can grow the shape without
 * breaking this client.
 */
interface SsoStatus {
  google_available: boolean;
  password_login_available?: boolean;
  domain?: string | null;
  reason?: string | null;
}

/** Stand-in until the probe tells us the institutional domain. The domain is
 *  configurable server-side, so it is never hard-coded into a message here. */
const DEFAULT_DOMAIN_LABEL = 'your college';

/**
 * What a person whose account the Main Admin DISABLED reads, wherever they meet
 * it: Google's `?error=sso_disabled` and `?signedOut=disabled`, when a session
 * they were using was cut. The password and code doors print the server's own
 * `DISABLED_SIGN_IN_MESSAGE` (app/routers/auth.py), which is these words.
 */
export const ACCOUNT_DISABLED_MESSAGE =
  'Your account has been disabled for security reasons. ' +
  'Contact the placement office (Main Admin) to resolve the issue.';

/**
 * `?error=` codes the callback redirects with, and what a human should read.
 *
 * THE KEYS ARE THE SERVER'S, `sso_*`-namespaced exactly as the callback
 * docstring in app/routers/auth.py lists them. Each message says what happened
 * and what to do next; none says "invalid" and stops. The one anyone will
 * actually hit is `sso_not_enrolled` — a real Google account that is not on the
 * roster, which is what signing in with a personal Gmail looks like from here.
 *
 * There is deliberately NO wrong-domain message. app/google_auth.py does not use
 * Google's `hd` claim and the server has no code for it: the roster is the
 * allowlist, and app/grant_access.py exists to admit staff whose address is not
 * on the student domain at all. Copy promising a refusal the server cannot emit
 * is a lie a student would act on — the old `wrong_domain` message told people a
 * personal Gmail "cannot sign in here, even if your name is on the roster",
 * when a personal Gmail on the roster signs in perfectly.
 */
function messageFor(code: string, domain: string): string {
  switch (code) {
    case 'sso_not_enrolled':
      return (
        'That Google account is not on the programme roster, so it cannot be ' +
        `signed in. Use your college account — the one ending @${domain}. ` +
        'Access is by roster only; there is no self-registration. If you ' +
        'should be on it, ask the placement office to add you.'
      );
    // Google verified the person and the office has disabled the account.
    case 'sso_disabled':
      return ACCOUNT_DISABLED_MESSAGE;
    case 'sso_unverified_email':
      return (
        'Google has not verified the email address on that account, so it ' +
        'cannot be used to prove who you are. Verify it with Google, then try ' +
        'again.'
      );
    case 'sso_denied':
      return (
        'You stopped at the Google screen, so nothing was signed in. Choose ' +
        'Sign in with Google again when you are ready.'
      );
    case 'sso_state':
      return (
        'That sign-in attempt could not be matched to the one this browser ' +
        'started — usually because it sat on the Google screen for more than ' +
        'ten minutes, was finished in a different browser, or this browser ' +
        'blocks cookies. Start again from this page.'
      );
    case 'sso_config':
      return (
        'Google sign-in is not switched on for this server yet, so there is ' +
        'nothing to sign in to. Tell whoever runs the dashboard.'
      );
    case 'sso_token':
      return (
        'Google verified you, but this server could not complete the exchange ' +
        'that finishes sign-in. That is a fault at our end, not yours — try ' +
        'once more, and tell whoever runs the dashboard if it persists.'
      );
    case 'sso_identity':
      return (
        'The identity token Google sent back did not pass verification, so it ' +
        'was refused rather than trusted. Try once more; if it keeps ' +
        'happening, tell whoever runs the dashboard.'
      );
    // Deliberately does NOT say "a different Google account is already linked
    // to this address". That is true, and it is a fact about whoever held the
    // address before — a college re-issues an institutional address to the next
    // intake, so the person reading this may simply be its new holder. A login
    // screen must not hand a stranger the shape of someone else's account. The
    // server logs both Google principals for the operator who has to decide;
    // this says only what the student needs, which is that retrying will not
    // help and one specific office can fix it.
    case 'sso_identity_mismatch':
      return (
        'This Google account does not match the one on file for this email ' +
        'address, so it was refused. Trying again will give the same result. ' +
        'Contact the placement office — they can check who the address belongs ' +
        'to and put it right.'
      );
    case 'sso_failed':
      return (
        'Google could not complete the sign-in and did not say why. Try once ' +
        'more; if it keeps happening, tell whoever runs the dashboard.'
      );
    default:
      // An unknown code is a bug on our side, not the visitor's. Say so
      // plainly, and quote the code so a support message can carry it.
      return (
        'Sign-in did not complete, and the reason given ' +
        `(${code}) is not one this page knows. Try again, and quote that ` +
        'wording if you need to report it.'
      );
  }
}

/**
 * What a failed password sign-in should say, by status code.
 *
 * The three codes mean genuinely different things and a single "sign-in
 * failed" collapses them into one dead end. 429 in particular must not read as
 * a wrong password: the person has typed the RIGHT one, possibly, and the
 * useful sentence is that Google still works and this counter does not gate it.
 *
 * 403 is two refusals, and only the server knows which: the password door is
 * shut, which it names in `X-Reep-Password-Door` (PASSWORD_DOOR_HEADER in
 * app/routers/auth.py), or the RIGHT password reached an account the office
 * has disabled or removed (`refuse_disabled_sign_in`). The second is the
 * server's own sentence: this page used to answer it with the door's, which
 * sent a disabled person to Google — and Google refuses them too.
 */
export function passwordErrorFor(err: unknown): string {
  const e = err as { status?: number; error?: { detail?: unknown } } | null;
  const status = e?.status;
  switch (status) {
    case 401:
      return 'That email and password did not match an account. Check both, or use Continue with Google.';
    case 403: {
      const doorShut =
        err instanceof HttpErrorResponse && err.headers.get('X-Reep-Password-Door') === 'closed';
      if (!doorShut && typeof e?.error?.detail === 'string') return e.error.detail;
      return (
        'Password sign-in is switched off on this server, so this form cannot ' +
        'work. Use Continue with Google.'
      );
    }
    case 429:
      return (
        'Too many failed attempts for this account or from this network, so ' +
        'password sign-in is paused for a few minutes. Continue with Google ' +
        'still works and is not affected by this.'
      );
    case 0:
    case undefined:
      return 'Could not reach the server. Check your connection and try again.';
    default:
      return `Sign-in failed (error ${status}). Try again, and quote that number if you need to report it.`;
  }
}

/**
 * What a refused one-time code should say. 401 is the only refusal the server
 * gives for a wrong, expired, replayed or misdirected code (one message, on
 * purpose — see login_with_code in app/routers/auth.py); 429 carries its own
 * words, which name Google because that door is not gated by the counter; and
 * 403 is a right code for an account the office disabled after the code was
 * sent (`refuse_disabled_sign_in`), which the server's sentence says.
 */
export function codeErrorFor(err: unknown): string {
  const e = err as { status?: number; error?: { detail?: unknown } } | null;
  switch (e?.status) {
    case 401:
      return 'That code is wrong or has expired.';
    case 403:
      if (typeof e?.error?.detail === 'string') return e.error.detail;
      break;
    case 429:
      return typeof e?.error?.detail === 'string'
        ? e.error.detail
        : 'Too many failed attempts. Wait and try again, or use Continue with Google.';
    case 0:
    case undefined:
      return 'Could not reach the server. Check your connection and try again.';
  }
  return `Sign-in failed (error ${e?.status}). Try again, and quote that number if you need to report it.`;
}

/**
 * The four portals on the picker. DESCRIPTIVE ONLY: the role comes from the
 * roster row behind the verified identity, never from this choice. What the
 * choice changes is the ID field's wording — a student is asked for a USN, a
 * mentor for a faculty email — and, for an ID typed without an "@", the
 * institutional address it is completed to (a USN is a roster email's local
 * part: see app.seed_roster). A wrong pick costs nothing: routing after
 * sign-in follows the real role, not the portal.
 */
interface Portal {
  key: 'student' | 'mentor' | 'admin' | 'alumni';
  label: string;
  icon: string;
  desc: string;
  fieldLabel: string;
  placeholder: string;
  helper: string;
}

/** The admin entrance is NOT a portal card. The chooser offers the three kinds
 *  of person the college has — students, faculty, alumni — and the placement
 *  office comes in through the design's dashed "Main Admin" door below it,
 *  which selects this. Same form, same roster lookup, same role-based routing;
 *  only the card is gone, so an office account is not one of four equal
 *  choices a first-year student has to read past. */
const ADMIN_DOOR: Portal = {
  key: 'admin',
  label: 'Admin',
  icon: 'admin_panel_settings',
  desc: 'Run placement operations',
  fieldLabel: 'Institutional email',
  placeholder: 'placement.admin@bgscet.ac.in',
  helper: 'Use your admin email.',
};

const PORTALS: readonly Portal[] = [
  {
    key: 'student',
    label: 'Student',
    icon: 'school',
    desc: 'Track skills, jobs & growth',
    fieldLabel: 'Institutional email or USN',
    placeholder: 'asha.rao@bgscet.ac.in or 1BG24MBA001',
    helper: 'Use your BGSCET student email or University Seat Number.',
  },
  {
    key: 'mentor',
    label: 'Faculty',
    icon: 'diversity_3',
    desc: 'Guide students, verify skills',
    fieldLabel: 'Institutional email',
    placeholder: 'kavya.n@bgscet.ac.in',
    helper: 'Use your faculty email.',
  },
  {
    key: 'alumni',
    label: 'Alumni',
    icon: 'workspace_premium',
    desc: 'Explore roles, stay connected',
    fieldLabel: 'Registered email',
    placeholder: 'rohan.shetty@alumni.bgscet.ac.in',
    helper: 'Use the email from your alumni registration.',
  },
];

/** localStorage keys for "Remember me" — the ID only, never the password. */
const REMEMBER_ID_KEY = 'reep.login.id';
const REMEMBER_PORTAL_KEY = 'reep.login.portal';

@Component({
  selector: 'app-login',
  standalone: true,
  // FormsModule for the code step's ngModel; the ID + password form is reactive.
  imports: [ReactiveFormsModule, FormsModule, RouterLink],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
  // Coming BACK from Google with the browser's back button restores this page
  // from the bfcache with its JS state intact — including `pending`, which
  // would leave the only button on the screen inert and mid-sentence. pageshow
  // fires on that restore where a constructor does not.
  host: { '(window:pageshow)': 'onRestored()' },
})
export class LoginComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  readonly portals = PORTALS;
  readonly portal = signal<Portal['key']>('student');
  readonly current = computed(
    () =>
      PORTALS.find((p) => p.key === this.portal()) ??
      (this.portal() === 'admin' ? ADMIN_DOOR : PORTALS[0]),
  );

  /** The refusal carried back on the callback redirect, if any. */
  readonly error = signal<string | null>(null);
  /** The raw `?error=` code, kept so the message can be rebuilt once the probe
   *  supplies the real domain (the `sso_not_enrolled` copy names it). */
  private readonly errorCode = signal<string | null>(null);
  /** Set the moment the browser is handed to Google, so the button stops. */
  readonly pending = signal(false);

  /** Fail-open: the button works unless the API positively says it cannot. */
  readonly available = signal(true);
  /** Inside the phone app: no Google button (see the template). */
  readonly native = isNativeApp();
  readonly unavailableReason = signal<string | null>(null);
  readonly domain = signal(DEFAULT_DOMAIN_LABEL);

  /**
   * Whether to render the ID + password form at all.
   *
   * FAILS CLOSED, which is the opposite of `available` above and deliberately
   * so. That one guards a button whose absence would strand everybody, so a
   * broken probe leaves it enabled. This one guards a form that most
   * deployments do not have: the password door is derived server-side (see
   * password_door_open in app/routers/auth.py), so a probe that 404s, times
   * out, or answers something unexpected must leave the form HIDDEN rather
   * than show a box whose every submission would 403. A form that cannot work
   * is worse than no form — it reads as "my password is wrong" rather than
   * "this door is shut".
   */
  readonly passwordAvailable = signal(false);

  /**
   * The probe could not get an answer (network down, a 5xx, a 404 from an
   * older API). The password form still stays HIDDEN, for the reason above,
   * but the screen must not then claim "this server signs in with Google
   * only": that is a statement about the server made when the server could
   * not be asked, and during an outage it told students who hold a password
   * that they do not (DEF-009, testing/docs/05-incident-reports.md).
   */
  readonly probeFailed = signal(false);

  /** The password form's own refusal, kept apart from the `?error=` one so a
   *  stale Google refusal is not re-rendered above a fresh 401. */
  readonly formError = signal<string | null>(null);
  readonly submitting = signal(false);
  readonly showPassword = signal(false);
  readonly forgotOpen = signal(false);
  readonly forgotEmail = signal('');
  readonly forgotBusy = signal(false);
  /** The server's one answer — the same words whether or not the address exists. */
  readonly forgotAnswer = signal<string | null>(null);
  /** `?verified=1|0` — the registration confirmation link lands here. */
  readonly verified = signal<string | null>(this.route.snapshot.queryParamMap.get('verified'));
  /**
   * `?signedOut=elsewhere` — the auth guard sets it when the session was
   * RETIRED rather than expired.
   *
   * A REEP account holds one live session, so signing in on a second device
   * ends the first. Without this the student simply finds themselves back at
   * the login screen mid-task, which reads as a fault in the app rather than as
   * the rule working — and the natural response is to sign in again, dropping
   * the other device, in a loop.
   */
  readonly signedOutElsewhere = signal<boolean>(
    this.route.snapshot.queryParamMap.get('signedOut') === 'elsewhere',
  );

  /** The second step, when the server answers a right password with a
   *  challenge instead of a session: whose mailbox the code went to and how
   *  long it lives. Non-null swaps the password form for the code form. */
  readonly codeStep = signal<{ email: string; minutes: number } | null>(null);
  readonly code = signal('');
  readonly codeBusy = signal(false);
  readonly codeError = signal<string | null>(null);
  /** Field errors render only after a submit attempt, as the design does. */
  private readonly attempted = signal(false);

  readonly form = this.fb.nonNullable.group({
    // `required` passes an ID of spaces, which `idErr` reads as empty and
    // `resolveEmail` would post as "@<domain>": one rule, not two.
    id: ['', [Validators.required, Validators.pattern(/\S/)]],
    password: ['', [Validators.required]],
    remember: [false],
  });

  /** The form's value as a signal. `computed()` re-runs only when a signal it
   *  read has changed, and a reactive form's `.value` is a plain property:
   *  read directly, the two messages below were worked out when `attempted`
   *  changed and never again, so they stayed however the fields were filled. */
  private readonly values = toSignal(this.form.valueChanges, {
    initialValue: this.form.getRawValue(),
  });

  readonly idErr = computed(() => this.attempted() && !(this.values().id ?? '').trim());
  readonly pwErr = computed(() => this.attempted() && !this.values().password);
  readonly idErrMsg = computed(() => `Enter your ${this.current().fieldLabel.toLowerCase()}.`);

  constructor() {
    const code = this.route.snapshot.queryParamMap.get('error');
    if (code) {
      this.errorCode.set(code);
      this.error.set(messageFor(code, this.domain()));
    }
    // `?signedOut=disabled`: the auth guard sets it when a session ended
    // because the Main Admin DISABLED the account (`X-Reep-Session: disabled`).
    // Signing in again cannot help, so it is the error box, not a quiet note.
    if (this.route.snapshot.queryParamMap.get('signedOut') === 'disabled') {
      this.error.set(ACCOUNT_DISABLED_MESSAGE);
    }
    this.restoreRemembered();
    void this.probe();
  }

  /** The design's Main Admin door: pick the Admin portal and put the cursor in
   *  the ID field. Descriptive, like every portal — the role is the roster's. */
  openMainAdmin(): void {
    // 'admin' is no card in PORTALS (see ADMIN_DOOR); this door is how it is picked.
    this.selectPortal('admin');
    queueMicrotask(() => document.querySelector<HTMLInputElement>('input[name="id"]')?.focus());
  }

  /** "Already approved? Sign in": an approved student may not have set a
   *  password yet, and Google works for them either way. Select the Student
   *  portal and bring that button into view and focus. */
  approvedSignIn(): void {
    this.selectPortal('student');
    queueMicrotask(() => {
      const g = document.querySelector<HTMLElement>('.google');
      g?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      g?.focus();
    });
  }

  selectPortal(key: Portal['key']): void {
    this.portal.set(key);
    this.formError.set(null);
  }

  toggleShowPassword(): void {
    this.showPassword.update((v) => !v);
  }

  toggleForgot(): void {
    this.forgotAnswer.set(null);
    this.forgotOpen.update((v) => !v);
  }

  /// Same-origin paths only. This check is a courtesy — the server re-applies
  /// it when it decides where to send the browser after the callback, because
  /// the value crosses an origin we do not control on the way there.
  private get safeNext(): string | undefined {
    const next = this.route.snapshot.queryParamMap.get('next');
    return next && next.startsWith('/') && !next.startsWith('//') ? next : undefined;
  }

  /// A full-page URL, not a router link: the redirect has to leave the SPA.
  /// `next` rides along as a query parameter because the SPA's own query string
  /// does not survive the hop out to Google and back.
  ///
  /// The CANONICAL route, not the `/auth/google/start` compat alias — that one
  /// is `include_in_schema=False` in app/routers/auth.py and exists for old
  /// bookmarks. Pointing the only button on this screen at it would make the
  /// login screen collateral damage the day the alias is tidied away.
  get signInUrl(): string {
    const start = `${environment.apiBase}/auth/sso/google`;
    const next = this.safeNext;
    return next ? `${start}?next=${encodeURIComponent(next)}` : start;
  }

  get subtitle(): string {
    return this.safeNext
      ? 'Sign in to carry on to the page you asked for.'
      : 'Sign in to continue to your REEP workspace.';
  }

  /** "Try again" on the unreachable-server note. */
  retryProbe(): void {
    void this.probe();
  }

  /**
   * Ask the API whether Google sign-in is actually configured.
   *
   * This is the gate `/api/interview/status` and `/api/voice/status` already
   * put in front of their buttons, for the same reason: a live-looking button
   * with no credentials behind it produces exactly the "why is it broken"
   * report this codebase has learned to avoid. It fails OPEN — a probe that
   * 404s (endpoint not deployed), times out, or answers something unexpected
   * leaves the button enabled, because a broken probe must never become the
   * reason nobody can sign in.
   *
   * `probeFailed` is set true on any failure and false ONLY once an answer
   * arrives, never before the request: resetting it first would flash the
   * "Google only" sentence (the very claim DEF-009 removed) while a retry is
   * in flight.
   */
  private async probe(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/auth/sso/status`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.probeFailed.set(true);
        return;
      }
      const status = (await res.json()) as SsoStatus;
      this.probeFailed.set(false);
      if (status.domain) {
        this.domain.set(status.domain);
        // Re-render the refusal now the real domain is known: `sso_not_enrolled`
        // is the message that names it, and naming the wrong one sends a
        // student to the wrong account.
        const code = this.errorCode();
        if (code) this.error.set(messageFor(code, status.domain));
      }
      if (status.google_available === false) {
        this.available.set(false);
        this.unavailableReason.set(
          status.reason ||
            'Google sign-in is not configured on this server, so there is no ' +
              'way in yet. Tell whoever runs the dashboard.',
        );
      }
      // `=== true`, not truthy: the field is optional on the interface, and an
      // older API that does not send it must leave the form hidden rather than
      // have `undefined` decide.
      this.passwordAvailable.set(status.password_login_available === true);
    } catch {
      // Fail open for Google, closed for the password form. See both docstrings.
      this.probeFailed.set(true);
    }
  }

  /**
   * The address to send. An ID typed without an "@" is completed to the
   * institutional domain the probe reported — a USN is exactly the local part
   * `app.seed_roster` derives a student's address from, so `1BG24MBA001`
   * becomes `1bg24mba001@<domain>`. Lower-cased because the roster is.
   */
  private resolveEmail(id: string): string {
    const trimmed = id.trim();
    if (trimmed.includes('@')) return trimmed.toLowerCase();
    const domain = this.domain().replace(/^@/, '');
    return `${trimmed.toLowerCase()}@${domain}`;
  }

  /**
   * ID + password sign-in, when the server says it offers it.
   *
   * Unlike the Google path this is an ordinary XHR: the API sets the same
   * httpOnly `reep_session` cookie on the response, so there is no redirect to
   * ride and the SPA can route immediately. Routing is BY ROLE through the same
   * HOME_FOR_ROLE map `homeRedirectGuard` uses — not the portal picked above
   * and not a hardcoded '/student', which is how the office account used to land on a
   * screen they have no rows for.
   */
  /** POST /auth/forgot. Shows the server's words verbatim: they are written to
   *  be identical for a real, a Google-only and an unknown address, and any
   *  rephrasing here would be the place that difference crept back in. */
  async sendReset(): Promise<void> {
    const email = this.forgotEmail().trim().toLowerCase();
    if (!email || this.forgotBusy()) return;
    this.forgotBusy.set(true);
    try {
      const res = await fetch(`${environment.apiBase}/auth/forgot`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      this.forgotAnswer.set(
        typeof body.detail === 'string'
          ? body.detail
          : res.ok
            ? 'If that address belongs to a REEP account, a link has been sent to it.'
            : 'Could not send a reset link just now. Try again in a moment.',
      );
    } catch {
      this.forgotAnswer.set('Could not reach the server. Try again in a moment.');
    } finally {
      this.forgotBusy.set(false);
    }
  }

  async submitPassword(): Promise<void> {
    this.attempted.set(true);
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    // Both fields are filled, so neither message has anything to say — and a
    // refusal below empties the password, which must not raise "Enter your
    // password." beside the server's answer to this attempt.
    this.attempted.set(false);
    this.submitting.set(true);
    this.formError.set(null);
    // A fresh attempt supersedes whatever Google said last time; leaving both
    // on screen reads as two simultaneous failures.
    this.error.set(null);
    const { id, password, remember } = this.form.getRawValue();
    try {
      const result = await this.auth.login(this.resolveEmail(id), password);
      if ('otp_required' in result) {
        // The password was right and a code is on its way; nothing is signed
        // in yet. Swap to the code form, and drop the password from the DOM
        // for the same shared-lab reason as the catch branch below.
        this.codeStep.set({ email: result.email, minutes: result.expires_in_minutes });
        this.code.set('');
        this.codeError.set(null);
        this.form.patchValue({ password: '' });
        return;
      }
      this.remember(remember ? id.trim() : null);
      const next = this.safeNext;
      await this.router.navigateByUrl(next ?? HOME_FOR_ROLE[result.role] ?? '/student');
    } catch (err: unknown) {
      this.formError.set(passwordErrorFor(err));
      // Never leave a password in a field behind a failed attempt: the next
      // person at a shared lab machine can read it out of the DOM.
      this.form.patchValue({ password: '' });
    } finally {
      this.submitting.set(false);
    }
  }

  /**
   * The second step: the six-digit code from the email. Success routes by
   * role exactly as `submitPassword` would have; a refusal keeps the code
   * form up with the server's reason, so the person can retype rather than
   * start over with the password.
   */
  async submitCode(): Promise<void> {
    const step = this.codeStep();
    const code = this.code().trim();
    if (!step || this.codeBusy() || !/^\d{6}$/.test(code)) return;
    this.codeBusy.set(true);
    this.codeError.set(null);
    try {
      const session = await this.auth.loginWithCode(step.email, code);
      const { id, remember } = this.form.getRawValue();
      this.remember(remember ? id.trim() : null);
      const next = this.safeNext;
      await this.router.navigateByUrl(next ?? HOME_FOR_ROLE[session.role] ?? '/student');
    } catch (err: unknown) {
      this.codeError.set(codeErrorFor(err));
      this.code.set('');
    } finally {
      this.codeBusy.set(false);
    }
  }

  /** Back to the ID + password form. The unspent code simply expires. */
  cancelCode(): void {
    this.codeStep.set(null);
    this.code.set('');
    this.codeError.set(null);
  }

  /** "Remember me" keeps the ID and the portal on THIS device — never the
   *  password. Storage can be unavailable (private window, locked-down lab
   *  browser), and that is not worth an error: it simply does not remember. */
  private remember(id: string | null): void {
    try {
      if (id) {
        localStorage.setItem(REMEMBER_ID_KEY, id);
        localStorage.setItem(REMEMBER_PORTAL_KEY, this.portal());
      } else {
        localStorage.removeItem(REMEMBER_ID_KEY);
        localStorage.removeItem(REMEMBER_PORTAL_KEY);
      }
    } catch {
      // Nothing to do — see the docstring.
    }
  }

  private restoreRemembered(): void {
    try {
      const id = localStorage.getItem(REMEMBER_ID_KEY);
      if (!id) return;
      const portal = localStorage.getItem(REMEMBER_PORTAL_KEY) as Portal['key'] | null;
      // The Main Admin door is remembered too: `remember()` stores whatever
      // `portal()` holds, and 'admin' is no card in PORTALS (see ADMIN_DOOR).
      if (portal && (portal === ADMIN_DOOR.key || PORTALS.some((p) => p.key === portal))) {
        this.portal.set(portal);
      }
      this.form.patchValue({ id, remember: true });
    } catch {
      // Same as above.
    }
  }

  /// Does NOT preventDefault — the anchor's navigation is the whole mechanism.
  /// This only stops the button re-arming while the browser is on its way out.
  beginSignIn(): void {
    if (!this.available()) return;
    this.pending.set(true);
    this.error.set(null);
  }

  /// The page came back into view — from the bfcache, another tab, or the back
  /// button. Whatever it was, we are no longer on our way to Google, so re-arm.
  onRestored(): void {
    this.pending.set(false);
  }
}
