/**
 * A control the board draws but the backend cannot yet answer.
 *
 * The admin boards show the whole console, including buttons whose endpoint is
 * a Phase 3 or Phase 4 task in 04-backend-changes.md — Promote batch, Grant
 * function with a scope target, Disable faculty, Sign out everywhere, the
 * import wizard's "Import 52 rows". Phase 2 builds those screens on the
 * endpoints that exist today, which leaves each of those controls with three
 * possible renderings and only one of them honest:
 *
 *   - Leave it out. The screen then stops matching the board it is reviewed
 *     against, and the reviewer cannot tell a missing control from a missed
 *     one.
 *   - Draw it live. It looks like every other button and does nothing, or
 *     worse, posts to an endpoint that answers 404. That is the "dead control
 *     that looks live" 06-phase-prompts.md forbids.
 *   - Draw it disabled, saying when it arrives. The board is recognisable, the
 *     control cannot be pressed, and the answer to "why is this grey" is on
 *     the control itself.
 *
 * This directive is the third. `[reepPending]="3"` disables the host and gives
 * it the sentence the sidebar already uses for a screen that has not been
 * built: "Available with Phase 3".
 *
 *     <button class="btn secondary" [reepPending]="3">Promote to semester 4</button>
 *
 * NEVER WITH FAKE DATA BESIDE IT. A disabled button is honest; a disabled
 * button over a table of invented rows is not. Where the board's data itself
 * comes from an unmerged task, the screen renders the empty state and a
 * `.notice.accent` saying what will fill it — not a plausible-looking sample.
 *
 * ACCESSIBILITY. On a `<button>` or `<input>` the native `disabled` property is
 * set, which is what takes it out of the tab order and stops the click. On any
 * other host — a link, a row, a whole panel — `disabled` does not exist, so the
 * directive uses `aria-disabled`, removes it from the tab order and blocks
 * pointer events, and `.btn[aria-disabled='true']` in reep-v2.scss already
 * carries the same 0.55 opacity as `:disabled`.
 *
 * The reason reaches a screen reader as the accessible DESCRIPTION, through
 * `title`, and the host's own name is left alone. Composing it into
 * `aria-label` instead was the first attempt and it is wrong twice: a control
 * that reads its name from its text content would have to be read back out of
 * the DOM, which is empty at construction and interpolated later, so the name
 * would be lost for exactly the buttons that have one; and overwriting a name
 * to carry a reason is what `title` is for. Where the host DOES declare an
 * `aria-label`, that string is ours to extend and the phase is appended to it.
 */

import { Directive, ElementRef, Renderer2, effect, inject, input } from '@angular/core';

/** The sentence on the control and in its accessible name. */
export function pendingLabel(phase: number | string): string {
  return `Available with Phase ${phase}`;
}

@Directive({
  selector: '[reepPending]',
  standalone: true,
})
export class PendingControlDirective {
  /** The phase that makes this control work — 3 or 4 in this release. */
  readonly reepPending = input.required<number | string>();

  private readonly host = (inject(ElementRef) as ElementRef<HTMLElement>).nativeElement;
  private readonly renderer = inject(Renderer2);

  /** An aria-label the template declared, before the phase was appended to it.
   *  Read at construction, which is when the attribute exists: unlike text
   *  content it is on the element itself and not a child node rendered later. */
  private readonly declaredLabel = this.host.getAttribute('aria-label');

  constructor() {
    effect(() => {
      const note = pendingLabel(this.reepPending());
      const takesDisabled = ['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA'].includes(this.host.tagName);

      if (takesDisabled) {
        this.renderer.setProperty(this.host, 'disabled', true);
      } else {
        this.renderer.setAttribute(this.host, 'aria-disabled', 'true');
        this.renderer.setAttribute(this.host, 'tabindex', '-1');
        this.renderer.setStyle(this.host, 'pointer-events', 'none');
      }

      // The accessible DESCRIPTION. A control that takes its name from its own
      // text keeps that name; one that declared an aria-label gets the phase
      // appended, because there is no text for the description to sit beside.
      this.renderer.setAttribute(this.host, 'title', note);
      if (this.declaredLabel) {
        this.renderer.setAttribute(this.host, 'aria-label', `${this.declaredLabel} — ${note}`);
      }
    });
  }
}
