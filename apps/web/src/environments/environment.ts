/**
 * Runtime configuration. `apiBase` is where the FastAPI backend answers — the
 * Python backend (apps/api-py) that took over Prisma, auth and the domain logic,
 * so the Angular client is purely the UI calling it over HTTP.
 *
 * The four `sentry*` fields are rewritten at build time by deploy.yml's "Point
 * the SPA at Sentry" step (a sed per field, each proved by a grep). There is
 * no `production` flag any more: its only reader was the Sentry environment
 * tag, it was hard-coded false, and every production browser event was filed
 * under `development` while the API filed under `prod`.
 */
export const environment = {
  /// The FastAPI API runs on 3300 (uvicorn). The dev proxy (proxy.conf.json)
  /// forwards /api -> http://localhost:3300 so this stays same-origin in the
  /// browser and the http-only session cookie is carried without CORS friction.
  apiBase: '/api',
  /// Sentry — the single observability + traceability tool, the reep-web
  /// project's key (never the API's). Blank = the SDK is never even DOWNLOADED
  /// (main.ts dynamic-imports it only when this is set), so dev builds and the
  /// initial bundle pay nothing. Set per deployment at build time from the
  /// WEB_SENTRY_DSN repository secret.
  sentryDsn: '',
  /// The commit this bundle was built from. Must equal the string sentry-cli
  /// uploads the source maps under and the API's SENTRY_RELEASE, or "first
  /// seen in this release" cannot be read across both halves of one trace.
  sentryRelease: '',
  /// Must equal the API's ENV — `prod` in production, four characters, not
  /// `production` — or the two halves of one distributed trace land in two
  /// Sentry environments and every production-scoped rule misses the web.
  sentryEnvironment: 'development',
  /// The base rate for the browser's tracesSampler (0.0-1.0). The interview
  /// screens are always traced and /login rarely, whatever this says; see
  /// main.ts. The API inherits this decision for every /api/* fetch.
  sentryTracesSampleRate: 0.1,
  /// Session Replay is DELIBERATELY not configured and not constructed: it
  /// reconstructs the DOM, nineteen routes render marks, a USN, a resume, an
  /// interview transcript or an admin console, it cannot be blocked per route
  /// in this SDK, and the students' consent covers the college's server, not
  /// Sentry's. There is no replaysSessionSampleRate here on purpose — not
  /// constructing the integration is the control, a rate of 0 is theatre.
};
