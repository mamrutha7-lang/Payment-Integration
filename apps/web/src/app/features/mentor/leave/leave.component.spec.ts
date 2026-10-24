import { ErrorHandler } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AuthService } from '../../../core/auth.service';
import { UNREADABLE_FILE_MESSAGE, UPLOAD_DROPPED_MESSAGE } from '../../../core/picked-file';
import { LeaveComponent } from './leave.component';

/**
 * A document attached to a leave request is sent as an in-memory copy of the
 * picked file (core/picked-file.ts), and a request that gets no answer says
 * the connection dropped instead of "Could not reach the server."
 */
const pdf = new TextEncoder().encode('%PDF-1.7 medical certificate');
const readable = () => Promise.resolve(pdf.buffer.slice(0));
const unreadable = () => Promise.reject(new DOMException('changed', 'NotReadableError'));

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

describe('Leave · attaching a document', () => {
  const realFetch = globalThis.fetch;
  let reported: unknown[];
  let posted: FormData[];
  let postFails: boolean;

  async function screen(): Promise<LeaveComponent> {
    reported = [];
    posted = [];
    postFails = false;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST' && String(url).endsWith('/attachments')) {
        if (postFails) throw new TypeError('Failed to fetch');
        posted.push(init.body as FormData);
        return { ok: true, status: 201, json: async () => ({}) } as Response;
      }
      if (String(url).endsWith('/attachments')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      // The screen's own loads are not under test; a refusal leaves them empty.
      return { ok: false, status: 503, json: async () => ({}) } as Response;
    }) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [LeaveComponent],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: { session: () => null } },
        { provide: ErrorHandler, useValue: { handleError: (e: unknown) => reported.push(e) } },
      ],
    }).compileComponents();
    const c = TestBed.createComponent(LeaveComponent).componentInstance;
    c.viewing.set({ id: 'leave-1' } as never);
    return c;
  }

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('sends the copy read at pick time, not the picker reference', async () => {
    const c = await screen();
    const cloudFile = pickedFile('certificate.pdf', [readable, unreadable]);
    await c.onAttach(picked(cloudFile));

    expect(posted).toHaveLength(1);
    const sent = posted[0].get('file') as File;
    expect(sent).not.toBe(cloudFile);
    expect(sent.name).toBe('certificate.pdf');
    expect(Array.from(new Uint8Array(await sent.arrayBuffer()))).toEqual(Array.from(pdf));
    expect(c.attachError()).toBeNull();
  });

  it('refuses a file the phone cannot read, without sending anything', async () => {
    const c = await screen();
    await c.onAttach(picked(pickedFile('Asha Rao certificate.pdf', [unreadable])));

    expect(posted).toHaveLength(0);
    expect(c.attachError()).toBe(UNREADABLE_FILE_MESSAGE);
    expect(String(reported[0])).not.toContain('Asha');
  });

  it('says the connection dropped when no answer comes back', async () => {
    const c = await screen();
    postFails = true;
    await c.onAttach(picked(pickedFile('certificate.pdf', [readable])));

    expect(c.attachError()).toBe(UPLOAD_DROPPED_MESSAGE);
    expect(c.attachBusy()).toBe(false);
  });
});
