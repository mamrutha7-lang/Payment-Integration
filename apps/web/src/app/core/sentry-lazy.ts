/**
 * The ONLY place `@sentry/angular` is named. main.ts dynamic-imports THIS
 * module, not the package: a dynamic import retains the imported module's whole
 * namespace, and `@sentry/angular` re-exports all of `@sentry/browser`, so
 * `await import('@sentry/angular')` ships rrweb (Session Replay), the feedback
 * widget and the profiler to reach four symbols. Measured 2026-09-08 with
 * `ng build`: 460,113 B raw that way, 151,343 B this way — and the Replay
 * recorder stops shipping to students who will never trigger it.
 *
 * NOTHING may import this file statically — that would put the SDK in the
 * initial chunk, which is the one thing the lazy import exists to prevent.
 * tests/test_codebase_guards.py (apps/api-py) reads this file as text.
 */
export {
  init,
  browserTracingIntegration,
  createErrorHandler,
  TraceService,
} from '@sentry/angular';
