import { ErrorHandler } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { UNREADABLE_FILE_MESSAGE } from '../../core/picked-file';
import { RegistrationComponent } from './registration.component';

/**
 * The CV is the first box on /register and is sent only after a dozen more
 * are filled in. The form keeps an in-memory copy from the moment it is
 * picked, so a file the phone can no longer read by then still goes with the
 * application (all nine "Registration submit failed" reports in Sentry, 1 to
 * 7 Oct 2026, came from Android).
 */
const pdf = new TextEncoder().encode('%PDF-1.7 curriculum vitae');

/** A picked file whose reads are scripted: `reads[n]` answers the n-th read. */
function pickedFile(name: string, reads: Array<() => Promise<ArrayBuffer>>): File {
  let n = 0;
  return {
    name,
    type: 'application/pdf',
    size: pdf.byteLength,
    lastModified: 1,
    arrayBuffer: () => (reads[n++] ?? reads[reads.length - 1])(),
  } as unknown as File;
}

const readable = () => Promise.resolve(pdf.buffer.slice(0));
const unreadable = () => Promise.reject(new DOMException('changed', 'NotReadableError'));

/** The picker's change event, as the template hands it to `onCv`. */
function picked(file: File): Event {
  return { target: { files: [file], value: 'C:\\fakepath\\' + file.name } } as unknown as Event;
}

async function bytesOf(file: File): Promise<number[]> {
  return Array.from(new Uint8Array(await file.arrayBuffer()));
}

describe('Registration form · a picked CV', () => {
  const realFetch = globalThis.fetch;
  let reported: unknown[];

  async function form(): Promise<RegistrationComponent> {
    reported = [];
    // The constructor asks for the hierarchy; the form works without it.
    globalThis.fetch = (async () => ({ ok: false, status: 503 }) as Response) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [RegistrationComponent],
      providers: [
        provideRouter([]),
        { provide: ErrorHandler, useValue: { handleError: (e: unknown) => reported.push(e) } },
      ],
    }).compileComponents();
    return TestBed.createComponent(RegistrationComponent).componentInstance;
  }

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('is kept as a copy, so it still sends after the phone stops being able to read it', async () => {
    const c = await form();
    const cloudFile = pickedFile('cv.pdf', [readable, unreadable]);
    await c.onCv(picked(cloudFile));

    const staged = c.cvFile();
    expect(staged).not.toBeNull();
    expect(staged).not.toBe(cloudFile);
    expect(staged!.name).toBe('cv.pdf');
    await expect(cloudFile.arrayBuffer()).rejects.toThrow();
    expect(await bytesOf(staged!)).toEqual(Array.from(pdf));
    expect(c.cvError()).toBeNull();
    expect(c.filesReading()).toBe(false);
  });

  it('is refused on the spot when it cannot be read, and reported without its name', async () => {
    const c = await form();
    await c.onCv(picked(pickedFile('Asha Rao CV.pdf', [unreadable])));

    expect(c.cvFile()).toBeNull();
    expect(c.cvError()).toBe(UNREADABLE_FILE_MESSAGE);
    expect(reported).toHaveLength(1);
    expect(String(reported[0])).toContain('could not be read on the device');
    expect(String(reported[0])).not.toContain('Asha');
  });

  it('is the later pick when an earlier read finishes last', async () => {
    const c = await form();
    let finishFirst!: (b: ArrayBuffer) => void;
    const slow = () => new Promise<ArrayBuffer>((resolve) => (finishFirst = resolve));

    const first = c.onCv(picked(pickedFile('old.pdf', [slow])));
    expect(c.filesReading()).toBe(true);
    await c.onCv(picked(pickedFile('new.pdf', [readable])));
    finishFirst(pdf.buffer.slice(0));
    await first;

    expect(c.cvFile()!.name).toBe('new.pdf');
    expect(c.filesReading()).toBe(false);
  });
});
