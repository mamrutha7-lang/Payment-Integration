/**
 * The floating REEP orb — the one button on every screen that opens the
 * assistant dock (agent-dock.component.ts): the typed REEP Agent and, for a
 * student, the mock interviewer, as two tabs of one panel.
 *
 * Lives in the SHELL, not in a route, because the design puts it on every
 * screen — a route-owned copy would vanish on the login screen's sibling routes
 * and would reset its dragged position on every navigation.
 *
 * DRAG VS TAP IS ONE GESTURE, resolved by distance. `pointerdown` starts a
 * drag; a `pointerup` that has travelled under DRAG_THRESHOLD px is treated as a
 * tap and toggles the dock. Without that threshold the orb is either
 * draggable or clickable but never both: every real tap moves a pointer by a
 * pixel or two, so "moved at all ⇒ drag" makes the button impossible to press on
 * a trackpad, and "pointerup ⇒ tap" makes it impossible to drag without
 * opening the dock.
 *
 * KEYBOARD. Enter and Space on a button fire `click`, never `pointerdown`, so
 * the pointer gesture alone left the orb unreachable without a mouse. A
 * keyboard-invoked click carries `detail === 0` (no pointer press count), and
 * that is what `onClick` toggles on; a pointer tap's own `click` (detail ≥ 1)
 * is ignored because the gesture already handled it.
 *
 * LISTENERS GO ON `document`, NOT ON THE ORB. A pointer that leaves the orb's
 * 58px box mid-drag — which is most of a drag — stops delivering events to it,
 * and the orb sticks to the cursor until the next click. They are removed on
 * pointerup and again in ngOnDestroy.
 *
 * THE ORB SHOWS THE INTERVIEW'S STATE without importing InterviewService: the
 * room mirrors a three-word state and its clock into AgentDockService (see
 * that file for why), so the orb pulses and carries "Live 04:12" while an
 * interview runs, wherever the dock is. Text and colour together — the clock
 * is the words, the pulse is the colour.
 */

import { Component, OnDestroy, computed, inject, signal } from '@angular/core';

import { AgentDockService } from '../core/agent-dock.service';

const DRAG_THRESHOLD = 4;

/** Keeps the orb on screen: it may not be dragged further than this from its
 *  anchored corner, leaving ~110px of it always reachable. */
const EDGE_MARGIN = 110;

@Component({
  selector: 'app-agent-orb',
  standalone: true,
  template: `
    <div class="agent-orb-wrap" [style.transform]="translate()">
      @if (dock.live() && dock.liveClock(); as clock) {
        <span class="agent-orb__live" role="status">
          <span class="agent-orb__live-word">Live</span>
          <span class="agent-orb__live-clock">{{ clock }}</span>
        </span>
      }
      <button
        type="button"
        class="agent-orb"
        [class.agent-orb--open]="dock.open()"
        [class.agent-orb--live]="dock.live()"
        [attr.aria-label]="label()"
        [attr.aria-expanded]="dock.open()"
        aria-haspopup="dialog"
        (pointerdown)="onPointerDown($event)"
        (click)="onClick($event)"
      >
        <span class="icon" aria-hidden="true">{{ glyph() }}</span>
      </button>
    </div>
  `,
})
export class AgentOrbComponent implements OnDestroy {
  readonly dock = inject(AgentDockService);

  /** Drag offset from the anchored corner, in px. Both are <= 0. */
  private readonly bx = signal(0);
  private readonly by = signal(0);
  readonly translate = computed(() => `translate(${this.bx()}px, ${this.by()}px)`);

  readonly label = computed(() =>
    this.dock.open() ? 'Close the REEP assistant' : 'Open the REEP assistant',
  );
  /**
   * All three are in the icon subset (tools/fonts/icon-names.txt).
   *
   * THE RESTING GLYPH IS `auto_awesome`, NOT `smart_toy`. This is the app's
   * own mark for "a model did this" already — it is on Generate Resume, on the
   * resume preview and on English's AI feedback — so the one button that opens
   * the assistant was the only AI surface in the product wearing a different
   * icon, and the one it wore was a cartoon robot. The admin Home's "Ask REEP"
   * tile keeps `smart_toy`: it is a labelled list item, where the icon is a
   * locator rather than the whole affordance. (The sidebars carried the same
   * row until 2026-09-16; this orb is now the one way into the chat from the
   * shell on every role.)
   */
  readonly glyph = computed(() =>
    this.dock.open() ? 'close' : this.dock.live() ? 'graphic_eq' : 'auto_awesome',
  );

  private startX = 0;
  private startY = 0;
  private originX = 0;
  private originY = 0;
  private travelled = 0;
  private dragging = false;

  onPointerDown(event: PointerEvent): void {
    this.dragging = true;
    this.travelled = 0;
    this.startX = event.clientX;
    this.startY = event.clientY;
    this.originX = this.bx();
    this.originY = this.by();
    document.addEventListener('pointermove', this.onMove);
    document.addEventListener('pointerup', this.onUp);
    document.addEventListener('pointercancel', this.onUp);
  }

  /** Keyboard activation only — see the file header. */
  onClick(event: MouseEvent): void {
    if (event.detail === 0) this.toggle();
  }

  private readonly onMove = (event: PointerEvent): void => {
    if (!this.dragging) return;
    const dx = event.clientX - this.startX;
    const dy = event.clientY - this.startY;
    this.travelled = Math.max(this.travelled, Math.hypot(dx, dy));
    // Clamped so the orb can never be dragged off screen and stranded.
    this.bx.set(clamp(this.originX + dx, -(window.innerWidth - EDGE_MARGIN), 0));
    this.by.set(clamp(this.originY + dy, -(window.innerHeight - EDGE_MARGIN), 0));
  };

  private readonly onUp = (): void => {
    if (!this.dragging) return;
    this.dragging = false;
    this.detach();
    if (this.travelled < DRAG_THRESHOLD) this.toggle();
  };

  private detach(): void {
    document.removeEventListener('pointermove', this.onMove);
    document.removeEventListener('pointerup', this.onUp);
    document.removeEventListener('pointercancel', this.onUp);
  }

  /** Open the dock on its last tab, or close it. Closing over a live
   *  interview asks first — the service raises the question and the dock
   *  renders it — rather than hiding outright. */
  toggle(): void {
    if (this.dock.open()) {
      this.dock.requestClose();
      return;
    }
    this.dock.show();
  }

  ngOnDestroy(): void {
    // A component torn down mid-drag (sign-out, route swap) must not leave two
    // document-level handlers bound to a dead instance.
    this.detach();
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
