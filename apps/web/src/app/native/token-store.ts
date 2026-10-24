/**
 * Where the phone app keeps its sign-in token: Capacitor Preferences.
 *
 * WHY A TOKEN AT ALL. On the website the server keeps the session in an
 * httpOnly cookie that JavaScript never sees. The phone app is served from
 * https://localhost (Android) or capacitor://localhost (iOS), so to the API it
 * is a different site and that cookie never reaches it. Instead, when the app
 * signs in, the API returns the same token in the response body (only to the
 * origins listed in the API's NATIVE_APP_ORIGINS) and the app sends it back as
 * `Authorization: Bearer <token>` — see native-fetch.ts.
 *
 * WHY PREFERENCES AND NOT localStorage. Android and iOS are both allowed to
 * clear a WebView's localStorage when the phone is low on space, which would
 * sign the student out at random. Preferences writes to the platform's own
 * app storage (SharedPreferences on Android, UserDefaults on iOS), which only
 * goes away when the app is uninstalled or its data is cleared.
 *
 * The value is also held in memory (`current`) because every request reads
 * it and Preferences is asynchronous. `load()` runs once before the app starts.
 */

import { Preferences } from '@capacitor/preferences';

const KEY = 'reep.session_token';

let current: string | null = null;

export const tokenStore = {
  /** The token in memory, or null. Synchronous: safe inside fetch. */
  get(): string | null {
    return current;
  },

  /** Read the saved token from the phone's storage into memory. */
  async load(): Promise<void> {
    try {
      const { value } = await Preferences.get({ key: KEY });
      current = value || null;
    } catch {
      current = null;
    }
  },

  async save(token: string): Promise<void> {
    current = token;
    try {
      await Preferences.set({ key: KEY, value: token });
    } catch {
      // Still signed in for this run; the next launch asks again.
    }
  },

  async clear(): Promise<void> {
    current = null;
    try {
      await Preferences.remove({ key: KEY });
    } catch {
      // Nothing to do: memory is already clear.
    }
  },
};
