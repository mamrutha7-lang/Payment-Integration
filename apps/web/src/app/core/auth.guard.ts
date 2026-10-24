import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from './auth.service';
import { homeForRole } from './session';
import type { Role } from './session';

export const authGuard: CanActivateFn = async (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  if (auth.isSignedIn()) return true;

  const session = await auth.refresh();
  if (session) return true;

  // `signedOut=elsewhere` is what turns a silent bounce into a sentence. Under
  // one device at a time the student's own second sign-in is the usual cause,
  // and a login screen that says nothing about it looks like a fault.
  // `signedOut=disabled` is the office having disabled the account, which
  // signing in again cannot fix.
  const signedOut = auth.accountDisabled() ? 'disabled' : auth.retiredElsewhere() ? 'elsewhere' : null;
  return router.createUrlTree(['/login'], {
    queryParams: signedOut ? { next: state.url, signedOut } : { next: state.url },
  });
};

/** UI navigation guard only; every API endpoint repeats this decision server-side. */
export const roleGuard = (...allowed: Role[]): CanActivateFn => async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const session = auth.session() ?? (await auth.refresh());
  if (session && allowed.includes(session.role)) return true;
  return router.createUrlTree([session ? homeForRole(session.role) : '/login']);
};

/** Passes when the session HOLDS the capability — which the Main Admin always
 *  does through the role baseline, and a MENTOR does only when the Main Admin
 *  granted it. UI navigation only; the API re-decides every call. */
export const capabilityGuard = (key: string): CanActivateFn => async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const session = auth.session() ?? (await auth.refresh());
  if (session?.capabilities?.includes(key)) return true;
  return router.createUrlTree([session ? homeForRole(session.role) : '/login']);
};

export const homeRedirectGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const session = auth.session() ?? (await auth.refresh());
  return router.createUrlTree([session ? homeForRole(session.role) : '/login']);
};
