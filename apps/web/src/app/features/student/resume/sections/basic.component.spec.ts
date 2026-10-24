import { ErrorHandler, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { AuthService } from '../../../../core/auth.service';
import { UNREADABLE_FILE_MESSAGE, UPLOAD_DROPPED_MESSAGE } from '../../../../core/picked-file';
import { ResumeBuilderService } from '../resume-builder.service';
import { ResumeIdentityService } from '../resume-identity.service';
import { RbBasicComponent } from './basic.component';

/**
 * The resume builder's profile photo is sent as an in-memory copy of the
 * picked image (core/picked-file.ts), and a request that gets no answer says
 * the connection dropped instead of "Could not reach the server."
 */
const jpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3]);
const readable = () => Promise.resolve(jpeg.buffer.slice(0));
const unreadable = () => Promise.reject(new DOMException('changed', 'NotReadableError'));

function pickedFile(name: string, reads: Array<() => Promise<ArrayBuffer>>): File {
  let n = 0;
  return {
    name,
    type: 'image/jpeg',
    size: jpeg.byteLength,
    lastModified: 1,
    arrayBuffer: () => (reads[n++] ?? reads[reads.length - 1])(),
  } as unknown as File;
}

function picked(file: File): Event {
  return { target: { files: [file], value: 'C:\\fakepath\\' + file.name } } as unknown as Event;
}

describe('Resume builder · uploading the profile photo', () => {
  const realFetch = globalThis.fetch;
  let reported: unknown[];
  let posted: FormData[];
  let postFails: boolean;

  async function section(): Promise<RbBasicComponent> {
    reported = [];
    posted = [];
    postFails = false;
    globalThis.fetch = (async (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        if (postFails) throw new TypeError('Failed to fetch');
        posted.push(init.body as FormData);
        return { ok: true, status: 201, json: async () => ({ id: 'photo-1' }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    }) as typeof fetch;
    await TestBed.configureTestingModule({
      imports: [RbBasicComponent],
      providers: [
        { provide: ErrorHandler, useValue: { handleError: (e: unknown) => reported.push(e) } },
        { provide: AuthService, useValue: { session: () => null } },
        {
          provide: ResumeBuilderService,
          useValue: {
            section: () => ({}),
            loaded: signal(false),
            patch: () => undefined,
            save: async () => undefined,
            error: signal(null),
          },
        },
        {
          provide: ResumeIdentityService,
          useValue: {
            usn: signal(''),
            courseName: signal(''),
            specializationName: signal(''),
            load: async () => undefined,
          },
        },
      ],
    }).compileComponents();
    return TestBed.createComponent(RbBasicComponent).componentInstance;
  }

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('sends the copy read at pick time, not the picker reference', async () => {
    const c = await section();
    const photo = pickedFile('me.jpg', [readable, unreadable]);
    await c.onPhotoFile(picked(photo));

    expect(posted).toHaveLength(1);
    const sent = posted[0].get('file') as File;
    expect(sent).not.toBe(photo);
    expect(sent.name).toBe('me.jpg');
    expect(Array.from(new Uint8Array(await sent.arrayBuffer()))).toEqual(Array.from(jpeg));
    expect(c.photoError()).toBeNull();
  });

  it('refuses a photo the phone cannot read, without sending anything', async () => {
    const c = await section();
    await c.onPhotoFile(picked(pickedFile('Asha.jpg', [unreadable])));

    expect(posted).toHaveLength(0);
    expect(c.photoError()).toBe(UNREADABLE_FILE_MESSAGE);
    expect(String(reported[0])).not.toContain('Asha');
  });

  it('says the connection dropped when no answer comes back', async () => {
    const c = await section();
    postFails = true;
    await c.onPhotoFile(picked(pickedFile('me.jpg', [readable])));

    expect(c.photoError()).toBe(UPLOAD_DROPPED_MESSAGE);
    expect(c.uploadingPhoto()).toBe(false);
  });
});
