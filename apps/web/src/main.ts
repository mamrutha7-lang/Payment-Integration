import { ErrorHandler, inject, provideAppInitializer } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';
import { scrubBreadcrumb, scrubEvent } from './app/core/telemetry-scrub';
import { environment } from './environments/environment';
import { isNativeApp } from './app/native/platform';

/**
 * Sentry is initialized before Angular bootstraps so framework errors are
 * captured through Angular's ErrorHandler, not only uncaught browser errors.
 * The SDK remains a dynamic import when no DSN is configured.
 *
 * THROUGH ./app/core/sentry-lazy, NOT '@sentry/angular' DIRECTLY. A dynamic
 * import retains the imported module's entire namespace, and @sentry/angular is
 * `export * from '@sentry/browser'` — so importing the package here ships
 * rrweb, the feedback widget and the profiler to reach four symbols. Measured
 * 2026-09-08 with ng build: 460,113 B raw / 152,367 B gz that way, 151,343 B /
 * 51,235 B gz through the re-export module. Destructuring in a .then() does NOT
 * fix it (the bundler cannot use a runtime property access). The chunk is lazy
 * either way and never counts against the initial budget, but it IS awaited
 * before bootstrapApplication, so it is a round trip the student waits through.
 *
 * Both the import and init() are guarded: a chunk that fails to load (a stale
 * index.html after a deploy) or an init that throws must cost telemetry, never
 * the app. Before this, a 404 on the SDK chunk rejected bootstrap() and the
 * student got a blank page.
 *
 * Rule 1 applied to telemetry, browser half: sendDefaultPii off, logs and
 * metrics off (they default to ON from 10.71.0 and bypass beforeSend), no
 * Session Replay (see environment.ts for why), and the three hooks from
 * telemetry-scrub as the last stop before anything leaves the machine.
 * tests/test_codebase_guards.py (apps/api-py) reads this file as text.
 */
async function bootstrap(): Promise<void> {
  const sentry = environment.sentryDsn
    ? await import('./app/core/sentry-lazy').catch((err: unknown) => {
        console.error('Sentry SDK failed to load; continuing without telemetry', err);
        return null;
      })
    : null;

  if (sentry) {
    try {
      sentry.init({
        dsn: environment.sentryDsn,
        // Both rewritten by deploy.yml. `environment` must equal the API's ENV
        // (`prod`, not `production`) or one trace splits across two Sentry
        // environments; `release` must equal what sentry-cli uploads the
        // source maps under, or every production frame stays minified.
        environment: environment.sentryEnvironment,
        release: environment.sentryRelease || undefined,
        // Low-cardinality identity on every event. Never a user, a USN or an
        // email: identity is not sent from this app.
        initialScope: { tags: { service: 'reep-web', application: 'reep' } },

        // MUST be @sentry/angular's wrapper, not @sentry/browser's: it forces
        // instrumentNavigation:false and hands navigation spans to TraceService.
        integrations: [sentry.browserTracingIntegration()],

        // ONE pattern, matched against the PATHNAME and only for same-origin
        // requests — which is what CloudFront gives us (the /api/* behaviour
        // and the SPA share one distribution; infra/cdk/reep_core/stack.py).
        // Do NOT add a wildcard-origin regex "for the future": it would attach
        // sentry-trace to any host serving a /api path. The day apiBase
        // becomes an absolute cross-origin URL, add THAT host literally,
        // together with `Access-Control-Allow-Headers: sentry-trace, baggage`
        // on the API's preflight — without it the browser blocks the REQUEST.
        tracePropagationTargets: [/^\/api(?:\/|$)/],

        // Root spans are named with a pathname here (pageload, navigation and
        // TraceService alike), so match paths, never route templates. This
        // decision is what the API inherits for /api/* FETCHES. It does not
        // reach the interview WebSocket — the browser SDK does not instrument
        // WebSocket, so that transaction is sampled by the API's own
        // SENTRY_TRACES_SAMPLE_RATE and must be raised there.
        tracesSampler: (ctx) => {
          if (ctx.name.startsWith('/student/assistant')) return 1.0;
          if (ctx.name.startsWith('/student/interviews')) return 1.0;
          if (ctx.name === '/login') return Math.min(0.05, environment.sentryTracesSampleRate);
          return environment.sentryTracesSampleRate;
        },

        sendDefaultPii: false, // deprecated in 10.71.0; do NOT swap for a bare
        //                        `dataCollection`, whose base defaults are
        //                        permissive the moment the key is present
        enableLogs: false, //    defaults to TRUE from 10.71.0
        enableMetrics: false, // same, one line down in the same resolver

        ignoreErrors: [
          // Native dynamic import(), so these are the BROWSER's words, one per
          // engine: Chrome/Edge, Firefox, Safari. 'ChunkLoadError' and
          // /Loading chunk \S+ failed/ are webpack names and are deliberately
          // absent — @angular/build is esbuild and can never produce them.
          // Filtered so one deploy does not bury a week of genuine issues; the
          // real fix is a reload handler, not silence.
          /Failed to fetch dynamically imported module/,
          /error loading dynamically imported module/,
          /Importing a module script failed/,
          // The LEGACY ResizeObserver message. The modern one is in the SDK's
          // own default list; this one is not.
          'ResizeObserver loop limit exceeded',
        ],
        denyUrls: [
          /extensions\//i,
          /^chrome:\/\//i,
          /^chrome-extension:\/\//i,
          /^moz-extension:\/\//i,
          /^safari-(web-)?extension:\/\//i,
        ],

        // Last stop before anything leaves the machine. The flags above are
        // the defence; these are the backstop for the path nobody has written
        // yet — and for the one that exists today: /reset?token= and
        // /activate?token= are in location.href, which httpContextIntegration
        // (a DEFAULT integration; the array above merges with the defaults)
        // writes into request.url on every event.
        beforeSend: (event) => scrubEvent(event),
        beforeSendTransaction: (event) => scrubEvent(event),
        beforeBreadcrumb: (crumb) => scrubBreadcrumb(crumb),
      });
    } catch (err) {
      console.error('Sentry init failed; continuing without telemetry', err);
    }
  }

  // THE PHONE APP (Ionic + Capacitor). Loaded only inside the app, as its own
  // chunk, so the website downloads none of it. It points the API calls at
  // the server, restores the saved sign-in token and returns Ionic's
  // providers. See src/app/native/native-bootstrap.ts.
  const nativeProviders = isNativeApp()
    ? await import('./app/native/native-bootstrap').then((m) => m.startNativeApp())
    : [];

  const baseConfig = { ...appConfig, providers: [...appConfig.providers, ...nativeProviders] };

  const config = sentry
    ? {
        ...baseConfig,
        providers: [
          ...baseConfig.providers,
          { provide: ErrorHandler, useValue: sentry.createErrorHandler() },
          // Without this there are no navigation spans at all, and the SDK
          // logs nothing: its own warning is gated on a flag that
          // browserTracingIntegration() has already set true. TraceService is
          // providedIn:'root' with Router in its own factory, so forcing
          // construction is the entire fix — no extra provider needed.
          provideAppInitializer(() => {
            inject(sentry.TraceService);
          }),
        ],
      }
    : baseConfig;

  await bootstrapApplication(App, config);
}

/**
 * Reveal the icon font only once it has actually loaded.
 *
 * Material Symbols draws its glyphs from LIGATURES: the markup says
 * `<span class="icon">chevron_left</span>` and the font turns that text into an
 * arrow. When the font does not arrive — a blocked or slow font host, an offline
 * client, a strict CSP — the browser renders the ligature text instead, and every
 * icon in the product becomes the literal word "chevron_left", "school",
 * "task_alt". Buttons stretch, the sidebar wraps, and the UI reads as broken
 * rather than as degraded.
 *
 * `.icon { visibility: hidden }` in reep-v2.scss is the default; this adds
 * `fonts-ready` to <html> only when the font is genuinely available, so a failed
 * load leaves a blank space where a glyph would be. Every icon in this app is
 * decorative (aria-hidden) and sits beside a real text label, so a missing glyph
 * costs nothing while a stray word costs the layout.
 *
 * Three ways out, in order: the font reports itself loaded; the Font Loading API
 * is missing entirely (very old browser — reveal, since we cannot tell); or the
 * check has not resolved within a second and `document.fonts.check` says the face
 * is there anyway. If the font truly never loads, the class is never added.
 */
const ICON_FACE = '24px "Material Symbols Rounded"';

function revealIcons(): void {
    document.documentElement.classList.add('fonts-ready');
}

if (!('fonts' in document)) {
    revealIcons();
} else {
    document.fonts
      .load(ICON_FACE)
      .then((faces) => {
              // The FACE STATUS, not document.fonts.check(). `check()` answers "can I
                  // render this text?", and a browser that has fallen back to a system font
                  // answers yes — which is exactly the broken case, so check() reveals the
                  // ligature words it was added to hide. `load()` resolves with the matched
                  // FontFace objects, and a face that failed to fetch is 'error', never
                  // 'loaded'.
                  if (faces.some((face) => face.status === 'loaded')) revealIcons();
      })
      .catch(() => {
              /* Blocked or failed: leave the icons hidden rather than showing ligature
                 text. Nothing else on the page depends on this resolving. */
      });
}

void bootstrap().catch((err) => console.error(err));
