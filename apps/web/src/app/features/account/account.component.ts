/**
 * My account — one screen for the login the person signed in with.
 *
 * Board: docs/redesign-2026-09/design/admin/Account.html
 * Spec:  docs/redesign-2026-09/02-admin-console-spec.md §24
 *
 * EVERY ROLE REACHES THIS, from the avatar menu, so nothing here may assume a
 * console. The panels hide themselves where they do not apply rather than
 * rendering a control that answers 403: a STUDENT and an ALUMNUS have no
 * signature (`/api/staff/signature` is `require_mentor`), so that card is not
 * drawn for them at all.
 *
 * THE PASSWORD PANEL IS DERIVED, NOT ASSUMED, and the derivation is the same
 * one the server makes. `password_door_open(db)` in app/routers/auth.py is
 * dev/CI always, `PASSWORD_LOGIN=true` always, `PASSWORD_LOGIN=false` never,
 * and otherwise open exactly when some account holds a real `scrypt:` hash —
 * and `GET /api/auth/sso/status` publishes that one bit as
 * `password_login_available`. So when the server says the door is SHUT, this
 * account holds no usable password either (no key exists anywhere, or keys are
 * forced off), and the honest screen is a sentence about Google rather than a
 * change-password form that cannot succeed. When it says OPEN the bit is about
 * the deployment and not about this row — accounts minted by `grant_access` and
 * `seed_roster` hold the SSO_ONLY sentinel — so the only per-account truth is
 * the 409 `POST /auth/change-password/code` answers, and that swaps the form
 * for the same sentence the moment it arrives. There is STILL no endpoint that
 * says "does THIS account hold a password": B15 put `google_linked`,
 * `last_sign_ins` and `notification_prefs` on `/auth/me` and nothing about the
 * password, so that 409 remains the only per-account truth.
 *
 * ONE PROOF, NEVER TWO. `/auth/change-password` takes the emailed code or the
 * current password and refuses both together (`_authorised_to_change`). This
 * screen offers the CODE, like `/account/password` does: a student who set
 * their first password last week does not reliably remember it, and the code
 * proves the mailbox is readable now. The current-password path stays the
 * API's, for staff and for the tests; it is not a second form here.
 *
 * B15 AND B3.6 HAVE LANDED, so the four controls this screen drew disabled are
 * live: the Google link state and `POST /auth/google/unlink`, the recent
 * sign-ins, the notification switches (`PUT /auth/notification-prefs`) and
 * `POST /auth/sign-out-everywhere`. `PendingControlDirective` is gone from this
 * component with them.
 *
 * ALL THREE READS ARRIVE ON ONE CALL. `google_linked`, `last_sign_ins` and
 * `notification_prefs` ride on `GET /api/auth/me` — the call `AuthService`
 * already owns — so this screen adds no endpoint of its own and its "refresh"
 * is `AuthService.refresh()`. A second raw `fetch` of `/auth/me` here would
 * paint the right numbers over a session signal the rest of the app reads and
 * would then be holding the old ones.
 *
 * `google_linked` IS `bool | null` AND THE NULL IS LOAD-BEARING. Only `/me`
 * answers it; `/login` mints the same model and leaves it null. A client that
 * reads absent as `false` tells somebody their Google sign-in is unlinked on
 * the screen immediately after they used it, so "not asked" is its own
 * rendering here and never "not linked".
 *
 * WHAT THE BOARD STILL DRAWS THAT NO ENDPOINT ANSWERS is the digest FREQUENCY
 * per topic (daily / weekly / immediately) and the five topics beside it, and
 * "All events". The catalogue behind `notification_prefs` holds exactly one
 * switch and it is on or off, so the card says that rather than drawing five
 * dropdowns over one boolean; and `/me` carries the last ten sign-ins, which is
 * the whole list REEP keeps for a person, so the button beside it re-reads that
 * list instead of promising a longer one. Neither is a disabled control with a
 * phase on it, because no phase adds them: a "Available with Phase N" that
 * names no phase is the same lie as a dead button.
 */

import { Component, ErrorHandler, computed, inject, signal } from '@angular/core';
import {
  AbstractControl,
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { Router } from '@angular/router';

import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/auth.service';
import {
  UNREADABLE_FILE_MESSAGE,
  UPLOAD_DROPPED_MESSAGE,
  readPickedFile,
} from '../../core/picked-file';
import type { Role, SessionPayload } from '../../core/session';
import { PluralPipe } from '../../shared/text/plural.pipe';

/** `GET /api/auth/sso/status` — which doors this SERVER offers. Unauthenticated
 *  by design and account-free: it reports the deployment, never a row. */
interface SignInDoors {
  google_available: boolean;
  password_login_available?: boolean;
  domain?: string | null;
  reason?: string | null;
}

/** One row of `last_sign_ins` — `SignInOut` in app/schemas/auth.py.
 *
 *  SUCCESSES ONLY, which the card says out loud: `login_events` records a
 *  sign-in that happened, and a failed attempt is the brute-force limiter's
 *  business. A "Result" column whose every value is "OK" would read as "nobody
 *  has ever tried and failed", which this list cannot know. */
interface SignIn {
  at: string;
  /** `password`, `code`, `google` or `activation` — auth.py's `DOOR_*`. */
  door: string;
  /** THE SERVER'S VIEW OF WHERE THE REQUEST CAME FROM, and behind the college's
   *  load balancer that is the balancer, identically for every row. Labelled as
   *  such on screen rather than as "your device's address", which is the one
   *  reading that would make an unfamiliar value alarming for no reason. */
  ip: string | null;
  user_agent: string | null;
}

/** One switch from auth.py's `NOTIFICATION_PREFS` — `NotificationPrefOut`. */
interface NotificationPref {
  enabled: boolean;
  label: string;
  /** False means NOTHING READS THIS PREFERENCE YET. Shown as "not wired yet",
   *  and `PUT /auth/notification-prefs` answers 422 to an attempt to switch it
   *  off — B2.2's rule for an unwired feature switch, applied to the second
   *  kind of switch. */
  enforced: boolean;
}

/** The three B15 fields `GET /api/auth/me` adds to the session payload.
 *
 *  DECLARED HERE AND NOT ON `SessionPayload`, because `SessionPayload` is the
 *  claim set every screen and guard reads and these three are answered by one
 *  endpoint out of four. `AuthService` holds the parsed body of that call, so
 *  the fields are present at runtime; this is the narrowing that reads them.
 *  Each is optional AND nullable on purpose — see the class docstring. */
interface AccountFacts {
  google_linked?: boolean | null;
  last_sign_ins?: SignIn[];
  notification_prefs?: Record<string, NotificationPref>;
}

/** `GET /api/staff/signature` — app/routers/signature.py `SignatureOut`. */
interface SignatureInfo {
  present: boolean;
  mime_type: string | null;
  size_bytes: number | null;
  uploaded_at: string | null;
}

/** What each role is called on screen. Mirrors `ROLE_LABEL` in
 *  layout/app-shell.component.ts — the console's vocabulary is Student /
 *  Faculty / Alumni and Main Admin, and "mentor" is a stored value. */
const ROLE_LABEL: Record<Role, string> = {
  STUDENT: 'Student',
  MENTOR: 'Faculty',
  ADMIN: 'Main Admin',
  ALUMNI: 'Alumni',
};

/** The roles `require_mentor` admits, and therefore the only ones with a
 *  signature slot (app/routers/signature.py). */
const ROLES_WITH_A_SIGNATURE: Role[] = ['MENTOR', 'ADMIN'];

/** `app/set_password.py::MIN_PASSWORD_LENGTH`. The server refuses anything
 *  shorter with a sentence of its own; this only stops the round trip. */
const MIN_PASSWORD_LENGTH = 12;

/** A signature is a small image (`MAX_SIGNATURE_BYTES`, 2 MB). */
const MAX_SIGNATURE_MEGABYTES = 2;

/** What each `login_events.door` is called on screen.
 *
 *  An unrecognised door renders its stored value rather than a guess: the model
 *  comment names a fifth (`reset`) that `DOOR_*` deliberately never writes, and
 *  a new door is meant to be a deploy rather than a type migration — so this
 *  map will be behind the server one day, and being visibly behind beats
 *  labelling an unknown sign-in "Password". */
const DOOR_LABEL: Record<string, string> = {
  password: 'Email and password',
  code: 'Emailed code',
  google: 'Google',
  activation: 'Activation link',
};

/** The two new passwords have to agree before either is worth posting. */
function bothPasswordsMatch(group: AbstractControl): ValidationErrors | null {
  const chosen = group.get('new_password')?.value;
  const repeated = group.get('confirm')?.value;
  if (!chosen || !repeated) {
    return null;
  }
  return chosen === repeated ? null : { mismatch: true };
}

@Component({
  selector: 'app-account',
  standalone: true,
  imports: [ReactiveFormsModule, PluralPipe],
  templateUrl: './account.component.html',
  styleUrl: './account.component.scss',
})
export class AccountComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly errorHandler = inject(ErrorHandler);

  readonly minPasswordLength = MIN_PASSWORD_LENGTH;
  readonly maxSignatureMegabytes = MAX_SIGNATURE_MEGABYTES;

  // ---- who is signed in ---------------------------------------------------

  readonly session = this.auth.session;
  readonly displayName = computed(() => this.session()?.name ?? '');
  readonly emailAddress = computed(() => this.session()?.email ?? '');
  readonly roleLabel = computed(() => ROLE_LABEL[this.session()?.role as Role] ?? 'Student');
  readonly holdsASignatureSlot = computed(() => {
    const role = this.session()?.role;
    if (!role) {
      return false;
    }
    return ROLES_WITH_A_SIGNATURE.includes(role);
  });

  readonly signingOut = signal(false);

  // ---- the B15 facts, all three from one GET /api/auth/me -----------------

  /** The session payload AS `/auth/me` ANSWERS IT. The service holds the parsed
   *  body; `SessionPayload` types only the claims every screen shares. */
  private readonly facts = computed(
    () => this.auth.session() as (SessionPayload & AccountFacts) | null,
  );

  /** True while `/auth/me` is being re-read — the initial load and the
   *  sign-ins "Refresh". */
  readonly rereadingAccount = signal(false);

  /** The re-read came back with no session at all. Under one device at a time
   *  that is a real event (this account was signed in somewhere else), and the
   *  screen says so instead of rendering every card as an empty state. */
  readonly accountUnavailable = computed(() => !this.rereadingAccount() && this.facts() === null);

  // ---- Google identity ----------------------------------------------------

  /** The unlink's own answer, which is authoritative and arrives before the
   *  next `/me`. Null until this screen has unlinked something. */
  private readonly googleLinkAfterWrite = signal<boolean | null>(null);

  /** `null` / absent is NOT ASKED and must never render as "not linked" — only
   *  `/auth/me` answers this field and `/login` mints the same model without
   *  it. So the template asks whether the answer is KNOWN before it reads it. */
  readonly googleLinkKnown = computed(() => {
    if (this.googleLinkAfterWrite() !== null) {
      return true;
    }
    const linked = this.facts()?.google_linked;
    return linked === true || linked === false;
  });

  readonly googleLinked = computed(
    () => (this.googleLinkAfterWrite() ?? this.facts()?.google_linked) === true,
  );

  readonly confirmGoogleUnlink = signal(false);
  readonly unlinkingGoogle = signal(false);
  readonly googleError = signal<string | null>(null);
  readonly googleFlash = signal<string | null>(null);

  // ---- sign out everywhere ------------------------------------------------

  readonly confirmSignOutEverywhere = signal(false);
  readonly signingOutEverywhere = signal(false);
  readonly signOutEverywhereError = signal<string | null>(null);

  // ---- notification preferences -------------------------------------------

  /** The PUT's own answer — the dense map, every key, as the server now holds
   *  it. Preferred over the session copy until the next `/me` re-read. */
  private readonly prefsAfterWrite = signal<Record<string, NotificationPref> | null>(null);

  readonly notificationPrefs = computed(() => {
    const map = this.prefsAfterWrite() ?? this.facts()?.notification_prefs ?? {};
    return Object.entries(map).map(([key, pref]) => ({ key, ...pref }));
  });

  readonly savingPrefKey = signal<string | null>(null);
  readonly prefsError = signal<string | null>(null);
  readonly prefsFlash = signal<string | null>(null);

  // ---- recent sign-ins ----------------------------------------------------

  readonly recentSignIns = computed(() => this.facts()?.last_sign_ins ?? []);

  // ---- which doors this server offers -------------------------------------

  readonly doors = signal<SignInDoors | null>(null);
  /** Until the probe has answered, no claim about either door is made. */
  readonly signInDoorsKnown = computed(() => this.doors() !== null);
  readonly googleDoorOpen = computed(() => this.doors()?.google_available === true);
  readonly collegeDomain = computed(() => this.doors()?.domain ?? null);

  /** The server's own sentence for why Google sign-in is not offered, or null
   *  when it is offered (or when the probe has not answered). */
  readonly googleDoorShutReason = computed(() => {
    const doors = this.doors();
    if (doors === null || doors.google_available) {
      return null;
    }
    return doors.reason ?? null;
  });

  /** The server says no account can sign in with a password here. See the
   *  class docstring: that answer also settles this account. */
  readonly passwordDoorShut = computed(() => this.doors()?.password_login_available === false);

  /** 409 from `/auth/change-password/code`: this row holds the SSO_ONLY
   *  sentinel, whatever the deployment's door does. */
  readonly accountHoldsNoPassword = signal(false);

  readonly signsInWithGoogleOnly = computed(
    () => this.accountHoldsNoPassword() || this.passwordDoorShut(),
  );

  // ---- change password: request a code, then spend it ---------------------

  readonly sendingCode = signal(false);
  readonly codeSent = signal(false);
  /** What the server said when it mailed the code — it names the address and
   *  how long the code lives, so neither is guessed here. */
  readonly codeNote = signal<string | null>(null);
  readonly savingPassword = signal(false);
  readonly passwordError = signal<string | null>(null);
  readonly passwordFlash = signal<string | null>(null);

  readonly passwordForm = new FormGroup(
    {
      code: new FormControl('', {
        nonNullable: true,
        validators: [Validators.required, Validators.pattern(/^\d{6}$/)],
      }),
      new_password: new FormControl('', {
        nonNullable: true,
        validators: [Validators.required, Validators.minLength(MIN_PASSWORD_LENGTH)],
      }),
      confirm: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    },
    { validators: [bothPasswordsMatch] },
  );

  // ---- signature ----------------------------------------------------------

  readonly signature = signal<SignatureInfo | null>(null);
  readonly signatureBusy = signal(false);
  readonly signatureError = signal<string | null>(null);
  readonly signatureFlash = signal<string | null>(null);
  readonly confirmSignatureRemoval = signal(false);

  /** The read failed. A FAILED READ IS NOT AN EMPTY SLOT: rendering "No
   *  signature on file" because the GET 500'd tells a mentor who has one that
   *  they have none, and the next thing they do is upload over it. The card
   *  says what happened and offers the read again. */
  readonly signatureLoadFailed = signal(false);

  /** Null until `GET /staff/signature` has answered — the loading state. */
  readonly signatureStillLoading = computed(() => this.signature() === null);

  /** The row when there IS one on file, so the template reads its fields
   *  without a chain of optional access. */
  readonly signatureOnFile = computed(() => {
    const info = this.signature();
    if (info === null || !info.present) {
      return null;
    }
    return info;
  });

  /** The preview, cache-busted by the upload time so a replacement shows at
   *  once rather than after a hard refresh. */
  readonly signatureImageUrl = computed(() => {
    const info = this.signatureOnFile();
    if (info === null) {
      return null;
    }
    const version = encodeURIComponent(info.uploaded_at ?? '');
    return `${environment.apiBase}/staff/signature/image?v=${version}`;
  });

  constructor() {
    void this.loadSignInDoors();
    void this.rereadAccount();
    if (this.holdsASignatureSlot()) {
      void this.loadSignature();
    }
  }

  // ---- loads --------------------------------------------------------------

  /** Re-read `GET /api/auth/me` THROUGH THE SERVICE.
   *
   *  This is the whole of this screen's own data load and it is also the
   *  sign-ins "Refresh": the three B15 fields ride on the one call the rest of
   *  the app already makes, so a raw `fetch` here would leave `AuthService`'s
   *  session — the thing the shell, the guards and the nav read — holding the
   *  values this screen has just replaced on the page.
   *
   *  IT RUNS ON EVERY VISIT rather than only when the fields are absent. They
   *  can be absent (a session set by `/login`, which mints the same model and
   *  answers all three at their defaults) and they can equally be STALE — a
   *  sign-in list is a security screen's reason for existing and a cached one
   *  is worth less than no list. The write answers held above are dropped here,
   *  because `/me` is now the fresher of the two. */
  async rereadAccount(): Promise<void> {
    if (this.rereadingAccount()) {
      return;
    }
    this.rereadingAccount.set(true);
    try {
      const session = await this.auth.refresh();
      if (session) {
        this.googleLinkAfterWrite.set(null);
        this.prefsAfterWrite.set(null);
      }
    } finally {
      this.rereadingAccount.set(false);
    }
  }

  /** Ask which sign-in doors exist. It fails CLOSED for the password form and
   *  OPEN for Google, the same way the login screen's probe does: a probe that
   *  cannot answer must never become the reason a form is hidden AND must never
   *  promise a door the server does not have. */
  private async loadSignInDoors(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/auth/sso/status`, {
        credentials: 'include',
      });
      if (!response.ok) {
        return;
      }
      this.doors.set((await response.json()) as SignInDoors);
    } catch {
      // Leave `doors` null: the password panel then shows the form, which is
      // the state that lets somebody finish what they came to do.
    }
  }

  async loadSignature(): Promise<void> {
    this.signatureError.set(null);
    this.signatureLoadFailed.set(false);
    try {
      const response = await fetch(`${environment.apiBase}/staff/signature`, {
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error(await this.detailOf(response));
      }
      this.signature.set((await response.json()) as SignatureInfo);
    } catch (err) {
      this.signature.set(null);
      this.signatureLoadFailed.set(true);
      this.signatureError.set(this.messageFrom(err, 'Could not load your signature.'));
    }
  }

  // ---- change password ----------------------------------------------------

  /** Ask for the code. Re-asking supersedes the previous one server-side, so
   *  there is never more than one live code in the mailbox. */
  async sendCode(): Promise<void> {
    if (this.sendingCode()) {
      return;
    }
    this.sendingCode.set(true);
    this.passwordError.set(null);
    this.passwordFlash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/auth/change-password/code`, {
        method: 'POST',
        credentials: 'include',
      });
      if (response.status === 409) {
        this.accountHoldsNoPassword.set(true);
        return;
      }
      if (!response.ok) {
        this.passwordError.set(await this.detailOf(response));
        return;
      }
      const body = (await response.json()) as { message?: string };
      this.codeNote.set(body.message ?? null);
      this.codeSent.set(true);
      this.passwordForm.controls.code.reset('');
    } catch {
      this.passwordError.set('Could not reach the server. Try again in a moment.');
    } finally {
      this.sendingCode.set(false);
    }
  }

  /** Spend the code and set the new password. Other devices go; this one stays,
   *  because the API re-issues this cookie at the new token_version before it
   *  answers and `refresh()` reads it back. */
  async changePassword(): Promise<void> {
    if (this.passwordForm.invalid || this.savingPassword()) {
      this.passwordForm.markAllAsTouched();
      return;
    }
    this.savingPassword.set(true);
    this.passwordError.set(null);
    this.passwordFlash.set(null);
    const { code, new_password } = this.passwordForm.getRawValue();
    try {
      const response = await fetch(`${environment.apiBase}/auth/change-password`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, new_password }),
      });
      if (response.status === 409) {
        this.accountHoldsNoPassword.set(true);
        return;
      }
      if (!response.ok) {
        await this.reportRefusedChange(response);
        return;
      }
      await this.auth.refresh();
      this.resetPasswordForm();
      this.passwordFlash.set(
        'Password changed. Your other devices have been signed out; this one stays.',
      );
    } catch {
      this.passwordError.set('Could not reach the server. Try again in a moment.');
    } finally {
      this.savingPassword.set(false);
    }
  }

  /** A REFUSED CODE IS NOT SPENT. `consume_user_code` compares first and only
   *  then runs the single-use UPDATE, so a wrong or expired code leaves the
   *  live one untouched (`app/account_links.py`) — and a 422 is refused BEFORE
   *  the proof is consumed at all (`change_password` checks
   *  `password_problem` first, on purpose). So the person stays on step two
   *  with the code they were emailed still good: the field is cleared, because
   *  the digits in it are the ones that were just refused, and "Send a new
   *  code" is beside it for the case where the code really has expired.
   *  Dropping back to step one here forced a re-issue after every typo. */
  private async reportRefusedChange(response: Response): Promise<void> {
    this.passwordError.set(await this.detailOf(response));
    this.passwordForm.patchValue({ code: '' });
    this.passwordForm.controls.code.markAsUntouched();
  }

  cancelPasswordChange(): void {
    this.resetPasswordForm();
    this.passwordError.set(null);
  }

  private resetPasswordForm(): void {
    this.passwordForm.reset();
    this.codeSent.set(false);
    this.codeNote.set(null);
  }

  // ---- signature ----------------------------------------------------------

  async uploadSignature(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const chosenFile = input.files?.[0];
    input.value = '';
    if (!chosenFile || this.signatureBusy()) {
      return;
    }
    this.signatureBusy.set(true);
    this.signatureError.set(null);
    this.signatureFlash.set(null);
    try {
      // Upload an in-memory copy (core/picked-file.ts): on a phone, a photo
      // held by Drive, Photos or another app can fail to be read at send time.
      const copy = await readPickedFile(chosenFile);
      if (!copy) {
        this.signatureError.set(UNREADABLE_FILE_MESSAGE);
        this.errorHandler.handleError(
          new Error('Account signature upload: the picked image could not be read on the device'),
        );
        return;
      }
      const form = new FormData();
      form.append('file', copy, chosenFile.name);
      const response = await fetch(`${environment.apiBase}/staff/signature`, {
        method: 'PUT',
        credentials: 'include',
        body: form,
      });
      if (!response.ok) {
        this.signatureError.set(await this.detailOf(response));
        return;
      }
      this.signature.set((await response.json()) as SignatureInfo);
      this.signatureFlash.set('Signature saved. It appears on your leave papers from now on.');
    } catch {
      // No HTTP answer at all. This used to print the browser's own words
      // ("Failed to fetch"); reported because no server saw it.
      this.signatureError.set(UPLOAD_DROPPED_MESSAGE);
      this.errorHandler.handleError(
        new Error('Account signature upload failed: no HTTP response (network error on the device)'),
      );
    } finally {
      this.signatureBusy.set(false);
    }
  }

  async removeSignature(): Promise<void> {
    if (this.signatureBusy()) {
      return;
    }
    this.signatureBusy.set(true);
    this.signatureError.set(null);
    this.signatureFlash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/staff/signature`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error(await this.detailOf(response));
      }
      this.confirmSignatureRemoval.set(false);
      this.signatureFlash.set(
        'Signature removed. Your leave papers show your name and the time only.',
      );
      await this.loadSignature();
    } catch (err) {
      this.signatureError.set(this.messageFrom(err, 'Could not remove the signature.'));
    } finally {
      this.signatureBusy.set(false);
    }
  }

  // ---- Google identity ----------------------------------------------------

  /** `POST /api/auth/google/unlink`.
   *
   *  THE REFUSAL IS THE FEATURE AND IT IS THE SERVER'S TO MAKE. Unlinking an
   *  account that holds no usable password deletes its only door, and REEP
   *  mints exactly that account by design (`grant_access` and `seed_roster`
   *  write the SSO_ONLY sentinel). The endpoint checks two things this client
   *  cannot see — a real `scrypt:` hash on THIS row, and whether the password
   *  door is open at all — and answers 409 with the sentence to show. So there
   *  is no second guess here: the button is offered, and the server's own words
   *  are what the person reads back.
   *
   *  It signs nobody out, so the session stands and there is nowhere to
   *  navigate; the row re-renders from the answer's `google_linked`. */
  async unlinkGoogle(): Promise<void> {
    if (this.unlinkingGoogle()) {
      return;
    }
    this.unlinkingGoogle.set(true);
    this.googleError.set(null);
    this.googleFlash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/auth/google/unlink`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) {
        this.googleError.set(await this.detailOf(response));
        return;
      }
      const body = (await response.json()) as { detail?: string; google_linked?: boolean };
      this.googleLinkAfterWrite.set(body.google_linked ?? false);
      this.confirmGoogleUnlink.set(false);
      this.googleFlash.set(body.detail ?? 'Google sign-in unlinked.');
    } catch {
      this.googleError.set('Could not reach the server. Try again in a moment.');
    } finally {
      this.unlinkingGoogle.set(false);
    }
  }

  // ---- notification preferences -------------------------------------------

  /** `PUT /api/auth/notification-prefs`, ONE KEY AT A TIME.
   *
   *  The endpoint takes a PARTIAL map deliberately: every key sent is set and
   *  every key omitted keeps what it had, so a screen that posted the whole map
   *  would overwrite a preference added by a later deploy with this build's
   *  default, on the first save by anybody who had not reloaded. Sending the
   *  one switch that was just clicked is that contract used as intended.
   *
   *  The answer is the DENSE map — every key, as the server now holds it — so
   *  it is taken as the new state rather than the click being assumed to have
   *  won. */
  async setNotificationPref(
    key: string,
    wanted: boolean,
    /** The checkbox itself, SO A REFUSAL CAN PUT IT BACK. `[checked]` is bound
     *  to the value the server gave us, and the click changed the DOM without
     *  changing that value — so Angular has nothing to re-render, and a 422
     *  would leave a ticked box sitting above the sentence explaining that it
     *  was not saved. Restoring it is the only way the control and the error
     *  message can agree. */
    box?: HTMLInputElement,
  ): Promise<void> {
    if (this.savingPrefKey() !== null) {
      return;
    }
    const restore = (): void => {
      if (box) {
        box.checked = !wanted;
      }
    };
    this.savingPrefKey.set(key);
    this.prefsError.set(null);
    this.prefsFlash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/auth/notification-prefs`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prefs: { [key]: wanted } }),
      });
      if (!response.ok) {
        restore();
        this.prefsError.set(await this.detailOf(response));
        return;
      }
      const body = (await response.json()) as Record<string, NotificationPref>;
      this.prefsAfterWrite.set(body);
      const saved = body[key];
      this.prefsFlash.set(
        saved
          ? `Saved — ${saved.label.toLocaleLowerCase()}: ${saved.enabled ? 'on' : 'off'}.`
          : 'Saved.',
      );
    } catch {
      restore();
      this.prefsError.set('Could not reach the server. Try again in a moment.');
    } finally {
      this.savingPrefKey.set(null);
    }
  }

  // ---- this device --------------------------------------------------------

  /** Sign out HERE. One device at a time means there is never another live
   *  session to end as well; ending every token the account has ever held is
   *  "Sign out everywhere" below. */
  async signOut(): Promise<void> {
    if (this.signingOut()) {
      return;
    }
    this.signingOut.set(true);
    try {
      await this.auth.logout();
      await this.router.navigate(['/login']);
    } finally {
      this.signingOut.set(false);
    }
  }

  /** `POST /api/auth/sign-out-everywhere` — and THIS DEVICE GOES TOO.
   *
   *  That is the endpoint's design, not an accident of it: keeping this session
   *  alive would mean re-issuing a cookie at the new `token_version`, and "sign
   *  out everywhere" would then have an exception in it — the one sentence
   *  somebody reaching for this button must not have to read. So the server
   *  deletes the cookie and every token minted before now is refused, this
   *  page's included.
   *
   *  WHICH MEANS THE SCREEN CANNOT STAY. Every request this component would
   *  make next is a 401, so it takes the app's ordinary signed-out path — clear
   *  the session the service holds, go to `/login` — exactly as "Sign out"
   *  above does. `auth.logout()` is idempotent and reads the cookie optionally,
   *  so posting it against the cookie the server has just deleted is a no-op
   *  that clears the signal, and a failure there must still not strand anybody
   *  on a dead screen.
   *
   *  Confirmed first, because it is the only button here that ends a session
   *  the person cannot see — and the reason they reach for it (a shared lab
   *  machine, a phone that was taken) is the reason it must not be a slip. */
  async signOutEverywhere(): Promise<void> {
    if (this.signingOutEverywhere()) {
      return;
    }
    this.signingOutEverywhere.set(true);
    this.signOutEverywhereError.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/auth/sign-out-everywhere`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) {
        this.signOutEverywhereError.set(await this.detailOf(response));
        return;
      }
    } catch {
      this.signOutEverywhereError.set('Could not reach the server. Try again in a moment.');
      return;
    } finally {
      this.signingOutEverywhere.set(false);
    }
    try {
      await this.auth.logout();
    } catch {
      // The cookie is already gone server-side; the session signal is cleared
      // by the navigation's guard on the next route either way.
    }
    await this.router.navigate(['/login']);
  }

  // ---- formatting ---------------------------------------------------------

  /** A door as the person would name it, or the stored value where this build
   *  does not know the door. */
  doorLabel(door: string): string {
    return DOOR_LABEL[door] ?? door;
  }

  /** The browser's own description of itself, or a dash. A missing user agent
   *  is a fact about the request, not a device called "Unknown". */
  deviceOf(userAgent: string | null): string {
    const described = userAgent?.trim();
    return described ? described : '—';
  }

  /** The address the request ARRIVED FROM, which behind a load balancer is the
   *  balancer for every row. Never captioned as the person's own address. */
  seenFrom(ip: string | null): string {
    const address = ip?.trim();
    return address ? address : '—';
  }

  /** `login_events.at` is `DateTime(timezone=True)`, so the offset travels with
   *  it and this renders in the reader's own zone. */
  signedInOn(iso: string): string {
    return this.uploadedOn(iso);
  }

  uploadedOn(iso: string | null): string {
    if (!iso) {
      return '';
    }
    return new Date(iso).toLocaleString('en-IN', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  fileSize(bytes: number | null): string {
    if (!bytes) {
      return '';
    }
    const oneMegabyte = 1024 * 1024;
    if (bytes >= oneMegabyte) {
      return `${(bytes / oneMegabyte).toFixed(1)} MB`;
    }
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  fileKind(mimeType: string | null): string {
    return mimeType === 'image/png' ? 'PNG' : 'JPEG';
  }

  // ---- errors -------------------------------------------------------------

  /** The server's own sentence where there is one — its refusals name the
   *  numbers. FastAPI answers a schema error with `detail` as a LIST, which
   *  rendered raw says "[object Object]". */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as { detail?: unknown };
      const detail = body.detail;
      if (typeof detail === 'string') {
        return detail;
      }
      if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0] as { msg?: unknown };
        if (typeof first.msg === 'string') {
          return first.msg;
        }
      }
    } catch {
      /* not JSON; fall through to the status */
    }
    return `The request was refused (${response.status}).`;
  }

  private messageFrom(err: unknown, fallback: string): string {
    return err instanceof Error ? err.message : fallback;
  }
}
