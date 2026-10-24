import { ApplicationConfig, isDevMode, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { provideServiceWorker } from '@angular/service-worker';

import { routes } from './app.routes';
import { isNativeApp } from './native/platform';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    // withComponentInputBinding binds a route's `data` (e.g. the placeholder
    // title) straight to a matching component @Input.
    provideRouter(routes, withComponentInputBinding()),
    // The client talks to the FastAPI backend over HTTP; withFetch keeps it on
    // the platform fetch so withCredentials carries the session cookie.
    provideHttpClient(withFetch()),

    /**
     * THE SERVICE WORKER, AND THE ONE THING IT MUST NEVER CACHE.
     *
     * REEP is installed to a student's home screen (public/manifest.webmanifest),
     * and an installed app that cannot open without a signal is a worse promise
     * than a bookmark. The worker caches the APP — index.html, the chunks, the
     * stylesheet, the self-hosted fonts and the icons — so the shell paints on
     * a bad campus connection and so a chunk request cannot 404 halfway through
     * a deploy.
     *
     * `ngsw-config.json` HAS NO `dataGroups`, AND THAT ABSENCE IS THE POINT.
     * A dataGroup is how ngsw caches API responses, and every interesting
     * response on this deployment is rule 1 material: marks, attendance, a USN,
     * an interview transcript. Cached, they would be written to the device's
     * disk by the BROWSER rather than by this app, outliving the httpOnly
     * `reep_session` cookie, surviving sign-out, and surviving the
     * single-device retirement in app/security.py — which exists precisely so
     * that signing in elsewhere ends the old session. They would also be
     * unreachable from `purge_students` and `purge_people`, which can empty a
     * database and cannot touch a Cache Storage entry on a handset in
     * Bengaluru. The file is strict JSON and cannot hold this comment, so it
     * lives here, beside the line that turns the worker on.
     *
     * `navigationUrls` excludes `/api/**` for the neighbouring reason: without
     * it a navigation-shaped request to the API would be answered with
     * index.html out of the cache, and a student would get the app shell where
     * a document download or an error should have been.
     *
     * THE REGISTERED SCRIPT IS `reep-sw.js`, NOT `ngsw-worker.js` (2026-10-01).
     * It is a few lines in `public/` that take every `/api/` request away from
     * ngsw and then `importScripts` ngsw unchanged. ngsw answers a network
     * failure with a 504 it makes up, so a phone that lost its signal during
     * an upload was shown "(504)" for a request no server ever saw; the
     * wrapper's own comment has the evidence. Registering `ngsw-worker.js`
     * here again brings that back on every screen.
     *
     * ENABLED ONLY WHERE A WORKER IS BUILT. `ng build` writes ngsw-worker.js
     * under the production configuration only, so registering the wrapper in
     * dev would import a file that is not there, on every developer's machine.
     * `registerWhenStable:30000` keeps the registration off the critical path —
     * the worker is fetched once the app goes stable, or after 30s if it never
     * does (a long-poll or an open WebSocket can keep it unstable, and this app
     * holds one open for the whole of a mock interview).
     */
    provideServiceWorker('reep-sw.js', {
      // Not inside the phone app: its files are already on the phone, inside
      // the app package, so there is nothing for a worker to cache.
      enabled: !isDevMode() && !isNativeApp(),
      registrationStrategy: 'registerWhenStable:30000',
    }),
  ],
};
