/**
 * The shared kit, ported from src/components/kit.tsx. One component is left.
 *
 * It held five — page intro, section, stat, empty and banner — ported together
 * on the premise that they were one conceptual unit always imported together.
 * They were not. The v2 design system in styles/reep-v2.scss answers the same
 * questions with global classes (`.card`, `.dt-table`, `.chip`, `.dense-*`),
 * every screen built since reaches for those, and four of the five ended the
 * redesign with no importer and no tag in any template. Phase 5 deleted them,
 * and `tone.ts` with them — its TONE_INK map had exactly one reader, the stat.
 *
 * `kit-page-intro` stays because it is genuinely used: features/assistant is
 * built on it. A deleted component that one screen still needs is a broken
 * screen; a kept component nobody imports is a second design system that drifts
 * from the one on screen, silently, until somebody ports a sixth thing to it.
 */

import { Component, Input } from '@angular/core';

/// Title + optional subtitle, with a projected action on the right.
@Component({
  selector: 'kit-page-intro',
  standalone: true,
  template: `
    <div class="intro">
      <div class="intro__text">
        <h1 class="reep-h1 intro__title">{{ title }}</h1>
        @if (subtitle) {
          <p class="reep-body2 intro__subtitle">{{ subtitle }}</p>
        }
      </div>
      <div class="intro__action no-print"><ng-content select="[action]"></ng-content></div>
    </div>
  `,
  styles: [
    `
      .intro {
        display: flex;
        flex-wrap: wrap;
        gap: 16px;
        justify-content: space-between;
        align-items: flex-end;
        margin-bottom: 40px;
      }
      .intro__text {
        min-width: 0;
      }
      .intro__title {
        margin: 0;
        color: var(--reep-text-primary);
      }
      .intro__subtitle {
        margin: 6px 0 0;
        max-width: 68ch;
        color: var(--reep-text-secondary);
      }
    `,
  ],
})
export class PageIntroComponent {
  @Input() title = '';
  @Input() subtitle?: string;
}

