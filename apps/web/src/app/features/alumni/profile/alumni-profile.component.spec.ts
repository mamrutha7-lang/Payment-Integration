import { ErrorHandler } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { UNREADABLE_FILE_MESSAGE } from '../../../core/picked-file';
import { AlumniProfileComponent } from './alumni-profile.component';

/**
 * The alumnus picks a resume and then fills in the rest before pressing Save,
 * so the profile keeps an in-memory copy from the moment it is picked
 * (core/picked-file.ts). A resume the phone stops being able to read in
 * between still goes with the profile.
 */
const pdf = new TextEncoder().encode('%PDF-1.7 resume');
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

describe('Alumni profile · the resume', () => {
  const realFetch = globalThis.fetch;
  const fresh = {
    created: false,
    name: 'A',
    email: 'a@example.com',
    company: null,
    designation: null,
    joined_on: null,
    graduation_year: null,
    resume: null,
    updated_at: null,
  };
  let reported: unknown[];
  let posted: FormData[];

  async function screen(): Promise<AlumniProfileComponent> {
    reported = [];
    posted = [];
    globalThis.fetch = (async (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') posted.push(init.body as FormData);
      return {
        ok: true,
        status: 200,
        json: async () => ({ ...fresh, created: init?.method === 'POST' }),
      } as Response;
    }) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [AlumniProfileComponent],
      providers: [{ provide: ErrorHandler, useValue: { handleError: (e: unknown) => reported.push(e) } }],
    }).compileComponents();
    const c = TestBed.createComponent(AlumniProfileComponent).componentInstance;
    c.company = 'Acme';
    return c;
  }

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('is kept as a copy, so Save still sends it after the phone stops being able to read it', async () => {
    const c = await screen();
    const cloudFile = pickedFile('resume.pdf', [readable, unreadable]);
    await c.onPick(picked(cloudFile));
    await expect(cloudFile.arrayBuffer()).rejects.toThrow();
    await c.save();

    expect(posted).toHaveLength(1);
    const sent = posted[0].get('resume') as File;
    expect(sent).not.toBe(cloudFile);
    expect(sent.name).toBe('resume.pdf');
    expect(Array.from(new Uint8Array(await sent.arrayBuffer()))).toEqual(Array.from(pdf));
    expect(c.saveError()).toBeNull();
  });

  it('is refused on the spot when it cannot be read, and reported without its name', async () => {
    const c = await screen();
    await c.onPick(picked(pickedFile('Asha Rao resume.pdf', [unreadable])));

    expect(c.pickedFile()).toBeNull();
    expect(c.saveError()).toBe(UNREADABLE_FILE_MESSAGE);
    expect(reported).toHaveLength(1);
    expect(String(reported[0])).not.toContain('Asha');
  });

  it('is the later pick when an earlier read finishes last', async () => {
    const c = await screen();
    let finishFirst!: (b: ArrayBuffer) => void;
    const slow = () => new Promise<ArrayBuffer>((resolve) => (finishFirst = resolve));

    const first = c.onPick(picked(pickedFile('old.pdf', [slow])));
    expect(c.resumeReading()).toBe(true);
    await c.onPick(picked(pickedFile('new.pdf', [readable])));
    finishFirst(pdf.buffer.slice(0));
    await first;

    expect(c.pickedFile()!.name).toBe('new.pdf');
    expect(c.resumeReading()).toBe(false);
  });
});
