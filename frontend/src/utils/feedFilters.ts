import type { Channel, Message } from '../types';
import { isPendingChannel } from './channelMembership';

/**
 * The feed's filter model and its single evaluator.
 *
 * `rowPassesFilters` decides what the timeline shows, for rows fetched over REST and rows
 * pushed over the WebSocket alike. Two of the filters (`type`, `outgoing`) are also sent
 * to `GET /api/messages` through `filtersToQuery` so hidden rows are not fetched; the
 * server answers the same question as the evaluator, which `feedFilters.json` pins on
 * both sides. Everything else (muted, pending) is client-side only.
 */

export interface FeedFilters {
  sources: { messages: boolean; control: boolean; events: boolean };
  chan: boolean;
  priv: boolean;
  incoming: boolean;
  outgoing: boolean;
  includeMuted: boolean;
  includePending: boolean;
  /**
   * The node picked in the Nodes facet: a control thread id (`contact:<key>`,
   * `channel:<key>`, `us`, `unresolved`), or null for all. Control rows of other threads
   * are hidden; messages are kept only for a channel node, and only that channel's.
   */
  node: string | null;
}

export const FEED_FILTERS_STORAGE_KEY = 'meshloom-feed-filters';

export const defaultFeedFilters: FeedFilters = {
  sources: { messages: true, control: true, events: false },
  chan: true,
  priv: true,
  incoming: true,
  outgoing: true,
  includeMuted: false,
  includePending: false,
  node: null,
};

/**
 * The Control preset (`#control`): the feed with only the control source on (`FeedView` also
 * opens the Nodes facet sheet with it on a phone). It is applied when the view opens and never written to storage, so the
 * regular feed keeps the filters the reader chose for it.
 */
export const controlPreset: FeedFilters = {
  ...defaultFeedFilters,
  sources: { messages: false, control: true, events: false },
};

export interface FeedQuery {
  type?: 'CHAN' | 'PRIV';
  outgoing?: boolean;
}

/** Fill a stored or partial object with defaults; a field of the wrong type is ignored. */
export function normalizeFeedFilters(raw: unknown): FeedFilters {
  const input = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>;
  const sources = (
    typeof input.sources === 'object' && input.sources !== null ? input.sources : {}
  ) as Record<string, unknown>;
  const flag = (value: unknown, fallback: boolean) =>
    typeof value === 'boolean' ? value : fallback;
  const d = defaultFeedFilters;
  return {
    sources: {
      messages: flag(sources.messages, d.sources.messages),
      control: flag(sources.control, d.sources.control),
      events: flag(sources.events, d.sources.events),
    },
    chan: flag(input.chan, d.chan),
    priv: flag(input.priv, d.priv),
    incoming: flag(input.incoming, d.incoming),
    outgoing: flag(input.outgoing, d.outgoing),
    includeMuted: flag(input.includeMuted, d.includeMuted),
    includePending: flag(input.includePending, d.includePending),
    node: typeof input.node === 'string' && input.node ? input.node : null,
  };
}

/** Stored filters, or the defaults when storage is absent, blocked or corrupted. */
export function loadFeedFilters(): FeedFilters {
  try {
    const stored = localStorage.getItem(FEED_FILTERS_STORAGE_KEY);
    if (stored) return normalizeFeedFilters(JSON.parse(stored));
  } catch {
    // Private window, blocked storage or invalid JSON: defaults, silently.
  }
  return normalizeFeedFilters(null);
}

export function saveFeedFilters(filters: FeedFilters): void {
  try {
    localStorage.setItem(FEED_FILTERS_STORAGE_KEY, JSON.stringify(filters));
  } catch {
    // The filters just do not survive a reload.
  }
}

/** Mirror of the backend's `effective_channel_muted` (`app/repository/channels.py`). */
export function isChannelMuted(channel: Channel, nowSec: number): boolean {
  return channel.muted && (channel.muted_until == null || channel.muted_until > nowSec);
}

/**
 * True when the chips leave no message to show (Messages off, or a pair of chips both
 * unchecked): no message request is sent, whatever the other sources show.
 */
export function hasNoMessages(filters: FeedFilters): boolean {
  return (
    !filters.sources.messages ||
    (!filters.chan && !filters.priv) ||
    (!filters.incoming && !filters.outgoing)
  );
}

/** True when nothing at all can show: no message and no control row. */
export function hasEmptySelection(filters: FeedFilters): boolean {
  return hasNoMessages(filters) && !filters.sources.control;
}

/**
 * The filters of a session that may not read direct messages (no `read_private`): `priv` is
 * off whatever was stored or pinned in the URL. A "DMs only" selection (`chan` off, `priv` on)
 * means nothing to such a session, so it falls back to channels. Applied when filters are
 * loaded; the caller keeps the original `priv` for storage so it is never rewritten.
 */
export function withoutPrivate(filters: FeedFilters): FeedFilters {
  return { ...filters, chan: filters.chan || filters.priv, priv: false };
}

/** The part of the filters the server evaluates (`type`, `outgoing` on `GET /api/messages`). */
export function filtersToQuery(filters: FeedFilters): FeedQuery {
  const query: FeedQuery = {};
  if (filters.chan !== filters.priv) query.type = filters.chan ? 'CHAN' : 'PRIV';
  if (filters.incoming !== filters.outgoing) query.outgoing = filters.outgoing;
  return query;
}

export type FeedFilterRow = Pick<Message, 'type' | 'conversation_key' | 'outgoing'>;

export type HiddenReason = 'muted' | 'pending';

/**
 * Why the muted/pending chips hide this row, or an empty list. Only a `CHAN` row whose
 * channel is still known can be hidden by them: a `PRIV` row, or a row of a deleted
 * channel, has no mute or membership to apply.
 */
function hiddenReasons(
  row: FeedFilterRow,
  channels: readonly Channel[],
  nowSec: number
): HiddenReason[] {
  if (row.type !== 'CHAN') return [];
  const key = row.conversation_key.toUpperCase();
  const channel = channels.find((c) => c.key.toUpperCase() === key);
  if (!channel) return [];
  const reasons: HiddenReason[] = [];
  if (isChannelMuted(channel, nowSec)) reasons.push('muted');
  if (isPendingChannel(channel)) reasons.push('pending');
  return reasons;
}

/** A node filters messages only when it is a channel, to that channel's rows. */
function rowMatchesNode(row: FeedFilterRow, node: string): boolean {
  return (
    row.type === 'CHAN' && node.toUpperCase() === `CHANNEL:${row.conversation_key.toUpperCase()}`
  );
}

/** Everything but the muted/pending chips. */
function passesBasicFilters(row: FeedFilterRow, filters: FeedFilters): boolean {
  if (hasNoMessages(filters)) return false;
  if (filters.node && !rowMatchesNode(row, filters.node)) return false;
  if (row.type === 'CHAN' ? !filters.chan : !filters.priv) return false;
  return row.outgoing ? filters.outgoing : filters.incoming;
}

export function rowPassesFilters(
  row: FeedFilterRow,
  filters: FeedFilters,
  channels: readonly Channel[],
  nowSec: number
): boolean {
  if (!passesBasicFilters(row, filters)) return false;
  const reasons = hiddenReasons(row, channels, nowSec);
  if (reasons.includes('muted') && !filters.includeMuted) return false;
  if (reasons.includes('pending') && !filters.includePending) return false;
  return true;
}

/**
 * Rows only the muted / pending chips keep out, for the counts on those chips. A row
 * hidden for both reasons counts under each: ticking either chip alone would not show it.
 */
export function countHiddenRows(
  rows: readonly FeedFilterRow[],
  filters: FeedFilters,
  channels: readonly Channel[],
  nowSec: number
): { muted: number; pending: number } {
  const counts = { muted: 0, pending: 0 };
  for (const row of rows) {
    if (!passesBasicFilters(row, filters)) continue;
    const reasons = hiddenReasons(row, channels, nowSec);
    if (reasons.includes('muted') && !filters.includeMuted) counts.muted += 1;
    if (reasons.includes('pending') && !filters.includePending) counts.pending += 1;
  }
  return counts;
}

/**
 * The feed in the URL hash: `#feed?src=messages,control,events&types=chan,priv&dir=in,out
 * &muted=1&pending=1&node=<pubkey64>&display=1` (also `#control?...`).
 *
 * Bookmarks sync to other vendors' clouds and a private channel's key is its access, so the
 * grammar carries only public data: source and type names, flags and a node's 64-hex public
 * key. A channel node is never encoded (and a 32-hex value is never accepted as a node).
 * Unknown keys, unknown tokens and malformed values are ignored one by one.
 */
export interface FeedHashState {
  /** Only the filters the query named, laid over the stored ones (URL > localStorage > defaults). */
  filters: Partial<FeedFilters>;
  /** Display mode (`FeedView`, `components/feed/feedDisplay.ts`): a layer over the filters. */
  display: boolean;
}

const HASH_SOURCES = ['messages', 'control', 'events'] as const;
const HASH_TYPES = ['chan', 'priv'] as const;
const HASH_DIRS = ['in', 'out'] as const;
const PUBKEY64 = /^[0-9a-f]{64}$/i;

/** The tokens of a list value that are in the allowlist; null when it has none to keep. */
function listTokens<T extends string>(value: string | null, allowed: readonly T[]): T[] | null {
  if (value === null) return null;
  if (value === '') return [];
  const kept = value
    .split(',')
    .map((token) => token.trim().toLowerCase())
    .filter((token): token is T => (allowed as readonly string[]).includes(token));
  return kept.length > 0 ? kept : null;
}

function flagValue(value: string | null): boolean | null {
  return value === '1' ? true : value === '0' ? false : null;
}

export function parseFeedQuery(query: string): FeedHashState {
  const params = new URLSearchParams(query);
  const filters: Partial<FeedFilters> = {};
  const sources = listTokens(params.get('src'), HASH_SOURCES);
  if (sources) {
    filters.sources = {
      messages: sources.includes('messages'),
      control: sources.includes('control'),
      events: sources.includes('events'),
    };
  }
  const types = listTokens(params.get('types'), HASH_TYPES);
  if (types) {
    filters.chan = types.includes('chan');
    filters.priv = types.includes('priv');
  }
  const dirs = listTokens(params.get('dir'), HASH_DIRS);
  if (dirs) {
    filters.incoming = dirs.includes('in');
    filters.outgoing = dirs.includes('out');
  }
  const muted = flagValue(params.get('muted'));
  if (muted !== null) filters.includeMuted = muted;
  const pending = flagValue(params.get('pending'));
  if (pending !== null) filters.includePending = pending;
  const node = params.get('node');
  if (node !== null && PUBKEY64.test(node)) filters.node = `contact:${node.toLowerCase()}`;
  return { filters, display: params.get('display') === '1' };
}

/** The query (no `?`) for the keys a state names, in grammar order; '' when it names none. */
export function encodeFeedQuery(state: FeedHashState): string {
  const { filters } = state;
  const parts: string[] = [];
  if (filters.sources) {
    const on = HASH_SOURCES.filter((name) => filters.sources?.[name]);
    parts.push(`src=${on.join(',')}`);
  }
  if (filters.chan !== undefined && filters.priv !== undefined) {
    parts.push(
      `types=${HASH_TYPES.filter((t) => (t === 'chan' ? filters.chan : filters.priv)).join(',')}`
    );
  }
  if (filters.incoming !== undefined && filters.outgoing !== undefined) {
    parts.push(
      `dir=${HASH_DIRS.filter((d) => (d === 'in' ? filters.incoming : filters.outgoing)).join(',')}`
    );
  }
  if (filters.includeMuted !== undefined) parts.push(`muted=${filters.includeMuted ? 1 : 0}`);
  if (filters.includePending !== undefined) parts.push(`pending=${filters.includePending ? 1 : 0}`);
  const key = filters.node?.startsWith('contact:') ? filters.node.slice('contact:'.length) : '';
  if (PUBKEY64.test(key)) parts.push(`node=${key.toLowerCase()}`);
  if (state.display) parts.push('display=1');
  return parts.join('&');
}

/** The query of the query: only what the grammar knows, so unknown keys never travel on. */
export function canonicalFeedQuery(query: string): string {
  return encodeFeedQuery(parseFeedQuery(query));
}

/** The full, explicit state of the filters: a bookmark of it does not depend on `localStorage`. */
export function feedQueryOf(filters: FeedFilters, display = false): string {
  return encodeFeedQuery({ filters, display });
}

/** The filters a query pins, over the base ones. */
export function applyFeedQuery(base: FeedFilters, query: string | undefined): FeedFilters {
  if (!query) return base;
  return { ...base, ...parseFeedQuery(query).filters };
}
