import { ErrorHandler, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AuthService } from '../../core/auth.service';
import { UNREADABLE_FILE_MESSAGE, UPLOAD_DROPPED_MESSAGE } from '../../core/picked-file';
import { AccountComponent } from './account.component';

/**
 * The account page is the second door to the staff signature (the first is
 * /mentor/signature). It sends an in-memory copy of the picked image
 * (core/picked-file.ts), and a request that gets no answer says the
 * connection dropped instead of printing the browser's "Failed to fetch".
 */
const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);
const readable = () => Promise.resolve(png.buffer.slice(0));
const unreadable = () => Promise.reject(new DOMException('changed', 'NotReadableError'));

function pickedFile(name: string, reads: Array<() => Promise<ArrayBuffer>>): File {
  let n = 0;
  return {
    name,
    type: 'image/png',
    size: png.byteLength,
    lastModified: 1,
    arrayBuffer: () => (reads[n++] ?? reads[reads.length - 1])(),
  } as unknown as File;
}

function picked(file: File): Event {
  return { target: { files: [file], value: 'C:\\fakepath\\' + file.name } } as unknown as Event;
}

describe('Account · uploading the signature', () => {
  const realFetch = globalThis.fetch;
  let reported: unknown[];
  let sent: FormData[];
  let putFails: boolean;

  async function page(): Promise<AccountComponent> {
    reported = [];
    sent = [];
    putFails = false;
    globalThis.fetch = (async (_url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        if (putFails) throw new TypeError('Failed to fetch');
        sent.push(init.body as FormData);
        return { ok: true, status: 200, json: async () => ({ present: true }) } as Response;
      }
      // The page's own loads are not under test; a refusal leaves them empty.
      return { ok: false, status: 503, json: async () => ({}) } as Response;
    }) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [AccountComponent],
      providers: [
        provideRouter([]),
        { provide: ErrorHandler, useValue: { handleError: (e: unknown) => reported.push(e) } },
        {
          provide: AuthService,
          useValue: { session: signal(null), refresh: async () => null, logout: async () => undefined },
        },
      ],
    }).compileComponents();
    return TestBed.createComponent(AccountComponent).componentInstance;
  }

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('sends the copy read at pick time, not the picker reference', async () => {
    const c = await page();
    const photo = pickedFile('signature.png', [readable, unreadable]);
    await c.uploadSignature(picked(photo));

    expect(sent).toHaveLength(1);
    const file = sent[0].get('file') as File;
    expect(file).not.toBe(photo);
    expect(file.name).toBe('signature.png');
    expect(Array.from(new Uint8Array(await file.arrayBuffer()))).toEqual(Array.from(png));
    expect(c.signatureError()).toBeNull();
  });

  it('refuses an image the phone cannot read, without sending anything', async () => {
    const c = await page();
    await c.uploadSignature(picked(pickedFile('Asha signature.png', [unreadable])));

    expect(sent).toHaveLength(0);
    expect(c.signatureError()).toBe(UNREADABLE_FILE_MESSAGE);
    expect(String(reported[0])).not.toContain('Asha');
  });

  it('says the connection dropped, not "Failed to fetch"', async () => {
    const c = await page();
    putFails = true;
    await c.uploadSignature(picked(pickedFile('signature.png', [readable])));

    expect(c.signatureError()).toBe(UPLOAD_DROPPED_MESSAGE);
    expect(c.signatureBusy()).toBe(false);
  });
});
