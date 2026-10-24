import { grantMatchesPolicy, recordingLabel } from './consent-sync';

/**
 * A consent row is a copy of the policy taken when the student agreed, and
 * the policy can change afterwards. These pin the two decisions the room
 * takes from that pair: whether Start may proceed without re-showing the
 * terms, and what "recording on/off" says.
 */
describe('grantMatchesPolicy', () => {
  const on = { scope_store_transcript: true, scope_store_audio: true };
  const noAudio = { scope_store_transcript: true, scope_store_audio: false };

  it('lets Start proceed while the policy still says what the grant says', () => {
    expect(grantMatchesPolicy(on, { store_transcript: true, store_audio: true })).toBe(true);
    expect(grantMatchesPolicy(noAudio, { store_transcript: true, store_audio: false })).toBe(true);
  });

  it('re-asks when the college turned recording ON after the student agreed', () => {
    // The direction that records a voice the student was never told about.
    expect(grantMatchesPolicy(noAudio, { store_transcript: true, store_audio: true })).toBe(false);
  });

  it('re-asks when the college turned recording OFF after the student agreed', () => {
    // The direction reported from the room: "recording on" over "No audio".
    expect(grantMatchesPolicy(on, { store_transcript: true, store_audio: false })).toBe(false);
  });

  it('re-asks on a transcript change too', () => {
    expect(grantMatchesPolicy(on, { store_transcript: false, store_audio: true })).toBe(false);
  });
});

describe('recordingLabel', () => {
  const grantOn = { scope_store_transcript: true, scope_store_audio: true };
  const grantOff = { scope_store_transcript: true, scope_store_audio: false };

  it('reads the policy, not the grant, once the card is loaded', () => {
    const allows = { policy: { store_transcript: true, store_audio: true }, recording_enabled_on_server: true };
    const forbids = { policy: { store_transcript: true, store_audio: false }, recording_enabled_on_server: true };
    expect(recordingLabel(grantOff, allows)).toBe('on');
    expect(recordingLabel(grantOn, forbids)).toBe('off');
  });

  it('says off when the server cannot record, whatever the college ticked', () => {
    const serverOff = { policy: { store_transcript: true, store_audio: true }, recording_enabled_on_server: false };
    expect(recordingLabel(grantOn, serverOff)).toBe('off');
  });

  it('treats an absent server flag as unknown, not as off', () => {
    const older = { policy: { store_transcript: true, store_audio: true } };
    expect(recordingLabel(grantOn, older)).toBe('on');
  });

  it('falls back to the grant when no card loaded', () => {
    expect(recordingLabel(grantOn, null)).toBe('on');
    expect(recordingLabel(grantOff, null)).toBe('off');
    expect(recordingLabel(null, null)).toBe('off');
  });
});
