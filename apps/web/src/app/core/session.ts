/**
 * The session shape, shared by contract with the FastAPI backend
 * (apps/api-py/app/routers/auth.py `_payload_for`): the claims the
 * `reep_session` cookie carries, exactly as the UI expects them.
 */

/// The roles this client knows how to be. DIRECTOR IS DELIBERATELY ABSENT
/// (2026-09-10): it was a second office account under another name and the
/// server now grants it nothing — no role gate, no capability baseline. The
/// server can still MINT one, though, from a row an un-migrated checkout
/// carries, so the client has to have an answer for a session it does not
/// recognise; `homeForRole` below is that answer, and leaving DIRECTOR out of
/// this union is what makes the compiler find every place that needs one.
export type Role = 'STUDENT' | 'MENTOR' | 'ADMIN' | 'ALUMNI';

export interface SessionPayload {
  userId: string;
  email: string;
  name: string;
  role: Role;
  /// Present for STUDENT, absent otherwise.
  studentId?: string;
  mentorId?: string;
  tokenVersion?: number;
  /// Resolved live by the API on every /login and /me — the role baseline
  /// plus any grants. Drives the nav and the route guards; every endpoint
  /// re-decides for itself.
  capabilities?: string[];
}

/// What `POST /api/auth/login` answers INSTEAD of a session when the server
/// requires a second step (`LoginChallenge` in app/schemas/auth.py): the
/// password was right, a six-digit code has been emailed, and no cookie was
/// set. `otp_required` is the discriminator the client branches on.
export interface LoginChallenge {
  otp_required: true;
  email: string;
  expires_in_minutes: number;
}

/// Where each role lands after signing in — the port of HOME_FOR_ROLE.
export const HOME_FOR_ROLE: Record<Role, string> = {
  STUDENT: '/student',
  MENTOR: '/mentor',
  ADMIN: '/admin',
  ALUMNI: '/alumni',
};

/**
 * The home for a role the server sent us, WHATEVER it sent.
 *
 * `session.role` is typed `Role`, but it is really whatever string the cookie
 * carries, and a retired role is exactly the case where those two disagree. A
 * bare `HOME_FOR_ROLE[role]` answers `undefined` there, and `createUrlTree([
 * undefined])` is a crash on the redirect that was meant to be the safe path.
 *
 * An unknown role gets `/login`, which is the truthful destination: the server
 * grants it no screen and no capability, so there is no workspace to send it
 * to. Sending it to `/admin` instead — which is what DIRECTOR used to do — put
 * a whole console in front of an account that would be refused by every request
 * it made.
 */
export function homeForRole(role: string | null | undefined): string {
  return HOME_FOR_ROLE[role as Role] ?? '/login';
}
