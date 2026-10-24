/**
 * The two decisions the interview room takes from the student's standing
 * acknowledgement and the college's CURRENT policy, as pure functions so a
 * spec can pin them without a browser.
 *
 * WHY THIS EXISTS (2026-09-17). A consent row is a COPY of the college's
 * policy taken at the moment the student pressed "I agree" — the server copies
 * `store_transcript` / `store_audio` onto it and never edits the row again.
 * The room used to start straight away whenever it held a row, so once a
 * student had agreed, the policy could change under them without the room
 * noticing: the office ticked "Allow voice recording", every existing
 * student's row still said `scope_store_audio: false`, the recorder refused
 * with `no_consent`, and the record's explanation ("they are shown the terms
 * again at their next Start") was not true, because nothing showed them. The
 * other direction was the one reported: the row said "on" from the day the
 * student agreed, the room printed "recording on" from that row, and the
 * office's screen said "No audio" for the same interview.
 *
 * So Start proceeds on the standing row ONLY while the policy still says what
 * the row says (`grantMatchesPolicy`); otherwise the terms are shown again,
 * and "I agree" posts the acknowledgement the server then supersedes. And the
 * label reads the policy — and the operator's switch — rather than the row,
 * because it is a statement about what will happen, not about what was once
 * agreed.
 */

/** The two storage scopes on a consent grant, from ConsentOut. */
export interface GrantScopes {
  scope_store_transcript: boolean;
  scope_store_audio: boolean;
}

/** The college's effective policy, from StudentPolicyOut.policy. */
export interface PolicyScopes {
  store_transcript: boolean;
  store_audio: boolean;
}

/** What the room knows about the deployment beyond the policy itself. */
export interface RecordingContext {
  policy: PolicyScopes;
  /** The operator's `INTERVIEW_RECORDING_ENABLED`, from StudentPolicyOut.
   *  Absent on a server that predates the field, which reads as "unknown" and
   *  falls back to the policy alone rather than to "off". */
  recording_enabled_on_server?: boolean;
}

/**
 * May Start proceed on this grant without showing the terms again?
 *
 * True only when the grant's two storage scopes equal the policy's — the same
 * comparison the server's `acknowledged` flag makes. A grant that predates a
 * policy change is a promise about a different disclosure, in either
 * direction: a recording the student was never told about, or a "recording
 * on" label over an interview the recorder will refuse.
 */
export function grantMatchesPolicy(grant: GrantScopes, policy: PolicyScopes): boolean {
  return (
    Boolean(grant.scope_store_transcript) === Boolean(policy.store_transcript) &&
    Boolean(grant.scope_store_audio) === Boolean(policy.store_audio)
  );
}

/**
 * The word after "recording" on the Start line.
 *
 * Reads the POLICY when the card is loaded, because that is what the next
 * interview will do — and only "on" when the operator's switch is on too,
 * since a college that allows recording on a server that cannot record
 * captures nothing (`recorder_or_reason`'s first gate). With no card, the
 * grant is the only fact available and is used as it always was.
 */
export function recordingLabel(
  grant: GrantScopes | null,
  card: RecordingContext | null,
): 'on' | 'off' {
  if (card) {
    const serverOn = card.recording_enabled_on_server !== false;
    return card.policy.store_audio && serverOn ? 'on' : 'off';
  }
  return grant?.scope_store_audio ? 'on' : 'off';
}
