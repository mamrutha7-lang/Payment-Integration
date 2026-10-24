import {
  AUDIO_ROUTE_STORAGE_KEY,
  forgetRouteChoiceForTests,
  readStoredRoute,
  routeFromDeviceLabels,
  storeRoute,
} from './audio-route';

/**
 * The preselection only ever says "earphones" or nothing: a device name that
 * does not match is not evidence of a speaker, and the default (speaker) is
 * the side that protects against the interviewer being cut off by its echo.
 */
describe('routeFromDeviceLabels', () => {
  it('recognises the names earphones and headsets go by', () => {
    for (const label of [
      'Default - Headset Microphone (Jabra EVOLVE 20)',
      'Headphones (Realtek(R) Audio)',
      'AirPods Pro',
      'Galaxy Buds2',
      'Wired earphones',
      'Hands-Free AG Audio (boAt Rockerz 255)',
      'Bluetooth headset',
    ]) {
      expect(routeFromDeviceLabels([label])).toBe('earphones');
    }
  });

  it('says nothing for built-in speakers and microphones', () => {
    expect(
      routeFromDeviceLabels([
        'Default - Microphone Array (Realtek(R) Audio)',
        'Speakers (Realtek(R) Audio)',
        'MacBook Pro Microphone',
      ]),
    ).toBeNull();
    expect(routeFromDeviceLabels([])).toBeNull();
  });
});

describe('stored route', () => {
  beforeEach(() => {
    localStorage.removeItem(AUDIO_ROUTE_STORAGE_KEY);
    forgetRouteChoiceForTests();
  });

  it('round-trips a choice', () => {
    expect(readStoredRoute()).toBeNull();
    storeRoute('earphones');
    expect(readStoredRoute()).toBe('earphones');
  });

  it('ignores anything else in the key', () => {
    localStorage.setItem(AUDIO_ROUTE_STORAGE_KEY, 'loudspeaker');
    expect(readStoredRoute()).toBeNull();
  });

  it('keeps a choice for the page when storage refuses it', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    try {
      storeRoute('earphones');
      expect(readStoredRoute()).toBe('earphones');
    } finally {
      setItem.mockRestore();
    }
  });
});
