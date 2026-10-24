/**
 * A PDF drawn into the page, page by page, with pdf.js.
 *
 * WHY NOT AN <iframe> OR <embed>. Every file a student uploads is served as an
 * ATTACHMENT (`document_store.content_disposition` says why): a student's PDF
 * rendered inline by the browser runs its JavaScript in this origin. pdf.js
 * reads the bytes and paints canvases, and the core library never runs a
 * PDF's scripts, so the office can read a CV on screen without the file
 * running anything here.
 *
 * pdf.js is loaded on first use, so it is a lazy chunk and never part of the
 * first paint. It runs on the main thread (`globalThis.pdfjsWorker`): a CV is
 * a page or two, and a separate worker would need its own asset outside the
 * service worker's manifest for no visible gain.
 *
 * A file pdf.js cannot draw, or a browser too old for it, shows a sentence and
 * leaves the Download button beside it as the way to read the file.
 */

import {
  Component,
  ElementRef,
  OnDestroy,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';

/** More pages than this are left to the download: the preview is for reading
 *  a CV at a glance, not for paging through a report. */
const MAX_PAGES = 6;

type PdfJs = typeof import('pdfjs-dist');

let pdfjsReady: Promise<PdfJs> | null = null;

/**
 * `Promise.withResolvers` is the one newer built-in pdf.js 4 calls without a
 * fallback (Chrome 119, Safari 17.4, Firefox 121). It is filled in here, on
 * the lazy path, rather than in polyfills.ts, so the first paint of every
 * other screen does not carry it. Non-enumerable, as `polyfills.ts` installs.
 */
function ensureWithResolvers(): void {
  const ctor = Promise as unknown as { withResolvers?: unknown };
  if (typeof ctor.withResolvers === 'function') return;
  Object.defineProperty(Promise, 'withResolvers', {
    configurable: true,
    writable: true,
    value: function withResolvers<T>() {
      let resolve!: (value: T | PromiseLike<T>) => void;
      let reject!: (reason?: unknown) => void;
      const promise = new Promise<T>((yes, no) => {
        resolve = yes;
        reject = no;
      });
      return { promise, resolve, reject };
    },
  });
}

/** Loads pdf.js once per page load, with its worker on the main thread. */
function loadPdfJs(): Promise<PdfJs> {
  pdfjsReady ??= (async () => {
    ensureWithResolvers();
    const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs');
    (globalThis as { pdfjsWorker?: unknown }).pdfjsWorker = worker;
    return import('pdfjs-dist');
  })();
  return pdfjsReady;
}

@Component({
  selector: 'app-pdf-preview',
  standalone: true,
  template: `
    @if (state() === 'loading') {
      <div class="loading-line" role="status">Opening the file…</div>
    }
    @if (state() === 'failed') {
      <div class="notice" role="status">
        <span class="icon" aria-hidden="true">info</span>
        <span>{{ failure() }}</span>
      </div>
    }
    @if (morePages() > 0) {
      <div class="more">
        {{ morePages() }} more {{ morePages() === 1 ? 'page' : 'pages' }} in the file. Download it to
        read them.
      </div>
    }
    <div class="pages" #pages [attr.aria-label]="label()" role="img"></div>
  `,
  styles: `
    :host {
      display: block;
    }
    .pages {
      display: grid;
      gap: 14px;
      justify-items: center;
    }
    .pages:empty {
      display: none;
    }
    :host ::ng-deep canvas {
      display: block;
      max-width: 100%;
      height: auto;
      background: var(--surface);
      box-shadow: 0 4px 16px rgba(30, 20, 46, 0.18);
    }
    .more {
      font-size: 12px;
      color: var(--muted);
      text-align: center;
      margin-bottom: 8px;
    }
  `,
})
export class PdfPreviewComponent implements OnDestroy {
  /** Where to read the PDF from: a same-origin API path, sent with the cookie. */
  readonly src = input.required<string>();
  /** What the drawing shows, for a screen reader. */
  readonly label = input('PDF preview');

  readonly state = signal<'loading' | 'ready' | 'failed'>('loading');
  readonly failure = signal('');
  readonly morePages = signal(0);

  private readonly pages = viewChild.required<ElementRef<HTMLDivElement>>('pages');
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private run = 0;

  constructor() {
    effect(() => {
      const src = this.src();
      void this.draw(src);
    });
  }

  ngOnDestroy(): void {
    this.run += 1;
  }

  private async draw(src: string): Promise<void> {
    const run = ++this.run;
    const host = this.pages().nativeElement;
    host.replaceChildren();
    this.morePages.set(0);
    this.state.set('loading');
    try {
      const response = await fetch(src, { credentials: 'include' });
      if (!response.ok) {
        this.fail(`The file could not be read (${response.status}).`);
        return;
      }
      const data = new Uint8Array(await response.arrayBuffer());
      const pdfjs = await loadPdfJs();
      // Never evaluate strings from the file (fonts compiled with eval were
      // CVE-2024-4367); the core library does not run a PDF's scripts at all.
      const doc = await pdfjs.getDocument({
        data,
        isEvalSupported: false,
        verbosity: pdfjs.VerbosityLevel.ERRORS,
      }).promise;
      if (run !== this.run) return;
      const shown = Math.min(doc.numPages, MAX_PAGES);
      // The component's own width: the page box is hidden while it is empty.
      const width = Math.max(this.host.nativeElement.clientWidth, 320);
      const ratio = window.devicePixelRatio || 1;
      for (let number = 1; number <= shown; number += 1) {
        const page = await doc.getPage(number);
        const base = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: (Math.min(width, 760) / base.width) * ratio });
        const canvas = document.createElement('canvas');
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${Math.floor(viewport.width / ratio)}px`;
        const context = canvas.getContext('2d');
        if (context === null) {
          this.fail('This browser cannot draw the file. Download it to read it.');
          return;
        }
        await page.render({ canvasContext: context, viewport }).promise;
        if (run !== this.run) return;
        host.appendChild(canvas);
      }
      this.morePages.set(doc.numPages - shown);
      this.state.set('ready');
      await doc.destroy();
    } catch {
      if (run === this.run) {
        this.fail('This file cannot be shown here. Download it to read it.');
      }
    }
  }

  private fail(message: string): void {
    this.failure.set(message);
    this.state.set('failed');
  }
}
