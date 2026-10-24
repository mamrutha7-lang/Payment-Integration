/**
 * Capacitor: the tool that wraps this Angular website into an Android (and
 * iOS) app. Docs: https://capacitorjs.com/docs/config
 *
 * How it works, in one paragraph: `npm run build` makes the website into
 * plain files in dist/web/browser. `npx cap sync` copies those files INTO the
 * android/ project (and ios/ once you add it). The app is a phone-native
 * window (a "WebView") that shows those files from the phone itself, at
 * https://localhost, and talks to the REEP server over the internet — the
 * server address is src/environments/native.ts.
 */
import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  // The app's permanent ID on the Play Store / App Store. It can never be
  // changed after the first upload to a store, so change it BEFORE that if
  // the college wants a different one.
  appId: 'in.ac.bgscet.reep',
  appName: 'REEP',
  // Where `ng build` puts the website (angular.json, "@angular/build:application").
  webDir: 'dist/web/browser',
  android: {
    // Keep the address bar scheme https (the default), so the app's origin
    // is https://localhost — the value the API's NATIVE_APP_ORIGINS lists.
    allowMixedContent: false,
  },
  plugins: {
    SplashScreen: {
      // Shown while the app starts; hidden by native-bootstrap.ts as soon as
      // the first screen is ready (with a 6-second safety timeout).
      launchAutoHide: false,
      backgroundColor: '#ece4f5',
      showSpinner: false,
      androidScaleType: 'CENTER_CROP',
    },
    StatusBar: {
      // Dark clock/battery icons on REEP's light header.
      style: 'LIGHT',
    },
    SystemBars: {
      // Draw under the status bar and the gesture bar, and give the page the
      // phone's safe-area sizes so Ionic's header and tab bar pad themselves.
      insetsHandling: 'css',
      initialViewportFitValueHint: 'cover',
    },
    Keyboard: {
      // When the keyboard opens, shrink the page so the box being typed in
      // stays visible instead of being hidden behind the keyboard.
      resizeOnFullScreen: true,
    },
  },
};

export default config;
