/**
 * Last-line PII scrubbing for the browser SDK — the twin of
 * apps/api-py/app/telemetry_scrub.py, with the same query allowlist.
 *
 * `sendDefaultPii: false` governs IP and user identity, NOT the URL. The
 * browser SDK's httpContextIntegration is a DEFAULT integration — and the
 * explicit `integrations:` array in main.ts MERGES with the defaults rather
 * than replacing them — so it sets `request.url` from `location.href` on
 * every event, and the navigation breadcrumb's `from`/`to` are path AND query.
 * /activate?token=… and /reset?token=… are Angular routes: without this, a
 * live, single-use account token ships on the first error a student hits on
 * that screen.
 *
 * No `@sentry/*` runtime import: this file is in the INITIAL chunk (main.ts
 * references it statically, because the hooks must exist before init), and
 * the SDK must stay in its lazy chunk. Types only, which the compiler erases.
 */

/** The SDK's own substitute, spelled the same so a filtered value reads the
 *  same whichever layer filtered it. */
export const FILTERED = '[Filtered]';

/**
 * An ALLOWLIST, not a denylist: a denylist has to be right about the parameter
 * the NEXT feature adds. These are the ones this app actually sends — each an
 * enumeration or a small integer, never free text. Keep in step with
 * QUERY_ALLOW in apps/api-py/app/telemetry_scrub.py.
 *
 * DELIBERATELY ABSENT: `token`, `code`, `state` (credentials), `q` (free text
 * a person typed), `next` (auth.guard.ts sets it to the WHOLE bounced url).
 */
export const QUERY_ALLOW: ReadonlySet<string> = new Set([
  'board',
  'day',
  'days',
  'degree',
  'download',
  'error',
  'include_inactive',
  'limit',
  'mode',
  'specialization',
  'status',
  'track',
  'verified',
  'why',
]);

const SENSITIVE_HEADERS: ReadonlySet<string> = new Set([
  'cookie',
  'set-cookie',
  'authorization',
  'proxy-authorization',
  'x-api-key',
]);

// The three identifier shapes this deployment carries, mirrored from
// apps/api-py/app/redaction.py: an email (which IS the USN here), a phone
// number, and a 10-character VTU USN holding both a letter and a digit.
const EMAIL = /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g;
const PHONE = /(?<!\w)(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,12}\d(?!\w)/g;
const USN = /(?<![A-Za-z0-9])(?=[A-Za-z0-9]{10}(?![A-Za-z0-9]))[A-Za-z0-9]{10}/g;
const REDACTED = '[redacted]';

/** Emails, phone numbers and USNs replaced; everything else untouched. */
export function redactPii(text: string): string {
  return text
    .replace(EMAIL, REDACTED)
    .replace(USN, (token) => (/[A-Za-z]/.test(token) && /\d/.test(token) ? REDACTED : token))
    .replace(PHONE, REDACTED);
}

/** Keep the screen selectors, blank everything else — the key stays visible
 *  so a trace can still say `token=[Filtered]` rather than nothing. */
export function filterQuery(qs: string): string {
  if (!qs) return qs;
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(qs.startsWith('?') ? qs.slice(1) : qs);
  } catch {
    return FILTERED;
  }
  const out = new URLSearchParams();
  params.forEach((value, key) => out.append(key, QUERY_ALLOW.has(key) ? value : FILTERED));
  return out.toString();
}

/** The query string filtered, the fragment dropped. Angular routes by path,
 *  so nothing is lost — and a fragment is one more place a link could carry
 *  a secret. A relative URL is fine; nothing here needs an origin. */
export function filterUrl(url: string): string {
  if (!url) return url;
  const hashAt = url.indexOf('#');
  const withoutFragment = hashAt >= 0 ? url.slice(0, hashAt) : url;
  const queryAt = withoutFragment.indexOf('?');
  if (queryAt < 0) return withoutFragment;
  const filtered = filterQuery(withoutFragment.slice(queryAt + 1));
  return filtered ? `${withoutFragment.slice(0, queryAt)}?${filtered}` : withoutFragment.slice(0, queryAt);
}

/** The subset of a Sentry event this file touches. Structural on purpose so
 *  the SDK's own `ErrorEvent` and `TransactionEvent` both satisfy it. */
export interface ScrubbableEvent {
  request?: {
    url?: string;
    query_string?: unknown;
    headers?: Record<string, string>;
    cookies?: unknown;
    data?: unknown;
  };
  breadcrumbs?: ScrubbableBreadcrumb[];
  user?: unknown;
  message?: string;
  exception?: { values?: Array<{ value?: string }> };
}

export interface ScrubbableBreadcrumb {
  category?: string;
  message?: string;
  data?: Record<string, unknown>;
}

/**
 * beforeSend AND beforeSendTransaction. Both, because request data rides a
 * sampled transaction too — a token does not need an error to leak, it needs
 * a dice roll on a successful navigation.
 */
export function scrubEvent<T extends ScrubbableEvent>(event: T): T {
  const request = event.request;
  if (request) {
    if (typeof request.url === 'string') request.url = filterUrl(request.url);
    if (typeof request.query_string === 'string') {
      request.query_string = filterQuery(request.query_string);
    } else if (request.query_string !== undefined) {
      request.query_string = FILTERED;
    }
    delete request.cookies;
    delete request.data;
    const headers = request.headers;
    if (headers) {
      for (const name of Object.keys(headers)) {
        if (SENSITIVE_HEADERS.has(name.toLowerCase())) headers[name] = FILTERED;
      }
    }
  }
  if (typeof event.message === 'string') event.message = redactPii(event.message);
  for (const entry of event.exception?.values ?? []) {
    if (typeof entry.value === 'string') entry.value = redactPii(entry.value);
  }
  if (event.breadcrumbs) {
    event.breadcrumbs = event.breadcrumbs
      .map((crumb) => scrubBreadcrumb(crumb))
      .filter((crumb): crumb is ScrubbableBreadcrumb => crumb !== null);
  }
  // Never set by this app (sendDefaultPii: false keeps the SDK from setting
  // it either). If a future call site does, it goes.
  delete event.user;
  return event;
}

/**
 * beforeBreadcrumb. The fetch/xhr breadcrumb records the full request URL, the
 * navigation breadcrumb records `from`/`to` with their query strings, and the
 * console breadcrumb records whatever a developer logged — which on a screen
 * that renders a student's record is that record.
 */
export function scrubBreadcrumb<T extends ScrubbableBreadcrumb>(crumb: T): T | null {
  const data = crumb.data;
  if (data) {
    for (const key of ['url', 'from', 'to']) {
      const value = data[key];
      if (typeof value === 'string') data[key] = filterUrl(value);
    }
    if (crumb.category === 'console') {
      // The logged arguments, verbatim objects included. The message keeps
      // the first line, redacted; the arguments do not travel.
      delete data['arguments'];
    }
  }
  if (typeof crumb.message === 'string') crumb.message = redactPii(crumb.message);
  return crumb;
}
