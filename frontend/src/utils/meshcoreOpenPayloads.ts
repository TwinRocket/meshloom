/**
 * Parsing and Open-wire helpers for rich-chat payloads sent as ordinary
 * plaintext mesh messages.
 *
 *   g:<gifId>        Giphy GIF        -> https://media.giphy.com/media/<id>/giphy.gif
 *   m:<lat>,<lon>|<label>|loc|poi     Location pin (Open map share)
 *   r:<hash>:<index> Emoji reaction   -> <index> picks an emoji from a fixed list
 *
 * Formats and the emoji table are ported from meshcore-open:
 *   lib/helpers/gif_helper.dart
 *   lib/helpers/reaction_helper.dart
 *   lib/widgets/emoji_picker.dart
 * (github.com/zjs81/meshcore-open, dev branch).
 *
 * Open reaction hash is Dart VM String.hashCode (seed 0, CombineHashes per
 * UTF-16 unit, FinalizeHash at kHashBits=30) on
 * `timestampSeconds + [senderName] + first5(text)`, then `hashCode & 0xFFFF`
 * as 4 hex chars — meshcore-open ReactionHelper, not the Sestriere port.
 * Channel hash uses the body after the "Name: " prefix; DMs omit senderName.
 */

import { parseSenderFromText } from './messageParser';

// --- Emoji table (order must match meshcore-open exactly for index compat) ---

export const QUICK_EMOJIS = ['👍', '❤️', '😂', '🎉', '👏', '🔥'];

// prettier-ignore
const SMILEYS = [
  '😀', '😃', '😄', '😁', '😅', '😂', '🤣', '😊', '😇', '🙂',
  '🙃', '😉', '😌', '😍', '🥰', '😘', '😗', '😙', '😚', '😋',
  '😛', '😝', '😜', '🤪', '🤨', '🧐', '🤓', '😎', '🥸', '🤩',
  '🥳', '😏', '😒', '😞', '😔', '😟', '😕', '🙁', '😣', '😖',
  '😫', '😩', '🥺', '😢', '😭', '😤', '😠', '😡', '🤬', '🤯',
  '😳', '🥵', '🥶', '😱', '😨', '😰', '😥', '😓', '🤗', '🤔',
  '🤭', '🤫', '🤥', '😶',
];

// prettier-ignore
const GESTURES = [
  '👍', '👎', '👊', '✊', '🤛', '🤜', '🤞', '✌️', '🤟', '🤘',
  '👌', '🤌', '🤏', '👈', '👉', '👆', '👇', '☝️', '👋', '🤚',
  '🖐️', '✋', '🖖', '👏', '🙌', '👐', '🤲', '🤝', '🙏', '✍️',
  '💅', '🤳', '💪',
];

// prettier-ignore
const HEARTS = [
  '❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '🤎', '💔',
  '❤️‍🔥', '❤️‍🩹', '💕', '💞', '💓', '💗', '💖', '💘', '💝', '💟',
  '💌', '💢', '💥', '💫', '💦', '💨', '🕳️', '💬', '👁️‍🗨️', '🗨️',
  '🗯️', '💭',
];

// prettier-ignore
const OBJECTS = [
  '🎉', '🎊', '🎈', '🎁', '🎀', '🪅', '🪆', '🏆', '🥇', '🥈',
  '🥉', '⚽', '⚾', '🥎', '🏀', '🏐', '🏈', '🏉', '🎾', '🥏',
  '🎳', '🏏', '🏑', '🏒', '🥍', '🏓', '🏸', '🥊', '🥋', '🥅',
  '⛳', '🔥', '⭐', '🌟', '✨', '⚡', '💡', '🔦', '🏮', '🪔',
  '📱', '💻', '⌚', '📷', '📺', '📻', '🎵', '🎶', '🚀',
];

/** Combined reaction emoji list, in the fixed index order used on the wire. */
export const REACTION_EMOJIS: readonly string[] = [
  ...QUICK_EMOJIS,
  ...SMILEYS,
  ...GESTURES,
  ...HEARTS,
  ...OBJECTS,
];

// --- GIF (g:<gifId>) ---

const GIF_WIRE = /^g:([A-Za-z0-9_-]+)$/;
const GIF_MEDIA =
  /^(?:https?:\/\/)?(?:media\d*\.)?giphy\.com\/media\/(?:[^/?]+\/)*([A-Za-z0-9_-]+)\/giphy(?:-[a-z]+)?\.gif(?:\?.*)?$/i;
const GIF_I = /^(?:https?:\/\/)?i\.giphy\.com\/([A-Za-z0-9_-]+)(?:\.gif)?(?:\?.*)?$/i;
const GIF_PAGE =
  /^(?:https?:\/\/)?(?:www\.)?giphy\.com\/gifs\/(?:[^/?]*-)?([A-Za-z0-9_]+)\/?(?:\?.*)?$/i;

/**
 * Parse a MeshCore Open GIF payload. Accepts the compact wire form and the
 * Giphy URL shapes Open's GifHelper understands (media, i.giphy, page).
 */
export function parseGif(text: string): string | null {
  const trimmed = text.trim();
  return (
    GIF_WIRE.exec(trimmed)?.[1] ??
    GIF_MEDIA.exec(trimmed)?.[1] ??
    GIF_I.exec(trimmed)?.[1] ??
    GIF_PAGE.exec(trimmed)?.[1] ??
    null
  );
}

/** Encode a GIF id as the compact Open wire form (`g:<id>`). */
export function formatGif(gifId: string): string {
  return `g:${gifId.trim()}`;
}

/** Build the Giphy media URL for a GIF id. */
export function giphyUrlForId(gifId: string): string {
  return `https://media.giphy.com/media/${gifId}/giphy.gif`;
}

// --- Location pin (m:<lat>,<lon>|<label>|loc|poi) ---

const LOCATION_PATTERN = /^m:(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\|([^|]*)\|(loc|poi)$/;

export interface ParsedLocation {
  lat: number;
  lon: number;
  label: string;
  kind: 'loc' | 'poi';
}

function isFiniteCoordinate(lat: number, lon: number): boolean {
  return (
    Number.isFinite(lat) &&
    Number.isFinite(lon) &&
    lat >= -90 &&
    lat <= 90 &&
    lon >= -180 &&
    lon <= 180
  );
}

/**
 * Parse a MeshCore Open location pin. Open sends
 * `m:<lat>,<lon>|<label>|loc` (own position) or `|poi` (dropped marker).
 */
export function parseLocation(text: string): ParsedLocation | null {
  const match = LOCATION_PATTERN.exec(text.trim());
  if (!match) return null;
  const lat = Number(match[1]);
  const lon = Number(match[2]);
  if (!isFiniteCoordinate(lat, lon)) return null;
  return { lat, lon, label: match[3], kind: match[4] as 'loc' | 'poi' };
}

/** Encode an Open location pin. Pipes in the label become slashes (Open rule). */
export function formatLocation(
  lat: number,
  lon: number,
  label: string,
  kind: 'loc' | 'poi' = 'loc'
): string | null {
  if (!isFiniteCoordinate(lat, lon) || (lat === 0 && lon === 0)) return null;
  const safeLabel = label.trim().replace(/\|/g, '/') || 'pin';
  return `m:${lat.toFixed(6)},${lon.toFixed(6)}|${safeLabel}|${kind}`;
}

/** OpenStreetMap pin URL for a parsed location. */
export function locationMapUrl(lat: number, lon: number): string {
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=16/${lat}/${lon}`;
}

// --- Reaction (r:<hash>:<index>) ---

const REACTION_PATTERN = /^r:([0-9a-f]{4}):([0-9a-f]{2})$/;

export interface ParsedReaction {
  /** The decoded reaction emoji. */
  emoji: string;
  /** Hash identifying the target message. */
  targetHash: string;
  /** Name of the target message's sender, when the payload carries one. */
  targetSender?: string;
}

/** Inputs for the Open reaction hash (matches meshcore-open ReactionHelper). */
export interface ReactionHashSource {
  /** Sender timestamp in Unix seconds (firmware wire resolution). */
  timestampSeconds: number;
  /** Channel: sender name. DM: null (implicit). */
  senderName: string | null;
  /** Message body only — no channel "Name: " prefix. */
  text: string;
}

/**
 * Parse a MeshCore Open reaction payload. Returns the decoded emoji and the
 * target-message hash, or null if the (trimmed) text is not a valid
 * `r:<hash>:<index>` payload or the index is out of range.
 */
export function parseReaction(text: string): ParsedReaction | null {
  const match = REACTION_PATTERN.exec(text.trim());
  if (!match) return null;
  const index = parseInt(match[2], 16);
  if (!Number.isInteger(index) || index < 0 || index >= REACTION_EMOJIS.length) {
    return null;
  }
  return { emoji: REACTION_EMOJIS[index], targetHash: match[1] };
}

/**
 * Dart VM String.hashCode (runtime/vm/object.h StringHasher + hash.h):
 * seed 0, CombineHashes per UTF-16 unit, FinalizeHash(hash, kHashBits=30).
 */
function dartStringHash(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash + str.charCodeAt(i)) >>> 0;
    hash = (hash + ((hash << 10) >>> 0)) >>> 0;
    hash ^= hash >>> 6;
    hash >>>= 0;
  }
  hash = (hash + ((hash << 3) >>> 0)) >>> 0;
  hash ^= hash >>> 11;
  hash >>>= 0;
  hash = (hash + ((hash << 15) >>> 0)) >>> 0;
  hash &= 0x3fffffff;
  return hash === 0 ? 1 : hash;
}

/**
 * 4-char lowercase hex Open reaction hash.
 * Input: `timestampSeconds + senderName? + first 5 UTF-16 units of text`.
 */
export function computeReactionHash(
  timestampSeconds: number,
  senderName: string | null,
  text: string
): string {
  const first5 = text.length >= 5 ? text.slice(0, 5) : text;
  const input =
    senderName != null
      ? `${timestampSeconds}${senderName}${first5}`
      : `${timestampSeconds}${first5}`;
  return (dartStringHash(input) & 0xffff).toString(16).padStart(4, '0');
}

/** Hex index of an emoji in the Open table, or null if it is not on the wire list. */
export function reactionEmojiIndex(emoji: string): string | null {
  const index = REACTION_EMOJIS.indexOf(emoji);
  if (index < 0) return null;
  return index.toString(16).padStart(2, '0');
}

/** Encode an outbound Open reaction (`r:HASH:INDEX`). Null when the emoji is not in the table. */
export function formatOpenReaction(
  timestampSeconds: number,
  senderName: string | null,
  text: string,
  emoji: string
): string | null {
  const index = reactionEmojiIndex(emoji);
  if (index == null) return null;
  return `r:${computeReactionHash(timestampSeconds, senderName, text)}:${index}`;
}

/**
 * Body a rich-payload parser should see: channel "Name: " stripped, then a
 * leading Open reply mention ("@[Name] ") if one is there.
 */
export function richMessageBody(fields: { type: 'PRIV' | 'CHAN'; text: string }): string {
  const content = fields.type === 'PRIV' ? fields.text : parseSenderFromText(fields.text).content;
  return splitReplyMention(content)?.body ?? content;
}

/**
 * Open hash inputs for a stored message, or null when it cannot be a target
 * (missing timestamp, or the row is itself a reaction).
 */
export function openReactionHashSource(fields: {
  type: 'PRIV' | 'CHAN';
  sender_timestamp: number | null;
  sender_name: string | null;
  text: string;
}): ReactionHashSource | null {
  if (fields.sender_timestamp == null) return null;
  const body = richMessageBody(fields);
  if (parseReaction(body) || parseMeshCoreOneReaction(body)) return null;
  return reactionHashSourceFromFields(fields);
}

/**
 * Map stored Meshloom message fields onto Open hash inputs.
 * Channel text is stored as "Name: body"; the hash uses the body only.
 * DMs omit senderName (Open 1:1 implicit).
 */
export function reactionHashSourceFromFields(fields: {
  type: 'PRIV' | 'CHAN';
  sender_timestamp: number | null;
  sender_name: string | null;
  text: string;
}): ReactionHashSource | null {
  if (fields.sender_timestamp == null) return null;
  if (fields.type === 'PRIV') {
    return { timestampSeconds: fields.sender_timestamp, senderName: null, text: fields.text };
  }
  const parsed = parseSenderFromText(fields.text);
  return {
    timestampSeconds: fields.sender_timestamp,
    senderName: fields.sender_name || parsed.sender,
    text: parsed.content,
  };
}

/**
 * Walk messages newest-first and return the index whose Open hash matches.
 * `getSource` should return null for rows that cannot be targets (missing
 * timestamp, or the reaction payload itself).
 */
export function findOpenReactionTargetIndex<T>(
  messages: readonly T[],
  targetHash: string,
  getSource: (message: T) => ReactionHashSource | null
): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    const source = getSource(messages[i]);
    if (!source) continue;
    if (
      computeReactionHash(source.timestampSeconds, source.senderName, source.text) === targetHash
    ) {
      return i;
    }
  }
  return -1;
}

/**
 * Parse an Open `r:HASH:INDEX` payload (or accept an already-parsed reaction)
 * and attach it to the newest matching target. MeshCore One is inbound-only
 * here — its hash is a different dialect.
 */
export function attachOpenReaction<T>(
  messages: readonly T[],
  reactionOrText: ParsedReaction | string,
  getSource: (message: T) => ReactionHashSource | null
): { targetIndex: number; reaction: ParsedReaction } | null {
  const reaction =
    typeof reactionOrText === 'string' ? parseReaction(reactionOrText) : reactionOrText;
  if (!reaction) return null;
  const targetIndex = findOpenReactionTargetIndex(messages, reaction.targetHash, getSource);
  if (targetIndex < 0) return null;
  return { targetIndex, reaction };
}

// --- MeshCore One reaction ({emoji}@[{sender}]\n{hash}) ---

// MeshCore One (github.com/Avi0n/MeshCoreOne, docs/Reactions.md) speaks a
// different reaction dialect that meshcore-open users see too, and which
// otherwise renders as an emoji followed by a junk token (issue #354):
//
//   channel: {emoji}@[{targetSenderName}]\n{hash}
//   DM:      {emoji}\n{hash}
//
// A newer MC1 build swaps the first line to "@[{targetSenderName}]{emoji}", so
// both orders are accepted. <hash> is 8 Crockford Base32 chars (SHA-256 of the
// target text + its sender timestamp, first 5 bytes) and is not resolved here.
// There is no wire representation for removing a reaction.

// Crockford Base32 is case-insensitive and normalizes I/L -> 1 and O -> 0, so
// every letter but U can appear in a received hash.
const MC1_HASH_PATTERN = /^[0-9a-tv-z]{8}$/i;

// The first line is the emoji plus, on a channel reaction, the target's name in
// either order. MC1 only checks that the emoji segment is non-empty and starts
// with an emoji, so match it loosely and test the first character.
const MC1_HEAD_PATTERN = /^(?:([^@[\]]+)(?:@\[([^\]]+)\])?|@\[([^\]]+)\](.+))$/;
const EMOJI_START = /^\p{Extended_Pictographic}/u;

/**
 * Parse a MeshCore One reaction payload. Returns the emoji, the (unresolved)
 * target-message hash and, for channel reactions, the target sender's name;
 * null when the text is not a MeshCore One reaction.
 */
export function parseMeshCoreOneReaction(text: string): ParsedReaction | null {
  const lines = text.trim().split('\n');
  if (lines.length !== 2) return null;
  const hash = lines[1].trim();
  if (!MC1_HASH_PATTERN.test(hash)) return null;
  const head = MC1_HEAD_PATTERN.exec(lines[0].trim());
  if (!head) return null;
  const emoji = (head[1] ?? head[4]).trim();
  if (!EMOJI_START.test(emoji)) return null;
  const targetSender = head[2] ?? head[3];
  return targetSender ? { emoji, targetHash: hash, targetSender } : { emoji, targetHash: hash };
}

// --- Reply-mention prefix (@[senderName] <payload>) ---

// meshcore-open prefixes replies with "@[senderName] " before the message body
// (see meshcore-open channels.md / BLE_PROTOCOL.md). Its own display code strips
// that prefix before parsing rich payloads, so a GIF/reaction reply arrives on
// the wire as "@[Name] g:<id>". parseGif/parseReaction stay strict (whole-body
// only); this splits the reply prefix off so the remainder can be parsed.
const REPLY_MENTION_PREFIX = /^(@\[[^\]]+\])\s+([\s\S]+)$/;

export interface SplitReplyMention {
  /** The leading "@[Name]" reply-mention token. */
  mention: string;
  /** The message remainder after the reply-mention prefix. */
  body: string;
}

/**
 * Split a leading meshcore-open reply mention ("@[Name] ") off the text, or
 * return null when there is no such prefix.
 */
export function splitReplyMention(text: string): SplitReplyMention | null {
  const match = REPLY_MENTION_PREFIX.exec(text.trim());
  if (!match) return null;
  return { mention: match[1], body: match[2] };
}
