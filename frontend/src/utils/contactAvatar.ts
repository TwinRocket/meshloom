/**
 * Generate consistent profile "images" for contacts.
 *
 * Uses the contact's public key to generate a consistent background color,
 * and extracts initials or emoji from the name for display.
 * Repeaters (type=2) and room servers (type=3) always show a fixed glyph.
 */

import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM } from '../types';

// Fixed contact-type avatar styling
const REPEATER_AVATAR = {
  text: '🛜',
  background: '#444444',
  textColor: '#ffffff',
};

const ROOM_AVATAR = {
  text: '🛖',
  background: '#6b4f2a',
  textColor: '#ffffff',
};

// DJB2 hash function for strings
export function hashString(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash = hash & hash; // Convert to 32-bit integer
  }
  return Math.abs(hash);
}

// Regex to match emoji (covers most common emoji ranges)
// Flag emojis (e.g., 🇺🇸) are TWO consecutive regional indicator symbols, so we match those first
const emojiRegex =
  /[\u{1F1E0}-\u{1F1FF}]{2}|[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\u{1F600}-\u{1F64F}]|[\u{1F680}-\u{1F6FF}]/u;

const WORD_RUN = /\p{L}+/gu;

function graphemesOf(value: string): string[] {
  return Array.from(value);
}

function fallbackKeyText(publicKey: string): string {
  return publicKey.slice(0, 2).toUpperCase();
}

/**
 * Two-letter mark, not a chopped word: `#meshloom` → `Me`, `Jane Smith` → `JS`.
 */
function getAvatarText(name: string | null, publicKey: string): string {
  if (!name) {
    return fallbackKeyText(publicKey);
  }

  // Hashtag channels are named "#meshloom": the marker is not part of the identity.
  const displayName = name.replace(/#/g, '').trim();
  if (!displayName) {
    return fallbackKeyText(publicKey);
  }

  const emojiMatch = displayName.match(emojiRegex);
  if (emojiMatch) {
    return emojiMatch[0];
  }

  const words = displayName.match(WORD_RUN) ?? [];
  const first = words[0];
  if (!first) {
    return fallbackKeyText(publicKey);
  }

  const second = words[1];
  if (second) {
    const a = graphemesOf(first)[0];
    const b = graphemesOf(second)[0];
    if (a && b) return (a + b).toLocaleUpperCase();
  }

  const letters = graphemesOf(first);
  const one = letters[0];
  if (!one) return fallbackKeyText(publicKey);
  const two = letters[1];
  return two ? one.toLocaleUpperCase() + two.toLocaleLowerCase() : one.toLocaleUpperCase();
}

/**
 * Generate a consistent HSL color from a public key.
 * Uses saturation and lightness ranges that work well for backgrounds.
 */
function getAvatarColor(publicKey: string): {
  background: string;
  text: string;
} {
  const hash = hashString(publicKey);

  // Use hash to generate hue (0-360)
  const hue = hash % 360;

  // Use different bits of hash for saturation variation (50-80%)
  const saturation = 50 + ((hash >> 8) % 30);

  // Lightness in a range that allows readable text (35-55%)
  const lightness = 35 + ((hash >> 16) % 20);

  const background = `hsl(${hue}, ${saturation}%, ${lightness}%)`;

  // Calculate perceived luminance to determine text color
  // For HSL, we can approximate: if lightness < 50%, use white text
  // We'll use a slightly lower threshold since saturated colors appear darker
  const textColor = lightness < 45 ? '#ffffff' : '#000000';

  return { background, text: textColor };
}

/**
 * Get all avatar properties for a contact.
 * Repeaters and room servers always get a special fixed avatar.
 */
export function getContactAvatar(
  name: string | null,
  publicKey: string,
  contactType?: number
): {
  text: string;
  background: string;
  textColor: string;
} {
  if (contactType === CONTACT_TYPE_REPEATER) {
    return REPEATER_AVATAR;
  }
  if (contactType === CONTACT_TYPE_ROOM) {
    return ROOM_AVATAR;
  }

  const text = getAvatarText(name, publicKey);
  const colors = getAvatarColor(publicKey);

  return {
    text,
    background: colors.background,
    textColor: colors.text,
  };
}
