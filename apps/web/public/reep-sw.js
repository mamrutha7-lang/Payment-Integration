/*
 * REEP's service worker: Angular's, with the API taken out of its hands.
 *
 * THE API MUST NEVER PASS THROUGH ngsw, AND THIS FILE IS THE ONLY THING THAT
 * SAYS SO (2026-10-01). ngsw intercepts every request the page makes, POSTs
 * included, and when the network fetch throws — a phone losing its signal
 * half-way through an upload — its `safeFetch` swallows the real error and
 * answers with a response it MADE UP: `504 Gateway Timeout`, empty body. A
 * student on /register was told "(504)" for a request that never left the
 * handset: CloudFront logged no 5xx, the ALB none, the API no request at all,
 * so nothing on the server side could ever have seen it. Reproduced in
 * Chromium against the exact ngsw-worker.js the app ships: cut the upload and
 * the page gets 504 under ngsw, `TypeError: Failed to fetch` without it, and
 * `TypeError` again under this wrapper.
 *
 * ngsw never served the API anyway — ngsw-config.json has no `dataGroups`
 * (rule 1: no student record may be cached on a handset) and `navigationUrls`
 * excludes `/api/**` — so taking `/api/` away from it costs nothing it did.
 * The `ngsw-bypass` header does the same per request, but it has to be on
 * every fetch in the app, and the one somebody forgets brings this back.
 *
 * THE ORDER IS THE MECHANISM. Listeners on the worker's global run in the
 * order they were added, so this one must be registered BEFORE
 * `importScripts`, which runs ngsw's constructor and adds its listener.
 * `stopImmediatePropagation()` keeps ngsw's listener from running, nobody
 * calls `respondWith`, and the browser performs the request exactly as if no
 * worker were installed: a real answer, or a real network error the page can
 * describe honestly.
 *
 * A FIXED NAME, SO IT IS DEPLOYED LIKE ngsw-worker.js: no-cache and in the
 * CloudFront invalidation (.github/workflows/deploy.yml), and a change to it
 * always needs a human (tools/ci/release_gate.py), because a broken worker
 * freezes every installed phone. It matches no asset group in
 * ngsw-config.json, so ngsw never caches it and check_ngsw_integrity.py never
 * hashes it — and it must stay that way.
 */
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.origin === self.location.origin && url.pathname.startsWith('/api/')) {
    event.stopImmediatePropagation();
  }
});

importScripts('./ngsw-worker.js');
