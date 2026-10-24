/**
 * Everything the phone app needs before Angular starts, and nothing the
 * website needs at all. main.ts loads this file ONLY inside the app (a
 * dynamic import), so none of it is in the website's download.
 *
 * In order:
 *   1. mark <html> so CSS can tell it is the app (`native-app`);
 *   2. let the page draw under the status bar and notch (`viewport-fit=cover`)
 *      — Ionic's header then pads itself by the phone's safe area;
 *   3. load Ionic's stylesheet (built separately, see angular.json `ionic`);
 *   4. read the saved sign-in token, and point every `/api/` call at the
 *      server with that token attached (native-fetch.ts);
 *   5. return the Angular providers: Ionic itself, plus a start-up step that
 *      wires the Android back button, the status bar and the splash screen.
 */

import { EnvironmentProviders, Provider, inject, provideAppInitializer } from '@angular/core';
import { NavigationEnd, Router } from '@angular/router';
import { MenuController, provideIonicAngular } from '@ionic/angular';
import { App } from '@capacitor/app';
import { SplashScreen } from '@capacitor/splash-screen';
import { StatusBar, Style } from '@capacitor/status-bar';
import { filter, take } from 'rxjs';

import { native } from '../../environments/native';
import { HOME_FOR_ROLE } from '../core/session';
import { installNativeFetch } from './native-fetch';
import { nativePlatform } from './platform';
import { nativeSessionHook } from './session-hook';
import { tokenStore } from './token-store';

/** Screens where Back should leave the app rather than go back a page. */
const ROOT_PATHS = new Set<string>(['/', '/login', ...Object.values(HOME_FOR_ROLE)]);

export async function startNativeApp(): Promise<(Provider | EnvironmentProviders)[]> {
  // If anything below fails, the splash must not cover the screen forever.
  const splashFallback = setTimeout(() => void SplashScreen.hide().catch(() => {}), 6000);

  const html = document.documentElement;
  html.classList.add('native-app', `native-${nativePlatform()}`);
  coverViewport();
  loadIonicStyles();

  if (!native.apiOrigin) {
    console.error(
      'REEP app: src/environments/native.ts has no apiOrigin, so the app does not know which ' +
        'server to talk to. Set it and rebuild.',
    );
  }

  await tokenStore.load();
  installNativeFetch(native.apiOrigin);
  nativeSessionHook.onToken = (token) => void (token ? tokenStore.save(token) : tokenStore.clear());

  return [
    // `hardwareBackButton: false` because the back button is handled below:
    // this app keeps Angular's own router-outlet rather than Ionic's, so
    // Ionic's built-in handler would have no page stack to go back through.
    provideIonicAngular({ mode: 'md', hardwareBackButton: false }),
    provideAppInitializer(() => {
      const router = inject(Router);
      const menu = inject(MenuController);
      wireBackButton(router, menu);
      void styleStatusBar();
      router.events
        .pipe(
          filter((e) => e instanceof NavigationEnd),
          take(1),
        )
        .subscribe(() => {
          clearTimeout(splashFallback);
          void SplashScreen.hide().catch(() => {});
        });
    }),
  ];
}

/**
 * Android's back button (and back gesture). Without this, pressing Back on any
 * screen closes the whole app, because a WebView has no back button of its own.
 *   - menu open        -> close the menu
 *   - on a home screen -> put the app in the background (like other apps)
 *   - anywhere else    -> go back one screen
 */
function wireBackButton(router: Router, menu: MenuController): void {
  void App.addListener('backButton', async ({ canGoBack }) => {
    if (await menu.isOpen()) {
      await menu.close();
      return;
    }
    const path = router.url.split(/[?#]/)[0] || '/';
    if (!canGoBack || ROOT_PATHS.has(path)) {
      await App.minimizeApp();
      return;
    }
    history.back();
  });
}

/** Dark clock and battery icons on REEP's light lilac header. */
async function styleStatusBar(): Promise<void> {
  try {
    await StatusBar.setStyle({ style: Style.Light });
  } catch {
    // Not available on this phone (or on the web preview). Cosmetic only.
  }
}

function coverViewport(): void {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]');
  if (meta && !meta.content.includes('viewport-fit')) {
    meta.content = `${meta.content}, viewport-fit=cover`;
  }
}

/**
 * Ionic's CSS is built as its own file (angular.json, `"inject": false`) and
 * added here, so the website never loads it. Its base rules would otherwise
 * change the website: Ionic fixes <body> in place for app-style scrolling.
 */
function loadIonicStyles(): void {
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = 'ionic.css';
  document.head.appendChild(link);
}
