/**
 * The client half of authentication.
 *
 * The client POSTs credentials to the FastAPI backend (apps/api-py), which sets
 * the http-only `reep_session` cookie and returns the
 * session payload. `withCredentials` is what carries that cookie back on every
 * later request — the cookie is never read by JavaScript, exactly as before.
 *
 * The signed-in session is held in a signal so guards and the shell can react
 * to it without re-fetching.
 */

import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import { environment } from '../../environments/environment';
import { nativeSessionHook } from '../native/session-hook';
import type { LoginChallenge, SessionPayload } from './session';

/// What /auth/login and /auth/login/code answer. `session_token` is filled in
/// only for the phone app (the API decides by the request's Origin); a browser
/// always gets null and keeps its httpOnly cookie. See native/token-store.ts.
type SignedIn = SessionPayload & { session_token?: string | null };

/// Hand the phone app its token, and keep it out of the session signal.
function keepToken(result: SignedIn): SessionPayload {
  const { session_token, ...session } = result;
  if (session_token) nativeSessionHook.onToken?.(session_token);
  return session;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);

  private readonly _session = signal<SessionPayload | null>(null);
  readonly session = this._session.asReadonly();
  readonly isSignedIn = computed(() => this._session() !== null);

  /// POST the credentials; the backend validates against the same scrypt hash
  /// and sets the session cookie. Returns the session so the caller can route
  /// by role — OR a `LoginChallenge` when the server requires an emailed code
  /// as a second step, in which case NO cookie was set and nothing is signed
  /// in until `loginWithCode` succeeds.
  async login(
    email: string,
    password: string,
    next?: string,
  ): Promise<SessionPayload | LoginChallenge> {
    const result = await firstValueFrom(
      this.http.post<SignedIn | LoginChallenge>(
        `${environment.apiBase}/auth/login`,
        { email, password, next },
        { withCredentials: true },
      ),
    );
    if ('otp_required' in result) return result;
    const session = keepToken(result);
    this._session.set(session);
    return session;
  }

  /// The second step: post the six-digit code the server emailed. On success
  /// the backend sets the same session cookie `login` would have.
  async loginWithCode(email: string, code: string): Promise<SessionPayload> {
    const session = keepToken(
      await firstValueFrom(
        this.http.post<SignedIn>(
          `${environment.apiBase}/auth/login/code`,
          { email, code },
          { withCredentials: true },
        ),
      ),
    );
    this._session.set(session);
    return session;
  }

  /**
   * True when the last `refresh()` failed because this session had been
   * RETIRED — signed out by a newer sign-in on another device, or by a logout —
   * rather than merely expiring.
   *
   * One device at a time makes that a routine event: opening REEP on a phone
   * drops the laptop. Bouncing to /login with no explanation reads as an app
   * that signs you out at random, so the guard turns this into a sentence on
   * the login screen. The server is the only party that can tell the two apart
   * (app/security.py::session_was_retired) and says so in a response header.
   */
  readonly retiredElsewhere = signal(false);

  /**
   * True when the last `refresh()` failed because the Main Admin DISABLED this
   * account (`X-Reep-Session: disabled`, app/security.py::session_was_disabled).
   * The guard sends them to a login screen that says so, rather than to a form
   * that will refuse them again with no word of why.
   */
  readonly accountDisabled = signal(false);

  /// Reads the current session from the cookie, or null. Used by the guard on
  /// first load and after a hard refresh. FastAPI exposes this as /auth/me.
  async refresh(): Promise<SessionPayload | null> {
    try {
      const session = await firstValueFrom(
        this.http.get<SessionPayload>(`${environment.apiBase}/auth/me`, {
          withCredentials: true,
        }),
      );
      this._session.set(session);
      this.retiredElsewhere.set(false);
      this.accountDisabled.set(false);
      return session;
    } catch (err) {
      this._session.set(null);
      // A 401 means the phone app's saved token is dead (expired, or retired
      // by a sign-in elsewhere): forget it so the next launch asks to sign in.
      if (err instanceof HttpErrorResponse && err.status === 401) nativeSessionHook.onToken?.(null);
      const why = err instanceof HttpErrorResponse ? err.headers.get('X-Reep-Session') : null;
      this.retiredElsewhere.set(why === 'retired');
      this.accountDisabled.set(why === 'disabled');
      return null;
    }
  }

  async logout(): Promise<void> {
    try {
      await firstValueFrom(
        this.http.post(`${environment.apiBase}/auth/logout`, {}, { withCredentials: true }),
      );
    } finally {
      // The phone app forgets its token even when the request fails (no
      // signal), or the student is left signed in on a shared phone.
      nativeSessionHook.onToken?.(null);
      this._session.set(null);
    }
  }
}
