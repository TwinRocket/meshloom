import type { Channel, Contact, Conversation } from '../types';
import { isFeedConversation } from '../components/feed/feedConversation';
import { findPublicChannel, PUBLIC_CHANNEL_NAME } from './publicChannel';
import { getContactDisplayName } from './pubkey';
import {
  SETTINGS_SECTION_ORDER,
  type SettingsSection,
} from '../components/settings/settingsConstants';

interface ParsedHashConversation {
  type:
    | 'channel'
    | 'contact'
    | 'raw'
    | 'feed'
    | 'map'
    | 'live'
    | 'visualizer'
    | 'search'
    | 'trace'
    | 'locate'
    | 'discovered'
    | 'test';
  /** Conversation identity token (channel key or contact public key, or legacy name token) */
  name: string;
  /** For the feed: the preset it opens in (`#control`) */
  feedPreset?: 'control';
  /** Optional human-readable label segment (ignored for identity resolution) */
  label?: string;
  /** For map view: public key prefix to focus on */
  mapFocusKey?: string;
  /** For locate view: key, prefix, or search token */
  locateKey?: string;
}

const SETTINGS_SECTIONS: SettingsSection[] = SETTINGS_SECTION_ORDER;

// Parse URL hash to get conversation
// (e.g., #channel/ABCDEF0123456789ABCDEF0123456789 or #contact/<64-char-pubkey>).
export function parseHashConversation(): ParsedHashConversation | null {
  const hash = window.location.hash.slice(1); // Remove leading #
  if (!hash) return null;

  if (hash === 'raw') {
    return { type: 'raw', name: 'raw' };
  }

  if (hash === 'feed') {
    return { type: 'feed', name: 'feed' };
  }

  if (hash === 'control') {
    // Kept for bookmarks and kiosk URLs: the Control page became a preset of the feed.
    return { type: 'feed', name: 'feed', feedPreset: 'control' };
  }

  if (hash === 'map') {
    return { type: 'map', name: 'map' };
  }

  if (hash === 'live') {
    return { type: 'live', name: 'live' };
  }

  if (hash === 'visualizer') {
    return { type: 'visualizer', name: 'visualizer' };
  }

  if (hash === 'search') {
    return { type: 'search', name: 'search' };
  }

  if (hash === 'trace') {
    return { type: 'trace', name: 'trace' };
  }

  if (hash === 'locate') {
    return { type: 'locate', name: 'locate' };
  }

  if (hash === 'discovered') {
    return { type: 'discovered', name: 'discovered' };
  }

  if (hash === 'test') {
    return { type: 'test', name: 'test' };
  }

  if (hash.startsWith('locate/')) {
    const locateKey = hash.slice('locate/'.length);
    if (locateKey) {
      return { type: 'locate', name: 'locate', locateKey: decodeURIComponent(locateKey) };
    }
    return { type: 'locate', name: 'locate' };
  }

  // Check for map with focus: #map/focus/{pubkey_prefix}
  if (hash.startsWith('map/focus/')) {
    const focusKey = hash.slice('map/focus/'.length);
    if (focusKey) {
      return { type: 'map', name: 'map', mapFocusKey: decodeURIComponent(focusKey) };
    }
    return { type: 'map', name: 'map' };
  }

  const slashIndex = hash.indexOf('/');
  if (slashIndex === -1) return null;

  const type = hash.slice(0, slashIndex);
  const value = hash.slice(slashIndex + 1);
  if (!(type === 'channel' || type === 'contact') || !value) {
    return null;
  }

  // Support both:
  // - Legacy: #channel/Public
  // - Stable: #channel/<id>
  // - Stable + readable: #channel/<id>/<display-name>
  const valueSlashIndex = value.indexOf('/');
  const tokenRaw = valueSlashIndex === -1 ? value : value.slice(0, valueSlashIndex);
  const labelRaw = valueSlashIndex === -1 ? '' : value.slice(valueSlashIndex + 1);

  const token = decodeURIComponent(tokenRaw);
  if (!token) return null;

  return {
    type,
    name: token,
    ...(labelRaw ? { label: decodeURIComponent(labelRaw) } : {}),
  };
}

/** Places inside a settings section a link may point at (`#settings/<section>/<anchor>`). */
const SETTINGS_ANCHORS: Partial<Record<SettingsSection, readonly string[]>> = {
  // The flasher's public docs link users to the backup (docs/user/*/backup.md).
  database: ['backup'],
};

function parseHashSettingsTarget(): { section: SettingsSection; anchor: string | null } | null {
  const hash = window.location.hash.slice(1);
  if (!hash.startsWith('settings/')) {
    return null;
  }

  const [rawSection, rawAnchor] = hash.slice('settings/'.length).split('/', 2);
  const section = decodeURIComponent(rawSection) as SettingsSection;
  if (!SETTINGS_SECTIONS.includes(section)) {
    return null;
  }
  const anchor = rawAnchor ? decodeURIComponent(rawAnchor) : null;
  return {
    section,
    anchor: anchor && SETTINGS_ANCHORS[section]?.includes(anchor) ? anchor : null,
  };
}

export function parseHashSettingsSection(): SettingsSection | null {
  return parseHashSettingsTarget()?.section ?? null;
}

/** The known anchor of a `#settings/<section>/<anchor>` hash, if any. */
export function parseHashSettingsAnchor(): string | null {
  return parseHashSettingsTarget()?.anchor ?? null;
}

// The hash is rewritten to `#settings/<section>` as soon as settings open, so
// the anchor is remembered when the hash is read and taken by the section.
let pendingSettingsAnchor: { section: SettingsSection; anchor: string } | null = null;

/** Remember the anchor of the current hash. A hash without one never clears a
 * pending anchor: by then it is usually the rewritten `#settings/<section>`. */
export function rememberSettingsAnchor(): void {
  const target = parseHashSettingsTarget();
  if (target && target.anchor) {
    pendingSettingsAnchor = { section: target.section, anchor: target.anchor };
  }
}

export function takeSettingsAnchor(section: SettingsSection): string | null {
  if (!pendingSettingsAnchor || pendingSettingsAnchor.section !== section) return null;
  const { anchor } = pendingSettingsAnchor;
  pendingSettingsAnchor = null;
  return anchor;
}

export function getSettingsHash(section: SettingsSection): string {
  return `#settings/${encodeURIComponent(section)}`;
}

export function resolveChannelFromHashToken(token: string, channels: Channel[]): Channel | null {
  const normalizedToken = token.trim();
  if (!normalizedToken) return null;

  // Preferred path: stable identity by channel key.
  const byKey = channels.find((c) => c.key.toLowerCase() === normalizedToken.toLowerCase());
  if (byKey) return byKey;

  // Legacy Public hashes should resolve to the canonical Public key, not any
  // arbitrary row that happens to share the display name.
  if (normalizedToken.toLowerCase() === PUBLIC_CHANNEL_NAME.toLowerCase()) {
    const publicChannel = findPublicChannel(channels);
    if (publicChannel) return publicChannel;
  }

  // Backward compatibility for legacy name-based hashes.
  return (
    channels.find((c) => c.name === normalizedToken || c.name === `#${normalizedToken}`) || null
  );
}

export function resolveContactFromHashToken(token: string, contacts: Contact[]): Contact | null {
  const normalizedToken = token.trim();
  if (!normalizedToken) return null;
  const lowerToken = normalizedToken.toLowerCase();

  // Preferred path: stable identity by full public key.
  const byKey = contacts.find((c) => c.public_key.toLowerCase() === lowerToken);
  if (byKey) return byKey;

  // Backward compatibility for legacy name/prefix-based hashes.
  return (
    contacts.find(
      (c) =>
        getContactDisplayName(c.name, c.public_key, c.last_advert) === normalizedToken ||
        c.public_key.toLowerCase().startsWith(lowerToken)
    ) || null
  );
}

/**
 * Generate a URL hash for focusing on a contact in the map view
 * @param publicKeyPrefix - The public key or prefix to focus on
 */
export function getMapFocusHash(publicKeyPrefix: string): string {
  return `#map/focus/${encodeURIComponent(publicKeyPrefix)}`;
}

export function getLocateHash(query?: string): string {
  const trimmed = query?.trim() ?? '';
  return trimmed ? `#locate/${encodeURIComponent(trimmed)}` : '#locate';
}

// Generate URL hash from conversation
export function getConversationHash(conv: Conversation | null): string {
  if (!conv) return '';
  if (conv.type === 'raw') return '#raw';
  if (isFeedConversation(conv)) return conv.feedPreset === 'control' ? '#control' : '#feed';
  if (conv.type === 'map') {
    return conv.mapFocusKey ? getMapFocusHash(conv.mapFocusKey) : '#map';
  }
  if (conv.type === 'live') return '#live';
  if (conv.type === 'visualizer') return '#visualizer';
  if (conv.type === 'search') return '#search';
  if (conv.type === 'trace') return '#trace';
  if (conv.type === 'locate') return getLocateHash(conv.locateKey);
  if (conv.type === 'discovered') return '#discovered';
  if (conv.type === 'test') return '#test';

  // Use immutable IDs for identity, append readable label for UX.
  if (conv.type === 'channel') {
    const label = conv.name.startsWith('#') ? conv.name.slice(1) : conv.name;
    return `#channel/${encodeURIComponent(conv.id)}/${encodeURIComponent(label)}`;
  }
  return `#contact/${encodeURIComponent(conv.id)}/${encodeURIComponent(conv.name)}`;
}

// Update URL hash without adding to history
export function updateUrlHash(conv: Conversation | null): void {
  const newHash = getConversationHash(conv);
  if (newHash !== window.location.hash) {
    window.history.replaceState(null, '', newHash || window.location.pathname);
  }
}

// Update URL hash and add a new browser history entry
export function pushUrlHash(conv: Conversation | null): void {
  const newHash = getConversationHash(conv);
  if (newHash !== window.location.hash) {
    window.history.pushState(null, '', newHash || window.location.pathname);
  }
}

export function updateSettingsHash(section: SettingsSection): void {
  const newHash = getSettingsHash(section);
  if (newHash !== window.location.hash) {
    window.history.replaceState(null, '', newHash);
  }
}

// Push a settings hash as a new browser history entry
export function pushSettingsHash(section: SettingsSection): void {
  const newHash = getSettingsHash(section);
  if (newHash !== window.location.hash) {
    window.history.pushState(null, '', newHash);
  }
}
