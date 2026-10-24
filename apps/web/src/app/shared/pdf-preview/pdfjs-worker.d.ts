/**
 * pdf.js ships no types for its worker module. The preview imports it only to
 * hand `WorkerMessageHandler` to pdf.js on the main thread (see
 * pdf-preview.component.ts), so this is the whole of what it needs.
 */
declare module 'pdfjs-dist/build/pdf.worker.min.mjs' {
  export const WorkerMessageHandler: unknown;
}
