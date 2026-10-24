import { ErrorHandler } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { UNREADABLE_FILE_MESSAGE, UPLOAD_DROPPED_MESSAGE } from '../../../core/picked-file';
import { UpskillingComponent } from './upskilling.component';

/**
 * Faculty Upskilling sends an in-memory copy of the picked certificate
 * (core/picked-file.ts), so a file the phone stops being able to read is not
 * the reason an upload fails, and a failure that is the connection says so.
 */
const pdf = new TextEncoder().encode('%PDF-1.7 course certificate');
const readable = () => Promise.resolve(pdf.buffer.slice(0));
const unreadable = () => Promise.reject(new DOMException('changed', 'NotReadableError'));

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

function picked(file: File): Event {
  return { target: { files: [file], value: 'C:\\fakepath\\' + file.name } } as unknown as Event;
}

describe('Faculty Upskilling · uploading a certificate', () => {
  const realFetch = globalThis.fetch;
  let reported: unknown[];
  let posted: FormData[];
  let postFails: boolean;

  async function screen(): Promise<UpskillingComponent> {
    reported = [];
    posted = [];
    postFails = false;
    globalThis.fetch = (async (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        if (postFails) throw new TypeError('Failed to fetch');
        posted.push(init.body as FormData);
        return { ok: true, status: 201, json: async () => ({}) } as Response;
      }
      return { ok: true, status: 200, json: async () => [] } as Response;
    }) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [UpskillingComponent],
      providers: [{ provide: ErrorHandler, useValue: { handleError: (e: unknown) => reported.push(e) } }],
    }).compileComponents();
    const c = TestBed.createComponent(UpskillingComponent).componentInstance;
    c.title = 'Financial Modelling';
    return c;
  }

  /** onPick starts the upload without returning its promise; let it finish. */
  async function settle(c: UpskillingComponent): Promise<void> {
    for (let i = 0; i < 20 && c.uploading(); i++) await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  }

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('sends the copy read at pick time, not the picker reference', async () => {
    const c = await screen();
    const cloudFile = pickedFile('cert.pdf', [readable, unreadable]);
    c.onPick(picked(cloudFile));
    await settle(c);

    expect(posted).toHaveLength(1);
    const sent = posted[0].get('file') as File;
    expect(sent).not.toBe(cloudFile);
    expect(sent.name).toBe('cert.pdf');
    expect(Array.from(new Uint8Array(await sent.arrayBuffer()))).toEqual(Array.from(pdf));
    expect(c.uploadError()).toBeNull();
  });

  it('refuses a file the phone cannot read, without sending anything', async () => {
    const c = await screen();
    c.onPick(picked(pickedFile('Asha Rao cert.pdf', [unreadable])));
    await settle(c);

    expect(posted).toHaveLength(0);
    expect(c.uploadError()).toBe(UNREADABLE_FILE_MESSAGE);
    expect(reported).toHaveLength(1);
    expect(String(reported[0])).not.toContain('Asha');
  });

  it('says the connection dropped when no answer comes back', async () => {
    const c = await screen();
    postFails = true;
    c.onPick(picked(pickedFile('cert.pdf', [readable])));
    await settle(c);

    expect(c.uploadError()).toBe(UPLOAD_DROPPED_MESSAGE);
    expect(String(reported[0])).toContain('no HTTP response');
  });
});
