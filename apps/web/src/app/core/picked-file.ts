/**
 * A file the student picked, copied into memory the moment it is picked.
 *
 * WHY (2026-10-14): students pressed "Submit claim" on Skilling and were told
 * "Could not reach the server." while the API and CloudFront recorded the same
 * upload working for everybody else that day. The edge log held the trace: a
 * POST to /api/student/uploads from an Android phone that sent its headers
 * (136 bytes on one, 142 on another) and then no file at all before the phone
 * hung up. The network was fine; the BROWSER could not read the file it had
 * been handed.
 *
 * A `File` from a picker is a reference, not bytes. The browser reads it again
 * when the upload is sent, and on Android a file that lives in Google Drive,
 * Photos or another app's storage can have been re-downloaded, changed or
 * stopped being readable by then (Chrome's `net::ERR_UPLOAD_FILE_CHANGED`).
 * The claim form asks for a category, a badge, an issuer and a note AFTER the
 * certificate, so there is always time for that to happen. `fetch` then
 * throws, which the page can only read as "no network".
 *
 * Reading the bytes at pick time closes that window: what is uploaded is the
 * copy in memory, which nothing on the phone can change. And a file that
 * cannot be read even then is reported on the spot, about the file, instead
 * of minutes later about the server.
 */

/**
 * The picked file as an in-memory copy, or null when its bytes cannot be read.
 * An empty read counts as unreadable: a cloud placeholder can report a name
 * and no content, and an empty file is not a certificate either way.
 */
export async function readPickedFile(file: File): Promise<File | null> {
  // A browser without Blob.arrayBuffer (Chromium < 76, Safari < 14) keeps the
  // reference as before, rather than refusing every file it is handed.
  if (typeof file.arrayBuffer !== 'function') return file;
  try {
    const bytes = await file.arrayBuffer();
    if (bytes.byteLength === 0) return null;
    return new File([bytes], file.name, { type: file.type, lastModified: file.lastModified });
  } catch {
    return null;
  }
}

export const UNREADABLE_FILE_MESSAGE =
  'This file could not be opened. If it is stored in Google Drive or another app, ' +
  'download it to this device first (or save it as a PDF), then choose it again.';

/**
 * What an upload screen says when its `fetch` THROWS: no HTTP answer arrived
 * at all. These screens used to say "Could not reach the server.", which sent
 * people to report the server down while it was taking every other upload
 * (2026-10-14). With the file already in memory, what is left to throw is the
 * phone's connection.
 */
export const UPLOAD_DROPPED_MESSAGE =
  'The file did not upload: the connection dropped. Check your internet ' +
  '(Wi-Fi works best for files) and try again.';
