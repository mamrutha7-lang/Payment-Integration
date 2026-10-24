/**
 * Make every existing API call work inside the phone app, without editing it.
 *
 * The screens call the API two ways: `fetch(`${environment.apiBase}/...`)`
 * (the house pattern) and Angular's HttpClient, which is configured
 * `withFetch()` and so also ends up in `fetch`. Both use the RELATIVE path
 * `/api/...`. On the website that is the same server as the page. In the
 * phone app the page is served from the phone itself (https://localhost), so
 * `/api/...` would ask the phone, not the REEP server.
 *
 * This wraps `window.fetch` once, at startup, inside the app only:
 *   1. a URL starting with `/api/` is pointed at `apiOrigin` (the real server);
 *   2. the saved sign-in token is added as `Authorization: Bearer ...`.
 * Nothing else is touched, so every screen keeps its code exactly as it is.
 *
 * NOT COVERED, and why: a plain link `<a href="/api/...">` or `window.open`
 * is opened by the WebView, not by fetch, so it carries no token. Downloads
 * built that way (some CSV/PDF buttons) do not work in the app yet.
 */

import { tokenStore } from './token-store';

export function installNativeFetch(apiOrigin: string): void {
  const origin = apiOrigin.replace(/\/+$/, '');
  const browserFetch = globalThis.fetch.bind(globalThis);

  const nativeFetch: typeof fetch = (input, init) => {
    const url =
      typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (!isApiPath(url)) return browserFetch(input, init);

    const target = origin + toPath(url);
    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    );
    const token = tokenStore.get();
    if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);

    if (input instanceof Request) {
      return browserFetch(new Request(target, input), { ...init, headers });
    }
    return browserFetch(target, { ...init, headers });
  };

  globalThis.fetch = nativeFetch;
}

/** True for `/api/...`, and for the app's own `https://localhost/api/...`. */
function isApiPath(url: string): boolean {
  return toPath(url).startsWith('/api/');
}

function toPath(url: string): string {
  if (url.startsWith('/')) return url;
  try {
    const parsed = new URL(url);
    // Only the app's OWN origin is rewritten. A URL already pointing at a real
    // server (the API, Sentry, anything else) is left exactly as written.
    if (parsed.origin === globalThis.location?.origin) {
      return parsed.pathname + parsed.search + parsed.hash;
    }
  } catch {
    // Not a URL we understand; leave it alone.
  }
  return url;
}
