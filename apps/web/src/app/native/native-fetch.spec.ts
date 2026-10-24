import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { installNativeFetch } from './native-fetch';
import { tokenStore } from './token-store';

describe('installNativeFetch (phone app only)', () => {
  const original = globalThis.fetch;
  let seen: { url: string; auth: string | null }[];

  beforeEach(async () => {
    seen = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      seen.push({ url, auth: new Headers(init?.headers).get('Authorization') });
      return new Response('{}');
    }) as typeof fetch;
    await tokenStore.clear();
  });

  afterEach(() => {
    globalThis.fetch = original;
  });

  it('points /api/ at the server and attaches the saved token', async () => {
    await tokenStore.save('tok-1');
    installNativeFetch('https://reep.example.edu/');
    await fetch('/api/auth/me', { credentials: 'include' });
    expect(seen).toEqual([{ url: 'https://reep.example.edu/api/auth/me', auth: 'Bearer tok-1' }]);
  });

  it('sends no Authorization header when nobody is signed in', async () => {
    installNativeFetch('https://reep.example.edu');
    await fetch('/api/register/hierarchy');
    expect(seen[0]).toEqual({ url: 'https://reep.example.edu/api/register/hierarchy', auth: null });
  });

  it('leaves every other URL exactly as written', async () => {
    await tokenStore.save('tok-1');
    installNativeFetch('https://reep.example.edu');
    await fetch('/fonts/inter-400-latin.woff2');
    await fetch('https://o1.ingest.sentry.io/api/1/envelope/');
    expect(seen).toEqual([
      { url: '/fonts/inter-400-latin.woff2', auth: null },
      { url: 'https://o1.ingest.sentry.io/api/1/envelope/', auth: null },
    ]);
  });
});
