import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { App } from './app';

/**
 * The root component is a shell: its template is `<router-outlet />` and nothing
 * else. So this asserts that it mounts and provides the outlet — which is all it
 * is responsible for. Screen behaviour belongs to the feature components.
 *
 * This file previously carried the `ng new` scaffold, which asserted an `<h1>`
 * containing "Hello, web" against a template that has never had one. `ng test`
 * therefore failed on a fresh checkout, which is worse than having no test: a
 * suite that is always red is a suite nobody reads, and a real regression would
 * have arrived as one more failure in an already-failing run.
 */
describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      // App imports RouterOutlet, which needs a router to activate against.
      providers: [provideRouter([])],
    }).compileComponents();
  });

  it('creates the root component', () => {
    const fixture = TestBed.createComponent(App);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('renders a router outlet for the lazy feature routes', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();

    // Angular replaces <router-outlet> with a comment anchor once it is
    // instantiated, so query the directive rather than the tag name.
    const outlet = fixture.debugElement.nativeElement as HTMLElement;
    expect(outlet.innerHTML).toContain('router-outlet');
  });
});
