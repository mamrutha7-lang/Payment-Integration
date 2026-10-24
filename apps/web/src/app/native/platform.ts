/**
 * Is this bundle running inside the phone app (Ionic + Capacitor)?
 *
 * The Android and iOS apps load the SAME built files as the website, so the
 * code has to ask at runtime. Capacitor's native side puts `window.Capacitor`
 * on the page before any of our scripts run, and `isNativePlatform()` is true
 * only inside the app — in a desktop or phone BROWSER it is absent or false.
 *
 * Read off `window` rather than imported from @capacitor/core on purpose:
 * this file is imported by main.ts and app.config.ts, which are in the
 * website's first download, and the website must not pay for the app.
 */

interface CapacitorGlobal {
  isNativePlatform?: () => boolean;
  getPlatform?: () => string;
}

function capacitor(): CapacitorGlobal | undefined {
  return (globalThis as { Capacitor?: CapacitorGlobal }).Capacitor;
}

export function isNativeApp(): boolean {
  try {
    return capacitor()?.isNativePlatform?.() === true;
  } catch {
    return false;
  }
}

/** 'android' | 'ios' inside the app, 'web' everywhere else. */
export function nativePlatform(): string {
  return isNativeApp() ? (capacitor()?.getPlatform?.() ?? 'web') : 'web';
}
