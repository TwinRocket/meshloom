// What the user types to add or join a hashtag channel, normalized like the
// official MeshCore app (app.meshcore.nz): trim, lowercase, add the '#' when it
// is missing, accept ^#[a-z0-9-]+$ only, 30 UTF-8 bytes at most.
//
// Input only. The key is always the hash of the exact name (hashtagKey.ts), and
// names from the Community catalogue, the network or imports never go through
// this function: "#Fr" created by meshcore-cli is a real, different channel.

export const HASHTAG_INPUT_MAX_BYTES = 30;
// The on-radio channel name field: 32 UTF-8 bytes including the leading '#'.
export const HASHTAG_EXACT_MAX_BYTES = 32;

const OFFICIAL_HASHTAG = /^#[a-z0-9-]+$/;

export type HashtagInputError =
  | 'newMessage.hashtagRequired'
  | 'newMessage.hashtagInvalid'
  | 'newMessage.hashtagTooLong'
  | 'newMessage.hashtagTooLongExtended';

export type HashtagInputResult =
  { ok: true; name: string } | { ok: false; error: HashtagInputError };

function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length;
}

export function normalizeHashtagInput(raw: string): HashtagInputResult {
  const lowered = raw.trim().toLowerCase();
  const name = lowered.startsWith('#') ? lowered : `#${lowered}`;
  if (name === '#') {
    return { ok: false, error: 'newMessage.hashtagRequired' };
  }
  if (!OFFICIAL_HASHTAG.test(name)) {
    return { ok: false, error: 'newMessage.hashtagInvalid' };
  }
  if (utf8Length(name) > HASHTAG_INPUT_MAX_BYTES) {
    return { ok: false, error: 'newMessage.hashtagTooLong' };
  }
  return { ok: true, name };
}

// "Extended names" option: join a channel created elsewhere with an unusual
// name. Only the surrounding whitespace of the input box and one leading '#'
// are removed; case, inner spaces and symbols are hashed as typed.
export function exactHashtagInput(raw: string): HashtagInputResult {
  const trimmed = raw.trim();
  const room = trimmed.startsWith('#') ? trimmed.slice(1) : trimmed;
  if (!room) {
    return { ok: false, error: 'newMessage.hashtagRequired' };
  }
  const name = `#${room}`;
  if (utf8Length(name) > HASHTAG_EXACT_MAX_BYTES) {
    return { ok: false, error: 'newMessage.hashtagTooLongExtended' };
  }
  return { ok: true, name };
}
