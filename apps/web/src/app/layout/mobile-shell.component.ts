/**
 * The phone app's frame: the same navigation as the website, drawn with Ionic
 * so it looks and behaves like an Android/iOS app.
 *
 *   ion-menu      the slide-in side menu (every screen this role has)
 *   ion-header    the top bar, with the menu button and the screen's name;
 *                 Ionic pads it by the phone's safe area (status bar, notch)
 *   ion-content   the scrolling area the screen renders into
 *   ion-tab-bar   the bottom tabs: this role's first four screens + "More"
 *
 * USED ONLY INSIDE THE APP. AppShellComponent renders this instead of its own
 * sidebar when `isNativeApp()` is true, inside an `@defer` block, so Ionic is
 * a separate download the website never makes. AppShellComponent still owns
 * WHO is signed in and WHICH screens they get (`navigation()`); this file only
 * draws them. One list of screens, two ways of drawing it — never two lists.
 *
 * Angular's own `<router-outlet>` is kept (not Ionic's `ion-router-outlet`),
 * so every existing route and screen works unchanged; the trade is that pages
 * switch without Ionic's slide animation.
 */

import { Component, computed, inject, input, output } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import {
  IonButtons,
  IonContent,
  IonHeader,
  IonItem,
  IonLabel,
  IonList,
  IonListHeader,
  IonMenu,
  IonMenuButton,
  IonMenuToggle,
  IonTabBar,
  IonTabButton,
  IonTitle,
  IonToolbar,
  MenuController,
} from '@ionic/angular';
import { filter, map } from 'rxjs';

import { AgentDockComponent } from './agent-dock.component';
import { AgentOrbComponent } from './agent-orb.component';
import type { NavigationGroup, NavigationItem } from './app-shell.component';

/** How many screens get a bottom tab; the rest are in the menu ("More"). */
const TAB_COUNT = 4;

@Component({
  selector: 'app-mobile-shell',
  standalone: true,
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    IonButtons,
    IonContent,
    IonHeader,
    IonItem,
    IonLabel,
    IonList,
    IonListHeader,
    IonMenu,
    IonMenuButton,
    IonMenuToggle,
    IonTabBar,
    IonTabButton,
    IonTitle,
    IonToolbar,
    AgentOrbComponent,
    AgentDockComponent,
  ],
  templateUrl: './mobile-shell.component.html',
  styleUrl: './mobile-shell.component.scss',
})
export class MobileShellComponent {
  private readonly router = inject(Router);
  private readonly menu = inject(MenuController);

  readonly navigation = input.required<readonly NavigationGroup[]>();
  readonly consoleName = input('REEP');
  readonly displayName = input('');
  readonly subtitle = input('');
  readonly initials = input('');
  readonly signOut = output<void>();

  /** The current path, without ?query or #hash. */
  private readonly path = toSignal(
    this.router.events.pipe(
      filter((e) => e instanceof NavigationEnd),
      map(() => this.router.url.split(/[?#]/)[0]),
    ),
    { initialValue: this.router.url.split(/[?#]/)[0] },
  );

  /** Every screen that can be opened, in menu order. */
  private readonly screens = computed<NavigationItem[]>(() =>
    this.navigation().flatMap((group) => group.items.filter((item) => item.path)),
  );

  readonly tabs = computed(() => this.screens().slice(0, TAB_COUNT));

  /** The screen we are on: the longest menu path the URL starts with. */
  private readonly current = computed<NavigationItem | null>(() => {
    const path = this.path();
    let best: NavigationItem | null = null;
    for (const item of this.screens()) {
      const p = item.path!;
      const hit = item.exact ? path === p : path === p || path.startsWith(p + '/');
      if (hit && (!best || p.length > best.path!.length)) best = item;
    }
    return best;
  });

  readonly pageTitle = computed(() => this.current()?.label ?? this.consoleName());
  readonly activeTab = computed(() => {
    const here = this.current()?.path;
    return this.tabs().some((t) => t.path === here) ? here! : 'more';
  });

  open(path: string): void {
    void this.router.navigateByUrl(path);
  }

  openMenu(): void {
    void this.menu.open('reep-menu');
  }

  async onSignOut(): Promise<void> {
    await this.menu.close('reep-menu');
    this.signOut.emit();
  }
}
