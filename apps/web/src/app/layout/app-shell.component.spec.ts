import { routes } from '../app.routes';
import { HOME_GROUPS, HOME_QUEUES } from '../features/admin/home/home.component';
import { ADMIN_NAVIGATION, GRANTABLE_ADMIN_SCREENS } from './app-shell.component';

/**
 * The console's three lists of destinations — the Main Admin's sidebar, the
 * screens a faculty account can be granted, and the Home's buttons — against
 * the route table they point into.
 *
 * A `routerLink` to a path the router does not declare is the defect the
 * 2026-09-10 browser audit found four times: markup that renders, does
 * nothing, and reads as a broken console. Angular's wildcard sends such a
 * click through homeRedirectGuard back to /admin, so nothing on screen says
 * so. These tests do.
 *
 * The second half pins the Home to the sidebar: every screen the sidebar
 * lists is a button on Home, gated on the same key. A screen added to one and
 * not the other is a screen a novice cannot find, or a button the guard
 * bounces.
 */

/** Every path the SPA declares, as the absolute string a routerLink uses. */
function declaredPaths(): Set<string> {
  const out = new Set<string>();
  const walk = (list: typeof routes, prefix: string): void => {
    for (const route of list) {
      const path = route.path ?? '';
      const full = path ? `${prefix}/${path}` : prefix;
      if (route.loadComponent || route.redirectTo !== undefined) out.add(full || '/');
      if (route.children) walk(route.children, full);
    }
  };
  walk(routes, '');
  return out;
}

const sidebarRows = ADMIN_NAVIGATION.flatMap((group) => group.items);
const homeTiles = HOME_GROUPS.flatMap((group) => group.tiles);

describe('the admin console points only at routes that exist', () => {
  const declared = declaredPaths();

  it('every Main Admin sidebar row', () => {
    for (const row of sidebarRows) {
      if (row.path) expect(declared, row.label).toContain(row.path);
    }
  });

  it('every grantable screen', () => {
    for (const screen of GRANTABLE_ADMIN_SCREENS) {
      expect(declared, screen.label).toContain(screen.path as string);
    }
  });

  it('every Home button and every Home count', () => {
    for (const tile of homeTiles) expect(declared, tile.label).toContain(tile.path);
    for (const queue of HOME_QUEUES) expect(declared, queue.label).toContain(queue.path);
  });
});

describe('Home lists every screen the sidebar lists', () => {
  it('with a button per screen, except Home itself', () => {
    const onHome = new Set(homeTiles.map((tile) => tile.path));
    for (const row of sidebarRows) {
      if (!row.path || row.path === '/admin') continue;
      expect(onHome, `no Home button opens ${row.label} (${row.path})`).toContain(row.path);
    }
  });

  it('gated on the same key as the sidebar row', () => {
    const rowByPath = new Map(sidebarRows.map((row) => [row.path, row]));
    for (const tile of homeTiles) {
      const row = rowByPath.get(tile.path);
      if (!row) continue; // Add faculty is reached from Faculty, not from the sidebar.
      expect(tile.capability, tile.label).toBe(row.capability);
      expect(tile.mainAdminOnly ?? false, tile.label).toBe(row.mainAdminOnly ?? false);
    }
  });

  it('says every label in words, never a key or a code', () => {
    for (const item of [...sidebarRows, ...homeTiles]) {
      expect(item.label, item.label).not.toMatch(/[._]|\bcohort\b|\bcapabilit/i);
    }
  });
});
