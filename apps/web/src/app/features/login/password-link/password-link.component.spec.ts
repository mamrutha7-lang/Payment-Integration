import { provideHttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';

import { PasswordLinkComponent, namesTheToken } from './password-link.component';

/**
 * /reset and /activate tell a broken LINK from a refused PASSWORD. The first
 * needs a new link and no password will ever get past it; the second is a
 * retry with the same link. Reading one as the other sends a person with a
 * shortened link round the form for good.
 */
function answering(status: number, body: unknown): typeof fetch {
  return (async () =>
    ({ ok: status < 400, status, json: async () => body }) as unknown as Response) as typeof fetch;
}

/** FastAPI's answer to a request its schema refused, locating the field. */
const schemaRefusal = (field: string) => ({
  detail: [
    {
      type: 'string_too_long',
      loc: ['body', field],
      msg: 'String should have at most 200 characters',
      input: 'x',
    },
  ],
});

async function submitted(mode: 'reset' | 'activate', reply: typeof fetch) {
  await TestBed.configureTestingModule({
    imports: [PasswordLinkComponent],
    providers: [
      provideHttpClient(),
      provideRouter([]),
      {
        provide: ActivatedRoute,
        useValue: {
          snapshot: { data: { mode }, queryParamMap: convertToParamMap({ token: 'abc123' }) },
        },
      },
    ],
  }).compileComponents();
  const c = TestBed.createComponent(PasswordLinkComponent).componentInstance;
  c.form.setValue({ password: 'a-valid-password-e2e', confirm: 'a-valid-password-e2e' });
  globalThis.fetch = reply;
  await c.submit();
  return c;
}

describe('Set-password link · a refused link is not a refused password', () => {
  const realFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  for (const mode of ['reset', 'activate'] as const) {
    it(`/${mode}: a 422 locating the token is a link problem`, async () => {
      const c = await submitted(mode, answering(422, schemaRefusal('token')));
      expect(c.linkError()).not.toBeNull();
      expect(c.passwordError()).toBeNull();
    });

    it(`/${mode}: the server's sentence for a dead link is shown as it is`, async () => {
      const c = await submitted(
        mode,
        answering(410, { detail: 'This link is not valid. Ask for a new one.' }),
      );
      expect(c.linkError()).toBe('This link is not valid. Ask for a new one.');
      expect(c.passwordError()).toBeNull();
    });

    it(`/${mode}: a refused password keeps the link and asks again`, async () => {
      const c = await submitted(mode, answering(422, { detail: 'Use at least 12 characters.' }));
      expect(c.linkError()).toBeNull();
      expect(c.passwordError()).toBe('Use at least 12 characters.');
    });
  }

  it('reads only the token as the link', () => {
    expect(namesTheToken(schemaRefusal('token').detail)).toBe(true);
    expect(namesTheToken(schemaRefusal('password').detail)).toBe(false);
    expect(namesTheToken('Use at least 12 characters.')).toBe(false);
    expect(namesTheToken(undefined)).toBe(false);
  });
});
