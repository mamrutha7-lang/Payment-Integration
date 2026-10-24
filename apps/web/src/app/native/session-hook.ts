/**
 * A two-line bridge between AuthService and the phone app's token storage.
 *
 * AuthService is part of the website's first download, and the token storage
 * imports Capacitor. Importing it straight into AuthService would make every
 * browser download Capacitor for nothing. Instead AuthService calls these
 * hooks, which do nothing on the website; native-bootstrap.ts fills them in
 * when (and only when) the code is running inside the app.
 */
export const nativeSessionHook = {
  /** Called with the token after a sign-in, and with null on sign-out. */
  onToken: null as ((token: string | null) => void) | null,
};
