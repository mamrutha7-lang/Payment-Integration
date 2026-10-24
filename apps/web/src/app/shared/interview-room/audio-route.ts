/**
 * Speaker or earphones: the one thing about the student's setup the echo gate
 * cannot measure well enough to decide alone.
 *
 * On SPEAKERS the microphone hears the interviewer, and the gate in
 * core/echo-gate.ts holds the uplink while it is audible so Nova never takes
 * its own voice for the student interrupting. On EARPHONES there is nothing
 * to hold back, and the gate only costs: a breath or an "mm" on a headset mic
 * is enough to pause the interviewer. So the room offers the choice, remembers
 * it on this device, and -- where the browser names the device -- preselects
 * it. It defaults to speaker because the failure that side prevents (the
 * interviewer cut off by its own echo) is the worse one.
 */

export type AudioRoute = 'speaker' | 'earphones';

/** Per-device convenience, not a record: a lab PC's next user may be on
 *  speakers, and nothing but this browser ever reads it. */
export const AUDIO_ROUTE_STORAGE_KEY = 'reep.interview.audioRoute';

/** Device names that mean the sound is going to the student's ears. Bluetooth
 *  audio is overwhelmingly earbuds and headsets for a student; a Bluetooth
 *  speaker would be preselected wrongly, and the switch is there for that. */
const EARPHONE_LABEL = /head(set|phone)|ear(phone|bud|piece)|airpods|\bbuds|bluetooth|hands-?free/i;

/** What the device names say, or null when they say nothing either way. */
export function routeFromDeviceLabels(labels: readonly string[]): AudioRoute | null {
  return labels.some((label) => EARPHONE_LABEL.test(label)) ? 'earphones' : null;
}

/** The choice made on this page, for when storage is blocked: a choice must
 *  still outrank a device name for as long as the page is open. */
let choiceThisPage: AudioRoute | null = null;

/** The student's own choice, or null when they have never made one. */
export function readStoredRoute(): AudioRoute | null {
  try {
    const value = localStorage.getItem(AUDIO_ROUTE_STORAGE_KEY);
    if (value === 'speaker' || value === 'earphones') return value;
  } catch {
    /* storage blocked: fall back to this page's choice */
  }
  return choiceThisPage;
}

export function storeRoute(route: AudioRoute): void {
  choiceThisPage = route;
  try {
    localStorage.setItem(AUDIO_ROUTE_STORAGE_KEY, route);
  } catch {
    /* storage blocked: the choice lasts this page only */
  }
}

/** Test seam: forget the in-page choice. */
export function forgetRouteChoiceForTests(): void {
  choiceThisPage = null;
}

/**
 * The labels of the devices the browser will use by default, input and
 * output. Empty until the page has microphone permission -- browsers withhold
 * labels before that -- which is why the room asks again after Start.
 */
export async function defaultDeviceLabels(): Promise<string[]> {
  try {
    const devices = await navigator.mediaDevices?.enumerateDevices?.();
    if (!devices) return [];
    const labels: string[] = [];
    for (const kind of ['audioinput', 'audiooutput'] as const) {
      const ofKind = devices.filter((d) => d.kind === kind);
      // Chrome lists a 'default' entry naming the real device; other engines
      // put the default first.
      const chosen = ofKind.find((d) => d.deviceId === 'default') ?? ofKind[0];
      if (chosen?.label) labels.push(chosen.label);
    }
    return labels;
  } catch {
    return [];
  }
}
