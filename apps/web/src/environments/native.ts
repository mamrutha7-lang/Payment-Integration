/**
 * Settings for the PHONE APP only (Android/iOS, built with Capacitor). The
 * website ignores this file.
 *
 * `apiOrigin` is the REEP server the app talks to — the same address you open
 * the website on, WITHOUT a trailing slash and WITHOUT `/api`. For example
 * 'https://reep.example.edu'. The app adds `/api/...` itself.
 *
 * It must be https:// for a real phone build: the app runs on
 * https://localhost, and Android refuses to let an https page call plain
 * http:// (that is called "mixed content" and is blocked for safety).
 *
 * The server must also list the app in its NATIVE_APP_ORIGINS setting
 * (apps/api-py/.env, or the API task's environment in production):
 *     NATIVE_APP_ORIGINS=https://localhost,capacitor://localhost
 * Without it every request from the app is refused by the browser engine.
 */
export const native = {
  apiOrigin: '',
};
